"""t5 [t2-3] 验收测试：解释生成流水线（LLM 增强版，可注入 Mock 客户端）。

运行（全程离线，只用标准库 unittest；**不真实调用任何外部 API**）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_explanation_llm.py

覆盖任务验收点：
  1. 双模式可切换（构造时 / 每次调用 / set_mode），template 模式永不触碰客户端；
  2. 提示词含关键字段：用户偏好、任务名、历史动作摘要、输出语言、事实 JSON、诚实规则；
  3. 异常 / 超时 / 空返回 / 超长 / 隐私泄漏 / 假装已执行 / 要素不全 → 整句回退模板，绝不崩；
  4. 双语输出：zh/en 各走自己语言的模板提示词，各调一次，出处标 hybrid + AI 生成标记；
  5. 默认 NullLLMClient（无 API key）不崩，设置中心 enabled 是三道闸之一；
  6. 核心永不出网：调用后 sys.modules 不新增网络栈；提示词隐私 lint 零命中。
"""
from __future__ import annotations

import ast
import copy
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
import facts_hash  # noqa: E402
import validate_schema as vs  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json").read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
PROMPTS = ROOT / "agent_explainer" / "prompts"
NATIVE = json.loads((ROOT / "samples" / "action_records.json").read_text(encoding="utf-8"))["records"]


def _all_fixture_records() -> list[dict]:
    """36 条合成夹具：12 native + 8 langfuse + 7 otel_genai + 9 mcp_events（全部只读）。"""
    records = list(copy.deepcopy(NATIVE))
    for name in ("langfuse.json", "otel_genai.json", "mcp_events.json"):
        payload = json.loads((ROOT / "samples" / "traces" / name).read_text(encoding="utf-8"))
        records.extend(ta.convert_trace_to_action_record(payload))
    return records


ALL_RECORDS = _all_fixture_records()
LANGS: tuple[str, ...] = ex.SUPPORTED_LANGS


def schema_errors(record: dict) -> list[str]:
    """ActionRecord schema 校验（与 t3/t4 测试同一入口）。"""
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]

SETTINGS = {"enabled": True, "model": "mock-model", "temperature": 0.3,
            "max_tokens": 200, "timeout_s": 5.0}
NETWORK_ROOTS = ("http", "https", "socket", "ssl", "urllib", "requests", "openai",
                 "httpx", "aiohttp", "urllib3", "anthropic", "tiktoken", "grpc")


# ----------------------------------------------------------------------------------
# 测试替身与工具
# ----------------------------------------------------------------------------------


def base_record(**overrides: object) -> dict:
    """演示夹具里的 delete/待拍板记录（requires_action=true，执行端未回报）。"""
    rec = copy.deepcopy(next(r for r in NATIVE if r["id"] == "act-demo-0005"))
    rec.update(overrides)
    return rec


def with_receipt(rec: dict, state: str | None) -> dict:
    out = copy.deepcopy(rec)
    receipt = dict(out.get("execution_receipt") or {})
    receipt["state"] = state
    out["execution_receipt"] = receipt
    return out


def facts_of(prompt: str) -> dict:
    """提示词里唯一的事实块（真实客户端与评测脚本都读这一段）。"""
    return json.loads(prompt.split(ex.FACTS_BEGIN)[1].split(ex.FACTS_END)[0])


def fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def compose(facts: dict) -> str:
    """按事实块写一句合规文案（模拟一个守规矩的模型，zh/en 各自成句）。"""
    limit = int(facts["max_length"])
    why = (facts["rationale"] or "来源没说明")[:12]
    result = (facts["result_summary"] or "来源没说明")[:10]
    if facts["language"] == "zh":
        head = "演示数据：" if facts["demo_data"] else ""
        return fit(f"{head}{facts['action_label']} {str(facts['subject'])[:10]}；"
                   f"因为 {why}；结果：{result}；需要你拍板", limit)
    head = "Demo data: " if facts["demo_data"] else ""
    return fit(f"{head}{facts['action_label']} {str(facts['subject'])[:12]}; because {why}; "
               f"Result: {result}; needs you to decide", limit)


class MockLLMClient(ex.LLMClient):
    """记录调用参数的假客户端：可控返回文本 / None / 空 / 非字符串 / 抛异常。"""

    model = "mock-model"
    configured = True

    def __init__(self, *, reply: str | None = None, raises: BaseException | None = None,
                 returns: str = "text") -> None:
        self.prompts: list[str] = []
        self.calls_kwargs: list[dict] = []
        self.reply = reply
        self.raises = raises
        self.returns = returns

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def complete(self, prompt: str, **kwargs) -> object:
        self.prompts.append(prompt)
        self.calls_kwargs.append(kwargs)
        if self.raises is not None:
            raise self.raises
        if self.returns == "none":
            return None
        if self.returns == "empty":
            return "   \n  "
        if self.returns == "list":
            return ["不像话的列表"]
        if self.returns == "int":
            return 42
        if self.reply is not None:
            return self.reply
        return compose(facts_of(prompt))


def llm_generator(client: ex.LLMClient | None = None, **kwargs) -> ex.ExplanationGenerator:
    settings = kwargs.pop("settings", SETTINGS)
    return ex.ExplanationGenerator(mode="llm", llm_client=client or MockLLMClient(),
                                   settings=settings, **kwargs)


