#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.0 字段合并名单：静态不变量 + 「名单真的被消费」的行为断言。

为什么要有这个测试
------------------
`server.EXPANSION_MERGE_FIELDS` 与 `server.EXPANSION_PROTECTED_FIELDS` 曾经是**声明**：
合并器没实现，所以没有任何运行期行为能暴露"名单写错"。这种"假闸门"最危险——
等 R5/R6/R7 真的开始写 `confidence_source` / `multimedia_evidence` / `group_id` 时，
错判会直接变成"入参能覆盖签名材料"或"入参能伪造干预历史"。

S0-2 起合并器已实现（`ingest.merge_facts_full`：4.4 白名单 + 5.0 白名单叠加，
受保护字段在合并层剥离，合并产物再签名），本文件因此从「守卫声明」升级为
「钉住实现」：静态四不变量保留，另加行为断言。

静态不变量（原有 7 用例）：
1. 两张名单**无交集**（同一字段不能既允许合并又禁止合并）；
2. 签名材料与决定语义类字段必须在 PROTECTED（导入方带进来只会造出验不过的假签名）；
3. `intervention_history` 在 PROTECTED（R1 裁定：干预每次独立成条 signed 记录）；
4. 5.0 新增字段**全部**落在两张名单之一（新增字段必须归类，不许悬空）。

新增（S0-2）：
- 白名单字段可合并、名单外新键拒绝、受保护字段剥离（含 `intervention_history` /
  `human_owner`）、blank 不覆盖、`changed_fields` 正确；
