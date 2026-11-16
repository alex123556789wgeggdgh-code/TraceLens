"""R3-2（REQ-003 B3-2 前置）验收测试：**反事实解释接线**与 `causal_explanation` 落账
+ T13 目录缺口的**前置微修**（全新数据根直接可用）。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：

    cd agent-explainer
    python -m unittest tests.test_counterfactual_wiring -v

钉住的口径（PM 裁决 TASK-R3-2 §2，落 docs/07 §11.19）：

1. **接线共点**：因果解释与 R3-1 的证据接线在**同一个** `_attach_decision_evidence`
   单点、同一份决定产物、**同一次**「记录更新 + 重签」——一次 `store.put`、
   一帧广播，`multimedia_evidence` 与 `causal_explanation` 落在同一个签名 body 里。
2. **模板纪律（0.4 反编造）**：文案全部来自输入记录——`alternatives[].option` 逐字是
   `intervention_options[].label`、`expected` 逐字是候选自带的 `consequence`；
   主句里的参数改动逐字是 `modified_fields` 的 `name`/`before`/`after`。
   记录里没有后果文案 → 句子明说"记录里没有写它的后果"，**不替记录编一句想象**。
3. **零误触发**：没有"没走的路"（单候选 + 参数没改）→ 不产解释、不进分母，只记原因码；
   `rejected` 不在决策完成口径内。
4. **离线可复算**：固定时钟下同一输入两次产出逐字相同（同一条 template 路径）。
5. **诚实口径**：`llm` 钩子预留不接（0.6 不接真模型）——产品路径传进去的 `llm` 恒为
   `None`，落账的 `model` 恒为 `template`；schema :1257 是封闭形状，**没有**
   `provided_by` 这个属性，所以"谁生成的"由 `model` 承担（模板 ≠ LLM），
   绝不靠新增字段绕 schema（schema 零改动是铁律）。
6. **签名覆盖**：落账后 `verify_record.ok=true`；把 `counterfactual` 改一个字符
   → `entry_hash` 复算失效 → 验签不过（`entry_hash` ∈ `SIGNATURE_FIELDS`，schema :1090）。
7. **前置微修（PM-VERDICT-R3-1 §3.3 遗留 3）**：`EvidenceService._store_payload`
   写盘前幂等 `mkdir(parents=True, exist_ok=True)` → 全新数据根上 T13 主动上传直接可用。
8. **零新路由 / 零 schema 改动 / 零签名域改动 / 前端零改动 / 零新依赖**。
9. **R3-4 追加（§E2 · L1 已批前置微修，PM-VERDICT-R3-2 §3.1 裁决 1）**：T17
   `evidence_causal` 写回走 D-resign 那条既有重签通道——写回后 `verify_record.ok=true`
   且 `prev_hash` 接上改前那条（链连续）；修复前的裸 `put` 形状当场红；
   自动接线通道零改动（生成侧分母一格不多）。

临时目录一律 `tests/scratch.py`（PM-VERDICT-R1-2a §3.3）；HTTP 断言级不重试
（`http_fixture`）。每个 HTTP 用例一台全新的签名服务器：因果解释的生成侧台账是
**服务实例级**，共用服务器会让用例互相攒次数，"一次决定 = 一次生成尝试"就钉不住。
"""
from __future__ import annotations

import base64
import copy
import http.client
import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import evidence as ev_mod          # noqa: E402
from agent_explainer import evidence_wiring as ew        # noqa: E402
from agent_explainer import ingest as ingest_mod        # noqa: E402
from agent_explainer import secretariat as sec_mod      # noqa: E402
from agent_explainer import server as sv                # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from jsonschema_mini import MiniValidator               # noqa: E402
import http_fixture as fx                               # noqa: E402
from scratch import scratch_dir, use_scratch_tempfile    # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
CAUSAL_SCHEMA = SCHEMA["properties"]["causal_explanation"]

BASE_TARGET = "act-demo-0004"        # medium 风险、3 个候选（每个都自带 consequence）、可改参数
PARAM = "max_move_count"             # 夹具里 default=188 的那个可编辑参数
CF_PREFIX = "act-r3c-"               # 本文件独占的夹具记录 id 前缀
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TINY_PNG = base64.b64encode(PNG_MAGIC + b"r32-tiny-payload").decode("ascii")


# --------------------------------------------------------------------------- #
# 夹具：走产品路径构造"决定之后"的记录（决定段是官方账，不手搓）
# --------------------------------------------------------------------------- #

def target(idx: int, *, options: list[dict] | None = None,
           language: str | None = None) -> dict:
    """一条**可决**的目标记录（演示夹具 act-demo-0004 的副本，换 id、清决定段）。"""
    rec = copy.deepcopy(next(r for r in NATIVE if r["id"] == BASE_TARGET))
    rec["id"] = f"{CF_PREFIX}{idx:02d}"
    rec["sequence_no"] = 900 + idx
    rec["status"] = "pending_confirmation"
    rec["requires_action"] = True
    rec["user_intervention"] = None
    rec["multimedia_evidence"] = []
    rec.pop("causal_explanation", None)
    if options is not None:
        rec["intervention_options"] = copy.deepcopy(options)
    if language is not None:
        rec["human_readable"] = dict(rec.get("human_readable") or {})
        rec["human_readable"]["language"] = language
    rec["execution_receipt"] = {"state": "unreported", "reported_by": None,
                                "reported_at": None, "detail": "夹具：无回执",
                                "sdk_version": None,
                                "effect_notice": "intervention.effect.recorded_only"}
    return rec


def decided(idx: int, decision: str, *, params: dict | None = None,
            reason: str | None = None, options: list[dict] | None = None,
            option_id: str | None = None, language: str | None = None) -> dict:
    """``apply_decision`` 的产物 = 决定落盘后的记录形状。"""
    return sv.apply_decision(target(idx, options=options, language=language), decision,
                             modified_params=params, reason=reason, option_id=option_id,
                             actor_id="local-user", actor_role="local")


def wiring_on_scratch(case: str, *, now=None) -> tuple[ew.EvidenceWiring, pathlib.Path]:
    """挂在临时目录上的因果解释接线（账本走 `tests/scratch.py`，整目录删）。"""
    root = scratch_dir(case)
    paths = FeaturePaths(root / "features")
    paths.ensure()
    return ew.EvidenceWiring(ev_mod.EvidenceService(paths, now=now)), root


def service(root: pathlib.Path, records: list[dict] | None = None, **over) -> sv.InterveneService:
    return sv.InterveneService(records=copy.deepcopy(records if records is not None else NATIVE),
                               features_root=root / "features",
                               sign_on_write=True, **over)


def fresh_service(root: pathlib.Path, records: list[dict] | None = None) -> sv.InterveneService:
    """**全新数据根**上的服务：features 目录连一层都不建（前置微修的现场）。"""
    return sv.InterveneService(records=copy.deepcopy(records if records is not None else NATIVE),
                               features_root=root / "brand-new" / "features",
                               sign_on_write=True)


def http_request(host: str, port: int, method: str, path: str, body=None,
                 timeout: float = 8.0) -> tuple[int, dict, object]:
    """一发一收（连接生命周期交给 tests/http_fixture；非幂等动词绝不重放）。"""
    def once(conn: http.client.HTTPConnection) -> tuple[int, dict, object]:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        headers = {"Connection": "close"}
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        heads = {str(k).lower(): v for k, v in response.getheaders()}
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = raw.decode("utf-8", "replace")
        return response.status, heads, data

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


