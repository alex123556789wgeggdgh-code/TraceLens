"""t18 [t4-2b] 验收测试：追加式审计真正接进产品服务（JSONL 文件链）。

运行（全程离线，只用标准库 unittest；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_audit_integration.py

覆盖任务验收点：
  1. 审计后端从内存切成 `audit.py` 的追加式 JSONL 链：`/intervene` 每次成功决定追加一条，
     条目带 t8 的 `entry_hash` 恒等别名（== `hash`），首条挂在 genesis 上；
  2. 启动即加载并 verify 整条链（含 seal 锚点）：断链时 `/health` 的 `audit.ok=false`
     且 stderr 有一句人话；`/api/audit` 返回 500 + 可读原因；
     服务端**不**清空、**不**截断、**不**重建（文件字节一字不变）；
  3. `GET /api/audit` 读文件链：返回 `entries` + `chain` + `meta`（count/head/sealed）；
     支持 limit/offset 与 event/actor_id/action_id/decision/q 过滤，参数非法 → 400；
  4. append-only：没有任何更新/删除/清空端点（DELETE/PATCH/PUT/POST 全 405，未知子路径 404）；
     `--no-reset` 时 `/api/reset` 连审计文件一个字节都不碰；允许 reset 时只追加一条
     `record_reset`；
  5. 重启后历史仍在、count 不归零、seq 续接；幂等重放不重复记账；
  6. 4.3 的会话身份写进审计（team 模式 actor_id=用户名、actor_role 映射、mode=live），
     local 模式仍是 `local-user`；鉴权事件也进文件链，且口令不进审计文件；
  7. 队长裁决落测试：`audit.EVENTS` 白名单已含 `auth.*`，历史 JSONL 仍保留
     `access_denied`/`note` + `fields.auth_event` 映射；
  8. 内存模式（不传 audit_log_path）保持 t8 口径逐字不变。

临时文件只写在系统临时目录（`tests/scratch.py` 的 `scratch_dir`，模块级 tearDown 整目录删除）。
"""
from __future__ import annotations

import copy
import http.client
import io
import json
import contextlib
import pathlib
import shutil
import sys
import unittest

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

TMP = scratch_dir("audit-integration")

PENDING = "act-demo-0004"            # pending_confirmation，可决定
PENDING_2 = "act-demo-0005"          # pending_confirmation，可决定
PENDING_3 = "act-demo-0008"          # pending_confirmation（要求 admin；local 等价 admin）
MODIFY_PARAMS = {"max_move_count": 2}   # 该记录唯一可编辑字段
HASH_PREFIX = "sha256:"
AUDIT_NAME = "audit.jsonl"
CODE_BROKEN = "AUDIT_CHAIN_BROKEN"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def setUpModule() -> None:      # noqa: N802 - unittest 约定
    TMP.mkdir(parents=True, exist_ok=True)


def tearDownModule() -> None:   # noqa: N802
    shutil.rmtree(TMP, ignore_errors=True)


def http_request(host: str, port: int, method: str, path: str, body=None,
                 extra_headers: dict | None = None,
                 timeout: float = 8.0) -> tuple[int, dict, object]:
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
        data: object = raw.decode("utf-8", "replace")
        if "json" in (resp.getheader("Content-Type") or ""):
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def code_of(data: object) -> str:
    return str(data.get("code") or "") if isinstance(data, dict) else ""


def detail_of(data: object) -> dict:
    return dict(data.get("detail") or {}) if isinstance(data, dict) else {}


