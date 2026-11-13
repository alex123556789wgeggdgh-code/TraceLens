# -*- coding: utf-8 -*-
"""4.8（t21）审计查看与导出：GET /api/audit/export + 落盘前 PII 打码。

覆盖三块（验收口径逐条对得上）：

1. **导出端点**：默认 CSV、`format=html` 可打印、`format=pdf` 优先 reportlab /
   不可用时退回可打印 HTML 并说明、非法 format → 400 ``EXPORT_FORMAT_INVALID``、
   只读 GET（POST → 405）、HEAD 无正文、分页 ``limit``、过滤条件与
   ``GET /api/audit`` 同口径（枚举不合法 / 时间倒挂 → 400 ``AUDIT_QUERY_INVALID``）。
2. **权限与记账**：未登录 401；导出需要 ``audit.export`` 能力（把角色里这项拿掉就 403）；
   每次导出追加一条 ``event=export``，只写格式/条数/字节/过滤摘要/内容指纹，
   失败的导出不记账；链断 → 500 且审计文件字节数不变。
3. **PII 打码（QA 发现）**：邮箱 / 手机号 / 身份证 / 银行卡样式数字 / IPv4 / IPv6 /
   Windows 与 POSIX 绝对路径 / ``api_key=`` / Bearer / OpenAI 风格前缀 / AKIA /
   PEM 头，在**写盘之前**打掉；导出文件与审计文件里都查不到原值；
   幂等；密钥类字段与 ``client_info.ip`` 的既有脱敏口径不回退。

真端口 HTTP 冒烟由本文件负责（对齐 test_audit_integration 的写法）。
"""
from __future__ import annotations

import copy
import csv
import http.client
import io
import json
import pathlib
import shutil
import sys
import unittest
from typing import Any
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import audit as au  # noqa: E402
from agent_explainer import auth as auth_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
SAMPLE_PATH = ROOT / "samples" / "action_records.json"
NATIVE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"]
from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

TMP = scratch_dir("audit-export")

PENDING = "act-demo-0004"        # pending_confirmation，可决定（可编辑字段 max_move_count）
PENDING_2 = "act-demo-0005"      # pending_confirmation，可决定（可编辑字段 max_delete_count）
MODIFY_PARAMS = {"max_move_count": 2}        # act-demo-0004 的可编辑字段
MODIFY_PARAMS_2 = {"max_delete_count": 1}    # act-demo-0005 的可编辑字段
EXPORT = sv.API_PATH_AUDIT_EXPORT
CODE_FORMAT = "EXPORT_FORMAT_INVALID"
CODE_QUERY = "AUDIT_QUERY_INVALID"
CODE_BROKEN = "AUDIT_CHAIN_BROKEN"
CODE_FORBIDDEN = "ROLE_FORBIDDEN"
CODE_REQUIRED = "AUTH_REQUIRED"

#: PII 探针：只在测试里出现，断言它们**不得**出现在审计文件与导出内容里
PII_PROBES: tuple[str, ...] = (
    "alice.chen@corp-mail.example",     # 邮箱
    "13800138000",                      # 手机号
    "110101199001011234",               # 身份证样式（18 位）
    "6222021234567890123",              # 银行卡样式（19 位）
    "10.20.30.40",                      # IPv4
    "C:\\Users\\alice\\notes.txt",      # Windows 绝对路径
    "/home/alice/secrets/token.txt",    # POSIX 绝对路径
)
PII_SECRET = "sk-" + "supersecretvalue123456"     # 拆开拼，测试源码里也是完整值


# --------------------------------------------------------------------------- #
# 小工具（与 test_audit_integration 同一套写法）
# --------------------------------------------------------------------------- #

def setUpModule() -> None:      # noqa: N420 - unittest 约定
    TMP.mkdir(parents=True, exist_ok=True)


def tearDownModule() -> None:   # noqa: N420
    shutil.rmtree(TMP, ignore_errors=True)


