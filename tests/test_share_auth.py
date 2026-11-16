"""R5-3 验收测试：REQ-005 B5-2 服务端鉴权 + T21/T23 挂载 + T7 代行接线。

任务书：``_pm-dispatch/TASK-R5-3-auth-wiring.md``（L1 四件套已批）。这个文件钉住四件事：

1. **B5-2 拦截矩阵**（§2.1）：``/intervene``（含 D-resign 写回路径）与 ``POST /api/rollback``
   消费 ``sharing`` 的权限级；``view`` 命中干预/回滚类端点一律 403（复用既有码
   ``SHARE_FORBIDDEN``，零新错误码、零新权限级）；``approve`` 只可审批（改参数=manage），
   ``manage`` 全量。矩阵语义取 REQ-005 核心逻辑 1 与 ``sharing.PERMISSIONS`` 三级。
2. **铁律 2「无邀请者不受影响」**（PM-PLAN §7.5 最高优先级）：local 免登录与队长本人路径
   **零行为变化**。闸门的接入口径是"这个身份在这个组**接受过**邀请"（``can()`` 的
   ``reason != no_grant``）；组里零邀请时**连** ``can()`` **都不调用**。所以"零回归"
   不是靠测得多，而是结构上这条路径根本不进新代码。
3. **拒绝零账本写入**（§2.1 尾句）：403 的那一次，记录零改写、决定段零变化、
   协作事件账本零追加、撤销日志零行、D-resign 签名计数零增长（只有审计链按既有
   ``ROLE_FORBIDDEN`` 口径多一条 ``auth.denied`` 取证）。
4. **T21 / T23 / T7**（§2.2-§2.4）：``POST /api/groups/<id>/share`` 挂上并回一次性
   ``invite_url``；``share_revoke`` 服务方法补齐（鉴权＝inviter / 队长 / 组内 manage）；
   认定书消费协作事件（代行句在 HTTP 认定书里出现/消失）+ 事件窗口未取全时如实登记。

传输纪律（铁律 3）：所有 HTTP 走 ``tests/http_fixture.py`` —— 端口就绪 + **连接级**瞬态
一次性重连；GET/HEAD 等幂等动词可重放，**POST/PUT/PATCH/DELETE 绝不重放**，
断言级失败绝不重试。临时目录一律 ``tests/scratch.py``。
"""

from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Mapping
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试

from agent_explainer import auth as auth_mod  # noqa: E402  身份口径（LOCAL_USER_ID）
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import sharing as sh  # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]

GROUP = "grp-r53-captain"
GROUP_PLAIN = "grp-r53-plain"          # 一条邀请都没有的组（零回归对照组）
GROUP_LOCAL = "grp-r54-local"          # 免登录单机自己创建的组（created_by="local"）
GROUP_ADMIN = "grp-r54-admin"          # admin 用户自己创建的组（R5-4 收紧的"组长本人"）
REC_PENDING = "act-r53-pending"        # 可干预（approve/reject/modify）
REC_ROLL = "act-r53-roll"              # 可回滚（notes.write 已 succeeded）
REC_NOGROUP = "act-r53-nogroup"        # 不属于任何任务组
OWNER = "did:r53-owner"                # 记录的责任根（human_owner）
CAPTAIN = "did:r53-captain"            # 队长本人（组的 created_by）
MODIFY_FIELDS = {"max_delete_count": 3, "move_to_trash": True}


def native(action_id: str) -> dict:
    return copy.deepcopy(next(row for row in NATIVE if row["id"] == action_id))


def seed_rows() -> list[dict]:
    """三条种子：可干预 / 可回滚 / 无组归属，前两条挂在同一个任务组下。"""
    pending = native("act-demo-0005")
    pending["id"] = REC_PENDING
    pending["group_id"] = GROUP
    pending["human_owner"] = OWNER
    roll = native("act-demo-0007")
    roll["id"] = REC_ROLL
    roll["status"] = "succeeded"
    roll["timestamp"] = "2026-11-01T10:00:00Z"
    roll["parent_action_id"] = None
    roll["user_intervention"] = None
    roll["group_id"] = GROUP
    roll["human_owner"] = OWNER
    free = native("act-demo-0008")
    free["id"] = REC_NOGROUP
    free["human_owner"] = OWNER
    return [pending, roll, free]


