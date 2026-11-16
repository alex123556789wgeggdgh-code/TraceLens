"""多模态证据**生成接线**（REQ-003 R3-1，docs/07 §11.18；B3-1 分母口径的唯一落点）
+ **因果解释接线**（REQ-003 R3-2 / B3-2 前置，docs/07 §11.19：counterfactual 模板 →
`causal_explanation` 落账，与证据接线共点）。

为什么单独成模块：`evidence.py` 只回答"证据怎么存、哈希是什么、谁给的"，
"什么时候该自动产一条证据、产不出来怎么记账"是另一套口径。R7-1 把建议取数
收在 `suggestions.py` 单点、`server.py` 只留接线点，本模块沿用同一范式：
**判定与账目在这里，server 只做落账与重签**（`server._attach_decision_evidence`）。

三条口径（PM 裁决 TASK-R3-1 §2，冻结于 docs/07 §11.18）：

* **触发口径**：决策步骤完成且 `status ∈ TRIGGER_STATUSES` 时，
  存在决定前后的参数文本差异 → 触发 `add_diff`；存在计划候选（≥2 个候选）
  → 触发 `add_diagram`（纯 stdlib SVG）。两个条件都不成立 → **零触发**，
  登记原因码，不造证据；
* **分母口径（B3-1）**：`attempts` = 支持类型（diff / SVG / 文本摘要）的**生成尝试**
  次数，失败也计分母；`successes` = 成功落进证据账本的次数；
  成功率 = successes / attempts ≥ 0.95。截图类**未实现/待接入**
  （见 :data:`SCREENSHOT_UNIMPLEMENTED`，D-7），**不计入分母**；
* **存储口径（PM-PLAN §5.1）**：证据一律经 `EvidenceService._store_payload`
  落盘为文件，记录里只放引用（sha256 + 定位符 + media_type + 尺寸），
  单件上限 :data:`EVIDENCE_ITEM_MAX_BYTES` = **256KB**，超限**降级为文本摘要证据**；
  **Base64 绝不入账本**——本模块只产文本与 SVG 字节，没有任何 base64 出口。

第四条口径（**R3-2 追加，REQ-003 B3-2 前置**，PM 裁决 TASK-R3-2 §2，冻结于 docs/07 §11.19）：

* **因果解释接线**：决策步骤完成且满足**反事实条件**（有被拒的备选 / 有明确
  before→after 参数变化）时，调用**既有** `EvidenceService.counterfactual()`
  （`evidence.py`，T17 的模板计算核心）产出模板文案，落账 `causal_explanation`；
  触发判定与文案取数就在本模块（:func:`causal_trigger` / :func:`causal_alternatives`），
  与 R3-1 的证据接线**共点**（同一个 `_attach_decision_evidence`、同一次记录更新 + 重签）。
  **模板纪律（0.4 反编造）**：`counterfactual` 句子里出现的每个事实、`alternatives` 里
  每条 `option`/`expected`，都逐字取自输入记录（`intervention_options` 的 `label` 与
  `consequence`、`user_intervention.decision`、被改参数的 before/after）；
  记录里没有的字段一律留空，**不编造"如果当时……就会……"的想象后果**。
  `llm` 生成方式**预留不接**（0.6 不接真模型）：模板路径的 `model` 如实标 `template`。

纪律与其它扩展模块一致：纯标准库、零新依赖、零新路由、零 schema 改动、
`SIGNATURE_FIELDS` 一字未动（证据 sha256 经 `entry_hash` 覆盖整条记录自然进入
签名范围，schema 1090 已明文；本模块只补生成与落账，不动密码学）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from . import evidence as evidence_mod

__all__ = [
    "WIRING_VERSION", "EVIDENCE_ITEM_MAX_BYTES", "TRIGGER_STATUSES",
    "DENOMINATOR_KINDS", "SCREENSHOT_UNIMPLEMENTED", "MAX_EVIDENCE_ITEMS",
    "MIN_PLAN_CANDIDATES", "SKIP_REASONS", "record_language",
    "param_diff_pair", "plan_candidates", "diagram_plan", "summarize_text",
    "record_item", "EvidenceWiring",
    "CAUSAL_KEYS", "CAUSAL_LIMITS", "CAUSAL_TRIGGER_STATUSES",
    "CAUSAL_TRIGGER_REASONS", "CAUSAL_SKIP_REASONS",
    "unchosen_options", "param_changes", "causal_trigger", "causal_alternatives",
    "causal_note", "causal_explanation",
]

WIRING_VERSION = "1.0"

#: PM-PLAN §5.1 冻结：**单件尺寸上限 256KB**，超限降级为文本摘要证据。
#: 这是接线侧的上限，与 ``evidence.MAX_BYTES``（T13 入参契约的 4MB）是两回事：
#: 用户/执行端主动上传的截图仍按 4MB 契约收，**自动接线**产出的东西必须更小，
#: 否则账本（记录 JSON）会被自己生成的巨型 diff 撑爆。
EVIDENCE_ITEM_MAX_BYTES = 256 * 1024

#: B3-1 触发口径里的"决策步骤完成"。PM 口径写作 ``{done, modified, succeeded}``；
#: 本仓 schema 的 status 枚举（schema 390）没有 ``done``，决定段落盘后的三个值是
#: ``approved``（放行）/``modified``（改后放行）/``rejected``（拦下），记录段完成值是
#: ``succeeded``。逐字映射：``done`` ≙ ``approved``（决定已落盘 = 该步完成），
#: ``modified`` 同名，``succeeded`` 同名；``rejected`` **不在口径内**——"拦下"不是
#: "完成"，被拦下的动作没有"决定之后的结果"可解释，不产证据（零误触发）。
TRIGGER_STATUSES: tuple[str, ...] = ("approved", "modified", "succeeded", "done")

#: B3-1 分母支持的三类（PM §2.1）：文本 diff、SVG 流程图、文本摘要。
DENOMINATOR_KINDS: tuple[str, ...] = ("diff", "svg", "text_summary")

#: D-7 登记：截图类"未实现/待接入"清单，**不计入 B3-1 分母**。
#: 本机是无头环境，抓屏需要浏览器/截屏能力（0.2 硬约束禁止引入），
#: 所以截图证据只能由执行端/用户经 T13 主动上传，运行时**不自动**产截图。
SCREENSHOT_UNIMPLEMENTED: tuple[str, ...] = (
    "screen_capture：运行时抓屏（需无头浏览器/桌面抓屏能力，本机不可达，D-7 不计分母）",
    "browser_screenshot：TraceLens 上游 trace 自带截图的搬运（待接入，未实现）",
    "chart_image：图表位图渲染（纯标准库无渲染库，未实现）",
)

#: schema 1175 ``multimedia_evidence.maxItems = 50``：落账是**追加 + 截断到上限**，
#: 绝不删除既有条目（历史记录零改写铁律）。截掉的条数如实登记在 ``truncated``。
MAX_EVIDENCE_ITEMS = 50

#: "计划候选"的下限：一个候选没有"选择"可解释，画流程图属于误触发。
MIN_PLAN_CANDIDATES = 2

#: 零触发时必须留下原因（静默跳过 = 缺陷，与 R7-1 的"不静默"同口径）。
SKIP_REASONS: tuple[str, ...] = (
    "record_not_object",     # 上游给了不是对象的东西
    "status_not_completed",  # 状态不在决策完成口径内（含 rejected / running）
    "no_trigger_condition",  # 既无参数差异也无计划候选 → 零误触发
    "missing_record_id",
)

#: 节点/边标签的裁剪上限：SVG 是给人看的，长标签截断比换行渲染更可控。
_LABEL_CLIP = 90
#: 降级摘要的每侧文本上限：取 1/8，保证 ``render_diff`` 的产物远小于 256KB。
_SUMMARY_SIDE_CHARS = EVIDENCE_ITEM_MAX_BYTES // 8

# --------------------------------------------------------------------------- #
# R3-2 因果解释（counterfactual）的口径常量：上限逐字取自 schema :1257 那一块
# --------------------------------------------------------------------------- #

#: schema :1257 ``causal_explanation`` 的对象形态是**封闭形状**
#: （``additionalProperties: false``），五个属性一个都不能多。
#: 这决定了一件诚实口径上的事：PM §2.2 说的 ``provided_by`` **不在这五个属性里**，
#: 而 schema 零改动是铁律，所以"谁生成的"这件事由 ``model`` 承担
#: （模板就写 ``template``，绝不冒充 ``llm``）—— 与
#: ``human_readable.generated_by`` 同一诚实思路，不新增字段。
CAUSAL_KEYS: frozenset[str] = frozenset({
    "counterfactual", "alternatives", "model", "generated_at", "language",
})

#: 长度与条数上限，逐字对齐 schema（``tests/test_counterfactual_wiring.py`` 把
#: "常量 == schema 里的数字"钉成断言：schema 一旦被人改动，这里立刻红，不会悄悄漂移）。
CAUSAL_LIMITS: dict[str, int] = {
    "counterfactual": 1000,     # schema :1273 ``maxLength``
    "alternatives": 6,          # schema :1278 ``maxItems``
    "option": 200,              # schema :1289 ``maxLength``
    "expected": 500,            # schema :1296 ``maxLength``
    "why": 500,                 # schema :1303 ``maxLength``
}
#: 兼容别名（旧代码里读起来更直白的名字，值就是 ``CAUSAL_LIMITS`` 里的同一份）
CAUSAL_MAX_ALTERNATIVES = CAUSAL_LIMITS["alternatives"]

#: "决策完成"口径与 R3-1 **同一份**（`TRIGGER_STATUSES`），不另立第二套：
#: 反事实解释解释的就是"决定之后"的事，决定没落盘就没有"之后"。
CAUSAL_TRIGGER_STATUSES: tuple[str, ...] = TRIGGER_STATUSES

#: 反事实条件（PM §2.1）：两个条件**任一**成立即触发；都不成立 → 零触发。
CAUSAL_TRIGGER_REASONS: tuple[str, ...] = ("rejected_alternative", "param_change")

#: 零触发的原因码与 R3-1 同一份（静默跳过 = 缺陷，与 R7-1 的"不静默"同口径）。
CAUSAL_SKIP_REASONS: tuple[str, ...] = SKIP_REASONS


# --------------------------------------------------------------------------- #
# 触发判定（纯函数，零 IO：可单测、可复算、不碰磁盘）
# --------------------------------------------------------------------------- #

def record_language(record: Mapping) -> str:
    """记录自己的文案语言（``human_readable.language``）：证据 caption 跟着它走。

    口径与 ``intervention_history`` 一致：只有明确是 ``en*`` 才走英文，其余按中文，
    缺失不猜。
    """
    readable = record.get("human_readable") if isinstance(record, Mapping) else None
    lang = str((readable.get("language") if isinstance(readable, Mapping) else "")
               or "").lower()
    return "en" if lang.startswith("en") else "zh"


def _fmt(value: object) -> str:
    """把任意参数值收敛成**稳定可 diff** 的一行文本（JSON 形态，键序无关）。

    为什么不用 ``str(value)``：``str`` 对 dict/list 的键序随上游走，
    同一份值两次渲染可能不一样 → diff 会"凭空多出差异"，分母就被污染了。
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _render_params(mapping: Mapping) -> str:
    lines = [f"{key} = {_fmt(mapping[key])}" for key in sorted(mapping)]
    return "\n".join(lines)


