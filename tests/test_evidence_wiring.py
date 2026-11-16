"""R3-1（REQ-003 B3-1/B3-3）验收测试：多模态证据**生成接线**与落账（签名覆盖证明）。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：

    cd agent-explainer
    python -m unittest tests.test_evidence_wiring -v

钉住的口径（PM 裁决 TASK-R3-1 §2，落 docs/07 §11.18）：

1. **触发口径（B3-1 分母定义）**：决策步骤完成且 `status ∈ {approved, modified,
   succeeded, done}` 时——存在决定前后的参数文本差异 → `add_diff`；
   存在计划候选（≥2 个）→ `add_diagram`。**生成尝试计分母**（失败也计），
   成功落账计分子，成功率 ≥95%；截图类"未实现/待接入"不计分母（D-7）。
2. **零误触发**：无参数差异 + 无候选（或只有 1 个候选）→ 零证据零分母，
   只登记原因码；`rejected` 不在触发口径内（"拦下"不是"完成"）。
3. **存储口径（PM-PLAN §5.1）**：证据一律经 ``EvidenceService._store_payload``
   落盘为文件；记录里只放引用（sha256 + 定位符 + media_type + 尺寸），
   单件 256KB 上限、超限**降级为文本摘要证据**；**Base64 不入账本**。
4. **B3-3 证明**：落账后 ``verify_record.ok=true``，且篡改证据 ``sha256``
   的一个字符 → 验签立即失效（证据哈希被 ``entry_hash`` → 签名链覆盖，
   schema 1090 明文；这里只补证明，不改密码学）。
5. **接线不反噬决定**：生成/落账异常只计数留证，``/intervene`` 仍是 200。
6. **零新路由 / 零 schema 改动 / 零签名域改动 / 前端零改动**：契约路由数不变，
   落账条目必须过 ``action_record.schema.json`` 的封闭形状。

临时目录一律 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）；HTTP 断言级不重试
（``http_fixture``）。每个 HTTP 用例一台全新的签名服务器：分母/分子计数器是
**服务实例级**台账，共用服务器会让用例互相攒次数，"一次决定 = 一次生成尝试"
就没法钉。
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import pathlib
import re
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import evidence as ev_mod          # noqa: E402
from agent_explainer import evidence_wiring as ew       # noqa: E402
from agent_explainer import ingest as ingest_mod        # noqa: E402
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

BASE_TARGET = "act-demo-0004"        # medium 风险、3 个候选、可改 max_move_count（188→）
PARAM = "max_move_count"
R3_PREFIX = "act-r3w-"               # 本文件独占的夹具记录 id 前缀
OVER_LIMIT_TEXT = "超限降级"        # 摘要首行/caption 里的如实标注（子串即可）


# --------------------------------------------------------------------------- #
# 夹具：走产品路径构造"决定之后"的记录（不手搓决定段）
# --------------------------------------------------------------------------- #

def target(idx: int, *, options: list[dict] | None = None) -> dict:
    """一条**可决**的目标记录（演示夹具 act-demo-0004 的副本，换 id、清决定段）。"""
    rec = copy.deepcopy(next(r for r in NATIVE if r["id"] == BASE_TARGET))
    rec["id"] = f"{R3_PREFIX}{idx:02d}"
    rec["sequence_no"] = 800 + idx
    rec["status"] = "pending_confirmation"
    rec["requires_action"] = True
    rec["user_intervention"] = None
    if options is not None:
        rec["intervention_options"] = copy.deepcopy(options)
    rec["execution_receipt"] = {"state": "unreported", "reported_by": None,
                                "reported_at": None, "detail": "夹具：无回执",
                                "sdk_version": None,
                                "effect_notice": "intervention.effect.recorded_only"}
    return rec


def decided(idx: int, decision: str, *, params: dict | None = None,
            reason: str | None = None, options: list[dict] | None = None,
            option_id: str | None = None) -> dict:
    """``apply_decision`` 的产物 = 决定落盘后的记录形状（决定段是官方账，不手搓）。"""
    return sv.apply_decision(target(idx, options=options), decision,
                             modified_params=params, reason=reason, option_id=option_id,
                             actor_id="local-user", actor_role="local")


def wiring_on_scratch(case: str) -> tuple[ew.EvidenceWiring, pathlib.Path]:
    """挂在临时目录上的证据账本（``tests/scratch.py``，用完整目录删）。"""
    root = scratch_dir(case)
    paths = FeaturePaths(root / "features")
    paths.ensure()
    return ew.EvidenceWiring(ev_mod.EvidenceService(paths)), root

def service(root: pathlib.Path, records: list[dict] | None = None, **over) -> sv.InterveneService:
    return sv.InterveneService(records=copy.deepcopy(records if records is not None else NATIVE),
                               features_root=root / "features",
                               sign_on_write=True, **over)


def item_files(service_obj: sv.InterveneService, record: dict) -> list[tuple[dict, bytes]]:
    """落账条目 → ``(条目, 落盘字节)``：文件本体是事实来源，逐字节复算用它。"""
    out: list[tuple[dict, bytes]] = []
    for item in record.get("multimedia_evidence") or []:
        path = service_obj.evidence_service.file_path(str(item.get("id") or ""))
        out.append((item, path.read_bytes() if path else b""))
    return out


def http_request(host: str, port: int, method: str, path: str, body=None,
                 timeout: float = 8.0, binary: bool = False) -> tuple[int, dict, object]:
    """一发一收（连接生命周期交给 tests/http_fixture；非幂等动词绝不重放）。

    ``binary=True``：按原始字节返回（T15 那种文件流），不做 JSON/文本解码。
    """
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
        if binary:
            return response.status, heads, raw
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = raw.decode("utf-8", "replace")
        return response.status, heads, data

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


# --------------------------------------------------------------------------- #
# A. 触发口径：什么该触发、什么绝不触发（零误触发）
# --------------------------------------------------------------------------- #

class TriggerGateTests(unittest.TestCase):
    """§2.1 触发口径：`add_diff` / `add_diagram` 的成立条件与零误触发。"""

    def setUp(self) -> None:
        self.wiring, self.root = wiring_on_scratch("evidence-wiring-gate")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_trigger_statuses_are_the_declared_pm_mapping(self) -> None:
        """PM 口径 ``{done, modified, succeeded}`` 的逐字映射：approved ≙ done，
        ``rejected`` **不在**口径内（拦下 ≠ 完成）。"""
        self.assertEqual({"approved", "modified", "succeeded", "done"},
                         set(ew.TRIGGER_STATUSES))
        self.assertNotIn("rejected", ew.TRIGGER_STATUSES)
        self.assertIn("modified", ew.TRIGGER_STATUSES)
        self.assertIn("succeeded", ew.TRIGGER_STATUSES)

    def test_modify_decision_triggers_add_diff(self) -> None:
        """正题①：决定改了参数 → 一条 ``diff`` 证据，字节与 sha256 逐字对得上。"""
        rec = decided(1, "modify", params={PARAM: 50})
        result = self.wiring.generate(rec)
        self.assertEqual(["diff", "diagram"], [item["kind"] for item in result["items"]],
                         "改了参数 + 有候选 → 两条计划都触发（分母 2）")
        item = next(i for i in result["items"] if i["kind"] == "diff")
        self.assertEqual("text/x-diff", item["media_type"])
        path = self.wiring.service.file_path(item["id"])
        self.assertTrue(path and path.exists(), "证据必须落盘为文件，不是塞进记录")
        raw = path.read_bytes()
        self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item["bytes"], len(raw))
        text = raw.decode("utf-8")
        self.assertIn(f"-{PARAM} = 188", text, "决定前的值必须出现在 diff 的删除侧")
        self.assertIn(f"+{PARAM} = 50", text, "决定后的值必须出现在 diff 的新增侧")

    def test_multi_candidate_decision_triggers_add_diagram(self) -> None:
        """正题②：多候选决策（3 个候选）→ 一条纯 stdlib SVG 流程图，含候选与已选标注。"""
        rec = decided(2, "approve")
        result = self.wiring.generate(rec)
        self.assertEqual(["diagram"], [item["kind"] for item in result["items"]])
        item = result["items"][0]
        self.assertEqual("image/svg+xml", item["media_type"])
        raw = self.wiring.service.file_path(item["id"]).read_bytes()
        self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        svg = raw.decode("utf-8")
        self.assertTrue(svg.lstrip().startswith("<svg"), "SVG 魔数必须是真的 SVG 文档")
        self.assertNotIn("base64", svg, "SVG 里不许夹 base64 位图")
        labels = [str(opt.get("label") or "") for opt in rec["intervention_options"]]
        for label in labels:
            self.assertIn(label, svg, "每个计划候选都要在图上看得见")
        self.assertIn("已选", svg, "图上要标出当时走了哪条")

    def test_modify_triggers_both_diff_and_diagram(self) -> None:
        """两个条件同时成立 → 两次生成尝试都进分母（diff + diagram）。"""
        result = self.wiring.generate(decided(3, "modify", params={PARAM: 20}))
        self.assertEqual(2, result["attempts"])
        self.assertEqual({"diff", "diagram"}, {item["kind"] for item in result["items"]})
        self.assertEqual(2, self.wiring.attempts)
        self.assertEqual(2, self.wiring.successes)

    def test_no_diff_and_single_candidate_triggers_nothing(self) -> None:
        """零误触发：approve 无参数差异 + 只有 1 个候选 → 零证据、零分母、登记原因。"""
        solo = [{"id": "opt-solo", "kind": "approve", "label": "只有这条路"}]
        rec = decided(4, "approve", options=solo)
        result = self.wiring.generate(rec)
        self.assertEqual([], result["items"])
        self.assertEqual(0, result["attempts"], "条件不成立不进分母（分母只数生成尝试）")
        self.assertEqual("no_trigger_condition", result["reason"])
        self.assertEqual(0, self.wiring.attempts)
        self.assertEqual(1, self.wiring.skip_reasons.get("no_trigger_condition"))
        self.assertEqual([], self.wiring.service.list(rec["id"]), "账本里也不许多出一条")

    def test_rejected_status_is_outside_the_trigger_set(self) -> None:
        """"拦下"不产证据：status=rejected → 零触发，分母不涨。"""
        rec = decided(5, "reject", reason="先别动")
        self.assertEqual("rejected", rec["status"])
        result = self.wiring.generate(rec)
        self.assertEqual([], result["items"])
        self.assertEqual("status_not_completed", result["reason"])
        self.assertEqual(0, self.wiring.attempts)
        self.assertEqual([], self.wiring.service.list(rec["id"]))

    def test_non_decision_status_is_outside_the_trigger_set(self) -> None:
        """记录段仍在 running（有候选、无决定）→ 不在决策完成口径内 → 零触发。"""
        running = target(6)
        running["status"] = "running"
        result = self.wiring.generate(running)
        self.assertEqual([], result["items"])
        self.assertEqual("status_not_completed", result["reason"])
        self.assertEqual(0, self.wiring.attempts)

    def test_params_supplied_but_unchanged_produce_no_diff(self) -> None:
        """传了参数但逐字没改 → 无差异 → 不产 diff（分母不被"假差异"污染）。"""
        rec = target(7)
        rec["status"] = "modified"
        rec["user_intervention"] = {
            "decision": "modify", "option_id": "opt-demo-0004-modify",
            "original_params": {PARAM: 188}, "modified_params": {PARAM: 188}}
        rec["intervention_options"] = [{"id": "opt-only", "kind": "modify", "label": "改一下"}]
        self.assertIsNone(ew.param_diff_pair(rec))
        result = self.wiring.generate(rec)
        self.assertEqual([], result["items"])
        self.assertEqual("no_trigger_condition", result["reason"])

    def test_param_diff_pair_falls_back_to_the_params_objects(self) -> None:
        """取数顺序：没有 ``modified_fields`` 时用 ``original_params``/``modified_params``
        整对象差集（schema C3/C4 形状），R7-1 同款兜底，不另立口径。"""
        rec = decided(8, "modify", params={PARAM: 30})
        ui = rec["user_intervention"]
        ui.pop("modified_fields")
        pair = ew.param_diff_pair(rec)
        self.assertIsNotNone(pair)
        self.assertIn(f"{PARAM} = 188", pair[0])
        self.assertIn(f"{PARAM} = 30", pair[1])
        result = self.wiring.generate(rec)
        self.assertEqual({"diff", "diagram"},
                         {item["kind"] for item in result["items"]},
                         "兜底取数只影响 diff 的取材，不改变两条计划")

    def test_plan_candidates_requires_at_least_two_options(self) -> None:
        """候选下限 = 2：单候选没有"选择"可解释，画流程图属于误触发。"""
        self.assertEqual(2, ew.MIN_PLAN_CANDIDATES)
        one = target(9)
        one["intervention_options"] = [{"id": "a", "kind": "approve", "label": "A"}]
        self.assertEqual([], ew.plan_candidates(one))
        self.assertIsNone(ew.diagram_plan(one))
        two = target(10)
        two["intervention_options"] = [{"id": "a", "kind": "approve", "label": "A"},
                                       {"id": "b", "kind": "reject", "label": "B"}]
        plan = ew.diagram_plan(two)
        self.assertIsNotNone(plan)
        self.assertEqual(3, len(plan["nodes"]), "根节点 + 两个候选")
        self.assertEqual(2, len(plan["edges"]))


# --------------------------------------------------------------------------- #
# B. 256KB 上限与降级（PM-PLAN §5.1 冻结口径）
# --------------------------------------------------------------------------- #

class SizeAndDegradationTests(unittest.TestCase):
    """§1.2 存储口径：单件 256KB 上限、超限降级为文本摘要证据、Base64 不入账本。"""

    def setUp(self) -> None:
        self.wiring, self.root = wiring_on_scratch("evidence-wiring-size")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_item_limit_is_the_frozen_256kb(self) -> None:
        self.assertEqual(256 * 1024, ew.EVIDENCE_ITEM_MAX_BYTES)
        self.assertEqual(256 * 1024, self.wiring.max_item_bytes)
        self.assertLess(ew.EVIDENCE_ITEM_MAX_BYTES, ev_mod.MAX_BYTES,
                        "接线侧上限必须低于 T13 入参契约的 4MB，两处口径不能混")

    def test_oversize_diff_degrades_to_text_summary(self) -> None:
        """300KB 的参数文本 → 降级为文本摘要证据：文件 ≤256KB、sha256 仍是文件逐字哈希、
        caption 与文件首行都写明"超限降级"，``degraded`` 计入分子内的降级一格。"""
        big = target(11)
        big["status"] = "modified"
        big["intervention_options"] = [{"id": "opt-only", "kind": "modify", "label": "改一下"}]
        big["user_intervention"] = {
            "decision": "modify", "option_id": "opt-only",
            "modified_fields": [{"name": "payload",
                                 "before": "b" * (300 * 1024),
                                 "after": "a" * (300 * 1024)}]}
        result = self.wiring.generate(big)
        self.assertEqual(1, result["attempts"])
        self.assertEqual(1, result["degraded"], "超限这一次必须自己声明降级")
        self.assertEqual(1, self.wiring.degraded)
        self.assertEqual(1, self.wiring.successes, "降级成功算分子（口径内的成功，不是失败）")
        item = result["items"][0]
        self.assertEqual("diff", item["kind"])
        self.assertLessEqual(item["bytes"], ew.EVIDENCE_ITEM_MAX_BYTES)
        raw = self.wiring.service.file_path(item["id"]).read_bytes()
        self.assertEqual(len(raw), item["bytes"])
        self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest(),
                         "降级不许让自报哈希与文件脱钩")
        self.assertIn("降级", item["caption"])
        self.assertIn(OVER_LIMIT_TEXT, raw.decode("utf-8")[:400],
                      "文件首行要自己说清这是摘要、原文多大")
        self.assertNotIn("b" * 100_000, raw.decode("utf-8"),
                         "300KB 的原文不得原样进摘要文件（头部只取 1/8 上限）")

    def test_real_records_never_reach_the_svg_ceiling(self) -> None:
        """schema 现状下 SVG 不可能超限：候选上限 6 个 × 标签裁到 90 字符 ≈ 几 KB。
        （所以下面两级降级梯是**兜底**，按形状构造来钉，不是真实记录的日常路径。）"""
        self.assertEqual(6, SCHEMA["properties"]["intervention_options"].get("maxItems"))
        rec = decided(15, "approve")
        plan = ew.diagram_plan(rec)
        svg = self.wiring.service._diagram_svg(plan["title"], plan["nodes"], plan["edges"])
        self.assertLess(len(svg), 8 * 1024, "真实形状的图离 256KB 上限差两个数量级")
        result = self.wiring.generate(rec)
        self.assertEqual(0, result["degraded"], "真实记录不该触发降级")

    def test_oversize_diagram_degrades_by_capping_candidates(self) -> None:
        """降级梯①（砍候选数、标签逐字保住）：图仍然落盘，标题写明"仅显示前 N 个"。

        构造口径：直接给 ``_emit_diagram`` 一个超上限的 plan（真实记录到不了这个规模，
        见上一个用例），钉的是"超限以后怎么降、降完还留下什么"。
        """
        label = "候" * 200
        nodes = [{"id": "decision", "label": "决策点"}]
        edges = [{"from": "decision", "to": f"opt-{n}"} for n in range(400)]
        for n in range(400):
            nodes.append({"id": f"opt-{n}", "label": f"approve｜{label}"})
        plan = {"kind": "diagram", "title": "计划候选：夹具（400 个候选）",
                "nodes": nodes, "edges": edges, "chosen": "opt-0",
                "candidates": [str(node["label"]) for node in nodes[1:]]}
        self.assertGreater(len(self.wiring.service._diagram_svg(
            plan["title"], plan["nodes"], plan["edges"])), ew.EVIDENCE_ITEM_MAX_BYTES,
            "夹具本身要真的超限")
        view, degraded = self.wiring._emit_diagram(f"{R3_PREFIX}12", plan, "zh")
        self.assertTrue(degraded)
        self.assertEqual("diagram", view["kind"])
        self.assertLessEqual(view["bytes"], ew.EVIDENCE_ITEM_MAX_BYTES)
        raw = self.wiring.service.file_path(view["id"]).read_bytes().decode("utf-8")
        self.assertIn("<svg", raw, "降级以后仍然是图，不是文本")
        self.assertIn("仅显示前", raw, "砍掉的候选数必须在图里如实写明")

    def test_oversize_diagram_degrades_by_clipping_labels(self) -> None:
        """降级梯②（单个标签就超 256KB → 截断标签）：图还在、语义还在，标题写明已截断。"""
        huge = "长" * (300 * 1024)
        plan = {"kind": "diagram", "title": "计划候选：巨型标签（2 个候选）",
                "nodes": [{"id": "decision", "label": "决策点"},
                          {"id": "opt-0", "label": huge}, {"id": "opt-1", "label": huge}],
                "edges": [{"from": "decision", "to": "opt-0"},
                          {"from": "decision", "to": "opt-1"}],
                "chosen": "opt-0", "candidates": [huge, huge]}
        view, degraded = self.wiring._emit_diagram(f"{R3_PREFIX}13", plan, "zh")
        self.assertTrue(degraded)
        self.assertEqual("diagram", view["kind"])
        self.assertLessEqual(view["bytes"], ew.EVIDENCE_ITEM_MAX_BYTES)
        raw = self.wiring.service.file_path(view["id"]).read_bytes().decode("utf-8")
        self.assertIn("<svg", raw)
        self.assertIn("标签已截断", raw, "截断这件事必须写在图里，不许静默")

    def test_oversize_diagram_falls_back_to_text_summary(self) -> None:
        """降级梯③（构造上到不了的兜底：连截断后的节点都超）→ 落文本摘要证据。

        口径钉子：兜底产物的 ``kind`` 必须仍在 ``evidence.KINDS`` 之内（schema 只认
        screenshot/diagram/diff），文本摘要用 ``diff``/``text/x-diff`` 承载，
        caption 与文件内容都写明"降级"。
        """
        wiring = ew.EvidenceWiring(self.wiring.service, max_item_bytes=64)
        plan = {"kind": "diagram", "title": "计划候选：兜底（2 个候选）",
                "nodes": [{"id": "decision", "label": "决策点"},
                          {"id": "opt-0", "label": "同意"}, {"id": "opt-1", "label": "拦下"}],
                "edges": [{"from": "decision", "to": "opt-0"}], "chosen": "opt-0",
                "candidates": ["approve｜同意", "reject｜拦下"]}
        view, degraded = wiring._emit_diagram(f"{R3_PREFIX}16", plan, "zh")
        self.assertTrue(degraded)
        self.assertEqual("diff", view["kind"], "兜底产物的 kind 必须仍在 KINDS 之内")
        self.assertIn("降级", view["caption"])
        raw = self.wiring.service.file_path(view["id"]).read_bytes().decode("utf-8")
        self.assertIn("approve｜同意", raw, "兜底文本要看得见候选清单")

    def test_summarize_text_is_deterministic_and_honest(self) -> None:
        """摘要是确定性函数：短文本逐字返回；长文本头部 + 如实标注截掉了多少。"""
        self.assertEqual("abc", ew.summarize_text("abc", 10))
        summary = ew.summarize_text("x" * 1000, 100)
        self.assertEqual(summary, ew.summarize_text("x" * 1000, 100))
        self.assertIn("# 余下 900 字符未纳入本摘要", summary)
        self.assertLessEqual(len(summary), 1000)

    def test_no_base64_anywhere_in_the_generated_items(self) -> None:
        """§1.2 铁律：Base64 不入账本 —— 生成侧连一个 base64/data_uri 出口都没有。"""
        payload = json.dumps(self.wiring.generate(
            decided(14, "modify", params={PARAM: 7}))["items"], ensure_ascii=False)
        self.assertNotIn("base64", payload.lower())
        self.assertNotIn("data:", payload)
        self.assertIsNone(re.search(r"[A-Za-z0-9+/]{120,}={0,2}", payload),
                          "120 字符以上的连续 base64 形态 = 二进制入账本的信号")


# --------------------------------------------------------------------------- #
# C. 落账形态：schema 封闭形状 + 文件本体 + 相对路径
# --------------------------------------------------------------------------- #

class LedgerShapeTests(unittest.TestCase):
    """§2.2 落账形态：条目只放引用、过 schema 校验、sha256 与文件逐字节一致。"""

    def setUp(self) -> None:
        self.root = scratch_dir("evidence-wiring-ledger")
        self.service = service(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_record_with_evidence_items_still_passes_the_schema(self) -> None:
        """零 schema 改动可证：落账后的记录必须过现仓 ``action_record.schema.json``
        （``multimedia_evidence`` 条目是 ``additionalProperties: false`` 的封闭形状）。"""
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 60}}, request_id="req-r31-shape")
        self.assertEqual([], VALIDATOR.collect(record), "带证据的记录要逐字过 schema")
        properties = set(SCHEMA["properties"]["multimedia_evidence"]["items"]["properties"])
        self.assertEqual({"kind"},
                         set(SCHEMA["properties"]["multimedia_evidence"]["items"]["required"]))
        for item in record["multimedia_evidence"]:
            self.assertTrue(set(item).issubset(properties),
                            f"条目出现了 schema 之外的键：{set(item) - properties}")
            self.assertIn("kind", item, "kind 是 schema 唯一的必填键")

    def test_wired_field_is_in_the_s0_2_merge_whitelist(self) -> None:
        """§2.2：接线写的字段就在 S0-2 合并器白名单里（导入/上报只合并白名单字段）。

        接线走的是**写入侧**通道；这条钉的是另一半：清单进了记录之后，
        上报/导入侧再送同名扩展字段时合并器怎么对待它 ——
        同一份清单 canonical 相同 → 不算变化、逐字保住；上游送来一份不同的清单 →
        白名单允许合并（S0-2 既定语义，合并发生在签名之前、由调用方重签覆盖事实），
        而入参想夹带的受保护字段（``intervention_history``）照旧剥离并留痕。
        """
        self.assertIn("multimedia_evidence", ingest_mod.EXPANSION_MERGE_FIELDS)
        self.assertNotIn("multimedia_evidence", ingest_mod.ALL_PROTECTED_FIELDS)
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 61}}, request_id="req-r31-merge")
        items = record["multimedia_evidence"]
        self.assertTrue(items)
        merged, changed, stripped = ingest_mod.merge_facts_full(
            record, {"multimedia_evidence": copy.deepcopy(items),
                     "intervention_history": [{"id": "act_forged"}]})
        self.assertEqual(items, merged["multimedia_evidence"], "同一份清单逐字保住")
        self.assertNotIn("multimedia_evidence", [row["field"] for row in changed],
                         "canonical 相同不算变化，签名材料不因此漂移")
        self.assertIn("intervention_history", stripped, "受保护字段剥离并留痕")
        self.assertNotIn("act_forged", json.dumps(merged, ensure_ascii=False))
        upstream = [{"kind": "diff", "sha256": "sha256:" + "a" * 64,
                     "media_type": "text/x-diff"}]
        merged2, changed2, _stripped = ingest_mod.merge_facts_full(
            record, {"multimedia_evidence": upstream})
        self.assertEqual(upstream, merged2["multimedia_evidence"],
                         "白名单字段允许上游合并进来（这条通道 S0-2 已建好，接线不新增）")
        self.assertEqual(["multimedia_evidence"], [row["field"] for row in changed2])

    def test_sha256_matches_the_stored_file_byte_for_byte(self) -> None:
        """§2.5：sha256 与落盘文件逐字节一致（自报哈希 = 文件复算哈希）。"""
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 90}}, request_id="req-r31-bytes")
        pairs = item_files(self.service, record)
        self.assertEqual(2, len(pairs))
        for item, raw in pairs:
            self.assertTrue(raw, "证据文件本体必须真的在磁盘上")
            self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item["bytes"], len(raw))
            self.assertLessEqual(item["bytes"], ew.EVIDENCE_ITEM_MAX_BYTES)

    def test_relative_path_locates_the_same_file_as_the_index(self) -> None:
        """相对路径口径：``evidence/files/<id>.<ext>``（相对数据根），
        与 ``file_path``/``uri`` 三处指向同一个文件，不产生第二套定位符。"""
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 40}}, request_id="req-r31-path")
        root = self.service.evidence_service.paths.root
        for item in record["multimedia_evidence"]:
            rel = self.service.evidence_service.relative_path(item["id"])
            self.assertTrue(rel.startswith("evidence/files/"), rel)
            target = root / pathlib.PurePosixPath(rel.replace("\\", "/"))
            self.assertTrue(target.exists(), f"相对路径指不到文件：{rel}")
            self.assertEqual(item["sha256"],
                             hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(f"/api/evidence/{item['id']}/file", item["uri"],
                             "uri 是 schema 认得的定位符，指回 T15 既有端点")

    def test_record_never_carries_payload_bytes_or_base64(self) -> None:
        """记录本体只放引用：没有 base64、没有 data_uri、没有 payload 键。"""
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 33}}, request_id="req-r31-nob64")
        text = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("base64", text.lower())
        self.assertNotIn("data:", text)
        for item in record["multimedia_evidence"]:
            self.assertFalse({"payload", "data_uri", "content"} & set(item))
            self.assertTrue(item.get("sha256"))
            self.assertTrue(item.get("media_type"))
            self.assertIn("generated_at", item)

    def test_items_are_appended_and_capped_at_the_schema_limit(self) -> None:
        """落账 = 追加，不删除既有条目；超过 schema ``maxItems=50`` 时截新追加的尾部。

        夹具口径：先正常落一次决定，再把记录里的证据列表**手动垫到 49 条**并把记录
        复位成可决状态（模拟"这条记录已经攒了很多证据"），第二次决定才会撞上上限。
        """
        action = "act-demo-0004"
        self.service.intervene({"action_id": action, "decision": "modify",
                                "modified_params": {PARAM: 21}}, request_id="req-r31-cap-0")
        stored = self.service.store.get(action)
        existing = [{"kind": "diff", "media_type": "text/x-diff", "sha256": f"{n:064x}",
                     "id": f"evd-pre-{n:03d}", "record_id": action} for n in range(49)]
        stored["multimedia_evidence"] = existing
        self.service.store.put(stored)
        # 复位成可决状态：上限这一格是"追加后的截断口径"，与决定本身的口径无关
        reopened = copy.deepcopy(stored)
        reopened["status"] = "pending_confirmation"
        reopened["requires_action"] = True
        reopened["user_intervention"] = None
        self.service.store.put(reopened)
        self.service.intervene({"action_id": action, "decision": "modify",
                                "modified_params": {PARAM: 22}}, request_id="req-r31-cap-1")
        final = self.service.store.get(action)
        self.assertEqual(2, self.service.evidence_wiring_records,
                         "第二次决定也走了一次落账")
        self.assertLessEqual(len(final["multimedia_evidence"]), ew.MAX_EVIDENCE_ITEMS)
        self.assertEqual(50, len(final["multimedia_evidence"]),
                         "既有 49 条必须一条不少，只截新追加的尾部")
        self.assertEqual(existing, final["multimedia_evidence"][:49], "历史条目逐字不动")


# --------------------------------------------------------------------------- #
# D. B3-3 证明：证据哈希被 entry_hash → 签名链覆盖
# --------------------------------------------------------------------------- #

class SignatureCoverageTests(unittest.TestCase):
    """§2.4 B3-3：只补测试证明，不改密码学（schema 1090 已明文此形状在签名范围内）。"""

    def setUp(self) -> None:
        self.root = scratch_dir("evidence-wiring-sign")
        self.service = service(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _decide(self, request_id: str) -> dict:
        record, _meta = self.service.intervene(
            {"action_id": "act-demo-0004", "decision": "modify",
             "modified_params": {PARAM: 77}}, request_id=request_id)
        return record

    def test_evidence_is_covered_by_entry_hash_and_verifies_ok(self) -> None:
        """落账后 ``verify_record.ok=true``：签名 valid、``entry_hash`` valid、链连续，
        且 ``entry_hash`` 与证据条目一起被重签通道盖住（不是伪造历史）。"""
        record = self._decide("req-r31-sign")
        self.assertTrue(record.get("signature"), "落账走 D-resign 通道：新签名必须盖上")
        self.assertTrue(record.get("entry_hash"))
        self.assertEqual(1, self.service.evidence_wiring_records)
        report = self.service.secretariat.verify_record(record)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual("valid", report["checks"]["signature"])
        self.assertEqual("valid", report["checks"]["entry_hash"])
        self.assertEqual("continuous", report["checks"]["prev_hash"])
        self.assertIn("multimedia_evidence",
                      json.dumps(record, ensure_ascii=False))

    def test_one_byte_tamper_of_an_evidence_sha256_breaks_verification(self) -> None:
        """篡改一个字节（sha256 的首位字符）→ ``entry_hash`` 复算失效 → 验签不过。"""
        record = self._decide("req-r31-tamper")
        tampered = copy.deepcopy(record)
        item = tampered["multimedia_evidence"][0]
        digest = item["sha256"]
        item["sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
        report = self.service.secretariat.verify_record(tampered)
        self.assertFalse(report["ok"], "证据哈希在签名范围内：改一个字符必须验不过")
        self.assertEqual("invalid", report["checks"]["entry_hash"])
        self.assertIn("SECRETARIAT_HASH_MISMATCH",
                      [problem["code"] for problem in report["problems"]])

    def test_entry_hash_changes_when_evidence_is_attached(self) -> None:
        """追加证据 ⇒ 记录 body 变 ⇒ ``entry_hash`` 变 ⇒ 旧签名对新 body 失效。
        （"落账不是改历史"的反证：新 body 必须由**新签名**覆盖才验得过。）"""
        record = self._decide("req-r31-entryhash")
        without = copy.deepcopy(record)
        without.pop("multimedia_evidence", None)
        for key in ("signature", "entry_hash", "tsa_timestamp"):
            without.pop(key, None)
        resigned = self.service.secretariat.sign_record(
            without, previous=record, allow_unapproved=True)
        self.assertNotEqual(record["entry_hash"], resigned["entry_hash"],
                            "证据条目必须在 entry_hash 的覆盖范围内")
        self.assertNotEqual(record["signature"], resigned["signature"],
                            "body 变了签名就得换：同一把签名不能盖两份 body")

    def test_sign_range_reduction_covers_the_attached_items(self) -> None:
        """``evidence.sign_range`` 的归约值随证据追加而变化，且与条目顺序无关
        （既有能力回归：证据哈希的规范归约是 entry_hash 的一部分）。"""
        record = self._decide("req-r31-signrange")
        digests = sorted(str(item["sha256"]) for item in record["multimedia_evidence"])
        self.assertEqual(2, len(digests))
        with_items = self.service.evidence_service.sign_range(record)
        self.assertTrue(with_items.startswith("sha256:"))
        self.assertNotEqual(self.service.evidence_service.sign_range({}), with_items)
        reordered = copy.deepcopy(record)
        reordered["multimedia_evidence"] = list(reversed(reordered["multimedia_evidence"]))
        self.assertEqual(with_items, self.service.evidence_service.sign_range(reordered),
                         "归约必须与顺序无关，否则同一份证据能算出两个哈希")
        for digest in digests:
            self.assertIn(digest, json.dumps(record))


# --------------------------------------------------------------------------- #
# E. 端到端：/intervene 单点接线（产品口径签名服务器）+ 分母/分子 + 回归
# --------------------------------------------------------------------------- #

class EvidenceWiringHttpTests(unittest.TestCase):
    """产品口径：``POST /intervene`` 决策完成 → 生成 → 落账 → 重签 → 读侧可查。

    每台服务器一套独立账本：B3-1 的分母/分子是**服务实例级**台账，
    共用一台会让用例互相攒次数，"一次决定 = 一次生成尝试"就钉不住。
    """

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"evidence-wiring-http-{case}")
        rows = copy.deepcopy(NATIVE) + [target(i) for i in range(20, 40)]
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

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.root, ignore_errors=True)

    def post(self, path: str, body: dict) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "POST", path, body)

    def get(self, path: str) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "GET", path)

    def modify(self, idx: int, value: int, **extra) -> tuple[int, dict, object]:
        return self.post("/intervene", {"action_id": f"{R3_PREFIX}{idx:02d}",
                                        "decision": "modify",
                                        "modified_params": {PARAM: value}, **extra})

    def stored(self, action_id: str) -> dict:
        status, _hd, data = self.get("/api/records")
        self.assertEqual(200, status, data)
        return next(r for r in data["records"] if r["id"] == action_id)

    def test_modify_intervene_wires_evidence_into_the_record(self) -> None:
        """端到端正题：modify 决定 → 响应体与 GET /api/records 都能看见两条证据
        （diff + diagram），分母 2 / 分子 2。"""
        status, _hd, data = self.modify(20, 44)
        self.assertEqual(200, status, data)
        self.assertEqual("modified", data["status"])
        kinds = [item["kind"] for item in data.get("multimedia_evidence") or []]
        self.assertEqual(["diff", "diagram"], kinds)
        stored = self.stored(f"{R3_PREFIX}20")
        self.assertEqual(kinds, [item["kind"] for item in stored["multimedia_evidence"]],
                         "读侧必须看见落账结果，不能只在响应里出现过")
        self.assertEqual(2, self.service.evidence_wiring.attempts)
        self.assertEqual(2, self.service.evidence_wiring.successes)
        self.assertEqual(1, self.service.evidence_wiring_records)
        self.assertEqual(0, self.service.evidence_wiring_records_failed)
        self.assertIsNone(self.service.evidence_wiring_problem)
        authoritative = self.service.store.get(f"{R3_PREFIX}20")
        report = self.service.secretariat.verify_record(authoritative)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual("valid", report["checks"]["signature"])

    def test_approve_intervene_wires_only_the_diagram(self) -> None:
        """approve 没有参数差异 → 只触发流程图（分母 1，不是"没触发就谎报 0/0"）。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{R3_PREFIX}21",
                                                     "decision": "approve"})
        self.assertEqual(200, status, data)
        self.assertEqual(["diagram"], [i["kind"] for i in data["multimedia_evidence"]])
        self.assertEqual(1, self.service.evidence_wiring.attempts)
        self.assertEqual(1, self.service.evidence_wiring.successes)

    def test_reject_intervene_triggers_no_evidence(self) -> None:
        """端到端零误触发：reject 不在触发口径内 → 记录里没有证据，分母不涨。"""
        status, _hd, data = self.post("/intervene", {"action_id": f"{R3_PREFIX}22",
                                                     "decision": "reject",
                                                     "reason": "先不动文件"})
        self.assertEqual(200, status, data)
        self.assertEqual("rejected", data["status"])
        self.assertFalse(data.get("multimedia_evidence"))
        self.assertFalse(self.stored(f"{R3_PREFIX}22").get("multimedia_evidence"))
        self.assertEqual(0, self.service.evidence_wiring.attempts)
        self.assertEqual(1, self.service.evidence_wiring.skip_reasons.get("status_not_completed"))
        self.assertEqual(1, self.service.interventions, "决定本身一个字都不受影响")

    def test_idempotent_replay_generates_the_evidence_once(self) -> None:
        """幂等重放提前返回，够不到接线点：同一次决定只生成一次、只签一次。"""
        key = "r31-replay-1"
        first = self.post("/intervene", {"action_id": f"{R3_PREFIX}23", "decision": "approve",
                                         "idempotency_key": key})
        self.assertEqual(200, first[0], first[2])
        attempts = self.service.evidence_wiring.attempts
        signed = self.service.signed_on_write
        replay = self.post("/intervene", {"action_id": f"{R3_PREFIX}23", "decision": "approve",
                                          "idempotency_key": key})
        self.assertEqual(200, replay[0], replay[2])
        self.assertEqual("true", replay[1].get("x-intervene-replay"), "重放标记必须如实")
        self.assertEqual(attempts, self.service.evidence_wiring.attempts, "分母不撒谎")
        self.assertEqual(signed, self.service.signed_on_write, "同一次决定只签一次")
        self.assertEqual(1, len(self.stored(f"{R3_PREFIX}23")["multimedia_evidence"]))

    def test_success_rate_meets_the_b3_1_bar_over_a_sample(self) -> None:
        """B3-1 判据：样本里成功率 ≥95%，且分母只数支持类型的生成尝试。"""
        for index, value in ((24, 100), (25, 90), (26, 80), (27, 70), (28, 60),
                             (29, 50), (30, 40), (31, 30), (32, 20), (33, 10)):
            self.assertEqual(200, self.modify(index, value)[0])
        facts = self.service.evidence_wiring.facts()
        self.assertEqual(20, facts["attempts"], "10 次决定 × (diff+diagram) = 20 次生成尝试")
        self.assertEqual(20, facts["successes"])
        self.assertEqual(0, facts["failures"])
        self.assertGreaterEqual(facts["success_rate"], 0.95, facts)
        self.assertEqual(["diff", "svg", "text_summary"], facts["denominator_kinds"])
        self.assertTrue(facts["screenshot_unimplemented"],
                        "D-7：截图类必须列成「未实现/待接入」清单，且不计入分母")
        self.assertNotIn("screenshot", facts["denominator_kinds"])

    def test_wiring_failure_never_breaks_the_decision(self) -> None:
        """证据是分析面：生成器抛异常时 /intervene 仍 200，决定落盘，故障计数留证。"""
        wiring = self.service.evidence_wiring
        original = wiring.generate

        def boom(record):
            raise RuntimeError("夹具：证据生成器炸了")

        wiring.generate = boom
        try:
            status, _hd, data = self.modify(34, 12)
        finally:
            wiring.generate = original
        self.assertEqual(200, status, data)
        self.assertEqual("modified", data["status"], "决定 ≠ 证据：生成器坏了也要记下决定")
        self.assertFalse(data.get("multimedia_evidence"), "失败的这一次不伪造任何证据")
        self.assertEqual(1, self.service.intervention_records, "S0-1 干预记录照常落盘")
        self.assertEqual(0, self.service.evidence_wiring_records)
        self.assertEqual(1, self.service.evidence_wiring_records_failed)
        self.assertEqual("EVIDENCE_WIRING_PERSIST_FAILED",
                         self.service.evidence_wiring_problem["code"])
        self.assertIn("夹具：证据生成器炸了", self.service.evidence_wiring_problem["reason"])

    def test_wiring_adds_no_new_http_route(self) -> None:
        """证据面常量与契约口径（0.6.0 L1 后：61 路由 / 44 声明 / 44 挂载）。

        历史：R3-1 时这条钉的是「54 / (44, 42)」—— 那时 `API_PATH_EVIDENCE` 与
        `API_PATH_COMPLIANCE_EXPORT` 都是「声明了常量没人接住」。0.6.0 L1 把
        `/api/evidence/{id}`、`/{id}/file`、`/api/records/{id}/evidence`、
        `/api/compliance/export` 全部接上（老板已批准），所以基线整体上移。
        **常量字面值一字未动**，动的只是"有没有分支接住它"。
        """
        routes = {(row["path"], row["verb"]) for row in CONTRACT["routes"]}
        self.assertEqual(61, len(CONTRACT["routes"]),
                         "0.6.0 L1 八功能接线后的契约路由数基线是 61")
        self.assertIn(("/intervene", "POST"), routes, "决定流入口不变")
        self.assertEqual("/api/evidence", sv.API_PATH_EVIDENCE, "证据面常量一字未动")
        self.assertEqual("/api/evidence", CONTRACT["path_constants"]["API_PATH_EVIDENCE"])
        declared = CONTRACT["declared_vs_mounted"]
        self.assertEqual((44, 44), (declared["declared"], declared["mounted"]),
                         "44 个常量必须全部挂载（历史两条欠账已在 0.6.0 L1 收口）")
        self.assertFalse(any("API_PATH_EVIDENCE" in row for row in declared["unmounted"]),
                         "0.6.0 L1 之后 API_PATH_EVIDENCE 必须已挂载，不许再出现在未挂载清单")
        for path, _verb in routes:
            self.assertFalse(path.startswith("/api/evidence-wiring"),
                             "接线不许顺手开新端点")
            self.assertFalse(path.startswith("/api/multimedia"),
                             "multimedia_evidence 是记录字段，不是新端点")
        status, _hd, data = self.get("/api/evidence-wiring")
        self.assertEqual(404, status, data)
        # 现状：未知路径落到静态文件分支（STATIC_NOT_FOUND），API 子路径才出 ROUTE_NOT_FOUND；
        # 两个都是 404，本用例钉的是"没有新端点被接住"，不是既有 404 口径的措辞。
        self.assertIn((data or {}).get("code"), {"ROUTE_NOT_FOUND", "STATIC_NOT_FOUND"})

    def test_existing_evidence_surface_still_works(self) -> None:
        """既有 evidence 面回归：T13 侧 add/list/feedback、T14 读单条、T15 读字节流。

        先走一次真实决定：数据根的证据目录由产品路径备好（懒口径），
        之后 T13 侧的主动上传（截图）与读侧端点照常工作 —— 接线没有挤掉既有面。
        """
        self.assertEqual(200, self.modify(37, 19)[0])
        view = self.service.evidence_service.add(
            "act-demo-0004", kind="screenshot",
            payload={"data_uri": "data:image/png;base64,"
                   + "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="},
            caption="回归夹具：一像素 PNG")
        self.assertEqual("screenshot", view["kind"])
        listed = self.service.evidence_service.list("act-demo-0004")
        self.assertIn(view["id"], [row["id"] for row in listed])
        voted = self.service.evidence_service.feedback(view["id"], vote="like")
        self.assertEqual(1, voted["feedback"]["like"])
        status, heads, data = self.get(f"/api/evidence/{view['id']}")
        self.assertEqual(200, status, data)
        self.assertEqual(view["id"], data["evidence"]["id"], "T14 读单条仍可用")
        status, heads, raw = http_request(self.host, self.port, "GET",
                                          f"/api/evidence/{view['id']}/file", binary=True)
        self.assertEqual(200, status)
        self.assertEqual("image/png", heads.get("content-type"))
        self.assertEqual(view["sha256"], hashlib.sha256(raw).hexdigest(), "T15 字节流逐字对账")

    def test_record_ledger_and_chain_stay_intact_after_wiring(self) -> None:
        """接线后账本与链完好：记录数只多一条 S0-1 干预记录、审计链验过、
        S0-1 的 ``intervention_history`` 引用列表不受接线影响（里面不塞证据条目）。"""
        before = len(self.service.store.all())
        self.assertEqual(200, self.modify(35, 15)[0])
        self.assertEqual(before + 1, len(self.service.store.all()),
                         "只多一条独立签名的干预记录，被干预记录是原地更新")
        stored = self.service.store.get(f"{R3_PREFIX}35")
        self.assertNotIn("intervention_history", stored,
                         "S0-1 的干预记录是独立记录：目标记录本体不塞引用列表（现状口径）")
        view = self.stored(f"{R3_PREFIX}35")
        history = view.get("intervention_history") or []
        self.assertEqual(1, len(history), "读侧组装出的引用列表照常一条")
        for row in history:
            self.assertNotIn("multimedia_evidence", row,
                             "证据只落在被干预记录本体，不塞进 S0-1 引用列表")
        self.assertTrue(self.service.require_audit_chain()["ok"], "审计链必须验得过")
        self.assertTrue(self.service.verify_audit_chain()["ok"])
        self.assertEqual(0, self.service.sign_failures)

    def test_intervention_record_stays_the_chain_head_after_evidence(self) -> None:
        """接线排在 S0-1 之前：那条独立签名干预记录的 ``prev_hash`` 接的就是
        **带证据的目标记录**，链尾仍是干预记录（R1 验收 TP-13 钉住的链头不变量，
        全量首跑时被"证据放后面"的顺序撞红过，本用例把顺序钉死）。"""
        self.assertEqual(200, self.modify(38, 16)[0])
        target = self.service.store.get(f"{R3_PREFIX}38")
        self.assertTrue(target.get("multimedia_evidence"), "目标记录带证据")
        events = [row for row in self.service.store.all()
                  if str(row.get("id") or "").startswith("act_iv_")]
        self.assertEqual(1, len(events), "一次决定一条独立签名干预记录")
        intervention = events[0]
        self.assertEqual(target["entry_hash"], intervention["prev_hash"],
                         "干预记录的 prev_hash 接带证据的 body：证据不能插到链尾之后")
        self.assertTrue(self.service.secretariat.verify_record(intervention)["ok"])
        self.assertTrue(self.service.secretariat.verify_record(target)["ok"])

    def test_evidence_items_are_visible_through_the_stream_broadcast(self) -> None:
        """落账即广播：``reason=evidence_attached`` 的那一帧带的是落账后的记录本体
        （既有 T9/SSE 通道，零新事件类型；界面不用等 4 秒轮询）。"""
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
            self.assertEqual(200, self.modify(36, 17)[0])
            frames = drain()
        finally:
            hub.unsubscribe(sub)
        attached = [frame for frame in frames
                    if str(frame.get("type") or "").endswith("updated")
                    and (frame.get("data") or {}).get("reason") == "evidence_attached"]
        self.assertTrue(attached, "落账必须广播一帧 evidence_attached")
        record = (attached[0].get("data") or {}).get("record") or {}
        self.assertTrue(record.get("multimedia_evidence"),
                        "广播里的记录要已经带着证据，不是决定前那份")
        self.assertTrue(record.get("signature"), "广播出去的必须是重签后的那份")


