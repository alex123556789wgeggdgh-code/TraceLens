"""测试夹具的 HTTP 连接加固（M2-PRE 4，PM-VERDICT-R1-9 裁决② / R2-3 台账）。

为什么需要：Windows 上循环回环连接偶发 ``[WinError 10053] 你的主机中的软件中止了一个
已建立的连接``（POSIX 侧同形：ECONNABORTED / ECONNRESET / ECONNREFUSED / ETIMEDOUT）。
它是传输层的事实，不是被测代码的结论：PM 环境全绿、沙箱与开发机偶发（PM-VERDICT-R1-9
裁决②把它定为环境台账项）。

这里只做两件事，口径写死在函数名和注释里：

1. **端口就绪等待** —— 真 connect 一次才算端口可用。``server.start_background_server``
   在构造时已 ``bind`` + ``listen``（``socketserver.TCPServer.activate``），接受队列先于
   ``serve_forever`` 生效，所以这一步是把"刚建服务器就发请求"的窗口关死，不是重试。
2. **连接级瞬态一次性重试** —— 分两段，副作用口径不同：
   * 连接建立阶段（``connect()``）瞬态：请求一个字节都没发出去，重连一次**零副作用**，
     任何动词都允许；
   * 请求已发出之后连接被中止：只有幂等动词（GET/HEAD/OPTIONS/TRACE）才重放一次；
     POST / PUT / PATCH / DELETE **绝不重放**——服务端可能已经落了这条写请求，
     重放会伪造出第二次副作用。

**断言级失败绝不重试**：``assert*`` 抛的 ``AssertionError``、HTTP 4xx/5xx 状态码、
JSON 解析失败、响应形状不符，一律原样抛出——瞬态判定只认 ``OSError`` 的连接类
``winerror``/``errno`` 与连接类异常，不看结果对不对。

加固不掩盖真回归，也不假装绿：进程内发生过的瞬态重试记在 ``retry_log()``，
用例可断言、报告可数数；重放次数封顶 1 次，第二次仍瞬态就照原样抛。
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import socket
import socketserver
import threading
import time
import urllib.error

__all__ = ["TRANSIENT_WSA", "TRANSIENT_ERRNO", "IDEMPOTENT_VERBS",
           "is_transient_connection_error", "wait_port_ready", "open_connection",
           "with_connection", "urlopen_resilient", "retry_log", "reset_retry_log"]

# Winsock 的瞬态连接码：10053 主机软件中止了已建立的连接、10054 连接被重置、
# 10061 拒绝连接、10060 连接超时、10065 网络已断开。
TRANSIENT_WSA = frozenset({10053, 10054, 10061, 10060, 10065})
TRANSIENT_ERRNO = frozenset({errno.ECONNABORTED, errno.ECONNRESET,
                             errno.ECONNREFUSED, errno.ETIMEDOUT})
# 幂等动词：重放不会新增副作用（RFC 9110 §9.2 的 safe 方法口径）。
IDEMPOTENT_VERBS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

_RETRIES: list[str] = []


def retry_log() -> list[str]:
    """本进程发生过的连接级重试（一条一行，含动词、码与阶段）。"""
    return list(_RETRIES)


def reset_retry_log() -> None:
    """清空记录（用例里想数准数时用）。"""
    _RETRIES.clear()


def _note(line: str) -> None:
    _RETRIES.append(line)


def is_transient_connection_error(exc: BaseException) -> bool:
    """只认传输层瞬态：连接被中止 / 重置 / 拒绝 / 超时 / 网络断开。

    不看"结果对不对"：状态码、断言失败、解析错误一律 ``False``。
    ``urllib.error.URLError`` 包着真实原因（``exc.reason``），这里剥一层再看。
    """
    seen: list[BaseException] = []
    node: BaseException | None = exc
    while isinstance(node, BaseException) and node not in seen:
        seen.append(node)
        if isinstance(node, urllib.error.URLError) and isinstance(node.reason, BaseException):
            node = node.reason          # urllib 把真实原因包在 reason 里（reason 也可能只是个串）
            continue
        if isinstance(node, (ConnectionError, TimeoutError, socket.timeout)):
            return True
        if isinstance(node, OSError):
            if getattr(node, "winerror", None) in TRANSIENT_WSA:
                return True
            if getattr(node, "errno", None) in TRANSIENT_ERRNO:
                return True
        ctx = getattr(node, "__context__", None)
        node = ctx if isinstance(ctx, BaseException) else None
    return False


def wait_port_ready(port: int, host: str = "127.0.0.1",
                    timeout: float = 5.0, interval: float = 0.02) -> bool:
    """端口就绪等待：真的 connect 一次成功就返回 True；到点仍连不上返回 False。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.settimeout(max(0.05, min(interval * 4, 1.0)))
            try:
                probe.connect((host, int(port)))
                return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def open_connection(host: str, port: int, *, timeout: float = 15.0,
                    wait_timeout: float = 5.0, attempts: int = 2) -> http.client.HTTPConnection:
    """端口就绪等待 + 连接阶段一次性重试，返回一个已连好的 ``HTTPConnection``。

    这一层只碰 ``connect()``：请求还没发过，重连零副作用，所以任何动词都适用。
    连接建立后失败由 ``with_connection`` 按动词决定要不要重放。
    """
    if not wait_port_ready(port, host, timeout=wait_timeout):
        # 端口探不到也照样把连接交出去：让真实请求把真实错误抛出来，不猜、不吞。
        _note(f"port {host}:{port} 就绪探测超时（仍按原样发请求）")
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
        try:
            conn.connect()
            return conn
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            if not is_transient_connection_error(exc) or attempt >= max(1, attempts) - 1:
                raise
            last = exc
            _note(f"CONNECT {host}:{port} 连接级瞬态 {type(exc).__name__}"
                  f"(winerror={getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)})"
                  f" → 重连第 {attempt + 1} 次（请求未发出，零副作用）")
            time.sleep(0.025)
    raise ConnectionError(f"连接 {host}:{port} 失败：{last}")  # pragma: no cover - 循环内已抛


