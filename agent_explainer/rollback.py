"""干预回滚（规格书功能一）—— 逆向操作注册表 + 回滚计划 + 本地回执 + 撤销日志。

为什么要单独一个"回滚"模块
--------------------------
"回滚"最容易做成一句骗人的按钮文案。本模块把规格书功能一拆成三个可分别验证的事实：

1. **机制覆盖**：每个有副作用的步骤，系统是否**登记了**逆向动作（:func:`coverage`
   给出比例；演示集目标 ≥ 90%）。没有登记的步骤绝不是"可以撤销"，而是"我们不知道
   怎么撤销"——界面必须照实说。
2. **影响面判断**：注册表有逆向动作 ≠ 这条记录可以撤销。来源在
   ``impact_scope.reversible`` 里明说"不可逆"时（删除不进回收站、邮件已发出），
   :func:`reversible` 必须返回 ``False``，界面照实显示"此操作不可撤销"（W-03）。
3. **回执边界**：本服务**不真的去调外部系统**（离线、无凭据）。执行只做两件事：
   写 ``<data>/features/rollback/journal.jsonl`` 的**回滚前快照**，以及返回一条
   ``simulated_local`` 的逆向回执。回滚 ActionRecord 的 ``execution_receipt.state``
   如实写 ``unreported``（外部系统没回执），绝不写"已恢复"。

为什么依赖顺序要参与回滚
------------------------
"撤销 A" 之前必须先撤销**依赖 A 的后续动作**，否则撤销顺序反了会留下悬空引用
（例如先恢复文件、再撤销一条引用该文件的笔记）。依赖关系有两条来源，本模块都认：
``parent_action_id``（动作树）与 ``context["depends_on"]``（显式依赖）。深度上限
``max_depth`` 防止一棵异常深的树把局部撤销变成全库回滚。

错误纪律
--------
所有对外抛出的错误都带 ``docs/07`` §7 的 ``rollback.*`` 错误码，并可在 ``detail``
里带上完整计划——前端据此展示"为什么不能回滚"，而不是一句 500。
"""

from __future__ import annotations

import copy
import pathlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .feature_store import (
    FeaturePaths,
    hash_object,
    new_id,
    open_store,
    utc_stamp,
)

__all__ = [
    "ROLLBACK_VERSION",
    "ROLLBACK_DIR",
    "DEFAULT_MAX_DEPTH",
    "NO_EFFECT_ACTIONS",
    "NOT_EXECUTED_STATUSES",
    "REQUIRES_CONFIRMATION_RISKS",
    "RollbackError",
    "register_inverse",
    "unregister_inverse",
    "inverse_of",
    "registered_tools",
    "reversible",
    "plan",
    "RollbackService",
]

#: 模块契约版本（写进回滚记录的 ``source.format_version``）
ROLLBACK_VERSION = "1.0"
#: 相对 ``FeaturePaths.root`` 的子目录名（只用于自检/文档，路径由 FeaturePaths 给）
ROLLBACK_DIR = "rollback"
#: 默认依赖递归深度
DEFAULT_MAX_DEPTH = 3
#: 这些动作**本身不产生外部副作用**：没有可撤销的东西，按"天然覆盖"处理
NO_EFFECT_ACTIONS = frozenset({"plan", "read", "confirm", "ask", "wait", "finish", "intervention"})
#: 这些状态说明步骤尚未真正执行：撤销路径 = 取消，而不是"恢复"
NOT_EXECUTED_STATUSES = frozenset({"pending_confirmation", "expired", "rejected", "skipped"})
#: 高危/严重风险的回滚必须再确认一次（产品 W-02 同一口径）
REQUIRES_CONFIRMATION_RISKS = frozenset({"high", "critical"})
#: 缺省回执说明（界面必须按这个字显示，不许写"已恢复"）
SIMULATED_NOTE = "本地模拟执行；外部系统未实际调用（离线降级）"

#: 允许来源标注的影响面"不可逆"。
_IRREVERSIBLE = object()

# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #


class RollbackError(Exception):
    """回滚域错误：机器码 + 人话原因 + 取证细节（与 ``FeatureStoreError`` 同形）。

    前端按 ``code`` 找 i18n 文案（``rollback.error.<code 小写>``）；``detail`` 里
    可以带完整计划，让"为什么不能回滚"变成可展示的数据而不是一句错误 toast。
    """

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 detail: Mapping[str, Any] | None = None, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.field = field
        self.detail: dict[str, Any] = dict(detail or {})
        self.hint = hint

    def render(self) -> str:
        tail = f"（{self.field}）" if self.field else ""
        return f"{self.code}: {self.reason}{tail}"

    def as_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "reason": self.reason}
        if self.field:
            payload["field"] = self.field
        if self.detail:
            payload["detail"] = copy.deepcopy(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


# --------------------------------------------------------------------------- #
# 逆向操作注册表
# --------------------------------------------------------------------------- #
#
# 表项 = {"op": 逆向动作 id, "describe_key": i18n 文案键}。
# apply（可选）= 调用方注入的真实逆向执行器；缺省 = 本地模拟（只写日志 + 回执）。
# 注册表是进程内全局表：它描述的是"产品知道怎么撤销哪些工具"，不随某次请求变化。

_INVERSE_REGISTRY: dict[str, dict[str, str]] = {}
_INVERSE_APPLIERS: dict[str, Callable[..., Any]] = {}

#: 工具名（小写）→ 逆向动作。精确匹配优先，其次按子串兜底。
#: 之所以要有兜底：外部工具名千奇百怪（filesystem.remove / fs.rm / rm_rf），
#: 只认死名单会让覆盖率变成"名单里恰好有几个"的假指标。
_GENERIC_FALLBACKS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("remove", "delete", "trash", "purge", "erase", "rm_"),
     "restore_file", "rollback.inverse.restore_file"),
    (("send", "email", "mail", "sms", "notify", "post_message"),
     "revoke_message", "rollback.inverse.revoke_message"),
    (("set_permission", "permission", "grant", "policy", "setting", "config", "toggle"),
     "undo_config_change", "rollback.inverse.undo_config_change"),
    (("booking", "reserve", "appointment", "schedule", "order"),
     "cancel_booking", "rollback.inverse.cancel_booking"),
    (("note", "memo", "comment", "doc_write"),
     "undo_note", "rollback.inverse.undo_note"),
    (("generate", "preview", "thumbnail", "render", "snapshot", "export"),
     "delete_generated", "rollback.inverse.delete_generated"),
    (("write", "save", "append", "create", "upload", "put_"),
     "undo_write", "rollback.inverse.undo_write"),
    (("read", "scan", "list", "search", "query", "compare", "hash", "fetch", "get_"),
     "noop_read", "rollback.inverse.noop_read"),
)
#: 兜底命中缓存：避免每次 plan 都重复扫一遍表（注册表变动时清空）
_FALLBACK_CACHE: dict[str, dict[str, str] | None] = {}


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tool_key(tool: Any) -> str:
    return _safe_text(tool).lower()


def register_inverse(tool: str, *, op: str, describe_key: str,
                     apply: Callable[..., Any] | None = None) -> None:
    """登记"工具 → 逆向动作"。

    参数是关键字契约（``op`` / ``describe_key`` / ``apply``）；字段非法时抛
    ``ROLLBACK_FIELD_INVALID``，而不是静默忽略——注册失败比"以为登记了"安全。

    ``apply`` 是可选的真实逆向执行器，签名约定::

        apply(record, inverse, *, actor, reason) -> Mapping | None

    返回值会并入回执（``{"op","applied","receipt_state","note"}``）。本产品默认
    不注入任何执行器：离线环境没有外部系统凭据，回执如实写 ``simulated_local``。
    """
    name = _safe_text(tool)
    if not name:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "工具名不能为空", field="tool")
    action = _safe_text(op)
    if not action:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "逆向动作 op 不能为空",
                            field="op", detail={"tool": name})
    describe = _safe_text(describe_key)
    if not describe:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "逆向动作必须带可翻译的文案键",
                            field="describe_key", detail={"tool": name, "op": action})
    if apply is not None and not callable(apply):
        raise RollbackError("ROLLBACK_FIELD_INVALID", "apply 必须是可调用对象",
                            field="apply", detail={"tool": name, "op": action})
    _INVERSE_REGISTRY[name] = {"op": action, "describe_key": describe}
    if apply is not None:
        _INVERSE_APPLIERS[name] = apply
    else:
        _INVERSE_APPLIERS.pop(name, None)
    _FALLBACK_CACHE.clear()


def unregister_inverse(tool: str) -> bool:
    """移除登记（主要给测试/插件卸载用；返回是否真的移除了）。"""
    name = _safe_text(tool)
    removed = _INVERSE_REGISTRY.pop(name, None) is not None
    _INVERSE_APPLIERS.pop(name, None)
    _FALLBACK_CACHE.clear()
    return removed


def _fallback_for(tool: str) -> dict[str, str] | None:
    key = _tool_key(tool)
    if not key:
        return None
    if key in _FALLBACK_CACHE:
        return copy.deepcopy(_FALLBACK_CACHE[key])
    found: dict[str, str] | None = None
    for needles, op, describe in _GENERIC_FALLBACKS:
        if any(needle in key for needle in needles):
            found = {"op": op, "describe_key": describe}
            break
    _FALLBACK_CACHE[key] = found
    return copy.deepcopy(found)


