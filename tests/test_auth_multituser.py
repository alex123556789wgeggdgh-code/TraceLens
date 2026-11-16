"""t13 [t4-3] 验收测试：多用户与权限（local / team 双模式）。

运行（全程离线，只用标准库 unittest；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_auth_multituser.py

覆盖任务验收点：
  1. 口令哈希（PBKDF2）与升级、口令强度规则、一次性生成口令；
  2. 会话 token：签发 / 校验 / 过期 / 伪造 / 登出吊销；角色以库为准（不来自 token）；
  3. 连续失败 5 次 → 锁定（429 + Retry-After）；
  4. 角色矩阵 ≥3×3（实际 4 角色 × 8 能力，冻结自 docs/01 §6.4）与逐项探针；
  5. 越权 403、未登录 401；CRUD 用户仅 admin；最后一名 admin 不可降级/禁用；
  6. team 首个 admin 引导（生成口令 / 环境变量注入，两个别名都支持）；
  7. local 模式免登录，t8 行为逐字不变（decided_by=local-user、审计链 genesis）；
  8. team 监听 0.0.0.0（局域网 / Docker 部署口径）；
  9. 强制点唯一：server.py 里不得出现 `role == "admin"` 式散落判断（AST 扫描）；
 10. 口令永不落盘、不进审计、不进 HTTP 响应（bootstrap 那次由控制台打印）。

临时文件只写在系统临时目录（`tests/scratch.py` 的 `scratch_dir`），模块级 tearDown 会整目录删掉。
"""
from __future__ import annotations

import ast
import copy
import http.client
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):   # tests/：裸 `python -m unittest tests.<模块>` 也能 import scratch
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import auth as au  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
import http_fixture as fx  # noqa: E402  连接加固：端口就绪等待 + 连接级瞬态一次性重试

WEB = ROOT / "agent_explainer" / "web"
SAMPLE_PATH = ROOT / "samples" / "action_records.json"
NATIVE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"]
from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

TMP = scratch_dir("auth")
PKG = ROOT / "agent_explainer"

ITER = 1000                      # 测试用低轮数（真机默认 210000）
ADMIN_PW = "Bootstrap-Passphrase-2026"
PW_EDITOR = "Editor-Passphrase-2026"
PW_VIEWER = "Viewer-Passphrase-2026"
PW_NEW = "Rotated-Passphrase-2027"
PENDING = "act-demo-0004"
# 该记录的可编辑字段只有 max_move_count（服务端会校验，写错字段名要 400）
MODIFY_PARAMS = {"max_move_count": 2}

# docs/01 §6.4 冻结的矩阵（测试即合同：改代码必须同时改这里，逼一次显式讨论）
DOCS_MATRIX: dict[str, set[str]] = {
    "admin": {"records.read", "data.write", "ingest.token.manage", "intervene.decide",
              "audit.read", "audit.export", "settings.write", "users.manage"},
    "editor": {"records.read", "data.write", "intervene.decide", "audit.read", "audit.export"},
    "viewer": {"records.read", "audit.read", "audit.export"},
    "local": {"records.read", "data.write", "ingest.token.manage", "intervene.decide",
              "audit.read", "audit.export", "settings.write"},
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def state_path(name: str) -> pathlib.Path:
    """每个用例一个独立状态文件（scratch_dir("auth")/<name>/auth-state.json）。"""
    folder = TMP / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / au.STATE_FILE_NAME


def team(name: str, **over) -> au.Authenticator:
    over.setdefault("iterations", ITER)
    over.setdefault("env", {})
    return au.Authenticator("team", state_path=state_path(name), **over)


def local(name: str, **over) -> au.Authenticator:
    over.setdefault("iterations", ITER)
    over.setdefault("env", {})
    return au.Authenticator("local", state_path=state_path(name), **over)


def fixture(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in NATIVE if r["id"] == action_id))


def http_request(host: str, port: int, method: str, path: str, body=None,
                 extra_headers: dict | None = None, timeout: float = 8.0) -> tuple[int, dict, object]:
    # M2-PRE 4（PM-VERDICT-R1-9 裁决②）：连接生命周期交给 tests/http_fixture——
    # 端口就绪等待 + 连接级瞬态一次性重试；非幂等动词绝不重放，断言级失败绝不重试。
    # 登录 / 改密这类写请求即使被 10053 中止也不重放：服务端可能已经落了这次写入。
    def once(conn: http.client.HTTPConnection) -> tuple[int, dict, object]:
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

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


def code_of(data: object) -> str:
    return str(data.get("code")) if isinstance(data, dict) else ""


def detail_of(data: object) -> dict:
    return dict(data.get("detail") or {}) if isinstance(data, dict) else {}


class Clock:
    """可注入的时钟：过期测试不靠 sleep。"""

    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class AuthCaseMixin(unittest.TestCase):
    """统一断言：统一错误体 {code, message, detail, request_id}。"""

    def assert_unified(self, status: int, headers: dict, data: object, code: str) -> dict:
        self.assertIsInstance(data, dict, f"错误体必须是对象：{data!r}")
        body = data  # type: ignore[assignment]
        self.assertEqual(code, code_of(body), body)
        for key in ("code", "message", "detail", "request_id"):
            self.assertIn(key, body, f"统一错误体缺字段：{body}")
        return body


# --------------------------------------------------------------------------- #
# 1. 矩阵与角色（docs/01 §6.4）
# --------------------------------------------------------------------------- #

class MatrixTests(unittest.TestCase):
    def test_matrix_matches_docs_table(self) -> None:
        self.assertEqual(set(au.PERMISSIONS),
                         {"records.read", "data.write", "ingest.token.manage", "intervene.decide",
                          "audit.read", "audit.export", "settings.write", "users.manage"})
        self.assertEqual(sorted(au.ROLES), ["admin", "editor", "local", "viewer"])
        self.assertEqual(list(au.TEAM_ROLES), ["admin", "editor", "viewer"])
        for role, expected in DOCS_MATRIX.items():
            self.assertEqual(set(au.ROLE_PERMISSIONS[role]), expected, f"{role} 的权限与 docs 表不符")
        self.assertEqual(len(au.ROLE_PERMISSIONS), 4)
        self.assertEqual(len(au.PERMISSIONS), 8)

    def test_viewer_has_no_intervene_and_no_users(self) -> None:
        """产品口径：viewer 干预入口隐藏、用户管理不可见，但能看审计与导出。"""
        for perm in ("intervene.decide", "users.manage", "settings.write", "data.write",
                     "ingest.token.manage"):
            self.assertFalse(au.can("viewer", perm), f"viewer 不该有 {perm}")
        for perm in ("records.read", "audit.read", "audit.export"):
            self.assertTrue(au.can("viewer", perm), f"viewer 该有 {perm}")

    def test_local_has_no_user_administration(self) -> None:
        self.assertNotIn("users.manage", au.ROLE_PERMISSIONS["local"])
        self.assertTrue(au.can("local", "intervene.decide"))
        self.assertTrue(au.can("local", "settings.write"))

    def test_rank_and_audit_role(self) -> None:
        auth = local("rank-role")
        self.assertTrue(au.rank_at_least("admin", "viewer"))
        self.assertTrue(au.rank_at_least("editor", "editor"))
        self.assertFalse(au.rank_at_least("viewer", "editor"))
        self.assertTrue(au.rank_at_least("viewer", None))
        self.assertTrue(au.rank_at_least("local", "admin"))
        self.assertEqual(set(au.ROLE_AUDIT_MAP), set(au.ROLES))
        for role in au.ROLES:
            self.assertIn(auth.audit_actor_role(role),
                          ("owner", "member", "viewer", "local", "system"))
        self.assertEqual(auth.audit_actor_role("admin"), "owner")
        self.assertEqual(auth.audit_actor_role("editor"), "member")

    def test_permissions_payload_publishes_matrix(self) -> None:
        auth = local("matrix-payload")
        payload = auth.permissions_payload()
        self.assertEqual(sorted(payload["permissions"]), sorted(au.PERMISSIONS))
        for role, expected in DOCS_MATRIX.items():
            self.assertEqual(set(payload["matrix"][role]), expected)
        self.assertEqual(payload["required"], False)
        self.assertEqual(payload["mode"], "local")


# --------------------------------------------------------------------------- #
# 2. 口令
# --------------------------------------------------------------------------- #

