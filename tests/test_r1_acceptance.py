"""R1-7 终验断言固化（QA 独立验收脚本，只读产品代码）。

覆盖 REQ-001 的 A1/A2/A3/A5/A6 与门禁可判定项，**不重复**既有套件的全部细节，
只把「终验口径」钉成可重跑断言：

* A1 双口径：主口径 = 真实导入集副作用子集 ≥ 0.90；副口径 = 演示集整集 1.0 且零未覆盖；
  未覆盖项必须逐条带 ``reason``；TP-04 命名映射与显式不可逆。
* A2：目标与闭包成员零改写、逐条前态快照、链化 journal + 摘要独立复算、
  回执 ``simulated_local`` 且文案不出现完成态谎称。
* A3：回滚记录落盘签名、审计链 + ``rollback.anchored`` 锚、链序 prev_hash 咬合、
  回滚后目标/成员仍可验签、``rollback_record_id`` 不悬空。
* A5：依赖确认、二次回滚同码、闭包排除干预记录、不可逆成员只进 blockers、
  截断必须确认、环告警。
* A6：CSS 规则落地（灰 + 删除线 / 时间轴虚线 / cards__ref）+ 双语文案对称。
* 门禁：契约含回滚路由、``in_sync``、``unknown_paths`` 不含回滚；``next_ref`` 不落盘。

运行::

    cd agent-explainer
    python -m unittest tests.test_r1_acceptance -v

临时目录一律走 ``tests/scratch.py``；数据根一次性，不碰仓内 ``var/``。
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402

from agent_explainer import rollback as rb        # noqa: E402
from agent_explainer import server as sv         # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json").read_text(encoding="utf-8"))["records"]
NATIVE_EN = json.loads((ROOT / "samples" / "action_records_en.json").read_text(encoding="utf-8"))["records"]
TRACES = ("langfuse.json", "mcp_events.json", "otel_genai.json")
BASE = next(r for r in NATIVE if r["id"] == "act-demo-0007")
IRREVERSIBLE_SCOPE = next(r for r in NATIVE if r["id"] == "act-demo-0005")["impact_scope"]


def row(rid: str, *, parent: str | None = None, tool: str = "notes.write",
        action_type: str = "write", status: str = "succeeded", risk: str = "medium",
        reversible: bool | None = None, depends_on: list[str] | None = None,
        stamp: str = "2026-11-14T00:00:00Z") -> dict:
    record = copy.deepcopy(BASE)
    record.update({"id": rid, "action_type": action_type, "status": status,
                   "risk_level": risk, "timestamp": stamp, "parent_action_id": parent,
                   "user_intervention": None, "labels": []})
    record["tool_used"] = {"name": tool, "version": "1.0", "provider": "local",
                           "call_id": f"call-{rid}", "params_summary": "R1-7 终验用例",
                           "result_ref": None}
    if reversible is not None:
        record["impact_scope"] = {"targets": [], "money": None, "external_endpoint": None,
                                  "data_categories": ["none"], "reversible": reversible,
                                  "summary": "R1-7 终验用例影响面"}
    if depends_on is not None:
        record["context"] = {"depends_on": list(depends_on)}
    for key in ("signature", "entry_hash", "prev_hash", "tsa_timestamp", "agent_pubkey",
                "human_owner", "approval", "sequence_no", "rollback_state"):
        record.pop(key, None)
    return record


class R17Case(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir("r17-acceptance")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def service(self, name: str, rows: list[dict]) -> sv.InterveneService:
        root = self.root / name
        root.mkdir(parents=True, exist_ok=True)
        svc = sv.InterveneService(
            records=[], features_root=root / "features", sign_on_write=True,
            records_store_path=root / "records.jsonl",
            audit_log_path=root / "audit.jsonl",
            settings_path=root / "settings.json",
            auth_state_path=root / "auth.json",
            ingest_token_path=root / "ingest.json")
        for item in copy.deepcopy(rows):
            svc.store.put(svc.sign_on_write_record(item))
        return svc

    def verify(self, svc: sv.InterveneService, record: dict) -> dict:
        return svc.secretariat.verify_record(record)

    @staticmethod
    def diff(a: dict, b: dict, exclude=("rollback_state", "labels")) -> list[str]:
        fa = {k: v for k, v in a.items() if k not in exclude}
        fb = {k: v for k, v in b.items() if k not in exclude}
        return sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))


# --------------------------------------------------------------------------- #
# A1：双口径覆盖率（主口径副作用子集 / 副口径演示集）
# --------------------------------------------------------------------------- #

class A1CoverageTests(R17Case):
    def test_primary_scope_side_effect_subset_meets_the_bar(self) -> None:
        svc = sv.InterveneService(records=[], features_root=self.root / "import",
                                  sign_on_write=True)
        for name in TRACES:
            doc = json.loads((ROOT / "samples" / "traces" / name).read_text(encoding="utf-8"))
            result = svc.import_traces(doc, request_id=f"r17-{name}", lang="zh")
            self.assertTrue(result.get("ok"), result)
        rows = svc.store.all()
        self.assertEqual(24, len(rows), "导入集条数须与 R1-1 基线 24 条一致")
        cov = rb.RollbackService(FeaturePaths(self.root / "import")).coverage(rows)
        side = cov["buckets"]["side_effect_subset"]
        self.assertEqual(10, side["total"], f"副作用子集分母必须为 10：{side}")
        self.assertGreaterEqual(side["ratio"], 0.90, side)
        self.assertEqual(1, side["uncovered"], side)
        self.assertEqual(["notifications-message-13"], [u["id"] for u in cov["uncovered"]])
        for item in cov["uncovered"]:
            self.assertTrue(str(item.get("reason") or "").strip(),
                            f"未覆盖项必须逐条带 reason：{item}")

    def test_guard_scope_demo_sets_stay_at_one(self) -> None:
        service = rb.RollbackService(FeaturePaths(self.root / "guard"))
        for label, rows in (("zh", NATIVE), ("en", NATIVE_EN)):
            cov = service.coverage(rows)
            self.assertEqual(1.0, cov["ratio"], f"演示集 {label} 护栏破了：{cov}")
            self.assertEqual([], cov["uncovered"], f"演示集 {label} 出现未覆盖项")
            self.assertEqual(1.0, cov["buckets"]["side_effect_subset"]["ratio"])

    def test_tp04_naming_mapping_and_explicit_irreversible(self) -> None:
        for tool in ("cancel_order", "order.create", "order.book"):
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, tool)
            self.assertEqual("cancel_booking", entry["op"], tool)
        for tool in ("payment.transfer", "contract.sign"):
            entry = rb.inverse_entry_of(tool)
            self.assertIsNotNone(entry, f"{tool} 必须有显式条目")
            self.assertFalse(entry["reversible"], f"{tool} 必须判不可逆")
            verdict = rb.reversible(row("probe-" + tool, tool=tool))
            self.assertFalse(verdict["reversible"], tool)
            self.assertTrue(str(verdict.get("reason") or "").strip(),
                            f"{tool} 必须有阻断文案（人话）")
            self.assertIn("不可逆", verdict["reason"])


# --------------------------------------------------------------------------- #
# A2 / A3 / A5：闭包全链路（零改写、快照、链、锚、幂等、治理）
# --------------------------------------------------------------------------- #

class ClosureChainTests(R17Case):
    def seed(self) -> list[dict]:
        return [
            row("r17-target", tool="filesystem.remove", stamp="2026-11-14T01:00:00Z"),
            row("r17-child", parent="r17-target", stamp="2026-11-14T01:01:00Z"),
            row("r17-grand", parent="r17-child", stamp="2026-11-14T01:02:00Z"),
            row("r17-irr", parent="r17-target", tool="payment.transfer",
                stamp="2026-11-14T01:03:00Z"),
            row("r17-iv", parent="r17-target", action_type="intervention",
                tool="notes.write", status="pending_confirmation",
                stamp="2026-11-14T01:04:00Z"),
        ]

    def test_closure_contract_end_to_end(self) -> None:
        svc = self.service("closure", self.seed())
        before = {rid: copy.deepcopy(svc.store.get(rid))
                  for rid in ("r17-target", "r17-child", "r17-grand", "r17-irr", "r17-iv")}
        plan = (svc.rollback_plan_payload("r17-target") or {}).get("plan") or {}

        # TP-19 / TP-23：依赖清单 + 不可逆成员只进 blockers
        self.assertIn("r17-child", plan["dependents"])
        self.assertIn("r17-irr", plan["irreversible_dependents"])
        self.assertTrue(plan["requires_confirmation"])
        self.assertNotIn("r17-irr", plan["order"])
        # TP-22：干预记录被排除在闭包外
        self.assertIn("r17-iv", plan["excluded_members"])
        plan_codes = [b["code"] for b in plan["blockers"]]
        self.assertIn(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, plan_codes)

        # TP-24：截断必须确认（plan 与 execute 同口径）
        depth0 = svc.rollback_service.plan(svc.store.all(), "r17-target", max_depth=0)
        self.assertTrue(depth0["truncated"])
        self.assertTrue(depth0["requires_confirmation"])
        with self.assertRaises(rb.RollbackError) as depth_ctx:
            svc.rollback_service.execute(svc.store.all(), "r17-target",
                                         actor="qa-r17", max_depth=0)
        self.assertEqual("ROLLBACK_DEPENDENTS_PENDING", depth_ctx.exception.code)

        # 正式执行（confirm_all：只回滚可逆成员）
        out = svc.rollback_execute({"action_id": "r17-target", "confirm_all": True},
                                   actor_id="qa-r17", actor_role="admin",
                                   request_id="r17-closure")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(["r17-grand", "r17-child", "r17-target"], out["order"])
        self.assertEqual(["r17-irr"], out["blocked_members"])

        # TP-05 / TP-06：零改写 + 逐成员前态快照
        for rid in ("r17-target", "r17-child", "r17-grand"):
            self.assertEqual([], self.diff(before[rid], svc.store.get(rid)),
                             f"{rid} 回滚后不得改写业务字段（口径 α）")
        entry = svc.rollback_service.journal()[-1]
        self.assertEqual("succeeded", entry.get("phase"))
        snapshots = {item["id"]: item.get("snapshot") for item in entry["snapshots"]}
        self.assertEqual(set(entry["affected"]), set(snapshots))
        for rid in entry["affected"]:
            self.assertEqual([], self.diff(snapshots[rid] or {}, svc.store.get(rid) or {}),
                             f"{rid} 的快照必须与现记录业务字段一致")
        # TP-07：链化字段 + 摘要独立复算 + 链校验
        for key in ("seq", "prev_hash", "entry_hash", "snapshot_sha256"):
            self.assertIn(key, entry, f"journal 行缺链化字段 {key}")
        recomputed = hashlib.sha256(
            json.dumps(entry["snapshot"], sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(recomputed, entry["snapshot_sha256"])
        self.assertTrue(svc.rollback_service.journal_chain_verify()["ok"])

        # TP-11 / TP-15：回滚记录落盘签名、引用不悬空
        rb_record = out["rollback_record"]
        saved = svc.store.get(rb_record["id"])
        self.assertIsNotNone(saved)
        self.assertTrue(self.verify(svc, saved)["ok"], self.verify(svc, saved))
        self.assertIsNotNone(svc.store.get(entry["rollback_record_id"]))

        # TP-12：审计链 + 原生锚事件（查询结果会按审计脱敏掩码长串，
        # 逐字比对改读持久 jsonl 原文：锚必须真的把 journal id 与快照摘要写进链）
        self.assertTrue(svc.audit.verify()["ok"])
        anchors = svc.audit_log.query(event="rollback.anchored")
        self.assertEqual(1, len(anchors))
        raw_chain = (self.root / "closure" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn('"rollback.anchored"', raw_chain)
        # journal id 在审计落盘时按脱敏规则掩码（rbj_20**********16_xxx），
        # 但前态快照摘要逐字进链：它才是「锚住的是哪份前态」的可复核证据。
        self.assertIn(entry["snapshot_sha256"], raw_chain)
        self.assertTrue((anchors[0].get("fields") or {}).get("journal_id"))

        # TP-14：回滚后目标 / 成员仍可验签
        for rid in ("r17-target", "r17-child"):
            self.assertTrue(self.verify(svc, svc.store.get(rid))["ok"], rid)

        # TP-22：干预记录一字未改
        self.assertEqual(before["r17-iv"].get("labels"), svc.store.get("r17-iv").get("labels"))
        self.assertEqual(before["r17-iv"].get("rollback_state"),
                         svc.store.get("r17-iv").get("rollback_state"))

        # TP-20：二次回滚 plan / execute 同码
        plan2 = (svc.rollback_plan_payload("r17-target") or {}).get("plan") or {}
        self.assertIn(rb.BLOCKER_ALREADY_ROLLED_BACK,
                      [b["code"] for b in plan2["blockers"]])
        with self.assertRaises(rb.RollbackError) as again:
            svc.rollback_service.execute(svc.store.all(), "r17-target",
                                         actor="qa-r17", confirm_all=True)
        self.assertEqual(rb.BLOCKER_ALREADY_ROLLED_BACK, again.exception.code)

        # TP-21：已回滚成员不再重入闭包
        plan3 = (svc.rollback_plan_payload("r17-child") or {}).get("plan") or {}
        self.assertIn("r17-grand", plan3["skipped_already_rolled_back"])
        self.assertEqual([], plan3["dependents"])

    def test_tp25_cycle_is_reported_not_silent(self) -> None:
        service = rb.RollbackService(FeaturePaths(self.root / "cycle"))
        rows = [row("r17-c1", depends_on=["r17-c2"]), row("r17-c2", depends_on=["r17-c1"])]
        plan = service.plan(rows, "r17-c1")
        self.assertTrue(plan["cycle_detected"])
        self.assertEqual(["r17-c1", "r17-c2"], sorted(plan["cycle_members"]))
        self.assertEqual(len(plan["order"]), len(set(plan["order"])))
        self.assertIn(rb.NOTICE_CYCLE_DETECTED, [n["code"] for n in plan["notices"]])

    def test_tp13_chain_prev_hash_is_the_intervention_record(self) -> None:
        record = row("r17-chain", status="pending_confirmation")
        record.update({"requires_action": True, "requires_action_reason": "R1-7 终验",
                       "intervention_options": [
                           {"id": "opt-approve", "kind": "approve",
                            "label": {"zh": "批准", "en": "Approve"},
                            "consequence": {"zh": "继续", "en": "Continue"}},
                           {"id": "opt-modify", "kind": "modify",
                            "label": {"zh": "修改", "en": "Modify"},
                            "consequence": {"zh": "改后继续", "en": "Continue edited"}},
                           {"id": "opt-reject", "kind": "reject",
                            "label": {"zh": "拒绝", "en": "Reject"},
                            "consequence": {"zh": "不执行", "en": "Do not run"}}]})
        svc = self.service("chain", [record])
        svc.intervene({"action_id": "r17-chain", "decision": "approve",
                       "reason": "R1-7 终验链序"},
                      request_id="r17-intervene")
        intervention = [r for r in svc.store.all()
                        if r.get("action_type") == "intervention"][-1]
        self.assertTrue(self.verify(svc, intervention)["ok"])
        out = svc.rollback_execute({"action_id": "r17-chain"}, actor_id="qa-r17",
                                   actor_role="admin", request_id="r17-chain-rb")
        self.assertEqual(intervention["entry_hash"], out["rollback_record"]["prev_hash"])
        self.assertTrue(self.verify(svc, out["rollback_record"])["ok"])
        self.assertTrue(self.verify(svc, svc.store.get("r17-chain"))["ok"])


# --------------------------------------------------------------------------- #
# A2：执行器回执契约（TP-08 / TP-09 / TP-10）
# --------------------------------------------------------------------------- #

class ReceiptContractTests(R17Case):
    def run_with(self, applier):
        service = rb.RollbackService(FeaturePaths(self.root / "receipt"), applier=applier)
        rows = [row("r17-receipt", tool="filesystem.remove")]
        try:
            service.execute(rows, "r17-receipt", actor="qa-r17")
            return service, None
        except rb.RollbackError as exc:
            return service, exc

    def test_default_receipt_is_simulated_and_copy_stays_honest(self) -> None:
        service = rb.RollbackService(FeaturePaths(self.root / "sim"))
        rows = [row("r17-sim", tool="notes.write")]
        out = service.execute(rows, "r17-sim", actor="qa-r17")
        entry = service.journal()[-1]
        self.assertEqual(rb.SIMULATED_RECEIPT_STATE, entry["result"]["receipt_state"])
        text = json.dumps([out["rollback_record"], entry], ensure_ascii=False)
        for word in ("已恢复", "已撤销", "已上链"):
            self.assertNotIn(word, text, f"回执/记录文案不得宣称 {word}")

    def test_out_of_range_state_and_inconsistent_note_are_rejected(self) -> None:
        def bad_state(record, inverse, **_kw):
            return {"receipt_state": "executed_external_system", "note": "越界"}

        def lying_note(record, inverse, **_kw):
            return {"receipt_state": rb.REGISTRY_RECEIPT_STATE,
                    "note": "本地模拟：外部系统未调用"}

        self.assertEqual(sorted(rb.RECEIPT_STATES),
                         ["executed_registry", "executed_sandbox", "failed",
                          "simulated_local"])
        _svc, exc = self.run_with(bad_state)
        self.assertIsNotNone(exc)
        self.assertEqual("receipt_state", exc.field)
        self.assertEqual(rb.FAILED_RECEIPT_STATE, rb.FAILED_RECEIPT_STATE)
        _svc2, exc2 = self.run_with(lying_note)
        self.assertIsNotNone(exc2)
        self.assertEqual("note", exc2.field)

    def test_applier_failure_lands_failed_entry(self) -> None:
        def boom(record, inverse, **_kw):
            raise rb.RollbackError("ROLLBACK_INVERSE_FAILED", "执行器崩溃", field="applier")

        service, exc = self.run_with(boom)
        self.assertIsNotNone(exc)
        entry = service.journal()[-1]
        self.assertEqual(rb.PHASE_FAILED, entry.get("phase"))
        self.assertEqual(rb.FAILED_RECEIPT_STATE, entry["result"]["receipt_state"])
        self.assertIsNone(entry.get("rollback_record_id"))


# --------------------------------------------------------------------------- #
# A6 + 门禁 + 术语（TP-27/28/29/30/32）
# --------------------------------------------------------------------------- #

class FrontendAndGateTests(unittest.TestCase):
    def test_tp27_tp28_css_rules_are_landed(self) -> None:
        css = (ROOT / "agent_explainer" / "web" / "ext" / "cards.css").read_text(encoding="utf-8")
        self.assertGreater(len(css.encode("utf-8")), 2000)
        self.assertIn('article.card[data-rollback="rolled_back"] .card__l1', css)
        rolled_block = css.split('article.card[data-rollback="rolled_back"] .card__l1', 1)[1]
        rolled_block = rolled_block.split("}", 1)[0]
        self.assertIn("line-through", rolled_block)
        self.assertIn("var(--c-text-dim)", rolled_block)
        self.assertIn(".cards__rolled", css)
        self.assertIn('content: "\\2504\\2504"', css)          # cards__ref 的链片字形
        self.assertIn('.timeline__tick[data-rolled="rolled_back"] .timeline__dot', css)
        self.assertIn("border: var(--line-1) dashed", css)
        styles = (ROOT / "agent_explainer" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("cards__", styles, "styles.css 不得出现 cards__ 边界")

    def test_tp29_i18n_symmetry(self) -> None:
        def flatten(node: dict, prefix: str = "") -> set[str]:
            out: set[str] = set()
            for key, value in node.items():
                name = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    out |= flatten(value, name)
                else:
                    out.add(name)
            return out

        zh = flatten(json.loads((ROOT / "agent_explainer/web/i18n/zh.json").read_text(encoding="utf-8")))
        en = flatten(json.loads((ROOT / "agent_explainer/web/i18n/en.json").read_text(encoding="utf-8")))
        cards_zh = {k for k in zh if k.startswith("cards.rollback")}
        cards_en = {k for k in en if k.startswith("cards.rollback")}
        self.assertTrue(cards_zh, "cards.rollback.* 不得为空")
        self.assertEqual(cards_zh, cards_en, "cards.rollback.* zh/en 必须键集相同")
        inverse_zh = {k for k in zh if k.startswith("rollback.inverse")}
        inverse_en = {k for k in en if k.startswith("rollback.inverse")}
        self.assertEqual(inverse_zh, inverse_en)
        self.assertEqual([], sorted((zh | en) - (zh & en))[:20], "双语字典不得缺键")

    def test_tp30_contract_carries_rollback_routes(self) -> None:
        contract = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
        routes = {(r["path"], r["verb"]): r for r in contract["routes"]}
        post = routes.get(("/api/rollback", "POST"))
        get = routes.get(("/api/rollback/plan", "GET"))
        self.assertIsNotNone(post, "POST /api/rollback 必须在契约里")
        self.assertIsNotNone(get, "GET /api/rollback/plan 必须在契约里")
        self.assertEqual("permission", post["auth"])
        self.assertEqual(["intervene.decide"], post["abilities"])
        self.assertTrue(post["csrf"])
        self.assertTrue(contract["doc_crosscheck"]["in_sync"])
        self.assertNotIn("/api/rollback", contract["frontend_endpoints"]["unknown_paths"])

    def test_tp32_next_ref_stays_out_of_contracts(self) -> None:
        schema = (ROOT / "docs" / "schemas" / "action_record.schema.json").read_text(encoding="utf-8")
        contract = (ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8")
        self.assertNotIn("next_ref", schema)
        self.assertNotIn("next_ref", contract)
        copy = json.loads((ROOT / "agent_explainer/web/i18n/zh.json").read_text(encoding="utf-8"))
        cards = copy.get("cards") or {}
        negatives = ("不会", "不得", "不写", "不是", "绝不", "禁止", "不宣称")
        for key, value in cards.items():
            text = str(value)
            for word in ("已恢复", "已上链", "Merkle 根"):
                if word not in text:
                    continue
                # 展示纪律 0.4：否定式豁免（「不会写成已恢复」是禁令本身，不是宣称）
                self.assertTrue(any(neg in text for neg in negatives),
                                f"{key} 出现肯定式禁语 {word}：{text}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
