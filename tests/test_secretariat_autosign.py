"""R4 集成测试：入库即签名（导入 / 上报两条写路径）。

产品入口默认开启（`--no-secretariat` 关）；库默认关闭，测试显式打开验证。
断言三件事：记录真的带上可验签的签名、签名记录过 schema、事实更新后会重签。
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"][0]
AGENT = str(SAMPLE["agent_id"])
OWNER = cs.did_from_name("autosign-owner", kind="user")


def sample(record_id: str, **overrides) -> dict:
    record = copy.deepcopy(SAMPLE)
    record["id"] = record_id
    record.pop("received_at", None)
    record["timestamp"] = "2026-11-11T15:00:00Z"
    record.update(overrides)
    return record


def call(base: str, path: str, body: object | None = None, method: str = "GET") -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class AutoSignTests(unittest.TestCase):
    def _serve(self, *, sign_on_write: bool):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        server, thread = sv.start_background_server(
            "127.0.0.1", 0, records=None, quiet=True,
            records_store_path=root / "records.jsonl",
            features_root=root / "features",
            audit_log_path=root / "audit.jsonl",
            ingest_token_path=root / "ingest.json",
            sign_on_write=sign_on_write)
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(3)))
        return server

    def test_import_signs_in_local_mode(self) -> None:
        server = self._serve(sign_on_write=True)
        status, result = call(server.url(), "/api/import",
                              {"records": [sample("act-auto-1")]}, "POST")
        self.assertEqual(200, status, result)
        self.assertEqual(1, result["signing"]["signed"])
        self.assertEqual(0, result["signing"]["failures"])
        status, record = call(server.url(), "/api/records/act-auto-1")
        self.assertEqual(200, status)
        self.assertTrue(record.get("signature"), "入库记录必须带签名")
        self.assertEqual([], VALIDATOR.collect(record), "签名后的记录必须过 schema")
        status, verified = call(server.url(), "/api/secretariat/verify",
                                {"record": record}, "POST")
        self.assertEqual(200, status)
        self.assertTrue(verified["results"][0]["ok"], verified["results"][0]["problems"])

    def test_import_update_resigns_changed_facts(self) -> None:
        server = self._serve(sign_on_write=True)
        first = sample("act-auto-2")
        call(server.url(), "/api/import", {"records": [first]}, "POST")
        status, before = call(server.url(), "/api/records/act-auto-2")
        changed = sample("act-auto-2",
                         intent="改过之后：整理相册并生成说明",
                         result_summary="改过之后：200 个文件已跳过")
        status, result = call(server.url(), "/api/import", {"records": [changed]}, "POST")
        self.assertEqual(200, status, result)
        self.assertEqual(1, result["signing"]["signed"])
        status, after = call(server.url(), "/api/records/act-auto-2")
        self.assertNotEqual(before["signature"], after["signature"])
        status, verified = call(server.url(), "/api/secretariat/verify",
                                {"record": after}, "POST")
        self.assertTrue(verified["results"][0]["ok"], verified["results"][0]["problems"])

    def test_ingest_endpoint_signs(self) -> None:
        server = self._serve(sign_on_write=True)
        status, result = call(server.url(), "/api/ingest/events",
                              {"records": [sample("act-auto-3")]}, "POST")
        self.assertEqual(200, status, result)
        self.assertGreaterEqual(result["signing"]["signed"], 1)
        status, record = call(server.url(), "/api/records/act-auto-3")
        self.assertTrue(record.get("signature"))
        status, verified = call(server.url(), "/api/secretariat/verify",
                                {"record": record}, "POST")
        self.assertTrue(verified["results"][0]["ok"])

    def test_off_keeps_records_unsigned(self) -> None:
        server = self._serve(sign_on_write=False)
        status, result = call(server.url(), "/api/import",
                              {"records": [sample("act-auto-4")]}, "POST")
        self.assertEqual(200, status)
        self.assertFalse(result["signing"]["enabled"])
        status, record = call(server.url(), "/api/records/act-auto-4")
        self.assertFalse(record.get("signature"))

    # -- t4 收口：登记身份 → 入库签名 → 验签 的整链（docs/12 §6 sign_on_write）--
    def test_registered_identity_owns_the_on_write_signature(self) -> None:
        """登记过身份的 Agent 入库时，签名必须来自账本里那条身份，而不是临时自签。"""
        server = self._serve(sign_on_write=True)
        base = server.url()
        status, created = call(base, "/api/secretariat/identities", {
            "agent_id": AGENT, "owner_did": OWNER,
            "image_hash": "sha256:boot-image", "boot_params": {"argv": ["run"]}}, "POST")
        self.assertEqual(201, status, created)
        ledger_pubkey = created["identity"]["pubkey"]
        self.assertTrue(ledger_pubkey)

        status, result = call(base, "/api/import", {"records": [sample("act-auto-5")]}, "POST")
        self.assertEqual(200, status, result)
        self.assertTrue(result["signing"]["enabled"])
        self.assertEqual(1, result["signing"]["signed"])
        self.assertEqual(0, result["signing"]["failures"])

        status, record = call(base, "/api/records/act-auto-5")
        self.assertEqual(200, status)
        self.assertTrue(record.get("signature"))
        self.assertEqual(ledger_pubkey, record["agent_pubkey"],
                         "入库签名要用登记身份的公钥")
        self.assertEqual(OWNER, record["human_owner"], "owner 必须来自身份账本，不是自动注册的兜底")
        self.assertEqual([], VALIDATOR.collect(record), "签名后的记录仍要过 schema")

        status, verified = call(base, "/api/secretariat/verify", {"record": record}, "POST")
        self.assertEqual(200, status)
        first = verified["results"][0]
        self.assertTrue(first["ok"], first["problems"])
        self.assertEqual("registered", first["checks"]["identity"],
                         "验签要认出这是已登记身份（registered，不是 auto/missing）")
        self.assertEqual("valid", first["checks"]["signature"])

    def test_unsigned_record_reports_missing_signature_honestly(self) -> None:
        """开关关闭时不签名，验签也不能假装通过：如实报 SECRETARIAT_SIGNATURE_INVALID。"""
        server = self._serve(sign_on_write=False)
        base = server.url()
        status, result = call(base, "/api/import", {"records": [sample("act-auto-6")]}, "POST")
        self.assertEqual(200, status, result)
        self.assertEqual(0, result["signing"]["signed"])
        status, record = call(base, "/api/records/act-auto-6")
        self.assertFalse(record.get("signature"))
        status, verified = call(base, "/api/secretariat/verify", {"record": record}, "POST")
        self.assertEqual(200, status)
        first = verified["results"][0]
        self.assertFalse(first["ok"])
        codes = {item["code"] for item in first["problems"]}
        self.assertIn("SECRETARIAT_SIGNATURE_INVALID", codes, codes)


if __name__ == "__main__":
    unittest.main()