class PasswordTests(unittest.TestCase):
    def test_hash_verify_and_reject(self) -> None:
        kdf = au.PasswordHash(ITER)
        encoded = kdf.hash(PW_EDITOR)
        self.assertTrue(encoded.startswith(au.PASSWORD_PREFIX), encoded)
        self.assertNotIn(PW_EDITOR, encoded)
        self.assertTrue(kdf.verify(PW_EDITOR, encoded))
        self.assertFalse(kdf.verify(PW_EDITOR + "x", encoded))
        self.assertFalse(kdf.verify("", encoded))

    def test_upgrade_flag(self) -> None:
        old = au.PasswordHash(1_000)
        strong = au.PasswordHash(2_000)
        encoded = old.hash(PW_EDITOR)
        self.assertTrue(strong.verify(PW_EDITOR, encoded), "低轮数哈希必须仍可校验")
        self.assertTrue(strong.needs_upgrade(encoded), "轮数变了要标记升级")
        self.assertFalse(strong.needs_upgrade(strong.hash(PW_EDITOR)))
        self.assertFalse(strong.needs_upgrade("not-a-hash"))
        self.assertFalse(strong.verify(PW_EDITOR, "garbage"))

    def test_strength_rules(self) -> None:
        self.assertTrue(au.password_strength(PW_EDITOR, username="ops-li")["ok"])
        self.assertEqual(au.password_strength("")["code"], "AUTH_PASSWORD_REQUIRED")
        self.assertEqual(au.password_strength("short")["code"], "AUTH_WEAK_PASSWORD")
        self.assertEqual(au.password_strength("tracelens123")["code"], "AUTH_WEAK_PASSWORD")
        self.assertEqual(au.password_strength("password")["code"], "AUTH_WEAK_PASSWORD")
        self.assertEqual(au.password_strength("aaaaaaaaaaaa")["code"], "AUTH_WEAK_PASSWORD")
        self.assertEqual(au.password_strength("ops-li", username="ops-li")["code"], "AUTH_WEAK_PASSWORD")
        self.assertEqual(au.password_strength("ops-li2026", username="ops-li")["code"],
                         "AUTH_WEAK_PASSWORD")
        # 口令够长（≥2×最小长度）时含用户名不算弱：这是实现里写明的豁免口径
        self.assertTrue(au.password_strength("ops-li-Passphrase-2026", username="ops-li")["ok"])
        self.assertEqual(au.password_strength("x" * 400)["code"], "AUTH_PASSWORD_TOO_LONG")

    def test_generated_password_is_strong_and_varied(self) -> None:
        seen = {au.new_generated_password() for _ in range(12)}
        self.assertEqual(len(seen), 12, "生成口令必须每次不同")
        for value in seen:
            self.assertGreaterEqual(len(value), 12)
            self.assertTrue(au.password_strength(value)["ok"], "生成口令必须通过强度规则")


# --------------------------------------------------------------------------- #
# 3. 首个 admin 引导（team）
# --------------------------------------------------------------------------- #

class BootstrapTests(AuthCaseMixin):
    def test_generated_password_shown_once_and_works(self) -> None:
        auth = team("boot-generated")
        report = auth.bootstrap_report or {}
        self.assertEqual(report.get("username"), "admin")
        self.assertEqual(report.get("source"), "generated")
        password = str(report.get("password") or "")
        self.assertGreaterEqual(len(password), au.MIN_PASSWORD_CHARS)
        users = auth.list_users()
        self.assertEqual([u["username"] for u in users], ["admin"])
        self.assertEqual(users[0]["role"], "admin")
        self.assertTrue(users[0]["must_change_password"], "引导账号必须标记首次改密")
        session = auth.authenticate("admin", password)
        self.assertEqual(session["role"], "admin")
        self.assertTrue(auth.me_payload(session)["ui"]["show_users"])

    def test_env_injection_both_aliases(self) -> None:
        for alias in au.ENV_ADMIN_PASSWORDS:
            auth = team(f"boot-env-{alias}", env={alias: ADMIN_PW})
            report = auth.bootstrap_report or {}
            self.assertEqual(report.get("source"), alias)
            session = auth.authenticate("admin", ADMIN_PW)
            self.assertEqual(session["role"], "admin")

    def test_bootstrap_only_in_team_and_only_once(self) -> None:
        self.assertIsNone(local("boot-local").bootstrap())
        auth = team("boot-once")
        self.assertIsNotNone(auth.bootstrap_report, "首次启动必须给出一次性口令")
        self.assertIsNone(auth.bootstrap(), "已有用户 → 不再引导")
        self.assertEqual(auth.status()["bootstrap_needed"], False)

    def test_status_reports_bootstrap_needed_and_cookie_contract(self) -> None:
        auth = au.Authenticator("team", state_path=state_path("boot-status"),
                                iterations=ITER, env={}, bootstrap=False)
        status = auth.status()
        self.assertTrue(status["required"])
        self.assertTrue(status["bootstrap_needed"])
        self.assertEqual(status["cookie"], "tl_session")
        self.assertEqual(status["csrf_header"], "X-CSRF-Token")
        self.assertEqual(status["token_header"], "X-TraceLens-Token")
        self.assertEqual(status["pbkdf2_iterations"], ITER)
        self.assertEqual(status["roles"], list(au.TEAM_ROLES))

    def test_local_mode_never_requires_login(self) -> None:
        auth = local("local-required")
        self.assertFalse(auth.required)
        session = auth.local_session()
        self.assertEqual(session["role"], "local")
        self.assertEqual(session["user_id"], au.LOCAL_USER_ID)
        self.assertEqual(session["workspace_id"], au.WORKSPACE_ID)
        self.assertTrue(session["local"])
        self.assertNotIn("token", session, "local 不发 token")
        active = auth.require(session, "intervene.decide")
        self.assertEqual(active["role"], "local")


# --------------------------------------------------------------------------- #
# 4. 用户 CRUD 与护栏
# --------------------------------------------------------------------------- #

