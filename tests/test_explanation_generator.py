"""t4 [t2-2] 验收测试：解释生成流水线（模板版，中英双语）。

运行（全程离线，只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_explanation_generator.py

覆盖任务验收点：
  1. 10 类 action_type × 2 语言，每种每语言至少 1 条断言（这里每格 4 条）；
  2. 缺失字段降级文案（原因/结果/对象/建议/不确定性/影响面 …）；
  3. 边界值（超长值、0/1/大量计数、耗时与成本极值、置信度阈值、预算下限）；
  4. 四要素齐全 + 单句 ≤120 字符 + zh/en 模板各自原生；
  5. 纯模板：不接 LLM、可重放、事实指纹可复算、产物过 schema 与产品 lint、隐私零命中；
  6. 决定 ≠ 生效（裁决③）：执行端没回报时禁止讲成"已执行"。
"""
from __future__ import annotations

import ast
import copy
import datetime as _dt
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import explanations as ex  # noqa: E402
from agent_explainer import trace_adapter as ta  # noqa: E402
from agent_explainer.trace_adapter import ACTION_LABEL, ACTION_TYPES  # noqa: E402
import facts_hash  # noqa: E402
import validate_schema as vs  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json").read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLES = ROOT / "samples"
NATIVE = json.loads((SAMPLES / "action_records.json").read_text(encoding="utf-8"))["records"]
FIXED_NOW = _dt.datetime(2026, 11, 10, 1, 0, 0, tzinfo=_dt.timezone.utc)

#: 任务点名的 10 类（验收要求：10 类 × 2 语言各 ≥1 条断言）
TEN_TYPES: tuple[str, ...] = ("read", "write", "plan", "confirm", "error",
                              "ask", "tool_call", "wait", "finish", "intervention")
LANGS: tuple[str, ...] = ex.SUPPORTED_LANGS

CJK = re.compile(r"[一-鿿]")


def base_record(**overrides: object) -> dict:
    """以演示夹具里的 read/succeeded 记录为模板，按需覆盖字段（合成数据）。"""
    template = next(r for r in NATIVE if r["id"] == "act-demo-0002")
    rec = copy.deepcopy(template)
    rec.update(overrides)
    return rec


def record_of_type(action_type: str, **overrides: object) -> dict:
    """尽量沿用夹具里该类型的真实样本；夹具没有的类型用模板改 action_type。"""
    for rec in NATIVE:
        if rec["action_type"] == action_type:
            out = copy.deepcopy(rec)
            out.update(overrides)
            return out
    return base_record(action_type=action_type, **overrides)


def elements(sentence: str, lang: str) -> list[str]:
    return [s for s in sentence.split(ex.JOINER[lang]) if s]


def assert_four_elements(case: unittest.TestCase, rec: dict, lang: str) -> str:
    """四要素断言：做了什么（动作标签）/ 为什么 / 结果 / 是否需要你操作。"""
    text = ex.generate(rec, lang)
    label = ACTION_LABEL[rec["action_type"] if rec["action_type"] in ACTION_TYPES else "other"][lang]
    case.assertIn(label, text, f"{lang} 句子里必须出现动作标签：{text}")
    if lang == "zh":
        case.assertTrue("因为" in text, f"缺『为什么』：{text}")
        case.assertTrue("结果" in text, f"缺『结果』：{text}")
        case.assertTrue("你" in text, f"缺『是否需要你操作』：{text}")
    else:
        low = text.lower()
        case.assertTrue("because" in low or "no reason" in low, f"missing WHY: {text}")
        case.assertTrue("result" in low, f"missing RESULT: {text}")
        case.assertTrue("you" in low, f"missing ACTION-NEEDED: {text}")
    case.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT, f"单句超 120：{len(text)} {text}")
    case.assertGreaterEqual(len(elements(text, lang)), 4, f"四要素段数不足：{text}")
    return text


# ----------------------------------------------------------------------------------
# 1. 10 类 × 2 语言矩阵
# ----------------------------------------------------------------------------------


