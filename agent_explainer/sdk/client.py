"""TraceLens Python SDK 客户端（4.4 实时接入 / t14）。

最小可用示例（真实上报到本机服务）::

    from agent_explainer.sdk import TraceLensClient, track

    client = TraceLensClient("http://127.0.0.1:8765")        # 本机 local 模式免凭证
    client.log_action("读取订单列表", action_type="tool_call", tool_used="orders.list")

    @track(client, action_type="tool_call", tool_used="orders.export")
    def export_orders(limit: int) -> int:
        ...
        return limit

    client.close()

界面侧：服务启动后打开 UI，SSE 会把 `action.created` / `action.updated` 直接推成卡片；
断线时界面自己退到**写明原因**的轮询（不是静默降级）。

口径
----
* 上报只发**事实字段**；``user_intervention`` / ``intervention_options`` /
  ``human_readable`` / ``execution_receipt`` 由服务端与解释层负责，SDK 不越权改写。
* ``@track`` 只上报参数**名**（``param:xxx``），参数值不进上报体（密钥自净）。
* 只有带 ``idempotency_key`` 的写入与只读请求会自动重试；裸 POST 失败即失败。
"""

from __future__ import annotations

import functools
import inspect
import itertools
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable, Iterator

from .. import ingest as ingest_mod
from .. import trace_adapter as ta

__all__ = ["TraceLensClient", "TraceLensFrame", "SdkTransportError", "EVENT_TYPES",
           "parse_sse_stream", "frame_action_id", "track", "summarize_result"]

EVENT_TYPES: tuple[str, ...] = tuple(ingest_mod.STREAM_EVENT_TYPES)
JSON_MIME = "application/json"
SSE_MIME = "text/event-stream"
SDK_VERSION = "sdk.v1"
IDEMPOTENCY_KEY_MAX = 200


class SdkTransportError(RuntimeError):
    """传输层/HTTP 错误：把服务端的 ``code``/``message`` 原样带出来，不编故事。"""

    def __init__(self, reason: str, *, status: int | None = None,
                 code: str | None = None, detail: Any = None,
                 retry_after: float | None = None, url: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
        self.url = url

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "reason": self.reason, "status": self.status,
                "code": self.code, "detail": self.detail,
                "retry_after": self.retry_after, "url": self.url}

    def __str__(self) -> str:
        bits = [self.reason]
        if self.code:
            bits.append(f"code={self.code}")
        if self.status:
            bits.append(f"status={self.status}")
        if self.url:
            bits.append(f"url={self.url}")
        return " · ".join(bits)


