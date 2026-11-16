"""R7-2（REQ-007 B7-2）验收测试：建议**写侧端点挂载**（T29 / T30 / T31 + 引擎开关）。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest tests.test_suggestions_mount_http -v

钉住的口径（任务 R7-2 §1.3 / §2.2 + PM-VERDICT-R7-1 §3.1 预批的 L1）：

1. **写侧挂载，零新码名**：``POST /api/suggestions/<id>/accept``（T29）、
   ``.../dismiss``（T30）、``POST /api/preferences``（T31）、
   ``POST /api/suggestions {"enabled": bool}``（开关专用入口）。
   错误一律走 ``EXPANSION_ERROR_TABLE`` 既有的 ``SUGGESTION_NOT_FOUND`` /
   ``SUGGESTION_STATE_INVALID`` / ``SUGGESTION_FIELD_INVALID`` / ``PREFERENCE_INVALID``，
   一个码名都不新增。
2. **守门三件套与 T21/T23/T24 逐字同款**：会话 + CSRF（只校验 Cookie 通道，
   token 通道与 local 免登录豁免）+ actor 由会话决定。请求体里没有
   ``actor``/``owner``/``user_id`` 可传：采纳永远落在**当前会话**名下，
   A 的决定不会变成 B 的偏好。
3. **权限位照 docs/07 §5 的冻结行逐字**：T29/T30/T31 写侧 = ``intervene.decide``
   （§5 那三行写在 5.0 路由表里，冻结区不可改）。引擎开关是本轮新增格、§5 无冻结行，
   取 ``settings.write``：``set_enabled`` 没有 user 维度，是服务级开关，
   挂在 ``records.read`` 上等于任何只读角色都能替全站关掉建议。
4. **无建议用户零行为变化**：GET 形状逐字不变；越权只按既有码族回答、业务账本零写入、
   只多一条 ``auth.denied``；干预主链路（``/intervene``）不受写侧挂载影响。
5. **开关诚实形状**：回读服务实例真值，并如实带 ``durable: false`` + ``note``
   （内存开关、重启回默认开——不落盘，不假装持久化）。
6. 传输纪律：``tests/http_fixture``（连接级瞬态补一次，**POST 绝不重放**，断言级零重试）；
   临时目录一律 ``tests/scratch.py``。
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import auth as auth_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import suggestions as sg  # noqa: E402
import http_fixture as fx  # noqa: E402
from scratch import scratch_dir  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
BASE_TARGET = "act-demo-0005"
TOOL = "filesystem.remove"
PARAM = "max_delete_count"
IV_PREFIX = "act-r7m-"                       # 本文件独占的夹具记录 id 前缀
PW = "R72passw0rd!longenough"
# 零新码名铁律：本轮挂载只允许出这些既有码（server.ERROR_TABLE / EXPANSION_ERROR_TABLE）
ALLOWED_CODES = {"SUGGESTION_NOT_FOUND", "SUGGESTION_STATE_INVALID",
                 "SUGGESTION_FIELD_INVALID", "PREFERENCE_INVALID",
                 "AUTH_REQUIRED", "CSRF_FAILED", "ROLE_FORBIDDEN",
                 "METHOD_NOT_ALLOWED", "ROUTE_NOT_FOUND", "INTERVENE_BODY_NOT_OBJECT"}


def target(idx: int) -> dict:
    """可决的目标记录（演示夹具副本换 id，决定段清空）——照 R7-1 同形状。"""
    rec = copy.deepcopy(next(r for r in NATIVE if r["id"] == BASE_TARGET))
    rec["id"] = f"{IV_PREFIX}{idx:02d}"
    rec["sequence_no"] = 950 + idx
    rec["status"] = "pending_confirmation"
    rec["requires_action"] = True
    rec["user_intervention"] = None
    rec["execution_receipt"] = {"state": "unreported", "reported_by": None,
                                "reported_at": None, "detail": "夹具：无回执",
                                "sdk_version": None,
                                "effect_notice": "intervention.effect.recorded_only"}
    return rec


def _decode(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", "replace")


def call(base: str, path: str, body: object | None = None, method: str = "GET",
         headers: dict[str, str] | None = None) -> tuple[int, dict, object]:
    """一发一收；4xx/5xx 也把响应体带回来（要看错误码）。POST 绝不重放。"""
    def once() -> tuple[int, dict, object]:
        payload = None
        extra = {"Connection": "close"}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            extra["Content-Type"] = "application/json"
        extra.update(dict(headers or {}))
        request = urllib.request.Request(f"{base}{path}", data=payload,
                                         headers=extra, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20.0) as response:
                return response.status, dict(response.headers), _decode(response.read())
        except urllib.error.HTTPError as exc:      # 状态码是结论，不是瞬态
            return exc.code, dict(exc.headers), _decode(exc.read())

    return fx.urlopen_resilient(once, verb=method, path=path, timeout=20.0)


def bearer(token: str) -> dict[str, str]:
    return {auth_mod.TOKEN_HEADER: token}


def code_of(payload: object) -> str:
    return str(payload.get("code") or "") if isinstance(payload, dict) else ""


class SuggestionMountCase(unittest.TestCase):
    """公共夹具：逐例一台全新的产品口径服务器（``port=0`` 随机空闲口）。

    为什么逐例一台、不是类级共用（照 R7-1 的 ``SuggestionsWiringHttpTests`` 同一取舍）：
    建议引擎按 ``(user, tool, param)`` **聚合成一行**，共用服务器时第二例拿不到
    「一条全新的 open 建议」，409 会是假红。这里要的是隔离，不是省那零点几秒。
    """

    mode = "local"

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"suggestions-mount-{self.mode}-{case}")
        records = copy.deepcopy(NATIVE) + [target(i) for i in range(1, 21)]
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=records, quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            mode=self.mode, bootstrap_admin=(self.mode == "team"),
            session_ttl=3600, sign_on_write=True)
        self.host, self.port = self.server.server_address[:2]
        self.base = self.server.url()
        self.service = self.server.service
        fx.wait_port_ready(self.port)
        self.boot = self.service.auth.bootstrap_report or {}
        self.admin_pw = str(self.boot.get("password") or "")
        self.uids: dict[str, str] = {}
        if self.mode == "team":
            for name, role in (("r72view", "viewer"), ("r72edit", "editor"),
                               ("r72edit2", "editor")):
                user = self.service.auth.create_user(name, role, PW,
                                                     actor={"user_id": "admin",
                                                            "role": "admin"})
                self.uids[name] = str(user["id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.root, ignore_errors=True)

    # -- 助手 ------------------------------------------------------------- #
    def modify(self, idx: int, value, headers: dict[str, str] | None = None):
        return call(self.base, "/intervene",
                    {"action_id": f"{IV_PREFIX}{idx:02d}", "decision": "modify",
                     "modified_params": {PARAM: value}}, method="POST", headers=headers)

    def rows(self, headers: dict[str, str] | None = None, *, param: str = PARAM) -> list[dict]:
        status, _hd, data = call(self.base, sv.API_PATH_SUGGESTIONS, headers=headers)
        self.assertEqual(200, status, data)
        return [r for r in (data or {}).get("suggestions", []) if r.get("param") == param]

    def seed(self, start: int = 1, headers: dict[str, str] | None = None) -> str:
        """连发 3 次同向 modify → 产出一条**新的**达阈 open 建议，返回它的 id。

        类级夹具共用一台服务器、建议账本逐例累加，所以种子必须按「本轮新增 + 仍 open」
        挑，不能拿 ``rows()[0]``——那会挑到上一轮已经采纳/拒绝过的行（409 假红）。
        team 模式下 ``/intervene`` 要 ``intervene.decide`` + CSRF，所以传会话头；
        种子计在这个会话的 actor 名下（身份口径 = 会话，不是请求体）。
        """
        before = {r["id"] for r in self.rows(headers)}
        for offset, value in enumerate((10, 8, 6)):
            status, _hd, data = self.modify(start + offset, value, headers)
            self.assertEqual(200, status, data)
        found = [r for r in self.rows(headers)
                 if r["id"] not in before and str(r.get("status")) == "open"
                 and int(r.get("occurrences") or 0) >= sg.DEFAULT_THRESHOLD]
        self.assertEqual(1, len(found), f"每轮只该产出一条新建议：{found}")
        return str(found[0]["id"])

    def post_action(self, suggestion_id: str, verb: str, body=None,
                    headers: dict[str, str] | None = None):
        return call(self.base, f"{sv.API_PATH_SUGGESTIONS}/{suggestion_id}/{verb}",
                    {} if body is None else body, method="POST", headers=headers)

    def post_preferences(self, body, headers: dict[str, str] | None = None):
        return call(self.base, sv.API_PATH_PREFERENCES, body, method="POST", headers=headers)

    def post_switch(self, body, headers: dict[str, str] | None = None):
        return call(self.base, sv.API_PATH_SUGGESTIONS, body, method="POST", headers=headers)

    def ledger(self) -> dict:
        facts = self.service.suggestion_service.facts()
        return {"records": len(self.service.store.all()),
                "suggestions": int(facts.get("suggestions") or 0),
                "preferences": int(facts.get("preferences") or 0),
                "denied": sum(1 for row in self.service.audit.entries()
                              if str(row.get("event") or "") == "auth.denied")}

    def assert_denied_code(self, status: int, data: object, expected: str) -> str:
        """拒绝必须是**既有码族**的那个码，且码名已在 ``server.ERROR_TABLE`` 注册。"""
        got = code_of(data)
        self.assertEqual(expected, got, f"码名漂移：{status} {data}")
        self.assertIn(got, ALLOWED_CODES, f"引入了新错误码：{got}")
        self.assertIn(got, sv.ERROR_TABLE, f"{got} 不在既有码表里（零新码名铁律）")
        self.assertIsInstance(data, dict)
        self.assertTrue(str((data or {}).get("message") or ""), f"{got} 的人话 message 为空")
        return got


class LocalMountHttpTests(SuggestionMountCase):
    """免登录单机（local 天然有全部能力）：挂载形状、判定语义、码族、只挂这一格。"""

    def test_accept_is_mounted_and_binds_the_session_actor(self) -> None:
        sug_id = self.seed()
        status, _hd, data = self.post_action(sug_id, "accept")
        self.assertEqual(200, status, f"T29 没挂上或被拒：{status} {data}")
        self.assertEqual({"ok", "action", "suggestion", "enabled", "threshold",
                          "cooldown_days", "preference"}, set(data),
                         f"响应形状多塞/少塞键：{sorted(data)}")
        self.assertEqual("accept", data["action"])
        self.assertEqual("accepted", data["suggestion"]["status"])
        self.assertEqual(sg.DEFAULT_THRESHOLD, data["threshold"],
                         "阈值由服务端回读，界面不许写死「3 次」")
        self.assertEqual(sg.COOLDOWN_DAYS, data["cooldown_days"])
        self.assertIsInstance(data["enabled"], bool, "开关状态必须如实回读")
        pref = data["preference"]
        self.assertEqual("suggestion", pref["source"], "采纳生成的偏好必须标 suggestion")
        row = [r for r in self.rows() if r["id"] == sug_id][0]
        expected = str(self.service.auth.local_session()["user_id"])
        self.assertEqual(expected, row["user_id"],
                         "读侧身份 = 会话身份（local 免登录 = local_session 的 user_id）")
        self.assertEqual(expected, pref["user_id"],
                         "actor 由会话决定：采纳落在建议主人=当前会话名下，请求体说了不算")
        self.assertEqual(TOOL, pref["tool"])
        self.assertEqual(1, len(self.service.suggestion_service.preferences(
            user_id=expected)))

    def test_dismiss_sets_the_seven_day_cooldown_and_echoes_the_reason(self) -> None:
        sug_id = self.seed(start=5)
        status, _hd, data = self.post_action(sug_id, "dismiss", {"reason": "先不动"})
        self.assertEqual(200, status, f"T30 没挂上：{status} {data}")
        self.assertNotIn("preference", data, "拒绝不生成偏好，响应也不许伪造一条")
        entry = data["suggestion"]
        self.assertEqual("dismissed", entry["status"])
        self.assertTrue(str(entry["cooldown_until"]), "7 天冷却到期时间由服务端算")
        self.assertEqual("先不动", entry["dismiss_reason"])
        self.assertEqual(sg.COOLDOWN_DAYS, data["cooldown_days"])
        self.assertEqual(0, self.service.suggestion_service.facts()["preferences"],
                         "拒绝不写偏好账本")

    def test_unknown_suggestion_keeps_the_existing_not_found_code(self) -> None:
        status, _hd, data = self.post_action("sug-does-not-exist", "accept")
        self.assertEqual(404, status, data)
        self.assert_denied_code(status, data, "SUGGESTION_NOT_FOUND")
        self.assertEqual({"suggestion_id": "sug-does-not-exist"}, (data or {}).get("detail"))

    def test_second_decision_on_the_same_suggestion_is_state_invalid(self) -> None:
        sug_id = self.seed(start=9)
        first, _hd, data = self.post_action(sug_id, "dismiss")
        self.assertEqual(200, first, data)
        status, _hd, data = self.post_action(sug_id, "accept")
        self.assertEqual(409, status, data)
        self.assert_denied_code(status, data, "SUGGESTION_STATE_INVALID")
        self.assertEqual("dismissed", (data or {}).get("detail", {}).get("status"),
                         "状态冲突要说清当下是什么状态，不许只报个码")

    def test_preferences_post_is_created_and_upserts_the_same_key(self) -> None:
        status, _hd, data = self.post_preferences({"tool": TOOL, "param": PARAM, "value": 6})
        self.assertEqual(201, status, f"T31 写侧没挂上：{status} {data}")
        self.assertEqual({"ok", "preference"}, set(data), f"响应形状：{sorted(data)}")
        first = data["preference"]
        self.assertEqual("manual", first["source"], "手动偏好必须标 manual，不冒充采纳来的")
        self.assertEqual(6, first["value"])
        status, _hd, second = self.post_preferences({"tool": TOOL, "param": PARAM, "value": 4})
        self.assertEqual(201, status, second)
        self.assertEqual(first["id"], second["preference"]["id"],
                         "同一 (user,tool,param) 必须覆盖，不许长第二行")
        status, _hd, data = call(self.base, sv.API_PATH_PREFERENCES)
        self.assertEqual(200, status, data)
        rows = [p for p in data["preferences"] if p["param"] == PARAM]
        self.assertEqual(1, len(rows), f"GET 读侧仍是一条：{rows}")

    def test_preferences_post_rejects_bad_values_with_the_existing_code(self) -> None:
        for body in ({"tool": "", "param": PARAM, "value": 6},
                     {"tool": TOOL, "param": "", "value": 6},
                     {"tool": TOOL, "param": PARAM, "value": None},
                     {"tool": TOOL, "param": PARAM, "value": {"nested": [1]}}):
            status, _hd, data = self.post_preferences(body)
            self.assertEqual(400, status, f"{body} 必须 400：{data}")
            self.assert_denied_code(status, data, "PREFERENCE_INVALID")

    def test_switch_is_service_level_honest_and_silences_the_chain(self) -> None:
        status, _hd, data = self.post_switch({"enabled": False})
        self.assertEqual(200, status, f"开关没挂上：{status} {data}")
        self.assertEqual({"ok", "enabled", "threshold", "cooldown_days", "durable",
                          "note"}, set(data), f"开关响应形状：{sorted(data)}")
        self.assertFalse(data["enabled"], "写 false 必须读回 false，不假绿")
        self.assertFalse(data["durable"],
                         "内存开关不落盘：必须如实带 durable=false，不假装持久化")
        self.assertTrue(str(data["note"]), "诚实说明缺的持久化，不留空话")
        before = self.ledger()
        for offset, value in enumerate((10, 8, 6)):
            status, _hd, data = self.modify(13 + offset, value)
            self.assertEqual(200, status, data)
            self.assertEqual("modified", data["status"], "关掉建议绝不影响决定")
        status, _hd, data = call(self.base, sv.API_PATH_SUGGESTIONS)
        self.assertFalse(data["enabled"], "GET 回读的就是服务实例真值（读写同源）")
        self.assertEqual(0, self.ledger()["suggestions"] - before["suggestions"],
                         "关掉之后连「消费」这个动作都不发生")
        self.assertEqual(3, self.ledger()["records"] - before["records"],
                         "关掉的是分析面，不是事实：干预事件照常落盘")
        status, _hd, data = self.post_switch({"enabled": True})
        self.assertEqual(200, status, data)
        self.assertTrue(data["enabled"])

    def test_switch_rejects_a_non_boolean_payload(self) -> None:
        for body in ({"enabled": "false"}, {"enabled": 0}, {"enabled": None}):
            status, _hd, data = self.post_switch(body)
            self.assertEqual(400, status, f"{body} 必须 400：{data}")
            self.assert_denied_code(status, data, "SUGGESTION_FIELD_INVALID")
            self.assertEqual("enabled", (data or {}).get("detail", {}).get("field"),
                             "统一错误体的 detail.field 要说清是哪个字段非法")

    def test_the_mount_is_limited_to_the_declared_slots(self) -> None:
        """只挂声明的格子：裸 id 的 POST 说 405，没声明的子动作仍 404。"""
        sug_id = self.seed(start=17)
        status, _hd, data = call(self.base, f"{sv.API_PATH_SUGGESTIONS}/{sug_id}",
                                 {}, method="POST")
        self.assertEqual(405, status, data)
        self.assert_denied_code(status, data, "METHOD_NOT_ALLOWED")
        self.assertIn("only /accept and /dismiss",
                      (data or {}).get("detail", {}).get("hint", ""))
        status, _hd, data = self.post_action(sug_id, "revoke")
        self.assertEqual(404, status, data)
        self.assert_denied_code(status, data, "ROUTE_NOT_FOUND")
        # GET 侧那条 405 逐字保留（R7-1 的口径不许被挂载顺手改掉）
        status, _hd, data = call(self.base, f"{sv.API_PATH_SUGGESTIONS}/{sug_id}/accept")
        self.assertEqual(405, status, data)
        self.assertEqual("accept/dismiss are POST only",
                         (data or {}).get("detail", {}).get("hint", ""))

    def test_users_without_suggestions_see_exactly_what_they_saw_before(self) -> None:
        """无建议用户零行为变化：GET 形状不变，POST 只按既有码族回答，主链路不受影响。"""
        status, _hd, data = call(self.base, sv.API_PATH_SUGGESTIONS)
        self.assertEqual(200, status, data)
        self.assertEqual({"ok", "suggestions", "count", "enabled", "threshold",
                          "cooldown_days"}, set(data),
                         f"T28 读侧形状不许被挂载改动：{sorted(data)}")
        before = self.ledger()
        status, _hd, data = self.post_action("sug-not-mine", "accept")
        self.assertEqual(404, status, data)
        self.assert_denied_code(status, data, "SUGGESTION_NOT_FOUND")
        status, _hd, data = self.modify(20, 7)
        self.assertEqual(200, status, data)
        self.assertEqual("modified", data["status"], "干预主链路必须照常工作")
        self.assertEqual(0, self.ledger()["denied"] - before["denied"],
                         "本机没有越权面：不该新增任何 auth.denied 审计")
        self.assertTrue(self.service.require_audit_chain()["ok"], "挂载不许动审计链")

    def test_contract_reports_the_two_exact_paths_mounted(self) -> None:
        """契约自证：两条精确路径的 POST 行各恰好一条，权限/CSRF 与代码同源。"""
        routes = {(r["path"], r["verb"]): r for r in CONTRACT["routes"]}
        for path in ("/api/suggestions", "/api/preferences"):
            self.assertEqual(1, sum(1 for r in CONTRACT["routes"]
                                    if (r["path"], r["verb"]) == (path, "POST")),
                             f"{path} 的 POST 行必须恰好一条")
            row = routes[(path, "POST")]
            self.assertTrue(row["csrf"], "写侧必须登记 CSRF（与 T23/T24 同口径）")
            self.assertTrue(row["line"], "行号来自代码抽取，不许手写")
        self.assertEqual(["intervene.decide"],
                         routes[("/api/preferences", "POST")]["abilities"],
                         "权限位照 docs/07 §5 的 T31 行：写侧 intervene.decide")
        self.assertEqual(["settings.write"],
                         routes[("/api/suggestions", "POST")]["abilities"],
                         "引擎开关是服务级：settings.write，不是 records.read")
        for path, _verb in routes:
            self.assertFalse(path.startswith("/api/suggestions/"),
                             "helper 挂载的子路由在契约里必须不可见（T21/T23 先例）")
        self.assertEqual([], [c for c in CONTRACT["declared_vs_mounted"]["unmounted"]
                              if "SUGGESTION" in c or "PREFERENCE" in c],
                         "建议族常量不得新增「声明 ≠ 挂载」")


class TeamSuggestionMountHttpTests(SuggestionMountCase):
    """team 模式：会话/CSRF/角色三格矩阵 + 「actor 只认会话」的身份铁律。

    三个账号在 ``setUp`` 里逐例新建（``r72view`` / ``r72edit`` / ``r72edit2``），
    口令固定为模块常量 ``PW``；``self.uids`` 是 username → user_id 的映射。
    """

    mode = "team"

    # -- 会话助手 --------------------------------------------------------- #
    def login(self, username: str, password: str | None = None) -> dict:
        status, _hd, data = call(self.base, sv.API_PATH_AUTH_LOGIN,
                                 {"username": username, "password": password or PW},
                                 method="POST")
        self.assertEqual(200, status, data)
        return data if isinstance(data, dict) else {}

    def cookie(self, login: dict) -> dict[str, str]:
        value = (login.get("cookie") or {}).get("value") if isinstance(login, dict) else ""
        return {"Cookie": f"{auth_mod.SESSION_COOKIE}={value}"}

    def csrf(self, login: dict) -> dict[str, str]:
        return {**self.cookie(login), auth_mod.CSRF_HEADER: str(login.get("csrf_token") or "")}

    def admin_token(self) -> str:
        return str(self.login("admin", self.admin_pw).get("token") or "")

    def editor(self, name: str = "r72edit") -> dict:
        """有 ``intervene.decide`` 的会话：只有它能造种子（``/intervene`` 要这一格）。"""
        return self.login(name)

    def test_anonymous_write_is_auth_required_and_changes_nothing(self) -> None:
        before = self.ledger()
        for label, call_it in (
                ("accept", lambda: self.post_action("sug-anon", "accept")),
                ("preferences", lambda: self.post_preferences(
                    {"tool": TOOL, "param": PARAM, "value": 6})),
                ("switch", lambda: self.post_switch({"enabled": False}))):
            status, _hd, data = call_it()
            self.assertEqual(401, status, f"{label} 匿名写入必须 401：{data}")
            self.assert_denied_code(status, data, "AUTH_REQUIRED")
        after = self.ledger()
        self.assertEqual(before["suggestions"], after["suggestions"], "越权失败零写入")
        self.assertEqual(before["preferences"], after["preferences"], "越权失败零写入")
        self.assertTrue(self.service.suggestion_service.enabled,
                        "匿名那次没把开关拨下去")

    def test_cookie_channel_needs_the_csrf_pair(self) -> None:
        editor = self.editor()
        sug_id = self.seed(start=3, headers=self.csrf(editor))
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.cookie(editor))
        self.assertEqual(403, status, f"Cookie 通道的 CSRF 守门没生效：{status} {data}")
        self.assert_denied_code(status, data, "CSRF_FAILED")
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.csrf(editor))
        self.assertEqual(200, status, f"带上配对头就该采纳成功：{status} {data}")
        self.assertEqual(self.uids["r72edit"], data["preference"]["user_id"],
                         "采纳必须绑在当前会话的 user_id 名下")

    def test_token_channel_is_exempt_from_csrf(self) -> None:
        """token 通道免 CSRF 双提交；actor 由**凭证**决定（请求体里塞谁都没用）。

        种子也用同一条 token 造：建议归属 = 凭证身份，挂载侧的归属校验只认这个口径。
        """
        token = self.admin_token()
        headers = bearer(token)
        sug_id = self.seed(start=6, headers=headers)
        status, _hd, data = self.post_action(
            sug_id, "accept",
            {"user_id": self.uids["r72edit"], "actor": self.uids["r72edit"],
             "owner": "someone-else"},
            headers=headers)
        self.assertEqual(200, status, f"token 通道应免 CSRF：{status} {data}")
        self.assertEqual(self.admin_user_id(token), data["preference"]["user_id"],
                         "token 通道的 actor 同样由凭证决定，请求体说了不算")
        self.assertNotEqual(self.uids["r72edit"], data["preference"]["user_id"],
                            "编辑的会话身份不许被请求体冒名顶替")

    def admin_user_id(self, token: str) -> str:
        """/api/auth/me 回读 token 背后的真实 user_id（不用测试侧的猜测）。"""
        status, _hd, data = call(self.base, sv.API_PATH_AUTH_ME, headers=bearer(token))
        self.assertEqual(200, status, data)
        body = data if isinstance(data, dict) else {}
        got = str(body.get("actor_id") or ((body.get("user") or {}).get("id"))
                  or body.get("user_id") or "")
        self.assertTrue(got, f"/api/auth/me 没给出会话身份（actor_id）：{body}")
        return got

    def test_decisions_are_scoped_to_the_logged_in_user(self) -> None:
        """A 采纳的建议不会变成 B 的偏好：per-user 数据面在挂载后仍然隔离。

        服务层 ``accept`` 只按 id 取行（R7-1 交付口径，当时只在进程内可达）；
        挂成 HTTP 后由**挂载侧**补归属校验（``server.suggestion_action`` 的 owned 检查），
        回答与读侧口径一致的 404 ``SUGGESTION_NOT_FOUND``——别人的行在你的清单里查不到。
        """
        a = self.editor("r72edit")
        b = self.editor("r72edit2")
        sug_id = self.seed(start=9, headers=self.csrf(a))
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.csrf(b))
        self.assertEqual(404, status, f"B 不该能动 A 的建议：{status} {data}")
        self.assert_denied_code(status, data, "SUGGESTION_NOT_FOUND")
        status, _hd, data = self.post_action(sug_id, "dismiss", {"reason": "B 想替 A 拒绝"},
                                             headers=self.csrf(b))
        self.assertEqual(404, status, f"拒绝侧同样不许越权：{status} {data}")
        self.assert_denied_code(status, data, "SUGGESTION_NOT_FOUND")
        self.assertEqual([sug_id], [r["id"] for r in self.rows(self.csrf(a))
                                    if r["status"] == "open"],
                         "B 那次越权不许把 A 的建议改成 accepted（零副作用）")
        status, _hd, data = call(self.base, sv.API_PATH_PREFERENCES, headers=self.csrf(b))
        self.assertEqual(200, status, data)
        self.assertEqual([], [p for p in data["preferences"]
                              if p["tool"] == TOOL and p["source"] == "suggestion"],
                         "B 的偏好账本里不许出现 A 采纳出来的那行")
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.csrf(a))
        self.assertEqual(200, status, data)
        self.assertEqual(self.uids["r72edit"], data["preference"]["user_id"])

    def test_readers_cannot_decide_or_flip_the_switch(self) -> None:
        """权限格矩阵：viewer 没有 intervene.decide / settings.write，写侧一律 403 显码。"""
        editor = self.editor()
        sug_id = self.seed(start=12, headers=self.csrf(editor))
        viewer = self.login("r72view")
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.csrf(viewer))
        self.assertEqual(403, status, data)
        self.assert_denied_code(status, data, "ROLE_FORBIDDEN")
        self.assertIn("intervene.decide", json.dumps(data, ensure_ascii=False),
                      "403 必须说清缺哪一格能力")
        status, _hd, data = self.post_preferences({"tool": TOOL, "param": PARAM, "value": 6},
                                                  headers=self.csrf(viewer))
        self.assertEqual(403, status, data)
        self.assert_denied_code(status, data, "ROLE_FORBIDDEN")
        status, _hd, data = self.post_switch({"enabled": False}, headers=self.csrf(viewer))
        self.assertEqual(403, status, data)
        self.assert_denied_code(status, data, "ROLE_FORBIDDEN")
        self.assertTrue(self.service.suggestion_service.enabled,
                        "被拒的开关请求必须零效果")
        status, _hd, data = call(self.base, sv.API_PATH_SUGGESTIONS, headers=self.csrf(viewer))
        self.assertEqual(200, status, "读侧不受影响：viewer 有 records.read，照旧能看清单")

    def test_editor_can_decide_but_not_flip_the_service_switch(self) -> None:
        """开关是服务级：editor 有 intervene.decide（能采纳），没有 settings.write。"""
        editor = self.editor()
        sug_id = self.seed(start=15, headers=self.csrf(editor))
        status, _hd, data = self.post_action(sug_id, "dismiss", {"reason": "team 验收"},
                                             headers=self.csrf(editor))
        self.assertEqual(200, status, data)
        self.assertEqual("team 验收", data["suggestion"]["dismiss_reason"])
        status, _hd, data = self.post_switch({"enabled": False}, headers=self.csrf(editor))
        self.assertEqual(403, status, data)
        self.assert_denied_code(status, data, "ROLE_FORBIDDEN")
        token = bearer(self.admin_token())
        status, _hd, data = self.post_switch({"enabled": False}, headers=token)
        self.assertEqual(200, status, f"admin 的 token 通道必须能拨开关：{status} {data}")
        self.assertFalse(data["enabled"])
        status, _hd, data = call(self.base, sv.API_PATH_SUGGESTIONS, headers=self.csrf(editor))
        self.assertFalse(data["enabled"], "服务级开关：editor 读到的就是 admin 拨下去的真值")
        self.post_switch({"enabled": True}, headers=token)

    def test_denial_writes_nothing_but_one_audit_entry(self) -> None:
        """越权只留一条 auth.denied 取证；建议/偏好/记录账本零增长（collab_guard 同口径）。"""
        editor = self.editor()
        sug_id = self.seed(start=18, headers=self.csrf(editor))
        viewer = self.login("r72view")
        before = self.ledger()
        status, _hd, data = self.post_action(sug_id, "accept", headers=self.csrf(viewer))
        self.assertEqual(403, status, data)
        after = self.ledger()
        self.assertEqual(1, after["denied"] - before["denied"], "拒绝要留痕，且只留一条")
        self.assertEqual(before["suggestions"], after["suggestions"])
        self.assertEqual(before["preferences"], after["preferences"])
        self.assertEqual(before["records"], after["records"])


class SuggestionMountContractDriftTests(unittest.TestCase):
    """纯读盘：契约与 docs/03 §6.5 双侧登记（挂载口径变化如实登记，不留 verb_drift）。"""

    def test_docs_03_6_5_carries_the_new_post_verbs(self) -> None:
        cross = CONTRACT["doc_crosscheck"]
        self.assertEqual([], cross["contract_only_paths"], "契约里有 docs/03 没写的路径")
        self.assertEqual([], cross["doc_only_paths"], "docs/03 写了契约里没有的路径")
        self.assertEqual([], cross["verb_drift"],
                         "挂载新增的 POST verb 必须在 docs/03 §6.5 的方法列登记")
        self.assertEqual([], cross["rows_not_aligned"])
        self.assertTrue(cross["in_sync"], cross)

    def test_the_r7_1_baseline_pin_tests_register_the_changed_state(self) -> None:
        """R7-1 的钉测改写必须登记「口径已变」，不许把断言删掉就当绿。"""
        text = (ROOT / "tests" / "test_suggestions_wiring.py").read_text(encoding="utf-8")
        self.assertIn("R7-2", text, "挂载后 R7-1 的钉测要登记口径变化")
        self.assertIn("SUGGESTION_NOT_FOUND", text,
                      "原来钉 ROUTE_NOT_FOUND 的用例要改成挂上后的真实现状")
        self.assertNotIn('self.assertEqual("ROUTE_NOT_FOUND", (data or {}).get("code"))',
                         text, "「写侧未挂载」的旧断言必须退役，不许留双重真相")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
