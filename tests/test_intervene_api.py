"""t8 [t4-1] 验收测试：`POST /intervene` 干预闭环（server.py + 界面联调的服务端侧）。

运行（全程离线，只用标准库 unittest；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_intervene_api.py

覆盖任务验收点：
  1. 三种 decision（approve / modify / reject）各自的状态流转与写回的字段；
  2. 非法参数 → 400 + 可读错误（统一错误体 {code, message, detail, request_id}）；
  3. 返回更新后的 ActionRecord，且过 schema v1.1、事实指纹可复算；
  4. 决定 ≠ 生效（裁决 Q2）：`execution_receipt` 不被 `/intervene` 改写；
  5. 幂等（02 Q6）、审计哈希链、并发只允许一次决定；
  6. 静态服务与离线口径（file:// 打开用本地模拟，页面无需后端）；
  7. 服务只用标准库、不出网、日志与审计不含参数明文；
  8. 干预写回即重签（D-resign / R1-2b）：被干预的目标记录在 verify 里仍 ``ok=true``，
     幂等重放绝不重签（``InterveneResignOnWriteTests``，产品口径的签名服务器）。
"""
from __future__ import annotations

import ast
import copy
import http.client
import io
import json
import pathlib
import re
import shutil
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import server as sv  # noqa: E402
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试
import facts_hash  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402
from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json").read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
WEB = ROOT / "agent_explainer" / "web"
SAMPLE_PATH = ROOT / "samples" / "action_records.json"
NATIVE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"]

PENDING: tuple[str, ...] = ("act-demo-0004", "act-demo-0005", "act-demo-0008")
EXPIRED = "act-demo-0011"
DECIDED = {"act-demo-0007": "modify", "act-demo-0010": "reject"}
NON_DECIDABLE = "act-demo-0002"
MODIFY_FIELDS = {"max_delete_count": 3, "move_to_trash": True}
HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
FIXED_AT = "2026-11-10T08:00:00Z"


def fixture(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in NATIVE if r["id"] == action_id))


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def http_request(host: str, port: int, method: str, path: str, body=None,
                 content_type: str = "application/json", extra_headers: dict | None = None,
                 timeout: float = 8.0) -> tuple[int, dict, object]:
    """一发一收的 HTTP 调用（每次新建连接，避免 keep-alive 干扰）。"""
    # M2-PRE 4（PM-VERDICT-R1-9 裁决② / R5-1 接线）：连接生命周期交给 tests/http_fixture——
    # 端口就绪等待 + 连接级瞬态一次性重试；POST/PUT/PATCH/DELETE 这类非幂等动词绝不重放
    # （服务端可能已写入），断言级失败绝不重试。请求构造与响应解析逐行不动，返回形状一字不改。
    def once(conn: http.client.HTTPConnection) -> tuple[int, dict, object]:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        headers = {"Connection": "close"}
        if payload is not None and content_type:
            headers["Content-Type"] = content_type
        headers.update(extra_headers or {})
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        data: object = raw
        if "json" in (resp.getheader("Content-Type") or ""):
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = raw.decode("utf-8", "replace")
        elif raw:
            data = raw.decode("utf-8", "replace")
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


class ServerTestCase(unittest.TestCase):
    """起一台随机端口的本机服务器；每个用例前重置演示存储。"""

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

    def setUp(self) -> None:
        status, _, body = self.call("POST", "/api/reset")
        self.assertEqual(status, 200, body)
        self.audit_before = len(self.server.service.audit.entries())

    def call(self, method: str, path: str, body=None, **kwargs) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, method, path, body, **kwargs)

    def post_intervene(self, body, **kwargs) -> tuple[int, dict, object]:
        return self.call("POST", "/intervene", body, **kwargs)

    def record(self, action_id: str) -> dict:
        status, _, data = self.call("GET", f"/api/records/{action_id}")
        self.assertEqual(status, 200, data)
        return data  # type: ignore[return-value]

    def assert_error(self, status: int, headers: dict, data: object, code: str,
                     min_status: int = 400, field: str | None = None) -> dict:
        self.assertIsInstance(data, dict, f"错误体必须是对象，收到 {data!r}")
        body = data  # type: ignore[assignment]
        self.assertEqual(status, min_status, body)
        self.assertEqual(sorted(body.keys()), ["code", "detail", "message", "request_id"],
                         f"统一错误体字段不符：{body}")
        self.assertEqual(body["code"], code, body)
        self.assertIsInstance(body["message"], str)
        self.assertGreaterEqual(len(body["message"].strip()), 10, "错误信息必须可读")
        self.assertIsInstance(body["detail"], dict)
        if field:
            self.assertEqual(body["detail"].get("field"), field, body)
        self.assertTrue(str(body["request_id"]).startswith("req-"), body)
        self.assertEqual(headers.get("x-request-id"), body["request_id"])
        return body


# --------------------------------------------------------------------------- #
# 1. 三种 decision 的流转
# --------------------------------------------------------------------------- #