def param_diff_pair(record: Mapping) -> tuple[str, str] | None:
    """决定前后的**参数文本差异** → ``(before_text, after_text)``；无差异返回 ``None``。

    取数顺序（越靠前越权威，取到就用，不二次实现第二套口径）：

    1. ``user_intervention.modified_fields``（``apply_decision`` 逐字段核过的
       ``[{name, before, after}]``，那是"真的改了"的官方账）；
    2. 退一步用 ``original_params`` 与 ``modified_params`` 的整对象差集（形状来自
       schema C3/C4，R7-1 的取数口径同款兜底）。

    两侧文本逐字相同 → ``None``：**零误触发**（传了参数但没真改，不产证据）。
    """
    if not isinstance(record, Mapping):
        return None
    ui = record.get("user_intervention")
    if not isinstance(ui, Mapping):
        return None
    fields = ui.get("modified_fields")
    if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
        rows = [item for item in fields if isinstance(item, Mapping) and item.get("name")]
        if rows:
            before = "\n".join(f"{item.get('name')} = {_fmt(item.get('before'))}"
                               for item in rows)
            after = "\n".join(f"{item.get('name')} = {_fmt(item.get('after'))}"
                              for item in rows)
            return (before, after) if before != after else None
    original = ui.get("original_params")
    modified = ui.get("modified_params")
    if not (isinstance(original, Mapping) and isinstance(modified, Mapping)):
        return None
    if not modified:
        return None
    merged_after = dict(original)
    merged_after.update(modified)
    before_text = _render_params(original)
    after_text = _render_params(merged_after)
    return (before_text, after_text) if before_text != after_text else None


