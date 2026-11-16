"""总裁办（secretariat）模块单测：身份确权 / 行为签名 / 责任归属 / 争议裁定。

运行（只跑本文件，不要跑全量）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_secretariat.py" -v

覆盖规格书第二节的验收点：
  1. 身份：签发/轮换/吊销都只**追加**，公钥可查、私钥永不出现；
  2. 签名：覆盖字段逐个篡改都验不过；同一 Agent 的记录首尾相接成链；
  3. 风险分级：low/medium 自签、high 要 1 个审批签、critical 要 2 个 + 盖章位；
  4. 责任链：只能收窄不能放大，深度上限 5 层，过期即失效；
  5. 认定书：只靠认定书本体即可离线验证，记录被动过则验不过。
"""

from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import secretariat as sec_mod  # noqa: E402
from agent_explainer.feature_store import FeaturePaths, GENESIS_HASH  # noqa: E402
from agent_explainer.secretariat import (  # noqa: E402
    MAX_DELEGATION_DEPTH,
    RISK_POLICY,
    ROLE_LABELS,
    TSA_LOCAL_PREFIX,
    Secretariat,
    SecretariatError,
    agent_did_of,
    risk_requirements,
)

OWNER = cs.did_from_name("demo-owner", kind="user")
BOSS = cs.did_from_name("boss", kind="user")
CFO = cs.did_from_name("cfo", kind="user")
AGENT = "agent-demo"


def _record(**overrides):
    base = {
        "schema_version": "1.1",
        "id": "act-0001",
        "timestamp": "2026-11-11T10:00:00Z",
        "agent_id": AGENT,
        "action_type": "read",
        "tool_used": {"name": "calendar.read", "version": "1.0"},
        "status": "succeeded",
        "risk_level": "low",
        "confidence": 0.93,
        "context": {"input": {"day": "friday"}},
        "result_summary": "3 events",
    }
    base.update(overrides)
    return base