# --------------------------------------------------------------------------- #
# G. R3-4 收口：证据展示面所读的三件事（T16 计数 / T15 字节与指纹 / D-7 截图）
# --------------------------------------------------------------------------- #

class EvidenceLedgerFaceTests(unittest.TestCase):
    """R3-4（TASK-R3-4 §2.3 / §2.6 / §2.1）后端口径：前端展示面**只读**这三件事。

    钉什么：

    1. **点赞点踩只落 T16**（``EvidenceService.feedback``）：计数长在证据条目上，
       重读一次还在（落盘）；`vote` 只认 like / dislike 这一对，越界就显码拒绝且
       **一格都不动**；证据不存在就说 ``EVIDENCE_NOT_FOUND``，不去猜 id。
    2. **数字只有一个来源**：落账记录行里的 ``multimedia_evidence`` 条目**不带**
       ``feedback``（``evidence_wiring`` 逐字裁剪），所以界面显示的数字必然来自
       账本读数——前端没有第二个可改的地方（配合前端闸门「不许自己 +1」）。
    3. **T15 的字节就是指纹的凭据**：文件字节流复算的 sha256 与记录里的 ``sha256``
       逐字相等，``bytes`` 等于真实长度；界面端出来的「这份证据的指纹」有据可查。
    4. **D-7**：自动接线永不产截图（分母里没有它），所以前端对截图条目只能说
       「未实现/待接入」。
    """

    def setUp(self) -> None:
        self.root = scratch_dir("evidence-ledger-face")
        self.service = service(self.root)
        self.addCleanup(shutil.rmtree, self.root, True)

    def wired(self, request_id: str) -> tuple[dict, dict]:
        """走产品路径产一条带证据的记录（modify：改参数 + 有候选 → 两条计划）。"""
        return self.service.intervene({"action_id": "act-demo-0004", "decision": "modify",
                                       "modified_params": {PARAM: 50}},
                                      request_id=request_id)

    def test_feedback_votes_land_on_the_evidence_entry_and_survive_a_reread(self) -> None:
        """§2.3：两次 like + 一次 dislike 落在证据条目上，重读一次数字还在（T16 落盘）。"""
        record, _meta = self.wired("req-r34-fb-land")
        items = record["multimedia_evidence"]
        self.assertTrue(items, "modify 决定应接线出证据")
        ledger = self.service.evidence_service
        first = next(item for item in items if item["kind"] == "diff")
        entry_id = str(first["id"])
        self.assertEqual({"like": 0, "dislike": 0}, ledger.get(entry_id)["feedback"],
                         "新证据的计数从 0 起算，界面不从空气里拿数字")
        self.assertEqual(1, ledger.feedback(entry_id, vote="like")["feedback"]["like"])
        self.assertEqual(2, ledger.feedback(entry_id, vote="like")["feedback"]["like"])
        after = ledger.feedback(entry_id, vote="dislike")["feedback"]
        self.assertEqual({"like": 2, "dislike": 1}, after)
        self.assertEqual({"like": 2, "dislike": 1}, ledger.get(entry_id)["feedback"],
                         "计数在索引账上，不在内存里飘")

    def test_a_vote_outside_like_dislike_is_refused_and_moves_no_count(self) -> None:
        """§2.3：`vote` 的封闭口径就是 like / dislike；越界显码拒绝，计数一格都不动。"""
        record, _meta = self.wired("req-r34-fb-vote")
        ledger = self.service.evidence_service
        entry_id = str(record["multimedia_evidence"][0]["id"])
        before = ledger.get(entry_id)["feedback"]
        with self.assertRaises(ev_mod.EvidenceError) as ctx:
            ledger.feedback(entry_id, vote="up")
        self.assertEqual("EVIDENCE_VOTE_INVALID", ctx.exception.code)
        self.assertEqual("vote", ctx.exception.field)
        self.assertEqual(["like", "dislike"], ctx.exception.detail["allowed"])
        self.assertEqual("up", ctx.exception.detail["received"])
        self.assertEqual(before, ledger.get(entry_id)["feedback"], "被拒绝的投票不许留下任何计数")

    def test_feedback_on_an_unknown_evidence_id_says_not_found_without_guessing(self) -> None:
        """§2.3：id 不在账上就明说 EVIDENCE_NOT_FOUND（前端据此显码，不粉成成功）。"""
        self.wired("req-r34-fb-404")
        ledger = self.service.evidence_service
        with self.assertRaises(ev_mod.EvidenceError) as ctx:
            ledger.feedback("evd-not-on-the-ledger", vote="like")
        self.assertEqual("EVIDENCE_NOT_FOUND", ctx.exception.code)
        self.assertEqual("evd-not-on-the-ledger", ctx.exception.detail["evidence_id"])
        self.assertEqual([], [row for row in ledger.list("act-demo-0004")
                              if row["id"] == "evd-not-on-the-ledger"])

    def test_the_landed_record_row_carries_no_feedback_so_counts_have_one_source(self) -> None:
        """§2.6：记录行不带 ``feedback``（逐字裁剪）→ 界面的数字只能来自账本读数。

        这条是前端「计数不许自己 +1」闸门的地基：记录行里没有可改的计数，
        账本读数（T14/T16）就是唯一来源；展示层再怎么算也算不出新数字。
        """
        record, _meta = self.wired("req-r34-row-shape")
        items = record["multimedia_evidence"]
        self.assertTrue(items)
        for item in items:
            self.assertNotIn("feedback", item, "记录行把 feedback 裁掉（T16 计数归证据账本）")
        self.assertEqual([], VALIDATOR.collect(record), "裁剪后的记录行仍逐字过 schema")
        ledger = self.service.evidence_service
        for item in items:
            self.assertIn("feedback", ledger.get(str(item["id"])),
                          "计数在证据账本里，一格都没丢")

    def test_the_served_bytes_are_what_the_recorded_sha256_claims(self) -> None:
        """§2.6：T15 的字节流复算 sha256 == 记录里的 ``sha256``，``bytes`` == 真实长度。"""
        record, _meta = self.wired("req-r34-t15")
        for item, raw in item_files(self.service, record):
            self.assertTrue(raw, "证据文件本体必须真的在盘上")
            self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item["bytes"], len(raw))
            payload = self.service.evidence_file_response(str(item["id"]))
            self.assertEqual(raw, payload["body"], "T15 发的就是盘上那份字节")
            self.assertEqual(item["sha256"], payload["sha256"], "发的字节与指纹同源")
            self.assertEqual(item["media_type"], payload["content_type"],
                             "Content-Type 来自落账的 media_type，不是界面猜的")
            self.assertIn(f"/api/evidence/{item['id']}/file", item["uri"],
                          "uri 指回本模块的 T15 端点（schema 明文）")

    def test_the_auto_wiring_never_produces_a_screenshot_so_the_face_says_pending(self) -> None:
        """§2.1（D-7）：自动接线只产 diff / diagram，截图类永远「未实现/待接入」。"""
        wiring, root = wiring_on_scratch("evidence-face-d7")
        self.addCleanup(shutil.rmtree, root, True)
        for decision, params in (("modify", {PARAM: 50}), ("approve", None)):
            produced = wiring.generate(decided(3, decision, params=params))
            kinds = [item["kind"] for item in produced["items"]]
            self.assertTrue(kinds, "这两类决定都应接线出证据")
            self.assertNotIn("screenshot", kinds, "自动接线永不产截图（D-7 不计分母）")
            self.assertTrue(set(kinds) <= {"diff", "diagram"}, f"接线产出越界：{kinds}")
        self.assertTrue(any("未实现" in line or "待接入" in line
                            for line in ew.SCREENSHOT_UNIMPLEMENTED),
                        "docs 口径逐字：截图类未实现/待接入（前端文案的落点）")
        self.assertNotIn("screenshot", ew.DENOMINATOR_KINDS)