def read_lines(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tamper_line(path: pathlib.Path, index: int = 0, **fields) -> None:
    """改一条已落盘的审计条目（模拟"有人动了历史"）。"""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry = json.loads(lines[index])
    entry.update(fields)
    lines[index] = json.dumps(entry, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_chain(path: pathlib.Path, count: int = 2) -> None:
    """用 audit.py 自己写几条合法条目（伪造"已有历史"的场景）。"""
    log = au.open_log(path)
    for i in range(count):
        log.append(au.build_event("note", actor_id="seed-user", note=f"预置第 {i + 1} 条"))


class AuditHttpCase(unittest.TestCase):
    """起真端口服务 + 收尾；`audit_path` 传下去就是落盘模式。"""

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
              mode: str = sv.MODE_LOCAL, allow_reset: bool = True, **over
              ) -> tuple[sv.TraceLensServer, int, int, pathlib.Path]:
        folder = self.workspace(name)
        path = audit_path or (folder / AUDIT_NAME)
        kwargs: dict = dict(records=copy.deepcopy(NATIVE), web_dir=WEB,
                            allow_reset=allow_reset)
        kwargs.update(over)
        srv, thread = sv.start_background_server("127.0.0.1", 0, mode=mode,
                                                 audit_log_path=path, **kwargs)
        host, port = srv.server_address[:2]
        self._servers.append((srv, thread))
        return srv, host, port, path

    def call(self, host: str, port: int, method: str, path: str, body=None,
             headers: dict | None = None):
        return http_request(host, port, method, path, body, headers)

    def intervene(self, host: str, port: int, action_id: str, decision: str,
                  **extra) -> tuple[int, dict, object]:
        body = {"action_id": action_id, "decision": decision}
        body.update(extra)
        return http_request(host, port, "POST", sv.API_PATH_INTERVENE, body)

    def login(self, host: str, port: int, username: str, password: str) -> dict:
        st, _, data = http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                                   {"username": username, "password": password})
        self.assertEqual(st, 200, data)
        cookie = f"{auth_mod.SESSION_COOKIE}={data['cookie']['value']}"
        return {"cookie": cookie, "csrf": data["csrf_token"], "role": data["user"]["role"]}

    def team_server(self, name: str, **over):
        folder = self.workspace(name)
        srv, host, port, path = self.start(
            name, mode=sv.MODE_TEAM,
            auth_state_path=folder / auth_mod.STATE_FILE_NAME,
            bootstrap_admin=True, session_ttl=3600, **over)
        boot = srv.service.auth.bootstrap_report or {}
        return srv, host, port, path, str(boot.get("password") or "")

    # -- 常用断言 ---------------------------------------------------------- #

    def assert_unified_error(self, status: int, data: object, code: str,
                             min_status: int = 400) -> dict:
        self.assertEqual(code_of(data), code, data)
        self.assertGreaterEqual(status, min_status, data)
        self.assertIsInstance(data, dict)
        for key in ("code", "message", "detail", "request_id"):
            self.assertIn(key, data, data)
        return detail_of(data)


# --------------------------------------------------------------------------- #
# 1. 落盘后端：写、读、别名、链形
# --------------------------------------------------------------------------- #