class TenTypesMatrixTests(unittest.TestCase):
    def test_matrix_ten_types_x_two_languages(self):
        for action_type in TEN_TYPES:
            rec = record_of_type(action_type)
            for lang in LANGS:
                with self.subTest(action_type=action_type, lang=lang):
                    assert_four_elements(self, rec, lang)

    def test_every_action_type_has_native_templates(self):
        self.assertEqual(set(ex.WHAT_PHRASE), set(ACTION_TYPES))
        for action_type, pair in ex.WHAT_PHRASE.items():
            self.assertEqual(set(pair), {"zh", "en"}, action_type)
            for lang, template in pair.items():
                self.assertIn("{subject}", template, f"{action_type}/{lang} 缺主体槽位")
        for action_type in ACTION_TYPES:
            self.assertIn(action_type, ex.NEED_HINT_BY_ACTION)
            self.assertEqual(set(ex.NEED_HINT_BY_ACTION[action_type]), {"zh", "en"})

    def test_templates_are_not_placeholder_translations(self):
        """zh 模板含中文骨架、en 模板含英文骨架，且 en 骨架里不含任何中文字符。"""
        for key, pair in (("why_prefix", ex.WHY_PREFIX), ("result_prefix", ex.RESULT_PREFIX),
                          ("need_prefix", ex.NEED_PREFIX), ("need_none", ex.NEED_NONE),
                          ("demo_flag", ex.DEMO_FLAG)):
            self.assertEqual(set(pair), {"zh", "en"}, key)
            self.assertTrue(CJK.search(pair["zh"]), f"{key} 的 zh 必须是中文骨架：{pair['zh']}")
            self.assertFalse(CJK.search(pair["en"]), f"{key} 的 en 必须是英文骨架：{pair['en']}")
        for status, pair in ex.STATUS_RESULT_PHRASE.items():
            self.assertIn("结果", pair["zh"], status)
            self.assertIn("Result", pair["en"], status)

    def test_sentence_pairs_differ_across_languages(self):
        for rec in NATIVE[:6]:
            zh, en = ex.generate(rec, "zh"), ex.generate(rec, "en")
            self.assertNotEqual(zh, en, rec["id"])
            self.assertTrue(CJK.search(zh), rec["id"])
            self.assertIn(ex.DEMO_FLAG["en"], en)          # 英文骨架前缀

    def test_intervention_type_says_recorded_only(self):
        rec = record_of_type("intervention")
        zh = ex.generate(rec, "zh")
        self.assertIn(ACTION_LABEL["intervention"]["zh"], zh)
        hint = ex.NEED_HINT_BY_ACTION["intervention"]
        self.assertIn("只被记录", hint["zh"])
        self.assertIn("recorded only", hint["en"])


# ----------------------------------------------------------------------------------
# 2. 四要素结构
# ----------------------------------------------------------------------------------


class FourElementsTests(unittest.TestCase):
    def test_explain_returns_four_nonempty_fields(self):
        for rec in NATIVE:
            for lang in LANGS:
                out = ex.explain(rec, lang)
                for name in ex.ELEMENTS:
                    self.assertTrue(str(out[name]).strip(), f"{rec['id']}/{lang}/{name} 为空")
                    self.assertLessEqual(len(out[name]), ex.SINGLE_SENTENCE_LIMIT, rec["id"])
                self.assertEqual(out["language"], lang)
                self.assertIn("flags", out)
                self.assertIn("sentence", out)

    def test_need_segment_states_whether_user_must_act(self):
        pending = record_of_type("delete")
        for lang in LANGS:
            out = ex.explain(pending, lang)
            self.assertTrue(out["needs_you"], out["need"])
            self.assertTrue(out["requires_action"])
            self.assertIn(ex.NEED_PREFIX[lang], out["need"])
        plain = record_of_type("read")
        for lang in LANGS:
            out = ex.explain(plain, lang)
            self.assertFalse(out["needs_you"])
            self.assertIn(ex.NEED_NONE[lang], out["need"])

    def test_doubt_and_demo_flags_in_sentence(self):
        low = record_of_type("tool_call", confidence=0.4,
                             uncertainty={"reason": "比对工具只给了命中数"})
        zh = ex.generate(low, "zh")
        self.assertTrue(zh.startswith(ex.DEMO_FLAG["zh"] + ex.DOUBT_FLAG["zh"]), zh)
        self.assertIn("比对工具只给了命中数", ex.l2(low, "zh")[-1])
        en = ex.generate(low, "en")
        self.assertTrue(en.startswith(ex.DEMO_FLAG["en"] + ex.DOUBT_FLAG["en"]), en)
        self.assertTrue(ex.explain(low, "en")["low_confidence"])

    def test_demo_flag_can_be_turned_off(self):
        gen = ex.ExplanationGenerator(show_demo_flag=False)
        for lang in LANGS:
            text = gen.generate(NATIVE[0], lang)
            self.assertNotIn(ex.DEMO_FLAG[lang], text)
        self.assertIn(ex.DEMO_FLAG["zh"], ex.generate(NATIVE[0], "zh"))

    def test_non_demo_record_has_no_demo_flag(self):
        rec = copy.deepcopy(NATIVE[0])
        rec["demo_data"] = False
        rec["synthetic"] = False
        for lang in LANGS:
            self.assertNotIn(ex.DEMO_FLAG[lang], ex.generate(rec, lang))


# ----------------------------------------------------------------------------------
# 3. 缺失字段降级
# ----------------------------------------------------------------------------------