class TraceLensFrame(dict):
    """一帧 SSE：``event`` / ``id``（服务端 seq）/ ``data``。

    仍是 dict（复制、序列化、断言都按字典来），属性只是读法糖：
    ``dict`` 不会把键名变成属性，所以这里显式给出 ``data`` 与 ``id``。
    """

    @property
    def event(self) -> str:
        return str(self.get("event") or "message")

    @property
    def data(self) -> Any:
        return self.get("data")

    @property
    def record_id(self) -> str | None:
        return frame_action_id(self)

    @property
    def seq(self) -> int | None:
        raw = self.get("id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None


def _loads(raw: bytes | str) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    text = str(raw)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_sse_stream(source: Any) -> Iterator[TraceLensFrame]:
    """按 SSE 规范解帧：``event:`` + ``id:`` + 多行 ``data:``，空行是一帧的结束。

    ``source`` 是任何有 ``readline()`` 的对象（``urlopen`` 返回的响应即可）。
    注释帧（以 ``:`` 开头）与未知字段按规范忽略 —— 服务端的心跳帧就没有 ``id:``，
    浏览器重连时的游标因此仍停在最后一条真事件上。
    """
    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    readline = getattr(source, "readline", None)
    if callable(readline) is False:
        raise TypeError("parse_sse_stream needs an object with readline()")
    while True:
        raw = readline()
        if raw in (None, b"", ""):
            return                                      # 连接关闭：正常收尾
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        line = line.rstrip("\r\n")
        if line == "":
            if data_lines:
                text = "\n".join(data_lines)
                payload: Any = text
                if text[:1] in ("{", "[", '"'):
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = {"raw": text}
                frame = TraceLensFrame(event=event or "message", id=event_id, data=payload)
                yield frame
            event, event_id, data_lines = None, None, []
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value.strip()
        elif field == "id":
            event_id = value.strip()
        elif field == "data":
            data_lines.append(value)
        # 其它字段（retry / 注释）忽略：客户端不该猜服务端没说的东西


def frame_action_id(frame: Mapping[str, Any]) -> str | None:
    """从一帧里取出关联的 ActionRecord id（取不到就返回 None，不编）。"""
    data = frame.get("data") if isinstance(frame, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    record = data.get("record")
    if isinstance(record, Mapping) and record.get("id"):
        return str(record["id"])
    for key in ("action_id", "record_id"):
        if data.get(key):
            return str(data[key])
    ids = data.get("ids")
    if isinstance(ids, Sequence) and ids:
        return str(ids[0])
    return None


def summarize_result(value: Any, *, limit: int = 160) -> str:
    """把返回值压成一句人话摘要（不含参数明文，只说形状与规模）。"""
    if value is None:
        return "执行完成（无返回值）"
    if isinstance(value, bool):
        return "执行完成（返回 true）" if value else "执行完成（返回 false）"
    if isinstance(value, (int, float)):
        return f"执行完成（返回数值 {value}）"
    if isinstance(value, str):
        return f"执行完成（返回 {len(value)} 字文本）"
    if isinstance(value, Mapping):
        keys = ", ".join(str(k) for k in list(value.keys())[:4])
        return f"执行完成（返回对象，字段：{keys}）" if keys else "执行完成（返回空对象）"
    if isinstance(value, (list, tuple)):
        return f"执行完成（返回 {len(value)} 条结果）"
    return f"执行完成（返回 {type(value).__name__}）"


def _param_names(fn: Callable[..., Any], args: tuple[Any, ...],
                 kwargs: Mapping[str, Any]) -> list[str]:
    """只取参数**名**：值不进上报体（密钥自净，t9 脱敏同口径）。"""
    names: list[str] = []
    try:
        spec = inspect.signature(fn)
        bound = list(spec.parameters)
        names = [p for p in bound[:len(args)] if p not in ("self", "cls")]
    except (TypeError, ValueError):
        names = []
    names += [str(k) for k in kwargs.keys() if str(k) not in ("self", "cls")]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen[:20]


class TraceLensClient:
    """一个到 TraceLens 服务的连接：写入用 ingest 令牌，读取用会话令牌。

    :param base_url: 服务地址，如 ``http://127.0.0.1:8765``（只该写回环/内网地址）。
    :param token: **ingest 令牌**（4.4 的机器凭证）。local 模式可省略（匿名上报，
        服务端会如实标 ``anonymous``）；team 模式必须带，否则 401。
    :param session_token: 用户会话令牌（4.3 发的），用于 ``records()`` /
        ``ingest_status()`` / 令牌签发这类需要用户权限的只读与管理接口。
    :param transport: 注入的假传输 ``fn(method, path, body, headers, query) ->
        (status, payload, headers)``；测试用它，不需要真开端口。
    :param stream_transport: 注入的假流：可迭代对象（帧 dict 列表）或
        ``fn(query, headers) -> iterable``。
    """

    def __init__(self, base_url: str, token: str | None = None, *,
                 session_token: str | None = None, timeout: float = 5.0,
                 stream_timeout: float = 300.0, retries: int = 2, backoff: float = 0.4,
                 agent_id: str = "sdk-agent", actor_id: str = "sdk-client",
                 workspace_id: str = "local", session_id: str | None = None,
                 language: str = "zh", source_kind: str = "sdk",
                 transport: Callable[..., tuple[int, Any, Mapping[str, Any]]] | None = None,
                 stream_transport: Any = None,
                 user_agent: str = f"TraceLensSDK/{SDK_VERSION}") -> None:
        split = urllib.parse.urlsplit(str(base_url))
        if split.scheme not in ("http", "https") or not split.netloc:
            raise ValueError(f"base_url 需要带 scheme 与 host，收到 {base_url!r}")
        self.scheme = split.scheme
        self.netloc = split.netloc
        self.prefix = split.path.rstrip("/")
        self.token = str(token).strip() if token else None
        self.session_token = str(session_token).strip() if session_token else None
        self.timeout = float(timeout)
        self.stream_timeout = float(stream_timeout)
        self.retries = max(0, int(retries))
        self.backoff = float(backoff)
        self.agent_id = str(agent_id)
        self.actor_id = str(actor_id)
        self.workspace_id = str(workspace_id)
        self.session_id = str(session_id or f"sdk-{uuid.uuid4().hex[:10]}")
        self.language = str(language)
        self.source_kind = str(source_kind)
        self.transport = transport
        self.stream_transport = stream_transport
        self.user_agent = str(user_agent)
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._open_streams = 0
        self.closed = False
        self.sent_events = 0
        self.http_calls = 0
        self.errors: list[dict[str, Any]] = []

    # -- 基础 --------------------------------------------------------------
    def url_for(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        parts = urllib.parse.urlencode(
            {k: v for k, v in (query or {}).items() if v is not None and v != ""})
        tail = f"{self.prefix}{path}"
        base = f"{self.scheme}://{self.netloc}{tail}"
        return f"{base}?{parts}" if parts else base

    def _headers(self, *, accept: str, write: bool) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": self.user_agent}
        if write and self.token:
            # 两个头都带：专用头最明确，Bearer 兼容只写 Authorization 的框架
            headers[ingest_mod.TOKEN_HEADER] = self.token
            headers["Authorization"] = f"{ingest_mod.BEARER_PREFIX} {self.token}"
        if self.session_token:
            headers["X-TraceLens-Token"] = self.session_token
        return headers

    def next_action_id(self, prefix: str = "act-sdk") -> str:
        with self._lock:
            n = next(self._counter)
        return f"{prefix}-{n:04d}"

    def request(self, method: str, path: str, *, body: Any = None,
                accept: str = JSON_MIME, timeout: float | None = None,
                query: Mapping[str, Any] | None = None,
                allow_retry: bool | None = None) -> tuple[int, Any, dict[str, Any]]:
        """发一个请求，返回 ``(status, payload, headers)``；传输层异常抛 :class:`SdkTransportError`。"""
        if self.closed:
            raise SdkTransportError("客户端已 close()，请新建实例")
        method = method.upper()
        write = method not in ("GET", "HEAD")
        headers = self._headers(accept=accept, write=write)
        self.http_calls += 1
        if self.transport is not None:
            status, payload, resp_headers = self.transport(
                method, path, body, headers, dict(query or {}))
            return int(status), payload, dict(resp_headers or {})
        url = self.url_for(path, query)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = JSON_MIME
        # 裸 POST 不自动重试：服务端不假装知道"这条到底进了没有"，客户端也不
        attempts = self.retries + 1 if (allow_retry is True or
                                       (allow_retry is None and (
                                           not write or self._has_idempotency(body)))) else 1
        last: Exception | None = None
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=data, method=method)
            for key, value in headers.items():
                req.add_header(key, value)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    return int(resp.status), _loads(resp.read()), dict(resp.headers)
            except urllib.error.HTTPError as exc:      # 4xx/5xx 也是响应
                payload = _loads(exc.read())
                headers_out = dict(exc.headers or {})
                if int(exc.code) >= 500 and attempt + 1 < attempts:
                    time.sleep(self.backoff * (2 ** attempt))
                    continue
                return int(exc.code), payload, headers_out
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(self.backoff * (2 ** attempt))
                    continue
                raise SdkTransportError(
                    f"连不上 {url}：{type(exc).__name__}: {exc}", url=url) from exc
        raise SdkTransportError(f"重试 {attempts} 次仍失败", url=url)     # pragma: no cover

    @staticmethod
    def _has_idempotency(body: Any) -> bool:
        return isinstance(body, Mapping) and bool(str(body.get("idempotency_key") or "").strip())

    def call(self, method: str, path: str, **kw: Any) -> Any:
        """同 :meth:`request`，但 4xx/5xx 直接抛 :class:`SdkTransportError`（错误码可读）。"""
        status, payload, headers = self.request(method, path, **kw)
        if status >= 400:
            info = payload if isinstance(payload, Mapping) else {}
            err = SdkTransportError(
                str(info.get("message") or info.get("reason") or f"HTTP {status}"),
                status=status, code=str(info.get("code") or "") or None,
                detail=info.get("detail"), url=self.url_for(path, kw.get("query")))
            self.errors.append(err.as_dict())
            if self.errors:
                self.errors = self.errors[-20:]
            raise err
        return payload

    # -- 只读 --------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self.call("GET", "/health", allow_retry=True)

    def records(self, *, limit: int | None = None, status: str | None = None,
                q: str | None = None) -> list[dict[str, Any]]:
        payload = self.call("GET", "/api/records", allow_retry=True,
                            query={"limit": limit, "status": status, "q": q})
        items = payload.get("records") if isinstance(payload, Mapping) else None
        return list(items) if isinstance(items, Sequence) else []

    def ingest_status(self) -> dict[str, Any]:
        """运维视角的上报状态（需要 ``ingest.token.manage`` 权限的会话）。"""
        return self.call("GET", "/api/ingest/status", allow_retry=True)

    def issue_ingest_token(self, label: str, *, scopes: Sequence[str] | None = None,
                           ttl_seconds: int | None = None) -> dict[str, Any]:
        """签发 ingest 令牌（需 4.3 会话 + ``ingest.token.manage``）：明文只返回一次。"""
        body: dict[str, Any] = {"label": str(label)}
        if scopes is not None:
            body["scopes"] = list(scopes)
        if ttl_seconds is not None:
            body["ttl_seconds"] = int(ttl_seconds)
        return self.call("POST", "/api/ingest/tokens", body=body)

    def revoke_ingest_token(self, token_id: str) -> dict[str, Any]:
        return self.call("POST", f"/api/ingest/tokens/{token_id}/revoke", body={})

    # -- 写入（4.4 契约：POST /api/ingest/events）--------------------------
    def emit_event(self, events: Any, *, idempotency_key: str | None = None,
                   mode: str = "create", language: str | None = None,
                   strict: bool | None = None, label: str | None = None,
                   note: str | None = None,
                   source_kind: str | None = None) -> dict[str, Any]:
        """提交一条或一批原生事件。返回服务端的批处理结果（含 ``stream.head_seq``）。

        ``source_kind``（默认 ``sdk``）通过契约里的 ``format`` 字段传给服务端，
        入库后 ``source.kind`` 就是它 —— 界面能区分"这是 SDK 实时上报的"和
        "这是手工粘贴/上传的"，不靠猜。服务端只接受 7 类接入方式，写错会回退。
        """
        items = [events] if isinstance(events, Mapping) else list(events)
        if not items:
            raise ValueError("emit_event 需要至少一个事件")
        body: dict[str, Any] = {"events": items, "mode": str(mode)}
        kind = str(source_kind or self.source_kind)
        if kind in ta.SOURCE_KINDS:
            body["format"] = kind
        if idempotency_key:
            key = str(idempotency_key)
            if len(key) > IDEMPOTENCY_KEY_MAX:
                raise ValueError(f"idempotency_key 最长 {IDEMPOTENCY_KEY_MAX} 字")
            body["idempotency_key"] = key
        if language:
            body["language"] = str(language)
        if strict is not None:
            body["strict"] = bool(strict)
        if label:
            body["label"] = str(label)
        if note:
            body["note"] = str(note)
        with self._lock:
            self.sent_events += len(items)
        return self.call("POST", "/api/ingest/events", body=body)

    def log_action(self, intent: str, *, action_id: str | None = None,
                   action_type: str = "tool_call", status: str = "running",
                   tool_used: str | None = None, rationale: str | None = None,
                   result_summary: str | None = None, cost: Mapping[str, Any] | None = None,
                   latency_ms: int | None = None, confidence: float | None = 0.6,
                   risk_level: str | None = None,
                   impact_scope: Mapping[str, Any] | Sequence[str] | None = None,
                   requires_action: bool = False,
                   requires_action_reason: str | None = None,
                   editable_fields: Sequence[Mapping[str, Any]] | None = None,
                   intervention_options: Sequence[Mapping[str, Any]] | None = None,
                   error: Mapping[str, Any] | None = None,
                   labels: Sequence[str] = (), param_names: Sequence[str] = (),
                   sequence_no: int | None = None, run_id: str | None = None,
                   agent_name: str | None = None, timestamp: str | None = None,
                   idempotency_key: str | None = None,
                   mode: str = "create") -> dict[str, Any]:
        """上报"我这一步做了什么"，立刻进界面。返回 ``{ok, action_id, accepted, ...}``。"""
        rid = str(action_id) if action_id else self.next_action_id()
        event = ingest_mod.make_event(
            rid, action_type=action_type, status=status, timestamp=timestamp,
            agent_id=self.agent_id, actor_id=self.actor_id,
            workspace_id=self.workspace_id, session_id=self.session_id,
            intent=intent, rationale=rationale, result_summary=result_summary,
            tool_used=tool_used, cost=cost, latency_ms=latency_ms,
            confidence=confidence, risk_level=risk_level, impact_scope=impact_scope,
            requires_action=requires_action, requires_action_reason=requires_action_reason,
            editable_fields=editable_fields, intervention_options=intervention_options,
            error=error, labels=list(labels), param_names=list(param_names),
            sequence_no=sequence_no if sequence_no is not None else next(self._counter),
            run_id=run_id, agent_name=agent_name, language=self.language,
            source_kind=self.source_kind)
        result = self.emit_event(event, idempotency_key=idempotency_key, mode=mode)
        return {"ok": True, "action_id": rid, "accepted": result.get("accepted"),
                "created": result.get("created"), "updated": result.get("updated"),
                "duplicates": result.get("duplicates"),
                "head_seq": (result.get("stream") or {}).get("head_seq"),
                "response": result}

    def update_action(self, action_id: str, *, idempotency_key: str | None = None,
                      **fields: Any) -> dict[str, Any]:
        """按 4.4 的合并白名单更新已有记录（永不覆盖决定字段）。"""
        rid = str(action_id)
        intent = str(fields.pop("intent", "") or f"更新 {rid} 的状态")
        action_type = str(fields.pop("action_type", "tool_call"))
        event = ingest_mod.make_event(rid, action_type=action_type, intent=intent,
                                      agent_id=self.agent_id, actor_id=self.actor_id,
                                      workspace_id=self.workspace_id,
                                      session_id=self.session_id,
                                      language=self.language, source_kind=self.source_kind,
                                      **fields)
        result = self.emit_event(event, mode="update", idempotency_key=idempotency_key)
        return {"ok": True, "action_id": rid, "accepted": result.get("accepted"),
                "updated": result.get("updated"), "duplicates": result.get("duplicates"),
                "head_seq": (result.get("stream") or {}).get("head_seq"),
                # 合并了哪些字段由服务端在 action.updated 帧里给出（界面用它做高亮）；
                # 响应体只报数量，避免同一份数据在两个地方各说一遍、口径漂移。
                "note": "合并字段清单在 action.updated 帧的 data.changed_fields 里",
                "response": result}

    # -- 实时读取（GET /api/stream）----------------------------------------
    def stream_events(self, *, types: Sequence[str] | None = None,
                      since: int | None = None, last_event_id: int | None = None,
                      max_events: int | None = None,
                      heartbeat: float | None = None) -> Iterator[TraceLensFrame]:
        """迭代服务端推来的帧；``since`` 断点续传（对应 ``Last-Event-ID``）。

        ``max_events`` 是"取够就收尾"：演示与测试用它，免得脚本挂在一条长连接上
        出不来。真实界面不设这个参数，靠心跳帧判断连接是否还活着、断了就退轮询。
        """
        if self.stream_transport is not None:
            source = self.stream_transport
            if callable(source):
                source = source(dict(self._stream_query(types, since, last_event_id,
                                                        max_events, heartbeat)),
                                self._headers(accept=SSE_MIME, write=False))
            for frame in source:
                if isinstance(frame, TraceLensFrame):
                    yield frame
                elif isinstance(frame, Mapping):
                    yield TraceLensFrame(dict(frame))
                else:
                    yield TraceLensFrame(event=str(frame), id=None, data=None)
            return
        query = self._stream_query(types, since, last_event_id, max_events, heartbeat)
        url = self.url_for("/api/stream", query)
        headers = self._headers(accept=SSE_MIME, write=False)
        headers["Cache-Control"] = "no-store"
        req = urllib.request.Request(url, method="GET")
        for key, value in headers.items():
            req.add_header(key, value)
        self.http_calls += 1
        try:
            resp = urllib.request.urlopen(req, timeout=self.stream_timeout)
        except urllib.error.HTTPError as exc:
            payload = _loads(exc.read())
            info = payload if isinstance(payload, Mapping) else {}
            raise SdkTransportError(
                str(info.get("message") or f"HTTP {exc.code}"), status=int(exc.code),
                code=str(info.get("code") or "") or None, detail=info.get("detail"),
                url=url) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise SdkTransportError(f"连不上流 {url}：{exc}", url=url) from exc
        with self._lock:
            self._open_streams += 1
        frames = 0
        try:
            for frame in parse_sse_stream(resp):
                yield frame
                frames += 1
                if max_events and frames >= int(max_events):
                    break
            # 服务端每帧都 flush，所以这里没有"缓冲区憋着不发"的窗口
        except (socket.timeout, TimeoutError):
            return                       # 一段时间没帧：收尾（服务端有心跳，正常不会走到）
        except (OSError, urllib.error.URLError) as exc:
            raise SdkTransportError(f"流中断：{type(exc).__name__}: {exc}", url=url) from exc
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
            with self._lock:
                self._open_streams -= 1

    def _stream_query(self, types, since, last_event_id, max_events,
                      heartbeat) -> dict[str, Any]:
        cursor = since if since is not None else last_event_id
        joined = ",".join(str(t) for t in (types or ()))
        return {"since": cursor, "types": joined or None,
                "max_events": max_events, "heartbeat": heartbeat}

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "TraceLensClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def stats(self) -> dict[str, Any]:
        return {"ok": True, "base_url": f"{self.scheme}://{self.netloc}{self.prefix}",
                "session_id": self.session_id, "sent_events": self.sent_events,
                "http_calls": self.http_calls, "open_streams": self._open_streams,
                "token_configured": bool(self.token),
                "session_token_configured": bool(self.session_token),
                "errors": list(self.errors)}


def track(client: TraceLensClient | None = None, *, action_type: str = "tool_call",
          intent: str | None = None, tool_used: str | None = None,
          emit_running: bool = True, status_success: str = "succeeded",
          status_failure: str = "failed", confidence: float | None = 0.6,
          risk_level: str | None = None,
          idempotency_prefix: str | None = None) -> Callable[[Callable[..., Any]], Any]:
    """装饰器：函数开始/结束各上报一条事件（同一条 id，结束用 ``mode=update`` 合并）。

    参数值不进入上报体：只有参数名作为 label（``param:xxx``）出现在界面上。
    函数抛异常时上报 ``failed`` 并原样抛出 —— 装饰器不吞异常、不假装成功。
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool = str(tool_used or fn.__name__)
        label = str(intent or f"执行 {fn.__name__}")

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cli = client
            if cli is None:
                raise TypeError("@track 需要一个 TraceLensClient 实例")
            rid = cli.next_action_id(f"act-{fn.__name__[:18]}")
            # 先记账再执行：抛异常的调用也要能追到那条 failed 记录
            wrapper.trace_ids.append(rid)                    # type: ignore[attr-defined]
            wrapper.last_action_id = rid                     # type: ignore[attr-defined]
            names = _param_names(fn, args, kwargs)
            started = time.perf_counter()
            key = None
            if idempotency_prefix:
                key = f"{idempotency_prefix}-{rid}"[:IDEMPOTENCY_KEY_MAX]
            if emit_running:
                cli.log_action(label, action_id=rid, action_type=action_type,
                               status="running", tool_used=tool, param_names=names,
                               confidence=confidence, risk_level=risk_level)
            try:
                out = fn(*args, **kwargs)
            except BaseException as exc:                    # noqa: BLE001 - 上报后原样抛
                cli.update_action(rid, intent=label, action_type=action_type,
                                  status=status_failure, tool_used=tool,
                                  latency_ms=int((time.perf_counter() - started) * 1000),
                                  error={"code": type(exc).__name__,
                                         "message": str(exc)[:200]},
                                  param_names=names,
                                  idempotency_key=None if key is None else f"{key}-fail")
                raise
            cli.update_action(rid, intent=label, action_type=action_type,
                              status=status_success, tool_used=tool,
                              result_summary=summarize_result(out),
                              latency_ms=int((time.perf_counter() - started) * 1000),
                              confidence=confidence, param_names=names,
                              idempotency_key=None if key is None else f"{key}-done")
            return out

        wrapper.trace_ids = []                               # type: ignore[attr-defined]
        wrapper.trace_action_type = action_type              # type: ignore[attr-defined]
        return wrapper

    return decorator
