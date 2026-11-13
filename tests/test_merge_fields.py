#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5.0 字段合并白名单的守卫（declare-only → 一旦有人实现合并，这里先拦住错判）。

为什么要有这个测试
------------------
`server.EXPANSION_MERGE_FIELDS` 与 `server.EXPANSION_PROTECTED_FIELDS` 目前是**声明**：
合并器还没实现，所以没有任何运行期行为能暴露"名单写错"。这种"假闸门"最危险——
等 R5/R6/R7 真的开始写 `confidence_source` / `multimedia_evidence` / `group_id` 时，
错判会直接变成"入参能覆盖签名材料"或"入参能伪造干预历史"。

本文件把四条不变量钉死：
1. 两张名单**无交集**（同一字段不能既允许合并又禁止合并）；
2. 签名材料与决定语义类字段必须在 PROTECTED（导入方带进来只会造出验不过的假签名）；
3. `intervention_history` 在 PROTECTED（R1 裁定：干预每次独立成条 signed 记录）；
4. 5.0 新增字段**全部**落在两张名单之一（新增字段必须归类，不许悬空）。

第 4 条是给后续子任务的：谁往记录里加字段，这里就会红一次，逼他明确"可合并 / 不可合并"。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_explainer import ingest, server  # noqa: E402

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


class ExpansionFieldListsTest(unittest.TestCase):
    """两张名单的静态不变量。"""

    def test_lists_do_not_overlap(self) -> None:
        overlap = set(server.EXPANSION_MERGE_FIELDS) & set(server.EXPANSION_PROTECTED_FIELDS)
        self.assertEqual(set(), overlap, f"同一字段不能既可合并又受保护：{sorted(overlap)}")

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


if __name__ == "__main__":  # pragma: no cover - 人工自检入口
    raise SystemExit(unittest.main(verbosity=2))