def template_generator(**kwargs) -> ex.ExplanationGenerator:
    return ex.ExplanationGenerator(**kwargs)


def assert_fallback(case: unittest.TestCase, rec: dict, lang: str, reason: str,
                    *, client: ex.LLMClient | None = None, **kwargs) -> str:
    """回退必须：不抛异常 + 内容与模板版逐字相同 + provenance 说明原因。"""
    gen = llm_generator(client, **kwargs)
    text = gen.generate(rec, lang)
    case.assertEqual(text, template_generator().generate(rec, lang),
                     f"回退后必须等于模板文案（reason={reason}）：{text}")
    info = gen._last_llm
    case.assertFalse(info["used"], f"回退不能标记已用 LLM：{info}")
    case.assertEqual(info["reason"], reason, f"回退原因应为 {reason}：{info}")
    case.assertGreaterEqual(gen.stats["llm_fallback"], 1)
    return text


# ----------------------------------------------------------------------------------
# 1. 双模式切换
# ----------------------------------------------------------------------------------


class ModeSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = base_record()
        self.mock = MockLLMClient()

    def test_default_mode_is_template_and_never_touches_client(self):
        gen = ex.ExplanationGenerator(llm_client=self.mock)
        self.assertEqual(gen.mode, ex.MODE_TEMPLATE)
        text = gen.generate(self.rec, "zh")
        self.assertEqual(text, template_generator().generate(self.rec, "zh"))
        self.assertEqual(self.mock.calls, 0, "template 模式一次都不许调客户端")
        self.assertFalse(gen.explain(self.rec, "zh")["llm"]["used"])
        self.assertNotIn("llm_used", gen.explain(self.rec, "zh")["flags"])

    def test_llm_mode_uses_client_output_and_model(self):
        reply = "演示数据：删除 12 个重复文件；因为内容完全相同；结果：等你拍板；需要你点头"
        gen = llm_generator(MockLLMClient(reply=reply))
        self.assertEqual(gen.generate(self.rec, "zh"), reply)
        self.assertEqual(gen._last_llm["model"], "mock-model")
        self.assertTrue(gen._last_llm["used"])
        self.assertEqual(gen.stats["llm_calls"], 1)
        self.assertEqual(gen.stats["llm_used"], 1)

    def test_per_call_mode_override_both_directions(self):
        plain = ex.ExplanationGenerator(llm_client=self.mock, settings=SETTINGS)
        plain.generate(self.rec, "zh", mode="llm")
        self.assertEqual(self.mock.calls, 1, "generate(mode='llm') 必须真的走客户端")
        rich = llm_generator(self.mock)
        before = self.mock.calls
        text = rich.generate(self.rec, "zh", mode="template")
        self.assertEqual(self.mock.calls, before, "generate(mode='template') 不许调客户端")
        self.assertEqual(text, template_generator().generate(self.rec, "zh"))

    def test_set_mode_is_chainable_and_switches(self):
        gen = ex.ExplanationGenerator(llm_client=self.mock)
        self.assertIs(gen.set_mode("llm"), gen)
        gen.settings = ex.LLMSettings.from_mapping(SETTINGS)
        gen.generate(self.rec, "zh")
        self.assertEqual(self.mock.calls, 1)
        gen.set_mode("template")
        gen.generate(self.rec, "zh")
        self.assertEqual(self.mock.calls, 1)

    def test_invalid_mode_raises_readable_error(self):
        for bad in ("LLM", "Template", "auto", "", 42, None):
            with self.subTest(mode=bad):
                with self.assertRaises(ex.ExplanationError) as ctx:
                    ex.ExplanationGenerator(mode=bad)
                self.assertEqual(ctx.exception.code, "EXPLAIN_MODE_UNSUPPORTED")
                self.assertIn("template", ctx.exception.hint or "")
        gen = template_generator()
        with self.assertRaises(ex.ExplanationError):
            gen.generate(base_record(), "zh", mode="gpt")

    def test_client_without_complete_is_rejected_at_construction(self):
        for bad in (object(), "client", 42, lambda: None, ["complete"]):
            with self.subTest(client=type(bad).__name__):
                with self.assertRaises(ex.ExplanationError) as ctx:
                    ex.ExplanationGenerator(mode="llm", llm_client=bad, settings=SETTINGS)
                self.assertEqual(ctx.exception.code, "EXPLAIN_LLM_CLIENT_INVALID")

    def test_no_client_defaults_to_null_client_offline(self):
        gen = ex.ExplanationGenerator(mode="llm", settings=SETTINGS)
        self.assertIsInstance(gen.llm_client, ex.NullLLMClient)
        self.assertFalse(gen.llm_status()["client_configured"])
        self.assertEqual(gen.generate(base_record(), "zh"),
                         template_generator().generate(base_record(), "zh"))

    def test_stats_observability_counters(self):
        gen = llm_generator(MockLLMClient(raises=RuntimeError("boom")))
        gen.generate(base_record(), "zh")
        gen.generate(base_record(), "en")
        self.assertEqual(gen.stats["llm_calls"], 2)
        self.assertEqual(gen.stats["llm_used"], 0)
        self.assertEqual(gen.stats["llm_fallback"], 2)
        self.assertEqual(gen.stats["llm_rejected"], 2)
        self.assertGreater(gen.stats["prompt_chars"], 500)


# ----------------------------------------------------------------------------------
# 2. 回退矩阵（一切失败 = 整句回退模板）
# ----------------------------------------------------------------------------------


class FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = base_record()

    def test_no_api_key_null_client_falls_back(self):
        gen = ex.ExplanationGenerator(mode="llm")            # 无 client、无 settings
        text = gen.generate(self.rec, "zh")
        self.assertEqual(text, template_generator().generate(self.rec, "zh"))
        self.assertEqual(gen._last_llm["reason"], "settings_disabled",
                         "设置中心没 enabled 之前，连客户端都不该问（省掉一次无谓调用）")
        self.assertEqual(gen.llm_status()["client_configured"], False)

    def test_configured_settings_but_null_client_still_falls_back(self):
        gen = ex.ExplanationGenerator(mode="llm", settings={"enabled": True, "model": "m"})
        self.assertEqual(gen.generate(self.rec, "zh"),
                         template_generator().generate(self.rec, "zh"))
        self.assertEqual(gen._last_llm["reason"], "client_not_configured")
        self.assertEqual(gen.stats["llm_calls"], 0, "NullLLMClient 不计入真实调用")

    def test_settings_center_disabled_falls_back(self):
        assert_fallback(self, self.rec, "zh", "settings_disabled",
                        settings={"enabled": False, "model": "mock-model"})

    def test_client_returned_none_empty_and_bad_types_fall_back(self):
        assert_fallback(self, self.rec, "zh", "client_returned_none",
                        client=MockLLMClient(returns="none"))
        assert_fallback(self, self.rec, "zh", "empty", client=MockLLMClient(returns="empty"))
        assert_fallback(self, self.rec, "zh", "client_returned_type:list",
                        client=MockLLMClient(returns="list"))
        assert_fallback(self, self.rec, "zh", "client_returned_type:int",
                        client=MockLLMClient(returns="int"))

    def test_exception_and_timeout_fall_back(self):
        assert_fallback(self, self.rec, "zh", "client_error:RuntimeError",
                        client=MockLLMClient(raises=RuntimeError("上游 500")))
        assert_fallback(self, self.rec, "zh", "timeout",
                        client=MockLLMClient(raises=TimeoutError("超过 timeout_s=5")))
        assert_fallback(self, self.rec, "en", "client_error:ConnectionError",
                        client=MockLLMClient(raises=ConnectionError("断网")))

    def test_overlong_output_is_rejected_not_clipped(self):
        reply = "演示数据：删除了 12 个文件；" + "因为这批文件的指纹与已保留样本完全一致所以判定为重复，" * 6 + "需要你拍板"
        self.assertGreater(len(reply), ex.SINGLE_SENTENCE_LIMIT)
        text = assert_fallback(self, self.rec, "zh", "over_length",
                               client=MockLLMClient(reply=reply))
        self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT)

    def test_privacy_leak_is_rejected(self):
        leaks = (
            ("演示数据：删除了 C:\\Users\\bob\\photos\\a.jpg；因为重复；结果：等你拍板；需要你点头", "privacy:windows_user_path"),
            ("演示数据：删除了 /home/alice/secret.txt；因为重复；结果：等你拍板；需要你点头", "privacy:linux_user_path"),
            ("演示数据：删除了 /Users/alice/secret.txt；因为重复；结果：等你拍板；需要你点头", "privacy:macos_user_path"),
            ("演示数据：用 sk-abcdefgh12345678 提交删除；因为重复；结果：等你拍板；需要你点头", "privacy:api_key"),
            ("演示数据：删除了 bob@corp-example.com 的邮件；因为重复；结果：等你拍板；需要你点头", "privacy:email"),
        )
        for reply, reason in leaks:
            with self.subTest(reason=reason):
                assert_fallback(self, self.rec, "zh", reason, client=MockLLMClient(reply=reply))

    def test_overclaim_is_rejected_when_runtime_silent(self):
        rec = with_receipt(base_record(), "unreported")
        replies = ("演示数据：已删除 12 个文件；因为重复；结果：已完成；不需要你操作",
                   "演示数据：删除了 12 个文件；因为重复；结果：已执行；不需要你操作",
                   "Demo data: it ran; because duplicates; Result: completed; nothing needed from you")
        for reply in replies:
            lang = "zh" if reply.startswith("演示") else "en"
            gen = llm_generator(MockLLMClient(reply=reply))
            text = gen.generate(rec, lang)
            with self.subTest(reply=reply[:20]):
                self.assertEqual(text, template_generator().generate(rec, lang))
                self.assertTrue(gen._last_llm["reason"].startswith("overclaim:"),
                                f"未回报时不许讲成已发生：{gen._last_llm}")
                self.assertEqual(gen.stats["llm_rejected"], 1)
                self.assertNotIn("已完成", text)
                self.assertNotIn("已执行", text)

    def test_claim_allowed_when_runtime_confirmed_execution(self):
        rec = with_receipt(base_record(), "executed")
        reply = "演示数据：删除了 12 个文件；因为内容重复；结果：已完成（执行端确认已执行）；不需要你操作"
        gen = llm_generator(MockLLMClient(reply=reply))
        self.assertEqual(gen.generate(rec, "zh"), reply)
        self.assertTrue(gen._last_llm["used"])

    def test_incomplete_output_is_rejected(self):
        for reply in ("随便写的一句话，什么要素都没有", "just words with no structure at all"):
            with self.subTest(reply=reply[:12]):
                lang = "zh" if reply.startswith("随便") else "en"
                assert_fallback(self, base_record(), lang, "incomplete",
                                client=MockLLMClient(reply=reply))

    def test_fallback_never_leaves_ui_empty(self):
        angry = MockLLMClient(raises=RuntimeError("服务不可用"))
        gen = llm_generator(angry)
        for rec in NATIVE:
            for lang in LANGS:
                text = gen.generate(rec, lang)
                self.assertTrue(text.strip(), "回退后界面不能空白")
                self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT)
        self.assertEqual(angry.calls, len(NATIVE) * len(LANGS))
        self.assertEqual(gen.stats["llm_used"], 0)

    def test_quoted_output_is_stripped_and_used(self):
        reply = "演示数据：删除 12 项；因为内容重复；结果：等你拍板；需要你点头"
        gen = llm_generator(MockLLMClient(reply=f"「{reply}」\n"))
        self.assertEqual(gen.generate(base_record(), "zh"), reply)