class UserCrudTests(AuthCaseMixin):
    def setUp(self) -> None:
        self.auth = team("crud")
        self.boot = self.auth.bootstrap_report or {}
        self.admin = self.auth.authenticate("admin", str(self.boot["password"]))

    def test_create_list_and_public_shape(self) -> None:
        user = self.auth.create_user("ops-li", "editor", PW_EDITOR,
                                     display_name="Ops Li", actor=self.admin)
        self.assertEqual(user["role"], "editor")
        self.assertNotIn("password_hash", user)
        rows = self.auth.list_users()
        self.assertEqual(len(rows), 2)
        self.assertIn("permissions", rows[0])
        self.assertEqual(set(rows[0]["permissions"]), DOCS_MATRIX["admin"])
        found = self.auth.get_user(str(user["id"]))
        self.assertEqual(found["username"], "ops-li")
        self.assertIsNone(self.auth.get_user("usr-nope"))

    def test_username_normalising_and_validation(self) -> None:
        user = self.auth.create_user("Ops.Li", "viewer", PW_VIEWER, actor=self.admin)
        self.assertEqual(user["username"], "ops.li")
        self.assertTrue(user["id"].startswith("usr-"))
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.create_user("admin", "viewer", PW_VIEWER, actor=self.admin)
        self.assertEqual(ctx.exception.code, "USER_EXISTS")
        for bad in ("", "有中文", "-lead", "a b"):
            with self.assertRaises(au.AuthError) as ctx:
                self.auth.create_user(bad, "viewer", PW_VIEWER, actor=self.admin)
            self.assertIn(ctx.exception.code, ("USERNAME_REQUIRED", "USERNAME_INVALID"), bad)
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.create_user("ghost", "wizard", PW_VIEWER, actor=self.admin)
        self.assertEqual(ctx.exception.code, "ROLE_INVALID")

    def test_weak_password_refused_on_create(self) -> None:
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.create_user("weakling", "viewer", "1234", actor=self.admin)
        self.assertEqual(ctx.exception.code, "AUTH_WEAK_PASSWORD")

    def test_role_and_status_changes(self) -> None:
        user = self.auth.create_user("flip", "viewer", PW_VIEWER, actor=self.admin)
        changed = self.auth.set_role(str(user["id"]), "editor", actor=self.admin)
        self.assertEqual(changed["role"], "editor")
        row = self.auth.public_user(self.auth.get_user(str(user["id"])))
        self.assertEqual(set(row["permissions"]), DOCS_MATRIX["editor"])
        self.assertNotIn("password_hash", row)
        disabled = self.auth.set_status(str(user["id"]), "disabled", actor=self.admin)
        self.assertEqual(disabled["status"], "disabled")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.authenticate("flip", PW_VIEWER)
        self.assertEqual(ctx.exception.code, "AUTH_USER_DISABLED")
        self.assertEqual(ctx.exception.status, 403)
        self.auth.set_status(str(user["id"]), "active", actor=self.admin)
        self.assertEqual(self.auth.authenticate("flip", PW_VIEWER)["role"], "editor")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.set_status(str(user["id"]), "suspended", actor=self.admin)
        self.assertEqual(ctx.exception.code, "USER_STATUS_INVALID")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.set_role(str(user["id"]), "wizard", actor=self.admin)
        self.assertEqual(ctx.exception.code, "ROLE_INVALID")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.set_role("usr-missing", "editor", actor=self.admin)
        self.assertEqual(ctx.exception.code, "USER_NOT_FOUND")

    def test_cannot_disable_self_or_last_admin(self) -> None:
        me = str(self.admin["user_id"])
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.set_status(me, "disabled", actor=self.admin)
        self.assertEqual(ctx.exception.code, "USER_SELF_DISABLE")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.set_role(me, "viewer", actor=self.admin)
        self.assertEqual(ctx.exception.code, "USER_LAST_ADMIN")
        self.assertEqual(ctx.exception.status, 409)
        # 有第二名 admin 时，先禁用另一名也允许（禁用是唯一口径）
        second = self.auth.create_user("admin2", "admin", ADMIN_PW, actor=self.admin)
        self.auth.set_status(str(second["id"]), "disabled", actor=self.admin)
        self.assertEqual(self.auth.get_user(me)["role"], "admin")
        self.auth.set_status(str(second["id"]), "active", actor=self.admin)
        # 两名 admin 在场 → 降级其中一名是允许的
        self.auth.set_role(me, "editor", actor=self.admin)
        self.assertEqual(self.auth.get_user(me)["role"], "editor")
        self.assertEqual(self.auth.get_user(str(second["id"]))["role"], "admin")

    def test_users_are_disabled_never_deleted(self) -> None:
        """鉴权域没有删除能力：任何"删用户"的入口在模块门口就被拒绝并留痕。"""
        user = self.auth.create_user("todelete", "viewer", PW_VIEWER, actor=self.admin)
        self.assertFalse(hasattr(self.auth, "delete_user"))
        self.assertFalse(hasattr(self.auth, "remove_user"))
        for name in ("delete_user", "remove_user", "purge_users", "reset_all_users"):
            with self.assertRaises(au.AuthError) as ctx:
                getattr(au, name)
            self.assertEqual(ctx.exception.code, "USER_DELETE_DISABLED", name)
        self.assertIsNotNone(self.auth.get_user(str(user["id"])))
        self.assertEqual(self.auth.get_user(str(user["id"]))["status"], "active")

    def test_reset_and_change_password(self) -> None:
        user = self.auth.create_user("rotator", "editor", PW_EDITOR, actor=self.admin)
        reset = self.auth.reset_password(str(user["id"]), None, actor=self.admin)
        issued = str(reset.get("initial_password") or "")
        self.assertTrue(issued, "管理员重置必须返回一次性口令")
        self.assertTrue(self.auth.get_user(str(user["id"]))["must_change_password"])
        self.assertEqual(self.auth.authenticate("rotator", issued)["role"], "editor")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.reset_password(str(user["id"]), "1234", actor=self.admin)
        self.assertEqual(ctx.exception.code, "AUTH_WEAK_PASSWORD")
        session = self.auth.authenticate("rotator", issued)
        self.auth.change_password(str(user["id"]), issued, PW_NEW)
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.change_password(str(user["id"]), issued, PW_EDITOR)
        self.assertEqual(ctx.exception.code, "AUTH_BAD_CREDENTIALS")
        self.assertEqual(self.auth.authenticate("rotator", PW_NEW)["role"], "editor")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.change_password(str(user["id"]), PW_NEW, PW_NEW)
        self.assertEqual(ctx.exception.code, "AUTH_PASSWORD_SAME")

    def test_password_is_never_written_to_disk(self) -> None:
        self.auth.create_user("secretive", "editor", PW_EDITOR, actor=self.admin)
        raw = pathlib.Path(self.auth.store.path).read_text(encoding="utf-8")
        for secret in (PW_EDITOR, ADMIN_PW, PW_NEW, str(self.boot["password"])):
            self.assertNotIn(secret, raw, "明文口令不得进入状态文件")
        self.assertIn(au.PASSWORD_PREFIX, raw)
        self.assertEqual(self.auth.secrets_leaked_to_disk([PW_EDITOR, ADMIN_PW]), [])
        report = self.auth.session_report()
        self.assertIn("ttl_seconds", report)
        self.assertGreaterEqual(report["total"], 1)


# --------------------------------------------------------------------------- #
# 5. 会话 token 与 CSRF
# --------------------------------------------------------------------------- #

class SessionTests(AuthCaseMixin):
    def setUp(self) -> None:
        self.clock = Clock()
        self.auth = team("session", now=self.clock)
        self.boot = self.auth.bootstrap_report or {}
        self.admin = self.auth.authenticate("admin", str(self.boot["password"]))
        self.editor_user = self.auth.create_user("edi", "editor", PW_EDITOR, actor=self.admin)

    def test_issue_verify_and_expiry(self) -> None:
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        active = self.auth.session_from_token(session["token"])
        self.assertEqual(active["role"], "editor")
        self.assertEqual(active["username"], "edi")
        self.clock.advance(self.auth.session_ttl + 5)
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.session_from_token(session["token"])
        self.assertEqual(ctx.exception.code, "AUTH_TOKEN_EXPIRED")
        self.assertEqual(ctx.exception.status, 401)

    def test_forged_token_is_rejected(self) -> None:
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        token = str(session["token"])
        forged = token[:-2] + ("ab" if not token.endswith("ab") else "cd")
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.session_from_token(forged)
        self.assertIn(ctx.exception.code, ("AUTH_TOKEN_INVALID", "AUTH_TOKEN_REVOKED"))
        for junk in ("", "abc", "a.b.c", "x" * 300):
            with self.assertRaises(au.AuthError) as ctx:
                self.auth.session_from_token(junk)
            self.assertIn(ctx.exception.code, ("AUTH_TOKEN_INVALID", "AUTH_TOKEN_REVOKED"))

    def test_logout_revokes_token(self) -> None:
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        self.auth.logout(session["token"])
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.session_from_token(session["token"])
        self.assertEqual(ctx.exception.code, "AUTH_TOKEN_REVOKED")
        self.auth.logout(session["token"])          # 幂等：重复登出不报错

    def test_role_comes_from_the_database_not_the_token(self) -> None:
        """token 里的角色不是授权依据：改角色后旧 token 立刻按新角色判定。"""
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        self.auth.set_role(str(self.editor_user["id"]), "viewer", actor=self.admin)
        active = self.auth.session_from_token(session["token"])
        self.assertEqual(active["role"], "viewer")
        with self.assertRaises(au.AuthForbiddenError) as ctx:
            self.auth.require(active, "intervene.decide")
        self.assertEqual(ctx.exception.code, "ROLE_FORBIDDEN")

    def test_disable_revokes_live_sessions(self) -> None:
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        self.auth.set_status(str(self.editor_user["id"]), "disabled", actor=self.admin)
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.session_from_token(session["token"])
        self.assertIn(ctx.exception.code, ("AUTH_TOKEN_REVOKED", "AUTH_USER_DISABLED"),
                      "禁用必须立刻让在线会话失效（吊销或禁用码都算对）")
        self.assertEqual(ctx.exception.status, 401 if ctx.exception.code == "AUTH_TOKEN_REVOKED" else 403)

    def test_require_is_the_single_enforcement_point(self) -> None:
        active = self.auth.session_from_token(
            self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))["token"])
        self.assertEqual(self.auth.require(active, "intervene.decide")["role"], "editor")
        for perm in sorted(DOCS_MATRIX["editor"]):
            self.auth.require(active, perm)
        for perm in sorted(DOCS_MATRIX["admin"] - DOCS_MATRIX["editor"]):
            with self.assertRaises(au.AuthForbiddenError) as ctx:
                self.auth.require(active, perm)
            self.assertEqual(ctx.exception.code, "ROLE_FORBIDDEN")
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(au.AuthRequiredError) as ctx:
            self.auth.require(None, "records.read")
        self.assertEqual(ctx.exception.code, "AUTH_REQUIRED")
        self.assertEqual(ctx.exception.status, 401)
        with self.assertRaises(au.AuthError) as ctx:
            self.auth.require(active, "not.a.permission")
        self.assertEqual(ctx.exception.code, "PERMISSION_UNKNOWN")

    def test_csrf_double_submit(self) -> None:
        session = self.auth.issue_session(self.auth.get_user(str(self.editor_user["id"])))
        token = self.auth.csrf_token_for(session)
        self.auth.check_csrf(session, token)
        for bad in (None, "", token + "x", "other-token"):
            with self.assertRaises(au.AuthForbiddenError) as ctx:
                self.auth.check_csrf(session, bad)
            self.assertEqual(ctx.exception.code, "CSRF_FAILED")

    def test_auth_events_are_emitted_and_drained_once(self) -> None:
        self.auth.take_events()
        with self.assertRaises(au.AuthError):
            self.auth.authenticate("edi", "wrong-passphrase-1")
        events = self.auth.take_events()
        self.assertTrue(any(e["event"] == "auth.login_failed" and e["reason"] == "bad_password"
                            for e in events), events)
        self.assertEqual(self.auth.take_events(), [], "事件必须被取走，不重复落盘")
        self.auth.authenticate("edi", PW_EDITOR)
        events = self.auth.take_events()
        self.assertTrue(any(e["event"] == "auth.login" and e["ok"] for e in events))
        for event in events:
            blob = json.dumps(event, ensure_ascii=False)
            self.assertNotIn(PW_EDITOR, blob, "鉴权事件不得包含口令")
        self.assertEqual(set(au.AUTH_EVENTS) >= {"auth.login", "auth.login_failed", "auth.logout"},
                         True)