class DegradationTests(unittest.TestCase):
    def test_missing_rationale_says_source_gave_no_reason(self):
        rec = base_record(rationale=None)
        gen = ex.ExplanationGenerator()
        for lang in LANGS:
            text = gen.generate(rec, lang)
            self.assertIn(ex.WHY_MISSING[lang], text)
            self.assertIn("reason_missing", ex.explain(rec, lang)["flags"])
        self.assertGreaterEqual(gen.stats["reason_missing"], 1)
        self.assertGreaterEqual(gen.stats["degraded_total"], 1)

    def test_missing_result_summary_on_success_is_explicit(self):
        rec = base_record(status="succeeded", result_summary=None)
        for lang in LANGS:
            self.assertIn(ex.RESULT_MISSING[lang], ex.generate(rec, lang))
            self.assertIn("result_missing", ex.explain(rec, lang)["flags"])

    def test_missing_intent_falls_back_without_inventing(self):
        rec = base_record(intent=None, tool_used=None)
        for lang in LANGS:
            self.assertIn(ex.SUBJECT_FALLBACK[lang], ex.generate(rec, lang))
            self.assertNotIn("也许", ex.generate(rec, "zh"))      # 不猜
            self.assertNotIn("probably", ex.generate(rec, "en").lower())

    def test_missing_failure_reason_and_suggestion(self):
        rec = base_record(action_type="error", status="failed", error=None,
                          result_summary=None, suggested_next=[])
        for lang in LANGS:
            text = ex.generate(rec, lang)
            self.assertIn(ex.FAIL_REASON_MISSING[lang], text)
            self.assertIn(ex.NEED_NONE[lang], text)

    def test_missing_requires_action_reason_uses_action_hint(self):
        rec = record_of_type("payment", requires_action=True, requires_action_reason=None)
        zh = ex.generate(rec, "zh")
        self.assertIn(ex.NEED_PREFIX["zh"], zh)
        self.assertIn(ex.NEED_HINT_BY_ACTION["payment"]["zh"][:4], zh)

    def test_low_confidence_without_uncertainty_still_flags(self):
        rec = base_record(confidence=0.2, uncertainty=None)
        zh = ex.generate(rec, "zh")
        self.assertTrue(zh.startswith(ex.DOUBT_FLAG["zh"]) or
                        zh.startswith(ex.DEMO_FLAG["zh"] + ex.DOUBT_FLAG["zh"]), zh)
        self.assertTrue(any("判断不确定" in s for s in ex.l2(rec, "zh")))

    def test_missing_cost_and_latency_show_as_none_in_l3(self):
        rec = base_record(cost=None, latency_ms=None)
        for lang in LANGS:
            line = ex.l3(rec, lang)
            self.assertIn("无" if lang == "zh" else "-", line)

    def test_unknown_action_type_and_status_degrade_to_defaults(self):
        rec = base_record(action_type="teleport", status="exploded")
        for lang in LANGS:
            text = ex.generate(rec, lang)
            self.assertIn(ACTION_LABEL["other"][lang], text)
            self.assertIn(ex.STATUS_RESULT_PHRASE["observed"][lang][:4], text)
            self.assertLessEqual(len(ex.l1(rec, lang)), ex.L1_LIMIT[lang])

    def test_garbage_nested_types_do_not_crash(self):
        rec = base_record(cost="0.03", latency_ms="fast", impact_scope="all",
                          user_intervention="approve", execution_receipt=5,
                          suggested_next="重试", intervention_options="approve")
        for lang in LANGS:
            text = ex.generate(rec, lang)
            self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT)
            self.assertGreaterEqual(len(ex.l2(rec, lang)), ex.L2_MIN_STEPS)

    def test_empty_record_nonstrict_degrades_strict_raises(self):
        loose = ex.ExplanationGenerator()
        text = loose.generate({}, "zh")
        self.assertGreaterEqual(len(text), 10)
        self.assertIn(ACTION_LABEL["other"]["zh"], text)
        strict = ex.ExplanationGenerator(strict=True)
        with self.assertRaises(ex.ExplanationError) as ctx:
            strict.generate({"action_type": "read", "status": "observed"}, "zh")
        self.assertEqual("EXPLAIN_FACTS_MISSING", ctx.exception.code)
        self.assertEqual("intent", ctx.exception.field)
        self.assertIn(ctx.exception.code, str(ctx.exception))


# ----------------------------------------------------------------------------------
# 4. 边界值
# ----------------------------------------------------------------------------------