# ----------------------------------------------------------------------------------
# 3. 提示词内容与隐私
# ----------------------------------------------------------------------------------


class PromptContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = base_record()
        self.mock = MockLLMClient()
        self.gen = llm_generator(self.mock)
        self.gen.generate(self.rec, "zh")
        self.prompt = self.mock.prompts[-1]

    def test_prompt_contains_task_name_and_preferences(self):
        context = (self.rec.get("context") or {})
        self.assertIn(str(context.get("task_name")), self.prompt, "缺任务名")
        self.assertIn(str(self.rec.get("agent_name")), self.prompt, "缺助手名")
        for needle in ("画像", "L1", "详略", "白话", "关注", "中文（简体）"):
            self.assertIn(needle, self.prompt, f"缺偏好/语言字段 {needle}")
        custom = ex.ExplanationGenerator(
            mode="llm", llm_client=self.mock, settings=SETTINGS,
            preferences={"audience": "C", "granularity": "L3", "verbosity": "detailed",
                         "plain_language": False, "focus": "safety"})
        custom.generate(self.rec, "zh")
        for needle in ("画像 C：谨慎、要看依据", "L3 含参数细节", "详细", "安全与风险"):
            self.assertIn(needle, self.mock.prompts[-1], f"用户偏好没进提示词：{needle}")

    def test_preferences_from_record_context_when_unset(self):
        rec = base_record(context={"task_name": "整理演示相册并去重", "audience_profile": "C",
                                   "default_granularity": "L3", "user_language": "zh"})
        self.gen.generate(rec, "zh")
        self.assertIn(ex.AUDIENCE_LABEL["C"]["zh"], self.mock.prompts[-1],
                      "没设偏好时应使用记录 context 里的画像")
        pinned = ex.ExplanationGenerator(mode="llm", llm_client=self.mock, settings=SETTINGS,
                                         preferences={"audience": "A"})
        pinned.generate(rec, "zh")
        self.assertIn(ex.AUDIENCE_LABEL["A"]["zh"], self.mock.prompts[-1])
        self.assertNotIn(ex.AUDIENCE_LABEL["C"]["zh"], self.mock.prompts[-1],
                         "设置中心显式设过的偏好必须压过记录里的画像")

    def test_prompt_contains_history_summary(self):
        history = NATIVE[:3]
        self.gen.set_history(history)
        self.gen.generate(self.rec, "zh")
        text = self.mock.prompts[-1]
        for index in (1, 2, 3):
            self.assertIn(f"{index}. ", text, f"历史摘要缺第 {index} 条")
        self.assertIn(f"最近 {len(history)} 条", text)
        labels = {ex.ACTION_LABEL[r["action_type"]]["zh"] for r in history}
        self.assertTrue(any(label in text for label in labels), "历史摘要里没有历史动作标签")
        self.gen.generate(self.rec, "zh", history=NATIVE[:1])
        self.assertIn("最近 1 条", self.mock.prompts[-1], "每次调用的 history 要覆盖生成器默认")

    def test_history_is_capped(self):
        self.gen.set_history(NATIVE * 3)
        self.assertEqual(len(self.gen.history), ex.HISTORY_MAX)
        self.gen.generate(self.rec, "zh")
        self.assertIn(f"最近 {ex.HISTORY_MAX} 条", self.mock.prompts[-1])

    def test_prompt_contains_key_facts_and_honesty_rule(self):
        must = [self.rec["action_type"], str(self.rec["tool_used"]["name"]),
                "unreported", "11-09", "决定 ≠ 生效", "≤ 120", "act-demo-0005",
                facts_hash.compute_facts_hash(self.rec)]
        for needle in must:
            self.assertIn(str(needle), self.prompt, f"提示词缺关键字段：{needle}")
        self.assertIn("因为", self.prompt)
        self.assertIn("需要你", self.prompt)
        self.assertIn("输出语言", self.prompt)

    def test_facts_block_is_parseable_json_with_expected_values(self):
        facts = facts_of(self.prompt)
        for key in ("record_id", "language", "max_length", "task_name", "audience",
                    "granularity", "verbosity", "plain_language", "focus", "action_type",
                    "action_label", "status", "subject", "requires_action", "decision",
                    "receipt_state", "risk_level", "tool", "latency", "cost", "confidence",
                    "demo_data", "facts_hash", "history"):
            self.assertIn(key, facts, f"事实块缺字段 {key}")
        self.assertEqual(facts["action_type"], self.rec["action_type"])
        self.assertEqual(facts["status"], self.rec["status"])
        self.assertEqual(facts["requires_action"], self.rec["requires_action"])
        self.assertEqual(facts["max_length"], ex.SINGLE_SENTENCE_LIMIT)
        self.assertEqual(facts["facts_hash"], facts_hash.compute_facts_hash(self.rec))
        self.assertIsNone(facts["decision"], "没决定的记录不许凭空写决定")

    def test_prompt_is_privacy_clean(self):
        rec = base_record(tool_used={"name": "filesystem.remove", "provider": "builtin",
                                     "version": "1.4",
                                     "params_summary": "target=C:\\Users\\alice\\photos\\a.jpg; "
                                                       "owner=alice@corp-example.com"})
        self.gen.generate(rec, "zh")
        text = self.mock.prompts[-1]
        for pattern, label in vs.FORBIDDEN_PATTERNS:
            self.assertIsNone(re.search(pattern, text), f"提示词泄漏 {label}")
        self.assertIn("<path:", text, "入参应已脱敏成占位符")

    def test_templates_have_no_unknown_tokens(self):
        for lang in LANGS:
            template = (PROMPTS / ex.PROMPT_FILES[lang]).read_text(encoding="utf-8")
            tokens = set(re.findall(r"\{\{\s*([a-z0-9_]+)\s*\}\}", template))
            self.assertTrue(tokens, "模板里要有占位符")
            filled, unknown = ex._fill_template(template, {"max_length": "120"} | {t: "x" for t in tokens})
            self.assertEqual(unknown, [], f"{lang} 模板有未登记的 token")
            self.assertNotIn("{{", filled)

    def test_all_placeholders_in_built_prompt_are_resolved(self):
        self.assertNotIn("{{", self.prompt, "组装后不能残留 {{token}}")

    def test_templates_are_native_per_language(self):
        zh = (PROMPTS / ex.PROMPT_FILES["zh"]).read_text(encoding="utf-8")
        en = (PROMPTS / ex.PROMPT_FILES["en"]).read_text(encoding="utf-8")
        self.assertIn("输出语言", zh)
        self.assertIn("决定 ≠ 生效", zh)
        self.assertIn("历史动作摘要", zh)
        self.assertIn("用户与任务", zh)
        self.assertRegex(zh, r"[一-鿿]", "中文模板要有中文骨架")
        self.assertIn("Output language: {{output_language}}", en)
        self.assertIn("A decision is not an effect", en)
        self.assertIn("Recent history", en)
        self.assertIn("User and task", en)
        self.assertIn("because", en.lower())
        self.assertNotIn("决定 ≠ 生效", en, "英文模板的规则必须是英文，不是中文复制粘贴")

    def test_missing_prompt_file_falls_back_without_crash(self):
        reply = "演示数据：删除 12 项；因为内容重复；结果：等你拍板；需要你点头"
        gen = ex.ExplanationGenerator(mode="llm", llm_client=MockLLMClient(reply=reply),
                                      settings=SETTINGS, prompt_dir=str(ROOT / "tests" / "no-such-dir"))
        self.assertEqual(gen.generate(self.rec, "zh"), reply)
        self.assertGreaterEqual(gen.stats["prompt_missing"], 1)
        self.assertIn("120", gen.llm_client.prompts[-1], "内置兜底模板也要带上长度约束")

    def test_prompt_is_deterministic(self):
        first = self.gen.build_prompt(self.rec, "zh")
        second = self.gen.build_prompt(self.rec, "zh")
        self.assertEqual(first, second, "同一记录同一语言的提示词必须逐字相同")
        self.assertEqual(self.gen.build_prompt(self.rec, "en"),
                         self.gen.build_prompt(self.rec, "en"))

    def test_call_kwargs_reach_the_client(self):
        kwargs = self.mock.calls_kwargs[-1]
        self.assertEqual(kwargs["model"], "mock-model")
        self.assertEqual(kwargs["temperature"], 0.3)
        self.assertEqual(kwargs["max_tokens"], 200)
        self.assertEqual(kwargs["timeout_s"], 5.0)
        self.assertEqual(kwargs["max_length"], ex.SINGLE_SENTENCE_LIMIT)
        self.assertEqual(kwargs["language"], "zh")
        self.assertEqual(kwargs["record_id"], "act-demo-0005")
        self.assertEqual(kwargs["prompt_template_id"], ex.PROMPT_TEMPLATE_ID)
        self.assertEqual(kwargs["response_format"], "plain_text")


