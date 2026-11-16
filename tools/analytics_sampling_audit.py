#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REQ-006 R6-2 / B6-1：预聚合账本的**抽样核对**（可复现、逐条对账、误差 0）。

被审对象（不另写第二套聚合实现，全部复用既有单点）：

* 账本侧：``analytics.dashboard(records, agg=FeaturePaths(...).agg_ledger)`` 写出的
  ``agg_daily.jsonl`` 合并行（``AggLedger.load()["rows"]``，同键后读胜出）；
* 事实侧：``analytics_agg.recompute_rows``（聚合定义的**唯一实现**）与
  ``analytics_agg.record_to_deltas``（**一条记录的贡献**单点）；
* 指标侧：``analytics._assemble_metrics``（指标装配单点），并把冻结的旧算法
  ``analytics._dashboard_materialized`` 当第三方参照，把流式路径
  ``analytics.dashboard(records)``（``reason=no_ledger_wired``）当第四方参照。

对账口径（TASK-R6-2 的铁律：比的是「账本追加行 vs 原始记录重算」，不是「算法 vs 算法」）：

1. **行级零误差**：账本合并行逐键 == ``recompute_rows(全量)``（用 ``_row_values`` 比）。
2. **逐条贡献零误差（抽样主证）**：对抽中的每条记录 r，
   ``recompute_rows(pop) − recompute_rows(pop \\ {r})`` 的整数增量
   **逐字段等于** ``record_to_deltas(r)``，且这些键在账本合并行里都真在、值都对得上。
3. **修正（冲账）行参与对账**：改一条记录再跑一次仪表盘 → 补偿行落账，
   合并行仍 == 新全量复算，旧键留**归零行**（历史行一字不改）。
4. **回退核对**：账本缺失 / 一次性迭代器 / agent 过滤 / 自定义 salt / 链被篡改 →
   如实回退并说明原因码，且回退数字与流式扫描逐键一致。

抽样**可复现**：固定种子（默认 ``20261115``），``random.Random(seed).sample``；
样本清单（序号 + record id）随报告输出，任何人换台机器都能重跑出同一批样本。

用法::

    python -X utf8 tools/analytics_sampling_audit.py                 # 100 例抽样 + 产物
    python -X utf8 tools/analytics_sampling_audit.py --census        # 全量逐条（不止抽样）
    python -X utf8 tools/analytics_sampling_audit.py --seed 7 --sample 50

产物（默认写到仓库 ``_tmp/``）：``r62_sampling_audit.json``（机器可读全量证据）、
``r62_sampling_audit.md``（逐条对账表，REPORT-R6-2 直接引用）。
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import analytics, analytics_agg, trace_adapter        # noqa: E402
from agent_explainer.analytics_agg import _consumed_of, _row_values        # noqa: E402
from agent_explainer.feature_store import FeaturePaths                     # noqa: E402

#: 抽样种子（写死 = 可复现；报告里登记的种子必须与此一致）
DEFAULT_SEED = 20261115
#: 任务书下限：抽样条数不得小于 50（低于此直接拒绝，不静默缩小审计）
MIN_SAMPLE = 50
DEFAULT_SAMPLE = 100
#: 演示语料只有 ~36 条真记录，补一批**确定性合成**记录把母体做到 ~1000+，
#: 抽样才是「从大母体里抽」，不是「把演示集全量当样本」。合成记录同样带
#: synthetic/demo 标注，且只进临时账本，绝不写 var/。
SYNTH_TARGET = 1000

METRIC_KEYS = ("total_tasks", "total_interventions", "intervention_rate",
               "avg_approval_seconds", "avg_approval_ms", "trust_health", "users",
               "trend", "heatmap", "top_parameters", "top_rejected", "approved",
               "rejected")
#: ``range``/``granularity`` 由 ``dashboard`` 补，不是 ``_assemble_metrics`` 的输出，
#: 只在两条公开路径（账本 / 流式）之间比对。
TAIL_KEYS = ("range", "granularity")
VALUE_FIELDS = ("count", "intervention_count", "sum_micros", "n")