class DurableWriteTests(AuditHttpCase):

    def test_startup_reports_durable_chain_before_any_entry(self) -> None:
        srv, host, port, path = self.start("durable-start")
        self.assertEqual(srv.service.audit_source, sv.AUDIT_SOURCE_JSONL)
        self.assertFalse(path.exists(), "启动不该预先造一个空审计文件")
        st, _, health = self.call(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertEqual(st, 200, health)
        audit = health["audit"]
        self.assertIs(audit["durable"], True)
        self.assertEqual(audit["source"], sv.AUDIT_SOURCE_JSONL)
        self.assertIs(audit["ok"], True, audit)
        self.assertEqual(audit["count"], 0)
        self.assertEqual(audit["head"], sv.GENESIS_HASH)
        self.assertEqual(audit["sealed_head"], sv.GENESIS_HASH)
        self.assertEqual(audit["path"], str(path))

    def test_intervene_appends_one_entry_to_the_file(self) -> None:
        srv, host, port, path = self.start("durable-write")
        st, hd, data = self.intervene(host, port, PENDING, "approve")
        self.assertEqual(st, 200, data)
        lines = read_lines(path)
        self.assertEqual(len(lines), 1)
        entry = lines[0]
        self.assertEqual(entry["event"], "decision")
        self.assertEqual(entry["action_id"], PENDING)
        self.assertEqual(entry["decision"], "approve")
        self.assertEqual(entry["actor_id"], sv.LOCAL_ACTOR_ID, "4.1 口径：本地决定人 local-user")
        self.assertEqual(entry["mode"], "local")
        self.assertEqual(entry["seq"], 1)
        self.assertEqual(entry["prev_hash"], au.GENESIS_HASH)
        self.assertEqual(entry["entry_hash"], entry["hash"], "t8 的恒等别名必须保留")
        self.assertTrue(str(entry["hash"]).startswith(HASH_PREFIX))
        # t8 口径不变：响应头与记录里写的是**内存链**条目哈希（t11 审计页两条都读得到）
        self.assertTrue(str(hd.get("x-intervene-audit-hash") or "").startswith(HASH_PREFIX))
        self.assertEqual(hd.get("x-intervene-audit-hash"),
                         data["user_intervention"]["audit_entry_hash"])

    def test_api_audit_reads_the_file_chain(self) -> None:
        srv, host, port, path = self.start("durable-read")
        self.intervene(host, port, PENDING, "approve")
        self.intervene(host, port, PENDING_2, "reject", reason="先不要发送")
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 200, audit)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])
        meta = audit["meta"]
        self.assertIs(meta["durable"], True)
        self.assertEqual(meta["source"], sv.AUDIT_SOURCE_JSONL)
        self.assertEqual(meta["count"], 2)
        self.assertEqual(meta["total"], 2)
        self.assertEqual(len(audit["entries"]), 2)
        self.assertEqual(audit["entries"][0]["seq"], 1)
        self.assertEqual(audit["entries"][1]["seq"], 2)
        self.assertEqual(audit["entries"][1]["prev_hash"], audit["entries"][0]["hash"])
        self.assertEqual(meta["head"], audit["entries"][1]["hash"])
        self.assertIn("sealed", meta)
        self.assertEqual(set(meta["sealed"].keys()),
                         {"sealed_head", "sealed_count", "seals"})
        self.assertEqual(audit["jsonl"]["count"], 2)
        self.assertEqual(audit["jsonl"]["ok"], True)

    def test_replay_does_not_double_bookkeep(self) -> None:
        srv, host, port, path = self.start("durable-idem")
        key = "idem-t18-0001"
        st1, hd1, d1 = self.intervene(host, port, PENDING, "approve", idempotency_key=key)
        st2, hd2, d2 = self.intervene(host, port, PENDING, "approve", idempotency_key=key)
        self.assertEqual(st1, 200, d1)
        self.assertEqual(st2, 200, d2)
        self.assertEqual(hd1.get("x-intervene-replay"), "false")
        self.assertEqual(hd2.get("x-intervene-replay"), "true")
        self.assertEqual(len(read_lines(path)), 1, "重放不得再记一笔")
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(audit["meta"]["count"], 1, audit["meta"])

    def test_three_decisions_chain_in_order(self) -> None:
        srv, host, port, path = self.start("durable-chain")
        self.intervene(host, port, PENDING, "approve")
        self.intervene(host, port, PENDING_2, "reject", reason="先不要发送")
        self.intervene(host, port, PENDING_3, "approve")
        lines = read_lines(path)
        self.assertEqual(len(lines), 3)
        self.assertEqual([e["seq"] for e in lines], [1, 2, 3])
        self.assertEqual([e["decision"] for e in lines], ["approve", "reject", "approve"])
        self.assertEqual(lines[0]["prev_hash"], au.GENESIS_HASH)
        self.assertEqual(lines[1]["prev_hash"], lines[0]["hash"])
        self.assertEqual(lines[2]["prev_hash"], lines[1]["hash"])
        report = au.verify_file(path)
        self.assertTrue(report["ok"], report["problems"])


# --------------------------------------------------------------------------- #
# 2. 重启保留、reset 纪律
# --------------------------------------------------------------------------- #

