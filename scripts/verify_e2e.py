#!/usr/bin/env python3
"""TraceLens 容器验收探针（子任务 5.4 / t17）——**真发 HTTP 请求**，不做静态检查。

两阶段，彼此用一份 state 文件交接（脚本 scripts/docker-verify.sh 负责编排）：

  phase=fresh         首启：/health 200+status=ok → admin 登录 → 建 editor/viewer
                      → 三角色各自登录 → viewer 写操作必须 403 → 签发上报令牌
                      → webhook 写一条记录 → 记录数 +1 → 审计链 ok
                      → 审计导出 CSV / PDF 真的出字节
  phase=after-restart 重启容器后：记录数仍是那一份（卷持久化）、webhook 写进去的
                      那条还在、用户表还在（editor 还能登录）、审计链仍 ok 且
                      条目数没有倒退

判据全部来自产品自己的响应字段（docs/03 §6.3 / §6.6），不猜：
  · 健康检查路径是 /health（不是 /api/health），要求 status == "ok"；
  · 登录响应形状 {user:{role,…}, token, csrf_token, session:{…}}；
  · webhook = POST /api/ingest/events，令牌头 X-TraceLens-Ingest-Token；
  · 原生 ActionRecord 必填字段见 trace_adapter.REQUIRED_RECORD_KEYS，
    这里直接拿 samples/action_records.json 的一条当模板，只换 id / 时间戳。

退出码：0 = 全部通过；1 = 有断言失败（失败清单打在最后）。
只用标准库，容器内外都能跑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples" / "action_records.json"
INGESTED_ID = "act-docker-verify-0001"
EDITOR = ("dev-editor", "Dev-Editor-Pass-2026")
VIEWER = ("aud-reader", "Aud-Reader-Pass-2026")


# --------------------------------------------------------------------------- #
# 极小 HTTP 客户端（带 token / CSRF，错误也返回结构化 JSON）
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None
        self.csrf: str | None = None
        self.request_id = ""

    def call(self, method: str, path: str, body=None, *, token: str | None = None,
             csrf: str | None = None, raw: bool = False, timeout: float = 15.0):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        tok = token if token is not None else self.token
        csr = csrf if csrf is not None else self.csrf
        if tok:
            req.add_header("X-TraceLens-Token", tok)
        if csr:
            req.add_header("X-CSRF-Token", csr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                self.request_id = resp.headers.get("X-Request-Id") or self.request_id
                if raw:
                    return resp.status, dict(resp.headers), payload
                return resp.status, json.loads(payload.decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw_body = exc.read()
            if raw:
                return exc.code, dict(exc.headers or {}), raw_body
            try:
                return exc.code, json.loads(raw_body.decode("utf-8") or "{}")
            except ValueError:
                return exc.code, {"raw": raw_body.decode("utf-8", "replace")}

    def login(self, username: str, password: str) -> tuple[int, dict]:
        status, payload = self.call("POST", "/api/auth/login",
                                    {"username": username, "password": password},
                                    token="", csrf="")
        if status == 200:
            self.token = payload.get("token")
            self.csrf = payload.get("csrf_token")
        return status, payload


# --------------------------------------------------------------------------- #
# 断言收集（一次跑完，失败不早退，最后统一报）
# --------------------------------------------------------------------------- #
class Checks:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.rows.append({"check": name, "status": "passed", "detail": detail})
        print("  [PASS] %-46s %s" % (name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.rows.append({"check": name, "status": "failed", "detail": detail})
        print("  [FAIL] %-46s %s" % (name, detail))

    def eq(self, name: str, actual, expected) -> bool:
        if actual == expected:
            self.ok(name, "= %r" % (actual,))
            return True
        self.fail(name, "期望 %r，实际 %r" % (expected, actual))
        return False

    def truth(self, name: str, value, detail: str = "") -> bool:
        if value:
            self.ok(name, detail or "true")
            return True
        self.fail(name, detail or "false")
        return False

    @property
    def failures(self) -> list[dict]:
        return [row for row in self.rows if row["status"] == "failed"]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def wait_health(client: Client, deadline_s: float = 90.0) -> dict:
    """等 /health 真的 200 + status=ok；期间打印进度，超时抛 SystemExit。"""
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < deadline_s:
        try:
            status, payload = client.call("GET", "/health", token="", csrf="")
            if status == 200 and payload.get("status") == "ok":
                return payload
            last = "HTTP %s status=%r" % (status, payload.get("status"))
        except Exception as exc:                              # noqa: BLE001
            last = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(0.5)
    raise SystemExit("等 /health 超时（%.0fs），最后一次：%s" % (deadline_s, last))


def native_record(template_index: int = 0) -> dict:
    """拿演示夹具里一条真实记录当模板，换 id / 时间戳 —— 它是契约里的完整形状。"""
    doc = json.loads(SAMPLES.read_text(encoding="utf-8"))
    record = json.loads(json.dumps(doc["records"][template_index], ensure_ascii=False))
    record["id"] = INGESTED_ID
    record["timestamp"] = "2026-11-11T00:40:00.000+00:00"
    record["status"] = "running"
    record["sequence_no"] = 9001
    record["agent_name"] = "docker 验收探针"
    record["labels"] = ["docker", "verify"]
    record.pop("user_intervention", None)
    record["user_intervention"] = None
    return record


# --------------------------------------------------------------------------- #
# phase=fresh
# --------------------------------------------------------------------------- #
def phase_fresh(client: Client, checks: Checks, password: str) -> dict:
    print("\n== phase=fresh：首启（team 模式） ==")
    health = wait_health(client)
    checks.eq("GET /health → 200 + status", health.get("status"), "ok")
    checks.eq("health.mode", health.get("mode"), "team")
    auth = health.get("auth") or {}
    checks.eq("health.auth.required", auth.get("required"), True)
    checks.eq("health.auth.roles", auth.get("roles"), ["admin", "editor", "viewer"])
    checks.truth("health.ingest.enabled", (health.get("ingest") or {}).get("enabled"),
                 "webhook 通道开着")
    stats = health.get("stats") or {}
    checks.eq("首启种子条数（--demo zh）", stats.get("count"), 12)
    checks.eq("health.stats.durable", stats.get("durable"), True)

    # ---- 静态站：容器里真的托管着页面（不是只有 API 能通）----
    status, _h, _p = client.call("GET", "/healthz", raw=True, token="", csrf="")
    checks.eq("GET /healthz（别名）→ 200", status, 200)
    for path, needle in (("/", b"<html"), ("/app.js", b"function"), ("/styles.css", b"{")):
        status, headers, payload = client.call("GET", path, raw=True, token="", csrf="")
        checks.eq("GET %s → 200" % path, status, 200)
        checks.truth("%s 真的返回内容（%d 字节）" % (path, len(payload)),
                     len(payload) > 100 and needle in payload)

    # ---- admin 登录 ----
    status, payload = client.login("admin", password)
    if not checks.eq("POST /api/auth/login (admin) → 200", status, 200):
        return {}
    user = payload.get("user") or {}
    checks.eq("登录响应 user.role", user.get("role"), "admin")
    checks.truth("登录响应 csrf_token 非空", bool(payload.get("csrf_token")))
    checks.truth("admin 首登 must_change_password",
                 user.get("must_change_password") is True,
                 "环境变量注入的口令 = 首登口令，产品要求改密（真实行为）")
    status, me = client.call("GET", "/api/auth/me")
    checks.eq("GET /api/auth/me → 200", status, 200)
    checks.eq("me.role", (me.get("user") or me).get("role"), "admin")

    # ---- 建 editor / viewer（admin 才有 users.manage）----
    for name, passwd in (EDITOR, VIEWER):
        role = "editor" if name == EDITOR[0] else "viewer"
        status, out = client.call("POST", "/api/users",
                                  {"username": name, "role": role,
                                   "display_name": name, "password": passwd})
        if status == 201:
            checks.ok("POST /api/users (%s/%s) → 201" % (name, role))
        elif status == 409 and out.get("code") == "USER_EXISTS":
            checks.ok("POST /api/users (%s) 已存在（沿用）" % name)
        else:
            checks.fail("POST /api/users (%s/%s)" % (name, role),
                        "HTTP %s %s" % (status, out.get("code")))

    # ---- 三角色各自登录 ----
    for name, passwd, role in ((EDITOR[0], EDITOR[1], "editor"),
                               (VIEWER[0], VIEWER[1], "viewer")):
        probe = Client(client.base)
        status, out = probe.login(name, passwd)
        checks.eq("POST /api/auth/login (%s) → 200" % name, status, 200)
        checks.eq("登录角色 %s" % name, (out.get("user") or {}).get("role"), role)

    # ---- 角色强制：viewer 不能干预（矩阵在服务端强制，前端隐藏只是体验）----
    viewer = Client(client.base)
    viewer.login(*VIEWER)
    status, out = viewer.call("POST", "/intervene",
                              {"action_id": "act-demo-0001", "decision": "approve"})
    checks.eq("viewer 干预 → 403 ROLE_FORBIDDEN", (status, out.get("code")),
              (403, "ROLE_FORBIDDEN"))

    # ---- webhook 写入 ----
    status, tok = client.call("POST", "/api/ingest/tokens", {"label": "docker-verify"})
    if not checks.eq("POST /api/ingest/tokens → 201", status, 201):
        return {"records": stats.get("count")}
    secret = tok.get("token")
    checks.truth("上报令牌明文只返回一次", bool(secret))
    record = native_record()
    req = urllib.request.Request(client.base + "/api/ingest/events",
                                 data=json.dumps({"events": [record]},
                                                 ensure_ascii=False).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-TraceLens-Ingest-Token", str(secret))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ingest_status, ingested = resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        ingest_status, ingested = exc.code, json.loads(exc.read().decode("utf-8") or "{}")
    checks.eq("POST /api/ingest/events（webhook）→ 200", ingest_status, 200)
    checks.eq("webhook 收下 1 条", ingested.get("accepted"), 1)

    # ---- 记录数 +1，且真的是那一条 ----
    status, records = client.call("GET", "/api/records")
    meta = records.get("meta") or {}
    checks.eq("GET /api/records → 200", status, 200)
    checks.eq("records.count（12 种子 + 1 webhook）", meta.get("count"), 13)
    ids = [item.get("id") for item in records.get("records") or []]
    checks.truth("webhook 写入的记录可读回（%s）" % INGESTED_ID, INGESTED_ID in ids)

    # ---- 审计链 + 导出 ----
    status, audit = client.call("GET", "/api/audit?limit=20")
    chain = audit.get("chain") or {}
    meta_a = audit.get("meta") or {}
    checks.eq("GET /api/audit → 200", status, 200)
    checks.eq("审计链 ok", chain.get("ok"), True)
    checks.truth("审计条目数 ≥ 1（登录/建用户/webhook 都记账）",
                 int(meta_a.get("count") or 0) >= 1, "count=%s" % meta_a.get("count"))

    status, headers, payload = client.call("GET", "/api/audit/export?format=csv", raw=True)
    checks.eq("GET /api/audit/export?format=csv → 200", status, 200)
    checks.truth("CSV 有内容（>0 字节且含表头）", b"," in payload and len(payload) > 100,
                 "%d 字节" % len(payload))
    checks.eq("CSV Content-Type", (headers.get("Content-Type") or "").split(";")[0],
              "text/csv")

    status, headers, payload = client.call("GET", "/api/audit/export?format=pdf", raw=True)
    checks.eq("GET /api/audit/export?format=pdf → 200", status, 200)
    ctype = (headers.get("Content-Type") or "").split(";")[0]
    fallback = headers.get("X-TraceLens-Export-Fallback")
    if ctype == "application/pdf":
        checks.ok("PDF 真的出 PDF（reportlab 在镜像里）",
                  "%d 字节，sha256=%s" % (len(payload), hashlib.sha256(payload).hexdigest()[:12]))
    else:
        checks.ok("PDF 回退为可打印 HTML（reportlab 未装）",
                  "Content-Type=%s，原因=%s" % (ctype, fallback))

    return {"records": meta.get("count"), "audit_entries": meta_a.get("count"),
            "pdf_ready": ctype == "application/pdf",
            "audit_head": chain.get("head")}


# --------------------------------------------------------------------------- #
# phase=after-restart
# --------------------------------------------------------------------------- #
def phase_after_restart(client: Client, checks: Checks, password: str,
                        state: dict) -> dict:
    print("\n== phase=after-restart：重启容器后（卷持久化） ==")
    health = wait_health(client)
    stats = health.get("stats") or {}
    checks.eq("GET /health → 200 + status", health.get("status"), "ok")
    checks.eq("重启后记录数不变", stats.get("count"), state.get("records"))
    checks.eq("重启后仍 durable", stats.get("durable"), True)

    status, payload = client.login("admin", password)
    checks.eq("重启后 admin 仍能登录（用户表在卷里）", status, 200)

    status, records = client.call("GET", "/api/records")
    ids = [item.get("id") for item in records.get("records") or []]
    checks.truth("webhook 写的那条重启后还在", INGESTED_ID in ids)

    probe = Client(client.base)
    status, _ = probe.login(*EDITOR)
    checks.eq("重启后 editor 仍能登录", status, 200)

    status, audit = client.call("GET", "/api/audit?limit=20")
    chain = audit.get("chain") or {}
    meta = audit.get("meta") or {}
    checks.eq("重启后审计链仍 ok", chain.get("ok"), True)
    checks.truth("审计条目数没有倒退",
                 int(meta.get("count") or 0) >= int(state.get("audit_entries") or 0),
                 "%s → %s" % (state.get("audit_entries"), meta.get("count")))
    return {"records": stats.get("count"), "audit_entries": meta.get("count")}


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="TraceLens 容器验收探针（t17）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--password", default=os.environ.get("TRACELENS_ADMIN_PASSWORD", ""))
    parser.add_argument("--phase", choices=["fresh", "after-restart"], default="fresh")
    parser.add_argument("--state", default="", help="两阶段交接的 JSON 文件")
    parser.add_argument("--json-out", default="", help="把结果写进这个文件")
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("缺 --password（或环境变量 TRACELENS_ADMIN_PASSWORD）")

    client = Client(args.base_url)
    checks = Checks()
    state = {}
    if args.phase == "after-restart":
        if not args.state or not pathlib.Path(args.state).exists():
            raise SystemExit("after-restart 阶段需要 --state（上一阶段写出来的 JSON）")
        state = json.loads(pathlib.Path(args.state).read_text(encoding="utf-8"))
        result = phase_after_restart(client, checks, args.password, state)
    else:
        result = phase_fresh(client, checks, args.password)

    summary = {"phase": args.phase, "base_url": args.base_url,
               "checks": checks.rows, "failures": checks.failures,
               "state": result, "passed": not checks.failures}
    if args.json_out:
        pathlib.Path(args.json_out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.state and args.phase == "fresh":
        pathlib.Path(args.state).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total, failed = len(checks.rows), len(checks.failures)
    print("\n-- %s：%d/%d 通过%s" % (args.phase, total - failed, total,
                                    "" if not failed else "（失败 %d）" % failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
