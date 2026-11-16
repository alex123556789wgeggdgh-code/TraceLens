#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6-2 / B6-1：预聚合账本**抽样核对**的钉子（配合 ``tools/analytics_sampling_audit.py``）。

钉死六类事实（TASK-R6-2 交付口径）：

1. **抽样可复现**：同种子同样本、换种子换样本；少于 50 条直接拒绝（不静默缩小审计）。
2. **逐条重算口径**：单条贡献 = ``record_to_deltas``；全部贡献之和 == ``recompute_rows``
   （账本写入用的就是这同一个函数，不存在第二套聚合实现）。
3. **误差 0**：账本合并行逐键 == 全量复算；抽中每条记录的**移除恒等边际**逐字段相等。
4. **修正（冲账）行参与对账**：改写一条 → 补偿行落账、旧键留归零行、合并行仍等于新复算。
5. **账本缺失/不可信的回退核对**：五种回退原因码如实，且数字与流式扫描逐键一致。
6. **指标装配零误差**：账本行装配 == ``_assemble_metrics`` == 流式路径 == 冻结的旧算法参照。

临时目录只走 ``tests/scratch.py``；审计全程对账本**只读**（除补偿用例显式再跑一次仪表盘）。
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scratch import scratch_dir                                    # noqa: E402

from agent_explainer import analytics, analytics_agg               # noqa: E402
from agent_explainer.feature_store import FeaturePaths             # noqa: E402

import analytics_sampling_audit as sa                              # noqa: E402

SYNTH = 220            # 测试母体规模（真仓演示 + 合成）：抽样下限 50 仍能真抽
VALUE_FIELDS = sa.VALUE_FIELDS


