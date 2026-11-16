/*
 * TraceLens 5.0 - REQ-004 R4-2: the multi-agent merged view (web/ext/groups.js)
 * ------------------------------------------------------------------
 * One tab, six zones, all read-side. Every number on this screen is the
 * server's own answer; the page adds no second fact source:
 *
 *   - merged timeline (core logic 1/3): the group's ActionRecords in the order
 *     `GroupService.aggregate` already sorted them (parsed absolute time,
 *     unparsable/missing stamps kept at the end and labelled, never guessed);
 *   - agent colour labels (core logic 3): the chip colour comes from
 *     `agents[].color_index`, i.e. the backend's `groups.color_index_for`
 *     (sha256(id) % PALETTE_SIZE). The palette below is a byte-for-byte copy of
 *     `groups.py:COLOR_PALETTE` and selfcheck.mjs pins the two tables against
 *     each other, so the page can never invent colours the backend disagrees
 *     with (task R4-2 §1.4: no second colour scheme);
 *   - avatar abbreviation in front of every step (core logic 5): a
 *     deterministic abbreviation of `agent_name` (fallback `agent_id`), pinned
 *     by the DOM self-test; a shared abbreviation is labelled, not renamed;
 *   - progress + total elapsed (core logic 4): the denominator is
 *     `progress.total` = the record count of this view as the response says,
 *     never the declared member count; the elapsed time is
 *     `window.total_ms` (the deterministic server window), never the browser
 *     clock;
 *   - filters (core logic 3/5): pick a group (list), tick agents off, flip the
 *     time order. After filtering the rows stay time-ordered, and records with
 *     no `group_id` stay in their own `ungrouped` column - they are never
 *     guessed into a group;
 *   - R4-1 aliases: `alias_fields`, `note` and every group's `updated_at_basis`
 *     are consumed here. `updated_at` is always shown together with the
 *     provenance label of the three honest ways it can be derived
 *     (`closed_at` / `last_event` / `created_at_fallback`); the response's own
 *     `note` is rendered verbatim, and the page never renders an alias as
 *     "the ledger was migrated" and never uses a browser/now() timestamp.
 *
 * Why this shape:
 *   - classic script, zero dependencies, works from file://: without a server
 *     the tab says it needs the server (groups.offline) and does not
 *     substitute demo data;
 *   - all copy is a dictionary key under the `groups` domain, no CJK literal
 *     and no sentence-shaped English literal lives in this file;
 *   - writes: the create form only calls the allowlisted `api/groups` POST.
 *     The baseline does not mount that route (R4-1 §9 / PM-VERDICT-R4-1 §1
 *     item 10), so the answer shown is the server's real code
 *     (ROUTE_NOT_FOUND) with the 10-agent limit stated from the same response
 *     contract - never a fake "created" state;
 *   - the panel never truncates agents: 10 members are shown, and an agent
 *     that appears in records without being a declared member is shown too
 *     (`aggregate` lists it), with an 11th agent's create attempt answered by
 *     the server's GROUP_AGENTS_EXCEEDED code.
 */
