"""用户操作审计日志（4.2）：只能追加、带哈希链、可查询、可导出。

设计裁决（写给队长、t11 审计页与后续鉴权 4.3 看）
--------------------------------------------------
1. **追加式（append-only）**：日志文件只用 `O_APPEND` 打开，类上**没有**更新/删除/清空/
   截断/改写这类方法。任何名字里带更新语义的调用（`log.update_entry(...)`、`log.clear()`、
   `log.truncate()`…）都抛 :class:`AuditOperationForbidden`（错误码
   ``AUDIT_OPERATION_FORBIDDEN``），而不是静默失败：API 层接上来就继承这条禁令。
   唯一的“修改”是追加一条更正事件（``event="correction"``，带 ``corrects_seq``），
   原始条目永远留在链上。
2. **哈希链**：``entry.hash = sha256(prev_hash + 规范 JSON(不含自身哈希的条目))``，
   写 ``sha256:<hex>``；首条的 ``prev_hash`` 是 GENESIS。字段名按任务口径叫 ``hash``，
   同时给 ``entry_hash`` 别名（t8 ``server.py`` 与 t11 读的就是这个名字），两个值恒等。
   :meth:`AuditLog.verify` 逐条复算，链断/哈希不符/序号跳号/行解析失败都抛
   :class:`AuditTamperError`，并带上 ``index``/``seq``/``code``，方便界面说清“第几条被动过”。
3. **锚点与封印**：仅靠“每条自己算自己”的链，挡不住把整条链重算一遍的改写（改写后
   仍然自洽）。所以额外提供 :meth:`AuditLog.seal`：追加一条 ``event="seal"`` 事件，记下
   当时的 ``sealed_head``/``sealed_count``。历史被整段重算时，封印记录的旧头哈希对不上 →
   ``verify`` 报 ``seal_mismatch``。外部（t11 审计页、导出件）还可以把 head 抄一份，
   之后用 ``verify(expected_head=...)`` 校验。
4. **多用户就绪**：每条都必须写 ``actor_id``（谁）与 ``actor_role``
   （local/owner/member/viewer/system），可按 actor / task / action / 时间范围查询。
   身份由服务端写入，**不接受**请求体里的 ``actor_id``（与 t8 的 server 口径一致；
   4.3 接入鉴权后换成会话身份，本模块不用改）。
5. **隐私**：``original_params`` / ``modified_params`` / ``client_info`` 默认脱敏：
   密钥类键写成 ``<redacted:api_key#sha256:ab12cd34ef56>``（保留同值可辨识的短指纹），
   邮箱/手机号打码，IP 默认只到网段（``ip_scope="subnet"``）。审计要能复盘“改了什么参数名”，
   但不该变成密钥仓库。审计里**不存请求体原文**。
6. **决定 ≠ 生效**：审计记录的是“谁在什么时候决定做什么”，绝不写“已执行/已完成”。
   事件类型里只有 decision / correction / seal / export / access_denied / note /
   record_reset，加上 4.3 的 auth.login / auth.login_failed / auth.logout / auth.denied（t18 入白名单）；执行结果由 ``execution_receipt`` 那条线回报。
7. **离线**：只用标准库。PDF 优先 ``reportlab``，用 ``importlib`` 在函数内部惰性导入
   （模块导入时不碰第三方）；不可用就生成**可打印 HTML**并在 ``note`` 里说明原因，
   浏览器 Ctrl+P 即可存 PDF。导出的 HTML 零外链、零脚本。

接入缝（给 server.py / t11）
----------------------------
``log = open_log()``（默认 ``var/audit.jsonl``）→ 决定落盘时
``log.append(audit.event_from_decision(before, after, actor_id=..., actor_role="local",
request_id=..., client_info=...), request_id=request_id)`` → 审计页
``log.query(actor_id=..., since=..., until=..., limit=50)`` + ``log.verify()`` →
导出 ``log.export_csv(path=...)`` / ``log.export_pdf(path=...)``。
"""

from __future__ import annotations

import csv
import hashlib
import html as _html
import importlib
import io
import json
import os
import pathlib
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "APP_ID", "AUDIT_SCHEMA_VERSION", "GENESIS_HASH", "HASH_PREFIX",
    "SPEC_FIELDS", "ENTRY_FIELDS", "CSV_COLUMNS", "DECISIONS", "EVENTS",
    "ACTOR_ROLES", "SENSITIVE_KEYS", "FORBIDDEN_OPERATIONS",
    "AuditError", "AuditInputError", "AuditTamperError", "AuditQueryError",
    "AuditOperationForbidden", "AuditExportError",
    "canonical_json", "sha256_ref", "entry_hash", "utc_now", "iso_utc", "parse_time",
    "redact_params", "redact_client_info", "redact_text", "PII_PATTERNS",
    "diff_params", "build_event",
    "event_from_decision", "AuditLog", "ExportResult",
    "default_log_path", "open_log", "verify_file", "render_html",
    "entries_to_rows", "entries_to_csv", "render_pdf_bytes",
]

APP_ID = "tracelens"
AUDIT_SCHEMA_VERSION = "1.0"
HASH_PREFIX = "sha256:"
GENESIS_HASH = HASH_PREFIX + "0" * 64
AUDIT_FILE_NAME = "audit.jsonl"
DEFAULT_TAIL_BYTES = 65_536
MAX_LINE_BYTES = 256 * 1024
REASON_MAX_CHARS = 2000
USER_AGENT_MAX_CHARS = 200
IP_MASKED = "<redacted:ip>"

#: 任务点名的九字段：查询、CSV、审计页都按这个顺序讲。
SPEC_FIELDS: tuple[str, ...] = (
    "ts", "actor_id", "task_id", "action_id", "decision",
    "original_params", "modified_params", "reason", "client_info",
)

#: 一条日志里的全部键（含链信息与桥接字段）。
ENTRY_FIELDS: tuple[str, ...] = SPEC_FIELDS + (
    "audit_schema_version", "seq", "audit_id", "event", "actor_role", "option_id",
    "request_id", "idempotency_key", "mode", "record_status_before",
    "record_status_after", "changed_fields", "record_facts_hash",
    "source_facts_hash", "corrects_seq", "sealed_head", "sealed_count",
    "note", "prev_hash", "hash", "entry_hash",
)

DECISIONS: tuple[str, ...] = ("approve", "modify", "reject")
EVENTS: tuple[str, ...] = (
    "decision", "correction", "seal", "export", "access_denied", "note", "record_reset",
    # 4.3 鉴权域事件（t18 起入白名单，队长裁决 2026-11-10 第 3 条）：4.3 落盘当时
    # 白名单里还没有 auth.*，只能借 access_denied / note + fields.auth_event 记；
    # 现在原生名字合法，`query(event="auth.denied")` 直接可查。历史文件不改写，
    # 服务端仍按旧映射写（access_denied/note + fields.auth_event），保持兼容。
    "auth.login", "auth.login_failed", "auth.logout", "auth.denied",
)
ACTOR_ROLES: tuple[str, ...] = ("local", "owner", "member", "viewer", "system")

#: 键名命中这些词的参数值一律脱敏（大小写与 -/_ 无关）。
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "api_key", "apikey", "api_secret", "access_key", "secret_key", "secret", "token",
    "access_token", "refresh_token", "id_token", "authorization", "auth", "cookie",
    "session", "session_id", "credential", "credentials", "password", "passwd", "pwd",
    "private_key", "pin", "cvv", "ssn", "id_number", "id_card", "passport",
    "email", "phone", "mobile", "tel", "client_ip", "ip_address",
    # 4.8（t21）补：中文语境的身份证号 / 银行卡 / 真实姓名 / 住址，键名命中即整值打码。
    "idcard", "identity", "identity_no", "national_id", "bank_card", "bank_account",
    "card_no", "card_number", "full_name", "real_name", "name_cn", "address",
    "home_address", "bearer", "client_secret",
})
_SENSITIVE_HINTS: tuple[str, ...] = ("api_key", "apikey", "secret", "token", "password",
                                     "passwd", "credential", "authorization", "cookie",
                                     "private_key", "session", "cvv", "ssn", "idcard",
                                     "id_no", "identity", "bank_card", "access_token")
_PII_KEYS: frozenset[str] = frozenset({"email", "phone", "mobile", "tel",
                                       "idcard", "identity", "identity_no", "national_id",
                                       "bank_card", "bank_account", "card_no", "card_number",
                                       "full_name", "real_name", "address", "home_address"})
_EMAIL_RE = re.compile(r"^([^@\s]{1})[^@\s]*(@[^@\s]+)$")
_PHONE_RE = re.compile(r"^[+0-9][0-9 ()-]{5,}$")