class DecisionTests(ServerTestCase):
    """任务验收：三种 decision 各自的状态流转 + 返回更新后的 ActionRecord。"""

    def test_approve_moves_pending_to_approved(self) -> None:
        status, headers, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertEqual(status, 200, data)
        rec = data  # type: ignore[assignment]
        self.assertEqual(rec["id"], PENDING[0], "返回体就是那条 ActionRecord")
        self.assertEqual(rec["status"], "approved")
        self.assertFalse(rec["requires_action"], "C1：有决定时不得再要求操作")
        ui = rec["user_intervention"]
        self.assertEqual(ui["decision"], "approve")
        self.assertIs(ui["recorded"], True, "recorded 恒为 true：决定已记录")
        self.assertEqual(ui["decided_by"], sv.LOCAL_ACTOR_ID)
        self.assertEqual(ui["decided_by_role"], "local")
        self.assertEqual(ui["option_id"], "opt-demo-0004-approve")
        self.assertTrue(HASH_RE.match(ui["audit_entry_hash"] or ""), ui.get("audit_entry_hash"))
        self.assertEqual(headers.get("x-intervene-status"), "approved")
        self.assertEqual(headers.get("x-intervene-replay"), "false")
        self.assertEqual(self.record(PENDING[0])["status"], "approved", "存储必须真的更新")

    def test_modify_records_edited_params(self) -> None:
        _, _, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                          "modified_params": MODIFY_FIELDS})
        rec = data  # type: ignore[assignment]
        self.assertEqual(rec["status"], "modified")
        ui = rec["user_intervention"]
        self.assertEqual(ui["decision"], "modify")
        self.assertEqual(ui["modified_params"], {"max_delete_count": 3, "move_to_trash": True})
        self.assertEqual(ui["original_params"]["count"], 12, "params_summary 解析出的干预前参数")
        changed = {f["name"]: (f["before"], f["after"]) for f in ui["modified_fields"]}
        self.assertEqual(changed, {"max_delete_count": (12, 3), "move_to_trash": (False, True)},
                         "逐字段核对视图（C 类用户诉求）")
        self.assertEqual(self.record(PENDING[1])["status"], "modified")

    def test_modify_accepts_string_numbers_and_coerces(self) -> None:
        """表单送来的是字符串，服务端按 editable_fields 的 type 换算。"""
        _, _, data = self.post_intervene({"action_id": PENDING[0], "decision": "modify",
                                          "modified_params": {"max_move_count": "12"}})
        ui = data["user_intervention"]  # type: ignore[index]
        self.assertEqual(ui["modified_params"], {"max_move_count": 12})
        self.assertEqual(ui["modified_fields"][0]["after"], 12)

    def test_reject_requires_and_stores_reason(self) -> None:
        _, _, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                                          "reason": "发送前没让我确认收件人"})
        rec = data  # type: ignore[assignment]
        self.assertEqual(rec["status"], "rejected")
        self.assertEqual(rec["user_intervention"]["decision"], "reject")
        self.assertEqual(rec["user_intervention"]["reason"], "发送前没让我确认收件人")

    def test_expired_record_can_still_be_decided(self) -> None:
        """超时只是提醒（W-02 禁止的是自动同意，不是用户决定）。"""
        self.assertEqual(fixture(EXPIRED)["status"], "expired")
        _, _, data = self.post_intervene({"action_id": EXPIRED, "decision": "approve"})
        self.assertEqual(data["status"], "approved", data)

    def test_returned_record_passes_schema_v11(self) -> None:
        cases = [
            {"action_id": PENDING[0], "decision": "approve"},
            {"action_id": PENDING[1], "decision": "modify", "modified_params": MODIFY_FIELDS},
            {"action_id": PENDING[2], "decision": "reject", "reason": "不该直接对外发送"},
            {"action_id": EXPIRED, "decision": "reject", "reason": "太晚了，先不追"},
        ]
        for body in cases:
            status, _, data = self.post_intervene(body)
            self.assertEqual(status, 200, data)
            self.assertEqual(schema_errors(data), [], f"{body['decision']} 的记录过不了 schema")
            ok, computed, expected = facts_hash.verify(data)
            self.assertTrue(ok, f"事实指纹不可复算：{computed} != {expected}")
            self.assertEqual(data["schema_version"], "1.1")
            allowed = {"decision", "option_id", "decided_by", "decided_by_role", "decided_at",
                       "original_params", "modified_params", "modified_fields", "reason",
                       "request_id", "audit_entry_hash", "recorded"}
            self.assertTrue(set(data["user_intervention"]).issubset(allowed),
                            sorted(data["user_intervention"]))

    def test_intervention_options_kept_for_audit(self) -> None:
        """决定后选项数组仍是原始事实（不伪造选项）。"""
        _, _, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        kinds = [o["kind"] for o in data["intervention_options"]]
        self.assertEqual(kinds, ["approve", "modify", "reject"])

    def test_all_three_decisions_cover_every_pending_record(self) -> None:
        plan = [
            {"action_id": PENDING[0], "decision": "approve"},
            {"action_id": PENDING[1], "decision": "modify", "modified_params": MODIFY_FIELDS},
            {"action_id": PENDING[2], "decision": "reject", "reason": "越权风险，先让我看"},
            {"action_id": EXPIRED, "decision": "approve"},
        ]
        for body in plan:
            status, _, data = self.post_intervene(body)
            self.assertEqual(status, 200, data)
            self.assertEqual(data["status"], sv.STATUS_BY_DECISION[body["decision"]])


# --------------------------------------------------------------------------- #
# 2. 决定 ≠ 生效（裁决 Q2 / 假闭环禁令）
# --------------------------------------------------------------------------- #

class HonestyTests(ServerTestCase):
    """`/intervene` 只记录决定；执行端没回执时不得写"已执行/已停止"。"""

    def test_receipt_untouched_and_effect_notice_set(self) -> None:
        before = fixture(PENDING[1])["execution_receipt"]
        _, _, data = self.post_intervene({"action_id": PENDING[1], "decision": "approve"})
        receipt = data["execution_receipt"]  # type: ignore[index]
        self.assertEqual(receipt["state"], "unreported", "决定不能伪装成执行结果")
        self.assertIsNone(receipt["reported_at"], "没有回执时间的 executed 就是假闭环")
        self.assertEqual(receipt["reported_by"], before["reported_by"])
        self.assertEqual(receipt["effect_notice"], sv.EFFECT_NOTICE_KEY)

    def test_no_forced_stop_wording_in_updated_record(self) -> None:
        banned = ("已强制执行", "已停止 AI", "已停止AI", "强制停止", "已执行完成", "已自动同意")
        for action_id, decision in ((PENDING[0], "approve"), (PENDING[1], "modify"),
                                    (PENDING[2], "reject")):
            body = {"action_id": action_id, "decision": decision}
            if decision == "modify":
                body["modified_params"] = MODIFY_FIELDS
            if decision == "reject":
                body["reason"] = "先不要对外发送"
            _, _, data = self.post_intervene(body)
            blob = json.dumps(data, ensure_ascii=False)
            for word in banned:
                self.assertNotIn(word, blob, f"{decision} 的结果里出现了禁止文案 {word}")

    def test_human_readable_recomputed_for_new_status(self) -> None:
        before = fixture(PENDING[0])
        _, _, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertNotEqual(data["human_readable"]["source_facts_hash"],  # type: ignore[index]
                            before["human_readable"]["source_facts_hash"],
                            "状态变了就必须重算指纹（02 §3.6）")
        self.assertEqual(data["human_readable"]["language"], before["human_readable"]["language"],
                         "界面语言切换靠双语字段，不许被服务端改掉")
        self.assertEqual(data["human_readable"]["generated_by"], "template")
        self.assertFalse(data["human_readable"]["may_contain_inference"])

    def test_status_wording_matches_decision_not_effect(self) -> None:
        _, _, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertIn("已同意", data["human_readable"]["l1"])  # type: ignore[index]
        _, _, data2 = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                           "modified_params": MODIFY_FIELDS})
        self.assertIn("改后已同意", data2["human_readable"]["l1"])  # type: ignore[index]