# ----------------------------------------------------------------------------------
# 4. 双语输出
# ----------------------------------------------------------------------------------


class BilingualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = base_record()
        self.mock = MockLLMClient()
        self.gen = llm_generator(self.mock)

    def test_two_languages_two_native_prompts_two_calls(self):
        out = self.gen.bilingual(self.rec)
        self.assertEqual(self.mock.calls, 2, "两个语种各调一次，不做二次烧 token")
        zh_prompt, en_prompt = self.mock.prompts
        self.assertIn("中文（简体）", zh_prompt)
        self.assertIn("决定 ≠ 生效", zh_prompt)
        self.assertIn("Output language: English", en_prompt)
        self.assertIn("A decision is not an effect", en_prompt)
        self.assertNotIn("决定 ≠ 生效", en_prompt, "英文模板要用英文诚实规则")

    def test_sentences_come_from_llm_and_are_marked_as_inference(self):
        out = self.gen.bilingual(self.rec)
        for lang in LANGS:
            entry = out[lang]
            self.assertEqual(entry["sentence"], entry["elements"]["sentence"])
            self.assertEqual(entry["elements"]["generated_by"], "hybrid")
            self.assertTrue(entry["elements"]["llm"]["used"])
            self.assertEqual(entry["human_readable"]["generated_by"], "hybrid")
            self.assertEqual(entry["human_readable"]["model"], "mock-model")
            self.assertTrue(entry["human_readable"]["may_contain_inference"],
                            "用了 LLM 必须打「AI 生成」标记")
            self.assertLessEqual(len(entry["sentence"]), ex.SINGLE_SENTENCE_LIMIT)
        self.assertNotEqual(out["zh"]["sentence"], out["en"]["sentence"],
                            "双语必须是各自成句，不是复制同一句")

    def test_fallback_bilingual_is_plain_template(self):
        gen = ex.ExplanationGenerator(mode="llm")            # NullLLMClient
        out = gen.bilingual(self.rec)
        for lang in LANGS:
            self.assertEqual(out[lang]["elements"]["generated_by"], "template")
            self.assertEqual(out[lang]["human_readable"]["generated_by"], "template")
            self.assertIsNone(out[lang]["human_readable"]["model"])
            self.assertFalse(out[lang]["human_readable"]["may_contain_inference"])
            self.assertEqual(out[lang]["sentence"],
                             template_generator().generate(self.rec, lang))

    def test_en_prompt_keeps_source_fact_values(self):
        """事实值原样透传（中文值不改写）；只有骨架与规则是英文。"""
        self.gen.generate(self.rec, "en")
        prompt = self.mock.prompts[-1]
        self.assertIn(str(self.rec["context"]["task_name"]), prompt)
        self.assertIn("Task name", prompt)