def plan_candidates(record: Mapping) -> list[dict]:
    """计划候选 = ``intervention_options`` 里可枚举的选项（schema 的候选清单）。

    少于 :data:`MIN_PLAN_CANDIDATES` 个 → 空列表：只有一个选项时"选了它"没有可比性，
    画流程图属于误触发（PM §2.1「多候选决策」）。
    """
    options = record.get("intervention_options") if isinstance(record, Mapping) else None
    if not (isinstance(options, Sequence) and not isinstance(options, (str, bytes))):
        return []
    listed = [dict(item) for item in options if isinstance(item, Mapping)]
    return listed if len(listed) >= MIN_PLAN_CANDIDATES else []


def _clip(text: object, limit: int = _LABEL_CLIP) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)] + "…"


def diagram_plan(record: Mapping) -> dict | None:
    """计划候选 → ``{title, nodes, edges, chosen, candidates}``；候选不足返回 ``None``。

    形状：一个"决策点"根节点 + 每个候选一个节点 + 根 → 候选的边。已选中的候选在
    标签里标出来（``（已选）``/``(chosen)``），看图的人一眼能回答"当时有哪些路、
    走了哪条"。节点 id 用选项 id，边只连真实存在的节点（`_diagram_svg` 对不存在的
    端点直接不画线，不给假图）。
    """
    candidates = plan_candidates(record)
    if not candidates:
        return None
    ui = record.get("user_intervention")
    ui = ui if isinstance(ui, Mapping) else {}
    chosen_id = str(ui.get("option_id") or "")
    decision = str(ui.get("decision") or "")
    lang = record_language(record)
    tool = _clip((record.get("tool_used") or {}).get("name")
                 if isinstance(record.get("tool_used"), Mapping)
                 else (record.get("tool_used") or record.get("action_type") or "action"), 40)
    nodes: list[dict] = [{"id": "decision",
                          "label": _clip(f"决策点：{tool}" if lang == "zh"
                                         else f"Decision point: {tool}")}]
    edges: list[dict] = []
    labels: list[str] = []
    for index, option in enumerate(candidates):
        node_id = _clip(str(option.get("id") or f"option-{index}"), 60)
        kind = str(option.get("kind") or "?")
        label = str(option.get("label") or option.get("description") or kind)
        chosen = bool(chosen_id) and chosen_id == str(option.get("id") or "")
        suffix = "（已选）" if (lang == "zh" and chosen) else (" (chosen)" if chosen else "")
        text = _clip(f"{kind}｜{label}{suffix}" if lang == "zh"
                     else f"{kind} | {label}{suffix}")
        nodes.append({"id": node_id, "label": text})
        edges.append({"from": "decision", "to": node_id})
        labels.append(text)
    total = len(candidates)
    title = (f"计划候选：{tool}（{total} 个候选）" if lang == "zh"
             else f"Plan candidates: {tool} ({total} options)")
    return {"title": title, "nodes": nodes, "edges": edges,
            "chosen": chosen_id, "candidates": labels}


def summarize_text(text: str, limit: int = _SUMMARY_SIDE_CHARS, *, note: str = "") -> str:
    """文本摘要（降级用，确定性可复算）：取头部 + 如实标注被截掉的规模。

    为什么只取头部不取中段：证据要"看得出当时是什么"，头部最可能含语义；
    中段采样会让摘要自己变成一条需要解释的产物。**绝不假装摘要等于全文**——
    第一行就写着它截掉了多少字符。
    """
    body = str(text or "")
    cap = max(0, int(limit))
    if len(body) <= cap:
        return body
    head = body[:cap]
    tail_note = note or "摘要（超限截断）"
    lines = [f"# {tail_note}: {len(body)} 字符 → {len(head)} 字符", head,
             f"# 余下 {len(body) - len(head)} 字符未纳入本摘要"]
    return "\n".join(lines)


def _summary_pair(before: str, after: str, lang: str) -> tuple[str, str]:
    note = "超限降级：文本摘要（原文超过 256KB）" if lang == "zh" \
        else "oversize → text summary (original exceeded 256KB)"
    return (summarize_text(before, _SUMMARY_SIDE_CHARS, note=note),
            summarize_text(after, _SUMMARY_SIDE_CHARS, note=note))


def record_item(view: Mapping, *, generated_at: str | None = None) -> dict:
    """证据索引视图 → **记录里的落账条目**（schema 1175 的 properties 之内）。

    逐字沿用 T13（``server.evidence_attach``）的裁剪：`feedback` 不进记录
    （反馈计数是账本侧的活数据，写进已签名记录会立刻过期）。
    只放**引用**：sha256 + 定位符（``uri``，schema 明文"uri 指回本模块的端点"）
    + media_type + 尺寸 + 时间戳 + caption + 来源；**不放文件内容、不放 Base64**。
    """
    item = {key: value for key, value in view.items()
            if key != "feedback" and key in _ITEM_KEYS}
    item.setdefault("kind", "")
    stamp = generated_at or str(view.get("created_at") or "")
    if stamp:
        item["generated_at"] = stamp
    return item


_ITEM_KEYS: frozenset[str] = frozenset({
    "evidence_id", "id", "record_id", "kind", "media_type", "uri", "sha256",
    "bytes", "caption", "generated_at", "created_at", "provided_by",
})


# --------------------------------------------------------------------------- #
# R3-2 因果解释：触发判定与取数（纯函数，零 IO：可单测、可离线复算、不碰磁盘）
# --------------------------------------------------------------------------- #

