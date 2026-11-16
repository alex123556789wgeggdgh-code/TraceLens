"""改进建议与用户偏好（规格书功能七，docs/07 §4.11 / §3.3 冻结契约）。

这一层回答一个问题：**同一个 (tool, param) 被同一个人反复手动改成同一个
方向，是不是该问一句"要不要把它变成默认偏好"？**

* ``DEFAULT_THRESHOLD = 3``：同一个 (tool, param) 被 modify 决定改到第 3 次，
  才生成一条 open 建议——不到阈值不打扰（观察 ≠ 猜测）。
* ``COOLDOWN_DAYS = 7``：dismiss 之后 7 天内不再为同一 (tool, param) 重新
  生成建议（拒绝也要留出安静期，不是把人按着头接受）。
* 接受（accept）= 生成一条 ``source="suggestion"`` 的 UserPreference；
  手动偏好（set_preference）= ``source="manual"``。两种来源在账本里分开，
  审计能看到"这条默认值是机器建议、人接受的"。

为什么建议不自动生效：决定 ≠ 生效在这里同样成立——accept 只是写偏好，
执行端读不读、什么时候读，是执行端的事（docs/07 §功能七）。

错误码是契约：``SUGGESTION_FIELD_INVALID`` / ``SUGGESTION_NOT_FOUND`` /
``SUGGESTION_STATE_INVALID`` / ``SUGGESTION_WRITE_FAILED`` / ``PREFERENCE_INVALID``。

R7-1（REQ-007 B7-1）接线口径（docs/07 §11.13，PM 裁决 R7-1 §2）：

* **取数只吃 S0-1 运行时干预事件**——``action_type == "intervention"`` 且
  ``source.adapter == "tracelens-intervention"`` 落款的**独立签名干预记录**
  （``intervention_history.py``）。``modified_params`` / ``modified_fields`` 从那条记录
  的决定段取。导入侧的 ``user_intervention`` 是历史导入面，**不在事件流口径里**：
  ``scan`` 只重放事件流，非落款记录计入 ``scan`` 的 ``non_event_records`` 并登记跳过原因。
* **「修改方向一致」可判定定义（逐字三条，落 docs/07 §11.13）**：数值型＝时间序单调
  （非严格）同向；枚举型＝收敛到同一值；时间型＝偏移符号一致。判定复用既有
  ``_direction``（本文件 ``def _direction``）语义，不新增第二套方向函数；
  单调性被破坏（方向反复 / 枚举换值 / 跨类型参数）→ **这段同向序列重新开始计数**，
  跨类型参数不聚合。
* **不静默**：没计数的事件必须留下原因码（``facts()["observe_skips"]``）——
  缺 target、缺参数、方向破坏、重复事件、冷却期内、已接受，全都在账上。
"""

from __future__ import annotations

import datetime as _dt
import sys
import threading
from collections.abc import Mapping

from .feature_store import FeaturePaths, FeatureStore, new_id, open_store, utc_stamp
# 干预事件落款的判定只有一处真相（不二次实现，REQ-006 预聚合复用同一形状）
from .intervention_history import INTERVENTION_ADAPTER, is_intervention_record

__all__ = [
    "SUGGESTIONS_VERSION", "DEFAULT_THRESHOLD", "COOLDOWN_DAYS", "DIRECTIONS",
    "SUGGESTION_STATUSES", "PREFERENCE_SOURCES", "SuggestionError",
    "SuggestionService", "default_service",
    # R7-1 取数口径（公开形状，供事件流消费方复用）
    "INTERVENTION_EVENT_ADAPTER", "EVENT_SEEN_LIMIT", "OBSERVE_SKIP_REASONS",
    "intervention_event_view",
]

SUGGESTIONS_VERSION = "1.0"
DEFAULT_THRESHOLD = 3
COOLDOWN_DAYS = 7
#: 方向：数值上升/下降；不可比的值如实记 set（不猜语义）
DIRECTIONS: tuple[str, ...] = ("increase", "decrease", "set")
SUGGESTION_STATUSES: tuple[str, ...] = ("open", "accepted", "dismissed")
PREFERENCE_SOURCES: tuple[str, ...] = ("manual", "suggestion")
_USER_SCOPE_NOTE = "分析类输出里的 user_id 由 HTTP 层负责脱敏，本模块只存原值"