def inverse_of(tool: str) -> dict[str, str] | None:
    """返回 ``{"op","describe_key"}``；没有登记则 ``None``（含兜底子串匹配）。"""
    name = _safe_text(tool)
    if not name:
        return None
    exact = _INVERSE_REGISTRY.get(name)
    if exact is not None:
        return dict(exact)
    return _fallback_for(name)


def registered_tools() -> list[str]:
    """当前精确登记的工具名（排序后，供接口/自检展示）。"""
    return sorted(_INVERSE_REGISTRY)


def _applier_for(tool: str) -> Callable[..., Any] | None:
    return _INVERSE_APPLIERS.get(_safe_text(tool))


def _seed_registry() -> None:
    """预置演示工具与规格书点名的六个逆向动作。

    分两层：
    * **逆向动作本身也作为工具名登记**（``cancel_booking`` 等六个）。规格书点名的
      默认逆向实现必须"点名可用"，即使用一个叫 ``cancel_booking`` 的工具时也能直接命中。
    * **演示集里的真实工具名**精确登记，配合 ``_GENERIC_FALLBACKS`` 覆盖外部未知工具名。
    """
    defaults = (
        ("cancel_booking", "cancel_booking", "rollback.inverse.cancel_booking"),
        ("restore_file", "restore_file", "rollback.inverse.restore_file"),
        ("undo_write", "undo_write", "rollback.inverse.undo_write"),
        ("revoke_message", "revoke_message", "rollback.inverse.revoke_message"),
        ("undo_config_change", "undo_config_change", "rollback.inverse.undo_config_change"),
        ("undo_note", "undo_note", "rollback.inverse.undo_note"),
        # —— 演示集（samples/action_records.json）里的具体工具 ——
        ("filesystem.remove", "restore_file", "rollback.inverse.restore_file"),
        ("filesystem.write_file", "undo_write", "rollback.inverse.undo_write"),
        ("notes.write", "undo_note", "rollback.inverse.undo_note"),
        ("message.send_email", "revoke_message", "rollback.inverse.revoke_message"),
        ("plugin.set_permission", "undo_config_change", "rollback.inverse.undo_config_change"),
        ("thumbnail.generate_preview", "delete_generated", "rollback.inverse.delete_generated"),
        ("image.hash_compare", "noop_read", "rollback.inverse.noop_read"),
        ("filesystem.scan_album", "noop_read", "rollback.inverse.noop_read"),
    )
    for tool, op, describe in defaults:
        _INVERSE_REGISTRY[tool] = {"op": op, "describe_key": describe}
    _FALLBACK_CACHE.clear()


_seed_registry()


# --------------------------------------------------------------------------- #
# 单条记录的可逆判断
# --------------------------------------------------------------------------- #


def _tool_name(record: Any) -> str:
    if not isinstance(record, Mapping):
        return ""
    tool = record.get("tool_used")
    if isinstance(tool, Mapping):
        return _safe_text(tool.get("name"))
    return ""


def reversible(record: Mapping) -> dict[str, Any]:
    """判断**这一条记录**能不能撤销：``{"reversible","op","reason","describe_key"}``。

    两个条件同时成立才算可逆（``docs/07`` §4.5）：

    1. 注册表（或子串兜底）里登记了该工具的逆向动作；
    2. 来源的 ``impact_scope.reversible`` **没有明说 False**。

    第 2 条是产品纪律（W-03）：登记表说"删除可以还原"，但来源说"不进回收站"，那
    事实以来源为准——界面必须写"此操作不可撤销"，不能拿兜底表覆盖来源事实。
    """
    if not isinstance(record, Mapping):
        return {"reversible": False, "op": None, "describe_key": None,
                "reason": "记录不是对象，无法判断可逆性"}
    tool = _tool_name(record)
    inverse = inverse_of(tool) if tool else None
    op = inverse["op"] if inverse else None
    describe = inverse["describe_key"] if inverse else None

    if record.get("rollback_state") == "rolled_back":
        return {"reversible": False, "op": op, "describe_key": describe,
                "reason": "该动作已经回滚过，不能重复回滚"}
    scope = record.get("impact_scope")
    if isinstance(scope, Mapping) and scope.get("reversible") is False:
        return {"reversible": False, "op": op, "describe_key": describe,
                "reason": "来源的影响面把该动作标注为不可撤销（impact_scope.reversible=false）"}
    if inverse is None:
        reason = ("记录没有工具动作，没有可撤销的外部副作用" if not tool
                  else f"逆向操作注册表里没有工具「{tool}」的逆向动作")
        return {"reversible": False, "op": None, "describe_key": None, "reason": reason}
    return {"reversible": True, "op": op, "describe_key": describe,
            "reason": "已登记逆向动作，且来源未标注不可逆"}


