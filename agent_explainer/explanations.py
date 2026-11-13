"""解释生成流水线（模板版，中英双语）—— t4 [t2-2]。

设计前提（全部可被测试机器校验）：

1. **纯模板、零 LLM、零网络**：文案是结构化事实的纯函数（护栏 P3）。
   同一记录 + 同一语言 → 字节级同一结论；没有随机、没有时钟（除 `generated_at` 可注入）。
2. **四要素齐全**：每句都含「做了什么 / 为什么 / 结果 / 是否需要你操作」。
   每段带固定前缀（zh：因为…/结果：…/需要你操作…；en：because…/Result: …/Needs you…），
   所以"四要素齐不齐"是可断言的，不是修辞感觉。
3. **缺失就降级，绝不编造**：`rationale` 为 null → 「来源没说明原因」；
   `result_summary` 为 null → 「来源没给结果摘要」；`confidence ≤ 0.6` → 句首打「不确定」。
4. **决定 ≠ 生效**（队长裁决③）：`user_intervention` 与 `execution_receipt.state` 分开讲；
   `unreported` 明说「执行端没回报，是否生效未知」，绝不写"已完成"。
5. **zh / en 两套原生模板**：英文不是中文的占位翻译，短语、语序、大小写各自写死。
6. **长度硬约束**（A9）：`generate()` 单句 ≤120 字符（本任务验收线）；
   `l1()` ≤40 字（en ≤80）；`l2()` 3–7 条且每条 ≤30；`l3()` ≤150。
   超长时按权重压缩**值**，保留前缀与骨架 → 四要素永不丢。
7. **隐私**：写进文案的每个字符串都过 `_redact()`（真实路径 / 密钥 / 邮箱）。

公开 API（t7 前端 / t8 干预 / t14 摄取直接调用）：
    ExplanationGenerator().generate(record, lang="zh") -> str        # 四要素单句 ≤120
    ExplanationGenerator().explain(record, lang="zh") -> dict        # 四要素分字段
    ExplanationGenerator().render_human_readable(record, lang, now=) # 完整 human_readable
    ExplanationGenerator().enrich(records, lang=..., now=...)        # 返回替换 human_readable 的新记录
    generate / explain / render_human_readable / enrich              # 模块级快捷函数
    ExplanationGenerator(mode="llm", llm_client=client, settings=s)  # t5：可选 LLM 增强（默认 template）
    ExplanationGenerator().build_prompt(record, "zh")                # 提示词（偏好/任务名/历史/输出语言）
    LLMSettings / LLMClient / NullLLMClient / make_generator / llm_status

t5 的 LLM 边界（第 8 条设计前提）：
    默认 `mode="template"`；`mode="llm"` 只通过注入的 `llm_client.complete(prompt, **kw)` 出去，
    本模块自己**永不出网**。文案要过四道上屏门禁（长度 / 隐私 / 决定≠生效 / 四要素），
    异常、超时、空返回、超长、泄漏、假装已执行、要素不全 → **整句回退模板**；
    `l1 / l2 / l3` 恒为模板产物，LLM 只替换"一句话正文"，所以出处记 `hybrid`
    且 `may_contain_inference=true`（界面必须显示「AI 生成」标记）。
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .trace_adapter import (
    ACTION_LABEL,
    ACTION_TYPES,
    L1_LIMIT,
    L3_LIMIT,
    OPTION_TEMPLATE,
    STATUS_LABEL,
    FACT_FIELDS,
    _clip,
    _iso,
    _mapping,
    _redact,
    _sha256_canonical,
)

__all__ = [
    "SINGLE_SENTENCE_LIMIT",
    "SUPPORTED_LANGS",
    "L2_STEP_LIMIT",
    "L2_MIN_STEPS",
    "L2_MAX_STEPS",
    "ELEMENTS",
    "SENTENCE_TEMPLATE_ID",
    "L1_TEMPLATE_ID",
    "L2_TEMPLATE_ID",
    "L3_TEMPLATE_ID",
    "OPTIONS_TEMPLATE_ID",
    "ExplanationError",
    "ExplanationGenerator",
    "default_generator",
    "generate",
    "explain",
    "l1",
    "l2",
    "l3",
    "describe_options",
    "render_human_readable",
    "enrich",
    # t5：可选 LLM 增强（默认 template，离线可用）
    "MODES",
    "MODE_TEMPLATE",
    "MODE_LLM",
    "PROMPT_TEMPLATE_ID",
    "PROMPT_FILES",
    "FACTS_BEGIN",
    "FACTS_END",
    "DEFAULT_PREFERENCES",
    "LLM_FORBIDDEN_PATTERNS",
    "OVERCLAIM_WORDS",
    "LLMSettings",
    "LLMClient",
    "NullLLMClient",
    "build_prompt",
    "llm_status",
    "make_generator",
]

# ----------------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------------

#: 本任务验收线：`generate()` 返回的单句长度上限（字符，zh 按字计）。
SINGLE_SENTENCE_LIMIT = 120
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "en")
ELEMENTS: tuple[str, ...] = ("what", "why", "result", "need")

L2_STEP_LIMIT = 30
L2_MIN_STEPS = 3
L2_MAX_STEPS = 7

SENTENCE_TEMPLATE_ID = "explainer.sentence.v1"
L1_TEMPLATE_ID = "explainer.l1.v2"
L2_TEMPLATE_ID = "explainer.l2.v2"
L3_TEMPLATE_ID = "explainer.l3.v1"
OPTIONS_TEMPLATE_ID = "explainer.options.v1"

#: 低置信阈值（DM：confidence ≤ 0.6 必须带 uncertainty，界面显示「不确定」）
LOW_CONFIDENCE = 0.6

#: 截止时间字符串（ISO8601）→ 只取"月-日 时:分"
_DUE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")

JOINER: dict[str, str] = {"zh": "；", "en": "; "}

#: 每段在 120 字符预算里按权重分配（前缀受保护，只裁值）。
SEGMENT_WEIGHTS: dict[str, float] = {"what": 0.32, "why": 0.20, "result": 0.26, "need": 0.22}

# —— 16 类动作的「做了什么」模板（zh / en 各写一套，不是翻译占位）———————————————
WHAT_PHRASE: dict[str, dict[str, str]] = {
    "read": {"zh": "{label}了：{subject}", "en": "{label} {subject}"},
    "write": {"zh": "{label}了：{subject}", "en": "{label} {subject}"},
    "delete": {"zh": "{label}了 {count} 项：{subject}", "en": "{label} {count} item(s): {subject}"},
    "plan": {"zh": "{label}了后面几步：{subject}", "en": "{label}: {subject}"},
    "confirm": {"zh": "{label}——{subject}", "en": "{label}: {subject}"},
    "ask": {"zh": "{label}：{subject}", "en": "{label}: {subject}"},
    "error": {"zh": "{label}，出在：{subject}", "en": "{label} at {subject}"},
    "tool_call": {"zh": "{label} {subject}", "en": "{label} {subject}"},
    "wait": {"zh": "{label}：{subject}", "en": "{label} for {subject}"},
    "finish": {"zh": "{label}：{subject}", "en": "{label}: {subject}"},
    "intervention": {"zh": "{label}：{subject}", "en": "{label}: {subject}"},
    "config_change": {"zh": "{label}：{subject}", "en": "{label} {subject}"},
    "send": {"zh": "{label}了 {subject}", "en": "{label} {subject}"},
    "payment": {"zh": "{label}：{subject}", "en": "{label}: {subject}"},
    "export": {"zh": "{label}了 {subject}", "en": "{label} {subject}"},
    "other": {"zh": "{label}：{subject}", "en": "{label} {subject}"},
}

# —— 「为什么」前缀与缺失降级 ——————————————————————————————————————
WHY_PREFIX: dict[str, str] = {"zh": "因为", "en": "because "}
WHY_MISSING: dict[str, str] = {"zh": "因为来源没说明原因", "en": "because the source gives no reason"}
RESULT_PREFIX: dict[str, str] = {"zh": "结果：", "en": "Result: "}
RESULT_MISSING: dict[str, str] = {"zh": "结果：来源没给结果摘要", "en": "Result: no result summary in the source"}
NEED_PREFIX: dict[str, str] = {"zh": "需要你操作：", "en": "Needs you: "}
NEED_NONE: dict[str, str] = {"zh": "不需要你操作", "en": "No action needed from you"}
NEED_SHORT: dict[str, str] = {"zh": "需要你拍板", "en": "Your decision needed"}
#: 决定已记录的步骤：结果段已经讲过决定与回执，这里只回答"还要不要你做事"
NEED_DECIDED: dict[str, str] = {"zh": "不用你再做什么", "en": "nothing more from you"}
#: 已超时：due 已过，催办语要换掉（不能再说"X 前"）；放在受保护前缀里，正文再长也不丢
EXPIRED_HEAD: dict[str, str] = {"zh": "（已超时，需要你重新拍板）",
                                "en": "(timed out; needs your call again) "}
DEMO_FLAG: dict[str, str] = {"zh": "演示数据：", "en": "Demo data: "}
DOUBT_FLAG: dict[str, str] = {"zh": "不确定：", "en": "Uncertain: "}

#: L1 用的现状标签（适配器标签 + 决定段三态；不改适配器，只在本模块补齐）
STATUS_L1_LABEL: dict[str, dict[str, str]] = {
    **STATUS_LABEL,
    "approved": {"zh": "已同意", "en": "approved"},
    "modified": {"zh": "改后已同意", "en": "edited & approved"},
    "rejected": {"zh": "已拒绝", "en": "rejected"},
}

#: L3 与「结果」段用的短回执（省字符，且避免"执行端 执行端没回报"这种重复）
RECEIPT_SHORT: dict[str, dict[str, str]] = {
    "unreported": {"zh": "未回报（是否生效未知）", "en": "not reported (effect unknown)"},
    "executed": {"zh": "执行端确认已执行", "en": "runtime confirmed it ran"},
    "failed": {"zh": "执行端报告执行失败", "en": "runtime reported failure"},
    "refused_by_runtime": {"zh": "被执行端拒绝执行", "en": "refused by runtime"},
    "timeout": {"zh": "执行端确认超时", "en": "confirmation timed out"},
}

#: 决定三态的短标签（句子/ L1 用；L2 用 DECISION_LABEL 全称）
DECISION_SHORT: dict[str, dict[str, str]] = {
    "approve": {"zh": "同意", "en": "approved"},
    "modify": {"zh": "改后同意", "en": "edited"},
    "reject": {"zh": "拒绝", "en": "rejected"},
}

# —— 状态 → 「结果」短语（含"结果"二字，便于机器断言四要素齐全）——————————————
STATUS_RESULT_PHRASE: dict[str, dict[str, str]] = {
    "observed": {"zh": "结果：只记到这一步，没有后续", "en": "Result: logged only, nothing after it"},
    "running": {"zh": "结果：正在做，还没出结果", "en": "Result: still running, no result yet"},
    "succeeded": {"zh": "结果：{summary}", "en": "Result: {summary}"},
    "failed": {"zh": "结果：失败了——{reason}", "en": "Result: failed — {reason}"},
    "skipped": {"zh": "结果：这一步被跳过，没有结果", "en": "Result: skipped, no result"},
    "pending_confirmation": {"zh": "结果：还没动手，在等人拍板", "en": "Result: not run yet, waiting for you"},
    "approved": {"zh": "结果：你选了{decision}，{receipt}", "en": "Result: you chose {decision}, {receipt}"},
    "modified": {"zh": "结果：你选了{decision}，{receipt}", "en": "Result: you chose {decision}, {receipt}"},
    "rejected": {"zh": "结果：你选了{decision}，{receipt}", "en": "Result: you chose {decision}, {receipt}"},
    "expired": {"zh": "结果：等到超时，没有自动执行", "en": "Result: timed out, nothing ran"},
}

#: 失败原因缺失时的降级（不编造原因）
FAIL_REASON_MISSING: dict[str, str] = {"zh": "来源没写失败原因", "en": "the source gives no failure reason"}

# —— 执行回执：决定 ≠ 生效（裁决③，禁止假闭环）—————————————————
RECEIPT_PHRASE: dict[str, dict[str, str]] = {
    "unreported": {"zh": "执行端没回报，是否生效未知", "en": "runtime has not reported back, effect unknown"},
    "executed": {"zh": "执行端确认已执行", "en": "runtime confirmed it ran"},
    "failed": {"zh": "执行端报告执行失败", "en": "runtime reported failure"},
    "refused_by_runtime": {"zh": "执行端拒绝执行这一步", "en": "runtime refused to run it"},
    "timeout": {"zh": "执行端超时未确认", "en": "runtime confirmation timed out"},
}

DECISION_LABEL: dict[str, dict[str, str]] = {
    "approve": {"zh": "同意", "en": "approved"},
    "modify": {"zh": "改后同意", "en": "approved after editing"},
    "reject": {"zh": "拒绝", "en": "rejected"},
}

RISK_LABEL: dict[str, dict[str, str]] = {
    "low": {"zh": "低风险", "en": "low risk"},
    "medium": {"zh": "中风险", "en": "medium risk"},
    "high": {"zh": "高风险", "en": "high risk"},
    "critical": {"zh": "极高风险", "en": "critical risk"},
}

#: 「需要你做什么」按动作类型给具体说法（比"请处理"更可行动）
NEED_HINT_BY_ACTION: dict[str, dict[str, str]] = {
    "read": {"zh": "看一眼要不要让它继续读", "en": "check whether it may keep reading"},
    "write": {"zh": "确认写入可以落地", "en": "confirm the write may land"},
    "delete": {"zh": "同意/改后同意/拒绝这次删除", "en": "approve/edit/reject this delete"},
    "plan": {"zh": "看计划要不要改", "en": "review the plan"},
    "confirm": {"zh": "同意/改后同意/拒绝", "en": "approve/edit/reject"},
    "ask": {"zh": "回答它的问题", "en": "answer its question"},
    "error": {"zh": "看建议：重试/改参数/找负责人", "en": "retry/edit params/ask owner"},
    "tool_call": {"zh": "确认这次工具调用可以执行", "en": "confirm this tool call"},
    "wait": {"zh": "给它要等的东西", "en": "provide what it waits for"},
    "finish": {"zh": "确认收尾结果可接受", "en": "accept the wrap-up"},
    "intervention": {"zh": "确认：干预只被记录，是否生效看执行端", "en": "note: intervention is recorded only"},
    "config_change": {"zh": "决定是否授权这项改动", "en": "allow the setting change or not"},
    "send": {"zh": "同意/改后同意/拒绝发送", "en": "approve/edit/reject the send"},
    "payment": {"zh": "确认这笔支出（金额与收款方）", "en": "confirm the payment (amount & payee)"},
    "export": {"zh": "确认导出目标与数据范围", "en": "confirm export target & data scope"},
    "other": {"zh": "看这一步要不要放行", "en": "decide whether to allow this step"},
}

#: 失败时把 suggested_next[0] 讲出来的模板（段内不用分号，保证"一句"读感）
SUGGEST_PHRASE: dict[str, str] = {
    "zh": "不需要你立刻做什么，建议：{next}",
    "en": "Nothing needed from you now, suggested: {next}",
}

DECIDED_PHRASE: dict[str, str] = {
    "zh": "你的决定已记录：{decision}，{receipt}",
    "en": "your decision is recorded: {decision}, {receipt}",
}

LOW_CONF_PHRASE: dict[str, str] = {
    "zh": "判断不确定：{reason}",
    "en": "Uncertain: {reason}",
}

IMPACT_STEP: dict[str, str] = {
    "zh": "影响：{count} 项，{reversible}",
    "en": "{count} items, {reversible}",
}
IMPACT_RISK_JOIN: dict[str, str] = {"zh": "，", "en": ", "}

IRREVERSIBLE: dict[str, str] = {"zh": "不可撤回", "en": "irreversible"}
REVERSIBLE: dict[str, str] = {"zh": "可撤回", "en": "reversible"}
UNKNOWN_REVERSIBLE: dict[str, str] = {"zh": "可撤回性未知", "en": "reversibility unknown"}

SUBJECT_FALLBACK: dict[str, str] = {"zh": "来源没给对象", "en": "no target in source"}
NO_TARGET: dict[str, str] = {"zh": "未说明对象", "en": "target not stated"}
COUNT_ALL: dict[str, str] = {"zh": "若干", "en": "some"}
COUNT_ONE: dict[str, str] = {"zh": "1", "en": "1"}

SOURCE_STEP: dict[str, str] = {
    "zh": "来源：{adapter} 导入（{fmt}）",
    "en": "Source: {adapter} import ({fmt})",
}
DEMO_STEP: dict[str, str] = {"zh": "这是合成演示数据", "en": "Synthetic demo data"}
ID_STEP: dict[str, str] = {"zh": "记录 id：{rid}", "en": "Record id: {rid}"}
TOOL_STEP: dict[str, str] = {"zh": "工具：{tool}", "en": "Tool: {tool}"}
COST_LATENCY_STEP: dict[str, str] = {"zh": "耗时 {latency}，成本 {cost}", "en": "{latency}, cost {cost}"}
LATENCY_STEP: dict[str, str] = {"zh": "耗时 {latency}", "en": "Took {latency}"}
COST_STEP: dict[str, str] = {"zh": "成本 {cost}", "en": "Cost {cost}"}
NEED_STEP: dict[str, str] = {
    "zh": "等你：同意 / 改后同意 / 拒绝",
    "en": "Your call: approve / edit / reject",
}
DECIDED_STEP: dict[str, str] = {"zh": "你的决定：{decision}（{receipt}）", "en": "Your decision: {decision} ({receipt})"}
NO_EFFECT_STEP: dict[str, str] = {"zh": "决定已记录，执行端没回报", "en": "Decision recorded; runtime silent"}

L3_JOINER = " · "
L3_FIELDS: dict[str, tuple[str, ...]] = {
    "zh": ("工具", "入参", "耗时", "成本", "决定", "执行端", "来源", "id"),
    "en": ("tool", "params", "latency", "cost", "decision", "runtime", "source", "id"),
}

# ----------------------------------------------------------------------------------
# t5：可选 LLM 增强（默认关闭，离线可用）
# ----------------------------------------------------------------------------------

MODES: tuple[str, ...] = ("template", "llm")
MODE_TEMPLATE, MODE_LLM = "template", "llm"

#: 提示词是随包分发的资源文件（PyInstaller 需 --add-data，口径见 docs/2.3-llm-notes.md）
PROMPT_TEMPLATE_ID = "explainer.prompt.v1"
PROMPT_SUBDIR = "prompts"
PROMPT_FILES: dict[str, str] = {"zh": "explain_zh.md", "en": "explain_en.md"}
FACTS_BEGIN = "<!-- TRACELENS-FACTS-BEGIN -->"
FACTS_END = "<!-- TRACELENS-FACTS-END -->"
HISTORY_MAX = 6
HISTORY_LINE_LIMIT = 46
FACTS_JSON_LIMIT = 3600

OUTPUT_LANGUAGE: dict[str, str] = {"zh": "中文（简体）", "en": "English"}
AUDIENCE_LABEL: dict[str, dict[str, str]] = {
    "A": {"zh": "画像 A：完全不懂技术", "en": "profile A: fully non-technical"},
    "B": {"zh": "画像 B：懂一点技术", "en": "profile B: semi-technical"},
    "C": {"zh": "画像 C：谨慎、要看依据", "en": "profile C: cautious, wants evidence"},
}
GRANULARITY_LABEL: dict[str, dict[str, str]] = {
    "L1": {"zh": "L1 只看结论", "en": "L1 conclusion only"},
    "L2": {"zh": "L2 结论 + 步骤", "en": "L2 conclusion + steps"},
    "L3": {"zh": "L3 含参数细节", "en": "L3 with parameter detail"},
}
VERBOSITY_LABEL: dict[str, dict[str, str]] = {
    "short": {"zh": "简短", "en": "short"},
    "standard": {"zh": "标准", "en": "standard"},
    "detailed": {"zh": "详细", "en": "detailed"},
}
FOCUS_LABEL: dict[str, dict[str, str]] = {
    "progress": {"zh": "进度", "en": "progress"},
    "safety": {"zh": "安全与风险", "en": "safety and risk"},
    "cost": {"zh": "成本", "en": "cost"},
    "decisions": {"zh": "要我拍板的事", "en": "things needing my call"},
    "none": {"zh": "无特别关注", "en": "no special focus"},
}
YES: dict[str, str] = {"zh": "是", "en": "yes"}
NO: dict[str, str] = {"zh": "否", "en": "no"}
DEFAULT_PREFERENCES: dict[str, Any] = {"audience": "A", "granularity": "L1",
                                       "verbosity": "standard", "plain_language": True,
                                       "focus": "progress", "language": None}

MISSING_MARK: dict[str, str] = {"zh": "来源没说明", "en": "not stated in source"}
NO_TASK: dict[str, str] = {"zh": "未命名任务", "en": "unnamed task"}
NO_AGENT: dict[str, str] = {"zh": "未命名助手", "en": "unnamed assistant"}
NO_DEADLINE: dict[str, str] = {"zh": "没有截止时间", "en": "no deadline"}
NO_DECISION: dict[str, str] = {"zh": "还没有决定", "en": "no decision yet"}
NO_RECEIPT: dict[str, str] = {"zh": "执行端没回报", "en": "runtime has not reported back"}
NO_HISTORY: dict[str, str] = {"zh": "（本会话没有更早的动作）", "en": "(no earlier actions in this session)"}
NO_IMPACT: dict[str, str] = {"zh": "来源没给影响面", "en": "no impact stated in source"}

LLM_DEFAULT_TEMPERATURE = 0.2
LLM_DEFAULT_MAX_TOKENS = 260
LLM_DEFAULT_TIMEOUT_S = 8.0

#: 红线（裁决③ / K-14 误导率 0 容忍）：执行端没确认时不许把"决定"讲成"已发生"
OVERCLAIM_WORDS: dict[str, tuple[str, ...]] = {
    "zh": ("已完成", "已执行", "执行完毕", "已经执行", "已删除", "已发送", "已支付", "已导出", "已写入"),
    "en": ("completed", "it ran", "was executed", "execution confirmed", "has been deleted",
           "was deleted", "were deleted", "has been sent", "was sent", "payment sent", "has been written"),
}
OVERCLAIM_OK_RECEIPTS: tuple[str, ...] = ("executed",)

#: 四要素信号（≥3/4 才算信息完整；"做了什么"用动作标签/对象判定，见 _llm_signals）
SIGNAL_PATTERNS: dict[str, tuple[str, str, str]] = {
    "zh": (r"因为|由于|原因|为了",
           r"结果|还没|没跑|失败|成功|找到|生成|等待|记录|没动",
           r"你|需要|无需|不用"),
    "en": (r"(?i)because|reason|due to|for this|in order to",
           r"(?i)result|failed|found|generated|not run|nothing ran|waiting|recorded|no result",
           r"(?i)\byou\b|needs|nothing needed|your call"),
}

#: LLM 输出侧隐私 lint。前 6 条与 `tools/validate_schema.FORBIDDEN_PATTERNS` **逐字同步**
#: （包内不能 import tools/，所以保留这份副本，由 tests/test_explanation_llm.py 双向比对防漂移），
#: 后 2 条是输出侧加码：短密钥与邮箱在文案里没有任何正当理由。
LLM_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[A-Za-z]:\\Users\\", "windows_user_path"),
    (r"/Users/\w", "macos_user_path"),
    (r"/home/\w", "linux_user_path"),
    (r"sk-[A-Za-z0-9]{12,}", "api_key"),
    (r"AKIA[0-9A-Z]{12,}", "cloud_access_key"),
    (r"Bearer\s+\S{16,}", "bearer_token"),
    (r"sk-[A-Za-z0-9_\-]{8,}", "api_key_short"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "email"),
)

_TOKEN_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")

#: 提示词文件丢了也不能崩：内置同键位的最小模板
PROMPT_FALLBACK: dict[str, str] = {
    "zh": ("把下面已核对的事实改写成一句普通人能懂的话：只输出一行、≤{{max_length}} 字符、"
           "四要素顺序固定（做了什么；因为…；结果：…；需要你做什么）。"
           "只能引用事实里的值，缺失写「来源没说明」；决定不等于生效，执行端状态={{receipt_state}}，"
           "未回报时禁止写已完成/已执行。输出语言：{{output_language}}。"
           "任务={{task_name}}，画像={{audience_label}}，详略={{verbosity_label}}，白话={{plain_language}}，"
           "关注={{focus_label}}，历史摘要：{{history}}\n事实 JSON：\n{{facts_json}}"),
    "en": ("Rewrite the verified facts below into one plain sentence: a single line, "
           "≤{{max_length}} characters, four fixed elements (what it did; because ...; "
           "Result: ...; what you need to do). Cite only the facts; write \"not stated in source\" "
           "when missing. A decision is not an effect: execution_receipt.state={{receipt_state}}, "
           "so never claim it ran when unreported. Output language: {{output_language}}. "
           "Task={{task_name}}, audience={{audience_label}}, verbosity={{verbosity_label}}, "
           "plain language={{plain_language}}, focus={{focus_label}}, history: {{history}}\n"
           "Facts JSON:\n{{facts_json}}"),
}


# ----------------------------------------------------------------------------------
# 错误
# ----------------------------------------------------------------------------------


class ExplanationError(ValueError):
    """渲染失败：带 code + 可读原因（与适配器同一风格，界面可直接显示）。"""

    def __init__(self, code: str, reason: str, *, lang: str | None = None,
                 field: str | None = None, hint: str | None = None) -> None:
        self.code = code
        self.reason = reason
        self.lang = lang
        self.field = field
        self.hint = hint
        super().__init__(self.render())

    def render(self) -> str:
        bits = [f"[{self.code}] {self.reason}"]
        if self.lang:
            bits.append(f"语言：{self.lang}")
        if self.field:
            bits.append(f"字段：{self.field}")
        if self.hint:
            bits.append(f"建议：{self.hint}")
        return "；".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.render(),
                "detail": {"lang": self.lang, "field": self.field, "hint": self.hint}}


# ----------------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------------


def _clean(text: Any) -> str:
    """进文案的每个字符串：先脱敏、再压空白、再裁剪。"""
    if text is None:
        return ""
    s = " ".join(str(text).split())
    return _redact(s)


def _seg(prefix: str, value: str, cap: int) -> str:
    """固定前缀受保护，只裁值；返回值长度恒 ≤ cap（省略号也计入预算）。"""
    if cap <= 0:
        return ""
    if not value:
        return _clip(prefix, cap)
    room = cap - len(prefix)
    if len(value) <= room:
        return prefix + value
    if room <= 4:
        return _clip(prefix + value, cap)
    return prefix + value[:room - 1] + "…"


def _caps(lang: str, prefix: str, budget: int,
          fixed: dict[str, int] | None = None,
          flexible: Sequence[str] = ELEMENTS,
          floors: dict[str, int] | None = None) -> dict[str, int]:
    """预算分配：固定骨架（降级短句、决定+回执等）先占自己的长度；可裁段各得一个
    保底额度（模板骨架，保证"因为/结果/需要你"信号不丢），剩余按权重分配。
    这样四要素骨架永不裁丢，总长仍由 generate 的兜底裁剪守住上限。"""
    joiners = len(JOINER[lang]) * (len(ELEMENTS) - 1)
    fixed = fixed or {}
    floors = floors or {}
    caps: dict[str, int] = dict(fixed)
    flex = [name for name in flexible if name not in caps]
    for name in flex:
        caps[name] = max(8, floors.get(name, 8))
    if not flex:
        return caps
    spare = budget - len(prefix) - joiners - sum(caps.values())
    if spare <= 0:                                   # 极端拥挤：各段只保底
        return caps
    weights = sum(SEGMENT_WEIGHTS[name] for name in flex)
    used = 0
    for name in flex[:-1]:
        add = int(spare * SEGMENT_WEIGHTS[name] / weights)
        caps[name] += add
        used += add
    caps[flex[-1]] += max(0, spare - used)
    return caps


def _human_due(value: Any) -> str:
    """把 ISO 截止时间压成"月-日 时:分"，省字符且不丢催办信息。"""
    match = _DUE_RE.match(str(value or ""))
    if not match:
        return _clean(value)
    _year, month, day, hour, minute = match.groups()
    return f"{month}-{day} {hour}:{minute}"


def _human_latency(ms: Any) -> str:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0:
        return ""
    if ms < 1000:
        return f"{int(ms)}ms"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    return f"{minutes}m{sec:02d}s"


def _human_cost(cost: Any) -> str:
    if not isinstance(cost, Mapping):
        return ""
    amount, currency = cost.get("amount"), cost.get("currency")
    if amount is None or currency is None:
        return ""
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return ""
    symbol = {"USD": "$", "CNY": "¥", "EUR": "€", "JPY": "¥"}.get(str(currency).upper(), "")
    text = f"{amount:.4f}".rstrip("0").rstrip(".")
    if text in ("", "0"):
        text = "0"
    return f"{symbol}{text}" if symbol else f"{text} {currency}"


def _target_count(impact: Mapping[str, Any] | None) -> int | None:
    if not isinstance(impact, Mapping):
        return None
    total = 0
    targets = impact.get("targets")
    if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
        for item in targets:
            if isinstance(item, Mapping):
                count = item.get("count")
                if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                    total += count
                else:
                    total += 1
    return total or None


def _facts_hash(record: Mapping[str, Any]) -> str:
    """与 tools/facts_hash.py 同一算法（复用包内 _sha256_canonical，避免两套实现）。"""
    payload = {"schema_version": record.get("schema_version"),
               "facts": {key: record.get(key) for key in FACT_FIELDS}}
    return "sha256:" + _sha256_canonical(payload)


def _fill_template(template: str, values: Mapping[str, str]) -> tuple[str, list[str]]:
    """`{{token}}` 替换：未登记的 token 用 "-" 占位并回报（防止模板与代码字段漂移）。"""
    unknown: list[str] = []

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        unknown.append(key)
        return "-"

    return _TOKEN_RE.sub(_sub, template), unknown


# ----------------------------------------------------------------------------------
# LLM 接入面（可注入；核心永不出网）
# ----------------------------------------------------------------------------------


class LLMSettings:
    """4.5 设置中心注入的模型配置：只描述"怎么调"。

    密钥只记录**有无**（`api_key_present`），值一律丢弃 —— 架构 §12 的明文禁令：
    密钥归 DPAPI（桌面）/ 环境变量（团队）管，不进本对象、不进 repr、不进审计。
    """

    __slots__ = ("enabled", "model", "base_url", "temperature", "max_tokens",
                 "timeout_s", "api_key_present", "language", "extra")

    def __init__(self, *, enabled: Any = False, model: Any = None, base_url: Any = None,
                 temperature: Any = LLM_DEFAULT_TEMPERATURE,
                 max_tokens: Any = LLM_DEFAULT_MAX_TOKENS,
                 timeout_s: Any = LLM_DEFAULT_TIMEOUT_S,
                 api_key_present: Any = False, language: Any = None,
                 extra: Any = None) -> None:
        self.enabled = bool(enabled)
        self.model = _clean(model)[:120] or None
        self.base_url = _redact(_clean(base_url))[:300] or None
        self.temperature = (float(temperature)
                            if isinstance(temperature, (int, float))
                            and not isinstance(temperature, bool) and 0.0 <= float(temperature) <= 2.0
                            else LLM_DEFAULT_TEMPERATURE)
        self.max_tokens = (int(max_tokens)
                           if isinstance(max_tokens, int) and not isinstance(max_tokens, bool)
                           and 40 <= int(max_tokens) <= 4000 else LLM_DEFAULT_MAX_TOKENS)
        self.timeout_s = (float(timeout_s)
                          if isinstance(timeout_s, (int, float))
                          and not isinstance(timeout_s, bool) and 0.5 <= float(timeout_s) <= 120.0
                          else LLM_DEFAULT_TIMEOUT_S)
        self.api_key_present = bool(api_key_present)
        self.language = language if language in SUPPORTED_LANGS else None
        self.extra = dict(extra) if isinstance(extra, Mapping) else {}

    _KNOWN = ("enabled", "model", "base_url", "temperature", "max_tokens",
              "timeout_s", "language", "api_key_present")
    _KEY_MATERIAL = ("api_key", "key", "api_key_env", "token")

    @classmethod
    def from_mapping(cls, data: Any) -> "LLMSettings":
        """`None` → 全默认（enabled=False，即 llm 模式也会回退模板）。未知键进 extra 不报错。"""
        item = _mapping(data)
        kwargs: dict[str, Any] = {key: item[key] for key in cls._KNOWN if key in item}
        material = next((item.get(key) for key in cls._KEY_MATERIAL if _clean(item.get(key))), None)
        kwargs["api_key_present"] = bool(kwargs.get("api_key_present")) or bool(material)
        kwargs["extra"] = {k: v for k, v in item.items()
                           if k not in set(cls._KNOWN) | set(cls._KEY_MATERIAL)}
        return cls(**kwargs)

    def call_kwargs(self) -> dict[str, Any]:
        return {"model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "timeout_s": self.timeout_s}

    def to_public_dict(self) -> dict[str, Any]:
        """界面 / 审计用的脱敏视图：没有密钥字段，base_url 已过脱敏。"""
        return {"enabled": self.enabled, "model": self.model, "base_url": self.base_url,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "timeout_s": self.timeout_s, "api_key_present": self.api_key_present,
                "language": self.language, "extra": dict(self.extra)}

    def __repr__(self) -> str:
        return (f"LLMSettings(enabled={self.enabled}, model={self.model!r}, "
                f"api_key_present={self.api_key_present})")


class LLMClient:
    """可注入客户端的最小协议（t13 的 OpenAI 兼容客户端实现它，密钥走 DPAPI / 环境变量）。

    `complete(prompt, **kwargs) -> str | None`
      · 返回 str   → 候选文案（过校验才上屏）
      · 返回 None  → 这次不可用 → 回退模板
      · 抛异常     → 失败（含 `TimeoutError`）→ 回退模板
    约定：客户端自己尊重 `kwargs["timeout_s"]`；异常信息里绝不含密钥。
    """

    configured: bool = True
    model: str | None = None

    def complete(self, prompt: str, **kwargs: Any) -> str | None:
        raise NotImplementedError


class NullLLMClient(LLMClient):
    """默认客户端：没配 key / 没网络 = 返回 None → 调用方回退模板。永不出网、永不抛。"""

    configured = False
    model = None

    def complete(self, prompt: str, **kwargs: Any) -> str | None:
        return None


# ----------------------------------------------------------------------------------
# 事实读取（把记录读成"渲染输入"，不做任何推断）
# ----------------------------------------------------------------------------------


class _View:
    """一条记录在某语言下的渲染输入。字段全部来自记录，缺失即 None/空。"""

    __slots__ = ("lang", "atype", "label", "status", "subject", "count", "count_text",
                 "rationale",
                 "summary", "fail_reason", "requires_action", "need_reason", "due_at",
                 "decision", "receipt", "confidence", "doubt", "impact", "risk",
                 "reversible", "tool_name", "tool_provider", "tool_version", "params",
                 "latency", "cost", "adapter", "source_fmt", "rid", "demo", "suggested",
                 "next_hint")

    def __init__(self, record: Mapping[str, Any], lang: str) -> None:
        self.lang = lang
        atype = record.get("action_type")
        self.atype = atype if atype in ACTION_TYPES else "other"
        self.label = ACTION_LABEL[self.atype][lang]
        status = record.get("status")
        self.status = status if status in STATUS_RESULT_PHRASE else "observed"

        tool = _mapping(record.get("tool_used"))
        self.tool_name = _clean(tool.get("name"))
        self.tool_provider = _clean(tool.get("provider"))
        self.tool_version = _clean(tool.get("version"))
        self.params = _clean(tool.get("params_summary"))

        impact = record.get("impact_scope")
        self.impact = impact if isinstance(impact, Mapping) else None
        self.count = _target_count(self.impact)

        self.subject = self._subject(record)
        self.count_text = (f"{self.count}" if self.count is not None else COUNT_ALL[lang])

        rationale = _clean(record.get("rationale"))
        self.rationale = rationale or None
        summary = _clean(record.get("result_summary"))
        self.summary = summary or None

        error = _mapping(record.get("error"))
        self.fail_reason = _clean(error.get("human_reason")) or None

        self.requires_action = bool(record.get("requires_action"))
        self.need_reason = _clean(record.get("requires_action_reason"))
        self.due_at = _clean(record.get("action_due_at"))

        ui = record.get("user_intervention")
        decision = _mapping(ui).get("decision") if isinstance(ui, Mapping) else None
        self.decision = decision if decision in DECISION_LABEL else None
        receipt = _mapping(record.get("execution_receipt")).get("state")
        self.receipt = receipt if receipt in RECEIPT_PHRASE else None

        conf = record.get("confidence")
        self.confidence = conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
        uncertainty = _mapping(record.get("uncertainty"))
        self.doubt = _clean(uncertainty.get("reason")) or None

        risk = record.get("risk_level")
        self.risk = risk if risk in RISK_LABEL else None
        if self.impact is not None:
            rev = self.impact.get("reversible")
            self.reversible = (IRREVERSIBLE[lang] if rev is False
                               else REVERSIBLE[lang] if rev is True
                               else UNKNOWN_REVERSIBLE[lang])
        else:
            self.reversible = None

        self.latency = _human_latency(record.get("latency_ms"))
        self.cost = _human_cost(record.get("cost"))
        source = _mapping(record.get("source"))
        self.adapter = _clean(source.get("adapter")) or _clean(source.get("kind"))
        self.source_fmt = _clean(source.get("format_version"))
        self.rid = _clean(record.get("id"))
        self.demo = bool(record.get("demo_data")) or bool(record.get("synthetic"))

        suggested = record.get("suggested_next")
        first = ""
        if isinstance(suggested, Sequence) and not isinstance(suggested, (str, bytes)):
            for item in suggested:
                text = _clean(_mapping(item).get("text"))
                if text:
                    first = text
                    break
        self.suggested = first or None
        self.next_hint = NEED_HINT_BY_ACTION[self.atype][lang]

    def _subject(self, record: Mapping[str, Any]) -> str:
        """做了什么 → 对象：只用来源给的名字/入参摘要/影响面指针，缺失就说没说。"""
        lang = self.lang
        parts: list[str] = []
        if self.tool_name:
            parts.append(self.tool_name)
        if self.params:
            parts.append(self.params if lang == "zh" else f"({self.params})")
        if not parts and self.impact is not None:
            targets = self.impact.get("targets")
            if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
                for item in targets:
                    ref = _clean(_mapping(item).get("ref"))
                    if ref:
                        parts.append(ref)
                        break
        if not parts:
            intent = _clean(record.get("intent"))
            if intent:
                parts.append(intent)
        if not parts:
            return SUBJECT_FALLBACK[lang]
        joined = " ".join(parts)
        subject = joined or NO_TARGET[lang]
        # 去重：来源 intent 常把动作名写一遍（"问你：xxx"），渲染时不再重复一遍
        for junk in (f"{self.label}：", f"{self.label}:", f"{self.label} ",):
            if subject.startswith(junk) and len(subject) > len(junk) + 2:
                subject = subject[len(junk):]
                break
        if lang == "zh" and subject.startswith("问你："):
            subject = subject[3:]
        return subject


# ----------------------------------------------------------------------------------
# 生成器
# ----------------------------------------------------------------------------------


class ExplanationGenerator:
    """解释引擎：模板版是底座（纯函数、双语、零依赖），LLM 增强是可选外挂。

    `strict=True` 时缺关键字段抛 ExplanationError（给上游做接入门禁）；
    默认 `strict=False`：缺什么就在文案里显式说"来源没说"，界面永不空白。

    模式（t5）：
      · `mode="template"`（默认）= 零 LLM、离线可用、可重放；
      · `mode="llm"` = 先按 `prompts/explain_{zh,en}.md` 组装提示词，调注入的
        `llm_client.complete(prompt, **kw)`；文案过校验才上屏，
        任何异常 / 超时 / 空返回 / 超长 / 泄漏隐私 / 假装已执行 / 要素不全 → **整句回退模板**。
      · 不注入客户端 = `NullLLMClient`（返回 None）；设置中心未 `enabled` 也回退 → 无 key 永不崩。
    """

    def __init__(self, *, lang: str = "zh", budget: int = SINGLE_SENTENCE_LIMIT,
                 strict: bool = False, show_demo_flag: bool = True,
                 mode: str = MODE_TEMPLATE, llm_client: Any = None,
                 settings: Any = None, preferences: Any = None,
                 history: Any = None, prompt_dir: Any = None) -> None:
        self.default_lang = self._check_lang(lang)
        self.budget = int(budget) if isinstance(budget, int) and budget >= 40 else SINGLE_SENTENCE_LIMIT
        self.strict = bool(strict)
        self.show_demo_flag = bool(show_demo_flag)
        self.mode = self._check_mode(mode)
        self.llm_client = self._check_client(llm_client)
        self.settings = (settings if isinstance(settings, LLMSettings)
                         else LLMSettings.from_mapping(settings))
        self.preferences = self._check_preferences(preferences)
        self.history = self._check_history(history)
        self.prompt_dir = Path(prompt_dir) if prompt_dir else None
        self._prompt_cache: dict[str, str] = {}
        self._last_llm: dict[str, Any] = {}
        self._stats = {"generated": 0, "reason_missing": 0, "result_missing": 0,
                       "needs_action": 0, "low_confidence": 0, "clipped": 0, "degraded_total": 0,
                       "llm_calls": 0, "llm_used": 0, "llm_fallback": 0, "llm_rejected": 0,
                       "prompt_missing": 0, "prompt_chars": 0}

    # ---- 基础设施 ---------------------------------------------------------------

    @staticmethod
    def _check_lang(lang: Any) -> str:
        if lang not in SUPPORTED_LANGS:
            raise ExplanationError("EXPLAIN_LANG_UNSUPPORTED",
                                   f"只支持中英双语，收到 {lang!r}", lang=str(lang),
                                   hint="lang 只能是 'zh' 或 'en'")
        return str(lang)

    def _view(self, record: Any, lang: str | None) -> _View:
        if isinstance(record, str):
            try:
                record = json.loads(record)
            except Exception as exc:                      # 不是 JSON 就报可读原因
                raise ExplanationError("EXPLAIN_RECORD_TYPE",
                                       f"记录必须是 ActionRecord 对象，收到无法解析的字符串（{exc}）",
                                       hint="先跑 trace_adapter 转成 ActionRecord 再解释") from exc
        if not isinstance(record, Mapping):
            raise ExplanationError("EXPLAIN_RECORD_TYPE",
                                   f"记录必须是 ActionRecord 对象，收到 {type(record).__name__}",
                                   hint="用 trace_adapter.convert_trace_to_action_record() 先转出记录")
        code, field = _missing_required(record)
        if code:
            if self.strict:
                raise ExplanationError(code,
                                       f"记录缺关键字段 {field}，无法生成可靠解释",
                                       field=field,
                                       hint="补齐 intent/action_type/status（schema v1.1 的 27 个必填字段）")
        self._bump(record)
        return _View(record, self._check_lang(self.default_lang if lang is None else lang))

    def _bump(self, record: Mapping[str, Any]) -> None:
        self._stats["generated"] += 1
        if not _clean(record.get("rationale")):
            self._stats["reason_missing"] += 1
            self._stats["degraded_total"] += 1
        if record.get("status") == "succeeded" and not _clean(record.get("result_summary")):
            self._stats["result_missing"] += 1
            self._stats["degraded_total"] += 1
        if record.get("requires_action"):
            self._stats["needs_action"] += 1
        conf = record.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool) and conf <= LOW_CONFIDENCE:
            self._stats["low_confidence"] += 1

    @property
    def stats(self) -> dict[str, int]:
        """K-05 / K-12 可观测：降级次数、需要干预数、低置信数、裁剪次数。"""
        return dict(self._stats)

    def reset_stats(self) -> None:
        for key in self._stats:
            self._stats[key] = 0

    # ---- 模式 / 配置 / 提示词（t5）---------------------------------------------

    @staticmethod
    def _check_mode(mode: Any) -> str:
        if mode not in MODES:
            raise ExplanationError("EXPLAIN_MODE_UNSUPPORTED",
                                   f"只支持 template / llm 双模式，收到 {mode!r}", field="mode",
                                   hint="mode='template'（离线默认）或 mode='llm'（需注入 llm_client + 设置中心 enabled）")
        return str(mode)

    @staticmethod
    def _check_client(client: Any) -> Any:
        if client is None:
            return NullLLMClient()
        if not callable(getattr(client, "complete", None)):
            raise ExplanationError("EXPLAIN_LLM_CLIENT_INVALID",
                                   f"llm_client 必须实现 complete(prompt, **kw)，收到 {type(client).__name__}",
                                   field="llm_client",
                                   hint="按 LLMClient 协议注入；留空则默认 NullLLMClient（自动回退模板）")
        return client

    def _check_preferences(self, preferences: Any) -> dict[str, Any]:
        """用户偏好：只认白名单键，非法值回落默认（设置中心写歪了也不能崩界面）。"""
        item = dict(DEFAULT_PREFERENCES)
        data = _mapping(preferences)
        for key, table in (("audience", AUDIENCE_LABEL), ("granularity", GRANULARITY_LABEL),
                           ("verbosity", VERBOSITY_LABEL), ("focus", FOCUS_LABEL)):
            if data.get(key) in table:
                item[key] = str(data[key])
        if "plain_language" in data:
            raw = data["plain_language"]
            if isinstance(raw, bool):
                item["plain_language"] = raw
            elif isinstance(raw, str):
                item["plain_language"] = raw.strip().lower() in ("true", "1", "yes", "y", "是")
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                item["plain_language"] = bool(raw)
        if data.get("language") in SUPPORTED_LANGS:
            item["language"] = str(data["language"])
        item["source"] = "settings" if data else "default"
        return item

    @staticmethod
    def _check_history(history: Any) -> list[Mapping[str, Any]]:
        items = history if isinstance(history, Sequence) and not isinstance(history, (str, bytes)) else []
        return [rec for rec in items if isinstance(rec, Mapping)][:HISTORY_MAX]

    def set_mode(self, mode: str) -> "ExplanationGenerator":
        self.mode = self._check_mode(mode)
        return self

    def set_preferences(self, preferences: Any) -> "ExplanationGenerator":
        self.preferences = self._check_preferences(preferences)
        return self

    def set_history(self, history: Any) -> "ExplanationGenerator":
        self.history = self._check_history(history)
        return self

    def llm_status(self) -> dict[str, Any]:
        """设置页 / 审计用：一眼看清"现在到底会不会调用模型"。"""
        return {"mode": self.mode,
                "settings_enabled": self.settings.enabled,
                "client_configured": bool(getattr(self.llm_client, "configured", True)),
                "client_type": type(self.llm_client).__name__,
                "model": self.settings.model or getattr(self.llm_client, "model", None),
                "api_key_present": self.settings.api_key_present,
                "history_count": len(self.history),
                "preferences": dict(self.preferences)}

    def _llm_gate(self, mode: str) -> str | None:
        """回退原因；None = 允许调用模型。三道闸：模式 → 设置中心 → 客户端已配置。"""
        if mode != MODE_LLM:
            return "mode_template"
        if not self.settings.enabled:
            return "settings_disabled"
        if not bool(getattr(self.llm_client, "configured", True)):
            return "client_not_configured"
        return None

    def _load_prompt(self, lang: str) -> str:
        cached = self._prompt_cache.get(lang)
        if cached:
            return cached
        directory = self.prompt_dir or Path(__file__).with_name(PROMPT_SUBDIR)
        try:
            text = (Path(directory) / PROMPT_FILES[lang]).read_text(encoding="utf-8")
            if not text.strip():
                raise OSError("empty prompt file")
        except OSError:
            self._stats["prompt_missing"] += 1
            text = PROMPT_FALLBACK[lang]
        self._prompt_cache[lang] = text
        return text

    def _history_lines(self, history: Sequence[Mapping[str, Any]], lang: str) -> list[str]:
        lines: list[str] = []
        for index, rec in enumerate(history[:HISTORY_MAX], start=1):
            try:
                past = _View(rec, lang)
            except Exception:                             # 历史里有坏记录 → 跳过，不影响当前解释
                continue
            line = f"{index}. {past.label}｜{self.l1(rec, lang)}"
            lines.append(_clip(line, HISTORY_LINE_LIMIT))
        return lines

    def _facts_block(self, record: Any, v: _View, prefs: Mapping[str, Any],
                     history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """提示词里唯一的"事实源"：全部取自记录、全部已脱敏，缺失就是 null。"""
        rec = _as_mapping(record)
        context = _mapping(rec.get("context"))
        facts: dict[str, Any] = {
            "record_id": v.rid or None,
            "language": v.lang,
            "max_length": self.budget,
            "task_name": _clean(context.get("task_name")) or None,
            "agent_name": _clean(rec.get("agent_name")) or None,
            "audience": prefs.get("audience"),
            "granularity": prefs.get("granularity"),
            "verbosity": prefs.get("verbosity"),
            "plain_language": bool(prefs.get("plain_language")),
            "focus": prefs.get("focus"),
            "action_type": v.atype,
            "action_label": v.label,
            "status": v.status,
            "subject": v.subject,
            "items_affected": v.count,
            "intent": _clean(rec.get("intent")) or None,
            "rationale": v.rationale,
            "result_summary": v.summary,
            "failure_reason": v.fail_reason,
            "requires_action": v.requires_action,
            "requires_action_reason": v.need_reason or None,
            "action_due_at": v.due_at or None,
            "decision": v.decision,
            "receipt_state": v.receipt,
            "risk_level": v.risk,
            "impact_reversible": (v.impact.get("reversible") if isinstance(v.impact, Mapping) else None),
            "tool": v.tool_name or None,
            "tool_params": v.params or None,
            "latency": v.latency or None,
            "cost": v.cost or None,
            "confidence": v.confidence,
            "uncertainty": v.doubt,
            "suggested_next": v.suggested or None,
            "demo_data": v.demo,
            "synthetic": bool(rec.get("synthetic")),
            "facts_hash": _facts_hash(rec),
            "history": [{"action_type": item.atype, "label": item.label, "l1": line.split("｜", 1)[-1]}
                        for item, line in self._history_pairs(history, v.lang)],
        }
        return facts

    def _history_pairs(self, history: Sequence[Mapping[str, Any]], lang: str):
        out = []
        for rec in history[:HISTORY_MAX]:
            try:
                past = _View(rec, lang)
            except Exception:
                continue
            out.append((past, f"{past.label}｜{self.l1(rec, lang)}"))
        return out

    def build_prompt(self, record: Any, lang: str | None = None, *,
                     history: Any = None, preferences: Any = None) -> str:
        """组装提示词（t13 客户端、评测脚本、审计都从这里取同一份文本）。"""
        v = self._view(record, lang)
        filled, _meta = self._build_prompt(record, v,
                                           self._check_history(history) if history is not None else self.history,
                                           self._check_preferences(preferences) if preferences is not None else self.preferences)
        return filled

    def _build_prompt(self, record: Any, v: _View, history: Sequence[Mapping[str, Any]],
                      prefs: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        rec = _as_mapping(record)
        context = _mapping(rec.get("context"))
        lang = v.lang
        audience = prefs["audience"]
        granularity = prefs["granularity"]
        if prefs.get("source") != "settings":             # 没显式设过偏好 → 用记录里的画像
            audience = context.get("audience_profile") if context.get("audience_profile") in AUDIENCE_LABEL else audience
            granularity = (context.get("default_granularity") if context.get("default_granularity")
                           in GRANULARITY_LABEL else granularity)
        pairs = self._history_pairs(history, lang)
        facts = self._facts_block(record, v, prefs, history)
        facts_json = json.dumps(facts, ensure_ascii=False, separators=(",", ":"), default=str)
        for slim_key in ("history", "tool_params", "suggested_next", "uncertainty"):
            if len(facts_json) <= FACTS_JSON_LIMIT:
                break
            facts[slim_key] = None
            facts_json = json.dumps(facts, ensure_ascii=False, separators=(",", ":"), default=str)
        facts_json = _clip(facts_json, FACTS_JSON_LIMIT)
        impact_text = (IMPACT_STEP[lang].format(count=v.count_text, reversible=v.reversible)
                       if v.impact is not None else NO_IMPACT[lang])
        tool_text = " / ".join(part for part in (v.tool_name, v.tool_provider, v.tool_version) if part) or MISSING_MARK[lang]
        values: dict[str, str] = {
            "max_length": str(self.budget),
            "language": OUTPUT_LANGUAGE[lang],
            "output_language": OUTPUT_LANGUAGE[lang],
            "lang_code": lang,
            "task_name": _clean(context.get("task_name")) or NO_TASK[lang],
            "agent_name": _clean(rec.get("agent_name")) or NO_AGENT[lang],
            "session_id": _clean(rec.get("session_id")) or "-",
            "record_id": v.rid or "-",
            "audience_label": AUDIENCE_LABEL.get(audience, AUDIENCE_LABEL["A"])[lang],
            "granularity": GRANULARITY_LABEL.get(granularity, GRANULARITY_LABEL["L1"])[lang],
            "verbosity_label": VERBOSITY_LABEL.get(prefs.get("verbosity"), VERBOSITY_LABEL["standard"])[lang],
            "plain_language": YES[lang] if prefs.get("plain_language") else NO[lang],
            "focus_label": FOCUS_LABEL.get(prefs.get("focus"), FOCUS_LABEL["progress"])[lang],
            "action_label": v.label,
            "action_type": v.atype,
            "status_label": STATUS_L1_LABEL[v.status][lang],
            "subject": v.subject,
            "count": v.count_text,
            "intent": _clean(rec.get("intent")) or MISSING_MARK[lang],
            "rationale": v.rationale or MISSING_MARK[lang],
            "result_summary": v.summary or MISSING_MARK[lang],
            "failure_reason": v.fail_reason or MISSING_MARK[lang],
            "requires_action": "true" if v.requires_action else "false",
            "need_reason": v.need_reason or MISSING_MARK[lang],
            "due_at": _human_due(v.due_at) if v.due_at else NO_DEADLINE[lang],
            "decision": DECISION_LABEL[v.decision][lang] if v.decision else NO_DECISION[lang],
            "receipt": RECEIPT_SHORT[v.receipt][lang] if v.receipt else NO_RECEIPT[lang],
            "receipt_state": v.receipt or "unreported",
            "risk": RISK_LABEL[v.risk][lang] if v.risk else MISSING_MARK[lang],
            "impact": impact_text,
            "tool": tool_text,
            "params": v.params or MISSING_MARK[lang],
            "latency": v.latency or MISSING_MARK[lang],
            "cost": v.cost or MISSING_MARK[lang],
            "confidence": f"{v.confidence}" if v.confidence is not None else MISSING_MARK[lang],
            "doubt": v.doubt or MISSING_MARK[lang],
            "suggested": v.suggested or MISSING_MARK[lang],
            "history": "\n".join(f"{i}. {line}" for i, (_p, line) in enumerate(pairs, start=1)) or NO_HISTORY[lang],
            "history_count": str(len(pairs)),
            "demo_data": "true" if v.demo else "false",
            "facts_json": facts_json,
        }
        filled, unknown = _fill_template(self._load_prompt(lang), values)
        meta = {"prompt_chars": len(filled), "unknown_tokens": unknown,
                "template_id": PROMPT_TEMPLATE_ID, "facts": facts,
                "facts_hash": facts["facts_hash"], "history_count": len(pairs)}
        return filled, meta

    # ---- LLM 调用与回退（一切失败都回退模板）-----------------------------------

    def _llm_meta(self, v: _View, *, used: bool, reason: str | None,
                  text: str | None = None, prompt_chars: int = 0) -> dict[str, Any]:
        return {"mode": self.mode, "used": bool(used), "reason": reason,
                "model": self.settings.model or getattr(self.llm_client, "model", None),
                "prompt_chars": int(prompt_chars), "chars": len(text) if text else 0,
                "language": v.lang}

    def _llm_fallback(self, v: _View, reason: str, template_sentence: str,
                      *, prompt_chars: int = 0) -> str:
        self._stats["llm_fallback"] += 1
        quiet = {"mode_template", "settings_disabled", "client_not_configured",
                 "client_returned_none", "empty"}
        if reason not in quiet:
            self._stats["llm_rejected"] += 1
        self._last_llm = self._llm_meta(v, used=False, reason=reason, prompt_chars=prompt_chars)
        return template_sentence

    def _validate_llm_text(self, text: str, v: _View) -> tuple[bool, str | None]:
        """上屏门禁：长度、隐私、决定≠生效、四要素 —— 任何一条不过 = 整句回退。"""
        if len(text) > self.budget:
            return False, "over_length"
        for pattern, label in LLM_FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                return False, f"privacy:{label}"
        if v.receipt not in OVERCLAIM_OK_RECEIPTS:
            probe = text if v.lang == "zh" else text.lower()
            for word in OVERCLAIM_WORDS[v.lang]:
                needle = word if v.lang == "zh" else word.lower()
                if needle in probe:
                    return False, f"overclaim:{word}"
        if self._llm_signals(text, v) < 3:
            return False, "incomplete"
        return True, None

    @staticmethod
    def _llm_signals(text: str, v: _View) -> int:
        why, result, need = SIGNAL_PATTERNS[v.lang]
        score = sum(1 for pattern in (why, result, need) if re.search(pattern, text))
        head = (v.subject.split(" ")[0] if v.lang == "en" else v.subject[:4]).strip()
        what_ok = bool(head) and (v.label in text or head in text or v.atype in text)
        return score + (1 if what_ok else 0)

    def _llm_generate(self, record: Any, v: _View, *, mode: str,
                      history: Sequence[Mapping[str, Any]]) -> str:
        template_sentence = self._generate_template(v)     # 兜底文案：同一套模板，绝不留空
        gate = self._llm_gate(mode)
        if gate:
            return self._llm_fallback(v, gate, template_sentence)
        prompt, meta = self._build_prompt(record, v, history, self.preferences)
        self._stats["prompt_chars"] = meta["prompt_chars"]
        self._stats["llm_calls"] += 1
        kwargs = self.settings.call_kwargs()
        kwargs.update({"language": v.lang, "max_length": self.budget,
                       "output_language": OUTPUT_LANGUAGE[v.lang],
                       "record_id": v.rid, "facts_hash": meta["facts_hash"],
                       "history_count": meta["history_count"],
                       "prompt_template_id": meta["template_id"],
                       "response_format": "plain_text", "stop": ["\n"]})
        try:
            raw = self.llm_client.complete(prompt, **kwargs)
        except Exception as exc:                          # 超时 / 网络 / 解析：一律回退
            reason = "timeout" if isinstance(exc, TimeoutError) else f"client_error:{type(exc).__name__}"
            return self._llm_fallback(v, reason, template_sentence, prompt_chars=meta["prompt_chars"])
        if raw is None:
            return self._llm_fallback(v, "client_returned_none", template_sentence,
                                      prompt_chars=meta["prompt_chars"])
        if not isinstance(raw, str):
            return self._llm_fallback(v, f"client_returned_type:{type(raw).__name__}",
                                      template_sentence, prompt_chars=meta["prompt_chars"])
        candidate = raw.strip().strip('"').strip("「」").strip()
        if not candidate:
            return self._llm_fallback(v, "empty", template_sentence, prompt_chars=meta["prompt_chars"])
        # 先校验**未脱敏原文**：泄漏与假闭环必须在脱敏之前被拦下，不能靠脱敏蒙混上屏
        ok, code = self._validate_llm_text(candidate, v)
        if not ok:
            return self._llm_fallback(v, code, template_sentence, prompt_chars=meta["prompt_chars"])
        text = _clean(candidate)
        if not text:
            return self._llm_fallback(v, "empty", template_sentence, prompt_chars=meta["prompt_chars"])
        if len(text) > self.budget:                       # 数学上不该发生；双保险
            self._stats["clipped"] += 1
            text = _clip(text, self.budget)
        self._last_llm = self._llm_meta(v, used=True, reason=None, text=text,
                                        prompt_chars=meta["prompt_chars"])
        self._stats["llm_used"] += 1
        return text

    # ---- 四要素分段 -------------------------------------------------------------

    def _what(self, v: _View) -> str:
        template = WHAT_PHRASE[v.atype][v.lang]
        return _clean(template.format(label=v.label, subject=v.subject, count=v.count_text))

    def _why(self, v: _View) -> tuple[str, str, bool, int]:
        """(前缀, 值, 可裁剪, 保底长度)：缺失 → 固定降级短句（含"因为"，不可裁）。"""
        if v.rationale:
            prefix = WHY_PREFIX[v.lang]
            return prefix, v.rationale, True, len(prefix) + 4
        return "", WHY_MISSING[v.lang], False, len(WHY_MISSING[v.lang])

    def _result(self, v: _View) -> tuple[str, str, bool, int]:
        """(前缀, 值, 可裁剪, 保底长度)。值含来源事实时可裁；纯状态短句不可裁。"""
        lang = v.lang
        phrase = STATUS_RESULT_PHRASE[v.status][lang]
        if v.status == "succeeded":
            if not v.summary:
                return "", RESULT_MISSING[lang], False, len(RESULT_MISSING[lang])
            text = _clean(phrase.format(summary=v.summary, reason=""))
            return "", text, True, len(RESULT_PREFIX[lang]) + 4
        if v.status == "failed":
            if v.fail_reason:
                text = _clean(phrase.format(summary="", reason=v.fail_reason))
                skeleton = _clean(phrase.format(summary="", reason=""))
                return "", text, True, len(skeleton) + 4
            # 来源没写失败原因 → 整句是固定降级文案，不参与裁剪
            text = _clean(phrase.format(summary="", reason=FAIL_REASON_MISSING[lang]))
            return "", text, False, len(text)
        if v.status in ("approved", "modified", "rejected"):
            # 决定 + 回执都是模板词（无自由事实）→ 整段不可裁，保证"决定≠生效"讲全
            decision = v.decision if v.decision in ("approve", "modify", "reject") else "approve"
            receipt = v.receipt if v.receipt in RECEIPT_SHORT else "unreported"
            text = _clean(phrase.format(decision=DECISION_SHORT[decision][lang],
                                        receipt=RECEIPT_SHORT[receipt][lang]))
            return "", text, False, len(text)
        text = _clean(phrase.format(summary="", reason=""))
        return "", text, False, len(text)

    def _need(self, v: _View) -> tuple[str, str, bool, int]:
        lang = v.lang
        if v.requires_action:
            prefix = NEED_PREFIX[lang]
            reason = v.need_reason or v.next_hint
            # 截止/超时信息放进受保护前缀：正文再长也不会把它挤掉
            if v.status == "expired":
                prefix = f"{prefix}{EXPIRED_HEAD[lang]}"
            elif v.due_at:
                due = _human_due(v.due_at)
                prefix = (f"{prefix}（{due} 前）" if lang == "zh" else f"{prefix}(by {due}) ")
            return prefix, _clean(reason), True, len(prefix) + 4
        if v.status == "pending_confirmation":
            return "", NEED_SHORT[lang], False, len(NEED_SHORT[lang])
        if v.status == "failed" and v.suggested:
            text = _clean(SUGGEST_PHRASE[lang].format(next=v.suggested))
            skeleton = _clean(SUGGEST_PHRASE[lang].format(next=""))
            return "", text, True, len(skeleton) + 4
        if v.decision is not None:
            return "", NEED_DECIDED[lang], False, len(NEED_DECIDED[lang])
        return "", NEED_NONE[lang], False, len(NEED_NONE[lang])

    # ---- 公开渲染 ---------------------------------------------------------------

    def generate(self, record: Any, lang: str | None = None, *,
                 mode: str | None = None, history: Any = None) -> str:
        """四要素单句（≤120 字符）。

        `mode="template"`（默认）零依赖、可重放；`mode="llm"` 走注入客户端，
        任何异常 / 超时 / 空返回 / 超长 / 泄漏 / 假装已执行 / 要素不全 → **整句回退模板**，
        界面永远拿到一句能读的话，不会白屏、不会崩。
        """
        v = self._view(record, lang)
        effective = self._check_mode(mode) if mode is not None else self.mode
        if effective == MODE_LLM:
            hist = self._check_history(history) if history is not None else self.history
            return self._llm_generate(record, v, mode=effective, history=hist)
        self._last_llm = self._llm_meta(v, used=False, reason="mode_template")
        return self._generate_template(v)

    def _generate_template(self, v: _View) -> str:
        """模板版正文：长度由预算算法保证，不靠运气。"""
        head = ""
        if self.show_demo_flag and v.demo:
            head += DEMO_FLAG[v.lang]
        if v.confidence is not None and v.confidence <= LOW_CONFIDENCE:
            head += DOUBT_FLAG[v.lang]

        what_text = self._what(v)
        what_floor = max(len(v.label) + 4, len(what_text) - len(v.subject))
        segments = [("what", "", what_text, True, what_floor),
                    ("why",) + self._why(v),
                    ("result",) + self._result(v),
                    ("need",) + self._need(v)]
        fixed = {name: len(prefix_text + value)
                 for name, prefix_text, value, flexible, _floor in segments if not flexible}
        floors = {name: max(floor, len(prefix_text) + 1)
                  for name, prefix_text, _value, flexible, floor in segments if flexible}
        caps = _caps(v.lang, head, self.budget, fixed,
                     [name for name, _, _, flexible, _f in segments if flexible], floors)
        segs = [_seg(prefix_text, value, caps[name]) if value
                else _clip(prefix_text, caps[name])
                for name, prefix_text, value, _flexible, _floor in segments]
        full = head + JOINER[v.lang].join(s for s in segs if s)
        if len(full) > self.budget:                       # 数学上不该发生；兜底并计数
            self._stats["clipped"] += 1
            full = _clip(full, self.budget)
        return full

    def explain(self, record: Any, lang: str | None = None, *,
                mode: str | None = None, history: Any = None) -> dict[str, Any]:
        """四要素分字段（前端逐条渲染 / 测试逐条断言）。每段 ≤120 字符。

        额外给出 provenance：`generated_by`（template / hybrid）与 `llm`
        （mode / used / reason / model / prompt_chars / chars）—— 界面据此显示
        「AI 生成」标记与"为什么回退了"。
        """
        v = self._view(record, lang)
        parts = {
            "what": _clip(self._what(v), self.budget),
            "why": _clip((WHY_PREFIX[v.lang] + v.rationale) if v.rationale else WHY_MISSING[v.lang],
                         self.budget),
            "result": _clip(_join_pair(self._result(v)), self.budget),
            "need": _clip(_join_pair(self._need(v)), self.budget),
        }
        out: dict[str, Any] = dict(parts)
        out["language"] = v.lang
        out["action_type"] = v.atype
        out["status"] = v.status
        out["requires_action"] = v.requires_action
        out["needs_you"] = v.requires_action or v.status == "pending_confirmation"
        out["low_confidence"] = v.confidence is not None and v.confidence <= LOW_CONFIDENCE
        out["demo_data"] = v.demo
        out["flags"] = [flag for flag, on in (
            ("demo_data", v.demo),
            ("low_confidence", out["low_confidence"]),
            ("reason_missing", v.rationale is None),
            ("result_missing", v.status == "succeeded" and v.summary is None),
            ("decision_only", v.decision is not None),
            ("failed", v.status == "failed"),
        ) if on]
        effective = self._check_mode(mode) if mode is not None else self.mode
        out["sentence"] = self.generate(record, v.lang, mode=mode, history=history)
        used = bool(self._last_llm.get("used"))
        out["generated_by"] = "hybrid" if used else "template"
        out["llm"] = dict(self._last_llm)
        if effective == MODE_LLM:
            out["flags"].append("llm_used" if used else "llm_fallback")
        return out

    def l1(self, record: Any, lang: str | None = None) -> str:
        """L1 结果级：结论 + 现状 + 需要你做什么（zh ≤40 字 / en ≤80 字符）。"""
        v = self._view(record, lang)
        lang_id = v.lang
        status = STATUS_L1_LABEL[v.status][lang_id]
        need = ""
        needs_you = v.requires_action or v.status == "pending_confirmation"
        if needs_you and "等" not in status and "awaiting" not in status:
            need = "｜等你" if lang_id == "zh" else " | needs you"
        if v.confidence is not None and v.confidence <= LOW_CONFIDENCE:
            need += "？" if lang_id == "zh" else "?"
        subject_room = L1_LIMIT[lang_id] - len(v.label) - len(status) - len(need) - 6
        subject = _clip(v.subject, max(4, subject_room))
        if lang_id == "zh":
            return _clip(f"{v.label} {subject}｜{status}{need}", L1_LIMIT["zh"])
        return _clip(f"{v.label}: {subject} ({status}){need}", L1_LIMIT["en"])

    def l2(self, record: Any, lang: str | None = None) -> list[str]:
        """L2 步骤级：3–7 条，每条一句话（≤30 字符），按重要性排序后截断。"""
        v = self._view(record, lang)
        lang_id = v.lang
        steps: list[str] = []
        steps.append(_clip(self._what(v), L2_STEP_LIMIT))
        steps.append(_clip((WHY_PREFIX[lang_id] + v.rationale) if v.rationale else WHY_MISSING[lang_id],
                           L2_STEP_LIMIT))
        steps.append(_clip(_join_pair(self._result(v)), L2_STEP_LIMIT))
        if v.requires_action:
            steps.append(_clip(NEED_STEP[lang_id], L2_STEP_LIMIT))
        if v.decision is not None:
            steps.append(_clip(DECIDED_STEP[lang_id].format(
                decision=DECISION_LABEL[v.decision][lang_id],
                receipt=RECEIPT_PHRASE[v.receipt or "unreported"][lang_id]), L2_STEP_LIMIT))
        if v.impact is not None:
            text = _clip(IMPACT_STEP[lang_id].format(count=v.count_text, reversible=v.reversible),
                         L2_STEP_LIMIT)
            if v.risk in RISK_LABEL:
                risk = RISK_LABEL[v.risk][lang_id]
                if len(text) + len(IMPACT_RISK_JOIN[lang_id]) + len(risk) <= L2_STEP_LIMIT:
                    text = text + IMPACT_RISK_JOIN[lang_id] + risk      # 装得下才说风险
            steps.append(text)
        lat = "" if v.latency in ("", "0ms") else v.latency        # 0ms 不进 L2（噪音）
        if v.cost and lat:
            steps.append(_clip(COST_LATENCY_STEP[lang_id].format(latency=lat, cost=v.cost),
                               L2_STEP_LIMIT))
        elif lat:
            steps.append(_clip(LATENCY_STEP[lang_id].format(latency=lat), L2_STEP_LIMIT))
        elif v.cost:
            steps.append(_clip(COST_STEP[lang_id].format(cost=v.cost), L2_STEP_LIMIT))
        if v.confidence is not None and v.confidence <= LOW_CONFIDENCE:
            steps.append(_clip(LOW_CONF_PHRASE[lang_id].format(reason=v.doubt or v.confidence),
                               L2_STEP_LIMIT))
        if v.doubt and not (v.confidence is not None and v.confidence <= LOW_CONFIDENCE):
            steps.append(_clip(LOW_CONF_PHRASE[lang_id].format(reason=v.doubt), L2_STEP_LIMIT))

        steps = [s for s in steps if s]
        padding: list[str] = []
        if v.demo:
            padding.append(DEMO_STEP[lang_id])
        padding.append(TOOL_STEP[lang_id].format(tool=v.tool_name) if v.tool_name
                       else ID_STEP[lang_id].format(rid=v.rid))
        if v.adapter:
            padding.append(SOURCE_STEP[lang_id].format(adapter=v.adapter, fmt=v.source_fmt or "?"))
        padding.append(ID_STEP[lang_id].format(rid=v.rid))
        for extra in padding:
            if len(steps) >= L2_MIN_STEPS:
                break
            text = _clip(extra, L2_STEP_LIMIT)
            if text and text not in steps:
                steps.append(text)
        return steps[:L2_MAX_STEPS]

    def l3(self, record: Any, lang: str | None = None) -> str:
        """L3 参数级：工具/入参/耗时/成本/依据（≤150 字符，明细走 detail 展开）。"""
        v = self._view(record, lang)
        keys = L3_FIELDS[v.lang]
        bits: list[str] = []
        tool_text = v.tool_name or ("-" if v.lang == "en" else "无")
        if v.tool_provider:
            tool_text = f"{tool_text}@{v.tool_provider}"
        if v.tool_version:
            tool_text = f"{tool_text}#{v.tool_version}"
        bits.append(f"{keys[0]} {tool_text}")
        bits.append(f"{keys[1]} {v.params or ('-' if v.lang == 'en' else '无')}")
        bits.append(f"{keys[2]} {v.latency or ('-' if v.lang == 'en' else '无')}")
        bits.append(f"{keys[3]} {v.cost or ('-' if v.lang == 'en' else '无')}")
        bits.append(f"{keys[4]} " + (DECISION_LABEL[v.decision][v.lang] if v.decision
                                     else ("none" if v.lang == "en" else "未记录")))
        bits.append(f"{keys[5]} " + (RECEIPT_SHORT[v.receipt][v.lang] if v.receipt
                                     else ("-" if v.lang == "en" else "无")))
        bits.append(f"{keys[6]} {v.adapter or ('-' if v.lang == 'en' else '无')}")
        bits.append(f"{keys[7]} {v.rid or ('-' if v.lang == 'en' else '无')}")
        return _clip(L3_JOINER.join(bits), L3_LIMIT)

    def describe_options(self, record: Any, lang: str | None = None) -> list[dict[str, Any]]:
        """三选项文案按语言取（共享字典 = 与 i18n 同一口径），字段/参数沿用记录本身。"""
        v = self._view(record, lang)
        source_record = _as_mapping(record)
        options = source_record.get("intervention_options")
        out: list[dict[str, Any]] = []
        if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
            for opt in options:
                item = _mapping(opt)
                kind = item.get("kind")
                text = OPTION_TEMPLATE.get(str(kind), {}).get(v.lang)
                label = _clean(text["label"] if text else item.get("label"))
                consequence = _clean(text["consequence"] if text else item.get("consequence"))
                out.append({"id": item.get("id"), "kind": kind, "label": label,
                            "consequence": consequence,
                            "editable_fields": copy.deepcopy(item.get("editable_fields") or [])})
        if not out and (v.requires_action or v.status == "pending_confirmation"):
            for kind in ("approve", "modify", "reject"):
                text = OPTION_TEMPLATE[kind][v.lang]
                out.append({"id": f"{kind}-{v.rid}", "kind": kind,
                            "label": _clean(text["label"]),
                            "consequence": _clean(text["consequence"]),
                            "editable_fields": []})
        return out

    def render_human_readable(self, record: Any, lang: str | None = None, *,
                              generated_by: str = "template",
                              model: str | None = None,
                              now: _dt.datetime | None = None) -> dict[str, Any]:
        """生成完整 human_readable（schema v1.1 字段封闭 + 事实指纹可复算）。

        出处口径（t5）：`l1 / l2 / l3` **恒为模板产物**（可 lint、可重放、离线可用）；
        LLM 只替换"一句话正文"（`generate()` / `explain()["sentence"]`）。
        所以 `generated_by="auto"` 在 LLM 文案被采纳时记 `hybrid`（并带 model），
        `may_contain_inference` 随之为 true → 界面必须显示「AI 生成」标记；
        回退时记 `template`，`model=null`、`may_contain_inference=false`。
        """
        v = self._view(record, lang)
        if generated_by == "auto":
            self.generate(record, v.lang)                  # 触发一次（llm 模式才会真调用）
            generated_by = "hybrid" if self._last_llm.get("used") else "template"
            if generated_by == "hybrid" and not model:
                model = self._last_llm.get("model")
        if generated_by not in ("template", "llm", "hybrid", "human"):
            raise ExplanationError("EXPLAIN_GENERATED_BY_INVALID",
                                   f"generated_by 非法：{generated_by!r}", field="generated_by",
                                   hint="只能是 template/llm/hybrid/human")
        if not isinstance(record, Mapping):
            raise ExplanationError("EXPLAIN_RECORD_TYPE", "记录必须是对象")
        stamp = now if isinstance(now, _dt.datetime) else _dt.datetime.now(_dt.timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
        inferred = generated_by in ("llm", "hybrid")
        return {
            "l1": self.l1(record, v.lang),
            "l2": self.l2(record, v.lang),
            "l3": self.l3(record, v.lang),
            "language": v.lang,
            "generated_by": generated_by,
            "template_id": (None if generated_by == "llm" and model
                            else f"{SENTENCE_TEMPLATE_ID}+{L1_TEMPLATE_ID}+{L2_TEMPLATE_ID}+{L3_TEMPLATE_ID}"),
            "model": model,
            "generated_at": _iso(stamp),
            "source_facts_hash": _facts_hash(record),
            "may_contain_inference": bool(inferred),
        }

    def enrich(self, records: Any, lang: str | None = None, *,
               generated_by: str = "template", model: str | None = None,
               now: _dt.datetime | None = None) -> list[dict[str, Any]]:
        """返回新记录列表（deep copy，只替换 human_readable）；原记录不动。"""
        items = records if isinstance(records, Sequence) and not isinstance(records, (str, bytes)) else [records]
        out: list[dict[str, Any]] = []
        for rec in items:
            if not isinstance(rec, Mapping):
                raise ExplanationError("EXPLAIN_RECORD_TYPE",
                                       f"记录必须是对象，收到 {type(rec).__name__}")
            clone = copy.deepcopy(dict(rec))
            clone["human_readable"] = self.render_human_readable(
                rec, lang, generated_by=generated_by, model=model, now=now)
            out.append(clone)
        return out

    def bilingual(self, record: Any, *, now: _dt.datetime | None = None) -> dict[str, Any]:
        """一次给出中英两版（界面切语言 = 取对应语种，不做机器翻译）。

        llm 模式下**每个语种各调一次**模型（提示词分别是中/英模板，不是一边生成再翻译），
        并且每语种只调一次：provenance 直接取那次调用的结果，不做二次调用烧 token。
        """
        out: dict[str, Any] = {}
        for lang in SUPPORTED_LANGS:
            elements = self.explain(record, lang)          # 内部调一次 generate()（llm 模式才真调用）
            used = bool(elements["llm"].get("used"))
            gen_by = ("hybrid" if used else "template") if self.mode == MODE_LLM else "template"
            model = elements["llm"].get("model") if gen_by == "hybrid" else None
            out[lang] = {"sentence": elements["sentence"],
                         "l1": self.l1(record, lang),
                         "l2": self.l2(record, lang),
                         "l3": self.l3(record, lang),
                         "elements": elements,
                         "options": self.describe_options(record, lang),
                         "human_readable": self.render_human_readable(record, lang,
                                                                      generated_by=gen_by,
                                                                      model=model, now=now)}
        return out

    def generate_batch(self, records: Any, lang: str | None = None) -> list[str]:
        return [self.generate(rec, lang) for rec in
                (records if isinstance(records, Sequence) and not isinstance(records, (str, bytes)) else [records])]


def _as_mapping(record: Any) -> Mapping[str, Any]:
    """容忍 JSON 字符串入参（界面侧常把记录当文本传来）；解析不了就当空对象。"""
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except Exception:
            return {}
    return record if isinstance(record, Mapping) else {}


def _join_pair(item: tuple[str, str, bool, int]) -> str:
    prefix, value, _flexible, _floor = item
    return (prefix + value) if value else prefix


def _missing_required(record: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """接入门禁：缺这三样就讲不出四要素，strict 模式直接报错。"""
    if not _clean(record.get("intent")):
        return "EXPLAIN_FACTS_MISSING", "intent"
    if record.get("action_type") not in ACTION_TYPES:
        return "EXPLAIN_FACTS_MISSING", "action_type"
    if record.get("status") not in STATUS_RESULT_PHRASE:
        return "EXPLAIN_FACTS_MISSING", "status"
    return None, None


# ----------------------------------------------------------------------------------
# 模块级快捷函数（默认单例，界面侧一行调用）
# ----------------------------------------------------------------------------------

_DEFAULT = ExplanationGenerator()


def default_generator() -> ExplanationGenerator:
    return _DEFAULT


def generate(record: Any, lang: str = "zh", **kwargs: Any) -> str:
    return _DEFAULT.generate(record, lang, **kwargs)


def explain(record: Any, lang: str = "zh", **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.explain(record, lang, **kwargs)


def l1(record: Any, lang: str = "zh") -> str:
    return _DEFAULT.l1(record, lang)


def l2(record: Any, lang: str = "zh") -> list[str]:
    return _DEFAULT.l2(record, lang)


def l3(record: Any, lang: str = "zh") -> str:
    return _DEFAULT.l3(record, lang)


def describe_options(record: Any, lang: str = "zh") -> list[dict[str, Any]]:
    return _DEFAULT.describe_options(record, lang)


def render_human_readable(record: Any, lang: str = "zh", **kwargs: Any) -> dict[str, Any]:
    return ExplanationGenerator().render_human_readable(record, lang, **kwargs)


def enrich(records: Any, lang: str = "zh", **kwargs: Any) -> list[dict[str, Any]]:
    return ExplanationGenerator().enrich(records, lang, **kwargs)


def build_prompt(record: Any, lang: str = "zh", **kwargs: Any) -> str:
    """提示词来自资源文件 `prompts/explain_{zh,en}.md`；评测脚本与 t13 客户端取同一份文本。"""
    return _DEFAULT.build_prompt(record, lang, **kwargs)


def llm_status() -> dict[str, Any]:
    """当前默认单例的 LLM 开关状态（设置页 / 审计用）。"""
    return _DEFAULT.llm_status()


def make_generator(**kwargs: Any) -> ExplanationGenerator:
    """设置中心 / 评测脚本入口：`make_generator(mode="llm", llm_client=..., settings={...})`。"""
    return ExplanationGenerator(**kwargs)
