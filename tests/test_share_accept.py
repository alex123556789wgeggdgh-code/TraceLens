"""R5-4b 验收测试：T24 接受端点 ``POST /api/shares/accept`` 挂载（L1 已批）。

任务书：``_pm-dispatch/TASK-R5-4b-accept-mount.md``。PM-VERDICT-R5-4 §3① 独立核实的
挂载缺口——服务方法 ``share_accept``（server.py）与 ``sharing.accept`` 早已就绪、
GET 侧有显式 405（hint「accept is POST only」），但 ``do_POST`` 没有这条分支，
POST 落 ``ROUTE_NOT_FOUND``，落地页只能如实报「这个地址没挂服务」。

这个文件钉住挂载后的六件事（§2.2 的六例 + 三条边界）：

1. **接受成功**：``{token}`` → 200、形状 ``{ok, share}``（不多塞键）、``status=accepted``、
   ``accepted_by`` = **当前会话的 actor**（local 免登录 = 会话 ``user_id`` = ``local``，
   决定段/审计的 ``local-user`` 是另一条口径，由 ``operator_ids()`` 两个都收进来）；
2. **四态拒绝**：过期 → ``SHARE_EXPIRED``(409)、撤销 → ``SHARE_TOKEN_INVALID``(400)、
   未知/缺 token → ``SHARE_TOKEN_INVALID``(400)、被别人先接受 → ``SHARE_TOKEN_USED``(409)
   ——**全部复用既有 ``SHARE_*`` 码族，零新码名**（逐条查 ``server.ERROR_TABLE`` 自证）；
3. **重复接受**：同一个人再点一次 = 幂等成功（``sharing.accept`` 的既有语义，逐字不变）；
4. **事件账本 append-only 自证**：成功接受按既有实现留两条痕（服务层 ``share.accept``
   + 端点层 ``accept``）、``verify_events().ok=true``；幂等的重复接受只留端点层那一条
   （服务层零写入）；被拒的那几次**一条都不追加**；
5. **落地页端到端**：按 ``web/share.html`` 源码里的真实请求形态（同一路径、同一个
   ``token`` 键、同一组头）在 HTTP 上原样走通，并接受后 ``GET /api/shares/permissions``
   以端点口径看到授权生效（``can()`` 的语义由既有测试负责，这里只验端点）；
6. **只挂这一格**：``GET`` 侧 405 逐字未动，``POST /api/shares`` 与
   ``POST /api/shares/permissions`` 仍落 ``ROUTE_NOT_FOUND``（落地页那句「未挂服务」
   的文案因此对其它未挂路由继续有效）；契约里 ``POST /api/shares/accept`` 出现、
   ``API_PATH_SHARES_ACCEPT`` 从「声明未挂载」清单消失。

鉴权口径（§2.1 的取舍，报告 REPORT-R5-4b 同段）：权限位取 docs/07 §5 已冻结的
``records.read``；``sharing.accept(token, invitee_did)`` 的既有签名把「谁在接受」当必需项
（空 ``invitee_did`` 直接 ``SHARE_FORBIDDEN``），所以 team 模式**要会话**——
匿名接受等于替受邀者编一个身份、把邀请烧成 ``accepted``、把伪造身份写进事件账本。
local 免登录（产品默认形态）零登录直接可用。CSRF 与 T23 同款：只对被 Cookie
证明身份的写请求做双提交校验，``X-TraceLens-Token`` 通道与 local 通道天然豁免。

传输纪律（铁律 4）：HTTP 一律走 ``tests/http_fixture.py``——连接级瞬态一次性重连，
GET/HEAD 幂等才重放，**POST 绝不重放**；临时目录一律 ``tests/scratch.py``。
前端零改动（铁律 1）：本文件只**读** ``share.html`` 源码做口径对齐，不写它一个字。

跑法：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest tests.test_share_accept -q
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，POST 绝不重放

from agent_explainer import auth as auth_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
SHARE_HTML = (WEB / "share.html").read_text(encoding="utf-8")

CAPTAIN = "did:r54b-captain"
#: local 免登录会话在**分享/协作层**的 actor 口径：``_actor_id_of(local_session)``
#: 读会话的 ``user_id``，实测是 ``"local"``（``auth.LOCAL_USER_ID``），
#: 而决定段/审计走 ``sv.LOCAL_ACTOR_ID`` = ``"local-user"``——R5-3 的**双身份口径**
#: 由 ``service.operator_ids()`` 两个都收进来自我弥合，本轮不改这个既有事实，只钉下来。
LOCAL = auth_mod.LOCAL_USER_ID
GROUP = "grp-r54b-group"
PW = "R54bpassw0rd!longenough"
#: 响应全文绝不允许出现的秘密字面量（一次性纪律，与 test_share_link 同口径）
SECRET_NEEDLES = ("\"token\"", "token_hash", "\"salt\"", "invite_url")
#: 本轮允许出现的码名（**零新码名**：全部来自既有族，且逐条在 server.ERROR_TABLE 里）
ALLOWED_CODES = {"SHARE_TOKEN_INVALID", "SHARE_EXPIRED", "SHARE_TOKEN_USED",
                 "SHARE_FORBIDDEN", "SHARE_NOT_FOUND", "AUTH_REQUIRED",
                 "CSRF_FAILED", "ROLE_FORBIDDEN", "METHOD_NOT_ALLOWED",
                 "ROUTE_NOT_FOUND"}


def call(base: str, path: str, body: object | None = None, method: str = "GET",
         headers: Mapping[str, str] | None = None) -> tuple[int, dict, object]:
    """``(status, headers, json body)``；4xx/5xx 也带回落盘体（要看错误码）。

    连接建立阶段的瞬态**任何动词**重连一次；请求已发出后只有幂等动词重放一次，
    POST/PUT/PATCH/DELETE 绝不重放（tests/http_fixture.py）。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    extra = {"Connection": "close"}
    if data is not None:
        extra["Content-Type"] = "application/json"
    extra.update(dict(headers or {}))

    def once():
        request = urllib.request.Request(base + path, data=data, method=method, headers=extra)
        try:
            with urllib.request.urlopen(request, timeout=20.0) as response:
                text = response.read().decode("utf-8", "replace")
                status = response.status
                head = {k.lower(): v for k, v in response.getheaders()}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            status = exc.code
            head = {k.lower(): v for k, v in exc.headers.items()}
            exc.close()
        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return status, head, payload

    return fx.urlopen_resilient(once, verb=method, path=path, timeout=20.0)