class BoundaryValueTests(unittest.TestCase):
    LONG = "很长" * 300          # 600 字符
    PATHY = "C:\\Users\\alice\\photos\\a.jpg"

    def test_oversized_values_stay_within_120(self):
        rec = base_record(intent=self.LONG, rationale=self.LONG, result_summary=self.LONG,
                          requires_action=True, requires_action_reason=self.LONG)
        for lang in LANGS:
            text = ex.generate(rec, lang)
            self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT, f"{lang} 超长")
            self.assertIn("…", text, f"{lang} 超长值必须显式截断")
            self.assertGreaterEqual(len(elements(text, lang)), 4)

    def test_oversized_values_keep_l1_l2_l3_limits(self):
        rec = base_record(intent=self.LONG, rationale=self.LONG, result_summary=self.LONG)
        for lang in LANGS:
            self.assertLessEqual(len(ex.l1(rec, lang)), ex.L1_LIMIT[lang])
            steps = ex.l2(rec, lang)
            self.assertTrue(ex.L2_MIN_STEPS <= len(steps) <= ex.L2_MAX_STEPS)
            for step in steps:
                self.assertLessEqual(len(step), ex.L2_STEP_LIMIT, step)
            self.assertLessEqual(len(ex.l3(rec, lang)), ex.L3_LIMIT)

    def test_custom_budget_is_respected(self):
        tight = ex.ExplanationGenerator(budget=60)
        for rec in NATIVE:
            for lang in LANGS:
                self.assertLessEqual(len(tight.generate(rec, lang)), 60, f"{rec['id']}/{lang}")

    def test_latency_humanisation_edges(self):
        cases = {0: "0ms", 1: "1ms", 999: "999ms", 1000: "1.0s", 59999: "60.0s",
                 60000: "1m00s", 3600000: "60m00s", -5: "", None: ""}
        for raw, expect in cases.items():
            self.assertEqual(expect, ex._human_latency(raw), f"latency={raw}")
        rec = base_record(latency_ms=0)
        self.assertFalse(any("耗时" in s for s in ex.l2(rec, "zh")), ex.l2(rec, "zh"))

    def test_cost_humanisation_edges(self):
        self.assertEqual("$0.0021", ex._human_cost({"amount": 0.0021, "currency": "USD"}))
        self.assertEqual("¥0.03", ex._human_cost({"amount": 0.03, "currency": "CNY"}))
        self.assertEqual("$1200", ex._human_cost({"amount": 1200, "currency": "USD"}))
        self.assertEqual("$0", ex._human_cost({"amount": 0, "currency": "USD"}))
        self.assertEqual("5 BTC", ex._human_cost({"amount": 5, "currency": "BTC"}))
        self.assertEqual("", ex._human_cost({"amount": 1}))
        self.assertEqual("", ex._human_cost({"currency": "USD"}))
        self.assertEqual("", ex._human_cost("0.03"))
        self.assertEqual("", ex._human_cost(None))
        self.assertEqual("", ex._human_cost({"amount": True, "currency": "USD"}))

    def test_confidence_threshold_boundaries(self):
        for value, low in ((0.0, True), (0.6, True), (0.61, False), (1.0, False), (None, False)):
            rec = base_record(confidence=value, uncertainty={"reason": "边界"} if value in (0.0, 0.6) else None)
            self.assertEqual(low, ex.explain(rec, "zh")["low_confidence"], f"confidence={value}")
            for lang in LANGS:
                self.assertLessEqual(len(ex.generate(rec, lang)), ex.SINGLE_SENTENCE_LIMIT)

    def test_target_count_edges_and_impact_wording(self):
        self.assertEqual(None, ex._target_count(None))
        self.assertEqual(None, ex._target_count({"targets": []}))
        self.assertEqual(1, ex._target_count({"targets": [{"kind": "resource"}]}))
        self.assertEqual(12, ex._target_count({"targets": [{"kind": "resource", "count": 12}]}))
        self.assertEqual(13, ex._target_count({"targets": [{"count": 12}, {"count": 1}]}))
        rec = record_of_type("delete", impact_scope={"targets": [{"kind": "file", "ref": "x", "count": 1000}],
                                                     "reversible": False, "data_categories": ["internal"],
                                                     "summary": "s" * 300},
                             risk_level="critical")
        for lang in LANGS:
            steps = ex.l2(rec, lang)
            self.assertTrue(any(("1000" in s and ("不可撤回" in s if lang == "zh" else "irreversible" in s))
                                for s in steps), steps)

    def test_many_items_and_options_boundaries(self):
        rec = base_record(suggested_next=[{"kind": "retry", "text": f"建议{i}"} for i in range(5)],
                          intervention_options=[{"id": f"o{i}", "kind": "other", "label": f"L{i}",
                                                 "consequence": f"C{i}"} for i in range(5)],
                          labels=["a" * 40] * 20)
        for lang in LANGS:
            self.assertLessEqual(len(ex.generate(rec, lang)), ex.SINGLE_SENTENCE_LIMIT)
            self.assertLessEqual(len(ex.l1(rec, lang)), ex.L1_LIMIT[lang])
            self.assertTrue(ex.describe_options(rec, lang))

    def test_due_at_present_and_expired_wording(self):
        rec = record_of_type("delete", action_due_at="2026-11-09T09:42:03Z")
        for lang in LANGS:
            text = ex.generate(rec, lang)
            self.assertIn("11-09 09:42", text, f"{lang} 句子里要留催办截止时间")
        expired = record_of_type("wait", status="expired", requires_action=True,
                                 requires_action_reason="需要你回答", action_due_at="2026-11-09T09:42:03Z")
        for lang in LANGS:
            text = ex.generate(expired, lang)
            # 超时提示在受保护前缀里，必须整段出现；截止时间点已无意义
            self.assertIn(ex.EXPIRED_HEAD[lang], text)
            self.assertNotIn("11-09 09:42", text, "超时后不再说截止点")

    def test_l2_step_count_bounds_for_every_fixture(self):
        for rec in NATIVE:
            for lang in LANGS:
                steps = ex.l2(rec, lang)
                self.assertGreaterEqual(len(steps), ex.L2_MIN_STEPS, f"{rec['id']}/{lang}")
                self.assertLessEqual(len(steps), ex.L2_MAX_STEPS, f"{rec['id']}/{lang}")
                for step in steps:
                    self.assertLessEqual(len(step), ex.L2_STEP_LIMIT, f"{rec['id']}/{lang} {step}")