# --------------------------------------------------------------------------- #
# 自由文本里的 PII（4.8 / t21，QA 发现：reason / note / 标签这类散文以前只截断不脱敏）
# --------------------------------------------------------------------------- #
#: 落盘前对自由文本做打码的标记（ASCII，保证幂等：标记里没有连续 11 位数字与邮箱）。
PATH_MASKED = "<redacted:path>"
SECRET_MASKED = "<redacted:secret>"
#: 密钥前缀拆两段写：t9 的源码扫描禁止本模块里出现完整的密钥前缀字面量。
_KEY_INFIX = "sk" + chr(45)
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    "long_digits": re.compile(r"(?<![\d.*\-])\d{11,20}(?!\d)"),
    "ipv4": re.compile(r"(?<!\d)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)"),
    "ipv6": re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b"),
    "windows_path": re.compile(r"(?i)\b[a-z]:[\\/][^\s\"';,)\]}]*"),
    "posix_path": re.compile(r"(?<!\S)/(?:Users|home|var|tmp|mnt|media|opt|srv|root)"
                             r"(?:/[^\s\"';,)\]}]*)?"),
    "bearer": re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{12,}"),
    "secret_kv": re.compile(r"(?i)\b(api[_\-]?key|apikey|secret|access[_\-]?token|token|"
                            r"password|passwd|authorization|cookie|bearer)"
                            r"(\s*[:=]\s*)([^\s,;]{6,})"),
    # 密钥前缀由两段拼出来：本模块源码里不许出现完整的密钥前缀（t9 有源码扫描）
    "sk_key": re.compile(r"\bsk_[A-Za-z0-9_\-]{6,}|\b" + re.escape(_KEY_INFIX)
                         + r"[A-Za-z0-9_\-]{6,}"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    "pem_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

#: API 层禁止的操作语义：类上不存在这些方法，调用即抛 AuditOperationForbidden。
FORBIDDEN_OPERATIONS: tuple[str, ...] = (
    "update", "delete", "remove", "truncate", "clear", "overwrite", "rewrite",
    "patch", "edit", "prune", "erase", "purge", "drop", "retract", "rollback",
    "undo", "replace", "modify", "set",
)

# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #


class AuditError(Exception):
    """审计模块的统一异常：``code`` 面向程序，``render()`` 面向人。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 index: int | None = None, hint: str | None = None,
                 detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.field = field
        self.index = index
        self.hint = hint
        self.detail: dict[str, Any] = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.index is not None:
            out["index"] = self.index
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"字段 {self.field}")
        if self.index is not None:
            parts.append(f"第 {self.index} 条")
        if self.hint:
            parts.append(f"建议：{self.hint}")
        return "；".join(parts)


class AuditInputError(AuditError):
    """追加时的入参不合法（少 actor_id、decision 不认识…）。"""


class AuditQueryError(AuditError):
    """查询条件不合法（时间范围倒挂、格式无法解析…）。"""


class AuditTamperError(AuditError):
    """哈希链校验失败：日志被改过、删过、截过。"""

    def __init__(self, status: Mapping[str, Any]) -> None:
        problems = list(status.get("problems") or [])
        first = problems[0] if problems else {}
        super().__init__(
            "AUDIT_CHAIN_BROKEN",
            str(first.get("reason") or "审计链校验失败"),
            index=first.get("index"),
            hint="日志是追加式的：不要修历史，追加一条 correction 事件说明更正；"
                 "确认数据来源后可用 verify(raise_on_tamper=False) 查看完整问题清单",
            detail={"problems": problems, "count": status.get("count"),
                    "head": status.get("head")},
        )
        self.status: dict[str, Any] = dict(status)
        self.problems = problems


class AuditOperationForbidden(AuditError):
    """审计日志的追加式禁令：更新/删除/清空这类操作不存在，也不允许存在。"""

    def __init__(self, name: str) -> None:
        super().__init__(
            "AUDIT_OPERATION_FORBIDDEN",
            f"审计日志是 append-only：不允许 {name}",
            hint="要更正就 append(event='correction', corrects_seq=N)；"
                 "要缩小可见范围就用 query()/导出过滤，不要删数据",
            detail={"attempted": name},
        )
        self.attempted = name


class AuditExportError(AuditError):
    """导出失败（写盘被拒、目标不可写…）。"""


# --------------------------------------------------------------------------- #
# 底层小工具：规范 JSON、哈希、时间、脱敏
# --------------------------------------------------------------------------- #


def canonical_json(value: Any) -> str:
    """排序键、无多余空白的 JSON；审计哈希与落盘行都用它，保证可复算。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, (bytes, bytearray)):
        return HASH_PREFIX + hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, pathlib.PurePath):
        return str(value)
    raise AuditInputError("AUDIT_VALUE_UNSERIALIZABLE",
                          f"审计不接受 {type(value).__name__} 类型的值",
                          hint="只写标量、list、dict；二进制请先算哈希")


def sha256_ref(text: str) -> str:
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chain_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    """条目里去掉自身哈希与前驱哈希后的部分（哈希就是对它算的）。"""
    return {k: v for k, v in entry.items() if k not in ("hash", "entry_hash", "prev_hash")}