# --------------------------------------------------------------------------- #
# 回滚计划
# --------------------------------------------------------------------------- #


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _record_dependencies(record: Mapping) -> set[str]:
    """一条记录显式声明依赖的目标 id 集合（动作树 + context.depends_on）。"""
    deps: set[str] = set()
    parent = record.get("parent_action_id")
    if isinstance(parent, str) and parent:
        deps.add(parent)
    context = record.get("context")
    if isinstance(context, Mapping):
        for item in _as_list(context.get("depends_on")):
            if isinstance(item, str) and item:
                deps.add(item)
    return deps


def _build_index(records: Sequence[Mapping]) -> dict[str, Mapping]:
    index: dict[str, Mapping] = {}
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        key = record.get("id")
        if isinstance(key, str) and key:
            index[key] = record
    return index


def _collect_dependents(index: Mapping[str, Mapping], action_id: str,
                        max_depth: int) -> tuple[list[str], list[str], bool]:
    """广度优先收集依赖 ``action_id`` 的后续步骤。

    返回 ``(dependents, order, truncated)``：

    * ``dependents``：发现顺序（浅层在前）；
    * ``order``：**回滚执行顺序**（深层在前的逆序，最后才是目标本身）；
    * ``truncated``：是否因为 ``max_depth`` 截断了更深的依赖（计划里必须如实说）。
    """
    positions = {key: position for position, key in enumerate(index)}
    depth_of: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(action_id, 0)]
    truncated = False
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            # 只在"确实还有下一层"时标记截断，避免虚报
            if any(current in _record_dependencies(rec) for key, rec in index.items()
                   if key != action_id and key not in depth_of):
                truncated = True
            continue
        for key, record in index.items():
            if key == action_id or key in depth_of:
                continue
            if current not in _record_dependencies(record):
                continue
            depth_of[key] = depth + 1
            queue.append((key, depth + 1))
    dependents = sorted(depth_of, key=lambda key: (depth_of[key], positions.get(key, 0)))
    order = sorted(depth_of, key=lambda key: (-depth_of[key], -positions.get(key, 0)))
    order.append(action_id)
    return dependents, order, truncated