class _Clock:
    """可控时钟：委托过期、认定书时间都不依赖真实时间。"""

    def __init__(self, start: str = "2026-11-11T10:00:00Z") -> None:
        self.value = start

    def __call__(self) -> str:
        return self.value

    def advance(self, seconds: int) -> None:
        import datetime as _dt

        raw = self.value[:-1] + "+00:00"
        moment = _dt.datetime.fromisoformat(raw) + _dt.timedelta(seconds=seconds)
        self.value = moment.astimezone(_dt.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")


class SecretariatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = scratch_dir("secretariat")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.clock = _Clock()
        self.sec = Secretariat(self.paths, now=self.clock)

    # -- 身份确权 -----------------------------------------------------
    def test_register_identity_returns_public_view_only(self) -> None:
        identity = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER,
                                              image_hash="sha256:img", boot_params={"n": 1})
        self.assertEqual(identity["agent_id"], AGENT)
        self.assertEqual(identity["owner_did"], OWNER)
        self.assertTrue(identity["pubkey"])
        self.assertEqual(len(identity["fingerprint"]), 16)
        self.assertIsNone(identity["revoked_at"])
        self.assertTrue(identity["boot_params_hash"])
        for forbidden in ("seed", "private_key", "secret"):
            self.assertNotIn(forbidden, identity)

    def test_rotation_appends_and_supersedes(self) -> None:
        first = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        # 不轮换的重复登记是幂等的（同一枚钥匙）
        same = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        self.assertEqual(first["pubkey"], same["pubkey"])
        second = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER, rotate=True)
        self.assertNotEqual(first["pubkey"], second["pubkey"])
        self.assertEqual(second["supersedes"], same["entry_hash"])
        self.assertEqual(len(self.sec.identities()), 1)
        self.assertEqual(self.sec.identity(AGENT)["entry_hash"], second["entry_hash"])

    def test_revoke_appends_and_marks(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        revoked = self.sec.revoke_identity(AGENT, reason="left the team")
        self.assertTrue(revoked["revoked_at"])
        self.assertEqual(self.sec.identity(AGENT)["revoked_at"], revoked["revoked_at"])
        self.assertEqual(self.sec.status()["counts"]["active_identities"], 0)

    def test_revoke_unknown_identity(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.revoke_identity("nobody")
        self.assertEqual(ctx.exception.code, "SECRETARIAT_IDENTITY_NOT_FOUND")
        self.assertEqual(ctx.exception.http_status, 404)

    def test_identity_requires_owner(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.register_identity(agent_id=AGENT, owner_did="")
        self.assertEqual(ctx.exception.code, "SECRETARIAT_FIELD_INVALID")

    def test_identities_ledger_chain_is_continuous(self) -> None:
        for index in range(4):
            self.sec.register_identity(agent_id=f"agent-{index}", owner_did=OWNER)
        report = self.sec.identities_ledger.verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["count"], 4)

    # -- 行为签名 -----------------------------------------------------
    def test_sign_record_sets_every_signed_field(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        for field in sec_mod.SIGNATURE_FIELDS:
            self.assertIn(field, signed, field)
        self.assertEqual(signed["tool"], "calendar.read")
        self.assertEqual(signed["human_owner"], OWNER)
        expected_did = cs.did_from_pubkey(cs.b64d(signed["agent_pubkey"]), kind="agent")
        self.assertEqual(signed["delegation_chain"], [OWNER, expected_did])
        self.assertNotEqual(signed["prev_hash"], "")
        self.assertTrue(signed["tsa_timestamp"].startswith(TSA_LOCAL_PREFIX))
        self.assertTrue(self.sec.verify_record(signed)["ok"])

    def test_sign_record_does_not_mutate_input(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        original = _record()
        before = copy.deepcopy(original)
        self.sec.sign_record(original)
        self.assertEqual(original, before)
        self.assertNotIn("signature", original)

    def test_sign_record_auto_registers_unknown_agent(self) -> None:
        signed = self.sec.sign_record(_record(agent_id="agent-new"))
        self.assertTrue(signed["signature"])
        self.assertIn("agent-new", [item["agent_id"] for item in self.sec.identities()])

    def test_sign_record_twice_is_refused(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(signed)
        self.assertEqual(ctx.exception.code, "SECRETARIAT_FIELD_INVALID")

    def test_tampering_any_signed_field_breaks_verification(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record(risk_level="medium"))
        self.assertTrue(self.sec.verify_record(signed)["ok"])
        for field in ("timestamp", "agent_id", "action_type", "status", "confidence"):
            tampered = copy.deepcopy(signed)
            if field == "confidence":
                tampered[field] = 0.11
            elif field == "agent_id":
                tampered[field] = "agent-other"
            else:
                tampered[field] = "tampered"
            report = self.sec.verify_record(tampered)
            self.assertFalse(report["ok"], field)
            codes = [problem["code"] for problem in report["problems"]]
            self.assertIn("SECRETARIAT_SIGNATURE_INVALID", codes, field)

    def test_tampering_entry_hash_alone_is_detected(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        signed["result_summary"] = "something else entirely"
        report = self.sec.verify_record(signed)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"]["entry_hash"], "invalid")

    def test_entry_hash_chains_two_records_of_same_agent(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        first = self.sec.sign_record(_record(id="a1", timestamp="2026-11-11T10:00:00Z"))
        second = self.sec.sign_record(_record(id="a2", timestamp="2026-11-11T10:01:00Z"))
        self.assertEqual(first["prev_hash"], GENESIS_HASH)
        self.assertEqual(second["prev_hash"], first["entry_hash"])
        self.assertNotEqual(first["entry_hash"], second["entry_hash"])

    def test_sign_chain_keeps_timestamp_order(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        records = [
            _record(id="a2", timestamp="2026-11-11T10:02:00Z"),
            _record(id="a1", timestamp="2026-11-11T10:00:00Z"),
        ]
        signed = self.sec.sign_chain(records)
        # 返回顺序与入参一致；链按时间顺序接（a1 在 a2 之前）
        self.assertEqual([item["id"] for item in signed], ["a2", "a1"])
        self.assertEqual(signed[1]["prev_hash"], GENESIS_HASH)
        self.assertEqual(signed[0]["prev_hash"], signed[1]["entry_hash"])

    def test_revoked_identity_cannot_sign(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        self.sec.revoke_identity(AGENT)
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record())
        self.assertEqual(ctx.exception.code, "SECRETARIAT_IDENTITY_REVOKED")

    def test_verify_reports_tsa_local_only(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        report = self.sec.verify_record(signed)
        self.assertEqual(report["tsa_state"], "local_only")
        self.assertEqual(report["checks"]["tsa"], "local_only")

    # -- 风险分级签名策略 ---------------------------------------------
    def test_risk_policy_table_matches_spec(self) -> None:
        self.assertEqual(RISK_POLICY["low"]["approvers"], 0)
        self.assertEqual(RISK_POLICY["medium"]["approvers"], 0)
        self.assertEqual(RISK_POLICY["high"]["approvers"], 1)
        self.assertEqual(RISK_POLICY["critical"]["approvers"], 2)
        self.assertTrue(RISK_POLICY["critical"]["seal"])
        self.assertEqual(set(RISK_POLICY), {"low", "medium", "high", "critical"})

    def test_high_risk_requires_one_approver(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(risk_level="high"))
        self.assertEqual(ctx.exception.code, "SECRETARIAT_APPROVAL_REQUIRED")
        self.assertEqual(ctx.exception.http_status, 409)
        signed = self.sec.sign_record(_record(risk_level="high"),
                                      approvers=[{"did": BOSS}])
        self.assertEqual(signed["approval"]["required"], 1)
        self.assertEqual(len(signed["approval"]["approvers"]), 1)
        self.assertTrue(signed["approval"]["approvers"][0]["signature"])
        self.assertTrue(self.sec.verify_record(signed)["ok"])

    def test_critical_risk_requires_two_approvers_and_seal_flag(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        # Q5 / A6：给了审批人但按 DID 去重后不足 → 入参字段问题 = FIELD_INVALID / 400
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(risk_level="critical"),
                                 approvers=[BOSS])
        self.assertEqual(ctx.exception.code, "SECRETARIAT_FIELD_INVALID")
        self.assertEqual(ctx.exception.field, "approvers")
        self.assertEqual(ctx.exception.http_status, 400)
        # 一个都没给 → 状态问题，仍是 APPROVAL_REQUIRED / 409
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(risk_level="critical"))
        self.assertEqual(ctx.exception.code, "SECRETARIAT_APPROVAL_REQUIRED")
        self.assertEqual(ctx.exception.http_status, 409)
        signed = self.sec.sign_record(
            _record(risk_level="critical", action_type="payment"),
            approvers=[BOSS, CFO])
        self.assertEqual(len(signed["approval"]["approvers"]), 2)
        self.assertTrue(signed["approval"]["sealed"])

    def test_allow_unapproved_marks_approval_incomplete(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record(risk_level="high"), allow_unapproved=True)
        report = self.sec.verify_record(signed)
        self.assertEqual(report["checks"]["approval"]["ok"], False)
        self.assertFalse(report["ok"])

    def test_unknown_risk_level_rejected(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            risk_requirements("casual")
        self.assertEqual(ctx.exception.code, "SECRETARIAT_FIELD_INVALID")

    def test_seal_and_verify(self) -> None:
        seal = self.sec.seal(subject="close incident", digest="ab" * 32, reason="reviewed")
        self.assertEqual(seal["algorithm"], "ed25519")
        self.assertTrue(self.sec.verify_seal(seal))
        tampered = dict(seal)
        tampered["digest"] = "cd" * 32
        self.assertFalse(self.sec.verify_seal(tampered))

    # -- 责任归属 -----------------------------------------------------
    def _scope(self, **overrides):
        scope = {"action_types": ["read", "write"], "tools": ["calendar.read"],
                 "max_risk": "medium"}
        scope.update(overrides)
        return scope

    def test_delegation_creates_depth_one(self) -> None:
        delegation = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope=self._scope(), ttl_seconds=3600)
        self.assertEqual(delegation["depth"], 1)
        self.assertTrue(delegation["signature"])
        self.assertEqual(len(self.sec.delegation_chain_of(delegation["id"])), 1)

    def test_delegation_narrowing_allowed(self) -> None:
        parent = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope=self._scope())
        child = self.sec.create_delegation(
            delegator_did="did:tracelens:agent:a1", delegatee_did="did:tracelens:agent:a2",
            scope={"action_types": ["read"], "tools": ["calendar.read"], "max_risk": "low"},
            parent_id=parent["id"])
        self.assertEqual(child["depth"], 2)
        self.assertEqual(len(self.sec.delegation_chain_of(child["id"])), 2)

    def test_delegation_widening_action_types_rejected(self) -> None:
        parent = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope={"action_types": ["read"], "tools": [], "max_risk": "low"})
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(
                delegator_did="did:tracelens:agent:a1",
                delegatee_did="did:tracelens:agent:a2",
                scope={"action_types": ["read", "payment"], "tools": [], "max_risk": "low"},
                parent_id=parent["id"])
        self.assertEqual(ctx.exception.code, "SECRETARIAT_SCOPE_WIDENED")

    def test_delegation_widening_risk_rejected(self) -> None:
        parent = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope={"action_types": ["read"], "tools": [], "max_risk": "low"})
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(
                delegator_did="did:tracelens:agent:a1",
                delegatee_did="did:tracelens:agent:a2",
                scope={"action_types": ["read"], "tools": [], "max_risk": "high"},
                parent_id=parent["id"])
        self.assertEqual(ctx.exception.code, "SECRETARIAT_SCOPE_WIDENED")

    def test_delegation_depth_limit(self) -> None:
        scope = {"action_types": ["read"], "tools": [], "max_risk": "low"}
        parent_id = None
        previous = OWNER
        for depth in range(1, MAX_DELEGATION_DEPTH + 1):
            delegatee = f"did:tracelens:agent:level{depth}"
            delegation = self.sec.create_delegation(
                delegator_did=previous, delegatee_did=delegatee,
                scope=scope, parent_id=parent_id)
            self.assertEqual(delegation["depth"], depth)
            parent_id = delegation["id"]
            previous = delegatee
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(
                delegator_did=previous, delegatee_did="did:tracelens:agent:toofar",
                scope=scope, parent_id=parent_id)
        self.assertEqual(ctx.exception.code, "SECRETARIAT_DEPTH_EXCEEDED")

    def test_delegation_requires_chain_continuity(self) -> None:
        parent = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope=self._scope())
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(
                delegator_did="did:tracelens:agent:imposter",
                delegatee_did="did:tracelens:agent:a2",
                scope=self._scope(), parent_id=parent["id"])
        self.assertEqual(ctx.exception.code, "SECRETARIAT_DELEGATION_INVALID")

    def test_expired_delegation_denied(self) -> None:
        delegation = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope=self._scope(), ttl_seconds=60)
        verdict = self.sec.authorise(delegation["id"], action_type="read",
                                     tool="calendar.read", risk_level="low")
        self.assertTrue(verdict["allowed"])
        self.clock.advance(120)
        expired = self.sec.authorise(delegation["id"], action_type="read",
                                     tool="calendar.read", risk_level="low")
        self.assertFalse(expired["allowed"])
        self.assertEqual(expired["reason"], "SECRETARIAT_DELEGATION_EXPIRED")

    def test_authorise_scope_and_risk_checks(self) -> None:
        delegation = self.sec.create_delegation(
            delegator_did=OWNER, delegatee_did="did:tracelens:agent:a1",
            scope={"action_types": ["read"], "tools": ["calendar.read"], "max_risk": "low"})
        self.assertTrue(self.sec.authorise(delegation["id"], action_type="read",
                                           tool="calendar.read", risk_level="low")["allowed"])
        self.assertFalse(self.sec.authorise(delegation["id"], action_type="write",
                                            tool="calendar.read", risk_level="low")["allowed"])
        self.assertFalse(self.sec.authorise(delegation["id"], action_type="read",
                                            tool="mail.send", risk_level="low")["allowed"])
        self.assertFalse(self.sec.authorise(delegation["id"], action_type="read",
                                            tool="calendar.read", risk_level="high")["allowed"])

    # -- 争议裁定 -----------------------------------------------------
    def test_letter_build_and_offline_verify(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        letter = self.sec.build_letter(subject="demo incident", records=[signed],
                                       responsibility="technical", audit_head="sha256:abc")
        self.assertEqual(letter["responsibility"]["role"], "technical")
        self.assertEqual(letter["responsibility"]["label"], ROLE_LABELS["technical"])
        self.assertTrue(letter["signature"])
        self.assertTrue(letter["public_key"])
        self.assertTrue(letter["verification"]["signatures_ok"])
        self.assertTrue(letter["verification"]["chain_ok"])
        self.assertEqual(letter["audit_head"], "sha256:abc")
        # 关键验收点：只靠认定书本体（JSON 里那点东西）就能验证
        reloaded = json.loads(json.dumps(letter))
        report = self.sec.verify_letter(reloaded)
        self.assertTrue(report["ok"], report)
        self.assertTrue(self.paths.letter_path(letter["id"]).exists())
        self.assertEqual(self.sec.letters()[0]["id"], letter["id"])

    def test_letter_verify_detects_tampered_record_digest(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        letter = self.sec.build_letter(subject="demo", records=[signed])
        letter["actions"][0]["entry_hash"] = "f" * 64
        report = self.sec.verify_letter(letter)
        self.assertFalse(report["ok"])
        codes = [problem["code"] for problem in report["problems"]]
        self.assertIn("SECRETARIAT_HASH_MISMATCH", codes)

    def test_letter_requires_at_least_one_record(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.build_letter(subject="empty", records=[])
        self.assertEqual(ctx.exception.code, "SECRETARIAT_LETTER_EMPTY")

    def test_letter_rejects_unknown_responsibility(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.build_letter(subject="x", records=[signed], responsibility="cosmic")
        self.assertEqual(ctx.exception.code, "SECRETARIAT_FIELD_INVALID")

    def test_letter_not_found(self) -> None:
        self.assertIsNone(self.sec.letter("letter-missing"))

    # -- 状态与落盘 ---------------------------------------------------
    def test_status_shape(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        self.sec.build_letter(subject="demo", records=[signed])
        status = self.sec.status()
        self.assertEqual(status["version"], sec_mod.SECRETARIAT_VERSION)
        self.assertEqual(status["counts"]["identities"], 1)
        self.assertEqual(status["counts"]["letters"], 1)
        self.assertEqual(status["counts"]["signed_records"], 1)
        self.assertEqual(set(status["risk_policy"]), set(RISK_POLICY))
        self.assertTrue(status["chains"]["identities"]["ok"])
        self.assertIn("degraded", status)
        self.assertIn("local-tsa", status["degraded"])
        self.assertNotIn("seed", json.dumps(status["keyring"]))

    def test_state_survives_reload(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        signed = self.sec.sign_record(_record())
        reopened = Secretariat(self.paths, now=self.clock)
        self.assertIsNotNone(reopened.identity(AGENT))
        self.assertEqual(reopened.identity(AGENT)["pubkey"], self.sec.identity(AGENT)["pubkey"])
        self.assertTrue(reopened.verify_record(signed)["ok"])
        self.assertEqual(reopened._head_of(AGENT), signed["entry_hash"])

    def test_keyring_file_never_holds_public_material_in_api_shape(self) -> None:
        self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        payload = json.loads(self.sec.keyring.facts()["path"] and
                             pathlib.Path(self.sec.keyring.facts()["path"]).read_text(
                                 encoding="utf-8"))
        self.assertIn("keys", payload)
        # 密钥库里当然是私钥；关键是任何对外视图都不含它
        self.assertNotIn("seed", json.dumps(self.sec.identities()))
        self.assertNotIn("seed", json.dumps(self.sec.status()))


class SecretariatHelpersTest(unittest.TestCase):
    def test_signed_payload_is_canonical_subset(self) -> None:
        record = {"b": 2, "a": 1, "c": 3}
        self.assertEqual(cs.signed_payload(record, ("a", "b")), b'{"a":1,"b":2}')

    def test_risk_requirements_returns_level(self) -> None:
        policy = risk_requirements("critical")
        self.assertEqual(policy["level"], "critical")
        self.assertTrue(policy["self_sign"])
        self.assertEqual(policy["approvers"], 2)


class SecretariatR2BoundaryTest(unittest.TestCase):
    """R2 复核自查边界（docs/audit/r2-review.md §自查四项 + 本轮修复项）。

    每条都是一个可独立复跑的断言：伪造 human_owner、链尾非本实例 DID、同 DID 委托、
    已吊销再签，加上本轮修的四条（自委托 / 已吊销实例派委托 / 过期父委托 / ttl 类型）。
    """

    def setUp(self) -> None:
        self.base = scratch_dir("secretariat")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.clock = _Clock()
        self.sec = Secretariat(self.paths, now=self.clock)
        self.identity = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        self.agent_did = agent_did_of(self.identity["pubkey"])

    def _scope(self, **overrides):
        scope = {"action_types": ["read"], "tools": ["calendar.read"], "max_risk": "low"}
        scope.update(overrides)
        return scope

    # -- 自查边界 ①：伪造 human_owner -------------------------------------
    def test_boundary_forged_human_owner_is_field_invalid(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(human_owner=CFO))
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("human_owner", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual(OWNER, ctx.exception.detail["ledger"])
        self.assertEqual(CFO, ctx.exception.detail["record"])

    # -- 自查边界 ②：链尾不是本实例 DID ------------------------------------
    def test_boundary_chain_tail_must_be_this_instance_did(self) -> None:
        self.assertEqual([OWNER, self.agent_did],
                         self.sec.sign_record(_record())["delegation_chain"])
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(delegation_chain=[OWNER, CFO]))
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual(CFO, ctx.exception.detail["tail"])
        self.assertEqual(self.agent_did, ctx.exception.detail["agent_did"])
        # 链根不是账本里的 owner → 400 FIELD_INVALID(field=human_owner)（docs/12 §3 冻结口径）
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(delegation_chain=[CFO, self.agent_did]))
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual("human_owner", ctx.exception.field)
        # 链里同一段 DID 重复出现（自委托写进记录）→ 结构问题
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record(delegation_chain=[OWNER, self.agent_did,
                                                           self.agent_did]))
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual([self.agent_did], ctx.exception.detail["duplicated"])

    # -- 自查边界 ③：同 DID 委托（自委托）---------------------------------
    def test_boundary_self_delegation_is_rejected(self) -> None:
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did=OWNER, delegatee_did=OWNER,
                                       scope=self._scope())
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual("delegatee_did", ctx.exception.field)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertEqual("self_delegation", ctx.exception.detail["reason"])
        # 子委托同样不能自委托：a1 → a1
        root = self.sec.create_delegation(delegator_did=OWNER,
                                          delegatee_did=self.agent_did,
                                          scope=self._scope())
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did=self.agent_did,
                                       delegatee_did=self.agent_did,
                                       scope=self._scope(), parent_id=root["id"])
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual("self_delegation", ctx.exception.detail["reason"])
        # 自委托被拒不留账本行（append-only 账本不能长出废链）
        self.assertEqual(1, len(self.sec.delegations_ledger.load()))

    # -- 自查边界 ④：已吊销再签 -------------------------------------------
    def test_boundary_sign_after_revoke_is_refused(self) -> None:
        self.sec.revoke_identity(AGENT, reason="leave")
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.sign_record(_record())
        self.assertEqual("SECRETARIAT_IDENTITY_REVOKED", ctx.exception.code)
        # B06 契约偏差：docs/12:236（A3）写 400，运行时是 409（状态冲突语义）。
        # 本条把运行时口径钉成契约，报告 docs/audit/r2-review.md §5 已建议改规格为 409。
        self.assertEqual(409, ctx.exception.http_status)

    # -- 本轮修复：已吊销实例不能再派委托 ---------------------------------
    def test_revoked_instance_cannot_be_delegation_party(self) -> None:
        self.sec.revoke_identity(AGENT, reason="leave")
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did=OWNER, delegatee_did=self.agent_did,
                                       scope=self._scope())
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual("delegatee_revoked", ctx.exception.detail["reason"])
        root = self.sec.create_delegation(delegator_did=OWNER,
                                          delegatee_did="did:tracelens:agent:sub1",
                                          scope=self._scope())
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did=self.agent_did,
                                       delegatee_did="did:tracelens:agent:sub2",
                                       scope=self._scope(), parent_id=root["id"])
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual("delegator_revoked", ctx.exception.detail["reason"])

    # -- 本轮修复：过期授权不能当新委托的根 -------------------------------
    def test_expired_parent_blocks_new_child_and_authorise(self) -> None:
        parent = self.sec.create_delegation(delegator_did=OWNER,
                                            delegatee_did="did:tracelens:agent:a1",
                                            scope=self._scope(), ttl_seconds=60)
        child = self.sec.create_delegation(delegator_did="did:tracelens:agent:a1",
                                           delegatee_did="did:tracelens:agent:a2",
                                           scope=self._scope(), ttl_seconds=3600,
                                           parent_id=parent["id"])
        self.assertTrue(self.sec.authorise(child["id"], action_type="read",
                                           tool="calendar.read",
                                           risk_level="low")["allowed"])
        self.clock.advance(120)        # 父委托过期，子委托自己还没到期
        expired = self.sec.authorise(child["id"], action_type="read",
                                     tool="calendar.read", risk_level="low")
        self.assertFalse(expired["allowed"])
        self.assertEqual("SECRETARIAT_DELEGATION_EXPIRED", expired["reason"])
        self.assertEqual("ancestor", expired["detail"]["scope"])
        self.assertEqual(parent["id"], expired["detail"]["delegation_id"])
        # 过期的父委托不能当新委托的根（否则等于替过期授权续期）
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.create_delegation(delegator_did="did:tracelens:agent:a1",
                                       delegatee_did="did:tracelens:agent:a3",
                                       scope=self._scope(), parent_id=parent["id"])
        self.assertEqual("SECRETARIAT_DELEGATION_INVALID", ctx.exception.code)
        self.assertEqual("parent_id", ctx.exception.field)
        self.assertEqual("parent_expired", ctx.exception.detail["reason"])
        # 未过期的那一层（child）仍然可以作为根
        ok = self.sec.create_delegation(delegator_did="did:tracelens:agent:a2",
                                        delegatee_did="did:tracelens:agent:a3",
                                        scope=self._scope(), parent_id=child["id"])
        self.assertEqual(3, ok["depth"])

    # -- 本轮修复：ttl 坏类型给 400，不冒成 500 ---------------------------
    def test_ttl_seconds_type_is_validated(self) -> None:
        for bad in (0, -1, "abc", True, 1.5, [], {}):
            with self.subTest(ttl=bad):
                with self.assertRaises(SecretariatError) as ctx:
                    self.sec.create_delegation(delegator_did=OWNER,
                                               delegatee_did="did:tracelens:agent:a1",
                                               scope=self._scope(), ttl_seconds=bad)
                self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
                self.assertEqual("ttl_seconds", ctx.exception.field)
                self.assertEqual(400, ctx.exception.http_status)
        # 不传 → 默认 30 天；数字字符串按 JSON 宽进（服务端传的是原值）
        default = self.sec.create_delegation(delegator_did=OWNER,
                                             delegatee_did="did:tracelens:agent:a1",
                                             scope=self._scope())
        self.assertEqual("2026-11-11T10:00:00Z", default["issued_at"])
        self.assertEqual("2026-12-11T10:00:00Z", default["expires_at"])

    # -- 本轮修复：吊销后重登记的口径要说准（不谎报"信息不同"）-----------
    def test_re_register_after_revoke_says_revoked(self) -> None:
        self.sec.revoke_identity(AGENT, reason="leave")
        with self.assertRaises(SecretariatError) as ctx:
            self.sec.register_identity(agent_id=AGENT, owner_did=OWNER)
        self.assertEqual("SECRETARIAT_FIELD_INVALID", ctx.exception.code)
        self.assertEqual(400, ctx.exception.http_status)
        self.assertIn("已被吊销", ctx.exception.reason)
        self.assertTrue(ctx.exception.detail["revoked_at"])
        # 显式 rotate=True = 新一代实例（docs/13 §1 #4 的唯一出口），并且能继续签名
        fresh = self.sec.register_identity(agent_id=AGENT, owner_did=OWNER, rotate=True)
        self.assertIsNone(fresh["revoked_at"])
        self.assertNotEqual(self.identity["pubkey"], fresh["pubkey"])
        signed = self.sec.sign_record(_record())
        self.assertTrue(self.sec.verify_record(signed)["ok"])

    # -- 本轮修复：委托签名出自委托人自己的可追溯密钥 ---------------------
    def test_delegation_signed_with_delegator_own_identity_key(self) -> None:
        root = self.sec.create_delegation(delegator_did=OWNER,
                                          delegatee_did=self.agent_did,
                                          scope=self._scope())
        # 人（未登记成身份）→ 本地为该 DID 生成的钥匙，如实标 local_key
        self.assertEqual("local_key", root["signer"]["kind"])
        self.assertNotEqual(self.identity["pubkey"], root["pubkey"])
        child = self.sec.create_delegation(delegator_did=self.agent_did,
                                           delegatee_did="did:tracelens:agent:sub",
                                           scope=self._scope(), parent_id=root["id"])
        # 已登记实例 → 用该实例身份账本里的公钥对应私钥签，责任链中间段不再是自证
        self.assertEqual("agent_identity", child["signer"]["kind"])
        self.assertEqual(AGENT, child["signer"]["agent_id"])
        self.assertEqual(self.identity["pubkey"], child["pubkey"])
        # 签名覆盖委托体（含 signer），`pubkey` 是验签公钥本身 → 不在被签的载荷里
        payload = {key: value for key, value in child.items()
                   if key not in ("signature", "pubkey", "seq", "prev_hash", "entry_hash")}
        self.assertTrue(cs.verify_b64(child["pubkey"], cs.canonical_bytes(payload),
                                      child["signature"]))
        # 换一条委托，签名必须不同（签名覆盖整条委托体）
        other = self.sec.create_delegation(delegator_did=self.agent_did,
                                           delegatee_did="did:tracelens:agent:sub2",
                                           scope=self._scope(), parent_id=root["id"])
        self.assertNotEqual(child["signature"], other["signature"])
        # 事后改 scope（绕过 create_delegation 的直接写盘/篡改场景）→ 委托签名复算必失败
        tampered = dict(child, scope={"action_types": ["payment"], "tools": [],
                                      "max_risk": "critical"})
        tampered_payload = {key: value for key, value in tampered.items()
                            if key not in ("signature", "pubkey", "seq", "prev_hash",
                                           "entry_hash")}
        self.assertFalse(cs.verify_b64(child["pubkey"], cs.canonical_bytes(tampered_payload),
                                       child["signature"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