# ----------------------------------------------------------------------------------
# 5. 出处、schema 与指纹
# ----------------------------------------------------------------------------------


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = base_record()

    def test_auto_generated_by_is_hybrid_when_used(self):
        gen = llm_generator()
        hr = gen.render_human_readable(self.rec, "zh", generated_by="auto")
        self.assertEqual(hr["generated_by"], "hybrid")
        self.assertEqual(hr["model"], "mock-model")
        self.assertTrue(hr["template_id"], "l1/l2/l3 仍是模板产物 → template_id 必须保留")
        self.assertIn(ex.SENTENCE_TEMPLATE_ID, hr["template_id"])
        self.assertTrue(hr["may_contain_inference"])
        self.assertEqual(hr["source_facts_hash"], facts_hash.compute_facts_hash(self.rec))
        self.assertLessEqual(len(hr["l1"]), vs.L1_MAX["zh"])
        self.assertTrue(3 <= len(hr["l2"]) <= vs.L2_MAX_STEPS)
        record = copy.deepcopy(self.rec)
        record["human_readable"] = hr
        self.assertEqual(schema_errors(record), [], f"hybrid 的 human_readable 要过 schema：{hr}")

    def test_auto_generated_by_is_template_when_fallback(self):
        gen = llm_generator(MockLLMClient(raises=TimeoutError("超时")))
        hr = gen.render_human_readable(self.rec, "zh", generated_by="auto")
        self.assertEqual(hr["generated_by"], "template")
        self.assertIsNone(hr["model"])
        self.assertFalse(hr["may_contain_inference"])
        self.assertEqual(schema_errors(
            {**copy.deepcopy(self.rec), "human_readable": hr}), [])

    def test_explicit_generated_by_still_wins(self):
        gen = llm_generator()
        hr = gen.render_human_readable(self.rec, "zh", generated_by="human", model=None)
        self.assertEqual(hr["generated_by"], "human")
        self.assertFalse(hr["may_contain_inference"])

    def test_enrich_batch_in_llm_mode(self):
        gen = llm_generator()
        original = copy.deepcopy(NATIVE)
        enriched = gen.enrich(NATIVE, lang="zh", generated_by="auto")
        self.assertEqual(len(enriched), len(NATIVE))
        self.assertEqual(NATIVE, original, "enrich 不许就地改写原记录")
        for rec in enriched:
            self.assertEqual(rec["human_readable"]["generated_by"], "hybrid")
            self.assertEqual(schema_errors(rec), [],
                             f"LLM 批处理后仍要过 schema：{rec['id']}")

    def test_facts_hash_is_untouched_by_llm_path(self):
        gen = llm_generator()
        gen.generate(self.rec, "zh")
        ok, declared, computed = facts_hash.verify(
            {**copy.deepcopy(self.rec),
             "human_readable": gen.render_human_readable(self.rec, "zh", generated_by="auto")})
        self.assertTrue(ok, f"指纹必须可复算：{declared} != {computed}")

    def test_llm_status_shows_the_three_gates(self):
        self.assertEqual(ex.ExplanationGenerator().llm_status()["mode"], "template")
        no_settings = ex.ExplanationGenerator(mode="llm", llm_client=MockLLMClient())
        self.assertFalse(no_settings.llm_status()["settings_enabled"])
        ready = llm_generator()
        status = ready.llm_status()
        self.assertTrue(status["settings_enabled"] and status["client_configured"])
        self.assertEqual(status["model"], "mock-model")
        self.assertEqual(status["client_type"], "MockLLMClient")