def labels_of(record: dict) -> list[str]:
    return [str(opt.get("label") or "") for opt in record.get("intervention_options") or []]


def consequences_of(record: dict) -> dict:
    return {str(opt.get("label") or ""): str(opt.get("consequence") or "")
            for opt in record.get("intervention_options") or []}


# --------------------------------------------------------------------------- #
# A. 触发口径：什么该产反事实、什么绝不产（零误触发）
# --------------------------------------------------------------------------- #

class CausalTriggerGateTests(unittest.TestCase):
    """§2.1 反事实条件：有被拒的备选 / 有明确 before→after 参数变化，二者任一成立才触发。"""

    def setUp(self) -> None:
        self.wiring, self.root = wiring_on_scratch("causal-gate")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_trigger_statuses_are_shared_with_the_r3_1_decision_set(self) -> None:
        """§2.1 与 R3-1 **同一份**"决策完成"口径（不另立第二套），``rejected`` 不在内。"""
        self.assertEqual(ew.TRIGGER_STATUSES, ew.CAUSAL_TRIGGER_STATUSES)
        self.assertNotIn("rejected", ew.CAUSAL_TRIGGER_STATUSES)
        self.assertEqual(ew.SKIP_REASONS, ew.CAUSAL_SKIP_REASONS)

    def test_modify_decision_triggers_on_both_conditions(self) -> None:
        """modify：未选候选（approve/reject 两条）+ 参数被改 → 触发，两个原因码都在。"""
        rec = decided(1, "modify", params={PARAM: 50})
        trigger = ew.causal_trigger(rec)
        self.assertIsNotNone(trigger)
        self.assertEqual(["rejected_alternative", "param_change"], trigger["reasons"])

    def test_approve_decision_triggers_on_rejected_alternatives(self) -> None:
        """approve：参数没改，但计划里有"没走的路" → 照样触发（反事实来自备选）。"""
        rec = decided(2, "approve")
        trigger = ew.causal_trigger(rec)
        self.assertEqual(["rejected_alternative"], (trigger or {}).get("reasons"))
        self.assertEqual([], ew.param_changes(rec))

    def test_single_option_without_param_change_triggers_nothing(self) -> None:
        """零误触发：只有 1 个候选 + 参数没改 → 没有"没走的路"，不产解释、不进分母。"""
        solo = [{"id": "opt-solo", "kind": "approve", "label": "只有这条路"}]
        rec = decided(3, "approve", options=solo)
        self.assertIsNone(ew.causal_trigger(rec))
        plan = self.wiring.causal(rec)
        self.assertIsNone(plan["explanation"])
        self.assertTrue(plan["skipped"])
        self.assertEqual("no_trigger_condition", plan["reason"])
        self.assertEqual(0, self.wiring.causal_attempts)
        self.assertEqual(1, self.wiring.causal_skip_reasons.get("no_trigger_condition"))

    def test_rejected_status_is_outside_the_causal_trigger_set(self) -> None:
        """"拦下"不产解释：status=rejected → 零触发，生成侧分母不涨（与 R3-1 同一裁决）。"""
        rec = decided(4, "reject", reason="先别动")
        self.assertEqual("rejected", rec["status"])
        plan = self.wiring.causal(rec)
        self.assertIsNone(plan["explanation"])
        self.assertEqual("status_not_completed", plan["reason"])
        self.assertEqual(0, self.wiring.causal_attempts)
        self.assertEqual(1, self.wiring.causal_skip_reasons.get("status_not_completed"))

    def test_record_without_option_id_counts_every_option_as_unchosen(self) -> None:
        """记录里没有 ``option_id`` → **不猜**选了谁：全部候选都算未选中（如实反映缺口）。"""
        rec = decided(5, "approve")
        rec["user_intervention"]["option_id"] = None
        unchosen = ew.unchosen_options(rec)
        self.assertEqual(len(rec["intervention_options"]), len(unchosen))

    def test_unchosen_options_exclude_the_chosen_one_verbatim(self) -> None:
        """剔除规则只看记录自己的账：命中的那条是"走了的路"，其余逐字留下。"""
        rec = decided(6, "modify", params={PARAM: 20})
        chosen = str(rec["user_intervention"]["option_id"])
        chosen_label = next(str(opt["label"]) for opt in rec["intervention_options"]
                            if opt["id"] == chosen)
        unchosen = ew.unchosen_options(rec)
        self.assertNotIn(chosen_label, [row["option"] for row in unchosen])
        self.assertEqual([label for label in labels_of(rec) if label != chosen_label],
                         [row["option"] for row in unchosen])

    def test_param_changes_share_the_param_diff_pair_source_order(self) -> None:
        """被拒参数的取数顺序与 ``param_diff_pair`` 逐字同一口径：官方账优先，
        没有 ``modified_fields`` 时退到 params 整对象差集。"""
        rec = decided(7, "modify", params={PARAM: 33})
        self.assertEqual([{"name": PARAM, "before": "188", "after": "33"}],
                         ew.param_changes(rec))
        rec["user_intervention"].pop("modified_fields")
        self.assertEqual([{"name": PARAM, "before": "188", "after": "33"}],
                         ew.param_changes(rec))

    def test_params_supplied_but_unchanged_yield_no_param_change(self) -> None:
        """传了参数但逐字没改 → 没有"被拒的那一份"，参数侧不列（零误触发）。"""
        rec = target(8)
        rec["status"] = "modified"
        rec["user_intervention"] = {
            "decision": "modify", "option_id": "opt-only",
            "original_params": {PARAM: 188}, "modified_params": {PARAM: 188}}
        rec["intervention_options"] = [{"id": "opt-only", "kind": "modify", "label": "改一下"}]
        self.assertEqual([], ew.param_changes(rec))
        self.assertIsNone(ew.causal_trigger(rec))

    def test_causal_ledger_counts_generated_side_only(self) -> None:
        """台账分工：生成侧在 ``causal_facts()``（attempts/generated/skips/truncated），
        零触发**不进分母**（口径逐字沿用 R3-1，不混在证据分母里）。"""
        self.wiring.causal(decided(9, "approve"))
        self.wiring.causal(decided(10, "approve", options=[{"id": "a", "kind": "approve",
                                                            "label": "只有一条"}]))
        facts = self.wiring.causal_facts()
        self.assertEqual(1, facts["attempts"])
        self.assertEqual(1, facts["generated"])
        self.assertEqual(1, facts["skips"])
        self.assertEqual(0, facts["failures"])
        self.assertEqual(0, self.wiring.attempts, "因果解释不进 B3-1 证据分母")


# --------------------------------------------------------------------------- #
# B. 模板纪律（0.4 反编造）：文案每个字都来自输入记录，可离线复算
# --------------------------------------------------------------------------- #

