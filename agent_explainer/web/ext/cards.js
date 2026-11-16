/*
 * TraceLens 5.0 - card decorations (web/ext/cards.js)
 * ------------------------------------------------------------------
 * One plugin, three decoration surfaces, all additive to the card app.js
 * rebuilds on every render:
 *
 *   - confidence (feature 2, R2-3): a colour-banded ring on the left of every
 *     card, banded by the strategy layer's own thresholds (green >= .9,
 *     light green .7-.9, yellow .5-.7, red below .5, neutral grey when there is
 *     no source to judge from - and then no number is shown at all), the exact
 *     4-decimal value + the source method + a few-sample warning in the hover
 *     text and in data-* attributes, a pulsing orange-red border below the
 *     policy threshold, the source as a readable line in the detail area, and
 *     the milliseconds the pass spent on each ring in data-conf-ms;
 *   - rollback (feature 1): a detail-row button gated by the client-side
 *     reversibility fields, a pure-DOM confirmation before any request, a
 *     rolled-back grey/strike/dashed state, and a dashed link from a rollback
 *     record to the step it reversed;
 *   - evidence + causal (feature 3): a thumbnail strip for screenshot /
 *     diagram / diff evidence, a modal enlargement, like/dislike feedback,
 *     and a "why" bubble with counterfactual + alternatives; when neither
 *     evidence nor an explanation exists it offers one server-side request.
 *
 * Why this shape:
 *   - app.js rebuilds <article class="card"> from scratch on renderAll(), so
 *     decorate() runs repeatedly. Every injected node carries
 *     data-trust-decorated and a surface checks for it first: idempotent, and
 *     no previous DOM is ever assumed;
 *   - all copy is a dictionary key under the `cards` domain (parts/cards.*.json
 *     -> i18n merge); no CJK and no sentence-shaped English literal lives here;
 *   - no API means record data still renders and buttons that need the server
 *     show a disabled, explained state instead of failing silently;
 *   - buttons stop propagation: the card header toggles expansion on click.
 */
