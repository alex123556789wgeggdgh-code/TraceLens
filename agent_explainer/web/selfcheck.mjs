/*
 * TraceLens web skeleton self-check (subtasks 3.1 / t6 and 3.2 / t7)
 * ------------------------------------------------------------------
 * Two jobs, both fully offline and dependency-free:
 *
 *   node selfcheck.mjs --sync-demo    regenerate demo-data.js from
 *                                     samples/action_records.json
 *   node selfcheck.mjs --sync-compare regenerate compare-data.js (run B,
 *                                     a documented variant of the same
 *                                     fixture, for desktop compare mode)
 *   node selfcheck.mjs --sync-embed   regenerate i18n/embed.js from
 *                                     i18n/zh.json + i18n/en.json
 *   node selfcheck.mjs                run every structural assertion
 *                                     (writes selfcheck-report.json)
 *
 * Why a shim DOM: app.js is a plain classic script (no build, no
 * framework), so the same file can be evaluated inside a minimal DOM
 * implemented here. That gives real structural assertions on the shipped
 * code: three zones, colour mapping, expand/collapse, language switch,
 * demo badge behaviour, the desktop sidebar list + status filter, the
 * timeline scrubber linkage and the compare diff marks. Pixel-level checks
 * (375x812, 1440x900, no horizontal scroll) are measured by the in-page
 * rig and screenshotted with a real browser - see README.md.
 *
 * Exit code 0 = every assertion passed, 1 = at least one failed.
 */

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(HERE, '..', '..');              // agent-explainer/
const SAMPLES = path.join(PKG, 'samples', 'action_records.json');
const SAMPLES_EN = path.join(PKG, 'samples', 'action_records_en.json');
const FILES = {
  index: path.join(HERE, 'index.html'),
  app: path.join(HERE, 'app.js'),
  styles: path.join(HERE, 'styles.css'),
  demo: path.join(HERE, 'demo-data.js'),
  demoEn: path.join(HERE, 'demo-data-en.js'),
  compare: path.join(HERE, 'compare-data.js'),
  zh: path.join(HERE, 'i18n', 'zh.json'),
  en: path.join(HERE, 'i18n', 'en.json'),
  embed: path.join(HERE, 'i18n', 'embed.js'),
  keys: path.join(HERE, 'i18n', 'keys.md'),
  self: path.join(HERE, 'selfcheck.mjs'),
  golden: path.join(HERE, 'verify', 'intervene-golden.json'),
  readme: path.join(HERE, 'README.md'),
  /* 5.0 R4: the secretariat panel is a classic script in web/ext/; the
   *  extension sources are scanned for i18n references and panel shape. */
  extSecretariat: path.join(HERE, 'ext', 'secretariat.js'),
  extSecretariatCss: path.join(HERE, 'ext', 'secretariat.css'),
  /* R1-9: the cold-start integration test runs the real shell + the real card
   * plugin over the real app.js card builder, so both files are artifacts. */
  extCore: path.join(HERE, 'ext', 'core.js'),
  extCards: path.join(HERE, 'ext', 'cards.js'),
  /* R3-4: the evidence / causal face owns its styles in ext/cards.css; the
   * copy keys it reaches for live in the cards domain of both dictionaries, so
   * both files are artifacts of this round (checks 105-106). */
  extCardsCss: path.join(HERE, 'ext', 'cards.css'),
  /* R5-4: the collaboration panel and the invitee's landing page. share.html is
   * a standalone document, so it is an artifact of its own: the one-time-token
   * rules (hash-only, no storage, no re-echo) are asserted against the file. */
  extCollab: path.join(HERE, 'ext', 'collab.js'),
  extCollabCss: path.join(HERE, 'ext', 'collab.css'),
  /* R7-2: the suggestions surface is ext/insights.js + ext/insights.css (both
   * already mounted in index.html since R6-3). The write-side mount it talks to
   * (T29 /accept, T30 /dismiss, T31 POST /api/preferences, plus the service-level
   * switch on POST /api/suggestions) is asserted over these two sources. */
  extInsights: path.join(HERE, 'ext', 'insights.js'),
  extInsightsCss: path.join(HERE, 'ext', 'insights.css'),
  /* R4-2 (REQ-004 B4-1/B4-2): the merged multi-agent view. The panel is the
   * consumer of the groups read face (T18/T19) and of the R4-1 response-layer
   * aliases, so the JS, its scoped CSS and the i18n parts file are artifacts
   * of this round; the colour table it copies from groups.py is pinned here. */
  extGroups: path.join(HERE, 'ext', 'groups.js'),
  extGroupsCss: path.join(HERE, 'ext', 'groups.css'),
  groupsPy: path.join(HERE, '..', 'groups.py'),
  i18nGroupsParts: path.join(HERE, 'i18n', 'parts', 'groups.zh.json'),
  i18nGroupsPartsEn: path.join(HERE, 'i18n', 'parts', 'groups.en.json'),
  shareHtml: path.join(HERE, 'share.html')
};

const CJK = /[\u3400-\u4DBF\u4E00-\u9FFF\u3000-\u303F\uFF00-\uFFEF]/;
const CJK_HAN = /[\u4E00-\u9FFF]/;
/* CJK counts need a global copy of the pattern (CJK itself is used with .test,
 * which must stay stateless). 4.7 asserts "zero Chinese in the English demo
 * data" as a count, so the harness counts characters, not just a yes/no. */
const CJK_ALL = new RegExp(CJK.source, 'g');
const cjkCount = (text) => String(text == null ? '' : text).split(CJK_ALL).length - 1;

function read(file) {
  return fs.readFileSync(file, 'utf8');
}

function readJson(file) {
  return JSON.parse(read(file));
}

function exists(file) {
  try {
    return fs.statSync(file).size > 0;
  } catch (e) {
    return false;
  }
}

function deepEqualJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/* ---------------------------------------------------------------- *
 * generators (single source of truth stays in samples/ and i18n/*.json)
 * ---------------------------------------------------------------- */

function syncDemo() {
  const source = readJson(SAMPLES);
  const payload = {
    _meta: Object.assign({}, source._meta || {}, {
      copy_of: 'samples/action_records.json',
      copy_generated_by: 'web/selfcheck.mjs --sync-demo',
      copy_note: (source._meta && source._meta.author_note) || ''
    }),
    records: source.records
  };
  const body = JSON.stringify(payload, null, 2);
  const out = [
    '/*',
    ' * Generated by web/selfcheck.mjs --sync-demo from samples/action_records.json.',
    ' * Do not edit by hand: it is a byte-for-byte copy of the record array, and',
    ' * web/selfcheck.mjs fails when the two diverge.',
    ' * Synthetic demo data (demo_data=true / synthetic=true / source.kind=demo).',
    ' */',
    '(function (global) {',
    "  'use strict';",
    '  global.TraceLensDemoData = ' + body + ';',
    "}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));",
    ''
  ].join('\n');
  fs.writeFileSync(FILES.demo, out, 'utf8');
  return { records: source.records.length, bytes: out.length };
}

/* The English twin of the demo set (4.7 / t20). samples/action_records_en.json
 * is written by tools/gen_samples_en.py as native English prose (same ids,
 * numbers, timestamps and statuses; only the copy changes language), and
 * demo-data-en.js is a byte-for-byte copy of it, exactly like demo-data.js is
 * for the Chinese set. An English UI loads this set, which is what makes
 * "zero Chinese data in the English interface" machine-checkable. */
function syncDemoEn() {
  const source = readJson(SAMPLES_EN);
  const payload = {
    _meta: Object.assign({}, source._meta || {}, {
      copy_of: 'samples/action_records_en.json',
      copy_generated_by: 'web/selfcheck.mjs --sync-demo',
      copy_note: (source._meta && source._meta.author_note) || ''
    }),
    records: source.records
  };
  const body = JSON.stringify(payload, null, 2);
  const out = [
    '/*',
    ' * Generated by web/selfcheck.mjs --sync-demo from samples/action_records_en.json.',
    ' * Do not edit by hand: it is a byte-for-byte copy of the English record array,',
    ' * and web/selfcheck.mjs fails when the two diverge.',
    ' * English demo set: the same 12 synthetic records as demo-data.js, with the copy',
    ' * authored in English by tools/gen_samples_en.py (no Chinese data in the en UI).',
    ' */',
    '(function (global) {',
    "  'use strict';",
    '  global.TraceLensDemoDataEn = ' + body + ';',
    "}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));",
    ''
  ].join('\n');
  fs.writeFileSync(FILES.demoEn, out, 'utf8');
  return { records: source.records.length, bytes: out.length };
}

/* Every synthetic run name the datasets can show (run A zh/en, run B). They are
 * dictionary copy (`demo.label.<name>`), so app.js holds no CJK literal and an
 * English UI renders no Chinese run label. selfcheck expands these into the
 * i18n reference set: a demo name without dictionary copy fails the run. */
function datasetPayloads() {
  const list = [];
  if (exists(FILES.demoEn)) list.push(loadScriptPayload(FILES.demoEn, 'TraceLensDemoDataEn'));
  if (exists(FILES.compare)) list.push(loadScriptPayload(FILES.compare, 'TraceLensCompareData'));
  return list.filter(Boolean);
}

function demoLabelNames() {
  const names = new Set();
  const collect = (payload) => {
    (payload.records || []).forEach((r) => {
      if (r && typeof r.agent_name === 'string' && r.agent_name) names.add(r.agent_name);
      if (r && r.context && typeof r.context.task_name === 'string' && r.context.task_name) {
        names.add(r.context.task_name);
      }
    });
  };
  collect(loadDemoPayload());
  datasetPayloads().forEach(collect);
  /* Run B's panel title is a run name and is shown in the compare table; the
   * main datasets' _meta.title is a file-level label that never renders, so it
   * must not demand dictionary copy. */
  if (exists(FILES.compare)) {
    const runB = loadScriptPayload(FILES.compare, 'TraceLensCompareData');
    if (runB._meta && typeof runB._meta.title === 'string' && runB._meta.title) {
      names.add(runB._meta.title);
    }
  }
  return [...names].sort();
}

/* Run B for the desktop compare mode: a deterministic variant of the same
 * synthetic fixture. Only machine fields are changed (status / risk / receipt /
 * latency / cost / confidence / tool version), one step is dropped and one is
 * added, so the diff marks (changed / only in A / only in B / same) all appear
 * without inventing new prose. The fact hash is cleared because the facts
 * behind it changed, and the variant table ships inside _meta. */
const COMPARE_VARIANT = {
  taskName: '整理演示相册并去重（第二次运行）',
  drop: ['act-demo-0011'],
  extra: { clone: 'act-demo-0012', suffix: 'b', sequenceNo: 13, shiftMinutes: 20 },
  status: {
    'act-demo-0002': 'succeeded',
    'act-demo-0005': 'approved',
    'act-demo-0006': 'succeeded',
    'act-demo-0009': 'succeeded'
  },
  risk: { 'act-demo-0004': 'critical', 'act-demo-0006': 'medium' },
  receipt: {
    'act-demo-0002': 'executed',
    'act-demo-0005': 'executed',
    'act-demo-0006': 'failed',
    'act-demo-0009': 'executed'
  },
  requiresAction: { 'act-demo-0005': false, 'act-demo-0006': false },
  confidence: { 'act-demo-0003': 0.45, 'act-demo-0004': 0.95, 'act-demo-0009': 0.6 },
  latencyFactor: 1.4,
  costFactor: 1.25,
  shiftMinutes: 150
};

const ISO_FULL = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

function shiftIso(iso, minutes) {
  const m = ISO_FULL.exec(String(iso || ''));
  if (!m) return iso;
  const base = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6] || 0);
  const shifted = new Date(base + minutes * 60000);
  const pad = (n, w) => String(n).padStart(w || 2, '0');
  if (m[7] === 'Z') return shifted.toISOString().replace(/\.\d{3}Z$/, 'Z');
  const sign = m[7][0] === '-' ? -1 : 1;
  const offsetMinutes = sign * (parseInt(m[7].slice(1, 3), 10) * 60 + parseInt(m[7].slice(4, 6), 10));
  const local = new Date(shifted.getTime() + offsetMinutes * 60000);
  return pad(local.getUTCFullYear(), 4) + '-' + pad(local.getUTCMonth() + 1) + '-' + pad(local.getUTCDate()) +
    'T' + pad(local.getUTCHours()) + ':' + pad(local.getUTCMinutes()) + ':' + pad(local.getUTCSeconds()) + m[7];
}

function variantRecords(sourceRecords) {
  const V = COMPARE_VARIANT;
  const idMap = {};
  sourceRecords.forEach((r) => {
    idMap[r.id] = String(r.id).replace(/^act-demo-/, 'act-demo-b-');
  });
  const remap = (value) => (Object.prototype.hasOwnProperty.call(idMap, value) ? idMap[value] : value);
  const out = [];
  sourceRecords.forEach((record, i) => {
    if (V.drop.indexOf(record.id) >= 0) return;
    const r = JSON.parse(JSON.stringify(record));
    const newId = idMap[record.id];
    r.id = newId;
    if (r.parent_action_id) r.parent_action_id = remap(r.parent_action_id);
    r.run_id = 'run-demo-b';
    if (r.session_id) r.session_id = String(r.session_id).replace(/^sess-/, 'sess-b-');
    r.timestamp = shiftIso(r.timestamp, V.shiftMinutes);
    r.received_at = shiftIso(r.received_at, V.shiftMinutes);
    if (V.status[r.id]) r.status = V.status[r.id];
    if (V.risk[r.id]) r.risk_level = V.risk[r.id];
    if (Object.prototype.hasOwnProperty.call(V.requiresAction, r.id)) r.requires_action = V.requiresAction[r.id];
    if (Object.prototype.hasOwnProperty.call(V.confidence, r.id)) r.confidence = V.confidence[r.id];
    if (V.receipt[r.id] && r.execution_receipt) r.execution_receipt.state = V.receipt[r.id];
    if (typeof r.latency_ms === 'number') r.latency_ms = Math.round(r.latency_ms * V.latencyFactor);
    if (r.cost && typeof r.cost.amount === 'number') r.cost.amount = Math.round(r.cost.amount * V.costFactor * 1e4) / 1e4;
    if (r.tool_used && r.tool_used.version) r.tool_used.version = String(r.tool_used.version) + ' (b)';
    if (r.context) r.context.task_name = V.taskName;
    if (r.human_readable) r.human_readable.source_facts_hash = null;
    if (r.source) {
      r.source.source_uri = 'samples/action_records.json (variant B)';
      r.source.raw_ref = 'local://raw/native/' + newId + '/source.json';
      r.source.kind = 'demo';
    }
    r.demo_data = true;
    r.synthetic = true;
    out.push(r);
  });
  const template = sourceRecords.filter((r) => r.id === V.extra.clone)[0];
  if (template) {
    const extra = JSON.parse(JSON.stringify(template));
    const last = out.length ? out[out.length - 1] : extra;
    const newId = 'act-demo-b-' + String(V.extra.sequenceNo).padStart(4, '0');
    extra.id = newId;
    extra.sequence_no = V.extra.sequenceNo;
    extra.run_id = 'run-demo-b';
    if (extra.session_id) extra.session_id = String(extra.session_id).replace(/^sess-/, 'sess-b-');
    extra.parent_action_id = last.parent_action_id ? remap(last.parent_action_id) : last.id;
    extra.timestamp = shiftIso(last.timestamp, V.extra.shiftMinutes);
    extra.received_at = shiftIso(last.received_at, V.extra.shiftMinutes);
    extra.status = 'succeeded';
    if (extra.execution_receipt) extra.execution_receipt.state = 'executed';
    if (extra.context) extra.context.task_name = V.taskName;
    if (extra.human_readable) extra.human_readable.source_facts_hash = null;
    if (extra.source) {
      extra.source.source_uri = 'samples/action_records.json (variant B)';
      extra.source.raw_ref = 'local://raw/native/' + newId + '/source.json';
      extra.source.kind = 'demo';
    }
    extra.demo_data = true;
    extra.synthetic = true;
    out.push(extra);
  }
  return out;
}

function comparePayload() {
  const source = readJson(SAMPLES);
  const records = variantRecords(source.records);
  return {
    _meta: {
      title: COMPARE_VARIANT.taskName,
      synthetic: true,
      real_data: false,
      demo_data: true,
      author_note: '合成对照运行：由 samples/action_records.json 按 _meta.variant 这张固定变体表派生，' +
        '只改机器字段（状态/风险/回执/耗时/花费/把握度/工具版本），删掉一步、多加一步，' +
        '用来演示“对比两次运行 + 差异高亮”。没有新写文案，也没有重算事实指纹（source_facts_hash=null）。',
      derived_from: 'samples/action_records.json',
      copy_generated_by: 'web/selfcheck.mjs --sync-compare',
      variant: COMPARE_VARIANT,
      record_count: records.length,
      generated_by: 'web/selfcheck.mjs --sync-compare',
      offline: true,
      no_secrets: true
    },
    records: records
  };
}

function syncCompare() {
  const payload = comparePayload();
  const out = [
    '/*',
    ' * Generated by web/selfcheck.mjs --sync-compare from samples/action_records.json.',
    ' * Do not edit by hand: web/selfcheck.mjs regenerates it and fails on any drift.',
    ' * Run B of the desktop compare mode: a deterministic variant (see _meta.variant).',
    ' * Synthetic demo data (demo_data=true / synthetic=true / source.kind=demo).',
    ' */',
    '(function (global) {',
    "  'use strict';",
    '  global.TraceLensCompareData = ' + JSON.stringify(payload, null, 2) + ';',
    "}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));",
    ''
  ].join('\n');
  fs.writeFileSync(FILES.compare, out, 'utf8');
  return { records: payload.records.length, bytes: out.length };
}

function syncEmbed() {  const zh = readJson(FILES.zh);
  const en = readJson(FILES.en);
  const out = [
    '/*',
    ' * Generated by web/selfcheck.mjs --sync-embed from i18n/zh.json + i18n/en.json.',
    ' * Do not edit by hand. It exists so that index.html works straight from the',
    ' * filesystem (file:// blocks fetch); when served over http(s), app.js re-reads',
    ' * the canonical JSON dictionaries. web/selfcheck.mjs fails on any drift.',
    ' */',
    '(function (global) {',
    "  'use strict';",
    '  global.TraceLensDicts = {',
    '    zh: ' + JSON.stringify(zh, null, 2) + ',',
    '    en: ' + JSON.stringify(en, null, 2),
    '  };',
    "}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));",
    ''
  ].join('\n');
  fs.writeFileSync(FILES.embed, out, 'utf8');
  return { zh: Object.keys(zh).length, en: Object.keys(en).length, bytes: out.length };
}

/* ---------------------------------------------------------------- *
 * minimal DOM shim (only the API surface app.js uses)
 * ---------------------------------------------------------------- */

class TextNode {
  constructor(value, owner) {
    this.tagName = '#text';
    this.nodeValue = String(value);
    this.parentNode = null;
    this._owner = owner || null;
  }
  get ownerDocument() { return this._owner || DOC; }
  get textContent() { return this.nodeValue; }
  set textContent(v) { this.nodeValue = String(v); }
}

class ClassList {
  constructor(node) { this.node = node; }
  _list() {
    const v = this.node.getAttribute('class');
    return v ? String(v).trim().split(/\s+/).filter(Boolean) : [];
  }
  _save(list) { this.node.setAttribute('class', list.join(' ')); }
  add(...names) {
    const list = this._list();
    names.forEach((n) => { if (n && list.indexOf(n) < 0) list.push(n); });
    this._save(list);
  }
  remove(...names) { this._save(this._list().filter((n) => names.indexOf(n) < 0)); }
  contains(name) { return this._list().indexOf(name) >= 0; }
  toggle(name, force) {
    const has = this.contains(name);
    const want = force === undefined ? !has : !!force;
    if (want) this.add(name); else this.remove(name);
    return want;
  }
  get length() { return this._list().length; }
  toString() { return this._list().join(' '); }
}

let DOC = null;

/* R1-9 (D-1): the trust layer follows the card tree with a MutationObserver
 * (web/ext/core.js), and the card badge is inserted with insertBefore - two
 * surfaces the earlier checks never touched. Delivery is a microtask, as in a
 * browser. Both are inert while nobody subscribes: recordMutation returns at
 * its first line when no observer exists, so the older checks see no change. */
const mutationHub = { observers: [], scheduled: false };

function isSelfOrDescendant(node, root) {
  let n = node;
  while (n) {
    if (n === root) return true;
    n = n.parentNode;
  }
  return false;
}

function flushMutations() {
  mutationHub.scheduled = false;
  mutationHub.observers.slice().forEach((obs) => {
    if (!obs.connected || !obs.records.length) return;
    const records = obs.records;
    obs.records = [];
    try {
      obs.callback(records, obs);
    } catch (e) {
      /* a broken observer must never break the page it watches */
    }
  });
}

function recordMutation(target, added, removed) {
  if (!mutationHub.observers.length) return;
  mutationHub.observers.forEach((obs) => {
    if (!obs.connected) return;
    const covered = obs.opts && obs.opts.subtree
      ? isSelfOrDescendant(target, obs.target)
      : target === obs.target;
    if (!covered) return;
    obs.records.push({ type: 'childList', target, addedNodes: added, removedNodes: removed });
  });
  if (mutationHub.scheduled) return;
  mutationHub.scheduled = true;
  Promise.resolve().then(flushMutations);
}

class MutationObserverShim {
  constructor(callback) {
    this.callback = callback;
    this.records = [];
    this.target = null;
    this.opts = {};
    this.connected = false;
  }
  observe(target, opts) {
    this.target = target;
    this.opts = opts || {};
    this.connected = true;
    mutationHub.observers.push(this);
  }
  disconnect() {
    this.connected = false;
    this.records = [];
    const i = mutationHub.observers.indexOf(this);
    if (i >= 0) mutationHub.observers.splice(i, 1);
  }
  takeRecords() {
    const out = this.records;
    this.records = [];
    return out;
  }
}

class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.attributes = Object.create(null);
    this.childNodes = [];
    this.parentNode = null;
    this.classList = new ClassList(this);
    this.style = {};
    this._listeners = Object.create(null);
    /* the owning document is decided at creation: each boot gets its own id
     * registry, so a later boot can never steal an id from an earlier one */
    this._owner = owner || null;
  }
  get ownerDocument() { return this._owner || DOC; }
  get id() { return this.getAttribute('id') || ''; }
  set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { if (v) this.setAttribute('hidden', 'true'); else this.removeAttribute('hidden'); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(v) { if (v) this.setAttribute('disabled', 'true'); else this.removeAttribute('disabled'); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') {
      const doc = this._owner || DOC;
      if (doc && typeof doc._index === 'function') doc._index(this);
    }
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  get dataset() {
    const node = this;
    return new Proxy(Object.create(null), {
      get(_t, prop) {
        if (typeof prop !== 'string') return undefined;
        const v = node.getAttribute('data-' + prop.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()));
        return v === null ? undefined : v;
      },
      set(_t, prop, value) {
        node.setAttribute('data-' + String(prop).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()), String(value));
        return true;
      },
      has(_t, prop) {
        return node.hasAttribute('data-' + String(prop).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()));
      }
    });
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    recordMutation(this, [child], []);
    return child;
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    const i = this.childNodes.indexOf(ref);
    child.parentNode = this;
    if (i < 0) this.childNodes.push(child); else this.childNodes.splice(i, 0, child);
    recordMutation(this, [child], []);
    return child;
  }
  replaceChild(newChild, oldChild) {
    const i = this.childNodes.indexOf(oldChild);
    if (i < 0) return oldChild;
    if (newChild.parentNode) newChild.parentNode.removeChild(newChild);
    this.childNodes[i] = newChild;
    newChild.parentNode = this;
    oldChild.parentNode = null;
    recordMutation(this, [newChild], [oldChild]);
    return oldChild;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    recordMutation(this, [], [child]);
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get firstChild() { return this.childNodes.length ? this.childNodes[0] : null; }
  get children() { return this.childNodes.filter((n) => n instanceof Element); }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(value) {
    this.childNodes.forEach((n) => { n.parentNode = null; });
    this.childNodes = [];
    const s = String(value);
    if (s !== '') this.appendChild(new TextNode(s, this._owner));
  }
  addEventListener(type, handler) {
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const list = this._listeners[type] || [];
    const i = list.indexOf(handler);
    if (i >= 0) list.splice(i, 1);
  }
  dispatchEvent(event) {
    const ev = Object.assign({ target: this, defaultPrevented: false, preventDefault() { ev.defaultPrevented = true; }, stopPropagation() { ev._stopped = true; } }, event);
    let node = this;
    while (node) {
      (node._listeners && node._listeners[ev.type] || []).slice().forEach((fn) => fn(ev));
      if (ev._stopped) break;
      node = node.parentNode;
    }
    return ev;
  }
  click() { return this.dispatchEvent({ type: 'click' }); }
  focus() { if (DOC) DOC.activeElement = this; }
  scrollIntoView() {}
  matches(selector) { return matchSelector(this, selector); }
  closest(selector) {
    let node = this;
    while (node) {
      if (node instanceof Element && node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelectorAll(selector) { return queryAll(this, selector); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
}

const COMPOUND_RE = /(\[[^\]]+\])|([#.]?[A-Za-z0-9_-]+)/g;

function parseCompound(text) {
  const out = { tag: null, id: null, classes: [], attrs: [] };
  let m;
  COMPOUND_RE.lastIndex = 0;
  while ((m = COMPOUND_RE.exec(text))) {
    const token = m[1] || m[2];
    if (token.startsWith('[')) {
      const inner = token.slice(1, -1);
      const op = /(^[^=]*?)([\^~\$|\*]?=)(.*)$/.exec(inner);
      if (op) {
        out.attrs.push({
          name: op[1].trim(),
          op: op[2],
          value: op[3].replace(/^["']|["']$/g, '')
        });
      } else {
        out.attrs.push({ name: inner.trim(), op: null, value: null });
      }
    } else if (token.startsWith('#')) out.id = token.slice(1);
    else if (token.startsWith('.')) out.classes.push(token.slice(1));
    else out.tag = token.toUpperCase();
  }
  return out;
}

function attrMatches(node, attr) {
  if (!node.hasAttribute(attr.name)) return false;
  if (!attr.op) return true;
  const value = node.getAttribute(attr.name) || '';
  switch (attr.op) {
    case '=': return value === attr.value;
    case '^=': return value.indexOf(attr.value) === 0;
    case '$=': return attr.value === '' || value.slice(-attr.value.length) === attr.value;
    case '*=': return value.indexOf(attr.value) >= 0;
    case '~=': return value.split(/\s+/).indexOf(attr.value) >= 0;
    case '|=': return value === attr.value || value.indexOf(attr.value + '-') === 0;
    default: return false;
  }
}

function matchCompound(node, compound) {
  if (!(node instanceof Element)) return false;
  if (compound.tag && node.tagName !== compound.tag) return false;
  if (compound.id && node.getAttribute('id') !== compound.id) return false;
  for (const cls of compound.classes) if (!node.classList.contains(cls)) return false;
  for (const attr of compound.attrs) if (!attrMatches(node, attr)) return false;
  return true;
}

function matchSelector(node, selector) {
  return String(selector).split(',').some((group) => {
    const chain = group.trim().split(/\s+/).map(parseCompound);
    if (!chain.length) return false;
    if (!matchCompound(node, chain[chain.length - 1])) return false;
    let idx = chain.length - 2;
    let parent = node.parentNode;
    while (idx >= 0) {
      if (!parent) return false;
      if (matchCompound(parent, chain[idx])) idx -= 1;
      parent = parent.parentNode;
    }
    return true;
  });
}

function walk(root, visit) {
  root.childNodes.forEach((child) => {
    if (child instanceof Element) {
      visit(child);
      walk(child, visit);
    }
  });
}

function queryAll(root, selector) {
  const groups = String(selector).split(',').map((g) => g.trim()).filter(Boolean);
  const found = [];
  walk(root, (node) => {
    if (groups.some((g) => matchSelector(node, g))) found.push(node);
  });
  return found;
}

function allNodes(root) {
  const out = [];
  walk(root, (n) => out.push(n));
  return out;
}

/* Tiny HTML parser: enough for our own well-formed index.html. It lets the
 * shim start from the real static skeleton, so getElementById resolves the
 * ids that index.html really ships (and fails when they are missing). */
function parseHtml(html, owner) {
  const src = html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<script[\s\S]*?<\/script>/g, '')
    .replace(/<style[\s\S]*?<\/style>/g, '')
    .replace(/<!DOCTYPE[^>]*>/i, '');
  const root = new Element('#document', owner);
  let current = root;
  const stack = [];
  const token = /<([A-Za-z][\w-]*)((?:\s+[^<>]*?)?)(\/?)>|<\/([A-Za-z][\w-]*)>|([^<]+)/g;
  let m;
  while ((m = token.exec(src))) {
    if (m[1]) {
      const node = new Element(m[1], owner);
      const attrRe = /([\w:-]+)(?:\s*=\s*"([^"]*)"|\s*=\s*'([^']*)')?/g;
      let a;
      while ((a = attrRe.exec(m[2] || ''))) {
        if (!a[1]) continue;
        node.setAttribute(a[1], a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : ''));
      }
      current.appendChild(node);
      if (!m[3]) {
        stack.push(current);
        current = node;
      }
    } else if (m[4]) {
      const popped = stack.pop();
      if (popped) current = popped;
    } else if (m[5] && m[5].trim() && current !== root) {
      current.appendChild(new TextNode(m[5].trim(), owner));
    }
  }
  return root;
}

function makeDocument(html) {
  const doc = {
    readyState: 'complete',
    title: '',
    activeElement: null,
    documentElement: null,
    body: null,
    _ids: new Map(),
    _listeners: Object.create(null),
    createElement(tag) { return new Element(tag, doc); },
    createTextNode(value) { return new TextNode(value, doc); },
    getElementById(id) { return doc._ids.get(id) || null; },
    _index(node) {
      const id = node.getAttribute('id');
      if (!id) return;
      if (node._owner && node._owner !== doc) return; /* foreign node */
      doc._ids.set(id, node);
    },
    addEventListener(type, handler) { (doc._listeners[type] = doc._listeners[type] || []).push(handler); },
    removeEventListener(type, handler) {
      const list = doc._listeners[type] || [];
      const i = list.indexOf(handler);
      if (i >= 0) list.splice(i, 1);
    },
    dispatchEvent(event) {
      const ev = Object.assign({ target: doc.body, defaultPrevented: false, preventDefault() { ev.defaultPrevented = true; } }, event);
      (doc._listeners[ev.type] || []).slice().forEach((fn) => fn(ev));
      return ev;
    },
    querySelectorAll(selector) { return queryAll(doc.documentElement, selector); },
    querySelector(selector) { return queryAll(doc.documentElement, selector)[0] || null; }
  };
  const parsed = parseHtml(html, doc);
  const htmlEl = parsed.children.find((n) => n.tagName === 'HTML') || parsed;
  doc.documentElement = htmlEl === parsed ? new Element('html', doc) : htmlEl;
  doc.body = doc.documentElement.children.find((n) => n.tagName === 'BODY') || parsed;
  DOC = doc;
  walk(doc.documentElement, (n) => doc._index(n));
  return doc;
}

function makeSandbox(html, options) {
  const opts = options || {};
  const document = makeDocument(html);
  if (opts.readyState) document.readyState = opts.readyState;
  const store = opts.storage instanceof Map ? opts.storage : new Map();
  const storage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    get length() { return store.size; }
  };
  const sandbox = {
    document,
    localStorage: storage,
    __storage: store,
    navigator: { language: opts.language || 'zh-CN' },
    location: { protocol: opts.protocol || 'file:', search: opts.search || '', href: 'file:///index.html' + (opts.search || '') },
    innerWidth: opts.innerWidth || 375,
    innerHeight: opts.innerHeight || 812,
    matchMedia(query) {
      const width = this.innerWidth || opts.innerWidth || 375;
      const max = /max-width:\s*(\d+)/.exec(query);
      const min = /min-width:\s*(\d+)/.exec(query);
      let matches = false;
      if (max) matches = width <= Number(max[1]);
      else if (min) matches = width >= Number(min[1]);
      return { matches, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} };
    },
    addEventListener(type, handler) { (sandbox.__listeners[type] = sandbox.__listeners[type] || []).push(handler); },
    removeEventListener() {},
    __listeners: Object.create(null),
    setTimeout,
    clearTimeout,
    /* R1-9 (D-1): web/ext/core.js follows the card tree with a
     * MutationObserver; the shim delivers on a microtask like a browser. */
    MutationObserver: MutationObserverShim,
    queueMicrotask: (fn) => Promise.resolve().then(fn),
    console: { log() {}, warn() {}, error() {} },
    Intl,
    JSON,
    Math,
    Date,
    decodeURIComponent,
    encodeURIComponent
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

function loadScript(sandbox, file, label) {
  const code = read(file);
  vm.runInContext(code, sandbox, { filename: label || file });
}

/* Load demo-data.js / embed.js exactly the way a browser would: evaluate the
 * classic script and read the global it publishes. No regex JSON scraping. */
/* Load a generated data script the way a browser would: evaluate the classic
 * script and read the global it publishes. No regex JSON scraping. */
function loadScriptPayload(file, globalName) {
  const sandbox = makeSandbox('<html><body></body></html>');
  loadScript(sandbox, file, path.basename(file));
  const data = sandbox[globalName];
  if (!data || !Array.isArray(data.records)) {
    throw new Error(path.basename(file) + ' exposes no records array');
  }
  return data;
}

function loadDemoPayload() {
  return loadScriptPayload(FILES.demo, 'TraceLensDemoData');
}

function loadDemoPayloadEn() {
  return loadScriptPayload(FILES.demoEn, 'TraceLensDemoDataEn');
}

/* The page script order is index.html's own: dictionaries, demo set (run A),
 * compare set (run B), app. Both boot paths use this list, so the shim can
 * never drift from the shipped page. */
function loadPageScripts(sandbox) {
  loadScript(sandbox, FILES.embed, 'embed.js');
  loadScript(sandbox, FILES.demo, 'demo-data.js');
  loadScript(sandbox, FILES.demoEn, 'demo-data-en.js');
  loadScript(sandbox, FILES.compare, 'compare-data.js');
  loadScript(sandbox, FILES.app, 'app.js');
}

function boot(options) {
  const opts = Object.assign({ search: '', innerWidth: 375, innerHeight: 812 }, options || {});
  const sandbox = makeSandbox(read(FILES.index), opts);
  const zh = readJson(FILES.zh);
  const en = readJson(FILES.en);
  sandbox.TraceLensDicts = { zh, en };
  loadPageScripts(sandbox);
  const ui = sandbox.TraceLensUI;
  if (!ui) throw new Error('app.js did not expose TraceLensUI');
  /* no explicit init(): the sandbox document is readyState=complete, so
   * app.js boots itself exactly like the static file:// page does */
  return { sandbox, ui, document: sandbox.document };
}

/* R1-9 (D-1): the same page, plus the trust layer, in index.html's own script
 * order (dictionaries, demo set, compare set, app.js, ext/core.js,
 * ext/cards.js). app.js boots itself while it is being evaluated, so the DOM
 * handed back is the page's FIRST paint - the shell's onRender listener did
 * not exist yet, which is exactly the shape R1-7 measured on a real machine
 * (REPORT-R1-7 §5 A4 / §9 D-1). The caller boots the shell itself
 * (ext.boot(), what the page does on its DOMContentLoaded timeout), so the
 * DOM can be read at each step with no timing guesswork. */
function bootExtLayer(options) {
  const opts = Object.assign({ search: '', innerWidth: 1400, innerHeight: 900, protocol: 'https:' }, options || {});
  const sandbox = makeSandbox(read(FILES.index), opts);
  const zh = readJson(FILES.zh);
  const en = readJson(FILES.en);
  sandbox.TraceLensDicts = { zh, en };
  loadPageScripts(sandbox);
  const ui = sandbox.TraceLensUI;
  if (!ui) throw new Error('app.js did not expose TraceLensUI');
  const doc = sandbox.document;
  const firstPaint = {
    cards: doc.querySelectorAll('article.card').length,
    details: doc.querySelectorAll('.card__detail').length,
    rollbackRows: doc.querySelectorAll('.cards__rollback').length,
    rollbackButtons: doc.querySelectorAll('.cards__rollback-btn').length
  };
  loadScript(sandbox, FILES.extCore, 'ext/core.js');
  loadScript(sandbox, FILES.extCards, 'ext/cards.js');
  const ext = sandbox.TraceLensExt;
  if (!ext) throw new Error('ext/core.js did not expose TraceLensExt');
  return { sandbox, ui, ext, document: doc, firstPaint };
}

/* ---------------------------------------------------------------- *
 * helpers for assertions
 * ---------------------------------------------------------------- */

function byZone(root) {
  return queryAll(root, 'section[data-zone]');
}

function cards(root) {
  return queryAll(root, 'article.card');
}

function cardById(root, id) {
  return cards(root).find((c) => c.getAttribute('data-id') === id) || null;
}

function findToggle(root, id) {
  return queryAll(root, 'button.card__toggle').find((b) => b.getAttribute('data-card') === id) || null;
}

function detailOf(root, id) {
  return queryAll(root, '[data-card-detail]').find((n) => n.getAttribute('data-card-detail') === id) || null;
}

function keyLabelsOf(root, id) {
  const detail = detailOf(root, id);
  if (!detail) return [];
  return queryAll(detail, 'dt.kv__key').map((n) => n.textContent);
}

function dictKeys(dict, prefix, out) {
  const keys = out || [];
  Object.keys(dict).forEach((k) => {
    const pathPart = prefix ? prefix + '.' + k : k;
    const v = dict[k];
    if (v && typeof v === 'object' && !Array.isArray(v)) dictKeys(v, pathPart, keys);
    else keys.push(pathPart);
  });
  return keys;
}

function dictValues(dict, prefix, out) {
  const map = out || {};
  Object.keys(dict).forEach((k) => {
    const pathPart = prefix ? prefix + '.' + k : k;
    const v = dict[k];
    if (v && typeof v === 'object' && !Array.isArray(v)) dictValues(v, pathPart, map);
    else map[pathPart] = v;
  });
  return map;
}

function collectReferencedKeys(appSource) {
  /* Any single-quoted dotted literal counts as a reference: t('a.b'),
   * row('card.field.intent'), ['zones', 'nav.sheet.title'], data-i18n
   * assignments. Keys built by concatenation ('status.' + key) are expanded
   * from the enums below, since the literal alone is only a prefix. */
  const literals = new Set();
  const dotted = /'([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)'/g;
  let m;
  while ((m = dotted.exec(appSource))) {
    if (!m[1].endsWith('.')) literals.add(m[1]);
  }
  const prefixes = new Set();
  const concat = /'([a-z][a-z0-9_.]*[._])'\s*\+/g;
  while ((m = concat.exec(appSource))) prefixes.add(m[1]);
  const STATUS = ['observed', 'pending_confirmation', 'approved', 'modified', 'rejected', 'running', 'succeeded', 'failed', 'skipped', 'expired', 'unknown'];
  const TYPES = ['read', 'write', 'delete', 'plan', 'confirm', 'ask', 'error', 'tool_call', 'wait', 'finish', 'intervention', 'config_change', 'send', 'payment', 'export', 'other', 'unknown'];
  const RISKS = ['low', 'medium', 'high', 'critical', 'unknown'];
  const RECEIPTS = ['unreported', 'executed', 'failed', 'refused_by_runtime', 'timeout'];
  const DECISIONS = ['approve', 'modify', 'reject'];
  const ROLES = ['admin', 'editor', 'viewer', 'agent', 'unknown'];
  const KINDS = ['retry', 'ignore', 'provide_input', 'edit_params', 'other'];
  const VIEWS = ['mobile', 'tablet', 'desktop'];
  const ZONES = ['needs_me', 'running', 'done'];
  const ENUMS = {
    'status.': STATUS, 'action_type.': TYPES, 'risk.': RISKS,
    'intervene.receipt.': RECEIPTS, 'intervene.decision.': DECISIONS,
    'role.': ROLES, 'card.suggested.kind.': KINDS, 'nav.view.': VIEWS,
    'nav.lang.': ['zh', 'en'], 'demo.check.': ['demo_data', 'synthetic', 'source_kind'],
    'level.': ['l1', 'l2', 'l3'],
    'demo.label.': demoLabelNames(),
    'zones.': ZONES.concat(ZONES.map((z) => z + '.title'), ZONES.map((z) => z + '.hint'), ZONES.map((z) => z + '.aria'), ZONES.map((z) => z + '.empty'))
  };
  const dynamic = [];
  prefixes.forEach((prefix) => {
    const list = ENUMS[prefix];
    if (list) list.forEach((leaf) => dynamic.push(prefix + leaf));
  });
  return {
    literals: [...literals].sort(),
    prefixes: [...prefixes].sort(),
    dynamic: dynamic.sort(),
    all: [...new Set([...literals, ...dynamic])].sort()
  };
}

function syncKeys() {
  const zhVals = dictValues(readJson(FILES.zh));
  const enVals = dictValues(readJson(FILES.en));
  const keys = Object.keys(zhVals).sort();
  const domains = [...new Set(keys.map((k) => k.split('.')[0]))].sort();
  const lines = [];
  lines.push('# i18n key inventory (`zh.json` / `en.json`)');
  lines.push('');
  lines.push('> Generated by `node selfcheck.mjs --sync-keys`. Do not edit by hand.');
  lines.push('> `selfcheck.mjs` fails when this inventory, the two dictionaries and the keys referenced by `app.js` drift apart.');
  lines.push('');
  lines.push('- key 命名：`<domain>.<object>.<action|state|field>`，全小写点分（`docs/03-architecture.md` 第 6 节）。');
  lines.push('- 双向覆盖率必须是 100%：切到 en 后界面 0 条中文残留（K-10）。');
  lines.push('- 渲染出来的节点都会带 `data-i18n="<key>"` 与 `data-i18n-lang`，因此"界面残留中文"可以机器断言，不靠人眼。');
  lines.push('- 新增文案的顺序：先加 `zh.json` + `en.json` → `node selfcheck.mjs --sync-keys --sync-embed` → 在 `app.js` 用 `t(' + '`' + "key" + '`' + ')` 引用。');
  lines.push('');
  lines.push('**Key 总数：' + keys.length + '（zh ' + keys.length + ' / en ' + Object.keys(enVals).length + '，双向一致）**');
  lines.push('');
  domains.forEach((domain) => {
    const dk = keys.filter((k) => k.split('.')[0] === domain);
    lines.push('## `' + domain + '`（' + dk.length + ' 个）');
    lines.push('');
    lines.push('| key | zh | en |');
    lines.push('| --- | --- | --- |');
    dk.forEach((k) => {
      const esc = (v) => String(v).replace(/\|/g, '\\|').replace(/\n/g, ' ');
      lines.push('| `' + k + '` | ' + esc(zhVals[k]) + ' | ' + esc(enVals[k]) + ' |');
    });
    lines.push('');
  });
  fs.writeFileSync(FILES.keys, lines.join('\n'), 'utf8');
  return { keys: keys.length, domains: domains.length, bytes: lines.join('\n').length };
}

/* ---------------------------------------------------------------- *
 * assertions
 * ---------------------------------------------------------------- */

function buildChecks() {
  const zh = readJson(FILES.zh);
  const en = readJson(FILES.en);
  const sample = readJson(SAMPLES);
  const appSource = read(FILES.app);
  const indexSource = read(FILES.index);
  const styleSource = read(FILES.styles);
  const demoSource = read(FILES.demo);
  const embedSource = read(FILES.embed);
  const keysMd = exists(FILES.keys) ? read(FILES.keys) : '';
  const extSecretariatSource = read(FILES.extSecretariat);
  const extSecretariatCss = read(FILES.extSecretariatCss);
  const extCollabSource = read(FILES.extCollab);
  const extCollabCssSource = read(FILES.extCollabCss);
  const extInsightsSource = read(FILES.extInsights);
  const extInsightsCssSource = read(FILES.extInsightsCss);
  const shareHtmlSource = read(FILES.shareHtml);

  const zhKeys = dictKeys(zh);
  const enKeys = dictKeys(en);
  /* 5.0: the trust-layer panels live in web/ext/*.js. Their dictionary keys
   * must count as referenced too, otherwise every extension key looks orphan
   * (this was the root cause of check 09 staying red at 89/91). */
  const extSources = (() => {
    try {
      const dir = path.join(HERE, 'ext');
      return fs.readdirSync(dir).filter((f) => f.endsWith('.js')).sort()
        .map((f) => read(path.join(dir, f))).join('\n');
    } catch (e) { return ''; }
  })();
  const referencedApp = collectReferencedKeys(appSource);
  const referenced = collectReferencedKeys(appSource + '\n' + extSources);

  /* Strings that come out of the demo fixture. The English UI must contain no
   * Chinese *copy*; a Chinese task name inside a demo record is data, not
   * chrome, so chrome assertions strip fixture strings before looking for CJK.
   * (English record text is t4 / t7 work, not 3.1.) */
  const dataStrings = (() => {
    const out = new Set();
    const walk = (value) => {
      if (typeof value === 'string') { if (value.length >= 2) out.add(value); return; }
      if (Array.isArray(value)) { value.forEach(walk); return; }
      if (value && typeof value === 'object') Object.keys(value).forEach((k) => walk(value[k]));
    };
    walk(sample.records);
    walk(sample._meta);
    return [...out].sort((a, b) => b.length - a.length);
  })();
  const stripData = (text) => dataStrings.reduce(
    (acc, s) => (acc.indexOf(s) >= 0 ? acc.split(s).join(' ') : acc),
    String(text));

  let ctx = null;
  function bootOnce() {
    if (!ctx) ctx = boot({ search: '' });
    return ctx;
  }

  const checks = [];
  const check = (name, fn) => checks.push({ name, fn });

  check('files: all required artifacts exist and are non-empty', () => {
    const missing = Object.keys(FILES).filter((k) => !exists(FILES[k]));
    if (missing.length) throw new Error('missing: ' + missing.join(', '));
    return Object.keys(FILES).map((k) => k + '=' + fs.statSync(FILES[k]).size + 'B').join(' ');
  });

  check('demo-data.js is an exact copy of samples/action_records.json records', () => {
    const payload = loadDemoPayload();
    if (!deepEqualJson(payload.records, sample.records)) throw new Error('records differ from samples/action_records.json');
    return 'records=' + payload.records.length + ', deep-equal to samples (loaded as a real classic script)';
  });

  check('demo set: >=12 records, >=10 action_type, three zones, all demo-flagged', () => {
    const recs = sample.records;
    const types = new Set(recs.map((r) => r.action_type));
    const zones = { needs_me: 0, running: 0, done: 0 };
    recs.forEach((r) => {
      const z = (r.requires_action === true && (r.status === 'pending_confirmation' || r.status === 'expired')) ? 'needs_me'
        : (r.status === 'running' || r.status === 'observed') ? 'running' : 'done';
      zones[z] += 1;
    });
    const badFlags = recs.filter((r) => !(r.demo_data === true && r.synthetic === true && r.source && r.source.kind === 'demo'));
    if (recs.length < 12) throw new Error('only ' + recs.length + ' records');
    if (types.size < 10) throw new Error('only ' + types.size + ' action types');
    if (zones.needs_me === 0 || zones.running === 0 || zones.done === 0) throw new Error('zone coverage gap: ' + JSON.stringify(zones));
    if (badFlags.length) throw new Error('records without the demo triple: ' + badFlags.map((r) => r.id).join(','));
    return 'records=' + recs.length + ' types=' + types.size + ' zones=' + JSON.stringify(zones);
  });

  check('i18n: zh/en key sets identical (100% bidirectional), no empty values', () => {
    const missingEn = zhKeys.filter((k) => enKeys.indexOf(k) < 0);
    const missingZh = enKeys.filter((k) => zhKeys.indexOf(k) < 0);
    const zhEmpty = zhKeys.filter((k) => !String(dictValues(zh)[k] || '').trim());
    const enEmpty = enKeys.filter((k) => !String(dictValues(en)[k] || '').trim());
    if (missingEn.length || missingZh.length || zhEmpty.length || enEmpty.length) {
      throw new Error('en missing=' + missingEn.join(',') + ' zh missing=' + missingZh.join(',') + ' zhEmpty=' + zhEmpty.join(',') + ' enEmpty=' + enEmpty.join(','));
    }
    return 'keys=' + zhKeys.length + ' (both directions)';
  });

  check('i18n: en dictionary has zero CJK characters, zh is Chinese', () => {
    const enVals = dictValues(en);
    const cjkEn = Object.keys(enVals).filter((k) => CJK.test(String(enVals[k])));
    const zhVals = dictValues(zh);
    const hanZh = Object.keys(zhVals).filter((k) => CJK_HAN.test(String(zhVals[k])));
    if (cjkEn.length) throw new Error('CJK in en dictionary: ' + cjkEn.join(','));
    if (hanZh.length < zhKeys.length * 0.6) throw new Error('zh dictionary is not Chinese for ' + (zhKeys.length - hanZh.length) + ' keys');
    return 'en CJK=0, zh Chinese keys=' + hanZh.length + '/' + zhKeys.length;
  });

  check('i18n: embed.js equals the canonical JSON dictionaries (no drift)', () => {
    const sandbox = makeSandbox('<html><body></body></html>');
    loadScript(sandbox, FILES.embed, 'embed.js');
    const d = sandbox.TraceLensDicts;
    if (!d) throw new Error('embed.js exposes no TraceLensDicts');
    if (!deepEqualJson(d.zh, zh)) throw new Error('zh drift between embed.js and zh.json');
    if (!deepEqualJson(d.en, en)) throw new Error('en drift between embed.js and en.json');
    return 'embed zh=' + dictKeys(d.zh).length + ' keys, en=' + dictKeys(d.en).length + ' keys';
  });

  check('no hardcoded copy: app.js / index.html / styles.css contain zero CJK', () => {
    const hits = [];
    if (CJK.test(appSource)) hits.push('app.js');
    if (CJK.test(indexSource)) hits.push('index.html');
    if (CJK.test(styleSource)) hits.push('styles.css');
    if (hits.length) throw new Error('CJK literals found in ' + hits.join(', '));
    return 'CJK in code=0; copy lives in i18n/*.json';
  });

  const zhDomains = new Set(zhKeys.map((k) => k.split('.')[0]));
  const inDictDomain = (k) => zhDomains.has(k.split('.')[0]);
  const appReferencedKeys = referencedApp.all.filter(inDictDomain);
  const referencedKeys = referenced.all.filter(inDictDomain);
  const prefixCovered = zhKeys.filter((k) => [...referenced.prefixes].some((p) => k.indexOf(p) === 0));
  const coverage = new Set(referencedKeys.concat(prefixCovered));

  check('every i18n key referenced by app.js exists in zh and en', () => {
    /* app.js only: extension sources also carry permission strings
     * ("audit.export", "settings.write") that are not dictionary keys. */
    const missing = appReferencedKeys.filter((k) => zhKeys.indexOf(k) < 0 || enKeys.indexOf(k) < 0);
    if (missing.length) throw new Error('keys referenced but not in the dictionaries: ' + missing.join(', '));
    return 'referenced=' + appReferencedKeys.length + ' (literals ' + referencedApp.literals.filter(inDictDomain).length +
      ', enum-expanded ' + referencedApp.dynamic.length + ', concat prefixes ' + referencedApp.prefixes.length + ')';
  });

  check('no orphan dictionary copy: every dictionary key is referenced', () => {
    /* Forward-looking dictionaries for a not-yet-shipped panel are the one
     * documented exception: they ship before the panel so translators work once.
     * R5-4 (task §2.1) shipped `ext/collab.js`, so `sharing.` is no longer an
     * exception: every sharing key must be referenced by real code, and the
     * panel builds no bare prefix that would fake it.
     * R4-2 (task §2.7) shipped `ext/groups.js`, so the `groups.` exception is
     * **removed**: the domain is a real asserted domain from this round on.
     * The dedicated R4-2 check below pins that every `groups.` key is
     * referenced by a quoted literal in the panel, and that the panel builds no
     * bare `groups.` prefix - so a future panel cannot re-open the exception by
     * shipping copy again. */
    /* Pre-existing trust/cards copy-deck keys that no shipped code renders yet.
     * Explicit list (not a domain wildcard) so a new orphan still fails. */
    const LEGACY_ORPHANS = new Set([
      'trust.launcher.aria', 'trust.tab.fallback', 'trust.empty.offline', 'trust.state.denied',
      'trust.state.empty', 'trust.status.request_id', 'trust.modal.title', 'trust.refresh',
      'trust.request.failed', 'trust.role.forbidden', 'trust.unit.seconds', 'trust.unit.ms',
      'trust.unit.minutes', 'trust.copy', 'trust.signature.valid', 'trust.signature.invalid',
      'trust.signature.absent', 'trust.chain.ok', 'trust.chain.broken',
      'cards.evidence.aria', 'cards.evidence.diff.summary', 'cards.evidence.vote.like',
      'cards.evidence.vote.dislike', 'cards.causal.empty',
      /* M2-PRE 2 (PM-VERDICT-R1-6 carry-over 4): the rollback family's
       * contract-side copy, one key per server code, added so
       * tools/i18n_lint.py's ERROR_DOMAINS gate can enforce
       * `<域>.error.<code>` for ROLLBACK_*. The card itself renders the
       * parallel `cards.rollback.error.<code>` deck (R1-2b, pinned by the DOM
       * self-test), so these nine keys are read by the gate, not by the page.
       * zh and en carry the same server message, code named in the copy. */
      'rollback.error.rollback_not_found', 'rollback.error.rollback_not_reversible',
      'rollback.error.rollback_dependents_pending', 'rollback.error.rollback_already_rolled_back',
      'rollback.error.rollback_inverse_failed', 'rollback.error.rollback_field_invalid',
      'rollback.error.rollback_dependents_irreversible', 'rollback.error.rollback_journal_unreadable',
      'rollback.error.rollback_idempotency_mismatch'
    ]);
    const unused = zhKeys.filter((k) => !coverage.has(k) && !LEGACY_ORPHANS.has(k));
    if (unused.length) throw new Error('dictionary keys never rendered: ' + unused.join(', '));
    return 'unreferenced keys=0 (' + zhKeys.length + ' keys covered by literals + enum/prefix expansion)';
  });

  check('secretariat panel: one tab, five zones, scoped CSS, zero CJK', () => {
    const js = extSecretariatSource;
    const css = extSecretariatCss;
    if (!/\.register\(\{/.test(js)) throw new Error('panel never registers with the extension shell');
    if (!/id:\s*'secretariat'/.test(js)) throw new Error('panel id is not secretariat');
    ['status', 'identities', 'delegations', 'letters', 'verify'].forEach((zone) => {
      if (js.indexOf("section(host, '" + zone + "')") < 0) throw new Error('missing zone: ' + zone);
    });
    if (js.indexOf('ctx.get(API') < 0) throw new Error('panel does not read through the guarded ctx.get');
    if (!/data-tab="secretariat"/.test(css)) throw new Error('panel CSS is not scoped to the secretariat tab');
    if (cjkCount(js) !== 0 || cjkCount(css) !== 0) throw new Error('panel source still contains CJK literals');
    return 'panel ' + js.length + 'B / css ' + css.length + 'B, zones=5, CJK=0';
  });

  check('runtime: no dictionary key resolves to the missing-text marker', () => {
    const { ui } = bootOnce();
    const marker = ui.t('error.missing_key').split('{')[0];
    ui.setLang('zh');
    const zhBad = zhKeys.filter((k) => {
      const v = ui.t(k);
      return !v || v.indexOf('i18n?') === 0 || (marker && v.indexOf(marker) === 0 && v.indexOf(k) >= 0);
    });
    ui.setLang('en');
    const enBad = enKeys.filter((k) => {
      const v = ui.t(k);
      return !v || v.indexOf('i18n?') === 0 || (marker && v.indexOf(marker) === 0 && v.indexOf(k) >= 0);
    });
    ui.setLang('zh');
    if (zhBad.length) throw new Error('zh keys not resolvable at runtime: ' + zhBad.slice(0, 8).join(', '));
    if (enBad.length) throw new Error('en keys not resolvable at runtime: ' + enBad.slice(0, 8).join(', '));
    const dom = ui.probe();
    if (dom.missingKeys.length) throw new Error('probe reports unresolved keys: ' + dom.missingKeys.slice(0, 8).join(', '));
    return 'runtime lookup resolves all ' + zhKeys.length + ' keys in both languages (grouped-key lookup pinned)';
  });

  check('design system: colour/spacing/font/radius/motion tokens present, motion <=200ms', () => {
    const required = ['--c-ok', '--c-progress', '--c-danger', '--c-pending', '--c-neutral',
      '--space-1', '--space-4', '--space-5', '--fs-0', '--fs-2', '--fs-4',
      '--radius-1', '--radius-2', '--radius-full', '--dur-fast', '--dur-base'];
    const missing = required.filter((token) => styleSource.indexOf(token + ':') < 0);
    if (missing.length) throw new Error('missing tokens: ' + missing.join(', '));
    const base = /--dur-base:\s*(\d+)ms/.exec(styleSource);
    const fast = /--dur-fast:\s*(\d+)ms/.exec(styleSource);
    const over = [];
    const durRe = /(?:transition|animation)[^;{}]*?(\d+(?:\.\d+)?)(ms|s)\b/g;
    let m;
    while ((m = durRe.exec(styleSource))) {
      const ms = m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1]);
      if (ms > 200) over.push(m[0]);
    }
    ['--dur-fast', '--dur-base'].forEach((token) => {
      const v = token === '--dur-fast' ? fast : base;
      if (v && Number(v[1]) > 200) over.push(token + '=' + v[1] + 'ms');
    });
    if (over.length) throw new Error('motion above 200ms: ' + over.join(' | '));
    return 'tokens=' + required.length + ', dur-fast=' + (fast ? fast[1] : '?') + 'ms, dur-base=' + (base ? base[1] : '?') + 'ms, no rule above 200ms';
  });

  check('render: three zones in order needs_me / running / done with data-zone hooks', () => {
    const { document } = bootOnce();
    const zones = byZone(document.body).filter((n) => n.classList.contains('zone'));
    const order = zones.map((n) => n.getAttribute('data-zone'));
    if (JSON.stringify(order) !== JSON.stringify(['needs_me', 'running', 'done'])) throw new Error('zone order is ' + JSON.stringify(order));
    const counts = zones.map((n) => n.getAttribute('data-count'));
    if (counts.join('/') !== '4/1/7') throw new Error('zone counts ' + counts.join('/') + ' (expected 4/1/7)');
    return 'zones=' + order.join(' > ') + ' counts=' + counts.join('/');
  });

  check('render: 12 cards rendered, needs-me is a serial queue (1 shown, 3 queued)', () => {
    const { document } = bootOnce();
    const all = cards(document.body);
    if (all.length !== 12) throw new Error('rendered cards=' + all.length);
    const needs = byZone(document.body).find((n) => n.getAttribute('data-zone') === 'needs_me');
    const priority = queryAll(needs, '.card--priority');
    const queuedBox = queryAll(needs, '.zone__queued');
    const queued = queuedBox.length ? queryAll(queuedBox[0], 'article.card') : [];
    if (priority.length !== 1) throw new Error('priority cards=' + priority.length + ' (expected exactly 1)');
    if (queued.length !== 3) throw new Error('queued cards=' + queued.length + ' (expected 3)');
    if (priority[0].getAttribute('data-id') !== 'act-demo-0004') throw new Error('first in queue is ' + priority[0].getAttribute('data-id'));
    return 'cards=12, shown=1 (' + priority[0].getAttribute('data-id') + '), queued=3';
  });

  check('colour mapping: green done / yellow running / red failed / blue awaiting you', () => {
    const { document } = bootOnce();
    const expect = {
      'act-demo-0001': 'ok',
      'act-demo-0005': 'pending',
      'act-demo-0006': 'danger',
      'act-demo-0009': 'progress',
      'act-demo-0010': 'neutral'
    };
    const bad = [];
    Object.keys(expect).forEach((id) => {
      const card = cardById(document.body, id);
      if (!card) { bad.push(id + ':missing'); return; }
      if (card.getAttribute('data-color') !== expect[id]) bad.push(id + ':' + card.getAttribute('data-color') + '!=' + expect[id]);
    });
    if (bad.length) throw new Error('colour mapping: ' + bad.join(', '));
    const cssChecks = ['data-color="ok"', 'data-color="pending"', 'data-color="danger"', 'data-color="progress"', 'data-color="neutral"'];
    const missingCss = cssChecks.filter((sel) => styleSource.indexOf('.' + 'card[' + sel + ']') < 0);
    if (missingCss.length) throw new Error('styles.css has no rule for ' + missingCss.join(', '));
    return 'records mapped + styles.css maps all five roles';
  });

  check('expand / collapse: click and keyboard both toggle aria-expanded and the detail', () => {
    /* Every action re-renders, so nodes must be re-queried after each step. */
    const { document } = bootOnce();
    const id = 'act-demo-0005';
    const open = (n) => n.getAttribute('aria-expanded') === 'true';
    let toggle = findToggle(document.body, id);
    let detail = detailOf(document.body, id);
    if (!toggle || !detail) throw new Error('card 0005 toggle/detail not found');
    if (open(toggle)) throw new Error('starts expanded');
    if (!detail.hidden) throw new Error('detail starts visible (L1 must be the default)');
    const ariaControls = toggle.getAttribute('aria-controls');

    toggle.click();
    toggle = findToggle(document.body, id);
    detail = detailOf(document.body, id);
    if (!open(toggle) || detail.hidden) throw new Error('expand after click failed');

    toggle.click();
    toggle = findToggle(document.body, id);
    detail = detailOf(document.body, id);
    if (open(toggle) || !detail.hidden) throw new Error('collapse after second click failed');

    toggle.dispatchEvent({ type: 'keydown', key: 'Enter' });
    toggle = findToggle(document.body, id);
    if (!open(toggle)) throw new Error('keyboard Enter did not expand');
    toggle.dispatchEvent({ type: 'keydown', key: ' ' });
    toggle = findToggle(document.body, id);
    if (open(toggle)) throw new Error('keyboard Space did not collapse');
    toggle.dispatchEvent({ type: 'keydown', key: 'a' });
    toggle = findToggle(document.body, id);
    if (open(toggle)) throw new Error('unrelated key toggled the card');

    /* re-expand, then re-query every node: each toggle repaints the zones, so
     * a node reference taken before the last action is already detached. */
    toggle.click();
    toggle = findToggle(document.body, id);
    detail = detailOf(document.body, id);
    const card = cardById(document.body, id);
    if (!open(toggle)) throw new Error('third toggle did not expand again');
    if (detail.hidden) throw new Error('detail still hidden after expanding');
    if (toggle.getAttribute('aria-controls') !== ariaControls) throw new Error('aria-controls changed between renders');
    if (document.getElementById(ariaControls) !== detail) {
      throw new Error('aria-controls points at ' + ariaControls + ' but the live detail node is ' +
        (detail ? detail.getAttribute('id') : 'missing') + ' (renderZones must keep aria wiring honest)');
    }
    if (!card.classList.contains('is-expanded')) throw new Error('expanded card lost its is-expanded hook');
    return 'click + Enter + Space verified, unrelated key ignored, aria-controls stable (' + ariaControls + '), card carries is-expanded';
  });

  check('detail fields: intent / rationale / human_readable / time / tool all appear', () => {
    const { ui, document } = bootOnce();
    const id = 'act-demo-0005';
    ui.setLang('zh');
    const toggle = findToggle(document.body, id);
    if (toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
    const labels = keyLabelsOf(document.body, id);
    const need = ['card.field.intent', 'card.field.rationale', 'card.field.human_l1', 'card.field.human_l2', 'card.field.human_l3', 'time.label', 'time.due', 'card.field.tool', 'card.field.risk']
      .map((k) => ui.t(k));
    const missing = need.filter((label) => labels.indexOf(label) < 0);
    if (missing.length) throw new Error('missing detail rows: ' + missing.join(', '));
    const detail = detailOf(document.body, id);
    const body = detail.textContent;
    const rec = sample.records.find((r) => r.id === id);
    [rec.human_readable.l1, rec.intent, rec.rationale, rec.human_readable.l3].forEach((fact) => {
      if (body.indexOf(fact) < 0) throw new Error('detail is missing the fact: ' + fact);
    });
    return 'rows=' + labels.length + ', facts rendered verbatim from the record';
  });

  check('language switch: zh -> en changes every data-i18n node and persists', () => {
    /* English chrome must carry no Chinese copy. Labels that interpolate
     * record content (a Chinese task name from the demo fixture) are exempt:
     * record text is t4/t7 data work, not UI copy. A node counts as
     * dictionary-rendered when it carries a non-empty data-i18n key. */
    const marked = (root) => queryAll(root, '[data-i18n]').filter((n) => !!n.getAttribute('data-i18n'));
    const enCtx = boot({ search: '?lang=en' });
    const enDoc = enCtx.document;
    if (enDoc.getElementById('sheet-title').textContent !== en.nav['sheet.title']) {
      throw new Error('sheet title not English: ' + enDoc.getElementById('sheet-title').textContent);
    }
    if (enDoc.title !== en.nav['app.name']) throw new Error('document.title not English: ' + enDoc.title);
    const nodes = marked(enDoc.body).filter((n) => n.getAttribute('data-i18n-lang') === 'en');
    if (nodes.length < 40) throw new Error('only ' + nodes.length + ' nodes marked as dictionary-rendered');
    const leaks = [];
    nodes.forEach((n) => {
      const text = stripData(n.textContent);
      const label = stripData(n.getAttribute('aria-label') || '');
      if (CJK.test(text) || CJK.test(label)) leaks.push(n.getAttribute('data-i18n') + ' -> ' + (CJK.test(text) ? text : label));
    });
    if (leaks.length) throw new Error('Chinese UI copy left in the English render: ' + leaks.slice(0, 4).join(' | '));
    const zhStamped = marked(enDoc.body).filter((n) => n.getAttribute('data-i18n-lang') === 'zh');
    if (zhStamped.length) throw new Error('stale zh stamps after the English switch: ' + zhStamped.slice(0, 4).map((n) => n.getAttribute('data-i18n')).join(','));

    const { ui, sandbox, document } = bootOnce();
    ui.setLang('en');
    const staleZh = marked(document.body).filter((n) => n.getAttribute('data-i18n-lang') === 'zh');
    if (staleZh.length) {
      throw new Error('setLang(en) left zh-rendered nodes behind: ' + staleZh.slice(0, 6).map((n) =>
        (n.getAttribute('data-i18n') || n.tagName) + '[' + (n.getAttribute('class') || '') + ']' +
        ' in #' + (((n.closest('[id]') || {}).getAttribute && n.closest('[id]').getAttribute('id')) || '?')).join(' | '));
    }
    if (sandbox.__storage.get('tracelens.ui_language') !== 'en') throw new Error('language not persisted to localStorage');
    ui.setLang('zh');
    const zoneTitle = document.getElementById('zone-title-needs_me');
    if (!CJK_HAN.test(zoneTitle.textContent)) throw new Error('switch back to zh produced no Chinese');
    if (sandbox.__storage.get('tracelens.ui_language') !== 'zh') throw new Error('zh not persisted');
    return 'en nodes=' + nodes.length + ' with 0 CJK chrome, zh/en repaint on every switch, localStorage round-trip ok';
  });

  check('every public mutation repaints and state matches the DOM', () => {
    const { ui, document } = bootOnce();
    const steps = [];
    const oneRender = (label, fn) => {
      const before = ui.state.stats.renders;
      fn();
      const delta = ui.state.stats.renders - before;
      if (delta === 0) throw new Error(label + ' changed state without repainting (renders stayed ' + before + ')');
      if (delta > 1) throw new Error(label + ' painted ' + delta + ' times: one action must be one render');
      steps.push(label);
    };
    oneRender('setLang(en)', () => ui.setLang('en'));
    oneRender('setLang(zh)', () => ui.setLang('zh'));
    oneRender('setRecords(demo)', () => ui.setRecords(sample.records));
    oneRender('setView(desktop)', () => ui.setView('desktop'));
    oneRender('setView(mobile)', () => ui.setView('mobile'));
    oneRender('setGranularity(l3)', () => ui.setGranularity('l3'));
    oneRender('setGranularity(l1)', () => ui.setGranularity('l1'));
    oneRender('openSheet(true)', () => ui.openSheet(true));
    oneRender('openSheet(false)', () => ui.openSheet(false));
    const fab = document.getElementById('fab');
    const sheet = document.getElementById('sheet');
    const open = ui.state.sheetOpen ? 'true' : 'false';
    if (fab.getAttribute('aria-expanded') !== open) throw new Error('ball aria-expanded disagrees with state.sheetOpen');
    if (sheet.getAttribute('data-open') !== open) throw new Error('sheet data-open disagrees with state.sheetOpen');
    if (document.getElementById('app').getAttribute('data-open') !== open) throw new Error('app data-open disagrees with state.sheetOpen');
    if (ui.probe().rendered.cards !== 12) throw new Error('probe cards=' + ui.probe().rendered.cards);
    return 'one render per action for ' + steps.length + ' mutations; ball / sheet / app agree with state';
  });

  check('language is restored from localStorage on a fresh boot', () => {
    const seeded = new Map([['tracelens.ui_language', 'en']]);
    const restored = boot({ search: '', storage: seeded });
    if (restored.ui.lang() !== 'en') throw new Error('stored language ignored, got ' + restored.ui.lang());
    const title = restored.document.getElementById('zone-title-needs_me');
    if (title.textContent !== en.zones['needs_me.title']) throw new Error('title not English after restore: ' + title.textContent);
    const browserDefault = boot({ search: '', language: 'en-GB' });
    if (browserDefault.ui.lang() !== 'en') throw new Error('navigator.language=en-GB did not select English');
    const zhDefault = boot({ search: '', language: 'fr-FR' });
    if (zhDefault.ui.lang() !== 'zh') throw new Error('unknown browser language should fall back to zh');
    return 'stored value wins over navigator.language; unknown browser language falls back to zh';
  });

  check('demo badge: machine-checked triple shows a persistent banner + per-card badges', () => {
    const { ui, document } = bootOnce();
    const status = ui.demoStatus(sample.records);
    if (!status.show) throw new Error('demo status is off for the demo set');
    if (status.matched.length !== 3) throw new Error('matched checks=' + JSON.stringify(status.matched));
    const banner = document.getElementById('demo-banner');
    if (banner.hidden) throw new Error('banner hidden for demo data');
    if (!CJK_HAN.test(banner.textContent) && !/[A-Za-z]/.test(banner.textContent)) throw new Error('banner has no text');
    if (document.getElementById('app').getAttribute('data-demo') !== 'true') throw new Error('data-demo not true');
    const cardBadges = queryAll(document.body, '[data-badge="demo"]');
    if (cardBadges.length !== 12) throw new Error('demo card badges=' + cardBadges.length);
    const fabBadge = queryAll(document.getElementById('fab'), '.fab__badge');
    if (fabBadge.length !== 1) throw new Error('floating ball carries no demo badge');
    return 'checks=' + status.matched.join(',') + '; banner + ball + 12 card badges';
  });

  check('demo badge: absent when the records carry none of the three flags', () => {
    const clean = JSON.parse(JSON.stringify(sample.records)).map((r) => {
      delete r.demo_data;
      delete r.synthetic;
      if (r.source) r.source.kind = 'langfuse';
      (r.labels || []).forEach(() => {});
      r.labels = ['real'];
      return r;
    });
    const { ui, document } = bootOnce();
    ui.setRecords(clean);
    const banner = document.getElementById('demo-banner');
    if (!banner.hidden) throw new Error('banner still visible for non-demo data');
    if (document.getElementById('app').getAttribute('data-demo') !== 'false') throw new Error('data-demo not false');
    if (queryAll(document.body, '[data-badge="demo"]').length !== 0) {
      const nodes = queryAll(document.body, '[data-badge="demo"]');
      throw new Error('demo badges still rendered :: show=' + ui.state.demo.show +
        ' matched=' + JSON.stringify(ui.state.demo.matched) +
        ' records=' + ui.state.records.length + ' renders=' + ui.state.stats.renders +
        ' cards=' + cards(document.body).length +
        ' sections=' + queryAll(document.body, '[data-zone]').length +
        ' perSection=' + queryAll(document.body, '[data-zone]').map((s) => s.getAttribute('data-zone') + ':' + cards(s).length).join(' ') +
        ' rec0001demo=' + JSON.stringify((ui.state.records.find((r) => r.id === 'act-demo-0001') || {}).demo_data) +
        ' doneIds=' + (ui.state.zones.done || []).map((r) => r.id).join('|') +
        ' cardDemoAttrs=' + cards(document.body).slice(0, 4).map((c) => c.getAttribute('data-demo')).join(',') +
        ' card0=' + (() => {
          const c = cards(document.body)[0];
          if (!c) return 'none';
          const attrs = Object.keys(c.attributes).filter((k) => k.indexOf('data-') === 0)
            .map((k) => k + '=' + c.attributes[k]).join(' ');
          const cid = c.getAttribute('data-id');
          const rec = ui.state.records.find((r) => r.id === cid) || {};
          return 'id=' + cid + ' recordDemoFlag=' + JSON.stringify(rec.demo_data) +
            ' recordIsDemo=' + ui.recordIsDemo(ui.state.demo, rec) +
            ' perRecord=' + JSON.stringify(ui.state.demo.perRecord[cid]) + ' attrs[' + attrs + ']';
        })() +
        ' nodes=' + nodes.map((n) => {
          const card = n.closest('article.card');
          const sec = n.closest('section[data-zone]');
          return (card ? card.getAttribute('data-id') : (n.getAttribute('class') || n.tagName)) +
            '@' + (sec ? sec.getAttribute('data-zone') : '-');
        }).join(','));
    }
    if (queryAll(document.getElementById('fab'), '.fab__badge').length !== 0) throw new Error('ball still shows a demo badge');
    ui.setRecords(sample.records);
    if (document.getElementById('demo-banner').hidden) throw new Error('demo banner did not come back');
    return 'non-demo: banner hidden, 0 badges; demo set restores them';
  });

  check('floating ball: task name + progress ring, click opens the bottom panel, Esc closes', () => {
    const { ui, document } = bootOnce();
    const fab = document.getElementById('fab');
    const label = queryAll(fab, '.fab__task')[0];
    const task = sample.records[0].context.task_name;
    if (!label || label.textContent !== task) throw new Error('ball shows ' + (label ? label.textContent : 'nothing'));
    const ring = queryAll(fab, '.fab__ring')[0];
    const style = ring.getAttribute('style') || '';
    if (style.indexOf('--progress:') < 0) throw new Error('ring has no --progress: ' + style);
    if (ring.getAttribute('data-progress') !== '58') throw new Error('progress=' + ring.getAttribute('data-progress') + ' (expected 58)');
    if (queryAll(fab, '.fab__percent')[0].textContent.indexOf('58') < 0) throw new Error('percent label missing');
    if (fab.getAttribute('tabindex') !== '0') throw new Error('ball is not keyboard reachable');
    if (!fab.getAttribute('aria-label')) throw new Error('ball has no aria-label');
    if (fab.getAttribute('aria-expanded') !== 'false') throw new Error('ball starts expanded');
    fab.click();
    if (fab.getAttribute('aria-expanded') !== 'true') {
      throw new Error('click did not open the panel :: stateSheetOpen=' + ui.state.sheetOpen +
        ' ballExpanded=' + document.getElementById('fab').getAttribute('aria-expanded') +
        ' sheetDataOpen=' + document.getElementById('sheet').getAttribute('data-open') +
        ' view=' + ui.state.view + ' renders=' + ui.state.stats.renders);
    }
    if (document.getElementById('sheet').hidden) throw new Error('aria says the panel is open but it is hidden');
    const sheet = document.getElementById('sheet');
    if (sheet.hidden || sheet.getAttribute('data-open') !== 'true') throw new Error('sheet not open');
    if (sheet.getAttribute('role') !== 'dialog' || sheet.getAttribute('aria-modal') !== 'true') throw new Error('mobile sheet is not a dialog');
    document.dispatchEvent({ type: 'keydown', key: 'Escape' });
    if (fab.getAttribute('aria-expanded') !== 'false' || !sheet.hidden) throw new Error('Escape did not close the panel');
    const close = document.getElementById('sheet-close');
    fab.click();
    close.click();
    if (fab.getAttribute('aria-expanded') !== 'false') throw new Error('close button did not close the panel');
    return 'task label + ring(58%) + click/Esc/close-button verified';
  });

  check('3.2 hooks: layout container, data-view, sidebar / zones / timeline / compare', () => {
    const { ui, document } = bootOnce();
    const app = document.getElementById('app');
    if (ui.viewForWidth(375) !== 'mobile' || ui.viewForWidth(700) !== 'tablet' || ui.viewForWidth(1440) !== 'desktop') throw new Error('breakpoints wrong');
    if (app.getAttribute('data-view') !== 'mobile') throw new Error('mobile view not applied at 375px');
    if (!document.getElementById('sidebar') || !document.getElementById('timeline') || !document.getElementById('compare')) throw new Error('layout hooks missing in index.html');
    if (document.getElementById('zones').getAttribute('data-hook') !== 'zones') throw new Error('zones hook missing');
    ui.setView('desktop');
    if (app.getAttribute('data-view') !== 'desktop') throw new Error('data-view did not follow setView(desktop)');
    if (document.getElementById('sidebar').getAttribute('aria-hidden') !== 'false') throw new Error('sidebar still hidden on desktop');
    if (document.getElementById('timeline').getAttribute('aria-hidden') !== 'false') throw new Error('timeline still hidden on desktop');
    if (!queryAll(document.getElementById('sidebar'), '[data-hook^="nav-"]').length) throw new Error('sidebar nav hooks missing');
    if (!queryAll(document.getElementById('timeline'), '[data-hook="timeline-items"]').length) throw new Error('timeline item hook missing');
    if (!queryAll(document.body, '[data-hook="intervene"]').length) throw new Error('intervention hook container missing');
    if (!queryAll(document.body, '[data-hook="intervene-button"]').length) throw new Error('intervention button hook missing');
    const buttons = queryAll(document.body, '[data-hook="intervene-button"]');
    /* t3.2 shipped the panel as a disabled skeleton; t4.1 turns the three
     * decisions on, and the honesty rule flips to: a button is enabled exactly
     * where the record is waiting for a decision, nowhere else. */
    if (!buttons.length) throw new Error('no intervention buttons rendered');
    const boxes = queryAll(document.body, '[data-hook="intervene"]');
    const decidableByCard = new Map();
    boxes.forEach((b) => decidableByCard.set(b.getAttribute('data-card'), b.getAttribute('data-decidable') === 'true'));
    const decidableCards = boxes.filter((b) => b.getAttribute('data-decidable') === 'true');
    if (!decidableCards.length) throw new Error('no record is offered for a decision');
    buttons.forEach((b) => {
      const card = b.getAttribute('data-card');
      const wants = decidableByCard.get(card) === true;
      const enabled = b.getAttribute('data-enabled') === 'true' && !b.disabled;
      if (wants !== enabled) {
        throw new Error('button enabled=' + enabled + ' but record decidable=' + wants +
          ' on ' + (card || '?') + '/' + (b.getAttribute('data-kind') || '?'));
      }
    });
    const kinds = decidableCards[0] ? queryAll(decidableCards[0], '[data-hook="intervene-button"]')
      .map((b) => b.getAttribute('data-kind')) : [];
    if (kinds.join(',') !== 'approve,modify,reject') throw new Error('the three decisions are not all offered: ' + kinds.join(','));
    if (styleSource.indexOf('min-width: 1024px') < 0) throw new Error('no desktop breakpoint in styles.css');
    ui.setView('mobile');
    return 'hooks present: app[data-view], sidebar, zones, timeline, compare, intervene; ' +
      decidableCards.length + ' decidable boxes with 3 enabled buttons each, ' +
      (buttons.length - queryAll(document.body, '[data-hook="intervene-button"][data-enabled="true"]').length) +
      ' buttons disabled for records that are not waiting';
  });

  check('accessibility: tabindex, aria-label, aria-expanded/controls, unique ids', () => {
    const { document } = bootOnce();
    const problems = [];
    queryAll(document.body, 'button').forEach((b) => {
      const label = (b.getAttribute('aria-label') || '') + b.textContent;
      if (!label.trim()) problems.push('button without label: ' + (b.getAttribute('class') || b.tagName));
      if (b.getAttribute('tabindex') === '-1') problems.push('button unfocusable: ' + (b.getAttribute('class') || ''));
    });
    queryAll(document.body, 'article.card').forEach((c) => {
      if (c.getAttribute('tabindex') !== '0') problems.push('card not focusable: ' + c.getAttribute('data-id'));
      if (!c.getAttribute('aria-label')) problems.push('card without aria-label: ' + c.getAttribute('data-id'));
    });
    queryAll(document.body, '[aria-controls]').forEach((n) => {
      const id = n.getAttribute('aria-controls');
      if (!document.getElementById(id)) problems.push('aria-controls points at nothing: ' + id);
    });
    const ids = new Map();
    allNodes(document.body).forEach((n) => {
      const id = n.getAttribute && n.getAttribute('id');
      if (!id) return;
      ids.set(id, (ids.get(id) || 0) + 1);
    });
    [...ids.entries()].filter(([, n]) => n > 1).forEach(([id, n]) => problems.push('duplicate id ' + id + ' x' + n));
    if (problems.length) throw new Error(problems.slice(0, 8).join(' | '));
    const labelled = queryAll(document.body, '[aria-label]').length;
    return 'buttons labelled, 12 focusable cards, aria-controls resolved, ' + ids.size + ' unique ids, aria-label nodes=' + labelled;
  });

  check('L1 default: cards show one line, cost/duration only inside the detail', () => {
    const { document } = bootOnce();
    const rec = sample.records.find((r) => r.id === 'act-demo-0001');
    const card = cardById(document.body, 'act-demo-0001');
    const l1 = queryAll(card, '.card__l1')[0].textContent;
    if (l1 !== rec.human_readable.l1) throw new Error('L1 text differs from the record');
    const detail = detailOf(document.body, 'act-demo-0001');
    const headText = queryAll(card, '.card__toggle')[0].textContent + queryAll(card, '.card__meta')[0].textContent;
    const cost = String(rec.cost.amount);
    if (headText.indexOf(cost) >= 0) throw new Error('cost visible before expansion');
    if (detail.textContent.indexOf(cost) < 0) throw new Error('cost missing after expansion');
    if (detail.textContent.indexOf(rec.human_readable.l2[0]) < 0) throw new Error('L2 steps missing after expansion');
    return 'L1 verbatim; cost ' + cost + ' only inside the detail; L2 steps rendered';
  });

  check('honesty: decision vs effect kept apart, no forced-stop wording', () => {
    const { ui, document } = bootOnce();
    ui.setLang('zh');
    const text = document.body.textContent;
    const banned = ['\u5DF2\u5F3A\u5236\u505C\u6B62', '\u5DF2\u505C\u6B62 AI', '\u5DF2\u6267\u884C\u5B8C\u6210'];
    const hits = banned.filter((w) => text.indexOf(w) >= 0);
    if (hits.length) throw new Error('forbidden wording present: ' + hits.join(', '));
    const effect = ui.t('intervene.effect.recorded_only');
    if (text.indexOf(effect) < 0) {
      const boxes = queryAll(document.body, '.options');
      const notes = queryAll(document.body, '.note--effect');
      throw new Error('effect notice not shown :: lang=' + ui.lang() + ' renders=' + ui.state.stats.renders +
        ' effect=' + JSON.stringify(effect) + ' noteTexts=' + notes.slice(0, 2).map((n) => JSON.stringify(n.textContent)).join(',') +
        ' optionsBoxes=' + boxes.length + ' effectNotes=' + notes.length);
    }
    const receipt = detailOf(document.body, 'act-demo-0010');
    if (!receipt) throw new Error('act-demo-0010 detail missing');
    const row = queryAll(receipt, '.next__receipt')[0];
    if (!row || row.getAttribute('data-receipt-state') !== 'refused_by_runtime') throw new Error('receipt state not surfaced on 0010');
    const unreported = detailOf(document.body, 'act-demo-0005');
    const rowU = queryAll(unreported, '.next__receipt')[0];
    if (rowU && rowU.getAttribute('data-receipt-state') !== 'unreported') throw new Error('unreported state tampered');
    if (ui.t('intervene.receipt.unreported').indexOf('\u672A\u56DE\u62A5') < 0) throw new Error('unreported label must say "not reported"');
    return 'no forced-stop copy; effect notice shown; receipt states surfaced verbatim';
  });

  check('offline purity: no network, no backend calls, no secrets in the web layer', () => {
    /* demo-data.js is data, not code: record 0008 legitimately carries a
     * synthetic external endpoint for the UI to display. The rule applies to
     * the code that runs (app.js, index.html).
     * t4.1 loosens exactly one thing: the page may now reach the local
     * sidecar over the *same origin*. "Same origin" is enforced here as:
     *  - no scheme+host literal anywhere (the /https?:\/\// ban stays),
     *  - no leading-slash API path (the old /\/api\// ban, rewritten so the
     *    relative form is allowed and the absolute form is not),
     *  - endpoints come from one allowlist (API_ENDPOINTS), all relative,
     *  - exactly two fetch() calls: the i18n refresh and the single guarded
     *    apiRequest, and apiRequest is only called with allowlist keys,
     *  - over file:// the request never leaves the page: the local
     *    simulation path (checked by the t4.1 intervention checks). */
    const scanned = appSource + indexSource;
    const bannedPatterns = [
      ['XMLHttpRequest', /XMLHttpRequest/],
      ['remote url', /https?:\/\//],
      ['absolute api path', /['"]\/(api\/|intervene|health)/],
      ['secret value', /sk-[A-Za-z0-9]{8,}/],
      ['token assignment', /api[_-]?key\s*[:=]\s*['"]/i],
      ['websocket', /new WebSocket/],
      ['event source', /new EventSource/],
      ['import statement', /^\s*import\s/m],
      ['module script', /type=["']module["']/]
    ];
    const hits = bannedPatterns.filter(([, re]) => re.test(scanned)).map(([name]) => name);
    if (hits.length) throw new Error('forbidden in web layer: ' + hits.join(', '));
    const fetchUses = (appSource.match(/fetch\(/g) || []).length;
    if (fetchUses !== 2) throw new Error('expected two fetch() (i18n refresh + guarded apiRequest), found ' + fetchUses);
    if (appSource.indexOf("fetch('i18n/'") < 0 && appSource.indexOf('fetch("i18n/"') < 0) throw new Error('the first fetch must be the i18n refresh');
    if (!/return global\.fetch\(path, init\)/.test(appSource)) throw new Error('the second fetch must be the guarded apiRequest call');
    const block = /var API_ENDPOINTS = \{([\s\S]*?)\};/.exec(appSource);
    if (!block) throw new Error('the API endpoint allowlist is missing');
    const paths = [...block[1].matchAll(/:\s*'([a-z/_-]+)'/g)].map((m) => m[1]);
    /* 4.3 grew the allowlist by exactly the auth + user administration
     * endpoints; 4.4 (t14) adds the realtime contract surface; 4.5 (t15) adds
     * the settings centre (read + PUT), its connection test and the documented
     * reset route; 4.8 (t21) adds the audit read surface and its export route
     * (both GET, gated server-side by audit.read / audit.export); 4.9 (t22) adds
     * the single import route (POST only, gated server-side by data.write + CSRF).
     * Every entry stays a relative same-origin path: no host, no scheme, no
     * wildcard. api/stream is read by the injectable EventSource (checked below),
     * api/ingest/* is the documented ingest surface the page may talk to. */
    const allowedPaths = ['health', 'api/records', 'intervene',
      'api/auth/status', 'api/auth/permissions', 'api/auth/login', 'api/auth/logout',
      'api/auth/me', 'api/auth/password', 'api/auth/check', 'api/users',
      'api/stream', 'api/ingest/status', 'api/ingest/events', 'api/ingest/tokens',
      'api/settings', 'api/settings/test-llm', 'api/reset',
      'api/audit', 'api/audit/export', 'api/import',
      'api/secretariat', 'api/rollback', 'api/evidence', 'api/groups', 'api/shares',
      'api/analytics', 'api/suggestions', 'api/preferences', 'api/compliance'].join(',');
    if (paths.join(',') !== allowedPaths) throw new Error('endpoint allowlist changed: ' + paths.join(','));
    if (paths.some((p) => p.startsWith('/') || /:\/\//.test(p))) throw new Error('endpoints must stay relative same-origin paths');
    const calls = [...appSource.matchAll(/apiRequest\('([a-zA-Z]+)'/g)].map((m) => m[1]);
    /* 4.3 adds "users": the user page talks to the allowlisted api/users
     * endpoint, and the only sub-path form is the one subPathOf admits.
     * 4.5 adds "settings" (masked read + PUT) and "reset" (data management):
     * both go through the same guarded apiRequest, never a raw fetch.
     * 4.8 adds "audit" (audit list, read through authFetch + the cookie) and
     * "auditExport" (the file download, response:'text' on the same door).
     * 4.9 adds "import": the paste/upload POST goes through authFetch (cookie +
     * CSRF header) into the same guarded apiRequest - never a raw fetch, never a
     * second upload channel for a picked file. */
    const allowedNames = ['health', 'records', 'intervene', 'users', 'settings', 'settingsTestLlm',
      'reset', 'audit', 'auditExport', 'import'];
    const outside = calls.filter((c) => allowedNames.indexOf(c) < 0);
    if (outside.length) throw new Error('apiRequest called outside the allowlist: ' + outside.join(','));
    /* 4.9 (t22): the import door is the allowlisted name, and it is the only
     * POST the page sends with a body over 4 KiB (the server caps a batch at
     * 64 KiB / 200 records, apiRequest refuses anything larger). */
    if (!/authFetch\('import',\s*body,\s*\{\s*method:\s*'POST'\s*\}/.test(appSource)) {
      throw new Error('the import must go through authFetch("import", body, { method: POST })');
    }
    if (!/credentials:\s*'omit'/.test(appSource)) throw new Error('the API request must not send credentials');
    /* 4.3: cookies are the one credential path the page may add, and only for
     * the auth endpoints - same-origin, never a host, never a bearer string. */
    if (!/credentials:\s*'same-origin'/.test(appSource)) throw new Error('auth requests must send same-origin credentials');
    if (/credentials:\s*'include'/.test(appSource)) throw new Error('credentials include is not allowed');
    if (/localStorage\.[a-zA-Z]+\(\s*['"][^'"]*(token|password|csrf)/i.test(appSource)) {
      throw new Error('session credentials must not be persisted in localStorage');
    }
    /* 4.4 (t14): server push is the one extra channel, and it stays narrow: the
     * constructor is injectable (the rig injects a fake, so no check ever opens
     * a socket), the URL is built from the api/stream allowlist entry alone, a
     * WebSocket stays banned, and app.js never hardcodes a browser constructor. */
    if (!/state\.rt\.factory \|\| global\.EventSource/.test(appSource)) {
      throw new Error('the stream constructor must be state.rt.factory falling back to global.EventSource');
    }
    if (/new EventSource\(/.test(appSource)) throw new Error('app.js must not hardcode a browser EventSource');
    if (!/function rtStreamUrl\(\) \{[\s\S]*?apiEndpoint\('stream'\)/.test(appSource)) {
      throw new Error('the stream URL must come from the endpoint allowlist');
    }
    if (/EventSource\([^)]*\/\//.test(appSource)) throw new Error('the stream URL must stay relative (no scheme)');
    if (!/function startPolling\(reason\)/.test(appSource) || !/rtSet\('poll', reason\)/.test(appSource)) {
      throw new Error('the polling fallback must be a named, labelled path, not a silent retry');
    }
    return 'no XHR/WebSocket/EventSource/module import/remote URL/absolute API path/secret; ' +
      paths.length + ' relative endpoints, 2 guarded fetch (' + calls.join('+') + '), credentials omitted, ' +
      'stream via injected constructor + allowlist URL, labelled polling fallback';
  });

  check('probe(): structural self-description for headless verification', () => {
    const { ui } = bootOnce();
    const p = ui.probe();
    const need = ['view', 'lang', 'records', 'zones', 'progress', 'demoBadge', 'demoMatched', 'missingKeys', 'stats', 'rendered', 'layout'];
    const missing = need.filter((k) => !(k in p));
    if (missing.length) throw new Error('probe is missing ' + missing.join(', '));
    if (p.missingKeys.length) throw new Error('unresolved i18n keys: ' + p.missingKeys.join(', '));
    if (p.records !== 12) throw new Error('probe records=' + p.records);
    if (p.zones.needs_me !== 4 || p.zones.running !== 1 || p.zones.done !== 7) throw new Error('probe zones=' + JSON.stringify(p.zones));
    if (p.demoBadge !== true) throw new Error('probe demoBadge=false');
    if (p.rendered.zones < 3) throw new Error('probe sees ' + p.rendered.zones + ' zones');
    return JSON.stringify({ zones: p.zones, progress: p.progress.percent, demoBadge: p.demoBadge, i18nNodes: p.rendered.i18nNodes, missingKeys: p.missingKeys.length });
  });

  check('query parameters: ?lang=en&open=1&?view=desktop honoured', () => {
    const en = boot({ search: '?lang=en&open=1&probe=1' });
    if (en.ui.lang() !== 'en') throw new Error('lang parameter ignored');
    if (en.ui.state.sheetOpen !== true) throw new Error('open parameter ignored');
    if (en.document.getElementById('sheet').hidden) throw new Error('sheet hidden with open=1');
    const desk = boot({ search: '?view=desktop', innerWidth: 1440, innerHeight: 900 });
    if (desk.document.getElementById('app').getAttribute('data-view') !== 'desktop') throw new Error('view parameter ignored');
    return '?lang / ?open / ?view work (used by t10-t11 walkthroughs)';
  });

  check('static page boot: index.html boots the app on its own (file:// demo path)', () => {
    const sandbox = makeSandbox(read(FILES.index), { search: '?open=1', readyState: 'loading' });
    sandbox.TraceLensDicts = { zh: readJson(FILES.zh), en: readJson(FILES.en) };
    loadPageScripts(sandbox);
    const ui = sandbox.TraceLensUI;
    const doc = sandbox.document;
    if (!ui) throw new Error('app.js did not expose TraceLensUI');
    if (ui.state.stats.renders !== 0) throw new Error('the app painted before DOMContentLoaded (double init)');
    doc.dispatchEvent({ type: 'DOMContentLoaded' });
    const p = ui.probe();
    if (p.stats.renders === 0) throw new Error('DOMContentLoaded did not boot the app: opening index.html shows an empty page');
    if (p.records !== 12) throw new Error('page boot loaded ' + p.records + ' records');
    if (p.rendered.cards !== 12 || queryAll(doc.body, 'article.card').length !== 12) throw new Error('page boot rendered ' + p.rendered.cards + ' cards');
    if (p.missingKeys.length) throw new Error('page boot has missing keys: ' + p.missingKeys.join(', '));
    if (p.sheetOpen !== true) throw new Error('?open=1 ignored on the page boot path');
    const fab = doc.getElementById('fab');
    if (fab.hidden) throw new Error('ball disappeared on the page boot path');
    if (fab.getAttribute('aria-expanded') !== 'true') throw new Error('ball does not describe the open sheet');
    if ((fab.getAttribute('class') || '').indexOf('is-open') < 0) throw new Error('ball lacks the is-open state');
    fab.click();
    if (ui.state.sheetOpen !== false) throw new Error('one click did not close the sheet (init registered twice)');
    return 'self-boot verified: DOMContentLoaded -> renders=' + p.stats.renders + ', cards=12, no double init';
  });

  check('verification rig: ?probe=1 report and ?frame=375x812 phone viewport', () => {
    /* a normal open never shows the rig */
    const plain = bootOnce();
    const plainText = plain.document.getElementById('probe-text');
    if (!plainText.hidden) throw new Error('probe report visible without a rig parameter');
    if (plain.document.getElementById('app').hasAttribute('data-frame')) throw new Error('frame attribute set without ?frame');

    const rig = boot({ search: '?probe=1&open=1&frame=375x812' });
    const app = rig.document.getElementById('app');
    if (app.getAttribute('data-frame') !== '375x812') throw new Error('data-frame not set: ' + app.getAttribute('data-frame'));
    if (!/--frame-w:\s*375px/.test(app.getAttribute('style') || '')) throw new Error('frame width not pinned: ' + app.getAttribute('style'));
    if (rig.ui.state.view !== 'mobile') throw new Error('frame rig must behave like the phone layout, got ' + rig.ui.state.view);
    if (rig.ui.state.frame.width !== 375 || rig.ui.state.frame.height !== 812) throw new Error('state.frame not recorded');
    const text = rig.document.getElementById('probe-text');
    if (text.hidden) throw new Error('probe report hidden with probe=1');
    const body = text.textContent;
    ['probe @ 375x812', 'dictKeys=' + zhKeys.length, 'zones needs_me=4 running=1 done=7', 'cards=12', 'demoBadges=12', 'progress=58%', 'i18nNodes=', 'missingKeys=0', 'PROBE: n/a (no layout metrics in this DOM)'].forEach((needle) => {
      if (body.indexOf(needle) < 0) throw new Error('probe report lacks "' + needle + '": ' + body);
    });
    if (text.getAttribute('data-rig-error') !== 'none') throw new Error('render step threw in the rig: ' + text.getAttribute('data-rig-error'));
    const layout = rig.ui.probe().layout;
    if (!layout || !layout.rig || layout.rig.box !== '375x812') throw new Error('probe().layout.rig missing for the frame mode');
    const rigWide = boot({ search: '?probe=1&frame=375x812', innerWidth: 1440, innerHeight: 900 });
    if (rigWide.ui.state.view !== 'mobile') throw new Error('the frame rig ignored its pinned viewport at window width 1440, got ' + rigWide.ui.state.view);
    if (text.getAttribute('data-probe-rig') !== '375x812') throw new Error('probe report has no rig marker');

    /* the CSS must contain the rig rules the app relies on */
    const rigCss = styleSource.slice(styleSource.indexOf('13. verification rig'));
    if (rigCss.indexOf('verification rig') < 0) throw new Error('styles.css has no verification rig section');
    ['.app[data-frame]', 'display: block', 'width: var(--frame-w', 'height: var(--frame-h',
      '.app[data-frame][data-frame-layout="mobile"] .sidebar', '.app[data-frame][data-frame-layout="mobile"] .rail',
      '.app[data-frame][data-frame-layout="mobile"] .compare',
      '.app[data-frame][data-frame-layout="mobile"] .fab', 'display: inline-flex',
      '.app[data-frame][data-frame-layout="mobile"] .sheet',
      '.app[data-frame][data-frame-layout="tablet"]',
      '.app[data-frame][data-frame-layout="desktop"]', 'grid-template-columns: var(--sidebar-w',
      '.app[data-frame][data-frame-layout="desktop"][data-compare="on"] .compare',
      '.probe'].forEach((needle) => {
      if (rigCss.indexOf(needle) < 0) throw new Error('styles.css lacks the rig rule: ' + needle);
    });
    return 'rig off by default; frame pins 375x812 + phone view; report text=' + body.split('\n').length + ' lines; probe().layout.rig pinned';
  });

  check('3.2 desktop sidebar: newest-first list, click focus, status filter collapses zones', () => {
    const b = boot({ search: '?view=desktop', innerWidth: 1440, innerHeight: 900 });
    const { ui, document: doc } = b;
    if (ui.state.view !== 'desktop') throw new Error('?view=desktop did not switch the view: ' + ui.state.view);
    const list = queryAll(doc.body, 'ol.sidebar__list')[0];
    if (!list) throw new Error('no reverse-chronological list in the sidebar');
    const items = queryAll(list, 'li.sidebar__item');
    if (items.length !== 12) throw new Error('sidebar list shows ' + items.length + ' of 12 actions');
    if (list.getAttribute('data-total') !== '12') throw new Error('sidebar list carries no total');
    const times = items.map((n) => Number(n.getAttribute('data-time')));
    for (let i = 1; i < times.length; i += 1) {
      if (times[i] > times[i - 1]) throw new Error('sidebar is not newest-first at row ' + i);
    }
    const ids = items.map((n) => n.getAttribute('data-id'));
    if (ids[ids.length - 1] !== 'act-demo-0001') throw new Error('the oldest step is not last: ' + ids[ids.length - 1]);
    const buttons = queryAll(list, 'button[data-hook="sidebar-item"]');
    if (buttons.length !== 12) throw new Error('sidebar rows without a button: ' + buttons.length);
    if (buttons.some((x) => !x.getAttribute('aria-label'))) throw new Error('a sidebar row has no accessible name');
    const covered = new Set(buttons.map((x) => x.getAttribute('data-card')));
    if (covered.size !== 12) throw new Error('sidebar rows cover ' + covered.size + ' distinct records');

    const focusId = buttons[2].getAttribute('data-card');
    const rendersBefore = ui.state.stats.renders;
    buttons[2].click();
    if (ui.state.focus !== focusId) throw new Error('click did not focus ' + focusId);
    if (ui.state.stats.renders - rendersBefore !== 1) throw new Error('one click cost ' + (ui.state.stats.renders - rendersBefore) + ' renders');
    const card = cardById(doc.body, focusId);
    if (!card || (card.getAttribute('class') || '').indexOf('is-focused') < 0) throw new Error('the matching card did not highlight');
    if (queryAll(doc.body, 'article.card.is-focused').length !== 1) throw new Error('more than one card highlighted');
    if (queryAll(doc.body, 'li.sidebar__item.is-focused').length !== 1) throw new Error('the list row did not highlight');
    if (ui.state.timelineIndex !== ui.state.records.findIndex((r) => r.id === focusId)) throw new Error('the timeline did not follow the list');
    const note = queryAll(doc.body, '[data-hook="focus-note"]')[0];
    if (!note || note.getAttribute('data-id') !== focusId) throw new Error('no focus note for the selected action');

    const filterBtns = queryAll(doc.body, 'button[data-hook="filter-zone"]');
    if (filterBtns.length !== 3) throw new Error('status filter has ' + filterBtns.length + ' buttons');
    const done = filterBtns.find((x) => x.getAttribute('data-zone') === 'done');
    if (done.getAttribute('aria-pressed') !== 'true') throw new Error('a zone filter is not pressed at boot');
    if (done.getAttribute('data-count') !== '7') throw new Error('done filter count = ' + done.getAttribute('data-count'));
    if (!done.getAttribute('aria-label')) throw new Error('a filter button has no accessible name');
    const rendersFilter = ui.state.stats.renders;
    done.click();
    if (ui.state.filter.done !== false) throw new Error('click did not switch the zone off');
    if (ui.state.stats.renders - rendersFilter !== 1) throw new Error('filter toggle cost ' + (ui.state.stats.renders - rendersFilter) + ' renders');
    const doneZone = byZone(doc.body).find((z) => z.getAttribute('data-zone') === 'done');
    if (doneZone.getAttribute('data-filtered') !== 'true') throw new Error('the hidden zone is not marked as filtered');
    if (queryAll(doneZone, 'article.card').length !== 0) throw new Error('a zone that is switched off still shows cards');
    if (queryAll(doc.body, 'article.card').length !== 5) throw new Error('after hiding done: ' + queryAll(doc.body, 'article.card').length + ' cards');
    const list2 = queryAll(doc.body, 'ol.sidebar__list')[0];
    if (list2.getAttribute('data-total') !== '12') throw new Error('the sidebar total changed with the filter');
    if (list2.getAttribute('data-shown') !== '5') throw new Error('sidebar shown = ' + list2.getAttribute('data-shown'));
    const notice = queryAll(doc.body, '[data-hook="filter-hidden"]')[0];
    if (!notice || notice.getAttribute('data-count') !== '7') throw new Error('no honest notice about the hidden actions');
    queryAll(doc.body, 'button[data-hook="filter-zone"]').find((x) => x.getAttribute('data-zone') === 'done').click();
    if (queryAll(doc.body, 'article.card').length !== 12) throw new Error('restoring the zone did not bring the 12 cards back');

    for (let i = 0; i < 3; i += 1) {
      const onBtn = queryAll(doc.body, 'button[data-hook="filter-zone"]').find((x) => x.getAttribute('aria-pressed') === 'true');
      if (onBtn) onBtn.click();
    }
    if (queryAll(doc.body, 'article.card').length !== 0) throw new Error('switching every zone off still shows cards');
    if (!queryAll(doc.body, '[data-hook="filter-all-off"]').length) throw new Error('no notice when every zone is off');
    if (queryAll(doc.body, 'li.sidebar__item').length !== 0) throw new Error('the sidebar list ignores the filter');
    const allOff = ui.filterState();
    if (Object.keys(allOff).some((k) => allOff[k] !== false)) throw new Error('filterState after turning all off: ' + JSON.stringify(allOff));
    ui.setFilter({ needs_me: true, running: true, done: true });
    if (queryAll(doc.body, 'article.card').length !== 12) throw new Error('setFilter(all) did not restore everything');
    const stored = JSON.parse(b.sandbox.localStorage.getItem('tracelens.filter.v1'));
    if (stored.done !== true) throw new Error('the filter was not written to localStorage');
    const seeded = boot({ search: '?view=desktop', innerWidth: 1440, innerHeight: 900, storage: new Map([['tracelens.filter.v1', '{"needs_me":true,"running":true,"done":false}']]) });
    if (seeded.ui.filterState().done !== false) throw new Error('a stored filter was not restored on the next open');
    if (queryAll(seeded.document.body, 'article.card').length !== 5) throw new Error('the restored filter did not apply to the list');
    const urlFilter = boot({ search: '?view=desktop&filter=needs_me,running', innerWidth: 1440, innerHeight: 900 });
    if (urlFilter.ui.filterState().done !== false) throw new Error('?filter=needs_me,running ignored');
    const urlAll = boot({ search: '?view=desktop&filter=needs_me', innerWidth: 1440, innerHeight: 900 });
    if (urlAll.ui.filterState().running !== false || urlAll.ui.filterState().done !== false) throw new Error('?filter=needs_me ignored');
    return 'list=12 newest-first, click->focus (1 render), filter hides 7 / restores 12, all-off notice, storage + ?filter restore';
  });

  check('3.2 timeline: draggable scrubber previews each step and drives list + card highlight', () => {
    const b = boot({ search: '?view=desktop', innerWidth: 1440, innerHeight: 900 });
    const { ui, document: doc } = b;
    const main = doc.getElementById('timeline-main');
    if (!main) throw new Error('the main area has no timeline strip');
    if (main.getAttribute('aria-hidden') !== 'false') throw new Error('the timeline strip is hidden in the desktop view');
    const scrub = queryAll(main, 'input[type="range"][data-hook="timeline-scrub"]')[0];
    if (!scrub) throw new Error('no range control to drag');
    if (scrub.getAttribute('min') !== '0' || scrub.getAttribute('max') !== '11' || scrub.getAttribute('step') !== '1') {
      throw new Error('scrub range attributes ' + scrub.getAttribute('min') + '..' + scrub.getAttribute('max') + ' step ' + scrub.getAttribute('step'));
    }
    if (!scrub.getAttribute('aria-valuetext') || !scrub.getAttribute('aria-label')) throw new Error('the scrubber has no accessible name/value');
    const ticks = queryAll(doc.body, 'li.timeline__tick');
    if (ticks.length !== 12) throw new Error('timeline ticks: ' + ticks.length);
    const peek = queryAll(doc.body, '[data-hook="timeline-peek"]')[0];
    if (peek.getAttribute('data-index') !== '0' || peek.getAttribute('data-id') !== ui.state.records[0].id) {
      throw new Error('the preview does not start at the first step: ' + peek.getAttribute('data-id'));
    }
    const firstL1 = String(ui.state.records[0].human_readable.l1);
    if (peek.textContent.indexOf(firstL1) < 0) throw new Error('the preview is not the record text');

    const before = ui.state.stats.renders;
    scrub.value = '3';
    main.dispatchEvent({ type: 'input', target: scrub });
    if (ui.state.timelineIndex !== 3) throw new Error('dragging to the 4th step -> index ' + ui.state.timelineIndex);
    if (ui.state.stats.renders - before !== 1) throw new Error('one drag step cost ' + (ui.state.stats.renders - before) + ' renders');
    const target3 = ui.state.records[3];
    const peek2 = queryAll(doc.body, '[data-hook="timeline-peek"]')[0];
    if (peek2.getAttribute('data-index') !== '3' || peek2.getAttribute('data-id') !== target3.id) throw new Error('the preview did not follow the drag');
    if (peek2.textContent.indexOf(String(target3.human_readable.l1)) < 0) throw new Error('preview text is not the dragged record');
    if (peek2.getAttribute('data-color') !== ui.colorRoleOf(target3)) throw new Error('the preview lost the colour role');
    const selected = queryAll(doc.body, 'li.timeline__tick.is-selected');
    if (selected.length !== 1 || selected[0].getAttribute('data-index') !== '3') throw new Error('the tick list did not highlight the dragged step');
    const focused = queryAll(doc.body, 'article.card.is-focused');
    if (focused.length !== 1 || focused[0].getAttribute('data-id') !== target3.id) throw new Error('the card list did not follow the drag');
    const scrub2 = queryAll(main, 'input[type="range"][data-hook="timeline-scrub"]')[0];
    if (scrub2.getAttribute('data-index') !== '3') throw new Error('the scrubber did not report its index: ' + scrub2.getAttribute('data-index'));
    if (scrub2.getAttribute('data-id') !== target3.id) throw new Error('the scrubber did not report the step it sits on');

    const ticks2 = queryAll(doc.body, 'button[data-hook="timeline-tick"]');
    ticks2[11].click();
    if (ui.state.timelineIndex !== 11) throw new Error('clicking the last tick -> index ' + ui.state.timelineIndex);
    scrub.value = '99';
    main.dispatchEvent({ type: 'change', target: scrub });
    if (ui.state.timelineIndex !== 11) throw new Error('an out-of-range value was not clamped');
    scrub.value = '-4';
    main.dispatchEvent({ type: 'input', target: scrub });
    if (ui.state.timelineIndex !== 0) throw new Error('a negative value was not clamped');

    const stepped = boot({ search: '?view=desktop&step=3', innerWidth: 1440, innerHeight: 900 });
    if (stepped.ui.state.timelineIndex !== 2) throw new Error('?step=3 -> index ' + stepped.ui.state.timelineIndex);
    const steppedPeek = queryAll(stepped.document.body, '[data-hook="timeline-peek"]')[0];
    if (steppedPeek.getAttribute('data-index') !== '2') throw new Error('?step=3 preview is not on step 3');
    if (stepped.ui.state.focus !== stepped.ui.state.records[2].id) throw new Error('?step=3 did not focus that action');
    const focusedBoot = boot({ search: '?view=desktop&focus=act-demo-0006', innerWidth: 1440, innerHeight: 900 });
    if (focusedBoot.ui.state.focus !== 'act-demo-0006') throw new Error('?focus was ignored');
    if (focusedBoot.ui.state.timelineIndex !== focusedBoot.ui.state.records.findIndex((r) => r.id === 'act-demo-0006')) {
      throw new Error('?focus did not move the scrubber');
    }
    return 'scrub 0..11, drag -> preview + ticks + card in sync, clamped, 1 render/step, ?step and ?focus jump to the step';
  });

  check('3.2 compare: two runs side by side, at least three highlighted difference classes', () => {
    const b = boot({ search: '?view=desktop&compare=on', innerWidth: 1440, innerHeight: 900 });
    const { ui, document: doc } = b;
    const app = doc.getElementById('app');
    const panel = doc.getElementById('compare');
    if (ui.compareOpen() !== true) throw new Error('?compare=on did not open the compare view');
    if (app.getAttribute('data-compare') !== 'on') throw new Error('data-compare not set on #app');
    const runs = ui.runsOf();
    if (runs.length !== 2) throw new Error('runs available: ' + runs.length + ' (is compare-data.js loaded?)');
    if (runs[0].records !== 12 || runs[1].records !== 12) throw new Error('run sizes ' + runs[0].records + ' / ' + runs[1].records);
    if (!runs[0].demo || !runs[1].demo) throw new Error('a run lost its demo provenance');
    const picks = queryAll(doc.body, 'select[data-slot]');
    if (picks.length !== 2) throw new Error('run pickers: ' + picks.length);
    picks.forEach((p) => {
      const opts = queryAll(p, 'option');
      if (opts.length !== 2) throw new Error('picker ' + p.getAttribute('data-slot') + ' offers ' + opts.length + ' runs');
    });
    const model = ui.compareModel();
    if (model.steps !== 13) throw new Error('aligned steps: ' + model.steps);
    if (model.diffs <= 0) throw new Error('the two runs show no differences');
    const cells = queryAll(doc.body, '[data-hook="compare-cell"]');
    if (cells.length !== 13 * 8) throw new Error('compare cells: ' + cells.length + ' (expected 13 steps x 8 fields)');
    const kinds = new Set(cells.map((c) => c.getAttribute('data-kind')));
    ['changed', 'only_a', 'only_b', 'same'].forEach((k) => {
      if (!kinds.has(k)) throw new Error('no row marked as ' + k);
    });
    if (model.classes.length < 3) throw new Error('difference classes in the model: ' + model.classes.join(','));
    const counts = {};
    ['changed', 'only_a', 'only_b', 'same'].forEach((k) => {
      counts[k] = queryAll(doc.body, `[data-hook="compare-cell"][data-kind="${k}"]`).length;
      if (counts[k] !== model.counts[k]) throw new Error('cells marked ' + k + ' = ' + counts[k] + ', model says ' + model.counts[k]);
      const values = queryAll(doc.body, '.compare__value--' + k).length;
      if (values !== 2 * counts[k]) throw new Error(k + ' highlight values ' + values + ' for ' + counts[k] + ' cells');
    });
    if (model.classes.length < 3 || counts.changed + counts.only_a + counts.only_b < 3) {
      throw new Error('fewer than three highlighted difference classes: ' + model.classes.join(','));
    }
    if (counts.changed + counts.only_a + counts.only_b !== model.diffs) throw new Error('highlight count does not match the model diffs');
    cells.forEach((c) => {
      const kind = c.getAttribute('data-kind');
      const values = queryAll(c, '.compare__value');
      if (values.length !== 2) throw new Error('a compare row does not show both sides');
      values.forEach((v) => {
        if (v.getAttribute('data-diff') !== kind) throw new Error('a value highlight does not match its row kind');
        if ((v.getAttribute('class') || '').indexOf('compare__value--' + kind) < 0) throw new Error('a value is missing the colour class for ' + kind);
      });
    });
    const stepA = queryAll(doc.body, '[data-hook="compare-step"][data-seq="11"]')[0];
    if (!stepA || stepA.getAttribute('data-hasB') !== 'false') throw new Error('step 11 is not marked as run A only');
    const stepB = queryAll(doc.body, '[data-hook="compare-step"][data-seq="13"]')[0];
    if (!stepB || stepB.getAttribute('data-hasA') !== 'false') throw new Error('step 13 is not marked as run B only');
    if (panel.getAttribute('data-diffs') !== String(model.diffs)) throw new Error('panel data-diffs = ' + panel.getAttribute('data-diffs'));
    if (Number(panel.getAttribute('data-classes')) < 3) throw new Error('panel data-classes = ' + panel.getAttribute('data-classes'));
    const summary = queryAll(doc.body, '.compare__summary')[0];
    if (!summary || summary.getAttribute('data-diffs') !== String(model.diffs)) throw new Error('the summary does not match the model');
    const legend = queryAll(doc.body, '.compare__legend .compare__key');
    if (legend.length !== 4) throw new Error('legend rows: ' + legend.length);
    legend.forEach((li) => {
      const cls = /compare__key--([a-z_]+)/.exec(li.getAttribute('class') || '');
      const value = queryAll(li, '.compare__key-count')[0];
      if (!cls || !value) throw new Error('a legend row is incomplete');
      if (value.textContent !== String(model.counts[cls[1]] || 0)) throw new Error('legend count for ' + cls[1] + ' = ' + value.textContent);
    });
    const rendersCompare = ui.state.stats.renders;
    ui.setCompare('a', 'b');
    if (ui.state.stats.renders - rendersCompare !== 1) throw new Error('switching a run cost ' + (ui.state.stats.renders - rendersCompare) + ' renders');
    const sameModel = ui.compareModel();
    if (sameModel.a !== 'b' || sameModel.b !== 'b') throw new Error('the picker did not move run A to B');
    if (sameModel.diffs !== 0) throw new Error('comparing a run with itself shows ' + sameModel.diffs + ' differences');
    const summary2 = queryAll(doc.body, '.compare__summary')[0];
    if (summary2.getAttribute('data-identical') !== 'true') throw new Error('the identical case is not stated honestly');
    ui.setCompare('a', 'a');
    const toggle = queryAll(doc.body, '[data-hook="compare-toggle"]')[0];
    toggle.click();
    if (ui.compareOpen() !== false) throw new Error('the toggle did not close compare');
    if (app.getAttribute('data-compare') !== 'off') throw new Error('data-compare not cleared');
    if (queryAll(doc.body, '[data-hook="compare-cell"]').length !== 0) throw new Error('the grid stayed after closing compare');
    queryAll(doc.body, '[data-hook="compare-toggle"]')[0].click();
    if (queryAll(doc.body, '[data-hook="compare-cell"]').length !== 13 * 8) throw new Error('the grid did not come back');
    return 'runs 12+12, 13 aligned steps x 8 fields, ' + model.classes.join('/') + ' classes, ' + model.diffs +
      ' highlighted cells, legend counts match, self-compare states "identical"';
  });

  check('3.2 responsive: frame rig switches layout buckets, phone numbers unchanged, en parity', () => {
    const wide = boot({ search: '?probe=1&frame=1440x900&compare=on', innerWidth: 1440, innerHeight: 900 });
    const wideApp = wide.document.getElementById('app');
    if (wideApp.getAttribute('data-frame') !== '1440x900') throw new Error('wide rig box not pinned');
    if (wideApp.getAttribute('data-frame-layout') !== 'desktop') throw new Error('data-frame-layout = ' + wideApp.getAttribute('data-frame-layout'));
    if (wide.ui.state.view !== 'desktop') throw new Error('a 1440x900 frame did not select the desktop view: ' + wide.ui.state.view);
    const wideText = wide.document.getElementById('probe-text').textContent;
    if (wide.document.getElementById('probe-text').getAttribute('data-rig-error') !== 'none') {
      throw new Error('a render step threw at 1440x900: ' + wide.document.getElementById('probe-text').getAttribute('data-rig-error'));
    }
    ['probe @ 1440x900', 'zones needs_me=4 running=1 done=7', 'cards=12', 'progress=58%', 'missingKeys=0',
      'desk sidebar=12', 'scrub=1/12', 'compare open=true runs=2 steps=13'].forEach((needle) => {
      if (wideText.indexOf(needle) < 0) throw new Error('the 1440x900 report lacks "' + needle + '"');
    });
    const deskProbe = wide.ui.probe();
    if (deskProbe.rendered.sidebarItems !== 12) throw new Error('desktop sidebar items: ' + deskProbe.rendered.sidebarItems);
    if (deskProbe.rendered.filterButtons !== 3) throw new Error('filter buttons: ' + deskProbe.rendered.filterButtons);
    if (deskProbe.rendered.timelineTicks !== 12) throw new Error('timeline ticks: ' + deskProbe.rendered.timelineTicks);
    if (deskProbe.rendered.compareCells !== 13 * 8) throw new Error('compare cells at 1440x900: ' + deskProbe.rendered.compareCells);
    if (deskProbe.desk.timeline.total !== 12) throw new Error('timeline total: ' + deskProbe.desk.timeline.total);

    const tablet = boot({ search: '?probe=1&frame=820x1180', innerWidth: 1600, innerHeight: 1200 });
    if (tablet.ui.state.view !== 'tablet') throw new Error('820px frame -> view ' + tablet.ui.state.view);
    if (tablet.document.getElementById('app').getAttribute('data-frame-layout') !== 'tablet') throw new Error('tablet layout not stamped');
    if (tablet.document.getElementById('timeline-main').getAttribute('aria-hidden') !== 'false') throw new Error('the tablet view hides the timeline');

    const phone = boot({ search: '?probe=1&frame=375x812&compare=on', innerWidth: 1440, innerHeight: 900 });
    const phoneApp = phone.document.getElementById('app');
    if (phoneApp.getAttribute('data-frame-layout') !== 'mobile') throw new Error('the phone frame did not stamp the mobile layout');
    if (phone.ui.state.view !== 'mobile') throw new Error('the phone frame lost the phone view: ' + phone.ui.state.view);
    const phoneText = phone.document.getElementById('probe-text').textContent;
    ['zones needs_me=4 running=1 done=7', 'cards=12', 'demoBadges=12', 'progress=58%', 'missingKeys=0',
      'desk sidebar=12', 'compare open=true runs=2'].forEach((needle) => {
      if (phoneText.indexOf(needle) < 0) throw new Error('the 375x812 report lost "' + needle + '" (mobile regression)');
    });
    if (phone.document.getElementById('timeline-main').getAttribute('aria-hidden') !== 'true') throw new Error('the timeline is announced as visible on the phone');
    if (queryAll(phone.document.body, 'button[data-hook="fab"]').length !== 1) throw new Error('the floating ball disappeared on the phone');
    const phoneFab = phone.document.getElementById('fab');
    if (phoneFab.hidden) throw new Error('the ball is hidden on the phone');
    if (phone.ui.state.sheetOpen !== false) throw new Error('?compare=on opened the phone sheet by itself');
    if (phoneApp.getAttribute('data-compare') !== 'on') throw new Error('the phone lost the compare state');
    const rigStart = styleSource.indexOf('13. verification rig');
    if (rigStart < 0) throw new Error('styles.css has no rig section');
    const phoneCss = styleSource.slice(0, rigStart);
    if (phoneCss.indexOf('@media (min-width: 1024px)') < 0) throw new Error('no desktop-only media block: the compare panel is not width-gated');
    const compareGate = phoneCss.indexOf('.app[data-compare="on"] .compare');
    if (compareGate < 0) throw new Error('styles.css has no compare open hook');
    const gateMedia = phoneCss.slice(0, compareGate).lastIndexOf('@media (min-width: 1024px)');
    if (gateMedia < 0 || compareGate - gateMedia > 200) throw new Error('the compare panel is not gated to >=1024px');

    const enDesk = boot({ search: '?view=desktop&lang=en&compare=on', innerWidth: 1440, innerHeight: 900 });
    const enDoc = enDesk.document;
    const chromeNodes = [
      ...queryAll(enDoc.body, '.sidebar__block-title'),
      ...queryAll(enDoc.body, '.sidebar__block-hint'),
      ...queryAll(enDoc.body, '.filter__label'),
      ...queryAll(enDoc.body, '.timeline__scrub-label'),
      ...queryAll(enDoc.body, '.timeline__peek-kicker'),
      ...queryAll(enDoc.body, '.compare__legend-title'),
      ...queryAll(enDoc.body, '.compare__legend .legend__label'),
      ...queryAll(enDoc.body, '.compare__summary'),
      ...queryAll(enDoc.body, '.compare__field'),
      ...queryAll(enDoc.body, '.compare__note')
    ];
    if (chromeNodes.length < 15) throw new Error('desktop copy nodes found: ' + chromeNodes.length);
    const cjk = chromeNodes.filter((n) => CJK.test(String(n.textContent || '')));
    if (cjk.length) throw new Error('Chinese chrome copy in the English desktop view: ' + cjk.slice(0, 3).map((n) => n.className + '=' + n.textContent).join(' | '));
    const zhDesk = boot({ search: '?view=desktop&lang=zh&compare=on', innerWidth: 1440, innerHeight: 900 });
    const zhTitles = queryAll(zhDesk.document.body, '.sidebar__block-title').map((n) => n.textContent);
    const enTitles = queryAll(enDoc.body, '.sidebar__block-title').map((n) => n.textContent);
    if (zhTitles.length !== enTitles.length || !zhTitles.length) throw new Error('block titles differ in count between zh and en');
    if (zhTitles.every((v, i) => v === enTitles[i])) throw new Error('the language switch left the desktop block titles identical');
    const enMissing = enDesk.ui.missingKeys();
    if (enMissing.length) throw new Error('missing keys in the English desktop view: ' + enMissing.join(', '));
    return '1440x900 desktop + 820x1180 tablet + 375x812 phone rig, report lines complete, mobile numbers unchanged, en desktop copy has no CJK';
  });

  check('compare-data.js: generated run B, synthetic provenance, no drift, wired into the page', () => {
    const source = read(FILES.compare);
    if (!source) throw new Error('web/compare-data.js is missing (node selfcheck.mjs --sync-compare)');
    const payload = comparePayload();
    const m = /global\.TraceLensCompareData = ([\s\S]*?);\n\}\(/.exec(source);
    if (!m) throw new Error('compare-data.js does not assign window.TraceLensCompareData');
    const parsed = JSON.parse(m[1]);
    if (JSON.stringify(parsed.records) !== JSON.stringify(payload.records)) {
      throw new Error('compare-data.js drifted from the generator: run node selfcheck.mjs --sync-compare');
    }
    if (parsed._meta.synthetic !== true || parsed._meta.real_data !== false || parsed._meta.demo_data !== true) {
      throw new Error('run B does not carry the synthetic/real_data/demo_data triple');
    }
    if (!parsed._meta.derived_from || !parsed._meta.variant) throw new Error('run B does not document how it was derived');
    if (parsed.records.length !== 12) throw new Error('run B records: ' + parsed.records.length);
    parsed.records.forEach((r) => {
      if (String(r.id).indexOf('act-demo-b-') !== 0) throw new Error('run B id is not namespaced: ' + r.id);
      if (r.demo_data !== true || r.synthetic !== true) throw new Error('run B record ' + r.id + ' lost its demo flags');
      if (!r.source || r.source.kind !== 'demo') throw new Error('run B record ' + r.id + ' has no demo source kind');
      if (!r.human_readable || r.human_readable.source_facts_hash !== null) throw new Error('run B keeps a stale fact hash: ' + r.id);
    });
    const seqs = parsed.records.map((r) => r.sequence_no);
    if (seqs.indexOf(11) >= 0) throw new Error('run B kept step 11, so the "only in run A" case is missing');
    if (seqs.indexOf(13) < 0) throw new Error('run B has no extra step 13, so the "only in run B" case is missing');
    if (new Set(seqs).size !== seqs.length) throw new Error('run B has duplicate step numbers');
    const index = read(FILES.index);
    const order = ['i18n/embed.js', 'demo-data.js', 'demo-data-en.js', 'compare-data.js', 'app.js'];
    const positions = order.map((src) => index.indexOf('src="' + src + '"'));
    if (positions.some((p) => p < 0)) throw new Error('index.html does not load every script: ' + order.join(' -> '));
    for (let i = 1; i < positions.length; i += 1) {
      if (positions[i] <= positions[i - 1]) throw new Error('index.html script order is wrong: ' + order.join(' -> '));
    }
    const b = bootOnce();
    if (b.ui.state.compareRecords.length !== 12) throw new Error('the page boot loaded ' + b.ui.state.compareRecords.length + ' run B records');
    if (b.ui.runsOf().length !== 2) throw new Error('the booted page does not offer two runs to compare');
    const single = boot({ search: '' });
    single.ui.setCompareData([]);
    if (single.ui.runsOf().length !== 1) throw new Error('without run B the panel must show a single run');
    if (single.ui.state.compare.a !== single.ui.state.compare.b) throw new Error('with one run the slots were not aligned to it');
    return 'run B = 12 synthetic variants (step 11 dropped, step 13 added), no drift, script order ' + order.join(' -> ');
  });

  check('keys.md: full key inventory matches the dictionaries', () => {
    if (!keysMd) throw new Error('web/i18n/keys.md is missing (architecture 6 requires it)');
    const domains = new Set(zhKeys.map((k) => k.split('.')[0]));
    /* Any backticked key inside a known domain counts, so keys whose leaf is
     * fixture text (4.7 demo.run.<name>, which keeps CJK out of app.js) are
     * inventoried like the ASCII ones. The domain filter keeps prose code
     * spans out of the comparison. */
    const listed = [...new Set([...keysMd.matchAll(/`([^`\n]+)`/g)]
      .map((m) => m[1])
      .filter((k) => k.indexOf('.') > 0 && domains.has(k.split('.')[0])))];
    const missing = zhKeys.filter((k) => listed.indexOf(k) < 0);
    const extra = listed.filter((k) => zhKeys.indexOf(k) < 0);
    if (missing.length) throw new Error('keys.md is missing: ' + missing.join(', '));
    if (extra.length) throw new Error('keys.md lists unknown keys: ' + extra.join(', '));
    const tableRows = (keysMd.match(/^\| `.*` \| .* \| .* \|$/gm) || []).length;
    if (tableRows !== zhKeys.length) throw new Error('keys.md rows=' + tableRows + ' but dictionaries have ' + zhKeys.length + ' keys');
    return 'keys.md documents ' + listed.length + ' keys in ' + domains.size + ' domains (zh/en identical)';
  });

  check('styles.css: every var(--token) used is defined in :root', () => {
    const defined = new Set([...styleSource.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
    const used = [...styleSource.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]);
    const missing = [...new Set(used.filter((tk) => !defined.has(tk)))];
    if (missing.length) throw new Error('tokens used but never defined: ' + missing.join(', '));
    return 'defined=' + defined.size + ', distinct used=' + new Set(used).size + ', undefined=0';
  });

  check('styles.css: responsive hooks (<=480 ball+sheet, >=1024 grid)', () => {
    const mobile = /@media \(max-width:\s*480px\)/.test(styleSource) || /@media\s*\(max-width:480px\)/.test(styleSource);
    const desktop = /min-width:\s*1024px/.test(styleSource);
    const tablet = /min-width:\s*481px/.test(styleSource);
    if (!mobile) throw new Error('no <=480px media query');
    if (!tablet) throw new Error('no >=481px media query');
    if (!desktop) throw new Error('no >=1024px desktop grid query');
    const reduced = /@media \(prefers-reduced-motion: reduce\)/.test(styleSource);
    if (!reduced) throw new Error('no prefers-reduced-motion block');
    const guards = ['overflow-x', 'overflow-wrap', 'min-width: 0'];
    const missing = guards.filter((g) => styleSource.indexOf(g) < 0);
    if (missing.length) throw new Error('missing horizontal-scroll guards: ' + missing.join(', '));
    return '480 / 481 / 1024 breakpoints + reduced-motion + overflow guards';
  });

  check('data source: demo set carries the synthetic provenance, not real data', () => {
    const meta = sample._meta || {};
    if (meta.synthetic !== true || meta.real_data !== false || meta.demo_data !== true) throw new Error('_meta provenance flags missing');
    if (String(meta.author_note || '').indexOf('demo_data') < 0) throw new Error('_meta.author_note does not state the triple');
    const demo = loadDemoPayload();
    if (!demo._meta || demo._meta.copy_of !== 'samples/action_records.json') throw new Error('demo-data.js does not declare its source');
    if (demo._meta.synthetic !== true || demo._meta.demo_data !== true) throw new Error('copy lost the provenance flags');
    const en = loadDemoPayloadEn();
    if (!en._meta || en._meta.copy_of !== 'samples/action_records_en.json') {
      throw new Error('demo-data-en.js does not declare its source');
    }
    if (en._meta.synthetic !== true || en._meta.demo_data !== true) {
      throw new Error('the English copy lost the provenance flags');
    }
    if (String(en._meta.language) !== 'en') throw new Error('demo-data-en.js _meta.language is not en');
    return 'zh + en copies declare their source; synthetic/real_data/demo_data flags present';
  });

  /* ---- 4.7 bilingual demo datasets: the English UI must show zero Chinese data ---- *
   * QA's release line (docs/01 K-10) covers chrome copy; this pins the other half:
   * the *data*. Both datasets are byte copies of the two fixtures, which share ids,
   * numbers, timestamps and statuses, so the pair renders the same facts in the two
   * languages and nothing has to be invented for English. */
  check('4.7 bilingual demo sets: same ids and machine fields, en data zero CJK, zh data really Chinese', () => {
    const zhData = loadDemoPayload();
    const enData = loadDemoPayloadEn();
    const zhIds = zhData.records.map((r) => r.id);
    const enIds = enData.records.map((r) => r.id);
    if (JSON.stringify(zhIds) !== JSON.stringify(enIds)) {
      throw new Error('the two demo sets do not show the same records in the same order');
    }
    const machine = (r) => JSON.stringify([r.status, r.action_type, r.risk_level, r.latency_ms,
      r.confidence, r.cost, r.requires_action, r.sequence_no, r.timestamp, r.received_at, r.agent_id,
      r.session_id, r.run_id, r.demo_data, r.synthetic]);
    const drift = [];
    zhData.records.forEach((r, i) => {
      if (machine(r) !== machine(enData.records[i])) drift.push(r.id);
    });
    if (drift.length) throw new Error('machine fields differ between languages (only copy may change): ' + drift.join(', '));
    const enCjk = cjkCount(JSON.stringify(enData));
    if (enCjk) throw new Error('English demo data contains ' + enCjk + ' CJK characters: ' +
      enData.records.filter((r) => cjkCount(JSON.stringify(r))).map((r) => r.id).slice(0, 4).join(', '));
    const zhCjk = cjkCount(JSON.stringify(zhData));
    if (zhCjk < 200) throw new Error('the Chinese demo set is not Chinese any more (CJK=' + zhCjk + ')');
    zhData.records.forEach((r) => {
      if (String(r.human_readable.language) !== 'zh') throw new Error(r.id + ' human_readable.language is not zh');
    });
    const zhById = {};
    zhData.records.forEach((r) => { zhById[r.id] = r; });
    enData.records.forEach((r) => {
      if (String(r.human_readable.language) !== 'en') throw new Error(r.id + ' human_readable.language is not en');
      const twin = zhById[r.id];
      if (!twin || !CJK_HAN.test(String(twin.human_readable.l1))) {
        throw new Error(r.id + ' Chinese L1 is not Chinese: the zh set lost its copy');
      }
    });
    return enData.records.length + ' records x 2 languages; en CJK=0, zh CJK=' + zhCjk +
      ', machine fields identical, ids in the same order';
  });

  check('4.7 language switch swaps the dataset: en UI shows zero CJK data, zh UI shows Chinese', () => {
    const en = boot({ language: 'en-US' });
    en.ui.setLang('en');
    if (en.ui.dataLang() !== 'en') throw new Error('setLang(en) did not switch the demo dataset: ' + en.ui.dataLang());
    const enIds = en.ui.state.records.map((r) => r.id);
    const wantEn = loadDemoPayloadEn().records.map((r) => r.id);
    if (JSON.stringify(enIds) !== JSON.stringify(wantEn)) throw new Error('the English UI is not reading demo-data-en.js');
    const dataCjk = cjkCount(JSON.stringify(en.ui.state.records));
    if (dataCjk) throw new Error('the English UI renders ' + dataCjk + ' CJK characters of demo data');
    const cardCjk = cards(en.document.body).map((c) => cjkCount(c.textContent)).reduce((a, b) => a + b, 0);
    if (cardCjk) throw new Error('English cards render ' + cardCjk + ' CJK characters (data leaked into the DOM)');
    const runNames = en.ui.runsOf().map((r) => r.name);
    const runCjk = runNames.map((n) => cjkCount(n)).reduce((a, b) => a + b, 0);
    if (runCjk) throw new Error('run labels keep Chinese in the English UI: ' + runNames.join(' / '));
    if (!en.ui.runsOf().every((r) => /[A-Za-z]/.test(r.name))) {
      throw new Error('run labels are not the translated copy: ' + runNames.join(' / '));
    }
    const zh = boot({ language: 'zh-CN' });
    zh.ui.setLang('zh');
    if (zh.ui.dataLang() !== 'zh') throw new Error('the Chinese UI did not load the Chinese dataset');
    const zhCardCjk = cards(zh.document.body).map((c) => cjkCount(c.textContent)).reduce((a, b) => a + b, 0);
    if (zhCardCjk < 40) throw new Error('the Chinese UI has no Chinese data to show (CJK=' + zhCardCjk + ')');
    en.ui.setLang('zh');
    if (en.ui.dataLang() !== 'zh') throw new Error('switching back to zh did not restore the Chinese dataset');
    const restored = en.ui.state.records.map((r) => r.id);
    if (JSON.stringify(restored) !== JSON.stringify(loadDemoPayload().records.map((r) => r.id))) {
      throw new Error('switching back to zh did not restore demo-data.js');
    }
    return 'en UI: ' + enIds.length + ' English records, 0 CJK in data, cards and run labels; ' +
      'zh UI: Chinese data; round-trip restores the zh set';
  });

  check('4.7 demo run labels are dictionary copy, so no CJK literal lives in app.js', () => {
    const zhVals = dictValues(readJson(FILES.zh));
    const enVals = dictValues(readJson(FILES.en));
    const names = demoLabelNames();
    const missing = names.filter((n) => typeof zhVals['demo.label.' + n] !== 'string'
      || typeof enVals['demo.label.' + n] !== 'string');
    if (missing.length) throw new Error('demo names without dictionary copy: ' + missing.join(' / '));
    const enCjk = names.filter((n) => cjkCount(enVals['demo.label.' + n]) > 0);
    if (enCjk.length) throw new Error('English run labels contain Chinese: ' + enCjk.join(' / '));
    const zhPlain = names.filter((n) => cjkCount(zhVals['demo.label.' + n]) === 0);
    if (zhPlain.length) throw new Error('Chinese run labels are not Chinese: ' + zhPlain.join(' / '));
    if (CJK.test(appSource)) throw new Error('app.js carries a CJK literal again; demo names must come from the dictionaries');
    return names.length + ' demo names mapped to zh + en copy, app.js CJK=0';
  });

  check('4.7 compare labels: every field label and the fieldLabel fallback resolve in zh and en', () => {
    const zhVals = dictValues(readJson(FILES.zh));
    const enVals = dictValues(readJson(FILES.en));
    const fieldKeys = Object.keys(zhVals).filter((k) => k.indexOf('compare.field.') === 0);
    if (!fieldKeys.length) throw new Error('no compare.field.* keys in the dictionary');
    const ghosts = fieldKeys.filter((k) => typeof enVals[k] !== 'string');
    if (ghosts.length) throw new Error('compare field labels missing in en: ' + ghosts.join(', '));
    const enCjk = fieldKeys.filter((k) => cjkCount(enVals[k]) > 0);
    if (enCjk.length) throw new Error('compare field labels contain Chinese in en: ' + enCjk.join(', '));
    if (typeof zhVals['compare.field.other'] !== 'string' || typeof enVals['compare.field.other'] !== 'string') {
      throw new Error('the fieldLabel fallback key compare.field.other is not in both dictionaries');
    }
    if (zhVals['compare.field.other'] === enVals['compare.field.other']) {
      throw new Error('compare.field.other was copied verbatim into en (untranslated)');
    }
    /* The 3.2 leftover: fieldLabel() used to answer every unknown key with
     * compare.field.l1, which labelled unrelated rows "L1". The fallback now
     * names a neutral key, and the stale literal must be gone from the code. */
    if (/'compare\.field\.l1'/.test(appSource)) {
      throw new Error('app.js still uses compare.field.l1 as the fieldLabel fallback');
    }
    const open = bootOnce();
    open.ui.setCompareOpen(true);
    open.ui.setCompare('a', 'a');
    open.ui.setCompare('b', 'b');
    if (!open.ui.compareOpen()) throw new Error('compare mode did not open');
    const head = queryAll(open.document.body, 'th.compare__field, .compare__field').map((n) => n.textContent);
    const heads = head.filter((v) => String(v).trim());
    if (!heads.length) throw new Error('the compare table renders no field labels');
    open.ui.setLang('en');
    const enHeads = queryAll(open.document.body, 'th.compare__field, .compare__field').map((n) => n.textContent)
      .filter((v) => String(v).trim());
    const leaked = enHeads.filter((v) => cjkCount(v) > 0);
    if (leaked.length) throw new Error('compare field labels in the English UI: ' + leaked.slice(0, 4).join(' / '));
    return fieldKeys.length + ' compare field labels in zh + en, neutral fallback, en table labels CJK-free';
  });

  /* ---- 4.1 intervention loop: the click path, driven end to end ---- *
   * The golden file is generated by agent_explainer/server.apply_decision
   * (see verify/README.md); the transport below is a stand-in for the local
   * sidecar, and the shim DOM really clicks the real handlers.
   */

  check('4.1 approve: click sends the decision, the answer replaces the card, queue shrinks', async () => {
    const { ui, document } = ivBoot();
    const log = [];
    ui.setApiTransport(transportFor('approve-0004', log));
    await ui.probeApi();
    if (ui.apiMode().mode !== 'live') throw new Error('sidecar probe did not report live: ' + JSON.stringify(ui.apiMode()));
    const box = ivBox(document, 'act-demo-0004');
    if (!box || box.getAttribute('data-decidable') !== 'true') throw new Error('act-demo-0004 is not offered for a decision');
    const before = ui.probe();
    const btn = ivButton(document, 'act-demo-0004', 'approve');
    if (!btn || btn.disabled) throw new Error('the approve button is missing or disabled');
    btn.click();
    await settle();
    if (log.length !== 1) throw new Error('expected exactly one request, saw ' + log.length);
    if (log[0].apiPath !== 'intervene' || log[0].apiPath.startsWith('/')) throw new Error('request left the allowlist: ' + log[0].apiPath);
    const body = log[0].body;
    if (body.action_id !== 'act-demo-0004' || body.decision !== 'approve') throw new Error('wrong body: ' + JSON.stringify(body));
    if (body.option_id !== goldenCase('approve-0004').option_id) throw new Error('option_id not sent: ' + body.option_id);
    if ('modified_params' in body || 'reason' in body) throw new Error('approve must not send params/reason');
    const rec = recordIn(ui, 'act-demo-0004');
    if (rec.status !== 'approved') throw new Error('status did not follow the answer: ' + rec.status);
    if (rec.requires_action !== false) throw new Error('requires_action is still true after the decision');
    if (rec.user_intervention.decision !== 'approve' || rec.user_intervention.recorded !== true) throw new Error('user_intervention not recorded');
    const after = ui.probe();
    if (after.zones.needs_me !== before.zones.needs_me - 1) throw new Error('the card did not leave the needs-me queue');
    const feedback = ivFind(document, '[data-hook="intervene-feedback"][data-card="act-demo-0004"]');
    if (feedback.length !== 1) throw new Error('no feedback block after a recorded decision');
    if (feedback[0].getAttribute('data-mode') !== 'live' || feedback[0].getAttribute('data-decision') !== 'approve') {
      throw new Error('feedback does not say live/approve: ' + feedback[0].getAttribute('data-mode'));
    }
    if (!ivFind(document, '[data-hook="intervene-mode"][data-mode="live"]').length) throw new Error('no mode label on the feedback');
    if (ivFind(document, '[data-hook="intervene-badge"][data-mode="live"]').length !== 1) throw new Error('no live badge on the panel');
    const panelText = String((ivBox(document, 'act-demo-0004') || {}).textContent || '');
    const keyLeak = panelText.match(/[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*/i);
    if (keyLeak) throw new Error('a raw dictionary key is visible copy: ' + keyLeak[0]);
    const summary = ui.interveneSummary();
    if (summary.decisions !== 1 || summary.local !== 0) throw new Error('counters wrong: ' + JSON.stringify(summary));
    if (ui.decidable('act-demo-0004').ok) throw new Error('a decided record must not be decidable any more');
    return '1 POST to "' + log[0].apiPath + '", status approved, needs_me ' + before.zones.needs_me + '->' + after.zones.needs_me +
      ', live feedback + badge rendered';
  });

  check('4.1 modify: the form comes from editable_fields and submits modified_params', async () => {
    const { ui, document } = ivBoot();
    const log = [];
    ui.setApiTransport(transportFor('modify-0005', log));
    await ui.probeApi();
    const modify = ivButton(document, 'act-demo-0005', 'modify');
    if (!modify || modify.disabled) throw new Error('the modify button is missing or disabled');
    modify.click();
    const form = ivFind(document, '[data-hook="intervene-form"][data-card="act-demo-0005"]');
    if (form.length !== 1) throw new Error('no form opened under the modify option');
    const inputs = ivFind(document, '[data-hook="intervene-field"][data-card="act-demo-0005"]');
    const names = inputs.map((n) => n.getAttribute('data-field')).sort();
    if (names.join(',') !== 'max_delete_count,move_to_trash') throw new Error('form fields do not match editable_fields: ' + names.join(','));
    const numberInput = inputs.find((n) => n.getAttribute('data-field') === 'max_delete_count');
    const checkInput = inputs.find((n) => n.getAttribute('data-field') === 'move_to_trash');
    if (numberInput.getAttribute('data-type') !== 'integer' || numberInput.getAttribute('type') !== 'number') throw new Error('integer field is not a number input');
    if (numberInput.getAttribute('min') !== '0' || numberInput.getAttribute('max') !== '12') throw new Error('range not passed to the input: ' + numberInput.getAttribute('min') + '-' + numberInput.getAttribute('max'));
    if (String(numberInput.value) !== '12') throw new Error('field not seeded with the current value: ' + numberInput.value);
    if (checkInput.getAttribute('data-type') !== 'boolean' || checkInput.getAttribute('type') !== 'checkbox' || checkInput.checked !== false) {
      throw new Error('boolean field is not an unchecked checkbox');
    }
    /* labels come from editable_fields[].label, never from a dictionary key:
     * a shadowed helper once rendered "compare.field.l1" as visible copy */
    const option = recordIn(ui, 'act-demo-0005').intervention_options.find((o) => o.kind === 'modify');
    const labels = ((option && option.editable_fields) || []).map((f) => f.label).filter(Boolean);
    if (!labels.length) throw new Error('the fixture lost the human field labels');
    const formText = String(form[0].textContent || '');
    labels.forEach((label) => {
      if (formText.indexOf(label) < 0) throw new Error('field label missing from the form: ' + label);
    });
    const rawKey = formText.match(/[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*/i);
    if (rawKey) throw new Error('a raw dictionary key leaked into the form: ' + rawKey[0]);
    if (!ivFind(document, '[data-hook="intervene-submit"]').length || !ivFind(document, '[data-hook="intervene-cancel"]').length) {
      throw new Error('the form has no submit/cancel');
    }
    /* type into the real inputs: the delegated capture handler stores them */
    numberInput.value = '3';
    document.getElementById('zones').dispatchEvent({ type: 'input', target: numberInput });
    checkInput.checked = true;
    document.getElementById('zones').dispatchEvent({ type: 'change', target: checkInput });
    if (ui.interveneStatus('act-demo-0005').form !== true) throw new Error('the form closed while editing');
    ivFind(document, '[data-hook="intervene-submit"]')[0].click();
    await settle();
    if (log.length !== 1) throw new Error('expected one submit, saw ' + log.length);
    const body = log[0].body;
    if (body.decision !== 'modify') throw new Error('decision: ' + body.decision);
    if (JSON.stringify(body.modified_params) !== JSON.stringify({ max_delete_count: 3, move_to_trash: true })) {
      throw new Error('modified_params not coerced from the form: ' + JSON.stringify(body.modified_params));
    }
    const rec = recordIn(ui, 'act-demo-0005');
    if (rec.status !== 'modified') throw new Error('status: ' + rec.status);
    if (rec.user_intervention.modified_fields.length !== 2) throw new Error('modified_fields lost: ' + rec.user_intervention.modified_fields.length);
    if (rec.execution_receipt.state !== 'unreported') throw new Error('the receipt must stay unreported, saw ' + rec.execution_receipt.state);
    const table = ivFind(document, '[data-hook="intervene-feedback"][data-card="act-demo-0005"] tr[data-field]');
    if (table.length !== 2) throw new Error('no before/after table for the changed parameters: ' + table.length);
    if (ivFind(document, '[data-hook="intervene-form"][data-card="act-demo-0005"]').length) throw new Error('the form stayed open after a recorded decision');
    return 'form built from 2 editable_fields, values coerced, POST ' + JSON.stringify(body.modified_params) +
      ' -> modified, 2-row change table, receipt untouched';
  });

  check('4.1 reject: the reason is required before anything is sent', async () => {
    const { ui, document } = ivBoot();
    const log = [];
    ui.setApiTransport(transportFor('reject-0008', log));
    await ui.probeApi();
    const reject = ivButton(document, 'act-demo-0008', 'reject');
    if (!reject || reject.disabled) throw new Error('the reject button is missing or disabled');
    reject.click();
    const box = ivFind(document, '[data-hook="intervene-reason-box"][data-card="act-demo-0008"]');
    if (box.length !== 1) throw new Error('no reason box opened for a requires_reason option');
    ivFind(document, '[data-hook="intervene-reject-confirm"]')[0].click();
    await settle();
    if (log.length !== 0) throw new Error('an empty reason must not reach the server');
    const errs = ivFind(document, '[data-hook="intervene-error"][data-card="act-demo-0008"]');
    if (errs.length !== 1) throw new Error('no inline validation message for the missing reason');
    if (String(errs[0].getAttribute('data-code')) !== 'reason_required') throw new Error('wrong validation code: ' + errs[0].getAttribute('data-code'));
    const zh = readJson(FILES.zh);
    if (errs[0].textContent.indexOf(zh.intervene['validation.reason_required']) < 0) throw new Error('the inline message is not the dictionary copy');
    if (recordIn(ui, 'act-demo-0008').status !== 'pending_confirmation') throw new Error('the record changed without a decision');
    ui.setInterveneReason('act-demo-0008', goldenCase('reject-0008').reason);
    ui.renderAll();
    ivFind(document, '[data-hook="intervene-reject-confirm"]')[0].click();
    await settle();
    if (log.length !== 1) throw new Error('expected one request after a reason was given, saw ' + log.length);
    if (log[0].body.reason !== goldenCase('reject-0008').reason) throw new Error('reason not sent: ' + log[0].body.reason);
    const rec = recordIn(ui, 'act-demo-0008');
    if (rec.status !== 'rejected') throw new Error('status: ' + rec.status);
    if (rec.execution_receipt.state !== 'unreported') throw new Error('reject must not fake an execution result');
    const feedback = ivFind(document, '[data-hook="intervene-feedback"][data-decision="reject"]');
    if (feedback.length !== 1 || feedback[0].textContent.indexOf(goldenCase('reject-0008').reason) < 0) {
      throw new Error('the recorded reason is not shown on the card');
    }
    return 'empty reason -> 0 requests + inline ' + zh.intervene['validation.reason_required'] +
      '; with reason -> rejected, reason shown, receipt untouched';
  });

  check('4.1 no server: the page degrades to a labelled local simulation', async () => {
    const { ui, document } = ivBoot('?probe=1&api=off');
    const mode = ui.apiMode();
    if (mode.mode !== 'forced_offline') throw new Error('?api=off did not force offline: ' + mode.mode);
    const chip = ivFind(document, '[data-hook="api-mode"]');
    if (chip.length !== 1 || chip[0].getAttribute('data-mode') !== 'forced_offline') throw new Error('no api mode chip in the topbar');
    const buttons = ivFind(document, '[data-hook="intervene-button"][data-enabled="true"]');
    if (!buttons.length) throw new Error('offline mode must still offer the three decisions');
    const approve = ivButton(document, 'act-demo-0004', 'approve');
    approve.click();
    await settle();
    if (ui.apiMode().requests !== 0) throw new Error('offline mode must not send requests, saw ' + ui.apiMode().requests);
    const rec = recordIn(ui, 'act-demo-0004');
    if (rec.status !== 'approved') throw new Error('the local transition did not happen: ' + rec.status);
    if (rec.user_intervention.decided_by !== 'local-sim') throw new Error('the simulation must label its actor: ' + rec.user_intervention.decided_by);
    if (rec.user_intervention.audit_entry_hash !== null) throw new Error('a simulation cannot produce an audit hash');
    if (rec.execution_receipt.state !== 'unreported') throw new Error('a simulation must not touch the receipt');
    const badge = ivFind(document, '[data-hook="intervene-badge"][data-mode="local"]');
    if (badge.length !== 1) throw new Error('the panel is not labelled as a local simulation');
    const zh = readJson(FILES.zh);
    if (badge[0].textContent.indexOf(zh.intervene['badge.local']) < 0) throw new Error('badge copy is not the dictionary copy');
    const notes = ivFind(document, '[data-hook="intervene-offline-note"]');
    if (!notes.length) throw new Error('no offline notice in the panel');
    if (notes[0].textContent.indexOf(zh.intervene['offline.notice']) < 0) throw new Error('offline notice copy is not the dictionary copy');
    const summary = ui.interveneSummary();
    if (summary.local !== 1 || summary.decisions !== 1) throw new Error('counters: ' + JSON.stringify(summary));
    if (ivFind(document, '[data-hook="intervene-error"]').length) throw new Error('a degradation must not be shown as an error');
    return 'offline chip + ' + buttons.length + ' enabled buttons, local transition, ' +
      zh.intervene['badge.local'] + ' badge + offline notice, 0 requests, receipt untouched';
  });

  check('4.1 refusal: a decided or non-deciding record is never sent', async () => {
    const { ui, document } = ivBoot();
    const log = [];
    ui.setApiTransport(transportFor('approve-0004', log));
    await ui.probeApi();
    const gate = ui.decidable('act-demo-0002');
    if (gate.ok) throw new Error('a succeeded record must not be decidable');
    const res = await ui.submitIntervene('act-demo-0002', 'approve', {});
    if (res.ok) throw new Error('the submit was accepted for a non-deciding record');
    if (log.length !== 0) throw new Error('the gate must reject before the network');
    if (recordIn(ui, 'act-demo-0002').status !== 'succeeded') throw new Error('the record changed anyway');
    const already = await ui.submitIntervene('act-demo-0007', 'modify', { values: { max_move_count: 5 } });
    if (already.ok) throw new Error('a decided record must not be decidable again');
    if (log.length !== 0) throw new Error('a decided record reached the server');
    if (recordIn(ui, 'act-demo-0007').status !== 'modified') throw new Error('the decided demo record lost its status');
    ['act-demo-0007', 'act-demo-0010'].forEach((id) => {
      if (ui.decidable(id).ok) throw new Error('a decided record is offered for a decision: ' + id);
      if (ivBox(document, id)) throw new Error('a decided record shows an intervention panel: ' + id);
      if (ivFind(document, '[data-hook="intervene-button"][data-card="' + id + '"]').length) {
        throw new Error('buttons rendered on a decided card: ' + id);
      }
    });
    /* the expired record is the one case a decision is still allowed for:
     * deciding an expired offer is legal, only the timer may not decide it */
    if (!ui.decidable('act-demo-0011').ok) throw new Error('an expired offer must stay decidable');
    const offline = ivFind(document, '[data-hook="intervene-button"][data-card="act-demo-0002"]');
    if (offline.length) throw new Error('a record with no options renders decision buttons');
    return 'succeeded + already-decided records blocked before the network (' + log.length +
      ' requests), expired offer stays decidable, decided cards render no buttons';
  });

  check('4.1 parity: the browser transition equals agent_explainer/server.apply_decision', async () => {
    const fields = GOLDEN._meta.parity_fields;
    const excluded = GOLDEN._meta.excluded_fields.map((s) => String(s).split(' ')[0]);
    const problems = [];
    (GOLDEN.cases || []).forEach((c) => {
      const out = uilessApply(c);
      if (!out.ok) { problems.push(c.name + ': browser refused (' + out.code + ')'); return; }
      const bad = parityDiff(out.record, c.result_record, fields);
      if (bad.length) problems.push(c.name + ' -> ' + bad.join('; '));
      excluded.forEach((f) => {
        if (f === 'human_readable') return;
        if (JSON.stringify(getIn(out.record, f)) === JSON.stringify(getIn(c.result_record, f))) {
          problems.push(c.name + ': excluded field ' + f + ' should differ (server-only evidence)');
        }
      });
      if (JSON.stringify(out.record.human_readable) === JSON.stringify(c.result_record.human_readable)) {
        problems.push(c.name + ': the browser must not pretend to re-render the product copy');
      }
      if (JSON.stringify(out.record.human_readable) !== JSON.stringify(c.input_record.human_readable)) {
        problems.push(c.name + ': the browser rewrote human_readable instead of keeping the input copy');
      }
      if (out.record.user_intervention.decided_at !== c.decided_at) problems.push(c.name + ': decided_at');
    });
    if (problems.length) throw new Error(problems.join(' | '));
    return (GOLDEN.cases || []).length + ' golden cases match on ' + fields.length +
      ' fields (status, requires_action, user_intervention, receipt state + effect_notice); server-only fields differ as documented';
  });

  check('4.1 probe rig: the report and data attributes describe the intervention layer', async () => {
    const { ui, document } = ivBoot('?probe=1&api=off');
    await settle();
    const node = document.getElementById('probe-text');
    const text = node.textContent || '';
    const zh = readJson(FILES.zh);
    if (text.indexOf(zh.intervene['probe.title']) < 0) throw new Error('the probe report has no intervention line');
    const p = ui.probe();
    ['mode', 'requests', 'decidable', 'buttonsEnabled', 'local', 'decisions', 'forms', 'results', 'errors']
      .forEach((k) => {
        if (p.intervene[k] === undefined) throw new Error('probe().intervene.' + k + ' missing');
      });
    const offered = (ui.state.records || []).filter((r) =>
      r.requires_action === true || (Array.isArray(r.intervention_options) && r.intervention_options.length));
    const expectedOptions = offered.reduce((n, r) => n + (r.intervention_options || []).length, 0);
    if (p.rendered.interveneBoxes !== offered.length) {
      throw new Error('intervention panels: ' + p.rendered.interveneBoxes + ', records offering options: ' + offered.length);
    }
    if (p.rendered.interveneButtons !== expectedOptions) {
      throw new Error('buttons ' + p.rendered.interveneButtons + ' != one per option ' + expectedOptions);
    }
    if (p.intervene.decidable !== 4) throw new Error('expected 4 decidable demo records, saw ' + p.intervene.decidable);
    if (p.intervene.buttonsEnabled !== p.intervene.decidable * 3) {
      throw new Error('every decidable record must offer 3 enabled buttons: ' + p.intervene.buttonsEnabled);
    }
    if (p.api.mode !== 'forced_offline') throw new Error('probe().api.mode: ' + p.api.mode);
    ['data-api-mode', 'data-intervene-decidable', 'data-intervene-buttons', 'data-intervene-local']
      .forEach((a) => {
        if (!node.getAttribute(a)) throw new Error('probe node missing ' + a);
      });
    if (node.getAttribute('data-api-mode') !== 'forced_offline') throw new Error('data-api-mode: ' + node.getAttribute('data-api-mode'));
    if (String(node.getAttribute('data-intervene-decidable')) !== '4') throw new Error('data-intervene-decidable: ' + node.getAttribute('data-intervene-decidable'));
    return 'report text + ' + p.rendered.interveneButtons + ' buttons / ' + p.intervene.decidable +
      ' decidable records / api.mode=' + p.api.mode + ' published for headless runs';
  });

  check('4.1 live sync: the page takes the records the server already stored', async () => {
    const { ui, document } = ivBoot();
    const log = [];
    const fixture = (ui.state.records || []).slice();
    const decided = JSON.parse(JSON.stringify(fixture.find((r) => String(r.id) === 'act-demo-0004')));
    decided.status = 'approved';
    decided.requires_action = false;
    decided.user_intervention = { decision: 'approve', recorded: true, decided_by: 'local-user', decided_by_role: 'local' };
    const pending = fixture.find((r) => String(r.id) === 'act-demo-0005');
    ui.setApiTransport((endpoint, apiPath) => {
      if (endpoint === 'health') {
        return { status: 200, data: { name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1' } };
      }
      if (endpoint !== 'records') return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
      log.push({ endpoint, apiPath });
      return { status: 200, data: { records: [decided, pending], meta: { count: 2, synthetic: true } } };
    });
    await ui.probeApi();
    const synced = await ui.syncRecordsFromApi();
    if (!synced) throw new Error('live mode did not take the records from the server');
    if (log.length !== 1 || log[0].endpoint !== 'records' || log[0].apiPath.startsWith('/')) {
      throw new Error('records read: ' + JSON.stringify(log));
    }
    if ((ui.state.records || []).length !== 2) throw new Error('the page kept the fixture set: ' + (ui.state.records || []).length);
    if (recordIn(ui, 'act-demo-0004').status !== 'approved') throw new Error('the stored decision did not reach the page');
    /* the record still carries its option list, so the panel stays and explains
     * what was on offer; the rule is the same as everywhere else: enabled
     * buttons only while the record is decidable */
    const box = ivBox(document, 'act-demo-0004');
    if (!box) throw new Error('the decided record dropped its option list');
    if (box.getAttribute('data-decidable') !== 'false') throw new Error('a record the server decided is offered for a decision again');
    if (ivFind(document, '[data-hook="intervene-button"][data-card="act-demo-0004"][data-enabled="true"]').length) {
      throw new Error('enabled buttons on a card the server already decided');
    }
    if (ivFind(document, '[data-hook="intervene-button"][data-card="act-demo-0005"]').length !== 3) {
      throw new Error('the still-pending record lost its three buttons');
    }
    const offline = ivBoot('?probe=1&api=off');
    const syncedOffline = await offline.ui.syncRecordsFromApi();
    if (syncedOffline !== false) throw new Error('offline mode tried to read records from a server');
    if ((offline.ui.state.records || []).length !== fixture.length + 0 && (offline.ui.state.records || []).length < 10) {
      throw new Error('offline mode replaced the demo data with nothing');
    }
    return 'live: 1 GET "' + log[0].apiPath + '" -> 2 server records, decided card locked (0 enabled buttons), ' +
      'pending card keeps 3; offline keeps the demo data with 0 reads';
  });

  /* ---------------------------------------------------------------- *
   * 4.3 multi-user: sign-in page, user page, role gating (t13)
   * ---------------------------------------------------------------- */

  const ALL_PERMS = ['records.read', 'data.write', 'ingest.token.manage', 'intervene.decide',
    'audit.read', 'audit.export', 'settings.write', 'users.manage'];
  const EDITOR_PERMS = ['records.read', 'data.write', 'intervene.decide', 'audit.read', 'audit.export'];
  const VIEWER_PERMS = ['records.read', 'audit.read', 'audit.export'];

  const authDocText = (document) => {
    const parts = [];
    const walk = (node) => {
      if (!node) return;
      if (typeof node.value === 'string') parts.push('value=' + node.value);
      const kids = node.children || [];
      parts.push(String(node.textContent === undefined ? '' : node.textContent));
      kids.forEach(walk);
    };
    walk(document.body);
    return parts.join(' ');
  };

  const authHealth = (mode) => () => ({
    status: 200,
    data: { name: 'TraceLens', status: 'ok', mode, schema_version: '1.1', auth: { required: mode === 'team', mode } },
    headers: { 'x-request-id': 'req-health' }
  });

  const authStatusPayload = (mode) => () => ({
    status: 200,
    data: {
      mode, required: mode === 'team', roles: ['admin', 'editor', 'viewer'],
      bootstrap_needed: false, bootstrap_needed_note: '',
      cookie: 'tl_session', csrf_header: 'X-CSRF-Token', token_header: 'X-TraceLens-Token',
      ui: { show_login: mode === 'team' }
    },
    headers: {}
  });

  const authMePayload = (role, permissions, username) => ({
    actor_id: username || role, role, username: username || role, workspace_id: 'default',
    mode: 'team', language: 'zh', permissions, local: false,
    must_change_password: false, expires_at: 2000000000, audit_actor_role: 'owner',
    ui: { show_login: true, show_users: permissions.indexOf('users.manage') >= 0,
      show_intervene: permissions.indexOf('intervene.decide') >= 0, show_settings: true,
      show_data_write: permissions.indexOf('data.write') >= 0, show_ingest_token: false }
  });

  const authLoginPayload = (role, permissions, username, token, csrf) => ({
    status: 200,
    data: {
      user: authMePayload(role, permissions, username),
      session: { expires_at: 2000000000, expires_in: 28800, sid: 'sid-sc' },
      csrf_token: csrf, token, mode: 'team', request_id: 'req-login',
      cookie: { name: 'tl_session', value: 'cookie-' + role, http_only: true, same_site: 'Lax', path: '/', max_age: 28800 }
    },
    headers: { 'x-request-id': 'req-login' }
  });

  function authBoot(handlers, search) {
    const inst = ivBoot(search || '?probe=1');
    const log = [];
    const map = Object.assign({
      health: authHealth('team'),
      authStatus: authStatusPayload('team')
    }, handlers || {});
    inst.ui.setApiTransport((endpoint, apiPath, body) => {
      log.push({ endpoint, apiPath, body });
      const fn = map[endpoint];
      if (!fn) return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-sc' } };
      return fn(endpoint, apiPath, body);
    });
    inst.handlers = map;
    return Object.assign(inst, { authLog: log });
  }

  async function signedInAs(inst, role, permissions, username, token, csrf) {
    const loginLog = [];
    const map = Object.assign({}, inst.handlers, {
      authLogin: () => { loginLog.push(1); return authLoginPayload(role, permissions, username, token, csrf); },
      authMe: () => ({ status: 200, data: authMePayload(role, permissions, username), headers: {} }),
      authPermissions: () => ({
        status: 200,
        data: {
          roles: ['admin', 'editor', 'viewer', 'local'], permissions: ALL_PERMS,
          matrix: { admin: ALL_PERMS, editor: permissions === EDITOR_PERMS ? EDITOR_PERMS : ALL_PERMS, viewer: VIEWER_PERMS, local: ALL_PERMS.slice(0, 7) }
        },
        headers: {}
      }),
      records: () => ({ status: 200, data: { records: (inst.ui.state.records || []).filter((r) => r.requires_action === true).slice(0, 2), meta: { count: 2, synthetic: true } }, headers: {} })
    });
    inst.ui.setApiTransport((endpoint, apiPath, body) => {
      inst.authLog.push({ endpoint, apiPath, body });
      const fn = map[endpoint];
      if (!fn) return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-sc' } };
      return fn(endpoint, apiPath, body);
    });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const out = await inst.ui.authLogin(username || role, 'a-long-passphrase-1');
    await inst.ui.syncRecordsFromApi();
    return { login: out, loginLog };
  }

  check('4.3 auth plumbing: one cookie name, one CSRF header, password inputs, no persisted secrets', () => {
    const need = ["AUTH_COOKIE_NAME = 'tl_session'", "AUTH_CSRF_HEADER = 'X-CSRF-Token'",
      "credentials: 'same-origin'", "authField('auth-password', 'password', 'password'",
      "'current-password'", "'new-password'"];
    const missing = need.filter((needle) => appSource.indexOf(needle) < 0);
    if (missing.length) throw new Error('auth plumbing literals missing: ' + missing.join(' | '));
    const persisted = [...appSource.matchAll(/localStorage\.[a-zA-Z]+\(\s*['"]([^'"]*)['"]\s*,\s*([^)]*)\)/g)]
      .filter((m) => /token|password|csrf|session/i.test(m[1] + m[2]));
    if (persisted.length) throw new Error('credentials must stay in memory, found localStorage writes: ' + persisted.length);
    const shell = ['id="auth-panel"', 'data-hook="auth-panel"', 'id="users-panel"', 'data-hook="users-panel"',
      'data-auth-state="unchecked"', 'data-auth-blocked="false"'];
    const shellMissing = shell.filter((needle) => indexSource.indexOf(needle) < 0);
    if (shellMissing.length) throw new Error('index.html shell is missing: ' + shellMissing.join(' | '));
    const { ui } = ivBoot();
    ['auth', 'can', 'authLogin', 'authLogout', 'authUsers', 'authUserCreate', 'authUserSet',
      'authOpenUsers', 'authChangePassword', 'authProbe', 'authPermissionMatrix'].forEach((name) => {
        if (typeof ui[name] !== 'function') throw new Error('public auth surface misses ' + name);
      });
    return 'cookie tl_session + X-CSRF-Token + password fields (new-password on create), 0 localStorage secrets, shell hooks + 11 auth methods';
  });

  check('4.3 offline (file://): no login page, no auth requests, the demo keeps every entry', async () => {
    const { ui, document } = ivBoot('?probe=1&api=off');
    await settle();
    await settle();
    const a = ui.auth();
    if (a.mode !== 'forced_offline') throw new Error('offline auth mode=' + a.mode);
    if (a.required !== false) throw new Error('offline page demands a sign-in');
    if (a.state !== 'offline') throw new Error('offline auth state=' + a.state);
    if (a.permissions.indexOf('intervene.decide') < 0) throw new Error('offline lost the local decide permission');
    const panel = document.getElementById('auth-panel');
    if (!panel || panel.hidden !== true) throw new Error('the login page is visible with no server');
    if (ivFind(document, '[data-hook="auth-panel"]').length) throw new Error('the login page claims its visibility hook while hidden');
    if (ivFind(document, '[data-hook="auth-login-form"]').length) throw new Error('a login form rendered offline');
    if (ivFind(document, '[data-hook="auth-logout"]').length) throw new Error('a logout button rendered offline');
    const enabled = ivFind(document, '[data-hook="intervene-button"][data-enabled="true"]').length;
    if (enabled !== 12) throw new Error('offline intervention entries changed: ' + enabled);
    if (ui.probe().rendered.usersPanel) throw new Error('the user page rendered offline');
    return 'offline: auth state=offline, 0 login/logout nodes, 12 enabled buttons (unchanged), 0 users panels';
  });

  check('4.3 team mode signed out: the sign-in page is the page, and 401 is shown as copy', async () => {
    const inst = authBoot({
      authMe: () => ({ status: 401, data: { code: 'AUTH_REQUIRED', message: 'Not signed in', detail: {}, request_id: 'req-sc' }, headers: {} }),
      records: () => ({ status: 401, data: { code: 'AUTH_REQUIRED', message: 'Not signed in', detail: {}, request_id: 'req-sc' }, headers: {} })
    });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const a = inst.ui.auth();
    if (a.state !== 'signed_out') throw new Error('signed-out state=' + a.state);
    if (a.required !== true) throw new Error('team mode did not report required=true');
    if ((inst.ui.state.records || []).length !== 0) throw new Error('record content stayed visible while signed out: ' + (inst.ui.state.records || []).length);
    const app = inst.document.getElementById('app');
    const main = inst.document.getElementById('main');
    if (!main || main.getAttribute('data-auth-blocked') !== 'true') throw new Error('the main region is not blocked while signed out');
    if (main.getAttribute('aria-hidden') !== 'true') throw new Error('the main region is not aria-hidden while signed out');
    const panel = ivFind(inst.document, '[data-hook="auth-panel"]')[0];
    if (!panel || panel.hidden !== false) throw new Error('the sign-in page is not visible');
    if (ivFind(inst.document, '[data-hook="auth-login-form"]').length !== 1) throw new Error('no single login form');
    const pass = ivFind(inst.document, '[data-hook="auth-password"]');
    if (pass.length !== 1) throw new Error('expected exactly one password input, found ' + pass.length);
    if (pass[0].getAttribute('type') !== 'password') throw new Error('the password field is not type=password');
    if (pass[0].getAttribute('autocomplete') !== 'current-password') throw new Error('the password field must ask for current-password');
    if (ivFind(inst.document, '[data-hook="intervene-button"]').length) throw new Error('intervention controls exist while signed out');
    if (app && app.getAttribute('data-auth-state') !== 'signed_out') throw new Error('app data-auth-state=' + (app && app.getAttribute('data-auth-state')));
    const probe = inst.ui.probe();
    if (probe.rendered.authLoginForms !== 1) throw new Error('probe.authLoginForms=' + probe.rendered.authLoginForms);
    return 'signed out: state=signed_out, main aria-hidden, 1 login form (1 password field, current-password), 0 intervene buttons, records=0';
  });

  check('4.3 sign-in: the role and CSRF come from the server, and no password or token is kept on the page', async () => {
    const inst = authBoot({});
    const secret = 'correct-horse-battery-42';
    const token = 'tok-never-rendered-9f3a';
    const res = await signedInAs(inst, 'editor', EDITOR_PERMS, 'ops-li', token, 'csrf-sc-1');
    if (!res.login || res.login.ok !== true) throw new Error('sign-in did not succeed: ' + JSON.stringify(res.login));
    const a = inst.ui.auth();
    if (a.state !== 'signed_in') throw new Error('state after sign-in=' + a.state);
    if (a.role !== 'editor') throw new Error('role after sign-in=' + a.role);
    if (a.csrf_present !== true) throw new Error('the CSRF secret did not reach the page');
    if (a.permissions.indexOf('intervene.decide') < 0) throw new Error('editor must be able to decide');
    if (a.permissions.indexOf('users.manage') >= 0) throw new Error('editor must not manage users');
    const loginCall = inst.authLog.find((c) => c.endpoint === 'authLogin');
    if (!loginCall) throw new Error('no login request was sent');
    if (loginCall.apiPath.startsWith('/')) throw new Error('login path must stay relative: ' + loginCall.apiPath);
    if (loginCall.body.password !== secret && loginCall.body.password !== 'a-long-passphrase-1') throw new Error('login body lost the password field');
    const pass = ivFind(inst.document, '[data-hook="auth-password"]')[0];
    if (pass && pass.value) throw new Error('the password stayed in the input after sign-in');
    const text = authDocText(inst.document);
    if (text.indexOf('a-long-passphrase-1') >= 0) throw new Error('the password is rendered on the page');
    if (text.indexOf(token) >= 0) throw new Error('the API token is rendered on the page');
    const probe = inst.ui.probe();
    if (probe.auth.state !== 'signed_in') throw new Error('probe.auth.state=' + probe.auth.state);
    if (probe.rendered.authUserChip !== 1) throw new Error('probe.rendered.authUserChip=' + probe.rendered.authUserChip);
    if (inst.ui.can('users.manage') === true) throw new Error('can() grants users.manage to an editor');
    return 'sign-in: role/permissions/CSRF from the server, relative login path, 0 password/token residue in the DOM, chip rendered';
  });

  check('4.3 viewer: no intervention entry at all, the users entry is hidden, a 403 is shown not swallowed', async () => {
    const inst = authBoot({
      users: () => ({ status: 403, data: { code: 'ROLE_FORBIDDEN', message: 'Role may not do this', detail: { permission: 'users.manage' }, request_id: 'req-sc' }, headers: {} })
    });
    await signedInAs(inst, 'viewer', VIEWER_PERMS, 'watcher', 'tok-viewer-1', 'csrf-sc-2');
    const enabled = ivFind(inst.document, '[data-hook="intervene-button"][data-enabled="true"]').length;
    if (enabled !== 0) throw new Error('a viewer sees ' + enabled + ' enabled intervention buttons');
    const boxes = ivFind(inst.document, '[data-hook="intervene"]');
    if (!boxes.length) throw new Error('the option list disappeared for the viewer (reading is allowed)');
    if (boxes.some((b) => b.getAttribute('data-decidable') === 'true')) throw new Error('a box stays decidable for a viewer');
    if (!ivFind(inst.document, '[data-hook="intervene-role-notice"]').length) throw new Error('no plain-language note explains why the entry is gone');
    const usersBtn = ivFind(inst.document, '[data-hook="auth-users-open"]')[0];
    if (!usersBtn) throw new Error('the users entry is missing from the chip');
    if (usersBtn.hidden !== true || usersBtn.getAttribute('data-auth-allowed') !== 'false') throw new Error('the users entry is not hidden for a viewer');
    const out = await inst.ui.authUsers();
    if (out.ok !== false) throw new Error('the users read did not fail for a viewer');
    if (inst.ui.state.auth.usersError !== 'auth.error.role_forbidden') throw new Error('the 403 was not mapped to copy: ' + inst.ui.state.auth.usersError);
    const denial = ivFind(inst.document, '[data-hook="auth-denial"]')[0];
    if (!denial) throw new Error('the 403 has no visible place on the page');
    if (denial.getAttribute('data-i18n') !== 'auth.error.role_forbidden') throw new Error('the denial is not i18n copy: ' + denial.getAttribute('data-i18n'));
    if (!denial.textContent) throw new Error('the denial node is empty');
    if (ivFind(inst.document, '[data-hook="users-panel"]').length) throw new Error('the user page rendered for a viewer');
    if (inst.ui.state.auth.denials < 1) throw new Error('the denial is not counted for the rig');
    return 'viewer: 0 enabled buttons, option list readable, role notice shown, users entry hidden, 403 -> auth.error.role_forbidden';
  });

  check('4.3 admin: the user page creates, changes role, disables and resets over the POST sub-resources', async () => {
    const users = [
      { id: 'usr-1', username: 'alice', display_name: 'Alice', role: 'editor', status: 'active', must_change_password: false, last_login_at: '2026-11-09T10:00:00Z' },
      { id: 'usr-2', username: 'bob', display_name: 'Bob', role: 'viewer', status: 'active', must_change_password: true, last_login_at: null }
    ];
    const list = () => ({ status: 200, data: { data: users.slice(), count: users.length, meta: { mode: 'team' } }, headers: {} });
    const inst = authBoot({
      users: (endpoint, apiPath, body) => {
        if (apiPath === 'api/users') {
          if (!body) return list();
          users.push({ id: 'usr-3', username: body.username, role: body.role, status: 'active', must_change_password: true, last_login_at: null, initial_password: 'issued-once-9', initial_password_note: 'shown once' });
          return { status: 201, data: { ok: true, user: users[users.length - 1], note: 'shown once' }, headers: {} };
        }
        if (apiPath === 'api/users/usr-1/status') { users[0].status = body.status; return { status: 200, data: { ok: true, user: users[0] }, headers: {} }; }
        if (apiPath === 'api/users/usr-2/role') { users[1].role = body.role; return { status: 200, data: { ok: true, user: users[1] }, headers: {} }; }
        if (apiPath === 'api/users/usr-2/password') { return { status: 200, data: { ok: true, user: Object.assign({}, users[1], { initial_password: 'reset-once-7' }) }, headers: {} }; }
        return { status: 405, data: { code: 'METHOD_NOT_ALLOWED', message: 'no', detail: {}, request_id: 'req-sc' }, headers: {} };
      }
    });
    await signedInAs(inst, 'admin', ALL_PERMS, 'root', 'tok-admin-1', 'csrf-sc-3');
    const usersBtn = ivFind(inst.document, '[data-hook="auth-users-open"]')[0];
    if (!usersBtn || usersBtn.hidden !== false) throw new Error('the users entry is not available to an admin');
    usersBtn.click();
    await settle();
    await settle();
    if (ivFind(inst.document, '[data-hook="user-row"]').length !== 2) throw new Error('user rows=' + ivFind(inst.document, '[data-hook="user-row"]').length);
    if (ivFind(inst.document, '[data-hook="user-create-form"]').length !== 1) throw new Error('no create form');
    if (ivFind(inst.document, '[data-hook="user-role"]').length !== 2) throw new Error('no per-row role select');
    if (ivFind(inst.document, '[data-hook="users-issued"]').length) throw new Error('an issued password is shown before one was issued');
    usersBtn.click();
    const statusBtn = ivFind(inst.document, '[data-hook="user-status"][data-user="usr-1"]')[0];
    if (!statusBtn) throw new Error('no disable control');
    if (statusBtn.getAttribute('data-status') !== 'active') throw new Error('the row status attribute is wrong');
    statusBtn.click();
    await settle();
    await settle();
    const statusCall = inst.authLog.find((c) => c.apiPath === 'api/users/usr-1/status');
    if (!statusCall) throw new Error('disable did not POST the status sub-resource: ' + JSON.stringify(inst.authLog.map((c) => c.apiPath)));
    if (statusCall.body.status !== 'disabled') throw new Error('disable body=' + JSON.stringify(statusCall.body));
    if (ivFind(inst.document, '[data-hook="user-status"][data-user="usr-1"][data-status="disabled"]').length !== 1) throw new Error('the row did not repaint to disabled');
    await inst.ui.authUserSet('usr-2', 'role', { role: 'editor' });
    await settle();
    if (!inst.authLog.some((c) => c.apiPath === 'api/users/usr-2/role' && c.body.role === 'editor')) throw new Error('role change did not reach the server');
    await inst.ui.authUserCreate({ username: 'carol', role: 'viewer', password: '' });
    await settle();
    await settle();
    const createCall = inst.authLog.find((c) => c.apiPath === 'api/users' && c.body && c.body.username === 'carol');
    if (!createCall) throw new Error('create did not POST api/users');
    if (ivFind(inst.document, '[data-hook="user-row"]').length !== 3) throw new Error('rows after create=' + ivFind(inst.document, '[data-hook="user-row"]').length);
    const issued = ivFind(inst.document, '[data-hook="users-issued"]')[0];
    if (!issued || issued.textContent !== 'issued-once-9') throw new Error('the one-time password is not shown once');
    if (inst.ui.state.auth.feedback !== 'auth.feedback.user_created_shown_once') throw new Error('feedback=' + inst.ui.state.auth.feedback);
    const deleteCall = inst.authLog.find((c) => /delete|remove/.test(c.apiPath));
    if (deleteCall) throw new Error('the page offered a delete path: ' + deleteCall.apiPath);
    if (authDocText(inst.document).indexOf('a-long-passphrase-1') >= 0) throw new Error('a password leaked into the page text');
    return 'admin: 3 rows after create, POST api/users + /status + /role, one-time password shown once, no delete path, no password residue';
  });

  check('4.3 the role matrix is read from the server: the page renders it and never invents permissions', async () => {
    if (["'records.read'", "'intervene.decide'", "'users.manage'", "'audit.read'", "'settings.write'"].some((lit) => appSource.indexOf(lit) >= 0)) {
      throw new Error('permission names must be assembled from parts, not written as dotted literals');
    }
    const matrix = {
      admin: ALL_PERMS,
      editor: EDITOR_PERMS,
      viewer: VIEWER_PERMS,
      local: ALL_PERMS.slice(0, 7)
    };
    const inst = authBoot({
      authPermissions: () => ({ status: 200, data: { roles: ['admin', 'editor', 'viewer', 'local'], permissions: ALL_PERMS, matrix }, headers: {} })
    });
    await signedInAs(inst, 'admin', ALL_PERMS, 'root', 'tok-admin-2', 'csrf-sc-4');
    const got = await inst.ui.authPermissionMatrix();
    if (!got || got.viewer.join(',') !== VIEWER_PERMS.join(',')) throw new Error('the page did not take the published matrix: ' + JSON.stringify(got));
    if (inst.ui.can('users.manage') !== true) throw new Error('admin lost users.manage from the published matrix');
    if (inst.ui.state.auth.matrix === null) throw new Error('matrix not stored');
    await inst.ui.authOpenUsers(true);
    await settle();
    const table = ivFind(inst.document, '[data-hook="auth-matrix"]')[0];
    if (!table) throw new Error('the admin page does not show the matrix');
    const rows = ivFind(inst.document, '[data-hook="auth-matrix-row"]');
    if (rows.length !== ALL_PERMS.length) throw new Error('the matrix table is not filled: ' + rows.length + ' rows');
    const viewerCells = ivFind(inst.document, '[data-hook="auth-matrix-cell"][data-role="viewer"][data-allowed="true"]');
    if (viewerCells.length !== VIEWER_PERMS.length) throw new Error('viewer allowed cells=' + viewerCells.length);
    const decideRow = ivFind(inst.document, '[data-hook="auth-matrix-row"][data-permission="intervene.decide"]')[0];
    if (!decideRow) throw new Error('the matrix has no row for the decide capability');
    return 'matrix read from api/auth/permissions, ' + rows.length + ' rows rendered, ' + viewerCells.length + ' viewer grants, 0 dotted permission literals in app.js';
  });

  check('4.3 auth copy: every server auth code has zh and en text, and the auth domain is in sync', () => {
    const block = /var AUTH_ERROR_KEYS = \{([\s\S]*?)\};/.exec(appSource);
    if (!block) throw new Error('AUTH_ERROR_KEYS is missing');
    const map = [...block[1].matchAll(/([A-Z_]+):\s*'([^']+)'/g)].map((m) => ({ code: m[1], key: m[2] }));
    if (map.length < 25) throw new Error('the auth error map is too small: ' + map.length);
    const missing = map.filter((m) => zhKeys.indexOf(m.key) < 0 || enKeys.indexOf(m.key) < 0);
    if (missing.length) throw new Error('auth codes without copy: ' + missing.map((m) => m.code + '=' + m.key).join(', '));
    const zhAuth = zhKeys.filter((k) => k.split('.')[0] === 'auth');
    const enAuth = enKeys.filter((k) => k.split('.')[0] === 'auth');
    if (zhAuth.length !== enAuth.length) throw new Error('auth domain size differs: zh=' + zhAuth.length + ' en=' + enAuth.length);
    const zhSet = new Set(zhAuth);
    const drift = enAuth.filter((k) => !zhSet.has(k));
    if (drift.length) throw new Error('auth keys only in en: ' + drift.join(', '));
    const noHan = zhAuth.filter((k) => !CJK_HAN.test(String(zh[k.split('.')[0]][k.split('.').slice(1).join('.')])));
    if (noHan.length) throw new Error('auth zh copy is not Chinese: ' + noHan.join(', '));
    const enVals = enAuth.map((k) => String(en[k.split('.')[0]][k.split('.').slice(1).join('.')]));
    const cjkEn = enVals.filter((v) => CJK.test(v));
    if (cjkEn.length) throw new Error('auth en copy contains CJK: ' + cjkEn.length);
    if (enVals.join(' ').toLowerCase().indexOf('password: ') >= 0) throw new Error('en copy looks like it echoes a password');
    return map.length + ' auth codes mapped to copy, ' + zhAuth.length + ' auth keys in zh and en, zh Chinese / en zero CJK';
  });

  /* ---------------------------------------------------------------- *
   * 4.4 realtime (t14): SSE push, labelled degradation, SDK-shaped frames
   * ---------------------------------------------------------------- */

  check('4.4 realtime push: a pushed frame becomes a visible card, and a change updates the same card', async () => {
    const { ui, document } = boot({ search: '?probe=1', protocol: 'http:' });
    const reads = [];
    const serverRecords = [rtRecord('act-rt-a', 'running'), rtRecord('act-rt-b', 'succeeded')];
    ui.setApiTransport((endpoint, apiPath) => {
      if (endpoint === 'health') return rtHealth();
      if (endpoint === 'records') {
        reads.push(apiPath);
        return { status: 200, data: { records: serverRecords, meta: { count: serverRecords.length, synthetic: false, stream_head_seq: 3, ingested_count: 2 } } };
      }
      return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
    });
    await ui.probeApi();
    if (ui.apiMode().mode !== 'live') throw new Error('the rig never reached live mode');
    let request = null;
    /* sources are captured per check: an earlier check's boot chain can open
     * its own stream later, so the global "last" is never a reliable handle */
    const created = [];
    ui.setStreamFactory((url, opts) => { request = { url, opts }; const s = new FakeSource(url, opts); created.push(s); return s; });
    const opened = await ui.startRealtime();
    try {
      if (!opened) throw new Error('startRealtime did not open the stream in live mode');
      if (!request || request.url !== 'api/stream') throw new Error('stream request: ' + JSON.stringify(request));
      const src = created[0];
      src.open();
      src.emit('hello', { head_seq: 3, mode: 'local', event_types: ['hello'] }, 3);
      const before = (ui.state.records || []).length;
      src.emit('action.created', { action_id: 'act-rt-new', source_kind: 'sdk', token_id: 'tok-1', record: rtRecord('act-rt-new', 'running', { intent: 'pushed by the sdk' }) }, 4);
      src.emit('ingest.batch', { accepted: 1, created: 1, updated: 0, ids: ['act-rt-new'] }, 5);
      src.emit('action.updated', { action_id: 'act-rt-new', changed_fields: ['status', 'result_summary'], record: rtRecord('act-rt-new', 'succeeded', { intent: 'pushed by the sdk', result_summary: 'uploaded 3 files' }) }, 6);
      if ((ui.state.records || []).length !== before + 1) {
        throw new Error('the pushed record never entered the list: ' + (ui.state.records || []).length + ' vs ' + before);
      }
      const pushed = recordIn(ui, 'act-rt-new');
      if (!pushed || pushed.status !== 'succeeded') throw new Error('the pushed update did not land on the same record: ' + JSON.stringify(pushed && pushed.status));
      if (cardById(document.body, 'act-rt-new') === null) throw new Error('the pushed action has no card: the list was not repainted');
      if (pushed.user_intervention || pushed.execution_receipt) throw new Error('a pushed frame invented a decision or a receipt');
      const rt = ui.realtimeStatus();
      if (rt.mode !== 'live') throw new Error('the chip says: ' + rt.mode + ' / ' + rt.reason);
      if (rt.lastSeq !== 6 || rt.added !== 1 || rt.updated !== 1 || rt.batches !== 1) {
        throw new Error('push counters: ' + JSON.stringify({ seq: rt.lastSeq, added: rt.added, updated: rt.updated, batches: rt.batches }));
      }
      const chip = ivFind(document, '[data-hook="realtime"]')[0];
      if (!chip) throw new Error('no realtime chip in the topbar');
      if (chip.getAttribute('data-mode') !== 'live') throw new Error('chip mode: ' + chip.getAttribute('data-mode'));
      if (chip.getAttribute('data-seq') !== '6') throw new Error('chip cursor: ' + chip.getAttribute('data-seq'));
      if (!CJK_HAN.test(chip.textContent)) throw new Error('the chip is not showing the i18n copy: ' + chip.textContent);
      /* a frame the page has no card for is counted and explained, never merged
       * into the visible list and never swallowed silently */
      src.emit('ingest.stats', { accepted: 9, created: 4, window_s: 60 }, 7);
      if ((ui.state.records || []).length !== before + 1) throw new Error('a stats frame changed the list');
      if (ui.realtimeStatus().lastSeq !== 7) throw new Error('the cursor stopped at a frame without a card');
      return '1 push -> ' + rt.added + ' visible card + ' + rt.updated + ' merged update, chip live at cursor '
        + rt.lastSeq + ', a stats frame counted without touching the list';
    } finally {
      ui.stopRealtime('check done');
      if (!created.every((s) => s.closed)) throw new Error('the check left a stream source open');
    }
  });

  check('4.4 degradation: a dead stream reconnects with backoff, then polls and says so (never silently)', async () => {
    const { ui, document } = boot({ search: '?probe=1', protocol: 'http:' });
    const reads = [];
    const server = [rtRecord('act-rt-a', 'running'), rtRecord('act-rt-b', 'succeeded')];
    ui.setApiTransport((endpoint, apiPath) => {
      if (endpoint === 'health') return rtHealth();
      if (endpoint === 'records') {
        reads.push(apiPath);
        return { status: 200, data: { records: server, meta: { count: server.length, synthetic: false, stream_head_seq: 9, ingested_count: 1 } } };
      }
      return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
    });
    await ui.probeApi();
    const delays = [];
    const jobs = [];
    const created = [];
    ui.setRealtimeScheduler((ms, fn) => { delays.push(ms); jobs.push(fn); });
    ui.setStreamFactory((url) => { const s = new FakeSource(url); created.push(s); return s; });
    const rtChipMode = (document) => {
      /* the topbar is rebuilt on every render, so the chip is re-read, never cached */
      const node = ivFind(document, '[data-hook="realtime"]')[0];
      if (!node) throw new Error('the topbar lost the realtime chip');
      return node;
    };
    await ui.startRealtime();
    try {
      if (created.length !== 1) throw new Error('startRealtime opened ' + created.length + ' sources');
      const first = created[0];
      first.emit('action.created', { action_id: 'act-rt-a', record: rtRecord('act-rt-a', 'running') }, 9);
      first.fail();
      let st = ui.realtimeStatus();
      if (st.mode !== 'reconnect' || st.attempts !== 1) throw new Error('after the first failure: ' + JSON.stringify({ mode: st.mode, attempts: st.attempts }));
      if (delays[delays.length - 1] !== 800) throw new Error('the first backoff step: ' + JSON.stringify(delays));
      const chip1 = rtChipMode(document);
      if (chip1.getAttribute('data-mode') !== 'reconnect') throw new Error('the chip hides the reconnect: ' + chip1.getAttribute('data-mode'));
      jobs[jobs.length - 1]();
      const second = created[1];
      if (!second || second === first) throw new Error('the reconnect did not open a new source');
      if (second.url.indexOf('api/stream?since=9') !== 0) throw new Error('the reconnect drops the cursor: ' + second.url);
      second.fail();
      jobs[jobs.length - 1]();
      created[2].fail();
      jobs[jobs.length - 1]();
      created[3].fail();
      st = ui.realtimeStatus();
      if (st.mode !== 'poll' || st.reason !== 'stream_repeated_error') {
        throw new Error('after repeated failures the page must poll and say why: ' + JSON.stringify({ mode: st.mode, reason: st.reason }));
      }
      if (delays.join(',') !== '800,1600,3200') throw new Error('backoff steps: ' + delays.join(','));
      await settle();
      st = ui.realtimeStatus();
      if (st.polls < 1) throw new Error('the polling fallback never read the server');
      const chip2 = rtChipMode(document);
      if (chip2.getAttribute('data-mode') !== 'poll') throw new Error('the chip still claims a push: ' + chip2.getAttribute('data-mode'));
      if (!CJK_HAN.test(chip2.textContent)) throw new Error('the polling state has no copy: ' + chip2.textContent);
      if (st.lastSeq !== 9) throw new Error('polling did not take the stream cursor from the server meta: ' + st.lastSeq);
      /* polling merges through the same pipeline: a new record shows up without a push */
      server.push(rtRecord('act-rt-c', 'succeeded'));
      const merged = await ui.pollOnce();
      if (!merged) throw new Error('pollOnce did not merge anything');
      if (cardById(document.body, 'act-rt-c') === null) throw new Error('the polled record is not visible');
      /* every frame name the server can publish is handled: no silent drop */
      const dropped = ui.realtimeEventTypes().filter((name) => !ui.applyStreamEvent(name, { head_seq: 1 }, 1).applied);
      if (dropped.length) throw new Error('frames the page drops: ' + dropped.join(', '));
      if (!reads.length) throw new Error('the fallback never talked to the server');
      return 'backoff ' + delays.join('/') + ' ms, resume from ?since=9, then labelled polling ('
        + st.polls + ' reads, cursor ' + st.lastSeq + '), ' + created.length + ' sources closed';
    } finally {
      ui.stopRealtime('check done');
      if (!created.every((s) => s.closed)) throw new Error('the check left a stream source open');
    }
  });

  check('4.4 realtime copy: the chip text is real i18n, zh Chinese, en CJK-free, and every key is referenced', () => {
    const zhRt = zh.realtime || {};
    const enRt = en.realtime || {};
    const names = Object.keys(zhRt);
    if (!names.length) throw new Error('no realtime domain in zh.json');
    if (names.join(',') !== Object.keys(enRt).join(',')) throw new Error('realtime key sets differ between zh and en');
    const zhVals = names.map((k) => String(zhRt[k]));
    const enVals = names.map((k) => String(enRt[k]));
    const noHan = names.filter((k, i) => !CJK_HAN.test(zhVals[i]));
    if (noHan.length) throw new Error('realtime zh copy is not Chinese: ' + noHan.join(', '));
    const cjkEn = names.filter((k, i) => CJK.test(enVals[i]));
    if (cjkEn.length) throw new Error('realtime en copy contains CJK: ' + cjkEn.join(', '));
    if ((enVals.join(' ') + ' ' + zhVals.join(' ')).indexOf('http') >= 0) {
      throw new Error('realtime copy names an address: it has to describe the state, not a host');
    }
    if (!/\{ms\}/.test(String(zhRt['mode.poll'])) || !/\{ms\}/.test(String(enRt['mode.poll']))) {
      throw new Error('the polling copy must name the interval: a labelled fallback, not a silent retry');
    }
    const refs = [...appSource.matchAll(/'(realtime\.[a-z.]+)'/g)].map((m) => m[1]);
    if (!refs.length) throw new Error('app.js references no realtime copy');
    const missing = refs.filter((key) => {
      const leaf = key.slice('realtime.'.length);
      return !(zhRt[leaf] !== undefined && enRt[leaf] !== undefined);
    });
    if (missing.length) throw new Error('realtime copy referenced without text: ' + missing.join(', '));
    const unused = names.map((k) => 'realtime.' + k).filter((k) => refs.indexOf(k) < 0);
    if (unused.length) throw new Error('realtime copy nothing references: ' + unused.join(', '));
    /* the chip renders in the other language too, with the placeholder filled */
    const { ui, document } = boot({ search: '?probe=1&lang=en', protocol: 'http:' });
    const chip = ivFind(document, '[data-hook="realtime"]')[0];
    if (!chip) throw new Error('the en page has no realtime chip');
    const text = chip.textContent;
    if (/\{[a-z]+\}/.test(text)) throw new Error('unfilled placeholder in the en chip: ' + text);
    if (CJK.test(text)) throw new Error('the en chip contains CJK: ' + text);
    const zhChip = ivFind(boot({ search: '?probe=1', protocol: 'http:' }).document, '[data-hook="realtime"]')[0];
    if (!CJK_HAN.test(zhChip.textContent)) throw new Error('the zh chip is not showing Chinese copy: ' + zhChip.textContent);
    return names.length + ' realtime keys in zh and en, ' + refs.length + ' references, placeholder filled in both languages';
  });

  check('4.4 probe rig and the Python contract: probe().realtime describes the layer, and the frame names match ingest.py', () => {
    const { ui } = bootOnce();
    const rt = ui.probe().realtime;
    if (!rt) throw new Error('probe() does not publish the realtime layer');
    const need = ['mode', 'reason', 'lastSeq', 'events', 'added', 'updated', 'batches', 'polls',
      'pollIntervalMs', 'reconnectDelay', 'eventTypes', 'endpoints', 'scheduler', 'ring'];
    const missing = need.filter((k) => !(k in rt));
    if (missing.length) throw new Error('probe().realtime is missing ' + missing.join(', '));
    /* the default rig page is a file:// page: it must admit that it is blocked */
    if (rt.mode !== 'blocked' && rt.mode !== 'idle') throw new Error('a file:// page must say it is blocked, not pretend: ' + rt.mode);
    if (rt.endpoints.stream !== 'api/stream' || rt.endpoints.ingestStatus !== 'api/ingest/status') {
      throw new Error('the realtime endpoints left the allowlist: ' + JSON.stringify(rt.endpoints));
    }
    const ingest = read(path.join(PKG, 'agent_explainer', 'ingest.py'));
    const pyEvents = [...ingest.matchAll(/^EVENT_[A-Z_]+ = "([a-z_.]+)"$/gm)].map((m) => m[1]);
    if (pyEvents.length < 7) throw new Error('ingest.py declares fewer frame types than expected: ' + pyEvents.length);
    const ignored = pyEvents.filter((e) => rt.eventTypes.indexOf(e) < 0);
    if (ignored.length) throw new Error('the page ignores frames the server publishes: ' + ignored.join(', '));
    const invented = rt.eventTypes.filter((e) => pyEvents.indexOf(e) < 0);
    if (invented.length) throw new Error('the page listens for frames the server never defines: ' + invented.join(', '));
    const server = read(path.join(PKG, 'agent_explainer', 'server.py'));
    const moved = ['API_PATH_STREAM = "/api/stream"', 'API_PATH_INGEST_EVENTS = "/api/ingest/events"',
      'API_PATH_INGEST_STATUS = "/api/ingest/status"', 'API_PATH_INGEST_TOKENS = "/api/ingest/tokens"']
      .filter((line) => server.indexOf(line) < 0);
    if (moved.length) throw new Error('server.py moved an endpoint the page pins: ' + moved.join(' | '));
    return rt.eventTypes.length + ' frame types match ingest.py, 4 endpoints match server.py, '
      + Object.keys(rt).length + ' realtime fields in probe()';
  });

  /* ---------------------------------------------------------------- *
   * 4.5 (t15) settings centre: model configuration, language, data
   *
   * The rig answers GET/PUT /api/settings and POST /api/settings/test-llm
   * with fixtures shaped exactly like server.py's settings_view(), so the
   * panel is judged on what it paints and on the one request it sends - never
   * on a claim in a comment. The secret is the interesting part: the page sees
   * a mask, and what the user types is only ever a form value.
   * ---------------------------------------------------------------- */

  const SETTINGS_MASK = '••••••1234';
  const SETTINGS_TYPED = 'sekret-typed-by-the-rig-9F3';

  const settingsFixture = (over) => Object.assign({
    name: 'TraceLens Settings', schema_version: '1.0', mode: 'local',
    revision: 3, updated_at: '2026-01-01T00:00:00+00:00', updated_by: 'local',
    path: 'var/settings.json', note: 'x', load_errors: [],
    llm: {
      mode: 'llm', provider: 'openai_compat', base_url: '127.0.0.1:11434/v1',
      model: 'qwen2.5:7b', enabled: true, allow_outbound: false, timeout_s: 8,
      temperature: 0.3, max_tokens: 400, api_key_present: true,
      api_key_mask: SETTINGS_MASK, api_key_source: 'dpapi',
      api_key_fingerprint: 'sha256:a432706533bf',
      api_key_set_at: '2026-01-01T00:00:00+00:00'
    },
    ui: { language: 'zh', granularity: 'L2', storage_key: 'tracelens.ui_language' },
    data: { actions: ['reset', 'export_json', 'export_audit_csv'], reset_enabled: true, export_enabled: true, record_source: 'demo_fixture' },
    security: {
      key_storage: 'dpapi', key_storage_available: true, key_storage_reason: 'ok',
      secret_in_file: false, outbound: 'blocked', env_overrides: [],
      env_names: ['TRACELENS_LLM_API_KEY'], mask: '••••••',
      effect_notice: 'settings.effect.engine_only', file_mode_note: 'x', outbound_note: 'x'
    },
    permissions: { role: 'local', permission: 'settings.write', can_read: true, can_write: true, note: 'settings.note.can_write' },
    engine: {
      mode: 'llm', provider: 'openai_compat', model: 'qwen2.5:7b', language: 'zh',
      gates: { mode: true, settings_enabled: true, client_configured: true },
      llm_will_run: true, client_type: 'OpenAiCompatibleClient',
      endpoint: '127.0.0.1:11434/v1/chat/completions', llm_status: {},
      effect_notice: 'settings.effect.engine_only', note: 'x'
    },
    team: { mode: 'local', roles: ['admin', 'editor', 'viewer'], auth: { mode: 'local', required: false }, writable: true, note: 'settings.note.local' },
    llm_test: {
      endpoint: '/api/settings/test-llm', method: 'POST', required_permission: 'settings.write',
      probe_only: true, last: {}, note: 'settings.note.test_probe_only'
    },
    data_management: {
      reset: { endpoint: '/api/reset', method: 'POST', enabled: true, required_permission: 'settings.write', note: 'settings.note.reset_scope' },
      export: { endpoint: '/api/records', method: 'GET', enabled: true, audit_event: 'export', note: 'settings.note.export_scope' },
      audit: { endpoint: '/api/audit', method: 'GET', note: 'settings.note.audit_append_only' }
    },
    paths: { settings: '/api/settings', test_llm: '/api/settings/test-llm', reset: '/api/reset', records: '/api/records' },
    limits: { body_bytes: 262144, prompt_max_chars: 2000 },
    ui_language_storage_key: 'tracelens.ui_language'
  }, over || {});

  function settingsBoot(handlers, search) {
    const inst = ivBoot(search || '?probe=1');
    const log = [];
    const state = { view: settingsFixture({}) };
    const map = Object.assign({
      health: () => ({
        status: 200, headers: { 'x-request-id': 'req-health-sc' },
        data: {
          name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1',
          endpoints: ['/health', '/api/records', '/api/settings', '/api/settings/test-llm', '/api/reset'],
          auth: { required: false }
        }
      }),
      records: () => ({ status: 200, data: { records: inst.ui.state.records, meta: { count: (inst.ui.state.records || []).length } }, headers: {} }),
      authStatus: () => ({ status: 200, data: { mode: 'local', required: false, auth_enabled: false, request_id: 'req-sc' }, headers: {} }),
      authMe: () => ({ status: 200, data: authMePayload('local', ALL_PERMS, 'local'), headers: {} }),
      authPermissions: () => ({ status: 200, data: { roles: ['admin', 'editor', 'viewer', 'local'], permissions: ALL_PERMS, matrix: {} }, headers: {} }),
      settings: () => ({ status: 200, data: state.view, headers: { 'x-request-id': 'req-settings' } }),
      settingsTestLlm: () => ({ status: 200, data: { ok: true, code: 'LLM_OK', message: '连接成功：128 毫秒收到 42 个字符的回复。', latency_ms: 128, model: 'qwen2.5:7b', chars: 42, sample: '模型回答', endpoint: '127.0.0.1:11434/v1/chat/completions', detail: {}, effect_notice: 'settings.test_llm.probe_only', engine: state.view.engine }, headers: {} }),
      reset: () => ({ status: 200, data: { ok: true, records: 0, request_id: 'req-reset' }, headers: {} })
    }, handlers || {});
    inst.ui.setApiTransport((endpoint, apiPath, body, method) => {
      log.push({ endpoint, apiPath, body, method });
      const fn = map[endpoint];
      if (!fn) return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-sc' } };
      return fn(endpoint, apiPath, body, method);
    });
    inst.handlers = map;
    inst.settingsState = state;
    inst.settingsLog = log;
    return inst;
  }

  const domText = (node) => {
    let out = '';
    if (!node) return out;
    if (typeof node.textContent === 'string') out += node.textContent;
    (node.childNodes || []).forEach((child) => { out += domText(child); });
    return out;
  };

  check('4.5 the settings routes are on the allowlist, PUT is the one extra verb, and the shell hosts the panel', () => {
    const server = read(path.join(PKG, 'agent_explainer', 'server.py'));
    const need = ['API_PATH_SETTINGS = "/api/settings"',
      'API_PATH_SETTINGS_TEST_LLM = "/api/settings/test-llm"',
      'API_PATH_RESET = "/api/reset"'];
    const moved = need.filter((line) => server.indexOf(line) < 0);
    if (moved.length) throw new Error('server.py moved an endpoint the page pins: ' + moved.join(' | '));
    const literals = ["settings: 'api/settings'", "settingsTestLlm: 'api/settings/test-llm'", "reset: 'api/reset'",
      "SETTINGS_KEY_KEEP = 'keep'", "SETTINGS_KEY_CLEAR = ''",
      "wanted === 'POST' || wanted === 'PUT' || wanted === 'GET'",
      "authFetch('settings', patch, { method: 'PUT' })",
      "if (isStr(opts.method)) merged.method = opts.method;",
      'id="settings-center"', 'data-hook="settings-center"'];
    const missing = literals.filter((needle) => appSource.indexOf(needle) < 0 && indexSource.indexOf(needle) < 0);
    if (missing.length) throw new Error('settings contract literals missing: ' + missing.join(' | '));
    /* the verb is validated in the one guarded request function, and PUT is
     * only ever sent to the settings endpoint (the server answers 405 for
     * every other route, which check 27 pins) */
    const verbs = (appSource.match(/wanted === '(GET|POST|PUT)'/g) || []).length;
    if (verbs !== 3) throw new Error('the verb allowlist is not GET/POST/PUT: ' + verbs);
    const putUses = (appSource.match(/\{ method: 'PUT' \}/g) || []).length;
    if (putUses !== 1) throw new Error('PUT must be requested from exactly one call site, found ' + putUses);
    if (/\bmethod:\s*['"]DELETE['"]/.test(appSource)) throw new Error('the page may not speak DELETE');
    const hooks = ['settingsPanel: doc.querySelectorAll(\'[data-hook="settings-center"]\').length'];
    const hooksMissing = hooks.filter((needle) => appSource.indexOf(needle) < 0);
    if (hooksMissing.length) throw new Error('probe() does not count the settings panel: ' + hooksMissing.join(' | '));
    return '3 routes pinned against server.py, verbs GET/POST/PUT in one guard, 1 PUT sender, probe hook counted';
  });

  check('4.5 the panel paints the settings view: mode, provider, address, masked key, engine state', async () => {
    const inst = settingsBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const before = inst.ui.settings();
    if (before.open !== false) throw new Error('the settings centre is open before anyone asked');
    if (inst.document.getElementById('settings-center').hidden !== true) throw new Error('the settings host is visible before it is opened');
    await inst.ui.settingsOpen(true);
    await settle();
    const panel = ivFind(inst.document, '[data-hook="settings-center"]')[0];
    if (!panel) throw new Error('the settings centre did not render after opening');
    const snap = inst.ui.settings();
    if (snap.loaded !== true) throw new Error('GET /api/settings was not read: ' + JSON.stringify(snap));
    if (snap.llmMode !== 'llm' || snap.engineMode !== 'llm' || snap.llmWillRun !== true) {
      throw new Error('the engine state is not the server\'s: ' + JSON.stringify(snap));
    }
    const modes = ivFind(inst.document, '[data-hook="settings-mode"]');
    if (modes.length !== 2) throw new Error('explanation mode radios = ' + modes.length + ', expected 2 (template + llm)');
    if (modes.filter((b) => b.checked === true).length !== 1) throw new Error('no single explanation mode is selected');
    if (modes.filter((b) => b.checked === true)[0].getAttribute('data-mode') !== 'llm') {
      throw new Error('the selected explanation mode does not match the settings view');
    }
    const provider = ivFind(inst.document, '[data-hook="settings-field"][data-field="provider"]')[0];
    if (!provider || (provider.childNodes || []).length !== 4) throw new Error('provider select is not the allowlist of 4');
    const address = ivFind(inst.document, '[data-hook="settings-field"][data-field="base_url"]')[0];
    if (!address || address.value !== '127.0.0.1:11434/v1') throw new Error('the base URL field is not prefilled from the view');
    const masks = ivFind(inst.document, '[data-hook="settings-key-mask"]');
    if (masks.length < 1) throw new Error('no masked key line on the panel');
    if (masks[0].textContent !== SETTINGS_MASK) throw new Error('the key line shows "' + masks[0].textContent + '" instead of the mask');
    const body = domText(inst.document.body);
    ['sekret', 'Bearer', 'Authorization', 'api_key'].forEach((needle) => {
      if (body.indexOf(needle) >= 0) throw new Error('the panel displays something that looks like a credential: ' + needle);
    });
    const secretInputs = ivFind(inst.document, '[data-hook="settings-secret"]');
    if (secretInputs.length !== 1) throw new Error('secret input count = ' + secretInputs.length);
    if (secretInputs[0].value) throw new Error('the secret field is prefilled with a value');
    if (secretInputs[0].getAttribute('placeholder') !== SETTINGS_MASK) throw new Error('the secret field does not show the mask as its placeholder');
    const fields = ivFind(inst.document, '[data-hook="settings-field"]');
    ['provider', 'base_url', 'model', 'timeout_s', 'temperature', 'max_tokens', 'allow_outbound'].forEach((name) => {
      if (!ivFind(inst.document, '[data-hook="settings-field"][data-field="' + name + '"]').length) {
        throw new Error('missing model field: ' + name);
      }
    });
    if (fields.length < 7) throw new Error('settings fields = ' + fields.length);
    const save = ivFind(inst.document, '[data-hook="settings-save"]')[0];
    if (!save || save.getAttribute('data-enabled') !== 'true') throw new Error('a writer sees a disabled save button');
    const notes = ivFind(inst.document, '[data-hook="settings-note"]');
    if (!notes.filter((n) => n.getAttribute('data-i18n') === 'settings.model.engine_will_run').length) {
      throw new Error('the panel does not say whether the model will run');
    }
    if (!notes.filter((n) => n.getAttribute('data-i18n') === 'settings.effect.engine_only').length) {
      throw new Error('the panel does not state that settings never touch recorded facts');
    }
    return 'panel from 1 GET: 2 mode radios (llm checked), 4 providers, 7 fields, mask ' + masks[0].textContent
      + ', secret field empty, 0 credential-looking strings';
  });

  check('4.5 saving is exactly one PUT with the key sentinel, and the typed secret is never read back', async () => {
    const inst = settingsBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.settingsOpen(true);
    await settle();
    const saved = settingsFixture({ revision: 4, llm: Object.assign({}, settingsFixture().llm, { model: 'deepseek-chat', base_url: '127.0.0.1:8080/v1', apiKey_mask: undefined }) });
    inst.settingsState.view = saved;
    let put = null;
    inst.handlers.settings = (endpoint, apiPath, body, method) => {
      put = { method, body, apiPath };
      return { status: 200, data: { settings: saved, changed: [{ field: 'model', before: 'qwen2.5:7b', after: 'deepseek-chat' }], engine: saved.engine, key_action: 'unchanged', effect_notice: 'settings.effect.engine_only' }, headers: {} };
    };
    ivFind(inst.document, '[data-hook="settings-field"][data-field="model"]')[0].value = 'deepseek-chat';
    ivFind(inst.document, '[data-hook="settings-field"][data-field="base_url"]')[0].value = '127.0.0.1:8080/v1';
    const out = await inst.ui.settingsSaveForm();
    if (!out || out.ok !== true) throw new Error('the save did not succeed: ' + JSON.stringify(out));
    if (!put) throw new Error('no PUT was sent');
    if (put.method !== 'PUT') throw new Error('the write verb is ' + put.method + ', expected PUT');
    if (put.apiPath !== 'api/settings') throw new Error('wrote to ' + put.apiPath);
    if (put.body.llm.apiKey !== 'keep') throw new Error('a blank secret field must send the keep sentinel, sent ' + JSON.stringify(put.body.llm.apiKey));
    if (put.body.llm.model !== 'deepseek-chat' || put.body.llm.base_url !== '127.0.0.1:8080/v1') {
      throw new Error('the edited fields did not reach the server: ' + JSON.stringify(put.body.llm));
    }
    if (put.body.llm.api_key !== undefined) throw new Error('the page invented a snake_case secret field');
    if (put.body.security !== undefined || put.body.revision !== undefined) throw new Error('the page tried to write server-owned sections');
    const puts = inst.settingsLog.filter((row) => row.method === 'PUT').length;
    if (puts !== 1) throw new Error('one save = ' + puts + ' PUTs');
    const snap = inst.ui.settings();
    if (snap.saves !== 1) throw new Error('save counter = ' + snap.saves);
    if (snap.feedback !== 'settings.center.saved') throw new Error('feedback after save = ' + snap.feedback);
    if (snap.model !== 'deepseek-chat' || snap.revision !== 4) throw new Error('the panel did not adopt the server answer: ' + JSON.stringify(snap));
    /* clearing the key sends the empty sentinel; typing sends the value, and
     * neither is ever echoed into the page */
    const clear = ivFind(inst.document, '[data-hook="settings-clear-key"]')[0];
    clear.checked = true;
    inst.settingsState.view = saved;
    put = null;
    await inst.ui.settingsSaveForm();
    if (!put || put.body.llm.apiKey !== '') throw new Error('clearing the secret must send "", sent ' + JSON.stringify(put && put.body.llm.apiKey));
    const secretBox = ivFind(inst.document, '[data-hook="settings-secret"]')[0];
    secretBox.value = SETTINGS_TYPED;
    clear.checked = false;
    put = null;
    await inst.ui.settingsSaveForm();
    if (!put || put.body.llm.apiKey !== SETTINGS_TYPED) throw new Error('a typed secret must be sent verbatim');
    const body = domText(inst.document.body);
    if (body.indexOf(SETTINGS_TYPED) >= 0) throw new Error('the typed secret was echoed into the page');
    const after = ivFind(inst.document, '[data-hook="settings-secret"]')[0];
    if (after.value === SETTINGS_TYPED) throw new Error('the secret field kept the typed value after saving');
    if (after.value) throw new Error('the secret field is not empty after saving: ' + after.value);
    const maskNode = ivFind(inst.document, '[data-hook="settings-key-mask"]')[0];
    if (maskNode.textContent.indexOf('••••') < 0) throw new Error('the key line is not a mask after saving: ' + maskNode.textContent);
    return '1 save = 1 PUT to api/settings; apiKey sentinel keep/""/typed; typed secret never echoed; mask kept';
  });

  check('4.5 a role without settings.write gets a read-only page, and the server 403 becomes a readable line', async () => {
    const readOnly = settingsFixture({
      permissions: { role: 'viewer', permission: 'settings.write', can_read: true, can_write: false, note: 'settings.note.readonly' },
      engine: { mode: 'template', model: '', language: 'zh', llm_will_run: false, effect_notice: 'settings.effect.engine_only' },
      team: { mode: 'team', roles: ['admin', 'editor', 'viewer'], auth: { mode: 'team', required: true }, writable: true, note: 'settings.note.team' }
    });
    const inst = settingsBoot({
      authMe: () => ({ status: 200, data: authMePayload('viewer', VIEWER_PERMS, 'viewa'), headers: {} }),
      authPermissions: () => ({ status: 200, data: { roles: ['admin', 'editor', 'viewer'], permissions: VIEWER_PERMS, matrix: {} }, headers: {} }),
      authStatus: () => ({ status: 200, data: { mode: 'team', required: true, auth_enabled: true, request_id: 'req-sc' }, headers: {} }),
      settings: () => ({ status: 200, data: readOnly, headers: {} }),
      settingsTestLlm: () => ({ status: 403, data: { code: 'ROLE_FORBIDDEN', message: 'no', detail: { role: 'viewer' }, request_id: 'req-sc' }, headers: {} })
    });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.settingsOpen(true);
    await settle();
    const snap = inst.ui.settings();
    if (snap.canWrite !== false) throw new Error('a viewer is reported as a writer');
    if (snap.role !== 'viewer') throw new Error('role=' + snap.role);
    if (ivFind(inst.document, '[data-hook="settings-save"]')[0].getAttribute('data-enabled') !== 'false') {
      throw new Error('the save button is enabled for a viewer');
    }
    if (ivFind(inst.document, '[data-hook="settings-test"]')[0].getAttribute('data-enabled') !== 'false') {
      throw new Error('the connection test is enabled for a viewer');
    }
    if (!ivFind(inst.document, '[data-hook="settings-note"][data-read-only="true"]').length) {
      throw new Error('the read-only state has no visible note');
    }
    if (ivFind(inst.document, '[data-hook="settings-field"]').length < 7) {
      throw new Error('the read-only view hides the fields it should still show');
    }
    const masks = ivFind(inst.document, '[data-hook="settings-key-mask"]');
    if (!masks.length || masks[0].textContent !== SETTINGS_MASK) throw new Error('a viewer lost the masked key line');
    /* the client-side gate is the courtesy; the server answer is the contract */
    const refused = await inst.ui.settingsTest({});
    if (refused.ok !== false || refused.code !== 'ROLE_FORBIDDEN') throw new Error('the viewer test was not refused: ' + JSON.stringify(refused));
    if (inst.ui.settings().denials < 1) throw new Error('a refused write is not counted as a denial');
    const denial = ivFind(inst.document, '[data-hook="settings-error"]')[0];
    if (!denial) throw new Error('the refusal has no visible place');
    if (denial.getAttribute('data-i18n') !== 'auth.error.role_forbidden') throw new Error('the 403 is not mapped to copy: ' + denial.getAttribute('data-i18n'));
    if (!denial.textContent) throw new Error('the denial node is empty');
    const engine = ivFind(inst.document, '[data-hook="settings-engine"]')[0];
    if (!engine) throw new Error('a viewer cannot see the engine state at all');
    if (ivFind(inst.document, '[data-hook="settings-save"]')[0].getAttribute('data-enabled') !== 'false') {
      throw new Error('a refused write re-enabled the save button');
    }
    const puts = inst.settingsLog.filter((row) => row.method === 'PUT').length;
    if (puts !== 0) throw new Error('a viewer sent ' + puts + ' PUTs (the page must not even try)');
    return 'viewer: read-only fields + mask, 0 PUTs, save/test disabled, 403 -> auth.error.role_forbidden, denials=1';
  });

  check('4.5 language switch: the interface follows the radio, ui.language is saved, and every settings key exists in zh and en', async () => {
    const inst = settingsBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.settingsOpen(true);
    await settle();
    const langs = ivFind(inst.document, '[data-hook="settings-lang"]');
    if (langs.length !== 2) throw new Error('language radios = ' + langs.length + ', expected zh + en');
    if (langs.filter((b) => b.checked === true)[0].getAttribute('data-lang') !== 'zh') throw new Error('the language radio does not follow the settings view');
    const en = langs.filter((b) => b.getAttribute('data-lang') === 'en')[0];
    en.checked = true;
    const patch = inst.ui.settingsReadForm();
    if (!patch.ui || patch.ui.language !== 'en') throw new Error('the form did not read the language choice: ' + JSON.stringify(patch));
    if (inst.ui.settingsApplyLanguage('en') !== 'en') throw new Error('applyUiLanguage(en) failed');
    const chrome = inst.document.documentElement.getAttribute('data-lang');
    if (chrome !== 'en') throw new Error('the page chrome is still data-lang=' + chrome);
    if (inst.ui.i18n.lang !== 'en') throw new Error('i18n.lang=' + inst.ui.i18n.lang);
    inst.ui.settingsApplyLanguage('zh');
    const zh = readJson(FILES.zh);
    const enDict = readJson(FILES.en);
    const zhKeys = dictKeys(zh);
    const enKeys = dictKeys(enDict);
    const settingsZh = zhKeys.filter((k) => k.indexOf('settings.') === 0);
    const missing = settingsZh.filter((k) => enKeys.indexOf(k) < 0);
    if (missing.length) throw new Error('settings keys missing from en.json: ' + missing.join(', '));
    const enOnly = enKeys.filter((k) => k.indexOf('settings.') === 0).filter((k) => zhKeys.indexOf(k) < 0);
    if (enOnly.length) throw new Error('en.json has settings keys zh.json does not: ' + enOnly.join(', '));
    const zhVals = dictValues(zh);
    const enVals = dictValues(enDict);
    const empty = settingsZh.filter((k) => !String(zhVals[k] || '').trim() || !String(enVals[k] || '').trim());
    if (empty.length) throw new Error('empty settings copy: ' + empty.join(', '));
    const cjkInEn = settingsZh.filter((k) => /[\u3400-\u9FFF\u3040-\u30FF]/.test(String(enVals[k])));
    if (cjkInEn.length) throw new Error('CJK in en settings copy: ' + cjkInEn.join(', '));
    const keys = zhKeys.filter((k) => k.indexOf('settings.') === 0);
    ['settings.error.llm_timeout', 'settings.error.llm_unreachable', 'settings.error.settings_url_insecure',
      'settings.error.settings_write_failed', 'settings.error.settings_file_invalid', 'settings.error.llm_outbound_blocked',
      'settings.note.can_write', 'settings.note.readonly', 'settings.note.team', 'settings.note.local',
      'settings.note.reset_scope', 'settings.note.export_scope', 'settings.note.audit_append_only',
      'settings.note.test_probe_only', 'settings.effect.engine_only', 'settings.test_llm.probe_only'].forEach((k) => {
        if (keys.indexOf(k) < 0) throw new Error('the settings page needs copy that does not exist: ' + k);
      });
    ['zh', 'en'].forEach((lang) => {
      inst.ui.setLang(lang);
      Object.keys(inst.ui.SETTINGS_ERROR_KEYS).forEach((code) => {
        const key = inst.ui.SETTINGS_ERROR_KEYS[code];
        const value = inst.ui.t(key);
        if (!value || value.indexOf('i18n?') === 0) throw new Error(lang + ' has no copy for ' + code + ' (' + key + ')');
      });
      Object.keys(inst.ui.SETTINGS_NOTE_KEYS).forEach((name) => {
        const value = inst.ui.t(inst.ui.SETTINGS_NOTE_KEYS[name]);
        if (!value || value.indexOf('i18n?') === 0) throw new Error(lang + ' has no copy for the note ' + name);
      });
    });
    const savedPatch = { llm: { mode: 'template' }, ui: { language: 'en' } };
    let body = null;
    inst.handlers.settings = (endpoint, apiPath, sent, method) => {
      body = { method, sent };
      return { status: 200, data: { settings: settingsFixture({ ui: { language: 'en', granularity: 'L2', storage_key: 'tracelens.ui_language' } }), changed: [], engine: settingsFixture().engine, key_action: 'unchanged' }, headers: {} };
    };
    const out = await inst.ui.settingsSave(savedPatch);
    if (!body || body.method !== 'PUT' || body.sent.ui.language !== 'en') throw new Error('the language save is not a PUT with ui.language: ' + JSON.stringify(body));
    if (out.ok !== true || inst.ui.settings().language !== 'en') throw new Error('the panel did not adopt the saved language');
    return 'radio -> ui.language=en in one PUT; chrome data-lang follows; '
      + keys.length + ' settings keys in zh + en, 0 empty, en CJK-free, '
      + Object.keys(inst.ui.SETTINGS_ERROR_KEYS).length + ' codes + '
      + Object.keys(inst.ui.SETTINGS_NOTE_KEYS).length + ' notes translate in both languages';
  });

  check('4.5 connection test: the result is readable in the user language, says probe-only, and survives a timeout', async () => {
    const inst = settingsBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.settingsOpen(true);
    await settle();
    const ok = await inst.ui.settingsTest({});
    if (!ok || ok.ok !== true || ok.code !== 'LLM_OK') throw new Error('the success path reported ' + JSON.stringify(ok));
    if (inst.ui.settings().tests !== 1) throw new Error('test counter = ' + inst.ui.settings().tests);
    const testRow = () => ivFind(inst.document, '[data-hook="settings-test-result"]')[0];
    const text = testRow().textContent;
    if (text.indexOf('42') < 0 || text.indexOf('128') < 0) throw new Error('the success line has no numbers the user can read: ' + text);
    const posts = inst.settingsLog.filter((row) => row.endpoint === 'settingsTestLlm');
    if (posts.length !== 1) throw new Error('one click = ' + posts.length + ' test requests');
    if (posts[0].method !== 'POST') throw new Error('the test verb is ' + posts[0].method);
    if (posts[0].apiPath !== 'api/settings/test-llm') throw new Error('the test endpoint is ' + posts[0].apiPath);
    if (posts[0].body && posts[0].body.apiKey !== undefined) throw new Error('the page tried to send a key with the test');
    const probeNote = ivFind(inst.document, '[data-hook="settings-note"][data-i18n="settings.test_llm.probe_only"]');
    if (!probeNote.length) throw new Error('the probe-only notice is missing from the test result');
    if (!probeNote[0].textContent) throw new Error('the probe-only notice is empty');
    const detail = ivFind(inst.document, '[data-hook="settings-test-result"] .settings__test-detail')[0];
    if (!detail || detail.textContent.indexOf('127.0.0.1:11434') < 0) throw new Error('the endpoint that was tested is not shown');
    /* a timeout has to land as a sentence, not as a spinner or a stack trace */
    inst.handlers.settingsTestLlm = () => ({
      status: 200,
      data: {
        ok: false, code: 'LLM_TIMEOUT', message: '连接模型端超时（0.5 秒没响应）：zz-raw-transport-string-zz',
        latency_ms: 500,
        model: 'qwen2.5:7b', chars: 0, sample: '', endpoint: '127.0.0.1:11434/v1/chat/completions',
        detail: { timeout_s: 0.5, raw: 'zz-raw-transport-string-zz', hint: '把超时调大' },
        effect_notice: 'settings.test_llm.probe_only', engine: settingsFixture().engine
      },
      headers: {}
    });
    inst.ui.setLang('zh');
    const timed = await inst.ui.settingsTest({ timeout_s: 0.5 });
    if (timed.ok !== false || timed.code !== 'LLM_TIMEOUT') throw new Error('the timeout was not reported: ' + JSON.stringify(timed));
    const zhLine = testRow().textContent;
    const zhCopy = inst.ui.t('settings.error.llm_timeout');
    if (zhLine.indexOf(zhCopy) < 0) throw new Error('the timeout line is not the dictionary sentence: ' + zhLine);
    inst.ui.setLang('en');
    const enLine = testRow().textContent;
    const enCopy = inst.ui.t('settings.error.llm_timeout');
    if (enLine.indexOf(enCopy) < 0) throw new Error('the timeout line does not follow the language: ' + enLine);
    if (enCopy.indexOf('timeout') < 0 && enCopy.indexOf('timed out') < 0) throw new Error('the English timeout copy is not readable: ' + enCopy);
    if (domText(inst.document.body).indexOf('zz-raw-transport-string-zz') >= 0) {
      const around = domText(inst.document.body).split('zz-raw-transport-string-zz')[0].slice(-220);
      throw new Error('the raw transport string leaked into the page, near: ' + around);
    }
    /* an insecure URL answer must be the security sentence, not a stack trace */
    inst.handlers.settings = () => ({ status: 400, data: { code: 'SETTINGS_URL_INSECURE', message: 'x', detail: { field: 'base_url' }, request_id: 'req-sc' }, headers: {} });
    const refused = await inst.ui.settingsSave({ llm: { mode: 'llm', base_url: '127.0.0.1:1/v1', model: 'm' } });
    if (refused.ok !== false || refused.code !== 'SETTINGS_URL_INSECURE') throw new Error('the insecure URL was not refused: ' + JSON.stringify(refused));
    const errNode = ivFind(inst.document, '[data-hook="settings-error"]')[0];
    if (!errNode || errNode.getAttribute('data-i18n') !== 'settings.error.settings_url_insecure') {
      throw new Error('the insecure URL answer is not mapped to copy: ' + (errNode && errNode.getAttribute('data-i18n')));
    }
    inst.ui.setLang('zh');
    if (ivFind(inst.document, '[data-hook="settings-error"]')[0].textContent.indexOf('https') < 0) {
      throw new Error('the Chinese answer lost the https hint the user needs');
    }
    return 'success line carries ms + chars, 1 POST per click, probe-only notice, endpoint shown; timeout -> dictionary sentence in zh + en (raw text hidden); insecure URL -> settings.error.settings_url_insecure';
  });

  check('4.5 data management and offline: export downloads what the server sent, reset needs a confirmation, no server means read-only', async () => {
    const inst = settingsBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.settingsOpen(true);
    await settle();
    const exported = await inst.ui.settingsExport();
    if (!exported || exported.ok !== true) throw new Error('export failed: ' + JSON.stringify(exported));
    const recordsCalls = inst.settingsLog.filter((row) => row.endpoint === 'records');
    if (!recordsCalls.length) throw new Error('export did not read GET /api/records');
    if (typeof inst.ui.state.settings.exportJson !== 'string' || inst.ui.state.settings.exportJson.indexOf('{') !== 0) {
      throw new Error('the exported payload is not the JSON the server sent');
    }
    const feedback = () => ivFind(inst.document, '[data-hook="settings-feedback"]')[0];
    if (feedback().getAttribute('data-i18n') !== 'settings.data.export_done') throw new Error('export feedback = ' + feedback().getAttribute('data-i18n'));
    if (!feedback().textContent) throw new Error('the export line is empty');
    const first = await inst.ui.settingsReset();
    if (first.ok !== false || first.code !== 'CONFIRM_REQUIRED') throw new Error('a reset without confirmation was accepted: ' + JSON.stringify(first));
    if (feedback().getAttribute('data-i18n') !== 'settings.data.reset_confirm') throw new Error('the confirmation prompt is not shown: ' + feedback().getAttribute('data-i18n'));
    if (inst.settingsLog.filter((row) => row.endpoint === 'reset').length !== 0) throw new Error('an unconfirmed reset still called the server');
    const second = await inst.ui.settingsReset(true);
    if (second.ok !== true) throw new Error('the confirmed reset failed: ' + JSON.stringify(second));
    const resetCalls = inst.settingsLog.filter((row) => row.endpoint === 'reset');
    if (resetCalls.length !== 1 || resetCalls[0].method !== 'POST') throw new Error('the confirmed reset is not one POST: ' + JSON.stringify(resetCalls));
    if (feedback().getAttribute('data-i18n') !== 'settings.data.reset_done') throw new Error('the reset result is not reported: ' + feedback().getAttribute('data-i18n'));
    const auditNote = ivFind(inst.document, '[data-hook="settings-note"][data-i18n="settings.data.audit_note"]');
    if (!auditNote.length) throw new Error('the panel does not say the audit log is append-only');
    /* offline: no server to write to, and the page says so instead of failing
     * a save with a mystery error */
    const off = ivBoot('?probe=1&api=off');
    await settle();
    await off.ui.settingsOpen(true);
    await settle();
    const offSnap = off.ui.settings();
    if (offSnap.online !== false) throw new Error('offline snapshot claims a transport');
    if (offSnap.loaded !== false) throw new Error('offline settings claim to be loaded');
    const offPanel = ivFind(off.document, '[data-hook="settings-center"]')[0];
    if (!offPanel) throw new Error('the settings centre is missing offline');
    if (!ivFind(off.document, '[data-hook="settings-note"][data-i18n="settings.center.offline"]').length) {
      throw new Error('offline mode does not say so on the settings page');
    }
    const offWriteControls = ivFind(off.document, '[data-hook="settings-save"], [data-hook="settings-test"], [data-hook="settings-reset"]');
    if (offWriteControls.length !== 0) {
      const live = offWriteControls.filter((n) => n.getAttribute('data-enabled') !== 'false').length;
      if (live) throw new Error('offline mode leaves ' + live + ' write controls enabled');
    }
    if (!ivFind(off.document, '[data-hook="settings-refresh"]').length) {
      throw new Error('offline settings offer no way to retry');
    }
    if (ivFind(off.document, '[data-hook="settings-field"]').length) {
      throw new Error('a model form rendered with no server to save to');
    }
    const offPuts = off.ui.state.settings.requests;
    if (offPuts !== 0) throw new Error('offline settings made ' + offPuts + ' requests');
    return 'export reads GET /api/records and reports the count; reset needs a confirmation (0 calls) then 1 POST; '
      + 'audit note shown; offline: 0 requests, the offline note + retry only, 0 writable controls, 0 form fields';
  });

  /* ---- 4.8 (t21): audit rail + export panel --------------------------- */

  const AUDIT_NEW_KEYS = [
    'audit.hint', 'audit.chip.chain_ok', 'audit.chip.chain_broken', 'audit.chip.count',
    'audit.chip.durable', 'audit.chip.memory', 'audit.filter.all',
    'audit.filter.decision.approve', 'audit.filter.decision.reject',
    'audit.filter.decision.modify', 'audit.row.time', 'audit.row.actor', 'audit.row.action',
    'audit.row.task', 'audit.row.decision', 'audit.row.hash', 'audit.decision.none',
    'audit.loading', 'audit.empty', 'audit.empty_filtered', 'audit.note.readonly',
    'audit.action.refresh', 'audit.action.more', 'audit.more_end', 'audit.aria.list',
    'audit.aria.refresh', 'audit.aria.more', 'audit.feedback.idle', 'audit.feedback.refreshed',
    'audit.feedback.loaded', 'audit.error.chain_broken', 'audit.error.query_invalid',
    'audit.error.route_not_found', 'audit.error.network', 'audit.error.timeout',
    'audit.error.offline', 'audit.error.generic',
    'export.hint', 'export.action.csv', 'export.action.pdf', 'export.action.print',
    'export.aria.csv', 'export.aria.pdf', 'export.aria.print', 'export.note.need_permission',
    'export.note.offline', 'export.note.no_popup', 'export.note.pdf_fallback',
    'export.state.none', 'export.state.last', 'export.feedback.idle', 'export.feedback.done',
    'export.feedback.print_opened', 'export.error.format_invalid', 'export.error.chain_broken',
    'export.error.query_invalid', 'export.error.reportlab_missing', 'export.error.route_not_found',
    'export.error.network', 'export.error.timeout', 'export.error.offline', 'export.error.generic'
  ];
  const auditEntry = (i) => ({
    seq: i + 1,
    ts: '2026-11-0' + ((i % 9) + 1) + 'T08:0' + (i % 10) + ':00Z',
    actor_id: (i % 2 === 0) ? 'local-user' : 'watcher',
    actor_role: 'owner',
    action_id: 'act-demo-000' + (i + 1),
    task_id: i === 0 ? 'task-77' : null,
    decision: (i === 0) ? 'approve' : ((i === 1) ? 'reject' : null),
    event: (i < 2) ? 'decision' : 'note',
    request_id: 'req-audit-' + i,
    reason: (i === 0) ? 'approve reason' : ((i === 1) ? 'reject reason' : 'note reason'),
    mode: 'local',
    prev_hash: 'sha256:' + '0'.repeat(64),
    hash: 'sha256:' + (i + 1).toString(16).padStart(64, '0')
  });
  const auditExportBody = '\uFEFFseq,ts,actor_id,decision,reason\n1,2026-11-01T08:00:00Z,local-user,approve,approve reason\n';

  function auditBoot(options, handlers, search) {
    const opts = options || {};
    const inst = ivBoot(search || '?probe=1');
    const log = [];
    const live = {
      total: (opts.total === undefined) ? 12 : opts.total,
      chainOk: opts.chainOk !== false,
      auditStatus: opts.auditStatus || 200,
      served: opts.served || 'csv',
      fallback: opts.fallback || '',
      note: opts.note || '',
      exportStatus: opts.exportStatus || 200,
      exportCode: opts.exportCode || 'AUDIT_CHAIN_BROKEN'
    };
    const map = Object.assign({
      health: () => ({
        status: 200, headers: { 'x-request-id': 'req-health-sc' },
        data: {
          name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1',
          endpoints: ['/health', '/api/records', '/api/audit', '/api/audit/export'],
          auth: { required: false },
          audit_export: {
            endpoint: '/api/audit/export', method: 'GET', formats: ['csv', 'html', 'pdf'],
            permission: 'audit.export', filters: ['actor_id', 'decision'], served: 0
          }
        }
      }),
      records: () => ({ status: 200, data: { records: inst.ui.state.records, meta: { count: (inst.ui.state.records || []).length } }, headers: {} }),
      authStatus: () => ({ status: 200, data: { mode: 'local', required: false, auth_enabled: false, request_id: 'req-sc' }, headers: {} }),
      authMe: () => ({ status: 200, data: authMePayload('local', ALL_PERMS, 'local'), headers: {} }),
      authPermissions: () => ({ status: 200, data: { roles: ['admin', 'editor', 'viewer', 'local'], permissions: ALL_PERMS, matrix: {} }, headers: {} }),
      audit: (endpoint, apiPath) => {
        log.push({ endpoint, apiPath });
        if (live.auditStatus !== 200) {
          return {
            status: live.auditStatus, headers: { 'x-request-id': 'req-audit' },
            data: { code: live.exportCode, message: 'Audit chain is broken at entry 3', detail: { count: 3 }, request_id: 'req-audit' }
          };
        }
        const limit = /limit=(\d+)/.test(apiPath) ? parseInt(/limit=(\d+)/.exec(apiPath)[1], 10) : 8;
        const offset = /offset=(\d+)/.test(apiPath) ? parseInt(/offset=(\d+)/.exec(apiPath)[1], 10) : 0;
        const count = Math.max(0, Math.min(limit, live.total - offset));
        return {
          status: 200, headers: { 'x-request-id': 'req-audit' },
          data: {
            entries: Array.from({ length: count }, (_, i) => auditEntry(offset + i)),
            chain: { ok: live.chainOk, count: 421, head: 'sha256:head', problems: live.chainOk ? [] : [{ code: 'hash_mismatch', index: 2, reason: 'entry 3' }] },
            meta: { mode: 'local', durable: true, count: 421 },
            jsonl: { path: 'var/log/audit.jsonl', count: 421, head: 'sha256:head', ok: live.chainOk, problems: [] }
          }
        };
      },
      auditExport: (endpoint, apiPath) => {
        log.push({ endpoint, apiPath });
        if (live.exportStatus !== 200) {
          return {
            status: live.exportStatus, headers: { 'x-request-id': 'req-export' },
            data: { code: live.exportCode, message: 'Audit chain is broken at entry 3', detail: { count: 3 }, request_id: 'req-export' }
          };
        }
        const html = live.served === 'html';
        return {
          status: 200,
          data: html ? '<!doctype html><html><body><table><tr><td>approve</td></tr></table></body></html>' : auditExportBody,
          headers: {
            'x-request-id': 'req-export',
            'x-tracelens-export-format': live.served,
            'x-tracelens-export-rows': '1',
            'x-tracelens-export-sha256': 'sha256:' + 'a'.repeat(64),
            'x-tracelens-export-filters': 'decision=approve;limit=500',
            'content-disposition': html ? 'inline; filename="tracelens-audit.html"' : 'attachment; filename="tracelens-audit.csv"',
            'content-type': html ? 'text/html; charset=utf-8' : 'text/csv; charset=utf-8'
          }
        };
      }
    }, handlers || {});
    const baseExport = map.auditExport;
    map.auditExport = (endpoint, apiPath) => {
      const res = baseExport(endpoint, apiPath);
      if (res && res.headers && live.fallback) res.headers['x-tracelens-export-fallback'] = live.fallback;
      if (res && res.headers && live.note) res.headers['x-tracelens-export-note'] = live.note;
      return res;
    };
    /* ?api=off means "no transport at all": the panel must notice that there is
     * nothing to talk to, exactly as it does in a browser with no server. */
    if (String(search || '').indexOf('api=off') < 0) {
      inst.ui.setApiTransport((endpoint, apiPath, body, method) => {
        const fn = map[endpoint];
        if (!fn) return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-sc' } };
        return fn(endpoint, apiPath, body, method);
      });
    }
    inst.handlers = map;
    inst.auditState = live;
    inst.auditLog = log;
    inst.auditCalls = (name) => log.filter((row) => row.endpoint === name);
    return inst;
  }

  check('4.8 audit panel: rows, chips, decision filter and paging all come from GET /api/audit', async () => {
    const inst = auditBoot({ total: 12 });
    const lang = inst.ui.lang();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const out = await inst.ui.auditRefresh();
    if (!out || out.ok !== true) throw new Error('the audit read did not succeed: ' + JSON.stringify(out));
    const rows = ivFind(inst.document, '[data-hook="audit-row"]');
    if (rows.length !== 8) throw new Error('audit rows = ' + rows.length + ', expected one page of 8 from the server');
    const first = rows[0];
    const hooks = ['audit-time', 'audit-actor', 'audit-action', 'audit-decision', 'audit-hash'];
    hooks.forEach((hook) => {
      const node = queryAll(first, '[data-hook="' + hook + '"]')[0];
      if (!node) throw new Error('row 1 has no ' + hook + ' field');
      const value = queryAll(node, '.audit__value')[0];
      if (!value || !String(value.textContent || '')) throw new Error(hook + ' shows nothing in row 1');
    });
    if (queryAll(first, '[data-hook="audit-decision"]')[0].getAttribute('data-decision') !== 'approve') {
      throw new Error('the decision of row 1 is not the server\'s approve');
    }
    const hashNode = queryAll(first, '[data-hook="audit-hash"]')[0];
    if (hashNode.getAttribute('data-hash') !== auditEntry(0).hash) {
      throw new Error('row 1 does not carry the entry hash for forensics');
    }
    const short = String(queryAll(hashNode, '.audit__value')[0].textContent || '');
    if (short.length >= 20) throw new Error('the hash is not a short code: ' + short);
    if (!String(queryAll(first, '[data-hook="audit-action"]')[0].textContent).includes('act-demo-0001')) {
      throw new Error('row 1 does not show which task the entry is about');
    }
    const chainChip = ivFind(inst.document, '[data-hook="audit-chain"]')[0];
    if (!chainChip || chainChip.getAttribute('data-ok') !== 'true') throw new Error('no intact-chain chip on a healthy chain');
    if (!String(chainChip.textContent)) throw new Error('the chain chip has no copy');
    const countChip = String(ivFind(inst.document, '[data-hook="audit-count"]')[0].textContent);
    if (countChip.indexOf('421') < 0) throw new Error('the count chip does not show the server count: ' + countChip);
    const storeChip = ivFind(inst.document, '[data-hook="audit-store"]')[0];
    if (storeChip.getAttribute('data-i18n') !== 'audit.chip.durable') throw new Error('a durable chain is not labelled as stored on disk');
    const snap = inst.ui.audit();
    /* 4.12 (H-01): the panel reads the first page by itself once it is on
     * screen, so a session that clicks refresh twice has 2 reads: the boot read
     * (autoLoads) and the manual one. Rows and counters stay the point. */
    if (snap.rows !== 8 || snap.autoLoads !== 1 || snap.refreshes !== 2 || snap.requests !== 2) {
      throw new Error('snapshot after the boot read + one manual refresh: ' + JSON.stringify(snap));
    }
    if (snap.canRead !== true || snap.canExport !== true) throw new Error('local mode lost an audit ability: ' + JSON.stringify(snap));
    if (snap.error !== '' || ivFind(inst.document, '[data-hook="audit-empty"]').length) throw new Error('a panel with rows shows an empty state');
    if (snap.more !== true) throw new Error('a full page should offer to load more');

    const reject = ivFind(inst.document, '[data-hook="audit-filter"][data-decision="reject"]')[0];
    if (!reject) throw new Error('no decision filter chip');
    reject.click();
    await settle();
    const filtered = inst.auditCalls('audit').slice(-1)[0];
    if (filtered.apiPath.indexOf('decision=reject') < 0) throw new Error('the filter chip did not send its decision: ' + filtered.apiPath);
    if (ivFind(inst.document, '[data-hook="audit-filter"][data-decision="reject"]')[0].getAttribute('data-active') !== 'true') {
      throw new Error('the active filter chip is not marked active');
    }
    const paged = await inst.ui.auditSetLimit(2);
    if (paged.ok !== true) throw new Error('the page-size change failed: ' + JSON.stringify(paged));
    const limitPath = inst.auditCalls('audit').slice(-1)[0].apiPath;
    if (limitPath.indexOf('limit=2') < 0) throw new Error('the page size never reached the request: ' + limitPath);
    const more = await inst.ui.auditMore();
    if (more.ok !== true) throw new Error('load more failed: ' + JSON.stringify(more));
    const morePath = inst.auditCalls('audit').slice(-1)[0].apiPath;
    if (morePath.indexOf('offset=2') < 0) throw new Error('load more did not ask for the next window: ' + morePath);
    if (inst.ui.audit().rows !== 4) throw new Error('load more appended the next window: ' + inst.ui.audit().rows);
    if (inst.ui.audit().loads !== 1) throw new Error('load-more counter: ' + inst.ui.audit().loads);
    const noMore = await inst.ui.auditSetLimit(50);
    if (noMore.ok !== true) throw new Error('the last-page read failed: ' + JSON.stringify(noMore));
    const moreBtn = ivFind(inst.document, '[data-hook="audit-more"]')[0];
    if (moreBtn.getAttribute('data-more') !== 'false' || moreBtn.getAttribute('data-enabled') !== 'false') {
      throw new Error('the last page still offers a live load-more button');
    }
    if (moreBtn.getAttribute('data-i18n') !== 'audit.more_end') throw new Error('the exhausted list does not say all loaded');
    return '8 rows from 1 GET (time/actor/task/decision/hash + chain chip 421), filter chip -> decision=reject, '
      + 'limit=2 then offset=2 appended to 4 rows, exhausted list -> audit.more_end';
  });

  check('4.8 export panel: CSV uses the server bytes, the PDF fallback is one readable line, print opens the allowlisted path', async () => {
    const inst = auditBoot({});
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await inst.ui.auditRefresh();
    const csv = ivFind(inst.document, '[data-hook="export-csv"]')[0];
    const pdf = ivFind(inst.document, '[data-hook="export-pdf"]')[0];
    const print = ivFind(inst.document, '[data-hook="export-print"]')[0];
    if (!csv || !pdf || !print) throw new Error('the export panel is missing an entry');
    [csv, pdf, print].forEach((b) => {
      if (b.getAttribute('data-enabled') !== 'true') throw new Error('an export entry is disabled for a role that may export');
      if (!String(b.textContent || '')) throw new Error('an export button has no label');
    });
    const out = await inst.ui.exportAudit('csv');
    if (!out || out.ok !== true) throw new Error('the CSV export failed: ' + JSON.stringify(out));
    if (out.format !== 'csv') throw new Error('the served format is ' + out.format);
    if (out.rows !== 1) throw new Error('the row count header was not read: ' + out.rows);
    if (out.filename !== 'tracelens-audit.csv') throw new Error('the filename comes from the server, got ' + out.filename);
    if (out.bytes !== auditExportBody.length) throw new Error('the download bytes are not what the server sent: ' + out.bytes);
    const sent = inst.auditCalls('auditExport').slice(-1)[0];
    if (sent.apiPath !== 'api/audit/export?format=csv&lang=' + inst.ui.lang()) throw new Error('the export path is ' + sent.apiPath);
    const snap = inst.ui.exportState();
    if (snap.count !== 1) throw new Error('export counter = ' + snap.count);
    if (snap.last.sha256.indexOf('sha256:') !== 0) throw new Error('no content fingerprint on the last export');
    if (snap.feedback !== 'export.feedback.done') throw new Error('export feedback = ' + snap.feedback);
    if (snap.error !== '') throw new Error('a clean export shows an error line');
    const lastNode = ivFind(inst.document, '[data-hook="export-last"]')[0];
    if (lastNode.getAttribute('data-visible') !== 'true' || !String(lastNode.textContent)) throw new Error('the last-export line is not shown');
    if (lastNode.getAttribute('data-rows') !== '1') throw new Error('the last-export line does not show the row count');

    inst.auditState.served = 'html';
    inst.auditState.fallback = 'reportlab is not installed: pip install reportlab';
    const pdfOut = await inst.ui.exportAudit('pdf');
    if (pdfOut.ok !== true) throw new Error('a missing PDF engine must not fail the export: ' + JSON.stringify(pdfOut));
    if (pdfOut.format !== 'html') throw new Error('the fallback did not serve the printable page: ' + pdfOut.format);
    if (!pdfOut.fallback) throw new Error('the fallback reason was dropped');
    const pdfSnap = inst.ui.exportState();
    if (pdfSnap.error !== 'export.note.pdf_fallback') throw new Error('the fallback is not a dictionary line: ' + pdfSnap.error);
    const note = ivFind(inst.document, '[data-hook="export-feedback"]')[0];
    if (note.getAttribute('data-i18n') !== 'export.note.pdf_fallback') throw new Error('the fallback line is not i18n copy');
    if (!String(note.textContent)) throw new Error('the fallback line renders empty');

    inst.auditState.served = 'csv';
    const printed = inst.ui.exportPrint('pdf');
    if (printed.path !== 'api/audit/export?format=html&lang=' + inst.ui.lang()) throw new Error('the print entry opens ' + printed.path);
    if (printed.ok !== false) throw new Error('the rig has no window.open, so the entry must report honestly');
    if (inst.ui.exportState().feedback !== 'export.note.no_popup') throw new Error('a blocked popup is not reported: ' + inst.ui.exportState().feedback);
    if (ivFind(inst.document, '[data-hook="export-need"]')[0].getAttribute('data-visible') !== 'false') {
      throw new Error('a role that may export is shown a permission warning');
    }

    /* a PDF built without a Chinese font silently drops Chinese glyphs: the
     * server ships that warning as a header and the page answers in the
     * user's language, never with the raw server sentence */
    inst.auditState.served = 'pdf';
    inst.auditState.fallback = '';
    inst.auditState.note = 'font_note: no CJK font, Chinese text may be dropped';
    const fontOut = await inst.ui.exportAudit('pdf');
    if (fontOut.ok !== true || fontOut.format !== 'pdf') throw new Error('the PDF export did not go through: ' + JSON.stringify(fontOut));
    if (!fontOut.note) throw new Error('the font note header was dropped');
    if (inst.ui.exportState().error !== 'export.note.pdf_font') throw new Error('the font warning is not dictionary copy: ' + inst.ui.exportState().error);
    const fontNote = ivFind(inst.document, '[data-hook="export-feedback"]')[0];
    if (fontNote.getAttribute('data-i18n') !== 'export.note.pdf_font') throw new Error('the font warning is not i18n copy');
    const fontText = String(fontNote.textContent || '');
    if (!fontText) throw new Error('the font warning renders empty');
    if (fontText.indexOf('no CJK font') >= 0) throw new Error('the raw server font note is pasted into the page');
    return 'csv bytes + filename + sha from 1 GET, pdf -> html fallback in dictionary copy, print path '
      + printed.path + ' with an honest no-popup note, pdf font warning -> export.note.pdf_font';
  });

  check('4.8 export follows the audit.export ability: disabled buttons carry a reason, and nothing leaves the page', async () => {
    const noExport = ['records.read', 'audit.read'];
    const inst = authBoot({});
    await signedInAs(inst, 'viewer', noExport, 'watcher', 'tok-viewer-t21', 'csrf-sc-t21');
    if (inst.ui.can('audit.export') === true) throw new Error('can() grants audit.export to a role without it');
    if (inst.ui.can('audit.read') !== true) throw new Error('the viewer lost the right to read the audit trail');
    const buttons = ivFind(inst.document, '[data-hook="export-csv"], [data-hook="export-pdf"], [data-hook="export-print"]');
    if (buttons.length !== 3) throw new Error('export entry count = ' + buttons.length);
    buttons.forEach((b) => {
      if (b.getAttribute('data-enabled') !== 'false' || b.getAttribute('disabled') === null) {
        throw new Error('an export button is enabled for a role without audit.export');
      }
    });
    const need = ivFind(inst.document, '[data-hook="export-need"]')[0];
    if (need.getAttribute('data-visible') !== 'true') throw new Error('the disabled state has no visible reason');
    if (need.getAttribute('data-i18n') !== 'export.note.need_permission') throw new Error('the reason is not the dictionary line: ' + need.getAttribute('data-i18n'));
    if (!String(need.textContent)) throw new Error('the reason line renders empty');
    const before = inst.authLog.filter((row) => row.endpoint === 'auditExport').length;
    const out = await inst.ui.exportAudit('csv');
    if (out.ok !== false || out.code !== 'ROLE_FORBIDDEN') throw new Error('a forbidden export did not fail cleanly: ' + JSON.stringify(out));
    const after = inst.authLog.filter((row) => row.endpoint === 'auditExport').length;
    if (after !== before) throw new Error('a forbidden export sent ' + (after - before) + ' request(s)');
    if (inst.ui.exportState().denials !== 1) throw new Error('the denial was not counted: ' + inst.ui.exportState().denials);

    const noRead = authBoot({
      audit: () => ({ status: 200, data: { entries: [auditEntry(0)], chain: { ok: true, count: 1, problems: [] }, meta: { durable: true } }, headers: {} })
    });
    await signedInAs(noRead, 'viewer', ['records.read'], 'reader', 'tok-reader-t21', 'csrf-sc-t21');
    const readCallsBefore = noRead.authLog.filter((row) => row.endpoint === 'audit').length;
    const readOut = await noRead.ui.auditRefresh();
    if (readOut.ok !== false || readOut.code !== 'ROLE_FORBIDDEN') throw new Error('a read without audit.read was not refused: ' + JSON.stringify(readOut));
    /* 4.12 (H-01): the boot read belongs to the profile that was signed in
     * when the page opened (it had audit.read); what must stay at zero is any
     * read asked for by the role that does not have it. */
    if (noRead.authLog.filter((row) => row.endpoint === 'audit').length !== readCallsBefore) {
      throw new Error('a forbidden read hit the server');
    }
    const readonly = ivFind(noRead.document, '[data-hook="audit-readonly"]')[0];
    if (!readonly) throw new Error('the audit panel has no read-only notice for a role that cannot read it');
    if (readonly.getAttribute('data-i18n') !== 'audit.note.readonly' || !String(readonly.textContent)) {
      throw new Error('the read-only notice is not readable copy');
    }
    ivFind(noRead.document, '[data-hook="audit-refresh"], [data-hook="audit-more"]').forEach((b) => {
      if (b.getAttribute('data-enabled') !== 'false') throw new Error('a read control is enabled for a role without audit.read');
    });
    return 'audit.export off: 3 disabled buttons + reason line, 0 requests, 1 denial; audit.read off: '
      + 'read-only notice + disabled controls';
  });

  check('4.8 audit panel states: empty, chain-broken and offline read from the dictionary, never raw server text', async () => {
    const empty = auditBoot({ total: 0 });
    await empty.ui.probeApi();
    await empty.ui.authProbe();
    const out = await empty.ui.auditRefresh();
    if (out.ok !== true) throw new Error('the empty read failed: ' + JSON.stringify(out));
    const emptyNode = ivFind(empty.document, '[data-hook="audit-empty"]')[0];
    if (!emptyNode || emptyNode.getAttribute('data-i18n') !== 'audit.empty') throw new Error('no dictionary empty state');
    if (!String(emptyNode.textContent)) throw new Error('the empty state renders empty');
    await empty.ui.auditFilter({ decision: 'reject' });
    const filtered = ivFind(empty.document, '[data-hook="audit-empty"]')[0];
    if (filtered.getAttribute('data-i18n') !== 'audit.empty_filtered') throw new Error('a filtered empty list does not say so: ' + filtered.getAttribute('data-i18n'));

    const broken = auditBoot({ auditStatus: 500 });
    await broken.ui.probeApi();
    await broken.ui.authProbe();
    const brokenOut = await broken.ui.auditRefresh();
    if (brokenOut.ok !== false || brokenOut.code !== 'AUDIT_CHAIN_BROKEN') throw new Error('a broken chain was not surfaced: ' + JSON.stringify(brokenOut));
    if (broken.ui.audit().status !== 'error') throw new Error('the panel is not in the error state');
    const errorNode = ivFind(broken.document, '[data-hook="audit-error"]')[0];
    if (!errorNode || errorNode.getAttribute('data-i18n') !== 'audit.error.chain_broken') throw new Error('the 500 has no dictionary line');
    const shown = String(errorNode.textContent || '');
    if (!shown) throw new Error('the error line renders empty');
    if (shown.indexOf('Audit chain is broken at entry 3') >= 0) throw new Error('the raw server message is pasted into the page');
    if (ivFind(broken.document, '[data-hook="audit-empty"]').length) throw new Error('a broken chain is shown as an empty list');

    const off = auditBoot({}, null, '?probe=1&api=off');
    await off.ui.probeApi();
    await off.ui.authProbe();
    const offOut = await off.ui.auditRefresh();
    if (offOut.ok !== false || offOut.offline !== true) throw new Error('offline read did not report offline: ' + JSON.stringify(offOut));
    if (off.ui.audit().requests !== 0) throw new Error('offline mode made ' + off.ui.audit().requests + ' audit requests');
    if (off.ui.audit().error !== 'audit.error.offline') throw new Error('offline audit error = ' + off.ui.audit().error);
    const offExport = await off.ui.exportAudit('csv');
    if (offExport.ok !== false || offExport.offline !== true) throw new Error('offline export did not report offline: ' + JSON.stringify(offExport));
    if (off.ui.exportState().reason !== 'export.error.offline') throw new Error('offline export reason = ' + off.ui.exportState().reason);
    if (off.auditLog.length) throw new Error('offline mode reached the network: ' + off.auditLog.length + ' calls');

    const zh = readJson(path.join(HERE, 'i18n', 'zh.json'));
    const en = readJson(path.join(HERE, 'i18n', 'en.json'));
    const hasHan = (s) => /[\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s));
    /* a copy line must be Chinese in zh.json - unless it is pure punctuation,
     * like the dash that stands for "this entry is not a decision" */
    const hasLetter = (s) => /[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s));
    const paramsOf = (s) => (String(s).match(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g) || []).sort().join(',');
    AUDIT_NEW_KEYS.forEach((key) => {
      const [domain, ...rest] = key.split('.');
      const leaf = rest.join('.');
      const zhValue = zh[domain] && zh[domain][leaf];
      const enValue = en[domain] && en[domain][leaf];
      if (!zhValue || !enValue) throw new Error('missing copy for ' + key);
      if (hasLetter(zhValue) && !hasHan(zhValue)) throw new Error(key + ' has no Chinese in zh.json');
      if (hasHan(enValue)) throw new Error(key + ' carries CJK in en.json');
      if (paramsOf(zhValue) !== paramsOf(enValue)) throw new Error(key + ' parameter sets differ: ' + paramsOf(zhValue) + ' vs ' + paramsOf(enValue));
    });
    const path1 = off.ui.exportPathFor('pdf');
    if (path1 !== 'api/audit/export?format=pdf&lang=' + off.ui.lang()) throw new Error('the export path is ' + path1);
    if (path1.startsWith('/') || /:\/\//.test(path1)) throw new Error('the export path is not relative same-origin: ' + path1);
    return 'empty -> audit.empty / audit.empty_filtered, 500 -> audit.error.chain_broken (raw text hidden), '
      + 'offline -> 0 requests + offline copy, ' + AUDIT_NEW_KEYS.length + ' keys zh+en with matching parameters';
  });

  /* ---------------------------------------------------------------- *
   * 4.9 import (t22): paste / upload entry + durable records disclosure
   *
   * QA 5.1 measured two gaps: "paste JSON / upload trace" existed only as a
   * planned endpoint with no entry a person can press, and a restart threw the
   * records away. These checks pin the entry (phone sheet + desktop rail), the
   * single POST door, the limits the page checks, the dictionary copy of every
   * outcome, and that a failure is never painted as a success.
   * ---------------------------------------------------------------- */

  function importBoot(options, handlers, search) {
    const opts = options || {};
    const inst = ivBoot(search || '?probe=1');
    const log = [];
    const demoRows = (inst.ui.state.records || []).slice(0, 12);
    const live = {
      perms: opts.perms || ALL_PERMS,
      status: (opts.importStatus === undefined) ? 200 : opts.importStatus,
      code: opts.importCode || '',
      detail: opts.importDetail || null,
      durable: opts.durable !== false,
      importedCount: 0,
      imported: (opts.imported === undefined) ? 8 : opts.imported,
      created: (opts.created === undefined) ? 8 : opts.created,
      updated: (opts.updated === undefined) ? 0 : opts.updated,
      duplicates: (opts.duplicates === undefined) ? 0 : opts.duplicates,
      skipped: (opts.skipped === undefined) ? 0 : opts.skipped,
      format: opts.format || 'langfuse',
      sourceKind: opts.sourceKind || '',
      recordsTotal: (opts.recordsTotal === undefined) ? 20 : opts.recordsTotal
    };
    const importRows = (n) => Array.from({ length: n }, (_, i) => ({
      id: 'obs-lf-' + String(i + 1).padStart(4, '0'),
      schema_version: '1.1',
      action_type: 'tool_call',
      status: 'succeeded',
      intent: 'read the photo index',
      agent_id: 'langfuse-demo',
      actor_id: 'local',
      workspace_id: 'local',
      session_id: 'sess-import',
      timestamp: '2026-11-09T08:00:00+08:00',
      source: { kind: live.sourceKind || 'paste_json', uri: 'paste://import/probe', format: live.format },
      human_readable: { text: 'read the photo index (done)', level: 'l1', generated_by: 'trace_adapter' },
      demo_data: false,
      synthetic: true
    }));
    const map = Object.assign({
      health: () => ({
        status: 200, headers: { 'x-request-id': 'req-health-sc' },
        data: {
          name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1',
          endpoints: ['/health', '/api/records', '/api/import', '/api/audit'],
          auth: { required: false },
          import: {
            endpoint: '/api/import', method: 'POST', permission: 'data.write',
            formats: ['langfuse', 'otel_genai', 'mcp_events', 'native'],
            auto_detect: true, modes: ['create', 'update', 'upsert'], default_mode: 'upsert',
            max_bytes: 65536, max_records: 200,
            source_kinds: ['paste_json', 'upload'], imports: 0, imported_records: 0
          },
          records_store: { durable: live.durable, mode: live.durable ? 'jsonl' : 'memory', path: 'var/records.jsonl' }
        }
      }),
      records: () => ({
        status: 200, headers: {},
        data: {
          records: demoRows.concat(importRows(live.importedCount)),
          meta: {
            count: demoRows.length + live.importedCount,
            imported_count: live.importedCount,
            records_durable: live.durable,
            records_store: { durable: live.durable, mode: live.durable ? 'jsonl' : 'memory' }
          }
        }
      }),
      authStatus: () => ({ status: 200, data: { mode: 'local', required: false, auth_enabled: false, request_id: 'req-sc' }, headers: {} }),
      authMe: () => ({ status: 200, data: authMePayload('local', live.perms, 'local'), headers: {} }),
      authPermissions: () => ({ status: 200, data: { roles: ['admin', 'editor', 'viewer', 'local'], permissions: live.perms, matrix: {} }, headers: {} }),
      import: (endpoint, apiPath, body, method) => {
        log.push({ endpoint, apiPath, body, method });
        if (live.status !== 200) {
          return {
            status: live.status, headers: { 'x-request-id': 'req-import' },
            data: {
              ok: false, code: live.code,
              message: 'Raw server sentence: cannot parse JSON near line 1 (never shown verbatim)',
              detail: live.detail || { bytes: 70000, max: 65536, index: 3 },
              request_id: 'req-import'
            }
          };
        }
        const kind = (body && body.filename) ? 'upload' : 'paste_json';
        live.importedCount = live.imported;
        return {
          status: 200,
          headers: {
            'x-request-id': 'req-import',
            'x-tracelens-import-format': live.format,
            'x-tracelens-imported': String(live.imported),
            'x-tracelens-source-kind': kind,
            'x-tracelens-durable': live.durable ? 'true' : 'false'
          },
          data: {
            ok: true,
            imported: live.imported, created: live.created, updated: live.updated,
            duplicates: live.duplicates, skipped: live.skipped,
            ids: importRows(live.imported).map((r) => r.id),
            duplicate_detail: [], skip_detail: [],
            format: live.format, requested_format: (body && body.format) || null,
            mode: (body && body.mode) || 'upsert', source_kind: kind, strict: true,
            filename: (body && body.filename) || null,
            bytes: JSON.stringify(body || '').length,
            stats: { count: demoRows.length + live.imported, by_status: { succeeded: live.imported } },
            records_total: live.recordsTotal, durable: live.durable,
            stream: { head_seq: 9, frames: 3, subscribers: 0 },
            actor: 'local-user', request_id: 'req-import',
            note: '导入 trace：格式 langfuse，来源 ' + kind + '，事件 8 条，入库 8（正文不进审计，只记格式与统计）'
          }
        };
      }
    }, handlers || {});
    if (String(search || '').indexOf('api=off') < 0) {
      inst.ui.setApiTransport((endpoint, apiPath, body, method) => {
        const fn = map[endpoint];
        if (!fn) return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-sc' } };
        return fn(endpoint, apiPath, body, method);
      });
    }
    inst.importLog = log;
    inst.importCalls = () => log.filter((row) => row.endpoint === 'import');
    inst.live = live;
    return inst;
  }

  const importPanelNodes = (document, hook) => ivFind(document, '[data-hook="' + hook + '"]');
  /* both viewports carry the panel, so state assertions read one mount
   * (the phone sheet) instead of counting the desktop rail twice */
  const sheetFind = (document, selector) => {
    const host = ivFind(document, '[data-hook="sheet-import"]')[0];
    return host ? queryAll(host, selector) : [];
  };

  check('4.9 import entry: the phone sheet and the desktop rail both carry paste + file + format/mode, and the limits chip shows the server cap', () => {
    const inst = importBoot();
    const mounts = importPanelNodes(inst.document, 'import-text');
    if (mounts.length !== 2) throw new Error('the paste box is mounted ' + mounts.length + ' times, expected 2 (phone sheet + desktop rail)');
    const sheet = importPanelNodes(inst.document, 'sheet-import')[0];
    if (!sheet) throw new Error('the phone bottom sheet has no import block');
    if (!queryAll(sheet, '[data-hook="import-text"]').length) throw new Error('the sheet import block has no paste box');
    if (!queryAll(sheet, '[data-hook="import-file"]').length) throw new Error('the sheet import block has no file picker');
    const rail = importPanelNodes(inst.document, 'rail-hooks')[0];
    if (!rail || !queryAll(rail, '[data-hook="import"]').length) throw new Error('the desktop rail has no import hook');
    const file = importPanelNodes(inst.document, 'import-file')[0];
    if (String(file.getAttribute('type') || '') !== 'file') throw new Error('the file control is not a file input');
    if (!String(file.getAttribute('accept') || '').includes('.json')) throw new Error('the file picker accepts nothing trace-shaped');
    const formats = importPanelNodes(inst.document, 'import-format')[0];
    const formatOptions = queryAll(formats, 'option');
    if (formatOptions.length !== 5) throw new Error('format selector has ' + formatOptions.length + ' options, expected auto + 4 formats');
    const modes = queryAll(importPanelNodes(inst.document, 'import-mode')[0], 'option');
    if (modes.length !== 3) throw new Error('mode selector has ' + modes.length + ' options, expected create/update/upsert');
    formatOptions.concat(modes).forEach((option) => {
      if (!String(option.textContent || '')) throw new Error('a format/mode option renders without copy');
    });
    const limits = String(importPanelNodes(inst.document, 'import-limits')[0].textContent || '');
    if (limits.indexOf('65536') < 0 || limits.indexOf('200') < 0) throw new Error('the limits chip does not show the cap: ' + limits);
    const size = importPanelNodes(inst.document, 'import-size')[0];
    if (size.getAttribute('data-bytes') !== '0' || size.getAttribute('data-over') !== 'false') throw new Error('the byte counter starts wrong: ' + size.getAttribute('data-bytes'));
    const snap = inst.ui.importState();
    if (snap.maxBytes !== 65536 || snap.maxRecords !== 200) throw new Error('the page caps differ from the server: ' + snap.maxBytes + '/' + snap.maxRecords);
    if (snap.formats !== 5 || snap.modes !== 3) throw new Error('format/mode constant shape: ' + JSON.stringify(snap));
    const probeImport = inst.ui.probe().import;
    if (!probeImport || probeImport.maxBytes !== 65536 || probeImport.maxRecords !== 200) {
      throw new Error('probe().import does not describe the panel limits: ' + JSON.stringify(probeImport));
    }
    if (probeImport.formats !== 5 || probeImport.modes !== 3) {
      throw new Error('probe().import format/mode shape: ' + JSON.stringify(probeImport));
    }
    const durable = importPanelNodes(inst.document, 'import-durable')[0];
    if (durable.getAttribute('data-durable') !== 'false') throw new Error('a fresh page claims durability before the server answers');
    const appSourceText = read(path.join(HERE, 'app.js'));
    if (appSourceText.indexOf("import.error.no_file_reader") < 0) throw new Error('the no-FileReader copy is not wired: a picked file must degrade honestly');
    return '2 mounts (sheet + rail), 5 format options, 3 modes, cap 65536/200 shown, probe().import canWrite';
  });

  check('4.9 paste import: exactly one POST to api/import, the answer paints the counts, and the imported rows reach the list', async () => {
    const inst = importBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const before = (inst.ui.state.records || []).length;
    const text = JSON.stringify({ batch: [{ id: 'obs-lf-0001', name: 'read the photo index' }] });
    inst.ui.importSet({ text, mode: 'create', format: '' });
    const out = await inst.ui.importRun();
    if (!out || out.ok !== true) throw new Error('the import did not succeed: ' + JSON.stringify(out));
    const calls = inst.importCalls();
    if (calls.length !== 1) throw new Error('the import sent ' + calls.length + ' requests, expected exactly 1');
    if (calls[0].method !== 'POST') throw new Error('the import verb is ' + calls[0].method);
    if (calls[0].apiPath !== 'api/import') throw new Error('the import path is ' + calls[0].apiPath);
    if (calls[0].body.payload !== text) throw new Error('the pasted text is not the payload that was sent');
    if (calls[0].body.mode !== 'create') throw new Error('the mode did not reach the request: ' + calls[0].body.mode);
    if (calls[0].body.filename) throw new Error('a paste sent a filename: ' + calls[0].body.filename);
    const snap = inst.ui.importState();
    if (snap.status !== 'done' || snap.requests !== 1 || snap.imports !== 1) throw new Error('import snapshot: ' + JSON.stringify(snap));
    if (snap.canWrite !== true || snap.online !== true || snap.enabled !== true) throw new Error('a usable panel is not described as usable: ' + JSON.stringify(snap));
    if (snap.denials !== 0 || snap.clientBlocked !== 0) throw new Error('a clean import counted a block: ' + JSON.stringify(snap));
    const facts = sheetFind(inst.document, '[data-hook="import-fact"]');
    if (facts.length !== 6) throw new Error('the summary shows ' + facts.length + ' facts, expected 6');
    const factOf = (kind) => facts.filter((n) => n.getAttribute('data-kind') === kind)[0];
    if (factOf('imported').getAttribute('data-count') !== '8') throw new Error('imported count is not the server answer');
    if (!String(factOf('imported').textContent || '').includes('8')) throw new Error('the imported fact renders no number');
    const durableLine = String(importPanelNodes(inst.document, 'import-result-durable')[0].textContent || '');
    if (!durableLine) throw new Error('the durable line renders empty');
    const feedback = importPanelNodes(inst.document, 'import-feedback')[0];
    if (feedback.getAttribute('data-i18n') !== 'import.feedback.done') throw new Error('the result line is not the dictionary summary: ' + feedback.getAttribute('data-i18n'));
    if (feedback.getAttribute('data-status') !== 'done') throw new Error('the panel is not in the done state');
    const after = (inst.ui.state.records || []).length;
    if (after <= before) throw new Error('the imported rows never reached the list: ' + before + ' -> ' + after);
    const importedRow = (inst.ui.state.records || []).find((r) => String(r.id) === 'obs-lf-0001');
    if (!importedRow) throw new Error('the imported record is missing from the zones');
    if (inst.ui.recordIsDemo(importedRow) === true) throw new Error('an imported record is painted as demo data');
    const chip = importPanelNodes(inst.document, 'import-durable')[0];
    if (chip.getAttribute('data-durable') !== 'true') throw new Error('the durable chip did not follow the server meta');
    const run = importPanelNodes(inst.document, 'import-run')[0];
    if (run.getAttribute('data-enabled') !== 'true') throw new Error('a usable import button is marked disabled');
    inst.ui.importClear();
    if (inst.ui.importState().bytes !== 0) throw new Error('clear left text behind');
    if (importPanelNodes(inst.document, 'import-summary')[0].getAttribute('data-visible') !== 'false') throw new Error('the summary survived the clear');
    return '1 POST api/import (payload verbatim), 6 facts (8 imported), list ' + before + '->' + after + ', not demo, durable chip true, clear resets';
  });

  check('4.9 upload: the file lands as the same payload with its name, a too-large file never leaves the page, and the mode note follows the selector', async () => {
    const inst = importBoot({ format: 'otel_genai' });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const otel = JSON.stringify({ resourceSpans: [{ scopeSpans: [] }] });
    inst.ui.importSet({ text: otel, filename: 'otel-export.json', mode: 'upsert' });
    const nameNode = importPanelNodes(inst.document, 'import-filename')[0];
    if (nameNode.getAttribute('data-visible') !== 'true' || nameNode.getAttribute('data-filename') !== 'otel-export.json') {
      throw new Error('the picked file name is not shown: ' + nameNode.getAttribute('data-filename'));
    }
    const out = await inst.ui.importRun();
    if (out.ok !== true) throw new Error('the upload import failed: ' + JSON.stringify(out));
    const call = inst.importCalls().slice(-1)[0];
    if (call.body.filename !== 'otel-export.json') throw new Error('the filename never reached the server: ' + JSON.stringify(call.body));
    if (call.body.payload !== otel) throw new Error('the file content is not the payload');
    if (inst.ui.importState().result.format !== 'otel_genai') throw new Error('the format shown is not the server detection');
    if (String(inst.live.sourceKind) === 'demo_data') throw new Error('an upload is labelled demo data');
    const big = 'x'.repeat(inst.ui.IMPORT_MAX_BYTES + 12);
    inst.ui.importSet({ text: big, filename: 'huge-trace.json' });
    if (inst.ui.importCheck().key !== 'import.validation.too_large') throw new Error('the size check did not fire: ' + JSON.stringify(inst.ui.importCheck()));
    const sizeNode = importPanelNodes(inst.document, 'import-size')[0];
    if (sizeNode.getAttribute('data-over') !== 'true') throw new Error('the byte counter does not flag the overrun');
    const blocked = await inst.ui.importRun();
    if (blocked.ok !== false || blocked.blocked !== true) throw new Error('an over-cap import was not blocked in the page: ' + JSON.stringify(blocked));
    if (inst.importCalls().length !== 1) throw new Error('an over-cap import reached the network: ' + inst.importCalls().length + ' calls');
    const feedback = importPanelNodes(inst.document, 'import-feedback')[0];
    if (feedback.getAttribute('data-i18n') !== 'import.validation.too_large') throw new Error('the size message is not dictionary copy: ' + feedback.getAttribute('data-i18n'));
    const params = JSON.parse(feedback.getAttribute('data-i18n-params') || '{}');
    if (!params.bytes || !params.limit) throw new Error('the size message has no numbers: ' + feedback.getAttribute('data-i18n-params'));
    if (inst.ui.importState().clientBlocked !== 1) throw new Error('the client block is not counted: ' + JSON.stringify(inst.ui.importState()));
    inst.ui.importSet({ text: JSON.stringify({ batch: [] }), filename: '', mode: 'update' });
    const note = importPanelNodes(inst.document, 'import-mode-note')[0];
    if (note.getAttribute('data-i18n') !== 'import.mode.note.update') throw new Error('the mode note does not follow the selector: ' + note.getAttribute('data-i18n'));
    return 'upload keeps filename + payload verbatim, 70 KB blocked with 0 requests, size params shown, mode note follows the selector';
  });

  check('4.9 import copy: every import key is in zh and en, en is CJK-free, parameters match, and no error code is invented', () => {
    const zh = readJson(path.join(HERE, 'i18n', 'zh.json'));
    const en = readJson(path.join(HERE, 'i18n', 'en.json'));
    const hasHan = (s) => /[\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s));
    const hasLetter = (s) => /[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s));
    const paramsOf = (s) => (String(s).match(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g) || []).sort().join(',');
    const zhKeys = Object.keys(zh.import || {}).map((k) => 'import.' + k);
    const enKeys = Object.keys(en.import || {}).map((k) => 'import.' + k);
    if (zhKeys.length < 60) throw new Error('the import domain has only ' + zhKeys.length + ' keys');
    if (zhKeys.sort().join(',') !== enKeys.sort().join(',')) throw new Error('zh and en import keys differ');
    zhKeys.forEach((key) => {
      const leaf = key.slice('import.'.length);
      const zhValue = zh.import[leaf];
      const enValue = en.import[leaf];
      if (!zhValue || !enValue) throw new Error('missing copy for ' + key);
      if (hasLetter(zhValue) && !hasHan(zhValue)) throw new Error(key + ' has no Chinese in zh.json');
      if (hasHan(enValue)) throw new Error(key + ' carries CJK in en.json');
      if (paramsOf(zhValue) !== paramsOf(enValue)) throw new Error(key + ' parameter sets differ: ' + paramsOf(zhValue) + ' vs ' + paramsOf(enValue));
    });
    const appSourceText = read(path.join(HERE, 'app.js'));
    const mapBlock = /var IMPORT_ERROR_KEYS = \{([\s\S]*?)\};/.exec(appSourceText);
    if (!mapBlock) throw new Error('the import error map is missing from app.js');
    const pairs = [...mapBlock[1].matchAll(/([A-Z][A-Z0-9_]+)\s*:\s*'([a-z_][a-z0-9_.]*)'/g)].map((m) => [m[1], m[2]]);
    if (pairs.length < 20) throw new Error('the import error map covers only ' + pairs.length + ' codes');
    const server = read(path.join(PKG, 'agent_explainer', 'server.py'));
    const storeSource = read(path.join(PKG, 'agent_explainer', 'store.py'));
    const invented = pairs.filter(([, key]) => !zh.import[key.slice('import.'.length)] || !en.import[key.slice('import.'.length)]);
    if (invented.length) throw new Error('the map points at copy that does not exist: ' + invented.map((p) => p[0]).join(','));
    /* NETWORK_ERROR / TIMEOUT / HTTP_STATUS are the page's own transport
     * outcomes (the request never reached the backend), so they are pinned
     * against the intervention map, not against server.py. */
    const CLIENT_CODES = ['NETWORK_ERROR', 'TIMEOUT', 'HTTP_STATUS'];
    const interveneMap = /var INTERVENE_ERROR_KEYS = \{([\s\S]*?)\};/.exec(appSourceText);
    if (!interveneMap) throw new Error('the intervention error map is missing, so transport codes cannot be pinned');
    const unknown = pairs.filter(([code]) => {
      if (CLIENT_CODES.indexOf(code) >= 0) return !new RegExp('\\b' + code + '\\s*:').test(interveneMap[1]);
      const inServer = server.indexOf('"' + code + '"') >= 0 || server.indexOf("'" + code + "'") >= 0;
      const inStore = storeSource.indexOf('"' + code + '"') >= 0 || storeSource.indexOf("'" + code + "'") >= 0;
      return !inServer && !inStore;
    });
    if (unknown.length) throw new Error('the page maps codes the backend never emits: ' + unknown.map((p) => p[0]).join(','));
    const deadCodes = pairs.filter(([, key]) => key.indexOf('import.error.') === 0
      && zhKeys.indexOf(key) < 0);
    if (deadCodes.length) throw new Error('the import map has codes without dictionary copy: ' + deadCodes.map((p) => p[1]).join(','));
    /* the constants the page checks must be the server's own numbers */
    const pyBytesMatch = /IMPORT_MAX_BYTES\s*=\s*(\d+)(?:\s*\*\s*(\d+))?/.exec(server);
    const pyBytes = String(Number(pyBytesMatch[1]) * (pyBytesMatch[2] ? Number(pyBytesMatch[2]) : 1));
    const pyRecords = /IMPORT_MAX_RECORDS\s*=\s*(\d+)/.exec(server);
    const pyPath = /API_PATH_IMPORT\s*=\s*"([^"]+)"/.exec(server);
    const jsBytes = /var IMPORT_MAX_BYTES = (\d+)/.exec(appSourceText);
    const jsRecords = /var IMPORT_MAX_RECORDS = (\d+)/.exec(appSourceText);
    if (!pyRecords || !pyPath) throw new Error('the import constants are not in server.py');
    if (jsBytes[1] !== pyBytes || jsRecords[1] !== pyRecords[1]) {
      throw new Error('the page caps ' + jsBytes[1] + '/' + jsRecords[1] + ' differ from server.py ' + pyBytes + '/' + pyRecords[1]);
    }
    const endpoint = /import:\s*'api\/import'/.test(appSourceText);
    if (!endpoint || pyPath[1] !== '/api/import') throw new Error('the import endpoint pair is not pinned: ' + pyPath[1]);
    const modes = /IMPORT_MODES[^=\n]*=\s*\(([^)]*)\)/.exec(server);
    const jsModes = /var IMPORT_MODES = \[([^\]]*)\]/.exec(appSourceText);
    if (!modes || !jsModes) throw new Error('the import mode lists are not pinned in server.py/app.js');
    const pyModeList = (modes[1].match(/"([a-z]+)"/g) || []).map((s) => s.replace(/"/g, '')).sort().join(',');
    const jsModeList = (jsModes[1].match(/'([a-z]+)'/g) || []).map((s) => s.replace(/'/g, '')).sort().join(',');
    if (pyModeList !== jsModeList) throw new Error('mode lists differ: py=' + pyModeList + ' js=' + jsModeList);
    const inst = importBoot();
    inst.ui.setLang('en');
    const titleEn = String(importPanelNodes(inst.document, 'import-title')[0].textContent || '');
    if (!titleEn || hasHan(titleEn)) throw new Error('the English panel title carries CJK: ' + titleEn);
    inst.ui.setLang('zh');
    const zhTitle = String(importPanelNodes(inst.document, 'import-title')[0].textContent || '');
    if (!hasHan(zhTitle)) throw new Error('the Chinese panel title is not Chinese: ' + zhTitle);
    return zhKeys.length + ' import keys in zh+en with matching parameters, ' + pairs.length
      + ' codes all backed by server.py/store.py, caps ' + pyBytes + '/' + pyRecords[1] + ' + modes match, panel repaints zh/en';
  });

  check('4.9 import states: no permission, offline, empty, 400, 413 and a store failure all read from the dictionary and send nothing when they must not', async () => {
    const viewer = importBoot({ perms: VIEWER_PERMS });
    await viewer.ui.probeApi();
    await viewer.ui.authProbe();
    viewer.ui.importSet({ text: JSON.stringify({ batch: [] }) });
    const run = importPanelNodes(viewer.document, 'import-run')[0];
    if (run.getAttribute('data-enabled') !== 'false') throw new Error('a role without data.write can press Import');
    const need = importPanelNodes(viewer.document, 'import-need')[0];
    if (!need || need.getAttribute('data-i18n') !== 'import.note.need_permission') throw new Error('the disabled Import carries no reason');
    const denied = await viewer.ui.importRun();
    if (denied.ok !== false) throw new Error('a forbidden import reported success');
    if (viewer.importCalls().length !== 0) throw new Error('a role without data.write reached the network');
    if (viewer.ui.importState().denials !== 1) throw new Error('the denial is not counted for the rig');

    const off = importBoot({}, null, '?probe=1&api=off');
    await off.ui.probeApi();
    await off.ui.authProbe();
    off.ui.importSet({ text: JSON.stringify({ batch: [] }) });
    const offOut = await off.ui.importRun();
    if (offOut.ok !== false) throw new Error('offline import reported success');
    if (off.ui.importState().requests !== 0) throw new Error('offline mode made ' + off.ui.importState().requests + ' requests');
    const offFeedback = importPanelNodes(off.document, 'import-feedback')[0];
    if (offFeedback.getAttribute('data-i18n') !== 'import.note.offline') throw new Error('offline copy is not the dictionary line: ' + offFeedback.getAttribute('data-i18n'));

    const empty = importBoot();
    await empty.ui.probeApi();
    await empty.ui.authProbe();
    if ((empty.ui.importCheck() || {}).key !== 'import.validation.empty') throw new Error('an empty paste is not caught in the page');
    const emptyOut = await empty.ui.importRun();
    if (emptyOut.ok !== false || empty.importCalls().length !== 0) throw new Error('an empty import reached the network');
    if (empty.ui.importState().clientBlocked !== 1) throw new Error('the empty block is not counted');

    const bad = importBoot({ importStatus: 400, importCode: 'IMPORT_INPUT_NOT_JSON' });
    await bad.ui.probeApi();
    await bad.ui.authProbe();
    /* valid JSON the page accepts, so the request really goes out and the
     * server is the one that says it cannot parse the document */
    bad.ui.importSet({ text: JSON.stringify({ batch: [{ id: 'obs-lf-0001' }] }) });
    const badOut = await bad.ui.importRun();
    if (badOut.ok !== false || badOut.code !== 'IMPORT_INPUT_NOT_JSON') throw new Error('the 400 was not surfaced as an answered request: ' + JSON.stringify(badOut));
    if (bad.ui.importState().status !== 'error') throw new Error('a 400 leaves the panel out of the error state');
    const badFeedback = sheetFind(bad.document, '[data-hook="import-feedback"]')[0];
    if (badFeedback.getAttribute('data-i18n') !== 'import.error.input_not_json') throw new Error('the 400 code is not mapped to copy: ' + badFeedback.getAttribute('data-i18n'));
    const badBody = String(bad.document.body.textContent || '');
    if (badBody.includes('Raw server sentence')) throw new Error('the raw server message is pasted into the page');
    if (sheetFind(bad.document, '[data-hook="import-fact"]').length) throw new Error('a failed import paints a success summary');

    const large = importBoot({ importStatus: 413, importCode: 'IMPORT_TOO_LARGE', importDetail: { bytes: 90000, max: 65536 } });
    await large.ui.probeApi();
    await large.ui.authProbe();
    large.ui.importSet({ text: JSON.stringify({ batch: [{ id: 'a' }] }) });
    await large.ui.importRun();
    const largeFeedback = importPanelNodes(large.document, 'import-feedback')[0];
    if (largeFeedback.getAttribute('data-i18n') !== 'import.error.too_large') throw new Error('413 is not mapped: ' + largeFeedback.getAttribute('data-i18n'));
    const largeParams = JSON.parse(largeFeedback.getAttribute('data-i18n-params') || '{}');
    if (String(largeParams.bytes) !== '90000' || String(largeParams.limit) !== '65536') throw new Error('the 413 numbers are not shown: ' + largeFeedback.getAttribute('data-i18n-params'));

    const storeFail = importBoot({ importStatus: 500, importCode: 'RECORD_STORE_WRITE_FAILED', importDetail: { hint: 'read-only volume' } });
    await storeFail.ui.probeApi();
    await storeFail.ui.authProbe();
    storeFail.ui.importSet({ text: JSON.stringify({ batch: [{ id: 'a' }] }) });
    await storeFail.ui.importRun();
    const storeFeedback = importPanelNodes(storeFail.document, 'import-feedback')[0];
    if (storeFeedback.getAttribute('data-i18n') !== 'import.error.store_write_failed') throw new Error('a store write failure is not mapped: ' + storeFeedback.getAttribute('data-i18n'));
    if (storeFail.ui.importState().imports !== 0) throw new Error('a write failure is counted as a successful import');
    if (storeFail.ui.importState().status !== 'error') throw new Error('a write failure is not an error state');

    const dup = importBoot({ imported: 0, created: 0, updated: 0, duplicates: 8, skipped: 0 });
    await dup.ui.probeApi();
    await dup.ui.authProbe();
    dup.ui.importSet({ text: JSON.stringify({ batch: [{ id: 'a' }] }), mode: 'create' });
    await dup.ui.importRun();
    const dupFacts = importPanelNodes(dup.document, 'import-fact');
    const dupImported = dupFacts.filter((n) => n.getAttribute('data-kind') === 'imported')[0];
    if (dupImported.getAttribute('data-count') !== '0') throw new Error('duplicates are counted as imports');
    if (dupFacts.filter((n) => n.getAttribute('data-kind') === 'duplicates')[0].getAttribute('data-count') !== '8') throw new Error('the duplicate count is not the server answer');
    if (dup.ui.importState().status !== 'done') throw new Error('a duplicate-only import is not a finished import');
    return 'viewer + offline + empty: 0 requests with dictionary reasons; 400/413/500 mapped, raw text hidden; duplicates shown as 0 imported / 8 duplicates';
  });

  /* ---------------------------------------------------------------- *
   * 4.10 mobile reachability: entry list, gating, panels, keyboard (t23)
   *
   * QA 5.1 D-4 said the phone layout had no way to settings, audit, users,
   * import or export. These checks are the counter-evidence: the entries
   * exist on a phone, they say why they are not available when the role
   * may not use them, every one opens the panel that already exists, the
   * keyboard can follow, the language switch repaints the list in place,
   * and the desktop sidebar is untouched.
   * ---------------------------------------------------------------- */

  const MORE_PANELS = ['settings', 'audit', 'users', 'import', 'export', 'language'];
  const moreNodes = (document, hook) => ivFind(document, '[data-hook="' + hook + '"]');
  const moreEntry = (document, panel) =>
    ivFind(document, '[data-hook="more-entry"][data-panel="' + panel + '"]')[0] || null;
  const moreEntryTitle = (node) => {
    const title = node ? node.querySelector('.more__entry-title') : null;
    return title ? String(title.textContent || '') : '';
  };
  const moreReasonNode = (document, panel) =>
    ivFind(document, '[data-hook="more-reason"][data-panel="' + panel + '"]')[0] || null;
  const duplicateIds = (document) => {
    const seen = {};
    const dupes = [];
    const walk = (node) => {
      if (!node) return;
      if (node.id) {
        if (seen[node.id]) dupes.push(node.id);
        seen[node.id] = 1;
      }
      (node.children || []).forEach(walk);
    };
    walk(document.body);
    return dupes;
  };
  const entryStateOf = (snapshot, panel) => {
    const found = (snapshot.entries || []).filter((e) => e.id === panel)[0];
    if (!found) throw new Error('probe.more has no entry for ' + panel);
    return found;
  };

  check('4.10 phone entry list: one entry button in the top bar lists settings, audit, users, import, export and language; the desktop layout refuses it', () => {
    const inst = ivBoot('?probe=1');
    const app = inst.document.getElementById('app');
    if (app.getAttribute('data-view') !== 'mobile') throw new Error('a plain window is not the phone layout: ' + app.getAttribute('data-view'));
    const buttons = moreNodes(inst.document, 'more-menu');
    if (buttons.length !== 1) throw new Error('expected exactly one phone entry button, found ' + buttons.length);
    const button = buttons[0];
    if (button.getAttribute('data-visible') !== 'true') throw new Error('the entry button hides itself on a phone');
    if (button.getAttribute('aria-controls') !== 'more-sheet') throw new Error('the entry button does not point at the list: ' + button.getAttribute('aria-controls'));
    if (!button.getAttribute('aria-label')) throw new Error('the entry button has no aria-label');
    if (String(button.textContent) !== inst.ui.t('more.title')) throw new Error('the entry button text is not dictionary copy: ' + button.textContent);
    const sheet = inst.document.getElementById('more-sheet');
    if (sheet.hidden !== true) throw new Error('the entry list is open before anyone asked for it');
    if (inst.ui.moreMenu(true) !== true) throw new Error('the entry list did not open');
    if (sheet.hidden !== false) throw new Error('the entry list is still hidden after opening');
    if (app.getAttribute('data-more-menu') !== 'on') throw new Error('data-more-menu=' + app.getAttribute('data-more-menu'));
    /* the top bar repaints, so the state must be read on the fresh node */
    const openButton = moreNodes(inst.document, 'more-menu')[0];
    if (openButton.getAttribute('aria-expanded') !== 'true') throw new Error('aria-expanded did not follow the list: ' + openButton.getAttribute('aria-expanded'));
    if (openButton.getAttribute('data-visible') !== 'true') throw new Error('the entry button hid itself once the list opened');
    if (String(openButton.textContent) !== inst.ui.t('more.title')) throw new Error('the entry button lost its copy after opening');
    const entries = moreNodes(inst.document, 'more-entry');
    const got = entries.map((n) => n.getAttribute('data-panel')).join(',');
    if (got !== MORE_PANELS.join(',')) throw new Error('the phone list is not the contract list: ' + got);
    entries.forEach((node) => {
      const panel = node.getAttribute('data-panel');
      if (node.getAttribute('type') !== 'button') throw new Error('entry ' + panel + ' is not a real button');
      if (node.getAttribute('tabindex') !== '0') throw new Error('entry ' + panel + ' is not keyboard reachable');
      if (!node.getAttribute('aria-label')) throw new Error('entry ' + panel + ' has no aria-label');
      if (node.getAttribute('aria-disabled') !== 'true' && node.getAttribute('aria-disabled') !== 'false') {
        throw new Error('entry ' + panel + ' does not publish its availability');
      }
      if (!moreEntryTitle(node)) throw new Error('entry ' + panel + ' has no title text');
      const hint = node.querySelector('.more__entry-hint, .more__entry-value, .more__entry-action');
      if (!hint || !String(hint.textContent)) throw new Error('entry ' + panel + ' has no hint line');
    });
    const probe = inst.ui.probe();
    if (probe.more.total !== 6) throw new Error('probe.more.total=' + probe.more.total);
    if (probe.rendered.moreEntries !== 6) throw new Error('probe.rendered.moreEntries=' + probe.rendered.moreEntries);
    if (probe.rendered.moreSheet !== 1) throw new Error('probe.rendered.moreSheet=' + probe.rendered.moreSheet);
    const desk = ivBoot('?probe=1&frame=1440x900');
    if (desk.document.getElementById('app').getAttribute('data-view') !== 'desktop') throw new Error('1440px is not the desktop layout');
    if (desk.ui.moreMenu(true) !== false) throw new Error('the phone list opens on the desktop layout');
    if (desk.document.getElementById('more-sheet').hidden !== true) throw new Error('the phone list is visible on the desktop layout');
    if (desk.ui.moreOpen('settings') !== false) throw new Error('a phone entry opens on the desktop layout');
    if (desk.document.getElementById('app').getAttribute('data-more-menu') !== 'off') throw new Error('data-more-menu is not off on desktop');
    if (moreNodes(desk.document, 'more-menu')[0].getAttribute('data-visible') !== 'false') throw new Error('the phone entry button shows itself on desktop');
    return '1 entry button (aria-controls/expanded, dictionary copy), 6 entries in contract order with title + hint + aria-label, probe.more 6, desktop refuses the list and hides the button';
  });

  check('4.10 entry gating: an entry the role may not use stays in the list, reads as not available, and says why in the user language', async () => {
    const offline = ivBoot('?probe=1&api=off');
    offline.ui.moreMenu(true);
    const off = offline.ui.moreState();
    const wantOffline = {
      /* file:// mirrors the server local profile: everything a local user may
       * do is open, what needs the server says so, users need an admin sign-in */
      settings: [true, ''], audit: [false, 'audit.error.offline'],
      users: [false, 'more.reason.users'], import: [false, 'import.note.offline'],
      export: [false, 'export.note.offline'], language: [true, '']
    };
    MORE_PANELS.forEach((panel) => {
      const st = entryStateOf(off, panel);
      const want = wantOffline[panel];
      if (st.enabled !== want[0]) throw new Error(panel + ' enabled=' + st.enabled + ' in offline mode, expected ' + want[0]);
      if (st.reason !== want[1]) throw new Error(panel + ' reason=' + st.reason + ', expected ' + want[1]);
      const node = moreEntry(offline.document, panel);
      if (!node) throw new Error('entry ' + panel + ' is missing in offline mode');
      if (node.getAttribute('data-enabled') !== String(want[0])) throw new Error(panel + ' data-enabled=' + node.getAttribute('data-enabled'));
      if (node.getAttribute('aria-disabled') !== String(!want[0])) throw new Error(panel + ' aria-disabled=' + node.getAttribute('aria-disabled'));
      const reason = moreReasonNode(offline.document, panel);
      if (want[1]) {
        if (!reason) throw new Error('the disabled entry ' + panel + ' has no visible reason');
        if (reason.getAttribute('data-i18n') !== want[1]) throw new Error(panel + ' reason copy=' + reason.getAttribute('data-i18n') + ', expected ' + want[1]);
        if (!String(reason.textContent)) throw new Error('the reason for ' + panel + ' is empty text');
        if (String(reason.textContent) !== offline.ui.t(want[1])) throw new Error('the reason for ' + panel + ' is not the translated line');
        if (node.getAttribute('aria-label').indexOf(offline.ui.t(want[1])) < 0) throw new Error('the aria-label of ' + panel + ' does not carry the reason');
        if (node.getAttribute('tabindex') !== '0') throw new Error('the disabled entry ' + panel + ' cannot be focused, so the reason is unreachable');
        if (node.getAttribute('disabled')) throw new Error('the disabled entry ' + panel + ' uses the disabled attribute and drops out of the keyboard');
      } else if (reason) throw new Error('an available entry ' + panel + ' carries a reason line');
    });
    if (off.available !== 2 || off.disabled !== 4) throw new Error('offline gating: available=' + off.available + ' disabled=' + off.disabled);

    const blocked = moreEntry(offline.document, 'audit');
    const before = offline.ui.probe().stats.renders;
    blocked.click();
    const afterClick = offline.ui.moreState();
    if (afterClick.blocked !== 1) throw new Error('a blocked entry is not counted: ' + afterClick.blocked);
    if (afterClick.open !== false || afterClick.target !== 'off') throw new Error('a blocked entry opened something');
    if (afterClick.menu !== true) throw new Error('the list closed when a blocked entry was tapped');
    const feedback = ivFind(offline.document, '[data-hook="more-feedback"]')[0];
    if (!feedback || feedback.getAttribute('data-visible') !== 'true') throw new Error('the blocked tap has no visible feedback line');
    if (feedback.getAttribute('data-i18n') !== 'more.feedback.blocked') throw new Error('the feedback line is not dictionary copy: ' + feedback.getAttribute('data-i18n'));
    if (String(feedback.textContent).indexOf(offline.ui.t('audit.error.offline')) < 0) throw new Error('the feedback line does not repeat the reason');
    if (offline.ui.probe().stats.renders - before > 1) throw new Error('a blocked tap repaints ' + (offline.ui.probe().stats.renders - before) + ' times');

    const viewer = authBoot({
      audit: () => ({ status: 200, data: { entries: [], meta: { count: 0 } }, headers: {} }),
      users: () => ({ status: 403, data: { code: 'ROLE_FORBIDDEN', message: 'Role may not do this', detail: { permission: 'users.manage' }, request_id: 'req-sc' }, headers: {} })
    }, '?probe=1');
    await signedInAs(viewer, 'viewer', VIEWER_PERMS, 'watcher', 'tok-sc-more', 'csrf-sc-more');
    viewer.ui.moreMenu(true);
    const view = viewer.ui.moreState();
    if (entryStateOf(view, 'audit').enabled !== true) throw new Error('a viewer may read the audit trail: ' + JSON.stringify(entryStateOf(view, 'audit')));
    if (entryStateOf(view, 'export').enabled !== true) throw new Error('a viewer holds audit.export: ' + JSON.stringify(entryStateOf(view, 'export')));
    if (entryStateOf(view, 'import').reason !== 'import.note.need_permission') throw new Error('the import entry is not gated on data.write: ' + entryStateOf(view, 'import').reason);
    if (entryStateOf(view, 'users').reason !== 'more.reason.users') throw new Error('the users entry is not gated on users.manage: ' + entryStateOf(view, 'users').reason);
    if (String(moreReasonNode(viewer.document, 'users').textContent) !== viewer.ui.t('more.reason.users')) throw new Error('the users reason is not translated copy');

    const local = auditBoot({}, '?probe=1');
    await local.ui.probeApi();
    await local.ui.authProbe();
    local.ui.moreMenu(true);
    const loc = local.ui.moreState();
    ['audit', 'export', 'import'].forEach((panel) => {
      if (entryStateOf(loc, panel).enabled !== true) throw new Error('the local admin cannot reach ' + panel + ': ' + JSON.stringify(entryStateOf(loc, panel)));
    });
    if (entryStateOf(loc, 'users').enabled !== true) throw new Error('the local profile cannot reach the user page: ' + JSON.stringify(entryStateOf(loc, 'users')));
    if (entryStateOf(loc, 'settings').enabled !== true) throw new Error('the settings entry is not available in local mode');

    /* the sign-in reason needs a role that holds users.manage with no session
     * behind it: the rig surface writes exactly that combination, and the
     * entry must ask for a sign-in instead of opening an empty admin page */
    local.ui.state.auth.session = null;
    local.ui.state.auth.required = true;
    local.ui.renderAll();
    const signedOut = local.ui.moreState();
    if (entryStateOf(signedOut, 'users').enabled !== false) throw new Error('the users entry is open without a session');
    if (entryStateOf(signedOut, 'users').reason !== 'more.reason.signin') throw new Error('users without a session must ask for a sign-in, got: ' + entryStateOf(signedOut, 'users').reason);
    const signinReason = moreReasonNode(local.document, 'users');
    if (!signinReason || String(signinReason.textContent) !== local.ui.t('more.reason.signin')) throw new Error('the sign-in reason is not visible copy: ' + (signinReason && signinReason.textContent));
    return 'offline: 4 disabled with the right reason copy (and 2 available), blocked tap says why in one render; viewer: import + users gated, audit + export open; local profile: all 6 open, and users without a session asks for a sign-in';
  });

  check('4.10 every entry opens the panel that already exists: audit + export as a drawer, import in the sheet, settings + users as screens, and no panel is rendered twice', async () => {
    const inst = auditBoot({}, '?probe=1&frame=375x812');
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const app = inst.document.getElementById('app');
    const rail = inst.document.getElementById('timeline');
    inst.ui.moreMenu(true);
    const rendersBefore = inst.ui.probe().stats.renders;
    if (inst.ui.moreOpen('export') !== true) throw new Error('the export entry did not open');
    if (inst.ui.probe().stats.renders - rendersBefore !== 1) throw new Error('opening export repaints ' + (inst.ui.probe().stats.renders - rendersBefore) + ' times');
    let s = inst.ui.moreState();
    if (s.open !== true || s.target !== 'export') throw new Error('moreState after export: ' + JSON.stringify({ open: s.open, target: s.target }));
    if (app.getAttribute('data-more-open') !== 'true') throw new Error('data-more-open=' + app.getAttribute('data-more-open'));
    if (app.getAttribute('data-more-target') !== 'export') throw new Error('data-more-target=' + app.getAttribute('data-more-target'));
    if (app.getAttribute('data-more-menu') !== 'off') throw new Error('the list stayed open on top of the drawer');
    if (rail.getAttribute('aria-hidden') !== 'false') throw new Error('the drawer is hidden from assistive tech: aria-hidden=' + rail.getAttribute('aria-hidden'));
    if (rail.getAttribute('role') !== 'dialog' || rail.getAttribute('aria-modal') !== 'true') throw new Error('the drawer is not announced as a dialog');
    const drawer = moreNodes(inst.document, 'more-drawer')[0];
    if (!drawer) throw new Error('the drawer has no head');
    if (moreNodes(inst.document, 'more-drawer').length !== 1) throw new Error('the drawer head exists more than once');
    if (drawer.getAttribute('data-target') !== 'export') throw new Error('the drawer head does not say what it shows');
    const drawerTitle = drawer.querySelector('.rail__drawer-title');
    if (!drawerTitle || !String(drawerTitle.textContent)) throw new Error('the drawer has no title');
    if (drawerTitle.getAttribute('id') !== 'more-drawer-title') throw new Error('the drawer title is not the label of the dialog');
    if (rail.getAttribute('aria-labelledby') !== 'more-drawer-title') throw new Error('the dialog is not labelled by the drawer title');
    if (moreNodes(inst.document, 'export').length !== 1) throw new Error('the export panel exists ' + moreNodes(inst.document, 'export').length + ' times');
    if (moreNodes(inst.document, 'export-buttons').length !== 1) throw new Error('the export buttons exist ' + moreNodes(inst.document, 'export-buttons').length + ' times');
    if (moreNodes(inst.document, 'more-drawer-back').length !== 1 || moreNodes(inst.document, 'more-drawer-close').length !== 1) throw new Error('the drawer has no single back/close control');
    moreNodes(inst.document, 'more-drawer-back')[0].click();
    s = inst.ui.moreState();
    if (s.open !== false || s.menu !== true) throw new Error('back from the drawer did not return to the list: ' + JSON.stringify({ open: s.open, menu: s.menu }));
    if (app.getAttribute('data-more-open') !== 'false') throw new Error('data-more-open stayed true');
    if (rail.getAttribute('aria-hidden') !== 'true') throw new Error('the closed drawer is still visible to assistive tech');
    if (rail.getAttribute('role')) throw new Error('the closed rail still claims to be a dialog');

    inst.ui.moreMenu(false);
    if (inst.ui.moreOpen('audit') !== true) throw new Error('the audit entry did not open');
    await settle();
    await settle();
    s = inst.ui.moreState();
    if (s.target !== 'audit' || s.open !== true) throw new Error('moreState after audit: ' + JSON.stringify({ open: s.open, target: s.target }));
    if (app.getAttribute('data-more-target') !== 'audit') throw new Error('data-more-target=' + app.getAttribute('data-more-target'));
    if (moreNodes(inst.document, 'audit').length !== 1) throw new Error('the audit panel exists ' + moreNodes(inst.document, 'audit').length + ' times');
    const auditList = moreNodes(inst.document, 'audit-list')[0];
    if (!auditList) throw new Error('the audit list is missing inside the drawer');
    const rows = (auditList.children || []).filter((n) => (n.className || '').indexOf('audit__row') >= 0).length;
    if (rows < 1) throw new Error('the audit drawer shows ' + rows + ' rows for the audit data');
    if (moreNodes(inst.document, 'audit-chips').length !== 1) throw new Error('the audit chips exist more than once');

    inst.ui.moreClose(false);
    const importBefore = inst.ui.probe().stats.renders;
    if (inst.ui.moreOpen('import') !== true) throw new Error('the import entry did not open');
    if (inst.ui.probe().stats.renders - importBefore !== 1) throw new Error('opening import repaints ' + (inst.ui.probe().stats.renders - importBefore) + ' times');
    if (app.getAttribute('data-open') !== 'true') throw new Error('the phone sheet did not open for import: data-open=' + app.getAttribute('data-open'));
    if (app.getAttribute('data-more-focus') !== 'import') throw new Error('the import block is not marked: data-more-focus=' + app.getAttribute('data-more-focus'));
    if (inst.document.getElementById('sheet-import').hidden === true) throw new Error('the import block is hidden inside the sheet');
    if (ivFind(inst.document, '[data-hook="import-text"]').length !== 2) throw new Error('the import entry duplicated a panel: ' + ivFind(inst.document, '[data-hook="import-text"]').length + ' import editors');

    inst.ui.moreMenu(false);
    const settingsBefore = inst.ui.probe().stats.renders;
    if (inst.ui.moreOpen('settings') !== true) throw new Error('the settings entry did not open');
    s = inst.ui.moreState();
    if (s.screen !== 'settings') throw new Error('moreState.screen=' + s.screen);
    if (app.getAttribute('data-more-screen') !== 'settings') throw new Error('data-more-screen=' + app.getAttribute('data-more-screen'));
    if (app.getAttribute('data-more-open') !== 'false') throw new Error('a screen opened on top of a drawer');
    if (moreNodes(inst.document, 'settings-center').length !== 1) throw new Error('the settings centre exists ' + moreNodes(inst.document, 'settings-center').length + ' times');
    if (inst.document.getElementById('settings-center').hidden === true) throw new Error('the settings centre is hidden after the entry');
    moreNodes(inst.document, 'settings-close')[0].click();
    if (inst.ui.moreState().screen !== 'off') throw new Error('closing settings left the phone screen flag set');
    if (inst.document.getElementById('settings-center').hidden !== true) throw new Error('closing settings did not hide the centre');
    return 'export/audit open the single rail panel as a dialog drawer (head + back + close, rows painted), import opens the sheet block (still 2 editors), settings opens and closes the single centre; one render per phone action';
  });

  const cssText = read(path.join(HERE, 'styles.css'));
  const cssFrom = (marker) => {
    const at = cssText.indexOf(marker);
    if (at < 0) throw new Error('styles.css has no section for: ' + marker);
    return cssText.slice(at);
  };
  const cssMore = cssFrom('4.10 (t23): phone reachability');
  const insideApp = (document, selector) => {
    const node = ivFind(document, selector)[0];
    if (!node) throw new Error('no node for ' + selector);
    return !!(node.closest && node.closest('#app'));
  };

  check('4.10 phone reachability on a 375x812 screen: the box stays a 375px column, every layer is inside it, the drawer is box-sized, and the desktop sidebar stays the only entry', async () => {
    const menu = ivBoot('?probe=1&frame=375x812&moremenu=1');
    const menuProbe = menu.ui.probe();
    const menuApp = menu.document.getElementById('app');
    if (menuProbe.view !== 'mobile') throw new Error('the 375x812 box is not the phone layout: ' + menuProbe.view);
    if (menuApp.getAttribute('data-frame') !== '375x812') throw new Error('data-frame=' + menuApp.getAttribute('data-frame'));
    if (menuApp.getAttribute('data-frame-layout') !== 'mobile') throw new Error('data-frame-layout=' + menuApp.getAttribute('data-frame-layout'));
    if (menuProbe.rendered.moreEntries !== 6) throw new Error('the rig box lists ' + menuProbe.rendered.moreEntries + ' entries');
    if (menuProbe.rendered.moreSheetOpen !== true) throw new Error('the entry list is not open in the rig box');
    if (!insideApp(menu.document, '[data-hook="more-sheet"]')) throw new Error('the entry list escaped the app box');
    const dupesMenu = duplicateIds(menu.document);
    if (dupesMenu.length) throw new Error('duplicate ids with the list open: ' + dupesMenu.join(','));

    const drawer = ivBoot('?probe=1&frame=375x812&more=audit');
    const drawerProbe = drawer.ui.probe();
    if (drawerProbe.more.open !== true || drawerProbe.more.target !== 'audit') throw new Error('the rig box did not open the audit drawer: ' + JSON.stringify({ open: drawerProbe.more.open, target: drawerProbe.more.target }));
    if (drawerProbe.rendered.moreDrawerHead !== 1) throw new Error('probe.rendered.moreDrawerHead=' + drawerProbe.rendered.moreDrawerHead);
    if (drawerProbe.rendered.moreDrawerButtons !== 2) throw new Error('probe.rendered.moreDrawerButtons=' + drawerProbe.rendered.moreDrawerButtons);
    if (!insideApp(drawer.document, '[data-hook="more-drawer"]')) throw new Error('the drawer head escaped the app box');
    const dupesDrawer = duplicateIds(drawer.document);
    if (dupesDrawer.length) throw new Error('duplicate ids with the drawer open: ' + dupesDrawer.join(','));
    moreNodes(drawer.document, 'more-drawer-back').concat(moreNodes(drawer.document, 'more-drawer-close')).forEach((node) => {
      if (node.getAttribute('tabindex') !== '0') throw new Error('a drawer control is not keyboard reachable');
      if (!node.getAttribute('aria-label') && !String(node.textContent)) throw new Error('a drawer control has no name');
    });

    const screen = ivBoot('?probe=1&frame=375x812&more=settings');
    const screenProbe = screen.ui.probe();
    if (screenProbe.more.screen !== 'settings') throw new Error('the settings screen did not open in the rig box');
    if (screenProbe.rendered.settingsPanel !== 1) throw new Error('probe.rendered.settingsPanel=' + screenProbe.rendered.settingsPanel);
    if (!insideApp(screen.document, '[data-hook="settings-center"]')) throw new Error('the settings screen escaped the app box');

    /* the DOM shim has no layout engine, so the 375px contract is pinned as
     * the rules the browser must follow: the drawer is box-sized, never wider
     * than the phone, and the table that cannot fit gets its own scroll box */
    if (cssMore.indexOf('width: min(88vw, 340px)') < 0) throw new Error('the drawer is not capped to the phone width');
    if (cssMore.indexOf('width: min(88%, 340px)') < 0) throw new Error('the drawer is not capped inside the rig box');
    if (cssMore.indexOf('height: var(--frame-h, 812px)') < 0) throw new Error('the drawer does not follow the rig box height');
    if (cssMore.indexOf('position: absolute') < 0) throw new Error('the phone layers are not box-anchored inside the rig');
    if (cssMore.indexOf('overflow-x: hidden') < 0) throw new Error('an open phone layer can push the page sideways');
    if (cssMore.indexOf('.users__scroll') < 0 || cssMore.indexOf('overflow-x: auto') < 0) throw new Error('the user table has no scroll box on a phone');
    if (cssMore.indexOf('[data-more-open="true"][data-frame][data-frame-layout="mobile"] .rail') < 0) throw new Error('the rig box has no drawer rule that beats the hidden rail rule');
    if (cssMore.indexOf('[data-more-target="audit"] .rail__hook:not([data-hook="audit"])') < 0) throw new Error('the drawer does not show one panel at a time');
    const slowMotion = (cssMore.match(/(\d+)ms/g) || []).filter((ms) => parseInt(ms, 10) > 200);
    if (slowMotion.length) throw new Error('phone motion is longer than 200ms: ' + slowMotion.join(','));

    const kb = ivBoot('?probe=1&frame=375x812');
    kb.ui.moreMenu(true);
    kb.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
    if (kb.ui.moreState().menu !== false) throw new Error('Escape did not close the entry list');
    if (kb.document.getElementById('app').getAttribute('data-more-menu') !== 'off') throw new Error('data-more-menu stayed on after Escape');
    if (kb.document.activeElement !== moreNodes(kb.document, 'more-menu')[0]) throw new Error('Escape did not hand focus back to the entry button');
    kb.ui.moreOpen('export');
    kb.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
    if (kb.ui.moreState().open !== false) throw new Error('Escape did not close the drawer');
    if (kb.ui.moreState().menu !== false) throw new Error('Escape left the list open under the drawer');
    if (kb.document.activeElement !== moreNodes(kb.document, 'more-menu')[0]) throw new Error('Escape from the drawer lost the keyboard');
    kb.ui.state.sheetOpen = true;
    kb.ui.renderAll();
    kb.document.dispatchEvent({ type: 'keydown', key: 'Escape' });
    if (kb.ui.state.sheetOpen !== false) throw new Error('Escape no longer closes the action sheet');

    const desk = ivBoot('?probe=1&frame=1440x900');
    const deskProbe = desk.ui.probe();
    ['nav-zones', 'nav-timeline', 'nav-compare', 'nav-audit', 'nav-export', 'nav-settings'].forEach((hook) => {
      const links = moreNodes(desk.document, hook);
      if (links.length !== 1) throw new Error('the desktop sidebar entry ' + hook + ' exists ' + links.length + ' times');
    });
    if (deskProbe.rendered.sidebarItems < 1) throw new Error('the desktop sidebar lost its summary items');
    if (deskProbe.rendered.moreEntries !== 0) throw new Error('the phone list renders on desktop: ' + deskProbe.rendered.moreEntries);
    if (deskProbe.more.menu !== false || deskProbe.more.open !== false) throw new Error('a phone layer is open on desktop');
    if (desk.document.getElementById('timeline').getAttribute('aria-hidden') !== 'false') throw new Error('the desktop rail is hidden from assistive tech');
    return '375x812 box: list, drawer and settings screen stay inside #app with unique ids, drawer rules are box-sized (min(88vw/88%,340px) + --frame-h) with motion <=200ms; Escape closes list, drawer and sheet with focus back; desktop keeps 6 sidebar entries and 0 phone layers';
  });

  check('4.10 language entry: the list switches the interface in place, every entry title and reason repaints, and the more domain is complete in zh and en', () => {
    const zh = readJson(path.join(HERE, 'i18n', 'zh.json'));
    const en = readJson(path.join(HERE, 'i18n', 'en.json'));
    const hasHan = (s) => /[\u3400-\u4dbf\u4e00-\u9fff]/.test(String(s));
    const paramsOf = (s) => (String(s).match(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g) || []).sort().join(',');
    const zhKeys = Object.keys(zh.more || {}).map((k) => 'more.' + k);
    const enKeys = Object.keys(en.more || {}).map((k) => 'more.' + k);
    if (zhKeys.length < 28) throw new Error('the more domain has only ' + zhKeys.length + ' keys');
    if (zhKeys.sort().join(',') !== enKeys.sort().join(',')) throw new Error('zh and en more keys differ');
    zhKeys.forEach((key) => {
      const leaf = key.slice('more.'.length);
      if (!zh.more[leaf] || !en.more[leaf]) throw new Error('missing copy for ' + key);
      if (!hasHan(zh.more[leaf])) throw new Error(key + ' has no Chinese in zh.json');
      if (hasHan(en.more[leaf])) throw new Error(key + ' carries CJK in en.json');
      if (paramsOf(zh.more[leaf]) !== paramsOf(en.more[leaf])) throw new Error(key + ' parameter sets differ');
    });

    const inst = ivBoot('?probe=1&lang=zh');
    inst.ui.moreMenu(true);
    const titlesZh = MORE_PANELS.map((panel) => moreEntryTitle(moreEntry(inst.document, panel)));
    const labelsZh = MORE_PANELS.map((panel) => String(moreEntry(inst.document, panel).getAttribute('aria-label') || ''));
    if (inst.ui.lang() !== 'zh') throw new Error('the boot language is not zh: ' + inst.ui.lang());
    const before = inst.ui.probe().stats.renders;
    const languageEntry = moreEntry(inst.document, 'language');
    if (!languageEntry) throw new Error('the language entry is missing');
    if (languageEntry.getAttribute('data-enabled') !== 'true') throw new Error('the language entry is not available on a phone');
    if (String(languageEntry.textContent).indexOf(inst.ui.t('nav.lang.en')) < 0) throw new Error('the language entry does not name the language it switches to: ' + languageEntry.textContent);
    languageEntry.click();
    if (inst.ui.lang() !== 'en') throw new Error('the language entry did not switch to en: ' + inst.ui.lang());
    if (inst.ui.probe().stats.renders - before !== 1) throw new Error('a language switch repaints ' + (inst.ui.probe().stats.renders - before) + ' times');
    if (inst.ui.moreState().menu !== true) throw new Error('the list closed on a language switch, so the new copy is invisible');
    MORE_PANELS.forEach((panel, i) => {
      const node = moreEntry(inst.document, panel);
      const title = moreEntryTitle(node);
      if (title !== inst.ui.t('more.entry.' + panel)) throw new Error('entry ' + panel + ' title stayed "' + title + '" after the switch');
      if (title === titlesZh[i]) throw new Error('entry ' + panel + ' title did not change with the language');
      const label = String(node.getAttribute('aria-label') || '');
      if (!label) throw new Error('entry ' + panel + ' lost its aria-label after the switch');
      if (label === labelsZh[i]) throw new Error('entry ' + panel + ' aria-label did not follow the language');
    });
    if (inst.document.documentElement.getAttribute('lang') !== 'en') throw new Error('html lang after the switch: ' + inst.document.documentElement.getAttribute('lang'));
    if (inst.document.getElementById('app').getAttribute('data-lang') !== 'en') throw new Error('app data-lang after the switch');
    if (inst.ui.dataLang() !== 'en') throw new Error('the demo records did not follow the interface language: ' + inst.ui.dataLang());
    if (inst.ui.probe().missingKeys.length) throw new Error('missing keys after the switch: ' + inst.ui.probe().missingKeys.join(','));
    const feedback = ivFind(inst.document, '[data-hook="more-feedback"]')[0];
    if (feedback.getAttribute('data-visible') !== 'true' || String(feedback.textContent).indexOf(inst.ui.t('nav.lang.en')) < 0) throw new Error('the switch is not reported in the new language: ' + feedback.textContent);

    const offlineEn = ivBoot('?probe=1&api=off&lang=en');
    offlineEn.ui.moreMenu(true);
    MORE_PANELS.forEach((panel) => {
      const node = moreEntry(offlineEn.document, panel);
      if (moreEntryTitle(node) !== offlineEn.ui.t('more.entry.' + panel)) throw new Error(panel + ' title is not English on boot: ' + moreEntryTitle(node));
      const reason = moreReasonNode(offlineEn.document, panel);
      if (reason && String(reason.textContent) !== offlineEn.ui.t(reason.getAttribute('data-i18n'))) throw new Error(panel + ' reason is not English: ' + reason.textContent);
    });
    offlineEn.ui.moreOpen('users');
    const blockedEn = ivFind(offlineEn.document, '[data-hook="more-feedback"]')[0];
    if (String(blockedEn.textContent).indexOf(offlineEn.ui.t('more.reason.users')) < 0) throw new Error('the blocked reason is not in English: ' + blockedEn.textContent);
    return 'more domain: ' + zhKeys.length + ' keys in zh + en with matching parameters and no CJK in en; the entry switches zh->en in one render, all 6 titles + labels + reasons + demo data follow, desktop-free phone copy only';
  });

  /* ---------------------------------------------------------------- *
   * 4.12 (t25): the five defects QA 5.2 walked into, each pinned here.
   * B-01 language switch must not drop imported records or recorded decisions,
   * H-01 the audit panel must read before it claims anything, H-02 the panel
   * must follow this page's own writes, H-03 a decision made elsewhere must
   * repaint that card, H-04 the card hint must not wrap into the expand label.
   * ---------------------------------------------------------------- */

  const deepCopy = (value) => JSON.parse(JSON.stringify(value));

  const importedRows = (n) => Array.from({ length: n }, (_, i) => ({
    id: 'obs-lf-' + String(i + 1).padStart(4, '0'),
    schema_version: '1.1',
    action_type: 'tool_call',
    status: 'succeeded',
    intent: 'read the photo index',
    agent_id: 'langfuse-demo',
    actor_id: 'local',
    workspace_id: 'local',
    session_id: 'sess-import',
    timestamp: '2026-11-09T08:00:00+08:00',
    source: { kind: 'paste_json', uri: 'paste://import/probe', format: 'langfuse' },
    human_readable: { text: 'read the photo index (done)', level: 'l1', generated_by: 'trace_adapter' },
    demo_data: false,
    synthetic: true
  }));

  const approveRemote = (record) => {
    const rec = deepCopy(record);
    rec.status = 'approved';
    rec.requires_action = false;
    rec.decision = 'approve';
    rec.user_intervention = {
      decision: 'approve', recorded: true, decided_by_role: 'owner',
      reason: 'ship it', decided_at: '2026-11-09T09:00:00Z',
      audit_entry_hash: 'sha256:' + '7'.repeat(64), modified_fields: []
    };
    return rec;
  };

  const auditPageOf = (entries) => ({
    status: 200,
    headers: { 'x-request-id': 'req-audit' },
    data: {
      entries: entries.slice(),
      chain: { ok: true, count: entries.length, head: 'sha256:head', problems: [] },
      meta: { mode: 'local', durable: true, count: entries.length },
      jsonl: { path: 'var/log/audit.jsonl', count: entries.length, head: 'sha256:head', ok: true, problems: [] }
    }
  });

  check('4.12 B-01: switching language over a live server keeps 20 records, the recorded decision, the filter and the place', async () => {
    const store = { rows: loadDemoPayload().records.map(deepCopy), reads: 0 };
    importedRows(8).forEach((r) => store.rows.push(r));
    const handlers = {
      health: authHealth('local'),
      authStatus: authStatusPayload('local'),
      authMe: () => ({ status: 200, data: authMePayload('local', ALL_PERMS, 'local'), headers: {} }),
      authPermissions: () => ({ status: 200, data: { roles: ['local'], permissions: ALL_PERMS, matrix: {} }, headers: {} }),
      records: () => {
        store.reads += 1;
        return {
          status: 200, headers: {},
          data: { records: store.rows, meta: { count: store.rows.length, records_durable: true } }
        };
      },
      audit: () => auditPageOf([auditEntry(0), auditEntry(1), auditEntry(2)])
    };
    const inst = authBoot(handlers, '?probe=1');
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await settle();
    if (inst.ui.probe().api.mode !== 'live') throw new Error('not a live session: ' + inst.ui.probe().api.mode);
    if (inst.ui.state.records.length !== 20) throw new Error('the store serves 20 rows, the page shows ' + inst.ui.state.records.length);

    /* the operator's own place: one open card, the timeline on record 7 */
    inst.ui.toggleCard('act-demo-0009');
    inst.ui.setTimelineIndex(6);
    inst.ui.toggleFilter('done');
    await settle();
    const filterBefore = JSON.stringify(inst.ui.probe().desk.filter);
    const expandedBefore = inst.ui.probe().expanded.slice();
    if (expandedBefore.indexOf('act-demo-0009') < 0) throw new Error('the card did not open before the switch');
    if (inst.ui.probe().desk.timeline.index !== 6) throw new Error('the timeline did not move before the switch');

    /* the decision arrives from the API, exactly as in the QA walk */
    const at = store.rows.findIndex((r) => r.id === 'act-demo-0004');
    store.rows[at] = approveRemote(store.rows[at]);
    const fresh = await inst.ui.refreshRecords({ reason: 'remote-decision' });
    await settle();
    if (!fresh || fresh.updated !== 1) throw new Error('the remote decision did not reach the list: ' + JSON.stringify(fresh));

    const readsBefore = store.reads;
    inst.ui.setLang('en');
    await settle();
    const p = inst.ui.probe();
    if (inst.ui.dataLang() !== 'en') throw new Error('the data language did not follow the interface');
    if (p.records !== 20) throw new Error('the language switch dropped the list to ' + p.records + ' records');
    const ids = inst.ui.state.records.map((r) => r.id);
    importedRows(8).forEach((r) => {
      if (ids.indexOf(r.id) < 0) throw new Error('the imported record ' + r.id + ' vanished on a language switch');
    });
    const decided = inst.ui.state.records.find((r) => r.id === 'act-demo-0004');
    if (!decided || !decided.user_intervention || decided.user_intervention.decision !== 'approve') {
      throw new Error('the approved action asks for a second decision after the switch: ' + JSON.stringify(decided && decided.user_intervention));
    }
    if (JSON.stringify(p.desk.filter) !== filterBefore) throw new Error('the filter changed: ' + filterBefore + ' -> ' + JSON.stringify(p.desk.filter));
    if (p.expanded.indexOf('act-demo-0009') < 0) throw new Error('the open card closed on a language switch: ' + JSON.stringify(p.expanded));
    if (p.desk.timeline.index !== 6) throw new Error('the timeline moved to ' + p.desk.timeline.index);
    if (store.reads <= readsBefore) throw new Error('live mode did not re-read api/records after the switch (' + readsBefore + ' -> ' + store.reads + ')');
    if (inst.ui.t('card.expand').indexOf('Expand') < 0) throw new Error('the copy did not switch to English');

    inst.ui.setLang('zh');
    await settle();
    const back = inst.ui.probe();
    if (back.records !== 20) throw new Error('switching back to zh dropped the list to ' + back.records);
    const again = inst.ui.state.records.find((r) => r.id === 'act-demo-0004');
    if (!again.user_intervention || again.user_intervention.decision !== 'approve') throw new Error('zh lost the recorded decision');
    if (back.desk.timeline.index !== 6) throw new Error('the round-trip lost the timeline position');
    return 'live: 20 records + 8 imported + an API-approved 0004 survive zh->en->zh; '
      + store.reads + ' api/records reads (the switch re-read), filter/open card/timeline #6 kept';
  });

  check('4.12 B-01 offline: the localized demo set is swapped, the operator records and the recorded decision are carried, one repaint', async () => {
    const inst = boot({ search: '', language: 'zh-CN' });
    const rows = loadDemoPayload().records.map(deepCopy);
    importedRows(8).forEach((r) => rows.push(r));
    inst.ui.setRecords(rows);
    if (inst.ui.state.records.length !== 20) throw new Error('the offline page shows ' + inst.ui.state.records.length + ' records');
    inst.ui.toggleCard('act-demo-0009');
    inst.ui.setTimelineIndex(4);
    const out = await inst.ui.submitIntervene('act-demo-0004', 'approve', { reason: 'ship it' });
    await settle();
    if (!out || out.ok !== true) throw new Error('the offline decision did not go through: ' + JSON.stringify(out));
    const decidedBefore = inst.ui.state.records.find((r) => r.id === 'act-demo-0004');
    if (!decidedBefore.user_intervention || decidedBefore.user_intervention.decision !== 'approve') {
      throw new Error('no local decision to carry: ' + JSON.stringify(decidedBefore.user_intervention));
    }
    const renders = inst.ui.state.stats.renders;
    inst.ui.setLang('en');
    const repaints = inst.ui.state.stats.renders - renders;
    if (repaints !== 1) throw new Error('the language switch repainted ' + repaints + ' times, expected exactly 1');

    const enIds = loadDemoPayloadEn().records.map((r) => r.id);
    const ids = inst.ui.state.records.map((r) => r.id);
    if (ids.length !== 20) throw new Error('the offline switch dropped the list to ' + ids.length);
    if (JSON.stringify(ids.slice(0, 12)) !== JSON.stringify(enIds)) throw new Error('the 12 demo rows did not follow the English set');
    importedRows(8).forEach((r) => {
      if (ids.indexOf(r.id) < 0) throw new Error('the operator record ' + r.id + ' vanished on a language switch');
    });
    const twin = inst.ui.state.records.find((r) => r.id === 'act-demo-0004');
    if (!twin.user_intervention || twin.user_intervention.decision !== 'approve') throw new Error('the recorded decision was left behind in Chinese');
    if (twin.status !== 'approved') throw new Error('the decided state was not carried: ' + twin.status);
    const enText = String(loadDemoPayloadEn().records.find((r) => r.id === 'act-demo-0004').human_readable.text);
    if (String(twin.human_readable.text) !== enText) throw new Error('the sentence did not follow the English copy');
    if (inst.ui.state.stats.dataLangOwn !== 8) throw new Error('the operator records carried = ' + inst.ui.state.stats.dataLangOwn + ', expected 8');
    if (inst.ui.state.stats.dataLangDecided < 1) throw new Error('no recorded decision was carried: ' + inst.ui.state.stats.dataLangDecided);
    if (inst.ui.state.stats.dataLangCarried !== inst.ui.state.stats.dataLangOwn + inst.ui.state.stats.dataLangDecided) {
      throw new Error('the carried counter does not add up: ' + JSON.stringify({
        carried: inst.ui.state.stats.dataLangCarried,
        own: inst.ui.state.stats.dataLangOwn,
        decided: inst.ui.state.stats.dataLangDecided
      }));
    }
    const p = inst.ui.probe();
    if (p.expanded.indexOf('act-demo-0009') < 0) throw new Error('the open card closed: ' + JSON.stringify(p.expanded));
    if (p.desk.timeline.index !== 4) throw new Error('the timeline moved to ' + p.desk.timeline.index);
    inst.ui.setLang('zh');
    if (inst.ui.state.records.length !== 20) throw new Error('the zh round-trip dropped the list');
    const zh = inst.ui.state.records.find((r) => r.id === 'act-demo-0004');
    if (zh.status !== 'approved') throw new Error('the round-trip lost the decision');
    return 'offline: 20 records (12 localized demo + 8 operator rows) and the approved 0004 survive zh->en->zh, '
      + inst.ui.state.stats.dataLangCarried + ' records carried (' + inst.ui.state.stats.dataLangOwn
      + ' operator rows + ' + inst.ui.state.stats.dataLangDecided + ' recorded decisions), English sentences, '
      + repaints + ' repaint, card + timeline #4 kept';  });

  check('4.12 H-01: the audit panel reads the store on its own - no "0 entries / in-memory chain" over real data', async () => {
    const live = [auditEntry(0), auditEntry(1), auditEntry(2)];
    const auditReads = { n: 0 };
    const inst = auditBoot({ total: 3 }, {
      audit: () => { auditReads.n += 1; return auditPageOf(live); }
    });
    /* before the first read the panel may not claim a chain, a count or
     * a store mode: it says it is reading, and it says so in words. */
    if (ivFind(inst.document, '[data-hook="audit-store"]').length) throw new Error('the panel claims a store mode before reading the server');
    if (ivFind(inst.document, '[data-hook="audit-count"]').length) throw new Error('the panel claims an entry count before reading the server');
    if (!ivFind(inst.document, '[data-hook="audit-chain"][data-ok="pending"]').length) throw new Error('the chain chip does not say it is reading');
    if (!ivFind(inst.document, '[data-hook="audit-chain"]')[0].getAttribute('data-i18n')) throw new Error('the pending chain chip has no dictionary copy');
    if (ivFind(inst.document, '[data-hook="audit-loading"]').length + ivFind(inst.document, '[data-hook="audit-pending"]').length < 1) {
      throw new Error('the panel shows a bare empty list with no explanation (audit: ' + JSON.stringify(inst.ui.audit()) + ')');
    }

    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await settle();
    const snap = inst.ui.audit();
    if (snap.loaded !== true) throw new Error('the panel never read the server: ' + JSON.stringify(snap));
    if (snap.autoLoads !== 1) throw new Error('auto-read counter = ' + snap.autoLoads);
    if (snap.requests !== 1) throw new Error('the first paint asked for ' + snap.requests + ' audit pages');
    if (snap.rows !== 3) throw new Error('3 entries on disk, the panel shows ' + snap.rows);
    if (auditReads.n !== 1) throw new Error('the first paint sent ' + auditReads.n + ' audit reads, expected exactly 1');
    const chain = ivFind(inst.document, '[data-hook="audit-chain"]')[0];
    if (chain.getAttribute('data-ok') !== 'true') throw new Error('a healthy chain is not reported after the read');
    const store = ivFind(inst.document, '[data-hook="audit-store"]')[0];
    if (!store || store.getAttribute('data-i18n') !== 'audit.chip.durable') throw new Error('the durable store is not labelled after the read');
    if (String(ivFind(inst.document, '[data-hook="audit-count"]')[0].textContent).indexOf('3') < 0) throw new Error('the count chip does not show the 3 entries');
    if (ivFind(inst.document, '[data-hook="audit-pending"]').length) throw new Error('the pending line survived the read');
    if (ivFind(inst.document, '[data-hook="audit-row"]').length !== 3) throw new Error('the 3 entries are not on screen');

    /* offline: the panel must say there is nothing to talk to, and ask for nothing */
    const off = auditBoot({}, null, '?probe=1&api=off');
    await off.ui.probeApi();
    await off.ui.authProbe();
    await settle();
    const offSnap = off.ui.audit();
    if (offSnap.autoLoads !== 0 || offSnap.requests !== 0) throw new Error('the offline panel sent ' + offSnap.requests + ' audit requests');
    if (offSnap.loaded !== false) throw new Error('offline is reported as a read chain');
    if (ivFind(off.document, '[data-hook="audit-store"]').length) throw new Error('offline claims a store mode');
    const offLine = ivFind(off.document, '[data-hook="audit-pending"]')[0];
    if (!offLine || offLine.getAttribute('data-i18n') !== 'audit.error.offline') throw new Error('offline says nothing about why the list is empty');
    const loading = ivFind(off.document, '[data-hook="audit-chip"]')[0];
    if (loading && String(ivFind(off.document, '[data-hook="audit-chain"]')[0].textContent).indexOf('reading') >= 0) {
      throw new Error('the copy is not the interface language: ' + loading.textContent);
    }
    return 'first paint: 1 GET /api/audit (no click) -> 3 rows, chain ok + durable + "3 entries"; '
      + 'before the read: no count/store chip, dashed chain, one honest pending line; offline: 0 requests';
  });

  check('4.12 H-02: a decision and an export made in this page appear in the audit panel without a click', async () => {
    const entries = [auditEntry(0), auditEntry(1)];
    const handlers = {
      audit: () => auditPageOf(entries),
      auditExport: () => ({
        status: 200,
        data: auditExportBody,
        headers: {
          'x-request-id': 'req-export',
          'x-tracelens-export-format': 'csv',
          'x-tracelens-export-rows': String(entries.length),
          'x-tracelens-export-sha256': 'sha256:' + 'a'.repeat(64)
        }
      }),
      intervene: () => {
        const rec = approveRemote(inst.ui.state.records.find((r) => r.id === 'act-demo-0004'));
        entries.push(Object.assign(auditEntry(entries.length), {
          decision: 'approve', event: 'decision', action_id: 'act-demo-0004'
        }));
        return { status: 200, headers: { 'x-request-id': 'req-iv' }, data: rec };
      }
    };
    const inst = auditBoot({ total: 2 }, handlers);
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await settle();
    if (inst.ui.audit().loaded !== true) throw new Error('the panel did not read the 2 entries at boot');
    if (inst.ui.audit().rows !== 2) throw new Error('boot rows = ' + inst.ui.audit().rows);
    const requestsBefore = inst.ui.audit().requests;

    const out = await inst.ui.submitIntervene('act-demo-0004', 'approve', { reason: 'ship it' });
    await settle();
    if (!out || out.ok !== true) throw new Error('the live decision failed: ' + JSON.stringify(out));
    const afterDecision = inst.ui.audit();
    if (afterDecision.autoRefreshes !== 1) throw new Error('the panel did not follow the decision: ' + JSON.stringify(afterDecision));
    if (afterDecision.requests !== requestsBefore + 1) throw new Error('the decision read ' + (afterDecision.requests - requestsBefore) + ' audit pages');
    if (afterDecision.rows !== 3) throw new Error('the new entry is not on screen: ' + afterDecision.rows + ' rows');
    const decisions = ivFind(inst.document, '[data-hook="audit-decision"]').map((n) => n.getAttribute('data-decision'));
    if (decisions.filter((d) => d === 'approve').length < 2) throw new Error('the panel does not show the decision this page made: ' + JSON.stringify(decisions));
    const feedback = ivFind(inst.document, '[data-hook="audit-feedback"]')[0];
    if (feedback.getAttribute('data-i18n') !== 'audit.feedback.refreshed') throw new Error('the panel does not say it re-read the log: ' + feedback.getAttribute('data-i18n'));

    const exported = await inst.ui.exportAudit('csv');
    await settle();
    if (!exported || exported.ok !== true) throw new Error('the export failed: ' + JSON.stringify(exported));
    if (inst.ui.audit().autoRefreshes !== 2) throw new Error('the panel did not follow the export: ' + JSON.stringify(inst.ui.audit()));
    if (inst.ui.audit().lastWriteReason.indexOf('export:') !== 0) throw new Error('the reason for the re-read is not recorded: ' + inst.ui.audit().lastWriteReason);

    /* one read per write: a second click on refresh is a manual read, not two */
    const manual = await inst.ui.auditRefresh();
    await settle();
    if (manual.ok !== true) throw new Error('the manual refresh failed: ' + JSON.stringify(manual));
    if (inst.ui.audit().autoRefreshes !== 2) throw new Error('a manual refresh counted as an auto write: ' + JSON.stringify(inst.ui.audit()));
    return 'decision -> 1 auto re-read (rows 2->3, approve visible, audit.feedback.refreshed), export -> 1 more, '
      + 'manual refresh stays manual (auto 2)';
  });

  check('4.12 H-03: a decision made in another window repaints that card, an unchanged frame still does not repaint', async () => {
    const entries = [auditEntry(0)];
    const inst = auditBoot({ total: 1 }, { audit: () => auditPageOf(entries) });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    await settle();
    const view = inst.ui.state.records.find((r) => r.id === 'act-demo-0009');
    inst.ui.toggleCard('act-demo-0009');
    await settle();
    const beforeNode = cardById(inst.document.body, 'act-demo-0009');
    if (!beforeNode) throw new Error('the card of the record to be decided is not on screen');
    const colorBefore = String(beforeNode.getAttribute('data-color'));
    const zoneBefore = String(beforeNode.getAttribute('data-zone'));
    const pushes = deepCopy(view);
    /* the QA 5.2 trap: every top-level field the old comparison looked at is
     * identical; only the recorded decision under user_intervention moved */
    pushes.decision = view.decision;
    pushes.status = view.status;
    pushes.result_summary = view.result_summary;
    pushes.latency_ms = view.latency_ms;
    pushes.timestamp = view.timestamp;
    pushes.user_intervention = {
      decision: 'approve', recorded: true, decided_by_role: 'editor',
      reason: 'from the other window', audit_entry_hash: 'sha256:' + '9'.repeat(64)
    };
    pushes.execution_receipt = { state: 'completed' };
    const applied = inst.ui.applyStreamEvent('action.decision_recorded', {
      record: pushes, action_id: 'act-demo-0009', decision: 'approve'
    }, 12);
    if (!applied || applied.applied !== true) throw new Error('the decision frame was not applied: ' + JSON.stringify(applied));
    if (applied.updated !== 1) throw new Error('the decision frame was read as "nothing changed": ' + JSON.stringify(applied));
    const now = inst.ui.state.records.find((r) => r.id === 'act-demo-0009');
    if (!now.user_intervention || now.user_intervention.decision !== 'approve') throw new Error('the card does not show the remote decision');
    if (inst.ui.probe().realtime.decisions !== 1) throw new Error('the decision frame was not counted: ' + JSON.stringify(inst.ui.probe().realtime));
    const cardNode = cardById(inst.document.body, 'act-demo-0009');
    if (!cardNode) throw new Error('the card of the decided record is gone');
    if (String(beforeNode.getAttribute('data-decided')) !== 'false') throw new Error('the card claimed a decision before it happened');
    const decidedMark = cardNode.getAttribute('data-decided') === 'true';
    const colorAfter = String(cardNode.getAttribute('data-color'));
    const zoneAfter = String(cardNode.getAttribute('data-zone'));
    if (!decidedMark) {
      throw new Error('the card gives no visible sign that the record is decided (color ' + colorBefore +
        ' -> ' + colorAfter + ', zone ' + zoneBefore + ' -> ' + zoneAfter + ')');
    }

    /* the cheap path stays cheap: the same record again changes nothing */
    const quiet = inst.ui.mergeIncomingRecords([now]);
    if (quiet.added !== 0 || quiet.updated !== 0) throw new Error('an unchanged record was counted as a change: ' + JSON.stringify(quiet));

    /* the sign is not only in the DOM: the stylesheet styles the decided card */
    const decidedCss = cssFrom('4.12 (t25): card affordance row');
    const mark = (/\n\.card\[data-decided="true"\] \.card__dot \{([^}]*)\}/.exec(decidedCss) || [])[1];
    if (!mark || mark.indexOf('box-shadow') < 0) throw new Error('a decided card has no visual sign in the stylesheet');
    if (mark.indexOf('var(--c-ok-line)') < 0) throw new Error('the decided mark uses an undefined token: ' + mark);

    /* and a full store read (the poll, the language switch) replaces it too */
    const store = inst.ui.state.records.map(deepCopy);
    const at = store.findIndex((r) => r.id === 'act-demo-0006');
    store[at] = approveRemote(store[at]);
    inst.ui.setApiTransport((endpoint) => (endpoint === 'records'
      ? { status: 200, headers: {}, data: { records: store, meta: { count: store.length } } }
      : auditPageOf(entries)));
    const fresh = await inst.ui.refreshRecords({ reason: 'poll' });
    await settle();
    if (!fresh || fresh.updated !== 1) throw new Error('the store read did not pick the decision up: ' + JSON.stringify(fresh));
    if (!inst.ui.state.records.find((r) => r.id === 'act-demo-0006').user_intervention) throw new Error('the refreshed record did not land in the view');
    return 'decision frame with identical top-level fields -> updated=1, card flips data-decided false->true ('
      + 'colour ' + colorBefore + '->' + colorAfter + ', zone ' + zoneBefore + '->' + zoneAfter + '); '
      + 'unchanged merge -> 0 changes; a store re-read updates the same card by id';
  });

  check('4.12 H-04: the card hint is its own full-width row, the expand label keeps its column (1400px repro)', () => {
    const css = cssFrom('4.12 (t25): card affordance row');
    const grid = (/\n\.card__toggle \{([^}]*)\}/.exec(css) || [])[1];
    if (!grid) throw new Error('the 4.12 block has no .card__toggle rule');
    if (grid.indexOf('display: grid') < 0) throw new Error('the card head is not a grid: ' + grid);
    if (grid.indexOf('grid-template-columns: auto minmax(0, 1fr) auto') < 0) throw new Error('the card head has no dot/title/expand columns: ' + grid);
    const hint = (/\n\.card__toggle \.card__hint \{([^}]*)\}/.exec(css) || [])[1];
    if (!hint) throw new Error('the hint has no rule of its own');
    if (hint.indexOf('grid-column: 2') < 0 || hint.indexOf('grid-row: 2') < 0) throw new Error('the hint is not a full-width row under the title: ' + hint);
    if (hint.indexOf('overflow-wrap: normal') < 0) throw new Error('the hint may still break one character per line: ' + hint);
    const chevron = (/\n\.card__toggle \.card__chevron \{([^}]*)\}/.exec(css) || [])[1];
    if (!chevron || chevron.indexOf('white-space: nowrap') < 0) throw new Error('the expand label can wrap into the hint: ' + chevron);
    if (chevron.indexOf('grid-column: 3') < 0) throw new Error('the expand label shares the title column: ' + chevron);
    const squeeze = (/@media \(min-width: 1024px\) and \(max-width: 1440px\) \{\s*\.zones \{([^}]*)\}/.exec(css) || [])[1];
    if (!squeeze || squeeze.indexOf('minmax(320px, 1fr)') < 0) throw new Error('a zone column can still be squeezed below a readable card at 1400px: ' + squeeze);

    const inst = bootOnce();
    inst.ui.setLang('zh');
    const toggles = ivFind(inst.document, 'button.card__toggle');
    const collapsed = cards(inst.document.body).filter((c) => queryAll(c, '[data-card-detail]')[0]
      && queryAll(c, '[data-card-detail]')[0].getAttribute('data-expanded') !== 'true');
    if (!collapsed.length) throw new Error('no collapsed card to inspect');
    collapsed.forEach((c) => {
      const id = c.getAttribute('data-id');
      const toggle = findToggle(inst.document.body, id);
      if (!toggle) throw new Error('card ' + id + ' has no toggle button');
      const kids = (toggle.children || []).map((n) => String(n.className));
      ['card__dot', 'card__l1', 'card__hint', 'card__chevron'].forEach((cls) => {
        if (kids.indexOf(cls) < 0) throw new Error('card ' + id + ' head is missing ' + cls + ' (grid: ' + kids.join(' ') + ')');
      });
      const hint = queryAll(toggle, 'span.card__hint')[0];
      if (!String(hint.textContent || '')) throw new Error('card ' + id + ' has an empty hint row');
      const chevron = queryAll(toggle, 'span.card__chevron')[0];
      if (String(chevron.textContent || '') !== inst.ui.t('card.expand')) throw new Error('card ' + id + ' expand label is not dictionary copy');
    });
    const opened = cards(inst.document.body).find((c) => queryAll(c, '[data-card-detail]')[0]
      && queryAll(c, '[data-card-detail]')[0].getAttribute('data-expanded') === 'true');
    if (opened && findToggle(inst.document.body, opened.getAttribute('data-id'))) {
      const t = findToggle(inst.document.body, opened.getAttribute('data-id'));
      if (queryAll(t, 'span.card__hint').length) throw new Error('an open card still asks the operator to open it');
    }
    const p = inst.ui.probe();
    if (p.rendered.cardHints < collapsed.length) throw new Error('only ' + p.rendered.cardHints + ' hint rows for ' + collapsed.length + ' collapsed cards');
    if (p.rendered.cardToggles !== cards(inst.document.body).length) throw new Error('card heads: ' + p.rendered.cardToggles + ' vs ' + cards(inst.document.body).length + ' cards');
    const l1 = queryAll(findToggle(inst.document.body, 'act-demo-0004'), 'span.card__l1')[0];
    if (!String(l1.textContent || '').trim()) throw new Error('the one-line summary is gone (the 3.1 contract)');
    return 'grid head: dot | title | expand columns, hint on its own row 2 (no per-character CJK wrap), '
      + 'chevron nowrap in column 3, zone columns >=320px at 1024-1440px; '
      + p.rendered.cardHints + ' hint rows / ' + p.rendered.cardToggles + ' card heads in the DOM';
  });

  /* R1-9 (D-1): the cold-start integration test over the REAL card path.
   * `.scratch/r16/dom-selftest.mjs` built its cards by hand, so the whole
   * first-paint class of bugs was invisible to it (R1-7 §9 D-1). Here the
   * cards come from app.js's own renderCard, the layer comes from the shipped
   * ext/core.js + ext/cards.js, and the script order is index.html's. */
  check('R1-9 D-1 cold start: the first screen carries the rollback entry (real renderCard path)', async () => {
    const layer = bootExtLayer({ search: '?view=desktop' });
    const doc = layer.document;
    const ui = layer.ui;
    const ext = layer.ext;
    const paint = layer.firstPaint;
    const rows = () => doc.querySelectorAll('.cards__rollback');
    const buttons = () => doc.querySelectorAll('.cards__rollback-btn');
    const marks = () => doc.querySelectorAll('[data-trust-decorated]');

    /* 1. the page's own first paint (app.js boots itself, before this layer is
     * parsed): real cards, and no trust decoration at all. That is the D-1
     * surface, recorded as data rather than argued about. */
    if (paint.cards < 1) throw new Error('the first paint built no card: app.js did not boot');
    if (paint.details !== paint.cards) throw new Error('a card has no detail host: ' + paint.details + '/' + paint.cards);
    if (paint.rollbackRows !== 0 || paint.rollbackButtons !== 0) {
      throw new Error('the trust layer painted before it existed: rows=' + paint.rollbackRows
        + ' buttons=' + paint.rollbackButtons);
    }

    /* 2. the shell boots (what index.html does on its DOMContentLoaded
     * timeout). R1-9 forces one decorate pass there, so the first screen -
     * with no repaint, no SSE frame and no dictionary refresh - has its entry. */
    ext.boot();
    /* the data side of the same gate: a row offers a button exactly where the
     * registry facts and the server's read-time view allow one (R1-6 G-21).
     * The point of pinning it here is that the cold-start repair must not have
     * moved that line: the entry shows on the first screen, and nowhere else. */
    const view = (() => {
      const meta = ui.metaInfo() || {};
      return meta.rollback_view && typeof meta.rollback_view === 'object' ? meta.rollback_view : {};
    })();
    const setId = (list, pick) => new Set((Array.isArray(list) ? list : [])
      .map((item) => (pick ? String(pick(item)) : String(item))).filter((v) => v !== ''));
    const rolled = setId(view.rolled_back);
    const kept = setId(view.kept_declared);
    const blocked = setId(view.blocked, (e) => (e && typeof e === 'object' ? e.action_id : e));
    const journalBad = !!(view.journal && typeof view.journal === 'object' && view.journal.ok === false);
    const expected = (loadDemoPayload().records || []).filter((rec) => {
      const tool = rec.tool_used && typeof rec.tool_used === 'object' ? String(rec.tool_used.name || '') : '';
      const scope = rec.impact_scope && typeof rec.impact_scope === 'object' ? rec.impact_scope : null;
      const id = String(rec.id || '');
      return !!tool
        && String(rec.action_type || '') !== 'rollback'
        && !rolled.has(id) && !blocked.has(id) && !kept.has(id)
        && !journalBad
        && !(scope && scope.reversible === false);
    }).length;
    const gate = 'journal-unreadable=' + journalBad + ', rolled=' + rolled.size
      + ', blocked=' + blocked.size + ', kept=' + kept.size
      + ', irreversible=' + (loadDemoPayload().records || [])
        .filter((rec) => rec.impact_scope && rec.impact_scope.reversible === false).length;
    const afterBoot = { rows: rows().length, buttons: buttons().length, marks: marks().length };
    if (afterBoot.rows !== paint.cards) {
      throw new Error('the first screen has ' + afterBoot.rows + ' rollback rows for ' + paint.cards
        + ' cards: the boot pass did not decorate every card');
    }
    if (afterBoot.buttons !== expected) {
      throw new Error('the first screen has ' + afterBoot.buttons + ' rollback entries, the gate allows '
        + expected + ' (' + gate + ')');
    }
    buttons().forEach((btn) => {
      const row = btn.closest('.cards__rollback');
      if (!row) throw new Error('a rollback button lives outside its detail row');
      if (!row.closest('article.card')) throw new Error('a rollback row lives outside a card');
      const label = String(btn.textContent || '').trim();
      if (!label) throw new Error('the first-screen rollback button has no copy');
      if (label.indexOf('cards.rollback.button') === 0 || label.indexOf('{op}') >= 0) {
        throw new Error('the first-screen rollback button is unresolved copy: ' + label);
      }
    });
    /* D-4 honesty on the first screen: a card with nothing to undo says so in
     * its own row instead of showing a dead button. */
    rows().forEach((row) => {
      if (row.querySelectorAll('.cards__rollback-btn').length) return;
      if (!row.querySelectorAll('.cards__note').length) {
        throw new Error('a card with no rollback entry has no explanation in its row: '
          + (row.closest('article.card') ? row.closest('article.card').getAttribute('data-id') : '?'));
      }
    });
    const stateAfterBoot = ext.state();
    if (stateAfterBoot.decorated < 1) throw new Error('boot forced no decoration pass: ' + stateAfterBoot.decorated);
    if (stateAfterBoot.watcher !== 'mutation') throw new Error('the card-tree watcher is not installed: ' + stateAfterBoot.watcher);

    /* 3. the second D-1 path: expanding a card calls app.js renderZones()
     * alone - no renderAll, no repaint. Since M2-PRE 1 (PM-VERDICT-R1-9
     * ruling 3) that rebuild ends with notify(), so the shell's listener pass
     * is the main path: the rebuilt cards are decorated inside the same click,
     * with no repaint and no timer in between. Let the page settle first so the
     * app has no pass left in its own chain: from then on a decorated list can
     * only come from the notify that renderZones fires itself. */
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (rows().length !== paint.cards) {
      throw new Error('the settled page lost its decoration: rows=' + rows().length + ' of ' + paint.cards);
    }
    const settled = ext.state();
    const settledMarks = marks().length;
    const rendersBefore = ui.probe().stats.renders;
    const card = doc.querySelectorAll('article.card')[0];
    const id = card.getAttribute('data-id');
    const toggle = doc.querySelectorAll('article.card#card-' + id + ' button.card__toggle')[0]
      || card.querySelectorAll('button.card__toggle')[0];
    if (!toggle) throw new Error('the real card has no expand button');
    toggle.dispatchEvent({ type: 'click' });
    const rendersAfterToggle = ui.probe().stats.renders;
    if (rendersAfterToggle !== rendersBefore) {
      throw new Error('the expand path is no longer the rebuild-without-repaint D-1 measured: renders '
        + rendersBefore + ' -> ' + rendersAfterToggle);
    }
    /* Reading the DOM right after the click, with no timer run in between, is
     * the shape of the main path: the notify is synchronous, so anything the
     * watcher could only add later must still be missing here. */
    const rightAfterToggle = { rows: rows().length, buttons: buttons().length, marks: marks().length };
    if (rightAfterToggle.rows !== paint.cards) {
      throw new Error('renderZones no longer notifies on its own: rows right after the click='
        + rightAfterToggle.rows + ' of ' + paint.cards);
    }
    if (rightAfterToggle.buttons !== expected) {
      throw new Error('the same-click restore has ' + rightAfterToggle.buttons
        + ' rollback entries, the gate allows ' + expected + ' (' + gate + ')');
    }
    if (rightAfterToggle.marks !== settledMarks) {
      throw new Error('the decoration is not idempotent across a rebuild: marks ' + settledMarks
        + ' -> ' + rightAfterToggle.marks);
    }
    const stateAfterToggle = ext.state();
    /* The shell's own counters tell the two paths apart: `renders` counts the
     * notifies it heard (the main path), `decorated` the debounced/forced passes
     * it ran on its own. One expand click must reach the shell exactly once. */
    if (stateAfterToggle.renders !== settled.renders + 1) {
      throw new Error('the zones rebuild notified the shell ' + (stateAfterToggle.renders - settled.renders)
        + ' times, expected exactly 1 (renders ' + settled.renders + ' -> ' + stateAfterToggle.renders + ')');
    }
    /* The window is generous on purpose: the claim is "the restore needs no
     * repaint", not "the restore is under N milliseconds". A loaded Node event
     * loop can delay a 16ms timer by an order of magnitude - and the passes
     * that follow a decorated list (renderAll's own trailing notify, the
     * debounced watcher) must add nothing to it. */
    await new Promise((resolve) => setTimeout(resolve, 500));
    const restored = { rows: rows().length, buttons: buttons().length, marks: marks().length };
    const rendersAfterRestore = ui.probe().stats.renders;
    if (restored.rows !== paint.cards) {
      throw new Error('after a zones rebuild the settled list has ' + restored.rows
        + ' rollback rows for ' + paint.cards + ' cards (immediately after the click: '
        + rightAfterToggle.rows + ', repaints ' + rendersBefore + ' -> ' + rendersAfterRestore + ')');
    }
    if (restored.marks !== settledMarks) {
      throw new Error('a later pass duplicated a decorated surface: marks ' + settledMarks
        + ' -> ' + restored.marks);
    }
    if (rendersAfterRestore !== rendersBefore) {
      throw new Error('the restore rode on a repaint, not on the shell: renders ' + rendersBefore
        + ' -> ' + rendersAfterRestore);
    }
    if (restored.buttons !== expected) {
      throw new Error('after the zones rebuild the list has ' + restored.buttons
        + ' rollback entries, the gate allows ' + expected + ' (' + gate + ')');
    }
    const opened = doc.querySelectorAll('article.card.is-expanded');
    if (opened.length < 1) throw new Error('the expand click did not expand a card');
    const openDetail = opened[0].querySelectorAll('.card__detail')[0];
    if (!openDetail || openDetail.hidden) throw new Error('the expanded card has no visible detail host');
    if (!openDetail.querySelectorAll('.cards__rollback').length) {
      throw new Error('the opened card has no rollback entry inside its detail');
    }
    /* 3b. the fallback, kept on purpose: a card tree that changes with no
     * notify at all is still decorated by the installed watcher. The card is
     * hand-built - a real record id and a detail host, no app.js call - so this
     * is the only path left that proves the watcher is more than installed. It
     * goes into the tree the rebuild left behind, not the captured card's own
     * (the click detached that subtree). */
    const beforeFallback = ext.state();
    const liveCard = doc.querySelectorAll('article.card')[0];
    if (!liveCard || !liveCard.parentNode) throw new Error('no live card tree to append to');
    const extra = doc.createElement('article');
    extra.setAttribute('class', 'card');
    extra.setAttribute('data-id', id);
    const extraDetail = doc.createElement('div');
    extraDetail.setAttribute('class', 'card__detail');
    extra.appendChild(extraDetail);
    liveCard.parentNode.appendChild(extra);
    await new Promise((resolve) => setTimeout(resolve, 200));
    const stateAfterWatch = ext.state();
    const extraRows = extraDetail.querySelectorAll('.cards__rollback').length;
    const rendersAfterFallback = ui.probe().stats.renders;
    extra.remove();
    if (extraRows !== 1) {
      throw new Error('the card-tree watcher decorated no row on a card that arrived with no notify: rows='
        + extraRows);
    }
    if (stateAfterWatch.decorated <= beforeFallback.decorated) {
      throw new Error('the card-tree watcher ran no pass: decorated=' + stateAfterWatch.decorated);
    }
    if (stateAfterWatch.renders !== beforeFallback.renders) {
      throw new Error('the fallback card reached the shell through a notify, not through the watcher: renders '
        + beforeFallback.renders + ' -> ' + stateAfterWatch.renders);
    }
    if (rendersAfterFallback !== rendersBefore) {
      throw new Error('the fallback pass rode on a repaint: renders ' + rendersBefore
        + ' -> ' + rendersAfterFallback);
    }
    return 'first paint (real renderCard): ' + paint.cards + ' cards, ' + paint.details + ' details, '
      + paint.rollbackRows + ' rollback rows (layer not parsed yet); after the shell boots: '
      + afterBoot.rows + ' rows + ' + afterBoot.buttons + ' entries = the gate (' + gate + '), '
      + afterBoot.marks + ' decorated nodes, passes=' + stateAfterBoot.decorated
      + '; expand click on a settled page = renderZones with its own notify (app repaints ' + rendersBefore
      + ' -> ' + rendersAfterToggle + ', shell notifies ' + settled.renders + ' -> '
      + stateAfterToggle.renders + '): rows restored to ' + rightAfterToggle.rows + ' + '
      + rightAfterToggle.buttons + ' entries in the same click, marks ' + settledMarks + ' -> '
      + rightAfterToggle.marks + ' (idempotent); settled ' + restored.rows + ' rows / ' + restored.marks
      + ' marks with app repaints unchanged (' + rendersBefore + ' -> ' + rendersAfterRestore
      + '); a hand-built card with no notify gets its row from the card-tree watcher, passes '
      + beforeFallback.decorated + ' -> ' + stateAfterWatch.decorated;
  });

  /* R2-3 (REQ-002 B2-2 / B2-1): the confidence ring. Three checks:
   * 94 the two band tables are the same numbers and the colours are tokens;
   * 95 every card wears a ring, the band is exact on all four boundaries, and
   *    "no source to judge from" stays neutral with no number at all;
   * 96 the hover carries value + source + the few-sample hint, the honest
   *    "default" wording, and the pass writes its own milliseconds (B2-2).
   * The records come from the shipped demo set with the confidence fields
   * overwritten, so the real renderCard path paints every card under test. */
  const CONF_CASES = [
    ['act-demo-0001', 0.95, 'logprobs', 40, 'green'],
    ['act-demo-0002', 0.9, 'template', 12, 'green'],
    ['act-demo-0003', 0.89, 'hybrid', 9, 'light_green'],
    ['act-demo-0004', 0.7, 'tool_success_rate', 7, 'light_green'],
    ['act-demo-0005', 0.69, 'template', 6, 'yellow'],
    ['act-demo-0006', 0.5, 'default', 5, 'yellow'],
    ['act-demo-0007', 0.4999, 'logprobs', 3, 'red'],
    ['act-demo-0008', 0.42, 'logprobs', 3, 'red'],
    ['act-demo-0009', null, 'template', 20, 'neutral'],
    ['act-demo-0010', 1.7, 'hybrid', 8, 'neutral'],
    ['act-demo-0011', '0.8', 'template', 8, 'neutral'],
    ['act-demo-0012', 0.95, null, null, 'green']
  ];

  function confLayer() {
    const layer = bootExtLayer({ search: '?view=desktop' });
    const records = JSON.parse(JSON.stringify(loadDemoPayload().records || []));
    CONF_CASES.forEach(([id, value, method, samples, band], index) => {
      const rec = records.find((r) => String(r.id) === id) || records[index % records.length];
      rec.confidence = value;
      if (method === null) delete rec.confidence_source;
      else rec.confidence_source = { method: method, samples: samples };
    });
    layer.ui.setApiTransport((endpoint) => {
      if (endpoint === 'health') {
        return { status: 200, data: { name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1' } };
      }
      if (endpoint !== 'records') return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
      return { status: 200, data: { records: records, meta: { count: records.length, synthetic: true } } };
    });
    return layer;
  }

  async function confPainted() {
    const layer = confLayer();
    await layer.ui.probeApi();
    const synced = await layer.ui.syncRecordsFromApi();
    if (!synced) throw new Error('the fixture records never reached the page');
    layer.ext.boot();
    await new Promise((resolve) => setTimeout(resolve, 60));
    const rings = [];
    CONF_CASES.forEach(([id, value, method, samples, band]) => {
      const card = cardById(layer.document.body, id);
      if (!card) throw new Error('no card for the fixture id ' + id);
      const toggle = card.querySelectorAll('button.card__toggle')[0];
      if (!toggle) throw new Error('the fixture card has no card head: ' + id);
      const ring = card.querySelectorAll('.cards__conf')[0];
      if (!ring) throw new Error('the card wears no confidence ring: ' + id);
      rings.push({ id: id, card: card, toggle: toggle, ring: ring, band: band, value: value, method: method });
    });
    return { layer: layer, rings: rings };
  }

  check('R2-3 confidence bands: one contract with confidence.py, colours are tokens only', () => {
    const py = read(path.join(HERE, '..', 'confidence.py'));
    const js = read(FILES.extCards);
    const css = read(path.join(HERE, 'ext', 'cards.css'));
    const styles = read(FILES.styles);
    const pyBlock = /THRESHOLDS:\s*dict\[str,\s*float\]\s*=\s*\{([^}]*)\}/.exec(py);
    const jsBlock = /var CONF_THRESHOLDS = \{([^}]*)\}/.exec(js);
    if (!pyBlock) throw new Error('confidence.py no longer exposes a readable THRESHOLDS table');
    if (!jsBlock) throw new Error('ext/cards.js carries no CONF_THRESHOLDS table (the JS side of the contract)');
    const pick = (text, name) => {
      const m = new RegExp('["\']?' + name + '["\']?\\s*:\\s*([0-9.]+)').exec(text);
      if (!m) throw new Error('no ' + name + ' threshold in ' + text.trim());
      return Number(m[1]);
    };
    const drift = [];
    ['green', 'light_green', 'yellow', 'red'].forEach((name) => {
      const a = pick(pyBlock[1], name);
      const b = pick(jsBlock[1], name);
      if (a !== b) drift.push(name + ': python=' + a + ' js=' + b);
    });
    if (drift.length) throw new Error('the band tables drifted apart (change both sides): ' + drift.join(', '));
    const pyPrec = /PRECISION\s*=\s*([0-9]+)/.exec(py);
    const jsPrec = /var CONF_PRECISION = ([0-9]+)/.exec(js);
    if (!pyPrec || !jsPrec || pyPrec[1] !== jsPrec[1]) {
      throw new Error('the 4-decimal precision drifted: python=' + (pyPrec && pyPrec[1]) + ' js=' + (jsPrec && jsPrec[1]));
    }
    /* the strategy layer's own band→token table (confidence.COLOR_BY_BAND),
     * mirrored as CSS rules: a colour is a token, never a literal, never here.
     * green -> ok (line ok, background ok-soft), light_green -> the ok_soft
     * step (pale ok-line border on the ok-soft ground), yellow -> progress,
     * red -> danger, unknown -> the neutral grey. */
    const tokenByBand = {
      green: ['--c-ok', '--c-ok-soft'],
      light_green: ['--c-ok-line', '--c-ok-soft'],
      yellow: ['--c-progress', '--c-progress-soft'],
      red: ['--c-danger', '--c-danger-soft']
    };
    Object.keys(tokenByBand).forEach((band) => {
      const rule = new RegExp('\\[data-band="' + band + '"\\]\\s*\\{([^}]*)\\}').exec(css);
      if (!rule) throw new Error('cards.css has no rule for band ' + band);
      tokenByBand[band].forEach((token) => {
        if (rule[1].indexOf('var(' + token + ')') < 0) {
          throw new Error('cards.css band ' + band + ' does not use ' + token);
        }
      });
    });
    if (!/\[data-band="neutral"\][^{]*\{[^}]*border-style: dashed/.test(css)) {
      throw new Error('"no source to judge from" is not a hollow neutral ring in CSS');
    }
    if (/#[0-9a-fA-F]{6}/.test(css)) throw new Error('cards.css carries a literal colour');
    if (!/article\.card\[data-trust-attention="low"\][^{]*\{[^}]*border-left-color: var\(--c-danger\)/.test(css)) {
      throw new Error('the red-band card border is not the orange-red token rule R2-3 asked for');
    }
    if (!/animation: cards-attention-pulse var\(--conf-pulse\)/.test(css)) {
      throw new Error('the low-confidence card has no tokenised pulse animation');
    }
    if (!/@media \(prefers-reduced-motion: reduce\)[^{]*\{[^}]*article\.card\[data-trust-attention="low"\]\s*\{\s*animation: none/.test(css)) {
      throw new Error('the pulse does not honour prefers-reduced-motion in this layer');
    }
    /* the badge family stays in styles.css; the card layer never leaks into it */
    if (!/\.badge--low-confidence\s*\{[^}]*--conf-pulse: 1600ms/.test(styles)) {
      throw new Error('.badge--low-confidence did not gain the tokenised pulse (styles.css badge section)');
    }
    if (!/@keyframes badge-low-confidence-pulse/.test(styles)) {
      throw new Error('the badge pulse has no keyframes in styles.css');
    }
    if (styles.indexOf('cards__') >= 0) throw new Error('the cards__ boundary leaked into styles.css');
    return 'bands green/light_green/yellow/red = '
      + ['green', 'light_green', 'yellow', 'red'].map((n) => n + ' ' + pick(jsBlock[1], n)).join(', ')
      + ' (identical to confidence.py), PRECISION=' + jsPrec[1]
      + '; 4 band→token rules + neutral dashed ring, 0 literal colours; red-band card: --c-danger border + pulse,'
      + ' reduced-motion honoured; styles.css keeps 0 cards__ selectors';
  });

  check('R2-3 ring on every card: four bands exact on 0.9/0.7/0.5, null stays neutral with no number', async () => {
    const painted = await confPainted();
    const zh = readJson(FILES.zh);
    const unknown = zh.cards['confidence.unknown'];
    const bad = [];
    const bands = {};
    painted.rings.forEach((entry) => {
      const ring = entry.ring;
      const band = String(ring.getAttribute('data-band') || '');
      bands[entry.id] = band;
      if (band !== entry.band) bad.push(entry.id + ': band=' + band + ' expected ' + entry.band
        + ' (confidence=' + JSON.stringify(entry.value) + ')');
      const text = String(ring.textContent || '');
      if (entry.band === 'neutral') {
        if (text !== '') bad.push(entry.id + ': a neutral ring shows a number: "' + text + '"');
        if (String(ring.getAttribute('data-confidence') || '') !== '') {
          bad.push(entry.id + ': a neutral ring carries data-confidence');
        }
        const label = String(ring.getAttribute('aria-label') || '') + ' ' + String(ring.getAttribute('title') || '');
        if (label.indexOf(unknown) < 0) bad.push(entry.id + ': the neutral ring says nothing about "no source"');
      } else {
        if (!/^\d+%$/.test(text)) bad.push(entry.id + ': the ring label is not a percentage: "' + text + '"');
        const shown = String(ring.getAttribute('data-confidence') || '');
        if (!/^[0-9.]+$/.test(shown)) bad.push(entry.id + ': data-confidence is not the number: "' + shown + '"');
        if (shown.split('.').length === 2 && shown.split('.')[1].length !== 4) {
          bad.push(entry.id + ': data-confidence is not 4 decimals: ' + shown);
        }
      }
      /* placement: the ring is the first child of the card head, and the head
       * keeps the dot / title / expand label the 4.12 checks pin */
      if (entry.toggle.firstChild !== ring) bad.push(entry.id + ': the ring is not at the left edge of the head');
      if (!entry.toggle.querySelectorAll('.card__dot').length) bad.push(entry.id + ': the status dot is gone');
      if (!entry.toggle.querySelectorAll('.card__l1').length) bad.push(entry.id + ': the title moved out of the head');
      /* attention: exactly the red band, never a neutral one, never 0.5 itself */
      const attention = entry.card.getAttribute('data-trust-attention');
      if (entry.band === 'red' && attention !== 'low') bad.push(entry.id + ': a red-band card has no attention hook');
      if (entry.band !== 'red' && attention !== null) bad.push(entry.id + ': ' + entry.band + ' band asks for attention');
      const ms = Number(ring.getAttribute('data-conf-ms'));
      if (!(ms >= 0) || ms >= 200) bad.push(entry.id + ': data-conf-ms=' + ring.getAttribute('data-conf-ms'));
    });
    if (bad.length) throw new Error(bad.join(' | '));
    const counts = {};
    Object.keys(bands).forEach((id) => { counts[bands[id]] = (counts[bands[id]] || 0) + 1; });
    const worst = Math.max.apply(null, painted.rings.map((e) => Number(e.ring.getAttribute('data-conf-ms'))));
    return '12 rings: ' + Object.keys(counts).sort().map((b) => b + '=' + counts[b]).join(' ')
      + ' (0.9→green, 0.89→light_green, 0.7→light_green, 0.69→yellow, 0.5→yellow, 0.4999→red,'
      + ' null/1.7/"0.8"→neutral with no number); attention only on the 2 red cards; '
      + 'ring is the first child of every head, dot + title intact; max data-conf-ms=' + worst + 'ms (<200)';
  });

  check('R2-3 hover + source: value, zh source copy, few-sample hint, no fake certainty, 200ms budget', async () => {
    const painted = await confPainted();
    const ui = painted.layer.ui;
    const t = (key, params) => String(ui.t(key, params || null));
    const ringOf = (id) => painted.rings.find((e) => e.id === id).ring;
    const tip = (id) => String(ringOf(id).getAttribute('title') || '');
    const openDetail = (id) => detailOf(painted.layer.document.body, id);
    const sourceText = (id) => {
      const detail = openDetail(id);
      const line = detail ? detail.querySelectorAll('.cards__source')[0] : null;
      if (!line) throw new Error(id + ': the detail area has no source line (B2-1 wants it visible)');
      return { line: line, text: String(line.textContent || '') };
    };
    const problems = [];

    /* 0.42, logprobs, 3 samples: number + source + the few-sample warning */
    const rich = tip('act-demo-0008');
    if (rich.indexOf('0.4200') < 0) problems.push('the hover does not carry the 4-decimal value: ' + rich);
    if (rich.indexOf(t('cards.confidence.method.logprobs', null)) < 0) problems.push('no logprobs source copy: ' + rich);
    if (rich.indexOf(t('cards.confidence.few_samples', { samples: 3 })) < 0) problems.push('no few-sample hint: ' + rich);
    if (String(ringOf('act-demo-0008').getAttribute('data-few') || '') !== 'true') problems.push('data-few is not true');

    /* 0.95, 40 samples: no hint, and the sample count is honest data */
    const warm = tip('act-demo-0001');
    if (warm.indexOf('0.9500') < 0) problems.push('the hover lost the value on a green card: ' + warm);
    if (warm.indexOf(t('cards.confidence.few_samples', { samples: 3 })) >= 0) problems.push('the few-sample hint fired at 40 samples');
    if (String(ringOf('act-demo-0001').getAttribute('data-samples') || '') !== '40') problems.push('data-samples lost the 40');

    /* method=default must never read as a guarantee (展示纪律 0.4) */
    const BANNED = ['已确认', '已担保', '保证没问题', '一定正确'];
    const def = tip('act-demo-0006');
    if (def.indexOf(t('cards.confidence.method.default', null)) < 0) problems.push('the default method is not named: ' + def);
    if (BANNED.some((w) => def.indexOf(w) >= 0)) problems.push('the default-method hover claims certainty: ' + def);
    if (String(ringOf('act-demo-0006').getAttribute('data-method') || '') !== 'default') problems.push('data-method lost "default"');

    /* no source object at all: the ring is green from its number, the source
     * line says "none to judge from" instead of inventing a method */
    const bare = sourceText('act-demo-0012');
    if (bare.text.indexOf(t('cards.confidence.source.none', null)) < 0) problems.push('a record without confidence_source fakes a source: ' + bare.text);
    if (String(bare.line.getAttribute('data-method') || '') !== 'unknown') problems.push('an absent source is not marked unknown');

    /* the source line carries the translated copy, not the bare enum */
    const zhLine = sourceText('act-demo-0004');
    if (zhLine.text.indexOf(t('cards.confidence.method.tool_success_rate', null)) < 0) problems.push('the source line shows the raw enum: ' + zhLine.text);
    if (/tool_success_rate/.test(zhLine.text)) problems.push('the source line shows the raw enum: ' + zhLine.text);

    /* B2-2: a pure display pass over the imported records - no network on the
     * ring path, and the whole set stays well under the 200ms reaction budget */
    const requestsBefore = painted.layer.ui.probe().stats;
    const times = painted.rings.map((e) => Number(e.ring.getAttribute('data-conf-ms')));
    const total = times.reduce((a, b) => a + b, 0);
    if (times.some((v) => !(v >= 0))) problems.push('a ring carries no timing mark');
    if (total >= 200) problems.push('the 12-ring pass cost ' + total + 'ms, over the 200ms budget');
    const layer = painted.layer;
    /* the warm pass: one full repaint of the whole set, timed synchronously
     * (the notify path decorates inside it, no network on the ring path) */
    const hotStart = Date.now();
    layer.ui.renderAll();
    const hotMs = Date.now() - hotStart;
    await new Promise((resolve) => setTimeout(resolve, 60));
    if (hotMs >= 200) problems.push('a warm repaint of the ring set took ' + hotMs + 'ms, over 200ms');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'hover: "0.4200" + logprobs copy + few-sample hint (3<5) | default method named honestly, 0 banned words | '
      + 'source line: tool_success_rate rendered as zh copy, an absent source says "none to judge from" | '
      + '12 rings cost ' + total.toFixed(3) + 'ms in the decoration pass (max ' + Math.max.apply(null, times).toFixed(3)
      + 'ms), warm repaint ' + hotMs + 'ms, 0 requests on the ring path (renders=' + requestsBefore.renders + ')';
  });

  /* ---------------------------------------------------------------- *
   * R5-4 (REQ-005): the collaboration panel + the invitee's landing page
   * ---------------------------------------------------------------- */

  /* a literal colour is only allowed as a var() fallback: measure it by
   * checking that a `var(` is open on the same line before every #hex.
   * Comments are stripped first: a comment may *name* a rule (this file
   * documents the boundary), a rule may not use it. */
  const stripComments = (source) => String(source)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '');
  const stripJsComments = (source) => String(source)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
  const strayHex = (css) => {
    const out = [];
    String(css).split('\n').forEach((line, index) => {
      const pattern = /#[0-9a-fA-F]{3,8}\b/g;
      let m;
      while ((m = pattern.exec(line))) {
        const before = line.slice(0, m.index);
        const open = before.lastIndexOf('var(');
        const close = before.lastIndexOf(')');
        if (!(open > close)) out.push('line ' + (index + 1) + ': ' + m[0]);
      }
    });
    return out;
  };

  check('R5-4 collab panel: one tab, six zones, the single request gate, zero CJK', () => {
    const js = extCollabSource;
    const css = extCollabCssSource;
    const problems = [];
    if (!/\.register\(\{/.test(js)) problems.push('panel never registers with the extension shell');
    if (!/id:\s*'collab'/.test(js)) problems.push('panel id is not collab');
    if (!/permission:\s*'records\.read'/.test(js)) problems.push('tab permission is not records.read');
    ['group', 'perms', 'issued', 'shared', 'accept', 'onbehalf'].forEach((zone) => {
      if (js.indexOf("section(host, '" + zone + "', 'sharing." + zone + ".title')") < 0) {
        problems.push('missing zone: ' + zone);
      }
    });
    ['ctx.get(GROUPS)', 'ctx.get(SHARES)', 'ctx.get(LETTERS)', 'ctx.post(GROUPS +', 'ctx.post(SHARES +']
      .forEach((call) => { if (js.indexOf(call) < 0) problems.push('no guarded call: ' + call); });
    if (/[^.\w]fetch\s*\(/.test(js)) problems.push('the panel builds its own fetch instead of the gate');
    if (/\bXMLHttpRequest\b/.test(js)) problems.push('the panel uses XMLHttpRequest');
    if (/localStorage|sessionStorage|indexedDB|document\.cookie/.test(js)) {
      problems.push('the panel persists something: the share secret must stay in memory');
    }
    const targets = [...js.matchAll(/'(api\/[a-z_/]+)'/g)].map((m) => m[1]);
    const allowed = ['api/groups', 'api/shares', 'api/secretariat/letters'];
    const outside = targets.filter((x) => !allowed.some((a) => x === a || x.indexOf(a + '/') === 0));
    if (outside.length) problems.push('request target outside the allowlist: ' + outside.join(','));
    if (!/data-tab="collab"/.test(css)) problems.push('CSS is not scoped to #trust-body[data-tab="collab"]');
    if (stripComments(css).indexOf('cards__') >= 0) problems.push('collab.css reaches into a cards__ selector');
    if (/collab/.test(styleSource)) problems.push('styles.css gained collab rules (the panel owns its own CSS)');
    const hex = strayHex(stripComments(css));
    if (hex.length) problems.push('literal colour outside a var() fallback: ' + hex.join(', '));
    if (cjkCount(js) !== 0 || cjkCount(css) !== 0) problems.push('CJK literals in the panel source');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'panel ' + js.length + 'B / css ' + css.length + 'B, zones=6, targets=' +
      targets.length + ' on 3 allowlisted bases, literal colours outside var()=0, CJK=0';
  });

  check('R7-2 suggestions panel: write-side verbs, both permission gates, the three-button toast stays non-modal', () => {
    const js = stripJsComments(extInsightsSource);
    const problems = [];
    if (!/id:\s*'suggestions'/.test(js)) problems.push('panel id is not suggestions');
    if (!/permission:\s*'records\.read'/.test(js)) problems.push('tab permission is not records.read');
    /* the write side: T29 /accept, T30 /dismiss, T31 POST /api/preferences,
     * plus the service-level switch on POST /api/suggestions (set_enabled entry) */
    if (!/ctx\.post\('api\/suggestions\/' \+ enc\(id\) \+ '\/' \+ verb/.test(js)) {
      problems.push('the panel does not POST api/suggestions/<id>/<verb>');
    }
    if (!/decideSuggestion\(ctx, id, 'accept'/.test(js) || !/decideSuggestion\(ctx, id, 'dismiss'/.test(js)) {
      problems.push('only accept and dismiss may be posted (no invented verb)');
    }
    if (/ctx\.post\('api\/suggestions\/' \+ enc\(id\) \+ '\/' \+ (?!verb)/.test(js)) {
      problems.push('a hardcoded sub-verb appeared next to the accept/dismiss pair');
    }
    if (!/ctx\.post\('api\/preferences', \{ tool: tool, param: param, value: value/.test(js)) {
      problems.push('the preference form does not POST api/preferences (T31 write side)');
    }
    if (!/ctx\.post\('api\/suggestions', \{ enabled: next \}\)/.test(js)) {
      problems.push('the switch does not POST api/suggestions {"enabled": bool}');
    }
    if (!/ctx\.post\(/.test(js)) problems.push('the panel never posts');
    if (/[^.\w]fetch\s*\(/.test(js)) problems.push('the panel builds its own fetch instead of the gate');
    if (/\bXMLHttpRequest\b/.test(js)) problems.push('the panel uses XMLHttpRequest');
    /* the two permission gates are separate: deciding needs intervene.decide,
     * the service-level switch needs settings.write (a read-only role must not
     * be able to turn suggestions off for everybody) */
    if (!/ctx\.can\('intervene\.decide'\)/.test(js)) problems.push('deciding is not gated on intervene.decide');
    if (!/ctx\.can\('settings\.write'\)/.test(js)) problems.push('the switch is not gated on settings.write');
    if (!/payload\.enabled/.test(js) || !/payload\.durable/.test(js)) {
      problems.push('the switch state is not read back from the server payload (enabled / durable)');
    }
    if (!/suggestions\.feature\.denied/.test(js) || !/suggestions\.feature\.failed/.test(js)) {
      problems.push('a refused switch is not reported to the user');
    }
    /* the three-button toast: accept / dismiss / later, and 稍后 snoozes that one
     * suggestion for the page session */
    ['suggestions.action.accept', 'suggestions.action.dismiss', 'suggestions.action.later']
      .forEach((key) => { if (js.indexOf("'" + key + "'") < 0) problems.push('toast lacks the action key ' + key); });
    if (!/sug\.snoozed\[id\] = true/.test(js)) problems.push('[稍后] does not snooze that suggestion id');
    if (!/sug\.snoozed\[id\]/.test(js) || !/sug\.shown\[id\]/.test(js)) {
      problems.push('the poll does not dedup shown / snoozed ids');
    }
    if (!/suggestions\.toast\.failed/.test(js)) problems.push('a failed write is not reported in the toast');
    /* non-blocking: no modal, no backdrop, no confirm dialog */
    if (!/aria-modal': 'false'/.test(js)) problems.push('the decision toast does not declare aria-modal=false');
    if (/trust-modal|confirmDialog|__backdrop/.test(js)) problems.push('the toast path reaches a modal / backdrop');
    if (!/setTimeout\(closeDecisionToast, TOAST_MS\)/.test(js)) {
      problems.push('the toast does not auto-dismiss on its own timer (it must never hold the page)');
    }
    const targets = [...js.matchAll(/'(api\/[a-z_/]+)'/g)].map((m) => m[1]);
    /* R6-4 added `api/analytics/export`: the two export buttons read the
     * honest header family, so the artifact must come back through this same
     * door (CSRF header, timeout, error envelope, request-id). The server
     * still ships exactly the two analytics routes. */
    const allowed = ['api/analytics/dashboard', 'api/analytics/export',
                     'api/preferences', 'api/suggestions'];
    const outside = targets.filter((x) => !allowed.some((a) => x === a || x.indexOf(a + '/') === 0));
    if (outside.length) problems.push('request target outside the allowlist: ' + outside.join(','));
    if (!/ext\/insights\.js/.test(indexSource)) problems.push('index.html does not mount ext/insights.js');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'tabs=2 (analytics 40 / suggestions 50), posts on 3 allowlisted bases (accept/dismiss + switch + preference), '
      + 'gates=intervene.decide+settings.write, read-back=enabled/durable, toast actions=accept/dismiss/later, '
      + 'modal=0, auto-dismiss=' + (js.indexOf('TOAST_MS') >= 0 ? 'own timer' : 'missing')
      + ', read bases=analytics/dashboard+analytics/export';
  });

  check('R6-4 analytics data plane: the trio drives the label, the slow note is measured, the ≤3s promise is conditional', () => {
    const js = stripJsComments(extInsightsSource);
    const css = stripComments(extInsightsCssSource);
    const problems = [];
    /* the panel paints the strip, and paints it above the cards */
    if (!/function paintDataPlane\(ctx, host, data\)/.test(js)) problems.push('no data-plane strip');
    if (js.indexOf('paintDataPlane(ctx, host, data);') > js.indexOf('paintMetrics(ctx, host, data);')) {
      problems.push('the data-plane strip is not painted above the metric cards');
    }
    /* it reads the trio the server sends: nothing is inferred from a clock */
    if (!/hasOwnProperty\.call\(source, 'pre_aggregated'\)/.test(js)) problems.push('pre_aggregated is not read as a real field');
    if (!/\['scanned', 'meta\.scanned'\]/.test(js)) problems.push('scanned is not read');
    if (!/pick\(agg, \['reason'\]\)/.test(js)) problems.push('agg.reason is not read');
    /* the honest degradation label: a streaming read always says so, and the
     * measured number (not a guess) decides the >1s half */
    if (!/var SLOW_MS = 1000;/.test(js)) problems.push('the one-second threshold is not pinned');
    if (!/noteRow\(ctx, box, 'analytics\.plane\.slow', \{ ms: ana\.elapsedMs \}, 'ext-note--warn'\)/.test(js)) {
      problems.push('the 未预聚合，加载较慢 note is not painted with the measured ms');
    }
    if (!/ana\.elapsedMs = ana\.startedAt && now >= ana\.startedAt \? now - ana\.startedAt : 0;/.test(js)) {
      problems.push('the load time is not measured around the one dashboard read');
    }
    /* the ≤3s register: the promise may only appear on a pre-aggregated read */
    if (!/trio\.pre === true \? 'analytics\.plane\.timing\.pre'/.test(js)) problems.push('the ≤3s claim is not gated on pre_aggregated=true');
    if (/analytics\.plane\.timing\.pre'/.test(js) && js.indexOf("'analytics.plane.timing.pre'") < 0) problems.push('the ≤3s key is not a literal');
    /* every documented fallback code gets its own sentence; an unknown code is
     * shown as a code, never translated into a guess */
    ['no_ledger_wired', 'agent_filter', 'custom_salt', 'input_not_rerunnable',
     'ledger_missing_no_records', 'ledger_unreadable', 'chain_tampered'].forEach((code) => {
      if (js.indexOf(code + ": 'analytics.plane.reason." + code + "'") < 0) {
        problems.push('no dictionary key wired for the fallback code ' + code);
      }
    });
    if (!/noteRow\(ctx, box, 'analytics\.plane\.reason', \{ code: trio\.reason/.test(js)) {
      problems.push('an unknown fallback code is not shown as data');
    }
    /* the durable=false register (the ledger did not take the write) */
    if (!/analytics\.plane\.not_durable/.test(js)) problems.push('a ledger write failure is not reported');
    /* §2.5 honesty: no push channel exists, so the page says the refresh is a
     * query, and the source proves it builds none */
    if (!/noteRow\(ctx, box, 'analytics\.plane\.refresh'\)/.test(js)) problems.push('the refresh note does not say 刷新＝查询');
    if (/\bEventSource\b|\bWebSocket\b/.test(js)) problems.push('the panel invented a push channel');
    /* every key the strip renders exists in both dictionaries */
    const zhDomain = (zh['analytics'] || {});
    const enDomain = (en['analytics'] || {});
    const referenced = [...new Set([...js.matchAll(/'(analytics\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]))]
      .filter((key) => key.indexOf('.') > 0 && !/\.$/.test(key));
    if (referenced.length < 80) problems.push('only ' + referenced.length + ' analytics keys referenced');
    /* the two button labels are built as 'analytics.export.' + format, so the
     * literal is only a prefix: both served formats must exist in the dictionary */
    ['csv', 'pdf'].forEach((format) => {
      if (typeof zhDomain['export.' + format] !== 'string') problems.push('zh key missing: analytics.export.' + format);
      if (typeof enDomain['export.' + format] !== 'string') problems.push('en key missing: analytics.export.' + format);
    });
    referenced.forEach((key) => {
      const leaf = key.slice('analytics.'.length);
      if (typeof zhDomain[leaf] !== 'string' || !zhDomain[leaf]) problems.push('zh key missing: ' + key);
      if (typeof enDomain[leaf] !== 'string' || !enDomain[leaf]) problems.push('en key missing: ' + key);
    });
    const zhLeaves = Object.keys(zhDomain);
    const enLeaves = Object.keys(enDomain);
    if (zhLeaves.length !== enLeaves.length) problems.push('analytics domain is not symmetric: zh '
      + zhLeaves.length + ' / en ' + enLeaves.length);
    /* the strip owns scoped styles and no palette of its own */
    if (!/data-tab="analytics"\] \.ext-ana__plane/.test(css)) problems.push('the data-plane strip has no scoped style');
    if (css.indexOf('cards__') >= 0) problems.push('insights.css reaches into a cards__ selector');
    if (cjkCount(css) !== 0) problems.push('CJK literals in insights.css');
    const hex = strayHex(css);
    if (hex.length) problems.push('literal colour outside a var() fallback: ' + hex.join(', '));
    if (problems.length) throw new Error(problems.join(' | '));
    return 'strip reads pre_aggregated/scanned/agg.reason, slow label=measured ms (threshold 1000), '
      + '≤3s copy gated on pre_aggregated=true, fallback codes wired=7, push channel=0, '
      + 'analytics keys referenced=' + referenced.length + ' (zh/en ' + zhLeaves.length + '/'
      + enLeaves.length + ' symmetric), css scoped to data-tab="analytics"';
  });

  check('R6-4 export face: two buttons, names from the served headers, a degradation is never a success, sha = fingerprint', () => {
    const js = stripJsComments(extInsightsSource);
    const problems = [];
    /* the frozen format enum is csv|pdf: two buttons, and `format=html` is a
     * server-side 400, so no HTML option may appear here */
    if (!/var EXPORT_FORMATS = \['csv', 'pdf'\];/.test(js)) problems.push('the format enum is not the frozen csv|pdf');
    if (/analytics\.export\.html/.test(js)) problems.push('an HTML export option was invented (format=html is 400)');
    if (/['"]csv['"],\s*['"]pdf['"],\s*['"]html['"]/.test(js)) problems.push('html joined the button list');
    /* the artifact comes back through the single door, as text, with an Accept
     * that covers the served formats; the panel never builds a fetch */
    if (!/ctx\.call\('api\/analytics\/export', null, \{/.test(js)) problems.push('the export does not go through the ctx door');
    if (!/method: 'GET', query: query, response: 'text', accept: EXPORT_ACCEPT/.test(js)) problems.push('the export request is not the honest text read');
    if (/[^.\w]fetch\s*\(/.test(js)) problems.push('the panel builds its own fetch');
    /* the naming follows the bytes actually served, not the button pressed */
    ['x-tracelens-export-format', 'x-tracelens-export-fallback', 'x-tracelens-export-sha256',
     'content-disposition', 'x-tracelens-export-note', 'x-tracelens-export-pre-aggregated'].forEach((name) => {
      if (js.indexOf("'" + name + "'") < 0) problems.push('header not read: ' + name);
    });
    if (!/filenameFromDisposition\(ctx\.header\(head, 'content-disposition'\)\)/.test(js)) {
      problems.push('the filename is not taken from the server (it is not invented)');
    }
    /* a degradation is a degradation: the reason code sits next to the words,
     * the copy owns the "this is not broken, and it is not a success" register,
     * and the toast has its own fallback key */
    if (!/noteRow\(ctx, box, 'analytics\.export\.fallback',/.test(js)) problems.push('the fallback row is missing');
    if (!/reason: record\.fallback \}, 'ext-note--warn'\)/.test(js)) problems.push('the fallback reason is not shown as data next to the warning style');
    if (!/ctx\.toast\('analytics\.export\.toast_fallback'/.test(js)) problems.push('a degraded export does not get the fallback toast');
    if (js.indexOf("'analytics.export.toast'") < 0) problems.push('the plain toast key disappeared');
    /* sha is presented as the fingerprint of that file, and its absence is a
     * stated missing header, not silence */
    if (!/noteRow\(ctx, box, 'analytics\.export\.sha', \{ sha: record\.sha \}\)/.test(js)) problems.push('the fingerprint row is missing');
    if (!/noteRow\(ctx, box, 'analytics\.export\.sha_missing', null, 'ext-note--warn'\)/.test(js)) problems.push('a missing fingerprint header is not reported');
    /* bytes honesty: CSV keeps the served UTF-8 BOM so the file and the sha
     * agree; a real PDF is handed to the browser instead of being decoded */
    if (!/record\.served === 'csv' \? '\\ufeff' \+ record\.text : record\.text/.test(js)) problems.push('the served CSV BOM is not restored');
    if (!/record\.served === 'pdf'\) record\.saved = ctx\.download\(exportUrl\(format\), record\.filename\)/.test(js)) {
      problems.push('a served PDF is not handed to the browser (the text channel cannot carry it)');
    }
    if (!/if \(!record\.saved\) noteRow\(ctx, box, 'analytics\.export\.not_saved'/.test(js)) problems.push('a file the panel could not ship is not reported');
    /* printable HTML: the Ctrl+P path exists and says who opens it (PM-VERDICT-R6-3 §6 第 5 条) */
    if (!/noteRow\(ctx, box, 'analytics\.export\.print'\)/.test(js)) problems.push('the printable-HTML hint is missing');
    if (!/global\.open\(url, '_blank', 'noopener'\)/.test(js)) problems.push('the printable view is not opened from the server bytes');
    if (!/'analytics\.export\.print_unavailable'/.test(js)) problems.push('a blocked popup is not admitted');
    /* refusals stay visible: 403 is the permission story, 400 shows the code and
     * the server's own sentence, offline ships nothing */
    if (!/record\.status === 403/.test(js) || !/'analytics\.export\.denied'/.test(js)) problems.push('a 403 is not shown as a permission denial');
    if (!/noteRow\(ctx, box, 'analytics\.export\.failed',/.test(js)) problems.push('a 400 is not shown with its code');
    if (!/if \(offlineNow\(\)\)[\s\S]{0,120}'analytics\.export\.offline'/.test(js)) problems.push('offline is not stated before any export request');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'buttons=2 (csv|pdf, no html option), headers read=6, filename=server disposition, '
      + 'fallback row+toast=warn not success, sha row + sha_missing row, csv BOM restored, '
      + 'pdf handed to browser, printable Ctrl+P path, 403/400/offline all stated';
  });

  check('R7-2 suggestions copy + CSS: every key exists in zh and en, scoped styles, zero literal colours', () => {
    const js = stripJsComments(extInsightsSource);
    const css = stripComments(extInsightsCssSource);
    const problems = [];
    const zhDomain = (zh['suggestions'] || {});
    const enDomain = (en['suggestions'] || {});
    const referenced = [...new Set([...js.matchAll(/'(suggestions\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]))];
    if (referenced.length < 45) problems.push('only ' + referenced.length + ' suggestions keys referenced');
    referenced.forEach((key) => {
      const leaf = key.slice('suggestions.'.length);
      if (typeof zhDomain[leaf] !== 'string' || !zhDomain[leaf]) problems.push('zh key missing: ' + key);
      if (typeof enDomain[leaf] !== 'string' || !enDomain[leaf]) problems.push('en key missing: ' + key);
    });
    const zhLeaves = Object.keys(zhDomain);
    const enLeaves = Object.keys(enDomain);
    if (zhLeaves.length !== enLeaves.length) problems.push('suggestions domain is not symmetric: zh '
      + zhLeaves.length + ' / en ' + enLeaves.length);
    zhLeaves.forEach((leaf) => {
      if (enLeaves.indexOf(leaf) < 0) problems.push('en key missing: suggestions.' + leaf);
    });
    /* honesty register: the delete / clear endpoint does not exist server-side,
     * so the panel must say 未提供 and must not fabricate a delete call */
    if (referenced.indexOf('suggestions.pref.unsupported') < 0) {
      problems.push('the preference list does not state that delete/clear is not provided');
    }
    if (/api\/preferences\/[^']*(delete|clear)/.test(js)) problems.push('the panel invents a delete/clear endpoint');
    /* styles: own file, scoped to the panel, palette from tokens only */
    if (!/data-tab="suggestions"/.test(css)) problems.push('CSS is not scoped to #trust-body[data-tab="suggestions"]');
    if (css.indexOf('cards__') >= 0) problems.push('insights.css reaches into a cards__ selector');
    const hex = strayHex(css);
    if (hex.length) problems.push('literal colour outside a var() fallback: ' + hex.join(', '));
    if (/ext-sug/.test(styleSource)) problems.push('styles.css gained suggestion rules (the panel owns its own CSS)');
    if (cjkCount(css) !== 0) problems.push('CJK literals in insights.css');
    if (!/ext\/insights\.css/.test(indexSource)) problems.push('index.html does not mount ext/insights.css');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'keys referenced=' + referenced.length + ' (zh/en both present, domain '
      + zhLeaves.length + '/' + enLeaves.length + ' symmetric), delete/clear=未提供, '
      + 'css ' + extInsightsCssSource.length + 'B scoped to data-tab="suggestions", '
      + 'literal colours outside var()=0, styles.css ext-sug rules=0, CJK=0';
  });

  check('R5-4 one-time secret: the invite link is rendered once, warned, wiped, never re-echoed', () => {
    const js = extCollabSource;
    const share = shareHtmlSource;
    const server = read(path.join(PKG, 'agent_explainer', 'server.py'));
    const problems = [];
    /* 0.4 display discipline in the panel */
    const warnings = (js.match(/sharing\.share\.result\.warning/g) || []).length;
    if (warnings < 2) problems.push('the "only shown once" warning is not on both the form and the result');
    if (!/dataset:\s*\{\s*onetime:\s*'true'\s*\}/.test(js)) problems.push('the one-time view is not marked');
    if (!/querySelectorAll\('\[data-onetime\]'\)/.test(js)) problems.push('closing does not wipe the secret nodes');
    if (js.indexOf('C.clear(result)') < 0 || js.indexOf('result.hidden = true') < 0) {
      problems.push('the one-time view is not cleared and hidden on close');
    }
    const echoes = (js.match(/data\.invite_url/g) || []).length;
    if (echoes !== 1) problems.push('invite_url is read ' + echoes + ' times in the panel (must be exactly the one-time view)');
    const columns = [...js.matchAll(/table\(box, \[([\s\S]*?)\]\)/g)].map((m) => m[1]);
    if (columns.length !== 2) problems.push('expected 2 share tables, found ' + columns.length);
    columns.forEach((cols) => {
      if (/token|invite_url|link/i.test(cols)) problems.push('a list column renders a secret: ' + cols.replace(/\s+/g, ' '));
    });
    /* the server side is what makes "never re-echo" structural */
    const fields = (server.match(/SHARE_ROW_FIELDS\s*=\s*\(([^)]*)\)/s) || ['', ''])[1];
    const names = (fields.match(/"([a-z_]+)"/g) || []).map((s) => s.replace(/"/g, ''));
    if (names.length !== 8) problems.push('SHARE_ROW_FIELDS is ' + names.length + ' fields, not 8');
    if (names.some((n) => /token|salt|hash|url/i.test(n))) problems.push('the row whitelist leaks a secret field: ' + names.join(','));
    /* the landing page: hash-only, no storage, no echo, one real request.
     * Measured on the executable part only: the page's own comment and its
     * copy deck *name* localStorage / location.hash to explain the rule, and a
     * rule that measures its own documentation proves nothing. */
    const shareCode = stripJsComments(stripComments(share)
      .replace(/<script id="share-i18n">[\s\S]*?<\/script>/g, ''));
    if (/\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|document\.cookie/.test(shareCode)) {
      problems.push('share.html touches browser storage');
    }
    const hashReads = (shareCode.match(/location\.hash/g) || []).length;
    if (hashReads !== 1) problems.push('location.hash is read ' + hashReads + ' times (must be exactly once)');
    if (!/TOKEN_FRAGMENT_KEY\s*=\s*'token'/.test(shareCode)) problems.push('the fragment key is not pinned to token');
    if (/(textContent|innerText|innerHTML)\s*=\s*[^;\n]*\btoken\b/.test(shareCode)) {
      problems.push('share.html prints the token into the DOM');
    }
    if (!/POST', \{ token: token \}/.test(shareCode)) problems.push('the accept request does not carry the token in the JSON body');
    if (/\?[^'"]*token/.test(shareCode)) problems.push('the token is put in a ?query somewhere');
    if (!/token = '';/.test(shareCode)) problems.push('the in-page token is not dropped after a successful accept');
    if (shareCode.indexOf('/api/shares/accept') < 0) problems.push('the accept endpoint is not the one the panel documents');
    if (/(https?:)?\/\/[a-z0-9.-]+/i.test(shareCode)) {
      problems.push('share.html names an external host or asset');
    }
    if (/<script[^>]+\bsrc=|<link[^>]+\bhref=|@import/.test(shareCode)) problems.push('share.html loads an external asset');
    if (!/ext\/collab\.js/.test(indexSource) || !/ext\/collab\.css/.test(indexSource)) {
      problems.push('index.html does not mount the collab panel');
    }
    if (problems.length) throw new Error(problems.join(' | '));
    return 'warning copy x' + warnings + ', one-time view wiped on close, list columns=2 (0 secrets), '
      + 'server row whitelist=' + names.length + ' fields (no token/salt/hash/url), '
      + 'share.html: 1 hash read / 0 storage APIs / 0 printed token / 0 external assets';
  });

  check('R5-4 visibility gate (B5-2): view hides intervene + rollback, leader path stays open', () => {
    const js = extCollabSource;
    const problems = [];
    ['intervene-button', 'intervene-submit', 'intervene-reject-confirm', 'intervene-reason-box']
      .forEach((hook) => {
        if (js.indexOf("'" + hook + "'") < 0) problems.push('gate does not cover the ' + hook + ' control');
      });
    if (js.indexOf("hide(pick(card, '.cards__rollback'))") < 0) problems.push('rollback affordance is not hidden for a view collaborator');
    if (!/var viewOnly = level === 'view';/.test(js)) problems.push('the hide rule is not keyed on the view level');
    const shareHidden = (js.match(/level === 'view' \|\| level === 'approve'/g) || []).length;
    if (shareHidden < 2) problems.push('the 再分享 control is not suppressed below manage (found ' + shareHidden + ' guards)');
    ['sharing.gate.hide.intervene', 'sharing.gate.hide.share', 'sharing.gate.denied',
     'sharing.gate.leader', 'sharing.gate.basis', 'sharing.gate.level', 'sharing.gate.title']
      .forEach((key) => { if (js.indexOf(key) < 0) problems.push('gate copy key missing: ' + key); });
    if (js.indexOf('shared_with_me') < 0 || js.indexOf("status !== 'accepted'") < 0) {
      problems.push('the derived level is not read from the accepted shared_with_me rows');
    }
    if (js.indexOf('markDenied(groupId, res.code)') < 0 || !/deniedFor\(/.test(js)) {
      problems.push('a 403 does not degrade the controls for this render');
    }
    const perms = (js.match(/'\/permissions'/g) || []).length;
    if (perms !== 1) problems.push('the permission probe is called ' + perms + ' times (one display read only)');
    /* T25 is a display, not the gate: `can_view/can_approve/can_manage` are all
     * false for the un-invited group leader, so using them to hide controls
     * would degrade the leader. Measure it by position: the T25 fields may only
     * appear in the panel's permission display, never in the derivation path. */
    const permsStart = js.indexOf('function renderPerms');
    if (permsStart < 0) problems.push('no permission display function');
    const t25 = /can_(view|approve|manage)/g;
    const outside = [];
    let found;
    while ((found = t25.exec(js))) {
      if (permsStart >= 0 && found.index < permsStart) outside.push(found[0] + '@' + found.index);
    }
    if (outside.length) problems.push('T25 fields used outside the display (would degrade the leader): ' + outside.join(', '));
    if (/collab|gate\.hide/.test(appSource)) problems.push('app.js was taught the collaboration gate (it must stay server-authoritative)');
    if (appSource.indexOf('.options') < 0) problems.push('the intervene affordance app.js owns has moved');
    if (problems.length) throw new Error(problems.join(' | '));
    return '4 intervene hooks + rollback hidden on view, 再分享 suppressed below manage (' + shareHidden +
      ' guards), 403 degrades + is reported, T25 read once for display only, app.js untouched by the gate';
  });

  check('R5-4 share landing page: standalone, hash-only token, honest states, zh/en inline copy', () => {
    const share = shareHtmlSource;
    const problems = [];
    const block = share.match(/<script id="share-i18n">([\s\S]*?)<\/script>/);
    if (!block) throw new Error('the landing page has no inline copy deck');
    const box = { window: {} };
    vm.runInNewContext(block[1], box);
    const dicts = box.window.__SHARE_I18N;
    if (!dicts || !dicts.zh || !dicts.en) throw new Error('the landing copy deck is not exposed as window.__SHARE_I18N');
    const zhKeys = Object.keys(dicts.zh).sort();
    const enKeys = Object.keys(dicts.en).sort();
    if (zhKeys.length < 20) problems.push('the landing deck is thin: ' + zhKeys.length + ' keys');
    if (zhKeys.join('|') !== enKeys.join('|')) {
      const only = (a, b) => a.filter((k) => b.indexOf(k) < 0);
      problems.push('zh/en landing key sets differ: zh-only ' + only(zhKeys, enKeys).join(',') +
        ' / en-only ' + only(enKeys, zhKeys).join(','));
    }
    const cjkEn = zhKeys.filter((k) => cjkCount(dicts.en[k]) > 0);
    if (cjkEn.length) problems.push('CJK in the en landing deck: ' + cjkEn.join(','));
    /* the zh deck must really be Chinese, and the en deck really Latin. The one
     * documented exception is the language-switch label: it names the *other*
     * language, in that language's own script-free form ("English" / "Chinese"),
     * which is how app.js's own switch stays CJK-free in the en UI (check 86). */
    const LATIN_ONLY = new Set(['lang.switch']);
    const noZh = zhKeys.filter((k) => cjkCount(dicts.zh[k]) === 0 && !LATIN_ONLY.has(k));
    if (noZh.length) problems.push('zh landing copy is not Chinese: ' + noZh.join(','));
    ['state.expired', 'state.revoked', 'state.invalid', 'state.used', 'state.accepted',
     'state.unmounted', 'state.auth', 'state.no_token', 'hint.nostore'].forEach((key) => {
      if (zhKeys.indexOf(key) < 0) problems.push('missing landing state: ' + key);
    });
    ['SHARE_EXPIRED', 'SHARE_TOKEN_INVALID', 'SHARE_TOKEN_USED', 'SHARE_FORBIDDEN',
     'AUTH_REQUIRED', 'ROUTE_NOT_FOUND'].forEach((code) => {
      if (share.indexOf("'" + code + "'") < 0) problems.push('no honest state for the server code ' + code);
    });
    if (!/lang="zh"/.test(share) || !/data-hook="lang-switch"/.test(share)) {
      problems.push('the page has no zh default + language switch');
    }
    if (!/showFacts\(permission, group\)/.test(share)) problems.push('a successful accept does not show permission + group');
    if (!/history\.replaceState/.test(share)) problems.push('the fragment is not stripped from the address bar');
    if (!/credentials:\s*'same-origin'/.test(share)) problems.push('the accept request drops the session cookie');
    if (!/cache:\s*'no-store'/.test(share)) problems.push('the accept request is cacheable');
    if (cjkCount(share.replace(/<script id="share-i18n">[\s\S]*?<\/script>/, '')) === 0) {
      problems.push('the page has no inline zh chrome (the deck must be self-contained)');
    }
    if (problems.length) throw new Error(problems.join(' | '));
    return 'inline deck ' + zhKeys.length + ' keys zh/en symmetric (en CJK=0), '
      + '6 server codes mapped to real states, 0 external assets, 0 storage APIs, '
      + 'success view = permission + group only';
  });

  check('R3-4 evidence face copy + CSS: keys exist in zh and en, styles scoped, the zoom layer is non-modal', () => {
    const js = stripJsComments(read(FILES.extCards));
    const css = stripComments(read(FILES.extCardsCss));
    const problems = [];
    const zhDomain = (zh.cards || {});
    const enDomain = (en.cards || {});
    /* every copy key the evidence / causal code reaches for must exist in BOTH
     * dictionaries: a missing key paints the raw key on the screen (the
     * scaffold round shipped `causal.request.pending` with no entry at all). */
    const referenced = [...new Set([...js.matchAll(/'cards\.([A-Za-z0-9_.]+)'/g)].map((m) => m[1]))]
      .filter((leaf) => !leaf.endsWith('.'));
    /* the kind label is built dynamically ('cards.evidence.kind.' + item.kind),
     * so the schema's own kinds are pinned directly, not through the prefix. */
    ['screenshot', 'diagram', 'diff', 'file'].forEach((kind) => {
      const leaf = 'evidence.kind.' + kind;
      if (typeof zhDomain[leaf] !== 'string' || !zhDomain[leaf]) problems.push('zh key missing: cards.' + leaf);
      if (typeof enDomain[leaf] !== 'string' || !enDomain[leaf]) problems.push('en key missing: cards.' + leaf);
    });
    const required = [
      'evidence.fingerprint', 'evidence.fingerprint.aria', 'evidence.size',
      'evidence.provided_by.runtime', 'evidence.provided_by.user',
      'evidence.provided_by.server', 'evidence.provided_by.unknown',
      'evidence.screenshot.pending', 'evidence.zoom.aria', 'evidence.zoom.close',
      'evidence.zoom.nonmodal', 'evidence.vote.failed.code', 'evidence.vote.count.pending',
      'causal.badge.template', 'causal.badge.llm', 'causal.badge.unknown',
      'causal.note.template', 'causal.note.llm', 'causal.note.model_unknown',
      'causal.request.pending'
    ];
    referenced.concat(required).forEach((leaf) => {
      if (typeof zhDomain[leaf] !== 'string' || !zhDomain[leaf]) problems.push('zh key missing: cards.' + leaf);
      if (typeof enDomain[leaf] !== 'string' || !enDomain[leaf]) problems.push('en key missing: cards.' + leaf);
    });
    const zhLeaves = Object.keys(zhDomain);
    const enLeaves = Object.keys(enDomain);
    if (zhLeaves.length !== enLeaves.length) {
      problems.push('cards domain is not symmetric: zh ' + zhLeaves.length + ' / en ' + enLeaves.length);
    }
    zhLeaves.forEach((leaf) => {
      if (enLeaves.indexOf(leaf) < 0) problems.push('en key missing: cards.' + leaf);
    });
    /* 0.4 honesty register on the generator marker: `model=template` never says
     * AI, `model=llm` is the only case allowed to, and an unlabelled model is
     * admitted instead of guessed. */
    if (/AI/i.test(String(zhDomain['causal.badge.template'] || ''))
      || /AI/i.test(String(enDomain['causal.badge.template'] || ''))) {
      problems.push('the template marker claims AI: the record says model=template');
    }
    if (!/AI/.test(String(zhDomain['causal.badge.llm'] || ''))
      || !/AI/.test(String(enDomain['causal.badge.llm'] || ''))) {
      problems.push('the llm marker must say AI out loud');
    }
    if (!/'cards\.causal\.badge\.unknown'/.test(js)) problems.push('an unlabelled generator is not admitted');
    if (!/CAUSAL_MODEL_KEYS\s*=\s*\{[\s\S]*template:[\s\S]*llm:/.test(js)) {
      problems.push('the marker table does not map the schema enum (template | llm)');
    }
    /* styles: this file owns the cards__ face; no literal colours, no CJK copy,
     * and no other stylesheet reaches in here. */
    const styled = ['cards__evidence', 'cards__strip', 'cards__item', 'cards__thumb', 'cards__thumb-img',
      'cards__thumb-svg', 'cards__thumb-caption', 'cards__diff-body', 'cards__meta', 'cards__fingerprint',
      'cards__size', 'cards__prov', 'cards__pending', 'cards__feedback', 'cards__vote', 'cards__count',
      'cards__vote-state', 'cards__zoom', 'cards__zoom-sheet', 'cards__zoom-head', 'cards__zoom-close',
      'cards__zoom-body', 'cards__zoom-content', 'cards__zoom-note', 'cards__causal', 'cards__why',
      'cards__why-icon', 'cards__causal-pop', 'cards__causal-text', 'cards__causal-list',
      'cards__causal-model', 'cards__badge', 'cards__badge-ai', 'cards__badge-template',
      'cards__badge-unknown', 'cards__causal-model-note'];
    styled.forEach((cls) => {
      if (css.indexOf('.' + cls) < 0) problems.push('cards.css has no rule for .' + cls);
    });
    if (css.indexOf('cards__modal') >= 0) problems.push('cards.css still styles a modal evidence view');
    const hex = strayHex(css);
    if (hex.length) problems.push('literal colour outside a var() fallback: ' + hex.join(', '));
    if (cjkCount(css) !== 0) problems.push('CJK literals in cards.css');
    if (cjkCount(read(FILES.extCardsCss)) !== 0) problems.push('CJK literals in cards.css (comments included)');
    if (css.indexOf('--c-') < 0) problems.push('cards.css uses no design tokens');
    if (stripComments(read(FILES.extCollabCss)).indexOf('cards__') >= 0) problems.push('collab.css reaches into a cards__ selector');
    if (stripComments(read(FILES.extInsightsCss)).indexOf('cards__') >= 0) problems.push('insights.css reaches into a cards__ selector');
    if (stripComments(styleSource).indexOf('cards__') >= 0) problems.push('styles.css reaches into a cards__ selector');
    if (!/ext\/cards\.css/.test(indexSource)) problems.push('index.html does not mount ext/cards.css');
    /* PM §2.1: 放大是「弹层或新视图，非模态阻塞优先」. The layer is transparent
     * and pointer-transparent, only the sheet takes pointers, nothing here
     * locks the page scroll, and the evidence path never uses the modal dialog. */
    if (!/'aria-modal':\s*'false'/.test(js)) problems.push('the zoom layer is not declared aria-modal=false');
    if (!/\.cards__zoom\s*\{[^}]*pointer-events:\s*none/.test(css)) problems.push('the zoom layer is not pointer-transparent: it would block the page');
    if (!/\.cards__zoom\s*\{[^}]*background:\s*transparent/.test(css)) problems.push('the zoom layer paints a scrim, which is the modal look');
    if (!/\.cards__zoom-sheet\s*\{[^}]*pointer-events:\s*auto/.test(css)) problems.push('the zoom sheet does not take pointers');
    if (/(^|[;\n])\s*(body|html)\s*\{[^}]*overflow:\s*hidden/.test(css)) problems.push('cards.css locks the page scroll (a non-modal layer never may)');
    if (/C\.openModal\('cards\.evidence/.test(js)) problems.push('evidence enlargement goes through the blocking modal');
    if (/openEvidenceModal/.test(js)) problems.push('the modal evidence view is still wired');
    if (!/function closeEvidenceZoom/.test(js) || !/zoom: 'open'/.test(js)) problems.push('the zoom layer has no explicit dismissal');
    if (!/pop\.hidden = true/.test(js)) problems.push('the causal bubble is open by default (it is an expander, not a dialog)');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'keys referenced=' + referenced.length + ' (every one in zh AND en, incl. the scaffold gap causal.request.pending), '
      + 'cards domain ' + zhLeaves.length + '/' + enLeaves.length + ' symmetric, '
      + 'R3-4 classes styled=' + styled.length + ', literal colours=0, CJK=0, other stylesheets touch cards__=0, '
      + 'zoom=non-modal (aria-modal=false, layer pointer-events:none, sheet:auto, no scroll lock, no C.openModal)';
  });

  check('R3-4 evidence wiring face: T15/T16/T17 only, failure shows the code, counts come from the ledger', () => {
    const js = stripJsComments(read(FILES.extCards));
    const problems = [];
    /* zero new routes: the face talks to the four endpoints that exist, and
     * `/api/evidence` is the one that is not mounted (docs/03, PM baseline) —
     * the face must show that refusal, never pretend a local success. */
    const endpoints = [...new Set([...js.matchAll(/var EP_[A-Z_]+ = '([^']+)'/g)].map((m) => m[1]))].sort();
    const expected = ['api/evidence', 'api/records', 'api/rollback', 'api/rollback/plan'];
    if (endpoints.join(',') !== expected.join(',')) {
      problems.push('the face uses endpoints outside the existing set: ' + endpoints.join(','));
    }
    if (!/EP_EVIDENCE \+ '\/' \+ encodeURIComponent\(item\.id\) \+ '\/feedback'/.test(js)) {
      problems.push('the vote does not go to T16 POST /api/evidence/<id>/feedback');
    }
    if (!/EP_RECORDS \+ '\/' \+ encodeURIComponent\(str\(record\.id\)\) \+ '\/causal'/.test(js)) {
      problems.push('the request button does not go to T17 POST /api/records/<id>/causal');
    }
    /* §2.3: a refusal is shown with its code, the state is machine-readable, and
     * the counts are never invented by the display layer. */
    if (!/'cards\.evidence\.vote\.failed\.code', \{ code:/.test(js)) problems.push('a failed vote is not shown with its code');
    if (!/data-vote-state|dataset: \{ evidence: item\.id, voteState: 'idle' \}/.test(js)) problems.push('the vote row carries no machine-readable outcome');
    if (/counts\[key\] \+= 1/.test(js)) problems.push('the display layer invents a vote count: the ledger owns the numbers');
    if (!/countsFrom\(result\.data\)/.test(js)) problems.push('the counts are not read from the landed payload');
    if (!/'cards\.evidence\.vote\.count\.pending'/.test(js)) problems.push('a success without counts is not admitted honestly');
    /* §2.6: the fingerprint and the provenance come out of the record verbatim */
    if (!/dataset: \{ sha: item\.sha256 \}/.test(js)) problems.push('the fingerprint is not carried as a machine-readable sha');
    if (!/fp\.setAttribute\('title', item\.sha256\)/.test(js)) problems.push('the full sha256 is not reachable from the shortened label');
    if (!/PROVENANCE_KEYS = \{[\s\S]*runtime:[\s\S]*user:[\s\S]*server:/.test(js)) problems.push('provided_by is not mapped to the schema enum');
    if (!/provided_by\.unknown/.test(js)) problems.push('a record without provided_by is not admitted');
    if (/item\.caption\.(replace|slice|substr|split)/.test(js)) problems.push('the display layer rewrites the landed caption');
    if (/item\.diff\.(replace|slice|substr)/.test(js)) problems.push('the display layer rewrites the diff bytes');
    /* D-7: a screenshot entry without bytes is stated as not wired, twice
     * (the thumbnail and the enlarged view), never as a broken picture. */
    const pending = (js.match(/screenshotPendingNote\(/g) || []).length;
    if (pending !== 3) problems.push('the D-7 note is painted ' + pending + ' times, expected 3 (definition + thumbnail + zoom)');
    if (!/'cards\.evidence\.screenshot\.pending'/.test(js)) problems.push('the D-7 wording is not a dictionary key');
    /* the SVG path keeps its guard: bytes are parsed as SVG, never as HTML */
    if (!/image\/svg\+xml/.test(js)) problems.push('the diagram is not parsed as SVG');
    if (!/MAX_SVG_BYTES = 524288/.test(js)) problems.push('the inline SVG has no size guard');
    if (problems.length) throw new Error(problems.join(' | '));
    return 'endpoints=' + endpoints.join(',') + ' (zero new routes), T16 vote + T17 causal wired, '
      + 'failure shows the code + data-vote-state, counts read from the payload (invented count=0), '
      + 'sha256 fingerprint (short label + full sha in title) + provided_by enum, '
      + 'D-7 screenshot note painted 3x, SVG guard (512KB + image/svg+xml)';
  });

  /* ---------------------------------------------------------------- *
   * R4-2 (REQ-004 B4-1 + B4-2): the merged multi-agent view
   * ---------------------------------------------------------------- */

  const GROUPS_AGENTS = ['agent-alpha', 'agent-bravo', 'agent-charlie', 'agent-delta',
    'agent-echo', 'agent-foxtrot', 'agent-golf', 'agent-hotel', 'agent-india', 'agent-juliet'];

  const GROUPS_ALIAS_NOTE = 'group_id / user_id / updated_at 是 /api/groups 响应层的只读派生别名'
    + '（账本实体未改：id / created_by / closed_at；零迁移、零回填、零新路由）';

  function groupEntities() {
    return [
      { id: 'grp-alpha', group_id: 'grp-alpha', user_id: 'cap-1', title: 'Alpha run',
        objective: 'merge ten agents', status: 'open', agent_ids: GROUPS_AGENTS.slice(),
        created_at: '2026-11-01T08:00:00Z', created_by: 'captain',
        updated_at: '2026-11-01T09:12:00Z', updated_at_basis: 'last_event' },
      { id: 'grp-closed', group_id: 'grp-closed', user_id: 'cap-1', title: 'Closed run',
        objective: 'shipped', status: 'completed', agent_ids: ['agent-alpha'],
        created_at: '2026-10-20T08:00:00Z', created_by: 'captain', closed_at: '2026-10-22T10:00:00Z',
        updated_at: '2026-10-22T10:00:00Z', updated_at_basis: 'closed_at' },
      { id: 'grp-empty', group_id: 'grp-empty', user_id: 'cap-1', title: 'Fallback run',
        objective: 'no parsable event', status: 'open', agent_ids: ['agent-bravo'],
        created_at: '2026-10-19T08:00:00Z', created_by: 'captain',
        updated_at: '2026-10-19T08:00:00Z', updated_at_basis: 'created_at_fallback' }
    ];
  }

  function groupRecords() {
    /* the aggregate's own order: ascending parsable time, and the record whose
     * timestamp the server could not read is already last - the page adds no
     * second sort that would give it a place it did not earn. */
    const rows = GROUPS_AGENTS.map((agent, i) => ({
      id: 'act-r' + (i + 1), group_id: 'grp-alpha', agent_id: agent,
      agent_name: agent.replace('agent-', '') + ' agent',
      action_type: ['read', 'write', 'tool_call', 'plan', 'confirm', 'send', 'export', 'ask', 'wait', 'finish'][i],
      status: i < 7 ? 'succeeded' : (i < 9 ? 'running' : 'pending_confirmation'),
      requires_action: i === 9, timestamp: '2026-11-01T08:0' + i + ':00Z',
      latency_ms: 40 + i, cost: 0.5 + i
    }));
    rows.push({
      id: 'act-r-bad', group_id: 'grp-alpha', agent_id: 'agent-juliet',
      agent_name: 'juliet agent', action_type: 'other', status: 'observed',
      timestamp: 'not-a-timestamp', latency_ms: 7, cost: 0.1
    });
    return rows;
  }

  function groupListPayload() {
    return {
      ok: true, groups: groupEntities(), count: groupEntities().length,
      alias_fields: ['group_id', 'user_id', 'updated_at'],
      note: GROUPS_ALIAS_NOTE,
      facts: { version: '1.0', max_agents: 10, stuck_after_seconds: 600, store: { path: 'groups.jsonl', count: 3 } }
    };
  }

  function groupAggregatePayload(groupId) {
    const group = groupEntities().find((g) => g.group_id === groupId) || groupEntities()[0];
    const id = group.group_id;
    if (id === 'grp-empty') {
      return {
        ok: true, group: group, records: [], agents: [],
        progress: { total: 0, done: 0, running: 0, needs_me: 0, percent: 0 },
        resources: { cost: null, latency_ms: {}, cost_by_agent: {}, latency_by_agent: {} },
        window: { started_at: null, ended_at: null, total_ms: 0 },
        stuck: [], generated_at: '2026-11-01T09:20:00Z',
        alias_fields: ['group_id', 'user_id', 'updated_at'], note: GROUPS_ALIAS_NOTE
      };
    }
    const records = groupRecords();
    return {
      ok: true, group: group, records: records,
      agents: GROUPS_AGENTS.map((agent, i) => ({
        id: agent, name: agent.replace('agent-', '') + ' agent',
        /* the backend's slot is sha256(id) % 8, so it always lands inside the
         * palette: with 10 agents two slots must be shared, which is the
         * collision the panel has to state */
        count: 1, color_index: i % 8, stuck: agent === 'agent-foxtrot'
      })),
      progress: { total: records.length, done: 7, running: 2, needs_me: 1, percent: 54 },
      resources: { cost: 12.5, latency_ms: { avg: 45 }, cost_by_agent: {}, latency_by_agent: {} },
      window: { started_at: '2026-11-01T08:00:00Z', ended_at: '2026-11-01T09:12:00Z', total_ms: 4320000 },
      stuck: ['agent-foxtrot'], generated_at: '2026-11-01T09:13:00Z',
      alias_fields: ['group_id', 'user_id', 'updated_at'], note: GROUPS_ALIAS_NOTE
    };
  }

  function groupsLayer(options) {
    const layer = bootExtLayer(Object.assign({ search: '?view=desktop' }, options || {}));
    loadScript(layer.sandbox, FILES.extGroups, 'ext/groups.js');
    if (!layer.sandbox.TraceLensGroups) throw new Error('ext/groups.js did not expose TraceLensGroups');
    const demo = JSON.parse(JSON.stringify(loadDemoPayload().records || []));
    /* the demo set carries no group_id at all: exactly the shape the ungrouped
     * column must state out loud instead of guessing a group */
    demo.forEach((record) => { delete record.group_id; });
    const calls = [];
    layer.ui.setApiTransport((endpoint, apiPath, body, method) => {
      /* app.js hands the transport the target as it appears in the page
       * (`api/groups`, `api/groups/<id>`): the gate below reads that shape. */
      const target = String(apiPath || '').replace(/^\//, '');
      if (endpoint === 'health') {
        return { status: 200, data: { name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1' } };
      }
      if (endpoint === 'records') {
        return { status: 200, data: { records: demo, meta: { count: demo.length, synthetic: true } } };
      }
      if (endpoint !== 'groups') return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
      calls.push({ apiPath: target, method: method || 'GET' });
      if ((method || 'GET') === 'POST') {
        /* the baseline does not mount the create route (PM-VERDICT-R4-1 §1
         * item 10): the honest answer is 404 ROUTE_NOT_FOUND, which is what the
         * page has to show. The 11-agent shape is exercised in the DOM
         * self-test, where the service's own GROUP_AGENTS_EXCEEDED answer can
         * be scripted against the same panel code. */
        return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {} } };
      }
      if (target === 'api/groups') return { status: 200, data: groupListPayload() };
      const id = target.replace('api/groups/', '');
      if (id === 'grp-ghost') return { status: 404, data: { code: 'GROUP_NOT_FOUND', message: 'no such group' } };
      return { status: 200, data: groupAggregatePayload(id) };
    });
    layer.__groups = { calls: calls, records: demo };
    return layer;
  }

  async function groupsPainted() {
    const layer = groupsLayer();
    await layer.ui.probeApi();
    const synced = await layer.ui.syncRecordsFromApi();
    if (!synced) throw new Error('the fixture records never reached the page');
    layer.ext.boot();
    if (!layer.ext.open('groups')) throw new Error('the groups tab is not reachable (permission gate)');
    await new Promise((resolve) => setTimeout(resolve, 80));
    return layer;
  }

  const nodeText = (node) => (node ? String(node.textContent || '') : '');

  check('R4-2 groups panel: one tab, eight zones, the guarded door, scoped CSS, mounted in index.html', () => {
    const js = read(FILES.extGroups);
    const css = read(FILES.extGroupsCss);
    const html = read(FILES.index);
    if (!/\.register\(\{/.test(js)) throw new Error('panel never registers with the extension shell');
    if (!/id:\s*'groups'/.test(js)) throw new Error('panel id is not groups');
    if (!/order:\s*30/.test(js)) throw new Error('the groups tab has no order slot');
    if (!/permission:\s*'records\.read'/.test(js)) throw new Error('the tab is not gated on records.read');
    ['list', 'detail', 'progress', 'resources', 'agents', 'timeline', 'ungrouped', 'create'].forEach((zone) => {
      if (js.indexOf("'" + zone + "'") < 0) throw new Error('missing zone: ' + zone);
    });
    if (js.indexOf("ctx.get(EP_GROUPS") < 0) throw new Error('panel does not read through the guarded ctx.get');
    if (js.indexOf("ctx.post(EP_GROUPS") < 0) throw new Error('the create form does not go through the guarded ctx.post');
    if (!/data-tab="groups"/.test(css)) throw new Error('panel CSS is not scoped to the groups tab');
    if (!/ext\/groups\.css/.test(html)) throw new Error('index.html does not mount ext/groups.css');
    if (!/ext\/groups\.js/.test(html)) throw new Error('index.html does not mount ext/groups.js');
    if (cjkCount(js) !== 0) throw new Error('panel source contains ' + cjkCount(js) + ' CJK literals');
    if (cjkCount(css) !== 0) throw new Error('panel CSS contains CJK literals');
    const zones = (js.match(/zone\(ctx, panel, '/g) || []).length;
    /* chars and bytes are different口径 (the source comments carry arrows), so
     * both are printed: a size quoted in a report must say which one it is */
    return 'panel ' + js.length + ' chars / ' + Buffer.byteLength(js, 'utf8') + ' bytes, css '
      + css.length + ' chars / ' + Buffer.byteLength(css, 'utf8') + ' bytes, zones=' + zones
      + ', CJK=0, mounted=css+js';
  });

  check('R4-2 colour contract: ext/groups.js copies groups.py COLOR_PALETTE, MAX_AGENTS pinned, no second palette', () => {
    const py = read(FILES.groupsPy);
    const js = read(FILES.extGroups);
    const css = read(FILES.extGroupsCss);
    const pyBlock = /COLOR_PALETTE = \(([\s\S]*?)\)/.exec(py);
    const jsBlock = /var GROUP_COLOR_PALETTE = \[([\s\S]*?)\]/.exec(js);
    if (!pyBlock) throw new Error('groups.py no longer exposes a readable COLOR_PALETTE');
    if (!jsBlock) throw new Error('ext/groups.js carries no GROUP_COLOR_PALETTE (the JS side of the contract)');
    const hexes = (text) => (text.match(/#[0-9a-fA-F]{6}/g) || []).map((h) => h.toLowerCase());
    const pyPalette = hexes(pyBlock[1]);
    const jsPalette = hexes(jsBlock[1]);
    if (pyPalette.length !== jsPalette.length) {
      throw new Error('palette lengths differ: python=' + pyPalette.length + ' js=' + jsPalette.length);
    }
    const drift = pyPalette.filter((h, i) => h !== jsPalette[i]);
    if (drift.length) throw new Error('the palettes drifted (change both sides): ' + drift.join(', '));
    const pySize = /PALETTE_SIZE = len\(COLOR_PALETTE\)/.test(py);
    const jsSize = /var GROUP_PALETTE_SIZE = GROUP_COLOR_PALETTE\.length/.test(js);
    if (!pySize || !jsSize) throw new Error('the palette size is not derived from the table on both sides');
    const pyMax = /MAX_AGENTS = (\d+)/.exec(py);
    const jsMax = /var GROUP_MAX_AGENTS = (\d+)/.exec(js);
    if (!pyMax || !jsMax || pyMax[1] !== jsMax[1]) {
      throw new Error('the agent limit drifted: python=' + (pyMax && pyMax[1]) + ' js=' + (jsMax && jsMax[1]));
    }
    if (Number(pyMax[1]) !== 10) throw new Error('MAX_AGENTS is no longer 10: ' + pyMax[1]);
    /* the CSS owns no agent colour: no palette hex appears in it outside a
     * token fallback, and every colour it does carry sits inside var() */
    const stripped = css.replace(/var\([^()]*\)/g, 'var()');
    const paletteInCss = pyPalette.filter((hex) => stripped.toLowerCase().indexOf(hex) >= 0);
    if (paletteInCss.length) {
      throw new Error('the CSS ships a second agent palette (the colour comes from the backend index): ' + paletteInCss.join(', '));
    }
    const stray = (stripped.match(/#[0-9a-fA-F]{3,8}\b/g) || []);
    if (stray.length) throw new Error('literal colours outside var() fallbacks: ' + stray.join(', '));
    /* the honest consequence of 8 slots for 10 agents is stated, not hidden */
    if (js.indexOf('groups.agents.color_shared') < 0) throw new Error('the palette collision is not stated by the panel');
    return 'palette=' + pyPalette.length + ' hexes identical (python/js), MAX_AGENTS=' + pyMax[1]
      + ', css palette hexes=0, css colours outside var()=0';
  });

  check('R4-2 alias discipline: alias_fields + note + updated_at_basis consumed, three bases, no browser clock', () => {
    const js = read(FILES.extGroups);
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    ['alias_fields', 'note', 'updated_at_basis'].forEach((field) => {
      if (js.indexOf("'" + field + "'") < 0 && js.indexOf('.' + field) < 0) {
        throw new Error('the panel does not consume the response field ' + field);
      }
    });
    if (js.indexOf('data.note') < 0) throw new Error('the server note is not rendered from the response');
    ['closed_at', 'last_event', 'created_at_fallback'].forEach((basis) => {
      const key = 'groups.alias.basis.' + basis;
      const leaf = 'alias.basis.' + basis;
      if (js.indexOf("'" + key + "'") < 0) throw new Error('no label wired for the basis ' + basis);
      if (!zh.groups[leaf] || !en.groups[leaf]) throw new Error('basis label key missing in a dictionary: ' + key);
    });
    if (zh.groups['alias.basis.absent'] === zh.groups['alias.basis.last_event']) {
      throw new Error('the absent-basis fallback copy is identical to a real basis');
    }
    /* 0.4: an alias is never printed as a migrated ledger, and the page never
     * reaches for a clock to fill a missing time. Comments are stripped first:
     * the rule itself names the words it forbids in shipped copy. */
    const code = js.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ');
    if (/migrat/i.test(code)) {
      throw new Error('the panel code talks about migration (the ledger is not migrated)');
    }
    if (/new Date\(/.test(code)) throw new Error('the panel builds a Date (the page has no clock authority)');
    const paint = /function paintTimeline\([\s\S]*?\n  \}/.exec(code);
    if (!paint) throw new Error('paintTimeline is not readable (the render timing must be measurable)');
    const nowTotal = (code.match(/Date\.now\(\)/g) || []).length;
    const nowInPaint = (paint[0].match(/Date\.now\(\)/g) || []).length;
    if (nowTotal === 0 || nowTotal !== nowInPaint) {
      throw new Error('Date.now() must live only in the render measurement (total=' + nowTotal
        + ', in paintTimeline=' + nowInPaint + ')');
    }
    if (js.indexOf('ctx.fmtTime') < 0) throw new Error('the panel does not print times through the app formatter');
    if (js.indexOf("groups.alias.updated.absent") < 0) throw new Error('a missing updated_at is not stated honestly');
    return 'alias fields consumed=3, basis labels=3 (zh+en), note=verbatim, new Date()=0, '
      + 'Date.now()=' + nowTotal + ' all inside paintTimeline=' + nowInPaint + ' (render timing only)';
  });

  check('R4-2 honest numbers: denominator = progress.total, duration = window.total_ms, members never the denominator', () => {
    const js = read(FILES.extGroups);
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    if (js.indexOf('source: \'progress.total\'') < 0) throw new Error('the progress bar does not name its denominator source');
    if (js.indexOf('denominator: \'records-in-view\'') < 0) throw new Error('the bar does not state that the denominator is the view record count');
    if (js.indexOf('groups.progress.note') < 0) throw new Error('the denominator is not explained in words');
    if (js.indexOf('groups.progress.members_not_denominator') < 0) throw new Error('the member count is not excluded from the denominator');
    if (js.indexOf('source: \'window.total_ms\'') < 0) throw new Error('the elapsed time does not name the deterministic window');
    if (js.indexOf('groups.resources.elapsed_note') < 0) throw new Error('the elapsed source is not explained in words');
    if (js.indexOf('data-render-ms') < 0) throw new Error('the render time is not machine-readable on the DOM');
    ['progress.note', 'progress.members_not_denominator', 'resources.elapsed_note'].forEach((leaf) => {
      const zhValue = String(zh.groups[leaf] || '');
      const enValue = String(en.groups[leaf] || '');
      if (!zhValue || !enValue) throw new Error('copy missing for ' + leaf);
      if (!/\{members\}/.test(zh.groups['progress.members_not_denominator'])) {
        throw new Error('the members line lost its parameter');
      }
    });
    /* the percent the server answers with is printed as it is: groups.py
     * _percent() is an integer 0-100, so a second rounding in the printed copy
     * would be a second fact source (the bar's own fill width is a pixel
     * ratio, not a printed number, so it is not this rule) */
    const progressFn = /function paintProgress\([\s\S]*?\n  \}/.exec(js);
    if (!progressFn) throw new Error('paintProgress is not readable');
    if (progressFn[0].indexOf("'groups.progress.percent', { percent: percent }") < 0) {
      throw new Error('progress.percent is not printed as the integer the server answered');
    }
    return 'denominator=progress.total, duration=window.total_ms, members excluded in copy (zh+en), percent printed as served';
  });

  check('R4-2 zero new routes: the panel speaks only api/groups, the app.js door is untouched', () => {
    const js = read(FILES.extGroups);
    const app = read(FILES.app);
    const targets = [...new Set((js.match(/'api\/[a-z0-9_\/-]*'/g) || []).map((s) => s.slice(1, -1)))].sort();
    if (targets.length !== 1 || targets[0] !== 'api/groups') {
      throw new Error('the panel reaches for more than the groups base: ' + targets.join(', '));
    }
    if (!/groups: 'api\/groups'/.test(app)) throw new Error('app.js no longer allowlists the groups base');
    const door = /function extRequest\([\s\S]*?\n  \}/.exec(app);
    if (!door) throw new Error('the guarded request door is not readable in app.js');
    if (/'api\/groups\/[^']*'/.test(js)) {
      throw new Error('the panel hardcodes a groups sub-path (the id must come from the response)');
    }
    const verbs = (js.match(/ctx\.(get|post)\(/g) || []).length;
    return 'targets=' + targets.join(',') + ' (allowlisted base), ctx calls=' + verbs + ', app.js door intact';
  });

  check('R4-2 10-agent boundary: 10 shown, no truncation, 11 refused, the create route states 404', () => {
    const js = read(FILES.extGroups);
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    if (js.indexOf('truncated: \'false\'') < 0) throw new Error('the panel does not state that it truncates nothing');
    if (js.indexOf('groups.agents.max_note') < 0) throw new Error('the 10-agent boundary is not stated in the agents zone');
    if (js.indexOf('groups.create.max') < 0) throw new Error('the create form does not refuse more than 10 agents locally');
    if (js.indexOf('GROUP_AGENTS_EXCEEDED') < 0) throw new Error('the server refusal code is not mapped to copy');
    if (js.indexOf('groups.create.unavailable') < 0) throw new Error('the unmounted create route is not stated by the form');
    if (js.indexOf("'ROUTE_NOT_FOUND'") < 0) throw new Error('the 404 answer is not shown as itself');
    if (js.indexOf("feedback.setAttribute('data-state', 'ok')") >= 0
      && js.indexOf("if (res.ok)") < 0) throw new Error('a create success is claimed without the server saying ok');
    ['agents.max_note', 'create.unavailable', 'create.max'].forEach((leaf) => {
      if (!zh.groups[leaf] || !en.groups[leaf]) throw new Error('boundary copy missing: ' + leaf);
    });
    if (zh.groups['create.max'].indexOf('{max}') < 0 || zh.groups['create.max'].indexOf('{count}') < 0) {
      throw new Error('the boundary copy lost its parameters');
    }
    return 'shown=10 (max pinned 10), truncated=false, 11th=create.max + GROUP_AGENTS_EXCEEDED, POST=ROUTE_NOT_FOUND stated';
  });

  check('R4-2 i18n parts: groups.zh/en parts add only new keys, parity, parameters identical, en has no CJK', () => {
    const partsZh = readJson(FILES.i18nGroupsParts);
    const partsEn = readJson(FILES.i18nGroupsPartsEn);
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    const collab = readJson(path.join(HERE, 'i18n', 'parts', 'collab.zh.json'));
    const zhLeaves = Object.keys(partsZh.groups);
    const enLeaves = Object.keys(partsEn.groups);
    if (zhLeaves.length !== enLeaves.length) throw new Error('the parts files differ in key count');
    const drift = zhLeaves.filter((k) => enLeaves.indexOf(k) < 0);
    if (drift.length) throw new Error('en parts is missing: ' + drift.join(', '));
    const overlap = zhLeaves.filter((k) => Object.prototype.hasOwnProperty.call(collab.groups, k));
    if (overlap.length) throw new Error('the parts file re-declares keys the collab parts owns: ' + overlap.join(', '));
    const missing = zhLeaves.filter((k) => !zh.groups[k] || !en.groups[k]);
    if (missing.length) throw new Error('parts keys not merged into the dictionaries: ' + missing.join(', '));
    const params = (text) => (String(text).match(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g) || []).sort().join(',');
    const paramDrift = zhLeaves.filter((k) => params(zh.groups[k]) !== params(en.groups[k]));
    if (paramDrift.length) throw new Error('zh/en parameters drifted for: ' + paramDrift.join(', '));
    const cjkEn = zhLeaves.filter((k) => cjkCount(en.groups[k]) !== 0);
    if (cjkEn.length) throw new Error('en copy contains CJK: ' + cjkEn.join(', '));
    return 'new keys=' + zhLeaves.length + ' (zh+en parity, params identical, en CJK=0, no key stolen from collab)';
  });

  check('R4-2 runtime: 10 agents with backend colours, merged order, bad stamps last, ungrouped column', async () => {
    const layer = await groupsPainted();
    const py = read(FILES.groupsPy);
    const palette = (/(COLOR_PALETTE = \([\s\S]*?\))/.exec(py)[1].match(/#[0-9a-fA-F]{6}/g) || [])
      .map((h) => h.toLowerCase());
    const body = layer.document.body;
    const panel = queryAll(body, 'section.ext-grp')[0];
    if (!panel) throw new Error('the groups panel did not paint (no section.ext-grp)');
    if (layer.document.getElementById('trust-body').getAttribute('data-tab') !== 'groups') {
      throw new Error('the shell did not open the groups tab');
    }
    const zones = queryAll(panel, 'section[data-zone]');
    if (zones.length !== 8) throw new Error('zones painted=' + zones.length + ' (expected 8)');
    const agents = queryAll(panel, 'li.ext-grp__agent');
    if (agents.length !== 10) throw new Error('agent rows=' + agents.length + ' (B4-2 needs all 10, no truncation)');
    const legend = queryAll(panel, 'ul.ext-grp__agents')[0];
    if (legend.getAttribute('data-agents') !== '10' || legend.getAttribute('data-max') !== '10') {
      throw new Error('the legend does not carry agents=10/max=10: ' + legend.getAttribute('data-agents')
        + '/' + legend.getAttribute('data-max'));
    }
    /* the chip colour is the backend index rendered through the backend table */
    const chips = queryAll(legend, 'span.ext-grp__chip');
    const wrong = [];
    agents.forEach((row, i) => {
      const chip = row.querySelectorAll('span.ext-grp__chip')[0];
      if (!chip) { wrong.push('agent ' + i + ' has no chip'); return; }
      const index = Number(chip.getAttribute('data-index'));
      const style = String(chip.getAttribute('style') || '');
      if (style.indexOf(palette[index]) < 0) {
        wrong.push('agent index ' + index + ' is not the backend hex ' + palette[index] + ' (' + style + ')');
      }
    });
    if (wrong.length) throw new Error(wrong.join('; '));
    /* ten agents into eight slots: the collision is stated, not hidden */
    const shared = Number(legend.getAttribute('data-color-shared'));
    if (shared < 2) throw new Error('the 8-slot collision is not counted: ' + shared);
    const colorNote = queryAll(panel, 'p.ext-grp__note--color')[0];
    if (!colorNote) throw new Error('the colour collision has no note on screen');
    if (colorNote.getAttribute('data-size') !== String(palette.length)) {
      throw new Error('the note does not state the palette size the backend owns');
    }
    const abbrs = agents.map((row) => row.querySelectorAll('.ext-grp__avatar-abbr')[0])
      .filter((node) => !!node).map((node) => node.textContent);
    if (new Set(abbrs).size !== 10) throw new Error('avatar abbreviations are not 10 distinct: ' + abbrs.join(','));
    /* merged timeline: 11 rows, the unreadable stamp stays last */
    const table = queryAll(panel, 'table.ext-grp__timeline')[0];
    if (!table) throw new Error('no merged timeline');
    const rows = queryAll(table, 'tbody tr');
    if (rows.length !== 11) throw new Error('timeline rows=' + rows.length + ' (expected 11)');
    if (table.getAttribute('data-total') !== '11' || table.getAttribute('data-bad-time') !== '1') {
      throw new Error('the timeline does not count its rows honestly: ' + table.getAttribute('data-total')
        + '/' + table.getAttribute('data-bad-time'));
    }
    if (rows[rows.length - 1].getAttribute('data-bad-time') !== 'true') {
      throw new Error('the record with an unreadable timestamp is not last');
    }
    const ordered = rows.slice(0, 10).map((r) => r.getAttribute('data-time'));
    const sortedCopy = ordered.slice().sort();
    if (ordered.join('|') !== sortedCopy.join('|')) throw new Error('the merged view is not time-ordered');
    /* filters keep the time order, and hiding an agent hides only its rows */
    const toggles = queryAll(panel, 'button.ext-grp__filter-btn');
    if (toggles.length !== 10) throw new Error('agent filter buttons=' + toggles.length);
    toggles[0].click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const filtered = queryAll(panel, 'table.ext-grp__timeline tbody tr');
    if (filtered.length !== 10) throw new Error('hiding one agent left ' + filtered.length + ' rows');
    if (filtered.some((r) => r.getAttribute('data-agent') === GROUPS_AGENTS[0])) {
      throw new Error('the filtered view still shows the hidden agent');
    }
    const filteredTimes = filtered.slice(0, 9).map((r) => r.getAttribute('data-time'));
    if (filteredTimes.join('|') !== filteredTimes.slice().sort().join('|')) {
      throw new Error('the filtered view lost the time order');
    }
    const hiddenNote = nodeText(panel).indexOf('1 个 Agent 被隐藏') >= 0;
    if (!hiddenNote) throw new Error('the hidden agent is not counted in words');
    toggles[0].click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    /* descending: the parsable part flips, the unreadable stamp stays last */
    const sort = queryAll(panel, 'button[data-act="sort"]')[0];
    sort.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const desc = queryAll(panel, 'table.ext-grp__timeline tbody tr');
    if (desc[desc.length - 1].getAttribute('data-bad-time') !== 'true') {
      throw new Error('descending order moved the unreadable stamp out of the last place');
    }
    const descTimes = desc.slice(0, 10).map((r) => r.getAttribute('data-time'));
    if (descTimes.join('|') !== descTimes.slice().sort().reverse().join('|')) {
      throw new Error('descending order is not the reverse time order');
    }
    sort.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    /* honest denominator + deterministic duration */
    const bar = queryAll(panel, 'div.ext-grp__bar')[0];
    if (bar.getAttribute('data-total') !== '11' || bar.getAttribute('data-source') !== 'progress.total') {
      throw new Error('the progress bar does not take its denominator from the response: '
        + bar.getAttribute('data-total') + '/' + bar.getAttribute('data-source'));
    }
    if (bar.getAttribute('aria-valuemax') !== '11') throw new Error('the bar max is not the response total');
    if (queryAll(panel, 'p.ext-grp__note').length < 2) throw new Error('the honest denominator line is missing');
    const elapsed = queryAll(panel, 'p.ext-grp__elapsed')[0];
    if (elapsed.getAttribute('data-ms') !== '4320000'
      || elapsed.getAttribute('data-source') !== 'window.total_ms') {
      throw new Error('the total duration is not the server window: ' + elapsed.getAttribute('data-ms'));
    }
    /* R4-1 aliases: the strip, the verbatim note, the basis label */
    const strip = queryAll(panel, 'div.ext-grp__alias')[0];
    if (!strip || strip.getAttribute('data-note') !== 'server') throw new Error('the alias strip is not fed by the server note');
    const noteLine = queryAll(strip, 'p.ext-grp__alias-note')[0];
    if (nodeText(noteLine).indexOf(GROUPS_ALIAS_NOTE) < 0) throw new Error('the server note is not shown verbatim');
    const fieldsLine = nodeText(queryAll(strip, 'p.ext-grp__alias-fields')[0]);
    ['group_id', 'user_id', 'updated_at'].forEach((field) => {
      if (fieldsLine.indexOf(field) < 0) throw new Error('alias_fields not shown: ' + field);
    });
    const updated = queryAll(panel, 'p.ext-grp__updated')[0];
    if (updated.getAttribute('data-basis') !== 'last_event') {
      throw new Error('updated_at is shown without the basis label (last_event expected): ' + updated.getAttribute('data-basis'));
    }
    const basisText = nodeText(queryAll(updated, 'span.ext-grp__updated-basis')[0]);
    const zh = readJson(FILES.zh);
    if (basisText.indexOf(String(zh.groups['alias.basis.last_event'])) < 0) {
      throw new Error('the basis label copy is not the dictionary sentence: ' + basisText);
    }
    /* ungrouped: the demo set has no group_id, so the column must say so */
    const ungroupedZone = queryAll(panel, 'section[data-zone="ungrouped"]')[0];
    const ungroupedBox = queryAll(ungroupedZone, 'div.ext-grp__ungrouped')[0];
    const ungroupedCount = layer.__groups.records.length;
    if (ungroupedBox.getAttribute('data-count') !== String(ungroupedCount)) {
      throw new Error('the ungrouped count is not the record count: ' + ungroupedBox.getAttribute('data-count'));
    }
    if (ungroupedBox.getAttribute('data-guessed') !== 'false') throw new Error('the panel claims it guessed a group');
    const listRows = queryAll(panel, 'table.ext-grp__table tbody tr');
    if (listRows.length !== 3) throw new Error('the group list rows=' + listRows.length);
    if (ungroupedCount === 0) throw new Error('the fixture lost its ungrouped records');
    const calls = layer.__groups.calls.map((c) => c.apiPath + ' ' + c.method);
    if (calls.filter((c) => c === 'api/groups GET').length !== 1) {
      throw new Error('the view is not one list call: ' + calls.join(', '));
    }
    const renderMs = Number(queryAll(panel, 'table.ext-grp__timeline')[0].getAttribute('data-render-ms'));
    if (!(renderMs >= 0)) throw new Error('the render time is not machine-readable');
    return 'zones=8, agents=10 (backend palette, colour-shared=' + shared + ', abbreviations 10 distinct), '
      + 'rows=11 (bad stamp last in asc+desc), filter keeps order, denominator=11 (progress.total), '
      + 'duration=4320000ms (window.total_ms), alias note verbatim, basis=last_event, ungrouped='
      + ungroupedCount + ' (guessed=false), render-ms=' + renderMs;
  });

  check('R4-2 create form is honest: no fake success, 11 agents refused, the unmounted route shown as 404', async () => {
    const layer = await groupsPainted();
    const panel = queryAll(layer.document.body, 'section.ext-grp')[0];
    const form = queryAll(panel, 'form.ext-grp__create')[0];
    if (!form) throw new Error('the create zone has no form');
    const feedback = queryAll(form, 'p.ext-grp__create-feedback')[0];
    const titleInput = queryAll(form, '[data-field="create-title"]')[0];
    const agentsInput = queryAll(form, '[data-field="create-agents"]')[0];
    const submit = queryAll(form, 'button[data-act="create-submit"]')[0];
    if (feedback.getAttribute('data-state') !== 'idle') throw new Error('the form starts in a non-idle state');
    /* the shim has no form-submit synthesis: the page's handler is on the
     * form, and a real browser reaches it from the submit button (type
     * submit), which the form carries and this check asserts below */
    const send = () => form.dispatchEvent({ type: 'submit' });
    if (submit.getAttribute('type') !== 'submit') throw new Error('the create button is not a submit button');
    send();
    if (feedback.getAttribute('data-state') !== 'invalid') {
      throw new Error('an empty create is not reported as invalid: ' + feedback.getAttribute('data-state'));
    }
    titleInput.value = 'Eleven agents';
    agentsInput.value = GROUPS_AGENTS.concat(['agent-kilo']).join(',');
    send();
    if (feedback.getAttribute('data-state') !== 'over-max') {
      throw new Error('11 agents are not refused locally: ' + feedback.getAttribute('data-state'));
    }
    if (nodeText(feedback).indexOf('10') < 0) throw new Error('the refusal does not state the limit');
    agentsInput.value = GROUPS_AGENTS.slice(0, 2).join(',');
    send();
    await new Promise((resolve) => setTimeout(resolve, 40));
    if (feedback.getAttribute('data-state') !== 'refused:ROUTE_NOT_FOUND') {
      throw new Error('the unmounted route is not shown as the server answered: ' + feedback.getAttribute('data-state'));
    }
    const text = nodeText(feedback);
    if (text.indexOf('ROUTE_NOT_FOUND') < 0) throw new Error('the code is not printed');
    if (text.indexOf(String(readJson(FILES.zh).groups['create.unavailable'])) < 0) {
      throw new Error('the form does not state that the route is not mounted');
    }
    const posts = layer.__groups.calls.filter((c) => c.method === 'POST');
    if (posts.length !== 1) throw new Error('one valid create = ' + posts.length + ' POSTs');
    if (posts[0].apiPath !== 'api/groups') throw new Error('the POST left the allowlisted base: ' + posts[0].apiPath);
    return 'idle -> invalid (empty) -> over-max (11 agents, limit printed) -> refused:ROUTE_NOT_FOUND (1 POST on api/groups), no success state';
  });

  check('R4-2 degraded states: no group selected, group not found, and the empty window all read from the response', async () => {
    const layer = groupsLayer();
    await layer.ui.probeApi();
    await layer.ui.syncRecordsFromApi();
    layer.ext.boot();
    layer.ext.open('groups');
    await new Promise((resolve) => setTimeout(resolve, 60));
    const panel = queryAll(layer.document.body, 'section.ext-grp')[0];
    const rows = queryAll(panel, 'table.ext-grp__table tbody tr');
    if (rows.length !== 3) throw new Error('group list rows=' + rows.length);
    /* the third group has no records and no window: the panel must not invent
     * a duration, a progress bar of 0/0, or an agent list */
    const third = rows[2].querySelectorAll('button[data-act="select"]')[0];
    third.click();
    await new Promise((resolve) => setTimeout(resolve, 60));
    const updated = queryAll(panel, 'p.ext-grp__updated')[0];
    if (updated.getAttribute('data-basis') !== 'created_at_fallback') {
      throw new Error('the created_at fallback basis is not shown: ' + updated.getAttribute('data-basis'));
    }
    const zh = readJson(FILES.zh);
    if (nodeText(queryAll(updated, 'span.ext-grp__updated-basis')[0])
      .indexOf(String(zh.groups['alias.basis.created_at_fallback'])) < 0) {
      throw new Error('the fallback basis sentence is not the dictionary copy');
    }
    const elapsed = queryAll(panel, 'p.ext-grp__elapsed')[0];
    if (elapsed.getAttribute('data-ms') !== '' || elapsed.getAttribute('data-window') !== 'absent') {
      throw new Error('a window with no endpoints still prints a duration: '
        + elapsed.getAttribute('data-ms') + '/' + elapsed.getAttribute('data-window'));
    }
    if (nodeText(elapsed).indexOf(String(zh.groups['resources.unknown'])) < 0) {
      throw new Error('an empty window is not printed as the server\'s "not provided"');
    }
    const bar = queryAll(panel, 'div.ext-grp__bar')[0];
    if (bar.getAttribute('data-total') !== '0') throw new Error('the bar denominator is invented for an empty group');
    if (queryAll(panel, 'li.ext-grp__agent').length !== 0) throw new Error('agents appear for a group with none');
    if (nodeText(panel).indexOf(String(zh.groups['agents.none'])) < 0) throw new Error('the empty agent list is not stated');
    if (nodeText(panel).indexOf(String(zh.groups['stuck.empty'])) < 0) throw new Error('the empty stuck list is not stated');
    /* the group-not-found code maps to its dictionary sentence */
    const errorKeys = read(FILES.extGroups);
    const en = readJson(FILES.en);
    ['GROUP_NOT_FOUND', 'GROUP_FIELD_INVALID', 'GROUP_AGENTS_EXCEEDED', 'GROUP_CLOSED', 'GROUP_WRITE_FAILED']
      .forEach((code) => {
        const mapped = new RegExp("['\"]?" + code + "['\"]?\\s*:\\s*'groups\\.error\\.[a-z_]+").exec(errorKeys);
        if (!mapped) throw new Error('server code not mapped to copy: ' + code);
        const leaf = /groups\.error\.([a-z_]+)/.exec(mapped[0])[1];
        if (!zh.groups['error.' + leaf] || !en.groups['error.' + leaf]) {
          throw new Error('server code ' + code + ' maps to a key that is not in both dictionaries: ' + leaf);
        }
      });
    const cost = queryAll(panel, 'p.ext-grp__cost')[0];
    if (cost.getAttribute('data-reported') !== 'false') throw new Error('a missing cost is presented as a number');
    return 'empty group: basis=created_at_fallback, duration=resources.unknown, total=0, agents=none, stuck=empty; '
      + '5 server codes mapped to groups.error.* copy';
  });

  /* ---- R8-1 (REQ-008 B8-4 first half): the pre-export verification gate ------
   * The backend refuses to package an evidence bundle while the audit chain or the
   * export ledger chain is broken, and it writes nothing at all. The page's side of
   * that promise is narrow and checkable: the refusal code maps to its own
   * dictionary line, the export counter does not move, no last-export line appears,
   * the raw server sentence never reaches the page, and zh/en/embed are in sync.
   * No new call face is mounted this round (the compliance export route is still
   * declared-but-unmounted), so the audit export panel is the reachable face. */
  check('R8-1 pre-export gate: a chain-broken refusal maps to its own copy, moves no counter, fakes no download', async () => {
    const inst = auditBoot({ exportStatus: 500, exportCode: 'COMPLIANCE_CHAIN_BROKEN' });
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const out = await inst.ui.exportAudit('csv');
    if (!out || out.ok !== false) throw new Error('a gate refusal was treated as a success: ' + JSON.stringify(out));
    if (out.code !== 'COMPLIANCE_CHAIN_BROKEN') throw new Error('the refusal code was lost: ' + out.code);
    if (inst.auditCalls('auditExport').length !== 1) throw new Error('the refusal retried the export: ' + inst.auditCalls('auditExport').length + ' calls');
    const snap = inst.ui.exportState();
    if (snap.count !== 0) throw new Error('a refusal counted as an export: count=' + snap.count);
    if (snap.error !== 'export.error.compliance_chain_broken') throw new Error('refusal line = ' + snap.error);
    const line = ivFind(inst.document, '[data-hook="export-feedback"]')[0];
    if (!line) throw new Error('the export panel has no feedback line to refuse with');
    if (line.getAttribute('data-i18n') !== 'export.error.compliance_chain_broken') {
      throw new Error('the refusal is not dictionary copy: ' + line.getAttribute('data-i18n'));
    }
    const shown = String(line.textContent || '');
    if (!shown) throw new Error('the refusal line renders empty');
    if (shown.indexOf('Audit chain is broken at entry 3') >= 0) throw new Error('the raw server message is pasted into the page');
    const lastNode = ivFind(inst.document, '[data-hook="export-last"]')[0];
    if (lastNode && lastNode.getAttribute('data-visible') !== 'false') {
      throw new Error('a refusal shows a last-export line - the page claims a file that was never written');
    }
    /* the map + the dictionaries + the generated embed must agree, key for key */
    const app = read(FILES.app);
    const map = app.split('EXPORT_ERROR_KEYS').slice(1).join('EXPORT_ERROR_KEYS').split('};')[0];
    if (!/COMPLIANCE_CHAIN_BROKEN\s*:\s*'export\.error\.compliance_chain_broken'/.test(map)) {
      throw new Error('app.js does not map COMPLIANCE_CHAIN_BROKEN to its own dictionary line');
    }
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    const leaf = 'error.compliance_chain_broken';
    if (!zh.export || !zh.export[leaf]) throw new Error('zh.json is missing export.' + leaf);
    if (!en.export || !en.export[leaf]) throw new Error('en.json is missing export.' + leaf);
    if (shown.indexOf(String(zh.export[leaf])) < 0 && shown.indexOf(String(en.export[leaf])) < 0) {
      throw new Error('the rendered line is not the dictionary sentence');
    }
    if (String(zh.export[leaf]).indexOf('零') < 0) throw new Error('the zh refusal does not say the zero-output promise');
    if (String(en.export[leaf]).indexOf('wrote nothing') < 0) throw new Error('the en refusal does not say the zero-output promise');
    [/成功/, /success/i].forEach(() => {
      if (/成功/.test(String(zh.export[leaf])) || /success/i.test(String(en.export[leaf]))) {
        throw new Error('a refusal line must not read like a success');
      }
    });
    const embed = read(path.join(HERE, 'i18n', 'embed.js'));
    if (embed.indexOf(leaf) < 0) throw new Error('i18n/embed.js is out of sync: run selfcheck.mjs --sync-embed');
    const keys = read(path.join(HERE, 'i18n', 'keys.md'));
    if (keys.indexOf('export.' + leaf) < 0) throw new Error('i18n/keys.md is out of sync: run selfcheck.mjs --sync-keys');
    return 'gate refusal: code mapped to export.error.compliance_chain_broken, count=0, no last-export line, '
      + 'raw server text kept out of the page, zh+en+embed+keys in sync';
  });

  /* ---- R8-5 (REQ-008 B8-3 under BOSS-REPLY-PM-PLAN-001 §D-2): the degraded
   * anchoring state is shown where a person looks for the evidence file.
   * Three things are checkable from the DOM: the panel states anchor_state
   * 'unavailable' and eth_tx_hash 'none' (the backend's own constants), the CID
   * line sits next to the "unanchored / awaiting integration" wording, and no
   * on-chain progress or completion copy exists anywhere in the dictionaries.
   * The live export face (button / range picker / progress / result page) is
   * R8-6; this round only the honest static slice is allowed to exist. */
  check('R8-5 degraded anchor state: panel says unanchored, shows no on-chain progress, fakes no tx hash', async () => {
    const inst = auditBoot();
    await inst.ui.probeApi();
    await inst.ui.authProbe();
    const box = ivFind(inst.document, '[data-hook="export-anchor"]')[0];
    if (!box) {
      throw new Error('the export panel has no degraded-state block: an offline CID without an '
        + 'anchor note is a lie waiting to happen');
    }
    if (box.getAttribute('data-anchor-state') !== 'unavailable') {
      throw new Error('the panel shows an anchor state the backend does not have: '
        + box.getAttribute('data-anchor-state'));
    }
    if (box.getAttribute('data-eth-tx-hash') !== 'none') {
      throw new Error('the panel carries a transaction hash that was never produced: '
        + box.getAttribute('data-eth-tx-hash'));
    }
    if (box.getAttribute('data-cid-anchored') !== 'false') {
      throw new Error('the panel implies the offline CID is anchored');
    }
    const leaves = ['anchor.state', 'anchor.cid', 'anchor.tx', 'anchor.deferred'];
    const lines = ivFind(inst.document, '[data-hook^="export-anchor-"]');
    if (lines.length !== leaves.length) {
      throw new Error('the degraded block has ' + lines.length + ' lines, expected ' + leaves.length);
    }
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    lines.forEach((node) => {
      const key = String(node.getAttribute('data-i18n') || '');
      const leaf = key.replace(/^export\./, '');
      if (leaves.indexOf(leaf) < 0) throw new Error('degraded line is not dictionary copy: ' + key);
      if (!zh.export || !zh.export[leaf]) throw new Error('zh.json is missing export.' + leaf);
      if (!en.export || !en.export[leaf]) throw new Error('en.json is missing export.' + leaf);
      const shown = String(node.textContent || '');
      if (!shown) throw new Error('degraded line renders empty: ' + key);
      if (shown.indexOf(String(zh.export[leaf])) < 0 && shown.indexOf(String(en.export[leaf])) < 0) {
        throw new Error('the degraded line is not the dictionary sentence: ' + key);
      }
    });
    const words = lines.map((node) => String(node.textContent || '')).join(' ');
    const cidLine = String((zh.export['anchor.cid'] || ''));
    if (cidLine.indexOf('未锚定（no anchor）') < 0) {
      throw new Error('the CID line does not say 未锚定（no anchor）: ' + cidLine);
    }
    if (words.indexOf('unavailable') < 0) throw new Error('the block never states anchor_state=unavailable');
    if (words.indexOf('None') < 0) throw new Error('the block never states eth_tx_hash=None');
    /* no completion illusion anywhere: dictionaries, generated embed, keys.md */
    const blobs = {
      'zh.json': JSON.stringify(zh),
      'en.json': JSON.stringify(en),
      'embed.js': read(path.join(HERE, 'i18n', 'embed.js')),
      'keys.md': read(path.join(HERE, 'i18n', 'keys.md'))
    };
    const lies = ['正在上链', '已上链', '交易哈希已生成', '上链完成', '锚定成功'];
    Object.keys(blobs).forEach((label) => {
      lies.forEach((lie) => {
        if (blobs[label].indexOf(lie) >= 0) {
          throw new Error(label + ' carries a completion illusion: ' + lie);
        }
      });
    });
    const app = read(FILES.app);
    if (app.indexOf("var EXPORT_ANCHOR_STATE = 'unavailable';") < 0) {
      throw new Error('app.js does not use the backend constant for the anchor state');
    }
    const embed = blobs['embed.js'];
    const keys = blobs['keys.md'];
    leaves.forEach((leaf) => {
      if (embed.indexOf(leaf) < 0) throw new Error('i18n/embed.js is out of sync: run --sync-embed (' + leaf + ')');
      if (keys.indexOf('export.' + leaf) < 0) throw new Error('i18n/keys.md is out of sync: run --sync-keys (' + leaf + ')');
    });
    /* R8-6 (this round): the live export face exists from here on, so the old
     * "no R8-6 face" boundary is replaced by the honest-面 contract. The panel
     * may mount the hosts, but it may not write its own progress (no timer, no
     * width math) and it may not speak to the unmounted compliance route. */
    const panelNoComments = app.split('function renderExportPanel')[1].split('function renderAuditPanel')[0]
      .replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
    ['setInterval', 'setTimeout', '/api/compliance', 'style.width'].forEach((token) => {
      if (panelNoComments.indexOf(token) >= 0) {
        throw new Error('the export panel writes its own progress or calls an unmounted route: ' + token);
      }
    });
    if (panelNoComments.indexOf('export-progress-host') < 0) {
      throw new Error('the export panel lost the R8-6 progress host');
    }
    if (panelNoComments.indexOf('export-result-host') < 0) {
      throw new Error('the export panel lost the R8-6 result host');
    }
    if (panelNoComments.indexOf('TraceLensCompliance') < 0) {
      throw new Error('the export panel does not delegate progress/result to ext/compliance.js');
    }
    /* the bar itself lives in ext/compliance.js: it must be indeterminate by
     * construction - declared non-determinate, never given a percentage, and
     * carrying no timer or animation that could imply one. */
    const extFile = read(path.join(HERE, 'ext', 'compliance.js'))
      .replace(/\/\*[\s\S]*?\*\//g, ' ');
    if (extFile.indexOf("'data-determinate', 'false'") < 0) {
      throw new Error('ext/compliance.js does not declare the progress bar non-determinate');
    }
    if (extFile.indexOf("'data-percent', ''") < 0) {
      throw new Error('ext/compliance.js does not clear the percentage');
    }
    ['setInterval', 'setTimeout', 'style.width', 'aria-valuenow'].forEach((token) => {
      if (extFile.indexOf(token) >= 0) {
        throw new Error('ext/compliance.js can fake a progress value: ' + token);
      }
    });
    if (/[\u4e00-\u9fff]/.test(extFile)) {
      throw new Error('ext/compliance.js carries a CJK literal instead of a dictionary key');
    }
    return 'degraded state rendered from backend constants: anchor_state=unavailable, eth_tx_hash=none, '
      + 'cid_anchored=false, 4 dictionary lines, no completion copy in zh/en/embed/keys, '
      + 'R8-6 face mounted as indeterminate (no percent, no timer, no unmounted route)';
  });

  /* ---- R8-6 (REQ-008 6): the live export face. This check is deliberately
   * SOURCE-level, not DOM-level: the audit rail panel is not on the boot path
   * this rig drives, so a DOM assertion here would need a second rendering
   * harness (that is R8-7 territory). What the source proves is the part that
   * would otherwise be untested: the hosts exist, the panel delegates instead
   * of computing, and the bar can never report a number. The DOM shape of the
   * panel is pinned by tests/test_export_face.py against the same files. */
  check('R8-6 export face: hosts mounted, delegated to ext/compliance.js, bar can never report a number', async () => {
    const app = read(FILES.app);
    const ext = read(path.join(HERE, 'ext', 'compliance.js'));
    const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
    const panel = strip(app.split('function renderExportPanel')[1].split('function renderAuditPanel')[0]);
    ['export-progress-host', 'export-result-host', 'export-format', 'export-range'].forEach((hook) => {
      if (panel.indexOf(hook) < 0) throw new Error('the export panel lost its R8-6 host: ' + hook);
    });
    if (panel.indexOf('TraceLensCompliance') < 0) {
      throw new Error('the export panel does not delegate progress/result to ext/compliance.js');
    }
    ['setInterval', 'setTimeout', 'style.width', 'percent'].forEach((token) => {
      if (panel.indexOf(token) >= 0) {
        throw new Error('the panel computes its own progress: ' + token);
      }
    });
    const bar = strip(ext);
    if (bar.indexOf("'data-determinate', 'false'") < 0) {
      throw new Error('ext/compliance.js does not declare the bar non-determinate');
    }
    if (bar.indexOf("'data-percent', ''") < 0) {
      throw new Error('ext/compliance.js does not clear the percentage');
    }
    ['setInterval', 'setTimeout', 'requestAnimationFrame', 'style.width', 'aria-valuenow'].forEach((token) => {
      if (bar.indexOf(token) >= 0) throw new Error('the bar can fake a value: ' + token);
    });
    if (CJK_HAN.test(bar)) throw new Error('ext/compliance.js carries a CJK literal');
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    ['result.title', 'result.anchored', 'progress.busy', 'format.current'].forEach((leaf) => {
      if (!zh.export[leaf]) throw new Error('zh.json is missing export.' + leaf);
      if (!en.export[leaf]) throw new Error('en.json is missing export.' + leaf);
    });
    return 'export face mounted: 4 hosts present, delegated to ext/compliance.js, '
      + 'bar non-determinate with no percent/timer, copy in zh+en';
  });

  check('0.6.0 update channel: mounted in settings, browser mode draws no controls, no invented progress', async () => {
    const app = read(FILES.app);
    const ext = read(path.join(HERE, 'ext', 'update.js'));
    const css = read(path.join(HERE, 'ext', 'update.css'));
    const html = read(FILES.index);
    const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');

    // 1) the panel only hands over a host; the section itself lives in ext/
    const panel = strip(app.split('function renderSettingsPanel')[1].split('function auditErrorKey')[0]);
    if (panel.indexOf('settings-update-host') < 0) {
      throw new Error('the settings panel lost its update mount point');
    }
    if (panel.indexOf('TraceLensUpdate') < 0) {
      throw new Error('the settings panel does not delegate to ext/update.js');
    }

    // 2) both assets are actually loaded by the page
    ['ext/update.js'].forEach((src) => {
      if (html.indexOf(src) < 0) throw new Error('index.html does not load ' + src);
    });
    if (html.indexOf('ext/update.css') < 0) throw new Error('index.html does not load ext/update.css');

    // 3) the bar can never report a number (same rule as R8-6)
    const bar = strip(ext);
    ['setInterval', 'setTimeout', 'requestAnimationFrame', 'style.width', 'aria-valuenow'].forEach((token) => {
      if (bar.indexOf(token) >= 0) throw new Error('the update bar can fake a value: ' + token);
    });
    if (bar.indexOf('data-determinate') < 0 || bar.indexOf("'false'") < 0) {
      throw new Error('ext/update.js does not declare the bar non-determinate');
    }
    if (CJK_HAN.test(bar)) throw new Error('ext/update.js carries a CJK literal');

    // 4) browser mode must bail out before drawing any control
    const head = bar.split('function bridge()')[0].length; // eslint-disable-line no-unused-vars
    const browserGuard = bar.indexOf('update.hint.browser');
    const firstButton = bar.indexOf('update.action.check');
    if (browserGuard < 0) {
      throw new Error('browser mode has no explaining line');
    }
    if (firstButton < 0 || browserGuard > firstButton) {
      throw new Error('the browser-mode line is not emitted before the controls');
    }
    if (bar.indexOf('if (!api)') < 0) {
      throw new Error('the section does not branch on the desktop bridge');
    }

    // 5) every reason code the UI claims to know has a sentence in both languages
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);
    const block = /var KNOWN_REASONS = \{([\s\S]*?)\};/.exec(bar);
    if (!block) throw new Error('ext/update.js lost its reason table');
    const codes = (block[1].match(/([a-z_]+):\s*1/g) || []).map((t) => t.split(':')[0].trim());
    if (codes.length < 15) throw new Error('the reason table shrank to ' + codes.length + ' codes');
    codes.forEach((code) => {
      if (!zh.update['reason.' + code]) throw new Error('zh.json is missing update.reason.' + code);
      if (!en.update['reason.' + code]) throw new Error('en.json is missing update.reason.' + code);
    });
    if (!zh.update['reason.other'] || !en.update['reason.other']) {
      throw new Error('the fallback sentence update.reason.other is missing');
    }

    // 6) no motion: a moving stripe would read as measured progress
    //    (comments are stripped first - the header explains the rule by name)
    const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, ' ');
    ['@keyframes', 'animation', 'transition'].forEach((token) => {
      if (cssCode.indexOf(token) >= 0) throw new Error('update.css animates: ' + token);
    });

    // 7) only tokens that exist in :root (an undefined one falls back to transparent)
    // collect every custom property the design system declares anywhere in
    // styles.css (a `:root` selector also appears in a comment, so slicing on
    // the string ':root' would pick up the wrong block)
    const defined = new Set((read(FILES.styles).match(/--[a-z0-9-]+\s*:/g) || [])
      .map((t) => t.replace(/\s*:$/, '')));
    const used = new Set((css.match(/var\(--[a-z0-9-]+\)/g) || []).map((t) => t.slice(4, -1)));
    const missing = [...used].filter((t) => !defined.has(t));
    if (missing.length) throw new Error('update.css uses undefined tokens: ' + missing.join(', '));

    return 'update section mounted, delegated to ext/update.js, ' + codes.length
      + ' reason codes all have copy, browser mode explains itself, no animation, tokens defined';
  });

  check('feature centre: eight features, eight addressable pages, no second implementation', async () => {
    const html = read(FILES.index);
    const js = read(path.join(HERE, 'ext', 'workspaces.js'));
    const css = read(path.join(HERE, 'ext', 'workspaces.css'));
    const strip = (t) => t.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
    const code = strip(js);
    const zh = readJson(FILES.zh);
    const en = readJson(FILES.en);

    // 1) both assets are actually loaded by the page
    if (html.indexOf('ext/workspaces.js') < 0) throw new Error('index.html does not load ext/workspaces.js');
    if (html.indexOf('ext/workspaces.css') < 0) throw new Error('index.html does not load ext/workspaces.css');
    if (html.indexOf('ext/workspaces.js') < html.indexOf('ext/update.js')) {
      throw new Error('workspaces.js loads before the plugins it hosts');
    }

    // 2) eight features, each with a page id, a title and a description in both languages
    const block = /var FEATURES = \[([\s\S]*?)\n  \];/.exec(code);
    if (!block) throw new Error('ext/workspaces.js lost its feature table');
    const ids = (block[1].match(/id:\s*'([a-z]+)'/g) || []).map((t) => t.split("'")[1]);
    if (ids.length !== 8) throw new Error('the feature table has ' + ids.length + ' entries, expected 8');
    new Set(ids).forEach((id) => {
      if (!zh.workspaces['feature.' + id + '.title']) throw new Error('zh.json is missing workspaces.feature.' + id + '.title');
      if (!zh.workspaces['feature.' + id + '.desc']) throw new Error('zh.json is missing workspaces.feature.' + id + '.desc');
      if (!en.workspaces['feature.' + id + '.title']) throw new Error('en.json is missing workspaces.feature.' + id + '.title');
      if (!en.workspaces['feature.' + id + '.desc']) throw new Error('en.json is missing workspaces.feature.' + id + '.desc');
    });
    if (ids.length !== new Set(ids).size) throw new Error('a feature id is listed twice');

    // 3) no second implementation: the export page mounts app.js's own panel,
    //    and the plugin pages call the registered tab renderer.
    if (code.indexOf('renderExportPanel') < 0) throw new Error('the export page does not mount the audit export panel');
    if (code.indexOf('compliance/export') >= 0) throw new Error('ext/workspaces.js grew its own export request');
    if (code.indexOf('plugin.tab.render(mount, C)') < 0) throw new Error('plugin pages do not delegate to the registered tab');
    if (code.indexOf('plugin.tab.repaint(') < 0) throw new Error('plugin pages never repaint on a state change');
    if (code.indexOf('openRollbackPlan') < 0) throw new Error('the rollback page does not reuse the card plugin plan dialog');
    if (code.indexOf('renderEvidenceAndCausal') < 0) throw new Error('the evidence page does not reuse the card evidence renderer');

    // 4) no CJK literal and no invented numbers in the source
    if (CJK_HAN.test(code)) throw new Error('ext/workspaces.js carries a CJK literal');
    ['setInterval', 'setTimeout', 'requestAnimationFrame'].forEach((token) => {
      if (code.indexOf(token) >= 0) throw new Error('ext/workspaces.js runs a timer: ' + token);
    });

    // 5) every token the stylesheet uses is declared in the design system
    const defined = new Set((read(FILES.styles).match(/--[a-z0-9-]+\s*:/g) || [])
      .map((t) => t.replace(/\s*:$/, '')));
    const used = new Set((css.match(/var\(--[a-z0-9-]+\)/g) || []).map((t) => t.slice(4, -1)));
    const missing = [...used].filter((t) => !defined.has(t));
    if (missing.length) throw new Error('workspaces.css uses undefined tokens: ' + missing.join(', '));
    if (css.indexOf('@media (max-width: 820px)') < 0) throw new Error('the workspace has no narrow layout');

    // 6) runtime: build the page, open it, walk all eight routes, close it.
    const sandbox = makeSandbox(read(FILES.index), { innerWidth: 1400, innerHeight: 900 });
    sandbox.TraceLensDicts = { zh: zh, en: en };
    loadPageScripts(sandbox);
    loadScript(sandbox, FILES.extCore, 'ext/core.js');
    loadScript(sandbox, FILES.extCards, 'ext/cards.js');
    const ext = sandbox.TraceLensExt;
    if (!ext) throw new Error('ext/core.js did not expose TraceLensExt');
    loadScript(sandbox, path.join(HERE, 'ext', 'workspaces.js'), 'ext/workspaces.js');
    ext.boot();
    const doc = sandbox.document;
    /* the shim's document-level search is rooted at <html>; the workspace is
     * appended to <body>, so every query below is scoped to the body like the
     * other DOM checks in this file. */
    const body = doc.body;
    const W = sandbox.TraceLensWorkspaces;
    if (!W) throw new Error('ext/workspaces.js did not expose TraceLensWorkspaces');

    /* one live record, so the record-scoped pages (rollback / confidence /
     * evidence) render their real controls instead of the empty state. */
    const liveRecord = {
      id: 'act-workspace-0001',
      schema_version: '1.1',
      timestamp: '2026-01-01T00:00:00Z',
      action_type: 'tool_call',
      status: 'success',
      confidence: 0.42,
      confidence_source: { method: 'logprobs', samples: 8 },
      tool_name: 'shell',
      human_readable: { summary: 'workspace fixture record' }
    };
    sandbox.TraceLensUI.setApiTransport((endpoint) => {
      if (endpoint === 'health') {
        return { status: 200, data: { name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1' } };
      }
      if (endpoint !== 'records') return { status: 404, data: { code: 'ROUTE_NOT_FOUND' } };
      return { status: 200, data: { records: [liveRecord], meta: { count: 1, synthetic: true } } };
    });
    await sandbox.TraceLensUI.syncRecordsFromApi();

    const entry = body.querySelector('[data-hook="feature-center-entry"]');
    if (!entry) throw new Error('the topbar has no feature-centre entry');
    if (entry.getAttribute('aria-haspopup') !== 'dialog') throw new Error('the entry does not announce a dialog');

    /* app.js rebuilds the topbar from a dozen call sites that never go through
     * the render notify (language switch, settings apply, demo toggle...). All
     * of them start with clear(#topbar), so the entry must put itself back. */
    if (code.indexOf('MutationObserver') < 0) {
      throw new Error('nothing re-adds the entry after app.js rebuilds the topbar');
    }
    if (W.state().watching !== true) throw new Error('no observer is watching #topbar');

    const topbar = body.querySelector('#topbar');
    if (!topbar) throw new Error('the page lost #topbar');
    while (topbar.firstChild) topbar.removeChild(topbar.firstChild);
    if (body.querySelector('[data-hook="feature-center-entry"]')) {
      throw new Error('the topbar wipe did not take effect (test would be vacuous)');
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
    if (!body.querySelector('[data-hook="feature-center-entry"]')) {
      throw new Error('the entry does not survive a topbar rebuild');
    }

    W.open('overview');
    const host = doc.getElementById('feature-center');
    if (!host || host.hidden) throw new Error('opening the feature centre left it hidden');
    const cards = body.querySelectorAll('.workspace-feature-card');
    if (cards.length !== 8) throw new Error('the overview shows ' + cards.length + ' cards, expected 8');
    const navItems = body.querySelectorAll('.workspace__nav-item');
    if (navItems.length !== 8) {
      throw new Error('the navigation lists ' + navItems.length + ' features, expected 8');
    }

    const pages = [];
    ids.forEach((id) => {
      W.show(id);
      const page = body.querySelector('#feature-page-' + id);
      if (!page) throw new Error('route ' + id + ' rendered no page');
      if (sandbox.location.hash !== 'feature=' + id) {
        throw new Error('route ' + id + ' did not write its address (' + sandbox.location.hash + ')');
      }
      const title = body.querySelector('#workspace-title');
      if (!title || title.textContent !== zh.workspaces['feature.' + id + '.title']) {
        throw new Error('route ' + id + ' does not print its own title');
      }
      pages.push(page.getAttribute('data-feature-page'));
    });
    if (new Set(pages).size !== 8) throw new Error('two features share one page');

    // the export page hosts app.js's panel, not a copy of it
    W.show('compliance');
    const mounted = body.querySelector('[data-hook="workspace-export-panel"] [data-hook="export-buttons"]');
    if (!mounted) throw new Error('the compliance page does not mount the audit export panel');

    // the evidence page carries a real submit form, not a static description
    W.show('evidence');
    if (!body.querySelector('[data-hook="evidence-upload-form"]')) {
      throw new Error('the evidence page has no upload form');
    }
    if (!body.querySelector('[data-hook="evidence-upload-submit"]')) {
      throw new Error('the evidence form has no submit control');
    }

    // the confidence page bands from the record, and filters without re-estimating
    W.show('confidence');
    if (body.querySelectorAll('.workspace-stat').length !== 4) {
      throw new Error('the confidence page does not print its four counters');
    }

    W.close();
    if (!host.hidden) throw new Error('closing the feature centre left it visible');
    if (sandbox.location.hash !== '') throw new Error('closing did not restore the previous address');

    return 'eight features, eight pages, entry in the topbar, ' + ids.join('/')
      + '; export page mounts the audit panel, evidence page submits, tokens defined, narrow layout present';
  });

  return checks;
}

/* ---------------------------------------------------------------- *
 * 4.1 intervention loop helpers (shared by the t4.1 checks)
 * ---------------------------------------------------------------- */

const GOLDEN = readJson(path.join(HERE, 'verify', 'intervene-golden.json'));
const goldenCase = (name) => (GOLDEN.cases || []).find((c) => c.name === name) || null;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function ivBoot(search) { return boot({ search: search || '?probe=1' }); }

function ivBox(document, id) {
  return queryAll(document.body, '[data-hook="intervene"][data-card="' + id + '"]')[0] || null;
}

function ivButton(document, id, kind) {
  return queryAll(document.body,
    '[data-hook="intervene-button"][data-card="' + id + '"][data-kind="' + kind + '"]')[0] || null;
}

function ivFind(document, selector) { return queryAll(document.body, selector); }

function recordIn(ui, id) {
  return (ui.state.records || []).find((r) => String(r.id) === String(id)) || null;
}

/* 4.4 (t14): a stand-in for the browser EventSource - the same listener surface,
 * no socket. The rig drives frames by hand, so push, cursor replay and the
 * polling fallback are reproducible without a running server. */
class FakeSource {
  constructor(url, opts) {
    this.url = url;
    this.opts = opts || {};
    this.closed = false;
    this.handlers = Object.create(null);
    FakeSource.instances.push(this);
    FakeSource.last = this;
  }
  addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); }
  emit(name, data, id) {
    const frame = { type: name, data: JSON.stringify(data === undefined ? {} : data) };
    if (id !== undefined) frame.lastEventId = String(id);
    (this.handlers[name] || []).forEach((fn) => fn(frame));
  }
  open() { if (typeof this.onopen === 'function') this.onopen({ type: 'open' }); }
  fail() { if (typeof this.onerror === 'function') this.onerror({ type: 'error' }); }
  close() { this.closed = true; }
}
FakeSource.instances = [];
FakeSource.last = null;

const rtHealth = () => ({
  status: 200,
  headers: { 'x-request-id': 'req-health-rt' },
  data: {
    name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1',
    endpoints: ['/health', '/api/records', '/api/ingest/events', '/api/ingest/status', '/api/stream'],
    auth: { required: false },
    ingest: { enabled: true, tokens: 1, head_seq: 3 },
    stream: { endpoint: '/api/stream', event_types: ['hello', 'action.created'], subscribers: 0 }
  }
});

const rtRecord = (id, status, extra) => Object.assign({
  id: id,
  schema_version: '1.1',
  timestamp: '2026-01-01T00:00:00Z',
  action_type: 'tool_call',
  status: status,
  agent_id: 'sdk-agent',
  actor_id: 'sdk-client',
  workspace_id: 'ws-rt',
  session_id: 'sess-rt',
  intent: 'upload the report',
  result_summary: 'done',
  labels: [],
  latency_ms: 12,
  confidence: 0.7,
  requires_action: false,
  demo_data: false,
  synthetic: false,
  source: { kind: 'sdk', adapter: 'native', format_version: '1.1 via sdk' }
}, extra || {});

function transportFor(name, log) {
  const c = goldenCase(name);
  if (!c) throw new Error('golden case not found: ' + name);
  /* only decision requests are logged: the health probe is not a submission */
  return (endpoint, apiPath, body) => {
    if (endpoint === 'health') {
      return { status: 200, data: { name: 'TraceLens', status: 'ok', mode: 'local', schema_version: '1.1',
        auth: { required: false } }, headers: { 'x-request-id': 'req-health' } };
    }
    if (endpoint !== 'intervene') {
      return { status: 404, data: { code: 'ROUTE_NOT_FOUND', message: 'no such endpoint', detail: {}, request_id: 'req-x' } };
    }
    if (log) log.push({ endpoint, apiPath, body });
    return {
      status: 200,
      data: JSON.parse(JSON.stringify(c.result_record)),
      headers: { 'x-request-id': 'req-selfcheck', 'x-intervene-replay': 'false' }
    };
  };
}

function getIn(obj, dotted) {
  return dotted.split('.').reduce((acc, key) => (acc === null || acc === undefined ? undefined : acc[key]), obj);
}

function parityDiff(a, b, fields) {
  const bad = [];
  fields.forEach((f) => {
    const x = canonical(normalise(f, getIn(a, f)));
    const y = canonical(normalise(f, getIn(b, f)));
    if (x !== y) bad.push(f + ': browser ' + x + ' != python ' + y);
  });
  return bad;
}

/* key order is not part of the contract: compare JSON canonically */
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort()
      .map((k) => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  }
  return JSON.stringify(value === undefined ? null : value);
}

/* the change list is a set, not a sequence: compare it keyed by field name */
function normalise(fieldPath, value) {
  if (fieldPath === 'user_intervention.modified_fields' && Array.isArray(value)) {
    return value.map((item) => ({ name: item.name, before: item.before, after: item.after }))
      .sort((x, y) => String(x.name).localeCompare(String(y.name)));
  }
  return value;
}

/* Drive app.js's own transition on a throwaway instance. The parity check
 * compares that with the Python result, so "browser and server agree" is a
 * measured fact instead of a claim in a comment. */
function uilessApply(c) {
  const { ui } = ivBoot('?api=off');
  return ui.applyDecision(JSON.parse(JSON.stringify(c.input_record)), c.decision, {
    optionId: c.option_id, reason: c.reason, values: c.form_values, at: c.decided_at
  });
}

/* ---------------------------------------------------------------- *
 * runner
 * ---------------------------------------------------------------- */

async function run() {
  const checks = buildChecks();
  const results = [];
  let failed = 0;
  for (let i = 0; i < checks.length; i += 1) {
    const c = checks[i];
    let res;
    const started = Date.now();
    try {
      const detail = await c.fn();
      res = { index: i + 1, name: c.name, status: 'passed', detail: String(detail || ''), ms: Date.now() - started };
    } catch (err) {
      failed += 1;
      /* the stack is part of the report: a failing assertion should point at the
       * line, not just name a symptom */
      const stack = String((err && err.stack) || '');
      res = {
        index: i + 1, name: c.name, status: 'failed',
        detail: String(err && err.message ? err.message : err),
        stack: stack.split('\n').slice(1, 5).map((l) => l.trim()).join(' | '),
        ms: Date.now() - started
      };
    }
    results.push(res);
    const mark = res.status === 'passed' ? 'PASS' : 'FAIL';
    console.log('[' + mark + '] ' + String(res.index).padStart(2, '0') + '  ' + res.name);
    console.log('       ' + res.detail);
    if (res.stack) console.log('       at ' + res.stack);
  }
  const report = {
    task: 't6 web skeleton (3.1/3.2) + t8 intervention loop (4.1)',
    app: 'TraceLens',
    generated_at: new Date().toISOString(),
    node: process.version,
    total: results.length,
    passed: results.length - failed,
    failed,
    status: failed === 0 ? 'pass' : 'fail',
    checks: results
  };
  fs.writeFileSync(path.join(HERE, 'selfcheck-report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  console.log('');
  console.log('self-check: ' + report.passed + '/' + report.total + ' passed' + (failed ? ', ' + failed + ' FAILED' : '') + ' (report: web/selfcheck-report.json)');
  return failed === 0 ? 0 : 1;
}

const args = process.argv.slice(2);
const syncs = args.filter((a) => a.startsWith('--sync'));
/* importable surface: t10/t11 integration tests can boot the same shim DOM */
export { boot, bootExtLayer, queryAll, parseHtml, makeDocument, CJK, CJK_HAN, FILES, SAMPLES, dictKeys, readJson, run };
const isMain = process.argv[1]
  ? import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
  : false;
if (syncs.length) {
  syncs.forEach((flag) => {
    if (flag === '--sync-demo') {
      const r = syncDemo();
      const en = syncDemoEn();
      console.log('demo-data.js regenerated: ' + r.records + ' records, ' + r.bytes + ' bytes');
      console.log('demo-data-en.js regenerated: ' + en.records + ' records, ' + en.bytes + ' bytes');
    } else if (flag === '--sync-embed') {
      const r = syncEmbed();
      console.log('i18n/embed.js regenerated: zh groups=' + r.zh + ', en groups=' + r.en + ', ' + r.bytes + ' bytes');
    } else if (flag === '--sync-compare') {
      const r = syncCompare();
      console.log('compare-data.js regenerated: ' + r.records + ' records (run B variant), ' + r.bytes + ' bytes');
    } else if (flag === '--sync-keys') {
      const r = syncKeys();
      console.log('i18n/keys.md regenerated: ' + r.keys + ' keys in ' + r.domains + ' domains, ' + r.bytes + ' bytes');
    } else {
      console.error('unknown flag: ' + flag);
      process.exit(2);
    }
  });
  process.exit(0);
}
if (isMain) process.exitCode = await run();
