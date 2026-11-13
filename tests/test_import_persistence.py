"""4.9（t22）数据导入（粘贴 / 上传）与记录持久化。

QA 5.1 量到的两个缺口，都在这里钉住：

* **D1 没有用户可用的导入入口**：`POST /api/import` 是唯一入口，粘贴文本与
  上传文件走同一条路；四种格式（Langfuse / OTel GenAI / MCP / 原生）自动识别，
  认不出来就 400 说清原因，绝不静默换格式硬解析。
* **重启回到演示 12 条**：记录与用户决定写进追加式 JSONL 日志
  （默认 `var/records.jsonl`），启动时加载，重启还在；团队模式下多个服务共享
  同一个日志文件，写入方立刻互相看得见。

覆盖口径：两种来源、四种格式识别、超限 / 非法 / 空 / mode 非法、重复与受保护
字段、审计不记正文、重启保留、团队共享、`reset` 只清记录（审计链只追加）、
`--reset-demo`、并发写、日志坏行与锁。

临时目录一律走 `tests/scratch.py`（系统临时目录优先，绝不落在 `tests/` 下）。
"""

from __future__ import annotations

import ast
import contextlib
import copy
import http.client
import io
import json
import os
import pathlib
import shutil
import sys
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_explainer import auth as auth_mod  # noqa: E402
from agent_explainer import ingest as ingest_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import store as st  # noqa: E402
from agent_explainer import trace_adapter as ta  # noqa: E402

from scratch import scratch_dir, scratch_reason  # noqa: E402

TMP = scratch_dir("import-persistence")
TRACES = REPO / "samples" / "traces"
NATIVE_FILE = REPO / "samples" / "action_records.json"

LANGFUSE = json.loads((TRACES / "langfuse.json").read_text(encoding="utf-8"))
OTEL = json.loads((TRACES / "otel_genai.json").read_text(encoding="utf-8"))
MCP = json.loads((TRACES / "mcp_events.json").read_text(encoding="utf-8"))
LANGFUSE_TEXT = (TRACES / "langfuse.json").read_text(encoding="utf-8")
OTEL_TEXT = (TRACES / "otel_genai.json").read_text(encoding="utf-8")
MCP_TEXT = (TRACES / "mcp_events.json").read_text(encoding="utf-8")
NATIVE_RECORDS = json.loads(NATIVE_FILE.read_text(encoding="utf-8"))["records"]

LANGFUSE_COUNT = 8
OTEL_COUNT = 7
MCP_COUNT = 9
DEMO_COUNT = len(NATIVE_RECORDS)          # 演示种子 = 12 条
PENDING_ID = next(r["id"] for r in NATIVE_RECORDS
                  if r.get("status") == "pending_confirmation")
CODES = {  # 存储层对外承诺的稳定 code（改名必须同步 UI 文案与 docs）
    "locked": "RECORD_STORE_LOCKED",
    "line_invalid": "RECORD_STORE_LINE_INVALID",
    "line_too_large": "RECORD_STORE_LINE_TOO_LARGE",
    "record_invalid": "RECORD_STORE_RECORD_INVALID",
    "tail_truncated": "RECORD_STORE_TAIL_TRUNCATED",
    "op_unknown": "RECORD_STORE_OP_UNKNOWN",
    "write_failed": "RECORD_STORE_WRITE_FAILED",
}
# 落盘交给 store.py，但它同样必须"纯标准库 + 不出网 + 不起进程"（server.py 的纯净性同口径）
STORE_STDLIB_OK = {"__future__", "copy", "datetime", "fcntl", "json", "msvcrt", "os",
                   "pathlib", "shutil", "tempfile", "threading", "time", "typing"}
BANNED_STORE_CALLS = frozenset({"urlopen", "create_connection", "HTTPConnection",
                                "HTTPSConnection", "Popen", "system", "spawn",
                                "startfile", "connect", "send", "recv", "urlopen"})


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def unlock(folder: pathlib.Path) -> None:
    for leaf in folder.rglob("*"):
        try:
            os.chmod(leaf, 0o666)          # Windows 只读位先解开，否则删不掉
        except OSError:
            pass


def room(name: str) -> pathlib.Path:
    """每个用例一个独立目录（系统临时目录下），旧的先整目录删掉。"""
    folder = TMP / name
    unlock(folder)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clean(folder: pathlib.Path) -> None:
    unlock(folder)
    shutil.rmtree(folder, ignore_errors=True)


def service(name: str, **over) -> sv.InterveneService:
    """服务级装配：默认给一条持久日志 + 一条审计 JSONL（与 `main()` 的默认一致）。"""
    folder = room(name)
    over.setdefault("records_store_path", folder / st.RECORD_FILE_NAME)
    over.setdefault("audit_log_path", folder / "audit.jsonl")
    svc = sv.InterveneService(**over)
    svc._t22_room = folder          # type: ignore[attr-defined]
    return svc


def boot(name: str, **over):
    """起一个真服务（只监听 127.0.0.1 的临时端口），返回 (srv, port)。"""
    folder = room(name)
    over.setdefault("records_store_path", folder / st.RECORD_FILE_NAME)
    over.setdefault("audit_log_path", folder / "audit.jsonl")
    return resume(folder, **over)


def resume(folder: pathlib.Path, **over):
    """重启口径：不换目录，只换进程（这才是"重启后还在不在"）。"""
    over.setdefault("quiet", True)
    srv, _thread = sv.start_background_server("127.0.0.1", 0, **over)
    srv._t22_room = folder          # type: ignore[attr-defined]
    return srv, srv.server_address[1]