def call(base: str, path: str, body: object | None = None, method: str = "GET",
         headers: Mapping[str, str] | None = None) -> tuple[int, dict, object]:
    """返回 ``(status, headers, json body)``；4xx/5xx 也把响应体带回来（要看错误码）。

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
        except urllib.error.HTTPError as exc:      # 4xx/5xx 也要把响应体带回来：要看错误码
            text = exc.read().decode("utf-8", "replace")
            status = exc.code
            head = {k.lower(): v for k, v in exc.headers.items()}
            exc.close()                            # 显式关闭：不给测试输出添 ResourceWarning 噪声
        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return status, head, payload

    return fx.urlopen_resilient(once, verb=method, path=path, timeout=20.0)


def bearer(token: str) -> dict[str, str]:
    """token 通道（专用头 ``X-TraceLens-Token``）：CSRF 双提交对它豁免，鉴权形状同 cookie。"""
    return {auth_mod.TOKEN_HEADER: token}


def code_of(payload: object) -> str:
    return str(payload.get("code")) if isinstance(payload, Mapping) else ""


class ShareAuthCase(unittest.TestCase):
    """公共夹具：临时数据根 + 服务器 + 任务组 + 邀请，逐类自建自拆。"""

    mode = "local"
    seed_invites = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("share-auth")
        cls.features = cls.root / "features"
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=seed_rows(), web_dir=WEB, allow_reset=True,
            quiet=True, features_root=cls.features,
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
        # 任务组：一条挂在 GROUP 下（有邀请），一条挂 GROUP_PLAIN（零邀请对照组）
        cls.service.group_service.create(title="R5-3 协作组", agent_ids=["agent-r53"],
                                         actor=CAPTAIN, group_id=GROUP)
        cls.service.group_service.create(title="R5-3 无邀请组", agent_ids=["agent-r53"],
                                         actor=CAPTAIN, group_id=GROUP_PLAIN)
        # R5-4（PM-VERDICT-R5-3 §3.1 收紧 T21）：发起分享只认"组长本人 / 组内 manage"。
        # "组长本人"是**建组那一刻的事实**（groups.py 的 created_by），不是权限表里的角色：
        # 免登录单机建的组 created_by="local"（不传 actor 的出厂写法），local 会话的
        # actor 恰好也是 "local"（``_actor_id_of`` 实测口径），两者事实相等 = 组长。
        cls.service.group_service.create(title="R5-4 免登录自建房", agent_ids=["agent-r54"],
                                         group_id=GROUP_LOCAL)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.root, ignore_errors=True)

    # -- 邀请助手（classmethod：类级夹具与用例里都能调）------------------
    @classmethod
    def invite(cls, *, permission: str, group_id: str = GROUP,
               inviter: str = CAPTAIN, invitee: str = "",
               ttl_seconds: int | None = None) -> dict:
        kwargs: dict[str, object] = {"group_id": group_id, "inviter_did": inviter,
                                     "permission": permission, "invitee_did": invitee}
        if ttl_seconds is not None:
            kwargs["ttl_seconds"] = ttl_seconds
        return cls.service.share_service.invite(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def grant(cls, *, permission: str, actor: str, group_id: str = GROUP) -> dict:
        """发一份邀请并**当场接受**（= 这个 actor 成了带邀请的协作者）。"""
        created = cls.invite(permission=permission, group_id=group_id, invitee=actor)
        return cls.service.share_service.accept(token=created["token"], invitee_did=actor)

    # -- 种子助手 ---------------------------------------------------------
    def fresh(self, kind: str, *, group_id: str = GROUP) -> str:
        """现场造一条可干预（``pending``）/ 可回滚（``roll``）记录，id 唯一。

        用例之间互不串味：不靠"上一条测试留下的记录状态"活着，也就不会出现
        因执行顺序而 409 的假阳性。
        """
        rows = seed_rows()
        row = rows[0] if kind == "pending" else rows[1]
        self._seq = int(getattr(self, "_seq", 0)) + 1
        row["id"] = f"act-r53-{kind}-{self._testMethodName}-{self._seq}"
        if group_id:
            row["group_id"] = group_id
        else:
            row.pop("group_id", None)      # 不属于任何任务组：字段整个不存在（5.0 可选）
        row["human_owner"] = OWNER
        self.service.store.put(row)
        return str(row["id"])

    # -- 取证助手 ---------------------------------------------------------
    def events_count(self) -> int:
        return int(((self.service.share_service.facts() or {}).get("events") or {})
                   .get("items") or 0)

    def record_of(self, action_id: str) -> dict:
        return self.service.store.get(action_id) or {}

    def audit_count(self) -> int:
        return len(self.service.audit.entries())

    def assert_zero_write_denial(self, before: dict, action_id: str,
                                 *, signed: bool = True) -> None:
        """拒绝的那一次：业务账本一个字节都不许多（记录/事件/签名/撤销计数）。"""
        self.assertEqual(before["records"], len(self.service.store.all()),
                         "403 之后记录条数变了（写路径不该被碰到）")
        self.assertEqual(before["status"], self.record_of(action_id).get("status"),
                         "403 之后记录状态被改写")
        self.assertEqual(before["events"], self.events_count(),
                         "403 之后协作事件账本多了一条（拒绝不该留痕）")
        self.assertEqual(before["signed"], self.service.signed_on_write,
                         "403 之后 D-resign 签名计数涨了（写回路径被触发）")
        self.assertEqual(before["rollbacks"], self.service.rollback_executions,
                         "403 之后撤销执行计数涨了（回滚写路径被触发）")


class GateMatrixTests(ShareAuthCase):
    """§2.1 拦截矩阵：view / approve / manage 三级 × 干预决定 / 回滚 / 再分享。

    直接调服务层判定（``collab_verdict`` / ``collab_guard``），把矩阵逐格钉死；
    同一格在真 HTTP 上的表现由 :class:`TeamShareGateHttpTests` 再钉一遍。
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.view_actor = "did:r53-view"
        cls.approve_actor = "did:r53-approve"
        cls.manage_actor = "did:r53-manage"
        cls.grant(permission="view", actor=cls.view_actor)
        cls.grant(permission="approve", actor=cls.approve_actor)
        cls.grant(permission="manage", actor=cls.manage_actor)

    def verdict(self, actor: str, required: str, group_id: str = GROUP) -> dict:
        return self.service.collab_verdict(group_id=group_id, required=required,
                                           operator_ids=[actor], endpoint="unit")

    def test_view_holder_is_blocked_on_every_write_class(self) -> None:
        """view = 只读：审批 / 改参数 / 回滚 / 再分享四类写动作全部拒（REQ-005 功能五）。"""
        for required in ("approve", "manage"):
            facts = self.verdict(self.view_actor, required)
            self.assertTrue(facts["engaged"], f"view 协作者应当被判定接进闸门：{facts}")
            self.assertFalse(facts["allowed"], f"view 不该过 {required} 这一关：{facts}")
            self.assertEqual(facts["reason"], "level_too_low", facts)
            self.assertEqual(facts["granted"], "view", facts)

    def test_approve_level_allows_decide_but_not_modify(self) -> None:
        """approve 级 = 可审批待确认步骤；``modify`` 是改参数 → manage 级才够。"""
        self.assertTrue(self.verdict(self.approve_actor, "approve")["allowed"])
        self.assertFalse(self.verdict(self.approve_actor, "manage")["allowed"])
        self.assertEqual(self.verdict(self.approve_actor, "manage")["reason"],
                         "level_too_low")

    def test_manage_level_covers_every_class(self) -> None:
        """manage = 全量（审批 + 改参数 + 回滚 + 再分享）：等级级联不发明新级。"""
        for required in sh.PERMISSIONS:
            self.assertTrue(self.verdict(self.manage_actor, required)["allowed"],
                            f"manage 应覆盖 {required}")

    def test_gate_matrix_matches_decision_map(self) -> None:
        """矩阵本体（常量表）：approve/reject → approve 级，modify → manage 级。"""
        self.assertEqual(sv.COLLAB_PERMISSION_FOR_DECISION,
                         {"approve": "approve", "reject": "approve", "modify": "manage"})
        self.assertEqual(sv.COLLAB_PERMISSION_FOR_ROLLBACK, "manage")
        self.assertEqual(sv.COLLAB_PERMISSION_FOR_SHARE, "manage")
        self.assertEqual(sv.COLLAB_DENIAL_CODE, "SHARE_FORBIDDEN")

    def test_group_without_any_invite_is_not_judged_at_all(self) -> None:
        """铁律 2 的结构级证据：这组零邀请 → ``engaged=False``，``can()`` 一次都不调。"""
        with mock.patch.object(self.service.share_service, "can",
                               side_effect=AssertionError("零邀请组不该调 can()")) as spy:
            for actor in (CAPTAIN, self.view_actor, sv.LOCAL_ACTOR_ID):
                facts = self.verdict(actor, "manage", group_id=GROUP_PLAIN)
                self.assertFalse(facts["engaged"], facts)
                self.assertTrue(facts["allowed"], facts)
                self.assertEqual(facts["reason"], "no_invites", facts)
            spy.assert_not_called()

    def test_captain_and_local_actor_pass_without_level_comparison(self) -> None:
        """队长本人与 local 免登录：``no_grant`` → 原样放行，不进等级比较。"""
        for actor in (CAPTAIN, sv.LOCAL_ACTOR_ID, auth_mod.LOCAL_USER_ID):
            facts = self.verdict(actor, "manage")
            self.assertFalse(facts["engaged"], f"{actor} 不该被当成协作者判定")
            self.assertTrue(facts["allowed"], f"{actor} 被挡在门外 = 打爆队长/local 路径")
            self.assertEqual(facts["reason"], "no_inviter_binding", facts)

    def test_record_without_group_is_not_judged(self) -> None:
        """记录不属于任何任务组 → 协作层无从判定（不发明 403，交给既有口径）。"""
        facts = self.service.collab_verdict(group_id="", required="manage",
                                            operator_ids=[self.view_actor])
        self.assertFalse(facts["engaged"], facts)
        self.assertTrue(facts["allowed"], facts)
        self.assertEqual(facts["reason"], "no_group", facts)

    def test_revoked_grant_stays_blocked(self) -> None:
        """撤销必须真收权：邀请撤销后，同一个人在这个组的写动作全拒。"""
        actor = "did:r53-revoked"
        created = self.grant(permission="manage", actor=actor)
        self.assertTrue(self.verdict(actor, "manage")["allowed"])
        self.service.share_service.revoke(str(created["id"]), actor=CAPTAIN)
        facts = self.verdict(actor, "approve")
        self.assertTrue(facts["engaged"], "撤销过的人仍是带邀请协作者，闸门必须继续生效")
        self.assertFalse(facts["allowed"], "撤销后越权放行 = 撤销形同虚设")
        self.assertEqual(facts["reason"], "grant_revoked", facts)

    def test_expired_grant_stays_blocked(self) -> None:
        """链接有效期 = 访问有效期（B5-1）：过期后不越权，也不打回"无邀请者"。"""
        actor = "did:r53-expired"
        created = self.invite(permission="manage", invitee=actor, ttl_seconds=1)
        self.service.share_service.accept(token=created["token"], invitee_did=actor)
        time.sleep(1.3)
        facts = self.verdict(actor, "approve")
        self.assertTrue(facts["engaged"], facts)
        self.assertFalse(facts["allowed"], "邀请过期后越权放行 = 过期形同虚设")
        self.assertEqual(facts["reason"], "grant_expired", facts)

    def test_identity_split_cannot_be_used_to_escape_the_gate(self) -> None:
        """身份集：授权绑在 ``user_id`` 上时，用 ``username`` 口径也挡得住（不能逃逸）。"""
        user_id, username = "u-r53-split", "split"
        self.grant(permission="view", actor=user_id)
        facts = self.service.collab_verdict(group_id=GROUP, required="approve",
                                            operator_ids=[username, user_id],
                                            endpoint="POST /intervene")
        self.assertTrue(facts["engaged"],
                        "只查 username 就当无邀请者放行 = 两个 id 口径互相绕闸门")
        self.assertFalse(facts["allowed"], facts)

    def test_guard_raises_existing_code_with_human_reason(self) -> None:
        """拒绝形状：既有码 ``SHARE_FORBIDDEN``（403）+ 人话 message + 机器 detail。"""
        with self.assertRaises(sv.InterveneError) as caught:
            self.service.collab_guard(group_id=GROUP, required="manage",
                                      endpoint="POST /api/rollback",
                                      action_id=REC_ROLL,
                                      operator_ids=[self.view_actor],
                                      actor_id=self.view_actor, request_id="req-r53")
        err = caught.exception
        self.assertEqual(err.code, "SHARE_FORBIDDEN")
        self.assertEqual(err.http_status, 403)
        self.assertEqual(err.detail["granted"], "view")
        self.assertEqual(err.detail["required"], "manage")
        self.assertEqual(err.detail["endpoint"], "POST /api/rollback")
        self.assertIn("view", str(err.message))

    def test_denial_writes_nothing_but_one_audit_entry(self) -> None:
        """拒绝只多一条审计取证（既有 ROLE_FORBIDDEN 同口径），业务账本零写入。"""
        before = {"records": len(self.service.store.all()),
                  "status": self.record_of(REC_ROLL).get("status"),
                  "events": self.events_count(),
                  "signed": self.service.signed_on_write,
                  "rollbacks": self.service.rollback_executions}
        audit_before = self.audit_count()
        with self.assertRaises(sv.InterveneError):
            self.service.collab_guard(group_id=GROUP, required="manage",
                                      endpoint="POST /api/rollback", action_id=REC_ROLL,
                                      operator_ids=[self.view_actor],
                                      actor_id=self.view_actor, request_id="req-r53")
        self.assert_zero_write_denial(before, REC_ROLL)
        tail = self.service.audit.entries()[audit_before:]
        self.assertEqual(len(tail), 1, "审计取证应当恰好一条")
        self.assertEqual(tail[0]["event"], "auth.denied", tail[0])

    def test_operator_ids_collects_both_id_shapes(self) -> None:
        """身份集本体：team = username + user_id；local = local-user + local。"""
        team = self.service.operator_ids({"username": "watch", "user_id": "u-1",
                                          "role": "editor"}, actor_id="watch")
        self.assertEqual(team, ["watch", "u-1"], team)
        local = self.service.operator_ids(self.service.auth.local_session(),
                                          actor_id=sv.LOCAL_ACTOR_ID)
        self.assertEqual(local, [sv.LOCAL_ACTOR_ID, auth_mod.LOCAL_USER_ID], local)
        self.assertEqual(self.service.operator_ids(None, actor_id=""),
                         [sv.LOCAL_ACTOR_ID])


