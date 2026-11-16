/*
 * TraceLens - standalone feature workspaces (web/ext/workspaces.js)
 * ------------------------------------------------------------------
 * One visible feature centre, eight addressable pages. Existing panels keep
 * their business logic: plugin pages call their registered render functions,
 * rollback/confidence/evidence reuse TraceLensCards, and compliance delegates
 * to the one export pipeline in app.js. No framework and no second data path.
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext || typeof Ext.register !== 'function' || !Ext.ctx) return;
  var C = Ext.ctx;
  var doc = global.document;
  var VERSION = '1.0';

  var FEATURES = [
    { id: 'rollback', group: 'records', titleKey: 'workspaces.feature.rollback.title',
      descKey: 'workspaces.feature.rollback.desc', permission: 'records.read' },
    { id: 'confidence', group: 'records', titleKey: 'workspaces.feature.confidence.title',
      descKey: 'workspaces.feature.confidence.desc', permission: 'records.read' },
    { id: 'evidence', group: 'records', titleKey: 'workspaces.feature.evidence.title',
      descKey: 'workspaces.feature.evidence.desc', permission: 'records.read' },
    { id: 'groups', group: 'collaboration', titleKey: 'workspaces.feature.groups.title',
      descKey: 'workspaces.feature.groups.desc', permission: 'records.read', pluginId: 'groups' },
    { id: 'collab', group: 'collaboration', titleKey: 'workspaces.feature.collab.title',
      descKey: 'workspaces.feature.collab.desc', permission: 'records.read', pluginId: 'collab' },
    { id: 'analytics', group: 'collaboration', titleKey: 'workspaces.feature.analytics.title',
      descKey: 'workspaces.feature.analytics.desc', permission: 'audit.read', pluginId: 'analytics' },
    { id: 'suggestions', group: 'collaboration', titleKey: 'workspaces.feature.suggestions.title',
      descKey: 'workspaces.feature.suggestions.desc', permission: 'records.read', pluginId: 'suggestions' },
    { id: 'compliance', group: 'output', titleKey: 'workspaces.feature.compliance.title',
      descKey: 'workspaces.feature.compliance.desc', permission: 'audit.export' }
  ];
  var PLUGIN_PAGES = { groups: true, collab: true, analytics: true, suggestions: true };
  var GROUP_KEYS = {
    records: 'workspaces.group.records',
    collaboration: 'workspaces.group.collaboration',
    output: 'workspaces.group.output'
  };

  var state = {
    open: false,
    current: 'overview',
    records: [],
    lang: 'zh',
    signature: '',
    selected: { rollback: '', evidence: '' },
    confidenceFilter: 'all',
    previousHash: '',
    lastHash: '',
    lastFocus: null,
    uploadBusy: false
  };
  var entryWatcher = null;

  function str(value) {
    return value === undefined || value === null ? '' : String(value);
  }

  function isObj(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
  }

  function featureOf(id) {
    var wanted = str(id);
    for (var i = 0; i < FEATURES.length; i += 1) {
      if (FEATURES[i].id === wanted) return FEATURES[i];
    }
    return null;
  }

  function byId(id) {
    return doc && typeof doc.getElementById === 'function' ? doc.getElementById(id) : null;
  }

  function button(parent, key, onClick, options) {
    var opts = options || {};
    var node = C.add(parent, C.el('button', {
      class: 'workspace-button' + (opts.primary ? ' workspace-button--primary' : '') +
             (opts.ghost ? ' workspace-button--ghost' : ''),
      attrs: { type: 'button' },
      dataset: opts.dataset || null
    }));
    C.textNode(node, key, opts.params || null);
    if (opts.disabled) node.disabled = true;
    if (opts.titleKey) node.setAttribute('title', C.t(opts.titleKey));
    if (onClick) node.addEventListener('click', onClick);
    return node;
  }

  function text(parent, key, tag, className, params) {
    var node = C.add(parent, C.el(tag || 'p', { class: className || '' }));
    C.textNode(node, key, params || null);
    return node;
  }

  function apiMode() {
    try {
      var snapshot = C.UI && typeof C.UI.apiMode === 'function' ? C.UI.apiMode() : null;
      return snapshot ? str(snapshot.mode) : '';
    } catch (e) {
      return '';
    }
  }

  function online() {
    return apiMode() === 'live';
  }

  function recordId(record) {
    return str(record && (record.id || record.action_id));
  }

  function recordTitle(record) {
    var human = isObj(record && record.human_readable) ? record.human_readable : {};
    var title = str(human.summary || human.title || record.summary || record.intent ||
                    record.action || record.tool_name || record.tool || recordId(record));
    return title || recordId(record);
  }

  function recordsSignature(records) {
    return (records || []).map(function (record) {
      var evidence = Array.isArray(record.multimedia_evidence) ? record.multimedia_evidence.length : 0;
      return [recordId(record), str(record.rollback_state), str(record.confidence), evidence,
              record.causal_explanation ? '1' : '0'].join(':');
    }).join('|');
  }

  function selectedRecord(featureId) {
    var records = state.records;
    if (!records.length) return null;
    var wanted = state.selected[featureId];
    var found = null;
    records.forEach(function (record) {
      if (recordId(record) === wanted) found = record;
    });
    if (!found) {
      found = records[0];
      state.selected[featureId] = recordId(found);
    }
    return found;
  }

  function routeFromHash() {
    var hash = '';
    try { hash = str(global.location && global.location.hash); } catch (e) { hash = ''; }
    var match = /^#feature=([a-z]+)$/.exec(hash);
    return match && featureOf(match[1]) ? match[1] : (match && match[1] === 'overview' ? 'overview' : '');
  }

  function rawHash() {
    try { return str(global.location && global.location.hash).replace(/^#/, ''); }
    catch (e) { return ''; }
  }

  function writeHash(route) {
    var next = route ? 'feature=' + route : '';
    state.lastHash = next;
    try { global.location.hash = next; } catch (e) { /* file shells may expose a read-only hash */ }
  }

  function restoreHash(value) {
    var next = str(value).replace(/^#/, '');
    if (/^feature=/.test(next)) next = '';
    state.lastHash = next;
    try { global.location.hash = next; } catch (e) { /* file shells may expose a read-only hash */ }
  }

  function ensureEntry() {
    var bar = byId('topbar');
    if (!bar) return null;
    var entry = null;
    try { entry = bar.querySelector('[data-hook="feature-center-entry"]'); } catch (e) { entry = null; }
    if (!entry) {
      entry = C.el('button', {
        class: 'workspace-entry', id: 'feature-center-entry',
        attrs: {
          type: 'button', 'aria-haspopup': 'dialog', 'aria-controls': 'feature-center'
        },
        dataset: { hook: 'feature-center-entry' }
      });
      var mark = C.add(entry, C.el('span', {
        class: 'workspace-entry__mark', text: '08', attrs: { 'aria-hidden': 'true' }
      }));
      void mark;
      var label = C.add(entry, C.el('span', { class: 'workspace-entry__label' }));
      C.textNode(label, 'workspaces.launcher');
      entry.setAttribute('aria-label', C.t('workspaces.launcher.aria'));
      entry.addEventListener('click', function () { open('overview'); });
      C.add(bar, entry);
    }
    entry.setAttribute('aria-expanded', state.open ? 'true' : 'false');
    var launcher = byId('trust-launcher');
    if (launcher) {
      launcher.hidden = true;
      launcher.setAttribute('data-workspace-replaced', 'true');
    }
    return entry;
  }

  /* app.js calls renderTopbar() directly from a dozen places (language switch,
   * settings apply, demo toggle, auth, progress) and every one of them starts
   * with clear(#topbar). Those calls never go through the render notify, so an
   * entry that only lives in onState is wiped and never comes back - the page
   * then looks exactly like the version before this feature existed. Watch the
   * bar instead of guessing when it is rebuilt. */
  function watchEntry() {
    var bar = byId('topbar');
    if (!bar || entryWatcher || typeof global.MutationObserver !== 'function') return;
    entryWatcher = new global.MutationObserver(function () {
      if (!byId('feature-center-entry')) ensureEntry();
    });
    try { entryWatcher.observe(bar, { childList: true }); } catch (e) { entryWatcher = null; }
  }

  function ensureShell() {
    var host = byId('feature-center');
    if (host) return host;
    host = C.el('section', {
      class: 'workspace', id: 'feature-center',
      attrs: {
        role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'workspace-title',
        'aria-label': C.t('workspaces.title')
      }
    });
    host.hidden = true;

    var frame = C.add(host, C.el('div', { class: 'workspace__frame' }));
    var sidebar = C.add(frame, C.el('aside', { class: 'workspace__sidebar' }));
    var brand = C.add(sidebar, C.el('button', {
      class: 'workspace__brand', attrs: { type: 'button' },
      dataset: { hook: 'workspace-overview' }
    }));
    C.add(brand, C.el('span', { class: 'workspace__brand-mark', text: 'TL', attrs: { 'aria-hidden': 'true' } }));
    var brandCopy = C.add(brand, C.el('span', { class: 'workspace__brand-copy' }));
    text(brandCopy, 'workspaces.title', 'strong', 'workspace__brand-title');
    text(brandCopy, 'workspaces.overview.short', 'span', 'workspace__brand-subtitle');
    brand.addEventListener('click', function () { show('overview'); });

    var nav = C.add(sidebar, C.el('nav', {
      class: 'workspace__nav', id: 'workspace-nav',
      attrs: { 'aria-label': C.t('workspaces.nav.aria') }
    }));
    nav.setAttribute('data-hook', 'workspace-nav');
    text(sidebar, 'workspaces.overview.hint', 'p', 'workspace__sidebar-hint');

    var main = C.add(frame, C.el('main', { class: 'workspace__main' }));
    var head = C.add(main, C.el('header', { class: 'workspace__header' }));
    var heading = C.add(head, C.el('div', { class: 'workspace__heading' }));
    var back = C.add(heading, C.el('button', {
      class: 'workspace__back', id: 'workspace-back', attrs: { type: 'button' },
      dataset: { hook: 'workspace-back' }
    }));
    C.textNode(back, 'workspaces.back');
    back.addEventListener('click', function () { show('overview'); });
    var title = C.add(heading, C.el('h1', {
      class: 'workspace__title', id: 'workspace-title', attrs: { tabindex: '-1' }
    }));
    title.setAttribute('data-hook', 'workspace-title');
    var subtitle = C.add(heading, C.el('p', {
      class: 'workspace__subtitle', id: 'workspace-subtitle'
    }));
    subtitle.setAttribute('data-hook', 'workspace-subtitle');
    /* folio: the running page number of the volume, set in the head margin.
     * Purely decorative - the title already says where you are. */
    var folio = C.add(head, C.el('p', {
      class: 'workspace__folio', id: 'workspace-folio',
      attrs: { 'aria-hidden': 'true' }
    }));
    folio.textContent = '00 / 08';
    var close = C.add(head, C.el('button', {
      class: 'workspace__close', id: 'workspace-close', attrs: { type: 'button' },
      dataset: { hook: 'workspace-close' }
    }));
    C.textNode(close, 'workspaces.close');
    close.addEventListener('click', function () { closeWorkspace(); });

    var content = C.add(main, C.el('div', {
      class: 'workspace__content', id: 'workspace-content',
      attrs: { role: 'region', 'aria-live': 'polite' },
      dataset: { hook: 'workspace-content', 'feature-page': 'overview' }
    }));
    C.add(doc.body, host);
    return host;
  }

  function renderNav() {
    var nav = byId('workspace-nav');
    if (!nav) return;
    C.clear(nav);
    var groups = ['records', 'collaboration', 'output'];
    groups.forEach(function (group) {
      var section = C.add(nav, C.el('section', {
        class: 'workspace__nav-group', dataset: { group: group }
      }));
      text(section, GROUP_KEYS[group], 'h2', 'workspace__nav-label');
      FEATURES.filter(function (feature) { return feature.group === group; })
        .forEach(function (feature) {
          var active = state.current === feature.id;
          var item = C.add(section, C.el('button', {
            class: 'workspace__nav-item' + (active ? ' is-active' : ''),
            attrs: { type: 'button', 'aria-current': active ? 'page' : null },
            dataset: { 'feature-nav': feature.id }
          }));
          C.add(item, C.el('span', {
            class: 'workspace__nav-number', text: ('0' + (FEATURES.indexOf(feature) + 1)).slice(-2),
            attrs: { 'aria-hidden': 'true' }
          }));
          text(item, feature.titleKey, 'span', 'workspace__nav-title');
          item.addEventListener('click', function () { show(feature.id); });
        });
    });
  }

  function renderHeader() {
    var feature = featureOf(state.current);
    var title = byId('workspace-title');
    var subtitle = byId('workspace-subtitle');
    var back = byId('workspace-back');
    var folio = byId('workspace-folio');
    C.clear(title);
    C.clear(subtitle);
    if (feature) {
      C.textNode(title, feature.titleKey);
      C.textNode(subtitle, feature.descKey);
      back.hidden = false;
      back.setAttribute('aria-label', C.t('workspaces.back'));
    } else {
      C.textNode(title, 'workspaces.overview.title');
      C.textNode(subtitle, 'workspaces.overview.subtitle');
      back.hidden = true;
    }
    if (folio) {
      folio.textContent = feature
        ? ('0' + (FEATURES.indexOf(feature) + 1)).slice(-2) + ' / 0' + FEATURES.length
        : '00 / 0' + FEATURES.length;
    }
  }

  function pageHost(id, className) {
    var content = byId('workspace-content');
    C.clear(content);
    content.setAttribute('data-feature-page', id);
    var host = C.add(content, C.el('section', {
      class: 'workspace-page' + (className ? ' ' + className : ''),
      id: 'feature-page-' + id,
      dataset: { 'feature-page': id }
    }));
    return host;
  }

  function renderHome() {
    var home = pageHost('overview', 'workspace-home');
    var intro = C.add(home, C.el('section', { class: 'workspace-home__intro' }));
    text(intro, 'workspaces.overview.kicker', 'p', 'workspace__eyebrow');
    text(intro, 'workspaces.overview.lead', 'h2', 'workspace-home__lead');
    text(intro, 'workspaces.overview.note', 'p', 'workspace-home__note');

    var grid = C.add(home, C.el('div', {
      class: 'workspace-home__grid', dataset: { hook: 'workspace-feature-grid' }
    }));
    FEATURES.forEach(function (feature, index) {
      var card = C.add(grid, C.el('button', {
        class: 'workspace-feature-card',
        attrs: { type: 'button' },
        dataset: { 'feature-card': feature.id, group: feature.group }
      }));
      var top = C.add(card, C.el('span', { class: 'workspace-feature-card__top' }));
      C.add(top, C.el('span', {
        class: 'workspace-feature-card__number', text: ('0' + (index + 1)).slice(-2),
        attrs: { 'aria-hidden': 'true' }
      }));
      text(top, GROUP_KEYS[feature.group], 'span', 'workspace-feature-card__group');
      text(card, feature.titleKey, 'strong', 'workspace-feature-card__title');
      text(card, feature.descKey, 'span', 'workspace-feature-card__desc');
      var action = C.add(card, C.el('span', { class: 'workspace-feature-card__action' }));
      C.textNode(action, 'workspaces.action.open');
      C.add(action, C.el('span', { text: '\u2192', attrs: { 'aria-hidden': 'true' } }));
      card.addEventListener('click', function () { show(feature.id); });
    });

    var identity = C.add(home, C.el('section', { class: 'workspace-identity' }));
    var copy = C.add(identity, C.el('div'));
    text(copy, 'workspaces.identity.title', 'h2', 'workspace-identity__title');
    text(copy, 'workspaces.identity.desc', 'p', 'workspace-identity__desc');
    button(identity, 'workspaces.identity.action', function () {
      closeWorkspace();
      Ext.open('secretariat');
    }, { ghost: true });
  }

  function openOriginalRecord(id) {
    var wanted = str(id);
    closeWorkspace();
    var card = byId('card-' + wanted);
    if (!card) {
      C.toast('workspaces.record.missing', null, 'warn');
      return false;
    }
    try {
      if (typeof card.scrollIntoView === 'function') card.scrollIntoView({ block: 'nearest' });
      if (typeof card.focus === 'function') card.focus();
    } catch (e) { /* the original list is already visible */ }
    return true;
  }

  function recordPicker(parent, featureId) {
    var record = selectedRecord(featureId);
    if (!record) return null;
    var wrap = C.add(parent, C.el('label', { class: 'workspace-field workspace-record-picker' }));
    text(wrap, 'workspaces.record.select', 'span', 'workspace-field__label');
    var select = C.add(wrap, C.el('select', {
      class: 'workspace-input', dataset: { hook: featureId + '-record-picker' }
    }));
    state.records.forEach(function (item) {
      var id = recordId(item);
      var option = C.add(select, C.el('option', {
        text: recordTitle(item) + ' · ' + id,
        attrs: { value: id, selected: id === recordId(record) ? 'selected' : null }
      }));
      option.value = id;
    });
    select.value = recordId(record);
    select.addEventListener('change', function () {
      state.selected[featureId] = str(select.value);
      renderCurrent();
    });
    return record;
  }

  function renderRollback(host) {
    var layout = C.add(host, C.el('div', { class: 'workspace-record-layout' }));
    var controls = C.add(layout, C.el('section', { class: 'workspace-panel workspace-record-controls' }));
    text(controls, 'workspaces.rollback.intro', 'p', 'workspace-panel__intro');
    var record = recordPicker(controls, 'rollback');
    if (!record) {
      C.renderEmpty(controls, 'workspaces.state.empty_records');
      return;
    }
    var current = C.add(controls, C.el('div', { class: 'workspace-record-summary' }));
    C.add(current, C.el('strong', { text: recordTitle(record) }));
    C.add(current, C.el('code', { text: recordId(record) }));
    if (str(record.rollback_state) === 'rolled_back') {
      text(current, 'cards.rollback.rolled', 'span', 'workspace-status workspace-status--muted');
    }
    var errorHost = C.add(controls, C.el('div', { class: 'workspace-inline-state' }));
    var allowed = C.can('intervene.decide');
    var plan = button(controls, 'workspaces.rollback.plan', function () {
      var cards = global.TraceLensCards;
      if (cards && typeof cards.openRollbackPlan === 'function') {
        cards.openRollbackPlan(record, plan, errorHost);
      }
    }, { primary: true, disabled: !online() || !allowed });
    plan.setAttribute('data-hook', 'workspace-rollback-plan');
    text(controls, 'workspaces.rollback.plan.hint', 'p', 'workspace-field__hint');
    if (!online()) text(controls, 'workspaces.state.offline', 'p', 'workspace-inline-note');
    else if (!allowed) text(controls, 'workspaces.state.denied', 'p', 'workspace-inline-note');
    button(controls, 'workspaces.action.open_record', function () {
      openOriginalRecord(recordId(record));
    }, { ghost: true });

    var listPanel = C.add(layout, C.el('section', { class: 'workspace-panel' }));
    text(listPanel, 'workspaces.rollback.list', 'h2', 'workspace-panel__title');
    var list = C.add(listPanel, C.el('div', { class: 'workspace-record-list' }));
    state.records.forEach(function (item) {
      var id = recordId(item);
      var row = C.add(list, C.el('button', {
        class: 'workspace-record-row' + (id === recordId(record) ? ' is-active' : ''),
        attrs: { type: 'button' }, dataset: { 'record-id': id }
      }));
      C.add(row, C.el('span', { class: 'workspace-record-row__title', text: recordTitle(item) }));
      C.add(row, C.el('code', { class: 'workspace-record-row__id', text: id }));
      if (str(item.rollback_state) === 'rolled_back') {
        text(row, 'cards.rollback.rolled', 'span', 'workspace-record-row__state');
      }
      row.addEventListener('click', function () {
        state.selected.rollback = id;
        renderCurrent();
      });
    });
  }

  function confidenceModel(record) {
    var cards = global.TraceLensCards;
    if (cards && typeof cards.confidence === 'function') return cards.confidence(record);
    return { value: null, shown: '', band: 'neutral', bandText: '', methodText: '', samples: null, tip: '' };
  }

  function renderConfidence(host) {
    var models = state.records.map(function (record) {
      return { record: record, model: confidenceModel(record) };
    });
    var known = models.filter(function (item) { return item.model.value !== null; });
    var low = known.filter(function (item) { return item.model.band === 'red'; });
    var missing = models.length - known.length;
    var stats = C.add(host, C.el('div', { class: 'workspace-stats' }));
    [
      ['workspaces.confidence.stats.total', models.length],
      ['workspaces.confidence.stats.known', known.length],
      ['workspaces.confidence.stats.low', low.length],
      ['workspaces.confidence.stats.missing', missing]
    ].forEach(function (entry) {
      var card = C.add(stats, C.el('section', { class: 'workspace-stat' }));
      text(card, entry[0], 'span', 'workspace-stat__label');
      C.add(card, C.el('strong', { class: 'workspace-stat__value', text: entry[1] }));
    });

    var toolbar = C.add(host, C.el('section', { class: 'workspace-panel workspace-toolbar' }));
    text(toolbar, 'workspaces.confidence.intro', 'p', 'workspace-panel__intro');
    var filterWrap = C.add(toolbar, C.el('label', { class: 'workspace-field' }));
    text(filterWrap, 'workspaces.confidence.filter.label', 'span', 'workspace-field__label');
    var filter = C.add(filterWrap, C.el('select', {
      class: 'workspace-input', dataset: { hook: 'confidence-filter' }
    }));
    [
      ['all', 'workspaces.confidence.filter.all'],
      ['low', 'workspaces.confidence.filter.low'],
      ['missing', 'workspaces.confidence.filter.missing']
    ].forEach(function (entry) {
      var option = C.add(filter, C.el('option', {
        attrs: { value: entry[0], selected: state.confidenceFilter === entry[0] ? 'selected' : null }
      }));
      C.textNode(option, entry[1]);
      option.value = entry[0];
    });
    filter.value = state.confidenceFilter;
    filter.addEventListener('change', function () {
      state.confidenceFilter = str(filter.value) || 'all';
      renderCurrent();
    });

    var visible = models.filter(function (item) {
      if (state.confidenceFilter === 'low') return item.model.band === 'red';
      if (state.confidenceFilter === 'missing') return item.model.value === null;
      return true;
    });
    var listPanel = C.add(host, C.el('section', { class: 'workspace-panel' }));
    var list = C.add(listPanel, C.el('div', {
      class: 'workspace-confidence-list', dataset: { hook: 'confidence-list', count: visible.length }
    }));
    if (!visible.length) {
      C.renderEmpty(list, 'workspaces.confidence.empty_filter');
      return;
    }
    visible.forEach(function (item) {
      var model = item.model;
      var record = item.record;
      var row = C.add(list, C.el('article', {
        class: 'workspace-confidence-row',
        dataset: { band: model.band, 'record-id': recordId(record) }
      }));
      var badge = C.add(row, C.el('span', {
        class: 'workspace-confidence-badge',
        text: model.value === null ? '\u2014' : C.fmtPercent(model.value, 0),
        attrs: { title: model.tip || null },
        dataset: { band: model.band, confidence: model.shown }
      }));
      void badge;
      var copy = C.add(row, C.el('div', { class: 'workspace-confidence-copy' }));
      C.add(copy, C.el('strong', { text: recordTitle(record) }));
      C.add(copy, C.el('code', { text: recordId(record) }));
      var meta = C.add(copy, C.el('p', { class: 'workspace-confidence-meta' }));
      C.add(meta, C.el('span', { text: model.bandText }));
      if (model.methodText) C.add(meta, C.el('span', { text: model.methodText }));
      if (model.samples !== null && model.samples !== undefined) {
        C.add(meta, C.el('span', { text: C.t('workspaces.confidence.samples', { count: model.samples }) }));
      }
      button(row, 'workspaces.action.open_record', function () {
        openOriginalRecord(recordId(record));
      }, { ghost: true });
    });
  }

  function refreshRecords() {
    var pending = null;
    try {
      if (C.UI && typeof C.UI.refreshRecords === 'function') {
        pending = C.UI.refreshRecords({ reason: 'workspaces' });
      }
    } catch (e) { pending = null; }
    return Promise.resolve(pending).then(function () {
      if (state.open) renderCurrent();
      return true;
    }, function () {
      if (state.open) renderCurrent();
      return false;
    });
  }

  function renderEvidence(host) {
    var layout = C.add(host, C.el('div', { class: 'workspace-evidence-layout' }));
    var main = C.add(layout, C.el('section', { class: 'workspace-panel workspace-evidence-main' }));
    text(main, 'workspaces.evidence.intro', 'p', 'workspace-panel__intro');
    var record = recordPicker(main, 'evidence');
    if (!record) {
      C.renderEmpty(main, 'workspaces.state.empty_records');
      return;
    }
    var actions = C.add(main, C.el('div', { class: 'workspace-actions' }));
    button(actions, 'workspaces.action.refresh', function () { refreshRecords(); }, { ghost: true });
    button(actions, 'workspaces.action.open_record', function () {
      openOriginalRecord(recordId(record));
    }, { ghost: true });
    text(main, 'workspaces.evidence.existing', 'h2', 'workspace-panel__title');
    var evidenceHost = C.add(main, C.el('div', {
      class: 'workspace-evidence-existing', dataset: { hook: 'workspace-evidence-existing' }
    }));
    var cards = global.TraceLensCards;
    if (cards && typeof cards.renderEvidenceAndCausal === 'function') {
      cards.renderEvidenceAndCausal(evidenceHost, record);
    } else {
      C.renderError(evidenceHost, { key: 'workspaces.state.unavailable' });
    }

    var upload = C.add(layout, C.el('section', { class: 'workspace-panel workspace-upload' }));
    text(upload, 'workspaces.evidence.upload.title', 'h2', 'workspace-panel__title');
    text(upload, 'workspaces.evidence.upload.hint', 'p', 'workspace-panel__intro');
    var form = C.add(upload, C.el('form', {
      class: 'workspace-form', dataset: { hook: 'evidence-upload-form' }
    }));
    var captionWrap = C.add(form, C.el('label', { class: 'workspace-field' }));
    text(captionWrap, 'workspaces.evidence.upload.caption', 'span', 'workspace-field__label');
    var caption = C.add(captionWrap, C.el('input', {
      class: 'workspace-input', attrs: { type: 'text', name: 'caption', maxlength: '200' }
    }));
    var beforeWrap = C.add(form, C.el('label', { class: 'workspace-field' }));
    text(beforeWrap, 'workspaces.evidence.upload.before', 'span', 'workspace-field__label');
    var before = C.add(beforeWrap, C.el('textarea', {
      class: 'workspace-input workspace-textarea', attrs: { name: 'before', rows: '5' }
    }));
    var afterWrap = C.add(form, C.el('label', { class: 'workspace-field' }));
    text(afterWrap, 'workspaces.evidence.upload.after', 'span', 'workspace-field__label');
    var after = C.add(afterWrap, C.el('textarea', {
      class: 'workspace-input workspace-textarea', attrs: { name: 'after', rows: '5' }
    }));
    var feedback = C.add(form, C.el('div', { class: 'workspace-inline-state' }));
    var allowed = C.can('data.write');
    var submit = button(form, 'workspaces.evidence.upload.submit', null, {
      primary: true, disabled: !online() || !allowed || state.uploadBusy
    });
    submit.setAttribute('data-hook', 'evidence-upload-submit');
    submit.setAttribute('type', 'submit');
    if (!online()) text(form, 'workspaces.state.offline', 'p', 'workspace-inline-note');
    else if (!allowed) text(form, 'workspaces.state.permission_write', 'p', 'workspace-inline-note');
    form.addEventListener('submit', function (event) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      if (submit.disabled || state.uploadBusy) return;
      state.uploadBusy = true;
      submit.disabled = true;
      C.clear(submit);
      C.textNode(submit, 'workspaces.evidence.upload.busy');
      C.clear(feedback);
      C.post('api/records/' + encodeURIComponent(recordId(record)) + '/evidence', {
        kind: 'diff',
        caption: str(caption.value),
        payload: { before: str(before.value), after: str(after.value) }
      }).then(function (result) {
        state.uploadBusy = false;
        if (!result || !result.ok) {
          submit.disabled = false;
          C.clear(submit);
          C.textNode(submit, 'workspaces.evidence.upload.submit');
          C.renderError(feedback, C.panelError(result, null));
          return;
        }
        C.toast('workspaces.evidence.uploaded', null, 'ok');
        refreshRecords();
      }, function () {
        state.uploadBusy = false;
        submit.disabled = false;
        C.clear(submit);
        C.textNode(submit, 'workspaces.evidence.upload.submit');
        C.renderError(feedback, { key: 'workspaces.state.error' });
      });
    });
  }

  function pluginById(id) {
    var plugins = Ext.plugins();
    for (var i = 0; i < plugins.length; i += 1) {
      if (plugins[i].id === id) return plugins[i];
    }
    return null;
  }

  function renderPlugin(host, feature) {
    var plugin = pluginById(feature.pluginId);
    if (!plugin || !plugin.tab || typeof plugin.tab.render !== 'function') {
      C.renderError(host, { key: 'workspaces.state.unavailable' });
      return;
    }
    if (plugin.tab.permission && !C.can(plugin.tab.permission)) {
      C.renderError(host, { key: 'workspaces.state.denied' });
      return;
    }
    var mount = C.add(host, C.el('div', {
      class: 'workspace-plugin', dataset: { plugin: plugin.id }
    }));
    try {
      plugin.tab.render(mount, C);
    } catch (err) {
      C.renderError(mount, {
        key: 'workspaces.state.error', message: err && err.message ? err.message : ''
      });
    }
  }

  /* REQ-008 keeps exactly one export panel. This page mounts the very same
   * renderer the audit rail uses (`TraceLensUI.renderExportPanel`) instead of
   * drawing a second set of buttons: one request path, one set of printed
   * values, no chance of two views disagreeing about the same export. */
  function renderCompliance(host) {
    var panel = C.add(host, C.el('section', { class: 'workspace-panel workspace-export' }));
    text(panel, 'workspaces.compliance.intro', 'p', 'workspace-panel__intro');
    text(panel, 'workspaces.compliance.shared', 'p', 'workspace-inline-note');
    var mount = C.add(panel, C.el('div', {
      class: 'workspace-export__mount', dataset: { hook: 'workspace-export-panel' }
    }));
    if (C.UI && typeof C.UI.renderExportPanel === 'function') {
      C.UI.renderExportPanel(mount);
    } else {
      C.renderError(mount, { key: 'workspaces.state.unavailable' });
    }
  }

  function renderCurrent() {
    if (!state.open) return;
    ensureShell();
    ensureEntry();
    renderNav();
    renderHeader();
    var feature = featureOf(state.current);
    if (!feature) {
      renderHome();
      return;
    }
    var host = pageHost(feature.id, 'workspace-page--' + feature.id);
    host.setAttribute('aria-label', C.t(feature.titleKey));
    if (feature.id === 'rollback') renderRollback(host);
    else if (feature.id === 'confidence') renderConfidence(host);
    else if (feature.id === 'evidence') renderEvidence(host);
    else if (feature.id === 'compliance') renderCompliance(host);
    else if (PLUGIN_PAGES[feature.id]) renderPlugin(host, feature);
  }

  function show(id) {
    var next = featureOf(id) ? str(id) : 'overview';
    state.current = next;
    writeHash(next);
    renderCurrent();
    var title = byId('workspace-title');
    try { if (title && typeof title.focus === 'function') title.focus(); } catch (e) { /* optional */ }
    return true;
  }

  function open(id) {
    var host = ensureShell();
    ensureEntry();
    if (!state.open) {
      state.previousHash = rawHash();
      state.lastFocus = doc && doc.activeElement ? doc.activeElement : null;
    }
    if (Ext.state && Ext.state().open) Ext.close();
    host.hidden = false;
    state.open = true;
    try { doc.body.setAttribute('data-workspace', 'open'); } catch (e) { /* no body */ }
    var requested = featureOf(id) ? str(id) : (id === 'overview' ? 'overview' : (routeFromHash() || 'overview'));
    show(requested);
    ensureEntry();
    return true;
  }

  function closeWorkspace() {
    var host = byId('feature-center');
    if (host) host.hidden = true;
    state.open = false;
    state.uploadBusy = false;
    try { doc.body.removeAttribute('data-workspace'); } catch (e) { /* no body */ }
    var content = byId('workspace-content');
    if (content) C.clear(content);
    writeHash(state.previousHash || '');
    ensureEntry();
    try {
      if (state.lastFocus && typeof state.lastFocus.focus === 'function') state.lastFocus.focus();
    } catch (e) { /* focus restoration is best effort */ }
    return true;
  }

  function onState(snapshot) {
    var records = snapshot && Array.isArray(snapshot.records) ? snapshot.records : [];
    var lang = snapshot && snapshot.lang ? str(snapshot.lang) : C.lang();
    var signature = recordsSignature(records);
    var recordsChanged = signature !== state.signature;
    var langChanged = lang !== state.lang;
    state.records = records.slice();
    state.signature = signature;
    state.lang = lang;
    ensureEntry();
    if (!state.open) return;
    if (langChanged) {
      renderCurrent();
      return;
    }
    var feature = featureOf(state.current);
    if (!feature) return;
    if (feature.id === 'compliance') {
      renderCurrent();
      return;
    }
    if (recordsChanged && !PLUGIN_PAGES[feature.id]) {
      renderCurrent();
      return;
    }
    if ((recordsChanged || langChanged) && PLUGIN_PAGES[feature.id]) {
      var plugin = pluginById(feature.pluginId);
      var mount = byId('feature-page-' + feature.id);
      if (plugin && plugin.tab && typeof plugin.tab.repaint === 'function' && mount) {
        try { plugin.tab.repaint(mount, C); } catch (e) { /* the next explicit navigation repaints */ }
      }
    }
  }

  function boot() {
    ensureShell();
    ensureEntry();
    watchEntry();
    global.addEventListener('hashchange', function () {
      var current = rawHash();
      if (current === state.lastHash) return;
      state.lastHash = current;
      var route = routeFromHash();
      if (route) open(route);
      else if (state.open) closeWorkspace();
    });
    /* Capture phase on purpose: a dialog opened from a workspace page must be
     * closed by its own handler first. Reading the modal state in the bubble
     * phase would see it already hidden and close the whole workspace too. */
    doc.addEventListener('keydown', function (event) {
      if (!state.open || !event || event.key !== 'Escape') return;
      if (event.defaultPrevented) return;
      var modal = byId('trust-modal');
      if (modal && !modal.hidden) return;
      closeWorkspace();
    }, true);
    var route = routeFromHash();
    if (route) open(route);
  }

  Ext.register({
    id: 'workspaces',
    version: VERSION,
    boot: boot,
    onState: onState
  });

  global.TraceLensWorkspaces = {
    version: VERSION,
    features: FEATURES.map(function (feature) { return feature.id; }),
    open: open,
    close: closeWorkspace,
    show: show,
    state: function () {
      return {
        open: state.open,
        current: state.current,
        records: state.records.length,
        lang: state.lang,
        confidenceFilter: state.confidenceFilter,
        selected: { rollback: state.selected.rollback, evidence: state.selected.evidence },
        watching: entryWatcher !== null
      };
    }
  };
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
