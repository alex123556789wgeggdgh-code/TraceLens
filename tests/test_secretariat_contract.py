"""总裁办 R2/R3 契约测试：身份不可伪造、责任链是 DID、签名记录过 schema、审批可验证。

与 ``test_secretariat.py`` 的分工：那个文件测"四支柱能不能用"，本文件测
"写进记录的东西是否符合数据契约、是否经得起伪造"。全部用临时目录，不写仓库。
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from agent_explainer.secretariat import Secretariat, SecretariatError  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLES = json.loads((ROOT / "samples" / "action_records.json")
                     .read_text(encoding="utf-8"))["records"]
BY_ID = {record["id"]: record for record in SAMPLES}

OWNER = cs.did_from_name("owner", kind="user")
BOSS = cs.did_from_name("boss", kind="user")
CFO = cs.did_from_name("cfo", kind="user")


class _Clock:
    def __init__(self, start: str = "2026-11-11T10:00:00Z") -> None:
        self.value = start

    def __call__(self) -> str:
        return self.value


class ContractBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.paths = FeaturePaths(self._temp.name)
        self.sec = Secretariat(self.paths, now=_Clock())
        self.agent_id = "agent-contract"
        self.identity = self.sec.register_identity(agent_id=self.agent_id, owner_did=OWNER)
        self.agent_did = cs.did_from_pubkey(cs.b64d(self.identity["pubkey"]), kind="agent")

    def sign(self, sample_id: str, *, risk: str | None = None,
             approvers: list[Any] | None = None, **overrides) -> dict:
        raw = copy.deepcopy(BY_ID[sample_id])
        raw["agent_id"] = self.agent_id
        raw.update(overrides)
        return self.sec.sign_record(raw, risk_level=risk, approvers=approvers or [])

    # -- R3：schema 合规 -------------------------------------------------
    def test_signed_records_validate_against_action_record_schema(self) -> None:
        cases = [
            ("low", self.sign("act-demo-0001")),
            ("medium", self.sign("act-demo-0004")),
            ("high", self.sign("act-demo-0008", risk="high", approvers=[BOSS])),
            ("critical", self.sign("act-demo-0005", risk="critical",
                                   approvers=[BOSS, CFO])),
        ]
        for label, signed in cases:
            errors = VALIDATOR.collect(signed)
            self.assertEqual([], errors, f"{label} 签名后的记录必须通过 schema："
                                         f"{[(e.data_path, e.message) for e in errors]}")
            self.assertTrue(self.sec.verify_record(signed)["ok"], label)

    def test_tool_is_derived_and_part_of_signed_facts(self) -> None:
        signed = self.sign("act-demo-0007")          # notes.write
        self.assertEqual("notes.write", signed["tool"])
        mutated = copy.deepcopy(signed)
        mutated["tool"] = "filesystem.remove"
        self.assertFalse(self.sec.verify_record(mutated)["ok"])

    # -- R3：责任链是 DID -------------------------------------------------
    def test_delegation_chain_uses_instance_did_not_agent_id(self) -> None:
        signed = self.sign("act-demo-0001")
        self.assertEqual([OWNER, self.agent_did], signed["delegation_chain"])
        self.assertNotIn(self.agent_id, signed["delegation_chain"])
        for item in signed["delegation_chain"]:
            self.assertRegex(item, r"^did:[a-z0-9]{2,32}:[a-z0-9-]{1,32}:[a-z0-9]{8,64}$")

    def test_forged_owner_or_chain_is_rejected(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001", human_owner=CFO)
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        # docs/12 §3 冻结表 C「链根校验」：根 ≠ owner_did → 400 FIELD_INVALID(field=human_owner)
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001", delegation_chain=[CFO, self.agent_did])
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("human_owner", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        # 链尾不是本实例 DID → 结构问题 = DELEGATION_INVALID / 400
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001", delegation_chain=[OWNER, CFO])
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual(400, ctx.exception.http_status)
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001", delegation_chain=[OWNER, "not-a-did"])
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        # 同一段 DID 在链里重复出现（自委托写进记录）→ 结构问题
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001", delegation_chain=[OWNER, self.agent_did,
                                                         self.agent_did])
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)

    # -- R3：审批不可伪造 -------------------------------------------------
    def test_critical_needs_two_distinct_approvers(self) -> None:
        # Q5（docs/12 §9）+ A6：给了审批人、但按 DID 去重后不足 → 复用
        # SECRETARIAT_FIELD_INVALID（field=approvers）/ 400，不再报 409。
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0005", risk="critical", approvers=[BOSS, BOSS])
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("approvers", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual(1, ctx.exception.detail["given"])
        self.assertEqual(2, ctx.exception.detail["supplied"])
        signed = self.sign("act-demo-0005", risk="critical", approvers=[BOSS, CFO])
        self.assertEqual(2, len(signed["approval"]["approvers"]))
        self.assertTrue(signed["approval"]["sealed"])
        self.assertTrue(signed["approval"]["seal"]["signature"])

    def test_missing_approvers_stay_approval_required(self) -> None:
        """一个审批人都没给（传空数组）＝ 状态未满足，仍是 409 APPROVAL_REQUIRED（Q5 的另一支）。"""
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0005", risk="critical", approvers=[])
        self.assertEqual("SECRETARIAT_APPROVAL_REQUIRED", ctx.exception.code)
        self.assertEqual(409, ctx.exception.http_status)
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0008", risk="high", approvers=[])
        self.assertEqual("SECRETARIAT_APPROVAL_REQUIRED", ctx.exception.code)
        self.assertEqual(409, ctx.exception.http_status)

    def test_approval_signature_is_bound_to_the_record(self) -> None:
        first = self.sign("act-demo-0008", risk="high", approvers=[BOSS])
        second = self.sign("act-demo-0008", risk="high", approvers=[BOSS],
                           id="act-demo-0008-copy")
        self.assertNotEqual(first["approval"]["approvers"][0]["signature"],
                            second["approval"]["approvers"][0]["signature"])

    def test_forged_approval_signature_is_reported(self) -> None:
        signed = self.sign("act-demo-0008", risk="high", approvers=[BOSS])
        broken = copy.deepcopy(signed)
        broken["approval"]["approvers"][0]["signature"] = cs.b64e(b"\x01" * 64)
        report = self.sec.verify_record(broken)
        self.assertFalse(report["ok"])
        codes = [problem["code"] for problem in report["problems"]]
        self.assertIn("SECRETARIAT_SIGNATURE_INVALID", codes)

    # -- R2：身份不可伪造 -------------------------------------------------
    def test_registration_is_idempotent_and_revocation_is_terminal(self) -> None:
        again = self.sec.register_identity(agent_id=self.agent_id, owner_did=OWNER)
        self.assertEqual(self.identity["id"], again["id"])
        self.assertEqual(1, len(self.sec.identities_ledger.load()))
        self.sec.revoke_identity(self.agent_id, reason="test")
        self.sec.revoke_identity(self.agent_id, reason="again")      # 幂等
        records = [entry for entry in self.sec.identities_ledger.load()
                   if entry.get("agent_id") == self.agent_id and entry.get("revoked_at")]
        self.assertEqual(1, len(records))
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0001")
        self.assertEqual("SECRETARIAT_IDENTITY_REVOKED", ctx.exception.code)

    def test_identity_records_never_leak_private_material(self) -> None:
        blob = json.dumps(self.sec.public_identity(self.identity), ensure_ascii=False)
        for forbidden in ("seed", "private_key", "privkey"):
            self.assertNotIn(forbidden, blob)

    def test_owner_did_must_match_schema_shape(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.register_identity(agent_id="agent-bad-owner", owner_did="did:x:y:z")
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)

    # -- R2：委托链 -------------------------------------------------------
    def test_delegation_did_must_be_well_formed(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did="not-a-did",
                                       delegatee_did="did:tracelens:agent:a1",
                                       scope={"action_types": ["read"]})
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)

    def test_delegator_agent_must_have_a_source(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(
                delegator_did="did:tracelens:agent:unknown",
                delegatee_did="did:tracelens:agent:a1",
                scope={"action_types": ["read"]})
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        root = self.sec.create_delegation(delegator_did=OWNER,
                                          delegatee_did="did:tracelens:agent:a1",
                                          scope={"action_types": ["read"]})
        child = self.sec.create_delegation(delegator_did="did:tracelens:agent:a1",
                                           delegatee_did="did:tracelens:agent:a2",
                                           scope={"action_types": ["read"]},
                                           parent_id=root["id"])
        self.assertEqual(2, child["depth"])


    # -- R3 复核补的边界：签名器不得写出不合规的已签名记录；链与认定书要如实标注 --
    # -- schema 是机器事实：签名器不得产出过签的字段 -----------------------
    def test_tool_over_schema_limit_is_refused_before_signing(self) -> None:
        raw = copy.deepcopy(BY_ID["act-demo-0003"])
        raw["agent_id"] = self.agent_id
        raw["tool_used"] = {"name": "x" * 160, "version": "1"}
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(raw)
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("tool", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual({"length": 160, "limit": 120}, ctx.exception.detail)
        # 边界内必须签得出来，且过 schema（不是"一律拒绝"）
        fits = copy.deepcopy(raw)
        fits["tool_used"] = {"name": "y" * 120, "version": "1"}
        fits["id"] = "act-fit-tool"
        signed = self.sec.sign_record(fits)
        self.assertEqual("y" * 120, signed["tool"])
        self.assertEqual([], [(e.data_path, e.message) for e in VALIDATOR.collect(signed)])

    def test_approver_role_over_schema_limit_is_refused(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0008", risk="high",
                      approvers=[{"did": BOSS, "role": "r" * 60}])
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("approvers", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual({"role_length": 60, "limit": 40, "did": BOSS},
                         ctx.exception.detail)
        signed = self.sign("act-demo-0008", risk="high",
                           approvers=[{"did": BOSS, "role": "r" * 40}])
        self.assertEqual("r" * 40, signed["approval"]["approvers"][0]["role"])
        self.assertEqual([], [(e.data_path, e.message) for e in VALIDATOR.collect(signed)])

    def test_too_many_approvers_are_refused(self) -> None:
        extra = [cs.did_from_name(f"extra-{index}", kind="user") for index in range(4)]
        with self.assertRaises(SecretariatError) as ctx:
            self.sign("act-demo-0005", risk="critical", approvers=[BOSS, CFO] + extra)
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("approvers", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual({"unique": 6, "limit": 4, "risk_level": "critical"},
                         ctx.exception.detail)
        # 4 个不同 user DID 必须签得出来并过 schema（critical 只要 2 个，多到上限不违规）
        signed = self.sign("act-demo-0005", risk="critical", approvers=[BOSS, CFO] + extra[:2])
        self.assertEqual(4, len(signed["approval"]["approvers"]))
        self.assertEqual(2, signed["approval"]["required"])
        self.assertEqual([], [(e.data_path, e.message) for e in VALIDATOR.collect(signed)])
        self.assertTrue(self.sec.verify_record(signed)["ok"])

    # -- 哈希链连续性：不能永远只能报 unverified --------------------------
    def test_prev_hash_continuity_is_verifiable_for_whole_chain(self) -> None:
        first = self.sign("act-demo-0001")
        second = self.sign("act-demo-0002")
        self.assertEqual("genesis", self.sec.verify_record(first)["checks"]["prev_hash"])
        self.assertEqual(first["entry_hash"], second["prev_hash"])
        self.assertEqual("continuous", self.sec.verify_record(second)["checks"]["prev_hash"])
        # 换一台机器（没有链头状态）：记录自证签名仍有效，连续性如实标"无法判定"
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        stranger = Secretariat(FeaturePaths(other.name))
        report = stranger.verify_record(second)
        self.assertEqual("valid", report["checks"]["signature"])
        self.assertEqual("unregistered", report["checks"]["identity"])
        self.assertEqual("unverifiable_without_chain", report["checks"]["prev_hash"])

    # -- 认定书：拿不到审计链头要登记，不许伪造 ---------------------------
    def test_letter_registers_missing_audit_head(self) -> None:
        signed = self.sign("act-demo-0001")
        letter = self.sec.build_letter(subject="无链头的认定书", records=[signed])
        self.assertEqual("", letter["audit_head"])
        scopes = [problem.get("detail", {}).get("scope")
                  for problem in letter["verification"]["problems"]]
        self.assertIn("audit_head", scopes)
        self.assertTrue(self.sec.verify_letter(letter)["checks"]["signature"],
                        "登记项在签名之前写进正文，认定书签名必须自洽")
        anchored = self.sec.build_letter(subject="有链头的认定书", records=[signed],
                                         audit_head="sha256:" + "a" * 64)
        self.assertEqual("sha256:" + "a" * 64, anchored["audit_head"])
        self.assertNotIn("audit_head",
                         [problem.get("detail", {}).get("scope")
                          for problem in anchored["verification"]["problems"]])


if __name__ == "__main__":
    unittest.main()
