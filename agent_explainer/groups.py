"""多 Agent 任务聚合视图（规格书功能四）：分组、按时间合并、进度、资源、"卡住"判定。

为什么分组只认声明，不做任何"看起来像同一件事"的启发式
------------------------------------------------------
规格书要求"分组准确率 ≥ 99%"。任何靠相似度 / 时间窗 / 工具名去猜归属的算法都会在
边界上猜错；猜错一次，就是把 A 的责任算到 B 头上——对一个解释型产品来说，这比
"没分组"更糟（责任归属是这个产品的立身之本）。所以本模块只有一条规则：

    ``record["group_id"]`` 是分组归属的**唯一事实源**。

因此 :meth:`GroupService.grouping_report` 的 ``accuracy`` 是
``matched / declared``，**按构造恒等于 1.0**（声明了就一定归得进去），而且它同时
如实报告有多少条记录**没有** ``group_id``、被留在了组外。本模块**不做**任何猜测，
也不把"未分组"偷偷算进分母来美化数字。

为什么聚合视图不是"新的记录库"
------------------------------
记录的唯一事实源是 ``store.py`` 的 ``RecordJournal``；本模块只在**读**的时候把某个
``group_id`` 下的记录合并成一张视图（排序 / 统计 / 资源求和），不复制、不改写、不
重新签名字段。``aggregate()`` 接收调用方给的记录列表，返回**新的**列表与统计对象；
入参字典一个字节都不改（只做顶层浅拷贝，嵌套结构按引用共享——本函数保证"不修改"，
不保证"深隔离"）。

"卡住"为什么要区分"没数据"和"没动静"
-----------------------------------
一个 Agent 只有 ``running`` / ``observed`` 两种步骤、且最后一步距今超过 10 分钟，
才叫"卡住"（``STUCK_AFTER_SECONDS``）：它开始过、但长时间没有任何事件。
反过来，**一条记录都没有的 Agent 不算卡住**——没数据不等于停下了，把"没上报"
说成"卡死"是编造事实。同理，没有可解析时间戳的记录不参与卡住判定，因为算不出
"多久没动"。

进度分区口径与 ``web/app.js`` 的 ``zoneOf()`` **逐字一致**（needs_me / running /
done），这样后端聚合出的数字和前端卡片三区的数字不会互相打脸。颜色索引用
``sha256(agent_id)`` 取模调色板长度：Python 内建 ``hash()`` 每个进程随机加盐，
跨进程不稳定，会让同一个 Agent 换一次重启就换个颜色。

写作用域：本模块只写 ``FeaturePaths.groups``（``groups.jsonl``，一个 ``FeatureStore``）。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import threading
from typing import Any, Iterable, Mapping, Sequence

from .feature_store import FeaturePaths, new_id, open_store, sha256_hex, utc_stamp

__all__ = [
    "GROUPS_VERSION",
    "MAX_AGENTS",
    "STUCK_AFTER_SECONDS",
    "COLOR_PALETTE",
    "PALETTE_SIZE",
    "GROUP_STATUSES",
    "OPEN_STATUS",
    "CLOSE_STATUSES",
    "STATUS_RUNNING",
    "STATUS_DECIDABLE",
    "GroupError",
    "color_index_for",
    "color_for",
    "zone_of",
    "GroupService",
    "default_group_service",
]

#: 聚合视图口径版本（返回体结构变了就升这个号，存储格式无关）
GROUPS_VERSION = "1.0"
#: 规格书功能四："最多 10 Agent 并发"（超过就报 GROUP_AGENTS_EXCEEDED，不截断）
MAX_AGENTS = 10
#: "卡住"门槛：只有 running/observed 且最后事件距今超过这么久（秒）
STUCK_AFTER_SECONDS = 600
#: Agent 颜色索引的调色板（顺序稳定；index = sha256(id) % 长度）。
#: 具体色值只是"给前端一个不看也行的默认值"，真正的主题色由 ``web/`` 的 CSS 变量决定；
#: 契约保证的是 **index 稳定**，不是这几个十六进制好看。
COLOR_PALETTE = (
    "#2563eb", "#0d9488", "#d97706", "#7c3aed",
    "#dc2626", "#0891b2", "#4d7c0f", "#be185d",
)
PALETTE_SIZE = len(COLOR_PALETTE)

#: 任务组状态：open 只有一个；关闭用终态（追加式语义：不删组，只改状态）
OPEN_STATUS = "open"
CLOSE_STATUSES = ("completed", "cancelled", "failed")
GROUP_STATUSES = (OPEN_STATUS,) + CLOSE_STATUSES

#: 与 web/app.js 的 RUNNING_STATUS 对齐：这两类状态算"在途"
STATUS_RUNNING = ("running", "observed")
#: 需要人拍板的状态（配合 requires_action=true 才进 needs_me 区）
STATUS_DECIDABLE = ("pending_confirmation", "expired")

_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #

class GroupError(Exception):
    """任务组错误：机器码 + 人话原因 + 字段 + 取证细节（与既有模块同形）。

    错误码只取 ``docs/07`` §7 的 groups 域：
    ``GROUP_NOT_FOUND`` / ``GROUP_FIELD_INVALID`` / ``GROUP_CLOSED`` /
    ``GROUP_AGENTS_EXCEEDED`` / ``GROUP_WRITE_FAILED``。
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
        payload: dict[str, Any] = {"code": self.code, "reason": self.reason}
        if self.field:
            payload["field"] = self.field
        if self.detail:
            payload["detail"] = dict(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


# --------------------------------------------------------------------------- #
# 小工具：文本 / 时间 / 颜色 / 分区
# --------------------------------------------------------------------------- #

def _text(value: Any) -> str:
    """取一个去空白的字符串；非字符串一律当空（不 str() 危险对象）。"""
    return value.strip() if isinstance(value, str) else ""


def _as_datetime(value: Any) -> _dt.datetime | None:
    """把时间戳解析成带时区的 UTC datetime；解析不了返回 ``None``（绝不猜）。

    支持 ISO-8601（带 ``Z`` / 带偏移 / 无时区按 UTC）、``datetime`` 与 Unix 秒。
    无时区的按 UTC 处理并在 docstring 里写明——猜本地时区会让窗口统计随时区漂移。
    """
    moment: _dt.datetime
    if isinstance(value, _dt.datetime):
        moment = value
    elif isinstance(value, bool):
        return None
    elif isinstance(value, (int, float)):
        try:
            moment = _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            moment = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc)