def _safe_id(value: Any, *, fallback: str = "local") -> str:
    """把任意 actor/id 收敛成 schema 的 identifier 形态（禁止空格与路径字符）。"""
    text = _safe_text(value)
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._:-")[:120]
    if not cleaned or not cleaned[0].isalnum():
        return fallback
    return cleaned


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def plan(records: Sequence[Mapping], action_id: str, *,
         max_depth: int = DEFAULT_MAX_DEPTH) -> dict[str, Any]:
    """生成回滚计划（不改任何东西）。

    返回 ``{"ok","action","target","dependents","order","reversible","blockers",
    "requires_confirmation","truncated"}``：

    * ``action`` = 目标 id（前端直接用来高亮）；
    * ``target`` = 目标记录摘要（动作类型/工具/状态/风险）；
    * ``dependents`` = 依赖该步骤的后续动作 id（浅→深）；
    * ``order`` = 回滚执行顺序（深→浅，目标在最后）；
    * ``blockers`` = ``[{code, action_id, reason}]``，非空时 ``ok=False``；
    * ``requires_confirmation`` = 有依赖或高危时必须二次确认。
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "max_depth 必须是非负整数",
                            field="max_depth", detail={"max_depth": max_depth})
    key = _safe_text(action_id)
    if not key:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "action_id 不能为空",
                            field="action_id")
    index = _build_index(records)
    target = index.get(key)
    if target is None:
        raise RollbackError("ROLLBACK_NOT_FOUND", f"找不到要回滚的动作：{key}",
                            field="action_id", detail={"action_id": key},
                            hint="先确认记录是否已导入；回滚只对已存在的记录生效")
    dependents, order, truncated = _collect_dependents(index, key, max_depth)
    verdict = reversible(target)

    blockers: list[dict[str, Any]] = []
    if target.get("rollback_state") == "rolled_back":
        blockers.append({"code": "ROLLBACK_ALREADY_ROLLED_BACK", "action_id": key,
                         "reason": "该动作已经回滚过，撤销日志里也有对应条目"})
    if not verdict["reversible"]:
        blockers.append({"code": "ROLLBACK_NOT_REVERSIBLE", "action_id": key,
                         "reason": verdict["reason"]})
    risk = target.get("risk_level")
    requires_confirmation = bool(dependents) or risk in REQUIRES_CONFIRMATION_RISKS
    return {
        "ok": not blockers,
        "action": key,
        "target": {
            "id": key,
            "action_type": target.get("action_type"),
            "tool": _tool_name(target) or None,
            "status": target.get("status"),
            "risk_level": risk,
            "rollback_state": target.get("rollback_state"),
        },
        "dependents": dependents,
        "order": order,
        "reversible": bool(verdict["reversible"]),
        "blockers": blockers,
        "requires_confirmation": bool(requires_confirmation),
        "truncated": bool(truncated),
    }


# --------------------------------------------------------------------------- #
# 覆盖率
# --------------------------------------------------------------------------- #


def _covered(record: Mapping) -> bool:
    """一步是否"有回滚路径"（见 :meth:`RollbackService.coverage` 的口径说明）。"""
    status = record.get("status")
    if status in NOT_EXECUTED_STATUSES:
        # 尚未执行：撤销路径 = 取消，不需要恢复任何已发生的影响
        return True
    if record.get("action_type") in NO_EFFECT_ACTIONS:
        # 只读/规划类：没有外部副作用，本来就没有要撤销的东西
        return True
    tool = _tool_name(record)
    if not tool:
        return False
    if inverse_of(tool) is None:
        return False
    scope = record.get("impact_scope")
    if isinstance(scope, Mapping) and scope.get("reversible") is False:
        # 已执行且来源标注不可逆 → 不假装有回滚路径
        return False
    return True


# --------------------------------------------------------------------------- #
# 回滚服务
# --------------------------------------------------------------------------- #


class RollbackService:
    """回滚执行 + 撤销日志（本地模拟回执）。

    用法::

        svc = RollbackService(paths)
        info = svc.plan(records, "act-demo-0007")     # 只读
        out = svc.execute(records, "act-demo-0007", actor="local", reason="...")
        out["records"]                                # 已标 rolled_back 的记录副本（服务端落盘）
        out["rollback_record"]                        # 新的回滚 ActionRecord
        svc.journal()                                 # 撤销日志（含回滚前快照）

    ``paths`` 是 :class:`FeaturePaths`；本模块只写 ``<data>`` 下的证据/日志，绝不
    写仓库 ``var/``（调用方传临时目录即可单测）。
    """

    def __init__(self, paths: FeaturePaths, *,
                 applier: Callable[..., Any] | None = None) -> None:
        if not isinstance(paths, FeaturePaths):
            raise TypeError("paths 必须是 feature_store.FeaturePaths")
        if applier is not None and not callable(applier):
            raise TypeError("applier 必须可调用")
        self.paths = paths
        self.paths.ensure()
        #: 调用方注入的真实逆向执行器；缺省 = 本地模拟（simulated_local）
        self.applier = applier

    # -- 读 ---------------------------------------------------------------
    def _journal(self) -> Any:
        store = open_store(self.paths.rollback_journal)
        store.load()
        return store

    def journal(self) -> list[dict[str, Any]]:
        """撤销日志（按写入顺序；每条含 ``snapshot`` 回滚前快照）。"""
        store = self._journal()
        return store.items()

    def coverage(self, records: Sequence[Mapping]) -> dict[str, Any]:
        """可回滚步骤覆盖率：``{"total","reversible","ratio"}``（验收线 ≥ 0.90）。

        口径（必须写清楚，否则指标会变成话术）：

        * **天然覆盖**：只读/规划类步骤没有外部副作用；``pending_confirmation`` /
          ``expired`` / ``rejected`` / ``skipped`` 的步骤还没执行——这两类的撤销路径
          分别是"什么都不用做"和"取消"，因此算覆盖；
        * **机制覆盖**：其余步骤要求注册表里有逆向动作，**且**来源未标注
          ``impact_scope.reversible=false``。已执行又标注不可逆的步骤不计入覆盖——
          它确实没有回滚路径，不能靠兜底表凑指标。
        """
        rows = [row for row in (records or []) if isinstance(row, Mapping)]
        total = len(rows)
        covered = sum(1 for row in rows if _covered(row))
        ratio = (covered / total) if total else 0.0
        return {"total": total, "reversible": covered, "ratio": round(ratio, 4)}

    # -- 写 ---------------------------------------------------------------
    def execute(self, records: Sequence[Mapping], action_id: str, *,
                actor: str, reason: str = "", confirm_all: bool = False,
                lang: str = "zh") -> dict[str, Any]:
        """执行回滚：写撤销日志 + 返回回滚 ActionRecord 与更新后的记录副本。

        语义边界（产品纪律，界面照此显示）：

        * 本服务**不调用外部系统**；逆向动作的 ``receipt_state`` 恒为
          ``simulated_local``，回滚记录的 ``execution_receipt.state`` 恒为
          ``unreported``（外部系统没有回执）。绝不写"已恢复"；
        * 返回 ``records``：原记录（目标 + 受影响的依赖）的副本，``rollback_state``
          已置 ``rolled_back``，由服务端负责落盘；传入的 ``records`` 不会被就地修改；
        * 同一 ``action_id`` 只能成功回滚一次（重复执行 →
          ``ROLLBACK_ALREADY_ROLLED_BACK``），撤销日志本身就是幂等屏障。
        """
        if not isinstance(actor, str) or not _safe_text(actor):
            raise RollbackError("ROLLBACK_FIELD_INVALID", "actor 不能为空", field="actor")
        plan_info = plan(records, action_id)
        key = plan_info["action"]
        rows = [row for row in (records or []) if isinstance(row, Mapping)]
        index = _build_index(rows)
        target = index[key]
        tool = _tool_name(target)

        codes = {item["code"] for item in plan_info["blockers"]}
        if "ROLLBACK_ALREADY_ROLLED_BACK" in codes or self._already_logged(key):
            raise RollbackError(
                "ROLLBACK_ALREADY_ROLLED_BACK",
                f"动作 {key} 已经回滚过，不能重复回滚",
                field="action_id",
                detail={"action_id": key, "plan": plan_info},
                hint="撤销日志是追加式的：重复回滚会掩盖第一次的回执，因此直接拒绝",
            )
        if not plan_info["reversible"]:
            verdict = reversible(target)
            raise RollbackError(
                "ROLLBACK_NOT_REVERSIBLE",
                verdict["reason"] or "该动作没有可用的逆向动作",
                field="action_id",
                detail={"action_id": key, "plan": plan_info,
                        "impact_scope": target.get("impact_scope")},
                hint="来源把影响面标为不可逆时，界面应显示『此操作不可撤销』",
            )
        if plan_info["dependents"] and not confirm_all:
            raise RollbackError(
                "ROLLBACK_DEPENDENTS_PENDING",
                f"动作 {key} 还有 {len(plan_info['dependents'])} 个依赖它的后续步骤，"
                "需要确认一并回滚",
                field="action_id",
                detail={"action_id": key, "plan": plan_info},
                hint="先展示依赖列表，用户确认后再带 confirm_all=true 重试",
            )

        verdict = reversible(target)
        inverse = {"op": verdict["op"], "describe_key": verdict["describe_key"],
                   "tool": tool or None}
        receipt = self._apply_inverse(target, inverse, actor=actor, reason=reason)
        started = utc_stamp()
        affected = list(plan_info["order"])
        rollback = self._build_rollback_record(
            target, action_id=key, actor=actor, reason=reason, receipt=receipt,
            affected=affected, lang=lang,
        )
        journal_entry = {
            "id": new_id("rbj"),
            "action_id": key,
            "tool": tool or None,
            "inverse_op": verdict["op"],
            "snapshot": copy.deepcopy(dict(target)),   # 回滚前快照（完整记录）
            "created_at": started,
            "applied_at": utc_stamp(),
            "result": dict(receipt),
            "actor": _safe_id(actor),
            "reason": reason or "",
            "affected": affected,
            "rollback_record_id": rollback["id"],
            "language": rollback["human_readable"]["language"],
        }
        store = self._journal()
        try:
            store.put(journal_entry)
        except Exception as exc:  # FeatureStoreError 等存储异常统一收敛
            raise RollbackError(
                "ROLLBACK_INVERSE_FAILED",
                f"撤销日志写入失败，回滚未落盘：{exc}",
                detail={"action_id": key, "path": str(self.paths.rollback_journal)},
                hint="检查数据目录可写与磁盘空间；失败时不要向用户显示『已回滚』",
            ) from exc

        updated: list[dict[str, Any]] = []
        for record_id in affected:
            current = index[record_id]
            updated_row = copy.deepcopy(dict(current))
            updated_row["rollback_state"] = "rolled_back"
            labels = list(updated_row.get("labels") or [])
            if "rolled-back" not in labels:
                labels.append("rolled-back")
            updated_row["labels"] = labels
            updated.append(updated_row)

        return {
            "ok": True,
            "rollback_record": rollback,
            "affected": affected,
            "inverse": receipt,
            "order": list(plan_info["order"]),
            "records": updated,
            "snapshot": copy.deepcopy(dict(target)),
            "journal_id": journal_entry["id"],
            "plan": plan_info,
        }

    # -- 内部 -------------------------------------------------------------
    def _already_logged(self, action_id: str) -> bool:
        return any(entry.get("action_id") == action_id for entry in self.journal())

    def _apply_inverse(self, target: Mapping, inverse: Mapping[str, Any], *,
                       actor: str, reason: str) -> dict[str, Any]:
        """执行逆向动作：注入的执行器优先，否则如实返回本地模拟回执。"""
        tool = _tool_name(target)
        applier = self.applier or _applier_for(tool)
        if applier is None:
            return {"op": inverse["op"], "applied": True,
                    "receipt_state": "simulated_local", "note": SIMULATED_NOTE}
        try:
            result = applier(target, dict(inverse), actor=actor, reason=reason)
        except RollbackError:
            raise
        except Exception as exc:  # 执行器自己的异常 → 统一错误码
            raise RollbackError(
                "ROLLBACK_INVERSE_FAILED",
                f"逆向动作 {inverse['op']} 执行失败：{exc}",
                detail={"action_id": target.get("id"), "op": inverse["op"]},
                hint="执行器应幂等；失败时日志不落盘，用户不会看到『已回滚』",
            ) from exc
        receipt = {"op": inverse["op"], "applied": True,
                   "receipt_state": "simulated_local", "note": SIMULATED_NOTE}
        if isinstance(result, Mapping):
            receipt.update(dict(result))
        receipt.setdefault("op", inverse["op"])
        receipt.setdefault("receipt_state", "simulated_local")
        return receipt

    def _build_rollback_record(self, target: Mapping, *, action_id: str, actor: str,
                               reason: str, receipt: Mapping[str, Any],
                               affected: Sequence[str], lang: str) -> dict[str, Any]:
        language = "en" if str(lang).lower().startswith("en") else "zh"
        generated_at = utc_stamp()
        tool = _tool_name(target) or str(target.get("action_type") or "action")
        op = str(receipt.get("op") or "rollback")
        label = _clip(tool, 28)
        dependents = max(0, len(affected) - 1)
        if language == "en":
            l1 = f"Rolled back: {label} (local simulation)"
            l2 = [f"Inverse op: {op}",
                  f"{dependents} dependent step(s) marked rolled back",
                  "External systems were not called; receipt is simulated_local"]
            l3 = (f"{tool} · op={op} · affected={len(affected)} · "
                  "execution_receipt=unreported（no external receipt）")
            result_summary = (f"Rollback recorded for {action_id}; local inverse "
                              f"{op} simulated (external systems not called)")
            intent = f"Roll back the effect of action {action_id}"
            rationale = reason or "The user asked to undo this action; the original step depended on it."
            reason_text = (f"Rollback requested for action {action_id}; see undo journal for the "
                           "pre-state snapshot.")
        else:
            l1 = f"已回滚：{label}（本地模拟）"
            l2 = [f"逆向动作：{op}",
                  f"一并标记后续依赖 {dependents} 个",
                  "未调用外部系统，回执状态 simulated_local"]
            l3 = (f"{tool} · 逆向动作={op} · 影响 {len(affected)} 条 · "
                  "执行端未回执（unreported）")
            result_summary = (f"已记录对 {action_id} 的回滚；本地模拟执行 {op}，"
                              "外部系统未实际调用")
            intent = f"回滚动作 {action_id} 的影响"
            rationale = reason or "用户要求撤销该动作；它影响的后续步骤需要一并回滚。"
            reason_text = f"对动作 {action_id} 执行回滚；回滚前快照见撤销日志。"

        source = target.get("source") if isinstance(target.get("source"), Mapping) else {}
        demo = bool(target.get("demo_data"))
        synthetic = bool(target.get("synthetic"))
        source_kind = "demo" if demo else "manual"
        policy = {
            "requires_human_confirmation": False,
            "auto_approve_forbidden": True,
            "timeout_behavior": "keep_pending",
            "queue_serial": False,
            "min_role_to_decide": "local",
        }
        human_readable: dict[str, Any] = {
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "language": language,
            "generated_by": "template",
            "template_id": "rollback.hr.v1",
            "model": None,
            "generated_at": generated_at,
            "may_contain_inference": False,
        }
        facts = {
            "action_id": action_id,
            "op": op,
            "affected": list(affected),
            "status": "succeeded",
            "language": language,
        }
        human_readable["source_facts_hash"] = "sha256:" + hash_object(facts)
        rollback_id = new_id("act-rb")
        record: dict[str, Any] = {
            "schema_version": "1.1",
            "id": rollback_id,
            "timestamp": generated_at,
            "received_at": generated_at,
            "sequence_no": None,
            "agent_id": _safe_id(target.get("agent_id"), fallback="tracelens-rollback"),
            "agent_name": target.get("agent_name"),
            "actor_id": _safe_id(target.get("actor_id"), fallback="local"),
            "workspace_id": _safe_id(target.get("workspace_id"), fallback="default"),
            "session_id": _safe_id(target.get("session_id"), fallback="local-session"),
            "run_id": target.get("run_id"),
            "parent_action_id": action_id,
            "depth": (int(target.get("depth") or 0) + 1),
            "action_type": "rollback",
            "tool_used": {
                "name": op,
                "version": ROLLBACK_VERSION,
                "provider": "local",
                "call_id": None,
                "params_summary": _clip(f"rollback {action_id}", 500),
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
                "computed_at": generated_at,
            },
            "uncertainty": None,
            "cost": None,
            "latency_ms": 0,
            "status": "succeeded",
            "requires_action": False,
            "requires_action_reason": None,
            "action_due_at": None,
            "intervention_options": [],
            "user_intervention": {
                "decision": "approve",
                "option_id": None,
                "decided_by": _safe_id(actor),
                "decided_by_role": "local",
                "decided_at": generated_at,
                "reason": reason_text,
                "recorded": True,
            },
            "execution_receipt": {
                # 外部系统没有回执：如实写 unreported；simulated_local 属于 inverse 回执
                "state": "unreported",
                "reported_by": None,
                "reported_at": None,
                "detail": ("本地逆向动作已记录（simulated_local）；外部系统未实际调用"
                           if language == "zh" else
                           "Local inverse recorded (simulated_local); external systems not called"),
                "sdk_version": None,
                "effect_notice": "intervention.effect.recorded_only",
            },
            "risk_level": "medium",
            "impact_scope": {
                "targets": [{"kind": "record", "ref": action_id,
                             "change": "update", "count": len(affected)}],
                "money": None,
                "external_endpoint": None,
                "data_categories": ["none"],
                "reversible": True,
                "summary": (f"撤销动作 {action_id} 及其 {max(0, len(affected) - 1)} 个后续步骤"
                            if language == "zh" else
                            f"Undo action {action_id} and {max(0, len(affected) - 1)} dependent step(s)"),
            },
            "policy": policy,
            "error": None,
            "suggested_next": [],
            "context": {
                "task_name": (target.get("context") or {}).get("task_name")
                if isinstance(target.get("context"), Mapping) else None,
                "audience_profile": "C",
                "default_granularity": "L1",
                "user_language": language,
            },
            "trace_link": target.get("trace_link") if isinstance(target.get("trace_link"), Mapping) else None,
            "source": {
                "kind": source_kind,
                "adapter": source.get("adapter") if demo and isinstance(source, Mapping) else "tracelens-rollback",
                "format_version": ROLLBACK_VERSION,
                "source_uri": source.get("source_uri") if demo and isinstance(source, Mapping) else None,
                "ingested_at": None,
                "raw_ref": None,
            },
            "demo_data": demo,
            "synthetic": synthetic,
            "labels": ["rollback", "simulated-local"],
            "rollback_ref": action_id,
        }
        return record


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.rollback``：临时目录跑一遍计划 + 执行 + 覆盖率。"""
    import json
    import tempfile

    repo = pathlib.Path(__file__).resolve().parents[1]
    sample = repo / "samples" / "action_records.json"
    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(temp)
        records: list[dict[str, Any]] = []
        if sample.exists():
            records = json.loads(sample.read_text(encoding="utf-8")).get("records", [])
        svc = RollbackService(paths)
        cov = svc.coverage(records)
        print(f"coverage: total={cov['total']} reversible={cov['reversible']} "
              f"ratio={cov['ratio']}")
        if records:
            target = "act-demo-0007"
            info = plan(records, target)
            print(f"plan({target}): ok={info['ok']} dependents={info['dependents']} "
                  f"order={info['order']} reversible={info['reversible']}")
            out = svc.execute(records, target, actor="local", reason="self-check")
            rb = out["rollback_record"]
            print(f"rollback: id={rb['id']} action_type={rb['action_type']} "
                  f"ref={rb['rollback_ref']} status={rb['status']} "
                  f"receipt={rb['execution_receipt']['state']} "
                  f"inverse={out['inverse']['receipt_state']}")
            print(f"journal entries: {len(svc.journal())}")
            replayed = RollbackService(paths)
            print(f"journal reload: {len(replayed.journal())}")
            try:
                svc.execute(records, target, actor="local")
            except RollbackError as exc:
                print(f"duplicate rollback rejected: {exc.code}")
            else:  # pragma: no cover
                print("ERROR: duplicate rollback was accepted")
                return 1
        print("rollback self-check ok")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
