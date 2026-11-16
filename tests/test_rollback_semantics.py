"""R1-2a 回滚语义回归：把 PM-VERDICT-R1-1.md §2 的七项裁决钉成可重跑断言。

运行::

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest discover -s tests -p "test_rollback_semantics.py"

**骨架定位**：本文件由 R1-2a 建立，只覆盖"改动语义本身"的最小断言；
TP-01~32 的完整清单由 R1-7 QA 扩写（REPORT-R1-1.md §5）。

用例 → 裁决 / TP 映射（口径出处：PM-VERDICT-R1-1.md §2、REPORT-R1-1.md §2 B 节）：

* ``D-closure`` / **TP-16**：闭包排除 ``action_type ∈ {intervention, rollback}``
* ``D-irrev``  / **TP-23**：不可逆成员进 blockers（新码
  ``ROLLBACK_DEPENDENTS_IRREVERSIBLE``），且绝不进执行范围
* **G-6** / **TP-20**：``plan`` 与 ``execute`` 共用撤销日志幂等屏障（同码、不自相矛盾）
* **G-7** / **TP-21**：已回滚（日志在案）的闭包成员不再重入
* **G-9** / **TP-24**：``truncated ⇒ requires_confirmation``；``execute`` 支持 ``max_depth`` 同口径
* **G-10** / **TP-25**：循环依赖输出 ``cycle_detected``（不静默、不无限递归）
* **G-16** / **TP-26**：journal 不可读 → ``rollback.*`` 码族（不逃逸 ``FEATURE_STORE_*``）
* ``D-α`` / **TP-13 + TP-14 + TP-22**：``execute`` 副本**零改写**——回滚后原记录
  （含干预记录）``verify_record`` 仍 ``ok=true``（口径 α 的核心断言）

临时数据根一律走 ``tests/scratch.py``（本机系统临时目录 ACL 不可写时会退回仓库
``var/``，与基线那 1 个环境噪声 error 同一口径）。
"""
from __future__ import annotations

import copy
import inspect
import json
import pathlib
import shutil
import sys
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

from agent_explainer import rollback as rb  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer.feature_store import (  # noqa: E402
    FeaturePaths,
    canonical_json,
)

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
WEB = ROOT / "agent_explainer" / "web"
TARGET_ID = "act-r12a-target"


# --------------------------------------------------------------------------- #
# 造数据的小工具（只用 schema 认识的字段，不造半成品记录）
# --------------------------------------------------------------------------- #

def rec(record_id: str, *, tool: str = "notes.write", action_type: str = "write",
        status: str = "succeeded", risk: str = "low", parent: str | None = None,
        depends_on: list[str] | None = None, reversible: bool | None = None,
        **extra: object) -> dict:
    row: dict = {
        "id": record_id,
        "action_type": action_type,
        "status": status,
        "risk_level": risk,
        "agent_id": "agent-r12a",
        "timestamp": "2026-11-14T00:00:00Z",
        "tool_used": {"name": tool, "version": "1.0", "provider": "local",
                      "call_id": f"call-{record_id}", "params_summary": "R1-2a 用例",
                      "result_ref": None},
        "labels": [],
    }
    if parent is not None:
        row["parent_action_id"] = parent
    if depends_on is not None:
        row["context"] = {"depends_on": list(depends_on)}
    if reversible is not None:
        row["impact_scope"] = {"targets": [], "money": None, "external_endpoint": None,
                               "data_categories": ["none"], "reversible": reversible,
                               "summary": "R1-2a 用例影响面"}
    row.update(extra)
    return row


def new_signed_record() -> dict:
    """一条可入库、可签名、可被干预的记录（照 REPORT-R1-1 §8 的 m4 写法）。"""
    base = copy.deepcopy(next(r for r in NATIVE if r["id"] == "act-demo-0004"))
    base["id"] = TARGET_ID
    base["action_type"] = "write"
    base["tool_used"] = {"name": "notes.write", "version": "1.0", "provider": "local",
                         "call_id": "r12a-chain", "params_summary": "写入笔记（口径 α 实测）",
                         "result_ref": None}
    base["status"] = "pending_confirmation"
    base["risk_level"] = "medium"
    base["requires_action"] = True
    base["requires_action_reason"] = "R1-2a：等待人工决定"
    base["labels"] = ["r12a", "alpha-probe"]
    base["impact_scope"] = {"targets": [{"kind": "file", "ref": "notes/r12a.md",
                                         "change": "create", "count": 1}],
                            "money": None, "external_endpoint": None,
                            "data_categories": ["none"], "reversible": True,
                            "summary": "写入一条笔记（R1-2a 口径 α 实测）"}
    for key in ("user_intervention", "rollback_state", "signature", "entry_hash",
                "prev_hash", "agent_pubkey", "human_owner", "approval",
                "tsa_timestamp", "sequence_no"):
        base.pop(key, None)
    return base


