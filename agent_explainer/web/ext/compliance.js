/*
 * TraceLens 5.0 - REQ-008 R8-6: compliance export face (web/ext/compliance.js)
 * ------------------------------------------------------------------
 * This file used to be a five-line placeholder. It now owns the two pieces of
 * the export surface that the panel in app.js hands over:
 *
 *   1. the progress bar. The export is ONE GET that answers with a file. The
 *      server publishes no progress channel, so a percentage would be invented
 *      - and inventing it is exactly the "completion illusion" the display
 *      discipline forbids. The bar is therefore indeterminate by construction:
 *      `role="progressbar"` with `aria-valuetext` (a sentence, not a number),
 *      `data-determinate="false"`, and `data-percent=""` always. There is no
 *      timer, no synthetic 0->30->70 ramp and no width style written from here.
 *
 *   2. the result card. Every value it prints comes from the response headers
 *      app.js already captured (`format` / `rows` / `sha256` / `filename` /
 *      `fallback`). The page adds no number of its own: no "exported at"
 *      timestamp (the browser clock is not a fact source), no percentage.
 *      The degraded anchoring state sits INSIDE the same card, so an offline
 *      sha256 can never be read as "on chain": `anchor_state` is unavailable,
 *      `eth_tx_hash` does not exist, and the verification guide points at
 *      tools/verify_export.py instead of claiming the file was verified.
 *
 * Why no tab: the export live face belongs to the audit rail panel that already
 * exists (R8-1 pre-export gate, R8-5 degraded block). Registering a second tab
 * would create a second export entry point and a second place where numbers are
 * printed. This module registers without a tab on purpose and exposes only
 * render functions for the panel to call.
 *
 * All copy is a dictionary key (`export.*` domain). No CJK literal, no
 * sentence-shaped English literal lives in this file.
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext || typeof Ext.register !== 'function' || !Ext.ctx) return;

  var ctx = Ext.ctx;
  var VERSION = '1.0';

  var HOOK_PROGRESS = 'export-progress';
  var HOOK_RESULT = 'export-result';

  /* the only honest anchoring state in 0.6.0 (mirrors compliance.ANCHOR_STATE
   * and the R8-5 constants; selfcheck pins the copies against each other) */
  var ANCHOR_STATE = 'unavailable';
  var ETH_TX_HASH = 'none';

  function str(value) {
    if (value === null || value === undefined) return '';
    return String(value);
  }

  function clearHost(host) {
    while (host.firstChild) host.removeChild(host.firstChild);
  }

  /* ---------------------------------------------------------------- *
   * 1. progress bar - indeterminate, by construction
   * ---------------------------------------------------------------- */
  function renderProgress(host, opts) {
    if (!host) return null;
    clearHost(host);
    host.setAttribute('data-hook', HOOK_PROGRESS);
    host.setAttribute('data-determinate', 'false');
    host.setAttribute('data-percent', '');
    var busy = !!(opts && opts.busy);
    host.setAttribute('data-visible', busy ? 'true' : 'false');
    if (!busy) return null;

    var track = ctx.add(host, ctx.el('div', {
      class: 'export__progress',
      attrs: {
        'data-hook': HOOK_PROGRESS + '-bar',
        role: 'progressbar',
        'aria-label': ctx.t('export.progress.aria'),
        'aria-valuetext': ctx.t('export.progress.busy')
      }
    }));
    /* the moving stripe is decoration only: no width is set from here */
    ctx.add(track, ctx.el('span', {
      class: 'export__progress-fill',
      attrs: { 'data-hook': HOOK_PROGRESS + '-fill', 'aria-hidden': 'true' }
    }));
    ctx.add(host, ctx.el('p', {
      class: 'rail__hook-meta export__progress-note',
      text: ctx.t('export.progress.busy'),
      attrs: { 'data-hook': HOOK_PROGRESS + '-note' }
    }));
    return track;
  }

  /* ---------------------------------------------------------------- *
   * 2. result card - every field is a captured response value
   * ---------------------------------------------------------------- */
  function factRow(list, key, label, value, opts) {
    var row = ctx.add(list, ctx.el('div', {
      class: 'export__result-row',
      attrs: { 'data-hook': HOOK_RESULT + '-row', 'data-field': key }
    }));
    ctx.add(row, ctx.el('dt', { class: 'export__result-key', text: label }));
    var dd = ctx.add(row, ctx.el('dd', { class: 'export__result-val' }));
    if (opts && opts.code) {
      ctx.add(dd, ctx.el('code', {
        class: 'export__result-code',
        text: str(value),
        attrs: { 'data-hook': HOOK_RESULT + '-value', 'data-field': key }
      }));
    } else {
      ctx.add(dd, ctx.el('span', {
        class: 'export__result-text',
        text: str(value),
        attrs: { 'data-hook': HOOK_RESULT + '-value', 'data-field': key }
      }));
    }
    return row;
  }

  function renderResult(host, opts) {
    if (!host) return null;
    clearHost(host);
    host.setAttribute('data-hook', HOOK_RESULT);

    var last = opts && opts.last;
    var visible = !!(last && typeof last === 'object');
    host.setAttribute('data-visible', visible ? 'true' : 'false');
    /* the anchoring flags are on the card itself too: a reader who only looks
     * at the result must still learn the file is not anchored */
    host.setAttribute('data-anchor-state', ANCHOR_STATE);
    host.setAttribute('data-eth-tx-hash', ETH_TX_HASH);

    if (!visible) {
      host.setAttribute('data-format', '');
      host.setAttribute('data-rows', '');
      host.setAttribute('data-sha', '');
      host.setAttribute('data-file', '');
      host.setAttribute('data-fallback', '');
      ctx.add(host, ctx.el('p', {
        class: 'rail__hook-note export__result-empty',
        text: ctx.t('export.result.empty'),
        attrs: { 'data-hook': HOOK_RESULT + '-empty' }
      }));
      return null;
    }

    host.setAttribute('data-format', str(last.format));
    host.setAttribute('data-rows', (last.rows === null || last.rows === undefined) ? '' : str(last.rows));
    host.setAttribute('data-sha', str(last.sha256));
    host.setAttribute('data-file', str(last.filename));
    host.setAttribute('data-fallback', str(last.fallback));

    ctx.add(host, ctx.el('h4', {
      class: 'export__result-title',
      text: ctx.t('export.result.title'),
      attrs: { 'data-hook': HOOK_RESULT + '-title' }
    }));

    var list = ctx.add(host, ctx.el('dl', {
      class: 'export__result-list',
      attrs: { 'data-hook': HOOK_RESULT + '-facts' }
    }));
    factRow(list, 'format', ctx.t('export.result.format', { value: str(last.format) }), str(last.format), null);
    factRow(list, 'rows',
      ctx.t('export.result.rows', { value: (last.rows === null || last.rows === undefined) ? '?' : str(last.rows) }),
      (last.rows === null || last.rows === undefined) ? '?' : str(last.rows), null);
    factRow(list, 'sha', ctx.t('export.result.sha', { value: '' }), str(last.sha256), { code: true });
    factRow(list, 'file', ctx.t('export.result.file', { value: str(last.filename) }), str(last.filename), null);
    if (last.fallback) {
      factRow(list, 'fallback', ctx.t('export.result.fallback', { value: str(last.fallback) }), str(last.fallback), null);
    }

    /* degraded anchoring, same card, right under the fingerprint */
    ctx.add(host, ctx.el('p', {
      class: 'rail__hook-note export__result-anchor',
      text: ctx.t('export.result.anchored'),
      attrs: {
        'data-hook': HOOK_RESULT + '-anchor',
        'data-anchor-state': ANCHOR_STATE,
        'data-eth-tx-hash': ETH_TX_HASH
      }
    }));

    /* verification is a guide, never a claim: the file has not been verified
     * by this page, and "signature ok" is not "endorsement present" */
    ctx.add(host, ctx.el('p', {
      class: 'rail__hook-meta export__result-verify',
      text: ctx.t('export.result.verify'),
      attrs: { 'data-hook': HOOK_RESULT + '-verify' }
    }));

    return host;
  }

  Ext.register({
    id: 'compliance',
    version: VERSION,
    /* no tab on purpose - see the header note */
    onState: function () { /* the panel repaints itself; nothing is fetched here */ }
  });

  global.TraceLensCompliance = {
    version: VERSION,
    anchorState: ANCHOR_STATE,
    ethTxHash: ETH_TX_HASH,
    hooks: { progress: HOOK_PROGRESS, result: HOOK_RESULT },
    renderProgress: renderProgress,
    renderResult: renderResult,
    state: function () {
      return { anchorState: ANCHOR_STATE, ethTxHash: ETH_TX_HASH };
    }
  };
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
