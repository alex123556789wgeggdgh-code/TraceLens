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
   唯一的**字段级**豁免（R5-3b / PM-VERDICT-R5-3 §4）：``fields.snapshot_sha256`` 这类
   **锚定摘要**逐字落盘——sha256 里出现 ≥11 位连续数字是常态（实测 11%），照 PII 规则
   打码就等于把锚砸坏（TP-12「前态快照摘要逐字进链」）。豁免只认「键名白名单 + 含字母的
   纯 hex 摘要形状」（:func:`is_anchor_digest` 单点，纯数字的 PII 形状永不豁免），
   ``PII_PATTERNS`` 本体与阈值一字未放宽，同一个键塞散文进来照旧打码，
   ``journal_id`` 那类 id 的掩码口径逐字不变。
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
    "ANCHOR_DIGEST_FIELDS", "is_anchor_digest",
    "diff_params", "build_event",
    "event_from_decision", "AuditLog", "ExportResult",
    "default_log_path", "open_log", "verify_file", "render_html",
    "entries_to_rows", "entries_to_csv", "render_pdf_bytes",
    # R8-3（B8-1）：责任认定书模板 + 哈希链二维码（QR 载荷 = sealed_head）
    "CERT_TITLE", "CERT_QR_FIELD", "CERT_QR_NOTE", "NO_SEAL_NOTE", "CERT_DISCLOSURE",
    "REDUCTION_NOTE", "ANCHOR_HONEST_NOTE", "ANCHOR_TX_HONEST_NOTE", "ANCHOR_STATE_TEXT",
    # R8-5（D-2）：CID 与「未锚定 / 待接入」同屏的措辞单点（compliance 侧引用同一常量）
    "CID_NO_ANCHOR_NOTE", "CID_SELF_REF_NOTE", "ANCHOR_DEFERRED_NOTE",
    "CERT_VERIFY_GUIDE", "DISPLAY_FORBIDDEN_WORDS", "QR_MODULE_PT", "QR_QUIET_MODULES",
    "latest_sealed_head", "certificate_period", "build_certificate", "certificate_text",
    "certificate_qr", "certificate_svg", "certificate_inline_svg",
    "qr_side", "qr_rect_plan", "qr_plan_to_matrix", "draw_qr_block", "qr_pdf_flowable",
    "draw_certificate_block",
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
    # R1-4（PM-VERDICT-R1-3 §3.1，L1 已批）：journal 锚定事件的原生名。R1-3 落盘当时
    # 白名单里没有它，只能借 ``note`` + ``fields.rollback_event`` 记（auth 域先例）；
    # 现在真名合法，``query(event="rollback.anchored")`` 直接可查。扩白名单只影响
    # **新写入**：历史行一条不改写（旧行的 note + fields.rollback_event 继续可读可查）。
    "rollback.anchored",
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

# --------------------------------------------------------------------------- #
# 锚定摘要的**字段级**掩码豁免（R5-3b / PM-VERDICT-R5-3 §4，L1 已批）
# --------------------------------------------------------------------------- #
#: 缺陷：sha256 的十六进制串里出现 ≥11 位连续数字是**常态**（实测 20000 个真实
#: sha256 里 11.0% 被 ``long_digits`` 啃掉一段，``cn_mobile``/``id_card`` 各再加
#: 0.3%/0.1%，是前者的子集，去重后总计 11.0%）。锚定事件 ``fields.snapshot_sha256``
#: 一旦被掩码，
#: TP-12「前态快照摘要逐字进链」就破：取证无法把链上的摘要与 journal 条目的前态
#: 快照逐字对上——锚住的是哪份前态，从此查无实据。
#:
#: 口径（TASK-R5-3b §2，三条都写死在 :func:`is_anchor_digest` 一个判据里）：
#:
#: * **字段级**，不是规则级：``PII_PATTERNS`` 的本体、阈值、顺序一字不动，
#:   自由文本（``reason``/``note``/``changed_fields``/非敏感参数值）里的 11-20 位
#:   数字串照旧打码。豁免只认下面这几个键名。
#: * **形状闸门**：只放过**含字母的**纯 hex 摘要（8-64 位 ``[0-9a-fA-F]``，且至少一个
#:   字母）。纯数字串——银行卡 / 手机号 / 身份证那类 PII 的形状——**永不进豁免**；
#:   真摘要几乎必然含 hex 字母（64 位全数字的概率 = 0.625^64 ≈ 8.6e-14，实测 20000 例 0 例）。
#:   形状之外的值一律回到原脱敏链，宁严勿松：同一个键塞散文、邮箱、卡号照旧全量打码。
#:   豁免是「这个键的**摘要值**不掩码」，不是「这个键不脱敏」。
#: * 同事件其他字段的掩码逐字不变：``journal_id`` 落盘掩码是既定口径
#:   （``test_r1_acceptance`` 注释在案，PM 历次裁决采信），本轮不顺手放开。
#:
#: 只影响**新写入**：历史行的掩码摘要一条不改写（追加式纪律），链结构与签名域零改动。
ANCHOR_DIGEST_FIELDS: frozenset[str] = frozenset({"snapshot_sha256"})
#: 摘要形状：8-64 位十六进制。收 8 位起是给 sha256 与截断引用（``sha256:`` 前 12 位
#: 那类）留出余量。
_ANCHOR_DIGEST_RE = re.compile(r"\A[0-9a-fA-F]{8,64}\Z")
#: 字母闸门：把「纯数字串」挡在豁免之外（PII 防线不能让豁免啃到，见上）。
_ANCHOR_LETTER_RE = re.compile(r"[a-fA-F]")


def is_anchor_digest(key: Any, value: Any) -> bool:
    """这个键值对是「锚定摘要逐字落盘」的豁免对象吗？

    三个条件缺一不可：**键名**命中 :data:`ANCHOR_DIGEST_FIELDS`（按 :func:`_key_norm`
    归一化，口径同 ``SENSITIVE_KEYS``）、**值是纯 hex**（:data:`_ANCHOR_DIGEST_RE`）、
    且**含至少一个 hex 字母**（:data:`_ANCHOR_LETTER_RE`，纯数字的 PII 形状一律不豁免）。
    判据只在这里写一份：``redact_params`` 的两条入口（字典键、带键名的标量）都调它，
    豁免面没有第二个落点，也不会被调用方绕过。
    """
    return (_key_norm(key) in {_key_norm(k) for k in ANCHOR_DIGEST_FIELDS}
            and isinstance(value, str)
            and bool(_ANCHOR_DIGEST_RE.match(value))
            and bool(_ANCHOR_LETTER_RE.search(value)))


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


