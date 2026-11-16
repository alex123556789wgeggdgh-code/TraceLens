"""R1-5 套件：逆向注册表补登 + 显式不可逆 + 覆盖率明细与双口径（TASK-R1-5 §2）。

钉住四件事（全部是**行为**断言，不钉行号）：

1. **补登记（可逆方向）**：LLM 生成类（``deepseek-chat`` / ``text-embedding-demo``）
   命中 ``undo_write``，语义 = **记录级补偿**（token 成本不可撤回，见 rollback 模块文档）；
   needle 补齐后 ``book_flight`` 有归宿，且**没有**把 ``notebook.*`` / ``bookmark.*``
   抢成 ``cancel_booking``（G-18 首匹配口径的已知面，逐条如实钉住）。
2. **补登记（显式不可逆方向）**：``payment.transfer`` / ``contract.sign`` /
   ``refund.payment`` → ``reversible()`` 返回不可逆 + **人话阻断文案**；
   ``plan()`` 出 ``ROLLBACK_NOT_REVERSIBLE``（blocker 的 ``reason`` 就是那句文案）、
   闭包成员出 ``ROLLBACK_DEPENDENTS_IRREVERSIBLE``、``execute()`` 拒绝执行。
3. **coverage() 纯增量**：既有三键（``total``/``reversible``/``ratio``）语义与数值
   一字不变；新增 ``uncovered``（逐条 ``{id,tool,reason}``）与 ``buckets``
   （分桶 + 副作用子集口径）；分桶与 ``_covered`` 逐条等价（口径不漂移）。
4. **双口径数值**：主口径 = 真实导入集的「有外部副作用子集」覆盖率（≥ 0.90），
   副口径 = 演示集整集护栏（= 1.0 且零未覆盖）。数值进测试，防"报告写一次、
   代码漂三年"。

临时目录一律走 ``tests/scratch.py``；注册表是**进程内全局表**，本套件登记探针后
必须 ``unregister_inverse`` 清场（``addCleanup``），否则污染同进程的其他套件。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

from agent_explainer import rollback as rb  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
NATIVE_EN = json.loads((ROOT / "samples" / "action_records_en.json")
                       .read_text(encoding="utf-8"))["records"]
TRACE_FILES = ("langfuse.json", "mcp_events.json", "otel_genai.json")


def rec(record_id: str, *, tool: str | None = "notes.write",
        action_type: str = "tool_call", status: str = "succeeded",
        risk: str = "medium", reversible: bool | None = None,
        **extra: object) -> dict:
    """造一条 schema 认得的动作记录（默认"已执行、有工具、来源未标不可逆"）。"""
    row: dict = {
        "id": record_id,
        "action_type": action_type,
        "status": status,
        "risk_level": risk,
        "agent_id": "agent-r15",
        "timestamp": "2026-11-14T00:00:00Z",
        "labels": [],
    }
    if tool:
        row["tool_used"] = {"name": tool, "version": "1.0", "provider": "local",
                            "call_id": f"call-{record_id}",
                            "params_summary": "R1-5 用例", "result_ref": None}
    if reversible is not None:
        row["impact_scope"] = {"targets": [], "money": None, "external_endpoint": None,
                               "data_categories": ["none"], "reversible": reversible,
                               "summary": "R1-5 用例影响面"}
    row.update(extra)
    return row


class RegistryCase(unittest.TestCase):
    """共用一次性数据根；登记过探针工具的用例自己挂 ``addCleanup`` 清场。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-registry-coverage")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def service(self, name: str) -> rb.RollbackService:
        return rb.RollbackService(FeaturePaths(self.root / name))

    def keep_probe(self, tool: str) -> None:
        """登记一个探针工具，并保证用例结束后从全局注册表摘掉。"""
        self.addCleanup(rb.unregister_inverse, tool)
        self.addCleanup(rb._FALLBACK_CACHE.clear)

    def blocker(self, plan: dict, code: str) -> dict:
        hits = [item for item in plan["blockers"] if item["code"] == code]
        self.assertTrue(hits, f"blockers 里没有 {code}：{plan['blockers']}")
        return hits[0]


# --------------------------------------------------------------------------- #
# ① 补登记（可逆方向）：LLM 生成类 + needle 补齐
# --------------------------------------------------------------------------- #

