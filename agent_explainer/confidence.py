"""5.0 置信度策略层（REQ-002 / R2-1）：给"置信度"这个数装上来源、颜色带与复算口径。

一句话：置信度不是拍脑袋的数字——来源必须可追溯（schema 的 ``confidence_source``），
颜色带由本模块的 ``THRESHOLDS`` 定义（schema 只保证"来源形状对"，阈值不在 schema 里），
来源没给的东西一律不许编造。

五条口径（docs/07 §4.6 的函数契约 + ``docs/schemas/action_record.schema.json`` 的机器事实）：

1. **只在签名前写**：``confidence`` 在 ``SIGNATURE_FIELDS``（``secretariat.py:77-80``，11 字段）
   里，所以置信度只能在导入侧、``sign_on_write_record`` 之前固化（:func:`fixate_for_import`）。
   本模块**不提供任何运行期改判入口**：运行期要改判，走既有的追加补偿记录
   （intervention / rollback），不在这里改一条已签名的历史行。
2. **零来源 = null，不是 1.0**：来源什么都没给 → ``confidence`` 维持 ``null``
   （schema「缺失 = null」既有口径），``confidence_source`` 如实标 ``{method: "default"}``；
   界面不得把这种记录里的数字讲成肯定。``DEFAULT_CONFIDENCE = 1.0`` 只服务于服务端
   **生成型**记录（``rollback.py`` 既有口径：``confidence=1.0`` + ``method="template"``），
   绝不用来给"来源没给"兜底。
3. **禁止编造 logprob**：来源没给 logprobs，:func:`from_logprobs` 返回 ``confidence=None``
   且 ``source.logprob=None``；缺一路来源就把那路填 ``null``，绝不把"没数据"讲成"很差"。
4. **枚举与键不发明**：``confidence_source.method`` 只用 schema 的五个值
   （``logprobs`` / ``tool_success_rate`` / ``hybrid`` / ``template`` / ``default``）；
   ``confidence_source`` 的键只用 schema 允许的那六个（``additionalProperties: false``）。
   来源给了个不认识的方法名（如 ``human``）→ 判为"来源不可判"，退回 ``default`` 形态，
   不替它背书、也不静默改成别的枚举值。
5. **边界精确值**（REQ-002 §核心逻辑 3）：``≥0.9`` green、``[0.7,0.9)`` light_green、
   ``[0.5,0.7)`` yellow、``<0.5`` red；``None`` / 越界 / 非数值 → ``unknown`` / ``neutral``。
   颜色带映射到语义色 token：``ok`` / ``ok_soft`` / ``progress`` / ``danger`` / ``neutral``。

任务边界（R2-1 建策略层，R2-2 接联动）：

* ``policy()`` 只返回模块级 dict——``GET /api/confidence/policy`` 的端点接线不在本任务；
* ``<0.5 → requires_action`` 的**导入联动**自 R2-2 起接进 :func:`fixate_for_import`
  （即导入固化 = 固化 + 联动，同一个签名前入口，见 :func:`apply_to_record` 的翻牌边界）；
* 联动只作用于**本轮新建 / 新合并的产物**：已落盘的历史行不回填（历史行是当时的事实，
  不是现状断言），也不提供任何运行期改判入口。

离线纯度：只用标准库，不出网、不起进程（与 ``tests/test_trace_adapter.py::OfflinePurityTests`` 同口径）。
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

__all__ = [
    "CONFIDENCE_VERSION", "THRESHOLDS", "DEFAULT_CONFIDENCE",
    "BANDS", "COLOR_BY_BAND", "BAND_UNKNOWN", "COLOR_NEUTRAL",
    "METHODS", "SOURCE_KEYS", "ACTION_TRIGGER", "UNCERTAINTY_REQUIRED_AT_OR_BELOW",
    "PRECISION", "band", "color", "normalize", "from_logprobs", "combine",
    "apply_to_record", "policy", "explain", "fixate_for_import", "is_valid_source",
]

#: 本模块的口径版本（与产品版本、数据契约版本各自独立，同 ``SECRETARIAT_VERSION`` 的写法）
CONFIDENCE_VERSION = "1.0"
#: 颜色带阈值（docs/07 §4.6 逐字；schema 里没有阈值，只有"来源形状"）
THRESHOLDS: dict[str, float] = {"green": 0.9, "light_green": 0.7, "yellow": 0.5, "red": 0.0}
#: 服务端生成型记录的默认置信度（``rollback.py`` 既有口径）。**不用于给"来源没给"兜底**。
DEFAULT_CONFIDENCE = 1.0

BANDS: tuple[str, ...] = ("green", "light_green", "yellow", "red")
BAND_UNKNOWN = "unknown"
COLOR_NEUTRAL = "neutral"
#: band → 语义色 token（界面按 token 取主题色，不硬编码十六进制）
COLOR_BY_BAND: dict[str, str] = {
    "green": "ok", "light_green": "ok_soft", "yellow": "progress", "red": "danger",
    BAND_UNKNOWN: COLOR_NEUTRAL,
}
#: 逐字等于 schema ``confidence_source.method`` 的枚举（``:1338-1345``）。禁止发明新值。
METHODS: tuple[str, ...] = ("logprobs", "tool_success_rate", "hybrid", "template", "default")
#: 逐字等于 schema ``confidence_source.properties``（``additionalProperties: false``）。
SOURCE_KEYS: tuple[str, ...] = ("method", "logprob", "tool_success_rate", "samples",
                                "computed_at", "weights")
#: 触发干预的下界：``< 0.5`` 即 red 带（REQ-002 §核心逻辑 3）。与 ``THRESHOLDS["yellow"]`` 同值。
ACTION_TRIGGER = THRESHOLDS["yellow"]
#: schema C7（``:1836-1859``）：``0 < confidence ≤ 0.6`` 必须带 ``uncertainty``。
UNCERTAINTY_REQUIRED_AT_OR_BELOW = 0.6
#: 归一化精度：与 ``trace_adapter._confidence`` 的 ``round(x, 4)`` 同口径 ⇒ 幂等，
#: 固化一步不会改变适配器已经算好的数（事实指纹 ``source_facts_hash`` 因此不会漂）。
PRECISION = 4

#: 只有这两种状态在"等你拍板"段（schema C1）；running/observed 可被转成待确认
DECIDABLE_STATUSES: tuple[str, str] = ("pending_confirmation", "expired")
FLIPPABLE_STATUSES: tuple[str, str, str, str] = ("pending_confirmation", "expired",
                                                 "running", "observed")
EFFECT_NOTICE = "intervention.effect.recorded_only"

_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?([Zz]|[+-][0-9]{2}:[0-9]{2})$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# ---------------------------------------------------------------------------
# 小工具（口径与 ingest.py / trace_adapter.py 逐字对齐：不夹紧、不编造）
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    moment = moment or _utc_now()
    return (moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _clip(text: Any, limit: int) -> str:
    body = str(text if text is not None else "")
    return body if len(body) <= int(limit) else (body[: max(1, int(limit) - 1)] + "…")


def _number(value: Any) -> float | None:
    """把入参折成 0–1 的 float；判不出来就返回 ``None``。

    三条硬口径：

    * ``bool`` 不是置信度（``True`` 绝不等于 1.0）→ ``None``；
    * 数值字符串（``"0.82"``）按 ``trace_adapter._confidence`` 同口径接受；
    * **越界不夹紧**：``-0.2`` / ``1.5`` / ``NaN`` / ``inf`` → ``None``。
      夹紧等于替来源编一个它没给的数（§1.6 展示纪律）。
    """
    if value is None or isinstance(value, bool):
        return None
    num: float | None = None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            return None
    if num is None or math.isnan(num) or math.isinf(num):
        return None
    if num < 0.0 or num > 1.0:
        return None
    return round(num, PRECISION)


def _as_int(value: Any) -> int | None:
    """样本数：非负整数才认（``bool`` 不算整数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _method(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text if text in METHODS else None


def _is_timestamp(value: Any) -> bool:
    return value is None or (isinstance(value, str) and bool(_TIMESTAMP_RE.match(value)))


def _weights(values: Any, *, fallback: tuple[float, float] = (0.7, 0.3)) -> tuple[float, float]:
    """权重归一：两个非负数且和 > 0 → 归一化；否则退回默认 (0.7, 0.3)（如实落盘）。"""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return fallback
    if len(values) != 2:
        return fallback
    parts: list[float] = []
    for item in values:
        if isinstance(item, bool):
            return fallback
        try:
            num = float(item)
        except (TypeError, ValueError):
            return fallback
        if math.isnan(num) or math.isinf(num) or num < 0.0:
            return fallback
        parts.append(num)
    total = parts[0] + parts[1]
    if total <= 0.0:
        return fallback
    return (round(parts[0] / total, PRECISION), round(parts[1] / total, PRECISION))


def _source(method: str, *, logprob: float | None = None,
            tool_success_rate: float | None = None, samples: int | None = None,
            weights: Sequence[float] | None = None,
            computed_at: str | None = None) -> dict[str, Any]:
    """造一个 schema 形态合规的 ``confidence_source``（键只用 schema 那六个）。

    ``default`` 形态按 §2.4 逐字四键（不带 ``computed_at``）：``method`` + 三个 ``null``。
    """
    body: dict[str, Any] = {"method": method, "logprob": logprob,
                            "tool_success_rate": tool_success_rate, "samples": samples}
    if method != "default" or computed_at is not None:
        body["computed_at"] = computed_at if computed_at is not None else _iso()
    if weights is not None:
        body["weights"] = [round(float(w), PRECISION) for w in list(weights)[:4]]
    return body


def is_valid_source(source: Any) -> bool:
    """这个 ``confidence_source`` 是否已符合 schema 形态（R2-3 展示层与测试都用这条）。"""
    if not isinstance(source, Mapping):
        return False
    if _method(source.get("method")) is None:
        return False
    for key in source:
        if key not in SOURCE_KEYS:
            return False
    for key in ("logprob", "tool_success_rate"):
        if key in source and source[key] is not None and _number(source[key]) is None:
            return False
    if "samples" in source and source["samples"] is not None:
        if _as_int(source["samples"]) is None:
            return False
    if "computed_at" in source and not _is_timestamp(source["computed_at"]):
        return False
    if "weights" in source:
        weights = source["weights"]
        if weights is not None:
            if isinstance(weights, (str, bytes)) or not isinstance(weights, Sequence):
                return False
            if len(weights) > 4:
                return False
            for item in weights:
                if _number(item) is None:
                    return False
    return True


def _keep_source(source: Any) -> dict[str, Any] | None:
    """来源自己给的形态：合规就**逐字保留**（不追加键、不改值），不合规返回 ``None``。

    逐字保留是刻意的：导入面已有测试钉"入参的 ``confidence_source`` 原样送达"，
    加一个键（哪怕 ``computed_at``）都是把别人给的事实改写。
    """
    return dict(source) if is_valid_source(source) else None


def _resolve_source(record: Mapping[str, Any], conf: float | None) -> dict[str, Any]:
    """来源形态的取值三层，逐层都不编造：

    1. 来源自带的形态合 schema → **逐字保留**（不追加键、不改值）；
    2. 来源给了个形态但不合 schema（``method`` 在枚举外、``logprob`` 越界、多出来的键……）
       → 判为「来源不可判」，退回 ``default`` 四键：不发明枚举值，也不替伪造值背书；
    3. 来源压根没给形态（缺键 / ``null``）→ 有数就按 §2.3 映射标方法，没数就是 ``default``。
    """
    given = record.get("confidence_source")
    kept = _keep_source(given)
    if kept is not None:
        return kept
    if isinstance(given, Mapping):
        return _source("default")
    if conf is None:
        return _source("default")
    rate = _number(record.get("tool_success_rate"))
    return _source(_method_for_given_value(record), tool_success_rate=rate)


def _method_for_given_value(record: Mapping[str, Any]) -> str:
    """来源显式给了一个置信度数、但没有任何可追溯来源形态时的方法名。

    按 §2.3 的映射：logprob→``logprobs``；rule→``tool_success_rate`` / ``template``；
    default→``default``。这里既没有 logprobs，也没有工具成功率统计，只有"来源自己给的数"
    （规则给定值）→ 取 ``template``（与 ``rollback.py`` 表示"值由模板/外部给定"同口径）。
    取 ``logprobs`` 会是伪造模型证据，取 ``tool_success_rate`` 会是伪造本地统计，
    取 ``default`` 又与"来源确实给了数"矛盾——三条都过不去，只有 ``template`` 说得清。
    """
    rate = record.get("tool_success_rate")
    return "tool_success_rate" if _number(rate) is not None else "template"


# ---------------------------------------------------------------------------
# 契约函数（docs/07 §4.6 逐名实现）
# ---------------------------------------------------------------------------


def band(value: Any) -> str:
    """置信度 → 颜色带：``green`` / ``light_green`` / ``yellow`` / ``red`` / ``unknown``。

    边界精确值：``0.9``→green、``0.7``→light_green、``0.5``→yellow、``0.4999``→red；
    ``None`` / 越界（``1.5``、``-0.1``）/ 非数值 → ``unknown``。
    """
    num = _number(value)
    if num is None:
        return BAND_UNKNOWN
    if num >= THRESHOLDS["green"]:
        return "green"
    if num >= THRESHOLDS["light_green"]:
        return "light_green"
    if num >= THRESHOLDS["yellow"]:
        return "yellow"
    return "red"


def color(value: Any) -> str:
    """置信度 → 语义色 token：``ok`` / ``ok_soft`` / ``progress`` / ``danger`` / ``neutral``。"""
    return COLOR_BY_BAND.get(band(value), COLOR_NEUTRAL)


def normalize(value: Any, *, source: str = "default") -> float | None:
    """校验并归一置信度到 ``0–1`` 的 float；判不出来 → ``None``（schema「缺失 = null」）。

    ``source`` 只是"这个数从哪来"的标注（``METHODS`` 之一），**不改变判定结果**：
    同一个数不因来源而变成另一个数。非法的 ``source`` 值按 ``default`` 看待，
    判定照旧如实（判不出就是 ``None``，绝不返回 ``DEFAULT_CONFIDENCE`` 兜底）。
    """
    if _method(source) is None:
        # 来源标注不认识：不改数字，只按"来源不可判"看待（返回口径不变）
        pass
    return _number(value)


def from_logprobs(logprobs: Any, *, tokens: Any = None) -> dict[str, Any]:
    """logprobs → ``{"confidence": float | None, "source": {...}}``（method="logprobs"）。

    入参形态（"来源给什么就用什么"，缺就 ``None``）：

    * **序列**：逐 token 的对数概率。``v ≤ 0`` 视为 ln 概率 → ``exp(v)``；
      ``0 < v ≤ 1`` 视为来源已折算好的概率（logprob 不可能为正）；``v > 1`` 视为非法值丢掉。
      置信度 = 接受值的算术平均（``mean(exp(v))``）。
    * **Mapping**：``{"logprobs": [...], "tokens": [...]}``（缺键 = 没给）；
      也接受 ``{"confidence": 0.83}``（来源已折算好的单个数）。
    * **单个数值**：``0–1`` 视为已折算的置信度；负数视为单个 logprob → ``exp(v)``。

    ``samples`` = 有 token 清单时取清单长度，否则取参与统计的概率个数。
    **禁止编造**：一个都拿不到 → ``confidence=None`` + ``method="default"`` 形态
    （schema：logprob 缺失 = 来源没给 logprobs）。
    """
    names: Any = tokens
    raw: Any = None
    hint: Any = None
    if isinstance(logprobs, Mapping):
        raw = logprobs.get("logprobs")
        if names is None:
            names = logprobs.get("tokens")
        hint = logprobs.get("confidence")
    elif isinstance(logprobs, (list, tuple)):
        raw = logprobs
    else:                                   # 单个数值：来源已折算好，或就是一个 logprob
        hint = logprobs

    probs = _fold_logprobs(raw)
    if not probs:
        single = _fold_single(hint)
        if single is None:
            # 来源没给 logprobs：如实 None，不补数
            return {"confidence": None, "source": _source("default")}
        return {"confidence": single,
                "source": _source("logprobs", logprob=single,
                                  samples=_sample_count(names, 1), computed_at=_iso())}
    conf = round(sum(probs) / len(probs), PRECISION)
    return {"confidence": conf,
            "source": _source("logprobs", logprob=conf,
                              samples=_sample_count(names, len(probs)), computed_at=_iso())}


def _fold_single(value: Any) -> float | None:
    """单个数的口径：``0–1`` 视为已折算的置信度；负数视为单个 logprob → ``exp(v)``。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return _number(value)
    if not isinstance(value, (int, float)):
        return None
    num = float(value)
    if math.isnan(num) or math.isinf(num):
        return None
    if num < 0.0:
        return round(max(0.0, min(1.0, math.exp(num))), PRECISION)
    return _number(num)


def _fold_logprobs(raw: Any) -> list[float]:
    """把 logprobs 序列折成 0–1 的概率列表（非法值丢掉，不夹紧、不补数）。"""
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return []
    out: list[float] = []
    for item in raw:
        if isinstance(item, Mapping):                       # OpenAI 形态：{"logprob": -0.2}
            item = item.get("logprob", item.get("log_probs", item.get("probability")))
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        value = float(item)
        if math.isnan(value) or math.isinf(value):
            continue
        if value <= 0.0:
            out.append(max(0.0, min(1.0, math.exp(value))))
        elif value <= 1.0:
            out.append(value)                               # 来源已折算好的概率
        # value > 1：logprob 与概率都不可能 > 1 → 丢掉这个值
    return out


def _sample_count(names: Any, fallback: int) -> int:
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        return int(fallback)
    return len(list(names))


def combine(logprob_conf: Any, tool_success_rate: Any, *,
            weights: Sequence[float] = (0.7, 0.3)) -> dict[str, Any]:
    """两路来源加权合成 → ``{"confidence": float | None, "source": {...}}``。

    三条口径（都不许把"没数据"讲成"很差"）：

    * 两路都有 → 加权平均，``method="hybrid"``，``weights`` 落归一化后的副本（schema 说明：
      便于事后复算）；
    * 只有一路 → 就用那一路（``logprobs`` / ``tool_success_rate``），**另一路填 null**，
      不给缺失的那路补 0 分；
    * 两路都没有 → ``confidence=None`` + ``method="default"``（禁止编造）。

    ``weights`` 非法（非两元 / 负数 / 全 0 / ``bool``）→ 退回默认 ``(0.7, 0.3)`` 并如实落盘。
    """
    a = _number(logprob_conf)
    b = _number(tool_success_rate)
    w1, w2 = _weights(weights)
    if a is None and b is None:
        return {"confidence": None, "source": _source("default")}
    if a is not None and b is not None:
        conf = round(a * w1 + b * w2, PRECISION)
        return {"confidence": conf,
                "source": _source("hybrid", logprob=a, tool_success_rate=b,
                                  weights=(w1, w2), computed_at=_iso())}
    if a is not None:
        return {"confidence": a,
                "source": _source("logprobs", logprob=a, computed_at=_iso())}
    return {"confidence": b,
            "source": _source("tool_success_rate", tool_success_rate=b, computed_at=_iso())}


def _options_for(record: Mapping[str, Any], conf: float, *, risk_note: str) -> list[dict[str, Any]]:
    """低置信时补齐的三条决定选项（与 ``ingest.default_intervention_options`` 同形、同文案口径）。

    本模块不 import ``ingest``（``ingest`` 在模块级 import 本模块，反向 import 就是循环依赖），
    所以这里是**逐字等价**的第二份；形状由 ``tests/test_confidence_policy.py`` 钉 schema。
    """
    rid = str(record.get("id") or "")
    rid = rid if _ID_RE.match(rid) else "record"
    short = _clip(record.get("intent") or record.get("tool_used") or rid, 40)
    return [
        {"id": f"opt-{rid}-approve", "kind": "approve", "label": "同意执行",
         "description": f"按来源声明的范围执行「{short}」",
         "consequence": _clip(f"来源声明将执行「{short}」；TraceLens 只记录你的决定，"
                              f"是否生效以执行端回执为准", 200),
         "effect_notice": EFFECT_NOTICE, "editable_fields": [],
         "requires_reason": False, "risk_note": risk_note, "dangerous": False},
        {"id": f"opt-{rid}-modify", "kind": "modify", "label": "改范围后同意",
         "description": "只执行你设定的范围",
         "consequence": "只执行你设定的范围，其余保持原样并在清单标注已跳过",
         "effect_notice": EFFECT_NOTICE,
         # schema 的 interventionOption 要求 kind=modify 必带 ≥1 个可编辑字段定义
         # （界面按它渲染表单，US-04）；来源没给字段清单时，只能给出"改写执行范围"这一项，
         # 不能凭空发明可改参数。
         "editable_fields": [{"name": "scope", "label": "执行范围（只跑你写下的范围）",
                              "type": "text", "required": True,
                              "help_text": _clip(f"来源声明的范围：{short}；改写后只执行你写下的范围，"
                                                 f"其余步骤保持原样并标注已跳过", 200)}],
         "requires_reason": False,
         "risk_note": _clip(f"置信度 {conf}（低置信）：改写只影响执行范围，"
                            f"不改变来源已记录的事实", 200),
         "dangerous": False},
        {"id": f"opt-{rid}-reject", "kind": "reject", "label": "先不执行",
         "description": "停下来，等你决定下一步",
         "consequence": "跳过这一步，来源侧需要自行处理后续",
         "effect_notice": EFFECT_NOTICE, "editable_fields": [],
         "requires_reason": True, "risk_note": None, "dangerous": True},
    ]


def _options_are_enough(options: Any) -> bool:
    """来源自带的三条决定选项是否"够用"——够 = 翻完 ``requires_action`` 之后仍是 schema 合法记录。

    只数 kind 是不够的（R2-2 前的口径），三条都得核对：

    * C1：``intervention_options`` ≥3 条，含 ``approve`` / ``modify`` / ``reject`` 三种 kind；
    * ``$defs/interventionOption``：``id`` / ``kind`` / ``label`` / ``consequence`` 是必填，
      ``consequence`` 还要 ≥4 字（"点这个会发生什么"是 T5 锚点，空串等于没有）；
    * 同一段的 ``allOf``：``kind=modify ⇒ editable_fields`` 必须存在且 ``minItems: 1``
      （界面按它渲染改写表单，US-04）——``editable_fields: []`` 的 modify 选项点下去是一张
      没有表单的空卡片，这种"看得见、点不动"的选项集必须换成合规的三条。
    """
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        return False
    items = [o for o in options if isinstance(o, Mapping)]
    if len(items) < 3:
        return False
    by_kind: dict[str, Mapping[str, Any]] = {}
    for opt in items:
        if not str(opt.get("id") or "").strip() or not str(opt.get("label") or "").strip():
            return False
        if len(str(opt.get("consequence") or "").strip()) < 4:
            return False
        by_kind.setdefault(str(opt.get("kind") or ""), opt)
    if not {"approve", "modify", "reject"} <= set(by_kind):
        return False
    fields = by_kind["modify"].get("editable_fields")
    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        return False
    return len(fields) >= 1


def _ensure_uncertainty(data: dict[str, Any], conf: float) -> None:
    """低置信必须有 ``uncertainty``（schema C7 + 展示纪律：禁止把不确定讲成肯定）。"""
    current = data.get("uncertainty")
    if isinstance(current, Mapping) and str(current.get("reason") or "").strip():
        return
    method = str((data.get("confidence_source") or {}).get("method")
                 if isinstance(data.get("confidence_source"), Mapping) else "default")
    data["uncertainty"] = {
        "reason": _clip(f"来源给出的置信度较低（{conf}，来源方式 {method}）；"
                        f"这个数不代表结果已核实", 300),
        "what_to_check": _clip("核对来源原始记录与工具返回，再决定放行、追问或拒绝", 300),
        "affected_fields": ["confidence"],
    }


def _request_action(data: dict[str, Any], conf: float) -> bool:
    """``<0.5`` → 要人拍板：``requires_action`` + 理由 + 三条决定选项（schema C1 全套）。

    返回是否真的翻了。三条码住不动的边界（翻了会造出 schema 非法 / 点不动的假卡片）：

    * 已有决定（``user_intervention`` 非空）→ 不翻：C1 要求此时 ``user_intervention`` 为 null；
    * 状态不在 ``FLIPPABLE_STATUSES``（已结束）→ 不翻：已结束的步骤"要人确认"是假的可点卡片；
    * 翻的时候必须把 C1 要求的配套字段一起补齐（status / reason / policy / 三条选项）。
    """
    if data.get("user_intervention") is not None:
        return False
    status = str(data.get("status") or "")
    if status not in FLIPPABLE_STATUSES:
        return False
    data["requires_action"] = True
    data["status"] = "expired" if status == "expired" else "pending_confirmation"
    reason = str(data.get("requires_action_reason") or "").strip()
    if len(reason) < 4:
        method = str((data.get("confidence_source") or {}).get("method")
                     if isinstance(data.get("confidence_source"), Mapping) else "default")
        reason = (f"置信度低于 {ACTION_TRIGGER}（当前 {conf}，来源方式 {method}），"
                  f"已按低置信要求人工确认：请核对来源原始记录后决定同意、改范围后同意或拒绝")
    data["requires_action_reason"] = _clip(reason, 300)
    policy_body = data.get("policy")
    if isinstance(policy_body, Mapping):
        merged = dict(policy_body)
        merged["requires_human_confirmation"] = True
        data["policy"] = merged
    else:
        data["policy"] = {"requires_human_confirmation": True, "auto_approve_forbidden": True,
                          "timeout_behavior": "keep_pending", "queue_serial": True,
                          "min_role_to_decide": "editor"}
    if data.get("user_intervention") is not None:      # 防御：C1 要求此时为 null
        data["user_intervention"] = None
    if not _options_are_enough(data.get("intervention_options")):
        data["intervention_options"] = _options_for(data, conf,
                                                    risk_note=_clip(f"置信度 {conf}（低置信）", 200))
    _ensure_uncertainty(data, conf)
    return True


def apply_to_record(record: Any, *, computed: Any = None) -> dict[str, Any]:
    """把置信度与来源写进记录：设 ``confidence`` / ``confidence_source``；
    ``< 0.5`` 时 ``requires_action=True`` 并补 ``reason`` 与三条选项（docs/07 §4.6 契约）。

    入参零改写：返回深拷贝出来的新记录（同 S0 组装器口径）。

    取值优先级（"来源给过什么就用什么"，一层层往下退，每层都不编造）：

    1. ``computed``——:func:`from_logprobs` / :func:`combine` 的产出
       （``{"confidence": ..., "source": {...}}``）；
    2. 记录自带的 ``confidence`` + 合规的 ``confidence_source``（逐字保留）；
    3. 来源显式给了数、但没有来源形态 → 按 §2.3 映射标方法（``template`` / ``tool_success_rate``）；
    4. 什么都没给 → ``confidence=null`` + ``{method: "default"}``（§2.4 口径）。
    """
    if not isinstance(record, Mapping):
        raise TypeError("apply_to_record 需要一个记录对象（Mapping）")
    data = copy.deepcopy(dict(record))
    comp: Mapping[str, Any] = computed if isinstance(computed, Mapping) else {}

    conf = _number(comp.get("confidence")) if comp else None
    source = _keep_source(comp.get("source")) if comp else None
    if conf is None:
        conf = _number(data.get("confidence"))
    if source is None:
        source = _resolve_source(data, conf)

    data["confidence"] = conf
    data["confidence_source"] = source
    if conf is not None:
        flipped = conf < ACTION_TRIGGER and _request_action(data, conf)
        if not flipped and conf <= UNCERTAINTY_REQUIRED_AT_OR_BELOW:
            _ensure_uncertainty(data, conf)
    return data


def policy() -> dict[str, Any]:
    """模块级策略字典（供未来的 ``GET /api/confidence/policy``；本任务不接 HTTP 路由）。"""
    return {
        "ok": True,
        "version": CONFIDENCE_VERSION,
        "thresholds": dict(THRESHOLDS),
        "bands": [{"band": name, "color": COLOR_BY_BAND[name],
                   "min": THRESHOLDS[name],
                   "max": None if name == "green" else THRESHOLDS[name],
                   "explain_zh": explain(_BAND_SAMPLE[name]),
                   "explain_en": explain(_BAND_SAMPLE[name], lang="en")}
                  for name in BANDS],
        "unknown_band": {"band": BAND_UNKNOWN, "color": COLOR_NEUTRAL},
        "default_confidence": DEFAULT_CONFIDENCE,
        "default_confidence_scope": "仅服务端生成型记录（rollback 形态）；来源没给时维持 null",
        "methods": list(METHODS),
        "source_keys": list(SOURCE_KEYS),
        "action_trigger": ACTION_TRIGGER,
        "uncertainty_required_at_or_below": UNCERTAINTY_REQUIRED_AT_OR_BELOW,
        "precision": PRECISION,
        "display_discipline": {
            "method_default": "来源不可判：显示为『未知』（neutral 中性色），不得把数字讲成肯定",
            "low_confidence": "低于 0.6 必须显示『不确定』并说明要看什么（schema C7）",
            "fabrication": "禁止编造 logprob：来源没给就是 null",
        },
        "wiring": {
            "fixation_point": "导入侧、签名之前（sign_on_write_record 之前）："
                              "ingest.make_event / IngestBroker.ingest / server.import_traces",
            "runtime_override": None,
            "runtime_override_note": "运行期改判走追加补偿记录（intervention / rollback），"
                                     "本模块不提供改判入口",
            "http_endpoint": None,
            "http_endpoint_note": "R2-1 边界：只返回本 dict，端点接线不在本任务（展示层归 R2-3）",
            "requires_action_linkage": {
                "wired": True,
                "trigger_below": ACTION_TRIGGER,
                "entry": "fixate_for_import（导入固化 = 固化 + 联动，同一个签名前入口）",
                "panel": "既有「需要我」面板（requires_action=true 的数据源），零新增端点",
                "writes": ["requires_action", "requires_action_reason", "intervention_options",
                           "policy.requires_human_confirmation", "status", "uncertainty"],
                "status_after": list(DECIDABLE_STATUSES),
                "not_flipped_when": ["已有 user_intervention（C1 要求此时必须为 null）",
                                     "status 不在可决定集：已结束的步骤弹『要人拍板』是假卡片"],
                "backfill": False,
                "backfill_note": "只作用于本轮新建 / 新合并的产物；已落盘的历史行不回填"
                                 "（历史行是当时的事实，不是现状断言）",
            },
        },
    }


#: 每档取一个代表值，只为在 ``policy()`` 里生成"这一档怎么讲"的示例文案
_BAND_SAMPLE: dict[str, float] = {"green": 0.95, "light_green": 0.8, "yellow": 0.6, "red": 0.25}


_EXPLAIN: dict[str, dict[str, str]] = {
    "green": {
        "zh": "置信度 {v}：来源判定为高置信（≥0.9），界面按绿色带展示，来源仍可在详情里核对。",
        "en": "Confidence {v}: source says high confidence (>=0.9); shown in the green band, "
              "with the source kept inspectable.",
    },
    "light_green": {
        "zh": "置信度 {v}：来源判定为中高置信（0.7–0.9），界面按浅绿展示，建议核对关键事实。",
        "en": "Confidence {v}: source says medium-high confidence (0.7-0.9); shown in light "
              "green, key facts worth a second look.",
    },
    "yellow": {
        "zh": "置信度 {v}：来源判定为中等置信（0.5–0.7），界面必须显示『不确定』并说明要看什么。",
        "en": "Confidence {v}: source says medium confidence (0.5-0.7); the UI must show "
              "\u201cuncertain\u201d and say what to check.",
    },
    "red": {
        "zh": "置信度 {v}：来源判定为低置信（<0.5），不得把数字讲成肯定，需要人来拍板。",
        "en": "Confidence {v}: source says low confidence (<0.5); never phrase the number as "
              "certain, a human must decide.",
    },
    BAND_UNKNOWN: {
        "zh": "来源未提供可判的置信度：显示为『未知』（中性色），不得把数字讲成肯定。",
        "en": "No judgeable confidence from the source: shown as \u201cunknown\u201d (neutral), "
              "and no number may be phrased as certain.",
    },
}


def explain(value: Any, *, lang: str = "zh") -> str:
    """一句话解释（zh / en 各一句）：带边界口径，且不可判时如实说"未知"。"""
    tongue = lang if lang in ("zh", "en") else "zh"
    name = band(value)
    template = _EXPLAIN[name][tongue]
    if name == BAND_UNKNOWN:
        return template
    num = _number(value)
    return template.format(v=(f"{num:.2f}" if num is not None else "—"))


def fixate_for_import(record: Any) -> dict[str, Any]:
    """导入固化（R2-1）+ 低置信联动（R2-2）：**签名之前**唯一接进导入路径的入口。

    R2-1 那半边（口径全部来自 §2.4 / §2.5，未变）：

    * 来源显式给了置信度 → ``normalize`` 后固化；自带的合规来源形态**逐字保留**，
      没有来源形态 → 按 §2.3 映射标方法（``template`` / ``tool_success_rate``）；
    * 来源什么都没给 → ``confidence`` 维持 ``null``（schema「缺失 = null」），
      ``confidence_source`` 置 ``{method: "default", logprob: null, tool_success_rate: null,
      samples: null}``；导入侧的 note「来源未提供置信度」已有，这里不动；
    * 来源给了个不认识的 ``method``（如 ``human``）→ 判为来源不可判，退回 ``default`` 形态，
      不发明枚举值、不替伪造值背书；
    * 低置信（``0 < confidence ≤ 0.6``）且记录没有 ``uncertainty`` → 补一条（schema C7 硬要求，
      否则导入的记录在 A2 校验处直接不合法）。

    R2-2 这半边（docs/02 D10 既定策略，``< 0.5`` 这条规则**不写进 schema**、只写在这里）：

    * ``confidence < ACTION_TRIGGER`` → ``requires_action=True``，并补齐 schema C1 要求的
      全套：``requires_action_reason``、三条含 approve/modify/reject 的决定选项、
      ``policy.requires_human_confirmation=True``、``status`` 落在可决定集、``uncertainty``。
      数据进的是既有「需要我」面板，不新增端点。
    * 翻牌边界原样保留（翻了会造出 schema 非法 / 点不动的假卡片）：已有 ``user_intervention``
      不翻；``status`` 不在 :data:`FLIPPABLE_STATUSES` 不翻。不翻就如实落盘，不硬造卡片。
    * 联动只作用于本轮新建 / 新合并的产物：已落盘的历史行不回填（铁律 2、4）。

    实现上直接复用 :func:`apply_to_record`（同一套取值优先级与翻牌纪律，两份实现迟早会漂）。
    入参零改写：返回深拷贝出来的新记录，签名链上的历史行一个字都不动（铁律 2）。
    """
    if not isinstance(record, Mapping):
        raise TypeError("fixate_for_import 需要一个记录对象（Mapping）")
    return apply_to_record(record)