# ----------------------------------------------------------------------------------
# 6. 设置中心注入面（4.5）
# ----------------------------------------------------------------------------------


class SettingsTests(unittest.TestCase):
    def test_defaults_when_nothing_configured(self):
        s = ex.LLMSettings.from_mapping(None)
        self.assertFalse(s.enabled)
        self.assertIsNone(s.model)
        self.assertEqual(s.temperature, ex.LLM_DEFAULT_TEMPERATURE)
        self.assertEqual(s.max_tokens, ex.LLM_DEFAULT_MAX_TOKENS)
        self.assertEqual(s.timeout_s, ex.LLM_DEFAULT_TIMEOUT_S)
        self.assertFalse(s.api_key_present)

    def test_out_of_range_values_fall_back_to_defaults(self):
        s = ex.LLMSettings.from_mapping({"enabled": True, "model": "m", "temperature": 99,
                                         "max_tokens": 5, "timeout_s": 0, "language": "fr"})
        self.assertEqual(s.temperature, ex.LLM_DEFAULT_TEMPERATURE)
        self.assertEqual(s.max_tokens, ex.LLM_DEFAULT_MAX_TOKENS)
        self.assertEqual(s.timeout_s, ex.LLM_DEFAULT_TIMEOUT_S)
        self.assertIsNone(s.language)
        self.assertTrue(s.enabled)

    def test_api_key_material_is_never_stored(self):
        secret = "sk-abcdefgh12345678"
        s = ex.LLMSettings.from_mapping({"enabled": True, "api_key": secret,
                                         "base_url": f"https://api.x.test/v1?token={secret}"})
        self.assertTrue(s.api_key_present, "只记密钥有无")
        for surface in (repr(s), str(s), json.dumps(s.to_public_dict(), ensure_ascii=False),
                        json.dumps(s.call_kwargs(), ensure_ascii=False)):
            self.assertNotIn(secret, surface, f"密钥明文出现在：{surface[:120]}")
        self.assertNotIn("api_key", s.to_public_dict())
        self.assertNotIn(secret, s.base_url or "", "base_url 必须脱敏")

    def test_unknown_keys_land_in_extra_and_model_is_clipped(self):
        s = ex.LLMSettings.from_mapping({"model": "m" * 300, "provider": "ollama",
                                         "seed": 7, "temperature": 0.0})
        self.assertLessEqual(len(s.model or ""), 120)
        self.assertEqual(s.extra.get("provider"), "ollama")
        self.assertEqual(s.extra.get("seed"), 7)
        self.assertEqual(s.temperature, 0.0)

    def test_make_generator_from_settings_center_payload(self):
        client = MockLLMClient()
        gen = ex.make_generator(mode="llm", llm_client=client, lang="zh",
                                settings={"enabled": True, "model": "qwen2.5:7b",
                                          "base_url": "http://127.0.0.1:11434/v1",
                                          "temperature": 0.2, "max_tokens": 300,
                                          "timeout_s": 6.0},
                                preferences={"audience": "B", "verbosity": "short"})
        self.assertEqual(gen.settings.model, "qwen2.5:7b")
        self.assertEqual(gen.preferences["audience"], "B")
        gen.generate(base_record(), "zh")
        self.assertEqual(client.calls_kwargs[-1]["model"], "qwen2.5:7b")
        self.assertEqual(client.calls_kwargs[-1]["timeout_s"], 6.0)

    def test_preferences_whitelist_and_garbage_immune(self):
        gen = ex.ExplanationGenerator(preferences={"audience": "Z", "granularity": "L9",
                                                    "verbosity": "endless", "focus": "mood",
                                                    "plain_language": "false",
                                                    "language": "fr", "evil": {"a": 1}})
        prefs = gen.preferences
        self.assertEqual(prefs["audience"], ex.DEFAULT_PREFERENCES["audience"])
        self.assertEqual(prefs["granularity"], ex.DEFAULT_PREFERENCES["granularity"])
        self.assertEqual(prefs["verbosity"], ex.DEFAULT_PREFERENCES["verbosity"])
        self.assertEqual(prefs["focus"], ex.DEFAULT_PREFERENCES["focus"])
        self.assertIs(prefs["plain_language"], False)
        self.assertIsNone(prefs["language"])
        self.assertEqual(gen.generate(base_record(), "zh"),
                         template_generator().generate(base_record(), "zh"))


# ----------------------------------------------------------------------------------
# 7. 离线纯净：核心永不出网
# ----------------------------------------------------------------------------------