class TeamShareGateHttpTests(ShareAuthCase):
    """§2.1 在真 HTTP 上的样子：role 够（editor 有 ``intervene.decide``）但分享等级不够。

    这一类专门把"角色允许、协作权限禁止"这一格钉住 —— 用 editor 角色登录，
    ``ROLE_FORBIDDEN`` 不会挡路，能看见的拒绝只可能来自 B5-2 闸门。
    """

    mode = "team"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = str(cls.service.auth.list_users()[0]["id"])
        cls.users: dict[str, str] = {}
        for name, permission in (("collabview", "view"), ("collabapprove", "approve"),
                                 ("collabmanage", "manage")):
            user = cls.service.auth.create_user(name, "editor", "R53passw0rd!longenough",
                                                actor={"user_id": "admin", "role": "admin"})
            uid = str(user["id"])
            # 当场接受邀请 = 这个登录人成了"带邀请协作者"（授权绑在 user_id 口径上）
            created = cls.service.share_service.invite(group_id=GROUP, inviter_did=CAPTAIN,
                                                       permission=permission, invitee_did=uid)
            cls.service.share_service.accept(token=created["token"], invitee_did=uid)
            cls.users[name] = uid

    def login(self, username: str, password: str = "R53passw0rd!longenough") -> dict:
        status, _headers, data = call(self.base, sv.API_PATH_AUTH_LOGIN,
                                      {"username": username, "password": password},
                                      method="POST")
        self.assertEqual(status, 200, data)
        body = data if isinstance(data, Mapping) else {}
        # 走 token 通道（Authorization: Bearer）：CSRF 双提交只对 cookie 通道生效，
        # 用它可以把"角色够、协作等级不够"这一格单独钉干净。
        return {"token": str(body.get("token") or ""),
                "csrf": str(body.get("csrf_token") or "")}

    def session_of(self, name: str) -> dict:
        return self.login(name)

    def admin_token(self) -> str:
        return self.login("admin", self.admin_pw)["token"]

    def snapshot(self, action_id: str) -> dict:
        return {"records": len(self.service.store.all()),
                "status": self.record_of(action_id).get("status"),
                "events": self.events_count(),
                "signed": self.service.signed_on_write,
                "rollbacks": self.service.rollback_executions}

    # -- 用例 --
    def test_view_grant_is_403_on_intervene_and_writes_nothing(self) -> None:
        """核心一格：editor 角色 + view 邀请 → ``POST /intervene`` 403，且零账本写入。"""
        session = self.session_of("collabview")
        target = self.fresh("pending")
        before = self.snapshot(target)
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "approve"},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 403, f"view 协作者拍板成功了：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(self.record_of(target).get("status"), "pending_confirmation")
        self.assert_zero_write_denial(before, target)
        self.assertIsNone(self.record_of(target).get("user_intervention"),
                          "拒绝的那一次写了决定段（D 段写入 = 越权落地）")

    def test_view_grant_is_403_on_rollback_and_writes_nothing(self) -> None:
        """view 命中回滚端点：403，原记录与撤销日志一条都不许多。"""
        session = self.session_of("collabview")
        target = self.fresh("roll")
        before = self.snapshot(target)
        status, _headers, data = call(self.base, sv.API_PATH_ROLLBACK,
                                      {"action_id": target, "confirm_all": True},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assert_zero_write_denial(before, target)

    def test_approve_grant_can_decide_but_not_modify(self) -> None:
        """approve 级：审批 200（决定真落盘）；``modify``（改参数）403。"""
        session = self.session_of("collabapprove")
        target = self.fresh("pending")
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "reject",
                                       "reason": "R5-3 approve 级审批"},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 200, f"approve 级审批被挡：{status} {data}")
        self.assertEqual(self.record_of(target).get("status"), "rejected")

        other = self.fresh("pending")
        before = self.snapshot(other)
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": other, "decision": "modify",
                                       "modified_params": MODIFY_FIELDS,
                                       "reason": "改参数要 manage"},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 403, f"approve 级改参数成功了：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assert_zero_write_denial(before, other)

    def test_approve_grant_is_403_on_rollback(self) -> None:
        """approve 级不够回滚（REQ-005 功能五把回滚划给 manage）。"""
        session = self.session_of("collabapprove")
        target = self.fresh("roll")
        before = self.snapshot(target)
        status, _headers, data = call(self.base, sv.API_PATH_ROLLBACK,
                                      {"action_id": target, "confirm_all": True},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 403, data)
        self.assert_zero_write_denial(before, target)

    def test_manage_grant_can_modify_and_rollback(self) -> None:
        """manage 全量：改参数 200（真改）、回滚 200（真撤销；原记录状态零改写）。"""
        session = self.session_of("collabmanage")
        target = self.fresh("pending")
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "modify",
                                       "modified_params": MODIFY_FIELDS,
                                       "reason": "manage 级改参数"},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 200, f"manage 级改参数被挡：{status} {data}")
        ui = (self.record_of(target).get("user_intervention") or {})
        self.assertEqual(ui.get("modified_params"), MODIFY_FIELDS)

        roll = self.fresh("roll")
        before = self.snapshot(roll)
        status, _headers, data = call(self.base, sv.API_PATH_ROLLBACK,
                                      {"action_id": roll, "confirm_all": True},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 200, f"manage 级回滚被挡：{status} {data}")
        self.assertGreater(self.service.rollback_executions, before["rollbacks"])
        self.assertEqual(self.record_of(roll).get("status"), "succeeded",
                         "口径 α：回滚零改写原记录")

    def test_captain_path_is_unaffected_by_the_gate(self) -> None:
        """队长本人（admin：inviter、组内无邀请授权）在同一条写路径上照旧放行。"""
        token = self.admin_token()
        target = self.fresh("pending")
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "approve",
                                       "reason": "队长本人路径"},
                                      method="POST", headers=bearer(token))
        self.assertEqual(status, 200, f"队长本人被闸门挡在门外：{status} {data}")
        self.assertEqual(self.record_of(target).get("status"), "approved")

        roll = self.fresh("roll")
        status, _headers, data = call(self.base, sv.API_PATH_ROLLBACK,
                                      {"action_id": roll, "confirm_all": True},
                                      method="POST", headers=bearer(token))
        self.assertEqual(status, 200, f"队长本人回滚被闸门挡在门外：{status} {data}")

    def test_records_without_group_are_unaffected(self) -> None:
        """不属于任务组的记录：闸门不判定，协作者也照既有角色口径走。"""
        session = self.session_of("collabview")
        target = self.fresh("pending", group_id="")
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "approve",
                                       "reason": "无组归属"},
                                      method="POST", headers=bearer(session["token"]))
        self.assertEqual(status, 200, f"无组记录被协作闸门挡了：{status} {data}")
        self.assertEqual(self.record_of(target).get("status"), "approved")