class LlmNeedleTests(RegistryCase):

    def test_r15_llm_generation_tools_map_to_record_level_compensation(self) -> None:
        """G-17 未覆盖的三条 LLM 动作：补登成 ``undo_write``（**记录级补偿**）。"""
        for tool in ("deepseek-chat", "text-embedding-demo"):
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, f"{tool} 仍无注册表条目（G-19 落空未收口）")
            self.assertEqual("undo_write", entry["op"],
                             f"{tool} 的补偿动作必须是 undo_write（记录级回滚）")
            self.assertEqual("exact", entry["source"],
                             f"{tool} 必须是**精确登记**，不能只靠兜底子串")
            self.assertTrue(entry["reversible"], f"{tool} 应判可逆（记录级补偿存在）")
            verdict = rb.reversible(rec("m-llm", tool=tool))
            self.assertTrue(verdict["reversible"], f"{tool} 的 reversible() 必须放行")
            self.assertEqual("undo_write", verdict["op"])
            self.assertIn("已登记逆向动作", verdict["reason"])
            self.assertEqual("mechanism", rb._coverage_bucket(rec("m-llm", tool=tool)),
                             "补登后 LLM 动作应落进「机制覆盖」桶")
            # 展示层契约：inverse_of 仍是两键，调用方拿不到 op 就以为可逆的旁路
            self.assertEqual({"op", "describe_key"}, set(rb.inverse_of(tool)),
                             "inverse_of() 的两键形态是对外契约，不得扩键")

    def test_r15_record_level_compensation_does_not_claim_token_revocation(self) -> None:
        """补偿语义的诚实边界：登记处必须写清"token 成本不可撤回"，也不许有假 op。

        口径说明：这里钉的是**界面事实载体**（op 名 + 文案键 + 登记注释），
        不是对整份源码做朴素子串匹配——"不得写成已撤回生成结果"这类禁令原文
        本身就会出现那串字，朴素匹配只会把纪律本身判红。
        """
        source = (ROOT / "agent_explainer" / "rollback.py").read_text(encoding="utf-8")
        for token in ("token", "记录级补偿", "不可撤回"):
            self.assertIn(token, source,
                          f"登记注释必须写清 undo_write 的补偿边界（缺 {token}）")
        # 不许存在"收回生成结果 / 退回 token"式的假 op（op 名进日志与界面）
        fake_ops = ("revoke_generated", "revoke_generation", "undo_llm",
                    "refund_token", "revoke_chat", "erase_generation")
        registered_ops = {str((rb.inverse_entry_of(tool) or {}).get("op"))
                          for tool in rb.registered_tools()}
        hits = [name for name in fake_ops
                if name in registered_ops or f'"{name}"' in source]
        self.assertEqual([], hits, "存在谎称可收回生成结果的 op 名")
        # LLM 走通用 undo_write 的文案键：不新造"已撤回生成"的界面话术
        self.assertEqual("rollback.inverse.undo_write",
                         rb.inverse_entry_of("deepseek-chat")["describe_key"])
        self.assertEqual("rollback.inverse.undo_write",
                         rb.inverse_entry_of("text-embedding-demo")["describe_key"])

    def test_r15_booking_needle_gives_book_flight_a_home(self) -> None:
        """needle 补齐 ``book``：``book_flight`` 有归宿（= ``cancel_booking``）。"""
        for tool in ("book_flight", "book_room", "book_appointment", "book.flight",
                     "trips.book_hotel", "bookings.create"):
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, f"{tool} 落空：booking needle 未补齐")
            self.assertEqual("cancel_booking", entry["op"],
                             f"{tool} 应映射 cancel_booking")
            self.assertEqual("fallback", entry["source"])
            self.assertTrue(rb.reversible(rec("m-book", tool=tool))["reversible"])

    def test_r15_booking_needle_does_not_hijack_notebook_or_bookmark(self) -> None:
        """G-18 首匹配口径的已知面：动词式 needle 不许把 ``notebook.*`` 抢成取消预订。"""
        self.assertEqual("undo_note", rb.inverse_of("notebook.write")["op"],
                         "裸 book needle 会让 notebook.write 被 cancel_booking 抢走")
        self.assertEqual("undo_write", rb.inverse_of("bookmark.create")["op"],
                         "裸 book needle 会让 bookmark.create 被 cancel_booking 抢走")
        self.assertEqual("undo_note", rb.inverse_of("notes.write")["op"])

    def test_r15_first_match_semantics_are_pinned_as_is(self) -> None:
        """首匹配即终局：既有语义可疑映射本轮**不修**（G-18 遗留），逐条钉现状防漂移。"""
        # calendar.create_event 走 create → undo_write（语义可疑，R1-7 待裁）
        self.assertEqual("undo_write", rb.inverse_of("calendar.create_event")["op"])
        # order.book 走 booking 组的 order → cancel_booking（方向正确）
        self.assertEqual("cancel_booking", rb.inverse_of("order.book")["op"])
        # 表末位的新组不得改写既有组的命中：mail.send 仍是 revoke_message
        self.assertEqual("revoke_message", rb.inverse_of("mail.send")["op"])