def http_request(port: int, method: str, path: str, body=None,
                 headers: dict | None = None, timeout: float = 15.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        hd = {"Connection": "close", "Accept": "application/json"}
        if payload is not None:
            hd["Content-Type"] = "application/json"
        hd.update(headers or {})
        conn.request(method, path, payload, hd)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        data: object = raw
        if "json" in (resp.getheader("Content-Type") or ""):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                pass
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def post_import(port: int, body, headers: dict | None = None):
    return http_request(port, "POST", sv.API_PATH_IMPORT, body, headers)


def import_text(port: int, payload, **extra):
    body = {"payload": payload}
    body.update(extra)
    return post_import(port, body)


def code_of(data: object) -> str:
    return str(data.get("code")) if isinstance(data, dict) else ""


def records_of(port: int):
    status, _hd, data = http_request(port, "GET", sv.API_PATH_RECORDS)
    assert status == 200, data
    assert isinstance(data, dict)
    return list(data.get("records") or []), dict(data.get("meta") or {})


def records_of_service(svc: sv.InterveneService) -> list[dict]:
    return list(svc.records_payload().get("records") or [])


def audit_events(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_raw": line})
    return out


def mcp_doc(count: int, start: int = 0) -> dict:
    """最小合法的 MCP 事件流（每条 ~90 字节）：用来打条数与体积的边界。"""
    return {"protocol": "2024-11-05", "session_id": "sess-t22",
            "events": [{"jsonrpc": "2.0", "id": start + i, "method": "tools/call",
                        "ts": "2026-11-09T08:00:00Z"} for i in range(count)]}


def mcp_text(count: int, start: int = 0) -> str:
    return json.dumps(mcp_doc(count, start), ensure_ascii=False)


def grab_import(svc: sv.InterveneService, payload, **kw):
    """执行导入，返回 (结果, None) 或 (None, 异常)。"""
    try:
        return svc.import_traces(payload, **kw), None
    except sv.InterveneError as exc:
        return None, exc


def ids_of(records: list[dict]) -> set[str]:
    return {str(r.get("id")) for r in records}


def source_kind(record: dict) -> str:
    source = record.get("source")
    return str(source.get("kind")) if isinstance(source, dict) else ""


# --------------------------------------------------------------------------- #
# A. 记录日志本体（store.py）
# --------------------------------------------------------------------------- #

class RecordJournalTests(unittest.TestCase):
    """`store.py` 的追加式日志：坏行跳过并报告、末行截断封口、锁、原子重写。"""

    def setUp(self) -> None:
        self.work = room("journal-" + self.id().split(".")[-1])
        self.path = self.work / st.RECORD_FILE_NAME
        self.addCleanup(lambda: clean(self.work))

    def journal(self, **over) -> st.RecordJournal:
        return st.open_journal(self.path, **over)

    def test_default_store_path_follows_mode(self) -> None:
        local = st.default_store_path("local", root=self.work)
        team = st.default_store_path("team", root=self.work)
        self.assertEqual(local.name, st.RECORD_FILE_NAME)
        self.assertEqual(local, team)
        self.assertEqual(local.parent.name, "var")

    def test_append_put_then_load_round_trip(self) -> None:
        j = self.journal()
        self.assertFalse(j.exists())
        j.append_put(NATIVE_RECORDS[0])
        j.append_put(NATIVE_RECORDS[1])
        loaded = j.load()
        self.assertTrue(loaded.existed)
        self.assertEqual(len(loaded.records), 2)
        self.assertEqual(loaded.lines, 2)
        self.assertTrue(loaded.ok, loaded.as_dict())
        self.assertEqual({r["id"] for r in loaded.records},
                         {NATIVE_RECORDS[0]["id"], NATIVE_RECORDS[1]["id"]})

    def test_last_write_wins_for_the_same_id(self) -> None:
        j = self.journal()
        first = copy.deepcopy(NATIVE_RECORDS[0])
        second = copy.deepcopy(first)
        second["status"] = "failed"
        j.append_put(first)
        j.append_put(second)
        loaded = j.load()
        self.assertEqual(len(loaded.records), 1)
        self.assertEqual(loaded.records[0]["status"], "failed")

    def test_bad_line_is_skipped_and_reported(self) -> None:
        j = self.journal()
        j.append_put(NATIVE_RECORDS[0])
        with open(self.path, "ab") as handle:
            handle.write(b"this is not json\n")
        j.append_put(NATIVE_RECORDS[1])
        loaded = j.load()
        self.assertEqual(len(loaded.records), 2, "坏行只跳过，其余照常装载")
        self.assertFalse(loaded.ok)
        self.assertIn(CODES["line_invalid"], [p["code"] for p in loaded.problems])

    def test_truncated_tail_is_sealed_on_next_append(self) -> None:
        j = self.journal()
        j.append_put(NATIVE_RECORDS[0])
        with open(self.path, "ab") as handle:
            handle.write(b'{"op": "put", "id": "half')     # 写一半就断电
        loaded = j.load()
        self.assertEqual(len(loaded.records), 1)
        self.assertFalse(loaded.ok)
        self.assertIn(CODES["tail_truncated"], [p["code"] for p in loaded.problems])
        again = j.append_put(NATIVE_RECORDS[1])
        self.assertTrue(again.get("sealed_partial_line"), again)
        self.assertEqual(len(j.load().records), 2)

    def test_unknown_op_is_reported_and_skipped(self) -> None:
        with open(self.path, "wb") as handle:
            handle.write((json.dumps({"op": "teleport", "id": "x"}) + "\n").encode("utf-8"))
            handle.write((json.dumps({"op": "put", "id": NATIVE_RECORDS[0]["id"],
                                      "record": NATIVE_RECORDS[0]}) + "\n").encode("utf-8"))
        loaded = self.journal().load()
        self.assertEqual(len(loaded.records), 1)
        self.assertIn(CODES["op_unknown"], [p["code"] for p in loaded.problems])

    def test_non_object_record_is_reported(self) -> None:
        with open(self.path, "wb") as handle:
            handle.write((json.dumps({"op": "put", "id": "x", "record": "not a record"})
                          + "\n").encode("utf-8"))
        loaded = self.journal().load()
        self.assertEqual(loaded.records, [])
        self.assertIn(CODES["record_invalid"], [p["code"] for p in loaded.problems])

    def test_clear_line_drops_earlier_puts(self) -> None:
        j = self.journal()
        for record in NATIVE_RECORDS[:4]:
            j.append_put(record)
        j.append_clear(reason="reset")
        loaded = j.load()
        self.assertEqual(loaded.records, [])
        self.assertTrue(loaded.ok, loaded.as_dict())

    def test_replace_all_writes_header_and_is_the_whole_file(self) -> None:
        j = self.journal()
        for record in NATIVE_RECORDS[:6]:
            j.append_put(record)
        j.append_clear(reason="reset")
        result = j.replace_all(NATIVE_RECORDS[:3], header={"written_by": "reset"})
        self.assertTrue(result["ok"])
        loaded = j.load()
        self.assertEqual(len(loaded.records), 3)
        self.assertTrue(loaded.ok, loaded.as_dict())
        first = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first.get("kind"), "header")
        self.assertEqual(first.get("written_by"), "reset")
        self.assertEqual(first.get("store_version"), st.STORE_VERSION)

    def test_oversized_line_is_rejected(self) -> None:
        j = self.journal()
        huge = copy.deepcopy(NATIVE_RECORDS[0])
        huge["intent"] = "长" * (st.MAX_LINE_BYTES // 2)
        with self.assertRaises(st.RecordStoreError) as caught:
            j.append_put(huge)
        self.assertEqual(caught.exception.code, CODES["line_too_large"])
        self.assertFalse(j.exists(), "超限的行一个字都不写")

    def test_lock_contention_reports_locked(self) -> None:
        j = self.journal(lock_timeout=0.2)
        j.append_put(NATIVE_RECORDS[0])
        held = st._FileLock(self.path, timeout=0.2)
        held.__enter__()
        try:
            with self.assertRaises(st.RecordStoreError) as caught:
                j.append_put(NATIVE_RECORDS[1])
            self.assertEqual(caught.exception.code, CODES["locked"])
        finally:
            held.__exit__(None, None, None)
        j.append_put(NATIVE_RECORDS[1])
        self.assertEqual(len(j.load().records), 2)

    def test_signature_changes_after_every_write(self) -> None:
        j = self.journal()
        self.assertIsNone(j.signature())
        j.append_put(NATIVE_RECORDS[0])
        before = j.signature()
        self.assertIsInstance(before, tuple)
        j.append_put(NATIVE_RECORDS[1])
        self.assertNotEqual(before, j.signature())
        info = j.stat()
        self.assertTrue(info["exists"])
        self.assertEqual(info["lines"], 2)

    def test_tail_lines_are_clipped_and_ordered(self) -> None:
        j = self.journal()
        for record in NATIVE_RECORDS[:7]:
            j.append_put(record)
        tail = j.tail_lines(limit=3)
        self.assertEqual(len(tail), 3)
        self.assertTrue(all(line.startswith("{") for line in tail))

    def test_threads_appending_concurrently_keep_every_row(self) -> None:
        j = self.journal(lock_timeout=10.0)
        picked = NATIVE_RECORDS[:6]
        errors: list[BaseException] = []

        def worker(shift: int) -> None:
            try:
                for k in range(5):
                    record = copy.deepcopy(picked[k % len(picked)])
                    record["id"] = f"rec-thread-{shift}-{k}"
                    j.append_put(record)
            except BaseException as exc:      # pragma: no cover - 失败时看详情
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(errors, errors)
        loaded = j.load()
        self.assertEqual(loaded.lines, 20, loaded.as_dict())
        self.assertEqual(len(loaded.records), 20)
        self.assertTrue(loaded.ok, loaded.as_dict())
        self.assertEqual(len({r["id"] for r in loaded.records}), 20)

    def test_self_check_runs_in_an_overridable_directory(self) -> None:
        probe = self.work / "self-check"
        probe.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("TRACELENS_STORE_SELFCHECK_DIR")
        os.environ["TRACELENS_STORE_SELFCHECK_DIR"] = str(probe)
        report = io.StringIO()
        try:
            with contextlib.redirect_stdout(report):     # 自检把 JSON 打到 stdout，别污染测试日志
                self.assertEqual(st._self_check(), 0)
        finally:
            if previous is None:
                os.environ.pop("TRACELENS_STORE_SELFCHECK_DIR", None)
            else:
                os.environ["TRACELENS_STORE_SELFCHECK_DIR"] = previous
        text = report.getvalue()
        self.assertIn('"ok": true', text, "自检必须打印可读的 JSON 报告")
        self.assertIn(st.RECORD_FILE_NAME, text)
        self.assertNotIn(str(TMP), str(REPO / "tests"))


# --------------------------------------------------------------------------- #
# B. 导入口径（服务层：格式、上限、去重、受保护字段、审计）
# --------------------------------------------------------------------------- #

class ImportTracesTests(unittest.TestCase):
    """`InterveneService.import_traces`：粘贴 / 上传、格式识别、上限、审计口径。"""

    def setUp(self) -> None:
        self.svc = service("import-unit-" + self.id().split(".")[-1])
        self.addCleanup(lambda: clean(self.svc._t22_room))

    def test_pasted_langfuse_is_detected_and_imported(self) -> None:
        out, err = grab_import(self.svc, LANGFUSE_TEXT, request_id="req-paste", lang="zh")
        self.assertIsNone(err, err)
        self.assertTrue(out["ok"])
        self.assertEqual(out["format"], "langfuse")
        self.assertEqual(out["imported"], LANGFUSE_COUNT)
        self.assertEqual(out["created"], LANGFUSE_COUNT)
        self.assertEqual(out["source_kind"], "paste_json")
        self.assertFalse(out["filename"], "粘贴没有文件名")
        self.assertTrue(set(out["ids"]) <= ids_of(records_of_service(self.svc)))
        self.assertEqual(out["records_total"], DEMO_COUNT + LANGFUSE_COUNT)
        for record in self.svc.store.all():
            if str(record["id"]) in set(out["ids"]):
                self.assertEqual(source_kind(record), "paste_json")
                self.assertNotEqual(record.get("demo_data"), True)

    def test_uploaded_file_is_labelled_upload(self) -> None:
        out, err = grab_import(self.svc, json.dumps(OTEL), request_id="req-upload")
        self.assertIsNone(err, err)
        self.assertEqual(out["format"], "otel_genai")
        self.assertEqual(out["imported"], OTEL_COUNT)
        self.assertEqual(out["source_kind"], "paste_json")
        uploaded, err2 = grab_import(self.svc,
                                     {"payload": MCP_TEXT, "filename": "mcp-export.json"},
                                     request_id="req-file")
        self.assertIsNone(err2, err2)
        self.assertEqual(uploaded["source_kind"], "upload")
        self.assertEqual(uploaded["filename"], "mcp-export.json")
        self.assertEqual(uploaded["format"], "mcp_events")
        rows = [r for r in self.svc.store.all() if source_kind(r) == "upload"]
        self.assertEqual(len(rows), MCP_COUNT)

    def test_all_four_formats_are_detected_from_content(self) -> None:
        wanted = {"langfuse": LANGFUSE, "otel_genai": OTEL, "mcp_events": MCP}
        for fmt, doc in wanted.items():
            out, err = grab_import(self.svc, doc, request_id=f"req-{fmt}")
            self.assertIsNone(err, f"{fmt}: {err}")
            self.assertEqual(out["format"], fmt)
        native, err = grab_import(self.svc, NATIVE_RECORDS, request_id="req-native")
        self.assertIsNone(err, err)
        self.assertEqual(native["format"], "native")
        # 原生夹具与演示种子同 id：默认 upsert 走"更新"，不能被当成新记录塞两份
        self.assertEqual(native["updated"], DEMO_COUNT)
        self.assertEqual(native["imported"], DEMO_COUNT)
        self.assertEqual(native["duplicates"], 0)
        self.assertEqual(len(self.svc.store.all()),
                         DEMO_COUNT + LANGFUSE_COUNT + OTEL_COUNT + MCP_COUNT,
                         "同一份内容反复 upsert 不能长出新记录")
        self.assertEqual(sorted(ta.SUPPORTED_FORMATS),
                         ["langfuse", "mcp_events", "native", "otel_genai"])

    def test_explicit_format_that_does_not_fit_is_rejected(self) -> None:
        _out, err = grab_import(self.svc, {"payload": OTEL_TEXT, "format": "langfuse"},
                                request_id="req-mismatch")
        self.assertIsNotNone(err)
        self.assertEqual(err.http_status, 400)
        self.assertTrue(str(err.code).startswith("TRACE_"), err.code)
        self.assertEqual(err.detail["adapter_code"], str(err.code))
        self.assertIn("supported", err.detail)

    def test_unrecognisable_payload_says_why(self) -> None:
        _out, err = grab_import(self.svc, {"payload": {"hello": "world"}},
                                request_id="req-junk")
        self.assertIsNotNone(err)
        self.assertEqual(err.http_status, 400)
        self.assertIn("adapter_code", err.detail, err.detail)
        self.assertIn("supported", err.detail, "要说清支持哪几种格式")
        self.assertTrue(err.message, "错误必须有一句给人看的话")

    def test_empty_and_non_json_bodies_are_rejected(self) -> None:
        _out, err = grab_import(self.svc, {"payload": ""}, request_id="req-empty")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_INPUT_EMPTY")
        _out, err = grab_import(self.svc, {"payload": "{ not json"}, request_id="req-badjson")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_INPUT_NOT_JSON")
        self.assertIn("line", err.detail, "非法 JSON 要说清第几行")
        _out, err = grab_import(self.svc, {"mode": "upsert"}, request_id="req-none")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_INPUT_EMPTY")

    def test_document_over_the_batch_cap_is_rejected(self) -> None:
        big = json.dumps(NATIVE_RECORDS * 20, ensure_ascii=False)
        self.assertGreater(len(big.encode("utf-8")), sv.IMPORT_MAX_BYTES)
        _out, err = grab_import(self.svc, {"payload": big}, request_id="req-big")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_TOO_LARGE")
        self.assertEqual(err.http_status, 413)
        self.assertEqual(err.detail["limit"], sv.IMPORT_MAX_BYTES)

    def test_more_records_than_the_cap_is_rejected(self) -> None:
        over = mcp_text(sv.IMPORT_MAX_RECORDS + 100)
        self.assertLess(len(over.encode("utf-8")), sv.IMPORT_MAX_BYTES)
        _out, err = grab_import(self.svc, {"payload": over}, request_id="req-many")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_TOO_MANY")
        self.assertEqual(err.detail["limit"], sv.IMPORT_MAX_RECORDS)
        out, err2 = grab_import(self.svc, {"payload": mcp_text(40)}, request_id="req-fits")
        self.assertIsNone(err2, err2)
        self.assertEqual(out["imported"], 40, "边界内的一批照常入库")
        self.assertEqual(sv.IMPORT_MAX_RECORDS, ingest_mod.MAX_EVENTS_PER_REQUEST,
                         "导入与实时接入的条数上限必须一致")

    def test_unknown_mode_is_rejected(self) -> None:
        _out, err = grab_import(self.svc, {"payload": MCP_TEXT, "mode": "yolo"},
                                request_id="req-mode")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "IMPORT_MODE_INVALID")
        self.assertIn("create", json.dumps(err.detail, ensure_ascii=False))

    def test_wrappers_and_bare_documents_all_reach_the_adapter(self) -> None:
        for field in sv.IMPORT_SOURCE_FIELDS:
            out, err = grab_import(self.svc,
                                   {field: {"protocol": "2024-11-05",
                                            "events": MCP["events"][:6]}},
                                   request_id=f"req-field-{field}")
            self.assertIsNone(err, f"{field}: {err}")
            self.assertEqual(out["format"], "mcp_events", field)
            self.assertGreaterEqual(out["imported"], 1, field)
        bare, err = grab_import(self.svc, MCP, request_id="req-bare")
        self.assertIsNone(err, err)
        self.assertEqual(bare["imported"], MCP_COUNT)

    def test_create_mode_counts_existing_ids_as_duplicates(self) -> None:
        first, err = grab_import(self.svc, {"payload": MCP_TEXT, "mode": "upsert"},
                                 request_id="req-dup-1")
        self.assertIsNone(err, err)
        self.assertEqual(first["created"], MCP_COUNT)
        _again, err2 = grab_import(self.svc, {"payload": MCP_TEXT, "mode": "create"},
                                   request_id="req-dup-2")
        self.assertIsNone(err2, err2)
        out, err3 = grab_import(self.svc, {"payload": MCP_TEXT, "mode": "create"},
                                request_id="req-dup-3")
        self.assertIsNone(err3, err3)
        self.assertEqual(out["duplicates"], MCP_COUNT)
        self.assertEqual(out["imported"], 0)
        self.assertEqual(out["created"], 0)
        self.assertTrue(out["duplicates_detail"] if "duplicates_detail" in out
                        else out["duplicate_detail"], "重复要逐条说清是谁撞了谁")

    def test_upsert_never_overwrites_the_users_decision(self) -> None:
        before = self.svc.store.get(PENDING_ID)
        self.assertIsNotNone(before)
        decided = sv.apply_decision(before, "approve", reason="我看过了",
                                    request_id="req-decide")
        self.svc.store.put(decided)
        self.assertEqual(decided["user_intervention"]["decision"], "approve")
        again, err = grab_import(self.svc, {"payload": NATIVE_RECORDS, "mode": "upsert"},
                                 request_id="req-protect")
        self.assertIsNone(err, err)
        self.assertEqual(again["updated"], DEMO_COUNT)
        after = self.svc.store.get(PENDING_ID)
        self.assertEqual(after["user_intervention"]["decision"], "approve",
                         "导入不得覆盖用户的决定（受保护字段）")

    def test_audit_records_the_import_but_never_the_pasted_body(self) -> None:
        secret = "internal-inbox@corp.example"
        doc = copy.deepcopy(MCP)
        doc["events"][0]["params"] = {"note": secret}
        out, err = grab_import(self.svc,
                               {"payload": json.dumps(doc, ensure_ascii=False),
                                "filename": "with-secret.json"},
                               request_id="req-audit")
        self.assertIsNone(err, err)
        text = (self.svc._t22_room / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, text, "粘贴正文一个字都不进审计")
        lines = audit_events(self.svc._t22_room / "audit.jsonl")
        imports = [e for e in lines if "import_source" in dict(e.get("fields") or {})]
        self.assertTrue(imports, "导入必须留下审计条目")
        fields = dict(imports[-1].get("fields") or {})
        self.assertEqual(fields["import_source"], "upload")
        self.assertEqual(fields["import_filename"], "with-secret.json")
        self.assertEqual(fields["import_body"], "not_recorded")
        self.assertIn("格式", str(imports[-1].get("reason") or ""))
        self.assertNotIn(secret, str(imports[-1].get("reason") or ""))

    def test_write_failure_is_a_loud_500_not_a_silent_success(self) -> None:
        journal = self.svc._t22_room / st.RECORD_FILE_NAME
        self.svc.import_traces({"payload": MCP_TEXT}, request_id="req-ro-1")
        os.chmod(journal, 0o444)
        self.addCleanup(lambda: os.chmod(journal, 0o666))
        _out, err = grab_import(self.svc, {"payload": OTEL_TEXT}, request_id="req-ro-2")
        self.assertIsNotNone(err, "写不进去要当场失败，不能出现导入成功、重启就没了")
        self.assertEqual(err.code, CODES["write_failed"])
        self.assertEqual(err.http_status, 500)
        self.assertNotIn("AttributeError", str(err))
        facts = self.svc.store.store_facts()
        self.assertEqual(facts["write_errors"], 1, facts)
        self.assertTrue(facts["problems"], facts)

    def test_durable_store_reports_facts_and_stats(self) -> None:
        self.svc.import_traces({"payload": MCP_TEXT, "filename": "mcp.json"},
                               request_id="req-facts")
        facts = self.svc.store.store_facts()
        self.assertTrue(facts["durable"])
        self.assertEqual(facts["mode"], "jsonl")
        self.assertEqual(facts["store_version"], st.STORE_VERSION)
        self.assertEqual(self.svc.store.stats()["durable"], True)
        self.assertEqual(self.svc.imports, 1)
        self.assertEqual(self.svc.imported_records, MCP_COUNT)

    def test_service_without_a_store_stays_memory_only(self) -> None:
        memory = sv.InterveneService(records=copy.deepcopy(NATIVE_RECORDS))
        out, err = grab_import(memory, {"payload": MCP_TEXT}, request_id="req-memory")
        self.assertIsNone(err, err)
        self.assertFalse(out["durable"])
        self.assertFalse(memory.store.stats()["durable"])
        self.assertFalse(memory.store.store_facts()["durable"])
        memory.store.reset_to_demo()
        self.assertEqual(len(memory.store.all()), DEMO_COUNT)


# --------------------------------------------------------------------------- #
# C. HTTP 入口（前端唯一会打的门）
# --------------------------------------------------------------------------- #

class ImportApiTests(unittest.TestCase):
    """`POST /api/import`：一个入口、可读的错误、团队模式权限与 CSRF。"""

    def setUp(self) -> None:
        self.srv, self.port = boot("import-api-" + self.id().split(".")[-1])
        self.addCleanup(lambda: (self.srv.shutdown(), clean(self.srv._t22_room)))

    def sign_in_team(self, label: str):
        """起一个 team 模式服务并登录管理员，返回 (port, cookie, csrf)。"""
        folder = room(f"import-team-{label}")
        srv, port = resume(folder, mode="team",
                           auth_state_path=folder / "auth-state.json",
                           bootstrap_admin=True,
                           records_store_path=folder / st.RECORD_FILE_NAME,
                           audit_log_path=folder / "audit.jsonl")
        self.addCleanup(lambda: (srv.shutdown(), clean(folder)))
        report = srv.service.auth.bootstrap_report or {}
        status, _hd, data = http_request(port, "POST", sv.API_PATH_AUTH_LOGIN,
                                         {"username": "admin",
                                          "password": str(report.get("password"))})
        self.assertEqual(status, 200, data)
        cookie = f"{auth_mod.SESSION_COOKIE}={data['cookie']['value']}"
        return port, cookie, str(data["csrf_token"])

    def test_paste_then_the_list_shows_the_imported_rows(self) -> None:
        before, _meta = records_of(self.port)
        self.assertEqual(len(before), DEMO_COUNT)
        status, hd, data = import_text(self.port, LANGFUSE_TEXT, mode="upsert", lang="zh")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["imported"], LANGFUSE_COUNT)
        self.assertEqual(data["format"], "langfuse")
        self.assertEqual(hd["x-import-format"], "langfuse")
        self.assertEqual(hd["x-import-imported"], str(LANGFUSE_COUNT))
        self.assertEqual(hd["x-import-durable"], "true")
        self.assertTrue(hd.get("x-request-id"))
        after, meta = records_of(self.port)
        self.assertEqual(len(after), len(before) + LANGFUSE_COUNT)
        self.assertEqual(meta["imported_count"], LANGFUSE_COUNT)
        self.assertTrue(meta["records_durable"])
        self.assertEqual(meta["records_store"]["durable"], True)

    def test_imported_rows_are_not_marked_as_demo_data(self) -> None:
        status, _hd, data = import_text(self.port, MCP_TEXT, filename="mcp.json")
        self.assertEqual(status, 200, data)
        rows, meta = records_of(self.port)
        self.assertEqual(len([r for r in rows if r.get("demo_data") is True]), DEMO_COUNT,
                         "演示徽标仍由 demo_data / source.kind 判定")
        imported = [r for r in rows if str(r.get("id")) in set(data["ids"])]
        self.assertEqual(len(imported), MCP_COUNT)
        for row in imported:
            self.assertNotEqual(row.get("demo_data"), True)
            self.assertEqual(source_kind(row), "upload")
        self.assertEqual(meta["imported_count"], MCP_COUNT)

    def test_get_on_the_import_route_is_method_not_allowed(self) -> None:
        status, _hd, data = http_request(self.port, "GET", sv.API_PATH_IMPORT)
        self.assertEqual(status, 405, data)
        self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED")

    def test_health_advertises_the_import_door_and_the_store(self) -> None:
        status, _hd, data = http_request(self.port, "GET", "/health")
        self.assertEqual(status, 200, data)
        self.assertIn(sv.API_PATH_IMPORT, data["endpoints"])
        block = data["import"]
        self.assertEqual(block["endpoint"], sv.API_PATH_IMPORT)
        self.assertEqual(block["method"], "POST")
        self.assertEqual(block["permission"], "data.write")
        self.assertEqual(block["max_bytes"], sv.IMPORT_MAX_BYTES)
        self.assertEqual(block["max_records"], sv.IMPORT_MAX_RECORDS)
        self.assertEqual(sorted(block["modes"]), ["create", "update", "upsert"])
        self.assertEqual(sorted(block["formats"]),
                         ["langfuse", "mcp_events", "native", "otel_genai"])
        self.assertTrue(block["auto_detect"])
        self.assertEqual(data["records_store"]["mode"], "jsonl")
        self.assertTrue(data["records_store"]["durable"])

    def test_oversize_body_is_rejected_with_a_readable_code(self) -> None:
        big = "x" * (sv.IMPORT_MAX_BYTES + 4096)
        status, _hd, data = import_text(self.port, big)
        self.assertEqual(status, 413, data)
        self.assertEqual(code_of(data), "INTERVENE_BODY_TOO_LARGE")
        self.assertIn("detail", data)

    def test_invalid_json_and_empty_body_are_400(self) -> None:
        status, _hd, data = import_text(self.port, "{ not json at all")
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "IMPORT_INPUT_NOT_JSON")
        self.assertTrue(str(data.get("message")))
        status, _hd, data = post_import(self.port, {"payload": ""})
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "IMPORT_INPUT_EMPTY")

    def test_bad_format_and_bad_mode_are_reported_to_the_page(self) -> None:
        status, _hd, data = import_text(self.port, OTEL_TEXT, format="langfuse")
        self.assertEqual(status, 400, data)
        self.assertTrue(code_of(data).startswith("TRACE_"), data)
        self.assertIn("adapter_code", data.get("detail") or {}, data)
        status, _hd, data = import_text(self.port, MCP_TEXT, mode="yolo")
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "IMPORT_MODE_INVALID")

    def test_local_mode_allows_the_import_without_a_session(self) -> None:
        status, _hd, data = import_text(self.port, LANGFUSE_TEXT)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["actor"], sv.LOCAL_ACTOR_ID)
        self.assertEqual(data["duplicates"], 0)

    def test_team_mode_needs_data_write_and_csrf(self) -> None:
        port, cookie, csrf = self.sign_in_team("gate")
        status, _hd, data = import_text(port, MCP_TEXT, filename="mcp.json")
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), "AUTH_REQUIRED")
        status, _hd, data = post_import(port, {"payload": MCP_TEXT}, {"Cookie": cookie})
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "CSRF_FAILED")
        status, _hd, data = post_import(port, {"payload": MCP_TEXT},
                                        {"Cookie": cookie, auth_mod.CSRF_HEADER: csrf})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["imported"], MCP_COUNT)
        status, _hd, data = http_request(port, "POST", sv.API_PATH_USERS,
                                         {"username": "vw", "role": "viewer"},
                                         {"Cookie": cookie, auth_mod.CSRF_HEADER: csrf})
        self.assertEqual(status, 201, data)
        _st, _hd, login = http_request(port, "POST", sv.API_PATH_AUTH_LOGIN,
                                       {"username": "vw",
                                        "password": data["user"]["initial_password"]})
        viewer_cookie = f"{auth_mod.SESSION_COOKIE}={login['cookie']['value']}"
        status, _hd, data = post_import(port, {"payload": MCP_TEXT},
                                        {"Cookie": viewer_cookie,
                                         auth_mod.CSRF_HEADER: str(login["csrf_token"])})
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")

    def test_import_and_intervene_share_the_protection_rules(self) -> None:
        status, _hd, data = import_text(self.port, LANGFUSE_TEXT)
        self.assertEqual(status, 200, data)
        rows, _meta = records_of(self.port)
        self.assertTrue(any(r["id"] == PENDING_ID for r in rows), "演示种子仍在")
        status, _hd, data = http_request(self.port, "POST", "/intervene",
                                         {"action_id": PENDING_ID, "decision": "approve",
                                          "reason": "我看过了"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user_intervention"]["decision"], "approve")
        status, _hd, data = import_text(self.port, NATIVE_RECORDS, mode="upsert")
        self.assertEqual(status, 200, data)
        rows, _meta = records_of(self.port)
        decided = next(r for r in rows if r["id"] == PENDING_ID)
        self.assertEqual(decided["user_intervention"]["decision"], "approve")


# --------------------------------------------------------------------------- #
# D. 持久化（重启、共享、reset、并发）
# --------------------------------------------------------------------------- #

class PersistenceTests(unittest.TestCase):
    """记录活得过重启；多个进程共享同一份日志；reset 只清记录，审计只追加。"""

    def test_records_survive_a_restart(self) -> None:
        srv, port = boot("restart")
        folder = srv._t22_room
        self.addCleanup(lambda: clean(folder))
        status, _hd, data = import_text(port, LANGFUSE_TEXT, filename="lf.json")
        self.assertEqual(status, 200, data)
        status, _hd, data = import_text(port, MCP_TEXT)
        self.assertEqual(status, 200, data)
        rows, meta = records_of(port)
        self.assertEqual(len(rows), DEMO_COUNT + LANGFUSE_COUNT + MCP_COUNT)
        self.assertTrue(meta["records_durable"])
        srv.shutdown()

        again, port2 = resume(folder,
                              records_store_path=folder / st.RECORD_FILE_NAME,
                              audit_log_path=folder / "audit.jsonl")
        self.addCleanup(again.shutdown)
        rows2, meta2 = records_of(port2)
        self.assertEqual(len(rows2), DEMO_COUNT + LANGFUSE_COUNT + MCP_COUNT,
                         "重启后记录必须还在")
        self.assertEqual(meta2["imported_count"], LANGFUSE_COUNT + MCP_COUNT)
        self.assertTrue(meta2["records_durable"])
        self.assertEqual(ids_of(rows2), ids_of(rows))

    def test_users_decision_survives_a_restart(self) -> None:
        srv, port = boot("decision-restart")
        folder = srv._t22_room
        self.addCleanup(lambda: clean(folder))
        status, _hd, data = http_request(port, "POST", "/intervene",
                                         {"action_id": PENDING_ID, "decision": "modify",
                                          "modified_params": {"max_move_count": 3},
                                          "reason": "最多移动 3 次，再多就停"})
        self.assertEqual(status, 200, data)
        srv.shutdown()
        again, port2 = resume(folder,
                              records_store_path=folder / st.RECORD_FILE_NAME,
                              audit_log_path=folder / "audit.jsonl")
        self.addCleanup(again.shutdown)
        rows, _meta = records_of(port2)
        decided = next(r for r in rows if r["id"] == PENDING_ID)
        self.assertEqual(decided["user_intervention"]["decision"], "modify")
        self.assertIn("max_move_count", json.dumps(decided, ensure_ascii=False))
        self.assertTrue((folder / "audit.jsonl").exists())

    def test_two_services_share_one_journal(self) -> None:
        folder = room("team-share")
        store_file = folder / st.RECORD_FILE_NAME
        one = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit-a.jsonl")
        two = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit-b.jsonl")
        self.addCleanup(lambda: clean(folder))
        self.assertEqual(len(one.store.all()), DEMO_COUNT)
        self.assertEqual(len(two.store.all()), DEMO_COUNT)
        out, err = grab_import(one, {"payload": LANGFUSE_TEXT, "filename": "lf.json"},
                               request_id="req-share-1")
        self.assertIsNone(err, err)
        self.assertEqual(out["imported"], LANGFUSE_COUNT)
        self.assertEqual(len(records_of_service(two)), DEMO_COUNT + LANGFUSE_COUNT,
                         "另一个进程必须立刻看到导入的记录")
        facts = two.store.store_facts()
        self.assertTrue(facts["durable"])
        self.assertEqual(facts["path"], str(store_file))
        out2, err2 = grab_import(two, {"payload": MCP_TEXT}, request_id="req-share-2")
        self.assertIsNone(err2, err2)
        self.assertEqual(len(one.store.all()), DEMO_COUNT + LANGFUSE_COUNT + MCP_COUNT)

    def test_reset_clears_records_only_and_audit_stays_appended(self) -> None:
        srv, port = boot("reset-semantics")
        folder = srv._t22_room
        self.addCleanup(lambda: (srv.shutdown(), clean(folder)))
        status, _hd, data = import_text(port, LANGFUSE_TEXT)
        self.assertEqual(status, 200, data)
        status, _hd, data = http_request(port, "POST", "/intervene",
                                         {"action_id": PENDING_ID, "decision": "approve",
                                          "reason": "确认"})
        self.assertEqual(status, 200, data)
        audit_file = folder / "audit.jsonl"
        audit_before = len(audit_events(audit_file))
        _st, _hd, before = http_request(port, "GET", sv.API_PATH_AUDIT)
        chain_before = int(dict(before.get("meta") or {}).get("count")
                           if isinstance(before, dict) else 0)

        status, _hd, data = http_request(port, "POST", sv.API_PATH_RESET, {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["records"], DEMO_COUNT)
        self.assertTrue(data["records_durable"])
        self.assertIn("演示", str(data["records_note"]))
        rows, meta = records_of(port)
        self.assertEqual(len(rows), DEMO_COUNT, "reset 清记录 → 回到演示种子")
        self.assertEqual(meta["imported_count"], 0)
        self.assertTrue(meta["records_durable"])

        self.assertGreaterEqual(len(audit_events(audit_file)), audit_before,
                                "审计只追加：reset 不删一行")
        _st, _hd, after = http_request(port, "GET", sv.API_PATH_AUDIT)
        chain_after = int(dict(after.get("meta") or {}).get("count")
                          if isinstance(after, dict) else 0)
        self.assertGreaterEqual(chain_after, chain_before)
        self.assertTrue(st.open_journal(folder / st.RECORD_FILE_NAME).load().ok)

    def test_reset_demo_flag_reseeds_at_startup(self) -> None:
        folder = room("reset-demo")
        store_file = folder / st.RECORD_FILE_NAME
        one = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit.jsonl")
        grab_import(one, {"payload": MCP_TEXT}, request_id="req-seed-1")
        self.assertEqual(len(one.store.all()), DEMO_COUNT + MCP_COUNT)
        two = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit.jsonl",
                                  reset_demo=True)
        self.addCleanup(lambda: clean(folder))
        rows = records_of_service(two)
        self.assertEqual(len(rows), DEMO_COUNT)
        self.assertEqual({r["id"] for r in rows}, {r["id"] for r in NATIVE_RECORDS})
        self.assertEqual(len(st.open_journal(store_file).load().records), DEMO_COUNT,
                         "--reset-demo 必须把日志一起清干净")
        third = sv.InterveneService(records_store_path=store_file,
                                    audit_log_path=folder / "audit-c.jsonl")
        self.assertEqual(len(third.store.all()), DEMO_COUNT)

    def test_memory_mode_forgets_everything_on_restart(self) -> None:
        srv, port = boot("memory-only", records_store_path=None)
        folder = srv._t22_room
        self.addCleanup(lambda: (srv.shutdown(), clean(folder)))
        status, _hd, data = import_text(port, LANGFUSE_TEXT)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["durable"])
        _rows, meta = records_of(port)
        self.assertFalse(meta["records_durable"])
        self.assertEqual(meta["records_store"]["mode"], "memory")
        _st, _hd, health = http_request(port, "GET", "/health")
        self.assertFalse(health["records_store"]["durable"])
        srv.shutdown()
        again, port2 = resume(folder, records_store_path=None,
                              audit_log_path=folder / "audit.jsonl")
        self.addCleanup(again.shutdown)
        rows2, _meta2 = records_of(port2)
        self.assertEqual(len(rows2), DEMO_COUNT, "纯内存模式重启回到演示种子（已知口径）")

    def test_corrupt_journal_is_reported_not_silently_repaired(self) -> None:
        folder = room("corrupt")
        store_file = folder / st.RECORD_FILE_NAME
        one = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit.jsonl")
        grab_import(one, {"payload": LANGFUSE_TEXT}, request_id="req-corrupt-1")
        with open(store_file, "ab") as handle:
            handle.write(b"\n{ broken json line\n")
        self.addCleanup(lambda: clean(folder))
        two = sv.InterveneService(records_store_path=store_file,
                                  audit_log_path=folder / "audit.jsonl")
        rows = records_of_service(two)
        self.assertGreaterEqual(len(rows), DEMO_COUNT + LANGFUSE_COUNT,
                                "坏行只跳过，其余记录照常装载")
        facts = two.store.store_facts()
        self.assertGreaterEqual(facts["problems"], 1, facts)
        self.assertIn(CODES["line_invalid"], {p["code"] for p in facts["problem_preview"]},
                      facts)
        self.assertTrue(facts["durable"])

    def test_concurrent_importers_from_three_processes(self) -> None:
        folder = room("concurrent")
        store_file = folder / st.RECORD_FILE_NAME
        services = [sv.InterveneService(records_store_path=store_file,
                                        audit_log_path=folder / f"audit-{i}.jsonl")
                    for i in range(3)]
        self.addCleanup(lambda: clean(folder))
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            svc = services[index]
            try:
                for k in range(3):
                    svc.import_traces({"payload": mcp_text(6, start=index * 100 + k * 10),
                                       "filename": f"p{index}-{k}.json"},
                                      request_id=f"req-cc-{index}-{k}")
            except BaseException as exc:      # pragma: no cover - 失败时看详情
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertFalse(errors, errors)
        loaded = st.open_journal(store_file).load()
        self.assertTrue(loaded.ok, loaded.as_dict())
        uploaded = {r["id"] for r in loaded.records if source_kind(r) == "upload"}
        self.assertEqual(len(uploaded), 54, "并发写入一行都不能丢")
        for svc in services:
            self.assertGreaterEqual(len(svc.store.all()), DEMO_COUNT + 6,
                                    "并发写入后每个进程都要看到彼此的结果")

    def test_cli_flags_docs_and_contract_all_agree(self) -> None:
        source = (REPO / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        for flag in ("--records-store", "--no-records-store",
                     "--records-store-lock", "--reset-demo"):
            self.assertIn(f'"{flag}"', source, f"命令行缺少 {flag}")
        docs = (REPO / "docs" / "03-architecture.md").read_text(encoding="utf-8")
        self.assertIn("/api/import", docs, "docs/03 必须写清导入入口")
        self.assertIn(st.RECORD_FILE_NAME, docs, "docs/03 必须写清持久化选型与默认路径")
        self.assertIn("--reset-demo", docs, "docs/03 必须写清 --reset-demo 的语义")
        self.assertIn("RECORD_STORE_WRITE_FAILED", docs, "docs/03 的码表必须有写不进去的码")
        contract = json.loads((REPO / "docs" / "api_contract.json").read_text(encoding="utf-8"))
        entry = [r for r in contract["routes"] if r.get("path") == "/api/import"]
        self.assertTrue(entry, "接口契约必须包含导入入口")
        self.assertEqual(entry[0]["verb"], "POST")
        self.assertEqual(entry[0]["abilities"], ["data.write"])
        self.assertTrue(entry[0]["csrf"], "导入是写操作，必须带 CSRF")
        self.assertIn(sv.API_PATH_IMPORT, contract["path_constants"].values(),
                      "路径常量必须进契约")
        self.assertIn("IMPORT_TOO_MANY", json.dumps(contract["error_codes"], ensure_ascii=False),
                      "导入的码必须在契约里")
        self.assertIn("RECORD_STORE_WRITE_FAILED",
                      json.dumps(contract["error_codes"], ensure_ascii=False),
                      "写不进去的码必须在契约里")
        self.assertTrue(contract["doc_crosscheck"]["contract_only_paths"] == [],
                        contract["doc_crosscheck"])
        self.assertTrue(scratch_reason(), "临时目录口径必须能说清")


class StorePurityTests(unittest.TestCase):
    """落盘是 `store.py` 的本职（`server.py` 被禁止写文件），所以纯净性要跟着搬过来：
    纯标准库、不出网、不起进程、不偷偷换成 SQLite（docs/03 §4 写的是 JSONL）。"""

    SOURCE = (REPO / "agent_explainer" / "store.py").read_text(encoding="utf-8")

    def test_no_network_or_process_apis(self) -> None:
        """`store.py` 的注释里写了"为什么不用 SQLite"，所以扫描只看**代码**：
        文档字符串里的中文解释不算出网，真正的 import / 属性调用才算。"""
        for token in ("http://", "https://", "urllib", "socket.", "subprocess",
                      "requests.", "httpx", "Popen", "os.system", "smtplib"):
            self.assertNotIn(token, self.SOURCE, f"store.py 含 {token}")
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.unparse(node)
                for token in ("urllib", "http", "socket", "subprocess", "sqlite3",
                              "apsw", "requests", "shelve", "dbm"):
                    self.assertNotIn(token, text, f"store.py 导入了 {token}：{text}")
            if isinstance(node, ast.Attribute):
                leaf = ast.unparse(node).split(".")[-1]
                self.assertNotIn(leaf, BANNED_STORE_CALLS,
                                 f"store.py 用了出网/起进程的 API：{ast.unparse(node)}")

    def test_imports_are_stdlib_only(self) -> None:
        tree = ast.parse(self.SOURCE)
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update((a.name or "").split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                mods.add((node.module or "").split(".")[0])
        extra = sorted(mods - STORE_STDLIB_OK)
        self.assertFalse(extra, f"store.py 引入了白名单外的依赖：{extra}")

    def test_public_api_surface_is_stable(self) -> None:
        """docs/03 第 4 节与 server.py 消费的就是这些名字：改名必须同步文档与文案。"""
        for name in ("RECORD_FILE_NAME", "STORE_VERSION", "OPS", "MAX_LINE_BYTES",
                     "LOCK_TIMEOUT_SECONDS", "default_store_path", "open_journal",
                     "RecordJournal", "JournalLoad", "_FileLock", "_self_check"):
            self.assertTrue(hasattr(st, name), f"store.py 缺公开面：{name}")
        self.assertEqual(st.RECORD_FILE_NAME, "records.jsonl")
        self.assertEqual(st.OPS, ("put", "clear", "header"))
        facts = st.open_journal(room("purity-api") / st.RECORD_FILE_NAME).stat()
        self.assertIn("path", facts)
        self.assertIn("appends", facts)
        self.assertIn("compactions", facts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