# --------------------------------------------------------------------------- #
# 6. 暴力尝试锁定
# --------------------------------------------------------------------------- #

class LockoutTests(AuthCaseMixin):
    def test_five_failures_lock_the_account(self) -> None:
        auth = team("lockout", iterations=ITER, env={}, max_failures=5, lockout_seconds=900)
        boot = auth.bootstrap_report or {}
        admin = auth.authenticate("admin", str(boot["password"]))
        auth.create_user("brute", "viewer", PW_VIEWER, actor=admin)
        codes = []
        for _ in range(4):
            with self.assertRaises(au.AuthError) as ctx:
                auth.authenticate("brute", "wrong-passphrase-1")
            codes.append(ctx.exception.code)
        self.assertEqual(codes, ["AUTH_BAD_CREDENTIALS"] * 4)
        with self.assertRaises(au.AuthRateLimitError) as ctx:
            auth.authenticate("brute", "wrong-passphrase-1")
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")
        self.assertEqual(ctx.exception.status, 429)
        self.assertGreater(ctx.exception.retry_after, 0)
        with self.assertRaises(au.AuthRateLimitError):
            auth.authenticate("brute", PW_VIEWER)          # 锁定期内连对口令也不给进
        with self.assertRaises(au.AuthRateLimitError) as ctx:
            auth.authenticate("brute", PW_VIEWER)
        self.assertGreaterEqual(ctx.exception.retry_after, 1)

    def test_success_resets_the_counter(self) -> None:
        auth = team("lockout-reset", iterations=ITER, env={}, max_failures=3, lockout_seconds=600)
        boot = auth.bootstrap_report or {}
        admin = auth.authenticate("admin", str(boot["password"]))
        auth.create_user("slip", "viewer", PW_VIEWER, actor=admin)
        for _ in range(2):
            with self.assertRaises(au.AuthError):
                auth.authenticate("slip", "wrong-passphrase-1")
        auth.authenticate("slip", PW_VIEWER)
        for _ in range(2):
            with self.assertRaises(au.AuthError):
                auth.authenticate("slip", "wrong-passphrase-2")
        auth.authenticate("slip", PW_VIEWER)               # 没被锁 → 说明计数已清零


# --------------------------------------------------------------------------- #
# 7. 状态持久化
# --------------------------------------------------------------------------- #

class PersistenceTests(AuthCaseMixin):
    def test_state_survives_a_restart(self) -> None:
        path = state_path("restart")
        first = au.Authenticator("team", state_path=path, iterations=ITER, env={})
        boot = first.bootstrap_report or {}
        created = first.create_user("persist", "editor", PW_EDITOR, actor=first.local_session())
        second = au.Authenticator("team", state_path=path, iterations=ITER, env={}, bootstrap=False)
        self.assertEqual({u["username"] for u in second.list_users()}, {"admin", "persist"})
        self.assertEqual(set(second.public_user(second.get_user(str(created["id"])))["permissions"]),
                         DOCS_MATRIX["editor"])
        session = second.authenticate("persist", PW_EDITOR)
        self.assertEqual(session["username"], "persist")
        with self.assertRaises(au.AuthError) as ctx:
            first.session_from_token(session["token"])
        self.assertIn(ctx.exception.code, ("AUTH_TOKEN_INVALID", "AUTH_TOKEN_REVOKED"),
                      "旧进程的签名密钥与新进程必须一致（密钥也落盘）")

    def test_secret_is_reused_across_restarts(self) -> None:
        path = state_path("secret")
        first = au.Authenticator("team", state_path=path, iterations=ITER, env={}, bootstrap=False)
        first.create_user("anchor", "viewer", PW_VIEWER, actor=first.local_session())
        session = first.issue_session(first.get_user(first._user_id("anchor")))
        second = au.Authenticator("team", state_path=path, iterations=ITER, env={}, bootstrap=False)
        self.assertEqual(second.session_from_token(session["token"])["username"], "anchor")

    def test_state_file_shape_and_no_secrets(self) -> None:
        path = state_path("shape")
        auth = au.Authenticator("team", state_path=path, iterations=ITER,
                                env={"TRACELENS_ADMIN_PASSWORD": ADMIN_PW})
        auth.create_user("shaped", "editor", PW_EDITOR, actor=auth.local_session())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data.keys()),
                         sorted(["schema_version", "app", "users", "sessions", "secret", "meta"]))
        self.assertNotIn(ADMIN_PW, path.read_text(encoding="utf-8"))
        self.assertNotIn(PW_EDITOR, path.read_text(encoding="utf-8"))
        self.assertEqual(auth.secrets_leaked_to_disk([ADMIN_PW, PW_EDITOR, "Bootstrap"]), [])
        self.assertTrue(path.read_text(encoding="utf-8").count(au.PASSWORD_PREFIX) >= 2)


# --------------------------------------------------------------------------- #
# 8. 纯度与"强制点唯一"（AST 扫描）
# --------------------------------------------------------------------------- #

