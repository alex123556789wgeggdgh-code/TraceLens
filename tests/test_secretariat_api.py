"""总裁办 T1–T10 接口测试（真发 HTTP，不 mock handler）。

覆盖：状态/身份/委托/签名并落盘/复算/认定书/离线复核/错误路径、族入口与 405 口径、
契约（docs/api_contract.json）与 docs/12 §6 冻结表一致、面板结构（A13/A14）与双语
诚实文案（A15）、team 模式的 401 / 403 ROLE_FORBIDDEN / 403 CSRF_FAILED 矩阵。
数据目录与记录存储都用临时目录，**不写仓库 var/**。
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from agent_explainer import auth as au  # noqa: E402
from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
import gen_api_contract as gac  # noqa: E402
import verify_letter as letter_tool  # noqa: E402

OWNER = cs.did_from_name("owner", kind="user")
CFO = cs.did_from_name("cfo", kind="user")
WEB = ROOT / "agent_explainer" / "web"
PANEL_JS = WEB / "ext" / "secretariat.js"
PANEL_CSS = WEB / "ext" / "secretariat.css"
SAMPLE_RECORD = json.loads((ROOT / "samples" / "action_records.json")
                           .read_text(encoding="utf-8"))["records"][0]
HAN = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]")


def sample_record(record_id: str, **overrides) -> dict:
    """team 模式播种 / 导入用的记录：固定时间戳，避免幂等窗口互相影响。"""
    record = copy.deepcopy(SAMPLE_RECORD)
    record["id"] = record_id
    record.pop("received_at", None)
    record["timestamp"] = "2026-11-12T09:00:00Z"
    record.update(overrides)
    return record


#: docs/12 §6 表 F 冻结的 T1–T10 接口面（路径 → 方法 → 能力 / 是否吃 CSRF）
FROZEN_SURFACE: dict[str, dict[str, tuple[str, bool]]] = {
    "/api/secretariat": {"GET": ("audit.read", False)},
    "/api/secretariat/status": {"GET": ("audit.read", False)},
    "/api/secretariat/identities": {"GET": ("audit.read", False),
                                    "POST": ("settings.write", True)},
    "/api/secretariat/identities/{id}/revoke": {"POST": ("settings.write", True)},
    "/api/secretariat/delegations": {"GET": ("audit.read", False),
                                     "POST": ("settings.write", True)},
    "/api/secretariat/records/{id}/sign": {"POST": ("settings.write", True)},
    "/api/secretariat/verify": {"POST": ("audit.read", False)},
    "/api/secretariat/letters": {"GET": ("audit.read", False),
                                 "POST": ("settings.write", True)},
    "/api/secretariat/letters/{id}": {"GET": ("audit.read", False)},
    "/api/secretariat/letters/{id}/verify": {"POST": ("audit.read", False)},
    "/api/secretariat/seal": {"POST": ("settings.write", True)},
}
#: 动态路由必须走这四个助手（docs/12 §6 "形状写在助手函数里"）
FAMILY_HELPERS = {
    "/api/secretariat/identities/{id}/revoke": "identity_route",
    "/api/secretariat/records/{id}/sign": "record_sign_route",
    "/api/secretariat/letters/{id}": "letter_route",
    "/api/secretariat/letters/{id}/verify": "letter_verify_route",
}
ZONES = ("status", "identities", "delegations", "letters", "verify")
FORBIDDEN_CLAIMS = ("可信时间戳", "硬件密钥", "已上链", "trusted timestamp",
                    "hardware key", "hardware security module", "blockchain", "anchored")
NEGATIONS = ("不是", "没有", "从未", "不假装", "not", "no ", "never", "without")


def call(base: str, path: str, body: object | None = None, method: str = "GET",
         token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def call_headers(base: str, path: str, body: object | None = None, method: str = "GET",
                 headers: dict | None = None) -> tuple[int, dict]:
    """带 Cookie / CSRF 头的调用（team 模式口径）。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    request = urllib.request.Request(base + path, data=data, method=method, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def flat_leaves(obj: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flat_leaves(value, name))
        else:
            out[name] = value
    return out



class SecretariatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=None, quiet=True,
            records_store_path=pathlib.Path(self._temp.name) / "records.jsonl",
            features_root=pathlib.Path(self._temp.name) / "features",
            audit_log_path=pathlib.Path(self._temp.name) / "audit.jsonl",
            ingest_token_path=pathlib.Path(self._temp.name) / "ingest.json")
        self.base = self.server.url()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    # -- T1/T2/T3 ------------------------------------------------------
    def test_status_and_identity_lifecycle(self) -> None:
        status, payload = call(self.base, "/api/secretariat/status")
        self.assertEqual(200, status)
        self.assertTrue(payload["secretariat"]["enabled"])
        self.assertIn("risk_policy", payload["secretariat"])

        status, created = call(self.base, "/api/secretariat/identities", {
            "agent_id": "agent-api-1", "owner_did": OWNER,
            "image_hash": "sha256:deadbeef", "boot_params": {"argv": ["run"]}}, "POST")
        self.assertEqual(201, status, created)
        identity = created["identity"]
        self.assertEqual(OWNER, identity["owner_did"])
        self.assertEqual(16, len(identity["fingerprint"]))
        self.assertNotIn("seed", json.dumps(identity))

        status, listing = call(self.base, "/api/secretariat/identities")
        self.assertEqual(200, status)
        self.assertEqual(1, listing["count"])

        status, revoked = call(
            self.base, f"/api/secretariat/identities/agent-api-1/revoke",
            {"reason": "test"}, "POST")
        self.assertEqual(200, status, revoked)
        self.assertTrue(revoked["identity"]["revoked_at"])

    def test_identity_validation_errors_are_400_with_code(self) -> None:
        status, payload = call(self.base, "/api/secretariat/identities", {
            "agent_id": "bad agent", "owner_did": OWNER}, "POST")
        self.assertEqual(400, status)
        self.assertEqual("SECRETARIAT_FIELD_INVALID", payload["code"])
        self.assertTrue(payload.get("request_id"))

    # -- T4 ------------------------------------------------------------
    def test_delegation_narrowing(self) -> None:
        status, created = call(self.base, "/api/secretariat/delegations", {
            "delegator_did": OWNER, "delegatee_did": "did:tracelens:agent:api-a1",
            "scope": {"action_types": ["read"], "max_risk": "medium"}}, "POST")
        self.assertEqual(201, status, created)
        self.assertEqual(1, created["delegation"]["depth"])

        status, widened = call(self.base, "/api/secretariat/delegations", {
            "delegator_did": "did:tracelens:agent:api-a1",
            "delegatee_did": "did:tracelens:agent:api-a2",
            "scope": {"action_types": ["read", "write"]},
            "parent_id": created["delegation"]["id"]}, "POST")
        self.assertEqual(409, status)
        self.assertEqual("SECRETARIAT_SCOPE_WIDENED", widened["code"])

    # -- T5/T6 ---------------------------------------------------------
    def test_sign_persists_and_verifies(self) -> None:
        status, record = call(self.base, "/api/records")
        self.assertEqual(200, status)
        action_id = record["records"][0]["id"]
        status, signed = call(self.base, f"/api/secretariat/records/{action_id}/sign",
                              {"risk_level": "low"}, "POST")
        self.assertEqual(200, status, signed)
        self.assertTrue(signed["verification"]["ok"])
        signature = signed["record"]["signature"]
        self.assertTrue(signature)

        status, reread = call(self.base, f"/api/records/{action_id}")
        self.assertEqual(signature, reread["signature"], "签名必须落盘，重读还在")

        status, verified = call(self.base, "/api/secretariat/verify",
                                {"records": [signed["record"]]}, "POST")
        self.assertEqual(200, status)
        self.assertEqual(1, verified["summary"]["ok"])

        status, missing = call(self.base, "/api/secretariat/records/no-such/sign",
                               {}, "POST")
        self.assertEqual(404, status)
        self.assertEqual("SECRETARIAT_RECORD_MISSING", missing["code"])

    # -- T7/T8/T9 + 离线复核 --------------------------------------------
    def test_letter_and_offline_verification(self) -> None:
        status, records = call(self.base, "/api/records")
        action_id = records["records"][0]["id"]
        call(self.base, f"/api/secretariat/records/{action_id}/sign",
             {"risk_level": "low"}, "POST")
        status, created = call(self.base, "/api/secretariat/letters", {
            "subject": "接口测试认定书", "record_ids": [action_id],
            "responsibility": "technical", "note": "api test"}, "POST")
        self.assertEqual(201, status, created)
        letter = created["letter"]
        self.assertTrue(letter["records_root"])
        self.assertTrue(letter["audit_head"], "审计链头必须写进认定书")
        self.assertTrue(created["verification"]["ok"])

        status, fetched = call(self.base, f"/api/secretariat/letters/{letter['id']}")
        self.assertEqual(200, status)
        self.assertEqual(letter["id"], fetched["letter"]["id"])

        status, verified = call(self.base,
                                f"/api/secretariat/letters/{letter['id']}/verify", {}, "POST")
        self.assertEqual(200, status)
        self.assertTrue(verified["ok"])

        offline = letter_tool.verify_letter(letter)
        self.assertTrue(offline["ok"], offline["problems"])

        tampered = json.loads(json.dumps(letter))
        tampered["actions"][0]["timestamp"] = "2020-01-01T00:00:00Z"
        self.assertFalse(letter_tool.verify_letter(tampered)["ok"],
                         "改了签名域内的字段必须离线验不过")

        root_broken = json.loads(json.dumps(letter))
        root_broken["records_root"] = "0" * 64
        self.assertFalse(letter_tool.verify_letter(root_broken)["ok"])

        broken = json.loads(json.dumps(letter))
        broken["actions"][0]["signature"] = cs.b64e(b"\x02" * 64)
        report = letter_tool.verify_letter(broken)
        self.assertFalse(report["ok"])
        self.assertTrue(any(item["code"] == "SECRETARIAT_SIGNATURE_INVALID"
                            for item in report["problems"]))

    def test_letter_missing_record_and_empty_are_400(self) -> None:
        status, payload = call(self.base, "/api/secretariat/letters",
                               {"subject": "x", "record_ids": []}, "POST")
        self.assertEqual(400, status)
        self.assertEqual("SECRETARIAT_LETTER_EMPTY", payload["code"])
        status, payload = call(self.base, "/api/secretariat/letters",
                               {"subject": "x", "record_ids": ["nope"]}, "POST")
        self.assertEqual(404, status)
        self.assertEqual("SECRETARIAT_RECORD_MISSING", payload["code"])

    # -- T10 + 方法/权限口径 --------------------------------------------
    def test_seal_and_method_errors(self) -> None:
        status, records = call(self.base, "/api/records")
        action_id = records["records"][0]["id"]
        status, sealed = call(self.base, "/api/secretariat/seal",
                              {"record_id": action_id, "reason": "api"}, "POST")
        self.assertEqual(200, status, sealed)
        self.assertTrue(sealed["seal"]["signature"])
        self.assertTrue(sealed["verification"]["ok"])

        status, payload = call(self.base, "/api/secretariat/status", {}, "POST")
        self.assertEqual(405, status)
        self.assertEqual("METHOD_NOT_ALLOWED", payload["code"])
        status, payload = call(self.base, "/api/secretariat/verify")
        self.assertEqual(405, status)
        status, payload = call(self.base, "/api/secretariat/seal")
        self.assertEqual(405, status)

    # -- t4 收口：族入口与 405 口径（docs/12 §6 T1）--------------------------
    def test_family_entry_equals_status_and_rejects_post(self) -> None:
        """族入口 `/api/secretariat` 必须等价 T1 status；POST 它要说 405，不能 404。"""
        status, family = call(self.base, "/api/secretariat")
        self.assertEqual(200, status)
        status, status_payload = call(self.base, "/api/secretariat/status")
        self.assertEqual(200, status)
        self.assertEqual(status_payload, family, "族入口与 /status 必须是同一份载荷")
        self.assertIn("risk_policy", family["secretariat"])
        self.assertNotIn("seed", json.dumps(family), "状态响应不能带密钥材料（A4）")

        status, payload = call(self.base, "/api/secretariat", {}, "POST")
        self.assertEqual(405, status, payload)
        self.assertEqual("METHOD_NOT_ALLOWED", payload["code"])
        self.assertEqual({"code", "message", "detail", "request_id"},
                         {"code", "message", "detail", "request_id"} & set(payload))

    # -- t4 收口：T7 列表形状 = 面板所需字段；limit 坏值用本域码 --------------
    def test_letters_list_matches_panel_contract(self) -> None:
        status, records = call(self.base, "/api/records")
        action_id = records["records"][0]["id"]
        call(self.base, f"/api/secretariat/records/{action_id}/sign",
             {"risk_level": "low"}, "POST")
        status, created = call(self.base, "/api/secretariat/letters",
                               {"subject": "列表形状核对", "record_ids": [action_id]}, "POST")
        self.assertEqual(201, status, created)

        status, listing = call(self.base, "/api/secretariat/letters")
        self.assertEqual(200, status)
        self.assertEqual(1, listing["count"])
        self.assertEqual(50, listing["limit"], "不传 limit 时如实回显生效值")
        entry = listing["letters"][0]
        for field in ("id", "subject", "created_at", "responsibility", "records"):
            self.assertIn(field, entry, "面板列表列要用这些字段（web/ext/secretariat.js）")
        self.assertIsInstance(entry["records"], int, "列表是摘要：records 是条数，不是记录正文")
        self.assertNotIn("actions", entry, "列表不得内联动作正文")

        status, capped = call(self.base, "/api/secretariat/letters?limit=99999")
        self.assertEqual(200, status)
        self.assertEqual(500, capped["limit"], "超上限如实截断到 500，不静默放大响应")

    def test_letters_limit_bad_value_uses_secretariat_domain_code(self) -> None:
        """总裁办端点不能外抛审计域码：面板按 `secretariat.error.*` 查文案。"""
        for raw in ("abc", "0", "-3", "1.5"):
            status, payload = call(self.base, f"/api/secretariat/letters?limit={raw}")
            self.assertEqual(400, status, raw)
            self.assertEqual("SECRETARIAT_FIELD_INVALID", payload["code"], raw)
            self.assertEqual("limit", payload["detail"]["field"])
            self.assertEqual({"code", "message", "detail", "request_id"},
                             {"code", "message", "detail", "request_id"} & set(payload))

    # -- t4 收口：所有错误都走统一错误体（docs/03 §6.4）---------------------
    def test_every_secretariat_error_carries_the_unified_body(self) -> None:
        cases = [
            ("/api/secretariat/letters/no-such", None, "GET", 404),
            ("/api/secretariat/letters/no-such/verify", {}, "POST", 404),
            ("/api/secretariat/records/no-such/sign", {}, "POST", 404),
            ("/api/secretariat/identities/no-such/revoke", {}, "POST", 404),
            ("/api/secretariat/identities/agent/revoke/extra", {}, "POST", 404),
            ("/api/secretariat/verify", {"records": "x"}, "POST", 400),
            ("/api/secretariat/seal", {"reason": "no record_id"}, "POST", 400),
            ("/api/secretariat/letters", {"subject": "x", "record_ids": []}, "POST", 400),
            ("/api/secretariat/letters?limit=abc", None, "GET", 400),
            ("/api/secretariat/verify", None, "GET", 405),
            ("/api/secretariat/seal", None, "GET", 405),
            ("/api/secretariat", {}, "POST", 405),
            ("/api/secretariat/status", {}, "POST", 405),
            ("/api/secretariat/identities", {"agent_id": "x"}, "PATCH", 405),
        ]
        for path, body, method, expected in cases:
            status, payload = call(self.base, path, body, method)
            self.assertEqual(expected, status, f"{method} {path} -> {payload}")
            self.assertTrue(str(payload.get("code")), f"{method} {path} 缺 code")
            self.assertTrue(str(payload.get("message")), f"{method} {path} 缺 message")
            self.assertIsInstance(payload.get("detail"), dict, f"{method} {path} 缺 detail")
            self.assertTrue(str(payload.get("request_id")), f"{method} {path} 缺 request_id")

    # -- t4 收口：A11 认定书绑定签发时的审计链头 ----------------------------
    def test_letter_binds_the_live_audit_head(self) -> None:
        status, records = call(self.base, "/api/records")
        action_id = records["records"][0]["id"]
        call(self.base, f"/api/secretariat/records/{action_id}/sign",
             {"risk_level": "low"}, "POST")
        head = str(self.server.service.audit_chain_status()["head"])
        status, created = call(self.base, "/api/secretariat/letters",
                               {"subject": "审计链头绑定", "record_ids": [action_id]}, "POST")
        self.assertEqual(201, status, created)
        letter = created["letter"]
        self.assertTrue(head, "本地模式必须有审计链头")
        self.assertEqual(head, letter["audit_head"], "认定书要绑定签发时的审计链头（A11）")
        self.assertEqual(64, len(letter["records_root"]))
        offline = letter_tool.verify_letter(letter)
        self.assertTrue(offline["ok"], offline["problems"])
        self.assertEqual(head, offline["audit_head"])

    # -- t4 收口：A10 离线 CLI 的退出码口径 --------------------------------
    def test_verify_letter_cli_exit_codes(self) -> None:
        status, records = call(self.base, "/api/records")
        action_id = records["records"][0]["id"]
        call(self.base, f"/api/secretariat/records/{action_id}/sign",
             {"risk_level": "low"}, "POST")
        status, created = call(self.base, "/api/secretariat/letters",
                               {"subject": "离线 CLI 复核", "record_ids": [action_id]}, "POST")
        self.assertEqual(201, status, created)
        letter = created["letter"]
        root = pathlib.Path(self._temp.name)
        good = root / "letter-good.json"
        good.write_text(json.dumps(letter, ensure_ascii=False), encoding="utf-8")
        tampered = json.loads(json.dumps(letter))
        tampered["subject"] = "被改过的主题"
        bad = root / "letter-bad.json"
        bad.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        script = str(ROOT / "tools" / "verify_letter.py")

        human = subprocess.run([sys.executable, script, str(good)], capture_output=True,
                               text=True, encoding="utf-8", timeout=120)
        self.assertEqual(0, human.returncode, human.stdout + human.stderr)
        self.assertIn("PASS", human.stdout)
        self.assertIn("local-tsa", human.stdout, "CLI 要如实说明只有本地时间戳（A15）")

        machine = subprocess.run([sys.executable, script, str(bad), "--json"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(1, machine.returncode, machine.stdout)
        report = json.loads(machine.stdout)
        self.assertFalse(report["ok"])
        self.assertTrue(any(item["code"] == "SECRETARIAT_SIGNATURE_INVALID"
                            for item in report["problems"]), report["problems"])
        self.assertIn("rfc3161_timestamp", report["offline_boundary"]["not_verifiable"])


class SecretariatContractTests(unittest.TestCase):
    """A12：契约文件与 docs/12 §6 冻结表逐条一致，不靠文档自述。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads((ROOT / "docs" / "api_contract.json")
                                  .read_text(encoding="utf-8"))

    def test_contract_matches_frozen_t1_t10_surface(self) -> None:
        rows = [row for row in self.contract["routes"]
                if row["path"].startswith("/api/secretariat")]
        self.assertEqual(14, len(rows), "T1–T10 + 族入口共 14 条 (path, verb)")
        seen: dict[str, set[str]] = {}
        for row in rows:
            seen.setdefault(row["path"], set()).add(row["verb"])
            expected = FROZEN_SURFACE.get(row["path"], {}).get(row["verb"])
            self.assertIsNotNone(expected, f"契约里有冻结表之外的路由：{row['verb']} {row['path']}")
            ability, csrf = expected
            self.assertEqual("permission", row["auth"], f"{row['verb']} {row['path']}")
            self.assertEqual([ability], row["abilities"], f"{row['verb']} {row['path']} 权限漂移")
            self.assertEqual(csrf, row["csrf"], f"{row['verb']} {row['path']} CSRF 口径漂移")
        self.assertEqual({path: set(spec) for path, spec in FROZEN_SURFACE.items()}, seen,
                         "契约与 docs/12 §6 的方法集合必须完全一致")

    def test_dynamic_routes_use_registered_helpers(self) -> None:
        for path, helper in FAMILY_HELPERS.items():
            self.assertIn(helper, gac.ROUTE_HELPERS, f"{helper} 未登记进 ROUTE_HELPERS")
            self.assertEqual(path, gac.ROUTE_HELPERS[helper])
            self.assertEqual({"GET"} if helper == "letter_route" else {"POST"},
                             gac.ROUTE_HELPER_VERBS[helper], f"{helper} 方法口径漂移")
            rows = [row for row in self.contract["routes"] if row["path"] == path]
            self.assertTrue(rows, f"契约缺少 {path}")
            for row in rows:
                self.assertEqual("family", row["match"], f"{path} 不是助手路由")

    def test_read_only_family_endpoints_declare_405(self) -> None:
        for path in ("/api/secretariat", "/api/secretariat/status"):
            self.assertIn(path, self.contract["method_not_allowed"]["POST"],
                          f"{path} 必须显式声明 POST → 405")
        for path in ("/api/secretariat/verify", "/api/secretariat/seal"):
            self.assertIn(path, self.contract["method_not_allowed"]["GET"],
                          f"{path} 必须显式声明 GET → 405")

    def test_docs_03_section_6_5_is_in_sync(self) -> None:
        cross = self.contract["doc_crosscheck"]
        self.assertEqual([], cross["contract_only_paths"], "契约里有 docs/03 §6.5 没写的路径")
        self.assertEqual([], cross["doc_only_paths"], "docs/03 §6.5 写了契约里没有的路径")
        self.assertTrue(cross["in_sync"])
        mine = {path for path in cross["contract_paths"]
                if path.startswith("/api/secretariat")}
        self.assertEqual(set(FROZEN_SURFACE), mine, "docs/03 §6.5 要覆盖 T1–T10 + 族入口")


class SecretariatPanelTests(unittest.TestCase):
    """A13/A14/A15 可自动化部分：面板结构、零硬编码文案、双语与诚实措辞。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PANEL_JS.read_text(encoding="utf-8")
        cls.css = PANEL_CSS.read_text(encoding="utf-8")
        parts = WEB / "i18n" / "parts"
        cls.zh = flat_leaves(json.loads((parts / "secretariat.zh.json")
                                        .read_text(encoding="utf-8")))
        cls.en = flat_leaves(json.loads((parts / "secretariat.en.json")
                                        .read_text(encoding="utf-8")))

    def test_panel_renders_five_zones_in_spec_order(self) -> None:
        positions = []
        for zone in ZONES:
            match = re.search(r"section\(host,\s*'%s'\)" % zone, self.source)
            self.assertIsNotNone(match, f"面板缺少分区 {zone}（A13 要 5 个 [data-zone]）")
            positions.append(match.start())
        self.assertEqual(sorted(positions), positions, "五区渲染顺序必须与 docs/12 §7 一致")
        self.assertIn("dataset: { zone: key }", self.source,
                      "分区要带 data-zone，A13 的 `?probe=1` 才有可核对的锚点")
        self.assertIn("id: 'secretariat'", self.source)
        self.assertIn("permission: 'audit.read'", self.source,
                      "面板注册权限 = audit.read（与 T1 同口径，viewer 可看）")

    def test_panel_uses_single_request_gate_and_gates_writes(self) -> None:
        self.assertIsNone(re.search(r"\bfetch\s*\(", self.source), "面板不许自己拼 fetch")
        self.assertIsNone(re.search(r"\bXMLHttpRequest\b", self.source))
        self.assertGreaterEqual(len(re.findall(r"ctx\.get\(", self.source)), 4)
        self.assertGreaterEqual(len(re.findall(r"ctx\.post\(", self.source)), 4)
        self.assertIn("ctx.can('settings.write')", self.source)
        self.assertIn("disabled: !writable", self.source,
                      "写按钮在缺 settings.write 时必须禁用（服务端仍会 403）")

    def test_panel_and_css_have_zero_cjk(self) -> None:
        """docs/12 A14：`web/ext/secretariat.js` 零 CJK（注释也算），文案只来自字典。"""
        self.assertEqual([], sorted({c for c in self.source if HAN.match(c)}),
                         "面板源码里不能有汉字")
        self.assertEqual([], sorted({c for c in self.css if HAN.match(c)}),
                         "面板样式里不能有汉字")

    def test_panel_copy_keys_exist_in_both_dictionaries(self) -> None:
        referenced = {key for key in re.findall(r"'(secretariat\.[A-Za-z0-9_.]+)'", self.source)
                      if not key.endswith(".")}
        self.assertGreaterEqual(len(referenced), 40, "静态文案键数量异常，检查抽取口径")
        missing = sorted(key for key in referenced
                         if key not in self.zh or key not in self.en)
        self.assertEqual([], missing, "面板引用的键必须在中英文典里都存在")
        for zone in ZONES:
            self.assertIn(f"secretariat.{zone}.title", self.zh)
            self.assertIn(f"secretariat.{zone}.title", self.en)

    def test_dictionaries_have_bilingual_parity(self) -> None:
        self.assertEqual(sorted(self.zh), sorted(self.en), "中英文典键必须一一对应")
        self.assertTrue(any(HAN.search(str(value)) for value in self.zh.values()),
                        "中文典要有中文（i18n_merge 的双语口径）")
        self.assertEqual([], [key for key, value in self.en.items() if HAN.search(str(value))],
                         "英文典里不能残留汉字")

    def test_honesty_copy_claims_nothing_it_cannot_prove(self) -> None:
        """A15：可以说"不是可信时间戳"，不可以宣称有它。"""
        self.assertIn("secretariat.honesty.title", self.zh)
        self.assertIn("secretariat.honesty.title", self.en)
        for index in range(1, 6):
            key = f"secretariat.honesty.p{index}"
            self.assertIn(key, self.zh, f"缺少诚实边界条目 {key}")
            self.assertIn(key, self.en, f"缺少英文诚实边界条目 {key}")
        for leaf, value in list(self.zh.items()) + list(self.en.items()):
            text = str(value)
            for phrase in FORBIDDEN_CLAIMS:
                if phrase.lower() in text.lower():
                    self.assertTrue(any(marker in text.lower() for marker in NEGATIONS),
                                    f"{leaf} 宣称了「{phrase}」，违反 A15：{text}")
        self.assertIn("local-tsa", str(self.zh["secretariat.honesty.p2"]))
        self.assertIn("local_only", str(self.en["secretariat.honesty.p2"]))


class SecretariatTeamModeTests(unittest.TestCase):
    """team 模式：读要 audit.read，写要 settings.write + CSRF，入库签名如实关闭。"""

    VIEWER_PASSWORD = "R4-View-Serial-91"

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = pathlib.Path(self._temp.name)
        (root / "auth").mkdir(parents=True, exist_ok=True)
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy([sample_record("act-team-seed")]),
            quiet=True, mode="team",
            auth_state_path=root / "auth" / "auth-state.json",
            bootstrap_admin=True, session_ttl=3600,
            records_store_path=root / "records.jsonl",
            features_root=root / "features",
            audit_log_path=root / "audit.jsonl",
            ingest_token_path=root / "ingest.json",
            web_dir=WEB, sign_on_write=True)
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        bootstrap = self.server.service.auth.bootstrap_report or {}
        self.admin = self._login("admin", str(bootstrap["password"]))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def _login(self, username: str, password: str) -> dict:
        status, body = call_headers(self.base, "/api/auth/login",
                                    {"username": username, "password": password}, "POST")
        self.assertEqual(200, status, body)
        return {"cookie": f"{au.SESSION_COOKIE}={body['cookie']['value']}",
                "csrf": str(body["csrf_token"])}

    def test_team_mode_enforces_auth_and_csrf(self) -> None:
        status, payload = call(self.base, "/api/secretariat/status")
        self.assertEqual(401, status, "team 模式未登录必须 401")
        self.assertEqual("AUTH_REQUIRED", payload["code"])
        self.assertTrue(payload.get("request_id"))
        status, payload = call(self.base, "/api/secretariat/identities",
                               {"agent_id": "anon-a1", "owner_did": OWNER}, "POST")
        self.assertEqual(401, status, payload)

        status, _ = call_headers(self.base, "/api/secretariat/status", None, "GET",
                                 {"Cookie": self.admin["cookie"]})
        self.assertEqual(200, status, "admin 的 audit.read 能读 T1")

        for path, body in (("/api/secretariat/identities",
                            {"agent_id": "team-a1", "owner_did": OWNER}),
                           ("/api/secretariat/seal",
                            {"record_id": "act-team-seed", "reason": "csrf"}),
                           ("/api/secretariat/letters",
                            {"subject": "csrf", "record_ids": ["act-team-seed"]}),
                           ("/api/secretariat/delegations",
                            {"delegator_did": OWNER, "delegatee_did": OWNER,
                             "scope": {"action_types": ["read"]}})):
            status, payload = call_headers(self.base, path, body, "POST",
                                           {"Cookie": self.admin["cookie"]})
            self.assertEqual(403, status, f"{path} 缺 CSRF 头必须 403")
            self.assertEqual("CSRF_FAILED", payload["code"], path)

        status, created = call_headers(self.base, "/api/secretariat/identities",
                                       {"agent_id": "team-a1", "owner_did": OWNER}, "POST",
                                       {"Cookie": self.admin["cookie"],
                                        au.CSRF_HEADER: self.admin["csrf"]})
        self.assertEqual(201, status, created)
        status, listing = call_headers(self.base, "/api/secretariat/letters", None, "GET",
                                       {"Cookie": self.admin["cookie"]})
        self.assertEqual(200, status, "GET 在 Cookie 通道免 CSRF（docs/12 §6）")

    def test_team_mode_viewer_read_only_and_honest_signing(self) -> None:
        self.server.service.auth.create_user("viewer-r4", "viewer", self.VIEWER_PASSWORD,
                                             actor={"user_id": "admin", "role": "admin"})
        viewer = self._login("viewer-r4", self.VIEWER_PASSWORD)
        status, _ = call_headers(self.base, "/api/secretariat/status", None, "GET",
                                 {"Cookie": viewer["cookie"]})
        self.assertEqual(200, status, "viewer 有 audit.read，能看 T1")
        status, payload = call_headers(self.base, "/api/secretariat/identities",
                                       {"agent_id": "viewer-a1", "owner_did": OWNER}, "POST",
                                       {"Cookie": viewer["cookie"],
                                        au.CSRF_HEADER: viewer["csrf"]})
        self.assertEqual(403, status, payload)
        self.assertEqual("ROLE_FORBIDDEN", payload["code"], "写侧要卡在 settings.write")

        status, result = call_headers(self.base, "/api/import",
                                      {"records": [sample_record("act-team-r4")]}, "POST",
                                      {"Cookie": self.admin["cookie"],
                                       au.CSRF_HEADER: self.admin["csrf"]})
        self.assertEqual(200, status, result)
        self.assertFalse(result["signing"]["enabled"],
                         "team 模式 owner→DID 未接，signing.enabled 必须如实 False")
        status, record = call_headers(self.base, "/api/records/act-team-r4", None, "GET",
                                      {"Cookie": self.admin["cookie"]})
        self.assertFalse(record.get("signature"), "team 模式不得偷偷签名")
        status, payload = call_headers(self.base, "/api/secretariat/status", None, "GET",
                                       {"Cookie": self.admin["cookie"]})
        self.assertEqual("team", payload["secretariat"]["mode"], "状态要如实标 mode")


if __name__ == "__main__":
    unittest.main()