class RestartAndResetTests(AuditHttpCase):

    def test_restart_keeps_history_and_count_does_not_reset(self) -> None:
        srv, host, port, path = self.start("restart-a")
        self.intervene(host, port, PENDING, "approve")
        self.intervene(host, port, PENDING_2, "reject", reason="先不要发送")
        first = read_lines(path)
        srv.shutdown()
        srv.server_close()

        srv2, host2, port2, path2 = self.start("restart-b", audit_path=path)
        self.assertEqual(path2, path, "同一份审计文件才叫重启")
        st, _, health = http_request(host2, port2, "GET", sv.API_PATH_HEALTH)
        self.assertEqual(health["audit"]["count"], 2, health["audit"])
        self.assertTrue(health["audit"]["ok"], health["audit"])
        st, _, audit = http_request(host2, port2, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(audit["meta"]["count"], 2)
        self.assertEqual([e["seq"] for e in audit["entries"]], [1, 2])
        self.assertEqual(audit["meta"]["head"], first[-1]["hash"])
        # 重启后接着写：seq 续接，历史仍在
        self.intervene(host2, port2, PENDING_3, "approve")
        after = read_lines(path)
        self.assertEqual(len(after), 3)
        self.assertEqual(after[2]["seq"], 3)
        self.assertEqual(after[2]["prev_hash"], after[1]["hash"])
        st, _, audit2 = http_request(host2, port2, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(audit2["meta"]["count"], 3)

    def test_reset_appends_record_reset_and_never_truncates(self) -> None:
        srv, host, port, path = self.start("reset-on")
        self.intervene(host, port, PENDING, "approve")
        before = path.read_bytes()
        st, _, data = self.call(host, port, "POST", "/api/reset", {})
        self.assertEqual(st, 200, data)
        self.assertIs(data["ok"], True)
        self.assertIs(data["durable"], True)
        lines = read_lines(path)
        self.assertEqual(len(lines), 2, "重置只追加一条，不删历史")
        self.assertEqual(lines[1]["event"], "record_reset")
        self.assertEqual(lines[1]["actor_id"], sv.LOCAL_ACTOR_ID)
        self.assertEqual(lines[0]["seq"], 1, "旧条目原样在")
        self.assertTrue(path.read_bytes().startswith(before), "文件只追加：前缀不变")
        st, _, record = self.call(host, port, "GET", f"{sv.API_PATH_RECORDS}/{PENDING}")
        self.assertNotEqual(record.get("status"), "approved", "记录回到初始快照")

    def test_no_reset_leaves_audit_file_untouched(self) -> None:
        srv, host, port, path = self.start("reset-off", allow_reset=False)
        self.intervene(host, port, PENDING, "approve")
        snapshot = path.read_bytes()
        st, _, data = self.call(host, port, "POST", "/api/reset", {})
        self.assert_unified_error(st, data, "RESET_DISABLED", min_status=403)
        self.assertEqual(path.read_bytes(), snapshot, "--no-reset 时审计文件必须一字节不动")
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(audit["meta"]["count"], 1)


# --------------------------------------------------------------------------- #
# 3. 篡改/断链：500 + /health 报断 + 不自动修复
# --------------------------------------------------------------------------- #

class TamperTests(AuditHttpCase):

    def test_tampered_chain_audits_endpoint_returns_500(self) -> None:
        srv, host, port, path = self.start("tamper-500")
        self.intervene(host, port, PENDING, "approve")
        tamper_line(path, 0, reason="有人把这条改成了好看的话")
        tampered = path.read_bytes()
        st, _, data = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 500, data)
        detail = self.assert_unified_error(st, data, CODE_BROKEN, min_status=500)
        self.assertIn("审计链不可用", str(detail.get("reason") or ""), detail)
        self.assertTrue(detail.get("problems"), detail)
        self.assertIn("hash_mismatch", {p.get("code") for p in detail["problems"]})
        self.assertIn("append-only", str(detail.get("hint") or ""))
        self.assertEqual(detail.get("path"), str(path))
        self.assertEqual(path.read_bytes(), tampered, "服务端不得清空/截断/重建审计文件")
        st2, _, audit2 = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st2, 500, audit2)
        self.assertEqual(path.read_bytes(), tampered, "重复请求也不许修复历史")

    def test_health_marks_audit_not_ok(self) -> None:
        srv, host, port, path = self.start("tamper-health")
        self.intervene(host, port, PENDING, "approve")
        st, _, health = self.call(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertTrue(health["audit"]["ok"], health["audit"])
        tamper_line(path, 0, decision="reject")
        st, _, health = self.call(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertEqual(st, 200, health)
        audit = health["audit"]
        self.assertIs(audit["ok"], False, audit)
        self.assertIs(audit["durable"], True)
        self.assertIn("审计链不可用", str(audit.get("reason") or ""), audit)
        self.assertTrue(audit.get("problems"), audit)

    def test_intervene_refuses_to_decide_on_a_broken_chain(self) -> None:
        srv, host, port, path = self.start("tamper-intervene")
        self.intervene(host, port, PENDING, "approve")
        tamper_line(path, 0, reason="改了历史")
        original = next(r for r in NATIVE if r["id"] == PENDING_2)
        snapshot = path.read_bytes()
        st, _, data = self.intervene(host, port, PENDING_2, "approve")
        self.assert_unified_error(st, data, CODE_BROKEN, min_status=500)
        st, _, record = self.call(host, port, "GET", f"{sv.API_PATH_RECORDS}/{PENDING_2}")
        self.assertEqual(st, 200, record)
        self.assertEqual(record.get("status"), original.get("status"),
                         "审计写不进去就不落决定：状态必须原样")
        self.assertEqual(record.get("user_intervention"), original.get("user_intervention"),
                         "失败的干预不得在记录上留下新的决定痕迹")
        self.assertEqual(len(read_lines(path)), 1, "失败的决定不进审计")
        self.assertEqual(path.read_bytes(), snapshot, "失败的写入不得改动审计文件")

    def test_seal_anchor_catches_a_full_rewrite(self) -> None:
        srv, host, port, path = self.start("tamper-seal")
        self.intervene(host, port, PENDING, "approve")
        srv.service.audit_log.seal(note="t18 封印链头")
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 200, audit)
        self.assertGreaterEqual(audit["meta"]["sealed"]["seals"], 1, audit["meta"])
        self.assertNotEqual(audit["meta"]["sealed"]["sealed_head"], sv.GENESIS_HASH)
        st, _, health = self.call(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertGreaterEqual(health["audit"]["seals"], 1, health["audit"])
        # 改历史（连哈希一起重算也挡得住：封印记着旧链头）
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        lines[0]["decision"] = "reject"
        path.write_text("\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")) for e in lines) + "\n",
                        encoding="utf-8")
        st, _, data = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 500, data)
        codes = {p.get("code") for p in detail_of(data).get("problems", [])}
        self.assertTrue({"hash_mismatch", "seal_mismatch"} & codes, codes)

    def test_startup_reports_broken_chain_to_stderr(self) -> None:
        folder = self.workspace("tamper-stderr")
        path = folder / AUDIT_NAME
        seed_chain(path, 2)
        tamper_line(path, 1, note="篡改")
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            srv, thread = sv.start_background_server("127.0.0.1", 0,
                                                      records=copy.deepcopy(NATIVE),
                                                      web_dir=WEB, audit_log_path=path)
        self._servers.append((srv, thread))
        captured = buffer.getvalue()
        self.assertIn("审计链不可用", captured)
        self.assertIn(str(path), captured)
        self.assertFalse(srv.service.audit_startup["ok"])
        self.assertEqual(srv.service.audit_problem.get("code"), "hash_mismatch",
                         srv.service.audit_problem)
        host, port = srv.server_address[:2]
        st, _, health = http_request(host, port, "GET", sv.API_PATH_HEALTH)
        self.assertIs(health["audit"]["ok"], False, health["audit"])

    def test_broken_chain_at_startup_stays_broken_after_reopen(self) -> None:
        folder = self.workspace("tamper-reopen")
        path = folder / AUDIT_NAME
        seed_chain(path, 1)
        tamper_line(path, 0, note="篡改")
        with contextlib.redirect_stderr(io.StringIO()):
            srv, host, port, _ = self.start("tamper-reopen-a", audit_path=path)
        st, _, data = http_request(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 500, data)
        srv.shutdown()
        srv.server_close()
        with contextlib.redirect_stderr(io.StringIO()):
            srv2, host2, port2, _ = self.start("tamper-reopen-b", audit_path=path)
        st, _, data = http_request(host2, port2, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 500, data)
        self.assertEqual(code_of(data), CODE_BROKEN, data)


