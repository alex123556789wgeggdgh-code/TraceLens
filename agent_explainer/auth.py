"""多用户与权限（4.3）：local / team 双模式的用户、会话、角色与权限矩阵。

契约来源（不许再开会确认的那两份文档）
--------------------------------------
`docs/01-product-and-users.md` §6.4 与 `docs/03-architecture.md` 第 5 节给出同一张矩阵，
本模块就是那张矩阵的**唯一实现点**：

| 能力 | local（免登录） | team-admin | team-editor | team-viewer |
|---|---|---|---|---|
| 看清单 / 时间轴 / 切语言 | ✅ | ✅ | ✅ | ✅ |
| 粘贴 / 上传 / 演示数据 | ✅ | ✅ | ✅ | ❌ 403 |
| webhook 令牌管理 | ✅ | ✅ | ❌ | ❌ |
| 干预：同意 / 修改 / 拒绝 | ✅ | ✅ | ✅ | ❌（入口隐藏 + 403） |
| 看审计 / 导出 CSV | ✅ | ✅ | ✅ | ✅ |
| 模型配置 / 清空数据 | ✅ | ✅ | ❌ | ❌ |
| 用户增删 / 改角色 / 重置密码 | —（无此概念） | ✅ | ❌ | ❌ |

实现要点（第 5 节逐条落地）
------------------------------
1. **单点强制**：路由只声明所需权限（`server.py` 的 `ROUTE_PERMISSIONS` 表），校验只发生在
   :meth:`Authenticator.require` 一处。禁止在 handler 里散落 `if role == "admin"`
   （t13 测试用 AST 断言 `server.py` 里这种写法为 0 处）。
2. **未登录 401 `AUTH_REQUIRED`；越权 403 `ROLE_FORBIDDEN`**（K-11 的 12 项判定用例）。
3. **鉴权结果写审计**：`auth.login` / `auth.login_failed` / `auth.logout` / `auth.denied`。
   口令、口令哈希、会话 token 一律不进审计（只记 username 与结果码）。
4. **记录级二次收口**：`policy.min_role_to_decide`（例：某条要求 editor 以上）由
   :meth:`Authenticator.rank_at_least` 判定，服务端在 `/intervene` 里二次收口。
5. **local 模式短路**：`actor_id="local", role="local", workspace_id="default"`，
   **不出现登录页**（T1 护栏：禁止要求注册）。team 模式必须登录。

安全基线（第 14 节）
--------------------
* 口令：PBKDF2-HMAC-SHA256，默认 **210000** 轮、16 字节随机盐，串里带参数
  （`pbkdf2_sha256$轮数$盐$哈希`），校验用 `hmac.compare_digest`；磁盘与审计里都没有明文。
* 会话：HMAC-SHA256 签名的紧凑 token（`base64url(payload).base64url(签名)`），
  带 `exp` 过期与 `sid` 吊销；Cookie 名 `tl_session`（HttpOnly + SameSite=Lax），
  非浏览器客户端走请求头 `X-TraceLens-Token`。
* CSRF：双提交令牌（docs 第 14 节）——**用 Cookie 证明身份**的写操作必须带
  `X-CSRF-Token`，值取自登录响应；只带 token 头的客户端（桌面/CLI/测试）没有 Cookie 的
  跨站携带问题，因此豁免。这样 4.1 的干预闭环不受影响。
* 限速：同一用户名连续 5 次失败 → 锁定 15 分钟（`locked_until`），返回 429 `RATE_LIMITED`
  并带 `Retry-After`。
* 首个管理员：team 模式零用户时生成；口令优先从环境变量注入
  （`TRACELENS_ADMIN_PASSWORD`，兼容架构文档写的 `TRACELNS_ADMIN_INITIAL_PASSWORD`，
  供 Docker/compose 的 env_file 使用），没给就生成一次性口令，**只打印到服务端控制台**，
  绝不出现在任何 HTTP 响应里；首次登录强制改密（`must_change_password`）。

给 t11（审计页）/ t14（设置中心）/ t17（Docker）的接口缝
--------------------------------------------------------
* `GET /api/auth/status`（公开）→ 模式、是否需要登录、是否需要初始化；
* `GET /api/auth/permissions`（公开）→ 角色→权限矩阵，前端只读它做隐藏/禁用；
* `Authenticator.permissions_payload()` 是同一份数据的 Python 入口；
* 与 t9 审计的桥：`audit_event_for(name, session, ...)` 产出 audit.py 能收的载荷
  （t9 的 `EVENTS` 里没有 auth.* 事件名，所以按 `access_denied` / `note` 落盘，
  原始名字在 `fields.auth_event` 里；要把 auth.* 变成一等公民需要改 audit.py，越界，已报队长）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "APP_ID", "MODES", "ROLES", "TEAM_ROLES", "USER_STATUSES", "PERMISSIONS",
    "ROLE_PERMISSIONS", "ROLE_RANK", "ROLE_AUDIT_MAP", "SESSION_COOKIE",
    "CSRF_HEADER", "CSRF_FIELD", "TOKEN_HEADER", "PBKDF2_ITERATIONS", "SALT_BYTES",
    "SESSION_TTL_SECONDS", "MAX_FAILURES", "LOCKOUT_SECONDS", "MIN_PASSWORD_CHARS",
    "ENV_ADMIN_PASSWORDS", "STATE_FILE_NAME", "LOCAL_USER_ID", "WORKSPACE_ID",
    "AUTH_EVENTS",
    "AuthError", "AuthInputError", "AuthRequiredError", "AuthForbiddenError",
    "AuthRateLimitError", "UserStoreError",
    "PasswordHash", "TokenSigner", "Authenticator",
    "default_state_path", "permission_matrix", "can", "rank_at_least",
]

APP_ID = "tracelens"
MODES: tuple[str, ...] = ("local", "team")
TEAM_ROLES: tuple[str, ...] = ("admin", "editor", "viewer")
ROLES: tuple[str, ...] = TEAM_ROLES + ("local",)
USER_STATUSES: tuple[str, ...] = ("active", "disabled")

#: 矩阵里的七项能力（服务端与前端共用同一组 ID，前端只读不判）。
PERMISSIONS: tuple[str, ...] = (
    "records.read",         # 看清单 / 时间轴 / 切语言
    "data.write",           # 粘贴 / 上传 / 演示数据
    "ingest.token.manage",  # webhook 写入令牌管理
    "intervene.decide",     # 干预：同意 / 修改 / 拒绝
    "audit.read",           # 看审计
    "audit.export",         # 导出 CSV / 可打印
    "settings.write",       # 模型配置 / 清空数据
    "users.manage",         # 用户增删 / 改角色 / 重置密码
)

#: 角色 → 权限。照 docs §6.4 逐行翻成布尔，不做任何"顺手放宽"。
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "local": ("records.read", "data.write", "ingest.token.manage", "intervene.decide",
              "audit.read", "audit.export", "settings.write"),
    "admin": ("records.read", "data.write", "ingest.token.manage", "intervene.decide",
              "audit.read", "audit.export", "settings.write", "users.manage"),
    "editor": ("records.read", "data.write", "intervene.decide",
               "audit.read", "audit.export"),
    "viewer": ("records.read", "audit.read", "audit.export"),
}

#: 记录级 `policy.min_role_to_decide` 用的序（local 等价 admin 的干预能力）。
ROLE_RANK: dict[str, int] = {"viewer": 1, "editor": 2, "admin": 3, "local": 3}

#: t9 审计的 actor_role 枚举是 local/owner/member/viewer/system，
#: 这里把内部角色映射过去（不改 audit.py，映射表写死并可测）。
ROLE_AUDIT_MAP: dict[str, str] = {"admin": "owner", "editor": "member",
                                  "viewer": "viewer", "local": "local"}

AUTH_EVENTS: tuple[str, ...] = ("auth.login", "auth.login_failed", "auth.logout",
                                "auth.denied", "auth.password", "auth.users")

SESSION_COOKIE = "tl_session"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"
TOKEN_HEADER = "X-TraceLens-Token"
PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16
SESSION_TTL_SECONDS = 8 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
MIN_PASSWORD_CHARS = 10
MAX_PASSWORD_CHARS = 256
LOCAL_USER_ID = "local"
WORKSPACE_ID = "default"
STATE_FILE_NAME = "auth-state.json"
ENV_ADMIN_PASSWORDS: tuple[str, ...] = ("TRACELENS_ADMIN_PASSWORD",
                                        "TRACELNS_ADMIN_INITIAL_PASSWORD")
TOKEN_SEPARATOR = "."
PASSWORD_PREFIX = "pbkdf2_sha256"
USERNAME_MIN, USERNAME_MAX = 2, 32
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
#: 明显口令：不是黑名单工程，只是拒绝"1234567890"这类零成本口令。
WEAK_PASSWORDS: frozenset[str] = frozenset({
    "password", "passw0rd", "tracelens", "admin123", "administrator", "letmein",
    "welcome", "iloveyou", "abc12345", "1234567890", "qwertyuiop", "11111111",
    "changeme", "tracelens123",
})

# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #


class AuthError(Exception):
    """鉴权域统一异常：``code`` 面向程序与 i18n，``status`` 面向 HTTP。"""

    def __init__(self, code: str, message: str, *, status: int = 400,
                 field: str | None = None, hint: str | None = None,
                 detail: Mapping[str, Any] | None = None,
                 retry_after: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = int(status)
        self.field = field
        self.hint = hint
        self.retry_after = retry_after
        self.detail: dict[str, Any] = dict(detail or {})

    def as_dict(self, request_id: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = dict(self.detail)
        if request_id:
            out["request_id"] = request_id
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.message}"]
        if self.field:
            parts.append(f"字段 {self.field}")
        if self.retry_after:
            parts.append(f"请 {self.retry_after} 秒后再试")
        if self.hint:
            parts.append(f"建议：{self.hint}")
        return "；".join(parts)


class AuthInputError(AuthError):
    """入参不合法（用户名格式、角色名、口令强度…）。"""


class AuthRequiredError(AuthError):
    """未登录 / 会话过期 → 401 AUTH_REQUIRED。"""

    def __init__(self, message: str = "Not signed in", *,
                 code: str = "AUTH_REQUIRED", **kw: Any) -> None:
        super().__init__(code, message, status=401, **kw)


class AuthForbiddenError(AuthError):
    """越权 → 403 ROLE_FORBIDDEN。"""

    def __init__(self, message: str = "This action needs a higher role", *,
                 code: str = "ROLE_FORBIDDEN", **kw: Any) -> None:
        super().__init__(code, message, status=403, **kw)


class AuthRateLimitError(AuthError):
    """登录限速 / 账号锁定 → 429 RATE_LIMITED（带 Retry-After）。"""

    def __init__(self, message: str, *, retry_after: int,
                 code: str = "RATE_LIMITED", **kw: Any) -> None:
        super().__init__(code, message, status=429, retry_after=retry_after, **kw)


class UserStoreError(AuthError):
    """用户表状态问题（重复用户名、找不到用户、最后一个管理员…）。"""

    def __init__(self, code: str, message: str, *, status: int = 409, **kw: Any) -> None:
        super().__init__(code, message, status=status, **kw)


# --------------------------------------------------------------------------- #
# 口令哈希与 token 签名
# --------------------------------------------------------------------------- #


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class PasswordHash:
    """PBKDF2-HMAC-SHA256：`pbkdf2_sha256$轮数$盐$哈希`，参数随串存盘。"""

    def __init__(self, iterations: int = PBKDF2_ITERATIONS) -> None:
        self.iterations = max(1_000, int(iterations))

    def hash(self, password: str) -> str:
        if not isinstance(password, str) or not password:
            raise AuthInputError("AUTH_PASSWORD_REQUIRED", "口令不能为空",
                                 field="password")
        salt = secrets.token_bytes(SALT_BYTES)
        digest = self._derive(password, salt, self.iterations)
        return f"{PASSWORD_PREFIX}${self.iterations}${_b64e(salt)}${_b64e(digest)}"

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                   iterations, dklen=32)

    def verify(self, password: str, encoded: str) -> bool:
        """常数时间比较；参数升级（轮数变化）时返回 True 并标 needs_upgrade。"""
        parsed = self.parse(encoded)
        if parsed is None or not isinstance(password, str):
            return False
        _, iterations, salt, digest = parsed
        probe = self._derive(password, salt, iterations)
        return hmac.compare_digest(probe, digest)

    @staticmethod
    def parse(encoded: Any) -> "tuple[str, int, bytes, bytes] | None":
        if not isinstance(encoded, str):
            return None
        parts = encoded.split("$")
        if len(parts) != 4 or parts[0] != PASSWORD_PREFIX:
            return None
        try:
            iterations = int(parts[1])
            salt = _b64d(parts[2])
            digest = _b64d(parts[3])
        except Exception:                                     # noqa: BLE001 - 存盘坏了当无效哈希
            return None
        if iterations < 1_000 or len(salt) < 8 or len(digest) < 16:
            return None
        return parts[0], iterations, salt, digest

    def needs_upgrade(self, encoded: str) -> bool:
        parsed = self.parse(encoded)
        return bool(parsed and parsed[1] != self.iterations)


class TokenSigner:
    """HMAC-SHA256 签名的紧凑会话 token：``base64url(payload).base64url(签名)``。"""

    def __init__(self, secret: bytes | str) -> None:
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if len(secret) < 16:
            raise AuthInputError("AUTH_SECRET_TOO_SHORT",
                                 "会话签名密钥太短", field="secret",
                                 detail={"bytes": len(secret), "min": 16},
                                 hint="用 secrets.token_bytes(32) 生成并存盘")
        self._secret = bytes(secret)

    @staticmethod
    def new_secret() -> str:
        return _b64e(secrets.token_bytes(32))

    def issue(self, payload: Mapping[str, Any]) -> str:
        body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8"))
        return f"{body}{TOKEN_SEPARATOR}{self.sign(body)}"

    def sign(self, body: str) -> str:
        return _b64e(hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest())

    def verify(self, token: Any, *, now: float | None = None) -> dict[str, Any]:
        """验签 + 过期检查；失败抛 AuthRequiredError（401）。"""
        if not isinstance(token, str) or TOKEN_SEPARATOR not in token:
            raise AuthRequiredError("No session token", code="AUTH_TOKEN_INVALID",
                                    hint="重新登录")
        body, _, signature = token.partition(TOKEN_SEPARATOR)
        expected = self.sign(body)
        if not hmac.compare_digest(expected, signature):
            raise AuthRequiredError("Session token signature does not match",
                                    code="AUTH_TOKEN_INVALID",
                                    hint="token 被改过或签名密钥已轮换；重新登录")
        try:
            payload = json.loads(_b64d(body).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuthRequiredError("Session token payload is unreadable",
                                    code="AUTH_TOKEN_INVALID",
                                    detail={"hint": type(exc).__name__})
        if not isinstance(payload, dict):
            raise AuthRequiredError("Session token payload is not an object",
                                    code="AUTH_TOKEN_INVALID")
        stamp = time.time() if now is None else float(now)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and stamp >= float(exp):
            raise AuthRequiredError("Session expired", code="AUTH_TOKEN_EXPIRED",
                                    detail={"expired_at": exp, "now": stamp},
                                    hint="重新登录")
        return payload

    def sign_for(self, data: str) -> str:
        return self.sign(data)


# --------------------------------------------------------------------------- #
# 口令强度
# --------------------------------------------------------------------------- #


def password_strength(password: Any, *, username: str | None = None) -> dict[str, Any]:
    """返回 {ok, code, reason}；只拦零成本口令，不装密码学专家。"""
    if not isinstance(password, str) or not password.strip():
        return {"ok": False, "code": "AUTH_PASSWORD_REQUIRED", "reason": "口令为空"}
    text = password.strip()
    if len(text) < MIN_PASSWORD_CHARS:
        return {"ok": False, "code": "AUTH_WEAK_PASSWORD",
                "reason": f"至少 {MIN_PASSWORD_CHARS} 个字符"}
    if len(text) > MAX_PASSWORD_CHARS:
        return {"ok": False, "code": "AUTH_PASSWORD_TOO_LONG",
                "reason": f"最多 {MAX_PASSWORD_CHARS} 个字符"}
    if text.lower() in WEAK_PASSWORDS:
        return {"ok": False, "code": "AUTH_WEAK_PASSWORD", "reason": "太常见"}
    if len(set(text)) <= 2:
        return {"ok": False, "code": "AUTH_WEAK_PASSWORD", "reason": "字符种类太少"}
    name = str(username or "").strip().lower()
    if name and text.lower() == name:
        return {"ok": False, "code": "AUTH_WEAK_PASSWORD", "reason": "不能和用户名一样"}
    if name and len(text) < 2 * MIN_PASSWORD_CHARS and name in text.lower():
        return {"ok": False, "code": "AUTH_WEAK_PASSWORD", "reason": "包含用户名"}
    return {"ok": True, "code": "", "reason": ""}


def new_generated_password(length: int = 16) -> str:
    """一次性口令：去掉了容易看错的字符（1/l/I/0/O），方便在控制台里抄。"""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    while True:
        text = "".join(secrets.choice(alphabet) for _ in range(length))
        if len(set(text)) >= 8 and not text.isdigit() and not text.isalpha():
            return text


# --------------------------------------------------------------------------- #
# 用户存储（用户表是可改的：这不是审计日志，两回事）
# --------------------------------------------------------------------------- #


class UserStore:
    """`var/auth/auth-state.json`：用户、会话吊销、签名密钥。原子写盘。

    存盘里**只有口令哈希串**，没有明文；`secrets_leaked_to_disk()` 给测试用。
    """

    def __init__(self, path: pathlib.Path | str, *, create_dirs: bool = True) -> None:
        self._path = pathlib.Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"schema_version": "1.0", "app": APP_ID,
                                      "users": {}, "sessions": {}, "secret": "",
                                      "meta": {}}
        if create_dirs and self._path.parent and not self._path.parent.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    @property
    def path(self) -> pathlib.Path:
        return self._path

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return self._data
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AuthError("AUTH_STATE_CORRUPT", "鉴权状态文件读不出来", status=500,
                                detail={"path": str(self._path), "hint": type(exc).__name__},
                                hint="修文件前先备份；不要删（历史用户要能查）")
            if isinstance(raw, dict) and isinstance(raw.get("users"), dict):
                self._data = raw
                self._data.setdefault("sessions", {})
                self._data.setdefault("meta", {})
                self._data.setdefault("secret", "")
            return self._data

    def save(self) -> None:
        with self._lock:
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self._path)

    def data(self) -> dict[str, Any]:
        with self._lock:
            return self._data

    def raw_text(self) -> str:
        return self._path.read_text(encoding="utf-8") if self._path.exists() else ""

    def __repr__(self) -> str:  # pragma: no cover - 调试
        with self._lock:
            return f"<UserStore path={str(self._path)!r} users={len(self._data['users'])}>"


# --------------------------------------------------------------------------- #
# 鉴权本体
# --------------------------------------------------------------------------- #


class Authenticator:
    """模式开关 + 用户 + 会话 + 权限矩阵（服务端唯一的鉴权入口）。"""

    def __init__(self, mode: str = "local", *, state_path: pathlib.Path | str | None = None,
                 session_ttl: int = SESSION_TTL_SECONDS,
                 max_failures: int = MAX_FAILURES,
                 lockout_seconds: int = LOCKOUT_SECONDS,
                 iterations: int = PBKDF2_ITERATIONS,
                 env: Mapping[str, str] | None = None,
                 now: Callable[[], float] | None = None,
                 bootstrap: bool = True) -> None:
        if mode not in MODES:
            raise AuthInputError("AUTH_MODE_INVALID", f"mode 只能是 {'/'.join(MODES)}",
                                 field="mode", detail={"received": mode})
        self.mode = mode
        self._now = now or time.time
        self._env = env if env is not None else os.environ
        self._session_ttl = max(60, int(session_ttl))
        self._max_failures = max(1, int(max_failures))
        self._lockout = max(1, int(lockout_seconds))
        self.kdf = PasswordHash(iterations)
        self.store = UserStore(state_path or default_state_path())
        self._ensure_secret()
        self._bootstrap_done = False
        self.bootstrap_report: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        if mode == "team" and bootstrap:
            self.bootstrap_report = self.bootstrap()
            self._bootstrap_done = True

    # -- 基本属性 ---------------------------------------------------------- #

    @property
    def required(self) -> bool:
        """team 模式必须登录；local 免登录（T1 护栏）。"""
        return self.mode == "team"

    @property
    def session_ttl(self) -> int:
        """会话有效期（秒）；服务端拼 Cookie 的 Max-Age 用它。"""
        return self._session_ttl

    @property
    def events(self) -> list[dict[str, Any]]:
        """待写审计的鉴权事件（调用方负责落盘；口令永不进事件）。"""
        return self._events

    def take_events(self) -> list[dict[str, Any]]:
        out, self._events = list(self._events), []
        return out

    def _emit(self, name: str, *, actor_id: str | None, username: str | None = None,
              role: str | None = None, ok: bool = True, reason: str | None = None,
              detail: Mapping[str, Any] | None = None) -> None:
        self._events.append({
            "event": name, "actor_id": actor_id, "username": username, "role": role,
            "ok": ok, "reason": reason, "detail": dict(detail or {}), "at": self._iso(),
        })

    def _iso(self) -> str:
        stamp = time.gmtime(int(self._now()))
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", stamp)

    def _secret_bytes(self) -> bytes:
        data = self.store.data()
        secret = data.get("secret")
        if isinstance(secret, str) and secret:
            return _b64d(secret)
        secret = TokenSigner.new_secret()
        data["secret"] = secret
        self.store.save()
        return _b64d(secret)

    def _ensure_secret(self) -> None:
        self._signer = TokenSigner(self._secret_bytes())

    # -- 模式与矩阵 -------------------------------------------------------- #

    def permissions_for(self, role: str) -> tuple[str, ...]:
        return ROLE_PERMISSIONS.get(role, ())

    def can(self, role: str, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(role, ())

    def rank_at_least(self, role: str, min_role: str | None) -> bool:
        """记录级 `policy.min_role_to_decide`：viewer 想动 editor 级记录 → False。"""
        if not min_role:
            return True
        return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(str(min_role), 0)

    def audit_actor_role(self, role: str) -> str:
        return ROLE_AUDIT_MAP.get(role, "viewer")

    def local_session(self) -> dict[str, Any]:
        """local 模式短路：不查库、不发 token、不出现登录页。"""
        return {
            "user_id": LOCAL_USER_ID, "username": LOCAL_USER_ID, "display_name": LOCAL_USER_ID,
            "role": "local", "status": "active", "workspace_id": WORKSPACE_ID,
            "permissions": list(self.permissions_for("local")), "local": True,
            "sid": "local", "issued_at": int(self._now()),
            "expires_at": int(self._now()) + self._session_ttl,
            "must_change_password": False,
        }

    def status(self) -> dict[str, Any]:
        users = self.list_users()
        return {
            "mode": self.mode,
            "required": self.required,
            "users_total": len(users),
            "bootstrap_needed": bool(self.required and not users),
            "cookie": SESSION_COOKIE,
            "csrf_header": CSRF_HEADER,
            "token_header": TOKEN_HEADER,
            "session_ttl_seconds": self._session_ttl,
            "max_failures": self._max_failures,
            "lockout_seconds": self._lockout,
            "roles": list(TEAM_ROLES),
            "password_min_chars": MIN_PASSWORD_CHARS,
            "pbkdf2_iterations": self.kdf.iterations,
        }

    def permissions_payload(self) -> dict[str, Any]:
        """前端只读这份做隐藏/禁用（服务端仍然是强制点）。"""
        return {
            "permissions": list(PERMISSIONS),
            "roles": list(TEAM_ROLES),
            "matrix": {role: list(ROLE_PERMISSIONS.get(role, ())) for role in ROLES},
            "labels": {role: f"role.{role}" for role in ROLES},
            "permission_labels": {name: f"permission.{name}" for name in PERMISSIONS},
            "mode": self.mode,
            "required": self.required,
        }

    # -- 首个管理员 -------------------------------------------------------- #

    def bootstrap(self) -> dict[str, Any] | None:
        """team 模式零用户时建 admin；口令优先来自环境变量，否则一次性生成。

        生成出来的口令**只返回给调用方打印到控制台**，绝不进 HTTP 响应、不进审计。
        """
        if self.mode != "team":
            return None
        if self.list_users():
            return None
        injected = None
        source = "generated"
        for name in ENV_ADMIN_PASSWORDS:
            value = self._env.get(name) if self._env is not None else None
            if isinstance(value, str) and value.strip():
                injected, source = value.strip(), name
                break
        if injected is None:
            injected = new_generated_password()
        self.create_user("admin", "admin", password=injected,
                         display_name="Administrator", must_change=True,
                         source="bootstrap")
        self.store.data()["meta"]["bootstrap_at"] = self._iso()
        self.store.save()
        self._emit("auth.login", actor_id=self._user_id("admin"), username="admin",
                   role="admin", reason="bootstrap_admin", detail={"source": source})
        return {"username": "admin", "password": injected, "source": source,
                "must_change_password": True,
                "show_once": "控制台一次性显示；之后只能由管理员重置",
                "env_hint": "Docker/compose 可用 TRACELENS_ADMIN_PASSWORD 注入"}

    # -- 用户 CRUD --------------------------------------------------------- #

    def _user_id(self, username: str) -> str:
        return f"usr-{hashlib.sha256(str(username).lower().encode('utf-8')).hexdigest()[:12]}"

    def list_users(self, *, q: str | None = None,
                   status: str | None = None) -> list[dict[str, Any]]:
        with self.store._lock:
            users = list(self.store.data()["users"].values())
        out = [self.public_user(user) for user in users]
        needle = q.strip().lower() if isinstance(q, str) and q.strip() else None
        rows: list[dict[str, Any]] = []
        for user in out:
            if status and user.get("status") != status:
                continue
            if needle and needle not in f"{user.get('username')} {user.get('display_name')}".lower():
                continue
            rows.append(user)
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.store._lock:
            user = self.store.data()["users"].get(str(user_id))
        return dict(user) if isinstance(user, dict) else None

    def public_user(self, user: Mapping[str, Any]) -> dict[str, Any]:
        """对外形状：绝不带口令哈希/盐/失败计数细节。"""
        return {
            "id": user.get("id"), "username": user.get("username"),
            "display_name": user.get("display_name"), "role": user.get("role"),
            "status": user.get("status"), "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"), "last_login_at": user.get("last_login_at"),
            "must_change_password": bool(user.get("must_change_password")),
            "locked_until": user.get("locked_until"),
            "permissions": list(ROLE_PERMISSIONS.get(str(user.get("role")), ())),
        }

    def create_user(self, username: str, role: str, password: str | None = None, *,
                    display_name: str | None = None, must_change: bool = False,
                    source: str = "admin",
                    actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._clean_username(username)
        if role not in TEAM_ROLES:
            raise AuthInputError("ROLE_INVALID", f"角色只能是 {'/'.join(TEAM_ROLES)}",
                                 field="role", detail={"received": role})
        user_id = self._user_id(name)
        with self.store._lock:
            users = self.store.data()["users"]
            if any(str(u.get("username", "")).lower() == name for u in users.values()):
                raise UserStoreError("USER_EXISTS", "用户名已存在", status=409,
                                     field="username",
                                     hint="换一个，或改已有用户的角色")
            given = password is not None and str(password) != ""
            if given:
                strength = password_strength(password, username=name)
                if not strength["ok"]:
                    raise AuthInputError(strength["code"], f"口令太弱：{strength['reason']}",
                                         field="password",
                                         hint=f"至少 {MIN_PASSWORD_CHARS} 个字符")
                secret = str(password)
                issued = None
                change = bool(must_change)
            else:
                secret = new_generated_password()
                issued = secret
                change = True
            record = {
                "id": user_id, "username": name,
                "display_name": (display_name or name).strip()[:64],
                "role": role, "status": "active",
                "password_hash": self.kdf.hash(secret),
                "created_at": self._iso(), "updated_at": self._iso(),
                "last_login_at": None, "failed_attempts": 0, "locked_until": None,
                "must_change_password": change, "source": source,
            }
            users[user_id] = record
            self.store.save()
        self._emit("auth.users", actor_id=(actor or {}).get("user_id"),
                   username=name, role=role, reason=f"create_user:{source}")
        out = self.public_user(record)
        if issued is not None:
            out["initial_password"] = issued
            out["initial_password_note"] = "只在这一次响应里出现；请立即转交本人并要求改密"
        return out

    def set_role(self, user_id: str, role: str, *,
                 actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        user = self._require_user(user_id)
        if role not in TEAM_ROLES:
            raise AuthInputError("ROLE_INVALID", f"角色只能是 {'/'.join(TEAM_ROLES)}",
                                 field="role", detail={"received": role})
        if role == user["role"]:
            return self.public_user(user)
        if user["role"] == "admin" and role != "admin":
            self._guard_last_admin(user, reason="demote")
        self._patch(user_id, {"role": role})
        self._emit("auth.users", actor_id=(actor or {}).get("user_id"),
                   username=user["username"], role=role, reason="set_role")
        return self.public_user(self.get_user(user_id) or user)

    def set_status(self, user_id: str, status: str, *,
                   actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        user = self._require_user(user_id)
        if status not in USER_STATUSES:
            raise AuthInputError("USER_STATUS_INVALID",
                                 f"status 只能是 {'/'.join(USER_STATUSES)}",
                                 field="status", detail={"received": status})
        actor_id = (actor or {}).get("user_id")
        if status == "disabled" and actor_id == user_id:
            raise UserStoreError("USER_SELF_DISABLE", "不能禁用当前登录的自己",
                                 status=409,
                                 hint="用另一个管理员账号禁用，避免把自己关在门外")
        if status == "disabled" and user["role"] == "admin":
            self._guard_last_admin(user, reason="disable")
        self._patch(user_id, {"status": status})
        if status == "disabled":
            self._revoke_user_sessions(user_id)
        self._emit("auth.users", actor_id=actor_id, username=user["username"],
                   role=user["role"], reason=f"set_status:{status}")
        return self.public_user(self.get_user(user_id) or user)

    def reset_password(self, user_id: str, password: str | None = None, *,
                       actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        user = self._require_user(user_id)
        if password:
            strength = password_strength(password, username=user["username"])
            if not strength["ok"]:
                raise AuthInputError(strength["code"],
                                     "口令太弱：" + str(strength["reason"]),
                                     field="password",
                                     hint=f"至少 {MIN_PASSWORD_CHARS} 个字符")
            secret, issued, change = str(password), None, True
        else:
            secret = new_generated_password()
            issued, change = secret, True
        self._patch(user_id, {"password_hash": self.kdf.hash(secret),
                              "must_change_password": change,
                              "failed_attempts": 0, "locked_until": None})
        self._revoke_user_sessions(user_id)
        self._emit("auth.users", actor_id=(actor or {}).get("user_id"),
                   username=user["username"], role=user["role"], reason="reset_password")
        out = self.public_user(self.get_user(user_id) or user)
        if issued is not None:
            out["initial_password"] = issued
            out["initial_password_note"] = "只在这一次响应里出现"
        return out

    def change_password(self, user_id: str, old_password: Any, new_password: Any) -> dict[str, Any]:
        """本人改密（管理员重置走 :meth:`reset_password`，不校验旧口令）。"""
        user = self._require_user(user_id)
        if not self.kdf.verify(str(old_password or ""), str(user.get("password_hash") or "")):
            self._emit("auth.password", actor_id=user_id, username=user["username"],
                       ok=False, reason="old_password_mismatch")
            raise AuthError("AUTH_BAD_CREDENTIALS", "当前口令不对", status=401,
                            field="old_password")
        strength = password_strength(new_password, username=user["username"])
        if not strength["ok"]:
            raise AuthInputError(strength["code"], f"口令太弱：{strength['reason']}",
                                 field="new_password",
                                 hint=f"至少 {MIN_PASSWORD_CHARS} 个字符")
        if self.kdf.verify(str(new_password), str(user["password_hash"])):
            raise AuthInputError("AUTH_PASSWORD_SAME", "新口令不能和当前口令相同",
                                 field="new_password")
        self._patch(user_id, {"password_hash": self.kdf.hash(str(new_password)),
                              "must_change_password": False})
        self._revoke_user_sessions(user_id, keep=None)
        self._emit("auth.password", actor_id=user_id, username=user["username"],
                   reason="password_changed")
        return {"ok": True, "username": user["username"], "changed_at": self._iso()}

    # -- 登录 / 会话 ------------------------------------------------------- #

    def authenticate(self, username: Any, password: Any, *,
                     client_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """校验口令 → 发会话。失败计数与锁定在这里处理（审计记结果码，不记口令）。"""
        name = str(username or "").strip().lower()
        user = self._find_by_username(name)
        if user is None:
            # 不泄露"用户名是否存在"：也做一次哈希，代价对齐，错误码统一
            self.kdf.hash(str(password or "x"))
            self._emit("auth.login_failed", actor_id=None, username=name or None,
                       ok=False, reason="unknown_user",
                       detail={"client": _client_hint(client_info)})
            raise AuthError("AUTH_BAD_CREDENTIALS", "用户名或口令不对", status=401,
                            hint="用户名不区分大小写，口令区分大小写")
        locked = self._locked_until(user)
        if locked:
            self._emit("auth.login_failed", actor_id=user["id"], username=name,
                       ok=False, reason="locked")
            raise AuthRateLimitError("账号已锁定：连续登录失败次数过多",
                                     retry_after=locked - int(self._now()),
                                     detail={"username": name, "locked_until": locked})
        if str(user.get("status")) != "active":
            self._emit("auth.login_failed", actor_id=user["id"], username=name,
                       ok=False, reason="disabled")
            raise AuthForbiddenError("这个账号已被禁用", code="AUTH_USER_DISABLED",
                                     detail={"username": name},
                                     hint="找管理员恢复")
        if not self.kdf.verify(str(password or ""), str(user.get("password_hash") or "")):
            failures = int(user.get("failed_attempts") or 0) + 1
            patch: dict[str, Any] = {"failed_attempts": failures}
            reason = "bad_password"
            if failures >= self._max_failures:
                patch["locked_until"] = int(self._now()) + self._lockout
                reason = "too_many_failures"
            self._patch(user["id"], patch)
            self._emit("auth.login_failed", actor_id=user["id"], username=name,
                       role=user["role"], ok=False, reason=reason,
                       detail={"failures": failures,
                               "client": _client_hint(client_info)})
            if reason == "too_many_failures":
                raise AuthRateLimitError("连续失败次数过多，账号已临时锁定",
                                         retry_after=self._lockout,
                                         detail={"username": name})
            raise AuthError("AUTH_BAD_CREDENTIALS", "用户名或口令不对", status=401,
                            detail={"remaining_attempts": max(0, self._max_failures - failures)})
        self._patch(user["id"], {"failed_attempts": 0, "locked_until": None,
                                 "last_login_at": self._iso()})
        session = self.issue_session(user)
        self._emit("auth.login", actor_id=user["id"], username=name, role=user["role"],
                   detail={"client": _client_hint(client_info)})
        return session

    def issue_session(self, user: Mapping[str, Any]) -> dict[str, Any]:
        sid = _b64e(secrets.token_bytes(16))
        csrf = _b64e(secrets.token_bytes(16))
        issued = int(self._now())
        expires = issued + self._session_ttl
        payload = {"sub": user["id"], "role": user["role"], "sid": sid,
                   "iat": issued, "exp": expires, "ws": WORKSPACE_ID,
                   "v": "1"}
        token = self._signer.issue(payload)
        with self.store._lock:
            self.store.data()["sessions"][sid] = {"user_id": user["id"], "issued": issued,
                                                  "expires": expires, "csrf": csrf}
            self._prune_sessions()
            self.store.save()
        return {
            "user_id": user["id"], "username": user["username"],
            "display_name": user.get("display_name"), "role": user["role"],
            "status": "active", "workspace_id": WORKSPACE_ID,
            "permissions": list(self.permissions_for(str(user["role"]))),
            "token": token, "csrf_token": csrf, "sid": sid,
            "issued_at": issued, "expires_at": expires,
            "expires_in": expires - issued,
            "must_change_password": bool(user.get("must_change_password")),
            "cookie": {"name": SESSION_COOKIE, "value": token, "http_only": True,
                       "same_site": "Lax", "path": "/", "max_age": self._session_ttl},
        }

    def session_from_token(self, token: Any) -> dict[str, Any]:
        payload = self._signer.verify(token, now=self._now())
        with self.store._lock:
            record = self.store.data()["sessions"].get(str(payload.get("sid")))
        if not isinstance(record, dict):
            raise AuthRequiredError("Session was signed out", code="AUTH_TOKEN_REVOKED",
                                    hint="重新登录")
        if record.get("revoked"):
            raise AuthRequiredError("Session was signed out", code="AUTH_TOKEN_REVOKED",
                                    hint="重新登录")
        user = self.get_user(str(payload.get("sub")))
        if user is None:
            raise AuthRequiredError("User of this session no longer exists",
                                    code="AUTH_USER_NOT_FOUND")
        if str(user.get("status")) != "active":
            raise AuthForbiddenError("这个账号已被禁用", code="AUTH_USER_DISABLED")
        if str(user.get("role")) != str(payload.get("role")):
            # 角色改了就用新角色（旧 token 里的 role 不作为授权依据）
            pass
        return {
            "user_id": user["id"], "username": user["username"],
            "display_name": user.get("display_name"), "role": str(user["role"]),
            "status": "active", "workspace_id": WORKSPACE_ID,
            "permissions": list(self.permissions_for(str(user["role"]))),
            "sid": payload.get("sid"), "issued_at": payload.get("iat"),
            "expires_at": payload.get("exp"),
            "must_change_password": bool(user.get("must_change_password")),
            "local": False,
        }

    def logout(self, token: Any) -> dict[str, Any]:
        try:
            payload = self._signer.verify(token)
        except AuthRequiredError as exc:
            self._emit("auth.logout", actor_id=None, ok=False, reason=exc.code)
            raise
        with self.store._lock:
            record = self.store.data()["sessions"].get(str(payload.get("sid")))
            if isinstance(record, dict):
                record["revoked"] = True
                record["revoked_at"] = self._iso()
            self._prune_sessions()
            self.store.save()
        self._emit("auth.logout", actor_id=str(payload.get("sub")),
                   role=str(payload.get("role")), reason="logout")
        return {"ok": True, "signed_out_at": self._iso()}

    def csrf_token_for(self, session: Mapping[str, Any]) -> str:
        """会话绑定的 CSRF 值：登录响应发给客户端，写请求用请求头带回来。

        双提交口径（docs 第 14 节）：这个值只出现在**登录响应体**里，跨站页面读不到，
        所以"能带上它"就证明请求来自本应用的脚本，而不是别站的表单。
        """
        sid = str(session.get("sid") or "")
        if not sid:
            return ""
        with self.store._lock:
            record = self.store.data()["sessions"].get(sid)
        if not isinstance(record, dict):
            return ""
        value = record.get("csrf")
        return str(value) if isinstance(value, str) else ""

    def check_csrf(self, session: Mapping[str, Any], header_value: Any) -> None:
        expected = self.csrf_token_for(session)
        if not expected:
            raise AuthForbiddenError("会话已失效，无法校验 CSRF", code="CSRF_FAILED",
                                     field=CSRF_HEADER, hint="重新登录")
        if not isinstance(header_value, str) or not header_value:
            raise AuthForbiddenError("缺少 CSRF 令牌", code="CSRF_FAILED", field=CSRF_HEADER,
                                     hint="用登录响应里的 csrf_token 填 " + CSRF_HEADER)
        if not hmac.compare_digest(expected, header_value):
            raise AuthForbiddenError("CSRF 令牌与会话不匹配", code="CSRF_FAILED",
                                     field=CSRF_HEADER,
                                     detail={"hint": "会话换过之后要重新登录取一次"})

    # -- 权限强制（单点） -------------------------------------------------- #

    def require(self, session: Mapping[str, Any] | None, permission: str) -> dict[str, Any]:
        """路由的唯一强制点：没会话 401，权限不够 403。"""
        if permission not in PERMISSIONS:
            raise AuthInputError("PERMISSION_UNKNOWN", "路由声明了不存在的权限",
                                 field="permission", detail={"received": permission},
                                 hint="矩阵在 auth.PERMISSIONS 里加一行，别在 handler 里判角色")
        if not session:
            raise AuthRequiredError("Not signed in",
                                    hint="登录后带 Cookie 或 X-TraceLens-Token")
        if not self.can(str(session.get("role")), permission):
            self._emit("auth.denied", actor_id=str(session.get("user_id") or ""),
                       username=str(session.get("username") or ""),
                       role=str(session.get("role") or ""), ok=False,
                       reason="ROLE_FORBIDDEN", detail={"permission": permission})
            raise AuthForbiddenError(f"这个操作需要更高角色（{permission}）",
                                     detail={"permission": permission,
                                             "role": session.get("role")},
                                     hint="界面已按矩阵隐藏该入口；需要请管理员操作")
        return dict(session)

    def authorize(self, session: Mapping[str, Any] | None, permission: str) -> bool:
        if not session:
            return False
        return self.can(str(session.get("role")), permission)

    def me_payload(self, session: Mapping[str, Any]) -> dict[str, Any]:
        """docs A3 的形状：actor_id / role / workspace_id / mode / language / ui。"""
        return {
            "actor_id": session.get("user_id"), "role": session.get("role"),
            "workspace_id": session.get("workspace_id") or WORKSPACE_ID,
            "mode": self.mode, "language": "zh",
            "username": session.get("username"),
            "display_name": session.get("display_name"),
            "permissions": list(session.get("permissions") or self.permissions_for(
                str(session.get("role")))),
            "local": bool(session.get("local")),
            "must_change_password": bool(session.get("must_change_password")),
            "expires_at": session.get("expires_at"),
            "audit_actor_role": self.audit_actor_role(str(session.get("role"))),
            "ui": {"show_login": self.required and not session.get("local"),
                   "show_users": self.can(str(session.get("role")), "users.manage"),
                   "show_intervene": self.can(str(session.get("role")), "intervene.decide"),
                   "show_settings": self.can(str(session.get("role")), "settings.write"),
                   "show_data_write": self.can(str(session.get("role")), "data.write"),
                   "show_ingest_token": self.can(str(session.get("role")),
                                                 "ingest.token.manage")},
        }

    # -- 内部 -------------------------------------------------------------- #

    def _clean_username(self, username: Any) -> str:
        name = str(username or "").strip().lower()
        if not name:
            raise AuthInputError("USERNAME_REQUIRED", "用户名不能为空", field="username")
        if not (USERNAME_MIN <= len(name) <= USERNAME_MAX) or not USERNAME_RE.match(name):
            raise AuthInputError("USERNAME_INVALID",
                                 f"用户名只能用小写字母、数字与 . _ -，长度 {USERNAME_MIN}-{USERNAME_MAX}",
                                 field="username", detail={"received": username})
        return name

    def _find_by_username(self, username: str) -> dict[str, Any] | None:
        needle = str(username or "").strip().lower()
        if not needle:
            return None
        with self.store._lock:
            for user in self.store.data()["users"].values():
                if str(user.get("username", "")).lower() == needle:
                    return dict(user)
        return None

    def _require_user(self, user_id: str) -> dict[str, Any]:
        user = self.get_user(user_id)
        if user is None:
            raise UserStoreError("USER_NOT_FOUND", "找不到这个用户", status=404,
                                 field="user_id", detail={"user_id": user_id})
        return user

    def _guard_last_admin(self, user: Mapping[str, Any], *, reason: str) -> None:
        active_admins = [u for u in self.list_users()
                         if u.get("role") == "admin" and u.get("status") == "active"]
        if user.get("role") == "admin" and len(active_admins) <= 1:
            raise UserStoreError("USER_LAST_ADMIN",
                                 "这是最后一个可用管理员，不能降权/禁用",
                                 field="role" if reason == "demote" else "status",
                                 hint="先建/启用另一个 admin，再动这个账号（防止把自己关在门外）")

    def _patch(self, user_id: str, patch: Mapping[str, Any]) -> None:
        with self.store._lock:
            user = self.store.data()["users"].get(str(user_id))
            if not isinstance(user, dict):
                raise UserStoreError("USER_NOT_FOUND", "找不到这个用户", status=404,
                                     field="user_id")
            user.update(dict(patch))
            user["updated_at"] = self._iso()
            self.store.save()

    def _locked_until(self, user: Mapping[str, Any]) -> int:
        raw = user.get("locked_until")
        if not isinstance(raw, (int, float)):
            return 0
        return int(raw) if int(raw) > int(self._now()) else 0

    def _revoke_user_sessions(self, user_id: str, *, keep: str | None = None) -> int:
        with self.store._lock:
            count = 0
            for sid, record in self.store.data()["sessions"].items():
                if isinstance(record, dict) and record.get("user_id") == str(user_id) \
                        and (keep is None or sid != keep):
                    record["revoked"] = True
                    record["revoked_at"] = self._iso()
                    count += 1
            self.store.save()
            return count

    def _prune_sessions(self) -> int:
        stamp = int(self._now())
        sessions = self.store.data()["sessions"]
        stale = [sid for sid, record in sessions.items()
                 if isinstance(record, dict) and (int(record.get("expires") or 0) < stamp - 60
                                                  or record.get("revoked"))]
        for sid in stale[:200]:
            sessions.pop(sid, None)
        return len(stale)

    def session_report(self) -> dict[str, Any]:
        with self.store._lock:
            sessions = self.store.data()["sessions"]
            total = len(sessions)
            live = sum(1 for record in sessions.values()
                       if isinstance(record, dict) and not record.get("revoked")
                       and int(record.get("expires") or 0) >= int(self._now()))
        return {"total": total, "live": live, "ttl_seconds": self._session_ttl}

    def secrets_leaked_to_disk(self, candidates: Iterable[str]) -> list[str]:
        """自检：把用过的明文口令交进来，报告哪些出现在了状态文件里（应当为空）。

        存盘只允许有口令哈希串（`pbkdf2_sha256$轮数$盐$哈希`）。
        """
        text = self.store.raw_text()
        return [str(item) for item in candidates
                if isinstance(item, str) and item and item in text]

    def sensitive_keys_on_disk(self) -> list[str]:
        """状态文件顶层键快照（审计/文档要证明没有明文口令字段）。"""
        with self.store._lock:
            return sorted(self.store.data().keys())


def _client_hint(client_info: Mapping[str, Any] | None) -> str:
    """审计里只留可公开的粗粒度线索，IP 由 t9 审计层再脱敏。"""
    if not isinstance(client_info, Mapping):
        return ""
    bits: list[str] = []
    channel = client_info.get("channel")
    if isinstance(channel, str) and channel.strip():
        bits.append(channel.strip()[:16])
    if client_info.get("cookie") is not None:
        bits.append("cookie")
    if client_info.get("token_header") is not None:
        bits.append("token")
    return "+".join(bits)


def permission_matrix() -> dict[str, tuple[str, ...]]:
    """矩阵快照（K-11 用例直接读它）。"""
    return {role: tuple(ROLE_PERMISSIONS.get(role, ())) for role in ROLES}


def can(role: str, permission: str) -> bool:
    """模块级快捷函数（前端/脚本对拍用，与 Authenticator.can 同源）。"""
    return permission in ROLE_PERMISSIONS.get(role, ())


def rank_at_least(role: str, min_role: str | None) -> bool:
    """记录级 `policy.min_role_to_decide` 的快捷判定（与 Authenticator 同源）。"""
    if not min_role:
        return True
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(str(min_role), 0)


def default_state_path(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """默认落点：``<包上级>/var/auth/auth-state.json``（与审计日志同侧）。"""
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    return base / "var" / "auth" / STATE_FILE_NAME


def _self_check() -> int:
    """自检入口：``python -m agent_explainer.auth``。

    只在临时状态文件上跑（`var/auth/s-*.json`），跑完删除，不碰真实用户表。
    口令轮数压低只为跑得动；真实运行用默认 210000 轮。
    """
    base = default_state_path().parent
    base.mkdir(parents=True, exist_ok=True)
    names = ("s-local.json", "s-team.json")
    report: dict[str, Any] = {"scratch_dir": str(base)}
    try:
        for name in names:
            if (base / name).exists():
                (base / name).unlink()
        local = Authenticator("local", state_path=base / names[0], iterations=1_000)
        report["local"] = {"required": local.required,
                           "actor": local.local_session()["user_id"],
                           "intervene": local.can("local", "intervene.decide"),
                           "users_manage": local.can("local", "users.manage")}

        env = {"TRACELENS_ADMIN_PASSWORD": "bootstrap-Pass-2026"}
        team = Authenticator("team", state_path=base / names[1],
                             iterations=1_000, env=env)
        boot = team.bootstrap_report or {}
        admin = team.authenticate("admin", "bootstrap-Pass-2026")
        team.check_csrf(admin, admin["csrf_token"])
        team.require(admin, "users.manage")
        report["bootstrap"] = {"username": boot.get("username"), "source": boot.get("source"),
                               "public_shape_has_password": "password" in
                               team.public_user(admin and team._require_user(admin["user_id"]))}
        viewer = team.create_user("viewer1", "viewer", password="viewer-Pass-2026")
        editor = team.create_user("editor1", "editor", password="editor-Pass-2026")
        v_session = team.authenticate("viewer1", "viewer-Pass-2026")
        e_session = team.authenticate("editor1", "editor-Pass-2026")
        probe: list[str] = []
        for permission in PERMISSIONS:
            for session in (v_session, e_session, admin):
                try:
                    team.require(session, permission)
                    probe.append(f"{session['username']}:{permission}=ok")
                except AuthError as exc:
                    probe.append(f"{session['username']}:{permission}={exc.code}")
        report["matrix_probe"] = probe
        report["intervene"] = {"viewer": team.can("viewer", "intervene.decide"),
                              "editor": team.can("editor", "intervene.decide"),
                              "admin": team.can("admin", "intervene.decide")}
        report["record_gate"] = {"viewer_x_editor": team.rank_at_least("viewer", "editor"),
                                 "editor_x_editor": team.rank_at_least("editor", "editor")}
        team.set_role(editor["id"], "viewer")
        report["after_set_role"] = (team.get_user(editor["id"]) or {}).get("role")
        team.set_status(editor["id"], "disabled")
        report["after_disable"] = (team.get_user(editor["id"]) or {}).get("status")
        try:
            team.authenticate("editor1", "editor-Pass-2026")
            report["disabled_login"] = "allowed(BAD)"
        except AuthError as exc:
            report["disabled_login"] = exc.code
        try:
            team.set_status(admin["user_id"], "disabled", actor=admin)
            report["self_disable"] = "allowed(BAD)"
        except AuthError as exc:
            report["self_disable"] = exc.code
        for _ in range(5):
            try:
                team.authenticate("viewer1", "wrong-Pass-1234")
            except AuthError:
                pass
        report["lockout"] = "not locked"
        try:
            team.authenticate("viewer1", "viewer-Pass-2026")
            report["lockout"] = "not locked (unexpected)"
        except AuthRateLimitError as exc:
            report["lockout"] = {"code": exc.code, "retry_after": exc.retry_after}
        team.logout(v_session["token"])
        try:
            team.session_from_token(v_session["token"])
            report["after_logout"] = "still valid (BAD)"
        except AuthError as exc:
            report["after_logout"] = exc.code
        try:
            team.session_from_token(v_session["token"][:-2] + "xy")
            report["forged_token"] = "accepted (BAD)"
        except AuthError as exc:
            report["forged_token"] = exc.code
        report["users"] = sorted(u["username"] for u in team.list_users())
        report["plaintext_leak"] = team.secrets_leaked_to_disk(
            ["bootstrap-Pass-2026", "viewer-Pass-2026", "editor-Pass-2026"])
        report["events"] = sorted({item["event"] for item in team.events})
        report["ok"] = bool(
            report["intervene"]["viewer"] is False
            and report["intervene"]["editor"] is True
            and report["local"]["intervene"] is True
            and report["local"]["users_manage"] is False
            and report["plaintext_leak"] == []
            and report["self_disable"] == "USER_SELF_DISABLE"
            and report["disabled_login"] == "AUTH_USER_DISABLED"
            and report["after_logout"] == "AUTH_TOKEN_REVOKED"
            and report["forged_token"] in ("AUTH_TOKEN_INVALID", "AUTH_TOKEN_REVOKED")
            and report["record_gate"]["viewer_x_editor"] is False
            and isinstance(report["lockout"], dict)
            and report["lockout"]["code"] == "RATE_LIMITED"
            and report["bootstrap"]["public_shape_has_password"] is False)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["ok"] else 1
    finally:
        for name in names:
            victim = base / name
            if victim.exists():
                victim.unlink()


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(_self_check())


def __getattr__(name: str) -> Any:
    """模块级守门：鉴权没有"删除用户"这种接口，禁用才是产品口径。"""
    lowered = str(name).lower()
    if any(verb in lowered for verb in ("delete", "remove", "purge", "erase", "wipe",
                                        "truncate", "reset_all", "erase_users")):
        raise AuthForbiddenError("鉴权不提供删除用户：禁用（status=disabled）是唯一口径",
                                 code="USER_DELETE_DISABLED",
                                 detail={"attempted": name},
                                 hint="禁用会吊销会话并保留责任归属；删除会让审计查无此人")
    raise AttributeError(f"module {__name__} 没有属性 {name}")