def bearer(token: str) -> dict[str, str]:
    """token 通道（专用头）：CSRF 双提交对它豁免，鉴权形状与 Cookie 通道一致。"""
    return {auth_mod.TOKEN_HEADER: token}


def code_of(payload: object) -> str:
    return str(payload.get("code")) if isinstance(payload, Mapping) else ""


def accept_path() -> str:
    return sv.API_PATH_SHARES_ACCEPT


class AcceptMountCase(unittest.TestCase):
    """公共夹具：临时数据根 + 真 HTTP 服务器 + 一个任务组；逐类自建自拆。"""

    mode = "local"

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("share-accept")
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=[], web_dir=WEB, allow_reset=True,
            quiet=True, features_root=cls.root / "features",
            audit_log_path=cls.root / "audit.jsonl",
            ingest_token_path=cls.root / "ingest.json",
            settings_path=cls.root / "settings.json",
            auth_state_path=cls.root / "auth.json",
            mode=cls.mode, bootstrap_admin=(cls.mode == "team"), session_ttl=3600)
        cls.service = cls.server.service
        cls.base = cls.server.url()
        cls.host, cls.port = cls.server.server_address[:2]
        fx.wait_port_ready(cls.port)
        cls.boot = cls.service.auth.bootstrap_report or {}
        cls.admin_pw = str(cls.boot.get("password") or "")
        cls.service.group_service.create(title="R5-4b 接受验收组", agent_ids=["agent-r54b"],
                                         actor=CAPTAIN, group_id=GROUP)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.root, ignore_errors=True)

    # -- 邀请助手（走 ``sharing.invite`` 真路径：token 明文只在这里出现一次）--
    @classmethod
    def invite(cls, *, permission: str = "view", group_id: str = GROUP,
               inviter: str = CAPTAIN, invitee: str = "",
               ttl_seconds: int | None = None) -> dict:
        kwargs: dict[str, object] = {"group_id": group_id, "inviter_did": inviter,
                                     "permission": permission, "invitee_did": invitee}
        if ttl_seconds is not None:
            kwargs["ttl_seconds"] = ttl_seconds
        return cls.service.share_service.invite(**kwargs)  # type: ignore[arg-type]

    # -- 取证助手 ---------------------------------------------------------
    def events(self) -> list[dict]:
        return list(self.service.share_service.events(limit=200))

    def events_count(self) -> int:
        return int(((self.service.share_service.facts() or {}).get("events") or {})
                   .get("items") or 0)

    def post_accept(self, token: object, *, headers: Mapping[str, str] | None = None,
                    body: object | None = None) -> tuple[int, dict, object]:
        """落地页同形状的一发 POST：``{"token": ...}`` + ``application/json``。"""
        payload = {"token": token} if body is None else body
        return call(self.base, accept_path(), payload, method="POST", headers=headers)

    def assert_denied_code(self, status: int, data: object, expected: str) -> str:
        """拒绝必须是**既有码族**的那个码，且码名已在 ``server.ERROR_TABLE`` 注册。"""
        got = code_of(data)
        self.assertEqual(expected, got, f"码名漂移：{status} {data}")
        self.assertIn(got, ALLOWED_CODES, f"引入了新错误码：{got}")
        self.assertIn(got, sv.ERROR_TABLE, f"{got} 不在既有码表里（零新码名铁律）")
        self.assertIsInstance(data, Mapping, f"错误体形状不对：{data}")
        self.assertTrue(str(data.get("message") or ""), f"{got} 的人话 message 为空")
        return got