def http_request(host: str, port: int, method: str, path: str, body: Any = None,
                 extra_headers: dict | None = None,
                 timeout: float = 8.0) -> tuple[int, dict, Any]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        headers = {"Connection": "close", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        data: Any = raw.decode("utf-8", "replace")
        if "json" in ctype:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def code_of(data: Any) -> str:
    return str(data.get("code") or "") if isinstance(data, dict) else ""


def detail_of(data: Any) -> dict:
    return dict(data.get("detail") or {}) if isinstance(data, dict) else {}


def read_lines(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def csv_rows(text: str) -> list[list[str]]:
    body = text[1:] if text.startswith("﻿") else text
    return [row for row in csv.reader(io.StringIO(body))]


def csv_dict(text: str) -> list[dict]:
    table = csv_rows(text)
    if len(table) < 2:
        return []
    head = table[0]
    return [dict(zip(head, row)) for row in table[1:]]


def entry_field(entry: dict, key: str) -> Any:
    """落盘条目的扩展字段：`build_event` 放顶层，`AuditLog` 镜像时收进 ``fields``。"""
    if key in entry:
        return entry[key]
    return dict(entry.get("fields") or {}).get(key)


class PiiAssertMixin:
    """PII 探针断言：这些原值不许出现在审计文件、CSV、HTML、PDF 里。"""

    def assert_no_pii(self, text: str, *, where: str) -> None:
        for probe in PII_PROBES + (PII_SECRET,):
            self.assertNotIn(probe, text, f"{where} 里出现了未脱敏的 PII：{probe}")


class ExportHttpCase(PiiAssertMixin, unittest.TestCase):
    """起真端口服务：传 ``audit_path`` 就是落盘模式（t18 口径）。"""

    def setUp(self) -> None:
        self._servers: list = []

    def tearDown(self) -> None:
        for srv, thread in self._servers:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
        self._servers = []

    def workspace(self, name: str) -> pathlib.Path:
        folder = TMP / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def start(self, name: str, *, audit_path: pathlib.Path | None = None,
              mode: str = sv.MODE_LOCAL, **over) -> tuple[Any, str, int, pathlib.Path]:
        folder = self.workspace(name)
        path = audit_path or (folder / au.AUDIT_FILE_NAME)
        kwargs: dict = dict(records=copy.deepcopy(NATIVE), web_dir=WEB,
                            allow_reset=True)
        kwargs.update(over)
        srv, thread = sv.start_background_server("127.0.0.1", 0, mode=mode,
                                                 audit_log_path=path, **kwargs)
        host, port = srv.server_address[:2]
        self._servers.append((srv, thread))
        return srv, host, port, path

    def team(self, name: str, **over):
        folder = self.workspace(name)
        srv, host, port, path = self.start(
            name, mode=sv.MODE_TEAM,
            auth_state_path=folder / auth_mod.STATE_FILE_NAME,
            bootstrap_admin=True, session_ttl=3600, **over)
        boot = srv.service.auth.bootstrap_report or {}
        return srv, host, port, path, str(boot.get("password") or "")

    def login(self, host: str, port: int, username: str, password: str) -> dict:
        st, _, data = http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                                   {"username": username, "password": password})
        self.assertEqual(st, 200, data)
        return {"cookie": f"{auth_mod.SESSION_COOKIE}={data['cookie']['value']}",
                "csrf": data["csrf_token"], "role": data["user"]["role"]}

    def decide(self, host: str, port: int, action_id: str, decision: str,
               **extra) -> tuple[int, dict, Any]:
        body = {"action_id": action_id, "decision": decision}
        body.update(extra)
        return http_request(host, port, "POST", sv.API_PATH_INTERVENE, body)

    def export(self, host: str, port: int, query: str = "",
               headers: dict | None = None) -> tuple[int, dict, Any]:
        return http_request(host, port, "GET", EXPORT + (query or ""),
                            extra_headers=headers)

    # -- 断言小工具 -------------------------------------------------------- #

    def seed_decisions(self, host: str, port: int) -> None:
        st, _, _ = self.decide(host, port, PENDING, "approve", reason="同意搬家")
        self.assertEqual(st, 200, "预置一次同意决定失败")
        st, _, _ = self.decide(host, port, PENDING_2, "reject", reason="风险太高，拒绝")
        self.assertEqual(st, 200, "预置一次拒绝决定失败")


class TestExportEndpoint(ExportHttpCase):
    """导出端点本体：格式、状态码、头、可解析性。"""

    def test_default_is_csv_with_attachment_and_header_row(self) -> None:
        _srv, host, port, _path = self.start("csv-default")
        status, headers, body = self.export(host, port)
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/csv"), headers)
        self.assertIn("attachment", headers.get("content-disposition", ""), headers)
        self.assertIn("tracelens-audit", headers.get("content-disposition", ""))
        self.assertTrue(body.startswith("﻿"), "CSV 带 BOM，Excel 才认中文")
        head = csv_rows(body)[0]
        for column in ("seq", "ts", "actor_id", "decision", "reason", "hash"):
            self.assertIn(column, head)

    def test_csv_contains_decisions_after_intervene(self) -> None:
        """验收点名：决定要在导出内容里看得见。"""
        srv, host, port, _path = self.start("csv-decisions")
        self.seed_decisions(host, port)
        status, headers, body = self.export(host, port, "?format=csv")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["x-tracelens-export-rows"]), 2, headers)
        decisions = [row["decision"] for row in csv_dict(body) if row["decision"]]
        self.assertIn("approve", decisions)
        self.assertIn("reject", decisions)
        rows = csv_dict(body)
        self.assertIn(PENDING, [row["action_id"] for row in rows])
        self.assertTrue(any("同意搬家" in row["reason"] for row in rows),
                        "理由列要能看见用户写的话")
        self.assertTrue(all(row["hash"].startswith("sha256:") for row in rows),
                        "每行都带链哈希，能回到原始文件取证")

    def test_export_sha256_header_matches_body(self) -> None:
        _srv, host, port, _path = self.start("csv-sha")
        status, headers, body = self.export(host, port)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-tracelens-export-sha256"],
                         au.sha256_ref(body)[:len(au.HASH_PREFIX) + 64])

    def test_csv_escapes_commas_quotes_and_newlines(self) -> None:
        srv, host, port, _path = self.start("csv-escape")
        reason = '他说"不能"，就一行\n第二行, 带逗号'
        st, _, _ = self.decide(host, port, PENDING, "approve", reason=reason)
        self.assertEqual(st, 200)
        _s, _h, body = self.export(host, port)
        rows = [r for r in csv_dict(body) if r["decision"] == "approve"]
        self.assertTrue(rows, "CSV 必须能原样解析回带逗号/引号/换行的理由")
        self.assertEqual(rows[0]["reason"], reason)

    def test_html_is_printable_and_script_free(self) -> None:
        srv, host, port, _path = self.start("html-print")
        self.seed_decisions(host, port)
        status, headers, body = self.export(host, port, "?format=html")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"), headers)
        self.assertIn("inline", headers.get("content-disposition", ""))
        self.assertIn("<!doctype html>", body)
        self.assertIn("哈希链校验", body)
        self.assertNotIn("<script", body.lower(), "可打印版不许带脚本")
        self.assertNotIn("http://", body, "可打印版零外链")
        self.assertIn("Ctrl+P", body, "要告诉普通人怎么存成 PDF")
        self.assertIn("approve", body)

    def test_html_empty_log_still_readable(self) -> None:
        _srv, host, port, _path = self.start("html-empty")
        status, _headers, body = self.export(host, port, "?format=html")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", body)
        self.assertIn("条目 0", body)

    def test_pdf_prefers_reportlab_and_falls_back_with_explanation(self) -> None:
        """reportlab 在 → 真 PDF；不在 → 可打印 HTML + 明确说明（不 500）。"""
        srv, host, port, _path = self.start("pdf-branch")
        self.seed_decisions(host, port)
        status, headers, body = self.export(host, port, "?format=pdf")
        self.assertEqual(status, 200, "reportlab 不可用也不能让人拿不到证据")
        served = headers.get("x-tracelens-export-format", "")
        if au._load_reportlab()[0] is None:
            self.assertEqual(served, "html", headers)
            self.assertIn("reportlab", headers.get("x-tracelens-export-fallback", ""),
                          headers)
            self.assertIn("<!doctype html>", body)
            self.assertIn("reportlab", body, "回退原因要写在页面上，普通人看得见")
        else:
            self.assertEqual(served, "pdf", headers)
            self.assertTrue(headers["content-type"].startswith("application/pdf"))
            self.assertTrue(body.encode("latin-1", "replace").startswith(b"%PDF"))
            # 没有中文字体时 PDF 会静默丢中文：这句话必须随文件一起给出，
            # 否则用户拿到一份"导出成功、中文没了"的证据
            probe_entry = {
                "seq": 1, "ts": "2026-11-01T08:00:00+08:00", "actor_id": "local-user",
                "action_id": "act-demo-0004", "decision": "approve", "reason": "同意搬家",
                "hash": au.sha256_ref("probe-1"), "prev_hash": au.sha256_ref("probe-0"),
            }
            _probe, probe_info = au.render_pdf_bytes([probe_entry], title="probe",
                                                     status={"ok": True, "count": 1})
            expect_note = str(probe_info.get("font_note") or "")
            note = headers.get("x-tracelens-export-note", "")
            if expect_note:
                self.assertTrue(note, "服务器知道缺中文字体，却没告诉调用方")
                self.assertTrue(note.isascii(), "响应头必须是 ASCII：" + repr(note))
            else:
                self.assertEqual(note, "", "没有字体警告就不要凭空给一行")

    def test_bad_format_is_400_export_format_invalid(self) -> None:
        _srv, host, port, _path = self.start("fmt-bad")
        status, _headers, data = self.export(host, port, "?format=xls")
        self.assertEqual(status, 400)
        self.assertEqual(code_of(data), CODE_FORMAT, data)
        for key in ("code", "message", "detail", "request_id"):
            self.assertIn(key, data, data)
        self.assertEqual(detail_of(data).get("field"), "format", data)
        self.assertEqual(detail_of(data).get("received"), "xls")
        self.assertEqual(list(detail_of(data).get("allowed") or []),
                         list(sv.AUDIT_EXPORT_FORMATS))

    def test_format_is_case_insensitive(self) -> None:
        _srv, host, port, _path = self.start("fmt-case")
        status, headers, body = self.export(host, port, "?format=CSV")
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-tracelens-export-format"], "csv")
        self.assertIn("seq", csv_rows(body)[0])

    def test_post_is_405_and_head_has_no_body(self) -> None:
        _srv, host, port, _path = self.start("fmt-methods")
        status, _h, data = http_request(host, port, "POST", EXPORT, {"format": "csv"})
        self.assertEqual(status, 405)
        self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED", data)
        status, headers, body = http_request(host, port, "HEAD", EXPORT)
        self.assertEqual(status, 200)
        self.assertEqual(body, "")
        self.assertEqual(headers.get("content-type", "").split(";")[0], "text/csv")

    def test_unknown_audit_subpath_still_404(self) -> None:
        """新端点不能把 /api/audit/* 的 404 口径带坏。"""
        _srv, host, port, _path = self.start("audit-404")
        status, _h, data = http_request(host, port, "GET", "/api/audit/nope")
        self.assertEqual(status, 404, data)

    def test_limit_caps_rows(self) -> None:
        srv, host, port, _path = self.start("csv-limit")
        self.seed_decisions(host, port)
        status, headers, body = self.export(host, port, "?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["x-tracelens-export-rows"]), 1, headers)
        self.assertEqual(len(csv_dict(body)), 1)

    def test_export_writes_no_files_into_the_workspace(self) -> None:
        """server.py 零文件写入：导出只在内存里排版。"""
        folder = self.workspace("csv-clean")
        srv, host, port, path = self.start("csv-clean", audit_path=folder / "a.jsonl")
        self.seed_decisions(host, port)
        before = sorted(p.name for p in folder.iterdir())
        self.export(host, port, "?format=csv")
        self.export(host, port, "?format=html")
        self.export(host, port, "?format=pdf")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), before)
        self.assertEqual(srv.service.exports_served, 3)

    def test_chain_broken_blocks_export_and_file_is_untouched(self) -> None:
        srv, host, port, path = self.start("csv-broken")
        self.seed_decisions(host, port)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        entry = json.loads(lines[0])
        entry["reason"] = "有人改了历史"
        lines[0] = json.dumps(entry, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        path.write_bytes(raw)
        size_before = path.stat().st_size
        status, _h, data = self.export(host, port)
        self.assertEqual(status, 500, data)
        self.assertEqual(code_of(data), CODE_BROKEN, data)
        self.assertEqual(path.stat().st_size, size_before, "断链时审计文件一个字节都不许动")


class TestExportFilters(ExportHttpCase):
    """过滤条件：与 GET /api/audit 同一套键与错误口径。"""

    def setUp(self) -> None:
        super().setUp()
        self.srv, self.host, self.port, self.path = self.start("filters")
        self.seed_decisions(self.host, self.port)
        self.export(self.host, self.port)      # 造一条 export 事件

    def rows(self, query: str) -> list[dict]:
        status, _headers, body = self.export(self.host, self.port, query)
        self.assertEqual(status, 200, body)
        return csv_dict(body)

    def test_decision_filter(self) -> None:
        rows = self.rows("?decision=reject")
        self.assertTrue(rows)
        self.assertEqual({r["decision"] for r in rows if r["decision"]}, {"reject"})
        self.assertNotIn(PENDING, [r["action_id"] for r in rows])

    def test_actor_and_action_filters(self) -> None:
        rows = self.rows(f"?action_id={PENDING}")
        self.assertTrue(rows)
        self.assertEqual({r["action_id"] for r in rows if r["action_id"]}, {PENDING})
        rows = self.rows("?actor_id=nobody-here")
        self.assertEqual(rows, [])

    def test_event_filter_finds_the_export_entry(self) -> None:
        rows = self.rows("?event=export")
        self.assertTrue(rows, "导出这件事本身要能查回来")
        self.assertEqual({r["event"] for r in rows}, {"export"})

    def test_time_window(self) -> None:
        today = au.iso_utc(au.utc_now())[:10]
        rows = self.rows(f"?since={today}T00:00:00Z")
        self.assertTrue(rows)
        rows = self.rows("?since=2000-01-01T00:00:00Z&until=2001-01-01T00:00:00Z")
        self.assertEqual(rows, [])

    def test_inverted_time_range_is_400(self) -> None:
        status, _h, data = self.export(self.host, self.port,
                                       "?since=2030-01-01T00:00:00Z&until=2020-01-01T00:00:00Z")
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), CODE_QUERY, data)

    def test_bad_enum_is_400(self) -> None:
        for query in ("?decision=maybe", "?event=not-an-event"):
            status, _h, data = self.export(self.host, self.port, query)
            self.assertEqual(status, 400, data)
            self.assertEqual(code_of(data), CODE_QUERY, data)

    def test_keyword_filter(self) -> None:
        rows = self.rows("?q=" + quote("风险太高"))
        self.assertTrue(rows)
        self.assertTrue(all("风险太高" in r["reason"] for r in rows
                            if r.get("reason")))

    def test_filters_are_reported_in_header(self) -> None:
        status, headers, _body = self.export(self.host, self.port,
                                             "?decision=approve&limit=3")
        self.assertEqual(status, 200)
        self.assertIn("decision=approve", headers.get("x-tracelens-export-filters", ""))
        self.assertIn("limit=3", headers.get("x-tracelens-export-filters", ""))