class EnforcementDisciplineTests(unittest.TestCase):
    BANNED = ("http://", "https://", "urllib", "socket.", "subprocess")

    def test_auth_and_server_stay_offline_capable(self) -> None:
        for name in ("auth.py", "server.py"):
            source = (PKG / name).read_text(encoding="utf-8")
            for needle in self.BANNED:
                self.assertNotIn(needle, source, f"{name} 里出现了 {needle}")

    def test_no_scattered_role_comparisons_in_server(self) -> None:
        """docs/03 §5：判定只准发生在 auth.require 里。

        扫描 server.py 的 AST：任何与角色字面量做 ==/!=/in/not in 比较的表达式都算违规，
        唯一放行的是"取角色"本身（赋值右值），所以扫描只看比较节点的两边。
        """
        source = (PKG / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        role_literals = set(au.ROLES)
        offenders: list[str] = []

        def side_has_role(node: ast.AST) -> bool:
            dumped = ast.dump(node)
            return "role" in dumped.lower()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left] + list(node.comparators)
            literals = [o for o in operands
                        if isinstance(o, ast.Constant) and isinstance(o.value, str)
                        and o.value in role_literals]
            if not literals:
                continue
            if any(side_has_role(o) for o in operands if o not in literals):
                offenders.append(f"line {node.lineno}: {ast.get_source_segment(source, node)}")
        self.assertEqual(offenders, [], "散落角色判断：\n" + "\n".join(offenders))

    def test_server_delegates_every_decision_to_auth(self) -> None:
        """路由必须经过 _require / auth.require（唯一强制点），并且 auth 域异常统一外发。"""
        source = (PKG / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn("_require", names)
        self.assertIn("_resolve_session", names)
        self.assertIn("_csrf_guard", names)
        self.assertIn("_send_auth_error", names)
        self.assertIn("auth.require", source)
        self.assertIn("auth_mod.AuthError", source)

    def test_web_layer_has_no_role_literal_comparisons(self) -> None:
        """页面只读服务端发布的矩阵做隐藏/禁用，不许自己按角色名分支。

        唯一的例外是"角色显示名"映射（t6 就有的 `t('role.' + r)` 标签表）：
        那是文案，不是授权判断。所以逐行看，出现角色字面量的行必须同时是标签行。
        """
        source = (WEB / "app.js").read_text(encoding="utf-8")
        offenders = []
        for index, line in enumerate(source.splitlines(), start=1):
            if any(needle in line for needle in ("=== 'admin'", "== 'admin'", "=== 'editor'",
                                                 "== 'editor'", "=== 'viewer'", "== 'viewer'")):
                if "role." not in line:
                    offenders.append(f"line {index}: {line.strip()}")
        self.assertEqual(offenders, [], "页面出现按角色名分支：\n" + "\n".join(offenders))
        self.assertIn("canPermission(", source)
        self.assertIn("AUTH_LOCAL_PERMISSIONS", source)


# --------------------------------------------------------------------------- #
# 9. team 模式 HTTP 闭环
# --------------------------------------------------------------------------- #

class TeamHttpTests(AuthCaseMixin):
    mode = "team"

    @classmethod
    def setUpClass(cls) -> None:
        cls.state = state_path("http-team")
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0,
            records=copy.deepcopy(NATIVE), web_dir=WEB, allow_reset=True,
            mode="team", auth_state_path=cls.state, bootstrap_admin=True,
            session_ttl=3600)
        cls.host, cls.port = cls.server.server_address[:2]
        cls.boot = cls.server.service.auth.bootstrap_report or {}
        cls.admin_pw = str(cls.boot["password"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    # -- 传输助手 --
    def call(self, method: str, path: str, body=None, session: dict | None = None,
             headers: dict | None = None, csrf: bool = True) -> tuple[int, dict, object]:
        extra = dict(headers or {})
        if session:
            extra["Cookie"] = session["cookie_header"]
            if csrf and body is not None and session.get("csrf"):
                extra[au.CSRF_HEADER] = session["csrf"]
        return http_request(self.host, self.port, method, path, body, extra)

    def login(self, username: str, password: str, *, token_channel: bool = False) -> dict:
        status, headers, data = http_request(self.host, self.port, "POST",
                                             sv.API_PATH_AUTH_LOGIN,
                                             {"username": username, "password": password},
                                             {"Connection": "close"})
        self.assertEqual(status, 200, data)
        body = data if isinstance(data, dict) else {}
        cookie = str(headers.get("set-cookie") or "")
        header = f"{au.SESSION_COOKIE}={body['cookie']['value']}"
        return {"body": body, "headers": headers, "csrf": str(body.get("csrf_token") or ""),
                "token": str(body.get("token") or ""), "cookie_header": header,
                "cookie_raw": cookie, "token_channel": token_channel}

    def session_for(self, username: str, role: str, password: str) -> dict:
        self.server.service.auth.create_user(username, role, password,
                                             actor={"user_id": "admin", "role": "admin"})
        return self.login(username, password)

    def reset_records(self) -> None:
        admin = self.login("admin", self.admin_pw)
        status, _, data = self.call("POST", "/api/reset", {}, session=admin)
        self.assertEqual(status, 200, data)

    def set_min_role(self, min_role: str | None) -> None:
        """改服务端里那条记录的决策门槛。

        `store.get()` 返回的是深拷贝，改完必须 `store.put()` 写回去，否则门槛改不动。
        """
        record = self.server.service.store.get(PENDING)
        self.assertIsNotNone(record)
        policy = dict(record.get("policy") or {})
        if min_role is None:
            policy.pop("min_role_to_decide", None)
        else:
            policy["min_role_to_decide"] = min_role
        record["policy"] = policy
        self.server.service.store.put(record)
        self.assertEqual((self.server.service.store.get(PENDING).get("policy") or {}).get(
            "min_role_to_decide"), min_role)

    # -- 用例 --
    def test_public_probes_and_static_shell(self) -> None:
        status, _, data = self.call("GET", sv.API_PATH_HEALTH)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["auth"]["required"], True)
        self.assertEqual(data["mode"], "team")
        status, _, data = self.call("GET", sv.API_PATH_AUTH_STATUS)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["required"], True)
        self.assertEqual(data["cookie"], au.SESSION_COOKIE)
        self.assertTrue(data["ui"]["show_login"])
        self.assertEqual(data["ui"]["users_path"], sv.API_PATH_USERS)
        status, _, data = self.call("GET", sv.API_PATH_AUTH_PERMISSIONS)
        self.assertEqual(status, 200, data)
        for role, expected in DOCS_MATRIX.items():
            self.assertEqual(set(data["matrix"][role]), expected)
        status, headers, _ = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))

    def test_every_data_route_is_401_without_a_session(self) -> None:
        for path in (sv.API_PATH_RECORDS, sv.API_PATH_RECORDS + "/" + PENDING,
                     sv.API_PATH_AUDIT, sv.API_PATH_AUTH_ME, sv.API_PATH_USERS):
            status, headers, data = self.call("GET", path)
            self.assertEqual(status, 401, f"{path} 未登录应 401：{data}")
            self.assert_unified(status, headers, data, "AUTH_REQUIRED")
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve"})
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), "AUTH_REQUIRED")

    def test_login_writes_the_session_cookie_and_headers(self) -> None:
        status, headers, data = http_request(self.host, self.port, "POST",
                                             sv.API_PATH_AUTH_LOGIN,
                                             {"username": "admin", "password": "wrong-passphrase-1"},
                                             {"Connection": "close"})
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), "AUTH_BAD_CREDENTIALS")
        session = self.login("admin", self.admin_pw)
        cookie = session["cookie_raw"]
        self.assertIn(f"{au.SESSION_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=", cookie)
        self.assertNotIn("Secure;", cookie, "本机 http 下不该带 Secure（--secure-cookies 才加）")
        self.assertTrue(session["csrf"])
        self.assertTrue(session["token"])
        self.assertEqual(session["headers"].get("x-auth-role"), "admin")
        self.assertEqual(session["body"]["user"]["role"], "admin")
        self.assertNotIn("password", session["body"], "登录响应体顶层不得有 password 字段")
        self.assertNotIn(self.admin_pw, json.dumps(session["body"], ensure_ascii=False),
                         "登录响应里绝不出现口令")
        status, _, data = self.call("GET", sv.API_PATH_AUTH_ME, session=session)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["role"], "admin")
        self.assertEqual(set(data["permissions"]), DOCS_MATRIX["admin"])
        self.assertTrue(data["ui"]["show_users"])
        self.assertEqual(data["username"], "admin")
        self.assertEqual(data["actor_id"], session["body"]["user"]["actor_id"],
                         "team 模式的 actor_id 是用户 id（可追溯）")
        self.assertTrue(str(data["actor_id"]).startswith("usr-"))

    def test_cookie_writes_need_the_csrf_header(self) -> None:
        session = self.login("admin", self.admin_pw)
        status, _, data = self.call("POST", sv.API_PATH_AUTH_PASSWORD,
                                    {"old_password": self.admin_pw, "new_password": PW_NEW},
                                    session=session, csrf=False)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "CSRF_FAILED")
        status, _, data = self.call("POST", sv.API_PATH_AUTH_PASSWORD,
                                    {"old_password": self.admin_pw, "new_password": PW_NEW},
                                    session=session)
        self.assertEqual(status, 200, data)
        again = self.login("admin", PW_NEW)
        self.assertEqual(again["body"]["user"]["role"], "admin")
        # 交还给原口令，其它用例继续用
        self.call("POST", sv.API_PATH_AUTH_PASSWORD,
                  {"old_password": PW_NEW, "new_password": self.admin_pw}, session=again)

    def test_token_channel_is_exempt_from_csrf_and_still_authorized(self) -> None:
        editor = self.session_for("edi-tok", "editor", PW_EDITOR)
        headers = {au.TOKEN_HEADER: editor["token"]}
        self.reset_records()
        status, _, data = http_request(self.host, self.port, "POST", "/intervene",
                                       {"action_id": PENDING, "decision": "approve",
                                        "idempotency_key": "t13-token-1"},
                                       {"Connection": "close", au.TOKEN_HEADER: editor["token"]})
        self.assertEqual(status, 200, data)
        ui = data["user_intervention"]
        self.assertEqual(ui["decision"], "approve")
        self.assertEqual(ui["decided_by"], "edi-tok")
        self.assertEqual(ui["decided_by_role"], "editor")
        self.assertNotEqual(ui["decided_by"], "local-user")
        status, _, data = http_request(self.host, self.port, "POST", "/intervene",
                                       {"action_id": "act-demo-0005", "decision": "approve"},
                                       {"Connection": "close", au.TOKEN_HEADER: "forged.token"})
        self.assertEqual(status, 401, data)
        self.assertIn(code_of(data), ("AUTH_REQUIRED", "AUTH_TOKEN_INVALID"))
        self.assertTrue(headers)

    def test_viewer_is_forbidden_but_can_read(self) -> None:
        viewer = self.session_for("watch", "viewer", PW_VIEWER)
        self.reset_records()
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve",
                                     "idempotency_key": "t13-viewer-1"}, session=viewer)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        self.assertEqual(detail_of(data).get("permission"), "intervene.decide")
        status, _, data = self.call("GET", sv.API_PATH_RECORDS, session=viewer)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["records"])
        status, _, data = self.call("GET", sv.API_PATH_AUDIT, session=viewer)
        self.assertEqual(status, 200, data)
        status, _, data = self.call("GET", sv.API_PATH_USERS, session=viewer)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        status, _, data = self.call("POST", "/api/reset", {}, session=viewer)
        self.assertEqual(status, 403, data)
        status, _, data = self.call("GET", sv.API_PATH_AUTH_ME, session=viewer)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["ui"]["show_intervene"], "viewer 的干预入口该隐藏")
        self.assertFalse(data["ui"]["show_users"], "viewer 的用户管理入口该隐藏")
        status, _, data = self.call("POST", sv.API_PATH_AUTH_CHECK,
                                    {"permission": "intervene.decide"}, session=viewer)
        self.assertEqual(status, 403, data)
        status, _, data = self.call("POST", sv.API_PATH_AUTH_CHECK,
                                    {"permission": "audit.read"}, session=viewer)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["allowed"])
        # 越权的决定不会改变记录
        status, _, record = self.call("GET", sv.API_PATH_RECORDS + "/" + PENDING, session=viewer)
        self.assertEqual(record["status"], "pending_confirmation", record)

    def test_editor_decides_but_cannot_administer(self) -> None:
        editor = self.session_for("edi", "editor", PW_EDITOR)
        self.reset_records()
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "modify",
                                     "modified_params": MODIFY_PARAMS,
                                     "reason": "删少一点，进回收站",
                                     "idempotency_key": "t13-editor-1"}, session=editor)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["status"], "modified")
        self.assertEqual(data["user_intervention"]["decided_by"], "edi")
        self.assertEqual(data["user_intervention"]["decided_by_role"], "editor")
        status, _, data = self.call("GET", sv.API_PATH_USERS, session=editor)
        self.assertEqual(status, 403, data)
        status, _, data = self.call("POST", sv.API_PATH_USERS,
                                    {"username": "ghost", "role": "viewer"}, session=editor)
        self.assertEqual(status, 403, data)
        status, _, data = self.call("POST", "/api/reset", {}, session=editor)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        status, _, data = self.call("GET", sv.API_PATH_USERS, session=editor)
        self.assertEqual(status, 403, data)

    def test_record_level_min_role_gate(self) -> None:
        editor = self.session_for("edi-gate", "editor", PW_EDITOR)
        self.reset_records()
        self.set_min_role("admin")
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve",
                                     "idempotency_key": "t13-gate-1"}, session=editor)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        self.assertEqual(detail_of(data).get("field"), "policy.min_role_to_decide")
        self.assertEqual(detail_of(data).get("min_role_to_decide"), "admin")
        self.assertEqual(detail_of(data).get("role"), "editor")
        status, _, record = self.call("GET", sv.API_PATH_RECORDS + "/" + PENDING, session=editor)
        self.assertEqual(record["status"], "pending_confirmation", "被挡下的决定不得改记录")
        self.set_min_role("editor")
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve",
                                     "idempotency_key": "t13-gate-2"}, session=editor)
        self.assertEqual(status, 200, data)
        status, _, data = self.call("POST", sv.API_PATH_AUTH_CHECK,
                                    {"permission": "intervene.decide",
                                     "min_role_to_decide": "admin"}, session=editor)
        self.assertEqual(status, 403, data)
        self.set_min_role(None)

    def test_admin_user_crud_over_http(self) -> None:
        admin = self.login("admin", self.admin_pw)
        status, _, data = self.call("POST", sv.API_PATH_USERS,
                                    {"username": "New.User", "role": "viewer"}, session=admin)
        self.assertEqual(status, 201, data)
        created = data["user"]
        self.assertEqual(created["username"], "new.user")
        self.assertEqual(created["role"], "viewer")
        issued = str(created.get("initial_password") or "")
        self.assertTrue(issued, "未指定口令时必须返回一次性初始口令")
        self.assertNotIn("password_hash", json.dumps(data))
        status, _, data = self.call("GET", sv.API_PATH_USERS, session=admin)
        self.assertEqual(status, 200, data)
        self.assertGreaterEqual(data["count"], 2)
        self.assertTrue(any(u["username"] == "new.user" for u in data["data"]))
        self.assertNotIn("password_hash", json.dumps(data))
        # 指定口令创建
        status, _, data = self.call("POST", sv.API_PATH_USERS,
                                    {"username": "chosen", "role": "editor",
                                     "password": PW_EDITOR}, session=admin)
        self.assertEqual(status, 201, data)
        self.assertFalse(data["user"].get("initial_password"))
        chosen_id = data["user"]["id"]
        # 改角色 / 禁用 / 重置
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{chosen_id}/role",
                                    {"role": "viewer"}, session=admin)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["role"], "viewer")
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{chosen_id}/status",
                                    {"status": "disabled"}, session=admin)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["status"], "disabled")
        status, _, data = http_request(self.host, self.port, "POST", sv.API_PATH_AUTH_LOGIN,
                                       {"username": "chosen", "password": PW_EDITOR},
                                       {"Connection": "close"})
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "AUTH_USER_DISABLED")
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{chosen_id}/password",
                                    {}, session=admin)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["user"]["must_change_password"])
        # 未知字段 / 未知用户 / 未知子资源
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{chosen_id}/role",
                                    {"rolex": "viewer"}, session=admin)
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "INTERVENE_FIELD_UNKNOWN")
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/usr-missing/role",
                                    {"role": "viewer"}, session=admin)
        self.assertEqual(status, 404, data)
        self.assertEqual(code_of(data), "USER_NOT_FOUND")
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{chosen_id}/delete",
                                    {}, session=admin)
        self.assertEqual(status, 404, data)
        self.assertEqual(code_of(data), "ROUTE_NOT_FOUND")
        self.assertEqual(detail_of(data).get("allowed"), ["role", "status", "password"])
        # t8 冻结的方法口径：PUT/PATCH/DELETE 永远 405
        for method in ("PUT", "PATCH", "DELETE"):
            status, _, data = self.call(method, sv.API_PATH_USERS, {"username": "x"}, session=admin)
            self.assertEqual(status, 405, f"{method} 应 405：{data}")
            self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED")
        # 最后一遍筛选参数（列表可查）
        status, _, data = self.call("GET", f"{sv.API_PATH_USERS}?q=new&status=active", session=admin)
        self.assertEqual(status, 200, data)
        self.assertTrue(all(u["status"] == "active" for u in data["data"]))

    def test_admin_cannot_be_demoted_or_disabled_over_http(self) -> None:
        admin = self.login("admin", self.admin_pw)
        rows = self.call("GET", sv.API_PATH_USERS, session=admin)[2]["data"]
        me_row = next(u for u in rows if u["username"] == "admin")
        # 自己不能把自己关在门外
        for verb, body in (("role", {"role": "viewer"}), ("status", {"status": "disabled"})):
            status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{me_row['id']}/{verb}",
                                        body, session=admin)
            self.assertEqual(status, 409, f"{verb} 应被挡住：{data}")
            self.assertIn(code_of(data), ("USER_SELF_DISABLE", "USER_LAST_ADMIN"),
                          "两条护栏都算：不能把自己关在门外")
        # 有两名 admin 时禁用另一名是允许的（禁用的门槛是"还剩一名可用 admin"）
        created = self.call("POST", sv.API_PATH_USERS,
                            {"username": "adminb", "role": "admin"}, session=admin)[2]["user"]
        issued = str(created.get("initial_password") or "")
        self.assertTrue(issued)
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{created['id']}/status",
                                    {"status": "disabled"}, session=admin)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["status"], "disabled")
        # 禁用中的账号登录不了（禁用是唯一口径，不是删除）
        status, _, data = http_request(self.host, self.port, "POST", sv.API_PATH_AUTH_LOGIN,
                                       {"username": "adminb", "password": issued},
                                       {"Connection": "close"})
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "AUTH_USER_DISABLED")
        self.assertEqual(self.call("GET", sv.API_PATH_USERS, session=admin)[2]["count"],
                         len(self.call("GET", sv.API_PATH_USERS, session=admin)[2]["data"]))
        status, _, data = self.call("POST", f"{sv.API_PATH_USERS}/{created['id']}/status",
                                    {"status": "active"}, session=admin)
        self.assertEqual(status, 200, data)
        again = self.login("adminb", issued)
        self.assertEqual(again["body"]["user"]["role"], "admin")

    def test_logout_revokes_the_cookie(self) -> None:
        session = self.session_for("leaver", "editor", PW_EDITOR)
        status, _, data = self.call("GET", sv.API_PATH_AUTH_ME, session=session)
        self.assertEqual(status, 200, data)
        status, headers, data = self.call("POST", sv.API_PATH_AUTH_LOGOUT, {}, session=session)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        cookie = str(headers.get("set-cookie") or "")
        self.assertIn(f"{au.SESSION_COOKIE}=", cookie, "登出必须把会话 cookie 清掉")
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("HttpOnly", cookie)
        status, _, data = self.call("GET", sv.API_PATH_AUTH_ME, session=session)
        self.assertEqual(status, 401, data)
        status, _, data = self.call("GET", sv.API_PATH_RECORDS,
                                    headers={au.TOKEN_HEADER: session["token"]})
        self.assertEqual(status, 401, data)

    def test_repeated_failures_answer_429_with_retry_after(self) -> None:
        self.session_for("hammer", "viewer", PW_VIEWER)
        last = None
        for _ in range(5):
            last = http_request(self.host, self.port, "POST", sv.API_PATH_AUTH_LOGIN,
                                {"username": "hammer", "password": "wrong-passphrase-9"},
                                {"Connection": "close"})
            self.assertIn(last[0], (401, 429), last[2])
        status, headers, data = last
        self.assertEqual(status, 429, data)
        self.assertEqual(code_of(data), "RATE_LIMITED")
        self.assertGreaterEqual(int(headers.get("retry-after") or 0), 1)
        self.assert_unified(status, headers, data, "RATE_LIMITED")
        self.assertEqual(detail_of(data).get("username"), "hammer")

    def test_audit_records_the_real_actor_and_auth_events(self) -> None:
        self.reset_records()
        editor = self.session_for("auditor", "editor", PW_EDITOR)
        viewer = self.session_for("peeker", "viewer", PW_VIEWER)
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve",
                                     "idempotency_key": "t13-audit-1"}, session=editor)
        self.assertEqual(status, 200, data)
        self.call("POST", "/intervene", {"action_id": "act-demo-0005", "decision": "approve"},
                  session=viewer)
        status, _, audit = self.call("GET", sv.API_PATH_AUDIT + "?limit=50", session=editor)
        self.assertEqual(status, 200, audit)
        blob = json.dumps(audit, ensure_ascii=False)
        self.assertIn("auditor", blob, "审计里必须查得到是谁做的决定")
        self.assertIn("member", blob, "editor 在审计里的身份标签是 member")
        decisions = [e for e in audit["entries"] if e.get("event") == "intervention.decision"]
        self.assertTrue(decisions)
        self.assertEqual(decisions[0]["actor_id"], "auditor")
        self.assertEqual(decisions[0]["actor_role"], "member", "editor 的审计身份是 member")
        auth_events = [e for e in audit["entries"] if str(e.get("event", "")).startswith("auth.")]
        self.assertTrue(auth_events, "登录/越权事件必须进审计链")
        self.assertTrue(any(e.get("event") == "auth.denied" for e in auth_events),
                        "越权 403 必须留痕")
        for entry in audit["entries"]:
            self.assertNotIn(PW_EDITOR, json.dumps(entry, ensure_ascii=False))
        self.assertNotIn(PW_EDITOR, blob, "审计里绝不出现口令")
        # 哈希链仍然连续（t9 的链不能因为 4.3 断掉）
        entries = audit["entries"]
        self.assertTrue(entries[-1]["prev_hash"], "链首必须挂在 genesis")

    def test_jsonl_sink_mirrors_denials_when_enabled(self) -> None:
        folder = TMP / "http-team-jsonl"
        folder.mkdir(parents=True, exist_ok=True)
        sink = folder / "audit-mirror.jsonl"
        if sink.exists():
            sink.unlink()
        srv, thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(NATIVE), web_dir=WEB, allow_reset=True,
            mode="team", auth_state_path=state_path("http-team-jsonl"),
            bootstrap_admin=True, audit_log_path=sink)
        host, port = srv.server_address[:2]
        try:
            boot = srv.service.auth.bootstrap_report or {}
            st, hd, data = http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                                        {"username": "admin", "password": str(boot["password"])},
                                        {"Connection": "close"})
            self.assertEqual(st, 200, data)
            cookie = f"{au.SESSION_COOKIE}={data['cookie']['value']}"
            st, _, data = http_request(host, port, "POST", sv.API_PATH_USERS,
                                       {"username": "jview", "role": "viewer"},
                                       {"Connection": "close", "Cookie": cookie,
                                        au.CSRF_HEADER: data["csrf_token"]})
            self.assertEqual(st, 201, data)
            st, _, data = http_request(host, port, "POST", sv.API_PATH_AUTH_LOGIN,
                                       {"username": "jview",
                                        "password": data["user"]["initial_password"]},
                                       {"Connection": "close"})
            vcookie = f"{au.SESSION_COOKIE}={data['cookie']['value']}"
            st, _, data = http_request(host, port, "POST", "/intervene",
                                       {"action_id": PENDING, "decision": "approve"},
                                       {"Connection": "close", "Cookie": vcookie})
            self.assertEqual(st, 403, data)
            lines = [json.loads(line) for line in
                     sink.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(lines, "JSONL 落盘必须有内容")
            events = [e.get("event") for e in lines]
            self.assertIn("access_denied", events, events)
            denial = next(e for e in lines if e.get("event") == "access_denied")
            self.assertEqual(denial["fields"]["auth_event"], "auth.denied")
            self.assertEqual(denial["fields"]["role"], "viewer")
            self.assertNotIn(PW_VIEWER, sink.read_text(encoding="utf-8"))
            st, _, audit = http_request(host, port, "GET", sv.API_PATH_AUDIT, None,
                                        {"Connection": "close", "Cookie": vcookie})
            self.assertIn("jsonl", audit)
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

    def test_query_and_header_abuse_is_rejected(self) -> None:
        admin = self.login("admin", self.admin_pw)
        status, _, data = self.call("GET", sv.API_PATH_USERS, session=admin,
                                    headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 200, data)
        status, _, data = self.call("POST", sv.API_PATH_USERS, "not json", session=admin)
        self.assertEqual(status, 400, data)
        self.assertIn(code_of(data), ("INTERVENE_BODY_NOT_OBJECT", "INTERVENE_BODY_INVALID"))
        status, _, data = self.call("POST", sv.API_PATH_AUTH_LOGIN,
                                    {"username": "admin", "password": self.admin_pw,
                                     "role": "admin"}, session=None)
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "INTERVENE_FIELD_UNKNOWN")
        status, _, data = self.call("OPTIONS", sv.API_PATH_AUTH_LOGIN)
        self.assertEqual(status, 405, data)


