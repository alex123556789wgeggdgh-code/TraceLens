"""拓展功能的追加式存储：总裁办账本 + 八大功能的实体日志（纯标准库）。

为什么要单独一个存储模块
-----------------------
既有的两套落盘各有明确职责，不能拿来塞新实体：

* ``store.py`` 的 ``RecordJournal`` 是 **ActionRecord 的唯一事实源**（整条读、整条写、
  启动全量装载）；往里塞 TaskGroup / 分享邀请 / 改进建议会让"记录"这个概念失去边界。
* ``audit.py`` 的审计链是 **取证用的**：只记"谁做了什么决定"，不承载业务对象，
  而且它的白名单事件（``decision`` / ``export`` / ``note``…）不该被业务表结构绑架。

总裁办与八大扩展功能需要的是"若干个很小的、以 id 为键的实体集合"：
Agent 身份、委托记录、责任认定书、任务组、分享邀请、改进建议、用户偏好、导出记录、
协作事件。它们的使用形态与 ``store.py`` 完全同构（整条读 / 整条写 / 启动全量装载），
所以本模块把同一套纪律抽出来复用，而不是每加一个功能就造一套半吊子文件读写：

* **追加式 JSONL**：一行一个 ``{"op": ..., "ts": ..., ...}``；崩溃留下的半行只坏一行；
* **同 id 最后写入胜出**：装载 = 按行回放，``put`` 覆盖、``append`` 只追加；
* **跨进程文件锁**：Windows ``msvcrt`` / POSIX ``fcntl``，锁旁车文件（``*.lock``）；
* **压实用原子替换**：临时文件写完 + ``fsync`` + ``os.replace``，不存在"清空后崩在中间"；
* **不删历史**：本模块**不提供** ``delete``。"删号等于毁证"是产品纪律（``auth.py`` 只禁用
  不删除），这些账本同理——撤销用状态字段（``revoked_at`` / ``status``），不抹行。

账本（:class:`Ledger`）
-----------------------
身份注册表、委托链、责任认定书索引、协作事件、导出记录这五类需要"不可篡改的追加式账本"
语义（``docs/07-secretariat-and-extensions.md`` §3）。:class:`Ledger` 在每次追加时写入
``seq`` / ``prev_hash`` / ``entry_hash``，``verify()`` 能离线复算整条链——断链如实报告，
**绝不自动清空或重建**（与审计链同一口径：篡改必须报错，不能"修好再继续"）。
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - 平台分支
    import msvcrt as _msvcrt
except Exception:  # pragma: no cover - 非 Windows
    _msvcrt = None  # type: ignore[assignment]

try:  # pragma: no cover - 平台分支
    import fcntl as _fcntl
except Exception:  # pragma: no cover - 非 POSIX
    _fcntl = None  # type: ignore[assignment]

__all__ = [
    "FEATURE_STORE_VERSION",
    "FEATURE_ROOT_DIR",
    "GENESIS_HASH",
    "MAX_LINE_BYTES",
    "LOCK_TIMEOUT_SECONDS",
    "OPS",
    "FeatureStoreError",
    "utc_stamp",
    "new_id",
    "canonical_json",
    "sha256_hex",
    "hash_object",
    "default_feature_root",
    "FeaturePaths",
    "FeatureStore",
    "Ledger",
    "open_store",
    "open_ledger",
]

#: 存储格式版本：写进每份文件的头行，将来换格式时能认出旧文件
FEATURE_STORE_VERSION = "1.0"
#: 数据目录下的子目录名（``var/features/``）
FEATURE_ROOT_DIR = "features"
#: 链头常量：第一条账本条目的 ``prev_hash``
GENESIS_HASH = "0" * 64
#: 单行上限：一行就是一个实体，超过这个尺寸说明有人拿它当日志倒垃圾
MAX_LINE_BYTES = 256 * 1024
#: 拿锁的等待预算（秒）
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.01
#: 允许的操作：header 文件头；put 同 id 覆盖；append 只追加；clear 压实成新集合
OPS = ("header", "put", "append", "clear")

_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(key: str) -> threading.RLock:
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


class FeatureStoreError(Exception):
    """可读错误：机器码 + 人话原因 + 取证细节（与 ``store.RecordStoreError`` 同形）。"""

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


# --------------------------------------------------------------------------- #
# 小工具：时间 / id / 规范 JSON / 哈希
# --------------------------------------------------------------------------- #

def utc_stamp(moment: float | None = None) -> str:
    """ISO-8601 UTC 秒级时间戳（与 ``audit.py`` 同一口径）。"""
    import datetime as _dt

    when = _dt.datetime.fromtimestamp(moment, _dt.timezone.utc) if moment else \
        _dt.datetime.now(_dt.timezone.utc)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(obj: Any) -> str:
    """规范 JSON（键排序 + 紧凑分隔符 + 不转义非 ASCII）。

    该函数存在的唯一理由：**签名覆盖的是字节**。同一份对象在不同实现里
    序列化出不同字节，签名就会互相不认。规范序列化是签名前的强前置条件
    （``docs/07-secretariat-and-extensions.md`` §2.2）。
    """
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FeatureStoreError("FEATURE_JSON_INVALID", f"对象无法规范序列化：{exc}",
                                hint="签名前先确认字段里没有 NaN/Infinity/自定义对象") from exc


def sha256_hex(data: Any) -> str:
    """sha256 十六进制摘要；``str`` 按 UTF-8 编码，其余按字节。"""
    import hashlib

    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:
        raw = canonical_json(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hash_object(obj: Any) -> str:
    """对象级哈希 = ``sha256_hex(canonical_json(obj))``。"""
    return sha256_hex(canonical_json(obj))


_ID_ALPHABET = "0123456789abcdef"


def new_id(prefix: str, *, moment: float | None = None, rand: bytes | None = None) -> str:
    """稳定可读的 id：``<prefix>_<UTC yyyymmddThhmmss>_<8 位十六进制>``。

    为什么要可读：取证时"这条记录是哪一秒产生的"直接看 id 就有，不必先去查库；
    后半段随机位保证同一秒内并发创建不会撞号。
    """
    import secrets

    stamp = utc_stamp(moment).replace("-", "").replace(":", "").replace("Z", "").replace("T", "")
    tail = (rand if rand is not None else secrets.token_bytes(4)).hex()
    tail = "".join(ch for ch in tail.lower() if ch in _ID_ALPHABET)[:8].ljust(8, "0")
    safe_prefix = "".join(ch for ch in str(prefix).lower() if ch in "abcdefghijklmnopqrstuvwxyz0123456789_") or "item"
    return f"{safe_prefix}_{stamp}_{tail}"


# --------------------------------------------------------------------------- #
# 数据目录布局
# --------------------------------------------------------------------------- #

def default_feature_root(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """默认特性数据目录：``<仓库/部署根>/var/features``。

    与 ``store.default_store_path`` 同口径：数据库调用方与测试一律显式传临时路径，
    绝不写仓库默认位置。
    """
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parents[1]
    return base / "var" / FEATURE_ROOT_DIR


class FeaturePaths:
    """``var/features/`` 下的路径清单（唯一来源，模块之间不自己拼路径）。

    布局::

        var/features/
          secretariat/
            identities.jsonl      Agent 身份注册表（Ledger）
            delegations.jsonl     委托链（Ledger）
            letters.jsonl         责任认定书索引（Ledger）
            letters/<id>.json     认定书正文
            keyring.json          Agent/总裁办私钥保管箱（chmod 0600，绝不进 API）
          groups.jsonl            多 Agent 任务组
          shares.jsonl            分享邀请
          collab_events.jsonl     协作操作审计事件（Ledger）
          suggestions.jsonl       自动化改进建议
          preferences.jsonl       用户偏好
          exports.jsonl           合规导出记录（Ledger）
          evidence/
            index.jsonl           多媒体证据索引
            files/<id>.<ext>      证据文件本体
          rollback/
            journal.jsonl         可逆操作的撤销日志
          analytics/
            agg_daily.jsonl       仪表盘预聚合账本（追加式哈希链，R6-1）
          anchors.jsonl           区块链存证锚点
    """

    def __init__(self, root: pathlib.Path | str | None = None) -> None:
        self.root = pathlib.Path(root) if root else default_feature_root()

    # -- 目录 -------------------------------------------------------------
    def ensure(self) -> None:
        for path in (self.root, self.secretariat_dir, self.letters_dir,
                     self.evidence_dir, self.evidence_files_dir, self.rollback_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def secretariat_dir(self) -> pathlib.Path:
        return self.root / "secretariat"

    @property
    def letters_dir(self) -> pathlib.Path:
        return self.secretariat_dir / "letters"

    @property
    def evidence_dir(self) -> pathlib.Path:
        return self.root / "evidence"

    @property
    def evidence_files_dir(self) -> pathlib.Path:
        return self.evidence_dir / "files"

    @property
    def rollback_dir(self) -> pathlib.Path:
        return self.root / "rollback"

    # -- 文件 -------------------------------------------------------------
    def file(self, name: str) -> pathlib.Path:
        return self.root / name

    @property
    def identities(self) -> pathlib.Path:
        return self.secretariat_dir / "identities.jsonl"

    @property
    def delegations(self) -> pathlib.Path:
        return self.secretariat_dir / "delegations.jsonl"

    @property
    def letters_index(self) -> pathlib.Path:
        return self.secretariat_dir / "letters.jsonl"

    @property
    def keyring(self) -> pathlib.Path:
        return self.secretariat_dir / "keyring.json"

    @property
    def groups(self) -> pathlib.Path:
        return self.root / "groups.jsonl"

    @property
    def shares(self) -> pathlib.Path:
        return self.root / "shares.jsonl"

    @property
    def collab_events(self) -> pathlib.Path:
        return self.root / "collab_events.jsonl"

    @property
    def suggestions(self) -> pathlib.Path:
        return self.root / "suggestions.jsonl"

    @property
    def preferences(self) -> pathlib.Path:
        return self.root / "preferences.jsonl"

    @property
    def exports(self) -> pathlib.Path:
        return self.root / "exports.jsonl"

    @property
    def anchors(self) -> pathlib.Path:
        return self.root / "anchors.jsonl"

    @property
    def evidence_index(self) -> pathlib.Path:
        return self.evidence_dir / "index.jsonl"

    @property
    def rollback_journal(self) -> pathlib.Path:
        return self.rollback_dir / "journal.jsonl"

    @property
    def analytics_dir(self) -> pathlib.Path:
        return self.root / "analytics"

    @property
    def agg_ledger(self) -> pathlib.Path:
        """仪表盘预聚合账本（R6-1）：追加式哈希链 JSONL，口径见 docs/07 §11.15。

        刻意不进 :meth:`ensure`：账本在**首次追加**时才建目录与文件——读侧命中判定
        以「文件存在」为准，GET 仪表盘不该留下空目录这种副作用。
        """
        return self.analytics_dir / "agg_daily.jsonl"

    def letter_path(self, letter_id: str) -> pathlib.Path:
        return self.letters_dir / f"{letter_id}.json"

    def evidence_file(self, evidence_id: str, ext: str) -> pathlib.Path:
        return self.evidence_files_dir / f"{evidence_id}.{ext}"

    def facts(self) -> dict[str, Any]:
        return {"root": str(self.root), "exists": self.root.exists(),
                "layout": "features.v1"}


# --------------------------------------------------------------------------- #
# 跨进程锁
# --------------------------------------------------------------------------- #

class _FileLock:
    """跨进程独占锁（锁旁车文件 ``<目标文件>.lock``）。"""

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
        return True

    def _release_os_lock(self, handle: Any) -> None:
        try:
            if _msvcrt is not None:  # pragma: no cover - Windows 分支
                handle.seek(0)
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
            elif _fcntl is not None:  # pragma: no cover - POSIX 分支
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass

    def __enter__(self) -> "_FileLock":
        self.target.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        self._process_lock.acquire()
        started = time.monotonic()
        while True:
            handle = open(self.lock_path, "a+b")  # noqa: SIM115 - 成功时由 self 持有
            if self._acquire_os_lock(handle):
                self._handle = handle
                self.held_ms = round((time.monotonic() - started) * 1000.0, 2)
                return self
            handle.close()
            if time.monotonic() >= deadline:
                self._process_lock.release()
                raise FeatureStoreError(
                    "FEATURE_STORE_LOCKED",
                    f"特性数据文件被另一个进程占着，等了 {round(self.timeout, 2)} 秒还没拿到锁",
                    detail={"path": str(self.lock_path), "timeout_seconds": self.timeout},
                    hint="确认没有第二个服务实例在写同一份数据目录")
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
# 存储本体
# --------------------------------------------------------------------------- #

class FeatureStore:
    """一个实体集合的追加式 JSONL 存储（同 id 最后写入胜出）。

    用法::

        store = FeatureStore(paths.groups)
        store.load()                 # 启动装载（文件不存在 → [])
        store.put({"id": ..., ...})  # 新增/覆盖一条并落盘
        store.items()                # 当前全集（按首次出现顺序）
        store.facts()                # 供 /health 与接口如实披露落盘状态
    """

    def __init__(self, path: pathlib.Path | str, *, id_field: str = "id",
                 lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.path = pathlib.Path(path)
        self.id_field = id_field
        self.lock_timeout = float(lock_timeout)
        self._lock = threading.RLock()
        self._items: list[dict[str, Any]] = []
        self._index: dict[str, dict[str, Any]] = {}
        self._loaded_signature: tuple[int, int] | None = None
        self.problems: list[dict[str, Any]] = []
        self.write_errors = 0
        self.writes = 0
        self.last_error: dict[str, Any] | None = None

    # -- 装载 -------------------------------------------------------------
    def _signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def load(self, *, force: bool = False) -> list[dict[str, Any]]:
        """装载文件（跨进程立刻可见：签名变了就重新装载）。"""
        with self._lock:
            signature = self._signature()
            if not force and signature is not None and signature == self._loaded_signature:
                return self.items()
            items: list[dict[str, Any]] = []
            index: dict[str, dict[str, Any]] = {}
            problems: list[dict[str, Any]] = []
            lines = 0
            if self.path.exists():
                try:
                    raw = self.path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise FeatureStoreError("FEATURE_STORE_READ_FAILED",
                                            f"读不了特性数据文件：{exc}",
                                            detail={"path": str(self.path)}) from exc
                for number, line in enumerate(raw.splitlines(), start=1):
                    text = line.strip()
                    if not text:
                        continue
                    lines += 1
                    if len(text.encode("utf-8")) > MAX_LINE_BYTES:
                        problems.append({"line": number, "code": "FEATURE_STORE_LINE_TOO_LARGE",
                                         "bytes": len(text.encode("utf-8"))})
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError as exc:
                        problems.append({"line": number, "code": "FEATURE_STORE_LINE_INVALID",
                                         "reason": exc.msg, "pos": exc.pos})
                        continue
                    if not isinstance(row, dict):
                        problems.append({"line": number, "code": "FEATURE_STORE_LINE_INVALID",
                                         "reason": "not an object"})
                        continue
                    op = row.get("op")
                    if op == "header":
                        continue
                    if op == "clear":
                        items = []
                        index = {}
                        for entry in row.get("items") or []:
                            if isinstance(entry, dict):
                                self._absorb(items, index, entry)
                        continue
                    if op in ("put", "append"):
                        entity = row.get("item") if op == "put" else row.get("item", row)
                        if not isinstance(entity, dict):
                            problems.append({"line": number, "code": "FEATURE_STORE_LINE_INVALID",
                                             "reason": "item is not an object"})
                            continue
                        self._absorb(items, index, entity)
                        continue
                    problems.append({"line": number, "code": "FEATURE_STORE_OP_UNKNOWN",
                                     "op": str(op)})
            self._items = items
            self._index = index
            self.problems = problems
            self._loaded_signature = signature
            return self.items()

    @staticmethod
    def _absorb(items: list[dict[str, Any]], index: dict[str, dict[str, Any]],
                entity: dict[str, Any]) -> None:
        key = entity.get("id")
        if isinstance(key, str) and key:
            existing = index.get(key)
            if existing is not None:
                # 同 id 最后写入胜出：原位替换，保持首次出现的顺序
                try:
                    position = items.index(existing)
                except ValueError:  # pragma: no cover - 防御
                    items.append(entity)
                else:
                    copy_entity = copy.deepcopy(entity)
                    items[position] = copy_entity
                    index[key] = copy_entity
                return
        stored = copy.deepcopy(entity)
        items.append(stored)
        if isinstance(key, str) and key:
            index[key] = stored

    # -- 读 ---------------------------------------------------------------
    def items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(item) for item in self._items]

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._index.get(str(key))
            return copy.deepcopy(found) if found is not None else None

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    # -- 写 ---------------------------------------------------------------
    def _ensure_header(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append_row(self, row: Mapping[str, Any]) -> None:
        payload = canonical_json(dict(row)) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise FeatureStoreError("FEATURE_STORE_LINE_TOO_LARGE",
                                    f"单条实体超过 {MAX_LINE_BYTES} 字节，拒绝写入",
                                    detail={"bytes": len(encoded), "limit": MAX_LINE_BYTES})
        self._ensure_header()
        try:
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            self.write_errors += 1
            self.last_error = {"code": "FEATURE_STORE_WRITE_FAILED", "reason": str(exc),
                               "path": str(self.path)}
            raise FeatureStoreError("FEATURE_STORE_WRITE_FAILED",
                                    f"写不进特性数据文件：{exc}",
                                    detail={"path": str(self.path)}) from exc

    def put(self, entity: Mapping[str, Any]) -> dict[str, Any]:
        """新增或覆盖一条（同 id 最后写入胜出）并立刻落盘。"""
        if not isinstance(entity, Mapping):
            raise FeatureStoreError("FEATURE_STORE_RECORD_INVALID", "实体必须是对象")
        key = entity.get(self.id_field)
        if not isinstance(key, str) or not key.strip():
            raise FeatureStoreError("FEATURE_STORE_RECORD_INVALID",
                                    f"实体缺少数值化的 {self.id_field} 字段",
                                    field=self.id_field)
        row = {"op": "put", "ts": utc_stamp(), "item": dict(entity)}
        with self._lock, _FileLock(self.path, timeout=self.lock_timeout):
            self.load(force=True)
            self._append_row(row)
            self._absorb(self._items, self._index, dict(entity))
            self._loaded_signature = self._signature()
            self.writes += 1
            return copy.deepcopy(self._index.get(str(key), dict(entity)))

    def append(self, entity: Mapping[str, Any]) -> dict[str, Any]:
        """只追加一条（不覆盖同名 id；账本基类会覆写本方法以写链字段）。"""
        if not isinstance(entity, Mapping):
            raise FeatureStoreError("FEATURE_STORE_RECORD_INVALID", "实体必须是对象")
        row = {"op": "append", "ts": utc_stamp(), "item": dict(entity)}
        with self._lock, _FileLock(self.path, timeout=self.lock_timeout):
            self.load(force=True)
            self._append_row(row)
            self._absorb(self._items, self._index, dict(entity))
            self._loaded_signature = self._signature()
            self.writes += 1
        return dict(entity)

    def replace_all(self, entities: Iterable[Mapping[str, Any]]) -> int:
        """换一批（原子压实）：写临时文件 + fsync + os.replace。"""
        batch = [dict(item) for item in entities]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, _FileLock(self.path, timeout=self.lock_timeout):
            handle, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp",
                                                 dir=str(self.path.parent))
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(canonical_json({"op": "header", "store": FEATURE_STORE_VERSION,
                                                 "ts": utc_stamp()}) + "\n")
                    stream.write(canonical_json({"op": "clear", "ts": utc_stamp(),
                                                 "items": batch}) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self.path)
            except OSError as exc:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                self.write_errors += 1
                raise FeatureStoreError("FEATURE_STORE_WRITE_FAILED",
                                        f"压实特性数据文件失败：{exc}",
                                        detail={"path": str(self.path)}) from exc
            self.load(force=True)
        return len(batch)

    # -- 状态 -------------------------------------------------------------
    def facts(self) -> dict[str, Any]:
        with self._lock:
            return {
                "path": str(self.path),
                "exists": self.path.exists(),
                "items": len(self._items),
                "writes": self.writes,
                "write_errors": self.write_errors,
                "problems": list(self.problems),
                "problem_count": len(self.problems),
            }


class Ledger(FeatureStore):
    """不可篡改的追加式账本：每条带 ``seq`` / ``prev_hash`` / ``entry_hash``。

    口径与 ``audit.py`` 的哈希链一致，但**互相独立**：审计链回答"谁做了什么决定"，
    账本回答"某个实体在什么时刻被追加过"。``verify()`` 只报告，不修、不清、不重建。
    """

    def head(self) -> tuple[int, str]:
        """返回 ``(seq, entry_hash)``；空账本是 ``(0, GENESIS_HASH)``。"""
        with self._lock:
            self.load()
            if not self._items:
                return 0, GENESIS_HASH
            last = self._items[-1]
            seq = last.get("seq")
            entry = last.get("entry_hash")
            if not isinstance(seq, int):
                seq = len(self._items)
            if not isinstance(entry, str) or len(entry) != 64:
                entry = self._recompute_hash(last, seq)
            return int(seq), str(entry)

    @staticmethod
    def _recompute_hash(item: Mapping[str, Any], seq: int) -> str:
        body = {key: value for key, value in item.items()
                if key not in ("seq", "entry_hash")}
        body["seq"] = int(seq)
        return hash_object(body)

    def append(self, entity: Mapping[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        if not isinstance(entity, Mapping):
            raise FeatureStoreError("FEATURE_STORE_RECORD_INVALID", "实体必须是对象")
        with self._lock, _FileLock(self.path, timeout=self.lock_timeout):
            self.load(force=True)
            seq, prev = (0, GENESIS_HASH)
            if self._items:
                last = self._items[-1]
                raw_seq = last.get("seq")
                seq = int(raw_seq) if isinstance(raw_seq, int) else len(self._items)
                raw_entry = last.get("entry_hash")
                prev = str(raw_entry) if isinstance(raw_entry, str) and len(raw_entry) == 64 \
                    else self._recompute_hash(last, seq)
            entry = dict(entity)
            entry["seq"] = seq + 1
            entry["prev_hash"] = prev
            body = {key: value for key, value in entry.items() if key != "entry_hash"}
            entry["entry_hash"] = hash_object(body)
            row = {"op": "append", "ts": utc_stamp(), "item": entry}
            self._append_row(row)
            self._absorb(self._items, self._index, entry)
            self._loaded_signature = self._signature()
            self.writes += 1
        return copy.deepcopy(entry)

    def verify(self) -> dict[str, Any]:
        """离线复算整条链：返回 ``{ok, count, head, problems}``（只报告，不修）。"""
        with self._lock:
            self.load(force=True)
            problems: list[dict[str, Any]] = []
            prev_expected = GENESIS_HASH
            expected_seq = 1
            for position, item in enumerate(self._items, start=1):
                seq = item.get("seq")
                recorded = item.get("entry_hash")
                recorded_prev = item.get("prev_hash")
                if seq != expected_seq:
                    problems.append({"code": "ledger_seq_gap", "index": position,
                                     "expected": expected_seq, "found": seq})
                if recorded_prev != prev_expected:
                    problems.append({"code": "ledger_prev_mismatch", "index": position,
                                     "expected": prev_expected, "found": recorded_prev})
                body = {key: value for key, value in item.items() if key != "entry_hash"}
                recomputed = hash_object(body)
                if recorded != recomputed:
                    problems.append({"code": "ledger_hash_mismatch", "index": position,
                                     "recorded": recorded, "recomputed": recomputed})
                prev_expected = str(recorded)
                expected_seq += 1
            return {
                "ok": not problems,
                "count": len(self._items),
                "head": prev_expected,
                "problems": problems,
                "path": str(self.path),
            }


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #

def open_store(path: pathlib.Path | str, **kwargs: Any) -> FeatureStore:
    """打开一个实体集合（调用方自己决定何时 ``load()``）。"""
    return FeatureStore(path, **kwargs)


def open_ledger(path: pathlib.Path | str, **kwargs: Any) -> Ledger:
    """打开一个追加式账本（写时自动算链）。"""
    return Ledger(path, **kwargs)


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.feature_store``：临时目录里跑一遍读写 + 链校验。"""
    import sys

    with tempfile.TemporaryDirectory() as temp:
        base = pathlib.Path(temp)
        paths = FeaturePaths(base)
        paths.ensure()
        store = open_store(paths.groups)
        store.load()
        store.put({"id": "g1", "title": "demo"})
        store.put({"id": "g1", "title": "demo2"})
        store.put({"id": "g2", "title": "other"})
        facts = store.facts()
        assert facts["items"] == 2, facts
        assert store.get("g1")["title"] == "demo2"
        ledger = open_ledger(paths.identities)
        ledger.append({"id": "i1", "agent_id": "a"})
        ledger.append({"id": "i2", "agent_id": "b"})
        report = ledger.verify()
        assert report["ok"], report
        print(f"feature_store self-check ok: store={facts['items']} ledger={report['count']} "
              f"queries={store.count()} head={report['head'][:12]}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