# --------------------------------------------------------------------------- #
# 3. 非法参数：400 + 可读错误
# --------------------------------------------------------------------------- #

class InvalidRequestTests(ServerTestCase):
    """非法参数 → 400（冲突/未找到给 409/404），统一错误体，信息可读。"""

    def test_missing_action_id(self) -> None:
        st, hd, data = self.post_intervene({"decision": "approve"})
        self.assert_error(st, hd, data, "INTERVENE_FIELD_MISSING", field="action_id")

    def test_missing_decision(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0]})
        self.assert_error(st, hd, data, "INTERVENE_FIELD_MISSING")

    def test_decision_not_in_enum(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "yes_please"})
        body = self.assert_error(st, hd, data, "INTERVENE_DECISION_INVALID")
        self.assertEqual(body["detail"]["allowed"], ["approve", "modify", "reject"])

    def test_action_id_wrong_type(self) -> None:
        st, hd, data = self.post_intervene({"action_id": 12345, "decision": "approve"})
        self.assert_error(st, hd, data, "INTERVENE_FIELD_TYPE")

    def test_action_id_too_long(self) -> None:
        st, hd, data = self.post_intervene({"action_id": "x" * 300, "decision": "approve"})
        self.assert_error(st, hd, data, "INTERVENE_FIELD_TYPE")

    def test_body_is_not_object(self) -> None:
        st, hd, data = self.post_intervene(["approve"])
        self.assert_error(st, hd, data, "INTERVENE_BODY_NOT_OBJECT")

    def test_body_is_not_json(self) -> None:
        st, hd, data = self.post_intervene("{not json")
        self.assert_error(st, hd, data, "INTERVENE_BODY_INVALID")

    def test_empty_body(self) -> None:
        st, hd, data = self.post_intervene(None)
        self.assert_error(st, hd, data, "INTERVENE_BODY_NOT_OBJECT")

    def test_unknown_field_rejected_identity_is_server_side(self) -> None:
        """身份由服务端写（4.3 换成会话身份），客户端自称 actor_id 必须被拒。"""
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve",
                                            "actor_id": "admin"})
        body = self.assert_error(st, hd, data, "INTERVENE_FIELD_UNKNOWN")
        self.assertIn("actor_id", body["detail"]["field"])
        self.assertEqual(self.record(PENDING[0])["status"], "pending_confirmation",
                         "被拒的请求绝不能改状态")

    def test_reject_without_reason(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject"})
        self.assert_error(st, hd, data, "INTERVENE_REASON_REQUIRED")

    def test_reject_reason_too_short(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                                            "reason": "x"})
        self.assert_error(st, hd, data, "INTERVENE_REASON_TOO_SHORT")

    def test_reject_reason_too_long(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                                            "reason": "长" * 600})
        self.assert_error(st, hd, data, "INTERVENE_FIELD_TYPE")

    def test_modify_without_params(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify"})
        body = self.assert_error(st, hd, data, "INTERVENE_PARAM_MISSING")
        self.assertEqual(sorted(body["detail"]["editable_fields"]),
                         ["max_delete_count", "move_to_trash"])

    def test_modify_empty_required_param(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                            "modified_params": {"max_delete_count": "  ",
                                                                "move_to_trash": True}})
        self.assert_error(st, hd, data, "INTERVENE_PARAM_EMPTY")

    def test_modify_unknown_field(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                            "modified_params": {"rm_rf_root": True}})
        body = self.assert_error(st, hd, data, "INTERVENE_PARAM_UNKNOWN")
        self.assertEqual(body["detail"]["field"], "rm_rf_root")

    def test_modify_wrong_type(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                            "modified_params": {"max_delete_count": "many"}})
        self.assert_error(st, hd, data, "INTERVENE_PARAM_TYPE")

    def test_modify_out_of_range(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                            "modified_params": {"max_delete_count": 999}})
        body = self.assert_error(st, hd, data, "INTERVENE_PARAM_RANGE")
        self.assertEqual(body["detail"]["max"], 12)

    def test_modify_without_real_change(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                            "modified_params": {"max_delete_count": 12,
                                                                "move_to_trash": False}})
        self.assert_error(st, hd, data, "INTERVENE_PARAM_NO_CHANGE")

    def test_params_not_allowed_for_approve(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve",
                                            "modified_params": {"max_move_count": 3}})
        self.assert_error(st, hd, data, "INTERVENE_PARAM_NOT_ALLOWED")

    def test_params_not_allowed_for_reject(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                                            "reason": "别发", "modified_params": {"x": 1}})
        self.assert_error(st, hd, data, "INTERVENE_PARAM_NOT_ALLOWED")

    def test_option_id_kind_mismatch(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve",
                                            "option_id": "opt-demo-0004-reject"})
        self.assert_error(st, hd, data, "INTERVENE_OPTION_MISMATCH")

    def test_option_id_not_in_record(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve",
                                            "option_id": "opt-demo-0008-approve"})
        self.assert_error(st, hd, data, "INTERVENE_OPTION_MISMATCH")

    def test_unknown_action_id_is_404(self) -> None:
        st, hd, data = self.post_intervene({"action_id": "act-nope-9999", "decision": "approve"})
        self.assert_error(st, hd, data, "INTERVENE_ACTION_NOT_FOUND", min_status=404)

    def test_already_decided_is_409(self) -> None:
        ok, _, _ = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertEqual(ok, 200)
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "reject",
                                            "reason": "改主意了"})
        body = self.assert_error(st, hd, data, "INTERVENE_ALREADY_DECIDED", min_status=409)
        self.assertEqual(body["detail"]["status"], "approved")
        self.assertEqual(self.record(PENDING[0])["status"], "approved", "重复决定不得覆盖第一次")

    def test_fixture_record_already_decided_is_409(self) -> None:
        for action_id, decision in DECIDED.items():
            st, hd, data = self.post_intervene({"action_id": action_id, "decision": "approve"})
            self.assert_error(st, hd, data, "INTERVENE_ALREADY_DECIDED", min_status=409)

    def test_record_not_waiting_for_decision_is_409(self) -> None:
        st, hd, data = self.post_intervene({"action_id": NON_DECIDABLE, "decision": "approve"})
        body = self.assert_error(st, hd, data, "INTERVENE_STATE_INVALID", min_status=409)
        self.assertIn("pending_confirmation", body["detail"]["decidable"])

    def test_wrong_content_type_is_415(self) -> None:
        st, hd, data = self.post_intervene(json.dumps({"action_id": PENDING[0],
                                                       "decision": "approve"}),
                                           content_type="text/plain")
        self.assert_error(st, hd, data, "INTERVENE_CONTENT_TYPE", min_status=415)

    def test_body_size_guard(self) -> None:
        """64 KB 上限：直接测读体守卫（避免大 body 造成 flaky 的 socket 行为）。"""
        handler = object.__new__(sv.TraceLensHandler)
        handler.headers = {"Content-Length": str(sv.MAX_BODY_BYTES + 1),
                           "Content-Type": "application/json"}
        handler.rfile = io.BytesIO(b"x" * 16)
        with self.assertRaises(sv.InterveneError) as ctx:
            sv.TraceLensHandler._read_body(handler)
        self.assertEqual(ctx.exception.code, "INTERVENE_BODY_TOO_LARGE")
        self.assertEqual(ctx.exception.http_status, 413)

    def test_method_not_allowed(self) -> None:
        for method, path in (("PUT", "/intervene"), ("DELETE", "/intervene"),
                             ("OPTIONS", "/intervene"), ("POST", "/health")):
            st, hd, data = self.call(method, path, {} if method in ("POST", "PUT", "DELETE") else None)
            self.assert_error(st, hd, data, "METHOD_NOT_ALLOWED", min_status=405)

    def test_unknown_post_route_is_404(self) -> None:
        st, hd, data = self.call("POST", "/api/nope", {"action_id": PENDING[0]})
        self.assert_error(st, hd, data, "ROUTE_NOT_FOUND", min_status=404)

    def test_error_bodies_echo_input_but_leak_nothing_internal(self) -> None:
        """400 可以把用户自己填错的值回显给他（表单要显示"这个值不是数字"），
        但绝不能带内部信息：栈、文件路径、别条记录的数据、密钥样式。"""
        marker = "quanzhen-unique-token-8888"
        st, _, data = self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                                           "modified_params": {"max_delete_count": marker}})
        self.assertGreaterEqual(st, 400)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertIn(marker, blob, "回显用户自己的输入，界面才能就地报错")
        for internal in ("Traceback", "File \"", ".py", ".json", "action_records",
                         "act-demo-0004", "sk-"):
            self.assertNotIn(internal, blob, f"错误响应泄露了内部信息：{internal}")

    def test_reason_and_params_never_reach_audit_or_access_log(self) -> None:
        secret = "liu-ming-zhao-7788"
        self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                             "modified_params": {"max_delete_count": 5, "move_to_trash": True}})
        self.post_intervene({"action_id": PENDING[2], "decision": "reject", "reason": secret})
        self.assertIn(secret, json.dumps(self.record(PENDING[2])["user_intervention"],
                                         ensure_ascii=False), "理由本身要存在记录里（可核对）")
        self.assertNotIn(secret, json.dumps(self.server.service.audit.entries(), ensure_ascii=False),
                         "审计只记决定与字段名（02 隐私条款）")
        self.assertNotIn(secret, json.dumps(self.server.access_log, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 4. 端点契约（界面联调要用的形状）
# --------------------------------------------------------------------------- #

class EndpointContractTests(ServerTestCase):

    def test_health_reports_local_mode_and_capabilities(self) -> None:
        st, hd, data = self.call("GET", "/health")
        self.assertEqual(st, 200, data)
        self.assertEqual(data["mode"], sv.MODE_LOCAL)
        self.assertEqual(data["schema_version"], "1.1")
        self.assertIn("/intervene", data["endpoints"])
        self.assertIn("decide", data["capabilities"])
        self.assertIs(data["auth"]["required"], False, "鉴权在 4.3 接入")
        self.assertEqual(data["stats"]["count"], len(NATIVE))
        self.assertTrue(data["audit"]["ok"], "链在空状态也必须自证完整")
        self.assertEqual(hd.get("content-type", "").split(";")[0], "application/json")

    def test_records_endpoint_returns_the_same_records_as_the_fixture(self) -> None:
        st, _, data = self.call("GET", "/api/records")
        self.assertEqual(st, 200)
        self.assertEqual(len(data["records"]), len(NATIVE))
        self.assertEqual([r["id"] for r in data["records"]], [r["id"] for r in NATIVE])
        self.assertIs(data["meta"]["synthetic"], True, "演示数据来源必须写清（A2）")

    def test_record_endpoint_and_404_shape(self) -> None:
        st, _, data = self.call("GET", f"/api/records/{PENDING[0]}")
        self.assertEqual(st, 200)
        self.assertEqual(data["id"], PENDING[0])
        st2, hd2, data2 = self.call("GET", "/api/records/act-nope-9999")
        self.assert_error(st2, hd2, data2, "INTERVENE_ACTION_NOT_FOUND", min_status=404)

    def test_response_headers_carry_request_metadata(self) -> None:
        st, hd, data = self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertEqual(st, 200)
        self.assertTrue(str(hd.get("x-request-id", "")).startswith("req-"))
        self.assertEqual(hd.get("x-intervene-mode"), sv.MODE_LOCAL)
        self.assertTrue(HASH_RE.match(hd.get("x-intervene-audit-hash") or ""))
        self.assertEqual(hd.get("x-content-type-options"), "nosniff")
        self.assertEqual(hd.get("cache-control"), "no-store")

    def test_reset_restores_the_demo_store_for_walkthroughs(self) -> None:
        self.post_intervene({"action_id": PENDING[0], "decision": "approve"})
        self.assertEqual(self.record(PENDING[0])["status"], "approved")
        st, _, data = self.call("POST", "/api/reset")
        self.assertEqual(st, 200, data)
        self.assertEqual(self.record(PENDING[0])["status"], "pending_confirmation")
        self.assertEqual(data["records"], len(NATIVE))

    def test_request_ids_are_unique_per_request(self) -> None:
        seen = set()
        for _ in range(4):
            _, hd, _ = self.call("GET", "/health")
            seen.add(hd.get("x-request-id"))
        self.assertEqual(len(seen), 4, seen)

    def test_ui_contract_shape_needed_by_the_frontend(self) -> None:
        """界面（t7 三区 + t8 联调）依赖的字段缝：状态、决定、回执、生效边界。"""
        _, _, data = self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                                          "reason": "不该对外发送"})
        for key in ("id", "status", "requires_action", "user_intervention", "execution_receipt",
                    "human_readable", "intervention_options"):
            self.assertIn(key, data)
        self.assertEqual(data["execution_receipt"]["effect_notice"], sv.EFFECT_NOTICE_KEY)
        self.assertEqual(data["user_intervention"]["recorded"], True)