# ----------------------------------------------------------------------------------
# 5. 决定 ≠ 生效（队长裁决③）
# ----------------------------------------------------------------------------------


class EffectHonestyTests(unittest.TestCase):
    def decided(self, decision: str, state: str) -> dict:
        rec = record_of_type("delete")
        rec["status"] = {"approve": "approved", "modify": "modified", "reject": "rejected"}[decision]
        rec["requires_action"] = False
        rec["user_intervention"] = {"decision": decision, "recorded": True,
                                    "decided_by": "actor-demo", "decided_at": "2026-11-09T10:00:00Z",
                                    "decided_by_role": "editor", "modified_fields": None,
                                    "option_id": None, "audit_entry_hash": None}
        rec["execution_receipt"] = {"state": state, "attempted_at": None, "finished_at": None,
                                    "effect_scope": None, "runtime_message": None}
        return rec

    def test_unreported_receipt_never_claims_execution(self):
        rec = self.decided("approve", "unreported")
        zh = ex.generate(rec, "zh")
        self.assertIn(ex.RECEIPT_SHORT["unreported"]["zh"], zh)
        self.assertNotIn("已执行", zh)
        self.assertNotIn("已完成", zh)
        en = ex.generate(rec, "en").lower()
        self.assertIn("not reported", en)
        self.assertIn("effect unknown", en)
        self.assertNotIn("it ran", en)

    def test_executed_receipt_says_confirmed(self):
        for lang in LANGS:
            text = ex.generate(self.decided("modify", "executed"), lang)
            self.assertIn(ex.RECEIPT_SHORT["executed"][lang], text)

    def test_refused_by_runtime_is_not_reported_as_success(self):
        rec = self.decided("reject", "refused_by_runtime")
        zh = ex.generate(rec, "zh")
        self.assertIn(ex.RECEIPT_SHORT["refused_by_runtime"]["zh"], zh)
        self.assertNotIn("已完成", zh)
        self.assertNotIn("执行成功", zh)

    def test_all_five_receipt_states_have_phrases_in_both_languages(self):
        for table in (ex.RECEIPT_PHRASE, ex.RECEIPT_SHORT):
            for state, pair in table.items():
                self.assertEqual(set(pair), {"zh", "en"}, state)
                self.assertNotEqual(pair["zh"], pair["en"])
        self.assertEqual(set(ex.RECEIPT_PHRASE),
                         {"unreported", "executed", "failed", "refused_by_runtime", "timeout"})
        self.assertEqual(set(ex.RECEIPT_SHORT), set(ex.RECEIPT_PHRASE))

    def test_no_invented_cost(self):
        rec = base_record(cost=None)
        for lang in LANGS:
            self.assertNotIn("$", ex.generate(rec, lang))
            self.assertNotIn("¥", ex.generate(rec, lang))


# ----------------------------------------------------------------------------------
# 6. 与 schema / 产品 lint / 事实指纹的集成
# ----------------------------------------------------------------------------------