def _ui(record: Mapping) -> Mapping:
    """``user_intervention`` 的安全视图（不是对象就当空对象，不猜决定）。"""
    ui = record.get("user_intervention") if isinstance(record, Mapping) else None
    return ui if isinstance(ui, Mapping) else {}


def unchosen_options(record: Mapping) -> list[dict]:
    """记录里**没被选中**的计划候选 = 口径里的"被拒的备选"。

    剔除规则只看记录自己的账：``user_intervention.option_id`` 命中的那条是"当时走的路"，
    其余候选（``kind`` 是 approve / modify / reject 都算）都是"没走的路"。
    记录里没有 ``option_id`` 时**不猜**"选了谁"——全部候选都算未选中，
    这是如实反映记录缺口，不是推断（推断就是编造）。

    逐字取数：``option`` 取候选自带的 ``label``（缺失退到 ``kind``/``id``），
    ``expected`` 取候选自带的 ``consequence``（schema :2209 就是"点这个会发生什么 +
    不点会怎样"，正是"备选选项的预期结果"），缺失再退到 ``description``；
    两个都没有 → 空串（**不编造后果**，schema 也允许空）。
    """
    if not isinstance(record, Mapping):
        return []
    options = record.get("intervention_options")
    if not (isinstance(options, Sequence) and not isinstance(options, (str, bytes))):
        return []
    chosen = str(_ui(record).get("option_id") or "")
    lang = record_language(record)
    decision = str(_ui(record).get("decision") or ("(none)" if lang == "en" else "（无）"))
    out: list[dict] = []
    for item in options:
        if not isinstance(item, Mapping):
            continue
        option_id = str(item.get("id") or "")
        if chosen and option_id == chosen:
            continue
        label = str(item.get("label") or item.get("kind") or option_id or "?")
        expected = str(item.get("consequence") or item.get("description") or "")
        why = (f"未选择；当时记录在案的决定是「{decision}」" if lang == "zh"
               else f"Not chosen; the on-record decision was \"{decision}\"")
        out.append({"option": _clip(label, CAUSAL_LIMITS["option"]),
                    "expected": _clip(expected, CAUSAL_LIMITS["expected"]),
                    "why": _clip(why, CAUSAL_LIMITS["why"])})
    return out


def param_changes(record: Mapping) -> list[dict]:
    """决定改掉的参数 = 口径里的"被拒参数"：``[{name, before, after}]``（文本化）。

    取数顺序与 :func:`param_diff_pair` **完全同一口径**（官方账优先：
    ``user_intervention.modified_fields`` → 退一步用 ``original_params``/
    ``modified_params`` 整对象差集），逐字段渲染也共用 :func:`_fmt`
    （键序无关，同一份值两次渲染逐字相同）。两侧渲染相同 = 没真改 → 不列。

    为什么这是"被拒的那一份"：决定把 ``name`` 从 ``before`` 改成 ``after``，
    ``before`` 就是"决定没要的那个值"，它是记录里的事实，不是想象。
    """
    if not isinstance(record, Mapping):
        return []
    ui = _ui(record)
    out: list[dict] = []
    fields = ui.get("modified_fields")
    if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
        for item in fields:
            if not isinstance(item, Mapping) or not item.get("name"):
                continue
            before, after = _fmt(item.get("before")), _fmt(item.get("after"))
            if before != after:
                out.append({"name": str(item.get("name")),
                            "before": before, "after": after})
        return out
    original, modified = ui.get("original_params"), ui.get("modified_params")
    if not (isinstance(original, Mapping) and isinstance(modified, Mapping)):
        return []
    if not modified:
        return []
    for key in sorted(modified):
        before = _fmt(original.get(key) if key in original else None)
        after = _fmt(modified[key])
        if before != after:
            out.append({"name": str(key), "before": before, "after": after})
    return out


def causal_trigger(record: Mapping) -> dict | None:
    """反事实条件判定：有被拒的备选 **或** 有明确 before→after 参数变化 →
    ``{reasons: [...]}``；两者都不成立 → ``None``（**零误触发**：没有"没走的路"
    就没有反事实可解释，硬写一句"如果当时选别的"就是编造）。"""
    if not isinstance(record, Mapping):
        return None
    reasons: list[str] = []
    if unchosen_options(record):
        reasons.append("rejected_alternative")
    if param_changes(record):
        reasons.append("param_change")
    return {"reasons": reasons} if reasons else None


def causal_alternatives(record: Mapping) -> list[dict]:
    """``alternatives`` 清单：未选候选 + 被拒参数（决定前的那一份）。

    顺序稳定（先候选后参数），**同一输入必得同一输出**（离线复算的前提）。
    条数不在这里裁（截断在 :func:`causal_explanation` 里做并如实计数），
    因为"截掉几条"属于落账口径，要能被台账看见。
    """
    lang = record_language(record)
    out = list(unchosen_options(record))
    for change in param_changes(record):
        name, before, after = change["name"], change["before"], change["after"]
        out.append({
            "option": _clip(f"保持决定前参数：{name} = {before}" if lang == "zh"
                            else f"Keep the pre-decision value: {name} = {before}",
                            CAUSAL_LIMITS["option"]),
            "expected": _clip(before, CAUSAL_LIMITS["expected"]),
            "why": _clip(f"决定把它改成 {after}" if lang == "zh"
                         else f"The decision changed it to {after}",
                         CAUSAL_LIMITS["why"]),
        })
    return out


