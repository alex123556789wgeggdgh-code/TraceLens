"""S0-4 独立验收测试：S0 共享前置四项（S0-1 / S0-2 / S0-2b / S0-3）的条款复现。

本文件由 **QA（独立验收方）** 新增，只做验收条款的**独立复现**，不改任何生产代码、
不改任何既有测试与 docs。编号与 `_pm-dispatch/REPORT-S0-4.md` 的验收用例清单一一对应。

运行（全程离线；HTTP 只走 127.0.0.1 环回）：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest discover -s tests -p "test_s0_acceptance.py"

用例 → 验收条款映射：

* QA-S0-01 一次干预 = 恰好一条独立 signed 记录（验签 / entry_hash / 链连续 / 过 schema）
* QA-S0-02 `intervention_history` 零落盘，只在 `/api/records` 响应里组装
* QA-S0-03 读时组装：≤50、时间序、归组不重复、入参零改写、孤儿不隐藏
* QA-S0-04 幂等 replay 不重复追加
* QA-S0-05 伪造入参（`intervention_history` / `signature` / `entry_hash` / `prev_hash` /
  `user_intervention`）被拒且零写入
* QA-S0-06 白名单 9 字段可合并 + `changed_fields` 逐条正确
* QA-S0-07 名单外新键拒绝（不进记录、不进 changed）
* QA-S0-08 受保护字段剥离并逐名上报（含 `intervention_history` / `human_owner`）
* QA-S0-09 blank 不覆盖
* QA-S0-10 单一实现：server / ingest 同一名单对象；`merge_facts` 是 `merge_facts_full` 包装
* QA-S0-11 合并产物过 schema + 签名有效 + 合并在签名之前（合入值参与 `entry_hash`）
* QA-S0-12 9 个 5.0 字段逐个过适配器闸门（`_assert_shape`）
* QA-S0-13 名单只增 9 项不改旧（14 → 23）；`REQUIRED_RECORD_KEYS` 27 项不动
* QA-S0-14 端到端落库：真 HTTP `POST /api/import` → 存储可读、schema 合法、签名有效
* QA-S0-15 受保护字段仍拒（含真 HTTP，逐个）；名单外新键仍 400

S0-3（文档）的用例在 `tests/test_docs_terminology.py`（QA-S0-16 ~ QA-S0-23）。
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
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer import ingest as ingest_mod  # noqa: E402
from agent_explainer import intervention_history as ih  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import trace_adapter as ta  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLES = json.loads((ROOT / "samples" / "action_records.json")
                     .read_text(encoding="utf-8"))["records"]
DECIDABLE = ("pending_confirmation", "expired")
STATUS_BY_DECISION = {"approve": "approved", "modify": "modified", "reject": "rejected"}
#: S0-2b 之前的 14 项可选字段（改前快照，用来钉"只追加、不改旧、不重排"）
OPTIONAL_BEFORE_S0_2B = (
    "received_at", "sequence_no", "agent_name", "depth", "run_id",
    "requires_action_reason", "action_due_at", "uncertainty", "impact_scope",
    "error", "suggested_next", "context", "trace_link", "labels",
)
#: 27 项必填（改前 = 改后；S0-2b 铁律 2）
REQUIRED_SNAPSHOT = (
    "schema_version", "id", "timestamp", "agent_id", "actor_id", "workspace_id",
    "session_id", "parent_action_id", "action_type", "tool_used", "intent",
    "rationale", "result_summary", "human_readable", "confidence", "cost",
    "latency_ms", "status", "requires_action", "intervention_options",
    "user_intervention", "execution_receipt", "risk_level", "policy", "source",
    "demo_data", "synthetic",
)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def call(base: str, path: str, body: object | None = None,
         method: str = "GET") -> tuple[int, dict, dict]:
    """返回 (status, headers(小写名), json body)；4xx 也把响应体带回来。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None

    def once():
        request = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, raw, headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            status, raw, headers = exc.code, exc.read(), dict(exc.headers)
            exc.close()
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return status, {k.lower(): v for k, v in headers.items()}, payload

    # M2-PRE 4（PM-VERDICT-R1-9 裁决② / R5-1 接线）：连接建立阶段瞬态任何动词重连一次；
    # 请求已发出后只有幂等动词（GET/HEAD/OPTIONS/TRACE）重放一次，POST/PUT/PATCH/DELETE
    # 绝不重放；HTTPError 状态码与断言级失败绝不重试。请求构造与响应解析逐行不动，返回形状一字不改。
    return fx.urlopen_resilient(once, verb=method, path=path, timeout=20.0)


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def sample(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in SAMPLES if r["id"] == action_id))


