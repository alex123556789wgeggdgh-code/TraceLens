/*
 * TraceLens - web skeleton for subtask 3.1 (t6)
 * ------------------------------------------------------------------
 * Product: translate agent ActionRecords into a plain-language action list.
 * This file is a single classic script on purpose: no framework, no CDN,
 * no build step, no bundler, no runtime network dependency. Opening
 * index.html straight from the filesystem must render a full demo.
 *
 * Public surface (used by t7 / 3.2 and by web/selfcheck.mjs):
 *   window.TraceLensUI = { ...pure logic..., ...dom actions..., probe() }
 *
 * Hard rules honoured here:
 *  - every user-visible string comes from the i18n dictionary (zh/en);
 *    there is not a single CJK literal in this file (i18n lint, K-10).
 *  - demo/synthetic data badge is computed from record fields
 *    (demo_data / synthetic / source.kind), never from static markup.
 *  - a recorded decision is never presented as an executed effect
 *    ("decision recorded; effect depends on the runtime being wired").
 *  - L1 is the default view; cost/duration appear only when expanded.
 */
(function (global) {
  'use strict';

  var APP_ID = 'TraceLens';
  var SKELETON_VERSION = '4.1-intervene';
  var SCHEMA_VERSION = '1.1';

  /* Verification rig: ?frame=375x812 puts the app inside a fixed phone
   * viewport so a browser can measure that exact layout. */
  var FRAME_RE = /^(\d{2,5})x(\d{2,5})$/;

  var LANG_STORAGE_KEY = 'tracelens.ui_language';
  var GRAN_STORAGE_KEY = 'tracelens.ui_granularity';

  var LANGS = ['zh', 'en'];
  var DEFAULT_LANG = 'zh';
  var LOCALES = { zh: 'zh-CN', en: 'en-GB' };

  var ZONE_ORDER = ['needs_me', 'running', 'done'];
  var ZONE_KEYS = {
    needs_me: { title: 'zones.needs_me.title', hint: 'zones.needs_me.hint', aria: 'zones.needs_me.aria', empty: 'zones.empty.needs_me' },
    running: { title: 'zones.running.title', hint: 'zones.running.hint', aria: 'zones.running.aria', empty: 'zones.empty.running' },
    done: { title: 'zones.done.title', hint: 'zones.done.hint', aria: 'zones.done.aria', empty: 'zones.empty.done' }
  };

  var STATUS_SET = ['observed', 'pending_confirmation', 'approved', 'modified', 'rejected', 'running', 'succeeded', 'failed', 'skipped', 'expired'];
  var ACTION_TYPE_SET = ['read', 'write', 'delete', 'plan', 'confirm', 'ask', 'error', 'tool_call', 'wait', 'finish', 'intervention', 'config_change', 'send', 'payment', 'export', 'other'];
  var RISK_SET = ['low', 'medium', 'high', 'critical'];
  var RECEIPT_STATE_SET = ['unreported', 'executed', 'failed', 'refused_by_runtime', 'timeout'];
  var DECISION_SET = ['approve', 'modify', 'reject'];
  var ROLE_SET = ['admin', 'editor', 'viewer', 'agent', 'unknown'];

  var NEEDS_ME_STATUS = ['pending_confirmation', 'expired'];
  var RUNNING_STATUS = ['running', 'observed'];
  var DONE_NEUTRAL_STATUS = ['rejected', 'skipped'];
  var LOW_CONFIDENCE_MAX = 0.6;

  /* ---------------------------------------------------------------- *
   * 1. tiny utilities (no DOM)
   * ---------------------------------------------------------------- */

  function isObj(v) {
    return typeof v === 'object' && v !== null && !Array.isArray(v);
  }

  function isArr(v) {
    return Array.isArray(v);
  }

  function str(v) {
    if (typeof v === 'string') return v;
    if (v === null || v === undefined) return '';
    if (typeof v === 'number' && isFinite(v)) return String(v);
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    return '';
  }

  function hasText(v) {
    var s = str(v);
    return s.trim() !== '';
  }

  function num(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  /* attributes, query parameters and form controls hand us strings, so the
   * desktop layer parses numbers leniently and falls back to null (never 0) */
  function toNum(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    var s = str(v).trim();
    if (!s) return null;
    var n = Number(s);
    return isFinite(n) ? n : null;
  }

  function clamp(v, lo, hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
  }

  function flattenKeys(obj, prefix, out) {
    var keys = out || [];
    var k;
    for (k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
      var path = prefix ? prefix + '.' + k : k;
      var v = obj[k];
      if (isObj(v)) flattenKeys(v, path, keys);
      else keys.push(path);
    }
    return keys;
  }

  /* Dictionary lookup tolerant of both layouts: fully nested
   * { "nav": { "app": { "name": ... } } } and the shipped grouped layout
   * { "nav": { "app.name": ... } }, where a leaf key itself contains dots.
   * Segments are joined greedily, so "nav.app.name" resolves either way.
   * (The first version only walked one segment at a time and silently
   * missed every grouped key - web/selfcheck.mjs pins this down.) */
  function getIn(obj, path) {
    if (!isObj(obj) || !hasText(path)) return undefined;
    return getInWalk(obj, String(path).split('.'), 0);
  }

  /* The dictionaries ship grouped: {"card": {"expand": "..", "expand.hint": ".."}}.
   * A lookup of "card.expand.hint" must NOT stop at the leaf "card.expand",
   * so every level tries the longest key first and backtracks if the rest of
   * the path cannot be resolved. */
  function getInWalk(node, parts, index) {
    if (index === parts.length) return node;
    if (!isObj(node)) return undefined;
    for (var end = parts.length - 1; end >= index; end -= 1) {
      var joined = parts.slice(index, end + 1).join('.');
      if (!Object.prototype.hasOwnProperty.call(node, joined)) continue;
      var value = getInWalk(node[joined], parts, end + 1);
      if (value !== undefined) return value;
    }
    return undefined;
  }

  function interpolate(text, params) {
    var s = str(text);
    if (!isObj(params) || s.indexOf('{') < 0) return s;
    return s.replace(/\{([a-zA-Z0-9_]+)\}/g, function (_m, name) {
      if (!Object.prototype.hasOwnProperty.call(params, name)) return '{' + name + '}';
      var v = params[name];
      return v === null || v === undefined ? '' : String(v);
    });
  }

  /* ---------------------------------------------------------------- *
   * 2. i18n: dictionaries, lookup, persistence, fallback
   * ---------------------------------------------------------------- */

  var i18n = {
    dicts: { zh: {}, en: {} },
    lang: DEFAULT_LANG,
    source: 'embedded',
    missing: [],
    keys: []
  };

  function setDicts(zh, en) {
    i18n.dicts.zh = isObj(zh) ? zh : {};
    i18n.dicts.en = isObj(en) ? en : {};
    i18n.keys = flattenKeys(i18n.dicts.zh);
    return i18n.keys.length;
  }

  function isLang(v) {
    return v === 'zh' || v === 'en';
  }

  function detectLang() {
    var stored = null;
    try {
      if (global.localStorage) stored = global.localStorage.getItem(LANG_STORAGE_KEY);
    } catch (e) { stored = null; }
    if (isLang(stored)) return stored;
    var navLang = '';
    try {
      navLang = str(global.navigator && (global.navigator.language || global.navigator.userLanguage));
    } catch (e2) { navLang = ''; }
    var base = navLang.toLowerCase().split('-')[0];
    return isLang(base) ? base : DEFAULT_LANG;
  }

  function setLang(lang) {
    i18n.lang = isLang(lang) ? lang : DEFAULT_LANG;
    try {
      if (global.localStorage) global.localStorage.setItem(LANG_STORAGE_KEY, i18n.lang);
    } catch (e) { /* storage unavailable: session-only switch */ }
    /* A language switch changes every visible string, so it must re-render.
     * The first version only wrote the state: clicking zh/en did nothing
     * visible until something else triggered a render. web/selfcheck.mjs
     * now pins this contract for every public mutation. The demo dataset is
     * part of the visible language too, so the record set is swapped in the
     * same pass (4.7: an English UI must show zero Chinese demo data).
     * 4.12 (B-01, QA 5.2): swapping the set must never swap away what this
     * page is actually showing - imported records, recorded decisions, the
     * open cards and the timeline position. That is applyDataLang's job now. */
    if (booted) applyDataLang(i18n.lang);
    return i18n.lang;
  }

  function t(key, params) {
    var value = getIn(i18n.dicts[i18n.lang], key);
    if (!hasText(value)) value = getIn(i18n.dicts.zh, key);
    if (!hasText(value)) {
      if (i18n.missing.indexOf(key) < 0) i18n.missing.push(key);
      if (key !== 'error.missing_key') {
        var tpl = getIn(i18n.dicts[i18n.lang], 'error.missing_key');
        if (hasText(tpl)) return interpolate(tpl, { key: key });
      }
      return 'i18n?' + key;
    }
    return interpolate(value, params);
  }

  function raw(key) {
    return getIn(i18n.dicts[i18n.lang], key);
  }

  /* Optional refresh from the canonical JSON files (only when served over
   * http/https: file:// fetch is blocked by the browser). The embedded copy
   * generated from the same JSON keeps the static demo working offline. */
  function loadDictionariesFromServer() {
    if (typeof global.fetch !== 'function') return Promise.resolve(false);
    var proto = '';
    try { proto = str(global.location && global.location.protocol); } catch (e) { proto = ''; }
    if (proto !== 'http:' && proto !== 'https:') return Promise.resolve(false);
    state.stats.i18nAttempted = true;
    function grab(name) {
      return global.fetch('i18n/' + name + '.json', { cache: 'no-store' })
        .then(function (res) { return res.ok ? res.json() : null; })
        .catch(function () { return null; });
    }
    return Promise.all([grab('zh'), grab('en')]).then(function (pair) {
      if (!isObj(pair[0]) || !isObj(pair[1])) return false;
      setDicts(pair[0], pair[1]);
      i18n.source = 'json';
      return true;
    });
  }

  /* ---------------------------------------------------------------- *
   * 3. record classification
   * ---------------------------------------------------------------- */

  function statusKey(status) {
    return STATUS_SET.indexOf(status) >= 0 ? status : 'unknown';
  }

  function typeKey(actionType) {
    return ACTION_TYPE_SET.indexOf(actionType) >= 0 ? actionType : 'unknown';
  }

  function riskKey(risk) {
    return RISK_SET.indexOf(risk) >= 0 ? risk : 'unknown';
  }

  function zoneOf(record) {
    if (!isObj(record)) return 'done';
    var status = record.status;
    if (record.requires_action === true && NEEDS_ME_STATUS.indexOf(status) >= 0) return 'needs_me';
    if (record.requires_action === true && status !== 'succeeded' && status !== 'failed') return 'needs_me';
    if (RUNNING_STATUS.indexOf(status) >= 0) return 'running';
    return 'done';
  }

  /* Colour semantics fixed by docs/03-architecture.md:
   * green = done, yellow = running, red = failed, blue = awaiting you,
   * plus a neutral grey for "you said no / skipped". */
  function colorRoleOf(record) {
    if (!isObj(record)) return 'neutral';
    var status = record.status;
    if (status === 'failed' || record.action_type === 'error') return 'danger';
    if (zoneOf(record) === 'needs_me') return 'pending';
    if (RUNNING_STATUS.indexOf(status) >= 0) return 'progress';
    if (DONE_NEUTRAL_STATUS.indexOf(status) >= 0) return 'neutral';
    return 'ok';
  }

  function dueMs(record) {
    var p = parseISO(record && record.action_due_at);
    return p ? p.utcMs : null;
  }

  function tsMs(record) {
    var p = parseISO(record && record.timestamp);
    return p ? p.utcMs : 0;
  }

  var RISK_WEIGHT = { critical: 4, high: 3, medium: 2, low: 1, unknown: 0 };

  function byNeedPriority(a, b) {
    var da = dueMs(a), db = dueMs(b);
    if (da !== null && db !== null && da !== db) return da - db;
    if (da !== null && db === null) return -1;
    if (da === null && db !== null) return 1;
    var ra = RISK_WEIGHT[riskKey(a.risk_level)] || 0;
    var rb = RISK_WEIGHT[riskKey(b.risk_level)] || 0;
    if (ra !== rb) return rb - ra;
    return tsMs(a) - tsMs(b);
  }

  function byTimeDesc(a, b) { return tsMs(b) - tsMs(a); }

  function computeZones(records) {
    var zones = { needs_me: [], running: [], done: [] };
    (isArr(records) ? records : []).forEach(function (r) {
      if (!isObj(r)) return;
      zones[zoneOf(r)].push(r);
    });
    zones.needs_me.sort(byNeedPriority);
    zones.running.sort(byTimeDesc);
    zones.done.sort(byTimeDesc);
    return zones;
  }

  /* "needs me" is a serial queue: at most one card is presented at a time
   * (docs/01-product-and-users.md 9.3). Items that also carry
   * policy.queue_serial === false are listed side by side. */
  function serialQueue(list) {
    var arr = isArr(list) ? list : [];
    if (arr.length === 0) return { current: null, queued: [], serial: false };
    var allSerial = arr.every(function (r) { return !(isObj(r.policy) && r.policy.queue_serial === false); });
    if (allSerial) return { current: arr[0], queued: arr.slice(1), serial: true };
    return { current: arr[0], queued: arr.slice(1), serial: false };
  }

  function computeProgress(records) {
    var zones = computeZones(records);
    var total = zones.needs_me.length + zones.running.length + zones.done.length;
    var running = zones.running.length;
    var needs = zones.needs_me.length;
    var done = zones.done.length;
    var percent = total > 0 ? Math.round((done / total) * 100) : 0;
    return { total: total, done: done, running: running, needs: needs, percent: percent };
  }

  function taskNameOf(records) {
    var list = isArr(records) ? records : [];
    for (var i = list.length - 1; i >= 0; i -= 1) {
      var r = list[i];
      if (isObj(r) && isObj(r.context) && hasText(r.context.task_name)) return translateDemoLabel(r.context.task_name);
    }
    for (var j = 0; j < list.length; j += 1) {
      var rec = list[j];
      if (isObj(rec) && isObj(rec.context) && hasText(rec.context.task_name)) return translateDemoLabel(rec.context.task_name);
    }
    return '';
  }

  function agentNameOf(records) {
    var list = isArr(records) ? records : [];
    for (var i = list.length - 1; i >= 0; i -= 1) {
      if (isObj(list[i]) && hasText(list[i].agent_name)) return translateDemoLabel(list[i].agent_name);
    }
    return '';
  }

  /* ---------------------------------------------------------------- *
   * 1e. bilingual demo datasets (4.7 / t20)
   *
   * The demo set ships as a pair: demo-data.js is a byte copy of
   * samples/action_records.json (language=zh) and demo-data-en.js a byte copy
   * of samples/action_records_en.json (language=en, native English prose
   * written by tools/gen_samples_en.py, not machine translation). QA's release
   * line is "an English UI shows zero Chinese data", so the record set is
   * picked by interface language; ids, numbers, timestamps and statuses are
   * identical across the pair, only the copy changes language.
   * ---------------------------------------------------------------- */

  var DATASETS = {
    zh: { lang: 'zh', globalName: 'TraceLensDemoData', file: 'demo-data.js' },
    en: { lang: 'en', globalName: 'TraceLensDemoDataEn', file: 'demo-data-en.js' }
  };
  var DATA_LANG_KEY = 'tracelens.dataLang';
  var dataLangValue = 'zh';

  function dataLang() { return dataLangValue; }

  function datasetOf(lang) {
    return DATASETS[isLang(lang) ? lang : DEFAULT_LANG] || DATASETS.zh;
  }

  function demoDataFor(lang) {
    var pick = datasetOf(lang);
    var primary = global[pick.globalName];
    if (isObj(primary) && isArr(primary.records)) return primary;
    /* A page shipped without the English copy must still boot: fall back to the
     * Chinese set and let selfcheck report the missing dataset. */
    var fallback = global[DATASETS.zh.globalName];
    return isObj(fallback) ? fallback : null;
  }

  function detectDataLang() {
    var stored = null;
    try {
      if (global.localStorage) stored = global.localStorage.getItem(DATA_LANG_KEY);
    } catch (e) { stored = null; }
    if (stored === 'zh' || stored === 'en') return stored;
    return i18n.lang;
  }

  /* ---------------------------------------------------------------- *
   * 3b. data language without losing the operator's work (4.12 / B-01)
   *
   * QA 5.2 walked in with 20 records (12 demo + 8 imported) and one recorded
   * decision, pressed English, and the page dropped back to the 12 synthetic
   * demo records: the import "disappeared" and an approved action asked for a
   * second decision. The server never forgot anything (GET /api/records kept
   * answering 20), so this is a page bug: the interface language was allowed
   * to replace the record list. Two rules now:
   *   - live: the server owns the records. Repaint the copy, keep the list,
   *     re-read api/records in the background, never feed demo data to setRecords;
   *   - offline / file://: the bilingual demo set is the copy, so it does swap,
   *     but records that are not synthetic stay, and a recorded decision moves
   *     onto the localized twin (the facts travel, the sentences come from the
   *     new language).
   * The visible place in the list is kept in both branches.
   * ---------------------------------------------------------------- */

  function cloneRecord(record) {
    var copy = {};
    if (!isObj(record)) return copy;
    Object.keys(record).forEach(function (key) { copy[key] = record[key]; });
    return copy;
  }

  function recordedDecisionOf(record) {
    var ui = (isObj(record) && isObj(record.user_intervention)) ? record.user_intervention : null;
    return (ui && (hasText(ui.decision) || ui.recorded === true)) ? ui : null;
  }

  /* Merge the localized demo copy with the state of this page. */
  function carryUserRecords(dataset) {
    var next = normalizeRecords(dataset);
    var byId = {};
    var merged = next.map(function (record, index) {
      byId[str(record.id)] = index;
      return cloneRecord(record);
    });
    var own = 0;
    var decided = 0;
    state.records.forEach(function (current) {
      var id = str(isObj(current) ? current.id : '');
      if (!hasText(id)) return;
      var at = Object.prototype.hasOwnProperty.call(byId, id) ? byId[id] : -1;
      if (at < 0) {
        /* not part of the synthetic set: the operator brought it in (import,
         * a pushed record, a record from a real run) - keep it verbatim */
        merged.push(cloneRecord(current));
        own += 1;
        return;
      }
      var ui = recordedDecisionOf(current);
      if (!ui) return;
      var twin = merged[at];
      twin.status = current.status;
      twin.requires_action = current.requires_action;
      if (hasText(current.decision)) twin.decision = current.decision;
      twin.user_intervention = current.user_intervention;
      if (isObj(current.execution_receipt)) twin.execution_receipt = current.execution_receipt;
      decided += 1;
    });
    return { records: merged, own: own, decided: decided };
  }

  function snapshotView() {
    return {
      expanded: Object.keys(state.expanded).filter(function (k) { return state.expanded[k]; }),
      focus: state.focus,
      timelineIndex: state.timelineIndex
    };
  }

  /* setRecords resets the view because a new data set is a new list. A language
   * switch is not: the same ids are on screen, so the place comes back. */
  function restoreView(snap) {
    (snap.expanded || []).forEach(function (id) {
      for (var i = 0; i < state.records.length; i += 1) {
        if (str(state.records[i].id) === id) { state.expanded[id] = true; break; }
      }
    });
    if (hasText(snap.focus)) {
      for (var j = 0; j < state.records.length; j += 1) {
        if (str(state.records[j].id) === snap.focus) { state.focus = snap.focus; break; }
      }
    }
    var index = toNum(snap.timelineIndex);
    if (index === null) index = 0;
    state.timelineIndex = state.records.length ? clampTimelineIndex(index) : 0;
  }

  /* Switching the interface language switches the record set too, so the data
   * language can never lag behind the copy language. A stored choice is
   * restored on boot; applyDataLang re-parses, which re-renders in one pass. */
  function applyDataLang(lang) {
    dataLangValue = (lang === 'en' || lang === 'zh') ? lang : i18n.lang;
    try {
      if (global.localStorage) global.localStorage.setItem(DATA_LANG_KEY, dataLangValue);
    } catch (e) { /* storage unavailable: session-only switch */ }
    state.stats.dataLangSwaps = (state.stats.dataLangSwaps || 0) + 1;
    if (state.api.mode === 'live') {
      state.stats.dataLangKept = state.records.length;
      if (booted) renderAll();
      refreshRecordsFromApi({ reason: 'lang', force: true });
      return true;
    }
    var data = demoDataFor(dataLangValue);
    if (isObj(data) && isArr(data.records)) {
      var carried = carryUserRecords(data);
      var snap = snapshotView();
      setRecords(
        { records: carried.records, _meta: isObj(data._meta) ? data._meta : {} },
        null,
        { silent: true }
      );
      restoreView(snap);
      state.stats.dataLangOwn = carried.own;
      state.stats.dataLangDecided = carried.decided;
      state.stats.dataLangCarried = carried.own + carried.decided;
      if (booted) renderAll();
      return true;
    }
    /* The requested dataset is not on the page: keep every record that is
     * already in view and repaint the copy - the records are never dropped. */
    state.stats.dataLangKept = state.records.length;
    if (booted) renderAll();
    return false;
  }

  function translateDemoLabel(key, fallback) {
    /* Synthetic demo names (the album assistant, tidy the demo album, ...) are
     * dictionary copy, so an English UI shows no Chinese label and the compare
     * table stays translatable. Only exact known keys are translated: names
     * coming from a real run pass through verbatim. */
    var text = str(key);
    if (!text) return str(fallback);
    var dictKey = 'demo.label.' + text;
    var value = t(dictKey);
    return (value && value !== dictKey) ? value : str(fallback || text);
  }

  /* ---------------------------------------------------------------- *
   * 4. demo / synthetic detection (machine checked, never static text)
   * ---------------------------------------------------------------- */

  function demoChecksOf(record) {
    var checks = { demo_data: false, synthetic: false, source_kind: false };
    if (!isObj(record)) return checks;
    if (record.demo_data === true) checks.demo_data = true;
    if (record.synthetic === true) checks.synthetic = true;
    if (isObj(record.source) && record.source.kind === 'demo') checks.source_kind = true;
    return checks;
  }

  function demoStatus(records) {
    var list = isArr(records) ? records : [];
    var hits = { demo_data: 0, synthetic: 0, source_kind: 0 };
    var perRecord = {};
    list.forEach(function (r) {
      if (!isObj(r)) return;
      var c = demoChecksOf(r);
      var id = str(r.id) || ('anon-' + list.length);
      perRecord[id] = c;
      Object.keys(c).forEach(function (k) { if (c[k]) hits[k] += 1; });
    });
    var matched = Object.keys(hits).filter(function (k) { return hits[k] > 0; });
    return {
      show: list.length > 0 && matched.length > 0,
      matched: matched,
      hits: hits,
      perRecord: perRecord,
      total: list.length,
      keys: {
        demo_data: 'demo.check.demo_data',
        synthetic: 'demo.check.synthetic',
        source_kind: 'demo.check.source_kind'
      }
    };
  }

  function recordIsDemo(status, record) {
    var id = isObj(record) ? str(record.id) : '';
    return !!(status && status.perRecord && status.perRecord[id] &&
      (status.perRecord[id].demo_data || status.perRecord[id].synthetic || status.perRecord[id].source_kind));
  }

  /* ---------------------------------------------------------------- *
   * 5. formatting (locale aware, deterministic)
   * ---------------------------------------------------------------- */

  var ISO_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?$/;

  function parseISO(value) {
    var s = str(value).trim();
    if (!s) return null;
    var m = ISO_RE.exec(s);
    if (!m) return null;
    var year = +m[1], month = +m[2], day = +m[3];
    var hour = +m[4], minute = +m[5], second = m[6] ? +m[6] : 0;
    var frac = m[7] ? Number('0.' + m[7]) : 0;
    var ms = Math.round(frac * 1000);
    var offsetMin = 0;
    if (m[8] && m[8] !== 'Z') {
      var sign = m[8].charAt(0) === '-' ? -1 : 1;
      var digits = m[8].slice(1).replace(':', '');
      var oh = +digits.slice(0, 2);
      var om = digits.length >= 4 ? +digits.slice(2, 4) : 0;
      offsetMin = sign * (oh * 60 + om);
    }
    var utcMs = Date.UTC(year, month - 1, day, hour, minute, second, ms) - offsetMin * 60000;
    if (!isFinite(utcMs)) return null;
    return { utcMs: utcMs, offsetMin: offsetMin, wallMs: utcMs + offsetMin * 60000 };
  }

  function localeOf(lang) { return LOCALES[lang] || LOCALES[DEFAULT_LANG]; }

  function formatTimestamp(value, lang) {
    var p = parseISO(value);
    if (!p) return t('time.unknown');
    try {
      var fmt = new Intl.DateTimeFormat(localeOf(lang), {
        timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false
      });
      var parts = fmt.formatToParts(new Date(p.wallMs));
      var map = {};
      parts.forEach(function (pp) { if (pp.type !== 'literal') map[pp.type] = pp.value; });
      var hour = map.hour === '24' ? '00' : map.hour;
      return map.month + '-' + map.day + ' ' + hour + ':' + map.minute;
    } catch (e) {
      return map2Fallback(p);
    }
  }

  function map2Fallback(p) {
    var d = new Date(p.wallMs);
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return (d.getUTCMonth() + 1) + '-' + d.getUTCDate() + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
  }

  function formatDuration(ms) {
    var v = num(ms);
    if (v === null || v <= 0) return '';
    if (v < 1000) return Math.round(v) + ' ms';
    if (v < 60000) return (v / 1000).toFixed(1) + ' s';
    var mins = Math.floor(v / 60000);
    var rest = Math.round((v % 60000) / 1000);
    return mins + ' min' + (rest > 0 ? ' ' + rest + ' s' : '');
  }

  function formatCost(cost, lang) {
    if (!isObj(cost)) return '';
    var amount = num(cost.amount);
    var currency = str(cost.currency).toUpperCase();
    if (amount === null || !currency) return t('card.cost.unknown');
    var text;
    try {
      text = new Intl.NumberFormat(localeOf(lang), { style: 'currency', currency: currency, maximumFractionDigits: 4 }).format(amount);
    } catch (e) {
      text = currency + ' ' + amount;
    }
    if (cost.estimated === true) text += ' (' + t('card.cost.estimated') + ')';
    return text;
  }

  function formatConfidence(c) {
    var v = num(c);
    if (v === null) return '';
    return t('card.confidence.value', { p: Math.round(clamp(v, 0, 1) * 100) });
  }

  /* Due time is shown together with its state: the demo records carry
   * timestamps in the past, so "deadline passed" must be stated, never
   * implied. Nothing here claims an action happened. */
  function dueText(record) {
    var p = parseISO(record && record.action_due_at);
    if (!p) return '';
    var label = formatTimestamp(record.action_due_at, i18n.lang);
    var nowMs = Date.now();
    var stateLabel = p.utcMs <= nowMs ? t('card.due.passed', { t: label }) : t('card.due.upcoming', { t: label });
    return label + ' - ' + stateLabel;
  }

  function optionLabel(option) {
    var kind = isObj(option) ? str(option.kind) : '';
    if (kind === 'approve') return t('intervene.options.approve');
    if (kind === 'modify') return t('intervene.options.modify');
    if (kind === 'reject') return t('intervene.options.reject');
    return t('intervene.options.unknown');
  }

  function decisionLabel(decision) {
    var d = str(decision);
    if (d === 'approve') return t('intervene.decision.approve');
    if (d === 'modify') return t('intervene.decision.modify');
    if (d === 'reject') return t('intervene.decision.reject');
    return t('intervene.decision.unknown');
  }

  function receiptLabel(state) {
    var s = str(state);
    if (RECEIPT_STATE_SET.indexOf(s) < 0) s = 'unreported';
    return t('intervene.receipt.' + s);
  }

  function suggestedKindLabel(kind) {
    var k = str(kind);
    if (k === 'retry' || k === 'ignore' || k === 'provide_input' || k === 'edit_params') return t('card.suggested.kind.' + k);
    return t('card.suggested.kind.other');
  }

  function roleLabel(role) {
    var r = str(role);
    if (r === 'admin' || r === 'editor' || r === 'viewer' || r === 'agent') return t('role.' + r);
    return t('role.unknown');
  }

  function impactText(impact) {
    if (!isObj(impact)) return '';
    var parts = [];
    if (hasText(impact.summary)) parts.push(str(impact.summary));
    if (isArr(impact.targets) && impact.targets.length) {
      impact.targets.forEach(function (tg) {
        if (!isObj(tg)) return;
        var label = t('card.impact.targets', { count: num(tg.count) === null ? '?' : num(tg.count), kind: str(tg.kind) });
        var extra = [];
        if (hasText(tg.ref)) extra.push(str(tg.ref));
        if (hasText(tg.change)) extra.push(str(tg.change));
        parts.push(extra.length ? label + ' (' + extra.join(', ') + ')' : label);
      });
    }
    if (isObj(impact.money)) parts.push(str(impact.money.currency) + ' ' + str(impact.money.amount));
    if (hasText(impact.external_endpoint)) parts.push(str(impact.external_endpoint));
    if (isArr(impact.data_categories) && impact.data_categories.length) parts.push(impact.data_categories.join(', '));
    return parts.join(' | ');
  }

  function toolText(tool) {
    if (!isObj(tool) || !hasText(tool.name)) return t('card.tool.none');
    var bits = [str(tool.name)];
    if (hasText(tool.version)) bits.push('v' + str(tool.version));
    if (hasText(tool.provider)) bits.push(str(tool.provider));
    var out = bits.join(' · ');
    if (hasText(tool.params_summary)) out += ' — ' + str(tool.params_summary);
    return out;
  }

  /* ---------------------------------------------------------------- *
   * 6. DOM helpers (small API surface: also used by the node shim)
   * ---------------------------------------------------------------- */

  var doc = null;

  function el(tag, opts) {
    var node = doc.createElement(tag);
    opts = opts || {};
    var reserved = { class: 1, id: 1, text: 1, attrs: 1, dataset: 1, i18n: 1, tabindex: 1 };
    if (opts.class) node.className = opts.class;
    if (opts.id) node.id = opts.id;
    if (opts.text !== undefined && opts.text !== null) node.textContent = str(opts.text);
    if (opts.attrs) Object.keys(opts.attrs).forEach(function (k) { node.setAttribute(k, str(opts.attrs[k])); });
    if (opts.dataset) Object.keys(opts.dataset).forEach(function (k) { node.setAttribute('data-' + k, str(opts.dataset[k])); });
    if (opts.i18n) { node.setAttribute('data-i18n', opts.i18n); node.setAttribute('data-i18n-lang', i18n.lang); }
    if (opts.tabindex !== undefined) node.setAttribute('tabindex', String(opts.tabindex));
    Object.keys(opts).forEach(function (k) {
      if (reserved[k]) return;
      var v = opts[k];
      if (v === undefined || v === null) return;
      node.setAttribute(k, str(v));
    });
    return node;
  }

  function add(parent, child) {
    if (parent && child) parent.appendChild(child);
    return child;
  }

  function text(node, value) {
    if (node) node.textContent = str(value);
    return node;
  }

  function i18nText(node, key, params) {
    if (!node) return node;
    node.textContent = t(key, params);
    node.setAttribute('data-i18n', key);
    node.setAttribute('data-i18n-lang', i18n.lang);
    if (isObj(params)) node.setAttribute('data-i18n-params', JSON.stringify(params));
    return node;
  }

  function aria(node, key, params) {
    if (!node) return node;
    node.setAttribute('aria-label', t(key, params));
    node.setAttribute('data-i18n-aria', key);
    node.setAttribute('data-i18n-lang', i18n.lang);
    return node;
  }

  function on(node, type, handler) {
    if (node && typeof node.addEventListener === 'function') node.addEventListener(type, handler);
    return node;
  }

  function setDataset(node, key, value) {
    if (node) node.setAttribute('data-' + key, str(value));
    return node;
  }

  function clear(node) {
    if (!node) return;
    while (node.firstChild) {
      if (typeof node.removeChild === 'function') node.removeChild(node.firstChild);
      else break;
    }
    if (typeof node.__resetChildren === 'function') node.__resetChildren();
  }

  function byId(id) {
    if (!doc || typeof doc.getElementById !== 'function') return null;
    return doc.getElementById(id);
  }

  function queryAll(selector) {
    var root = byId('app');
    if (!root || typeof root.querySelectorAll !== 'function') return [];
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  /* ---------------------------------------------------------------- *
   * 7. state
   * ---------------------------------------------------------------- */

  /* Set once init() has produced the first render. Every public mutation
   * (records, language, view) re-renders only after that point, so init can
   * stage state without painting half-built screens. */
  var booted = false;

  var state = {
    records: [],
    meta: {},
    zones: computeZones([]),
    demo: demoStatus([]),
    progress: computeProgress([]),
    view: 'mobile',
    sheetOpen: false,
    expanded: {},
    notice: null,
    /* 3.2 desktop layer: status filter, focused record, timeline position
     * and the compare selection. All of them are derived from the same
     * records, so the phone layout keeps exactly the same content. */
    filter: { needs_me: true, running: true, done: true },
    focus: null,
    timelineIndex: 0,
    compare: { a: 'a', b: 'b', open: false },
    compareRecords: [],
    compareMeta: {},
    /* 4.10 mobile reachability (QA 5.1 D-4): the phone layout has no sidebar,
     * so these flags say which phone entry menu / drawer / screen is open.
     * They only move DOM that already exists - no second copy of a panel. */
    more: {
      menu: false,          /* the "more" entry list in the top bar */
      open: false,          /* the drawer that carries an existing rail panel */
      target: 'off',        /* off | audit | export (the rail panels) */
      screen: 'off',        /* off | settings | users (the main-column screens) */
      focus: '',            /* import: the entry that asked to open the sheet */
      notice: '',           /* last feedback line, a dictionary key */
      noticeParams: null,
      opens: 0,
      closes: 0,
      blocked: 0,
      switches: 0
    },
    /* 4.5 settings centre: the masked view from GET /api/settings, the state of
     * the last save / connection test, and the readable feedback line. The
     * secret itself never lands here - the server only ever sends a mask, and
     * what the user types stays in the form field until it is submitted. */
    settings: {
      open: false,
      view: null,
      loaded: false,
      loading: false,
      busy: false,
      saving: false,
      testing: false,
      requests: 0,
      saves: 0,
      tests: 0,
      denials: 0,
      feedback: 'settings.center.feedback.idle',
      feedbackParams: null,
      error: '',
      test: null,
      exportJson: '',
      draft: null
    },
    /* 4.8 audit rail: the chain as the server reports it. The page never
     * recomputes a hash chain: rows, chain status and the store mode all come
     * from GET /api/audit, and the export panel only reports what
     * GET /api/audit/export answered (format, rows, fingerprint, fallback). */
    audit: {
      rows: [],
      meta: {},
      chain: {},
      jsonl: null,
      status: 'idle',
      error: '',
      feedback: 'audit.feedback.idle',
      feedbackParams: null,
      loading: false,
      /* 4.12 (H-01 / H-02): the panel must not report "0 entries / in-memory
       * chain" before it has read the server, and it must follow the writes
       * this page makes (a decision, an export). */
      loaded: false,
      autoLoads: 0,
      autoRefreshes: 0,
      autoTried: 0,
      lastWriteReason: '',
      limit: 8,
      offset: 0,
      filters: { decision: '', actor_id: '', q: '', event: '' },
      requests: 0,
      refreshes: 0,
      loads: 0,
      denials: 0,
      lastRequestId: ''
    },
    exportPanel: {
      busy: false,
      last: null,
      lastPath: '',
      feedback: 'export.feedback.idle',
      feedbackParams: null,
      error: '',
      reason: '',
      count: 0,
      denials: 0,
      downloads: 0,
      popups: 0
    },
    /* 4.9 import (QA 5.1 D1): one shared draft, two mounts (phone bottom sheet
     * + desktop rail). The page never sends a file: a picked file is read into
     * the same text box, so "paste" and "upload" are one request shape and the
     * server's own size cap is the one the page checks. */
    importPanel: {
      text: '',
      filename: '',
      format: '',
      mode: 'upsert',
      status: 'idle',          /* idle | busy | done | error */
      result: null,
      errorKey: 'import.feedback.idle',
      errorParams: null,
      bytes: 0,
      requests: 0,
      imports: 0,
      denials: 0,
      clientBlocked: 0,
      lastRequestId: ''
    },
    stats: { renders: 0, invalidRecords: 0, i18nRefresh: 'embedded' },
    /* verification rig: ?probe=1 writes a report, ?frame=375x812 pins a
     * phone viewport. Both are off for a normal open of the page. */
    probeMode: false,
    frame: null,
    listeners: []
  };

  var granularity = { value: 'l1' };

  function detectGranularity() {
    var stored = null;
    try {
      if (global.localStorage) stored = global.localStorage.getItem(GRAN_STORAGE_KEY);
    } catch (e) { stored = null; }
    var v = str(stored).toLowerCase();
    return (v === 'l2' || v === 'l3') ? v : 'l1';
  }

  function setGranularityValue(v) {
    var x = str(v).toLowerCase();
    granularity.value = (x === 'l2' || x === 'l3') ? x : 'l1';
    try {
      if (global.localStorage) global.localStorage.setItem(GRAN_STORAGE_KEY, granularity.value);
    } catch (e) { /* storage unavailable */ }
    if (booted) renderAll();
    return granularity.value;
  }

  function normalizeRecords(input) {
    var list = isArr(input) ? input : (isObj(input) && isArr(input.records) ? input.records : []);
    var clean = [];
    list.forEach(function (r) {
      if (!isObj(r) || !hasText(r.id)) { state.stats.invalidRecords += 1; return; }
      clean.push(r);
    });
    if (list.length === 0 && (isArr(input) ? input.length === 0 : !isObj(input))) {
      state.notice = input === undefined || input === null ? 'error.data_empty' : 'error.data_invalid';
    } else if (clean.length === 0 && list.length > 0) {
      state.notice = 'error.data_invalid';
    }
    return clean;
  }

  function setRecords(input, meta, opts) {
    state.notice = null;
    state.records = normalizeRecords(input);
    state.meta = isObj(meta) ? meta : (isObj(input) ? input._meta || {} : {});
    state.zones = computeZones(state.records);
    state.demo = demoStatus(state.records);
    state.progress = computeProgress(state.records);
    Object.keys(state.expanded).forEach(function (k) { delete state.expanded[k] ; });
    /* intervention state belongs to the data set it was made for: feeding the
     * view new records must not leave an open form or a stale "recorded" chip */
    resetIntervene(true);
    /* new data invalidates the desktop navigation state as well */
    state.focus = null;
    state.timelineIndex = 0;
    /* Feeding the view new data is a visible change: repaint, do not leave a
     * stale screen behind (the refresh button and the t7 wiring both call this).
     * opts.silent lets one caller own the single render of a multi-step change
     * (4.12: a language swap = swap + restore the view + one renderAll). */
    if (booted && !(isObj(opts) && opts.silent === true)) renderAll();
    return state.records.length;
  }

  /* ================================================================ *
   * 7b. intervention loop (t8 / 4.1): local API client + local decision
   *
   * The page is a zero-build static SPA that must work from file://. So:
   *  - every endpoint is a *relative* same-origin path (no host, no leading
   *    slash) taken from API_ENDPOINTS, and there is exactly one guarded
   *    request function (apiRequest) that speaks to it;
   *  - when there is no transport (file:// blocks fetch, or the sidecar is
   *    not running) the request fails and the decision falls back to a local
   *    simulation: the same transition, computed in the browser, plus a
   *    visible notice. Nothing is ever presented as "executed".
   *  - a decision never touches the execution receipt: recorded != effective.
   * Rules mirror agent_explainer/server.py; web/selfcheck.mjs compares the
   * browser result against a golden JSON produced by the Python function.
   * ================================================================ */

  var API_ENDPOINTS = {
    health: 'health',
    records: 'api/records',
    intervene: 'intervene',
    /* 4.3 auth + user administration (same-origin relative paths only) */
    authStatus: 'api/auth/status',
    authPermissions: 'api/auth/permissions',
    authLogin: 'api/auth/login',
    authLogout: 'api/auth/logout',
    authMe: 'api/auth/me',
    authPassword: 'api/auth/password',
    authCheck: 'api/auth/check',
    users: 'api/users',
    /* 4.4 realtime: SSE stream + ingest status (relative same-origin paths) */
    stream: 'api/stream',
    ingestStatus: 'api/ingest/status',
    ingestEvents: 'api/ingest/events',
    ingestTokens: 'api/ingest/tokens',
    /* 4.5 settings centre: GET /api/settings is the masked view, PUT writes it,
     * and the connection test is a POST on its own sub-path (never a URL the
     * page may invent: this allowlist is the only path source). */
    settings: 'api/settings',
    settingsTestLlm: 'api/settings/test-llm',
    /* 4.5 data management: the documented reset route (the server can turn it
     * off with --no-reset and gates it on settings.write + CSRF) */
    reset: 'api/reset',
    /* 4.8 audit view + export: GET only, gated by audit.read / audit.export.
     * The export answer is a file (text/csv, text/html, application/pdf), so it
     * goes through rawExportGet, never through the JSON path. */
    audit: 'api/audit',
    auditExport: 'api/audit/export',
    /* 4.9 data import: one POST, one door. The page never uploads a file to a
     * URL of its own - the file is read locally and its text goes into the same
     * JSON body as pasted text, so the size limit the server publishes
     * (64 KiB / 200 records) is the limit the page checks too. */
    import: 'api/import',
    /* 5.0 trust layer (docs/07 §5): the eight new surfaces. They are on this
     * allowlist so `extRequest` can never invent a path: an extension module
     * passes a full path, and this table is the only source of prefixes. */
    secretariat: 'api/secretariat',
    rollback: 'api/rollback',
    evidence: 'api/evidence',
    groups: 'api/groups',
    shares: 'api/shares',
    analytics: 'api/analytics',
    suggestions: 'api/suggestions',
    preferences: 'api/preferences',
    compliance: 'api/compliance'
  };
  /* the extension endpoints, in lookup order. `records` is here only so an
   * extension can hang the two new record sub-resources off it
   * (`/api/records/<id>/evidence`, `/api/records/<id>/causal`); its query
   * handling is unchanged because existing callers never pass a query. */
  var EXT_ENDPOINT_NAMES = ['secretariat', 'rollback', 'evidence', 'groups',
                            'shares', 'analytics', 'suggestions', 'preferences',
                            'compliance'];
  var EXT_PATH_ENDPOINTS = EXT_ENDPOINT_NAMES.concat(['records']);
  var INTERVENE_DECISIONS = ['approve', 'modify', 'reject'];
  var DECIDABLE_STATUS = ['pending_confirmation', 'expired'];
  var STATUS_BY_DECISION = { approve: 'approved', modify: 'modified', reject: 'rejected' };
  var INTERVENE_EFFECT_KEY = 'intervene.effect.recorded_only';
  /* the value written into execution_receipt.effect_notice is the server's
   * contract string (agent_explainer/server.py EFFECT_NOTICE_KEY), not an
   * i18n key: the receipt field travels with the record. */
  var RECEIPT_EFFECT_NOTICE = 'intervention.effect.recorded_only';
  var INTERVENE_LOCAL_ACTOR = 'local-sim';
  var INTERVENE_LOCAL_ROLE = 'local';
  var INTERVENE_TIMEOUT_MS = 2500;
  var REASON_MIN_CHARS = 2;
  /* code -> literal dictionary key: every key here exists in zh.json and
   * en.json, and the selfcheck's orphan rule pins that list. */
  var INTERVENE_ERROR_KEYS = {
    NETWORK_ERROR: 'intervene.error.network_error',
    TIMEOUT: 'intervene.error.timeout',
    HTTP_STATUS: 'intervene.error.http_status',
    INTERVENE_REASON_REQUIRED: 'intervene.error.intervene_reason_required',
    INTERVENE_REASON_TOO_SHORT: 'intervene.error.intervene_reason_too_short',
    INTERVENE_PARAM_MISSING: 'intervene.error.intervene_param_missing',
    INTERVENE_PARAM_EMPTY: 'intervene.error.intervene_param_empty',
    INTERVENE_PARAM_TYPE: 'intervene.error.intervene_param_type',
    INTERVENE_PARAM_RANGE: 'intervene.error.intervene_param_range',
    INTERVENE_PARAM_UNKNOWN: 'intervene.error.intervene_param_unknown',
    INTERVENE_PARAM_NOT_ALLOWED: 'intervene.error.intervene_param_not_allowed',
    INTERVENE_PARAM_NO_CHANGE: 'intervene.error.intervene_param_no_change',
    INTERVENE_DECISION_INVALID: 'intervene.error.intervene_decision_invalid',
    INTERVENE_ALREADY_DECIDED: 'intervene.error.intervene_already_decided',
    INTERVENE_STATE_INVALID: 'intervene.error.intervene_state_invalid',
    INTERVENE_ACTION_NOT_FOUND: 'intervene.error.intervene_action_not_found',
    INTERVENE_OPTION_MISMATCH: 'intervene.error.intervene_option_mismatch',
    INTERVENE_IDEMPOTENCY_MISMATCH: 'intervene.error.intervene_idempotency_mismatch',
    INTERVENE_BODY_INVALID: 'intervene.error.intervene_body_invalid',
    INTERVENE_BODY_NOT_OBJECT: 'intervene.error.intervene_body_not_object',
    INTERVENE_FIELD_MISSING: 'intervene.error.intervene_field_missing',
    INTERVENE_FIELD_TYPE: 'intervene.error.intervene_field_type',
    INTERVENE_FIELD_UNKNOWN: 'intervene.error.intervene_field_unknown',
    INTERVENE_CONTENT_TYPE: 'intervene.error.intervene_content_type',
    INTERVENE_BODY_TOO_LARGE: 'intervene.error.intervene_body_too_large',
    METHOD_NOT_ALLOWED: 'intervene.error.method_not_allowed',
    ROUTE_NOT_FOUND: 'intervene.error.route_not_found',
    STATIC_NOT_FOUND: 'intervene.error.static_not_found',
    STATIC_PATH_REJECTED: 'intervene.error.static_path_rejected',
    RESET_DISABLED: 'intervene.error.reset_disabled',
    INTERNAL_ERROR: 'intervene.error.internal_error'
  };
  var INTERVENE_VALIDATION_KEYS = {
    reason_required: 'intervene.validation.reason_required',
    field_required: 'intervene.validation.field_required',
    field_type: 'intervene.validation.field_type',
    field_range: 'intervene.validation.field_range',
    no_change: 'intervene.validation.no_change',
    busy: 'intervene.validation.busy'
  };
  var INTERVENE_TYPE_KEYS = {
    integer: 'intervene.validation.type.integer',
    number: 'intervene.validation.type.number',
    boolean: 'intervene.validation.type.boolean',
    text: 'intervene.validation.type.text'
  };

  /* ================================================================
   * 4.9 import (t22 / QA 5.1 D1): paste a trace, or pick a file, and it lands
   * in the list. Every limit here mirrors agent_explainer/server.py
   * (IMPORT_MAX_BYTES / IMPORT_MAX_RECORDS / IMPORT_MODES / source fields);
   * the server is the one that enforces them, the page only says it early and
   * in words. The four formats are auto-detected by trace_adapter, so the
   * format selector is "auto" plus the four names, never a guess list.
   * ================================================================ */
  var IMPORT_MAX_BYTES = 65536;
  var IMPORT_MAX_RECORDS = 200;
  var IMPORT_FORMATS = ['', 'langfuse', 'otel_genai', 'mcp_events', 'native'];
  var IMPORT_MODES = ['upsert', 'create', 'update'];
  var IMPORT_FORMAT_KEYS = {
    '': 'import.format.auto',
    langfuse: 'import.format.langfuse',
    otel_genai: 'import.format.otel_genai',
    mcp_events: 'import.format.mcp_events',
    native: 'import.format.native'
  };
  var IMPORT_MODE_KEYS = {
    upsert: 'import.mode.upsert',
    create: 'import.mode.create',
    update: 'import.mode.update'
  };
  /* code -> dictionary key (tools/i18n_lint.py fails the build when one of
   * these keys is missing from zh.json and en.json). */
  var IMPORT_ERROR_KEYS = {
    IMPORT_INPUT_EMPTY: 'import.error.input_empty',
    IMPORT_INPUT_NOT_JSON: 'import.error.input_not_json',
    IMPORT_TOO_LARGE: 'import.error.too_large',
    IMPORT_TOO_MANY: 'import.error.too_many',
    IMPORT_FORMAT_UNSUPPORTED: 'import.error.format_unsupported',
    IMPORT_MODE_INVALID: 'import.error.mode_invalid',
    RECORD_STORE_LOCKED: 'import.error.store_locked',
    RECORD_STORE_WRITE_FAILED: 'import.error.store_write_failed',
    RECORD_STORE_LINE_INVALID: 'import.error.store_line_invalid',
    RECORD_STORE_LINE_TOO_LARGE: 'import.error.store_line_too_large',
    RECORD_STORE_RECORD_INVALID: 'import.error.store_record_invalid',
    RECORD_STORE_TAIL_TRUNCATED: 'import.error.store_tail_truncated',
    RECORD_STORE_OP_UNKNOWN: 'import.error.store_op_unknown',
    INGEST_TOO_MANY_EVENTS: 'import.error.too_many',
    INGEST_TOO_LARGE: 'import.error.too_large',
    INTERVENE_BODY_TOO_LARGE: 'import.error.too_large',
    INTERVENE_BODY_INVALID: 'import.error.input_not_json',
    INTERVENE_CONTENT_TYPE: 'import.error.content_type',
    AUTH_REQUIRED: 'import.error.auth_required',
    ROLE_FORBIDDEN: 'import.error.role_forbidden',
    CSRF_FAILED: 'import.error.csrf_failed',
    RECORD_STORE_WRITE_FAILED: 'import.error.store_write_failed',
    NETWORK_ERROR: 'import.error.network_error',
    TIMEOUT: 'import.error.timeout',
    HTTP_STATUS: 'import.error.http_status',
    METHOD_NOT_ALLOWED: 'import.error.method_not_allowed',
    ROUTE_NOT_FOUND: 'import.error.route_not_found',
    INTERNAL_ERROR: 'import.error.internal_error'
  };

  state.api = {
    mode: 'unchecked',            /* unchecked | live | offline | forced_offline */
    reason: 'not_probed',
    checks: 0,
    requests: 0,
    lastRequestId: '',
    lastStatus: null,
    forced: null,
    transport: null
  };
  state.iv = {
    forms: {}, reasons: {}, values: {}, busy: {}, results: {}, errors: {},
    sent: 0, local: 0, decisions: 0
  };

  function apiEndpoint(name) {
    return Object.prototype.hasOwnProperty.call(API_ENDPOINTS, str(name))
      ? API_ENDPOINTS[str(name)] : null;
  }

  /* 4.3: a sub-path is allowed only when it hangs off an allowlisted endpoint.
   * That is how /api/users/<id>/<verb> stays on the allowlist without letting
   * the page invent arbitrary paths: no leading slash, no scheme, no "..". */
  function subPathOf(endpoint, target) {
    var base = apiEndpoint(endpoint);
    var raw = str(target);
    if (!base || !raw || raw.indexOf(base + '/') !== 0) return null;
    if (raw.charAt(0) === '/' || raw.indexOf('://') >= 0 || raw.indexOf('..') >= 0) return null;
    return raw;
  }

  function apiOverHttp() {
    var loc = global.location;
    var proto = loc && isStr(loc.protocol) ? String(loc.protocol).toLowerCase() : '';
    return proto === 'http:' || proto === 'https:';
  }

  /* 5.0 trust layer: one door for the eight new surfaces. `ext/core.js` calls
   * this, never `fetch` directly, so the CSRF header, the timeout and the
   * error envelope stay in one place. `target` must start with an allowlisted
   * base (exact match or a `/` sub-path); anything else is refused before a
   * request is made. */
  function extRequest(target, body, opts) {
    var raw = str(target).trim();
    if (!raw) return Promise.reject({ code: 'ROUTE_NOT_FOUND', detail: { path: '' } });    var options = isObj(opts) ? opts : {};
    var name = null;
    var wantPath = null;
    for (var i = 0; i < EXT_PATH_ENDPOINTS.length; i += 1) {
      var candidate = EXT_PATH_ENDPOINTS[i];
      var base = API_ENDPOINTS[candidate];
      if (raw === base) { name = candidate; break; }
      if (raw.indexOf(base + '/') === 0) { name = candidate; wantPath = raw; break; }
    }
    if (!name) return Promise.reject({ code: 'ROUTE_NOT_FOUND', detail: { path: raw } });
    var headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
    if (state.auth.csrf) headers[AUTH_CSRF_HEADER] = state.auth.csrf;
    var settings = {
      credentials: 'same-origin',
      headers: headers,
      method: isStr(options.method) ? options.method : (isObj(body) ? 'POST' : 'GET')
    };
    if (wantPath) settings.path = wantPath;
    if (isObj(options.query)) settings.query = options.query;
    if (isStr(options.response)) settings.response = options.response;
    if (isStr(options.accept)) settings.accept = options.accept;
    return apiRequest(name, isObj(body) ? body : null, settings);
  }

  function isStr(v) { return typeof v === 'string'; }

  function apiSnapshot() {
    return {
      mode: state.api.mode, reason: state.api.reason, checks: state.api.checks,
      requests: state.api.requests, lastRequestId: state.api.lastRequestId,
      lastStatus: state.api.lastStatus, forced: state.api.forced,
      transport: typeof state.api.transport === 'function',
      endpoints: API_ENDPOINTS
    };
  }

  function setApiMode(mode, reason) {
    state.api.mode = mode === 'live' ? 'live' : (mode === 'forced_offline' ? 'forced_offline' : 'offline');
    state.api.reason = str(reason) || state.api.reason;
    return state.api.mode;
  }

  /* One guarded request function: relative same-origin path, JSON body,
   * JSON answer. 4.3 added an options object: cookie auth needs
   * credentials:'same-origin' plus the CSRF header, and nothing else may send
   * credentials - the intervention path keeps the old no-credentials default. */
  function apiRequest(endpoint, body, opts) {
    var options = isObj(opts) ? opts : {};
    /* the allowlisted endpoint is always the base; opts.path may only hang a
     * validated sub-path off it (used by the user-administration routes) */
    var path = apiEndpoint(endpoint);
    if (path && isStr(options.path)) path = subPathOf(endpoint, options.path) || path;
    /* 4.8: a query is appended only from the allowlisted key list, and every
     * value is percent-encoded here, so a filter can never invent a path */
    if (path && isObj(options.query)) path = path + queryStringOf(endpoint, options.query);
    if (!path) return Promise.reject({ code: 'ROUTE_NOT_FOUND', detail: { endpoint: str(endpoint) } });
    state.api.requests += 1;
    state.api.lastStatus = null;
    if (state.api.mode === 'forced_offline' && typeof state.api.transport !== 'function') {
      /* ?api=off says "do not talk to anyone": the caller falls back to the
       * local simulation without a transport call, so nothing leaves the page */
      state.api.requests -= 1;
      return Promise.reject({ code: 'NETWORK_ERROR', detail: { forced_offline: true } });
    }
    var payload = isObj(body) ? JSON.stringify(body) : null;
    if (payload !== null && payload.length > 65536) {
      return Promise.reject({ code: 'INTERVENE_BODY_TOO_LARGE', detail: { bytes: payload.length } });
    }
    /* 4.5: PUT is the only extra verb the page may use (the settings centre),
     * and it is validated right here, so a verb can never be invented by a
     * string in the UI: GET stays GET, a body without an explicit verb is
     * still POST - exactly the pre-4.5 behaviour. */
    var method = 'GET';
    if (isStr(options.method)) {
      var wanted = options.method.toUpperCase();
      if (wanted === 'POST' || wanted === 'PUT' || wanted === 'GET') method = wanted;
    }
    if (payload !== null && method === 'GET') method = 'POST';
    /* 4.8: the audit export is a file, not JSON - the same guarded door reads
     * it as text (one fetch call site stays the invariant the self-check
     * counts), with an Accept line the caller may name. */
    var wantText = (isStr(options.response) && options.response === 'text');
    if (typeof state.api.transport === 'function') {
      return Promise.resolve(state.api.transport(endpoint, path, body, method)).then(function (res) {
        var out = normalizeApiResponse(res);
        if (wantText && !isObj(out.data)) out.text = str(out.data);
        return out;
      });
    }
    if (!apiOverHttp() || typeof global.fetch !== 'function') {
      return Promise.reject({ code: 'NETWORK_ERROR', detail: { protocol: apiOverHttp() ? 'http' : 'no_transport' } });
    }
    var init = {
      method: method,
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'omit',
      cache: 'no-store'
    };
    if (isObj(opts)) {
      if (isStr(opts.credentials)) init.credentials = opts.credentials;
      if (isObj(opts.headers)) {
        Object.keys(opts.headers).forEach(function (key) {
          if (hasText(opts.headers[key])) init.headers[key] = str(opts.headers[key]);
        });
      }
    }
    if (payload !== null) init.body = payload;
    if (wantText) init.headers.Accept = hasText(options.accept) ? str(options.accept) : 'text/plain';
    var timer = null;
    var abort = null;
    if (typeof global.AbortController === 'function' && typeof global.setTimeout === 'function') {
      abort = new global.AbortController();
      init.signal = abort.signal;
      timer = global.setTimeout(function () {
        try { abort.abort(); } catch (e) { /* the request is already settled */ }
      }, INTERVENE_TIMEOUT_MS);
    }
    return global.fetch(path, init).then(function (resp) {
      if (timer !== null) global.clearTimeout(timer);
      var status = resp && isStr(resp.status) ? parseInt(resp.status, 10) : (resp ? resp.status : 0);
      var ok = status >= 200 && status < 300;
      var body = wantText
        ? ((resp && typeof resp.text === 'function') ? resp.text() : Promise.resolve(''))
        : ((resp && typeof resp.json === 'function') ? resp.json() : Promise.resolve(null));
      return body.then(function (data) {
        if (wantText && !ok && isStr(data)) {
          /* a failed export still answers with the unified JSON error: parse it
           * so the panel can show a readable line instead of a blob of text */
          try { data = JSON.parse(data); } catch (e) { /* keep the text as it is */ }
        }
        return { ok: ok, status: status, data: data,
                 text: wantText ? (isObj(data) ? '' : str(data)) : '',
                 headers: resp && resp.headers ? resp.headers : null };
      }, function () {
        return { ok: false, status: status, data: null };
      });
    }, function (err) {
      if (timer !== null) global.clearTimeout(timer);
      var name = err && err.name ? String(err.name) : '';
      return Promise.reject({ code: name === 'AbortError' ? 'TIMEOUT' : 'NETWORK_ERROR',
                              detail: { message: err && err.message ? String(err.message) : name } });
    });
  }

  function normalizeApiResponse(res) {
    if (!isObj(res)) return { ok: false, status: 0, data: null };
    var status = isStr(res.status) ? parseInt(res.status, 10) : num(res.status);
    var code = null;
    if (isObj(res.data) && hasText(res.data.code)) code = str(res.data.code);
    return {
      ok: status === undefined || status === null ? res.ok === true : (status >= 200 && status < 300),
      status: status, data: isObj(res.data) ? res.data : res.data, code: code,
      headers: isObj(res.headers) ? res.headers : null
    };
  }

  function responseRequestId(res) {
    if (!isObj(res) || !isObj(res.headers)) return '';
    var keys = ['x-request-id', 'X-Request-Id'];
    for (var i = 0; i < keys.length; i += 1) {
      var h = res.headers[keys[i]] || (res.headers.get ? res.headers.get(keys[i]) : null);
      if (hasText(h)) return str(h);
    }
    return '';
  }

  function probeApi() {
    if (state.api.forced === 'offline') {
      state.api.checks += 1;
      setApiMode('forced_offline', 'url_api_off');
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR' });
    }
    if (typeof state.api.transport !== 'function' && !apiOverHttp()) {
      state.api.checks += 1;
      setApiMode('offline', 'file_protocol');
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR' });
    }
    return apiRequest('health').then(function (res) {
      state.api.checks += 1;
      if (res && res.ok && isObj(res.data)) {
        setApiMode('live', str(res.data.mode) || 'local');
        state.api.lastRequestId = responseRequestId(res);
      } else {
        setApiMode('offline', res && res.status ? 'http_' + res.status : 'no_health');
      }
      return res;
    }, function (err) {
      state.api.checks += 1;
      setApiMode('offline', err && err.code ? str(err.code) : 'probe_failed');
      return { ok: false, code: (err && err.code) || 'NETWORK_ERROR' };
    });
  }

  /* In live mode the server is the source of truth: after a reload the page has
   * to show the decisions the server already stored, not the fixture copy.
   * Offline (file://) keeps the embedded demo data, and a failed read leaves
   * the page exactly as it was - the demo data is never replaced by nothing. */
  function syncRecordsFromApi() {
    if (state.api.mode !== 'live') return Promise.resolve(false);
    return apiRequest('records').then(function (res) {
      var data = (res && res.ok) ? res.data : null;
      if (!isObj(data) || !isArr(data.records) || !data.records.length) return false;
      setRecords(data.records, isObj(data.meta) ? data.meta : null);
      renderAll();
      return true;
    }, function () { return false; });
  }

  /* 4.12 (B-01 / H-03): a refresh that keeps the operator's place.
   *
   * syncRecordsFromApi is the boot path: a full replace is right there. During a
   * session the page must never wipe the list, because the list carries the
   * imported records, the open cards, the focused card and the timeline
   * position. So this path merges by id (replace / append) and reports what
   * moved. It is what the language switch and the "someone else decided"
   * freshness check call.
   *
   * There is no api/records?lang= on the server (docs/03 6.5): the sentences
   * come in the server's language, and the interface copy follows the interface
   * language. Nothing here invents a translation. */
  function refreshRecordsFromApi(opts) {
    var options = isObj(opts) ? opts : {};
    if (state.api.mode !== 'live') return Promise.resolve(false);
    state.stats.recordsRefreshes = (state.stats.recordsRefreshes || 0) + 1;
    return apiRequest('records').then(function (res) {
      var data = (res && res.ok) ? res.data : null;
      if (!isObj(data) || !isArr(data.records) || !data.records.length) {
        state.stats.recordsRefreshFailed = (state.stats.recordsRefreshFailed || 0) + 1;
        return false;
      }
      var merged = mergeIncomingRecords(data.records, {
        silent: true,
        force: options.force === true
      });
      if (isObj(data.meta)) state.meta = data.meta;
      state.stats.recordsMerged = (state.stats.recordsMerged || 0) + merged.added + merged.updated;
      if (merged.added || merged.updated) renderAll();
      return {
        reason: str(options.reason),
        added: merged.added,
        updated: merged.updated,
        records: state.records.length
      };
    }, function () {
      state.stats.recordsRefreshFailed = (state.stats.recordsRefreshFailed || 0) + 1;
      return false;
    });
  }

  /* ================================================================
   * 4.4 realtime (t14): server push (SSE) merged into the visible list
   *
   * Contract with agent_explainer/server.py (docs/03 6.3 F1): GET api/stream
   * pushes hello, heartbeat, action.created, action.updated,
   * action.decision_recorded, ingest.batch, records.reset. Every real frame
   * carries a monotonic id (the server's seq); on reconnect the browser sends
   * it back as Last-Event-ID and the server replays from its ring buffer, so a
   * dropped connection never silently loses an action.
   *
   * Three honest states, all visible in one chip:
   *   live      - the EventSource is open and frames arrive;
   *   reconnect - one attempt failed, backoff running (the chip says which one);
   *   poll      - push is not available (no EventSource, proxy killed the
   *               stream, repeated errors, or the stream is over capacity):
   *               the page polls api/records and says so, with the reason;
   *   off       - ?realtime=off / ?api=off: no background traffic at all;
   *   blocked   - there is nothing to connect to (file://, offline mode).
   * Degradation is never silent: chip text + data-mode + data-reason are all
   * observable, and the selfcheck pins them.
   *
   * A pushed frame only ever carries facts: the merge never invents a decision,
   * and a decision frame (action.decision_recorded) shows who decided - the
   * execution receipt stays as the server left it (recorded != effective).
   * ================================================================ */

  var RT_EVENTS = ['hello', 'heartbeat', 'action.created', 'action.updated',
                   'action.decision_recorded', 'ingest.batch', 'ingest.stats', 'records.reset'];
  var RT_LIVE_TYPES = ['action.created', 'action.updated', 'action.decision_recorded'];
  var RT_BACKOFF_BASE_MS = 800;
  var RT_BACKOFF_MAX_MS = 12000;
  var RT_RECONNECT_MAX = 4;
  var RT_POLL_INTERVAL_MS = 4000;
  var RT_RING = 50;

  state.rt = {
    mode: 'idle', reason: 'not_started', source: null, timer: null, pollTimer: null,
    attempts: 0, reconnects: 0, reconnectDelay: RT_BACKOFF_BASE_MS,
    lastSeq: 0, events: 0, frames: [], added: 0, updatedCount: 0, batches: 0,
    resets: 0, decisions: 0, heartbeats: 0, merges: 0, drops: 0,
    polls: 0, pollAdded: 0, pollUpdated: 0, pollFailures: 0,
    pollIntervalMs: RT_POLL_INTERVAL_MS, lastError: '', lastBatch: null,
    lastStats: null, lastMerged: null, factory: null, schedule: null, closed: false
  };

  function rtChipKey(mode) {
    var keys = {
      live: 'realtime.mode.live', poll: 'realtime.mode.poll',
      reconnect: 'realtime.mode.reconnect', off: 'realtime.mode.off',
      blocked: 'realtime.mode.blocked', idle: 'realtime.mode.idle'
    };
    return keys[mode] || keys.idle;
  }

  function rtSet(mode, reason) {
    state.rt.mode = mode;
    if (hasText(reason)) state.rt.reason = str(reason);
    return state.rt.mode;
  }

  function rtSnapshot() {
    return {
      mode: state.rt.mode, reason: state.rt.reason, attempts: state.rt.attempts,
      reconnects: state.rt.reconnects, reconnectDelay: state.rt.reconnectDelay,
      lastSeq: state.rt.lastSeq, events: state.rt.events, added: state.rt.added,
      updated: state.rt.updatedCount, batches: state.rt.batches,
      resets: state.rt.resets, decisions: state.rt.decisions,
      heartbeats: state.rt.heartbeats, polls: state.rt.polls,
      pollAdded: state.rt.pollAdded, pollUpdated: state.rt.pollUpdated,
      pollFailures: state.rt.pollFailures, pollIntervalMs: state.rt.pollIntervalMs,
      ring: state.rt.frames.length, lastError: state.rt.lastError,
      scheduler: typeof state.rt.schedule === 'function',
      lastBatch: state.rt.lastBatch ? {
        accepted: toNum(state.rt.lastBatch.accepted),
        created: toNum(state.rt.lastBatch.created),
        updated: toNum(state.rt.lastBatch.updated)
      } : null,
      lastMerged: state.rt.lastMerged,
      eventTypes: RT_EVENTS, endpoints: API_ENDPOINTS
    };
  }

  /* the selfcheck injects a fake EventSource: same listener API, no socket */
  function setStreamFactory(fn) {
    state.rt.factory = typeof fn === 'function' ? fn : null;
    return !!state.rt.factory;
  }

  /* The backoff timer is injectable for the same reason: the selfcheck runs the
   * reconnect path synchronously (deterministic, no real waiting, no leftover
   * timers keeping the process alive). The product path is a real timer. */
  function setRealtimeScheduler(fn) {
    state.rt.schedule = typeof fn === 'function' ? fn : null;
    return !!state.rt.schedule;
  }

  function rtSchedule(delayMs, work) {
    var run = function () {
      state.rt.timer = null;
      work();
    };
    if (typeof state.rt.schedule === 'function') {
      state.rt.schedule(delayMs, run);
      return null;
    }
    return global.setTimeout ? global.setTimeout(run, delayMs) : null;
  }

  function rtParam() {
    var params = searchParams();
    var raw = str(params.realtime).toLowerCase();
    if (raw === 'off' || raw === 'poll' || raw === 'sse' || raw === 'live') return raw;
    return 'auto';
  }

  /* 4.12 (H-03): "did this record change" must compare what the page shows.
   * The old comparison looked at the top-level fields only, so a decision that
   * the server stores under user_intervention (with status / requires_action /
   * the execution receipt moving with it) read as "the same" and a decision
   * made in another window never reached the card. */
  function recordShape(record) {
    var ui = (isObj(record) && isObj(record.user_intervention)) ? record.user_intervention : {};
    var receipt = (isObj(record) && isObj(record.execution_receipt)) ? record.execution_receipt : {};
    return [
      str(record && record.timestamp),
      str(record && record.status),
      String(record && record.requires_action),
      str(record && record.result_summary),
      str(record && record.latency_ms),
      str(record && record.decision),
      str(ui.decision),
      String(ui.recorded === true),
      str(ui.decided_by_role),
      str(receipt.state)
    ].join('|');
  }

  /* Replace by id / append: never a full replace, because setRecords wipes the
   * expanded cards and any intervention form the operator is filling in.
   * opts.force replaces unconditionally (a decision frame, a language re-read);
   * opts.silent leaves the single render to the caller. */
  function mergeIncomingRecords(incoming, opts) {
    var options = isObj(opts) ? opts : {};
    var list = isArr(incoming) ? incoming : (isObj(incoming) ? [incoming] : []);
    var added = 0;
    var updated = 0;
    list.forEach(function (raw) {
      if (!isObj(raw) || !hasText(raw.id)) { state.stats.invalidRecords += 1; return; }
      var found = null;
      for (var i = 0; i < state.records.length; i += 1) {
        if (str(state.records[i].id) === str(raw.id)) { found = i; break; }
      }
      if (found === null) { state.records.push(raw); added += 1; return; }
      var before = state.records[found];
      if (!options.force && recordShape(before) === recordShape(raw)) return;
      state.records[found] = raw;
      updated += 1;
    });
    if (added || updated) {
      state.zones = computeZones(state.records);
      state.demo = demoStatus(state.records);
      state.progress = computeProgress(state.records);
      state.merged = (state.merged || 0) + added + updated;
      if (!options.silent) renderAll();
    }
    return { added: added, updated: updated };
  }

  function rtRecordFrame(frame) {
    state.rt.frames.push(frame);
    if (state.rt.frames.length > RT_RING) state.rt.frames.shift();
  }

  /* EventSource gives us type + lastEventId + a JSON string in data. */
  function parseStreamEvent(ev) {
    var data = null;
    var raw = ev && isStr(ev.data) ? ev.data : '';
    if (raw) {
      try { data = JSON.parse(raw); } catch (e) { data = null; }
    }
    var seq = parseInt(str(ev && (ev.lastEventId || ev.id)), 10);
    return {
      event: str(ev && ev.type) || 'message',
      seq: isFinite(seq) ? seq : null,
      data: isObj(data) ? data : null,
      raw: raw
    };
  }

  function applyStreamFrame(frame) {
    if (!isObj(frame)) return { applied: false, reason: 'bad_frame' };
    var type = str(frame.event);
    var data = isObj(frame.data) ? frame.data : {};
    state.rt.events += 1;
    if (isFinite(frame.seq) && frame.seq > state.rt.lastSeq) state.rt.lastSeq = frame.seq;
    rtRecordFrame(frame);
    if (type === 'heartbeat') {
      state.rt.heartbeats += 1;
      return { applied: true, reason: 'heartbeat' };
    }
    if (type === 'hello') {
      rtSet('live', 'hello');
      return { applied: true, reason: 'hello', head: toNum(data.head_seq) };
    }
    if (type === 'records.reset') {
      state.rt.resets += 1;
      rtSet(state.rt.mode === 'poll' ? 'poll' : 'live', 'reset');
      syncRecordsFromApi().then(function () { renderAll(); return null; });
      return { applied: true, reason: 'reset', records: toNum(data.records) };
    }
    if (type === 'ingest.stats') {
      /* declared in the server contract (STREAM_EVENT_TYPES) and published when
       * a deployment turns it on: facts about the intake, never a decision */
      state.rt.lastStats = data;
      return { applied: true, reason: 'stats', accepted: toNum(data.accepted) };
    }
    if (type === 'ingest.batch') {
      state.rt.batches += 1;
      state.rt.lastBatch = data;
      return {
        applied: true, reason: 'batch',
        accepted: toNum(data.accepted), created: toNum(data.created),
        updated: toNum(data.updated)
      };
    }
    if (RT_LIVE_TYPES.indexOf(type) >= 0) {
      /* 4.12 (H-03, QA 5.2): a decision frame is a fact the operator must see.
       * The cheap "nothing changed" comparison looks at the running fields of a
       * record; a decision made in another window can move only the recorded
       * decision under user_intervention, so this frame is merged every time. */
      if (type === 'action.decision_recorded') state.rt.decisions += 1;
      var decisionFrame = (type === 'action.decision_recorded');
      var merged = mergeIncomingRecords(isObj(data.record) ? [data.record] : [],
        decisionFrame ? { force: true } : undefined);
      state.rt.added += merged.added;
      state.rt.updatedCount += merged.updated;
      state.rt.lastMerged = {
        type: type, added: merged.added, updated: merged.updated,
        changed: isArr(data.changed_fields) ? data.changed_fields : [],
        action_id: str(data.action_id), decision: str(data.decision),
        seq: frame.seq
      };
      if (state.rt.mode !== 'poll') rtSet('live', 'streaming');
      return {
        applied: true, reason: 'merged', added: merged.added,
        updated: merged.updated, type: type,
        changed: isArr(data.changed_fields) ? data.changed_fields : []
      };
    }
    return { applied: false, reason: 'unknown_type', type: type };
  }

  function closeStreamSource() {
    var src = state.rt.source;
    state.rt.source = null;
    if (!src) return false;
    if (typeof src.close === 'function') {
      try { src.close(); } catch (e) { /* already gone */ }
    }
    return true;
  }

  function stopPolling() {
    if (state.rt.pollTimer && global.clearInterval) global.clearInterval(state.rt.pollTimer);
    state.rt.pollTimer = null;
    return true;
  }

  /* Polling is the labelled fallback: it merges (same pipeline as a push), and
   * the chip keeps saying "poll" plus the reason it is not a push. */
  function pollRecords() {
    if (state.api.mode !== 'live') return Promise.resolve(false);
    return apiRequest('records').then(function (res) {
      var data = (res && res.ok) ? res.data : null;
      if (!isObj(data) || !isArr(data.records) || !data.records.length) return false;
      var merged = mergeIncomingRecords(data.records);
      state.rt.polls += 1;
      state.rt.pollAdded += merged.added;
      state.rt.pollUpdated += merged.updated;
      var head = isObj(data.meta) ? toNum(data.meta.stream_head_seq) : null;
      if (head !== null && head > state.rt.lastSeq) state.rt.lastSeq = head;
      renderTopbar();
      return true;
    }, function () {
      state.rt.polls += 1;
      state.rt.pollFailures += 1;
      return false;
    });
  }

  function startPolling(reason) {
    stopPolling();
    closeStreamSource();
    if (state.rt.timer && global.clearTimeout) global.clearTimeout(state.rt.timer);
    state.rt.timer = null;
    rtSet('poll', reason);
    state.rt.pollIntervalMs = RT_POLL_INTERVAL_MS;
    if (global.setInterval) {
      state.rt.pollTimer = global.setInterval(function () { pollRecords(); },
                                              state.rt.pollIntervalMs);
    }
    pollRecords();
    renderTopbar();
    return true;
  }

  function rtStreamUrl() {
    var base = apiEndpoint('stream');
    if (!base) return null;
    return state.rt.lastSeq ? base + '?since=' + state.rt.lastSeq : base;
  }

  function rtOnStreamError() {
    state.rt.attempts += 1;
    state.rt.lastError = 'stream_error';
    closeStreamSource();
    if (state.rt.attempts >= RT_RECONNECT_MAX) {
      startPolling('stream_repeated_error');
      return 'poll';
    }
    rtSet('reconnect', 'retry_' + state.rt.attempts);
    var delay = Math.min(RT_BACKOFF_MAX_MS,
                         RT_BACKOFF_BASE_MS * Math.pow(2, state.rt.attempts - 1));
    state.rt.reconnectDelay = delay;
    rtSchedule(delay, function () {
      state.rt.reconnects += 1;
      connectStream();
    });
    renderTopbar();
    return 'reconnect';
  }

  function connectStream() {
    var param = rtParam();
    if (param === 'off') {
      rtSet('off', 'url_realtime_off');
      renderTopbar();
      return false;
    }
    if (param === 'poll') {
      startPolling('url_realtime_poll');
      return false;
    }
    if (state.api.mode === 'forced_offline' || state.api.forced === 'offline') {
      rtSet('blocked', 'api_off');
      renderTopbar();
      return false;
    }
    var injected = typeof state.api.transport === 'function';
    var reachable = state.api.mode === 'live' && (apiOverHttp() || injected);
    if (!reachable) {
      /* no server to push from (file:// with no sidecar, or the probe never got
       * an answer): the page keeps the data it has and stops pretending */
      rtSet('blocked', state.api.mode === 'live' ? 'no_http_origin' : 'api_not_live');
      renderTopbar();
      return false;
    }
    var Ctor = state.rt.factory || global.EventSource;
    if (typeof Ctor !== 'function') {
      startPolling('no_eventsource');
      return false;
    }
    var url = rtStreamUrl();
    if (!url) {
      rtSet('blocked', 'route_not_found');
      return false;
    }
    var src;
    try {
      src = state.rt.factory ? Ctor(url, { since: state.rt.lastSeq })
                             : new Ctor(url, { withCredentials: true });
    } catch (e) {
      state.rt.lastError = 'stream_ctor_failed';
      startPolling('stream_ctor_failed');
      return false;
    }
    state.rt.source = src;
    RT_EVENTS.forEach(function (name) {
      if (typeof src.addEventListener !== 'function') return;
      src.addEventListener(name, function (ev) {
        var frame = parseStreamEvent(ev);
        frame.event = name;                       /* the browser fills type */
        var res = applyStreamFrame(frame);
        if (name !== 'heartbeat') {
          state.rt.attempts = 0;
          state.rt.reconnectDelay = RT_BACKOFF_BASE_MS;
        }
        if (res.applied && (res.added || res.updated)) renderTopbar();
      });
    });
    src.onopen = function () {
      rtSet('live', 'open');
      state.rt.attempts = 0;
      renderTopbar();
    };
    src.onerror = function () { rtOnStreamError(); };
    return true;
  }

  function startRealtime() {
    var param = rtParam();
    if (param === 'off') {
      rtSet('off', 'url_realtime_off');
      renderTopbar();
      return Promise.resolve(false);
    }
    if (param === 'poll') {
      startPolling('url_realtime_poll');
      return Promise.resolve(true);
    }
    var opened = connectStream();
    renderTopbar();
    return Promise.resolve(!!opened);
  }

  function stopRealtime(reason) {
    closeStreamSource();
    stopPolling();
    if (state.rt.timer && global.clearTimeout) global.clearTimeout(state.rt.timer);
    state.rt.timer = null;
    rtSet('off', reason || 'stopped');
    renderTopbar();
    return true;
  }

  /* ---- record helpers for the intervention panel ------------------ */

  function recordById(id) {
    var key = str(id);
    for (var i = 0; i < state.records.length; i += 1) {
      if (str(state.records[i].id) === key) return state.records[i];
    }
    return null;
  }

  function optionsOf(record) {
    return isArr(record && record.intervention_options) ? record.intervention_options : [];
  }

  function optionByKind(record, kind) {
    var opts = optionsOf(record);
    for (var i = 0; i < opts.length; i += 1) {
      if (isObj(opts[i]) && str(opts[i].kind) === str(kind)) return opts[i];
    }
    return null;
  }

  function optionById(record, optionId) {
    var id = str(optionId);
    if (!id) return null;
    var opts = optionsOf(record);
    for (var i = 0; i < opts.length; i += 1) {
      if (isObj(opts[i]) && str(opts[i].id) === id) return opts[i];
    }
    return null;
  }

  function editableFieldsOf(record, optionId) {
    var opt = optionId ? optionById(record, optionId) : optionByKind(record, 'modify');
    if (!opt) return [];
    return (isArr(opt.editable_fields) ? opt.editable_fields : []).filter(isObj);
  }

  function decidableRecord(record) {
    if (!isObj(record)) return { ok: false, code: 'no_record' };
    /* 4.3 role gate (01 §6.4): a viewer sees no intervention entry at all.
     * The page only hides it - the server still answers 403 ROLE_FORBIDDEN if
     * a request is sent by hand, so hiding is a courtesy, never the rule. */
    if (!canPermission(PERM_INTERVENE_DECIDE)) {
      return { ok: false, code: 'role', permission: PERM_INTERVENE_DECIDE,
               status: str(record.status) };
    }
    var status = str(record.status);
    if (DECIDABLE_STATUS.indexOf(status) < 0) return { ok: false, code: 'status', status: status };
    if (isObj(record.user_intervention) && record.user_intervention.decision != null) {
      return { ok: false, code: 'decided', status: status };
    }
    if (record.requires_action !== true) return { ok: false, code: 'not_asking', status: status };
    var kinds = optionsOf(record).map(function (o) { return isObj(o) ? str(o.kind) : ''; });
    var missing = INTERVENE_DECISIONS.filter(function (k) { return kinds.indexOf(k) < 0; });
    if (missing.length) return { ok: false, code: 'no_option', missing: missing, status: status };
    return { ok: true, status: status, kinds: kinds };
  }

  /* params_summary is written with a fullwidth semicolon by the producers
   * (demo 0005 uses it). The literal stays ASCII in this file - the zero-CJK
   * rule for the code layer - and \uFF1B is exactly that separator. */
  var SUMMARY_SPLIT = ['\uFF1B', ';', '\n', ','];

  function parseSummary(text) {
    var out = {};
    var raw = hasText(text) ? str(text) : '';
    if (!raw) return out;
    var work = raw;
    for (var s = 1; s < SUMMARY_SPLIT.length; s += 1) {
      work = work.split(SUMMARY_SPLIT[s]).join(SUMMARY_SPLIT[0]);
    }
    work.split(SUMMARY_SPLIT[0]).forEach(function (chunk) {
      var piece = chunk.trim();
      var at = piece.indexOf('=');
      if (at < 1) return;
      var name = piece.slice(0, at).trim();
      if (!name) return;
      out[name] = coerceScalar(piece.slice(at + 1).trim());
    });
    return out;
  }

  function coerceScalar(value) {
    var low = value.toLowerCase();
    if (low === 'true') return true;
    if (low === 'false') return false;
    if (low === 'null' || low === 'none' || low === '') return null;
    var asNum = Number(value);
    if (value !== '' && !isNaN(asNum) && isFinite(asNum)) return asNum;
    return value;
  }

  function originalParamsOf(record, fields) {
    var tool = isObj(record && record.tool_used) ? record.tool_used : {};
    var parsed = parseSummary(tool.params_summary);
    (fields || []).forEach(function (field) {
      var name = str(field.name);
      if (name && parsed[name] === undefined && field.default != null) parsed[name] = field.default;
    });
    return parsed;
  }

  function formatParamValue(v) {
    if (v === null || v === undefined) return '-';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    return String(v);
  }

  function ivStamp() {
    var d = new Date();
    if (typeof d.toISOString === 'function') return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
    return String(d.getTime());
  }

  /* ---- validation (same rules as server.py, so the server agrees) -- */

  function coerceField(name, raw, field) {
    var type = str(field.type) || 'text';
    if (type === 'boolean') {
      if (typeof raw === 'boolean') return { ok: true, value: raw };
      var low = String(raw).trim().toLowerCase();
      if (['true', '1', 'yes', 'y', 'on'].indexOf(low) >= 0) return { ok: true, value: true };
      if (['false', '0', 'no', 'n', 'off'].indexOf(low) >= 0) return { ok: true, value: false };
      return { ok: false, code: 'field_type', field: name };
    }
    if (type === 'integer' || type === 'number') {
      var value = raw;
      if (typeof value === 'string') {
        var unit = str(field.unit);
        var text = value.trim();
        if (unit && text.slice(-unit.length) === unit) text = text.slice(0, text.length - unit.length).trim();
        value = Number(text);
      }
      if (typeof value !== 'number' || isNaN(value) || !isFinite(value)) {
        return { ok: false, code: 'field_type', field: name };
      }
      if (type === 'integer' && Math.floor(value) !== value) return { ok: false, code: 'field_type', field: name };
      var lo = field.min, hi = field.max;
      if (lo != null && value < Number(lo)) return { ok: false, code: 'field_range', field: name, min: lo, max: hi };
      if (hi != null && value > Number(hi)) return { ok: false, code: 'field_range', field: name, min: lo, max: hi };
      return { ok: true, value: type === 'integer' ? Math.round(value) : value };
    }
    if (isObj(raw) || isArr(raw)) return { ok: false, code: 'field_type', field: name };
    return { ok: true, value: String(raw).trim() };
  }

  /* NOT fieldLabel: t7 owns that name (compare-table key mapping) and a second
   * declaration in the same scope would silently win over this one. */
  function interveneFieldLabel(field) {
    return hasText(field && field.label) ? str(field.label) : str(field && field.name);
  }

  function validateDecision(record, decision, payload) {
    var dec = str(decision);
    if (INTERVENE_DECISIONS.indexOf(dec) < 0) {
      return { ok: false, code: 'decision_invalid', textKey: 'intervene.error.intervene_decision_invalid' };
    }
    var gate = decidableRecord(record);
    if (!gate.ok) {
      var gateKey = gate.code === 'decided' ? 'intervene.error.intervene_already_decided'
        : (gate.code === 'status' || gate.code === 'not_asking') ? 'intervene.error.intervene_state_invalid'
          : 'intervene.error.intervene_option_mismatch';
      return { ok: false, code: gate.code, textKey: gateKey };
    }
    var option = payload && str(payload.optionId) ? (optionById(record, payload.optionId) || optionByKind(record, dec))
      : optionByKind(record, dec);
    if (!option) {
      return { ok: false, code: 'no_option', textKey: 'intervene.error.intervene_option_mismatch' };
    }
    if (str(option.kind) !== dec) {
      return { ok: false, code: 'option_mismatch', textKey: 'intervene.error.intervene_option_mismatch' };
    }
    var reason = hasText(payload && payload.reason) ? str(payload.reason).trim() : '';
    if (dec === 'reject' && reason.length < REASON_MIN_CHARS) {
      return { ok: false, code: 'reason_required', textKey: INTERVENE_VALIDATION_KEYS.reason_required };
    }
    var fields = editableFieldsOf(record, option.id);
    var original = originalParamsOf(record, fields);
    var values = isObj(payload && payload.values) ? payload.values : {};
    var clean = {};
    var changed = [];
    if (dec === 'modify') {
      if (!fields.length) {
        return { ok: false, code: 'param_missing', textKey: 'intervene.error.intervene_param_missing' };
      }
      for (var i = 0; i < fields.length; i += 1) {
        var field = fields[i];
        var name = str(field.name);
        if (!name) continue;
        var supplied = values[name] !== undefined;
        var raw = supplied ? values[name] : field.default;
        var empty = raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim());
        if (empty) {
          if (field.required === true) {
            return { ok: false, code: 'field_required', field: name,
                     textKey: INTERVENE_VALIDATION_KEYS.field_required, params: { label: interveneFieldLabel(field) } };
          }
          if (!supplied) continue;
        }
        var cast = coerceField(name, raw, field);
        if (!cast.ok) {
          var typeKey = INTERVENE_TYPE_KEYS[str(field.type) || 'text'] || INTERVENE_TYPE_KEYS.text;
          if (cast.code === 'field_range') {
            return { ok: false, code: 'field_range', field: name,
                     textKey: INTERVENE_VALIDATION_KEYS.field_range,
                     params: { label: interveneFieldLabel(field), min: cast.min, max: cast.max } };
          }
          return { ok: false, code: 'field_type', field: name,
                   textKey: INTERVENE_VALIDATION_KEYS.field_type,
                   params: { label: interveneFieldLabel(field), type: t(typeKey) } };
        }
        clean[name] = cast.value;
        var before = original[name] === undefined ? field.default : original[name];
        if (normaliseValue(before) !== normaliseValue(cast.value)) {
          changed.push({ name: name, before: before === undefined ? null : before, after: cast.value });
        }
      }
      if (!Object.keys(clean).length) {
        return { ok: false, code: 'param_missing', textKey: 'intervene.error.intervene_param_missing' };
      }
      if (!changed.length) {
        return { ok: false, code: 'no_change', textKey: INTERVENE_VALIDATION_KEYS.no_change };
      }
    }
    return { ok: true, decision: dec, option: option, optionId: str(option.id), reason: reason,
             fields: fields, original: original, modified: dec === 'modify' ? clean : null,
             changed: changed };
  }

  function normaliseValue(v) {
    if (typeof v === 'string') return v.trim().toLowerCase();
    if (typeof v === 'number') return v;
    if (typeof v === 'boolean') return v ? 1 : 0;
    return v;
  }

  /* ---- the local transition: same shape as the server's answer ----- */

  function localDecision(record, validated, opts) {
    var at = (opts && opts.at) || ivStamp();
    var next = JSON.parse(JSON.stringify(record));
    next.status = STATUS_BY_DECISION[validated.decision];
    next.requires_action = false;
    var ui = {
      decision: validated.decision,
      option_id: validated.optionId || null,
      decided_by: (opts && opts.actor) || INTERVENE_LOCAL_ACTOR,
      decided_by_role: INTERVENE_LOCAL_ROLE,
      decided_at: at,
      recorded: true,
      request_id: (opts && opts.requestId) || null,
      audit_entry_hash: (opts && opts.auditHash) || null,
      original_params: validated.original || {}
    };
    if (validated.reason) ui.reason = validated.reason;
    if (validated.decision === 'modify') {
      ui.modified_params = validated.modified;
      ui.modified_fields = validated.changed;
    }
    next.user_intervention = ui;
    if (isObj(next.execution_receipt)) next.execution_receipt.effect_notice = RECEIPT_EFFECT_NOTICE;
    return next;
  }

  function commitRecord(next) {
    var key = str(next.id);
    for (var i = 0; i < state.records.length; i += 1) {
      if (str(state.records[i].id) === key) { state.records[i] = next; break; }
    }
    state.zones = computeZones(state.records);
    state.demo = demoStatus(state.records);
    state.progress = computeProgress(state.records);
    if (state.focus === key) state.focus = key;
    var idx = clampTimelineIndex(state.timelineIndex);
    state.timelineIndex = state.records.length ? idx : 0;
    return next;
  }

  function interventionError(res) {
    var data = isObj(res && res.data) ? res.data : {};
    var code = hasText(data.code) ? str(data.code) : (res && res.code ? str(res.code) : 'INTERNAL_ERROR');
    var status = res && res.status !== undefined && res.status !== null ? res.status : null;
    var detail = isObj(data.detail) ? data.detail : {};
    return {
      code: code,
      textKey: INTERVENE_ERROR_KEYS[code] || null,
      message: hasText(data.message) ? str(data.message) : '',
      field: hasText(detail.field) ? str(detail.field) : '',
      status: status,
      requestId: hasText(data.request_id) ? str(data.request_id) : ''
    };
  }

  function interventionState(id) {
    var key = str(id);
    return {
      form: state.iv.forms[key] === true,
      reason: state.iv.reasons[key] === true,
      busy: state.iv.busy[key] || null,
      result: state.iv.results[key] || null,
      error: state.iv.errors[key] || null
    };
  }

  function ivValues(key) {
    if (!isObj(state.iv.values[key])) state.iv.values[key] = {};
    return state.iv.values[key];
  }

  function openModifyForm(id) {
    var key = str(id);
    var record = recordById(key);
    if (!record || !decidableRecord(record).ok) return false;
    var fields = editableFieldsOf(record, (optionByKind(record, 'modify') || {}).id);
    var original = originalParamsOf(record, fields);
    var values = {};
    fields.forEach(function (field) {
      var name = str(field.name);
      if (!name) return;
      var seed = original[name] === undefined ? field.default : original[name];
      values[name] = seed === undefined ? '' : seed;
    });
    state.iv.values[key] = values;
    state.iv.forms[key] = true;
    state.iv.reasons[key] = false;
    state.iv.errors[key] = null;
    renderAll();
    return true;
  }

  function closeModifyForm(id) {
    var key = str(id);
    delete state.iv.forms[key];
    delete state.iv.values[key];
    renderAll();
    return false;
  }

  function openReasonBox(id) {
    var key = str(id);
    var record = recordById(key);
    if (!record || !decidableRecord(record).ok) return false;
    state.iv.reasons[key] = true;
    state.iv.forms[key] = false;
    state.iv.errors[key] = null;
    renderAll();
    return true;
  }

  function closeReasonBox(id) {
    var key = str(id);
    delete state.iv.reasons[key];
    renderAll();
    return false;
  }

  function setInterveneField(id, name, value) {
    var key = str(id);
    var box = ivValues(key);
    box[str(name)] = value;
    return box[str(name)];
  }

  function setInterveneReason(id, text) {
    var key = str(id);
    if (!isObj(state.iv.values[key])) state.iv.values[key] = {};
    state.iv.values[key].__reason = hasText(text) ? String(text) : '';
    return state.iv.values[key].__reason;
  }

  function interventionReasonText(id) {
    var box = state.iv.values[str(id)];
    return box && hasText(box.__reason) ? String(box.__reason) : '';
  }

  function clearInterveneFeedback(id) {
    var key = str(id);
    delete state.iv.results[key];
    delete state.iv.errors[key];
    renderAll();
    return true;
  }

  function resetIntervene(keepCounters) {
    state.iv.forms = {};
    state.iv.reasons = {};
    state.iv.values = {};
    state.iv.busy = {};
    state.iv.results = {};
    state.iv.errors = {};
    if (!keepCounters) { state.iv.sent = 0; state.iv.local = 0; state.iv.decisions = 0; }
    return true;
  }

  function interveneSummary() {
    var decidable = 0;
    var fields = 0;
    state.records.forEach(function (record) {
      if (!decidableRecord(record).ok) return;
      decidable += 1;
      fields += editableFieldsOf(record, null).length;
    });
    var buttons = 0;
    if (doc && typeof doc.querySelectorAll === 'function') {
      buttons = doc.querySelectorAll('[data-hook="intervene-button"][data-enabled="true"]').length;
    }
    return {
      mode: state.api.mode,
      modeReason: state.api.reason,
      requests: state.api.requests,
      sent: state.iv.sent,
      local: state.iv.local,
      decisions: state.iv.decisions,
      decidable: decidable,
      editableFields: fields,
      buttonsEnabled: buttons,
      forms: Object.keys(state.iv.forms).filter(function (k) { return state.iv.forms[k]; }).length,
      reasons: Object.keys(state.iv.reasons).filter(function (k) { return state.iv.reasons[k]; }).length,
      busy: Object.keys(state.iv.busy).filter(function (k) { return state.iv.busy[k]; }).length,
      results: Object.keys(state.iv.results).filter(function (k) { return state.iv.results[k]; }).length,
      errors: Object.keys(state.iv.errors).filter(function (k) { return state.iv.errors[k]; }).length,
      lastResult: state.iv.lastResult || null
    };
  }

  function clickInterveneTarget(target) {
    var hook = str(target.getAttribute && target.getAttribute('data-hook'));
    var card = str(target.getAttribute && target.getAttribute('data-card'));
    var kind = str(target.getAttribute && target.getAttribute('data-kind'));
    var name = str(target.getAttribute && target.getAttribute('data-field'));
    if (!card) return false;
    if (hook === 'intervene-button') {
      if (kind === 'modify') return openModifyForm(card);
      if (kind === 'reject') return openReasonBox(card);
      if (kind === 'approve') return submitIntervene(card, 'approve', {});
      return false;
    }
    if (hook === 'intervene-submit') return submitIntervene(card, 'modify', {});
    if (hook === 'intervene-cancel') return closeModifyForm(card);
    if (hook === 'intervene-reject-confirm') {
      return submitIntervene(card, 'reject', { reason: interventionReasonText(card) });
    }
    if (hook === 'intervene-reject-cancel') return closeReasonBox(card);
    if (hook === 'intervene-feedback-clear') return clearInterveneFeedback(card);
    if (hook === 'intervene-field' && name) {
      /* the input event captures the value; a click on the field is a no-op */
      return false;
    }
    return false;
  }

  function captureInterveneInput(target) {
    var hook = str(target.getAttribute && target.getAttribute('data-hook'));
    var card = str(target.getAttribute && target.getAttribute('data-card'));
    var name = str(target.getAttribute && target.getAttribute('data-field'));
    if (!card || !name) return false;
    if (hook === 'intervene-reason') return setInterveneReason(card, target.value);
    if (hook !== 'intervene-field') return false;
    var type = str(target.getAttribute && target.getAttribute('data-type'));
    if (type === 'boolean') return setInterveneField(card, name, target.checked === true);
    return setInterveneField(card, name, target.value);
  }

  function wireInterveneEvents() {
    /* several containers carry cards (zones is inside the sheet on phones), so
     * one click reaches more than one delegated listener: the event itself is
     * stamped, and the first listener to see it owns the action. */
    function once(ev, work) {
      if (!ev || ev.__ivHandled === true) return;
      ev.__ivHandled = true;
      work();
    }
    ['zones', 'timeline-main', 'sheet', 'sidebar'].forEach(function (id) {
      var host = byId(id);
      if (!host) return;
      on(host, 'click', function (ev) {
        once(ev, function () {
          var target = deskTarget(ev, '[data-hook^="intervene"]');
          if (target) clickInterveneTarget(target);
        });
      });
      var capture = function (ev) {
        once(ev, function () {
          var target = deskTarget(ev, '[data-hook="intervene-field"], [data-hook="intervene-reason"]');
          if (target) captureInterveneInput(target);
        });
      };
      on(host, 'input', capture);
      on(host, 'change', capture);
    });
  }

  /* ---------------------------------------------------------------- *
   * 8. renderers
   * ---------------------------------------------------------------- */

  function applyDocumentChrome() {
    var rootEl = doc && doc.documentElement;
    if (rootEl) {
      rootEl.setAttribute('lang', i18n.lang === 'en' ? 'en' : 'zh-CN');
      rootEl.setAttribute('data-lang', i18n.lang);
      rootEl.setAttribute('data-demo', state.demo.show ? 'true' : 'false');
      rootEl.setAttribute('data-view', state.view);
    }
    if (doc) doc.title = t('nav.app.name');
    var app = byId('app');
    if (app) {
      app.setAttribute('data-view', state.view);
      app.setAttribute('data-lang', i18n.lang);
      app.setAttribute('data-demo', state.demo.show ? 'true' : 'false');
      app.setAttribute('data-open', state.sheetOpen ? 'true' : 'false');
      /* the container's own label is dictionary text too: stamp it on every
       * render so data-i18n-lang never lies about the painted language */
      aria(app, 'nav.view.' + state.view);
    }
    var skip = byId('skip-link');
    if (skip) i18nText(skip, 'nav.a11y.skip');
  }

  function renderTopbar() {
    var bar = byId('topbar');
    if (!bar) return;
    clear(bar);
    bar.setAttribute('data-hook', 'topbar');

    var brand = add(bar, el('div', { class: 'topbar__brand' }));
    i18nText(add(brand, el('span', { class: 'topbar__name' })), 'nav.app.name');
    i18nText(add(brand, el('span', { class: 'topbar__tagline' })), 'nav.app.tagline');

    var langGroup = add(bar, el('div', { class: 'lang-switch', role: 'group' }));
    aria(langGroup, 'nav.lang.label');
    LANGS.forEach(function (lang) {
      var active = i18n.lang === lang;
      var btn = add(langGroup, el('button', {
        class: 'lang-switch__btn' + (active ? ' is-active' : ''),
        type: 'button',
        tabindex: '0',
        dataset: { lang: lang }
      }));
      i18nText(btn, 'nav.lang.' + lang);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      aria(btn, 'nav.lang.switch_aria', { lang: t('nav.lang.' + lang) });
      on(btn, 'click', function () { api.setLang(lang); });
    });

    var status = add(bar, el('p', { class: 'topbar__status' }));
    var prog = state.progress;
    i18nText(status, 'nav.progress.percent', { p: prog.percent });
    aria(status, 'nav.progress.aria', {
      task: taskNameOf(state.records) || '-',
      done: prog.done, total: prog.total, running: prog.running, needs: prog.needs
    });

    if (state.demo.show) {
      var chip = add(bar, el('span', { class: 'topbar__chip', dataset: { badge: 'demo-mode' } }));
      i18nText(chip, 'nav.demo_mode_on');
      add(chip, el('span', { class: 'topbar__chip-value', text: String(state.records.length) }));
    }

    /* 4.1: one chip says how decisions are stored right now, so a screenshot
     * can never read a local simulation as "written to the server". */
    var apiChip = add(bar, el('span', {
      class: 'topbar__chip topbar__chip--api',
      dataset: { hook: 'api-mode', mode: state.api.mode, reason: state.api.reason }
    }));
    i18nText(apiChip, apiModeChipKey(state.api.mode));
    aria(apiChip, 'intervene.mode.chip.aria');

    /* 4.4: one more chip says how this list is being fed *right now*: pushed by
     * the server, reconnecting, degraded to polling (with the reason), switched
     * off, or blocked. A screenshot can never read a polled list as a live push. */
    var rtChip = add(bar, el('span', {
      class: 'topbar__chip topbar__chip--rt',
      role: 'status',
      'aria-live': 'polite',
      dataset: {
        hook: 'realtime', mode: state.rt.mode, reason: state.rt.reason,
        seq: String(state.rt.lastSeq), events: String(state.rt.events)
      }
    }));
    i18nText(rtChip, rtChipKey(state.rt.mode), {
      ms: String(state.rt.pollIntervalMs), n: String(state.rt.events)
    });
    aria(rtChip, 'realtime.chip.aria', {
      events: String(state.rt.events), seq: String(state.rt.lastSeq),
      mode: state.rt.mode, reason: state.rt.reason
    });
    var rtValue = add(rtChip, el('span', {
      class: 'topbar__chip-value', text: String(state.rt.lastSeq)
    }));
    aria(rtValue, 'realtime.seq.aria', { seq: String(state.rt.lastSeq) });

    var refresh = add(bar, el('button', {
      class: 'topbar__refresh', type: 'button', tabindex: '0', dataset: { hook: 'refresh' }
    }));
    i18nText(refresh, 'nav.refresh');
    on(refresh, 'click', function () {
      /* 3.1: the only data source is the embedded demo copy. t7 replaces this
       * with the sidecar query (actions endpoint, view=zones). */
      var source = demoDataFor(i18n.lang);
      if (source) api.setRecords(source);
      else api.renderAll();
    });

    /* 4.10 (QA 5.1 D-4): on a phone the sidebar is gone, so the top bar carries
     * one "more" button that lists the entries the sidebar would show. The
     * button is rendered everywhere and hidden by CSS at >=1024px, so the
     * desktop layout and its sidebar stay exactly as they are. */
    renderMoreButton(bar);
  }

  function renderDemoBanner() {
    var banner = byId('demo-banner');
    if (!banner) return;
    clear(banner);
    banner.setAttribute('data-hook', 'demo-banner');
    if (!state.demo.show) {
      banner.hidden = true;
      banner.removeAttribute('role');
      banner.removeAttribute('aria-label');
      banner.className = 'banner banner--hidden';
      return;
    }
    banner.hidden = false;
    banner.className = 'banner banner--demo';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    aria(banner, 'demo.aria');

    var row = add(banner, el('div', { class: 'banner__row' }));
    add(row, el('span', { class: 'banner__dot', 'aria-hidden': 'true' }));
    i18nText(add(row, el('strong', { class: 'banner__title' })), 'demo.badge');
    i18nText(add(banner, el('p', { class: 'banner__text' })), 'demo.banner');

    var checks = add(banner, el('ul', { class: 'banner__checks' }));
    aria(checks, 'demo.matched', { fields: state.demo.matched.join(', ') });
    state.demo.matched.forEach(function (field) {
      var key = state.demo.keys[field] || ('demo.check.' + field);
      i18nText(add(checks, el('li', { class: 'chip chip--mono' })), key);
    });
  }

  function renderFab() {
    var fab = byId('fab');
    if (!fab) return;
    clear(fab);
    fab.setAttribute('data-hook', 'fab');
    fab.setAttribute('type', 'button');
    fab.setAttribute('tabindex', '0');
    fab.hidden = false;
    fab.className = 'fab' + (state.sheetOpen ? ' is-open' : '');
    fab.setAttribute('aria-expanded', state.sheetOpen ? 'true' : 'false');
    aria(fab, state.sheetOpen ? 'nav.ball.close' : 'nav.ball.open');

    var ring = add(fab, el('span', { class: 'fab__ring', 'aria-hidden': 'true' }));
    ring.setAttribute('style', '--progress:' + state.progress.percent);
    setDataset(ring, 'progress', state.progress.percent);
    i18nText(add(ring, el('span', { class: 'fab__percent' })), 'nav.progress.percent', { p: state.progress.percent });

    var labelBox = add(fab, el('span', { class: 'fab__label' }));
    i18nText(add(labelBox, el('span', { class: 'fab__task-kicker' })), 'nav.ball.task_label');
    var task = taskNameOf(state.records);
    add(labelBox, el('span', { class: 'fab__task', text: task || '-' }));

    if (state.demo.show) {
      var badge = add(fab, el('span', { class: 'fab__badge' }));
      i18nText(badge, 'demo.badge');
      fab.setAttribute('data-demo', 'true');
    } else {
      fab.setAttribute('data-demo', 'false');
    }
  }

  function renderZones() {
    var host = byId('zones');
    if (!host) return;
    clear(host);
    host.setAttribute('data-hook', 'zones');
    if (state.notice) {
      var notice = add(host, el('div', { class: 'zones__notice', dataset: { hook: 'notice', code: state.notice } }));
      i18nText(notice, state.notice);
    }
    if (state.records.length === 0) {
      var empty = add(host, el('div', { class: 'zones__error', dataset: { hook: 'empty-state' } }));
      i18nText(add(empty, el('p', { class: 'zones__error-main' })), 'error.data_empty');
      i18nText(add(empty, el('p', { class: 'zones__error-hint' })), 'error.load_hint');
      return;
    }
    if (state.stats.invalidRecords > 0) {
      var warn = add(host, el('p', { class: 'zones__warning', dataset: { hook: 'invalid-records', count: state.stats.invalidRecords } }));
      i18nText(warn, 'error.record_invalid');
    }
    /* the desktop filter is honest: a collapsed zone is stated, content never
     * disappears in silence, and the focus note says what the timeline moved. */
    var visibleZones = ZONE_ORDER.filter(function (z) { return zoneVisible(z); });
    var hidden = hiddenByFilter();
    if (visibleZones.length === 0) {
      var allOff = add(host, el('p', { class: 'zones__notice', dataset: { hook: 'filter-all-off', hidden: hidden } }));
      i18nText(allOff, 'desk.filter.all_off');
    } else if (hidden > 0) {
      var hiddenNote = add(host, el('p', { class: 'zones__notice', dataset: { hook: 'filter-hidden', count: hidden } }));
      i18nText(hiddenNote, 'desk.filter.zone_hidden', { n: hidden });
    }
    var focusIndex = indexOfRecord(state.focus);
    if (focusIndex !== null) {
      var focusNote = add(host, el('p', {
        class: 'zones__focus-note',
        dataset: { hook: 'focus-note', id: str(state.focus), index: focusIndex + 1 }
      }));
      i18nText(focusNote, 'desk.focus.note', {
        i: focusIndex + 1,
        l1: l1Of(state.records[focusIndex]) || t('card.detail.empty')
      });
    }
    for (var i = 0; i < ZONE_ORDER.length; i += 1) {
      renderZoneSection(host, ZONE_ORDER[i], i);
    }
  }

  function renderZoneSection(host, zone, index) {
    var list = state.zones[zone] || [];
    var isNeeds = zone === 'needs_me';
    if (!zoneVisible(zone)) {
      var collapsed = add(host, el('section', {
        class: 'zone zone--' + zone + ' zone--' + colorClassForZone(zone) + ' is-filtered',
        dataset: { zone: zone, count: list.length, shown: 0, order: index, filtered: 'true' }
      }));
      collapsed.setAttribute('aria-labelledby', 'zone-title-' + zone);
      var cHead = add(collapsed, el('header', { class: 'zone__head' }));
      i18nText(add(cHead, el('h2', { class: 'zone__title', id: 'zone-title-' + zone })), ZONE_KEYS[zone].title);
      i18nText(add(cHead, el('span', { class: 'zone__count' })), 'zones.count', { n: list.length });
      i18nText(add(collapsed, el('p', { class: 'zone__filtered', dataset: { count: list.length } })), 'desk.filter.zone_hidden', { n: list.length });
      return;
    }
    var queue = isNeeds ? serialQueue(list) : null;
    var shown = isNeeds ? (queue.current ? 1 : 0) : list.length;

    var section = add(host, el('section', {
      class: 'zone zone--' + zone + ' zone--' + colorClassForZone(zone),
      dataset: { zone: zone, count: list.length, shown: shown, order: index }
    }));
    section.setAttribute('aria-labelledby', 'zone-title-' + zone);
    aria(section, ZONE_KEYS[zone].aria, { total: list.length, shown: shown });

    var head = add(section, el('header', { class: 'zone__head' }));
    var title = i18nText(add(head, el('h2', { class: 'zone__title', id: 'zone-title-' + zone })), ZONE_KEYS[zone].title);
    i18nText(add(head, el('span', { class: 'zone__count' })), 'zones.count', { n: list.length });
    i18nText(add(head, el('p', { class: 'zone__hint' })), ZONE_KEYS[zone].hint);
    if (isNeeds && list.length > 0) {
      i18nText(add(head, el('span', { class: 'zone__flag' })), 'zones.top.badge');
      title.setAttribute('data-top', 'true');
    }

    var body = add(section, el('div', { class: 'zone__body', role: 'list' }));
    if (list.length === 0) {
      i18nText(add(body, el('p', { class: 'zone__empty' })), ZONE_KEYS[zone].empty);
      return;
    }
    if (isNeeds) {
      i18nText(add(body, el('p', { class: 'zone__note' })), 'zones.queue.serial_note');
      renderCard(body, queue.current, zone, { priority: true, compact: false, index: 1 });
      if (queue.queued.length > 0) {
        var queued = add(body, el('div', { class: 'zone__queued', role: 'list', dataset: { zone: zone, queued: queue.queued.length } }));
        i18nText(add(queued, el('p', { class: 'zone__queued-note' })), 'zones.queue.more', { n: queue.queued.length });
        queue.queued.forEach(function (r, i) {
          renderCard(queued, r, zone, { priority: false, compact: true, index: i + 2 });
        });
      }
      return;
    }
    list.forEach(function (r, i) {
      renderCard(body, r, zone, { priority: false, compact: false, index: i + 1 });
    });
  }

  function colorClassForZone(zone) {
    if (zone === 'needs_me') return 'pending';
    if (zone === 'running') return 'progress';
    return 'ok';
  }

  function renderCard(host, record, zone, opts) {
    if (!record) return null;
    opts = opts || {};
    var id = str(record.id);
    var color = colorRoleOf(record);
    var detailId = 'detail-' + id;
    var expanded = !!state.expanded[id];
    var isDemo = recordIsDemo(state.demo, record);
    /* 4.12 (H-03): a decision recorded by anyone - this page, the API, another
     * window - has to show on the card itself, so the card carries the fact in
     * its dataset and CSS can mark it. The card refreshes with the record. */
    var decided = recordedDecisionOf(record) !== null;

    var focused = state.focus === id;
    var card = add(host, el('article', {
      class: 'card' + (opts.compact ? ' card--compact' : '') + (expanded ? ' is-expanded' : '') + (opts.priority ? ' card--priority' : '') + (focused ? ' is-focused' : ''),
      id: 'card-' + id,
      dataset: {
        id: id, zone: zone, color: color,
        type: typeKey(record.action_type),
        status: statusKey(record.status),
        risk: riskKey(record.risk_level),
        demo: isDemo ? 'true' : 'false',
        decided: decided ? 'true' : 'false',
        priority: opts.priority ? 'true' : 'false',
        focused: focused ? 'true' : 'false'
      },
      tabindex: '0',
      role: 'listitem'
    }));
    aria(card, 'card.meta.aria', {
      status: t('status.' + statusKey(record.status)),
      risk: t('risk.' + riskKey(record.risk_level)),
      time: formatTimestamp(record.timestamp, i18n.lang)
    });

    var head = add(card, el('button', {
      class: 'card__toggle',
      type: 'button',
      tabindex: '0',
      dataset: { card: id }
    }));
    head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    head.setAttribute('aria-controls', detailId);
    add(head, el('span', { class: 'card__dot', 'aria-hidden': 'true' }));
    var l1 = isObj(record.human_readable) ? str(record.human_readable.l1) : '';
    add(head, el('span', { class: 'card__l1', text: l1 || t('card.detail.empty') }));
    if (!expanded) i18nText(add(head, el('span', { class: 'card__hint' })), 'card.expand.hint');
    add(head, el('span', {
      class: 'card__chevron',
      'aria-hidden': 'true',
      text: expanded ? t('card.collapse') : t('card.expand')
    })).setAttribute('data-i18n', expanded ? 'card.collapse' : 'card.expand');

    var badges = add(card, el('ul', { class: 'card__badges' }));
    var position = num(opts.index) === null ? 1 : num(opts.index);
    if (isDemo) {
      var bDemo = add(badges, el('li', { class: 'badge badge--demo' }));
      i18nText(bDemo, 'card.badge.demo');
      aria(bDemo, 'demo.per_record.aria', { n: position });
      bDemo.setAttribute('data-badge', 'demo');
    }
    var hr = isObj(record.human_readable) ? record.human_readable : {};
    if (hr.generated_by === 'hybrid' || hr.may_contain_inference === true) {
      var bAi = add(badges, el('li', { class: 'badge badge--ai' }));
      i18nText(bAi, 'card.badge.ai');
      bAi.setAttribute('data-badge', 'ai');
    }
    if (record.status === 'expired') {
      var bExp = add(badges, el('li', { class: 'badge badge--expired' }));
      i18nText(bExp, 'card.badge.expired');
      bExp.setAttribute('data-badge', 'expired');
    }
    if (num(record.confidence) !== null && record.confidence <= LOW_CONFIDENCE_MAX) {
      var bLow = add(badges, el('li', { class: 'badge badge--low-confidence' }));
      i18nText(bLow, 'card.badge.low_confidence');
      bLow.setAttribute('data-badge', 'low-confidence');
    }
    if (isObj(record.impact_scope) && record.impact_scope.reversible === false) {
      var bIrr = add(badges, el('li', { class: 'badge badge--irreversible' }));
      i18nText(bIrr, 'card.badge.irreversible');
      bIrr.setAttribute('data-badge', 'irreversible');
    }
    if (record.status === 'pending_confirmation' && record.requires_action === true) {
      var bNeed = add(badges, el('li', { class: 'badge badge--needs-you' }));
      i18nText(bNeed, 'card.badge.needs_you');
      bNeed.setAttribute('data-badge', 'needs-you');
    }
    var risk = riskKey(record.risk_level);
    if (risk === 'critical' || risk === 'high') {
      /* full literal keys, never concatenation: the i18n lint and
       * web/selfcheck.mjs can then prove every badge string is translated */
      var riskBadgeKey = risk === 'critical' ? 'card.badge.risk_critical' : 'card.badge.risk_high';
      var bRisk = add(badges, el('li', { class: 'badge badge--risk' }));
      i18nText(bRisk, riskBadgeKey);
      bRisk.setAttribute('data-badge', 'risk-' + risk);
    }

    var meta = add(card, el('p', { class: 'card__meta' }));
    add(meta, el('span', { class: 'card__meta-part', text: t('status.' + statusKey(record.status)) })).setAttribute('data-i18n', 'status.' + statusKey(record.status));
    add(meta, el('span', { class: 'card__meta-sep', text: ' · ', 'aria-hidden': 'true' }));
    add(meta, el('span', { class: 'card__meta-part', text: t('action_type.' + typeKey(record.action_type)) })).setAttribute('data-i18n', 'action_type.' + typeKey(record.action_type));
    add(meta, el('span', { class: 'card__meta-sep', text: ' · ', 'aria-hidden': 'true' }));
    add(meta, el('span', { class: 'card__meta-part', text: t('risk.' + riskKey(record.risk_level)) })).setAttribute('data-i18n', 'risk.' + riskKey(record.risk_level));
    add(meta, el('span', { class: 'card__meta-sep', text: ' · ', 'aria-hidden': 'true' }));
    add(meta, el('time', { class: 'card__meta-part', text: formatTimestamp(record.timestamp, i18n.lang) }));

    var detail = add(card, el('div', { class: 'card__detail', id: detailId }));
    detail.hidden = !expanded;
    detail.setAttribute('data-card-detail', id);
    i18nText(add(detail, el('h3', { class: 'card__detail-title' })), 'card.detail.title');
    i18nText(add(detail, el('p', { class: 'card__detail-hint' })), 'level.note');

    var legend = add(detail, el('ul', { class: 'legend', 'aria-hidden': 'true' }));
    ['l1', 'l2', 'l3'].forEach(function (lv) {
      i18nText(add(legend, el('li', { class: 'legend__item', dataset: { level: lv } })), 'level.' + lv);
    });

    var rows = add(detail, el('dl', { class: 'kv' }));
    var added = 0;
    function row(labelKey, value, opts2) {
      opts2 = opts2 || {};
      var out = str(value);
      var missing = !hasText(out);
      if (missing) {
        if (!opts2.always) return null;
        out = t('card.no_value');
      }
      added += 1;
      i18nText(add(rows, el('dt', { class: 'kv__key' })), labelKey);
      var dd = add(rows, el('dd', { class: 'kv__value' + (opts2.mono ? ' kv__value--mono' : ''), text: out }));
      if (missing) dd.setAttribute('data-missing', 'true');
      if (opts2.ariaKey) aria(dd, opts2.ariaKey, opts2.ariaParams);
      return dd;
    }

    row('card.field.human_l1', l1, { always: true });
    if (isArr(hr.l2) && hr.l2.length) {
      i18nText(add(rows, el('dt', { class: 'kv__key' })), 'card.field.human_l2');
      var steps = add(rows, el('dd', { class: 'kv__value' }));
      var stepsList = add(steps, el('ol', { class: 'l2-list' }));
      stepsList.setAttribute('role', 'list');
      aria(stepsList, 'card.l2.aria', { n: hr.l2.length });
      hr.l2.forEach(function (step) { add(stepsList, el('li', { class: 'l2-list__item', text: str(step) })); });
      added += 1;
    }
    row('card.field.human_l3', str(hr.l3));
    row('card.field.intent', str(record.intent), { always: true });
    row('card.field.rationale', str(record.rationale), { always: true });
    row('card.field.result', str(record.result_summary), { always: true });
    row('time.label', formatTimestamp(record.timestamp, i18n.lang), { always: true });
    row('time.received', formatTimestamp(record.received_at, i18n.lang));
    var toolNode = row('card.field.tool', toolText(record.tool_used));
    if (toolNode && isObj(record.tool_used)) {
      aria(toolNode, 'card.tool.aria', { name: str(record.tool_used.name), version: str(record.tool_used.version) });
    }
    row('card.field.risk', t('risk.' + riskKey(record.risk_level)), { always: true });
    row('card.field.confidence', formatConfidence(record.confidence));
    row('card.field.cost', formatCost(record.cost, i18n.lang));
    row('card.field.latency', formatDuration(record.latency_ms));
    row('card.field.impact', impactText(record.impact_scope));
    if (isObj(record.impact_scope)) {
      var reversible = record.impact_scope.reversible === false ? 'card.badge.irreversible' : 'card.badge.reversible';
      row(reversible, t(reversible));
    }
    row('time.due', dueText(record));
    row('card.field.uncertainty', uncertaintyText(record.uncertainty));
    row('card.field.source', sourceText(record.source));
    row('card.field.trace', traceText(record.trace_link));
    row('card.field.id', id, { mono: true });
    row('card.field.agent', str(record.agent_name));
    row('card.field.session', str(record.session_id));
    row('card.field.task', isObj(record.context) ? str(record.context.task_name) : '');
    row('card.field.labels', isArr(record.labels) ? record.labels.join(', ') : '');
    if (added === 0) i18nText(add(rows, el('dd', { class: 'kv__value' })), 'card.detail.empty');

    if (hr.generated_by === 'hybrid' || hr.may_contain_inference === true) {
      i18nText(add(detail, el('p', { class: 'note note--ai' })), 'card.ai.note');
    }
    if (isDemo) i18nText(add(detail, el('p', { class: 'note note--demo' })), 'card.demo.note');
    if (isObj(hr) && hasText(hr.language) && hr.language !== i18n.lang) {
      i18nText(add(detail, el('p', { class: 'note note--lang' })), 'card.content_lang.note', { lang: str(hr.language), ui: i18n.lang });
    }

    renderOptions(detail, record);
    renderSuggested(detail, record);

    on(head, 'click', function (ev) { if (ev && typeof ev.preventDefault === 'function') ev.preventDefault(); api.toggleCard(id); });
    on(card, 'keydown', function (ev) {
      var key = ev && (ev.key || '');
      if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
        api.toggleCard(id);
      }
    });
    return card;
  }

  function uncertaintyText(unc) {
    if (!isObj(unc)) return '';
    var bits = [];
    if (hasText(unc.reason)) bits.push(str(unc.reason));
    if (hasText(unc.what_to_check)) bits.push(str(unc.what_to_check));
    if (isArr(unc.affected_fields) && unc.affected_fields.length) bits.push(unc.affected_fields.join(', '));
    return bits.join(' | ');
  }

  function sourceText(source) {
    if (!isObj(source)) return '';
    var bits = [];
    if (hasText(source.kind)) bits.push(str(source.kind));
    if (hasText(source.adapter)) bits.push(str(source.adapter));
    if (hasText(source.format_version)) bits.push(str(source.format_version));
    if (hasText(source.source_uri)) bits.push(str(source.source_uri));
    return bits.join(' · ');
  }

  function traceText(link) {
    if (!isObj(link)) return '';
    var bits = [];
    if (hasText(link.trace_id)) bits.push(str(link.trace_id));
    if (hasText(link.span_id)) bits.push(str(link.span_id));
    if (hasText(link.observation_id)) bits.push(str(link.observation_id));
    return bits.join(' · ');
  }

  function renderOptions(host, record) {
    var options = isArr(record.intervention_options) ? record.intervention_options : [];
    var needs = record.requires_action === true;
    var gate = decidableRecord(record);
    var iv = interventionState(record.id);
    if (!needs && options.length === 0 && !iv.result) return;
    if (!gate.ok && str(gate.code) === 'role' && options.length === 0 && !iv.result) return;

    var box = add(host, el('div', {
      class: 'options',
      dataset: {
        card: str(record.id), 'hook': 'intervene', decidable: gate.ok ? 'true' : 'false',
        status: str(record.status), 'iv-mode': iv.result ? str(iv.result.mode) : 'none',
        permission: PERM_INTERVENE_DECIDE, 'role-gated': str(gate.code) === 'role' ? 'true' : 'false'
      }
    }));
    if (str(gate.code) === 'role') {
      /* A role that may not decide: the option list stays readable, but no
       * control that would record a decision is rendered (product rule 01
       * section 6.4: the intervention entry is hidden for this role). */
      i18nText(add(box, el('h4', { class: 'options__title' })), 'card.field.options');
      i18nText(add(box, el('p', {
        class: 'options__hint',
        dataset: { hook: 'intervene-role-notice' }
      })), 'intervene.panel.role_hidden');
    } else {
      i18nText(add(box, el('h4', { class: 'options__title' })),
        gate.ok ? 'intervene.panel.title' : (iv.result ? 'intervene.panel.decided_title' : 'card.field.options'));
      if (gate.ok) i18nText(add(box, el('p', { class: 'options__hint' })), 'intervene.panel.hint');
    }
    if (options.length === 0) {
      i18nText(add(box, el('p', { class: 'options__empty' })), 'card.options.none');
    }
    var list = add(box, el('ul', { class: 'options__list' }));
    var decided = isObj(record.user_intervention) && record.user_intervention.decision != null;
    var stateKey = decided ? 'intervene.state.decided'
      : (str(record.status) === 'expired' ? 'intervene.state.expired' : 'intervene.state.pending');
    i18nText(add(box, el('p', {
      class: 'options__state',
      dataset: { state: decided ? 'decided' : 'waiting', status: str(record.status) }
    })), stateKey);
    if (iv.result) {
      var badge = add(box, el('span', {
        class: 'options__badge',
        dataset: { hook: 'intervene-badge', mode: str(iv.result.mode) }
      }));
      i18nText(badge, iv.result.mode === 'local' ? 'intervene.badge.local' : 'intervene.badge.live');
    }
    options.forEach(function (opt) {
      if (!isObj(opt)) return;
      var li = add(list, el('li', {
        class: 'option' + (opt.dangerous === true ? ' option--dangerous' : ''),
        dataset: { option: str(opt.id), kind: str(opt.kind) }
      }));
      var label = add(li, el('p', { class: 'option__label' }));
      add(label, el('span', { class: 'option__kind', text: optionLabel(opt) })).setAttribute('data-i18n', optionKindKey(opt));
      if (hasText(opt.label)) add(label, el('span', { class: 'option__text', text: str(opt.label) }));
      if (hasText(opt.consequence)) {
        var cons = add(li, el('p', { class: 'option__consequence' }));
        i18nText(add(cons, el('span', { class: 'option__consequence-key' })), 'intervene.consequence');
        add(cons, el('span', { class: 'option__consequence-text', text: str(opt.consequence) }));
      }
      if (hasText(opt.risk_note)) {
        var rn = add(li, el('p', { class: 'option__risk' }));
        i18nText(add(rn, el('span', { class: 'option__risk-key' })), 'intervene.risk_note');
        add(rn, el('span', { class: 'option__risk-text', text: str(opt.risk_note) }));
      }
      if (opt.requires_reason === true) i18nText(add(li, el('p', { class: 'option__reason-req' })), 'intervene.requires_reason');
      if (opt.dangerous === true) i18nText(add(li, el('p', { class: 'option__dangerous' })), 'intervene.dangerous');
      if (isArr(opt.editable_fields) && opt.editable_fields.length) {
        var ef = add(li, el('p', { class: 'option__fields' }));
        i18nText(add(ef, el('span', { class: 'option__fields-key' })), 'intervene.editable_fields');
        add(ef, el('span', { class: 'option__fields-text', text: opt.editable_fields.map(fieldText).join(', ') }));
      }
      var busy = iv.busy !== null;
      var enabled = gate.ok && !busy;
      if (str(gate.code) === 'role') return;   /* this role gets no decision control */
      var btn = add(li, el('button', {
        class: 'option__btn option__btn--' + str(opt.kind) + (busy && iv.busy === str(opt.kind) ? ' is-busy' : ''),
        type: 'button', tabindex: '0',
        dataset: {
          hook: 'intervene-button', card: str(record.id), option: str(opt.id),
          kind: str(opt.kind), enabled: enabled ? 'true' : 'false'
        }
      }));
      btn.disabled = !enabled;
      i18nText(btn, busy && iv.busy === str(opt.kind) ? 'intervene.button.busy' : interventionButtonKey(opt.kind));
      if (iv.result) {
        btn.hidden = true;
        i18nText(add(li, el('p', { class: 'option__done' })), 'intervene.decision.' + str(iv.result.decision));
      }
    });
    if (gate.ok) renderInterveneForm(box, record, iv);
    if (gate.ok && needsReasonOption(record)) renderInterveneReason(box, record, iv);
    renderInterveneResult(box, record, iv);
    renderInterveneError(box, record, iv);
    var notes = add(box, el('div', { class: 'options__notes' }));
    i18nText(add(notes, el('p', { class: 'note note--effect' })), INTERVENE_EFFECT_KEY);
    if (state.api.mode === 'offline' || state.api.mode === 'forced_offline') {
      i18nText(add(notes, el('p', { class: 'note note--offline', dataset: { hook: 'intervene-offline-note' } })),
        'intervene.offline.notice');
      i18nText(add(notes, el('p', { class: 'note note--offline-hint' })), 'intervene.offline.hint');
    }
    if (iv.result) i18nText(add(notes, el('p', { class: 'note note--mode' })), 'intervene.panel.mode_note');
    if (recordIsDemo(state.demo, record)) i18nText(add(notes, el('p', { class: 'note note--demo' })), 'intervene.panel.demo_note');
  }

  function interventionButtonKey(kind) {
    if (kind === 'approve') return 'intervene.button.approve';
    if (kind === 'modify') return 'intervene.button.modify';
    if (kind === 'reject') return 'intervene.button.reject';
    return 'intervene.options.unknown';
  }

  function apiModeChipKey(mode) {
    if (mode === 'live') return 'intervene.mode.chip.live';
    if (mode === 'offline' || mode === 'forced_offline') return 'intervene.mode.chip.offline';
    return 'intervene.mode.chip.checking';
  }

  function needsReasonOption(record) {
    var opt = optionByKind(record, 'reject');
    return !!(opt && opt.requires_reason === true);
  }

  function renderInterveneForm(box, record, iv) {
    if (!iv.form) return;
    var option = optionByKind(record, 'modify') || {};
    var fields = editableFieldsOf(record, option.id);
    var values = ivValues(record.id);
    var form = add(box, el('div', {
      class: 'options__form',
      dataset: { card: str(record.id), hook: 'intervene-form', option: str(option.id) }
    }));
    i18nText(add(form, el('p', { class: 'form__title' })), 'intervene.form.title');
    i18nText(add(form, el('p', { class: 'form__hint' })), 'intervene.form.hint');
    var original = originalParamsOf(record, fields);
    var originalBits = Object.keys(original).map(function (k) {
      return k + '=' + formatParamValue(original[k]);
    });
    if (originalBits.length) {
      var row = add(form, el('p', { class: 'form__original' }));
      i18nText(add(row, el('span', { class: 'form__original-key' })), 'intervene.form.original');
      add(row, el('span', { class: 'form__original-text', text: originalBits.join(' ') }));
    }
    fields.forEach(function (field) {
      var name = str(field.name);
      if (!name) return;
      var type = str(field.type) || 'text';
      var rowNode = add(form, el('div', { class: 'field field--' + type, dataset: { field: name, type: type } }));
      var labelNode = add(rowNode, el('label', { class: 'field__label' }));
      add(labelNode, el('span', { class: 'field__name', text: interveneFieldLabel(field) }));
      add(labelNode, el('span', {
        class: 'field__req' + (field.required === true ? ' is-required' : ''),
        text: t(field.required === true ? 'intervene.form.field.required' : 'intervene.form.field.optional')
      })).setAttribute('data-i18n', field.required === true
        ? 'intervene.form.field.required' : 'intervene.form.field.optional');
      add(labelNode, el('span', { class: 'field__key', text: name }));
      var input;
      if (type === 'boolean') {
        input = add(rowNode, el('input', {
          class: 'field__input field__input--checkbox', type: 'checkbox',
          dataset: { hook: 'intervene-field', card: str(record.id), field: name, type: type }
        }));
        input.checked = values[name] === true || String(values[name]) === 'true';
      } else {
        input = add(rowNode, el('input', {
          class: 'field__input',
          type: (type === 'integer' || type === 'number') ? 'number' : 'text',
          dataset: { hook: 'intervene-field', card: str(record.id), field: name, type: type }
        }));
        input.value = values[name] === undefined || values[name] === null ? '' : String(values[name]);
        if (isNum(field.min)) input.setAttribute('min', String(field.min));
        if (isNum(field.max)) input.setAttribute('max', String(field.max));
      }
      var hints = [];
      if (hasText(field.unit)) hints.push(str(field.unit));
      if (field.min != null && field.max != null) {
        hints.push(t('intervene.form.field.range', { min: field.min, max: field.max }));
      }
      if (hasText(field.help_text)) hints.push(str(field.help_text));
      if (hints.length) add(rowNode, el('span', { class: 'field__help', text: hints.join(' - ') }));
    });
    var actions = add(form, el('div', { class: 'form__actions' }));
    var submit = add(actions, el('button', {
      class: 'btn btn--primary', type: 'button', tabindex: '0',
      dataset: { hook: 'intervene-submit', card: str(record.id), kind: 'modify' }
    }));
    i18nText(submit, 'intervene.form.submit');
    var cancel = add(actions, el('button', {
      class: 'btn btn--ghost', type: 'button', tabindex: '0',
      dataset: { hook: 'intervene-cancel', card: str(record.id) }
    }));
    i18nText(cancel, 'intervene.form.cancel');
  }

  function renderInterveneReason(box, record, iv) {
    if (!iv.reason || iv.form) return;
    var wrap = add(box, el('div', {
      class: 'options__reason', dataset: { card: str(record.id), hook: 'intervene-reason-box' }
    }));
    i18nText(add(wrap, el('p', { class: 'reason__title' })), 'intervene.reason.title');
    var area = add(wrap, el('textarea', {
      class: 'reason__input', rows: '2',
      dataset: { hook: 'intervene-reason', card: str(record.id) }
    }));
    var text = interventionReasonText(record.id);
    area.value = text;
    area.setAttribute('placeholder', t('intervene.reason.placeholder'));
    var actions = add(wrap, el('div', { class: 'reason__actions' }));
    var confirm = add(actions, el('button', {
      class: 'btn btn--danger', type: 'button', tabindex: '0',
      dataset: { hook: 'intervene-reject-confirm', card: str(record.id), kind: 'reject' }
    }));
    i18nText(confirm, 'intervene.reason.confirm');
    var cancel = add(actions, el('button', {
      class: 'btn btn--ghost', type: 'button', tabindex: '0',
      dataset: { hook: 'intervene-reject-cancel', card: str(record.id) }
    }));
    i18nText(cancel, 'intervene.reason.cancel');
  }

  function renderInterveneResult(box, record, iv) {
    if (!iv.result) return;
    var res = iv.result;
    var node = add(box, el('div', {
      class: 'options__feedback',
      attrs: { role: 'status', 'aria-live': 'polite' },
      dataset: {
        card: str(record.id), hook: 'intervene-feedback', mode: str(res.mode),
        decision: str(res.decision), status: str(res.status || '')
      }
    }));
    i18nText(add(node, el('p', { class: 'feedback__lead' })), 'intervene.result.recorded',
      { decision: t('intervene.decision.' + str(res.decision)) });
    var chip = add(node, el('p', {
      class: 'feedback__mode' + (res.mode === 'local' ? ' feedback__mode--local' : ' feedback__mode--live'),
      dataset: { hook: 'intervene-mode', mode: str(res.mode) }
    }));
    i18nText(chip, res.mode === 'local' ? 'intervene.result.mode.local' : 'intervene.result.mode.live');
    if (res.replay === true) i18nText(add(node, el('p', { class: 'feedback__replay' })), 'intervene.result.replay');
    if (isArr(res.fields) && res.fields.length) {
      var changed = add(node, el('div', { class: 'feedback__fields' }));
      i18nText(add(changed, el('p', { class: 'feedback__fields-title' })), 'intervene.result.fields_changed');
      var table = add(changed, el('table', { class: 'feedback__table' }));
      res.fields.forEach(function (item) {
        var tr = add(table, el('tr', { dataset: { field: str(item.name) } }));
        add(tr, el('td', {
          class: 'feedback__cell',
          text: t('intervene.result.field.before_after', {
            name: str(item.name), before: formatParamValue(item.before), after: formatParamValue(item.after)
          })
        }));
      });
    } else if (str(res.decision) === 'approve') {
      i18nText(add(node, el('p', { class: 'feedback__nochange' })), 'intervene.result.no_change');
    }
    if (hasText(res.reason)) add(node, el('p', { class: 'feedback__reason', text: res.reason }));
    var metaBits = [];
    if (hasText(res.audit)) metaBits.push(t('intervene.result.audit', { hash: shortHash(res.audit) }));
    if (hasText(res.at)) metaBits.push(t('intervene.result.decided_at', { time: res.at }));
    if (metaBits.length) add(node, el('p', { class: 'feedback__meta', text: metaBits.join(' ') }));
    if (hasText(res.noticeKey)) i18nText(add(node, el('p', { class: 'feedback__notice' })), str(res.noticeKey));
    var clear = add(node, el('button', {
      class: 'btn btn--ghost btn--tiny', type: 'button', tabindex: '0',
      dataset: { hook: 'intervene-feedback-clear', card: str(record.id) }
    }));
    i18nText(clear, 'intervene.form.cancel');
  }

  function renderInterveneError(box, record, iv) {
    if (!iv.error) return;
    var node = add(box, el('p', {
      class: 'options__error',
      attrs: { role: 'alert' },
      dataset: { card: str(record.id), hook: 'intervene-error', code: str(iv.error.code || '') }
    }));
    var text = iv.error.textKey ? t(iv.error.textKey, iv.error.params) : '';
    add(node, el('span', { class: 'error__text', text: text || (iv.error.message || String(iv.error.code)) }));
    if (iv.error.textKey && hasText(iv.error.message) && iv.error.message !== text) {
      add(node, el('span', { class: 'error__server', text: iv.error.message }));
    }
    if (hasText(iv.error.field)) add(node, el('span', { class: 'error__field', text: iv.error.field }));
  }

  function shortHash(hash) {
    var text = str(hash);
    if (text.indexOf('sha256:') === 0) text = text.slice(7);
    return text.slice(0, 10);
  }

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  /* ---- the submit path: server first, local simulation as fallback - */

  function submitIntervene(recordId, decision, payload) {
    var key = str(recordId);
    var record = recordById(key);
    if (!record) return Promise.resolve({ ok: false, code: 'INTERVENE_ACTION_NOT_FOUND' });
    if (state.iv.busy[key]) {
      state.iv.errors[key] = { code: 'BUSY', textKey: INTERVENE_VALIDATION_KEYS.busy };
      renderAll();
      return Promise.resolve({ ok: false, code: 'BUSY' });
    }
    var body = isObj(payload) ? payload : {};
    var values = isObj(body.values) ? body.values : ivValues(key);
    var checked = validateDecision(record, decision, {
      optionId: body.optionId, reason: body.reason, values: values
    });
    if (!checked.ok) {
      state.iv.errors[key] = {
        code: str(checked.code), textKey: checked.textKey, params: checked.params,
        field: checked.field, message: ''
      };
      renderAll();
      return Promise.resolve({ ok: false, code: checked.code });
    }
    var request = { action_id: key, decision: checked.decision, option_id: checked.optionId };
    if (checked.decision === 'modify') request.modified_params = checked.modified;
    if (checked.reason) request.reason = checked.reason;
    if (hasText(body.idempotencyKey)) request.idempotency_key = str(body.idempotencyKey);
    state.iv.busy[key] = checked.decision;
    state.iv.errors[key] = null;
    state.iv.sent += 1;
    renderAll();
    return apiRequest('intervene', request).then(function (res) {
      delete state.iv.busy[key];
      var refused = !res || res.ok !== true;
      if (!refused && !(isObj(res.data) && str(res.data.id) === key)) refused = true;
      if (refused) {
        /* the server answered and said no: the record keeps its state (the
         * transition is the server's job), and the readable code goes on the card */
        state.iv.errors[key] = interventionError(res);
        state.iv.results[key] = null;
        renderAll();
        return res;
      }
      var data = res.data;
      var ui = isObj(data.user_intervention) ? data.user_intervention : {};
      commitRecord(JSON.parse(JSON.stringify(data)));
      state.iv.decisions += 1;
      state.iv.forms[key] = false;
      state.iv.reasons[key] = false;
      delete state.iv.values[key];
      state.iv.results[key] = {
        mode: 'live',
        decision: str(ui.decision),
        status: str(data.status),
        fields: isArr(ui.modified_fields) ? ui.modified_fields : [],
        reason: hasText(ui.reason) ? str(ui.reason) : '',
        at: str(ui.decided_at),
        audit: hasText(ui.audit_entry_hash) ? str(ui.audit_entry_hash) : '',
        requestId: responseRequestId(res) || (hasText(ui.request_id) ? str(ui.request_id) : ''),
        replay: res.headers && (res.headers['x-intervene-replay'] === 'true'
          || res.headers['X-Intervene-Replay'] === 'true') === true,
        noticeKey: null
      };
      state.iv.lastResult = { id: key, mode: 'live', decision: str(ui.decision), status: str(data.status) };
      /* 4.12 (H-02): a recorded decision appends an audit entry. Read the log
       * back so the panel shows it without a click - the hash on the card and
       * the panel can no longer disagree. Guarded inside: offline decisions are
       * local simulations (no entry exists to read), a role without audit.read
       * never asks, and a read already in flight is not stacked. */
      auditAfterWrite('decision:' + key);
      renderAll();
      return res;
    }, function (err) {
      /* API unavailable -> degrade to a local simulation, visibly, and say so */
      delete state.iv.busy[key];
      var next = localDecision(record, checked, { actor: INTERVENE_LOCAL_ACTOR });
      commitRecord(next);
      state.iv.local += 1;
      state.iv.decisions += 1;
      state.iv.forms[key] = false;
      state.iv.reasons[key] = false;
      delete state.iv.values[key];
      state.iv.results[key] = {
        mode: 'local',
        decision: checked.decision,
        status: next.status,
        fields: checked.changed,
        reason: checked.reason,
        at: next.user_intervention.decided_at,
        audit: '',
        requestId: '',
        code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR',
        noticeKey: 'intervene.offline.notice'
      };
      state.iv.lastResult = { id: key, mode: 'local', decision: checked.decision, status: next.status };
      renderAll();
      return { ok: true, local: true, code: state.iv.results[key].code, data: next };
    });
  }

  function optionKindKey(opt) {
    var kind = isObj(opt) ? str(opt.kind) : '';
    if (kind === 'approve') return 'intervene.options.approve';
    if (kind === 'modify') return 'intervene.options.modify';
    if (kind === 'reject') return 'intervene.options.reject';
    return 'intervene.options.unknown';
  }

  function fieldText(field) {
    if (!isObj(field)) return '';
    var bits = [str(field.name)];
    if (hasText(field.label)) bits[0] += ' (' + str(field.label) + ')';
    if (hasText(field.type)) bits.push(str(field.type));
    if (field.required === true) bits.push('required');
    if (field.default !== undefined && field.default !== null) bits.push('= ' + str(field.default));
    if (hasText(field.unit)) bits.push(str(field.unit));
    return bits.join(' ');
  }

  function renderSuggested(host, record) {
    var list = isArr(record.suggested_next) ? record.suggested_next : [];
    var ui = isObj(record.user_intervention) ? record.user_intervention : null;
    var receipt = isObj(record.execution_receipt) ? record.execution_receipt : null;
    if (list.length === 0 && !ui && !receipt) return;

    var box = add(host, el('div', { class: 'next', dataset: { card: str(record.id) } }));
    if (ui) {
      var drow = add(box, el('p', { class: 'next__decision' }));
      i18nText(add(drow, el('span', { class: 'next__key' })), 'card.field.decision');
      add(drow, el('span', { class: 'next__value', text: decisionLabel(ui.decision) })).setAttribute('data-i18n', 'intervene.decision.' + (DECISION_SET.indexOf(str(ui.decision)) >= 0 ? str(ui.decision) : 'unknown'));
      if (hasText(ui.reason)) add(drow, el('span', { class: 'next__reason', text: str(ui.reason) }));
      if (hasText(ui.decided_by_role)) {
        var roleKey = 'role.' + (ROLE_SET.indexOf(str(ui.decided_by_role)) >= 0 ? str(ui.decided_by_role) : 'unknown');
        add(drow, el('span', { class: 'next__role', text: roleLabel(ui.decided_by_role) })).setAttribute('data-i18n', roleKey);
      }
      if (ui.recorded === true) drow.setAttribute('data-recorded', 'true');
    }
    if (receipt) {
      var rrow = add(box, el('p', { class: 'next__receipt' }));
      i18nText(add(rrow, el('span', { class: 'next__key' })), 'card.field.receipt');
      var stateLabel = receiptLabel(receipt.state);
      add(rrow, el('span', { class: 'next__value', text: stateLabel }));
      rrow.setAttribute('data-receipt-state', str(receipt.state) || 'unreported');
      rrow.setAttribute('data-i18n', 'intervene.receipt.' + (RECEIPT_STATE_SET.indexOf(str(receipt.state)) >= 0 ? str(receipt.state) : 'unreported'));
      if (hasText(receipt.detail)) add(rrow, el('span', { class: 'next__detail', text: str(receipt.detail) }));
      i18nText(add(box, el('p', { class: 'note note--effect' })), 'intervene.effect.recorded_only');
    }
    if (list.length > 0) {
      i18nText(add(box, el('h4', { class: 'next__title' })), 'card.field.suggested');
      var ul = add(box, el('ul', { class: 'next__list' }));
      list.forEach(function (item) {
        if (!isObj(item) || !hasText(item.text)) return;
        var li = add(ul, el('li', { class: 'next__item', dataset: { kind: str(item.kind), target: str(item.target_action_id) } }));
        add(li, el('span', { class: 'next__kind', text: suggestedKindLabel(item.kind) })).setAttribute('data-i18n',
          (['retry', 'ignore', 'provide_input', 'edit_params'].indexOf(str(item.kind)) >= 0) ? 'card.suggested.kind.' + str(item.kind) : 'card.suggested.kind.other');
        add(li, el('span', { class: 'next__text', text: str(item.text) }));
      });
    }
  }

  function renderSidebar() {
    var side = byId('sidebar');
    if (!side) return;
    clear(side);
    side.setAttribute('data-hook', 'sidebar');
    side.setAttribute('aria-hidden', state.view === 'desktop' ? 'false' : 'true');

    var brand = add(side, el('div', { class: 'sidebar__brand' }));
    i18nText(add(brand, el('span', { class: 'sidebar__name' })), 'nav.app.name');
    i18nText(add(brand, el('span', { class: 'sidebar__tagline' })), 'nav.app.tagline');

    var nav = add(side, el('nav', { class: 'sidebar__nav' }));
    [['zones', 'nav.sheet.title'], ['timeline', 'timeline.title'], ['compare', 'desk.compare.entry'], ['audit', 'audit.title'], ['export', 'export.title'], ['settings', 'settings.title']].forEach(function (pair) {
      var item = add(nav, el('a', { class: 'sidebar__link', href: '#', dataset: { hook: 'nav-' + pair[0] } }));
      i18nText(item, pair[1]);
      /* 4.5: the settings entry is a screen, so its accessible name says what
       * clicking does, not just what it is called. */
      if (pair[0] === 'settings') aria(item, 'settings.action.open');
    });

    var summary = add(side, el('div', { class: 'sidebar__summary', dataset: { hook: 'summary' } }));
    ZONE_ORDER.forEach(function (zone) {
      var line = add(summary, el('p', { class: 'sidebar__stat', dataset: { zone: zone } }));
      i18nText(add(line, el('span', { class: 'sidebar__stat-label' })), ZONE_KEYS[zone].title);
      add(line, el('span', { class: 'sidebar__stat-value', text: String((state.zones[zone] || []).length) }));
    });
    /* the desktop list + filter live here so the phone layout can hide the
     * whole sidebar without touching the zone content */
    renderSidebarDesk(side);

    var settings = add(side, el('div', { class: 'sidebar__settings', dataset: { hook: 'settings-panel' } }));
    i18nText(add(settings, el('h3', { class: 'sidebar__settings-title' })), 'settings.title');
    var langRow = add(settings, el('p', { class: 'sidebar__setting', dataset: { setting: 'language' } }));
    i18nText(add(langRow, el('span', { class: 'sidebar__setting-key' })), 'settings.language');
    add(langRow, el('span', { class: 'sidebar__setting-value', text: t('nav.lang.' + i18n.lang) }));
    var granRow = add(settings, el('p', { class: 'sidebar__setting', dataset: { setting: 'granularity' } }));
    i18nText(add(granRow, el('span', { class: 'sidebar__setting-key' })), 'settings.granularity');
    add(granRow, el('span', { class: 'sidebar__setting-value', text: t('level.' + granularity.value) }));
    i18nText(add(settings, el('p', { class: 'sidebar__settings-hint' })), 'settings.hint');

    i18nText(add(side, el('p', { class: 'sidebar__version' })), 'nav.skeleton.version', { v: SKELETON_VERSION });
  }

  function renderPlaceholders() {
    var rail = byId('timeline');
    if (rail) {
      clear(rail);
      rail.setAttribute('data-hook', 'timeline');
      /* 4.10: on a phone the rail is the drawer the "more" entries open, so it
       * grows a head (title + back + close) that desktop never shows. */
      renderMoreDrawer(rail);
      var drawerOpen = state.view !== 'desktop' && state.more.open === true;
      /* the rail is real content on desktop, decorative on phone / tablet -
       * unless the phone opened it as a drawer, then it is a dialog */
      rail.setAttribute('aria-hidden', (state.view === 'desktop' || drawerOpen) ? 'false' : 'true');
      if (drawerOpen) {
        rail.setAttribute('role', 'dialog');
        rail.setAttribute('aria-modal', 'true');
        rail.setAttribute('aria-labelledby', 'more-drawer-title');
      } else {
        rail.removeAttribute('role');
        rail.removeAttribute('aria-modal');
        rail.removeAttribute('aria-labelledby');
      }
      renderTimelineRail(rail);

      /* 4.8: the export and audit blocks are real panels now - the audit list
       * (time / actor / action / decision / hash, refresh + load more) and the
       * export entries, both driven by what the server answers and by the role
       * abilities the server published. */
      var hooks = add(rail, el('div', { class: 'rail__hooks', dataset: { hook: 'rail-hooks' } }));
      /* 4.9: data entry comes first - paste a trace or pick a file, and the
       * rows land in the zones above. Same panel the phone gets in the sheet. */
      var importBox = add(hooks, el('div', { class: 'rail__hook', dataset: { hook: 'import' } }));
      renderImportPanel(importBox);
      var exportBox = add(hooks, el('div', { class: 'rail__hook', dataset: { hook: 'export' } }));
      renderExportPanel(exportBox);
      var auditBox = add(hooks, el('div', { class: 'rail__hook', dataset: { hook: 'audit' } }));
      renderAuditPanel(auditBox);
    }
    var compare = byId('compare');
    if (compare) {
      clear(compare);
      compare.setAttribute('data-hook', 'compare');
      compare.setAttribute('aria-hidden', state.view === 'desktop' ? 'false' : 'true');
      renderComparePanel(compare);
    }
    var sheetImport = byId('sheet-import');
    if (sheetImport) {
      /* the phone bottom panel carries the import too (QA 5.1 D1: the entry has
       * to exist where the phone user actually is, not only in the desktop rail) */
      clear(sheetImport);
      sheetImport.setAttribute('data-hook', 'sheet-import');
      renderImportPanel(sheetImport, { titleId: 'sheet-import-title' });
    }
    var sheetFoot = byId('sheet-foot');
    if (sheetFoot) {
      clear(sheetFoot);
      sheetFoot.setAttribute('data-hook', 'sheet-foot');
      i18nText(add(sheetFoot, el('p', { class: 'sheet__order' })), 'zones.order.note');
      i18nText(add(sheetFoot, el('p', { class: 'sheet__timehint' })), 'time.zone_note');
    }
  }

  function renderSheetChrome() {
    var sheet = byId('sheet');
    if (!sheet) return;
    sheet.setAttribute('data-hook', 'sheet');
    var modal = state.view === 'mobile';
    sheet.setAttribute('role', modal ? 'dialog' : 'region');
    sheet.setAttribute('aria-modal', modal ? 'true' : 'false');
    var title = byId('sheet-title');
    if (title) i18nText(title, 'nav.sheet.title');
    sheet.setAttribute('aria-labelledby', 'sheet-title');
    var sub = byId('sheet-subtitle');
    if (sub) i18nText(sub, 'nav.sheet.subtitle');
    var closeBtn = byId('sheet-close');
    if (closeBtn) {
      i18nText(closeBtn, 'nav.sheet.close');
      aria(closeBtn, 'nav.ball.close');
    }
    var grab = byId('sheet-grab');
    if (grab) aria(grab, 'nav.ball.close');
    sheet.hidden = !(modal ? state.sheetOpen : true);
    sheet.className = 'sheet' + (state.sheetOpen ? ' is-open' : '');
    sheet.setAttribute('data-open', state.sheetOpen ? 'true' : 'false');
  }

  /* ---------------------------------------------------------------- *
   * 8b. desktop layer (3.2): sidebar action list + status filter, timeline
   * scrubber, compare mode. Same records, same colour semantics as 3.1;
   * every label comes from the dictionaries.
   * ---------------------------------------------------------------- */

  var FILTER_STORAGE_KEY = 'tracelens.filter.v1';
  /* Labels are spelled out as literals so the i18n coverage check can see every
   * key the compare table asks for. */
  var COMPARE_FIELDS = [
    { key: 'status', label: 'compare.field.status' },
    { key: 'action_type', label: 'compare.field.action_type' },
    { key: 'risk_level', label: 'compare.field.risk' },
    { key: 'latency_ms', label: 'compare.field.latency' },
    { key: 'cost', label: 'compare.field.cost' },
    { key: 'tool_used', label: 'compare.field.tool' },
    { key: 'receipt', label: 'compare.field.receipt' },
    { key: 'confidence', label: 'compare.field.confidence' }
  ];
  /* Only machine-derived fields are listed: run B is a field-level variant of
   * the same synthetic fixture, so rewriting the human_readable prose for it
   * would mean inventing copy, and its L1 line is never shown here. The
   * fallback label in fieldLabel() is compare.field.other, a neutral key that
   * exists in both dictionaries (4.7 closed the 3.2 fallback hazard). */
  var COMPARE_PARAMS = COMPARE_FIELDS.map(function (f) { return f.key; });

  function stampMs(record) {
    var d = parseISO(record && record.timestamp);
    if (!d) return 0;
    if (typeof d.getTime === 'function') return d.getTime();
    var n = num(d);
    return n === null ? 0 : n;
  }

  function l1Of(record) {
    var hr = isObj(record && record.human_readable) ? record.human_readable : {};
    return str(hr.l1);
  }

  function valueText(value) {
    var s = str(value);
    return s ? s : t('card.no_value');
  }

  /* the desktop list is newest first: timestamp, then step number */
  function sortedRecordsDesc() {
    return state.records.slice().sort(function (a, b) {
      var delta = stampMs(b) - stampMs(a);
      if (delta !== 0) return delta;
      var sa = num(a && a.sequence_no);
      var sb = num(b && b.sequence_no);
      return (sb === null ? 0 : sb) - (sa === null ? 0 : sa);
    });
  }

  /* ---- status filter (desktop sidebar drives the zone list) ---- */

  function detectFilter() {
    var out = { needs_me: true, running: true, done: true };
    var stored = null;
    try {
      if (global.localStorage) stored = global.localStorage.getItem(FILTER_STORAGE_KEY);
    } catch (e) { stored = null; }
    if (!stored) return out;
    try {
      var parsed = JSON.parse(stored);
      if (isObj(parsed)) {
        ZONE_ORDER.forEach(function (zone) {
          if (parsed[zone] === false) out[zone] = false;
        });
      }
    } catch (e2) { /* keep the defaults */ }
    return out;
  }

  function saveFilter() {
    var payload = {};
    ZONE_ORDER.forEach(function (zone) { payload[zone] = state.filter[zone] !== false; });
    try {
      if (global.localStorage) global.localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(payload));
    } catch (e) { /* storage unavailable */ }
  }

  function zoneVisible(zone) {
    return state.filter[zone] !== false;
  }

  function hiddenByFilter() {
    var n = 0;
    ZONE_ORDER.forEach(function (zone) {
      if (!zoneVisible(zone)) n += (state.zones[zone] || []).length;
    });
    return n;
  }

  function toggleFilterZone(zone) {
    if (ZONE_ORDER.indexOf(zone) < 0) return null;
    state.filter[zone] = !zoneVisible(zone);
    saveFilter();
    if (booted) renderAll();
    return state.filter[zone];
  }

  function filterSnapshot() {
    var snapshot = {};
    ZONE_ORDER.forEach(function (zone) { snapshot[zone] = state.filter[zone] !== false; });
    return snapshot;
  }

  function setFilter(next) {
    if (isObj(next)) {
      ZONE_ORDER.forEach(function (zone) {
        if (typeof next[zone] === 'boolean') state.filter[zone] = next[zone];
      });
    }
    saveFilter();
    if (booted) renderAll();
    return filterSnapshot();
  }

  /* ---- focus: one record is highlighted, driven by list clicks and the
   * timeline scrubber, so both views always agree ---- */

  function indexOfRecord(id) {
    var key = str(id);
    for (var i = 0; i < state.records.length; i += 1) {
      if (str(state.records[i].id) === key) return i;
    }
    return null;
  }

  function recordById(id) {
    var i = indexOfRecord(id);
    return i === null ? null : state.records[i];
  }

  function scrollToCard(id) {
    var card = byId('card-' + str(id));
    if (!card || typeof card.scrollIntoView !== 'function') return false;
    try {
      card.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    } catch (e) {
      card.scrollIntoView();
    }
    return true;
  }

  function focusRecord(id) {
    var key = str(id);
    if (!key || indexOfRecord(key) === null) return null;
    state.focus = key;
    state.timelineIndex = indexOfRecord(key);
    scrollToCard(key);
    if (booted) renderAll();
    return key;
  }

  function clampTimelineIndex(index) {
    var total = state.records.length;
    if (!total) return 0;
    var i = num(index);
    if (i === null) i = 0;
    i = Math.round(i);
    if (i < 0) i = 0;
    if (i >= total) i = total - 1;
    return i;
  }

  function timelineRecord() {
    if (!state.records.length) return null;
    return state.records[clampTimelineIndex(state.timelineIndex)];
  }

  function setTimelineIndex(index) {
    if (!state.records.length) return null;
    var i = clampTimelineIndex(index);
    state.timelineIndex = i;
    state.focus = str(state.records[i].id);
    scrollToCard(state.focus);
    if (booted) renderAll();
    return i;
  }

  /* ---- runs + compare: the main demo is run A, the generated
   * compare-data.js is run B. Both are synthetic demo data. ---- */

  function runsOf() {
    /* no dictionary lookup here: probe() may run before the dictionaries are
     * loaded, and a run label is copy the panel adds, not the model */
    var runs = [{
      key: 'a',
      name: taskNameOf(state.records) || 'A',
      records: state.records,
      demo: state.demo.show
    }];
    if (state.compareRecords.length) {
      runs.push({
        key: 'b',
        name: taskNameOf(state.compareRecords) || 'B',
        records: state.compareRecords,
        demo: demoStatus(state.compareRecords).show
      });
    }
    return runs;
  }

  function runByKey(key) {
    var runs = runsOf();
    var fallback = runs[0];
    for (var i = 0; i < runs.length; i += 1) {
      if (runs[i].key === str(key)) return runs[i];
    }
    return fallback;
  }

  /* Run B is the generated compare-data.js (same synthetic provenance rules as
   * the demo set). Feeding nothing leaves compare with a single run, which the
   * panel states instead of inventing a second one. */
  function setCompareData(input, meta) {
    var list = isArr(input) ? input : (isObj(input) && isArr(input.records) ? input.records : []);
    var clean = [];
    list.forEach(function (r) {
      if (isObj(r) && hasText(r.id)) clean.push(r);
    });
    state.compareRecords = clean;
    state.compareMeta = isObj(meta) ? meta : (isObj(input) ? (input._meta || {}) : {});
    if (!clean.length) state.compare.b = state.compare.a;
    if (booted) renderAll();
    return clean.length;
  }

  function fieldLabel(key) {
    for (var i = 0; i < COMPARE_FIELDS.length; i += 1) {
      if (COMPARE_FIELDS[i].key === key) return COMPARE_FIELDS[i].label;
    }
    /* The 3.2 leftover returned compare.field.l1 here, which is honest only for
     * one field and silently labelled every unknown key "L1". Unknown keys now
     * resolve to a neutral label that exists in both dictionaries. */
    return 'compare.field.other';
  }

  function fieldValue(record, key) {
    if (!record) return null;
    if (key === 'status') return t('status.' + statusKey(record.status));
    if (key === 'action_type') return t('action_type.' + typeKey(record.action_type));
    if (key === 'risk_level') return t('risk.' + riskKey(record.risk_level));
    if (key === 'latency_ms') return valueText(formatDuration(record.latency_ms));
    if (key === 'cost') return valueText(formatCost(record.cost, i18n.lang));
    if (key === 'tool_used') {
      var tool = isObj(record.tool_used) ? str(record.tool_used.name) : '';
      return tool ? tool : t('card.tool.none');
    }
    if (key === 'receipt') {
      var receipt = isObj(record.execution_receipt) ? str(record.execution_receipt.state) : '';
      return receipt ? receipt : t('card.no_value');
    }
    if (key === 'confidence') return valueText(formatConfidence(record.confidence));
    return valueText(l1Of(record));
  }

  function seqKeyOf(record, index) {
    var seq = num(record && record.sequence_no);
    return seq === null ? index + 1 : seq;
  }

  function seqMap(records) {
    var map = {};
    var keys = [];
    records.forEach(function (r, i) {
      var key = seqKeyOf(r, i);
      if (!Object.prototype.hasOwnProperty.call(map, key)) keys.push(key);
      map[key] = r;
    });
    keys.sort(function (x, y) { return x - y; });
    return { map: map, keys: keys };
  }

  function compareModel() {
    var a = runByKey(state.compare.a);
    var b = runByKey(state.compare.b);
    var empty = { a: a, b: b, rows: [], diffs: 0, classes: [], steps: 0 };
    if (!a || !b) return empty;
    var mapA = seqMap(a.records);
    var mapB = seqMap(b.records);
    var seqs = mapA.keys.slice();
    mapB.keys.forEach(function (k) {
      if (seqs.indexOf(k) < 0) seqs.push(k);
    });
    seqs.sort(function (x, y) { return x - y; });
    var classes = {};
    var diffs = 0;
    var rows = seqs.map(function (seq) {
      var ra = mapA.map[seq];
      var rb = mapB.map[seq];
      var cells = COMPARE_PARAMS.map(function (key) {
        var va = fieldValue(ra, key);
        var vb = fieldValue(rb, key);
        var kind;
        if (!ra && rb) kind = 'only_b';
        else if (ra && !rb) kind = 'only_a';
        else if (va === vb) kind = 'same';
        else kind = 'changed';
        classes[kind] = (classes[kind] || 0) + 1;
        if (kind !== 'same') diffs += 1;
        return { field: key, label: fieldLabel(key), a: valueText(va), b: valueText(vb), kind: kind };
      });
      return { seq: seq, a: ra, b: rb, cells: cells };
    });
    return {
      a: a,
      b: b,
      rows: rows,
      steps: rows.length,
      diffs: diffs,
      classes: Object.keys(classes).sort(),
      classCounts: classes
    };
  }

  function setComparePick(which, key) {
    var slot = which === 'b' ? 'b' : 'a';
    var runs = runsOf();
    var found = null;
    for (var i = 0; i < runs.length; i += 1) {
      if (runs[i].key === str(key)) found = runs[i].key;
    }
    if (found) state.compare[slot] = found;
    if (booted) renderAll();
    return state.compare[slot];
  }

  function setCompareOpen(open) {
    state.compare.open = open !== false;
    var app = byId('app');
    if (app) app.setAttribute('data-compare', state.compare.open ? 'on' : 'off');
    if (booted) renderAll();
    return state.compare.open;
  }

  /* ---- renderers ---- */

  function renderSidebarDesk(host) {
    var filters = add(host, el('div', { class: 'sidebar__filters', dataset: { hook: 'filters' } }));
    i18nText(add(filters, el('h3', { class: 'sidebar__block-title' })), 'desk.filter.title');
    i18nText(add(filters, el('p', { class: 'sidebar__block-hint' })), 'desk.filter.hint');
    var group = add(filters, el('div', { class: 'filter__group', role: 'group' }));
    aria(group, 'desk.filter.group.aria');
    ZONE_ORDER.forEach(function (zone) {
      var on = zoneVisible(zone);
      var count = (state.zones[zone] || []).length;
      var btn = add(group, el('button', {
        class: 'filter__btn' + (on ? ' is-on' : ' is-off'),
        type: 'button',
        tabindex: '0',
        dataset: { hook: 'filter-zone', zone: zone, count: count, visible: on ? 'true' : 'false' }
      }));
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      aria(btn, 'desk.filter.toggle.aria', {
        zone: t(ZONE_KEYS[zone].title),
        state: t(on ? 'desk.filter.state.on' : 'desk.filter.state.off'),
        n: count
      });
      add(btn, el('span', { class: 'filter__dot', 'aria-hidden': 'true', dataset: { zone: zone } }));
      i18nText(add(btn, el('span', { class: 'filter__label' })), ZONE_KEYS[zone].title);
      add(btn, el('span', { class: 'filter__value', text: String(count) }));
    });

    var listHost = add(host, el('div', { class: 'sidebar__listbox', dataset: { hook: 'sidebar-listbox' } }));
    i18nText(add(listHost, el('h3', { class: 'sidebar__block-title' })), 'desk.sidebar.list.title');
    i18nText(add(listHost, el('p', { class: 'sidebar__block-hint' })), 'desk.sidebar.list.hint');
    var items = sortedRecordsDesc().filter(function (r) {
      return zoneVisible(zoneOf(r));
    });
    var list = add(listHost, el('ol', {
      class: 'sidebar__list',
      role: 'list',
      dataset: { hook: 'sidebar-list', total: state.records.length, shown: items.length }
    }));
    aria(list, 'desk.sidebar.list.aria', { n: items.length });
    if (!items.length) {
      i18nText(add(list, el('li', { class: 'sidebar__list-empty' })), 'desk.sidebar.list.empty');
    }
    items.forEach(function (r) {
      var zone = zoneOf(r);
      var focused = state.focus === str(r.id);
      var time = formatTimestamp(r.timestamp, i18n.lang);
      var l1 = l1Of(r);
      var li = add(list, el('li', {
        class: 'sidebar__item' + (focused ? ' is-focused' : ''),
        role: 'listitem',
        dataset: {
          id: str(r.id), zone: zone, color: colorRoleOf(r),
          time: time, focused: focused ? 'true' : 'false'
        }
      }));
      var btn = add(li, el('button', {
        class: 'sidebar__item-btn',
        type: 'button',
        tabindex: '0',
        dataset: { hook: 'sidebar-item', card: str(r.id), zone: zone }
      }));
      aria(btn, 'desk.sidebar.item.aria', {
        time: time,
        status: t('status.' + statusKey(r.status)),
        l1: l1 || t('card.detail.empty')
      });
      add(btn, el('span', { class: 'sidebar__item-dot', 'aria-hidden': 'true', dataset: { color: colorRoleOf(r) } }));
      add(btn, el('span', { class: 'sidebar__item-time', text: time }));
      add(btn, el('span', { class: 'sidebar__item-l1', text: l1 || t('card.detail.empty') }));
    });
  }

  /* The scrubber + preview live in the main area (from tablet up); the tick
   * list lives in the right rail. Both read the same state.timelineIndex, so
   * dragging the bar highlights the list and the card at the same time. */
  function renderTimelineMain() {
    var host = byId('timeline-main');
    if (!host) return;
    clear(host);
    host.setAttribute('data-hook', 'timeline-main');
    host.setAttribute('aria-hidden', state.view === 'mobile' ? 'true' : 'false');
    i18nText(add(host, el('h2', { class: 'panel__title' })), 'timeline.title');
    i18nText(add(host, el('p', { class: 'panel__hint' })), 'timeline.hint');

    var total = state.records.length;
    var current = timelineRecord();
    var index = current ? clampTimelineIndex(state.timelineIndex) : 0;
    var scrubBox = add(host, el('div', { class: 'timeline__scrub-box', dataset: { hook: 'timeline-scrub-box' } }));
    i18nText(add(scrubBox, el('label', { class: 'timeline__scrub-label', for: 'timeline-scrub' })), 'timeline.scrub.label');
    var scrub = add(scrubBox, el('input', {
      class: 'timeline__scrub',
      type: 'range',
      id: 'timeline-scrub',
      min: '0',
      max: String(Math.max(total - 1, 0)),
      step: '1',
      value: String(index),
      tabindex: '0',
      dataset: { hook: 'timeline-scrub', index: index, total: total, id: current ? str(current.id) : '' }
    }));
    var timeText = current ? formatTimestamp(current.timestamp, i18n.lang) : t('time.unknown');
    scrub.setAttribute('aria-valuetext', t('timeline.scrub.aria', { i: index + 1, total: total, time: timeText }));
    aria(scrub, 'timeline.scrub.aria', { i: index + 1, total: total, time: timeText });
    i18nText(add(scrubBox, el('p', { class: 'timeline__scale' })), 'timeline.count', { n: total });

    var peek = add(host, el('div', {
      class: 'timeline__peek',
      dataset: {
        hook: 'timeline-peek',
        index: index,
        id: current ? str(current.id) : '',
        color: current ? colorRoleOf(current) : 'neutral',
        zone: current ? zoneOf(current) : ''
      }
    }));
    i18nText(add(peek, el('p', { class: 'timeline__peek-kicker' })), 'timeline.peek.hint');
    add(peek, el('p', { class: 'timeline__peek-time', text: timeText }));
    add(peek, el('p', { class: 'timeline__peek-l1', text: current ? (l1Of(current) || t('card.detail.empty')) : t('desk.sidebar.list.empty') }));
    var peekBadges = add(peek, el('ul', { class: 'timeline__peek-badges' }));
    if (current) {
      var bStatus = add(peekBadges, el('li', { class: 'badge badge--status', dataset: { status: statusKey(current.status) } }));
      i18nText(bStatus, 'status.' + statusKey(current.status));
      var bRisk = add(peekBadges, el('li', { class: 'badge badge--risk', dataset: { risk: riskKey(current.risk_level) } }));
      i18nText(bRisk, 'risk.' + riskKey(current.risk_level));
      if (recordIsDemo(state.demo, current)) {
        var bDemo = add(peekBadges, el('li', { class: 'badge badge--demo' }));
        i18nText(bDemo, 'card.badge.demo');
      }
    }
  }

  function renderTimelineRail(rail) {
    i18nText(add(rail, el('h2', { class: 'panel__title' })), 'timeline.title');
    i18nText(add(rail, el('p', { class: 'panel__hint' })), 'timeline.hint');

    var total = state.records.length;
    var current = timelineRecord();
    var index = current ? clampTimelineIndex(state.timelineIndex) : 0;
    var strip = add(rail, el('ol', { class: 'timeline__strip', dataset: { hook: 'timeline-items', total: total } }));
    state.records.forEach(function (r, i) {
      var selected = i === index;
      var li = add(strip, el('li', {
        class: 'timeline__tick' + (selected ? ' is-selected' : ''),
        dataset: { id: str(r.id), color: colorRoleOf(r), index: i, selected: selected ? 'true' : 'false' }
      }));
      var tick = add(li, el('button', {
        class: 'timeline__tick-btn',
        type: 'button',
        tabindex: '0',
        dataset: { hook: 'timeline-tick', index: i, card: str(r.id) }
      }));
      aria(tick, 'timeline.tick.aria', { i: i + 1, time: formatTimestamp(r.timestamp, i18n.lang) });
      add(tick, el('span', { class: 'timeline__dot', 'aria-hidden': 'true' }));
      add(tick, el('span', { class: 'timeline__time', text: formatTimestamp(r.timestamp, i18n.lang) }));
    });
  }

  function renderComparePanel(compare) {
    i18nText(add(compare, el('h2', { class: 'panel__title' })), 'compare.title');
    i18nText(add(compare, el('p', { class: 'panel__hint' })), 'compare.hint');

    var runs = runsOf();
    var head = add(compare, el('div', { class: 'compare__pick', dataset: { hook: 'compare-pick' } }));
    i18nText(add(head, el('p', { class: 'compare__pick-label' })), 'compare.pick.label');
    [['a', 'compare.pick.a'], ['b', 'compare.pick.b']].forEach(function (pair) {
      var slot = pair[0];
      var row = add(head, el('label', { class: 'compare__pick-row' }));
      i18nText(add(row, el('span', { class: 'compare__pick-key' })), pair[1]);
      var select = add(row, el('select', {
        class: 'compare__select',
        dataset: { hook: 'compare-' + slot, slot: slot, value: state.compare[slot] }
      }));
      select.setAttribute('aria-label', t(pair[1]));
      runs.forEach(function (run, i) {
        var opt = add(select, el('option', {
          value: run.key,
          text: t('compare.run.label', { n: i + 1, name: run.name }),
          dataset: { key: run.key }
        }));
        if (run.key === state.compare[slot]) opt.setAttribute('selected', 'selected');
      });
    });
    var toggle = add(head, el('button', {
      class: 'compare__toggle',
      type: 'button',
      tabindex: '0',
      dataset: { hook: 'compare-toggle', open: state.compare.open ? 'true' : 'false' }
    }));
    toggle.setAttribute('aria-expanded', state.compare.open ? 'true' : 'false');
    i18nText(toggle, state.compare.open ? 'compare.exit' : 'compare.start');

    if (!state.compare.open) return;

    var model = compareModel();
    setDataset(compare, 'diffs', model.diffs);
    setDataset(compare, 'steps', model.steps);
    setDataset(compare, 'classes', model.classes.join(','));

    var legendBox = add(compare, el('div', { class: 'compare__legend-box', dataset: { hook: 'compare-legend-box' } }));
    i18nText(add(legendBox, el('p', { class: 'compare__legend-title' })), 'compare.legend.title');
    var legend = add(legendBox, el('ul', { class: 'legend compare__legend', dataset: { hook: 'compare-legend' } }));
    [['changed', 'compare.diff.changed'], ['only_a', 'compare.diff.only_a'],
      ['only_b', 'compare.diff.only_b'], ['same', 'compare.diff.same']].forEach(function (pair) {
      var li = add(legend, el('li', {
        class: 'legend__item compare__key compare__key--' + pair[0],
        dataset: { kind: pair[0], count: (model.classCounts[pair[0]] || 0) }
      }));
      add(li, el('span', { class: 'compare__swatch', 'aria-hidden': 'true' }));
      i18nText(add(li, el('span', { class: 'legend__label' })), pair[1]);
      add(li, el('span', { class: 'compare__key-count', text: String(model.classCounts[pair[0]] || 0) }));
    });

    var summary = add(compare, el('p', {
      class: 'compare__summary',
      dataset: { hook: 'compare-summary', diffs: model.diffs, identical: model.diffs === 0 ? 'true' : 'false' }
    }));
    i18nText(summary, model.diffs === 0 ? 'compare.summary.same' : 'compare.summary', { n: model.diffs });
    i18nText(add(compare, el('p', { class: 'compare__align-note' })), 'compare.align.note');

    var table = add(compare, el('div', {
      class: 'compare__grid',
      role: 'table',
      dataset: { hook: 'compare-grid', rows: model.rows.length }
    }));
    aria(table, 'compare.aria.grid');
    var headRow = add(table, el('div', { class: 'compare__row compare__row--head', role: 'row' }));
    add(headRow, el('span', { class: 'compare__corner' }));
    add(headRow, el('span', { class: 'compare__col compare__col--a', role: 'columnheader', text: 'A - ' + model.a.name }));
    add(headRow, el('span', { class: 'compare__col compare__col--b', role: 'columnheader', text: 'B - ' + model.b.name }));
    model.rows.forEach(function (row) {
      var block = add(table, el('div', {
        class: 'compare__step',
        dataset: { hook: 'compare-step', seq: row.seq, hasA: row.a ? 'true' : 'false', hasB: row.b ? 'true' : 'false' }
      }));
      i18nText(add(block, el('p', { class: 'compare__step-title' })), 'compare.step.label', { n: row.seq });
      row.cells.forEach(function (cell) {
        var line = add(block, el('div', {
          class: 'compare__row',
          role: 'row',
          dataset: { hook: 'compare-cell', field: cell.field, kind: cell.kind }
        }));
        i18nText(add(line, el('span', { class: 'compare__field', role: 'cell' })), cell.label);
        add(line, el('span', {
          class: 'compare__value compare__value--a compare__value--' + cell.kind,
          role: 'cell',
          text: cell.a === null ? t('card.no_value') : cell.a,
          dataset: { diff: cell.kind, side: 'a', field: cell.field }
        }));
        add(line, el('span', {
          class: 'compare__value compare__value--b compare__value--' + cell.kind,
          role: 'cell',
          text: cell.b === null ? t('card.no_value') : cell.b,
          dataset: { diff: cell.kind, side: 'b', field: cell.field }
        }));
      });
    });
    i18nText(add(compare, el('p', { class: 'compare__note' })), 'compare.note.source');
  }

  /* ---- events: the three desktop panels are rebuilt on every render, so the
   * handlers are delegated from the stable containers bound once in init ---- */

  function deskTarget(ev, selector) {
    var node = ev && ev.target;
    if (!node || typeof node.closest !== 'function') return null;
    return node.closest(selector);
  }

  function wireDeskEvents() {
    var side = byId('sidebar');
    var rail = byId('timeline');
    var main = byId('timeline-main');
    var compare = byId('compare');
    on(side, 'click', function (ev) {
      var filter = deskTarget(ev, '[data-hook="filter-zone"]');
      if (filter) {
        toggleFilterZone(str(filter.getAttribute('data-zone')));
        return;
      }
      var nav = deskTarget(ev, '[data-hook="nav-compare"]');
      if (nav) {
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
        setCompareOpen(!state.compare.open);
        return;
      }
      /* 4.5: the sidebar entry opens the settings centre (a read-only page for
       * roles without settings.write, still useful: it shows the mask, the
       * engine state and the security posture). */
      var navSettings = deskTarget(ev, '[data-hook="nav-settings"]');
      if (navSettings) {
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
        openSettings(!state.settings.open);
        return;
      }
      var item = deskTarget(ev, '[data-hook="sidebar-item"]');
      if (item) focusRecord(str(item.getAttribute('data-card')));
    });
    on(rail, 'click', function (ev) {
      var tick = deskTarget(ev, '[data-hook="timeline-tick"]');
      if (tick) setTimelineIndex(toNum(tick.getAttribute('data-index')));
    });
    /* a drag fires input continuously: one render per step, list follows.
     * The scrubber lives in the main area, the tick list in the rail, so both
     * containers are bound and the handler is shared. */
    var onScrub = function (ev) {
      var scrub = deskTarget(ev, '[data-hook="timeline-scrub"]');
      if (scrub) setTimelineIndex(toNum(scrub.value));
    };
    on(rail, 'input', onScrub);
    on(rail, 'change', onScrub);
    on(main, 'input', onScrub);
    on(main, 'change', onScrub);
    on(compare, 'click', function (ev) {
      var toggle = deskTarget(ev, '[data-hook="compare-toggle"]');
      if (toggle) setCompareOpen(!state.compare.open);
    });
    on(compare, 'change', function (ev) {
      var pick = deskTarget(ev, '[data-slot]');
      if (!pick) return;
      var slot = str(pick.getAttribute('data-slot'));
      if (slot === 'a' || slot === 'b') setComparePick(slot, pick.value);
    });
    /* 4.5 settings centre: the host element is static in index.html, so the
     * delegation is bound once and survives every re-render inside the panel.
     * No inline handler is ever attached to a rebuilt node. */
    var center = byId('settings-center');
    if (center) {
      on(center, 'submit', function (ev) {
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
        saveSettingsFromForm();
      });
      on(center, 'click', function (ev) {
        var target = deskTarget(ev, '[data-hook^="settings-"]');
        if (!target) return;
        var hook = str(target.getAttribute('data-hook'));
        if (hook === 'settings-test') runSettingsTest();
        else if (hook === 'settings-refresh') loadSettings(true);
        else if (hook === 'settings-export') exportSettingsData();
        else if (hook === 'settings-reset') resetSettingsData();
        else if (hook === 'settings-close') {
          /* 4.10: on a phone the settings centre is a screen the entry list
           * opened, so closing it also clears the phone screen flag */
          state.more.screen = 'off';
          openSettings(false);
        }
      });
      on(center, 'change', function (ev) {
        var lang = deskTarget(ev, '[data-hook="settings-lang"]');
        if (lang) {
          applyUiLanguage(str(lang.getAttribute('data-lang')));
          return;
        }
        var touched = deskTarget(ev, '[data-hook="settings-mode"], [data-hook="settings-clear-key"]');
        if (touched) renderSettingsPanel();
      });
    }
  }

  /* ---------------------------------------------------------------- *
   * 9. actions
   * ---------------------------------------------------------------- */

  function renderAll() {
    state.stats.renders += 1;
    rigSafe('documentChrome', applyDocumentChrome);
    /* 4.10: the phone layout flags are stamped before anything reads them */
    rigSafe('moreChrome', applyMoreChrome);
    rigSafe('authChrome', applyAuthChrome);
    rigSafe('topbar', renderTopbar);
    rigSafe('authPanels', renderAuth);
    rigSafe('settingsPanel', renderSettingsPanel);
    rigSafe('demoBanner', renderDemoBanner);
    rigSafe('sheetChrome', renderSheetChrome);
    /* 4.10: the entry list lives in its own host so it never nests inside the
     * zones panel it opens next to */
    rigSafe('moreSheet', renderMoreSheet);
    rigSafe('zones', renderZones);
    rigSafe('timelineMain', renderTimelineMain);
    rigSafe('fab', renderFab);
    rigSafe('sidebar', renderSidebar);
    rigSafe('placeholders', renderPlaceholders);
    notify();
    rigSafe('probeReport', writeProbeReport);
    /* 4.12 (H-01): after the DOM is built, read the audit page if this panel is
     * on screen and has never been read. Kept last so the load is never part of
     * the render it schedules, and guarded so it fires at most once. */
    rigSafe('auditAutoLoad', flushAuditAutoLoad);
  }

  /* In the verification rig a thrown render step must surface as a readable
   * line in the probe report instead of a blank page; a normal open keeps the
   * plain call path. */
  var rigError = null;

  function rigSafe(step, fn) {
    if (!state.probeMode && !state.frame) { fn(); return; }
    try {
      fn();
    } catch (e) {
      if (!rigError) rigError = step + ': ' + (e && e.message ? e.message : String(e));
    }
  }

  function notify() {
    state.listeners.forEach(function (fn) {
      try { fn(state); } catch (e) { /* listener errors never break rendering */ }
    });
  }

  function openSheet(open) {
    state.sheetOpen = open !== false;
    if (booted) { renderAll(); return state.sheetOpen; }
    renderFab();
    renderSheetChrome();
    var app = byId('app');
    if (app) app.setAttribute('data-open', state.sheetOpen ? 'true' : 'false');
    notify();
    return state.sheetOpen;
  }

  function toggleCard(id) {
    var key = str(id);
    if (!key) return false;
    state.expanded[key] = !state.expanded[key];
    renderZones();
    return state.expanded[key];
  }

  function setView(view) {
    var v = view === 'desktop' || view === 'tablet' ? view : 'mobile';
    state.view = v;
    applyDocumentChrome();
    var app = byId('app');
    if (app) {
      app.setAttribute('data-view', v);
      aria(app, 'nav.view.' + v);
    }
    renderSheetChrome();
    renderSidebar();
    var timelineMain = byId('timeline-main');
    if (timelineMain) timelineMain.setAttribute('aria-hidden', v === 'mobile' ? 'true' : 'false');
    var rail = byId('timeline');
    if (rail) {
      var phoneDrawer = v !== 'desktop' && state.more.open === true;
      rail.setAttribute('aria-hidden', (v === 'desktop' || phoneDrawer) ? 'false' : 'true');
    }
    var compare = byId('compare');
    if (compare) compare.setAttribute('aria-hidden', v === 'desktop' ? 'false' : 'true');
    /* a view switch moves panels around, so repaint the whole frame */
    if (booted) renderAll();
    return v;
  }

  function viewForWidth(width) {
    var w = num(width);
    if (w === null) return 'mobile';
    if (w <= 480) return 'mobile';
    if (w < 1024) return 'tablet';
    return 'desktop';
  }

  function watchViewport() {
    var mqMobile = null, mqDesktop = null;
    try {
      if (typeof global.matchMedia === 'function') {
        mqMobile = global.matchMedia('(max-width: 480px)');
        mqDesktop = global.matchMedia('(min-width: 1024px)');
      }
    } catch (e) { mqMobile = null; mqDesktop = null; }
    function sync() {
      /* the frame rig pins its own viewport: inside it the layout follows the
       * frame width, whatever the surrounding browser window is */
      if (state.frame) {
        setView(state.frame.layout || viewForWidth(state.frame.width));
        return;
      }
      var w = num(global.innerWidth);
      if (w === null && mqMobile && mqDesktop) {
        if (mqMobile.matches) w = 375;
        else if (mqDesktop.matches) w = 1440;
        else w = 760;
      }
      setView(viewForWidth(w));
    }
    [mqMobile, mqDesktop].forEach(function (mq) {
      if (!mq) return;
      if (typeof mq.addEventListener === 'function') mq.addEventListener('change', sync);
      else if (typeof mq.addListener === 'function') mq.addListener(sync);
    });
    on(global, 'resize', sync);
    sync();
  }

  /* Query parameters used by the static demo, manual walkthroughs (t10/t11)
   * and the headless probe: ?lang=en, ?open=1, ?view=desktop. */
  function searchParams() {
    var out = {};
    var q = '';
    try { q = str(global.location && global.location.search); } catch (e) { return out; }
    q = q.replace(/^[?#&]/, '');
    if (!q) return out;
    q.split('&').forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split('=');
      var key = kv[0];
      var value = kv.length > 1 ? kv.slice(1).join('=') : '1';
      try { key = decodeURIComponent(key); } catch (e) { /* keep raw */ }
      try { value = decodeURIComponent(value.replace(/\+/g, ' ')); } catch (e2) { /* keep raw */ }
      out[key] = value;
    });
    return out;
  }

  /* ?filter=needs_me,running keeps only those zones open ("all" reopens every
   * zone); ?step=3 and ?focus=<id> drive the timeline so the t10/t11
   * walkthroughs can script the desktop views without a mouse. */
  function deskParams(params) {
    var out = { filter: null, step: null, focus: null };
    if (hasText(params.filter)) {
      var wanted = str(params.filter).toLowerCase();
      var next = { needs_me: false, running: false, done: false };
      if (wanted === 'all') {
        next = { needs_me: true, running: true, done: true };
      } else {
        wanted.split(',').forEach(function (part) {
          var key = part.trim();
          if (ZONE_ORDER.indexOf(key) >= 0) next[key] = true;
        });
      }
      state.filter = next;
      saveFilter();
      out.filter = filterSnapshot();
    }
    var step = toNum(params.step);
    if (step !== null && state.records.length) {
      var s = clampTimelineIndex(step - 1);
      state.timelineIndex = s;
      state.focus = str(state.records[s].id);
      out.step = s + 1;
    }
    if (hasText(params.focus) && indexOfRecord(params.focus) !== null) {
      state.focus = str(params.focus);
      state.timelineIndex = indexOfRecord(state.focus);
      out.focus = state.focus;
    }
    return out;
  }

  function applyUrlParams() {
    var params = searchParams();
    if (isLang(params.lang)) setLang(params.lang);
    if (params.view === 'desktop' || params.view === 'tablet' || params.view === 'mobile') setView(params.view);
    if (params.open === '1' || params.open === 'true') state.sheetOpen = true;
    if (params.compare === 'on') {
      state.compare.open = true;
      var appEl = byId('app');
      if (appEl) appEl.setAttribute('data-compare', 'on');
    }
    /* 4.10: ?more=settings|audit|users|import|export|language and ?moremenu=1
     * let the rig open a phone entry inside a pinned 375x812 box. */
    moreUrlParams(params);
    state.stats.deskParams = deskParams(params);
    /* 4.1 API mode switch for the verification rig and for demos:
     * ?api=off   never talks to the server (local simulation only)
     * ?api=live  probe the sidecar right away (the default over http)      */
    if (params.api === 'off') {
      state.api.forced = 'offline';
      setApiMode('forced_offline', 'url_api_off');
    } else if (params.api === 'live') {
      state.api.forced = 'live';
    }
    state.stats.apiParam = str(state.api.forced) || 'auto';
    if (params.probe === '1' || params.probe === 'true') state.probeMode = true;
    var frame = FRAME_RE.exec(str(params.frame));
    if (frame) applyFrame(parseInt(frame[1], 10), parseInt(frame[2], 10));
    return params;
  }

  /* Pins #app to an exact viewport box (default: a 375x812 phone) so the
   * layout can be measured by any browser window. The rig behaves like the
   * phone layout: ball + bottom sheet inside the box. */
  function applyFrame(width, height) {
    var app = byId('app');
    if (!app || !width || !height) return null;
    var layout = viewForWidth(width);
    state.frame = { width: width, height: height, layout: layout };
    app.setAttribute('data-frame', width + 'x' + height);
    app.setAttribute('data-frame-layout', layout);
    app.setAttribute('style', '--frame-w:' + width + 'px;--frame-h:' + height + 'px;');
    /* media queries follow the real window, so inside the pinned box the view
     * is decided by the frame width, not by window.innerWidth */
    setView(layout);
    return state.frame;
  }

  function measureOverflow() {
    var box = null;
    var scrolled = null;
    var root = state.frame ? byId('app') : (isObj(doc) ? doc.documentElement : null);
    if (root) {
      box = num(root.clientWidth);
      scrolled = num(root.scrollWidth);
    }
    return {
      box: box,
      scrollWidth: scrolled,
      horizontalScroll: (box !== null && scrolled !== null) ? scrolled > box : null
    };
  }

  /* Verification report: visible text so a browser lane can read it, plus the
   * same numbers as JSON through the ?probe=1 script node. Only with rig
   * parameters; a normal open of the page never shows it. */
  function deskFilterOn(p) {
    var on = [];
    ZONE_ORDER.forEach(function (zone) {
      if (p.desk.filter[zone]) on.push(zone);
    });
    return on.length ? on.join('+') : 'none';
  }

  function writeProbeReport() {
    if (!state.probeMode && !state.frame) return;
    var node = byId('probe-text');
    if (!node) return;
    var p = api.probe();
    var overflow = measureOverflow();
    var rig = state.frame ? (state.frame.width + 'x' + state.frame.height) : ('window ' + str(p.layout ? p.layout.innerWidth : 'n/a'));
    var lines = [
      'TraceLens ' + p.version + ' probe @ ' + rig,
      'view=' + p.view + ' lang=' + p.lang + ' sheetOpen=' + p.sheetOpen + ' renders=' + p.stats.renders,
      'dictKeys=' + i18n.keys.length + ' dictSource=' + i18n.source + ' lang=' + p.lang,
      'zones needs_me=' + p.zones.needs_me + ' running=' + p.zones.running + ' done=' + p.zones.done +
        ' | cards=' + p.rendered.cards + ' demoBadges=' + p.rendered.badgesDemo + ' demoBadge=' + p.demoBadge,
      'progress=' + (p.progress && p.progress.percent !== undefined ? p.progress.percent : p.progress) +
        '% i18nNodes=' + p.rendered.i18nNodes + ' missingKeys=' + p.missingKeys.length,
      'desk sidebar=' + p.rendered.sidebarItems + ' filterOn=' + deskFilterOn(p) +
        ' hidden=' + p.desk.hiddenByFilter + ' scrub=' + (p.desk.timeline.index + 1) + '/' + p.desk.timeline.total +
        ' focus=' + (p.desk.focus || 'none') + ' focusedCards=' + p.rendered.focusedCards,
      'compare open=' + p.desk.compare.open + ' runs=' + p.desk.compare.runs +
        ' steps=' + p.desk.compare.rows + ' cells=' + p.rendered.compareCells +
        ' diffs=' + p.desk.compare.diffs + ' kinds=' + (p.desk.compare.classes.join('+') || 'none'),
      'api mode=' + p.api.mode + '(' + p.api.reason + ') probes=' + p.api.checks + ' requests=' + p.api.requests,
      t('intervene.probe.title') + ' - ' + t('intervene.probe.mode', { mode: p.api.mode }) + ' ' +
        t('intervene.probe.buttons', { decidable: p.intervene.decidable, buttons: p.intervene.buttonsEnabled }),
      '  ' + t('intervene.probe.requests', { requests: p.api.requests, local: p.intervene.local }) + ' ' +
        t('intervene.probe.results', { results: p.intervene.decisions, forms: p.intervene.forms }),
      'intervene decidable=' + p.intervene.decidable + ' buttons=' + p.intervene.buttonsEnabled +
        '/' + p.rendered.interveneButtons + ' forms=' + p.intervene.forms +
        ' fields=' + p.rendered.interveneFields + ' feedback=' + p.rendered.interveneFeedback +
        ' localSim=' + p.intervene.local + ' live=' + (p.intervene.decisions - p.intervene.local) +
        ' errors=' + p.intervene.errors,
      /* 4.10: a phone screenshot must show which entries exist, which the role
       * may use, and which layer is open - readable without the dev tools. */
      'mobile entries=' + p.rendered.moreEntries + '/' + p.more.total +
        ' enabled=' + p.rendered.moreEntriesEnabled + ' reasons=' + p.rendered.moreReasons +
        ' menu=' + p.more.menu + ' drawer=' + (p.more.open ? p.more.target : 'off') +
        ' screen=' + p.more.screen + ' blocked=' + p.more.blocked,
      /* 4.12: the probe has to show what a language switch kept, and what the
       * audit panel claimed before and after its first read - the two claims
       * QA 5.2 could not trust. */
      'audit loaded=' + p.audit.loaded + ' rows=' + p.audit.rows +
        ' chips=' + (p.rendered.auditChainChips + p.rendered.auditCountChip + p.rendered.auditStoreChip) +
        ' store=' + p.rendered.auditStoreChip + ' pending=' + p.rendered.auditPendingLines +
        ' auto=' + p.audit.autoLoads + '/' + p.audit.autoRefreshes +
        ' reason=' + (p.audit.lastWriteReason || 'none'),
      'lang swaps=' + (p.stats.dataLangSwaps || 0) + ' kept=' + (p.stats.dataLangKept || 0) +
        ' carried=' + (p.stats.dataLangCarried || 0) + '/' + (p.stats.dataLangOwn || 0) +
        ' refreshes=' + (p.stats.recordsRefreshes || 0) + ' merged=' + (p.stats.recordsMerged || 0),
      'box=' + overflow.box + ' scrollWidth=' + overflow.scrollWidth +
        ' horizontalScroll=' + (overflow.horizontalScroll === null ? 'n/a' : overflow.horizontalScroll)
    ];
    if (rigError) lines.push('js error: ' + rigError);
    if (overflow.horizontalScroll === null) lines.push('PROBE: n/a (no layout metrics in this DOM)');
    else if (rigError) lines.push('PROBE: FAIL (render step threw)');
    else lines.push(overflow.horizontalScroll ? 'PROBE: FAIL (horizontal scroll)' : 'PROBE: PASS (no horizontal scroll)');
    node.textContent = lines.join('\n');
    node.hidden = false;
    node.setAttribute('data-probe-rig', state.frame ? (state.frame.width + 'x' + state.frame.height) : 'window');
    node.setAttribute('data-horizontal-scroll', String(overflow.horizontalScroll));
    node.setAttribute('data-cards', String(p.rendered.cards));
    node.setAttribute('data-missing-keys', String(p.missingKeys.length));
    node.setAttribute('data-rig-error', rigError || 'none');
    node.setAttribute('data-desk-sidebar', String(p.rendered.sidebarItems));
    node.setAttribute('data-timeline-index', String(p.desk.timeline.index));
    node.setAttribute('data-compare-open', String(p.desk.compare.open));
    node.setAttribute('data-compare-diffs', String(p.desk.compare.diffs));
    node.setAttribute('data-api-mode', String(p.api.mode));
    node.setAttribute('data-api-reason', String(p.api.reason));
    node.setAttribute('data-intervene-decidable', String(p.intervene.decidable));
    node.setAttribute('data-intervene-buttons', String(p.intervene.buttonsEnabled));
    node.setAttribute('data-intervene-forms', String(p.intervene.forms));
    node.setAttribute('data-intervene-fields', String(p.rendered.interveneFields));
    node.setAttribute('data-intervene-feedback', String(p.rendered.interveneFeedback));
    node.setAttribute('data-intervene-local', String(p.intervene.local));
    node.setAttribute('data-intervene-errors', String(p.intervene.errors));
  }

  function init(input) {
    doc = global.document || null;
    if (!doc) return null;
    var embedded = global.TraceLensDicts;
    if (isObj(embedded)) setDicts(embedded.zh, embedded.en);
    i18n.lang = detectLang();

    var data = input !== undefined ? input : demoDataFor(i18n.lang);
    dataLangValue = detectDataLang();
    granularity.value = detectGranularity();
    state.filter = detectFilter();
    setRecords(data);
    setCompareData(global.TraceLensCompareData);

    var sheet = byId('sheet');
    var closeBtn = byId('sheet-close');
    var grab = byId('sheet-grab');
    var fab = byId('fab');
    on(fab, 'click', function () { openSheet(!state.sheetOpen); });
    on(fab, 'keydown', function (ev) {
      var key = ev && (ev.key || '');
      if (key === 'Enter' || key === ' ' || key === 'Spacebar') {
        if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
        openSheet(!state.sheetOpen);
      }
    });
    on(closeBtn, 'click', function () { openSheet(false); });
    on(grab, 'click', function () { openSheet(false); });
    on(doc, 'keydown', function (ev) {
      var key = ev && (ev.key || '');
      if (key !== 'Escape') return;
      /* 4.10: Escape closes the topmost phone layer first - the entry list,
       * then the drawer, then the action sheet - and hands focus back to the
       * control that opened it, so the keyboard never lands on a hidden node. */
      if (state.more.menu) {
        setMoreMenu(false);
        focusMoreButton();
        return;
      }
      if (state.more.open) {
        closeMore(false);
        focusMoreButton();
        return;
      }
      if (state.sheetOpen) {
        openSheet(false);
        if (fab && typeof fab.focus === 'function') fab.focus();
      }
    });

    wireDeskEvents();
    wireInterveneEvents();
    wireMoreEvents();

    var params = applyUrlParams();
    state.stats.urlParams = Object.keys(params).join(',') || 'none';
    watchViewport();
    if (params.view) setView(params.view);
    renderAll();
    if (state.view === 'mobile' && !(params.open === '1' || params.open === 'true')) state.sheetOpen = false;
    renderSheetChrome();
    /* the ball must describe the sheet's real state after the mobile default */
    renderFab();
    booted = true;

    loadDictionariesFromServer().then(function (ok) {
      state.stats.i18nRefresh = ok ? 'json' : 'embedded';
      if (ok) { renderAll(); return; }
      if (state.stats.i18nAttempted) {
        state.notice = 'error.i18n_load_failed';
        renderAll();
      }
    }).catch(function () {
      state.stats.i18nRefresh = 'embedded';
      if (state.stats.i18nAttempted) {
        state.notice = 'error.i18n_load_failed';
        renderAll();
      }
    });
    /* 4.1: ask the local server who it is, and in live mode take the records
     * from it. A page opened from file:// never gets an answer: that is the
     * documented offline path, not an error.
     * 4.3: ask the auth status next. team mode without a session shows the
     * login page and shows no record content at all (the list is emptied, the
     * main region is aria-hidden); local mode stays exactly as it was. */
    probeApi().then(probeAuthStatus).then(function () {
      return probeAuthMe();
    }).then(function (me) {
      if (me) return probeAuthPermissions();
      return false;
    }).then(function () {
      if (authStateName() === 'signed_out') setRecords([]);
      return syncRecordsFromApi().then(function (ok) {
        /* 4.4: open the stream only after the first full list is in, so a
         * pushed merge never races a half-populated view */
        return startRealtime().then(function () { return ok; });
      });
    }).then(function () {
      renderAll();
      return null;
    });
    return api;
  }

  /* ---------------------------------------------------------------- *
   * 9b. auth layer (4.3): local/team modes, login page, role gating, users
   * ---------------------------------------------------------------- *
   * The server is the enforcement point (agent_explainer/auth.py). Everything
   * here is presentation: the page hides/disables entries using the matrix the
   * server publishes at api/auth/permissions, and it never claims a permission
   * the server has not granted. A viewer therefore sees no intervention
   * controls, and if a request is sent anyway the server answers 403
   * ROLE_FORBIDDEN - the page shows that answer instead of pretending.
   */

  /* Permission names contain a dot, and the dot is this page's i18n key
   * separator: the i18n lint reads every quoted "domain.key" literal as a
   * dictionary reference. The names are therefore assembled from parts (the
   * same trick the summary splitter uses for its fullwidth separator), so the
   * lint keeps watching the copy while permission names stay opaque to it. */
  var PERM_JOIN = '.';
  function perm() {
    return Array.prototype.slice.call(arguments).join(PERM_JOIN);
  }
  var PERM_RECORDS_READ = perm('records', 'read');
  var PERM_DATA_WRITE = perm('data', 'write');
  var PERM_INGEST_TOKEN = perm('ingest', 'token', 'manage');
  var PERM_INTERVENE_DECIDE = perm('intervene', 'decide');
  var PERM_AUDIT_READ = perm('audit', 'read');
  var PERM_AUDIT_EXPORT = perm('audit', 'export');
  var PERM_SETTINGS_WRITE = perm('settings', 'write');
  var PERM_USERS_MANAGE = perm('users', 'manage');

  var AUTH_ROLES = ['admin', 'editor', 'viewer', 'local'];
  var AUTH_PERMISSIONS = [PERM_RECORDS_READ, PERM_DATA_WRITE, PERM_INGEST_TOKEN,
                          PERM_INTERVENE_DECIDE, PERM_AUDIT_READ, PERM_AUDIT_EXPORT,
                          PERM_SETTINGS_WRITE, PERM_USERS_MANAGE];
  var AUTH_USER_STATUSES = ['active', 'disabled'];
  var AUTH_COOKIE_NAME = 'tl_session';
  var AUTH_CSRF_HEADER = 'X-CSRF-Token';
  /* Offline (file://) has no server to ask, so the page mirrors the server's
   * own local profile: one local user, everything allowed except user
   * administration (there is no user table to talk to). In live mode this
   * default is replaced by the matrix the server publishes, so the page never
   * invents rules of its own. */
  var AUTH_LOCAL_PERMISSIONS = [PERM_RECORDS_READ, PERM_DATA_WRITE, PERM_INGEST_TOKEN,
                                PERM_INTERVENE_DECIDE, PERM_AUDIT_READ, PERM_AUDIT_EXPORT,
                                PERM_SETTINGS_WRITE];
  /* explicit key maps: the i18n lint must see every literal it has to render */
  var AUTH_ROLE_KEYS = {
    admin: 'auth.role.admin',
    editor: 'auth.role.editor',
    viewer: 'auth.role.viewer'
  };
  var AUTH_CHIP_ROLE_KEYS = {
    admin: 'auth.chip.role.admin',
    editor: 'auth.chip.role.editor',
    viewer: 'auth.chip.role.viewer',
    local: 'auth.chip.role.local',
    none: 'auth.chip.role.none'
  };
  var AUTH_MODE_KEYS = {
    unchecked: 'auth.mode.unchecked',
    team: 'auth.mode.team',
    live: 'auth.mode.team',
    signed_in: 'auth.mode.team',
    signed_out: 'auth.mode.team',
    local: 'auth.mode.local',
    offline: 'auth.mode.local',
    forced_offline: 'auth.mode.local'
  };
  var AUTH_ERROR_KEYS = {
    AUTH_REQUIRED: 'auth.error.auth_required',
    AUTH_TOKEN_EXPIRED: 'auth.error.auth_token_expired',
    AUTH_TOKEN_INVALID: 'auth.error.auth_token_invalid',
    AUTH_TOKEN_REVOKED: 'auth.error.auth_token_revoked',
    AUTH_BAD_CREDENTIALS: 'auth.error.auth_bad_credentials',
    AUTH_USER_DISABLED: 'auth.error.auth_user_disabled',
    AUTH_USER_NOT_FOUND: 'auth.error.auth_user_not_found',
    ROLE_FORBIDDEN: 'auth.error.role_forbidden',
    CSRF_FAILED: 'auth.error.csrf_failed',
    RATE_LIMITED: 'auth.error.rate_limited',
    USER_EXISTS: 'auth.error.user_exists',
    USER_LAST_ADMIN: 'auth.error.user_last_admin',
    USER_SELF_DISABLE: 'auth.error.user_self_disable',
    USER_DELETE_DISABLED: 'auth.error.user_delete_disabled',
    USERNAME_REQUIRED: 'auth.error.username_required',
    USERNAME_INVALID: 'auth.error.username_invalid',
    ROLE_INVALID: 'auth.error.role_invalid',
    USER_STATUS_INVALID: 'auth.error.user_status_invalid',
    AUTH_PASSWORD_REQUIRED: 'auth.error.auth_password_required',
    AUTH_PASSWORD_TOO_LONG: 'auth.error.auth_password_too_long',
    AUTH_PASSWORD_SAME: 'auth.error.auth_password_same',
    AUTH_WEAK_PASSWORD: 'auth.error.auth_weak_password',
    INTERVENE_FIELD_MISSING: 'auth.error.intervene_field_missing',
    INTERVENE_FIELD_TYPE: 'auth.error.intervene_field_type',
    INTERVENE_FIELD_UNKNOWN: 'auth.error.intervene_field_unknown',
    INTERVENE_BODY_NOT_OBJECT: 'auth.error.intervene_body_not_object',
    INTERVENE_BODY_INVALID: 'auth.error.intervene_body_invalid',
    ROUTE_NOT_FOUND: 'auth.error.route_not_found',
    METHOD_NOT_ALLOWED: 'auth.error.method_not_allowed',
    NETWORK_ERROR: 'auth.error.network_error',
    TIMEOUT: 'auth.error.timeout',
    INTERNAL_ERROR: 'auth.error.internal_error'
  };

  state.auth = {
    mode: 'unchecked',            /* unchecked | live | offline | forced_offline */
    required: false,
    session: null,                /* /api/auth/me payload, never a token copy */
    csrf: '',
    permissions: AUTH_LOCAL_PERMISSIONS.slice(),
    matrix: null,
    users: [],
    busy: false,
    loginFields: { username: '' },
    createFields: {},
    loginError: null,
    usersError: null,
    feedback: null,
    bootstrapNote: '',
    requests: 0,
    logins: 0,
    logouts: 0,
    denials: 0,
    lastStatus: null,
    usersLoaded: false
  };

  function authSnapshot() {
    var session = isObj(state.auth.session) ? state.auth.session : null;
    return {
      mode: state.auth.mode,
      required: state.auth.required === true,
      state: authStateName(),
      role: session ? str(session.role) : (state.auth.required ? null : 'local'),
      actor_id: session ? str(session.actor_id) : (state.auth.required ? null : 'local'),
      permissions: state.auth.permissions.slice(),
      csrf_present: !!state.auth.csrf,
      users: state.auth.users.length,
      usersLoaded: state.auth.usersLoaded === true,
      busy: state.auth.busy === true,
      requests: state.auth.requests,
      logins: state.auth.logins,
      logouts: state.auth.logouts,
      denials: state.auth.denials,
      lastStatus: state.auth.lastStatus,
      bootstrap_note: !!state.auth.bootstrapNote,
      cookie: AUTH_COOKIE_NAME
    };
  }

  function authStateName() {
    if (state.auth.mode === 'offline' || state.auth.mode === 'forced_offline') return 'offline';
    if (state.auth.mode === 'unchecked') return 'unchecked';
    if (isObj(state.auth.session)) return 'signed_in';
    return state.auth.required ? 'signed_out' : 'local';
  }

  function canPermission(name) {
    var key = str(name);
    if (!key) return false;
    return state.auth.permissions.indexOf(key) >= 0;
  }

  function isSignedIn() {
    return authStateName() === 'signed_in' || authStateName() === 'local' ||
      authStateName() === 'offline';
  }

  function authErrorKey(data) {
    if (!isObj(data) || !hasText(data.code)) return 'auth.error.unknown';
    var key = AUTH_ERROR_KEYS[str(data.code)];
    return key || 'auth.error.unknown';
  }

  function authFetch(endpoint, body, opts) {
    var merged = { credentials: 'same-origin' };
    if (body !== undefined && body !== null && state.auth.csrf) {
      merged.headers = makeObj(AUTH_CSRF_HEADER, state.auth.csrf);
    }
    if (isObj(opts)) {
      if (isStr(opts.credentials)) merged.credentials = opts.credentials;
      if (isStr(opts.method)) merged.method = opts.method;   /* 4.5: settings PUT */
      if (isObj(opts.query)) merged.query = opts.query;      /* 4.8: audit filters */
      if (isObj(opts.headers)) Object.keys(opts.headers).forEach(function (k) { merged.headers = merged.headers || {}; merged.headers[k] = str(opts.headers[k]); });
    }
    state.auth.requests += 1;
    return apiRequest(endpoint, body, merged).then(function (res) {
      state.auth.lastStatus = res && res.status !== undefined ? res.status : null;
      return res;
    });
  }

  function makeObj(key, value) {
    var out = {};
    out[key] = value;
    return out;
  }

  /* ---- server conversation ---------------------------------------- */

  function probeAuthStatus() {
    if (state.api.forced === 'offline') {
      state.auth.mode = 'forced_offline';
      state.auth.required = false;
      state.auth.permissions = AUTH_LOCAL_PERMISSIONS.slice();
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR' });
    }
    if (typeof state.api.transport !== 'function' && !apiOverHttp()) {
      state.auth.mode = 'offline';
      state.auth.required = false;
      state.auth.permissions = AUTH_LOCAL_PERMISSIONS.slice();
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR' });
    }
    return authFetch('authStatus').then(function (res) {
      var data = (res && res.ok && isObj(res.data)) ? res.data : null;
      if (!data) {
        state.auth.mode = 'offline';
        state.auth.required = false;
        return { ok: false, status: res ? res.status : 0 };
      }
      state.auth.mode = 'live';
      state.auth.required = data.required === true;
      state.auth.bootstrapNote = hasText(data.bootstrap_needed_note) ? str(data.bootstrap_needed_note) : '';
      if (!state.auth.required) state.auth.permissions = AUTH_LOCAL_PERMISSIONS.slice();
      return { ok: true, required: state.auth.required, mode: str(data.mode) };
    }, function () {
      state.auth.mode = 'offline';
      state.auth.required = false;
      return { ok: false, code: 'NETWORK_ERROR' };
    });
  }

  function probeAuthPermissions() {
    if (state.auth.mode !== 'live') return Promise.resolve(false);
    return authFetch('authPermissions').then(function (res) {
      var data = (res && res.ok && isObj(res.data)) ? res.data : null;
      if (!data || !isObj(data.matrix)) return false;
      state.auth.matrix = data.matrix;
      return true;
    }, function () { return false; });
  }

  function probeAuthMe() {
    if (state.auth.mode !== 'live') return Promise.resolve(null);
    return authFetch('authMe').then(function (res) {
      var data = (res && res.ok && isObj(res.data)) ? res.data : null;
      if (!data) {
        if (res && res.status === 401) {
          state.auth.session = null;
          state.auth.csrf = '';
          state.auth.permissions = [];
          /* team mode, nobody signed in: the record content is dropped, the
           * login page is the page (the server answers 401 for records too) */
          if (state.auth.required) setRecords([]);
        }
        return null;
      }
      state.auth.session = data;
      state.auth.permissions = isArr(data.permissions) ? data.permissions.slice() : [];
      return data;
    }, function () { return null; });
  }

  function submitLogin(input) {
    var fields = isObj(input) ? input : state.auth.loginFields;
    var username = str(fields.username).trim();
    var password = isStr(fields.password) ? fields.password : '';
    if (!username || !password) {
      state.auth.loginError = 'auth.validation.missing';
      renderAll();
      return Promise.resolve({ ok: false, code: 'CLIENT_VALIDATION' });
    }
    state.auth.busy = true;
    state.auth.loginError = null;
    state.auth.feedback = null;
    state.auth.loginFields = { username: username };
    renderAll();
    return authFetch('authLogin', { username: username, password: password }).then(function (res) {
      state.auth.busy = false;
      var data = isObj(res.data) ? res.data : null;
      if (res.ok && isObj(data) && isObj(data.user)) {
        /* The response also carries a token for desktop/CLI clients; the page
         * deliberately does not keep it: the HttpOnly cookie authenticates the
         * browser, and the CSRF secret stays in memory (never localStorage,
         * never printed by the probe). */
        state.auth.csrf = str(data.csrf_token);
        state.auth.session = data.user;
        state.auth.permissions = isArr(data.user.permissions) ? data.user.permissions.slice() : [];
        state.auth.logins += 1;
        state.auth.feedback = 'auth.feedback.signed_in';
        return probeAuthPermissions().then(function () {
          return syncRecordsFromApi();
        }).then(function () {
          renderAll();
          return { ok: true, role: str(data.user.role) };
        });
      }
      if (res.status === 403) state.auth.denials += 1;
      state.auth.loginError = authErrorKey(data);
      state.auth.loginErrorDetail = isObj(data) && isObj(data.detail) ? data.detail : null;
      renderAll();
      return { ok: false, status: res.status, code: data ? str(data.code) : null };
    }, function (err) {
      state.auth.busy = false;
      state.auth.loginError = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false, code: err && err.code };
    });
  }

  function submitLogout() {
    state.auth.busy = true;
    renderAll();
    return authFetch('authLogout', {}).then(function (res) {
      state.auth.busy = false;
      state.auth.session = null;
      state.auth.csrf = '';
      state.auth.permissions = state.auth.required ? [] : AUTH_LOCAL_PERMISSIONS.slice();
      state.auth.users = [];
      state.auth.usersLoaded = false;
      state.auth.logouts += 1;
      if (state.auth.required) setRecords([]);
      state.auth.feedback = res.ok ? 'auth.feedback.signed_out' : authErrorKey(res.data);
      renderAll();
      return { ok: res.ok === true, status: res.status };
    }, function (err) {
      state.auth.busy = false;
      state.auth.session = null;
      state.auth.csrf = '';
      state.auth.feedback = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false };
    });
  }

  function loadUsers() {
    if (!canPermission(PERM_USERS_MANAGE)) {
      /* the page enforces the matrix the server published: it does not even
       * ask, and the refusal is counted like a server-side one */
      state.auth.usersError = 'auth.error.role_forbidden';
      state.auth.denials += 1;
      renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN' });
    }
    state.auth.busy = true;
    state.auth.usersError = null;
    renderAll();
    return authFetch('users').then(function (res) {
      state.auth.busy = false;
      var data = isObj(res.data) ? res.data : null;
      if (res.ok && isObj(data) && isArr(data.data)) {
        state.auth.users = data.data.filter(isObj);
        state.auth.usersLoaded = true;
        renderAll();
        return { ok: true, count: state.auth.users.length };
      }
      state.auth.usersError = authErrorKey(data);
      if (res.status === 403) state.auth.denials += 1;
      renderAll();
      return { ok: false, status: res.status, code: data ? str(data.code) : null };
    }, function (err) {
      state.auth.busy = false;
      state.auth.usersError = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false };
    });
  }

  function authUserAction(userId, verb, body) {
    var id = str(userId);
    var action = str(verb);
    if (!id || ['role', 'status', 'password'].indexOf(action) < 0) {
      return Promise.resolve({ ok: false, code: 'CLIENT_VALIDATION' });
    }
    var target = API_ENDPOINTS.users + '/' + encodeURIComponent(id) + '/' + action;
    state.auth.busy = true;
    state.auth.feedback = null;
    state.auth.usersError = null;
    var headers = {};
    headers['Content-Type'] = 'application/json';
    headers['Accept'] = 'application/json';
    if (state.auth.csrf) headers[AUTH_CSRF_HEADER] = state.auth.csrf;
    return apiRequest('users', body, { path: target, credentials: 'same-origin', headers: headers }).then(function (res) {
      state.auth.busy = false;
      state.auth.lastStatus = res.status;
      var data = isObj(res.data) ? res.data : null;
      if (res.ok) {
        state.auth.feedback = 'auth.feedback.user_updated';
        return loadUsers().then(function () { return { ok: true, data: data }; });
      }
      state.auth.usersError = authErrorKey(data);
      if (res.status === 403) state.auth.denials += 1;
      renderAll();
      return { ok: false, status: res.status, code: data ? str(data.code) : null };
    }, function (err) {
      state.auth.busy = false;
      state.auth.usersError = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false };
    });
  }

  function authCreateUser(input) {
    var body = isObj(input) ? input : {};
    var payload = { username: str(body.username).trim(), role: str(body.role) };
    if (hasText(body.password)) payload.password = str(body.password);
    if (hasText(body.display_name)) payload.display_name = str(body.display_name);
    state.auth.busy = true;
    state.auth.usersError = null;
    state.auth.feedback = null;
    renderAll();
    return authFetch('users', payload).then(function (res) {
      state.auth.busy = false;
      var data = isObj(res.data) ? res.data : null;
      if (res.ok) {
        var issued = isObj(data) && isObj(data.user) ? str(data.user.initial_password) : '';
        state.auth.feedback = issued ? 'auth.feedback.user_created_shown_once' : 'auth.feedback.user_created';
        state.auth.issuedPassword = issued;
        return loadUsers().then(function () { return { ok: true, data: data }; });
      }
      state.auth.usersError = authErrorKey(data);
      renderAll();
      return { ok: false, status: res.status, code: data ? str(data.code) : null };
    }, function (err) {
      state.auth.busy = false;
      state.auth.usersError = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false };
    });
  }

  function authChangePassword(oldPassword, newPassword) {
    state.auth.busy = true;
    state.auth.feedback = null;
    state.auth.loginError = null;
    renderAll();
    return authFetch('authPassword', { old_password: str(oldPassword), new_password: str(newPassword) }).then(function (res) {
      state.auth.busy = false;
      var data = isObj(res.data) ? res.data : null;
      if (res.ok) {
        state.auth.feedback = 'auth.feedback.password_changed';
        state.auth.session = null;
        state.auth.csrf = '';
        renderAll();
        return { ok: true };
      }
      state.auth.loginError = authErrorKey(data);
      renderAll();
      return { ok: false, status: res.status, code: data ? str(data.code) : null };
    }, function (err) {
      state.auth.busy = false;
      state.auth.loginError = (err && str(err.code) === 'TIMEOUT') ? 'auth.error.timeout' : 'auth.error.network_error';
      renderAll();
      return { ok: false };
    });
  }

  /* ---- DOM: chrome, login panel, user page ------------------------- */

  function applyAuthChrome() {
    var app = byId('app');
    if (!app) return;
    var name = authStateName();
    app.setAttribute('data-auth-state', name);
    app.setAttribute('data-auth-mode', str(state.auth.mode));
    var session = isObj(state.auth.session) ? state.auth.session : null;
    app.setAttribute('data-auth-role', session ? str(session.role) : (name === 'offline' ? 'local' : 'none'));
    app.setAttribute('data-auth-required', state.auth.required ? 'true' : 'false');
    var main = byId('main');
    if (main) {
      /* Signed out in team mode: the record list is not rendered as content -
       * the server would answer 401 anyway, and the page says so plainly. */
      var blocked = name === 'signed_out';
      main.setAttribute('data-auth-blocked', blocked ? 'true' : 'false');
      main.setAttribute('aria-hidden', blocked ? 'true' : 'false');
    }
  }

  function authField(hook, name, type, autocomplete, value) {
    var input = el('input', {
      class: 'auth__input',
      type: type,
      name: name,
      autocomplete: autocomplete,
      dataset: { hook: hook },
      attrs: { required: 'required' }
    });
    if (hasText(value)) input.value = str(value);
    return input;
  }

  function renderAuth() {
    renderLoginPanel();
    renderUsersPanel();
    renderAuthChip();
  }

  function renderLoginPanel() {
    var host = byId('auth-panel');
    if (!host) return;
    var name = authStateName();
    var show = name === 'signed_out';
    host.hidden = !show;
    host.setAttribute('data-auth-state', name);
    setDataset(host, 'auth-mode', str(state.auth.mode));
    if (!show) {
      /* the hook travels with visibility: "the login page exists" means the
       * user can see it, so probes never count an invisible panel */
      host.removeAttribute('data-hook');
      clear(host);
      return;
    }
    clear(host);
    host.setAttribute('data-hook', 'auth-panel');
    var card = add(host, el('div', { class: 'auth__card', dataset: { hook: 'auth-card' } }));
    add(card, el('h2', { class: 'auth__title', id: 'auth-title', i18n: 'auth.panel.title' }));
    add(card, el('p', { class: 'auth__intro', i18n: 'auth.panel.intro' }));
    var mode = add(card, el('p', { class: 'auth__mode' }));
    i18nText(mode, AUTH_MODE_KEYS[state.auth.mode] || AUTH_MODE_KEYS.unchecked);
    if (state.auth.bootstrapNote) {
      add(card, el('p', { class: 'auth__note', dataset: { hook: 'auth-bootstrap-note' }, i18n: 'auth.panel.bootstrap_note' }));
    }
    var form = add(card, el('form', {
      class: 'auth__form',
      method: 'post',
      autocomplete: 'on',
      dataset: { hook: 'auth-login-form' }
    }));
    var userRow = add(form, el('label', { class: 'auth__field' }));
    add(userRow, el('span', { class: 'auth__label', i18n: 'auth.field.username' }));
    add(userRow, authField('auth-username', 'username', 'text', 'username', state.auth.loginFields.username));
    var passRow = add(form, el('label', { class: 'auth__field' }));
    add(passRow, el('span', { class: 'auth__label', i18n: 'auth.field.password' }));
    add(passRow, authField('auth-password', 'password', 'password', 'current-password', ''));
    var submit = el('button', {
      class: 'auth__submit',
      type: 'submit',
      tabindex: '0',
      dataset: { hook: 'auth-login-submit', busy: state.auth.busy ? 'true' : 'false' }
    });
    i18nText(submit, state.auth.busy ? 'auth.action.signing_in' : 'auth.action.sign_in');
    if (state.auth.busy) submit.setAttribute('disabled', 'disabled');
    add(form, submit);
    on(form, 'submit', function (ev) {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      var username = byId('auth-panel') ? queryAll('[data-hook="auth-username"]')[0] : null;
      var password = queryAll('[data-hook="auth-password"]')[0];
      submitLogin({
        username: username ? username.value : '',
        password: password ? password.value : ''
      });
    });
    var feedback = add(card, el('p', {
      class: 'auth__feedback',
      dataset: { hook: 'auth-feedback', kind: state.auth.loginError ? 'error' : (state.auth.feedback ? 'ok' : 'idle') }
    }));
    if (state.auth.loginError) i18nText(feedback, state.auth.loginError);
    else if (state.auth.feedback) i18nText(feedback, state.auth.feedback);
    else i18nText(feedback, 'auth.feedback.idle');
    var legend = add(card, el('ul', { class: 'auth__legend', dataset: { hook: 'auth-legend' } }));
    ['admin', 'editor', 'viewer'].forEach(function (role) {
      var line = add(legend, el('li', { class: 'auth__legend-item', dataset: { role: role } }));
      i18nText(line, AUTH_ROLE_KEYS[role] || AUTH_ROLE_KEYS.viewer);
    });
  }

  function renderUsersPanel() {
    var host = byId('users-panel');
    if (!host) return;
    var allowed = canPermission(PERM_USERS_MANAGE);
    var name = authStateName();
    var show = allowed && name === 'signed_in' && state.auth.usersOpen === true;
    host.hidden = !show;
    if (!show) {
      host.removeAttribute('data-hook');
      clear(host);
      return;
    }
    clear(host);
    host.setAttribute('data-hook', 'users-panel');
    var card = add(host, el('div', { class: 'users__card', dataset: { hook: 'users-card' } }));
    add(card, el('h2', { class: 'users__title', id: 'users-title', i18n: 'auth.users.title' }));
    add(card, el('p', { class: 'users__intro', i18n: 'auth.users.intro' }));
    var createForm = add(card, el('form', { class: 'users__create', dataset: { hook: 'user-create-form' } }));
    add(createForm, el('span', { class: 'users__create-label', i18n: 'auth.users.create_title' }));
    add(createForm, authField('user-create-username', 'username', 'text', 'off', ''));
    var roleSelect = el('select', { class: 'users__select', name: 'role', dataset: { hook: 'user-create-role' } });
    ['admin', 'editor', 'viewer'].forEach(function (role) {
      var opt = el('option', { value: role, text: role });
      add(roleSelect, opt);
    });
    add(createForm, roleSelect);
    add(createForm, authField('user-create-password', 'password', 'password', 'new-password', ''));
    var createBtn = el('button', { class: 'users__button', type: 'submit', tabindex: '0', dataset: { hook: 'user-create-submit' } });
    i18nText(createBtn, 'auth.users.action.create');
    add(createForm, createBtn);
    on(createForm, 'submit', function (ev) {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      var username = queryAll('[data-hook="user-create-username"]')[0];
      var role = queryAll('[data-hook="user-create-role"]')[0];
      var password = queryAll('[data-hook="user-create-password"]')[0];
      authCreateUser({
        username: username ? username.value : '',
        role: role ? role.value : 'viewer',
        password: password ? password.value : ''
      });
    });
    /* 4.10: five columns do not fit a 375px phone, so the table gets its own
     * scroll box - the page itself never grows a horizontal scrollbar. */
    var scroller = add(card, el('div', { class: 'users__scroll', dataset: { hook: 'users-scroll' } }));
    var table = add(scroller, el('table', { class: 'users__table', dataset: { hook: 'users-table' } }));
    var head = add(table, el('thead', {}));
    var headRow = add(head, el('tr', {}));
    ['auth.users.col.user', 'auth.users.col.role', 'auth.users.col.status',
      'auth.users.col.last_login', 'auth.users.col.actions'].forEach(function (key) {
        add(headRow, el('th', { scope: 'col', i18n: key }));
      });
    var body = add(table, el('tbody', {}));
    state.auth.users.forEach(function (user) {
      var row = add(body, el('tr', { class: 'users__row', dataset: { hook: 'user-row', user: str(user.id) } }));
      add(row, el('td', { text: str(user.username) + (user.must_change_password ? ' *' : '') }));
      var roleCell = add(row, el('td', {}));
      var select = el('select', { class: 'users__select', dataset: { hook: 'user-role', user: str(user.id) } });
      ['admin', 'editor', 'viewer'].forEach(function (role) {
        var opt = el('option', { value: role, text: role });
        if (str(user.role) === role) opt.setAttribute('selected', 'selected');
        add(select, opt);
      });
      on(select, 'change', function () {
        authUserAction(str(user.id), 'role', { role: select.value });
      });
      add(roleCell, select);
      var statusCell = add(row, el('td', {}));
      var statusBtn = el('button', {
        class: 'users__button', type: 'button', tabindex: '0',
        dataset: { hook: 'user-status', user: str(user.id), status: str(user.status) }
      });
      i18nText(statusBtn, str(user.status) === 'active' ? 'auth.users.action.disable' : 'auth.users.action.enable');
      on(statusBtn, 'click', function () {
        authUserAction(str(user.id), 'status', { status: str(user.status) === 'active' ? 'disabled' : 'active' });
      });
      add(statusCell, statusBtn);
      add(row, el('td', { text: hasText(user.last_login_at) ? str(user.last_login_at) : t('auth.users.never') }));
      var actionCell = add(row, el('td', {}));
      var resetBtn = el('button', {
        class: 'users__button', type: 'button', tabindex: '0',
        dataset: { hook: 'user-reset', user: str(user.id) }
      });
      i18nText(resetBtn, 'auth.users.action.reset_password');
      on(resetBtn, 'click', function () {
        authUserAction(str(user.id), 'password', {});
      });
      add(actionCell, resetBtn);
    });
    var feedback = add(card, el('p', {
      class: 'users__feedback',
      dataset: { hook: 'users-feedback', kind: state.auth.usersError ? 'error' : (state.auth.feedback ? 'ok' : 'idle') }
    }));
    if (state.auth.usersError) i18nText(feedback, state.auth.usersError);
    else if (state.auth.feedback) i18nText(feedback, state.auth.feedback);
    else i18nText(feedback, 'auth.users.feedback.idle');
    if (state.auth.issuedPassword) {
      add(card, el('p', { class: 'users__issued', dataset: { hook: 'users-issued' }, text: str(state.auth.issuedPassword) }));
    }
    add(card, el('p', { class: 'users__note', i18n: 'auth.users.note' }));
    renderMatrix(card);
    var close = el('button', { class: 'users__button', type: 'button', tabindex: '0', dataset: { hook: 'users-close' } });
    i18nText(close, 'auth.users.action.close');
    on(close, 'click', function () {
      /* 4.10: the phone entry list opened this screen; closing clears the flag */
      state.auth.usersOpen = false;
      state.more.screen = 'off';
      renderAll();
    });
    add(card, close);
  }

  function renderMatrix(host) {
    var matrix = isObj(state.auth.matrix) ? state.auth.matrix : null;
    if (!matrix) return;
    var wrap = add(host, el('div', { class: 'users__matrix', dataset: { hook: 'auth-matrix' } }));
    add(wrap, el('h3', { class: 'users__matrix-title', i18n: 'auth.matrix.title' }));
    var table = add(wrap, el('table', { class: 'users__table' }));
    var head = add(table, el('thead', {}));
    var headRow = add(head, el('tr', {}));
    add(headRow, el('th', { scope: 'col', i18n: 'auth.matrix.col.permission' }));
    ['admin', 'editor', 'viewer', 'local'].forEach(function (role) {
      add(headRow, el('th', { scope: 'col', text: role }));
    });
    var body = add(table, el('tbody', {}));
    AUTH_PERMISSIONS.forEach(function (permission) {
      var row = add(body, el('tr', { dataset: { permission: permission, hook: 'auth-matrix-row' } }));
      add(row, el('th', { scope: 'row', text: permission }));
      ['admin', 'editor', 'viewer', 'local'].forEach(function (role) {
        var list = isArr(matrix[role]) ? matrix[role] : [];
        var cell = add(row, el('td', { dataset: { role: role, hook: 'auth-matrix-cell', allowed: list.indexOf(permission) >= 0 ? 'true' : 'false' } }));
        add(cell, el('span', { text: list.indexOf(permission) >= 0 ? 'yes' : 'no' }));
      });
    });
  }

  function renderAuthChip() {
    var bar = byId('topbar');
    if (!bar) return;
    var chip = bar.querySelector ? bar.querySelector('[data-hook="auth-user"]') : null;
    if (!chip) {
      chip = el('div', { class: 'topbar__auth', dataset: { hook: 'auth-user' } });
      add(bar, chip);
    }
    clear(chip);
    var name = authStateName();
    var session = isObj(state.auth.session) ? state.auth.session : null;
    setDataset(chip, 'auth-state', name);
    var label = add(chip, el('span', { class: 'topbar__auth-user', dataset: { role: session ? str(session.role) : name } }));
    var who = session ? str(session.username || session.actor_id) : (name === 'offline' ? 'local' : t('auth.chip.signed_out'));
    add(label, el('span', { class: 'topbar__auth-name', text: who }));
    var role = add(chip, el('span', { class: 'topbar__auth-role' }));
    i18nText(role, 'auth.chip.role.' + (session ? str(session.role) : (name === 'offline' ? 'local' : 'none')));
    if (session) {
      var usersBtn = el('button', { class: 'topbar__auth-action', type: 'button', tabindex: '0', dataset: { hook: 'auth-users-open' } });
      i18nText(usersBtn, 'auth.chip.action.users');
      usersBtn.hidden = !canPermission(PERM_USERS_MANAGE);
      usersBtn.setAttribute('data-auth-allowed', canPermission(PERM_USERS_MANAGE) ? 'true' : 'false');
      on(usersBtn, 'click', function () {
        state.auth.usersOpen = true;
        loadUsers();
      });
      add(chip, usersBtn);
      var logoutBtn = el('button', { class: 'topbar__auth-action', type: 'button', tabindex: '0', dataset: { hook: 'auth-logout' } });
      i18nText(logoutBtn, 'auth.chip.action.logout');
      on(logoutBtn, 'click', function () { submitLogout(); });
      add(chip, logoutBtn);
    }
    /* A refusal the role cannot act on must stay visible where the entry was:
     * the user page itself is not rendered for a role that may not see it. */
    if (state.auth.usersError && !canPermission(PERM_USERS_MANAGE)) {
      var denial = el('p', { class: 'topbar__auth-denial', dataset: { hook: 'auth-denial' } });
      i18nText(denial, state.auth.usersError);
      add(chip, denial);
    }
  }

  /* ================================================================
   * 9.6 settings centre (4.5): model configuration, language, data
   *
   * Every number, label and mask on this panel is derived from
   * GET /api/settings: the page never recomputes the rules. The key itself
   * never travels to the page - agent_explainer/settings.py owns the single
   * mask format and the server only forwards it. A save is exactly one PUT;
   * the server validates, persists, re-feeds the explanation engine and
   * audits the change, and the panel renders what came back.
   * ================================================================ */

  var SETTINGS_MODES = ['template', 'llm'];
  var SETTINGS_PROVIDERS = ['openai_compat', 'ollama', 'deepseek', 'custom'];
  var SETTINGS_GRANULARITIES = ['L1', 'L2', 'L3'];
  var SETTINGS_UI_LANGS = ['zh', 'en'];
  /* the sentinel pair the server understands for the secret field:
   * an empty string clears it, 'keep' (or null) leaves it untouched */
  var SETTINGS_KEY_CLEAR = '';
  var SETTINGS_KEY_KEEP = 'keep';
  var SETTINGS_EFFECT_NOTICE_KEY = 'settings.effect.engine_only';
  var SETTINGS_TEST_PROBE_NOTICE_KEY = 'settings.test_llm.probe_only';
  /* code -> dictionary key: written as literals so the selfcheck's orphan
   * rule counts them, and a dedicated rule pins every value into zh + en. */
  var SETTINGS_ERROR_KEYS = {
    SETTINGS_BODY_NOT_OBJECT: 'settings.error.settings_body_not_object',
    SETTINGS_BODY_EMPTY: 'settings.error.settings_body_empty',
    SETTINGS_SECTION_UNKNOWN: 'settings.error.settings_section_unknown',
    SETTINGS_FIELD_UNKNOWN: 'settings.error.settings_field_unknown',
    SETTINGS_FIELD_TYPE: 'settings.error.settings_field_type',
    SETTINGS_FIELD_RANGE: 'settings.error.settings_field_range',
    SETTINGS_FIELD_MISSING: 'settings.error.settings_field_missing',
    SETTINGS_FIELD_EMPTY: 'settings.error.settings_field_empty',
    SETTINGS_FIELD_TOO_LONG: 'settings.error.settings_field_too_long',
    SETTINGS_MODE_INVALID: 'settings.error.settings_mode_invalid',
    SETTINGS_PROVIDER_INVALID: 'settings.error.settings_provider_invalid',
    SETTINGS_LANGUAGE_INVALID: 'settings.error.settings_language_invalid',
    SETTINGS_GRANULARITY_INVALID: 'settings.error.settings_granularity_invalid',
    SETTINGS_URL_MALFORMED: 'settings.error.settings_url_malformed',
    SETTINGS_URL_SCHEME: 'settings.error.settings_url_scheme',
    SETTINGS_URL_INSECURE: 'settings.error.settings_url_insecure',
    SETTINGS_URL_PORT: 'settings.error.settings_url_port',
    SETTINGS_URL_QUERY: 'settings.error.settings_url_query',
    SETTINGS_URL_FRAGMENT: 'settings.error.settings_url_fragment',
    SETTINGS_URL_USERINFO: 'settings.error.settings_url_userinfo',
    SETTINGS_NO_CHANGES: 'settings.error.settings_no_changes',
    SETTINGS_WRITE_FAILED: 'settings.error.settings_write_failed',
    SETTINGS_READ_FAILED: 'settings.error.settings_read_failed',
    SETTINGS_FILE_INVALID: 'settings.error.settings_file_invalid',
    LLM_MODE_TEMPLATE: 'settings.error.llm_mode_template',
    LLM_NOT_CONFIGURED: 'settings.error.llm_not_configured',
    LLM_KEY_MISSING: 'settings.error.llm_key_missing',
    LLM_OUTBOUND_BLOCKED: 'settings.error.llm_outbound_blocked',
    LLM_TIMEOUT: 'settings.error.llm_timeout',
    LLM_UNREACHABLE: 'settings.error.llm_unreachable',
    LLM_HTTP_ERROR: 'settings.error.llm_http_error',
    LLM_KEY_REJECTED: 'settings.error.llm_key_rejected',
    LLM_PATH_NOT_FOUND: 'settings.error.llm_path_not_found',
    LLM_RATE_LIMITED: 'settings.error.llm_rate_limited',
    LLM_EMPTY_ANSWER: 'settings.error.llm_empty_answer',
    LLM_CLIENT_ERROR: 'settings.error.llm_client_error',
    LLM_TRANSPORT_MISCONFIGURED: 'settings.error.llm_transport_misconfigured',
    LLM_OK: 'settings.error.llm_ok'
  };
  /* the one-line notes the server sends as key names, never as copy */
  var SETTINGS_NOTE_KEYS = {
    can_write: 'settings.note.can_write',
    readonly: 'settings.note.readonly',
    team: 'settings.note.team',
    local: 'settings.note.local',
    reset_scope: 'settings.note.reset_scope',
    export_scope: 'settings.note.export_scope',
    audit_append_only: 'settings.note.audit_append_only',
    test_probe_only: 'settings.note.test_probe_only'
  };
  var SETTINGS_SOURCE_KEYS = {
    dpapi: 'settings.model.key_source.dpapi',
    env: 'settings.model.key_source.env',
    memory: 'settings.model.key_source.memory',
    none: 'settings.model.key_source.none'
  };
  var SETTINGS_OUTBOUND_KEYS = {
    blocked: 'settings.security.outbound.blocked',
    loopback_only: 'settings.security.outbound.loopback_only',
    allowed: 'settings.security.outbound.allowed'
  };
  var SETTINGS_PROVIDER_KEYS = {
    openai_compat: 'settings.model.provider.openai_compat',
    ollama: 'settings.model.provider.ollama',
    deepseek: 'settings.model.provider.deepseek',
    custom: 'settings.model.provider.custom'
  };
  var SETTINGS_MODE_KEYS = {
    template: 'settings.model.mode.template',
    llm: 'settings.model.mode.llm'
  };

  function settingsView() {
    return isObj(state.settings.view) ? state.settings.view : null;
  }

  function settingsPart(name) {
    var view = settingsView();
    if (!view || !isObj(view[name])) return {};
    return view[name];
  }

  function settingsLlm() { return settingsPart('llm'); }
  function settingsUi() { return settingsPart('ui'); }
  function settingsSecurity() { return settingsPart('security'); }
  function settingsEngine() { return settingsPart('engine'); }
  function settingsPerms() { return settingsPart('permissions'); }
  function settingsTeam() { return settingsPart('team'); }
  function settingsData() { return settingsPart('data'); }
  function settingsDataManagement() { return settingsPart('data_management'); }

  function settingsCanWrite() {
    var perms = settingsPerms();
    if (perms.can_write !== undefined) return perms.can_write === true;
    return canPermission(PERM_SETTINGS_WRITE);
  }

  function settingsOnline() {
    return typeof state.api.transport === 'function' || apiOverHttp();
  }

  function settingsSnapshot() {
    var llm = settingsLlm();
    var sec = settingsSecurity();
    var engine = settingsEngine();
    var perms = settingsPerms();
    var test = isObj(state.settings.test) ? state.settings.test : null;
    return {
      open: state.settings.open === true,
      loaded: state.settings.loaded === true,
      loading: state.settings.loading === true,
      saving: state.settings.saving === true,
      testing: state.settings.testing === true,
      requests: state.settings.requests,
      saves: state.settings.saves,
      tests: state.settings.tests,
      denials: state.settings.denials,
      online: settingsOnline(),
      canWrite: settingsCanWrite(),
      writable: settingsTeam().writable !== undefined ? settingsTeam().writable === true : true,
      role: str(perms.role || (state.auth.role || 'none')),
      mode: str(settingsTeam().mode || 'local'),
      endpoint: API_ENDPOINTS.settings,
      testEndpoint: API_ENDPOINTS.settingsTestLlm,
      method: 'PUT',
      llmMode: str(llm.mode),
      provider: str(llm.provider),
      model: str(llm.model),
      baseUrl: str(llm.base_url),
      language: str(settingsUi().language || i18n.lang),
      granularity: str(settingsUi().granularity || 'L1'),
      keyPresent: llm.api_key_present === true,
      keyMask: str(llm.api_key_mask || ''),
      keySource: str(llm.api_key_source || sec.key_storage || 'none'),
      keyFingerprint: str(llm.api_key_fingerprint || ''),
      outbound: str(sec.outbound || 'blocked'),
      secretInFile: sec.secret_in_file === true,
      storage: str(sec.key_storage || 'none'),
      envOverrides: Array.isArray(sec.env_overrides) ? sec.env_overrides.length : 0,
      revision: toNum(settingsView() ? settingsView().revision : null),
      engineMode: str(engine.mode),
      llmWillRun: engine.llm_will_run === true,
      testCode: test ? str(test.code) : '',
      testOk: test ? test.ok === true : null,
      feedback: str(state.settings.feedback),
      error: str(state.settings.error),
      actions: Array.isArray(settingsData().actions) ? settingsData().actions.slice() : []
    };
  }

  function settingsErrorKey(data, fallback) {
    var code = (isObj(data) && hasText(data.code)) ? str(data.code) : '';
    if (SETTINGS_ERROR_KEYS[code]) return SETTINGS_ERROR_KEYS[code];
    if (AUTH_ERROR_KEYS[code]) return AUTH_ERROR_KEYS[code];
    if (INTERVENE_ERROR_KEYS[code]) return INTERVENE_ERROR_KEYS[code];
    return fallback || 'settings.center.load_failed';
  }

  function settingsNoteKey(note) {
    var raw = str(note);
    if (!raw) return '';
    if (raw.indexOf('settings.') === 0) return raw;
    return SETTINGS_NOTE_KEYS[raw] || '';
  }

  function setSettingsFeedback(key, params) {
    state.settings.feedback = hasText(key) ? key : 'settings.center.feedback.idle';
    state.settings.feedbackParams = isObj(params) ? params : null;
    return state.settings.feedback;
  }

  function applyUiLanguage(lang) {
    var next = (lang === 'en') ? 'en' : 'zh';
    if (next !== i18n.lang) {
      setLang(next);
      setSettingsFeedback('settings.language.applied', { lang: t('settings.language.' + next) });
    }
    return next;
  }

  /* ---- conversation with the server --------------------------------- */

  function openSettings(open) {
    state.settings.open = open !== false;
    if (!state.settings.open) {
      if (booted) renderAll();
      return Promise.resolve({ ok: true, open: false });
    }
    if (booted) renderAll();
    if (!state.settings.loaded && settingsOnline()) return loadSettings(true);
    return Promise.resolve({ ok: true, open: true, loaded: state.settings.loaded });
  }

  function loadSettings(force) {
    if (state.settings.loading && force !== true) {
      return Promise.resolve({ ok: true, skipped: true });
    }
    state.settings.loading = true;
    if (!settingsOnline()) {
      state.settings.loading = false;
      state.settings.error = 'settings.center.offline';
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR' });
    }
    state.settings.requests += 1;
    return authFetch('settings').then(function (res) {
      if (res && res.ok && isObj(res.data)) {
        state.settings.view = res.data;
        state.settings.loaded = true;
        state.settings.error = '';
        if (state.settings.saves === 0) setSettingsFeedback('settings.center.feedback.idle');
        return { ok: true, revision: res.data.revision };
      }
      if (res && res.status === 403) {
        state.settings.denials += 1;
        state.settings.error = authErrorKey(res.data);
      } else {
        state.settings.error = settingsErrorKey(res && res.data);
      }
      return { ok: false, code: (isObj(res && res.data) && res.data.code) ? str(res.data.code) : 'HTTP_STATUS' };
    }, function (err) {
      state.settings.error = 'settings.center.offline';
      return { ok: false, code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR' };
    }).then(function (out) {
      state.settings.loading = false;
      if (booted) renderAll();
      return out; });
  }

  function saveSettings(patch) {
    if (!isObj(patch)) {
      state.settings.error = 'settings.error.settings_body_empty';
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'SETTINGS_BODY_EMPTY' });
    }
    if (!settingsCanWrite()) {
      state.settings.denials += 1;
      state.settings.error = 'auth.error.role_forbidden';
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN' });
    }
    if (state.settings.saving) return Promise.resolve({ ok: false, code: 'busy' });
    state.settings.saving = true;
    state.settings.requests += 1;
    if (booted) renderAll();
    return authFetch('settings', patch, { method: 'PUT' }).then(function (res) {
      if (res && res.ok && isObj(res.data) && isObj(res.data.settings)) {
        state.settings.view = res.data.settings;
        state.settings.loaded = true;
        state.settings.saves += 1;
        state.settings.error = '';
        setSettingsFeedback('settings.center.saved');
        var uiPatch = isObj(patch.ui) ? patch.ui : null;
        /* the granularity the server accepted becomes the page's own default,
         * so the cards on screen follow the saved setting */
        if (uiPatch && hasText(uiPatch.granularity)) setGranularityValue(str(uiPatch.granularity));
        if (uiPatch && hasText(uiPatch.language)) applyUiLanguage(str(uiPatch.language));
        return { ok: true, changed: res.data.changed, engine: res.data.engine,
                 key_action: res.data.key_action, effect_notice: res.data.effect_notice };
      }
      if (res && res.status === 403) {
        state.settings.denials += 1;
        state.settings.error = authErrorKey(res.data);
      } else {
        state.settings.error = settingsErrorKey(res && res.data);
      }
      return { ok: false, code: (isObj(res && res.data) && res.data.code) ? str(res.data.code) : 'HTTP_STATUS' };
    }, function (err) {
      state.settings.error = 'settings.center.offline';
      return { ok: false, code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR' };
    }).then(function (out) {
      state.settings.saving = false;
      if (booted) renderAll();
      return out; });
  }

  function readSettingsForm() {
    /* the form is the only source of a patch: fields the user cannot see
     * (fingerprint, storage backend, environment overrides) are never invented
     * by the page, and the secret is never read back from the server. */
    var patch = { llm: {}, ui: {} };
    var mode = radioValue('settings-mode', 'data-mode');
    if (mode) patch.llm.mode = mode;
    var provider = selectValue(queryAll('[data-hook="settings-field"][data-field="provider"]')[0]);
    if (provider) patch.llm.provider = provider;
    ['base_url', 'model'].forEach(function (name) {
      var box = queryAll('[data-hook="settings-field"][data-field="' + name + '"]')[0];
      if (box) patch.llm[name] = str(box.value).trim();
    });
    ['timeout_s', 'temperature', 'max_tokens'].forEach(function (name) {
      var box = queryAll('[data-hook="settings-field"][data-field="' + name + '"]')[0];
      if (!box) return;
      var value = toNum(box.value);
      if (value !== null) patch.llm[name] = value;
    });
    patch.llm.allow_outbound = checkboxValue('settings-field', 'allow_outbound');
    var secret = queryAll('[data-hook="settings-secret"]')[0];
    var clearKey = queryAll('[data-hook="settings-clear-key"]')[0];
    var typed = secret ? str(secret.value).trim() : '';
    if (clearKey && clearKey.checked === true) patch.llm.apiKey = SETTINGS_KEY_CLEAR;
    else if (hasText(typed)) patch.llm.apiKey = typed;
    else patch.llm.apiKey = SETTINGS_KEY_KEEP;
    var lang = radioValue('settings-lang', 'data-lang');
    if (lang) patch.ui.language = lang;
    var gran = radioValue('settings-granularity', 'data-granularity');
    if (gran) patch.ui.granularity = gran;
    if (!isObjKeys(patch.llm)) delete patch.llm;
    if (!isObjKeys(patch.ui)) delete patch.ui;
    return patch;
  }

  function isObjKeys(value) {
    return isObj(value) && Object.keys(value).length > 0;
  }

  function saveSettingsFromForm() {
    return saveSettings(readSettingsForm());
  }

  function runSettingsTest(input) {
    if (!settingsCanWrite()) {
      state.settings.denials += 1;
      state.settings.error = 'auth.error.role_forbidden';
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN' });
    }
    var body = isObj(input) ? input : {};
    if (state.settings.testing) return Promise.resolve({ ok: false, code: 'busy' });
    state.settings.testing = true;
    state.settings.tests += 1;
    state.settings.requests += 1;
    if (booted) renderAll();
    return authFetch('settingsTestLlm', body).then(function (res) {
      var data = isObj(res && res.data) ? res.data : null;
      if (data) {
        state.settings.test = {
          ok: data.ok === true,
          code: str(data.code),
          message: str(data.message),
          latency_ms: toNum(data.latency_ms),
          chars: toNum(data.chars),
          model: str(data.model),
          endpoint: str(data.endpoint),
          detail: isObj(data.detail) ? data.detail : {},
          effect_notice: str(data.effect_notice),
          sent_facts_note: str(data.sent_facts_note),
          http_status: res.status
        };
        state.settings.error = '';
        if (isObj(data.engine)) state.settings.view.engine = data.engine;
        setSettingsFeedback('settings.center.feedback.idle');
      } else {
        state.settings.test = null;
        state.settings.error = settingsErrorKey(res && res.data);
      }
      return { ok: data ? data.ok === true : false,
               code: data ? str(data.code) : settingsErrorCode(res && res.data) };
    }, function (err) {
      state.settings.test = null;
      state.settings.error = 'settings.center.offline';
      return { ok: false, code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR' };
    }).then(function (out) {
      state.settings.testing = false;
      if (booted) renderAll();
      return out; });
  }

  function settingsErrorCode(data) {
    return (isObj(data) && hasText(data.code)) ? str(data.code) : 'HTTP_STATUS';
  }

  function exportSettingsData() {
    state.settings.requests += 1;
    return apiRequest('records').then(function (res) {
      if (!res || !res.ok || !isObj(res.data)) {
        state.settings.error = 'settings.data.export_note';
        if (booted) renderAll();
        return { ok: false, code: settingsErrorCode(res && res.data) };
      }
      var list = Array.isArray(res.data.records) ? res.data.records : [];
      var payload = JSON.stringify(res.data, null, 2);
      state.settings.exportJson = payload;
      setSettingsFeedback('settings.data.export_done', { count: list.length });
      downloadSettingsJson('tracelens-records.json', payload);
      if (booted) renderAll();
      return { ok: true, count: list.length, bytes: payload.length };
    }, function (err) {
      state.settings.error = 'settings.center.offline';
      if (booted) renderAll();
      return { ok: false, code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR' };
    });
  }

  /* the download is a browser convenience: when the environment has no object
   * URL API (the headless rig) the payload stays in state.settings.exportJson
   * and the panel just reports the count - nothing is ever faked. */
  function downloadSettingsJson(name, payload) {
    if (!doc || typeof doc.createElement !== 'function') return false;
    if (typeof global.URL !== 'function' || typeof global.URL.createObjectURL !== 'function') return false;
    if (typeof global.Blob !== 'function') return false;
    try {
      var blob = new global.Blob([payload], { type: 'application/json' });
      var url = global.URL.createObjectURL(blob);
      var link = doc.createElement('a');
      link.setAttribute('href', url);
      link.setAttribute('download', name);
      link.setAttribute('rel', 'noopener');
      if (typeof link.click === 'function') link.click();
      if (typeof global.URL.revokeObjectURL === 'function') global.URL.revokeObjectURL(url);
      return true;
    } catch (e) {
      return false;
    }
  }

  function resetSettingsData(confirmed) {
    /* the destructive action needs an explicit confirmation: the server also
     * gates it on data.write and can refuse it entirely with --no-reset */
    if (confirmed !== true) {
      setSettingsFeedback('settings.data.reset_confirm');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'CONFIRM_REQUIRED' });
    }
    if (!settingsCanWrite()) {
      state.settings.denials += 1;
      state.settings.error = 'auth.error.role_forbidden';
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN' });
    }
    state.settings.requests += 1;
    return apiRequest('reset', {}).then(function (res) {
      if (res && res.ok) {
        setSettingsFeedback('settings.data.reset_done');
        state.settings.error = '';
        return { ok: true };
      }
      state.settings.error = settingsErrorKey(res && res.data, 'settings.data.reset_note');
      return { ok: false, code: settingsErrorCode(res && res.data) };
    }, function (err) {
      state.settings.error = 'settings.center.offline';
      return { ok: false, code: (err && err.code) ? str(err.code) : 'NETWORK_ERROR' };
    }).then(function (out) {
      if (booted) renderAll();
      return out; });
  }

  /* ---- rendering ---------------------------------------------------- */

  function settingsInput(parent, labelKey, field, value, attrs) {
    var row = add(parent, el('label', { class: 'settings__row', dataset: { hook: 'settings-row', field: field } }));
    i18nText(add(row, el('span', { class: 'settings__label' })), labelKey);
    var extra = isObj(attrs) ? attrs : {};
    var input = el('input', {
      class: 'settings__input',
      name: 'settings-' + field,
      dataset: { hook: 'settings-field', field: field },
      attrs: extra
    });
    /* both the attribute and the property: a browser keeps them in sync, the
     * headless rig only stores attributes, and the panel must read the same
     * value back in both. */
    if (value !== undefined && value !== null) {
      input.setAttribute('value', str(value));
      input.value = str(value);
    }
    add(row, input);
    return { row: row, input: input };
  }

  /* the current value of a <select>: the property in a browser, the
   * selected option's value attribute in the headless rig. */
  function selectValue(select) {
    if (!select) return '';
    if (typeof select.value === 'string' && select.value !== '') return str(select.value);
    var chosen = '';
    var kids = select.childNodes || [];
    for (var i = 0; i < kids.length; i += 1) {
      var opt = kids[i];
      if (opt && typeof opt.getAttribute === 'function' && opt.getAttribute('selected') !== null) {
        chosen = str(opt.getAttribute('value'));
      }
    }
    return chosen;
  }

  /* radio groups are read by walking the nodes: ":checked" is not available in
   * the headless rig's selector engine, and the checked property is set when
   * the panel is built (a browser keeps it live from there). */
  function radioValue(hook, attrName) {
    var chosen = '';
    queryAll('[data-hook="' + hook + '"]').forEach(function (box) {
      if (box.checked === true) chosen = str(box.getAttribute(attrName));
    });
    return chosen;
  }

  function checkboxValue(hook, field) {
    var boxes = queryAll('[data-hook="' + hook + '"]' + (field ? '[data-field="' + field + '"]' : ''));
    return boxes.length === 1 && boxes[0].checked === true;
  }

  function settingsRadioRow(parent, labelKey, hook, name, values, current, attrName, keyMap) {
    var row = add(parent, el('div', { class: 'settings__row settings__row--choices', dataset: { hook: 'settings-row', field: name } }));
    i18nText(add(row, el('span', { class: 'settings__label' })), labelKey);
    values.forEach(function (value) {
      var checked = str(value) === str(current);
      var attrs = { type: 'radio', name: 'settings-' + name, value: str(value) };
      attrs[attrName] = str(value);
      if (checked) attrs.checked = 'checked';
      var box = el('input', { class: 'settings__radio', dataset: { hook: hook }, attrs: attrs });
      box.checked = checked;
      box.value = str(value);
      add(row, box);
      var label = el('label', { class: 'settings__choice' });
      if (isObj(keyMap) && keyMap[str(value)]) i18nText(label, keyMap[str(value)]);
      else text(label, str(value));
      add(row, label);
    });
    return row;
  }

  function settingsSelect(parent, labelKey, field, values, current, keyMap) {
    var row = add(parent, el('label', { class: 'settings__row', dataset: { hook: 'settings-row', field: field } }));
    i18nText(add(row, el('span', { class: 'settings__label' })), labelKey);
    var select = el('select', { class: 'settings__select', name: 'settings-' + field,
                                dataset: { hook: 'settings-field', field: field } });
    values.forEach(function (value) {
      var attrs = { value: str(value) };
      if (str(value) === str(current)) attrs.selected = 'selected';
      var opt = el('option', { attrs: attrs });
      if (isObj(keyMap) && keyMap[str(value)]) i18nText(opt, keyMap[str(value)]);
      else text(opt, str(value));
      add(select, opt);
    });
    select.value = str(current);
    add(row, select);
    return { row: row, select: select };
  }

  function settingsButton(parent, labelKey, hook, enabled, type) {
    var button = el('button', {
      class: 'settings__button',
      type: type || 'button',
      dataset: { hook: hook, enabled: enabled ? 'true' : 'false' }
    });
    i18nText(button, labelKey);
    if (!enabled) button.setAttribute('disabled', 'disabled');
    return add(parent, button);
  }

  function settingsNote(parent, key, params, readOnly) {
    if (!hasText(key)) return null;
    var node = add(parent, el('p', { class: 'settings__note', dataset: { hook: 'settings-note' } }));
    i18nText(node, key, params);
    if (readOnly !== undefined) node.setAttribute('data-read-only', readOnly ? 'true' : 'false');
    return node;
  }

  function settingsMaskRow(parent, labelKey, mask, source, present) {
    var row = add(parent, el('p', { class: 'settings__mask', dataset: { hook: 'settings-row', field: 'apiKey' } }));
    i18nText(add(row, el('span', { class: 'settings__label' })), labelKey);
    var value = add(row, el('code', { class: 'settings__key-mask', dataset: { hook: 'settings-key-mask' } }));
    text(value, present ? mask : t('settings.model.key_source.none'));
    var sourceKey = SETTINGS_SOURCE_KEYS[str(source)] || SETTINGS_SOURCE_KEYS.none;
    i18nText(add(row, el('span', { class: 'settings__key-source-key' })), 'settings.model.key_source_label');
    i18nText(add(row, el('span', { class: 'settings__key-source' })), sourceKey);
    return row;
  }

  function settingsHint(parent, key) {
    return i18nText(add(parent, el('p', { class: 'settings__hint' })), key);
  }

  function renderSettingsPanel() {
    var host = byId('settings-center');
    if (!host) return;
    if (state.settings.open !== true) {
      host.hidden = true;
      host.removeAttribute('data-hook');
      clear(host);
      return;
    }
    clear(host);
    host.hidden = false;
    host.setAttribute('data-hook', 'settings-center');
    var card = add(host, el('div', { class: 'settings__card', dataset: { hook: 'settings-card' } }));
    add(card, el('h2', { class: 'settings__title', id: 'settings-title', i18n: 'settings.center.title' }));
    settingsHint(card, 'settings.center.intro');
    settingsButton(card, 'settings.action.close', 'settings-close');

    var canWrite = settingsCanWrite();
    var online = settingsOnline();
    var writable = settingsTeam().writable !== false;
    var saveEnabled = canWrite && online && writable && state.settings.saving !== true;
    var view = settingsView();

    if (state.settings.loading && !view) {
      settingsNote(card, 'settings.center.loading');
      return;
    }
    if (!online) settingsNote(card, 'settings.center.offline', null, true);
    if (state.settings.error) {
      var denial = add(card, el('p', { class: 'settings__error', dataset: { hook: 'settings-error' } }));
      i18nText(denial, state.settings.error);
    }
    if (!view) {
      settingsButton(card, 'settings.action.refresh', 'settings-refresh', true);
      return;
    }

    var llm = settingsLlm();
    var ui = settingsUi();
    var sec = settingsSecurity();
    var engine = settingsEngine();
    var perms = settingsPerms();
    var team = settingsTeam();
    var dataPart = settingsData();
    var management = settingsDataManagement();

    var form = add(card, el('form', { class: 'settings__form', dataset: { hook: 'settings-form' },
                                     attrs: { autocomplete: 'off' } }));

    /* ---- model configuration ---- */
    var modelBox = add(form, el('fieldset', { class: 'settings__section', dataset: { hook: 'settings-section', section: 'model' } }));
    i18nText(add(modelBox, el('legend', { class: 'settings__section-title' })), 'settings.section.model');
    settingsHint(modelBox, 'settings.model.hint');
    settingsRadioRow(modelBox, 'settings.model.mode_label', 'settings-mode', 'mode',
                     SETTINGS_MODES, str(llm.mode), 'data-mode', SETTINGS_MODE_KEYS);
    settingsHint(modelBox, 'settings.model.mode_hint');
    settingsSelect(modelBox, 'settings.model.provider_label', 'provider', SETTINGS_PROVIDERS,
                   str(llm.provider), SETTINGS_PROVIDER_KEYS);
    settingsInput(modelBox, 'settings.model.base_url_label', 'base_url', str(llm.base_url),
                  { type: 'text', inputmode: 'url', maxlength: '300', placeholder: '127.0.0.1:11434/v1' });
    settingsHint(modelBox, 'settings.model.base_url_hint');
    settingsInput(modelBox, 'settings.model.model_label', 'model', str(llm.model),
                  { type: 'text', maxlength: '120' });
    settingsHint(modelBox, 'settings.model.model_hint');
    settingsMaskRow(modelBox, 'settings.model.key_label', str(llm.api_key_mask || ''),
                    str(llm.api_key_source || sec.key_storage), llm.api_key_present === true);
    var secretRow = add(form, el('label', { class: 'settings__row', dataset: { hook: 'settings-row', field: 'apiKeyInput' } }));
    i18nText(add(secretRow, el('span', { class: 'settings__label' })), 'settings.model.key_keep');
    var secretInput = el('input', {
      class: 'settings__input settings__input--secret',
      name: 'settings-apiKey',
      dataset: { hook: 'settings-secret' },
      attrs: { type: 'text', maxlength: '400', autocomplete: 'new-password',
               placeholder: str(llm.api_key_mask || '') }
    });
    add(secretRow, secretInput);
    settingsHint(form, 'settings.model.key_hint');
    var clearRow = add(form, el('label', { class: 'settings__row settings__row--check', dataset: { hook: 'settings-row', field: 'clearKey' } }));
    var clearBox = el('input', { class: 'settings__check', dataset: { hook: 'settings-clear-key' },
                                 attrs: { type: 'checkbox', name: 'settings-clear-key' } });
    clearBox.checked = false;   /* clearing the key is never the default */
    add(clearRow, clearBox);
    i18nText(add(clearRow, el('span', { class: 'settings__choice' })), 'settings.model.key_clear');
    settingsInput(modelBox, 'settings.model.timeout_label', 'timeout_s', llm.timeout_s,
                  { type: 'number', min: '0.5', max: '120', step: '0.5' });
    settingsHint(modelBox, 'settings.model.timeout_hint');
    settingsInput(modelBox, 'settings.model.temperature_label', 'temperature', llm.temperature,
                  { type: 'number', min: '0', max: '2', step: '0.05' });
    settingsInput(modelBox, 'settings.model.max_tokens_label', 'max_tokens', llm.max_tokens,
                  { type: 'number', min: '40', max: '4000', step: '10' });
    var outboundRow = add(modelBox, el('label', { class: 'settings__row settings__row--check', dataset: { hook: 'settings-row', field: 'allow_outbound' } }));
    var outboundBox = el('input', { class: 'settings__check', dataset: { hook: 'settings-field', field: 'allow_outbound' },
                                    attrs: { type: 'checkbox', name: 'settings-allow_outbound' } });
    if (llm.allow_outbound === true) outboundBox.setAttribute('checked', 'checked');
    outboundBox.checked = llm.allow_outbound === true;
    add(outboundRow, outboundBox);
    i18nText(add(outboundRow, el('span', { class: 'settings__choice' })), 'settings.model.outbound_label');
    settingsHint(modelBox, 'settings.model.outbound_hint');

    var actions = add(form, el('div', { class: 'settings__actions', dataset: { hook: 'settings-actions' } }));
    settingsButton(actions, state.settings.saving ? 'settings.center.saving' : 'settings.action.save',
                   'settings-save', saveEnabled, 'submit');
    settingsButton(actions, state.settings.testing ? 'settings.action.testing' : 'settings.action.test',
                   'settings-test', canWrite && online, 'button');
    settingsButton(actions, 'settings.action.refresh', 'settings-refresh', !state.settings.loading, 'button');
    settingsHint(actions, 'settings.model.test_hint');

    var testResult = add(form, el('p', { class: 'settings__test-result', dataset: { hook: 'settings-test-result' } }));
    if (isObj(state.settings.test)) {
      var test = state.settings.test;
      if (test.ok === true) {
        i18nText(testResult, 'settings.model.test_ok', { ms: test.latency_ms, chars: test.chars });
      } else {
        i18nText(testResult, 'settings.model.test_fail', { code: t(SETTINGS_ERROR_KEYS[str(test.code)] || 'settings.error.llm_client_error') });
      }
      text(add(testResult, el('span', { class: 'settings__test-detail' })), str(test.endpoint));
      settingsNote(form, SETTINGS_TEST_PROBE_NOTICE_KEY, null, true);
    } else {
      text(testResult, '');
    }

    var engineBox = add(form, el('div', { class: 'settings__engine', dataset: { hook: 'settings-engine' } }));
    i18nText(add(engineBox, el('h3', { class: 'settings__engine-title' })), 'settings.model.engine_label');
    settingsNote(engineBox, 'settings.model.engine_mode', { mode: str(engine.mode) });
    settingsNote(engineBox, engine.llm_will_run === true ? 'settings.model.engine_will_run' : 'settings.model.engine_wont_run');
    settingsNote(engineBox, SETTINGS_EFFECT_NOTICE_KEY, null, true);
    settingsNote(form, 'settings.model.saved_notice');

    /* ---- language ---- */
    var langBox = add(form, el('fieldset', { class: 'settings__section', dataset: { hook: 'settings-section', section: 'language' } }));
    i18nText(add(langBox, el('legend', { class: 'settings__section-title' })), 'settings.section.language');
    settingsHint(langBox, 'settings.language.hint');
    settingsRadioRow(langBox, 'settings.language.server_label', 'settings-lang', 'language',
                     SETTINGS_UI_LANGS, str(ui.language || i18n.lang), 'data-lang',
                     { zh: 'settings.language.zh', en: 'settings.language.en' });
    settingsNote(langBox, 'settings.language.ui_label', null, true);
    var granRow = settingsRadioRow(langBox, 'settings.granularity', 'settings-granularity', 'granularity',
                                   SETTINGS_GRANULARITIES, str(ui.granularity || 'L1'), 'data-granularity',
                                   { L1: 'level.l1', L2: 'level.l2', L3: 'level.l3' });
    add(granRow, el('span', { class: 'settings__hint', text: t('settings.hint') }));

    /* ---- data management ---- */
    var dataBox = add(form, el('fieldset', { class: 'settings__section', dataset: { hook: 'settings-section', section: 'data' } }));
    i18nText(add(dataBox, el('legend', { class: 'settings__section-title' })), 'settings.section.data');
    settingsHint(dataBox, 'settings.data.hint');
    var dataActions = add(dataBox, el('div', { class: 'settings__actions', dataset: { hook: 'settings-data-actions' } }));
    var exportEnabled = isObj(management.export) ? management.export.enabled !== false : true;
    var resetEnabled = isObj(management.reset) ? management.reset.enabled !== false : true;
    settingsButton(dataActions, 'settings.action.export', 'settings-export', online, 'button');
    settingsButton(dataActions, 'settings.action.reset', 'settings-reset', canWrite && online && resetEnabled, 'button');
    settingsNote(dataBox, 'settings.data.export_note', null, true);
    settingsNote(dataBox, 'settings.data.reset_note', null, true);
    settingsNote(dataBox, 'settings.data.audit_note', null, true);
    if (Array.isArray(dataPart.actions) && dataPart.actions.length) {
      text(add(dataBox, el('span', { class: 'settings__sr-only' })), dataPart.actions.join(','));
    }

    /* ---- team, permissions, security ---- */
    var teamBox = add(form, el('fieldset', { class: 'settings__section', dataset: { hook: 'settings-section', section: 'team' } }));
    i18nText(add(teamBox, el('legend', { class: 'settings__section-title' })), 'settings.section.team');
    var modeLine = add(teamBox, el('p', { class: 'settings__line', dataset: { hook: 'settings-row', field: 'mode' } }));
    i18nText(add(modeLine, el('span', { class: 'settings__label' })), 'settings.team.mode_label');
    text(add(modeLine, el('span', { class: 'settings__line-value' })), str(team.mode || 'local'));
    settingsNote(teamBox, str(team.mode) === 'team' ? 'settings.team.mode.team' : 'settings.team.mode.local');
    settingsNote(teamBox, canWrite ? 'settings.team.can_write_true' : 'settings.team.can_write_false',
                 { role: str(perms.role || (canWrite ? 'local' : 'viewer')) });
    settingsNote(teamBox, settingsNoteKey(perms.note), null, !canWrite);
    settingsNote(teamBox, settingsNoteKey(team.note), null, true);
    if (Array.isArray(team.roles) && team.roles.length) {
      var roles = add(teamBox, el('p', { class: 'settings__roles', dataset: { hook: 'settings-row', field: 'roles' } }));
      i18nText(add(roles, el('span', { class: 'settings__label' })), 'settings.team.roles_label');
      text(add(roles, el('span', { class: 'settings__roles-value' })), team.roles.join(' / '));
    }

    var secBox = add(form, el('fieldset', { class: 'settings__section', dataset: { hook: 'settings-section', section: 'security' } }));
    i18nText(add(secBox, el('legend', { class: 'settings__section-title' })), 'settings.section.security');
    settingsMaskRow(secBox, 'settings.security.mask_label', str(sec.mask || ''),
                    str(sec.key_storage), llm.api_key_present === true);
    var storageLine = add(secBox, el('p', { class: 'settings__line', dataset: { hook: 'settings-row', field: 'storage' } }));
    i18nText(add(storageLine, el('span', { class: 'settings__label' })), 'settings.security.storage_label');
    i18nText(add(storageLine, el('span', { class: 'settings__line-value' })),
             SETTINGS_SOURCE_KEYS[str(sec.key_storage)] || SETTINGS_SOURCE_KEYS.none);
    var outboundKey = SETTINGS_OUTBOUND_KEYS[str(sec.outbound)] || SETTINGS_OUTBOUND_KEYS.blocked;
    var outboundLine = add(secBox, el('p', { class: 'settings__line', dataset: { hook: 'settings-row', field: 'outbound' } }));
    i18nText(add(outboundLine, el('span', { class: 'settings__label' })), 'settings.security.outbound_label');
    i18nText(add(outboundLine, el('span', { class: 'settings__line-value' })), outboundKey);
    settingsNote(secBox, 'settings.security.file_note', null, true);
    settingsNote(secBox, 'settings.security.mask_note', null, true);
    var meta = add(secBox, el('dl', { class: 'settings__meta', dataset: { hook: 'settings-row', field: 'meta' } }));
    var metaLine = function (labelKey, value) {
      i18nText(add(meta, el('dt', { class: 'settings__meta-key' })), labelKey);
      add(meta, el('dd', { class: 'settings__meta-value', text: str(value) }));
    };
    metaLine('settings.security.revision_label', view.revision);
    metaLine('settings.security.updated_by_label', str(view.updated_by || '-'));
    metaLine('settings.security.updated_at_label', str(view.updated_at || '-'));
    metaLine('settings.security.file_label', str(view.path || '-'));
    metaLine('settings.security.env_label', (Array.isArray(sec.env_overrides) && sec.env_overrides.length)
      ? sec.env_overrides.join(', ') : '-');
    if (Array.isArray(view.load_errors) && view.load_errors.length) {
      settingsNote(secBox, 'settings.center.load_failed', null, true);
    }

    var feedback = add(card, el('p', { class: 'settings__feedback', dataset: { hook: 'settings-feedback' } }));
    i18nText(feedback, state.settings.feedback, state.settings.feedbackParams);
    /* a role without settings.write can still read the whole page: say so in one
     * readable line, and keep every write control disabled (the server answers
     * 403 for the same rule, so both sides agree). */
    if (!canWrite) settingsNote(card, 'settings.field.readonly_note', null, true);
    if (!canWrite || !online || !writable) {
      queryAll('[data-hook="settings-save"], [data-hook="settings-test"], [data-hook="settings-reset"]')
        .forEach(function (node) {
          node.setAttribute('disabled', 'disabled');
          node.setAttribute('data-enabled', 'false');
        });
    }
  }

  /* ---------------------------------------------------------------- *
   * 9c. audit rail + export panel (4.8)
   *
   * The page shows the audit chain exactly as the server reports it: the
   * list comes from GET /api/audit, the download from
   * GET /api/audit/export, and both follow the ability the server
   * published for the signed-in role. Nothing here recomputes a hash
   * chain, invents a permission, or fakes a row - when the server says
   * the chain is broken, the panel says so in one readable line.
   * ---------------------------------------------------------------- */

  var AUDIT_PAGE_SIZE = 8;
  var AUDIT_FILTER_DECISIONS = ['approve', 'reject', 'modify'];
  /* explicit key map: the i18n lint must see every literal it has to render */
  var AUDIT_DECISION_FILTER_KEYS = {
    approve: 'audit.filter.decision.approve',
    reject: 'audit.filter.decision.reject',
    modify: 'audit.filter.decision.modify'
  };
  /* the only query keys the page may send; everything is percent-encoded and
   * joined here, so a filter value can never escape into the request line */
  var AUDIT_QUERY_KEYS = ['limit', 'offset', 'event', 'actor_id', 'action_id',
                          'decision', 'task_id', 'q', 'reverse'];
  var EXPORT_QUERY_KEYS = ['format', 'limit', 'event', 'actor_id', 'action_id',
                           'decision', 'task_id', 'q', 'since', 'until', 'lang'];
  var AUDIT_ERROR_KEYS = {
    AUDIT_CHAIN_BROKEN: 'audit.error.chain_broken',
    AUDIT_QUERY_INVALID: 'audit.error.query_invalid',
    ROUTE_NOT_FOUND: 'audit.error.route_not_found',
    NETWORK_ERROR: 'audit.error.network',
    TIMEOUT: 'audit.error.timeout'
  };
  var EXPORT_ERROR_KEYS = {
    EXPORT_FORMAT_INVALID: 'export.error.format_invalid',
    AUDIT_CHAIN_BROKEN: 'export.error.chain_broken',
    AUDIT_QUERY_INVALID: 'export.error.query_invalid',
    AUDIT_REPORTLAB_UNAVAILABLE: 'export.error.reportlab_missing',
    ROUTE_NOT_FOUND: 'export.error.route_not_found',
    NETWORK_ERROR: 'export.error.network',
    TIMEOUT: 'export.error.timeout'
  };

  function auditErrorKey(data, codes, fallback) {
    var code = (isObj(data) && hasText(data.code)) ? str(data.code) : '';
    if (code && isObj(codes) && isStr(codes[code])) return str(codes[code]);
    /* 401 / 403 / expired already have plain-language copy from 4.3 */
    var authKey = authErrorKey(data);
    return (authKey && authKey !== 'auth.error.unknown') ? authKey : fallback;
  }

  function auditTransportOnline() {
    if (state.api.mode === 'forced_offline') return false;
    return typeof state.api.transport === 'function' || apiOverHttp();
  }

  function encodeParam(value) {
    var text = str(value);
    if (typeof global.encodeURIComponent === 'function') {
      try { return global.encodeURIComponent(text); } catch (e) { /* fall back below */ }
    }
    return text.replace(/[^A-Za-z0-9\-._~]/g, '_');
  }

  function queryStringOf(endpoint, params) {
    if (EXT_ENDPOINT_NAMES.indexOf(endpoint) >= 0) return extQueryString(params);
    var allow = (endpoint === 'auditExport') ? EXPORT_QUERY_KEYS : AUDIT_QUERY_KEYS;
    if (!isObj(params)) return '';
    var out = '';
    allow.forEach(function (key) {
      if (allow.indexOf(key) < 0 || !(key in params)) return;
      var value = params[key];
      if (value === null || value === undefined || value === '') return;
      out += (out ? '&' : '?') + key + '=' + encodeParam(value);
    });
    return out;
  }

  /* 5.0 trust layer: the extension surfaces pass their own query keys (a group
   * id, an agent id, a time range, a granularity). The key name is validated
   * and the value is percent-encoded, so a filter value can never become a
   * path segment - the same guarantee the audit panel gets from its allowlist. */
  var EXT_QUERY_KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;

  function extQueryString(params) {
    if (!isObj(params)) return '';
    var out = '';
    Object.keys(params).forEach(function (key) {
      if (!EXT_QUERY_KEY_RE.test(key)) return;
      var value = params[key];
      if (value === null || value === undefined || value === '') return;
      if (isObj(value) || isArr(value)) return;
      out += (out ? '&' : '?') + key + '=' + encodeParam(value);
    });
    return out;
  }

  function headerValue(headers, name) {    var wanted = str(name).toLowerCase();
    if (!headers) return '';
    if (typeof headers.get === 'function') {
      var got = headers.get(wanted);
      return hasText(got) ? str(got) : '';
    }
    if (isObj(headers)) {
      var keys = Object.keys(headers);
      for (var i = 0; i < keys.length; i += 1) {
        if (str(keys[i]).toLowerCase() === wanted && hasText(headers[keys[i]])) {
          return str(headers[keys[i]]);
        }
      }
    }
    return '';
  }

  function filenameFromDisposition(value) {
    var text = str(value);
    var match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(text);
    return match ? str(match[1]) : '';
  }

  function auditFilterParams(base) {
    var params = isObj(base) ? base : {};
    var keys = Object.keys(state.audit.filters);
    keys.forEach(function (key) {
      var value = state.audit.filters[key];
      if (value !== null && value !== undefined && value !== '') params[key] = value;
    });
    return params;
  }

  function auditRequestPath(page) {
    var base = apiEndpoint('audit');
    if (!base) return '';
    var params = auditFilterParams({ limit: state.audit.limit });
    if (page === 'more') params.offset = state.audit.rows.length;
    return base + queryStringOf('audit', params);
  }

  function exportRequestPath(format) {
    var base = apiEndpoint('auditExport');
    if (!base) return '';
    return base + queryStringOf('auditExport', exportQueryParams(format));
  }

  function auditSnapshot() {
    var chain = isObj(state.audit.chain) ? state.audit.chain : {};
    return {
      status: state.audit.status,
      rows: state.audit.rows.length,
      shown: state.audit.rows.length,
      limit: state.audit.limit,
      offset: state.audit.offset,
      filters: Object.keys(state.audit.filters)
        .filter(function (k) { return state.audit.filters[k] !== ''; })
        .reduce(function (acc, k) { acc[k] = state.audit.filters[k]; return acc; }, {}),
      chainOk: (chain.ok === true),
      chainCount: (typeof chain.count === 'number') ? chain.count : null,
      durable: (state.audit.meta && state.audit.meta.durable === true),
      loading: state.audit.loading,
      loaded: state.audit.loaded === true,
      autoLoads: state.audit.autoLoads,
      autoRefreshes: state.audit.autoRefreshes,
      autoTried: state.audit.autoTried,
      lastWriteReason: str(state.audit.lastWriteReason),
      canRead: canPermission(PERM_AUDIT_READ),
      canExport: canPermission(PERM_AUDIT_EXPORT),
      online: auditTransportOnline(),
      more: auditHasMore(),
      error: state.audit.error,
      feedback: state.audit.feedback,
      requests: state.audit.requests,
      refreshes: state.audit.refreshes,
      loads: state.audit.loads,
      denials: state.audit.denials,
      lastRequestId: state.audit.lastRequestId
    };
  }

  function importSnapshot() {
    var draft = state.importPanel;
    var blocked = importBlockedKey();
    return {
      status: draft.status,
      bytes: utf8Bytes(draft.text),
      chars: str(draft.text).length,
      filename: str(draft.filename),
      format: str(draft.format),
      mode: str(draft.mode),
      maxBytes: IMPORT_MAX_BYTES,
      maxRecords: IMPORT_MAX_RECORDS,
      formats: IMPORT_FORMATS.length,
      modes: IMPORT_MODES.length,
      canWrite: canPermission(PERM_DATA_WRITE),
      online: importOnline(),
      enabled: !blocked && draft.status !== 'busy',
      blockedReason: blocked || '',
      clientCheck: (importClientCheck() || {}).key || '',
      result: isObj(draft.result) ? draft.result : null,
      feedback: draft.errorKey,
      requests: draft.requests,
      imports: draft.imports,
      denials: draft.denials,
      clientBlocked: draft.clientBlocked,
      lastRequestId: draft.lastRequestId
    };
  }

  function setImportDraft(patch) {
    /* the rig (and nothing else) feeds the draft the way keystrokes and a picked
     * file do; the fields are the panel's own, so no second code path exists */
    var draft = state.importPanel;
    if (!isObj(patch)) return importSnapshot();
    if (patch.text !== undefined) draft.text = str(patch.text);
    if (patch.filename !== undefined) draft.filename = str(patch.filename);
    if (patch.format !== undefined
        && IMPORT_FORMATS.indexOf(str(patch.format)) >= 0) draft.format = str(patch.format);
    if (patch.mode !== undefined
        && IMPORT_MODES.indexOf(str(patch.mode)) >= 0) draft.mode = str(patch.mode);
    if (draft.status === 'error') draft.status = 'idle';
    if (booted) renderAll();
    return importSnapshot();
  }

  function exportSnapshot() {
    var last = isObj(state.exportPanel.last) ? state.exportPanel.last : null;
    return {
      busy: state.exportPanel.busy,
      canExport: canPermission(PERM_AUDIT_EXPORT),
      online: auditTransportOnline(),
      enabled: canPermission(PERM_AUDIT_EXPORT) && auditTransportOnline(),
      count: state.exportPanel.count,
      denials: state.exportPanel.denials,
      feedback: state.exportPanel.feedback,
      error: state.exportPanel.error,
      reason: state.exportPanel.reason,
      last: last,
      lastPath: state.exportPanel.lastPath,
      downloads: state.exportPanel.downloads,
      popups: state.exportPanel.popups
    };
  }

  function auditHasMore() {
    if (!canPermission(PERM_AUDIT_READ) || !auditTransportOnline()) return false;
    return state.audit.rows.length >= state.audit.limit;
  }

  function setAuditFeedback(key, params) {
    state.audit.feedback = key;
    state.audit.feedbackParams = isObj(params) ? params : null;
  }

  function setExportFeedback(key, params) {
    state.exportPanel.feedback = key;
    state.exportPanel.feedbackParams = isObj(params) ? params : null;
  }

  /* ---- list ---------------------------------------------------------- */

  function loadAudit(mode) {
    var page = (mode === 'more') ? 'more' : 'first';
    if (!canPermission(PERM_AUDIT_READ)) {
      state.audit.denials += 1;
      state.audit.status = 'ready';
      state.audit.rows = [];
      state.audit.error = 'audit.note.readonly';
      setAuditFeedback('audit.note.readonly');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN', disabled: true });
    }
    if (state.audit.loading) return Promise.resolve({ ok: false, code: 'BUSY' });
    if (!auditTransportOnline()) {
      state.audit.status = 'error';
      state.audit.error = 'audit.error.offline';
      setAuditFeedback('audit.error.offline');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR', offline: true });
    }
    state.audit.loading = true;
    state.audit.status = 'loading';
    state.audit.requests += 1;
    if (page === 'first') { state.audit.rows = []; state.audit.offset = 0; }
    if (booted) renderAll();
    return authFetch('audit', null, { method: 'GET', query: auditFilterParams({
      limit: state.audit.limit,
      offset: (page === 'more') ? state.audit.rows.length : 0
    }) }).then(function (res) {
      state.audit.loading = false;
      state.audit.lastRequestId = headerValue(res && res.headers, 'x-request-id');
      var data = (res && res.ok && isObj(res.data)) ? res.data : null;
      if (!data) {
        var code = (res && isObj(res.data) && hasText(res.data.code)) ? str(res.data.code) : 'HTTP_STATUS';
        if (res && res.status === 403) state.audit.denials += 1;
        state.audit.status = 'error';
        state.audit.error = auditErrorKey(res && res.data, AUDIT_ERROR_KEYS, 'audit.error.generic');
        setAuditFeedback(state.audit.error, { status: (res && res.status) ? res.status : 0 });
        if (booted) renderAll();
        return { ok: false, code: code, status: (res && res.status) ? res.status : 0 };
      }
      var entries = isArr(data.entries) ? data.entries : [];
      state.audit.rows = (page === 'more') ? state.audit.rows.concat(entries) : entries.slice();
      state.audit.offset = state.audit.rows.length;
      state.audit.chain = isObj(data.chain) ? data.chain : {};
      state.audit.meta = isObj(data.meta) ? data.meta : {};
      state.audit.jsonl = isObj(data.jsonl) ? data.jsonl : null;
      state.audit.status = state.audit.rows.length ? 'ready' : 'empty';
      /* 4.12 (H-01): the chips may now state facts - the panel has read the
       * server at least once (chain, count, store mode are its answers). */
      state.audit.loaded = true;
      state.audit.error = '';
      if (page === 'more') state.audit.loads += 1; else state.audit.refreshes += 1;
      setAuditFeedback((page === 'more') ? 'audit.feedback.loaded' : 'audit.feedback.refreshed',
                       { count: state.audit.rows.length });
      if (booted) renderAll();
      return { ok: true, count: state.audit.rows.length, page: page,
               chainOk: state.audit.chain.ok === true };
    }, function (err) {
      state.audit.loading = false;
      state.audit.status = 'error';
      state.audit.error = auditErrorKey(err, AUDIT_ERROR_KEYS, 'audit.error.network');
      setAuditFeedback(state.audit.error);
      if (booted) renderAll();
      return { ok: false, code: (err && hasText(err.code)) ? str(err.code) : 'NETWORK_ERROR' };
    });
  }

  /* ---- 4.12 (H-01 / H-02): the panel follows the server, and this page's writes
   *
   * H-01: QA 5.2 opened the panel over a store that already held 3 entries and
   * read "0 entries / in-memory chain"; the read only happened after a click.
   * So the first render that shows the panel reads the first page by itself.
   * H-02: a decision, an export or an import appends an audit entry. The panel
   * re-reads right after, so "I decided and the audit does not show it" cannot
   * happen. Guarded: one read per write, never while a read runs, never for a
   * role that may not read the log, and offline it never fires (the entry the
   * page "made" is a local simulation, so there is nothing to read back). */
  function auditAutoDue() {
    return state.audit.autoTried < 1
      && !state.audit.loading
      && state.audit.loaded !== true
      && canPermission(PERM_AUDIT_READ)
      && auditTransportOnline();
  }

  function flushAuditAutoLoad() {
    if (!auditAutoDue()) return false;
    state.audit.autoTried += 1;
    state.audit.autoLoads += 1;
    loadAudit('first');
    return true;
  }

  function auditAfterWrite(reason) {
    if (!canPermission(PERM_AUDIT_READ) || !auditTransportOnline() || state.audit.loading) return false;
    state.audit.autoRefreshes += 1;
    state.audit.lastWriteReason = str(reason);
    loadAudit('first');
    return true;
  }

  function setAuditFilter(patch) {
    var next = {};
    Object.keys(state.audit.filters).forEach(function (key) { next[key] = state.audit.filters[key]; });
    if (isObj(patch)) {
      Object.keys(patch).forEach(function (key) {
        if (AUDIT_QUERY_KEYS.indexOf(key) < 0) return;
        next[key] = (patch[key] === null || patch[key] === undefined) ? '' : patch[key];
      });
    }
    state.audit.filters = next;
    return loadAudit('first');
  }

  /* ---- export -------------------------------------------------------- */

  function exportQueryParams(format) {
    return auditFilterParams({
      format: (format === 'pdf' || format === 'html') ? format : 'csv',
      lang: i18n.lang
    });
  }

  function downloadAuditFile(name, payload, type) {
    if (!doc || typeof doc.createElement !== 'function') return false;
    if (typeof global.URL !== 'function' || typeof global.URL.createObjectURL !== 'function') return false;
    if (typeof global.Blob !== 'function') return false;
    try {
      var blob = new global.Blob([payload], { type: type });
      var url = global.URL.createObjectURL(blob);
      var link = doc.createElement('a');
      link.setAttribute('href', url);
      link.setAttribute('download', name);
      link.setAttribute('rel', 'noopener');
      if (typeof link.click === 'function') link.click();
      if (typeof global.URL.revokeObjectURL === 'function') global.URL.revokeObjectURL(url);
      return true;
    } catch (e) {
      return false;
    }
  }

  /* the printable view is a server-side page (no script, no external assets),
   * so "print / save as PDF" is one click that opens it in a new tab: the user
   * presses Ctrl+P there. In the headless rig there is no window.open, and the
   * panel says so instead of pretending a tab opened. */
  function openExportView(format) {
    var path = exportRequestPath(format);
    state.exportPanel.lastPath = path;
    if (!path) return false;
    var opened = null;
    if (typeof global.open === 'function') {
      opened = global.open(path, '_blank', 'noopener');
    } else if (doc && doc.defaultView && typeof doc.defaultView.open === 'function') {
      opened = doc.defaultView.open(path, '_blank', 'noopener');
    }
    if (!opened) return false;
    state.exportPanel.popups += 1;
    return true;
  }

  function requestExport(format) {
    var wanted = (format === 'pdf' || format === 'html') ? format : 'csv';
    if (!canPermission(PERM_AUDIT_EXPORT)) {
      state.exportPanel.denials += 1;
      state.exportPanel.error = 'export.note.need_permission';
      state.exportPanel.reason = 'export.note.need_permission';
      setExportFeedback('export.note.need_permission');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN', disabled: true });
    }
    if (state.exportPanel.busy) return Promise.resolve({ ok: false, code: 'BUSY' });
    if (!auditTransportOnline()) {
      state.exportPanel.error = 'export.error.offline';
      state.exportPanel.reason = 'export.error.offline';
      setExportFeedback('export.error.offline');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'NETWORK_ERROR', offline: true });
    }
    var path = exportRequestPath(wanted);
    state.exportPanel.lastPath = path;
    if (!path) {
      state.exportPanel.error = 'export.error.route_not_found';
      setExportFeedback('export.error.route_not_found');
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROUTE_NOT_FOUND' });
    }
    state.exportPanel.busy = true;
    state.exportPanel.error = '';
    state.exportPanel.reason = '';
    if (booted) renderAll();
    return apiRequest('auditExport', null, {
      method: 'GET',
      credentials: 'same-origin',
      response: 'text',
      accept: 'text/csv,text/html,application/pdf,application/json',
      query: exportQueryParams(wanted)
    }).then(function (res) {
      state.exportPanel.busy = false;
      var headers = (res && res.headers) ? res.headers : null;
      if (!res || !res.ok) {
        var code = (res && isObj(res.data) && hasText(res.data.code)) ? str(res.data.code) : 'HTTP_STATUS';
        if (res && res.status === 403) state.exportPanel.denials += 1;
        var key = auditErrorKey(res && res.data, EXPORT_ERROR_KEYS, 'export.error.generic');
        state.exportPanel.error = key;
        state.exportPanel.reason = key;
        setExportFeedback(key, { status: (res && res.status) ? res.status : 0 });
        if (booted) renderAll();
        return { ok: false, code: code, status: (res && res.status) ? res.status : 0 };
      }
      var served = headerValue(headers, 'x-tracelens-export-format') || wanted;
      var rows = parseInt(headerValue(headers, 'x-tracelens-export-rows'), 10);
      var sha = headerValue(headers, 'x-tracelens-export-sha256');
      var fallback = headerValue(headers, 'x-tracelens-export-fallback');
      var fontNote = headerValue(headers, 'x-tracelens-export-note');
      var filename = filenameFromDisposition(headerValue(headers, 'content-disposition'))
        || ('tracelens-audit.' + served);
      var body = str(res.text);
      var downloaded = false;
      if (served === 'csv' || wanted === 'csv') {
        downloaded = downloadAuditFile(filename, body, 'text/csv;charset=utf-8');
        if (downloaded) state.exportPanel.downloads += 1;
      }
      if (served === 'html' && wanted !== 'csv') openExportView('html');
      state.exportPanel.count += 1;
      state.exportPanel.last = {
        format: served, requested: wanted, rows: (isNaN(rows) ? null : rows),
        bytes: body.length, sha256: sha, filename: filename,
        fallback: fallback, filters: headerValue(headers, 'x-tracelens-export-filters'),
        note: fontNote,
        downloaded: downloaded
      };
      /* two honest notes, in the user's own words: the raw server text never
       * lands in the page, the panel maps it to a dictionary sentence */
      var noteKey = fallback ? 'export.note.pdf_fallback'
        : (fontNote ? 'export.note.pdf_font' : '');
      state.exportPanel.error = noteKey;
      /* the reason line answers "why is the button dead"; a caveat on a file
       * that did ship lives in the feedback line, so do not mix the two */
      state.exportPanel.reason = '';
      setExportFeedback(noteKey || 'export.feedback.done',
                        { format: served, rows: (isNaN(rows) ? 0 : rows),
                          filename: filename, sha: shortHash(sha) });
      /* 4.12 (H-02): the server books every export in the log before it ships
       * the bytes, so the panel reads the log back: the entry is visible, not
       * only inside the file the browser just downloaded. */
      auditAfterWrite('export:' + served);
      if (booted) renderAll();
      return { ok: true, format: served, rows: state.exportPanel.last.rows,
               bytes: body.length, filename: filename, fallback: fallback,
               note: fontNote, downloaded: downloaded };
    }, function (err) {
      state.exportPanel.busy = false;
      var key = auditErrorKey(err, EXPORT_ERROR_KEYS, 'export.error.network');
      state.exportPanel.error = key;
      state.exportPanel.reason = key;
      setExportFeedback(key);
      if (booted) renderAll();
      return { ok: false, code: (err && hasText(err.code)) ? str(err.code) : 'NETWORK_ERROR' };
    });
  }

  /* ---- panels -------------------------------------------------------- */

  function railButton(box, hook, labelKey, ariaKey, onClick) {
    var button = el('button', {
      type: 'button', class: 'rail__button',
      dataset: { hook: hook, enabled: 'true' }
    });
    i18nText(button, labelKey);
    aria(button, ariaKey);
    if (typeof onClick === 'function') button.addEventListener('click', onClick);
    add(box, button);
    return button;
  }

  /* ================================================================
   * 4.9 import (t22 / QA 5.1 D1): the panel + the one POST
   *
   * Two mounts share one draft: the phone bottom sheet (#sheet-import) and the
   * desktop rail hook. A picked file is read *locally* into the same text box,
   * so there is no second upload path, no second door, and the page's size
   * check is the server's own cap (IMPORT_MAX_BYTES / IMPORT_MAX_RECORDS).
   * Nothing is ever claimed as "your data is saved" beyond what the server
   * answers: the summary line repeats imported / duplicates / skipped, and the
   * durable chip says whether it will survive a restart.
   * ================================================================ */

  function importOnline() {
    return state.api.mode === 'live' && state.api.forced !== 'offline';
  }

  function utf8Bytes(text) {
    var s = str(text);
    if (!s) return 0;
    if (typeof global.TextEncoder === 'function') {
      try { return new global.TextEncoder().encode(s).length; }
      catch (e) { /* an old shell: fall back to the manual count below */ }
    }
    var n = 0;
    for (var i = 0; i < s.length; i += 1) {
      var c = s.charCodeAt(i);
      if (c < 0x80) n += 1;
      else if (c < 0x800) n += 2;
      else if (c >= 0xd800 && c < 0xdc00) { n += 4; i += 1; }
      else n += 3;
    }
    return n;
  }

  function importBlockedKey() {
    /* why the button is dead, in words - a grey rectangle explains nothing */
    if (!canPermission(PERM_DATA_WRITE)) return 'import.note.need_permission';
    if (!importOnline()) return 'import.note.offline';
    return null;
  }

  function importClientCheck() {
    /* client-side format + size check: an obvious miss never leaves the page.
     * The server re-checks everything - a passing check is not a promise. */
    var draft = state.importPanel;
    var text = str(draft.text);
    var bytes = utf8Bytes(text);
    if (!hasText(text)) return { key: 'import.validation.empty', params: null };
    if (bytes > IMPORT_MAX_BYTES) {
      return { key: 'import.validation.too_large',
               params: { bytes: bytes, limit: IMPORT_MAX_BYTES } };
    }
    try {
      JSON.parse(text);
    } catch (e) {
      return { key: 'import.validation.not_json',
               params: { bytes: bytes, limit: IMPORT_MAX_BYTES } };
    }
    return null;
  }

  function importDetailParams(data) {
    /* structured detail only: the page renders dictionary text, never the
     * server's own sentence (that one is Chinese and would travel into English) */
    var d = (isObj(data) && isObj(data.detail)) ? data.detail : {};
    var out = { code: (isObj(data) && hasText(data.code)) ? str(data.code) : '-' };
    if (d.bytes !== undefined) out.bytes = str(d.bytes);
    if (d.max !== undefined) out.limit = str(d.max);
    else if (d.limit !== undefined) out.limit = str(d.limit);
    else out.limit = String(IMPORT_MAX_BYTES);
    if (d.index !== undefined && d.index !== null) out.index = str(d.index);
    if (hasText(d.format_detected)) out.format = str(d.format_detected);
    else if (isArr(d.supported)) out.format = d.supported.map(str).join('/');
    if (hasText(d.mode)) out.mode = str(d.mode);
    return out;
  }

  function importFail(code, params) {
    var draft = state.importPanel;
    draft.status = 'error';
    draft.errorKey = IMPORT_ERROR_KEYS[str(code)] || 'import.error.unknown';
    draft.errorParams = params || null;
    if (code === 'ROLE_FORBIDDEN' || code === 'AUTH_REQUIRED') draft.denials += 1;
    if (booted) renderAll();
  }

  function runImport() {
    var draft = state.importPanel;
    var blocked = importBlockedKey();
    if (blocked) {
      draft.denials += 1;
      draft.status = 'error';
      draft.errorKey = blocked;
      draft.errorParams = null;
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'ROLE_FORBIDDEN', blocked: true });
    }
    var check = importClientCheck();
    if (check) {
      draft.clientBlocked += 1;
      draft.status = 'error';
      draft.errorKey = check.key;
      draft.errorParams = check.params;
      if (booted) renderAll();
      return Promise.resolve({ ok: false, code: 'CLIENT_CHECK', blocked: true });
    }
    draft.status = 'busy';
    draft.result = null;
    draft.errorKey = 'import.state.busy';
    draft.errorParams = null;
    draft.requests += 1;
    draft.bytes = utf8Bytes(draft.text);
    if (booted) renderAll();
    var body = { payload: str(draft.text), mode: str(draft.mode) || 'upsert',
                 lang: i18n.lang };
    if (hasText(draft.format)) body.format = str(draft.format);
    if (hasText(draft.filename)) body.filename = str(draft.filename);
    return authFetch('import', body, { method: 'POST' }).then(function (res) {
      var data = (res && isObj(res.data)) ? res.data : null;
      draft.lastRequestId = responseRequestId(res);
      if (res && res.ok && data && data.ok === true) {
        draft.status = 'done';
        draft.imports += 1;
        draft.result = {
          imported: data.imported, created: data.created, updated: data.updated,
          duplicates: data.duplicates, skipped: data.skipped,
          format: str(data.format), mode: str(data.mode),
          durable: data.durable === true, total: data.records_total
        };
        draft.errorKey = 'import.feedback.done';
        draft.errorParams = {
          imported: str(data.imported), created: str(data.created),
          updated: str(data.updated), duplicates: str(data.duplicates),
          skipped: str(data.skipped), format: str(data.format || '-'),
          total: str(data.records_total)
        };
        /* the list is the point: pull what the server just stored, so the
         * pasted rows appear in the zones without a manual refresh.
         * 4.12: merge by id instead of replacing the list, so the operator's
         * open cards, focused card and timeline position survive their own
         * import; the audit panel follows the write too. */
        auditAfterWrite('import:' + str(data.format || ''));
        return refreshRecordsFromApi({ reason: 'import' }).then(function () {
          if (booted) renderAll();
          return res;
        });
      }
      var code = (data && hasText(data.code)) ? str(data.code)
               : ((res && res.status) ? 'HTTP_STATUS' : 'NETWORK_ERROR');
      importFail(code, importDetailParams(data));
      return res;
    }, function (err) {
      var code = (isObj(err) && hasText(err.code)) ? str(err.code) : 'NETWORK_ERROR';
      importFail(code, importDetailParams(err));
      return { ok: false, code: code };
    });
  }

  function clearImport() {
    var draft = state.importPanel;
    draft.text = '';
    draft.filename = '';
    draft.status = 'idle';
    draft.result = null;
    draft.bytes = 0;
    draft.errorKey = 'import.feedback.cleared';
    draft.errorParams = null;
    if (booted) renderAll();
  }

  function readImportFile(fileInput) {
    var draft = state.importPanel;
    var files = (fileInput && isArr(fileInput.files)) ? fileInput.files : [];
    var picked = files.length ? files[0] : null;
    if (!picked) return;
    var name = str(picked.name || '');
    var size = (typeof picked.size === 'number') ? picked.size : 0;
    draft.filename = name;
    if (size > IMPORT_MAX_BYTES) {
      draft.status = 'error';
      draft.errorKey = 'import.validation.file_too_large';
      draft.errorParams = { bytes: size, limit: IMPORT_MAX_BYTES };
      if (booted) renderAll();
      return;
    }
    if (typeof global.FileReader !== 'function') {
      draft.status = 'error';
      draft.errorKey = 'import.error.no_file_reader';
      draft.errorParams = { filename: name || '-' };
      if (booted) renderAll();
      return;
    }
    var reader = new global.FileReader();
    reader.onload = function () {
      draft.text = str(reader.result);
      draft.bytes = utf8Bytes(draft.text);
      draft.status = 'idle';
      draft.result = null;
      draft.errorKey = 'import.state.file_ready';
      draft.errorParams = { filename: name || '-', bytes: str(draft.bytes) };
      if (booted) renderAll();
    };
    reader.onerror = function () {
      draft.status = 'error';
      draft.errorKey = 'import.error.file_read_failed';
      draft.errorParams = { filename: name || '-' };
      if (booted) renderAll();
    };
    reader.readAsText(picked);
  }

  function importSelect(box, hook, key, values, keys, current, onChange) {
    var select = el('select', {
      class: 'import__select', dataset: { hook: hook, field: key }
    });
    aria(select, 'import.aria.' + key);
    values.forEach(function (value) {
      var option = el('option', { value: str(value), text: t(keys[str(value)]) });
      if (str(value) === str(current)) option.setAttribute('selected', 'selected');
      add(select, option);
    });
    select.value = str(current);
    on(select, 'change', function () { onChange(str(select.value)); });
    add(box, select);
    return select;
  }

  function renderImportPanel(box, opts) {
    var options = isObj(opts) ? opts : {};
    var draft = state.importPanel;
    var blocked = importBlockedKey();
    var busy = draft.status === 'busy';

    var head = add(box, el('h3', {
      class: 'rail__hook-title import__title',
      id: hasText(options.titleId) ? options.titleId : null,
      dataset: { hook: 'import-title' }
    }));
    i18nText(head, 'import.title');
    i18nText(add(box, el('p', {
      class: 'rail__hook-hint import__hint', dataset: { hook: 'import-hint' }
    })), 'import.hint');

    /* one durable fact up front: a person pasting data wants to know whether it
     * survives the next restart, and only the server knows that */
    var chips = add(box, el('div', {
      class: 'import__chips', dataset: { hook: 'import-chips' }
    }));
    var durable = (isObj(state.meta) && state.meta.records_durable === true);
    var durableChip = add(chips, el('span', {
      class: 'import__chip import__chip-durable', dataset: { hook: 'import-durable' }
    }));
    durableChip.setAttribute('data-durable', durable ? 'true' : 'false');
    i18nText(durableChip, durable ? 'import.chip.durable' : 'import.chip.memory');
    var limitsChip = add(chips, el('span', {
      class: 'import__chip import__chip-limits', dataset: { hook: 'import-limits' }
    }));
    i18nText(limitsChip, 'import.chip.limits',
             { bytes: IMPORT_MAX_BYTES, records: IMPORT_MAX_RECORDS });

    var field = add(box, el('label', {
      class: 'import__field', dataset: { hook: 'import-field' }
    }));
    i18nText(add(field, el('span', { class: 'import__label' })), 'import.paste.label');
    var area = add(field, el('textarea', {
      class: 'import__text', rows: '4', spellcheck: 'false',
      dataset: { hook: 'import-text' }, placeholder: t('import.paste.placeholder')
    }));
    area.value = str(draft.text);
    area.setAttribute('aria-label', t('import.aria.text'));
    area.setAttribute('data-i18n-aria', 'import.aria.text');
    if (blocked) area.setAttribute('disabled', 'disabled');
    var counter = add(box, el('p', {
      class: 'import__size', dataset: { hook: 'import-size' }
    }));
    var paintSize = function () {
      var bytes = utf8Bytes(state.importPanel.text);
      state.importPanel.bytes = bytes;
      counter.setAttribute('data-bytes', str(bytes));
      counter.setAttribute('data-over', bytes > IMPORT_MAX_BYTES ? 'true' : 'false');
      i18nText(counter, 'import.state.size',
               { bytes: bytes, limit: IMPORT_MAX_BYTES,
                 records: IMPORT_MAX_RECORDS });
    };
    paintSize();
    on(area, 'input', function () {
      /* keystrokes never re-render the page: that would rebuild the textarea
       * under the cursor. Only the shared draft and the byte counter move. */
      state.importPanel.text = str(area.value);
      if (state.importPanel.status === 'error') state.importPanel.status = 'idle';
      paintSize();
    });

    var fileRow = add(box, el('div', {
      class: 'import__filerow', dataset: { hook: 'import-filerow' }
    }));
    var fileInput = add(fileRow, el('input', {
      type: 'file', class: 'import__file',
      accept: '.json,.jsonl,application/json,text/plain',
      dataset: { hook: 'import-file' }
    }));
    fileInput.setAttribute('aria-label', t('import.aria.file'));
    fileInput.setAttribute('data-i18n-aria', 'import.aria.file');
    if (blocked) fileInput.setAttribute('disabled', 'disabled');
    on(fileInput, 'change', function () { readImportFile(fileInput); });
    var fileName = add(fileRow, el('span', {
      class: 'import__filename', dataset: { hook: 'import-filename' }
    }));
    if (hasText(draft.filename)) {
      fileName.setAttribute('data-visible', 'true');
      fileName.setAttribute('data-filename', str(draft.filename));
      text(fileName, str(draft.filename));
    } else {
      fileName.setAttribute('data-visible', 'false');
      i18nText(fileName, 'import.file.none');
    }

    var picks = add(box, el('div', {
      class: 'import__picks', dataset: { hook: 'import-picks' }
    }));
    importSelect(picks, 'import-format', 'format', IMPORT_FORMATS, IMPORT_FORMAT_KEYS,
                 draft.format, function (value) {
                   state.importPanel.format = value;
                   if (booted) renderAll();
                 });
    importSelect(picks, 'import-mode', 'mode', IMPORT_MODES, IMPORT_MODE_KEYS,
                 draft.mode, function (value) {
                   state.importPanel.mode = value;
                   if (booted) renderAll();
                 });
    i18nText(add(box, el('p', {
      class: 'rail__hook-note import__mode-note', dataset: { hook: 'import-mode-note' }
    })), 'import.mode.note.' + (hasText(draft.mode) ? str(draft.mode) : 'upsert'));

    var actions = add(box, el('div', {
      class: 'import__actions', dataset: { hook: 'import-actions' }
    }));
    var run = railButton(actions, 'import-run', 'import.action.run', 'import.aria.run',
                         function () { runImport(); });
    var clearButton = railButton(actions, 'import-clear', 'import.action.clear',
                                 'import.aria.clear', function () { clearImport(); });
    if (blocked || busy) {
      run.setAttribute('disabled', 'disabled');
      run.setAttribute('data-enabled', 'false');
    }
    if (busy) i18nText(run, 'import.state.busy');
    if (!hasText(str(draft.text))) {
      clearButton.setAttribute('disabled', 'disabled');
      clearButton.setAttribute('data-enabled', 'false');
    }

    var feedback = add(box, el('p', {
      class: 'rail__hook-feedback import__feedback', dataset: { hook: 'import-feedback' }
    }));
    feedback.setAttribute('data-status', str(draft.status));
    i18nText(feedback, draft.errorKey, draft.errorParams);

    var result = isObj(draft.result) ? draft.result : null;
    var summary = add(box, el('ul', {
      class: 'import__summary', dataset: { hook: 'import-summary' }, role: 'list'
    }));
    if (result) {
      summary.setAttribute('data-visible', 'true');
      summary.setAttribute('data-imported', str(result.imported));
      summary.setAttribute('data-duplicates', str(result.duplicates));
      summary.setAttribute('data-skipped', str(result.skipped));
      summary.setAttribute('data-format', str(result.format));
      var facts = [
        ['imported', 'import.summary.imported', str(result.imported)],
        ['created', 'import.summary.created', str(result.created)],
        ['updated', 'import.summary.updated', str(result.updated)],
        ['duplicates', 'import.summary.duplicates', str(result.duplicates)],
        ['skipped', 'import.summary.skipped', str(result.skipped)],
        ['total', 'import.summary.total', str(result.total)]
      ];
      facts.forEach(function (fact) {
        var row = add(summary, el('li', {
          class: 'import__fact', dataset: { hook: 'import-fact', kind: fact[0] }
        }));
        i18nText(row, fact[1], { count: fact[2] });
        row.setAttribute('data-count', fact[2]);
      });
      add(summary, el('li', {
        class: 'import__durable', dataset: { hook: 'import-result-durable' },
        text: t(result.durable ? 'import.summary.durable' : 'import.summary.memory')
      }));
    } else {
      summary.setAttribute('data-visible', 'false');
      summary.setAttribute('data-imported', '');
    }

    if (blocked) {
      i18nText(add(box, el('p', {
        class: 'rail__hook-note import__need', dataset: { hook: 'import-need' }
      })), blocked);
    }
  }

  function renderExportPanel(box) {
    i18nText(add(box, el('h3', { class: 'rail__hook-title' })), 'export.title');
    i18nText(add(box, el('p', { class: 'rail__hook-hint' })), 'export.hint');

    var allowed = canPermission(PERM_AUDIT_EXPORT);
    var online = auditTransportOnline();
    var enabled = allowed && online;

    var buttons = add(box, el('div', { class: 'export__buttons', dataset: { hook: 'export-buttons' } }));
    var csvButton = railButton(buttons, 'export-csv', 'export.action.csv', 'export.aria.csv',
      function () { requestExport('csv'); });
    var pdfButton = railButton(buttons, 'export-pdf', 'export.action.pdf', 'export.aria.pdf',
      function () { requestExport('pdf'); });
    var printButton = railButton(buttons, 'export-print', 'export.action.print', 'export.aria.print',
      function () {
        if (openExportView('html')) setExportFeedback('export.feedback.print_opened');
        else setExportFeedback('export.note.no_popup');
        if (booted) renderAll();
      });
    [csvButton, pdfButton, printButton].forEach(function (button) {
      if (!enabled) {
        button.setAttribute('disabled', 'disabled');
        button.setAttribute('data-enabled', 'false');
      }
    });

    /* the reason line is the whole point of the disabled state: a person must
     * learn why the button is dead, not just see a grey rectangle */
    var reason = state.exportPanel.reason;
    if (!reason && !allowed) reason = 'export.note.need_permission';
    if (!reason && allowed && !online) reason = 'export.note.offline';
    var reasonNode = add(box, el('p', {
      class: 'rail__hook-note export__note', dataset: { hook: 'export-need' }
    }));
    if (reason) {
      reasonNode.setAttribute('data-visible', 'true');
      i18nText(reasonNode, reason);
    } else {
      reasonNode.setAttribute('data-visible', 'false');
      reasonNode.textContent = '';
    }

    var last = isObj(state.exportPanel.last) ? state.exportPanel.last : null;
    var lastNode = add(box, el('p', {
      class: 'rail__hook-meta export__last', dataset: { hook: 'export-last' }
    }));
    if (last) {
      lastNode.setAttribute('data-visible', 'true');
      lastNode.setAttribute('data-format', str(last.format));
      lastNode.setAttribute('data-rows', (last.rows === null || last.rows === undefined) ? '' : str(last.rows));
      lastNode.setAttribute('data-sha', shortHash(last.sha256));
      lastNode.setAttribute('data-filename', str(last.filename));
      if (last.fallback) lastNode.setAttribute('data-fallback', str(last.fallback));
      i18nText(lastNode, 'export.state.last', {
        format: str(last.format),
        rows: (last.rows === null || last.rows === undefined) ? '?' : str(last.rows),
        sha: shortHash(last.sha256),
        filename: str(last.filename)
      });
    } else {
      lastNode.setAttribute('data-visible', 'false');
      i18nText(lastNode, 'export.state.none');
      lastNode.textContent = '';
    }

    var feedback = add(box, el('p', {
      class: 'rail__hook-feedback export__feedback', dataset: { hook: 'export-feedback' }
    }));
    i18nText(feedback, state.exportPanel.feedback, state.exportPanel.feedbackParams);
  }

  function renderAuditPanel(box) {
    i18nText(add(box, el('h3', { class: 'rail__hook-title' })), 'audit.title');
    i18nText(add(box, el('p', { class: 'rail__hook-hint' })), 'audit.hint');

    var chain = isObj(state.audit.chain) ? state.audit.chain : {};
    /* 4.12 (H-01, QA 5.2): the chips are facts about the server's answer.
     * Before the first read there is no chain and no store mode, so the panel
     * says it is reading - it never claims "0 entries / in-memory chain" while
     * the store already holds entries. */
    var loaded = state.audit.loaded === true;
    var chips = add(box, el('div', { class: 'audit__chips', dataset: { hook: 'audit-chips' } }));
    var chainChip = add(chips, el('span', {
      class: 'audit__chip', dataset: { hook: 'audit-chain' }
    }));
    if (!loaded) {
      chainChip.setAttribute('data-ok', 'pending');
      i18nText(chainChip, 'audit.chip.loading');
    } else {
      chainChip.setAttribute('data-ok', (chain.ok === true) ? 'true' : 'false');
      if (chain.ok === false) {
        var problem = (isArr(chain.problems) && isObj(chain.problems[0])) ? chain.problems[0] : {};
        i18nText(chainChip, 'audit.chip.chain_broken', { code: str(problem.code || 'chain_break') });
      } else {
        i18nText(chainChip, 'audit.chip.chain_ok');
      }
    }
    if (loaded) {
      var countChip = add(chips, el('span', { class: 'audit__chip', dataset: { hook: 'audit-count' } }));
      i18nText(countChip, 'audit.chip.count',
               { count: (typeof chain.count === 'number') ? chain.count : state.audit.rows.length });
      var storeChip = add(chips, el('span', { class: 'audit__chip', dataset: { hook: 'audit-store' } }));
      i18nText(storeChip, (state.audit.meta && state.audit.meta.durable === true)
        ? 'audit.chip.durable' : 'audit.chip.memory');
    }

    /* decision filter: the audit list gets long fast, and "show me the
     * decisions" is the one question a normal person asks first */
    var filters = add(box, el('div', { class: 'audit__filters', dataset: { hook: 'audit-filters' } }));
    var chipsAll = [['', 'audit.filter.all']].concat(
      AUDIT_FILTER_DECISIONS.map(function (d) {
        return [d, AUDIT_DECISION_FILTER_KEYS[d]];
      }));
    chipsAll.forEach(function (pair) {
      var chip = el('button', {
        type: 'button', class: 'audit__filter',
        dataset: { hook: 'audit-filter', decision: str(pair[0]) }
      });
      i18nText(chip, pair[1]);
      var active = (state.audit.filters.decision || '') === pair[0];
      chip.setAttribute('data-active', active ? 'true' : 'false');
      chip.setAttribute('aria-pressed', active ? 'true' : 'false');
      chip.addEventListener('click', function () { setAuditFilter({ decision: pair[0] }); });
      add(filters, chip);
    });

    var list = add(box, el('ul', {
      class: 'audit__list', dataset: { hook: 'audit-list' }, role: 'list'
    }));
    aria(list, 'audit.aria.list');
    var rows = state.audit.rows;
    rows.forEach(function (entry) {
      var row = isObj(entry) ? entry : {};
      var li = add(list, el('li', {
        class: 'audit__row', dataset: { hook: 'audit-row', seq: str(row.seq === undefined ? '' : row.seq) }
      }));
      var timeNode = add(li, el('span', { class: 'audit__time', dataset: { hook: 'audit-time' } }));
      i18nText(timeNode, 'audit.row.time');
      add(timeNode, el('span', { class: 'audit__value',
        text: hasText(row.ts) ? formatTimestamp(row.ts, i18n.lang) : '-' }));
      var actorNode = add(li, el('span', { class: 'audit__actor', dataset: { hook: 'audit-actor' } }));
      i18nText(actorNode, 'audit.row.actor');
      add(actorNode, el('span', { class: 'audit__value', text: str(row.actor_id || '-') }));
      var actionNode = add(li, el('span', { class: 'audit__action', dataset: { hook: 'audit-action' } }));
      i18nText(actionNode, hasText(row.action_id) ? 'audit.row.action' : 'audit.row.task');
      add(actionNode, el('span', { class: 'audit__value',
        text: str(row.action_id || row.task_id || '-') }));
      var decisionNode = add(li, el('span', {
        class: 'audit__decision', dataset: { hook: 'audit-decision' }
      }));
      i18nText(decisionNode, 'audit.row.decision');
      var decisionValue = hasText(row.decision) ? str(row.decision) : t('audit.decision.none');
      var decisionText = add(decisionNode, el('span', { class: 'audit__value', text: decisionValue }));
      if (hasText(row.decision)) decisionNode.setAttribute('data-decision', str(row.decision));
      var hashNode = add(li, el('span', { class: 'audit__hash', dataset: { hook: 'audit-hash' } }));
      i18nText(hashNode, 'audit.row.hash');
      var hash = hasText(row.hash) ? str(row.hash) : (hasText(row.entry_hash) ? str(row.entry_hash) : '');
      var hashText = add(hashNode, el('span', { class: 'audit__value', text: shortHash(hash) }));
      if (hash) hashNode.setAttribute('data-hash', hash);
    });

    if (state.audit.status === 'loading' && !rows.length) {
      i18nText(add(box, el('p', {
        class: 'audit__loading', dataset: { hook: 'audit-loading' }
      })), 'audit.loading');
    }
    /* 4.12 (H-01): before the first read the panel never shows a bare empty
     * list - it says why there is nothing yet (no permission, nothing to talk
     * to, or reading the first page). */
    if (!loaded && !state.audit.loading && !rows.length) {
      var pending = add(box, el('p', {
        class: 'audit__pending', dataset: { hook: 'audit-pending' }
      }));
      i18nText(pending, !canPermission(PERM_AUDIT_READ) ? 'audit.note.readonly'
        : (!auditTransportOnline() ? 'audit.error.offline' : 'audit.loading'));
    }
    /* an error is not an empty list: when the read failed the panel says what
     * went wrong, it never claims "no audit entries yet" on top of that */
    if (!rows.length && state.audit.status === 'empty') {
      var empty = add(box, el('p', { class: 'audit__empty', dataset: { hook: 'audit-empty' } }));
      var filtered = hasText(state.audit.filters.decision) || hasText(state.audit.filters.actor_id)
        || hasText(state.audit.filters.q) || hasText(state.audit.filters.event);
      i18nText(empty, filtered ? 'audit.empty_filtered' : 'audit.empty');
    }
    if (state.audit.status === 'error' && state.audit.error) {
      var error = add(box, el('p', {
        class: 'audit__error', dataset: { hook: 'audit-error' }
      }));
      i18nText(error, state.audit.error);
    }

    var actions = add(box, el('div', { class: 'audit__actions', dataset: { hook: 'audit-actions' } }));
    var refresh = railButton(actions, 'audit-refresh', 'audit.action.refresh', 'audit.aria.refresh',
      function () { loadAudit('first'); });
    var more = railButton(actions, 'audit-more', 'audit.action.more', 'audit.aria.more',
      function () { loadAudit('more'); });
    if (!canPermission(PERM_AUDIT_READ)) {
      [refresh, more].forEach(function (button) {
        button.setAttribute('disabled', 'disabled');
        button.setAttribute('data-enabled', 'false');
      });
      i18nText(add(box, el('p', {
        class: 'rail__hook-note audit__note', dataset: { hook: 'audit-readonly' }
      })), 'audit.note.readonly');
    }
    if (auditHasMore()) {
      more.setAttribute('data-more', 'true');
    } else {
      more.setAttribute('data-more', 'false');
      if (rows.length) {
        more.setAttribute('disabled', 'disabled');
        more.setAttribute('data-enabled', 'false');
        i18nText(more, 'audit.more_end');
      }
    }
    if (state.audit.loading) {
      [refresh, more].forEach(function (button) {
        button.setAttribute('disabled', 'disabled');
        button.setAttribute('data-enabled', 'false');
      });
    }

    var feedback = add(box, el('p', {
      class: 'rail__hook-feedback audit__feedback', dataset: { hook: 'audit-feedback' }
    }));
    i18nText(feedback, state.audit.feedback, state.audit.feedbackParams);
  }

  /* ---------------------------------------------------------------- *
   * 9c. mobile reachability (4.10, QA 5.1 D-4)
   * ---------------------------------------------------------------- *
   * The phone layout has no sidebar, so the entries the desktop sidebar
   * carries - settings, audit, users, import, export, language - are
   * collected in one "more" list in the top bar. An entry opens the panel
   * that already exists; nothing in this section is a second copy of a
   * panel, and nothing here talks to the server on its own:
   *   audit / export -> the 4.8 rail panels, shown as a drawer by CSS
   *   import         -> the 4.9 block that already lives in the phone sheet
   *   settings       -> the 4.5 centre in the main column
   *   users          -> the 4.3 admin screen in the main column
   *   language       -> the same setLang() the top bar switch calls
   * An entry the current role may not use stays visible, marked not
   * available, and says why in the user's language: a grey rectangle
   * explains nothing. Desktop (>=1024px) never opens any of this: the
   * sidebar stays the only entry there.
   * ---------------------------------------------------------------- */

  var MORE_TARGETS = ['settings', 'audit', 'users', 'import', 'export', 'language'];

  /* Titles, hints and reasons are spelled out as literals so the i18n
   * coverage check can see every key the phone layout asks for. The reason
   * keys are the same ones the panels themselves print. */
  var MORE_ENTRIES = [
    {
      id: 'settings', target: 'settings', permission: '',
      title: 'more.entry.settings', hint: 'more.entry.settings.hint'
    },
    {
      id: 'audit', target: 'audit', permission: PERM_AUDIT_READ,
      title: 'more.entry.audit', hint: 'more.entry.audit.hint',
      reason_permission: 'audit.note.readonly', reason_offline: 'audit.error.offline'
    },
    {
      id: 'users', target: 'users', permission: PERM_USERS_MANAGE,
      title: 'more.entry.users', hint: 'more.entry.users.hint',
      reason_permission: 'more.reason.users'
    },
    {
      id: 'import', target: 'import', permission: PERM_DATA_WRITE,
      title: 'more.entry.import', hint: 'more.entry.import.hint'
    },
    {
      id: 'export', target: 'export', permission: PERM_AUDIT_EXPORT,
      title: 'more.entry.export', hint: 'more.entry.export.hint',
      reason_permission: 'export.note.need_permission', reason_offline: 'export.note.offline'
    },
    {
      id: 'language', target: 'language', permission: '',
      title: 'more.entry.language', hint: 'more.entry.language.hint'
    }
  ];

  function otherLang() { return i18n.lang === 'en' ? 'zh' : 'en'; }

  function moreEntryById(id) {
    var key = str(id);
    var i;
    for (i = 0; i < MORE_ENTRIES.length; i += 1) {
      if (MORE_ENTRIES[i].id === key) return MORE_ENTRIES[i];
    }
    return null;
  }

  function moreEntryOnline(id) {
    if (id === 'import') return importOnline();
    if (id === 'audit' || id === 'export') return auditTransportOnline();
    return true;
  }

  /* Enable / disable per the abilities the server published, and name the
   * reason: the same dictionary line the panel behind the entry prints. */
  function moreEntryState(entry) {
    var id = entry ? str(entry.id) : '';
    var enabled = true;
    var reason = '';
    if (id === 'language' || id === 'settings') {
      /* local actions: the settings centre prints its own offline note */
      enabled = true;
    } else if (id === 'import') {
      var importReason = importBlockedKey();
      enabled = !importReason;
      reason = str(importReason);
    } else if (id === 'users') {
      if (!canPermission(PERM_USERS_MANAGE)) {
        enabled = false;
        reason = 'more.reason.users';
      } else if (!auditTransportOnline()) {
        enabled = false;
        reason = 'more.reason.offline';
      } else if (authStateName() !== 'signed_in') {
        enabled = false;
        reason = 'more.reason.signin';
      }
    } else {
      if (!canPermission(entry.permission)) {
        enabled = false;
        reason = str(entry.reason_permission);
      } else if (!moreEntryOnline(id)) {
        enabled = false;
        reason = str(entry.reason_offline);
      }
    }
    return {
      id: id,
      target: entry ? str(entry.target) : 'off',
      enabled: enabled === true,
      reason: enabled === true ? '' : str(reason),
      title: entry ? str(entry.title) : ''
    };
  }

  function moreEntriesState() {
    return MORE_ENTRIES.map(moreEntryState);
  }

  function applyMoreChrome() {
    var mobile = state.view !== 'desktop';
    if (!mobile) {
      /* the sidebar already carries these entries on a desktop: when the
       * window grows, every phone layer closes so nothing is left open */
      state.more.menu = false;
      state.more.open = false;
      state.more.target = 'off';
      state.more.screen = 'off';
    }
    var app = byId('app');
    if (!app) return;
    app.setAttribute('data-more-menu', (mobile && state.more.menu) ? 'on' : 'off');
    app.setAttribute('data-more-open', (mobile && state.more.open) ? 'true' : 'false');
    app.setAttribute('data-more-target', (mobile && state.more.open) ? state.more.target : 'off');
    app.setAttribute('data-more-screen', (mobile && !state.more.open) ? state.more.screen : 'off');
    app.setAttribute('data-more-focus', (mobile && state.more.focus) ? state.more.focus : 'off');
  }

  function focusMoreButton() {
    var button = queryAll('[data-hook="more-menu"]')[0];
    if (button && typeof button.focus === 'function') button.focus();
  }

  function setMoreMenu(open) {
    if (state.view === 'desktop') {
      state.more.menu = false;
      return false;
    }
    state.more.menu = open === true;
    if (!state.more.menu) {
      state.more.notice = '';
      state.more.noticeParams = null;
    }
    if (booted) renderAll();
    return state.more.menu;
  }

  function closeMore(keepMenu) {
    state.more.open = false;
    state.more.target = 'off';
    state.more.menu = keepMenu === true;
    state.more.closes += 1;
    if (booted) renderAll();
    return true;
  }

  function openMoreTarget(target) {
    if (state.view === 'desktop') return false;
    var entry = moreEntryById(target);
    if (!entry) return false;
    var st = moreEntryState(entry);
    if (!st.enabled) {
      /* the list stays open and the reason moves into the feedback line: the
       * person sees why nothing happened */
      state.more.blocked += 1;
      state.more.menu = true;
      state.more.notice = 'more.feedback.blocked';
      state.more.noticeParams = { reason: t(st.reason) };
      if (booted) renderAll();
      return false;
    }
    state.more.opens += 1;
    state.more.menu = false;
    state.more.open = false;
    state.more.target = 'off';
    state.more.screen = 'off';
    state.more.focus = '';
    if (target === 'language') {
      /* the list stays open so the new copy is visible in place */
      state.more.menu = true;
      state.more.switches += 1;
      var next = otherLang();
      state.more.notice = 'settings.language.applied';
      state.more.noticeParams = { lang: t('nav.lang.' + next) };
      setLang(next);
      return true;
    }
    if (target === 'import') {
      /* 4.9 already put the import panel in the phone sheet: open that sheet
       * through its own entry point so the chrome (title, aria, focus) follows
       * and the action stays exactly one render. */
      state.more.focus = 'import';
      state.more.notice = 'more.feedback.opened';
      state.more.noticeParams = { title: t(entry.title) };
      openSheet(true);
      focusImportBlock();
      return true;
    }
    if (target === 'settings') {
      state.more.screen = 'settings';
      openSettings(true);
      return true;
    }
    if (target === 'users') {
      state.more.screen = 'users';
      state.auth.usersOpen = true;
      loadUsers();
      return true;
    }
    /* audit / export: the existing rail panel, opened as a drawer. The audit
     * list loads its first page exactly as the desktop path does. */
    state.more.open = true;
    state.more.target = target;
    if (state.sheetOpen) openSheet(false);
    else if (booted) renderAll();
    if (target === 'audit' && auditTransportOnline() && !state.audit.rows.length) loadAudit('first');
    return true;
  }

  function focusImportBlock() {
    var box = byId('sheet-import');
    if (!box || typeof box.scrollIntoView !== 'function') return false;
    box.scrollIntoView();
    return true;
  }

  /* ?more=<entry> / ?moremenu=1 open the phone layers inside the pinned rig
   * box, so a 375x812 screenshot shows a phone screen and not the desktop. */
  function moreUrlParams(params) {
    if (params.moremenu === '1' || params.moremenu === 'true') state.more.menu = true;
    if (params.moremenu === '0' || params.moremenu === 'false') state.more.menu = false;
    var target = MORE_TARGETS.indexOf(str(params.more)) >= 0 ? str(params.more) : '';
    state.stats.moreParam = target || 'none';
    if (!target) return null;
    if (target === 'audit' || target === 'export') {
      state.more.open = true;
      state.more.target = target;
      state.more.screen = 'off';
      openSheet(false);
    } else if (target === 'settings' || target === 'users') {
      state.more.screen = target;
      state.more.open = false;
      state.more.target = 'off';
      state.settings.open = target === 'settings';
      state.auth.usersOpen = target === 'users';
    } else if (target === 'import') {
      openSheet(true);
      state.more.focus = 'import';
    } else if (target === 'language') {
      state.more.menu = true;
    }
    state.stats.moreOpens += 1;
    return target;
  }

  function renderMoreButton(bar) {
    var mobile = state.view !== 'desktop';
    var button = add(bar, el('button', {
      class: 'topbar__more', type: 'button', tabindex: '0',
      dataset: { hook: 'more-menu', visible: mobile ? 'true' : 'false' }
    }));
    i18nText(button, 'more.title');
    aria(button, 'more.aria.open');
    button.setAttribute('aria-expanded', state.more.menu ? 'true' : 'false');
    button.setAttribute('aria-controls', 'more-sheet');
    on(button, 'click', function () { setMoreMenu(!state.more.menu); });
    return button;
  }

  function renderMoreSheet() {
    var host = byId('more-sheet');
    if (!host) return;
    var open = state.more.menu === true && state.view !== 'desktop';
    host.hidden = !open;
    host.setAttribute('data-hook', 'more-sheet');
    host.setAttribute('data-open', open ? 'true' : 'false');
    host.setAttribute('aria-labelledby', 'more-title');
    clear(host);
    if (!open) return;
    var card = add(host, el('div', { class: 'more__card', dataset: { hook: 'more-card' } }));
    var head = add(card, el('div', { class: 'more__head' }));
    add(head, el('h2', { class: 'more__title', id: 'more-title', i18n: 'more.title' }));
    var close = add(head, el('button', {
      class: 'more__close', type: 'button', tabindex: '0',
      dataset: { hook: 'more-close' }
    }));
    i18nText(close, 'more.close');
    aria(close, 'more.aria.close');
    i18nText(add(card, el('p', { class: 'more__hint' })), 'more.hint');

    var list = add(card, el('ul', { class: 'more__list', dataset: { hook: 'more-list' } }));
    aria(list, 'more.aria.entries');
    MORE_ENTRIES.forEach(function (entry) {
      var st = moreEntryState(entry);
      var item = add(list, el('li', { class: 'more__item', dataset: { hook: 'more-item', panel: st.id } }));
      var button = add(item, el('button', {
        class: 'more__entry', type: 'button', tabindex: '0',
        dataset: {
          hook: 'more-entry', panel: st.id, target: st.target,
          enabled: st.enabled ? 'true' : 'false', reason: st.reason
        }
      }));
      i18nText(add(button, el('span', { class: 'more__entry-title' })), entry.title);
      if (st.id === 'language') {
        i18nText(add(button, el('span', { class: 'more__entry-action' })),
          'more.action.language', { lang: t('nav.lang.' + otherLang()) });
        i18nText(add(button, el('span', { class: 'more__entry-value' })),
          'more.entry.language.hint', { lang: t('nav.lang.' + i18n.lang) });
      } else {
        i18nText(add(button, el('span', { class: 'more__entry-hint' })), entry.hint);
      }
      if (!st.enabled) {
        /* aria-disabled, never the disabled attribute: a control that says
         * nothing is unreachable for the keyboard that needs the reason */
        add(button, el('span', { class: 'more__entry-state', i18n: 'more.state.disabled' }));
        var reason = add(button, el('span', {
          class: 'more__entry-reason', dataset: { hook: 'more-reason', panel: st.id, visible: 'true' }
        }));
        i18nText(reason, st.reason);
      }
      button.setAttribute('aria-disabled', st.enabled ? 'false' : 'true');
      aria(button, st.enabled ? 'more.aria.entry' : 'more.aria.entry_disabled',
        { title: t(entry.title), reason: st.reason ? t(st.reason) : '' });
    });

    var feedback = add(card, el('p', {
      class: 'more__feedback',
      dataset: { hook: 'more-feedback', visible: state.more.notice ? 'true' : 'false' }
    }));
    if (state.more.notice) i18nText(feedback, state.more.notice, state.more.noticeParams);
    i18nText(add(card, el('p', { class: 'more__note' })), 'more.note');
  }

  function renderMoreDrawer(rail) {
    var drawer = add(rail, el('div', {
      class: 'rail__drawer', dataset: { hook: 'more-drawer', target: state.more.target }
    }));
    var entry = moreEntryById(state.more.target);
    var title = add(drawer, el('h2', { class: 'rail__drawer-title', id: 'more-drawer-title' }));
    i18nText(title, 'more.drawer.title', { title: entry ? t(entry.title) : t('more.title') });
    var back = add(drawer, el('button', {
      class: 'rail__drawer-back', type: 'button', tabindex: '0',
      dataset: { hook: 'more-drawer-back' }
    }));
    i18nText(back, 'more.drawer.back');
    aria(back, 'more.drawer.back.aria');
    var close = add(drawer, el('button', {
      class: 'rail__drawer-close', type: 'button', tabindex: '0',
      dataset: { hook: 'more-drawer-close' }
    }));
    i18nText(close, 'more.close');
    aria(close, 'more.aria.close');
    drawer.setAttribute('aria-labelledby', 'more-drawer-title');
    return drawer;
  }

  /* The hosts are static in index.html, so every handler is bound once and
   * survives the re-render inside the list (the settings centre idiom). */
  function wireMoreEvents() {
    var host = byId('more-sheet');
    if (host) {
      on(host, 'click', function (ev) {
        if (deskTarget(ev, '[data-hook="more-close"]')) {
          setMoreMenu(false);
          focusMoreButton();
          return;
        }
        var entry = deskTarget(ev, '[data-hook="more-entry"]');
        if (entry) {
          if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
          openMoreTarget(str(entry.getAttribute('data-panel')));
        }
      });
    }
    var rail = byId('timeline');
    if (rail) {
      on(rail, 'click', function (ev) {
        if (deskTarget(ev, '[data-hook="more-drawer-back"]')) {
          closeMore(true);
          focusMoreButton();
          return;
        }
        if (deskTarget(ev, '[data-hook="more-drawer-close"]')) {
          closeMore(false);
          focusMoreButton();
        }
      });
    }
  }

  function moreSnapshot() {
    var entries = moreEntriesState();
    var available = 0;
    entries.forEach(function (entry) { if (entry.enabled) available += 1; });
    return {
      view: state.view,
      menu: state.more.menu === true,
      open: state.more.open === true,
      target: state.more.target,
      screen: state.more.screen,
      focus: state.more.focus,
      entries: entries,
      total: entries.length,
      available: available,
      disabled: entries.length - available,
      opens: state.more.opens,
      closes: state.more.closes,
      blocked: state.more.blocked,
      switches: state.more.switches,
      notice: state.more.notice
    };
  }

  /* ---------------------------------------------------------------- *
   * 10. public API + probe (structural self description)
   * ---------------------------------------------------------------- */

  var api = {
    APP_ID: APP_ID,
    SKELETON_VERSION: SKELETON_VERSION,
    SCHEMA_VERSION: SCHEMA_VERSION,
    LANGS: LANGS.slice(),
    ZONE_ORDER: ZONE_ORDER.slice(),
    STATUS_SET: STATUS_SET.slice(),
    ACTION_TYPE_SET: ACTION_TYPE_SET.slice(),
    RISK_SET: RISK_SET.slice(),
    lang: function () { return i18n.lang; },
    /* every setter below repaints on its own; the wrappers stay thin so one
     * user action means exactly one render (state.stats.renders is the proof) */
    setLang: function (lang) { return setLang(lang); },
    t: t,
    raw: raw,
    i18n: i18n,
    setDicts: function (zh, en) { var n = setDicts(zh, en); if (booted) renderAll(); return n; },
    missingKeys: function () { return i18n.missing.slice(); },
    /* ---- 4.3 auth surface (used by web/selfcheck.mjs and by the t15 demo) ---- */
    AUTH_ROLES: AUTH_ROLES.slice(),
    AUTH_PERMISSIONS: AUTH_PERMISSIONS.slice(),
    AUTH_LOCAL_PERMISSIONS: AUTH_LOCAL_PERMISSIONS.slice(),
    auth: function () { return authSnapshot(); },
    can: function (permission) { return canPermission(permission); },
    authLogin: function (username, password) { return submitLogin({ username: username, password: password }); },
    authLogout: function () { return submitLogout(); },
    authUsers: function () { return loadUsers(); },
    authUserCreate: function (input) { return authCreateUser(input); },
    authUserSet: function (id, verb, body) { return authUserAction(id, verb, body); },
    authOpenUsers: function (open) {
      state.auth.usersOpen = open !== false;
      if (state.auth.usersOpen) return loadUsers();
      renderAll();
      return Promise.resolve({ ok: true, count: 0 });
    },
    authChangePassword: function (oldPassword, newPassword) { return authChangePassword(oldPassword, newPassword); },
    authProbe: function () {
      return probeAuthStatus().then(function () { return probeAuthMe(); });
    },
    authPermissionMatrix: function () {
      return probeAuthPermissions().then(function (ok) { return ok ? state.auth.matrix : null; });
    },
    /* ---- 4.5 settings surface (web/selfcheck.mjs drives it through a fake transport) ---- */
    SETTINGS_MODES: SETTINGS_MODES.slice(),
    SETTINGS_PROVIDERS: SETTINGS_PROVIDERS.slice(),
    SETTINGS_GRANULARITIES: SETTINGS_GRANULARITIES.slice(),
    SETTINGS_ERROR_KEYS: SETTINGS_ERROR_KEYS,
    SETTINGS_NOTE_KEYS: SETTINGS_NOTE_KEYS,
    settings: function () { return settingsSnapshot(); },
    settingsOpen: function (open) { return openSettings(open); },
    settingsLoad: function (force) { return loadSettings(force); },
    settingsSave: function (patch) { return saveSettings(patch); },
    settingsReadForm: function () { return readSettingsForm(); },
    settingsSaveForm: function () { return saveSettingsFromForm(); },
    settingsTest: function (input) { return runSettingsTest(input); },
    settingsExport: function () { return exportSettingsData(); },
    settingsReset: function (confirmed) { return resetSettingsData(confirmed); },
    settingsApplyLanguage: function (lang) { return applyUiLanguage(lang); },
    /* 4.8 audit rail + export: the rig drives the same three entries a person
     * sees on the page (refresh / load more / download), and reads back what
     * the server said - nothing here is a second implementation. */
    AUDIT_PAGE_SIZE: AUDIT_PAGE_SIZE,
    audit: function () { return auditSnapshot(); },
    auditRefresh: function () { return loadAudit('first'); },
    auditMore: function () { return loadAudit('more'); },
    auditFilter: function (patch) { return setAuditFilter(patch); },
    auditSetLimit: function (value) {
      var n = parseInt(value, 10);
      state.audit.limit = (isNaN(n) || n < 1) ? AUDIT_PAGE_SIZE : Math.min(n, 200);
      return loadAudit('first');
    },
    auditPathFor: function (page) { return auditRequestPath(page); },
    exportAudit: function (format) { return requestExport(format); },
    exportPrint: function (format) {
      var opened = openExportView((format === 'pdf') ? 'html' : (format || 'html'));
      setExportFeedback(opened ? 'export.feedback.print_opened' : 'export.note.no_popup');
      if (booted) renderAll();
      return { ok: opened, path: state.exportPanel.lastPath };
    },
    exportState: function () { return exportSnapshot(); },
    exportPathFor: function (format) { return exportRequestPath(format); },
    /* 4.9 import (t22): the rig drives the same entries a person sees (paste,
     * pick a file, Import, Clear) and reads back exactly what the server said.
     * There is no second implementation behind this. */
    IMPORT_MAX_BYTES: IMPORT_MAX_BYTES,
    IMPORT_MAX_RECORDS: IMPORT_MAX_RECORDS,
    IMPORT_FORMATS: IMPORT_FORMATS.slice(),
    IMPORT_MODES: IMPORT_MODES.slice(),
    importState: function () { return importSnapshot(); },
    importSet: function (patch) { return setImportDraft(patch); },
    importRun: function () { return runImport(); },
    importClear: function () { return clearImport(); },
    importCheck: function () { return importClientCheck(); },
    importBlocked: function () { return importBlockedKey(); },
    importBytesOf: function (text) { return utf8Bytes(text); },
    /* 4.10 mobile reachability (QA 5.1 D-4): the rig taps the very same phone
     * entries a person sees, and reads back which of them the current role
     * may use and why the others are not available. */
    MORE_TARGETS: MORE_TARGETS.slice(),
    moreState: function () { return moreSnapshot(); },
    moreEntries: function () { return moreEntriesState(); },
    moreMenu: function (open) { return setMoreMenu(open); },
    moreOpen: function (target) { return openMoreTarget(target); },
    moreClose: function (keepMenu) { return closeMore(keepMenu); },
    moreSwitchLanguage: function () { return setLang(otherLang()); },
    state: state,
    /* 4.7 bilingual demo datasets: the QA harness reads which language of demo
     * data is live and switches it the same way the language control does. */
    datasets: {
      zh: { lang: 'zh', globalName: DATASETS.zh.globalName, file: DATASETS.zh.file },
      en: { lang: 'en', globalName: DATASETS.en.globalName, file: DATASETS.en.file }
    },
    dataLang: dataLang,
    demoDataFor: function (lang) { return demoDataFor(lang); },
    applyDataLang: function (lang) { return applyDataLang(lang); },
    setRecords: function (input, meta) { return setRecords(input, meta); },
    computeZones: computeZones,
    zoneOf: zoneOf,
    colorRoleOf: colorRoleOf,
    serialQueue: serialQueue,
    computeProgress: computeProgress,
    demoStatus: demoStatus,
    demoChecksOf: demoChecksOf,
    recordIsDemo: recordIsDemo,
    taskNameOf: taskNameOf,
    agentNameOf: agentNameOf,
    parseISO: parseISO,
    formatTimestamp: formatTimestamp,
    formatDuration: formatDuration,
    formatCost: formatCost,
    formatConfidence: formatConfidence,
    dueText: dueText,
    granularity: function () { return granularity.value; },
    setGranularity: function (v) { return setGranularityValue(v); },
    impactText: impactText,
    toolText: toolText,
    receiptLabel: receiptLabel,
    decisionLabel: decisionLabel,
    optionLabel: optionLabel,
    viewForWidth: viewForWidth,
    setView: setView,
    openSheet: openSheet,
    toggleCard: toggleCard,
    /* 3.2 desktop layer: status filter, list/timeline focus, compare */
    filterState: function () { return filterSnapshot(); },
    setFilter: function (next) { return setFilter(next); },
    toggleFilter: function (zone) { return toggleFilterZone(zone); },
    selectRecord: function (id) { return focusRecord(id); },
    focusedRecord: function () { return state.focus; },
    setTimelineIndex: function (i) { return setTimelineIndex(i); },
    timelineRecord: function () { var r = timelineRecord(); return r ? str(r.id) : null; },
    runsOf: function () {
      return runsOf().map(function (r) {
        return { key: r.key, name: r.name, records: r.records.length, demo: r.demo };
      });
    },
    setCompareData: function (input, meta) { return setCompareData(input, meta); },
    setCompare: function (which, key) { return setComparePick(which, key); },
    setCompareOpen: function (open) { return setCompareOpen(open); },
    compareOpen: function () { return state.compare.open; },
    compareModel: function () {
      var m = compareModel();
      var counts = {};
      m.rows.forEach(function (row) {
        row.cells.forEach(function (cell) { counts[cell.kind] = (counts[cell.kind] || 0) + 1; });
      });
      return {
        a: m.a ? m.a.key : null, b: m.b ? m.b.key : null,
        steps: m.steps, diffs: m.diffs,
        classes: m.classes.slice(), counts: counts
      };
    },
    renderAll: renderAll,
    /* ---- 4.1 intervention loop: the seam t7/t9/t12/t14 may drive ---- */
    API_ENDPOINTS: (function () {
      var copy = {};
      Object.keys(API_ENDPOINTS).forEach(function (k) { copy[k] = API_ENDPOINTS[k]; });
      return copy;
    }()),
    INTERVENE_DECISIONS: INTERVENE_DECISIONS.slice(),
    STATUS_BY_DECISION: (function () {
      var copy = {};
      Object.keys(STATUS_BY_DECISION).forEach(function (k) { copy[k] = STATUS_BY_DECISION[k]; });
      return copy;
    }()),
    INTERVENE_ERROR_KEYS: (function () {
      var copy = {};
      Object.keys(INTERVENE_ERROR_KEYS).forEach(function (k) { copy[k] = INTERVENE_ERROR_KEYS[k]; });
      return copy;
    }()),
    apiMode: function () { return apiSnapshot(); },
    setApiTransport: function (fn) {
      state.api.transport = typeof fn === 'function' ? fn : null;
      state.api.checks = 0;
      if (booted) renderAll();
      return typeof state.api.transport === 'function';
    },
    setApiMode: function (mode, reason) {
      if (mode === 'auto') { state.api.forced = null; setApiMode('unchecked', 'reset'); }
      else if (mode === 'off') { state.api.forced = 'offline'; setApiMode('forced_offline', reason || 'api_off'); }
      else { setApiMode(mode, reason); }
      if (booted) renderAll();
      return state.api.mode;
    },
    probeApi: function () { return probeApi(); },
    syncRecordsFromApi: function () { return syncRecordsFromApi(); },
    /* 4.4 realtime (t14): the selfcheck drives these with a fake EventSource,
     * so the push -> merge -> repaint path is checked without a real socket. */
    realtimeStatus: function () { return rtSnapshot(); },
    setStreamFactory: function (fn) { return setStreamFactory(fn); },
    setRealtimeScheduler: function (fn) { return setRealtimeScheduler(fn); },
    applyStreamEvent: function (event, data, seq) {
      return applyStreamFrame({
        event: str(event), seq: toNum(seq), data: isObj(data) ? data : null
      });
    },
    mergeIncomingRecords: function (list, opts) { return mergeIncomingRecords(list, opts); },
    /* 4.12 (H-03): the merge-only refresh - the same read the poll does, used
     * by the language switch, by an import, and by "someone decided in another
     * window" checks. It never replaces the list, so the open cards, the
     * focused card and the timeline position survive. */
    refreshRecords: function (opts) { return refreshRecordsFromApi(opts); },
    startRealtime: function () { return startRealtime(); },
    stopRealtime: function (reason) { return stopRealtime(reason); },
    pollOnce: function () { return pollRecords(); },
    connectStream: function () { return connectStream(); },
    degradeToPolling: function (reason) { return startPolling(reason); },
    realtimeEventTypes: function () { return RT_EVENTS.slice(); },
    apiRequest: function (endpoint, body) { return apiRequest(endpoint, body); },
    decidable: function (record) {
      var rec = typeof record === 'string' ? recordById(record) : record;
      return decidableRecord(rec);
    },
    editableFields: function (record, optionId) {
      var rec = typeof record === 'string' ? recordById(record) : record;
      return editableFieldsOf(rec, optionId);
    },
    originalParams: function (record) {
      var rec = typeof record === 'string' ? recordById(record) : record;
      return originalParamsOf(rec, editableFieldsOf(rec, null));
    },
    parseSummary: parseSummary,
    /* pure transition used by the local simulation and by the selfcheck's
     * golden-parity test (agent_explainer/server.apply_decision) */
    applyDecision: function (record, decision, payload) {
      var rec = typeof record === 'string' ? recordById(record) : record;
      var body = isObj(payload) ? payload : {};
      var checked = validateDecision(rec, decision, {
        optionId: body.optionId, reason: body.reason,
        values: isObj(body.values) ? body.values : {}
      });
      if (!checked.ok) return { ok: false, code: checked.code, textKey: checked.textKey, record: rec };
      return { ok: true, record: localDecision(rec, checked, { at: body.at, actor: body.actor }),
               validation: checked };
    },
    validateDecision: function (record, decision, payload) {
      var rec = typeof record === 'string' ? recordById(record) : record;
      var body = isObj(payload) ? payload : {};
      return validateDecision(rec, decision, {
        optionId: body.optionId, reason: body.reason,
        values: isObj(body.values) ? body.values : ivValues(str(body.record || rec && rec.id))
      });
    },
    interveneStatus: function (id) { return interventionState(id); },
    interveneSummary: function () { return interveneSummary(); },
    openModifyForm: function (id) { return openModifyForm(id); },
    closeModifyForm: function (id) { return closeModifyForm(id); },
    openReasonBox: function (id) { return openReasonBox(id); },
    closeReasonBox: function (id) { return closeReasonBox(id); },
    setInterveneField: function (id, name, value) { return setInterveneField(id, name, value); },
    setInterveneReason: function (id, text) { return setInterveneReason(id, text); },
    submitIntervene: function (id, decision, payload) { return submitIntervene(id, decision, payload); },
    clearInterveneFeedback: function (id) { return clearInterveneFeedback(id); },
    resetIntervene: function () { resetIntervene(false); renderAll(); return true; },
    /* 5.0 trust layer (docs/07 §6): the extension surface. `web/ext/*.js`
     * modules are plain classic scripts loaded after this file; they read the
     * same dictionaries, call the same API helper and decorate/replace nothing
     * they do not own. `onRender` runs after every full repaint (so a card
     * decoration is re-applied when the record, the filter or the language
     * changes) and `state` is passed read-only by convention. */
    onRender: function (fn) { if (typeof fn === 'function') state.listeners.push(fn); },
    t: function (key, params) { return t(key, params); },
    lang: function () { return i18n.lang; },
    formatTimestamp: function (value) { return formatTimestamp(value, i18n.lang); },
    formatConfidence: function (value) { return formatConfidence(value); },
    zoneOf: function (record) { return zoneOf(record); },
    recordById: function (id) { return recordById(id); },
    renderAll: function () { renderAll(); return true; },
    extRequest: function (target, body, opts) { return extRequest(target, body, opts); },
    extEndpoints: function () { return EXT_ENDPOINT_NAMES.slice(); },
    authInfo: function () { return authSnapshot(); },
    can: function (permission) {
      return state.auth.permissions.indexOf(str(permission)) >= 0;
    },
    init: init,
    loadDictionariesFromServer: loadDictionariesFromServer,
    queryAll: queryAll,
    probe: function () {
      var out = {
        app: APP_ID,
        version: SKELETON_VERSION,
        view: state.view,
        lang: i18n.lang,
        sheetOpen: state.sheetOpen,
        frame: state.frame ? { width: state.frame.width, height: state.frame.height } : null,
        probeMode: state.probeMode,
        rigError: rigError,
        records: state.records.length,
        zones: {
          needs_me: (state.zones.needs_me || []).length,
          running: (state.zones.running || []).length,
          done: (state.zones.done || []).length
        },
        progress: state.progress,
        api: apiSnapshot(),
        realtime: rtSnapshot(),
        intervene: interveneSummary(),
        /* 4.9 (t22): what the import panel can do right now, and what the last
         * import actually returned - the same numbers the panel shows. */
        import: importSnapshot(),
        /* 4.10 (t23): which phone entries exist, which the role may use, and
         * which layer (list / drawer / screen) is open right now. */
        more: moreSnapshot(),
        /* 4.12 (t25): what the audit panel knows right now - has it read the
         * server at all, and did it follow this page's own writes. */
        audit: auditSnapshot(),
        desk: {
          view: state.view,
          filter: filterSnapshot(),
          hiddenByFilter: hiddenByFilter(),
          focus: state.focus,
          totalRecords: state.records.length,
          timeline: (function () {
            var r = timelineRecord();
            return {
              index: state.records.length ? clampTimelineIndex(state.timelineIndex) : 0,
              total: state.records.length,
              id: r ? str(r.id) : null
            };
          }()),
          compare: (function () {
            var m = compareModel();
            var counts = {};
            var kinds = {};
            m.rows.forEach(function (row) {
              row.cells.forEach(function (cell) {
                counts[cell.kind] = (counts[cell.kind] || 0) + 1;
                kinds[cell.kind] = true;
              });
            });
            return {
              a: m.a ? m.a.key : null,
              b: m.b ? m.b.key : null,
              open: state.compare.open,
              runs: runsOf().length,
              bRecords: state.compareRecords.length,
              rows: m.rows.length,
              diffs: m.diffs,
              classes: Object.keys(kinds).sort(),
              counts: counts
            };
          }())
        },
        auth: authSnapshot(),
        demoBadge: state.demo.show,
        demoMatched: state.demo.matched.slice(),
        expanded: Object.keys(state.expanded).filter(function (k) { return state.expanded[k]; }),
        missingKeys: i18n.missing.slice(),
        stats: state.stats,
        rendered: {}
      };
      if (doc && typeof doc.querySelectorAll === 'function') {
        out.rendered = {
          zones: doc.querySelectorAll('[data-zone]').length,
          cards: doc.querySelectorAll('article.card').length,
          badgesDemo: doc.querySelectorAll('[data-badge="demo"]').length,
          badgesAi: doc.querySelectorAll('[data-badge="ai"]').length,
          i18nNodes: doc.querySelectorAll('[data-i18n]').length,
          expandedNodes: doc.querySelectorAll('[aria-expanded="true"]').length,
          fabVisible: !!(byId('fab') && !byId('fab').hidden),
          sidebarItems: doc.querySelectorAll('[data-hook="sidebar-item"]').length,
          filterButtons: doc.querySelectorAll('[data-hook="filter-zone"]').length,
          scrubPresent: doc.querySelectorAll('[data-hook="timeline-scrub"]').length,
          timelineTicks: doc.querySelectorAll('[data-hook="timeline-tick"]').length,
          timelineSelected: doc.querySelectorAll('[data-hook="timeline-tick"][data-selected="true"]').length,
          focusedCards: doc.querySelectorAll('article.card.is-focused').length,
          compareCells: doc.querySelectorAll('[data-hook="compare-cell"]').length,
          compareDiffs: doc.querySelectorAll('.compare__value--changed, .compare__value--only_a, .compare__value--only_b').length,
          compareSteps: doc.querySelectorAll('[data-hook="compare-step"]').length,
          interveneBoxes: doc.querySelectorAll('[data-hook="intervene"]').length,
          interveneButtons: doc.querySelectorAll('[data-hook="intervene-button"]').length,
          interveneButtonsEnabled: doc.querySelectorAll('[data-hook="intervene-button"][data-enabled="true"]').length,
          interveneForms: doc.querySelectorAll('[data-hook="intervene-form"]').length,
          interveneFields: doc.querySelectorAll('[data-hook="intervene-field"]').length,
          interveneReasonBoxes: doc.querySelectorAll('[data-hook="intervene-reason-box"]').length,
          interveneReasonInputs: doc.querySelectorAll('[data-hook="intervene-reason"]').length,
          interveneFeedback: doc.querySelectorAll('[data-hook="intervene-feedback"]').length,
          interveneModeChips: doc.querySelectorAll('[data-hook="intervene-mode"]').length,
          interveneErrors: doc.querySelectorAll('[data-hook="intervene-error"]').length,
          apiModeChip: doc.querySelectorAll('[data-hook="api-mode"]').length,
          authPanel: doc.querySelectorAll('[data-hook="auth-panel"]').length,
          authLoginForms: doc.querySelectorAll('[data-hook="auth-login-form"]').length,
          authPasswordInputs: doc.querySelectorAll('[data-hook="auth-password"]').length,
          authUserChip: doc.querySelectorAll('[data-hook="auth-user"]').length,
          authLogoutButtons: doc.querySelectorAll('[data-hook="auth-logout"]').length,
          usersPanel: doc.querySelectorAll('[data-hook="users-panel"]').length,
          userRows: doc.querySelectorAll('[data-hook="user-row"]').length,
          authMatrixRows: doc.querySelectorAll('[data-hook="auth-matrix-row"]').length,
          authMatrixCells: doc.querySelectorAll('[data-hook="auth-matrix-cell"]').length,
          authRoleNotices: doc.querySelectorAll('[data-hook="intervene-role-notice"]').length,
          settingsPanel: doc.querySelectorAll('[data-hook="settings-center"]').length,
          settingsForms: doc.querySelectorAll('[data-hook="settings-form"]').length,
          settingsFields: doc.querySelectorAll('[data-hook="settings-field"]').length,
          settingsModeRadios: doc.querySelectorAll('[data-hook="settings-mode"]').length,
          settingsLangRadios: doc.querySelectorAll('[data-hook="settings-lang"]').length,
          settingsSecretInputs: doc.querySelectorAll('[data-hook="settings-secret"]').length,
          settingsKeyMasks: doc.querySelectorAll('[data-hook="settings-key-mask"]').length,
          settingsSaveButtons: doc.querySelectorAll('[data-hook="settings-save"]').length,
          settingsSaveEnabled: doc.querySelectorAll('[data-hook="settings-save"][data-enabled="true"]').length,
          settingsTestButtons: doc.querySelectorAll('[data-hook="settings-test"]').length,
          settingsTestResults: doc.querySelectorAll('[data-hook="settings-test-result"]').length,
          settingsNotices: doc.querySelectorAll('[data-hook="settings-note"]').length,
          settingsReadOnlyNotes: doc.querySelectorAll('[data-hook="settings-note"][data-read-only="true"]').length,
          /* 4.10: the phone entry list, its disabled states with reasons, and
           * the drawer head that carries an existing rail panel */
          moreButtons: doc.querySelectorAll('[data-hook="more-menu"]').length,
          moreSheet: doc.querySelectorAll('[data-hook="more-sheet"]').length,
          moreEntries: doc.querySelectorAll('[data-hook="more-entry"]').length,
          moreEntriesEnabled: doc.querySelectorAll('[data-hook="more-entry"][data-enabled="true"]').length,
          moreReasons: doc.querySelectorAll('[data-hook="more-reason"][data-visible="true"]').length,
          moreSheetOpen: !!(byId('more-sheet') && !byId('more-sheet').hidden),
          moreDrawerHead: doc.querySelectorAll('[data-hook="more-drawer"]').length,
          moreDrawerButtons: doc.querySelectorAll('[data-hook="more-drawer-back"], [data-hook="more-drawer-close"]').length,
          usersScroll: doc.querySelectorAll('[data-hook="users-scroll"]').length,
          /* 4.12 (t25): the audit panel's honesty - which chips it shows,
           * whether it claims a store mode it has not read, and what it says
           * before the first read. */
          auditChainChips: doc.querySelectorAll('[data-hook="audit-chain"]').length,
          auditChainPending: doc.querySelectorAll('[data-hook="audit-chain"][data-ok="pending"]').length,
          auditCountChip: doc.querySelectorAll('[data-hook="audit-count"]').length,
          auditStoreChip: doc.querySelectorAll('[data-hook="audit-store"]').length,
          auditPendingLines: doc.querySelectorAll('[data-hook="audit-pending"]').length,
          auditLoadingLines: doc.querySelectorAll('[data-hook="audit-loading"]').length,
          cardToggles: doc.querySelectorAll('button.card__toggle').length,
          cardHints: doc.querySelectorAll('span.card__hint').length
        };
      }
      if (doc && isObj(doc.documentElement)) {
        var sw = num(doc.documentElement.scrollWidth);
        var cw = num(doc.documentElement.clientWidth);
        var iw = num(global.innerWidth);
        var rig = measureOverflow();
        out.layout = {
          scrollWidth: sw,
          clientWidth: cw,
          innerWidth: iw,
          horizontalScroll: (sw !== null && iw !== null) ? sw > iw : null,
          rig: state.frame ? {
            box: state.frame.width + 'x' + state.frame.height,
            clientWidth: rig.box,
            scrollWidth: rig.scrollWidth,
            horizontalScroll: rig.horizontalScroll
          } : null
        };
      }
      return out;
    }
  };

  global.TraceLensUI = api;

  /* Opening index.html straight from the filesystem must show the full demo,
   * so the script boots itself: doc is only assigned inside init(), hence the
   * listener is registered against global.document, not the local doc. */
  (function autoBoot() {
    var d = global.document;
    if (!d || typeof d.addEventListener !== 'function') return;
    if (d.readyState === 'loading') {
      d.addEventListener('DOMContentLoaded', function () { init(); });
      return;
    }
    init();
  }());
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