(function (global) {
  'use strict';

  var ext = global.TraceLensExt;
  if (!ext || typeof ext.register !== 'function' || !ext.ctx) return;

  var C = ext.ctx;
  var doc = global.document;
  var VERSION = '1.0';

  var ATTR = 'data-trust-decorated';
  var ATTR_ATTENTION = 'data-trust-attention';
  var ATTR_ROLLED = 'data-trust-rolled';

  var EP_ROLLBACK = 'api/rollback';
  var EP_ROLLBACK_PLAN = 'api/rollback/plan';
  var EP_EVIDENCE = 'api/evidence';
  var EP_RECORDS = 'api/records';

  var MAX_EVIDENCE = 24;
  var MAX_SVG_BYTES = 524288;

  /* R2-3 (REQ-002 B2-2): the confidence colour bands. These numbers are the
   * strategy layer's own: `agent_explainer/confidence.py:THRESHOLDS`
   * (green 0.9 / light_green 0.7 / yellow 0.5 / red 0.0) and `PRECISION = 4`.
   * Same source, kept in sync by hand on purpose - the page cannot import
   * Python, and selfcheck check 94 pins the two tables against each other, so
   * a change on one side goes red. CHANGE BOTH SIDES.
   * Colours are never here: they are CSS tokens in web/ext/cards.css, keyed by
   * the `data-band` the ring carries. */
  var CONF_THRESHOLDS = { green: 0.9, light_green: 0.7, yellow: 0.5, red: 0.0 };
  var CONF_BAND_NEUTRAL = 'neutral';
  var CONF_PRECISION = 4;
  /* the red band edge: below it the card asks for attention (R2-2 linkage) */
  var CONF_LOW_EDGE = CONF_THRESHOLDS.yellow;
  /* schema note on `confidence_source.samples`: fewer than 5 is "not enough" */
  var CONF_FEW_SAMPLES = 5;
  var CONF_METHOD_KEYS = {
    logprobs: 'cards.confidence.method.logprobs',
    tool_success_rate: 'cards.confidence.method.tool_success_rate',
    hybrid: 'cards.confidence.method.hybrid',
    template: 'cards.confidence.method.template',
    default: 'cards.confidence.method.default'
  };
  var CONF_BAND_KEYS = {
    green: 'cards.confidence.band.green',
    light_green: 'cards.confidence.band.light_green',
    yellow: 'cards.confidence.band.yellow',
    red: 'cards.confidence.band.red',
    neutral: 'cards.confidence.band.neutral'
  };

  var ODDS = { like: 'up', dislike: 'down' };

  /* ---------------------------------------------------------------- *
   * small value / DOM helpers
   * ---------------------------------------------------------------- */

  function str(value) {
    return value === undefined || value === null ? '' : String(value);
  }

  function hasText(value) {
    return str(value).trim() !== '';
  }

  function isObj(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
  }

  function isArr(value) {
    return Object.prototype.toString.call(value) === '[object Array]';
  }

  function num(value) {
    return typeof value === 'number' && isFinite(value) ? value : null;
  }

  /* Monotonic millisecond clock for the B2-2 timing marks (R1-7/R1-9 A4 style):
   * the display layer never asks the server for anything, so the only time a
   * colour can be late by is the time this pass spends in the DOM. */
  function hrNow() {
    try {
      if (global.performance && typeof global.performance.now === 'function') {
        return global.performance.now();
      }
    } catch (e) { /* no performance object: Date.now() is the fallback */ }
    return Date.now();
  }

  function msText(ticks) {
    var v = Number(ticks);
    return isFinite(v) && v >= 0 ? v.toFixed(3) : '';
  }

  function byId(id) {
    return doc ? doc.getElementById(id) : null;
  }

  function make(tag, opts) {
    return C.el(tag, opts || null);
  }

  function add(parent, child) {
    return C.add(parent, child);
  }

  function say(key, params, kind) {
    try { C.toast(key, params || null, kind || 'info'); } catch (e) { /* toast is cosmetic */ }
  }

  function stop(ev) {
    if (!ev) return;
    if (typeof ev.stopPropagation === 'function') ev.stopPropagation();
    if (typeof ev.preventDefault === 'function') ev.preventDefault();
  }

  function marked(root, name) {
    if (!root || typeof root.querySelector !== 'function') return false;
    return !!root.querySelector('[' + ATTR + '="' + name + '"]');
  }

  function mark(node, name) {
    if (node) node.setAttribute(ATTR, name);
    return node;
  }

  function aria(node, key, params) {
    if (!node) return node;
    node.setAttribute('aria-label', C.t(key, params || null));
    node.setAttribute('data-i18n-aria', key);
    if (params) node.setAttribute('data-i18n-params', JSON.stringify(params));
    return node;
  }

  function textNode(parent, key, params, tag, cls) {
    var node = make(tag || 'span', { class: cls || '' });
    C.textNode(node, key, params || null);
    return add(parent, node);
  }

  function plain(parent, text, tag, cls) {
    var node = make(tag || 'span', { class: cls || '', text: str(text) });
    return add(parent, node);
  }

  function note(parent, key, cls) {
    return textNode(parent, key, null, 'p', cls || 'cards__note');
  }

  function offline() {
    try {
      var UI = C.UI;
      var snapshot = UI && typeof UI.apiMode === 'function' ? UI.apiMode() : null;
      var mode = snapshot ? str(snapshot.mode) : '';
      if (mode === 'offline' || mode === 'forced_offline') return true;
    } catch (e) { /* fall through to the protocol check */ }
    try {
      if (global.location && str(global.location.protocol) === 'file:') return true;
    } catch (e2) { /* no location */ }
    return false;
  }

  function refreshList(reason) {
    var UI = C.UI;
    var done = function () {
      try {
        if (UI && typeof UI.renderAll === 'function') UI.renderAll();
      } catch (e) { /* the existing view stays usable */ }
    };
    try {
      if (UI && typeof UI.refreshRecords === 'function') {
        var pending = UI.refreshRecords({ reason: str(reason) || 'cards' });
        if (pending && typeof pending.then === 'function') {
          pending.then(done, done);
          return;
        }
      }
    } catch (e2) { /* fall back to the local repaint */ }
    done();
  }

  function showError(host, result, retry) {
    if (!host) return;
    C.renderError(host, C.panelError(result, retry));
  }

  function clearNode(node) {
    if (node) C.clear(node);
  }

  /* ---------------------------------------------------------------- *
   * 1. confidence (feature 2, R2-3): ring + hover + attention
   * ---------------------------------------------------------------- *
   * One circular icon at the left edge of the card head, wearing the colour
   * band of `confidence`; the exact number, its source method and the
   * few-sample warning ride on it as hover text and machine-readable data:
   *
   *   - the band is decided here from `CONF_THRESHOLDS`, a copy of the
   *     strategy layer's `confidence.THRESHOLDS` (selfcheck check 94 pins the
   *     two tables against each other); CSS only maps a band token to a colour
   *     token, so this file never hard-codes a colour;
   *   - `confidence` null / out of range / not a number is "no source to judge
   *     from": the ring goes neutral grey and shows NO number. A number is
   *     never invented here, and "the source gave no basis" (`method=default`)
   *     is shown as the weak thing it is;
   *   - below the red edge the card itself carries `data-trust-attention`, the
   *     hook cards.css turns into the orange-red border and the pulse;
   *   - every ring writes the milliseconds this pass spent on it into
   *     `data-conf-ms` - the B2-2 mark the DOM self-test measures, with the
   *     cold first paint and a warm repaint reported as two numbers.
   */

  function confidenceSource(record) {
    return isObj(record.confidence_source) ? record.confidence_source : null;
  }

  function confidenceValue(record) {
    var value = num(record.confidence);
    /* null / not finite / outside 0-1 = "nothing to show". Not 0, not 1, not a
     * rounded guess: the schema's "missing = null", kept honest in JS. */
    if (value === null || value < 0 || value > 1) return null;
    return Number(value.toFixed(CONF_PRECISION));
  }

  function confidenceBand(value) {
    if (value === null) return CONF_BAND_NEUTRAL;
    if (value >= CONF_THRESHOLDS.green) return 'green';
    if (value >= CONF_THRESHOLDS.light_green) return 'light_green';
    if (value >= CONF_THRESHOLDS.yellow) return 'yellow';
    return 'red';
  }

  function confidenceMethodKey(method) {
    return CONF_METHOD_KEYS[str(method)] || null;
  }

  function confidenceMethodText(method) {
    var key = confidenceMethodKey(method);
    /* a method the schema does not know is "source not decidable": the page
     * neither vouches for it nor silently swaps in another label */
    return key ? C.t(key, null) : C.t('cards.confidence.method.unknown', null);
  }

  function confidenceSamples(source) {
    return source ? num(source.samples) : null;
  }

  function confidenceModel(record) {
    var source = confidenceSource(record);
    var value = confidenceValue(record);
    var method = source && hasText(source.method) ? str(source.method) : '';
    var known = confidenceMethodKey(method) !== null;
    var samples = confidenceSamples(source);
    var few = samples !== null && samples < CONF_FEW_SAMPLES ? samples : null;
    var band = confidenceBand(value);
    var shown = value === null ? '' : value.toFixed(CONF_PRECISION);
    var tip = [];
    if (value === null) {
      tip.push(C.t('cards.confidence.unknown', null));
    } else {
      tip.push(C.t('cards.confidence.hover', {
        value: shown,
        method: confidenceMethodText(method)
      }));
      if (samples !== null) tip.push(C.t('cards.confidence.samples', { samples: samples }));
      if (few !== null) tip.push(C.t('cards.confidence.few_samples', { samples: few }));
    }
    return {
      value: value,
      shown: shown,
      band: band,
      bandText: C.t(CONF_BAND_KEYS[band] || CONF_BAND_KEYS.neutral, null),
      method: method,
      methodText: confidenceMethodText(method),
      known: known,
      samples: samples,
      few: few,
      tip: tip.join('')
    };
  }

  function decorateConfidence(card, record) {
    var started = hrNow();
    var model = confidenceModel(record);
    /* "requires_action because of low confidence": the only honest client-side
     * signal is the policy threshold - confidence.apply_to_record() raises
     * requires_action below 0.5, and the page cannot read the intent behind a
     * decision that is high-confidence for another reason. */
    if (model.value !== null && model.value < CONF_LOW_EDGE) card.setAttribute(ATTR_ATTENTION, 'low');
    else card.removeAttribute(ATTR_ATTENTION);

    var toggle = card.querySelector('.card__toggle');
    if (!toggle || marked(toggle, 'confidence')) return;

    var badge = make('span', {
      class: 'cards__badge cards__conf',
      attrs: { role: 'img' },
      dataset: {
        band: model.band,
        /* the number the ring is showing, 4 decimals; empty = no number to show */
        confidence: model.shown,
        method: model.known ? model.method : 'unknown',
        samples: model.samples === null ? '' : String(model.samples),
        few: model.few === null ? '' : 'true',
        confMs: msText(hrNow() - started)
      }
    });
    badge.setAttribute('title', model.tip);
    if (model.value === null) {
      /* no number, no percentage, no colour claim: only "nothing to judge from" */
      aria(badge, 'cards.confidence.unknown', null);
    } else {
      aria(badge, 'cards.confidence.aria', { value: model.shown });
      badge.textContent = C.fmtPercent(model.value, 0);
    }
    mark(badge, 'confidence');
    if (toggle.firstChild) toggle.insertBefore(badge, toggle.firstChild);
    else add(toggle, badge);
  }

  function decorateConfidenceSource(detail, record) {
    if (!detail || marked(detail, 'confidence-source')) return;
    var model = confidenceModel(record);
    var line = make('p', {
      class: 'cards__source',
      dataset: { band: model.band, method: model.known ? model.method : 'unknown' }
    });
    /* B2-1: the source is readable text beside the number, never a bare enum.
     * "no source to judge from" is shown as such, not dressed up. */
    C.textNode(line, model.known ? 'cards.confidence.source' : 'cards.confidence.source.none',
               model.known ? { method: confidenceMethodText(model.method) } : null);
    if (model.samples !== null) {
      add(line, make('span', {
        class: 'cards__source-samples',
        text: C.t('cards.confidence.samples', { samples: model.samples })
      }));
    }
    if (model.few !== null) {
      add(line, make('span', {
        class: 'cards__source-few',
        text: C.t('cards.confidence.few_samples', { samples: model.few })
      }));
    }
    mark(line, 'confidence-source');
    add(detail, line);
  }

  /* ---------------------------------------------------------------- *
   * 2. rollback (feature 1)
   * ---------------------------------------------------------------- */

  function jumpToCard(ref) {
    var found = null;
    try {
      found = C.UI && typeof C.UI.recordById === 'function' ? C.UI.recordById(ref) : null;
    } catch (e) { found = null; }
    var anchor = byId('card-' + ref);
    if (found && anchor && typeof anchor.scrollIntoView === 'function') {
      try { anchor.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
      catch (e2) { try { anchor.scrollIntoView(); } catch (e3) { /* not focusable */ } }
      try { if (typeof anchor.focus === 'function') anchor.focus(); } catch (e4) { /* ok */ }
      return;
    }
    say('cards.rollback.ref.missing', null, 'warn');
  }

  /* ---------------------------------------------------------------- *
   * 2. rollback (feature 1) - plan and registry driven (R1-6)
   * ---------------------------------------------------------------- *
   * R1-1 left three gaps in this section and they are closed together:
   *
   *  - G-21: the button is no longer gated by `impact_scope.reversible` alone.
   *    What a row may offer comes from the facts the server derived for this
   *    batch at read time (`meta.rollback_view`: journal / complete /
   *    rolled_back / links / blocked / kept_declared) plus the record's own
   *    registry facts. A row with nothing to undo, a row already rolled back
   *    and a row the server blocked get no button at all, and the reason is
   *    stated in plain language (the server's own `reason` string is shown as
   *    data beside the dictionary copy).
   *  - G-22: a click reads `GET /api/rollback/plan` before anything is sent.
   *    The dialog names the step it will undo, lists the dependency closure,
   *    the plan's blockers and its notices, and only the confirmation sends
   *    the POST - with `confirm_all=true` whenever the plan asked for a
   *    confirmation, because that is the gate the executor enforces.
   *  - G-20: the blocked details render from `meta.rollback_view`, which is
   *    derived at read time and never stored, and the two reason families
   *    (`ROLLBACK_NOT_REVERSIBLE` versus the no-side-effect / already-rolled
   *    codes) get their own copy.
   *
   * Every label is a dictionary key; server text (reasons, notices, receipts)
   * is rendered as data. No framework, ES5 only, works from file://.
   */

  var STATE_ROLLED = 'rolled_back';
  var STATE_BLOCKED = 'rollback_blocked';
  var CODE_NOT_REVERSIBLE = 'ROLLBACK_NOT_REVERSIBLE';
  var CODE_ALREADY_ROLLED_BACK = 'ROLLBACK_ALREADY_ROLLED_BACK';
  var MAX_REF_ROWS = 8;
  var MAX_DATA_CHARS = 220;
  var MAX_DESCRIBE_CHARS = 120;

  /* One literal key per inverse op the registry can name
   * (agent_explainer/rollback.py). Concatenated keys are invisible to
   * `tools/i18n_lint.py` and to the orphan rule of `web/selfcheck.mjs`, so the
   * table spells all of them out; `escalate_*` ops are included because the
   * page may read them from a record and must never fall back to a raw code. */
  var INVERSE_KEYS = {
    restore_file: 'rollback.inverse.restore_file',
    revoke_message: 'rollback.inverse.revoke_message',
    undo_config_change: 'rollback.inverse.undo_config_change',
    cancel_booking: 'rollback.inverse.cancel_booking',
    undo_note: 'rollback.inverse.undo_note',
    delete_generated: 'rollback.inverse.delete_generated',
    undo_write: 'rollback.inverse.undo_write',
    noop_read: 'rollback.inverse.noop_read',
    irreversible_financial: 'rollback.inverse.irreversible_financial',
    irreversible_contract: 'rollback.inverse.irreversible_contract',
    escalate_financial_reversal: 'rollback.inverse.escalate_financial_reversal',
    escalate_contract_rescission: 'rollback.inverse.escalate_contract_rescission'
  };

  /* `receipt_state` values (rollback.RECEIPT_STATES) plus the state the
   * compensation record itself always carries in `execution_receipt`. */
  var RECEIPT_KEYS = {
    simulated_local: 'cards.rollback.receipt.simulated_local',
    executed_sandbox: 'cards.rollback.receipt.executed_sandbox',
    executed_registry: 'cards.rollback.receipt.executed_registry',
    failed: 'cards.rollback.receipt.failed',
    unreported: 'cards.rollback.receipt.unreported'
  };

  /* The labels the compensation record is written with say which receipt state
   * its wording belongs to (R1-4 / G-14): a sandbox pilot must never read as a
   * local simulation, and neither of them reads as an external success. */
  var LABEL_RECEIPTS = {
    'simulated-local': 'simulated_local',
    'executed-sandbox': 'executed_sandbox',
    'executed-registry': 'executed_registry'
  };

  /* Codes the rollback endpoints answer with, mapped to the copy the card
   * shows. The server's own message wins in the panel (it is written for
   * humans); the key is what the page says when the server stayed silent. */
  var ROLLBACK_ERROR_KEYS = {
    ROLLBACK_NOT_REVERSIBLE: 'cards.rollback.error.rollback_not_reversible',
    ROLLBACK_ALREADY_ROLLED_BACK: 'cards.rollback.error.rollback_already_rolled_back',
    ROLLBACK_DEPENDENTS_PENDING: 'cards.rollback.error.rollback_dependents_pending',
    ROLLBACK_DEPENDENTS_IRREVERSIBLE: 'cards.rollback.error.rollback_dependents_irreversible',
    ROLLBACK_JOURNAL_UNREADABLE: 'cards.rollback.error.rollback_journal_unreadable',
    ROLLBACK_INVERSE_FAILED: 'cards.rollback.error.rollback_inverse_failed',
    ROLLBACK_NOT_FOUND: 'cards.rollback.error.rollback_not_found',
    ROLLBACK_FIELD_INVALID: 'cards.rollback.error.rollback_field_invalid',
    ROLLBACK_IDEMPOTENCY_MISMATCH: 'cards.rollback.error.rollback_idempotency_mismatch'
  };

  function tableGet(table, key) {
    if (!table || !hasText(key)) return '';
    return Object.prototype.hasOwnProperty.call(table, key) ? str(table[key]) : '';
  }

  function clip(value, max) {
    var text = str(value);
    var limit = num(max) === null ? MAX_DATA_CHARS : num(max);
    return text.length > limit ? text.slice(0, limit - 1) + '\u2026' : text;
  }

  function nowMs() {
    return typeof Date === 'function' ? new Date().getTime() : 0;
  }

  function toolNameOf(record) {
    return isObj(record && record.tool_used) ? str(record.tool_used.name) : '';
  }

  /* Registry describe keys are named `rollback.inverse.<op>`. Only keys spelled
   * out in INVERSE_KEYS are ever looked up, so a record can never steer the
   * page into a dictionary key the dictionaries do not hold. */
  function inverseKeyFor(op) {
    var name = str(op);
    if (name.indexOf('rollback.inverse.') === 0 && tableGet(INVERSE_KEYS, name.slice(17))) return name;
    return tableGet(INVERSE_KEYS, name);
  }

  /* Which receipt wording a compensation record belongs to. The truth order is
   * the server's own: the record's `facts.receipt_state` (the state that went
   * into its fact hash) first, then the labels it was written with, then the
   * receipt state field. `unreported` is the standing state of every
   * compensation record and is said separately, never as the receipt state. */
  function receiptStateKey(value) {
    var key = str(value);
    if (key === 'unreported') return '';
    return tableGet(RECEIPT_KEYS, key) ? key : '';
  }

  function receiptOfRecord(record) {
    var facts = isObj(record && record.facts) ? record.facts : null;
    var fromFacts = facts ? receiptStateKey(facts.receipt_state) : '';
    if (fromFacts) return fromFacts;
    var labels = isArr(record && record.labels) ? record.labels : [];
    var i;
    for (i = 0; i < labels.length; i += 1) {
      var mapped = tableGet(LABEL_RECEIPTS, str(labels[i]));
      if (mapped) return mapped;
    }
    var receipt = isObj(record && record.execution_receipt) ? record.execution_receipt : null;
    return receipt ? receiptStateKey(receipt.state) : '';
  }

  /* The confirm copy has to name the step it will undo. The record's own human
   * layer is the first source; a registry describe key is the fallback, and
   * the id is the last honest answer. */
  function describeOf(record) {
    var human = isObj(record && record.human_readable) ? record.human_readable : null;
    var l1 = human ? str(human.l1) : '';
    if (hasText(l1)) return clip(l1, MAX_DESCRIBE_CHARS);
    var inverse = inverseKeyFor(toolNameOf(record));
    if (inverse) return clip(C.t(inverse, null), MAX_DESCRIBE_CHARS);
    return clip(str(record && record.id), MAX_DESCRIBE_CHARS);
  }

  /* The read-time view the server put in the last record payload. Nothing here
   * is decided by the page: it indexes what `derive_rollback_view` answered,
   * and an absent view simply means "no facts to gate on". */
  function metaView() {
    var meta = null;
    var view = null;
    try {
      meta = C.UI && typeof C.UI.metaInfo === 'function' ? C.UI.metaInfo() : null;
    } catch (e) { meta = null; }
    view = isObj(meta) && isObj(meta.rollback_view) ? meta.rollback_view : null;

    var out = {
      present: !!view,
      journal: isObj(view && view.journal) ? view.journal : null,
      complete: !!(view && view.complete === true),
      plannedCount: num(view && view.planned),
      planLimit: num(view && view.plan_limit),
      rolled: {},
      links: {},
      blocked: {},
      kept: {},
      planned: {}
    };
    if (!view) return out;

    (isArr(view.rolled_back) ? view.rolled_back : []).forEach(function (id) {
      if (hasText(id)) out.rolled[str(id)] = true;
    });
    (isArr(view.links) ? view.links : []).forEach(function (link) {
      if (!isObj(link) || !hasText(link.action_id)) return;
      out.links[str(link.action_id)] = link;
    });
    (isArr(view.blocked) ? view.blocked : []).forEach(function (entry) {
      if (!isObj(entry) || !hasText(entry.action_id)) return;
      out.blocked[str(entry.action_id)] = entry;
    });
    (isArr(view.kept_declared) ? view.kept_declared : []).forEach(function (id) {
      if (hasText(id)) out.kept[str(id)] = true;
    });
    (isArr(view.planned_ids) ? view.planned_ids : []).forEach(function (id) {
      if (hasText(id)) out.planned[str(id)] = true;
    });
    return out;
  }

  function codesOf(entry) {
    var codes = isObj(entry) && isArr(entry.codes) ? entry.codes : [];
    var out = [];
    codes.forEach(function (code) {
      var text = str(code);
      if (hasText(text) && out.indexOf(text) < 0) out.push(text);
    });
    return out;
  }

  function memberIds(entry) {
    var direct = isObj(entry) && isArr(entry.members) ? entry.members : [];
    var detail = isObj(entry) && isArr(entry.members_detail) ? entry.members_detail : [];
    var out = [];
    direct.forEach(function (id) {
      var text = str(id);
      if (hasText(text) && out.indexOf(text) < 0) out.push(text);
    });
    detail.forEach(function (row) {
      var text = isObj(row) ? str(row.id || row.action_id) : str(row);
      if (hasText(text) && out.indexOf(text) < 0) out.push(text);
    });
    return out;
  }

  /* One jumpable link to another step (G-24: cards__ref is the styled, dashed
   * chain chip; clicking it is a plain id lookup). */
  function refLink(parent, ref, key, ariaKey, cls) {
    var link = make('button', {
      class: cls || 'cards__ref',
      attrs: { type: 'button', 'data-ref': ref }
    });
    C.textNode(link, key, { id: ref });
    link.setAttribute('data-i18n-params', JSON.stringify({ id: ref }));
    aria(link, ariaKey, { id: ref });
    link.addEventListener('click', function (ev) { stop(ev); jumpToCard(ref); });
    return add(parent, link);
  }

  function dataLine(parent, text, cls) {
    return plain(parent, clip(text, MAX_DATA_CHARS), 'span', cls || 'cards__plan-data');
  }

  function idList(parent, ids, key, ariaKey) {
    var list = make('div', { class: 'cards__refs' });
    var shown = 0;
    ids.forEach(function (id) {
      if (shown >= MAX_REF_ROWS) return;
      shown += 1;
      var label = null;
      try {
        var found = C.UI && typeof C.UI.recordById === 'function' ? C.UI.recordById(id) : null;
        label = found ? describeOf(found) : '';
      } catch (e) { label = ''; }
      var row = add(list, make('div', { class: 'cards__refs-row' }));
      refLink(row, id, key, ariaKey);
      if (hasText(label)) dataLine(row, label, 'cards__refs-label');
    });
    if (ids.length > shown) plain(list, '+' + String(ids.length - shown), 'span', 'cards__refs-more');
    return add(parent, list);
  }

  /* A compensation record: it points back at the step it undid, names the
   * inverse action from the registry key, and shows the receipt state it was
   * really written with (simulated / sandbox pilot / registry executed). */
  function decorateCompensation(row, record) {
    var ref = hasText(record.rollback_ref) ? str(record.rollback_ref) : str(record.parent_action_id);
    if (hasText(ref)) refLink(row, ref, 'cards.rollback.ref', 'cards.rollback.ref.aria');

    var op = toolNameOf(record);
    var inverse = inverseKeyFor(op);
    if (inverse) {
      var line = textNode(row, 'cards.rollback.inverse.label', { op: op }, 'p', 'cards__inverse');
      line.setAttribute('data-i18n-params', JSON.stringify({ op: op }));
      note(row, inverse, 'cards__note cards__note--inverse');
      if (inverse === 'rollback.inverse.undo_write') {
        note(row, 'cards.rollback.record.level', 'cards__note cards__note--level');
      }
    }

    var state = receiptOfRecord(record);
    if (state) {
      var label = textNode(row, 'cards.rollback.receipt.label', { state: state }, 'p', 'cards__receipt');
      label.setAttribute('data-i18n-params', JSON.stringify({ state: state }));
      var copy = tableGet(RECEIPT_KEYS, state);
      if (copy) note(row, copy, 'cards__note cards__note--receipt');
      if (state === 'executed_sandbox') {
        var chip = make('span', { class: 'cards__chip' });
        C.textNode(chip, 'cards.rollback.pilot.chip', null);
        add(row, chip);
        note(row, 'cards.rollback.pilot.note', 'cards__note cards__note--pilot');
      }
    }

    var receipt = isObj(record.execution_receipt) ? record.execution_receipt : null;
    if (!receipt) return;
    if (str(receipt.state) === 'unreported') {
      note(row, 'cards.rollback.receipt.unreported', 'cards__note cards__note--receipt');
    }
    if (hasText(receipt.detail)) {
      var detail = textNode(row, 'cards.rollback.receipt.note', { note: clip(receipt.detail, MAX_DATA_CHARS) },
                            'p', 'cards__note cards__note--receipt-data');
      detail.setAttribute('data-i18n-params', JSON.stringify({ note: clip(receipt.detail, MAX_DATA_CHARS) }));
    }
  }

  /* G-20: the blocked family. The server's read-time entry carries the codes,
   * the members and one human reason string; the page picks the copy by code
   * and shows the server's reason beside it. No button is offered on these
   * rows, and nothing here is written back anywhere. */
  function decorateBlocked(row, entry, record) {
    note(row, 'cards.rollback.blocked.note', 'cards__note cards__note--blocked');
    var codes = codesOf(entry);
    var isNoSideEffect = !hasText(toolNameOf(record));

    if (codes.indexOf(CODE_NOT_REVERSIBLE) >= 0) {
      note(row, 'cards.rollback.blocked.irreversible', 'cards__note cards__note--blocked');
    } else if (codes.indexOf(CODE_ALREADY_ROLLED_BACK) >= 0) {
      note(row, 'cards.rollback.blocked.already', 'cards__note cards__note--blocked');
    } else if (isNoSideEffect) {
      note(row, 'cards.rollback.blocked.no_effect', 'cards__note cards__note--blocked');
    } else {
      note(row, 'cards.rollback.blocked', 'cards__note cards__note--blocked');
    }

    if (codes.length) {
      var codeLine = textNode(row, 'cards.rollback.blocked.code', { code: codes.join(' / ') },
                              'p', 'cards__code');
      codeLine.setAttribute('data-i18n-params', JSON.stringify({ code: codes.join(' / ') }));
    }
    if (entry && hasText(entry.reason)) {
      note(row, 'cards.rollback.blocked.reason_label', 'cards__label');
      dataLine(row, str(entry.reason), 'cards__plan-data cards__plan-data--reason');
    }
    var members = memberIds(entry);
    if (members.length) {
      var count = num(entry && (entry.members_count !== undefined ? entry.members_count : members.length));
      var detail = textNode(row, 'cards.rollback.blocked.detail',
                            { count: String(count === null ? members.length : count) },
                            'p', 'cards__label');
      detail.setAttribute('data-i18n-params', JSON.stringify({ count: String(count === null ? members.length : count) }));
      idList(row, members, 'cards.rollback.plan.member.aria', 'cards.rollback.plan.member.aria', 'cards__refs cards__refs--members');
    }
  }

  function decorateRollback(detail, card, record) {
    if (!detail || marked(detail, 'rollback')) return;

    var view = metaView();
    var id = str(record.id);
    var type = str(record.action_type);
    var link = tableGet(view.links, id) ? view.links[id] : null;
    var entry = tableGet(view.blocked, id) ? view.blocked[id] : null;
    var state = str(record.rollback_state);
    if (!state && tableGet(view.rolled, id)) state = STATE_ROLLED;

    var row = make('div', { class: 'cards__rollback' });
    mark(row, 'rollback');
    add(detail, row);

    /* The compensation record is never a rollback target: it points back,
     * names its inverse action and shows its own receipt. */
    if (type === 'rollback') {
      decorateCompensation(row, record);
      return;
    }

    /* G-24: rolled back reads as grey + strike-through, and the chain to the
     * compensation record that follows it stays jumpable. */
    if (state === STATE_ROLLED) {
      card.setAttribute(ATTR_ROLLED, 'true');
      note(row, 'cards.rollback.rolled', 'cards__rolled');
      note(row, 'cards.rollback.rolled.note', 'cards__note cards__note--rolled');
      var nextRef = isObj(link) ? str(link.next_ref) : '';
      if (!hasText(nextRef)) nextRef = str(record.next_ref);
      if (hasText(nextRef)) refLink(row, nextRef, 'cards.rollback.next.ref', 'cards.rollback.next.ref.aria');
      return;
    }

    if (state === STATE_BLOCKED || entry) {
      decorateBlocked(row, entry, record);
      return;
    }

    /* Nothing to undo: no tool action means no external side effect, so the
     * button never appears (G-21: the registry, not a flag, says so). */
    if (!hasText(toolNameOf(record))) {
      note(row, 'cards.rollback.no_side_effect', 'cards__note cards__note--quiet');
      return;
    }

    /* The journal cannot be read: the server derived nothing for this batch,
     * so the page closes the rollback entry instead of offering a guess. */
    if (view.journal && view.journal.ok === false) {
      note(row, 'cards.rollback.journal_unreadable', 'cards__note cards__note--quiet');
      return;
    }

    var scope = isObj(record.impact_scope) ? record.impact_scope : null;
    if (scope && scope.reversible === false) {
      note(row, 'cards.rollback.irreversible', 'cards__note cards__note--quiet');
      return;
    }

    /* A partial batch is stated, never smoothed over: the server says how many
     * rows it derived facts for, and the page repeats that bound verbatim. */
    if (view.present && view.complete === false && !tableGet(view.planned, id)) {
      var planned = view.plannedCount === null ? '' : String(view.plannedCount);
      var limit = view.planLimit === null ? '' : String(view.planLimit);
      var partial = textNode(row, 'cards.rollback.view_partial',
                             { planned: planned, limit: limit }, 'p', 'cards__note cards__note--quiet');
      partial.setAttribute('data-i18n-params', JSON.stringify({ planned: planned, limit: limit }));
    }
    if (tableGet(view.kept, id)) {
      note(row, 'cards.rollback.kept_declared', 'cards__note cards__note--quiet');
    }

    var actions = make('div', { class: 'cards__rollback-row' });
    add(row, actions);
    var button = make('button', { class: 'cards__rollback-btn', attrs: { type: 'button' } });
    C.textNode(button, 'cards.rollback.button');
    add(actions, button);

    var errorHost = make('div', { class: 'cards__error' });
    add(row, errorHost);

    var blockedReason = '';
    if (offline()) blockedReason = 'cards.rollback.offline';
    else if (C.can('intervene.decide') === false) blockedReason = 'cards.rollback.no_permission';
    if (blockedReason) {
      button.disabled = true;
      button.setAttribute('title', C.t(blockedReason, null));
      note(row, blockedReason, 'cards__note cards__note--quiet');
    }

    button.addEventListener('click', function (ev) {
      stop(ev);
      if (button.disabled) return;
      openPlanDialog(record, button, errorHost);
    });
  }

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy === true;
    if (busy === true) button.setAttribute('data-busy', 'true');
    else button.removeAttribute('data-busy');
    clearNode(button);
    C.textNode(button, busy === true ? 'cards.rollback.pending' : 'cards.rollback.button');
  }

  function showRollbackError(host, result, retry) {
    if (!host) return;
    var code = result && result.code ? str(result.code) : '';
    var key = tableGet(ROLLBACK_ERROR_KEYS, code);
    C.renderError(host, {
      key: key || 'cards.rollback.failed',
      code: code,
      message: result && result.message ? str(result.message) : '',
      requestId: result && result.requestId ? str(result.requestId) : '',
      retry: retry
    });
  }

  function submitRollback(record, button, confirmAll, closeAfter) {
    var id = str(record.id);
    if (!id) return;
    setBusy(button, true);
    /* The idempotency key is generated once per confirmation, so a double click
     * cannot post the same rollback twice (server: ROLLBACK_IDEMPOTENCY_MISMATCH). */
    var body = {
      action_id: id,
      reason: '',
      confirm_all: confirmAll === true,
      idempotency_key: 'rb-' + id + '-' + String(nowMs())
    };

    C.post(EP_ROLLBACK, body).then(function (result) {
      if (!result || !result.ok) {
        setBusy(button, false);
        if (typeof closeAfter === 'function') closeAfter('failed', result);
        return;
      }
      var data = isObj(result.data) ? result.data : {};
      var inverse = isObj(data.inverse) ? data.inverse : null;
      var state = inverse ? str(inverse.receipt_state) : '';
      if (hasText(state)) say('cards.rollback.done.receipt', { state: state }, 'ok');
      else say('cards.rollback.done', null, 'ok');
      setBusy(button, false);
      if (typeof closeAfter === 'function') closeAfter('ok', result);
      refreshList('rollback');
    }, function () {
      setBusy(button, false);
      if (typeof closeAfter === 'function') {
        closeAfter('failed', { ok: false, status: 0, data: null, code: 'NETWORK_ERROR', message: '', requestId: '' });
      }
    });
  }

  /* A4: the dialog is opened before the plan is read, so opening it never
   * waits on the network; the measured open time lands on the dialog node as
   * `data-a4-ms` (the plan round trip is `data-plan-ms`). */
  function openPlanDialog(record, button, errorHost) {
    var id = str(record.id);
    if (!id) return;
    clearNode(errorHost);
    var clickedAt = nowMs();
    var box = make('div', {
      class: 'cards-plan',
      attrs: { 'data-plan-target': id, 'data-a4-ms': '0', 'data-plan-ms': 'pending' }
    });
    mark(box, 'rollback-plan');
    C.openModal('cards.rollback.confirm.title', box);
    box.setAttribute('data-a4-ms', String(nowMs() - clickedAt));
    C.renderLoading(box, 'cards.rollback.plan.loading');
    readPlan(box, record, button, clickedAt);
  }

  function readPlan(box, record, button, clickedAt) {
    C.get(EP_ROLLBACK_PLAN, { action_id: str(record.id) }).then(function (result) {
      clearNode(box);
      box.setAttribute('data-plan-ms', String(nowMs() - clickedAt));
      if (!result || !result.ok) { planFailed(box, record, button, result); return; }
      renderPlan(box, record, button, isObj(result.data) ? result.data.plan : null);
    }, function () {
      clearNode(box);
      box.setAttribute('data-plan-ms', String(nowMs() - clickedAt));
      planFailed(box, record, button,
                 { ok: false, status: 0, data: null, code: 'NETWORK_ERROR', message: '', requestId: '' });
    });
  }

  /* The plan is the gate: when it does not come back the page says so, offers a
   * labelled retry, and sends nothing. A rollback request is never built on a
   * plan the page has not read (G-22). */
  function planFailed(box, record, button, result) {
    clearNode(box);
    /* renderError owns the host (it clears it), so the honest line goes in
     * after the panel, never before it. */
    showRollbackError(box, result, null);
    note(box, 'cards.rollback.plan.failed', 'cards__note cards__note--blocked');
    var row = planRow(box, 'cards__plan-row--actions');
    var again = add(row, make('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    C.textNode(again, 'cards.rollback.plan.retry');
    again.addEventListener('click', function (ev) {
      stop(ev);
      clearNode(box);
      C.renderLoading(box, 'cards.rollback.plan.loading');
      readPlan(box, record, button, nowMs());
    });
  }

  function planRow(parent, cls) {
    return add(parent, make('div', { class: cls || 'cards__plan-row' }));
  }

  function renderPlan(box, record, button, plan) {
    var id = str(record.id);
    if (!isObj(plan)) {
      planFailed(box, record, button, { ok: false, status: 0, data: null, code: '', message: '', requestId: '' });
      return;
    }

    var head = planRow(box, 'cards__plan-head');
    var describe = describeOf(record);
    var title = textNode(head, 'cards.rollback.confirm.describe', { describe: describe }, 'p', 'cards__plan-describe');
    title.setAttribute('data-i18n-params', JSON.stringify({ describe: describe }));
    /* The standing honesty line stays beside the specific one: the receipt
     * belongs to the server and nothing here is ever worded as a restoration. */
    note(head, 'cards.rollback.confirm.body', 'cards__note cards__note--quiet');
    var target = textNode(head, 'cards.rollback.plan.target', { id: id }, 'p', 'cards__plan-meta');
    target.setAttribute('data-i18n-params', JSON.stringify({ id: id }));
    var tool = toolNameOf(record);
    if (hasText(tool)) {
      var toolLine = textNode(head, 'cards.rollback.plan.tool', { tool: tool }, 'p', 'cards__plan-meta');
      toolLine.setAttribute('data-i18n-params', JSON.stringify({ tool: tool }));
    }

    var order = isArr(plan.order) ? plan.order : [];
    if (order.length) {
      var scope = textNode(box, 'cards.rollback.plan.scope', { count: String(order.length) }, 'p', 'cards__plan-scope');
      scope.setAttribute('data-i18n-params', JSON.stringify({ count: String(order.length) }));
      var orderLine = textNode(box, 'cards.rollback.plan.order', { count: String(order.length) }, 'p', 'cards__plan-meta');
      orderLine.setAttribute('data-i18n-params', JSON.stringify({ count: String(order.length) }));
    }

    /* G-22: the dependency list is shown before anything is sent. */
    var dependents = isArr(plan.dependents) ? plan.dependents : [];
    if (dependents.length) {
      var deps = textNode(box, 'cards.rollback.plan.deps', { count: String(dependents.length) },
                          'p', 'cards__label');
      deps.setAttribute('data-i18n-params', JSON.stringify({ count: String(dependents.length) }));
      idList(box, dependents, 'cards.rollback.plan.deps.aria', 'cards.rollback.plan.deps.aria');
    } else {
      note(box, 'cards.rollback.plan.deps.none', 'cards__note cards__note--quiet');
    }

    var confirmAll = plan.requires_confirmation === true;
    if (confirmAll) note(box, 'cards.rollback.plan.confirm_needed', 'cards__note cards__note--confirm');

    if (plan.truncated === true) {
      var dropped = isArr(plan.truncated_members) ? plan.truncated_members.length : 0;
      var trunc = textNode(box, 'cards.rollback.plan.truncated', { count: String(dropped) },
                           'p', 'cards__note cards__note--truncated');
      trunc.setAttribute('data-i18n-params', JSON.stringify({ count: String(dropped) }));
    }

    var notices = isArr(plan.notices) ? plan.notices : [];
    if (notices.length) {
      note(box, 'cards.rollback.plan.notices', 'cards__label');
      var noticeBox = planRow(box, 'cards__plan-notices');
      notices.forEach(function (item) {
        if (!isObj(item)) return;
        var line = planRow(noticeBox, 'cards__plan-notice');
        dataLine(line, str(item.code), 'cards__plan-code');
        if (hasText(item.reason)) dataLine(line, str(item.reason), 'cards__plan-data');
        var members = isArr(item.action_ids) ? item.action_ids : [];
        if (members.length) idList(line, members, 'cards.rollback.plan.member.aria', 'cards.rollback.plan.member.aria');
      });
    }

    var blockers = isArr(plan.blockers) ? plan.blockers : [];
    var hardBlocked = false;
    if (blockers.length) {
      note(box, 'cards.rollback.plan.blockers', 'cards__label');
      var blockerBox = planRow(box, 'cards__plan-blockers');
      blockers.forEach(function (item) {
        if (!isObj(item)) return;
        var line = planRow(blockerBox, 'cards__plan-blocker');
        var code = str(item.code);
        dataLine(line, code, 'cards__plan-code');
        if (hasText(item.reason)) dataLine(line, str(item.reason), 'cards__plan-data cards__plan-data--reason');
        var members = memberIds(item);
        if (members.length) idList(line, members, 'cards.rollback.plan.member.aria', 'cards.rollback.plan.member.aria');
        /* A member the registry cannot undo stays out of scope: the page says
         * so per member and never words it as undone (展示纪律 0.4). */
        if (code === CODE_NOT_REVERSIBLE) hardBlocked = true;
      });
    }
    var irreversible = isArr(plan.irreversible_dependents) ? plan.irreversible_dependents : [];
    if (irreversible.length) {
      note(box, 'cards.rollback.plan.blockers', 'cards__label');
      idList(box, irreversible, 'cards.rollback.plan.member.aria', 'cards.rollback.plan.member.aria');
    }
    if (plan.ok === false && hardBlocked) {
      note(box, 'cards.rollback.plan.blocked_stop', 'cards__note cards__note--blocked');
    }

    var row = planRow(box, 'cards__plan-row--actions');
    var canSend = plan.ok === true || !hardBlocked;
    if (canSend) {
      var yes = add(row, make('button', { class: 'trust-btn trust-btn--danger', attrs: { type: 'button' } }));
      C.textNode(yes, confirmAll ? 'cards.rollback.confirm.yes_all' : 'cards.rollback.confirm.yes');
      yes.setAttribute('data-confirm-all', confirmAll ? 'true' : 'false');
      yes.addEventListener('click', function (ev) {
        stop(ev);
        submitRollback(record, button, confirmAll, function (kind, result) {
          if (kind === 'failed') {
            clearNode(box);
            showRollbackError(box, result, function () {
              clearNode(box);
              C.renderLoading(box, 'cards.rollback.plan.loading');
              readPlan(box, record, button, nowMs());
            });
            return;
          }
          C.closeModal();
        });
      });
    }
    var no = add(row, make('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    C.textNode(no, 'cards.rollback.plan.close');
    no.addEventListener('click', function (ev) { stop(ev); C.closeModal(); });
    box.setAttribute('data-plan-ok', plan.ok === true ? 'true' : 'false');
    box.setAttribute('data-plan-confirm-all', confirmAll ? 'true' : 'false');
    box.setAttribute('data-plan-deps', String(dependents.length));
    box.setAttribute('data-plan-order', String(order.length));
  }

  /* ---------------------------------------------------------------- *
   * 3. multimedia evidence + causal explanation (feature 3)
   * ---------------------------------------------------------------- */

  function guessKind(uri) {
    var text = str(uri).toLowerCase();
    if (/\.svg(?:$|[?#])/.test(text)) return 'diagram';
    if (/\.(?:diff|patch)(?:$|[?#])/.test(text)) return 'diff';
    if (/\.(?:png|jpe?g|gif|webp)(?:$|[?#])/.test(text)) return 'screenshot';
    return 'file';
  }

  function normalizeItem(raw) {
    if (typeof raw === 'string') {
      var value = raw.trim();
      return { id: '', kind: guessKind(value), uri: value, caption: '', diff: '',
               sha256: '', bytes: null, providedBy: '', feedback: null };
    }
    if (!isObj(raw)) return null;
    var kind = str(raw.kind);
    if (kind !== 'screenshot' && kind !== 'diagram' && kind !== 'diff') kind = guessKind(str(raw.uri));
    var diff = typeof raw.diff === 'string' ? raw.diff
      : (typeof raw.text === 'string' ? raw.text : '');
    return {
      id: str(raw.evidence_id || raw.id),
      kind: kind,
      uri: str(raw.uri || raw.url),
      caption: str(raw.caption),
      diff: diff,
      mediaType: str(raw.media_type),
      /* R3-4 §2.6: the ledger's own sha256 / byte size / provenance, displayed as
       * "the fingerprint of this evidence" and "who put it here". Taken verbatim
       * from the landed record - the display layer never rewrites a fact. */
      sha256: str(raw.sha256),
      bytes: num(raw.bytes),
      providedBy: str(raw.provided_by),
      feedback: isObj(raw.feedback) ? raw.feedback : null
    };
  }

  function evidenceList(value) {
    var out = [];
    if (isArr(value)) {
      value.slice(0, MAX_EVIDENCE).forEach(function (raw) {
        var item = normalizeItem(raw);
        if (item) out.push(item);
      });
      return out;
    }
    if (typeof value === 'string' && hasText(value)) {
      var one = normalizeItem(value.trim());
      if (one) out.push(one);
    }
    return out;
  }

  function safeUri(uri) {
    var text = str(uri).trim();
    if (!text) return '';
    if (/^data:image\/(?:png|jpe?g|gif|webp|svg\+xml)[;,]/i.test(text)) return text;
    if (text.charAt(0) === '/' && text.charAt(1) !== '/') return text;
    if (text.indexOf('api/evidence/') === 0) return '/' + text;
    if (/^https?:\/\//i.test(text)) {
      try {
        var loc = global.location;
        if (loc && /^https?:$/i.test(str(loc.protocol)) && typeof global.URL === 'function') {
          var parsed = new global.URL(text, loc.href);
          if (parsed.origin === loc.origin) return parsed.pathname + parsed.search;
        }
      } catch (e) { return ''; }
      return '';
    }
    return '';
  }

  function canFetch() {
    if (offline()) return false;
    try { return typeof global.fetch === 'function'; } catch (e) { return false; }
  }

  function fetchText(uri) {
    return new Promise(function (resolve, reject) {
      var safe = safeUri(uri);
      if (!safe || /^data:/i.test(safe) || !canFetch()) {
        reject({ code: 'OFFLINE' });
        return;
      }
      global.fetch(safe, { credentials: 'same-origin', cache: 'no-store' }).then(function (resp) {
        if (!resp || !resp.ok) { reject({ code: 'HTTP_' + str(resp && resp.status) }); return; }
        resp.text().then(resolve, reject);
      }, reject);
    });
  }

  function inlineSvg(text) {
    var raw = str(text || '');
    if (!raw || raw.length > MAX_SVG_BYTES) return null;
    if (/<\s*(?:script|iframe|foreignObject)\b/i.test(raw)) return null;
    if (/\son[a-z]+\s*=/i.test(raw)) return null;
    if (/(?:href|src)\s*=\s*["']?\s*(?:javascript:|data:(?!image\/)|https?:)/i.test(raw)) return null;
    if (!global.DOMParser || !doc) return null;
    var parsed;
    try { parsed = new global.DOMParser().parseFromString(raw, 'image/svg+xml'); }
    catch (e) { return null; }
    var svg = parsed ? parsed.documentElement : null;
    if (!svg || str(svg.nodeName).toLowerCase() !== 'svg') return null;
    if (typeof parsed.querySelector === 'function' && parsed.querySelector('script')) return null;
    try { return doc.importNode(svg, true); }
    catch (e2) { return null; }
  }

  function fetchFailureNote(host) {
    if (!host) return;
    clearNode(host);
    note(host, offline() ? 'cards.evidence.remote' : 'cards.evidence.fetch.failed', 'cards__note');
  }

  /* R3-4 §2.1（D-7 口径）：截图从来不是运行时能产的东西——自动接线只产 diff 与
   * SVG，位图截图只能由执行端/用户主动上传（`provided_by` 由账本如实标注）。
   * 所以"没有可用图"的截图条目只说一件事：待接入。不画破图、不猜内容。 */
  function screenshotPendingNote(parent) {
    return note(parent, 'cards.evidence.screenshot.pending', 'cards__note cards__pending');
  }

  /* R3-4 §2.6：证据的 sha256 展示成「这份证据的指纹」，逐字取落账值；
   * 来源（`provided_by`，schema 1175 的封闭枚举 runtime/user/server）如实标注，
   * 记录里没写就明说没写——不猜是谁放的。 */
  var PROVENANCE_KEYS = {
    runtime: 'cards.evidence.provided_by.runtime',
    user: 'cards.evidence.provided_by.user',
    server: 'cards.evidence.provided_by.server'
  };

  function evidenceMetaRow(item) {
    var row = make('div', { class: 'cards__meta', attrs: { 'data-evidence-meta': 'true' } });
    if (hasText(item.sha256)) {
      var fp = add(row, make('p', { class: 'cards__fingerprint', dataset: { sha: item.sha256 } }));
      plain(fp, C.t('cards.evidence.fingerprint', { sha: C.shortHash(item.sha256) }),
            'span', 'cards__fingerprint-text');
      aria(fp, 'cards.evidence.fingerprint.aria', { sha: item.sha256 });
      fp.setAttribute('title', item.sha256);
    }
    if (item.bytes !== null) {
      plain(row, C.t('cards.evidence.size', { size: C.fmtBytes(item.bytes) }),
            'span', 'cards__size');
    }
    plain(row, C.t(PROVENANCE_KEYS[item.providedBy] || 'cards.evidence.provided_by.unknown'),
          'span', 'cards__prov');
    return row;
  }

  function countsFrom(data) {
    var node = data;
    if (!isObj(node)) return null;
    if (isObj(node.feedback)) node = node.feedback;
    else if (isObj(node.evidence) && isObj(node.evidence.feedback)) node = node.evidence.feedback;
    else if (isObj(node.result) && isObj(node.result.feedback)) node = node.result.feedback;
    if (!isObj(node)) return null;
    function pick() {
      for (var i = 0; i < arguments.length; i += 1) {
        var candidate = arguments[i];
        if (typeof candidate === 'number' && isFinite(candidate)) {
          return Math.max(0, Math.round(candidate));
        }
      }
      return null;
    }
    return { like: pick(node.like, node.likes, node.up, node.helpful),
             dislike: pick(node.dislike, node.dislikes, node.down, node.unhelpful) };
  }

  function initialCount(value) {
    return typeof value === 'number' && isFinite(value) ? Math.max(0, Math.round(value)) : 0;
  }

  function voteRow(item, recordFeedback, single) {
    var seed = item.feedback || (single ? recordFeedback : null);
    var counts = { like: initialCount(seed && seed.like), dislike: initialCount(seed && seed.dislike) };
    var row = make('div', {
      class: 'cards__feedback',
      dataset: { evidence: item.id, voteState: 'idle' }
    });
    var stateHost = make('div', { class: 'cards__vote-state-host' });
    var errorHost = make('div', { class: 'cards__error' });
    var buttons = {};

    function refresh() {
      Object.keys(buttons).forEach(function (key) {
        var button = buttons[key];
        var label = key === 'like' ? 'cards.evidence.vote.like.aria' : 'cards.evidence.vote.dislike.aria';
        button.setAttribute('aria-label', C.t(label, { count: counts[key] }));
        button.setAttribute('title', C.t(label, { count: counts[key] }));
        button.setAttribute('data-i18n-aria', label);
        var value = button.querySelector('.cards__count');
        if (value) value.textContent = String(counts[key]);
      });
    }

    function voteButton(key) {
      var button = make('button', {
        class: 'cards__vote',
        attrs: { type: 'button', 'aria-pressed': 'false' },
        dataset: { vote: key }
      });
      var glyph = make('span', { attrs: { 'aria-hidden': 'true' } });
      glyph.textContent = key === 'like' ? '\u2191' : '\u2193';
      add(button, glyph);
      plain(add(button, make('span', { class: 'cards__count-wrap' })), String(counts[key]), 'span', 'cards__count');
      var disabled = offline() || !item.id;
      button.disabled = !!disabled;
      if (!item.id) button.setAttribute('title', C.t('cards.evidence.fetch.failed', null));
      else if (offline()) button.setAttribute('title', C.t('cards.evidence.vote.offline', null));
      button.addEventListener('click', function (ev) {
        stop(ev);
        if (button.disabled) return;
        clearNode(errorHost);
        clearNode(stateHost);
        row.setAttribute('data-vote-state', 'pending');
        button.disabled = true;
        C.post(EP_EVIDENCE + '/' + encodeURIComponent(item.id) + '/feedback', { vote: key })
          .then(function (result) {
            button.disabled = false;
            if (!result || !result.ok) {
              /* §2.3 失败显码：`showError` 把服务端错误码与 request id 摊开在行内，
               * 计数一格都不动——反馈没落账就不许把数字改得像是落了账。 */
              var code = (result && result.code) || 'UNKNOWN';
              row.setAttribute('data-vote-state', 'failed:' + str(code));
              textNode(stateHost, 'cards.evidence.vote.failed.code', { code: str(code) }, 'span', 'cards__vote-state');
              showError(errorHost, result, function () { button.disabled = false; });
              say('cards.evidence.vote.failed', null, 'error');
              return;
            }
            row.setAttribute('data-vote-state', 'ok');
            var next = countsFrom(result.data);
            if (next && (next.like !== null || next.dislike !== null)) {
              if (next.like !== null) counts.like = next.like;
              if (next.dislike !== null) counts.dislike = next.dislike;
              refresh();
            } else {
              /* 落账成功、这一份响应没带回计数：计数只在账上，展示层不自己 +1。
               * （§1.3「点赞点踩如实落 T16」——数字的来源是记录，不是界面算术。） */
              textNode(stateHost, 'cards.evidence.vote.count.pending', null, 'span', 'cards__vote-state');
            }
            textNode(stateHost, 'cards.evidence.vote.done', null, 'span', 'cards__vote-state cards__vote-state-ok');
            say('cards.evidence.vote.done', null, 'ok');
          }, function () {
            button.disabled = false;
            row.setAttribute('data-vote-state', 'failed:NETWORK_ERROR');
            textNode(stateHost, 'cards.evidence.vote.failed.code', { code: 'NETWORK_ERROR' }, 'span', 'cards__vote-state');
            showError(errorHost, { ok: false, status: 0, code: 'NETWORK_ERROR', message: '', requestId: '' }, null);
            say('cards.evidence.vote.failed', null, 'error');
          });
      });
      buttons[key] = button;
      return add(row, button);
    }

    voteButton('like');
    voteButton('dislike');
    refresh();
    add(row, stateHost);
    add(row, errorHost);
    return row;
  }

  function renderEvidenceBody(item, host, caption) {
    if (item.kind === 'screenshot') {
      var uri = safeUri(item.uri);
      if (!uri) { screenshotPendingNote(host); return; }
      var img = make('img', { class: 'cards__img', attrs: { src: uri, alt: caption } });
      img.addEventListener('error', function () { fetchFailureNote(host); });
      add(host, img);
      return;
    }
    if (item.kind === 'diagram') {
      if (item.svgText) {
        var svg = inlineSvg(item.svgText);
        if (svg) add(host, svg);
        else fetchFailureNote(host);
        return;
      }
      C.renderLoading(host, 'trust.state.loading');
      fetchText(item.uri).then(function (text) {
        item.svgText = text;
        var node = inlineSvg(text);
        clearNode(host);
        if (node) add(host, node);
        else fetchFailureNote(host);
      }, function () { fetchFailureNote(host); });
      return;
    }
    if (item.kind === 'diff') {
      var pre = make('pre', { class: 'cards__diff-body', text: item.diff || '' });
      add(host, pre);
      if (item.diff) return;
      C.renderLoading(host, 'trust.state.loading');
      fetchText(item.uri).then(function (text) {
        item.diff = text;
        clearNode(host);
        add(host, make('pre', { class: 'cards__diff-body', text: text }));
      }, function () { fetchFailureNote(host); });
      return;
    }
    if (hasText(item.uri)) add(host, make('p', { class: 'cards__note cards__uri', text: item.uri }));
    else fetchFailureNote(host);
  }

  /* R3-4 §2.1 放大：PM 裁决「弹层或新视图，非模态阻塞优先」——这里**不用**
   * `C.openModal`（那是 `aria-modal="true"` + 遮罩的阻塞对话框）。层自身
   * `aria-modal="false"`、不锁滚动、不夺键盘焦点：点遮罩、点关闭、按 Esc
   * 都只关这一层，卡片区在放大期间照常可读可滚。 */
  var zoomLayer = null;

  function closeEvidenceZoom() {
    if (zoomLayer && zoomLayer.parentNode) zoomLayer.parentNode.removeChild(zoomLayer);
    zoomLayer = null;
  }

  function openEvidenceZoom(item, caption, kindLabel) {
    closeEvidenceZoom();
    if (!doc || !doc.body) return;
    var layer = make('div', {
      class: 'cards__zoom',
      attrs: {
        role: 'dialog', 'aria-modal': 'false', tabindex: '-1',
        'aria-label': C.t('cards.evidence.zoom.aria', { kind: kindLabel })
      },
      dataset: { kind: item.kind, zoom: 'open' }
    });
    layer.addEventListener('click', function (ev) {
      if (ev && ev.target === layer) closeEvidenceZoom();
    });
    layer.addEventListener('keydown', function (ev) {
      var key = ev && (ev.key || ev.keyIdentifier);
      if (key === 'Escape' || key === 'Esc') closeEvidenceZoom();
    });
    add(doc.body, layer);
    zoomLayer = layer;
    var sheet = add(layer, make('div', { class: 'cards__zoom-sheet' }));
    var head = add(sheet, make('header', { class: 'cards__zoom-head' }));
    textNode(head, 'cards.evidence.modal.title', null, 'h4', 'cards__zoom-title');
    var close = add(head, make('button', { class: 'cards__zoom-close', attrs: { type: 'button' } }));
    close.addEventListener('click', function (ev) { stop(ev); closeEvidenceZoom(); });
    C.textNode(close, 'cards.evidence.zoom.close');
    var body = add(sheet, make('div', { class: 'cards__zoom-body' }));
    add(body, make('p', { class: 'cards__zoom-caption', text: caption }));
    var host = add(body, make('div', { class: 'cards__zoom-content' }));
    add(body, evidenceMetaRow(item));
    note(body, 'cards.evidence.zoom.nonmodal', 'cards__note cards__zoom-note');
    renderEvidenceBody(item, host, caption);
    try {
      if (typeof layer.focus === 'function') layer.focus();
    } catch (e) { /* focus is cosmetic: the page stays interactive either way */ }
  }

  function evidenceCard(item, recordFeedback, single) {
    var kindLabel = C.t('cards.evidence.kind.' + item.kind, null);
    var captionText = hasText(item.caption) ? item.caption : C.t('cards.evidence.caption.empty', null);
    var holder = make('div', {
      class: 'cards__item',
      attrs: { role: 'listitem' },
      dataset: { evidence: item.kind, evidenceId: item.id }
    });

    if (item.kind === 'diff') {
      var details = make('details', { class: 'cards__diff' });
      var summary = make('summary', { class: 'cards__thumb cards__thumb--diff' });
      var badge = make('span', { class: 'cards__kind', attrs: { 'aria-hidden': 'true' } });
      badge.textContent = '\u2261';
      add(summary, badge);
      plain(add(summary, make('span', { class: 'cards__thumb-label' })), kindLabel, 'span', 'cards__thumb-kind');
      plain(add(summary, make('span', { class: 'cards__thumb-caption' })), captionText, 'span', 'cards__thumb-caption');
      add(details, summary);
      var status = make('p', { class: 'cards__note' });
      var pre = make('pre', { class: 'cards__diff-body', text: item.diff || '' });
      add(details, pre);
      if (!item.diff) {
        add(details, status);
        details.addEventListener('toggle', function () {
          if (!details.open || item.diff) return;
          clearNode(status);
          status.appendChild(make('span', { text: '' }));
          C.renderLoading(status, 'trust.state.loading');
          fetchText(item.uri).then(function (text) {
            item.diff = text;
            pre.textContent = text;
            if (status.parentNode) status.parentNode.removeChild(status);
          }, function () {
            fetchFailureNote(status);
          });
        });
      }
      add(holder, details);
      var enlarge = make('button', { class: 'cards__link-btn', attrs: { type: 'button' } });
      C.textNode(enlarge, 'cards.evidence.diff.open');
      enlarge.addEventListener('click', function (ev) {
        stop(ev);
        openEvidenceZoom(item, captionText, kindLabel);
      });
      add(holder, enlarge);
    } else {
      var thumb = make('button', { class: 'cards__thumb', attrs: { type: 'button' }, dataset: { kind: item.kind } });
      aria(thumb, 'cards.evidence.open', { caption: captionText });
      add(holder, thumb);
      if (item.kind === 'screenshot') {
        var uri = safeUri(item.uri);
        if (uri) {
          var img = make('img', { class: 'cards__thumb-img', attrs: { src: uri, alt: '', loading: 'lazy', decoding: 'async' } });
          img.addEventListener('error', function () {
            if (img.parentNode) img.parentNode.removeChild(img);
            thumb.appendChild(make('span', { class: 'cards__kind', attrs: { 'aria-hidden': 'true' }, text: '\u25A3' }));
          });
          add(thumb, img);
        } else {
          thumb.appendChild(make('span', { class: 'cards__kind', attrs: { 'aria-hidden': 'true' }, text: '\u25A3' }));
          /* D-7：自动接线从不产截图，这条条目只能等执行端/用户上传——如实说，不画破图 */
          screenshotPendingNote(holder);
        }
      } else if (item.kind === 'diagram') {
        var svgHost = add(thumb, make('span', { class: 'cards__thumb-svg' }));
        var glyph = make('span', { class: 'cards__kind', attrs: { 'aria-hidden': 'true' } });
        glyph.textContent = '\u25A6';
        add(svgHost, glyph);
        if (safeUri(item.uri) && canFetch()) {
          fetchText(item.uri).then(function (text) {
            var node = inlineSvg(text);
            if (!node) return;
            item.svgText = text;
            clearNode(svgHost);
            add(svgHost, node);
          }, function () { /* keep the glyph */ });
        }
      } else {
        thumb.appendChild(make('span', { class: 'cards__kind', attrs: { 'aria-hidden': 'true' }, text: '\u25A3' }));
      }
      plain(add(thumb, make('span', { class: 'cards__thumb-kind' })), kindLabel, 'span', 'cards__thumb-kind');
      plain(add(thumb, make('span', { class: 'cards__thumb-caption' })), captionText, 'span', 'cards__thumb-caption');
      thumb.addEventListener('click', function (ev) {
        stop(ev);
        openEvidenceZoom(item, captionText, kindLabel);
      });
    }

    add(holder, evidenceMetaRow(item));
    if (item.id) add(holder, voteRow(item, recordFeedback, single));
    return holder;
  }

  function decorateEvidence(detail, record) {
    if (!detail || marked(detail, 'evidence')) return;
    var list = evidenceList(record.multimedia_evidence);
    if (!list.length) return;
    var section = make('section', { class: 'cards__evidence' });
    mark(section, 'evidence');
    textNode(section, 'cards.evidence.title', null, 'h4', 'cards__evidence-title');
    var strip = add(section, make('div', {
      class: 'cards__strip',
      attrs: { role: 'list' },
      dataset: { count: String(list.length) }
    }));
    var recordFeedback = isObj(record.evidence_feedback) ? record.evidence_feedback : null;
    list.forEach(function (item) {
      add(strip, evidenceCard(item, recordFeedback, list.length === 1));
    });
    if (offline()) note(section, 'cards.evidence.vote.offline', 'cards__note');
    add(detail, section);
  }

  function causalOf(record) {
    var raw = record.causal_explanation;
    if (typeof raw === 'string' && hasText(raw)) {
      return { counterfactual: raw.trim(), alternatives: [], model: '' };
    }
    if (!isObj(raw)) return null;
    var counterfactual = hasText(raw.counterfactual) ? str(raw.counterfactual)
      : (hasText(raw.text) ? str(raw.text) : '');
    var alternatives = [];
    if (isArr(raw.alternatives)) {
      raw.alternatives.forEach(function (alt) {
        if (typeof alt === 'string' && hasText(alt)) alternatives.push({ option: '', expected: '', why: str(alt) });
        else if (isObj(alt)) {
          alternatives.push({
            option: str(alt.option || alt.id || alt.name),
            expected: str(alt.expected || alt.outcome || alt.result),
            why: str(alt.why || alt.reason || alt.note)
          });
        }
      });
    }
    if (!hasText(counterfactual) && !alternatives.length) return null;
    return { counterfactual: counterfactual, alternatives: alternatives, model: str(raw.model) };
  }

  function renderAlternatives(host, alternatives) {
    if (!alternatives.length) return;
    textNode(host, 'cards.causal.alternatives', null, 'p', 'cards__causal-label');
    var list = add(host, make('ul', { class: 'cards__causal-list' }));
    alternatives.forEach(function (alt) {
      var item = add(list, make('li', { class: 'cards__causal-item' }));
      if (hasText(alt.option)) add(item, make('p', { class: 'cards__causal-option', text: C.t('cards.causal.option', { option: alt.option }) }));
      if (hasText(alt.expected)) add(item, make('p', { class: 'cards__causal-expected', text: C.t('cards.causal.expected', { expected: alt.expected }) }));
      if (hasText(alt.why)) add(item, make('p', { class: 'cards__causal-why', text: C.t('cards.causal.reason', { why: alt.why }) }));
    });
  }

  /* R3-4 §2.2 诚实口径：`causal_explanation.model` 是 schema 的封闭枚举
   * （template | llm，见 docs/schemas §causal_explanation 与 evidence_wiring
   * CAUSAL_KEYS 的注释——schema 零改动，所以「谁生成的」只由 model 承担）。
   * 模板就明说「模板生成：逐字取自记录字段」，绝不因为排版漂亮就冒充 AI；
   * llm 才标「AI 生成」，与 `human_readable.may_contain_inference` 的既有口径同调。
   * model 缺失/未知 → 说「没标注」，不猜。 */
  var CAUSAL_MODEL_KEYS = {
    template: { badge: 'cards.causal.badge.template', note: 'cards.causal.note.template' },
    llm: { badge: 'cards.causal.badge.llm', note: 'cards.causal.note.llm' }
  };

  function causalModelMarker(parent, model) {
    var key = str(model).toLowerCase();
    var known = CAUSAL_MODEL_KEYS[key];
    var line = make('p', { class: 'cards__causal-model', dataset: { model: str(model) } });
    add(line, make('span', {
      class: known ? ('cards__badge cards__badge-' + (key === 'llm' ? 'ai' : 'template'))
                   : 'cards__badge cards__badge-unknown',
      text: C.t(known ? known.badge : 'cards.causal.badge.unknown', null)
    }));
    add(line, make('span', {
      class: 'cards__causal-model-note',
      text: C.t(known ? known.note : 'cards.causal.note.model_unknown', null)
    }));
    return add(parent, line);
  }

  function decorateCausal(detail, record) {
    if (!detail || marked(detail, 'causal')) return;
    var causal = causalOf(record);
    var hasEvidence = evidenceList(record.multimedia_evidence).length > 0;

    if (!causal) {
      if (hasEvidence) return;
      var requestBox = make('div', { class: 'cards__causal cards__causal--request' });
      mark(requestBox, 'causal');
      var request = make('button', { class: 'cards__link-btn cards__why-request', attrs: { type: 'button' } });
      C.textNode(request, 'cards.causal.request');
      aria(request, 'cards.causal.request.aria', null);
      if (offline()) {
        request.disabled = true;
        request.setAttribute('title', C.t('cards.causal.request.offline', null));
      }
      var errorHost = make('div', { class: 'cards__error' });
      add(requestBox, request);
      add(requestBox, errorHost);
      add(detail, requestBox);
      if (offline()) note(requestBox, 'cards.causal.request.offline', 'cards__note');
      request.addEventListener('click', function (ev) {
        stop(ev);
        if (request.disabled) return;
        clearNode(errorHost);
        request.disabled = true;
        clearNode(request);
        C.textNode(request, 'cards.causal.request.pending');
        C.post(EP_RECORDS + '/' + encodeURIComponent(str(record.id)) + '/causal', { lang: C.lang() })
          .then(function (result) {
            request.disabled = false;
            clearNode(request);
            C.textNode(request, 'cards.causal.request');
            if (!result || !result.ok) {
              showError(errorHost, result, function () { request.click(); });
              say('cards.causal.request.failed', null, 'error');
              return;
            }
            say('cards.causal.request.done', null, 'ok');
            refreshList('causal');
          }, function () {
            request.disabled = false;
            clearNode(request);
            C.textNode(request, 'cards.causal.request');
            showError(errorHost, { ok: false, status: 0, code: 'NETWORK_ERROR', message: '', requestId: '' }, null);
            say('cards.causal.request.failed', null, 'error');
          });
      });
      return;
    }

    var section = make('div', {
      class: 'cards__causal',
      dataset: { causal: 'true', model: str(causal.model), open: 'false' }
    });
    mark(section, 'causal');
    var why = make('button', { class: 'cards__why', attrs: { type: 'button', 'aria-expanded': 'false' } });
    var icon = make('span', { class: 'cards__why-icon', attrs: { 'aria-hidden': 'true' } });
    icon.textContent = '\u003F';
    add(why, icon);
    C.textNode(why, 'cards.causal.why');
    add(section, why);

    /* §2.2：气泡 = 非模态展开块（`pop.hidden` 切换，不遮页、不锁焦点、不锁滚动）。
     * 无解释不画气泡：上面 `!causal` 的分支只画「请求因果解释」按钮。 */
    var pop = make('div', { class: 'cards__causal-pop' });
    pop.hidden = true;
    if (hasText(causal.counterfactual)) {
      textNode(pop, 'cards.causal.counterfactual', null, 'p', 'cards__causal-label');
      add(pop, make('p', { class: 'cards__causal-text', text: causal.counterfactual }));
    }
    causalModelMarker(pop, causal.model);
    renderAlternatives(pop, causal.alternatives);
    if (hasText(causal.model)) add(pop, make('p', { class: 'cards__note', text: C.t('cards.causal.model', { model: causal.model }) }));
    add(section, pop);

    why.addEventListener('click', function (ev) {
      stop(ev);
      var open = pop.hidden;
      pop.hidden = !open;
      why.setAttribute('aria-expanded', open ? 'true' : 'false');
      section.setAttribute('data-open', open ? 'true' : 'false');
    });
    add(detail, section);
  }

  /* ---------------------------------------------------------------- *
   * 4. the decoration entry point (called after every renderAll)
   * ---------------------------------------------------------------- */

  function decorate(card, record) {
    if (!card || !record) return;
    var state = str(record.rollback_state);
    if (state === 'rolled_back') card.setAttribute(ATTR_ROLLED, 'true');
    else card.removeAttribute(ATTR_ROLLED);

    try { decorateConfidence(card, record); } catch (e) { /* additive */ }

    var detail = null;
    try { detail = typeof card.querySelector === 'function' ? card.querySelector('.card__detail') : null; }
    catch (e2) { detail = null; }
    if (!detail) return;

    try { decorateConfidenceSource(detail, record); } catch (e3) { /* additive */ }
    try { decorateRollback(detail, card, record); } catch (e4) { /* additive */ }
    try { decorateEvidence(detail, record); } catch (e5) { /* additive */ }
    try { decorateCausal(detail, record); } catch (e6) { /* additive */ }
  }

  var last = { lang: '', records: 0 };

  function onState(state) {
    last = {
      lang: state && state.lang ? str(state.lang) : '',
      records: state && isArr(state.records) ? state.records.length : 0
    };
    return last;
  }

  ext.register({
    id: 'cards',
    version: VERSION,
    decorate: decorate,
    onState: onState
  });

  /* Standalone workspaces reuse these exact implementations. Keeping the
   * entry points here prevents a second confidence classifier, a second
   * rollback flow, or a second evidence renderer from drifting apart. */
  global.TraceLensCards = {
    version: VERSION,
    confidence: confidenceModel,
    openRollbackPlan: openPlanDialog,
    renderEvidenceAndCausal: function (host, record) {
      if (!host || !record) return null;
      clearNode(host);
      decorateEvidence(host, record);
      decorateCausal(host, record);
      return host;
    },
    refreshRecords: refreshList
  };
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
