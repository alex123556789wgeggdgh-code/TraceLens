/*
 * TraceLens 5.0 - trust layer shell (web/ext/core.js)
 * ------------------------------------------------------------------
 * The nine new surfaces (secretariat + the eight extensions, docs/07) live in
 * their own files under web/ext/. This file owns the only shared machinery:
 *
 *   - the "trust centre" launcher + tabbed panel (one dialog, never a copy of
 *     an existing panel: the page already has settings / audit / users and this
 *     layer must not grow a second one);
 *   - a single request door (`UI.extRequest`), so the CSRF header, the timeout
 *     and the unified error envelope stay in app.js where they already live;
 *   - dictionary lookup for the new i18n domains (zh/en, same files);
 *   - the card-decoration pipeline: every full repaint re-applies each
 *     registered feature's decoration to the cards it cares about, because
 *     app.js rebuilds cards from scratch on every render;
 *   - toast + modal + honest empty/error states.
 *
 * Hard rules carried over from app.js:
 *   - no framework, no CDN, no build step: this is a classic script and works
 *     from file://;
 *   - every user-visible string is a dictionary key (`data-i18n` stamped), so
 *     the i18n lint can prove nothing is hard-coded;
 *   - the page never claims something it has not read: a panel that has not
 *     talked to the server says so, and a 403 is shown as a 403.
 */
