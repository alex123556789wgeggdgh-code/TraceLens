"""R5-1 · 分享链接化 +「与我共享」列表组装（REQ-005 B5-1）。

钉住的验收条款：**B5-1「分享链接在有效期内可用，过期自动失效」**，外加任务契约 §1/§2
的三条口径——

1. **链接形状**：``<base>/share.html#token=<明文 token>``。``#fragment`` 不是 ``?query``：
   浏览器不把 fragment 发给服务器（不进访问日志），落地页加载第三方资源时 ``Referer``
   也不带它——明文 token 少两条外泄路径。
2. **一次性纪律**（``sharing.py`` 头注释的 token 纪律，扩展到 ``invite_url``）：
   明文 token 与 ``invite_url`` 只在邀请创建那一刻随响应出现**一次**；
   列表 / 详情 / 事件 / 撤销响应里，token、salt、``token_hash``、``invite_url``
   一个都不许出现（逐条扫响应 JSON 全文 + 扫落盘账本文件字面量）。
3. **列表组装**：``GET /api/shares`` 消费既有 ``ShareService.list_for``，逐条走白名单投影
   （8 个对外字段），派生状态（含 ``expired``）由 ``sharing.expired()`` 判定，响应层零时间判断。

已知状态（诚实口径，不装完成）：落地页 ``web/share.html`` 已随 **R5-4** 交付
（token 只从 ``location.hash`` 读、不落任何浏览器存储、页面不回显 token）；
``POST /api/shares/accept``（T24）已随 **R5-4b** 挂载（接受流四态 + 落地页端到端用例见
``tests/test_share_accept.py``）；落地页那句「未挂服务」的文案**保留不动**，现在指向的是
其它真没挂的路由（``POST /api/shares``、``POST /api/shares/permissions``），
绝不伪装接受成功。

数据目录一律走 ``tests/scratch.py``；HTTP 出口走 ``tests/http_fixture.py``
（连接级一次性重试、幂等动词才重放、POST/断言绝不重试）。

跑法：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest tests.test_share_link -q
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import pathlib
import shutil
import sys
import unittest
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  HTTP 出口走夹具（连接级瞬态一次性重试）
from agent_explainer import feature_store as fs  # noqa: E402
from agent_explainer import groups as groups_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import settings as st  # noqa: E402  R5-4 §2.5：base_url 定档走既有 settings
from agent_explainer import sharing as sh  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402

UTC = _dt.timezone.utc
WEB = ROOT / "agent_explainer" / "web"
ALICE, BOB, CAROL = "did:alice", "did:bob", "did:carol"
LOCAL = sv.LOCAL_ACTOR_ID
FUTURE = "2099-01-01T00:00:00Z"
PAST = "2020-01-01T00:00:00Z"
#: 响应/账本里绝不允许出现的字面量（token 三兄弟 + 一次性链接 + token 前缀）
FORBIDDEN_NEEDLES = ("token", "token_hash", "salt", "invite_url", sh.TOKEN_PREFIX)
#: 逐条列表行对外的 8 个字段（与 server.SHARE_ROW_FIELDS 同一份，钉死防漂移）
ROW_KEYS = {"invite_id", "group_id", "permission", "status",
            "created_at", "expires_at", "inviter", "invitee"}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def walk_keys(node: object, path: str = "$") -> list[tuple[str, str]]:
    """递归收集响应里的 ``(路径, 键名)``：泄漏断言按键名逐条查，不靠"看起来没有"。"""
    found: list[tuple[str, str]] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            name = str(key)
            found.append((f"{path}.{name}", name))
            found.extend(walk_keys(value, f"{path}.{name}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(walk_keys(value, f"{path}[{index}]"))
    return found


def assert_no_secret_material(case: unittest.TestCase, payload: object,
                              *tokens: str, label: str = "") -> str:
    """响应全文扫一遍：秘密键名一个不许有，明文 token 一个字都不许有。"""
    text = json.dumps(payload, ensure_ascii=False)
    tag = f"（{label}）" if label else ""
    for _path, name in walk_keys(payload):
        lowered = name.lower()
        for secret in sh.SECRET_FIELDS + ("invite_url",):
            case.assertNotIn(secret, lowered,
                             f"{tag}响应里出现了秘密键 {name!r}（SECRET_FIELDS 口径）")
    for needle in FORBIDDEN_NEEDLES:
        case.assertNotIn(needle, text, f"{tag}响应全文含秘密字面量 {needle!r}")
    for token in tokens:
        if token:
            case.assertNotIn(token, text, f"{tag}响应全文含明文 token")
    return text


def seed_invite(store: fs.FeatureStore, *, invite_id: str, group_id: str,
                inviter: str, invitee: str = "", permission: str = "view",
                token: str, salt: str, created_at: str = PAST,
                expires_at: str = FUTURE, status: str = "pending",
                accepted_by: str | None = None, accepted_at: str | None = None,
                revoked_at: str | None = None) -> dict:
    """按 ``ShareService.invite`` 的落盘形状直接写一条邀请（HTTP 夹具的种子）。

    只有 ``token_hash = sha256(salt + token)`` 落盘——与 ``invite()`` 一字不差的纪律；
    测试**已知**明文 token，所以能拿它去扫响应与账本，证明"造得出来、泄不出去"。
    """
    record = {"id": invite_id, "group_id": group_id, "inviter_did": inviter,
              "invitee_did": invitee, "permission": permission,
              "token_hash": fs.sha256_hex(salt + token), "salt": salt,
              "expires_at": expires_at, "status": status, "created_at": created_at,
              "accepted_at": accepted_at, "accepted_by": accepted_by,
              "revoked_at": revoked_at}
    store.put(record)
    return record


# --------------------------------------------------------------------------- #
# 一、链接形状与 base 口径（PM 裁决 §2.1）
# --------------------------------------------------------------------------- #

class InviteLinkShapeTests(unittest.TestCase):
    """``invite_link`` / ``invite_base_url`` 是纯字符串装配，无存储依赖。"""

    def setUp(self) -> None:
        self.service = sv.InterveneService(records=[], features_root=scratch_dir("share_link_shape"))
        self.addCleanup(shutil.rmtree, pathlib.Path(self.service._features().root),
                        ignore_errors=True)
        self.token = sh.TOKEN_PREFIX + "a" * 48

    def test_link_is_landing_page_with_token_in_the_fragment_not_the_query(self) -> None:
        got = self.service.invite_link(self.token, base="http://127.0.0.1:8765")
        self.assertEqual(got["url"], "http://127.0.0.1:8765/share.html#token=" + self.token)
        #: 安全口径逐条钉：fragment 承载、没有 query、没有第二条 #
        self.assertNotIn("?", got["url"], "token 走 ?query 会进服务器访问日志")
        self.assertEqual(got["url"].count("#"), 1)
        self.assertIn(f"#{sv.SHARE_TOKEN_FRAGMENT}=", got["url"])
        self.assertEqual(got["base"], "http://127.0.0.1:8765")
        self.assertEqual(got["base_source"], "explicit")

    def test_link_falls_back_to_a_relative_path_when_no_base_is_given(self) -> None:
        for given in (None, "", "   "):
            got = self.service.invite_link(self.token, base=given)
            self.assertEqual(got["url"], f"/share.html#token={self.token}",
                             f"base={given!r} 应回落相对路径：{got}")
            self.assertEqual(got["base_source"], "relative")
            self.assertEqual(got["reason"], "base_missing")

    def test_base_is_validated_by_the_existing_base_url_policy(self) -> None:
        # 明文 http 连远端：既有的 base_url 口径直接拒（SETTINGS_URL_INSECURE），回落相对路径
        insecure = self.service.invite_base_url("http://team.example.com")
        self.assertEqual(insecure["base"], "", insecure)
        self.assertEqual(insecure["source"], "relative")
        self.assertEqual(insecure["reason"], "SETTINGS_URL_INSECURE")
        # 裸 Host（协议前缀由路由层的 LOOPBACK_SCHEME 运行时拼）：本层不猜协议
        naked = self.service.invite_base_url("127.0.0.1:8765")
        self.assertEqual(naked["base"], "", naked)
        self.assertEqual(naked["reason"], "SETTINGS_URL_MALFORMED")
        # https 远端 + 路径前缀：归一化交给既有 display（前缀保留、尾斜杠吃掉）
        remote = self.service.invite_base_url("https://team.example.com/tracelens/")
        self.assertEqual(remote["base"], "https://team.example.com/tracelens", remote)
        self.assertEqual(remote["source"], "explicit")
        link = self.service.invite_link(self.token, base="https://team.example.com/tracelens/")
        self.assertTrue(link["url"].startswith("https://team.example.com/tracelens/share.html#"),
                        link["url"])

    def test_malformed_token_never_yields_a_half_baked_url(self) -> None:
        for bad in ("", None, "  ", "abc#x", "a b", "a/b", "a?b", "a%b"):
            got = self.service.invite_link(bad, base="http://127.0.0.1:8765")
            self.assertEqual(got["url"], "", f"畸形 token {bad!r} 不该拼出 URL：{got}")
            self.assertEqual(got["reason"], "token_invalid")

    def test_no_new_http_route_is_registered_for_the_landing_page(self) -> None:
        """铁律 4 自证：``/share.html`` 是静态落地页，不是新注册的 API 路由。"""
        declared = sv.declared_api_paths()
        values = {value for value in declared.values() if isinstance(value, str)}
        self.assertNotIn(sv.SHARE_LANDING_PATH, values,
                         "/share.html 不得成为 API_PATH_* 常量（本轮零新增 HTTP 路由）")
        self.assertEqual("/api/shares", sv.API_PATH_SHARES)
        self.assertEqual("/api/shares/accept", sv.API_PATH_SHARES_ACCEPT)
        self.assertEqual("/api/shares/permissions", sv.API_PATH_SHARES_PERMISSIONS)
        self.assertEqual(sv.SHARE_LANDING_PATH, "/share.html")


# --------------------------------------------------------------------------- #
# 二、一次性纪律：token / invite_url 只出现一次，落盘零明文
# --------------------------------------------------------------------------- #

class OneShotDisciplineTests(unittest.TestCase):
    """响应侧 + 账本侧的泄漏扫描（B5-1 的安全半边；铁律 2/3 的可执行化）。"""

    def setUp(self) -> None:
        self.base = scratch_dir("share_link_oneshot")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.groups = groups_mod.GroupService(self.paths)
        self.group_id = self.groups.create(agent_ids=["agent-a"], title="R5-1 分享演练")["id"]
        # InterveneService 在自己的 share_service 上懒装配（同一个 features 根）
        self.service = sv.InterveneService(records=[], features_root=self.base)
        self.invited = self.service.share_invite(
            self.group_id, {"permission": "view", "invitee_did": BOB,
                            "ttl_seconds": 3600},
            actor_id=ALICE)
        self.token = self.invited["token"]
        self.ledger = self.paths.shares.read_text(encoding="utf-8")
        self.events = self.paths.collab_events.read_text(encoding="utf-8")

    def test_invite_response_carries_invite_url_and_token_exactly_once_each(self) -> None:
        self.assertTrue(self.invited["ok"], self.invited)
        self.assertEqual(self.invited["invite_url"],
                         f"/share.html#token={self.token}", self.invited["invite_url"])
        body = json.dumps(self.invited, ensure_ascii=False)
        self.assertEqual(body.count(self.token), 2,
                         "明文 token 只允许出现在顶层 token 与 invite_url 两处")
        self.assertEqual(body.count("/share.html#token="), 1)
        # share 对象（列表/详情复用的那一层）既无 token 也无 invite_url
        share = self.invited["share"]
        for key in tuple(sh.SECRET_FIELDS) + ("invite_url",):
            self.assertNotIn(key, share, f"share 对象泄漏 {key}")

    def test_plaintext_token_is_absent_from_the_shares_ledger(self) -> None:
        self.assertNotIn(self.token, self.ledger, "明文 token 落盘了")
        self.assertNotIn("invite_url", self.ledger, "一次性链接被写进了账本")
        # 反面证据：落盘只有哈希 + 盐（"零明文"不等于"什么都没写"）
        self.assertIn("token_hash", self.ledger)
        self.assertIn("salt", self.ledger)

    def test_collab_events_never_carry_the_token_or_the_link(self) -> None:
        self.assertNotIn(self.token, self.events, "协作事件里出现明文 token")
        self.assertNotIn("/share.html", self.events, "协作事件里出现邀请链接")
        for needle in ("token_hash", "salt"):
            self.assertNotIn(needle, self.events, f"协作事件里出现 {needle}")
        report = self.service.share_service.verify_events()
        self.assertTrue(report["ok"], report)

    def test_url_bearing_detail_keys_are_scrubbed_at_write_time(self) -> None:
        """R5-1 扩的脱敏词（``url`` / ``link``）：调用方把链接塞进 detail 也泄不出去。"""
        entry = self.service.share_service.record_event(
            actor=ALICE, action="share.debug", target="inv_probe",
            detail={"token": self.token, "invite_url": self.invited["invite_url"],
                    "share_link": "x", "keep": "ok",
                    "nested": {"salt": "s", "group_id": self.group_id}})
        detail = entry["detail"]
        for key in ("token", "invite_url", "share_link"):
            self.assertEqual(detail[key], "<redacted>", key)
        self.assertEqual(detail["nested"]["salt"], "<redacted>")
        self.assertEqual(detail["keep"], "ok", "非敏感键照常保留")
        self.assertEqual(detail["nested"]["group_id"], self.group_id)
        text = self.paths.collab_events.read_text(encoding="utf-8")
        self.assertNotIn(self.token, text, "打码后账本里仍有明文 token")

    def test_service_level_list_and_detail_and_revoke_are_secret_free(self) -> None:
        shares = self.service.share_service
        accepted = shares.accept(token=self.token, invitee_did=BOB)
        detail = shares.get(str(accepted["id"]))
        listing = self.service.shares_payload(actor_id=ALICE)
        revoked = shares.revoke(str(accepted["id"]), actor=ALICE)
        assert_no_secret_material(self, {"accept": accepted, "get": detail,
                                         "list": listing, "revoke": revoked},
                                  self.token, label="邀请族响应全景")
        self.assertEqual(revoked["status"], "revoked")


# --------------------------------------------------------------------------- #
# 三、有效期语义（B5-1 的字面要求：有效期内可用、过期自动失效）
# --------------------------------------------------------------------------- #

class ExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("share_link_expiry")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.clock = {"now": _dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)}
        self.groups = groups_mod.GroupService(self.paths, now=lambda: self.clock["now"])
        self.group_id = self.groups.create(agent_ids=["agent-a"], title="有效期演练")["id"]
        self.shares = sh.ShareService(self.paths, now=lambda: self.clock["now"],
                                      group_lookup=self.groups.get)

    def advance(self, hours: float) -> None:
        self.clock["now"] = self.clock["now"] + _dt.timedelta(hours=hours)

    def invite(self, **kwargs) -> dict:
        kwargs.setdefault("ttl_seconds", 24)
        return self.shares.invite(group_id=self.group_id, inviter_did=ALICE,
                                  permission="view", invitee_did=BOB, **kwargs)

    def test_link_is_usable_inside_the_window_and_grants_view_only(self) -> None:
        created = self.invite()
        accepted = self.shares.accept(token=created["token"], invitee_did=BOB)
        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(self.shares.expired(accepted))
        view = self.shares.can(BOB, "view", self.group_id)
        self.assertTrue(view["allowed"], view)
        approve = self.shares.can(BOB, "approve", self.group_id)
        self.assertFalse(approve["allowed"], approve)
        self.assertEqual(approve["reason"], "level_too_low")

    def test_expiry_flips_list_status_and_revokes_the_grant(self) -> None:
        self.invite()
        self.advance(25)
        listing = self.shares.list_for(ALICE)
        self.assertEqual([row["status"] for row in listing["issued"]], ["expired"], listing)
        denied = self.shares.can(BOB, "view", self.group_id)
        self.assertFalse(denied["allowed"], denied)
        self.assertEqual(denied["reason"], "no_grant", "未接受的邀请过期 → 从未有有效授权")
        self.assertTrue(self.shares.expired(listing["issued"][0]))

    def test_accepted_invite_also_stops_working_when_the_link_expires(self) -> None:
        created = self.invite()
        accepted = self.shares.accept(token=created["token"], invitee_did=BOB)
        self.assertTrue(self.shares.can(BOB, "view", self.group_id)["allowed"])
        self.advance(25)
        listing = self.shares.list_for(BOB)
        self.assertEqual([row["status"] for row in listing["shared_with_me"]], ["expired"],
                         listing)
        denied = self.shares.can(BOB, "view", self.group_id)
        self.assertFalse(denied["allowed"], denied)
        self.assertEqual(denied["reason"], "grant_expired", denied)
        self.assertIsNone(self.shares.grant_for(BOB, self.group_id))

    def test_accepting_an_expired_link_is_share_expired(self) -> None:
        created = self.invite()
        self.advance(25)
        with self.assertRaises(sh.ShareError) as caught:
            self.shares.accept(token=created["token"], invitee_did=BOB)
        self.assertEqual(caught.exception.code, "SHARE_EXPIRED")
        self.assertEqual(caught.exception.http_status, 409)

    def test_revoke_marks_revoked_and_kills_the_token(self) -> None:
        created = self.invite()
        revoked = self.shares.revoke(str(created["id"]), actor=ALICE)
        self.assertEqual(revoked["status"], "revoked")
        listing = self.shares.list_for(ALICE)
        self.assertEqual([row["status"] for row in listing["issued"]], ["revoked"], listing)
        with self.assertRaises(sh.ShareError) as caught:
            self.shares.accept(token=created["token"], invitee_did=BOB)
        self.assertEqual(caught.exception.code, "SHARE_TOKEN_INVALID")

    def test_derived_status_precedence_is_revoked_then_expired(self) -> None:
        created = self.invite()
        self.advance(25)
        revoked = self.shares.revoke(str(created["id"]), actor=ALICE)
        self.assertEqual(revoked["status"], "revoked", "撤销优先于过期（人做的事优先）")
        self.assertFalse(revoked["expired"], "派生 expired 只在 status==expired 时为真")


# --------------------------------------------------------------------------- #
# 四、列表组装：白名单投影 + 多用户隔离（「与我共享」的服务端半边）
# --------------------------------------------------------------------------- #

class ListAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("share_link_list")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.clock = {"now": _dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)}
        self.groups = groups_mod.GroupService(self.paths, now=lambda: self.clock["now"])
        self.group_id = self.groups.create(agent_ids=["agent-a"], title="列表组装演练")["id"]
        self.shares = sh.ShareService(self.paths, now=lambda: self.clock["now"],
                                      group_lookup=self.groups.get)
        self.service = sv.InterveneService(records=[], features_root=self.base)

    def test_row_projection_is_the_eight_field_whitelist(self) -> None:
        created = self.shares.invite(group_id=self.group_id, inviter_did=ALICE,
                                     permission="approve", invitee_did=BOB)
        public = self.shares.get(str(created["id"]))
        row = self.service.share_list_row(public)
        self.assertEqual(set(row), ROW_KEYS, row)
        self.assertEqual(tuple(sv.SHARE_ROW_FIELDS), tuple(row), "常量与投影顺序一致")
        self.assertEqual(row["invite_id"], created["id"])
        self.assertEqual(row["group_id"], self.group_id)
        self.assertEqual(row["permission"], "approve")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["inviter"], ALICE)
        self.assertEqual(row["invitee"], BOB)
        self.assertEqual(row["created_at"], "2026-11-11T12:00:00Z")
        self.assertEqual(row["expires_at"], "2026-11-12T12:00:00Z")
        for key in tuple(sh.SECRET_FIELDS) + ("invite_url", "expired", "accepted_by"):
            self.assertNotIn(key, row, f"投影里不该有 {key}")

    def test_projection_tolerates_missing_keys_without_inventing_values(self) -> None:
        row = self.service.share_list_row({"id": "inv_x", "status": "pending"})
        self.assertEqual(set(row), ROW_KEYS)
        self.assertIsNone(row["group_id"])
        self.assertIsNone(row["inviter"])

    def test_payload_top_level_shape_stays_docs_t22(self) -> None:
        self.shares.invite(group_id=self.group_id, inviter_did=LOCAL, permission="view",
                           invitee_did=BOB)
        self.shares.invite(group_id=self.group_id, inviter_did=ALICE, permission="view",
                           invitee_did=LOCAL)
        payload = self.service.shares_payload(actor_id=LOCAL)
        self.assertEqual(set(payload), {"ok", "issued", "shared_with_me", "count"}, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"],
                         len(payload["issued"]) + len(payload["shared_with_me"]))
        for row in payload["issued"] + payload["shared_with_me"]:
            self.assertEqual(set(row), ROW_KEYS, row)
        assert_no_secret_material(self, payload, label="GET /api/shares 响应体")

    def test_multi_user_isolation_sees_only_ones_own_rows(self) -> None:
        self.shares.invite(group_id=self.group_id, inviter_did=ALICE,
                           permission="view", invitee_did=BOB)
        for actor, issued, shared in ((ALICE, 1, 0), (BOB, 0, 1), (CAROL, 0, 0)):
            payload = self.service.shares_payload(actor_id=actor)
            self.assertEqual(len(payload["issued"]), issued, actor)
            self.assertEqual(len(payload["shared_with_me"]), shared, actor)
            self.assertEqual(payload["count"], issued + shared, actor)
        bob_row = self.service.shares_payload(actor_id=BOB)["shared_with_me"][0]
        self.assertEqual(bob_row["invitee"], BOB)
        self.assertEqual(bob_row["inviter"], ALICE)

    def test_rows_carry_the_derived_status_without_time_logic_in_the_response_layer(self) -> None:
        created = self.shares.invite(group_id=self.group_id, inviter_did=ALICE,
                                     permission="view", invitee_did=BOB)
        self.clock["now"] = self.clock["now"] + _dt.timedelta(hours=25)
        rows = self.service.shares_payload(actor_id=ALICE)["issued"]
        self.assertEqual([row["status"] for row in rows], ["expired"], rows)
        self.assertEqual(rows[0]["expires_at"], "2026-11-12T12:00:00Z",
                         "expires_at 原样抄：判过期由 sharing.expired() 负责")
        self.assertEqual(created["status"], "pending", "记录本体零改写（历史不可重写）")


# --------------------------------------------------------------------------- #
# 五、真发 HTTP 的 GET /api/shares（挂载路由的响应层，不 mock handler）
# --------------------------------------------------------------------------- #

class ShareHttpTests(unittest.TestCase):
    TOKENS = {"inv_a": "shr_" + "a" * 48, "inv_b": "shr_" + "b" * 48,
              "inv_c": "shr_" + "c" * 48, "inv_d": "shr_" + "d" * 48}

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = scratch_dir("share_link_http")
        cls.paths = FeaturePaths(cls.base)
        cls.paths.ensure()
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy([]), web_dir=WEB,
            allow_reset=True, features_root=cls.base)
        cls.host, cls.port = cls.server.server_address[:2]
        # 实测口径（不猜）：local 模式下 GET /api/shares 用的 actor 是会话的 user_id
        # = ``"local"``，**不是** ``server.LOCAL_ACTOR_ID``（"local-user"，那是无会话时的
        # 兜底值）。种子行按服务器真正会问的那个身份写，免得列表"看着是空的"被当成 bug。
        cls.actor = cls.server.service._actor_id_of(
            cls.server.service.auth.local_session())
        store = fs.open_store(cls.paths.shares)
        # cls.actor 发出的两条（一条活、一条已过期）+ 分享给 cls.actor 的一条 + 无关的一条
        seed_invite(store, invite_id="inv_a", group_id="grp_http", inviter=cls.actor,
                    invitee=BOB, permission="view", token=cls.TOKENS["inv_a"],
                    salt="s" * 32, created_at="2026-11-11T10:00:00Z", expires_at=FUTURE)
        seed_invite(store, invite_id="inv_c", group_id="grp_http", inviter=cls.actor,
                    invitee=CAROL, permission="manage", token=cls.TOKENS["inv_c"],
                    salt="u" * 32, created_at="2026-11-09T10:00:00Z",
                    expires_at=PAST, status="accepted",
                    accepted_by=CAROL, accepted_at="2026-11-09T11:00:00Z")
        seed_invite(store, invite_id="inv_b", group_id="grp_http", inviter=ALICE,
                    invitee=cls.actor, permission="approve", token=cls.TOKENS["inv_b"],
                    salt="t" * 32, created_at="2026-11-11T09:00:00Z",
                    expires_at=FUTURE, status="accepted",
                    accepted_by=cls.actor, accepted_at="2026-11-11T09:30:00Z")
        seed_invite(store, invite_id="inv_d", group_id="grp_other", inviter="did:dave",
                    invitee="did:erin", permission="view", token=cls.TOKENS["inv_d"],
                    salt="v" * 32, created_at="2026-11-11T08:00:00Z", expires_at=FUTURE)
        fx.wait_port_ready(cls.port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.base, ignore_errors=True)

    def get(self, path: str) -> tuple[int, dict, object]:
        def once(conn):
            conn.request("GET", path, None, {"Connection": "close"})
            resp = conn.getresponse()
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            text = raw.decode("utf-8", "replace")
            try:
                data: object = json.loads(text)
            except json.JSONDecodeError:
                data = text
            return resp.status, headers, data
        # 连接级瞬态一次性重试；GET 幂等才重放，断言级失败绝不重试（tests/http_fixture.py）
        return fx.with_connection(self.host, self.port, once, verb="GET",
                                  path=path, timeout=8.0)

    def test_list_endpoint_returns_the_assembled_projection(self) -> None:
        status, _headers, body = self.get("/api/shares")
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), {"ok", "issued", "shared_with_me", "count"}, body)
        ids = [row["invite_id"] for row in body["issued"]]
        self.assertEqual(sorted(ids), ["inv_a", "inv_c"], body)
        self.assertEqual([row["invite_id"] for row in body["shared_with_me"]], ["inv_b"], body)
        self.assertEqual(body["count"], 3, body)
        for row in body["issued"] + body["shared_with_me"]:
            self.assertEqual(set(row), ROW_KEYS, row)

    def test_expired_share_is_reported_as_expired_over_http(self) -> None:
        status, _headers, body = self.get("/api/shares")
        self.assertEqual(status, 200, body)
        by_id = {row["invite_id"]: row for row in body["issued"]}
        self.assertEqual(by_id["inv_c"]["status"], "expired", by_id["inv_c"])
        self.assertEqual(by_id["inv_a"]["status"], "pending", by_id["inv_a"])
        self.assertEqual([row["status"] for row in body["shared_with_me"]], ["accepted"])

    def test_http_response_leaks_no_token_salt_hash_or_invite_url(self) -> None:
        status, _headers, body = self.get("/api/shares")
        self.assertEqual(status, 200, body)
        assert_no_secret_material(self, body, *self.TOKENS.values(),
                                  label="GET /api/shares")

    def test_unrelated_invites_are_not_listed_for_this_actor(self) -> None:
        status, _headers, body = self.get("/api/shares")
        self.assertEqual(status, 200, body)
        listed = {row["invite_id"] for row in body["issued"] + body["shared_with_me"]}
        self.assertNotIn("inv_d", listed, "与当前身份无关的邀请泄进了列表")

    def test_landing_page_is_not_an_api_route(self) -> None:
        """铁律 4 的 HTTP 侧证据：本轮零新增路由——``/api/share.html`` 没有 API 接住者。

        落地页是**静态文件** ``/share.html``（R5-4 交付），不是 API 端点：
        现在它落到静态目录的"文件不存在"口径（``STATIC_NOT_FOUND``），
        两种"没有这条 API 路由"的写法都如实接受，与 R5-4 之后文件存不存在无关。
        """
        status, _headers, body = self.get("/api/share.html")
        self.assertEqual(status, 404, f"多出一条接住 share.html 的 API 路由：{status} {body}")
        code = str(body.get("code")) if isinstance(body, Mapping) else ""
        self.assertIn(code, ("ROUTE_NOT_FOUND", "STATIC_NOT_FOUND"), body)


class BaseUrlConfigTests(unittest.TestCase):
    """R5-4 §2.5 · ``server.base_url`` 定档（PM-VERDICT-R5-1 §3.2 批准的 L1 事项）。

    钉住四件事：

    1. **配置项住在既有 settings 通道**（``settings`` 的 ``server.base_url``），
       不另起存储：写路径严格（``parse_base_url`` 同一把尺子，非回环明文 ``http``
       报 ``SETTINGS_URL_INSECURE``），读路径宽容（手改坏值 → 回落未配置，不炸服务）。
    2. **路由装配层真把它递给** ``share_invite(..., base_url=...)``：端到端 HTTP 上，
       配好之后 ``invite_url`` 的前缀就是配置值；没配 = 相对路径回落，**逐字等于 R5-1 现状**。
    3. **base 不接受调用方指定**：请求体里塞 ``base_url`` 不改落点（钓鱼站点口径）。
    4. **响应形状零漂移**：``{ok, share, token, invite_url, note}`` 五个键，不多不少。
    """

    GROUP = "grp-r54-base"

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("share_base_url")
        cls.paths = FeaturePaths(cls.root)
        cls.paths.ensure()
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=[], web_dir=WEB, allow_reset=True, quiet=True,
            features_root=cls.root, settings_path=cls.root / "settings.json",
            audit_log_path=cls.root / "audit.jsonl",
            ingest_token_path=cls.root / "ingest.json")
        cls.host, cls.port = cls.server.server_address[:2]
        cls.service = cls.server.service
        # 组长本人 = local 免登录（R5-4 收紧后发邀请只认组长/组内 manage）：
        # 不传 actor 的出厂写法把 created_by 落成 "local"，与会话 actor 事实相等。
        cls.service.group_service.create(title="R5-4 base 定档组", agent_ids=["agent-r54"],
                                         group_id=cls.GROUP)
        fx.wait_port_ready(cls.port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.root, ignore_errors=True)

    # -- 助手 --
    def setUp(self) -> None:
        """逐用例复位 base_url：用例之间不串味（顺序变了也不会假绿/假红）。"""
        try:
            self.service.settings.apply({"server": {"base_url": ""}}, actor_id="local-user")
        except st.SettingsError as exc:            # 本来就是空的 → 没有改变
            self.assertEqual(exc.code, "SETTINGS_NO_CHANGES", exc.code)

    def post(self, path: str, body: object) -> tuple[int, object]:
        def once(conn):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, payload,
                         {"Content-Type": "application/json", "Connection": "close"})
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", "replace")
            try:
                data: object = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
            return resp.status, data
        # POST 绝不重放（tests/http_fixture.py：服务端可能已落这条写请求）
        return fx.with_connection(self.host, self.port, once, verb="POST",
                                  path=path, timeout=8.0)

    def set_base(self, value: str) -> dict:
        """写 ``server.base_url``；值没变就按既有哨兵原样接受视图（``SETTINGS_NO_CHANGES``
        是"没改变"的事实，不是失败，这里不拿它当异常）。"""
        view = self.service.settings.public()
        if str(value) == str(view["server"]["base_url"]):
            return view
        view, _changed = self.service.settings.apply(
            {"server": {"base_url": value}}, actor_id="local-user")
        return view

    def share(self) -> tuple[int, dict]:
        status, data = self.post(f"{sv.API_PATH_GROUPS}/{self.GROUP}/share",
                                 {"permission": "view"})
        return status, (data if isinstance(data, Mapping) else {})

    # -- 配置面 --
    def test_unset_by_factory_and_exposed_in_the_public_view(self) -> None:
        """出厂为空 = 未配置：``public()`` 的 ``server`` 块如实说"回落相对路径"。"""
        view = self.service.settings.public()
        self.assertEqual(view["server"]["base_url"], "", view["server"])
        self.assertFalse(view["server"]["configured"], view["server"])
        self.assertIn("share.html", view["server"]["note"], view["server"])
        self.assertEqual(self.service.share_base_url(), "")

    def test_apply_normalises_and_the_service_reads_it(self) -> None:
        """写路径：归一化交给既有 ``parse_base_url``（前缀保留、尾斜杠吃掉）。"""
        self.set_base("https://team.example.com/tracelens/")
        self.assertEqual(self.service.share_base_url(), "https://team.example.com/tracelens")
        view = self.service.settings.public()
        self.assertTrue(view["server"]["configured"], view["server"])
        self.set_base("http://127.0.0.1:8899")
        self.assertEqual(self.service.share_base_url(), "http://127.0.0.1:8899",
                         "本机回环明文 http 必须收（同源部署的常态）")

    def test_apply_rejects_plaintext_to_a_non_loopback_host(self) -> None:
        """明文 http 连远端：既有码 ``SETTINGS_URL_INSECURE`` 原样拒，配置零写入。"""
        with self.assertRaises(st.SettingsError) as ctx:
            self.set_base("http://team.example.com")
        self.assertEqual(ctx.exception.code, "SETTINGS_URL_INSECURE", ctx.exception.code)
        self.assertEqual(self.service.settings.public()["server"]["base_url"], "")

    def test_apply_is_strict_about_section_and_fields(self) -> None:
        """段与字段的严格性：``server`` 只装 ``base_url``，别的字段与写法一律拒。"""
        cases = (({"server": "http://127.0.0.1:1"}, "SETTINGS_FIELD_TYPE"),
                 ({"server": {"port": 8080}}, "SETTINGS_FIELD_UNKNOWN"),
                 ({"server": {}}, "SETTINGS_NO_CHANGES"),
                 ({"serve": {"base_url": "http://127.0.0.1:1"}}, "SETTINGS_SECTION_UNKNOWN"))
        for patch, code in cases:
            with self.assertRaises(st.SettingsError) as ctx:
                self.service.settings.apply(patch, actor_id="local-user")
            self.assertEqual(ctx.exception.code, code, f"{patch} → {ctx.exception.code}")

    def test_load_is_tolerant_of_a_hand_edited_bad_value(self) -> None:
        """读路径宽容：用户手改坏值 → 归零（未配置），服务不炸、链接回落相对路径。"""
        store = st.SettingsStore(self.root / "bad-server-base.json", mode="local",
                                 writable=True)
        store.load()
        self.assertEqual(store.public()["server"]["base_url"], "")
        bad = st.defaults_doc()
        bad["server"]["base_url"] = "ht!tp://team.example.com"
        (self.root / "bad-server-base.json").write_text(
            json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        reloaded = st.SettingsStore(self.root / "bad-server-base.json", mode="local",
                                    writable=True)
        reloaded.load()
        self.assertEqual(reloaded.public()["server"]["base_url"], "",
                         "坏值不该把服务端的启动/加载路径炸掉")
        self.assertFalse(reloaded.public()["server"]["configured"])

    # -- 装配面（端到端）--
    def test_configured_base_prefixes_the_one_time_invite_url(self) -> None:
        """端到端：配好 base 后，``invite_url`` 的前缀就是配置值，token 仍在 ``#fragment``。"""
        self.set_base("http://127.0.0.1:8899")
        status, data = self.share()
        self.assertEqual(status, 201, data)
        self.assertEqual(set(data), {"ok", "share", "token", "invite_url", "note"}, data)
        self.assertEqual(str(data["invite_url"]),
                         f"http://127.0.0.1:8899{sv.SHARE_LANDING_PATH}"
                         f"#{sv.SHARE_TOKEN_FRAGMENT}={data['token']}", data)
        self.assertNotIn("?", str(data["invite_url"]), "token 走 ?query 会进访问日志")

    def test_unconfigured_keeps_the_relative_fallback_byte_for_byte(self) -> None:
        """未配置 = R5-1 现状逐字不变：``/share.html#token=…``（相对路径）。"""
        self.set_base("")
        self.assertEqual(self.service.share_base_url(), "")
        status, data = self.share()
        self.assertEqual(status, 201, data)
        self.assertEqual(str(data["invite_url"]),
                         f"{sv.SHARE_LANDING_PATH}#{sv.SHARE_TOKEN_FRAGMENT}={data['token']}",
                         data)

    def test_the_request_body_cannot_choose_the_landing_host(self) -> None:
        """base 由服务端配置说了算：请求体里塞 ``base_url`` 不改落点（防钓鱼站点）。"""
        self.set_base("http://127.0.0.1:8899")
        status, data = self.post(f"{sv.API_PATH_GROUPS}/{self.GROUP}/share",
                                 {"permission": "view", "base_url": "https://evil.example"})
        self.assertEqual(status, 201, data)
        self.assertTrue(str(data["invite_url"]).startswith("http://127.0.0.1:8899"), data)
        self.assertNotIn("evil.example", json.dumps(data, ensure_ascii=False), data)
        self.set_base("")


if __name__ == "__main__":  # 手工自检：python tests/test_share_link.py -v
    unittest.main(verbosity=2)