# --------------------------------------------------------------------------- #
# R7-1 取数口径：S0-1 运行时干预事件 → 可计数事件视图
# --------------------------------------------------------------------------- #

#: 事件流落款（判定复用 ``intervention_history.is_intervention_record``；这里只是对外说明常量）
INTERVENTION_EVENT_ADAPTER = INTERVENTION_ADAPTER
#: 一条建议账目里留存的「已计数事件 id」上限：幂等去重窗口（只多存 id，不改计数语义）
EVENT_SEEN_LIMIT = 50
#: 事件未计数的原因码（登记进 ``facts()["observe_skips"]``；静默漏算 = 缺陷，任务 §2.3）
OBSERVE_SKIP_REASONS: tuple[str, ...] = (
    "not_object",             # 传进来的不是对象
    "not_intervention_event",  # 不是本机 S0-1 落款的干预事件（导入侧记录走这里）
    "not_modify",             # approve / reject：没有可学习的参数方向
    "missing_target",         # 缺 parent_action_id：语义上站不住的"干预"
    "missing_actor",          # 缺决定人：按既有 ``local`` 缺省口径计数并登记
    "no_changed_params",      # modified_fields 与 modified_params 都给不出"改过什么"
    "no_value_pair",          # 缺 before 或 after：方向无从判定（不猜）
    "duplicate_event",        # 同一事件第二次到达（scan 重放 / 重复投递）
    "run_break",              # 单调性破坏：方向反复 / 枚举换值 / 跨类型
    "cooldown",               # dismiss 后的 7 天冷却期内
    "accepted",               # 已采纳：不再为同一 (tool, param) 重复建议
)