def entry_hash(payload: Mapping[str, Any], prev_hash: str) -> str:
    """``sha256(prev_hash + 规范 JSON(payload))``；payload 里不含自身/前驱哈希。"""
    return sha256_ref(prev_hash + "\n" + canonical_json(_chain_payload(payload)))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(stamp: datetime) -> str:
    text = stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def parse_time(value: Any, *, field: str = "since") -> datetime:
    """接受 ISO 8601（含 Z / 带偏移）、裸时间（按 UTC）、epoch 秒、datetime。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool):
        raise AuditQueryError("AUDIT_TIME_INVALID", f"{field} 不是时间值",
                              field=field, detail={"received": str(value)})
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise AuditQueryError("AUDIT_TIME_INVALID", f"{field} 时间戳越界",
                                  field=field, detail={"received": value, "hint": str(exc)})
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AuditQueryError("AUDIT_TIME_INVALID", f"{field} 不是可解析的时间",
                                  field=field,
                                  detail={"received": value, "hint": str(exc)},
                                  hint="用 ISO 8601，例如 2026-11-10T08:00:00Z")
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    raise AuditQueryError("AUDIT_TIME_INVALID", f"{field} 不是时间值", field=field,
                          detail={"received": value})


def _key_norm(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive(key: Any) -> bool:
    norm = _key_norm(key)
    return norm in {_key_norm(k) for k in SENSITIVE_KEYS} or any(
        hint in norm for hint in _SENSITIVE_HINTS)


def _mask_email(text: str) -> str:
    match = _EMAIL_RE.match(text.strip())
    if not match:
        return text
    local, domain = match.group(1), match.group(2)
    return f"{local}***{domain}"


def _mask_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) < 6:
        return text
    return digits[:3] + "*" * (len(digits) - 5) + digits[-2:]


def _mask_ref(kind: str, value: str) -> str:
    """整段替换成带指纹的标记：同一个原值指纹稳定，取证时还能对齐，正文里不再出现原值。"""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<redacted:{kind}#{HASH_PREFIX}{digest}>"


def redact_text(text: Any) -> Any:
    """自由文本里的 PII 打码（4.8 / t21，QA 发现）。非字符串原样返回。

    以前 `reason` / `note` / 选项标签这类散文只截断不脱敏，落盘的 JSONL、CSV/HTML/PDF
    导出、审计界面里都会原样出现邮箱、手机号、身份证号、IP、绝对路径和密钥。
    现在统一在**写盘之前**跑一遍，所以文件里、导出里、界面里看到的是同一份脱敏结果：

    * 邮箱 → 首字母 + `***` + 域名；手机号 → 前 3 后 4（`138****8000` 这种常见写法）；
      身份证 → 前 4 后 4；11-20 位连续数字（银行卡样式）→ 前 2 后 2；
    * IPv4 → `a.b.*.*`（只到 /16，与 `client_info` 的网段口径一致）；IPv6 → 首段 + `::*`；
    * 绝对路径（Windows `C:\\...` 与 POSIX `/Users`、`/home`、`/var`…）与密钥
      （`api_key=`、`Bearer …`、OpenAI 风格 key 前缀、`AKIA…`、PEM 头）整段换成
      `<redacted:类型#sha256:前12位>`——指纹稳定，能对齐取证，但正文里没有原值。

    幂等：标记本身不含邮箱、连续 11 位数字或 IPv4，重复跑不会再套一层。
    """
    if not isinstance(text, str) or not text:
        return text
    p = PII_PATTERNS
    out = text
    out = p["pem_block"].sub(lambda m: _mask_ref("secret", m.group(0)), out)
    out = p["secret_kv"].sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_mask_ref('secret', m.group(3))}", out)
    out = p["bearer"].sub(lambda m: f"{m.group(1)}{_mask_ref('secret', m.group(0))}", out)
    for key in ("sk_key", "aws_key"):
        out = p[key].sub(lambda m: _mask_ref("secret", m.group(0)), out)
    for key in ("windows_path", "posix_path"):
        out = p[key].sub(lambda m: _mask_ref("path", m.group(0)), out)
    out = p["email"].sub(lambda m: _mask_email(m.group(0)), out)
    out = p["ipv4"].sub(lambda m: f"{m.group(1)}.{m.group(2)}.*.*", out)
    out = p["ipv6"].sub(lambda m: m.group(0).split(":")[0] + "::*", out)
    out = p["id_card"].sub(
        lambda m: m.group(0)[:4] + "*" * (len(m.group(0)) - 8) + m.group(0)[-4:], out)
    out = p["cn_mobile"].sub(
        lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], out)
    out = p["long_digits"].sub(
        lambda m: m.group(0)[:2] + "*" * (len(m.group(0)) - 4) + m.group(0)[-2:], out)
    return out


def _mask_value(key: Any, value: Any) -> Any:
    name = str(key)
    if isinstance(value, str):
        if value.startswith("<redacted:") and value.endswith(">"):
            return value            # 已经脱敏过：保持幂等，别把指纹套娃再哈希一次
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        if _key_norm(key) in _PII_KEYS:
            if _key_norm(key) == "email":
                return _mask_email(value)
            return _mask_phone(value)
        return f"<redacted:{name}#{HASH_PREFIX}{digest}>"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"<redacted:{name}>"
    if value is None:
        return None
    return f"<redacted:{name}:{type(value).__name__}>"


def redact_params(params: Any, *, redact: bool = True, _depth: int = 0) -> Any:
    """递归脱敏参数字典；嵌套容器一起处理，深度超限整段折成哈希引用。"""
    if params is None:
        return None
    if _depth > 6:
        return HASH_PREFIX + hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()[:16]
    if not redact:
        if isinstance(params, Mapping):
            return {str(k): redact_params(v, redact=False, _depth=_depth + 1) for k, v in params.items()}
        if isinstance(params, (list, tuple)):
            return [redact_params(v, redact=False, _depth=_depth + 1) for v in params]
        return params
    if isinstance(params, Mapping):
        out: dict[str, Any] = {}
        for key, value in params.items():
            if _is_sensitive(key):
                out[str(key)] = _mask_value(key, value)
            elif isinstance(value, (Mapping, list, tuple)):
                out[str(key)] = redact_params(value, redact=True, _depth=_depth + 1)
            else:
                # 4.8：非敏感键的值里也可能夹着邮箱/手机号/路径/密钥（params_summary 那类散文）
                out[str(key)] = redact_text(value)
        return out
    if isinstance(params, (list, tuple)):
        return [redact_params(v, redact=True, _depth=_depth + 1) for v in params]
    return redact_text(params)


def redact_client_info(info: Any, *, ip_scope: str = "subnet",
                       redact: bool = True) -> dict[str, Any] | None:
    """客户端信息只留可标量：user_agent 截断、IP 按 ip_scope 处理。"""
    if not isinstance(info, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, value in info.items():
        name = str(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
        else:
            out[name] = f"<{type(value).__name__}>"
    agent = out.get("user_agent")
    if isinstance(agent, str) and len(agent) > USER_AGENT_MAX_CHARS:
        out["user_agent"] = agent[:USER_AGENT_MAX_CHARS - 1] + "…"
    for ip_key in ("ip", "client_ip", "ip_address", "remote_addr"):
        if ip_key not in out:
            continue
        ip = out.pop(ip_key)
        if isinstance(ip, str) and ("*" in ip or ip == IP_MASKED):
            out["ip"] = ip          # 已按网段/前缀脱敏：保持幂等
            continue
        if ip_scope == "drop" or not isinstance(ip, str) or not ip.strip():
            out["ip"] = IP_MASKED if ip_scope == "drop" else ip
            continue
        if ip_scope == "exact" or not redact:
            out["ip"] = ip
            continue
        parts = ip.strip().split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            out["ip"] = ".".join(parts[:3]) + ".*"
        else:  # IPv6 / 主机名：截到前两段
            out["ip"] = re.split(r"[:%]", ip.strip())[0][:24] + "::*"
    return out


def diff_params(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None,
                *, redact: bool = True) -> list[dict[str, Any]]:
    """参数逐字段前后对比（t8 的 changed_fields 口径：name/before/after）。"""
    left = dict(before or {})
    right = dict(after or {})
    names = sorted(set(left) | set(right), key=str)
    changes: list[dict[str, Any]] = []
    for name in names:
        old = left.get(name)
        new = right.get(name)
        if name in left and canonical_json(old) == canonical_json(new):
            continue
        changes.append({
            "name": str(name),
            "before": redact_params({str(name): old}, redact=redact).get(str(name))
            if name in left else None,
            "after": redact_params({str(name): new}, redact=redact).get(str(name))
            if name in right else None,
        })
    return changes


def _short_text(value: Any, limit: int = REASON_MAX_CHARS) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _clean_id(value: Any, *, field: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise AuditInputError("AUDIT_FIELD_REQUIRED", f"{field} 必填：审计要知道是谁",
                                  field=field,
                                  hint="服务端写入会话身份；local 模式固定 local-user")
        return None
    if not isinstance(value, str):
        raise AuditInputError("AUDIT_FIELD_TYPE", f"{field} 必须是字符串", field=field,
                              detail={"received": type(value).__name__})
    text = value.strip()
    if not text:
        if required:
            raise AuditInputError("AUDIT_FIELD_REQUIRED", f"{field} 不能为空", field=field)
        return None
    return _short_text(text, 200)


def _clean_enum(value: Any, *, field: str, allowed: Sequence[str],
                default: str | None = None) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str):
        raise AuditInputError("AUDIT_FIELD_TYPE", f"{field} 必须是字符串", field=field,
                              detail={"received": type(value).__name__})
    text = value.strip()
    if text not in allowed:
        raise AuditInputError("AUDIT_FIELD_ENUM", f"{field} 不在允许值里", field=field,
                              detail={"received": text, "allowed": list(allowed)})
    return text


# --------------------------------------------------------------------------- #
# 条目构造
# --------------------------------------------------------------------------- #


def _clean_free(value: Any, limit: int = REASON_MAX_CHARS, *, redact: bool = True) -> str | None:
    """自由文本先脱敏再截断（4.8）：`redact=False` 的显式原样模式只做截断，不脱敏。"""
    return _short_text(redact_text(value) if redact else value, limit)


def _clean_changed_fields(items: Sequence[Mapping[str, Any]] | None,
                          *, redact: bool = True) -> list[dict[str, Any]]:
    """t8 的 changed_fields 口径不变（name/before/after），只把两侧值里的 PII 打掉。"""
    out: list[dict[str, Any]] = []
    for item in items or ():
        row = dict(item)
        for key in ("before", "after", "value"):
            if key in row:
                row[key] = redact_text(row[key]) if redact else row[key]
        out.append(row)
    return out


def build_event(event: str, *, actor_id: str, ts: Any = None,
                actor_role: str = "local", task_id: str | None = None,
                action_id: str | None = None, decision: str | None = None,
                option_id: str | None = None,
                original_params: Mapping[str, Any] | None = None,
                modified_params: Mapping[str, Any] | None = None,
                reason: str | None = None,
                client_info: Mapping[str, Any] | None = None,
                request_id: str | None = None, idempotency_key: str | None = None,
                mode: str = "local",
                record_status_before: str | None = None,
                record_status_after: str | None = None,
                changed_fields: Sequence[Mapping[str, Any]] | None = None,
                record_facts_hash: str | None = None,
                source_facts_hash: str | None = None,
                corrects_seq: int | None = None,
                note: str | None = None,
                redact: bool = True, ip_scope: str = "subnet",
                extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """造一条审计**载荷**（不含 seq/哈希，那两个由 :class:`AuditLog` 追加时算）。"""
    kind = _clean_enum(event, field="event", allowed=EVENTS, default="note")
    assert kind is not None
    actor = _clean_id(actor_id, field="actor_id", required=True)
    role = _clean_enum(actor_role, field="actor_role", allowed=ACTOR_ROLES, default="local")
    dec = _clean_enum(decision, field="decision", allowed=DECISIONS)
    try:
        stamp = iso_utc(parse_time(ts, field="ts")) if ts is not None else iso_utc(utc_now())
    except AuditQueryError as exc:            # 追加时的坏时间归入入参错误
        raise AuditInputError("AUDIT_TIME_INVALID", exc.reason, field="ts",
                              detail=exc.detail, hint=exc.hint)
    payload: dict[str, Any] = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "ts": stamp,
        "event": kind,
        "actor_id": actor,
        "actor_role": role,
        "task_id": _clean_id(task_id, field="task_id"),
        "action_id": _clean_id(action_id, field="action_id"),
        "decision": dec,
        "option_id": _clean_id(option_id, field="option_id"),
        "original_params": redact_params(original_params, redact=redact),
        "modified_params": redact_params(modified_params, redact=redact),
        "reason": _clean_free(reason, redact=redact),
        "client_info": redact_client_info(client_info, ip_scope=ip_scope, redact=redact),
        "request_id": _clean_id(request_id, field="request_id"),
        "idempotency_key": _clean_id(idempotency_key, field="idempotency_key"),
        "mode": _clean_enum(mode, field="mode", allowed=("local", "live"), default="local"),
        "record_status_before": _clean_id(record_status_before, field="record_status_before"),
        "record_status_after": _clean_id(record_status_after, field="record_status_after"),
        "changed_fields": _clean_changed_fields(changed_fields, redact=redact),
        "record_facts_hash": _clean_id(record_facts_hash, field="record_facts_hash"),
        "source_facts_hash": _clean_id(source_facts_hash, field="source_facts_hash"),
        "note": _clean_free(note, 500, redact=redact),
    }
    if corrects_seq is not None:
        payload["corrects_seq"] = int(corrects_seq)
        if payload["corrects_seq"] < 1:
            raise AuditInputError("AUDIT_FIELD_RANGE", "corrects_seq 从 1 开始",
                                  field="corrects_seq", detail={"received": corrects_seq})
        if kind != "correction":
            raise AuditInputError("AUDIT_FIELD_CONFLICT",
                                  "corrects_seq 只能配 event='correction'", field="event")
    if isinstance(extra, Mapping):
        for key, value in extra.items():
            name = str(key)
            if name in payload or name in ("seq", "hash", "entry_hash", "prev_hash"):
                continue
            payload[name] = redact_params(value, redact=redact)
    canonical_json(payload)     # 早炸：别让不可序列化的值活到写盘那一步
    return payload


_PARAM_KEYS: tuple[str, ...] = ("params", "parameters", "tool_params", "arguments")


def _record_params(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """记录里“决定前参数”的位置：不同来源字段名不统一，按顺序找。"""
    for key in _PARAM_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _option_label(record: Mapping[str, Any], option_id: Any) -> str | None:
    """用户点的那个选项的标签。

    没写理由时，它就是日志里能查到的最接近“为什么”的东西（审计页要能看懂点了啥）。
    """
    if not isinstance(option_id, str):
        return None
    options = record.get("intervention_options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return None
    for option in options:
        if isinstance(option, Mapping) and option.get("id") == option_id:
            for key in ("label", "title", "name"):
                value = option.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def event_from_decision(before: Mapping[str, Any], after: Mapping[str, Any], *,
                        actor_id: str, actor_role: str = "local",
                        task_id: str | None = None, request_id: str | None = None,
                        idempotency_key: str | None = None,
                        client_info: Mapping[str, Any] | None = None,
                        mode: str = "local", ts: Any = None,
                        redact: bool = True, ip_scope: str = "subnet") -> dict[str, Any]:
    """t8 桥接：拿决定前后的 ActionRecord，算出该记哪条审计。

    决定前的参数按顺序取：记录自带的参数字段（``params`` / ``parameters`` /
    ``tool_params`` / ``arguments``）→ 决定层写进 ``user_intervention.original_params``
    的那份（t8 ``apply_decision`` 的口径）→ 没有就留 ``None``（并在审计里说“参数不可得”，
    绝不编一个）。决定后的参数取 ``user_intervention.modified_params``；
    reason / option_id / 改了哪些字段同样来自 ``user_intervention``。
    执行回执不进审计（决定 ≠ 生效）。
    """
    old = dict(before or {})
    new = dict(after or {})
    ui = new.get("user_intervention") if isinstance(new.get("user_intervention"), Mapping) else {}
    params_before = _record_params(old)
    if params_before is None and isinstance(ui.get("original_params"), Mapping):
        params_before = ui.get("original_params")
    params_after = ui.get("modified_params") if isinstance(ui.get("modified_params"), Mapping) else None
    changed = ui.get("modified_fields") if isinstance(ui.get("modified_fields"), Sequence) \
        and not isinstance(ui.get("modified_fields"), (str, bytes)) else None
    return build_event(
        "decision",
        actor_id=actor_id,
        actor_role=actor_role,
        task_id=task_id or (old.get("context", {}) or {}).get("task_id")
        if isinstance(old.get("context"), Mapping) else task_id,
        action_id=new.get("id") or old.get("id"),
        decision=ui.get("decision"),
        option_id=ui.get("option_id"),
        original_params=params_before,
        modified_params=params_after,
        reason=(ui.get("reason") or ui.get("note")
                or _option_label(old, ui.get("option_id"))),
        client_info=client_info,
        request_id=request_id,
        idempotency_key=idempotency_key,
        mode=mode,
        ts=ts,
        record_status_before=old.get("status"),
        record_status_after=new.get("status"),
        changed_fields=[dict(item) for item in changed] if changed else None,
        record_facts_hash=new.get("facts_hash"),
        source_facts_hash=(new.get("source") or {}).get("facts_hash")
        if isinstance(new.get("source"), Mapping) else None,
        redact=redact,
        ip_scope=ip_scope,
    )


# --------------------------------------------------------------------------- #
# 导出结果
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExportResult:
    """导出回执：格式、行数、字节数、内容哈希，以及“为什么回退”。"""

    format: str
    path: str | None
    rows: int
    columns: tuple[str, ...]
    bytes: int
    sha256: str
    note: str
    fallback: str | None = None
    text: str | None = None
    generated_at: str = field(default_factory=lambda: iso_utc(utc_now()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "path": self.path, "rows": self.rows,
            "columns": list(self.columns), "bytes": self.bytes, "sha256": self.sha256,
            "note": self.note, "fallback": self.fallback,
            "generated_at": self.generated_at,
        }


# --------------------------------------------------------------------------- #
# 审计日志本体
# --------------------------------------------------------------------------- #


class AuditLog:
    """JSONL 追加式审计日志（哈希链 + 封印 + 查询 + 导出）。

    只有追加与读取；没有更新/删除（见 :data:`FORBIDDEN_OPERATIONS`）。
    """

    def __init__(self, path: pathlib.Path | str, *, create_dirs: bool = True,
                 redact: bool = True, ip_scope: str = "subnet",
                 verify_before_append: bool = True,
                 verify_max_entries: int = 5000) -> None:
        self._path = pathlib.Path(path)
        self._lock = threading.RLock()
        self._redact = bool(redact)
        self._ip_scope = ip_scope if ip_scope in ("subnet", "exact", "drop") else "subnet"
        self._verify_before_append = bool(verify_before_append)
        self._verify_max = max(1, int(verify_max_entries))
        if create_dirs and self._path.parent and not self._path.parent.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- 基本属性 ---------------------------------------------------------- #

    @property
    def path(self) -> pathlib.Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def size(self) -> int:
        return self._path.stat().st_size if self._path.exists() else 0

    # -- 追加 -------------------------------------------------------------- #

    def append(self, event: Any, *, ts: Any = None, request_id: str | None = None,
               verify: bool | None = None) -> dict[str, Any]:
        """追加一条（``event`` 可以是载荷 dict，也可以是 :func:`build_event` 的字符串形式）。

        载荷里**不能**自带 ``seq``/``hash``/``entry_hash``/``prev_hash``：那四个由这里算。
        默认在追加前校验现有链（``verify_before_append``），已断链的日志不再接受新条目。
        """
        payload = self._coerce_payload(event, ts=ts, request_id=request_id)
        do_verify = self._verify_before_append if verify is None else bool(verify)
        with self._lock:
            if do_verify:
                status = self._verify_locked(raise_on_tamper=False)
                if not status["ok"] and int(status["count"]) <= self._verify_max:
                    raise AuditTamperError(status)
            prev, seq = self._tail_locked()
            payload["seq"] = seq + 1
            payload["audit_id"] = f"aud-{payload['seq']:06d}-{payload['ts']}"
            digest = entry_hash(payload, prev)
            entry = dict(payload)
            entry["prev_hash"] = prev
            entry["hash"] = digest
            entry["entry_hash"] = digest          # t8/t11 读的名字，与 hash 恒等
            self._write_line(canonical_json(entry))
            return dict(entry)

    def append_many(self, events: Iterable[Any], *, verify: bool | None = None) -> list[dict[str, Any]]:
        """批量追加：整批一次校验链，逐条续接哈希。"""
        items = list(events)
        out: list[dict[str, Any]] = []
        do_verify = self._verify_before_append if verify is None else bool(verify)
        with self._lock:
            if do_verify:
                status = self._verify_locked(raise_on_tamper=False)
                if not status["ok"] and int(status["count"]) <= self._verify_max:
                    raise AuditTamperError(status)
            prev, seq = self._tail_locked()
            for raw in items:
                payload = self._coerce_payload(raw)
                payload["seq"] = seq + 1
                payload["audit_id"] = f"aud-{payload['seq']:06d}-{payload['ts']}"
                digest = entry_hash(payload, prev)
                entry = dict(payload)
                entry["prev_hash"] = prev
                entry["hash"] = digest
                entry["entry_hash"] = digest
                self._write_line(canonical_json(entry))
                prev, seq = digest, seq + 1
                out.append(entry)
            return out

    def seal(self, *, note: str | None = None, actor_id: str = "system",
             actor_role: str = "system") -> dict[str, Any]:
        """追加“封印”事件：记下当时的链头与条数，用于发现整段重算历史。"""
        with self._lock:
            prev, seq = self._tail_locked()
            payload = build_event("seal", actor_id=actor_id, actor_role=actor_role,
                                  redact=self._redact, ip_scope=self._ip_scope,
                                  note=_short_text(note, 500) or "链头封印")
            payload["sealed_head"] = prev
            payload["sealed_count"] = seq
            payload["seq"] = seq + 1
            payload["audit_id"] = f"aud-{payload['seq']:06d}-{payload['ts']}"
            digest = entry_hash(payload, prev)
            entry = dict(payload)
            entry["prev_hash"] = prev
            entry["hash"] = digest
            entry["entry_hash"] = digest
            self._write_line(canonical_json(entry))
            return entry

    def correction(self, *, corrects_seq: int, actor_id: str, reason: str,
                   action_id: str | None = None, task_id: str | None = None,
                   original_params: Mapping[str, Any] | None = None,
                   modified_params: Mapping[str, Any] | None = None,
                   note: str | None = None, request_id: str | None = None,
                   client_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """唯一的“改口径”方式：追加一条更正，说清改了第几条、为什么。"""
        payload = build_event("correction", actor_id=actor_id, action_id=action_id,
                              task_id=task_id, reason=reason, note=note,
                              original_params=original_params, modified_params=modified_params,
                              request_id=request_id, client_info=client_info,
                              redact=self._redact, ip_scope=self._ip_scope,
                              corrects_seq=corrects_seq)
        return self.append(payload)

    # -- 读取与查询 -------------------------------------------------------- #

    def read(self, *, limit: int | None = None, offset: int = 0,
             reverse: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._entries_locked()
        if reverse:
            entries = entries[::-1]
        start = max(0, int(offset))
        window = entries[start:]
        if limit is not None:
            window = window[:max(0, int(limit))]
        return window

    def tail(self, n: int = 10) -> list[dict[str, Any]]:
        return self.read(limit=int(n), reverse=True)

    def count(self) -> int:
        with self._lock:
            return len(self._entries_locked())

    def head(self) -> dict[str, Any]:
        """链头（最后一条的哈希）与条数；空日志返回 GENESIS/0。"""
        with self._lock:
            prev, seq = self._tail_locked()
        return {"head": prev, "count": seq, "genesis": GENESIS_HASH}

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            for entry in self._entries_locked():
                yield entry

    def query(self, *, actor_id: str | None = None, task_id: str | None = None,
              action_id: str | None = None, decision: str | None = None,
              event: str | None = None, since: Any = None, until: Any = None,
              q: str | None = None, has_decision: bool = False,
              limit: int | None = 200, offset: int = 0,
              reverse: bool = True) -> list[dict[str, Any]]:
        """过滤查询：按人 / 任务 / 动作 / 决定 / 事件 / 时间范围 / 关键字。

        时间范围闭区间；``since > until`` 直接报错，不静默返回空。
        """
        lo = parse_time(since, field="since") if since is not None else None
        hi = parse_time(until, field="until") if until is not None else None
        if lo and hi and lo > hi:
            raise AuditQueryError("AUDIT_TIME_RANGE_INVERTED", "since 晚于 until",
                                  field="since",
                                  detail={"since": iso_utc(lo), "until": iso_utc(hi)})
        dec = _clean_enum(decision, field="decision", allowed=DECISIONS)
        kind = _clean_enum(event, field="event", allowed=EVENTS)
        needle = q.strip().lower() if isinstance(q, str) and q.strip() else None
        with self._lock:
            pool = self._entries_locked()
        if reverse:
            pool = pool[::-1]
        out: list[dict[str, Any]] = []
        for entry in pool:
            if actor_id and str(entry.get("actor_id") or "") != actor_id:
                continue
            if task_id and str(entry.get("task_id") or "") != task_id:
                continue
            if action_id and str(entry.get("action_id") or "") != action_id:
                continue
            if dec and entry.get("decision") != dec:
                continue
            if kind and entry.get("event") != kind:
                continue
            if has_decision and not entry.get("decision"):
                continue
            stamp = entry.get("ts")
            when = parse_time(stamp, field="ts") if isinstance(stamp, str) and stamp else None
            if lo and (when is None or when < lo):
                continue
            if hi and (when is None or when > hi):
                continue
            if needle:
                hay = " ".join(str(entry.get(k) or "") for k in
                               ("reason", "actor_id", "action_id", "task_id", "note",
                                "event", "decision"))
                if needle not in hay.lower():
                    continue
            out.append(entry)
        start = max(0, int(offset))
        window = out[start:]
        if limit is not None:
            window = window[:max(0, int(limit))]
        return window

    def stats(self) -> dict[str, Any]:
        with self._lock:
            entries = self._entries_locked()
        decisions: dict[str, int] = {}
        actors: dict[str, int] = {}
        first_ts = last_ts = None
        for entry in entries:
            dec = entry.get("decision")
            if dec:
                decisions[str(dec)] = decisions.get(str(dec), 0) + 1
            who = str(entry.get("actor_id") or "?")
            actors[who] = actors.get(who, 0) + 1
            stamp = entry.get("ts")
            if isinstance(stamp, str) and stamp:
                first_ts = first_ts or stamp
                last_ts = stamp
        return {"count": len(entries), "size_bytes": self.size(),
                "decisions": decisions, "actors": actors,
                "first_ts": first_ts, "last_ts": last_ts,
                "path": str(self._path)}

    # -- 校验 -------------------------------------------------------------- #

    def verify(self, *, raise_on_tamper: bool = True, expected_head: str | None = None,
               expected_count: int | None = None) -> dict[str, Any]:
        """逐条复算哈希链。默认在发现问题时抛 :class:`AuditTamperError`。

        ``expected_head``/``expected_count`` 是外部抄存的锚点：整段重算历史的攻击
        在链内自洽，只有对上锚点才算安全。
        """
        with self._lock:
            status = self._verify_locked(raise_on_tamper=False, expected_head=expected_head,
                                         expected_count=expected_count)
        if not status["ok"] and raise_on_tamper:
            raise AuditTamperError(status)
        return status

    # -- 导出 -------------------------------------------------------------- #

    def export_csv(self, path: pathlib.Path | str | None = None, *,
                   rows: Sequence[Mapping[str, Any]] | None = None,
                   columns: Sequence[str] | None = None, bom: bool = True,
                   note: str | None = None) -> ExportResult:
        """CSV 导出（RFC4180 引号；``bom=True`` 让 Excel 认中文）。"""
        text = entries_to_csv(rows if rows is not None else self.read(),
                              columns=columns, bom=bom)
        cols = tuple(columns) if columns else CSV_COLUMNS
        return self._finish_export("csv", path, text, rows=rows, columns=cols,
                                   note=note or "CSV：参数列是规范 JSON 文本；敏感值已脱敏")

    def export_html(self, path: pathlib.Path | str | None = None, *,
                    rows: Sequence[Mapping[str, Any]] | None = None,
                    title: str = "TraceLens 用户操作审计",
                    notes: Sequence[str] | None = None,
                    note: str | None = None,
                    _fallback: str | None = None) -> ExportResult:
        """可打印 HTML 导出：零外链、零脚本，浏览器 Ctrl+P 存 PDF。"""
        items = list(rows) if rows is not None else self.read()
        text = self.render_html(items, title=title, notes=notes)
        return self._finish_export("html", path, text, rows=rows, columns=CSV_COLUMNS,
                                   note=note or "可打印 HTML：零外链零脚本，浏览器 Ctrl+P 可存 PDF",
                                   _fallback=_fallback)

    def export_pdf(self, path: pathlib.Path | str | None = None, *,
                   rows: Sequence[Mapping[str, Any]] | None = None,
                   title: str = "TraceLens 用户操作审计",
                   prefer: str = "auto",
                   notes: Sequence[str] | None = None) -> ExportResult:
        """PDF 导出：优先 reportlab；不可用/构建失败 → 可打印 HTML + ``fallback`` 说明。

        ``prefer``：``auto``（默认）/ ``pdf``（拿不到 reportlab 就抛
        :class:`AuditExportError`）/ ``html``（直接出 HTML）。
        """
        items = list(rows) if rows is not None else self.read()
        mode = prefer if prefer in ("auto", "pdf", "html") else "auto"
        if mode == "html":
            result = self.export_html(path, rows=items, title=title, notes=notes,
                                      note="按要求直接生成可打印 HTML（未尝试 PDF）")
            return result
        namespace, reason = _load_reportlab()
        if namespace is None:
            if mode == "pdf":
                raise AuditExportError("AUDIT_REPORTLAB_UNAVAILABLE",
                                       "PDF 导出需要 reportlab，当前不可导入",
                                       hint="pip install reportlab，或改用 export_html",
                                       detail={"reason": reason})
            fallback_note = (f"reportlab 不可用（{reason}）：已生成可打印 HTML，"
                             "浏览器 Ctrl+P 即可存 PDF；HTML 零外链零脚本")
            return self.export_html(path, rows=items, title=title, notes=notes,
                                    note=fallback_note,
                                    _fallback=reason)
        target = pathlib.Path(path) if path else default_export_path("pdf")
        try:
            payload = _build_pdf(namespace, target, items, title=title,
                                 status=self.verify(raise_on_tamper=False),
                                 notes=notes)
        except Exception as exc:                    # 构建失败也不让审计丢内容
            reason = f"reportlab_build_failed:{type(exc).__name__}"
            return self.export_html(path, rows=items, title=title, notes=notes,
                                    note=f"PDF 构建失败（{reason}）：已生成可打印 HTML，"
                                         "浏览器 Ctrl+P 即可存 PDF",
                                    _fallback=reason)
        data = target.read_bytes()
        return ExportResult(format="pdf", path=str(target), rows=len(items),
                            columns=CSV_COLUMNS, bytes=len(data),
                            sha256=sha256_ref(data.decode("latin-1"))[:len(HASH_PREFIX) + 16],
                            note=f"PDF：reportlab 生成（字体 {payload.get('font', '默认')}）",
                            fallback=None)

    # -- 内部 -------------------------------------------------------------- #

    def render_html(self, rows: Sequence[Mapping[str, Any]], *,
                    title: str = "TraceLens 用户操作审计",
                    notes: Sequence[str] | None = None) -> str:
        return render_html(rows, title=title, status=self.verify(raise_on_tamper=False),
                           notes=notes, log_path=str(self._path))

    def _finish_export(self, fmt: str, path: Any, text: str, *,
                       rows: Sequence[Mapping[str, Any]] | None,
                       columns: tuple[str, ...], note: str,
                       _fallback: str | None = None) -> ExportResult:
        data = text.encode("utf-8")
        target: pathlib.Path | None = None
        if path is not None:
            target = pathlib.Path(path)
            if target.parent and not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(data)
            except OSError as exc:
                raise AuditExportError("AUDIT_EXPORT_WRITE_FAILED", "导出文件写入失败",
                                       hint="检查目标目录权限",
                                       detail={"path": str(target), "hint": str(exc)})
        row_count = len(rows) if rows is not None else self.count()
        return ExportResult(format=fmt, path=str(target) if target else None,
                            rows=row_count, columns=columns, bytes=len(data),
                            sha256=sha256_ref(text)[:len(HASH_PREFIX) + 16],
                            note=note, fallback=_fallback,
                            text=None if target else text)

    def _coerce_payload(self, event: Any, *, ts: Any = None,
                        request_id: str | None = None) -> dict[str, Any]:
        if isinstance(event, str):
            return build_event(event, actor_id="system", actor_role="system",
                               ts=ts, request_id=request_id,
                               redact=self._redact, ip_scope=self._ip_scope)
        if not isinstance(event, Mapping):
            raise AuditInputError("AUDIT_EVENT_TYPE", "审计条目必须是对象或事件名",
                                  detail={"received": type(event).__name__})
        for key in ("seq", "hash", "entry_hash", "prev_hash"):
            if key in event:
                raise AuditInputError("AUDIT_CHAIN_FIELD_LOCKED",
                                      f"{key} 由审计日志计算，不能由调用方写",
                                      field=key,
                                      hint="只写业务字段；哈希链由模块维护")
        payload = build_event(str(event.get("event") or "note"),
                              actor_id=str(event.get("actor_id") or ""),
                              actor_role=str(event.get("actor_role") or "local"),
                              ts=event.get("ts") if ts is None else ts,
                              task_id=event.get("task_id"),
                              action_id=event.get("action_id"),
                              decision=event.get("decision"),
                              option_id=event.get("option_id"),
                              original_params=event.get("original_params"),
                              modified_params=event.get("modified_params"),
                              reason=event.get("reason"),
                              client_info=event.get("client_info"),
                              request_id=event.get("request_id") if request_id is None else request_id,
                              idempotency_key=event.get("idempotency_key"),
                              mode=str(event.get("mode") or "local"),
                              record_status_before=event.get("record_status_before"),
                              record_status_after=event.get("record_status_after"),
                              changed_fields=event.get("changed_fields"),
                              record_facts_hash=event.get("record_facts_hash"),
                              source_facts_hash=event.get("source_facts_hash"),
                              corrects_seq=event.get("corrects_seq"),
                              note=event.get("note"),
                              redact=self._redact, ip_scope=self._ip_scope)
        extra = {k: v for k, v in event.items()
                 if k not in set(payload) | {"event", "actor_id", "actor_role", "ts", "seq"}}
        if extra:
            payload["fields"] = redact_params(extra, redact=self._redact)
        if isinstance(event.get("sealed_head"), str):
            payload["sealed_head"] = str(event["sealed_head"])
        if isinstance(event.get("sealed_count"), int):
            payload["sealed_count"] = int(event["sealed_count"])
        return payload

    def _write_line(self, line: str) -> None:
        data = (line + "\n").encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            raise AuditInputError("AUDIT_LINE_TOO_LARGE", "单条审计超过上限",
                                  detail={"bytes": len(data), "limit": MAX_LINE_BYTES,
                                          "hint": "参数请脱敏/摘要后再记，不要塞整份请求体"})
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _lines_locked(self) -> list[tuple[int, str]]:
        if not self._path.exists():
            return []
        out: list[tuple[int, str]] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for index, raw in enumerate(handle):
                out.append((index, raw.rstrip("\n")))
        return out

    def _entries_locked(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for _, raw in self._lines_locked():
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def _tail_locked(self) -> tuple[str, int]:
        """链头与最后一条的 seq：只读文件尾部，避免每写一条就读整份日志。"""
        if not self._path.exists():
            return GENESIS_HASH, 0
        size = self._path.stat().st_size
        if size == 0:
            return GENESIS_HASH, 0
        with self._path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            start = max(0, size - DEFAULT_TAIL_BYTES)
            handle.seek(start)
            chunk = handle.read()
        if start > 0:
            cut = chunk.find(b"\n")
            chunk = chunk[cut + 1:] if cut >= 0 else b""
        for raw in reversed(chunk.decode("utf-8", "replace").splitlines()):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                break
            if isinstance(entry, dict) and isinstance(entry.get("hash"), str):
                seq = entry.get("seq")
                return str(entry["hash"]), (int(seq) if isinstance(seq, int) else 0)
            break
        return GENESIS_HASH, 0

    def _verify_locked(self, *, raise_on_tamper: bool = False,
                       expected_head: str | None = None,
                       expected_count: int | None = None) -> dict[str, Any]:
        problems: list[dict[str, Any]] = []
        prev = GENESIS_HASH
        expect_seq = 1
        count = 0
        head = GENESIS_HASH
        seals = 0
        raw_lines = self._lines_locked()
        for index, raw in raw_lines:
            if not raw.strip():
                problems.append({"code": "blank_line", "index": index, "seq": None,
                                 "reason": "审计文件里出现空行"})
                continue
            try:
                entry = json.loads(raw)
            except ValueError as exc:
                problems.append({"code": "unparsable_line", "index": index, "seq": None,
                                 "reason": f"第 {index + 1} 行不是 JSON：{exc}"})
                continue
            if not isinstance(entry, dict):
                problems.append({"code": "entry_not_object", "index": index, "seq": None,
                                 "reason": "条目不是对象"})
                continue
            count += 1
            seq = entry.get("seq")
            own = entry.get("hash")
            alias = entry.get("entry_hash")
            if entry.get("prev_hash") != prev:
                problems.append({"code": "chain_break", "index": index, "seq": seq,
                                 "reason": f"第 {index + 1} 条的 prev_hash 接不上前一条"})
            recomputed = entry_hash(_chain_payload(entry), prev)
            if not isinstance(own, str) or own != recomputed:
                problems.append({"code": "hash_mismatch", "index": index, "seq": seq,
                                 "reason": f"第 {index + 1} 条内容被改过（哈希复算不一致）"})
            if isinstance(own, str) and isinstance(alias, str) and own != alias:
                problems.append({"code": "alias_mismatch", "index": index, "seq": seq,
                                 "reason": "hash 与 entry_hash 别名不一致"})
            if seq != expect_seq:
                problems.append({"code": "seq_gap", "index": index, "seq": seq,
                                 "reason": f"序号跳号：期望 {expect_seq}，实际 {seq}"})
            if entry.get("event") == "seal":
                seals += 1
                sealed_head = entry.get("sealed_head")
                sealed_count = entry.get("sealed_count")
                if sealed_head != prev:
                    problems.append({"code": "seal_mismatch", "index": index, "seq": seq,
                                     "reason": f"第 {index + 1} 条封印记的链头与当前链不符"
                                               "（历史被整段改写）"})
                if sealed_count != expect_seq - 1:
                    problems.append({"code": "seal_count_mismatch", "index": index,
                                     "seq": seq,
                                     "reason": f"第 {index + 1} 条封印记的条数与当前不符"})
            head = own if isinstance(own, str) else head
            prev = own if isinstance(own, str) else prev
            expect_seq += 1
        size = self.size()
        file_digest = ""
        if size:
            with self._path.open("rb") as handle:
                file_digest = HASH_PREFIX + hashlib.sha256(handle.read()).hexdigest()
        if expected_head and head != expected_head:
            problems.append({"code": "head_mismatch", "index": count - 1, "seq": None,
                             "reason": "链头与外部锚点不符（历史被改写或被截断）"})
        if expected_count is not None and count != int(expected_count):
            problems.append({"code": "count_mismatch", "index": count, "seq": None,
                             "reason": f"条数与外部锚点不符：期望 {expected_count}，实际 {count}"})
        return {"ok": not problems, "count": count, "head": head,
                "genesis": GENESIS_HASH, "lines": len(raw_lines), "seals": seals,
                "size_bytes": size, "file_sha256": file_digest,
                "path": str(self._path), "problems": problems,
                "checked_at": iso_utc(utc_now())}

    def __getattr__(self, name: str) -> Any:
        """追加式禁令：任何“更新/删除/清空”语义的方法名一律拒绝，而不是当作不存在。"""
        lowered = name.lower()
        if lowered.startswith("__") and lowered.endswith("__"):
            # 协议名（__setstate__ 等）交回标准语义，不挪用追加式禁令去伪装 AttributeError
            raise AttributeError(f"{type(self).__name__} 没有属性 {name}")
        if any(verb in lowered for verb in FORBIDDEN_OPERATIONS):
            raise AuditOperationForbidden(name)
        raise AttributeError(f"{type(self).__name__} 没有属性 {name}")

    def __repr__(self) -> str:
        return f"<AuditLog path={str(self._path)!r} count={self.count()}>"


# --------------------------------------------------------------------------- #
# 导出实现：行 → CSV / 可打印 HTML / reportlab PDF
# --------------------------------------------------------------------------- #

CSV_COLUMNS: tuple[str, ...] = (
    "seq", "ts", "actor_id", "actor_role", "task_id", "action_id", "decision",
    "option_id", "event", "request_id", "original_params", "modified_params",
    "changed_fields", "reason", "client_info", "record_status_before",
    "record_status_after", "mode", "prev_hash", "hash",
)

_HTML_COLUMNS: tuple[tuple[str, str], ...] = (
    ("seq", "#"), ("ts", "时间"), ("actor_id", "谁"), ("actor_role", "身份"),
    ("task_id", "任务"), ("action_id", "动作"), ("decision", "决定"),
    ("original_params", "决定前参数"), ("modified_params", "修改后参数"),
    ("changed_fields", "改了哪些"), ("reason", "理由"), ("client_info", "客户端"),
    ("record_status_after", "之后状态"), ("hash", "哈希"),
)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (Mapping, list, tuple)):
        return canonical_json(value)
    return str(value)


def _changed_text(value: Any) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return _cell_text(value)
    parts: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            parts.append(f"{item.get('name')}:{_cell_text(item.get('before'))}→"
                         f"{_cell_text(item.get('after'))}")
        else:
            parts.append(_cell_text(item))
    return " | ".join(parts)


def entries_to_rows(entries: Sequence[Mapping[str, Any]],
                    columns: Sequence[str] | None = None) -> list[list[str]]:
    """条目 → 文本表格（CSV 与 HTML 共用同一套取值口径）。"""
    cols = tuple(columns) if columns else CSV_COLUMNS
    rows = [[str(c) for c in cols]]
    for entry in entries:
        line: list[str] = []
        for col in cols:
            value = entry.get(col)
            if col == "changed_fields":
                line.append(_changed_text(value))
            else:
                line.append(_cell_text(value))
        rows.append(line)
    return rows


def entries_to_csv(entries: Sequence[Mapping[str, Any]], *,
                   columns: Sequence[str] | None = None, bom: bool = True) -> str:
    """规范 CSV 文本（``\\r\n`` 行尾 + 全引号由 csv 模块决定）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in entries_to_rows(entries, columns=columns):
        writer.writerow(row)
    text = buffer.getvalue()
    return ("\ufeff" + text) if bom else text


