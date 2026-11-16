r"""trace_adapter —— 外部 trace 格式 → ActionRecord（t3，纯标准库、零网络）。

## 这个模块解决什么

产品定位是**只读接入 + 翻译层**（`docs/01-product-and-users.md`）：不采集、不改写源系统，
只把已有 trace 翻译成普通人能看懂的行动清单。本模块是接入层的第一环：

    Langfuse trace/observation ┐
    OTel GenAI / OTLP JSON     ├─→ convert_trace_to_action_record() ─→ list[ActionRecord]
    MCP 事件流（JSON-RPC）      │                                     （schema v1.1 校验通过）
    NativeActionList（原生）    ┘

## 支持的 4 种输入（`SUPPORTED_FORMATS`）

| 格式名 | 规范依据（写入 `source.format_version`） | 输入形态 |
|---|---|---|
| `langfuse` | Langfuse 公开 API v1（OpenAPI 1.0.0，含 Ingestion API batch 信封） | 导出的 trace（内嵌 `observations`）/ observation 列表 / `{"batch":[...]}` |
| `otel_genai` | OTLP JSON v1.3 + GenAI 语义约定 1.30.0（`gen_ai.*`） | `resourceSpans → scopeSpans → spans` 树 |
| `mcp_events` | Model Context Protocol 2025-06-18（JSON-RPC 2.0） | 事件流：JSON-RPC 消息数组（可带 `ts/direction/session_id` 信封） |
| `native` | ActionRecord schema 1.1 | ActionRecord 数组，或 `{"records":[...]}` |

格式自动识别见 `detect()`；识别不出 → `TraceFormatError(code="TRACE_FORMAT_UNKNOWN")`，
消息里说明"看到了哪些键 / 为什么判不出"，禁止静默当成某一种格式硬解。

## 三条不可违反的工程纪律

1. **禁止编造事实**（护栏 P3）。来源没给原因 → `rationale=null`；只有 token 数、没有金额 →
   `cost=null`；没有结果内容 → `result_summary` 只写"来源未提供结果内容"。
   翻译层（t4）才有资格做人话组织，本模块只搬事实 + 一句模板 L1。
2. **未知字段不丢，但不参与翻译**（W-10）。每个来源对象里"规范未收录 / 本适配器不消费"的字段
   收进 `raw_payloads[raw_ref]`，记录里只留指针 `source.raw_ref = "local://raw/<adapter>/<id>.json"`。
   ActionRecord 是字段封闭的（`additionalProperties:false`），所以未知字段**不可能**出现在返回的
   record 里 —— 要拿原文请用 `convert_with_raw()`（store 层落盘后按指针取）。
3. **保守默认**（安全方向）。来源未提供影响面而动作有副作用（delete/send/payment/config_change/export）
   → 生成指向源记录的指针型 `impact_scope`，`reversible=false`（按不可撤回处理），并写 `uncertainty`
   说明"这是保守假设"。风险等级只在来源给了证据时才升到 high/critical，不做"看起来很危险"的猜测。

## 干预字段的边界（队长 Q2 裁决）

`requires_action=true` 时 schema 要求 ≥3 个带 `consequence` 的选项。适配器只生成**通用三选项**
（approve/modify/reject）+ 由事实拼出的后果文案；`editable_fields` 从来源入参的键名派生
（来源没给入参 → 一个 `params_note` 兜底字段，标签里明说"来源未提供入参"）。
适配器永远写 `user_intervention=null` 与 `execution_receipt.state="unreported"`：
**决定和执行都不是接入层能声称的事**（P4）。

## 实时接入复用（t14）

`normalize_events(batch)`：接受 webhook/SDK 上报的一批事件（list / `{"events":[...]}` /
JSON 字符串 / bytes，**允许一批里混多种格式**，逐条自动识别），返回展平后的记录并**按 `id` 去重**
（`id` 是幂等键，重复上报必须不产生重复卡片）。实现与批量转换共用同一套解析器。

用法：

    from agent_explainer import convert_trace_to_action_record, normalize_events
    records = convert_trace_to_action_record(json.loads(text))
    outcome = convert_with_raw(payload, source_kind="upload", language="zh")
    live    = normalize_events(webhook_body)      # 去重 + source.kind="webhook"
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# ----------------------------------------------------------------------------------
# 常量与契约
# ----------------------------------------------------------------------------------

SCHEMA_VERSION = "1.1"
"""目标记录格式版本；原生输入不等于 "1.1" → 直接拒绝（`SCHEMA_VERSION_UNSUPPORTED`）。"""

ADAPTER_NAME = "trace_adapter"
ADAPTER_VERSION = "0.1.0"

FORMAT_LANGFUSE = "langfuse"
FORMAT_OTEL = "otel_genai"
FORMAT_MCP = "mcp_events"
FORMAT_NATIVE = "native"

SUPPORTED_FORMATS: tuple[str, ...] = (FORMAT_LANGFUSE, FORMAT_OTEL, FORMAT_MCP, FORMAT_NATIVE)

#: 写进 `source.format_version`（schema 限制 ≤40 字符），完整规范名见 samples/README.md
#: 与夹具 `_meta.spec_basis`，归档里也保留全名（`source_format_version`）。
FORMAT_SPEC_VERSION: dict[str, str] = {
    FORMAT_LANGFUSE: "Langfuse public API v1 (OpenAPI 1.0.0)",
    FORMAT_OTEL: "OTLP JSON v1.3 + GenAI semconv 1.30.0",
    FORMAT_MCP: "MCP 2025-06-18 (JSON-RPC 2.0)",
    FORMAT_NATIVE: "ActionRecord schema 1.1",
}

#: 归档与自检用的完整规范名（可超过 format_version 的 40 字符上限）
FORMAT_SPEC_FULL: dict[str, str] = {
    FORMAT_LANGFUSE: "Langfuse public API v1 (OpenAPI 1.0.0, Ingestion API schema 2.0)",
    FORMAT_OTEL: "OTLP JSON v1.3 + GenAI semantic conventions 1.30.0",
    FORMAT_MCP: "Model Context Protocol 2025-06-18 (JSON-RPC 2.0)",
    FORMAT_NATIVE: "ActionRecord schema 1.1 (Draft 2020-12)",
}

#: 适配器名（`source.adapter`）
FORMAT_ADAPTER: dict[str, str] = {
    FORMAT_LANGFUSE: "langfuse_export",
    FORMAT_OTEL: "otel_json",
    FORMAT_MCP: "mcp_events",
    FORMAT_NATIVE: "native",
}

ACTION_TYPES: tuple[str, ...] = (
    "read", "write", "delete", "plan", "confirm", "ask", "error", "tool_call",
    "wait", "finish", "intervention", "config_change", "send", "payment",
    "export", "other",
)

#: C10：这些动作类型必须有影响面
SIDE_EFFECT_TYPES: frozenset[str] = frozenset(
    {"delete", "send", "payment", "config_change", "export"})

RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")

IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: TraceLens 在外部格式里约定的扩展命名空间（写进 samples/README.md，避免"隐式魔法键"）。
#: Langfuse: metadata.tracelens{...}；OTel: 属性 tracelens.risk.level 等；MCP: params._meta.tracelens{...}
EXT_NAMESPACE = "tracelens"

EFFECT_NOTICE_KEY = "intervention.effect.recorded_only"
TEMPLATE_L1_ID = "adapter.l1.v1"
TEMPLATE_OPTIONS_ID = "adapter.options.v1"

L1_LIMIT = {"zh": 40, "en": 80}
L3_LIMIT = 150
INTENT_LIMIT = 480
TEXT_LIMIT = 900

_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)

# ActionRecord 的字段封闭集合（与 docs/schemas/action_record.schema.json 对齐；
# 自检 _assert_shape 用它保证"适配器不会产出 schema 之外的键"）。
REQUIRED_RECORD_KEYS: tuple[str, ...] = (
    "schema_version", "id", "timestamp", "agent_id", "actor_id", "workspace_id",
    "session_id", "parent_action_id", "action_type", "tool_used", "intent",
    "rationale", "result_summary", "human_readable", "confidence", "cost",
    "latency_ms", "status", "requires_action", "intervention_options",
    "user_intervention", "execution_receipt", "risk_level", "policy", "source",
    "demo_data", "synthetic",
)
# 5.0 扩展字段（S0-2b）：9 个可合并字段进可选名单，导入面（HTTP）才送得到合并层
# （`ingest.merge_facts_full` 按 `EXPANSION_MERGE_FIELDS` 取值）。**闸门哲学不变**：
# 字段封闭仍在，受保护字段（`EXPANSION_PROTECTED_FIELDS`：signature / entry_hash /
# intervention_history / human_owner …）不在下面这份名单里，`_assert_shape` 照旧拦。
# 权威定义在 `ingest.py:EXPANSION_MERGE_FIELDS`；本模块**不能**反向 import 它——
# `ingest.py` 在模块级 import 了本模块（依赖方向是 ingest → trace_adapter），
# 反向 import 就是循环依赖（PM 口径：循环依赖不许硬绕）。因此这里是**逐字等价**的
# 第二份声明，由 `tests/test_trace_adapter.py::ExpansionFieldGateTests` 用
# `ingest.EXPANSION_MERGE_FIELDS` 逐字比对钉住：两处一旦漂移就红。
OPTIONAL_RECORD_KEYS: tuple[str, ...] = (
    "received_at", "sequence_no", "agent_name", "depth", "run_id",
    "requires_action_reason", "action_due_at", "uncertainty", "impact_scope",
    "error", "suggested_next", "context", "trace_link", "labels",
    # 以下为 5.0 扩展段（内容与顺序 = EXPANSION_MERGE_FIELDS 全集，逐字照抄）
    "group_id", "shared_users", "multimedia_evidence", "causal_explanation",
    "rollback_ref", "rollback_state", "improvement_suggestion",
    "evidence_feedback", "confidence_source",
)

# 事实指纹（与 tools/facts_hash.py 完全一致；测试逐条交叉比对，证明"两处一套算法"）
FACT_FIELDS: tuple[str, ...] = (
    "action_type", "confidence", "cost", "error", "impact_scope", "intent",
    "latency_ms", "rationale", "result_summary", "status",
)

# ----------------------------------------------------------------------------------
# 错误
# ----------------------------------------------------------------------------------


class TraceFormatError(ValueError):
    """非法/不可解析的输入。`message` 必须是人能读懂的中文原因（界面直接显示）。

    属性：
      code    机器可读码（前端按 code 出 i18n 文案，界面展示用 `message`）
      reason  人话原因
      fmt     判定出的（或尝试中的）格式
      field   出问题的字段路径，例如 `observations[3].startTime`
      index   出问题的记录序号（批量场景）
    """

    def __init__(self, code: str, reason: str, *, fmt: str | None = None,
                 field: str | None = None, index: int | None = None,
                 hint: str | None = None) -> None:
        self.code = code
        self.reason = reason
        self.fmt = fmt
        self.field = field
        self.index = index
        self.hint = hint
        super().__init__(self.render())

    def render(self) -> str:
        parts = [f"[{self.code}] {self.reason}"]
        if self.fmt:
            parts.append(f"格式：{self.fmt}")
        if self.field:
            parts.append(f"位置：{self.field}")
        if self.index is not None:
            parts.append(f"第 {self.index + 1} 条")
        if self.hint:
            parts.append(f"建议：{self.hint}")
        return "；".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """给统一错误体 `{code, message, detail, request_id}` 用（架构文档第 7 节）。"""
        return {"code": self.code, "message": self.render(),
                "detail": {"reason": self.reason, "format": self.fmt,
                           "field": self.field, "index": self.index, "hint": self.hint}}


# ----------------------------------------------------------------------------------
# 小工具：清洗 / 时间 / 脱敏摘要
# ----------------------------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> Sequence[Any]:
    return value if isinstance(value, list) else []


def _clip(text: Any, limit: int) -> str:
    s = str(text)
    if len(s) <= limit:
        return s
    keep = max(1, limit - 1)
    return s[:keep].rstrip() + "…"


def _ident(value: Any, fallback: str = "", *, label: str = "id") -> str:
    """把来源 ID 清洗成 schema 的 identifier 形态（禁空格/路径字符）。

    不猜测：清洗后为空则用 fallback；fallback 也为空则报错（ID 不能编造）。
    """
    raw = "" if value is None else str(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "-", raw)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._:")
    if cleaned and not cleaned[0].isalnum():
        cleaned = "x-" + cleaned
    cleaned = cleaned[:128]
    if not cleaned:
        cleaned = _ident(fallback, "", label="fallback") if fallback else ""
    if not cleaned:
        raise TraceFormatError("TRACE_FIELD_MISSING",
                               f"{label} 缺失，且来源没有提供可用作 ID 的字段",
                               field=label,
                               hint="每条记录必须有来源 ID（幂等键），不能由适配器编造")
    if not IDENT_RE.match(cleaned):
        raise TraceFormatError("TRACE_FIELD_INVALID",
                               f"{label} 清洗后仍不合法：{raw!r}",
                               field=label,
                               hint="请检查来源 ID 是否包含大量非法字符")
    return cleaned


def _text(value: Any, limit: int = TEXT_LIMIT) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
    else:
        s = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if not s:
        return None
    return _clip(s, limit)


_PATH_RE = re.compile(r"(?:[A-Za-z]:\\+|\\\\[A-Za-z0-9_.-]+\\+|/(?:home|Users|var|etc|mnt|root|media)/)[^\s\"'<>|]*")
_TOKEN_RE = re.compile(r"\b(?:sk|ghp|glpat|github_pat|AKIA|ASIA)[A-Za-z0-9_\-]{6,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _redact(text: str) -> str:
    """参数/结果摘要脱敏（与 DM 第 9 节导出脱敏同向）：路径 → `<path:n>`，密钥 → `<token>`。"""
    counters = {"n": 0}

    def _path(m: re.Match[str]) -> str:
        counters["n"] += 1
        return f"<path:{counters['n']}>"

    out = _PATH_RE.sub(_path, text)
    out = _TOKEN_RE.sub("<token>", out)
    out = _BEARER_RE.sub("Bearer <token>", out)
    out = _EMAIL_RE.sub("<email>", out)
    return out


def _summarize(value: Any, limit: int = 180) -> str | None:
    """把来源入参/出参压成一句**脱敏事实摘要**（不是翻译，不是完整明文）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return _clip(_redact(" ".join(value.split())), limit)
    if isinstance(value, Mapping):
        bits: list[str] = []
        for key, val in list(value.items())[:12]:
            if isinstance(val, str):
                shown = _redact(" ".join(val.split()))
                bits.append(f"{key}={_clip(shown, 40)}")
            elif val is None:
                bits.append(f"{key}=null")
            elif isinstance(val, (int, float, bool)):
                bits.append(f"{key}={json.dumps(val)}")  # true/false，不写 Python 的 True/False
            elif isinstance(val, list):
                bits.append(f"{key}={len(val)}项")
            elif isinstance(val, Mapping):
                bits.append(f"{key}({len(val)}键)")
        if len(value) > 12:
            bits.append(f"…共{len(value)}键")
        return _clip("；".join(bits), limit) if bits else None
    if isinstance(value, list):
        return _clip(f"{len(value)} 项列表：" + " | ".join(
            _clip(_redact(" ".join(str(v).split())), 40) for v in value[:5]), limit)
    return _clip(_redact(str(value)), limit)