# --------------------------------------------------------------------------- #
# 5. 幂等与审计
# --------------------------------------------------------------------------- #

class IdempotencyAndAuditTests(ServerTestCase):

    def test_same_idempotency_key_returns_same_record_once(self) -> None:
        body = {"action_id": PENDING[0], "decision": "approve", "idempotency_key": "idem-1"}
        st1, hd1, d1 = self.post_intervene(body)
        st2, hd2, d2 = self.post_intervene(body)
        self.assertEqual((st1, st2), (200, 200))
        self.assertEqual(d1, d2, "重放必须返回同一条记录")
        self.assertEqual(hd2.get("x-intervene-replay"), "true")
        self.assertEqual(hd1.get("x-intervene-audit-hash"), hd2.get("x-intervene-audit-hash"),
                         "重放不得产生新审计条目")
        entries = self.server.service.audit.entries()
        mine = [e for e in entries[self.audit_before:] if e.get("action_id") == PENDING[0]]
        self.assertEqual(len(mine), 1, [e["event"] for e in mine])

    def test_idempotency_key_reuse_on_other_record_is_409(self) -> None:
        self.post_intervene({"action_id": PENDING[0], "decision": "approve",
                             "idempotency_key": "idem-2"})
        st, hd, data = self.post_intervene({"action_id": PENDING[1], "decision": "approve",
                                            "idempotency_key": "idem-2"})
        self.assert_error(st, hd, data, "INTERVENE_IDEMPOTENCY_MISMATCH", min_status=409)

    def test_audit_entry_shape_and_chain(self) -> None:
        self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                             "modified_params": MODIFY_FIELDS})
        st, _, data = self.call("GET", "/api/audit")
        self.assertEqual(st, 200)
        self.assertTrue(data["chain"]["ok"], data["chain"])
        entries = [e for e in data["entries"] if e["event"] == "intervention.decision"]
        self.assertTrue(entries)
        last = entries[-1]
        self.assertEqual(last["action_id"], PENDING[1])
        self.assertEqual(last["decision"], "modify")
        self.assertEqual(last["mode"], sv.MODE_LOCAL)
        self.assertEqual(last["record_status"], "modified")
        self.assertTrue(HASH_RE.match(last["entry_hash"]))
        self.assertTrue(HASH_RE.match(last["prev_hash"]))
        self.assertEqual(last["changed_fields"], ["max_delete_count", "move_to_trash"])

    def test_chain_links_and_detects_tampering(self) -> None:
        for action_id, decision in ((PENDING[0], "approve"), (PENDING[2], "reject")):
            body = {"action_id": action_id, "decision": decision}
            if decision == "reject":
                body["reason"] = "先不发送"
            self.post_intervene(body)
        chain = self.server.service.audit.verify()
        self.assertTrue(chain["ok"], chain)
        entries = self.server.service.audit.entries()
        self.assertGreaterEqual(len(entries), 2)
        self.assertEqual(entries[0]["prev_hash"], sv.GENESIS_HASH)
        for prev, nxt in zip(entries, entries[1:]):
            self.assertEqual(prev["entry_hash"], nxt["prev_hash"])
        # 篡改一条 → 自检必须失败
        service = sv.InterveneService(records=copy.deepcopy(NATIVE))
        service.audit.append("intervention.decision", action_id="x", decision="approve")
        self.assertTrue(service.audit.verify()["ok"])
        service.audit._entries[0]["decision"] = "reject"
        self.assertFalse(service.audit.verify()["ok"])

    def test_audit_and_access_log_do_not_leak_params(self) -> None:
        marker = "min-gan-zhi-biao-9271"
        self.post_intervene({"action_id": PENDING[1], "decision": "modify",
                             "modified_params": {"max_delete_count": 12, "move_to_trash": True}})
        self.post_intervene({"action_id": PENDING[2], "decision": "reject",
                             "reason": marker})
        audit_blob = json.dumps(self.server.service.audit.entries(), ensure_ascii=False)
        self.assertNotIn(marker, audit_blob, "审计只记决定与字段名，不记理由/参数明文")
        log_blob = json.dumps(self.server.access_log, ensure_ascii=False)
        self.assertNotIn(marker, log_blob)
        self.assertNotIn("modified_params", log_blob)

    def test_concurrent_decisions_allow_exactly_one(self) -> None:
        results = []

        def worker() -> None:
            st, _, data = http_request(self.host, self.port, "POST", "/intervene",
                                       {"action_id": PENDING[0], "decision": "approve"})
            results.append((st, data))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        oks = [r for r in results if r[0] == 200]
        conflicts = [r for r in results if r[0] == 409]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(conflicts), 4, results)
        self.assertEqual(self.record(PENDING[0])["status"], "approved")

    def test_parallel_records_all_decide(self) -> None:
        results = {}

        def worker(action_id: str, decision: str) -> None:
            body = {"action_id": action_id, "decision": decision}
            if decision == "modify":
                body["modified_params"] = MODIFY_FIELDS
            if decision == "reject":
                body["reason"] = "并发测试：先不发送"
            st, _, data = http_request(self.host, self.port, "POST", "/intervene", body)
            results[action_id] = (st, data)

        plan = [(PENDING[0], "approve"), (PENDING[1], "modify"), (PENDING[2], "reject"),
                (EXPIRED, "approve")]
        threads = [threading.Thread(target=worker, args=pair) for pair in plan]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        for action_id, decision in plan:
            st, data = results[action_id]
            self.assertEqual(st, 200, data)
            self.assertEqual(data["status"], sv.STATUS_BY_DECISION[decision])
        self.assertTrue(self.server.service.audit.verify()["ok"])


