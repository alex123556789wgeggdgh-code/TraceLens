"""R2-1 验收：置信度策略层 `agent_explainer/confidence.py` + 导入固化接线（REQ-002 地基）。

对应 `TASK-R2-1-confidence-policy.md` §4 的验收条款，逐组映射：

* **A 边界**（band/color 四档精确值与邻域）→ ``BandBoundaryTests``
* **B 归一**（normalize 非法输入：bool / 负数 / 越界 / 字符串）→ ``NormalizeTests``
* **C 来源**（from_logprobs 缺失即 None，禁止编造 logprob）→ ``FromLogprobsTests``
* **D 合成**（combine 权重合成、缺一路不补 0）→ ``CombineTests``
* **E 契约**（apply_to_record：<0.5 → requires_action + reason + 三条选项；入参零改写）
  → ``ApplyToRecordTests``
* **F 枚举与形态**（method 枚举逐字 = schema；source 键 ⊆ schema；不可判退回 default）
  → ``SourceShapeTests`` / ``PolicyPayloadTests``
* **G 展示纪律**（explain 双语各一句；不可判不得讲成肯定）→ ``ExplainTests``
* **H 导入固化端到端**（带 confidence → 落盘带 confidence + confidence_source 且
  ``verify_record`` 通过；不带 → null + default 形态且验签通过；低置信不翻 requires_action
  ——联动接线归 R2-2）→ ``ImportFixationTests``
* **I 铁律静态自证**（SIGNATURE_FIELDS 零改动、无运行期改判入口、零新依赖）
  → ``IronRuleStaticTests``

运行：

    cd "C:\\TraceLens\\agent-explainer"
    python -X utf8 -m unittest tests.test_confidence_policy -v
    python -m pytest tests/test_confidence_policy.py -q        # 有 pytest 时同口径

临时目录一律走 ``tests/scratch.py`` 的探测/退回约定（§1.4：不碰 ``tempfile`` 的标准临时目录）。
"""
from __future__ import annotations

import ast
import copy
import json
import math
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

