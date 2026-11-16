"""R8-1（REQ-008 B8-4 前半 + T13 微修）验收测试：**导出前强制 verify 阻断** + T13 重签。

运行（全程离线，只用标准库；不起服务、不出网）：

    cd agent-explainer
    python -m unittest tests.test_compliance_export_gate -v

钉住的口径（TASK-R8-1 §2.1 / §2.3，PM-PLAN REQ-008 B8-4）：

1. **强制点在服务层，绕不过**：``ComplianceService.export()`` 在**建包之前**跑双验
   （审计链 + 合规导出账本链）。注入的 ``audit_verifier`` 断了 → 即使调用方传
   ``chain_ok=True`` 也拒绝（第 2 组用例）。只在路由面加闸门 = 这里必红。
2. **真拒绝，不是只标记**：拒绝时 ``export_dir`` **零新文件**、导出账本**零新行**、
   账本文件字节一字不动；``detail`` 里带 ``files_written=0`` /
   ``ledger_rows_appended=0`` 两个 0 供机器核对（第 3、4 组）。
3. **码不新增**：唯一拒绝码是既有的 ``COMPLIANCE_CHAIN_BROKEN``（500），
   没有新异常族、没有新错误码（第 5 组钉「格式非法 / 空包」都在闸门**之后**）。
4. **人话 + 断点**：``reason`` 是中文句子（哪条链断、断在第几条、后果、下一步）；
   ``detail`` 给链名 / 位置 / 链头 / 期望-实测摘要，**不给**参数原文、理由原文、
   密钥、文件路径（docs/03 §13 脱敏口径）（第 4、6 组）。
5. **T37 口径不变**：``verify`` 是取证工具，**只报告不阻断、不修**（第 7 组）。
6. **T13 修后必须验签通过**：挂证据即走既有 D-resign 通道重签
   （``entry_hash`` / ``signature`` 换新、``prev_hash`` 接旧值、``verify_record.ok=true``），
   广播帧与盘上那条逐字相同；并带**反证**：同样的写回若不重签，验签立刻 MISMATCH
   （第 8 组——这条红了就说明有人把修复改回裸 ``put``）。
7. **诚实前端面**：导出错误映射表把 ``COMPLIANCE_CHAIN_BROKEN`` 映射到中英双份键，
   且**没有**新增任何前端调用面（compliance 导出路由仍是未挂载的声明面，R8-6 的活）
   （第 9 组）。

纪律：临时目录一律 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）；不并发跑（全量串行）。
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import compliance as cm           # noqa: E402
from agent_explainer import server as sv               # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from scratch import scratch_dir                        # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "agent_explainer" / "web" / "app.js").read_text(encoding="utf-8")
ZH = json.loads((ROOT / "agent_explainer" / "web" / "i18n" / "zh.json")
                .read_text(encoding="utf-8"))
EN = json.loads((ROOT / "agent_explainer" / "web" / "i18n" / "en.json")
                .read_text(encoding="utf-8"))
EMBED = (ROOT / "agent_explainer" / "web" / "i18n" / "embed.js").read_text(encoding="utf-8")

#: 账本条目的实体键（docs/07 §3.3 + 实况 ``counts``）：闸门**不许**往里加键。
#: ``seq`` / ``prev_hash`` / ``entry_hash`` 是 ``Ledger.append`` 写的链字段，
#: 不在 ``export()`` 的返回值里（链归账本，不帰响应）。
ENTRY_KEYS = frozenset({"id", "user_id", "time_range", "format", "root_hash", "ipfs_cid",
                        "eth_tx_hash", "anchor_state", "path", "bytes", "sha256",
                        "chain_ok", "counts", "created_at"})
#: 断链时 reason 必须说清的四件事（中文人话，不是错误码堆叠）。
REASON_MARKERS = ("导出前强制校验未通过", "零证据文件产出", "零导出记录落账",
                  "不会自动清空")


def records(n: int = 3) -> list[dict]:
    return copy.deepcopy(NATIVE[:n])


def audit_entries(n: int = 3) -> list[dict]:
    """几条形状够用的审计条目（组包只做规范哈希归约，不校验审计 schema）。"""
    return [{"seq": i + 1, "ts": f"2026-11-0{i + 1}T08:00:00Z", "actor_id": "local-user",
             "event": "decision", "request_id": f"req-gate-{i}", "reason": "gate fixture"}
            for i in range(n)]


def broken_audit_verdict(*, code: str = "hash_mismatch", index: int = 2) -> dict:
    """注入钩子的返回值：形状对齐既有 ``audit.verify()`` / ``verify_audit_chain()``。"""
    return {"ok": False, "mode": "durable", "count": 5, "head": "sha256:" + "a" * 64,
            "problems": [{"code": code, "index": index, "seq": index + 1,
                          "reason": "entry 3 recomputed hash != recorded entry_hash",
                          "path": "E:/secret/var/log/audit.jsonl"}]}


def tamper_ledger_file(path: pathlib.Path, *, mutate) -> bytes:
    """改**盘上**的账本行（模拟篡改/半写），返回**改后**的字节。

    只动 JSONL 文件本体：这是 T37 / ``Ledger.verify`` 复算的对象，也是导出的落盘对象。
    返回值用来核对「拒绝导出 / 只报告取证都不许再改写这个文件」。
    """
    rows = [json.loads(line) for line in path.read_bytes().decode("utf-8").splitlines()
            if line.strip()]
    for row in rows:
        mutate(row["item"])
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))
    return path.read_bytes()


class GateCaseBase(unittest.TestCase):
    """每台一套独立 features 根；scratch 名 = 用例名（全量串行，不互踩）。"""

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"compliance-gate-{case}")
        self.paths = FeaturePaths(self.root / "features")
        self.paths.ensure()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def service(self, **kwargs) -> cm.ComplianceService:
        return cm.ComplianceService(self.paths, **kwargs)

    def export_files(self) -> list[str]:
        d = self.paths.root / "exports"
        return sorted(p.name for p in d.glob("*") if p.is_file()) if d.exists() else []

    def ledger_rows(self) -> int:
        path = self.paths.exports
        if not path.exists():
            return 0
        return len([line for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()])


# --------------------------------------------------------------------------- #
# 1. 正常链 → 放行（回归：闸门不许把好链的导出也拦了）
# --------------------------------------------------------------------------- #

class GateCleanPathTests(GateCaseBase):

    def test_clean_chain_still_exports(self) -> None:
        """两条链都好 → 照常出文件、照常落账，且 ``chain_ok`` 为真。"""
        svc = self.service(audit_verifier=lambda: {"ok": True, "count": 5,
                                                   "head": "sha256:" + "b" * 64,
                                                   "problems": []})
        view = svc.export(records=records(), audit_entries=audit_entries(),
                          format="csv", user_id="local-user")
        target = pathlib.Path(str(view["path"]))
        self.assertTrue(target.exists(), "好链的导出必须真落盘")
        self.assertGreater(view["bytes"], 0)
        self.assertTrue(view["sha256"].startswith("sha256:"))
        self.assertIsNone(view["eth_tx_hash"], "0.6.0 不上链：eth_tx_hash 恒为 None")
        self.assertEqual(view["anchor_state"], cm.ANCHOR_STATE)
        self.assertTrue(view["chain_ok"], "好链的账本行 chain_ok 必须为真")
        self.assertEqual(self.ledger_rows(), 1)

    def test_entry_keys_are_the_documented_set(self) -> None:
        """闸门**不新增**账本实体键（docs/07 §3.3 的字段清单是契约的一部分）。"""
        svc = self.service()
        view = svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        self.assertEqual(set(view) - {"uri"}, set(ENTRY_KEYS),
                         "导出账本条目的键集变了（响应形状要保持）")
        self.assertEqual(view["uri"], f"/api/compliance/exports/{view['id']}/file")

    def test_gate_reports_both_sides_when_passing(self) -> None:
        """``verify_export_chains`` 是只读的：好链返回两条 side 都 ok，零写入。"""
        svc = self.service(audit_verifier=lambda: {"ok": True, "count": 2,
                                                   "head": "sha256:" + "c" * 64,
                                                   "problems": []})
        gate = svc.verify_export_chains()
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["audit"]["source"], "injected")
        self.assertEqual(gate["compliance_ledger"]["source"], "ledger_verify")
        self.assertEqual(self.export_files(), [], "只读闸门不许产出任何文件")
        self.assertEqual(self.ledger_rows(), 0, "只读闸门不许追加任何账本行")


# --------------------------------------------------------------------------- #
# 2. 审计侧断 → 拒绝（含「传 chain_ok=True 也绕不过」的强制力证明）
# --------------------------------------------------------------------------- #

class GateAuditBreakTests(GateCaseBase):

    def test_injected_broken_audit_refuses_even_when_caller_says_ok(self) -> None:
        """核心条款：注入的复算说断，``chain_ok=True`` 也拦（强制点在服务层）。"""
        svc = self.service(audit_verifier=lambda: broken_audit_verdict())
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(),
                       format="csv", chain_ok=True)
        err = ctx.exception
        self.assertEqual(err.code, "COMPLIANCE_CHAIN_BROKEN")
        self.assertEqual(err.http_status, 500)
        detail = err.detail or {}
        self.assertEqual(detail["side"], "audit")
        self.assertEqual(detail["broken_sides"], ["audit"])
        self.assertEqual(detail["sides"]["audit"]["source"], "injected")
        self.assertEqual(detail["problems"][0]["code"], "hash_mismatch")
        self.assertEqual(detail["problems"][0]["index"], 2)

    def test_chain_ok_false_still_refuses_without_verifier(self) -> None:
        """没注入钩子时（单测/自检直接构造）：``chain_ok=False`` 照旧拒绝，
        且如实写明这一侧是「调用方声明」，不假称自己复算过。"""
        svc = self.service()
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(),
                       format="csv", chain_ok=False)
        detail = ctx.exception.detail or {}
        self.assertEqual(ctx.exception.code, "COMPLIANCE_CHAIN_BROKEN")
        self.assertEqual(detail["side"], "audit")
        self.assertEqual(detail["sides"]["audit"]["source"], "caller")

    def test_verifier_raising_is_a_refusal_not_a_pass(self) -> None:
        """「验不了」= 不合格：钩子抛异常绝不能当成「验过了」放行。"""
        def boom() -> dict:
            raise RuntimeError("audit store unreadable")

        svc = self.service(audit_verifier=boom)
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        detail = ctx.exception.detail or {}
        self.assertEqual(ctx.exception.code, "COMPLIANCE_CHAIN_BROKEN")
        self.assertEqual(detail["problems"][0]["code"], "audit_verify_error")
        self.assertIn("audit store unreadable", detail["problems"][0]["reason"])
        self.assertEqual(self.export_files(), [])

    def test_broken_audit_refuses_any_format_and_empty_bundle(self) -> None:
        """闸门排在格式校验与建包**之前**：断链时非法 format 与空包都轮不到表态。"""
        svc = self.service(audit_verifier=lambda: broken_audit_verdict())
        for kwargs in ({"format": "exe"}, {"format": "pdf", "records": [], "audit_entries": []}):
            with self.subTest(**kwargs):
                with self.assertRaises(cm.ComplianceError) as ctx:
                    svc.export(records=kwargs.get("records", records()),
                               audit_entries=kwargs.get("audit_entries", audit_entries()),
                               format=kwargs["format"])
                self.assertEqual(ctx.exception.code, "COMPLIANCE_CHAIN_BROKEN")


# --------------------------------------------------------------------------- #
# 3-4. 账本侧断 → 拒绝 + 零产出 + 人话与断点
# --------------------------------------------------------------------------- #

class GateLedgerBreakTests(GateCaseBase):

    def _export_once(self) -> dict:
        svc = self.service()
        return svc.export(records=records(), audit_entries=audit_entries(), format="csv")

    def test_broken_ledger_refuses_with_zero_output(self) -> None:
        """账本链断 → 拒绝，且**零新文件、零新行、账本文件字节一字不动**（真拒绝）。"""
        first = self._export_once()
        files_before = self.export_files()
        rows_before = self.ledger_rows()
        bytes_after_tamper = tamper_ledger_file(self.paths.exports,
                                                mutate=lambda item: item.update({"format": "exe"}))
        svc = self.service()
        self.assertFalse(svc.verify_export_chains()["ok"], "篡改后的账本链必须判为断")
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        err = ctx.exception
        self.assertEqual(err.code, "COMPLIANCE_CHAIN_BROKEN")
        detail = err.detail or {}
        self.assertEqual(detail["side"], "compliance_ledger")
        self.assertEqual(detail["sides"]["compliance_ledger"]["problem_count"] >= 1, True)
        self.assertEqual(detail["files_written"], 0)
        self.assertEqual(detail["ledger_rows_appended"], 0)
        self.assertEqual(self.export_files(), files_before,
                         f"断链时不许产出任何文件（多出来：{set(self.export_files()) - set(files_before)}）")
        self.assertEqual(self.ledger_rows(), rows_before, "断链时不许追加任何账本行")
        self.assertEqual(self.paths.exports.read_bytes(), bytes_after_tamper,
                         "拒绝导出不许顺手改写账本（追加式纪律）")
        self.assertTrue(pathlib.Path(str(first["path"])).exists(),
                        "先前那好导出不能被拒绝动作顺手删掉")

    def test_reason_is_human_readable_with_the_break_point(self) -> None:
        """``reason`` 是中文人话：说清哪条链、断在第几条、后果、下一步（不是码堆叠）。"""
        self._export_once()
        tamper_ledger_file(self.paths.exports, mutate=lambda item: item.update({"seq": 42}))
        svc = self.service()
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        reason = ctx.exception.reason
        for marker in REASON_MARKERS:
            self.assertIn(marker, reason, f"人话里缺少「{marker}」：{reason}")
        self.assertIn("合规导出账本链", reason)
        self.assertIn("序号断裂", reason)
        self.assertIn("第 1 条", reason, "断点要说清第几条（账本 index 是 1 基）")
        self.assertIn("COMPLIANCE_CHAIN_BROKEN", ctx.exception.code)

    def test_detail_carries_break_point_without_leaking_secrets(self) -> None:
        """``detail`` 给断点（链名/位置/链头/期望-实测），**不给**路径与原文参数。"""
        self._export_once()
        tamper_ledger_file(self.paths.exports,
                           mutate=lambda item: item.update({"user_id": "leak@corp.example"}))
        svc = self.service()
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        detail = ctx.exception.detail or {}
        text = json.dumps(detail, ensure_ascii=False)
        self.assertIn("head", detail["sides"]["compliance_ledger"])
        self.assertTrue(detail["problems"][0]["code"].startswith("ledger_"))
        self.assertNotIn("path", detail, "detail 顶层不许带文件路径（docs/03 §13）")
        self.assertNotIn(str(self.root), text, "detail 不许泄漏数据目录路径")
        self.assertNotIn("@corp.example", text, "detail 不许回显被改动的字段原文")
        self.assertNotIn("\n", ctx.exception.reason, "reason 是一句人话，不是多行日志堆叠")

    def test_audit_side_index_is_printed_as_human_position(self) -> None:
        """审计链 index 是 0 基：人话里要印「第 3 条」，与既有 ``audit_broken_reason`` 同口径。"""
        svc = self.service(audit_verifier=lambda: broken_audit_verdict(index=2))
        with self.assertRaises(cm.ComplianceError) as ctx:
            svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        self.assertIn("第 3 条", ctx.exception.reason,
                      "审计链断点位置必须按人类第几条报（0 基 index 要 +1）")


# --------------------------------------------------------------------------- #
# 5. 不新增码、不新增异常族
# --------------------------------------------------------------------------- #

class GateNoNewErrorSurfaceTests(unittest.TestCase):

    def test_only_the_existing_code_is_used(self) -> None:
        """闸门只用既有码：``COMPLIANCE_CHAIN_BROKEN``（ERROR_TABLE 里已有，500）。"""
        self.assertIn("COMPLIANCE_CHAIN_BROKEN", sv.ERROR_TABLE,
                      "拒绝码必须是既有 ERROR_TABLE 里的成员（本轮零新增码）")
        self.assertEqual(sv.ERROR_TABLE["COMPLIANCE_CHAIN_BROKEN"][0], 500)
        self.assertIn("no evidence file is produced",
                      sv.ERROR_TABLE["COMPLIANCE_CHAIN_BROKEN"][1],
                      "码表的英文口径必须说清「不产出证据文件」，与闸门的零产出口径一致")

    def test_gate_helpers_are_exported_for_reuse(self) -> None:
        """``gate_reason`` / ``gate_detail`` 是模块级函数（服务端/测试同一口径，不复制）。"""
        self.assertTrue(callable(cm.gate_reason) and callable(cm.gate_detail))
        gate = {"ok": False,
                "audit": {"ok": False, "source": "injected", "count": 1,
                          "head": "sha256:" + "d" * 64,
                          "problems": [{"code": "hash_mismatch", "index": 0,
                                        "reason": "x"}]},
                "compliance_ledger": {"ok": True, "source": "ledger_verify", "count": 0,
                                      "head": "", "problems": []}}
        detail = cm.gate_detail(gate)
        self.assertEqual(detail["side"], "audit")
        self.assertEqual(detail["files_written"], 0)
        self.assertEqual(detail["ledger_rows_appended"], 0)
        self.assertIn("审计链", cm.gate_reason(gate))


# --------------------------------------------------------------------------- #
# 6-7. 服务端接线 live + T37 只报告不阻断
# --------------------------------------------------------------------------- #

class GateServerWiringTests(unittest.TestCase):
    """真实 ``InterveneService`` 的合规服务必须带上注入钩子（不是只存在于单测里）。"""

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"compliance-gate-{case}")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def service(self) -> sv.InterveneService:
        return sv.InterveneService(records=copy.deepcopy(NATIVE),
                                   features_root=self.root / "features",
                                   sign_on_write=True)

    def test_service_property_injects_the_audit_hook(self) -> None:
        svc = self.service()
        comp = svc.compliance_service
        self.assertIsNotNone(comp._audit_verifier,
                             "服务端没有把既有审计链复算注入合规服务（闸门可被绕过）")
        verdict = svc.compliance_audit_verdict()
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(set(verdict), {"ok", "count", "head", "problems"},
                         "判据形状只带这四件事：不带路径、不带参数原文（docs/03 §13）")

    def test_service_level_export_is_gated_without_http_route(self) -> None:
        """绕过 HTTP 直接调服务层，账本断了照样拒（强制点不可绕）。"""
        svc = self.service()
        comp = svc.compliance_service
        comp.export(records=records(), audit_entries=audit_entries(), format="csv")
        paths = FeaturePaths(self.root / "features")
        files_before = sorted(p.name for p in paths.exports.parent.glob("*") if p.is_file())
        tamper_ledger_file(paths.exports, mutate=lambda item: item.update({"format": "exe"}))
        rows_before = len([line for line in paths.exports.read_text(encoding="utf-8").splitlines()
                           if line.strip()])
        with self.assertRaises(cm.ComplianceError) as ctx:
            comp.export(records=records(), audit_entries=audit_entries(), format="csv")
        self.assertEqual(ctx.exception.code, "COMPLIANCE_CHAIN_BROKEN")
        after = sorted(p.name for p in paths.exports.parent.glob("*") if p.is_file())
        self.assertEqual(after, files_before, "断链时服务层导出也不许产出文件")
        self.assertEqual(len([line for line in paths.exports.read_text(encoding="utf-8").splitlines()
                              if line.strip()]), rows_before)


class T37ReportOnlyTests(unittest.TestCase):
    """T37 的口径**不许**被本轮改动染上阻断/修复语义：取证要能看断在哪。"""

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"compliance-gate-{case}")
        self.paths = FeaturePaths(self.root / "features")
        self.paths.ensure()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_verify_still_reports_a_broken_ledger_and_repairs_nothing(self) -> None:
        svc = cm.ComplianceService(self.paths)
        view = svc.export(records=records(), audit_entries=audit_entries(), format="csv")
        bytes_after_tamper = tamper_ledger_file(self.paths.exports,
                                                mutate=lambda item: item.update({"format": "exe"}))
        report = svc.verify(view["id"])          # 不抛：取证工具必须能跑完
        self.assertFalse(report["ledger_ok"], "T37 必须如实报账本链断了")
        self.assertTrue(report["ledger_problems"], "T37 要给得出断点明细")
        self.assertEqual(report["sha256_ok"], True,
                         "文件本体没动：T37 的逐字节复算仍应通过")
        self.assertEqual(self.paths.exports.read_bytes(), bytes_after_tamper,
                         "T37 只报告：不许改写账本一个字节")
        self.assertIsNotNone(svc.get(view["id"]), "断链的账本仍可读取（取证入口不能被封）")


# --------------------------------------------------------------------------- #
# 8. T13 L1 微修：挂证据即重签（带反证）
# --------------------------------------------------------------------------- #

class T13ResignTests(unittest.TestCase):

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"compliance-gate-{case}")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def decided_row(self, svc: sv.InterveneService, tag: str) -> dict:
        record, _meta = svc.intervene({"action_id": "act-demo-0004",
                                       "decision": "modify",
                                       "modified_params": {"max_move_count": 50}},
                                      request_id=f"probe-{tag}")
        return record

    def attach(self, svc: sv.InterveneService, rid: str) -> dict:
        return svc.evidence_attach(rid, {"kind": "diff", "provided_by": "user",
                                         "caption": "R8-1 T13 重签钉测",
                                         "payload": {"before": "x = 1", "after": "x = 2",
                                                     "filename": "gate"}},
                                   actor_id="local-user")

    def test_attach_resigns_and_the_ledger_verifies(self) -> None:
        """修后：``verify_record.ok=true``、entry_hash/signature 换新、prev_hash 接旧值。"""
        svc = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                  features_root=self.root / "features",
                                  sign_on_write=True)
        before = self.decided_row(svc, "resign")
        rid = str(before["id"])
        old_entry_hash, old_signature = before["entry_hash"], before["signature"]
        signed_before = svc.signed_on_write
        view = self.attach(svc, rid)
        row = svc.store.get(rid)
        report = svc.secretariat.verify_record(row)
        self.assertTrue(report["ok"], f"挂证据后验签必须通过：{report['problems']}")
        self.assertEqual(report["checks"]["entry_hash"], "valid")
        self.assertEqual(report["checks"]["prev_hash"], "continuous")
        self.assertEqual(report["problems"], [])
        self.assertNotEqual(row["entry_hash"], old_entry_hash, "新 body 必须重算 entry_hash")
        self.assertNotEqual(row["signature"], old_signature, "新 body 必须由新签名覆盖")
        self.assertEqual(row["prev_hash"], old_entry_hash, "链要接在改前那条之后（链没断）")
        self.assertEqual(svc.sign_failures, 0)
        self.assertEqual(svc.signed_on_write - signed_before, 1, "挂证据这一次写回必须签一次")
        self.assertEqual(len(row["multimedia_evidence"]), len(before.get("multimedia_evidence") or []) + 1)
        self.assertTrue(view["ok"])
        self.assertEqual(view["signing"]["enabled"], svc.mode == sv.MODE_LOCAL)
        self.assertEqual(view["signing"]["signed"], 1)
        self.assertEqual(view["signing"]["failures"], 0)
        self.assertEqual(view["evidence_hash"], svc.evidence_service.sign_range(row))
        self.assertEqual(view["evidence"]["provided_by"], "user")

    def test_broadcast_carries_the_stored_signed_row(self) -> None:
        """广播帧与盘上那条**逐字相同**：不再出现「广播带未签 body、账本带旧签名」。"""
        svc = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                  features_root=self.root / "features",
                                  sign_on_write=True)
        record = self.decided_row(svc, "broadcast")
        rid = str(record["id"])
        hub = svc.stream_hub
        sub = hub.subscribe()

        def drain() -> list[dict]:
            frames: list[dict] = []
            while True:
                frame = sub.get(timeout=0.2)
                if not frame:
                    return frames
                frames.append(frame)

        try:
            self.attach(svc, rid)
            frames = drain()
        finally:
            hub.unsubscribe(sub)
        attached = [frame for frame in frames
                    if str(frame.get("type") or "").endswith("updated")
                    and (frame.get("data") or {}).get("reason") == "evidence_attached"]
        self.assertTrue(attached, "落账必须广播一帧 evidence_attached（reason 不许改）")
        sent = (attached[0].get("data") or {}).get("record") or {}
        stored = svc.store.get(rid)
        self.assertEqual(sent, stored, "广播里的记录要与账本里那条逐字相同")
        self.assertTrue(sent.get("signature"), "广播出去的必须是重签后的那份")

    def test_bare_put_control_would_fail_verification(self) -> None:
        """**反证**（探针 C 的机器版）：同样的写回若不重签，验签立刻 MISMATCH。

        这条红 = 有人把修复改回裸 ``store.put(updated)``（T13 硬缺陷复现）。
        """
        svc = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                  features_root=self.root / "features",
                                  sign_on_write=True)
        record = self.decided_row(svc, "control")
        rid = str(record["id"])
        bare = copy.deepcopy(dict(record))
        bare["multimedia_evidence"] = list(bare.get("multimedia_evidence") or []) + [
            {"id": "ev-control-0001", "kind": "diff", "sha256": "0" * 64,
             "media_type": "text/plain", "caption": "反证夹具", "provided_by": "user"}]
        svc.store.put(bare)                          # 故意裸写：不重签
        report = svc.secretariat.verify_record(svc.store.get(rid))
        self.assertFalse(report["ok"],
                         "反证夹具必须能复现「裸 put → 验签红」，否则这条钉测是空跑")
        self.assertEqual(report["checks"]["entry_hash"], "invalid")
        codes = [str(item.get("code")) for item in report["problems"]]
        self.assertIn("SECRETARIAT_HASH_MISMATCH", codes)


# --------------------------------------------------------------------------- #
# 9. 诚实前端面：映射齐、无新增调用面
# --------------------------------------------------------------------------- #

class HonestFrontendFaceTests(unittest.TestCase):

    def test_export_error_map_covers_the_refusal_code(self) -> None:
        """导出失败必须映射到中英双份字典键（不显示服务端原文，也不假成功）。"""
        block = APP_JS.split("EXPORT_ERROR_KEYS", 1)[1].split("};", 1)[0]
        self.assertIn("COMPLIANCE_CHAIN_BROKEN", block,
                      "导出错误映射表没有覆盖新暴露的拒绝码")
        matched = re.search(r"COMPLIANCE_CHAIN_BROKEN\s*:\s*'([^']+)'", block)
        self.assertTrue(matched, "映射项必须是单引号点号键（i18n_lint 的采集口径）")
        key = matched.group(1)
        domain, _, leaf = key.partition(".")
        self.assertEqual(domain, "export")
        self.assertTrue(ZH[domain][leaf], f"{key} 在 zh.json 里没有文案")
        self.assertTrue(EN[domain][leaf], f"{key} 在 en.json 里没有文案")
        self.assertIn(leaf, EMBED, f"{key} 没同步进 i18n/embed.js（跑 --sync-embed）")

    def test_chain_broken_copy_is_a_refusal_not_a_success(self) -> None:
        """文案本身要诚实：说「一个字都没写」，不许出现「导出成功」字样。"""
        zh_leaf = ZH["export"]["error.compliance_chain_broken"]
        en_leaf = EN["export"]["error.compliance_chain_broken"]
        for text in (zh_leaf, en_leaf):
            self.assertNotIn("成功", str(text))
        self.assertIn("零", str(zh_leaf))
        self.assertIn("wrote nothing", str(en_leaf))

    def test_contract_baseline_is_untouched(self) -> None:
        """铁律基线的**现行值**：61 路由 / 44 声明 / 44 挂载 / 0 条未挂载。

        历史：这条测试原本钉的是「54 路由 / 44 声明 / 42 挂载 / 恰 2 条未挂载」——
        那 2 条（`/api/compliance/export`、`/api/evidence`）是 R8 阶段**登记不修**的欠账。
        0.6.0 L1「八功能接线」把两条都挂上了（并经老板批准），所以基线整体上移：
        路由 54 → 61（T13 读写 2 条、T17 1 条、T18 写侧 1 条、T33 1 条、
        `_evidence_route` 族 2 条），未挂载 2 → 0。

        这条测试的**意义没变**：路由面仍然只许按批准过的清单增长，
        下次再想悄悄加一条，这里照样会红。
        """
        self.assertEqual(len(CONTRACT["routes"]), 61,
                         "契约路由数变了（0.6.0 L1 八功能接线后的基线是 61）")
        cross = CONTRACT["declared_vs_mounted"]
        self.assertEqual(cross["declared"], 44)
        self.assertEqual(cross["mounted"], 44,
                         "44 个 API_PATH 常量必须全部接进分发（声明即挂载）")
        self.assertEqual(cross["unmounted"], [],
                         "未挂载清单必须为空：0.6.0 L1 把最后两条欠账收掉了")

    def test_no_new_frontend_call_face(self) -> None:
        """零新增调用面：app.js 里不许出现 compliance 导出的调用面（R8-6 的活）。"""
        self.assertNotIn("complianceExport", APP_JS,
                         "app.js 出现了 compliance 导出调用面（越界：R8-6 才做前端面）")
        self.assertNotIn("api/compliance/export", APP_JS,
                         "前端不许新增 /api/compliance/export 调用")

    def test_compliance_extension_is_implemented_without_a_new_door(self) -> None:
        """R8-6 落地后反向守住：扩展**已实装**，但**不开第二扇门**。

        R8-1/R8-2 时这条断言是「compliance.js 保持 5 行占位（注册导出 UI 属 R8-6）」。
        R8-6 把面做出来之后，同一条纪律必须换方向成立：占位必须被替换，但
        ``Ext.register`` 只能出现一次、**不得带 tab**（否则就是第二个导出入口），
        且**不得新增任何调用面**（T33 仍未挂载）。
        """
        text = (ROOT / "agent_explainer" / "web" / "ext" / "compliance.js").read_text(encoding="utf-8")
        self.assertGreater(len(text.splitlines()), 12,
                           "compliance.js 仍是占位（R8-6 应已实装）")
        self.assertEqual(1, text.count("Ext.register({"),
                         "扩展只能注册一次，多注册＝多一个导出入口")
        self.assertNotIn("tab:", text.split("Ext.register({", 1)[1].split("});", 1)[0],
                         "本模块不许开 tab（第二个导出入口）")
        for token in ("api/compliance/export", "/api/compliance"):
            self.assertNotIn(token, text,
                             f"扩展里出现未挂载的合规路由调用面：{token}")


if __name__ == "__main__":  # 手工自检：python tests/test_compliance_export_gate.py -v
    unittest.main(verbosity=2)
