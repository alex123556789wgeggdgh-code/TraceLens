"""R5-2 · 代行语义（``on_behalf_of``）+ 责任认定书渲染增补 + ``_scrub`` 递归定档（REQ-005 B5-3）。

钉住的验收条款：**B5-3「所有协作操作记录完整，可审计追溯」** + 「代行部分权限」在责任链与
责任认定书中表达清楚（口径来源：PM-PLAN §7.1 冻结 + TASK-R5-2 §2 的 PM 裁决）。

四组口径，各有一组断言钉住：

1. **事件层标注**（§2.1）：``record_event(..., on_behalf_of=<真人 DID>)`` 把标注写进
   ``detail.on_behalf_of``；``actor_did`` **仍是实际操作人**（协作者本人）。缺省不传 →
   事件形状逐字不变（既有回归）。代行是**附加标注**，不是把 actor 换成老板的名字。
2. **认定书渲染增补**（§2.2）：动作条目只在**有标注**时多一行「由 X 代行 Y 行使权限，
   最终责任归 Y（human_owner）」；无标注的条目连键序都不变。``human_owner`` 字段不动、
   ``delegation_chain`` 结构不动、签名域 11 字段一字不动；认定书的离线可验证性不破坏
   （``verify_letter`` 直接吃落盘文件）。
3. **测试面**（§2.3）：链校验 ``verify_events`` 保持为真、账本只追加（逐字前缀自证）、
   ``can()`` 权限闸门行为不因标注而变宽（B5-2 归 R5-3，本轮只保证不越权）。
4. **``_scrub`` 递归定档**（§2.4，已批 L1）：嵌套/列表写法也打码；深度封顶；
   ``invite_id`` 一类键用**精确键名豁免**（选"子串匹配 + 显式豁免"而非整词匹配，理由写在
   ``sharing._SENSITIVE_DETAIL_EXEMPT`` 的注释里，本文件把两条口径都钉住）。

本文件**不碰 HTTP**（纯服务层：sharing + secretariat + 落盘账本），所以不引
``tests/http_fixture.py``；临时目录一律走 ``tests/scratch.py``（铁律 3）。

跑法：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest tests.test_on_behalf -q
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys
import unittest
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import secretariat as sec_mod  # noqa: E402
from agent_explainer import sharing as sh  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from agent_explainer.secretariat import Secretariat  # noqa: E402

#: 真人（human_owner）：责任链的链根，最终担责的那位
OWNER = cs.did_from_name("r52-owner", kind="user")
#: 另一位真人：只用于「事件自述与责任根冲突」的口径测试
OTHER = cs.did_from_name("r52-other", kind="user")
#: 协作者 DID（事件的 ``actor_did`` = 实际操作人）
COLLAB = "did:tracelens:user:r52collab"
AGENT = "agent-r52"
RECORD_ID = "act-r52-0001"
RECORD_ID_2 = "act-r52-0002"
GROUP = "grp-r52"
PLAIN_TOKEN = "shr_" + "a" * 48

#: 事件的固定形状（docs/07 §3.3 的七项 + 账本自动补的三项）：代行标注**不新增顶级键**
EVENT_KEYS = {"id", "actor_did", "action", "target", "at", "detail", "signature",
              "seq", "prev_hash", "entry_hash"}
#: 认定书顶级字段（docs/12 §5 冻结表 E 逐字一份）：同样不许因代行语义而多一个键
LETTER_KEYS = {"id", "kind", "version", "subject", "created_at", "mode", "period", "note",
               "responsibility", "agents", "delegation_chain", "actions", "verification",
               "records_root", "audit_head", "determination", "public_key", "fingerprint",
               "signature"}


def _record(record_id: str = RECORD_ID, **overrides) -> dict:
    base = {
        "schema_version": "1.1",
        "id": record_id,
        "timestamp": "2026-11-14T10:00:00Z",
        "agent_id": AGENT,
        "action_type": "approve",
        "tool_used": {"name": "intervene.approve", "version": "1.0"},
        "status": "succeeded",
        "risk_level": "low",
        "confidence": 0.91,
        "context": {"input": {"step": "s3"}},
        "result_summary": "协作者代队长批了第三步",
    }
    base.update(overrides)
    return base


def _nest(depth: int, leaf: object) -> dict:
    """造 ``depth`` 层嵌套字典（``{"L0": {"L1": … {leaf}}}``），用来试递归深度封顶。"""
    node: dict = {"leaf": leaf}
    for index in range(depth):
        node = {f"L{index}": node}
    return node


def _legacy_scrub(detail: Mapping) -> dict:
    """R5-2 **改前**的打码算法（逐字重写一遍当参照物）：只递归字典、不递归列表。

    用它钉住"改前能对的东西改后一个字都不能变"：扁平 detail、纯嵌套字典 detail、
    豁免键的 detail 三种形状，新旧算法必须给出逐字相同的结果。
    """
    out: dict = {}
    for key, value in detail.items():
        name = str(key)
        lowered = name.lower()
        if any(word in lowered for word in sh._SENSITIVE_DETAIL_KEYS):
            out[name] = "<redacted>"
        elif isinstance(value, Mapping):
            out[name] = _legacy_scrub(value)
        else:
            out[name] = value
    return out


# --------------------------------------------------------------------------- #
# 一、事件层：actor 与 on_behalf_of 是两个字段，不是一个被替换成另一个
# --------------------------------------------------------------------------- #

class EventLayerOnBehalfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("on_behalf_events")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.service = sh.ShareService(self.paths)

    def test_annotated_event_carries_actor_and_principal_as_two_fields(self) -> None:
        entry = self.service.record_event(
            actor=COLLAB, action="intervene.approve", target=RECORD_ID,
            detail={"group_id": GROUP, "permission": "approve"}, on_behalf_of=OWNER)
        self.assertEqual(entry["actor_did"], COLLAB, "actor 必须记实际操作人（协作者）")
        self.assertEqual(entry["detail"][sh.ON_BEHALF_KEY], OWNER, "替谁做的必须写出来")
        self.assertEqual(entry["detail"]["group_id"], GROUP)
        self.assertIsNone(entry["signature"], "本模块无密钥：代行标注不是签名，也不伪造签名")
        self.assertEqual(set(entry), EVENT_KEYS, "代行标注不新增事件顶级键（只进 detail）")

    def test_event_shape_is_word_for_word_unchanged_without_the_annotation(self) -> None:
        detail = {"group_id": GROUP, "permission": "approve"}
        entry = self.service.record_event(actor=OWNER, action="share.accept",
                                          target="inv_probe", detail=detail)
        self.assertEqual(set(entry), EVENT_KEYS)
        self.assertEqual(entry["detail"], detail, "普通协作操作的 detail 逐字不变")
        text = json.dumps(entry, ensure_ascii=False)
        self.assertNotIn("behalf", text, "缺省不写：连键名都不许出现（既有事件形状不漂移）")

    def test_annotation_and_scrubbing_coexist_in_the_same_detail(self) -> None:
        entry = self.service.record_event(
            actor=COLLAB, action="share.invite", target="inv_probe",
            detail={"token": PLAIN_TOKEN,
                    "urls": ["http://x/share.html#token=shr_secrettoken"],
                    "record_id": RECORD_ID},
            on_behalf_of=OWNER)
        detail = entry["detail"]
        self.assertEqual(detail["token"], "<redacted>")
        self.assertEqual(detail["urls"], "<redacted>", "键名含 url → 整个列表值打码")
        self.assertEqual(detail["record_id"], RECORD_ID, "审计要用的键不能顺手被打码")
        self.assertEqual(detail[sh.ON_BEHALF_KEY], OWNER, "代行标注自己不是打码目标")
        text = self.paths.collab_events.read_text(encoding="utf-8")
        self.assertNotIn(PLAIN_TOKEN, text, "打码后账本里仍有明文 token")
        self.assertNotIn("shr_secrettoken", text, "列表里的明文 token 泄进账本了")

    def test_the_on_behalf_key_is_never_a_scrub_target(self) -> None:
        self.assertFalse(sh._is_sensitive_key(sh.ON_BEHALF_KEY))
        entry = self.service.record_event(
            actor=COLLAB, action="share.debug", target="t",
            detail={"meta": {sh.ON_BEHALF_KEY: OWNER}})
        self.assertEqual(entry["detail"]["meta"][sh.ON_BEHALF_KEY], OWNER,
                         "嵌套位置上的 on_behalf_of 也不许被打码")

    def test_param_wins_over_a_hand_written_detail_key(self) -> None:
        entry = self.service.record_event(
            actor=COLLAB, action="intervene.approve", target=RECORD_ID,
            detail={sh.ON_BEHALF_KEY: OTHER}, on_behalf_of=OWNER)
        self.assertEqual(entry["detail"][sh.ON_BEHALF_KEY], OWNER,
                         "口径只有一个来源：参数入口（带校验、带长度封顶）")

    def test_empty_annotation_is_rejected_not_silently_dropped(self) -> None:
        for bad in ("", "   "):
            with self.assertRaises(sh.ShareError, msg=f"空串 {bad!r} 必须报出来") as ctx:
                self.service.record_event(actor=COLLAB, action="a", target="t",
                                          on_behalf_of=bad)
            self.assertEqual(ctx.exception.field, "on_behalf_of")
        self.assertEqual(self.service.verify_events()["count"], 0, "拒掉的不许留下半条事件")

    def test_non_string_annotation_is_rejected(self) -> None:
        for bad in (1, 3.5, True, ["did:x"], {"did": "x"}, object()):
            with self.assertRaises(sh.ShareError, msg=f"非字符串 {bad!r} 必须报出来"):
                self.service.record_event(actor=COLLAB, action="a", target="t",
                                          on_behalf_of=bad)
        self.assertEqual(self.service.verify_events()["count"], 0)

    def test_self_delegation_annotation_is_rejected(self) -> None:
        with self.assertRaises(sh.ShareError) as ctx:
            self.service.record_event(actor=COLLAB, action="a", target="t",
                                      on_behalf_of=COLLAB)
        self.assertEqual(ctx.exception.field, "on_behalf_of")
        self.assertEqual(self.service.verify_events()["count"], 0)

    def test_overlong_annotation_is_rejected(self) -> None:
        long_did = "did:tracelens:user:" + "z" * sh.ON_BEHALF_MAX_LEN
        with self.assertRaises(sh.ShareError) as ctx:
            self.service.record_event(actor=COLLAB, action="a", target="t",
                                      on_behalf_of=long_did)
        self.assertEqual(ctx.exception.field, "on_behalf_of")

    def test_chain_stays_true_and_history_is_append_only(self) -> None:
        first = self.service.record_event(actor=COLLAB, action="intervene.approve",
                                          target=RECORD_ID, on_behalf_of=OWNER)
        before_text = self.paths.collab_events.read_text(encoding="utf-8")
        self.service.record_event(actor=OWNER, action="share.accept", target="inv_2")
        second = self.service.record_event(actor=COLLAB, action="intervene.rollback",
                                           target=RECORD_ID_2, on_behalf_of=OWNER)
        report = self.service.verify_events()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["count"], 3)
        after_text = self.paths.collab_events.read_text(encoding="utf-8")
        self.assertTrue(after_text.startswith(before_text), "既有事件被改写了（账本必须只追加）")
        ids = [item["id"] for item in self.service.events(limit=50)]
        self.assertEqual(ids, [first["id"], ids[1], second["id"]], "顺序 = 追加顺序")


# --------------------------------------------------------------------------- #
# 二、_scrub 递归定档（PM-VERDICT-R5-1 §3.5 遗留 5 已批 L1）
# --------------------------------------------------------------------------- #

class ScrubRecursionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("on_behalf_scrub")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.service = sh.ShareService(self.paths)

    def _detail(self, detail: dict) -> dict:
        return self.service.record_event(actor=COLLAB, action="share.debug",
                                         target="t", detail=detail)["detail"]

    def test_sensitive_keys_inside_list_elements_are_scrubbed(self) -> None:
        got = self._detail({"payload": [{"token": PLAIN_TOKEN, "note": "ok"},
                                        {"nested": {"salt": "s", "keep": 1}}]})
        self.assertEqual(got["payload"][0]["token"], "<redacted>", "列表里的字典也要下钻")
        self.assertEqual(got["payload"][0]["note"], "ok", "非敏感值不许顺手裁掉")
        self.assertEqual(got["payload"][1]["nested"]["salt"], "<redacted>")
        self.assertEqual(got["payload"][1]["nested"]["keep"], 1)

    def test_list_valued_sensitive_key_redacts_the_whole_value(self) -> None:
        got = self._detail({"urls": [PLAIN_TOKEN, "x"], "link_list": [PLAIN_TOKEN]})
        self.assertEqual(got["urls"], "<redacted>")
        self.assertEqual(got["link_list"], "<redacted>")
        text = self.paths.collab_events.read_text(encoding="utf-8")
        self.assertNotIn(PLAIN_TOKEN, text)

    def test_deeply_nested_token_never_survives(self) -> None:
        detail = _nest(5, {"token": PLAIN_TOKEN})
        got = self._detail(detail)
        self.assertNotIn(PLAIN_TOKEN, json.dumps(got), "五层嵌套里的 token 泄了")
        self.assertNotIn(PLAIN_TOKEN, self.paths.collab_events.read_text(encoding="utf-8"))

    def test_depth_is_capped_and_the_overflow_is_redacted_not_traversed(self) -> None:
        detail = _nest(12, {"token": PLAIN_TOKEN})
        got = self._detail(detail)
        text = json.dumps(got, ensure_ascii=False)
        self.assertNotIn(PLAIN_TOKEN, text, "深嵌套里的明文必须打码（账本撤不回）")
        self.assertIn(sh._SCRUB_DEPTH_MARK, text, "超深子树必须留下看得见的打码标记")

        def depth(node: object, at: int = 0) -> int:
            if isinstance(node, Mapping):
                return max([depth(value, at + 1) for value in node.values()] or [at])
            if isinstance(node, (list, tuple)):
                return max([depth(item, at + 1) for item in node] or [at])
            return at

        self.assertLessEqual(depth(got), sh._SCRUB_MAX_DEPTH + 1,
                             "输出结构必须被封顶（防深嵌套 DoS / 深递归）")

    def test_exempt_names_survive_and_the_exemption_is_inert_today(self) -> None:
        for name in sorted(sh._SENSITIVE_DETAIL_EXEMPT):
            self.assertFalse(any(word in name.lower() for word in sh._SENSITIVE_DETAIL_KEYS),
                             f"{name} 今天并不命中敏感词 → 豁免清单当前是惰性的（零行为变化）")
        got = self._detail({"invite_id": "inv_1",
                            "deep": {"inviter_did": COLLAB, "invitee_did": OWNER}})
        self.assertEqual(got["invite_id"], "inv_1")
        self.assertEqual(got["deep"]["inviter_did"], COLLAB)
        self.assertEqual(got["deep"]["invitee_did"], OWNER,
                         "审计要用的邀请字段被打码 = 责任链查不下去")

    def test_the_exemption_is_exact_names_not_a_substring_rule(self) -> None:
        got = self._detail({"invite_id": "inv_1", "invite_id_token": PLAIN_TOKEN})
        self.assertEqual(got["invite_id"], "inv_1")
        self.assertEqual(got["invite_id_token"], "<redacted>",
                         "豁免只认精确键名：名字里带 token 的一律打码，不许被 invite 豁免顺放")

    def test_non_sensitive_nested_values_are_untouched(self) -> None:
        detail = {"keep": {"a": [1, 2, {"b": "c"}]}, "list": ["x", {"y": "z"}]}
        got = self._detail(detail)
        self.assertEqual(got["keep"], {"a": [1, 2, {"b": "c"}]})
        self.assertEqual(got["list"], ["x", {"y": "z"}])

    def test_flat_and_dict_only_details_match_the_pre_change_algorithm(self) -> None:
        cases = [
            {"group_id": GROUP, "permission": "view"},
            {"token": PLAIN_TOKEN, "keep": "ok"},
            {"nested": {"salt": "s", "group_id": GROUP}, "link": "x"},
            {"invite_id": "inv_1", "deep": {"invitee_did": OWNER}},
            {},
        ]
        for case in cases:
            self.assertEqual(sh._scrub(case), _legacy_scrub(case),
                             f"改前能对的东西改后变了：{case}")

    def test_tuple_values_keep_their_type_in_memory(self) -> None:
        got = self._detail({"pair": (1, 2)})
        self.assertIsInstance(got["pair"], tuple,
                              "列表/元组各返回同类型：不许把元组悄悄变成列表")


# --------------------------------------------------------------------------- #
# 三、认定书：只往有标注的行上添一句人话，其余一字不变
# --------------------------------------------------------------------------- #

class LetterRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("on_behalf_letter")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.sec = Secretariat(self.paths)
        self.shares = sh.ShareService(self.paths)
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        self.signed = self.sec.sign_record(_record())

    def _letter(self, events=None, **kwargs) -> dict:
        return self.sec.build_letter(subject="代行责任认定", records=[self.signed],
                                     audit_head="sha256:head", collab_events=events,
                                     **kwargs)

    def _events(self) -> list[dict]:
        self.shares.record_event(actor=COLLAB, action="intervene.approve",
                                 target=RECORD_ID, detail={"group_id": GROUP},
                                 on_behalf_of=OWNER)
        return self.shares.events(limit=50)

    def _verify_offline(self, letter: dict) -> dict:
        """离线复算：只吃**落盘文件**（审计方拿到的就是这份），不碰服务端状态。"""
        reloaded = json.loads(self.paths.letter_path(letter["id"]).read_text(encoding="utf-8"))
        return self.sec.verify_letter(reloaded)

    def test_annotated_row_gains_the_frozen_sentence(self) -> None:
        letter = self._letter(self._events())
        action = letter["actions"][0]
        self.assertEqual(action["on_behalf_actor"], COLLAB)
        self.assertEqual(action["on_behalf_of"], OWNER)
        self.assertEqual(
            action["on_behalf_note"],
            f"由 {COLLAB} 代行 {OWNER} 行使权限，最终责任归 {OWNER}（human_owner）",
            "措辞逐字取自 PM-PLAN §7.1，不许自创说法")
        report = self._verify_offline(letter)
        self.assertTrue(report["ok"], report)

    def test_letter_without_annotation_is_word_for_word_unchanged(self) -> None:
        unrelated = [{"id": "evt_x", "actor_did": COLLAB, "action": "share.accept",
                      "target": "inv_other", "detail": {"group_id": GROUP}}]
        baseline = self._letter(None)
        for label, events in (("None", None), ("空列表", []), ("无关事件", unrelated)):
            got = self._letter(events)
            self.assertEqual(json.dumps(got["actions"], ensure_ascii=False),
                             json.dumps(baseline["actions"], ensure_ascii=False),
                             f"{label} 时动作条目应当逐字不变")
            self.assertEqual(got["records_root"], baseline["records_root"], f"{label} 时根哈希漂移")
            for entry in got["actions"]:
                self.assertNotIn("on_behalf_note", entry)

    def test_unannotated_rows_in_a_mixed_letter_are_unchanged(self) -> None:
        signed2 = self.sec.sign_record(_record(RECORD_ID_2, timestamp="2026-11-14T11:00:00Z"))
        records = [self.signed, signed2]
        baseline = self.sec.build_letter(subject="混合", records=records,
                                         audit_head="sha256:head")
        self.shares.record_event(actor=COLLAB, action="intervene.approve",
                                 target=RECORD_ID, on_behalf_of=OWNER)
        annotated = self.sec.build_letter(subject="混合", records=records,
                                          audit_head="sha256:head",
                                          collab_events=self.shares.events(limit=50))
        by_id = {entry["id"]: entry for entry in annotated["actions"]}
        for entry in baseline["actions"]:
            hit = by_id[entry["id"]]
            if entry["id"] == RECORD_ID:
                self.assertIn("on_behalf_note", hit)
                continue
            self.assertEqual(json.dumps(hit, ensure_ascii=False),
                             json.dumps(entry, ensure_ascii=False),
                             "无标注的行被连带改了（只许给有标注的行添话）")

    def test_signature_fields_human_owner_and_chain_are_untouched(self) -> None:
        baseline = self._letter(None)
        annotated = self._letter(self._events())
        base_entry, hit_entry = baseline["actions"][0], annotated["actions"][0]
        for field in sec_mod.SIGNATURE_FIELDS:
            self.assertEqual(hit_entry[field], base_entry[field],
                             f"签名域字段 {field} 被代行语义动了")
        self.assertEqual(set(hit_entry) - set(base_entry), set(sec_mod.ON_BEHALF_FIELDS),
                         "代行只追加声明的三个键，别的键一个不许多")
        self.assertEqual(annotated["delegation_chain"], baseline["delegation_chain"],
                         "delegation_chain 不改结构（PM-PLAN §7.1 冻结）")
        for link in annotated["delegation_chain"]:
            self.assertEqual(set(link), {"did", "source"})
        self.assertEqual(annotated["agents"], baseline["agents"],
                         "agents[].human_owner 是责任根，一个字都不动")
        self.assertNotIn("human_owner", hit_entry,
                         "动作条目不新增责任字段：最终责任归记录里的 human_owner")
        self.assertEqual(self.signed["human_owner"], OWNER, "记录的 human_owner 被改了")

    def test_records_are_not_rewritten_by_the_letter(self) -> None:
        snapshot = copy.deepcopy(self.signed)
        self._letter(self._events())
        self.assertEqual(self.signed, snapshot, "生成认定书不得就地改写记录（零历史改写）")

    def test_annotated_letter_tampering_is_still_detected(self) -> None:
        letter = self._letter(self._events())
        reloaded = json.loads(self.paths.letter_path(letter["id"]).read_text(encoding="utf-8"))
        reloaded["actions"][0]["on_behalf_note"] = "由 别人 代行 别人 行使权限"
        report = self.sec.verify_letter(reloaded)
        self.assertFalse(report["ok"], "代行句在认定书签名正文之外？那它就能被偷改")
        self.assertIn("SECRETARIAT_SIGNATURE_INVALID",
                      [problem["code"] for problem in report["problems"]])

    def test_conflicting_annotation_is_registered_and_responsibility_stays_with_owner(self) -> None:
        self.shares.record_event(actor=COLLAB, action="intervene.approve",
                                 target=RECORD_ID, detail={"group_id": GROUP},
                                 on_behalf_of=OTHER)
        letter = self._letter(self.shares.events(limit=50))
        note = letter["actions"][0]["on_behalf_note"]
        self.assertIn(OWNER, note, "最终责任必须写在 human_owner 上")
        self.assertNotIn(OTHER, note, "事件自述的真人不得顶掉记录里的责任根")
        hits = [problem for problem in letter["verification"]["problems"]
                if isinstance(problem.get("detail"), Mapping)
                and problem["detail"].get("scope") == sh.ON_BEHALF_KEY]
        self.assertTrue(hits, "标注与责任根冲突被静默吞掉了")
        self.assertEqual(hits[0]["detail"]["human_owner"], OWNER)
        self.assertEqual(hits[0]["detail"]["on_behalf_of"], OTHER)
        self.assertTrue(self._verify_offline(letter)["ok"])

    def test_event_without_actor_renders_nothing_but_registers_a_problem(self) -> None:
        forged = [{"id": "evt_no_actor", "actor_did": "", "action": "intervene.approve",
                   "target": RECORD_ID, "detail": {sh.ON_BEHALF_KEY: OWNER}}]
        letter = self._letter(forged)
        self.assertNotIn("on_behalf_note", letter["actions"][0],
                         "写不出主语就不许编一个名字进认定书")
        scopes = [problem.get("detail", {}).get("scope")
                  for problem in letter["verification"]["problems"]]
        self.assertIn(sh.ON_BEHALF_KEY, scopes)

    def test_matching_rules_target_record_id_and_action_id_only(self) -> None:
        cases = [
            ({"target": RECORD_ID, "detail": {}}, True, "target 就是记录 id"),
            ({"target": GROUP, "detail": {"record_id": RECORD_ID}}, True, "detail.record_id"),
            ({"target": GROUP, "detail": {"action_id": RECORD_ID}}, True, "detail.action_id"),
            ({"target": "inv_other", "detail": {"group_id": GROUP}}, False, "指向别的东西"),
            ({"target": "", "detail": {sh.ON_BEHALF_KEY: OWNER}}, False, "空标识不许匹配一切"),
        ]
        for payload, expect, label in cases:
            with self.subTest(label):
                event = {"id": "evt_case", "actor_did": COLLAB, "action": "intervene.approve",
                         "target": payload["target"],
                         "detail": dict(payload["detail"], **{sh.ON_BEHALF_KEY: OWNER})}
                letter = self._letter([event])
                entry = letter["actions"][0]
                self.assertEqual("on_behalf_note" in entry, expect, label)

    def test_last_annotation_wins_for_repeated_delegation(self) -> None:
        self.shares.record_event(actor=COLLAB, action="intervene.approve",
                                 target=RECORD_ID, on_behalf_of=OWNER)
        self.shares.record_event(actor=cs.did_from_name("r52-second", kind="user"),
                                 action="intervene.approve", target=RECORD_ID,
                                 on_behalf_of=OWNER)
        letter = self._letter(self.shares.events(limit=50))
        self.assertEqual(letter["actions"][0]["on_behalf_actor"],
                         cs.did_from_name("r52-second", kind="user"),
                         "账本升序：最近一次代行才是当下有效的事实")
        self.assertEqual(self.shares.verify_events()["count"], 2, "每一次代行都要留下（不覆盖）")

    def test_letter_top_level_shape_gains_no_new_key(self) -> None:
        self.assertEqual(set(self._letter(None)), LETTER_KEYS)
        self.assertEqual(set(self._letter(self._events())), LETTER_KEYS,
                         "代行语义不得新增认定书顶级字段（docs/12 §5 冻结表 E）")

    def test_public_helpers_are_pure_and_bounded(self) -> None:
        self.assertEqual(sec_mod.on_behalf_index(None), {})
        self.assertEqual(sec_mod.on_behalf_index([{"detail": "not-an-object"}]), {})
        self.assertEqual(sec_mod.on_behalf_index(
            [{"actor_did": COLLAB, "target": RECORD_ID,
              "detail": {sh.ON_BEHALF_KEY: 123}}]), {}, "非字符串标注整条跳过")
        note = sec_mod.on_behalf_note(actor=COLLAB, principal=OWNER)
        self.assertTrue(note.startswith("由 "))
        self.assertEqual(sec_mod.on_behalf_note(actor="x" * 999, principal="y" * 999)
                         .count("x"), sh.ON_BEHALF_MAX_LEN, "DID 裁到上限：正文进签名必须有界")


# --------------------------------------------------------------------------- #
# 四、权限闸门：代行标注不是权限（B5-2 归 R5-3，本轮只保证语义不越权）
# --------------------------------------------------------------------------- #

class PermissionGateUnchangedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("on_behalf_gate")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.service = sh.ShareService(self.paths)
        created = self.service.invite(group_id=GROUP, inviter_did=OWNER,
                                      permission="view", invitee_did=COLLAB)
        self.service.accept(token=created["token"], invitee_did=COLLAB)

    def test_the_annotation_never_widens_the_gate(self) -> None:
        before = self.service.can(COLLAB, "approve", GROUP)
        self.assertFalse(before["allowed"], "view 协作者本来就不能拍板（B5-2 的底座）")
        self.service.record_event(actor=COLLAB, action="intervene.approve",
                                  target=RECORD_ID, detail={"group_id": GROUP},
                                  on_behalf_of=OWNER)
        after = self.service.can(COLLAB, "approve", GROUP)
        self.assertEqual(before, after, "记一条代行事件就把权限放大了")
        self.assertFalse(self.service.can(COLLAB, "manage", GROUP)["allowed"])
        self.assertTrue(self.service.can(COLLAB, "view", GROUP)["allowed"])

    def test_the_owner_judgements_are_untouched_by_the_annotation(self) -> None:
        """队长侧的 ``can()`` 判定逐字不变：代行标注既不放大协作者权限，也不动别人的判定。"""
        owner_view = self.service.can(OWNER, "view", GROUP)
        collab_view = self.service.can(COLLAB, "view", GROUP)
        self.service.record_event(actor=COLLAB, action="intervene.approve",
                                  target=RECORD_ID, on_behalf_of=OWNER)
        self.assertEqual(self.service.can(OWNER, "view", GROUP), owner_view,
                         "记一条代行事件把队长的判定改了")
        self.assertEqual(self.service.can(COLLAB, "view", GROUP), collab_view,
                         "协作者已获得的 view 不许因为多了一条事件就变化")
        self.assertTrue(collab_view["allowed"])
        self.assertFalse(self.service.can(COLLAB, "manage", GROUP)["allowed"])


if __name__ == "__main__":  # 手工自检：python tests/test_on_behalf.py -v
    unittest.main(verbosity=2)