# --------------------------------------------------------------------------- #
# 4. append-only 面
# --------------------------------------------------------------------------- #

class AppendOnlySurfaceTests(AuditHttpCase):

    def test_write_methods_are_blocked_on_the_audit_endpoint(self) -> None:
        srv, host, port, path = self.start("append-only")
        self.intervene(host, port, PENDING, "approve")
        for method in ("DELETE", "PATCH", "PUT", "OPTIONS"):
            st, _, data = self.call(host, port, method, sv.API_PATH_AUDIT)
            self.assertEqual(st, 405, (method, data))
            self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED", (method, data))
        st, _, data = self.call(host, port, "POST", sv.API_PATH_AUDIT, {})
        self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED", data)
        self.assertEqual(st, 405, data)

    def test_no_delete_clear_endpoint_exists(self) -> None:
        srv, host, port, path = self.start("append-only-2")
        for probe in ("/api/audit/delete", "/api/audit/clear", "/api/audit/truncate",
                      "/api/audit/reset"):
            st, _, data = self.call(host, port, "POST", probe, {})
            self.assertEqual(code_of(data), "ROUTE_NOT_FOUND", (probe, data))
            st, _, data = self.call(host, port, "GET", probe)
            self.assertEqual(code_of(data), "STATIC_NOT_FOUND", (probe, data))

    def test_log_object_has_no_mutating_methods(self) -> None:
        srv, host, port, path = self.start("append-only-3")
        log = srv.service.audit_log
        self.assertIsNotNone(log)
        for name in ("update", "delete", "truncate", "clear", "erase", "purge", "rewrite"):
            with self.assertRaises(au.AuditOperationForbidden, msg=name):
                getattr(log, name)
        with self.assertRaises(au.AuditOperationForbidden):
            getattr(au, "delete_log")

    def test_server_only_appends_through_the_broker(self) -> None:
        """追加式纪律钉在源码上：server.py 只准经 `_append_durable*` 写审计。"""
        source = (ROOT / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("audit_log.append(", source)
        self.assertIn("self._append_durable(", source)
        self.assertIn("self._append_durable_strict(", source)
        self.assertIn("verify_audit_chain", source)
        self.assertIn("--no-audit-log", source)
        self.assertIn("AUDIT_CHAIN_BROKEN", source)


# --------------------------------------------------------------------------- #
# 5. 查询与分页
# --------------------------------------------------------------------------- #

class QueryTests(AuditHttpCase):

    def setUp(self) -> None:
        super().setUp()
        self.srv, self.host, self.port, self.path = self.start("query")
        self.intervene(self.host, self.port, PENDING, "approve")
        self.intervene(self.host, self.port, PENDING_2, "reject", reason="先不要发送")
        self.intervene(self.host, self.port, PENDING_3, "approve")

    def payload(self, query: str = "") -> tuple[int, object]:
        st, _, data = self.call(self.host, self.port, "GET", sv.API_PATH_AUDIT + query)
        return st, data

    def test_limit_and_offset_page_forward(self) -> None:
        st, data = self.payload("?limit=1")
        self.assertEqual(st, 200, data)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["seq"], 1)
        self.assertEqual(data["meta"]["limit"], 1)
        self.assertEqual(data["meta"]["total"], 3)
        st, data = self.payload("?limit=1&offset=2")
        self.assertEqual(data["entries"][0]["seq"], 3)

    def test_reverse_gives_newest_first(self) -> None:
        st, data = self.payload("?limit=2&reverse=true")
        self.assertEqual(st, 200, data)
        self.assertEqual([e["seq"] for e in data["entries"]], [3, 2])

    def test_filters(self) -> None:
        st, data = self.payload("?event=decision")
        self.assertEqual(st, 200, data)
        self.assertEqual(len(data["entries"]), 3)
        st, data = self.payload("?decision=reject")
        self.assertEqual([e["decision"] for e in data["entries"]], ["reject"])
        st, data = self.payload(f"?action_id={PENDING_3}")
        self.assertTrue(data["entries"], data)
        self.assertEqual({e["action_id"] for e in data["entries"]}, {PENDING_3})
        st, data = self.payload(f"?actor_id={sv.LOCAL_ACTOR_ID}")
        self.assertEqual(len(data["entries"]), 3)
        st, data = self.payload("?actor_id=nobody")
        self.assertEqual(data["entries"], [])
        st, data = self.payload("?q=reject")
        self.assertEqual([e["decision"] for e in data["entries"]], ["reject"])

    def test_bad_query_parameters_are_400_not_silently_default(self) -> None:
        st, data = self.payload("?limit=abc")
        self.assert_unified_error(st, data, "AUDIT_QUERY_INVALID")
        st, data = self.payload("?limit=-5")
        self.assert_unified_error(st, data, "AUDIT_QUERY_INVALID")
        st, data = self.payload("?offset=-1")
        self.assert_unified_error(st, data, "AUDIT_QUERY_INVALID")
        st, data = self.payload("?event=nonsense")
        self.assert_unified_error(st, data, "AUDIT_QUERY_INVALID")

    def test_limit_is_capped(self) -> None:
        st, data = self.payload("?limit=99999")
        self.assertEqual(st, 200, data)
        self.assertLessEqual(data["meta"]["limit"], sv.AUDIT_QUERY_LIMIT_MAX)