# --------------------------------------------------------------------------- #
# 10. 监听地址：team 要能被局域网访问
# --------------------------------------------------------------------------- #

class TeamBindTests(AuthCaseMixin):
    def test_team_default_bind_is_lan_reachable(self) -> None:
        self.assertEqual(sv.TEAM_HOST, "0.0.0.0")
        self.assertEqual(sv.MODE_TEAM, "team")
        srv, thread = sv.start_background_server(
            sv.TEAM_HOST, 0, records=copy.deepcopy(NATIVE), web_dir=WEB, allow_reset=True,
            mode="team", auth_state_path=state_path("bind-team"), bootstrap_admin=True)
        host, port = srv.server_address[:2]
        try:
            self.assertEqual(host, "0.0.0.0", "team 模式必须监听 0.0.0.0（局域网/Docker）")
            status, _, data = http_request("127.0.0.1", port, "GET", sv.API_PATH_HEALTH, None,
                                           {"Connection": "close"})
            self.assertEqual(status, 200, data)
            self.assertEqual(data["mode"], "team")
            self.assertTrue(data["auth"]["required"])
            status, _, data = http_request("127.0.0.1", port, "GET", sv.API_PATH_RECORDS, None,
                                           {"Connection": "close"})
            self.assertEqual(status, 401, data)
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

    def test_local_default_bind_stays_loopback(self) -> None:
        srv, thread = sv.start_background_server("127.0.0.1", 0, records=copy.deepcopy(NATIVE),
                                                 web_dir=WEB, allow_reset=True)
        try:
            self.assertEqual(srv.server_address[0], "127.0.0.1")
            status, _, data = http_request("127.0.0.1", srv.server_address[1],
                                           "GET", sv.API_PATH_HEALTH, None, {"Connection": "close"})
            self.assertEqual(status, 200, data)
            self.assertEqual(data["mode"], "local")
            self.assertFalse(data["auth"]["required"])
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)

    def test_cli_mode_flags_exist(self) -> None:
        """命令行必须能选模式（默认 local），并把一次性口令交给控制台。"""
        parser_flags = ("--mode", "--auth-state", "--session-ttl", "--no-bootstrap",
                        "--audit-log", "--secure-cookies")
        source = (PKG / "server.py").read_text(encoding="utf-8")
        for flag in parser_flags:
            self.assertIn(f'"{flag}"', source, f"缺少 {flag}")
        self.assertIn("bootstrap_report", source)
        self.assertIn("TRACELENS_ADMIN_PASSWORD", source)

    def test_local_mode_keeps_auth_state_out_of_the_repo(self) -> None:
        """本地模式没有用户表：鉴权状态默认放系统临时目录，别在仓库里留签名密钥。"""
        tmp = pathlib.Path(tempfile.gettempdir()).resolve()
        local_path = sv.default_auth_state_path(sv.MODE_LOCAL).resolve()
        self.assertTrue(local_path.is_relative_to(tmp), local_path)
        self.assertEqual(local_path.name, au.STATE_FILE_NAME)
        self.assertEqual(sv.default_auth_state_path(sv.MODE_TEAM), au.default_state_path())