# --------------------------------------------------------------------------- #
# 6. 静态服务与离线口径
# --------------------------------------------------------------------------- #

    def test_records_endpoints_reflect_a_recorded_decision(self) -> None:
        """界面刷新时要看到服务器已有的决定：/api/records 与单条端点必须读同一份存储。"""
        st, _, data = self.call("POST", "/intervene", {
            "action_id": "act-demo-0004", "decision": "approve", "idempotency_key": "idem-listing-1"})
        self.assertEqual(st, 200, data)
        st, _, listing = self.call("GET", "/api/records")
        self.assertEqual(st, 200)
        recs = {r["id"]: r for r in listing["records"]}
        self.assertEqual(recs["act-demo-0004"]["status"], "approved")
        self.assertFalse(recs["act-demo-0004"]["requires_action"])
        self.assertTrue(recs["act-demo-0004"]["user_intervention"]["recorded"])
        self.assertTrue(recs["act-demo-0005"]["requires_action"], "其它记录不应被这次决定影响")
        st, _, single = self.call("GET", "/api/records/act-demo-0004")
        self.assertEqual(st, 200)
        one = single.get("record") if isinstance(single.get("record"), dict) else single
        self.assertEqual(one["status"], "approved")
        st, _, audit = self.call("GET", "/api/audit")
        self.assertEqual(st, 200)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])
        self.assertGreaterEqual(audit["chain"]["count"], 1)

    def test_meta_says_the_data_is_synthetic(self) -> None:
        """演示数据必须自己声明 synthetic，界面据此打演示徽标。"""
        st, _, listing = self.call("GET", "/api/records")
        self.assertEqual(st, 200)
        self.assertTrue(listing["meta"]["synthetic"])
        self.assertEqual(listing["meta"]["count"], len(listing["records"]))