class SchemaAndLintTests(unittest.TestCase):
    def enriched(self, rec: dict, lang: str) -> dict:
        return ex.ExplanationGenerator().enrich([rec], lang, now=FIXED_NOW)[0]

    def test_rendered_human_readable_passes_schema(self):
        for rec in NATIVE:
            for lang in LANGS:
                out = self.enriched(rec, lang)
                errors = [f"{e.keyword}: {e.message} @ {e.data_path}"
                          for e in VALIDATOR.collect(out)]
                self.assertEqual([], errors, f"{rec['id']}/{lang} schema 不通过")

    def test_rendered_fields_respect_product_limits(self):
        long_record = base_record(rationale="很" * 500, result_summary="长" * 500)
        for rec in NATIVE + [long_record]:
            for lang in LANGS:
                hr = self.enriched(rec, lang)["human_readable"]
                self.assertLessEqual(len(hr["l1"]), vs.L1_MAX[lang], rec["id"])
                self.assertGreaterEqual(len(hr["l2"]), 3, rec["id"])
                self.assertLessEqual(len(hr["l2"]), vs.L2_MAX_STEPS, rec["id"])
                for step in hr["l2"]:
                    self.assertLessEqual(len(step), vs.L2_MAX_PER_STEP, step)
                self.assertLessEqual(len(hr["l3"]), vs.L3_MAX, rec["id"])
                self.assertEqual(hr["language"], lang)
                self.assertEqual("template", hr["generated_by"])
                self.assertFalse(hr["may_contain_inference"])
                self.assertEqual(FIXED_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), hr["generated_at"])

    def test_facts_hash_is_recomputable_after_render(self):
        for rec in NATIVE:
            for lang in LANGS:
                out = self.enriched(rec, lang)
                ok, declared, computed = facts_hash.verify(out)
                self.assertTrue(ok, f"{rec['id']}/{lang} 指纹不可复算：{declared} != {computed}")
                self.assertEqual(ex._facts_hash(rec), out["human_readable"]["source_facts_hash"])

    def test_render_does_not_touch_the_original_record(self):
        before = copy.deepcopy(NATIVE)
        enriched = ex.enrich(NATIVE, "zh", now=FIXED_NOW)
        self.assertEqual(before, NATIVE, "enrich 必须返回新记录，不能就地改写")
        self.assertEqual(len(enriched), len(NATIVE))
        self.assertNotEqual(before[0]["human_readable"], enriched[0]["human_readable"])

    def test_privacy_lint_zero_hits_on_all_outputs(self):
        hits: list[str] = []
        for rec in NATIVE:
            for lang in LANGS:
                payload = {"sentence": ex.generate(rec, lang), "explain": ex.explain(rec, lang),
                           "l1": ex.l1(rec, lang), "l2": ex.l2(rec, lang), "l3": ex.l3(rec, lang),
                           "hr": self.enriched(rec, lang)["human_readable"],
                           "options": ex.describe_options(rec, lang)}
                for text in vs.iter_strings(payload):
                    for pattern, label in vs.FORBIDDEN_PATTERNS:
                        if re.search(pattern, text):
                            hits.append(f"{rec['id']}/{lang}/{label}: {text[:60]}")
        self.assertEqual([], hits)

    def test_raw_private_path_is_redacted_in_output(self):
        rec = base_record(intent=f"read {self.PATHY} with key sk-abcdefghij123456",
                          rationale=f"路径 {self.PATHY} 里有 {self.PATHY}",
                          result_summary=f"写了 {self.PATHY}，联系人 bob@example.com",
                          tool_used={"name": "fs.read", "version": None, "provider": None,
                                     "call_id": None,
                                     "params_summary": f"path={self.PATHY}", "result_ref": None})
        for lang in LANGS:
            payload = [ex.generate(rec, lang), ex.l1(rec, lang), ex.l2(rec, lang),
                       ex.l3(rec, lang), ex.explain(rec, lang)]
            for text in vs.iter_strings(payload):
                for pattern, label in vs.FORBIDDEN_PATTERNS:
                    self.assertIsNone(re.search(pattern, text),
                                      f"{lang}/{label} 泄漏：{text[:80]}")
            self.assertIn("<path:1>", ex.generate(rec, lang))

    PATHY = "C:\\Users\\alice\\photos\\a.jpg"

    def test_all_records_from_adapter_render_clean(self):
        for name in ("langfuse", "otel_genai", "mcp_events"):
            doc = json.loads((SAMPLES / "traces" / f"{name}.json").read_text(encoding="utf-8"))
            for rec in ta.convert_trace_to_action_record(doc, now=FIXED_NOW):
                for lang in LANGS:
                    text = ex.generate(rec, lang)
                    self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT, f"{name}/{rec['id']}")
                    self.assertGreaterEqual(len(elements(text, lang)), 4, f"{name}/{rec['id']}")
                    out = self.enriched(rec, lang)
                    errors = [f"{e.keyword}: {e.message}" for e in VALIDATOR.collect(out)]
                    self.assertEqual([], errors, f"{name}/{rec['id']}/{lang}")
                    ok, _d, _c = facts_hash.verify(out)
                    self.assertTrue(ok, f"{name}/{rec['id']}/{lang} 指纹")


# ----------------------------------------------------------------------------------
# 7. 选项文案（三选项双语 + 后果）
# ----------------------------------------------------------------------------------


