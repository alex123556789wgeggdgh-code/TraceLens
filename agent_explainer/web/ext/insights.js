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
 *   - all requests go through ctx.get / ctx.post / ctx.call / ctx.download, and
 *     every failure ends in ctx.renderError(host, ctx.panelError(res, retry)).
 *     An offline page says it needs the server instead of inventing data.
 *
 * R6-4 closed the dashboard face (task §2.1/§2.2, 展示纪律 0.4):
 *   - the analytics tab reads the dashboard's metadata trio
 *     (`pre_aggregated` / `scanned` / `agg.reason`) out loud: a pre-aggregated
 *     read says so, a streaming read says 「未预聚合，加载较慢」 and names the
 *     reason code as data. The "≤3s" promise is only ever shown when
 *     `pre_aggregated` is true;
 *   - the export buttons name a file from the response headers
 *     (`X-TraceLens-Export-Format` for the bytes served,
 *     `-Fallback` for a degradation, `-Sha256` for the fingerprint of those
 *     bytes). A degradation is written as a degradation, never as a success;
 *     `X-TraceLens-Export-Fallback` shows its reason code next to the words
 *     "这不是坏掉，也不算成功". A fingerprint row says 这份文件的指纹 and, when a
 *     browser download follows, that the fingerprint belongs to the response
 *     the panel just read.
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
  var TOAST_MS = 12000;
  /* R6-4 (dashboard frontend face):
   *   - SLOW_MS  : the honest "未预聚合，加载较慢" label fires on a measured load
   *                over one second, not on a guess about the server's internals;
   *   - EXPORT_ACCEPT / EXPORT_FORMATS: the frozen format enum is `csv|pdf`,
   *                so the panel ships exactly two buttons. `format=html` is 400
   *                on the server and therefore gets no button here;
   *   - PLANE_REASON: one dictionary key per documented fallback reason code.
   *                An unknown code is shown as the code it is, never as a
   *                translated guess. */
  var SLOW_MS = 1000;
  var EXPORT_ACCEPT = 'text/csv,text/html,application/pdf,application/json';
  var EXPORT_FORMATS = ['csv', 'pdf'];
  var PLANE_REASON = {
    no_ledger_wired: 'analytics.plane.reason.no_ledger_wired',
    agent_filter: 'analytics.plane.reason.agent_filter',
    custom_salt: 'analytics.plane.reason.custom_salt',
    input_not_rerunnable: 'analytics.plane.reason.input_not_rerunnable',
    ledger_missing_no_records: 'analytics.plane.reason.ledger_missing_no_records',
    ledger_unreadable: 'analytics.plane.reason.ledger_unreadable',
    chain_tampered: 'analytics.plane.reason.chain_tampered'
  };

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
    paintedLang: '',
    /* R6-4: measured load time (the honest label eats a real number, not a
     * claim) and the last export answer as the headers described it. */
    startedAt: 0,
    elapsedMs: 0,
    exportHost: null,
    export: { busy: '', last: null, seq: 0 }
  };

  function exportReset() {
    ana.export = { busy: '', last: null, seq: ana.export.seq || 0 };
  }

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

  /* ---------------------------------------------------------------- *
   * 2b. the data plane and the export face (R6-4): honest by construction
   * ---------------------------------------------------------------- */

  function noteRow(ctx, parent, key, params, extraClass) {
    var node = ctx.textNode(parent, key, params || null, 'p');
    node.className = 'ext-note' + (extraClass ? ' ' + extraClass : '');
    return node;
  }

  /* The three metadata items the dashboard ships with its numbers
   * (docs/07 §11.16 / §11.17): where the numbers came from, how many records
   * this read walked, and the reason code when it had to walk them. Missing
   * values stay `null` — the panel then says it has no information instead of
   * painting the fast story. */
  function metaTrio(data) {
    var source = isObj(data) ? data : {};
    var agg = isObj(source.agg) ? source.agg : {};
    var pre = null;
    if (Object.prototype.hasOwnProperty.call(source, 'pre_aggregated')) {
      pre = source.pre_aggregated === true;
    }
    return {
      pre: pre,
      scanned: numOf(pick(source, ['scanned', 'meta.scanned'])),
      reason: str(pick(agg, ['reason'])),
      durable: agg.durable === false ? false : true,
      head: str(pick(agg, ['head'])),
      problems: numOf(pick(agg, ['problems_count'])),
      generatedAt: str(pick(source, ['generated_at', 'meta.generated_at']))
    };
  }

  function paintDataPlane(ctx, host, data) {
    var trio = metaTrio(data);
    var box = ctx.add(host, ctx.el('section', {
      class: 'ext-panel__section ext-ana__plane',
      attrs: { 'aria-label': ctx.t('analytics.plane.title') }
    }));
    box.setAttribute('data-pre-aggregated',
                     trio.pre === null ? 'unknown' : (trio.pre ? 'true' : 'false'));
    box.setAttribute('data-elapsed-ms', String(ana.elapsedMs));
    sectionTitle(ctx, box, 'analytics.plane.title', 'h4');

    /* 1. 来源如实：命中 / 回退 / 不知道，三句里没有第四句 */
    noteRow(ctx, box, trio.pre === true ? 'analytics.plane.pre'
                : (trio.pre === false ? 'analytics.plane.stream' : 'analytics.plane.unknown'),
            null, 'ext-ana__plane-source');

    /* 2. 「未预聚合，加载较慢」：回退必标；命中但实测超一秒也标（数字是自己的尺子） */
    if (trio.pre === false) {
      noteRow(ctx, box, 'analytics.plane.slow', { ms: ana.elapsedMs }, 'ext-note--warn');
      var reasonKey = PLANE_REASON[trio.reason];
      if (reasonKey) noteRow(ctx, box, reasonKey, null, 'ext-ana__plane-reason');
      else noteRow(ctx, box, 'analytics.plane.reason', { code: trio.reason || 'unknown' },
                   'ext-ana__plane-reason');
    } else if (trio.pre === true && ana.elapsedMs > SLOW_MS) {
      noteRow(ctx, box, 'analytics.plane.slow_hit', { ms: ana.elapsedMs }, 'ext-note--warn');
    }

    /* 3. ≤3s 的承诺只在命中时成立（任务 §2.5 的诚实口径） */
    noteRow(ctx, box, trio.pre === true ? 'analytics.plane.timing.pre'
                : 'analytics.plane.timing.stream');

    /* 4. 扫了多少条 / 账本走到哪 / 写没写进去 / 有没有读不动的行 */
    if (trio.scanned !== null) noteRow(ctx, box, 'analytics.plane.scanned', { count: trio.scanned });
    if (trio.pre === true && !trio.durable) {
      noteRow(ctx, box, 'analytics.plane.not_durable', null, 'ext-note--warn');
    }
    if (trio.problems !== null && trio.problems > 0) {
      noteRow(ctx, box, 'analytics.plane.problems', { count: trio.problems }, 'ext-note--warn');
    }
    if (trio.head) noteRow(ctx, box, 'analytics.plane.head', { head: trio.head });
    if (trio.generatedAt) {
      noteRow(ctx, box, 'analytics.plane.generated', { at: ctx.fmtTime(trio.generatedAt) });
    }
    /* 5. 没有推送通道：刷新＝查询时机，不把「重新查询」画成服务端推送 */
    noteRow(ctx, box, 'analytics.plane.refresh');
    return box;
  }

  /* The server writes the served filename into Content-Disposition; the panel
   * reads it and never invents a name. No header → no name → no file: a name
   * made up here is one more claim the page cannot back up. */
  function filenameFromDisposition(value) {
    var text = str(value);
    if (!text) return '';
    var extended = /filename\*\s*=\s*[^'";]*'[^'";]*'([^;]+)/i.exec(text);
    if (extended && str(extended[1]).trim()) return str(extended[1]).trim();
    var plain = /filename\s*=\s*"?([^";]+?)"?\s*(?:;|$)/i.exec(text);
    return plain ? str(plain[1]).trim() : '';
  }

  /* A browser navigation (download anchor / printable tab) reads the server's
   * own bytes; the panel never re-encodes them. */
  function exportUrl(format) {
    return '/api/analytics/export?format=' + enc(format) + '&' + anaQueryString();
  }

  function saveArtifact(ctx, record) {
    try {
      if (typeof global.Blob !== 'function' || typeof global.URL !== 'function'
          || typeof global.URL.createObjectURL !== 'function') return false;
      if (!record.filename) return false;
      /* The served CSV bytes carry a UTF-8 BOM (docs/07 §11.16); decoding to
       * text drops it, so the panel puts that BOM back: the saved bytes then
       * equal the served bytes and `-Sha256` really describes this file. */
      var payload = record.served === 'csv' ? '\ufeff' + record.text : record.text;
      var blob = new global.Blob([payload],
                                 { type: record.contentType || 'text/plain;charset=utf-8' });
      var url = global.URL.createObjectURL(blob);
      var saved = ctx.download(url, record.filename);
      if (typeof global.setTimeout === 'function') {
        global.setTimeout(function () {
          try { global.URL.revokeObjectURL(url); } catch (e) { /* already gone */ }
        }, 2000);
      }
      return !!saved;
    } catch (e) {
      return false;
    }
  }

  function requestExport(ctx, format) {
    if (ana.export.busy) return;
    if (offlineNow()) {
      ana.export.last = { offline: true, requested: format };
      paintExportRow(ctx, ana.exportHost);
      return;
    }
    ana.export.busy = format;
    ana.export.seq += 1;
    var seq = ana.export.seq;
    paintExportRow(ctx, ana.exportHost);
    var query = anaQuery();
    query.format = format;
    ctx.call('api/analytics/export', null, {
      method: 'GET', query: query, response: 'text', accept: EXPORT_ACCEPT
    }).then(function (res) {
      if (seq !== ana.export.seq) return;
      ana.export.busy = '';
      var head = res && res.headers ? res.headers : null;
      var served = ctx.header(head, 'x-tracelens-export-format');
      var record = {
        requested: format,
        offline: false,
        ok: !!(res && res.ok),
        status: res && typeof res.status === 'number' ? res.status : 0,
        code: res && res.code ? String(res.code) : '',
        message: res && res.message ? String(res.message) : '',
        served: served,
        fallback: ctx.header(head, 'x-tracelens-export-fallback'),
        sha: ctx.header(head, 'x-tracelens-export-sha256'),
        note: ctx.header(head, 'x-tracelens-export-note'),
        preAggregated: ctx.header(head, 'x-tracelens-export-pre-aggregated'),
        scanned: ctx.header(head, 'x-tracelens-export-scanned'),
        filename: filenameFromDisposition(ctx.header(head, 'content-disposition')),
        contentType: ctx.header(head, 'content-type'),
        bytes: res && typeof res.text === 'string' ? res.text.length : 0,
        text: res && typeof res.text === 'string' ? res.text : '',
        saved: false,
        opened: null
      };
      if (record.ok) {
        /* A real PDF is bytes the text channel cannot survive: the panel hands
         * the browser the server URL and says so. CSV / printable HTML ship as
         * text, so the panel saves exactly those served characters. */
        if (record.served === 'pdf') record.saved = ctx.download(exportUrl(format), record.filename);
        else record.saved = saveArtifact(ctx, record);
      }
      ana.export.last = record;
      paintExportRow(ctx, ana.exportHost);
      if (!record.ok) {
        ctx.toast('analytics.export.failed',
                  { code: record.code || 'HTTP_STATUS',
                    message: record.message || String(record.status || '') }, 'warn');
      } else if (record.fallback) {
        ctx.toast('analytics.export.toast_fallback',
                  { requested: format.toUpperCase(), format: (record.served || '—').toUpperCase() },
                  'warn');
      } else {
        ctx.toast('analytics.export.toast',
                  { format: (record.served || '—').toUpperCase() }, 'info');
      }
    });
  }

  function openPrintView(ctx, record) {
    var url = exportUrl(record.requested);
    var opened = null;
    try {
      if (typeof global.open === 'function') opened = global.open(url, '_blank', 'noopener');
    } catch (e) { opened = null; }
    record.opened = !!opened;
    paintExportRow(ctx, ana.exportHost);
  }

  function paintExportRow(ctx, host) {
    if (!host) return;
    ctx.clear(host);
    if (offlineNow()) {
      noteRow(ctx, host, 'analytics.export.offline', null, 'ext-note--warn');
      return;
    }
    if (!ctx.can('audit.export')) {
      noteRow(ctx, host, 'analytics.export.denied', null, 'ext-note--warn');
      return;
    }
    var buttons = ctx.add(host, ctx.el('div', { class: 'ext-ana__export-buttons' }));
    /* exactly two buttons: the frozen format enum is csv|pdf, `format=html`
     * answers 400 ANALYTICS_FORMAT_INVALID, so no HTML option is offered. */
    EXPORT_FORMATS.forEach(function (format) {
      var button = ctx.add(buttons, ctx.el('button', {
        class: 'trust-btn',
        attrs: { type: 'button', 'data-export-format': format }
      }));
      ctx.textNode(button, 'analytics.export.' + format);
      button.addEventListener('click', function () { requestExport(ctx, format); });
      if (ana.export.busy) button.setAttribute('disabled', 'true');
    });
    if (ana.export.busy) {
      noteRow(ctx, host, 'analytics.export.busy', { format: ana.export.busy.toUpperCase() });
    }
    paintExportResult(ctx, host);
  }

  function paintExportResult(ctx, host) {
    var record = ana.export.last;
    if (!record) return;
    var box = ctx.add(host, ctx.el('div', { class: 'ext-ana__export-result' }));
    box.setAttribute('data-export-format', record.served || 'unknown');
    box.setAttribute('data-export-fallback', record.fallback || '');
    box.setAttribute('data-export-sha', record.sha || '');
    box.setAttribute('data-export-saved', record.saved ? 'true' : 'false');
    box.setAttribute('data-export-requested', record.requested || '');

    if (record.offline) {
      noteRow(ctx, box, 'analytics.export.offline', null, 'ext-note--warn');
      return;
    }
    if (!record.ok) {
      /* 403 is the permission story, everything else is the code + the server's
       * own sentence: the unified error body is what the page shows. */
      if (record.status === 403) {
        noteRow(ctx, box, 'analytics.export.denied', null, 'ext-note--warn');
      } else {
        noteRow(ctx, box, 'analytics.export.failed',
                { code: record.code || 'HTTP_STATUS',
                  message: record.message || String(record.status || '') }, 'ext-note--warn');
      }
      return;
    }
    if (!record.served) {
      noteRow(ctx, box, 'analytics.export.format_missing', null, 'ext-note--warn');
    }
    noteRow(ctx, box, 'analytics.export.result',
            { filename: record.filename || '\u2014', format: (record.served || '\u2014').toUpperCase() });
    /* 降级不是成功：原因码原样写在句子边上（PM-VERDICT-R6-3 §2 口径） */
    if (record.fallback) {
      noteRow(ctx, box, 'analytics.export.fallback',
              { requested: (record.requested || '').toUpperCase(),
                format: (record.served || '\u2014').toUpperCase(),
                reason: record.fallback }, 'ext-note--warn');
    }
    if (record.sha) noteRow(ctx, box, 'analytics.export.sha', { sha: record.sha });
    else noteRow(ctx, box, 'analytics.export.sha_missing', null, 'ext-note--warn');
    if (record.served === 'csv' && record.saved) noteRow(ctx, box, 'analytics.export.bytes');
    if (record.served === 'pdf' && record.saved) {
      noteRow(ctx, box, 'analytics.export.pdf_direct', null, 'ext-note--warn');
    }
    if (record.served === 'html') {
      noteRow(ctx, box, 'analytics.export.print');
      var print = ctx.add(box, ctx.el('button', {
        class: 'trust-btn', attrs: { type: 'button', 'data-export-print': 'true' }
      }));
      ctx.textNode(print, 'analytics.export.print_open');
      print.addEventListener('click', function () { openPrintView(ctx, record); });
      if (record.opened === true) noteRow(ctx, box, 'analytics.export.printed');
      if (record.opened === false) {
        noteRow(ctx, box, 'analytics.export.print_unavailable', null, 'ext-note--warn');
      }
    }
    if (record.note) noteRow(ctx, box, 'analytics.export.server_note', { note: record.note });
    if (record.preAggregated) {
      noteRow(ctx, box, 'analytics.export.plane_echo',
              { value: record.preAggregated, scanned: record.scanned || '\u2014' });
    }
    if (!record.saved) noteRow(ctx, box, 'analytics.export.not_saved', null, 'ext-note--warn');
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
    /* R6-4: the slow label eats a measured number, so the clock starts here and
     * stops when this one read comes back. */
    ana.startedAt = typeof Date.now === 'function' ? Date.now() : 0;
    ana.elapsedMs = 0;
    ctx.renderLoading(host);
    ctx.get('api/analytics/dashboard', anaQuery()).then(function (res) {
      if (seq !== ana.seq) return;
      ana.loading = false;
      var now = typeof Date.now === 'function' ? Date.now() : 0;
      ana.elapsedMs = ana.startedAt && now >= ana.startedAt ? now - ana.startedAt : 0;
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
    /* R6-4: the buttons and their honest answer are painted together, so a
     * degradation shows up next to the button that caused it. */
    var exportHost = ctx.add(exportRow, ctx.el('div', { class: 'ext-ana__export-body' }));
    ana.exportHost = exportHost;
    exportReset();
    paintExportRow(ctx, exportHost);

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

    /* R6-4: the numbers are read only after the page has said where they came
     * from, so the data plane strip is painted first, above the cards. */
    paintDataPlane(ctx, host, data);
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
    paintedLang: '',
    /* R7-2：服务端回读的真值 + 写侧挂载的界面状态 */
    threshold: null,
    cooldownDays: null,
    durable: true,
    featureBusy: false,
    featureMsg: null,
    featureMsgHost: null,
    featureButton: null,
    snoozed: {},
    toastNode: null,
    toastTimer: 0
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

  /* ---------------------------------------------------------------- *
   * 5b. R7-2：开关走写侧挂载（POST api/suggestions），界面不自己编状态
   * ---------------------------------------------------------------- */

  function resProblem(res) {
    var data = isObj(res) && isObj(res.data) ? res.data : {};
    var code = str(data.code) || String((res && res.status) || 'UNKNOWN');
    var message = str(data.message) || '';
    return { code: code, message: message };
  }

  function paintSwitch(ctx) {
    var button = sug.featureButton;
    if (!button) return;
    button.setAttribute('aria-checked', sug.enabled ? 'true' : 'false');
    if (button.classList) {
      if (sug.enabled) button.classList.add('is-on');
      else button.classList.remove('is-on');
    }
    // 开关是服务级：只有 settings.write 能拨（viewer/editor 看到禁用 + 说明，不是假装可点）
    if (ctx.can('settings.write')) button.removeAttribute('disabled');
    else button.setAttribute('disabled', 'disabled');
    button.setAttribute('aria-busy', sug.featureBusy ? 'true' : 'false');
  }

  function paintFeatureMessage(ctx) {
    var host = sug.featureMsgHost;
    if (!host) return;
    ctx.clear(host);
    var msg = sug.featureMsg;
    if (!msg) {
      // 没有操作反馈时，这里显示诚实说明：开关不落盘（服务端 durable=false 时）
      if (sug.durable === false) {
        var note = ctx.textNode(host, 'suggestions.feature.durable', null, 'p');
        note.className = 'ext-note';
      }
      return;
    }
    var box = ctx.add(host, ctx.el('p', { class: 'ext-note' }));
    if (msg.kind === 'warn') box.className = 'ext-note ext-note--warn';
    box.textContent = ctx.t(msg.key, msg.params || null);
    ctx.stamp(box, msg.key);
  }

  function saveFeatureSwitch(ctx, next) {
    if (!ctx.can('settings.write')) {
      sug.featureMsg = { kind: 'warn', key: 'suggestions.feature.denied' };
      paintFeatureMessage(ctx);
      paintSwitch(ctx);
      return;
    }
    if (sug.featureBusy) return;
    if (offlineNow()) {
      offlineState(ctx, sug.featureMsgHost, function () { saveFeatureSwitch(ctx, next); });
      return;
    }
    sug.featureBusy = true;
    paintSwitch(ctx);
    ctx.post('api/suggestions', { enabled: next }).then(function (res) {
      sug.featureBusy = false;
      var payload = isObj(res) && isObj(res.data) ? res.data : {};
      if (res.ok && typeof payload.enabled === 'boolean') {
        sug.enabled = payload.enabled;                    // 服务端回读，不用点下去的值
        sug.durable = payload.durable !== false;
        saveEnabled(sug.enabled);                         // 只做本地镜像，不当真值
        sug.featureMsg = {
          kind: 'ok',
          key: 'suggestions.feature.saved',
          params: { state: sug.enabled ? 'true' : 'false' }
        };
        paintFeatureState(ctx);
        paintSwitch(ctx);
        paintFeatureMessage(ctx);
        if (sug.enabled) loadSuggestions(ctx);
        else {
          sug.items = null;
          if (sug.itemsHost) ctx.renderEmpty(sug.itemsHost, 'suggestions.feature.off');
        }
        return;
      }
      var problem = resProblem(res);
      sug.featureMsg = {
        kind: 'warn',
        key: 'suggestions.feature.failed',
        params: { code: problem.code, message: problem.message || '\u2014' }
      };
      paintSwitch(ctx);
      paintFeatureMessage(ctx);
      loadSuggestions(ctx);                               // 写失败就回读服务端，界面不猜
    }, function () {
      sug.featureBusy = false;
      sug.featureMsg = {
        kind: 'warn',
        key: 'suggestions.feature.failed',
        params: { code: 'NETWORK', message: '\u2014' }
      };
      paintSwitch(ctx);
      paintFeatureMessage(ctx);
    });
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
      // 开关真值以服务端回读为准（T28 的 enabled 字段就是那个服务级开关的对外视图）；
      // localStorage 只是首帧的本地镜像，绝不让它替服务端说话。
      var payload = isObj(res.data) ? res.data : {};
      if (typeof payload.enabled === 'boolean') {
        sug.enabled = payload.enabled;
        saveEnabled(sug.enabled);
        paintFeatureState(ctx);
        paintSwitch(ctx);
      }
      if (typeof payload.threshold === 'number') sug.threshold = payload.threshold;
      if (typeof payload.cooldown_days === 'number') sug.cooldownDays = payload.cooldown_days;
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
      // 冷却天数以服务端回读为准（T28 payload 的 cooldown_days），界面不写死 7
      var coolingDays = typeof sug.cooldownDays === 'number' ? sug.cooldownDays : 7;
      if (verb === 'accept') ctx.toast('suggestions.toast.accepted', null, 'info');
      else ctx.toast('suggestions.toast.dismissed', { days: coolingDays }, 'info');
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
    sug.featureButton = switchButton;
    var knob = ctx.add(switchButton, ctx.el('span', { class: 'ext-switch__knob', attrs: { 'aria-hidden': 'true' } }));
    knob.textContent = '';
    var switchLabel = ctx.add(feature, ctx.el('span', { class: 'ext-switch__label', text: ctx.t('suggestions.feature.switch') }));
    ctx.stamp(switchLabel, 'suggestions.feature.switch');
    var statusNode = ctx.add(feature, ctx.el('span', { class: 'ext-switch__state' }));
    sug.statusNode = statusNode;
    paintFeatureState(ctx);
    var featureNote = ctx.textNode(feature, 'suggestions.feature.note', null, 'p');
    featureNote.className = 'ext-note';
    var featureMsgHost = ctx.add(feature, ctx.el('div', { class: 'ext-sug__feature-msg' }));
    sug.featureMsgHost = featureMsgHost;
    paintFeatureMessage(ctx);

    var feedbackHost = ctx.add(panel, ctx.el('div', { class: 'ext-sug__feedback' }));
    sug.feedbackHost = feedbackHost;

    var listSection = ctx.add(panel, ctx.el('section', { class: 'ext-panel__section' }));
    sectionTitle(ctx, listSection, 'suggestions.list.title', 'h4');
    var pollNote = ctx.textNode(listSection, 'suggestions.poll.note', null, 'p');
    pollNote.className = 'ext-note';
    var timingNote = ctx.textNode(listSection, 'suggestions.poll.timing', null, 'p');
    timingNote.className = 'ext-note';
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
    // 删除 / 清空：后端未提供端点，界面如实标注「未提供」，不摆假按钮（任务 §2.2 的诚实口径）
    var unsupported = ctx.textNode(prefSection, 'suggestions.pref.unsupported', null, 'p');
    unsupported.className = 'ext-note ext-note--warn';
    sectionTitle(ctx, prefSection, 'suggestions.pref.add.title', 'h5');
    suggestionForm(ctx, prefSection);

    switchButton.addEventListener('click', function () {
      saveFeatureSwitch(ctx, !sug.enabled);
    });
    paintSwitch(ctx);

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
   * 6b. R7-2：三键提示卡（采纳 / 拒绝 / 稍后）——非模态、不抢焦点、不阻塞
   *
   * 为什么不用 ``ctx.toast``：core.js 那个是纯文本、单实例、5.2 秒自动收起的
   * 通知条，没有按钮位；任务 §2.1 要的是「采纳 / 拒绝 / 稍后」三个动作。
   * 这里在本模块内自建一个节点（自己的定时器、自己的类名前缀 ext-sug-toast*），
   * 不碰 core.js / core.css，也不加遮罩：页面其余部分照常可点（无模态阻塞）。
   * ---------------------------------------------------------------- */

  function closeDecisionToast() {
    if (sug.toastTimer) {
      clearTimeout(sug.toastTimer);
      sug.toastTimer = 0;
    }
    if (sug.toastNode && sug.toastNode.parentNode) {
      sug.toastNode.parentNode.removeChild(sug.toastNode);
    }
    sug.toastNode = null;
  }

  function toastNote(ctx, node, key, params) {
    var line = node.querySelector ? node.querySelector('.ext-sug-toast__note') : null;
    if (!line) return;
    ctx.clear(line);
    line.textContent = ctx.t(key, params || null);
    ctx.stamp(line, key);
  }

  function decideFromToast(ctx, id, verb, node) {
    var row = node.querySelector ? node.querySelector('.ext-sug-toast__row') : null;
    var buttons = [];
    if (row && row.children) {
      for (var i = 0; i < row.children.length; i += 1) buttons.push(row.children[i]);
    }
    if (offlineNow()) {
      toastNote(ctx, node, 'suggestions.toast.failed', {
        code: 'OFFLINE', message: '\u2014'
      });
      return;
    }
    buttons.forEach(function (b) { if (b && b.setAttribute) b.setAttribute('disabled', 'disabled'); });
    ctx.post('api/suggestions/' + enc(id) + '/' + verb, {}).then(function (res) {
      if (!res.ok) {
        // 如实反馈：把服务端的码与人话原因显示出来，按钮恢复，绝不假装成功
        var problem = resProblem(res);
        toastNote(ctx, node, 'suggestions.toast.failed', {
          code: problem.code, message: problem.message || '\u2014'
        });
        buttons.forEach(function (b) {
          if (b && b.removeAttribute && b.getAttribute('data-locked') !== 'true') {
            b.removeAttribute('disabled');
          }
        });
        return;
      }
      var days = typeof sug.cooldownDays === 'number' ? sug.cooldownDays : 7;
      sug.shown[id] = true;
      delete sug.snoozed[id];
      ctx.toast(verb === 'accept' ? 'suggestions.toast.accepted' : 'suggestions.toast.dismissed',
                verb === 'accept' ? null : { days: days }, 'info');
      closeDecisionToast();
      loadSuggestions(ctx);
      loadPreferences(ctx);
    }, function () {
      toastNote(ctx, node, 'suggestions.toast.failed', { code: 'NETWORK', message: '\u2014' });
      buttons.forEach(function (b) {
        if (b && b.removeAttribute && b.getAttribute('data-locked') !== 'true') {
          b.removeAttribute('disabled');
        }
      });
    });
  }

  function decisionToast(ctx, item) {
    var doc = typeof document !== 'undefined' ? document : null;
    if (!doc || !doc.body) return;
    var id = str(pick(item, ['id', 'suggestion_id']));
    if (!id) return;
    closeDecisionToast();
    var node = ctx.el('div', {
      class: 'ext-sug-toast',
      attrs: { role: 'status', 'aria-live': 'polite', 'aria-modal': 'false' }
    });
    var head = ctx.add(node, ctx.el('p', { class: 'ext-sug-toast__text' }));
    head.textContent = ctx.t('suggestions.toast.new.one', {
      tool: str(pick(item, ['tool'])) || '\u2014',
      param: str(pick(item, ['param', 'parameter'])) || '\u2014'
    });
    ctx.stamp(head, 'suggestions.toast.new.one');
    var row = ctx.add(node, ctx.el('div', { class: 'ext-sug-toast__row' }));
    var accept = ctx.add(row, ctx.el('button', {
      class: 'trust-btn trust-btn--primary', attrs: { type: 'button' }
    }));
    ctx.textNode(accept, 'suggestions.action.accept');
    var dismiss = ctx.add(row, ctx.el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    ctx.textNode(dismiss, 'suggestions.action.dismiss');
    var later = ctx.add(row, ctx.el('button', { class: 'trust-btn', attrs: { type: 'button' } }));
    ctx.textNode(later, 'suggestions.action.later');
    ctx.add(node, ctx.el('p', { class: 'ext-sug-toast__note' }));
    var canDecide = ctx.can('intervene.decide');
    if (!canDecide) {
      // 如实：这一格缺权限就禁用两个写侧按钮，不摆出可点的样子
      accept.setAttribute('disabled', 'disabled');
      dismiss.setAttribute('disabled', 'disabled');
      toastNote(ctx, node, 'suggestions.action.denied');
    }
    accept.addEventListener('click', function () { decideFromToast(ctx, id, 'accept', node); });
    dismiss.addEventListener('click', function () { decideFromToast(ctx, id, 'dismiss', node); });
    later.addEventListener('click', function () {
      // [稍后] = 本次页面会话不再弹同一条（刷新后恢复提示：如实写在 i18n 文案里）
      sug.snoozed[id] = true;
      closeDecisionToast();
      ctx.toast('suggestions.toast.later.done', null, 'info');
    });
    doc.body.appendChild(node);
    sug.toastNode = node;
    sug.toastTimer = setTimeout(closeDecisionToast, TOAST_MS);
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
      var payload = isObj(res.data) ? res.data : {};
      if (typeof payload.enabled === 'boolean' && payload.enabled !== sug.enabled) {
        sug.enabled = payload.enabled;
        saveEnabled(sug.enabled);
        paintFeatureState(ctx);
        paintSwitch(ctx);
      }
      var list = suggestionRows(res.data);
      var fresh = [];
      list.forEach(function (item) {
        var id = str(pick(item, ['id', 'suggestion_id']));
        if (!id || sug.shown[id] || sug.snoozed[id]) return;
        sug.shown[id] = true;
        if (sug.baselineDone) fresh.push(item);
      });
      sug.baselineDone = true;
      if (!fresh.length) return;
      if (fresh.length === 1) {
        // 一条：给能直接决定的三键提示卡（采纳 / 拒绝 / 稍后）
        decisionToast(ctx, fresh[0]);
      } else {
        // 多条：不堆提示卡，只给一条文本通知，逐条决定留在面板里（不阻塞界面）
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