# --------------------------------------------------------------------------- #
# 6. 身份（4.3 会话身份进审计）与鉴权事件
# --------------------------------------------------------------------------- #

class IdentityTests(AuditHttpCase):

    def test_team_session_identity_lands_in_the_audit_file(self) -> None:
        srv, host, port, path, admin_pw = self.team_server("identity-team")
        admin = self.login(host, port, "admin", admin_pw)
        st, _, data = http_request(host, port, "POST", sv.API_PATH_USERS,
                                   {"username": "ed_t18", "role": "editor"},
                                   {"Cookie": admin["cookie"],
                                    auth_mod.CSRF_HEADER: admin["csrf"]})
        self.assertEqual(st, 201, data)
        pw = data["user"]["initial_password"]
        editor = self.login(host, port, "ed_t18", pw)
        st, _, data = http_request(host, port, "POST", sv.API_PATH_INTERVENE,
                                   {"action_id": PENDING, "decision": "approve"},
                                   {"Cookie": editor["cookie"],
                                    auth_mod.CSRF_HEADER: editor["csrf"]})
        self.assertEqual(st, 200, data)
        lines = read_lines(path)
        decisions = [e for e in lines if e.get("decision")]
        self.assertTrue(decisions, lines)
        last = decisions[-1]
        self.assertEqual(last["actor_id"], "ed_t18", last)
        self.assertEqual(last["actor_role"], "member", "editor → member（4.2 枚举）")
        self.assertEqual(last["mode"], "live", "team 落审计归 live（4.2 枚举只有 local/live）")
        st, _, audit = http_request(host, port, "GET", f"{sv.API_PATH_AUDIT}?actor_id=ed_t18",
                                    None, {"Cookie": editor["cookie"]})
        self.assertEqual(st, 200, audit)
        self.assertTrue(audit["entries"], audit)

    def test_viewer_denials_are_recorded_in_the_file(self) -> None:
        srv, host, port, path, admin_pw = self.team_server("identity-viewer")
        admin = self.login(host, port, "admin", admin_pw)
        st, _, data = http_request(host, port, "POST", sv.API_PATH_USERS,
                                   {"username": "vw_t18", "role": "viewer"},
                                   {"Cookie": admin["cookie"],
                                    auth_mod.CSRF_HEADER: admin["csrf"]})
        self.assertEqual(st, 201, data)
        viewer = self.login(host, port, "vw_t18", data["user"]["initial_password"])
        st, _, data = http_request(host, port, "POST", sv.API_PATH_INTERVENE,
                                   {"action_id": PENDING, "decision": "approve"},
                                   {"Cookie": viewer["cookie"],
                                    auth_mod.CSRF_HEADER: viewer["csrf"]})
        self.assertEqual(st, 403, data)
        lines = read_lines(path)
        denials = [e for e in lines if e.get("event") == "access_denied"]
        self.assertTrue(denials, "越权必须落盘")
        self.assertEqual(denials[-1]["fields"]["auth_event"], "auth.denied")
        self.assertEqual(denials[-1]["fields"]["role"], "viewer")
        self.assertEqual(denials[-1]["fields"]["username"], "vw_t18")
        # 鉴权事件的 actor_id 用 4.3 的用户 id（用户名在 fields.username）；决定事件用用户名
        self.assertIn(denials[-1]["actor_id"], ("vw_t18",) + tuple(
            str(e.get("actor_id") or "") for e in lines))
        self.assertTrue(str(denials[-1]["actor_id"]))

    def test_login_events_land_in_the_file_without_passwords(self) -> None:
        srv, host, port, path, admin_pw = self.team_server("identity-login")
        http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                     {"username": "admin", "password": "definitely-wrong-1"})
        st, _, data = http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                                   {"username": "admin", "password": admin_pw})
        self.assertEqual(st, 200, data)
        cookie = f"{auth_mod.SESSION_COOKIE}={data['cookie']['value']}"
        http_request(host, port, "POST", sv.API_PATH_AUTH_LOGOUT, {}, {"Cookie": cookie})
        text = path.read_text(encoding="utf-8")
        lines = read_lines(path)
        events = [e.get("fields", {}).get("auth_event") for e in lines
                  if isinstance(e.get("fields"), dict)]
        self.assertIn("auth.login_failed", events, events)
        self.assertIn("auth.login", events, events)
        self.assertIn("auth.logout", events, events)
        self.assertNotIn(admin_pw, text, "口令绝不进审计文件")
        self.assertNotIn("definitely-wrong-1", text)

    def test_events_whitelist_accepts_auth_names(self) -> None:
        """队长裁决 3：`audit.EVENTS` 已含 auth.*，历史映射保持不变。"""
        for name in ("auth.login", "auth.login_failed", "auth.logout", "auth.denied"):
            self.assertIn(name, au.EVENTS, name)
            self.assertEqual(au.build_event(name, actor_id="x-user")["event"], name)
        folder = self.workspace("events-enum")
        path = folder / AUDIT_NAME
        log = au.open_log(path)
        log.append(au.build_event("auth.login", actor_id="x-user", reason="登录成功"))
        log.append(au.build_event("access_denied", actor_id="x-user", reason="越权",
                                  extra={"auth_event": "auth.denied"}))
        report = au.verify_file(path)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual([e["fields"]["auth_event"] for e in read_lines(path)
                          if e.get("event") == "access_denied"], ["auth.denied"])
        data = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                   audit_log_path=path).audit_payload(event="auth.login")
        self.assertEqual(len(data["entries"]), 1, data["meta"])