DEFAULT_HTML_NOTES: tuple[str, ...] = (
    "本页由 TraceLens 审计日志生成：日志是追加式的，历史条目不可修改、不可删除。",
    "参数已按规则脱敏（密钥类只留短指纹，邮箱/手机号打码，IP 只到网段），不含请求体原文。",
    "「决定」记录的是用户决定做什么，不等于已执行：是否生效以执行端回报的 execution_receipt 为准。",
)


def render_html(rows: Sequence[Mapping[str, Any]], *,
                title: str = "TraceLens 用户操作审计",
                status: Mapping[str, Any] | None = None,
                notes: Sequence[str] | None = None,
                log_path: str | None = None) -> str:
    """可打印 HTML：内联 CSS、A4 横向、零外链、零脚本。"""
    chain = dict(status or {})
    table = entries_to_rows(rows, columns=[c for c, _ in _HTML_COLUMNS])
    labels = [label for _, label in _HTML_COLUMNS]
    ok = bool(chain.get("ok", True))
    notes_list = list(notes) if notes is not None else list(DEFAULT_HTML_NOTES)
    problems = list(chain.get("problems") or [])
    head = str(chain.get("head") or GENESIS_HASH)
    lines: list[str] = []
    lines.append("<!doctype html>")
    lines.append('<html lang="zh-CN">')
    lines.append('<head><meta charset="utf-8">')
    lines.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    lines.append(f"<title>{_html.escape(title)}</title>")
    lines.append("<style>"
                 ":root{color-scheme:light}"
                 "body{margin:16px;font:13px/1.5 \"Microsoft YaHei\",\"PingFang SC\","
                 "system-ui,sans-serif;color:#1c1f24;background:#fff}"
                 "h1{font-size:19px;margin:0 0 4px}"
                 ".meta{color:#5b6472;margin:0 0 10px;font-size:12px}"
                 ".chain{padding:8px 10px;border-radius:6px;margin:0 0 10px;font-size:12px;"
                 "border:1px solid #d7dee8}"
                 ".chain--ok{background:#f2f8f3;border-color:#c3ddc9}"
                 ".chain--bad{background:#fdf2f2;border-color:#e8c4c4;font-weight:600}"
                 "ul.notes{margin:0 0 12px;padding-left:18px;font-size:12px;color:#3d4653}"
                 "table{border-collapse:collapse;width:100%;table-layout:fixed}"
                 "th,td{border:1px solid #d7dee8;padding:4px 6px;text-align:left;"
                 "vertical-align:top;word-break:break-all;font-size:11px}"
                 "th{background:#f4f6f9;position:sticky;top:0}"
                 "tbody tr:nth-child(even){background:#fafbfd}"
                 "code{font-family:ui-monospace,Consolas,monospace;font-size:11px}"
                 "footer{margin-top:12px;font-size:11px;color:#5b6472}"
                 "@page{size:A4 landscape;margin:10mm}"
                 "@media print{body{margin:0;font-size:10px}.chain{print-color-adjust:exact}}"
                 "</style></head><body>")
    lines.append(f"<h1>{_html.escape(title)}</h1>")
    lines.append(f'<p class="meta">生成时间 {iso_utc(utc_now())} · 条目 {chain.get("count", len(rows))}'
                 f' · 日志文件 <code>{_html.escape(str(log_path or ""))}</code></p>')
    cls = "chain chain--ok" if ok else "chain chain--bad"
    if ok:
        lines.append(f'<p class="{cls}">哈希链校验：<strong>通过</strong>（{chain.get("count", 0)} 条，'
                     f'封印 {chain.get("seals", 0)} 次）· 链头 <code>{_html.escape(head)}</code></p>')
    else:
        detail = "；".join(str(p.get("reason")) for p in problems[:5])
        lines.append(f'<p class="{cls}">哈希链校验：<strong>失败</strong> — '
                     f'{_html.escape(detail)}。请勿把这份导出当作可信记录，'
                     f'先按 t11 的取证流程核对原始文件。</p>')
    lines.append('<ul class="notes">')
    for note in notes_list:
        lines.append(f"<li>{_html.escape(str(note))}</li>")
    lines.append("</ul>")
    lines.append("<table><thead><tr>")
    for label in labels:
        lines.append(f"<th>{_html.escape(label)}</th>")
    lines.append("</tr></thead><tbody>")
    for row in table[1:]:
        lines.append("<tr>")
        for cell in row:
            lines.append(f"<td>{_html.escape(cell)}</td>")
        lines.append("</tr>")
    lines.append("</tbody></table>")
    lines.append(f'<footer>链头 <code>{_html.escape(head)}</code> · 文件哈希 '
                 f'<code>{_html.escape(str(chain.get("file_sha256") or ""))}</code> · '
                 f'审计 schema {AUDIT_SCHEMA_VERSION} · 本页零外链零脚本，Ctrl+P 可存 PDF</footer>')
    lines.append("</body></html>")
    return "\n".join(lines)