_TS_Z = re.compile(r"([Zz])$")


def _parse_ts(value: Any, *, where: str, fmt: str | None = None) -> _dt.datetime:
    """解析 ISO8601；无时区 → 按 UTC 处理（写进 README 的公开约定），不可解析 → 可读错误。"""
    s = str(value).strip()
    if not s:
        raise TraceFormatError("TRACE_FIELD_MISSING", f"{where} 为空，无法确定动作发生时间",
                               fmt=fmt, field=where)
    norm = _TS_Z.sub("+00:00", s)
    if norm.endswith(" ") and norm.count(":") == 2:  # 'YYYY-MM-DD HH:MM:SS'
        norm = norm.replace(" ", "T", 1)
    norm = norm.replace("t", "T", 1) if "T" not in norm and "t" in norm else norm
    try:
        d = _dt.datetime.fromisoformat(norm)
    except ValueError as exc:
        raise TraceFormatError("TRACE_TIMESTAMP_INVALID",
                               f"{where} 不是可解析的时间格式：{value!r}（{exc}）",
                               fmt=fmt, field=where,
                               hint="请使用带时区偏移的 ISO8601，例如 2026-11-09T09:12:03.420Z") from exc
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def _iso(d: _dt.datetime) -> str:
    """规范化为带时区的 ISO8601（schema 的 pattern 要求偏移，统一用 Z）。"""
    d = d.astimezone(_dt.timezone.utc)
    base = d.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{d.microsecond:06d}Z" if d.microsecond else f"{base}Z"


def _iso_from_ns(nanos: Any, *, where: str, fmt: str | None = None) -> _dt.datetime:
    """OTLP `*UnixNano`（字符串或整数）→ UTC 时间（整数运算，避免浮点精度丢失）。"""
    try:
        ns = int(str(nanos).strip())
    except (TypeError, ValueError) as exc:
        raise TraceFormatError("TRACE_FIELD_INVALID",
                               f"{where} 不是整数纳秒时间：{nanos!r}",
                               fmt=fmt, field=where) from exc
    if ns <= 0:
        raise TraceFormatError("TRACE_FIELD_INVALID", f"{where} 为非正数纳秒时间戳：{ns}",
                               fmt=fmt, field=where)
    return _EPOCH + _dt.timedelta(microseconds=ns // 1000)


# ----------------------------------------------------------------------------------
# 动作类型判定（关键词表集中在这里，测试直接断言这张表的行为）
# ----------------------------------------------------------------------------------

#: (关键字, action_type)：按顺序匹配，先命中先赢（delete 优先于 write 是有意的）。
ACTION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("intervention", "intervention"),
    ("approve", "confirm"), ("confirm", "confirm"), ("approval", "confirm"), ("consent", "confirm"),
    ("delete", "delete"), ("remove", "delete"), ("drop", "delete"), ("purge", "delete"),
    ("trash", "delete"), ("uninstall", "delete"),
    ("payment", "payment"), ("pay", "payment"), ("charge", "payment"), ("transfer", "payment"),
    ("invoice", "payment"), ("refund", "payment"),
    ("export", "export"),
    ("config_change", "config_change"), ("config", "config_change"), ("setting", "config_change"),
    ("permission", "config_change"), ("install_plugin", "config_change"),
    ("send", "send"), ("email", "send"), ("mail", "send"), ("post_message", "send"),
    ("publish", "send"), ("webhook", "send"),
    ("ask", "ask"), ("question", "ask"), ("clarify", "ask"),
    ("plan", "plan"), ("todo", "plan"), ("decompose", "plan"), ("outline", "plan"),
    ("wait", "wait"), ("sleep", "wait"), ("poll", "wait"), ("progress", "wait"),
    ("finish", "finish"), ("complete", "finish"), ("summary", "finish"), ("final", "finish"),
    ("read", "read"), ("glob", "read"), ("grep", "read"), ("list", "read"), ("search", "read"),
    ("fetch", "read"), ("scan", "read"), ("ls", "read"), ("cat", "read"), ("inspect", "read"),
    ("write", "write"), ("edit", "write"), ("create", "write"), ("patch", "write"),
    ("update", "write"), ("append", "write"),
    ("tool", "tool_call"), ("function", "tool_call"), ("invoke", "tool_call"),
    ("mcp", "tool_call"), ("call", "tool_call"),
)

#: 关键词只在这些"低置信来源"里生效（span 名 / 工具名），避免整段输出文本命中关键词。
KEYWORD_SOURCE_FIELDS: tuple[str, ...] = ("name", "tool_name", "method")

CHANGE_BY_ACTION: dict[str, str] = {
    "read": "read", "write": "update", "delete": "delete", "plan": "update",
    "confirm": "update", "ask": "read", "error": "read", "tool_call": "update",
    "wait": "read", "finish": "read", "intervention": "update",
    "config_change": "update", "send": "send", "payment": "send", "export": "export",
    "other": "update",
}

ACTION_LABEL: dict[str, dict[str, str]] = {
    "read": {"zh": "读取", "en": "Read"},
    "write": {"zh": "写入", "en": "Write"},
    "delete": {"zh": "删除", "en": "Delete"},
    "plan": {"zh": "规划", "en": "Plan"},
    "confirm": {"zh": "请求确认", "en": "Ask for confirmation"},
    "ask": {"zh": "向你提问", "en": "Question for you"},
    "error": {"zh": "执行报错", "en": "Error"},
    "tool_call": {"zh": "调用工具", "en": "Tool call"},
    "wait": {"zh": "等待", "en": "Wait"},
    "finish": {"zh": "收尾汇总", "en": "Wrap-up"},
    "intervention": {"zh": "人工干预", "en": "Intervention"},
    "config_change": {"zh": "改配置", "en": "Config change"},
    "send": {"zh": "发送", "en": "Send"},
    "payment": {"zh": "付款", "en": "Payment"},
    "export": {"zh": "导出", "en": "Export"},
    "other": {"zh": "执行步骤", "en": "Step"},
}

STATUS_LABEL: dict[str, dict[str, str]] = {
    "observed": {"zh": "已记录", "en": "logged"},
    "running": {"zh": "进行中", "en": "running"},
    "succeeded": {"zh": "已完成", "en": "done"},
    "failed": {"zh": "失败", "en": "failed"},
    "skipped": {"zh": "已跳过", "en": "skipped"},
    "pending_confirmation": {"zh": "等你确认", "en": "awaiting you"},
    "expired": {"zh": "等待超时", "en": "timed out"},
}

OPTION_TEMPLATE: dict[str, dict[str, dict[str, str]]] = {
    "approve": {
        "zh": {"label": "同意执行", "consequence": "按原计划执行这一步，结果会写进时间轴"},
        "en": {"label": "Approve", "consequence": "Run this step as planned; the result goes on the timeline"},
    },
    "modify": {
        "zh": {"label": "修改后同意", "consequence": "先按你改的参数执行，其余步骤不受影响"},
        "en": {"label": "Edit & approve", "consequence": "Run with your edited parameters; other steps unchanged"},
    },
    "reject": {
        "zh": {"label": "拒绝执行", "consequence": "这一步跳过，后续步骤按未执行处理，不会自动重跑"},
        "en": {"label": "Reject", "consequence": "Skip this step; later steps are marked not run, no auto-retry"},
    },
}


def classify_action(name: Any, *, base: str = "tool_call") -> str:
    """按关键词表把来源名（span 名 / 工具名 / MCP 方法）映射到 action_type。

    命中不了 → `base`（各格式自己的保守默认），**不猜**具体语义。
    """
    s = str(name or "").lower()
    if not s:
        return base
    for keyword, action in ACTION_KEYWORDS:
        if keyword in s:
            return action
    return base


# ----------------------------------------------------------------------------------
# 扩展元数据（tracelens.* 命名空间）读取
# ----------------------------------------------------------------------------------


def _ext_from_mapping(meta: Any) -> dict[str, Any]:
    """从 metadata / _meta 里取 `tracelens` 扩展（支持嵌套对象与点号扁平键）。"""
    meta = _mapping(meta)
    ext: dict[str, Any] = {}
    nested = meta.get(EXT_NAMESPACE)
    if isinstance(nested, Mapping):
        ext.update(dict(nested))
    prefix = EXT_NAMESPACE + "."
    for key, val in meta.items():
        if isinstance(key, str) and key.startswith(prefix):
            ext[key[len(prefix):]] = val
    return ext


def _ext_from_attributes(attrs: Mapping[str, Any]) -> dict[str, Any]:
    ext: dict[str, Any] = {}
    prefix = EXT_NAMESPACE + "."
    for key, val in attrs.items():
        if isinstance(key, str) and key.startswith(prefix):
            ext[key[len(prefix):]] = val
    return ext


def _risk_from(value: Any) -> str | None:
    s = str(value or "").strip().lower()
    return s if s in RISK_LEVELS else None


def _impact_from(ext: Mapping[str, Any], *, fmt: str, obs_id: str) -> dict[str, Any] | None:
    """从扩展元数据构造 impact_scope（只搬来源给的对象标识，不编造路径）。"""
    impact = ext.get("impact")
    impact = _mapping(impact) if impact is not None else {}
    targets_raw = impact.get("targets", ext.get("impact.targets"))
    if isinstance(targets_raw, str):
        try:
            targets_raw = json.loads(targets_raw)
        except json.JSONDecodeError:
            targets_raw = None
    categories = impact.get("data_categories", ext.get("impact.data_categories"))
    reversible = impact.get("reversible", ext.get("impact.reversible"))
    summary = _text(impact.get("summary", ext.get("impact.summary")), 300)

    if targets_raw is None and reversible is None and not categories and not summary:
        return None

    targets: list[dict[str, Any]] = []
    for item in _list(targets_raw)[:200]:
        if isinstance(item, Mapping):
            kind = str(item.get("kind", "resource"))
            ref = _text(item.get("ref", item.get("target", "")), 500)
            if not ref:
                continue
            target: dict[str, Any] = {"kind": kind if kind in {
                "file", "directory", "table", "record", "endpoint", "service",
                "mailbox", "payment", "resource", "pii_dataset"} else "resource",
                "ref": _redact(ref),
                "change": item.get("change") if item.get("change") in {
                    "create", "update", "delete", "read", "send", "export"} else None,
                "count": item.get("count") if isinstance(item.get("count"), int) else None}
            targets.append({k: v for k, v in target.items() if v is not None})
        elif isinstance(item, str) and item.strip():
            targets.append({"kind": "resource", "ref": _redact(item.strip())})

    cats = [c for c in _list(categories) if c in {
        "none", "internal", "personal_data", "financial", "credentials",
        "health", "contract", "customer_data"}]

    out: dict[str, Any] = {
        "targets": targets,
        "money": None,
        "external_endpoint": None,
        "data_categories": cats,
        "reversible": bool(reversible) if reversible is not None else False,
        "summary": summary,
    }
    money = _mapping(impact.get("money"))
    if money:
        amount = money.get("amount")
        currency = str(money.get("currency", "")).strip().upper()
        if isinstance(amount, (int, float)) and re.match(r"^[A-Z]{3}$", currency):
            out["money"] = {"currency": currency, "amount": float(amount),
                            "direction": money.get("direction")
                            if money.get("direction") in ("in", "out") else "out"}
    endpoint = _mapping(impact.get("external_endpoint"))
    if endpoint.get("url"):
        out["external_endpoint"] = {
            "url": _clip(_redact(str(endpoint["url"])), 500),
            "method": _text(endpoint.get("method"), 10),
            "audience": _text(endpoint.get("audience"), 200)}
    if not out["data_categories"]:
        out["data_categories"] = ["internal"]
    if not out["summary"]:
        out["summary"] = _clip(f"来源声明的影响面（{fmt} 扩展元数据）", 300)
    return out