# --------------------------------------------------------------------------- #
# 7. 内存模式（t8 口径）与 CLI 默认
# --------------------------------------------------------------------------- #

class MemoryParityTests(AuditHttpCase):

    def start_memory(self, name: str, **over):
        folder = self.workspace(name)
        srv, thread = sv.start_background_server("127.0.0.1", 0,
                                                 records=copy.deepcopy(NATIVE),
                                                 web_dir=WEB, **over)
        self._servers.append((srv, thread))
        host, port = srv.server_address[:2]
        return srv, host, port, folder

    def test_memory_backend_keeps_the_t8_shape(self) -> None:
        srv, host, port, _ = self.start_memory("memory-shape")
        self.assertEqual(srv.service.audit_source, sv.AUDIT_SOURCE_MEMORY)
        self.assertIsNone(srv.service.audit_log)
        self.intervene(host, port, PENDING, "modify", modified_params=MODIFY_PARAMS)
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertEqual(st, 200, audit)
        self.assertIs(audit["meta"]["durable"], False)
        self.assertIn("内存哈希链预览", audit["meta"]["note"])
        self.assertNotIn("jsonl", audit)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])
        entries = [e for e in audit["entries"] if e["event"] == "intervention.decision"]
        self.assertTrue(entries)
        last = entries[-1]
        self.assertEqual(last["action_id"], PENDING)
        self.assertEqual(last["decision"], "modify")
        self.assertEqual(last["mode"], sv.MODE_LOCAL)
        self.assertEqual(last["record_status"], "modified")
        self.assertEqual(last["changed_fields"], ["max_move_count"])
        self.assertTrue(str(last["entry_hash"]).startswith(HASH_PREFIX))

    def test_memory_health_shape_has_no_durable_fields(self) -> None:
        srv, host, port, _ = self.start_memory("memory-health")
        st, _, health = self.call(host, port, "GET", sv.API_PATH_HEALTH)
        audit = health["audit"]
        self.assertTrue(audit["ok"], audit)
        self.assertIs(audit["durable"], False)
        self.assertEqual(audit["source"], sv.AUDIT_SOURCE_MEMORY)
        self.assertIn("count", audit)
        self.assertIn("head", audit)
        self.assertNotIn("path", audit, "内存模式没有文件路径可报")

    def test_reset_in_memory_mode_appends_only_to_the_preview_chain(self) -> None:
        srv, host, port, folder = self.start_memory("memory-reset")
        self.intervene(host, port, PENDING, "approve")
        st, _, data = self.call(host, port, "POST", "/api/reset", {})
        self.assertEqual(st, 200, data)
        self.assertIs(data["durable"], False)
        self.assertFalse((folder / AUDIT_NAME).exists())
        st, _, audit = self.call(host, port, "GET", sv.API_PATH_AUDIT)
        self.assertIn("demo.store_reset", [e["event"] for e in audit["entries"]])


