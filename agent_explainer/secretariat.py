"""总裁办：责任认定与签名背书中枢（规格书第二节；docs/07 §2）。

一句话使命：证明"哪个 AI、在哪个时间段、做了哪些事、谁为这些行为背书签字、
出了问题谁担责"。对应 NIST 的 Agent 治理四支柱——身份识别、授权、审计、不可抵赖。

四个机制（与规格书 2.1–2.4 一一对应）
------------------------------------
1. **身份确权**：每个 Agent 实例一枚 Ed25519 密钥对，公钥绑定镜像哈希、启动参数哈希、
   所属用户 DID，注册进 append-only 账本（``identities.jsonl``，带 ``seq``/``prev_hash``/
   ``entry_hash``）。
2. **行为签名**：每条 ActionRecord 由**该 Agent 的私钥**签名，签名覆盖
   ``timestamp/agent_id/action_type/tool/input_hash/output_hash/prev_hash/entry_hash/
   risk_level/confidence/status``；签名前先做规范 JSON 序列化（防编码歧义）。
   风险分级决定"要不要人工审批签、要不要总裁办盖章"（``RISK_POLICY``）。
3. **责任归属**：每次委托生成 ``DelegationRecord``，形成 人 → 主 Agent → 子 Agent 的责任链；
   权限**只能收窄不能放大**，委托链最大深度 5 层。
4. **争议裁定**：出事时可生成**可离线验证**的责任认定书——涉案身份、责任链、行为摘要、
   验签结果、哈希链连续性、责任判定，全部由总裁办公钥签名。

诚实边界（本模块刻意不做的事，写进 docs/07 §10）
------------------------------------------------
* 密钥来源是**本地密钥库**（``keyring.json``，``chmod 0600``），不是 HSM/TEE；
  生产环境应把 seed 的来源换成 TEE/HSM —— 本模块只依赖 32 字节 seed 这个抽象。
* 可信时间戳写 ``local-tsa:<ISO>``，``verify_record`` 会如实标 ``tsa_state="local_only"``，
  **绝不**显示成"RFC 3161 可信时间戳已获取"。
* 审批人签名由本机为该 DID 生成的密钥产生：它证明"这次审批在本机被记录并签名"，
  **不**证明真实世界里的签字意愿。界面文案必须照这个口径写。

纯标准库、无网络；落盘一律走 ``feature_store`` 的追加式账本（断链只报告，不修不重建）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from . import crypto_sig as cs
# 「代行」标注的键名与长度上限由协作层（sharing）单点定义：两边各写一份字面量，
# 键名漂移的后果是"责任标注静默消失"——那正是 B5-3 要避免的事。sharing 只 import
# 标准库与 feature_store，不反向 import 本模块，无循环依赖。
from . import sharing
from .feature_store import (
    GENESIS_HASH,
    FeaturePaths,
    FeatureStore,
    FeatureStoreError,
    Ledger,
    hash_object,
    new_id,
    open_ledger,
    open_store,
    sha256_hex,
    utc_stamp,
)

__all__ = [
    "SECRETARIAT_VERSION",
    "SIGNATURE_FIELDS",
    "RISK_POLICY",
    "RISK_ORDER",
    "MAX_DELEGATION_DEPTH",
    "ROLES",
    "ROLE_LABELS",
    "TSA_LOCAL_PREFIX",
    "KEYRING_ID",
    "ON_BEHALF_FIELDS",
    "ON_BEHALF_TEMPLATE",
    "ON_BEHALF_REF_KEYS",
    "on_behalf_note",
    "on_behalf_index",
    "SecretariatError",
    "Secretariat",
    "risk_requirements",
    "default_secretariat",
]

#: 本模块的口径版本（与产品版本 0.5.0、数据契约 1.1 各自独立）
SECRETARIAT_VERSION = "1.0"
#: 签名覆盖的字段（规格书 2.2 点名 + 三个决定"这条记录做了什么"的事实字段）。
#: 顺序固定：规范 JSON 排序会抹平顺序差异，但文档与测试都按这个列表讲。
SIGNATURE_FIELDS: tuple[str, ...] = (
    "timestamp", "agent_id", "action_type", "tool", "input_hash", "output_hash",
    "prev_hash", "entry_hash", "risk_level", "confidence", "status",
)
#: 风险分级 → 签名要求（规格书 2.5 表）
RISK_POLICY: dict[str, dict[str, Any]] = {
    "low": {"approvers": 0, "seal": False, "describe_key": "secretariat.risk.low",
            "examples": "读取日历、查询天气"},
    "medium": {"approvers": 0, "seal": False, "describe_key": "secretariat.risk.medium",
               "examples": "写入笔记、修改草稿"},
    "high": {"approvers": 1, "seal": False, "describe_key": "secretariat.risk.high",
             "examples": "支付、发送邮件、删除文件"},
    "critical": {"approvers": 2, "seal": True, "describe_key": "secretariat.risk.critical",
                 "examples": "转账超限、对外合同、删除生产数据"},
}
#: 风险排序（越大越危险），委托 scope 只能往下收
RISK_ORDER: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}
#: 委托链最大深度（规格书 2.3：建议 ≤ 5 层）
MAX_DELEGATION_DEPTH = 5
#: 以下三个上限来自数据契约 `docs/schemas/action_record.schema.json`（机器事实，
#: docs/12 §1 判定优先级：schema > docs/02 > 实现）。签名器写出的字段必须过自家 schema，
#: 否则"已签名记录"在 A2（MiniValidator）处必然失败，且超限值会进签名域/哈希。
#: approval.approvers: maxItems 4
MAX_APPROVERS = 4
#: approval.approvers[].role: maxLength 40
APPROVER_ROLE_MAX = 40
#: tool: maxLength 120（签名域第 4 域，由 tool_used.name 派生）
TOOL_NAME_MAX = 120
#: 每个 Agent 在链头记录里保留多少条历史 entry_hash，供 prev_hash 连续性检查。
#: 有限长度：超出上限的老记录如实退回 unverified，不假装"已验证"。
HEAD_HISTORY = 500
#: 责任判定的三种类型
ROLES: tuple[str, ...] = ("technical", "decision", "platform")
ROLE_LABELS: dict[str, str] = {
    "technical": "技术责任",
    "decision": "决策责任",
    "platform": "平台责任",
}
#: 离线环境的时间戳前缀（honest downgrade，见模块头注释）
TSA_LOCAL_PREFIX = "local-tsa:"
#: 总裁办自己的密钥在密钥库里的保留 id
KEYRING_ID = "__secretariat__"
#: 密钥库文件权限：仅属主可读写（Windows 尽力而为，见 _Keyring）
KEYRING_MODE = 0o600

#: WRITE 进“已签名记录”的 DID 必须满足 schema 的 ``$defs/did``：
#: ``^did:[a-z0-9]{2,32}:[a-z0-9-]{1,32}:[a-z0-9]{8,64}$``。
#: 责任链链尾是 **Agent 实例的 DID**（由公钥派生），不是 ``agent_id``。
RECORD_DID_RE = re.compile(r"^did:[a-z0-9]{2,32}:[a-z0-9-]{1,32}:[a-z0-9]{8,64}$")
#: 委托记录本身的 DID 只做“四段、段非空”的结构校验（历史委托不因新规则失效），
#: 但形态必须像 DID，避免把随便一个字符串写进责任链。
LOOSE_DID_RE = re.compile(r"^did:[a-z0-9]{2,32}:[a-z0-9-]{1,32}:[A-Za-z0-9._-]{1,64}$")
#: agent_id 的形状（docs/09 D13）：它会变成 keyring 键与文件名。
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
#: Windows 保留名（不区分大小写、含扩展名也算），作为 agent_id 会建不出文件。
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _require_record_did(value: Any, *, field: str) -> str:
    """校验一个“会写进已签名记录”的 DID；不合法直接 400。"""
    text = str(value or "").strip()
    if not RECORD_DID_RE.match(text):
        raise SecretariatError(
            "SECRETARIAT_FIELD_INVALID",
            f"{field} 必须是合法 DID（did:tracelens:user|agent:<8-64 位小写字母数字>）",
            field=field, detail={"received": text[:120]})
    return text


def _require_loose_did(value: Any, *, field: str) -> str:
    """校验委托记录里的 DID（四段结构，历史数据兼容）。"""
    text = str(value or "").strip()
    if not LOOSE_DID_RE.match(text):
        raise SecretariatError(
            "SECRETARIAT_DELEGATION_INVALID",
            f"{field} 必须是 DID（did:<方法>:<类型>:<标识>）",
            field=field, detail={"received": text[:120]})
    return text


def agent_did_of(pubkey_b64: str) -> str:
    """由 Agent 公钥派生实例 DID（责任链链尾用它，不用 agent_id）。"""
    try:
        raw = cs.b64d(str(pubkey_b64 or ""))
    except (ValueError, TypeError):
        raw = b""
    if not raw:
        return ""
    return cs.did_from_pubkey(raw, kind="agent")


def approval_digest(record: Mapping[str, Any]) -> str:
    """审批签名的记录绑定摘要（排除 ``entry_hash`` 与 ``approval`` 自身，避免自引用）。

    审批签名 payload 用它兜住“这条记录”的事实：换一条记录、改一个字段，
    审批签名就复算不出来，跨记录复用因此失效。
    """
    facts = {key: record.get(key) for key in SIGNATURE_FIELDS if key != "entry_hash"}
    return hash_object(facts)


class SecretariatError(Exception):
    """可读错误：机器码 + 人话原因 + 字段 + 取证细节 + HTTP 建议状态。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 detail: Mapping[str, Any] | None = None, hint: str | None = None,
                 status: int | None = None) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.field = field
        self.detail: dict[str, Any] = dict(detail or {})
        self.hint = hint
        self.http_status = int(status or _status_for(code))

    def render(self) -> str:
        tail = f"（{self.field}）" if self.field else ""
        return f"{self.code}: {self.reason}{tail}"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.reason}
        if self.field:
            payload["field"] = self.field
        if self.detail:
            payload["detail"] = dict(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


def _status_for(code: str) -> int:
    """码 → HTTP 状态：字段类 400，缺失 404，状态冲突 409，能力不足 403，落盘 500。"""
    text = str(code)
    if text.endswith(("_NOT_FOUND", "_MISSING")):
        return 404
    if text in ("SECRETARIAT_SCOPE_WIDENED", "SECRETARIAT_DEPTH_EXCEEDED",
                "SECRETARIAT_APPROVAL_REQUIRED", "SECRETARIAT_IDENTITY_REVOKED",
                "SECRETARIAT_DELEGATION_EXPIRED"):
        return 409
    if text in ("SECRETARIAT_WRITE_FAILED", "SECRETARIAT_KEYSTORE_UNAVAILABLE"):
        return 500
    return 400


def _now_iso() -> str:
    return utc_stamp()


def _parse_iso(text: Any) -> _dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _iso(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# 「代行」标注（REQ-005 B5-3；口径来源 PM-PLAN §7.1 冻结 + TASK-R5-2 §2.2，只读渲染增补）
# --------------------------------------------------------------------------- #

#: 认定书的动作条目**只在有代行标注时**才多这三个键；无标注的行为行逐字不变（连键序都不动）。
#: 记的是"谁做的（actor）/ 替谁做的（on_behalf_of）/ 怎么用人话说（note）"。
ON_BEHALF_FIELDS = ("on_behalf_of", "on_behalf_actor", "on_behalf_note")
#: 措辞逐字取自 PM-PLAN §7.1：「由 X 代行 Y 行使权限，最终责任归 human_owner」。
#: Y 取**记录里的** ``human_owner``（责任根），不是事件里那个标注字符串——
#: 「最终责任归真人」是冻结口径，不能被一条事件的自述改写。
ON_BEHALF_TEMPLATE = "由 {actor} 代行 {principal} 行使权限，最终责任归 {principal}（human_owner）"
#: 代行句进认定书正文、正文进认定书签名 → 必须有界（DID 两侧各自已有上限，这里再兜一道）。
ON_BEHALF_NOTE_MAX = 640
#: 协作事件指向"哪条记录"的字段：事件 ``target`` 之外，``detail`` 里这两个键也认。
#: 只认这三处、不猜别的形状：猜出来的关联会在认定书里凭空多出一句责任归属的话。
ON_BEHALF_REF_KEYS = ("record_id", "action_id")
#: 协作事件**窗口未取全**时登记进 ``verification.problems`` 的取证 scope（R5-3 / T7 接线）。
#: 口径（PM-VERDICT-R5-2 §3.2）：认定书只带得动**窗口内**的事件，窗口外的代行标注不参与
#: 渲染 —— 认定书必须**自己承认**这一点，不得让读者以为"这份认定书看过了全部协作记录"。
COLLAB_EVENTS_SCOPE = "collab_events"
#: 窗口未取全这条登记的固定说法（进 ``detail.reason``，界面走 i18n，这里只放事实）。
COLLAB_EVENTS_WINDOW_REASON = ("协作事件只读到了最近 {returned} 条（该身份账本共 {total} 条，"
                               "窗口上限 {limit}）：窗口外的代行标注不参与本次渲染")


def on_behalf_note(*, actor: str, principal: str) -> str:
    """把「谁做的 / 替谁做的 / 谁担责」渲染成认定书上那一行人话（纯函数，可单测）。

    ``actor`` = 实际操作人（协作者 DID，即事件的 ``actor_did``）；
    ``principal`` = 最终担责的真人（**记录里的** ``human_owner``）。
    两侧都裁到上限：这句话进认定书正文、正文进认定书签名，不能有无限长的自述。
    """
    who = str(actor or "").strip()[:sharing.ON_BEHALF_MAX_LEN]
    owner = str(principal or "").strip()[:sharing.ON_BEHALF_MAX_LEN]
    text = ON_BEHALF_TEMPLATE.format(actor=who, principal=owner)
    return text[:ON_BEHALF_NOTE_MAX]


def on_behalf_index(events: Iterable[Mapping[str, Any]] | None
                    ) -> dict[str, dict[str, str]]:
    """把带代行标注的协作事件收成 ``记录标识 → {actor, on_behalf_of, event_id}``。

    口径（TASK-R5-2 §2.2：标注的**唯一载体是事件层** ``detail.on_behalf_of``）：

    * 只收 ``detail.on_behalf_of`` 为非空字符串的事件，其余（含畸形值）**整条跳过**——
      没有标注就没有代行句，认定书逐字不变；
    * 关联标识取事件的 ``target``，外加 ``detail.record_id`` / ``detail.action_id``；
      空标识跳过（空串会把"所有没有 id 的记录"都关联上，那是凭空造责任）；
    * 同一条记录被多次代行时**后写的胜出**（账本升序，最近一次才是当下有效的事实）；
      每一次代行在账本里都是独立一条、链上可查，这里只是渲染用的索引；
    * 本函数**不改账本、不读文件**：调用方把 ``ShareService.events()`` 递进来
      （协作层与总裁办之间不新增依赖方向，只共用同一个键名常量）。
    """
    index: dict[str, dict[str, str]] = {}
    for event in (events or []):
        if not isinstance(event, Mapping):
            continue
        detail = event.get("detail")
        if not isinstance(detail, Mapping):
            continue
        raw = detail.get(sharing.ON_BEHALF_KEY)
        if not isinstance(raw, str):
            continue
        principal = raw.strip()
        if not principal:
            continue
        refs: list[str] = []
        target = event.get("target")
        if isinstance(target, str) and target.strip():
            refs.append(target.strip())
        for key in ON_BEHALF_REF_KEYS:
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                refs.append(value.strip())
        if not refs:
            continue
        hit = {"actor": str(event.get("actor_did") or "").strip(),
               "on_behalf_of": principal[:sharing.ON_BEHALF_MAX_LEN],
               "event_id": str(event.get("id") or "")}
        for ref in refs:
            index[ref] = dict(hit)
    return index


#: 委托默认有效期（30 天）
DEFAULT_DELEGATION_TTL = 30 * 86400


def _coerce_ttl(value: Any) -> int:
    """``ttl_seconds`` 只接受整数秒：坏类型必须是 400，不能冒成 ``ValueError`` → 500。

    ``None`` 走默认 30 天；显式传 ``0`` 不再被当成"没传"（原实现 ``if ttl_seconds else 默认``
    会把 0 秒的请求变成 30 天，等于凭空多给一个月的授权），交给范围检查拒掉。
    """
    if value is None:
        return DEFAULT_DELEGATION_TTL
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                               "ttl_seconds 必须是整数秒（1 秒 ~ 366 天）",
                               field="ttl_seconds", detail={"received": type(value).__name__})
    if isinstance(value, float) and not value.is_integer():
        raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                               "ttl_seconds 必须是整数秒（1 秒 ~ 366 天）",
                               field="ttl_seconds", detail={"received": value})
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                               "ttl_seconds 必须是整数秒（1 秒 ~ 366 天）",
                               field="ttl_seconds",
                               detail={"received": str(value)[:120]}) from exc