# ----------------------------------------------------------------------------------
# 中间表示
# ----------------------------------------------------------------------------------


@dataclass
class NormalizedEvent:
    """一个来源对象（observation / span / MCP 事件 / 原生记录）的归一化事实。"""

    source_id: str
    fmt: str
    timestamp: _dt.datetime | None = None
    agent_id: Any = None
    agent_name: Any = None
    actor_id: Any = None
    workspace_id: Any = None
    session_id: Any = None
    run_id: Any = None
    parent_source_id: str | None = None
    action_type: str | None = None
    tool: dict[str, Any] | None = None
    intent: str | None = None
    rationale: Any = None
    result_summary: Any = None
    status: str | None = None
    requires_action: bool = False
    requires_action_reason: Any = None
    action_due_at: _dt.datetime | None = None
    risk_level: str | None = None
    impact_scope: dict[str, Any] | None = None
    impact_from_source: bool = False
    confidence: float | None = None
    cost: dict[str, Any] | None = None
    latency_ms: int | None = None
    error: dict[str, Any] | None = None
    suggested_next: list[dict[str, Any]] | None = None
    task_name: Any = None
    trace_link: dict[str, Any] | None = None
    input_keys: list[tuple[str, Any]] = field(default_factory=list)
    synthetic: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    archived_known: dict[str, Any] = field(default_factory=dict)
    native_record: dict[str, Any] | None = None
    sequence_no: int | None = None
    depth: int | None = None


# ----------------------------------------------------------------------------------
# 格式识别
# ----------------------------------------------------------------------------------


def _loads(payload: Any) -> Any:
    """str/bytes → JSON（解析失败给可读原因）；其他类型原样返回。"""
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TraceFormatError("TRACE_INPUT_NOT_UTF8",
                                   f"输入不是 UTF-8 文本（{exc}）",
                                   hint="trace 文件请用 UTF-8 保存") from exc
        return _loads(text)
    if isinstance(payload, str):
        s = payload.strip()
        if not s:
            raise TraceFormatError("TRACE_INPUT_EMPTY", "输入是空字符串，没有任何 trace 内容")
        try:
            return json.loads(s)
        except json.JSONDecodeError as exc:
            raise TraceFormatError("TRACE_INPUT_NOT_JSON",
                                   f"输入不是合法 JSON：{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）",
                                   hint="粘贴内容时请完整复制，注意括号与引号配对") from exc
    return payload


def detect(payload: Any) -> str:
    """识别 4 种输入之一；识别不出抛 `TraceFormatError`（附"我看到了什么"）。"""
    doc = _loads(payload)

    if isinstance(doc, str):
        raise TraceFormatError("TRACE_INPUT_TYPE",
                               "解析结果是字符串，不是 trace 对象（外层引号包裹了整个 JSON）",
                               hint="请粘贴 JSON 对象/数组本身，不要包成字符串")
    if doc is None:
        raise TraceFormatError("TRACE_INPUT_EMPTY", "输入解析为 null，没有 trace 内容")
    if isinstance(doc, (int, float, bool)):
        raise TraceFormatError("TRACE_INPUT_TYPE", f"输入是标量（{type(doc).__name__}），不是 trace 结构")

    # 原生：ActionRecord 数组 / {"records":[...]}
    if isinstance(doc, Mapping):
        for key in ("records", "action_records"):
            if isinstance(doc.get(key), list):
                return FORMAT_NATIVE
        if "schema_version" in doc and "action_type" in doc:
            return FORMAT_NATIVE
        if doc.get("format") in ("action_record_list", "native_action_list"):
            return FORMAT_NATIVE
    if isinstance(doc, list) and doc and all(isinstance(x, Mapping) for x in doc):
        keys = set().union(*(set(x) for x in doc[:5]))
        if {"schema_version", "action_type", "status"} & keys and "action_type" in keys:
            return FORMAT_NATIVE

    # Langfuse
    if _looks_langfuse(doc):
        return FORMAT_LANGFUSE
    # OTel / OTLP
    if _looks_otel(doc):
        return FORMAT_OTEL
    # MCP
    if _looks_mcp(doc):
        return FORMAT_MCP

    seen = sorted(keys)[:8] if isinstance(doc, Mapping) and (keys := set(doc)) else []
    raise TraceFormatError(
        "TRACE_FORMAT_UNKNOWN",
        "认不出这是哪种 trace 格式"
        + (f"；顶层键：{seen}" if seen else "；顶层不是对象/数组"),
        hint=f"支持 {'/'.join(SUPPORTED_FORMATS)}；ActionRecord 需含 schema_version+action_type，"
             f"Langfuse 需含 observations/batch 信封，OTLP 需含 resourceSpans，MCP 需含 jsonrpc 事件")


def _looks_langfuse(doc: Any) -> bool:
    if isinstance(doc, Mapping):
        if isinstance(doc.get("observations"), list):
            return True
        if isinstance(doc.get("batch"), list):  # Langfuse Ingestion API
            return True
        if {"traceId", "startTime"} <= set(doc) and "traceId" in doc:
            return True
        if "traceId" in doc and "observations" in doc:
            return True
        if {"id", "timestamp"} <= set(doc) and "observations" in doc:
            return True
    if isinstance(doc, list) and doc and all(isinstance(x, Mapping) for x in doc):
        keys = set().union(*(set(x) for x in doc[:5]))
        if {"traceId", "startTime"} <= keys:
            return True
        if all({"type", "body"} <= set(x) for x in doc[:5]):
            return {"trace-create", "observation-create", "observation-update",
                    "span-create", "generation-create", "event-create"} & {
                        str(x.get("type")) for x in doc[:5]}
    return False


def _looks_otel(doc: Any) -> bool:
    if isinstance(doc, Mapping):
        if isinstance(doc.get("resourceSpans"), list):
            return True
        if isinstance(doc.get("scopeSpans"), list) and "scope" in doc:
            return True
        if "spans" in doc and isinstance(doc.get("spans"), list):
            return True
        if {"traceId", "spanId", "startTimeUnixNano"} <= set(doc):
            return True
    if isinstance(doc, list) and doc and all(isinstance(x, Mapping) for x in doc):
        keys = set().union(*(set(x) for x in doc[:5]))
        return {"spanId", "startTimeUnixNano"} <= keys
    return False


def _looks_mcp(doc: Any) -> bool:
    if isinstance(doc, Mapping):
        if doc.get("protocol") in ("mcp", "model-context-protocol"):
            return True
        for key in ("events", "messages", "stream"):
            if isinstance(doc.get(key), list):
                items = [x for x in doc[key] if isinstance(x, Mapping)]
                if items and any("jsonrpc" in x for x in items):
                    return True
        if doc.get("jsonrpc") == "2.0":
            return True
    if isinstance(doc, list) and doc and all(isinstance(x, Mapping) for x in doc):
        return any("jsonrpc" in x for x in doc[:5])
    return False


# ----------------------------------------------------------------------------------
# 解析器 1：Langfuse
# ----------------------------------------------------------------------------------

LANGFUSE_TRACE_FIELDS = frozenset({
    "id", "timestamp", "name", "input", "output", "metadata", "sessionId", "userId",
    "tags", "release", "version", "projectId", "environment", "observations",
    "htmlPath", "public", "bookmark", "latency", "totalCost", "totalUsage", "createdAt",
})
LANGFUSE_OBS_FIELDS = frozenset({
    "id", "traceId", "type", "name", "startTime", "endTime", "parentId",
    "parentObservationId", "input", "output", "metadata", "level", "statusMessage",
    "model", "modelParameters", "usage", "usageDetails", "costDetails",
    "completionStartTime", "promptId", "version", "environment", "status",
})


def _langfuse_envelope_items(doc: Any) -> list[Mapping[str, Any]]:
    """把各种 Langfuse 输入形态展平成 (trace 上下文, observation) 对。"""
    items: list[Mapping[str, Any]] = []
    if isinstance(doc, Mapping):
        if isinstance(doc.get("batch"), list):
            for entry in doc["batch"]:
                entry = _mapping(entry)
                body = _mapping(entry.get("body", entry.get("data")))
                etype = str(entry.get("type", "")).lower()
                if etype.endswith("trace-create") or etype == "trace-create":
                    items.append({"__trace__": body, "observations": []})
                elif body:
                    items.append({"__trace__": _mapping(body.get("traceCtx")), "obs": body})
            return items
        if isinstance(doc.get("observations"), list):
            return [doc]
        if "traceId" in doc:
            return [{"observations": [doc], "__trace__": {}}]
        return []
    if isinstance(doc, list):
        out: list[Mapping[str, Any]] = []
        for item in doc:
            item = _mapping(item)
            if not item:
                continue
            if {"type", "body"} <= set(item):
                body = _mapping(item.get("body"))
                etype = str(item.get("type", "")).lower()
                out.append({"__trace__": body if etype.endswith("trace-create") else {},
                            "observations": [] if etype.endswith("trace-create")
                            else ([body] if body else [])})
            elif "traceId" in item and "observations" not in item:
                out.append({"__trace__": {}, "observations": [item]})
            else:
                out.append(item)
        return out
    return []


