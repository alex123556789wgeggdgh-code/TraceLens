"""4.4 实时接入（t14）：webhook 写入 + 变更广播（SSE）+ SDK 上报的公共后端。

一句话：让 Agent「边跑边报」——进来的是事实，推出去的也是同一份事实。

工程纪律（与 docs/03 §5、§6.3 E1 / F1 的既有契约对齐）：

1. **只做写入与广播**：不改解释引擎（``explanations.py``），不改审计链算法
   （``audit.py``）。上报产生的记账通过服务端已有的追加式审计 broker 落一条
   ``note`` 事件（``extra.ingest_event``），审计口径与 t9 / t18 完全一致。
2. **令牌只存哈希**：``sha256(salt + 明文)``，明文只在创建那一刻返回一次；
   校验用 ``hmac.compare_digest``（定长时间比较），枚举令牌不会从响应时间泄漏。
   令牌文件读不到 = 一个令牌都没有（fail closed），绝不"降级放行"。
3. **归一化只复用** :mod:`agent_explainer.trace_adapter`：``source_kind="webhook"``
   （SDK 用 ``"sdk"``）。未知字段归档、坏记录按 ``strict`` 决定报错还是跳过——
   与 t3 的离线批处理是同一套代码，不存在"实时一套规则、离线另一套规则"。
4. **存储口径不变**：入库 = 新增，或按白名单合并事实字段。
   永不覆盖 ``user_intervention`` / ``intervention_options`` / ``execution_receipt``
   的决定语义（t8：决定 ≠ 生效），也永不覆盖 ``human_readable``
   （那是 t5 解释层的产物，不是上报方可以随口改写的字段）。
5. **广播是有界队列**：慢订阅者丢最旧帧并标记 ``degraded``，绝不把写入方拖住；
   断线重连用 ``Last-Event-ID`` 从有界环形缓冲补发，补不上的部分如实说明。

离线纯度：本模块是包内顶层文件，受 ``tests/test_trace_adapter.py`` 的离线纯净扫描
约束——只用标准库，不出现任何 URL 字面量、网络模块或子进程调用。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import pathlib
import queue
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from . import audit as audit_mod
from . import trace_adapter as ta

__all__ = [
    "INGEST_SCHEMA_VERSION", "INGEST_VERSION",
    "TOKEN_HEADER", "AUTH_HEADER_NAME", "BEARER_PREFIX", "TOKEN_SECRET_PREFIX",
    "SCOPES", "SCOPE_WRITE", "SCOPE_MANAGE",
    "EVENT_CREATED", "EVENT_UPDATED", "EVENT_DECISION", "EVENT_RESET",
    "EVENT_HELLO", "EVENT_HEARTBEAT", "EVENT_BATCH", "EVENT_STATS",
    "STREAM_EVENT_TYPES", "REPLAY_RING_SIZE",
    "MAX_EVENTS_PER_REQUEST", "MAX_ENVELOPE_BYTES",
    "MERGE_FIELDS", "PROTECTED_FIELDS",
    "ACTION_TYPE_LABELS", "STATUS_LABELS", "RECORD_STATUSES", "DECIDABLE_STATUSES",
    "EFFECT_NOTICE", "SECRET_KEY_HINTS",
    "IngestError", "INGEST_STATUS", "ingest_status_of",
    "TokenVault", "RateLimiter", "StreamHub", "Subscriber", "IngestBroker",
    "make_event", "make_events_batch", "parse_envelope", "merge_facts",
    "human_readable_for", "default_intervention_options", "stamp_source",
    "public_record", "scrub",
    "ANONYMOUS_LOCAL_TOKEN",
]

INGEST_SCHEMA_VERSION = "ingest.v1"
INGEST_VERSION = "4.4-v1"

# ---------------------------------------------------------------------------
# 凭证
# ---------------------------------------------------------------------------

#: 团队模式的 webhook 凭证（与 4.3 的会话 token 分开：这个只能写入，读不到用户表）
TOKEN_HEADER = "X-TraceLens-Ingest-Token"
AUTH_HEADER_NAME = "Authorization"
BEARER_PREFIX = "Bearer"
TOKEN_SECRET_PREFIX = "tlk_"

SCOPE_WRITE = "ingest:write"
SCOPE_MANAGE = "tokens:manage"
SCOPES: tuple[str, ...] = (SCOPE_WRITE, SCOPE_MANAGE)

#: local 模式的匿名通道：T1 护栏要求本机零门槛，所以本机上报不需要令牌，
#: 但如实标成 anonymous —— 界面与健康检查都会写明「本机匿名写入」。
ANONYMOUS_LOCAL_TOKEN: dict[str, Any] = {
    "token_id": "local-anonymous",
    "label": "local",
    "scopes": [SCOPE_WRITE],
    "anonymous": True,
}

# ---------------------------------------------------------------------------
# 事件类型（SSE 的 event: 行；与界面 app.js 的监听器一一对应）
# ---------------------------------------------------------------------------

EVENT_CREATED = "action.created"
EVENT_UPDATED = "action.updated"
EVENT_DECISION = "action.decision_recorded"
EVENT_RESET = "records.reset"
EVENT_HELLO = "hello"
EVENT_HEARTBEAT = "heartbeat"
EVENT_BATCH = "ingest.batch"
EVENT_STATS = "ingest.stats"

STREAM_EVENT_TYPES: tuple[str, ...] = (
    EVENT_HELLO, EVENT_CREATED, EVENT_UPDATED, EVENT_DECISION, EVENT_RESET,
    EVENT_BATCH, EVENT_STATS, EVENT_HEARTBEAT,
)

#: 断线重连补发的环形缓冲大小（有界，内存可控）
REPLAY_RING_SIZE = 200

MAX_EVENTS_PER_REQUEST = 200
MAX_ENVELOPE_BYTES = 64 * 1024          # 与 server.MAX_BODY_BYTES 同口径
DEFAULT_RATE_CAPACITY = 40              # 桶容量（突发）
DEFAULT_RATE_PER_MIN = 600              # 稳态：每分钟允许的条数
IDEMPOTENCY_TTL_SECONDS = 15 * 60

#: 更新时允许合并的事实字段（白名单，越白名单越短）
MERGE_FIELDS: tuple[str, ...] = (
    "status", "timestamp", "result_summary", "tool_used", "latency_ms", "cost",
    "confidence", "risk_level", "impact_scope", "error", "requires_action",
    "requires_action_reason", "action_due_at", "uncertainty", "suggested_next",
    "labels", "sequence_no", "context", "trace_link", "run_id", "agent_name",
)
#: 明确不合并的字段（写进自检，防止以后有人"顺手"加进去）
PROTECTED_FIELDS: tuple[str, ...] = (
    "id", "schema_version", "user_intervention", "intervention_options",
    "human_readable", "source", "demo_data", "synthetic", "policy",
)
#: 这些事实字段变化后，``human_readable`` 由本模块按合并后的事实**重算**
#: （不是客户端塞进来的文案：文案字段本身在上面的保护名单里，永不合并）。
#: 不做这件事会出现"角标已完成、文案还写着进行中"这种自相矛盾的界面。
READABLE_FACT_FIELDS: frozenset[str] = frozenset({
    "status", "result_summary", "tool_used", "risk_level", "requires_action",
    "requires_action_reason", "latency_ms", "error", "impact_scope", "action_due_at",
})

# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

#: code -> (HTTP 状态, 人话说明)
INGEST_STATUS: dict[str, tuple[int, str]] = {
    "INGEST_DISABLED": (403, "Realtime ingest is switched off on this server."),
    "INGEST_TOKEN_MISSING": (401, "No ingest credential was supplied."),
    "INGEST_TOKEN_INVALID": (401, "The ingest credential is unknown."),
    "INGEST_TOKEN_DISABLED": (403, "The ingest credential was revoked."),
    "INGEST_TOKEN_EXPIRED": (403, "The ingest credential has expired."),
    "INGEST_SCOPE_FORBIDDEN": (403, "The ingest credential lacks the required scope."),
    "INGEST_RATE_LIMITED": (429, "Ingest rate limit reached; slow down."),
    "INGEST_SCHEMA_INVALID": (400, "The ingest envelope is not a valid object or array."),
    "INGEST_EMPTY": (400, "The ingest envelope carries no events."),
    "INGEST_TOO_MANY_EVENTS": (413, "The batch carries more events than allowed."),
    "INGEST_TOO_LARGE": (413, "The batch is larger than the byte limit."),
    "INGEST_FIELD_TYPE": (400, "An ingest field has the wrong type."),
    "INGEST_IDEMPOTENCY_CONFLICT": (409, "The same idempotency key was reused with another body."),
    "INGEST_FORMAT_UNSUPPORTED": (422, "The event format is not one of the four supported."),
    "INGEST_ADAPTER_REJECTED": (422, "The trace adapter refused the events."),
    "INGEST_STORE_FAILURE": (500, "The record store refused the write."),
    "STREAM_ACCEPT_REQUIRED": (406, "The stream endpoint needs Accept: text/event-stream."),
    "STREAM_LIMIT_REACHED": (503, "Too many live stream subscribers."),
    "STREAM_QUERY_INVALID": (400, "The stream query parameters are not valid."),
}


def ingest_status_of(code: str) -> int:
    return int(INGEST_STATUS.get(str(code), (500, "Ingest failure."))[0])


class IngestError(Exception):
    """带 code / 人话 / 定位（field、index）的入参错误。服务端翻成统一 JSON。"""

    def __init__(self, code: str, reason: str | None = None, *, field: str | None = None,
                 index: int | None = None, hint: str | None = None,
                 detail: Mapping[str, Any] | None = None,
                 status: int | None = None, retry_after: float | None = None) -> None:
        self.code = str(code)
        self.status = int(status if status is not None else ingest_status_of(self.code))
        self.reason = str(reason if reason else INGEST_STATUS.get(self.code, ("", ""))[1])
        self.field = field
        self.index = index
        self.hint = hint
        self.detail: dict[str, Any] = dict(detail or {})
        self.retry_after = retry_after
        super().__init__(self.render())

    def render(self) -> str:
        where = f"（字段 {self.field}）" if self.field else ""
        at = f"（第 {int(self.index) + 1} 条）" if isinstance(self.index, int) else ""
        tail = f"提示：{self.hint}" if self.hint else ""
        return f"{self.code}: {self.reason}{where}{at}{tail}"

    def as_dict(self, request_id: str | None = None) -> dict[str, Any]:
        detail = dict(self.detail)
        if self.field:
            detail.setdefault("field", self.field)
        if isinstance(self.index, int):
            detail.setdefault("index", self.index)
        if self.hint:
            detail.setdefault("hint", self.hint)
        if self.retry_after is not None:
            detail.setdefault("retry_after_ms", int(self.retry_after * 1000))
        out: dict[str, Any] = {"code": self.code, "message": self.reason, "detail": detail}
        if request_id:
            out["request_id"] = request_id
        return out


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    moment = moment or _utc_now()
    return (moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _consteq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _clean_id(value: Any, limit: int = 128) -> str:
    text = str(value if value is not None else "").strip()
    return text[:int(limit)]


def _clip(text: Any, limit: int) -> str:
    body = str(text if text is not None else "")
    return body if len(body) <= int(limit) else (body[: max(1, int(limit) - 1)] + "…")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _brief(value: Any, limit: int = 120) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = value if isinstance(value, str) else _canonical(value)
    return text if len(text) <= limit else (text[:limit] + "…")


def public_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """去掉盐与哈希的对外视图（任何接口都不得返回凭证材料）。"""
    return {k: v for k, v in record.items() if k not in ("salt", "hash")}


SECRET_KEY_HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                    "authorization", "cookie", "session", "credential", "private_key")


def scrub(value: Any, *, depth: int = 0) -> Any:
    """上报前的自净：密钥类键名整值打码，深度与条数有界。"""
    if depth > 4:
        return "<truncated>"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(hint in name for hint in SECRET_KEY_HINTS):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = scrub(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    return text if len(text) <= 200 else (text[:200] + "…")


# ---------------------------------------------------------------------------
# 令牌保管箱
# ---------------------------------------------------------------------------

class TokenVault:
    """ingest 令牌的保管箱：只存 ``sha256(salt + 明文)``，明文一次性返回。

    ``path=None`` 时是纯内存保管箱（测试与嵌入式调用用），进程退出即消失；
    产品入口把路径指到 ``var/ingest/tokens.json``（与 4.3 的用户表同处，都不进版本库）。
    这不是追加式审计链：令牌表是可重写的密钥表，所以用「临时文件 + 原子替换」写入，
    不追加历史（历史里留着一堆旧哈希没有意义，反而是泄漏面）。
    """

    def __init__(self, path: pathlib.Path | str | None = None, *,
                 now: datetime | None = None) -> None:
        self.path = pathlib.Path(path) if path else None
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self._read_failed: str | None = None
        self._now = now
        if self.path is not None:
            self._load()

    # -- 持久化 ----------------------------------------------------------
    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # 读不到就当没有令牌：绝不"降级放行"，并如实报告原因
            self._read_failed = f"{type(exc).__name__}: {exc}"
            self._records = []
            return
        tokens = doc.get("tokens") if isinstance(doc, Mapping) else None
        self._records = ([dict(r) for r in tokens if isinstance(r, Mapping)]
                         if isinstance(tokens, list) else [])

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {"ingest_schema_version": INGEST_SCHEMA_VERSION,
                   "written_at": _iso(), "tokens": self._records}
        blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.parent / f"{self.path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
            tmp.write_text(blob + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            raise IngestError("INGEST_STORE_FAILURE",
                              f"令牌表写不进去：{type(exc).__name__}: {exc}",
                              detail={"path": str(self.path),
                                      "hint": "check disk space and file permissions; "
                                              "the vault never falls back to 'allow all'"}) from exc

    # -- 令牌 ------------------------------------------------------------
    def create(self, label: str, *, scopes: Sequence[str] = (SCOPE_WRITE,),
               created_by: str = "local-user",
               ttl_seconds: int | None = None) -> tuple[str, dict[str, Any]]:
        name = _clean_id(label)
        if not name:
            raise IngestError("INGEST_FIELD_TYPE", "label is required", field="label")
        wanted = [str(s) for s in scopes] or [SCOPE_WRITE]
        for scope in wanted:
            if scope not in SCOPES:
                raise IngestError("INGEST_FIELD_TYPE", f"unknown scope {scope!r}",
                                  field="scopes", hint="/".join(SCOPES))
        secret = TOKEN_SECRET_PREFIX + secrets.token_hex(24)
        salt = secrets.token_hex(16)
        record: dict[str, Any] = {
            "token_id": "tok-" + secrets.token_hex(6),
            "label": name,
            "scopes": sorted(set(wanted)),
            "salt": salt,
            "hash": _sha(salt + secret),
            "prefix": secret[:12],
            "created_at": _iso(self._now),
            "created_by": _clean_id(created_by),
            "expires_at": None,
            "disabled_at": None,
            "last_used_at": None,
            "uses": 0,
        }
        if isinstance(ttl_seconds, int) and ttl_seconds > 0:
            record["expires_at"] = _iso(datetime.fromtimestamp(
                time.time() + ttl_seconds, tz=timezone.utc))
        with self._lock:
            self._records.append(record)
            self._save()
            return secret, copy.deepcopy(record)

    def _find(self, secret: str) -> dict[str, Any] | None:
        text = str(secret or "")
        if not text:
            return None
        with self._lock:
            for record in self._records:
                if _consteq(_sha(str(record.get("salt", "")) + text),
                            str(record.get("hash", ""))):
                    return record
        return None

    def verify(self, secret: str, *, scope: str = SCOPE_WRITE) -> dict[str, Any]:
        """校验并返回令牌记录；失败一律抛 :class:`IngestError`。"""
        if _is_blank(secret):
            raise IngestError("INGEST_TOKEN_MISSING",
                              "缺少 ingest 凭证：请求头 "
                              f"{AUTH_HEADER_NAME}: {BEARER_PREFIX} <token> 或 {TOKEN_HEADER}: <token>")
        record = self._find(secret)
        if record is None:
            raise IngestError("INGEST_TOKEN_INVALID", "令牌不存在（不透露是哪一个，避免枚举）")
        if record.get("disabled_at"):
            raise IngestError("INGEST_TOKEN_DISABLED", "该令牌已被吊销",
                              detail={"token_id": record.get("token_id")})
        expires = record.get("expires_at")
        if expires and str(expires) <= _iso(self._now):
            raise IngestError("INGEST_TOKEN_EXPIRED", f"令牌已过期（{expires}）",
                              detail={"token_id": record.get("token_id"), "expires_at": expires})
        if scope and scope not in list(record.get("scopes") or []):
            raise IngestError("INGEST_SCOPE_FORBIDDEN", f"该令牌没有 {scope} 权限",
                              detail={"token_id": record.get("token_id"),
                                      "scopes": list(record.get("scopes") or []),
                                      "needs": scope})
        return copy.deepcopy(record)

    def record_use(self, token_id: str) -> None:
        with self._lock:
            for record in self._records:
                if record.get("token_id") == str(token_id):
                    record["uses"] = int(record.get("uses") or 0) + 1
                    record["last_used_at"] = _iso(self._now)
                    break
            self._save()

    def revoke(self, token_id: str, *, by: str = "local-user") -> dict[str, Any]:
        target = _clean_id(token_id)
        with self._lock:
            for record in self._records:
                if record.get("token_id") == target:
                    record["disabled_at"] = _iso(self._now)
                    record["disabled_by"] = _clean_id(by)
                    self._save()
                    return public_record(record)
        raise IngestError("INGEST_TOKEN_INVALID", f"没有 id 为 {target} 的令牌", field="token_id")

    def list_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [public_record(r) for r in self._records]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            stamp = _iso(self._now)
            active = [r for r in self._records
                      if not r.get("disabled_at")
                      and not (r.get("expires_at") and str(r["expires_at"]) <= stamp)]
            return {"path": str(self.path) if self.path else "memory",
                    "tokens": len(self._records), "active": len(active),
                    "revoked": sum(1 for r in self._records if r.get("disabled_at")),
                    "read_failed": self._read_failed,
                    "storage": "file" if self.path else "memory",
                    "note": "令牌只存 sha256(salt+明文)；明文只在创建时返回一次"}


# ---------------------------------------------------------------------------
# 限速
# ---------------------------------------------------------------------------

class RateLimiter:
    """每令牌的令牌桶：容量 = 突发，速率 = 稳态。返回 (允许, 建议等待秒)。"""

    def __init__(self, capacity: float = DEFAULT_RATE_CAPACITY,
                 per_minute: float = DEFAULT_RATE_PER_MIN) -> None:
        self.capacity = float(max(1.0, capacity))
        self.per_second = float(max(1.0 / 60.0, per_minute / 60.0))
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, *, cost: float = 1.0,
              now: float | None = None) -> tuple[bool, float]:
        stamp = time.monotonic() if now is None else float(now)
        need = float(max(0.001, cost))
        with self._lock:
            level, last = self._buckets.get(str(key), (self.capacity, stamp))
            level = min(self.capacity, level + (stamp - last) * self.per_second)
            if level >= need:
                self._buckets[str(key)] = (level - need, stamp)
                return True, 0.0
            self._buckets[str(key)] = (level, stamp)
            return False, (need - level) / self.per_second

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(str(key), None)


# ---------------------------------------------------------------------------
# 广播（SSE 的后端）
# ---------------------------------------------------------------------------

class Subscriber:
    """一个 SSE 订阅者：有界队列 + 类型过滤 + 降级计数。"""

    def __init__(self, maxsize: int = 200, types: Sequence[str] | None = None,
                 since: int = 0, filters: Mapping[str, Any] | None = None) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=int(maxsize))
        self.types = set(types) if types else None
        self.dropped = 0
        self.degraded = False
        self.closed = False
        self.since = int(since)
        self.filters = dict(filters or {})

    def matches(self, frame: Mapping[str, Any]) -> bool:
        if self.types and str(frame.get("type")) not in self.types:
            return False
        data = frame.get("data") if isinstance(frame.get("data"), Mapping) else {}
        for key, value in self.filters.items():
            if str(data.get(key, "")) != str(value):
                return False
        return True

    def offer(self, frame: Mapping[str, Any]) -> None:
        if self.closed:
            return
        try:
            self._q.put_nowait(dict(frame))
        except queue.Full:
            # 慢客户端：丢最旧的一帧并如实标记，绝不堵住写入方
            self.degraded = True
            self.dropped += 1
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(dict(frame))
            except queue.Full:
                pass

    def get(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """返回一帧；``{}`` = 这一轮没有（心跳时机），``None`` = 流已结束。"""
        try:
            frame = self._q.get(timeout=timeout)
        except queue.Empty:
            return {}
        if frame is None:
            self.closed = True
            return None
        return frame

    def close(self) -> None:
        self.closed = True

    def snapshot(self) -> dict[str, Any]:
        return {"queued": self._q.qsize(), "dropped": self.dropped,
                "degraded": self.degraded, "closed": self.closed,
                "since": self.since,
                "types": sorted(self.types) if self.types else "all"}


class StreamHub:
    """把「记录新增/变更」广播给正在看的界面。有界、可补发、可关闭。"""

    def __init__(self, ring_size: int = REPLAY_RING_SIZE, *, limit: int = 16) -> None:
        self._lock = threading.RLock()
        self._subs: list[Subscriber] = []
        self._ring: list[dict[str, Any]] = []
        self._ring_size = int(max(1, ring_size))
        self.limit = int(limit)
        self.seq = 0
        self.published = 0
        self.stopped = False
        self.last_published_at: str | None = None
        self.rejected = 0

    # -- 订阅 ------------------------------------------------------------
    def subscribe(self, *, types: Sequence[str] | None = None, since: int = 0,
                  maxsize: int = 200,
                  filters: Mapping[str, Any] | None = None) -> Subscriber:
        with self._lock:
            if self.stopped:
                self.rejected += 1
                raise IngestError("STREAM_LIMIT_REACHED", "广播已关闭（服务正在收尾）")
            if len(self._subs) >= self.limit:
                self.rejected += 1
                raise IngestError("STREAM_LIMIT_REACHED",
                                  f"同时在线的订阅者已达上限（{self.limit}）",
                                  detail={"limit": self.limit, "subscribers": len(self._subs),
                                          "hint": "the page falls back to polling; nothing is "
                                                  "lost, the records endpoint stays authoritative"})
            sub = Subscriber(maxsize=maxsize, types=types, since=since, filters=filters)
            self._subs.append(sub)
            if since > 0:
                for frame in self._ring:
                    if int(frame.get("seq") or 0) > since and sub.matches(frame):
                        sub.offer(frame)
                        sub.since = int(frame.get("seq") or sub.since)
            return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        sub.close()
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    # -- 发布 ------------------------------------------------------------
    def publish(self, event_type: str, data: Mapping[str, Any], *,
                record_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self.seq += 1
            frame = {"seq": self.seq, "type": str(event_type), "ts": _iso(),
                     "record_id": _clean_id(record_id) or None,
                     "data": copy.deepcopy(dict(data or {}))}
            self._ring.append(frame)
            if len(self._ring) > self._ring_size:
                del self._ring[: len(self._ring) - self._ring_size]
            self.published += 1
            self.last_published_at = frame["ts"]
            for sub in list(self._subs):
                if sub.matches(frame):
                    sub.offer(frame)
            return frame

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            for sub in self._subs:
                sub.closed = True
                try:
                    sub._q.put_nowait(None)
                except queue.Full:
                    pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"subscribers": len(self._subs), "published": self.published,
                    "head_seq": self.seq, "frames_kept": len(self._ring),
                    "ring_size": self._ring_size, "limit": self.limit,
                    "rejected": self.rejected, "stopped": self.stopped,
                    "last_published_at": self.last_published_at,
                    "subscribers_detail": [s.snapshot() for s in self._subs][:8]}

    def frames_after(self, seq: int, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(f) for f in self._ring
                    if int(f.get("seq") or 0) > int(seq)][:limit]


# ---------------------------------------------------------------------------
# 信封
# ---------------------------------------------------------------------------

ENVELOPE_KEYS = ("events", "records", "batch", "observations", "spans",
                 "messages", "stream", "logs")


def parse_envelope(payload: Any, *, max_events: int = MAX_EVENTS_PER_REQUEST,
                   max_bytes: int = MAX_ENVELOPE_BYTES) -> tuple[Any, dict[str, Any]]:
    """把请求体拆成「事件集合 + 控制参数」。

    三种写法（都写进 README 与自检，不做隐式魔法）：

    - ``{"events": [...]}`` / ``{"records": [...]}``（推荐：可带 ``idempotency_key``）
    - 裸数组 ``[...]``（单条也写成一条数组）
    - 单条事件对象（带 ``schema_version`` + ``action_type`` 的原生记录）
    """
    if payload is None:
        raise IngestError("INGEST_EMPTY", "请求体是空的",
                          hint='{"events":[...]} / {"records":[...]} / 裸数组 / 单条事件')
    if isinstance(payload, (str, bytes)):
        raise IngestError("INGEST_SCHEMA_INVALID",
                          f"请求体是 {type(payload).__name__}，需要 JSON 对象或数组", field="body")
    if not isinstance(payload, (Mapping, list)):
        raise IngestError("INGEST_SCHEMA_INVALID",
                          f"请求体是 {type(payload).__name__}，需要 JSON 对象或数组", field="body")

    meta: dict[str, Any] = {}
    items: Any
    if isinstance(payload, list):
        items = payload
    else:
        doc = payload
        for key in ("idempotency_key", "format", "language", "source_kind", "label", "note"):
            if key in doc:
                meta[key] = doc[key]
        if "strict" in doc:
            if not isinstance(doc["strict"], bool):
                raise IngestError("INGEST_FIELD_TYPE", "strict 必须是布尔值", field="strict")
            meta["strict"] = bool(doc["strict"])
        mode = str(doc.get("mode") or "").strip().lower()
        if mode and mode not in ("create", "update", "upsert"):
            raise IngestError("INGEST_FIELD_TYPE",
                              f"mode={mode!r} 只支持 create / update / upsert", field="mode")
        meta["mode"] = mode or "create"
        found = None
        for key in ENVELOPE_KEYS:
            if isinstance(doc.get(key), list):
                found = key
                break
        if found:
            items = list(doc[found] or [])
            meta["wrapper"] = found
        elif "schema_version" in doc and "action_type" in doc:
            items = [doc]                            # 单条事件
            meta["wrapper"] = "single"
        else:
            raise IngestError("INGEST_SCHEMA_INVALID",
                              "既没有 events/records 数组，也不是一条原生事件",
                              field=next(iter(sorted(doc)), None),
                              hint='需要 {"events":[...]}、{"records":[...]}，'
                                   '或带 schema_version + action_type 的单条事件')

    if not isinstance(items, list):
        raise IngestError("INGEST_FIELD_TYPE", "事件集合必须是数组", field="events")
    if not items:
        raise IngestError("INGEST_EMPTY", "事件集合是空数组", field="events",
                          hint="empty batches are rejected, never a silent no-op success")
    if len(items) > int(max_events):
        raise IngestError("INGEST_TOO_MANY_EVENTS",
                          f"一批 {len(items)} 条超过上限 {int(max_events)}",
                          field="events", detail={"count": len(items), "limit": int(max_events)})
    try:
        size = len(_canonical(payload).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise IngestError("INGEST_SCHEMA_INVALID",
                          f"请求体不是可序列化的 JSON：{type(exc).__name__}", field="body") from exc
    if size > int(max_bytes):
        raise IngestError("INGEST_TOO_LARGE", f"请求体 {size} 字节超过上限 {int(max_bytes)}",
                          detail={"bytes": size, "limit": int(max_bytes)})
    meta.setdefault("mode", "create")
    meta["bytes"] = size
    meta["fingerprint"] = _sha(_canonical(items))
    return items, meta


# ---------------------------------------------------------------------------
# 合并规则
# ---------------------------------------------------------------------------

def merge_facts(before: Mapping[str, Any],
                incoming: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按白名单合并事实字段，返回 (新记录, changed_fields)。

    ``PROTECTED_FIELDS`` 挡在外面的永不覆盖：决定语义（``user_intervention`` /
    ``intervention_options``）、执行回执、解释文案、来源归因、演示标记、policy。
    上报方能改的是"这一步发生了什么"，不是"人决定了什么"。
    """
    merged = copy.deepcopy(dict(before))
    changed: list[dict[str, Any]] = []
    for field in MERGE_FIELDS:
        if field not in incoming:
            continue
        value = incoming[field]
        if _is_blank(value) and not isinstance(value, (bool, int, float)):
            continue
        prior = merged.get(field)
        if _canonical(prior) == _canonical(value):
            continue
        merged[field] = copy.deepcopy(value)
        changed.append({"field": field, "before": _brief(prior), "after": _brief(value)})
    merged["received_at"] = _iso()
    return merged, changed