class TemplateDisciplineTests(unittest.TestCase):
    """§1.2 模板纪律：反事实文案逐字来自记录，缺字段就说"记录里没写"，不编造。"""

    def setUp(self) -> None:
        self.wiring, self.root = wiring_on_scratch("causal-template",
                                                   now=lambda: "2026-01-01T00:00:00Z")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_alternatives_are_verbatim_from_the_record(self) -> None:
        """正题：``option`` 逐字是候选 ``label``、``expected`` 逐字是候选自带的
        ``consequence``（schema :2209 的"点这个会发生什么"），一字不改。"""
        rec = decided(11, "approve")
        explanation, truncated = ew.causal_explanation(self.wiring.service, rec)
        self.assertEqual(0, truncated)
        table = consequences_of(rec)
        chosen = str(rec["user_intervention"]["option_id"])
        chosen_label = next(str(opt["label"]) for opt in rec["intervention_options"]
                            if opt["id"] == chosen)
        rows = {row["option"]: row for row in explanation["alternatives"]}
        for label, consequence in table.items():
            if label == chosen_label:
                continue
            self.assertIn(label, rows, "未选候选的 label 必须逐字出现在 option 里")
            self.assertEqual(consequence, rows[label]["expected"],
                             "expected 必须逐字是记录自带的 consequence")

    def test_counterfactual_quotes_the_record_param_change(self) -> None:
        """主句追加的事实小句里，参数名与前后值逐字来自 ``modified_fields``。"""
        rec = decided(12, "modify", params={PARAM: 60})
        explanation, _ = ew.causal_explanation(self.wiring.service, rec)
        text = explanation["counterfactual"]
        self.assertIn(PARAM, text)
        self.assertIn(f"{PARAM} 从 188 改成 60", text, "被拒参数逐字可查")
        self.assertIn("记录在案的决定是「modify」", text, "决定值取自记录，不是推断")

    def test_missing_consequence_is_reported_as_missing(self) -> None:
        """候选没写 ``consequence``/``description`` → ``expected`` 留空、
        句子明说"记录里没有写它的后果"：**绝不替记录编一句预期**。"""
        bare = [{"id": "opt-bare", "kind": "approve", "label": "裸选项"},
                {"id": "opt-bare-2", "kind": "modify", "label": "第二条裸选项"},
                {"id": "opt-bare-3", "kind": "reject", "label": "第三条裸选项"}]
        rec = decided(13, "approve", options=bare)
        explanation, _ = ew.causal_explanation(self.wiring.service, rec)
        self.assertEqual(["", ""], [row["expected"] for row in explanation["alternatives"][:2]])
        self.assertIn("记录里没有写它的后果", explanation["counterfactual"])
        self.assertNotIn("将会", explanation["counterfactual"], "不替记录编造想象后果")

    def test_template_is_offline_recomputable(self) -> None:
        """离线复算：同一输入两次调用逐字相同（固定时钟下连 ``generated_at`` 也定死）。"""
        rec = decided(14, "modify", params={PARAM: 21})
        first, _ = ew.causal_explanation(self.wiring.service, rec)
        second, _ = ew.causal_explanation(self.wiring.service, rec)
        self.assertEqual(first, second)
        self.assertEqual("2026-01-01T00:00:00Z", first["generated_at"])
        other = ew.EvidenceWiring(ev_mod.EvidenceService(self.wiring.service.paths,
                                                         now=lambda: "2026-01-01T00:00:00Z"))
        self.assertEqual(first, other.causal(rec)["explanation"],
                         "换一台同构接线也复算出同一份，不依赖实例状态")

    def test_model_is_template_because_llm_hook_is_reserved(self) -> None:
        """§1.2 诚实口径：模板路径 ``model=template``；``llm`` 钩子只在被显式注入时生效
        （0.6 只预留钩子，产品路径永不注入）。"""
        rec = decided(15, "approve")
        explanation, _ = ew.causal_explanation(self.wiring.service, rec)
        self.assertEqual("template", explanation["model"])

        def fake_llm(record, candidates, *, lang="zh"):
            return {"counterfactual": "hook-written", "alternatives": candidates,
                    "language": lang}

        hook, _ = ew.causal_explanation(self.wiring.service, rec, llm=fake_llm)
        self.assertEqual("llm", hook["model"], "注入钩子时如实标 llm（钩子确实在，没接死）")
        self.assertEqual("hook-written", hook["counterfactual"],
                         "LLM 路径的文案由模型自己写，接线不覆写")

    def test_generated_at_is_the_clock_not_a_fabricated_stamp(self) -> None:
        """服务时钟给不出时间戳时 ``generated_at`` 为 ``None``（schema 收 null）——
        **不编一个时间戳**冒充"生成于某刻"。"""
        blind = ev_mod.EvidenceService(self.wiring.service.paths, now=lambda: "")
        explanation, _ = ew.causal_explanation(blind, decided(16, "approve"))
        self.assertIsNone(explanation["generated_at"])
        self.assertEqual(ew.CAUSAL_KEYS, set(explanation))

    def test_language_follows_the_record(self) -> None:
        """语言跟记录自己的 ``human_readable.language``（与 R3-1 caption 同口径）。"""
        zh = ew.causal_explanation(self.wiring.service, decided(17, "approve"))[0]
        en = ew.causal_explanation(self.wiring.service,
                                   decided(18, "approve", language="en"))[0]
        self.assertEqual("zh", zh["language"])
        self.assertEqual("en", en["language"])
        self.assertIn("Not chosen", en["alternatives"][0]["why"], "en 记录出英文文案")
        self.assertIn("on-record decision", en["counterfactual"])

    def test_language_follows_the_request_when_given(self) -> None:
        """``lang`` 显式传入时以请求为准（T17 的 ``lang`` 就是这个语义）。"""
        rec = decided(19, "approve")                      # 记录自己是 zh
        forced = ew.causal_explanation(self.wiring.service, rec, lang="en")[0]
        self.assertEqual("en", forced["language"])
        self.assertIn("options not chosen", forced["counterfactual"])

    def test_no_facts_outside_the_record_in_the_sentence(self) -> None:
        """反编造的可核对形式：主句里每个「」引号内容都在记录里逐字存在
        （候选 label / 候选 consequence / 决定值——引号是"引用"，不是模板自由发挥）。"""
        rec = decided(20, "modify", params={PARAM: 90})
        explanation, _ = ew.causal_explanation(self.wiring.service, rec)
        text = explanation["counterfactual"]
        table = consequences_of(rec)
        intervention = json.dumps(rec["user_intervention"], ensure_ascii=False)
        quoted = [chunk.split("」")[0] for chunk in text.split("「") if "」" in chunk]
        self.assertTrue(quoted, "模板句应带记录字段的引用")
        for head in quoted:
            self.assertTrue(head in labels_of(rec) or head in table.values()
                            or f"\"{head}\"" in intervention or head == rec["id"],
                            f"主句引用了记录之外的内容：{head!r}")


# --------------------------------------------------------------------------- #
# C. schema 1257 形态合规（零 schema 改动：落账形状必须过封闭形状）
# --------------------------------------------------------------------------- #