class SuggestionError(Exception):
    """建议/偏好域异常：``code`` 对齐 docs/07 §7，HTTP 层照表翻译。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: dict | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.field = field
        self.hint = hint
        self.detail = dict(detail or {})

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.detail:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"field={self.field}")
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return "；".join(parts)


def _now() -> str:
    return utc_stamp()


def _parse(stamp: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).isoformat(timespec="seconds") \
        .replace("+00:00", "Z")


def _direction(before, after) -> str:
    try:
        return "increase" if float(after) > float(before) else "decrease"
    except (TypeError, ValueError):
        return "set"


def _as_float(value):
    """能当数字判就当数字判（bool 不算：True 冒充 1 会让"收敛"判成假相等）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _same_value(left, right) -> bool:
    """枚举/不可比值的"是不是同一个值"（枚举＝收敛到同一值）。

    数字按数值比（``3`` 与 ``"3.0"`` 是同一个值），其余按字面比。布尔只与布尔比。
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    a, b = _as_float(left), _as_float(right)
    if a is not None and b is not None:
        return a == b
    return str(left) == str(right)


def _run_continues(previous_direction, previous_to, direction, after) -> bool:
    """「修改方向一致」的可判定口径（docs/07 §11.13 三条定义的代码落点）。

    * **数值型＝时间序单调（非严格）同向**：同向（``increase``/``decrease`` 一致）且
      新值相对上一次的目标值不反向（上升段允许持平，下降段允许持平）；
    * **枚举型＝收敛到同一值**：两侧都判不成数字（``_direction`` 记 ``set``）时，
      只有"又改成同一个值"才算同向；换成另一个值即破坏；
    * **时间型＝偏移符号一致**：``_direction`` 判不出 ISO 时间串的偏移（非数值 → ``set``），
      所以这一条在代码侧按 ``set`` 的收敛口径落，**不假装能判时间偏移**（诚实边界，
      三条定义的字面口径见 docs/07 §11.13）。

    跨类型参数天然不聚合：一侧是数字、一侧不是 → ``direction`` 不同 → 破坏同向段。
    """
    if direction != str(previous_direction or ""):
        return False
    if direction == "set":
        return _same_value(previous_to, after)
    old, new = _as_float(previous_to), _as_float(after)
    if old is None or new is None:
        return _same_value(previous_to, after)
    if direction == "increase":
        return new >= old
    return new <= old


def _tool_of(record) -> str:
    """事件所属工具名：干预记录的 ``tool_used.name`` 继承自被干预记录（S0-1 构造口径）。"""
    tool = record.get("tool_used") if isinstance(record, Mapping) else None
    if isinstance(tool, Mapping) and tool.get("name"):
        return str(tool.get("name"))
    if isinstance(tool, str) and tool:
        return tool
    return "—"


def _pairs_from_fields(view: Mapping) -> list[dict]:
    """首选取数：``user_intervention.modified_fields``（apply_decision 只写"真改过"的字段）。"""
    out: list[dict] = []
    for field in (view.get("modified_fields") or []):
        if not isinstance(field, Mapping) or not str(field.get("name") or ""):
            continue
        out.append({"param": str(field["name"]),
                    "before": field.get("before"), "after": field.get("after")})
    return out


def _pairs_from_params(view: Mapping) -> list[dict]:
    """兜底取数：只有 schema C3 的两个对象（``modified_params`` / ``original_params``）时，
    用两侧差集还原"改过什么"。传了但没改的键不算修改；``original_params`` 缺该键时
    ``before`` 为 ``None``（不编造原值，由调用方按 ``no_value_pair`` 登记）。
    """
    modified = view.get("modified_params")
    if not isinstance(modified, Mapping) or not modified:
        return []
    original = view.get("original_params")
    original = original if isinstance(original, Mapping) else {}
    out: list[dict] = []
    for name, value in modified.items():
        key = str(name or "")
        if not key:
            continue
        before = original.get(name) if name in original else None
        if name in original and _same_value(before, value):
            continue
        out.append({"param": key, "before": before, "after": value})
    return out


def intervention_event_view(record) -> dict:
    """把一条记录收敛成「可计数事件视图」——R7-1 取数口径的**唯一出口**。

    返回 ``{"tool", "actor", "target", "event_id", "events": [{"param", "before",
    "after", "direction"}], "skips": [原因码…]}``。``skips`` 就是"这条为什么没计数"的登记，
    调用方负责记账（``facts()["observe_skips"]``）。REQ-006 的预聚合直接消费这同一个形状，
    不另起第二套取数（任务 §2.5）。
    """
    view: dict = {"tool": _tool_of(record), "actor": None, "target": None,
                  "event_id": None, "events": [], "skips": []}
    if not isinstance(record, Mapping):
        view["skips"].append("not_object")
        return view
    view["event_id"] = str(record.get("id")) if record.get("id") else None
    view["target"] = (str(record.get("parent_action_id"))
                      if record.get("parent_action_id") else None)
    ui = record.get("user_intervention")
    ui = ui if isinstance(ui, Mapping) else {}
    actor = ui.get("decided_by") or record.get("actor_id")
    view["actor"] = str(actor) if actor not in (None, "") else None
    if str(ui.get("decision") or "") != "modify":
        view["skips"].append("not_modify")
        return view
    if not view["target"]:
        # 没有目标记录的"干预"在语义上站不住：登记 + 跳过，不静默计入
        view["skips"].append("missing_target")
        return view
    if not view["actor"]:
        view["skips"].append("missing_actor")     # 仍按既有 "local" 缺省口径计数，不丢数据
    pairs = _pairs_from_fields(ui) or _pairs_from_params(ui)
    if not pairs:
        view["skips"].append("no_changed_params")
        return view
    for pair in pairs:
        if pair.get("before") is None or pair.get("after") is None:
            view["skips"].append("no_value_pair")
            continue
        pair["direction"] = _direction(pair["before"], pair["after"])
        view["events"].append(pair)
    return view


class SuggestionService:
    """建议 + 偏好两个账本的唯一写入者（``suggestions.jsonl`` / ``preferences.jsonl``）。

    线程安全：HTTP 多线程下持 ``RLock``；两个账本都是 FeatureStore（同 id 覆盖），
    同键建议只有一条。幂等口径（R7-1）：计数以**事件 id** 为锚——带 id 的事件第二次
    到达只登记 ``duplicate_event``、``occurrences`` 不涨；没有 id 的旧形状记录无法去重
    （如实：不去重，不假装幂等）。
    """

    def __init__(self, paths: FeaturePaths, *, now=None, enabled: bool = True) -> None:
        if not isinstance(paths, FeaturePaths):
            raise TypeError("SuggestionService 需要 FeaturePaths（见 feature_store.py）")
        self.paths = paths
        self._now = now or _now
        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        self._suggestions: FeatureStore = open_store(paths.suggestions)
        self._preferences: FeatureStore = open_store(paths.preferences)
        # R7-1 接线台账：消费了多少事件、为什么没计数（静默漏算 = 缺陷）
        self._events_observed = 0
        self._skips: dict[str, int] = {}

    # -- 开关 -------------------------------------------------------------- #
    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # -- 接线台账（R7-1：没计数必须留下原因，不静默） -------------------------- #
    def _register_skip(self, reason: str) -> None:
        with self._lock:
            key = str(reason)
            self._skips[key] = self._skips.get(key, 0) + 1

    def observe_skips(self) -> dict:
        """未计数事件的原因码计数（副本；与 ``facts()["observe_skips"]`` 同一张表）。"""
        with self._lock:
            return dict(self._skips)

    # -- 观察（T28 的生成侧 / R7-1 接线入口） -------------------------------- #
    def observe(self, record: dict, *, user_id) -> dict | None:
        """消费一条**已决定**记录；到达阈值就返回 open 建议（否则 None）。

        R7-1：命中 S0-1 落款（``action_type="intervention"`` + ``tracelens-intervention``）
        的记录直接交给 ``observe_intervention``——运行时干预事件流是 B7-1 的唯一取数面。
        其余记录保持既有"已决定记录"口径（docs/07 §4.11 的 ``observe`` 契约逐字不变）：
        只有 ``decision=modify`` 且带 ``modified_fields`` 才计数——approve/reject
        没有可学习的参数方向。``enabled=False`` 时全链路静默（T28 的 enabled 字段
        就是这个开关的对外视图）。
        """
        if not self._enabled:
            return None
        if is_intervention_record(record):
            return self.observe_intervention(record, user_id=user_id)
        view = record.get("user_intervention") if isinstance(record, dict) else None
        if not isinstance(view, dict) or view.get("decision") != "modify":
            return None
        tool = _tool_of(record)
        user = str(user_id if user_id not in (None, "") else "local")
        event_id = str(record.get("id")) if record.get("id") else None
        created: dict | None = None
        for field in (view.get("modified_fields") or []):
            if not isinstance(field, Mapping) or not str(field.get("name") or ""):
                continue
            param = str(field["name"])
            before, after = field.get("before"), field.get("after")
            if before is None or after is None:
                self._register_skip("no_value_pair")
                continue
            created = self._count(tool, param, before, after, user,
                                  event_id=event_id) or created
        return created

    def observe_intervention(self, record, *, user_id=None) -> dict | None:
        """B7-1 接线入口：消费**一条 S0-1 运行时干预事件**（独立签名的那条干预记录）。

        ``user_id`` 传谁就用谁（服务端传 T28 读侧的同一身份口径，见 docs/07 §11.13）；
        不传时落回事件自己的 ``decided_by``，再落回既有 ``"local"`` 缺省口径。
        非本机落款的记录不计数，只登记 ``not_intervention_event``。
        """
        if not self._enabled:
            return None
        if not is_intervention_record(record):
            self._register_skip("not_intervention_event")
            return None
        view = intervention_event_view(record)
        for reason in view["skips"]:
            self._register_skip(reason)
        events = view["events"]
        if not events:
            return None
        user = str(user_id if user_id not in (None, "") else (view["actor"] or "local"))
        with self._lock:
            self._events_observed += 1
        created: dict | None = None
        for event in events:
            created = self._count(view["tool"], event["param"], event["before"],
                                  event["after"], user, event_id=view["event_id"]) or created
        return created

    def scan(self, records, *, user_id) -> dict:
        """对既有 **S0-1 干预事件**做幂等重算（事件账本重放 / 离线补算）。

        幂等由事件 id 去重保证（``EVENT_SEEN_LIMIT`` 窗口）：同一批记录扫第二遍，
        ``occurrences`` 一个字都不涨，``suggestions_touched`` 归 0。
        导入侧的 ``user_intervention`` 不在事件流口径里（PM 裁决 R7-1 §2.1）：计入
        ``non_event_records`` 并登记 ``not_intervention_event``，不计数。
        """
        touched = 0
        non_event = 0
        scanned = 0
        for record in (records or []):
            if not isinstance(record, Mapping):
                continue
            scanned += 1
            if not is_intervention_record(record):
                non_event += 1
                self._register_skip("not_intervention_event")
                continue
            if self.observe_intervention(record, user_id=user_id) is not None:
                touched += 1
        return {"scanned": scanned,
                "suggestions_touched": touched,
                "non_event_records": non_event,
                "threshold": DEFAULT_THRESHOLD, "cooldown_days": COOLDOWN_DAYS}

    def _count(self, tool: str, param: str, before, after, user: str, *,
               event_id: str | None = None) -> dict | None:
        """同 ``(user, tool, param)`` 的同向计数；到阈值才产出建议。

        * **方向一致才累加**：``_run_continues`` 判不动（方向反复 / 枚举换值 / 跨类型）
          就把这段同向序列**从 1 重新开始**（任务 §2.3「单调性破坏即重置」），
          所以"改回去"永远不会被算成"改过来"的第四次；
        * **幂等锚点**：``event_id`` 已在本账目里出现过（scan 重放 / 重复投递）→ 只登记
          ``duplicate_event``，``occurrences`` 不涨；账本同键只有一条建议，不产生第二条。
        """
        with self._lock:
            key = (user, tool, param)
            existing = None
            for item in self._suggestions.items():
                if (item.get("user_id"), item.get("tool"), item.get("param")) == key:
                    existing = dict(item)
                    break
            direction = _direction(before, after)
            if existing and existing.get("status") == "dismissed":
                cooldown = _parse(str(existing.get("cooldown_until") or ""))
                if cooldown and _parse(self._now()) and _parse(self._now()) < cooldown:
                    self._register_skip("cooldown")
                    return None
            if existing and existing.get("status") == "accepted":
                self._register_skip("accepted")
                return None
            seen = [str(e) for e in (existing.get("events") or [])] if existing else []
            if event_id and event_id in seen:
                self._register_skip("duplicate_event")
                return None
            if existing is None:
                entry = {"id": new_id("sug"), "user_id": user, "tool": tool,
                         "param": param, "direction": direction,
                         "from_value": before, "to_value": after,
                         "occurrences": 1, "status": "open",
                         "created_at": self._now(), "decided_at": None,
                         "cooldown_until": None,
                         "events": [event_id] if event_id else []}
            else:
                entry = existing
                if _run_continues(existing.get("direction"), existing.get("to_value"),
                                  direction, after):
                    entry["occurrences"] = int(existing.get("occurrences") or 0) + 1
                else:
                    self._register_skip("run_break")
                    entry["occurrences"] = 1
                    entry["run_broken_at"] = self._now()
                entry["direction"] = direction
                entry["from_value"] = before
                entry["to_value"] = after
                if event_id:
                    seen.append(event_id)
                entry["events"] = seen[-EVENT_SEEN_LIMIT:] if seen else []
            if int(entry["occurrences"]) < DEFAULT_THRESHOLD:
                self._suggestions.put(entry)
                return None
            if entry["status"] != "open":
                entry["status"] = "open"
            self._suggestions.put(entry)
            return dict(entry)

    # -- 查询（T28） ------------------------------------------------------- #
    def list(self, *, user_id, status: str | None = None) -> list[dict]:
        if status is not None and status not in SUGGESTION_STATUSES:
            raise SuggestionError("SUGGESTION_FIELD_INVALID",
                                  f"status 只支持 {'/'.join(SUGGESTION_STATUSES)}",
                                  field="status", detail={"received": status})
        user = str(user_id if user_id not in (None, "") else "local")
        with self._lock:
            rows = [dict(item) for item in self._suggestions.items()
                    if item.get("user_id") == user
                    and (status is None or item.get("status") == status)]
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows

    def get(self, suggestion_id: str) -> dict | None:
        with self._lock:
            found = self._suggestions.get(suggestion_id)
        return dict(found) if isinstance(found, dict) else None

    # -- 决定（T29 / T30） ------------------------------------------------- #
    def accept(self, suggestion_id: str, *, user_id) -> dict:
        """接受建议：生成 ``source="suggestion"`` 的偏好；建议状态置 accepted。"""
        with self._lock:
            entry = self._suggestions.get(suggestion_id)
            if not isinstance(entry, dict):
                raise SuggestionError("SUGGESTION_NOT_FOUND",
                                      f"建议 {suggestion_id} 不存在",
                                      detail={"suggestion_id": suggestion_id})
            if entry.get("status") != "open":
                raise SuggestionError("SUGGESTION_STATE_INVALID",
                                      f"建议状态是 {entry.get('status')}，只有 open 可接受",
                                      field="status",
                                      detail={"status": entry.get("status")})
            user = str(user_id if user_id not in (None, "") else "local")
            preference = self._upsert_preference(
                user_id=user, tool=str(entry.get("tool") or "—"),
                param=str(entry.get("param") or "—"),
                value=entry.get("to_value"), source="suggestion")
            entry["status"] = "accepted"
            entry["decided_at"] = self._now()
            entry["preference_id"] = preference["id"]
            self._suggestions.put(entry)
            return {"suggestion": dict(entry), "preference": dict(preference)}

    def dismiss(self, suggestion_id: str, *, user_id, reason: str = "") -> dict:
        """拒绝建议：进入 7 天冷却，期间不为同一 (tool, param) 重新打扰。"""
        with self._lock:
            entry = self._suggestions.get(suggestion_id)
            if not isinstance(entry, dict):
                raise SuggestionError("SUGGESTION_NOT_FOUND",
                                      f"建议 {suggestion_id} 不存在",
                                      detail={"suggestion_id": suggestion_id})
            if entry.get("status") != "open":
                raise SuggestionError("SUGGESTION_STATE_INVALID",
                                      f"建议状态是 {entry.get('status')}，只有 open 可拒绝",
                                      field="status",
                                      detail={"status": entry.get("status")})
            now = _parse(self._now())
            entry["status"] = "dismissed"
            entry["decided_at"] = self._now()
            entry["cooldown_until"] = _iso(now + _dt.timedelta(days=COOLDOWN_DAYS)) if now else None
            if str(reason or "").strip():
                entry["dismiss_reason"] = str(reason).strip()[:200]
            self._suggestions.put(entry)
            return dict(entry)

    # -- 偏好（T31） ------------------------------------------------------- #
    def _upsert_preference(self, *, user_id: str, tool: str, param: str,
                           value, source: str) -> dict:
        if source not in PREFERENCE_SOURCES:
            raise SuggestionError("PREFERENCE_INVALID",
                                  f"source 只支持 {'/'.join(PREFERENCE_SOURCES)}",
                                  field="source", detail={"received": source})
        if not str(tool).strip() or not str(param).strip():
            raise SuggestionError("PREFERENCE_INVALID", "tool 与 param 不能为空",
                                  field="tool/param")
        if value is None or isinstance(value, (dict, list)):
            raise SuggestionError("PREFERENCE_INVALID",
                                  "value 必须是 string/number/boolean，不能是空或复合结构",
                                  field="value", detail={"received": repr(value)})
        existing = self._preference_entry(user_id, tool, param)
        if existing is None:
            entry = {"id": new_id("pref"), "user_id": user_id, "tool": tool,
                     "param": param, "value": value, "source": source,
                     "created_at": self._now(), "updated_at": self._now()}
        else:
            entry = existing
            entry["value"] = value
            entry["source"] = source
            entry["updated_at"] = self._now()
        self._preferences.put(entry)
        return dict(entry)

    def _preference_entry(self, user_id: str, tool: str, param: str) -> dict | None:
        for item in self._preferences.items():
            if (item.get("user_id"), item.get("tool"), item.get("param")) \
                    == (user_id, tool, param):
                return dict(item)
        return None

    def set_preference(self, *, user_id, tool: str, param: str, value,
                       source: str = "manual") -> dict:
        user = str(user_id if user_id not in (None, "") else "local")
        with self._lock:
            return self._upsert_preference(user_id=user, tool=str(tool),
                                           param=str(param), value=value,
                                           source=source)

    def preferences(self, *, user_id) -> list[dict]:
        user = str(user_id if user_id not in (None, "") else "local")
        with self._lock:
            rows = [dict(item) for item in self._preferences.items()
                    if item.get("user_id") == user]
        rows.sort(key=lambda item: (str(item.get("tool")), str(item.get("param"))))
        return rows

    def preference_for(self, *, user_id, tool: str, param: str) -> dict | None:
        user = str(user_id if user_id not in (None, "") else "local")
        with self._lock:
            found = self._preference_entry(user, str(tool), str(param))
        return dict(found) if found else None

    # -- 自检事实 ----------------------------------------------------------- #
    def facts(self) -> dict:
        with self._lock:
            return {"version": SUGGESTIONS_VERSION,
                    "suggestions": self._suggestions.count(),
                    "preferences": self._preferences.count(),
                    "threshold": DEFAULT_THRESHOLD,
                    "cooldown_days": COOLDOWN_DAYS,
                    "events_observed": self._events_observed,
                    "observe_skips": dict(self._skips),
                    "enabled": self._enabled}


def default_service(paths: FeaturePaths, *, now=None, enabled: bool = True) -> SuggestionService:
    return SuggestionService(paths, now=now, enabled=enabled)


def _intervention_event(idx: int, tool: str, param: str, before, after, *,
                        actor: str = "local-user") -> dict:
    """S0-1 落款的干预事件（形状 = ``build_intervention_record`` 产物的最小子集）。

    自检与测试都从这里拿事件，保证「取数面 = 运行时干预记录」这一条不走形。
    """
    return {
        "id": f"act_iv_{idx:04d}",
        "action_type": "intervention",
        "parent_action_id": f"act-{idx:04d}",
        "timestamp": "2026-11-11T10:00:00Z",
        "actor_id": actor,
        "agent_id": "agent-a",
        "tool_used": {"name": tool},
        "source": {"kind": "manual", "adapter": INTERVENTION_EVENT_ADAPTER,
                   "format_version": "1.0"},
        "user_intervention": {"decision": "modify", "decided_by": actor,
                              "decided_by_role": "local",
                              "decided_at": "2026-11-11T10:30:00Z",
                              "original_params": {param: before},
                              "modified_params": {param: after},
                              "modified_fields": [{"name": param, "before": before,
                                                   "after": after}]},
    }


def _decided(idx: int, tool: str, param: str, before, after) -> dict:
    """既有「已决定记录」形状（docs/07 §4.11 的 ``observe`` 契约侧，不是事件流）。"""
    return {"id": f"act-{idx:04d}", "agent_id": "agent-a", "action_type": "write",
            "status": "modified", "timestamp": "2026-11-11T10:00:00Z",
            "tool_used": {"name": tool},
            "user_intervention": {"decision": "modify", "decided_by": "local-user",
                                  "decided_at": "2026-11-11T10:30:00Z",
                                  "modified_fields": [
                                      {"name": param, "before": before,
                                       "after": after}]}}


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.suggestions``：阈值→接受/拒绝→冷却→方向破坏→幂等全链路。"""

    with tempfile_dir() as temp:
        paths = FeaturePaths(temp)
        paths.ensure()
        service = SuggestionService(paths)
        for idx in range(1, DEFAULT_THRESHOLD):
            assert service.observe_intervention(
                _intervention_event(idx, "fs.write", "max_move_count", 5, 10),
                user_id="local-user") is None
        suggestion = service.observe_intervention(
            _intervention_event(DEFAULT_THRESHOLD, "fs.write", "max_move_count", 5, 10),
            user_id="local-user")
        assert suggestion and suggestion["status"] == "open" \
            and suggestion["occurrences"] == DEFAULT_THRESHOLD, suggestion
        assert suggestion["direction"] == "increase"
        accepted = service.accept(suggestion["id"], user_id="local-user")
        assert accepted["preference"]["source"] == "suggestion"
        assert accepted["preference"]["value"] == 10
        assert service.preference_for(user_id="local-user", tool="fs.write",
                                      param="max_move_count") is not None
        # 手动偏好覆盖（同键 upsert，source 变 manual）
        manual = service.set_preference(user_id="local-user", tool="fs.write",
                                        param="max_move_count", value=20)
        assert manual["source"] == "manual" and manual["value"] == 20
        # 状态机：accepted 不能再 dismiss（决定过就是决定过）
        try:
            service.dismiss(suggestion["id"], user_id="local-user")
            raise SystemExit("accepted 被允许 dismiss = 状态机漏了")
        except SuggestionError as exc:
            assert exc.code == "SUGGESTION_STATE_INVALID"
        # 冷却：另一条建议被拒绝后，同键第 4 次 observe 不再生成
        for idx in range(20, 20 + DEFAULT_THRESHOLD):
            second = service.observe_intervention(
                _intervention_event(idx, "mail.send", "delay", 0, 60), user_id="local-user")
        assert second and second["status"] == "open"
        dismissed = service.dismiss(second["id"], user_id="local-user",
                                    reason="先观察")
        assert dismissed["status"] == "dismissed" and dismissed["cooldown_until"]
        assert service.observe_intervention(
            _intervention_event(9, "mail.send", "delay", 7, 14), user_id="local-user") is None
        # 方向反复：单调性破坏 → 同向段从 1 重新开始，第 3 次不产建议
        for idx in range(30, 33):
            before, after = (0, 60) if idx < 32 else (60, 30)
            broke = service.observe_intervention(
                _intervention_event(idx, "cal.read", "window", before, after),
                user_id="local-user")
        assert broke is None, "方向反复不该产出建议"
        rows = [r for r in service.list(user_id="local-user") if r["tool"] == "cal.read"]
        assert rows and rows[0]["occurrences"] == 1, rows
        assert service.observe_skips().get("run_break"), service.observe_skips()
        # scan 幂等：同一批事件重放第二遍，occurrences 不涨、touched 归 0
        batch = [_intervention_event(i, "mail.read", "delay_ms", 100, 50)
                 for i in range(40, 43)]
        first = service.scan(batch, user_id="local-user")
        assert first["suggestions_touched"] == 1 and first["scanned"] == 3, first
        again = service.scan(batch, user_id="local-user")
        assert again["suggestions_touched"] == 0, again
        rows = [r for r in service.list(user_id="local-user") if r["tool"] == "mail.read"]
        assert rows and rows[0]["occurrences"] == DEFAULT_THRESHOLD, rows
        # 导入侧 user_intervention 不在事件流口径（scan 只吃 S0-1 落款）；
        # observe 的「已决定记录」契约仍照 §4.11 计数
        legacy = service.scan([_decided(90, "fs.delete", "keep_days", 3, 1)],
                              user_id="local-user")
        assert legacy["suggestions_touched"] == 0 and legacy["non_event_records"] == 1, legacy
        hit = None
        for idx in range(91, 94):
            hit = service.observe(_decided(idx, "fs.delete", "keep_days", 3, 1),
                                  user_id="local-user")
        assert hit and hit["occurrences"] == DEFAULT_THRESHOLD, hit
        facts = service.facts()
        print(f"suggestions self-check ok: suggestions={facts['suggestions']} "
              f"preferences={facts['preferences']} events_observed={facts['events_observed']} "
              f"skips={facts['observe_skips']} "
              f"cooldown={COOLDOWN_DAYS}d threshold={DEFAULT_THRESHOLD}")
    return 0


def tempfile_dir():
    import tempfile
    return tempfile.TemporaryDirectory()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_self_check())