def stamp_source(record: dict[str, Any], *, kind: str, source_uri: str) -> dict[str, Any]:
    """把接入归因写进 ``source``（schema 只允许那五个键，不乱加）。

    上报方声明的 ``adapter`` / ``format_version`` 保留；``kind`` 由服务端按真实通道盖章
    ——否则任何客户端都能自称 ``manual``，K-03 的接入归因就废了。
    令牌与批次信息进审计 ``note``（t9 口径），不进记录。
    """
    source = dict(record.get("source") or {})
    source["kind"] = kind
    source["adapter"] = str(source.get("adapter") or "native")[:80]
    source["format_version"] = str(source.get("format_version")
                                   or f"{ta.SCHEMA_VERSION} via {kind}")[:40]
    source["source_uri"] = _clip(source_uri, 500)
    source["ingested_at"] = _iso()
    record["source"] = source
    record["received_at"] = _iso()
    return record


# ---------------------------------------------------------------------------
# 接入器
# ---------------------------------------------------------------------------

class IngestBroker:
    """webhook / SDK 上报的「鉴权 → 拆信封 → 幂等 → 归一化 → 落库 → 广播 → 记账」编排。"""

    def __init__(self, store: Any, hub: StreamHub | None = None, *,
                 vault: TokenVault | None = None, enabled: bool = True,
                 mode: str = "local", language: str = "zh",
                 rate_capacity: float = DEFAULT_RATE_CAPACITY,
                 rate_per_minute: float = DEFAULT_RATE_PER_MIN,
                 max_events: int = MAX_EVENTS_PER_REQUEST,
                 max_bytes: int = MAX_ENVELOPE_BYTES,
                 idempotency_ttl: float = IDEMPOTENCY_TTL_SECONDS,
                 strict_default: bool = True,
                 signer: Any = None) -> None:
        self.store = store
        #: 可选的写入前签名器：``signer(record, previous) -> record``。
        #: 由 server.InterveneService 注入（产品入口默认开启，库默认关闭）；
        #: 这里只负责"落库前过一道"，不把密码学带进 ingest。
        self.signer = signer
        self.hub = hub or StreamHub()
        self.vault = vault
        self.enabled = bool(enabled)
        self.mode = str(mode)
        self.language = language if language in ("zh", "en") else "zh"
        self.max_events = int(max_events)
        self.max_bytes = int(max_bytes)
        self.idempotency_ttl = float(idempotency_ttl)
        self.strict_default = bool(strict_default)
        self.limiter = RateLimiter(rate_capacity, rate_per_minute)
        self._lock = threading.RLock()
        self._idempotency: dict[str, tuple[str, dict[str, Any], float]] = {}
        self.counters = {"batches": 0, "accepted": 0, "created": 0, "updated": 0,
                         "duplicates": 0, "skipped": 0, "rejected": 0, "replays": 0,
                         "rate_limited": 0, "sdk_events": 0, "webhook_events": 0}
        self.last_batch: dict[str, Any] = {}

    # -- 鉴权 -------------------------------------------------------------
    def authenticate(self, secret: str | None, *, scope: str = SCOPE_WRITE) -> dict[str, Any]:
        """team 模式必须有有效令牌；local 模式匿名放行（并如实标注）。"""
        if self.vault is None:
            if self.mode == "team":
                raise IngestError("INGEST_DISABLED",
                                  "team 模式没有令牌保管箱，实时接入不可用（fail closed）")
            return dict(ANONYMOUS_LOCAL_TOKEN)
        if _is_blank(secret):
            if self.mode == "team":
                raise IngestError("INGEST_TOKEN_MISSING",
                                  f"team 模式的上报必须带令牌：{AUTH_HEADER_NAME}: "
                                  f"{BEARER_PREFIX} <token>")
            return dict(ANONYMOUS_LOCAL_TOKEN)
        return self.vault.verify(secret, scope=scope)

    # -- 幂等 -------------------------------------------------------------
    def _idempotency_get(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._idempotency.get(str(key))
            if entry is None:
                return None
            prior_print, result, stamp = entry
            if time.monotonic() - stamp > self.idempotency_ttl:
                del self._idempotency[str(key)]
                return None
            if prior_print != fingerprint:
                raise IngestError("INGEST_IDEMPOTENCY_CONFLICT",
                                  "同一个 idempotency_key 带了不同的请求体",
                                  detail={"idempotency_key": str(key),
                                          "hint": "the key means 'the same request, sent twice'; "
                                                  "use a new key for a new body"})
            return dict(result)

    def _idempotency_put(self, key: str, fingerprint: str, result: Mapping[str, Any]) -> None:
        with self._lock:
            self._idempotency[str(key)] = (fingerprint, dict(result), time.monotonic())
            if len(self._idempotency) > 256:
                cutoff = time.monotonic() - self.idempotency_ttl
                for stale in [k for k, (_p, _r, s) in self._idempotency.items() if s < cutoff]:
                    del self._idempotency[stale]

    # -- 主流程 -----------------------------------------------------------
    def ingest(self, payload: Any, *, token: Mapping[str, Any] | None = None,
               idempotency_key: str | None = None, format: str | None = None,
               source_kind: str = "webhook", request_id: str | None = None,
               strict: bool | None = None) -> dict[str, Any]:
        """一次上报的完整处理。审计记账由调用方（服务端）落，见 :meth:`audit_event`。"""
        if not self.enabled:
            raise IngestError("INGEST_DISABLED", "本服务已关闭实时接入（--no-ingest）")
        record_token = dict(token or ANONYMOUS_LOCAL_TOKEN)
        if record_token.get("anonymous") and self.mode == "team":
            raise IngestError("INGEST_TOKEN_MISSING", "team 模式不接受匿名上报")

        items, meta = parse_envelope(payload, max_events=self.max_events,
                                     max_bytes=self.max_bytes)
        key = _clean_id(idempotency_key or meta.get("idempotency_key"))
        fingerprint = str(meta.get("fingerprint"))
        cached = self._idempotency_get(key, fingerprint) if key else None
        with self._lock:
            self.counters["batches"] += 1
        if cached is not None:
            with self._lock:
                self.counters["replays"] += 1
            replay = dict(cached)
            replay["replay"] = True
            replay["idempotency"] = {"key": key, "replay": True,
                                     "fingerprint": fingerprint[:16]}
            return replay

        ok, wait = self.limiter.allow(str(record_token.get("token_id") or "anonymous"),
                                      cost=float(len(items)))
        if not ok:
            with self._lock:
                self.counters["rate_limited"] += 1
                self.counters["rejected"] += 1
            raise IngestError("INGEST_RATE_LIMITED",
                              f"上报太快：每令牌约每分钟 {int(self.limiter.per_second * 60)} 条",
                              retry_after=wait,
                              detail={"capacity": self.limiter.capacity,
                                      "per_minute": int(self.limiter.per_second * 60),
                                      "events_in_batch": len(items)})

        # 接入方式：调用方声明优先（SDK 会经信封的 format 字段自报 "sdk"），
        # 再次是服务端按端点给的默认值（E1 默认 webhook）；写不认识的一律回落 webhook。
        meta_kind = str(meta.get("format") or "")
        kind = str(format if format in ta.SOURCE_KINDS
                   else (meta_kind if meta_kind in ta.SOURCE_KINDS else source_kind))
        if kind not in ta.SOURCE_KINDS:
            kind = "webhook"
        source_uri = f"webhook://ingest/{_clean_id(record_token.get('token_id'))}"
        strict_flag = self.strict_default if strict is None else bool(strict)
        if strict is None and isinstance(meta.get("strict"), bool):
            strict_flag = bool(meta["strict"])
        language = str(meta.get("language") or self.language)

        if not strict_flag:
            # 整批转换在第一条坏事件处停下：那样一条坏记录会让整批好数据都不入库。
            # strict=false 的语义是"坏事件跳过、好事件照常入库"，所以逐条重放一次，
            # 跳过原因按**原始下标**报出来，调用方才知道改哪一条。
            outcome = self._convert_lenient(items, kind=kind, source_uri=source_uri,
                                            language=language)
        else:
            try:
                outcome = ta.normalize_events_with_raw(
                    items, source_kind=kind, source_uri=source_uri,
                    language=language, strict=True)
            except ta.TraceFormatError as exc:
                with self._lock:
                    self.counters["rejected"] += 1
                code = ("INGEST_FORMAT_UNSUPPORTED"
                        if str(exc.code) in ("TRACE_FORMAT_UNSUPPORTED", "TRACE_FORMAT_UNKNOWN")
                        else "INGEST_ADAPTER_REJECTED")
                raise IngestError(code, exc.reason, field=exc.field, index=exc.index,
                                  hint=exc.hint,
                                  detail={"adapter_code": exc.code,
                                          "hint": "fix the event; do not switch the format "
                                                  "silently. strict=false skips bad events and "
                                                  "lists them in stats.skip_reasons"}) from exc

        created: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        mode = str(meta.get("mode") or "create")
        for record in outcome.records:
            stamp_source(record, kind=kind, source_uri=source_uri)
            action_id = str(record.get("id"))
            before = self.store.get(action_id)
            if before is None:
                if self.signer is not None:
                    record = self.signer(record, None)
                self.store.put(record)
                created.append(record)
                frames.append(self.hub.publish(EVENT_CREATED, {
                    "record": record, "action_id": action_id, "source_kind": kind,
                    "token_id": record_token.get("token_id"), "received_at": _iso()},
                    record_id=action_id))
                continue
            if mode not in ("update", "upsert"):
                duplicates.append({"action_id": action_id,
                                   "reason": "id 已存在，mode=create 不覆盖已有记录"})
                continue
            after, changed = merge_facts(before, record)
            refreshed = self._refresh_readable(after, before, changed, language=language)
            if refreshed is not None:
                changed = list(changed) + [{"field": "human_readable", "before": "旧事实的文案",
                                            "after": _clip(str(refreshed.get("l1") or ""), 80)}]
            if self.signer is not None:
                after = self.signer(after, before)
            self.store.put(after)
            updated.append(after)
            frames.append(self.hub.publish(EVENT_UPDATED, {
                "record": after, "action_id": action_id, "changed_fields": changed,
                "source_kind": kind, "token_id": record_token.get("token_id"),
                "received_at": _iso()}, record_id=action_id))

        batch_frame = self.hub.publish(EVENT_BATCH, {
            "accepted": len(outcome.records), "created": len(created),
            "updated": len(updated), "duplicates": len(duplicates),
            "format": outcome.format, "token_id": record_token.get("token_id"),
            "ids": [str(r.get("id")) for r in outcome.records][:40],
            "request_id": request_id})

        result = {
            "ok": True,
            "accepted": len(created) + len(updated),
            "created": len(created), "updated": len(updated),
            "duplicates": len(duplicates), "skipped": int(outcome.stats.get("skipped") or 0),
            "ids": [str(r.get("id")) for r in outcome.records],
            "duplicate_detail": duplicates[:20],
            "format": outcome.format, "mode": mode, "source_kind": kind,
            "token": {"token_id": record_token.get("token_id"),
                      "label": record_token.get("label"),
                      "scopes": list(record_token.get("scopes") or []),
                      "anonymous": bool(record_token.get("anonymous"))},
            "stats": dict(outcome.stats),
            "raw_payloads": len(outcome.raw_payloads),
            "stream": {"head_seq": batch_frame["seq"], "frames": [f["seq"] for f in frames],
                       "subscribers": self.hub.snapshot()["subscribers"]},
            "idempotency": ({"key": key, "replay": False, "fingerprint": fingerprint[:16]}
                            if key else None),
            "replay": False,
            "request_id": request_id,
            "ingest_version": INGEST_VERSION,
            "received_at": _iso(),
            "note": "accepted records are facts; nothing here says TraceLens executed the "
                    "action（决定 ≠ 生效，t8 口径）",
        }
        with self._lock:
            self.counters["accepted"] += len(created) + len(updated)
            self.counters["created"] += len(created)
            self.counters["updated"] += len(updated)
            self.counters["duplicates"] += len(duplicates)
            self.counters["skipped"] += int(outcome.stats.get("skipped") or 0)
            self.counters["webhook_events" if kind == "webhook" else "sdk_events"] += len(items)
            self.last_batch = {"at": result["received_at"], "accepted": result["accepted"],
                               "format": outcome.format,
                               "token_id": result["token"]["token_id"]}
        if key:
            self._idempotency_put(key, fingerprint, result)
        if self.vault is not None and not record_token.get("anonymous"):
            self.vault.record_use(str(record_token.get("token_id")))
        return result

    # -- 宽容模式：坏事件跳过、好事件入库（适配器整批停用时逐条重放）----------
    def _convert_lenient(self, items: Sequence[Any], *, kind: str, source_uri: str,
                         language: str) -> ta.ConversionOutcome:
        records: list[dict[str, Any]] = []
        raws: dict[str, dict[str, Any]] = {}
        skips: list[dict[str, Any]] = []
        formats: list[str] = []
        spec: str | None = None
        for index, item in enumerate(items):
            try:
                one = ta.normalize_events_with_raw([item], source_kind=kind,
                                                   source_uri=source_uri,
                                                   language=language, strict=True)
            except ta.TraceFormatError as exc:
                event_id = item.get("id") if isinstance(item, Mapping) else None
                skips.append({"index": index,
                              "id": _clean_id(event_id) or None,
                              "code": str(exc.code),
                              "reason": _clip(str(exc.reason), 160)})
                continue
            records.extend(one.records)
            raws.update(one.raw_payloads)
            if one.format and one.format not in formats:
                formats.append(one.format)
            spec = spec or one.stats.get("format_spec_version")
        total = len(items)
        stats = {"input_events": total, "converted": len(records), "skipped": len(skips),
                 "skip_reasons": skips[:20], "unknown_field_records": len(raws),
                 "formats": formats,
                 "conversion_rate": (len(records) / total) if total else 0.0,
                 "format_spec_version": spec, "source_kind": kind, "language": language,
                 "adapter_name": ta.ADAPTER_NAME, "adapter_version": ta.ADAPTER_VERSION,
                 "lenient": True}
        return ta.ConversionOutcome(records=records, raw_payloads=raws,
                                    format=(formats[0] if formats else "native"),
                                    stats=stats)

    # -- 文案随事实重算（只用自己的模板，永不接受上报写进来的文案）----------
    def _refresh_readable(self, after: dict[str, Any], before: Mapping[str, Any],
                          changed: Sequence[str], *, language: str) -> dict[str, Any] | None:
        """事实字段变了就按合并后的事实重算 ``human_readable``。

        口径：
        * ``human_readable`` 在 ``PROTECTED_FIELDS`` 里——上报方永远写不进文案，
          这里只允许本模块按**已落库的事实**重算，不存在"客户端改文案"的通道；
        * 只重算 l1/l3 与 ``generated_at``；上报方自己声明的补充说明（旧 l2 里
          除 intent 之外的行）原样保留，不丢信息；
        * ``may_contain_inference=true`` 保持如实：这些文字来自上报方，
          TraceLens 没有核实过（t4/t5 的口径）；
        * 没有事实字段变化时一个字都不动，避免每次上报都刷一次时间戳。
        """
        facts = {str(c.get("field")) for c in changed if isinstance(c, Mapping) and c.get("field")}
        if not facts.intersection(READABLE_FACT_FIELDS):
            return None
        old = before.get("human_readable")
        old = old if isinstance(old, Mapping) else {}
        notes = [str(x) for x in list(old.get("l2") or [])[1:]]
        after["human_readable"] = human_readable_for(
            str(after.get("id") or ""), intent=str(after.get("intent") or ""),
            action_type=str(after.get("action_type") or "tool_call"),
            status=str(after.get("status") or "running"),
            tool_used=(after.get("tool_used") or None),
            language=str(old.get("language") or language),
            extra_notes=notes)
        return after["human_readable"]

    # -- 记账（复用 t9 审计，不改审计）------------------------------------
    def audit_event(self, result: Mapping[str, Any], *, actor_id: str,
                    actor_role: str = "system", request_id: str | None = None,
                    idempotency_key: str | None = None,
                    mode: str = "local") -> dict[str, Any]:
        """把一次成功上报记成审计 ``note`` 事件（参数正文不进审计，只记数量与 id）。"""
        ids = [str(i) for i in list(result.get("ids") or [])][:20]
        token = result.get("token") if isinstance(result.get("token"), Mapping) else {}
        return audit_mod.build_event(
            "note", actor_id=_clean_id(actor_id) or "ingest", actor_role=actor_role,
            action_id=(ids[0] if ids else None),
            reason=str(result.get("note") or "实时接入批量上报"),
            request_id=request_id, idempotency_key=_clean_id(idempotency_key),
            mode="local" if mode == "local" else "live",
            client_info={"channel": str(result.get("source_kind") or "webhook")},
            note=f"ingest batch: {result.get('accepted')} accepted, "
                 f"{result.get('created')} created, {result.get('updated')} updated, "
                 f"{result.get('duplicates')} duplicates",
            extra={"ingest_event": "batch_accepted", "format": result.get("format"),
                   "accepted": result.get("accepted"), "created": result.get("created"),
                   "updated": result.get("updated"), "duplicates": result.get("duplicates"),
                   "skipped": result.get("skipped"), "ids": ids,
                   "token_id": token.get("token_id"),
                   "source_kind": result.get("source_kind"),
                   "stream_head_seq": (result.get("stream") or {}).get("head_seq")})

    def status(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self.counters)
            last = dict(self.last_batch)
        return {"enabled": self.enabled, "version": INGEST_VERSION,
                "schema": INGEST_SCHEMA_VERSION, "mode": self.mode,
                "counters": counters, "last_batch": last,
                "stream": self.hub.snapshot(),
                "tokens": (self.vault.stats() if self.vault
                           else {"storage": "none", "tokens": 0,
                                 "note": "未配置令牌保管箱（local 模式匿名写入）"}),
                "rate": {"capacity": self.limiter.capacity,
                         "per_minute": int(self.limiter.per_second * 60)},
                "limits": {"max_events": self.max_events, "max_bytes": self.max_bytes},
                "merge_fields": list(MERGE_FIELDS),
                "protected_fields": list(PROTECTED_FIELDS),
                "event_types": list(STREAM_EVENT_TYPES)}


# ---------------------------------------------------------------------------
# SDK / 手写 webhook 的事件构造器（字段封闭，过 t3 的两道闸门）
# ---------------------------------------------------------------------------

#: 动作/状态的最小中文标签：只给 L1 那一行用。解释层在 t5（``explanations.py``），
#: 这里不越界写文案，只保证记录字段封闭、能进界面。
ACTION_TYPE_LABELS: dict[str, str] = {
    "read": "读取", "write": "写入", "delete": "删除", "plan": "制定计划",
    "confirm": "请求确认", "ask": "提问", "error": "报错", "tool_call": "调用工具",
    "wait": "等待", "finish": "收尾", "intervention": "人工干预",
    "config_change": "改配置", "send": "发送", "payment": "付款",
    "export": "导出", "other": "其他动作",
}
STATUS_LABELS: dict[str, str] = {
    "observed": "已观察", "pending_confirmation": "等你确认", "approved": "已同意",
    "modified": "已改后同意", "rejected": "已拒绝", "running": "进行中",
    "succeeded": "已完成", "failed": "失败", "skipped": "已跳过", "expired": "已超时",
}
RECORD_STATUSES: tuple[str, ...] = (
    "observed", "pending_confirmation", "approved", "modified", "rejected",
    "running", "succeeded", "failed", "skipped", "expired",
)
#: t8 的迁移表：只有这两个状态能接受决定
DECIDABLE_STATUSES: tuple[str, ...] = ("pending_confirmation", "expired")
EFFECT_NOTICE = "intervention.effect.recorded_only"


def human_readable_for(action_id: str, *, intent: str, action_type: str, status: str,
                       tool_used: str | None = None, language: str = "zh",
                       extra_notes: Sequence[str] = ()) -> dict[str, Any]:
    """造 ``human_readable``（字段封闭要求它必须存在）。

    口径：l1 是「动作 + 对象 + 状态」的机械拼装；l2 直接引用上报方自己写的
    ``intent``；l3 标明来源是实时接入且**不是演示数据**。
    ``may_contain_inference`` 如实为 ``true``：这些文字来自上报方，
    不是 TraceLens 核实过的事实。
    """
    lang = language if language in ("zh", "en") else "zh"
    label = ACTION_TYPE_LABELS.get(action_type, action_type)
    status_label = STATUS_LABELS.get(status, status)
    what = _clip(tool_used or intent, 28)
    lines: list[str] = [_clip(intent, 160)]
    lines.extend(_clip(n, 160) for n in list(extra_notes)[:3])
    return {
        "l1": _clip(f"{label}：{what}（{status_label}）", ta.L1_LIMIT.get(lang, 40)),
        "l2": lines,
        "l3": _clip(f"{action_type} · {action_id} · 实时上报（webhook/SDK）"
                    f" · {status_label} · 非演示数据", 150),
        "language": lang,
        "generated_by": "sdk.v1",
        "template_id": "sdk.generic.v1",
        "model": None,
        "generated_at": _iso(),
        "source_facts_hash": None,
        "may_contain_inference": True,
    }


def default_intervention_options(action_id: str, *, intent: str, action_type: str,
                                 language: str = "zh",
                                 editable_fields: Sequence[Mapping[str, Any]] | None = None,
                                 risk_note: str | None = None) -> list[dict[str, Any]]:
    """要人拍板的记录必须带三条决定选项（与夹具/适配器默认口径同形）。

    没有这三条，t8 的干预面板就渲染不出按钮——所以 ``requires_action=true``
    却给不出选项，是上报方的缺陷，应当如实报错，而不是默默产出一张
    「看得见、点不动」的卡片。
    """
    rid = _clean_id(action_id)
    short = _clip(intent, 40)
    fields = [dict(f) for f in (editable_fields or [])]
    return [
        {"id": f"opt-{rid}-approve", "kind": "approve", "label": "同意执行",
         "description": f"按上报的范围执行「{short}」",
         "consequence": _clip(f"来源声明将执行「{short}」；TraceLens 只记录你的决定，"
                              f"是否生效以执行端回执为准", 300),
         "effect_notice": EFFECT_NOTICE, "editable_fields": [],
         "requires_reason": False, "risk_note": risk_note, "dangerous": False},
        {"id": f"opt-{rid}-modify", "kind": "modify", "label": "改范围后同意",
         "description": "只执行你设定的范围",
         "consequence": "只执行你设定的范围，其余保持原样并在清单标注已跳过",
         "effect_notice": EFFECT_NOTICE, "editable_fields": fields,
         "requires_reason": False,
         "risk_note": risk_note or ("来源未提供可修改字段，只能整体同意或拒绝"
                                    if not fields else None),
         "dangerous": False},
        {"id": f"opt-{rid}-reject", "kind": "reject", "label": "先不执行",
         "description": "停下来，等你决定下一步",
         "consequence": "跳过这一步，来源侧需要自行处理后续",
         "effect_notice": EFFECT_NOTICE, "editable_fields": [],
         "requires_reason": True, "risk_note": None, "dangerous": True},
    ]


def make_event(action_id: str, *, action_type: str = "tool_call",
               status: str = "running", timestamp: str | None = None,
               agent_id: str = "sdk-agent", actor_id: str = "sdk-client",
               workspace_id: str = "local", session_id: str = "sdk-session",
               parent_action_id: str | None = None,
               intent: str = "", rationale: str | None = None,
               result_summary: str | None = None, tool_used: str | None = None,
               cost: Mapping[str, Any] | None = None, latency_ms: int | None = None,
               confidence: float | None = None, risk_level: str | None = None,
               impact_scope: Mapping[str, Any] | Sequence[str] | None = None,
               requires_action: bool = False,
               requires_action_reason: str | None = None,
               editable_fields: Sequence[Mapping[str, Any]] | None = None,
               intervention_options: Sequence[Mapping[str, Any]] | None = None,
               execution_receipt: Mapping[str, Any] | None = None,
               error: Mapping[str, Any] | None = None, labels: Sequence[str] = (),
               param_names: Sequence[str] = (),
               sequence_no: int | None = None, run_id: str | None = None,
               agent_name: str | None = None, depth: int | None = None,
               trace_link: Mapping[str, Any] | None = None,
               language: str = "zh", source_kind: str = "sdk",
               source_uri: str = "local://sdk",
               policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """造一条**字段封闭**的原生 ActionRecord（SDK 与手写 webhook 都走这里）。

    两条硬闸门（都来自 t3，这里不放水）：

    - ``parse_native`` 要求 ``schema_version`` / ``id`` / ``timestamp`` / 16 类
      ``action_type`` / 状态枚举 / ``agent_id`` / ``actor_id`` / ``workspace_id`` /
      ``session_id`` / ``intent`` / ``policy`` / ``source`` 齐全；
    - ``_assert_shape`` 要求 ``REQUIRED_RECORD_KEYS`` 一个不缺，且不多出 schema 之外的键。

    所以 ``human_readable`` / ``cost`` / ``intervention_options`` 等在这里补齐，
    补齐口径全部如实标注（``generated_by="sdk.v1"``、``may_contain_inference=true``、
    ``demo_data=false``），绝不伪装成人工撰写或已核实的事实。
    """
    rid = _clean_id(action_id)
    if not rid:
        raise IngestError("INGEST_FIELD_TYPE", "action_id 不能为空", field="id")
    if action_type not in ta.ACTION_TYPES:
        raise IngestError("INGEST_FIELD_TYPE",
                          f"action_type={action_type!r} 不在 16 类枚举内",
                          field="action_type", hint="/".join(ta.ACTION_TYPES))
    if status not in RECORD_STATUSES:
        raise IngestError("INGEST_FIELD_TYPE", f"status={status!r} 不在状态枚举内",
                          field="status", hint="/".join(RECORD_STATUSES))
    intent_text = str(intent or "").strip()
    if not intent_text:
        raise IngestError("INGEST_FIELD_TYPE", "intent 不能为空（这行会直接进界面）",
                          field="intent")
    if risk_level is not None and risk_level not in ta.RISK_LEVELS:
        raise IngestError("INGEST_FIELD_TYPE",
                          f"risk_level={risk_level!r} 不在 {ta.RISK_LEVELS} 内",
                          field="risk_level")
    if confidence is not None:
        value = float(confidence)
        if not (0.0 <= value <= 1.0):
            raise IngestError("INGEST_FIELD_TYPE", "confidence 必须在 0 到 1 之间",
                              field="confidence")
        confidence = value
    if requires_action and status not in DECIDABLE_STATUSES + ("running", "observed"):
        raise IngestError("INGEST_FIELD_TYPE",
                          f"requires_action=true 时 status 不能是 {status!r}"
                          "（已结束的动作不能要人确认）", field="status",
                          hint="用 pending_confirmation（推荐）或 expired；"
                               "已完成的动作直接报 succeeded")
    effective_status = ("pending_confirmation"
                        if (requires_action and status in ("running", "observed")) else status)
    if source_kind not in ta.SOURCE_KINDS:
        raise IngestError("INGEST_FIELD_TYPE",
                          f"source_kind={source_kind!r} 不在 7 类接入方式内",
                          field="source_kind", hint="/".join(ta.SOURCE_KINDS))

    impact: Any = None
    if isinstance(impact_scope, Mapping):
        impact = dict(impact_scope)
    elif impact_scope:
        impact = {"targets": [{"kind": "resource", "ref": _clip(str(t), 200),
                               "change": "update"} for t in list(impact_scope)[:20]],
                  "money": None, "external_endpoint": None,
                  "data_categories": ["internal"], "reversible": None,
                  "summary": _clip("上报方声明的影响面清单（未由 TraceLens 核实）", 300)}
    # 有副作用但没声明影响面 → 留 null，由适配器按 C10 的保守假设补齐

    notes: list[str] = []
    if confidence is None:
        notes.append("来源未提供置信度")
    if impact is None and action_type in ta.SIDE_EFFECT_TYPES:
        notes.append("来源未提供影响面，按保守假设处理")
    options: list[dict[str, Any]] = []
    if requires_action:
        options = ([dict(o) for o in intervention_options] if intervention_options
                   else default_intervention_options(
                       rid, intent=intent_text, action_type=action_type,
                       language=language, editable_fields=editable_fields,
                       risk_note=(notes[0] if notes else None)))
        if not options:
            raise IngestError("INGEST_FIELD_TYPE",
                              "requires_action=true 必须给出 intervention_options（三条决定选项）",
                              field="intervention_options",
                              hint="留空会得到一张看得见但点不动的卡片，这是缺陷")
    merged_labels = [str(l) for l in labels]
    merged_labels += [f"param:{_clean_id(p, 40)}" for p in param_names]

    event: dict[str, Any] = {
        "schema_version": ta.SCHEMA_VERSION,
        "id": rid,
        "timestamp": timestamp or _iso(),
        "agent_id": _clean_id(agent_id, 80),
        "actor_id": _clean_id(actor_id, 80),
        "workspace_id": _clean_id(workspace_id, 80),
        "session_id": _clean_id(session_id, 80),
        "parent_action_id": _clean_id(parent_action_id, 128) or None,
        "action_type": action_type,
        "tool_used": _clip(tool_used, 120) if tool_used else None,
        "intent": _clip(intent_text, 480),
        "rationale": _clip(rationale, 480) if rationale else None,
        "result_summary": _clip(result_summary, 1000) if result_summary else None,
        "human_readable": human_readable_for(
            rid, intent=intent_text, action_type=action_type, status=effective_status,
            tool_used=tool_used, language=language, extra_notes=notes),
        "confidence": confidence,
        "cost": dict(cost) if isinstance(cost, Mapping) else None,
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "status": effective_status,
        "requires_action": bool(requires_action),
        "intervention_options": options,
        "user_intervention": None,
        "execution_receipt": dict(execution_receipt) if execution_receipt else {
            "state": "unreported", "reported_by": None, "reported_at": None,
            "detail": "来源未回报执行结果（决定 ≠ 生效）",
            "sdk_version": None, "effect_notice": EFFECT_NOTICE},
        "risk_level": risk_level or ("high" if action_type in ta.SIDE_EFFECT_TYPES else "low"),
        "policy": dict(policy) if policy else {
            "requires_human_confirmation": bool(requires_action),
            "auto_approve_forbidden": True,
            "timeout_behavior": "keep_pending",
            "queue_serial": bool(requires_action),
            "min_role_to_decide": "editor"},
        "source": {"kind": source_kind, "adapter": "native",
                   "format_version": f"{ta.SCHEMA_VERSION} via {source_kind}"[:40],
                   "source_uri": _clip(source_uri, 500)},
        "demo_data": False,
        "synthetic": False,
        "received_at": _iso(),
        "sequence_no": int(sequence_no) if isinstance(sequence_no, int) else None,
        "agent_name": _clean_id(agent_name, 80) or None,
        "depth": int(depth) if isinstance(depth, int) else None,
        "run_id": _clean_id(run_id, 128) or None,
        "requires_action_reason": (
            _clip(requires_action_reason, 300) if requires_action_reason
            else (_clip("来源把这一步标记为需要人工确认，请决定同意、修改后同意或拒绝", 300)
                  if requires_action else None)),
        "action_due_at": None,
        "uncertainty": ({"reason": _clip("；".join(notes), 300),
                         "what_to_check": _clip("核对来源原始记录，再决定是否放行", 300),
                         "affected_fields": ["confidence", "impact_scope"]} if notes else None),
        "impact_scope": impact,
        "error": dict(error) if error else None,
        "suggested_next": None,
        "context": None,
        "trace_link": dict(trace_link) if trace_link else None,
        "labels": merged_labels,
    }
    missing = [k for k in ta.REQUIRED_RECORD_KEYS if k not in event]
    extra = sorted(set(event) - set(ta.REQUIRED_RECORD_KEYS) - set(ta.OPTIONAL_RECORD_KEYS))
    if missing or extra:      # 自家构造器的闸门：这是 t14 的 bug，不能靠脏数据入库掩盖
        raise IngestError("INGEST_STORE_FAILURE",
                          f"make_event 产出的记录不符合字段封闭：缺 {missing}；多出 {extra}",
                          detail={"hint": "这是 t14 构造器的缺陷，请报告给后端，不要手工改数据"})
    return event


def make_events_batch(events: Sequence[Mapping[str, Any]], *,
                      idempotency_key: str | None = None, mode: str = "create",
                      language: str | None = None,
                      label: str | None = None) -> dict[str, Any]:
    """把若干事件包成 webhook 信封（单条也走这里，保持一种写法）。"""
    body: dict[str, Any] = {"events": [dict(e) for e in events], "mode": mode}
    if idempotency_key:
        body["idempotency_key"] = _clean_id(idempotency_key)
    if language:
        body["language"] = language
    if label:
        body["label"] = _clean_id(label)
    return body
