"""记录持久化：JSONL 日志 + 内存索引（子任务 4.9 / t22）。

为什么是 JSONL 而不是 SQLite
--------------------------
这个项目整条证据链都是追加式 JSONL（t9 的审计链就是这个格式），记录存储跟着同一口径：

* 普通人能直接打开看：一行一条记录，记事本就行，不需要 sqlite3 客户端；
* 崩溃友好：写一半的最后一行装载时跳过并如实报告，前面的记录一条不丢；
* 零依赖：标准库就有文件锁（Windows ``msvcrt`` / POSIX ``fcntl``），不需要装驱动；
* 够用：本地演示与团队小部署的记录量级是几百到几万条，内存索引完全够。

数据落在哪里
------------
默认 ``var/records.jsonl``（产品入口 ``main()`` 给这个默认值）。``team`` 模式部署时把
``var/`` 挂成卷，多个进程/多次重启读同一份文件；写入前拿独占文件锁，日志按行追加，
压实（compaction）用「写临时文件 + :func:`os.replace` 原子替换」，不存在"清空后崩在中间"
这种把记录全丢掉的状态。

记下来的东西
------------
每条记录一行 ``{"op": "put", "id": ..., "ts": ..., "record": {...}}``；
装载 = 按行回放，**最后写入胜出**。记录里的 ``user_intervention`` 是用户做过决定的事实，
QA 5.1 发现的缺口正是它活不过重启：这里它必须活下来。演示数据自带的
``demo_data`` / ``synthetic`` / ``source.kind`` 字段原样保留，界面上的"演示数据"徽标
仍按这三个字段判定，导入进来的记录不会被误标成演示数据。

口径边界
--------
* 只做记录存储：审计链在 ``audit.py``，本文件不写审计，也不改审计；
* 不删除历史：``op="clear"`` 是「换一批记录」的原子动作（``/api/reset`` 用），
  它把日志压实成新种子，不是把用户数据静默清空——调用方必须在审计里留一条；
* 没有网络：本文件不读写任何 URL，纯本地文件。
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

__all__ = [
    "STORE_VERSION",
    "RECORD_FILE_NAME",
    "OPS",
    "MAX_LINE_BYTES",
    "RecordStoreError",
    "default_store_path",
    "JournalLoad",
    "RecordJournal",
    "open_journal",
]

#: 存储格式版本：写进首行，将来换格式时能认出旧文件
STORE_VERSION = "1.1"
RECORD_FILE_NAME = "records.jsonl"
#: 允许的操作：put 写入一条；clear 换一批（压实成新种子）；header 文件头
OPS = ("put", "clear", "header")
#: 单行上限：一行就是一条完整记录，超过这个尺寸说明有人拿它当日志倒垃圾
MAX_LINE_BYTES = 512 * 1024
#: 拿锁的等待预算（秒）：本地文件锁不会长挂，超了就是真出问题
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.01

_HEADER_KIND = "header"


class RecordStoreError(Exception):
    """记录存储的可读错误：机器码 + 人话原因 + 取证细节。"""

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
            payload["detail"] = dict(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


def default_store_path(mode: str = "local", root: pathlib.Path | str | None = None) -> pathlib.Path:
    """默认数据目录：仓库/部署根下的 ``var/records.jsonl``。

    ``team`` 与 ``local`` 都在 ``var/`` 下：团队部署把 ``var/`` 挂卷即共享同一份记录，
    本地模式则让"重启后还在"这句话在双击运行的场景里也成立。测试与库调用方一律显式
    传自己的临时路径，绝不写仓库默认位置。
    """
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parents[1]
    return base / "var" / RECORD_FILE_NAME


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_stamp() -> str:
    # 与 audit.py 同一个 ISO-8601 UTC 口径，但本模块不 import audit（职责独立）
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# 文件锁：Windows 用 msvcrt，POSIX 用 fcntl，都不可用时退回进程内锁并如实报告
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - 平台分支
    import msvcrt as _msvcrt
except Exception:  # pragma: no cover - 非 Windows
    _msvcrt = None  # type: ignore[assignment]

try:  # pragma: no cover - 平台分支
    import fcntl as _fcntl
except Exception:  # pragma: no cover - 非 POSIX
    _fcntl = None  # type: ignore[assignment]

_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(key: str) -> threading.RLock:
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


class _FileLock:
    """跨进程独占锁（锁旁车文件 ``<记录文件>.lock``）。

    为什么锁旁车文件而不去锁记录文件本身：追加写与压实替换会换掉记录文件的句柄，
    锁在旁车上，锁的寿命与文件替换互不干扰。拿不到锁就轮询到预算耗尽，
    然后抛 :class:`RecordStoreError`（说清锁被谁占着、等了多久），绝不静默跳过写入。
    """

    def __init__(self, target: pathlib.Path, *, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.target = pathlib.Path(target)
        self.lock_path = pathlib.Path(str(self.target) + ".lock")
        self.timeout = float(timeout)
        self._handle: Any = None
        self._process_lock = _process_lock(str(self.lock_path.resolve()))
        self.held_ms = 0.0

    def _acquire_os_lock(self, handle: Any) -> bool:
        if _msvcrt is not None:  # pragma: no cover - Windows 分支
            handle.seek(0)
            try:
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        if _fcntl is not None:  # pragma: no cover - POSIX 分支
            try:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError:
                return False
            return True
        return True                                   # 两者都没有：只保留进程内锁

    def _release_os_lock(self, handle: Any) -> None:
        try:
            if _msvcrt is not None:  # pragma: no cover - Windows 分支
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
            elif _fcntl is not None:  # pragma: no cover - POSIX 分支
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass                                      # 句柄即将关闭，锁随之释放

    def __enter__(self) -> "_FileLock":
        self.target.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        self._process_lock.acquire()
        started = time.monotonic()
        while True:
            handle = open(self.lock_path, "a+b")      # noqa: SIM115 - 成功时由 self 持有
            if self._acquire_os_lock(handle):
                self._handle = handle
                self.held_ms = round((time.monotonic() - started) * 1000.0, 2)
                return self
            handle.close()
            if time.monotonic() >= deadline:
                self._process_lock.release()
                raise RecordStoreError(
                    "RECORD_STORE_LOCKED",
                    f"记录文件被另一个进程占着，等了 {round(self.timeout, 2)} 秒还没拿到锁",
                    detail={"path": str(self.lock_path), "timeout_seconds": self.timeout},
                    hint="确认没有第二个服务实例在写同一份记录；不要删 .lock 旁车文件")
            time.sleep(LOCK_POLL_SECONDS)

    def __exit__(self, *exc_info: Any) -> None:
        if self._handle is not None:
            self._release_os_lock(self._handle)
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        self._process_lock.release()


# --------------------------------------------------------------------------- #
# 装载结果
# --------------------------------------------------------------------------- #

class JournalLoad:
    """一次装载的结果：记录 + 如实报告的问题（坏行不静默）。"""

    def __init__(self, records: Iterable[Mapping[str, Any]], *, problems: list[dict[str, Any]],
                 lines: int, put_lines: int, seeded: bool, bytes_: int,
                 path: pathlib.Path, existed: bool) -> None:
        self.records: list[dict[str, Any]] = [copy.deepcopy(dict(r)) for r in records]
        self.problems = problems
        self.lines = int(lines)
        self.put_lines = int(put_lines)
        self.seeded = bool(seeded)
        self.bytes = int(bytes_)
        self.path = pathlib.Path(path)
        self.existed = bool(existed)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "existed": self.existed, "lines": self.lines,
                "records": len(self.records), "put_lines": self.put_lines,
                "seeded": self.seeded, "bytes": self.bytes,
                "problems": list(self.problems), "problem_count": len(self.problems)}


# --------------------------------------------------------------------------- #
# 日志本体
# --------------------------------------------------------------------------- #

class RecordJournal:
    """追加式 JSONL 记录日志 + 原子压实。

    用法（服务端只经 ``ActionStore`` 调这三件事）::

        journal = open_journal(path)
        loaded = journal.load()          # 启动时装载（文件不存在 → existed=False, records=[]）
        journal.seed(records)            # 首次启动写入演示种子（原子压实 + 文件头）
        journal.append_put(record)       # 每次决定 / 导入 / 更新之后
        journal.replace_all(records)     # /api/reset：换一批记录（审计由调用方追加）
    """

    def __init__(self, path: pathlib.Path | str, *,
                 lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.path = pathlib.Path(path)
        self.lock_timeout = float(lock_timeout)
        self._lock = threading.RLock()
        self.appends = 0
        self.compactions = 0
        self.last_error: dict[str, Any] | None = None
        self.load_report: JournalLoad | None = None

    # -- 基本事实 ----------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def signature(self) -> tuple[int, int] | None:
        """``(mtime_ns, bytes)``：这份日志现在长什么样。

        调用方用它判断"别的进程/别的服务实例是不是写过同一份文件"——一次 ``stat``
        的代价，换来团队部署里多人共用一份记录时读得到彼此的写入。
        """
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def stat(self) -> dict[str, Any]:
        """给 /health 与 /api/records 的存储事实（不含任何记录正文）。"""
        info: dict[str, Any] = {"path": str(self.path), "exists": self.exists(),
                                "store_version": STORE_VERSION,
                                "appends": self.appends, "compactions": self.compactions}
        if self.load_report is not None:
            info["loaded_records"] = len(self.load_report.records)
            info["load_problems"] = len(self.load_report.problems)
        try:
            if self.exists():
                stat = self.path.stat()
                info["bytes"] = int(stat.st_size)
                info["lines"] = _count_lines(self.path)
        except OSError as exc:  # pragma: no cover - 极端文件系统故障
            info["stat_error"] = f"{type(exc).__name__}: {exc}"
        return info

    # -- 装载 --------------------------------------------------------------
    def load(self) -> JournalLoad:
        """按行回放；**最后写入胜出**。坏行跳过并记进 ``problems``。"""
        with self._lock:
            existed = self.exists()
            records: "dict[str, dict[str, Any]]" = {}
            problems: list[dict[str, Any]] = []
            lines = 0
            put_lines = 0
            seeded = False
            bytes_ = 0
            if existed:
                bytes_ = int(self.path.stat().st_size)
                with open(self.path, "rb") as handle:
                    for raw_index, raw in enumerate(handle, start=1):
                        lines += 1
                        text = raw.decode("utf-8", "replace").strip()
                        if not text:
                            continue
                        try:
                            entry = json.loads(text)
                        except ValueError as exc:
                            last = raw_index == lines and not raw.endswith(b"\n")
                            problems.append({
                                "line": raw_index,
                                "code": "RECORD_STORE_TAIL_TRUNCATED" if last
                                        else "RECORD_STORE_LINE_INVALID",
                                "reason": ("最后一行不完整（断电/强杀进程的典型现场），"
                                           "已跳过这一行，前面的记录照常装载"
                                           if last else f"第 {raw_index} 行不是合法 JSON，已跳过"),
                                "detail": {"error": str(exc)[:160],
                                           "bytes": len(raw)},
                            })
                            continue
                        if not isinstance(entry, Mapping):
                            problems.append({"line": raw_index, "code": "RECORD_STORE_LINE_INVALID",
                                             "reason": f"第 {raw_index} 行不是对象，已跳过",
                                             "detail": {"type": type(entry).__name__}})
                            continue
                        kind = str(entry.get("kind") or "")
                        if kind == _HEADER_KIND:
                            seeded = True
                            continue
                        op = str(entry.get("op") or "")
                        if op == "clear":
                            records.clear()
                            continue
                        if op != "put":
                            problems.append({"line": raw_index,
                                             "code": "RECORD_STORE_OP_UNKNOWN",
                                             "reason": f"第 {raw_index} 行的 op={op!r} 不认识，已跳过",
                                             "detail": {"allowed": list(OPS)}})
                            continue
                        record = entry.get("record")
                        if not isinstance(record, Mapping) or not record.get("id"):
                            problems.append({"line": raw_index,
                                             "code": "RECORD_STORE_RECORD_INVALID",
                                             "reason": f"第 {raw_index} 行没有完整记录（缺 id），已跳过",
                                             "detail": {}})
                            continue
                        records[str(record["id"])] = copy.deepcopy(dict(record))
                        put_lines += 1
            self.load_report = JournalLoad(records.values(), problems=problems, lines=lines,
                                           put_lines=put_lines, seeded=seeded, bytes_=bytes_,
                                           path=self.path, existed=existed)
            return self.load_report

    # -- 写入 --------------------------------------------------------------
    def append_put(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """追加一条记录（锁内 append + flush + fsync）。"""
        if not isinstance(record, Mapping) or not record.get("id"):
            raise RecordStoreError("RECORD_STORE_RECORD_INVALID",
                                   "写入的记录缺少 id，拒绝写入",
                                   detail={"keys": sorted(str(k) for k in record)[:20]
                                           if isinstance(record, Mapping) else []})
        return self._append({"op": "put", "id": str(record["id"]), "ts": _utc_stamp(),
                             "record": copy.deepcopy(dict(record))})

    def append_clear(self, *, reason: str = "") -> dict[str, Any]:
        """追加一条 clear：语义上"这批记录作废"，由调用方紧接着压实。"""
        return self._append({"op": "clear", "ts": _utc_stamp(),
                             "reason": str(reason)[:200]})

    def _append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        line = _canonical(entry)
        size = len(line.encode("utf-8"))
        if size > MAX_LINE_BYTES:
            raise RecordStoreError("RECORD_STORE_LINE_TOO_LARGE",
                                   f"单条记录 {size} 字节，超过单行上限 {MAX_LINE_BYTES}",
                                   detail={"bytes": size, "limit": MAX_LINE_BYTES,
                                           "id": entry.get("id")})
        with self._lock:
            with _FileLock(self.path, timeout=self.lock_timeout):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                prefix = b""
                if self.exists() and not _ends_with_newline(self.path):
                    # 上一行是被强杀/断电截断的半行：先补一个换行把它封口，
                    # 让它作为"坏行"被报告，后面每行才还能逐行解析
                    prefix = b"\n"
                with open(self.path, "ab" if self.exists() else "wb") as handle:
                    handle.write(prefix + line.encode("utf-8") + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            self.appends += 1
            self.last_error = None
            return {"ok": True, "op": str(entry.get("op") or ""), "bytes": size,
                    "sealed_partial_line": bool(prefix), "path": str(self.path)}

    def seed(self, records: Iterable[Mapping[str, Any]], *, reason: str = "seed") -> dict[str, Any]:
        """首次启动写种子：文件头 + 全量记录（原子替换）。"""
        return self.replace_all(records, header={"seeded_at": _utc_stamp(), "reason": reason[:200]})

    def replace_all(self, records: Iterable[Mapping[str, Any]], *,
                    header: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """全量替换（压实）：临时文件写完 + fsync + :func:`os.replace` 原子换名。

        绝不"先清空再逐条写"——那样中途崩溃会把记录全丢掉。
        """
        rows: list[str] = []
        head: dict[str, Any] = {"kind": _HEADER_KIND, "store_version": STORE_VERSION,
                                "written_at": _utc_stamp()}
        if header:
            head.update({str(k): _clip(v) for k, v in dict(header).items()})
        rows.append(_canonical(head))
        count = 0
        for record in records:
            if not isinstance(record, Mapping) or not record.get("id"):
                continue
            rows.append(_canonical({"op": "put", "id": str(record["id"]),
                                    "ts": _utc_stamp(),
                                    "record": copy.deepcopy(dict(record))}))
            count += 1
        blob = ("\n".join(rows) + "\n").encode("utf-8")
        with self._lock:
            with _FileLock(self.path, timeout=self.lock_timeout):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = tempfile.NamedTemporaryFile(
                    dir=str(self.path.parent), prefix=self.path.name + ".",
                    suffix=".tmp", delete=False)
                tmp = pathlib.Path(handle.name)
                try:
                    with handle as sink:
                        sink.write(blob)
                        sink.flush()
                        os.fsync(sink.fileno())
                    os.replace(tmp, self.path)       # 原子替换：旧文件一直在，直到这一行
                except OSError:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
            self.compactions += 1
            self.last_error = None
            return {"ok": True, "records": count, "bytes": len(blob), "path": str(self.path)}

    # -- 供测试与运维：整份文件的行数（不解析）------------------------------
    def tail_lines(self, limit: int = 5) -> list[str]:
        """文件尾部几行（截断到 160 字符）：给自检和取证用，不含密钥字段解析。"""
        if not self.exists():
            return []
        with open(self.path, "rb") as handle:
            raw = handle.read()
        rows = [line.decode("utf-8", "replace")[:160] for line in raw.splitlines() if line]
        return rows[-int(limit):]


def _clip(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:limit]


def _count_lines(path: pathlib.Path) -> int:
    total = 0
    with open(path, "rb") as handle:
        for line in handle:
            if line.strip():
                total += 1
    return total


def _ends_with_newline(path: pathlib.Path) -> bool:
    """空文件算"以换行结尾"：不需要封口。"""
    try:
        size = path.stat().st_size
        if size == 0:
            return True
        with open(path, "rb") as handle:
            handle.seek(size - 1)
            return handle.read(1) == b"\n"
    except OSError:
        return True


def open_journal(path: pathlib.Path | str, *,
                 lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> RecordJournal:
    """构造日志对象（不碰磁盘；装载由调用方显式 :meth:`RecordJournal.load`）。"""
    return RecordJournal(path, lock_timeout=lock_timeout)


# --------------------------------------------------------------------------- #
# 自检入口：python -m agent_explainer.store
# --------------------------------------------------------------------------- #

def _self_check() -> int:  # pragma: no cover - 人工自检
    import shutil

    # 自检写在自己的临时目录：给了 TRACELENS_STORE_SELFCHECK_DIR 就用它（沙箱里
    # 系统临时目录可能不可写，测试与 CI 用这个开关指向自己的工作目录）
    override = os.environ.get("TRACELENS_STORE_SELFCHECK_DIR")
    workspace = (pathlib.Path(override) if override
                 else pathlib.Path(tempfile.gettempdir()) / "tracelens-store-selfcheck")
    shutil.rmtree(workspace, ignore_errors=True)
    journal = open_journal(workspace / RECORD_FILE_NAME)
    journal.seed([{"id": "a1", "status": "pending", "demo_data": True},
                  {"id": "a2", "status": "running", "synthetic": True}])
    journal.append_put({"id": "a1", "status": "done", "demo_data": True,
                        "user_intervention": {"decision": "approve"}})
    loaded = journal.load()
    assert len(loaded.records) == 2, loaded.as_dict()
    assert loaded.records[0]["user_intervention"]["decision"] == "approve"
    # 断电现场：最后一行被截断
    with open(journal.path, "ab") as handle:
        handle.write(b'{"op": "put", "id": "a3", "record": {"id": "a3"')
    broken = journal.load()
    assert len(broken.records) == 2 and broken.problems, broken.as_dict()
    assert broken.problems[0]["code"] == "RECORD_STORE_TAIL_TRUNCATED", broken.problems
    journal.append_clear(reason="selfcheck")
    assert journal.load().records == [], journal.load().as_dict()
    shutil.rmtree(workspace, ignore_errors=True)
    print(json.dumps({"ok": True, "store_version": STORE_VERSION,
                      "path": str(journal.path), "load": loaded.as_dict(),
                      "broken_load": broken.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(_self_check())
