/*
 * TraceLens 5.0 - Secretariat panel (web/ext/secretariat.js)
 * ---------------------------------------------------------------------------
 * Five zones: status & policy / identity / responsibility chain (delegation) /
 * adjudication (letters) / verify & seal.
 *
 * Discipline (docs/07 §6, docs/12 §6):
 *   - every request goes through `ctx.call/get/post` (the single request gate),
 *     this file never builds its own `fetch`;
 *   - all copy comes from `ctx.t()`; this file stays free of hard-coded copy
 *     (docs/12 A14: zero CJK here, dictionary keys only);
 *   - offline (request failure) honestly renders `secretariat.offline`, never
 *     fabricated data;
 *   - write buttons are disabled when `settings.write` is missing (the server
 *     still answers 403 ROLE_FORBIDDEN; the front end only improves the UX).
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext) return;

  var API = 'api/secretariat';

  function t(key, params) { return Ext.t(key, params); }

  function canWrite(ctx) {
    try { return ctx.can('settings.write') === true; } catch (e) { return false; }
  }

  function text(key, params, tag) {
    return Ext.ctx.el(tag || 'p', { text: t(key, params) });
  }

  function section(host, key) {
    var box = Ext.ctx.el('section', { class: 'sec-zone', dataset: { zone: key } });
    var head = Ext.ctx.add(box, Ext.ctx.el('header', { class: 'sec-zone__head' }));
    Ext.ctx.textNode(head, 'secretariat.' + key + '.title', null, 'h3');
    Ext.ctx.add(host, box);
    return box;
  }

  function btn(parent, key, onClick, opts) {
    var node = Ext.ctx.el('button', {
      class: 'sec-btn' + (opts && opts.primary ? ' sec-btn--primary' : ''),
      attrs: { type: 'button' }
    });
    Ext.ctx.textNode(node, key);
    if (opts && opts.disabled) node.disabled = true;
    node.addEventListener('click', onClick);
    Ext.ctx.add(parent, node);
    return node;
  }

  function kv(parent, labelKey, value, params) {
    var row = Ext.ctx.el('div', { class: 'sec-kv' });
    var dt = Ext.ctx.add(row, Ext.ctx.el('dt'));
    Ext.ctx.textNode(dt, labelKey, params || null);
    var dd = Ext.ctx.add(row, Ext.ctx.el('dd'));
    dd.textContent = value == null || value === '' ? '—' : String(value);
    Ext.ctx.add(parent, row);
    return row;
  }

  function table(parent, columns) {
    var wrap = Ext.ctx.el('div', { class: 'sec-table-wrap' });
    var tableEl = Ext.ctx.add(wrap, Ext.ctx.el('table', { class: 'sec-table' }));
    var headRow = Ext.ctx.add(Ext.ctx.add(tableEl, Ext.ctx.el('thead')), Ext.ctx.el('tr'));
    columns.forEach(function (column) {
      var th = Ext.ctx.add(headRow, Ext.ctx.el('th', { attrs: { scope: 'col' } }));
      if (column.indexOf('secretariat.') === 0) Ext.ctx.textNode(th, column);
      else th.textContent = column;
    });
    var body = Ext.ctx.add(tableEl, Ext.ctx.el('tbody'));
    Ext.ctx.add(parent, wrap);
    return body;
  }

  function cell(row, value) {
    var td = Ext.ctx.add(row, Ext.ctx.el('td'));
    td.textContent = value == null || value === '' ? '—' : String(value);
    return td;
  }

  function field(parent, labelKey, input) {
    var wrap = Ext.ctx.el('label', { class: 'sec-field' });
    var caption = Ext.ctx.add(wrap, Ext.ctx.el('span', { class: 'sec-field__label' }));
    Ext.ctx.textNode(caption, labelKey);
    Ext.ctx.add(wrap, input);
    Ext.ctx.add(parent, wrap);
    return input;
  }

  function input(kind, placeholder) {
    return Ext.ctx.el('input', {
      class: 'sec-input',
      attrs: { type: kind || 'text', autocomplete: 'off',
               placeholder: placeholder || '' }
    });
  }

  function errorToast(ctx, res) {
    var code = res && res.code ? String(res.code).toLowerCase() : '';
    ctx.toast(code ? 'secretariat.error.' + code : 'secretariat.offline');
  }

  function formBox(parent, titleKey) {
    var box = Ext.ctx.el('div', { class: 'sec-form' });
    var head = Ext.ctx.add(box, Ext.ctx.el('h4'));
    Ext.ctx.textNode(head, titleKey);
    Ext.ctx.add(parent, box);
    return box;
  }

  function note(parent, key, params) {
    var node = Ext.ctx.add(parent, Ext.ctx.el('p', { class: 'sec-note' }));
    Ext.ctx.textNode(node, key, params || null);
    return node;
  }

  function fail(host, ctx, res) {
    var zone = host.querySelector('.sec-zone');
    var target = zone || host;
    ctx.renderError(target, ctx.panelError(res, function () { ctx.refresh(); }));
  }

  /* ---------------------------------------------------------------- *
   * 1. Status + risk policy
   * ---------------------------------------------------------------- */
  function renderStatus(host, ctx, body) {
    var box = section(host, 'status');
    var status = (body && body.secretariat) || {};
    var counts = status.counts || {};
    var list = Ext.ctx.add(box, Ext.ctx.el('dl', { class: 'sec-kv-list' }));
    kv(list, 'secretariat.status.identities', counts.identities);
    kv(list, 'secretariat.status.delegations', counts.delegations);
    kv(list, 'secretariat.status.letters', counts.letters);
    var chains = status.chains || {};
    ['identities', 'delegations', 'letters'].forEach(function (name) {
      var chain = chains[name] || {};
      kv(list, 'secretariat.status.chain',
         name + ' · ' + (chain.ok ? t('secretariat.status.chain_ok')
                                  : t('secretariat.status.chain_broken')));
    });
    kv(list, 'secretariat.status.version_label', status.version);
    note(box, 'secretariat.status.tsa_local');
    if (status.degraded && status.degraded.length) {
      kv(list, 'secretariat.status.degraded', status.degraded.join(', '));
    }

    var policy = status.risk_policy || {};
    var tableBody = table(box, ['secretariat.policy.risk', 'secretariat.policy.approval',
                                'secretariat.policy.seal']);
    Object.keys(policy).forEach(function (level) {
      var spec = policy[level] || {};
      var row = Ext.ctx.add(tableBody, Ext.ctx.el('tr'));
      cell(row, t('secretariat.policy.risk.' + level));
      var needed = Number(spec.approvers || 0);
      cell(row, needed === 0 ? t('secretariat.policy.approval.none')
                             : t('secretariat.policy.approval.' + (needed === 1 ? 'one' : 'two')));
      cell(row, spec.seal ? t('secretariat.policy.seal') : '—');
    });
    note(box, 'secretariat.policy.note');
  }

  /* ---------------------------------------------------------------- *
   * 2. Identity registration
   * ---------------------------------------------------------------- */
  function renderIdentities(host, ctx) {
    var box = section(host, 'identities');
    var writable = canWrite(ctx);
    var body = Ext.ctx.add(box, Ext.ctx.el('div'));
    ctx.get(API + '/identities').then(function (res) {
      if (!res.ok) { fail(box, ctx, res); return; }
      var items = (res.data && res.data.identities) || [];
      Ext.ctx.clear(body);
      if (!items.length) {
        ctx.renderEmpty(body, 'secretariat.identities.empty');
      } else {
        var rows = table(body, ['secretariat.col.agent', 'secretariat.col.fingerprint',
                                'secretariat.col.owner', 'secretariat.col.created',
                                'secretariat.col.revoked', '']);
        items.forEach(function (item) {
          var row = Ext.ctx.add(rows, Ext.ctx.el('tr'));
          cell(row, item.agent_id);
          cell(row, item.fingerprint);
          cell(row, item.owner_did);
          cell(row, item.created_at);
          cell(row, item.revoked_at ? t('secretariat.identities.revoked')
                                    : t('secretariat.identities.active'));
          var actions = Ext.ctx.add(row, Ext.ctx.el('td'));
          if (!item.revoked_at) {
            btn(actions, 'secretariat.identities.revoke', function () {
              ctx.confirmDialog('secretariat.identities.revoke_confirm',
                                'secretariat.identities.revoke_confirm',
                                function () {
                                  ctx.post(API + '/identities/' +
                                           encodeURIComponent(item.agent_id) + '/revoke', {})
                                    .then(function (out) {
                                      if (!out.ok) { errorToast(ctx, out); return; }
                                      ctx.refresh();
                                    });
                                }, 'secretariat.identities.revoke');
            }, { disabled: !writable });
          }
        });
      }
      var form = formBox(box, 'secretariat.form.title');
      var agent = field(form, 'secretariat.form.agent_id', input());
      var owner = field(form, 'secretariat.form.owner_did', input());
      var image = field(form, 'secretariat.form.image_hash', input());
      var boot = field(form, 'secretariat.form.boot_params', Ext.ctx.el('textarea', {
        class: 'sec-input', attrs: { rows: '2' }
      }));
      var rotateLabel = Ext.ctx.add(form, Ext.ctx.el('label', { class: 'sec-check' }));
      var rotate = Ext.ctx.add(rotateLabel, Ext.ctx.el('input', { attrs: { type: 'checkbox' } }));
      Ext.ctx.textNode(rotateLabel, 'secretariat.form.rotate');
      var submit = btn(form, 'secretariat.form.submit', function () {
        var payload = { agent_id: agent.value.trim(), owner_did: owner.value.trim() };
        if (image.value.trim()) payload.image_hash = image.value.trim();
        if (boot.value.trim()) {
          try { payload.boot_params = JSON.parse(boot.value); }
          catch (e) { ctx.toast('secretariat.form.boot_params.invalid'); return; }
        }
        if (rotate.checked) payload.rotate = true;
        ctx.post(API + '/identities', payload).then(function (res2) {
          if (!res2.ok) { errorToast(ctx, res2); return; }
          ctx.refresh();
        });
      }, { primary: true, disabled: !writable });
      if (!writable) note(form, 'secretariat.gate.settings_write');
      submit.setAttribute('data-action', 'register-identity');
    });
  }

  /* ---------------------------------------------------------------- *
   * 3. Responsibility chain (delegation)
   * ---------------------------------------------------------------- */
  function renderDelegations(host, ctx) {
    var box = section(host, 'delegations');
    var writable = canWrite(ctx);
    var body = Ext.ctx.add(box, Ext.ctx.el('div'));
    ctx.get(API + '/delegations').then(function (res) {
      if (!res.ok) { fail(box, ctx, res); return; }
      var items = (res.data && res.data.delegations) || [];
      Ext.ctx.clear(body);
      if (!items.length) ctx.renderEmpty(body, 'secretariat.delegations.empty');
      else {
        var rows = table(body, ['secretariat.col.agent', 'secretariat.delegations.col.scope',
                                'secretariat.delegations.col.depth',
                                'secretariat.delegations.col.expires',
                                'secretariat.delegations.col.status']);
        items.forEach(function (item) {
          var row = Ext.ctx.add(rows, Ext.ctx.el('tr'));
          cell(row, (item.delegator_did || '') + ' → ' + (item.delegatee_did || ''));
          var scope = item.scope || {};
          cell(row, [].concat(scope.action_types || [], scope.tools || [],
                              scope.max_risk ? '≤' + scope.max_risk : []).join(' · '));
          cell(row, item.depth);
          cell(row, item.expires_at);
          cell(row, item.expired ? t('secretariat.delegations.expired')
                                 : t('secretariat.delegations.active'));
        });
      }
      var form = formBox(box, 'secretariat.form.delegation.title');
      var delegator = field(form, 'secretariat.form.delegator', input());
      var delegatee = field(form, 'secretariat.form.delegatee', input());
      var actions = field(form, 'secretariat.form.actions', input('text', 'read,write'));
      var tools = field(form, 'secretariat.form.tools', input('text', 'calendar.read'));
      var maxRisk = field(form, 'secretariat.form.max_risk', input('text', 'low'));
      var ttl = field(form, 'secretariat.form.ttl', input('number', '2592000'));
      var parent = field(form, 'secretariat.form.parent_id', input());
      btn(form, 'secretariat.form.delegation.submit', function () {
        var scope = { max_risk: maxRisk.value.trim() || 'low' };
        if (actions.value.trim()) {
          scope.action_types = actions.value.split(',').map(function (s) { return s.trim(); })
            .filter(Boolean);
        }
        if (tools.value.trim()) {
          scope.tools = tools.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        }
        var payload = {
          delegator_did: delegator.value.trim(), delegatee_did: delegatee.value.trim(),
          scope: scope
        };
        if (ttl.value.trim()) payload.ttl_seconds = Number(ttl.value);
        if (parent.value.trim()) payload.parent_id = parent.value.trim();
        ctx.post(API + '/delegations', payload).then(function (res2) {
          if (!res2.ok) { errorToast(ctx, res2); return; }
          ctx.refresh();
        });
      }, { primary: true, disabled: !writable });
      note(box, 'secretariat.delegations.max_depth', { max: (res.data && res.data.max_depth) || 5 });
      if (!writable) note(form, 'secretariat.gate.settings_write');
    });
  }

  /* ---------------------------------------------------------------- *
   * 4. Adjudication (letters)
   * ---------------------------------------------------------------- */
  function letterChecks(letter) {
    var verification = (letter && letter.verification) || {};
    return [
      ['signatures_ok', verification.signatures_ok],
      ['chain_ok', verification.chain_ok],
      ['records_root', Boolean(letter.records_root)],
      ['audit_head', Boolean(letter.audit_head)],
      ['tsa', verification.tsa_state === 'local_only' ? 'local_only' : verification.tsa_state]
    ];
  }

  function openLetter(ctx, letter) {
    var box = Ext.ctx.el('div', { class: 'sec-letter' });
    kv(box, 'secretariat.detail.subject', letter.subject);
    kv(box, 'secretariat.detail.title', letter.responsibility ?
       t('secretariat.responsibility.' + letter.responsibility.role) : '');
    kv(box, 'secretariat.detail.hash.records_root', letter.records_root);
    kv(box, 'secretariat.detail.hash.audit_head', letter.audit_head || '');
    kv(box, 'secretariat.detail.tsa.local', letter.verification ?
       letter.verification.tsa_state : '');
    var checks = Ext.ctx.add(box, Ext.ctx.el('dl', { class: 'sec-kv-list' }));
    letterChecks(letter).forEach(function (pair) {
      kv(checks, 'secretariat.detail.check.' + pair[0],
         pair[1] ? t('secretariat.detail.pass') : t('secretariat.detail.fail'));
    });
    var actions = (letter.actions || []);
    var rows = table(box, ['secretariat.col.agent', 'secretariat.letters.records',
                           'secretariat.detail.checks']);
    actions.forEach(function (action) {
      var row = Ext.ctx.add(rows, Ext.ctx.el('tr'));
      cell(row, action.agent_id);
      cell(row, action.id);
      cell(row, action.signature_ok ? t('secretariat.detail.pass') : t('secretariat.detail.fail'));
    });
    var foot = Ext.ctx.add(box, Ext.ctx.el('div', { class: 'sec-form__foot' }));
    btn(foot, 'secretariat.detail.download', function () {
      try {
        var blob = new global.Blob([JSON.stringify(letter, null, 2)],
                                   { type: 'application/json' });
        var url = global.URL.createObjectURL(blob);
        ctx.download(url, (letter.id || 'letter') + '.json');
        global.setTimeout(function () { global.URL.revokeObjectURL(url); }, 2000);
        ctx.toast('secretariat.detail.download.done');
      } catch (e) {
        ctx.toast('secretariat.detail.download.failed');
      }
    }, { primary: true });
    ctx.openModal('secretariat.letters.detail', box);
  }

  function renderLetters(host, ctx) {
    var box = section(host, 'letters');
    var writable = canWrite(ctx);
    var body = Ext.ctx.add(box, Ext.ctx.el('div'));
    ctx.get(API + '/letters').then(function (res) {
      if (!res.ok) { fail(box, ctx, res); return; }
      var items = (res.data && res.data.letters) || [];
      Ext.ctx.clear(body);
      if (!items.length) ctx.renderEmpty(body, 'secretariat.letters.empty');
      else {
        var rows = table(body, ['secretariat.letters.subject', 'secretariat.letters.created', 'secretariat.letters.responsibility', 'secretariat.letters.col.records', '']);
        items.forEach(function (item) {
          var row = Ext.ctx.add(rows, Ext.ctx.el('tr'));
          cell(row, item.subject);
          cell(row, item.created_at);
          cell(row, item.responsibility);
          cell(row, item.records);
          var actions = Ext.ctx.add(row, Ext.ctx.el('td'));
          btn(actions, 'secretariat.letters.view', function () {
            ctx.get(API + '/letters/' + encodeURIComponent(item.id)).then(function (out) {
              if (!out.ok) { errorToast(ctx, out); return; }
              openLetter(ctx, out.data.letter);
            });
          });
        });
      }
      var form = formBox(box, 'secretariat.form.letter.title');
      var subject = field(form, 'secretariat.form.letter.subject', input());
      var records = Ext.ctx.add(form, Ext.ctx.el('fieldset', { class: 'sec-records' }));
      Ext.ctx.textNode(Ext.ctx.add(records, Ext.ctx.el('legend')), 'secretariat.form.letter.record_ids');
      var picked = {};
      ctx.records().slice(0, 40).forEach(function (record) {
        var label = Ext.ctx.add(records, Ext.ctx.el('label', { class: 'sec-check' }));
        var boxEl = Ext.ctx.add(label, Ext.ctx.el('input', {
          attrs: { type: 'checkbox', value: record.id }
        }));
        boxEl.addEventListener('change', function () {
          if (boxEl.checked) picked[record.id] = true; else delete picked[record.id];
        });
        var text = Ext.ctx.add(label, Ext.ctx.el('span'));
        text.textContent = record.id + ' · ' +
          (record.human_readable && record.human_readable.l1 ? record.human_readable.l1 : '');
      });
      var responsibility = field(form, 'secretariat.form.letter.responsibility',
                                 input('text', 'technical'));
      var noteInput = field(form, 'secretariat.form.letter.note', Ext.ctx.el('textarea', {
        class: 'sec-input', attrs: { rows: '2' }
      }));
      btn(form, 'secretariat.form.letter.submit', function () {
        var ids = Object.keys(picked);
        if (!ids.length) { ctx.toast('secretariat.letters.selected'); return; }
        var payload = {
          subject: subject.value.trim(), record_ids: ids,
          responsibility: responsibility.value.trim() || 'technical',
          note: noteInput.value.trim()
        };
        ctx.post(API + '/letters', payload).then(function (res2) {
          if (!res2.ok) { errorToast(ctx, res2); return; }
          ctx.toast('secretariat.letters.generated');
          ctx.refresh();
        });
      }, { primary: true, disabled: !writable });
      if (!writable) note(form, 'secretariat.gate.settings_write');
    });
  }

  /* ---------------------------------------------------------------- *
   * 5. Verify & seal
   * ---------------------------------------------------------------- */
  function renderVerify(host, ctx) {
    var box = section(host, 'verify');
    note(box, 'secretariat.verify.hint');
    var area = Ext.ctx.add(box, Ext.ctx.el('textarea', {
      class: 'sec-input sec-json', attrs: { rows: '6', spellcheck: 'false' }
    }));
    var result = Ext.ctx.add(box, Ext.ctx.el('div', { class: 'sec-result' }));
    ctx.renderEmpty(result, 'secretariat.verify.empty');
    btn(box, 'secretariat.verify.run', function () {
      var parsed = null;
      try { parsed = JSON.parse(area.value); }
      catch (e) { ctx.toast('secretariat.verify.empty'); return; }
      ctx.post(API + '/verify', { record: parsed }).then(function (res) {
        if (!res.ok) { ctx.renderError(result, ctx.panelError(res)); return; }
        Ext.ctx.clear(result);
        var data = res.data || {};
        var one = (data.results || [])[0] || {};
        var head = Ext.ctx.add(result, Ext.ctx.el('p', { class: 'sec-verdict' }));
        Ext.ctx.textNode(head, one.ok ? 'secretariat.verify.pass' : 'secretariat.verify.fail');
        var list = Ext.ctx.add(result, Ext.ctx.el('dl', { class: 'sec-kv-list' }));
        Object.keys(one.checks || {}).forEach(function (key) {
          var value = one.checks[key];
          kv(list, 'secretariat.detail.check.' + key,
             typeof value === 'object' ? JSON.stringify(value) : String(value));
        });
        if ((one.problems || []).length) {
          var ul = Ext.ctx.add(result, Ext.ctx.el('ul', { class: 'sec-problems' }));
          one.problems.forEach(function (problem) {
            Ext.ctx.add(ul, Ext.ctx.el('li', { text: problem.code }));
          });
        }
      });
    }, { primary: true });

    var form = formBox(box, 'secretariat.sign.title');
    var signId = field(form, 'secretariat.sign.record_id', input());
    var signRisk = field(form, 'secretariat.sign.risk', input('text', 'low'));
    var signApprovers = field(form, 'secretariat.sign.approvers', input('text',
      'did:tracelens:user:...'));
    var writable = canWrite(ctx);
    btn(form, 'secretariat.sign.submit', function () {
      var payload = { risk_level: signRisk.value.trim() || 'low' };
      if (signApprovers.value.trim()) {
        payload.approvers = signApprovers.value.split(',').map(function (s) { return s.trim(); })
          .filter(Boolean);
      }
      ctx.post(API + '/records/' + encodeURIComponent(signId.value.trim()) + '/sign', payload)
        .then(function (res) {
          if (!res.ok) { errorToast(ctx, res); return; }
          ctx.toast('secretariat.sign.done');
        });
    }, { primary: true, disabled: !writable });
    if (!writable) note(form, 'secretariat.gate.settings_write');

    var sealForm = formBox(box, 'secretariat.seal.title');
    var recordId = field(sealForm, 'secretariat.seal.record_id', input());
    var reason = field(sealForm, 'secretariat.seal.reason', input());
    btn(sealForm, 'secretariat.seal.submit', function () {
      ctx.post(API + '/seal', { record_id: recordId.value.trim(), reason: reason.value.trim() })
        .then(function (res) {
          if (!res.ok) { errorToast(ctx, res); return; }
          ctx.toast('secretariat.seal.done');
        });
    }, { disabled: !writable });
    if (!writable) note(sealForm, 'secretariat.gate.settings_write');
  }

  function renderHonesty(host) {
    var box = Ext.ctx.add(host, Ext.ctx.el('section', { class: 'sec-zone sec-zone--honesty' }));
    Ext.ctx.textNode(Ext.ctx.add(box, Ext.ctx.el('h3')), 'secretariat.honesty.title');
    var list = Ext.ctx.add(box, Ext.ctx.el('ul', { class: 'sec-honesty' }));
    ['p1', 'p2', 'p3', 'p4', 'p5'].forEach(function (key) {
      Ext.ctx.add(list, Ext.ctx.el('li', { text: t('secretariat.honesty.' + key) }));
    });
  }

  function render(host, ctx) {
    Ext.ctx.clear(host);
    Ext.ctx.add(host, Ext.ctx.el('p', { class: 'sec-intro', text: t('secretariat.intro') }));
    Ext.ctx.add(host, Ext.ctx.el('p', { class: 'sec-offline', text: t('secretariat.offline') }));
    ctx.get(API).then(function (res) {
      if (!res.ok) {
        ctx.renderError(host, ctx.panelError(res, function () { ctx.refresh(); }));
        return;
      }
      Ext.ctx.clear(host);
      Ext.ctx.add(host, Ext.ctx.el('p', { class: 'sec-intro', text: t('secretariat.intro') }));
      renderStatus(host, ctx, res.data);
      renderIdentities(host, ctx);
      renderDelegations(host, ctx);
      renderLetters(host, ctx);
      renderVerify(host, ctx);
      renderHonesty(host);
    }, function (err) {
      ctx.renderError(host, ctx.panelError(err || { code: 'ROUTE_NOT_FOUND' },
                                            function () { ctx.refresh(); }));
    });
  }

  Ext.register({
    id: 'secretariat',
    version: '1.0',
    tab: {
      titleKey: 'secretariat.tab',
      order: 10,
      permission: 'audit.read',
      render: render,
      repaint: render
    }
  });
}(typeof window !== 'undefined' ? window : this));