def _rows_equal(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(analytics_agg._row_values(a.get(k, {})) == analytics_agg._row_values(b.get(k, {}))
               for k in keys)


class SamplingAuditFixture(unittest.TestCase):
    """共享一次母体 + 一次账本构建，供各钉子复用（建账本身是审计的主证）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.work = pathlib.Path(scratch_dir("r62_sampling_tests"))
        cls.pop, cls.census = sa.build_population(seed=sa.DEFAULT_SEED, synth=SYNTH)
        cls.ledger = FeaturePaths(cls.work / "main").agg_ledger
        cls.ledger.parent.mkdir(parents=True, exist_ok=True)
        cls.report = analytics.dashboard(cls.pop, agg=cls.ledger)
        cls.snap = analytics_agg.AggLedger(cls.ledger).load()
        cls.rows = cls.snap["rows"]
        cls.full = analytics_agg.recompute_rows(cls.pop, salt="")

    def setUp(self) -> None:
        if type(self) is SamplingAuditFixture:
            self.skipTest("基类只做共享夹具")


class SampleReproducibilityTests(SamplingAuditFixture):

    def test_same_seed_same_sample(self) -> None:
        """抽样可复现：同种子两次抽出的 (下标, id) 序列逐位相同。"""
        a = sa.sample_records(self.pop, seed=sa.DEFAULT_SEED, n=60)
        b = sa.sample_records(self.pop, seed=sa.DEFAULT_SEED, n=60)
        self.assertEqual([i for i, _ in a], [i for i, _ in b])
        ident = lambda r: r.get("id") if isinstance(r, dict) else "«non-dict»"
        self.assertEqual([ident(r) for _, r in a], [ident(r) for _, r in b])
        self.assertGreaterEqual(len(a), sa.MIN_SAMPLE)

    def test_other_seed_draws_other_sample(self) -> None:
        """换种子就换样本（种子不是装饰，是真随机源）。"""
        a = {i for i, _ in sa.sample_records(self.pop, seed=1, n=60)}
        b = {i for i, _ in sa.sample_records(self.pop, seed=2, n=60)}
        self.assertNotEqual(a, b)

    def test_below_floor_is_refused(self) -> None:
        """少于任务书下限（50 条）→ 直接拒绝，不静默缩小审计。"""
        with self.assertRaises(ValueError):
            sa.sample_records(self.pop, seed=sa.DEFAULT_SEED, n=49)

    def test_sample_is_a_real_subset_of_the_population(self) -> None:
        """样本必须真取自母体（不是另造一批）；母体远大于样本，抽样才叫抽样。"""
        picked = sa.sample_records(self.pop, seed=sa.DEFAULT_SEED, n=50)
        self.assertEqual(len(picked), 50)
        self.assertGreater(len(self.pop), 5 * len(picked))
        for index, rec in picked:
            self.assertIs(self.pop[index], rec)


class ContributionLedgerTests(SamplingAuditFixture):

    def test_sum_of_deltas_equals_recompute_rows(self) -> None:
        """逐条贡献之和 == 全量复算：证明"一条记录的贡献"与账本口径是同一件事。"""
        merged: dict = {}
        for rec in self.pop:
            if not isinstance(rec, dict):
                continue                       # 与 recompute_rows 同一守卫（非 dict 整条跳过）
            for delta in analytics_agg.record_to_deltas(rec, salt=""):
                slot = merged.setdefault(delta["key"], {"count": 0, "intervention_count": 0,
                                                        "sum_micros": 0, "n": 0})
                slot["count"] += delta["count"]
                slot["intervention_count"] += delta["intervention_count"]
                slot["sum_micros"] += delta["approval_latency_agg"]["sum_micros"]
                slot["n"] += delta["approval_latency_agg"]["n"]
        self.assertEqual(set(merged), set(self.full))
        for key, slot in merged.items():
            row = self.full[key]
            agg = row["approval_latency_agg"]
            self.assertEqual(slot["count"], row["count"], key)
            self.assertEqual(slot["intervention_count"], row["intervention_count"], key)
            self.assertEqual(slot["sum_micros"], agg["sum_micros"], key)
            self.assertEqual(slot["n"], agg["n"], key)

    def test_ledger_rows_equal_full_recompute_zero_error(self) -> None:
        """误差 0（行级）：账本合并行逐键 == 原始记录全量复算。"""
        self.assertTrue(self.report.get("pre_aggregated"), self.report.get("agg"))
        self.assertTrue(_rows_equal(self.rows, self.full),
                        f"账本行与复算不符：{[k for k in set(self.rows) | set(self.full)][:3]}")
        self.assertGreater(len(self.rows), 0)

    def test_removal_identity_for_every_record(self) -> None:
        """逐条重算口径（全量逐条，不止抽样）：移除一条的边际 == 该条的 ``record_to_deltas``。"""
        for index, rec in enumerate(self.pop):
            if not isinstance(rec, dict):
                continue
            deltas = analytics_agg.record_to_deltas(rec, salt="")
            if not deltas:
                continue
            wo = analytics_agg.recompute_rows(self.pop[:index] + self.pop[index + 1:], salt="")
            for delta in deltas:
                key = delta["key"]
                self.assertIn(key, self.full)
                base = wo.get(key)
                for field in VALUE_FIELDS:
                    got = sa._int_of(self.full[key], field) - (
                        sa._int_of(base, field) if base else 0)
                    self.assertEqual(got, sa._delta_value(delta, field),
                                     f"{rec.get('id')} {key}.{field}")

    def test_sampled_records_all_zero_error(self) -> None:
        """抽中的每条记录逐条对账：账本行存在 + 值一致 + 移除恒等，误差条数 = 0。"""
        picked = sa.sample_records(self.pop, seed=sa.DEFAULT_SEED, n=50)
        bad = [sa.audit_contribution(self.pop, i, self.rows, self.full)
               for i, _ in picked]
        fails = [x for x in bad if not (x["row_ok"] and x["identity_ok"])]
        self.assertEqual(fails, [], f"逐条对账误差 {len(fails)} 条：{fails[:2]}")

    def test_watermark_covers_every_consumed_record(self) -> None:
        """水位线快照覆盖每条被消费的记录（键 → 摘要），逐条重放才可能对上。"""
        consumed = analytics_agg._consumed_of(self.snap["state"])
        for rec in self.pop:
            if not isinstance(rec, dict):
                continue
            key = analytics_agg.record_key(rec)
            self.assertEqual(consumed.get(key), analytics_agg.record_digest(rec), key)

    def test_chain_verifies_after_bootstrap(self) -> None:
        """建账后整条哈希链自证通过（抽样核对的账本本身必须可信）。"""
        verify = analytics_agg.AggLedger(self.ledger).verify()
        self.assertTrue(verify["ok"], verify["problems"])
        self.assertEqual(verify["problems_count"], 0)
        self.assertEqual(verify["rows"], len(self.rows))


class MetricsAssemblyParityTests(SamplingAuditFixture):

    def test_metrics_zero_error_across_all_paths(self) -> None:
        """15 个指标键：账本行装配 == ``_assemble_metrics`` == 流式 == 冻结旧算法参照。"""
        counters = analytics_agg.rows_to_counters(
            self.rows, start=__import__("datetime").date(2000, 1, 1),
            end=__import__("datetime").date.max, granularity="day")
        from_rows = analytics._assemble_metrics(counters)
        stream = analytics.dashboard(self.pop)
        materialized = analytics._dashboard_materialized(self.pop)
        for key in sa.METRIC_KEYS:
            a = self.report.get(key)
            self.assertEqual(json.dumps(a, sort_keys=True),
                             json.dumps(from_rows.get(key), sort_keys=True), key)
            self.assertEqual(json.dumps(a, sort_keys=True),
                             json.dumps(stream.get(key), sort_keys=True), key)
            self.assertEqual(json.dumps(a, sort_keys=True),
                             json.dumps(materialized.get(key), sort_keys=True), key)
        for key in sa.TAIL_KEYS:
            self.assertEqual(json.dumps(self.report.get(key), sort_keys=True),
                             json.dumps(stream.get(key), sort_keys=True), key)


class CompensationParticipationTests(unittest.TestCase):
    """修正（冲账）行参与对账：键唯一的小母体，旧键必留归零行。"""

    def setUp(self) -> None:
        self.work = pathlib.Path(scratch_dir("r62_sampling_comp"))
        self.ledger = FeaturePaths(self.work / "comp").agg_ledger
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.solo = {"schema_version": "1.1", "id": "r62-comp-solo",
                     "timestamp": "2026-11-02T09:00:00.000+08:00",
                     "tool_used": {"name": "r62.comp.tool"}, "agent_id": "a", "actor_id": "s",
                     "user_intervention": {"decision": "approve", "decided_by": "user-01",
                                           "decided_at": "2026-11-02T09:01:00.000+08:00",
                                           "modified_fields": [{"name": "limit",
                                                                "old": "1", "new": "2"}]}}

    def test_compensation_rows_keep_zeroed_old_keys(self) -> None:
        analytics.dashboard([self.solo], agg=self.ledger)
        before = analytics_agg.AggLedger(self.ledger).verify()["lines"]
        moved = copy.deepcopy(self.solo)
        moved["user_intervention"] = dict(self.solo["user_intervention"], decision="reject")
        outcome = analytics.dashboard([moved], agg=self.ledger)
        rows = analytics_agg.AggLedger(self.ledger).load()["rows"]
        fresh = analytics_agg.recompute_rows([moved], salt="")
        old_keys = {d["key"] for d in analytics_agg.record_to_deltas(self.solo, salt="")}
        self.assertGreater(int(outcome["agg"].get("compensation_keys") or 0), 0, outcome["agg"])
        self.assertTrue(_rows_equal(rows, fresh))
        self.assertGreater(analytics_agg.AggLedger(self.ledger).verify()["lines"], before)
        for key in old_keys:
            self.assertIn(key, rows, key)
            self.assertEqual(analytics_agg._row_values(rows[key]), (0, 0, 0, 0, []), key)

    def test_compensated_big_population_still_zero_error(self) -> None:
        """大母体里改写一条记录（换日期换决定）：合并行仍等于新全量复算，链照样自证。"""
        work = pathlib.Path(scratch_dir("r62_sampling_comp_big"))
        ledger = FeaturePaths(work / "big").agg_ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        pop, _ = sa.build_population(seed=sa.DEFAULT_SEED, synth=60)
        analytics.dashboard(pop, agg=ledger)
        picked = sa.sample_records(pop, seed=sa.DEFAULT_SEED, n=50)
        pos, rec = picked[0]
        mutated = json.loads(json.dumps(rec))
        moment = analytics._ts(rec)
        stamp = moment.isoformat() if moment else "2026-11-09T12:00:00.000+08:00"
        mutated["user_intervention"] = {"decision": "reject", "decided_by": "user-99",
                                        "decided_at": stamp}
        mutated["timestamp"] = "2026-11-14T23:59:59.000+08:00"
        pop2 = list(pop)
        pop2[pos] = mutated
        second = analytics.dashboard(pop2, agg=ledger)
        rows2 = analytics_agg.AggLedger(ledger).load()["rows"]
        self.assertTrue(second.get("pre_aggregated"), second.get("agg"))
        self.assertTrue(_rows_equal(rows2, analytics_agg.recompute_rows(pop2, salt="")))
        self.assertTrue(analytics_agg.AggLedger(ledger).verify()["ok"])


class FallbackHonestyTests(unittest.TestCase):
    """账本缺失 / 不可信 / 不可复算：如实回退并给出原因码，绝不消费被污染的账本。"""

    def setUp(self) -> None:
        self.work = pathlib.Path(scratch_dir("r62_sampling_fallback"))
        self.pop, _ = sa.build_population(seed=sa.DEFAULT_SEED, synth=40)
        self.ledger = FeaturePaths(self.work / "ok").agg_ledger
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        analytics.dashboard(self.pop, agg=self.ledger)

    def test_ledger_missing_without_records_does_not_build_one(self) -> None:
        empty = FeaturePaths(self.work / "missing").agg_ledger
        empty.parent.mkdir(parents=True, exist_ok=True)
        report = analytics.dashboard([], agg=empty)
        self.assertEqual(report["agg"]["reason"], "ledger_missing_no_records")
        self.assertFalse(empty.exists(), "空账本不许落地")

    def test_one_shot_iterator_falls_back(self) -> None:
        report = analytics.dashboard(iter(self.pop), agg=self.ledger)
        self.assertEqual(report["agg"]["reason"], "input_not_rerunnable")

    def test_agent_and_salt_filters_fall_back(self) -> None:
        self.assertEqual(analytics.dashboard(self.pop, agg=self.ledger,
                                             agent="x")["agg"]["reason"], "agent_filter")
        self.assertEqual(analytics.dashboard(self.pop, agg=self.ledger,
                                             salt="pepper")["agg"]["reason"], "custom_salt")

    def test_tampered_chain_falls_back_and_numbers_match_stream(self) -> None:
        broken = FeaturePaths(self.work / "tampered").agg_ledger
        broken.parent.mkdir(parents=True, exist_ok=True)
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1][:-3] + "9zz"
        broken.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = analytics.dashboard(self.pop, agg=broken)
        self.assertEqual(report["agg"]["reason"], "chain_tampered")
        stream = analytics.dashboard(self.pop)
        for key in sa.METRIC_KEYS:
            self.assertEqual(json.dumps(report.get(key), sort_keys=True),
                             json.dumps(stream.get(key), sort_keys=True), key)


class HonestExclusionTests(SamplingAuditFixture):

    def test_unparsable_and_non_dict_records_are_not_counted(self) -> None:
        """时间戳解析不出来 / 非 dict 记录：整条不进任何键（不补数、不折算成 0）。"""
        rows = analytics_agg.recompute_rows(self.pop + sa.dirty_fixtures()[:2]
                                            + ["not-a-dict", None], salt="")
        self.assertTrue(_rows_equal(rows, self.full),
                        f"脏记录进了计数：{[k for k in set(rows) ^ set(self.full)][:3]}")

    def test_non_dict_records_never_reach_the_contribution_point(self) -> None:
        """口径登记：``record_to_deltas`` 只吃 dict（``dict(record)`` 会抛），
        ``recompute_rows`` 上游的 ``isinstance`` 守卫把非 dict 整条跳过——
        所以调用方（审计、账本、任何复算）都必须先守卫，不许把脏行喂进单点。"""
        for junk in ("not-a-dict", None, 42, ["list-record"]):
            rows = analytics_agg.recompute_rows(self.pop + [junk], salt="")
            self.assertTrue(_rows_equal(rows, self.full), junk)
        self.assertRaises((ValueError, TypeError), analytics_agg.record_to_deltas, "not-a-dict")

    def test_nameless_modified_field_makes_no_heatmap_row(self) -> None:
        """``modified_fields`` 没有 ``name`` → 热力图行不生成，本体行照计（记录确实发生）。"""
        fx = next(f for f in sa.dirty_fixtures()
                  if isinstance(f, dict) and f.get("id") == "dirty-field-no-name")
        deltas = analytics_agg.record_to_deltas(fx, salt="")
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0]["param"], "")
        rows = analytics_agg.recompute_rows(self.pop + [fx], salt="")
        changed = {k: v for k, v in rows.items()
                   if analytics_agg._row_values(v)
                   != analytics_agg._row_values(self.full.get(k, {}))}
        self.assertEqual(len(changed), 1)
        self.assertEqual(list(changed.values())[0]["param"], "")

    def test_out_of_range_record_does_not_move_the_dashboard(self) -> None:
        """区间外记录：读侧按区间过滤，看板数字逐键不变。"""
        work = pathlib.Path(scratch_dir("r62_sampling_range"))
        ledger = FeaturePaths(work / "range").agg_ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        scoped = analytics.dashboard(self.pop, agg=ledger,
                                     range_from="2026-11-01", range_to="2026-11-15")
        oob = analytics.dashboard(self.pop + [sa.out_of_range_fixture()], agg=ledger,
                                  range_from="2026-11-01", range_to="2026-11-15")
        for key in sa.METRIC_KEYS:
            self.assertEqual(json.dumps(scoped.get(key), sort_keys=True),
                             json.dumps(oob.get(key), sort_keys=True), key)

    def test_duplicate_id_collapses_in_the_ledger(self) -> None:
        """同 ``record_key`` 同内容重复出现：水位线折叠，账本不双计（天真复算会双计）。"""
        work = pathlib.Path(scratch_dir("r62_sampling_dup"))
        ledger = FeaturePaths(work / "dup").agg_ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        analytics.dashboard(self.pop, agg=ledger)
        dup = copy.deepcopy(self.pop[0])
        outcome = analytics.dashboard(self.pop + [dup], agg=ledger)
        rows = analytics_agg.AggLedger(ledger).load()["rows"]
        naive = analytics_agg.recompute_rows(self.pop + [dup], salt="")
        self.assertEqual(int(outcome["agg"].get("newly_aggregated") or 0), 0, outcome["agg"])
        self.assertTrue(_rows_equal(rows, self.full))
        self.assertFalse(_rows_equal(naive, self.full),
                         "天真复算本应把重复 id 计两次（这条不符才证明折叠有效）")


class AuditEntrypointTests(unittest.TestCase):
    """审计入口整跑一遍：所有门禁为真、逐条不符 0 条（= 报告里的"误差 0"）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.work = pathlib.Path(scratch_dir("r62_sampling_audit_entry"))
        cls.report = sa.audit(seed=sa.DEFAULT_SEED, sample_n=sa.MIN_SAMPLE,
                              synth=SYNTH, workdir=cls.work / "audit")

    def test_audit_passes_with_zero_error(self) -> None:
        self.assertTrue(self.report["ok"], self.report["errors"])
        self.assertEqual(self.report["errors"], [])
        self.assertEqual(self.report["mismatch_records"], 0)
        self.assertEqual(self.report["row_diff_keys"], 0)
        self.assertEqual(self.report["metric_diff"], [])
        self.assertGreaterEqual(self.report["sample_n"], sa.MIN_SAMPLE)

    def test_audit_records_the_sample_list_for_replay(self) -> None:
        """样本清单（下标 + id）随报告输出：别人换台机器能重跑出同一批。"""
        self.assertEqual(len(self.report["sample_list"]), self.report["sample_n"])
        self.assertEqual(self.report["seed"], sa.DEFAULT_SEED)
        ids = [x["id"] for x in self.report["sample_list"]]
        self.assertTrue(all(ids))
        self.assertEqual(len(self.report["per_record"]), self.report["sample_n"])

    def test_audit_markdown_renders_the_reconciliation_table(self) -> None:
        md = sa.render_markdown(self.report)
        self.assertIn("逐条对账表", md)
        self.assertIn(f"种子：`{sa.DEFAULT_SEED}`", md)
        self.assertGreaterEqual(md.count("\n| "), self.report["sample_n"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