def parse_langfuse(doc: Any, *, fmt: str = FORMAT_LANGFUSE) -> list[NormalizedEvent]:
    if isinstance(doc, Mapping) and isinstance(doc.get("observations"), list) \
            and not doc["observations"]:
        raise TraceFormatError("TRACE_LANGFUSE_EMPTY",
                               "识别为 Langfuse 导出，但 observations 是空数组，没有任何可转出的步骤",
                               fmt=fmt, hint="Langfuse 导出需带 observations（SPAN/EVENT/GENERATION）")
    traces = _langfuse_envelope_items(doc)
    if not traces:
        raise TraceFormatError("TRACE_LANGFUSE_EMPTY",
                               "识别为 Langfuse 格式，但里面没有 trace 也没有 observations",
                               fmt=fmt, hint="检查导出文件是否为 Langfuse 的 trace/observation JSON")

    events: list[NormalizedEvent] = []
    for trace in traces:
        trace_ctx = _mapping(trace.get("__trace__"))
        trace_unknown = {k: v for k, v in trace.items()
                         if k not in LANGFUSE_TRACE_FIELDS and k != "__trace__"}
        ctx_unknown = {k: v for k, v in trace_ctx.items() if k not in LANGFUSE_TRACE_FIELDS}
        meta = _mapping(trace.get("metadata", trace_ctx.get("metadata")))
        ext = _ext_from_mapping(meta)
        trace_meta_unknown = {k: v for k, v in meta.items()
                              if k not in (EXT_NAMESPACE, "rationale", "reason", "agent_id",
                                           "agent_name", "actor_id", "workspace_id",
                                           "confidence", "synthetic")}
        base_raw: dict[str, Any] = {}
        if trace_unknown:
            base_raw["trace_unknown_fields"] = trace_unknown
        if ctx_unknown:
            base_raw["trace_batch_body_unknown_fields"] = ctx_unknown
        if trace_meta_unknown:
            base_raw["trace_metadata_unknown_fields"] = trace_meta_unknown

        trace_id = trace.get("id", trace_ctx.get("id"))
        session_id = trace.get("sessionId", trace_ctx.get("sessionId"))
        user_id = trace.get("userId", trace_ctx.get("userId"))
        project_id = trace.get("projectId", trace_ctx.get("projectId"))
        task_name = _text(trace.get("name", trace_ctx.get("name")), 200)
        trace_ts = trace.get("timestamp", trace_ctx.get("timestamp"))

        observations = [o for o in _list(trace.get("observations")) if isinstance(o, Mapping)]
        if not observations:
            # 只有 trace 本体（无 observation）：trace 自己成为一条记录
            observations = [{"id": trace_id, "type": "EVENT", "name": task_name,
                             "startTime": trace_ts, "endTime": trace_ts,
                             "input": trace.get("input"), "output": trace.get("output"),
                             "metadata": meta, "__from_trace__": True}]

        for obs in observations:
            obs_id = obs.get("id")
            if obs_id is None:
                raise TraceFormatError("TRACE_FIELD_MISSING",
                                       "observation 缺少 id（记录的幂等键）",
                                       fmt=fmt, field="observations[].id")
            where_start = "startTime"
            start = obs.get("startTime", trace_ts)
            if start is None:
                raise TraceFormatError("TRACE_FIELD_MISSING",
                                       f"observation {obs_id} 没有 startTime，且所属 trace 也没有 timestamp",
                                       fmt=fmt, field=f"observations[].{where_start}", index=None)
            ts = _parse_ts(start, where=where_start, fmt=fmt)
            end_raw = obs.get("endTime")

            obs_meta = _mapping(obs.get("metadata"))
            obs_ext = _ext_from_mapping(obs_meta)
            ext_all = {**ext, **obs_ext}
            obs_unknown = {k: v for k, v in obs.items() if k not in LANGFUSE_OBS_FIELDS
                           and k != "__from_trace__"}
            obs_meta_unknown = {k: v for k, v in obs_meta.items()
                                if k not in (EXT_NAMESPACE, "rationale", "reason", "agent_id",
                                             "agent_name", "actor_id", "workspace_id",
                                             "confidence", "synthetic")}
            raw: dict[str, Any] = dict(base_raw)
            if obs_unknown:
                raw["observation_unknown_fields"] = obs_unknown
            if obs_meta_unknown:
                raw["observation_metadata_unknown_fields"] = obs_meta_unknown

            otype = str(obs.get("type", "SPAN")).upper()
            model = _text(obs.get("model"), 120)
            name = _text(obs.get("name"), 200)
            level = str(obs.get("level", "DEFAULT")).upper()
            status_message = _text(obs.get("statusMessage"), 400)

            tool: dict[str, Any] | None
            if obs.get("__from_trace__"):
                tool = None
                base_action = "other"
            elif otype == "GENERATION":
                tool = {"name": model or name or "llm", "version": None,
                        "provider": _text(ext_all.get("provider"), 120)}
                base_action = "tool_call"
            else:
                tool = {"name": name or otype.lower(), "version": _text(obs.get("version"), 40),
                        "provider": None}
                base_action = "tool_call" if otype == "SPAN" else "other"
            tool["call_id"] = _text(obs_id, 200)
            tool["params_summary"] = _summarize(obs.get("input"))
            tool["result_ref"] = f"local://raw/{FORMAT_ADAPTER[fmt]}/{_ident(obs_id, 'x')}/output.json"
            tool = {k: v for k, v in tool.items() if v is not None} or None
            if not name and not model:
                tool = None

            action = str(ext_all.get("action_type", "")).strip().lower()
            if action not in ACTION_TYPES:
                action = classify_action(name, base=base_action)

            usage = _mapping(obs.get("usage")) or _mapping(obs.get("usageDetails"))
            cost = _langfuse_cost(usage, fmt=fmt)
            latency = None
            if end_raw is not None:
                end = _parse_ts(end_raw, where="endTime", fmt=fmt)
                latency = max(0, int((end - ts).total_seconds() * 1000))

            requires = bool(ext_all.get("requires_confirmation", ext_all.get("requires_action", False)))
            status = "succeeded"
            if level == "ERROR":
                status = "failed"
            if requires:
                status = "pending_confirmation"
            elif end_raw is None:
                status = "running"
            if otype == "EVENT" and not requires and end_raw is None and level != "ERROR":
                status = "observed"

            risk = _risk_from(ext_all.get("risk_level", ext_all.get("risk.level")))
            impact = _impact_from(ext_all, fmt=fmt, obs_id=str(obs_id))
            confidence = _confidence(ext_all.get("confidence"))

            intent_src = _text(ext_all.get("intent"))
            intent = intent_src or _langfuse_intent(otype, name, model)

            parent = obs.get("parentObservationId", obs.get("parentId"))
            events.append(NormalizedEvent(
                source_id=str(obs_id), fmt=fmt, timestamp=ts,
                agent_id=ext_all.get("agent_id", meta.get("agent_id")),
                agent_name=ext_all.get("agent_name", meta.get("agent_name")),
                actor_id=ext_all.get("actor_id", meta.get("actor_id", user_id)),
                workspace_id=ext_all.get("workspace_id", meta.get("workspace_id", project_id)),
                session_id=ext_all.get("session_id", session_id),
                run_id=ext_all.get("run_id", trace.get("release", trace_ctx.get("release"))),
                parent_source_id=None if parent in (None, "") else str(parent),
                action_type=action, tool=tool, intent=intent,
                rationale=ext_all.get("rationale", obs_meta.get("rationale",
                                                                obs_meta.get("reason",
                                                                            meta.get("rationale")))),
                result_summary=_text(obs.get("output"), INTENT_LIMIT),
                status=status,
                requires_action=requires,
                requires_action_reason=_text(ext_all.get("requires_action_reason",
                                                          ext_all.get("reason")), 300),
                action_due_at=(_parse_ts(ext_all.get("due_at"), where="tracelens.due_at", fmt=fmt)
                               if ext_all.get("due_at") else None),
                risk_level=risk, impact_scope=impact,
                impact_from_source=bool(impact),
                confidence=confidence,
                cost=cost, latency_ms=latency,
                error=_langfuse_error(status, level, status_message, str(obs_id), fmt),
                task_name=task_name,
                trace_link={"trace_id": _text(trace_id, 128), "span_id": None,
                            "observation_id": _text(obs_id, 128),
                            "source_url": _text(trace.get("htmlPath", trace_ctx.get("htmlPath")), 500)},
                input_keys=_input_keys(obs.get("input")),
                synthetic=_synthetic(ext_all.get("synthetic", meta.get("synthetic"))),
                raw=raw,
                archived_known=({"source_usage": usage} if usage and cost is None else {}),
                depth=obs.get("depth") if isinstance(obs.get("depth"), int) else None,
            ))
    return events


def _langfuse_intent(otype: str, name: str | None, model: str | None) -> str:
    if otype == "GENERATION":
        return _clip(f"调用模型 {model or '未知模型'}：{name or '未命名生成'}", INTENT_LIMIT)
    if otype == "EVENT":
        return _clip(f"上报事件：{name or '未命名事件'}", INTENT_LIMIT)
    return _clip(f"执行步骤：{name or '未命名步骤'}", INTENT_LIMIT)


def _langfuse_cost(usage: Mapping[str, Any], *, fmt: str) -> dict[str, Any] | None:
    """Langfuse usage → cost。**只有 token 数时 cost=null**（禁止把 token 编成金额）。"""
    if not usage:
        return None
    total_cost = usage.get("totalCost", usage.get("total_cost",
                                                  _mapping(usage.get("costDetails")).get("totalCost")))
    if not isinstance(total_cost, (int, float)):
        return None
    unit = str(usage.get("unit", "USD")).strip().upper()
    currency = unit if re.match(r"^[A-Z]{3}$", unit) else "USD"
    cost: dict[str, Any] = {"currency": currency, "amount": float(total_cost),
                            "estimated": bool(usage.get("costEstimated", False))}
    tin, tout = usage.get("input"), usage.get("output")
    if isinstance(tin, int) and tin >= 0:
        cost["tokens_in"] = tin
    if isinstance(tout, int) and tout >= 0:
        cost["tokens_out"] = tout
    return cost


def _langfuse_error(status: str, level: str, message: str | None, obs_id: str,
                    fmt: str) -> dict[str, Any] | None:
    if status != "failed":
        return None
    return {"code": "LANGFUSE_LEVEL_ERROR" if level == "ERROR" else "SOURCE_REPORTED_FAILURE",
            "human_reason": _clip(message, 400) if message
            else "来源把这一步标记为失败（ERROR），但没有写明原因；原始内容见 raw_ref",
            "technical_detail_ref": f"local://raw/{FORMAT_ADAPTER[fmt]}/{_ident(obs_id, 'x')}/source.json",
            "retryable": False,
            "failed_step": None}


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= num <= 1.0:
        return round(num, 4)
    return None


def _synthetic(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _input_keys(value: Any) -> list[tuple[str, Any]]:
    """来源入参的顶层键（用于动态生成"修改后同意"的可编辑字段，字段名来自事实）。"""
    if not isinstance(value, Mapping):
        return []
    out: list[tuple[str, Any]] = []
    for key, val in value.items():
        if isinstance(val, (str, int, float, bool)) or val is None:
            out.append((str(key), val))
        if len(out) >= 10:
            break
    return out


# ----------------------------------------------------------------------------------
# 解析器 2：OTel GenAI / OTLP JSON
# ----------------------------------------------------------------------------------

OTEL_SPAN_FIELDS = frozenset({
    "traceId", "spanId", "parentSpanId", "name", "kind", "startTimeUnixNano",
    "endTimeUnixNano", "attributes", "status", "duration",
})
OTEL_ATTR_VALUE_FIELDS = frozenset({"stringValue", "intValue", "doubleValue", "boolValue",
                                    "arrayValue", "kvlistValue", "bytesValue"})
#: 适配器会消费的属性键前缀；其它属性键进 raw（不参与翻译）。
OTEL_KNOWN_ATTR_PREFIXES = ("gen_ai.", "tracelens.", "service.", "telemetry.sdk.", "user.",
                            "session.", "workspace.", "exception.")


def _attr_value(node: Any) -> Any:
    """OTLP `AnyValue` → Python 值。"""
    if isinstance(node, Mapping):
        node = _mapping(node)
        for key in ("stringValue", "boolValue"):
            if key in node:
                return node[key]
        for key in ("intValue", "doubleValue"):
            if key in node:
                try:
                    return float(node[key]) if key == "doubleValue" else int(node[key])
                except (TypeError, ValueError):
                    return node[key]
        if "arrayValue" in node:
            items = _mapping(node["arrayValue"]).get("values", [])
            return [_attr_value(v) for v in _list(items)]
        if "kvlistValue" in node:
            pairs = _mapping(node["kvlistValue"]).get("values", [])
            return {str(_mapping(p).get("key")): _attr_value(_mapping(p).get("value"))
                    for p in _list(pairs)}
        if "bytesValue" in node:
            return f"<bytes:{len(str(node['bytesValue']))}>"
        return json.dumps(node, ensure_ascii=False)
    return node


def _flatten_attributes(nodes: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for node in _list(nodes):
        node = _mapping(node)
        key = node.get("key")
        if key is None:
            continue
        out[str(key)] = _attr_value(node.get("value"))
    return out


def _otel_spans(doc: Any) -> list[tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]]:
    """展平 resourceSpans → scopeSpans → spans：[(resource attrs, scope, span)]。

    兼容三种常见导出：完整 OTLP 批次、单 scopeSpans、裸 span（实时 webhook 常一条一 span）。
    """
    out: list[tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]] = []
    if isinstance(doc, list):
        bare = [s for s in doc if isinstance(s, Mapping) and ("spanId" in s or "traceId" in s)]
        return [({}, {}, s) for s in bare]
    for resource in _list(doc.get("resourceSpans")):
        resource = _mapping(resource)
        res_attrs = _flatten_attributes(_mapping(resource.get("resource")).get("attributes"))
        for scope_spans in _list(resource.get("scopeSpans")):
            scope_spans = _mapping(scope_spans)
            scope = _mapping(scope_spans.get("scope"))
            for span in _list(scope_spans.get("spans")):
                if isinstance(span, Mapping):
                    out.append((res_attrs, scope, span))
    if out:
        return out
    for scope_spans in _list(doc.get("scopeSpans")):
        scope_spans = _mapping(scope_spans)
        scope = _mapping(scope_spans.get("scope"))
        for span in _list(scope_spans.get("spans")):
            if isinstance(span, Mapping):
                out.append({}, scope, span)
    if out:
        return out
    spans = [s for s in _list(doc.get("spans")) if isinstance(s, Mapping)]
    if spans:
        return [({}, {}, s) for s in spans]
    if "spanId" in doc or "traceId" in doc:
        return [({}, {}, doc)]
    return out