def _load_reportlab() -> tuple[Mapping[str, Any] | None, str | None]:
    """惰性导入 reportlab；任何失败都当作“不可用”，并带上原因。"""
    try:
        namespace = {
            "pagesizes": importlib.import_module("reportlab.lib.pagesizes"),
            "platypus": importlib.import_module("reportlab.platypus"),
            "styles": importlib.import_module("reportlab.lib.styles"),
            "units": importlib.import_module("reportlab.lib.units"),
            "pdfbase": importlib.import_module("reportlab.pdfbase"),
            "cidfonts": importlib.import_module("reportlab.pdfbase.cidfonts"),
        }
    except Exception as exc:                        # ImportError 之外的沙箱拒绝也算
        return None, f"reportlab_unavailable:{type(exc).__name__}"
    return namespace, None


def _register_pdf_font(namespace: Mapping[str, Any]) -> tuple[str, str]:
    """尝试注册中文 CID 字体；失败就退回 Helvetica 并说明可能丢字。"""
    pdfbase = namespace["pdfbase"]
    cidfonts = namespace["cidfonts"]
    for name in ("STSong-Light", "STSongStd-Light", "HeiseiMin-W3"):
        try:
            cidfonts.UnicodeCIDFont(name)
            pdfbase.registerFont(name)
            return name, f"注册 CJK 字体 {name}"
        except Exception:
            continue
    return "Helvetica", "未找到可用 CJK 字体：中文可能被丢弃，建议改用可打印 HTML"