def _verify_verdict(result: dict) -> tuple:
    """把 verify_record 的结论收敛成可比较的元组（回滚前后必须完全一致）。"""
    checks = result.get("checks") or {}
    problems = tuple(sorted(str(item.get("code"))
                            for item in (result.get("problems") or [])
                            if isinstance(item, dict)))
    return (bool(result.get("ok")), str(checks.get("signature")),
            str(checks.get("entry_hash")), problems)


def http_post(base: str, path: str, body: object,
              headers: dict[str, str] | None = None) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"<raw>": raw}


class ServiceCase(unittest.TestCase):
    """共用一个一次性数据根（``FeaturePaths`` 指向 scratch，绝不写仓库 ``var/``）。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-semantics")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def paths(self, name: str) -> FeaturePaths:
        return FeaturePaths(self.root / name)

    def service(self, name: str) -> rb.RollbackService:
        return rb.RollbackService(self.paths(name))

    def codes(self, plan: dict) -> list[str]:
        return [item["code"] for item in plan["blockers"]]

    def blocker(self, plan: dict, code: str) -> dict:
        hits = [item for item in plan["blockers"] if item["code"] == code]
        self.assertTrue(hits, f"blockers 里没有 {code}：{plan['blockers']}")
        return hits[0]


# --------------------------------------------------------------------------- #
# D-closure / D-irrev / G-9 / G-10：闭包治理（纯函数层，无落盘）
# --------------------------------------------------------------------------- #

class ClosureGovernanceTests(ServiceCase):

    def test_tp16_intervention_and_rollback_records_are_outside_the_closure(self) -> None:
        """TP-16 / D-closure：干预决定与回滚补偿记录不进撤销闭包，也不参与截断。"""
        rows = [rec("t"),
                rec("iv", tool="plan", action_type="intervention", parent="t"),
                rec("rbr", tool="plan", action_type="rollback", parent="t"),
                rec("child", parent="t")]
        info = rb.plan(rows, "t")
        self.assertEqual(["child"], info["dependents"],
                         "干预/回滚记录被算成依赖闭包成员（P0-4 未修）")
        self.assertEqual({"iv", "rbr"}, set(info["excluded_members"]))
        self.assertEqual(["child", "t"], info["order"])
        self.assertTrue(info["requires_confirmation"], "有真实依赖时必须二次确认")
        notice = [n for n in info["notices"]
                  if n["code"] == rb.NOTICE_MEMBERS_EXCLUDED]
        self.assertTrue(notice, "闭包排除必须留提示，不得静默")
        self.assertEqual({"iv", "rbr"}, set(notice[0]["action_ids"]))

    def test_tp16_excluded_records_do_not_fake_truncation(self) -> None:
        """TP-16 / G-9：干预记录在 max_depth 之外，不得虚报"截断了依赖"。"""
        rows = [rec("t"), rec("iv", tool="plan", action_type="intervention", parent="t")]
        info = rb.plan(rows, "t", max_depth=0)
        self.assertEqual([], info["dependents"])
        self.assertEqual([], info["truncated_members"])
        self.assertFalse(info["truncated"],
                         "只有干预记录在下一层：不算截断（截断只说真话）")

    def test_tp23_irreversible_member_goes_to_blockers_and_never_executes(self) -> None:
        """TP-23 / D-irrev：不可逆成员进新码 blockers，且绝不进执行范围与日志 affected。"""
        rows = [rec("t"),
                rec("m", tool="filesystem.remove", action_type="delete",
                    risk="critical", parent="t", reversible=False)]
        info = rb.plan(rows, "t")
        self.assertFalse(info["ok"])
        self.assertIn(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, self.codes(info))
        self.assertEqual(["m"], info["irreversible_dependents"])
        blocker = self.blocker(info, rb.BLOCKER_DEPENDENTS_IRREVERSIBLE)
        self.assertEqual(["m"], blocker["members"], "blocker 必须附成员清单")
        self.assertTrue(blocker["members_detail"][0]["reason"])
        self.assertNotIn("m", info["order"], "不可逆成员不得进入回滚执行顺序")

        svc = self.service("tp23")
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(rows, "t", actor="local", reason="不可逆闭包")
        self.assertEqual(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, ctx.exception.code)
        self.assertEqual(["m"], ctx.exception.detail["members"])

        out = svc.execute(rows, "t", actor="local", reason="只回滚可逆部分",
                          confirm_all=True)
        self.assertEqual(["t"], out["affected"], "不可逆成员被算进执行范围")
        self.assertNotIn("m", [row["id"] for row in out["records"]])
        self.assertEqual(["m"], out["blocked_members"])
        entry = svc.journal()[-1]
        self.assertEqual(["m"], entry["blocked_members"], "日志必须记下谁没被处理")
        self.assertEqual(["t"], entry["affected"])

    def test_tp23_member_without_inverse_is_also_blocked(self) -> None:
        """D-irrev 口径与 coverage 对齐：已执行且注册表无逆向动作的成员同样撤销不掉。"""
        rows = [rec("t"),
                rec("m", tool="payment.transfer", parent="t", risk="high")]
        info = rb.plan(rows, "t")
        self.assertIn(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, self.codes(info))
        self.assertEqual(["m"], info["irreversible_dependents"])

    def test_tp23_not_executed_and_read_only_members_do_not_block(self) -> None:
        """D-irrev 不得扩大化：未执行 / 只读成员没有外部副作用，不该变成阻断。"""
        rows = [rec("t"),
                rec("pend", tool="payment.transfer", status="pending_confirmation",
                    parent="t"),
                rec("read", tool="http.get", action_type="read", parent="t")]
        info = rb.plan(rows, "t")
        self.assertEqual([], info["irreversible_dependents"])
        self.assertEqual({"pend", "read"}, set(info["dependents"]))
        self.assertEqual({"pend", "read", "t"}, set(info["order"]))
        self.assertEqual("t", info["order"][-1], "目标必须在执行顺序末位")

    def test_tp24_truncated_implies_requires_confirmation(self) -> None:
        """TP-24 / G-9：truncated=true 必然 requires_confirmation=true，且列出被丢成员。"""
        rows = [rec("t"), rec("m1", parent="t"), rec("m2", parent="m1")]
        for depth, dropped in ((0, ["m1", "m2"]), (1, ["m2"])):
            info = rb.plan(rows, "t", max_depth=depth)
            self.assertTrue(info["truncated"], f"max_depth={depth} 应报截断")
            self.assertTrue(info["requires_confirmation"],
                            f"max_depth={depth}：truncated 必须要求二次确认")
            self.assertTrue(set(dropped) & set(info["truncated_members"]),
                            f"max_depth={depth} 必须列出被丢弃的成员")
            codes = [n["code"] for n in info["notices"]]
            self.assertIn(rb.NOTICE_DEPENDENTS_TRUNCATED, codes, "截断必须留提示")
        # 深度足够时不报截断（不虚报）
        self.assertFalse(rb.plan(rows, "t")["truncated"])

    def test_tp24_execute_accepts_max_depth_and_gates_on_confirmation(self) -> None:
        """TP-24 / G-9：execute 支持 max_depth 形参，与 plan 同口径；截断未确认不得静默执行。"""
        params = inspect.signature(rb.RollbackService.execute).parameters
        self.assertIn("max_depth", params, "execute 不接受 max_depth：预览深度与执行闭包无法对齐")
        self.assertEqual(rb.DEFAULT_MAX_DEPTH, params["max_depth"].default)
        rows = [rec("t"), rec("m1", parent="t"), rec("m2", parent="m1")]
        svc = self.service("tp24")
        preview = rb.plan(rows, "t", max_depth=0)
        self.assertEqual(["t"], preview["order"])
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(rows, "t", actor="local", reason="depth=0 预览", max_depth=0)
        self.assertEqual("ROLLBACK_DEPENDENTS_PENDING", ctx.exception.code)
        self.assertIn("max_depth", ctx.exception.detail)
        out = svc.execute(rows, "t", actor="local", reason="确认只回滚浅层",
                          confirm_all=True, max_depth=0)
        self.assertEqual(preview["order"], out["order"],
                         "预览深度与执行闭包必须对齐（G-9）")
        self.assertEqual(["t"], out["affected"])
        self.assertEqual(0, svc.journal()[-1]["max_depth"])

    def test_tp25_cycle_dependency_is_reported_not_silent(self) -> None:
        """TP-25 / G-10：循环依赖必须输出 cycle_detected，且 plan 不无限递归。"""
        rows = [rec("c1", parent="c2"), rec("c2", parent="c1")]
        info = rb.plan(rows, "c1")
        self.assertTrue(info["cycle_detected"], "循环依赖被静默吞掉")
        self.assertEqual({"c1", "c2"}, set(info["cycle_members"]))
        self.assertIn(rb.NOTICE_CYCLE_DETECTED,
                      [n["code"] for n in info["notices"]])
        # 环不阻断执行顺序的构造：目标永远在末位，成员不重复
        self.assertEqual(info["order"][-1], "c1")
        self.assertEqual(len(info["order"]), len(set(info["order"])))

    def test_self_dependency_reports_cycle(self) -> None:
        rows = [rec("s", parent="s")]
        info = rb.plan(rows, "s")
        self.assertTrue(info["cycle_detected"])
        self.assertEqual(["s"], info["cycle_members"])

    def test_module_level_plan_stays_pure_and_backward_compatible(self) -> None:
        """铁律 3：既有合法调用（位置参数 + 既有键）不得破坏；plan 全程只读。"""
        rows = copy.deepcopy(NATIVE)
        snapshot = copy.deepcopy(rows)
        info = rb.plan(rows, "act-demo-0007")
        for key in ("ok", "action", "target", "dependents", "order", "reversible",
                    "blockers", "requires_confirmation", "truncated"):
            self.assertIn(key, info, f"plan 丢了既有字段 {key}")
        self.assertEqual(snapshot, rows, "plan 就地修改了传入记录")


# --------------------------------------------------------------------------- #
# G-6 / G-7 / G-16 + α 零改写（涉及撤销日志）
# --------------------------------------------------------------------------- #

class JournalBarrierTests(ServiceCase):

    def test_tp20_plan_and_execute_share_the_undo_journal_barrier(self) -> None:
        """TP-20 / G-6：二次回滚在 plan 与 execute 两侧报同一个码，plan 不再自相矛盾。"""
        rows = [rec("t"), rec("m", parent="t")]
        svc = self.service("tp20")
        first = svc.execute(rows, "t", actor="local", reason="第一次回滚",
                            confirm_all=True)
        self.assertTrue(first["ok"])

        second = svc.plan(rows, "t")
        self.assertFalse(second["ok"], "plan 读不到撤销日志：与 execute 口径不一致（G-6）")
        self.assertIn(rb.BLOCKER_ALREADY_ROLLED_BACK, self.codes(second))
        self.assertEqual("journal", self.blocker(second, rb.BLOCKER_ALREADY_ROLLED_BACK)["source"])

        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(rows, "t", actor="local", reason="第二次回滚", confirm_all=True)
        self.assertEqual(rb.BLOCKER_ALREADY_ROLLED_BACK, ctx.exception.code)
        inner = ctx.exception.detail["plan"]
        self.assertIn(rb.BLOCKER_ALREADY_ROLLED_BACK, self.codes(inner),
                      "detail.plan.blockers 与异常码不一致：plan 与 execute 不同口径")

    def test_tp21_rolled_back_members_do_not_re_enter_the_closure(self) -> None:
        """TP-21 / G-7：日志在案的成员不再重入后续回滚（跳过 + 提示，不重复处理）。"""
        rows = [rec("t"), rec("m1", parent="t"), rec("m2", parent="m1")]
        svc = self.service("tp21")
        svc.execute(rows, "m1", actor="local", reason="先回滚子树 m1", confirm_all=True)
        info = svc.plan(rows, "t")
        self.assertIn("m1", info["skipped_already_rolled_back"])
        self.assertEqual([], info["dependents"], "已回滚成员被重复卷入闭包")
        self.assertEqual(["t"], info["order"])
        self.assertIn(rb.NOTICE_MEMBERS_ALREADY_ROLLED_BACK,
                      [n["code"] for n in info["notices"]])

    def test_tp21_record_derived_state_is_honoured_for_legacy_rows(self) -> None:
        """G-7 兼容口径：历史行里已有的 rollback_state=rolled_back 也当作"已在案"。"""
        rows = [rec("t"), rec("m", parent="t", rollback_state="rolled_back")]
        info = rb.plan(rows, "t")
        self.assertIn("m", info["skipped_already_rolled_back"])
        self.assertEqual([], info["dependents"])

    def test_tp26_journal_unreadable_raises_rollback_codes(self) -> None:
        """TP-26 / G-16：journal 读不动 → rollback.* 码族，绝不逃逸 FEATURE_STORE_*。"""
        paths = self.paths("tp26")
        paths.ensure()
        paths.rollback_journal.mkdir(parents=True)   # 日志路径变成目录 → 读必失败
        svc = rb.RollbackService(paths)
        rows = [rec("t")]
        for label, call in (("journal", svc.journal),
                            ("plan", lambda: svc.plan(rows, "t")),
                            ("execute", lambda: svc.execute(rows, "t", actor="local"))):
            with self.subTest(label=label):
                with self.assertRaises(rb.RollbackError) as ctx:
                    call()
                self.assertEqual(rb.CODE_JOURNAL_UNREADABLE, ctx.exception.code)
                self.assertTrue(ctx.exception.code.startswith("ROLLBACK_"))
                self.assertNotEqual("FEATURE_STORE_READ_FAILED", ctx.exception.code)
                self.assertTrue(ctx.exception.detail.get("cause"))

    def test_alpha_execute_copies_are_byte_identical_to_the_originals(self) -> None:
        """D-α 核心：execute 返回的副本逐字段等于原记录（零改写），入参也不被就地改。"""
        rows = [rec("t"), rec("m", parent="t")]
        snapshot = copy.deepcopy(rows)
        svc = self.service("alpha")
        out = svc.execute(rows, "t", actor="local", reason="α 零改写",
                          confirm_all=True)
        self.assertEqual(snapshot, rows, "execute 就地修改了传入记录")
        by_id = {row["id"]: row for row in snapshot}
        self.assertEqual({"t", "m"}, {row["id"] for row in out["records"]})
        for row in out["records"]:
            self.assertEqual(by_id[row["id"]], row, f"{row['id']} 副本被改写")
            self.assertEqual(canonical_json(by_id[row["id"]]), canonical_json(row),
                             f"{row['id']} 规范 JSON 字节变了：签名必失效")
            self.assertNotEqual("rolled_back", row.get("rollback_state"))
            self.assertNotIn("rolled-back", row.get("labels") or [])

    def test_alpha_journal_still_keeps_the_pre_state_snapshot(self) -> None:
        """α 只砍"写回记录"，不动 journal 前态快照（A2 唯一证据链的起点）。"""
        rows = [rec("t", labels=["keep-me"], risk="high")]
        svc = self.service("alpha-snapshot")
        before = copy.deepcopy(rows[0])
        out = svc.execute(rows, "t", actor="local", reason="快照语义核对")
        entry = svc.journal()[-1]
        self.assertEqual(before, entry["snapshot"], "journal 快照必须是回滚前状态")
        self.assertEqual(out["snapshot"], entry["snapshot"])
        self.assertEqual(["keep-me"], entry["snapshot"]["labels"])


# --------------------------------------------------------------------------- #
# D-α 的真实链：签名 → 干预 → 回滚 → 逐条复算验签（A3 / P0-3 / P0-4 的正向断言）
# --------------------------------------------------------------------------- #

class SignedChainAlphaTests(unittest.TestCase):
    """口径 α 的终局证据：回滚之后，已签名记录（含干预记录）验签必须全绿。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-alpha-chain")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def serve(self) -> sv.TraceLensServer:
        server, thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(NATIVE), web_dir=WEB,
            allow_reset=True, quiet=True,
            features_root=self.root / "features", sign_on_write=True,
            audit_log_path=None, records_store_path=None,
            settings_path=self.root / "settings.json", settings_writable=False,
            ingest_token_path=self.root / "ingest-tokens.json")
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(3)))
        return server

    def test_alpha_clean_signed_chain_stays_verifiable(self) -> None:
        """TP-14 直证：签名记录回滚前后**同一条**验签都必须 ok=true（口径 α 的全部要求）。"""
        server = self.serve()
        svc = server.service
        created = svc.store.put(svc.sign_on_write_record(new_signed_record()))
        self.assertTrue(created.get("signature"), "记录未签名，无法验证 P0-3")
        before = svc.secretariat.verify_record(created)
        self.assertTrue(before["ok"], before.get("problems"))

        plan = svc.rollback_plan_payload(TARGET_ID)["plan"]
        self.assertEqual([], plan["dependents"])
        out = svc.rollback_execute({"action_id": TARGET_ID, "reason": "α 干净链",
                                    "confirm_all": True},
                                   actor_id="local-user", request_id="req-r12a-clean")
        self.assertTrue(out["ok"])

        after = svc.store.get(TARGET_ID)
        self.assertEqual(canonical_json(created), canonical_json(after),
                         "回滚改写了已签名记录的字节（P0-3 未修）")
        self.assertNotEqual("rolled_back", after.get("rollback_state"))
        self.assertNotIn("rolled-back", after.get("labels") or [])
        check = svc.secretariat.verify_record(after)
        self.assertTrue(check["ok"], check.get("problems"))
        # 状态改由读侧派生：回滚记录 rollback_ref 指回目标
        self.assertEqual(TARGET_ID, out["rollback_record"]["rollback_ref"])
        self.assertEqual("rollback", out["rollback_record"]["action_type"])

    def test_alpha_rollback_leaves_signed_records_verifiable(self) -> None:
        server = self.serve()
        svc = server.service
        base = server.url()

        # 1) 入库即签名（写入路径与 server 的 sign_on_write 同一口径）
        created = svc.store.put(svc.sign_on_write_record(new_signed_record()))
        self.assertTrue(created.get("signature"), "记录未签名，无法验证 P0-3")
        first = svc.secretariat.verify_record(created)
        self.assertTrue(first["ok"], first.get("problems"))

        # 2) 干预 → 追加一条独立签名的 intervention 记录（parent 指向被干预记录）
        status, body = http_post(base, "/intervene",
                                 {"action_id": TARGET_ID, "decision": "approve",
                                  "reason": "R1-2a：先批准再回滚（闭包排除干预记录）。",
                                  "idempotency_key": "r12a-chain-1"})
        self.assertEqual(200, status, body)
        iv_rows = [r for r in svc.store.all() if r.get("action_type") == "intervention"
                   and r.get("parent_action_id") == TARGET_ID]
        self.assertTrue(iv_rows, "没有生成干预记录，TP-22 无法验证")

        # 3) plan：干预记录不得算进依赖闭包（TP-16 / P0-4）
        plan = svc.rollback_plan_payload(TARGET_ID)["plan"]
        self.assertEqual([], plan["dependents"], "干预记录又被算进闭包")
        self.assertIn(iv_rows[0]["id"], plan["excluded_members"])

        # 4) 执行回滚（经 server 落盘路径：outcome["records"] 逐条 store.put）
        pre_rows = {row["id"]: copy.deepcopy(row) for row in svc.store.all()}
        pre_bytes = {key: canonical_json(row) for key, row in pre_rows.items()}
        iv_id = iv_rows[0]["id"]
        iv_pre = svc.secretariat.verify_record(pre_rows[iv_id])
        self.assertTrue(iv_pre["ok"], iv_pre.get("problems"))
        target_pre = svc.secretariat.verify_record(pre_rows[TARGET_ID])

        out = svc.rollback_execute({"action_id": TARGET_ID, "reason": "R1-2a 链序",
                                    "confirm_all": True},
                                   actor_id="local-user", request_id="req-r12a")
        self.assertTrue(out["ok"])

        # 5) 终局：回滚逐字段不改写任何记录，验签结论也不得变坏（P0-3 的修复证据）
        rows_after = {row["id"]: row for row in svc.store.all()}
        target_after = rows_after[TARGET_ID]
        self.assertEqual(pre_bytes[TARGET_ID], canonical_json(target_after),
                         "回滚改写了已签名记录的字节（P0-3 未修）")
        self.assertEqual(pre_rows[TARGET_ID].get("labels"), target_after.get("labels"))
        self.assertEqual(pre_rows[TARGET_ID].get("rollback_state"),
                         target_after.get("rollback_state"))
        self.assertNotIn("rolled-back", target_after.get("labels") or [])
        target_after_check = svc.secretariat.verify_record(target_after)
        self.assertEqual(_verify_verdict(target_pre), _verify_verdict(target_after_check),
                         "回滚让目标记录的验签结论变坏（写回了字段 → entry_hash 复算失效）")

        iv_after = rows_after[iv_id]
        self.assertEqual(pre_bytes[iv_id], canonical_json(iv_after))
        iv_after_check = svc.secretariat.verify_record(iv_after)
        self.assertTrue(iv_after_check["ok"],
                        f"干预记录被回滚改写 → 验签失效（基线 P0-3/P0-4）："
                        f"{iv_after_check.get('problems')}")

        # 6) 回滚记录本身签名有效（为 R1-2b 的"回滚记录落盘"预置口径）
        signed = svc.sign_on_write_record(out["rollback_record"])
        self.assertEqual(TARGET_ID, signed.get("rollback_ref"))
        rb_check = svc.secretariat.verify_record(signed)
        self.assertTrue(rb_check["ok"], rb_check.get("problems"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