class StaticAndOfflineTests(ServerTestCase):
    """页面必须能离线打开（file://），服务器只是可选增强；静态服务不得越界。"""

    def test_path_helpers_match_stdlib(self) -> None:
        """自写 request_path / unquote 必须与标准库同义：不用它是要有人验收的。"""
        from urllib import parse as url_parse
        cases = [
            "/", "/index.html", "/i18n/zh.json", "/a b.css",
            "/%2e%2e/secret", "/..%2fsecret", "/%E7%94%BB%E5%86%8C.html",
            "/x%2Fy", "/%ZZ", "/a%20b?next=/intervene#frag", "?q=1", "#frag", "",
            "/%ff%fe", "/%2", "/%%",
        ]
        for raw in cases:
            self.assertEqual(sv.request_path(raw), url_parse.urlsplit(raw).path, raw)
            self.assertEqual(sv.unquote(raw), url_parse.unquote(raw), raw)

    def test_encoded_traversal_is_still_rejected(self) -> None:
        for path in ("/%2e%2e/server.py", "/..%2fserver.py", "/%2e%2e%5cserver.py",
                     "/i18n/../../../server.py", "/%41%42%43"):
            st, _, data = self.call("GET", path)
            snippet = json.dumps(data, ensure_ascii=False)[:120]
            self.assertIn(st, (400, 403, 404), f"{path} -> {st}: {snippet}")
            self.assertNotIn("apply_decision", snippet, path)

    def test_index_served_at_root(self) -> None:
        st, hd, data = self.call("GET", "/")
        self.assertEqual(st, 200)
        self.assertTrue(hd.get("content-type", "").startswith("text/html"))
        self.assertIn("TraceLensUI", data)
        self.assertIn("demo-data.js", data)

    def test_assets_served_with_content_types(self) -> None:
        expected = {"/app.js": "text/javascript", "/styles.css": "text/css",
                    "/i18n/zh.json": "application/json", "/i18n/en.json": "application/json",
                    "/i18n/embed.js": "text/javascript", "/compare-data.js": "text/javascript"}
        for path, ctype in expected.items():
            st, hd, _ = self.call("GET", path)
            self.assertEqual(st, 200, path)
            self.assertTrue(hd.get("content-type", "").startswith(ctype), f"{path} -> {hd.get('content-type')}")

    def test_static_responses_are_never_cached(self) -> None:
        """界面可热更，静态资源就不许落缓存。

        只给 ``Last-Modified`` 时，Chromium 会按启发式新鲜度直接复用磁盘缓存；
        固定端口（``--port``）下 origin 不变，换版本后仍可能把旧界面拿出来显示。
        这一条钉的是"装上了却看不到"的那条路。
        """
        for path in ("/", "/index.html", "/app.js", "/styles.css",
                     "/ext/workspaces.js", "/i18n/embed.js"):
            st, hd, _ = self.call("GET", path)
            self.assertEqual(st, 200, path)
            self.assertEqual("no-store", hd.get("cache-control"),
                             f"{path} 缺 no-store：会落到磁盘缓存")

    def test_missing_file_uses_the_unified_error_body(self) -> None:
        st, hd, data = self.call("GET", "/no-such-file.js")
        self.assert_error(st, hd, data, "STATIC_NOT_FOUND", min_status=404)

    def test_path_traversal_is_rejected(self) -> None:
        for path in ("/../samples/action_records.json", "/..%2f..%2fsamples",
                     "/static/..\\..\\samples\\action_records.json", "/%2e%2e/secret"):
            st, hd, data = self.call("GET", path)
            self.assertIn(st, (400, 404), path)
            if st == 400:
                self.assert_error(st, hd, data, "STATIC_PATH_REJECTED")
            self.assertNotIn("schema_version", json.dumps(data, ensure_ascii=False)[:4000],
                             "绝不能把 web/ 之外的文件吐出去")

    def test_directory_listing_is_off(self) -> None:
        st, hd, data = self.call("GET", "/verify")
        self.assertIn(st, (400, 404), data)
        if st == 400:
            self.assertEqual(data["code"], "STATIC_PATH_REJECTED")

    def test_web_layer_needs_no_backend_for_the_first_paint(self) -> None:
        """file:// 离线打开的数据源是内联 demo-data.js，与 samples 逐字节一致。"""
        text = (WEB / "demo-data.js").read_text(encoding="utf-8")
        marker = "global.TraceLensDemoData = "
        start = text.index(marker) + len(marker)
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
        self.assertIs(payload["_meta"]["synthetic"], True)
        self.assertEqual(payload["records"], NATIVE,
                         "离线演示数据必须等于服务端数据源，否则联调口径会分叉")

    def test_records_payload_unchanged_by_static_requests(self) -> None:
        for path in ("/", "/app.js", "/styles.css", "/i18n/zh.json"):
            self.call("GET", path)
        _, _, data = self.call("GET", "/api/records")
        self.assertEqual([r["status"] for r in data["records"]], [r["status"] for r in NATIVE])