class CausalSchemaShapeTests(unittest.TestCase):
    """§2.2 落账形态：schema :1257 的封闭形状 + 长度上限，逐字零改动。"""

    def setUp(self) -> None:
        self.wiring, self.root = wiring_on_scratch("causal-schema")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_module_limits_are_the_schema_numbers(self) -> None:
        """常量 == schema 里的数字（schema 一旦改动，本用例立刻红，不许悄悄漂）。"""
        self.assertEqual({"counterfactual", "alternatives", "model", "generated_at",
                          "language"}, set(CAUSAL_SCHEMA["properties"]))
        self.assertEqual(ew.CAUSAL_KEYS, set(CAUSAL_SCHEMA["properties"]))
        self.assertIs(False, CAUSAL_SCHEMA["additionalProperties"])
        self.assertEqual(["counterfactual"], CAUSAL_SCHEMA["required"])
        self.assertEqual(CAUSAL_SCHEMA["properties"]["counterfactual"]["maxLength"],
                         ew.CAUSAL_LIMITS["counterfactual"])
        self.assertEqual(CAUSAL_SCHEMA["properties"]["alternatives"]["maxItems"],
                         ew.CAUSAL_LIMITS["alternatives"])
        alt_props = CAUSAL_SCHEMA["properties"]["alternatives"]["items"]["properties"]
        self.assertEqual(alt_props["option"]["maxLength"], ew.CAUSAL_LIMITS["option"])
        self.assertEqual(alt_props["expected"]["maxLength"], ew.CAUSAL_LIMITS["expected"])
        self.assertEqual(alt_props["why"]["maxLength"], ew.CAUSAL_LIMITS["why"])
        self.assertEqual(["template", "llm"], CAUSAL_SCHEMA["properties"]["model"]["enum"])

    def test_landed_explanation_passes_the_record_schema(self) -> None:
        """落账形状整条记录过 ``action_record.schema.json``（MiniValidator 零 problems）。"""
        rec = decided(21, "modify", params={PARAM: 12})
        rec["causal_explanation"] = self.wiring.causal(rec)["explanation"]
        problems = VALIDATOR.collect(rec)
        self.assertEqual([], problems, problems)

    def test_closed_shape_leaves_no_room_for_provided_by(self) -> None:
        """§2.2 的 ``provided_by`` 在 schema :1257 里**没有**这个属性（封闭形状）：
        诚实口径由 ``model`` 承担，落账对象里不许出现它（schema 零改动）。"""
        rec = decided(22, "approve")
        explanation = self.wiring.causal(rec)["explanation"]
        self.assertNotIn("provided_by", explanation)
        self.assertEqual(ew.CAUSAL_KEYS, set(explanation))
        with_provided = dict(explanation)
        with_provided["provided_by"] = "runtime"
        self.assertTrue(VALIDATOR.collect({**rec, "causal_explanation": with_provided}),
                        "越界字段必须被 schema 拒绝（证明我们没有偷偷扩形状）")

    def test_alternatives_are_capped_and_truncation_is_counted(self) -> None:
        """备选超过 schema ``maxItems=6`` → 截到 6 条，**截掉的条数进台账**（不静默）。"""
        rec = decided(23, "modify", params={PARAM: 7})
        rec["user_intervention"]["modified_fields"] = [
            {"name": f"field_{n}", "before": n, "after": n + 1} for n in range(10)]
        explanation = self.wiring.causal(rec)
        self.assertEqual(ew.CAUSAL_LIMITS["alternatives"], len(explanation["explanation"]["alternatives"]))
        self.assertEqual(6, explanation["truncated"], "12 条留 6 条：截掉 6 条要如实登记")
        self.assertEqual(6, self.wiring.causal_truncated)

    def test_counterfactual_is_clipped_to_the_schema_limit(self) -> None:
        """超长参数与超长标签 → 主句裁到 schema ``maxLength=1000`` 并以省略号收尾。"""
        rec = decided(24, "modify", params={PARAM: 3})
        rec["user_intervention"]["modified_fields"] = [
            {"name": f"p{n}", "before": "x" * 400, "after": "y" * 400} for n in range(10)]
        explanation = self.wiring.causal(rec)["explanation"]
        self.assertLessEqual(len(explanation["counterfactual"]),
                             ew.CAUSAL_LIMITS["counterfactual"])
        self.assertTrue(explanation["counterfactual"].endswith("…"),
                        "裁剪必须留下裁剪痕迹，不许静默截字")

    def test_schema_string_form_is_still_accepted(self) -> None:
        """schema 的字符串形态（一句话反事实）仍是合法形态：既有形状零改动，
        接线只在对象形态上落账，不逼历史数据改形状。"""
        self.assertIn("string", CAUSAL_SCHEMA["type"])
        self.assertEqual([], VALIDATOR.collect({**decided(25, "approve"),
                                               "causal_explanation": "一句话反事实"}))


# --------------------------------------------------------------------------- #
# D. 落账与签名：与 R3-1 共点、一次重签、验签覆盖
# --------------------------------------------------------------------------- #