def causal_note(record: Mapping, *, lang: str = "zh") -> str:
    """追加在模板句后面的**事实小句**：只引用记录字段，不引入任何新事实。

    为什么模板本体（`evidence.counterfactual`）之外还要补这一句：那句模板说的是
    "选择不同 → 结果会变"这个**结构**，而 B3-2 要的解释得看得见"当时有哪些路、
    被拒的那一份参数是什么"。这里的每个字都来自 ``intervention_options`` 的
    ``label``/``consequence`` 与 ``modified_fields`` 的 ``name``/``before``/``after``；
    记录里没有后果文案的候选，句子会明说"记录里没有写它的后果"，不替记录补一句想象。
    """
    parts: list[str] = []
    options = unchosen_options(record)
    if options:
        first = options[0]
        label, expected = first["option"], first["expected"]
        if lang == "en":
            head = (f"There were {len(options)} options not chosen" if len(options) > 1
                    else "There was one option not chosen")
            parts.append(f"{head}: \"{label}\" "
                         + (f"carries the recorded consequence \"{expected}\"."
                            if expected else "has no consequence recorded on the record."))
        else:
            head = (f"当时还有 {len(options)} 个未选择的候选" if len(options) > 1
                    else "当时还有一个未选择的候选")
            parts.append(f"{head}：「{label}」"
                         + (f"在记录里写着的后果是「{expected}」。"
                            if expected else "在记录里没有写它的后果。"))
    changes = param_changes(record)
    if changes:
        listed = "；".join(f"{row['name']} 从 {row['before']} 改成 {row['after']}"
                           for row in changes[:3])
        tail = (f"（其余 {len(changes) - 3} 项未在此列出）" if len(changes) > 3 else "")
        parts.append(("决定改掉的参数（决定前的那一份就是被拒的那一份）：" + listed + tail)
                     if lang == "zh" else
                     ("Parameters the decision changed (the pre-decision value is the "
                      f"rejected one): {listed}"
                      + (f" (other {len(changes) - 3} changes not listed here)"
                         if len(changes) > 3 else "")))
    return " ".join(parts)


def causal_explanation(service: evidence_mod.EvidenceService, record: Mapping, *,
                       lang: str | None = None, llm=None) -> tuple[dict | None, int]:
    """调用**既有** :meth:`EvidenceService.counterfactual` 产出模板解释，
    并收敛到 schema :1257 的封闭形状。返回 ``(解释 | None, 截断条数)``。

    * **零新模板实现**：反事实主句来自 ``evidence.py`` 的 ``counterfactual()``
      （T17 同款），本函数只做"取数 → 传参 → 按 schema 裁剪"；
    * ``lang=None`` → 跟记录自己的语言（``human_readable.language``，与 R3-1 caption 同口径）；
      显式传 ``zh``/``en`` 时以请求为准（T17 的 ``lang`` 就是这个语义）；
    * ``llm`` 钩子**预留不接**：传进来就交给 ``counterfactual()``（它会把 ``model``
      如实标成 ``llm``）；产品接线路径永远传 ``None``，所以落账的一律是 ``template``；
    * 裁剪全部显式：主句 ≤1000、备选 ≤6 条、每条 ``option``/``expected``/``why``
      ≤200/500/500，**超出的条数返回给调用方记台账**（截断不许静默）；
    * ``generated_at`` 缺失时给 ``None``（schema 的 ``nullableTimestamp`` 收 null）——
      **不编时间戳**。
    """
    if causal_trigger(record) is None:
        return None, 0
    lang_now = lang if lang in ("zh", "en") else record_language(record)
    alternatives = causal_alternatives(record)
    raw = service.counterfactual(record, alternatives, lang=lang_now, llm=llm)
    if not isinstance(raw, Mapping):
        return None, len(alternatives)
    text = str(raw.get("counterfactual") or "")
    if not text:
        return None, len(alternatives)
    if llm is None:
        # 模板路径才追加事实小句；LLM 路径（0.6 才接）的文案由模型自己写，这里不覆写。
        note = causal_note(record, lang=lang_now)
        text = f"{text}{note}" if note else text
    kept = alternatives[:CAUSAL_LIMITS["alternatives"]]
    truncated = max(0, len(alternatives) - len(kept))
    explanation = {
        "counterfactual": _clip(text, CAUSAL_LIMITS["counterfactual"]),
        "alternatives": [{"option": _clip(str(row.get("option") or "?"),
                                          CAUSAL_LIMITS["option"]) or "?",
                          "expected": _clip(str(row.get("expected") or ""),
                                            CAUSAL_LIMITS["expected"]),
                          "why": _clip(str(row.get("why") or ""),
                                       CAUSAL_LIMITS["why"])}
                         for row in kept],
        "model": str(raw.get("model") or "template"),
        "generated_at": str(raw.get("generated_at") or "") or None,
        "language": lang_now,
    }
    return explanation, truncated


# --------------------------------------------------------------------------- #
# 接线服务：生成 + 分母/分子台账（B3-1 的计数器在这里，不在响应载荷里）
# --------------------------------------------------------------------------- #