def parse_otel(doc: Any, *, fmt: str = FORMAT_OTEL) -> list[NormalizedEvent]:
    flat = _otel_spans(doc)
    if not flat:
        raise TraceFormatError("TRACE_OTEL_EMPTY",
                               "识别为 OTLP 格式，但 resourceSpans/scopeSpans 里没有任何 span",
                               fmt=fmt, hint="OTLP JSON 需要 resourceSpans[].scopeSpans[].spans[]")

    events: list[NormalizedEvent] = []
    for res_attrs, scope, span in flat:
        span_id = span.get("spanId")
        if span_id in (None, ""):
            raise TraceFormatError("TRACE_FIELD_MISSING", "span 缺少 spanId（记录的幂等键）",
                                   fmt=fmt, field="spans[].spanId")
        attrs = _flatten_attributes(span.get("attributes"))
        merged_attrs = {**res_attrs, **attrs}
        ext = _ext_from_attributes(merged_attrs)

        start_raw = span.get("startTimeUnixNano")
        if start_raw in (None, ""):
            raise TraceFormatError("TRACE_FIELD_MISSING", f"span {span_id} 缺少 startTimeUnixNano",
                                   fmt=fmt, field="spans[].startTimeUnixNano")
        ts = _iso_from_ns(start_raw, where="startTimeUnixNano", fmt=fmt)
        end_raw = span.get("endTimeUnixNano")
        latency = None
        if end_raw not in (None, ""):
            end = _iso_from_ns(end_raw, where="endTimeUnixNano", fmt=fmt)
            latency = max(0, int((end - ts).total_seconds() * 1000))

        span_unknown = {k: v for k, v in span.items() if k not in OTEL_SPAN_FIELDS}
        attr_unknown = {k: v for k, v in merged_attrs.items()
                        if not any(k.startswith(p) for p in OTEL_KNOWN_ATTR_PREFIXES)}
        scope_unknown = {k: v for k, v in scope.items() if k not in ("name", "version")}
        raw: dict[str, Any] = {}
        if span_unknown:
            raw["span_unknown_fields"] = span_unknown
        if attr_unknown:
            raw["attribute_unknown_keys"] = attr_unknown
        if scope_unknown:
            raw["instrumentation_scope_unknown_fields"] = scope_unknown

        op = str(merged_attrs.get("gen_ai.operation.name", "")).lower()
        tool_name = _text(merged_attrs.get("gen_ai.tool.name"), 120)
        model = _text(merged_attrs.get("gen_ai.request.model"), 120)
        provider = _text(merged_attrs.get("gen_ai.provider.name"), 120)
        name = _text(span.get("name"), 200)

        tool: dict[str, Any] | None = None
        if op == "execute_tool" and tool_name:
            tool = {"name": tool_name, "version": None, "provider": provider,
                    "call_id": _text(merged_attrs.get("gen_ai.tool.call.id"), 200),
                    "params_summary": _summarize(_json_maybe(merged_attrs.get("gen_ai.tool.call.arguments")))}
            tool = {k: v for k, v in tool.items() if v is not None}
            base_action = classify_action(tool_name, base="tool_call")
        elif model:
            tool = {"name": model, "version": None, "provider": provider,
                    "call_id": _text(span_id, 200),
                    "params_summary": _summarize(_json_maybe(merged_attrs.get("gen_ai.input.messages")))}
            tool = {k: v for k, v in tool.items() if v is not None}
            base_action = "tool_call" if op in ("chat", "generate_content") else "other"
        elif name:
            tool = {"name": name, "version": None, "provider": None,
                    "call_id": _text(span_id, 200)}
            base_action = classify_action(name, base="tool_call")

        action = str(ext.get("action_type", "")).strip().lower()
        if action not in ACTION_TYPES:
            if op == "execute_tool":
                action = classify_action(tool_name or name, base="tool_call")
            elif op in ("chat", "generate_content", "embeddings", "invoke_agent"):
                action = classify_action(name, base="other")
            else:
                action = classify_action(name, base="other")

        status_code = str(_mapping(span.get("status")).get("code", "STATUS_CODE_UNSET"))
        status_reason = _text(_mapping(span.get("status")).get("reason"), 400)
        requires = _truthy(ext.get("requires_confirmation", ext.get("requires_action")))
        is_err = status_code == "STATUS_CODE_ERROR"
        exc_events = [e for e in _list(span.get("events"))
                      if isinstance(e, Mapping) and str(_mapping(e).get("name")) == "exception"]
        if is_err:
            status = "failed"
        elif requires:
            status = "pending_confirmation"
        elif end_raw in (None, ""):
            status = "running"
        else:
            status = "succeeded"

        tokens_in = merged_attrs.get("gen_ai.usage.input_tokens")
        tokens_out = merged_attrs.get("gen_ai.usage.output_tokens")
        money = merged_attrs.get("gen_ai.cost.usage.total_amount")
        currency = str(merged_attrs.get("gen_ai.cost.usage.currency", "USD")).strip().upper()
        cost = None
        archived: dict[str, Any] = {}
        if isinstance(money, (int, float)) and re.match(r"^[A-Z]{3}$", currency):
            cost = {"currency": currency, "amount": float(money),
                    "estimated": _truthy(merged_attrs.get("gen_ai.cost.usage.estimated"))}
            if isinstance(tokens_in, (int, float)):
                cost["tokens_in"] = int(tokens_in)
            if isinstance(tokens_out, (int, float)):
                cost["tokens_out"] = int(tokens_out)
        elif tokens_in is not None or tokens_out is not None:
            archived["gen_ai_usage_tokens"] = {"input_tokens": tokens_in,
                                               "output_tokens": tokens_out,
                                               "note": "来源只有 token 计数，无金额 → cost=null（不编造金额）"}

        result_raw = _json_maybe(merged_attrs.get("gen_ai.tool.call.result"))
        finish = merged_attrs.get("gen_ai.response.finish_reasons")
        summary = _text(result_raw, INTENT_LIMIT)
        if summary is None and finish:
            summary = _clip(f"模型返回结束原因：{_summarize(finish, 120)}", 1000)
        if summary is None and status == "succeeded":
            summary = f"来源上报为成功，但未提供结果内容（span {span_id}）"

        risk = _risk_from(ext.get("risk_level", ext.get("risk.level")))
        impact = _impact_from(ext, fmt=fmt, obs_id=str(span_id))
        confidence = _confidence(ext.get("confidence"))

        intent = _text(ext.get("intent")) or _clip(
            f"{name or (op or 'span')}｜{('操作 ' + op) if op else '无 gen_ai.operation.name'}"
            + (f"｜工具 {tool_name}" if tool_name else ""), INTENT_LIMIT)

        error = None
        if status == "failed":
            exc_reason = None
            for ev in exc_events:
                ev_attrs = _flatten_attributes(_mapping(ev).get("attributes"))
                exc_reason = _text(ev_attrs.get("exception.message") or ev_attrs.get("exception.type"), 400)
                if exc_reason:
                    break
            error = {"code": "OTEL_STATUS_CODE_ERROR",
                     "human_reason": _clip(status_reason or exc_reason, 400)
                     if (status_reason or exc_reason)
                     else "来源把这一步标记为 Error，但没有写明原因；原始 span 见 raw_ref",
                     "technical_detail_ref": f"local://raw/{FORMAT_ADAPTER[fmt]}/otel-{_ident(span_id, 'x')}/source.json",
                     "retryable": False,
                     "failed_step": _text(name, 200)}
            if exc_reason:
                raw["exception_events"] = [dict(e) for e in exc_events]

        events.append(NormalizedEvent(
            source_id=f"otel-{_ident(span_id, 'x')}", fmt=fmt, timestamp=ts,
            agent_id=ext.get("agent_id", merged_attrs.get("gen_ai.agent.id")
                             or merged_attrs.get("gen_ai.agent.name")),
            agent_name=ext.get("agent_name", merged_attrs.get("gen_ai.agent.name")),
            actor_id=ext.get("actor_id", merged_attrs.get("user.id")
                             or merged_attrs.get("tracelens.actor_id")),
            workspace_id=ext.get("workspace_id", merged_attrs.get("workspace.id")),
            session_id=ext.get("session_id", merged_attrs.get("gen_ai.conversation.id")
                               or merged_attrs.get("session.id")),
            run_id=ext.get("run_id"),
            parent_source_id=(f"otel-{_ident(span.get('parentSpanId'), 'x')}"
                              if span.get("parentSpanId") not in (None, "") else None),
            action_type=action, tool=tool, intent=intent,
            rationale=_text(ext.get("rationale")),
            result_summary=summary,
            status=status, requires_action=requires,
            requires_action_reason=_text(ext.get("requires_action_reason", ext.get("reason")), 300),
            action_due_at=(_parse_ts(ext.get("due_at"), where="tracelens.due_at", fmt=fmt)
                           if ext.get("due_at") else None),
            risk_level=risk, impact_scope=impact, impact_from_source=bool(impact),
            confidence=confidence, cost=cost, latency_ms=latency, error=error,
            task_name=_text(merged_attrs.get("tracelens.task_name")
                            or merged_attrs.get("gen_ai.agent.name"), 200),
            trace_link={"trace_id": _text(span.get("traceId"), 128),
                        "span_id": _text(span_id, 128), "observation_id": None,
                        "source_url": _text(merged_attrs.get("tracelens.source_url"), 500)},
            input_keys=_input_keys(_json_maybe(merged_attrs.get("gen_ai.tool.call.arguments"))),
            synthetic=_synthetic(ext.get("synthetic")),
            raw=raw, archived_known=archived,
            depth=span.get("tracelens.depth") if isinstance(span.get("tracelens.depth"), int) else None,
        ))
    return events


def _json_maybe(value: Any) -> Any:
    """OTel 属性常把 JSON 存成字符串；能解析就解析（解析不了原样返回）。"""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return value
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on", "需要确认")
    return bool(value)


# ----------------------------------------------------------------------------------
# 解析器 3：MCP 事件流（JSON-RPC 2.0）
# ----------------------------------------------------------------------------------

MCP_EVENT_FIELDS = frozenset({
    "ts", "timestamp", "direction", "session_id", "request_id", "server", "message",
    "jsonrpc", "id", "method", "params", "result", "error", "type",
})
MCP_PARAM_FIELDS = frozenset({
    "name", "arguments", "_meta", "progressToken", "cursor", "reason", "relatedRequestToolName",
    "content", "role", "level", "logger", "data", "taskId", "serverInfo", "protocolVersion",
    "capabilities", "instructions", "tools", "resources", "prompts", "clientInfo",
    "roots", "resource", "uri", "arguments", "id",
})
MCP_NO_RECORD_METHODS = frozenset({"initialize", "notifications/initialized",
                                   "ping", "notifications/cancelled_response"})

MCP_METHOD_BASE: dict[str, tuple[str, str]] = {
    #  method → (基础 action_type, 是否工具调用)
    "tools/call": ("tool_call", True),
    "tools/list": ("read", False),
    "resources/read": ("read", False),
    "resources/list": ("read", False),
    "prompts/get": ("read", False),
    "prompts/list": ("read", False),
    "completion/complete": ("read", False),
    "logging/setLevel": ("config_change", False),
}


def _mcp_events(doc: Any) -> list[dict[str, Any]]:
    """展平事件流：每个元素 = 一个 JSON-RPC 消息（信封字段合并进来，含流级默认值）。"""
    raw_items: list[Any] = []
    defaults: dict[str, Any] = {}
    if isinstance(doc, Mapping):
        defaults = {key: doc[key] for key in ("session_id", "user_id", "workspace_id", "server")
                    if doc.get(key) is not None}
        for key in ("events", "messages", "stream", "batch"):
            if isinstance(doc.get(key), list):
                raw_items = list(doc[key])
                break
        else:
            raw_items = [doc]
    elif isinstance(doc, list):
        raw_items = list(doc)
    events: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        item = dict(item)
        message = item.pop("message", None)
        if isinstance(message, Mapping):
            merged = dict(message)
            for key, val in item.items():
                if key not in merged:
                    merged[key] = val
            events.append(merged)
        else:
            events.append(item)
    out = [e for e in events if "jsonrpc" in e]
    for event in out:
        for key, val in defaults.items():
            event.setdefault(key, val)
    return out