# --------------------------------------------------------------------------- #
# 11. local 模式逐字不变（t8 护栏）
# --------------------------------------------------------------------------- #

class LocalModeParityTests(AuthCaseMixin):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(NATIVE), web_dir=WEB, allow_reset=True)
        cls.host, cls.port = cls.server.server_address[:2]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def call(self, method: str, path: str, body=None,
             headers: dict | None = None) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, method, path, body, headers)

    def test_no_login_no_users_no_setup(self) -> None:
        status, _, data = self.call("GET", sv.API_PATH_AUTH_ME)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["role"], "local")
        self.assertEqual(data["actor_id"], au.LOCAL_USER_ID)
        self.assertTrue(data["local"])
        self.assertFalse(data["ui"]["show_login"], "local 模式不得出现登录页")
        self.assertFalse(data["ui"]["show_users"], "local 没有用户表")
        status, _, data = self.call("GET", sv.API_PATH_USERS)
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        status, _, data = self.call("POST", sv.API_PATH_USERS,
                                    {"username": "ghost", "role": "viewer"})
        self.assertEqual(status, 403, data)
        status, _, data = self.call("POST", sv.API_PATH_AUTH_LOGIN,
                                    {"username": "admin", "password": ADMIN_PW})
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), "AUTH_BAD_CREDENTIALS")
        status, _, data = self.call("GET", sv.API_PATH_AUTH_STATUS)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["required"])
        self.assertFalse(data["bootstrap_needed"])

    def test_t8_behaviour_is_byte_identical(self) -> None:
        status, _, data = self.call("GET", sv.API_PATH_HEALTH)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["auth"]["required"])
        status, _, data = self.call("GET", sv.API_PATH_RECORDS)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(data["records"]), len(NATIVE))
        status, _, data = self.call("POST", "/intervene",
                                    {"action_id": PENDING, "decision": "approve",
                                     "idempotency_key": "t13-local-1"})
        self.assertEqual(status, 200, data)
        ui = data["user_intervention"]
        self.assertEqual(ui["decided_by"], sv.LOCAL_ACTOR_ID, "local 决定人仍是 local-user")
        self.assertEqual(ui["decided_by_role"], sv.LOCAL_ACTOR_ROLE)
        self.assertEqual(data["status"], "approved")
        status, _, audit = self.call("GET", sv.API_PATH_AUDIT)
        self.assertEqual(status, 200, audit)
        self.assertEqual(audit["entries"][0]["prev_hash"], sv.GENESIS_HASH,
                         "审计链首仍接 genesis")
        decisions = [e for e in audit["entries"] if e.get("event") == "intervention.decision"]
        self.assertTrue(decisions)
        self.assertEqual(decisions[-1]["actor_id"], sv.LOCAL_ACTOR_ID,
                         "4.1 钉住：本地模式的决定人与审计条仍是 local-user")
        self.assertEqual(decisions[-1]["mode"], "local", "审计 mode 用的是 4.2 的枚举值")
        for entry in [e for e in audit["entries"]
                      if str(e.get("event", "")).startswith("auth.")]:
            self.assertIn(entry["actor_id"], (au.LOCAL_USER_ID, None),
                          "鉴权域内部身份是 local（docs/03 §5.5）；失败的登录还没有身份")
            self.assertNotEqual(entry["actor_id"], sv.LOCAL_ACTOR_ID,
                                "鉴权条不借用决定段的 local-user")
        status, _, data = self.call("POST", "/api/reset", {})
        self.assertEqual(status, 200, data)
        for method in ("PUT", "PATCH", "DELETE"):
            status, _, data = self.call(method, "/intervene", {"action_id": PENDING})
            self.assertEqual(status, 405, f"{method} 应 405：{data}")
        status, _, data = self.call("OPTIONS", "/intervene")
        self.assertEqual(status, 405, data)
        status, _, data = self.call("POST", sv.API_PATH_HEALTH, {})
        self.assertEqual(status, 405, data)
        status, _, data = self.call("GET", "/api/nope")
        self.assertEqual(status, 404, data)
        self.assertEqual(code_of(data), "STATIC_NOT_FOUND", "t8 口径：GET 未知路径走静态路由")
        status, _, data = self.call("POST", "/api/nope", {"action_id": PENDING})
        self.assertEqual(status, 404, data)
        self.assertEqual(code_of(data), "ROUTE_NOT_FOUND", "t8 口径：POST 未知路由 404")

    def test_offline_file_protocol_never_needs_the_server(self) -> None:
        """T1 护栏：file:// 打开时页面不请求鉴权（自检 4.3 用例覆盖渲染，这里锁代码）。"""
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("AUTH_LOCAL_PERMISSIONS", source)
        self.assertIn("forced_offline", source)
        self.assertIn("credentials: 'omit'", source)


def setUpModule() -> None:
    TMP.mkdir(parents=True, exist_ok=True)


def tearDownModule() -> None:
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