class CausalLandingSignatureTests(unittest.TestCase):
    """§2.1 接线共点 + §1.4 落账走「记录更新 + 重签」通道（签名覆盖由测试证明）。"""

    def setUp(self) -> None:
        self.root = scratch_dir("causal-landing")
        self.addCleanup(shutil.rmtree, self.root, True)
        rows = copy.deepcopy(NATIVE) + [target(i) for i in range(50, 70)]
        self.service = service(self.root, rows)

    def _decide(self, idx: int, decision: str = "modify", **extra) -> dict:
        record, _meta = self.service.intervene({"action_id": f"{CF_PREFIX}{idx:02d}",
                                                "decision": decision, **extra},
                                               request_id=f"req-r32-{idx}")
        return record

    def test_landing_resigns_and_verifies_ok(self) -> None:
        """落账后验签全绿：``signature=valid`` / ``entry_hash=valid`` /
        ``prev_hash=continuous``（D-resign 既有通道，签名域零改动）。"""
        record = self._decide(50, "modify", modified_params={PARAM: 40})
        self.assertTrue(record.get("causal_explanation"), "决定后记录里要有反事实")
        authoritative = self.service.store.get(record["id"])
        report = self.service.secretariat.verify_record(authoritative)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual("valid", report["checks"]["signature"])
        self.assertEqual("valid", report["checks"]["entry_hash"])
        self.assertEqual("continuous", report["checks"]["prev_hash"])
        self.assertEqual(0, self.service.sign_failures)

    def test_one_byte_tamper_of_the_explanation_breaks_verification(self) -> None:
        """把 ``counterfactual`` 改一个字符 → ``entry_hash`` 复算失效 → 验签不过：
        因果解释在签名覆盖范围内（``entry_hash`` ∈ SIGNATURE_FIELDS，schema :1090）。"""
        record = self._decide(51, "modify", modified_params={PARAM: 41})
        tampered = copy.deepcopy(self.service.store.get(record["id"]))
        text = tampered["causal_explanation"]["counterfactual"]
        tampered["causal_explanation"]["counterfactual"] = text[:-1] + ("！" if not
                                                                        text.endswith("！") else "。")
        report = self.service.secretariat.verify_record(tampered)
        self.assertFalse(report["ok"], "签名之后偷偷改掉解释必须当场红")
        self.assertIn(report["checks"]["entry_hash"], ("invalid", "mismatch"),
                      report["checks"])

    def test_explanation_shares_one_signed_body_with_the_evidence(self) -> None:
        """共点的可核对形式：证据与解释落在**同一次** ``store.put`` 的同一个 body 里
        （落账阶段只 put 一次、只重签一次），链尾不多出一格。"""
        puts: list[tuple[str, dict]] = []
        real_put = self.service.store.put

        def spy(record):
            puts.append((str(record.get("id") or ""), copy.deepcopy(dict(record))))
            return real_put(record)

        self.service.store.put = spy
        signed_before = self.service.signed_on_write
        record = self._decide(52, "modify", modified_params={PARAM: 42})
        self.service.store.put = real_put
        target_puts = [row for row in puts if row[0] == record["id"]]
        # 目标记录全程只被 put 两次：决定落盘/重签一次 + 证据与解释**合并**落账一次
        # （解释**没有**多出第三次落账；落账那一次就是最后一次，两个产物同在它的 body 里）
        self.assertEqual(2, len(target_puts), [row[0] for row in target_puts])
        landing = target_puts[-1][1]
        self.assertTrue(landing.get("multimedia_evidence"), "落账那次要带证据")
        self.assertTrue(landing.get("causal_explanation"), "同一次落账要带解释")
        self.assertFalse(target_puts[0][1].get("causal_explanation"),
                         "解释只在合并落账那一次出现，不是提前塞进决定")
        self.assertEqual(3, self.service.signed_on_write - signed_before,
                         "决定落盘签一次 + D-resign 一次 + 合并落账重签一次，不多签")
        self.assertEqual(1, self.service.evidence_wiring_records)
        self.assertEqual(1, self.service.causal_wiring_records)
        events = [row for row in self.service.store.all()
                  if str(row.get("id") or "").startswith("act_iv_")]
        self.assertEqual(1, len(events))
        self.assertEqual(record["entry_hash"], events[0]["prev_hash"],
                         "干预记录仍是链头：解释落账不能插到链尾之后")
        self.assertTrue(self.service.secretariat.verify_record(events[0])["ok"])

    def test_the_explanation_never_touches_the_facts_fields(self) -> None:
        """解释只动分析面：与同参数的纯 ``apply_decision`` 产物逐字段比对，
        事实字段与决定字段（``user_intervention``）必须逐字相同——解释不是决定。"""
        record = self._decide(53, "modify", modified_params={PARAM: 44})
        stored = self.service.store.get(record["id"])
        pure = decided(53, "modify", params={PARAM: 44})
        facts_fields = ("id", "sequence_no", "created_at", "action_type", "actor_id",
                        "agent_id", "tool_used", "risk_assessment", "intervention_options",
                        "user_intervention", "execution_receipt", "human_readable", "status")
        def plain(record: dict) -> dict:
            row = copy.deepcopy(dict(record))
            ui = row.get("user_intervention")
            if isinstance(ui, dict):
                # `decided_at` 是落盘时刻（两次构造必然差几秒）、`audit_entry_hash` 是决定
                # 落盘**之后**才回填的官方账：两者都不是解释能动的字段，比对时排除掉
                ui.pop("audit_entry_hash", None)
                ui.pop("decided_at", None)
                ui.pop("request_id", None)   # 请求号是决定流的管道，纯构造夹具没有
            hr = row.get("human_readable")
            if isinstance(hr, dict):
                hr.pop("generated_at", None)  # 人话层的生成时刻（微秒级），同样随钟表走
            return row
        pure_clean, stored_clean = plain(pure), plain(stored)
        for field in facts_fields:
            self.assertEqual(json.dumps(pure_clean.get(field), sort_keys=True, ensure_ascii=False),
                             json.dumps(stored_clean.get(field), sort_keys=True, ensure_ascii=False),
                             f"落账解释改动了事实/决定字段 {field}")
        self.assertTrue(stored.get("causal_explanation"), "分析面字段该动的都动了")
        self.assertTrue(stored["user_intervention"].get("audit_entry_hash"),
                        "审计锚点照常回填（解释接线不碰决定流的审计）")

    def test_causal_explanation_is_in_the_expansion_merge_whitelist(self) -> None:
        """§1.4 铁律：``causal_explanation`` 在 ``EXPANSION_MERGE_FIELDS`` 白名单内
        （与 ``multimedia_evidence`` 同通道），且不在受保护字段名单里。"""
        self.assertIn("causal_explanation", ingest_mod.EXPANSION_MERGE_FIELDS)
        self.assertNotIn("causal_explanation", ingest_mod.ALL_PROTECTED_FIELDS)

    def test_the_service_template_never_injects_an_llm(self) -> None:
        """产品路径的 spy：接线调 ``counterfactual`` 时 ``llm`` 恒为 ``None``
        （0.6 不接真模型，落账的一律是模板产物）。"""
        seen: dict = {}
        real = self.service.evidence_service.counterfactual

        def spy(record, candidates, *, lang="zh", llm=None):
            seen["llm"] = llm
            seen["lang"] = lang
            seen["candidates"] = candidates
            return real(record, candidates, lang=lang, llm=llm)

        self.service.evidence_service.counterfactual = spy
        record = self._decide(54, "modify", modified_params={PARAM: 43})
        self.assertIsNone(seen["llm"], "llm 钩子预留不接：产品路径绝不注入")
        self.assertTrue(seen["candidates"], "candidates 由接线从记录里取，不是空传")
        self.assertEqual("template", record["causal_explanation"]["model"])

    def test_wiring_cannot_fabricate_a_second_decision(self) -> None:
        """落账只动分析面字段：决定段（``user_intervention``）逐字不变，
        审计链照常验得过（解释不是决定，不能反噬决定流）。"""
        record = self._decide(55, "modify", modified_params={PARAM: 44})
        stored = self.service.store.get(record["id"])
        decided_rec = decided(55, "modify", params={PARAM: 44})
        self.assertEqual(decided_rec["user_intervention"]["decision"],
                         stored["user_intervention"]["decision"])
        self.assertEqual("modified", stored["status"])
        self.assertTrue(self.service.require_audit_chain()["ok"])
        self.assertTrue(self.service.verify_audit_chain()["ok"])


# --------------------------------------------------------------------------- #
# E. 端到端：/intervene 单点 + T13 前置微修 + 既有 evidence 面回归
# --------------------------------------------------------------------------- #