from agent_explainer import confidence as cf  # noqa: E402
from agent_explainer import ingest as ingest_mod  # noqa: E402
from agent_explainer import secretariat as sec_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import store as st  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((REPO / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SOURCE_SCHEMA = SCHEMA["properties"]["confidence_source"]
SAMPLE = json.loads((REPO / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"][0]

#: docs/07 §4.6 要求的函数名（逐名实现的核对清单）
CONTRACT_NAMES = ("band", "color", "normalize", "from_logprobs", "combine",
                  "apply_to_record", "policy", "explain")
#: 铁律 1：模块不许提供任何运行期改判入口
RUNTIME_OVERRIDE_TOKENS = ("set_confidence", "update_confidence", "override", "patch",
                           "mutate", "write_record", "change_confidence", "revise")


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def source_errors(source) -> list[str]:
    """对 ``confidence_source`` 做 schema 校验。

    不能拿子 schema 单独校验：它里面有 ``$ref": "#/typelets/nullableTimestamp"``，
    脱离根 schema 解析不了（``MiniValidator`` 直接抛异常）。所以把形态挂进一条合法记录里验。
    """
    probe = base_record("act-r21-source-probe", confidence=None, confidence_source=source)
    return schema_errors(probe)


def base_record(aid: str = "act-r21-base", **over) -> dict:
    """演示种子的一条：字段齐全、schema v1.1 合法、未签名。"""
    rec = copy.deepcopy(SAMPLE)
    rec["id"] = aid
    rec["parent_action_id"] = None
    rec.pop("received_at", None)
    rec.update(over)
    return rec


def running_record(aid: str = "act-r21-running", **over) -> dict:
    """进行中的记录：可被低置信联动转成"等你拍板"（apply_to_record 的用例夹具）。"""
    return base_record(aid, status="running", requires_action=False,
                       requires_action_reason=None, intervention_options=[],
                       user_intervention=None, **over)


class DictStore:
    """最小记录库：与 ``InterveneService.store`` 同接口（get/put），用于纯逻辑测试。"""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def get(self, key: str):
        return self.records.get(str(key))

    def put(self, record) -> None:
        self.records[str(record["id"])] = record

    def all(self):
        return list(self.records.values())


def service(name: str, **over) -> sv.InterveneService:
    """服务级装配：临时目录走 scratch，落盘 + 审计 + 入库签名都开（与产品默认一致）。"""
    folder = scratch_dir(name)
    svc = sv.InterveneService(records_store_path=folder / st.RECORD_FILE_NAME,
                              audit_log_path=folder / "audit.jsonl",
                              sign_on_write=True, **over)
    svc._r21_room = folder          # type: ignore[attr-defined]
    return svc


def import_payload(svc: sv.InterveneService, records, **kw) -> dict:
    kw.setdefault("request_id", "req-r21")
    return svc.import_traces({"payload": records}, **kw)


# --------------------------------------------------------------------------- #
# A. band / color 四档边界
# --------------------------------------------------------------------------- #

class BandBoundaryTests(unittest.TestCase):
    def test_band_exact_thresholds_are_inclusive_downward(self) -> None:
        for value, want in ((1.0, "green"), (0.9, "green"), (0.8999, "light_green"),
                            (0.7, "light_green"), (0.6999, "yellow"), (0.5, "yellow"),
                            (0.4999, "red"), (0.0, "red")):
            self.assertEqual(want, cf.band(value), f"band({value}) 必须是 {want}")

    def test_band_none_out_of_range_and_non_numeric_are_unknown(self) -> None:
        for value in (None, 1.5, -0.1, float("nan"), float("inf"), True, False,
                      "abc", "", {}, [], object()):
            self.assertEqual("unknown", cf.band(value), f"band({value!r}) 必须 unknown")

    def test_color_tokens_match_bands(self) -> None:
        self.assertEqual("ok", cf.color(0.95))
        self.assertEqual("ok_soft", cf.color(0.7))
        self.assertEqual("progress", cf.color(0.5))
        self.assertEqual("danger", cf.color(0.4999))
        self.assertEqual("neutral", cf.color(None))
        self.assertEqual("neutral", cf.color(2.0))

    def test_color_is_always_the_token_of_band(self) -> None:
        for value in (0.0, 0.3, 0.5, 0.6999, 0.7, 0.8999, 0.9, 1.0, None, "0.42"):
            self.assertEqual(cf.COLOR_BY_BAND[cf.band(value)], cf.color(value), str(value))

    def test_thresholds_are_verbatim_from_the_contract(self) -> None:
        self.assertEqual({"green": 0.9, "light_green": 0.7, "yellow": 0.5, "red": 0.0},
                         cf.THRESHOLDS)
        self.assertEqual("1.0", cf.CONFIDENCE_VERSION)
        self.assertEqual(1.0, cf.DEFAULT_CONFIDENCE)
        self.assertEqual(0.5, cf.ACTION_TRIGGER)


# --------------------------------------------------------------------------- #
# B. normalize
# --------------------------------------------------------------------------- #

class NormalizeTests(unittest.TestCase):
    def test_illegal_inputs_return_none_not_a_fabricated_number(self) -> None:
        for value in (True, False, -0.2, 1.5, "abc", "", None, {}, [], object(),
                      float("nan"), float("inf")):
            self.assertIsNone(cf.normalize(value), f"normalize({value!r}) 必须是 None")

    def test_numbers_and_numeric_strings_are_accepted_and_rounded(self) -> None:
        self.assertEqual(0.82, cf.normalize(0.82))
        self.assertEqual(0.82, cf.normalize("0.82"))          # 与 trace_adapter._confidence 同口径
        self.assertEqual(0.0, cf.normalize(0))
        self.assertEqual(1.0, cf.normalize(1))
        self.assertEqual(0.1235, cf.normalize(0.123456))      # PRECISION=4

    def test_normalize_is_idempotent_so_the_signed_value_does_not_drift(self) -> None:
        for value in (0.82, 0.123456, 1, 0, "0.7"):
            once = cf.normalize(value)
            self.assertEqual(once, cf.normalize(once), str(value))

    def test_no_source_never_falls_back_to_default_confidence(self) -> None:
        """§2.4：来源什么都没给 → ``None``（schema「缺失 = null」），不是 1.0。"""
        self.assertIsNone(cf.normalize(None))
        self.assertIsNone(cf.normalize(None, source="template"))
        self.assertIsNone(cf.normalize(None, source="logprobs"))

    def test_unknown_source_label_does_not_change_the_verdict(self) -> None:
        self.assertEqual(0.5, cf.normalize(0.5, source="human"))
        self.assertEqual(0.5, cf.normalize("0.5", source=""))
        self.assertIsNone(cf.normalize("human", source="default"))


# --------------------------------------------------------------------------- #
# C. from_logprobs
# --------------------------------------------------------------------------- #

class FromLogprobsTests(unittest.TestCase):
    def test_missing_logprobs_is_none_and_never_a_made_up_logprob(self) -> None:
        for value in (None, [], {}, {"logprobs": []}, {"logprobs": None}, [None, "x", True]):
            out = cf.from_logprobs(value)
            self.assertIsNone(out["confidence"], f"from_logprobs({value!r}) 必须 None")
            self.assertEqual("default", out["source"]["method"])
            self.assertIsNone(out["source"]["logprob"], "禁止编造 logprob")
            self.assertEqual([], source_errors(out["source"]), out["source"])

    def test_sequence_is_the_mean_of_exponentials(self) -> None:
        out = cf.from_logprobs([-0.1, -0.2, -0.3], tokens=["a", "b", "c"])
        want = round(sum(math.exp(v) for v in (-0.1, -0.2, -0.3)) / 3, 4)
        self.assertEqual(want, out["confidence"])
        self.assertEqual("logprobs", out["source"]["method"])
        self.assertEqual(want, out["source"]["logprob"])
        self.assertEqual(3, out["source"]["samples"])
        self.assertEqual([], source_errors(out["source"]))

    def test_mapping_openai_shapes_and_single_number(self) -> None:
        mapped = cf.from_logprobs({"logprobs": [{"logprob": -0.2}], "tokens": ["x"]})
        self.assertEqual(round(math.exp(-0.2), 4), mapped["confidence"])
        self.assertEqual(1, mapped["source"]["samples"])
        folded = cf.from_logprobs({"confidence": 0.83})
        self.assertEqual(0.83, folded["confidence"])
        single = cf.from_logprobs(-0.5)                       # 单个 logprob → exp
        self.assertEqual(round(math.exp(-0.5), 4), single["confidence"])
        self.assertEqual(0.83, cf.from_logprobs(0.83)["confidence"])

    def test_impossible_values_are_dropped_not_clamped(self) -> None:
        mixed = cf.from_logprobs([1.5, -0.2])                 # 1.5 不可能是 logprob/概率
        self.assertEqual(round(math.exp(-0.2), 4), mixed["confidence"])
        self.assertEqual(1, mixed["source"]["samples"])
        self.assertEqual(1.0, cf.from_logprobs([1.5, 2.0, -0.0])["confidence"],
                         "-0.0 是合法 logprob（exp(-0.0)=1.0），1.5/2.0 丢掉")


# --------------------------------------------------------------------------- #
# D. combine
# --------------------------------------------------------------------------- #

class CombineTests(unittest.TestCase):
    def test_both_sources_are_weighted_and_weights_are_recorded(self) -> None:
        out = cf.combine(0.9, 0.5)
        self.assertEqual(0.78, out["confidence"])             # 0.9*0.7 + 0.5*0.3
        self.assertEqual("hybrid", out["source"]["method"])
        self.assertEqual([0.7, 0.3], out["source"]["weights"])
        self.assertEqual(0.9, out["source"]["logprob"])
        self.assertEqual(0.5, out["source"]["tool_success_rate"])
        self.assertEqual([], source_errors(out["source"]))

    def test_missing_side_is_null_not_zero(self) -> None:
        only_logprob = cf.combine(0.9, None)
        self.assertEqual(0.9, only_logprob["confidence"])
        self.assertEqual("logprobs", only_logprob["source"]["method"])
        self.assertIsNone(only_logprob["source"]["tool_success_rate"])
        only_rate = cf.combine(None, 0.5)
        self.assertEqual(0.5, only_rate["confidence"])
        self.assertEqual("tool_success_rate", only_rate["source"]["method"])
        self.assertIsNone(only_rate["source"]["logprob"])

    def test_no_source_at_all_is_default(self) -> None:
        out = cf.combine(None, None)
        self.assertIsNone(out["confidence"])
        self.assertEqual("default", out["source"]["method"])

    def test_illegal_weights_fall_back_and_weights_are_normalized(self) -> None:
        for bad in ((0, 0), (-1, 2), "abc", (0.7,), (0.7, 0.3, 0.1), (True, 0.3), None):
            out = cf.combine(0.9, 0.5, weights=bad)           # type: ignore[arg-type]
            self.assertEqual(0.78, out["confidence"], f"非法权重 {bad!r} 必须退回 (0.7,0.3)")
            self.assertEqual([0.7, 0.3], out["source"]["weights"])
        even = cf.combine(0.9, 0.5, weights=(2, 2))           # 合法：归一化成 (0.5,0.5)
        self.assertEqual(0.7, even["confidence"])
        self.assertEqual([0.5, 0.5], even["source"]["weights"])


# --------------------------------------------------------------------------- #
# E. apply_to_record（契约函数本体）
# --------------------------------------------------------------------------- #

class ApplyToRecordTests(unittest.TestCase):
    def test_low_confidence_demands_a_human_decision(self) -> None:
        """契约：``< 0.5`` → ``requires_action=True`` + 理由 + 三条决定选项。"""
        computed = cf.from_logprobs([-1.2, -1.0, -0.9])       # ≈0.33
        out = cf.apply_to_record(running_record("act-r21-low"), computed=computed)
        self.assertTrue(out["requires_action"])
        self.assertEqual("pending_confirmation", out["status"], "schema C1：要人拍板只能是这两个状态")
        self.assertEqual(computed["confidence"], out["confidence"])
        self.assertGreaterEqual(len(str(out["requires_action_reason"])), 4)
        kinds = {str(o.get("kind")) for o in out["intervention_options"]}
        self.assertLessEqual({"approve", "modify", "reject"}, kinds)
        self.assertTrue(out["policy"]["requires_human_confirmation"])
        self.assertIsNotNone(out["uncertainty"], "schema C7：低置信必须说明哪里不确定")
        self.assertEqual("logprobs", out["confidence_source"]["method"])
        self.assertEqual([], schema_errors(out), "翻完必须是 schema 合法记录")

    def test_confidence_exactly_half_a_trigger_boundary(self) -> None:
        out = cf.apply_to_record(running_record("act-r21-050", confidence=0.5))
        self.assertFalse(out["requires_action"], "0.5 = yellow 下界，不触发（<0.5 才触发）")
        self.assertIsNotNone(out["uncertainty"], "≤0.6 必须带 uncertainty（C7）")
        self.assertEqual("yellow", cf.band(out["confidence"]))

    def test_very_low_confidence_in_a_finished_step_is_not_flipped(self) -> None:
        """已结束的步骤"要人拍板"是假的可点卡片：不翻 requires_action，只如实标不确定。"""
        out = cf.apply_to_record(base_record("act-r21-done", status="succeeded",
                                             requires_action=False,
                                             intervention_options=[],
                                             confidence=0.2))
        self.assertFalse(out["requires_action"])
        self.assertEqual("succeeded", out["status"])
        self.assertIsNotNone(out["uncertainty"])
        self.assertEqual([], schema_errors(out))

    def test_record_with_an_existing_decision_is_not_flipped(self) -> None:
        decided = base_record("act-r21-decided", status="approved", confidence=0.2,
                              requires_action=False, intervention_options=[],
                              user_intervention={"decision": "approve", "option_id": None,
                                                 "decided_by": "local-user",
                                                 "decided_by_role": "local",
                                                 "decided_at": "2026-11-14T08:00:00Z",
                                                 "modified_fields": [], "recorded": True})
        out = cf.apply_to_record(decided)
        self.assertFalse(out["requires_action"], "C1：已有决定的记录不得再变成「要人拍板」")
        self.assertEqual([], schema_errors(out))

    def test_input_record_is_never_mutated(self) -> None:
        """入参零改写（铁律 2）：改的是深拷贝出来的新记录。"""
        raw = running_record("act-r21-immutable", confidence=0.1)
        snapshot = copy.deepcopy(raw)
        cf.apply_to_record(raw)
        self.assertEqual(snapshot, raw, "apply_to_record 不许改入参")

    def test_existing_compliant_source_is_kept_verbatim(self) -> None:
        given = {"method": "logprobs", "logprob": 0.82, "samples": 12}
        out = cf.apply_to_record(running_record("act-r21-keep", confidence=0.82,
                                                confidence_source=given))
        self.assertEqual(given, out["confidence_source"], "来源给过的形态逐字保留（不追加键）")

    def test_given_number_without_source_uses_the_ruled_mapping(self) -> None:
        """§2.3 映射：规则给定值 → ``template``；带工具成功率 → ``tool_success_rate``。"""
        plain = cf.apply_to_record(running_record("act-r21-num", confidence=0.91))
        self.assertEqual("template", plain["confidence_source"]["method"])
        self.assertIsNone(plain["confidence_source"]["logprob"], "没给 logprobs 就是 None")
        with_rate = cf.apply_to_record(running_record("act-r21-rate", confidence=0.91,
                                                      tool_success_rate=0.6))
        self.assertEqual("tool_success_rate", with_rate["confidence_source"]["method"])
        self.assertEqual(0.6, with_rate["confidence_source"]["tool_success_rate"])

    def test_illegal_confidence_is_dropped_to_null_with_default_shape(self) -> None:
        for bad in (1.5, -0.1, "human", True):
            out = cf.apply_to_record(running_record("act-r21-bad", confidence=bad))
            self.assertIsNone(out["confidence"], f"非法置信度 {bad!r} → null，不夹紧不编造")
            self.assertEqual("default", out["confidence_source"]["method"])
            self.assertFalse(out["requires_action"], "判不出置信度不能凭空要人拍板")

    def test_computed_source_wins_over_record_fields(self) -> None:
        computed = cf.combine(0.8, 0.4)
        out = cf.apply_to_record(running_record("act-r21-comp", confidence=0.99,
                                                confidence_source={"method": "template"}),
                                 computed=computed)
        self.assertEqual(0.68, out["confidence"])
        self.assertEqual("hybrid", out["confidence_source"]["method"])
        self.assertEqual([], schema_errors(out))

    def test_non_mapping_record_is_rejected(self) -> None:
        for bad in (None, [], "record"):
            with self.assertRaises(TypeError):
                cf.apply_to_record(bad)
            with self.assertRaises(TypeError):
                cf.fixate_for_import(bad)


# --------------------------------------------------------------------------- #
# F. method 枚举与 confidence_source 形态（对 schema 校验）
# --------------------------------------------------------------------------- #

class SourceShapeTests(unittest.TestCase):
    def test_methods_and_keys_are_verbatim_from_the_schema(self) -> None:
        schema_methods = tuple(SOURCE_SCHEMA["properties"]["method"]["enum"])
        self.assertEqual(schema_methods, cf.METHODS, "禁止发明新枚举值")
        self.assertEqual(set(SOURCE_SCHEMA["properties"]), set(cf.SOURCE_KEYS))
        self.assertFalse(SOURCE_SCHEMA["additionalProperties"])

    def test_every_generated_source_passes_the_schema(self) -> None:
        generated = [cf.from_logprobs(None)["source"],
                     cf.from_logprobs([-0.1])["source"],
                     cf.combine(0.9, 0.5)["source"],
                     cf.combine(0.9, None)["source"],
                     cf.combine(None, 0.5)["source"],
                     cf.combine(0.9, 0.5, weights=(1, 1))["source"],
                     cf.apply_to_record(running_record("act-r21-shape"))["confidence_source"],
                     cf.fixate_for_import(running_record("act-r21-shape2",
                                                         confidence=None))["confidence_source"]]
        for source in generated:
            self.assertEqual([], source_errors(source), source)

    def test_default_shape_is_the_ruled_four_keys(self) -> None:
        source = cf.fixate_for_import(running_record("act-r21-dflt", confidence=None))
        self.assertEqual({"method": "default", "logprob": None,
                          "tool_success_rate": None, "samples": None},
                         source["confidence_source"])

    def test_unrecognised_method_falls_back_to_default(self) -> None:
        """来源自带 method="human"（schema 外）：判为不可判，退回 default，不替它背书。"""
        out = cf.fixate_for_import(running_record("act-r21-human", confidence=0.9,
                                                  confidence_source={"method": "human"}))
        self.assertEqual("default", out["confidence_source"]["method"])
        self.assertEqual([], source_errors(out["confidence_source"]))

    def test_is_valid_source_gate(self) -> None:
        self.assertTrue(cf.is_valid_source({"method": "logprobs", "logprob": 0.82, "samples": 12}))
        self.assertTrue(cf.is_valid_source({"method": "default", "logprob": None,
                                            "tool_success_rate": None, "samples": None}))
        for bad in ({"method": "human"}, {"method": "logprobs", "logprob": 1.4},
                    {"method": "logprobs", "extra": 1}, {"method": "logprobs", "samples": -3},
                    {"method": "logprobs", "samples": True}, {"logprob": 0.5},
                    {"method": "hybrid", "weights": [0.1, 0.1, 0.1, 0.1, 0.1]},
                    {"method": "template", "computed_at": "not-a-time"}, None, "x"):
            self.assertFalse(cf.is_valid_source(bad), str(bad))


class PolicyPayloadTests(unittest.TestCase):
    def test_policy_is_a_plain_json_dict_with_the_contract_values(self) -> None:
        body = cf.policy()
        json.dumps(body, ensure_ascii=False)                  # 必须可序列化
        self.assertEqual(True, body["ok"])
        self.assertEqual(cf.THRESHOLDS, body["thresholds"])
        self.assertEqual(list(cf.METHODS), body["methods"])
        self.assertEqual(0.5, body["action_trigger"])
        self.assertEqual(1.0, body["default_confidence"])
        self.assertEqual(4, len(body["bands"]))
        for item in body["bands"]:
            self.assertIn(item["color"], ("ok", "ok_soft", "progress", "danger"))
            self.assertTrue(item["explain_zh"] and item["explain_en"])

    def test_policy_declares_the_wiring_honestly(self) -> None:
        """策略负载必须如实申报接线现状：联动已接（R2-2），端点与运行期改判入口仍未接。"""
        wiring = cf.policy()["wiring"]
        self.assertIsNone(wiring["http_endpoint"], "端点接线不在 R2-1/R2-2，负载不得谎称已接")
        self.assertIsNone(wiring["runtime_override"], "任何轮次都不提供运行期改判入口")
        self.assertIn("签名之前", wiring["fixation_point"])
        linkage = wiring["requires_action_linkage"]
        self.assertIsInstance(linkage, dict, "R2-2 已接线：联动状态要申报成事实，不再写 None")
        self.assertTrue(linkage["wired"])
        self.assertEqual(cf.ACTION_TRIGGER, linkage["trigger_below"])
        self.assertEqual("fixate_for_import（导入固化 = 固化 + 联动，同一个签名前入口）",
                         linkage["entry"])
        self.assertFalse(linkage["backfill"], "历史行不回填（铁律 4：历史行不是现状断言）")
        self.assertEqual(list(cf.DECIDABLE_STATUSES), linkage["status_after"])


# --------------------------------------------------------------------------- #
# G. explain 展示纪律
# --------------------------------------------------------------------------- #

class ExplainTests(unittest.TestCase):
    def test_bilingual_one_sentence_each(self) -> None:
        for value in (0.95, 0.8, 0.6, 0.3, None):
            zh = cf.explain(value, lang="zh")
            en = cf.explain(value, lang="en")
            self.assertTrue(zh.strip() and en.strip())
            self.assertEqual(0, zh.count("\n"), "zh 一句话")
            self.assertEqual(0, en.count("\n"), "en 一句话")
            self.assertNotEqual(zh, en)

    def test_unknown_never_phrases_a_number_as_certain(self) -> None:
        zh = cf.explain(None, lang="zh")
        en = cf.explain(None, lang="en")
        self.assertIn("未知", zh)
        self.assertIn("不得把数字讲成肯定", zh)
        self.assertIn("unknown", en.lower())
        self.assertIn("certain", en.lower())

    def test_low_band_says_a_human_must_decide(self) -> None:
        self.assertIn("人来拍板", cf.explain(0.3, lang="zh"))
        self.assertIn("human", cf.explain(0.3, lang="en").lower())

    def test_unknown_lang_falls_back_to_zh(self) -> None:
        self.assertEqual(cf.explain(0.95, lang="zh"), cf.explain(0.95, lang="fr"))


# --------------------------------------------------------------------------- #
# H. 导入固化端到端（签名之前写死 → 落盘 → 验签）
# --------------------------------------------------------------------------- #

class ImportFixationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = service("r21-import-" + self.id().split(".")[-1])
        self.addCleanup(lambda: shutil.rmtree(self.svc._r21_room, ignore_errors=True))

    def test_import_with_confidence_fixates_it_before_the_signature(self) -> None:
        rec = base_record("act-r21-e2e-given", confidence=0.82,
                          confidence_source={"method": "logprobs", "logprob": 0.82,
                                             "samples": 12})
        outcome = import_payload(self.svc, [rec], request_id="req-r21")
        self.assertEqual(1, outcome["created"])
        stored = self.svc.store.get("act-r21-e2e-given")
        self.assertEqual(0.82, stored["confidence"])
        self.assertEqual({"method": "logprobs", "logprob": 0.82, "samples": 12},
                         stored["confidence_source"], "来源形态逐字送达，不追加键")
        self.assertEqual([], schema_errors(stored))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "固化在签名之前 → 验签必须过")
        self.assertEqual("valid", checks["entry_hash"], "confidence_source 也进了 entry_hash")

    def test_import_without_confidence_is_null_plus_default_shape(self) -> None:
        rec = base_record("act-r21-e2e-none", confidence=None)
        rec.pop("confidence_source", None)
        outcome = import_payload(self.svc, [rec], request_id="req-r21")
        self.assertEqual(1, outcome["created"])
        stored = self.svc.store.get("act-r21-e2e-none")
        self.assertIsNone(stored["confidence"], "§2.4：来源什么都没给 → 维持 null")
        self.assertEqual({"method": "default", "logprob": None,
                          "tool_success_rate": None, "samples": None},
                         stored["confidence_source"])
        self.assertEqual([], schema_errors(stored))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"])
        self.assertEqual("valid", checks["entry_hash"])

    def test_upsert_fixates_the_merged_product(self) -> None:
        first = base_record("act-r21-e2e-merge", confidence=None)
        import_payload(self.svc, [first], request_id="req-r21")
        incoming = base_record("act-r21-e2e-merge", confidence=0.64,
                               confidence_source={"method": "tool_success_rate",
                                                  "tool_success_rate": 0.64, "samples": 9})
        outcome = import_payload(self.svc, [incoming], request_id="req-r21")
        self.assertEqual(1, outcome["updated"])
        stored = self.svc.store.get("act-r21-e2e-merge")
        self.assertEqual(0.64, stored["confidence"])
        self.assertEqual("tool_success_rate", stored["confidence_source"]["method"])
        self.assertEqual([], schema_errors(stored))
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "合并 → 固化 → 重签：合并在签名之前")

    def test_low_confidence_import_flips_requires_action_with_full_c1(self) -> None:
        """R2-2 计划内的行为变更（任务 §2.3）：导入侧 ``<0.5`` 现在**翻**，且翻完 C1 全套合规。

        R2-1 的同名用例断言"不翻"——它断言的是当时（联动未接线）的现状，不是永久契约；
        R2-2 把联动接进 ``fixate_for_import`` 之后，这条路径必须断言"翻 + reason + 三条选项
        + policy + 可决定状态 + uncertainty"，并且落盘记录 schema 校验零错、验签通过。
        """
        rec = running_record("act-r21-e2e-low", confidence=0.31)
        rec.pop("uncertainty", None)
        import_payload(self.svc, [rec], request_id="req-r21")
        stored = self.svc.store.get("act-r21-e2e-low")
        self.assertEqual(0.31, stored["confidence"])
        self.assertTrue(stored["requires_action"], "R2-2：低置信导入必须进「需要我」面板")
        self.assertEqual("pending_confirmation", stored["status"], "C1：状态必须落在可决定集")
        self.assertGreaterEqual(len(str(stored["requires_action_reason"] or "")), 4,
                                "C1：reason 必填且 ≥4 字")
        self.assertEqual({"approve", "modify", "reject"},
                         {o["kind"] for o in stored["intervention_options"]},
                         "C1：三条决定选项齐备，面板才点得动")
        self.assertTrue(stored["policy"]["requires_human_confirmation"], "C1：policy 同步为需人确认")
        self.assertIsNotNone(stored["uncertainty"], "C7：≤0.6 必须带 uncertainty")
        self.assertEqual([], schema_errors(stored), "翻完必须是一条 schema 合法记录")
        checks = self.svc.secretariat.verify_record(stored)["checks"]
        self.assertEqual("valid", checks["signature"], "联动写在签名之前 ⇒ 签名覆盖联动结果")

    def test_forged_method_string_becomes_default_shape(self) -> None:
        rec = base_record("act-r21-e2e-forged", confidence=0.7,
                          confidence_source={"method": "human", "logprob": 0.99})
        import_payload(self.svc, [rec], request_id="req-r21")
        stored = self.svc.store.get("act-r21-e2e-forged")
        self.assertEqual("default", stored["confidence_source"]["method"])
        self.assertEqual([], source_errors(stored["confidence_source"]))
        self.assertEqual([], schema_errors(stored), "schema 外的 method 绝不能进落盘记录")

    def test_confidence_in_the_signature_domain_is_tamper_evident(self) -> None:
        """铁律 1 的反证：签名之后改 confidence → 验签必须红（所以只能导入前固化）。"""
        rec = base_record("act-r21-e2e-tamper", confidence=0.9)
        import_payload(self.svc, [rec], request_id="req-r21")
        stored = self.svc.store.get("act-r21-e2e-tamper")
        tampered = dict(stored)
        tampered["confidence"] = 0.99
        checks = self.svc.secretariat.verify_record(tampered)["checks"]
        self.assertEqual("invalid", checks["signature"])
        self.assertEqual("invalid", checks["entry_hash"])

    def test_native_event_builder_fixates_before_signing(self) -> None:
        """``make_event``（SDK 上报的组装器）也在字段封闭闸门之前固化。"""
        given = ingest_mod.make_event("act-r21-sdk-given", action_type="write",
                                      status="running", intent="写一份周报",
                                      confidence=0.77)
        self.assertEqual(0.77, given["confidence"])
        self.assertEqual("template", given["confidence_source"]["method"])
        self.assertIsNone(given["confidence_source"]["logprob"], "没给 logprobs 就是 None")
        self.assertEqual([], source_errors(given["confidence_source"]))
        bare = ingest_mod.make_event("act-r21-sdk-bare", action_type="read",
                                     status="running", intent="查一下日历")
        self.assertIsNone(bare["confidence"], "来源什么都没给 → 维持 null，不兜底成 1.0")
        self.assertEqual({"method": "default", "logprob": None,
                          "tool_success_rate": None, "samples": None},
                         bare["confidence_source"])
        self.assertIn("来源未提供置信度", json.dumps(bare, ensure_ascii=False),
                      "导入侧既有 note 口径不动（notes 已如实说明）")
        self.assertFalse(bare["requires_action"],
                         "来源没给置信度 ⇒ 判不出低置信，联动不翻（不凭空造卡片）")

    def test_reported_events_are_fixated_before_the_signer(self) -> None:
        """上报路径同口径：``IngestBroker`` 也在 signer 之前固化（签名覆盖置信度）。"""
        signed: list[dict] = []

        def signer(record, previous):
            signed.append(copy.deepcopy(record))
            return record

        store = DictStore()
        broker = ingest_mod.IngestBroker(store, ingest_mod.StreamHub(), mode="local",
                                         language="zh", signer=signer)
        event = ingest_mod.make_event("act-r21-broker", action_type="read",
                                      status="succeeded", intent="读一份月度报表",
                                      agent_id="r21-agent", actor_id="r21-actor",
                                      workspace_id="r21-ws", session_id="r21-sess",
                                      confidence=None)
        result = broker.ingest({"events": [event]}, token={"anonymous": True},
                               request_id="req-r21-broker", strict=True,
                               source_kind="sdk")
        self.assertEqual(1, result["created"])
        self.assertTrue(signed, "signer 必须被调过")
        self.assertEqual("default", signed[0]["confidence_source"]["method"],
                         "进 signer 的记录已带来源形态（固化在签名之前）")
        self.assertIsNone(signed[0]["confidence"])
        self.assertEqual("default", store.get("act-r21-broker")["confidence_source"]["method"])


# --------------------------------------------------------------------------- #
# I. 铁律静态自证
# --------------------------------------------------------------------------- #

class IronRuleStaticTests(unittest.TestCase):
    def test_signature_fields_untouched(self) -> None:
        """§1.1：11 字段一字未动，``confidence`` 在签名域里 → 只能签名前固化。"""
        self.assertEqual(("timestamp", "agent_id", "action_type", "tool", "input_hash",
                          "output_hash", "prev_hash", "entry_hash", "risk_level",
                          "confidence", "status"), tuple(sec_mod.SIGNATURE_FIELDS))
        self.assertEqual(11, len(sec_mod.SIGNATURE_FIELDS))
        self.assertIn("confidence", sec_mod.SIGNATURE_FIELDS)

    def test_no_runtime_override_entry_in_the_module_surface(self) -> None:
        """§1.1：服务端不得提供任何运行期改判 confidence 的入口。"""
        names = list(cf.__all__)
        for name in names:
            for token in RUNTIME_OVERRIDE_TOKENS:
                self.assertNotIn(token, name, f"{name} 看起来是运行期改判入口")
        self.assertIn("fixate_for_import", names, "唯一的写入口是导入固化")

    def test_contract_functions_are_all_present(self) -> None:
        """docs/07 §4.6 的函数名逐名存在且可调用。"""
        for name in CONTRACT_NAMES:
            self.assertTrue(callable(getattr(cf, name, None)), f"缺契约函数 {name}")
        self.assertEqual(("logprobs", "tool_success_rate", "hybrid", "template", "default"),
                         cf.METHODS)

    def test_module_is_stdlib_only_and_offline(self) -> None:
        """§1.3：零新依赖（纯标准库），且无网络字面量。"""
        source = (REPO / "agent_explainer" / "confidence.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        allowed = set(sys.stdlib_module_names)
        self.assertTrue(roots <= allowed, f"非标准库依赖：{sorted(roots - allowed)}")
        for token in ("http://", "https://", "urllib", "socket.", "subprocess"):
            self.assertNotIn(token, source)

    def test_merge_allowlists_untouched(self) -> None:
        """§2.5：两张白名单本任务零改动（已覆盖 confidence / confidence_source）。"""
        self.assertIn("confidence", ingest_mod.MERGE_FIELDS)
        self.assertIn("confidence_source", ingest_mod.EXPANSION_MERGE_FIELDS)
        self.assertNotIn("confidence_source", ingest_mod.EXPANSION_PROTECTED_FIELDS)

    def test_scratch_reason_is_reported_not_tempdir(self) -> None:
        """§1.4：临时目录来自 scratch 的探测/退回约定（本文件不碰标准临时目录）。"""
        self.assertTrue(scratch_reason())
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        # 只查真用法（行首 import / 真调用），否则连本条断言自己的字面量都会命中
        self.assertIsNone(re.search(r"^import tempfile", source, re.M), "不许 import tempfile")
        self.assertIsNone(re.search(r"TemporaryDirectory\s*\(", source), "不许用标准临时目录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