class OptionsTests(unittest.TestCase):
    def test_three_options_localized_with_consequences(self):
        rec = record_of_type("delete")
        for lang in LANGS:
            opts = ex.describe_options(rec, lang)
            self.assertEqual(["approve", "modify", "reject"], [o["kind"] for o in opts])
            for opt, template in zip(opts, ("approve", "modify", "reject")):
                self.assertEqual(ex.OPTION_TEMPLATE[template][lang]["label"], opt["label"])
                self.assertTrue(len(opt["consequence"]) >= 4, opt)
                self.assertIsInstance(opt["editable_fields"], list)

    def test_synthetic_options_when_record_has_none(self):
        rec = record_of_type("confirm")
        rec["intervention_options"] = []
        for lang in LANGS:
            self.assertEqual(3, len(ex.describe_options(rec, lang)))
        self.assertEqual([], ex.describe_options(base_record(requires_action=False), "zh"))

    def test_options_keep_ids_and_editable_fields_from_record(self):
        rec = record_of_type("delete")
        opts = ex.describe_options(rec, "en")
        self.assertEqual([o["id"] for o in rec["intervention_options"]], [o["id"] for o in opts])
        modify = [o for o in opts if o["kind"] == "modify"][0]
        self.assertEqual(rec["intervention_options"][1]["editable_fields"], modify["editable_fields"])


# ----------------------------------------------------------------------------------
# 8. API 表面与可观测
# ----------------------------------------------------------------------------------


class ApiSurfaceTests(unittest.TestCase):
    def test_module_level_helpers_delegate_to_generator(self):
        gen = ex.ExplanationGenerator()
        rec = NATIVE[1]
        self.assertEqual(gen.generate(rec, "en"), ex.generate(rec, "en"))
        self.assertEqual(gen.l1(rec, "zh"), ex.l1(rec, "zh"))
        self.assertEqual(gen.l2(rec, "zh"), ex.l2(rec, "zh"))
        self.assertEqual(gen.l3(rec, "zh"), ex.l3(rec, "zh"))
        self.assertEqual(gen.describe_options(rec, "zh"), ex.describe_options(rec, "zh"))
        self.assertIs(ex.default_generator(), ex.default_generator(), "默认生成器要复用")

    def test_generator_default_language_is_used(self):
        en_gen = ex.ExplanationGenerator(lang="en")
        self.assertIn(ex.DEMO_FLAG["en"], en_gen.generate(NATIVE[0]))
        self.assertEqual("en", en_gen.explain(NATIVE[0])["language"])

    def test_unsupported_language_raises_readable_error(self):
        for bad in ("fr", "ZH", "zh_CN", "zh-Hans", ""):
            with self.assertRaises(ex.ExplanationError) as ctx:
                ex.generate(NATIVE[0], bad)
            self.assertEqual("EXPLAIN_LANG_UNSUPPORTED", ctx.exception.code)
            self.assertIn(ctx.exception.code, str(ctx.exception))
            self.assertTrue(ctx.exception.reason.strip())
            self.assertTrue(ctx.exception.hint)

    def test_lang_none_falls_back_to_generator_default(self):
        self.assertEqual(ex.ExplanationGenerator(lang="en").generate(NATIVE[0]),
                         ex.ExplanationGenerator(lang="en").generate(NATIVE[0], None))
        self.assertIn(ex.DEMO_FLAG["zh"], ex.generate(NATIVE[0], None))

    def test_non_mapping_input_raises_readable_error(self):
        for bad in (None, 42, [], "not json", ["a"]):
            with self.assertRaises(ex.ExplanationError) as ctx:
                ex.generate(bad, "zh")
            self.assertEqual("EXPLAIN_RECORD_TYPE", ctx.exception.code)

    def test_json_string_record_is_accepted(self):
        """界面上常把记录当 JSON 文本传进来：解析后要得到与 dict 入参一样的结果。"""
        for source in (NATIVE[0], NATIVE[4]):
            text = json.dumps(source, ensure_ascii=False)
            for lang in LANGS:
                self.assertEqual(ex.generate(source, lang), ex.generate(text, lang))
                self.assertEqual(ex.l2(source, lang), ex.l2(text, lang))
                self.assertEqual(ex.describe_options(source, lang), ex.describe_options(text, lang))
        deleting = json.dumps(NATIVE[4], ensure_ascii=False)   # requires_action=true
        self.assertTrue(NATIVE[4]["requires_action"], "夹具第 5 条应是等待用户拍板的记录")
        for lang in LANGS:
            kinds = [o["kind"] for o in ex.describe_options(deleting, lang)]
            self.assertEqual(["approve", "modify", "reject"], kinds)
        plan_text = json.dumps(NATIVE[0], ensure_ascii=False)  # requires_action=false
        self.assertEqual([], ex.describe_options(plan_text, "en"), "不需要的记录不要凭空造选项")

    def test_stats_counters_observability(self):
        gen = ex.ExplanationGenerator()
        gen.reset_stats()
        gen.generate(base_record(rationale=None), "zh")
        gen.generate(record_of_type("delete"), "zh")
        gen.generate(base_record(confidence=0.3, uncertainty={"reason": "低"}), "zh")
        gen.generate(base_record(status="succeeded", result_summary=None), "zh")
        stats = gen.stats
        self.assertGreaterEqual(stats["reason_missing"], 1)
        self.assertGreaterEqual(stats["needs_action"], 1)
        self.assertGreaterEqual(stats["low_confidence"], 1)
        self.assertGreaterEqual(stats["result_missing"], 1)
        self.assertGreaterEqual(stats["degraded_total"], 2)

    def test_bilingual_bundle_contains_everything(self):
        bundle = ex.ExplanationGenerator().bilingual(NATIVE[4], now=FIXED_NOW)
        self.assertEqual({"zh", "en"}, set(bundle))
        for lang in LANGS:
            part = bundle[lang]
            self.assertEqual({"sentence", "l1", "l2", "l3", "elements", "options", "human_readable"},
                             set(part))
            self.assertEqual(lang, part["human_readable"]["language"])

    def test_enrich_rejects_non_mapping(self):
        with self.assertRaises(ex.ExplanationError):
            ex.ExplanationGenerator().enrich(["not json"], "zh")

    def test_generated_by_validation(self):
        with self.assertRaises(ex.ExplanationError) as ctx:
            ex.ExplanationGenerator().render_human_readable(NATIVE[0], "zh", generated_by="gpt")
        self.assertEqual("EXPLAIN_GENERATED_BY_INVALID", ctx.exception.code)
        hr = ex.ExplanationGenerator().render_human_readable(
            NATIVE[0], "zh", generated_by="llm", model="demo-model", now=FIXED_NOW)
        self.assertTrue(hr["may_contain_inference"], "llm/hybrid 必须打 inference 标记")
        self.assertEqual("demo-model", hr["model"])
        self.assertIsNone(hr["template_id"])