# --------------------------------------------------------------------------- #
# ② 补登记（显式不可逆方向）：TP-04 后半句
# --------------------------------------------------------------------------- #

class ExplicitIrreversibleTests(RegistryCase):

    def test_r15_payment_and_contract_are_registered_and_irreversible(self) -> None:
        """登记在案（不是"没登记"）+ ``reversible=False`` + 人话阻断文案。"""
        expected = {
            "payment.transfer": (rb.IRREVERSIBLE_FINANCIAL_OP, "支付"),
            "contract.sign": (rb.IRREVERSIBLE_CONTRACT_OP, "合同"),
            "refund.payment": (rb.IRREVERSIBLE_FINANCIAL_OP, "退款"),
        }
        for tool, (op, keyword) in expected.items():
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, f"{tool} 未登记：落空会被界面读成「名单没登记」，"
                                        f"补一个兜底 needle 就能被「修」掉（G-19 的病根）")
            self.assertEqual(op, entry["op"], f"{tool} 的 op 应是升级流程 {op}，不是 undo_*")
            self.assertFalse(entry["reversible"], f"{tool} 必须判显式不可逆")
            self.assertIn("不可逆", entry["irreversible_reason"],
                          f"{tool} 的阻断文案必须说清「为什么不可逆」")
            self.assertIn(keyword, entry["irreversible_reason"],
                          f"{tool} 的阻断文案必须点名业务对象（{keyword}）")
            self.assertIn("请", entry["irreversible_reason"],
                          f"{tool} 的阻断文案必须给出人工通道（该走哪条流程）")
            # 措辞纪律：文案里必须自带**反宣称**（docs/07 §10 的"已撤销/已恢复"禁令）。
            # 口径说明：不能用"文案里出现'已撤销'就判红"的朴素子串匹配——那句
            # 「不得宣称签署已撤销」本身就是合规的禁令用法；要钉的是"有没有将尽的
            # 完成态谎称"与"有没有自带不得宣称的约束"。
            self.assertIn("不得宣称", entry["irreversible_reason"],
                          f"{tool} 的阻断文案必须自带「不得宣称……」的约束")
            for affirmative in ("已恢复", "已上链", "已冲正完成", "已退款完成", "已解除"):
                self.assertNotIn(affirmative, entry["irreversible_reason"],
                                 f"阻断文案不得出现完成态谎称「{affirmative}」")
            self.assertEqual("exact", entry["source"])
            self.assertIn(tool, rb.registered_tools(),
                          f"{tool} 必须出现在 registered_tools()（登记在案的证据）")

    def test_r15_reversible_reason_is_the_blocking_note(self) -> None:
        """``reversible()`` 的 ``reason`` = 登记时那句人话（界面唯一依据）。"""
        for tool, reason in (("payment.transfer", rb.IRREVERSIBLE_FINANCIAL_REASON),
                             ("contract.sign", rb.IRREVERSIBLE_CONTRACT_REASON),
                             ("refund.payment", rb.IRREVERSIBLE_REFUND_REASON)):
            verdict = rb.reversible(rec("m-irrev", tool=tool, risk="high"))
            self.assertFalse(verdict["reversible"], f"{tool} 必须判不可逆")
            self.assertEqual(reason, verdict["reason"],
                             f"{tool} 的 reason 必须是登记时那句文案，不能是兜底话术")
            self.assertEqual("uncovered_registry_irreversible",
                             rb._coverage_bucket(rec("m-irrev", tool=tool)),
                             f"{tool} 在覆盖率里必须算**未覆盖**（不许靠补登凑指标）")

    def test_r15_source_fact_still_wins_over_registry(self) -> None:
        """W-03 纪律不变：来源明说不可逆时，原因仍引用来源（两个不可逆来源不串味）。"""
        verdict = rb.reversible(rec("m-source", tool="payment.transfer", reversible=False))
        self.assertFalse(verdict["reversible"])
        self.assertIn("impact_scope.reversible=false", verdict["reason"])
        self.assertNotIn("资金转移", verdict["reason"])
        self.assertEqual("uncovered_source_irreversible",
                         rb._coverage_bucket(rec("m-source", tool="payment.transfer",
                                                 reversible=False)))

    def test_r15_plan_blocks_target_with_the_blocking_note(self) -> None:
        """``plan()`` 单目标：``ROLLBACK_NOT_REVERSIBLE``，``reason`` = 人话文案。"""
        rows = [rec("p", tool="payment.transfer", risk="high")]
        plan = rb.plan(rows, "p")
        self.assertFalse(plan["ok"], "不可逆动作的计划层必须不通过")
        self.assertFalse(plan["reversible"])
        blocker = self.blocker(plan, "ROLLBACK_NOT_REVERSIBLE")
        self.assertEqual("p", blocker["action_id"])
        self.assertEqual(rb.IRREVERSIBLE_FINANCIAL_REASON, blocker["reason"],
                         "blocker 必须携带那句人话（前端的回滚按钮文案唯一依据）")

    def test_r15_plan_blocks_closure_member_as_irreversible_dependent(self) -> None:
        """闭包成员不可逆 → ``ROLLBACK_DEPENDENTS_IRREVERSIBLE`` + 成员点名。"""
        rows = [rec("t", tool="notes.write"),
                rec("m", tool="contract.sign", risk="high",
                    parent_action_id="t")]
        plan = rb.plan(rows, "t")
        self.assertFalse(plan["ok"])
        self.assertEqual(["m"], plan["irreversible_dependents"])
        blocker = self.blocker(plan, rb.BLOCKER_DEPENDENTS_IRREVERSIBLE)
        self.assertEqual(["m"], blocker["members"])
        self.assertIn(rb.IRREVERSIBLE_CONTRACT_REASON, blocker["reason"])
        detail = {item["id"]: item for item in blocker["members_detail"]}
        self.assertEqual(rb.IRREVERSIBLE_CONTRACT_REASON, detail["m"]["reason"],
                         "成员级取证必须逐条带那句人话（界面逐条说明原因）")

    def test_r15_execute_refuses_an_irreversible_target(self) -> None:
        """``execute()`` 与计划层同一道闸门：拒绝执行、不落任何终态。"""
        rows = [rec("p", tool="refund.payment", risk="high")]
        service = self.service("execute-refuse")
        with self.assertRaises(rb.RollbackError) as ctx:
            service.execute(rows, "p", actor="local")
        self.assertEqual("ROLLBACK_NOT_REVERSIBLE", ctx.exception.code)
        self.assertEqual(rb.IRREVERSIBLE_REFUND_REASON, ctx.exception.reason,
                         "API 层的 reason 就是阻断文案本身（界面无需二次翻译）")
        self.assertEqual([], service.journal(), "被拒绝的不可逆回滚绝不落撤销日志")

    def test_r15_refund_needle_is_conservative_not_reversible(self) -> None:
        """needle 补齐 ``refund``：同类工具名判**不可逆**，不是凑成 ``undo_*``。"""
        for tool in ("refund.process", "refunds.issue", "payment.gateway_charge",
                     "contract.sign_docs", "e-sign.envelope"):
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, f"{tool} 落空")
            self.assertFalse(entry["reversible"],
                             f"{tool} 落进不可逆兜底表才是保守方向（G-18）")
            self.assertEqual("fallback-irreversible", entry["source"])
            self.assertFalse(rb.reversible(rec("m-n", tool=tool))["reversible"])

    def test_r15_irreversible_needles_avoid_known_collateral_names(self) -> None:
        """needle 精确性（逐条实测钉死）：``transfer`` / ``sign`` 裸词不许当 needle。"""
        # 文件传输与导出是**可逆/只读**面，绝不被判成资金/签署动作
        self.assertNotEqual("fallback-irreversible",
                            (rb.inverse_entry_of("file.transfer") or {}).get("source"),
                            "裸 transfer 会把 file.transfer（文件传输）判成资金动作")
        self.assertNotEqual("fallback-irreversible",
                            (rb.inverse_entry_of("design.export") or {}).get("source"),
                            "design.* 本身含有 sign. / esign. 子串，这些形态禁作 needle")
        self.assertNotEqual("fallback-irreversible",
                            (rb.inverse_entry_of("signage.board_write") or {}).get("source"),
                            "signage（标识牌）不该被签署语义抢走")
        # 已知残余面如实钉住（保守方向：说撤销不掉，绝不谎称可逆）→ G-18 / R1-7 backlog
        self.assertFalse(rb.inverse_entry_of("billing.set_permission")["reversible"],
                         "billing.* 因 billing 落进资金不可逆：保守方向，如实登记")
        self.assertFalse(rb.inverse_entry_of("designing.signing")["reversible"],
                         "designing.*/signing 的已知误伤面：钉现状，不许静默漂成可逆")

    def test_r15_irreversible_registration_requires_the_blocking_note(self) -> None:
        """登记契约：判不可逆**必须**带人话文案；非布尔 ``reversible`` 一律拒登。"""
        self.keep_probe("probe.r15.irreversible")
        with self.assertRaises(rb.RollbackError) as ctx:
            rb.register_inverse("probe.r15.irreversible", op="escalate_x",
                                describe_key="rollback.inverse.x", reversible=False)
        self.assertEqual("ROLLBACK_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("irreversible_reason", ctx.exception.field)
        with self.assertRaises(rb.RollbackError) as ctx:
            rb.register_inverse("probe.r15.irreversible", op="escalate_x",
                                describe_key="rollback.inverse.x", reversible=0)
        self.assertEqual("ROLLBACK_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("reversible", ctx.exception.field)
        # reversible=True 时传入的文案必须丢弃（不给"其实可逆"留自相矛盾的文案）
        rb.register_inverse("probe.r15.irreversible", op="undo_x",
                            describe_key="rollback.inverse.x",
                            irreversible_reason="这条本来就可逆")
        entry = rb.inverse_entry_of("probe.r15.irreversible")
        self.assertTrue(entry["reversible"])
        self.assertNotIn("irreversible_reason", entry)


# --------------------------------------------------------------------------- #
# ③ coverage() 纯增量：明细 + 分桶 + 与 _covered 等价
# --------------------------------------------------------------------------- #

class CoverageDetailTests(RegistryCase):

    def test_r15_legacy_three_keys_are_unchanged(self) -> None:
        """C-1 兼容纪律：``total``/``reversible``/``ratio`` 语义与数值一字不变。"""
        service = self.service("coverage-compat")
        self.assertEqual({"total": 12, "reversible": 12, "ratio": 1.0},
                         {k: service.coverage(NATIVE)[k]
                          for k in ("total", "reversible", "ratio")},
                         "演示集三键基线（R1-1 §3）不得漂移")
        self.assertEqual({"total": 12, "reversible": 12, "ratio": 1.0},
                         {k: service.coverage(NATIVE_EN)[k]
                          for k in ("total", "reversible", "ratio")})
        self.assertEqual({"total": 0, "reversible": 0, "ratio": 0.0},
                         {k: service.coverage([])[k]
                          for k in ("total", "reversible", "ratio")},
                         "空集零除：既有口径不变")

    def test_r15_detail_keys_are_pure_additions(self) -> None:
        """新增键是纯增量：``uncovered`` 逐条 ``{id,tool,reason}``，``buckets`` 带子集口径。"""
        service = self.service("coverage-keys")
        cov = service.coverage([rec("ok", tool="notes.write"),
                                rec("free", tool="payment.transfer", risk="high"),
                                rec("no-tool", tool=None, action_type="error",
                                    status="failed"),
                                rec("unknown", tool="unknown.tool_xyz"),
                                rec("pending", tool="payment.transfer",
                                    status="pending_confirmation"),
                                rec("plan-step", tool="plan", action_type="plan")])
        self.assertEqual({"total", "reversible", "ratio", "uncovered", "buckets"},
                         set(cov), "coverage() 的键集合：三键 + uncovered + buckets")
        self.assertEqual(6, cov["total"])
        self.assertEqual(3, cov["reversible"], "天然覆盖 3 条（未执行 + 只读规划）")
        by_id = {row["id"]: row for row in cov["uncovered"]}
        self.assertEqual({"free", "no-tool", "unknown"}, set(by_id),
                         "未覆盖逐条必须点名：不可逆 / 无工具 / 没命中")
        for row in cov["uncovered"]:
            self.assertEqual({"id", "tool", "reason", "bucket", "bucket_label",
                              "action_type", "status"}, set(row))
            self.assertTrue(row["reason"], "每条未覆盖都必须带人话原因（C-1 硬要求）")
        counts = cov["buckets"]["counts"]
        self.assertEqual(cov["total"], sum(counts.values()),
                         "分桶计数必须等于 total（口径不重不漏）")
        self.assertEqual(set(rb.COVERAGE_BUCKETS), set(counts),
                         "桶键必须与 COVERAGE_BUCKETS 一一对应（含零计数，稳定契约）")
        self.assertEqual(set(rb.COVERAGE_BUCKETS), set(cov["buckets"]["labels"]),
                         "每个桶都要有中文标签（界面直接展示）")
        self.assertEqual({"total", "covered", "uncovered", "ratio", "note"},
                         set(cov["buckets"]["side_effect_subset"]))

    def test_r15_side_effect_subset_is_the_primary_scope(self) -> None:
        """主口径的分母 = 全体 − 天然覆盖两桶；A1 的 ≥0.9 判据挂在这个 ratio 上。"""
        service = self.service("coverage-primary")
        cov = service.coverage(NATIVE)
        counts = cov["buckets"]["counts"]
        natural = sum(counts[key] for key in rb.NATURAL_COVERAGE_BUCKETS)
        side = cov["buckets"]["side_effect_subset"]
        self.assertEqual(cov["total"] - natural, side["total"],
                         "副作用子集分母必须剔掉天然覆盖（G-17：整集 1.0 没有判别力）")
        self.assertGreater(natural, 0, "演示集必须确有天然覆盖成员，否则口径不成立")
        self.assertEqual(counts["mechanism"], side["covered"],
                         "子集覆盖数 = 机制覆盖数（天然覆盖不进分子）")
        self.assertEqual(side["total"] - side["covered"], side["uncovered"])
        self.assertEqual(round(side["covered"] / side["total"], 4), side["ratio"])
        self.assertEqual(1.0, side["ratio"], "演示集副作用子集补登后仍是满覆盖")
        self.assertEqual([], cov["uncovered"])

    def test_r15_bucket_and_covered_are_equivalent_row_by_row(self) -> None:
        """口径唯一性：``_covered`` 与「桶 ∈ 覆盖桶」逐条等价（防两套判据漂移）。"""
        rows = [rec("a", tool="notes.write"),
                rec("b", tool="payment.transfer", risk="high"),
                rec("c", tool="contract.sign", risk="high"),
                rec("d", tool="refund.payment"),
                rec("e", tool="unknown.tool_xyz"),
                rec("f", tool=None, action_type="error", status="failed"),
                rec("g", tool="payment.transfer", status="pending_confirmation"),
                rec("h", tool="plan", action_type="plan"),
                rec("i", tool="notes.write", reversible=False),
                rec("j", tool="deepseek-chat"),
                rec("k", tool="text-embedding-demo"),
                rec("l", tool="book_flight"),
                rec("m", tool="http.get", action_type="read"),
                rec("n", tool="notes.write", rollback_state="rolled_back")]
        for row in rows:
            bucket = rb._coverage_bucket(row)
            self.assertEqual(rb._covered(row),
                             bucket in rb.COVERED_COVERAGE_BUCKETS,
                             f"{row['id']} 的 _covered 与分桶判据不一致：{bucket}")
            self.assertIn(bucket, rb.COVERAGE_BUCKETS, f"{row['id']} 落进未登记的桶")
        # 已回滚行：覆盖率口径**不查** rollback_state（与 R1-1 逐字一致，本轮不动它），
        # 但 reversible() 判它不可重复回滚 —— 两个口径各自的职责边界钉清楚防漂移。
        rolled = rows[13]
        self.assertEqual("mechanism", rb._coverage_bucket(rolled))
        self.assertFalse(rb.reversible(rolled)["reversible"])
        self.assertIn("已经回滚过", rb.reversible(rolled)["reason"])

    def test_r15_coverage_detail_stays_out_of_persistent_contracts(self) -> None:
        """明细不进 schema、不进 ``docs/api_contract.json``（§0.4：签名域零相关）。"""
        schema = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                            .read_text(encoding="utf-8"))
        for key in ("uncovered", "buckets", "side_effect_subset"):
            self.assertNotIn(key, schema["properties"],
                             f"coverage 明细键 {key} 绝不进记录 schema")
        self.assertFalse(schema.get("additionalProperties", True))
        contract = (ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8")
        for token in ('"uncovered"', '"side_effect_subset"', '"COVERAGE_BUCKETS"'):
            self.assertNotIn(token, contract,
                             f"coverage 明细 {token} 不进对外契约（本轮不新增 HTTP 入口）")


# --------------------------------------------------------------------------- #
# ④ 双口径实测数值（主口径 ≥0.9 / 副口径 =1.0）
# --------------------------------------------------------------------------- #

class DualScopeTests(RegistryCase):
    """与 R1-1 m5 同口径：``samples/traces/*.json`` 进程内导入（真实适配器样本）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("rollback-registry-coverage-import")
        cls.service = sv.InterveneService(records=[], features_root=cls.root,
                                          sign_on_write=True)
        for name in TRACE_FILES:
            doc = json.loads((ROOT / "samples" / "traces" / name)
                             .read_text(encoding="utf-8"))
            res = cls.service.import_traces(doc, request_id=f"req-r15-{name}", lang="zh")
            if not res.get("ok"):  # pragma: no cover
                raise AssertionError(f"导入失败 {name}：{res}")
        cls.imported = cls.service.store.all()
        cls.rollback = rb.RollbackService(FeaturePaths(cls.root))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_r15_primary_scope_side_effect_subset_meets_the_bar(self) -> None:
        """主口径：真实导入集的副作用子集覆盖率 ≥ 0.90（R1-5 验收线）。"""
        self.assertEqual(24, len(self.imported),
                         "导入集条数与 R1-1 §3 基线（24 条）不一致")
        cov = self.rollback.coverage(self.imported)
        side = cov["buckets"]["side_effect_subset"]
        self.assertEqual(10, side["total"],
                         "副作用子集分母须为 10（R1-1 §3 同口径，只动分子不动分母）")
        self.assertGreaterEqual(side["ratio"], 0.90,
                                f"主口径未达标：{side}（补登后应为 9/10=0.9）")
        self.assertEqual(9, side["covered"],
                         "补登三条 LLM 后机制覆盖应为 9（G-17 收口）")
        self.assertGreaterEqual(cov["ratio"], 0.90,
                                f"整集 ratio 同步上台阶：{cov['ratio']}")
        self.assertEqual(1, side["uncovered"])
        self.assertEqual(["notifications-message-13"],
                         [row["id"] for row in cov["uncovered"]],
                         "唯一残留：无工具动作的记录（不许为凑指标改判它）")

    def test_r15_guard_scope_demo_sets_stay_at_one(self) -> None:
        """副口径：演示集整集 = 1.0 且零未覆盖（回归护栏，对 A1 无判别力）。"""
        for label, rows in (("zh", NATIVE), ("en", NATIVE_EN)):
            cov = self.rollback.coverage(rows)
            self.assertEqual(1.0, cov["ratio"], f"演示集 {label} 整集护栏破了：{cov}")
            self.assertEqual([], cov["uncovered"], f"演示集 {label} 出现未覆盖项")
            self.assertEqual(1.0, cov["buckets"]["side_effect_subset"]["ratio"],
                             f"演示集 {label} 副作用子集也须满覆盖")

    def test_r15_registry_changes_do_not_touch_the_signature_domain(self) -> None:
        """铁律：注册表与覆盖率是**读侧派生**，签名域 11 字段与历史行零相关。"""
        from agent_explainer import secretariat as sec
        self.assertEqual(("timestamp", "agent_id", "action_type", "tool", "input_hash",
                          "output_hash", "prev_hash", "entry_hash", "risk_level",
                          "confidence", "status"),
                         tuple(sec.SIGNATURE_FIELDS),
                         "SIGNATURE_FIELDS 只读：本轮一个字段都没动")
        cov = self.rollback.coverage(NATIVE)
        self.assertEqual(12, cov["total"], "覆盖率统计不得改写记录（纯读侧）")
        self.assertEqual(NATIVE, json.loads((ROOT / "samples" / "action_records.json")
                                           .read_text(encoding="utf-8"))["records"],
                         "samples/ 演示集必须逐字未动（零历史改写）")


if __name__ == "__main__":  # 手工自检：python tests/test_rollback_registry_coverage.py -v
    unittest.main(verbosity=2)
