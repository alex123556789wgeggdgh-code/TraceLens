/*
 * TraceLens 5.0 - collaboration panel (web/ext/collab.js)
 * ---------------------------------------------------------------------------
 * R5-4 (REQ-005 R5-4): the collaboration surface.
 *
 * Four jobs, each with one existing data face behind it (zero new endpoints):
 *   1. share a task group   -> POST /api/groups/<id>/share (T21)
 *   2. what I issued / what was shared with me, + revoke
 *                           -> GET /api/shares (T22, 8-field whitelist rows)
 *                           -> POST /api/shares/<id>/revoke (T23)
 *   3. the purple on-behalf mark
 *                           -> GET /api/secretariat/letters (T8: actions[].on_behalf_*)
 *   4. button visibility per collaboration level (B5-2, front-end half)
 *                           -> derived from the T22 rows this panel already
 *                              reads, with the server's own 403 as fallback
 *
 * Discipline (docs/07 §6, docs/12 §6, task book R5-4 §1):
 *   - every request goes through `ctx.get/post` (the single request gate);
 *     this file never builds its own `fetch` and never names a host;
 *   - all copy comes from `ctx.t()`: zero CJK literal here, dictionary keys only;
 *   - the one-time secret rule: `invite_url` and the plaintext token are
 *     rendered exactly once, in the moment they are created, with the
 *     "shown once only" warning (`sharing.share.result.warning`). No list, detail,
 *     repaint, log, or storage ever re-echoes them - the server's row whitelist
 *     (`server.SHARE_ROW_FIELDS`) structurally never sends them, and closing
 *     this view wipes the text out of the DOM.
 *   - expired / revoked rows are shown as what the server says they are, never
 *     as "still working", and the revoke control is off for them.
 *   - hiding a button is a UX improvement, never the gate: the server still
 *     answers 403 `SHARE_FORBIDDEN`. When a 403 arrives, the control disappears
 *     for this render and the reason is shown, not swallowed.
 *   - derived level never invents a fact: `shared_with_me` has a row for a group
 *     only when somebody granted me one, and a group with no row keeps every
 *     control (that is the group leader and the single-user local install - the
 *     R5-4 ruling is that they must not be degraded).
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext) return;

  var C = Ext.ctx;
  var GROUPS = 'api/groups';
  var SHARES = 'api/shares';
  var LETTERS = 'api/secretariat/letters';
  var DECOR = 'data-collab-decorated';
  var TRUST_ATTR = 'data-trust-decorated';
  var LEVELS = ['view', 'approve', 'manage'];
  var LEVEL_KEYS = { view: 'sharing.perms.view',
                     approve: 'sharing.perms.approve',
                     manage: 'sharing.perms.manage' };
  var STATUS_KEYS = { pending: 'sharing.issued.status.pending',
                      accepted: 'sharing.issued.status.accepted',
                      revoked: 'sharing.issued.status.revoked',
                      expired: 'sharing.issued.status.expired' };

  var state = {
    levelByGroup: {},
    grants: [],
    denied: {},
    onBehalf: {},
    group: '',
    sharesLoaded: false,
    lettersLoaded: false
  };

  function t(key, params) { return Ext.t(key, params); }
  function el(tag, opts) { return C.el(tag, opts); }
  function add(parent, node) { return C.add(parent, node); }
  function str(value) { return value === null || value === undefined ? '' : String(value); }
  function isArr(value) { return Object.prototype.toString.call(value) === '[object Array]'; }
  function isObj(value) { return !!value && typeof value === 'object'; }
  function levelOf(value) {
    var text = str(value);
    return LEVELS.indexOf(text) >= 0 ? text : '';
  }
  function levelKey(permission) {
    var level = levelOf(permission);
    return LEVEL_KEYS[level] || 'sharing.perms.none';
  }
  function groupOf(record) { return str(isObj(record) ? record.group_id : ''); }

  /* ---------------------------------------------------------------- *
   * 1. DOM helpers (same shape as the secretariat panel)
   * ---------------------------------------------------------------- */

  /* The title key is passed as a literal, never built by concatenation: a
   * 'sharing.' + key prefix would make every sharing key look "referenced" to
   * the selfcheck orphan gate. Callers name the key they render. */
  function section(host, key, titleKey) {
    var box = el('section', { class: 'collab-zone', dataset: { zone: key } });
    var head = add(box, el('header', { class: 'collab-zone__head' }));
    C.textNode(head, titleKey, null, 'h3');
    add(host, box);
    return box;
  }

  function zoneIn(host, zone) {
    if (!host || typeof host.querySelectorAll !== 'function') return null;
    var found = host.querySelectorAll('[data-zone]');
    for (var i = 0; i < found.length; i += 1) {
      if (found[i] && str(found[i].getAttribute('data-zone')) === zone) return found[i];
    }
    return null;
  }

  function btn(parent, key, onClick, opts) {
    var node = el('button', {
      class: 'collab-btn' + (opts && opts.primary ? ' collab-btn--primary' : '') +
             (opts && opts.danger ? ' collab-btn--danger' : ''),
      attrs: { type: 'button' }
    });
    C.textNode(node, key);
    if (opts && opts.disabled) node.disabled = true;
    if (opts && opts.hook) node.setAttribute('data-collab-hook', String(opts.hook));
    node.addEventListener('click', onClick);
    add(parent, node);
    return node;
  }

  function note(parent, key, params, cls) {
    var node = add(parent, el('p', { class: 'collab-note' + (cls ? ' ' + cls : '') }));
    C.textNode(node, key, params || null);
    return node;
  }

  function kv(parent, labelKey, value) {
    var row = el('div', { class: 'collab-kv' });
    C.textNode(add(row, el('dt')), labelKey);
    var dd = add(row, el('dd'));
    dd.textContent = value === '' || value === null || value === undefined ? '—' : String(value);
    add(parent, row);
    return row;
  }

  function table(parent, columns) {
    var wrap = el('div', { class: 'collab-table-wrap' });
    var tableEl = add(wrap, el('table', { class: 'collab-table' }));
    var headRow = add(add(tableEl, el('thead')), el('tr'));
    columns.forEach(function (column) {
      var th = add(headRow, el('th', { attrs: { scope: 'col' } }));
      if (column.indexOf('sharing.') === 0) C.textNode(th, column);
      else th.textContent = column;
    });
    var body = add(tableEl, el('tbody'));
    add(parent, wrap);
    return body;
  }

  function cell(row, value) {
    var td = add(row, el('td'));
    td.textContent = value === '' || value === null || value === undefined ? '—' : String(value);
    return td;
  }

  function field(parent, labelKey, inputNode, hintKey) {
    var wrap = el('label', { class: 'collab-field' });
    C.textNode(add(wrap, el('span', { class: 'collab-field__label' })), labelKey);
    add(wrap, inputNode);
    add(parent, wrap);
    if (hintKey) note(parent, hintKey);
    return inputNode;
  }

  function input(kind, placeholder) {
    return el('input', {
      class: 'collab-input',
      attrs: { type: kind || 'text', autocomplete: 'off', placeholder: placeholder || '' }
    });
  }

  function select(parent, values) {
    var wrap = el('label', { class: 'collab-field' });
    C.textNode(add(wrap, el('span', { class: 'collab-field__label' })),
               'sharing.share.permission');
    var box = el('select', { class: 'collab-select', attrs: { name: 'collab-permission' } });
    values.forEach(function (value) {
      var option = el('option', { attrs: { value: value }, dataset: { level: value } });
      C.textNode(option, 'sharing.share.permission.' + value);
      add(box, option);
    });
    add(wrap, box);
    add(parent, wrap);
    return box;
  }

  function selectValue(box, fallback) {
    if (!box) return fallback;
    var value = typeof box.value === 'string' ? box.value : '';
    if (value) return value;
    var options = box.options || [];
    for (var i = 0; i < options.length; i += 1) {
      if (options[i] && options[i].selected) return str(options[i].value);
    }
    return fallback;
  }

  function hide(node) {
    if (!node) return null;
    node.hidden = true;
    node.setAttribute('aria-hidden', 'true');
    var cls = str(node.className);
    if (cls.indexOf('collab--hidden') < 0) node.className = (cls + ' collab--hidden').trim();
    if (node.style) node.style.display = 'none';
    return node;
  }

  function hasMark(host, key) {
    if (!host || typeof host.querySelectorAll !== 'function') return false;
    var found = host.querySelectorAll('[' + DECOR + ']');
    for (var i = 0; i < found.length; i += 1) {
      if (found[i] && str(found[i].getAttribute(DECOR)) === key) return true;
    }
    return false;
  }

  function stamp(node, key) {
    node.setAttribute(DECOR, key);
    node.setAttribute(TRUST_ATTR, key);
    return node;
  }

  function hooksIn(root, hook) {
    var out = [];
    if (!root || typeof root.querySelectorAll !== 'function') return out;
    var found = root.querySelectorAll('[data-hook]');
    for (var i = 0; i < found.length; i += 1) {
      var node = found[i];
      if (node && str(node.getAttribute('data-hook')) === hook) out.push(node);
    }
    return out;
  }

  function pick(root, selector) {
    var found = null;
    try { found = root && typeof root.querySelector === 'function' ? root.querySelector(selector) : null; }
    catch (e) { found = null; }
    return found;
  }

  /* The share/accept/revoke endpoints answer exactly these eight codes
   * (docs/07 §4.9): one key each, and the deck is the copy. Anything else the
   * server says (``ROUTE_NOT_FOUND`` on the not-yet-mounted T24,
   * ``ROLE_FORBIDDEN``, a transport failure) is reported with the generic
   * trust-layer copy plus the code, never with a made-up sentence. */
  var ERROR_KEYS = {
    share_not_found: 'sharing.error.share_not_found',
    share_permission_invalid: 'sharing.error.share_permission_invalid',
    share_expired: 'sharing.error.share_expired',
    share_token_invalid: 'sharing.error.share_token_invalid',
    share_token_used: 'sharing.error.share_token_used',
    share_forbidden: 'sharing.error.share_forbidden',
    share_write_failed: 'sharing.error.share_write_failed',
    group_not_found: 'sharing.error.group_not_found'
  };

  function errorToast(ctx, res) {
    var code = str(res && res.code).toLowerCase();
    var key = ERROR_KEYS[code];
    if (key) {
      ctx.toast(key);
      return key;
    }
    ctx.toast('trust.request.failed', { detail: code || 'unknown' });
    return 'trust.request.failed';
  }

  /* ---------------------------------------------------------------- *
   * 2. derived collaboration level + on-behalf index
   * ---------------------------------------------------------------- */

  function indexShares(payload) {
    var levels = {};
    var grants = [];
    var rows = isObj(payload) && isArr(payload.shared_with_me) ? payload.shared_with_me : [];
    rows.forEach(function (row) {
      if (!isObj(row)) return;
      var group = str(row.group_id);
      var level = levelOf(row.permission);
      var status = str(row.status);
      if (!group || !level) return;
      grants.push({ group_id: group, permission: level, status: status });
      /* Only an accepted row binds. pending / expired / revoked are the server
       * saying "this row grants you nothing right now". */
      if (status !== 'accepted') return;
      var current = str(levels[group]);
      if (!current) levels[group] = level;
      else if (level === 'manage') levels[group] = 'manage';
      else if (level === 'approve' && current === 'view') levels[group] = 'approve';
    });
    state.levelByGroup = levels;
    state.grants = grants;
    state.sharesLoaded = true;
    return levels;
  }

  function levelFor(group) { return str(state.levelByGroup[str(group) || '']); }

  function deniedFor(group) { return !!state.denied[str(group)]; }

  function markDenied(group, code) {
    var key = str(group);
    if (key) state.denied[key] = str(code || 'share_forbidden');
  }

  function indexLetters(payload) {
    var index = {};
    var letters = isObj(payload) && isArr(payload.letters) ? payload.letters : [];
    letters.forEach(function (letter) {
      if (!isObj(letter) || !isArr(letter.actions)) return;
      letter.actions.forEach(function (action) {
        if (!isObj(action)) return;
        var id = str(action.id);
        var actor = str(action.on_behalf_actor);
        if (!id || !actor) return;
        index[id] = { id: id, actor: actor,
                      principal: str(action.on_behalf_of),
                      note: str(action.on_behalf_note),
                      letter: str(letter.id) };
      });
    });
    state.onBehalf = index;
    state.lettersLoaded = true;
    return index;
  }

  function sharesPass(ctx) {
    if (state.sharesLoaded) return Promise.resolve(null);
    return ctx.get(SHARES).then(function (res) {
      if (res && res.ok) indexShares(res.data);
      return res;
    }, function () { return null; });
  }

  function lettersPass(ctx, force) {
    if (state.lettersLoaded && !force) return Promise.resolve(null);
    return ctx.get(LETTERS).then(function (res) {
      if (res && res.ok) indexLetters(res.data);
      return res;
    }, function () { return null; });
  }

  /* ---------------------------------------------------------------- *
   * 3. the purple on-behalf mark on a record card
   * ---------------------------------------------------------------- */

  function onBehalfBadge(entry) {
    var badge = el('span', {
      class: 'collab-onbehalf',
      attrs: {
        title: t('sharing.onbehalf.detail',
                 { actor: entry.actor, principal: entry.principal || '—' })
      },
      dataset: { actor: entry.actor, principal: entry.principal, letter: entry.letter }
    });
    badge.textContent = t('sharing.onbehalf.badge', { actor: entry.actor });
    return stamp(badge, 'onbehalf');
  }

  function decorateOnBehalf(card, record) {
    var entry = state.onBehalf[str(record.id)];
    if (!entry) return;
    var toggle = pick(card, '.card__toggle');
    var host = toggle || card;
    if (hasMark(host, 'onbehalf')) return;
    var badge = onBehalfBadge(entry);
    if (toggle && toggle.firstChild) toggle.insertBefore(badge, toggle.firstChild);
    else add(host, badge);

    var detail = pick(card, '.card__detail');
    if (!detail || hasMark(detail, 'onbehalf-detail')) return;
    var box = el('div', { class: 'collab-onbehalf-detail' });
    stamp(box, 'onbehalf-detail');
    C.textNode(box, 'sharing.onbehalf.detail',
               { actor: entry.actor, principal: entry.principal || '—' }, 'p');
    C.textNode(box, 'sharing.onbehalf.actor', { actor: entry.actor }, 'p');
    if (entry.principal) {
      C.textNode(box, 'sharing.onbehalf.principal', { principal: entry.principal }, 'p');
    }
    if (entry.note) C.textNode(box, 'sharing.onbehalf.note', { note: entry.note }, 'p');
    add(detail, box);
  }

  /* ---------------------------------------------------------------- *
   * 4. the button-visibility gate on a card (B5-2 front-end half)
   * ---------------------------------------------------------------- */

  function decorateGate(card, record) {
    var group = groupOf(record);
    var level = levelFor(group);
    var refused = deniedFor(group);
    if (!level && !refused) return;
    var viewOnly = level === 'view';
    if (viewOnly) {
      /* view collaborator: no decision control, no rollback affordance.
       * The gate itself is the server's: this only removes controls that
       * would have answered 403 (docs/07 §6: hide, never fake). */
      ['intervene-button', 'intervene-submit', 'intervene-reject-confirm',
       'intervene-reason-box'].forEach(function (hook) {
        hooksIn(card, hook).forEach(hide);
      });
      hide(pick(card, '.cards__rollback'));
    }
    var detail = pick(card, '.card__detail');
    var host = detail || card;
    if (hasMark(host, 'gate')) return;
    var notice = el('p', { class: 'collab-gate',
                           dataset: { gate: viewOnly ? 'view' : 'denied', group: group } });
    stamp(notice, 'gate');
    C.textNode(notice, viewOnly ? 'sharing.gate.hide.intervene' : 'sharing.gate.denied');
    add(host, notice);
    if (level === 'view' || level === 'approve') {
      var share = el('p', { class: 'collab-gate collab-gate--share',
                            dataset: { gate: 'share', group: group } });
      stamp(share, 'gate-share');
      C.textNode(share, 'sharing.gate.hide.share');
      add(host, share);
    }
  }

  function decorate(card, record, ctx) {
    if (!card || !record) return;
    if (!state.sharesLoaded || !state.lettersLoaded) {
      /* One lazy pair of reads, guarded by the cache flags: the card list can
       * paint long before this panel was ever opened, and the on-behalf marks
       * (plus the derived visibility) must not wait for a click. Offline leaves
       * both caches empty = honest "no marks, nothing hidden by derivation". */
      sharesPass(ctx);
      lettersPass(ctx);
    }
    try { decorateOnBehalf(card, record); } catch (e) { /* additive */ }
    try { decorateGate(card, record); } catch (e2) { /* additive */ }
  }

  /* ---------------------------------------------------------------- *
   * 5. task groups + the one-time share dialog
   * ---------------------------------------------------------------- */

  function renderGroups(host, ctx, groups) {
    var box = section(host, 'group', 'sharing.group.title');
    if (!groups.length) {
      ctx.renderEmpty(box, 'sharing.group.empty');
      return;
    }
    var list = add(box, el('ul', { class: 'collab-groups' }));
    groups.forEach(function (group) {
      if (!isObj(group)) return;
      var id = str(group.id);
      if (!id) return;
      var level = levelFor(id);
      var li = add(list, el('li', {
        class: 'collab-group' + (state.group === id ? ' collab-group--active' : ''),
        dataset: { group: id, level: level }
      }));
      add(li, el('h4', { class: 'collab-group__title', text: str(group.title) || id }));
      add(li, el('span', { class: 'collab-group__id', text: id }));
      var meta = add(li, el('div', { class: 'collab-group__meta' }));
      C.textNode(meta, 'sharing.group.owner', { who: str(group.created_by) || 'local' }, 'span');
      C.textNode(meta, 'groups.list.agents',
                 { count: isArr(group.agent_ids) ? group.agent_ids.length : 0 }, 'span');
      C.textNode(meta, 'groups.list.status', { status: str(group.status) || 'open' }, 'span');
      var row = add(li, el('div', { class: 'collab-group__row' }));
      btn(row, 'groups.list.select', function () {
        state.group = id;
        ctx.refresh();
      }, { hook: 'pick-' + id });
      if (state.group === id) {
        btn(row, 'sharing.group.refresh', function () { ctx.refresh(); },
            { hook: 'refresh-' + id });
      }
      if (level === 'view' || level === 'approve') {
        /* No share control for a collaborator whose granted level is below
         * manage (server rule: T21 needs manage, and after the R5-4 tightening
         * only the group leader himself passes without a grant). The reason is
         * shown, not hidden as a mystery. */
        note(row, 'sharing.gate.hide.share', null, 'collab-gate');
      } else if (deniedFor(id)) {
        /* The server refused a share request for this group in this render:
         * the control is put away with the reason instead of staying a button
         * that only answers 403 again. */
        note(row, 'sharing.gate.denied', null, 'collab-gate');
      } else {
        btn(row, 'sharing.group.share', function () {
          state.group = id;
          openShareDialog(ctx, id);
        }, { primary: true, hook: 'share-' + id });
      }
    });
  }

  function openShareDialog(ctx, groupId) {
    var level = levelFor(groupId);
    if (level === 'view' || level === 'approve') {
      ctx.toast('sharing.gate.hide.share');
      return;
    }
    if (deniedFor(groupId)) {
      ctx.toast('sharing.gate.denied');
      return;
    }
    var doc = global.document;
    var panel = doc && typeof doc.getElementById === 'function'
      ? doc.getElementById('trust-body') : null;
    var host = panel || (doc ? doc.body : null);
    if (!host) return;
    var old = zoneIn(host, 'share');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var box = el('section', { class: 'collab-zone collab-share', dataset: { zone: 'share' } });
    C.textNode(box, 'sharing.share.title', null, 'h3');
    add(host, box);
    var form = add(box, el('div', { class: 'collab-form' }));
    var invitee = field(form, 'sharing.share.invitee', input('text', 'did:...'),
                        'sharing.share.invitee.hint');
    var permission = select(form, LEVELS);
    var ttl = field(form, 'sharing.share.ttl', input('text', '86400'), 'sharing.share.ttl.hint');
    var hint = note(form, 'sharing.share.permission.view.hint');
    permission.addEventListener('change', function () {
      var value = levelOf(selectValue(permission, 'view')) || 'view';
      hint.textContent = t('sharing.share.permission.' + value + '.hint');
    });
    var result = add(box, el('div', { class: 'collab-share__result' }));
    result.hidden = true;
    var submit = btn(form, 'sharing.share.submit', function () {
      var ttlText = str(ttl.value).trim();
      if (ttlText && !(/^[1-9][0-9]*$/).test(ttlText)) {
        ctx.toast('sharing.share.required');
        return;
      }
      var payload = { permission: levelOf(selectValue(permission, 'view')) || 'view' };
      if (str(invitee.value).trim()) payload.invitee_did = str(invitee.value).trim();
      if (ttlText) payload.ttl_seconds = parseInt(ttlText, 10);
      submit.disabled = true;
      var busy = add(form, el('span', { class: 'collab-share__pending' }));
      C.textNode(busy, 'sharing.share.pending');
      ctx.post(GROUPS + '/' + encodeURIComponent(groupId) + '/share', payload).then(function (res) {
        submit.disabled = false;
        if (busy.parentNode) busy.parentNode.removeChild(busy);
        if (!res.ok) {
          markDenied(groupId, res.code);
          errorToast(ctx, res);
          note(box, 'sharing.gate.denied', null, 'collab-gate');
          /* The server answered for this group in this session: put the control
           * away with the reason (the panel re-renders and the row explains
           * itself) instead of leaving a button whose only answer is 403 again.
           * The mark is kept until the page reloads - the panel never guesses
           * that the server changed its mind. */
          ctx.refresh();
          return;
        }
        showOneTime(ctx, result, isObj(res.data) ? res.data : {});
        ctx.toast('sharing.share.done');
        state.sharesLoaded = false;
        sharesPass(ctx);
      }, function (err) {
        submit.disabled = false;
        if (busy.parentNode) busy.parentNode.removeChild(busy);
        markDenied(groupId, isObj(err) ? err.code : '');
        errorToast(ctx, isObj(err) ? err : { code: '' });
        ctx.refresh();
      });
    }, { primary: true, hook: 'share-submit' });
    note(form, 'sharing.share.result.warning', null, 'collab-share__warning');
  }

  /* The one and only place the plaintext link is ever rendered. Closing this
   * view wipes the text out of the DOM; nothing here re-prints it. */
  function showOneTime(ctx, result, data) {
    C.clear(result);
    result.hidden = false;
    var share = isObj(data.share) ? data.share : {};
    var url = str(data.invite_url);
    var token = str(data.token);
    C.textNode(result, 'sharing.share.result', null, 'h4');
    C.textNode(result, 'sharing.share.result.warning', null, 'p');
    var line = add(result, el('pre', { class: 'collab-share__link',
                                       dataset: { onetime: 'true' } }));
    line.textContent = url;
    kv(result, 'sharing.share.result.link', url);
    if (token && url.indexOf(token) < 0) {
      var tokenLine = add(result, el('pre', { class: 'collab-share__token',
                                              dataset: { onetime: 'true' } }));
      tokenLine.textContent = token;
      kv(result, 'sharing.share.result.token', token);
    }
    if (!url && !token) C.textNode(result, 'sharing.share.result.no_token', null, 'p');
    kv(result, 'sharing.share.permission', t(levelKey(share.permission)));
    if (share.expires_at) {
      C.textNode(result, 'sharing.share.result.expires',
                 { time: C.fmtTime(share.expires_at) }, 'p');
    }
    var actions = add(result, el('div', { class: 'collab-share__actions' }));
    btn(actions, 'sharing.share.result.copy', function () {
      copyText(ctx, url || token);
    }, { hook: 'copy-link' });
    btn(actions, 'sharing.share.close', function () {
      var secrets = result.querySelectorAll('[data-onetime]');
      for (var i = 0; i < secrets.length; i += 1) secrets[i].textContent = '';
      var views = result.querySelectorAll('dd');
      for (var j = 0; j < views.length; j += 1) views[j].textContent = '';
      C.clear(result);
      result.hidden = true;
    }, { hook: 'close-share' });
  }

  function copyText(ctx, value) {
    var text = str(value);
    var nav = global.navigator;
    if (!text) {
      ctx.toast('sharing.share.result.no_token');
      return false;
    }
    if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
      nav.clipboard.writeText(text).then(function () {
        ctx.toast('sharing.share.copied');
      }, function () {
        ctx.toast('sharing.share.copy.failed');
      });
      return true;
    }
    /* No clipboard in this browser context: say so, never pretend it copied. */
    ctx.toast('sharing.share.copy.failed');
    return false;
  }

  /* ---------------------------------------------------------------- *
   * 6. issued / shared-with-me (+ T23 revoke)
   * ---------------------------------------------------------------- */

  function statusText(status) {
    var key = STATUS_KEYS[str(status)];
    return key ? t(key) : str(status) || '—';
  }

  function renderIssued(host, ctx, rows) {
    var box = section(host, 'issued', 'sharing.issued.title');
    if (!rows.length) {
      ctx.renderEmpty(box, 'sharing.issued.empty');
      return;
    }
    var body = table(box, ['sharing.issued.col.group', 'sharing.issued.col.permission',
                           'sharing.issued.col.invitee', 'sharing.issued.col.status',
                           'sharing.issued.col.expires', 'sharing.issued.col.action']);
    rows.forEach(function (row) {
      if (!isObj(row)) return;
      var id = str(row.invite_id);
      var status = str(row.status);
      var live = status === 'pending' || status === 'accepted';
      var tr = add(body, el('tr', { dataset: { invite: id, status: status,
                                              group: str(row.group_id) } }));
      cell(tr, str(row.group_id));
      cell(tr, t(levelKey(row.permission)));
      cell(tr, str(row.invitee));
      cell(tr, statusText(status));
      cell(tr, row.expires_at ? C.fmtTime(row.expires_at) : '');
      var td = add(tr, el('td'));
      if (!live) {
        /* Expired / revoked: shown as what they are, no revoke affordance. */
        note(td, status === 'expired' ? 'sharing.issued.status.expired'
                                      : 'sharing.issued.status.revoked',
             null, 'collab-muted');
        return;
      }
      btn(td, 'sharing.issued.revoke', function () {
        C.clear(td);
        var reason = field(td, 'sharing.issued.revoke.reason', input('text', ''));
        C.textNode(td, 'sharing.issued.revoke_confirm', null, 'p');
        var yes = add(td, el('button', { class: 'collab-btn collab-btn--danger',
                                         attrs: { type: 'button' } }));
        C.textNode(yes, 'trust.confirm.yes');
        yes.addEventListener('click', function () {
          var payload = {};
          if (str(reason.value).trim()) payload.reason = str(reason.value).trim();
          ctx.post(SHARES + '/' + encodeURIComponent(id) + '/revoke', payload).then(function (res) {
            if (!res.ok) {
              ctx.toast('sharing.issued.revoke.failed');
              errorToast(ctx, res);
              return;
            }
            ctx.toast('sharing.issued.revoked');
            state.sharesLoaded = false;
            sharesPass(ctx).then(function () { ctx.refresh(); });
          }, function (err) {
            ctx.toast('sharing.issued.revoke.failed');
            errorToast(ctx, isObj(err) ? err : { code: '' });
          });
        });
        var no = add(td, el('button', { class: 'collab-btn', attrs: { type: 'button' } }));
        C.textNode(no, 'trust.confirm.no');
        no.addEventListener('click', function () { ctx.refresh(); });
      }, { danger: true, hook: 'revoke-' + id });
    });
  }

  function renderShared(host, ctx, rows) {
    var box = section(host, 'shared', 'sharing.shared.title');
    if (!rows.length) {
      ctx.renderEmpty(box, 'sharing.shared.empty');
      return;
    }
    var body = table(box, ['sharing.shared.col.group', 'sharing.shared.col.inviter',
                           'sharing.shared.col.permission', 'sharing.shared.col.expires',
                           'sharing.issued.col.status']);
    rows.forEach(function (row) {
      if (!isObj(row)) return;
      var tr = add(body, el('tr', { dataset: { group: str(row.group_id),
                                              level: levelOf(row.permission),
                                              status: str(row.status) } }));
      cell(tr, str(row.group_id));
      cell(tr, str(row.inviter));
      cell(tr, t(levelKey(row.permission)));
      cell(tr, row.expires_at ? C.fmtTime(row.expires_at) : '');
      /* An expired row prints the server's own status word - never "still
       * shared with me". */
      cell(tr, statusText(row.status));
    });
  }

  /* ---------------------------------------------------------------- *
   * 7. accept a token (T24) + effective permissions (T25) + gate summary
   * ---------------------------------------------------------------- */

  function renderAccept(host, ctx) {
    var box = section(host, 'accept', 'sharing.accept.title');
    var form = add(box, el('div', { class: 'collab-form' }));
    var token = field(form, 'sharing.accept.token', input('text', ''));
    btn(form, 'sharing.accept.submit', function () {
      var value = str(token.value).trim();
      if (!value) {
        ctx.toast('sharing.accept.required');
        return;
      }
      ctx.post(SHARES + '/accept', { token: value }).then(function (res) {
        if (!res.ok) {
          errorToast(ctx, res);
          return;
        }
        ctx.toast('sharing.accept.done');
        token.value = '';
        state.sharesLoaded = false;
        sharesPass(ctx).then(function () { ctx.refresh(); });
      }, function (err) {
        errorToast(ctx, isObj(err) ? err : { code: '' });
      });
    }, { primary: true, hook: 'accept-submit' });
  }

  function renderPerms(host, ctx, payload) {
    var box = section(host, 'perms', 'sharing.perms.title');
    if (!state.group) {
      C.textNode(box, 'sharing.group.label', null, 'p');
      C.textNode(box, 'sharing.group.none', null, 'p');
      C.textNode(box, 'sharing.perms.need_group', null, 'p');
      return;
    }
    C.textNode(box, 'sharing.group.label', null, 'p');
    add(box, el('p', { class: 'collab-perms__group', text: state.group }));
    var data = isObj(payload) ? payload : null;
    if (!data) {
      C.textNode(box, 'sharing.perms.need_group', null, 'p');
    } else {
      var flags = [{ key: 'sharing.perms.view', ok: data.can_view === true },
                   { key: 'sharing.perms.approve', ok: data.can_approve === true },
                   { key: 'sharing.perms.manage', ok: data.can_manage === true }];
      var list = add(box, el('ul', { class: 'collab-perms' }));
      flags.forEach(function (item) {
        var li = add(list, el('li', { dataset: { level: item.key,
                                                allowed: item.ok ? 'true' : 'false' } }));
        C.textNode(li, item.key);
        add(li, el('span', { class: 'collab-perms__state',
                             text: t(item.ok ? 'sharing.perms.allowed'
                                             : 'sharing.perms.denied') }));
      });
      if (data.via) C.textNode(box, 'sharing.perms.via', { via: str(data.via) }, 'p');
      var none = !data.can_view && !data.can_approve && !data.can_manage;
      if (none && levelFor(state.group) === 'view') {
        C.textNode(box, 'sharing.perms.view_only', null, 'p');
      } else if (none) {
        C.textNode(box, 'sharing.perms.none', null, 'p');
      }
    }
    var gate = add(box, el('div', { class: 'collab-gate-list' }));
    C.textNode(gate, 'sharing.gate.title', null, 'h4');
    var level = levelFor(state.group);
    if (level) {
      C.textNode(gate, 'sharing.gate.level', { level: t(levelKey(level)) }, 'p');
      if (level === 'view') C.textNode(gate, 'sharing.gate.hide.intervene', null, 'p');
      if (level === 'view' || level === 'approve') {
        C.textNode(gate, 'sharing.gate.hide.share', null, 'p');
      }
    } else {
      /* No grant row for this group: the leader path (and the single-user local
       * install). Nothing is hidden by derivation - the server decides. */
      C.textNode(gate, 'sharing.gate.leader', null, 'p');
    }
    if (deniedFor(state.group)) {
      C.textNode(gate, 'sharing.gate.denied', null, 'p');
      C.textNode(gate, 'sharing.gate.basis', { reason: str(state.denied[state.group]) }, 'p');
    }
  }

  /* ---------------------------------------------------------------- *
   * 8. the on-behalf zone
   * ---------------------------------------------------------------- */

  function renderOnBehalf(host, ctx) {
    var box = section(host, 'onbehalf', 'sharing.onbehalf.title');
    C.textNode(box, 'sharing.onbehalf.hint', null, 'p');
    var entries = [];
    ctx.records().forEach(function (record) {
      var id = str(isObj(record) ? record.id : '');
      if (id && state.onBehalf[id]) entries.push(state.onBehalf[id]);
    });
    if (!entries.length) {
      ctx.renderEmpty(box, 'sharing.onbehalf.empty');
      return;
    }
    C.textNode(box, 'sharing.onbehalf.count', { count: entries.length }, 'p');
    var list = add(box, el('ul', { class: 'collab-onbehalf-list' }));
    entries.forEach(function (entry) {
      var li = add(list, el('li', { class: 'collab-onbehalf__row',
                                    dataset: { record: entry.id, actor: entry.actor,
                                               principal: entry.principal } }));
      add(li, onBehalfBadge(entry));
      C.textNode(li, 'sharing.onbehalf.detail',
                 { actor: entry.actor, principal: entry.principal || '—' }, 'p');
      if (entry.note) C.textNode(li, 'sharing.onbehalf.note', { note: entry.note }, 'p');
      add(li, el('span', { class: 'collab-onbehalf__id', text: entry.id }));
    });
  }

  /* ---------------------------------------------------------------- *
   * 9. render / repaint
   * ---------------------------------------------------------------- */

  function groupList(payload) {
    return isObj(payload) && isArr(payload.groups) ? payload.groups : [];
  }

  function sharesRows(payload) {
    var data = isObj(payload) ? payload : {};
    return { issued: isArr(data.issued) ? data.issued : [],
             shared: isArr(data.shared_with_me) ? data.shared_with_me : [] };
  }

  function frame(host) {
    C.clear(host);
    var head = add(host, el('header', { class: 'collab-head' }));
    C.textNode(head, 'sharing.title', null, 'h2');
    C.textNode(head, 'sharing.intro', null, 'p');
    return host;
  }

  function paint(host, ctx, groups, shares, perms) {
    frame(host);
    indexShares(shares);
    var rows = sharesRows(shares);
    renderGroups(host, ctx, groupList(groups));
    renderPerms(host, ctx, perms);
    renderIssued(host, ctx, rows.issued);
    renderShared(host, ctx, rows.shared);
    renderAccept(host, ctx);
    renderOnBehalf(host, ctx);
  }

  function offline(host, ctx, res) {
    frame(host);
    C.textNode(host, 'sharing.offline', null, 'p');
    ctx.renderError(host, ctx.panelError(res || { code: 'ROUTE_NOT_FOUND' },
                                         function () { ctx.refresh(); }));
  }

  function render(host, ctx) {
    frame(host);
    C.textNode(host, 'sharing.offline', null, 'p');
    Promise.all([ctx.get(GROUPS), ctx.get(SHARES)]).then(function (pair) {
      var groups = pair[0] && pair[0].ok ? pair[0].data : null;
      var shares = pair[1] && pair[1].ok ? pair[1].data : null;
      if (!groups && !shares) {
        offline(host, ctx, pair[1] || pair[0]);
        return;
      }
      indexShares(shares);
      var perms = state.group ? ctx.get(SHARES + '/permissions', { group_id: state.group })
                              : Promise.resolve(null);
      Promise.all([lettersPass(ctx, true), perms]).then(function (done) {
        paint(host, ctx, groups, shares, done[1] && done[1].ok ? done[1].data : null);
      });
    }, function (err) {
      offline(host, ctx, isObj(err) ? err : { code: '' });
    });
  }

  Ext.register({
    id: 'collab',
    version: '1.0',
    decorate: decorate,
    tab: {
      titleKey: 'sharing.tab',
      order: 20,
      permission: 'records.read',
      render: render,
      repaint: render
    }
  });
}(typeof window !== 'undefined' ? window : this));
