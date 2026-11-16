"""R2-2 验收：低置信联动 `<0.5 → requires_action>`（REQ-002 / B2-3）+ R2-1 遗留三项。

对应 `TASK-R2-2-confidence-linkage.md` §4 的验收条款，逐组映射：

* **A 联动端到端**（0.31 导入 → 翻牌 + C1 全套合规；0.5 边界不翻；已结束不翻；
  已有决定不翻；合并产物同样翻；三条选项已够用就不换）→ ``LinkageEndToEndTests``
* **B 决定选项合规**（R2-1 遗留①：``kind=modify ⇒ editable_fields ≥1``）
  → ``InterventionOptionComplianceTests``
* **C 事实指纹**（R2-1 遗留②：落盘记录的 ``source_facts_hash`` 必须对应**最终固化后**的事实）
  → ``FactsHashAlignmentTests``
* **D 上报帧记账**（R2-1 遗留③：``changed_fields`` 并入固化 / 联动实际写入的字段）
  → ``FrameAccountingTests``
* **E 夹具兼容与历史行**（§2.4 夹具核查、§1.4 历史行不回填）→ ``FixtureCompatibilityTests``
* **F 铁律静态自证**（签名域零改动、零新端点、零运行期改判入口、临时目录走 scratch）
  → ``IronRuleStaticTests``

运行：

    cd "C:\\TraceLens\\agent-explainer"
    python -X utf8 -m unittest tests.test_confidence_linkage -v
    python -m pytest tests/test_confidence_linkage.py -q        # 有 pytest 时同口径

临时目录一律走 ``tests/scratch.py`` 的探测/退回约定（§1.3：不碰 ``tempfile`` 的标准临时目录）。
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import shutil
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in (REPO, REPO / "tests", REPO / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir, scratch_reason  # noqa: E402  临时目录一律走 scratch

import facts_hash  # noqa: E402  产品口径的事实指纹（tools/facts_hash.py）
from agent_explainer import confidence as cf  # noqa: E402
from agent_explainer import ingest as ing  # noqa: E402
from agent_explainer import secretariat as sec_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import store as st  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((REPO / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
DEMO = json.loads((REPO / "samples" / "action_records.json")
                  .read_text(encoding="utf-8"))["records"]
SAMPLE = DEMO[0]
EXAMPLES = json.loads((REPO / "docs" / "schemas" / "examples" / "action_record.examples.json")
                      .read_text(encoding="utf-8"))["records"]


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def base_record(aid: str = "act-r22-base", **over) -> dict:
    """演示种子的一条：字段齐全、schema v1.1 合法、未签名（与 R2-1 测试同口径）。"""
    rec = copy.deepcopy(SAMPLE)
    rec["id"] = aid
    rec["parent_action_id"] = None
    rec.pop("received_at", None)
    rec.update(over)
    return rec


def running_record(aid: str = "act-r22-running", **over) -> dict:
    """进行中、没人拍过板的记录：低置信联动的合法翻牌对象。"""
    rec = base_record(aid, status="running", requires_action=False,
                      requires_action_reason=None, intervention_options=[],
                      user_intervention=None)
    rec.update(over)
    return rec


def needs_decision_record(aid: str, options, **over) -> dict:
    """把选项清单挂进一条**本身已合规**的「要人拍板」记录里再验 schema。

    为什么绕这一步：``tools/jsonschema_mini.py`` 的 ``MiniValidator`` 不能单独校验带
    ``$ref`` 的子 schema（R2-1 遗留④，未修），所以 ``interventionOption`` 的合规性
    只能整条记录一起验（R2-1 测试集同款做法）。
    """
    rec = base_record(aid, status="pending_confirmation", requires_action=True,
                      requires_action_reason="来源置信度低，需要人拍板",
                      intervention_options=options, user_intervention=None,
                      confidence=0.9, **over)
    policy = dict(rec.get("policy") or {})
    policy["requires_human_confirmation"] = True          # C1 要求
    rec["policy"] = policy
    rec.pop("uncertainty", None)                          # 0.9 不需 uncertainty（C7 只到 0.6）
    return rec


def with_fact_hash(rec: dict) -> dict:
    """把声明的事实指纹按产品口径算好写进去（模拟适配器在归一化时算好的那一步）。"""
    rec["human_readable"] = dict(rec.get("human_readable") or {})
    rec["human_readable"]["source_facts_hash"] = facts_hash.compute_facts_hash(rec)
    return rec


def service(name: str, **over) -> sv.InterveneService:
    """服务级装配：临时目录走 scratch，落盘 + 审计 + 入库签名都开（与产品默认一致）。"""
    folder = scratch_dir(name)
    svc = sv.InterveneService(records_store_path=folder / st.RECORD_FILE_NAME,
                              audit_log_path=folder / "audit.jsonl",
                              sign_on_write=True, **over)
    svc._r22_room = folder          # type: ignore[attr-defined]
    return svc


def import_records(svc: sv.InterveneService, records, **body) -> dict:
    """走产品入口 ``POST /api/import`` 的那条链（trace_adapter → 固化 + 联动 → 签名）。"""
    return svc.import_traces({"payload": list(records), **body}, request_id="req-r22")


def broker_pair(name: str):
    """实时上报面：``IngestBroker`` + ``StreamHub``（帧可从 hub 的环形缓冲读回）。"""
    store = DictStoreShim()
    hub = ing.StreamHub()
    return ing.IngestBroker(store, hub, mode="local", language="zh"), store, hub


def event(aid: str, **kw) -> dict:
    kw.setdefault("intent", "把月度报表上传到共享盘")
    return ing.make_event(aid, agent_id="r22-agent", actor_id="r22-actor",
                          workspace_id="r22-ws", session_id="r22-sess", **kw)


def frames_of(hub: ing.StreamHub) -> list[dict]:
    return hub.frames_after(0, limit=100)


def frames_of_type(hub: ing.StreamHub, event_type: str) -> list[dict]:
    return [f for f in frames_of(hub) if f.get("type") == event_type]


def changed_names(frame: dict) -> set[str]:
    return {str(c.get("field")) for c in (frame["data"].get("changed_fields") or [])}


class DictStoreShim:
    """最小记录库：与 ``IngestBroker`` 需要的 get/put/all 同接口（同 R2-1 测试口径）。"""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def get(self, key: str):
        return self.records.get(str(key))

    def put(self, record) -> None:
        self.records[str(record["id"])] = record

    def all(self):
        return list(self.records.values())


# --------------------------------------------------------------------------- #
# A. 联动端到端（docs/02 D10：<0.5 → 要人拍板，走既有「需要我」面板）
# --------------------------------------------------------------------------- #

class LinkageEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = service("r22-linkage-" + self.id().split(".")[-1])
        self.addCleanup(lambda: shutil.rmtree(self.svc._r22_room, ignore_errors=True))

    def test_import_031_flips_into_the_needs_me_panel_with_full_c1(self) -> None:
        """§4.2 主用例：0.31 导入 → requires_action=true + 三选项 + reason + policy，schema 零错。"""
        rec = running_record("act-r22-flip-031", confidence=0.31)
        rec.pop("uncertainty", None)
        outcome = import_records(self.svc, [rec])
        self.assertEqual(1, outcome["created"])
        stored = self.svc.store.get("act-r22-flip-031")
        self.assertEqual(0.31, stored["confidence"])
        self.assertTrue(stored["requires_action"], "低置信必须自动挂到「需要我」")
        self.assertEqual("pending_confirmation", stored["status"], "C1：状态只能落在可决定集")
        self.assertEqual({"approve", "modify", "reject"},
                         {o["kind"] for o in stored["intervention_options"]},
                         "C1：三条决定选项齐备（面板才点得动，不是假卡片）")
        self.assertGreaterEqual(len(str(stored["requires_action_reason"] or "")), 4,
                                "C1：为什么要拍板必须写清（≥4 字）")
        self.assertTrue(stored["policy"]["requires_human_confirmation"],
                        "§2.1：policy.requires_human_confirmation 必须同步为 true")
        self.assertIsNotNone(stored["uncertainty"], "C7：低置信必须说明哪里不确定")
        self.assertEqual([], schema_errors(stored), "联动之后必须是一条合法记录")
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "联动写在签名之前 ⇒ 签名覆盖联动结果")
        self.assertEqual("valid", checks["entry_hash"])

    def test_confidence_exactly_half_does_not_flip(self) -> None:
        """翻牌边界：``< 0.5`` 才翻；0.5 这一档是 yellow，不弹卡片，但 C7 的 uncertainty 要有。"""
        rec = running_record("act-r22-edge-050", confidence=0.5)
        rec.pop("uncertainty", None)
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-edge-050")
        self.assertEqual(0.5, stored["confidence"])
        self.assertFalse(stored["requires_action"], "0.5 不在 <0.5 区间：不得翻")
        self.assertEqual("running", stored["status"], "不翻就不动 status")
        self.assertIsNotNone(stored["uncertainty"], "C7：≤0.6 仍必须带 uncertainty")
        self.assertEqual([], schema_errors(stored))

    def test_finished_step_is_not_flipped(self) -> None:
        """边界保持（§2.1）：已结束的步骤弹「要人拍板」是假卡片 ⇒ 不翻，如实落盘。"""
        rec = base_record("act-r22-done-low", status="succeeded", requires_action=False,
                          intervention_options=[], confidence=0.31)
        rec.pop("uncertainty", None)
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-done-low")
        self.assertEqual(0.31, stored["confidence"])
        self.assertFalse(stored["requires_action"], "已结束 ⇒ 不翻")
        self.assertEqual("succeeded", stored["status"])
        self.assertEqual([], stored["intervention_options"], "不翻就不塞选项")
        self.assertIsNotNone(stored["uncertainty"], "C7 照旧补齐")
        self.assertEqual([], schema_errors(stored))

    def test_record_with_an_existing_decision_is_not_flipped(self) -> None:
        """边界保持（§2.1）：已有 ``user_intervention`` ⇒ 不翻（C1 要求此时 user_intervention 为 null）。"""
        decided = next(r["user_intervention"] for r in DEMO if r.get("user_intervention"))
        opts = copy.deepcopy(ing.default_intervention_options(
            "act-r22-decided", intent="清理演示相册里的重复项", action_type="delete"))
        rec = base_record("act-r22-decided", status="observed", requires_action=False,
                          requires_action_reason=None, intervention_options=opts,
                          user_intervention=copy.deepcopy(decided), confidence=0.31)
        rec.pop("uncertainty", None)
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-decided")
        self.assertEqual("observed", stored["status"], "observed 在可决定集内，唯一拦住翻牌的是已有决定")
        self.assertFalse(stored["requires_action"], "已有决定 ⇒ 不翻（不制造 C1 非法记录）")
        self.assertEqual([o["id"] for o in opts],
                         [o["id"] for o in stored["intervention_options"]], "不翻就不换选项")
        self.assertIsNotNone(stored["user_intervention"])
        self.assertEqual([], schema_errors(stored))

    def test_upsert_flips_the_merged_product_and_resigns(self) -> None:
        """合并产物分支也接联动：0.9 → 0.31 的 upsert 之后，合并产物必须挂到「需要我」并重签。"""
        import_records(self.svc, [running_record("act-r22-merge-low", confidence=0.9)])
        self.assertFalse(self.svc.store.get("act-r22-merge-low")["requires_action"])
        outcome = import_records(self.svc, [running_record("act-r22-merge-low", confidence=0.31)])
        self.assertEqual(1, outcome["updated"])
        stored = self.svc.store.get("act-r22-merge-low")
        self.assertEqual(0.31, stored["confidence"])
        self.assertTrue(stored["requires_action"], "合并产物同样要联动")
        self.assertEqual("pending_confirmation", stored["status"])
        self.assertEqual({"approve", "modify", "reject"},
                         {o["kind"] for o in stored["intervention_options"]})
        self.assertEqual([], schema_errors(stored))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "合并 → 联动 → 重签：联动必须在签名之前")

    def test_linkage_keeps_options_that_are_already_enough(self) -> None:
        """来源自带合规三条 ⇒ 联动只翻牌，不替换来源的选项（id 逐字保留）。"""
        opts = copy.deepcopy(ing.default_intervention_options(
            "act-r22-keep-opts", intent="导出客户名单", action_type="export"))
        rec = running_record("act-r22-keep-opts", confidence=0.28, intervention_options=opts)
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-keep-opts")
        self.assertTrue(stored["requires_action"])
        self.assertEqual([o["id"] for o in opts], [o["id"] for o in stored["intervention_options"]],
                         "三条已够用 ⇒ 不覆盖来源自己给的选项")

    def test_insufficient_modify_options_are_replaced_with_compliant_ones(self) -> None:
        """来源的三条里 modify 带 ``editable_fields: []`` ⇒ 是「点得开、没表单」的空卡片，联动必须换成合规三条。"""
        forged = [{"id": "opt-a", "kind": "approve", "label": "同意", "consequence": "按来源范围执行"},
                  {"id": "opt-b", "kind": "modify", "label": "改范围", "consequence": "只跑设定范围",
                   "editable_fields": []},
                  {"id": "opt-c", "kind": "reject", "label": "先不执行", "consequence": "停下来等决定",
                   "requires_reason": True}]
        rec = running_record("act-r22-empty-modify", confidence=0.22, intervention_options=forged)
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-empty-modify")
        self.assertTrue(stored["requires_action"])
        modify = [o for o in stored["intervention_options"] if o["kind"] == "modify"]
        self.assertEqual(1, len(modify))
        self.assertGreaterEqual(len(modify[0]["editable_fields"]), 1,
                                "C1 + interventionOption：modify 必须带 ≥1 个可改字段")
        self.assertEqual([], schema_errors(stored))

    def test_sdk_event_builder_flips_before_the_field_gate(self) -> None:
        """SDK 直连组装器（``make_event``）：低置信事件在字段封闭闸门之前就翻好，补齐 C1 全套。"""
        ev = event("act-r22-mkevent-low", status="running", confidence=0.2)
        self.assertTrue(ev["requires_action"], "上报组装器也接联动（签名链上游）")
        self.assertEqual("pending_confirmation", ev["status"])
        self.assertEqual({"approve", "modify", "reject"},
                         {o["kind"] for o in ev["intervention_options"]})
        self.assertGreaterEqual(len(str(ev["requires_action_reason"] or "")), 4)
        self.assertTrue(ev["policy"]["requires_human_confirmation"])
        self.assertIsNotNone(ev["uncertainty"], "C7：低置信必须带 uncertainty")
        self.assertIn("confidence_source", ev, "固化与联动在同一次写入里完成")
        for opt in ev["intervention_options"]:
            for key in ("id", "kind", "label", "consequence"):
                self.assertIn(key, opt, "决定选项的必填四项")
            self.assertGreaterEqual(len(opt["consequence"]), 4)
        self.assertEqual([], schema_errors(needs_decision_record(
            "act-r22-mkevent-low", ev["intervention_options"])),
            "联动补齐的三条挂进合法记录后必须过 schema")


# --------------------------------------------------------------------------- #
# B. 决定选项合规（R2-1 遗留①：editable_fields 不合 schema）
# --------------------------------------------------------------------------- #

class InterventionOptionComplianceTests(unittest.TestCase):
    def test_default_modify_option_carries_a_real_editable_field(self) -> None:
        """遗留①：默认 modify 选项必须带 ≥1 个可改字段（原来是 ``[]``，不合 schema）。"""
        opts = ing.default_intervention_options("act-r22-def-opts", intent="删除 12 个演示文件",
                                                action_type="delete")
        self.assertEqual(["approve", "modify", "reject"], [o["kind"] for o in opts])
        modify = opts[1]
        self.assertEqual(1, len(modify["editable_fields"]),
                         "来源没给字段清单时给且只给一项：改写执行范围")
        field = modify["editable_fields"][0]
        self.assertEqual("scope", field["name"])
        for key in ("name", "label", "type"):
            self.assertIn(key, field)
        self.assertEqual("text", field["type"])
        self.assertEqual([], schema_errors(needs_decision_record("act-r22-def-opts", opts)),
                         "三条挂进合法记录后必须整体过 schema（C1 + interventionOption）")

    def test_make_event_requires_action_options_are_schema_compliant(self) -> None:
        """遗留① 的原缺陷形态：SDK 组装器给出 ``requires_action=true`` 且来源没给字段清单
        ⇒ 三条选项必须过 schema（原形态 modify 带 ``editable_fields: []``，A2 校验当场拦下）。"""
        ev = event("act-r22-mkevent-req", action_type="delete",
                   status="pending_confirmation", requires_action=True,
                   intent="删除 12 个演示文件", confidence=0.9)
        self.assertTrue(ev["requires_action"])
        modify = [o for o in ev["intervention_options"] if o["kind"] == "modify"][0]
        self.assertGreaterEqual(len(modify["editable_fields"]), 1, "不能是空数组")
        self.assertEqual([], schema_errors(needs_decision_record(
            "act-r22-mkevent-req", ev["intervention_options"])), "组装器给的三条必须合规")

    def test_source_provided_editable_fields_are_kept_verbatim(self) -> None:
        """来源真给了字段 ⇒ 逐字用它，不替换、不追加（不凭空发明参数）。"""
        given = [{"name": "max_delete_count", "label": "最多删除几个", "type": "integer",
                  "min": 0, "max": 50, "unit": "个"}]
        ev = event("act-r22-given-fields", action_type="delete", status="pending_confirmation",
                   requires_action=True, intent="删除演示文件", confidence=0.9,
                   editable_fields=given)
        modify = [o for o in ev["intervention_options"] if o["kind"] == "modify"][0]
        self.assertEqual(given, modify["editable_fields"], "来源给的字段清单逐字送达")
        self.assertNotIn("可改字段", str(modify["risk_note"] or ""),
                         "来源给了字段就不必再写『未提供可改字段』")
        self.assertEqual([], schema_errors(needs_decision_record(
            "act-r22-given-fields", ev["intervention_options"])))

    def test_risk_note_says_what_the_default_field_is(self) -> None:
        """遗留① 的文案：原文案「只能整体同意或拒绝」在新形态下是假话，必须改成如实说明。"""
        opts = ing.default_intervention_options("act-r22-note", intent="整理演示相册",
                                                action_type="write")
        note = str(opts[1]["risk_note"] or "")
        self.assertIn("改写执行范围", note)
        self.assertNotIn("只能整体同意或拒绝", note, "文案不得否认自己刚给出的可改字段")


# --------------------------------------------------------------------------- #
# C. 事实指纹对齐（R2-1 遗留②：source_facts_hash 必须对应最终固化后的事实）
# --------------------------------------------------------------------------- #

class FactsHashAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = service("r22-hash-" + self.id().split(".")[-1])
        self.addCleanup(lambda: shutil.rmtree(self.svc._r22_room, ignore_errors=True))

    def test_legal_import_leaves_the_fact_hash_untouched(self) -> None:
        """幂等：合规数值经固化是 ``round(x, 4)``，事实没变 ⇒ 指纹一个字都不动（既有断言不漂）。"""
        rec = base_record("act-r22-hash-legal", confidence=0.9)
        declared = (rec["human_readable"] or {}).get("source_facts_hash")
        self.assertTrue(declared, "演示夹具自带 P3 指纹（tools/validate_schema.py 机器复算过）")
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-hash-legal")
        self.assertEqual(declared, stored["human_readable"]["source_facts_hash"],
                         "没改事实就不许重算出第二个指纹")
        self.assertEqual(facts_hash.compute_facts_hash(stored),
                         stored["human_readable"]["source_facts_hash"])

    def test_illegal_confidence_hash_follows_the_final_fact(self) -> None:
        """遗留② 点名用例：非法数打成 ``null`` ⇒ 落盘指纹必须对应打成 null 之后的事实。"""
        rec = with_fact_hash(base_record("act-r22-hash-illegal", confidence="1.7",
                                         status="running", intervention_options=[]))
        pre_hash = rec["human_readable"]["source_facts_hash"]
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-hash-illegal")
        self.assertIsNone(stored["confidence"], "非法数打成 null（不夹紧、不编造）")
        self.assertEqual(facts_hash.compute_facts_hash(stored),
                         stored["human_readable"]["source_facts_hash"],
                         "声明的指纹必须等于最终落盘事实的指纹")
        self.assertNotEqual(pre_hash, stored["human_readable"]["source_facts_hash"])
        self.assertNotEqual(facts_hash.compute_facts_hash(dict(stored, confidence=1.7)),
                            stored["human_readable"]["source_facts_hash"],
                            "指纹若算在固化之前就是现在这个值：证明对齐发生在固化之后")
        self.assertEqual([], schema_errors(stored))

    def test_linkage_status_change_is_inside_the_hash(self) -> None:
        """联动改了 ``status``（``FACT_FIELDS`` 含 status）⇒ 指纹必须包含翻牌后的状态。"""
        rec = with_fact_hash(running_record("act-r22-hash-flip", confidence=0.31))
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r22-hash-flip")
        self.assertEqual("pending_confirmation", stored["status"])
        readable = stored["human_readable"]
        self.assertEqual(facts_hash.compute_facts_hash(stored), readable["source_facts_hash"])
        self.assertNotEqual(facts_hash.compute_facts_hash(dict(stored, status="running")),
                            readable["source_facts_hash"])
        self.assertEqual([], schema_errors(stored))

    def test_import_create_path_copy_follows_the_flipped_status(self) -> None:
        """R2-3 前置微修（PM-VERDICT-R2-2 §3 第 1 条，批准的 L1）：导入的**新建路径**也按最终事实重算文案。

        联动把 ``status`` 翻成 ``pending_confirmation`` 之后，界面角标说「等你确认」、
        l1 还写着「进行中」正是 ``READABLE_FACT_FIELDS`` 要防的自相矛盾。
        实时上报面（``IngestBroker`` 新建路径，ingest.py:1127-1138）一直有这一环，
        ``server.import_traces`` 的新建路径缺它 —— 本条把它钉住，口径与逐字对齐的那段一致：
        快照 → 固化 → 记账 → 只在固化碰到事实字段时重算 → 对齐指纹 → 签名。
        """
        aid = "act-r23-create-copy"
        rec = running_record(aid, confidence=0.28)
        rec["human_readable"] = ing.human_readable_for(
            aid, intent=str(rec.get("intent") or ""),
            action_type=str(rec.get("action_type") or "tool_call"), status="running",
            tool_used=str((rec.get("tool_used") or {}).get("name") or ""), language="zh")
        with_fact_hash(rec)
        self.assertIn(ing.STATUS_LABELS["running"], rec["human_readable"]["l1"],
                      "用例前提：入参文案写的是「进行中」")
        import_records(self.svc, [rec])
        stored = self.svc.store.get(aid)
        self.assertEqual("pending_confirmation", stored["status"])
        self.assertIn(ing.STATUS_LABELS["pending_confirmation"], stored["human_readable"]["l1"],
                      "新建路径必须在签名之前按翻牌后的 status 重算 l1（与上报面同口径）")
        self.assertNotIn(ing.STATUS_LABELS["running"], stored["human_readable"]["l1"],
                         "角标要人拍板、文案还写着进行中 = 自相矛盾，禁止落盘")
        self.assertEqual([str(rec["intent"])], stored["human_readable"]["l2"],
                         "上报方自己声明的补充说明原样保留：重算不丢信息")
        self.assertEqual("template", stored["human_readable"]["generated_by"],
                         "这句话是 ingest 的模板拼出来的，导入面就照实说（v1.1 枚举）")
        self.assertEqual(facts_hash.compute_facts_hash(stored),
                         stored["human_readable"]["source_facts_hash"],
                         "重算文案不得把声明的指纹写没：resync 必须跟着翻牌后的最终事实")
        self.assertEqual([], schema_errors(stored))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "重算写在签名之前 ⇒ 签名覆盖重算后的文案")

    def test_import_create_path_keeps_the_copy_when_no_fact_moved(self) -> None:
        """幂等边界：固化没碰到事实字段（0.9 合规、不翻牌）⇒ 文案与声明的指纹一个字都不动。"""
        rec = base_record("act-r23-create-keep", confidence=0.9)
        uploaded = copy.deepcopy(rec["human_readable"])
        self.assertTrue(uploaded.get("source_facts_hash"), "演示夹具自带 P3 指纹")
        import_records(self.svc, [rec])
        stored = self.svc.store.get("act-r23-create-keep")
        self.assertFalse(stored["requires_action"], "0.9 不翻牌 ⇒ 不该触发重算")
        for key in ("l1", "l2", "l3", "language", "generated_by", "template_id"):
            self.assertEqual(uploaded[key], stored["human_readable"][key],
                             f"没改事实就不许动上报文案的 {key}")
        self.assertEqual(uploaded["source_facts_hash"], stored["human_readable"]["source_facts_hash"],
                         "没改事实就不许重算出第二个指纹（护栏 P3 的幂等口径）")
        self.assertEqual([], schema_errors(stored))

    def test_merge_path_hash_follows_the_merged_facts(self) -> None:
        """合并路径：合并 + 联动之后的事实才是最终事实，指纹必须跟着它（顺序调整的用后效果）。"""
        import_records(self.svc, [running_record("act-r22-hash-merge", confidence=0.9)])
        import_records(self.svc, [running_record("act-r22-hash-merge", confidence=0.33)])
        stored = self.svc.store.get("act-r22-hash-merge")
        self.assertTrue(stored["requires_action"])
        self.assertEqual(facts_hash.compute_facts_hash(stored),
                         stored["human_readable"]["source_facts_hash"])

    def test_sdk_reported_readable_keeps_null_hash_and_final_status_text(self) -> None:
        """实时上报面：``human_readable_for`` 的口径是 hash=null ⇒ 不替它发明指纹；
        但联动翻了 status 之后，文案必须跟着最终事实（角标与 l1 不许自相矛盾）。"""
        broker, store, hub = broker_pair("r22-broker-create")
        broker.ingest({"events": [event("act-r22-broker-low", status="running",
                                        confidence=0.28)]})
        stored = store.get("act-r22-broker-low")
        self.assertTrue(stored["requires_action"])
        self.assertEqual("pending_confirmation", stored["status"])
        self.assertIsNone(stored["human_readable"]["source_facts_hash"],
                          "SDK 上报文案的指纹口径是 null：resync 不得无中生有")
        self.assertIn(ing.STATUS_LABELS["pending_confirmation"], stored["human_readable"]["l1"],
                      "固化必须在重算文案之前：l1 不得还写着「进行中」")
        self.assertEqual([], self.v11_gaps(stored))

    def test_broker_merge_text_follows_the_flipped_status(self) -> None:
        """遗留② 的接线效果（合并路径）：固化挪到 ``_refresh_readable`` 之前 ⇒ 文案与状态一致。"""
        broker, store, _hub = broker_pair("r22-broker-merge")
        broker.ingest({"events": [event("act-r22-broker-merge", status="running",
                                        confidence=0.9)]})
        self.assertIn(ing.STATUS_LABELS["running"], store.get("act-r22-broker-merge")["human_readable"]["l1"])
        broker.ingest({"mode": "upsert",
                       "events": [event("act-r22-broker-merge", status="running",
                                        confidence=0.27)]})
        stored = store.get("act-r22-broker-merge")
        self.assertEqual("pending_confirmation", stored["status"])
        self.assertIn(ing.STATUS_LABELS["pending_confirmation"], stored["human_readable"]["l1"])
        self.assertNotIn(ing.STATUS_LABELS["running"], stored["human_readable"]["l1"])
        self.assertEqual([], self.v11_gaps(stored))

    def v11_gaps(self, stored: dict) -> list[str]:
        """上报面记录的 schema 差异里，剔除**本任务之前就存在**的 SDK 口径项。

        ``make_event`` / ``human_readable_for`` 产出的记录走的是 ``IngestBroker`` 自己的字段封闭闸门，
        不是 v1.1 的 ``MiniValidator``：``generated_by="sdk.v1"``、``source_facts_hash=null``、
        ``suggested_next=null`` 是 4.x/5.x 定下的既有口径（R2-2 铁律：不回填、不改这些字段）。
        所以这里只验"联动有没有把记录改出新的非法形态"，不替既有产品边界背书。
        """
        pre_existing = ("generated_by", "source_facts_hash", "suggested_next")
        return [e for e in schema_errors(stored)
                if not any(e.endswith(f"@ $.human_readable.{p}") or e.endswith(f"@ $.{p}")
                           for p in pre_existing)]


# --------------------------------------------------------------------------- #
# D. 上报帧记账（R2-1 遗留③：changed_fields 并入固化 / 联动写入的字段）
# --------------------------------------------------------------------------- #

class FrameAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = service("r22-frame-" + self.id().split(".")[-1])
        self.addCleanup(lambda: shutil.rmtree(self.svc._r22_room, ignore_errors=True))

    def test_broker_update_frame_accounts_the_linkage(self) -> None:
        """实时上报合并帧：联动实际写入的字段必须逐条进 ``changed_fields``（前端才看得见"为什么开始要人拍板"）。"""
        broker, _store, hub = broker_pair("r22-frame-broker")
        broker.ingest({"events": [event("act-r22-frame-merge", status="running",
                                        confidence=0.9)]})
        broker.ingest({"mode": "upsert",
                       "events": [event("act-r22-frame-merge", status="running",
                                        confidence=0.3)]})
        frame = frames_of_type(hub, ing.EVENT_UPDATED)[-1]
        names = changed_names(frame)
        self.assertLessEqual({"confidence", "requires_action", "requires_action_reason", "status"},
                             names, "固化/联动写入的字段要并入记账")
        self.assertIn("human_readable", names, "既有的文案重算条目不得丢")
        for entry in frame["data"]["changed_fields"]:
            self.assertEqual({"field", "before", "after"}, set(entry),
                             "条目形状与合并层逐字同款，前端不用分两种读法")
        fixation_names = {f for f in names if f in set(ing.FIXATION_TRACKED_FIELDS)}
        self.assertFalse(fixation_names & set(ing.ALL_PROTECTED_FIELDS),
                         "联动补齐的受保护字段（policy / 选项 / 文案）只落盘，不记进 changed_fields")

    def test_import_upsert_frame_accounts_the_linkage(self) -> None:
        """导入合并帧同理：``changed_fields`` 里看得见 requires_action / status 的来历。"""
        import_records(self.svc, [running_record("act-r22-frame-import", confidence=0.9)])
        import_records(self.svc, [running_record("act-r22-frame-import", confidence=0.34)])
        frame = frames_of_type(self.svc.stream_hub, ing.EVENT_UPDATED)[-1]
        names = changed_names(frame)
        self.assertLessEqual({"confidence", "requires_action", "status"}, names)
        self.assertFalse(names & set(ing.ALL_PROTECTED_FIELDS),
                         "导入合并帧也不把受保护字段记进 changed_fields")

    def test_created_frame_keeps_its_shape(self) -> None:
        """新建帧不发明 ``changed_fields``（无"前后"可比 ⇒ 沿用既有帧形状，不扩字段语义）。"""
        import_records(self.svc, [running_record("act-r22-frame-create", confidence=0.31)])
        frame = frames_of_type(self.svc.stream_hub, ing.EVENT_CREATED)[-1]
        self.assertNotIn("changed_fields", frame["data"])
        self.assertTrue(frame["data"]["record"]["requires_action"], "新建记录本身已带联动结果")

    def test_no_change_no_entry(self) -> None:
        """幂等：固化没改任何字段 ⇒ 记账为空列表（不刷假条目）。"""
        rec = running_record("act-r22-no-change", confidence=0.9)
        self.assertEqual([], ing.account_fixation_changes(rec, copy.deepcopy(rec)))

    def test_entries_are_the_fields_actually_written(self) -> None:
        """单元口径：只记"真变了"的字段，且限定在申报过的六个可上报字段内。"""
        before = running_record("act-r22-diff", confidence=0.9)
        after = cf.fixate_for_import(running_record("act-r22-diff", confidence=0.3))
        names = {c["field"] for c in ing.account_fixation_changes(before, after)}
        self.assertLessEqual(names, set(ing.FIXATION_TRACKED_FIELDS))
        self.assertIn("requires_action", names)
        self.assertNotIn("policy", names, "policy 是受保护字段：如实落盘，不进 changed_fields")


# --------------------------------------------------------------------------- #
# E. 夹具兼容与历史行（§2.4 / 铁律 4）
# --------------------------------------------------------------------------- #

class FixtureCompatibilityTests(unittest.TestCase):
    def test_demo_seed_bypasses_the_import_fixation(self) -> None:
        """§2.4 证据 A：演示数据是旁路装载（``_load_persisted`` 直接进库），不经导入固化链
        ⇒ 联动改不到它，夹具的 12 条字段逐字保持原样。"""
        svc = service("r22-seed", records=DEMO)
        self.addCleanup(lambda: shutil.rmtree(svc._r22_room, ignore_errors=True))
        for rec in DEMO:
            stored = svc.store.get(rec["id"])
            self.assertIsNotNone(stored)
            self.assertEqual(rec["requires_action"], stored["requires_action"],
                             f"{rec['id']} 的 requires_action 被联动改过：种子数据不该走导入链")
            self.assertEqual(rec["confidence"], stored["confidence"])
            self.assertNotIn("confidence_source", stored,
                             f"{rec['id']} 被固化写过：种子装载是旁路，不经签名前固化")

    def test_act_0004_example_is_a_schema_example_not_import_input(self) -> None:
        """§2.4 证据 B：docs/02:702 那个 ``act-0004-read-contracts``（0.45 / requires_action=false）
        住在 schema 示例文件里，消费方是 ``tools/validate_schema.py``；产品代码里一个字都没提它。
        两条理由各自都够它保持原样：① 不在导入链上；② 它是 ``status=succeeded`` 的已结束步骤，
        联动边界本来就不翻（§2.1 保留的边界）。"""
        fixture = next(r for r in EXAMPLES if r["id"] == "act-0004-read-contracts")
        self.assertEqual(0.45, fixture["confidence"])
        self.assertFalse(fixture["requires_action"])
        self.assertEqual("succeeded", fixture["status"])
        self.assertEqual([], schema_errors(fixture), "夹具本身必须继续通过 schema v1.1")
        self.assertEqual("red", cf.band(fixture["confidence"]), "0.45 落在 red 档")
        self.assertFalse(cf._request_action(dict(fixture), fixture["confidence"]),
                         "已结束的步骤 ⇒ 联动不翻（夹具无需硬改）")
        for src in (REPO / "agent_explainer" / "server.py", REPO / "agent_explainer" / "ingest.py",
                    REPO / "agent_explainer" / "trace_adapter.py"):
            self.assertNotIn("act-0004", src.read_text(encoding="utf-8"),
                             "产品侧不消费这个 schema 夹具")

    def test_no_sample_fixture_is_below_the_trigger(self) -> None:
        """§2.4 证据 C：导入夹具（``samples/``）里没有任何 ``confidence < 0.5`` 的记录
        ⇒ 既有导入断言不会因为联动改写低置信记录而失效。"""
        seen = 0
        for path in sorted((REPO / "samples").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            for rec in (doc.get("records") if isinstance(doc, dict) else doc) or []:
                if isinstance(rec, dict):
                    seen += 1
                    conf = rec.get("confidence")
                    if isinstance(conf, (int, float)):
                        self.assertGreaterEqual(conf, cf.ACTION_TRIGGER,
                                                f"{path.name}:{rec.get('id')} 低于触发线，需上报 PM")
        self.assertGreaterEqual(seen, 24, "扫描必须真的覆盖两份演示夹具（12 + 12）")

    def test_history_rows_are_not_backfilled(self) -> None:
        """铁律 4：已落盘的历史行不回填。历史行有低置信但没有 requires_action ⇒ 保持原样
        （历史行是当时的事实，不是现状断言）。"""
        historic = with_fact_hash(base_record(
            "act-r22-history", status="succeeded", requires_action=False,
            intervention_options=[], confidence=0.31,
            uncertainty={"reason": "当时来源就给了 0.31，低置信",
                         "what_to_check": "核对来源原始记录",
                         "affected_fields": ["confidence"]}))
        snapshot = json.dumps(historic, ensure_ascii=False, sort_keys=True)
        svc = service("r22-backfill", records=[historic])
        self.addCleanup(lambda: shutil.rmtree(svc._r22_room, ignore_errors=True))
        import_records(svc, [running_record("act-r22-newcomer", confidence=0.29)])
        self.assertTrue(svc.store.get("act-r22-newcomer")["requires_action"], "新产物照常联动")
        self.assertEqual(snapshot, json.dumps(svc.store.get("act-r22-history"),
                                              ensure_ascii=False, sort_keys=True),
                         "历史行被回填了（铁律 4）")


# --------------------------------------------------------------------------- #
# F. 铁律静态自证
# --------------------------------------------------------------------------- #

class IronRuleStaticTests(unittest.TestCase):
    def test_signature_fields_untouched(self) -> None:
        """§1.1：``SIGNATURE_FIELDS`` 11 字段一字未动（联动全部写在签名之前）。"""
        self.assertEqual(("timestamp", "agent_id", "action_type", "tool", "input_hash",
                          "output_hash", "prev_hash", "entry_hash", "risk_level",
                          "confidence", "status"), tuple(sec_mod.SIGNATURE_FIELDS))
        self.assertEqual(11, len(sec_mod.SIGNATURE_FIELDS))
        self.assertIn("confidence", sec_mod.SIGNATURE_FIELDS,
                      "置信度在签名域里 ⇒ 联动必须写在签名之前（铁律 2）")

    def test_no_new_confidence_http_endpoint(self) -> None:
        """§1.2：零新增 HTTP 路由——联动只走既有「需要我」面板的数据面（``requires_action``）。"""
        source = (REPO / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'"/api/confidence', source),
                          "R2-2 不得新增置信度端点（policy 的端点接线不在本任务）")
        self.assertIsNone(cf.policy()["wiring"]["http_endpoint"])

    def test_no_runtime_override_entry(self) -> None:
        """铁律：模块不提供任何运行期改判入口（改判走追加补偿记录）。"""
        banned = ("set_confidence", "update_confidence", "override", "patch",
                  "mutate", "write_record", "change_confidence", "revise")
        self.assertFalse([n for n in cf.__all__ if any(b in n for b in banned)])

    def test_merge_allowlists_untouched(self) -> None:
        """§1.2：两张白名单零改动（``confidence`` / ``confidence_source`` 本来就在名单里）。"""
        self.assertIn("confidence", ing.MERGE_FIELDS)
        self.assertIn("confidence_source", ing.EXPANSION_MERGE_FIELDS)
        self.assertNotIn("confidence_source", ing.EXPANSION_PROTECTED_FIELDS)

    def test_frame_accounting_only_touches_reportable_fields(self) -> None:
        """遗留③ 的口径写进常量、不写进口头约定：记账名单 ⊆ 两张白名单，且不含受保护字段。"""
        self.assertLessEqual(set(ing.FIXATION_TRACKED_FIELDS),
                             set(ing.MERGE_FIELDS) | set(ing.EXPANSION_MERGE_FIELDS))
        self.assertFalse(set(ing.FIXATION_TRACKED_FIELDS) & set(ing.ALL_PROTECTED_FIELDS))

    def test_scratch_reason_is_reported_not_tempdir(self) -> None:
        """§1.3：临时目录来自 scratch 的探测/退回约定（本文件不碰标准临时目录）。"""
        self.assertTrue(scratch_reason())
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^import tempfile", source, re.M), "不许 import tempfile")
        self.assertIsNone(re.search(r"TemporaryDirectory\s*\(", source), "不许用标准临时目录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