def expansion_values() -> dict:
    """9 个 5.0 可合并字段各一个 schema 合法值（形状取自 schema，独立于执行员工夹具）。"""
    return {
        "group_id": "grp-qa-s0",
        "shared_users": [{"did": cs.did_from_name("qa-s0-sharee", kind="user"),
                          "permission": "view",
                          "shared_at": "2026-11-14T00:00:00Z"}],
        "multimedia_evidence": [{"kind": "screenshot", "evidence_id": "ev-qa-s0",
                                 "media_type": "image/png",
                                 "uri": "local://evidence/qa-s0.png",
                                 "sha256": "b" * 64, "bytes": 1024,
                                 "caption": "QA 合成证据（非真实采集）",
                                 "provided_by": "server"}],
        "causal_explanation": {"counterfactual": "若当时只读不写，目标文件不会被改",
                               "alternatives": [{"option": "只读",
                                                 "expected": "文件保持原样",
                                                 "why": "读不触发写入"}],
                               "model": "template", "language": "zh"},
        "rollback_ref": "act-demo-0002",
        "rollback_state": "none",
        "improvement_suggestion": {"id": "sug-qa-s0", "tool": "files.upload",
                                   "param": "overwrite", "direction": "down",
                                   "occurrences": 2, "status": "pending"},
        "evidence_feedback": {"like": 2, "dislike": 0,
                              "updated_at": "2026-11-14T00:00:00Z"},
        "confidence_source": {"method": "logprobs", "logprob": 0.75, "samples": 8},
    }


def live_interventions(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("action_type") or "") == ih.INTERVENTION_ACTION_TYPE]


# --------------------------------------------------------------------------- #
# S0-1：写入侧 + 读时组装（真 HTTP，持久化 + 入库签名）
# --------------------------------------------------------------------------- #