def with_connection(host: str, port: int, run, *, verb: str = "GET",
                    path: str = "", timeout: float = 15.0,
                    wait_timeout: float = 5.0, attempts: int = 2):
    """跑一次 ``run(conn)``；只在连接级瞬态时补一次，且重放只给幂等动词。

    ``run`` 自己负责发请求、读响应、解码返回；夹具的返回形状零改动。
    """
    method = str(verb or "GET").upper()
    replayable = method in IDEMPOTENT_VERBS
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        conn: http.client.HTTPConnection | None = None
        try:
            conn = open_connection(host, port, timeout=timeout,
                                   wait_timeout=wait_timeout, attempts=attempts)
            return run(conn)
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            if not is_transient_connection_error(exc) or attempt >= max(1, attempts) - 1:
                raise
            if not replayable:
                # 请求已经发出：非幂等动词绝不重放（服务端可能已落这条写请求）。
                _note(f"{method} {path} 连接级瞬态 {type(exc).__name__}"
                      f"(winerror={getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)})"
                      f" → 不重放（非幂等动词，服务端可能已写入）")
                raise
            _note(f"{method} {path} 连接级瞬态 {type(exc).__name__}"
                  f"(winerror={getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)})"
                  f" → 幂等动词重放第 {attempt + 1} 次")
            time.sleep(0.025)
        finally:
            if conn is not None:
                conn.close()
    raise ConnectionError(f"{method} {path} 连接失败：{last}")  # pragma: no cover - 循环内已抛