class PurityTests(unittest.TestCase):
    SOURCE = (ROOT / "agent_explainer" / "explanations.py").read_text(encoding="utf-8")

    def test_imports_stay_stdlib_only(self):
        tree = ast.parse(self.SOURCE)
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        allowed = {"__future__", "copy", "datetime", "json", "re", "collections", "pathlib", "typing"}
        self.assertTrue(roots <= allowed, f"出现了非白名单 import：{roots - allowed}")
        for banned in NETWORK_ROOTS:
            self.assertNotIn(banned, roots)

    def test_source_has_no_endpoint_or_key_literals(self):
        for needle in ("https://api", "http://api", "openai.com", "Authorization:",
                       "requests.post", "urllib.request", "sk-proj-"):
            self.assertNotIn(needle, self.SOURCE, f"源码不该出现 {needle}")
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{12,}", self.SOURCE), "不许硬编码密钥")

    def test_llm_calls_do_not_load_network_modules(self):
        before = set(sys.modules)
        gen = llm_generator()
        for rec in NATIVE:
            for lang in LANGS:
                gen.generate(rec, lang)
        fresh = set(sys.modules) - before
        offenders = [name for name in fresh if name.split(".")[0] in NETWORK_ROOTS]
        self.assertEqual(offenders, [], f"LLM 路径加载了网络栈：{offenders}")

    def test_prompts_contain_no_secrets_or_endpoints(self):
        client = MockLLMClient(returns="none")
        gen = llm_generator(client)
        for rec in ALL_RECORDS:
            gen.generate(rec, "zh")
        self.assertEqual(client.calls, len(ALL_RECORDS), "36 条夹具都要过一遍隐私检查")
        for prompt in client.prompts:
            self.assertNotIn("sk-", prompt)
            self.assertNotIn("Authorization", prompt)
            self.assertNotIn("Bearer ", prompt)
            for pattern, label in vs.FORBIDDEN_PATTERNS:
                self.assertIsNone(re.search(pattern, prompt), f"提示词泄漏 {label}")

    def test_template_mode_output_is_byte_identical_after_t5(self):
        """t5 不许改动 t4 的模板文案（36 条夹具 × 双语回归护栏）。"""
        llm = llm_generator()
        plain = template_generator()
        for rec in ALL_RECORDS:
            for lang in LANGS:
                self.assertEqual(plain.generate(rec, lang), ex.generate(rec, lang), rec["id"])
                self.assertEqual(plain.generate(rec, lang),
                                 llm.generate(rec, lang, mode="template"), rec["id"])

    def test_all_fixtures_in_llm_mode_are_safe_and_marked(self):
        """36 条夹具（native/langfuse/otel/mcp）× 双语：零异常、≤120、四要素、回退原因可枚举。"""
        gen = llm_generator()
        used, fallbacks = 0, []
        for rec in ALL_RECORDS:
            for lang in LANGS:
                text = gen.generate(rec, lang)
                self.assertTrue(text.strip(), f"{rec['id']}/{lang} 界面空白")
                self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT,
                                     f"{rec['id']}/{lang} 超 120：{text}")
                self.assertTrue(re.search("因为|结果|你" if lang == "zh" else
                                          "(?i)because|Result|you", text),
                                f"{rec['id']}/{lang} 缺四要素信号：{text}")
                if gen._last_llm["used"]:
                    used += 1
                else:
                    fallbacks.append(gen._last_llm["reason"])
        total = len(ALL_RECORDS) * len(LANGS)
        self.assertEqual(used + len(fallbacks), total)
        self.assertGreaterEqual(used, total - 2, "夹具里只允许极个别触发诚实门禁")
        for reason in fallbacks:
            self.assertTrue(reason.startswith(("overclaim:", "privacy:", "incomplete", "over_length")),
                            f"回退原因不可枚举：{reason}")
        self.assertEqual(gen.stats["llm_fallback"], len(fallbacks))

    def test_full_llm_matrix_stays_within_budget_and_four_elements(self):
        gen = llm_generator()
        for rec in NATIVE:
            for lang in LANGS:
                text = gen.generate(rec, lang)
                self.assertLessEqual(len(text), ex.SINGLE_SENTENCE_LIMIT,
                                     f"{rec['id']}/{lang} 超 120：{text}")
                self.assertGreaterEqual(gen._llm_signals(text, ex._View(rec, lang)), 3,
                                        f"{rec['id']}/{lang} 要素不全：{text}")
                self.assertTrue(re.search(r"因为|结果|你" if lang == "zh" else
                                          r"(?i)because|Result|you", text),
                                f"{rec['id']}/{lang} 缺四要素信号：{text}")
        self.assertEqual(gen.stats["llm_used"], len(NATIVE) * len(LANGS))
        self.assertEqual(gen.stats["llm_fallback"], 0)

    def test_forbidden_pattern_lists_do_not_drift(self):
        """包内 lint 必须逐条覆盖 tools/validate_schema 的隐私模式（两份清单不许各自漂走）。"""
        local = {pattern for pattern, _label in ex.LLM_FORBIDDEN_PATTERNS}
        product = {pattern for pattern, _label in vs.FORBIDDEN_PATTERNS}
        self.assertEqual(product - local, set(), "包内隐私清单漏了产品 lint 的模式")
        samples = ("C:\\Users\\bob\\x", "/Users/bob/x", "/home/bob/x", "sk-abcdefghijkl",
                   "AKIAIOSFODNN7EXAMPLE", "Bearer " + "a" * 20,
                   "C:\\Users\\bob\\x; sk-abcdefghijkl; /home/bob/x; AKIAIOSFODNN7EXAMPLE")
        for sample in samples:
            product_hit = any(re.search(p, sample) for p, _l in vs.FORBIDDEN_PATTERNS)
            local_hit = any(re.search(p, sample) for p, _l in ex.LLM_FORBIDDEN_PATTERNS)
            self.assertTrue(local_hit, f"包内 lint 漏拦：{sample}")
            self.assertTrue(product_hit, f"产品 lint 样本失效（请同步更新清单）：{sample}")
        self.assertTrue(any("email" in label for _p, label in ex.LLM_FORBIDDEN_PATTERNS),
                        "输出侧要额外拦邮箱")


if __name__ == "__main__":
    unittest.main(verbosity=2)