def parse_mcp(doc: Any, *, fmt: str = FORMAT_MCP) -> list[NormalizedEvent]:
    events_in = _mcp_events(doc)
    if not events_in:
        raise TraceFormatError("TRACE_MCP_EMPTY",
                               "识别为 MCP 事件流，但里面没有带 jsonrpc 字段的事件",
                               fmt=fmt, hint="事件流每条应是 JSON-RPC 2.0 消息（可加 ts/session_id 信封）")

    # 第一遍：索引 responses（按 id）与 initialize 的 serverInfo（会话身份要往后带）
    responses: dict[str, Mapping[str, Any]] = {}
    session_ctx: dict[str, Any] = {}
    for ev in events_in:
        if "method" not in ev and ("result" in ev or "error" in ev) and ev.get("id") is not None:
            responses[str(ev["id"])] = ev
        result = _mapping(ev.get("result"))
        if "protocolVersion" in result and "serverInfo" in result:
            server_info = _mapping(result.get("serverInfo"))
            session_ctx["server_name"] = _text(server_info.get("name"), 120)
            session_ctx["server_version"] = _text(server_info.get("version"), 40)
            session_ctx["protocol_version"] = _text(result.get("protocolVersion"), 40)

    out: list[NormalizedEvent] = []
    for idx, ev in enumerate(events_in):
        method = ev.get("method")
        if method is None:
            continue  # 纯响应已合并进对应请求
        method = str(method)
        if method in MCP_NO_RECORD_METHODS:
            continue

        params = _mapping(ev.get("params"))
        result = _mapping(ev.get("result")) if isinstance(ev.get("result"), Mapping) else {}
        envelope_unknown = {k: v for k, v in ev.items() if k not in MCP_EVENT_FIELDS}
        params_unknown = {k: v for k, v in params.items() if k not in MCP_PARAM_FIELDS}
        result_unknown = {k: v for k, v in result.items()
                          if k not in ("content", "isError", "protocolVersion", "serverInfo",
                                       "capabilities", "instructions", "tools", "resources",
                                       "prompts", "completion")}
        raw: dict[str, Any] = {}
        if envelope_unknown:
            raw["event_unknown_fields"] = envelope_unknown
        if params_unknown:
            raw["params_unknown_fields"] = params_unknown
        if result_unknown:
            raw["result_unknown_fields"] = result_unknown

        ext = _ext_from_mapping(params.get("_meta"))
        meta_unknown = {k: v for k, v in _mapping(params.get("_meta")).items()
                        if k not in (EXT_NAMESPACE, "progressToken", "io.modelcontextprotocol/related-request")}
        if meta_unknown:
            raw["meta_unknown_fields"] = meta_unknown

        tool_name = _text(params.get("name"), 120)
        base, is_tool_call = MCP_METHOD_BASE.get(method, ("other", False))
        action = str(ext.get("action_type", "")).strip().lower()
        if action not in ACTION_TYPES:
            if method == "notifications/cancelled":
                action = "intervention"
            elif method == "notifications/message":
                action = "error" if str(params.get("level", "")).lower() in (
                    "error", "critical", "fatal") else "other"
            elif method == "notifications/progress":
                action = "wait"
            elif is_tool_call:
                action = classify_action(tool_name, base=base)
            else:
                action = base

        paired = responses.get(str(ev.get("id"))) if ev.get("id") is not None else None
        rpc_error = _mapping(ev.get("error")) or _mapping(_mapping(paired).get("error"))
        is_error_result = bool(result.get("isError")) or bool(paired and
                                                              _mapping(paired.get("result")).get("isError"))
        requires = _truthy(ext.get("requires_confirmation", ext.get("requires_action")))
        log_level = str(params.get("level", "")).lower() if method == "notifications/message" else ""
        log_failure = log_level in ("error", "critical", "fatal")
        if rpc_error:
            status = "failed"
        elif log_failure:
            # 服务端把这条日志标成 error/critical：这是一次失败事件，不是"成功上报的日志"
            status = "failed"
        elif is_error_result:
            # 工具返回 isError=true：业务失败（不是"调用完成"）
            status = "failed"
        elif method == "notifications/cancelled":
            status = "skipped"
        elif requires:
            status = "pending_confirmation"
        elif is_tool_call and paired is None:
            status = "running"
        else:
            status = "succeeded"

        ts_raw = ev.get("ts", ev.get("timestamp"))
        if ts_raw is None:
            raise TraceFormatError("TRACE_FIELD_MISSING",
                                   f"第 {idx + 1} 条 MCP 事件没有 ts/timestamp，无法确定发生时间",
                                   fmt=fmt, field=f"events[{idx}].ts", index=idx,
                                   hint="事件流每条请带 ts（带时区的 ISO8601）")
        ts = _parse_ts(ts_raw, where="ts", fmt=fmt)
        latency = None
        if paired is not None:
            paired_ts_raw = paired.get("ts", paired.get("timestamp"))
            if paired_ts_raw is not None:
                latency = max(0, int((_parse_ts(paired_ts_raw, where="ts", fmt=fmt)
                                      - ts).total_seconds() * 1000))

        content_src = result.get("content")
        if not content_src and paired:
            content_src = _mapping(paired.get("result")).get("content")
        content = _list(content_src)
        summary = _summarize([_mapping(c).get("text", c) for c in content] or content, INTENT_LIMIT)
        if summary is None and is_tool_call and status == "succeeded":
            summary = f"来源上报成功，但未返回内容（工具 {tool_name or method}）"

        tool: dict[str, Any] | None = None
        if is_tool_call and tool_name:
            tool = {"name": tool_name,
                    "version": session_ctx.get("server_version"),
                    "provider": session_ctx.get("server_name"),
                    "call_id": _text(ev.get("id"), 200),
                    "params_summary": _summarize(params.get("arguments"))}
            tool = {k: v for k, v in tool.items() if v is not None}
        elif method in MCP_METHOD_BASE:
            tool = {"name": method, "version": session_ctx.get("server_version"),
                    "provider": session_ctx.get("server_name")}
            tool = {k: v for k, v in tool.items() if v is not None}

        error = None
        if status == "failed":
            code = rpc_error.get("code")
            message = _text(rpc_error.get("message"), 400)
            if is_error_result and not message:
                message = _summarize([_mapping(c).get("text") for c in content] or content, 400)
            detail_ref = (f"local://raw/{FORMAT_ADAPTER[fmt]}/"
                          f"{_ident(ev.get('id', idx), 'x')}/source.json")
            if log_failure and not rpc_error:
                data = _mapping(params.get("data"))
                log_code = _text(data.get("code"), 64)
                error = {"code": f"MCP_LOG_{log_code}" if log_code else "MCP_LOG_ERROR",
                         "human_reason": _text(data.get("message") or data.get("msg"), 400)
                         or _summarize(data, 400)
                         or "服务端上报了一条错误日志，但未给出说明；原始事件见 raw_ref",
                         "technical_detail_ref": detail_ref,
                         "retryable": False,
                         "failed_step": _text(params.get("logger") or method, 200)}
            else:
                error = {"code": f"MCP_RPC_{code}" if isinstance(code, int) else "MCP_TOOL_IS_ERROR",
                         "human_reason": message or "MCP 服务端返回错误，但未给出说明；原始响应见 raw_ref",
                         "technical_detail_ref": detail_ref,
                         "retryable": _truthy(_mapping(rpc_error.get("data")).get("retryable")),
                         "failed_step": _text(tool_name or method, 200)}

        risk = _risk_from(ext.get("risk_level", ext.get("risk.level")))
        impact = _impact_from(ext, fmt=fmt, obs_id=str(ev.get("id", idx)))
        confidence = _confidence(ext.get("confidence"))

        ev_id = ev.get("id")
        source_id = _ident(ev_id if ev_id is not None else f"{method}-{idx}", "x")

        intent = _text(ext.get("intent")) or (
            _clip(f"调用 MCP 工具 {tool_name}（{session_ctx.get('server_name') or '未声明服务端'}）",
                  INTENT_LIMIT)
            if is_tool_call and tool_name else
            _clip(f"MCP {method}：{params.get('logger') or tool_name or ''}".strip("："), INTENT_LIMIT))

        reason = _text(ext.get("requires_action_reason", ext.get("reason")), 300)
        if method == "notifications/cancelled" and not reason:
            reason = _text(params.get("reason"), 300)

        parent_raw = (params.get("relatedRequestToolName") or params.get("requestId")
                      or _mapping(_mapping(params.get("_meta")).get(
                          "io.modelcontextprotocol/related-request")).get("requestId"))
        out.append(NormalizedEvent(
            source_id=source_id, fmt=fmt, timestamp=ts,
            agent_id=ext.get("agent_id", session_ctx.get("server_name")),
            agent_name=ext.get("agent_name", session_ctx.get("server_name")),
            actor_id=ext.get("actor_id", ev.get("user_id")),
            workspace_id=ext.get("workspace_id", ev.get("workspace_id")),
            session_id=ext.get("session_id", ev.get("session_id")),
            run_id=ext.get("run_id", ev.get("request_id")),
            parent_source_id=(_ident(parent_raw, "x") if parent_raw is not None else None),
            action_type=action, tool=tool, intent=intent,
            rationale=_text(ext.get("rationale")),
            result_summary=summary,
            status=status, requires_action=requires,
            requires_action_reason=reason,
            action_due_at=(_parse_ts(ext.get("due_at"), where="tracelens.due_at", fmt=fmt)
                           if ext.get("due_at") else None),
            risk_level=risk, impact_scope=impact, impact_from_source=bool(impact),
            confidence=confidence,
            cost=None, latency_ms=latency, error=error,
            task_name=_text(ext.get("task_name") or session_ctx.get("server_name"), 200),
            trace_link={"trace_id": _text(ev.get("session_id"), 128), "span_id": None,
                        "observation_id": _text(ev.get("id"), 128),
                        "source_url": _text(ext.get("source_url"), 500)},
            input_keys=_input_keys(params.get("arguments")),
            synthetic=_synthetic(ext.get("synthetic")),
            raw=raw,
            archived_known=({"mcp_protocol_version": session_ctx["protocol_version"]}
                            if session_ctx.get("protocol_version") else {}),
        ))
    if not out:
        raise TraceFormatError("TRACE_MCP_NO_RECORDS",
                               "MCP 事件流里只有握手/心跳（initialize、notifications/initialized），"
                               "没有可作为动作的事件",
                               fmt=fmt, hint="请包含 tools/call、notifications/message 等业务事件")
    return out


# ----------------------------------------------------------------------------------
# 解析器 4：NativeActionList
# ----------------------------------------------------------------------------------

NATIVE_WRAPPER_KEYS = ("records", "action_records")


def parse_native(doc: Any, *, fmt: str = FORMAT_NATIVE) -> list[NormalizedEvent]:
    """原生 ActionRecord 数组：**逐字段原样搬运**（不改写事实），只做结构闸门。"""
    records: list[Any] = []
    top_unknown: dict[str, Any] = {}
    if isinstance(doc, list):
        records = list(doc)
    elif isinstance(doc, Mapping):
        for key in NATIVE_WRAPPER_KEYS:
            if isinstance(doc.get(key), list):
                records = list(doc[key])
                break
        else:
            if "schema_version" in doc and "action_type" in doc:
                records = [doc]
            else:
                raise TraceFormatError("TRACE_NATIVE_EMPTY",
                                       "识别为原生 ActionRecord 列表，但里面没有 records 数组",
                                       fmt=fmt, field=next(iter(sorted(doc)), None),
                                       hint='请用 {"records":[...]} 或直接给 ActionRecord 数组')
        top_unknown = {k: v for k, v in doc.items() if k not in NATIVE_WRAPPER_KEYS
                       and k not in ("_meta", "schema_version")}
    if not records:
        raise TraceFormatError("TRACE_NATIVE_EMPTY", "ActionRecord 数组是空的", fmt=fmt)

    out: list[NormalizedEvent] = []
    for idx, rec in enumerate(records):
        if not isinstance(rec, Mapping):
            raise TraceFormatError("TRACE_RECORD_TYPE",
                                   f"第 {idx + 1} 条不是对象，而是 {type(rec).__name__}",
                                   fmt=fmt, field=f"records[{idx}]", index=idx)
        version = rec.get("schema_version")
        if version != SCHEMA_VERSION:
            raise TraceFormatError("SCHEMA_VERSION_UNSUPPORTED",
                                   f"第 {idx + 1} 条的 schema_version={version!r}，本版本只支持 {SCHEMA_VERSION!r}",
                                   fmt=fmt, field=f"records[{idx}].schema_version", index=idx,
                                   hint="本版本不静默兼容旧 schema_version：请把记录手工升级到 SCHEMA_VERSION（1.1）后重新导入；仓库没有提供自动迁移脚本，不要指望 tools/ 里有现成的")
        rid = rec.get("id")
        if rid in (None, ""):
            raise TraceFormatError("TRACE_FIELD_MISSING", f"第 {idx + 1} 条缺少 id（幂等键）",
                                   fmt=fmt, field=f"records[{idx}].id", index=idx)
        _parse_ts(rec.get("timestamp"), where=f"records[{idx}].timestamp", fmt=fmt)
        action_type = rec.get("action_type")
        if action_type not in ACTION_TYPES:
            raise TraceFormatError("TRACE_ENUM_INVALID",
                                   f"第 {idx + 1} 条 action_type={action_type!r} 不在 16 类枚举内",
                                   fmt=fmt, field=f"records[{idx}].action_type", index=idx)
        status = rec.get("status")
        if status not in {"observed", "pending_confirmation", "approved", "modified", "rejected",
                          "running", "succeeded", "failed", "skipped", "expired"}:
            raise TraceFormatError("TRACE_ENUM_INVALID",
                                   f"第 {idx + 1} 条 status={status!r} 不在状态枚举内",
                                   fmt=fmt, field=f"records[{idx}].status", index=idx)
        for key in ("agent_id", "actor_id", "workspace_id", "session_id", "intent"):
            value = rec.get(key)
            if value in (None, ""):
                raise TraceFormatError("TRACE_FIELD_MISSING",
                                       f"第 {idx + 1} 条缺少必填字段 {key}（或为空）",
                                       fmt=fmt, field=f"records[{idx}].{key}", index=idx)
        if not isinstance(rec.get("policy"), Mapping):
            raise TraceFormatError("TRACE_FIELD_MISSING",
                                   f"第 {idx + 1} 条缺少 policy（超时行为必须显式声明）",
                                   fmt=fmt, field=f"records[{idx}].policy", index=idx)
        if not isinstance(rec.get("source"), Mapping):
            raise TraceFormatError("TRACE_FIELD_MISSING",
                                   f"第 {idx + 1} 条缺少 source（接入归因，N39）",
                                   fmt=fmt, field=f"records[{idx}].source", index=idx)
        record = json.loads(json.dumps(rec, ensure_ascii=False))  # 深拷贝，绝不改写入参
        out.append(NormalizedEvent(
            source_id=str(rid), fmt=fmt, native_record=record,
            raw=({"record_unknown_keys": sorted(set(record) -
                                                set(REQUIRED_RECORD_KEYS) -
                                                set(OPTIONAL_RECORD_KEYS))}
                 if (set(record) - set(REQUIRED_RECORD_KEYS) - set(OPTIONAL_RECORD_KEYS)) else {}),
            sequence_no=rec.get("sequence_no") if isinstance(rec.get("sequence_no"), int) else None,
            depth=rec.get("depth") if isinstance(rec.get("depth"), int) else None,
        ))
    if top_unknown:
        for ev in out:
            ev.raw.setdefault("list_wrapper_unknown_fields", top_unknown)
    return out


