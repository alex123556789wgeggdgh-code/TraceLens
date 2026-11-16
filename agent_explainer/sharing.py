"""共享与协作模式（规格书功能五）：带期限的邀请、权限级联、协作事件账本。

这一层回答的三个问题
--------------------
1. **谁被允许看 / 拍板 / 管理某任务组**——只看"已接受且未过期未撤销的邀请"，
   不发散、不猜。权限级联：``manage ⊃ approve ⊃ view``（高等级包含低等级）。
2. **邀请链接怎么保管**——明文 token **只在创建那一刻**返回一次；落盘只有
   ``sha256(salt + token)``（与 ``ingest.py`` 的令牌保管箱同一纪律）。列表 /
   详情 / 事件里再也不出现 token，也不出现 salt 与哈希本身。
3. **协作操作怎么留痕**——``invite`` / ``accept`` / ``revoke`` 都会往
   ``collab_events.jsonl``（:class:`feature_store.Ledger`）追加一条事件：
   ``id / actor_did / action / target / at / detail / signature``，链字段
   ``seq / prev_hash / entry_hash`` 由账本自动算，``verify()`` 能离线复算。
   协作者**代真人**操作时事件多一个 ``detail.on_behalf_of`` 标注：``actor_did`` 仍是
   实际操作人（协作者本人），记录的 ``human_owner`` 与 ``delegation_chain`` 一个字都不动。
   「谁做的 / 替谁做的 / 谁担责」在**事件层**一次说清（REQ-005 B5-3；口径见
   :meth:`ShareService.record_event`，认定书侧的渲染见 ``secretariat.on_behalf_index``）。

``signature`` 为什么是 ``None``
------------------------------
签名需要密钥，而密钥的唯一持有者是 ``secretariat.py`` 的 ``keyring``。本模块是纯
标准库、无密钥依赖的协作层，所以事件记录的 ``signature`` 字段**留空占位**
（``None``），不伪造、不写假签名。需要签名的那一层（路由 / 总裁办）可以读取事件后用
``secretariat.sign_record`` / ``Ed25519`` 补签，但"补签"必须发生在本模块之外，且
本轮没有默认开启这件事——**界面不得把这条事件显示成"已签名"**。

过期语义（写清楚，免得好心办坏事）
---------------------------------
``expires_at`` 是 ISO-8601 UTC 绝对时间。**链接过期 = 访问权过期**：即使邀请
已被接受，一旦过了 ``expires_at``，``status`` 就报 ``expired``，``can()`` 不再放行，
``accept`` 报 ``SHARE_EXPIRED``。"在有效期内可用、过期失效"是规格书功能五的验收点，
这里按字面执行，不做"接受过就永久有效"的宽限。

本模块**不校验任务组是否存在**：TaskGroup 的生命周期归 :class:`groups.GroupService`，
路由层在分享前先 ``group_service.get()``（缺失即 ``GROUP_NOT_FOUND``）。为了让这条
接缝不需要调用方另写一层判断，构造函数接受可选的 ``group_lookup`` 回调
（``groups.get`` 即可），给了就会在 :meth:`ShareService.invite` 里先查组是否存在。

``can()`` 只回答"邀请关系给了你什么"，**不回答"你是不是组的所有者"**
------------------------------------------------------------------
组归属（谁创建了组、谁有 ``data.write``）由路由层判定，本模块一概不问。R5-3 把这条
接缝写成可依赖的口径：写路径闸门用 :meth:`ShareService.invites_for_group`（只读）先问
"这组里有没有邀请"，再用 :meth:`ShareService.can` 问"邀请给了你什么"。
**没有邀请的组 / 没被邀请的人（队长本人、local 免登录）在闸门里一律原样放行**——
``can()`` 对这种情况回答 ``reason="no_grant"``，闸门把它当作"不是协作者"，绝不把
"没有邀请"当成"权限不足"来拒绝（那是把协作层做成全站鉴权，越界且必然打爆 local 模式）。
同一登录人的两个 id 口径（``username`` / ``user_id``）由调用方作为**身份集**传入
（:meth:`ShareService.revoke` 的 ``trusted_ids``），本模块按身份集取最强授权。

写作用域：``FeaturePaths.shares``（``shares.jsonl``，实体集合：同 id 最后写入胜出，
所以"接受/撤销"是覆盖写而不是追加）+ ``FeaturePaths.collab_events``（只能追加的账本）。
"""

from __future__ import annotations

import copy
import datetime as _dt
import hmac
import pathlib
import secrets
import threading
from typing import Any, Callable, Iterable, Mapping

from .feature_store import (
    FeaturePaths,
    new_id,
    open_ledger,
    open_store,
    sha256_hex,
    utc_stamp,
)

__all__ = [
    "SHARING_VERSION",
    "PERMISSIONS",
    "LEVELS",
    "PERMISSION_RANK",
    "DEFAULT_TTL_SECONDS",
    "TOKEN_PREFIX",
    "SHARE_STATUSES",
    "SHARE_HTTP_STATUS",
    "SECRET_FIELDS",
    "ON_BEHALF_KEY",
    "ON_BEHALF_MAX_LEN",
    "ShareError",
    "permission_level",
    "ShareService",
]