def urlopen_resilient(build, *, verb: str = "GET", path: str = "",
                      timeout: float = 15.0, attempts: int = 2):
    """``urllib.request.urlopen`` 的同口径加固：``build()`` 每次造一个新 Request。

    ``HTTPError``（有状态码的响应）不是瞬态，原样返回/抛出；只有包着连接类原因的
    ``URLError`` 才算瞬态，且重放只给幂等动词。
    """
    method = str(verb or "GET").upper()
    replayable = method in IDEMPOTENT_VERBS
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return build()
        except urllib.error.HTTPError:
            raise                       # 状态码是结论，不是瞬态
        except urllib.error.URLError as exc:
            last = exc
            if not is_transient_connection_error(exc) or attempt >= max(1, attempts) - 1:
                raise
            if not replayable:
                _note(f"{method} {path} 连接级瞬态 {type(exc.reason).__name__}"
                      f"(winerror={getattr(exc.reason, 'winerror', None) or getattr(exc.reason, 'errno', None)})"
                      f" → 不重放（非幂等动词，服务端可能已写入）")
                raise
            _note(f"{method} {path} 连接级瞬态 {type(exc.reason).__name__}"
                  f"(winerror={getattr(exc.reason, 'winerror', None) or getattr(exc.reason, 'errno', None)})"
                  f" → 幂等动词重放第 {attempt + 1} 次")
            time.sleep(0.025)
    raise ConnectionError(f"{method} {path} 连接失败：{last}")  # pragma: no cover - 循环内已抛


if __name__ == "__main__":  # 手工自检：python tests/http_fixture.py
    # 1. 瞬态判定：只认连接类，不认结论类
    abort = OSError(22, "aborted")
    abort.winerror = 10053  # type: ignore[attr-defined]
    assert is_transient_connection_error(abort), "10053 必须算瞬态"
    assert is_transient_connection_error(urllib.error.URLError(abort)), "URLError 要剥 reason"
    assert is_transient_connection_error(ConnectionResetError("reset"))
    assert not is_transient_connection_error(AssertionError("断言级失败"))
    assert not is_transient_connection_error(
        urllib.error.HTTPError("http://x", 500, "boom", {}, None))
    refused = ConnectionRefusedError()
    refused.winerror = 10061  # type: ignore[attr-defined]
    assert is_transient_connection_error(refused)
    # 2. 端口就绪：没有监听者的端口探不到，必须如实返回 False（不猜、不吞）
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    live_port = probe.getsockname()[1]
    probe.close()
    assert wait_port_ready(live_port, timeout=0.5) is False, "已关闭的端口不该探通"
    # 3. 端口就绪 + 连接阶段：真起一个测试服务器（和夹具同款守护线程），必须连得上
    class _Quiet(socketserver.BaseRequestHandler):
        def handle(self):
            with contextlib.suppress(OSError):
                self.request.recv(1024)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Quiet)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert wait_port_ready(port, timeout=1.0) is True, "在听的端口必须探通"
        conn = open_connection("127.0.0.1", port, timeout=2.0)
        conn.close()
        # 4. 非幂等动词绝不重放：请求已发出后中止 → 一次都不重放，原样抛
        reset_retry_log()
        calls = {"n": 0}

        def boom(_conn):
            calls["n"] += 1
            raise ConnectionAbortedError(22, "aborted")

        try:
            with_connection("127.0.0.1", port, boom, verb="POST", path="/api/intervene",
                            wait_timeout=0.2)
            raise AssertionError("POST 的瞬态中止必须原样抛出")
        except ConnectionAbortedError:
            pass
        assert calls["n"] == 1, f"非幂等动词被重放了：{calls['n']} 次"
        # 5. 幂等动词重放一次，且只一次
        reset_retry_log()
        calls = {"n": 0}

        def abort_once(_conn):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionAbortedError(22, "aborted")
            return "ok"

        got = with_connection("127.0.0.1", port, abort_once, verb="GET", path="/api/records",
                              wait_timeout=0.2)
        assert got == "ok" and calls["n"] == 2, f"GET 应重放恰好一次：{calls}"
        replays = retry_log()
        assert len(replays) >= 1, "重放必须留痕"
        # 6. 结论类失败不碰重试：断言失败原样穿出，一条重试都不记
        reset_retry_log()

        def assert_fails(_conn):
            raise AssertionError("断言级失败绝不重试")

        try:
            with_connection("127.0.0.1", port, assert_fails, verb="GET", path="/api/records",
                            wait_timeout=0.2)
            raise AssertionError("断言失败必须原样抛出")
        except AssertionError as exc:
            assert "断言级失败" in str(exc)
        assert retry_log() == [], f"断言级失败不该留重试痕迹：{retry_log()}"
    finally:
        srv.shutdown()
        srv.server_close()
    print("http_fixture self-check ok；本次记录的重试：")
    for line in retry_log():
        print("  -", line)
    print("OK")