class LocalAcceptHttpTests(AcceptMountCase):
    """产品默认形态（local 免登录）：落地页的真实路径 —— 零登录、直接接受。"""

    mode = "local"

    def test_post_accept_is_mounted_and_binds_the_current_actor(self) -> None:
        """挂载本身：200 + 形状 ``{ok, share}`` + ``accepted_by`` = 当前会话 actor。"""
        created = self.invite()
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(status, 200, f"T24 没挂上或被拒：{status} {data}")
        self.assertEqual({"ok", "share"}, set(data), f"响应多塞了键：{data}")
        share = data["share"]
        self.assertEqual(created["id"], share["id"], data)
        self.assertEqual("accepted", share["status"], data)
        self.assertFalse(share["expired"], data)
        self.assertEqual(LOCAL, share["accepted_by"],
                         "权限绑到了别人头上：必须绑当前会话的 actor")
        self.assertTrue(str(share["accepted_at"]), "accepted_at 没写：事件时间无法核对")
        self.assertEqual(GROUP, share["group_id"], data)
        self.assertEqual("view", share["permission"], data)

    def test_repeat_accept_by_the_same_actor_is_idempotent_success(self) -> None:
        """同一个人再点一次链接：幂等成功（``sharing.accept`` 既有语义逐字不变）。"""
        created = self.invite()
        first = self.post_accept(created["token"])
        self.assertEqual(200, first[0], first[2])
        accepted_at = first[2]["share"]["accepted_at"]
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(200, status, f"重复接受不该报错：{status} {data}")
        self.assertEqual("accepted", data["share"]["status"], data)
        self.assertEqual(LOCAL, data["share"]["accepted_by"], data)
        self.assertEqual(accepted_at, data["share"]["accepted_at"],
                         "幂等成功不该改写 accepted_at（服务层零写入）")

    def test_expired_link_answers_share_expired(self) -> None:
        """过期链接 → 409 ``SHARE_EXPIRED``（B5-1「过期自动失效」在端点上的表现）。"""
        created = self.invite(ttl_seconds=1)
        time.sleep(1.3)
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(409, status, f"过期链接还能接受：{status} {data}")
        self.assert_denied_code(status, data, "SHARE_EXPIRED")
        self.assertEqual(created["id"], data["detail"]["invite_id"], data)

    def test_revoked_link_answers_share_token_invalid(self) -> None:
        """已撤销 → 400 ``SHARE_TOKEN_INVALID``（既有口径：token 已死，少一个枚举面）。"""
        created = self.invite()
        self.service.share_service.revoke(str(created["id"]), actor=CAPTAIN,
                                          reason="R5-4b 验收")
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(400, status, f"撤销的 token 还能接受：{status} {data}")
        self.assert_denied_code(status, data, "SHARE_TOKEN_INVALID")
        self.assertEqual("revoked", data["detail"]["status"],
                         "撤销的拒绝要在 detail 里说清是 revoked")

    def test_unknown_and_missing_token_are_share_token_invalid(self) -> None:
        """未知 token / 缺 token / 空串：同一个 400 码，不透露「猜中了哪一条」。"""
        for given in ("shr_" + "f" * 48, "", None):
            status, _headers, data = self.post_accept(given)
            self.assertEqual(400, status, f"token={given!r} 的响应不对：{data}")
            self.assert_denied_code(status, data, "SHARE_TOKEN_INVALID")
        status, _headers, data = self.post_accept(None, body={})
        self.assertEqual(400, status, data)
        self.assert_denied_code(status, data, "SHARE_TOKEN_INVALID")

    def test_token_taken_by_another_actor_is_share_token_used(self) -> None:
        """一次性纪律：token 已被别人接受 → 409 ``SHARE_TOKEN_USED``。"""
        created = self.invite()
        self.service.share_service.accept(token=str(created["token"]),
                                          invitee_did="did:r54b-first")
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(409, status, f"一次性 token 被重复消费：{status} {data}")
        self.assert_denied_code(status, data, "SHARE_TOKEN_USED")
        self.assertEqual("did:r54b-first", data["detail"]["accepted_by"], data)

    def test_accept_appends_events_and_the_ledger_stays_append_only(self) -> None:
        """事件账本：成功接受留痕、链自证；被拒的那几次一条都不追加。

        一次成功接受在**既有实现**里留两条痕（本轮一字未动，如实钉下来）：
        服务层 ``sharing.accept`` 记 ``share.accept``（target=邀请 id），
        端点层 ``share_accept`` 记 ``accept``（target=任务组）——两条都是 append-only。
        幂等的重复接受只留端点层那一条（``sharing.accept`` 走幂等早退、零写入）。
        """
        self.assertTrue(self.service.share_service.verify_events()["ok"],
                        "开局链就是断的（不该由本轮背）")
        created = self.invite()
        before = self.events_count()
        status, _headers, _data = self.post_accept(created["token"])
        self.assertEqual(200, status)
        self.assertEqual(before + 2, self.events_count(),
                         "成功接受的留痕条数与既有实现不符（服务层 + 端点层各一条）")
        self.assertTrue(self.service.share_service.verify_events()["ok"],
                        "接受写坏了 append-only 链")
        tail = self.events()[-2:]
        self.assertEqual({"share.accept", "accept"}, {row["action"] for row in tail},
                         f"事件动作名不在既有族里：{[row['action'] for row in tail]}")
        for row in tail:
            self.assertEqual(LOCAL, row["actor_did"], f"事件记的不是接受的人：{row}")

        mid = self.events_count()
        self.assertEqual(200, self.post_accept(created["token"])[0])
        self.assertEqual(mid + 1, self.events_count(),
                         "幂等重复接受只该留端点层那一条（服务层零写入）")

        # 先把三种被拒的种子造完（造种子自己会留痕：share.invite / share.accept），
        # 再取快照，只数**被拒的那三发**多没多出一条。
        late = self.invite(ttl_seconds=1)                       # 过期
        burned = self.invite()                                  # 已被别人接受
        self.service.share_service.accept(token=str(burned["token"]),
                                          invitee_did="did:r54b-second")
        time.sleep(1.3)
        after = self.events_count()
        self.post_accept("shr_" + "0" * 48)                     # 无效 token
        self.post_accept(late["token"])                         # 过期
        self.post_accept(burned["token"])                       # 已被别人接受
        self.assertEqual(after, self.events_count(), "被拒的接受追加了事件（拒绝不该留痕）")
        self.assertTrue(self.service.share_service.verify_events()["ok"],
                        "append-only 链在拒绝路径上被写坏了")

    def test_response_never_echoes_the_token_material(self) -> None:
        """一次性纪律在端点上的表现：响应全文不含明文 token / salt / token_hash。"""
        created = self.invite()
        secret = str(created["token"])
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(200, status, data)
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(secret, text, "响应回显了明文 token")
        for needle in SECRET_NEEDLES:
            self.assertNotIn(needle, text, f"响应全文含秘密字面量 {needle!r}")

    def test_get_side_still_says_post_only(self) -> None:
        """GET 侧显式 405 逐字未动（本轮只挂 POST，不改别人的 405 口径）。"""
        status, _headers, data = call(self.base, accept_path())
        self.assertEqual(405, status, data)
        self.assert_denied_code(status, data, "METHOD_NOT_ALLOWED")
        self.assertEqual("accept is POST only", data["detail"]["hint"], data)
        self.assertEqual("GET", data["detail"]["method"], data)

    def test_the_mount_is_limited_to_this_one_route(self) -> None:
        """只挂 ``/api/shares/accept`` 这一格：同族其它 POST 仍落 ROUTE_NOT_FOUND。

        落地页那句「这个地址没挂服务」的文案**继续有效**——它现在指向的是这些
        真没挂的路由，不再指向 T24。
        """
        for path in (sv.API_PATH_SHARES, sv.API_PATH_SHARES_PERMISSIONS,
                     f"{sv.API_PATH_SHARES}/inv-r54b-nope/revoke-x"):
            status, _headers, data = call(self.base, path, {}, method="POST")
            self.assertEqual(404, status, f"{path} 顺手被挂上了：{status} {data}")
            self.assert_denied_code(status, data, "ROUTE_NOT_FOUND")
        self.assertNotEqual("ROUTE_NOT_FOUND", code_of(self.post_accept("shr_" + "9" * 48)[2]),
                            "T24 自己还在报 ROUTE_NOT_FOUND")

    def test_landing_page_request_shape_end_to_end(self) -> None:
        """落地页端到端（§2.2 最后一例）：按 ``share.html`` 源码的真实请求形态走通。

        口径：**从页面源码里读**出路径常量、body 键名与 CSRF 头名，逐字对齐服务端，
        再在 HTTP 上原样重放页面的两步（``GET /api/auth/me`` → ``POST accept``）；
        接受完用 ``GET /api/shares/permissions`` 这个端点看权限生效（``can()`` 的
        语义由既有测试钉，本轮只验端点）。
        """
        for needle in (f"var ACCEPT_PATH = '{accept_path()}';",
                       "var CSRF_HEADER = 'X-CSRF-Token';",
                       "request(ACCEPT_PATH, 'POST', { token: token }, csrf)"):
            self.assertIn(needle, SHARE_HTML, f"页面请求形态与挂载口径不一致：{needle!r}")
        me_status, _me_headers, _me = call(self.base, sv.API_PATH_AUTH_ME)
        self.assertEqual(200, me_status, "local 免登录下 /api/auth/me 应可用（页面第一步）")
        created = self.invite(permission="approve")
        status, _headers, data = call(self.base, accept_path(),
                                      {"token": created["token"]}, method="POST",
                                      headers={"Accept": "application/json",
                                               "Content-Type": "application/json"})
        self.assertEqual(200, status, f"页面同形状的请求没走通：{status} {data}")
        self.assertIn("if (result.ok) return { key: 'state.accepted', tone: 'ok' };",
                      SHARE_HTML, "页面成功态的映射被改动过（前端零改动铁律）")
        perms = call(self.base, f"{sv.API_PATH_SHARES_PERMISSIONS}?group_id={GROUP}")
        self.assertEqual(200, perms[0], perms[2])
        self.assertTrue(perms[2]["can_view"], f"接受后 view 没生效：{perms[2]}")
        self.assertTrue(perms[2]["can_approve"], f"接受后 approve 没生效：{perms[2]}")
        self.assertFalse(perms[2]["can_manage"], f"越级生效：{perms[2]}")

    def test_no_new_error_codes_and_contract_reports_the_route_mounted(self) -> None:
        """契约口径：路由出现在契约里、常量从「声明未挂载」清单消失、码名零新增。"""
        contract = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
        rows = [row for row in contract["routes"]
                if row["path"] == accept_path() and row["verb"] == "POST"]
        self.assertEqual(1, len(rows), f"契约里 POST {accept_path()} 应为恰好一条：{rows}")
        row = rows[0]
        self.assertEqual("exact", row["match"], row)
        self.assertEqual("permission", row["auth"], row)
        self.assertEqual(["records.read"], row["abilities"],
                         "权限位与 docs/07 §5 的 T24 冻结口径漂移")
        self.assertTrue(row["csrf"], "CSRF 口径与 T23 同款：cookie 通道要双提交头")
        self.assertIn(accept_path(), contract["method_not_allowed"]["GET"],
                      "GET 侧的显式 405 必须还在契约里")
        unmounted = contract["declared_vs_mounted"]["unmounted"]
        self.assertNotIn("API_PATH_SHARES_ACCEPT=/api/shares/accept", unmounted,
                         "契约仍把 T24 记成「声明未挂载」：--write 没跑，或挂载没生效")

    def test_non_object_body_is_refused_by_the_existing_shape_rule(self) -> None:
        """形状守门沿用服务层既有口径：非对象 body 不静默接受。"""
        for bad in ([{"token": "shr_" + "1" * 48}], "not-an-object", 7):
            status, _headers, data = self.post_accept(None, body=bad)
            self.assertNotEqual(200, status, f"非对象 body 被当成接受：{status} {data}")
            self.assertNotIn(200, (status,), f"{bad!r} 居然成功了")
            self.assertLess(status, 500, f"裸 500：形状错误没翻成统一错误体 {status} {data}")


