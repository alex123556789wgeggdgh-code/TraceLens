/*
 * TraceLens 0.6.0 - update channel face (web/ext/update.js)
 * ------------------------------------------------------------------
 * The settings card hosts this section; app.js only creates the mount point and
 * hands over its DOM primitives. Everything below is this module's business:
 *
 *   1. FACTS. Current version, desktop shell version, installed versions - all
 *      read from `window.tracelensDesktop.update.status()`. No number is
 *      computed here (no "downloaded 40%" from a byte count we do not have).
 *
 *   2. HONESTY ABOUT WHERE WE ARE. When the page runs in a plain browser there
 *      is no updater at all: the section says so in one line and renders no
 *      controls. A disabled button that looks clickable is worse than a
 *      sentence explaining why.
 *
 *   3. INDETERMINATE PROGRESS. Installing is one IPC call that answers when it
 *      is done. The server-bridge publishes no byte counts, so a percentage
 *      would be invented. The bar is `data-determinate="false"` with
 *      `aria-valuetext` (a sentence), no timer, no width written from here.
 *      Same rule as the export panel (R8-6), applied for the same reason.
 *
 *   4. FAILURE IS A FIRST-CLASS STATE. Every failure carries a reason code from
 *      desktop/updater.js. Known codes get a translated sentence; unknown ones
 *      fall back to a generic sentence plus the raw technical text in a <code>,
 *      so a user can paste it into a ticket without us pretending to know.
 *
 * All copy is a dictionary key (`update.*` domain). No CJK literal, no
 * sentence-shaped English literal lives in this file.
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext || typeof Ext.register !== 'function' || !Ext.ctx) return;

  var ctx = Ext.ctx;
  var VERSION = '1.0';

  var HOOK = 'update';
  /* Reason codes that have a translated sentence in `update.reason.*`.
   * Anything else falls through to `update.reason.other`, and the engine's own
   * message is printed verbatim in a <code> so the user can still quote it.
   * Keeping this list and parts/update.*.json in step is checked by
   * tests/test_update_face.py - a code with no sentence is worse than a
   * generic sentence, because it renders as a bare token. */
  var KNOWN_REASONS = {
    source_not_configured: 1, source_unreachable: 1, timeout: 1, http_status: 1,
    bad_index: 1, downgrade_refused: 1, already_installed: 1,
    package_hash_mismatch: 1, host_missing_capability: 1, host_too_old: 1,
    hash_mismatch: 1, missing_entries: 1, undeclared_entries: 1, unsafe_path: 1,
    no_previous_version: 1, no_target: 1, install_exception: 1,
    status_failed: 1, check_failed: 1, install_failed: 1
  };

  var state = {
    phase: 'idle',        /* idle | loading | checking | installing | done | error */
    status: null,
    check: null,
    result: null,
    error: null,          /* {reason, message} */
    loaded: false
  };

  function str(value) {
    if (value === null || value === undefined) return '';
    return String(value);
  }

  function clearHost(host) {
    while (host.firstChild) host.removeChild(host.firstChild);
  }

  function bridge() {
    return global.tracelensDesktop && global.tracelensDesktop.update
      ? global.tracelensDesktop.update : null;
  }

  function reasonKey(reason) {
    var code = str(reason);
    return KNOWN_REASONS[code] ? 'update.reason.' + code : 'update.reason.other';
  }

  /* ---------------------------------------------------------------- *
   * 1. progress bar - indeterminate, by construction
   * ---------------------------------------------------------------- */
  function renderProgress(host, busy) {
    clearHost(host);
    host.setAttribute('data-hook', HOOK + '-progress');
    host.setAttribute('data-determinate', 'false');
    host.setAttribute('data-percent', '');
    host.setAttribute('data-visible', busy ? 'true' : 'false');
    if (!busy) return null;
    var track = ctx.add(host, ctx.el('div', {
      class: 'update__progress',
      attrs: {
        'data-hook': HOOK + '-progress-bar',
        role: 'progressbar',
        'aria-label': ctx.t('update.progress.aria'),
        'aria-valuetext': ctx.t('update.progress.busy')
      }
    }));
    ctx.add(track, ctx.el('span', {
      class: 'update__progress-fill',
      attrs: { 'data-hook': HOOK + '-progress-fill', 'aria-hidden': 'true' }
    }));
    ctx.add(host, ctx.el('p', {
      class: 'update__note',
      text: ctx.t('update.progress.busy'),
      attrs: { 'data-hook': HOOK + '-progress-note' }
    }));
    return track;
  }

  /* ---------------------------------------------------------------- *
   * 2. facts - every value comes from status()
   * ---------------------------------------------------------------- */
  function factRow(list, field, labelKey, value) {
    var row = ctx.add(list, ctx.el('div', {
      class: 'update__fact',
      attrs: { 'data-hook': HOOK + '-fact', 'data-field': field }
    }));
    ctx.add(row, ctx.el('dt', { class: 'update__fact-key', text: ctx.t(labelKey) }));
    ctx.add(row, ctx.el('dd', {
      class: 'update__fact-val',
      text: value,
      attrs: { 'data-hook': HOOK + '-value', 'data-field': field }
    }));
    return row;
  }

  function statusLine(host) {
    var line = ctx.add(host, ctx.el('p', {
      class: 'update__status',
      attrs: { 'data-hook': HOOK + '-status', 'data-phase': state.phase, role: 'status' }
    }));
    var text = '';
    if (state.phase === 'loading') {
      text = ctx.t('update.state.loading');
    } else if (state.phase === 'checking') {
      text = ctx.t('update.state.checking');
    } else if (state.phase === 'installing') {
      text = ctx.t('update.state.installing');
    } else if (state.phase === 'done') {
      text = ctx.t('update.state.installed', { value: str(state.result && state.result.version) });
    } else if (state.phase === 'error') {
      text = ctx.t(reasonKey(state.error && state.error.reason),
                   { value: str(state.error && state.error.message) });
    } else if (state.check) {
      if (state.check.ok === false) {
        text = ctx.t(reasonKey(state.check.reason), { value: str(state.check.message) });
      } else if (state.check.available) {
        text = ctx.t('update.state.available',
                     { value: str(state.check.latest), current: str(state.check.current || state.check.hostVersion) });
      } else {
        text = ctx.t('update.state.uptodate',
                     { value: str(state.check.current || state.check.hostVersion) });
      }
    }
    line.textContent = text;
    return line;
  }

  /* ---------------------------------------------------------------- *
   * 3. the section
   * ---------------------------------------------------------------- */
  function render(host) {
    if (!host) return null;
    clearHost(host);
    host.setAttribute('data-hook', HOOK + '-section');

    ctx.add(host, ctx.el('h3', {
      class: 'update__title',
      text: ctx.t('update.title'),
      attrs: { 'data-hook': HOOK + '-title' }
    }));

    var api = bridge();
    if (!api) {
      /* plain browser: say it, do not draw dead controls */
      host.setAttribute('data-browser-mode', 'true');
      ctx.add(host, ctx.el('p', {
        class: 'update__hint',
        text: ctx.t('update.hint.browser'),
        attrs: { 'data-hook': HOOK + '-browser-hint' }
      }));
      return host;
    }
    host.setAttribute('data-browser-mode', 'false');

    var facts = ctx.add(host, ctx.el('dl', {
      class: 'update__facts',
      attrs: { 'data-hook': HOOK + '-facts' }
    }));
    var st = state.status || {};
    factRow(facts, 'active', 'update.current', st.active ? str(st.active) : ctx.t('update.current.builtin'));
    factRow(facts, 'host', 'update.host', str(st.hostVersion || '-'));
    factRow(facts, 'installed', 'update.installed',
            Array.isArray(st.installed) && st.installed.length ? st.installed.join(', ') : '-');

    /* source: only the user can set it; the renderer never guesses an address */
    var srcBox = ctx.add(host, ctx.el('div', {
      class: 'update__source',
      attrs: { 'data-hook': HOOK + '-source' }
    }));
    var label = ctx.add(srcBox, ctx.el('label', {
      class: 'update__label',
      text: ctx.t('update.source.label'),
      attrs: { 'data-hook': HOOK + '-source-label' }
    }));
    var input = ctx.add(srcBox, ctx.el('input', {
      class: 'update__input',
      attrs: {
        'data-hook': HOOK + '-source-input',
        type: 'text',
        maxlength: '512',
        placeholder: ctx.t('update.source.placeholder'),
        'aria-label': ctx.t('update.source.label')
      }
    }));
    input.value = str(st.source || '');
    label.setAttribute('for', '');
    ctx.add(srcBox, ctx.el('p', {
      class: 'update__hint',
      text: ctx.t('update.source.hint'),
      attrs: { 'data-hook': HOOK + '-source-hint' }
    }));

    var actions = ctx.add(host, ctx.el('div', {
      class: 'update__actions',
      attrs: { 'data-hook': HOOK + '-actions' }
    }));
    var busy = state.phase === 'checking' || state.phase === 'installing' || state.phase === 'loading';

    var saveBtn = ctx.add(actions, ctx.el('button', {
      class: 'update__button',
      text: ctx.t('update.action.save'),
      attrs: { 'data-hook': HOOK + '-save', type: 'button' }
    }));
    saveBtn.addEventListener('click', function () { saveSource(input.value); });

    var checkBtn = ctx.add(actions, ctx.el('button', {
      class: 'update__button',
      text: ctx.t('update.action.check'),
      attrs: { 'data-hook': HOOK + '-check', type: 'button' }
    }));
    if (busy) checkBtn.setAttribute('disabled', 'disabled');
    checkBtn.addEventListener('click', function () { doCheck(); });

    var canInstall = !busy && state.check && state.check.ok && state.check.available;
    var installBtn = ctx.add(actions, ctx.el('button', {
      class: 'update__button update__button--primary',
      text: ctx.t('update.action.install'),
      attrs: { 'data-hook': HOOK + '-install', type: 'button',
               'data-enabled': canInstall ? 'true' : 'false' }
    }));
    if (!canInstall) installBtn.setAttribute('disabled', 'disabled');
    installBtn.addEventListener('click', function () { doInstall(); });

    var needsRestart = state.phase === 'done' && state.result && state.result.requiresRestart;
    var restartBtn = ctx.add(actions, ctx.el('button', {
      class: 'update__button',
      text: ctx.t('update.action.restart'),
      attrs: { 'data-hook': HOOK + '-restart', type: 'button',
               'data-visible': needsRestart ? 'true' : 'false' }
    }));
    if (!needsRestart) restartBtn.setAttribute('disabled', 'disabled');
    restartBtn.addEventListener('click', function () { api.restart(); });

    var canRollback = !busy && Array.isArray(st.installed) && st.installed.length > 1;
    var rollbackBtn = ctx.add(actions, ctx.el('button', {
      class: 'update__button',
      text: ctx.t('update.action.rollback'),
      attrs: { 'data-hook': HOOK + '-rollback', type: 'button',
               'data-enabled': canRollback ? 'true' : 'false' }
    }));
    if (!canRollback) rollbackBtn.setAttribute('disabled', 'disabled');
    rollbackBtn.addEventListener('click', function () { doRollback(); });

    var progress = ctx.add(host, ctx.el('div', {
      class: 'update__progress-host',
      attrs: { 'data-hook': HOOK + '-progress-host' }
    }));
    renderProgress(progress, busy);

    statusLine(host);

    if (state.error && state.error.message) {
      ctx.add(host, ctx.el('code', {
        class: 'update__raw',
        text: str(state.error.message),
        attrs: { 'data-hook': HOOK + '-error-raw' }
      }));
    }

    if (state.check && state.check.notes) {
      ctx.add(host, ctx.el('p', {
        class: 'update__hint',
        text: ctx.t('update.notes', { value: str(state.check.notes) }),
        attrs: { 'data-hook': HOOK + '-notes' }
      }));
    }
    if (state.check && state.check.warning) {
      ctx.add(host, ctx.el('p', {
        class: 'update__warn',
        text: str(state.check.warning),
        attrs: { 'data-hook': HOOK + '-warning' }
      }));
    }
    return host;
  }

  /* ---------------------------------------------------------------- *
   * 4. actions
   * ---------------------------------------------------------------- */
  var hostRef = null;

  function repaint() {
    if (hostRef && hostRef.parentNode) render(hostRef);
  }

  function loadStatus() {
    var api = bridge();
    if (!api) return;
    state.phase = 'loading';
    repaint();
    api.status().then(function (st) {
      state.status = st || null;
      state.loaded = true;
      state.phase = 'idle';
      repaint();
    }, function (err) {
      state.phase = 'error';
      state.error = { reason: 'status_failed', message: str(err && err.message) };
      repaint();
    });
  }

  function saveSource(value) {
    var api = bridge();
    if (!api) return;
    api.config({ source: str(value).trim() }).then(function (cfg) {
      state.status = state.status || {};
      state.status.source = cfg && cfg.source ? cfg.source : null;
      state.check = null;
      state.phase = 'idle';
      state.error = null;
      repaint();
    }, function (err) {
      state.phase = 'error';
      state.error = { reason: 'config_failed', message: str(err && err.message) };
      repaint();
    });
  }

  function doCheck() {
    var api = bridge();
    if (!api) return;
    state.phase = 'checking';
    state.error = null;
    repaint();
    api.check().then(function (res) {
      state.check = res || null;
      state.phase = res && res.ok === false ? 'error' : 'idle';
      if (res && res.ok === false) {
        state.error = { reason: res.reason, message: res.message };
      }
      repaint();
    }, function (err) {
      state.phase = 'error';
      state.error = { reason: 'check_failed', message: str(err && err.message) };
      repaint();
    });
  }

  function doInstall() {
    var api = bridge();
    if (!api) return;
    state.phase = 'installing';
    state.error = null;
    repaint();
    api.install({}).then(function (res) {
      if (res && res.ok) {
        state.result = res;
        state.phase = 'done';
        state.error = null;
      } else {
        state.phase = 'error';
        state.result = null;
        state.error = { reason: (res && res.reason) || 'install_failed',
                        message: (res && res.message) || '' };
      }
      repaint();
    }, function (err) {
      state.phase = 'error';
      state.error = { reason: 'install_failed', message: str(err && err.message) };
      repaint();
    });
  }

  function doRollback() {
    var api = bridge();
    if (!api) return;
    state.phase = 'installing';
    state.error = null;
    repaint();
    api.rollback({}).then(function (res) {
      if (res && res.ok) {
        state.phase = 'done';
        state.result = { version: res.version, requiresRestart: true };
        state.error = null;
      } else {
        state.phase = 'error';
        state.error = { reason: (res && res.reason) || 'rollback_failed',
                        message: (res && res.message) || '' };
      }
      repaint();
    }, function (err) {
      state.phase = 'error';
      state.error = { reason: 'rollback_failed', message: str(err && err.message) };
      repaint();
    });
  }

  /* app.js calls this with the mount point; it paints and then fills in status */
  function mount(host) {
    hostRef = host;
    render(host);
    if (bridge() && !state.loaded) loadStatus();
    return host;
  }

  /* the shell offers one entry point (Help -> Check for updates) */
  if (global.tracelensDesktop && global.tracelensDesktop.update
      && typeof global.tracelensDesktop.update.onOpenRequest === 'function') {
    global.tracelensDesktop.update.onOpenRequest(function () {
      global.dispatchEvent(new global.CustomEvent('tracelens:open-update'));
    });
  }

  Ext.register({ id: 'update', version: VERSION });

  global.TraceLensUpdate = {
    mount: mount,
    render: mount,
    renderProgress: renderProgress,
    state: state,
    version: VERSION
  };
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