- 名单是**参数驱动**的（把名单换掉行为就变），证明消费方读的是名单而不是硬编码；
- `server` 与 `ingest` 引用同一份名单与同一份实现（不得两套逻辑）；
- `merge_facts` 的既有 4.4 语义未被削弱；
- 合并落在签名之前：合并产物参与 `entry_hash`/`signature`，改一个字节就验不过。
"""
from __future__ import annotations

import copy
import inspect
import json
import pathlib
import shutil
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in (REPO, REPO / "tests", REPO / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import ingest, server  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

#: 5.0 引入的可选记录字段全集（docs/07 §3.1）。
EXPANSION_FIELDS_5_0 = (
    "agent_pubkey", "human_owner", "delegation_chain", "input_hash", "output_hash",
    "prev_hash", "entry_hash", "signature", "tsa_timestamp", "approval",
    "multimedia_evidence", "causal_explanation", "confidence", "confidence_source",
    "uncertainty", "risk_level", "requires_action", "intervention_history",
    "group_id", "shared_users", "rollback_ref", "rollback_state",
    "improvement_suggestion", "evidence_feedback",
)

#: 绝不能从入参合并的字段：签名材料 + 决定语义 + 来源归因。
MUST_BE_PROTECTED = (
    "signature", "entry_hash", "prev_hash", "input_hash", "output_hash",
    "agent_pubkey", "approval", "tsa_timestamp", "user_intervention",
    "intervention_options", "human_readable", "source", "demo_data", "synthetic",
    "execution_receipt", "intervention_history",
)

# 名单内容的逐字快照（铁律：两张名单内容不改）。写死在这里，改了就会红。
EXPANSION_MERGE_FIELDS_SNAPSHOT = (
    "group_id", "shared_users", "multimedia_evidence", "causal_explanation",
    "rollback_ref", "rollback_state", "improvement_suggestion", "evidence_feedback",
    "confidence_source",
)
EXPANSION_PROTECTED_FIELDS_SNAPSHOT = (
    "user_intervention", "intervention_options", "human_readable", "source",
    "demo_data", "synthetic", "policy", "signature", "entry_hash", "prev_hash",
    "agent_pubkey", "approval", "tsa_timestamp", "input_hash", "output_hash",
    "execution_receipt", "intervention_history",
    "human_owner", "delegation_chain",
)

SCHEMA = json.loads((REPO / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLE = json.loads((REPO / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"][0]

SHAREE_DID = cs.did_from_name("s0-2-sharee", kind="user")
ATTACKER_DID = cs.did_from_name("s0-2-attacker", kind="user")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def base_record(aid: str = "act-s0-2-base", **over) -> dict:
    """演示种子的一条：字段齐全、schema v1.1 合法、未签名。"""
    rec = copy.deepcopy(SAMPLE)
    rec["id"] = aid
    rec["parent_action_id"] = None
    rec.pop("received_at", None)
    rec.update(over)
    return rec


def expansion_values() -> dict:
    """`EXPANSION_MERGE_FIELDS` 九个字段各给一个 schema 合法值。"""
    return {
        "group_id": "grp-s0-2",
        "shared_users": [{"did": SHAREE_DID, "permission": "view",
                          "shared_at": "2026-11-14T00:00:00Z"}],
        "multimedia_evidence": [{"kind": "screenshot", "evidence_id": "ev-s0-2"}],
        "causal_explanation": {"counterfactual": "若当时选「只复制不覆盖」，线上文件不会被改"},
        "rollback_ref": "act-s0-2-original",
        "rollback_state": "none",
        "improvement_suggestion": {"id": "sug-s0-2", "tool": "files.upload",
                                   "param": "overwrite", "direction": "down",
                                   "occurrences": 2, "status": "pending"},
        "evidence_feedback": {"like": 3, "dislike": 1,
                              "updated_at": "2026-11-14T00:00:00Z"},
        "confidence_source": {"method": "logprobs", "logprob": 0.82},
    }


def forged_values() -> dict:
    """`EXPANSION_PROTECTED_FIELDS` 全部字段各给一个伪造值（永不许落进记录）。"""
    return {
        "user_intervention": {"decision": "approve", "reason": "上报方替用户决定"},
        "intervention_options": [{"id": "forged", "kind": "approve",
                                  "label": "替批", "consequence": "直接放行"}],
        "human_readable": {"l1": "上报方自己写的文案", "l2": [], "l3": ""},
        "source": {"kind": "manual"},
        "demo_data": True,
        "synthetic": True,
        "policy": {"name": "forged-policy", "version": "0", "rules": []},
        "signature": "deadbeef" * 8,
        "entry_hash": "0" * 64,
        "prev_hash": "1" * 64,
        "agent_pubkey": "Zm9yZ2VkLXB1YmtleQ==",
        "approval": {"state": "approved"},
        "tsa_timestamp": "2026-01-01T00:00:00Z",
        "input_hash": "2" * 64,
        "output_hash": "3" * 64,
        "execution_receipt": {"state": "executed"},
        "intervention_history": [{"target_action_id": "act-fake",
                                  "decision": "approve", "actor_id": "attacker"}],
        "human_owner": ATTACKER_DID,
        "delegation_chain": [ATTACKER_DID],
    }


# ---------------------------------------------------------------------------
# 静态不变量（原有 7 用例，语义不变）
# ---------------------------------------------------------------------------

class ExpansionFieldListsTest(unittest.TestCase):
    """两张名单的静态不变量。"""

    def test_lists_do_not_overlap(self) -> None:
        overlap = set(server.EXPANSION_MERGE_FIELDS) & set(server.EXPANSION_PROTECTED_FIELDS)
        self.assertEqual(set(), overlap, f"同一字段不能既允许合并又禁止合并：{sorted(overlap)}")

    def test_signature_material_is_protected(self) -> None:
        missing = [name for name in MUST_BE_PROTECTED
                   if name not in server.EXPANSION_PROTECTED_FIELDS]
        self.assertEqual([], missing, f"这些字段必须禁止合并：{missing}")

    def test_merge_list_never_holds_signature_material(self) -> None:
        leaked = [name for name in MUST_BE_PROTECTED
                  if name in server.EXPANSION_MERGE_FIELDS]
        self.assertEqual([], leaked, f"签名材料混进了可合并名单：{leaked}")

    def test_intervention_history_is_derived_only(self) -> None:
        self.assertIn("intervention_history", server.EXPANSION_PROTECTED_FIELDS)
        self.assertNotIn("intervention_history", server.EXPANSION_MERGE_FIELDS)

    def test_every_expansion_field_is_classified(self) -> None:
        classified = set(server.EXPANSION_MERGE_FIELDS) | set(server.EXPANSION_PROTECTED_FIELDS)
        # 4.4 已有的字段（confidence / uncertainty / risk_level / requires_action /
        # human_owner / delegation_chain）归 ingest.MERGE_FIELDS 管，这里只查 5.0 新增项。
        owned_elsewhere = set(ingest.MERGE_FIELDS) | set(ingest.PROTECTED_FIELDS)
        dangling = [name for name in EXPANSION_FIELDS_5_0
                    if name not in classified and name not in owned_elsewhere]
        self.assertEqual([], dangling,
                         f"这些 5.0 字段既不在扩展名单也不在 4.4 名单里：{dangling}")

    def test_ingest_lists_do_not_overlap(self) -> None:
        overlap = set(ingest.MERGE_FIELDS) & set(ingest.PROTECTED_FIELDS)
        self.assertEqual(set(), overlap, f"4.4 名单重叠：{sorted(overlap)}")

    def test_expansion_lists_are_stable_tuples(self) -> None:
        # 顺序无关但类型必须稳：合并器会按元组做成员测试，list 会被误改。
        self.assertIsInstance(server.EXPANSION_MERGE_FIELDS, tuple)
        self.assertIsInstance(server.EXPANSION_PROTECTED_FIELDS, tuple)


# ---------------------------------------------------------------------------
# 单一实现：一份名单、一个合并器（不得两套逻辑）
# ---------------------------------------------------------------------------

class SingleImplementationTest(unittest.TestCase):
    """名单与实现都只有一份：server 与 ingest 指向同一个对象。"""

    def test_server_reexports_the_same_list_objects(self) -> None:
        self.assertIs(server.EXPANSION_MERGE_FIELDS, ingest.EXPANSION_MERGE_FIELDS,
                      "server 与 ingest 必须是同一个名单对象，不能各写一份")
        self.assertIs(server.EXPANSION_PROTECTED_FIELDS, ingest.EXPANSION_PROTECTED_FIELDS)

    def test_list_contents_are_unchanged(self) -> None:
        self.assertEqual(EXPANSION_MERGE_FIELDS_SNAPSHOT, ingest.EXPANSION_MERGE_FIELDS)
        self.assertEqual(EXPANSION_PROTECTED_FIELDS_SNAPSHOT,
                         ingest.EXPANSION_PROTECTED_FIELDS)

    def test_merger_defaults_consume_the_two_lists(self) -> None:
        """合并器的默认接线就是这两张名单（不是硬编码字段名）。"""
        params = inspect.signature(ingest.merge_facts_full).parameters
        self.assertIs(ingest.EXPANSION_MERGE_FIELDS,
                      params["expansion_fields"].default)
        self.assertIs(ingest.MERGE_FIELDS, params["merge_fields"].default)
        self.assertIs(ingest.ALL_PROTECTED_FIELDS, params["protected_fields"].default)
        self.assertTrue(set(ingest.EXPANSION_PROTECTED_FIELDS)
                        <= set(ingest.ALL_PROTECTED_FIELDS))

    def test_merge_facts_is_a_wrapper_over_the_same_impl(self) -> None:
        """`merge_facts` 的既有口径不变：2 元组 + 4.4 语义 + 盖 `received_at`。"""
        before = base_record(status="running")
        incoming = {"status": "succeeded", "result_summary": "已上传 3 个文件",
                    "user_intervention": {"decision": "approve"},
                    "human_readable": {"l1": "上报方文案", "l2": [], "l3": ""}}
        out = ingest.merge_facts(before, incoming)
        self.assertIsInstance(out, tuple)
        self.assertEqual(2, len(out))
        merged, changed = out
        self.assertEqual("succeeded", merged["status"])
        self.assertEqual("已上传 3 个文件", merged["result_summary"])
        self.assertEqual(before["user_intervention"], merged["user_intervention"],
                         "4.4 的语义挡板不得削弱：决定 ≠ 生效")
        self.assertEqual(before["human_readable"], merged["human_readable"])
        self.assertEqual({"status", "result_summary"}, {c["field"] for c in changed})
        self.assertTrue(merged.get("received_at"))


# ---------------------------------------------------------------------------
# 合并行为（§2 验收锚点）
# ---------------------------------------------------------------------------

class ExpansionMergeBehaviourTest(unittest.TestCase):
    """`merge_facts_full` 的白名单 / 剥离 / blank / changed 记账行为。"""

    def merge(self, incoming: dict, before: dict | None = None, **kw):
        base = before if before is not None else base_record()
        return ingest.merge_facts_full(base, incoming, **kw), base

    def test_expansion_whitelist_merges_and_records_changes(self) -> None:
        (merged, changed, stripped), before = self.merge(expansion_values())
        for field, value in expansion_values().items():
            self.assertEqual(value, merged.get(field), f"{field} 应合并进记录")
        names = {c["field"] for c in changed}
        self.assertEqual(set(ingest.EXPANSION_MERGE_FIELDS), names,
                         "changed_fields 应逐条记这九个字段")
        for entry in changed:
            self.assertIn("before", entry)
            self.assertIn("after", entry)
        self.assertEqual([], stripped)
        self.assertEqual([], VALIDATOR.collect(merged), "合并产物必须过 schema v1.1")
        self.assertNotEqual(before, merged)

    def test_field_outside_both_lists_is_never_merged(self) -> None:
        incoming = {"not_in_any_list": "越界字段", "causal_explanation": {"counterfactual": "x"}}
        (merged, changed, _stripped), _before = self.merge(incoming)
        self.assertNotIn("not_in_any_list", merged, "两张名单之外的新键不得合并")
        self.assertNotIn("not_in_any_list", {c["field"] for c in changed})
        self.assertEqual({"counterfactual": "x"}, merged["causal_explanation"])

    def test_protected_fields_are_stripped_and_reported(self) -> None:
        incoming = dict(expansion_values())
        incoming.update(forged_values())
        incoming["id"] = "act-s0-2-injected"
        incoming["schema_version"] = "999-forged"
        (merged, changed, stripped), before = self.merge(incoming)
        self.assertEqual(sorted(ingest.ALL_PROTECTED_FIELDS), sorted(stripped),
                         "入参里出现的受保护字段应逐名上报（不静默吞掉）")
        # 决定语义 / 文案 / 来源 / 演示标记 / policy：保持服务端已有值
        for field in ("user_intervention", "intervention_options", "human_readable",
                      "source", "demo_data", "synthetic", "policy"):
            self.assertEqual(before.get(field), merged.get(field), f"{field} 不得被入参覆盖")
        # 签名材料与责任链：入参值一个都不落进记录
        for field in ("signature", "entry_hash", "prev_hash", "agent_pubkey", "approval",
                      "tsa_timestamp", "input_hash", "output_hash", "execution_receipt",
                      "intervention_history", "human_owner", "delegation_chain"):
            self.assertNotEqual(forged_values()[field], merged.get(field),
                                f"{field} 的伪造值不得进入记录")
        self.assertNotIn("intervention_history", merged)
        self.assertNotIn("signature", merged)
        self.assertNotEqual("act-s0-2-injected", merged["id"], "id 由服务端定，不随入参改")
        self.assertFalse({c["field"] for c in changed} & set(ingest.EXPANSION_PROTECTED_FIELDS),
                         "受保护字段不得出现在 changed_fields 里")
        self.assertEqual([], VALIDATOR.collect(merged))

    def test_blank_expansion_values_do_not_overwrite(self) -> None:
        before = base_record(group_id="grp-existing", rollback_state="rolled_back",
                             causal_explanation={"counterfactual": "已有解释"})
        incoming = {"group_id": "", "rollback_ref": None, "causal_explanation": "",
                    "confidence_source": None, "evidence_feedback": None}
        (merged, changed, _stripped), _b = self.merge(incoming, before=before)
        self.assertEqual("grp-existing", merged["group_id"])
        self.assertEqual("rolled_back", merged["rollback_state"])
        self.assertEqual({"counterfactual": "已有解释"}, merged["causal_explanation"])
        self.assertNotIn("rollback_ref", merged)
        self.assertEqual(set(), {c["field"] for c in changed} & set(ingest.EXPANSION_MERGE_FIELDS),
                         "blank 值不算变化（与 4.4 同款语义）")

    def test_equal_canonical_value_is_not_a_change(self) -> None:
        values = expansion_values()
        (first, _c1, _s1), _before = self.merge(values)
        (again, changed2, _s2), _b = self.merge(values, before=first)
        self.assertEqual(set(), {c["field"] for c in changed2} & set(values),
                         "canonical 相同 → 不记变化（重复上报不制造假变化）")
        self.assertEqual(first["causal_explanation"], again["causal_explanation"])

    def test_4_4_merge_still_wins_for_its_own_fields(self) -> None:
        """叠加不削弱：4.4 字段照常合并，两层字段一次调用一起合。"""
        incoming = {"status": "succeeded", "latency_ms": 220, "labels": ["s0-2"],
                    **expansion_values()}
        (merged, changed, _stripped), _before = self.merge(
            incoming, before=base_record(status="running"))
        names = {c["field"] for c in changed}
        self.assertIn("status", names)
        self.assertIn("latency_ms", names)
        self.assertIn("labels", names)
        self.assertTrue(set(ingest.EXPANSION_MERGE_FIELDS) <= names)

    def test_merge_follows_the_lists_not_hardcoded_names(self) -> None:
        """把名单换掉行为就变 → 证明消费方读的是名单。"""
        incoming = expansion_values()
        (only_group, _c, _s), _b = self.merge(incoming, expansion_fields=("group_id",))
        self.assertEqual("grp-s0-2", only_group["group_id"])
        self.assertNotIn("causal_explanation", only_group,
                         "名单里没有的字段不得被合并（不得硬编码字段名）")
        (none_merged, _c2, _s2), _b = self.merge(incoming, expansion_fields=())
        for field in expansion_values():
            self.assertNotIn(field, none_merged)

    def test_strip_layer_is_the_gate(self) -> None:
        """剥离层是真闸门：关掉剥离并显式允许，签名材料才会进；默认接线拦住它。"""
        (loose, _c, _s), _b = self.merge({"signature": "deadbeef"},
                                         merge_fields=("signature",),
                                         protected_fields=())
        self.assertEqual("deadbeef", loose.get("signature"))
        (strict, _c2, stripped2), _b = self.merge({"signature": "deadbeef"},
                                                  merge_fields=("signature",))
        self.assertNotIn("signature", strict, "默认口径下签名材料由剥离层拦下")
        self.assertEqual(["signature"], stripped2)


# ---------------------------------------------------------------------------
# 合并落在签名之前（合并产物参与 entry_hash / signature）
# ---------------------------------------------------------------------------

class MergeBeforeSignatureTest(unittest.TestCase):
    """导入路径的口径：合并 → 签名 → 入库（签名覆盖合并后的事实）。"""

    def setUp(self) -> None:
        self.root = scratch_dir("merge-fields-signing")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.svc = server.InterveneService(
            records=[base_record()], sign_on_write=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl")

    def merge_sign_and_store(self, incoming: dict) -> dict:
        aid = str(base_record()["id"])
        first = self.svc.store.get(aid)
        signed_first = self.svc.sign_on_write_record(first, None)
        self.svc.store.put(signed_first)
        before = self.svc.store.get(aid)
        merged, _changed, _stripped = ingest.merge_facts_full(before, incoming)
        signed = self.svc.sign_on_write_record(merged, before)
        self.svc.store.put(signed)
        return self.svc.store.get(aid)

    def test_merged_expansion_fields_survive_signing_and_verify(self) -> None:
        incoming = dict(expansion_values())
        incoming.update(forged_values())
        incoming["status"] = "succeeded"
        stored = self.merge_sign_and_store(incoming)
        # 合并进来了
        for field, value in expansion_values().items():
            self.assertEqual(value, stored.get(field), f"{field} 应留在签名后的记录里")
        self.assertEqual("succeeded", stored["status"])
        # 过 schema + 签名有效（local 模式）
        self.assertEqual([], VALIDATOR.collect(stored), "签名后的合并记录必须过 schema v1.1")
        self.assertTrue(stored.get("signature"))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], checks)
        self.assertEqual("valid", checks["entry_hash"], checks)
        self.assertIn(checks["prev_hash"], ("continuous", "genesis"), checks)

    def test_forged_owner_cannot_assign_responsibility_to_someone_else(self) -> None:
        """受保护字段被剥离 → 签名照做，责任链的根由服务端按身份账本填。"""
        incoming = dict(forged_values())
        incoming["result_summary"] = "合并后的事实"
        stored = self.merge_sign_and_store(incoming)
        self.assertNotEqual(ATTACKER_DID, stored.get("human_owner"),
                            "入参不能替别人派责任（剥离后由身份账本填）")
        self.assertNotIn("intervention_history", stored, "干预历史永不从入参落盘")
        self.assertEqual("合并后的事实", stored["result_summary"])
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"],
                         "剥离伪造责任链后签名必须照做（不因入参脏而失败）")
        self.assertEqual([], VALIDATOR.collect(stored))

    def test_merged_values_are_covered_by_the_entry_hash(self) -> None:
        """合并产物参与 `entry_hash`：签名后改一个扩展字段 → 整条记录验不过。

        口径如实：`SIGNATURE_FIELDS`（11 项，一字未动）覆盖
        `timestamp/agent_id/action_type/tool/input_hash/output_hash/prev_hash/
        entry_hash/risk_level/confidence/status`；5.0 扩展字段进的是**整条记录体的
        `entry_hash`**。两者是链在一起的：`entry_hash` 本身在签名里。
        """
        stored = self.merge_sign_and_store(expansion_values())
        tampered = copy.deepcopy(stored)
        tampered["causal_explanation"] = {"counterfactual": "签名之后偷偷改掉的反事实"}
        result = self.svc.secretariat.verify_record(tampered)
        self.assertFalse(result["ok"])
        self.assertEqual("invalid", result["checks"]["entry_hash"])

    def test_signature_binds_the_entry_hash_of_the_merged_record(self) -> None:
        """换掉 `entry_hash` 字段本身 → 签名同时验不过（合并结果被签名链锁住）。"""
        stored = self.merge_sign_and_store(expansion_values())
        forged = copy.deepcopy(stored)
        forged["entry_hash"] = "f" * 64
        result = self.svc.secretariat.verify_record(forged)
        self.assertFalse(result["ok"])
        self.assertEqual("invalid", result["checks"]["signature"])
        self.assertEqual("invalid", result["checks"]["entry_hash"])


if __name__ == "__main__":  # pragma: no cover - 人工自检入口
    raise SystemExit(unittest.main(verbosity=2))