def _stamp(moment: _dt.datetime | None = None) -> str:
    """ISO-8601 UTC 秒级时间戳（统一走 ``feature_store.utc_stamp``）。"""
    if moment is None:
        return utc_stamp()
    return utc_stamp(moment.timestamp())


def color_index_for(agent_id: Any) -> int:
    """Agent 的稳定颜色索引 = ``int(sha256(id), 16) % PALETTE_SIZE``。

    为什么不用内建 ``hash()``：CPython 对 str 的哈希每个进程加随机盐，重启一次
    同一个 Agent 就换颜色，用户会以为"换了个 Agent"。
    """
    text = str(agent_id if agent_id is not None else "")
    return int(sha256_hex(text), 16) % PALETTE_SIZE


def color_for(agent_id: Any) -> str:
    """按稳定索引取默认色值（前端若自带主题色，应以 ``color_index`` 为准）。"""
    return COLOR_PALETTE[color_index_for(agent_id)]


def zone_of(record: Mapping[str, Any] | None) -> str:
    """记录属于哪个区：``needs_me`` / ``running`` / ``done``。

    逐条对齐 ``web/app.js`` 的 ``zoneOf()``（含"requires_action 为真但状态不是
    succeeded/failed 也算 needs_me"这条），否则同一批记录前后端会数出两个答案。
    """
    if not isinstance(record, Mapping):
        return "done"
    status = record.get("status")
    if record.get("requires_action") is True and status not in ("succeeded", "failed"):
        return "needs_me"
    if status in STATUS_RUNNING:
        return "running"
    return "done"


def _percent(done: int, total: int) -> int:
    """整数百分比；用 +0.5 取整以对齐 JS ``Math.round``（Python round 是银行家舍入）。"""
    if total <= 0:
        return 0
    return int((done * 100.0 / total) + 0.5)


def _agent_id_of(item: Any) -> str:
    """从 id 字符串或 ``{"id": ...}`` / ``{"agent_id": ...}`` 对象里取 agent id。"""
    if isinstance(item, Mapping):
        raw = item.get("id")
        if not _text(raw):
            raw = item.get("agent_id")
    else:
        raw = item
    if not isinstance(raw, str):
        raise GroupError("GROUP_FIELD_INVALID",
                         "agent_ids 里只能放 agent id 字符串（或带 id 字段的对象）",
                         field="agent_ids")
    text = raw.strip()
    if not text:
        raise GroupError("GROUP_FIELD_INVALID", "agent_ids 里不允许空字符串", field="agent_ids")
    return text