(function (global) {
  'use strict';

  var UI = global.TraceLensUI;
  if (!UI || typeof UI.onRender !== 'function') return;

  var VERSION = '1.0';

  /* Dictionary domains owned by this layer (docs/07 §4.4). */
  var DOMAINS = ['trust', 'secretariat', 'rollback', 'confidence', 'evidence',
                 'groups', 'sharing', 'analytics', 'suggestions', 'compliance'];

  var plugins = [];
  var pluginById = {};
  var booted = false;

  var state = {
    open: false,
    tab: '',
    lang: 'zh',
    records: [],
    apiMode: '',
    role: '',
    perms: [],
    offline: false,
    renders: 0,
    opens: 0,
    api: { requests: 0, errors: 0, lastRequestId: '', lastStatus: null }
  };

  /* ---------------------------------------------------------------- *
   * 1. dictionary + formatting (no DOM)
   * ---------------------------------------------------------------- */

  function dictFor(lang) {
    var dicts = global.TraceLensDicts;
    if (!dicts) return null;
    return dicts[lang] || dicts.zh || null;
  }

  function currentLang() {
    var value = '';
    try { value = String(global.document.documentElement.getAttribute('data-lang') || ''); } catch (e) { value = ''; }
    if (!value && typeof UI.lang === 'function') value = String(UI.lang() || '');
    if (!value) value = state.lang || 'zh';
    return value === 'en' ? 'en' : 'zh';
  }

  /* Dictionary lookup follows app.js exactly: the dictionaries are grouped by
   * domain and the stored key is the suffix ("card" -> "field.cost"), so the
   * dotted lookup key is split once and then walked if it is deeper still. */
  function rawLookup(lang, key) {
    var dotted = String(key);
    var dot = dotted.indexOf('.');
    if (dot <= 0) return '';
    var bucket = dictFor(lang);
    if (!bucket) return '';
    var domain = dotted.slice(0, dot);
    var node = bucket[domain];
    if (!node || typeof node !== 'object') return '';
    var suffix = dotted.slice(dot + 1);
    if (Object.prototype.hasOwnProperty.call(node, suffix) && typeof node[suffix] === 'string') {
      return node[suffix];
    }
    var parts = suffix.split('.');
    for (var i = 0; i < parts.length; i += 1) {
      if (!node || typeof node !== 'object') return '';
      node = node[parts[i]];
    }
    return typeof node === 'string' ? node : '';
  }

  function interpolate(text, params) {
    if (!params || typeof text !== 'string') return text;
    return text.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : whole;
    });
  }

  function t(key, params) {
    var lang = currentLang();
    var value = rawLookup(lang, key);
    if (!value && lang !== 'zh') value = rawLookup('zh', key);
    if (!value) return String(key);
    return interpolate(value, params);
  }

  function stamp(node, key) {
    if (!node) return node;
    node.setAttribute('data-i18n', String(key));
    node.setAttribute('data-i18n-lang', currentLang());
    return node;
  }

  function fmtTime(value) {
    var text = value === undefined || value === null ? '' : String(value);
    if (!text) return '';
    try {
      return String(UI.formatTimestamp(text) || text);
    } catch (e) {
      return text;
    }
  }

  function fmtNumber(value, digits) {
    var number = typeof value === 'number' ? value : Number(value);
    if (!isFinite(number)) return '';
    var places = typeof digits === 'number' ? digits : 0;
    return number.toFixed(places);
  }

  function fmtPercent(value, digits) {
    var number = typeof value === 'number' ? value : Number(value);
    if (!isFinite(number)) return '';
    var places = typeof digits === 'number' ? digits : 1;
    return (number * 100).toFixed(places) + '%';
  }

  function fmtBytes(value) {
    var number = typeof value === 'number' ? value : Number(value);
    if (!isFinite(number) || number < 0) return '';
    if (number < 1024) return number + ' B';
    if (number < 1024 * 1024) return (number / 1024).toFixed(1) + ' KB';
    return (number / (1024 * 1024)).toFixed(2) + ' MB';
  }

  function shortHash(value) {
    var text = value === undefined || value === null ? '' : String(value);
    if (text.length <= 16) return text;
    return text.slice(0, 8) + '\u2026' + text.slice(-6);
  }

  /* ---------------------------------------------------------------- *
   * 2. request door
   * ---------------------------------------------------------------- */

  function requestIdOf(data) {
    if (!data || typeof data !== 'object') return '';
    var id = data.request_id;
    return typeof id === 'string' ? id : '';
  }

  /* One shape for every caller: {ok, status, data, code, message, requestId}.
   * A rejected transport promise is folded into the same shape, so a panel
   * never has to write two error paths. */
  function call(target, body, opts) {
    state.api.requests += 1;
    return UI.extRequest(target, body, opts).then(function (res) {
      var data = res && res.data ? res.data : null;
      var ok = !!(res && res.ok);
      var status = res && typeof res.status === 'number' ? res.status : 0;
      var code = data && typeof data.code === 'string' ? data.code : '';
      var message = data && typeof data.message === 'string' ? data.message : '';
      state.api.lastStatus = status;
      state.api.lastRequestId = requestIdOf(data);
      if (!ok) state.api.errors += 1;
      return { ok: ok, status: status, data: data, code: code, message: message,
               requestId: requestIdOf(data) };
    }, function (err) {
      state.api.errors += 1;
      var code = err && typeof err.code === 'string' ? err.code : 'NETWORK_ERROR';
      state.api.lastStatus = 0;
      return { ok: false, status: 0, data: null, code: code, message: '', requestId: '' };
    });
  }

  function get(target, query) {
    return call(target, null, { method: 'GET', query: query || null });
  }

  function post(target, body) {
    return call(target, body || {}, { method: 'POST' });
  }

  /* ---------------------------------------------------------------- *
   * 3. tiny DOM helpers
   * ---------------------------------------------------------------- */

  function el(tag, opts) {
    var node = global.document.createElement(tag);
    var options = opts || {};
    if (options.class) node.className = options.class;
    if (options.id) node.id = options.id;
    if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
    if (options.dataset) {
      Object.keys(options.dataset).forEach(function (key) {
        node.setAttribute('data-' + key, String(options.dataset[key]));
      });
    }
    if (options.attrs) {
      Object.keys(options.attrs).forEach(function (key) {
        var value = options.attrs[key];
        if (value === null || value === undefined || value === false) return;
        node.setAttribute(key, value === true ? '' : String(value));
      });
    }
    return node;
  }

  function add(parent, child) {
    if (parent && child) parent.appendChild(child);
    return child;
  }

  function textNode(parent, key, params, tag) {
    var node = add(parent, el(tag || 'span', { text: t(key, params) }));
    return stamp(node, key);
  }

  function clear(node) {
    if (!node) return node;
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ---------------------------------------------------------------- *
   * 4. toast + modal
   * ---------------------------------------------------------------- */

  var toastTimer = null;

  function ensureToast() {
    var host = global.document.getElementById('trust-toast');
    if (host) return host;
    host = el('div', { class: 'trust-toast', id: 'trust-toast', attrs: { role: 'status' } });
    host.hidden = true;
    global.document.body.appendChild(host);
    return host;
  }

  function toast(key, params, kind) {
    var host = ensureToast();
    clear(host);
    host.setAttribute('data-kind', kind || 'info');
    textNode(host, key, params || null);
    host.hidden = false;
    if (toastTimer) global.clearTimeout(toastTimer);
    toastTimer = global.setTimeout(function () {
      host.hidden = true;
      toastTimer = null;
    }, 5200);
    return host;
  }

  function ensureModal() {
    var host = global.document.getElementById('trust-modal');
    if (host) return host;
    host = el('div', { class: 'trust-modal', id: 'trust-modal', attrs: { role: 'dialog', 'aria-modal': 'true' } });
    host.hidden = true;
    var backdrop = add(host, el('div', { class: 'trust-modal__backdrop' }));
    backdrop.addEventListener('click', function () { closeModal(); });
    add(host, el('div', { class: 'trust-modal__dialog', id: 'trust-modal-body' }));
    global.document.body.appendChild(host);
    return host;
  }

  function openModal(titleKey, node) {
    var host = ensureModal();
    var body = global.document.getElementById('trust-modal-body');
    clear(body);
    var head = add(body, el('header', { class: 'trust-modal__head' }));
    textNode(head, titleKey, null, 'h2');
    var close = add(head, el('button', { class: 'trust-modal__close', attrs: { type: 'button' } }));
    stamp(close, 'trust.close');
    close.addEventListener('click', function () { closeModal(); });
    if (node) add(body, node);
    host.hidden = false;
    return host;
  }

  function closeModal() {
    var host = global.document.getElementById('trust-modal');
    if (host) host.hidden = true;
  }

  function confirmDialog(titleKey, bodyKey, onYes, confirmKey) {
    var box = el('div', { class: 'trust-confirm' });
    textNode(box, bodyKey, null, 'p');
    var row = add(box, el('div', { class: 'trust-confirm__row' }));
    var yes = add(row, el('button', { class: 'trust-btn trust-btn--danger', attrs: { type: 'button' } }));
    textNode(yes, confirmKey || 'trust.confirm.yes');
    var no = add(row, el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    textNode(no, 'trust.confirm.no');
    yes.addEventListener('click', function () {
      closeModal();
      onYes();
    });
    no.addEventListener('click', function () { closeModal(); });
    return openModal(titleKey, box);
  }

  /* ---------------------------------------------------------------- *
   * 5. shell: launcher + tabbed panel
   * ---------------------------------------------------------------- */

  function visiblePlugins() {
    return plugins.filter(function (plugin) {
      if (!plugin.tab) return false;
      if (plugin.tab.permission && !can(plugin.tab.permission)) return false;
      return true;
    }).sort(function (a, b) {
      return (a.tab.order || 100) - (b.tab.order || 100);
    });
  }

  function ensureShell() {
    var host = global.document.getElementById('trust-panel');
    if (host) return host;
    host = el('div', { class: 'trust', id: 'trust-panel', attrs: { role: 'presentation' } });
    host.hidden = true;
    var backdrop = add(host, el('div', { class: 'trust__backdrop' }));
    backdrop.addEventListener('click', function () { close(); });
    var dialog = add(host, el('section', {
      class: 'trust__dialog',
      attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'trust-title' }
    }));
    var head = add(dialog, el('header', { class: 'trust__head' }));
    var title = add(head, el('h2', { class: 'trust__title', id: 'trust-title' }));
    stamp(title, 'trust.title');
    var sub = add(head, el('p', { class: 'trust__sub' }));
    stamp(sub, 'trust.subtitle');
    var closeBtn = add(head, el('button', { class: 'trust__close', attrs: { type: 'button' } }));
    stamp(closeBtn, 'trust.close');
    closeBtn.addEventListener('click', function () { close(); });
    var tabs = add(dialog, el('nav', { class: 'trust__tabs', attrs: { role: 'tablist' } }));
    tabs.id = 'trust-tabs';
    var body = add(dialog, el('div', { class: 'trust__body', id: 'trust-body', attrs: { role: 'tabpanel' } }));
    var foot = add(dialog, el('footer', { class: 'trust__foot' }));
    var status = add(foot, el('span', { class: 'trust__status', id: 'trust-status' }));
    status.setAttribute('data-hook', 'trust-status');
    global.document.body.appendChild(host);

    var launcher = el('button', {
      class: 'trust-launcher', id: 'trust-launcher',
      attrs: { type: 'button', 'aria-haspopup': 'dialog' }
    });
    var dot = add(launcher, el('span', { class: 'trust-launcher__icon', attrs: { 'aria-hidden': 'true' } }));
    dot.textContent = '\u25C9';
    var label = add(launcher, el('span', { class: 'trust-launcher__label' }));
    stamp(label, 'trust.launcher');
    launcher.setAttribute('aria-label', t('trust.launcher'));
    launcher.addEventListener('click', function () { toggle(); });
    global.document.body.appendChild(launcher);
    return host;
  }

  function renderTabs() {
    var strip = global.document.getElementById('trust-tabs');
    if (!strip) return;
    clear(strip);
    var list = visiblePlugins();
    if (!list.length) {
      textNode(strip, 'trust.empty.plugins', null, 'p');
      return;
    }
    if (!state.tab && list.length) state.tab = list[0].id;
    list.forEach(function (plugin) {
      var button = add(strip, el('button', {
        class: 'trust-tab' + (plugin.id === state.tab ? ' is-active' : ''),
        attrs: {
          type: 'button', role: 'tab',
          'aria-selected': plugin.id === state.tab ? 'true' : 'false'
        },
        dataset: { tab: plugin.id }
      }));
      textNode(button, plugin.tab.titleKey);
      button.addEventListener('click', function () { showTab(plugin.id); });
    });
  }

  function statusLine(note) {
    var node = global.document.getElementById('trust-status');
    if (!node) return;
    clear(node);
    if (note && note.key) {
      textNode(node, note.key, note.params || null);
      node.setAttribute('data-tone', note.tone || 'info');
      return;
    }
    var notes = [];
    notes.push(t('trust.status.api', { requests: state.api.requests, errors: state.api.errors }));
    if (state.role) notes.push(t('trust.status.role', { role: state.role }));
    node.textContent = notes.join(' \u00B7 ');
    node.setAttribute('data-tone', 'info');
  }

  function showTab(id) {
    var plugin = pluginById[String(id)];
    if (!plugin || !plugin.tab) return;
    state.tab = plugin.id;
    renderTabs();
    var host = global.document.getElementById('trust-body');
    if (!host) return;
    clear(host);
    host.setAttribute('data-tab', plugin.id);
    if (plugin.tab.render) {
      try {
        plugin.tab.render(host, ctx);
      } catch (err) {
        renderError(host, { key: 'trust.state.error', params: { detail: err && err.message ? err.message : '' } });
      }
    }
    statusLine(null);
  }

  function open(tabId) {
    var host = ensureShell();
    if (!visiblePlugins().length) {
      toast('trust.empty.plugins', null, 'warn');
      return false;
    }
    host.hidden = false;
    state.open = true;
    state.opens += 1;
    if (tabId && pluginById[String(tabId)]) showTab(tabId);
    else showTab(state.tab || (visiblePlugins()[0] || {}).id);
    try { global.document.body.setAttribute('data-trust', 'open'); } catch (e) { /* no body */ }
    return true;
  }

  function close() {
    var host = global.document.getElementById('trust-panel');
    if (host) host.hidden = true;
    state.open = false;
    try { global.document.body.removeAttribute('data-trust'); } catch (e) { /* no body */ }
  }

  function toggle() {
    if (state.open) { close(); return false; }
    return open();
  }

  /* ---------------------------------------------------------------- *
   * 6. shared panel states (loading / empty / error / retry)
   * ---------------------------------------------------------------- */

  function renderLoading(host, key) {
    clear(host);
    var box = add(host, el('div', { class: 'trust-state', dataset: { state: 'loading' } }));
    var spinner = add(box, el('span', { class: 'trust-state__spinner', attrs: { 'aria-hidden': 'true' } }));
    spinner.textContent = '\u00B7\u00B7\u00B7';
    textNode(box, key || 'trust.state.loading');
    return box;
  }

  function renderError(host, note) {
    clear(host);
    if (!host) return null;
    var box = add(host, el('div', { class: 'trust-state', dataset: { state: 'error' } }));
    if (note && note.message) {
      add(box, el('p', { class: 'trust-state__detail', text: note.message }));
    } else if (note && note.key) {
      textNode(box, note.key, note.params || null, 'p');
    } else {
      textNode(box, 'trust.state.error', null, 'p');
    }
    if (note && note.code) add(box, el('p', { class: 'trust-state__code', text: String(note.code) }));
    if (note && note.requestId) {
      add(box, el('p', { class: 'trust-state__rid', text: 'req ' + String(note.requestId) }));
    }
    if (note && typeof note.retry === 'function') {
      var again = add(box, el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
      textNode(again, 'trust.state.retry');
      again.addEventListener('click', function () { note.retry(); });
    }
    return box;
  }

  function renderEmpty(host, key) {
    clear(host);
    var box = add(host, el('div', { class: 'trust-state', dataset: { state: 'empty' } }));
    textNode(box, key, null, 'p');
    return box;
  }

  /* A single place that turns a failed call into a readable panel state: the
   * server's own message wins (it is written for humans), otherwise a key. */
  function panelError(result, retry) {
    return {
      key: 'trust.state.error',
      code: result && result.code ? result.code : '',
      message: result && result.message ? result.message : '',
      requestId: result && result.requestId ? result.requestId : '',
      retry: retry
    };
  }

  /* ---------------------------------------------------------------- *
   * 7. card decoration pipeline
   * ---------------------------------------------------------------- */

  function bodyCardElements() {
    var found = [];
    try {
      found = Array.prototype.slice.call(global.document.querySelectorAll('article.card'));
    } catch (e) {
      found = [];
    }
    return found;
  }

  function decorate(records) {
    var cards = bodyCardElements();
    if (!cards.length) return;
    var index = {};
    (records || []).forEach(function (record) {
      if (record && typeof record.id === 'string') index[record.id] = record;
    });
    cards.forEach(function (card) {
      var id = card.getAttribute('data-id') || String(card.id || '').replace(/^card-/, '');
      var record = index[id];
      if (!record) return;
      plugins.forEach(function (plugin) {
        if (typeof plugin.decorate !== 'function') return;
        try {
          plugin.decorate(card, record, ctx);
        } catch (err) {
          /* one broken decoration must never break the list */
        }
      });
    });
  }

  /* ---------------------------------------------------------------- *
   * 8. registration + boot
   * ---------------------------------------------------------------- */

  function can(permission) {
    if (!permission) return true;
    if (typeof UI.can !== 'function') return true;
    try { return UI.can(permission) === true; } catch (e) { return false; }
  }

  function register(plugin) {
    if (!plugin || typeof plugin.id !== 'string' || !plugin.id) return null;
    if (pluginById[plugin.id]) return pluginById[plugin.id];
    var copy = plugin;
    copy.version = plugin.version || VERSION;
    plugins.push(copy);
    pluginById[copy.id] = copy;
    if (booted) {
      renderTabs();
      if (state.open && state.tab === copy.id) showTab(copy.id);
    }
    return copy;
  }

  function authedRole() {
    if (typeof UI.authInfo !== 'function') return '';
    try {
      var info = UI.authInfo();
      return info && typeof info.role === 'string' ? info.role : '';
    } catch (e) {
      return '';
    }
  }

  function onRender(internal) {
    state.renders += 1;
    state.lang = currentLang();
    state.records = internal && Array.isArray(internal.records) ? internal.records : [];
    state.role = authedRole();
    plugins.forEach(function (plugin) {
      if (typeof plugin.onState !== 'function') return;
      try {
        plugin.onState({ records: state.records, lang: state.lang, role: state.role }, ctx);
      } catch (e) { /* the list must keep rendering */ }
    });
    try {
      decorate(state.records);
    } catch (e) { /* decoration is additive */ }
    if (state.open) {
      statusLine(null);
      var host = global.document.getElementById('trust-body');
      var plugin = pluginById[state.tab];
      if (plugin && plugin.tab && typeof plugin.tab.repaint === 'function') {
        try { plugin.tab.repaint(host, ctx); } catch (e) { /* keep the panel */ }
      }
    }
  }

  function boot() {
    if (booted) return;
    booted = true;
    ensureShell();
    renderTabs();
    state.lang = currentLang();
    state.role = authedRole();
    global.document.addEventListener('keydown', function (ev) {
      if (!ev || ev.key !== 'Escape') return;
      var modal = global.document.getElementById('trust-modal');
      if (modal && !modal.hidden) { closeModal(); return; }
      if (state.open) close();
    });
    plugins.forEach(function (plugin) {
      if (typeof plugin.boot !== 'function') return;
      try { plugin.boot(ctx); } catch (e) { /* keep the shell alive */ }
    });
    statusLine(null);
  }

  function download(path, filename) {
    var target = String(path || '');
    if (!target) return false;
    var anchor = el('a', { attrs: { href: target, rel: 'noopener' } });
    if (filename) anchor.setAttribute('download', String(filename));
    global.document.body.appendChild(anchor);
    anchor.click();
    global.document.body.removeChild(anchor);
    return true;
  }

  /* ---------------------------------------------------------------- *
   * 9. the context every plugin receives
   * ---------------------------------------------------------------- */

  var ctx = {
    version: VERSION,
    UI: UI,
    t: t,
    stamp: stamp,
    lang: currentLang,
    can: can,
    call: call,
    get: get,
    post: post,
    el: el,
    add: add,
    clear: clear,
    textNode: textNode,
    esc: esc,
    fmtTime: fmtTime,
    fmtNumber: fmtNumber,
    fmtPercent: fmtPercent,
    fmtBytes: fmtBytes,
    shortHash: shortHash,
    toast: toast,
    openModal: openModal,
    closeModal: closeModal,
    confirmDialog: confirmDialog,
    renderLoading: renderLoading,
    renderError: renderError,
    renderEmpty: renderEmpty,
    panelError: panelError,
    open: open,
    close: close,
    showTab: showTab,
    refresh: function () { if (state.open) showTab(state.tab); },
    download: download,
    records: function () { return state.records.slice(); },
    apiState: function () {
      return { requests: state.api.requests, errors: state.api.errors,
               lastRequestId: state.api.lastRequestId, lastStatus: state.api.lastStatus };
    },
    recordById: function (id) {
      var found = null;
      state.records.forEach(function (record) {
        if (record && record.id === id) found = record;
      });
      return found;
    }
  };

  global.TraceLensExt = {
    version: VERSION,
    register: register,
    plugins: function () { return plugins.slice(); },
    open: open,
    close: close,
    toggle: toggle,
    boot: boot,
    t: t,
    ctx: ctx,
    state: function () {
      return { open: state.open, tab: state.tab, lang: state.lang,
               renders: state.renders, opens: state.opens, plugins: plugins.length,
               api: ctx.apiState() };
    }
  };

  UI.onRender(onRender);

  /* app.js boots itself on DOMContentLoaded; this file is loaded before that
   * event, so registering a one-shot listener is enough and works from file://
   * (no network, no timing assumption). */
  (function scheduleBoot() {
    var doc = global.document;
    if (!doc) return;
    if (doc.readyState === 'loading') {
      doc.addEventListener('DOMContentLoaded', function () {
        global.setTimeout(boot, 0);
      });
      return;
    }
    global.setTimeout(boot, 0);
  }());
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
