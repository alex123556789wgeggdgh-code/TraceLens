"""R5-3b 验收测试：审计锚定摘要的**字段级**掩码豁免（PM-VERDICT-R5-3 §4 立的潜伏缺陷）。

缺陷（非 R5-3 引入）：``audit.redact_text`` 的 ``long_digits`` 规则（11-20 位连续数字，
银行卡式 PII）会啃掉 ``rollback.anchored`` 锚定事件里 ``fields.snapshot_sha256`` 的一段
——sha256 十六进制串里出现 ≥11 位连续数字是**常态**（本文件实测：20000 个真实 sha256
里 11.0% 命中，``cn_mobile``/``id_card`` 是子集，去重后总计 11.0%）。摘要一被掩码，
TP-12「前态快照摘要逐字进链」就破：取证无法把链上摘要与 journal 前态快照逐字对上。

本套件钉住的四件事（TASK-R5-3b §2）：

  1. **受控触发**：构造含 15-17 位连续数字串的 sha256（mock/受控数据，不靠随机撞）→
     锚定摘要在载荷、``fields`` 桶、落盘 jsonl 原文三个面上都**逐字**存在；
  2. **规则本体未放宽**（铁律 2）：自由文本里的 15 位数字串照旧打码，
     ``redact_text`` 对同一份摘要照旧掩码——豁免只发生在**字段级**；
  3. **其余 PII 防线逐字不变**（铁律 3）：同一事件 ``journal_id`` 落盘掩码口径不动；
     豁免的形状闸门把「纯数字串」（卡号/手机号/身份证形状）挡在门外；
  4. **链与签名不受影响**：``audit.verify()`` / ``entry_hash`` 独立复算 / journal 链复算 /
     记录验签全部照旧通过，``SIGNATURE_FIELDS`` 11 字段一字未动。

二选一（TASK §2.3）取**后者**：不靠固定种子躲随机，而是钉「**无论哈希内容如何**都逐字
进链」——形状闸门对任何 ``[0-9a-f]{64}`` 摘要恒成立（本套件 240 例 sweep + 6 例受控
端到端各验一遍），随机性从断言前提里被拿掉，而不是被 seed 藏起来。理由见 REPORT §2.3。

运行::

    cd agent-explainer
    python -m unittest tests.test_anchor_masking_exemption -v

临时目录一律走 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）；本文件不启 HTTP，
故不挂 ``tests/http_fixture.py`` 的连接重试纪律（沿用其"临时目录走 scratch"这一条）。
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import pathlib
import shutil
import sys
import unittest
from collections.abc import Mapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402

from agent_explainer import audit as au          # noqa: E402
from agent_explainer import feature_store as fs  # noqa: E402
from agent_explainer import rollback as rb       # noqa: E402
from agent_explainer import secretariat as sec   # noqa: E402
from agent_explainer import server as sv         # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
BASE = next(r for r in NATIVE if r["id"] == "act-demo-0007")

# --------------------------------------------------------------------------- #
# 受控数据
# --------------------------------------------------------------------------- #

def digest_of(seed: str) -> str:
    """一份真实形状的 sha256（十六进制小写，64 位）。"""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def long_run(text: str) -> str:
    """``long_digits`` 规则在这段文本里命中的连续数字串（没命中返回空串）。"""
    match = au.PII_PATTERNS["long_digits"].search(text)
    return match.group(0) if match else ""


#: 受控触发样本：这些 sha256 里含 **≥15 位连续数字**，改前**必然**被 ``long_digits``
#: 掩码（``test_controlled_digests_are_masked_by_the_rule_body`` 逐条自证样本没失效）。
TRIGGER_SEEDS: tuple[str, ...] = tuple(f"anchor-{n}" for n in
                                       (15, 105, 109, 160, 174, 186))
TRIGGER_DIGESTS: tuple[str, ...] = tuple(digest_of(s) for s in TRIGGER_SEEDS)

#: 既定口径：journal_id 落盘掩码（``test_r1_acceptance`` 注释在案，PM 历次裁决采信）。
JOURNAL_ID = "rbj_20261114235900_abcdef1234567890"
#: 19 位纯数字 = 银行卡式 PII（形状闸门必须挡住它）。
CARD = "6222021234567890123"
EMAIL = "alice.chen@corp-mail.example"
TARGET = "act-r53b-target"
CHILD = "act-r53b-child"


def anchor_fields(digest: str) -> dict[str, Any]:
    """服务端锚定载荷（``server.py`` 的 ``anchor_fields`` 同形，逐字段照抄口径）。"""
    return {
        "rollback_event": "rollback.anchored",
        "journal_id": JOURNAL_ID,
        "snapshot_sha256": digest,
        "rollback_record_id": "act-r53b-rollback",
        "journal_phase": "succeeded",
    }


def anchor_event(digest: str, **kw: Any) -> dict[str, Any]:
    """走 ``build_event`` 标准通道（写盘那条路）造一条锚定载荷。"""
    return au.build_event("rollback.anchored",
                          actor_id=str(kw.pop("actor_id", "local")),
                          actor_role="local", mode="local",
                          action_id=str(kw.pop("action_id", TARGET)),
                          extra=dict(anchor_fields(digest)), **kw)


@contextlib.contextmanager
def forced_snapshot_digest(target_id: str, digest: str):
    """把**目标记录那份前态快照**的摘要钉成受控值（受控触发，不靠随机撞）。

    只替换 ``rollback.hash_object`` 里 ``id == target_id`` 那一层的返回值：journal 链哈希、
    成员快照哈希、签名域、审计链哈希全部照旧走真实现——被控的只有"这份摘要长什么样"。
    """
    real = rb.hash_object

    def patched(obj: Any) -> str:
        if isinstance(obj, Mapping) and str(obj.get("id") or "") == target_id:
            return digest
        return real(obj)

    rb.hash_object = patched                      # type: ignore[assignment]
    try:
        yield
    finally:
        rb.hash_object = real                     # type: ignore[assignment]


def row(rid: str, *, parent: str | None = None, tool: str = "notes.write",
        action_type: str = "write", status: str = "succeeded", risk: str = "medium",
        stamp: str = "2026-11-14T23:00:00Z") -> dict:
    """种子记录（口径同 ``test_r1_acceptance.row``：剥掉签名/链字段，落盘时由服务端补）。"""
    record = copy.deepcopy(BASE)
    record.update({"id": rid, "action_type": action_type, "status": status,
                   "risk_level": risk, "timestamp": stamp, "parent_action_id": parent,
                   "user_intervention": None, "labels": []})
    record["tool_used"] = {"name": tool, "version": "1.0", "provider": "local",
                           "call_id": f"call-{rid}", "params_summary": "R5-3b 掩码豁免用例",
                           "result_ref": None}
    for key in ("signature", "entry_hash", "prev_hash", "tsa_timestamp", "agent_pubkey",
                "human_owner", "approval", "sequence_no", "rollback_state"):
        record.pop(key, None)
    return record


def durable_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 1. 单元级：豁免判据 + 载荷（规则本体未放宽）
# --------------------------------------------------------------------------- #

class AnchorExemptionUnitTests(unittest.TestCase):
    def test_controlled_digests_are_masked_by_the_rule_body(self) -> None:
        """受控触发前提成立：这 6 份摘要在**规则本体**下必被掩码（样本没失效）。

        钉住"样本会被咬"，豁免测试才不是在测空气：改前 ``redact_text`` 对每份摘要
        都留星（PM-VERDICT-R5-3 §4 的实锤形态）。
        """
        self.assertEqual(6, len(TRIGGER_DIGESTS))
        for digest in TRIGGER_DIGESTS:
            run = long_run(digest)
            self.assertGreaterEqual(len(run), 15,
                                    f"受控样本 {digest} 的连续数字串只有 {len(run)} 位")
            masked = au.redact_text(digest)
            self.assertNotEqual(digest, masked, f"规则本体没啃动 {digest}，样本失效")
            self.assertIn("*", masked)
            self.assertNotIn(digest, masked, "规则本体被放宽了：整串还在")

    def test_anchor_digest_field_lands_verbatim_in_payload(self) -> None:
        """锚定摘要在 ``build_event(extra=…)`` 载荷里逐字存在（6 份受控摘要逐个验）。"""
        for digest in TRIGGER_DIGESTS:
            payload = anchor_event(digest)
            self.assertEqual(digest, payload.get("snapshot_sha256"),
                             "fields.snapshot_sha256 被掩码：TP-12 的锚是坏的")
            # 第二条入口：extra 落盘进 fields 桶前还要过一遍 redact_params（字典键那条）
            self.assertEqual(digest,
                             au.redact_params(anchor_fields(digest))["snapshot_sha256"],
                             "fields 桶那条入口没豁免（dict 键路径漏了）")

    def test_other_anchor_fields_keep_the_established_masking(self) -> None:
        """同事件其余字段逐字不变：``journal_id`` 照旧掩码（铁律 3，不顺手放开）。"""
        payload = anchor_event(TRIGGER_DIGESTS[0])
        self.assertEqual(au.redact_params(JOURNAL_ID), payload.get("journal_id"),
                         "journal_id 的落盘掩码是既定口径，本轮不许动")
        self.assertNotIn(JOURNAL_ID, au.canonical_json(payload))
        self.assertIn("rbj_20**", str(payload.get("journal_id")))
        # 无长数字的字段不受影响（逐字原样，掩码面没有扩大）
        self.assertEqual("act-r53b-rollback", payload.get("rollback_record_id"))
        self.assertEqual("rollback.anchored", payload.get("rollback_event"))
        self.assertEqual("succeeded", payload.get("journal_phase"))
        self.assertEqual(TARGET, payload.get("action_id"))

    def test_shape_gate_keeps_pii_out_of_the_exemption(self) -> None:
        """形状闸门：纯数字串与散文**不豁免**（PII 防线不给豁免让路）。"""
        cases = [
            (CARD, "19 位纯数字（卡号形状：全数字 → 字母闸门拒收）"),
            ("13800138000", "11 位纯数字（手机号形状）"),
            ("110101199001011234", "18 位纯数字（身份证形状）"),
            (f"卡号 {CARD} 邮箱 {EMAIL}", "同键塞进来的散文"),
            (EMAIL, "邮箱"),
            ("C:\\Users\\alice\\notes.txt", "Windows 路径"),
            ("0123456", "7 位 hex（低于形状下限）"),
            ("f" * 65, "65 位 hex（超上限）"),
            ("zzzzzzzzzzzzzzzzzzzz", "非 hex"),
            (1234567890123, "非字符串"),
            (None, "None"),
        ]
        for value, why in cases:
            self.assertFalse(au.is_anchor_digest("snapshot_sha256", value),
                             f"{why} 被当成摘要豁免了")
            got = au.redact_params({"snapshot_sha256": value})["snapshot_sha256"]
            if isinstance(value, str) and long_run(value):
                self.assertNotIn(value, au.canonical_json(got),
                                 f"{why} 逐字落盘 = PII 防线破了")
                self.assertIn("*", str(got))
            self.assertFalse(au.is_anchor_digest("journal_id", TRIGGER_DIGESTS[0]),
                             "非白名单键蹭到了豁免")
            self.assertFalse(au.is_anchor_digest("snapshot_sha256", "abc"),
                             "长度低于 8 位的值蹭到了豁免")

    def test_pii_rule_body_is_not_relaxed(self) -> None:
        """铁律 2：掩码规则本体一字未放宽（阈值、顺序、free text 面全在）。"""
        # ① 规则函数自己：同一份摘要照旧掩码（豁免只在字段级，不发生在 redact_text 里）
        for digest in TRIGGER_DIGESTS:
            self.assertNotEqual(digest, au.redact_text(digest))
        # ② 自由文本面：reason / note / changed_fields / 非敏感参数值里的 15 位数字串
        prose = f"卡号 {CARD} 已核验，联系 {EMAIL}"
        payload = au.build_event(
            "decision", actor_id="alice", decision="approve", action_id="act-x",
            reason=prose, note=prose,
            changed_fields=[{"name": "card", "before": CARD, "after": CARD}],
            original_params={"card_no_plain": CARD, "note": prose})
        text = au.canonical_json(payload)
        self.assertNotIn(CARD, text, "free text 的 long_digits 掩码被放宽了")
        self.assertNotIn(EMAIL, text)
        self.assertIn("*", str(payload.get("reason")))
        self.assertIn("*", str(payload.get("note")))
        for item in payload.get("changed_fields") or []:
            self.assertIn("*", str(item.get("before")))
        # ③ 阈值未动：10 位放行、11 位打码（口径逐字不变）
        self.assertEqual("1234567890", au.redact_text("1234567890"))
        self.assertNotIn("12345678901", au.redact_text("编号 12345678901 已登记"))
        # ④ 规则表本体：规则名与阈值都没被改（防"顺手调阈值"）
        self.assertIn("long_digits", au.PII_PATTERNS)
        self.assertEqual("(?<![\\d.*\\-])\\d{11,20}(?!\\d)",
                         au.PII_PATTERNS["long_digits"].pattern)
        self.assertFalse(au.is_anchor_digest("api_key", "aabbccddeeff0011"),
                         "敏感键不得蹭豁免（敏感键整值打码优先）")

    def test_is_anchor_digest_is_the_single_judgement_point(self) -> None:
        """豁免面只有一个判据：键名归一化 + hex 形状 + 字母闸门，三者缺一不可。"""
        digest = TRIGGER_DIGESTS[0]
        self.assertTrue(au.is_anchor_digest("snapshot_sha256", digest))
        self.assertTrue(au.is_anchor_digest("snapshotSha256", digest),
                        "键名按 _key_norm 归一化（口径同 SENSITIVE_KEYS）")
        self.assertTrue(au.is_anchor_digest("snapshot-sha256", digest))
        self.assertTrue(au.is_anchor_digest("snapshot_sha256", digest.upper()))
        self.assertEqual(frozenset({"snapshot_sha256"}), au.ANCHOR_DIGEST_FIELDS,
                         "白名单只有一个键：本轮不扩面")
        # 边角：全数字 sha256 会被字母闸门挡下（此时摘要仍被掩码）。真实 sha256
        # 全数字的概率 = 0.625^64 ≈ 8.6e-14，240 例 sweep + 20000 例实测均 0 例
        # （宁可对这一缕摘要保留掩码，也不给纯数字的 PII 形状开门）。
        self.assertFalse(au.is_anchor_digest("snapshot_sha256", "0" * 64))


# --------------------------------------------------------------------------- #
# 2. 落盘级：jsonl 原文逐字（TP-12 口径）+ 链复算 + 幂等
# --------------------------------------------------------------------------- #

class AnchorDurableChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir("r53b-durable")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_durable_jsonl_carries_the_digest_verbatim(self) -> None:
        """落盘 jsonl **原文**逐字含摘要（TP-12 的取证面），链与哈希不受影响。"""
        digest = TRIGGER_DIGESTS[0]
        path = self.root / "audit.jsonl"
        log = au.open_log(path)
        entry = log.append(anchor_event(digest))
        raw = path.read_text(encoding="utf-8")
        self.assertIn('"rollback.anchored"', raw)
        self.assertIn(digest, raw, "锚定摘要没逐字进链（TP-12 口径）")
        self.assertNotIn("*", str((entry.get("fields") or {})["snapshot_sha256"]),
                         "摘要字段里出现了掩码星号")
        self.assertEqual(digest, (entry.get("fields") or {})["snapshot_sha256"])
        # 链校验 + entry_hash 独立复算（豁免不许弄断链）
        report = au.open_log(path).verify(raise_on_tamper=False)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(1, int(report["count"]))
        row = durable_rows(path)[-1]
        recomputed = au.entry_hash({k: v for k, v in row.items()
                                    if k not in ("hash", "entry_hash", "prev_hash")},
                                   str(row["prev_hash"]))
        self.assertEqual(recomputed, row["hash"], "链哈希复算必须对得上（豁免不改链结构）")
        self.assertEqual(row["hash"], row["entry_hash"])
        self.assertEqual(digest, (row.get("fields") or {})["snapshot_sha256"])

    def test_fields_bucket_path_is_exempted_too(self) -> None:
        """第二条写盘入口（裸 dict → ``_coerce_payload`` 的 ``fields`` 桶）也逐字。

        锚定事件走 ``build_event`` 时摘要在载荷顶层；调用方直接 ``append(dict)`` 时
        摘要在 ``fields`` 桶里生成。两条路都必须认字段级豁免，不能只钉一条。
        """
        digest = TRIGGER_DIGESTS[1]
        path = self.root / "fields-bucket.jsonl"
        log = au.open_log(path)
        log.append({"event": "rollback.anchored", "actor_id": "local",
                    "actor_role": "local", **anchor_fields(digest)})
        raw = path.read_text(encoding="utf-8")
        self.assertIn(digest, raw, "fields 桶那条路仍在掩码摘要")
        self.assertEqual(digest, (durable_rows(path)[-1].get("fields") or {})["snapshot_sha256"])
        self.assertEqual(au.redact_params(JOURNAL_ID),
                         (durable_rows(path)[-1].get("fields") or {})["journal_id"],
                         "同桶的 journal_id 照旧掩码")

    def test_redaction_stays_idempotent_after_the_exemption(self) -> None:
        """幂等：豁免过的摘要重复跑不套娃，历史那种已掩码值也不二次掩码。"""
        digest = TRIGGER_DIGESTS[2]
        once = au.redact_params(anchor_fields(digest))
        self.assertEqual(once, au.redact_params(once), "重复脱敏必须逐字稳定")
        self.assertEqual(digest, once["snapshot_sha256"])
        legacy = au.redact_text(digest)           # 改前那种被掩码的历史形态
        self.assertIn("*", legacy)
        self.assertEqual(legacy, au.redact_params({"snapshot_sha256": legacy})["snapshot_sha256"],
                         "已脱敏标记不许再套一层（历史行读侧口径不变）")
        self.assertEqual(legacy, au.redact_text(legacy))

    def test_every_real_sha256_lands_verbatim(self) -> None:
        """**无论哈希内容如何**都逐字进链（TASK §2.3 二选一的后者）。

        240 份真实形状的 sha256 逐条写盘：其中约 11% 含 ≥11 位连续数字（改前必被掩码），
        改后 240/240 逐字。断言前提里没有"这轮哈希运气好"这一项：随机性被形状闸门消掉，
        不是被固定种子藏起来。
        """
        path = self.root / "sweep.jsonl"
        log = au.open_log(path, verify_before_append=False)
        digests = [digest_of(f"sweep-{i}") for i in range(240)]
        would_mask = [d for d in digests if au.redact_text(d) != d]
        self.assertGreater(len(would_mask), 0, "样本里一个会被掩码的都没有，测了空气")
        for digest in digests:
            self.assertTrue(au.is_anchor_digest("snapshot_sha256", digest),
                            f"{digest} 过不了形状闸门：豁免面对真摘要不成立")
            log.append(anchor_event(digest))
        raw = path.read_text(encoding="utf-8")
        for digest in digests:
            self.assertIn(digest, raw, "这份摘要没逐字进链")
        rows = durable_rows(path)
        self.assertEqual(240, len(rows))
        self.assertEqual(digests, [str((r.get("fields") or {})["snapshot_sha256"])
                                   for r in rows])
        self.assertTrue(au.open_log(path).verify(raise_on_tamper=False)["ok"],
                        "240 条锚定事件写盘后链必须照旧通过")


# --------------------------------------------------------------------------- #
# 3. 端到端：服务端回滚 → journal → 审计链（TP-12 全链路）
# --------------------------------------------------------------------------- #

class AnchorEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = scratch_dir("r53b-e2e")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def service(self, name: str, rows: list[dict]) -> sv.InterveneService:
        root = self.root / name
        root.mkdir(parents=True, exist_ok=True)
        svc = sv.InterveneService(
            records=[], features_root=root / "features", sign_on_write=True,
            records_store_path=root / "records.jsonl",
            audit_log_path=root / "audit.jsonl",
            settings_path=root / "settings.json",
            auth_state_path=root / "auth.json",
            ingest_token_path=root / "ingest.json")
        for item in copy.deepcopy(rows):
            svc.store.put(svc.sign_on_write_record(item))
        return svc

    @staticmethod
    def seed() -> list[dict]:
        return [row(TARGET, tool="filesystem.remove"),
                row(CHILD, parent=TARGET)]

    def test_controlled_anchor_end_to_end_is_verbatim_for_every_seed(self) -> None:
        """受控 15-17 位数字摘要 → 真回滚 → 落盘审计原文逐字含摘要（6 个样本各一次）。

        这条就是 TP-12 在全量里随机触发的那条断言的**必然触发**版本：哈希内容由
        ``forced_snapshot_digest`` 钉死，不再靠运气（PM-VERDICT-R5-3 §4 的实锤形态）。
        """
        for index, digest in enumerate(TRIGGER_DIGESTS):
            svc = self.service(f"seed-{index}", self.seed())
            with forced_snapshot_digest(TARGET, digest):
                out = svc.rollback_execute({"action_id": TARGET, "confirm_all": True},
                                           actor_id="qa-r53b", actor_role="admin",
                                           request_id=f"r53b-{index}")
            self.assertTrue(out.get("ok"), out)
            entry = svc.rollback_service.journal()[-1]
            self.assertEqual(digest, entry["snapshot_sha256"], "journal 侧摘要必须是受控值")
            raw = (self.root / f"seed-{index}" / "audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"rollback.anchored"', raw)
            self.assertIn(digest, raw,
                          f"锚定摘要落盘被掩码（受控 {long_run(digest)}）：TP-12 破")
            anchors = [r for r in durable_rows(self.root / f"seed-{index}" / "audit.jsonl")
                       if (r.get("fields") or {}).get("rollback_event") == "rollback.anchored"]
            self.assertEqual(1, len(anchors))
            fields = anchors[0].get("fields") or {}
            self.assertEqual(digest, fields["snapshot_sha256"])
            self.assertEqual(au.redact_params(str(entry["id"])), fields["journal_id"],
                             "journal_id 掩码口径逐字不变（铁律 3）")
            self.assertNotIn(str(entry["id"]), raw, "journal_id 不许逐字进链（既定口径）")
            # 链与签名：豁免不许弄断审计链、journal 链与记录验签
            self.assertTrue(svc.audit.verify()["ok"])
            self.assertTrue(svc.rollback_service.journal_chain_verify()["ok"])
            saved = svc.store.get(str(entry["rollback_record_id"]))
            self.assertIsNotNone(saved)
            self.assertTrue(svc.secretariat.verify_record(saved)["ok"],
                            svc.secretariat.verify_record(saved))

    def test_real_hash_anchor_matches_independent_recomputation(self) -> None:
        """不 mock：真实摘要逐字进链，且取证端能独立复算（锚定可复核性回到位）。"""
        svc = self.service("real-hash", self.seed())
        out = svc.rollback_execute({"action_id": TARGET, "confirm_all": True},
                                   actor_id="qa-r53b", actor_role="admin",
                                   request_id="r53b-real")
        self.assertTrue(out.get("ok"), out)
        entry = svc.rollback_service.journal()[-1]
        digest = str(entry["snapshot_sha256"])
        recomputed = hashlib.sha256(
            json.dumps(entry["snapshot"], sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(recomputed, digest, "摘要 = 规范 JSON 的 sha256（独立复算）")
        self.assertEqual(fs.hash_object(entry["snapshot"]), digest)
        raw = (self.root / "real-hash" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn(digest, raw, "真实摘要未逐字进链")
        self.assertEqual(digest, out["snapshot_sha256"], "响应与 journal 条目同值")
        self.assertTrue(au.open_log(self.root / "real-hash" / "audit.jsonl")
                        .verify(raise_on_tamper=False)["ok"])

    def test_signature_domain_untouched_by_the_exemption(self) -> None:
        """铁律 1：签名域 11 字段一字未动，豁免只在审计写路径上（零链/签名改动）。"""
        self.assertEqual((
            "timestamp", "agent_id", "action_type", "tool", "input_hash", "output_hash",
            "prev_hash", "entry_hash", "risk_level", "confidence", "status",
        ), sec.SIGNATURE_FIELDS, "SIGNATURE_FIELDS 一字未动")
        self.assertEqual(11, len(sec.SIGNATURE_FIELDS))
        svc = self.service("sig", self.seed())
        for rid in (TARGET, CHILD):
            self.assertTrue(svc.secretariat.verify_record(svc.store.get(rid))["ok"], rid)
        out = svc.rollback_execute({"action_id": TARGET, "confirm_all": True},
                                   actor_id="qa-r53b", actor_role="admin",
                                   request_id="r53b-sig")
        self.assertTrue(out.get("ok"), out)
        for rid in (TARGET, CHILD):
            self.assertTrue(svc.secretariat.verify_record(svc.store.get(rid))["ok"],
                            f"{rid} 回滚后仍可验签（签名域零改动）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