class TeamAcceptHttpTests(AcceptMountCase):
    """team 模式：§2.1 的取舍钉死——身份是 ``sharing.accept`` 的必需项，所以要会话。"""

    mode = "team"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.uids: dict[str, str] = {}
        for name, role in (("r54bview", "viewer"), ("r54bedit", "editor")):
            user = cls.service.auth.create_user(name, role, PW,
                                                actor={"user_id": "admin", "role": "admin"})
            cls.uids[name] = str(user["id"])

    def login(self, username: str, password: str | None = None) -> dict:
        status, _headers, data = call(self.base, sv.API_PATH_AUTH_LOGIN,
                                      {"username": username,
                                       "password": password or PW}, method="POST")
        self.assertEqual(200, status, data)
        return data if isinstance(data, Mapping) else {}

    def test_team_accept_requires_a_session(self) -> None:
        """无会话（匿名）→ 401 ``AUTH_REQUIRED``：不为「匿名接受」编造身份。"""
        created = self.invite(invitee=self.uids["r54bview"])
        status, _headers, data = self.post_accept(created["token"])
        self.assertEqual(401, status, f"匿名接受被放行了：{status} {data}")
        self.assert_denied_code(status, data, "AUTH_REQUIRED")
        listing = self.service.share_service.list_for(self.uids["r54bview"])
        states = {str(item["id"]): str(item["status"])
                  for item in listing["shared_with_me"]}
        self.assertEqual("pending", states.get(str(created["id"])),
                         "匿名那次把邀请烧掉了：状态必须还是 pending")

    def test_team_accept_binds_the_logged_in_identity(self) -> None:
        """登录后的受邀者接受 → 200，``accepted_by`` = 这个会话的 ``user_id``。"""
        uid = self.uids["r54bview"]
        created = self.invite(invitee=uid)
        token = str(self.login("r54bview").get("token") or "")
        self.assertTrue(token, "登录没拿到 token")
        status, _headers, data = self.post_accept(created["token"], headers=bearer(token))
        self.assertEqual(200, status, f"受邀者登录接受被拒：{status} {data}")
        self.assertEqual(uid, data["share"]["accepted_by"], data)
        self.assertEqual("accepted", data["share"]["status"], data)

    def test_directed_invite_rejects_another_signed_in_identity(self) -> None:
        """定向邀请：登录的是别人 → 403 ``SHARE_FORBIDDEN``（既有语义，逐字不变）。"""
        target = self.uids["r54bview"]
        created = self.invite(invitee=target)
        other = str(self.login("r54bedit").get("token") or "")
        status, _headers, data = self.post_accept(created["token"], headers=bearer(other))
        self.assertEqual(403, status, f"别人顶名接受成功了：{status} {data}")
        self.assert_denied_code(status, data, "SHARE_FORBIDDEN")
        self.assertEqual(target, data["detail"]["invitee_did"], data)

    def test_cookie_channel_needs_the_csrf_pair(self) -> None:
        """CSRF 与 T23 同款：Cookie 通道缺双提交头 → 403 ``CSRF_FAILED``，补上即 200。

        诚实登记（报告的遗留项）：``share.html`` 的 CSRF 取自 ``GET /api/auth/me``
        （源码 ``me.data.csrf_token``），而 ``/api/auth/me`` 的响应里**没有**这个键
        （``auth.me_payload`` 的 A3 冻结形状），CSRF 令牌只在**登录响应**里出现
        （``auth.py`` 的 ``csrf_token``）。于是 team 模式的 Cookie 路径上，落地页拿不到
        配对头、会如实撞在 403 上；前端零改动铁律下本轮不修，只把这个事实钉成测试。
        """
        login = self.login("r54bview")
        cookie_value = str(login.get("cookie", {}).get("value") or "")
        self.assertTrue(cookie_value, f"登录响应里没有会话 Cookie：{list(login)}")
        csrf = str(login.get("csrf_token") or "")
        self.assertTrue(csrf, f"登录响应里没有 csrf_token：{list(login)}")
        uid = self.uids["r54bview"]
        cookie = {"Cookie": f"{auth_mod.SESSION_COOKIE}={cookie_value}"}

        created = self.invite(invitee=uid)
        status, _headers, data = self.post_accept(created["token"], headers=cookie)
        self.assertEqual(403, status, f"Cookie 通道的 CSRF 守门没生效：{status} {data}")
        self.assert_denied_code(status, data, "CSRF_FAILED")

        created = self.invite(invitee=uid)
        status, _headers, data = self.post_accept(
            created["token"], headers={**cookie, auth_mod.CSRF_HEADER: csrf})
        self.assertEqual(200, status, f"带上配对头就该接受成功：{status} {data}")
        self.assertEqual(uid, data["share"]["accepted_by"], data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