class S01InterventionLedgerTests(unittest.TestCase):
    """S0-1 验收：一次干预 = 恰好一条独立 signed 记录；历史零落盘；replay 不重复。"""

    def setUp(self) -> None:
        self.root = scratch_dir("qa-s0-4-intervention")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store_path = self.root / "records.jsonl"
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=None, quiet=True,
            records_store_path=self.store_path,
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(),
                                self.thread.join(5)))
        self.base = self.server.url()

    # -- 夹具 ---------------------------------------------------------------
    def view(self) -> list[dict]:
        status, _hd, data = call(self.base, sv.API_PATH_RECORDS)
        self.assertEqual(200, status, data)
        return data["records"]

    def view_of(self, action_id: str) -> dict:
        for row in self.view():
            if row.get("id") == action_id:
                return row
        raise AssertionError(f"视图里找不到 {action_id}")

    def stored(self, action_id: str) -> dict:
        status, _hd, data = call(self.base, f"/api/records/{action_id}")
        self.assertEqual(200, status, data)
        return data

    def disk_rows(self) -> list[dict]:
        out: list[dict] = []
        for line in self.store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("op") == "put" and isinstance(entry.get("record"), dict):
                out.append(entry["record"])
        return out

    def pick_decidable(self, risks: tuple[str, ...] | None = None) -> dict:
        rows = [r for r in self.view()
                if r.get("demo_data") and str(r.get("status")) in DECIDABLE
                and not isinstance(r.get("user_intervention"), dict)]
        if risks:
            rows = [r for r in rows if str(r.get("risk_level")) in risks]
        self.assertTrue(rows, "演示数据里没有可决记录")
        return sorted(rows, key=lambda r: str(r.get("id")))[0]

    def intervene(self, action_id: str, decision: str = "approve", **extra):
        body: dict = {"action_id": action_id, "decision": decision}
        body.update(extra)
        return call(self.base, "/intervene", body, "POST")

    def verify(self, record: dict) -> dict:
        status, _hd, data = call(self.base, "/api/secretariat/verify",
                                 {"record": record}, "POST")
        self.assertEqual(200, status, data)
        return data["results"][0]

    # -- QA-S0-01 -----------------------------------------------------------
    def test_qa_s0_01_one_intervention_one_signed_record(self) -> None:
        first = self.pick_decidable(("low", "medium"))
        status, headers, body = self.intervene(first["id"], "approve",
                                               idempotency_key="qa-s0-01-a")
        self.assertEqual(200, status, body)
        audit_hash = headers.get("x-intervene-audit-hash") or ""
        self.assertTrue(audit_hash.startswith("sha256:"), headers)

        mine = live_interventions(self.disk_rows())
        self.assertEqual(1, len(mine), "一次干预必须恰好追加一条干预记录")
        record = mine[0]
        self.assertEqual(ih.INTERVENTION_ACTION_TYPE, record["action_type"])
        self.assertEqual(first["id"], record.get("parent_action_id"),
                         "target_action_id 落在 schema 既有链字段 parent_action_id")
        self.assertEqual(STATUS_BY_DECISION["approve"], record.get("status"),
                         "schema C2：approve ↔ approved")
        ui = record.get("user_intervention") or {}
        self.assertEqual("approve", ui.get("decision"))
        self.assertEqual(audit_hash, ui.get("audit_entry_hash"),
                         "记录 ↔ 审计双向可追：审计 entry_hash 原样写进记录")
        self.assertEqual(first.get("agent_id"), record.get("agent_id"), "agent_id 继承")
        self.assertEqual(first.get("risk_level"), record.get("risk_level"), "risk_level 继承")
        self.assertEqual([], schema_errors(record), "干预记录必须过 schema v1.1")

        checks = self.verify(record)
        self.assertEqual("valid", checks["checks"]["signature"], checks["problems"])
        self.assertEqual("valid", checks["checks"]["entry_hash"], checks["problems"])
        self.assertIn(checks["checks"]["prev_hash"], ("continuous", "genesis"),
                      "entry_hash 链必须连续")

        # 第二条干预接同一条链（D-resign / R1-2b 之后的链口径）：两次干预之间多了一次
        # **授权写回** —— 决定流把目标记录重签落盘（同一条 Agent 链），所以后一条干预的
        # prev_hash 接的是它写入时刻的链头 = 那次写回重签后的目标记录哈希；链仍连续，
        # 而且"谁在什么时候签了什么"的顺序可追（一次写回一次签名，没有隐形中间态）。
        other = next(r for r in self.view()
                     if r.get("id") != first["id"] and r.get("demo_data")
                     and str(r.get("status")) in DECIDABLE
                     and not isinstance(r.get("user_intervention"), dict)
                     and r.get("risk_level") in ("low", "medium"))
        status, _hd, body = self.intervene(other["id"], "reject", reason="QA 第二条")
        self.assertEqual(200, status, body)
        mine = live_interventions(self.disk_rows())
        self.assertEqual(2, len(mine))
        heads = {row.get("entry_hash") for row in self.disk_rows() if row.get("entry_hash")}
        self.assertIn(mine[1]["prev_hash"], heads,
                      "两次干预必须接同一条 entry_hash 链：prev_hash 必须是在案链头")
        written_back = self.stored(other["id"])
        self.assertTrue(written_back.get("entry_hash"),
                        "被干预的目标记录在授权写回点重签后必须有 entry_hash")
        self.assertEqual(written_back["entry_hash"], mine[1]["prev_hash"],
                         "链头就是那次授权写回（重签后的目标记录）：顺序可追")
        target_checks = self.verify(written_back)
        self.assertEqual("valid", target_checks["checks"]["signature"],
                         "D-resign（R1-2b）：干预之后目标记录必须验签通过（P-3 的修复面）")
        self.assertEqual("valid", target_checks["checks"]["entry_hash"],
                         target_checks["problems"])

    # -- QA-S0-02 -----------------------------------------------------------
    def test_qa_s0_02_history_is_never_written_to_disk(self) -> None:
        target = self.pick_decidable()
        status, _hd, body = self.intervene(target["id"], "approve",
                                           idempotency_key="qa-s0-02")
        self.assertEqual(200, status, body)

        raw = self.store_path.read_text(encoding="utf-8")
        self.assertNotIn("intervention_history", raw,
                         "记录落盘文件里绝不允许出现 intervention_history")
        for row in self.disk_rows():
            self.assertNotIn("intervention_history", row)
        self.assertNotIn("intervention_history", self.stored(target["id"]),
                         "单条端点读的是存储本体，也不带这个键")

        entry = self.view_of(target["id"]).get("intervention_history")
        self.assertIsInstance(entry, list, "视图里必须挂上 intervention_history")
        self.assertEqual(1, len(entry))
        item = entry[0]
        self.assertIsInstance(item, dict, "schema :1399 只要求条目是对象")
        for key in ("target_action_id", "decision", "actor_id", "audit_entry_hash",
                    "entry_hash", "signed"):
            self.assertIn(key, item, f"视图条目缺 {key}（无法追回签名记录/审计事件）")
        self.assertEqual(target["id"], item["target_action_id"])
        self.assertTrue(item["signed"])

    # -- QA-S0-03 -----------------------------------------------------------
    def test_qa_s0_03_assembly_caps_orders_and_keeps_orphans(self) -> None:
        """组装器纯函数口径：上限 50（schema :1398）、时间升序、入参零改写、孤儿可见。"""
        target = {"id": "act-qa-target", "action_type": "delegate", "timestamp":
                  "2026-01-01T00:00:00Z", "status": "succeeded"}
        rows = [copy.deepcopy(target)]
        for index in range(60):
            rows.append({
                "id": f"iv-qa-{index:03d}",
                "action_type": ih.INTERVENTION_ACTION_TYPE,
                "parent_action_id": target["id"],
                "source": {"adapter": ih.INTERVENTION_ADAPTER},
                "timestamp": f"2026-02-01T00:{index // 60:02d}:{index % 60:02d}Z",
                "status": "approved",
                "actor_id": "local-user",
                "user_intervention": {"decision": "approve", "decided_by": "local-user",
                                      "decided_by_role": "local", "recorded": True},
                "entry_hash": f"sha256:{index:064d}",
                "signature": "c2ln",
            })
        snapshot = copy.deepcopy(rows)
        out = ih.assemble_intervention_history(rows)
        self.assertEqual(snapshot, rows, "组装器不得改写入参（零落盘的前提）")
        self.assertEqual(1, len(out), "有归属的干预记录从顶层归组掉（不重复列出）")
        history = out[0]["intervention_history"]
        self.assertEqual(ih.HISTORY_LIMIT, len(history), "上限 50（= schema maxItems）")
        self.assertEqual(50, len(history))
        stamps = [h["timestamp"] for h in history]
        self.assertEqual(sorted(stamps), stamps, "必须按时间升序")
        self.assertEqual("2026-02-01T00:00:10Z", stamps[0], "保留最近 50 条")
        self.assertEqual("2026-02-01T00:00:59Z", stamps[-1])

        # 孤儿干预记录（目标不在本批）不得被隐藏
        orphan = copy.deepcopy(rows[1])
        orphan["id"] = "iv-qa-orphan"
        orphan["parent_action_id"] = "act-gone"
        out2 = ih.assemble_intervention_history([orphan])
        self.assertEqual(1, len(out2), "孤儿必须留在顶层：绝不让数据凭空消失")
        self.assertNotIn("intervention_history", out2[0])

    # -- QA-S0-04 -----------------------------------------------------------
    def test_qa_s0_04_replay_does_not_append_twice(self) -> None:
        target = self.pick_decidable()
        key = "qa-s0-04-idem"
        status, headers, body = self.intervene(target["id"], "approve", idempotency_key=key)
        self.assertEqual(200, status, body)
        self.assertEqual("false", headers.get("x-intervene-replay"))
        status, headers, body = self.intervene(target["id"], "approve", idempotency_key=key)
        self.assertEqual(200, status, body)
        self.assertEqual("true", headers.get("x-intervene-replay"), "同 key 必须命中 replay")
        self.assertEqual(1, len(live_interventions(self.disk_rows())),
                         "replay 绝不允许重复追加干预记录")
        self.assertEqual(1, self.server.service.intervention_records)

    # -- QA-S0-05 -----------------------------------------------------------
    def test_qa_s0_05_forged_fields_are_rejected_with_zero_writes(self) -> None:
        target = self.pick_decidable()
        before = self.stored(target["id"])
        forged: dict[str, object] = {
            "intervention_history": [{"target_action_id": target["id"],
                                      "decision": "approve", "actor_id": "attacker"}],
            "signature": "ZGVhZGJlZWY=",
            "entry_hash": "0" * 64,
            "prev_hash": "1" * 64,
            "user_intervention": {"decision": "approve", "recorded": False},
        }
        for field, value in forged.items():
            status, _hd, data = self.intervene(target["id"], "approve", **{field: value})
            self.assertEqual(400, status, f"{field} 必须被入参闸门拒：{data}")
            self.assertEqual("INTERVENE_FIELD_UNKNOWN", data.get("code"), data)
        self.assertEqual([], live_interventions(self.disk_rows()), "被拒的入参零写入")
        after = self.stored(target["id"])
        self.assertEqual(before, after, "记录本体一个字都不许被伪造入参改动")