class CausalWiringHttpTests(unittest.TestCase):
    """产品口径：``POST /intervene`` → 生成 → 落账 → 重签 → 读侧可查（HTTP 只走环回）。"""

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"causal-http-{case}")
        rows = copy.deepcopy(NATIVE) + [target(i) for i in range(60, 80)]
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=rows, quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.host, self.port = self.server.server_address[:2]
        self.service = self.server.service
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def post(self, path: str, body: dict) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "POST", path, body)

    def get(self, path: str) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "GET", path)

    def stored(self, action_id: str) -> dict:
        status, _hd, data = self.get("/api/records")
        self.assertEqual(200, status, data)
        return next(r for r in data["records"] if r["id"] == action_id)

    def test_modify_intervene_lands_the_explanation(self) -> None:
        """端到端正题：modify 决定 → 响应体与 GET 都能看见 ``causal_explanation``，
        被拒参数与未选候选逐字可见，生成侧与落账侧计数各 1。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}60",
                                                     "decision": "modify",
                                                     "modified_params": {PARAM: 44}})
        self.assertEqual(200, status, data)
        explanation = data.get("causal_explanation")
        self.assertIsInstance(explanation, dict, data)
        self.assertEqual("template", explanation["model"])
        self.assertIn(f"{PARAM} 从 188 改成 44", explanation["counterfactual"])
        stored = self.stored(f"{CF_PREFIX}60")
        self.assertEqual(explanation, stored["causal_explanation"],
                         "读侧必须看见落账结果，不能只在响应里出现过")
        self.assertEqual(1, self.service.evidence_wiring.causal_attempts)
        self.assertEqual(1, self.service.evidence_wiring.causal_generated)
        self.assertEqual(1, self.service.causal_wiring_records)
        self.assertEqual(0, self.service.causal_wiring_records_failed)

    def test_approve_intervene_lands_explanation_from_rejected_alternatives(self) -> None:
        """approve 没有参数差异，也有反事实：内容来自"没走的路"（未选候选 label 逐字）。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}61",
                                                     "decision": "approve"})
        self.assertEqual(200, status, data)
        explanation = data["causal_explanation"]
        labels = labels_of(self.stored(f"{CF_PREFIX}61"))
        options = [row["option"] for row in explanation["alternatives"]]
        self.assertTrue(options, "备选清单不能空")
        for option in options:
            self.assertIn(option, labels, "备选必须逐字是计划里的候选")

    def test_reject_intervene_lands_no_explanation(self) -> None:
        """端到端零误触发：reject → 记录里没有解释，生成侧分母不涨，决定不受影响。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}62",
                                                     "decision": "reject",
                                                     "reason": "先不动文件"})
        self.assertEqual(200, status, data)
        self.assertFalse((data.get("causal_explanation") or {}), "拦下不产反事实")
        self.assertFalse((self.stored(f"{CF_PREFIX}62").get("causal_explanation") or {}))
        self.assertEqual(0, self.service.evidence_wiring.causal_attempts)
        self.assertEqual(1, self.service.evidence_wiring.causal_skip_reasons.get("status_not_completed"))
        self.assertEqual(1, self.service.interventions)

    def test_solo_option_intervene_lands_no_explanation(self) -> None:
        """端到端零误触发②：单候选 + 参数没改（approve）→ 零解释、零分母、只记原因码。"""
        self.service.store.put(target(79, options=[{"id": "opt-solo", "kind": "approve",
                                                    "label": "只有一条路"}]))
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}79",
                                                     "decision": "approve"})
        self.assertEqual(200, status, data)
        self.assertFalse((data.get("causal_explanation") or {}))
        self.assertEqual(0, self.service.evidence_wiring.causal_attempts)
        self.assertEqual(1, self.service.evidence_wiring.causal_skip_reasons.get("no_trigger_condition"))

    def test_causal_counters_do_not_enter_the_response_payload(self) -> None:
        """生成侧台账是服务端属性，不塞进响应载荷（与 R3-1/R7-1/S0-1 同一纪律）。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}63",
                                                     "decision": "approve"})
        self.assertEqual(200, status, data)
        text = json.dumps(data, ensure_ascii=False)
        for key in ("causal_attempts", "causal_generated", "causal_truncated",
                    "causal_skips", "causal_skip_reasons", "last_causal_problem"):
            self.assertNotIn(key, text, f"计数字段 {key} 不该出现在响应里")
        self.assertIn("causal_explanation", text, "落账结果本身要可见")

    def test_causal_wiring_failure_never_breaks_the_decision(self) -> None:
        """解释生成异常 → 决定仍 200、决定段照常落盘、审计链验得过，异常只计数留证。"""
        def boom(record, *, llm=None):
            raise RuntimeError("夹具：解释生成炸了")

        self.service.evidence_wiring.causal = boom
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}64",
                                                     "decision": "modify",
                                                     "modified_params": {PARAM: 25}})
        self.assertEqual(200, status, data)
        self.assertEqual("modified", data["status"])
        self.assertFalse((data.get("causal_explanation") or {}))
        self.assertTrue(self.service.verify_audit_chain()["ok"])
        self.assertEqual(1, self.service.interventions)

    def test_broadcast_carries_the_landed_explanation(self) -> None:
        """落账即广播：``reason=evidence_attached`` 那一帧带的是**带解释**的重签记录
        （既有 T9/SSE 通道，零新事件类型）。"""
        hub = self.service.stream_hub
        sub = hub.subscribe()

        def drain() -> list[dict]:
            frames: list[dict] = []
            while True:
                frame = sub.get(timeout=0.2)
                if not frame:
                    return frames
                frames.append(frame)

        try:
            self.assertEqual(200, self.post("/intervene", {"action_id": f"{CF_PREFIX}65",
                                                           "decision": "modify",
                                                           "modified_params": {PARAM: 26}})[0])
            frames = drain()
        finally:
            hub.unsubscribe(sub)
        attached = [frame for frame in frames
                    if str(frame.get("type") or "").endswith("updated")
                    and (frame.get("data") or {}).get("reason") == "evidence_attached"]
        self.assertTrue(attached, "落账必须广播一帧 evidence_attached（既有名单）")
        record = (attached[0].get("data") or {}).get("record") or {}
        self.assertTrue(record.get("causal_explanation"), "广播里的记录要已带解释")
        self.assertTrue(record.get("signature"), "广播出去的必须是重签后的那份")

    def test_idempotent_replay_lands_the_explanation_once(self) -> None:
        """幂等重放提前返回，够不到接线点：同一次决定只生成一次解释。"""
        key = "r32-replay-1"
        first = self.post("/intervene", {"action_id": f"{CF_PREFIX}66", "decision": "approve",
                                         "idempotency_key": key})
        self.assertEqual(200, first[0], first[2])
        second = self.post("/intervene", {"action_id": f"{CF_PREFIX}66", "decision": "approve",
                                          "idempotency_key": key})
        self.assertEqual(200, second[0], second[2])
        self.assertEqual(1, self.service.evidence_wiring.causal_attempts)
        self.assertEqual(1, self.service.causal_wiring_records)

    def test_fresh_data_root_makes_t13_upload_usable(self) -> None:
        """**前置微修**（PM-VERDICT-R3-1 §3.3 遗留 3）：全新数据根上一个目录都没建，
        T13 主动上传（``EvidenceService.add`` → ``_store_payload``）直接可用，
        不再抛 ``EVIDENCE_WRITE_FAILED``。"""
        root = scratch_dir("causal-fresh-root-t13")
        self.addCleanup(shutil.rmtree, root, True)
        paths = FeaturePaths(root / "features")     # 故意不 ensure()
        svc = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                  features_root=root / "features", sign_on_write=True)
        view = svc.evidence_attach("act-demo-0001",
                                   {"kind": "screenshot",
                                    "payload": {"data_uri": f"data:image/png;base64,{TINY_PNG}"},
                                    "caption": "前置微修钉测：全新数据根主动上传"},
                                   actor_id="local-user")
        self.assertTrue(view["ok"], view)
        evidence = view["evidence"]
        stored = svc.evidence_service.file_path(evidence["id"])
        self.assertTrue(stored and stored.exists(),
                        "证据文件必须真的落在 evidence/files/ 下（修复前这里是写盘失败）")
        self.assertEqual(evidence["bytes"], stored.stat().st_size)
        self.assertEqual(evidence["sha256"],
                         __import__("hashlib").sha256(stored.read_bytes()).hexdigest())

    def test_fresh_data_root_intervene_lands_both_products(self) -> None:
        """全新数据根上跑一次真实决定：证据与因果解释**都**落得下来（接线侧零缺口）。"""
        root = scratch_dir("causal-fresh-root-intervene")
        self.addCleanup(shutil.rmtree, root, True)
        svc = fresh_service(root, copy.deepcopy(NATIVE) + [target(78)])
        record, _meta = svc.intervene({"action_id": f"{CF_PREFIX}78", "decision": "modify",
                                       "modified_params": {PARAM: 19}},
                                      request_id="req-r32-fresh")
        self.assertTrue(record.get("multimedia_evidence"), "证据要落下来")
        self.assertTrue(record.get("causal_explanation"), "解释要落下来")
        self.assertEqual(1, svc.causal_wiring_records)
        self.assertTrue(svc.secretariat.verify_record(svc.store.get(record["id"]))["ok"])

    def test_existing_evidence_surface_still_works(self) -> None:
        """既有 evidence 面回归：T13 挂证据 / T14 列表 / T15 取字节 / T16 投票 /
        T17 主动补解释（请求带 candidates 与 lang）全部照常，且与自动接线互不干扰。"""
        svc = self.service
        view = svc.evidence_attach("act-demo-0001",
                                   {"kind": "diff", "payload": {"before": "a\n",
                                                                "after": "b\n"},
                                    "caption": "回归：T13 主动上传"},
                                   actor_id="local-user")
        self.assertTrue(view["ok"], view)
        listed = svc.evidence_service.list("act-demo-0001")
        self.assertEqual(1, len(listed))
        got = svc.evidence_file_response(listed[0]["id"])
        self.assertTrue(str(got["content_type"]).startswith("text/"), got["content_type"])
        self.assertIn(b"-a", got["body"])
        voted = svc.evidence_service.feedback(listed[0]["id"], vote="like")
        self.assertEqual(1, voted["feedback"]["like"])
        causal = svc.evidence_causal("act-demo-0001",
                                     {"candidates": [{"option": "只出清单",
                                                      "expected": "相册不动"}],
                                      "lang": "en"})
        self.assertTrue(causal["ok"], causal)
        self.assertEqual("en", causal["causal_explanation"]["language"])
        self.assertEqual("只出清单", causal["causal_explanation"]["alternatives"][0]["option"])
        self.assertEqual(0, svc.evidence_wiring.causal_attempts,
                         "T17 是主动面，不进自动接线的生成侧分母")

    def test_intervention_record_is_still_the_chain_head(self) -> None:
        """解释落账排在 S0-1 之前：独立签名干预记录的 ``prev_hash``
        接的就是**带解释的 body**（R3-1 钉住的链头不变量不被接线打断）。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{CF_PREFIX}67",
                                                     "decision": "modify",
                                                     "modified_params": {PARAM: 27}})
        self.assertEqual(200, status, data)
        target_record = self.service.store.get(f"{CF_PREFIX}67")
        self.assertTrue(target_record.get("causal_explanation"))
        events = [row for row in self.service.store.all()
                  if str(row.get("id") or "").startswith("act_iv_")]
        self.assertEqual(1, len(events))
        self.assertEqual(target_record["entry_hash"], events[0]["prev_hash"])
        self.assertTrue(self.service.secretariat.verify_record(target_record)["ok"])
        self.assertTrue(self.service.secretariat.verify_record(events[0])["ok"])


# --------------------------------------------------------------------------- #
# E2. T17 写回重签（L1 前置微修 · PM-VERDICT-R3-2 §3.1 裁决 1）
# --------------------------------------------------------------------------- #

class CausalT17ResignTests(unittest.TestCase):
    """§2.4 前置微修：T17 ``evidence_causal`` 写回走 D-resign 那条**既有**重签通道。

    修之前这里是裸 ``store.put(updated)``：body 换了、签名与 ``entry_hash`` 沿用旧的，
    账本验签当场 MISMATCH（``entry_hash=invalid``）。这里钉住修完之后的事：
    未签名行补签得绿、已签名行重签且链连续、修复前的形状当场红（钉"修的是什么"）、
    一次写回只有一次落账一次重签、广播带的是重签后的那份、签名关掉时绝不凭空造签名。
    **自动接线通道零改动**同样钉住（生成侧分母一格不多）。
    """

    def setUp(self) -> None:
        self.root = scratch_dir("causal-t17-resign")
        self.addCleanup(shutil.rmtree, self.root, True)
        rows = copy.deepcopy(NATIVE) + [target(i) for i in range(90, 96)]
        self.service = service(self.root, rows)

    def _t17(self, action_id: str, **over) -> dict:
        body: dict = {"candidates": [{"option": "只出清单", "expected": "相册不动"}],
                      "lang": "zh"}
        body.update(over)
        return self.service.evidence_causal(action_id, body)

    def _decide(self, idx: int) -> dict:
        record, _meta = self.service.intervene({"action_id": f"{CF_PREFIX}{idx:02d}",
                                                "decision": "modify",
                                                "modified_params": {PARAM: 30 + idx}},
                                               request_id=f"req-r34-t17-{idx}")
        return record

    def test_writeback_on_a_row_that_was_never_signed_verifies_green(self) -> None:
        """样本直载（从没签过名）的记录被 T17 写回后补签：验签三项绿、链头 genesis。"""
        before = self.service.store.get("act-demo-0001")
        self.assertFalse(before.get("signature"), "样本直载的记录本来就没签名")
        self.assertFalse(before.get("causal_explanation"))
        result = self._t17("act-demo-0001")
        self.assertTrue(result["ok"], result)
        saved = self.service.store.get("act-demo-0001")
        self.assertTrue(saved.get("causal_explanation"), "解释要写回记录本体")
        report = self.service.secretariat.verify_record(saved)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual("valid", report["checks"]["signature"])
        self.assertEqual("valid", report["checks"]["entry_hash"])
        self.assertEqual("genesis", report["checks"]["prev_hash"])
        self.assertEqual(0, self.service.sign_failures)

    def test_writeback_on_a_signed_row_anchors_prev_hash_and_verifies_green(self) -> None:
        """走过决定流的已签名记录：写回后 ``prev_hash`` 接的就是改前那条的 ``entry_hash``
        （链连续，D-resign 口径），验签三项全绿，``entry_hash`` 跟着新 body 换。"""
        record = self._decide(90)
        landed = self.service.store.get(record["id"])
        self.assertTrue(landed.get("signature"), "决定流落盘的记录要已签名")
        self._t17(record["id"])
        after = self.service.store.get(record["id"])
        self.assertEqual(landed["entry_hash"], after["prev_hash"],
                         "重签的 prev_hash 必须接上改前那条：责任链不能断头")
        self.assertNotEqual(landed["entry_hash"], after["entry_hash"],
                            "解释换了 body，entry_hash 必须跟着换（不是沿用旧的）")
        report = self.service.secretariat.verify_record(after)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual("valid", report["checks"]["signature"])
        self.assertEqual("valid", report["checks"]["entry_hash"])
        self.assertEqual("continuous", report["checks"]["prev_hash"])

    def test_the_shape_this_fix_replaces_is_a_ledger_mismatch(self) -> None:
        """钉住"这次修的是什么"：换 body 沿用旧签名（修复前那个裸 ``put`` 的形状）
        验签当场红——微修有对象，不是空喊。"""
        before = self.service.store.get("act-demo-0001")
        explanation = self._t17("act-demo-0001")["causal_explanation"]
        stale = copy.deepcopy(dict(before))
        stale["causal_explanation"] = explanation            # 只换 body，签名照旧
        report = self.service.secretariat.verify_record(stale)
        self.assertFalse(report["ok"], "修复前的形状必须当场红，否则这次微修没有靶子")
        self.assertEqual("invalid", report["checks"]["entry_hash"])

    def test_one_writeback_is_one_put_and_one_resign(self) -> None:
        """一次写回 = 一次落账 + 一次重签：不多落一格、不多签一次（签名不能签两次）。"""
        record = self._decide(91)
        puts: list[str] = []
        real_put = self.service.store.put

        def spy(row: dict) -> dict:
            puts.append(str(row.get("id") or ""))
            return real_put(row)

        self.service.store.put = spy
        signed_before = self.service.signed_on_write
        try:
            self._t17(record["id"])
        finally:
            self.service.store.put = real_put
        self.assertEqual([record["id"]], puts, "T17 写回只落一次账，不排中间态")
        self.assertEqual(1, self.service.signed_on_write - signed_before, "只重签一次")
        self.assertEqual(0, self.service.sign_failures)

    def test_the_broadcast_frame_carries_the_resigned_row(self) -> None:
        """广播带出去的是**重签后**那份（``entry_hash`` 与账上一字不差），
        ``reason=causal_written`` 复用既有名单，零新事件类型。"""
        record = self._decide(92)
        hub = self.service.stream_hub
        sub = hub.subscribe()
        try:
            self.assertTrue(self._t17(record["id"])["ok"])
            frames: list[dict] = []
            while True:
                frame = sub.get(timeout=0.3)
                if not frame:
                    break
                frames.append(frame)
        finally:
            hub.unsubscribe(sub)
        written = [frame for frame in frames
                   if str(frame.get("type") or "").endswith("updated")
                   and (frame.get("data") or {}).get("reason") == "causal_written"]
        self.assertTrue(written, "写回必须广播一帧 causal_written（既有名单）")
        row = (written[-1].get("data") or {}).get("record") or {}
        self.assertTrue(row.get("causal_explanation"), "广播里的记录要已带解释")
        self.assertEqual(self.service.store.get(record["id"])["entry_hash"],
                         row.get("entry_hash"), "广播出去的必须是重签后的那份")

    def test_the_signing_block_is_honest_and_disabled_signing_invents_nothing(self) -> None:
        """响应只**追加** ``signing`` 块（与回滚端点同一既有口径）：签了几条、
        失败几条都如实；签名关掉时解释照落、**绝不凭空造签名**。"""
        result = self._t17("act-demo-0001")
        self.assertEqual({"ok", "causal_explanation", "signing"}, set(result.keys()),
                         "响应只追加 signing 块，不塞生成侧计数器")
        self.assertTrue(result["signing"]["enabled"])
        self.assertEqual(1, result["signing"]["signed"])
        self.assertEqual(0, result["signing"]["failures"])
        root = scratch_dir("causal-t17-unsigned")
        self.addCleanup(shutil.rmtree, root, True)
        svc = sv.InterveneService(records=copy.deepcopy(NATIVE),
                                  features_root=root / "features", sign_on_write=False)
        out = svc.evidence_causal("act-demo-0001",
                                 {"candidates": [{"option": "只出清单",
                                                  "expected": "相册不动"}], "lang": "zh"})
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["signing"]["enabled"])
        self.assertEqual(0, out["signing"]["signed"])
        row = svc.store.get("act-demo-0001")
        self.assertTrue(row.get("causal_explanation"), "签名关掉不影响解释落账")
        self.assertFalse(row.get("signature"), "没开签名就绝不造签名（如实）")
        self.assertEqual(0, svc.signed_on_write)

    def test_the_auto_wiring_channel_stays_untouched(self) -> None:
        """§2.4 铁律：自动接线通道零改动 —— T17 写回不进生成侧分母、
        不动落账计数，改完之后账仍是绿的。"""
        record = self._decide(93)
        attempts = self.service.evidence_wiring.causal_attempts
        landed = self.service.causal_wiring_records
        signed_before = self.service.signed_on_write
        self._t17(record["id"])
        self.assertEqual(attempts, self.service.evidence_wiring.causal_attempts,
                         "T17 是主动面，不记进自动接线的生成侧分母")
        self.assertEqual(landed, self.service.causal_wiring_records,
                         "自动接线的落账计数不因写回而多一格")
        self.assertEqual(1, self.service.signed_on_write - signed_before)
        self.assertTrue(self.service.secretariat.verify_record(
            self.service.store.get(record["id"]))["ok"])


# --------------------------------------------------------------------------- #
# F. 口径落地：模块自检 + docs/07 §11.19 + 签名域与路由零改动
# --------------------------------------------------------------------------- #

class CausalDisciplineTests(unittest.TestCase):
    """卫生与口径：自检可跑、docs 落条款、签名域一字未动、契约路由数不变。"""

    def test_module_self_check_runs_green(self) -> None:
        """``python -m agent_explainer.evidence_wiring`` 的自检在测试环境跑得通
        （含因果解释与全新数据根写盘；临时目录走 ``tests/scratch.py`` 替身）。"""
        with use_scratch_tempfile():
            self.assertEqual(0, ew._self_check())
        with use_scratch_tempfile():
            self.assertEqual(0, ev_mod._self_check())

    def test_signature_fields_are_untouched(self) -> None:
        """§1.1 铁律：``SIGNATURE_FIELDS`` 11 字段一字未动
        （解释与证据都靠 ``entry_hash`` 进签名域）。"""
        self.assertEqual(11, len(sec_mod.SIGNATURE_FIELDS))
        self.assertNotIn("causal_explanation", sec_mod.SIGNATURE_FIELDS)
        self.assertNotIn("multimedia_evidence", sec_mod.SIGNATURE_FIELDS)
        self.assertIn("entry_hash", sec_mod.SIGNATURE_FIELDS)

    def test_no_new_routes_and_the_contract_stays_at_the_baseline(self) -> None:
        """契约面基线（现行值 61 条 / 未挂载 0 条）。

        历史：R3-1 当时要求「解释接线不开端点」，断言 54 条且 `/api/records/{id}/causal`
        **不在**契约里。0.6.0 L1 把这条路由真正挂上了（服务方法一直是本文件对应的
        `evidence_causal`，只是 HTTP 面从未接线，老板已批准修复），所以断言从
        「不存在」翻转为「存在且可服务」。
        """
        routes = {(row["path"], row["verb"]) for row in CONTRACT["routes"]}
        self.assertEqual(61, len(routes))
        self.assertIn("/api/records/{id}/causal", {path for path, _ in routes},
                      "T17 因果写回必须真的挂上（0.6.0 L1）")
        self.assertIn(("/api/records/{id}/causal", "POST"), routes)
        unmounted = {(row if isinstance(row, str) else str(row.get("symbol") or row))
                     .split("=")[0]
                     for row in CONTRACT["declared_vs_mounted"]["unmounted"]}
        self.assertEqual(set(), unmounted,
                         "未挂载清单必须为空（两条历史欠账已收口）")

    def test_docs_07_section_11_19_documents_the_wiring(self) -> None:
        """§2.5：接线口径落 docs/07 §11.19（追加小节，不新开一级章节，§0–§10 冻结）。"""
        text = (ROOT / "docs" / "07-secretariat-and-extensions.md").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("### 11.19 "))
        start = text.find("### 11.19 ")
        self.assertIn("### 11.18 ", text[:start], "§11.19 必须在 §11.18 之后（只追加）")
        body = text[start:]
        self.assertEqual(0, sum(1 for line in body.splitlines()
                                if line.startswith("## ")),
                         "追加小节不得新开一级章节（docs 术语闸门）")
        for needle in ("counterfactual", "causal_explanation", "模板", "离线可复算",
                       "被拒", "共点", "D-resign", "model", "mkdir", "§11.18"):
            self.assertIn(needle, body, f"§11.19 必须写明 {needle}")

    def test_trigger_and_skip_codes_are_shared_not_duplicated(self) -> None:
        """口径不另立：触发集 / 零触发原因码 / 反事实条件码都在模块里单点定义。"""
        self.assertEqual(("rejected_alternative", "param_change"), ew.CAUSAL_TRIGGER_REASONS)
        self.assertEqual(4, len(ew.CAUSAL_SKIP_REASONS))
        self.assertEqual(ew.TRIGGER_STATUSES, ew.CAUSAL_TRIGGER_STATUSES)

    def test_frontend_reads_the_landed_shape_without_changes(self) -> None:
        """前端零改动可证：既有 ``cards.js`` 的 ``causalOf`` 读的就是我们落的形状
        （``counterfactual`` / ``alternatives[].option|expected|why`` / ``model``）。"""
        js = (ROOT / "agent_explainer" / "web" / "ext" / "cards.js").read_text(encoding="utf-8")
        self.assertIn("record.causal_explanation", js)
        self.assertIn("raw.alternatives", js)
        self.assertIn("alt.option", js)
        self.assertIn("raw.model", js)


if __name__ == "__main__":  # 手工自检：python tests/test_counterfactual_wiring.py -v
    unittest.main(verbosity=2)
