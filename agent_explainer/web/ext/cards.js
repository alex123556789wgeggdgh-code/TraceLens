/*
 * TraceLens 5.0 - card decorations (web/ext/cards.js)
 * ------------------------------------------------------------------
 * One plugin, three decoration surfaces, all additive to the card app.js
 * rebuilds on every render:
 *
 *   - confidence (feature 2): a colour-banded circular badge on the card body
 *     (green >= .9, ok-soft .7-.9, progress .5-.7, danger below .5), the exact
 *     value in title/aria, a pulsing attention border below the policy
 *     threshold, and the source method as a small line in the detail area;
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
  var EP_EVIDENCE = 'api/evidence';
  var EP_RECORDS = 'api/records';

  var MAX_EVIDENCE = 24;
  var MAX_SVG_BYTES = 524288;

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
   * 1. confidence (feature 2)
   * ---------------------------------------------------------------- */

  function bandOf(value) {
    if (value >= 0.9) return 'high';
    if (value >= 0.7) return 'ok';
    if (value >= 0.5) return 'mid';
    return 'low';
  }

  function decorateConfidence(card, record) {
    var value = num(record.confidence);
    if (value === null) return;

    /* "requires_action because of low confidence": the only honest client-side
     * signal is the policy threshold - confidence.apply_to_record() raises
     * requires_action below 0.5, and the page cannot read the intent behind a
     * decision that is high-confidence for another reason. */
    if (value < 0.5) card.setAttribute(ATTR_ATTENTION, 'low');
    else card.removeAttribute(ATTR_ATTENTION);

    var toggle = card.querySelector('.card__toggle');
    if (!toggle || marked(toggle, 'confidence')) return;

    var exact = String(value);
    var band = bandOf(value);
    var badge = make('span', {
      class: 'cards__badge',
      attrs: { role: 'img', 'aria-label': C.t('cards.confidence.aria', { value: exact }) },
      dataset: { band: band, confidence: exact }
    });
    badge.textContent = C.fmtPercent(value, 0);
    badge.setAttribute('title', C.t('cards.confidence.aria', { value: exact }));
    badge.setAttribute('data-i18n-aria', 'cards.confidence.aria');
    badge.setAttribute('data-i18n-params', JSON.stringify({ value: exact }));
    mark(badge, 'confidence');
    if (toggle.firstChild) toggle.insertBefore(badge, toggle.firstChild);
    else add(toggle, badge);
  }

  function decorateConfidenceSource(detail, record) {
    if (!detail || marked(detail, 'confidence-source')) return;
    var source = record.confidence_source;
    if (!isObj(source) || !hasText(source.method)) return;
    var method = str(source.method);
    var line = textNode(detail, 'cards.confidence.source', { method: method }, 'p', 'cards__source');
    line.setAttribute('data-i18n-params', JSON.stringify({ method: method }));
    mark(line, 'confidence-source');
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

  function runRollback(record, button, errorHost) {
    var id = str(record && record.id);
    if (!id) return;
    clearNode(errorHost);
    button.disabled = true;
    button.setAttribute('data-busy', 'true');
    clearNode(button);
    C.textNode(button, 'cards.rollback.pending');

    var failed = function (result) {
      button.disabled = false;
      button.removeAttribute('data-busy');
      clearNode(button);
      C.textNode(button, 'cards.rollback.button');
      showError(errorHost, result, function () { runRollback(record, button, errorHost); });
      say('cards.rollback.failed', null, 'error');
    };

    C.post(EP_ROLLBACK, { action_id: id, reason: '' }).then(function (result) {
      if (!result || !result.ok) { failed(result); return; }
      say('cards.rollback.done', null, 'ok');
      refreshList('rollback');
    }, function () {
      failed({ ok: false, status: 0, data: null, code: 'NETWORK_ERROR', message: '', requestId: '' });
    });
  }

  function decorateRollback(detail, card, record) {
    if (!detail || marked(detail, 'rollback')) return;

    var state = str(record.rollback_state);
    var type = str(record.action_type);
    var scope = isObj(record.impact_scope) ? record.impact_scope : null;
    var irreversible = !!(scope && scope.reversible === false);

    if (state === 'rolled_back') {
      card.setAttribute(ATTR_ROLLED, 'true');
      var rolled = textNode(detail, 'cards.rollback.rolled', null, 'p', 'cards__rolled');
      rolled.setAttribute(ATTR, 'rollback');
      return;
    }

    var row = make('div', { class: 'cards__rollback' });
    mark(row, 'rollback');
    add(detail, row);

    if (type === 'rollback') {
      var ref = hasText(record.rollback_ref) ? str(record.rollback_ref) : '';
      if (!ref) return;
      var link = make('button', { class: 'cards__ref', attrs: { type: 'button' } });
      C.textNode(link, 'cards.rollback.ref', { id: ref });
      link.setAttribute('data-i18n-params', JSON.stringify({ id: ref }));
      aria(link, 'cards.rollback.ref.aria', { id: ref });
      link.addEventListener('click', function (ev) { stop(ev); jumpToCard(ref); });
      add(row, link);
      return;
    }

    if (state === 'rollback_blocked') {
      note(row, 'cards.rollback.blocked', 'cards__rollback-note');
      return;
    }

    if (irreversible) {
      note(row, 'cards.rollback.irreversible', 'cards__rollback-note');
      return;
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
      note(row, blockedReason, 'cards__rollback-note');
    }

    button.addEventListener('click', function (ev) {
      stop(ev);
      if (button.disabled) return;
      C.confirmDialog('cards.rollback.confirm.title', 'cards.rollback.confirm.body', function () {
        runRollback(record, button, errorHost);
      }, 'cards.rollback.confirm.yes');
    });
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
      return { id: '', kind: guessKind(value), uri: value, caption: '', diff: '', feedback: null };
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
      mediaType: str(raw.media_type),
      diff: diff,
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
    var row = make('div', { class: 'cards__feedback' });
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
        attrs: { type: 'button', 'aria-pressed': 'false' }
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
        button.disabled = true;
        C.post(EP_EVIDENCE + '/' + encodeURIComponent(item.id) + '/feedback', { vote: key })
          .then(function (result) {
            button.disabled = false;
            if (!result || !result.ok) {
              showError(errorHost, result, function () { button.disabled = false; });
              say('cards.evidence.vote.failed', null, 'error');
              return;
            }
            var next = countsFrom(result.data);
            if (next) {
              if (next.like !== null) counts.like = next.like;
              if (next.dislike !== null) counts.dislike = next.dislike;
            } else {
              counts[key] += 1;
            }
            refresh();
            say('cards.evidence.vote.done', null, 'ok');
          }, function () {
            button.disabled = false;
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
    add(row, errorHost);
    return row;
  }

  function openEvidenceModal(item, caption, kindLabel) {
    var body = make('div', { class: 'cards__modal' });
    add(body, make('p', { class: 'cards__modal-caption', text: caption }));
    var host = add(body, make('div', { class: 'cards__modal-body' }));
    C.openModal('cards.evidence.modal.title', body);

    if (item.kind === 'screenshot') {
      var uri = safeUri(item.uri);
      if (!uri) { fetchFailureNote(host); return; }
      var img = make('img', { class: 'cards__modal-img', attrs: { src: uri, alt: caption } });
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
      var render = function (text) {
        item.svgText = text;
        var node = inlineSvg(text);
        clearNode(host);
        if (node) add(host, node);
        else fetchFailureNote(host);
      };
      fetchText(item.uri).then(render, function () { fetchFailureNote(host); });
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

  function evidenceCard(item, recordFeedback, single) {
    var kindLabel = C.t('cards.evidence.kind.' + item.kind, null);
    var captionText = hasText(item.caption) ? item.caption : C.t('cards.evidence.caption.empty', null);
    var holder = make('div', { class: 'cards__item', attrs: { role: 'listitem' } });

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
        openEvidenceModal(item, captionText, kindLabel);
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
        openEvidenceModal(item, captionText, kindLabel);
      });
    }

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
    var strip = add(section, make('div', { class: 'cards__strip', attrs: { role: 'list' } }));
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

    var section = make('div', { class: 'cards__causal' });
    mark(section, 'causal');
    var why = make('button', { class: 'cards__why', attrs: { type: 'button', 'aria-expanded': 'false' } });
    var icon = make('span', { class: 'cards__why-icon', attrs: { 'aria-hidden': 'true' } });
    icon.textContent = '\u003F';
    add(why, icon);
    C.textNode(why, 'cards.causal.why');
    add(section, why);

    var pop = make('div', { class: 'cards__causal-pop' });
    pop.hidden = true;
    if (hasText(causal.counterfactual)) {
      textNode(pop, 'cards.causal.counterfactual', null, 'p', 'cards__causal-label');
      add(pop, make('p', { class: 'cards__causal-text', text: causal.counterfactual }));
    }
    renderAlternatives(pop, causal.alternatives);
    if (hasText(causal.model)) add(pop, make('p', { class: 'cards__note', text: C.t('cards.causal.model', { model: causal.model }) }));
    add(section, pop);

    why.addEventListener('click', function (ev) {
      stop(ev);
      var open = pop.hidden;
      pop.hidden = !open;
      why.setAttribute('aria-expanded', open ? 'true' : 'false');
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
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