# --------------------------------------------------------------------------- #
# S0-2：合并器（纯函数层）
# --------------------------------------------------------------------------- #

class S02MergeContractTests(unittest.TestCase):
    """S0-2 验收：白名单合并 / 名单外拒绝 / 受保护剥离 / blank / 单一实现。"""

    def base(self) -> dict:
        rec = sample("act-demo-0001")
        rec["group_id"] = "grp-before"
        return rec

    # -- QA-S0-06 -----------------------------------------------------------
    def test_qa_s0_06_whitelist_merges_with_change_log(self) -> None:
        before = self.base()
        incoming = expansion_values()
        merged, changed, stripped = ingest_mod.merge_facts_full(before, incoming)
        self.assertEqual([], stripped, "入参没带受保护字段，不该有剥离记录")
        for field, value in incoming.items():
            self.assertEqual(value, merged.get(field), f"{field} 必须合并进记录")
        names = [c["field"] for c in changed]
        for field in ingest_mod.EXPANSION_MERGE_FIELDS:
            self.assertIn(field, names, f"{field} 必须在 changed_fields 里逐条记账")
        for item in changed:
            self.assertEqual({"field", "before", "after"}, set(item), "记账形状 field/before/after")
        self.assertEqual("grp-before", before["group_id"], "入参记录本体不得被改写")
        self.assertEqual("grp-qa-s0", merged["group_id"])

    # -- QA-S0-07 -----------------------------------------------------------
    def test_qa_s0_07_field_outside_both_lists_is_rejected(self) -> None:
        before = self.base()
        merged, changed, _stripped = ingest_mod.merge_facts_full(
            before, {"not_in_any_list": "x", "brand_new_key": 1, "confidence": 0.42})
        self.assertNotIn("not_in_any_list", merged)
        self.assertNotIn("brand_new_key", merged)
        self.assertNotIn("not_in_any_list", [c["field"] for c in changed])
        self.assertEqual(0.42, merged.get("confidence"), "4.4 白名单照旧生效（叠加不削弱）")

    # -- QA-S0-08 -----------------------------------------------------------
    def test_qa_s0_08_protected_fields_stripped_and_named(self) -> None:
        before = self.base()
        forged = {
            "intervention_history": [{"target_action_id": "act-demo-0001",
                                      "decision": "approve", "actor_id": "attacker"}],
            "human_owner": cs.did_from_name("qa-s0-attacker", kind="user"),
            "delegation_chain": [cs.did_from_name("qa-s0-attacker", kind="user")],
            "signature": "ZGVhZGJlZWY=",
            "entry_hash": "0" * 64,
            "prev_hash": "1" * 64,
            "user_intervention": {"decision": "reject", "recorded": False},
            "execution_receipt": {"state": "executed"},
            "human_readable": {"l1": "伪造文案", "l2": ["a"], "l3": ""},
            "source": {"kind": "manual"},
            "demo_data": False,
            "policy": {"name": "forged", "version": "0", "rules": []},
        }
        merged, _changed, stripped = ingest_mod.merge_facts_full(before, forged)
        self.assertEqual(sorted(forged), sorted(stripped),
                         "入参里出现的受保护字段必须逐个上报（不静默吞）")
        for field in ("intervention_history", "human_owner", "signature"):
            self.assertIn(field, stripped, f"{field} 必须在剥离清单里被点名")
        for field in forged:
            self.assertEqual(before.get(field), merged.get(field),
                             f"{field} 必须保持服务端值（伪造值进不来）")
        for field in ("intervention_history", "human_owner", "delegation_chain",
                      "signature", "entry_hash", "prev_hash"):
            self.assertNotIn(field, merged, f"{field} 不是记录字段，合并后不得出现")
        self.assertEqual(before["source"], merged["source"], "来源归因保持服务端值")
        self.assertTrue(merged.get("demo_data"), "演示标记保持服务端值")

    # -- QA-S0-09 -----------------------------------------------------------
    def test_qa_s0_09_blank_values_do_not_overwrite(self) -> None:
        before = self.base()
        before["rollback_state"] = "rolled_back"
        before["evidence_feedback"] = {"like": 1, "dislike": 0}
        before["confidence_source"] = {"method": "human"}
        merged, changed, _stripped = ingest_mod.merge_facts_full(
            before, {"group_id": "", "rollback_state": None, "evidence_feedback": "",
                     "confidence_source": "   "})
        self.assertEqual("grp-before", merged.get("group_id"), "空串不得覆盖已有值")
        self.assertEqual("rolled_back", merged.get("rollback_state"), "null 不得覆盖已有值")
        self.assertEqual({"like": 1, "dislike": 0}, merged.get("evidence_feedback"))
        self.assertEqual({"method": "human"}, merged.get("confidence_source"))
        names = [c["field"] for c in changed]
        for field in ("group_id", "rollback_state", "evidence_feedback",
                      "confidence_source"):
            self.assertNotIn(field, names, f"blank 不该记账：{field}")

    # -- QA-S0-10 -----------------------------------------------------------
    def test_qa_s0_10_single_implementation_across_server_and_ingest(self) -> None:
        self.assertIs(ingest_mod.EXPANSION_MERGE_FIELDS, sv.EXPANSION_MERGE_FIELDS,
                      "server 必须 re-export ingest 的同一份名单（不是复制一份）")
        self.assertIs(ingest_mod.EXPANSION_PROTECTED_FIELDS, sv.EXPANSION_PROTECTED_FIELDS)
        self.assertEqual(9, len(ingest_mod.EXPANSION_MERGE_FIELDS))
        self.assertEqual(ingest_mod.EXPANSION_MERGE_FIELDS,
                         tuple(ta.OPTIONAL_RECORD_KEYS[len(OPTIONAL_BEFORE_S0_2B):]),
                         "适配器名单新增段必须与合并名单逐字等价（含顺序）")

        before = self.base()
        incoming = expansion_values()
        compat, compat_changed = ingest_mod.merge_facts(before, incoming)
        full, full_changed, _stripped = ingest_mod.merge_facts_full(before, incoming)
        compat.pop("received_at", None)      # 两次调用各自打时间戳，比较业务内容
        full.pop("received_at", None)
        self.assertEqual(full, compat, "merge_facts 必须与 merge_facts_full 同源")
        self.assertEqual(full_changed, compat_changed)


