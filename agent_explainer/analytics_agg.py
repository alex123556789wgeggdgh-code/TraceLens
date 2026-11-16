"""R6-1（REQ-006 B6-2 / D-5 修订口径）：队长仪表盘**预聚合账本**（docs/07 §11.15）。

账本落在 ``var/features/analytics/agg_daily.jsonl``（路径唯一来源 = ``FeaturePaths.agg_ledger``），
是**追加式哈希链账本**：日粒度聚合行 + 水位线状态行，逐行 ``entry_hash`` 链式续接。

三条铁律口径（PM 裁决，写在这里防漂移）：

1. **append-only**：预聚合本身也是追加行；修正 = 追加补偿行（同键后写胜出，audit
   ``correction`` 先例）；历史行**一个字都不改**，压实不做、删除不做。
2. **哈希口径复用 ``audit`` 单点**：``canonical_json`` / ``entry_hash`` / ``GENESIS_HASH``
   全部 import 自 ``audit``——本模块不写第二套哈希。行键序 = ``canonical_json`` 排序键。
3. **写入时机 = 读侧（dashboard 调用）**：导入链/干预链零触发、零耦合。水位线是
   「记录 id → 内容摘要」的映射（状态行全量快照）：新记录增量聚合；记录被改写
   → 全量复算受影响键 → 追加补偿行。**聚合定义只有一个复算函数**
   （:func:`recompute_rows`），增量与补偿共用，不写第二套口径。

B6-2 的「≤3s」只在**预聚合命中**时承诺；未预聚合/链被篡改/参数不支持时
:func:`merge_dashboard` 回退到 :func:`analytics._stream_scan` 流式逐行扫描，并在
报告元数据里**如实标注** ``{pre_aggregated, scanned, agg.reason}``——降级可见，不装快。

B6-1 前瞻（R6-2 抽样核对）的可复算性由两件事保证：链可逐行复算（:func:`AggLedger.verify`）、
聚合定义 = ``recompute_rows`` 一个纯函数 + docs/07 §11.15 的文字定义。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import threading
from typing import Any, Iterable, Iterator, Mapping

from .analytics import (
    _assemble_metrics,
    _bucket_key,
    _decision,
    _decided_at,
    _latency_micros,
    _stream_scan,
    anonymise,
)
from .audit import GENESIS_HASH, canonical_json, entry_hash
from .feature_store import MAX_LINE_BYTES, FeatureStoreError, _FileLock, utc_stamp

__all__ = [
    "AGG_VERSION", "AGG_FILE_NAME", "KIND_HEADER", "KIND_AGG", "KIND_STATE",
    "AggLedgerError", "AggLedger", "record_digest", "record_key", "group_key",
    "record_to_deltas", "recompute_rows", "rows_to_counters", "merge_dashboard",
]

#: 聚合账本格式版本（写进头行与每行，将来换格式能认出旧文件）
AGG_VERSION = "1.0"
#: 账本文件名（``FeaturePaths.analytics_dir`` 下；路径唯一来源是 FeaturePaths）
AGG_FILE_NAME = "agg_daily.jsonl"
KIND_HEADER = "header"
KIND_AGG = "agg"
KIND_STATE = "state"
#: problems 上报封顶（坏行再多也不把元数据撑爆；计数如实保留在 problems_count）
PROBLEM_REPORT_LIMIT = 12


class AggLedgerError(Exception):
    """预聚合账本异常：机器码 + 人话原因（与 ``audit.AuditError`` 同形，不联网不进 HTTP）。"""

    def __init__(self, code: str, reason: str, *, detail: Mapping[str, Any] | None = None,
                 hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.detail: dict[str, Any] = dict(detail or {})
        self.hint = hint

    def as_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "reason": self.reason}
        if self.detail:
            out["detail"] = dict(self.detail)
        if self.hint:
            out["hint"] = self.hint
        return out


# --------------------------------------------------------------------------- #
# 单条记录 → 聚合贡献（口径与 analytics.dashboard 逐项对齐，只认记录里写下的事实）
# --------------------------------------------------------------------------- #


def record_digest(record: Mapping[str, Any]) -> str:
    """记录内容摘要：``sha256(规范 JSON)``——水位线判定"这条记录变没变"就靠它。"""
    from .audit import sha256_ref

    return sha256_ref(canonical_json(dict(record)))


def record_key(record: Mapping[str, Any]) -> str:
    """水位线的键：记录 ``id``；没有 id 的记录按内容摘要记键（整份记录库里 id 唯一）。"""
    ident = record.get("id")
    if isinstance(ident, str) and ident.strip():
        return ident
    return "digest:" + record_digest(record)[len("sha256:"):]


def group_key(date: str, tool: str, param: str, decision: str) -> str:
    """聚合行的键 = ``canonical_json([date, tool, param, decision])``（规范序列化，可复算）。"""
    return canonical_json([date, tool, param, decision])


def _tool_of(record: Mapping[str, Any]) -> str:
    return str(((record.get("tool_used") or {}).get("name")) or "—")


def record_to_deltas(record: Mapping[str, Any], *, salt: str = "") -> list[dict[str, Any]]:
    """一条记录 → 若干聚合贡献 ``{key,date,tool,param,decision,count,intervention_count,
    approval_latency_agg,users}``；时间戳解析不出来 → 空列表（与仪表盘"算不出就不计"同口径）。

    键语义（docs/07 §11.15）：``param=""`` 的行是**记录本体**行（count/干预数/批准时长/
    决定人只记在这类行上）；``param!=""`` 的行是**热力图**行（只记 count，被改了哪个
    参数改了几次），延迟/用户在热力图行上恒为 0/空——读侧只从本体行求和任务类指标，
    绝不双计。
    """
    moment = _ts_safe(record)
    if moment is None:
        return []
    date = moment.date().isoformat()
    tool = _tool_of(record)
    view = _decision(record)
    dec = str(view.get("decision") or "")
    deltas: list[dict[str, Any]] = []
    intervention = 1 if dec else 0
    users: list[str] = []
    sum_micros = 0
    n_latency = 0
    if dec:
        users.append(anonymise(view.get("decided_by") or "local", salt=salt))
        decided = _decided_at(record)
        if decided is not None and decided >= moment:
            sum_micros = _latency_micros(decided, moment)
            n_latency = 1
    deltas.append({"key": group_key(date, tool, "", dec), "date": date, "tool": tool,
                   "param": "", "decision": dec, "count": 1,
                   "intervention_count": intervention,
                   "approval_latency_agg": {"sum_micros": sum_micros, "n": n_latency},
                   "users": sorted(users)})
    for field in (view.get("modified_fields") or []):
        name = str((field or {}).get("name") or "")
        if name:
            deltas.append({"key": group_key(date, tool, name, dec), "date": date,
                           "tool": tool, "param": name, "decision": dec, "count": 1,
                           "intervention_count": 0,
                           "approval_latency_agg": {"sum_micros": 0, "n": 0},
                           "users": []})
    return deltas


def _ts_safe(record: Mapping[str, Any]):
    """与 ``analytics._ts`` 同口径的时间解析（import 复用，不写第二套）。"""
    from .analytics import _ts

    return _ts(dict(record))


# --------------------------------------------------------------------------- #
# 全量复算：聚合定义的**唯一实现**（增量、补偿、抽样核对都复算这一个函数）
# --------------------------------------------------------------------------- #


def recompute_rows(records: Iterable[Any], *, salt: str = "") -> dict[str, dict[str, Any]]:
    """流式逐行聚合全部记录 → ``{group_key: 聚合行值}``。O(1) 内存：不整载记录，
    只保留按键的计数器（键基数随 日期×工具×参数×决定 增长，与记录条数无关）。"""
    groups: dict[str, dict[str, Any]] = {}
    for record in (records or []):
        if not isinstance(record, dict):
            continue
        for delta in record_to_deltas(record, salt=salt):
            slot = groups.get(delta["key"])
            if slot is None:
                slot = {"date": delta["date"], "tool": delta["tool"],
                        "param": delta["param"], "decision": delta["decision"],
                        "count": 0, "intervention_count": 0,
                        "approval_latency_agg": {"sum_micros": 0, "n": 0},
                        "users": []}
                groups[delta["key"]] = slot
            slot["count"] += delta["count"]
            slot["intervention_count"] += delta["intervention_count"]
            agg = slot["approval_latency_agg"]
            agg["sum_micros"] += delta["approval_latency_agg"]["sum_micros"]
            agg["n"] += delta["approval_latency_agg"]["n"]
            for user in delta["users"]:
                if user not in slot["users"]:
                    slot["users"].append(user)
    for slot in groups.values():
        slot["users"] = sorted(slot["users"])
    return groups


def _row_values(row: Mapping[str, Any]) -> tuple:
    """比较两行聚合值是否相等（补偿行只追加**值真变了**的键，不刷全账本）。"""
    agg = row.get("approval_latency_agg") or {}
    return (int(row.get("count") or 0), int(row.get("intervention_count") or 0),
            int(agg.get("sum_micros") or 0), int(agg.get("n") or 0),
            sorted(str(u) for u in (row.get("users") or [])))


def _row_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """落盘行载荷（``seq/prev_hash/entry_hash`` 由账本追加时算，载荷里不出现）。"""
    return {"agg_version": AGG_VERSION, "kind": KIND_AGG,
            "date": str(values["date"]), "tool": str(values["tool"]),
            "param": str(values["param"]), "decision": str(values["decision"]),
            "count": int(values["count"]),
            "intervention_count": int(values["intervention_count"]),
            "approval_latency_agg": {
                "sum_micros": int(values["approval_latency_agg"]["sum_micros"]),
                "n": int(values["approval_latency_agg"]["n"])},
            "users": sorted(str(u) for u in (values.get("users") or [])),
            "updated_at": utc_stamp()}


# --------------------------------------------------------------------------- #
# 账本 IO：追加式哈希链（audit 口径），读侧逐行流式
# --------------------------------------------------------------------------- #


class AggLedger:
    """``agg_daily.jsonl`` 的追加式账本：行带 ``seq/prev_hash/entry_hash``，链头续接。

    哈希口径与 ``audit.py`` 完全同源：``entry_hash = sha256(prev_hash + "\\n" +
    规范 JSON(payload))``，首条 ``prev_hash`` 是 ``GENESIS_HASH``；``payload`` 含 ``seq``
    （audit 先例）。读侧逐行流式消费（``for line in handle``），坏行如实登记不静默。
    """

    def __init__(self, path: pathlib.Path | str, *,
                 lock_timeout: float = 5.0) -> None:
        self.path = pathlib.Path(path)
        self.lock_timeout = float(lock_timeout)
        self._lock = threading.RLock()

    # -- 读（逐行流式，O(1)：不整载文件） ---------------------------------- #

    def load(self) -> dict[str, Any]:
        """装载合并视图：``{exists, rows, state, head, seq, lines, problems,
        problems_count, chain_ok}``；同键后读胜出（append-only 的"修正=补偿行"读取面）。"""
        with self._lock:
            rows: dict[str, dict[str, Any]] = {}
            index: dict[str, int] = {}
            state: dict[str, Any] | None = None
            problems: list[dict[str, Any]] = []
            head = GENESIS_HASH
            seq = 0
            lines = 0
            existed = self.path.exists()
            if existed:
                try:
                    handle = open(self.path, "rb")
                except OSError as exc:
                    raise AggLedgerError("AGG_LEDGER_READ_FAILED",
                                         f"读不了预聚合账本：{exc}",
                                         detail={"path": str(self.path)}) from exc
                with handle:
                    for number, raw in enumerate(handle, start=1):   # 逐行消费，不 read_text
                        lines += 1
                        text = raw.decode("utf-8", "replace").strip()
                        if not text:
                            continue
                        try:
                            row = json.loads(text)
                        except ValueError as exc:
                            self._problem(problems, number, "AGG_LEDGER_LINE_INVALID",
                                          str(exc)[:160])
                            continue
                        if not isinstance(row, dict):
                            self._problem(problems, number, "AGG_LEDGER_LINE_INVALID",
                                          "not an object")
                            continue
                        kind = str(row.get("kind") or "")
                        if kind == KIND_HEADER:
                            continue
                        if kind not in (KIND_AGG, KIND_STATE):
                            self._problem(problems, number, "AGG_LEDGER_KIND_UNKNOWN",
                                          f"kind={kind!r}")
                            continue
                        # 链校验：prev_hash 续接 + 逐行复算 entry_hash
                        row_seq = row.get("seq")
                        recorded_prev = row.get("prev_hash")
                        recorded_hash = row.get("entry_hash")
                        if recorded_prev != head:
                            self._problem(problems, number, "AGG_LEDGER_PREV_MISMATCH",
                                          f"期望 {head}，实录 {recorded_prev}")
                        recomputed = entry_hash(row, head)
                        if recorded_hash != recomputed:
                            self._problem(problems, number, "AGG_LEDGER_HASH_MISMATCH",
                                          "逐行复算 entry_hash 不符")
                        if row_seq != seq + 1:
                            self._problem(problems, number, "AGG_LEDGER_SEQ_GAP",
                                          f"期望 seq={seq + 1}，实录 {row_seq}")
                        head = str(recorded_hash or "")
                        seq = int(row_seq) if isinstance(row_seq, int) else seq + 1
                        if kind == KIND_STATE:
                            state = row
                            continue
                        key = group_key(str(row.get("date") or ""),
                                        str(row.get("tool") or ""),
                                        str(row.get("param") or ""),
                                        str(row.get("decision") or ""))
                        if key in index:
                            rows[key] = row                       # 同键后读胜出（补偿行语义）
                        else:
                            index[key] = lines
                            rows[key] = row
                existed = True
            return {"exists": existed, "rows": rows, "state": state, "head": head,
                    "seq": seq, "lines": lines, "problems": problems,
                    "problems_count": len(problems), "chain_ok": not problems}

    @staticmethod
    def _problem(problems: list[dict[str, Any]], line: int, code: str, reason: str) -> None:
        if len(problems) < PROBLEM_REPORT_LIMIT:
            problems.append({"line": line, "code": code, "reason": reason})

    def _tail(self) -> tuple[str, int]:
        """链头 ``(prev_hash, seq)``：读文件**最后一行**的哈希；空账本 = GENESIS/0。"""
        if not self.path.exists():
            return GENESIS_HASH, 0
        last = b""
        with open(self.path, "rb") as handle:
            for raw in handle:
                text = raw.strip()
                if text:
                    last = text
        if not last:
            return GENESIS_HASH, 0
        try:
            row = json.loads(last.decode("utf-8"))
        except ValueError as exc:
            raise AggLedgerError("AGG_LEDGER_TAIL_INVALID",
                                 f"账本最后一行不是合法 JSON，链无法续接：{exc}",
                                 detail={"path": str(self.path)}) from exc
        entry = row.get("entry_hash") if isinstance(row, dict) else None
        if isinstance(row, dict) and str(row.get("kind") or "") == KIND_HEADER:
            return GENESIS_HASH, 0      # 只有头行的文件 = 链还没开始
        seq = row.get("seq") if isinstance(row, dict) else None
        if not isinstance(entry, str) or not entry:
            raise AggLedgerError("AGG_LEDGER_TAIL_INVALID",
                                 "账本最后一行没有 entry_hash，链无法续接",
                                 detail={"path": str(self.path)})
        return str(entry), (int(seq) if isinstance(seq, int) else 0)

    # -- 写（追加 + fsync；历史行永不改写） --------------------------------- #

    def append_rows(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """锁内追加一批行：逐行续接 ``seq/prev_hash/entry_hash`` 后规范序列化落盘。

        行载荷超过 ``MAX_LINE_BYTES``（状态行快照随记录数增长）→ 如实抛错，
        调用方把 ``durable:false`` 写进元数据，不装持久、不静默降级。
        """
        if not payloads:
            return []
        written: list[dict[str, Any]] = []
        with self._lock, _FileLock(self.path, timeout=self.lock_timeout):
            head, seq = self._tail()
            lines: list[str] = []
            for payload in payloads:
                if str(payload.get("kind") or "") == KIND_HEADER:
                    # 头行是格式指纹，**不参与哈希链**（链从第一条 agg/state 行起算，
                    # 读侧校验逐行跳过 header——两边口径必须一致，这里就是那一边）。
                    line = canonical_json(dict(payload))
                    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                        raise AggLedgerError("AGG_LEDGER_LINE_TOO_LARGE",
                                             "头行超限（不可能级别的事件，如实抛错）",
                                             detail={"path": str(self.path)})
                    lines.append(line)
                    continue
                entry = dict(payload)
                entry["seq"] = seq + 1
                entry["prev_hash"] = head
                digest = entry_hash(entry, head)
                entry["entry_hash"] = digest
                line = canonical_json(entry)
                if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                    raise AggLedgerError(
                        "AGG_LEDGER_LINE_TOO_LARGE",
                        f"单行 {len(line.encode('utf-8'))} 字节，超过上限 {MAX_LINE_BYTES}"
                        "（水位线状态行随记录数增长；请走 R6-2 的账本压实）",
                        detail={"path": str(self.path), "kind": str(payload.get("kind"))})
                lines.append(line)
                head, seq = digest, seq + 1
                written.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(self.path, "ab" if self.path.exists() else "wb") as handle:
                    for line in lines:
                        handle.write((line + "\n").encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise AggLedgerError("AGG_LEDGER_WRITE_FAILED",
                                     f"写不进预聚合账本：{exc}",
                                     detail={"path": str(self.path)}) from exc
        return written

    def verify(self) -> dict[str, Any]:
        """离线复算整条链：``{ok, rows, lines, head, problems}``（B6-1 复算口径，只报告不修）。"""
        snapshot = self.load()
        return {"ok": snapshot["chain_ok"], "rows": len(snapshot["rows"]),
                "lines": snapshot["lines"], "head": snapshot["head"],
                "problems": snapshot["problems"], "problems_count": snapshot["problems_count"],
                "path": str(self.path)}

    def create_header(self) -> None:
        """首次建账本写头行（格式指纹）；已存在就什么都不做。"""
        if self.path.exists():
            return
        self.append_rows([{"agg_version": AGG_VERSION, "kind": KIND_HEADER,
                           "layout": "analytics-agg.v1", "updated_at": utc_stamp()}])


def _state_payload(consumed: Mapping[str, str]) -> dict[str, Any]:
    """水位线状态行：记录键 → 内容摘要 的全量快照（同 kind 后读胜出 = 最新水位线）。"""
    return {"agg_version": AGG_VERSION, "kind": KIND_STATE,
            "consumed": {key: digest for key, digest in consumed.items()},
            "consumed_count": len(consumed), "updated_at": utc_stamp()}


def _consumed_of(state: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(state, Mapping):
        return {}
    raw = state.get("consumed")
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


# --------------------------------------------------------------------------- #
# 读侧合并：dashboard() 的预聚合优先路径（命中读账本；回退流式并如实标注）
# --------------------------------------------------------------------------- #


def rows_to_counters(rows: Mapping[str, Mapping[str, Any]], *,
                     start: _dt.date, end: _dt.date,
                     granularity: str) -> dict[str, Any]:
    """账本合并行 → 指标计数器（与 :func:`analytics._stream_scan` 的计数器同形）。

    任务类指标只从 ``param=""`` 本体行求和；热力图只从 ``param!=""`` 行求 count——
    这条读取规则与写入规则（``record_to_deltas``）是一体两面，docs/07 §11.15 写死。
    """
    tasks = 0
    interventions = 0
    approved = 0
    rejected = 0
    micros = 0
    n_latency = 0
    users: set[str] = set()
    buckets: dict[str, list[int]] = {}
    heat: dict[tuple[str, str], int] = {}
    rejects: dict[str, int] = {}
    for row in rows.values():
        try:
            day = _dt.date.fromisoformat(str(row.get("date") or ""))
        except ValueError:
            continue
        if day < start or day > end:
            continue
        tool = str(row.get("tool") or "")
        param = str(row.get("param") or "")
        decision = str(row.get("decision") or "")
        count = int(row.get("count") or 0)
        if param == "":
            tasks += count
            interventions += int(row.get("intervention_count") or 0)
            bucket = _bucket_key(_dt.datetime.combine(day, _dt.time()), granularity)
            slot = buckets.setdefault(bucket, [0, 0])
            slot[0] += count
            slot[1] += int(row.get("intervention_count") or 0)
            agg = row.get("approval_latency_agg") or {}
            micros += int(agg.get("sum_micros") or 0)
            n_latency += int(agg.get("n") or 0)
            for user in (row.get("users") or []):
                users.add(str(user))
            if decision in ("approve", "modify"):
                approved += count
            elif decision == "reject":
                rejected += count
                rejects[tool] = rejects.get(tool, 0) + count
        else:
            heat[(tool, param)] = heat.get((tool, param), 0) + count
    # 归零补偿行留下的"零任务桶"必须去掉：流式路径对没有记录的日期**不放假趋势点**
    # （空桶不放假点，旧算法逐字如此），预聚合路径多一个 {tasks:0} 就是两路径不同结果。
    buckets = {key: value for key, value in buckets.items() if value[0] > 0}
    return {"tasks": tasks, "interventions": interventions, "approved": approved,
            "rejected": rejected, "micros": micros, "n_latency": n_latency,
            "users": users, "buckets": buckets, "heat": heat, "rejects": rejects}


def merge_dashboard(records: Any, *, path: pathlib.Path | str,
                    start: _dt.date, end: _dt.date, granularity: str,
                    agent: str | None = None, salt: str = "") -> dict[str, Any]:
    """预聚合优先的仪表盘合并：``{report, meta}``；report 是指标区（尾部键由 dashboard 补）。

    路径选择（全部如实进 ``meta``）：

    * ``agent`` 过滤 / 自定义 ``salt``：账本行不含 agent、用户脱敏盐写死在聚合时
      （``salt=""`` 与服务端现状一致）→ 回退流式扫描，``reason=agent_filter/custom_salt``；
    * 输入是一次性迭代器（无法二次流式复算）→ 回退流式，``reason=input_not_rerunnable``；
    * 账本缺失且无可聚合记录 → 回退流式（不建空账本），``reason=ledger_missing_no_records``；
    * 链校验不过（篡改/坏行）→ 数字走流式扫描（**不消费被污染的账本**），
      ``reason=chain_tampered``；
    * 其余：增量聚合（水位线外新记录）+ 修正补偿行 → 报告从账本合并行读出，
      ``pre_aggregated=true``；追加失败 → 数字照出、``durable=false``（本会话内存复算，
      下次调用幂等重试）。
    """
    ledger = AggLedger(path)
    meta: dict[str, Any] = {"path": str(ledger.path)}
    if agent:
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        return _fallback(report, meta, scanned, "agent_filter")
    if salt:
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        return _fallback(report, meta, scanned, "custom_salt")
    if not isinstance(records, (list, tuple)):
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        return _fallback(report, meta, scanned, "input_not_rerunnable")

    try:
        snapshot = ledger.load()
    except (AggLedgerError, FeatureStoreError, OSError) as exc:
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        meta["problems"] = [_as_problem(exc)]
        return _fallback(report, meta, scanned, "ledger_unreadable")
    if snapshot["problems_count"] and not snapshot["chain_ok"]:
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        meta["problems"] = snapshot["problems"]
        meta["problems_count"] = snapshot["problems_count"]
        return _fallback(report, meta, scanned, "chain_tampered")

    consumed = _consumed_of(snapshot["state"])
    # 第一遍流式：水位线判定（谁新、谁被改），只数不聚（聚合复算只有一个函数）。
    known_before = set(consumed)
    new_ids = 0
    changed = False
    for record in records:
        if not isinstance(record, dict):
            continue
        key = record_key(record)
        digest = record_digest(record)
        if consumed.get(key) == digest:
            continue
        consumed[key] = digest
        changed = True
        if key not in known_before:
            new_ids += 1
    scanned = len(records)

    if not snapshot["exists"] and not changed:
        # 账本缺失且无可聚合记录（空记录边界）：不建空账本、不留空目录——
        # 走流式并如实标 ledger_missing_no_records。
        report, _ = _stream_scan(records, start=start, end=end, agent=agent,
                                 granularity=granularity, salt=salt)
        return _fallback(report, meta, scanned, "ledger_missing_no_records")

    merged_rows = snapshot["rows"]
    compensation = 0
    durable = True
    head = snapshot["head"]
    problems: list[dict[str, Any]] = []

    if changed:
        # 第二遍全量复算（聚合定义唯一实现 recompute_rows）→ 与账本合并行 diff →
        # 只追加**值真变了**的键；消失的键追加"归零补偿行"（last-write-wins 的修正
        # 先例，历史行一字不改）。没有增量专用口径：增量、补偿、复算共用同一个函数。
        fresh = recompute_rows(records, salt="")
        payloads: list[dict[str, Any]] = []
        for key, values in fresh.items():
            old = merged_rows.get(key)
            if old is None or _row_values(old) != _row_values(values):
                payloads.append(_row_payload(values))
                if old is not None:
                    compensation += 1
        for key, old in merged_rows.items():
            if key not in fresh and _row_values(old) != (0, 0, 0, 0, []):
                zero = dict(old)
                zero.update({"count": 0, "intervention_count": 0,
                             "approval_latency_agg": {"sum_micros": 0, "n": 0},
                             "users": []})
                payloads.append(_row_payload(zero))
                compensation += 1
        payloads.append(_state_payload(consumed))
        if not snapshot["exists"]:
            payloads.insert(0, {"agg_version": AGG_VERSION, "kind": KIND_HEADER,
                                "layout": "analytics-agg.v1", "updated_at": utc_stamp()})
        try:
            written = ledger.append_rows(payloads)
            if written:
                head = str(written[-1].get("entry_hash") or head)
        except (AggLedgerError, FeatureStoreError, OSError) as exc:
            durable = False
            problems.append(_as_problem(exc))
            # 追加失败：账本没写进 = 下次调用幂等重试；本轮数字不装坏——从
            # "内存复算视图"读（下面的 view 与落盘成功后的合并视图逐键相等）。
        # 读侧视图 = 落盘成功后的合并视图（fresh 覆盖旧值；消失键归零）。
        view: dict[str, Any] = {key: dict(values) for key, values in fresh.items()}
        for key, old in merged_rows.items():
            if key not in view:
                zero = dict(old)
                zero.update({"count": 0, "intervention_count": 0,
                             "approval_latency_agg": {"sum_micros": 0, "n": 0},
                             "users": []})
                view[key] = zero
    else:
        view = dict(merged_rows)

    counters = rows_to_counters(view, start=start, end=end, granularity=granularity)
    report = _assemble_metrics(counters)
    meta.update({"pre_aggregated": True, "scanned": scanned,
                 "newly_aggregated": new_ids, "compensation_keys": compensation,
                 "durable": durable, "head": head,
                 "ledger_rows": len(view)})
    if problems:
        meta["problems"] = problems
    return {"report": report, "meta": meta}


def _fallback(report: dict[str, Any], meta: dict[str, Any], scanned: int,
              reason: str) -> dict[str, Any]:
    meta.update({"pre_aggregated": False, "scanned": scanned, "reason": reason})
    return {"report": report, "meta": meta}


def _as_problem(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, (AggLedgerError, FeatureStoreError)):
        return exc.as_dict()
    return {"code": "AGG_LEDGER_WRITE_FAILED", "reason": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# 自检（``python -m agent_explainer.analytics_agg``）
# --------------------------------------------------------------------------- #


def _self_check() -> int:  # pragma: no cover - 人工自检
    import sys
    import tempfile

    from .analytics import dashboard

    # 纯度纪律：包内核只许标准库，自检不借道 tests/。受限沙箱里裸临时目录
    # 可能拒绝锁文件写入（环境事实，不是产品失败）：探针不可写就如实跳过。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        probe = pathlib.Path(temp) / "probe.lock"
        try:
            probe.write_bytes(b"")
        except OSError as exc:
            print(f"analytics_agg self-check skipped: temp dir not writable "
                  f"in this sandbox ({exc.__class__.__name__})")
            return 0
        ledger_path = pathlib.Path(temp) / "agg_daily.jsonl"

        def record(idx: int, decision: str | None, day: int) -> dict:
            out = {"id": f"act-{idx:04d}", "agent_id": "agent-a",
                   "timestamp": f"2026-11-{day:02d}T09:00:00Z",
                   "tool_used": {"name": "fs.write"}}
            if decision:
                out["user_intervention"] = {
                    "decision": decision, "decided_by": "local-user",
                    "decided_at": f"2026-11-{day:02d}T10:00:00Z",
                    "modified_fields": [{"name": "max_move_count"}]}
            return out

        records = [record(i, "approve" if i % 2 else "reject", 1 + i % 5)
                   for i in range(1, 41)]
        first = dashboard(records, agg=ledger_path)
        second = dashboard(records, agg=ledger_path)
        assert first["pre_aggregated"] is True and second["scanned"] == 40
        assert first["total_tasks"] == 40 and first["total_interventions"] == 40
        plain = dashboard(records)
        for key in ("total_tasks", "total_interventions", "intervention_rate",
                    "avg_approval_seconds", "approved", "rejected", "trend",
                    "heatmap", "top_parameters", "top_rejected", "users"):
            assert first[key] == plain[key], key
        report = AggLedger(ledger_path).verify()
        assert report["ok"], report
        print(f"analytics_agg self-check ok: rows={report['rows']} lines={report['lines']} "
              f"head={report['head'][:18]}… pre_aggregated={first['pre_aggregated']}")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_check())