#: 共享层口径版本
SHARING_VERSION = "1.0"
#: 权限等级：高等级包含低等级（manage ⊃ approve ⊃ view）
PERMISSIONS = ("view", "approve", "manage")
LEVELS = {"view": 1, "approve": 2, "manage": 3}
#: 别名（名字更直白的读法；值与 ``LEVELS`` 同一个对象）
PERMISSION_RANK = LEVELS
#: 邀请默认有效期：24 小时
DEFAULT_TTL_SECONDS = 86400
#: 明文 token 前缀（便于日志里一眼认出，但 token 本身绝不落盘）
TOKEN_PREFIX = "shr_"
#: 邀请的**存储**状态；对外的 ``status`` 是派生值，多一个 ``expired``
SHARE_STATUSES = ("pending", "accepted", "revoked")
#: 对外可见（派生）状态
VISIBLE_STATUSES = ("pending", "accepted", "expired", "revoked")
#: §7 错误码 → HTTP 状态（字段/参数 400；不存在 404；状态冲突 409；越权 403；存储 500）
SHARE_HTTP_STATUS = {
    "SHARE_NOT_FOUND": 404,
    "SHARE_PERMISSION_INVALID": 400,
    "SHARE_EXPIRED": 409,
    "SHARE_TOKEN_INVALID": 400,
    "SHARE_TOKEN_USED": 409,
    "SHARE_FORBIDDEN": 403,
    "SHARE_WRITE_FAILED": 500,
    "GROUP_NOT_FOUND": 404,
}
#: 绝不进任何响应 / 事件的字段（salt 与哈希都是"能离线爆破明文"的材料）
SECRET_FIELDS = ("salt", "token_hash", "token")

#: 事件 detail 里命中这些词的键整值打码（与 audit.py 的脱敏纪律同口径）。
#: R5-1 扩两个词：``url`` / ``link`` —— 分享链接（``invite_url`` / ``share_link``）里
#: 装着**明文 token**，它和明文 token 同一条纪律（只在创建那一刻出现一次）。调用方把
#: 链接塞进事件 detail 是必然会发生的写法（"记一下我发出去的链接"），所以这条路径必须在
#: 写入侧结构性打码，而不是靠调用方"记得别写"。
_SENSITIVE_DETAIL_KEYS = (
    "token", "token_hash", "salt", "secret", "password", "passwd",
    "api_key", "apikey", "private_key", "credential",
    "url", "link",
)

#: 打码口径的两条 R5-2 定档（PM-VERDICT-R5-1 §3.5 遗留 5 → TASK-R5-2 §2.4，已批 L1）。
#:
#: **一、匹配方式：沿用子串匹配 + 显式豁免清单**（任务书给的"二选一"，这里选前半，理由写死）：
#: 子串匹配是**宁多打码**的方向 —— ``invite_url`` / ``share_link`` / ``landing_url_v2``
#: 这类"名字里带 url/link 的明文载体"被同一条规则一并盖住；改成整词匹配等于每出现一个新
#: 写法就得回来补一个词，漏补的那一次就是把明文 token 写进**只追加、撤不回**的事件账本。
#: 代价是子串会误伤：这份名单将来若加进 ``invite``，``invite_id`` / ``inviter_did`` /
#: ``invitee_did`` 会一起被打码 —— 那三个字段正是责任认定与审计要读的，打了码责任链就查不下去。
#: 所以选"名单命中即打码 + **精确键名**点名豁免"：豁免只认精确名字（不是子串），优先级高于打码。
#: 今天的豁免是**惰性的**（这三个名字都不命中现有敏感词），它钉住的是将来扩词那一刻的行为。
_SENSITIVE_DETAIL_EXEMPT = frozenset({"invite_id", "inviter_did", "invitee_did"})

#: **二、递归深度封顶**（防深嵌套 DoS）：``detail`` 是协作层自己写的对象，真嵌套到第 8 层
#: 往下已经不是"审计要读的东西"了。超深的子树整块换成 ``_SCRUB_DEPTH_MARK`` ——
#: **失败方向选打码**：账本只追加、写进去撤不回，宁可少一层可读性，也不能把明文泄进去。
_SCRUB_MAX_DEPTH = 8
_SCRUB_DEPTH_MARK = "<scrubbed:too_deep>"

#: 「代行」标注在事件 ``detail`` 里的键名（PM-PLAN §7.1 冻结口径，``secretariat`` 引用同一份，
#: 两边不各写一遍字面量 —— 键名漂移等于责任标注静默消失）。
ON_BEHALF_KEY = "on_behalf_of"
#: 代行标注的真人 DID 长度上限（``detail`` 进签名与账本，必须有界；``secretariat`` 侧同用此上限）
ON_BEHALF_MAX_LEN = 256


# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #

class ShareError(Exception):
    """共享层错误：机器码 + 人话原因 + 字段 + 取证细节（与既有模块同形）。

    错误码只取 ``docs/07`` §7 的 sharing 域（外加路由层要的 ``GROUP_NOT_FOUND``）：
    ``SHARE_NOT_FOUND`` / ``SHARE_PERMISSION_INVALID`` / ``SHARE_EXPIRED`` /
    ``SHARE_TOKEN_INVALID`` / ``SHARE_TOKEN_USED`` / ``SHARE_FORBIDDEN`` /
    ``SHARE_WRITE_FAILED``。``http_status`` 给出 §7 的 HTTP 映射，路由层不必再抄一遍。
    """

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 detail: Mapping[str, Any] | None = None, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.field = field
        self.detail: dict[str, Any] = dict(detail or {})
        self.hint = hint

    @property
    def http_status(self) -> int:
        """§7 的 HTTP 映射（未知码一律 400，绝不把内部错误泄成 200）。"""
        return int(SHARE_HTTP_STATUS.get(self.code, 400))

    def render(self) -> str:
        tail = f"（{self.field}）" if self.field else ""
        return f"{self.code}: {self.reason}{tail}"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "reason": self.reason,
                                   "http_status": self.http_status}
        if self.field:
            payload["field"] = self.field
        if self.detail:
            payload["detail"] = dict(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _text(value: Any) -> str:
    """去空白的字符串；非字符串当空（不 str() 危险对象）。"""
    return value.strip() if isinstance(value, str) else ""


def _as_datetime(value: Any) -> _dt.datetime | None:
    """时间戳 → 带时区的 UTC datetime；解析不了返回 ``None``（不猜时区，无时区按 UTC）。"""
    moment: _dt.datetime
    if isinstance(value, _dt.datetime):
        moment = value
    elif isinstance(value, bool):
        return None
    elif isinstance(value, (int, float)):
        try:
            moment = _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            moment = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc)


def _stamp(moment: _dt.datetime | None = None) -> str:
    """ISO-8601 UTC 秒级时间戳（统一走 ``feature_store.utc_stamp``）。"""
    if moment is None:
        return utc_stamp()
    return utc_stamp(moment.timestamp())


def permission_level(permission: Any) -> int:
    """权限名 → 等级数字；未知权限返回 0（调用方据此拒绝，而不是当成 view 放行）。"""
    return int(LEVELS.get(_text(permission), 0))


def _is_sensitive_key(name: str) -> bool:
    """键名要不要打码：先查豁免点名（精确键名，优先级高），再查敏感词子串命中。"""
    if name in _SENSITIVE_DETAIL_EXEMPT:
        return False
    lowered = name.lower()
    return any(word in lowered for word in _SENSITIVE_DETAIL_KEYS)


def _scrub_value(value: Any, depth: int) -> Any:
    """按值递归打码：字典按键名，列表/元组逐元素；深度到顶就整块打码（见 ``_SCRUB_MAX_DEPTH``）。

    标量原样返回 —— 这是"既有事件形状不漂移"的那一半证据：扁平 detail 的每个值都直接透传。
    列表与元组各返回同类型（元组不会被悄悄变成列表），其余类型（含集合等 JSON 不认识的形状）
    原样带上，由落盘层自己决定怎么序列化，本层不裁、不猜。
    """
    if depth >= _SCRUB_MAX_DEPTH:
        return _SCRUB_DEPTH_MARK
    if isinstance(value, Mapping):
        return _scrub(value, depth + 1)
    if isinstance(value, list):
        return [_scrub_value(item, depth) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(item, depth) for item in value)
    return value


def _scrub(detail: Mapping[str, Any], depth: int = 0) -> dict[str, Any]:
    """事件 detail 脱敏：命中敏感键名的值整值替换成 ``<redacted>``，并**递归进嵌套字典与列表**。

    R5-2 定档（PM-VERDICT-R5-1 §3.5 遗留 5）：旧口径只递归字典、**不递归列表**，
    ``detail={"urls": [token…]}`` / ``detail={"payload": [{"token": …}]}`` 这类写法
    今天照样把明文写进账本。现在列表/元组也下钻，深度封顶 ``_SCRUB_MAX_DEPTH``，
    豁免清单精确点名（口径与理由见那两个常量处的注释）。
    非敏感的值原样返回：嵌套里的审计字段一个都不动。
    """
    scrubbed: dict[str, Any] = {}
    for key, value in detail.items():
        name = str(key)
        if _is_sensitive_key(name):
            scrubbed[name] = "<redacted>"
        else:
            scrubbed[name] = _scrub_value(value, depth + 1)
    return scrubbed


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #

class ShareService:
    """邀请 / 接受 / 撤销 / 权限查询 + 协作事件账本。

    用法::

        service = ShareService(paths)
        created = service.invite(group_id=gid, inviter_did="did:alice",
                                 permission="view", invitee_did="did:bob")
        created["token"]                      # 明文只在这里出现一次
        accepted = service.accept(token=created["token"], invitee_did="did:bob")
        service.can("did:bob", "approve", gid)   # {"allowed": False, ...}  view 不能拍板
        service.verify_events()["ok"]            # 事件链可离线复算

    ``now`` 可传 ``datetime`` / Unix 秒 / 无参回调（测试用来冻住时间）。
    ``group_lookup`` 可选（``group_service.get``），给了就在 ``invite`` 时校验任务组
    是否存在，缺失报 ``GROUP_NOT_FOUND``；不给则本模块不问组的存在性（见模块头）。
    """

    def __init__(self, paths: FeaturePaths | pathlib.Path | str | None = None, *,
                 now: Any = None,
                 group_lookup: Callable[[str], Any] | None = None) -> None:
        if isinstance(paths, (str, pathlib.Path)):
            paths = FeaturePaths(paths)
        if paths is None:
            paths = FeaturePaths()
        self.paths: FeaturePaths = paths
        self._now = now
        self._group_lookup = group_lookup
        self._lock = threading.RLock()
        self._shares = open_store(self.paths.shares)
        self._shares.load()
        self._events = open_ledger(self.paths.collab_events)
        self._events.load()

    # -- 时钟 -------------------------------------------------------------
    def _now_dt(self) -> _dt.datetime:
        raw = self._now() if callable(self._now) else self._now
        moment = _as_datetime(raw)
        return moment if moment is not None else _dt.datetime.now(_dt.timezone.utc)

    # -- 邀请 -------------------------------------------------------------
    def invite(self, *, group_id: str, inviter_did: str, permission: str,
               invitee_did: str = "", ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
        """发出一份邀请，返回记录 **加一次性明文 ``token``**（落盘只有哈希+盐）。

        * ``permission`` 只能是 ``view`` / ``approve`` / ``manage``，否则
          ``SHARE_PERMISSION_INVALID``；
        * ``ttl_seconds`` 必须是正整数秒，``None`` 视为默认 24 小时；其它（0/负数/
          字符串）一律 ``SHARE_FORBIDDEN`` 并带 ``field``，**不**把非法值悄悄改成默认；
        * 不能授出高于自己在**该组**所持等级的权限（防止"被只读分享的人转手发管理邀请"）。
          当发起人在该组还没有任何已生效授权时视为首个邀请（引导规则，写在文档里，
          不藏着）：组归属与"谁能发起分享"由路由层的 ``data.write`` 权限判定；
        * 定向邀请（``invitee_did`` 非空）只允许被邀请人接受，别人拿到 token 也报
          ``SHARE_FORBIDDEN``。

        明文 token 的生成与保管与 ``ingest.TokenVault`` 同纪律：随机 192 bit，
        ``sha256(salt + token)`` 落盘，比对用 ``hmac.compare_digest``。
        """
        wanted = _text(permission)
        if wanted not in PERMISSIONS:
            raise ShareError("SHARE_PERMISSION_INVALID",
                             f"permission 只能是 {'/'.join(PERMISSIONS)}",
                             field="permission", hint="/".join(PERMISSIONS))
        group_text = _text(group_id)
        if not group_text:
            raise ShareError("SHARE_FORBIDDEN", "缺少 group_id：不知道该分享哪个任务组",
                             field="group_id")
        inviter = _text(inviter_did)
        if not inviter:
            raise ShareError("SHARE_FORBIDDEN", "缺少 inviter_did：不知道是谁在分享",
                             field="inviter_did")
        target = _text(invitee_did)

        ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ShareError("SHARE_FORBIDDEN",
                             "ttl_seconds 必须是正整数秒（不支持 0 / 负数 / '永久'）",
                             field="ttl_seconds", detail={"ttl_seconds": repr(ttl)})

        if self._group_lookup is not None:
            self._require_group(group_text)

        own = self._strongest_grant(inviter, group_text)
        if own is not None and LEVELS[wanted] > own[0]:
            raise ShareError(
                "SHARE_FORBIDDEN",
                f"你在这个任务组只有 {own[1].get('permission')} 权限，不能授出 {wanted}",
                field="permission",
                detail={"granted": own[1].get("permission"), "requested": wanted,
                        "invite_id": own[1].get("id")},
            )

        now = self._now_dt()
        token = TOKEN_PREFIX + secrets.token_hex(24)
        salt = secrets.token_hex(16)
        record: dict[str, Any] = {
            "id": new_id("inv"),
            "group_id": group_text,
            "inviter_did": inviter,
            "invitee_did": target,
            "permission": wanted,
            "token_hash": sha256_hex(salt + token),
            "salt": salt,
            "expires_at": _stamp(now + _dt.timedelta(seconds=ttl)),
            "status": "pending",
            "created_at": _stamp(now),
            "accepted_at": None,
            "accepted_by": None,
            "revoked_at": None,
        }
        with self._lock:
            self._shares.put(record)
        self.record_event(actor=inviter, action="share.invite", target=record["id"],
                          detail={"group_id": group_text, "permission": wanted,
                                  "invitee_did": target})
        view = self._public(record)
        view["token"] = token  # 明文只在此刻出现一次
        return view

    def accept(self, *, token: str, invitee_did: str) -> dict[str, Any]:
        """用 token 接受邀请并把邀请绑定到 ``invitee_did``。

        * 未知 / 空 token → ``SHARE_TOKEN_INVALID``（不透露"猜到了哪一条"）；
        * 已撤销 → ``SHARE_TOKEN_INVALID``（token 已死，说"无效"比说"已撤销"少一个枚举面，
          reason 里会写清原因）；
        * 过期 → ``SHARE_EXPIRED``；
        * 已被**同一个人**接受 → 幂等成功（重复点链接不该报错）；被别人先接受 →
          ``SHARE_TOKEN_USED``。
        """
        token_text = _text(token)
        if not token_text:
            raise ShareError("SHARE_TOKEN_INVALID", "缺少 token", field="token",
                             hint="链接里的 token 只在创建时出现过一次")
        record = self._find_by_token(token_text)
        if record is None:
            raise ShareError("SHARE_TOKEN_INVALID",
                             "token 无效或对应的邀请已不存在", field="token")
        invite_id = str(record.get("id"))
        if _text(record.get("status")) == "revoked":
            raise ShareError("SHARE_TOKEN_INVALID",
                             f"邀请 {invite_id} 已被撤销", field="token",
                             detail={"invite_id": invite_id, "status": "revoked"})
        if self.expired(record):
            raise ShareError("SHARE_EXPIRED",
                             f"邀请 {invite_id} 已过期（{record.get('expires_at')}）",
                             field="token",
                             detail={"invite_id": invite_id,
                                     "expires_at": record.get("expires_at")})
        person = _text(invitee_did)
        if not person:
            raise ShareError("SHARE_FORBIDDEN", "缺少 invitee_did：不知道是谁在接受",
                             field="invitee_did")
        accepted_by = _text(record.get("accepted_by"))
        if accepted_by:
            if accepted_by == person:
                return self._public(record)  # 幂等：同一个人再点一次不报错
            raise ShareError("SHARE_TOKEN_USED",
                             f"邀请 {invite_id} 已被别人接受",
                             field="token",
                             detail={"invite_id": invite_id,
                                     "accepted_by": accepted_by})
        bound = _text(record.get("invitee_did"))
        if bound and bound != person:
            raise ShareError("SHARE_FORBIDDEN",
                             f"这份邀请是指定给 {bound} 的，不接受你的身份",
                             field="invitee_did",
                             detail={"invite_id": invite_id, "invitee_did": bound})

        updated = dict(record)
        updated["invitee_did"] = bound or person
        updated["accepted_by"] = person
        updated["accepted_at"] = _stamp(self._now_dt())
        updated["status"] = "accepted"
        with self._lock:
            self._shares.put(updated)
        self.record_event(actor=person, action="share.accept", target=invite_id,
                          detail={"group_id": record.get("group_id"),
                                  "permission": record.get("permission")})
        return self._public(updated)

    def revoke(self, invite_id: str, *, actor: str,
               trusted_ids: Iterable[str] = (), reason: str = "") -> dict[str, Any]:
        """撤销邀请（发起人本人，或在该组持有 manage 权限的人）。

        已撤销的再撤一次是幂等的（返回当前记录，不报错、不重复追加事件），
        因为"撤销"天然幂等，而重复报错只会让界面弹一个没有意义的气泡。

        ``trusted_ids``（R5-3，TASK-R5-3 §2.3 的路由层装配口径）
        --------------------------------------------------------
        同一个登录人在本仓有**两个 id 口径**：决定段与审计用 ``username``
        （``server.audit_actor``），分享/协作层用会话的 ``user_id``
        （``server._actor_id_of``）。只拿一个 id 去查授权，就会出现"用 ``user_id``
        接受的邀请、用 ``username`` 撤销时被当成局外人"这种自相矛盾。
        所以路由层把**操作者这次请求带来的全部身份**传进来：授权按身份集取最强，
        事件里的 ``actor_did`` 仍是**主身份** ``actor``（不冒充别人，也不替换事实现场）。
        默认 ``()`` = 只认 ``actor`` —— 与改动前**逐字同行为**（既有调用零变化）。

        ``reason``：路由层 ``POST /api/shares/<id>/revoke`` 的可选 ``reason`` 原样进事件
        ``detail``。默认空字符串 → ``detail`` 逐字不变。"撤销"是收权，收完发现只留下
        "谁撤的"没有"为什么撤"，事后查不动。
        """
        key = _text(invite_id)
        record = self._shares.get(key) if key else None
        if record is None:
            raise ShareError("SHARE_NOT_FOUND", f"没有 id 为 {key} 的邀请",
                             field="invite_id", detail={"invite_id": key})
        person = _text(actor)
        if not person:
            raise ShareError("SHARE_FORBIDDEN", "缺少 actor：不知道是谁在撤销",
                             field="actor")
        ids = [person]
        for item in (trusted_ids or ()):
            extra = _text(item)
            if extra and extra not in ids:
                ids.append(extra)
        if _text(record.get("status")) == "revoked":
            return self._public(record)
        group_text = _text(record.get("group_id"))
        if _text(record.get("inviter_did")) not in ids:
            verdicts = [self.can(identity, "manage", group_text) for identity in ids]
            if not any(verdict.get("allowed") for verdict in verdicts):
                detail: dict[str, Any] = {"invite_id": key, "group_id": group_text,
                                          "reason": verdicts[0].get("reason")}
                if len(ids) > 1:
                    # 多身份时把每个人的答案都带上：查的人不必猜是哪个口径没匹配上
                    detail["reasons"] = {identity: verdict.get("reason")
                                         for identity, verdict in zip(ids, verdicts)}
                raise ShareError(
                    "SHARE_FORBIDDEN",
                    "只有发起人本人，或在该任务组持有 manage 权限的人，才能撤销邀请",
                    field="actor",
                    detail=detail,
                )
        updated = dict(record)
        updated["status"] = "revoked"
        updated["revoked_at"] = _stamp(self._now_dt())
        updated["revoked_by"] = person
        with self._lock:
            self._shares.put(updated)
        note = _text(reason)
        event_detail: dict[str, Any] = {"group_id": group_text,
                                        "status_before": record.get("status")}
        if note:
            event_detail["reason"] = note[:500]
        if len(ids) > 1:
            event_detail["actor_ids"] = list(ids)
        self.record_event(actor=person, action="share.revoke", target=key,
                          detail=event_detail)
        return self._public(updated)

    # -- 查询 -------------------------------------------------------------
    def get(self, invite_id: str) -> dict[str, Any] | None:
        """按 id 取邀请的**公开视图**（不含 salt / token_hash / token）。"""
        key = _text(invite_id)
        if not key:
            return None
        record = self._shares.get(key)
        return self._public(record) if record is not None else None

    def invites_for_group(self, group_id: str) -> list[dict[str, Any]]:
        """某个任务组的**全部**邀请（公开视图，按创建顺序；R5-3 鉴权闸门的只读探针）。

        为什么需要它（TASK-R5-3 §2.1 / PM-PLAN §7.5 最高优先级）：写路径入口要先回答
        "这个组里到底有没有邀请"，才谈得上"改动只对**带邀请的协作者**生效、
        无邀请者零行为变化"。这必须是**只读**的：闸门自身不写存储、不写事件账本，
        拒绝的那一次也不留下任何字节。返回 :meth:`_public` 视图
        （``salt`` / ``token_hash`` / ``token`` 一律剔掉，与列表同一条纪律）。
        """
        key = _text(group_id)
        if not key:
            return []
        return [self._public(record) for record in self._shares.items()
                if _text(record.get("group_id")) == key]

    def list_for(self, actor: str) -> dict[str, Any]:
        """我这个身份能看到的邀请：``{"issued": [...], "shared_with_me": [...]}``。

        ``issued`` = 我发出的（含 pending / accepted / revoked / expired）；
        ``shared_with_me`` = 指定给我或已被我接受的。两条都按创建顺序（旧→新），
        对外的 ``status`` 是派生值：``revoked`` 优先，其次是 ``expired``（过期即失效，
        即便当初已被接受），再是 ``accepted``，否则 ``pending``。
        """
        person = _text(actor)
        issued: list[dict[str, Any]] = []
        shared: list[dict[str, Any]] = []
        if not person:
            return {"actor": "", "issued": [], "shared_with_me": []}
        for record in self._shares.items():
            if _text(record.get("inviter_did")) == person:
                issued.append(self._public(record))
            if (person and (person == _text(record.get("accepted_by"))
                            or person == _text(record.get("invitee_did")))):
                shared.append(self._public(record))
        return {"actor": person, "issued": issued, "shared_with_me": shared}

    def grant_for(self, actor: str, group_id: str) -> dict[str, Any] | None:
        """我在这组的**最强**有效授权；没有就 ``None``（不被"邀请过"当成"有权限"）。"""
        found = self._strongest_grant(_text(actor), _text(group_id))
        if found is None:
            return None
        level, record = found
        return {
            "invite_id": record.get("id"),
            "group_id": _text(record.get("group_id")),
            "actor": _text(record.get("accepted_by")),
            "permission": _text(record.get("permission")),
            "level": level,
            "invited_by": _text(record.get("inviter_did")),
            "accepted_at": record.get("accepted_at"),
            "expires_at": record.get("expires_at"),
        }

    def can(self, actor: str, permission: str, group_id: str) -> dict[str, Any]:
        """``{allowed, via, level, reason}``：这个身份能否做这件事。

        **只回答"邀请关系给了你什么"**，不回答"你是不是组的所有者"——组归属与
        ``data.write`` 由路由层判定（见模块头）。因此 ``view`` 授权下
        ``can(actor, "approve", gid)["allowed"] is False``，这就是规格书功能五
        "view 无法点击干预"的服务端断言；前端隐藏按钮只是体验，服务端仍要按这里拒绝。

        ``reason`` 是稳定的机器码（``ok`` / ``actor_missing`` / ``group_missing`` /
        ``no_grant`` / ``grant_expired`` / ``grant_revoked`` / ``level_too_low``），
        界面文案走 i18n，不在 Python 里拼句子。权限查询**总是能回答**：参数缺失返回
        不允许，而不是抛异常（安全默认 + 不让查询接口因缺参 500）。
        """
        wanted = _text(permission)
        if wanted not in PERMISSIONS:
            raise ShareError("SHARE_PERMISSION_INVALID",
                             f"permission 只能是 {'/'.join(PERMISSIONS)}",
                             field="permission", hint="/".join(PERMISSIONS))
        person = _text(actor)
        group_text = _text(group_id)
        needed = LEVELS[wanted]
        if not person:
            return {"allowed": False, "via": "none", "level": 0,
                    "reason": "actor_missing", "invite_id": None, "granted": None}
        if not group_text:
            return {"allowed": False, "via": "none", "level": 0,
                    "reason": "group_missing", "invite_id": None, "granted": None}
        found = self._strongest_grant(person, group_text)
        if found is None:
            return {"allowed": False, "via": "none", "level": 0,
                    "reason": self._missing_reason(person, group_text),
                    "invite_id": None, "granted": None}
        level, record = found
        allowed = level >= needed
        return {
            "allowed": allowed,
            "via": "grant",
            "level": level,
            "reason": "ok" if allowed else "level_too_low",
            "invite_id": record.get("id"),
            "granted": _text(record.get("permission")),
        }

    def expired(self, invite: Any) -> bool:
        """这份邀请是否已过期（接受过也算：链接有效期就是访问有效期）。

        接受 id 字符串或记录本身；**未知 id 视为已失效**（安全默认，`True`），
        记录里没有 ``expires_at`` 视为永不过期（`False`），``expires_at`` 解析不了
        视为已失效（坏数据不该放大成访问权）。
        """
        if invite is None:
            return True
        record: Mapping[str, Any]
        if isinstance(invite, str):
            key = _text(invite)
            if not key:
                return True
            found = self._shares.get(key)
            if found is None:
                return True
            record = found
        elif isinstance(invite, Mapping):
            record = invite
        else:
            return True
        raw = record.get("expires_at")
        if raw is None or raw == "":
            return False
        moment = _as_datetime(raw)
        if moment is None:
            return True
        return moment <= self._now_dt()

    # -- 协作事件 ---------------------------------------------------------
    def record_event(self, *, actor: str, action: str, target: str,
                     detail: Mapping[str, Any] | None = None,
                     on_behalf_of: str | None = None) -> dict[str, Any]:
        """追加一条协作事件到账本（append-only，绝不改/删既有事件）。

        事件形状按 §3.3：``id / actor_did / action / target / at / detail /
        signature``；账本再补 ``seq / prev_hash / entry_hash``。``detail`` 里的
        敏感键（token / salt / secret / password…）整值打码，token 永不入事件。
        ``signature`` 恒为 ``None``——本模块不持密钥，**不伪造签名**（见模块头）。

        「代行」标注（REQ-005 B5-3 / PM-PLAN §7.1 冻结口径，R5-2）
        --------------------------------------------------------
        协作者**代真人**操作时传 ``on_behalf_of=<human_owner 的 DID>``：它作为附加标注写进
        ``detail``（键名 :data:`ON_BEHALF_KEY`），``actor_did`` **仍是实际操作人**（协作者本人）。
        三句话把责任说清，谁都不背错锅：

        1. **谁做的** → ``actor_did``（= 协作者 DID）：事件永远记实际操作人，代行是**附加标注**，
           不是把 actor 替换成老板的名字（替换等于把事实现场抹掉）；
        2. **替谁做的** → ``detail.on_behalf_of``（= 那位真人）；
        3. **谁担责** → 记录里的 ``human_owner`` 字段**一个字都不动**，最终责任仍归真人。

        缺省（不传 / 传 ``None``）→ **零变化**：``detail`` 原样落盘，事件形状逐字不变，
        普通协作操作（队长本人操作）连一个多余键都不多。参数与 ``detail`` 里手写的同名键
        同时出现时**以本参数为准**（口径只有一个来源：参数入口做校验与长度封顶，
        手写键不校验，所以别手写字面量）。
        """
        person = _text(actor)
        if not person:
            raise ShareError("SHARE_FORBIDDEN", "缺少 actor：协作事件必须记清是谁做的",
                             field="actor")
        what = _text(action)
        if not what:
            raise ShareError("SHARE_FORBIDDEN", "缺少 action：协作事件必须记清做了什么",
                             field="action")
        if detail is not None and not isinstance(detail, Mapping):
            raise ShareError("SHARE_FORBIDDEN", "detail 必须是对象",
                             field="detail")
        payload = dict(detail or {})
        if on_behalf_of is not None:
            if not isinstance(on_behalf_of, str):
                raise ShareError("SHARE_FORBIDDEN",
                                 "on_behalf_of 必须是字符串（被代演的真人 DID）",
                                 field="on_behalf_of")
            principal = _text(on_behalf_of)
            if not principal:
                raise ShareError(
                    "SHARE_FORBIDDEN",
                    "on_behalf_of 是空串：不传就是「队长本人操作」，传了就得写明替谁做的",
                    field="on_behalf_of")
            if len(principal) > ON_BEHALF_MAX_LEN:
                raise ShareError(
                    "SHARE_FORBIDDEN",
                    f"on_behalf_of 超过 {ON_BEHALF_MAX_LEN} 个字符：它进事件与认定书正文，"
                    "必须有界（真人的 DID 远短于此）",
                    field="on_behalf_of",
                    detail={"length": len(principal), "limit": ON_BEHALF_MAX_LEN})
            if principal == person:
                raise ShareError(
                    "SHARE_FORBIDDEN",
                    "on_behalf_of 不能就是操作人自己：自己代理自己没有权限可代，"
                    "这种标注只会让责任链多一个说不清的环",
                    field="on_behalf_of",
                    detail={"actor": person, "on_behalf_of": principal})
            payload[ON_BEHALF_KEY] = principal
        entry = {
            "id": new_id("evt"),
            "actor_did": person,
            "action": what,
            "target": _text(target),
            "at": _stamp(self._now_dt()),
            "detail": _scrub(payload),
            # 签名槽位保留：密钥在 secretariat.keyring，本模块不持有 → 如实留空
            "signature": None,
        }
        with self._lock:
            stored = self._events.append(entry)
        return dict(stored)

    def events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """最近的协作事件（按时间升序返回窗口内全部；``limit <= 0`` 返回空列表）。

        升序是为了让界面"从旧到新"讲一个协作过程；要最新一条就取 ``[-1]``。
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ShareError("SHARE_FORBIDDEN", "limit 必须是整数", field="limit")
        items = self._events.items()
        if limit <= 0:
            return []
        return [dict(item) for item in items[-limit:]]

    def verify_events(self) -> dict[str, Any]:
        """离线复算协作事件链（``{ok,count,head,problems}``）；断链只报告，不重建。"""
        return self._events.verify()

    # -- 状态 / 自检 ------------------------------------------------------
    def facts(self) -> dict[str, Any]:
        """落盘状态（供 ``/health`` 如实披露），不泄露任何 token 材料。"""
        return {
            "version": SHARING_VERSION,
            "shares": self._shares.facts(),
            "events": self._events.facts(),
            "permissions": list(PERMISSIONS),
            "default_ttl_seconds": DEFAULT_TTL_SECONDS,
            "note": "token 只存 sha256(salt+token)；明文只在创建时返回一次；"
                    "事件 signature 留空（本模块无密钥，不伪造签名）",
        }

    # -- 内部 -------------------------------------------------------------
    def _require_group(self, group_id: str) -> None:
        """用注入的 ``group_lookup`` 校验任务组存在（不存在报 GROUP_NOT_FOUND）。"""
        lookup = self._group_lookup
        if lookup is None:
            return
        try:
            found = lookup(group_id)
        except Exception as exc:  # noqa: BLE001 - 把组模块的错误码翻译成共享域错误
            code = getattr(exc, "code", None)
            if code == "GROUP_NOT_FOUND":
                raise ShareError("GROUP_NOT_FOUND", f"没有 id 为 {group_id} 的任务组",
                                 field="group_id",
                                 detail={"group_id": group_id}) from exc
            raise
        if not found:
            raise ShareError("GROUP_NOT_FOUND", f"没有 id 为 {group_id} 的任务组",
                             field="group_id", detail={"group_id": group_id})

    def _status_of(self, record: Mapping[str, Any]) -> str:
        """对外派生状态：``revoked`` > ``expired`` > ``accepted`` > ``pending``。"""
        raw = _text(record.get("status")) or "pending"
        if raw == "revoked":
            return "revoked"
        if self.expired(record):
            return "expired"
        if raw == "accepted":
            return "accepted"
        return "pending" if raw not in VISIBLE_STATUSES else raw

    def _strongest_grant(self, actor: str, group_id: str
                         ) -> tuple[int, dict[str, Any]] | None:
        """在 ``actor`` 对该组的有效授权里取等级最高的一条（同级取更晚接受的）。"""
        if not actor or not group_id:
            return None
        best: tuple[int, dict[str, Any]] | None = None
        for record in self._shares.items():
            if _text(record.get("group_id")) != group_id:
                continue
            if _text(record.get("accepted_by")) != actor:
                continue
            if self._status_of(record) != "accepted":
                continue
            level = permission_level(record.get("permission"))
            if level <= 0:
                continue
            if best is None or level > best[0]:
                best = (level, record)
        return best

    def _missing_reason(self, actor: str, group_id: str) -> str:
        """没有有效授权时给一个**可解释**的原因（过期/撤销/从未有邀请）。"""
        for record in self._shares.items():
            if _text(record.get("group_id")) != group_id:
                continue
            if _text(record.get("accepted_by")) != actor:
                continue
            status = self._status_of(record)
            if status == "expired":
                return "grant_expired"
            if status == "revoked":
                return "grant_revoked"
        return "no_grant"

    def _find_by_token(self, token: str) -> dict[str, Any] | None:
        """按明文 token 找邀请（逐条 ``sha256(salt+token)`` 定时比较，不建明文索引）。"""
        if not token:
            return None
        for record in self._shares.items():
            salt = str(record.get("salt") or "")
            expected = str(record.get("token_hash") or "")
            if not expected:
                continue
            if hmac.compare_digest(sha256_hex(salt + token), expected):
                return record
        return None

    def _public(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """公开视图：剔掉 salt / token_hash / token，补派生的 ``status`` / ``expired``。"""
        view = {key: copy.deepcopy(value) for key, value in record.items()
                if key not in SECRET_FIELDS}
        status = self._status_of(record)
        view["status"] = status
        view["expired"] = status == "expired"
        return view


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #

def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.sharing``：临时目录里跑一遍邀请→接受→越权→撤销。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(pathlib.Path(temp))
        paths.ensure()
        clock = {"now": _dt.datetime(2026, 11, 11, 12, 0, tzinfo=_dt.timezone.utc)}
        service = ShareService(paths, now=lambda: clock["now"])
        created = service.invite(group_id="grp_demo", inviter_did="did:alice",
                                 permission="view", invitee_did="did:bob")
        assert created["status"] == "pending", created
        assert "salt" not in created and "token_hash" not in created, created
        accepted = service.accept(token=created["token"], invitee_did="did:bob")
        assert accepted["status"] == "accepted", accepted
        assert service.can("did:bob", "view", "grp_demo")["allowed"] is True
        assert service.can("did:bob", "approve", "grp_demo")["allowed"] is False
        clock["now"] = clock["now"] + _dt.timedelta(hours=25)
        assert service.expired(accepted) is True
        assert service.list_for("did:alice")["issued"][0]["status"] == "expired"
        report = service.verify_events()
        assert report["ok"] is True, report
        print(f"sharing self-check ok: invites={len(service.list_for('did:alice')['issued'])} "
              f"events={report['count']} chain={report['ok']}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
