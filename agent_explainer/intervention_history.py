"""S0-1：``intervention_history`` 的运行时写入器 + 读时组装。

口径（docs/07 §3.2、schema 1393-1403、任务 §2）：

* **每次干预独立成条 signed ActionRecord**（``action_type = "intervention"``）：
  ``parent_action_id`` 指向被干预记录，``user_intervention.audit_entry_hash`` 接住那条
  ``intervention.decision`` 审计事件的 ``entry_hash`` → 记录 ↔ 审计双向可追。
* **被干预记录本体永不落盘 ``intervention_history``**：它已在
  ``server.EXPANSION_PROTECTED_FIELDS`` 的禁入参名单里；往已签名记录上追加/修改数组字段
  会破坏 ``entry_hash``（签名覆盖整条记录体），所以历史只在读时于**对端**组装。
* **读时组装**：``/api/records`` 响应里按 ``parent_action_id`` 把干预记录归组为目标记录的
  ``intervention_history`` 数组（时间升序、上限 50 = schema 1398 的 ``maxItems``，
  条目为对象 = schema 1399）。有归属的干预记录不在顶层重复出现，但随时可用
  ``GET /api/records/<干预记录 id>`` 逐条取回并验签；孤儿干预记录（目标不在本批）留在顶层，
  绝不让数据凭空消失。归组只认 ``source.adapter = "tracelens-intervention"`` 的落款：
  外部适配器自己产出的 ``action_type="intervention"`` 记录不属于本机干预历史，留在顶层。

契约字段 → schema 落点（根 ``additionalProperties: false``，所以**不能用自造字段名**）：
``target_action_id`` → 根 ``parent_action_id``；``decision`` / ``actor_id`` /
``actor_role`` / ``modified_params`` / ``reason`` / ``audit_entry_hash`` →
``user_intervention.*``（根 ``actor_id`` 同时写决定人，与审计 ``actor_id`` 一致）。

只用标准库。签名由调用方（``server.InterveneService.sign_on_write_record`` →
``secretariat.sign_record``）完成：``prev_hash`` 取该 Agent 的链头，与 rollback 的范式一致
（构造范式见 ``rollback.py`` 的 ``_build_rollback_record``）。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from .feature_store import hash_object, new_id, utc_stamp

__all__ = ["INTERVENTION_ACTION_TYPE", "INTERVENTION_ID_PREFIX", "HISTORY_LIMIT",
           "build_intervention_record", "history_entry", "is_intervention_record",
           "assemble_intervention_history"]

#: schema 133 行的 action_type 枚举里已有 ``intervention``，这里只把口径钉成常量
INTERVENTION_ACTION_TYPE = "intervention"
INTERVENTION_ID_PREFIX = "act_iv"
INTERVENTION_VERSION = "1.0"
INTERVENTION_TEMPLATE_ID = "intervention.hr.v1"
INTERVENTION_ADAPTER = "tracelens-intervention"
#: 决定 ≠ 生效（C11 / 假闭环禁令）。与 ``server.EFFECT_NOTICE_KEY`` 同值：本模块不得
#: 反向 import server（server 依赖本模块），所以在这里独立声明。
INTERVENTION_EFFECT_NOTICE = "intervention.effect.recorded_only"
#: schema 1398：``intervention_history.maxItems = 50``
HISTORY_LIMIT = 50

#: 以下枚举与 schema 逐字对齐：越界值不写进记录，避免造出一条自家 schema 不过的记录
_DECISIONS = ("approve", "modify", "reject")
_STATUS_BY_DECISION = {"approve": "approved", "modify": "modified", "reject": "rejected"}
_ROLES = ("local", "admin", "editor", "viewer")
_MIN_ROLES = ("local", "editor", "admin")
_RISK_LEVELS = ("low", "medium", "high", "critical")
_HIGH_RISK = ("high", "critical")
_STATUSES = ("observed", "pending_confirmation", "approved", "modified", "rejected",
             "running", "succeeded", "failed", "skipped", "expired")
_AUDIT_FIELDS = ("code", "human_reason", "failed_step", "retryable",
                 "technical_detail_ref")

#: 目标记录 ``user_intervention`` 里允许搬过来的键（该对象 ``additionalProperties: false``，
#: 多余的键会让这条记录当场不合规）
_UI_KEYS = ("decision", "option_id", "decided_by", "decided_by_role", "decided_at",
            "original_params", "modified_params", "modified_fields", "reason",
            "recorded", "request_id", "audit_entry_hash")
#: 目标 ``policy`` 里允许继承的键（同样 additionalProperties: false）
_POLICY_KEYS = ("requires_human_confirmation", "auto_approve_forbidden",
                "timeout_behavior", "queue_serial", "min_role_to_decide")


# --------------------------------------------------------------------------- #
# 小工具：把任意来源值收敛成 schema 认得的形态（不猜、不静默补假数据）
# --------------------------------------------------------------------------- #

def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value))


def _safe_id(value: Any, *, fallback: str) -> str:
    """收敛成 schema 的 identifier 形态（``^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$``）。"""
    text = _safe_text(value)
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._:-")[:120]
    if not cleaned or not cleaned[0].isalnum():
        return fallback
    return cleaned


def _clip(text: Any, limit: int) -> str:
    text = " ".join(_safe_text(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _one_of(value: Any, allowed: Sequence[str], fallback: str | None) -> str | None:
    text = _safe_text(value)
    return text if text in allowed else fallback


def _tool_name(record: Mapping[str, Any]) -> str:
    tool_used = record.get("tool_used")
    if isinstance(tool_used, Mapping) and tool_used.get("name"):
        return _clip(tool_used.get("name"), 120)
    if isinstance(tool_used, str) and tool_used:
        return _clip(tool_used, 120)
    return _clip(record.get("action_type") or "action", 120)


def is_intervention_record(record: Any) -> bool:
    """是不是**本运行时写下的**干预记录。

    只认 ``action_type == "intervention"`` 且 ``source.adapter`` 是本模块的落款：
    schema 的 action_type 枚举里 ``intervention`` 是公开值，外部适配器（langfuse /
    mcp_events / 原生导入）本来就能产出这类记录，那些记录**不是**本机干预历史，
    读时组装绝不许把它们从列表里吞掉（``tests/test_import_persistence.py`` 的
    适配器夹具就钉着这条）。
    """
    if not isinstance(record, Mapping):
        return False
    if str(record.get("action_type") or "") != INTERVENTION_ACTION_TYPE:
        return False
    source = record.get("source")
    return bool(isinstance(source, Mapping)
                and str(source.get("adapter") or "") == INTERVENTION_ADAPTER)


# --------------------------------------------------------------------------- #
# 写入侧：构造一条独立的干预记录（签名由调用方做，本模块不碰密钥）
# --------------------------------------------------------------------------- #

def build_intervention_record(decided: Mapping[str, Any], *,
                              decision: Any = None,
                              actor_id: Any = None,
                              actor_role: Any = None,
                              request_id: Any = None,
                              audit_entry_hash: Any = None,
                              generated_at: Any = None,
                              intervention_id: Any = None) -> dict[str, Any]:
    """照 ``rollback._build_rollback_record`` 的范式构造**一条新记录**（未签名）。

    入参 ``decided`` 是"决定之后"的被干预记录（``apply_decision`` 的产物）：决定段、
    agent / tool / risk / 上下文线索全从它继承，语义不变、字段名按既有惯例落位。

    铁律：绝不返回 ``intervention_history`` 键（铁律 3），绝不修改入参（铁律 2）。
    """
    if not isinstance(decided, Mapping):
        raise TypeError("build_intervention_record 需要一条记录对象")
    source_ui = decided.get("user_intervention")
    source_ui = source_ui if isinstance(source_ui, Mapping) else {}

    target_id = _safe_id(decided.get("id"), fallback="unknown-action")
    dec = _one_of(decision if decision is not None else source_ui.get("decision"),
                  _DECISIONS, "")
    if not dec:
        # 决定枚举不认识：宁可早失败，也不写一条"状态与决定对不上"的记录（§3.4 不许猜）
        raise ValueError(f"未知的干预决定：{decision!r}")
    actor = _safe_id(actor_id if actor_id is not None else source_ui.get("decided_by"),
                     fallback="local-user")
    role = _one_of(actor_role if actor_role is not None else source_ui.get("decided_by_role"),
                   _ROLES, "local")
    stamp = _safe_text(generated_at) or utc_stamp()
    readable = decided.get("human_readable")
    lang = "en" if str((readable.get("language") if isinstance(readable, Mapping) else "")
                       or "").lower().startswith("en") else "zh"
    risk = _one_of(decided.get("risk_level"), _RISK_LEVELS, "low")
    status = _STATUS_BY_DECISION[dec]      # schema C2：status 与 decision 一一对应
    tool = _tool_name(decided)
    verb = {"approve": "放行", "modify": "改后放行", "reject": "拦下"}[dec] if lang == "zh" \
        else {"approve": "approved", "modify": "modified", "reject": "rejected"}[dec]
    label = _clip(tool, 28)

    # 决定段：把被干预记录上的决定段整体搬过来（含 original/modified_params 与 reason，
    # 那是 schema C3/C4 的硬要求），再补上这次的 request_id 与审计 entry_hash。
    intervention: dict[str, Any] = {}
    for key in _UI_KEYS:
        if key in source_ui:
            intervention[key] = copy.deepcopy(source_ui[key])
    intervention["decision"] = dec
    intervention["decided_by"] = actor
    intervention["decided_by_role"] = role
    intervention["decided_at"] = _safe_text(source_ui.get("decided_at")) or stamp
    intervention["recorded"] = True
    if intervention.get("option_id") is not None:
        intervention["option_id"] = _clip(intervention.get("option_id"), 128)
    if intervention.get("reason") is not None:
        intervention["reason"] = _clip(intervention.get("reason"), 500)
    clean_request = _clip(request_id if request_id is not None
                          else source_ui.get("request_id"), 64)
    intervention["request_id"] = clean_request or None
    clean_audit = _safe_text(audit_entry_hash if audit_entry_hash is not None
                             else source_ui.get("audit_entry_hash"))
    if clean_audit:
        intervention["audit_entry_hash"] = clean_audit
    if dec != "modify":
        # C3 只在 decision=modify 时要求非空 params；别的决定带上参数会变成"看着像改过"
        intervention.pop("modified_params", None)
        intervention.pop("modified_fields", None)
    elif (not isinstance(intervention.get("modified_params"), Mapping)
          or not intervention["modified_params"]
          or not isinstance(intervention.get("original_params"), Mapping)
          or not intervention["original_params"]):
        # schema C3 要求 modify 两侧都是非空对象。``apply_decision`` 保证了这一点；
        # 真缺了就是上游脏数据，写一条"声称改过却没参数"的记录等于伪造决定语义 → 失败。
        raise ValueError("decision=modify 缺少非空的 modified_params/original_params")

    audit_tail = (clean_audit[:16] + "…") if len(clean_audit) > 16 else (clean_audit or "未记录")
    if lang == "zh":
        l1 = _clip(f"已干预：{verb} {label}", 80)
        l2 = [_clip(f"决定：{dec}（决定人 {actor} / 角色 {role}）", 80),
              _clip(f"被干预记录：{target_id}", 80),
              "决定 ≠ 生效：执行端未回执（unreported）"]
        l3 = _clip(f"{tool} · 决定={dec} · 风险={risk} · 审计 entry_hash={audit_tail} · "
                   "本条是独立签名记录，历史只在读时组装", 2000)
        intent = _clip(f"记录对 {target_id} 的人工干预决定（{dec}）", 500)
        rationale = _clip(intervention.get("reason")
                          or f"人工在 {dec} 中拍板；本条把这次决定独立签名存档。", 1000)
        result_summary = _clip(f"已为 {target_id} 追加一条独立签名的干预记录，状态记为 "
                               f"{status}（决定已记录，未生效）", 1000)
        receipt_detail = "人工决定已记录；外部系统未被调用，执行端未回执"
        scope_summary = _clip(f"更新记录 {target_id} 的决定状态（决定 ≠ 生效）", 300)
    else:
        l1 = _clip(f"Intervened: {verb} {label}", 80)
        l2 = [_clip(f"Decision: {dec} (by {actor} / role {role})", 80),
              _clip(f"Target action: {target_id}", 80),
              "Decision is not execution: receipt stays unreported"]
        l3 = _clip(f"{tool} · decision={dec} · risk={risk} · audit entry_hash={audit_tail} · "
                   "standalone signed record; history assembled at read time", 2000)
        intent = _clip(f"Record the human intervention decision ({dec}) for {target_id}", 500)
        rationale = _clip(intervention.get("reason")
                          or f"Human decided {dec}; this standalone record signs that decision.",
                          1000)
        result_summary = _clip(f"Standalone signed intervention record appended for {target_id}; "
                               f"status {status} (decision recorded, not executed)", 1000)
        receipt_detail = "Human decision recorded; external systems not called; no receipt"
        scope_summary = _clip(f"Decision state of record {target_id} updated "
                              "(decision ≠ execution)", 300)

    facts = {"target_action_id": target_id, "decision": dec, "actor_id": actor,
             "actor_role": role, "status": status, "risk_level": risk,
             "audit_entry_hash": clean_audit or None, "language": lang}
    human_readable = {
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "language": lang,
        "generated_by": "template",
        "template_id": INTERVENTION_TEMPLATE_ID,
        "model": None,
        "generated_at": stamp,
        "may_contain_inference": False,
        "source_facts_hash": "sha256:" + hash_object(facts),
    }

    # policy：继承被干预记录；高危必须人确认（schema C9），两个常量断言钉死
    source_policy = decided.get("policy")
    policy: dict[str, Any] = {}
    if isinstance(source_policy, Mapping):
        for key in _POLICY_KEYS:
            if key in source_policy:
                policy[key] = copy.deepcopy(source_policy[key])
    policy["requires_human_confirmation"] = bool(
        policy.get("requires_human_confirmation")) or risk in _HIGH_RISK
    policy["auto_approve_forbidden"] = True          # schema const：写 false = 非法记录
    policy["timeout_behavior"] = "keep_pending"      # schema const：同上
    policy["queue_serial"] = bool(policy.get("queue_serial", False))
    if _one_of(policy.get("min_role_to_decide"), _MIN_ROLES, None) is None:
        policy.pop("min_role_to_decide", None)

    demo = bool(decided.get("demo_data"))
    synthetic = bool(decided.get("synthetic")) or demo
    # C12/C15：演示数据必须 source.kind=demo；非演示用 manual（不算实时上报/导入，
    # 不会把 /api/records 的 ingested_count、imported_count 灌水）
    source_kind = "demo" if demo else "manual"

    context_source = decided.get("context")
    context_source = context_source if isinstance(context_source, Mapping) else {}
    raw_task = context_source.get("task_name")
    context = {
        "task_name": (_clip(raw_task, 200)
                      if isinstance(raw_task, str) and raw_task.strip() else None),
        "audience_profile": _one_of(context_source.get("audience_profile"),
                                    ("A", "B", "C"), "C"),
        "default_granularity": _one_of(context_source.get("default_granularity"),
                                       ("L1", "L2", "L3"), "L1"),
        "user_language": "en" if lang == "en" else "zh",
    }

    record: dict[str, Any] = {
        "schema_version": decided.get("schema_version") if isinstance(
            decided.get("schema_version"), str) else "1.1",
        "id": _safe_id(intervention_id, fallback=new_id(INTERVENTION_ID_PREFIX)),
        "timestamp": stamp,
        "received_at": stamp,
        "sequence_no": None,
        "agent_id": _safe_id(decided.get("agent_id"), fallback="tracelens-unknown"),
        "agent_name": (_clip(decided.get("agent_name"), 120)
                       if _safe_text(decided.get("agent_name")) else None),
        "actor_id": actor,
        "workspace_id": _safe_id(decided.get("workspace_id"), fallback="default"),
        "session_id": _safe_id(decided.get("session_id"), fallback="local-session"),
        "run_id": (_safe_id(decided.get("run_id"), fallback="")
                   if _safe_text(decided.get("run_id")) else None),
        # 契约里的 target_action_id：落在 schema 既有的链字段上
        "parent_action_id": target_id,
        "depth": int(decided.get("depth") or 0) + 1,
        "action_type": INTERVENTION_ACTION_TYPE,
        "tool_used": {
            "name": tool,
            "version": INTERVENTION_VERSION,
            "provider": "local",
            "call_id": None,
            "params_summary": _clip(f"intervention {dec} on {target_id}", 500),
            "result_ref": None,
        },
        "intent": intent,
        "rationale": rationale,
        "result_summary": result_summary,
        "human_readable": human_readable,
        "confidence": 1.0,
        "confidence_source": {
            "method": "template",
            "logprob": None,
            "tool_success_rate": None,
            "samples": 1,
            "computed_at": stamp,
        },
        "uncertainty": None,
        "cost": None,
        "latency_ms": 0,
        "status": status,
        "requires_action": False,
        "requires_action_reason": None,
        "action_due_at": None,
        "intervention_options": [],
        "user_intervention": intervention,
        "execution_receipt": {
            "state": "unreported",
            "reported_by": None,
            "reported_at": None,
            "detail": _clip(receipt_detail, 500),
            "sdk_version": None,
            "effect_notice": INTERVENTION_EFFECT_NOTICE,
        },
        "risk_level": risk,
        "impact_scope": {
            "targets": [{"kind": "record", "ref": target_id, "change": "update", "count": 1}],
            "money": None,
            "external_endpoint": None,
            "data_categories": ["none"],
            "reversible": False,
            "summary": scope_summary,
        },
        "policy": policy,
        "error": None,
        "suggested_next": [],
        "context": context,
        "trace_link": (copy.deepcopy(decided.get("trace_link"))
                       if isinstance(decided.get("trace_link"), Mapping) else None),
        "source": {
            "kind": source_kind,
            "adapter": INTERVENTION_ADAPTER,
            "format_version": INTERVENTION_VERSION,
            "raw_ref": None,
            "source_uri": None,
        },
        "demo_data": demo,
        "synthetic": synthetic,
    }
    group_id = _safe_id(decided.get("group_id"), fallback="")
    if group_id:
        record["group_id"] = group_id
    # 铁律 3：这条记录里永远不出现 intervention_history 键
    record.pop("intervention_history", None)
    return record


# --------------------------------------------------------------------------- #
# 读时组装：把散落的干预记录归组为目标记录的历史视图（不落盘）
# --------------------------------------------------------------------------- #

def history_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    """一条干预记录 → 历史条目（对象；schema 1399 只要求对象）。

    条目带 ``entry_hash`` 与 ``signed``：视图里的每一行都能追回那条独立签名记录。
    """
    ui = record.get("user_intervention")
    ui = ui if isinstance(ui, Mapping) else {}
    reason = ui.get("reason")
    return {
        "id": _safe_id(record.get("id"), fallback="unknown-intervention"),
        "action_type": _safe_text(record.get("action_type")) or INTERVENTION_ACTION_TYPE,
        "target_action_id": (_safe_id(record.get("parent_action_id"), fallback="") or None),
        "timestamp": (_safe_text(record.get("timestamp")) or None),
        "status": (_safe_text(record.get("status")) or None),
        "decision": (_one_of(ui.get("decision"), _DECISIONS, None)),
        "actor_id": (_safe_id(record.get("actor_id"), fallback="") or None),
        "actor_role": _one_of(ui.get("decided_by_role"), _ROLES, None),
        "reason": (_clip(reason, 500) if reason is not None else None),
        "modified_params": (copy.deepcopy(ui.get("modified_params"))
                            if isinstance(ui.get("modified_params"), Mapping) else None),
        "audit_entry_hash": (_safe_text(ui.get("audit_entry_hash")) or None),
        "entry_hash": (_safe_text(record.get("entry_hash")) or None),
        "signed": bool(record.get("signature")),
    }


def _sort_key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (str(entry.get("timestamp") or ""), str(entry.get("id") or ""))


def assemble_intervention_history(records: Sequence[Any], *,
                                  limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """返回**新的**记录列表：目标记录上挂 ``intervention_history`` 视图数组。

    * 只在响应里挂：入参记录对象不被改写（``store.all()`` 给的是深拷贝，这里再拷一层，
      所以绝不会有 ``intervention_history`` 落盘）；
    * 上限 ``limit``（默认 50 = schema ``maxItems``），按时间升序保留最近的那些；
    * 有归属的干预记录从顶层列表归组掉（不重复显示）；孤儿干预记录（目标已不在本批）
      留在顶层，绝不让数据凭空消失。
    """
    rows = [copy.deepcopy(dict(item)) for item in records if isinstance(item, Mapping)]
    present = {str(row.get("id")) for row in rows}
    by_target: dict[str, list[dict[str, Any]]] = {}
    absorbed: set[str] = set()
    for row in rows:
        if not is_intervention_record(row):
            continue
        target = _safe_id(row.get("parent_action_id"), fallback="")
        if not target or target not in present:
            continue                                    # 孤儿：留在顶层，不隐藏数据
        by_target.setdefault(target, []).append(history_entry(row))
        absorbed.add(str(row.get("id")))
    cap = limit if isinstance(limit, int) else HISTORY_LIMIT
    cap = max(0, min(int(cap), HISTORY_LIMIT))
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("id")) in absorbed:
            continue
        history = by_target.get(str(row.get("id")))
        if history:
            history.sort(key=_sort_key)
            row["intervention_history"] = history[-cap:] if cap else []
        out.append(row)
    return out