# --------------------------------------------------------------------------- #
# 7. 纯净性：只用标准库、不出网、不碰 web/ 之外的产品文件
# --------------------------------------------------------------------------- #

class PurityTests(unittest.TestCase):
    SOURCE = (ROOT / "agent_explainer" / "server.py").read_text(encoding="utf-8")
    STDLIB_OK = {"__future__", "argparse", "copy", "datetime", "hashlib", "json", "math",
                 "pathlib", "threading", "collections", "collections.abc", "http",
                 "http.server", "http.client", "typing", "io", "re", "sys",
                 "time", "socketserver", "socket", "mimetypes", "secrets",
                 # 4.7：`gen_api_contract.py` 那套 AST 口径要靠 importlib 做延迟导入，
                 # 它是标准库；此前漏登记，让这条纯净性测试一直红着。
                 "importlib",
                 # 4.3（t13）加：本地模式的鉴权状态默认放系统临时目录，仓库里不留签名密钥
                 "tempfile"}

    def test_source_carries_no_network_literals(self) -> None:
        """与 tests/test_trace_adapter.py 的离线纯度扫描同口径：包内不出现
        URL 字面量、URL 解析模块、socket. 或 subprocess。server.py 里
        给操作员看的本机地址是运行时拼装的（LOOPBACK_SCHEME）。"""
        for token in ("http://", "https://", "urllib", "socket.", "subprocess"):
            self.assertNotIn(token, self.SOURCE, f"server.py 含 {token}")

    def test_imports_are_stdlib_or_local_package(self) -> None:
        tree = ast.parse(self.SOURCE)
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:                                   # from .explanations import ...
                    continue
                mods.add(node.module or "")
        for mod in sorted(mods):
            top = mod.split(".")[0]
            # 以解释器自己的标准库清单为准（Python 3.10+ 的 sys.stdlib_module_names），
            # 再叠加显式白名单：白名单是为了记录"为什么允许"，而不是唯一判据。
            # 之前只认手写白名单，导致 `importlib` / `inspect` 这类标准库模块让这条
            # 纯净性闸门长期假红。
            stdlib = getattr(sys, "stdlib_module_names", frozenset())
            self.assertTrue(mod in self.STDLIB_OK or top in self.STDLIB_OK
                            or mod in stdlib or top in stdlib,
                            f"server.py 引入了非标准库依赖：{mod}")
        self.assertNotIn("fastapi", mods)
        self.assertNotIn("uvicorn", mods)

    def test_no_outbound_network_apis(self) -> None:
        banned = ["urlopen", "create_connection", "HTTPConnection(", "HTTPSConnection(",
                  "socket.connect", "requests.", "httpx", "WebSocket", "smtplib",
                  "subprocess", "os.system", "Popen"]
        for token in banned:
            self.assertNotIn(token, self.SOURCE, f"服务端不得主动出网/起进程：{token}")
        self.assertNotIn("openai", self.SOURCE.lower())

    def test_no_secret_literals_and_no_remote_hosts(self) -> None:
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{12,}", self.SOURCE))
        self.assertIsNone(re.search(r"https?://(?!127\.0\.0\.1|localhost)\w+", self.SOURCE),
                          "只允许本机回环地址")
        self.assertNotIn("Authorization:", self.SOURCE)
        self.assertNotIn("api_key", self.SOURCE, "密钥属于 t13 的设置中心，服务端不碰")

    def test_writes_no_files(self) -> None:
        for token in ("write_text", "write_bytes", "open(", ".unlink", "mkdir", "shutil"):
            self.assertNotIn(token, self.SOURCE, "服务端不得写文件（落盘属 t9/t11）")

    def test_samples_are_read_only(self) -> None:
        """跑完所有 HTTP 测试后，夹具文件必须与初始内容一致。"""
        self.assertEqual(json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"], NATIVE)

    def test_pure_apply_decision_does_not_mutate_the_input(self) -> None:
        rec = fixture(PENDING[1])
        snapshot = copy.deepcopy(rec)
        sv.apply_decision(rec, "modify", modified_params=MODIFY_FIELDS, decided_at=FIXED_AT)
        self.assertEqual(rec, snapshot, "纯函数必须返回新记录，绝不就地改写")

    def test_apply_decision_maps_every_decision_to_status(self) -> None:
        mapping = {"approve": "approved", "modify": "modified", "reject": "rejected"}
        self.assertEqual(sv.STATUS_BY_DECISION, mapping)
        self.assertEqual(sv.DECIDABLE_STATUSES, ("pending_confirmation", "expired"))
        for decision, status in mapping.items():
            body = {"action_id": PENDING[0] if decision != "modify" else PENDING[1],
                    "decision": decision}
            if decision == "modify":
                body["modified_params"] = MODIFY_FIELDS
            if decision == "reject":
                body["reason"] = "先不发送"
            new = sv.apply_decision(fixture(body.pop("action_id")), decision,
                                    modified_params=body.get("modified_params"),
                                    reason=body.get("reason"), decided_at=FIXED_AT)
            self.assertEqual(new["status"], status)

    def test_service_level_body_parsing_rules(self) -> None:
        """HTTP 之下的同一套校验（t9 换存储层时规则不分叉）。"""
        service = sv.InterveneService(records=copy.deepcopy(NATIVE))
        with self.assertRaises(sv.InterveneError) as ctx:
            service.intervene(None, request_id="req-x")
        self.assertEqual(ctx.exception.code, "INTERVENE_BODY_NOT_OBJECT")
        with self.assertRaises(sv.InterveneError) as ctx:
            service.intervene(b'{"action_id": "a"}', request_id="req-x")   # 缺 decision
        self.assertEqual(ctx.exception.code, "INTERVENE_FIELD_MISSING")
        with self.assertRaises(sv.InterveneError) as ctx:
            service.intervene("{{{", request_id="req-x")
        self.assertEqual(ctx.exception.code, "INTERVENE_BODY_INVALID")
        ok, meta = service.intervene({"action_id": PENDING[0], "decision": "approve"},
                                      request_id="req-ok")
        self.assertEqual(ok["status"], "approved")
        self.assertEqual(meta["request_id"], "req-ok")
        self.assertFalse(meta["replay"])

    def test_params_summary_parser_is_conservative(self) -> None:
        self.assertEqual(sv.parse_params_summary("a=1；b=true；c=x"),
                         {"a": 1, "b": True, "c": "x"})
        self.assertEqual(sv.parse_params_summary("没有等号"), {})
        self.assertEqual(sv.parse_params_summary(None), {})
        self.assertEqual(sv.parse_params_summary(""), {})


class InterveneResignOnWriteTests(unittest.TestCase):
    """D-resign（PM-VERDICT-R1-2a §3.2，R1-2b 落地）：干预写回必须在授权写回点重签。

    这台服务器是产品口径：持久化 store + 入库即签名（R4）。决定流改了目标记录的
    ``status`` / ``user_intervention`` / ``human_readable`` —— 旧签名盖不住新 body，
    于是被干预的那条记录在 ``/api/secretariat/verify`` 里当场复算失效（P-3 的根因）。
    重签只发生在服务端自己授权的两个写回点（决定流 / 导入合并），幂等重放不重签。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("intervene-resign")
        cls.store_path = cls.root / "records.jsonl"
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(NATIVE), quiet=True,
            records_store_path=cls.store_path,
            features_root=cls.root / "features",
            audit_log_path=cls.root / "audit.jsonl",
            ingest_token_path=cls.root / "ingest.json",
            settings_path=cls.root / "settings.json",
            auth_state_path=cls.root / "auth.json",
            sign_on_write=True)
        cls.host, cls.port = cls.server.server_address[:2]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        shutil.rmtree(cls.root, ignore_errors=True)

    def post(self, path: str, body: dict) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "POST", path, body)

    def stored(self, action_id: str) -> dict:
        status, _hd, data = http_request(self.host, self.port, "GET",
                                         f"/api/records/{action_id}")
        self.assertEqual(200, status, data)
        self.assertIsInstance(data, dict)
        return data

    def verify(self, record: dict) -> dict:
        status, _hd, data = self.post("/api/secretariat/verify", {"record": record})
        self.assertEqual(200, status, data)
        return data["results"][0]

    def test_intervened_target_record_still_verifies(self) -> None:
        """正题：干预之后，被干预的目标记录 ``verify_record.ok = true``。"""
        before = self.stored(PENDING[0])
        baseline = self.verify(before)
        self.assertEqual("absent", baseline["checks"]["signature"],
                         "演示种子入库不签名：这是 P-3 的对照基线，不许假装它一直合规")

        status, headers, data = self.post("/intervene", {"action_id": PENDING[0],
                                                         "decision": "approve",
                                                         "reason": "同意执行"})
        self.assertEqual(200, status, data)
        self.assertEqual("false", headers.get("x-intervene-replay"))

        after = self.stored(PENDING[0])
        self.assertEqual("approved", after["status"])
        self.assertEqual("approve", (after.get("user_intervention") or {}).get("decision"),
                         "user_intervention.decision 记决定动词，status 记流转后的状态")
        first = self.verify(after)
        self.assertTrue(first["ok"], f"干预后的目标记录必须验签通过：{first['problems']}")
        self.assertEqual("valid", first["checks"]["signature"])
        self.assertEqual("valid", first["checks"]["entry_hash"])
        self.assertIn(first["checks"]["prev_hash"], ("continuous", "genesis"),
                      "重签后的 prev_hash 必须接得上责任链（链头或链起点，不能是断点）")
        self.assertEqual([], schema_errors(after), f"重签后的记录必须过 schema v1.1：{schema_errors(after)}")
        self.assertTrue(after.get("signature"), "决定改了 body：签名必须重算，不能留着盖旧事实的签名")
        self.assertEqual(0, self.server.service.sign_failures)
        self.assertGreaterEqual(self.server.service.signed_on_write, 1)

    def test_idempotent_replay_does_not_resign(self) -> None:
        """幂等重放绝不重签：同一次决定只签一次，签名计数不撒谎。"""
        key = "r12b-iv-resign-1"
        status, _hd, first = self.post("/intervene", {"action_id": PENDING[1],
                                                      "decision": "reject",
                                                      "reason": "先不发送",
                                                      "idempotency_key": key})
        self.assertEqual(200, status, first)
        signed_after_first = self.stored(PENDING[1])["signature"]
        counters = (self.server.service.signed_on_write, self.server.service.sign_failures)

        status, headers, replay = self.post("/intervene", {"action_id": PENDING[1],
                                                           "decision": "reject",
                                                           "reason": "先不发送",
                                                           "idempotency_key": key})
        self.assertEqual(200, status, replay)
        self.assertEqual("true", headers.get("x-intervene-replay"))
        self.assertEqual(signed_after_first, self.stored(PENDING[1])["signature"],
                         "重放不得改写记录，也不得产生第二个签名")
        self.assertEqual(counters,
                         (self.server.service.signed_on_write,
                          self.server.service.sign_failures),
                         "重放不得增加签名计数：签名次数 = 真实写回次数")

    def test_unsigned_server_is_unaffected(self) -> None:
        """关签名的 t8 口径（本文件主套件用的那台）：重签是 no-op，不假装签了。"""
        service = sv.InterveneService(records=copy.deepcopy(NATIVE))
        self.assertFalse(service.sign_on_write)
        record = service.store.get(PENDING[2])
        signed = service.sign_on_write_record(record, record)
        self.assertEqual(record.get("signature"), signed.get("signature"),
                         "sign_on_write=False 时一个字都不动（包括不新增签名）")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
