/*
 * TraceLens 5.0 - insights surface (web/ext/insights.js)
 * ------------------------------------------------------------------
 * Two tabs, one file (docs/07 §6.3):
 *   - analytics    (order 40, audit.read)  : behaviour dashboard
 *   - suggestions  (order 50, records.read): automation suggestions + preferences
 *
 * Discipline carried over from web/ext/core.js:
 *   - no framework, no CDN, no build step; classic script, works from file://;
 *   - every user-visible string is a dictionary key: `ctx.t(key)` fills it and
 *     `ctx.stamp(node, key)` records it, so the i18n lint can prove nothing is
 *     hard-coded here;
 *   - every number shown comes from the server. The page filters, formats and
 *     lays out; it never recomputes a metric it was given. When the server did
 *     not send a value the cell shows an em dash, never a guess;
 *   - no innerHTML: server text is written through textContent / setAttribute,
 *     which the DOM escapes; ctx.esc is therefore not re-applied to already
 *     escaped output. Nothing built here is ever parsed as markup;
 *   - all requests go through ctx.get / ctx.post / ctx.download, and every
 *     failure ends in ctx.renderError(host, ctx.panelError(res, retry)). An
 *     offline page says it needs the server instead of inventing data.
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext || typeof Ext.register !== 'function') return;

  var UI = global.TraceLensUI;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var DAY_MS = 86400000;
  var POLL_MS = 60000;
  var SUG_ENABLED_KEY = 'tracelens.ext.suggestions.enabled';

  /* ---------------------------------------------------------------- *
   * 1. small helpers (no copy, no DOM text)
   * ---------------------------------------------------------------- */

  function isObj(value) {
    return !!value && typeof value === 'object' && Object.prototype.toString.call(value) !== '[object Array]';
  }

  function isArr(value) {
    return Object.prototype.toString.call(value) === '[object Array]';
  }

  function str(value) {
    return value === undefined || value === null ? '' : String(value);
  }

  function numOf(value) {
    if (typeof value === 'number') return isFinite(value) ? value : null;
    if (typeof value === 'string' && value.replace(/\s+/g, '') !== '') {
      var parsed = Number(value);
      return isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function clamp01(value) {
    var number = numOf(value);
    if (number === null) return 0;
    if (number < 0) return 0;
    if (number > 1) return 1;
    return number;
  }

  function getPath(obj, path) {
    var parts = String(path).split('.');
    var node = obj;
    for (var i = 0; i < parts.length; i += 1) {
      if (!node || typeof node !== 'object') return undefined;
      if (!Object.prototype.hasOwnProperty.call(node, parts[i])) return undefined;
      node = node[parts[i]];
    }
    return node;
  }

  /* First path that carries a real value; 0 and false are real values. */
  function pick(obj, paths) {
    if (!obj) return undefined;
    for (var i = 0; i < paths.length; i += 1) {
      var value = getPath(obj, paths[i]);
      if (value !== undefined && value !== null && value !== '') return value;
    }
    return undefined;
  }

  function pickNum(obj, paths) {
    return numOf(pick(obj, paths));
  }

  function pickList(obj, paths) {
    var value = pick(obj, paths);
    return isArr(value) ? value : [];
  }

  function unwrapRecord(payload, keys) {
    var node = payload;
    if (isObj(node) && isArr(keys)) {
      for (var i = 0; i < keys.length; i += 1) {
        if (isObj(node[keys[i]])) { node = node[keys[i]]; break; }
      }
    }
    return isObj(node) ? node : {};
  }

  function unwrapList(payload, keys) {
    if (isArr(payload)) return payload;
    if (isObj(payload) && isArr(keys)) {
      for (var i = 0; i < keys.length; i += 1) {
        if (isArr(payload[keys[i]])) return payload[keys[i]];
      }
    }
    return [];
  }

  function enc(value) {
    try { return encodeURIComponent(str(value)); } catch (e) { return ''; }
  }

  function pad2(value) {
    return value < 10 ? '0' + value : String(value);
  }

  function dateOffset(days) {
    var date = new Date();
    date.setDate(date.getDate() + days);
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function displayValue(value) {
    if (value === undefined || value === null) return '\u2014';
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (e) { return String(value); }
    }
    return String(value);
  }

  function apiModeName() {
    try {
      if (UI && typeof UI.apiMode === 'function') {
        var snap = UI.apiMode();
        return snap && typeof snap.mode === 'string' ? snap.mode : '';
      }
    } catch (e) { /* probe not ready */ }
    return '';
  }

  function offlineNow() {
    var mode = apiModeName();
    return mode === 'offline' || mode === 'forced_offline';
  }

  function liveNow() {
    return apiModeName() === 'live';
  }

  function offlineState(ctx, host, retry) {
    if (!host) return null;
    return ctx.renderError(host, { key: 'trust.state.offline', retry: retry });
  }

  function copyValue(ctx, value) {
    var text = str(value);
    if (!text) return false;

    function done(ok) {
      ctx.toast(ok ? 'trust.copied' : 'trust.copy.failed', null, ok ? 'info' : 'warn');
    }

    function fallback() {
      try {
        var area = ctx.el('textarea', { class: 'ext-copy-area' });
        area.value = text;
        area.setAttribute('readonly', 'true');
        global.document.body.appendChild(area);
        area.select();
        var ok = false;
        if (typeof global.document.execCommand === 'function') {
          ok = !!global.document.execCommand('copy');
        }
        global.document.body.removeChild(area);
        done(ok);
      } catch (err) {
        done(false);
      }
    }

    try {
      var clip = global.navigator && global.navigator.clipboard;
      if (clip && typeof clip.writeText === 'function') {
        clip.writeText(text).then(function () { done(true); }, function () { fallback(); });
        return true;
      }
    } catch (e) { /* fall through to the offline copy path */ }
    fallback();
    return true;
  }

  function durText(ctx, seconds) {
    var value = numOf(seconds);
    if (value === null || value < 0) return ctx.t('analytics.duration.missing');
    if (value < 60) return ctx.t('analytics.duration.seconds', { value: Number(value.toFixed(1)) });
    if (value < 3600) return ctx.t('analytics.duration.minutes', { value: Number((value / 60).toFixed(1)) });
    return ctx.t('analytics.duration.hours', { value: Number((value / 3600).toFixed(1)) });
  }

  function countText(ctx, value) {
    var number = numOf(value);
    return number === null ? ctx.t('analytics.metric.missing') : ctx.fmtNumber(number, 0);
  }

  function percentText(ctx, value) {
    var number = numOf(value);
    return number === null ? ctx.t('analytics.metric.missing') : ctx.fmtPercent(number, 1);
  }

  function hintText(ctx, parent, key) {
    var node = ctx.add(parent, ctx.el('span', { class: 'ext-metric__hint', text: ctx.t(key) }));
    return ctx.stamp(node, key);
  }

  function sectionTitle(ctx, parent, key, tag) {
    return ctx.textNode(parent, key, null, tag || 'h4');
  }

  function fieldRow(ctx, parent, labelKey, control) {
    var row = ctx.add(parent, ctx.el('label', { class: 'ext-field' }));
    var label = ctx.add(row, ctx.el('span', { class: 'ext-field__label', text: ctx.t(labelKey) }));
    ctx.stamp(label, labelKey);
    ctx.add(row, control);
    return row;
  }

  function textInput(ctx, kind, value, extraClass) {
    return ctx.el('input', {
      class: 'ext-input' + (extraClass ? ' ' + extraClass : ''),
      attrs: { type: kind, value: str(value) }
    });
  }

  function selectInput(ctx, options) {
    var select = ctx.el('select', { class: 'ext-input' });
    (options || []).forEach(function (option) {
      var node = ctx.add(select, ctx.el('option', { attrs: { value: option.value } }));
      node.textContent = ctx.t(option.key);
      ctx.stamp(node, option.key);
    });
    return select;
  }

  function selectValue(select) {
    return select && select.value ? String(select.value) : '';
  }

  /* ---------------------------------------------------------------- *
   * 2. analytics tab state
   * ---------------------------------------------------------------- */

  var ana = {
    from: '',
    to: '',
    agent: '',
    granularity: 'day',
    data: null,
    loading: false,
    seq: 0,
    body: null,
    paintedLang: ''
  };

  function anaDefaults() {
    if (!ana.from) ana.from = dateOffset(-6);
    if (!ana.to) ana.to = dateOffset(0);
  }

  function anaQuery() {
    var query = { from: ana.from, to: ana.to, granularity: ana.granularity };
    if (ana.agent) query.agent = ana.agent;
    return query;
  }

  function anaQueryString() {
    var query = anaQuery();
    var order = ['from', 'to', 'agent', 'granularity'];
    var parts = [];
    order.forEach(function (key) {
      if (query[key] !== undefined && query[key] !== null && query[key] !== '') {
        parts.push(key + '=' + enc(query[key]));
      }
    });
    return parts.join('&');
  }

  function anaRead(ctx) {
    anaDefaults();
    var host = ana.body;
    if (!host) return;
    if (offlineNow()) {
      ana.data = null;
      offlineState(ctx, host, function () { anaRead(ctx); });
      return;
    }
    var seq = ana.seq + 1;
    ana.seq = seq;
    ana.loading = true;
    ctx.renderLoading(host);
    ctx.get('api/analytics/dashboard', anaQuery()).then(function (res) {
      if (seq !== ana.seq) return;
      ana.loading = false;
      if (!res.ok) {
        ana.data = null;
        ctx.renderError(host, ctx.panelError(res, function () { anaRead(ctx); }));
        return;
      }
      ana.data = unwrapRecord(res.data, ['dashboard', 'report', 'analytics']);
      paintAnalytics(ctx);
    });
  }

  /* ---------------------------------------------------------------- *
   * 3. analytics tab render
   * ---------------------------------------------------------------- */

  function renderAnalytics(host, ctx) {
    ana.paintedLang = ctx.lang();
    ctx.clear(host);
    anaDefaults();
    var panel = ctx.add(host, ctx.el('section', { class: 'ext-ana' }));

    var head = ctx.add(panel, ctx.el('header', { class: 'ext-panel__head' }));
    sectionTitle(ctx, head, 'analytics.title', 'h3').className = 'ext-panel__title';
    var sub = ctx.textNode(head, 'analytics.subtitle', null, 'p');
    sub.className = 'ext-panel__sub';

    var form = ctx.add(panel, ctx.el('form', {
      class: 'ext-form ext-ana__filters',
      attrs: { 'aria-label': ctx.t('analytics.filter.aria') }
    }));

    var fromInput = textInput(ctx, 'date', ana.from);
    fieldRow(ctx, form, 'analytics.filter.from', fromInput);
    var toInput = textInput(ctx, 'date', ana.to);
    fieldRow(ctx, form, 'analytics.filter.to', toInput);
    var agentInput = textInput(ctx, 'text', ana.agent);
    agentInput.setAttribute('placeholder', ctx.t('analytics.filter.agent.placeholder'));
    fieldRow(ctx, form, 'analytics.filter.agent', agentInput);
    var granSelect = selectInput(ctx, [
      { value: 'day', key: 'analytics.granularity.day' },
      { value: 'week', key: 'analytics.granularity.week' }
    ]);
    granSelect.value = ana.granularity;
    fieldRow(ctx, form, 'analytics.filter.granularity', granSelect);

    var actions = ctx.add(form, ctx.el('div', { class: 'ext-ana__actions' }));
    var submit = ctx.add(actions, ctx.el('button', { class: 'trust-btn trust-btn--primary', attrs: { type: 'submit' } }));
    ctx.textNode(submit, 'analytics.filter.apply');
    var refresh = ctx.add(actions, ctx.el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    ctx.textNode(refresh, 'analytics.filter.refresh');

    var exportRow = ctx.add(panel, ctx.el('div', { class: 'ext-ana__export' }));
    sectionTitle(ctx, exportRow, 'analytics.export.title', 'h4');
    var exportNote = ctx.textNode(exportRow, 'analytics.export.note', null, 'p');
    exportNote.className = 'ext-note';

    if (ctx.can('audit.export')) {
      var exportButtons = ctx.add(exportRow, ctx.el('div', { class: 'ext-ana__export-buttons' }));
      ['csv', 'pdf'].forEach(function (format) {
        var key = 'analytics.export.' + format;
        var button = ctx.add(exportButtons, ctx.el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
        ctx.textNode(button, key);
        button.addEventListener('click', function () {
          var url = '/api/analytics/export?format=' + enc(format) + '&' + anaQueryString();
          ctx.download(url);
          ctx.toast('analytics.export.toast', { format: format.toUpperCase() }, 'info');
        });
      });
    } else {
      var denied = ctx.textNode(exportRow, 'analytics.export.denied', null, 'p');
      denied.className = 'ext-note ext-note--warn';
    }

    var body = ctx.add(panel, ctx.el('div', { class: 'ext-ana__body' }));
    ana.body = body;

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      ana.from = fromInput.value || dateOffset(-6);
      ana.to = toInput.value || dateOffset(0);
      ana.agent = String(agentInput.value || '').trim();
      ana.granularity = selectValue(granSelect) || 'day';
      anaRead(ctx);
    });
    refresh.addEventListener('click', function () { anaRead(ctx); });
    fromInput.addEventListener('change', function () {
      ana.from = fromInput.value || dateOffset(-6);
      anaRead(ctx);
    });
    toInput.addEventListener('change', function () {
      ana.to = toInput.value || dateOffset(0);
      anaRead(ctx);
    });
    agentInput.addEventListener('change', function () {
      ana.agent = String(agentInput.value || '').trim();
      anaRead(ctx);
    });
    granSelect.addEventListener('change', function () {
      ana.granularity = selectValue(granSelect) || 'day';
      anaRead(ctx);
    });

    if (ana.data) paintAnalytics(ctx);
    else anaRead(ctx);
  }

  function repaintAnalytics(host, ctx) {
    if (ctx.lang() === ana.paintedLang) return;
    renderAnalytics(host, ctx);
  }

  /* ---------------------------------------------------------------- *
   * 4. analytics paint: metrics / trend / heatmap / top lists
   * ---------------------------------------------------------------- */

  function paintAnalytics(ctx) {
    var host = ana.body;
    if (!host) return;
    var data = ana.data || {};
    ctx.clear(host);

    if (!Object.keys(data).length) {
      ctx.renderEmpty(host, 'analytics.empty');
      return;
    }

    paintMetrics(ctx, host, data);
    paintTrend(ctx, host, data);
    paintHeatmap(ctx, host, data);
    paintTopLists(ctx, host, data);
  }

  function metricCard(ctx, parent, labelKey, valueText, barValue, hintKey) {
    var card = ctx.add(parent, ctx.el('div', { class: 'ext-metric' }));
    var label = ctx.add(card, ctx.el('span', { class: 'ext-metric__label', text: ctx.t(labelKey) }));
    ctx.stamp(label, labelKey);
    var value = ctx.add(card, ctx.el('span', { class: 'ext-metric__value', text: valueText }));
    value.setAttribute('title', String(valueText));
    if (barValue !== null && barValue !== undefined) {
      var bar = ctx.add(card, ctx.el('div', { class: 'ext-metric__bar' }));
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', ctx.t('analytics.metric.bar.aria', { value: percentText(ctx, barValue) }));
      var fill = ctx.add(bar, ctx.el('span', { class: 'ext-metric__bar-fill' }));
      fill.setAttribute('style', 'width:' + (clamp01(barValue) * 100).toFixed(1) + '%');
    }
    if (hintKey) hintText(ctx, card, hintKey);
    return card;
  }

  function paintMetrics(ctx, host, data) {
    var grid = ctx.add(host, ctx.el('div', { class: 'ext-metric-grid' }));

    var totalTasks = pickNum(data, ['total_tasks', 'totals.tasks', 'totals.total', 'tasks_total', 'total']);
    var totalInterventions = pickNum(data, ['total_interventions', 'totals.interventions',
      'interventions_total', 'interventions']);
    var rate = pickNum(data, ['intervention_rate', 'rate', 'totals.rate']);
    var approval = pickNum(data, ['avg_approval_seconds', 'average_approval_seconds',
      'avg_approval_time', 'totals.avg_approval_seconds', 'totals.avg_time']);
    var approvalMs = approval === null ? pickNum(data, ['avg_approval_ms', 'average_approval_ms']) : null;
    var users = pickNum(data, ['users', 'user_count', 'unique_users', 'anonymised_users',
      'anonymized_users', 'totals.users']);

    metricCard(ctx, grid, 'analytics.metric.tasks', countText(ctx, totalTasks), null, null);
    metricCard(ctx, grid, 'analytics.metric.interventions', countText(ctx, totalInterventions), null, null);
    metricCard(ctx, grid, 'analytics.metric.rate', percentText(ctx, rate), null, null);

    var approvalSeconds = approval !== null ? approval : (approvalMs !== null ? approvalMs / 1000 : null);
    metricCard(ctx, grid, 'analytics.metric.approval', durText(ctx, approvalSeconds), null, null);

    var trust = pickNum(data, ['trust_health', 'trust.health', 'trust_health_score']);
    var derived = false;
    if (trust === null && rate !== null) {
      trust = 1 - rate;
      derived = true;
    }
    var trustCard = metricCard(ctx, grid, 'analytics.metric.trust', percentText(ctx, trust), trust,
      derived ? 'analytics.metric.trust.derived' : 'analytics.metric.trust.hint');
    if (trustCard && derived) trustCard.setAttribute('data-derived', 'true');

    metricCard(ctx, grid, 'analytics.metric.users', countText(ctx, users), null,
      'analytics.metric.users.hint');
  }

  function trendSeries(data) {
    var rows = pickList(data, ['trend', 'series', 'by_bucket', 'intervention_trend',
      'intervention_rate_series']);
    var series = [];
    rows.forEach(function (row, index) {
      if (!isObj(row)) {
        var flat = numOf(row);
        if (flat !== null) series.push({ label: String(index + 1), rate: flat });
        return;
      }
      var label = pick(row, ['bucket', 'date', 'label', 'key', 'period', 'at']);
      var value = pickNum(row, ['rate', 'intervention_rate', 'value', 'ratio']);
      if (value === null) return;
      series.push({ label: str(label) || String(index + 1), rate: value });
    });
    return series;
  }

  function paintTrend(ctx, host, data) {
    var box = ctx.add(host, ctx.el('section', { class: 'ext-ana__section' }));
    sectionTitle(ctx, box, 'analytics.chart.title', 'h4');
    var body = ctx.add(box, ctx.el('div', { class: 'ext-ana__chart-host' }));
    var series = trendSeries(data);
    if (!series.length) {
      ctx.renderEmpty(body, 'analytics.chart.empty');
      return;
    }
    var rates = series.map(function (point) { return clamp01(point.rate); });
    var maxRate = Math.max.apply(null, rates);
    var minRate = Math.min.apply(null, rates);
    var aria = ctx.t('analytics.chart.aria', {
      points: series.length,
      max: ctx.fmtPercent(maxRate, 0),
      min: ctx.fmtPercent(minRate, 0)
    });
    ctx.add(body, drawTrendChart(ctx, series, aria));
  }

  function svgNode(tag, attrs) {
    var doc = global.document;
    var node = doc.createElementNS(SVG_NS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) { node.setAttribute(key, String(attrs[key])); });
    }
    return node;
  }

  function drawTrendChart(ctx, series, ariaLabel) {
    var width = 640;
    var height = 220;
    var left = 56;
    var right = width - 16;
    var top = 20;
    var bottom = height - 44;
    var svg = svgNode('svg', {
      class: 'ext-ana-chart',
      viewBox: '0 0 ' + width + ' ' + height,
      role: 'img',
      'aria-label': ariaLabel
    });

    function xAt(index) {
      if (series.length <= 1) return (left + right) / 2;
      return left + (right - left) * index / (series.length - 1);
    }

    function yAt(value) {
      return bottom - clamp01(value) * (bottom - top);
    }

    [0, 0.5, 1].forEach(function (tick) {
      var y = yAt(tick);
      svg.appendChild(svgNode('line', {
        class: 'ext-ana-chart__grid', x1: left, y1: y, x2: right, y2: y
      }));
      var label = svgNode('text', { class: 'ext-ana-chart__tick', x: left - 8, y: y + 4 });
      label.textContent = ctx.fmtPercent(tick, 0);
      svg.appendChild(label);
    });

    var points = series.map(function (point, index) {
      return xAt(index).toFixed(1) + ',' + yAt(point.rate).toFixed(1);
    }).join(' ');
    svg.appendChild(svgNode('polyline', { class: 'ext-ana-chart__line', points: points }));

    series.forEach(function (point, index) {
      var x = xAt(index);
      var y = yAt(point.rate);
      svg.appendChild(svgNode('circle', { class: 'ext-ana-chart__dot', cx: x, cy: y, r: 3 }));
    });

    var marks = [];
    if (series.length <= 8) {
      series.forEach(function (point, index) { marks.push(index); });
    } else {
      marks = [0, Math.floor((series.length - 1) / 2), series.length - 1];
    }
    marks.forEach(function (index) {
      var point = series[index];
      var label = svgNode('text', {
        class: 'ext-ana-chart__x', x: xAt(index), y: bottom + 18, textAnchor: 'middle'
      });
      label.textContent = point.label;
      svg.appendChild(label);
    });

    var yTitle = svgNode('text', { class: 'ext-ana-chart__axis', x: 8, y: 14 });
    yTitle.textContent = ctx.t('analytics.chart.y');
    svg.appendChild(yTitle);
    var xTitle = svgNode('text', {
      class: 'ext-ana-chart__axis', x: right, y: height - 8, textAnchor: 'end'
    });
    xTitle.textContent = ctx.t('analytics.chart.x');
    svg.appendChild(xTitle);

    return svg;
  }

  function heatEntries(data) {
    var rows = pickList(data, ['heatmap', 'heat', 'parameter_heatmap', 'top_parameters',
      'parameters', 'top_params']);
    var entries = [];
    rows.forEach(function (row) {
      if (!isObj(row)) return;
      var tool = str(pick(row, ['tool', 'agent_tool', 'tool_name', 'name']));
      var param = str(pick(row, ['param', 'parameter', 'field', 'param_name']));
      var count = pickNum(row, ['count', 'occurrences', 'modifications', 'total', 'value']);
      if (!tool && !param) return;
      entries.push({ tool: tool || '\u2014', param: param || '\u2014', count: count === null ? 0 : count });
    });
    return entries;
  }

  function paintHeatmap(ctx, host, data) {
    var box = ctx.add(host, ctx.el('section', { class: 'ext-ana__section' }));
    sectionTitle(ctx, box, 'analytics.heat.title', 'h4');
    var body = ctx.add(box, ctx.el('div', { class: 'ext-ana__heat-host' }));
    var entries = heatEntries(data);
    if (!entries.length) {
      ctx.renderEmpty(body, 'analytics.heat.empty');
      return;
    }

    var tools = [];
    var params = [];
    var counts = {};
    var max = 0;
    entries.forEach(function (entry) {
      if (tools.indexOf(entry.tool) < 0) tools.push(entry.tool);
      if (params.indexOf(entry.param) < 0) params.push(entry.param);
      var key = entry.tool + '\u0000' + entry.param;
      if (!counts[key] || counts[key] < entry.count) counts[key] = entry.count;
      if (entry.count > max) max = entry.count;
    });
    tools = tools.slice(0, 8);
    params = params.slice(0, 8);

    var table = ctx.add(body, ctx.el('table', {
      class: 'ext-ana__heat',
      attrs: { 'aria-label': ctx.t('analytics.heat.title') }
    }));
    var thead = ctx.add(table, ctx.el('thead'));
    var headRow = ctx.add(thead, ctx.el('tr'));
    var corner = ctx.add(headRow, ctx.el('th', { attrs: { scope: 'col' } }));
    ctx.textNode(corner, 'analytics.heat.tool');
    params.forEach(function (param) {
      var th = ctx.add(headRow, ctx.el('th', { attrs: { scope: 'col' } }));
      th.setAttribute('title', param);
      th.textContent = param;
    });

    var tbody = ctx.add(table, ctx.el('tbody'));
    tools.forEach(function (tool) {
      var row = ctx.add(tbody, ctx.el('tr'));
      var rowHead = ctx.add(row, ctx.el('th', { attrs: { scope: 'row' }, text: tool }));
      rowHead.setAttribute('title', tool);
      params.forEach(function (param) {
        var count = counts[tool + '\u0000' + param];
        if (count === undefined) count = 0;
        var cell = ctx.add(row, ctx.el('td', {
          class: 'ext-ana__heat-cell' + (count ? '' : ' is-zero')
        }));
        cell.setAttribute('title', ctx.t('analytics.heat.cell', {
          tool: tool, param: param, count: count
        }));
        var fill = ctx.add(cell, ctx.el('span', { class: 'ext-ana__heat-fill' }));
        var ratio = max > 0 ? count / max : 0;
        fill.setAttribute('style', 'opacity:' + (count ? (0.15 + 0.85 * ratio).toFixed(2) : '0'));
        var text = ctx.add(cell, ctx.el('span', { class: 'ext-ana__heat-count' }));
        text.textContent = count ? ctx.fmtNumber(count, 0) : '';
      });
    });

    var legend = ctx.add(body, ctx.el('div', { class: 'ext-ana__heat-legend' }));
    var low = ctx.textNode(legend, 'analytics.heat.legend.low', null, 'span');
    low.className = 'ext-ana__heat-legend-label';
    [0.2, 0.5, 0.8, 1].forEach(function (opacity) {
      var swatch = ctx.add(legend, ctx.el('span', { class: 'ext-ana__heat-swatch' }));
      swatch.setAttribute('style', 'opacity:' + opacity);
    });
    var high = ctx.textNode(legend, 'analytics.heat.legend.high', null, 'span');
    high.className = 'ext-ana__heat-legend-label';
  }

  function topEntry(ctx, parent, titleKey, rows, nameKeys) {
    var box = ctx.add(parent, ctx.el('div', { class: 'ext-ana__top' }));
    sectionTitle(ctx, box, titleKey, 'h5');
    var host = ctx.add(box, ctx.el('div', { class: 'ext-ana__top-host' }));
    if (!rows.length) {
      ctx.renderEmpty(host, 'analytics.top.empty');
      return;
    }
    var list = ctx.add(host, ctx.el('ul', { class: 'ext-ana__top-list' }));
    rows.slice(0, 8).forEach(function (row) {
      if (!isObj(row)) return;
      var item = ctx.add(list, ctx.el('li'));
      var name = str(pick(row, nameKeys)).trim();
      var param = str(pick(row, ['param', 'parameter']));
      if (name && param) name = name + ' \u00B7 ' + param;
      if (!name) name = str(pick(row, ['id'])) || '\u2014';
      var nameNode = ctx.add(item, ctx.el('span', { class: 'ext-ana__top-name', text: name }));
      nameNode.setAttribute('title', name);
      var count = pickNum(row, ['count', 'occurrences', 'modifications', 'total']);
      var countNode = ctx.add(item, ctx.el('span', { class: 'ext-ana__top-count' }));
      if (count === null) countNode.textContent = ctx.t('analytics.metric.missing');
      else {
        countNode.textContent = ctx.t('analytics.top.count', { count: ctx.fmtNumber(count, 0) });
        ctx.stamp(countNode, 'analytics.top.count');
      }
    });
  }

  function paintTopLists(ctx, host, data) {
    var box = ctx.add(host, ctx.el('section', { class: 'ext-ana__section' }));
    sectionTitle(ctx, box, 'analytics.top.title', 'h4');
    var grid = ctx.add(box, ctx.el('div', { class: 'ext-ana__tops' }));
    topEntry(ctx, grid, 'analytics.top.parameters',
      pickList(data, ['top_parameters', 'top_params', 'parameters', 'most_modified']),
      ['tool', 'parameter', 'param', 'name']);
    topEntry(ctx, grid, 'analytics.top.rejected',
      pickList(data, ['top_rejected', 'rejected_steps', 'top_rejections', 'rejected']),
      ['tool', 'step', 'action', 'name']);
  }

  /* ---------------------------------------------------------------- *
   * 5. suggestions tab state
   * ---------------------------------------------------------------- */

  var sug = {
    items: null,
    prefs: null,
    enabled: true,
    loadingItems: false,
    loadingPrefs: false,
    seqItems: 0,
    seqPrefs: 0,
    itemsHost: null,
    prefsHost: null,
    feedbackHost: null,
    statusNode: null,
    shown: {},
    baselineDone: false,
    polledAt: 0,
    polling: false,
    paintedLang: ''
  };

  function readEnabled() {
    try {
      if (global.localStorage) {
        var saved = global.localStorage.getItem(SUG_ENABLED_KEY);
        if (saved === '0') return false;
      }
    } catch (e) { /* file:// may refuse storage */ }
    return true;
  }

  function saveEnabled(value) {
    try {
      if (global.localStorage) global.localStorage.setItem(SUG_ENABLED_KEY, value ? '1' : '0');
    } catch (e) { /* best effort only */ }
  }

  sug.enabled = readEnabled();

  function suggestionRows(payload) {
    return unwrapList(payload, ['suggestions', 'items', 'results', 'data']);
  }

  function preferenceRows(payload) {
    return unwrapList(payload, ['preferences', 'items', 'results', 'data']);
  }

  function suggestionStatus(item) {
    return isObj(item) ? str(pick(item, ['status', 'state'])) : '';
  }

  function cooldownUntil(item) {
    var value = pick(item, ['cooldown_until', 'cooldownUntil', 'expires_at']);
    if (!value) return null;
    var at = Date.parse(str(value));
    return isFinite(at) ? at : null;
  }

  function paintFeatureState(ctx) {
    var node = sug.statusNode;
    if (!node) return;
    ctx.clear(node);
    ctx.textNode(node, sug.enabled ? 'suggestions.feature.on' : 'suggestions.feature.off');
  }

  function loadSuggestions(ctx) {
    var host = sug.itemsHost;
    if (!host) return;
    if (!sug.enabled) {
      sug.items = null;
      ctx.renderEmpty(host, 'suggestions.feature.off');
      return;
    }
    if (offlineNow()) {
      sug.items = null;
      offlineState(ctx, host, function () { loadSuggestions(ctx); });
      return;
    }
    var seq = sug.seqItems + 1;
    sug.seqItems = seq;
    sug.loadingItems = true;
    ctx.renderLoading(host);
    ctx.get('api/suggestions').then(function (res) {
      if (seq !== sug.seqItems) return;
      sug.loadingItems = false;
      if (!res.ok) {
        sug.items = null;
        ctx.renderError(host, ctx.panelError(res, function () { loadSuggestions(ctx); }));
        return;
      }
      sug.items = suggestionRows(res.data);
      paintSuggestions(ctx);
    });
  }

  function paintSuggestions(ctx) {
    var host = sug.itemsHost;
    if (!host) return;
    ctx.clear(host);
    var items = isArr(sug.items) ? sug.items : [];
    var open = [];
    var cooling = [];
    var now = Date.now();
    items.forEach(function (item) {
      var status = suggestionStatus(item);
      if (!status || status === 'open' || status === 'pending') open.push(item);
      else if (status === 'dismissed') {
        var until = cooldownUntil(item);
        if (until !== null && until > now) cooling.push(item);
      }
    });

    if (!open.length) ctx.renderEmpty(host, 'suggestions.list.empty');
    else {
      var list = ctx.add(host, ctx.el('div', { class: 'ext-sug__list' }));
      open.forEach(function (item) { paintSuggestionCard(ctx, list, item); });
    }

    if (cooling.length) {
      var box = ctx.add(host, ctx.el('section', { class: 'ext-sug__cooldown' }));
      sectionTitle(ctx, box, 'suggestions.cooldown.title', 'h5');
      var note = ctx.textNode(box, 'suggestions.cooldown.note', null, 'p');
      note.className = 'ext-note';
      var rows = ctx.add(box, ctx.el('ul', { class: 'ext-sug__cooldown-list' }));
      cooling.forEach(function (item) {
        var row = ctx.add(rows, ctx.el('li'));
        var name = ctx.add(row, ctx.el('span', { class: 'ext-sug__cooldown-name' }));
        name.textContent = ctx.t('suggestions.cooldown.row', {
          tool: str(pick(item, ['tool'])) || '\u2014',
          param: str(pick(item, ['param', 'parameter'])) || '\u2014'
        });
        ctx.stamp(name, 'suggestions.cooldown.row');
        var until = ctx.add(row, ctx.el('span', { class: 'ext-sug__cooldown-until' }));
        var at = cooldownUntil(item);
        until.textContent = ctx.t('suggestions.cooldown.until', {
          until: at === null ? '\u2014' : ctx.fmtTime(new Date(at).toISOString())
        });
        ctx.stamp(until, 'suggestions.cooldown.until');
      });
    }
  }

  function directionLabel(ctx, item) {
    var direction = str(pick(item, ['direction', 'trend']));
    if (direction === 'increase' || direction === 'up') return 'suggestions.direction.increase';
    if (direction === 'decrease' || direction === 'down') return 'suggestions.direction.decrease';
    return '';
  }

  function paintSuggestionCard(ctx, parent, item) {
    var card = ctx.add(parent, ctx.el('article', { class: 'ext-sug-card' }));
    var id = str(pick(item, ['id', 'suggestion_id']));
    if (id) card.setAttribute('data-id', id);

    var head = ctx.add(card, ctx.el('div', { class: 'ext-sug-card__head' }));
    var tool = str(pick(item, ['tool']));
    var param = str(pick(item, ['param', 'parameter']));
    var title = str(pick(item, ['title']));
    var nameNode = ctx.add(head, ctx.el('strong', { class: 'ext-sug-card__name' }));
    nameNode.textContent = title || ((tool || '\u2014') + ' \u00B7 ' + (param || '\u2014'));

    var directionKey = directionLabel(ctx, item);
    if (directionKey) {
      var badge = ctx.add(head, ctx.el('span', { class: 'ext-sug-card__badge', text: ctx.t(directionKey) }));
      ctx.stamp(badge, directionKey);
    }

    var occurrences = pickNum(item, ['occurrences', 'count', 'hits', 'times']);
    if (occurrences !== null) {
      var occ = ctx.add(head, ctx.el('span', { class: 'ext-sug-card__occ' }));
      occ.textContent = ctx.t('suggestions.card.occurrences', { count: ctx.fmtNumber(occurrences, 0) });
      ctx.stamp(occ, 'suggestions.card.occurrences');
    }

    var change = ctx.add(card, ctx.el('div', { class: 'ext-sug-card__change' }));
    ctx.add(change, ctx.el('code', {
      class: 'ext-sug-card__value', text: displayValue(pick(item, ['from_value', 'from', 'old_value']))
    }));
    var arrow = ctx.add(change, ctx.el('span', { class: 'ext-sug-card__arrow', attrs: { 'aria-hidden': 'true' } }));
    arrow.textContent = '\u2192';
    ctx.add(change, ctx.el('code', {
      class: 'ext-sug-card__value', text: displayValue(pick(item, ['to_value', 'to', 'new_value']))
    }));

    var actions = ctx.add(card, ctx.el('div', { class: 'ext-sug-card__actions' }));
    var accept = ctx.add(actions, ctx.el('button', { class: 'trust-btn trust-btn--primary', attrs: { type: 'button' } }));
    ctx.textNode(accept, 'suggestions.action.accept');
    var dismiss = ctx.add(actions, ctx.el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    ctx.textNode(dismiss, 'suggestions.action.dismiss');

    if (!ctx.can('intervene.decide')) {
      accept.setAttribute('disabled', 'disabled');
      dismiss.setAttribute('disabled', 'disabled');
      return;
    }
    accept.addEventListener('click', function () { decideSuggestion(ctx, id, 'accept', accept, dismiss); });
    dismiss.addEventListener('click', function () { decideSuggestion(ctx, id, 'dismiss', dismiss, accept); });
  }

  function decideSuggestion(ctx, id, verb, button, other) {
    if (!id) return;
    if (offlineNow()) {
      offlineState(ctx, sug.feedbackHost, function () { decideSuggestion(ctx, id, verb, button, other); });
      return;
    }
    if (button) {
      button.setAttribute('disabled', 'disabled');
      button.textContent = ctx.t('suggestions.action.busy');
    }
    if (other) other.setAttribute('disabled', 'disabled');
    ctx.post('api/suggestions/' + enc(id) + '/' + verb, {}).then(function (res) {
      if (!res.ok) {
        if (button) {
          button.removeAttribute('disabled');
          ctx.clear(button);
          ctx.textNode(button, verb === 'accept' ? 'suggestions.action.accept' : 'suggestions.action.dismiss');
        }
        if (other) other.removeAttribute('disabled');
        if (sug.feedbackHost) {
          ctx.renderError(sug.feedbackHost, ctx.panelError(res, function () {
            decideSuggestion(ctx, id, verb, button, other);
          }));
        }
        return;
      }
      if (sug.feedbackHost) ctx.clear(sug.feedbackHost);
      if (verb === 'accept') ctx.toast('suggestions.toast.accepted', null, 'info');
      else ctx.toast('suggestions.toast.dismissed', { days: 7 }, 'info');
      loadSuggestions(ctx);
      loadPreferences(ctx);
    });
  }

  function loadPreferences(ctx) {
    var host = sug.prefsHost;
    if (!host) return;
    if (offlineNow()) {
      sug.prefs = null;
      offlineState(ctx, host, function () { loadPreferences(ctx); });
      return;
    }
    var seq = sug.seqPrefs + 1;
    sug.seqPrefs = seq;
    sug.loadingPrefs = true;
    ctx.renderLoading(host);
    ctx.get('api/preferences').then(function (res) {
      if (seq !== sug.seqPrefs) return;
      sug.loadingPrefs = false;
      if (!res.ok) {
        sug.prefs = null;
        ctx.renderError(host, ctx.panelError(res, function () { loadPreferences(ctx); }));
        return;
      }
      sug.prefs = preferenceRows(res.data);
      paintPreferences(ctx);
    });
  }

  function sourceKey(source) {
    var value = str(source);
    if (value === 'manual') return 'suggestions.pref.source.manual';
    if (value === 'suggestion') return 'suggestions.pref.source.suggestion';
    return '';
  }

  function paintPreferences(ctx) {
    var host = sug.prefsHost;
    if (!host) return;
    ctx.clear(host);
    var rows = isArr(sug.prefs) ? sug.prefs : [];
    if (!rows.length) {
      ctx.renderEmpty(host, 'suggestions.pref.empty');
      return;
    }
    var table = ctx.add(host, ctx.el('table', { class: 'ext-sug__prefs' }));
    var thead = ctx.add(table, ctx.el('thead'));
    var headRow = ctx.add(thead, ctx.el('tr'));
    ['suggestions.pref.tool', 'suggestions.pref.param', 'suggestions.pref.value',
      'suggestions.pref.source'].forEach(function (key) {
      var th = ctx.add(headRow, ctx.el('th', { attrs: { scope: 'col' } }));
      ctx.textNode(th, key);
    });
    var tbody = ctx.add(table, ctx.el('tbody'));
    rows.forEach(function (row) {
      var tr = ctx.add(tbody, ctx.el('tr'));
      ctx.add(tr, ctx.el('td', { text: str(pick(row, ['tool'])) || '\u2014' }));
      ctx.add(tr, ctx.el('td', { text: str(pick(row, ['param', 'parameter'])) || '\u2014' }));
      ctx.add(tr, ctx.el('td', { text: displayValue(pick(row, ['value'])) }));
      var cell = ctx.add(tr, ctx.el('td'));
      var key = sourceKey(pick(row, ['source']));
      if (key) {
        var sourceNode = ctx.add(cell, ctx.el('span', { class: 'ext-sug__source', text: ctx.t(key) }));
        ctx.stamp(sourceNode, key);
      } else {
        cell.textContent = str(pick(row, ['source'])) || '\u2014';
      }
    });
  }

  function suggestionForm(ctx, parent) {
    var form = ctx.add(parent, ctx.el('form', { class: 'ext-form ext-sug__add' }));
    var toolInput = textInput(ctx, 'text', '');
    fieldRow(ctx, form, 'suggestions.pref.add.tool', toolInput);
    var paramInput = textInput(ctx, 'text', '');
    fieldRow(ctx, form, 'suggestions.pref.add.param', paramInput);
    var valueInput = textInput(ctx, 'text', '');
    fieldRow(ctx, form, 'suggestions.pref.add.value', valueInput);
    var submit = ctx.add(form, ctx.el('button', { class: 'trust-btn trust-btn--primary', attrs: { type: 'submit' } }));
    ctx.textNode(submit, 'suggestions.pref.add.submit');

    function savePreference() {
      if (offlineNow()) {
        offlineState(ctx, sug.feedbackHost, savePreference);
        return;
      }
      var tool = String(toolInput.value || '').trim();
      var param = String(paramInput.value || '').trim();
      var value = String(valueInput.value || '').trim();
      if (!tool || !param || !value) {
        if (sug.feedbackHost) {
          ctx.clear(sug.feedbackHost);
          var box = ctx.add(sug.feedbackHost, ctx.el('div', { class: 'trust-state', dataset: { state: 'error' } }));
          ctx.textNode(box, 'suggestions.pref.invalid', null, 'p');
        }
        return;
      }
      submit.setAttribute('disabled', 'disabled');
      ctx.post('api/preferences', { tool: tool, param: param, value: value, source: 'manual' })
        .then(function (res) {
          submit.removeAttribute('disabled');
          if (!res.ok) {
            if (sug.feedbackHost) {
              ctx.renderError(sug.feedbackHost, ctx.panelError(res, savePreference));
            }
            return;
          }
          if (sug.feedbackHost) ctx.clear(sug.feedbackHost);
          toolInput.value = '';
          paramInput.value = '';
          valueInput.value = '';
          ctx.toast('suggestions.toast.pref.saved', null, 'info');
          loadPreferences(ctx);
        });
    }

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      savePreference();
    });
    if (!ctx.can('intervene.decide')) submit.setAttribute('disabled', 'disabled');
    return form;
  }

  /* ---------------------------------------------------------------- *
   * 6. suggestions tab render
   * ---------------------------------------------------------------- */

  function renderSuggestions(host, ctx) {
    sug.paintedLang = ctx.lang();
    ctx.clear(host);
    var panel = ctx.add(host, ctx.el('section', { class: 'ext-sug' }));

    var head = ctx.add(panel, ctx.el('header', { class: 'ext-panel__head' }));
    sectionTitle(ctx, head, 'suggestions.title', 'h3').className = 'ext-panel__title';
    var sub = ctx.textNode(head, 'suggestions.subtitle', null, 'p');
    sub.className = 'ext-panel__sub';

    var feature = ctx.add(panel, ctx.el('div', { class: 'ext-sug__feature' }));
    var switchButton = ctx.add(feature, ctx.el('button', {
      class: 'ext-switch', attrs: { type: 'button', role: 'switch', 'aria-checked': sug.enabled ? 'true' : 'false' }
    }));
    var knob = ctx.add(switchButton, ctx.el('span', { class: 'ext-switch__knob', attrs: { 'aria-hidden': 'true' } }));
    knob.textContent = '';
    var switchLabel = ctx.add(feature, ctx.el('span', { class: 'ext-switch__label', text: ctx.t('suggestions.feature.switch') }));
    ctx.stamp(switchLabel, 'suggestions.feature.switch');
    var statusNode = ctx.add(feature, ctx.el('span', { class: 'ext-switch__state' }));
    sug.statusNode = statusNode;
    paintFeatureState(ctx);
    var featureNote = ctx.textNode(feature, 'suggestions.feature.note', null, 'p');
    featureNote.className = 'ext-note';

    var feedbackHost = ctx.add(panel, ctx.el('div', { class: 'ext-sug__feedback' }));
    sug.feedbackHost = feedbackHost;

    var listSection = ctx.add(panel, ctx.el('section', { class: 'ext-panel__section' }));
    sectionTitle(ctx, listSection, 'suggestions.list.title', 'h4');
    var pollNote = ctx.textNode(listSection, 'suggestions.poll.note', null, 'p');
    pollNote.className = 'ext-note';
    if (!ctx.can('intervene.decide')) {
      var denied = ctx.textNode(listSection, 'suggestions.action.denied', null, 'p');
      denied.className = 'ext-note ext-note--warn';
    }
    var itemsHost = ctx.add(listSection, ctx.el('div', { class: 'ext-sug__items' }));
    sug.itemsHost = itemsHost;

    var prefSection = ctx.add(panel, ctx.el('section', { class: 'ext-panel__section' }));
    sectionTitle(ctx, prefSection, 'suggestions.pref.title', 'h4');
    var prefsHost = ctx.add(prefSection, ctx.el('div', { class: 'ext-sug__prefs-host' }));
    sug.prefsHost = prefsHost;
    sectionTitle(ctx, prefSection, 'suggestions.pref.add.title', 'h5');
    suggestionForm(ctx, prefSection);

    switchButton.addEventListener('click', function () {
      sug.enabled = !sug.enabled;
      saveEnabled(sug.enabled);
      switchButton.setAttribute('aria-checked', sug.enabled ? 'true' : 'false');
      switchButton.classList.toggle('is-on', sug.enabled);
      paintFeatureState(ctx);
      if (sug.enabled) loadSuggestions(ctx);
      else {
        sug.items = null;
        ctx.renderEmpty(itemsHost, 'suggestions.feature.off');
      }
    });
    switchButton.classList.toggle('is-on', sug.enabled);

    if (isArr(sug.items)) paintSuggestions(ctx);
    else loadSuggestions(ctx);
    if (isArr(sug.prefs)) paintPreferences(ctx);
    else loadPreferences(ctx);
  }

  function repaintSuggestions(host, ctx) {
    if (ctx.lang() === sug.paintedLang) return;
    renderSuggestions(host, ctx);
  }

  /* ---------------------------------------------------------------- *
   * 7. the non-intrusive toast: poll at most once per 60 s
   * ---------------------------------------------------------------- */

  function pollSuggestions(state, ctx) {
    if (!sug.enabled) return;
    if (sug.polling) return;
    if (!ctx.can('records.read')) return;
    if (!liveNow()) return;
    var now = Date.now();
    if (now - sug.polledAt < POLL_MS) return;
    sug.polledAt = now;
    sug.polling = true;
    ctx.get('api/suggestions', { status: 'open' }).then(function (res) {
      sug.polling = false;
      if (!res.ok) return;
      var list = suggestionRows(res.data);
      var fresh = [];
      list.forEach(function (item) {
        var id = str(pick(item, ['id', 'suggestion_id']));
        if (!id || sug.shown[id]) return;
        sug.shown[id] = true;
        if (sug.baselineDone) fresh.push(item);
      });
      sug.baselineDone = true;
      if (!fresh.length) return;
      if (fresh.length === 1) {
        ctx.toast('suggestions.toast.new.one', {
          tool: str(pick(fresh[0], ['tool'])) || '\u2014',
          param: str(pick(fresh[0], ['param', 'parameter'])) || '\u2014'
        }, 'info');
      } else {
        ctx.toast('suggestions.toast.new.many', { count: fresh.length }, 'info');
      }
    }, function () {
      sug.polling = false;
    });
  }

  /* ---------------------------------------------------------------- *
   * 8. registration
   * ---------------------------------------------------------------- */

  Ext.register({
    id: 'analytics',
    version: '1.0',
    tab: {
      titleKey: 'analytics.tab',
      order: 40,
      permission: 'audit.read',
      render: renderAnalytics,
      repaint: repaintAnalytics
    }
  });

  Ext.register({
    id: 'suggestions',
    version: '1.0',
    tab: {
      titleKey: 'suggestions.tab',
      order: 50,
      permission: 'records.read',
      render: renderSuggestions,
      repaint: repaintSuggestions
    },
    onState: pollSuggestions
  });
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