# --------------------------------------------------------------------------- #
# S0-2：合并在签名之前（真 HTTP 端到端）
# --------------------------------------------------------------------------- #

class S02MergeEndToEndTests(unittest.TestCase):
    """QA-S0-11：合并产物过 schema、签名有效，且合入值参与 `entry_hash`。"""

    def setUp(self) -> None:
        self.root = scratch_dir("qa-s0-4-merge-e2e")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=None, quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(),
                                self.thread.join(5)))
        self.base = self.server.url()

    def test_qa_s0_11_merged_product_is_schema_valid_and_signed_after_merge(self) -> None:
        target_id = "act-demo-0001"          # low risk：不受四眼闸门干扰
        status, _hd, before = call(self.base, f"/api/records/{target_id}")
        self.assertEqual(200, status, before)
        payload = sample(target_id)
        values = expansion_values()
        payload.update(copy.deepcopy(values))
        # 受保护字段随同一次导入送进来：合并层必须剥掉（证明走的确实是合并层，不是整条覆盖）
        payload["user_intervention"] = {"decision": "reject", "decided_by": "attacker",
                                        "decided_by_role": "user", "recorded": False,
                                        "decided_at": "2026-01-01T00:00:00Z"}
        status, _hd, data = call(self.base, sv.API_PATH_IMPORT,
                                 {"payload": [payload], "mode": "upsert"}, "POST")
        self.assertEqual(200, status, data)

        status, _hd, stored = call(self.base, f"/api/records/{target_id}")
        self.assertEqual(200, status, stored)
        for field, value in values.items():
            self.assertEqual(value, stored.get(field), f"{field} 未落进合并产物")
        self.assertEqual(before.get("user_intervention"), stored.get("user_intervention"),
                         "受保护字段必须被合并层剥离：伪造的决定不得覆盖服务端值")
        self.assertEqual([], schema_errors(stored), "合并产物必须过 schema v1.1")

        status, _hd, verified = call(self.base, "/api/secretariat/verify",
                                     {"record": stored}, "POST")
        self.assertEqual(200, status, verified)
        result = verified["results"][0]
        self.assertEqual("valid", result["checks"]["signature"], result["problems"])
        self.assertEqual("valid", result["checks"]["entry_hash"], result["problems"])
        self.assertIn(result["checks"]["prev_hash"], ("continuous", "genesis"))
        self.assertTrue(result["ok"], result["problems"])

        # 合并在签名之前：签名后偷改合入的扩展字段 → entry_hash 复算不过
        tampered = copy.deepcopy(stored)
        tampered["causal_explanation"]["model"] = "tampered-after-sign"
        status, _hd, again = call(self.base, "/api/secretariat/verify",
                                  {"record": tampered}, "POST")
        self.assertEqual(200, status, again)
        self.assertEqual("invalid", again["results"][0]["checks"]["entry_hash"],
                         "合入值必须被 entry_hash 覆盖（合并在签名之前）")
        count = len(call(self.base, sv.API_PATH_RECORDS)[2]["records"])
        self.assertEqual(len(SAMPLES), count, "upsert 撞已有 id 不得新增记录")