class ShareEndpointsHttpTests(ShareAuthCase):
    """§2.2 T21 挂载 + §2.3 T23 撤销：端到端（发邀请 → 一次性链接 → 按权限行动）。

    鉴权口径与 §2.1 完全同源：T21 的写路径闸门 = 组内 ``manage`` 才可再分享；
    T23 的撤销权 = 发起人 / 队长（身份事实相等）/ 组内 ``manage``，由 ``sharing.revoke``
    单点判定。

    R5-4 改口径（PM-VERDICT-R5-3 §3.1 批准的 L1 事项）：R5-3 这一轮如实登记的
    "既无邀请又不在组里的局外人，在 T21 上仍按既有 ``data.write`` 规则放行"
    **已被收紧关掉** —— 现在只有"组长本人（``created_by`` 事实相等）"与
    "组内 ``manage`` 协作者"两种身份能发邀请，其余（含 admin/editor 这类
    有 ``data.write`` 的无关用户）403。收紧矩阵见
    :class:`ShareInitiateTightenHttpTests`；本类里凡是要发邀请的地方，
    都改走"组长自己的组"（``GROUP_ADMIN``），不再借 admin 的 ``data.write`` 过关。
    """

    mode = "team"
    PW = "R53passw0rd!longenough"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = str(cls.service.auth.list_users()[0]["id"])
        # R5-4 收紧后，"组长本人"= 这个组的 created_by 与操作者身份事实相等。
        # admin 对 GROUP 的 created_by（CAPTAIN）什么也不是 —— 他正好是
        # "有 data.write 的无关用户"，留下来钉 403 那一格；
        # 要钉"组长本人 201"就用 admin 自己建的这个组。
        cls.service.group_service.create(title="R5-4 组长自建房", agent_ids=["agent-r54"],
                                         actor=cls.admin, group_id=GROUP_ADMIN)
        cls.uids: dict[str, str] = {}
        cls.invites: dict[str, str] = {}
        for name, permission in (("viewshare", "view"), ("mgrshare", "manage"),
                                 ("stranger", "")):
            user = cls.service.auth.create_user(name, "editor", cls.PW,
                                                actor={"user_id": "admin", "role": "admin"})
            uid = str(user["id"])
            cls.uids[name] = uid
            if permission:
                created = cls.service.share_service.invite(group_id=GROUP,
                                                           inviter_did=CAPTAIN,
                                                           permission=permission,
                                                           invitee_did=uid)
                cls.service.share_service.accept(token=created["token"], invitee_did=uid)
                cls.invites[name] = str(created["id"])

    def login(self, username: str, password: str | None = None) -> str:
        status, _headers, data = call(self.base, sv.API_PATH_AUTH_LOGIN,
                                      {"username": username,
                                       "password": password or self.PW}, method="POST")
        self.assertEqual(status, 200, data)
        body = data if isinstance(data, Mapping) else {}
        return str(body.get("token") or "")

    def admin_token(self) -> str:
        return self.login("admin", self.admin_pw)

    def share_path(self, group_id: str = GROUP) -> str:
        return f"{sv.API_PATH_GROUPS}/{group_id}/share"

    def revoke_path(self, invite_id: str) -> str:
        return f"{sv.API_PATH_SHARES}/{invite_id}/revoke"

    # -- T21 --
    def test_share_route_mounted_returns_one_time_invite_url(self) -> None:
        """T21：``POST /api/groups/<id>/share`` 有分支接住，201 且响应含一次性 ``invite_url``。

        R5-4 收紧后发邀请的人 = 组长本人（``GROUP_ADMIN`` 的 ``created_by`` 就是 admin）。
        同一发请求打在 admin **不是**组长的 ``GROUP`` 上必须 403：这条一次性链接的
        铸造权只属于组长与组内 manage。
        """
        token = self.admin_token()
        status, _headers, data = call(self.base, self.share_path(GROUP_ADMIN),
                                      {"permission": "view"},
                                      method="POST", headers=bearer(token))
        self.assertEqual(status, 201, f"T21 没挂上或被拒：{status} {data}")
        self.assertEqual(set(data), {"ok", "share", "token", "invite_url", "note"}, data)
        self.assertTrue(str(data["invite_url"]).endswith(
            f"#{sv.SHARE_TOKEN_FRAGMENT}={data['token']}"), data)
        self.assertEqual(data["share"]["group_id"], GROUP_ADMIN, data)
        self.assertEqual(data["share"]["permission"], "view", data)

        status, _headers, data = call(self.base, self.share_path(GROUP),
                                      {"permission": "view"},
                                      method="POST", headers=bearer(token))
        self.assertEqual(status, 403, f"无关用户还能对别人的组发邀请：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)

    def test_invite_token_never_leaks_into_listing_routes(self) -> None:
        """一次性纪律：token 只在创建那一刻出现，列表与权限查询里都摸不到它。"""
        token = self.admin_token()
        status, _headers, data = call(self.base, self.share_path(GROUP_ADMIN),
                                      {"permission": "approve"},
                                      method="POST", headers=bearer(token))
        self.assertEqual(status, 201, data)
        secret = str(data["token"])
        for path in (sv.API_PATH_SHARES,
                     f"{sv.API_PATH_SHARES_PERMISSIONS}?group_id={GROUP_ADMIN}"):
            status, _headers, listing = call(self.base, path, headers=bearer(token))
            self.assertEqual(status, 200, listing)
            self.assertNotIn(secret, json.dumps(listing, ensure_ascii=False),
                             f"{path} 泄了明文 token")

    def test_reshare_needs_manage_level_in_that_group(self) -> None:
        """再分享＝manage：view 协作者转手访问权 403；manage 协作者 201；组长本人照旧 201。"""
        view = self.login("viewshare")
        status, _headers, data = call(self.base, self.share_path(), {"permission": "manage"},
                                      method="POST", headers=bearer(view))
        self.assertEqual(status, 403, f"view 协作者发出了新邀请：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(data["detail"]["granted"], "view", data)
        self.assertEqual(data["detail"]["required"], "manage", data)

        manager = self.login("mgrshare")
        status, _headers, data = call(self.base, self.share_path(), {"permission": "view"},
                                      method="POST", headers=bearer(manager))
        self.assertEqual(status, 201, f"组内 manage 再分享被挡：{status} {data}")
        # 组长本人（这个组的 created_by 就是 admin）：收紧前后同一条路，零变化
        status, _headers, data = call(self.base, self.share_path(GROUP_ADMIN),
                                      {"permission": "view"},
                                      method="POST", headers=bearer(self.admin_token()))
        self.assertEqual(status, 201, f"组长本人发邀请被挡：{status} {data}")

    # -- T23 --
    def test_revoke_route_mounted_for_inviter(self) -> None:
        """T23：inviter 撤销自己的邀请 → 200，形状 ``{ok, invite}``（不多塞键）。"""
        created = self.invite(permission="view", group_id=GROUP, inviter=self.admin)
        status, _headers, data = call(self.base, self.revoke_path(str(created["id"])),
                                      {"reason": "R5-3 撤销验收"}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 200, f"T23 没挂上或 inviter 撤销被拒：{status} {data}")
        self.assertEqual(set(data), {"ok", "invite"}, data)
        self.assertEqual(data["invite"]["status"], "revoked", data)
        self.assertEqual(data["invite"]["id"], created["id"], data)

    def test_revoke_is_403_for_stranger_and_200_for_group_manager(self) -> None:
        """鉴权＝仅 inviter / 队长 / 组内 manage：局外人 403，持 manage 的协作者 200。"""
        created = self.invite(permission="view", group_id=GROUP, inviter=self.admin)
        stranger = self.login("stranger")
        status, _headers, data = call(self.base, self.revoke_path(str(created["id"])), {},
                                      method="POST", headers=bearer(stranger))
        self.assertEqual(status, 403, f"局外人撤销了别人的邀请：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)

        manager = self.login("mgrshare")
        status, _headers, data = call(self.base, self.revoke_path(str(created["id"])), {},
                                      method="POST", headers=bearer(manager))
        self.assertEqual(status, 200, f"组内 manage 撤销被拒：{status} {data}")
        self.assertEqual(data["invite"]["status"], "revoked", data)

    def test_revoke_is_idempotent_and_event_ledger_stays_append_only(self) -> None:
        """撤销幂等（不重复追加事件）；事件账本只追加不改写（append-only 链自证）。"""
        created = self.invite(permission="view", group_id=GROUP, inviter=self.admin)
        path = self.revoke_path(str(created["id"]))
        before = self.events_count()
        status, _headers, data = call(self.base, path, {}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 200, data)
        after = self.events_count()
        self.assertGreater(after, before, "撤销必须留一条协作事件（可追责）")
        status, _headers, data = call(self.base, path, {}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 200, data)
        self.assertEqual(self.events_count(), after, "重复撤销追加了第二条事件")
        self.assertTrue(self.service.share_service.verify_events()["ok"],
                        "撤销写坏了 append-only 链")

    def test_revoked_collaborator_cannot_act_on_write_endpoints(self) -> None:
        """收权闭环：邀请撤销后，这个人在该组的写动作一律被挡（``grant_revoked``）。

        撤销在进程内做（inviter = 组长 DID，与 setUpClass 的邀请同源），
        被撤销的人以**真实登录会话**在 HTTP 上撞闸门 —— 端到端两端口径都要对。
        """
        name, uid = "viewshare", self.uids["viewshare"]
        target = self.fresh("pending")
        self.service.share_service.revoke(self.invites[name], actor=CAPTAIN,
                                          reason="R5-3 收权闭环")
        facts = self.service.collab_verdict(group_id=GROUP, required="view",
                                            operator_ids=[uid])
        self.assertTrue(facts["engaged"] and not facts["allowed"], facts)
        self.assertEqual(facts["reason"], "grant_revoked", facts)
        session = self.login(name)
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "approve"},
                                      method="POST", headers=bearer(session))
        self.assertEqual(status, 403, f"被撤销的人还能干预：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(data["detail"]["reason"], "grant_revoked", data)
        self.assertEqual(self.record_of(target).get("status"), "pending_confirmation")


class ShareInitiateTightenHttpTests(ShareAuthCase):
    """R5-4 §2.6 T21 收紧矩阵（PM-VERDICT-R5-3 §3.1 批准的 L1 事项）。

    收紧前的现状（R5-3 如实登记过）：T21 只查 ``data.write`` + ``collab_guard``，
    组里零邀请时闸门不接电 → 任何 ``data.write`` 用户可对**任意组**发邀请。
    收紧后放行两种身份，其余 403：

    * 组长本人（``groups.py`` 的 ``created_by`` 与操作者身份**事实相等**）→ 201
    * 组内 ``manage`` 协作者（``collab_guard`` 的等级判定真放行）→ 201
    * 无关的 ``data.write`` 用户 → 403 ``SHARE_FORBIDDEN``（``not_group_leader``）
    * ``view`` / ``approve`` 协作者 → 403 ``SHARE_FORBIDDEN``（等级不够，既有语义）

    拒绝侧的账本纪律与 ``collab_guard`` 同一条：业务账本零写入，只多一条 ``auth.denied``。
    找不到的组不在这里报 403：交回既有 ``GROUP_NOT_FOUND``（本轮不动那条口径）。
    """

    mode = "team"
    PW = "R54passw0rd!longenough"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = str(cls.service.auth.list_users()[0]["id"])
        # 组长本人：admin 自己建的组（created_by = admin 的 user_id）
        cls.service.group_service.create(title="R5-4 组长自建房", agent_ids=["agent-r54"],
                                         actor=cls.admin, group_id=GROUP_ADMIN)
        cls.uids: dict[str, str] = {}
        for name, permission in (("t4view", "view"), ("t4approve", "approve"),
                                 ("t4manage", "manage"), ("t4stranger", "")):
            user = cls.service.auth.create_user(name, "editor", cls.PW,
                                                actor={"user_id": "admin", "role": "admin"})
            uid = str(user["id"])
            cls.uids[name] = uid
            if permission:
                created = cls.service.share_service.invite(group_id=GROUP,
                                                           inviter_did=CAPTAIN,
                                                           permission=permission,
                                                           invitee_did=uid)
                cls.service.share_service.accept(token=created["token"], invitee_did=uid)

    def login(self, username: str, password: str | None = None) -> str:
        status, _headers, data = call(self.base, sv.API_PATH_AUTH_LOGIN,
                                      {"username": username,
                                       "password": password or self.PW}, method="POST")
        self.assertEqual(status, 200, data)
        body = data if isinstance(data, Mapping) else {}
        return str(body.get("token") or "")

    def admin_token(self) -> str:
        return self.login("admin", self.admin_pw)

    def share_path(self, group_id: str) -> str:
        return f"{sv.API_PATH_GROUPS}/{group_id}/share"

    def snapshot(self) -> dict:
        return {"records": len(self.service.store.all()),
                "events": self.events_count(),
                "denials": sum(1 for row in self.service.audit.entries()
                               if str(row.get("event") or "") == "auth.denied")}

    # -- 放行的两格 --
    def test_group_leader_is_201_on_his_own_group(self) -> None:
        """组长本人：``created_by`` 事实相等 → 201，一次性 ``invite_url`` 照旧回。"""
        status, _headers, data = call(self.base, self.share_path(GROUP_ADMIN),
                                      {"permission": "view"}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 201, f"组长发邀请被挡：{status} {data}")
        self.assertTrue(str(data.get("invite_url") or "").endswith(
            f"#{sv.SHARE_TOKEN_FRAGMENT}={data['token']}"), data)

    def test_group_manager_is_201(self) -> None:
        """组内 ``manage`` 协作者：等级判定真放行 → 201（收紧不改这一格）。"""
        status, _headers, data = call(self.base, self.share_path(GROUP),
                                      {"permission": "view"}, method="POST",
                                      headers=bearer(self.login("t4manage")))
        self.assertEqual(status, 201, f"组内 manage 再分享被挡：{status} {data}")

    # -- 挡下的三格 --
    def test_unrelated_data_write_user_is_403(self) -> None:
        """收紧的新增格：editor 角色（有 ``data.write``）对别人的组发邀请 → 403。"""
        before = self.snapshot()
        status, _headers, data = call(self.base, self.share_path(GROUP),
                                      {"permission": "manage"}, method="POST",
                                      headers=bearer(self.login("t4stranger")))
        self.assertEqual(status, 403, f"无关 data.write 用户发出了邀请：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(str(data["detail"]["reason"]), "not_group_leader", data)
        self.assertEqual(str(data["detail"]["created_by"]), CAPTAIN, data)
        self.assertEqual(self.events_count(), before["events"],
                         "拒绝的那一次写了协作事件（业务账本多了一条）")
        self.assertEqual(len(self.service.store.all()), before["records"],
                         "拒绝的那一次写了记录")
        denials = [row for row in self.service.audit.entries()
                   if str(row.get("event") or "") == "auth.denied"]
        self.assertEqual(len(denials), before["denials"] + 1,
                         "拒绝必须留一条 auth.denied 取证（既有 collab_guard 同口径）")
        last = denials[-1]
        detail = (last.get("detail") or {}) if isinstance(last.get("detail"), Mapping) else {}
        self.assertEqual(str(detail.get("verdict") or ""), "not_group_leader", last)
        self.assertEqual(str(detail.get("group_id") or ""), GROUP, last)
        self.assertEqual(str(detail.get("endpoint") or ""),
                         "POST /api/groups/<id>/share", last)

    def test_view_and_approve_collaborators_are_403(self) -> None:
        """等级不够的两种协作者：``view`` / ``approve`` 都不能转手访问权（既有语义不变）。"""
        for name, granted in (("t4view", "view"), ("t4approve", "approve")):
            status, _headers, data = call(self.base, self.share_path(GROUP),
                                          {"permission": "view"}, method="POST",
                                          headers=bearer(self.login(name)))
            self.assertEqual(status, 403, f"{name} 发出了新邀请：{status} {data}")
            self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
            self.assertEqual(str(data["detail"]["granted"]), granted, data)
            self.assertEqual(str(data["detail"]["required"]), "manage", data)

    def test_unknown_group_keeps_the_existing_not_found_code(self) -> None:
        """查无此组：不在这里发明 403，交回既有 ``GROUP_NOT_FOUND``（零口径漂移）。"""
        status, _headers, data = call(self.base, self.share_path("grp-does-not-exist"),
                                      {"permission": "view"}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 404, f"找不到的组被收紧闸门改写了口径：{status} {data}")
        self.assertEqual(code_of(data), "GROUP_NOT_FOUND", data)

    # -- 「无邀请者不受影响」的结构性证据 --
    def test_leader_path_is_structurally_unchanged(self) -> None:
        """结构性证据（铁律「无邀请者不受影响」）：零邀请组连 ``can()`` 都不调。

        三件事一次钉住：

        1. ``GROUP_PLAIN`` 一条邀请都没有 → 协作层闸门**不接电**（``no_invites``），
           ``can()`` 一次都不调 —— R5-3 的"零邀请 = 零新代码路径"结构保证，收紧没动它；
        2. 收紧只在这条"不接电"的路径上加了一次组长事实比较：admin 不是这个组的
           ``created_by`` → 403 ``not_group_leader``（收紧前这里放行）；
        3. 组长自己建房子的组（``GROUP_ADMIN``）：``share_leader_facts`` 说他是组长、
           协作层照样不接电（``engaged=False``）→ 201，和收紧前逐字同一条路。
        """
        plain = self.service.collab_verdict(group_id=GROUP_PLAIN,
                                            required=sv.COLLAB_PERMISSION_FOR_SHARE,
                                            operator_ids=[self.admin])
        self.assertFalse(plain["engaged"], plain)
        self.assertEqual(plain["reason"], "no_invites", plain)
        with mock.patch.object(self.service.share_service, "can",
                               side_effect=AssertionError("零邀请组不该调 can()")) as spy:
            status, _headers, data = call(self.base, self.share_path(GROUP_PLAIN),
                                          {"permission": "view"}, method="POST",
                                          headers=bearer(self.admin_token()))
            spy.assert_not_called()
        self.assertEqual(status, 403, f"零邀请组的无关用户没被收紧挡住：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(str(data["detail"]["reason"]), "not_group_leader", data)

        facts = self.service.collab_verdict(group_id=GROUP_ADMIN,
                                            required=sv.COLLAB_PERMISSION_FOR_SHARE,
                                            operator_ids=[self.admin])
        self.assertFalse(facts["engaged"], facts)
        self.assertIn(facts["reason"], ("no_invites", "no_inviter_binding"), facts)
        leader = self.service.share_leader_facts(GROUP_ADMIN, operator_ids=[self.admin])
        self.assertTrue(leader["leader"] and leader["known"], leader)
        status, _headers, data = call(self.base, self.share_path(GROUP_ADMIN),
                                      {"permission": "view"}, method="POST",
                                      headers=bearer(self.admin_token()))
        self.assertEqual(status, 201, data)
        self.assertEqual(str(data["share"]["inviter_did"]), self.admin, data)


class LocalModeParityTests(ShareAuthCase):
    """铁律 2 的主战场：local 免登录模式**全动作**零行为变化（PM-PLAN §7.5）。

    这一类不看"越权被挡住"，只看"没邀请的人一切照旧"：干预、回滚、发邀请、撤销
    在 local 模式下全绿，且协作事件账本在写路径前后一条都不许多（闸门只读不写账本）。
    """

    mode = "local"

    def test_local_intervene_is_untouched(self) -> None:
        target = self.fresh("pending")
        status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                      {"action_id": target, "decision": "approve",
                                       "reason": "local 免登录拍板"}, method="POST")
        self.assertEqual(status, 200, f"local 模式干预被闸门影响：{status} {data}")
        self.assertEqual(self.record_of(target).get("status"), "approved")

    def test_local_rollback_is_untouched(self) -> None:
        target = self.fresh("roll")
        before = self.service.rollback_executions
        status, _headers, data = call(self.base, sv.API_PATH_ROLLBACK,
                                      {"action_id": target, "confirm_all": True},
                                      method="POST")
        self.assertEqual(status, 200, f"local 模式回滚被闸门影响：{status} {data}")
        self.assertNotEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertGreater(self.service.rollback_executions, before)

    def test_local_share_invite_and_revoke_round_trip(self) -> None:
        """local 模式下 T21→T23 闭环可用（免登录 = 自己建房子的组长，不经 ``can()`` 等级判定）。

        R5-4 收紧后"组长本人"是事实比较：免登录会话的 actor 是 ``"local"``
        （``_actor_id_of`` 实测口径），``groups.py`` 不给 actor 时写的 ``created_by``
        也是 ``"local"`` —— 同一台单机上自己建的组当然自己发得了邀请。
        别人（CAPTAIN）建的组在 local 单机上是"别人的组"：收紧后发不出邀请，
        这一格在下面 ``test_local_cannot_share_someone_elses_group`` 里如实钉住。
        """
        status, _headers, data = call(self.base, f"{sv.API_PATH_GROUPS}/{GROUP_LOCAL}/share",
                                      {"permission": "view"}, method="POST")
        self.assertEqual(status, 201, f"local 发邀请被挡：{status} {data}")
        invite_id = str(data["share"]["id"])
        self.assertIn("token", data, data)
        status, _headers, data = call(self.base, f"{sv.API_PATH_SHARES}/{invite_id}/revoke",
                                      {}, method="POST")
        self.assertEqual(status, 200, f"local 撤销被挡：{status} {data}")
        self.assertEqual(data["invite"]["status"], "revoked", data)

    def test_local_cannot_share_someone_elses_group(self) -> None:
        """R5-4 收紧的新增格：别人的组（``created_by`` 不是自己）在 local 单机也发不出邀请。

        这不是"免登录功能退化"：单机模式下这条路径本来没有第二个用户可分享，
        而 team 模式下"任何 ``data.write`` 用户可对任意组发邀请"是真越权。
        组长本人（含 local）逐字未变 = 铁律「无邀请者不受影响」的边界。
        """
        status, _headers, data = call(self.base, f"{sv.API_PATH_GROUPS}/{GROUP}/share",
                                      {"permission": "view"}, method="POST")
        self.assertEqual(status, 403, f"无关身份发出了邀请：{status} {data}")
        self.assertEqual(code_of(data), "SHARE_FORBIDDEN", data)
        self.assertEqual(str(data["detail"]["reason"]), "not_group_leader", data)

    def test_local_write_paths_add_no_collab_events(self) -> None:
        """local 干预/回滚不写协作账本：闸门只读，代行标注只能由协作层自己写。"""
        pending, roll = self.fresh("pending"), self.fresh("roll")
        before = self.events_count()
        status, _headers, _data = call(self.base, sv.API_PATH_INTERVENE,
                                       {"action_id": pending, "decision": "reject",
                                        "reason": "零事件取证"}, method="POST")
        self.assertEqual(status, 200)
        status, _headers, _data = call(self.base, sv.API_PATH_ROLLBACK,
                                       {"action_id": roll, "confirm_all": True},
                                       method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(self.events_count(), before,
                         "local 写路径多写了协作事件（闸门越界写账本）")

    def test_group_without_invites_behaves_exactly_as_before(self) -> None:
        """零邀请组（对照组）：干预照旧绿；``can()`` 一次都不调（结构级零变化）。"""
        target = self.fresh("pending", group_id=GROUP_PLAIN)
        with mock.patch.object(self.service.share_service, "can",
                               side_effect=AssertionError("零邀请组不该调 can()")) as spy:
            status, _headers, data = call(self.base, sv.API_PATH_INTERVENE,
                                          {"action_id": target, "decision": "approve",
                                           "reason": "零邀请组"}, method="POST")
            spy.assert_not_called()
        self.assertEqual(status, 200, data)
        self.assertEqual(self.record_of(target).get("status"), "approved")

    def test_contract_surface_gains_no_new_route(self) -> None:
        """契约自证：T21/T23 走 ``_group_route``/``_share_revoke_route``，不新增契约面。

        0.6.0 L1 修订：`POST /api/groups` 现在是**有意挂上**的 T18 建组入口
        （老板批准的八功能接线），因此从「不许出现」翻转为「必须出现」。
        T21 的发邀请仍走 `_group_route`（`/api/groups/<id>/share`），契约里依旧不可见 ——
        这条测试原本要防的"helper 挂载顺手扩面"仍然成立。
        """
        contract = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
        routes = {(str(row.get("verb")), str(row.get("path"))) for row in contract["routes"]}
        self.assertIn(("GET", "/api/groups"), routes, "契约里的 GET /api/groups 不见了")
        self.assertIn(("GET", "/api/shares"), routes, "契约里的 GET /api/shares 不见了")
        self.assertIn(("POST", "/api/groups"), routes,
                      "T18 建组入口必须在契约里（0.6.0 L1）")
        self.assertNotIn(("POST", "/api/groups/{id}/share"), routes,
                         "T21 走 helper 挂载，不许在契约里长出独立行")
        self.assertTrue(callable(self.service.share_revoke))
        self.assertTrue(callable(sv.TraceLensHandler._share_revoke_route))
        self.assertTrue(callable(sv.TraceLensHandler._group_route))


class LetterCollabEventsTests(ShareAuthCase):
    """§2.4 T7 一行接线：代行句在 HTTP 认定书里出现 / 消失 + 窗口未取全如实登记。"""

    mode = "local"

    def create_letter(self, record_ids: list[str]) -> dict:
        status, _headers, data = call(self.base, sv.API_PATH_SECRETARIAT_LETTERS,
                                      {"subject": "R5-3 代行验收", "record_ids": record_ids,
                                       "responsibility": "technical"}, method="POST")
        self.assertEqual(status, 201, data)
        return data

    def test_annotated_event_reaches_the_letter_over_http(self) -> None:
        """事件层标注 → HTTP 认定书 ``actions[].on_behalf_note``（接线前这行永不出）。"""
        target = self.fresh("roll")
        self.service.share_service.record_event(actor="did:r53-collab",
                                                action="intervene.approve", target=target,
                                                detail={"group_id": GROUP},
                                                on_behalf_of=OWNER)
        letter = self.create_letter([target])["letter"]
        rows = {str(row.get("id")): row for row in letter["actions"]}
        self.assertIn("on_behalf_note", rows[target], "代行句没进认定书 = T7 那行接线没生效")
        self.assertIn(OWNER, rows[target]["on_behalf_note"], rows[target])
        self.assertEqual(rows[target]["on_behalf_actor"], "did:r53-collab", rows[target])

    def test_letter_without_annotation_gains_no_new_key(self) -> None:
        """无代行标注时逐字不变：动作条目不多这三个键（既有 R5-2 口径不破）。"""
        target = self.fresh("pending")
        letter = self.create_letter([target])["letter"]
        for row in letter["actions"]:
            for key in ("on_behalf_of", "on_behalf_actor", "on_behalf_note"):
                self.assertNotIn(key, row, f"无标注的动作条目多出了 {key}")

    def test_window_not_fully_read_is_registered_in_verification(self) -> None:
        """事件多于窗口 → ``verification.problems`` 登记一条（不伪装"看全了"）。"""
        for index in range(3):
            self.service.share_service.record_event(actor="did:r53-flood",
                                                    action="share.debug",
                                                    target=f"evt-r53-{index}")
        target = self.fresh("roll")
        with mock.patch.object(sv.InterveneService, "LETTER_COLLAB_EVENTS_LIMIT", 2):
            letter = self.create_letter([target])["letter"]
        problems = [item for item in (letter.get("verification") or {}).get("problems") or []
                    if (item.get("detail") or {}).get("scope") == "collab_events"]
        self.assertEqual(len(problems), 1, f"窗口未取全没登记：{problems}")
        detail = problems[0]["detail"]
        self.assertEqual(detail["limit"], 2, detail)
        self.assertEqual(detail["returned"], 2, detail)
        self.assertGreaterEqual(detail["total"], 3, detail)

    def test_window_fully_read_registers_nothing(self) -> None:
        """事件在窗口内 → 不登记（"看全了"不需要自我声明）。"""
        target = self.fresh("pending")
        with mock.patch.object(sv.InterveneService, "LETTER_COLLAB_EVENTS_LIMIT",
                               self.events_count() + 50):
            letter = self.create_letter([target])["letter"]
        scopes = [(item.get("detail") or {}).get("scope")
                  for item in (letter.get("verification") or {}).get("problems") or []]
        self.assertNotIn("collab_events", scopes, scopes)

    def test_letter_never_rewrites_records_nor_signature_fields(self) -> None:
        """T7 只读：生成认定书不改记录、不动签名域（SIGNATURE_FIELDS 一字未动）。"""
        from agent_explainer import secretariat as sec

        self.assertEqual(tuple(sec.SIGNATURE_FIELDS), (
            "timestamp", "agent_id", "action_type", "tool", "input_hash", "output_hash",
            "prev_hash", "entry_hash", "risk_level", "confidence", "status"),
            "签名域被改写（铁律 1：SIGNATURE_FIELDS 一字未动）")
        target = self.fresh("roll")
        before = copy.deepcopy(self.record_of(target))
        self.service.share_service.record_event(actor="did:r53-collab",
                                                action="intervene.approve", target=target,
                                                on_behalf_of=OWNER)
        self.create_letter([target])
        self.assertEqual(copy.deepcopy(self.record_of(target)), before,
                         "生成认定书改写了记录（T7 是只读消费）")


if __name__ == "__main__":  # 手工自检：python tests/test_share_auth.py -v
    unittest.main(verbosity=2)