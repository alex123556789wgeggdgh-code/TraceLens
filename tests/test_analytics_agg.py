"""R6-1（REQ-006 B6-2 / D-5 修订口径）验收测试：队长仪表盘**预聚合账本**。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest tests.test_analytics_agg -v

钉死的口径（PM 裁决，落 docs/07 §11.15）：

1. **追加式**：第二次聚合只追加、历史行一字不改（字节前缀复测）；
2. **修正 = 追加补偿行**：记录被改写 → 受影响键追加新值行（同键后读胜出），
   消失的键追加**归零补偿行**；历史行永不改写；
3. **哈希链 verify 通过**：行 ``entry_hash = sha256(prev_hash + 规范 JSON)``，
   口径逐字复用 ``audit`` 单点（``canonical_json``/``entry_hash``/``GENESIS_HASH``）；
4. **双路径同结果**：预聚合命中 vs 流式回退对同一数据**逐指标相等**，且都等于
   旧物化算法（复算参照）——三条线钉死 B6-1 的可复算性；
5. **增量水位线**：新记录只聚合增量（``newly_aggregated`` 如实计数），
   幂等重跑零追加；
6. **O(1) 内存口径**：流式路径逐行消费——一次性生成器输入必须能扫完（物化整载
   的写法会二次迭代失败）；
7. **边界**：空账本/空记录不建文件、如实 0；
8. **1000 条级计时实测**：真测真报（``3s`` 达标与否都如实打印），不伪造计时；
9. **如实元数据**：``pre_aggregated`` / ``scanned`` / ``agg.reason`` 三件套，
   降级可见（UI 标注数据面，前端归 R6-4）。

临时目录一律 ``tests/scratch.py``；HTTP 断言级不重试（``http_fixture``）。
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
import stat
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, str(_p))

from agent_explainer import analytics as an  # noqa: E402
from agent_explainer import analytics_agg as ag  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
import http_fixture as fx  # noqa: E402
from scratch import scratch_dir, use_scratch_tempfile  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))

#: 双路径必须逐键相等的指标区键（T26 契约键位；generated_at 随时钟动，不逐字比）
METRIC_KEYS = ("total_tasks", "total_interventions", "intervention_rate",
               "avg_approval_seconds", "avg_approval_ms", "trust_health",
               "users", "trend", "heatmap", "top_parameters", "top_rejected",
               "approved", "rejected", "range", "granularity")


def rec(i: int, *, day: int = 12, decision: str | None = None,
        tool: str = "fs.write", fields: tuple[str, ...] = (),
        decided_minutes: int | None = 60, agent_id: str = "agent-a",
        user: str = "local-user", bad_ts: bool = False) -> dict:
    """仪表盘形状的 ActionRecord（消费字段与 dashboard 口径逐项对齐）。"""
    out: dict = {"id": f"act-r6-{i:04d}", "agent_id": agent_id,
                 "timestamp": ("not-a-time" if bad_ts
                               else f"2026-11-{day:02d}T09:00:00Z"),
                 "tool_used": {"name": tool}}
    if decision:
        out["user_intervention"] = {
            "decision": decision, "decided_by": user,
            "decided_at": (f"2026-11-{day:02d}T{9 + decided_minutes:02d}:00:00Z"
                           if decided_minutes is not None else None),
            "modified_fields": [{"name": name} for name in fields]}
    return out


def rich_records() -> list[dict]:
    """多样夹具：3 工具 × 4 日 × 决定/无决定 × 多参数 × 多用户 + 坏时间戳 + 区间外。"""
    rows: list[dict] = []
    tools = ("fs.write", "calendar.read", "shell.exec")
    decisions = (None, "approve", "modify", "reject")
    users = ("local-user", "team-alice", "team-bob")
    i = 0
    for day in (10, 11, 12, 13):
        for ti, tool in enumerate(tools):
            for di, dec in enumerate(decisions):
                i += 1
                rows.append(rec(i, day=day, decision=dec, tool=tool,
                                fields=("path", "path", "limit")
                                if dec == "modify" else ("path",)
                                if dec == "reject" else (),
                                decided_minutes=30 * (di + 1) if dec else None,
                                user=users[(ti + di) % len(users)]))
    i += 1
    rows.append(rec(i, bad_ts=True, decision="approve"))          # 时间戳坏：算不出不计
    i += 1
    rows.append({"id": f"act-r6-{i:04d}", "agent_id": "agent-a",
                 "timestamp": "2026-10-01T09:00:00Z", "tool_used": {"name": "fs.write"},
                 "user_intervention": {"decision": "approve", "decided_by": "local-user",
                                       "decided_at": "2026-10-01T10:00:00Z",
                                       "modified_fields": []}})   # 区间外（10 月）
    rows.append("not-a-dict")                                       # 脏项：跳过不崩
    return rows


def ledger_path(root: pathlib.Path) -> pathlib.Path:
    paths = FeaturePaths(root / "features")
    return paths.agg_ledger


def metrics_of(report: dict) -> dict:
    return {key: report[key] for key in METRIC_KEYS}


def plain_metrics(records, **kw) -> dict:
    """无账本的流式基线（同参数）。"""
    return metrics_of(an.dashboard(records, **kw))


# --------------------------------------------------------------------------- #
# A. 账本本体：追加式 / 补偿行 / 哈希链 / 水位线 / 边界 / 幂等
# --------------------------------------------------------------------------- #

class AggLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir(f"agg-ledger-{self.id().rsplit('.', 1)[-1]}")
        self.path = ledger_path(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def snapshot_bytes(self) -> bytes:
        return self.path.read_bytes() if self.path.exists() else b""

    def test_append_only_second_pass_appends_not_overwrites(self) -> None:
        """§2.5-1 追加式：第二次聚合的行**追加**在文件尾部，第一次的字节前缀一字不改。"""
        first = rich_records()[:12]
        r1 = an.dashboard(first, agg=self.path)
        self.assertTrue(r1["pre_aggregated"], r1["agg"])
        prefix = self.snapshot_bytes()
        grown = first + rich_records()[12:20]
        r2 = an.dashboard(grown, agg=self.path)
        suffix = self.snapshot_bytes()
        self.assertTrue(suffix.startswith(prefix), "append-only：旧行必须原样是前缀")
        self.assertGreater(len(suffix), len(prefix), "新记录必须追加出行")
        self.assertEqual(r2["agg"]["newly_aggregated"], 8)
        self.assertEqual(r2["total_tasks"], r1["total_tasks"] + 8)

    def test_correction_appends_compensation_row(self) -> None:
        """§2.5-2 修正 = 追加补偿行：记录补上决定后，同键新行后读胜出，历史行不改。"""
        base = [rec(1, day=11, decision=None),
                rec(2, day=11, decision="approve", fields=("path",))]
        an.dashboard(base, agg=self.path)
        prefix = self.snapshot_bytes()
        updated = [dict(base[0],
                        user_intervention={"decision": "modify",
                                           "decided_by": "local-user",
                                           "decided_at": "2026-11-11T11:00:00Z",
                                           "modified_fields": [{"name": "limit"}]}),
                   base[1]]
        r2 = an.dashboard(updated, agg=self.path)
        suffix = self.snapshot_bytes()
        self.assertTrue(suffix.startswith(prefix), "补偿行只能追加，历史一字不改")
        self.assertGreater(r2["agg"]["compensation_keys"], 0, "必须发生补偿")
        self.assertEqual(r2["total_interventions"], 2)
        self.assertEqual(r2["total_tasks"], 2)
        # 补偿后的数字必须与流式基线逐指标相等
        self.assertEqual(metrics_of(r2), plain_metrics(updated))

    def test_moved_date_vanishes_keys_get_zero_compensation(self) -> None:
        """记录日期被改 → 旧日期键追加**归零补偿行**，趋势不放假点（两路径同结果）。"""
        base = [rec(1, day=10, decision="approve"), rec(2, day=10, decision=None)]
        r1 = an.dashboard(base, agg=self.path)
        self.assertEqual(len(r1["trend"]), 1)
        moved = [dict(base[0], timestamp="2026-11-11T09:00:00Z"), base[1]]
        r2 = an.dashboard(moved, agg=self.path)
        self.assertEqual(r2["agg"]["compensation_keys"], 1,
                         "旧日期消失的键（approve 本体行）必须追加归零补偿行")
        self.assertEqual(metrics_of(r2), plain_metrics(moved))
        self.assertEqual([b["bucket"] for b in r2["trend"]],
                         [b["bucket"] for b in plain_metrics(moved)["trend"]],
                         "归零补偿行不得在 trend 里留下 tasks=0 的幻影点")

    def test_hash_chain_verify_passes(self) -> None:
        """§2.5-3 哈希链 verify：逐行复算 entry_hash 全通过，链头稳定。"""
        records = rich_records()
        an.dashboard(records, agg=self.path)
        report = ag.AggLedger(self.path).verify()
        self.assertTrue(report["ok"], report["problems"])
        self.assertGreater(report["rows"], 0)
        self.assertEqual(report["head"], report["head"])
        # 链式口径 = audit 单点：第一条链行的 prev_hash 是 audit GENESIS
        first_line = self.snapshot_bytes().decode("utf-8").splitlines()[1]
        row = json.loads(first_line)
        self.assertEqual(row["prev_hash"], ag.GENESIS_HASH)
        self.assertEqual(row["entry_hash"],
                         __import__("agent_explainer.audit", fromlist=["entry_hash"])
                         .entry_hash(row, row["prev_hash"]))

    def test_tamper_is_detected_and_falls_back_to_stream(self) -> None:
        """链被篡改 → 如实检测（不消费污染数据），数字走流式并标注 reason。"""
        records = rich_records()
        an.dashboard(records, agg=self.path)
        lines = self.snapshot_bytes().decode("utf-8").splitlines()
        tampered = json.loads(lines[2])
        tampered["count"] = int(tampered.get("count") or 0) + 7  # 只动聚合值
        lines[2] = json.dumps(tampered, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        got = an.dashboard(records, agg=self.path)
        self.assertFalse(got["pre_aggregated"], "篡改后必须降级，不得装命中")
        self.assertEqual(got["agg"]["reason"], "chain_tampered", got["agg"])
        self.assertEqual(metrics_of(got), plain_metrics(records),
                         "降级后的数字必须仍等于流式基线")
        self.assertIn("problems", got["agg"], "篡改证据进元数据，取证不靠猜")

    def test_watermark_state_row_tracks_new_records_only(self) -> None:
        """§2.5-5 增量水位线：状态行记全量消费快照；第二次调用只聚合新增。"""
        first = rich_records()[:10]
        r1 = an.dashboard(first, agg=self.path)
        snap = ag.AggLedger(self.path).load()
        self.assertIsNotNone(snap["state"])
        self.assertEqual(snap["state"]["consumed_count"], 10)
        grown = first + rich_records()[10:13]
        r2 = an.dashboard(grown, agg=self.path)
        self.assertEqual(r2["agg"]["newly_aggregated"], 3, "水位线外新增 3 条 → 只聚合 3 条")
        self.assertEqual(r2["scanned"], 13, "scanned = 本次消费的记录条数（如实）")

    def test_aggregation_is_idempotent(self) -> None:
        """§2.5-9 聚合定义幂等：同输入重跑 → 零追加、指标逐键相等。"""
        records = rich_records()
        r1 = an.dashboard(records, agg=self.path)
        size1 = self.path.stat().st_size
        r2 = an.dashboard(records, agg=self.path)
        size2 = self.path.stat().st_size
        self.assertEqual(size1, size2, "同输入重跑不得追加任何行")
        self.assertEqual(r2["agg"]["newly_aggregated"], 0)
        self.assertEqual(metrics_of(r1), metrics_of(r2))
        self.assertEqual(r2["agg"]["head"], r1["agg"]["head"])

    def test_empty_records_and_missing_ledger_creates_nothing(self) -> None:
        """§2.5-8 空边界：空记录 + 账本缺失 → 不建文件、如实 0、reason 标注。"""
        got = an.dashboard([], agg=self.path)
        self.assertFalse(self.path.exists(), "空账本边界不许留空文件")
        self.assertFalse(got["pre_aggregated"])
        self.assertEqual(got["agg"]["reason"], "ledger_missing_no_records", got["agg"])
        self.assertEqual(got["scanned"], 0)
        self.assertEqual(got["total_tasks"], 0)
        self.assertEqual(got["total_interventions"], 0)
        self.assertEqual(got["trend"], [])

    def test_write_failure_keeps_numbers_and_marks_durable_false(self) -> None:
        """账本写不进（只读文件）→ 数字照出、``durable:false`` 如实，不装持久、不装坏数。"""
        records = rich_records()[:8]
        an.dashboard(records, agg=self.path)          # 先引导建账本（可读）
        os.chmod(self.path, stat.S_IREAD)             # 追加会撞 PermissionError
        try:
            grown = records + rich_records()[8:12]
            got = an.dashboard(grown, agg=self.path)
        finally:
            os.chmod(self.path, stat.S_IWRITE | stat.S_IREAD)
        self.assertTrue(got["pre_aggregated"], "本轮从复算视图读，命中口径成立")
        self.assertFalse(got["agg"]["durable"], "写不进必须如实 durable:false")
        self.assertIn("problems", got["agg"], "写失败的机器码进元数据，取证不靠猜")
        self.assertEqual(metrics_of(got), plain_metrics(grown))
        # 权限恢复后重跑：数字与流式基线一致（幂等重试成功）
        retry = an.dashboard(grown, agg=self.path)
        self.assertTrue(retry["agg"]["durable"])
        self.assertEqual(metrics_of(retry), plain_metrics(grown))

    def test_unreadable_ledger_falls_back_honestly(self) -> None:
        """账本不可读（坏 JSON 满屏）→ 流式回退 + reason 如实；数字不掺污染数据。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json}\n{also not json}\n", encoding="utf-8")
        records = rich_records()[:6]
        got = an.dashboard(records, agg=self.path)
        self.assertFalse(got["pre_aggregated"])
        self.assertEqual(got["agg"]["reason"], "chain_tampered",
                         "坏行=链校验不过（读侧逐行登记），必须降级")
        self.assertEqual(metrics_of(got), plain_metrics(records))

    def test_feature_paths_is_the_single_source(self) -> None:
        """§2.1 落点经 FeaturePaths 注册；ensure() 不预建 analytics 目录（GET 零副作用）。"""
        paths = FeaturePaths(self.root)
        self.assertEqual(paths.agg_ledger,
                         self.root / "analytics" / ag.AGG_FILE_NAME)
        paths.ensure()
        self.assertFalse(paths.analytics_dir.exists(),
                         "账本目录只在首次追加时创建，ensure 不预建")


