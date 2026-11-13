"""TraceLens（行动透镜）核心包 —— 纯标准库、离线可用。

本包按 `docs/03-architecture.md` 第 3 节的模块树切分：

    agent_explainer/
      trace_adapter/  → t3 以单文件模块 `trace_adapter.py` 落地
      explanations/   → t4 翻译引擎，落地为单文件模块 `explanations.py`（纯模板，中英双语）
                        t5 在同一模块加可选 LLM 增强：mode="template"（默认）| "llm"，
                        提示词是资源文件 `prompts/explain_zh.md` / `explain_en.md`
      audit/ auth/ ingest/ settings/ server/ store/ → 后续任务

依赖政策（架构文档第 11 节）：核心运行时**只允许 Python 标准库**。
本包内任何模块都不得 `import` 第三方库，也不得发起网络请求
（K-12：无网络、无 API key 时全功能可用）。该约束由
`tests/test_trace_adapter.py::OfflinePurityTests` 与
`tests/test_explanation_generator.py::PurityTests` 用 AST 静态检查强制。
LLM 增强（t5）不在本包内出网：只能通过调用方注入的
`llm_client.complete(prompt, **kw)` 出去；未注入 = `NullLLMClient`（返回 None → 回退模板）。

导出的公开 API（下游 t4/t14/t15 请使用这些符号）：

    convert_trace_to_action_record(payload, ...) -> list[dict]
    normalize_events(batch, ...)                 -> list[dict]
    detect(payload) / detect_format(payload)     -> str
    convert_with_raw(payload, ...)               -> ConversionOutcome
    TraceFormatError / SCHEMA_VERSION / SUPPORTED_FORMATS / FORMAT_SPEC_VERSION
    generate_explanation(record, lang) / explain_record(...) / explain_l1|l2|l3(...)
    render_human_readable(record, lang, generated_by="template"|"auto") / enrich(records, ...)
    make_generator(mode="llm", llm_client=..., settings={...}) / build_prompt(record, lang) / llm_status()
"""

from __future__ import annotations

from .trace_adapter import (  # noqa: F401  （公开 API 再导出）
    ADAPTER_NAME,
    ADAPTER_VERSION,
    FORMAT_LANGFUSE,
    FORMAT_MCP,
    FORMAT_NATIVE,
    FORMAT_OTEL,
    FORMAT_SPEC_VERSION,
    SUPPORTED_FORMATS,
    SCHEMA_VERSION,
    ConversionOutcome,
    TraceFormatError,
    convert,
    convert_trace_to_action_record,
    convert_with_raw,
    detect,
    detect_format,
    normalize_events,
    normalize_events_with_raw,
)

from .explanations import (  # noqa: F401  （t4 翻译引擎公开 API）
    ExplanationError,
    ExplanationGenerator,
    default_generator,
    describe_options,
    enrich,
)
from .explanations import (  # noqa: F401  （t5 可选 LLM 增强：默认关闭，客户端由调用方注入）
    DEFAULT_PREFERENCES,
    FACTS_BEGIN,
    FACTS_END,
    LLM_FORBIDDEN_PATTERNS,
    LLMClient,
    LLMSettings,
    MODES,
    MODE_LLM,
    MODE_TEMPLATE,
    NullLLMClient,
    OVERCLAIM_WORDS,
    PROMPT_FILES,
    PROMPT_TEMPLATE_ID,
    build_prompt,
    llm_status,
    make_generator,
)
from .explanations import explain as explain_record  # 避免与本包动词命名冲突
from .explanations import generate as generate_explanation
from .explanations import l1 as explain_l1
from .explanations import l2 as explain_l2
from .explanations import l3 as explain_l3
from .explanations import render_human_readable

__version__ = "0.5.0"   # 与 server.SERVER_VERSION 同源（0.5.0 起收掉 README 问题 9 的历史不一致）

__all__ = [
    "__version__",
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "SCHEMA_VERSION",
    "SUPPORTED_FORMATS",
    "FORMAT_SPEC_VERSION",
    "FORMAT_LANGFUSE",
    "FORMAT_OTEL",
    "FORMAT_MCP",
    "FORMAT_NATIVE",
    "TraceFormatError",
    "ConversionOutcome",
    "convert_trace_to_action_record",
    "convert_with_raw",
    "convert",
    "normalize_events",
    "normalize_events_with_raw",
    "detect",
    "detect_format",
    # —— t4 解释引擎 ——
    "ExplanationGenerator",
    "ExplanationError",
    "default_generator",
    "generate_explanation",
    "explain_record",
    "explain_l1",
    "explain_l2",
    "explain_l3",
    "describe_options",
    "render_human_readable",
    "enrich",
    # —— t5 可选 LLM 增强（默认 template；客户端注入，核心永不出网）——
    "MODES",
    "MODE_TEMPLATE",
    "MODE_LLM",
    "LLMSettings",
    "LLMClient",
    "NullLLMClient",
    "PROMPT_TEMPLATE_ID",
    "PROMPT_FILES",
    "FACTS_BEGIN",
    "FACTS_END",
    "DEFAULT_PREFERENCES",
    "LLM_FORBIDDEN_PATTERNS",
    "OVERCLAIM_WORDS",
    "make_generator",
    "build_prompt",
    "llm_status",
]