def _build_pdf(namespace: Mapping[str, Any], target: Any,
               entries: Sequence[Mapping[str, Any]], *, title: str,
               status: Mapping[str, Any], notes: Sequence[str] | None = None) -> dict[str, Any]:
    """用 reportlab 排一份 A4 横向表格；调用方负责回退。

    ``target`` 可以是路径（写文件）也可以是可写字节流（HTTP 直接回字节，不落盘）。
    """
    platypus = namespace["platypus"]
    pagesizes = namespace["pagesizes"]
    styles = namespace["styles"]
    units = namespace["units"]
    font, font_note = _register_pdf_font(namespace)
    sheet = styles.getSampleStyleSheet()
    title_style = sheet["Title"] if hasattr(sheet, "__getitem__") else getattr(sheet, "Title")
    body_style = sheet["Normal"] if hasattr(sheet, "__getitem__") else getattr(sheet, "Normal")
    table = entries_to_rows(entries, columns=[c for c, _ in _HTML_COLUMNS])
    data = [[label for _, label in _HTML_COLUMNS]] + table[1:]
    sink = str(target) if isinstance(target, (str, pathlib.Path)) else target
    doc = platypus.SimpleDocTemplate(
        sink, pagesize=pagesizes.landscape(pagesizes.A4),
        title=title, author=APP_ID, subject="audit",
    )
    flow: list[Any] = [platypus.Paragraph(title, title_style),
                       platypus.Paragraph(
                           f"条目 {status.get('count', len(entries))} · 链头 "
                           f"{status.get('head', GENESIS_HASH)} · 哈希链校验 "
                           f"{'通过' if status.get('ok', True) else '失败（见 HTML 说明）'}",
                           body_style),
                       platypus.Paragraph(font_note, body_style)]
    for note in (notes if notes is not None else DEFAULT_HTML_NOTES):
        flow.append(platypus.Paragraph(str(note), body_style))
    flow.append(platypus.Spacer(1, 4 * units.mm))
    grid = platypus.Table(data, repeatRows=1)
    grid.setStyle(platypus.TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, (0.84, 0.87, 0.91)),
        ("BACKGROUND", (0, 0), (-1, 0), (0.95, 0.96, 0.98)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flow.append(grid)
    doc.build(flow)
    return {"font": font, "font_note": font_note, "rows": len(data),
            "path": str(target)}


# --------------------------------------------------------------------------- #
# 入口与便利函数
# --------------------------------------------------------------------------- #


def render_pdf_bytes(entries: Sequence[Mapping[str, Any]], *,
                     title: str = "TraceLens 用户操作审计",
                     status: Mapping[str, Any] | None = None,
                     notes: Sequence[str] | None = None) -> tuple[bytes, dict[str, Any]]:
    """在内存里排 PDF 并返回 ``(字节, 说明)``；reportlab 不可用抛 :class:`AuditExportError`。

    HTTP 层要直接回一份 PDF，不能让服务自己往磁盘写文件（server.py 零文件写入是
    t13/t14 钉住的纯度），所以这里只吃内存流。调用方拿到
    :class:`AuditExportError` 就按规格退回可打印 HTML 并说明原因。
    """
    namespace, reason = _load_reportlab()
    if namespace is None:
        raise AuditExportError("AUDIT_REPORTLAB_UNAVAILABLE",
                               "PDF 导出需要 reportlab，当前不可导入",
                               hint="pip install reportlab，或改用 format=html 打印后存 PDF",
                               detail={"reason": reason})
    buffer = io.BytesIO()
    payload = _build_pdf(namespace, buffer, list(entries), title=title,
                         status=dict(status or {}), notes=notes)
    payload.pop("path", None)
    payload["rows"] = max(0, int(payload.get("rows", 1)) - 1)
    data = buffer.getvalue()
    if not data:
        raise AuditExportError("AUDIT_PDF_EMPTY", "PDF 构建结果为空",
                               hint="改用 format=html 打印后存 PDF",
                               detail={"reason": payload.get("font_note", "")})
    return data, payload


def default_log_path(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """默认落盘位置：``<package 上级>/var/audit.jsonl``（与演示数据同仓，不写出包外）。"""
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    return base / "var" / AUDIT_FILE_NAME


def default_export_path(ext: str = "csv", root: pathlib.Path | str | None = None) -> pathlib.Path:
    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    stamp = iso_utc(utc_now()).replace("-", "").replace(":", "").replace("T", "-")
    suffix = ext if ext.startswith(".") else "." + ext
    return base / "var" / f"audit-{stamp}{suffix}"


def open_log(path: pathlib.Path | str | None = None, *,
             redact: bool = True, ip_scope: str = "subnet",
             verify_before_append: bool = True) -> AuditLog:
    """产品入口：``open_log()`` 拿到默认审计日志句柄（追加式）。"""
    return AuditLog(path or default_log_path(), redact=redact, ip_scope=ip_scope,
                    verify_before_append=verify_before_append)


def verify_file(path: pathlib.Path | str, *, expected_head: str | None = None,
                expected_count: int | None = None) -> dict[str, Any]:
    """只校验一份已有日志（审计页/取证脚本入口，不写任何东西）。"""
    return AuditLog(path, verify_before_append=False).verify(
        raise_on_tamper=False, expected_head=expected_head, expected_count=expected_count)


def __getattr__(name: str) -> Any:
    """模块级也守追加式禁令：``audit.delete_log(...)`` 这类名字一律拒绝。"""
    lowered = name.lower()
    if lowered.startswith("__") and lowered.endswith("__"):
        raise AttributeError(f"module {__name__} 没有属性 {name}")
    if any(verb in lowered for verb in FORBIDDEN_OPERATIONS):
        raise AuditOperationForbidden(name)
    raise AttributeError(f"module {__name__} 没有属性 {name}")


if __name__ == "__main__":  # 手工自检：写三条 → 校验 → 打印链头（用完即清，不留演示文件）
    scratch = default_export_path("selfcheck-audit.jsonl")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    demo = AuditLog(scratch)
    demo.append(build_event("note", actor_id="local-user", note="自检起点"))
    demo.append(build_event("decision", actor_id="alice", action_id="act-demo-0004",
                            decision="approve", reason="自检：同意"))
    demo.append(build_event("decision", actor_id="bob", action_id="act-demo-0005",
                            decision="reject", reason="自检：拒绝",
                            client_info={"channel": "web", "ip": "203.0.113.7",
                                         "user_agent": "Mozilla/5.0"}))
    report = demo.verify()
    print(canonical_json({"count": report["count"], "ok": report["ok"],
                          "head": report["head"], "actors": demo.stats()["actors"]}))
    os.remove(scratch)   # 删的是本次自检的临时文件，不是审计记录（审计记录永不删）