class CliContractTests(unittest.TestCase):

    def test_default_audit_path_is_the_data_dir(self) -> None:
        self.assertEqual(sv.default_audit_log_path(), au.default_log_path())
        self.assertEqual(sv.default_audit_log_path().name, au.AUDIT_FILE_NAME)
        self.assertEqual(sv.default_audit_log_path().parent.name, "var")

    def test_cli_flags_are_wired(self) -> None:
        source = (ROOT / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"--audit-log"', source)
        self.assertIn('"--no-audit-log"', source)
        self.assertIn("args.no_audit_log", source)
        self.assertIn("str(default_audit_log_path())", source)

    def test_broken_helpers_are_human_readable(self) -> None:
        status = {"ok": False, "count": 3, "head": "sha256:" + "a" * 64,
                  "path": "var/audit.jsonl", "sealed_head": "sha256:" + "b" * 64,
                  "sealed_count": 2,
                  "problems": [{"code": "hash_mismatch", "index": 1, "seq": 2,
                                "reason": "第 2 条内容被改过（哈希复算不一致）"}]}
        reason = sv.audit_broken_reason(status)
        self.assertIn("审计链不可用", reason)
        self.assertIn("第 2 条", reason)
        self.assertIn("共 1 处问题", reason)
        self.assertIn("不会自动清空", reason)
        detail = sv.audit_broken_detail(status)
        self.assertEqual(detail["problem_count"], 1)
        self.assertEqual(detail["sealed_count"], 2)
        self.assertEqual(detail["problems"][0]["code"], "hash_mismatch")
        self.assertIn(CODE_BROKEN, sv.ERROR_TABLE)
        self.assertEqual(int(sv.ERROR_TABLE[CODE_BROKEN][0]), 500)


if __name__ == "__main__":
    TMP.mkdir(parents=True, exist_ok=True)
    runner = unittest.main(verbosity=2)