(function (global) {
  'use strict';

  var Ext = global.TraceLensExt;
  if (!Ext || typeof Ext.register !== 'function' || !Ext.ctx) return;

  var VERSION = '1.0';

  var EP_GROUPS = 'api/groups';

  /* --- the two tables the backend owns (pinned by selfcheck.mjs) --------- *
   * groups.py:COLOR_PALETTE (8 hex values, order stable) and
   * groups.py:MAX_AGENTS = 10. `color_index` in the aggregate response is
   * `int(sha256(agent_id), 16) % 8`, so with 10 agents two of them must share
   * a colour slot: the page says so out loud instead of inventing a 9th and
   * 10th colour (which would be a second colour scheme). */
  var GROUP_COLOR_PALETTE = [
    '#2563eb', '#0d9488', '#d97706', '#7c3aed',
    '#dc2626', '#0891b2', '#4d7c0f', '#be185d'
  ];
  var GROUP_PALETTE_SIZE = GROUP_COLOR_PALETTE.length;
  var GROUP_MAX_AGENTS = 10;

  /* server alias block (server.py:UPDATED_AT_BASIS_*): three named ways, and
   * the label for each lives in the dictionary so both languages explain it. */
  var UPDATED_AT_BASIS_KEYS = {
    closed_at: 'groups.alias.basis.closed_at',
    last_event: 'groups.alias.basis.last_event',
    created_at_fallback: 'groups.alias.basis.created_at_fallback'
  };
  /* server-side codes of the groups face (groups.py GroupError codes) */
  var GROUP_ERROR_KEYS = {
    GROUP_NOT_FOUND: 'groups.error.group_not_found',
    GROUP_FIELD_INVALID: 'groups.error.group_field_invalid',
    GROUP_AGENTS_EXCEEDED: 'groups.error.group_agents_exceeded',
    GROUP_CLOSED: 'groups.error.group_closed',
    GROUP_WRITE_FAILED: 'groups.error.group_write_failed'
  };
  /* record statuses the dictionary names. Written as a literal map, not built
   * by concatenation: selfcheck's orphan gate counts a quoted literal as a
   * real reference, a bare status-prefix plus a runtime value would only fake
   * the coverage. */
  var STATUS_LABEL_KEYS = {
    succeeded: 'groups.status.succeeded',
    failed: 'groups.status.failed',
    running: 'groups.status.running',
    observed: 'groups.status.observed',
    skipped: 'groups.status.skipped',
    pending_confirmation: 'groups.status.pending_confirmation',
    approved: 'groups.status.approved',
    modified: 'groups.status.modified',
    rejected: 'groups.status.rejected',
    expired: 'groups.status.expired'
  };
  var STATUS_OTHER_KEY = 'groups.status.other';
  var ABBR_SPLIT = /[\s\-_/\\.:,;|]+/;
  var STAMP_SHAPE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;

  var G = {
    listSeq: 0, detailSeq: 0,
    listLoaded: false, listLoading: false,
    list: [], listError: null, listNote: '', listAliasFields: null,
    facts: {},
    selected: '', detail: null, detailLoading: false, detailError: null,
    detailNote: '', detailAliasFields: null,
    hidden: {}, sortDesc: false,
    paintedLang: '',
    hosts: {}, body: null,
    createFeedback: null,
    renderMs: 0, renderedRows: 0, renderedAgents: 0,
    createSeq: 0
  };

  /* ---------------------------------------------------------------- *
   * 1. small helpers (all defensive: a missing field stays missing)
   * ---------------------------------------------------------------- */

  function str(v) { return v === undefined || v === null ? '' : String(v); }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isArr(v) { return Array.isArray(v); }
  function num(v) {
    /* an absent field stays absent: Number(null) and Number('') are both 0, and
     * printing that as a measurement would invent a number the backend never
     * answered with (a cost the server did not report is not a cost of 0) */
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    var n = typeof v === 'number' ? v : Number(v);
    return isFinite(n) ? n : null;
  }

  /* The page does not write a second time parser for sorting: the server's
   * aggregate order is the order. To decide whether a row can be labelled
   * "has a time" the page only checks the shape of the stamp - it never
   * converts it, never fills a gap, never assumes a zone. */
  function stampParsable(value) {
    var text = str(value).trim();
    return text && STAMP_SHAPE.test(text);
  }

  /* Deterministic avatar abbreviation: first letter of the first two tokens,
   * first two characters of a single token. `agent_name` first because that is
   * what the human reads; `agent_id` is the fallback the aggregate gives back
   * (`name` defaults to the id there). */
  function abbreviationOf(name, id) {
    var source = str(name || id).trim();
    if (!source) return '?';
    var tokens = source.split(ABBR_SPLIT).filter(function (t) { return t.length > 0; });
    var letters = tokens.length >= 2 ? tokens[0].charAt(0) + tokens[1].charAt(0)
      : source.slice(0, 2);
    return letters.toUpperCase();
  }

  function statusLabel(ctx, status) {
    var value = str(status);
    var key = Object.prototype.hasOwnProperty.call(STATUS_LABEL_KEYS, value)
      ? STATUS_LABEL_KEYS[value] : STATUS_OTHER_KEY;
    return ctx.t(key);
  }

  function elapsedText(ctx, ms) {
    var total = num(ms);
    if (total === null) return ctx.t('groups.resources.unknown');
    var hours = Math.floor(total / 3600000);
    var minutes = Math.floor((total % 3600000) / 60000);
    var seconds = Math.floor((total % 60000) / 1000);
    var rest = Math.round(total % 1000);
    var parts = [];
    if (hours) parts.push(ctx.t('groups.unit.hours', { value: hours }));
    if (minutes) parts.push(ctx.t('groups.unit.minutes', { value: minutes }));
    if (seconds) parts.push(ctx.t('groups.unit.seconds', { value: seconds }));
    if (!parts.length) parts.push(ctx.t('groups.unit.ms', { value: rest }));
    return parts.join(' ');
  }

  function aliasFieldsText(fields) {
    var list = isArr(fields) ? fields.map(str).filter(function (f) { return f; }) : [];
    return list.join(' / ');
  }

  function groupLabel(group) {
    return str(group && group.title) || str(group && (group.group_id || group.id));
  }

  function basisLabelKey(basis) {
    var key = UPDATED_AT_BASIS_KEYS[str(basis)];
    return key || '';
  }

  /* The three honest ways are printed with their label; a basis the server
   * invents later is printed verbatim (labelled as the raw value) so the page
   * never has to guess a meaning for an unknown string. */
  function basisText(ctx, basis) {
    var key = basisLabelKey(basis);
    return key ? ctx.t(key) : str(basis);
  }

  function aliasStrip(ctx, parent, note, fields) {
    var box = ctx.add(parent, ctx.el('div', {
      class: 'ext-grp__alias',
      dataset: { note: note ? 'server' : 'missing', fields: fields && fields.length ? fields.join(',') : '' }
    }));
    ctx.textNode(box, 'groups.alias.title', null, 'h4');
    var noteLine = ctx.add(box, ctx.el('p', { class: 'ext-note ext-grp__alias-note' }));
    if (str(note).trim()) {
      /* the server's own sentence, verbatim: the page must not reword the
       * provenance it is not the author of (0.4) */
      noteLine.textContent = str(note).trim();
    } else {
      ctx.textNode(noteLine, 'groups.alias.note.missing');
    }
    var fieldsLine = ctx.add(box, ctx.el('p', { class: 'ext-grp__alias-fields' }));
    var joined = aliasFieldsText(fields);
    if (joined) {
      ctx.textNode(fieldsLine, 'groups.alias.fields', { fields: joined });
    } else {
      ctx.textNode(fieldsLine, 'groups.alias.fields.missing');
    }
    return box;
  }

  function updatedLine(ctx, parent, group) {
    var updated = str(group && group.updated_at);
    var basis = str(group && group.updated_at_basis);
    var line = ctx.add(parent, ctx.el('p', {
      class: 'ext-grp__updated',
      dataset: { basis: basis || 'absent', updated: updated || 'absent' }
    }));
    if (!updated) {
      ctx.textNode(line, 'groups.alias.updated.absent');
      return line;
    }
    ctx.textNode(line, 'groups.detail.updated', { time: ctx.fmtTime(updated) });
    var hint = ctx.add(line, ctx.el('span', { class: 'ext-grp__updated-basis' }));
    ctx.textNode(hint, basis ? 'groups.alias.basis' : 'groups.alias.basis.absent',
      { basis: basisText(ctx, basis) });
    return line;
  }

  function colorChip(ctx, parent, index, id, shared) {
    var slot = num(index);
    var known = slot !== null && slot >= 0 && slot < GROUP_PALETTE_SIZE;
    var color = known ? GROUP_COLOR_PALETTE[slot] : '';
    var chip = ctx.add(parent, ctx.el('span', {
      class: 'ext-grp__chip' + (known ? '' : ' ext-grp__chip--none'),
      attrs: {
        'aria-hidden': 'true',
        style: color ? 'background:' + color : null
      },
      dataset: {
        index: slot === null ? '' : slot,
        agent: str(id),
        shared: shared ? String(shared) : '1'
      }
    }));
    return chip;
  }

  function avatar(ctx, parent, agent, sharedAbbr) {
    var abbr = abbreviationOf(agent && agent.name, agent && agent.id);
    var box = ctx.add(parent, ctx.el('span', {
      class: 'ext-grp__avatar',
      dataset: { agent: str(agent && agent.id), abbr: abbr, shared: sharedAbbr ? String(sharedAbbr) : '1' }
    }));
    colorChip(ctx, box, agent && agent.color_index, agent && agent.id, agent && agent.color_shared);
    var text = ctx.add(box, ctx.el('span', { class: 'ext-grp__avatar-abbr', text: abbr }));
    text.setAttribute('title', str((agent && agent.name) || (agent && agent.id)));
    return box;
  }

  function countColorShared(agents) {
    var byIndex = {};
    agents.forEach(function (agent) {
      var slot = num(agent.color_index);
      if (slot === null) return;
      byIndex[slot] = (byIndex[slot] || 0) + 1;
    });
    var sharedAgents = 0;
    Object.keys(byIndex).forEach(function (slot) {
      if (byIndex[slot] > 1) sharedAgents += byIndex[slot];
    });
    return sharedAgents;
  }

  function countAbbrShared(agents) {
    var byAbbr = {};
    agents.forEach(function (agent) {
      var abbr = abbreviationOf(agent.name, agent.id);
      byAbbr[abbr] = (byAbbr[abbr] || 0) + 1;
    });
    var shared = 0;
    Object.keys(byAbbr).forEach(function (abbr) {
      if (byAbbr[abbr] > 1) shared += byAbbr[abbr];
    });
    return shared;
  }

  function visibleAgents(agents) {
    return agents.filter(function (agent) { return !G.hidden[str(agent.id)]; });
  }

  function zone(ctx, parent, name, titleKey) {
    var section = ctx.add(parent, ctx.el('section', {
      class: 'ext-panel__section ext-grp__zone',
      dataset: { zone: name }
    }));
    var head = ctx.add(section, ctx.el('h4', { class: 'ext-panel__section-title' }));
    ctx.stamp(head, titleKey);
    return section;
  }

  /* ---------------------------------------------------------------- *
   * 2. reads: T18 list, T19 aggregate (one call per view, no fan-out)
   * ---------------------------------------------------------------- */

  function readList(ctx) {
    var seq = G.listSeq + 1;
    G.listSeq = seq;
    G.listLoading = true;
    G.listError = null;
    ctx.renderLoading(G.hosts.list);
    ctx.get(EP_GROUPS).then(function (res) {
      if (seq !== G.listSeq) return;
      G.listLoading = false;
      if (!res.ok) {
        G.list = [];
        G.listLoaded = false;
        G.listError = res;
        ctx.renderError(G.hosts.list, ctx.panelError(res, function () { readList(ctx); }));
        return;
      }
      var data = isObj(res.data) ? res.data : {};
      G.list = isArr(data.groups) ? data.groups.filter(isObj) : [];
      G.listLoaded = true;
      G.listNote = str(data.note);
      G.listAliasFields = isArr(data.alias_fields) ? data.alias_fields : null;
      G.facts = isObj(data.facts) ? data.facts : {};
      paintList(ctx);
      if (!G.selected && G.list.length) {
        G.selected = str(G.list[0].group_id || G.list[0].id);
      }
      if (G.selected) readDetail(ctx);
    });
  }

  function readDetail(ctx) {
    var id = str(G.selected);
    if (!id) return;
    var seq = G.detailSeq + 1;
    G.detailSeq = seq;
    G.detailLoading = true;
    G.detailError = null;
    ctx.renderLoading(G.hosts.timeline);
    ctx.get(EP_GROUPS + '/' + encodeURIComponent(id)).then(function (res) {
      if (seq !== G.detailSeq) return;
      G.detailLoading = false;
      if (!res.ok) {
        G.detail = null;
        G.detailError = res;
        ctx.renderError(G.hosts.timeline, ctx.panelError(res, function () { readDetail(ctx); }));
        paintHead(ctx);
        paintAgents(ctx);
        paintProgress(ctx);
        return;
      }
      var data = isObj(res.data) ? res.data : {};
      G.detail = data;
      G.detailNote = str(data.note);
      G.detailAliasFields = isArr(data.alias_fields) ? data.alias_fields : null;
      G.paintedLang = ctx.lang();
      paintHead(ctx);
      paintProgress(ctx);
      paintResources(ctx);
      paintAgents(ctx);
      paintTimeline(ctx);
    });
  }

  /* ---------------------------------------------------------------- *
   * 3. skeleton
   * ---------------------------------------------------------------- */

  function renderGroups(host, ctx) {
    G.paintedLang = ctx.lang();
    ctx.clear(host);
    var panel = ctx.add(host, ctx.el('section', { class: 'ext-grp' }));

    var head = ctx.add(panel, ctx.el('header', { class: 'ext-panel__head' }));
    var title = ctx.add(head, ctx.el('h3', { class: 'ext-panel__title' }));
    ctx.stamp(title, 'groups.title');
    var sub = ctx.textNode(head, 'groups.intro', null, 'p');
    sub.className = 'ext-panel__sub';

    G.hosts.alias = ctx.add(head, ctx.el('div', { class: 'ext-grp__alias-host' }));

    var listZone = zone(ctx, panel, 'list', 'groups.list.title');
    G.hosts.list = ctx.add(listZone, ctx.el('div', { class: 'ext-grp__list-host' }));

    var headZone = zone(ctx, panel, 'detail', 'groups.detail.group');
    G.hosts.head = ctx.add(headZone, ctx.el('div', { class: 'ext-grp__head-host' }));

    G.hosts.progress = ctx.add(zone(ctx, panel, 'progress', 'groups.progress.title'),
      ctx.el('div', { class: 'ext-grp__progress-host' }));
    G.hosts.resources = ctx.add(zone(ctx, panel, 'resources', 'groups.resources.title'),
      ctx.el('div', { class: 'ext-grp__resources-host' }));
    G.hosts.agents = ctx.add(zone(ctx, panel, 'agents', 'groups.agents.title'),
      ctx.el('div', { class: 'ext-grp__agents-host' }));

    var timelineZone = zone(ctx, panel, 'timeline', 'groups.timeline.title');
    G.hosts.filters = ctx.add(timelineZone, ctx.el('div', { class: 'ext-grp__filters-host' }));
    G.hosts.timeline = ctx.add(timelineZone, ctx.el('div', { class: 'ext-grp__timeline-host' }));

    G.hosts.ungrouped = ctx.add(zone(ctx, panel, 'ungrouped', 'groups.ungrouped.title'),
      ctx.el('div', { class: 'ext-grp__ungrouped-host' }));

    var createZone = zone(ctx, panel, 'create', 'groups.create.title');
    G.hosts.create = ctx.add(createZone, ctx.el('div', { class: 'ext-grp__create-host' }));
    paintCreate(ctx);

    paintUngrouped(ctx);
    readList(ctx);
  }

  function repaintGroups(host, ctx) {
    if (ctx.lang() === G.paintedLang) return;
    renderGroups(host, ctx);
  }

  /* ---------------------------------------------------------------- *
   * 4. paints (each zone is rebuilt from the response only)
   * ---------------------------------------------------------------- */

  function paintAlias(ctx) {
    var host = G.hosts.alias;
    if (!host) return;
    ctx.clear(host);
    var note = str(G.detailNote) || str(G.listNote);
    var fields = G.detailAliasFields || G.listAliasFields;
    aliasStrip(ctx, host, note, fields);
  }

  function paintHead(ctx) {
    var host = G.hosts.head;
    if (!host) return;
    ctx.clear(host);
    var group = isObj(G.detail) && isObj(G.detail.group) ? G.detail.group : null;
    if (!group) {
      var chosen = null;
      G.list.forEach(function (item) {
        if (str(item.group_id || item.id) === G.selected) chosen = item;
      });
      group = chosen;
    }
    if (!group) {
      ctx.renderEmpty(host, 'groups.detail.empty');
      paintAlias(ctx);
      return;
    }
    var row = ctx.add(host, ctx.el('div', { class: 'ext-grp__head-row' }));
    ctx.textNode(row, 'groups.detail.group', { title: groupLabel(group) }, 'strong');
    ctx.textNode(row, 'groups.detail.status', { status: str(group.status) }, 'span');
    var refresh = ctx.add(row, ctx.el('button', {
      class: 'trust-btn', attrs: { type: 'button' }, dataset: { act: 'refresh' }
    }));
    ctx.textNode(refresh, 'groups.detail.refresh');
    refresh.addEventListener('click', function () {
      readList(ctx);
      readDetail(ctx);
    });
    updatedLine(ctx, host, group);
    paintAlias(ctx);
  }

  function paintList(ctx) {
    var host = G.hosts.list;
    if (!host) return;
    ctx.clear(host);
    if (!G.list.length) {
      ctx.renderEmpty(host, serverSaysOffline() ? 'groups.offline' : 'groups.list.empty');
      paintAlias(ctx);
      return;
    }
    var count = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__count', dataset: { groups: G.list.length }
    }));
    ctx.textNode(count, 'groups.list.count', { count: G.list.length });

    var table = ctx.add(host, ctx.el('table', { class: 'ext-grp__table' }));
    var thead = ctx.add(table, ctx.el('thead'));
    var hrow = ctx.add(thead, ctx.el('tr'));
    /* header labels are the plain keys: the row cells below carry the
     * sentence keys with their parameters (groups.list.status / .created) */
    ['groups.list.title', 'groups.agents.title', 'groups.list.col.status',
      'groups.list.col.created', 'groups.list.col.updated', 'groups.list.select'].forEach(function (key) {
      var th = ctx.add(hrow, ctx.el('th', { attrs: { scope: 'col' } }));
      ctx.textNode(th, key);
    });
    var tbody = ctx.add(table, ctx.el('tbody'));
    G.list.forEach(function (group) {
      var id = str(group.group_id || group.id);
      var tr = ctx.add(tbody, ctx.el('tr', {
        dataset: { group: id, selected: id === G.selected ? 'true' : 'false' }
      }));
      var titleCell = ctx.add(tr, ctx.el('td', { text: groupLabel(group) }));
      if (id === G.selected) {
        var chosen = ctx.add(titleCell, ctx.el('span', {
          class: 'ext-grp__selected', dataset: { group: id }
        }));
        ctx.textNode(chosen, 'groups.list.selected', { title: groupLabel(group) });
      }
      var agents = isArr(group.agent_ids) ? group.agent_ids : [];
      var agentsCell = ctx.add(tr, ctx.el('td', { dataset: { agents: agents.length } }));
      ctx.textNode(agentsCell, 'groups.list.agents', { count: agents.length });
      ctx.add(tr, ctx.el('td', { text: ctx.t('groups.list.status', { status: str(group.status) }) }));
      var createdCell = ctx.add(tr, ctx.el('td', {
        dataset: { created: str(group.created_at) || 'absent' }
      }));
      if (str(group.created_at)) {
        ctx.textNode(createdCell, 'groups.list.created', { time: ctx.fmtTime(group.created_at) });
      }
      var timeCell = ctx.add(tr, ctx.el('td', {
        class: 'ext-grp__cell-updated',
        dataset: { basis: str(group.updated_at_basis) || 'absent', updated: str(group.updated_at) || 'absent' }
      }));
      var line = str(group.updated_at) ? ctx.fmtTime(group.updated_at) : '';
      timeCell.textContent = line;
      if (str(group.updated_at)) {
        ctx.textNode(timeCell, 'groups.alias.basis', { basis: basisText(ctx, group.updated_at_basis) }, 'span');
      }
      var actionCell = ctx.add(tr, ctx.el('td'));
      var open = ctx.add(actionCell, ctx.el('button', {
        class: 'trust-btn', attrs: { type: 'button' }, dataset: { act: 'select', group: id }
      }));
      ctx.textNode(open, 'groups.list.select');
      open.addEventListener('click', function () {
        G.selected = id;
        G.hidden = {};
        paintList(ctx);
        readDetail(ctx);
      });
    });
    paintAlias(ctx);
  }

  function paintProgress(ctx) {
    var host = G.hosts.progress;
    if (!host) return;
    ctx.clear(host);
    var data = isObj(G.detail) ? G.detail : null;
    if (!data) {
      ctx.renderEmpty(host, G.detailError ? 'groups.detail.empty' : 'groups.detail.reading');
      return;
    }
    var progress = isObj(data.progress) ? data.progress : {};
    var total = num(progress.total);
    var done = num(progress.done);
    var running = num(progress.running);
    var needs = num(progress.needs_me);
    var percent = num(progress.percent);
    var bar = ctx.add(host, ctx.el('div', {
      class: 'ext-grp__bar',
      dataset: {
        total: total === null ? '' : total,
        done: done === null ? '' : done,
        source: 'progress.total',
        denominator: 'records-in-view'
      },
      attrs: { role: 'progressbar', 'aria-valuemin': '0' }
    }));
    bar.setAttribute('aria-valuenow', done === null ? '0' : String(done));
    bar.setAttribute('aria-valuemax', total === null ? '0' : String(total));
    bar.setAttribute('aria-label', ctx.t('groups.progress.bar'));
    var fill = ctx.add(bar, ctx.el('span', { class: 'ext-grp__bar-fill' }));
    var ratio = total && total > 0 && done !== null ? Math.min(1, Math.max(0, done / total)) : 0;
    fill.setAttribute('style', 'width:' + (ratio * 100).toFixed(1) + '%');

    var label = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__progress-label',
      dataset: { total: total === null ? '' : total, done: done === null ? '' : done }
    }));
    ctx.textNode(label, 'groups.progress.label', {
      done: done === null ? '?' : done,
      running: running === null ? '?' : running,
      needs_me: needs === null ? '?' : needs,
      total: total === null ? '?' : total
    });
    /* groups.py:_percent answers with an integer 0-100 (not a 0-1 ratio): the
     * page prints it as it is, no second rounding */
    if (percent !== null) {
      ctx.textNode(host, 'groups.progress.percent', { percent: percent }, 'span');
    }
    /* the honest denominator line (task §2.3): the boss rejected a denominator
     * built from a declared member count, so the page states where the number
     * came from, in words, next to the bar. */
    var note = ctx.add(host, ctx.el('p', { class: 'ext-note ext-grp__note' }));
    ctx.textNode(note, 'groups.progress.note');
    var memberCount = isArr(isObj(data.group) ? data.group.agent_ids : null)
      ? data.group.agent_ids.length : 0;
    var members = ctx.add(host, ctx.el('p', {
      class: 'ext-note ext-grp__note ext-grp__note--members',
      dataset: { members: memberCount, total: total === null ? '' : total }
    }));
    ctx.textNode(members, 'groups.progress.members_not_denominator', { members: memberCount });
  }

  function paintResources(ctx) {
    var host = G.hosts.resources;
    if (!host) return;
    ctx.clear(host);
    var data = isObj(G.detail) ? G.detail : null;
    if (!data) {
      ctx.renderEmpty(host, 'groups.detail.reading');
      return;
    }
    var win = isObj(data.window) ? data.window : {};
    var resources = isObj(data.resources) ? data.resources : {};
    var started = str(win.started_at);
    var ended = str(win.ended_at);
    /* a window with no endpoints has no duration: the server answers 0 for an
     * empty group, and printing that as a measured elapsed time would be a
     * number the backend never measured, so the line says "not provided" */
    var totalMs = (started && ended) ? num(win.total_ms) : null;
    var line = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__elapsed',
      dataset: {
        ms: totalMs === null ? '' : totalMs,
        source: 'window.total_ms',
        window: (started && ended) ? 'known' : 'absent'
      }
    }));
    ctx.textNode(line, 'groups.resources.elapsed', { value: elapsedText(ctx, totalMs) });
    var windowLine = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__window',
      dataset: { started: started || 'absent', ended: ended || 'absent' }
    }));
    if (started && ended) {
      ctx.textNode(windowLine, 'groups.resources.window', {
        start: ctx.fmtTime(started), end: ctx.fmtTime(ended)
      });
    } else {
      ctx.textNode(windowLine, 'groups.resources.window.absent');
    }
    var note = ctx.add(host, ctx.el('p', { class: 'ext-note ext-grp__note' }));
    ctx.textNode(note, 'groups.resources.elapsed_note');
    var cost = num(resources.cost);
    var costLine = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__cost',
      dataset: { cost: cost === null ? '' : cost, reported: cost === null ? 'false' : 'true' }
    }));
    if (cost === null) {
      ctx.textNode(costLine, 'groups.resources.cost', { value: ctx.t('groups.resources.unknown') });
    } else {
      ctx.textNode(costLine, 'groups.resources.cost', { value: ctx.fmtNumber(cost, 2) });
    }
  }

  /* The stuck summary is its own fact from the response (data.stuck), so it is
   * painted even when the group has no agent rows: an empty stuck list is what
   * the server answered, and hiding it would leave the reader guessing. */
  function paintStuckSummary(ctx, host, data, stuckIds) {
    var ids = isArr(stuckIds) ? stuckIds : (isArr(data.stuck) ? data.stuck : []);
    var box = ctx.add(host, ctx.el('div', {
      class: 'ext-grp__stuck',
      dataset: { count: ids.length }
    }));
    if (ids.length) {
      ctx.textNode(box, 'groups.agents.stuck_count', { count: ids.length }, 'p');
      var title = ctx.add(box, ctx.el('p', { class: 'ext-grp__stuck-title' }));
      ctx.textNode(title, 'groups.stuck.title');
      var since = ctx.add(box, ctx.el('p', { class: 'ext-grp__stuck-since' }));
      ctx.textNode(since, 'groups.stuck.since', {
        time: ctx.fmtTime(str(isObj(data.window) ? data.window.ended_at : ''))
      });
    } else {
      ctx.textNode(box, 'groups.stuck.empty', null, 'p');
    }
  }

  function paintAgents(ctx) {
    var host = G.hosts.agents;
    if (!host) return;
    ctx.clear(host);
    var data = isObj(G.detail) ? G.detail : null;
    if (!data) {
      ctx.renderEmpty(host, 'groups.detail.reading');
      return;
    }
    var agents = isArr(data.agents) ? data.agents.filter(isObj) : [];
    if (!agents.length) {
      ctx.renderEmpty(host, 'groups.agents.none');
      paintStuckSummary(ctx, host, data, isArr(data.stuck) ? data.stuck : []);
      return;
    }
    var sharedColor = countColorShared(agents);
    var sharedAbbr = countAbbrShared(agents);
    G.sharedColor = sharedColor;
    G.sharedAbbr = sharedAbbr;
    var list = ctx.add(host, ctx.el('ul', {
      class: 'ext-grp__agents',
      dataset: {
        agents: agents.length,
        max: GROUP_MAX_AGENTS,
        'palette-size': GROUP_PALETTE_SIZE,
        'color-shared': sharedColor,
        'abbr-shared': sharedAbbr
      }
    }));
    agents.forEach(function (agent) {
      var item = ctx.add(list, ctx.el('li', {
        class: 'ext-grp__agent',
        dataset: {
          agent: str(agent.id),
          index: num(agent.color_index) === null ? '' : num(agent.color_index),
          count: num(agent.count) === null ? '' : num(agent.count),
          stuck: agent.stuck === true ? 'true' : 'false',
          hidden: G.hidden[str(agent.id)] ? 'true' : 'false'
        }
      }));
      avatar(ctx, item, {
        id: agent.id, name: agent.name, color_index: agent.color_index,
        color_shared: sharedColor > 0 ? sharedColor : 0
      }, sharedAbbr > 0 ? sharedAbbr : 0);
      var name = ctx.add(item, ctx.el('span', { class: 'ext-grp__agent-name', text: str(agent.name || agent.id) }));
      name.setAttribute('title', str(agent.id));
      var count = ctx.add(item, ctx.el('span', { class: 'ext-grp__agent-count' }));
      ctx.textNode(count, 'groups.agents.count', { count: num(agent.count) === null ? '?' : num(agent.count) });
      if (agent.stuck === true) {
        var badge = ctx.add(item, ctx.el('span', { class: 'ext-grp__badge ext-grp__badge--stuck' }));
        ctx.textNode(badge, 'groups.agents.stuck');
      }
    });
    var stuckIds = isArr(data.stuck) ? data.stuck : [];
    paintStuckSummary(ctx, host, data, stuckIds);
    if (sharedColor > 0) {
      var colorNote = ctx.add(host, ctx.el('p', {
        class: 'ext-note ext-grp__note ext-grp__note--color',
        dataset: { shared: sharedColor, size: GROUP_PALETTE_SIZE, max: GROUP_MAX_AGENTS }
      }));
      ctx.textNode(colorNote, 'groups.agents.color_shared', {
        count: sharedColor, size: GROUP_PALETTE_SIZE
      });
    }
    if (sharedAbbr > 0) {
      var abbrNote = ctx.add(host, ctx.el('p', {
        class: 'ext-note ext-grp__note ext-grp__note--abbr',
        dataset: { shared: sharedAbbr }
      }));
      ctx.textNode(abbrNote, 'groups.agents.abbr_shared', { count: sharedAbbr });
    }
    var maxLine = ctx.add(host, ctx.el('p', {
      class: 'ext-note ext-grp__note ext-grp__note--max',
      dataset: { shown: agents.length, max: GROUP_MAX_AGENTS, truncated: 'false' }
    }));
    ctx.textNode(maxLine, 'groups.agents.max_note', { shown: agents.length, max: GROUP_MAX_AGENTS });
  }

  function paintFilters(ctx, agents) {
    var host = G.hosts.filters;
    if (!host) return;
    ctx.clear(host);
    var bar = ctx.add(host, ctx.el('div', { class: 'ext-grp__filters' }));
    var label = ctx.add(bar, ctx.el('span', { class: 'ext-grp__filters-label' }));
    ctx.textNode(label, 'groups.timeline.filter');
    var hint = ctx.add(bar, ctx.el('span', { class: 'ext-note ext-grp__filters-hint' }));
    ctx.textNode(hint, 'groups.timeline.filter.hint');

    var all = ctx.add(bar, ctx.el('button', {
      class: 'trust-btn', attrs: { type: 'button' }, dataset: { act: 'filter-all' }
    }));
    ctx.textNode(all, 'groups.timeline.filter.all');
    all.addEventListener('click', function () {
      G.hidden = {};
      paintAgents(ctx);
      paintTimeline(ctx);
    });
    var none = ctx.add(bar, ctx.el('button', {
      class: 'trust-btn', attrs: { type: 'button' }, dataset: { act: 'filter-none' }
    }));
    ctx.textNode(none, 'groups.timeline.filter.none');
    none.addEventListener('click', function () {
      G.hidden = {};
      agents.forEach(function (agent) { G.hidden[str(agent.id)] = true; });
      paintAgents(ctx);
      paintTimeline(ctx);
    });

    var agentBar = ctx.add(bar, ctx.el('div', { class: 'ext-grp__filter-agents' }));
    agents.forEach(function (agent) {
      var on = !G.hidden[str(agent.id)];
      var toggle = ctx.add(agentBar, ctx.el('button', {
        class: 'ext-grp__filter-btn' + (on ? ' is-on' : ''),
        attrs: { type: 'button', 'aria-pressed': on ? 'true' : 'false' },
        dataset: { act: 'toggle-agent', agent: str(agent.id), on: on ? 'true' : 'false' }
      }));
      colorChip(ctx, toggle, agent.color_index, agent.id, 0);
      ctx.add(toggle, ctx.el('span', {
        class: 'ext-grp__filter-abbr',
        text: abbreviationOf(agent.name, agent.id)
      }));
      ctx.add(toggle, ctx.el('span', { class: 'ext-grp__filter-name', text: str(agent.name || agent.id) }));
      toggle.addEventListener('click', function () {
        var key = str(agent.id);
        if (G.hidden[key]) delete G.hidden[key]; else G.hidden[key] = true;
        paintAgents(ctx);
        paintTimeline(ctx);
      });
    });

    var sort = ctx.add(bar, ctx.el('button', {
      class: 'trust-btn',
      attrs: { type: 'button', 'aria-label': ctx.t('groups.timeline.sort.aria') },
      dataset: { act: 'sort', order: G.sortDesc ? 'desc' : 'asc' }
    }));
    ctx.textNode(sort, G.sortDesc ? 'groups.timeline.sort.desc' : 'groups.timeline.sort.asc');
    sort.addEventListener('click', function () {
      G.sortDesc = !G.sortDesc;
      paintTimeline(ctx);
    });
  }

  function orderedRows(records) {
    /* rows keep the server's ascending order; the desc switch reverses the
     * parsable part only and keeps the stamp-less rows at the end, so a
     * timestamp the server could not read is never given a position it did
     * not earn (and never a guessed time). */
    var parsable = [];
    var bad = [];
    records.forEach(function (record) {
      if (stampParsable(record.timestamp)) parsable.push(record); else bad.push(record);
    });
    if (G.sortDesc) parsable.reverse();
    return { rows: parsable.concat(bad), badCount: bad.length };
  }

  function paintTimeline(ctx) {
    var host = G.hosts.timeline;
    if (!host) return;
    var started = Date.now();
    ctx.clear(host);
    var data = isObj(G.detail) ? G.detail : null;
    if (!data) {
      ctx.renderEmpty(host, G.detailError ? 'groups.detail.empty' : 'groups.detail.reading');
      G.renderMs = Date.now() - started;
      return;
    }
    var allAgents = isArr(data.agents) ? data.agents.filter(isObj) : [];
    var records = isArr(data.records) ? data.records.filter(isObj) : [];
    if (!records.length) {
      ctx.renderEmpty(host, 'groups.timeline.empty');
      G.renderMs = Date.now() - started;
      G.renderedRows = 0;
      return;
    }
    paintFilters(ctx, allAgents);

    var shown = visibleAgents(allAgents);
    var visibleIds = {};
    shown.forEach(function (agent) { visibleIds[str(agent.id)] = true; });
    var ordered = orderedRows(records);
    var rows = ordered.rows.filter(function (record) {
      var id = str(record.agent_id);
      /* a record whose agent is not in the agents list at all stays visible:
       * hiding it would silently drop an agent from the screen */
      if (!allAgents.length) return true;
      var known = false;
      allAgents.forEach(function (agent) { if (str(agent.id) === id) known = true; });
      return known ? !!visibleIds[id] : true;
    });
    var hiddenAgents = allAgents.length - shown.length;
    /* computed once per paint, not per row: the colour slot per agent, and how
     * many agents share a colour slot / an abbreviation */
    var indexById = {};
    var abbrCounts = {};
    allAgents.forEach(function (agent) {
      indexById[str(agent.id)] = agent.color_index;
      var abbr = abbreviationOf(agent.name, agent.id);
      abbrCounts[abbr] = (abbrCounts[abbr] || 0) + 1;
    });
    var sharedColor = countColorShared(allAgents);

    var table = ctx.add(host, ctx.el('table', {
      class: 'ext-grp__timeline',
      dataset: {
        rows: rows.length,
        total: records.length,
        'bad-time': ordered.badCount,
        order: G.sortDesc ? 'desc' : 'asc',
        'render-ms': 0
      }
    }));
    var thead = ctx.add(table, ctx.el('thead'));
    var hrow = ctx.add(thead, ctx.el('tr'));
    ['groups.timeline.col.time', 'groups.timeline.col.agent', 'groups.timeline.col.action',
      'groups.timeline.col.status'].forEach(function (key) {
      var th = ctx.add(hrow, ctx.el('th', { attrs: { scope: 'col' } }));
      ctx.textNode(th, key);
    });
    var tbody = ctx.add(table, ctx.el('tbody'));
    rows.forEach(function (record) {
      var parsable = stampParsable(record.timestamp);
      var agentId = str(record.agent_id);
      var agentName = str(record.agent_name);
      var tr = ctx.add(tbody, ctx.el('tr', {
        dataset: {
          step: str(record.id),
          agent: agentId,
          time: parsable ? str(record.timestamp) : 'absent',
          'bad-time': parsable ? 'false' : 'true'
        }
      }));
      var timeCell = ctx.add(tr, ctx.el('td', {
        class: 'ext-grp__cell-time',
        dataset: { parsable: parsable ? 'true' : 'false' }
      }));
      if (parsable) {
        timeCell.textContent = ctx.fmtTime(record.timestamp);
      } else {
        ctx.textNode(timeCell, 'groups.timeline.bad_time');
      }
      var agentCell = ctx.add(tr, ctx.el('td', { class: 'ext-grp__cell-agent' }));
      var abbr = abbreviationOf(agentName, agentId);
      avatar(ctx, agentCell, {
        id: agentId, name: agentName,
        color_index: Object.prototype.hasOwnProperty.call(indexById, agentId)
          ? indexById[agentId] : null,
        color_shared: sharedColor
      }, abbrCounts[abbr] > 1 ? abbrCounts[abbr] : 0);
      ctx.add(agentCell, ctx.el('span', { class: 'ext-grp__agent-id', text: agentId }));
      ctx.add(tr, ctx.el('td', { class: 'ext-grp__cell-action', text: str(record.action_type) }));
      var statusCell = ctx.add(tr, ctx.el('td', {
        class: 'ext-grp__cell-status',
        dataset: { status: str(record.status) || 'absent' }
      }));
      statusCell.textContent = statusLabel(ctx, record.status);
    });

    var count = ctx.add(host, ctx.el('p', {
      class: 'ext-grp__timeline-count',
      dataset: { shown: rows.length, total: records.length, 'bad-time': ordered.badCount }
    }));
    ctx.textNode(count, 'groups.timeline.count', { shown: rows.length, total: records.length });
    if (hiddenAgents > 0) {
      ctx.textNode(host, 'groups.timeline.hidden', { count: hiddenAgents }, 'p');
    }
    if (ordered.badCount > 0) {
      var badNote = ctx.add(host, ctx.el('p', {
        class: 'ext-note ext-grp__note ext-grp__note--badtime',
        dataset: { count: ordered.badCount }
      }));
      ctx.textNode(badNote, 'groups.timeline.bad_time');
    }
    if (!rows.length) {
      /* the filter bar and the table stay on screen so the user can tick an
       * agent back on: the honest statement is a line, not a wiped panel */
      var none = ctx.add(host, ctx.el('p', {
        class: 'ext-grp__timeline-none', dataset: { state: 'no-match' }
      }));
      ctx.textNode(none, 'groups.timeline.no_match');
    }
    G.renderMs = Date.now() - started;
    G.renderedRows = rows.length;
    G.renderedAgents = allAgents.length;
    table.setAttribute('data-render-ms', String(G.renderMs));
    count.setAttribute('data-render-ms', String(G.renderMs));
    count.setAttribute('data-agents', String(allAgents.length));
  }

  /* Records with no `group_id` are a column of their own. The page never
   * guesses a group for them (groups.py file head: the only source of truth is
   * record["group_id"]), so they are listed, counted, and kept out of every
   * group view. */
  function ungroupedRecords(ctx) {
    var records = [];
    try { records = ctx.records() || []; } catch (e) { records = []; }
    return records.filter(function (record) {
      if (!isObj(record)) return false;
      var value = record.group_id;
      return value === undefined || value === null || str(value).trim() === '';
    });
  }

  function paintUngrouped(ctx) {
    var host = G.hosts.ungrouped;
    if (!host) return;
    ctx.clear(host);
    var ungrouped = ungroupedRecords(ctx);
    var box = ctx.add(host, ctx.el('div', {
      class: 'ext-grp__ungrouped',
      dataset: { count: ungrouped.length, guessed: 'false' }
    }));
    ctx.textNode(box, 'groups.ungrouped.count', { count: ungrouped.length }, 'p');
    ctx.textNode(box, 'groups.ungrouped.note', null, 'p');
    if (!ungrouped.length) return;
    var list = ctx.add(host, ctx.el('ul', { class: 'ext-grp__ungrouped-list' }));
    ungrouped.slice(0, 50).forEach(function (record) {
      var item = ctx.add(list, ctx.el('li', {
        dataset: { step: str(record.id), agent: str(record.agent_id) }
      }));
      ctx.add(item, ctx.el('span', {
        class: 'ext-grp__ungrouped-abbr',
        text: abbreviationOf(record.agent_name, record.agent_id)
      }));
      ctx.add(item, ctx.el('span', { class: 'ext-grp__ungrouped-id', text: str(record.id) }));
      ctx.add(item, ctx.el('span', {
        class: 'ext-grp__ungrouped-time',
        text: stampParsable(record.timestamp) ? ctx.fmtTime(record.timestamp) : ctx.t('groups.timeline.bad_time')
      }));
    });
    if (ungrouped.length > 50) {
      ctx.textNode(host, 'groups.ungrouped.more', { count: ungrouped.length - 50 }, 'p');
    }
  }

  function paintCreate(ctx) {
    var host = G.hosts.create;
    if (!host) return;
    ctx.clear(host);
    var form = ctx.add(host, ctx.el('form', {
      class: 'ext-form ext-grp__create',
      attrs: { 'aria-label': ctx.t('groups.create.title') }
    }));
    var titleInput = ctx.add(form, ctx.el('input', {
      class: 'ext-input', attrs: { type: 'text', name: 'title', 'data-field': 'create-title' }
    }));
    ctx.textNode(form, 'groups.create.field.title', null, 'label');
    var objectiveInput = ctx.add(form, ctx.el('input', {
      class: 'ext-input', attrs: { type: 'text', name: 'objective', 'data-field': 'create-objective' }
    }));
    ctx.textNode(form, 'groups.create.field.objective', null, 'label');
    var agentsInput = ctx.add(form, ctx.el('textarea', {
      class: 'ext-input', attrs: { name: 'agents', 'data-field': 'create-agents' }
    }));
    ctx.textNode(form, 'groups.create.field.agents', null, 'label');

    var submit = ctx.add(form, ctx.el('button', {
      class: 'trust-btn trust-btn--primary',
      attrs: { type: 'submit' }, dataset: { act: 'create-submit' }
    }));
    ctx.textNode(submit, 'groups.create.submit');

    var feedback = ctx.add(form, ctx.el('p', {
      class: 'ext-grp__create-feedback', dataset: { state: 'idle' }
    }));
    var unavailable = ctx.add(form, ctx.el('p', {
      class: 'ext-note ext-grp__note ext-grp__note--unavailable',
      dataset: { route: 'POST /api/groups', mounted: 'false' }
    }));
    ctx.textNode(unavailable, 'groups.create.unavailable');

    function agentList() {
      var raw = str(agentsInput.value);
      return raw.split(/[,\n]+/).map(function (item) { return item.trim(); })
        .filter(function (item) { return item.length > 0; });
    }

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var title = str(titleInput.value).trim();
      var agents = agentList();
      if (!title || !agents.length) {
        feedback.setAttribute('data-state', 'invalid');
        ctx.clear(feedback);
        ctx.textNode(feedback, 'groups.create.required');
        return;
      }
      if (agents.length > GROUP_MAX_AGENTS) {
        feedback.setAttribute('data-state', 'over-max');
        ctx.clear(feedback);
        ctx.textNode(feedback, 'groups.create.max', { count: agents.length, max: GROUP_MAX_AGENTS });
        return;
      }
      feedback.setAttribute('data-state', 'sending');
      ctx.clear(feedback);
      ctx.renderLoading(feedback);
      var seq = G.createSeq + 1;
      G.createSeq = seq;
      ctx.post(EP_GROUPS, {
        title: title,
        objective: str(objectiveInput.value).trim(),
        agent_ids: agents
      }).then(function (res) {
        if (seq !== G.createSeq) return;
        ctx.clear(feedback);
        if (res.ok) {
          feedback.setAttribute('data-state', 'ok');
          ctx.textNode(feedback, 'groups.create.done');
          readList(ctx);
          return;
        }
        var code = str(res.code);
        feedback.setAttribute('data-state', 'refused:' + code);
        var line = ctx.add(feedback, ctx.el('span', { class: 'ext-grp__create-code', text: code }));
        void line;
        if (GROUP_ERROR_KEYS[code]) {
          ctx.textNode(feedback, GROUP_ERROR_KEYS[code], null, 'span');
        }
        if (code === 'ROUTE_NOT_FOUND') {
          ctx.textNode(feedback, 'groups.create.unavailable', null, 'span');
        }
      });
    });
  }

  function serverSaysOffline() {
    try {
      var mode = typeof Ext.ctx.UI.apiMode === 'function' ? Ext.ctx.UI.apiMode() : null;
      return !!(mode && mode.mode === 'offline');
    } catch (e) {
      return false;
    }
  }

  /* ---------------------------------------------------------------- *
   * 5. registration
   * ---------------------------------------------------------------- */

  Ext.register({
    id: 'groups',
    version: VERSION,
    tab: {
      titleKey: 'groups.tab',
      order: 30,
      permission: 'records.read',
      render: renderGroups,
      repaint: repaintGroups
    },
    onState: function (state, ctx) {
      /* a new record payload changes the ungrouped column: repaint only that
       * zone, nothing is fetched a second time */
      if (G.hosts.ungrouped) paintUngrouped(ctx);
      void state;
    }
  });

  /* the rig reads these numbers straight off the DOM, no internal access */
  global.TraceLensGroups = {
    version: VERSION,
    palette: GROUP_COLOR_PALETTE.slice(),
    paletteSize: GROUP_PALETTE_SIZE,
    maxAgents: GROUP_MAX_AGENTS,
    abbreviation: abbreviationOf,
    state: function () {
      return {
        selected: G.selected,
        groups: G.list.length,
        agents: G.renderedAgents,
        rows: G.renderedRows,
        renderMs: G.renderMs,
        hidden: Object.keys(G.hidden).length,
        order: G.sortDesc ? 'desc' : 'asc'
      };
    }
  };
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