# --------------------------------------------------------------------------- #
# S0-2b：适配器可选字段闸门（纯函数 + 真 HTTP）
# --------------------------------------------------------------------------- #

class S02bAdapterGatePureTests(unittest.TestCase):
    """QA-S0-12 / QA-S0-13：9 字段逐个过闸门；名单只增不改。"""

    def test_qa_s0_12_nine_fields_pass_the_shape_gate_one_by_one(self) -> None:
        values = expansion_values()
        self.assertEqual(set(ingest_mod.EXPANSION_MERGE_FIELDS), set(values))
        for field in ingest_mod.EXPANSION_MERGE_FIELDS:
            rec = sample("act-demo-0001")
            rec["id"] = f"act-qa-s0-12-{field}"
            rec["parent_action_id"] = None
            rec[field] = copy.deepcopy(values[field])
            out = ta.convert_trace_to_action_record([rec])
            self.assertEqual(1, len(out), f"{field} 被适配器闸门拦掉了")
            self.assertEqual(values[field], out[0][field],
                             f"{field} 必须原样透传（原生路径既有口径）")
            self.assertEqual([], schema_errors(out[0]), f"{field}：schema 不通过")

    def test_qa_s0_13_lists_only_grew_and_required_is_untouched(self) -> None:
        self.assertEqual(REQUIRED_SNAPSHOT, ta.REQUIRED_RECORD_KEYS,
                         "27 项必填一字不动")
        self.assertEqual(len(REQUIRED_SNAPSHOT), len(ta.REQUIRED_RECORD_KEYS))
        self.assertEqual(OPTIONAL_BEFORE_S0_2B,
                         ta.OPTIONAL_RECORD_KEYS[:len(OPTIONAL_BEFORE_S0_2B)],
                         "既有 14 项必须原样在最前（只追加，不重排、不改名）")
        self.assertEqual(tuple(ingest_mod.EXPANSION_MERGE_FIELDS),
                         ta.OPTIONAL_RECORD_KEYS[len(OPTIONAL_BEFORE_S0_2B):],
                         "新增段 = 9 个 5.0 字段，逐字等价")
        self.assertEqual(23, len(ta.OPTIONAL_RECORD_KEYS), "14 + 9 = 23")
        self.assertEqual(len(set(ta.OPTIONAL_RECORD_KEYS)), len(ta.OPTIONAL_RECORD_KEYS),
                         "可选名单不得有重复键")
        self.assertEqual(set(),
                         set(ingest_mod.EXPANSION_PROTECTED_FIELDS)
                         & set(ta.OPTIONAL_RECORD_KEYS),
                         "受保护字段与可选名单必须零交集")
        for field in ingest_mod.EXPANSION_MERGE_FIELDS:
            self.assertIn(field, SCHEMA["properties"],
                          f"{field} 必须是 schema 既有属性（零新增 schema 键）")