# --------------------------------------------------------------------------- #
# H. 口径落地：模块自检 + docs/07 §11.18 条款锚点
# --------------------------------------------------------------------------- #

class WiringDisciplineTests(unittest.TestCase):
    """口径与卫生：模块自检可跑、docs 落条款、计数器不进响应载荷、签名域零改动。"""

    def test_module_self_check_runs_green(self) -> None:
        """``python -m agent_explainer.evidence_wiring`` 的自检在测试环境跑得通
        （临时目录走 ``tests/scratch.py`` 替身，PM-VERDICT-R1-2a §3.3）。"""
        with use_scratch_tempfile():
            self.assertEqual(0, ew._self_check())

    def test_denominator_kinds_are_the_supported_three(self) -> None:
        """§2.1 分母三类逐字：diff / SVG / 文本摘要；截图类列清单不计分母。"""
        self.assertEqual(("diff", "svg", "text_summary"), ew.DENOMINATOR_KINDS)
        self.assertTrue(any("截图" in line or "抓屏" in line
                            for line in ew.SCREENSHOT_UNIMPLEMENTED))
        self.assertTrue(any("未实现" in line or "待接入" in line
                            for line in ew.SCREENSHOT_UNIMPLEMENTED))

    def test_counters_do_not_enter_the_response_payload(self) -> None:
        """分母/分子台账是服务端属性，不塞进响应载荷（与 R7-1/S0-1 同一纪律）。"""
        root = scratch_dir("evidence-wiring-payload")
        try:
            svc = service(root)
            record, meta = svc.intervene({"action_id": "act-demo-0004",
                                          "decision": "modify",
                                          "modified_params": {PARAM: 5}},
                                         request_id="req-r31-payload")
            text = json.dumps({"record": record, "meta": meta}, ensure_ascii=False)
            for key in ("attempts", "successes", "success_rate", "degraded"):
                self.assertNotIn(key, text, f"计数器 {key} 不该出现在响应里")
            self.assertIn("multimedia_evidence", text, "落账结果要在记录里可见")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_signature_fields_are_untouched(self) -> None:
        """§1.1 铁律：SIGNATURE_FIELDS 11 字段一字未动（证据靠 entry_hash 进签名域）。"""
        from agent_explainer import secretariat as sec
        self.assertEqual(11, len(sec.SIGNATURE_FIELDS))
        self.assertNotIn("multimedia_evidence", sec.SIGNATURE_FIELDS)
        self.assertIn("entry_hash", sec.SIGNATURE_FIELDS)

    def test_docs_07_section_11_18_documents_the_denominator(self) -> None:
        """§2.6：触发口径与分母定义落 docs/07 §11.18（冻结区不动，只追加）。"""
        text = (ROOT / "docs" / "07-secretariat-and-extensions.md").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("### 11.18 "))
        start = text.find("### 11.18 ")
        body = text[start:]
        self.assertEqual(0, sum(1 for line in body.splitlines()
                                if line.startswith("## ")),
                         "追加小节不得新开一级章节（docs 术语闸门）")
        for needle in ("生成尝试", "256KB", "未实现/待接入", "entry_hash", "D-resign",
                       "multimedia_evidence", "B3-1", "B3-3"):
            self.assertIn(needle, body, f"§11.18 必须写明 {needle}")
        self.assertIn("### 11.17 ", text[:start], "§11.18 必须在 §11.17 之后（只追加）")