# --------------------------------------------------------------------------- #
# 母体：真仓演示语料 + 确定性合成记录
# --------------------------------------------------------------------------- #

def _read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))     # 显式 utf-8，不走 PowerShell 管道


def _jsonl_records(path: pathlib.Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            inner = obj.get("record")
            out.append(inner if isinstance(inner, dict) else obj)
    return out


def repo_records() -> tuple[list[dict], dict]:
    """真仓演示语料（只读）：samples/zh+en、samples/traces/*、data/records.jsonl。"""
    census: dict = {}
    raw: list[dict] = []
    for name in ("action_records.json", "action_records_en.json"):
        p = ROOT / "samples" / name
        if p.exists():
            doc = _read_json(p)
            recs = doc.get("records") if isinstance(doc, dict) else doc
            keep = [r for r in (recs or []) if isinstance(r, dict)]
            raw += keep
            census[name] = len(keep)
    for name in ("langfuse.json", "mcp_events.json", "otel_genai.json"):
        p = ROOT / "samples" / "traces" / name
        if not p.exists():
            continue
        try:
            conv = trace_adapter.convert(_read_json(p))
        except Exception as exc:                                   # 源不可用 → 如实登记，不猜
            census[f"traces/{name}"] = f"skipped:{type(exc).__name__}"
            continue
        keep = [r for r in conv if isinstance(r, dict)]
        raw += keep
        census[f"traces/{name}"] = len(keep)
    for rel in (("data", "records.jsonl"), ("var", "records.jsonl")):
        recs = _jsonl_records(ROOT.joinpath(*rel))
        raw += recs
        census["/".join(rel)] = len(recs)
    return raw, census


def synthetic_records(count: int, *, seed: int) -> list[dict]:
    """确定性合成记录（同 seed 同序列）：覆盖干预/热力图/延迟/多用户/边界各维。"""
    rng = random.Random(f"synth:{seed}")
    tools = ("web.search", "file.write", "shell.exec", "email.send", "db.query", "image.edit")
    params = ("path", "query", "recipient", "limit", "mode", "timeout")
    decisions = ("approve", "modify", "reject", "cooperate", "auto_approved", "")
    weights = (34, 20, 9, 10, 7, 20)
    tz = _dt.timezone(_dt.timedelta(hours=8))
    base = _dt.datetime(2026, 11, 1, 8, 0, 0, tzinfo=tz)
    out: list[dict] = []
    for i in range(count):
        moment = base + _dt.timedelta(days=rng.randrange(0, 15),
                                      seconds=rng.randrange(0, 8 * 3600),
                                      milliseconds=rng.randrange(0, 1000))
        dec = rng.choices(decisions, weights=weights)[0]
        tool = rng.choice(tools)
        ui = None
        if dec:
            lag = rng.randrange(0, 900)
            decided = moment + _dt.timedelta(seconds=lag)
            if rng.random() < 0.03:                     # 诚实边界：时间倒挂 → 延迟不计
                decided = moment - _dt.timedelta(seconds=60)
            ui = {"decision": dec,
                  "decided_by": f"user-{rng.randrange(1, 7):02d}",
                  "decided_at": decided.isoformat(),
                  "modified_fields": [{"name": n, "old": "a", "new": "b"}
                                      for n in rng.sample(params, rng.randrange(1, 4))]
                                      if dec == "modify" else []}
        out.append({"schema_version": "1.1", "id": f"synth-{i:05d}",
                    "timestamp": moment.isoformat(), "agent_id": "synth-agent",
                    "actor_id": "synth", "action_type": "tool_call",
                    "tool_used": {"name": tool}, "user_intervention": ui,
                    "synthetic": True, "demo_data": True,
                    "source": {"kind": "demo", "adapter": "r62_sampling_audit"}})
    return out


def dirty_fixtures() -> list:
    """脏样本（**诚实排除**用，绝不参与补偿）：坏时间戳 / 缺时间戳 / 非 dict / 无参数名。

    口径来自 ``record_to_deltas``：时间戳解析不出来 → 整条不计；``modified_fields`` 里
    没有 ``name`` 的项 → 热力图行不生成，但**本体行照计**（记录确实发生过）。
    """
    good = synthetic_records(3, seed=7)
    return [
        {"id": "dirty-bad-ts", "timestamp": "not-a-time",
         "tool_used": {"name": "web.search"}, "user_intervention": None},
        {"id": "dirty-no-ts", "tool_used": {"name": "web.search"}},
        "not-a-dict-record",
        None,
        {"id": "dirty-field-no-name", "timestamp": good[1]["timestamp"],
         "tool_used": {"name": "file.write"},
         "user_intervention": {"decision": "modify", "decided_by": "user-02",
                               "decided_at": good[1]["timestamp"],
                               "modified_fields": [{"old": "a", "new": "b"}]}},
    ]


def out_of_range_fixture() -> dict:
    """区间外记录：读侧按区间过滤，不得混进看板数字。"""
    return {"id": "dirty-out-of-range", "timestamp": "2099-01-01T00:00:00+08:00",
            "tool_used": {"name": "file.write"},
            "user_intervention": {"decision": "reject", "decided_by": "user-01",
                                  "decided_at": "2099-01-01T00:10:00+08:00"}}


def dedupe(records: list) -> list:
    """按 ``record_key`` 去重（保留首次出现）：zh/en/records.jsonl 的 id 是同一条记录。"""
    seen: set = set()
    out: list = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        key = analytics_agg.record_key(rec)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def build_population(*, seed: int = DEFAULT_SEED, synth: int = SYNTH_TARGET,
                     with_dirty: bool = True) -> tuple[list, dict]:
    real, census = repo_records()
    pop = dedupe(real + synthetic_records(synth, seed=seed))
    census["real_unique"] = len(dedupe(real))
    census["synth"] = synth
    if with_dirty:
        pop = pop + dirty_fixtures()
        census["dirty"] = len(dirty_fixtures())
    census["population"] = len(pop)
    return pop, census


def sample_records(population: list, *, seed: int = DEFAULT_SEED,
                   n: int = DEFAULT_SAMPLE) -> list:
    """固定种子抽样 → ``[(母体下标, 记录), ...]``（同 seed 同结果 = 可复现）。"""
    if n < MIN_SAMPLE:
        raise ValueError(f"抽样条数不得小于 {MIN_SAMPLE}（任务书 B6-1 下限），收到 {n}")
    idx = list(range(len(population)))
    picked = random.Random(f"sample:{seed}").sample(idx, min(n, len(population)))
    return [(i, population[i]) for i in sorted(picked)]


# --------------------------------------------------------------------------- #
# 逐条对账
# --------------------------------------------------------------------------- #

def _int_of(row, field: str) -> int:
    if field in ("count", "intervention_count"):
        return int(row.get(field) or 0)
    agg = row.get("approval_latency_agg") or {}
    return int(agg.get("sum_micros" if field == "sum_micros" else "n") or 0)


def _delta_value(delta: dict, field: str) -> int:
    if field in ("count", "intervention_count"):
        return int(delta.get(field) or 0)
    agg = delta.get("approval_latency_agg") or {}
    return int(agg.get("sum_micros" if field == "sum_micros" else "n") or 0)


def audit_contribution(population: list, index: int, ledger_rows: dict, full: dict) -> dict:
    """一条记录的贡献对账：移除恒等式 + 账本行存在性与值一致。"""
    rec = population[index]
    if not isinstance(rec, dict):
        return {"index": index, "id": "«not-a-dict»", "keys": 0,
                "excluded": "not-a-dict", "row_ok": True, "identity_ok": True}
    key = analytics_agg.record_key(rec)
    deltas = analytics_agg.record_to_deltas(rec, salt="")
    if not deltas:
        return {"index": index, "id": str(rec.get("id") or key), "keys": 0,
                "excluded": "no-delta（时间戳解析不出来，与仪表盘同口径不计数）",
                "row_ok": True, "identity_ok": True}
    wo = analytics_agg.recompute_rows(population[:index] + population[index + 1:], salt="")
    row_ok = identity_ok = True
    d_count = d_int = d_micros = d_n = 0
    notes: list[str] = []
    for delta in deltas:
        k = delta["key"]
        if k not in ledger_rows:
            row_ok = False
            notes.append(f"账本缺键 {k}")
            continue
        if _row_values(ledger_rows[k]) != _row_values(full[k]):
            row_ok = False
            notes.append(f"账本行 != 复算行 {k}")
        base = wo.get(k)
        for field in VALUE_FIELDS:
            got = _int_of(full[k], field) - (_int_of(base, field) if base else 0)
            want = _delta_value(delta, field)
            if got != want:
                identity_ok = False
                notes.append(f"{k}.{field} 移除恒等 {got} != 贡献 {want}")
        for user in delta["users"]:
            in_full = user in (full[k].get("users") or [])
            in_wo = bool(base) and user in (base.get("users") or [])
            if not in_full or (not in_wo and not in_full):
                identity_ok = False
                notes.append(f"{k}.users 用户 {user} 非本条带入")
        d_count += delta["count"]
        d_int += delta["intervention_count"]
        d_micros += delta["approval_latency_agg"]["sum_micros"]
        d_n += delta["approval_latency_agg"]["n"]
    dec = str(analytics._decision(rec).get("decision") or "—")
    return {"index": index, "id": str(rec.get("id") or key),
            "date": str(deltas[0]["date"]), "tool": str(deltas[0]["tool"]),
            "decision": dec, "keys": len(deltas),
            "d_count": d_count, "d_intervention": d_int,
            "d_sum_micros": d_micros, "d_n": d_n,
            "row_ok": row_ok, "identity_ok": identity_ok,
            "notes": notes[:3]}


def audit(*, seed: int = DEFAULT_SEED, sample_n: int = DEFAULT_SAMPLE,
          synth: int = SYNTH_TARGET, workdir=None, census: bool = False) -> dict:
    """跑完整审计 → 机器可读报告（``ok`` 为真才算通过）。"""
    if workdir is None:                                     # 临时目录只走 tests/scratch.py
        from scratch import scratch_dir
        workdir = scratch_dir("r62_sampling_audit")
    workdir = pathlib.Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ledger_path = FeaturePaths(workdir).agg_ledger

    pop, pop_census = build_population(seed=seed, synth=synth)
    picked = sample_records(pop, seed=seed, n=sample_n)

    t0 = time.perf_counter()
    first = analytics.dashboard(pop, agg=ledger_path)
    t_bootstrap = (time.perf_counter() - t0) * 1000.0
    snap = analytics_agg.AggLedger(ledger_path).load()
    verify = analytics_agg.AggLedger(ledger_path).verify()
    rows = snap["rows"]
    full = analytics_agg.recompute_rows(pop, salt="")

    errors: list[str] = []
    checks: dict[str, bool] = {}

    # 1) 行级零误差：账本合并行 == 全量复算（唯一实现）
    row_diff = [k for k in set(rows) | set(full)
                if _row_values(rows.get(k, {})) != _row_values(full.get(k, {}))]
    checks["ledger_rows_equal_recompute"] = not row_diff
    if row_diff:
        errors.append(f"行级误差 {len(row_diff)} 键：{row_diff[:3]}")
    checks["ledger_chain_ok"] = bool(verify["ok"])
    if not verify["ok"]:
        errors.append(f"链校验不过：{verify['problems'][:2]}")
    checks["ledger_pre_aggregated"] = bool(first.get("pre_aggregated"))
    if not first.get("pre_aggregated"):
        errors.append(f"未走预聚合：{first.get('agg')}")
    checks["ledger_bootstrap_first"] = int(first["agg"].get("newly_aggregated") or 0) > 0

    # 水位线：每条被消费的记录都以摘要进快照
    consumed = _consumed_of(snap["state"])
    wm_ok = all(consumed.get(analytics_agg.record_key(r)) == analytics_agg.record_digest(r)
                for r in pop if isinstance(r, dict))
    checks["watermark_covers_all_records"] = wm_ok
    if not wm_ok:
        errors.append("水位线未覆盖全部记录（键/摘要不符）")

    # 2) 逐条贡献对账（抽样 = 主证；--census = 全量逐条）
    targets = list(enumerate(pop)) if census else picked
    per_record: list[dict] = []
    mismatch = 0
    for _pos, _rec in targets:
        item = audit_contribution(pop, _pos, rows, full)
        per_record.append(item)
        if not (item["row_ok"] and item["identity_ok"]):
            mismatch += 1
    checks["per_record_zero_error"] = mismatch == 0
    if mismatch:
        errors.append(f"逐条对账误差 {mismatch} 条")

    # 3) 指标装配零误差：账本路径 == 装配单点 == 流式路径 == 旧算法参照
    counters = analytics_agg.rows_to_counters(rows, start=_dt.date(2000, 1, 1),
                                              end=_dt.date.max, granularity="day")
    metrics_from_rows = analytics._assemble_metrics(counters)
    stream = analytics.dashboard(pop)
    materialized = analytics._dashboard_materialized(pop)
    metric_diff: list[str] = []
    for key in METRIC_KEYS:
        a, b = first.get(key), metrics_from_rows.get(key)
        c, d = stream.get(key), materialized.get(key)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            metric_diff.append(f"{key}: 账本行装配 {a!r} != 直接装配 {b!r}")
        if json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True):
            metric_diff.append(f"{key}: 账本 {a!r} != 流式 {c!r}")
        if json.dumps(a, sort_keys=True) != json.dumps(d, sort_keys=True):
            metric_diff.append(f"{key}: 账本 {a!r} != 旧算法参照 {d!r}")
    checks["metrics_zero_error"] = not metric_diff
    for key in TAIL_KEYS:
        a, c = first.get(key), stream.get(key)
        if json.dumps(a, sort_keys=True) != json.dumps(c, sort_keys=True):
            metric_diff.append(f"{key}: 账本 {a!r} != 流式 {c!r}")
    checks["tail_keys_zero_error"] = not metric_diff
    if metric_diff:
        errors.extend(metric_diff[:6])

    # 4a) 修正（冲账）行：专用小母体，键唯一 → 旧键必留**归零行**（大母体键太密，旧键不消失）
    comp_dir = workdir / "comp"
    comp_dir.mkdir(parents=True, exist_ok=True)
    comp_ledger = FeaturePaths(comp_dir).agg_ledger
    comp_ledger.parent.mkdir(parents=True, exist_ok=True)
    solo = {"schema_version": "1.1", "id": "r62-solo", "timestamp": "2026-11-02T09:00:00.000+08:00",
            "tool_used": {"name": "r62.solo.tool"}, "agent_id": "a", "actor_id": "s",
            "user_intervention": {"decision": "approve", "decided_by": "user-01",
                                  "decided_at": "2026-11-02T09:01:00.000+08:00",
                                  "modified_fields": [{"name": "limit", "old": "1", "new": "2"}]}}
    analytics.dashboard([solo], agg=comp_ledger)
    moved = copy.deepcopy(solo)
    moved["user_intervention"] = dict(solo["user_intervention"], decision="reject")
    analytics.dashboard([moved], agg=comp_ledger)
    rows_comp = analytics_agg.AggLedger(comp_ledger).load()["rows"]
    full_comp = analytics_agg.recompute_rows([moved], salt="")
    old_keys = {d["key"] for d in analytics_agg.record_to_deltas(solo, salt="")}
    zeroed = [k for k in old_keys
              if k in rows_comp and _row_values(rows_comp[k]) == (0, 0, 0, 0, [])]
    checks["compensation_zero_rows_present"] = (
        len(zeroed) == len(old_keys)
        and all(_row_values(rows_comp.get(k, {})) == _row_values(full_comp.get(k, {}))
                for k in set(rows_comp) | set(full_comp)))
    if not checks["compensation_zero_rows_present"]:
        errors.append(f"旧键没有留下归零行（{len(zeroed)}/{len(old_keys)}）")

    # 4) 修正（冲账）行参与对账：改一条记录 → 补偿行落账 → 合并行仍等于新全量复算
    target_pos = picked[0][0]
    original = pop[target_pos]
    mutated = json.loads(json.dumps(original))
    dec_now = str(analytics._decision(original).get("decision") or "")
    moment = analytics._ts(original)
    stamp = (moment.isoformat() if moment
             else "2026-11-09T12:00:00.000+08:00")
    mutated["user_intervention"] = {"decision": "reject" if dec_now != "reject" else "approve",
                                    "decided_by": "user-99", "decided_at": stamp}
    mutated["timestamp"] = "2026-11-14T23:59:59.000+08:00"
    pop2 = list(pop)
    pop2[target_pos] = mutated
    second = analytics.dashboard(pop2, agg=ledger_path)
    rows2 = analytics_agg.AggLedger(ledger_path).load()["rows"]
    full2 = analytics_agg.recompute_rows(pop2, salt="")
    comp_diff = [k for k in set(rows2) | set(full2)
                 if _row_values(rows2.get(k, {})) != _row_values(full2.get(k, {}))]
    old_keys = {d["key"] for d in analytics_agg.record_to_deltas(original, salt="")}
    zeroed = [k for k in old_keys
              if k in rows2 and _row_values(rows2[k]) == (0, 0, 0, 0, [])]
    checks["compensation_rows_equal_recompute"] = not comp_diff
    checks["compensation_appended"] = int(second["agg"].get("compensation_keys") or 0) > 0
    checks["ledger_grows_after_compensation"] = (analytics_agg.AggLedger(ledger_path)
                                                 .verify()["lines"] > verify["lines"])
    if comp_diff:
        errors.append(f"补偿后行级误差 {len(comp_diff)} 键：{comp_diff[:3]}")
    if not checks["compensation_appended"]:
        errors.append("未追加补偿行（compensation_keys=0）")
    pop2[target_pos] = original                                # 还原：审计不改母体

    # 5) 回退核对（诚实回退，绝不消费被污染的账本）
    fallbacks: dict[str, str] = {}
    empty_dir = workdir / "no-ledger"
    empty_dir.mkdir(parents=True, exist_ok=True)
    r = analytics.dashboard([], agg=FeaturePaths(empty_dir).agg_ledger)
    fallbacks["ledger_missing_no_records"] = str(r["agg"].get("reason"))
    r = analytics.dashboard(iter(pop), agg=ledger_path)
    fallbacks["input_not_rerunnable"] = str(r["agg"].get("reason"))
    r = analytics.dashboard(pop, agg=ledger_path, agent="synth-agent")
    fallbacks["agent_filter"] = str(r["agg"].get("reason"))
    r = analytics.dashboard(pop, agg=ledger_path, salt="pepper")
    fallbacks["custom_salt"] = str(r["agg"].get("reason"))
    tampered = workdir / "tampered"
    tampered.mkdir(parents=True, exist_ok=True)
    tp = FeaturePaths(tampered).agg_ledger
    tp.parent.mkdir(parents=True, exist_ok=True)
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    broken = list(lines)
    pos = min(3, len(broken) - 1)
    broken[pos] = broken[pos][:-3] + "9zz"
    tp.write_text("\n".join(broken) + "\n", encoding="utf-8")
    r = analytics.dashboard(pop, agg=tp)
    fallbacks["chain_tampered"] = str(r["agg"].get("reason"))
    checks["fallback_reasons_honest"] = all(fallbacks[c] == c for c in fallbacks)
    if not checks["fallback_reasons_honest"]:
        errors.append(f"回退原因码不诚实：{fallbacks}")

    # 6) 脏记录诚实排除（绝不"补"一条、也绝不把脏数据折算成 0 计数）
    #    6a 时间戳解析不出来 / 非 dict → 整条不进任何键（与仪表盘"算不出就不计"同口径）
    unparsed = [fx for fx in dirty_fixtures()
                if not isinstance(fx, dict)
                or fx.get("id") in ("dirty-bad-ts", "dirty-no-ts")]
    rows_dirty = analytics_agg.recompute_rows(pop + unparsed, salt="")
    shifted = [k for k in set(rows_dirty) | set(full)
               if _row_values(rows_dirty.get(k, {})) != _row_values(full.get(k, {}))]
    checks["unparsable_records_excluded"] = not shifted
    if shifted:
        errors.append(f"解析不出的记录参与了计数（{len(shifted)} 键）：{shifted[:3]}")

    #    6b 热力图行只认"有名字的参数"：无 name 的 modified_field 不生成热力图行，本体行照计
    fx = next(f for f in dirty_fixtures() if isinstance(f, dict)
              and f.get("id") == "dirty-field-no-name")
    deltas_fx = analytics_agg.record_to_deltas(fx, salt="")
    rows_fx = analytics_agg.recompute_rows(pop + [fx], salt="")
    changed_fx = {k: v for k, v in rows_fx.items()
                  if _row_values(v) != _row_values(full.get(k, {}))}
    checks["nameless_field_no_heat_row"] = (len(deltas_fx) == 1
                                            and len(changed_fx) == 1
                                            and all(str(v.get("param") or "") == ""
                                                    for v in changed_fx.values()))
    if not checks["nameless_field_no_heat_row"]:
        errors.append(f"无 name 的参数生成了热力图行或计数异常：{list(changed_fx)[:3]}")

    probe = FeaturePaths(workdir / "probe").agg_ledger
    probe.parent.mkdir(parents=True, exist_ok=True)
    analytics.dashboard(pop, agg=probe)                     # 探针账本（主账本保持原样）

    #    6c 区间外记录：读侧按区间过滤，看板数字不受影响
    oob = out_of_range_fixture()
    scoped = analytics.dashboard(pop, agg=probe, range_from="2026-11-01", range_to="2026-11-15")
    scoped_oob = analytics.dashboard(pop + [oob], agg=probe,
                                     range_from="2026-11-01", range_to="2026-11-15")
    checks["out_of_range_excluded_by_range"] = (scoped["total_tasks"]
                                                == scoped_oob["total_tasks"])
    if not checks["out_of_range_excluded_by_range"]:
        errors.append(f"区间外记录进了数字：{scoped['total_tasks']} != {scoped_oob['total_tasks']}")

    #    6d 重复 id（同 record_key 同内容）：水位线折叠 → 账本不双计（天真复算会双计）
    dup_dir = workdir / "dup-probe"
    dup_dir.mkdir(parents=True, exist_ok=True)
    dup_ledger = FeaturePaths(dup_dir).agg_ledger
    analytics.dashboard(pop, agg=dup_ledger)
    dup = copy.deepcopy(pop[0])
    d_up = analytics.dashboard(pop + [dup], agg=dup_ledger)
    rows_probe = analytics_agg.AggLedger(dup_ledger).load()["rows"]
    naive = analytics_agg.recompute_rows(pop + [dup], salt="")
    checks["duplicate_id_collapses_in_ledger"] = (
        int(d_up["agg"].get("newly_aggregated") or 0) == 0
        and all(_row_values(rows_probe.get(k, {})) == _row_values(full.get(k, {}))
                for k in set(rows_probe) | set(full))
        and any(_row_values(naive.get(k, {})) != _row_values(full.get(k, {}))
                for k in set(naive) | set(full)))
    if not checks["duplicate_id_collapses_in_ledger"]:
        errors.append("重复 id 在账本里没有折叠（双计或水位线未去重）")

    ok = not errors
    led = {"path": str(ledger_path), "lines": verify["lines"], "rows": verify["rows"],
           "head": verify["head"], "chain_ok": verify["ok"],
           "problems": verify["problems_count"], "bootstrap_ms": round(t_bootstrap, 1)}
    return {
        "task": "R6-2 / B6-1 抽样核对",
        "ok": ok,
        "seed": seed,
        "sample_n": len(picked),
        "min_sample": MIN_SAMPLE,
        "census_mode": census,
        "population": len(pop),
        "population_census": pop_census,
        "ledger": led,
        "sample_list": [{"index": i,
                         "id": (rec.get("id") if isinstance(rec, dict) else "«not-a-dict»"),
                         "record_key": (analytics_agg.record_key(rec)
                                        if isinstance(rec, dict) else "—")}
                        for i, rec in picked],
        "per_record": per_record,
        "checks": checks,
        "fallback_reasons": fallbacks,
        "metric_keys": list(METRIC_KEYS) + list(TAIL_KEYS),
        "metrics_snapshot": {k: first.get(k) for k in METRIC_KEYS + TAIL_KEYS},
        "compensation_vanished_keys": len(zeroed),
        "errors": errors,
        "mismatch_records": mismatch,
        "row_diff_keys": len(row_diff),
        "metric_diff": metric_diff,
    }


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #

def render_markdown(report: dict) -> str:
    led = report["ledger"]
    lines = [
        "# R6-2 / B6-1 抽样核对报告（机器生成）",
        "",
        f"- 种子：`{report['seed']}` · 抽样 **{report['sample_n']}** 条"
        f"（下限 {report['min_sample']}）· 母体 **{report['population']}** 条"
        f"{' · 模式=全量逐条（census）' if report['census_mode'] else ''}",
        f"- 账本：`{led['path']}` · 行数 {led['lines']} · 合并行 {led['rows']} · "
        f"链校验 {'OK' if led['chain_ok'] else 'FAIL'} · 首建 {led['bootstrap_ms']} ms",
        f"- 结论：**{'误差 0，通过' if report['ok'] else '未通过'}**"
        f"（逐条不符 {report['mismatch_records']} 条 · 行级不符 {report['row_diff_keys']} 键）",
        "",
        "## 门禁（逐项）",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
    ]
    for key, value in report["checks"].items():
        lines.append(f"| `{key}` | {'PASS' if value else 'FAIL'} |")
    lines += ["", "## 回退原因码（诚实回退）", "",
              "| 场景 | 期望 | 实测 |", "| --- | --- | --- |"]
    for code, got in report["fallback_reasons"].items():
        lines.append(f"| {code} | `{code}` | `{got}` |")
    lines += ["", "## 逐条对账表", "",
              "| # | record id | 日期 | 工具 | 决定 | 贡献键 | Δcount | Δ干预 | Δµs | Δn | 账本行 | 移除恒等 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, item in enumerate(report["per_record"], 1):
        lines.append(
            f"| {i} | `{item['id']}` | {item.get('date', '—')} | {item.get('tool', '—')} "
            f"| {item.get('decision', '—')} | {item['keys']} "
            f"| {item.get('d_count', 0)} | {item.get('d_intervention', 0)} "
            f"| {item.get('d_sum_micros', 0)} | {item.get('d_n', 0)} "
            f"| {'Y' if item['row_ok'] else 'N'} | {'Y' if item['identity_ok'] else 'N'} |")
    if report["errors"]:
        lines += ["", "## 误差明细", ""] + [f"- {e}" for e in report["errors"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R6-2 / B6-1 预聚合账本抽样核对")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    ap.add_argument("--synth", type=int, default=SYNTH_TARGET)
    ap.add_argument("--census", action="store_true", help="全量逐条（不止抽样）")
    ap.add_argument("--out-prefix", default=str(ROOT / "_tmp" / "r62_sampling_audit"))
    args = ap.parse_args(argv)

    report = audit(seed=args.seed, sample_n=args.sample, synth=args.synth, census=args.census)
    prefix = pathlib.Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    led = report["ledger"]
    print(f"seed={report['seed']} sample={report['sample_n']} "
          f"population={report['population']} ledger_lines={led['lines']} "
          f"rows={led['rows']} chain_ok={led['chain_ok']}")
    print("checks " + " ".join(f"{k}={'OK' if v else 'FAIL'}"
                               for k, v in report["checks"].items()))
    print(f"mismatch_records={report['mismatch_records']} "
          f"row_diff_keys={report['row_diff_keys']} metric_diff={len(report['metric_diff'])}")
    print(f"ARTIFACTS {prefix.with_suffix('.json')} {prefix.with_suffix('.md')}")
    print("AUDIT " + ("PASS" if report["ok"] else "FAIL"))
    for e in report["errors"][:10]:
        print("ERROR:", e)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