class S02bEndToEndHttpTests(unittest.TestCase):
    """QA-S0-14 / QA-S0-15：真 HTTP 导入 → 落库；受保护字段/名单外新键仍 400。"""

    def setUp(self) -> None:
        self.root = scratch_dir("qa-s0-4-adapter-http")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=None, quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(),
                                self.thread.join(5)))
        self.base = self.server.url()

    def post_import(self, payload, mode: str = "upsert"):
        return call(self.base, sv.API_PATH_IMPORT, {"payload": payload, "mode": mode}, "POST")

    def record(self, action_id: str) -> dict:
        status, _hd, data = call(self.base, f"/api/records/{action_id}")
        self.assertEqual(200, status, data)
        return data

    def test_qa_s0_14_nine_fields_reach_the_store_over_real_http(self) -> None:
        payload = sample("act-demo-0001")
        payload["id"] = "act-qa-s0-14"
        payload["parent_action_id"] = None
        values = expansion_values()
        payload.update(copy.deepcopy(values))
        status, _hd, data = self.post_import([payload])
        self.assertEqual(200, status, data)
        stored = self.record("act-qa-s0-14")
        for field, value in values.items():
            self.assertEqual(value, stored.get(field),
                             f"{field} 必须经真 HTTP 落库（端到端通道）")
        self.assertEqual([], schema_errors(stored), "落库记录必须 schema 合法")
        status, _hd, verified = call(self.base, "/api/secretariat/verify",
                                     {"record": stored}, "POST")
        self.assertEqual(200, status, verified)
        self.assertEqual("valid", verified["results"][0]["checks"]["signature"],
                         verified["results"][0]["problems"])
        rows = call(self.base, sv.API_PATH_RECORDS)[2]["records"]
        self.assertEqual(len(SAMPLES) + 1, len(rows), "create 一条只该多一条")

    def test_qa_s0_15_protected_and_unknown_keys_still_400_over_http(self) -> None:
        protected = ("signature", "entry_hash", "prev_hash", "agent_pubkey", "approval",
                     "tsa_timestamp", "input_hash", "output_hash",
                     "intervention_history", "human_owner", "delegation_chain")
        for field in protected:
            payload = sample("act-demo-0001")
            payload["id"] = f"act-qa-s0-15-{field}"
            payload["parent_action_id"] = None
            payload[field] = "forged" if field != "approval" else {"state": "approved"}
            status, _hd, data = self.post_import([payload])
            self.assertEqual(400, status, f"{field} 必须仍被适配器闸门拒：{data}")
            self.assertEqual("ADAPTER_OUTPUT_INVALID", data.get("code"), data)
            status, _hd, _rows = call(self.base, f"/api/records/"
                                                  f"act-qa-s0-15-{field}")
            self.assertEqual(404, status, f"{field}：被拒的记录绝不能落库")

        for field in ("brand_new_key", "expansion_field_not_in_lists", "signaturex"):
            payload = sample("act-demo-0001")
            payload["id"] = f"act-qa-s0-15-{field}"
            payload["parent_action_id"] = None
            payload[field] = 1
            status, _hd, data = self.post_import([payload])
            self.assertEqual(400, status, f"{field} 名单外新键必须 400：{data}")
            self.assertEqual("ADAPTER_OUTPUT_INVALID", data.get("code"), data)


if __name__ == "__main__":  # 手工自检：python tests/test_s0_acceptance.py -v
    unittest.main(verbosity=2)