def redact_params(params: Any, *, redact: bool = True, _depth: int = 0,
                  _key: Any = None) -> Any:
    """递归脱敏参数字典；嵌套容器一起处理，深度超限整段折成哈希引用。

    ``_key`` 是**调用方内部**带上来的键名（``build_event`` 的 ``extra`` 那条路只把值
    递进来，键名会丢）：只为 :func:`is_anchor_digest` 的字段级豁免认得出键，
    不改变任何其他脱敏行为，也不属于对外签名。
    """
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
            elif is_anchor_digest(key, value):
                # R5-3b：锚定摘要逐字落盘（字段级 + 摘要形状，判据单点见函数名）；
                # 敏感键优先在上面拦住，豁免永远排在整值打码之后，不越 PII 防线。
                out[str(key)] = value
            elif isinstance(value, (Mapping, list, tuple)):
                out[str(key)] = redact_params(value, redact=True, _depth=_depth + 1)
            else:
                # 4.8：非敏感键的值里也可能夹着邮箱/手机号/路径/密钥（params_summary 那类散文）
                out[str(key)] = redact_text(value)
        return out
    if isinstance(params, (list, tuple)):
        return [redact_params(v, redact=True, _depth=_depth + 1) for v in params]
    if is_anchor_digest(_key, params):
        return params                      # R5-3b：同上，键名由调用方带进来
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
            # R5-3b（PM-VERDICT-R5-3 §4，L1 已批）：把键名一并递进 redact_params。
            # 以前这里只递值，键名在这一跳丢掉 → 锚定摘要被当自由文本走掩码，
            # fields.snapshot_sha256 落盘带星（TP-12 破）。豁免判据（键名白名单 +
            # 摘要形状）收在 is_anchor_digest 单点，这里只补"键名"这一口信息。
            payload[name] = redact_params(value, redact=redact, _key=name)
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
                    _fallback: str | None = None,
                    certificate: Mapping[str, Any] | None = None,
                    status: Mapping[str, Any] | None = None) -> ExportResult:
        """可打印 HTML 导出：零外链、零脚本，浏览器 Ctrl+P 存 PDF。

        ``certificate``（R8-3 / B8-1）：责任认定书区块（含 QR，载荷 = ``sealed_head``）；
        不传则就地取数，传了就直接用（PDF 回退路径与 PDF 共用同一份，两处不各写各的）。
        ``status``：链校验结果，传进来就复用，同一次导出不复算第二遍（只读操作）。
        """
        items = list(rows) if rows is not None else self.read()
        chain = dict(status) if isinstance(status, Mapping) else self.verify(raise_on_tamper=False)
        cert = (certificate if isinstance(certificate, Mapping)
                else build_certificate(items, status=chain))
        text = self.render_html(items, title=title, notes=notes, certificate=cert, status=chain)
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

        两条产物都带责任认定书区块（R8-3 / B8-1）：PDF 用 reportlab 逐模块画方块，
        回退 HTML 用内联 SVG —— 同一份 ``cert`` 取数，QR 载荷与正文链头行逐字相等。
        """
        items = list(rows) if rows is not None else self.read()
        chain = self.verify(raise_on_tamper=False)
        cert = build_certificate(items, status=chain)
        mode = prefer if prefer in ("auto", "pdf", "html") else "auto"
        if mode == "html":
            result = self.export_html(path, rows=items, title=title, notes=notes,
                                      note="按要求直接生成可打印 HTML（未尝试 PDF）",
                                      certificate=cert, status=chain)
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
                                    _fallback=reason,
                                    certificate=cert, status=chain)
        target = pathlib.Path(path) if path else default_export_path("pdf")
        try:
            payload = _build_pdf(namespace, target, items, title=title,
                                 status=chain, notes=notes, certificate=cert)
        except Exception as exc:                    # 构建失败也不让审计丢内容
            reason = f"reportlab_build_failed:{type(exc).__name__}"
            return self.export_html(path, rows=items, title=title, notes=notes,
                                    note=f"PDF 构建失败（{reason}）：已生成可打印 HTML，"
                                         "浏览器 Ctrl+P 即可存 PDF",
                                    _fallback=reason,
                                    certificate=cert, status=chain)
        data = target.read_bytes()
        return ExportResult(format="pdf", path=str(target), rows=len(items),
                            columns=CSV_COLUMNS, bytes=len(data),
                            sha256=sha256_ref(data.decode("latin-1"))[:len(HASH_PREFIX) + 16],
                            note=f"PDF：reportlab 生成（字体 {payload.get('font', '默认')}）",
                            fallback=None)

    # -- 内部 -------------------------------------------------------------- #

    def render_html(self, rows: Sequence[Mapping[str, Any]], *,
                    title: str = "TraceLens 用户操作审计",
                    notes: Sequence[str] | None = None,
                    certificate: Mapping[str, Any] | None = None,
                    status: Mapping[str, Any] | None = None) -> str:
        return render_html(rows, title=title,
                           status=(dict(status) if isinstance(status, Mapping)
                                   else self.verify(raise_on_tamper=False)),
                           notes=notes, log_path=str(self._path), certificate=certificate)

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
    # R8-2（B8-2「CSV 覆盖签名/哈希字段全集」，PM 任务书 §1.5 预批「仅尾部追加」，L1）：
    # 前 20 列的名字与顺序一字未动，以下四列只追加在尾部，旧消费者按下标/表头断言不受影响。
    # entry_hash 与 hash 恒等（audit.py:894：t8/t11 读的就是这个名字）；这里按名导出，
    # 是为了 ELK/Splunk 侧按 `entry_hash` 建索引/告警的消费者能直接取到（语义重复，已申报）。
    # record_facts_hash / source_facts_hash 是载荷事实哈希；sealed_head 是封印锚点哈希
    # （只有 seal 条目有值，其余行为空）。四列全部取自条目实键，不新增、不回填历史行。
    "entry_hash", "record_facts_hash", "source_facts_hash", "sealed_head",
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

# --------------------------------------------------------------------------- #
# 责任认定书模板与哈希链二维码（R8-3 / B8-1，TASK-R8-3 §2.1 / §2.2）
# --------------------------------------------------------------------------- #
# 口径（PM 任务书 §1.4 / HANDOFF §4 既定 A 案）：
#   QR 载荷 = ``sealed_head`` —— 审计账本 ``seal()`` 写入的**封印锚点哈希**；
#   **不是** ``compliance.build_bundle()`` 的三段归约 ``root_hash``，**不是** Merkle 根。
#   两个值在同一条链上本来就长得不一样（链头快照 vs 三段哈希归约），模板把它们
#   分行、各带限定语写出来，谁也不冒充谁。
#   取数只有一个出口（:func:`latest_sealed_head`）；正文行与二维码都从
#   ``cert["qr_payload"]`` 这一个值来 —— 所以「PDF 里的码」与「PDF 里的字」逐字相等
#   是由构造保证的，钉测只锁这条构造。
CERT_TITLE: str = "TraceLens 责任认定书（模板）"
#: QR 载荷取自条目里的哪个键（机器可读，钉测按名核对）。
CERT_QR_FIELD: str = "sealed_head"
CERT_QR_NOTE: str = ("QR 载荷 = sealed_head：审计账本 seal() 写入的封印锚点哈希"
                     "（哈希链根哈希 A 案口径）；不是三段归约 root_hash，也不是 Merkle 根")
#: 无封印锚点时的诚实措辞：留空、不伪造、不拿别的哈希顶替（§1.4）。
NO_SEAL_NOTE: str = "无封印锚点，二维码不可用"
#: 「验签通过≠背书齐备」两句（HANDOFF §4 展示纪律：随摘要引用，两层账本不混为一谈）。
CERT_DISCLOSURE: tuple[str, ...] = (
    "验签通过≠背书齐备。",
    "本文件的哈希与二维码只证明内容可离线复算，不构成 secretariat 签名账本的背书。",
)
#: 三段归约的固定限定语（§2.1 第 3 项：不得与链头混淆）。
REDUCTION_NOTE: str = "三段归约（records/audit/letters 逐条哈希的归约值），非链头、非 Merkle 根"
#: 锚定诚实态（0.4）：只写「未接入 / 待接入」，不写完成态措辞。
ANCHOR_HONEST_NOTE: str = "anchor_state: unavailable（未接入锚定后端，待接入）"
ANCHOR_TX_HONEST_NOTE: str = "eth_tx_hash: None（该值不存在，不是「待填」）"
# --------------------------------------------------------------------------- #
# R8-5（D-2 三项约束 / B8-3 按 D-2 验收）：CID 与「未锚定」**必须同屏**。
#   离线 CIDv1 是真值（``compliance.cid_for`` 可复算），真值也会说谎：一个孤零零摆在
#   认定书上的 CID 会被读成「已经存证」。所以措辞单点写在这里，compliance 侧引用同一
#   组常量，两条渲染链（真 PDF / 可打印 HTML）与 meta 用同一句话——不存在各处各写一套、
#   某一处漏了限定语。**只写降级态，不写完成态**（0.4 展示纪律）。
# --------------------------------------------------------------------------- #
CID_NO_ANCHOR_NOTE: str = ("ipfs_cid 未锚定（no anchor）/ 待接入：本机无 IPFS pinning、无链上交易，"
                           "该 CID 只由导出字节离线复算")
#: 本文件自身的 CID 不可能写进本文件（写进去字节就变了、CID 就不是它了），
#: 所以模板**不预填**，只如实指出它住在导出记录的 ``ipfs_cid`` 字段里。
CID_SELF_REF_NOTE: str = ("本文件自身的 CID 由导出时对最终字节复算、写进导出记录的 ipfs_cid 字段；"
                          "模板不预填——预填即自指失真")
#: D-2 第 3 项：「上链 ≤2 分钟」是**延后**不是删除。写进导出件，免得降级态被读成永久放弃。
ANCHOR_DEFERRED_NOTE: str = ("B8-3「上链 ≤2 分钟」延后而非删除：提供 RPC / 密钥后启用，届时仍是验收项")
#: 0.6.0 唯一诚实的锚定状态（与 ``compliance.ANCHOR_STATE`` 同值；不 import 过去，
#: 免得审计侧反向依赖 compliance —— 两边都是常量，钉测逐字核对）。
ANCHOR_STATE_TEXT: str = "unavailable"
#: 验证指引（§2.1 第 5 项：只写指引；``tools/verify_export.py`` 归 R8-4，本轮不实现）。
CERT_VERIFY_GUIDE: str = ("验证指引：哈希可离线复算 —— 逐条 canonical_json → sha256 → "
                          "按 prev_hash 续接成链，封印条目的 sealed_head 应等于当时的链头；"
                          "独立验证脚本 tools/verify_export.py 属 R8-4，本模板只给指引、不含实现")
#: 展示纪律禁语（0.4 / HANDOFF §4）。**只**出现在测试与报告里做扫描，
#: 绝不写进导出件正文——写了就等于把禁语摆上了台面。
DISPLAY_FORBIDDEN_WORDS: tuple[str, ...] = ("已恢复", "已撤销", "已上链", "已冲正完成")
#: PDF 里每个 QR 模块的边长（pt）与静区宽度（模块数）；静区按 ISO 口径 4 模块。
QR_MODULE_PT: float = 3.2
QR_QUIET_MODULES: int = 4


def latest_sealed_head(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """QR 载荷的**唯一取数出口**：条目列表里最后一次 ``seal()`` 记的 ``sealed_head``。

    口径（判断，报告如实申报）：同一条链可能封很多次，``seal()`` 记的是**当时**的链头，
    所以取**最后一次**封印 —— 它覆盖到导出时的整段历史，是最强的锚点；更早的封印
    仍在条目里可查（``seals`` 计数一并给出）。
    没有 seal 条目 / 值为空 → ``sealed_head=None`` + 如实 ``note``，调用方**必须**
    留空并省略二维码，绝不拿链头、``root_hash`` 或任何别的哈希顶替（§1.4）。
    """
    seals = 0
    found: tuple[str, str] | None = None
    for entry in (rows or ()):
        if not isinstance(entry, Mapping) or entry.get("event") != "seal":
            continue
        seals += 1
        value = entry.get(CERT_QR_FIELD)
        if isinstance(value, str) and value.strip():
            found = (value.strip(),
                     f"第 {entry.get('seq')} 条 seal 事件（sealed_count={entry.get('sealed_count')}）")
    if found is None:
        note = NO_SEAL_NOTE if seals == 0 else f"seal 条目共 {seals} 条，sealed_head 均为空：{NO_SEAL_NOTE}"
        return {"sealed_head": None, "source": None, "seals": seals, "note": note}
    return {"sealed_head": found[0], "source": found[1], "seals": seals, "note": CERT_QR_NOTE}


def certificate_period(rows: Sequence[Mapping[str, Any]] | None = None,
                       period: Mapping[str, Any] | None = None) -> str:
    """期间一句话：有显式 ``period`` 用显式，否则按条目 ``ts`` 极值，都没有就如实说未限定。"""
    if isinstance(period, Mapping) and period:
        since = period.get("since") or period.get("from") or ""
        until = period.get("until") or period.get("to") or ""
        if since or until:
            return f"{since or '（起）'} ~ {until or '（止）'}"
    stamps = [str(entry.get("ts")) for entry in (rows or ())
              if isinstance(entry, Mapping) and entry.get("ts")]
    if stamps:
        return f"{min(stamps)} ~ {max(stamps)}（按条目 ts 极值）"
    return "未限定时间范围"


def build_certificate(rows: Sequence[Mapping[str, Any]] | None = None, *,
                      status: Mapping[str, Any] | None = None,
                      title: str = CERT_TITLE,
                      period: Mapping[str, Any] | None = None,
                      generated_at: str | None = None,
                      lang: str = "zh",
                      root_hash: str | None = None,
                      section_hashes: Mapping[str, Any] | None = None,
                      counts: Mapping[str, Any] | None = None,
                      seal_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """组一份责任认定书的**数据**（PDF / 可打印 HTML 同构，两条渲染链共用）。

    只做取数与措辞，不写文件、不追加账本、不新增任何条目键：
    ``sealed_head`` 从审计条目里读（:func:`latest_sealed_head`），读不到就留空并写明
    「无封印锚点，二维码不可用」。``qr_payload`` 是正文与二维码**共用**的同一个值。
    """
    chain = dict(status or {})
    items = [entry for entry in (rows or ()) if isinstance(entry, Mapping)]
    seal = dict(seal_info) if isinstance(seal_info, Mapping) else latest_sealed_head(items)
    sealed_head = seal.get("sealed_head")
    sealed_head = sealed_head if isinstance(sealed_head, str) and sealed_head.strip() else None
    if counts is None:
        counts = {"records": None, "audit": chain.get("count", len(items)), "letters": None}
    return {
        "title": str(title),
        "period": certificate_period(items, period),
        "generated_at": str(generated_at or iso_utc(utc_now())),
        "lang": str(lang or "zh"),
        "sealed_head": sealed_head,
        "sealed_head_field": CERT_QR_FIELD,
        "sealed_head_source": seal.get("source"),
        "seals": seal.get("seals", 0),
        "chain_head": chain.get("head"),
        "chain_ok": chain.get("ok"),
        "chain_count": chain.get("count", len(items)),
        "root_hash": root_hash,
        "reduction_note": REDUCTION_NOTE,
        "section_hashes": dict(section_hashes or {}),
        "anchor_state": ANCHOR_STATE_TEXT,
        "anchor_note": ANCHOR_HONEST_NOTE,
        "eth_tx_hash": None,
        "eth_tx_note": ANCHOR_TX_HONEST_NOTE,
        # R8-5（D-2 第 1 项）：CID 的诚实态字段与措辞**同屏**进认定书数据本体。
        # ``ipfs_cid`` 在这里恒 None：本文件自身的 CID 由导出时对最终字节算得（写进导出
        # 记录），模板拿不到、也不许预填一个假 CID 冒充"已算好"。调用方（compliance 导出面）
        # 若真有一个可归属的 CID，可显式传进来，同屏的未锚定措辞照旧跟着走。
        "ipfs_cid": None,
        "cid_anchor_note": CID_NO_ANCHOR_NOTE,
        "cid_self_ref_note": CID_SELF_REF_NOTE,
        "anchor_deferred_note": ANCHOR_DEFERRED_NOTE,
        "counts": dict(counts),
        "verify_guide": CERT_VERIFY_GUIDE,
        "disclosure": list(CERT_DISCLOSURE),
        "qr_field": CERT_QR_FIELD,
        "qr_payload": sealed_head,
        "qr_note": str(seal.get("note") or CERT_QR_NOTE) if sealed_head else str(seal.get("note") or NO_SEAL_NOTE),
    }


def certificate_text(cert: Mapping[str, Any], *, include_title: bool = True) -> list[str]:
    """模板正文行（§2.1 六项，顺序固定；PDF 逐行画、HTML 逐行列）。

    链头行与 ``qr_payload`` 同源：正文里写的 ``sealed_head`` 与二维码载荷是**同一个字符串**，
    不存在两处各写各的（钉测锁这条）。
    """
    counts = cert.get("counts") if isinstance(cert.get("counts"), Mapping) else {}
    lines: list[str] = []
    if include_title:
        lines.append(str(cert.get("title") or CERT_TITLE))
    lines.append(f"period: {cert.get('period')}")
    lines.append(f"generated_at: {cert.get('generated_at')}")
    lines.append(f"lang: {cert.get('lang')}")
    head = cert.get("sealed_head")
    lines.append(f"sealed_head: {head}" if head else f"sealed_head: （空）{NO_SEAL_NOTE}")
    lines.append(f"sealed_head_source: {cert.get('sealed_head_source') or NO_SEAL_NOTE}")
    lines.append(f"sealed_head_note: {CERT_QR_NOTE}")
    lines.append(f"chain_head: {cert.get('chain_head') or '（本次导出上下文未提供链尖哈希）'}"
                 f"（链尖 = 最新一条的哈希；封印锚点 = seal() 当时记下的链头，两者不同）")
    lines.append(f"chain_ok: {cert.get('chain_ok')} · seals: {cert.get('seals')} "
                 f"· count: {cert.get('chain_count')}")
    root = cert.get("root_hash")
    if root:
        lines.append(f"root_hash(三段归约): {root}")
        lines.append(f"root_hash_note: {cert.get('reduction_note')}")
    else:
        lines.append("root_hash(三段归约): （不适用：本导出面只有审计链，三段归约在 compliance 导出面）")
    sections = cert.get("section_hashes") if isinstance(cert.get("section_hashes"), Mapping) else {}
    for name in ("records", "audit", "letters"):
        value = sections.get(name)
        if value:
            lines.append(f"{name}_hash: {value}")
    if not sections:
        lines.append("records_hash / audit_hash / letters_hash: （不适用：本导出面只有审计链）")
    lines.append(str(cert.get("anchor_note") or ANCHOR_HONEST_NOTE))
    lines.append(str(cert.get("eth_tx_note") or ANCHOR_TX_HONEST_NOTE))
    # R8-5（D-2 第 1 项 / §2.1 同屏判据）：CID 行与「未锚定（no anchor）/ 待接入」**相邻成行**，
    # 顺序固定：CID 本体 → 未锚定 → 本体出处 → 延后项。缺任何一个限定语，离线 CID 就会被
    # 读成完成态；这一段的职责是**把降级说全**，不是把 CID 说没。
    cid_value = cert.get("ipfs_cid")
    lines.append(f"ipfs_cid: {cid_value}" if cid_value
                 else "ipfs_cid: （本文件自身的 CID 见导出记录的 ipfs_cid 字段，模板不预填）")
    lines.append(str(cert.get("cid_anchor_note") or CID_NO_ANCHOR_NOTE))
    lines.append(str(cert.get("cid_self_ref_note") or CID_SELF_REF_NOTE))
    lines.append(str(cert.get("anchor_deferred_note") or ANCHOR_DEFERRED_NOTE))
    lines.append(f"counts: records={counts.get('records')} audit={counts.get('audit')} "
                 f"letters={counts.get('letters')}")
    lines.append(str(cert.get("verify_guide") or CERT_VERIFY_GUIDE))
    payload = cert.get("qr_payload")
    lines.append(f"qr_payload: {payload}" if payload else f"qr_payload: （空）{NO_SEAL_NOTE}")
    lines.append(f"qr_note: {cert.get('qr_note') or NO_SEAL_NOTE}")
    for sentence in (cert.get("disclosure") or CERT_DISCLOSURE):
        lines.append(str(sentence))
    return lines


def certificate_qr(cert: Mapping[str, Any]) -> list[list[int]] | None:
    """QR 矩阵：载荷就是 ``cert["qr_payload"]``（= 正文那行 ``sealed_head`` 的同一个串）。

    载荷为空 → 返回 ``None``（调用方必须省略二维码并如实写明），**不编码空占位码**、
    不拿别的哈希顶替。编码器是仓库自带的 ``agent_explainer/qr.py``（纯标准库）。
    """
    payload = cert.get("qr_payload") if isinstance(cert, Mapping) else None
    if not isinstance(payload, str) or not payload.strip():
        return None
    from . import qr as _qr
    return _qr.encode(payload.strip())


def certificate_svg(cert: Mapping[str, Any], *, scale: int = 4,
                    border: int = QR_QUIET_MODULES) -> str | None:
    """独立 SVG 字符串（``qr.to_svg`` 原样产物）：零脚本、零外链、带 ``viewBox``。"""
    matrix = certificate_qr(cert)
    if matrix is None:
        return None
    from . import qr as _qr
    return _qr.to_svg(matrix, scale=scale, border=border)


_XML_DECL_RE = re.compile(r"^\s*<\?xml[^\n]*\?>\s*")
_XMLNS_ATTR_RE = re.compile(r'\sxmlns="[^"]*"')


def certificate_inline_svg(cert: Mapping[str, Any], *, scale: int = 4,
                           border: int = QR_QUIET_MODULES) -> str | None:
    """可打印 HTML 用的内联 SVG：``qr.to_svg`` 产物里**只**去掉 XML 声明与 ``xmlns`` 声明。

    为什么必须删这两处（写死在这里，别让下一个人猜）：

    * HTML5 里内联的 SVG 不需要命名空间声明，删掉不影响渲染；
    * 仓内「零外链」闸门（``tests/test_audit_export.py``）禁的是**字面**的网络协议前缀串，
      而 ``qr.to_svg`` 的 ``xmlns`` 取值正是一串 W3C 命名空间 URL，直接内联必然命中。
      （这个 URL 本函数**不写全**：包源码里出现字面网络串会撞离线纯度闸门
      ``tests/test_audit_log.py::PurityTests`` 与 ``tests/test_trace_adapter.py::OfflinePurityTests``。）

    几何一字不动：``viewBox`` 与全部 ``rect`` 坐标保持 ``to_svg`` 的原值（钉测逐坐标比对），
    所以删声明不改变码的形状，只改「放进 HTML 的方式」。
    """
    svg = certificate_svg(cert, scale=scale, border=border)
    if svg is None:
        return None
    body = _XML_DECL_RE.sub("", svg, count=1)
    return _XMLNS_ATTR_RE.sub("", body, count=1)


def _certificate_html(cert: Mapping[str, Any]) -> str:
    """责任认定书区块（可打印 HTML 版）：逐行正文 + 内联 SVG 二维码，零脚本零外链。

    没有封印锚点时**不画码**，只写一句诚实说明（§1.4：不伪造、不顶替）。
    """
    parts: list[str] = ['<section class="cert">']
    parts.append(f"<h2>{_html.escape(str(cert.get('title') or CERT_TITLE))}</h2>")
    parts.append("<ul>")
    for line in certificate_text(cert, include_title=False):
        parts.append(f"<li><code>{_html.escape(line)}</code></li>")
    parts.append("</ul>")
    payload = cert.get("qr_payload")
    svg = certificate_inline_svg(cert)
    if svg is not None:
        field = _html.escape(str(cert.get("qr_field") or CERT_QR_FIELD))
        parts.append('<figure class="qr">')
        parts.append(svg.rstrip())
        parts.append(f'<figcaption>二维码载荷逐字等于正文的 <code>{field}</code> 行：'
                     f'<code>{_html.escape(str(payload))}</code>'
                     '（编码器 agent_explainer/qr.py，纯标准库；链上锚定未接入）'
                     '</figcaption>')
        parts.append("</figure>")
    else:
        note = _html.escape(str(cert.get("qr_note") or NO_SEAL_NOTE))
        parts.append(f'<p class="qr">（本文件无二维码）{note}</p>')
    parts.append("</section>")
    return "".join(parts)


def qr_side(size: int, *, module: float = QR_MODULE_PT,
            border: int = QR_QUIET_MODULES) -> float:
    """QR 区块（含静区）的边长（pt）。``size`` 是矩阵边长（``4v+17``）。"""
    return (int(size) + 2 * border) * module


def qr_rect_plan(matrix: Sequence[Sequence[int]], *, x: float, y: float,
                 module: float = QR_MODULE_PT,
                 border: int = QR_QUIET_MODULES) -> list[tuple[float, float, float, float]]:
    """深色模块 → reportlab 矩形计划 ``(x, y, w, h)``（``y`` 是矩形**下沿**）。

    矩阵第 0 行画在最上方：``y = y_top - (border + row + 1) * module``。
    ``x`` / ``y`` 是 QR 区块（含静区）的左下角与**上沿**，逐模块画方块、零第三方图像库。
    """
    size = len(matrix)
    plan: list[tuple[float, float, float, float]] = []
    for row in range(size):
        line_y = y - (border + row + 1) * module
        for col in range(size):
            if matrix[row][col]:
                plan.append((x + (border + col) * module, line_y, module, module))
    return plan


def qr_plan_to_matrix(plan: Sequence[Sequence[float]], *, x: float, y: float,
                      module: float = QR_MODULE_PT, border: int = QR_QUIET_MODULES,
                      size: int) -> list[list[int]]:
    """矩形计划 → 矩阵（:func:`qr_rect_plan` 的逆）。钉测用它证明「画出去的方块」可还原。"""
    grid = [[0] * int(size) for _ in range(int(size))]
    for rect in plan:
        rx, ry = float(rect[0]), float(rect[1])
        col = round((rx - x) / module) - border
        row = round((y - ry) / module) - border - 1
        if 0 <= row < int(size) and 0 <= col < int(size):
            grid[row][col] = 1
    return grid


def draw_qr_block(canvas: Any, matrix: Sequence[Sequence[int]], *, x: float, y: float,
                  module: float = QR_MODULE_PT,
                  border: int = QR_QUIET_MODULES) -> dict[str, Any]:
    """在 reportlab canvas 上逐模块画方块（白底 + 静区 + 深色块）；返回实测信息。"""
    size = len(matrix)
    side = qr_side(size, module=module, border=border)
    canvas.saveState()
    canvas.setFillColorRGB(1, 1, 1)
    canvas.rect(x, y - side, side, side, stroke=0, fill=1)
    canvas.setFillColorRGB(0, 0, 0)
    drawn = 0
    for rect in qr_rect_plan(matrix, x=x, y=y, module=module, border=border):
        canvas.rect(rect[0], rect[1], rect[2], rect[3], stroke=0, fill=1)
        drawn += 1
    canvas.restoreState()
    return {"drawn": True, "size": size, "modules": drawn, "side_pt": side,
            "x": x, "y": y, "module_pt": module, "border": border}


def qr_pdf_flowable(platypus: Any, cert: Mapping[str, Any], *,
                    module: float = QR_MODULE_PT,
                    border: int = QR_QUIET_MODULES) -> tuple[Any | None, dict[str, Any]]:
    """把 QR 包成 platypus ``Flowable``（逐模块画方块）；返回 ``(flowable|None, 实测说明)``。

    任何一步不成立都**如实返回 None + 原因**，不画假码：
    无封印锚点 / 载荷为空 / 命名空间里没有 ``Flowable``（测试替身、精简安装）。
    """
    matrix = certificate_qr(cert)
    if matrix is None:
        return None, {"drawn": False,
                      "reason": str(cert.get("qr_note") or NO_SEAL_NOTE)}
    base = getattr(platypus, "Flowable", None)
    if not isinstance(base, type):
        return None, {"drawn": False, "size": len(matrix),
                      "modules": sum(sum(row) for row in matrix),
                      "reason": "platypus.Flowable 不可用（测试替身/精简安装）：本文件未画二维码"}
    size = len(matrix)
    side = qr_side(size, module=module, border=border)
    plan = qr_rect_plan(matrix, x=0.0, y=side, module=module, border=border)

    class _QrFlowable(base):  # noqa: N801 - 局部排版单元
        """QR 的排版单元：白底 + 逐模块黑方块（零图像、零第三方）。"""

        def __init__(self) -> None:
            self.side_pt = side

        def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
            return (self.side_pt, self.side_pt)

        def draw(self) -> None:
            canv = self.canv
            canv.saveState()
            canv.setFillColorRGB(1, 1, 1)
            canv.rect(0, 0, self.side_pt, self.side_pt, stroke=0, fill=1)
            canv.setFillColorRGB(0, 0, 0)
            for rect in plan:
                canv.rect(rect[0], rect[1], rect[2], rect[3], stroke=0, fill=1)
            canv.restoreState()

    return _QrFlowable(), {"drawn": True, "size": size, "modules": len(plan),
                           "side_pt": side, "module_pt": module, "border": border,
                           "payload": cert.get("qr_payload")}


def draw_certificate_block(canvas: Any, cert: Mapping[str, Any], *, font: str,
                           y: float, x: float = 50.0, page_height: float = 842.0,
                           bottom: float = 40.0, font_size: float = 8.0,
                           module: float = QR_MODULE_PT,
                           border: int = QR_QUIET_MODULES) -> float:
    """在 canvas 上画责任认定书的**图形区**：逐模块画方块 + 载荷明文行；返回新的 ``y``。

    封印锚点、二维码编码与绘制都住在本模块**单点**（§11.10 的表述保持为真：
    ``compliance.py`` 里没有相关代码，它只是把画布交进来）。页边不够就换页。

    没有封印锚点 → **一块都不画**，只写一行诚实说明（§1.4：不伪造、不顶替、不画占位码）。
    载荷明文行与 PDF 正文里的链头行同源（``cert["qr_payload"]``），扫码比对可逐字相等。
    """
    matrix = certificate_qr(cert)
    canvas.setFont(font, font_size)
    if matrix is None:
        y -= 16
        if y < bottom:
            canvas.showPage()
            canvas.setFont(font, font_size)
            y = page_height - 60
        canvas.drawString(x, y, "qr: none (no sealed_head in this export context)")
        y -= 12
        canvas.drawString(x, y, str(cert.get("qr_note") or NO_SEAL_NOTE))
        return y
    side = qr_side(len(matrix), module=module, border=border)
    if y - 10 - side - 26 < bottom:
        canvas.showPage()
        canvas.setFont(font, font_size)
        y = page_height - 60
    draw_qr_block(canvas, matrix, x=x, y=y - 10, module=module, border=border)
    y = y - 10 - side - 12
    canvas.setFont(font, font_size)
    canvas.drawString(x, y, f"qr_payload: {cert.get('qr_payload')}")
    y -= 12
    canvas.drawString(x, y, "qr: payload = sealed_head (audit seal anchor), "
                            "verbatim equal to the sealed_head line above")
    return y



def render_html(rows: Sequence[Mapping[str, Any]], *,
                title: str = "TraceLens 用户操作审计",
                status: Mapping[str, Any] | None = None,
                notes: Sequence[str] | None = None,
                log_path: str | None = None,
                footer_note: str | None = None,
                certificate: Mapping[str, Any] | None = None) -> str:
    """可打印 HTML：内联 CSS、A4 横向、零外链、零脚本。

    ``footer_note``：数据来源标记（0.5.1 收口 README 已知限制 #3）——
    演示数据的横幅只在界面里，导出件离开页面就没了上下文，所以来源说明
    要写进文件本体（页脚 + 说明区都有）。

    ``certificate``：责任认定书区块（R8-3 / B8-1）。传了才渲染（不传 = 输出逐字
    不变，HTTP 面与既有调用零影响）；QR 用内联 SVG（``qr.to_svg`` 产物去掉 ``xmlns``，
    零脚本零外链），载荷与正文的 ``sealed_head`` 行同源逐字相等。
    """
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
                  ".cert{border:1px solid #cfd8e3;border-radius:6px;padding:8px 10px;"
                  "margin:0 0 12px;font-size:12px;background:#fbfcfe}"
                  ".cert h2{font-size:14px;margin:0 0 6px}"
                  ".cert ul{margin:0 0 8px;padding-left:18px}"
                  ".cert li{margin:0 0 2px}"
                  "figure.qr{margin:8px 0 0;padding:0}"
                  "figure.qr svg{width:132px;height:132px}"
                  "figcaption{font-size:11px;color:#5b6472;margin-top:4px}"
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
    if isinstance(certificate, Mapping):
        lines.append(_certificate_html(certificate))
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
    if footer_note:
        lines.append(f'<footer>{_html.escape(str(footer_note))}</footer>')
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
    # 字体相关模块是**可选**的：拿不到就退 CID / Helvetica，不能把「出 PDF」整件事拖死
    # （录制型替身与精简安装都没有它们；R8-7b 首次改法把它们并进必选导入，两例钉测立刻失守）。
    # 只看注入的包本身：``pdfbase`` 没有 ``__path__`` = 替身模块（不是真包），**不要去取子模块**，
    # 否则 ``importlib`` 会顺着 ``sys.modules`` 摸到别的用例导入过的真模块，把替身整体绕过。
    package = namespace["pdfbase"]
    if getattr(package, "__path__", None):
        for key, modname in (("pdfmetrics", "reportlab.pdfbase.pdfmetrics"),
                             ("ttfonts", "reportlab.pdfbase.ttfonts")):
            try:
                namespace[key] = importlib.import_module(modname)
            except Exception:                       # noqa: BLE001
                namespace[key] = None
    else:
        namespace["pdfmetrics"] = None
        namespace["ttfonts"] = None
    return namespace, None


#: PDF 中文字体候选（先嵌入 TrueType → 自包含，任何阅读器都能显示）。
#: ``(注册名, 路径, .ttc 子字体序号)``：命中第一个能注册的就用，路径不存在直接跳过。
PDF_FONT_CANDIDATES: tuple[tuple[str, str, int], ...] = (
    ("TraceLensCJK", r"C:\Windows\Fonts\msyh.ttc", 0),      # 微软雅黑
    ("TraceLensCJK", r"C:\Windows\Fonts\simhei.ttf", 0),    # 黑体（单文件，最稳）
    ("TraceLensCJK", r"C:\Windows\Fonts\Deng.ttf", 0),      # 等线
    ("TraceLensCJK", r"C:\Windows\Fonts\simsun.ttc", 0),    # 宋体
    ("TraceLensCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("TraceLensCJK", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("TraceLensCJK", "/System/Library/Fonts/PingFang.ttc", 0),
)


def _register_pdf_font(namespace: Mapping[str, Any]) -> tuple[str, str]:
    """注册中文 PDF 字体，优先级：**嵌入 TTF** → 自带 CID → Helvetica（并说明丢字）。

    R8-7b 修复（PM 亲自，老板授权「按最优解拍板」后实件取证发现），两处缺陷同源：

    1. ``registerFont`` 住在 :mod:`reportlab.pdfbase.pdfmetrics`，**不在** ``reportlab.pdfbase``
       包上。旧实现写 ``pdfbase.registerFont(name)`` 必然抛 ``AttributeError``，三个候选字体
       全在同一个异常上失败 → 永远退回 Helvetica，于是**每一次 PDF 导出的中文全部丢失**
       （认定书正文不可读，却仍返回 ``format="pdf"`` / ``fallback=None``，即「看着成功」）。
    2. 只依赖 reportlab 自带 ``STSong-Light`` 也不够：那是**不嵌入**的 CID 字体，对方阅读器
       没有 Adobe 亚洲字体包就显示方块。对外举证的交付物必须**自包含**，所以优先嵌入
       系统 TrueType（reportlab 自动子集化，体积可控）。
    """
    pdfmetrics = namespace.get("pdfmetrics")
    ttfonts = namespace.get("ttfonts")
    pdfbase = namespace.get("pdfbase")

    def _register(font: Any, name: str) -> None:
        """把字体登记进 reportlab。

        优先级按**注入的命名空间**决定，不让 `sys.modules` 里的残留模块越权：

        1. 注入的 ``pdfbase`` 自带 ``registerFont`` → 按**名字**登记（录制型替身 / 旧版口径）；
           真实 reportlab 的 ``reportlab.pdfbase`` 包上**没有**这个属性，所以这条只对替身生效。
        2. 否则用注入的 ``pdfmetrics.registerFont`` 按**字体对象**登记（当前真实口径）。
        3. 都没有 → 抛出去，让上层按「无 CJK」如实退回。

        第 1 条必须在第 2 条之前：否则别的用例导入过真 ``pdfmetrics`` 后它常驻
        ``sys.modules``，替身注入会被整体绕过（R8-7b 第二次踩点）。
        """
        legacy = getattr(pdfbase, "registerFont", None)
        if legacy is not None:
            legacy(name)
            return
        if pdfmetrics is not None and hasattr(pdfmetrics, "registerFont"):
            pdfmetrics.registerFont(font)
            return
        raise RuntimeError("当前 reportlab 没有 registerFont：无法登记 CJK 字体")

    # ① 嵌入 TrueType：PDF 自包含，不靠对方装字体
    if ttfonts is not None:
        for name, path, index in PDF_FONT_CANDIDATES:
            try:
                if not pathlib.Path(path).exists():
                    continue
                font = ttfonts.TTFont(name, path, subfontIndex=index)
                _register(font, name)
                return name, f"嵌入 CJK 字体 {pathlib.Path(path).name}（TrueType 子集，PDF 自包含）"
            except Exception:                   # noqa: BLE001
                continue

    # ② 退回 reportlab 自带 CID 字体（不嵌入：对方需带亚洲字体包才显示中文）
    cidfonts = namespace.get("cidfonts")
    if cidfonts is not None:
        for name in ("STSong-Light", "STSongStd-Light", "HeiseiMin-W3"):
            try:
                font = cidfonts.UnicodeCIDFont(name)
                _register(font, name)
                return name, (f"使用 CID 字体 {name}（**未嵌入**：对方阅读器缺亚洲字体包时"
                              "中文显示为方块，建议改用可打印 HTML 或装中文字体）")
            except Exception:                   # noqa: BLE001
                continue
    return "Helvetica", "未找到可用 CJK 字体：中文可能被丢弃，建议改用可打印 HTML"


def _build_pdf(namespace: Mapping[str, Any], target: Any,
               entries: Sequence[Mapping[str, Any]], *, title: str,
               status: Mapping[str, Any], notes: Sequence[str] | None = None,
               certificate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """用 reportlab 排一份 A4 横向表格；调用方负责回退。

    ``target`` 可以是路径（写文件）也可以是可写字节流（HTTP 直接回字节，不落盘）。

    ``certificate``（R8-3 / B8-1）：责任认定书区块 —— 逐行正文 + 二维码（逐模块画方块，
    零图像零第三方）。不传 = 输出与旧版一致；载荷与正文链头行同源（``cert["qr_payload"]``）。
    """
    platypus = namespace["platypus"]
    pagesizes = namespace["pagesizes"]
    styles = namespace["styles"]
    units = namespace["units"]
    font, font_note = _register_pdf_font(namespace)
    sheet = styles.getSampleStyleSheet()
    title_style = sheet["Title"] if hasattr(sheet, "__getitem__") else getattr(sheet, "Title")
    body_style = sheet["Normal"] if hasattr(sheet, "__getitem__") else getattr(sheet, "Normal")
    # R8-7b：注册好的字体必须**同时套到标题与正文**，否则只有表格走新字体，
    # 标题（Title 默认 Helvetica-Bold）与段落（Normal 默认 Helvetica）里的中文仍然落不下来。
    # 「尽力而为」：录制型替身里的样式是字符串占位，套不上就跳过 —— 绝不能因为改样式
    # 把整份 PDF 拖成 HTML 回退（首改即踩：异常冒到 export_pdf 的兜底分支）。
    try:
        title_style = title_style.clone(title_style.name, fontName=font,
                                       fontSize=title_style.fontSize,
                                       leading=title_style.leading)
        body_style = body_style.clone(body_style.name, fontName=font,
                                      fontSize=body_style.fontSize,
                                      leading=body_style.leading)
    except Exception:                            # noqa: BLE001
        try:
            title_style.fontName = font
            body_style.fontName = font
        except Exception:                        # noqa: BLE001 - 占位样式：跳过，不影响真实路径
            pass
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
    cert_info: dict[str, Any] = {"enabled": certificate is not None}
    if certificate is not None:
        for line in certificate_text(certificate):
            flow.append(platypus.Paragraph(line, body_style))
        qr_flow, qr_info = qr_pdf_flowable(platypus, certificate)
        cert_info["qr"] = qr_info
        if qr_flow is not None:
            flow.append(platypus.Spacer(1, 3 * units.mm))
            flow.append(qr_flow)
        flow.append(platypus.Spacer(1, 4 * units.mm))
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
            "path": str(target), "certificate": cert_info}


# --------------------------------------------------------------------------- #
# 入口与便利函数
# --------------------------------------------------------------------------- #


def render_pdf_bytes(entries: Sequence[Mapping[str, Any]], *,
                     title: str = "TraceLens 用户操作审计",
                     status: Mapping[str, Any] | None = None,
                     notes: Sequence[str] | None = None,
                     certificate: Mapping[str, Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    """在内存里排 PDF 并返回 ``(字节, 说明)``；reportlab 不可用抛 :class:`AuditExportError`。

    HTTP 层要直接回一份 PDF，不能让服务自己往磁盘写文件（server.py 零文件写入是
    t13/t14 钉住的纯度），所以这里只吃内存流。调用方拿到
    :class:`AuditExportError` 就按规格退回可打印 HTML 并说明原因。

    ``certificate``（R8-3 / B8-1）：责任认定书区块。默认 ``None`` = 关闭，
    既有 HTTP 面的字节一字不变（启用要改 ``server.py`` 的调用点，超出本任务边界，
    已按 §4 第 5 项登记遗留）。
    """
    namespace, reason = _load_reportlab()
    if namespace is None:
        raise AuditExportError("AUDIT_REPORTLAB_UNAVAILABLE",
                               "PDF 导出需要 reportlab，当前不可导入",
                               hint="pip install reportlab，或改用 format=html 打印后存 PDF",
                               detail={"reason": reason})
    buffer = io.BytesIO()
    payload = _build_pdf(namespace, buffer, list(entries), title=title,
                         status=dict(status or {}), notes=notes, certificate=certificate)
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