def risk_requirements(risk_level: str) -> dict[str, Any]:
    """某一风险等级需要的签名材料（规格书 2.5 表的代码口径）。"""
    level = str(risk_level or "low").strip().lower()
    if level not in RISK_POLICY:
        raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                               f"不认识的风险等级 {risk_level!r}：只支持 low/medium/high/critical",
                               field="risk_level", status=400)
    policy = dict(RISK_POLICY[level])
    policy["level"] = level
    policy["self_sign"] = True
    return policy


# --------------------------------------------------------------------------- #
# 本地密钥库
# --------------------------------------------------------------------------- #

class _Keyring:
    """本地密钥库：``keyring.json``，只存 32 字节 seed 与公钥（``chmod 0600``）。

    为什么不是一个"真·密钥服务"：本产品**离线、零依赖**，没有 HSM/TEE 可用。
    因此这里提供的是**可替换的抽象**：调用方只需要"某个 key_id 对应一枚 seed"。
    生产环境把 ``seed_of()`` 换成 TEE/HSM 的取钥匙实现即可，其余代码一行不改。
    私钥**永不**出现在 API 响应、审计条目或错误信息里（``public_view`` 只给公钥）。
    """

    def __init__(self, path: pathlib.Path | str) -> None:
        self.path = pathlib.Path(path)
        self._cache: dict[str, dict[str, Any]] | None = None
        self.writes = 0

    # -- 读写 ---------------------------------------------------------
    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretariatError("SECRETARIAT_KEYSTORE_UNAVAILABLE",
                                   f"密钥库读不出来：{exc}",
                                   detail={"path": str(self.path)}) from exc
        entries = raw.get("keys") if isinstance(raw, dict) else None
        self._cache = entries if isinstance(entries, dict) else {}
        return self._cache

    def _flush(self) -> None:
        entries = self._load()
        payload = {"schema": SECRETARIAT_VERSION, "kind": "tracelens.keyring",
                   "updated_at": _now_iso(), "keys": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp",
                                             dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise SecretariatError("SECRETARIAT_KEYSTORE_UNAVAILABLE",
                                   f"密钥库写不进去：{exc}",
                                   detail={"path": str(self.path)}) from exc
        try:
            os.chmod(self.path, KEYRING_MODE)
        except OSError:  # pragma: no cover - Windows 上 chmod 只尽力而为
            pass
        self.writes += 1

    # -- 语义 ---------------------------------------------------------
    def ensure(self, key_id: str, *, kind: str = "agent") -> dict[str, Any]:
        """确保 ``key_id`` 有一枚密钥（没有就生成），返回**公开视图**。"""
        entries = self._load()
        entry = entries.get(str(key_id))
        if entry is None:
            seed, pub = cs.generate_keypair()
            entry = {"seed": cs.b64e(seed), "pubkey": cs.b64e(pub), "kind": str(kind),
                     "created_at": _now_iso()}
            entries[str(key_id)] = entry
            self._flush()
        return self.public_view(str(key_id)) or {}

    def rotate(self, key_id: str, *, kind: str = "agent") -> dict[str, Any]:
        """换一枚新密钥（身份轮换 = 新实例 = 新密钥）。

        旧私钥被替换掉，但历史记录仍然能用**它们自己的** ``agent_pubkey`` 验证——
        身份账本里保留着每一代公钥（``supersedes`` 链），所以"换了钥匙"不会让
        过去的签名失效，这正是"责任可追溯"要的效果。
        """
        seed, pub = cs.generate_keypair()
        entries = self._load()
        entries[str(key_id)] = {"seed": cs.b64e(seed), "pubkey": cs.b64e(pub),
                                "kind": str(kind), "created_at": _now_iso()}
        self._flush()
        return self.public_view(str(key_id)) or {}

    def seed_of(self, key_id: str) -> bytes:
        entries = self._load()
        entry = entries.get(str(key_id))
        if entry is None:
            self.ensure(key_id)
            entry = self._load().get(str(key_id)) or {}
        try:
            return cs.b64d(str(entry.get("seed")))
        except (ValueError, TypeError) as exc:  # pragma: no cover - 密钥库被手改
            raise SecretariatError("SECRETARIAT_KEYSTORE_UNAVAILABLE",
                                   "密钥库里这枚 seed 不是合法的 base64",
                                   detail={"key_id": str(key_id)}) from exc

    def public_of(self, key_id: str) -> bytes:
        try:
            return cs.b64d(str((self._load().get(str(key_id)) or {}).get("pubkey")))
        except (ValueError, TypeError):
            return b""

    def public_view(self, key_id: str) -> dict[str, Any] | None:
        entry = self._load().get(str(key_id))
        if not isinstance(entry, dict):
            return None
        pub_b64 = str(entry.get("pubkey") or "")
        return {"key_id": str(key_id), "pubkey": pub_b64, "kind": str(entry.get("kind") or ""),
                "created_at": str(entry.get("created_at") or ""),
                "fingerprint": sha256_hex(pub_b64)[:16]}

    def has(self, key_id: str) -> bool:
        return str(key_id) in self._load()

    def count(self) -> int:
        return len(self._load())

    def facts(self) -> dict[str, Any]:
        return {"path": str(self.path), "exists": self.path.exists(),
                "keys": self.count(), "writes": self.writes,
                "mode": oct(KEYRING_MODE), "source": "local_file"}


# --------------------------------------------------------------------------- #
# 总裁办
# --------------------------------------------------------------------------- #

class Secretariat:
    """身份 + 签名 + 委托 + 认定书。全部状态落在 ``FeaturePaths`` 下。"""

    def __init__(self, paths: FeaturePaths, *, mode: str = "local", now: Any = None,
                 auto_register: bool = True) -> None:
        self.paths = paths
        self.mode = str(mode or "local")
        self.auto_register = bool(auto_register)
        self._now = now if callable(now) else _now_iso
        self.paths.ensure()
        self._identities: Ledger | None = None
        self._delegations: Ledger | None = None
        self._letters: Ledger | None = None
        self._heads: FeatureStore | None = None
        self._keyring: _Keyring | None = None
        self.signed = 0
        self.failures = 0
        self.degraded = ["local-tsa", "in-process-keys"]

    # -- 装配 ---------------------------------------------------------
    @property
    def identities_ledger(self) -> Ledger:
        if self._identities is None:
            self._identities = open_ledger(self.paths.identities)
            self._identities.load()
        return self._identities

    @property
    def delegations_ledger(self) -> Ledger:
        if self._delegations is None:
            self._delegations = open_ledger(self.paths.delegations)
            self._delegations.load()
        return self._delegations

    @property
    def letters_ledger(self) -> Ledger:
        if self._letters is None:
            self._letters = open_ledger(self.paths.letters_index)
            self._letters.load()
        return self._letters

    @property
    def heads(self) -> FeatureStore:
        if self._heads is None:
            self._heads = open_store(self.paths.secretariat_dir / "heads.jsonl")
            self._heads.load()
        return self._heads

    @property
    def keyring(self) -> _Keyring:
        if self._keyring is None:
            self._keyring = _Keyring(self.paths.keyring)
        return self._keyring

    def _seal_key(self) -> str:
        return KEYRING_ID

    # -- 身份确权 -----------------------------------------------------
    def register_identity(self, *, agent_id: str, owner_did: str,
                          image_hash: str | None = None,
                          boot_params: Any = None, kind: str = "agent",
                          rotate: bool = False) -> dict[str, Any]:
        """签发/轮换一个 Agent 身份（公钥进 append-only 账本，私钥只留在密钥库）。

        ``rotate=True`` 表示"这是同 id 的新实例"：换一枚新密钥并把旧公钥留在
        账本里（旧记录的签名仍可验证）。不轮换时重复登记是幂等的。
        """
        agent = str(agent_id or "").strip()
        owner = str(owner_did or "").strip()
        if not agent:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID", "agent_id 不能为空",
                                   field="agent_id")
        if not AGENT_ID_RE.match(agent) or agent.split(".")[0].lower() in WINDOWS_RESERVED:
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                "agent_id 只允许字母数字与 . _ : -（1–120 位），且不能是 Windows 保留名",
                field="agent_id", detail={"received": agent[:120]})
        owner = _require_record_did(owner, field="owner_did")
        kind_text = str(kind or "agent").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", kind_text):
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "kind 只允许小写字母开头的标识（≤32 位）", field="kind")
        image = str(image_hash).strip() if image_hash else None
        if image is not None and (len(image) > 200 or " " in image or "\n" in image):
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "image_hash 必须是不含空白、≤200 字符的字符串",
                                   field="image_hash")
        try:
            boot_hash = hash_object(boot_params) if boot_params not in (None, "", {}) else None
        except (TypeError, ValueError) as exc:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   f"boot_params 必须是可规范序列化的 JSON 值：{exc}",
                                   field="boot_params") from exc
        previous = self.identity(agent)
        if previous is not None and not rotate:
            # 幂等：同 id、同所属人、同镜像/启动参数、同种类 → 返回原身份，**不追加新行**。
            same = (owner == str(previous.get("owner_did") or "")
                    and image == previous.get("image_hash")
                    and boot_hash == previous.get("boot_params_hash")
                    and kind_text == str(previous.get("kind") or "agent"))
            if same and not previous.get("revoked_at"):
                return self.public_identity(previous)
            if same and previous.get("revoked_at"):
                # 信息一模一样，唯一的事实是"这枚身份已经吊销"：话要说准，
                # 不能报"信息不同"。码与状态保持 FIELD_INVALID/400（T2 的专属码只有这一个），
                # 吊销是终局，重登记不复活；要新实例必须显式 rotate=True。
                raise SecretariatError(
                    "SECRETARIAT_FIELD_INVALID",
                    "这个 agent_id 的身份已被吊销：重登记不会复活它。"
                    "确需新实例请显式 rotate=True（等于新一代实例与新密钥）",
                    field="rotate",
                    detail={"agent_id": agent, "revoked_at": previous.get("revoked_at")})
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                "这个 agent_id 已有身份且信息不同：换实例请显式 rotate=True，"
                "改所属人请走委托而不是重登记",
                field="rotate", detail={"agent_id": agent})
        key_id = f"agent:{agent}"
        view = (self.keyring.rotate(key_id, kind=kind_text)
                if (rotate and previous is not None)
                else self.keyring.ensure(key_id, kind=kind_text))
        record = {
            "id": new_id("agt"),
            "agent_id": agent,
            "kind": kind_text,
            "pubkey": view.get("pubkey", ""),
            "fingerprint": view.get("fingerprint", ""),
            "image_hash": image,
            "boot_params_hash": boot_hash,
            "owner_did": owner,
            "created_at": self._now(),
            "revoked_at": None,
            "supersedes": previous.get("entry_hash") if previous else None,
        }
        try:
            stored = self.identities_ledger.append(record)
        except FeatureStoreError as exc:
            raise SecretariatError("SECRETARIAT_WRITE_FAILED",
                                   f"身份账本写不进去：{exc.reason}") from exc
        return self.public_identity(stored)

    def identities(self) -> list[dict[str, Any]]:
        """所有身份的最新状态（同一 Agent 只出最新一条，吊销也算最新）。"""
        latest: dict[str, dict[str, Any]] = {}
        for entry in self.identities_ledger.load():
            agent = str(entry.get("agent_id") or "")
            if not agent:
                continue
            latest[agent] = entry
        return [self.public_identity(entry) for entry in
                sorted(latest.values(), key=lambda item: str(item.get("agent_id")))]

    def identity(self, agent_id: str) -> dict[str, Any] | None:
        """某 Agent 的最新身份记录（含公钥；绝不含私钥）。"""
        wanted = str(agent_id or "")
        found: dict[str, Any] | None = None
        for entry in self.identities_ledger.load():
            if str(entry.get("agent_id") or "") == wanted:
                found = entry
        return self.public_identity(found) if found else None

    def revoke_identity(self, agent_id: str, *, reason: str = "") -> dict[str, Any]:
        """吊销：**追加**一条带 ``revoked_at`` 的记录，绝不删历史。

        已经吊销过就返回当前记录，不重复追加（幂等），否则同一件事会在账本里
        留两条“吊销”，责任时间线会读起来像吊销了两次。
        """
        current = self.identity(agent_id)
        if current is None:
            raise SecretariatError("SECRETARIAT_IDENTITY_NOT_FOUND",
                                   f"没有这个 Agent 的身份：{agent_id}",
                                   field="agent_id")
        if current.get("revoked_at"):
            return current
        record = dict(current)
        record.pop("seq", None)
        record.pop("prev_hash", None)
        record.pop("entry_hash", None)
        record["id"] = new_id("agt")
        record["created_at"] = self._now()
        record["revoked_at"] = self._now()
        record["revoke_reason"] = str(reason or "")[:200]
        record["supersedes"] = current.get("entry_hash")
        try:
            stored = self.identities_ledger.append(record)
        except FeatureStoreError as exc:
            raise SecretariatError("SECRETARIAT_WRITE_FAILED",
                                   f"身份账本写不进去：{exc.reason}") from exc
        return self.public_identity(stored)

    @staticmethod
    def public_identity(record: Mapping[str, Any] | None) -> dict[str, Any]:
        """对外视图：去掉任何私钥材料，只留可验证的公开字段。"""
        if not isinstance(record, Mapping):
            return {}
        out = {key: value for key, value in record.items()
               if key not in ("seed", "private_key", "secret", "privkey")}
        if "pubkey" in out and "fingerprint" not in out:
            out["fingerprint"] = sha256_hex(str(out.get("pubkey") or ""))[:16]
        return out

    def fingerprint(self, agent_id: str) -> str:
        found = self.identity(agent_id)
        return str(found.get("fingerprint") or "") if found else ""

    def _resolve_identity(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """取（必要时自动签发）记录所属 Agent 的身份。"""
        agent = str(record.get("agent_id") or "").strip()
        if not agent:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "记录没有 agent_id，无法确定由谁签名", field="agent_id")
        found = self.identity(agent)
        if found is None:
            if not self.auto_register:
                raise SecretariatError("SECRETARIAT_IDENTITY_MISSING",
                                       f"这个 Agent 还没登记身份：{agent}",
                                       field="agent_id")
            owner = str(record.get("human_owner") or "").strip() or \
                cs.did_from_name("local", kind="user")
            return self.register_identity(agent_id=agent, owner_did=owner)
        return found

    # -- 行为签名 -----------------------------------------------------
    def sign_record(self, record: Mapping[str, Any], *, risk_level: str | None = None,
                    approvers: Sequence[Mapping[str, Any] | str] | None = None,
                    previous: Mapping[str, Any] | None = None,
                    allow_unapproved: bool = False) -> dict[str, Any]:
        """给一条 ActionRecord 签名，返回**新字典**（绝不就地修改入参）。

        签名链路：``input_hash/output_hash`` → ``prev_hash``（该 Agent 上一条） →
        ``entry_hash``（覆盖整条记录体） → ``signature``（覆盖 ``SIGNATURE_FIELDS``）。
        """
        if not isinstance(record, Mapping):
            raise SecretariatError("SECRETARIAT_FIELD_INVALID", "记录必须是对象")
        if record.get("signature"):
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "这条记录已经签过名：不重复签，避免责任歧义",
                                   field="signature")
        identity = self._resolve_identity(record)
        if identity.get("revoked_at"):
            raise SecretariatError("SECRETARIAT_IDENTITY_REVOKED",
                                   f"这个 Agent 的身份已被吊销（{identity['revoked_at']}）",
                                   field="agent_id")
        agent = str(record.get("agent_id") or "")
        level = str(risk_level or record.get("risk_level") or "low").strip().lower()
        policy = risk_requirements(level)

        out = dict(record)
        # Q1：风险等级先归一化再写回记录，避免"签名时说 high、记录里还是 low"。
        out["risk_level"] = level
        out["agent_pubkey"] = str(identity.get("pubkey") or "")
        owner = str(identity.get("owner_did") or "")
        # 入参自称的所属人必须与账本一致：不允许导入方替别人派责任。
        claimed_owner = str(record.get("human_owner") or "").strip()
        if claimed_owner and claimed_owner != owner:
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                "human_owner 与身份账本不一致：责任链的根不能由记录自己声明",
                field="human_owner", detail={"ledger": owner, "record": claimed_owner})
        out["human_owner"] = owner
        instance_did = agent_did_of(out["agent_pubkey"])
        if not instance_did:
            raise SecretariatError("SECRETARIAT_KEYSTORE_UNAVAILABLE",
                                   "身份没有可用公钥，派生不出本实例 DID",
                                   detail={"agent_id": agent})
        chain = record.get("delegation_chain")
        if chain is None:
            chain = [owner, instance_did]
        out["delegation_chain"] = self._validated_chain(
            chain, owner=owner, agent_did=instance_did)
        # 规格书表 3 的签名字段叫 `tool`（工具名字符串），产品既有的记录用
        # `tool_used`（对象，含 name/version）。两者都要能验证，所以签名前把
        # 工具名落到 `tool`（可选字段），避免"签了名却不知道签的是哪个工具调用"。
        if not out.get("tool"):
            tool_used = record.get("tool_used")
            if isinstance(tool_used, Mapping) and tool_used.get("name"):
                out["tool"] = str(tool_used.get("name"))
            elif isinstance(tool_used, str) and tool_used:
                out["tool"] = tool_used
        out.setdefault("tool", None)
        tool_value = out.get("tool")
        if tool_value is not None and len(str(tool_value)) > TOOL_NAME_MAX:
            # 签名器不裁也不留：tool 是签名域的一员，裁掉等于签一个"记录里看不见的值"，
            # 留着则这条已签名记录过不了自家 schema（maxLength 120）→ 直接拒签。
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                f"tool 超过 {TOOL_NAME_MAX} 个字符：schema 的上限就是 {TOOL_NAME_MAX}，"
                "而 tool 进签名域与 entry_hash，超限值签出来这条记录当场不合规",
                field="tool",
                detail={"length": len(str(tool_value)), "limit": TOOL_NAME_MAX})
        out["input_hash"] = self._hash_of(record, ("context", "input"))
        out["output_hash"] = self._hash_of(record, ("tool_result",)) or \
            self._hash_of(record, ("result_summary",))
        previous = previous if isinstance(previous, Mapping) else None
        if previous is not None and previous.get("entry_hash"):
            out["prev_hash"] = str(previous.get("entry_hash"))
        else:
            out["prev_hash"] = self._head_of(agent)

        # 审批对象必须在算 entry_hash 之前成形（entry_hash 覆盖 approval）。
        # critical 的盖章 digest 用的是"签名事实归约"（不含 entry_hash，防自引用）。
        approval = self._build_approval(level, policy, approvers or [],
                                        record=out, allow_unapproved=allow_unapproved)
        if approval.get("required") or approval.get("approvers") or approval.get("seal"):
            out["approval"] = approval

        body = {key: value for key, value in out.items()
                if key not in ("signature", "tsa_timestamp", "entry_hash")}
        out["entry_hash"] = hash_object(body)
        payload = cs.signed_payload(out, SIGNATURE_FIELDS)
        try:
            signature = cs.sign(self.keyring.seed_of(f"agent:{agent}"), payload)
        except (ValueError, TypeError) as exc:  # pragma: no cover - 密钥库损坏
            raise SecretariatError("SECRETARIAT_KEYSTORE_UNAVAILABLE",
                                   f"取不到这个 Agent 的密钥：{exc}",
                                   detail={"agent_id": agent}) from exc
        out["signature"] = cs.b64e(signature)
        out["tsa_timestamp"] = f"{TSA_LOCAL_PREFIX}{self._now()}"
        self._set_head(agent, str(out["entry_hash"]))
        self.signed += 1
        return out

    @staticmethod
    def _validated_chain(chain: Any, *, owner: str, agent_did: str) -> list[str]:
        """责任链只接受 DID：链根是记账的人，链尾是本 Agent 实例的 DID。

        错误码按 docs/12 §3 冻结表 C 的分工（B07 收口）：
        链根不等于账本里的 ``owner_did`` → ``SECRETARIAT_FIELD_INVALID``（field=human_owner），
        因为它本质是"入参替责任链的根自己指认了一个人"；链的结构问题（长度、重复段、
        链尾）→ ``SECRETARIAT_DELEGATION_INVALID``。
        """
        if not isinstance(chain, (list, tuple)):
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "delegation_chain 必须是 DID 数组",
                                   field="delegation_chain")
        items = [_require_record_did(item, field="delegation_chain") for item in chain]
        if len(items) < 2:
            raise SecretariatError("SECRETARIAT_DELEGATION_INVALID",
                                   "责任链至少要有『人 → 本 Agent』两段",
                                   field="delegation_chain")
        if len(items) > 8:
            raise SecretariatError("SECRETARIAT_DELEGATION_INVALID",
                                   "责任链最多 8 个 DID（深度上限 5 → 最多 6 段，留 2 格余量）",
                                   field="delegation_chain", detail={"length": len(items)})
        duplicates = sorted({item for item in items if items.count(item) > 1})
        if duplicates:
            raise SecretariatError(
                "SECRETARIAT_DELEGATION_INVALID",
                "责任链里同一段 DID 不能重复出现：同一个人/实例重复出现等于凭空多出一层委托",
                field="delegation_chain", detail={"duplicated": duplicates[:4]})
        if items[0] != owner:
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                "责任链的根必须是人（身份账本里的 owner_did）：链根不能由入参自己指认",
                field="human_owner",
                detail={"root": items[0], "owner": owner})
        if items[-1] != agent_did:
            raise SecretariatError("SECRETARIAT_DELEGATION_INVALID",
                                   "责任链的尾必须是本 Agent 实例的 DID",
                                   field="delegation_chain",
                                   detail={"tail": items[-1], "agent_did": agent_did})
        return items

    def sign_chain(self, records: Sequence[Mapping[str, Any]], *,
                   allow_unapproved: bool = False) -> list[dict[str, Any]]:
        """批量签名：按时间顺序给每条记录接上同 Agent 的前一条。"""
        ordered = sorted(enumerate(records),
                         key=lambda pair: (self._ts_key(pair[1]), pair[0]))
        heads: dict[str, Mapping[str, Any]] = {}
        out: list[dict[str, Any]] = [{} for _ in records]
        for index, record in ordered:
            agent = str(record.get("agent_id") or "")
            previous = heads.get(agent)
            signed = self.sign_record(record, previous=previous,
                                      allow_unapproved=allow_unapproved)
            heads[agent] = signed
            out[index] = signed
        return out

    def verify_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """逐项复算：身份、签名、记录哈希、前序哈希、时间戳、责任链、审批、盖章。"""
        problems: list[dict[str, Any]] = []
        checks: dict[str, Any] = {}
        if not isinstance(record, Mapping):
            return {"ok": False, "checks": {}, "tsa_state": "missing",
                    "problems": [{"code": "SECRETARIAT_FIELD_INVALID",
                                  "detail": {"reason": "record is not an object"}}]}
        agent = str(record.get("agent_id") or "")
        identity = self.identity(agent) if agent else None
        history = self._pubkeys_of(agent) if agent else set()
        pub_b64 = str(record.get("agent_pubkey") or "")
        if identity and identity.get("pubkey"):
            if not pub_b64:
                checks["identity"] = "registered"
            elif pub_b64 in history or pub_b64 == identity["pubkey"]:
                checks["identity"] = ("registered" if pub_b64 == identity["pubkey"]
                                      else "historical")
            else:
                checks["identity"] = "mismatch"
                problems.append({"code": "SECRETARIAT_IDENTITY_MISMATCH",
                                 "detail": {"registered": identity["pubkey"],
                                            "recorded": pub_b64}})
        else:
            checks["identity"] = "unregistered"
        signature = str(record.get("signature") or "")
        if not signature:
            checks["signature"] = "absent"
            problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                             "detail": {"reason": "no signature on this record"}})
        else:
            payload = cs.signed_payload(record, SIGNATURE_FIELDS)
            ok = False
            try:
                ok = cs.verify_b64(pub_b64, payload, signature) if pub_b64 else False
            except (ValueError, TypeError):
                ok = False
            if not ok and identity and identity.get("pubkey"):
                try:
                    ok = cs.verify_b64(str(identity["pubkey"]), payload, signature)
                except (ValueError, TypeError):
                    ok = False
            checks["signature"] = "valid" if ok else "invalid"
            if not ok:
                problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                                 "detail": {"agent_id": agent}})
        expected_entry = record.get("entry_hash")
        body = {key: value for key, value in record.items()
                if key not in ("signature", "tsa_timestamp", "entry_hash")}
        recomputed = hash_object(body)
        checks["entry_hash"] = "valid" if expected_entry == recomputed else "invalid"
        if expected_entry != recomputed:
            problems.append({"code": "SECRETARIAT_HASH_MISMATCH",
                             "detail": {"recorded": expected_entry, "recomputed": recomputed}})
        recorded_prev = str(record.get("prev_hash") or "")
        if not recorded_prev:
            checks["prev_hash"] = "absent"
        elif recorded_prev == GENESIS_HASH:
            checks["prev_hash"] = "genesis"
        else:
            known = self._known_hashes(agent)
            if recorded_prev in known:
                checks["prev_hash"] = "continuous"
            elif known:
                checks["prev_hash"] = "unverified"
            else:
                checks["prev_hash"] = "unverifiable_without_chain"
        stamp = str(record.get("tsa_timestamp") or "")
        if not stamp:
            checks["tsa"] = "absent"
        elif stamp.startswith(TSA_LOCAL_PREFIX):
            checks["tsa"] = "local_only"
        else:
            checks["tsa"] = "unknown"
        # 责任链：根是人、尾是本实例 DID、每项都是 DID。
        chain = record.get("delegation_chain")
        owner = str(record.get("human_owner") or "")
        expected_tail = agent_did_of(pub_b64)
        if not isinstance(chain, (list, tuple)) or not chain:
            checks["chain"] = "absent"
        else:
            bad = [str(item) for item in chain if not RECORD_DID_RE.match(str(item))]
            if bad:
                checks["chain"] = "invalid"
                problems.append({"code": "SECRETARIAT_DELEGATION_INVALID",
                                 "detail": {"scope": "delegation_chain", "bad": bad[:4]}})
            elif owner and str(chain[0]) != owner:
                checks["chain"] = "invalid"
                problems.append({"code": "SECRETARIAT_DELEGATION_INVALID",
                                 "detail": {"scope": "chain_root", "root": str(chain[0]),
                                            "human_owner": owner}})
            elif expected_tail and str(chain[-1]) != expected_tail:
                checks["chain"] = "invalid"
                problems.append({"code": "SECRETARIAT_DELEGATION_INVALID",
                                 "detail": {"scope": "chain_tail", "tail": str(chain[-1]),
                                            "agent_did": expected_tail}})
            else:
                checks["chain"] = "valid"
        approval = record.get("approval") if isinstance(record.get("approval"), Mapping) else {}
        required = int(approval.get("required") or 0)
        given = approval.get("approvers") if isinstance(approval.get("approvers"), list) else []
        dids = [str(item.get("did") or "") for item in given if isinstance(item, Mapping)]
        distinct = len(set(d for d in dids if d))
        signatures_ok = True
        if given:
            signatures_ok = self._verify_approvers(record, given, required=required,
                                                   distinct=distinct, problems=problems)
        approval_ok = (len(given) >= required and distinct >= required and signatures_ok)
        checks["approval"] = {"required": required, "given": len(given),
                              "distinct": distinct, "signatures_ok": signatures_ok,
                              "ok": approval_ok}
        if len(given) < required:
            problems.append({"code": "SECRETARIAT_APPROVAL_REQUIRED",
                             "detail": {"required": required, "given": len(given)}})
        elif distinct < required:
            problems.append({"code": "SECRETARIAT_APPROVAL_REQUIRED",
                             "detail": {"required": required, "distinct": distinct,
                                        "reason": "approvers must be different DIDs"}})
        # 盖章：critical 必须 sealed；sealed 时验 seal 签名与 digest 绑定。
        policy = RISK_POLICY.get(str(record.get("risk_level") or "low").lower(), {})
        needs_seal = bool(policy.get("seal"))
        seal = approval.get("seal") if isinstance(approval.get("seal"), Mapping) else None
        sealed = bool(approval.get("sealed"))
        if needs_seal and not sealed:
            checks["seal"] = "absent"
            problems.append({"code": "SECRETARIAT_APPROVAL_REQUIRED",
                             "detail": {"scope": "seal", "risk_level": record.get("risk_level")}})
        elif sealed and seal is None:
            checks["seal"] = "invalid"
            problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                             "detail": {"scope": "seal", "reason": "sealed without seal object"}})
        elif seal is not None:
            digest_ok = str(seal.get("digest") or "") == approval_digest(record)
            seal_ok = digest_ok and self.verify_seal(seal)
            checks["seal"] = "valid" if seal_ok else "invalid"
            if not seal_ok:
                problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                                 "detail": {"scope": "seal", "digest_ok": digest_ok}})
        else:
            checks["seal"] = "not_required"
        return {"ok": not problems, "checks": checks, "problems": problems,
                "tsa_state": checks.get("tsa", "absent"), "agent_id": agent}

    def _verify_approvers(self, record: Mapping[str, Any], entries: list[Any], *,
                          required: int, distinct: int,
                          problems: list[dict[str, Any]]) -> bool:
        """逐个复算审批签名；公钥不在本地密钥库时如实标"无法验证"。"""
        base = {
            "record_id": str(record.get("id") or ""),
            "action_type": str(record.get("action_type") or ""),
            "input_hash": record.get("input_hash"),
            "output_hash": record.get("output_hash"),
            "risk_level": str(record.get("risk_level") or ""),
            "required": required,
        }
        ok_all = True
        for entry in entries:
            if not isinstance(entry, Mapping):
                ok_all = False
                continue
            did = str(entry.get("did") or "")
            payload = dict(base, did=did, role=str(entry.get("role") or ""),
                           signed_at=str(entry.get("signed_at") or ""))
            signature = str(entry.get("signature") or "")
            view = self.keyring.public_view(f"user:{did}") if did else None
            pub = str((view or {}).get("pubkey") or "")
            verified = False
            if pub and signature:
                try:
                    verified = cs.verify_b64(pub, cs.canonical_bytes(payload), signature)
                except (ValueError, TypeError):
                    verified = False
            if not verified:
                ok_all = False
                problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                                 "detail": {"scope": "approval", "did": did,
                                            "reason": ("no local key for approver" if not pub
                                                       else "signature does not match")}})
        if distinct < required:
            ok_all = False
        return ok_all

    def seal(self, *, subject: str, digest: str, reason: str = "") -> dict[str, Any]:
        """总裁办盖章：用总裁办自己的密钥对 ``subject+digest`` 签发。"""
        if not str(subject or "").strip() or not str(digest or "").strip():
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "盖章必须给 subject 与 digest", field="subject")
        view = self.keyring.ensure(self._seal_key(), kind="secretariat")
        body = {"subject": str(subject), "digest": str(digest),
                "reason": str(reason or ""), "sealed_at": self._now(),
                "pubkey": view.get("pubkey", ""), "fingerprint": view.get("fingerprint", "")}
        signature = cs.sign(self.keyring.seed_of(self._seal_key()), cs.canonical_bytes(body))
        out = dict(body)
        out["seal_id"] = new_id("seal")
        out["signature"] = cs.b64e(signature)
        out["algorithm"] = "ed25519"
        return out

    def verify_seal(self, seal: Mapping[str, Any]) -> bool:
        if not isinstance(seal, Mapping):
            return False
        body = {key: seal.get(key) for key in
                ("subject", "digest", "reason", "sealed_at", "pubkey", "fingerprint")}
        try:
            return cs.verify_b64(str(body.get("pubkey") or ""), cs.canonical_bytes(body),
                                 str(seal.get("signature") or ""))
        except (ValueError, TypeError):
            return False

    # -- 责任归属（委托链）-------------------------------------------
    def create_delegation(self, *, delegator_did: str, delegatee_did: str,
                          scope: Mapping[str, Any], ttl_seconds: int | None = None,
                          parent_id: str | None = None) -> dict[str, Any]:
        """生成一条委托：权限只能收窄，深度不得超过 5 层，过期授权不能当新委托的根。

        R2 收口的四条硬约束（都在 docs/12 §3 冻结表 C 的既有错误码族内，不新增码）：

        * **不能自委托**：委托人与受托人是同一个 DID 时，责任链凭空多出一层，
          深度上限变成"自己刷层数"的工具 → ``SECRETARIAT_DELEGATION_INVALID`` / 400。
        * **已吊销的实例不能作为委托任何一方**：吊销的语义是终局（A3），
          给已吊销的实例派委托等于把责任写进黑洞 → ``SECRETARIAT_DELEGATION_INVALID`` / 400。
        * **父委托必须未过期**：过期授权重新长出子委托 = 变相把过期授权续期。
        * ``ttl_seconds`` 只接受整数秒；坏类型给 400，不让它冒成 500。
        """
        delegator_did = _require_loose_did(delegator_did, field="delegator_did")
        delegatee_did = _require_loose_did(delegatee_did, field="delegatee_did")
        if str(delegator_did) == str(delegatee_did):
            raise SecretariatError(
                "SECRETARIAT_DELEGATION_INVALID",
                "委托人不能是自己：同 DID 自委托会让责任链凭空多出一段，"
                "追不出这一层新增的受托方",
                field="delegatee_did",
                detail={"delegator_did": delegator_did,
                        "delegatee_did": delegatee_did, "reason": "self_delegation"})
        if str(delegator_did).startswith("did:tracelens:agent:"):
            source, revoked = self._agent_did_source(delegator_did)
            if source is None:
                raise SecretariatError(
                    "SECRETARIAT_DELEGATION_INVALID",
                    "委托人不是已登记或已受托的 Agent：责任链会凭空多出一段",
                    field="delegator_did", detail={"delegator_did": delegator_did})
            if revoked:
                raise SecretariatError(
                    "SECRETARIAT_DELEGATION_INVALID",
                    "委托人这个 Agent 实例的身份已被吊销：吊销是终局，"
                    "不能再以它的名义往下派责任",
                    field="delegator_did",
                    detail={"delegator_did": delegator_did, "reason": "delegator_revoked"})
        _, delegatee_revoked = self._agent_did_source(delegatee_did)
        if delegatee_revoked:
            raise SecretariatError(
                "SECRETARIAT_DELEGATION_INVALID",
                "受托人这个 Agent 实例的身份已被吊销：给已吊销的实例派委托"
                "等于把责任写进黑洞",
                field="delegatee_did",
                detail={"delegatee_did": delegatee_did, "reason": "delegatee_revoked"})
        parent: dict[str, Any] | None = None
        depth = 1
        issued = _parse_iso(self._now()) or _dt.datetime.now(_dt.timezone.utc)
        if parent_id:
            parent = self.delegation(parent_id)
            if parent is None:
                raise SecretariatError("SECRETARIAT_DELEGATION_INVALID",
                                       f"找不到父委托：{parent_id}", field="parent_id")
            depth = int(parent.get("depth") or 1) + 1
            if depth > MAX_DELEGATION_DEPTH:
                raise SecretariatError(
                    "SECRETARIAT_DEPTH_EXCEEDED",
                    f"委托链最大 {MAX_DELEGATION_DEPTH} 层，再往下就追不清责任了",
                    field="parent_id", detail={"depth": depth, "limit": MAX_DELEGATION_DEPTH})
            if str(parent.get("delegatee_did")) != str(delegator_did):
                raise SecretariatError(
                    "SECRETARIAT_DELEGATION_INVALID",
                    "委托人不是父委托的受托人：责任链会断",
                    field="delegator_did",
                    detail={"parent_delegatee": parent.get("delegatee_did")})
            parent_expires = _parse_iso(parent.get("expires_at"))
            if parent_expires is not None and parent_expires < issued:
                raise SecretariatError(
                    "SECRETARIAT_DELEGATION_INVALID",
                    "父委托已过期：过期授权不能再长出新的委托，否则等于替过期授权续期",
                    field="parent_id",
                    detail={"parent_id": str(parent_id),
                            "expires_at": parent.get("expires_at"),
                            "reason": "parent_expired"})
        normalised = self._normalise_scope(scope)
        if parent is not None:
            self._check_narrowing(self._normalise_scope(parent.get("scope") or {}), normalised)
        if not str(delegator_did or "").strip() or not str(delegatee_did or "").strip():
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "委托人和受托人都不能为空", field="delegator_did")
        ttl = _coerce_ttl(ttl_seconds)
        if ttl <= 0 or ttl > 366 * 86400:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   "有效期必须在 1 秒到 366 天之间", field="ttl_seconds")
        expires = _iso(issued + _dt.timedelta(seconds=ttl))
        body = {
            "id": new_id("dlg"),
            "delegator_did": str(delegator_did),
            "delegatee_did": str(delegatee_did),
            "scope": normalised,
            "depth": depth,
            "parent_id": str(parent_id) if parent_id else None,
            "issued_at": _iso(issued),
            "expires_at": expires,
            "mode": self.mode,
        }
        key_id = f"user:{body['delegator_did']}"
        identity = self._identity_for_agent_did(body["delegator_did"])
        if identity is not None and not identity.get("revoked_at"):
            # 委托人是一个**已登记实例**：用该实例自己的密钥签这条委托，公钥可与身份账本对上。
            # 否则"委托签名"只是替委托人现造一枚本地钥匙签的自证，证明不了委托人签过。
            key_id = f"agent:{identity.get('agent_id')}"
            body["signer"] = {"kind": "agent_identity",
                              "agent_id": str(identity.get("agent_id") or ""),
                              "delegator_did": body["delegator_did"]}
            self.keyring.ensure(key_id, kind="agent")
        else:
            body["signer"] = {"kind": "local_key",
                              "delegator_did": body["delegator_did"],
                              "note": "委托人不是已登记实例：签的是本地为该 DID 生成的密钥，"
                                      "只证明这条委托在本地被记录，不证明委托人的真实意愿"}
            self.keyring.ensure(key_id, kind="user")
        body["signature"] = cs.b64e(
            cs.sign(self.keyring.seed_of(key_id), cs.canonical_bytes(body)))
        body["pubkey"] = self.keyring.public_view(key_id)["pubkey"]
        try:
            stored = self.delegations_ledger.append(body)
        except FeatureStoreError as exc:
            raise SecretariatError("SECRETARIAT_WRITE_FAILED",
                                   f"委托账本写不进去：{exc.reason}") from exc
        return self.public_delegation(stored)

    def delegations(self) -> list[dict[str, Any]]:
        return [self.public_delegation(entry) for entry in self.delegations_ledger.load()]

    def delegation(self, delegation_id: str) -> dict[str, Any] | None:
        for entry in self.delegations_ledger.load():
            if str(entry.get("id") or "") == str(delegation_id):
                return self.public_delegation(entry)
        return None

    @staticmethod
    def public_delegation(record: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            return {}
        return {key: value for key, value in record.items()
                if key not in ("seed", "private_key", "secret")}

    def delegation_chain_of(self, delegation_id: str) -> list[dict[str, Any]]:
        """从根到这条委托的链（人 → 主 Agent → …）。"""
        chain: list[dict[str, Any]] = []
        current = self.delegation(delegation_id)
        guard = 0
        while current and guard < MAX_DELEGATION_DEPTH + 2:
            chain.append(current)
            parent_id = current.get("parent_id")
            current = self.delegation(str(parent_id)) if parent_id else None
            guard += 1
        chain.reverse()
        return chain

    def _known_agent_did(self, did: str) -> bool:
        """某个 Agent DID 是否“有出处”：已登记实例，或某条委托的受托人。"""
        source, _revoked = self._agent_did_source(did)
        return source is not None

    def _identity_for_agent_did(self, did: str) -> dict[str, Any] | None:
        """按**实例 DID** 找回登记身份：账本里最后一条带这枚公钥的记录（公钥 → DID）。

        轮换写入新一代公钥、吊销写入当时那一代公钥 + ``revoked_at``，所以"这一代实例
        有没有被吊销"必须按公钥逐代看，不能只看 Agent 的最新一条身份记录。
        """
        wanted = str(did or "").strip()
        if not wanted:
            return None
        found: dict[str, Any] | None = None
        for entry in self.identities_ledger.load():
            if agent_did_of(str(entry.get("pubkey") or "")) == wanted:
                found = entry          # 后写的算这一代的终态
        return found

    def _agent_did_source(self, did: str) -> tuple[str | None, bool]:
        """Agent DID 的出处与吊销状态：``("identity", revoked)`` / ``("delegation", False)`` / ``(None, False)``。"""
        wanted = str(did or "").strip()
        if not wanted:
            return None, False
        identity = self._identity_for_agent_did(wanted)
        if identity is not None:
            return "identity", bool(identity.get("revoked_at"))
        for entry in self.delegations_ledger.load():
            if str(entry.get("delegatee_did") or "") == wanted:
                return "delegation", False
        return None, False

    def authorise(self, delegation_id: str, *, action_type: str,
                  tool: str | None = None, risk_level: str = "low") -> dict[str, Any]:
        """按委托链判定某动作是否被授权（收窄原则逐层检查）。

        过期判定覆盖**整条链**：叶子委托没过期、但它的某一代父委托已过期时同样拒绝
        （``reason`` 仍是 ``SECRETARIAT_DELEGATION_EXPIRED``，``detail.scope="ancestor"``
        说清是哪一层过期）。授权不能从过期的根上长出来。
        """
        delegation = self.delegation(delegation_id)
        if delegation is None:
            raise SecretariatError("SECRETARIAT_DELEGATION_INVALID",
                                   f"找不到这条委托：{delegation_id}",
                                   field="delegation_id")
        expires = _parse_iso(delegation.get("expires_at"))
        now = _parse_iso(self._now()) or _dt.datetime.now(_dt.timezone.utc)
        if expires is not None and expires < now:
            return {"allowed": False, "reason": "SECRETARIAT_DELEGATION_EXPIRED",
                    "depth": delegation.get("depth"), "delegation": delegation}
        for ancestor in self.delegation_chain_of(delegation_id)[:-1]:
            ancestor_expires = _parse_iso(ancestor.get("expires_at"))
            if ancestor_expires is not None and ancestor_expires < now:
                return {"allowed": False, "reason": "SECRETARIAT_DELEGATION_EXPIRED",
                        "depth": delegation.get("depth"), "delegation": delegation,
                        "detail": {"scope": "ancestor",
                                   "delegation_id": ancestor.get("id"),
                                   "expires_at": ancestor.get("expires_at")}}
        scope = self._normalise_scope(delegation.get("scope") or {})
        allowed_types = scope.get("action_types") or []
        allowed_tools = scope.get("tools") or []
        max_risk = scope.get("max_risk") or "low"
        if allowed_types and str(action_type) not in allowed_types:
            return {"allowed": False, "reason": "SECRETARIAT_SCOPE_INVALID",
                    "detail": {"action_type": action_type, "allowed": allowed_types},
                    "delegation": delegation}
        if allowed_tools and tool and str(tool) not in allowed_tools:
            return {"allowed": False, "reason": "SECRETARIAT_SCOPE_INVALID",
                    "detail": {"tool": tool, "allowed": allowed_tools},
                    "delegation": delegation}
        if RISK_ORDER.get(str(risk_level)) is None:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   f"不认识的风险等级 {risk_level!r}", field="risk_level")
        if RISK_ORDER[str(risk_level)] > RISK_ORDER.get(str(max_risk), 1):
            return {"allowed": False, "reason": "SECRETARIAT_SCOPE_INVALID",
                    "detail": {"risk_level": risk_level, "max_risk": max_risk},
                    "delegation": delegation}
        return {"allowed": True, "reason": "ok", "depth": delegation.get("depth"),
                "scope": scope, "delegation": delegation}

    # -- 争议裁定（责任认定书）---------------------------------------
    def build_letter(self, *, subject: str, records: Sequence[Mapping[str, Any]],
                     responsibility: str = "technical",
                     period: Mapping[str, Any] | None = None,
                     note: str = "", audit_head: str | None = None,
                     collab_events: Sequence[Mapping[str, Any]] | None = None,
                     collab_window: Mapping[str, Any] | None = None,
                     ) -> dict[str, Any]:
        """生成可离线验证的责任认定书，并落盘 + 进索引账本。

        ``collab_events``（R5-2 / B5-3「代行」渲染增补，**只读**）：调用方把协作层
        ``ShareService.events()`` 的协作事件递进来。带 ``detail.on_behalf_of`` 标注的事件
        会让对应的动作条目多一行「由 X 代行 Y 行使权限，最终责任归 Y（human_owner）」
        （X = 事件 ``actor_did`` = 实际操作人；Y = **记录里的** ``human_owner``）。

        ``collab_window``（R5-3 / T7 接线，**只读**的取数自述）：调用方把"这次只看了账本
        最近多少条、账本一共多少条"作为事实递进来，形状
        ``{"limit": 500, "total": 12345, "returned": 500, "complete": False}``。
        ``complete`` 不为 ``True`` 时，认定书在 ``verification.problems`` 里如实登记一条
        （scope = :data:`COLLAB_EVENTS_SCOPE`）：**窗口外的代行标注不参与本次渲染**，
        这份认定书不是"看过了全部协作记录"的结论。不传 → 认定书逐字不变（既有调用零变化）。
        分页取数能力本仓没有（``ShareService.events`` 只有 ``limit``），按 PM-VERDICT-R5-2
        §3.2 的口径**从简登记**，分页列 backlog。
        
        三条边界（都是可测的）：

        * **不传 / 传空 → 认定书逐字不变**：动作条目一个键都不多，键序、哈希、
          ``records_root`` 与改前完全一致（离线复算口径不动）；
        * **代行标注只进渲染层**：``human_owner`` 字段不动、``delegation_chain`` 结构不动、
          签名域（``SIGNATURE_FIELDS`` 11 字段）一字不动 —— 最终责任永远归真人，
          事件层说的"谁做的"在这里只加一句人话，不改变任何已签名的字；
        * **标注与责任根冲突不静默**：事件里的 ``on_behalf_of`` 与记录的 ``human_owner``
          不一致时，认定书按口径把责任写在 ``human_owner`` 上，并把这次不一致登记进
          ``verification.problems``（"记了日志但责任归属说不清"正是 REQ-005 点名要防的）。
        """
        title = str(subject or "").strip()
        if not title:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID", "认定书需要一个主题",
                                   field="subject")
        role = str(responsibility or "technical").strip().lower()
        if role not in ROLES:
            raise SecretariatError("SECRETARIAT_FIELD_INVALID",
                                   f"责任判定只能是 {', '.join(ROLES)}", field="responsibility")
        items = [dict(record) for record in records if isinstance(record, Mapping)]
        if not items:
            raise SecretariatError("SECRETARIAT_LETTER_EMPTY",
                                   "认定书没有涉及任何记录：先选记录再生成",
                                   field="record_ids")
        verified = [(item, self.verify_record(item)) for item in items]
        # 先成形“要写进认定书的动作条目”，再算 records_root：离线复核只会拿到
        # 这些条目（不是原记录全量），两边必须用同一份输入复算，否则根哈希必然对不上。
        action_items = [{
            "id": str(item.get("id") or ""),
            "timestamp": str(item.get("timestamp") or ""),
            "agent_id": str(item.get("agent_id") or ""),
            "action_type": str(item.get("action_type") or ""),
            "status": str(item.get("status") or ""),
            "risk_level": str(item.get("risk_level") or ""),
            "result_summary": str(item.get("result_summary") or "")[:300],
            # ---- 离线复算所需的最小字段集（docs/12 §5 冻结）----
            # verified/records_root 之外，逐条 Agent 签名也要能被第三方复算：
            # 这 7 个字段 + entry_hash 就是 SIGNATURE_FIELDS 的全部输入。
            "tool": item.get("tool"),
            "input_hash": item.get("input_hash"),
            "output_hash": item.get("output_hash"),
            "prev_hash": item.get("prev_hash"),
            "confidence": item.get("confidence"),
            "signature": item.get("signature"),
            "agent_pubkey": item.get("agent_pubkey"),
            "entry_hash": item.get("entry_hash"),
            "signature_ok": result.get("checks", {}).get("signature") == "valid",
        } for item, result in verified]
        # 「代行」渲染增补（R5-2 / B5-3，只读增补）：**只往有标注的**动作条目上追加三个键，
        # 无标注的条目逐字不变（键序也不动）→ digest 列表与 records_root 的计算路径一字未改。
        # 唯一可见的连带效应：没有 entry_hash 的动作（未签名的记录）走"整条规范 JSON 哈希"，
        # 代行键会进那一次哈希 —— 复算输入就是认定书自带的 actions 本体（同一份东西），
        # 两边同源，所以离线可验证性不破（tests 用 verify_letter 与 tools/verify_letter.py 钉住）。
        on_behalf = on_behalf_index(collab_events)
        on_behalf_problems: list[dict[str, Any]] = []
        for action, (item, _result) in zip(action_items, verified):
            hit = on_behalf.get(str(item.get("id") or ""))
            if not hit:
                continue
            actor = str(hit.get("actor") or "")
            if not actor:
                # 事件里连"谁做的"都没记 → 代行句写不出主语。不猜一个名字填进认定书，
                # 也不静默丢掉：登记一条问题，让复核的人看得见"这里本该有一句代行话"。
                on_behalf_problems.append({
                    "code": "SECRETARIAT_FIELD_INVALID",
                    "detail": {"scope": sharing.ON_BEHALF_KEY,
                               "id": str(item.get("id") or ""),
                               "event_id": str(hit.get("event_id") or ""),
                               "reason": "代行事件没记 actor_did：写不出「由谁代行」，"
                                         "本条不渲染代行句"}})
                continue
            owner = str(item.get("human_owner") or "").strip()
            principal = str(hit.get("on_behalf_of") or "")
            if owner and owner != principal:
                # 责任根只有一个：记录的 human_owner。事件的自述与它对不上时，
                # 认定书按口径写 human_owner，并登记这次不一致，不静默、不改写记录。
                on_behalf_problems.append({
                    "code": "SECRETARIAT_FIELD_INVALID",
                    "detail": {"scope": sharing.ON_BEHALF_KEY,
                               "id": str(item.get("id") or ""),
                               "event_id": str(hit.get("event_id") or ""),
                               "human_owner": owner[:sharing.ON_BEHALF_MAX_LEN],
                               "on_behalf_of": principal[:sharing.ON_BEHALF_MAX_LEN],
                               "reason": "事件里的代行标注与记录的责任根（human_owner）不一致："
                                         "最终责任按冻结口径写在 human_owner 上，"
                                         "这次不一致如实登记"}})
            action["on_behalf_of"] = principal
            action["on_behalf_actor"] = actor
            action["on_behalf_note"] = on_behalf_note(
                actor=actor, principal=(owner or principal))
        digests = [str(action.get("entry_hash") or sha256_hex(cs.canonical_json(action)))
                   for action in action_items]
        records_root = sha256_hex("\n".join(sorted(digests)))
        signatures_ok = all(result.get("checks", {}).get("signature") == "valid"
                            for _, result in verified)
        chain_ok = all(result.get("checks", {}).get("entry_hash") == "valid"
                       for _, result in verified)
        agents: dict[str, dict[str, Any]] = {}
        for item, result in verified:
            agent = str(item.get("agent_id") or "")
            bucket = agents.setdefault(agent, {"agent_id": agent, "actions": 0,
                                               "fingerprint": self.fingerprint(agent),
                                               "human_owner": str(item.get("human_owner") or ""),
                                               "signature_ok": True})
            bucket["actions"] += 1
            bucket["signature_ok"] = bool(bucket["signature_ok"]
                                          and result.get("checks", {}).get("signature") == "valid")
        problems: list[dict[str, Any]] = [
            problem for _, result in verified for problem in result.get("problems", [])][:50]
        # 代行标注的冲突登记（R5-2）：不参与上面的 [:50] 截断 —— 责任归属说不清的条目
        # 不能被一堆别的问题挤出列表；没冲突时这个列表是空的，认定书零变化。
        problems.extend(on_behalf_problems)
        # 协作事件窗口未取全（R5-3 / T7）：认定书必须自己承认"只看了最近这么多条"。
        # 从简登记（PM-VERDICT-R5-2 §3.2）：分页能力缺失不伪装成"看全了"，如实进 problems。
        if isinstance(collab_window, Mapping) and collab_window.get("complete") is not True:
            _taken = collab_window.get("returned")
            _total = collab_window.get("total")
            _cap = collab_window.get("limit")
            problems.append({"code": "SECRETARIAT_FIELD_INVALID",
                             "detail": {"scope": COLLAB_EVENTS_SCOPE,
                                        "reason": COLLAB_EVENTS_WINDOW_REASON.format(
                                            returned=_taken, total=_total, limit=_cap),
                                        "limit": _cap, "total": _total,
                                        "returned": _taken}})
        if not str(audit_head or "").strip():
            # docs/12 §5 尾注：拿不到审计链头时写 "" 并**如实登记**，不得伪造一个链头。
            problems.append({"code": "SECRETARIAT_FIELD_INVALID",
                             "detail": {"scope": "audit_head",
                                        "reason": "调用方未提供审计链头：留空并登记，不伪造"}})
        body = {
            "id": new_id("letter"),
            "kind": "responsibility_letter",
            "version": SECRETARIAT_VERSION,
            "subject": title,
            "created_at": self._now(),
            "mode": self.mode,
            "period": dict(period or {}),
            "note": str(note or "")[:1000],
            "responsibility": {"role": role, "label": ROLE_LABELS.get(role, role),
                               "basis": "recorded_actions_and_signatures"},
            "agents": sorted(agents.values(), key=lambda item: str(item.get("agent_id"))),
            "delegation_chain": self._chain_for_records(items),
            "actions": action_items,
            "verification": {
                "signatures_ok": signatures_ok,
                "chain_ok": chain_ok,
                "problems": problems,
                "tsa_state": "local_only",
            },
            "records_root": records_root,
            "audit_head": str(audit_head or ""),
            "determination": self._determination(role, verified),
        }
        view = self.keyring.ensure(self._seal_key(), kind="secretariat")
        body["public_key"] = view.get("pubkey", "")
        body["fingerprint"] = view.get("fingerprint", "")
        body["signature"] = cs.b64e(
            cs.sign(self.keyring.seed_of(self._seal_key()), cs.canonical_bytes(body)))
        try:
            self.paths.letters_dir.mkdir(parents=True, exist_ok=True)
            self.paths.letter_path(body["id"]).write_text(
                json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.letters_ledger.append({
                "id": body["id"], "subject": title, "created_at": body["created_at"],
                "responsibility": role, "records": len(items),
                "records_root": records_root, "audit_head": body["audit_head"],
                "fingerprint": body["fingerprint"],
                "signature": body["signature"],
            })
        except (OSError, FeatureStoreError) as exc:
            raise SecretariatError("SECRETARIAT_WRITE_FAILED",
                                   f"认定书写不进去：{exc}") from exc
        return body

    def letters(self, *, limit: int = 50) -> list[dict[str, Any]]:
        entries = self.letters_ledger.load()
        return [dict(entry) for entry in entries[-max(1, min(int(limit), 500)):]][::-1]

    def letter(self, letter_id: str) -> dict[str, Any] | None:
        path = self.paths.letter_path(str(letter_id))
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def verify_letter(self, letter: Mapping[str, Any]) -> dict[str, Any]:
        """离线验证：只依赖认定书本体（含公钥），不需要服务端任何状态。"""
        problems: list[dict[str, Any]] = []
        if not isinstance(letter, Mapping):
            return {"ok": False, "problems": [{"code": "SECRETARIAT_FIELD_INVALID"}]}
        body = {key: value for key, value in letter.items() if key != "signature"}
        pub_b64 = str(letter.get("public_key") or "")
        try:
            signature_ok = cs.verify_b64(pub_b64, cs.canonical_bytes(body),
                                         str(letter.get("signature") or ""))
        except (ValueError, TypeError):
            signature_ok = False
        if not signature_ok:
            problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                             "detail": {"scope": "letter"}})
        raw_actions = letter.get("actions") if isinstance(letter.get("actions"), list) else []
        digests = [str(action.get("entry_hash") or sha256_hex(cs.canonical_json(action)))
                   for action in raw_actions]
        recomputed_root = sha256_hex("\n".join(sorted(digests)))
        root_ok = recomputed_root == str(letter.get("records_root") or "")
        if not root_ok:
            problems.append({"code": "SECRETARIAT_HASH_MISMATCH",
                             "detail": {"scope": "records_root",
                                        "recorded": letter.get("records_root"),
                                        "recomputed": recomputed_root}})
        # 逐条动作签名离线复算：认定书自带 agent_pubkey + 最小字段集，不依赖服务端。
        action_checks: list[dict[str, Any]] = []
        for action in raw_actions:
            if not isinstance(action, Mapping):
                action_checks.append({"id": "", "ok": False})
                continue
            action_id = str(action.get("id") or "")
            pub = str(action.get("agent_pubkey") or "")
            signature = str(action.get("signature") or "")
            verified_action = False
            try:
                verified_action = bool(pub and signature) and cs.verify_b64(
                    pub, cs.signed_payload(action, SIGNATURE_FIELDS), signature)
            except (ValueError, TypeError):
                verified_action = False
            action_checks.append({"id": action_id, "ok": verified_action})
            if not verified_action:
                problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                                 "detail": {"scope": "action", "id": action_id}})
        actions_ok = all(item["ok"] for item in action_checks) if action_checks else False
        return {"ok": not problems, "problems": problems,
                "checks": {"signature": signature_ok, "records_root": root_ok,
                           "actions": action_checks, "actions_ok": actions_ok,
                           "public_key": bool(pub_b64)},
                "fingerprint": str(letter.get("fingerprint") or "")}

    # -- 仪表盘 -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        identities = self.identities()
        letters = self.letters(limit=500)
        return {
            "version": SECRETARIAT_VERSION,
            "mode": self.mode,
            "enabled": True,
            "counts": {
                "identities": len(identities),
                "active_identities": len([item for item in identities
                                          if not item.get("revoked_at")]),
                "delegations": len(self.delegations()),
                "letters": len(letters),
                "signed_records": self.signed,
            },
            "risk_policy": {level: {**dict(policy), "level": level}
                            for level, policy in RISK_POLICY.items()},
            "delegation": {"max_depth": MAX_DELEGATION_DEPTH},
            "chains": {
                "identities": self.identities_ledger.verify(),
                "delegations": self.delegations_ledger.verify(),
                "letters": self.letters_ledger.verify(),
            },
            "keyring": self.keyring.facts(),
            "signature": {"algorithm": "ed25519", "fields": list(SIGNATURE_FIELDS),
                          "canonical": "sorted-json"},
            "degraded": list(self.degraded),
            "paths": self.paths.facts(),
        }

    # -- 内部工具 -----------------------------------------------------
    @staticmethod
    def _ts_key(record: Mapping[str, Any]) -> str:
        return str(record.get("timestamp") or "")

    @staticmethod
    def _hash_of(record: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
        node: Any = record
        for step in path:
            if not isinstance(node, Mapping) or step not in node:
                return None
            node = node[step]
        if node in (None, "", {}, []):
            return None
        try:
            return hash_object(node)
        except FeatureStoreError:
            return None

    def _head_of(self, agent_id: str) -> str:
        entry = self.heads.get(str(agent_id)) or {}
        head = str(entry.get("entry_hash") or "")
        return head if len(head) == 64 else GENESIS_HASH

    def _set_head(self, agent_id: str, entry_hash: str) -> None:
        if not agent_id or len(str(entry_hash)) != 64:
            return
        try:
            entry = self.heads.get(str(agent_id)) or {}
            # 连续性检查需要"这个 Agent 历史上签过哪些 entry_hash"，只留链头会让
            # 除链头之外的一切 prev_hash 都只能报 unverified（几乎永远验不上）。
            # 因此链头记录里带上有限长度的历史（超出上限就如实退回 unverified）。
            history = [str(item) for item in (entry.get("entry_hashes") or [])
                       if len(str(item)) == 64]
            previous_head = str(entry.get("entry_hash") or "")
            if len(previous_head) == 64 and previous_head != str(entry_hash):
                history.append(previous_head)
            history.append(str(entry_hash))
            history = history[-HEAD_HISTORY:]
            self.heads.put({"id": str(agent_id), "entry_hash": str(entry_hash),
                            "entry_hashes": history, "updated_at": self._now()})
        except FeatureStoreError:  # pragma: no cover - 落盘失败不该让签名失败
            pass

    def _pubkeys_of(self, agent_id: str) -> set[str]:
        """这个 Agent 历史上用过的所有公钥（轮换后旧签名仍可验证）。"""
        keys: set[str] = set()
        for entry in self.identities_ledger.load():
            if str(entry.get("agent_id") or "") == str(agent_id):
                pub = str(entry.get("pubkey") or "")
                if pub:
                    keys.add(pub)
        return keys

    def _known_hashes(self, agent_id: str) -> set[str]:
        known = {str(entry.get("entry_hash") or "")
                 for entry in self.identities_ledger.load()}
        entry = self.heads.get(str(agent_id)) or {}
        known.add(str(entry.get("entry_hash") or ""))
        for item in (entry.get("entry_hashes") or []):
            known.add(str(item))
        return {item for item in known if len(item) == 64}

    def _build_approval(self, level: str, policy: Mapping[str, Any],
                        approvers: Sequence[Mapping[str, Any] | str], *,
                        record: Mapping[str, Any],
                        allow_unapproved: bool) -> dict[str, Any]:
        """按风险策略生成 ``approval``：形状严格按 docs/02 §2.8（schema 封闭）。

        审批签名的 payload 绑定本条记录（``record_id`` + 动作事实 + 风险 + 所需人数），
        同一审批人给两条不同记录的签名因此不会相同（跨记录复用失效）。
        同一个 DID 只算一个人（docs/09 裁决 D8）。人数不足的口径按 Q5 分两种（docs/12 §9）：
        **给了**审批人但去重后不足 → ``SECRETARIAT_FIELD_INVALID`` / 400（field=approvers，
        入参问题）；**根本没给**审批人 → ``SECRETARIAT_APPROVAL_REQUIRED`` / 409（状态问题，
        还缺人工审批这一事实本身）。
        """
        required = int(policy.get("approvers") or 0)
        # schema 上限先于签名动作（approval.approvers: maxItems 4、role: maxLength 40）：
        # 越界的审批人只会写出一条"已签名但不合规"的记录，还白白为用户 DID 生成密钥。
        unique_supplied = {
            str((item.get("did") if isinstance(item, Mapping) else item) or "").strip()
            for item in approvers} - {""}
        if len(unique_supplied) > MAX_APPROVERS:
            raise SecretariatError(
                "SECRETARIAT_FIELD_INVALID",
                f"approvers 去重后 {len(unique_supplied)} 个：schema 的 approval.approvers "
                f"上限是 {MAX_APPROVERS} 个，多出来的签名写进记录就是一条不合规的已签名记录",
                field="approvers",
                detail={"unique": len(unique_supplied), "limit": MAX_APPROVERS,
                        "risk_level": level})
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        supplied = 0                          # 调用方交上来的审批人条目（含重复）
        for index, raw in enumerate(approvers):
            if isinstance(raw, Mapping):
                did_raw = raw.get("did") or raw.get("decided_by") or ""
                role = str(raw.get("role") or ("approver" if index == 0 else "co_approver"))
            else:
                did_raw = raw
                role = "approver" if index == 0 else "co_approver"
            did = str(did_raw or "").strip()
            if not did:
                continue
            _require_record_did(did, field="approvers")
            if len(role) > APPROVER_ROLE_MAX:
                raise SecretariatError(
                    "SECRETARIAT_FIELD_INVALID",
                    f"approvers[].role 超过 {APPROVER_ROLE_MAX} 个字符：schema 的上限是 "
                    f"{APPROVER_ROLE_MAX}，审批条目的 role 也在这条已签名记录里",
                    field="approvers",
                    detail={"role_length": len(role), "limit": APPROVER_ROLE_MAX,
                            "did": did})
            supplied += 1
            if did in seen:
                continue                      # 同一个人签两次不算两个人
            seen.add(did)
            key_id = f"user:{did}"
            self.keyring.ensure(key_id, kind="user")
            stamp = self._now()
            payload = {
                "did": did, "role": role, "signed_at": stamp,
                "record_id": str(record.get("id") or ""),
                "action_type": str(record.get("action_type") or ""),
                "input_hash": record.get("input_hash"),
                "output_hash": record.get("output_hash"),
                "risk_level": level,
                "required": required,
            }
            signature = cs.b64e(cs.sign(self.keyring.seed_of(key_id),
                                        cs.canonical_bytes(payload)))
            entries.append({"did": did, "role": role, "signed_at": stamp,
                            "signature": signature})
        if len(entries) < required and not allow_unapproved:
            if supplied:
                # Q5（docs/12 §9 / §7 A6）：调用方**确实给了**审批人，但按 DID 去重后
                # 人数不足（同一个人签两次不算第二个人）→ 这是**入参字段**问题，
                # 复用 SECRETARIAT_FIELD_INVALID / 400，不新增错误码（不动 i18n 与错误码清单）。
                raise SecretariatError(
                    "SECRETARIAT_FIELD_INVALID",
                    f"approvers 按 DID 去重后只有 {len(entries)} 个：{level} 风险需要 "
                    f"{required} 个**不同**的 user DID，同一个人签两次不算第二个人",
                    field="approvers",
                    detail={"required": required, "given": len(entries),
                            "supplied": supplied, "risk_level": level,
                            "reason": "duplicate approver DID"})
            # 根本没传审批人（或传空数组）且未 allow_unapproved → 状态问题：
            # 这条记录还缺人工审批，保持 SECRETARIAT_APPROVAL_REQUIRED / 409。
            raise SecretariatError(
                "SECRETARIAT_APPROVAL_REQUIRED",
                f"{level} 风险需要 {required} 个不同的人审批签名，现在只有 {len(entries)} 个",
                field="approvers",
                detail={"required": required, "given": len(entries), "risk_level": level})
        sealed = bool(policy.get("seal")) and bool(entries) and len(entries) >= required
        out: dict[str, Any] = {"required": required, "approvers": entries, "sealed": sealed}
        if sealed:
            out["seal"] = self.seal(
                subject=str(record.get("id") or ""),
                digest=approval_digest(record),
                reason=f"{level} 风险：总裁办盖章")
        return out

    @staticmethod
    def _normalise_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(scope, Mapping):
            raise SecretariatError("SECRETARIAT_SCOPE_INVALID",
                                   "scope 必须是对象", field="scope")
        action_types = scope.get("action_types") or []
        tools = scope.get("tools") or []
        max_risk = str(scope.get("max_risk") or "low").strip().lower()
        if not isinstance(action_types, (list, tuple)) or \
                not all(isinstance(item, str) for item in action_types):
            raise SecretariatError("SECRETARIAT_SCOPE_INVALID",
                                   "scope.action_types 必须是字符串数组",
                                   field="scope.action_types")
        if not isinstance(tools, (list, tuple)) or \
                not all(isinstance(item, str) for item in tools):
            raise SecretariatError("SECRETARIAT_SCOPE_INVALID",
                                   "scope.tools 必须是字符串数组", field="scope.tools")
        if max_risk not in RISK_ORDER:
            raise SecretariatError("SECRETARIAT_SCOPE_INVALID",
                                   f"scope.max_risk 只能是 {', '.join(RISK_ORDER)}",
                                   field="scope.max_risk")
        return {"action_types": sorted({item for item in action_types}),
                "tools": sorted({item for item in tools}),
                "max_risk": max_risk}

    @staticmethod
    def _check_narrowing(parent: Mapping[str, Any], child: Mapping[str, Any]) -> None:
        widened: dict[str, Any] = {}
        parent_types = set(parent.get("action_types") or [])
        child_types = set(child.get("action_types") or [])
        if parent_types and not child_types.issubset(parent_types):
            widened["action_types"] = sorted(child_types - parent_types)
        parent_tools = set(parent.get("tools") or [])
        child_tools = set(child.get("tools") or [])
        if parent_tools and not child_tools.issubset(parent_tools):
            widened["tools"] = sorted(child_tools - parent_tools)
        if RISK_ORDER[str(child.get("max_risk"))] > RISK_ORDER[str(parent.get("max_risk"))]:
            widened["max_risk"] = {"parent": parent.get("max_risk"),
                                   "child": child.get("max_risk")}
        if widened:
            raise SecretariatError(
                "SECRETARIAT_SCOPE_WIDENED",
                "委托权限只能收窄不能放大：子委托超出了父委托的范围",
                field="scope", detail={"widened": widened})

    def _chain_for_records(self, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        for record in records:
            for did in record.get("delegation_chain") or []:
                if str(did) not in [item.get("did") for item in chain]:
                    chain.append({"did": str(did), "source": "delegation_chain"})
        return chain

    @staticmethod
    def _determination(role: str, verified: Sequence[tuple[Mapping[str, Any],
                                                           Mapping[str, Any]]]) -> dict[str, Any]:
        failed = [str(item.get("id") or "") for item, result in verified
                  if result.get("checks", {}).get("signature") != "valid"]
        agents = sorted({str(item.get("agent_id") or "") for item, _ in verified})
        if failed:
            conclusion = "signature_invalid"
        elif role == "platform":
            conclusion = "platform_review_required"
        else:
            conclusion = "attributable_to_agent"
        return {
            "role": role,
            "label": ROLE_LABELS.get(role, role),
            "conclusion": conclusion,
            "agents": agents,
            "unsigned_or_tampered": failed,
            "note": "认定书由总裁办公钥签名，可离线验证；签名只证明记录未被改动，"
                    "不改变真实世界里的法律责任。",
        }


def default_secretariat(paths: FeaturePaths | None = None, *,
                        mode: str = "local") -> Secretariat:
    """按数据目录装配一个总裁办（产品入口与服务端共用这一处）。"""
    return Secretariat(paths or FeaturePaths(), mode=mode)


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.secretariat``：临时目录里跑一遍全链路。"""
    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(temp)
        sec = Secretariat(paths)
        identity = sec.register_identity(agent_id="agent-demo",
                                         owner_did=cs.did_from_name("demo", kind="user"))
        record = {"id": "act_1", "timestamp": "2026-11-11T12:00:00Z", "agent_id": "agent-demo",
                  "action_type": "read", "tool": "calendar.read", "status": "succeeded",
                  "risk_level": "low", "confidence": 0.92,
                  "context": {"input": {"day": "friday"}}, "result_summary": "3 events"}
        signed = sec.sign_record(record)
        report = sec.verify_record(signed)
        assert report["ok"], report
        chain_ok = sec.status()["chains"]["identities"]["ok"]
        delegation = sec.create_delegation(
            delegator_did="did:tracelens:user:demo",
            delegatee_did="did:tracelens:agent:agent-demo",
            scope={"action_types": ["read"], "tools": ["calendar.read"], "max_risk": "low"})
        letter = sec.build_letter(subject="demo incident", records=[signed])
        verified = sec.verify_letter(letter)
        assert verified["ok"], verified
        print(f"secretariat self-check ok: identity={identity['fingerprint']} "
              f"signature={report['checks']['signature']} chain={chain_ok} "
              f"delegation_depth={delegation['depth']} letter={letter['id']}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