class TestExportPermission(ExportHttpCase):
    """权限与记账：audit.export 是唯一强制点。"""

    def test_team_mode_requires_login(self) -> None:
        _srv, host, port, _path, _pw = self.team("perm-anon")
        status, _h, data = self.export(host, port)
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), CODE_REQUIRED, data)

    def test_viewer_can_export_because_the_matrix_grants_audit_export(self) -> None:
        """docs/01 §6.4：查看者"只能看和导出" → 导出对 viewer 是放行的（干预才隐藏）。"""
        srv, host, port, _path, _pw = self.team("perm-viewer")
        srv.service.auth.create_user("viewer1", "viewer", "V1ew-pass-2026!")
        session = self.login(host, port, "viewer1", "V1ew-pass-2026!")
        self.assertNotIn("intervene.decide", auth_mod.ROLE_PERMISSIONS["viewer"])
        self.assertIn("audit.export", auth_mod.ROLE_PERMISSIONS["viewer"])
        status, headers, body = self.export(host, port, "?format=csv",
                                            headers={"Cookie": session["cookie"]})
        self.assertEqual(status, 200, body)
        self.assertIn("seq", csv_rows(body)[0])

    def test_role_without_audit_export_gets_403(self) -> None:
        """把 audit.export 从角色表里拿掉 → 立刻 403：证明这一条真的由能力判定，
        不是写死的角色名（路由里不许出现 `if role ==` 的判断）。"""
        srv, host, port, _path, _pw = self.team("perm-strip")
        srv.service.auth.create_user("viewer2", "viewer", "V2ew-pass-2026!")
        session = self.login(host, port, "viewer2", "V2ew-pass-2026!")
        original = dict(auth_mod.ROLE_PERMISSIONS)
        value = original["viewer"]
        kind = type(value)
        try:
            auth_mod.ROLE_PERMISSIONS["viewer"] = kind(
                [x for x in value if x != "audit.export"])
            status, _h, data = self.export(host, port, headers={"Cookie": session["cookie"]})
            self.assertEqual(status, 403, data)
            self.assertEqual(code_of(data), CODE_FORBIDDEN, data)
            self.assertEqual(detail_of(data).get("permission"), "audit.export", data)
        finally:
            auth_mod.ROLE_PERMISSIONS.clear()
            auth_mod.ROLE_PERMISSIONS.update(original)
        status, _h, body = self.export(host, port, headers={"Cookie": session["cookie"]})
        self.assertEqual(status, 200, body)

    def test_editor_and_admin_can_export(self) -> None:
        srv, host, port, _path, _pw = self.team("perm-editor")
        srv.service.auth.create_user("editor1", "editor", "Ed1tor-pass-2026!")
        session = self.login(host, port, "editor1", "Ed1tor-pass-2026!")
        status, _h, body = self.export(host, port, "?format=html",
                                       headers={"Cookie": session["cookie"]})
        self.assertEqual(status, 200, body)
        self.assertIn("<!doctype html>", body)

    def test_each_export_appends_one_export_event(self) -> None:
        srv, host, port, path = self.start("book-exports")
        self.seed_decisions(host, port)
        base = len(read_lines(path))
        self.export(host, port, "?format=csv")
        self.export(host, port, "?format=html&decision=approve")
        entries = read_lines(path)
        self.assertEqual(len(entries), base + 2, "导出两次 = 记两条")
        exports = [e for e in entries if e.get("event") == "export"]
        self.assertEqual(len(exports), 2)
        for entry in exports:
            self.assertEqual(entry.get("actor_id"), sv.LOCAL_ACTOR_ID)
            self.assertIn("导出审计证据", str(entry.get("reason") or ""))
        formats = {entry_field(e, "export_format") for e in exports}
        self.assertIn("csv", formats)
        self.assertIn("html", formats)

    def test_export_event_records_filters_not_secrets(self) -> None:
        srv, host, port, path = self.start("book-filters")
        self.seed_decisions(host, port)
        self.export(host, port, "?decision=reject&actor_id=local-user")
        entry = [e for e in read_lines(path) if e.get("event") == "export"][-1]
        filters = dict(entry_field(entry, "export_filters") or {})
        self.assertEqual(filters.get("decision"), "reject")
        self.assertEqual(filters.get("actor_id"), "local-user")
        self.assertIn("decision=reject", str(entry.get("reason")))
        self.assertTrue(str(entry_field(entry, "export_sha256") or "").startswith("sha256:"))
        self.assertIsInstance(entry_field(entry, "export_rows"), int)
        self.assertNotIn("reason", filters, "过滤摘要里不塞理由原文")

    def test_failed_export_is_not_booked(self) -> None:
        srv, host, port, path = self.start("book-fail")
        self.seed_decisions(host, port)
        base = len(read_lines(path))
        self.export(host, port, "?format=xls")
        self.export(host, port, "?decision=maybe")
        self.assertEqual(len(read_lines(path)), base, "失败的导出不记账")

    def test_health_advertises_the_export_endpoint(self) -> None:
        _srv, host, port, _path = self.start("book-health")
        status, _h, data = http_request(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertEqual(status, 200)
        self.assertIn(EXPORT, data["endpoints"])
        block = data["audit_export"]
        self.assertEqual(block["endpoint"], EXPORT)
        self.assertEqual(block["method"], "GET")
        self.assertEqual(block["permission"], "audit.export")
        self.assertEqual(list(block["formats"]), list(sv.AUDIT_EXPORT_FORMATS))


class TestPiiRedaction(PiiAssertMixin, unittest.TestCase):
    """落盘前 PII 打码：单元级（audit.redact_text）+ 真落盘 + 导出链路。"""

    def mask_all(self, text: str) -> str:
        return au.redact_text(text)

    def test_each_pattern_is_masked(self) -> None:
        cases = {
            "邮箱": ("请联系 alice.chen@corp-mail.example 确认", "alice.chen@corp-mail.example"),
            "手机号": ("手机 13800138000 可联系", "13800138000"),
            "身份证": ("身份证 110101199001011234 已核验", "110101199001011234"),
            "银行卡": ("卡号 6222021234567890123", "6222021234567890123"),
            "IPv4": ("来源 10.20.30.40 已放行", "10.20.30.40"),
            "IPv6": ("来源 fe80:0000:0000:0000:0000:0000:0000:1 已放行",
                     "fe80:0000:0000:0000:0000:0000:0000:1"),
            "Windows 路径": ("文件在 C:\\Users\\alice\\notes.txt 里", "C:\\Users\\alice\\notes.txt"),
            "POSIX 路径": ("文件在 /home/alice/secrets/token.txt 里",
                           "/home/alice/secrets/token.txt"),
            "api_key 赋值": ("配置里 api_key=abc123456789 已保存", "abc123456789"),
            "Bearer": ("带 Bearer abcdefghijklmnop 请求", "abcdefghijklmnop"),
            "OpenAI 前缀": ("密钥 " + PII_SECRET + " 泄漏了", PII_SECRET),
            "AKIA": ("aws 用 AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
            "PEM": ("-----BEGIN RSA PRIVATE KEY----- MIIBV... -----END RSA PRIVATE KEY-----",
                    "-----BEGIN RSA PRIVATE KEY-----"),
        }
        for name, (text, secret) in cases.items():
            masked = self.mask_all(text)
            self.assertNotIn(secret, masked, f"{name} 没被打码：{masked}")

    def test_mask_keeps_readability(self) -> None:
        masked = self.mask_all("联系 a.chen@corp.example，手机 13800138000，IP 10.20.30.40")
        self.assertIn("a***@corp.example", masked, "邮箱留首字母与域名，客服还能核对")
        self.assertIn("138****8000", masked, "手机号留前 3 后 4（138****8000 这种写法）")
        self.assertIn("10.20.*.*", masked, "IP 只到 /16")

    def test_idempotent(self) -> None:
        text = ("邮箱 a@corp.example 手机 13800138000 身份证 110101199001011234 "
                "IP 10.20.30.40 路径 C:\\Users\\a\\x.txt 与 /home/a/x " + PII_SECRET)
        once = self.mask_all(text)
        self.assertEqual(once, self.mask_all(once), "重复打码不得套娃")

    def test_stable_fingerprint_for_the_same_value(self) -> None:
        a = self.mask_all("路径 C:\\Users\\alice\\notes.txt")
        b = self.mask_all("路径 C:\\Users\\alice\\notes.txt")
        self.assertEqual(a, b, "同一原值指纹一致，取证时能对齐")
        self.assertIn("<redacted:path#sha256:", a)

    def test_ordinary_text_untouched(self) -> None:
        text = "搬家方案 A：先打包书房，预算 3 件，预计 2 小时（上限 100 元）"
        self.assertEqual(self.mask_all(text), text, "普通人写的话不许被啃掉")

    def test_build_event_masks_free_text(self) -> None:
        payload = au.build_event("decision", actor_id="alice", decision="approve",
                                 reason="联系 alice.chen@corp-mail.example，手机 13800138000",
                                 note="路径 C:\\Users\\alice\\notes.txt",
                                 changed_fields=[{"name": "contact",
                                                  "before": "13800138000",
                                                  "after": "alice.chen@corp-mail.example"}])
        self.assert_no_pii(au.canonical_json(payload), where="审计载荷")

    def test_durable_file_has_no_pii(self) -> None:
        folder = TMP / "pii-file"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        log = au.open_log(folder / "audit.jsonl")
        log.append(au.build_event(
            "decision", actor_id="alice", decision="approve", action_id="act-demo-0004",
            reason=(f"邮箱 {PII_PROBES[0]} 手机 {PII_PROBES[1]} 身份证 {PII_PROBES[2]} "
                    f"IP {PII_PROBES[4]} 路径 {PII_PROBES[5]} 与 {PII_PROBES[6]} "
                    f"密钥 {PII_SECRET}"),
            original_params={"contact": PII_PROBES[0], "count": 3},
            client_info={"channel": "web", "ip": "10.20.30.40", "user_agent": "Mozilla/5.0"}))
        text = (folder / "audit.jsonl").read_text(encoding="utf-8")
        self.assert_no_pii(text, where="审计文件")
        self.assertIn("a***@corp-mail.example", text)
        self.assertIn("<redacted:path#", text)
        # 既有脱敏口径不回退：client_info 的 IP 仍是网段口径（t9 冻结）
        entry = json.loads(text.splitlines()[-1])
        self.assertEqual(entry["client_info"]["ip"], "10.20.30.*",
                         "client_info 的 IP 仍是网段口径（t9 冻结）")
        self.assertNotIn("10.20.30.40", json.dumps(entry["client_info"]))

    def test_sensitive_key_redaction_does_not_regress(self) -> None:
        payload = au.build_event(
            "note", actor_id="alice",
            original_params={"api_key": PII_SECRET, "token": "abcdefghijklmnop",
                             "email": PII_PROBES[0], "phone": PII_PROBES[1],
                             "count": 3},
            redact=True)
        params = dict(payload["original_params"] or {})
        self.assertTrue(str(params["api_key"]).startswith("<redacted:api_key#sha256:"),
                        params)
        self.assertTrue(str(params["token"]).startswith("<redacted:token#sha256:"), params)
        self.assertEqual(params["email"], "a***@corp-mail.example")
        phone = str(params["phone"])
        self.assertTrue(phone.startswith("138") and phone.endswith("00")
                        and "*" in phone, phone)
        self.assertNotIn(PII_PROBES[1], phone)
        self.assertEqual(params["count"], 3)

    def test_redact_false_stays_raw_for_the_explicit_preview_mode(self) -> None:
        payload = au.build_event("note", actor_id="alice", reason=f"邮箱 {PII_PROBES[0]}",
                                 redact=False)
        self.assertIn(PII_PROBES[0], str(payload["reason"]),
                      "显式不脱敏的预览口径仍然原样（写盘路径默认是脱敏的）")


class TestPiiThroughHttp(ExportHttpCase):
    """验收点名：导出内容含决定、不含密钥与未脱敏 PII。"""

    def test_intervene_pii_never_reaches_disk_or_export(self) -> None:
        srv, host, port, path = self.start("pii-http")
        reason = (f"邮箱 {PII_PROBES[0]}，手机 {PII_PROBES[1]}，"
                  f"身份证 {PII_PROBES[2]}，IP {PII_PROBES[4]}，路径 {PII_PROBES[5]}")
        st, _, data = self.decide(host, port, PENDING, "approve", reason=reason)
        self.assertEqual(st, 200, data)
        st, _, data = self.decide(host, port, PENDING_2, "modify",
                                  modified_params=dict(MODIFY_PARAMS_2),
                                  reason=f"改成 {PII_PROBES[1]} 这台机器")
        self.assertEqual(st, 200, data)
        disk = path.read_text(encoding="utf-8")
        self.assert_no_pii(disk, where="落盘审计")
        for query in ("?format=csv", "?format=html", "?format=pdf"):
            status, _h, body = self.export(host, port, query)
            self.assertEqual(status, 200, body)
            self.assert_no_pii(body, where=f"导出 {query}")
        self.assertIn("approve", str(self.export(host, port)[2]))

    def test_recorded_reason_stays_human_readable(self) -> None:
        srv, host, port, _path = self.start("pii-readable")
        self.decide(host, port, PENDING, "approve",
                    reason=f"搬家预算已确认，联系人 a.chen@corp.example，手机 13800138000")
        _s, _h, body = self.export(host, port, "?format=csv")
        row = [r for r in csv_dict(body) if r["decision"] == "approve"][-1]
        self.assertIn("搬家预算已确认", row["reason"])
        self.assertIn("a***@corp.example", row["reason"])
        self.assertIn("138****8000", row["reason"])

    def test_modify_field_values_are_masked_in_csv(self) -> None:
        """改参数的理由里带 PII：CSV 的 changed_fields / reason 列都不许漏出原值。"""
        srv, host, port, _path = self.start("pii-changed")
        self.decide(host, port, PENDING, "modify",
                    modified_params=dict(MODIFY_PARAMS),
                    reason=f"改成 1 件，通知 {PII_PROBES[1]}（{PII_PROBES[0]}）")
        _s, _h, body = self.export(host, port, "?format=csv")
        self.assert_no_pii(body, where="CSV 的 changed_fields / reason 列")
        self.assertIn("max_move_count", body, "脱敏不能把业务字段一起吃掉")
        rows = [r for r in csv_dict(body) if r["decision"] == "modify"]
        self.assertTrue(rows)
        self.assertIn("138****8000", rows[-1]["reason"])

    def test_export_of_a_memory_mode_service_works(self) -> None:
        """内存链模式（t8 预览口径）也要能导出：QA 的本地脚本跑这条路。"""
        service = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                      audit_log_path=None)
        service.intervene({"action_id": PENDING, "decision": "approve",
                           "reason": f"邮箱 {PII_PROBES[0]}"}, request_id="req-1")
        result = service.audit_export(fmt="csv")
        self.assertEqual(result["format"], "csv")
        self.assert_no_pii(result["body"].decode("utf-8"), where="内存模式 CSV")
        self.assertIn("approve", result["body"].decode("utf-8"))
        self.assertEqual(result["chain"]["durable"], False)
        html = service.audit_export(fmt="pdf")
        self.assertIn(html["format"], ("pdf", "html"))
        self.assertIn("font_note", html, "PDF 的字体警告要跟着结果一起返回")
        self.assertIsInstance(html["font_note"], str)
        header_keys = {str(k).lower() for k in html["headers"]}
        if html["font_note"]:
            self.assertIn("x-tracelens-export-note", header_keys,
                          "有字体警告却没进响应头，界面上就没人看得见")
            self.assertTrue(html["headers"]["X-TraceLens-Export-Note"].isascii())
        else:
            self.assertNotIn("x-tracelens-export-note", header_keys)
        events = [e.get("event") for e in service.audit.entries()]
        self.assertIn("audit.export", events)

    def test_memory_mode_filters_and_errors(self) -> None:
        service = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                      audit_log_path=None)
        service.intervene({"action_id": PENDING, "decision": "approve",
                           "reason": "同意"}, request_id="req-2")
        rows = service.audit_export(fmt="csv", filters={"decision": "approve"})
        self.assertGreaterEqual(rows["rows"], 1)
        empty = service.audit_export(fmt="csv", filters={"actor_id": "nobody"})
        self.assertEqual(empty["rows"], 0)
        for bad in ({"decision": "maybe"}, {"event": "nope"},
                    {"since": "2030-01-01T00:00:00Z", "until": "2020-01-01T00:00:00Z"}):
            with self.assertRaises(sv.InterveneError) as ctx:
                service.audit_export(fmt="csv", filters=bad)
            self.assertEqual(ctx.exception.code, CODE_QUERY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