# ----------------------------------------------------------------------------------
# 9. 纯模板 / 离线纯净（不接入 LLM）
# ----------------------------------------------------------------------------------


class PurityTests(unittest.TestCase):
    MODULE = ROOT / "agent_explainer" / "explanations.py"
    FORBIDDEN_IMPORTS = {"requests", "urllib3", "httpx", "aiohttp", "socket", "subprocess",
                         "multiprocessing", "openai", "anthropic", "mcp", "tiktoken", "http"}

    def test_no_llm_or_network_imports(self):
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        allowed = set(sys.stdlib_module_names) | {"agent_explainer", "tools"}
        for root in roots:
            self.assertIn(root, allowed, f"引入了非标准库 {root}")
            self.assertNotIn(root, self.FORBIDDEN_IMPORTS, f"引入了联网/LLM 模块 {root}")

    def test_source_has_no_network_or_key_literals(self):
        """t5 之后允许出现 `api_key_present` / `base_url` 这两个**字段名**（只记有无、且脱敏），
        但绝不允许出现密钥值、端点值或任何网络调用字面量。"""
        src = self.MODULE.read_text(encoding="utf-8")
        for token in ("http://", "https://", "urllib", "socket", "openai", "requests",
                      "Authorization:", "Bearer ey"):
            self.assertNotIn(token, src, f"源码含 {token}")
        self.assertIsNone(re.search(r"api_key\w*\s*[:=]\s*[\"'][^\"']+[\"']", src),
                          "源码里硬编码了密钥值")
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{12,}", src), "源码里硬编码了密钥")
        self.assertNotIn("self.api_key =", src, "只允许记录密钥有无（api_key_present），不许存密钥本身")

    def test_deterministic_and_replayable(self):
        for rec in NATIVE:
            for lang in LANGS:
                self.assertEqual(ex.generate(rec, lang), ex.generate(rec, lang), rec["id"])
                self.assertEqual(ex.l2(rec, lang), ex.l2(rec, lang), rec["id"])
        gen_a, gen_b = ex.ExplanationGenerator(), ex.ExplanationGenerator()
        self.assertEqual(gen_a.generate(NATIVE[3], "en"), gen_b.generate(NATIVE[3], "en"))

    def test_generated_at_only_time_source_is_injectable(self):
        a = ex.ExplanationGenerator().render_human_readable(NATIVE[0], "zh", now=FIXED_NOW)
        b = ex.ExplanationGenerator().render_human_readable(NATIVE[0], "zh", now=FIXED_NOW)
        self.assertEqual(a, b)
        self.assertEqual(FIXED_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), a["generated_at"])
        naive = ex.ExplanationGenerator().render_human_readable(
            NATIVE[0], "zh", now=_dt.datetime(2026, 11, 10, 1, 0, 0))
        self.assertEqual(naive["generated_at"], a["generated_at"], "裸时间按 UTC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