def _agent_list(group: Mapping[str, Any]) -> list[str]:
    """取任务组声明的 agent id 列表（坏数据一律当空，不把字符串拆成字符）。"""
    raw = group.get("agent_ids")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw if _text(item)]


def _cost_amount(record: Mapping[str, Any]) -> float | None:
    """取记录里的成本金额；``cost`` 可能是 ``{"amount": 0.03}`` 或裸数字。"""
    raw = record.get("cost")
    amount = raw.get("amount") if isinstance(raw, Mapping) else raw
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    return float(amount)


def _latency_ms(record: Mapping[str, Any]) -> float | None:
    """取记录里的耗时（毫秒）；非数字/布尔一律当"未上报"。"""
    raw = record.get("latency_ms")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #

class GroupService:
    """TaskGroup 的读写 + 聚合视图（over ``groups.jsonl``）。

    用法::

        paths = FeaturePaths(tempdir)
        service = GroupService(paths)
        group = service.create(title="发布检查", agent_ids=["a", "b"])
        view = service.aggregate(group["id"], records)     # 只读，不改 records
        report = service.grouping_report(records)          # 分组准确率，如实报告未分组数

    ``now`` 可传 ``datetime`` / Unix 秒 / 无参回调（测试用来冻住时间），
    不传就是"现在"。时间一律按 UTC 比较，展示用的时间戳统一序列化成 ``...Z``。
    """

    def __init__(self, paths: FeaturePaths | pathlib.Path | str | None = None, *,
                 now: Any = None) -> None:
        if isinstance(paths, (str, pathlib.Path)):
            paths = FeaturePaths(paths)
        if paths is None:
            paths = FeaturePaths()
        self.paths: FeaturePaths = paths
        self._now = now
        self._lock = threading.RLock()
        self._store = open_store(self.paths.groups)
        self._store.load()

    # -- 时钟 -------------------------------------------------------------
    def _now_dt(self) -> _dt.datetime:
        raw = self._now() if callable(self._now) else self._now
        moment = _as_datetime(raw)
        return moment if moment is not None else _dt.datetime.now(_dt.timezone.utc)

    # -- 创建 / 读 / 关闭 -------------------------------------------------
    def create(self, *, title: str, agent_ids: Sequence[Any], objective: str = "",
               actor: str = "local", group_id: str | None = None) -> dict[str, Any]:
        """新建任务组；超过 :data:`MAX_AGENTS` 直接报错（**不截断、不静默丢 Agent**）。

        ``agent_ids`` 会按"首次出现"去重并保持调用方给的顺序：顺序就是界面上
        Agent 色块的顺序，若按字典序重排，用户会以为成员换了位置。
        """
        name = _text(title)
        if not name:
            raise GroupError("GROUP_FIELD_INVALID", "任务组标题不能为空", field="title")

        if isinstance(agent_ids, (str, bytes, Mapping)) or agent_ids is None:
            raise GroupError("GROUP_FIELD_INVALID", "agent_ids 必须是一个数组", field="agent_ids")
        try:
            raw_items = list(agent_ids)
        except TypeError as exc:
            raise GroupError("GROUP_FIELD_INVALID", "agent_ids 必须是一个数组",
                             field="agent_ids") from exc

        agents: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            agent = _agent_id_of(item)
            if agent in seen:
                continue
            seen.add(agent)
            agents.append(agent)
        if not agents:
            raise GroupError("GROUP_FIELD_INVALID", "任务组至少要有一个 Agent", field="agent_ids")
        if len(agents) > MAX_AGENTS:
            raise GroupError(
                "GROUP_AGENTS_EXCEEDED",
                f"一个任务组最多 {MAX_AGENTS} 个 Agent，收到了 {len(agents)} 个",
                field="agent_ids",
                detail={"requested": len(agents), "max": MAX_AGENTS},
                hint="拆成多个任务组：超过上限的聚合视图无法保证可读性",
            )

        if group_id is None:
            new_group_id = new_id("grp")
        else:
            new_group_id = _text(group_id)
            if not new_group_id:
                raise GroupError("GROUP_FIELD_INVALID", "group_id 不能为空", field="group_id")
            if self._store.get(new_group_id) is not None:
                raise GroupError("GROUP_FIELD_INVALID",
                                 f"任务组 {new_group_id} 已存在", field="group_id",
                                 detail={"group_id": new_group_id})

        record = {
            "id": new_group_id,
            "title": name,
            "objective": _text(objective),
            "created_at": _stamp(self._now_dt()),
            "status": OPEN_STATUS,
            "agent_ids": agents,
            "closed_at": None,
            "created_by": _text(actor) or "local",
        }
        with self._lock:
            self._store.put(record)
        return _copy(record)

    def get(self, group_id: str) -> dict[str, Any] | None:
        """按 id 取任务组；不存在返回 ``None``（路由层据此报 GROUP_NOT_FOUND）。"""
        key = _text(group_id)
        if not key:
            return None
        found = self._store.get(key)
        return _copy(found) if found is not None else None

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """列出任务组（按创建顺序）；``status`` 给了就只回该状态。"""
        if status is not None:
            wanted = _text(status)
            if wanted not in GROUP_STATUSES:
                raise GroupError("GROUP_FIELD_INVALID",
                                 f"status 只能是 {'/'.join(GROUP_STATUSES)}",
                                 field="status")
        else:
            wanted = ""
        items = self._store.items()
        return [_copy(item) for item in items
                if not wanted or item.get("status") == wanted]

    def close(self, group_id: str, *, status: str = "completed",
              actor: str = "local") -> dict[str, Any]:
        """关闭任务组（终态：completed / cancelled / failed）。

        已关闭的组再关一次报 ``GROUP_CLOSED``：这是状态冲突，不是"幂等成功"——
        悄悄成功会让界面以为"刚刚又关了一次"。
        """
        target = _text(status)
        if target not in CLOSE_STATUSES:
            raise GroupError("GROUP_FIELD_INVALID",
                             f"关闭状态只能是 {'/'.join(CLOSE_STATUSES)}", field="status")
        key = _text(group_id)
        current = self.get(key)
        if current is None:
            raise GroupError("GROUP_NOT_FOUND", f"没有 id 为 {key} 的任务组",
                             field="group_id", detail={"group_id": key})
        if current.get("status") != OPEN_STATUS:
            raise GroupError("GROUP_CLOSED",
                             f"任务组 {key} 已经是 {current.get('status')} 状态",
                             field="group_id",
                             detail={"group_id": key, "status": current.get("status")})
        updated = dict(current)
        updated["status"] = target
        updated["closed_at"] = _stamp(self._now_dt())
        updated["closed_by"] = _text(actor) or "local"
        with self._lock:
            self._store.put(updated)
        return _copy(updated)

    # -- 聚合视图 ---------------------------------------------------------
    def aggregate(self, group_id: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """把某个任务组的记录合并成一张视图（**只读**，入参一个字节都不改）。

        返回::

            {"group": {...},
             "records": [按 timestamp 升序、仅本组记录],
             "agents":  [{"id","name","count","color_index","stuck"}],
             "progress":{"total","done","running","needs_me","percent"},
             "resources":{"cost","latency_ms","cost_by_agent","latency_by_agent"},
             "window": {"started_at","ended_at","total_ms"},
             "stuck": [agent id],
             "generated_at": ISO UTC}

        细节裁决：

        * 归属只看 ``record["group_id"]``，与 :meth:`grouping_report` 同一口径，
          不做"agent 在成员表里就算本组"的补集推断；
        * 排序按解析后的**绝对时间**（不是字符串比较），无时区的按 UTC；
          时间戳缺失/坏掉的排在最后并保持原有相对顺序（稳定排序）；
        * ``agents`` 先列声明顺序的成员，再列"记录了但不在成员表里"的 Agent
          （按条数降序、id 升序）；``count`` 是记录条数；
        * ``resources.cost`` 只累加记录里真实给出的 ``cost.amount``，缺失按 0 计——
          "未上报成本"和"成本为 0"在界面上必须分开说，这里只给可加总的数字；
        * ``window.total_ms`` 由首尾两条记录的时间差算，单条记录为 0。
        """
        key = _text(group_id)
        group = self.get(key)
        if group is None:
            raise GroupError("GROUP_NOT_FOUND", f"没有 id 为 {key} 的任务组",
                             field="group_id", detail={"group_id": key})

        candidates: list[tuple[_dt.datetime | None, dict[str, Any]]] = []
        for record in records or ():
            if not isinstance(record, Mapping):
                continue
            if _text(record.get("group_id")) != key:
                continue
            # 顶层浅拷贝：不把调用方的字典交出去，也不改它
            candidates.append((_as_datetime(record.get("timestamp")), dict(record)))
        candidates.sort(key=lambda pair: (0, pair[0]) if pair[0] is not None
                        else (1, _EPOCH))
        ordered = [record for _, record in candidates]
        stamps = [stamp for stamp, _ in candidates]

        agent_order: list[str] = []
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        statuses: dict[str, set[str]] = {}
        last_seen: dict[str, _dt.datetime | None] = {}
        cost_by_agent: dict[str, float] = {}
        latency_by_agent: dict[str, float] = {}

        for agent in _agent_list(group):
            agent_text = str(agent)
            if agent_text and agent_text not in counts:
                agent_order.append(agent_text)
                counts[agent_text] = 0
                cost_by_agent[agent_text] = 0.0
                latency_by_agent[agent_text] = 0.0

        total_cost = 0.0
        total_latency = 0.0
        progress = {"total": 0, "done": 0, "running": 0, "needs_me": 0, "percent": 0}
        for position, record in enumerate(ordered):
            progress["total"] += 1
            progress[zone_of(record)] += 1
            agent = _text(record.get("agent_id"))
            if agent:
                if agent not in counts:
                    agent_order.append(agent)
                    counts[agent] = 0
                    cost_by_agent[agent] = 0.0
                    latency_by_agent[agent] = 0.0
                counts[agent] += 1
                statuses.setdefault(agent, set()).add(str(record.get("status") or ""))
                name = _text(record.get("agent_name"))
                if name and agent not in names:
                    names[agent] = name
                stamp = stamps[position]
                if stamp is not None:
                    previous = last_seen.get(agent)
                    if previous is None or stamp > previous:
                        last_seen[agent] = stamp
            amount = _cost_amount(record)
            if amount is not None:
                total_cost += amount
                if agent:
                    cost_by_agent[agent] = round(cost_by_agent.get(agent, 0.0) + amount, 6)
            latency = _latency_ms(record)
            if latency is not None:
                total_latency += latency
                if agent:
                    latency_by_agent[agent] = round(latency_by_agent.get(agent, 0.0) + latency, 3)

        progress["percent"] = _percent(progress["done"], progress["total"])

        now = self._now_dt()
        deadline = now - _dt.timedelta(seconds=STUCK_AFTER_SECONDS)
        stuck_ids: set[str] = set()
        for agent in agent_order:
            seen = statuses.get(agent) or set()
            if not seen or not seen.issubset(set(STATUS_RUNNING)):
                continue
            moment = last_seen.get(agent)
            if moment is not None and moment < deadline:
                stuck_ids.add(agent)

        agents: list[dict[str, Any]] = []
        for agent in agent_order:
            agents.append({
                "id": agent,
                "name": names.get(agent, agent),
                "count": counts.get(agent, 0),
                "color_index": color_index_for(agent),
                "stuck": agent in stuck_ids,
            })
        # 声明过的成员保持声明顺序；后补的成员按条数降序、id 升序，排到后面
        declared = set(_agent_list(group))
        head = [item for item in agents if item["id"] in declared]
        tail = sorted([item for item in agents if item["id"] not in declared],
                      key=lambda item: (-item["count"], item["id"]))
        agents = head + tail

        known = [stamp for stamp in stamps if stamp is not None]
        if known:
            started, ended = min(known), max(known)
            window = {
                "started_at": _stamp(started),
                "ended_at": _stamp(ended),
                "total_ms": round((ended - started).total_seconds() * 1000.0, 1),
            }
        else:
            window = {"started_at": None, "ended_at": None, "total_ms": 0}

        return {
            "group": group,
            "records": ordered,
            "agents": agents,
            "progress": progress,
            "resources": {
                "cost": round(total_cost, 6),
                "latency_ms": round(total_latency, 3),
                "cost_by_agent": cost_by_agent,
                "latency_by_agent": latency_by_agent,
            },
            "window": window,
            "stuck": [item["id"] for item in agents if item["stuck"]],
            "generated_at": _stamp(now),
        }

    def grouping_report(self, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """分组准确率报告：``{"declared","grouped","matched","accuracy",...}``。

        **口径要说清（这里不做的事比做了的事更重要）**：本模块不给任何记录"猜"一个
        group_id。所以：

        * ``declared`` = 声明了非空 ``group_id`` 的记录数；
        * ``grouped``  = 真的被归入某个组的记录数（按构造 == ``declared``）；
        * ``matched``  = 声明与归组一致的记录数（同理 == ``declared``）；
        * ``accuracy`` = ``matched / declared``，``declared == 0`` 时为 ``1.0``
          并在 ``accuracy_basis`` 标 ``"vacuous"``（空集上的准确率没有意义，但也不该
          报 0 假装失败）；
        * ``ungrouped`` = **没有** group_id、被留在组外的记录数——这个数字必须单独报，
          否则"准确率 100%"会被误读成"全都分好组了"。

        为了让报告可复核，``basis`` 固定为 ``"declared_only"``：这就是"准确率 100%"
        的真实含义——只统计声明过归属的记录。
        """
        declared = 0
        ungrouped = 0
        groups: set[str] = set()
        total = 0
        for record in records or ():
            if not isinstance(record, Mapping):
                continue
            total += 1
            group_id = _text(record.get("group_id"))
            if group_id:
                declared += 1
                groups.add(group_id)
            else:
                ungrouped += 1
        accuracy = round(declared / declared, 6) if declared else 1.0
        return {
            "declared": declared,
            "grouped": declared,
            "matched": declared,
            "accuracy": accuracy,
            "accuracy_basis": "declared_only" if declared else "vacuous",
            "ungrouped": ungrouped,
            "groups": len(groups),
            "total": total,
            "basis": "declared_only",
        }

    # -- 状态 / 自检 ------------------------------------------------------
    def facts(self) -> dict[str, Any]:
        """落盘状态（供 ``/health`` 如实披露）：文件、条数、写入次数、坏行问题。"""
        return {"version": GROUPS_VERSION, "max_agents": MAX_AGENTS,
                "stuck_after_seconds": STUCK_AFTER_SECONDS,
                "store": self._store.facts()}


def _copy(entity: Mapping[str, Any] | None) -> dict[str, Any]:
    """顶层浅拷贝：把内部对象交出去之前先断开引用，但不动嵌套结构。"""
    return dict(entity) if entity is not None else {}


def default_group_service(paths: FeaturePaths, *, now: Any = None) -> GroupService:
    """工厂：默认用给定数据目录的 ``groups.jsonl``（与 ``default_secretariat`` 同形）。"""
    return GroupService(paths, now=now)


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #

def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.groups``：临时目录里跑一遍建组 / 聚合 / 报告。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(pathlib.Path(temp))
        paths.ensure()
        service = GroupService(paths)
        group = service.create(title="发布检查", agent_ids=["a1", "a2", "a1"], objective="上线")
        assert group["agent_ids"] == ["a1", "a2"], group["agent_ids"]
        records = [
            {"id": "r1", "group_id": group["id"], "agent_id": "a1", "status": "succeeded",
             "timestamp": "2026-11-11T10:00:00+00:00", "latency_ms": 100,
             "cost": {"amount": 0.02}},
            {"id": "r2", "group_id": group["id"], "agent_id": "a2", "status": "running",
             "timestamp": "2026-11-11T18:00:05+08:00", "latency_ms": 400,
             "cost": {"amount": 0.01}},
            {"id": "r3", "group_id": group["id"], "agent_id": "a1",
             "status": "pending_confirmation", "requires_action": True,
             "timestamp": "2026-11-11T10:00:02+00:00"},
            {"id": "r4", "group_id": "other", "agent_id": "a9", "status": "succeeded",
             "timestamp": "2026-11-11T10:00:03+00:00"},
            # 自检必须有一条**真的没有 group_id** 的记录：`ungrouped` 计数才被验证到。
            # （旧夹具里四条都带 group_id，断言却要 ungrouped == 1 —— 自检自己先错了。）
            {"id": "r5", "agent_id": "a3", "status": "succeeded",
             "timestamp": "2026-11-11T10:00:04+00:00"},
        ]
        view = service.aggregate(group["id"], records)
        assert [r["id"] for r in view["records"]] == ["r1", "r3", "r2"], view["records"]
        assert view["progress"]["total"] == 3 and view["progress"]["done"] == 1
        assert view["resources"]["cost"] == 0.03
        assert view["window"]["total_ms"] == 5000.0
        assert view["agents"][0]["color_index"] == color_index_for("a1")
        report = service.grouping_report(records)
        assert report["accuracy"] == 1.0 and report["ungrouped"] == 1, report
        assert service.close(group["id"])["status"] == "completed"
        print(f"groups self-check ok: group={group['id']} records={len(view['records'])} "
              f"agents={len(view['agents'])} report={report['accuracy']}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