# --------------------------------------------------------------------------- #
# B. 双路径同结果 + 复算参照（B6-1 可复算性）
# --------------------------------------------------------------------------- #

class ParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir(f"agg-parity-{self.id().rsplit('.', 1)[-1]}")
        self.path = ledger_path(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_preaggregated_hit_equals_streaming_fallback(self) -> None:
        """§2.5-4 预聚合命中 vs 流式回退：同一数据逐指标相等（全部 15 个键）。"""
        records = rich_records()
        hit = an.dashboard(records, agg=self.path)
        stream = an.dashboard(records)          # 无账本 = 流式回退路径
        self.assertTrue(hit["pre_aggregated"])
        self.assertFalse(stream["pre_aggregated"])
        self.assertEqual(metrics_of(hit), metrics_of(stream))

    def test_stream_matches_the_materialized_reference(self) -> None:
        """流式扫描逐键等于**旧物化算法**（复算参照）——改造没有改口径。"""
        records = rich_records()
        reference = an._dashboard_materialized(records, range_from="2026-11-01",
                                               range_to="2026-11-30")
        stream = an.dashboard(records, range_from="2026-11-01", range_to="2026-11-30")
        self.assertEqual(metrics_of(reference), metrics_of(stream))

    def test_week_granularity_parity(self) -> None:
        """granularity=week：账本日期行聚合出的周桶与流式逐条完全一致。"""
        records = rich_records()
        hit = an.dashboard(records, agg=self.path, granularity="week")
        stream = an.dashboard(records, granularity="week")
        self.assertTrue(hit["pre_aggregated"])
        self.assertEqual(metrics_of(hit), metrics_of(stream))
        self.assertTrue(all(len(b["bucket"]) == 10 for b in hit["trend"]),
                        "周桶必须是 ISO 日期（周一）")

    def test_range_filter_parity(self) -> None:
        """区间过滤：账本行按 date∈[from,to] 读出，与流式区间扫描同结果（含端点）。"""
        records = rich_records()
        hit = an.dashboard(records, agg=self.path,
                           range_from="2026-11-11", range_to="2026-11-12")
        stream = an.dashboard(records, range_from="2026-11-11", range_to="2026-11-12")
        self.assertEqual(metrics_of(hit), metrics_of(stream))
        self.assertGreater(hit["total_tasks"], 0)
        self.assertLess(hit["total_tasks"], stream["total_tasks"] + 1)

    def test_agent_filter_falls_back_with_honest_metadata(self) -> None:
        """agent 过滤不在账本键里 → 回退流式，reason 如实；数字仍逐键正确。"""
        records = rich_records()
        hit = an.dashboard(records, agg=self.path, agent="agent-a")
        stream = an.dashboard(records, agent="agent-a")
        self.assertFalse(hit["pre_aggregated"], "agent 过滤不许假命中")
        self.assertEqual(hit["agg"]["reason"], "agent_filter", hit["agg"])
        self.assertEqual(metrics_of(hit), metrics_of(stream))
        self.assertEqual(hit["total_tasks"], len(records) - 2)  # 脏项与坏时间戳不进任务数

    def test_custom_salt_falls_back_with_honest_metadata(self) -> None:
        """自定义脱敏盐写不进账本（用户摘要在聚合时定死）→ 回退 + reason 如实。"""
        records = rich_records()[:10]
        hit = an.dashboard(records, agg=self.path, salt="demo-salt")
        stream = an.dashboard(records, salt="demo-salt")
        self.assertFalse(hit["pre_aggregated"])
        self.assertEqual(hit["agg"]["reason"], "custom_salt", hit["agg"])
        self.assertEqual(metrics_of(hit), metrics_of(stream))

    def test_generator_input_streams_in_one_pass(self) -> None:
        """§2.5-6 O(1) 口径证据：一次性生成器能被**流式单遍**扫完；
        账本路径需要可重放输入（复算二次扫描），生成器如实降级，绝不整载。"""
        records = rich_records()
        gen = iter(records)
        got = an.dashboard(gen, agg=self.path)
        self.assertFalse(got["pre_aggregated"])
        self.assertEqual(got["agg"]["reason"], "input_not_rerunnable", got["agg"])
        self.assertEqual(got["scanned"], len(records), "scanned 如实 = 消费条数")
        self.assertEqual(list(gen), [], "生成器必须被单遍消费干净（物化整载的写法过不了这")

    def test_aggregation_rows_are_recomputable_from_records(self) -> None:
        """§2.4 B6-1 前瞻：聚合定义可复算——recompute_rows(记录流) 逐键等于账本合并行，
        且整条链 verify 通过。R6-2 抽样核对就复算这两个出口。"""
        records = rich_records()
        an.dashboard(records, agg=self.path)
        fresh = ag.recompute_rows(records)
        snap = ag.AggLedger(self.path).load()
        self.assertTrue(snap["chain_ok"])
        self.assertEqual(set(fresh), set(snap["rows"]),
                         "从记录复算的键集必须与账本合并行逐键相等")
        for key, values in fresh.items():
            self.assertEqual(ag._row_values(values), ag._row_values(snap["rows"][key]),
                             f"键 {key} 的聚合值复算不符")

    def test_intervention_update_end_to_end_parity(self) -> None:
        """S0-1 形态的干预改写（决定段追加）→ 预聚合数字与流式基线保持逐键相等。"""
        base = [rec(i, day=10 + i % 3, decision=("approve", "modify", "reject")[i % 3],
                    fields=("path",) if i % 3 == 1 else ())
                for i in range(1, 13)]
        an.dashboard(base, agg=self.path)
        updated = copy.deepcopy(base)
        for i in (2, 5, 8):
            updated[i]["user_intervention"]["modified_fields"].append(
                {"name": f"limit_{i}"})
            updated[i]["user_intervention"]["decision"] = "modify"
        hit = an.dashboard(updated, agg=self.path)
        self.assertEqual(metrics_of(hit), plain_metrics(updated))
        self.assertGreater(hit["agg"]["compensation_keys"], 0)
        self.assertTrue(ag.AggLedger(self.path).verify()["ok"])


# --------------------------------------------------------------------------- #
# C. 1000 条级计时实测（铁律 4：真测真报，不伪造 0.2/0.4）
# --------------------------------------------------------------------------- #

class BenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir(f"agg-bench-{self.id().rsplit('.', 1)[-1]}")
        self.path = ledger_path(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, n: int = 1000) -> list[dict]:
        tools = ("fs.write", "calendar.read", "shell.exec", "net.fetch", "db.query")
        params = ("path", "limit", "timeout", "scope")
        rows: list[dict] = []
        for i in range(1, n + 1):
            dec = (None, "approve", "modify", "reject")[i % 4]
            fields = (params[i % len(params)], params[(i + 2) % len(params)]) \
                if dec == "modify" else (params[i % len(params)],) if dec == "reject" else ()
            rows.append(rec(i, day=1 + i % 26, decision=dec, tool=tools[i % len(tools)],
                            fields=fields, decided_minutes=5 + i % 55,
                            user=f"user-{i % 9}"))
        return rows

    def test_benchmark_1000_records_real_timing(self) -> None:
        """§2.5-10：1000 条级账本计时**实测**并如实打印；预聚合命中路径 3s 达标与否都报。"""
        records = self.build(1000)
        t0 = time.perf_counter()
        plain = an.dashboard(records)                       # 流式基线
        t_stream = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        warm = an.dashboard(records, agg=self.path)         # 首跑=引导聚合+落盘
        t_bootstrap = (time.perf_counter() - t0) * 1000.0
        self.assertTrue(warm["pre_aggregated"])
        hits: list[float] = []
        for _ in range(3):
            t0 = time.perf_counter()
            hit = an.dashboard(records, agg=self.path)      # 命中：读账本合并行
            hits.append((time.perf_counter() - t0) * 1000.0)
        best = min(hits)
        print(f"\n[r6-1 bench] records=1000 stream={t_stream:.1f}ms "
              f"bootstrap={t_bootstrap:.1f}ms hit_best={best:.1f}ms "
              f"hit_all={[round(x, 1) for x in hits]} "
              f"ledger_rows={hit['agg']['ledger_rows']} scanned={hit['scanned']} "
              f"pre_aggregated={hit['pre_aggregated']}")
        self.assertEqual(metrics_of(hit), metrics_of(plain),
                         "基准数据上双路径也必须逐键相等")
        self.assertLess(hit["scanned"], 0 + 1001)
        # 达标判据（B6-2 修订口径：3s 承诺只对预聚合命中成立）——实测多少比多少，
        # 不达标就红给 PM 看，不静默调阈值。
        self.assertLess(best / 1000.0, 3.0,
                        f"预聚合命中实测 {best:.1f}ms，超 3s 口径必须上报 PM")


# --------------------------------------------------------------------------- #
# D. 接线：FeaturePaths 落盘 + 既有 T26/T27 端点带元数据（零新路由）
# --------------------------------------------------------------------------- #

class WiringHttpTests(unittest.TestCase):
    """产品口径服务器：GET /api/analytics/dashboard 命中/回退双路径 + 元数据形状。"""

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"agg-wiring-http-{case}")
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(NATIVE), quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.host, self.port = self.server.server_address[:2]
        self.service = self.server.service
        self.agg = (self.root / "features" / "analytics" / "agg_daily.jsonl")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.root, ignore_errors=True)

    def get(self, path: str) -> tuple[int, dict, object]:
        def once(conn):
            conn.request("GET", path, headers={"Connection": "close"})
            response = conn.getresponse()
            raw = response.read()
            try:
                data = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = raw.decode("utf-8", "replace")
            return response.status, dict(response.getheaders()), data

        return fx.with_connection(self.host, self.port, once, verb="GET", path=path)

    def analytics(self, query: str = "") -> dict:
        status, _hd, data = self.get(f"/api/analytics/dashboard{query}")
        self.assertEqual(200, status, data)
        self.assertTrue(data["ok"])
        return data["analytics"]

    def test_dashboard_carries_the_honest_metadata(self) -> None:
        """首跑引导聚合（pre_aggregated=true, newly>0），二跑命中零新增；元数据三件套在位。"""
        first = self.analytics()
        self.assertIn("pre_aggregated", first, "T26 响应必须带如实元数据（R6-4 的数据面）")
        self.assertIn("scanned", first)
        self.assertIn("agg", first)
        self.assertTrue(first["pre_aggregated"], first["agg"])
        self.assertGreater(first["agg"]["newly_aggregated"], 0)
        self.assertTrue(self.agg.exists(), "账本落 FeaturePaths 注册位置")
        self.assertEqual(first["agg"]["head"],
                         ag.AggLedger(self.agg).verify()["head"],
                         "元数据的 head 必须是落盘后的链头（R6-2 审计入口）")
        second = self.analytics()
        self.assertTrue(second["pre_aggregated"])
        self.assertEqual(second["agg"]["newly_aggregated"], 0)
        self.assertEqual(second["agg"]["head"], first["agg"]["head"],
                         "无新增记录的第二次调用不推进链头")
        for key in METRIC_KEYS:
            self.assertIn(key, second, "T26 契约键位不许被 R6-1 动")

    def test_agent_filter_on_http_path_falls_back_marked(self) -> None:
        """HTTP 上带 agent 过滤 → 响应如实标 pre_aggregated=false + reason，数字不掺假。"""
        records = self.service.store.all()
        agent = str(records[0].get("agent_id") or "") if records else ""
        report = self.analytics(f"?agent={agent}")
        self.assertFalse(report["pre_aggregated"])
        self.assertEqual(report["agg"]["reason"], "agent_filter")
        self.assertEqual(report["total_tasks"],
                         an.dashboard(records, agent=agent)["total_tasks"])

    def test_contract_carries_no_new_analytics_route(self) -> None:
        """零新路由：契约里 analytics 族仍是两条已知 (path,verb)，预聚合不挂新端点。"""
        routes = {(route["path"], route["verb"]) for route in CONTRACT["routes"]}
        analytics_routes = {(p, v) for (p, v) in routes if p.startswith("/api/analytics")}
        self.assertEqual(analytics_routes,
                         {("/api/analytics/dashboard", "GET"),
                          ("/api/analytics/export", "GET")},
                         "R6-1 前后 analytics 路由面一字不动（族根是前缀门，不是路由）")
        for p, _v in routes:
            self.assertNotIn("agg", p, "预聚合是内部账本，不是 HTTP 面")

    def test_modules_self_check_green(self) -> None:
        """两个模块自检（analytics 旧自检 + analytics_agg 新自检）在替身临时目录下全绿。"""
        with use_scratch_tempfile():
            self.assertEqual(0, an._self_check())
            self.assertEqual(0, ag._self_check())


if __name__ == "__main__":  # 手工自检：python tests/test_analytics_agg.py -v
    unittest.main(verbosity=2)