class EvidenceWiring:
    """决策完成时的证据生成器 + B3-1 分母台账。

    计数口径（PM §2.1，逐字）：**生成尝试 = 分母**（``attempts``），
    **成功落账 = 分子**（``successes``）；失败也进分母、不进分子；
    超限降级成功的算成功（分子 +1）并另记 ``degraded`` 一格，
    因为"降级"是口径内的成功，不是失败。零触发**不进分母**，
    只记 ``skips`` + 原因码（条件不成立 ≠ 生成失败）。

    R3-2 起本类还管**因果解释的生成侧**（``causal()`` / :meth:`causal_facts`）：
    同一套计数纪律（触发成立进 ``causal_attempts``、零触发只进
    ``causal_skips`` + 原因码、异常进 ``causal_failures`` 并留证），
    与证据台账分开计数——两类产物的"生成尝试"不是一回事，混在一个分母里
    B3-1 的成功率就说不清是谁的。落账那一格在 server 侧（``causal_wiring_records``）。
    """

    def __init__(self, evidence_service: evidence_mod.EvidenceService, *,
                 max_item_bytes: int = EVIDENCE_ITEM_MAX_BYTES) -> None:
        if not isinstance(evidence_service, evidence_mod.EvidenceService):
            raise TypeError("EvidenceWiring 需要 EvidenceService（见 evidence.py）")
        self.service = evidence_service
        self.max_item_bytes = int(max_item_bytes)
        # -- B3-1 台账（分母/分子/降级/失败/零触发；服务端属性，不进任何响应载荷） --
        self.attempts = 0
        self.successes = 0
        self.degraded = 0
        self.failures = 0
        self.skips = 0
        self.skip_reasons: dict[str, int] = {}
        self.last_problem: dict | None = None
        # -- R3-2 因果解释的**生成侧**台账（落账那一格在 server 侧，分工同证据侧） ----
        # 计数口径逐字沿用 R3-1：触发成立才进 attempts；零触发只记 skips + 原因码；
        # 生成异常进 failures 并留证（last_causal_problem），绝不抛给决定流。
        self.causal_attempts = 0
        self.causal_generated = 0
        self.causal_skips = 0
        self.causal_failures = 0
        self.causal_truncated = 0
        self.causal_skip_reasons: dict[str, int] = {}
        self.last_causal_problem: dict | None = None

    # -- 台账 ------------------------------------------------------------- #
    def _skip(self, reason: str) -> None:
        self.skips += 1
        self.skip_reasons[reason] = int(self.skip_reasons.get(reason) or 0) + 1

    def _causal_skip(self, reason: str) -> None:
        self.causal_skips += 1
        self.causal_skip_reasons[reason] = int(self.causal_skip_reasons.get(reason) or 0) + 1

    def success_rate(self) -> float | None:
        """成功率 = 分子 / 分母；**分母为 0 → None**（没有生成尝试就没有成功率可谈，
        不拿 1.0 冒充"100% 达标"，也不拿 0.0 冒充失败）。"""
        if self.attempts <= 0:
            return None
        return self.successes / self.attempts

    def facts(self) -> dict:
        return {"version": WIRING_VERSION, "attempts": self.attempts,
                "successes": self.successes, "degraded": self.degraded,
                "failures": self.failures, "skips": self.skips,
                "skip_reasons": dict(self.skip_reasons),
                "success_rate": self.success_rate(),
                "max_item_bytes": self.max_item_bytes,
                "denominator_kinds": list(DENOMINATOR_KINDS),
                "screenshot_unimplemented": list(SCREENSHOT_UNIMPLEMENTED)}

    def causal_facts(self) -> dict:
        """因果解释的**生成侧**台账（服务端属性，不进任何响应载荷；分工同 ``facts()``）。"""
        return {"attempts": self.causal_attempts, "generated": self.causal_generated,
                "skips": self.causal_skips, "failures": self.causal_failures,
                "truncated": self.causal_truncated,
                "skip_reasons": dict(self.causal_skip_reasons),
                "trigger_reasons": list(CAUSAL_TRIGGER_REASONS),
                "max_alternatives": CAUSAL_LIMITS["alternatives"],
                "limits": dict(CAUSAL_LIMITS)}

    # -- 因果解释（R3-2 / B3-2 前置：counterfactual 模板 → causal_explanation 落账） --
    def causal(self, record: Mapping, *, llm=None) -> dict:
        """决策完成时该产的反事实解释：返回 ``{explanation, skipped, reason, truncated}``。

        **绝不抛异常**：解释是分析面，取数或模板出问题时只登记台账
        （``causal_failures`` + ``last_causal_problem``）并返回 ``explanation=None``，
        绝不让一次决定变成 500（与 :meth:`generate` 同款隔离）。
        零触发**不进分母**：只记 ``causal_skips`` + 原因码。
        """
        if not isinstance(record, Mapping):
            self._causal_skip("record_not_object")
            return self._causal_empty("record_not_object")
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            self._causal_skip("missing_record_id")
            return self._causal_empty("missing_record_id")
        status = str(record.get("status") or "")
        if status not in CAUSAL_TRIGGER_STATUSES:
            self._causal_skip("status_not_completed")
            return self._causal_empty("status_not_completed")
        if causal_trigger(record) is None:
            self._causal_skip("no_trigger_condition")
            return self._causal_empty("no_trigger_condition")
        self.causal_attempts += 1
        try:
            explanation, truncated = causal_explanation(self.service, record, llm=llm)
        except Exception as exc:                        # noqa: BLE001 - 兜底：不反噬决定
            self.causal_failures += 1
            self.last_causal_problem = {
                "code": "CAUSAL_WIRING_GENERATION_FAILED",
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "record_id": record_id}
            return self._causal_empty("generation_failed")
        if explanation is None:
            self.causal_failures += 1
            self.last_causal_problem = {
                "code": "CAUSAL_WIRING_TEMPLATE_EMPTY",
                "reason": "counterfactual 模板没有产出可用文案",
                "record_id": record_id}
            return self._causal_empty("template_empty")
        self.causal_generated += 1
        if truncated:
            self.causal_truncated += truncated
        return {"explanation": explanation, "skipped": False,
                "reason": None, "truncated": truncated}

    def _causal_empty(self, reason: str) -> dict:
        return {"explanation": None, "skipped": True, "reason": reason, "truncated": 0}

    # -- 生成（B3-1 的调用点） --------------------------------------------- #
    def generate(self, record: Mapping) -> dict:
        """决策完成时该产的证据：返回 ``{items, planned, attempts, degraded,
        skipped, reason, truncated}``。

        **绝不抛异常**：证据是分析面，生成失败只登记台账（``failures`` +
        ``last_problem``），绝不让一次决定变成 500（R7-1 同款隔离）。
        """
        if not isinstance(record, Mapping):
            self._skip("record_not_object")
            return self._empty("record_not_object")
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            self._skip("missing_record_id")
            return self._empty("missing_record_id")
        status = str(record.get("status") or "")
        if status not in TRIGGER_STATUSES:
            self._skip("status_not_completed")
            return self._empty("status_not_completed")
        lang = record_language(record)
        plans: list[dict] = []
        pair = param_diff_pair(record)
        if pair is not None:
            plans.append({"kind": "diff", "before": pair[0], "after": pair[1]})
        diagram = diagram_plan(record)
        if diagram is not None:
            plans.append({"kind": "diagram", **diagram})
        if not plans:
            self._skip("no_trigger_condition")
            return self._empty("no_trigger_condition")
        items: list[dict] = []
        degraded_here = 0
        for plan in plans:
            self.attempts += 1
            try:
                view, degraded = (self._emit_diff(record_id, plan, lang)
                                  if plan["kind"] == "diff"
                                  else self._emit_diagram(record_id, plan, lang))
            except evidence_mod.EvidenceError as exc:      # 证据域异常：如实登记
                self.failures += 1
                self.last_problem = {"code": exc.code, "reason": exc.reason[:200],
                                     "record_id": record_id, "plan": plan["kind"]}
                continue
            except Exception as exc:                        # noqa: BLE001 - 兜底：不反噬决定
                self.failures += 1
                self.last_problem = {"code": "EVIDENCE_WIRING_GENERATION_FAILED",
                                     "reason": f"{type(exc).__name__}: {exc}"[:200],
                                     "record_id": record_id, "plan": plan["kind"]}
                continue
            self.successes += 1
            if degraded:
                self.degraded += 1
                degraded_here += 1
            items.append(record_item(view))
        return {"items": items,
                "planned": [plan["kind"] for plan in plans],
                "attempts": len(plans), "degraded": degraded_here,
                "skipped": False, "reason": None, "truncated": 0}

    def _empty(self, reason: str) -> dict:
        return {"items": [], "planned": [], "attempts": 0, "degraded": 0,
                "skipped": True, "reason": reason, "truncated": 0}

    # -- 单件生成（都走 EvidenceService → _store_payload，绝不自己写盘） ----- #
    def _prepare_store(self) -> None:
        """写盘前把证据目录备好（幂等，一次 ``mkdir(parents=True, exist_ok=True)``）。

        为什么接线侧要补这一步：``FeaturePaths.ensure()`` 是"用到才建目录"的懒口径，
        而 ``EvidenceService._store_payload`` 自己不建目录（它只管原子换名）。
        一个全新的数据根上，第一次决定就要落证据，目录还没人建 → 写盘必然失败，
        分母白涨一格。建目录的**落点口径**仍归 ``FeaturePaths``，
        **写盘口径**仍归 ``_store_payload``：接线不自己写文件。
        """
        self.service.paths.evidence_files_dir.mkdir(parents=True, exist_ok=True)

    def _emit_diff(self, record_id: str, plan: dict, lang: str) -> tuple[dict, bool]:
        """文本 diff 证据；超过 256KB → **降级为文本摘要证据**（PM-PLAN §5.1）。

        为什么先量字节再降级、而不是"写完再截"：``add_diff`` 内部自己渲染，
        字节数就是落盘字节数（同一 ``render_diff``），量一次就够，不会
        "自报 200KB 实际落盘 300KB"。
        """
        before, after = str(plan["before"]), str(plan["after"])
        self._prepare_store()
        caption = "决定前后参数差异（自动生成）" if lang == "zh" \
            else "Parameter change before/after the decision (auto-generated)"
        data = evidence_mod.render_diff(before, after, filename="params")
        degraded = len(data) > self.max_item_bytes
        if degraded:
            before, after = _summary_pair(before, after, lang)
            caption = (caption + "·超限降级文本摘要") if lang == "zh" \
                else (caption + " (oversize → text summary)")
            data = evidence_mod.render_diff(before, after, filename="params")
        view = self.service.add_diff(record_id, before=before, after=after,
                                     filename="params", caption=caption)
        return view, degraded

    def _emit_diagram(self, record_id: str, plan: dict, lang: str) -> tuple[dict, bool]:
        """SVG 流程图证据；超过 256KB → 先砍候选数、再截标签、最后**降级为文本摘要证据**。

        SVG 是纯 stdlib 拼的（``evidence._diagram_svg``），尺寸只随候选数与标签长度涨，
        所以降级是一条**保真度递减**的梯子，每级都如实写明丢了什么：

        ① **砍候选数**（标签逐字保住）：从"只画 1 个候选"起逐级放宽，取能装下的最小规模，
        标题里写"仅显示前 N 个，共 M 个候选"；
        ② **截断标签**（候选数保住）：连 2 个节点都装不下 = 单个标签就是巨型文本，
        这时截到 :data:`_LABEL_CLIP` 字符，图还在、语义还在；
        ③ **文本摘要证据**：连截断后的两个节点都超（构造上到不了，留作兜底），
        落一份候选清单的逐行摘要（``kind="diff"`` / ``media_type=text/x-diff``），
        文件里是可读文本，不是一张画不出来的图。
        """
        title, nodes, edges = str(plan["title"]), list(plan["nodes"]), list(plan["edges"])
        caption = "计划候选流程图（自动生成）" if lang == "zh" \
            else "Plan candidates diagram (auto-generated)"
        self._prepare_store()
        svg = self.service._diagram_svg(title, nodes, edges)
        if len(svg) <= self.max_item_bytes:
            view = self.service.add_diagram(record_id, title=title, nodes=nodes,
                                            edges=edges, caption=caption)
            return view, False
        total = max(0, len(nodes) - 1)      # 节点 0 是决策点根节点

        def _trimmed(keep: int) -> tuple[list[dict], list[dict]]:
            picked = [nodes[0]] + nodes[1:keep + 1]
            ids = {str(node.get("id")) for node in picked}
            lines = [edge for edge in edges
                     if str(edge.get("from")) in ids and str(edge.get("to")) in ids]
            return picked, lines

        def _note(keep: int, lang_now: str) -> str:
            if lang_now == "zh":
                return f"（仅显示前 {keep} 个，共 {total} 个候选）" if keep < total else ""
            return (f" (first {keep} of {total} candidates shown)"
                    if keep < total else "")

        # 降级 ①：砍候选数，标签逐字保住
        for keep in range(1, total + 1):
            picked, lines = _trimmed(keep)
            if len(self.service._diagram_svg(title, picked, lines)) <= self.max_item_bytes:
                view = self.service.add_diagram(record_id, title=title + _note(keep, lang),
                                                nodes=picked, edges=lines, caption=caption)
                return view, True
        # 降级 ②：截断标签，候选数（前两个节点）保住
        clipped = [{"id": _clip(node.get("id"), 40), "label": _clip(node.get("label"), 40)}
                   for node in nodes[:2]]
        clipped_ids = {str(node["id"]) for node in clipped}
        clipped_edges = [edge for edge in edges
                         if str(edge.get("from")) in clipped_ids
                         and str(edge.get("to")) in clipped_ids]
        svg = self.service._diagram_svg(title, clipped, clipped_edges)
        if len(svg) <= self.max_item_bytes:
            suffix = "（标签已截断）" if lang == "zh" else " (labels truncated)"
            view = self.service.add_diagram(record_id, title=title + _note(1, lang) + suffix,
                                            nodes=clipped, edges=clipped_edges,
                                            caption=caption)
            return view, True
        # 降级 ③：文本摘要证据（构造上到不了：②已把标签截到 40 字符；留作兜底，不静默）
        summary = summarize_text("\n".join(str(line) for line in plan["candidates"]),
                                 _SUMMARY_SIDE_CHARS,
                                 note=("超限降级：候选清单文本摘要" if lang == "zh"
                                       else "oversize → candidate text summary"))
        view = self.service.add_diff(record_id, before="", after=summary,
                                     filename="candidates",
                                     caption=(caption + "·超限降级文本摘要") if lang == "zh"
                                     else (caption + " (oversize → text summary)"))
        return view, True


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.evidence_wiring``：临时目录里跑一遍触发→降级→台账。"""
    import pathlib
    import shutil
    import sys
    import tempfile

    from .feature_store import FeaturePaths

    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(temp)
        paths.ensure()
        wiring = EvidenceWiring(evidence_mod.EvidenceService(paths))
        decided = {"id": "act-wire-1", "status": "modified",
                   "tool_used": {"name": "filesystem.remove"},
                   "intervention_options": [
                       {"id": "opt-a", "kind": "approve", "label": "同意"},
                       {"id": "opt-b", "kind": "modify", "label": "改后同意"},
                       {"id": "opt-c", "kind": "reject", "label": "拦下"}],
                   "user_intervention": {"decision": "modify", "option_id": "opt-b",
                                         "modified_fields": [
                                             {"name": "max_delete_count",
                                              "before": 12, "after": 6}],
                                         "original_params": {"max_delete_count": 12},
                                         "modified_params": {"max_delete_count": 6}}}
        result = wiring.generate(decided)
        assert result["attempts"] == 2 and result["degraded"] == 0, result
        kinds = {item["kind"] for item in result["items"]}
        assert kinds == {"diff", "diagram"}, kinds
        for item in result["items"]:
            target = wiring.service.file_path(item["id"])
            assert target and target.exists(), item
            import hashlib
            raw = target.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
            assert len(raw) == item["bytes"]
        # 零误触发：单候选 + 无差异
        quiet = dict(decided)
        quiet["intervention_options"] = [{"id": "opt-a", "kind": "approve", "label": "同意"}]
        quiet["user_intervention"] = {"decision": "approve", "option_id": "opt-a",
                                      "original_params": {"x": 1}, "modified_params": {}}
        empty = wiring.generate(quiet)
        assert empty["items"] == [] and empty["attempts"] == 0, empty
        assert wiring.skip_reasons.get("no_trigger_condition") == 1, wiring.skip_reasons
        # 超限降级：300KB 的参数文本
        big = dict(decided)
        big["id"] = "act-wire-2"
        big["user_intervention"] = {"decision": "modify", "option_id": "opt-b",
                                    "modified_fields": [
                                        {"name": "payload",
                                         "before": "b" * (300 * 1024),
                                         "after": "a" * (300 * 1024)}]}
        down = wiring.generate(big)
        assert down["attempts"] == 2 and down["degraded"] == 1, down
        assert wiring.degraded == 1, wiring.facts()
        assert max(item["bytes"] for item in down["items"]) <= EVIDENCE_ITEM_MAX_BYTES, down
        rate = wiring.success_rate()
        assert rate is not None and rate >= 0.95, wiring.facts()
        # -- R3-2 因果解释：模板形态 / 逐字取数 / 零触发 / 离线复算 ----------------
        causal = wiring.causal(decided)
        exp = causal["explanation"]
        assert exp and causal["truncated"] == 0 and not causal["skipped"], causal
        assert set(exp) == set(CAUSAL_KEYS), exp          # schema 1257 的封闭形状
        assert exp["model"] == "template" and exp["language"] == "zh", exp
        assert len(exp["counterfactual"]) <= CAUSAL_LIMITS["counterfactual"], exp
        # 文案里的每个事实都来自上面那份记录：参数改动逐字可见，
        # 未选候选里第一个的 label 逐字可见（夹具的候选**没有 consequence**，
        # 所以句子必须明说"记录里没有写它的后果"——不替记录编一句后果）
        assert "max_delete_count 从 12 改成 6" in exp["counterfactual"], exp
        assert "「同意」" in exp["counterfactual"], exp
        assert "记录里没有写它的后果" in exp["counterfactual"], exp
        alts = exp["alternatives"]
        assert [row["option"] for row in alts[:2]] == ["同意", "拦下"], alts
        assert all(row["expected"] == "" for row in alts[:2]), alts
        assert any(row["expected"] == "12" for row in alts), alts
        # 离线复算：固定时钟下同一输入两次产出逐字相同（generated_at 也跟着定死）
        fixed = EvidenceWiring(evidence_mod.EvidenceService(
            paths, now=lambda: "2026-01-01T00:00:00Z"))
        first = fixed.causal(decided)["explanation"]
        second = fixed.causal(decided)["explanation"]
        assert first == second, (first, second)
        # 零触发：单候选 + 参数没改 → 没有"没走的路"，不产解释、不进分母
        quiet_causal = wiring.causal(quiet)
        assert quiet_causal["explanation"] is None, quiet_causal
        assert wiring.causal_skip_reasons.get("no_trigger_condition") == 1, wiring.causal_facts()
        assert wiring.causal_attempts == 1, wiring.causal_facts()   # 只有 decided 那一次
        # 前置微修（PM-VERDICT-R3-1 §3.3 遗留 3）：全新数据根不 ensure() 也能直接写盘
        fresh_root = pathlib.Path(temp) / "fresh-root"
        fresh = evidence_mod.EvidenceService(FeaturePaths(fresh_root))
        shot = fresh.add_diff("act-fresh", before="a", after="b")
        stored = fresh.file_path(shot["id"])
        assert stored and stored.exists(), shot
        assert wiring.causal_failures == 0 and wiring.last_causal_problem is None
        shutil.rmtree(temp, ignore_errors=True)
        print(f"evidence_wiring self-check ok: attempts={wiring.attempts} "
              f"successes={wiring.successes} degraded={wiring.degraded} "
              f"skips={wiring.skips} rate={rate:.2f} "
              f"causal_generated={wiring.causal_generated} "
              f"causal_skips={wiring.causal_skips} "
              f"causal_truncated={wiring.causal_truncated} fresh_root_write=ok")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_check())