# ----------------------------------------------------------------------------------
# 记录装配（三条工程纪律在这里落地）
# ----------------------------------------------------------------------------------


def _build_record(ev: NormalizedEvent, *, source_kind: str, source_uri: str | None,
                  language: str, ingested_at: _dt.datetime, seq: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    lang = language if language in ("zh", "en") else "zh"
    adapter = FORMAT_ADAPTER[ev.fmt]

    if ev.native_record is not None:
        record = ev.native_record
        record.setdefault("received_at", _iso(ingested_at))
        if record.get("sequence_no") is None:
            record["sequence_no"] = ev.sequence_no if ev.sequence_no is not None else seq
        if record.get("depth") is None:
            record["depth"] = ev.depth
        raw_ref = _mapping(record.get("source")).get("raw_ref")
        raw_payload = None
        if ev.raw:
            raw_ref = raw_ref or f"local://raw/{adapter}/{record['id']}.json"
            record.setdefault("source", {})["raw_ref"] = raw_ref
            raw_payload = {"format": ev.fmt, "record_id": record["id"],
                           "adapter_version": ADAPTER_VERSION, **ev.raw}
        _assert_shape(record)
        return record, raw_payload

    record_id = _ident(ev.source_id, "x")
    tool = ev.tool
    if tool is not None:
        tool = {k: v for k, v in tool.items() if v is not None}
        # 工具名/提供方/调用 id 同样要脱敏：来源可能把真实路径、token 写进名字里，
        # 而隐私 lint 扫的是整条记录的每一个字符串。
        for key, limit in (("name", 120), ("version", 40), ("provider", 120), ("call_id", 200)):
            if isinstance(tool.get(key), str):
                tool[key] = _clip(_redact(tool[key]), limit)
        if not tool.get("name"):
            tool = None
    action_type = ev.action_type if ev.action_type in ACTION_TYPES else "other"
    status = ev.status if ev.status in {
        "observed", "pending_confirmation", "approved", "modified", "rejected",
        "running", "succeeded", "failed", "skipped", "expired"} else "observed"

    # C10：有副作用的动作必须有影响面；来源没给 → 指针型 + 保守 reversible=false
    impact = ev.impact_scope
    conservative_impact = False
    if impact is None and action_type in SIDE_EFFECT_TYPES:
        impact = {
            "targets": [{"kind": "resource",
                         "ref": f"source://{adapter}/{record_id}",
                         "change": CHANGE_BY_ACTION.get(action_type, "update")}],
            "money": None,
            "external_endpoint": None,
            "data_categories": ["internal"],
            "reversible": False,
            "summary": _clip("来源未提供影响面；按不可撤回处理（保守假设，请核对原始记录）", 300),
        }
        conservative_impact = True
    risk = ev.risk_level if ev.risk_level in RISK_LEVELS else "low"

    requires = bool(ev.requires_action)
    reason = _text(ev.requires_action_reason, 300)
    if requires and not reason:
        reason = ("来源把这一步标记为需要人工确认（"
                  + ("高危动作" if risk in ("high", "critical") else "需要人拍板")
                  + "），请决定同意、修改后同意或拒绝")[:300]
    if risk in ("high", "critical") and impact is not None and not ev.impact_from_source:
        conservative_impact = True

    options: list[dict[str, Any]] = []
    if requires:
        options = _default_options(ev, record_id, action_type, impact, lang)

    uncertainty = None
    notes: list[str] = []
    if conservative_impact:
        notes.append("来源未提供影响面与可撤回性，已按不可撤回处理")
    if risk == "low" and action_type in SIDE_EFFECT_TYPES:
        notes.append("来源未提供风险等级，风险按低处理（可在设置里配置风险规则）")
    if ev.confidence is None:
        notes.append("来源未提供置信度")
    if notes:
        uncertainty = {
            "reason": _clip("；".join(notes), 300),
            "what_to_check": _clip(f"原始记录（{ev.fmt}）与工具 {tool['name'] if tool else action_type}"
                                   f" 的参数/影响面说明", 300),
            "affected_fields": ["risk_level", "impact_scope"] if conservative_impact else ["confidence"],
        }
    if ev.confidence is not None and ev.confidence <= 0.6:
        uncertainty = {
            "reason": _clip(f"来源给出的置信度较低（{ev.confidence}）", 300),
            "what_to_check": _clip("核对原始记录里的入参与结果，再决定是否放行", 300),
            "affected_fields": ["intent", "result_summary", "risk_level"],
        }

    result_summary = _text(ev.result_summary, 1000)
    if result_summary is None and status == "succeeded":
        result_summary = f"来源上报为成功，但未提供结果内容（{adapter} 记录 {record_id}）"[:1000]

    error = ev.error
    if status == "failed" and error is None:
        error = {"code": "SOURCE_REPORTED_FAILURE",
                 "human_reason": f"来源上报为失败，但未提供原因（{adapter} 记录 {record_id}）",
                 "technical_detail_ref": f"local://raw/{adapter}/{record_id}/source.json",
                 "retryable": False, "failed_step": None}
    suggested = ev.suggested_next
    if status == "failed":
        if not suggested:
            suggested = [{"text": _clip("打开原始记录看细节，再决定重试、改参数或跳过", 200),
                          "kind": "other"}]

    label = ACTION_LABEL.get(action_type, ACTION_LABEL["other"])[lang]
    what = _clip(_redact(str((tool or {}).get("name") or action_type)), 24)
    status_label = STATUS_LABEL.get(status, STATUS_LABEL["observed"])[lang]
    l1 = _clip(f"{label}：{what}（{status_label}）", L1_LIMIT[lang])

    l3_parts: list[str] = []
    if tool and tool.get("name"):
        l3_parts.append(f"工具 {tool['name']}")
    if tool and tool.get("params_summary"):
        l3_parts.append(f"入参 {tool['params_summary']}")
    if ev.latency_ms is not None:
        l3_parts.append(f"耗时 {ev.latency_ms}ms")
    if ev.cost is not None:
        l3_parts.append(f"成本 {ev.cost['amount']} {ev.cost['currency']}"
                        + ("（约）" if ev.cost.get("estimated") and lang == "zh"
                           else (" (est.)" if ev.cost.get("estimated") else "")))
    l3_parts.append(f"来源 {ev.fmt}/{adapter}")
    l3 = _clip(" · ".join(l3_parts), L3_LIMIT)

    rationale_raw = _text(ev.rationale, 1000)
    intent_raw = _text(ev.intent, INTENT_LIMIT) or f"{adapter} 动作 {record_id}"
    # 记录里落地的 intent/rationale 先脱敏，事实指纹用**同一份**脱敏后的值（保证可重算）
    intent_value = _clip(_redact(intent_raw), INTENT_LIMIT)
    rationale_value = _clip(_redact(rationale_raw), 1000) if rationale_raw else None
    result_value = _clip(_redact(result_summary), 1000) if result_summary else None
    error_record = _redact_tree(error) if error else None
    facts = {
        "action_type": action_type,
        "confidence": ev.confidence,
        "cost": ev.cost,
        "error": error_record,
        "impact_scope": impact,
        "intent": intent_value,
        "latency_ms": ev.latency_ms,
        "rationale": rationale_value,
        "result_summary": result_value,
        "status": status,
    }
    facts_hash = "sha256:" + _sha256_canonical({"schema_version": SCHEMA_VERSION,
                                                "facts": facts})

    raw_ref: str | None = None
    raw_payload: dict[str, Any] | None = None
    if ev.raw or ev.archived_known:
        raw_ref = f"local://raw/{adapter}/{record_id}.json"
        raw_payload = {"format": ev.fmt, "record_id": record_id,
                       "adapter": adapter, "adapter_version": ADAPTER_VERSION,
                       "source_format_version": FORMAT_SPEC_VERSION[ev.fmt],
                       "source_format_full": FORMAT_SPEC_FULL[ev.fmt]}
        if ev.raw:
            raw_payload["unknown_fields"] = _redact_tree(ev.raw)
        if ev.archived_known:
            raw_payload["archived_known_fields"] = _redact_tree(ev.archived_known)

    synthetic = ev.synthetic
    demo = source_kind == "demo"
    if synthetic is None:
        synthetic = True  # 本项目所有夹具都是合成的（A1）；真实接入请由来源显式声明
    if demo:
        synthetic = True

    record = {
        "schema_version": SCHEMA_VERSION,
        "id": record_id,
        "timestamp": _iso(ev.timestamp or ingested_at),
        "received_at": _iso(ingested_at),
        "sequence_no": ev.sequence_no if ev.sequence_no is not None else seq,
        "agent_id": _ident(ev.agent_id, "unknown-agent", label="agent_id"),
        "agent_name": _text(ev.agent_name, 120),
        "actor_id": _ident(ev.actor_id, "local", label="actor_id"),
        "workspace_id": _ident(ev.workspace_id, "default", label="workspace_id"),
        "session_id": _ident(ev.session_id, f"sess-{adapter}", label="session_id"),
        "run_id": _text(ev.run_id, 128),
        "parent_action_id": (_ident(ev.parent_source_id, "x", label="parent_action_id")
                             if ev.parent_source_id not in (None, "") else None),
        "depth": ev.depth,
        "action_type": action_type,
        "tool_used": tool,
        "intent": intent_value,
        "rationale": rationale_value,
        "result_summary": result_value,
        "human_readable": {
            "l1": l1,
            "l2": [],
            "l3": l3,
            "language": lang,
            "generated_by": "template",
            "template_id": TEMPLATE_L1_ID,
            "model": None,
            "generated_at": _iso(ingested_at),
            "source_facts_hash": facts_hash,
            "may_contain_inference": False,
        },
        "confidence": ev.confidence,
        "uncertainty": uncertainty,
        "cost": ev.cost,
        "latency_ms": ev.latency_ms,
        "status": status,
        "requires_action": requires,
        "requires_action_reason": reason if requires else _text(ev.requires_action_reason, 300),
        "action_due_at": _iso(ev.action_due_at) if ev.action_due_at else None,
        "intervention_options": options,
        "user_intervention": None,
        "execution_receipt": {
            "state": "unreported",
            "reported_by": None,
            "reported_at": None,
            "detail": ("接入层只记录事实，执行端未回执" if lang == "zh"
                       else "adapter records facts only; runtime has not reported"),
            "sdk_version": None,
            "effect_notice": EFFECT_NOTICE_KEY,
        },
        "risk_level": risk,
        "impact_scope": impact,
        "policy": {
            "requires_human_confirmation": bool(requires or risk in ("high", "critical")),
            "auto_approve_forbidden": True,
            "timeout_behavior": "keep_pending",
            "queue_serial": True,
            "min_role_to_decide": "editor" if requires else "admin",
        },
        "error": error_record,
        "suggested_next": suggested or [],
        "context": {
            "task_name": _text(_redact(str(ev.task_name)), 200) if ev.task_name else None,
            "default_granularity": "L1",
            "user_language": lang,
        },
        "trace_link": {k: v for k, v in _mapping(ev.trace_link).items()
                       if k in ("trace_id", "span_id", "observation_id", "source_url")} or None,
        "source": {
            "kind": source_kind,
            "adapter": adapter,
            "format_version": _clip(FORMAT_SPEC_VERSION[ev.fmt], 40),
            "source_uri": _text(source_uri, 500),
            "ingested_at": _iso(ingested_at),
            "raw_ref": raw_ref,
        },
        "demo_data": demo,
        "synthetic": bool(synthetic),
        "labels": [f"format:{ev.fmt}", f"adapter:{adapter}"] + (["demo"] if demo else []),
    }
    record["context"] = {k: v for k, v in record["context"].items() if v is not None} or None
    record["labels"] = [x for x in record["labels"] if len(x) <= 40][:12]

    _assert_shape(record)
    return record, raw_payload


def _sha256_canonical(payload: Mapping[str, Any]) -> str:
    """与 `tools/facts_hash.py` 同一套算法（测试逐条交叉比对，证明两处一致）。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact_tree(obj: Any) -> Any:
    """归档原文也先脱敏（真实路径 / 密钥不得落盘，DM 第 9 节）。"""
    if isinstance(obj, str):
        return _redact(obj)
    if isinstance(obj, Mapping):
        return {k: _redact_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_tree(v) for v in obj]
    return obj


def _default_options(ev: NormalizedEvent, record_id: str, action_type: str,
                     impact: Mapping[str, Any] | None, lang: str) -> list[dict[str, Any]]:
    """通用三选项：文案由事实拼出；`editable_fields` 字段名来自来源入参（不编造字段）。"""
    fields: list[dict[str, Any]] = []
    for key, val in ev.input_keys[:8]:
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80] or "field"
        if isinstance(val, bool):
            ftype, default = "boolean", val
        elif isinstance(val, int):
            ftype, default = "integer", val
        elif isinstance(val, float):
            ftype, default = "number", val
        elif isinstance(val, str):
            ftype, default = ("text" if len(val) > 60 else "string"), _clip(_redact(val), 120)
        else:
            # 结构体入参：不猜形状，只给一个空的可填字段
            ftype, default = "text", None
        fields.append({"name": clean, "label": _clip(key, 60), "type": ftype,
                       "required": True, "default": default})
    if not fields:
        fields = [{"name": "params_note",
                   "label": _clip("参数说明（来源未提供入参）" if lang == "zh"
                                  else "Params note (source gave none)", 60),
                   "type": "text", "required": True, "default": None}]

    irreversible = bool(impact and impact.get("reversible") is False)
    approve = OPTION_TEMPLATE["approve"][lang]
    modify = OPTION_TEMPLATE["modify"][lang]
    reject = OPTION_TEMPLATE["reject"][lang]
    approve_consequence = approve["consequence"]
    if lang == "zh" and irreversible:
        approve_consequence = _clip(f"按原计划执行：{ACTION_LABEL[action_type]['zh']}这一步，"
                                    f"且完成后不可撤回", 200)
    elif lang == "en" and irreversible:
        approve_consequence = _clip(f"Run {ACTION_LABEL[action_type]['en']} as planned; "
                                    f"cannot be undone", 200)

    return [
        {"id": _ident(f"opt-approve-{record_id}", "opt-approve"), "kind": "approve",
         "label": _clip(approve["label"], 40), "description": None,
         "consequence": _clip(approve_consequence, 200),
         "effect_notice": EFFECT_NOTICE_KEY, "editable_fields": [],
         "requires_reason": False,
         "risk_note": _clip("本步骤完成后不可撤回", 200) if irreversible and lang == "zh"
         else (_clip("Not reversible after it runs", 200) if irreversible else None),
         "dangerous": False},
        {"id": _ident(f"opt-modify-{record_id}", "opt-modify"), "kind": "modify",
         "label": _clip(modify["label"], 40), "description": None,
         "consequence": _clip(modify["consequence"], 200),
         "effect_notice": EFFECT_NOTICE_KEY, "editable_fields": fields,
         "requires_reason": False, "risk_note": None, "dangerous": False},
        {"id": _ident(f"opt-reject-{record_id}", "opt-reject"), "kind": "reject",
         "label": _clip(reject["label"], 40), "description": None,
         "consequence": _clip(reject["consequence"], 200),
         "effect_notice": EFFECT_NOTICE_KEY, "editable_fields": [],
         "requires_reason": True,
         "risk_note": _clip("拒绝只记录决定；是否真的停下取决于执行端是否接入", 200) if lang == "zh"
         else _clip("Reject is recorded only; stopping needs runtime integration", 200),
         "dangerous": True},
    ]


def _assert_shape(record: Mapping[str, Any]) -> None:
    """适配器自检：字段封闭 + 必填齐全（违反 = 适配器 bug，抛可读错误而不是产出脏记录）。"""
    keys = set(record)
    missing = [k for k in REQUIRED_RECORD_KEYS if k not in keys]
    extra = sorted(keys - set(REQUIRED_RECORD_KEYS) - set(OPTIONAL_RECORD_KEYS))
    if missing or extra:
        raise TraceFormatError(
            "ADAPTER_OUTPUT_INVALID",
            "适配器产出的记录不符合 ActionRecord 字段封闭要求"
            + (f"：缺 {missing}" if missing else "")
            + (f"；多出 {extra}" if extra else ""),
            field="record", hint="这是适配器缺陷，请把这条记录报告给后端（不要手工改数据）")


# ----------------------------------------------------------------------------------
# 公开 API
# ----------------------------------------------------------------------------------

PARSERS = {
    FORMAT_LANGFUSE: parse_langfuse,
    FORMAT_OTEL: parse_otel,
    FORMAT_MCP: parse_mcp,
    FORMAT_NATIVE: parse_native,
}
SOURCE_KINDS = ("demo", "paste_json", "upload", "webhook", "sdk", "file_watch", "manual")


@dataclass(frozen=True)
class ConversionOutcome:
    """一次转换的完整结果：记录 + 未知字段归档 + 统计（K-03 接入成功率归因）。"""

    records: list[dict[str, Any]]
    raw_payloads: dict[str, dict[str, Any]]
    format: str
    stats: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"records": self.records, "raw_payloads": self.raw_payloads,
                "format": self.format, "stats": self.stats}


def convert_with_raw(payload: Any, *, format: str | None = None,
                     source_kind: str = "upload", source_uri: str | None = None,
                     language: str = "zh", strict: bool = True,
                     now: _dt.datetime | None = None) -> ConversionOutcome:
    """转换 + 返回未知字段归档。`strict=False` 时跳过坏记录并在 `stats.skipped` 里说明原因。"""
    if source_kind not in SOURCE_KINDS:
        raise TraceFormatError("TRACE_SOURCE_KIND_INVALID",
                               f"source.kind={source_kind!r} 不在 7 类接入方式内",
                               hint="/".join(SOURCE_KINDS))
    doc = _loads(payload)
    fmt = format if format else detect(doc)
    if fmt not in PARSERS:
        raise TraceFormatError("TRACE_FORMAT_UNSUPPORTED", f"不支持的格式：{fmt}", fmt=fmt)

    ingested = now or _dt.datetime.now(_dt.timezone.utc)
    events = PARSERS[fmt](doc, fmt=fmt)

    records: list[dict[str, Any]] = []
    raw_payloads: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    for seq, ev in enumerate(events):
        try:
            record, raw_payload = _build_record(ev, source_kind=source_kind,
                                                source_uri=source_uri, language=language,
                                                ingested_at=ingested, seq=seq)
        except TraceFormatError as exc:
            if strict:
                exc.index = seq
                raise
            skipped.append({"index": seq, "source_id": ev.source_id,
                            "code": exc.code, "reason": exc.reason})
            continue
        prior = seen_ids.get(record["id"])
        if prior is not None:
            skipped.append({"index": seq, "source_id": ev.source_id,
                            "code": "TRACE_DUPLICATE_ID",
                            "reason": f"id={record['id']} 与第 {prior + 1} 条重复，按幂等键丢弃"})
            continue
        seen_ids[record["id"]] = seq
        records.append(record)
        if raw_payload:
            pointer = _mapping(record.get("source")).get("raw_ref") \
                or f"local://raw/{FORMAT_ADAPTER[fmt]}/{record['id']}.json"
            raw_payloads[pointer] = raw_payload

    return ConversionOutcome(
        records=records, raw_payloads=raw_payloads, format=fmt,
        stats={"input_events": len(events), "converted": len(records),
               "skipped": len(skipped), "skip_reasons": skipped,
               "unknown_field_records": len(raw_payloads),
               "source_kind": source_kind, "language": language,
               "adapter_version": ADAPTER_VERSION,
               "format_spec_version": FORMAT_SPEC_VERSION[fmt],
               "format_spec_full": FORMAT_SPEC_FULL[fmt],
               "conversion_rate": (round(len(records) / len(events), 4) if events else 0.0)})


def convert_trace_to_action_record(payload: Any, *, format: str | None = None,
                                   source_kind: str = "upload",
                                   source_uri: str | None = None,
                                   language: str = "zh", strict: bool = True,
                                   now: _dt.datetime | None = None) -> list[dict[str, Any]]:
    """4 种输入 → ActionRecord 数组（任务点名的主 API）。

    非法输入 → `TraceFormatError`（人话原因 + 机器码）；每条记录字段封闭，
    未知字段请用 `convert_with_raw()` 取归档（`source.raw_ref` 是指针）。
    """
    return convert_with_raw(payload, format=format, source_kind=source_kind,
                            source_uri=source_uri, language=language, strict=strict,
                            now=now).records


#: 架构文档第 19 节契约别名：`trace_adapter.convert(doc, format)->list[ActionRecord]`
def convert(doc: Any, format: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    return convert_trace_to_action_record(doc, format=format, **kwargs)


def normalize_events_with_raw(batch: Any, *, source_kind: str = "webhook",
                              source_uri: str | None = None, language: str = "zh",
                              strict: bool = True,
                              now: _dt.datetime | None = None) -> ConversionOutcome:
    """t14 实时接入复用：一批事件（可混格式）→ 记录（按 `id` 去重）+ 归档 + 统计。"""
    ingested = now or _dt.datetime.now(_dt.timezone.utc)
    doc = _loads(batch)

    items: list[Any]
    # 整批若能整体识别为一种格式（Langfuse 导出、OTLP 资源、MCP 事件流、原生集合），
    # 必须整批解析：逐条拆开会丢掉文档级上下文（trace 身份、请求-响应配对、会话信封）。
    whole_ok = False
    if isinstance(doc, (Mapping, list)):
        try:
            detect(doc)
            whole_ok = True
        except TraceFormatError:
            whole_ok = False
    if whole_ok:
        items = [doc]
    elif isinstance(doc, Mapping):
        for key in ("events", "batch", "records", "observations", "spans",
                    "messages", "stream", "logs", "data"):
            if isinstance(doc.get(key), list):
                items = list(doc[key])
                break
        else:
            items = [doc]
    elif isinstance(doc, list):
        items = list(doc)
    else:
        raise TraceFormatError("TRACE_INPUT_TYPE",
                               f"批量事件必须是对象或数组，收到 {type(doc).__name__}",
                               fmt=source_kind)

    all_records: list[dict[str, Any]] = []
    raw_all: dict[str, dict[str, Any]] = {}
    skip_reasons: list[dict[str, Any]] = []
    formats_seen: list[str] = []
    for idx, item in enumerate(items):
        if isinstance(item, Mapping) and set(item) <= {"events", "batch", "records"}:
            # 批中批：拆开逐条处理，避免整批被当成一条记录
            inner = None
            for key in ("events", "batch", "records"):
                if isinstance(item.get(key), list):
                    inner = item[key]
                    break
            sub_items = inner or []
        else:
            sub_items = [item]
        for sub in sub_items:
            try:
                outcome = convert_with_raw(sub, source_kind=source_kind, source_uri=source_uri,
                                           language=language, strict=strict, now=ingested)
            except TraceFormatError as exc:
                if strict:
                    exc.index = idx
                    raise
                skip_reasons.append({"index": idx, "code": exc.code, "reason": exc.reason})
                continue
            formats_seen.append(outcome.format)
            raw_all.update(outcome.raw_payloads)
            for reason in outcome.stats["skip_reasons"]:
                skip_reasons.append({"index": idx, **reason})
            for record in outcome.records:
                if any(r["id"] == record["id"] for r in all_records):
                    skip_reasons.append({"index": idx, "source_id": record["id"],
                                         "code": "TRACE_DUPLICATE_ID",
                                         "reason": f"id={record['id']} 重复上报，已去重（幂等键）"})
                    continue
                all_records.append(record)

    unique_formats = sorted(set(formats_seen))
    label = (unique_formats[0] if len(unique_formats) == 1
             else ("mixed:" + "+".join(unique_formats) if unique_formats else "empty"))
    parsed = len(all_records) + len(skip_reasons)
    return ConversionOutcome(
        records=all_records, raw_payloads=raw_all, format=label,
        stats={"input_events": len(items), "converted": len(all_records),
               "skipped": len(skip_reasons), "skip_reasons": skip_reasons,
               "unknown_field_records": len(raw_all), "formats": sorted(set(formats_seen)),
               "conversion_rate": (round(len(all_records) / parsed, 4) if parsed else 0.0),
               "format_spec_version": "; ".join(FORMAT_SPEC_VERSION[f]
                                                for f in unique_formats
                                                if f in FORMAT_SPEC_VERSION) or None,
               "source_kind": source_kind, "language": language,
               "adapter_version": ADAPTER_VERSION})


def normalize_events(batch: Any, *, source_kind: str = "webhook",
                     source_uri: str | None = None, language: str = "zh",
                     strict: bool = True,
                     now: _dt.datetime | None = None) -> list[dict[str, Any]]:
    """webhook/SDK 一批事件 → 记录数组（重复 `id` 100% 去重，不产生重复卡片）。"""
    return normalize_events_with_raw(batch, source_kind=source_kind, source_uri=source_uri,
                                     language=language, strict=strict, now=now).records


__all__ = [
    "SCHEMA_VERSION", "ADAPTER_NAME", "ADAPTER_VERSION",
    "SUPPORTED_FORMATS", "FORMAT_SPEC_VERSION", "FORMAT_LANGFUSE", "FORMAT_OTEL",
    "FORMAT_MCP", "FORMAT_NATIVE", "ACTION_TYPES", "SIDE_EFFECT_TYPES",
    "ACTION_KEYWORDS", "EXT_NAMESPACE", "REQUIRED_RECORD_KEYS", "OPTIONAL_RECORD_KEYS",
    "FACT_FIELDS", "TraceFormatError", "NormalizedEvent", "ConversionOutcome",
    "detect", "detect_format", "convert_trace_to_action_record", "convert_with_raw",
    "convert", "normalize_events", "normalize_events_with_raw",
    "parse_langfuse", "parse_otel", "parse_mcp", "parse_native",
    "classify_action", "_redact",
]

# 兼容别名（架构文档写的是 detect(bytes)->format）
detect_format = detect
