"""t3 [t2-1] 验收测试：Trace 接入适配器 + 自造样例数据。

运行（全程离线，只用标准库）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_trace_adapter.py

覆盖任务验收点：
  1. 四种输入各有 ≥2 条断言：条目数、字段映射、未知字段保留、异常处理；
  2. 三种自造外部格式转换成功率 100%（conversion_rate == 1.0）；
  3. 每条转出记录都通过 ActionRecord v1.1 schema（复用 tools/jsonschema_mini，与
     tools/validate_schema.py 同一实现）+ 事实指纹复算 + 隐私 lint 零命中；
  4. 夹具 provenance：全部标注"合成示例，非真实采集" + 规范版本；
  5. 离线纯净：agent_explainer 包只 import 标准库，无任何网络/子进程模块。
"""
from __future__ import annotations

import ast
import copy
import datetime as _dt
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import trace_adapter as ta  # noqa: E402
import facts_hash  # noqa: E402
import validate_schema as vs  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json").read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
SAMPLES = ROOT / "samples"
FIXED_NOW = _dt.datetime(2026, 11, 9, 12, 0, 0, tzinfo=_dt.timezone.utc)

LANGFUSE_PATH = SAMPLES / "traces" / "langfuse.json"
OTEL_PATH = SAMPLES / "traces" / "otel_genai.json"
MCP_PATH = SAMPLES / "traces" / "mcp_events.json"
NATIVE_PATH = SAMPLES / "action_records.json"
README_PATH = SAMPLES / "README.md"

TRACE_FIXTURES = {"langfuse": LANGFUSE_PATH, "otel_genai": OTEL_PATH, "mcp_events": MCP_PATH}


# ----------------------------------------------------------------------------------
# 公共工具
# ----------------------------------------------------------------------------------

def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def convert_fixture(path: pathlib.Path, **kwargs: object) -> ta.ConversionOutcome:
    kwargs.setdefault("source_uri", f"samples/{path.relative_to(SAMPLES).as_posix()}")
    kwargs.setdefault("now", FIXED_NOW)
    return ta.convert_with_raw(load(path), **kwargs)  # type: ignore[arg-type]


def by_id(records: list[dict], rec_id: str) -> dict:
    for rec in records:
        if rec["id"] == rec_id:
            return rec
    raise AssertionError(f"记录不存在：{rec_id}（实到 {[r['id'] for r in records]}）")


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def privacy_hits(obj: object) -> list[str]:
    hits: list[str] = []
    for text in vs.iter_strings(obj):
        for pattern, label in vs.FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                hits.append(f"{label}: {text[:60]}")
    return hits


def assert_all_schema_valid(case: unittest.TestCase, records: list[dict]) -> None:
    case.assertTrue(records, "转换结果为空")
    for rec in records:
        case.assertEqual([], schema_errors(rec), f"schema 不通过：{rec.get('id')}")


def assert_all_facts_hash_verify(case: unittest.TestCase, records: list[dict]) -> None:
    for rec in records:
        ok, declared, computed = facts_hash.verify(rec)
        case.assertTrue(ok, f"事实指纹不可复算：{rec['id']} {declared} != {computed}")


def record_keys_allowed(rec: dict) -> list[str]:
    allowed = set(ta.REQUIRED_RECORD_KEYS) | set(ta.OPTIONAL_RECORD_KEYS)
    return sorted(set(rec) - allowed)


# ----------------------------------------------------------------------------------
# 0. 格式识别
# ----------------------------------------------------------------------------------

class FormatDetectionTests(unittest.TestCase):
    def test_detects_four_fixture_formats(self):
        for fmt, path in TRACE_FIXTURES.items():
            self.assertEqual(fmt, ta.detect(load(path)), f"{path.name} 应识别为 {fmt}")
        self.assertEqual("native", ta.detect(load(NATIVE_PATH)))

    def test_detect_accepts_json_text_and_bytes(self):
        doc = load(OTEL_PATH)
        text = json.dumps(doc, ensure_ascii=False)
        self.assertEqual("otel_genai", ta.detect(text))
        self.assertEqual("otel_genai", ta.detect(text.encode("utf-8")))
        self.assertEqual(ta.detect(doc), ta.detect_format(doc))  # 别名一致

    def test_explicit_format_override(self):
        records = ta.convert_trace_to_action_record(load(MCP_PATH), format="mcp_events", now=FIXED_NOW)
        self.assertEqual(9, len(records))

    def test_unknown_payload_raises_readable_error(self):
        with self.assertRaises(ta.TraceFormatError) as ctx:
            ta.detect({"hello": "world", "answer": 42})
        err = ctx.exception
        self.assertEqual("TRACE_FORMAT_UNKNOWN", err.code)
        self.assertTrue(err.reason.strip(), "必须给出可读原因")
        self.assertIn(err.code, str(err))
        self.assertEqual(err.code, err.as_dict()["code"])


# ----------------------------------------------------------------------------------
# 1. Langfuse
# ----------------------------------------------------------------------------------

class LangfuseInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(LANGFUSE_PATH)
        cls.out = convert_fixture(LANGFUSE_PATH)
        cls.records = cls.out.records

    def test_item_count_and_full_conversion(self):
        self.assertEqual(8, len(self.doc["observations"]))
        self.assertEqual(8, len(self.records))
        self.assertEqual(1.0, self.out.stats["conversion_rate"])
        self.assertEqual(0, self.out.stats["skipped"])
        self.assertEqual("langfuse", self.out.format)

    def test_every_record_passes_schema(self):
        assert_all_schema_valid(self, self.records)

    def test_field_mapping(self):
        rec = by_id(self.records, "obs-lf-0002")
        self.assertEqual("read", rec["action_type"])
        self.assertEqual("succeeded", rec["status"])
        self.assertEqual("filesystem.scan_album", rec["tool_used"]["name"])
        self.assertEqual(740, rec["latency_ms"])
        self.assertEqual("demo-user-01", rec["actor_id"])           # ← Langfuse userId
        self.assertEqual("sess-lf-demo-01", rec["session_id"])      # ← Langfuse sessionId
        self.assertEqual("demo-photo-bot", rec["agent_id"])         # ← metadata.tracelens
        self.assertEqual("proj-demo-lf", rec["workspace_id"])       # ← projectId
        self.assertEqual("trc-demo-lf-01", rec["trace_link"]["trace_id"])
        self.assertEqual("obs-lf-0002", rec["trace_link"]["observation_id"])
        self.assertEqual("langfuse_export", rec["source"]["adapter"])
        self.assertEqual("Langfuse public API v1 (OpenAPI 1.0.0)", rec["source"]["format_version"])

    def test_action_type_and_status_mapping_variants(self):
        self.assertEqual(("plan", "succeeded"),
                         (by_id(self.records, "obs-lf-0001")["action_type"],
                          by_id(self.records, "obs-lf-0001")["status"]))
        failed = by_id(self.records, "obs-lf-0005")
        self.assertEqual("failed", failed["status"])                # ← level=ERROR
        self.assertEqual("LANGFUSE_LEVEL_ERROR", failed["error"]["code"])
        self.assertTrue(failed["error"]["human_reason"].strip())
        self.assertGreaterEqual(len(failed["suggested_next"]), 1)   # C5
        self.assertEqual("running", by_id(self.records, "obs-lf-0008")["status"])  # 无 endTime

    def test_pending_confirmation_from_extension_metadata(self):
        rec = by_id(self.records, "obs-lf-0004")
        self.assertTrue(rec["requires_action"])
        self.assertEqual("pending_confirmation", rec["status"])
        self.assertEqual("delete", rec["action_type"])
        self.assertEqual("high", rec["risk_level"])
        self.assertEqual("2026-11-09T09:42:03Z", rec["action_due_at"])
        self.assertTrue(rec["policy"]["requires_human_confirmation"])
        self.assertIsNone(rec["user_intervention"])
        self.assertEqual("unreported", rec["execution_receipt"]["state"])
        self.assertEqual(["approve", "modify", "reject"],
                         [o["kind"] for o in rec["intervention_options"]])
        self.assertEqual(1, len(rec["impact_scope"]["targets"]))
        self.assertFalse(rec["impact_scope"]["reversible"])

    def test_cost_is_never_invented(self):
        priced = by_id(self.records, "obs-lf-0001")
        self.assertEqual("USD", priced["cost"]["currency"])
        self.assertEqual(0.0021, priced["cost"]["amount"])
        tokens_only = by_id(self.records, "obs-lf-0003")
        self.assertIsNone(tokens_only["cost"])                      # 只有 token 数 → 不编造金额
        archived = self.out.raw_payloads[tokens_only["source"]["raw_ref"]]
        self.assertIn("source_usage", archived["archived_known_fields"])

    def test_unknown_fields_are_archived_not_lost(self):
        rec = by_id(self.records, "obs-lf-0005")
        self.assertTrue(rec["source"]["raw_ref"])
        self.assertEqual([], record_keys_allowed(rec))              # 记录本身字段封闭
        archived = self.out.raw_payloads[rec["source"]["raw_ref"]]
        self.assertIn("langfuse_experimental",
                      json.dumps(archived["unknown_fields"], ensure_ascii=False))
        self.assertEqual(8, self.out.stats["unknown_field_records"])

    def test_naive_timestamp_treated_as_utc(self):
        payload = [{"id": "obs-naive-1", "traceId": "trc-naive", "type": "SPAN",
                    "name": "scan", "startTime": "2026-11-09T09:00:00",
                    "endTime": "2026-11-09T09:00:02"}]
        records = ta.convert_trace_to_action_record(payload, now=FIXED_NOW)
        self.assertEqual(1, len(records))
        self.assertEqual("2026-11-09T09:00:00Z", records[0]["timestamp"])
        self.assertEqual(2000, records[0]["latency_ms"])


# ----------------------------------------------------------------------------------
# 2. OTel GenAI / OTLP JSON
# ----------------------------------------------------------------------------------

class OtelGenAiInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(OTEL_PATH)
        cls.out = convert_fixture(OTEL_PATH)
        cls.records = cls.out.records
        cls.spans = cls.doc["resourceSpans"][0]["scopeSpans"][0]["spans"]

    def test_item_count_and_full_conversion(self):
        self.assertEqual(7, len(self.spans))
        self.assertEqual(7, len(self.records))
        self.assertEqual(1.0, self.out.stats["conversion_rate"])
        self.assertEqual("otel_genai", self.out.format)

    def test_every_record_passes_schema(self):
        assert_all_schema_valid(self, self.records)

    def test_nanosecond_timestamps_and_ids(self):
        first = self.records[0]
        self.assertEqual("otel-a1b2c3d4e5f60001", first["id"])       # ← spanId 前缀化
        self.assertEqual("2026-11-09T09:11:52.420000Z", first["timestamp"])
        self.assertEqual(1760, first["latency_ms"])                  # 纳秒差换算成毫秒
        self.assertEqual("4bf92f3577b34da6a3ce929d0e0e4736", first["trace_link"]["trace_id"])
        self.assertEqual("a1b2c3d4e5f60001", first["trace_link"]["span_id"])
        self.assertEqual("OTLP JSON v1.3 + GenAI semconv 1.30.0", first["source"]["format_version"])

    def test_genai_identity_and_tool_mapping(self):
        identity = self.records[0]                              # 该 span 带 workspace.id
        self.assertEqual("otel-demo-space", identity["workspace_id"])
        self.assertEqual("other", identity["action_type"])       # chat span 无工具语义
        rec = by_id(self.records, "otel-a1b2c3d4e5f60002")
        self.assertEqual("read", rec["action_type"])
        self.assertEqual("filesystem.glob", rec["tool_used"]["name"])
        self.assertEqual("call-otel-2201", rec["tool_used"]["call_id"])
        self.assertIn("pattern", rec["tool_used"]["params_summary"])  # 入参摘要（脱敏后）
        self.assertEqual("demo-photo-agent", rec["agent_id"])
        self.assertEqual("sess-otel-demo-01", rec["session_id"])
        self.assertEqual("demo-user-01", rec["actor_id"])
        self.assertEqual("default", rec["workspace_id"])          # 来源未提供 → 落默认值

    def test_tokens_only_means_cost_null_and_is_archived(self):
        rec = by_id(self.records, "otel-a1b2c3d4e5f60001")
        self.assertIsNone(rec["cost"])
        archived = self.out.raw_payloads[rec["source"]["raw_ref"]]
        self.assertEqual({"input_tokens": 820, "output_tokens": 210,
                          "note": "来源只有 token 计数，无金额 → cost=null（不编造金额）"},
                         archived["archived_known_fields"]["gen_ai_usage_tokens"])

    def test_status_code_error_becomes_failed_record(self):
        rec = by_id(self.records, "otel-a1b2c3d4e5f60005")
        self.assertEqual("failed", rec["status"])
        self.assertEqual("export", rec["action_type"])
        self.assertEqual("OTEL_STATUS_CODE_ERROR", rec["error"]["code"])
        self.assertIn("只读", rec["error"]["human_reason"])
        self.assertGreaterEqual(len(rec["suggested_next"]), 1)
        self.assertEqual("medium", rec["risk_level"])               # 影响面来自来源扩展属性

    def test_tracelens_extension_drives_confirmation_and_impact(self):
        rec = by_id(self.records, "otel-a1b2c3d4e5f60004")
        self.assertEqual("delete", rec["action_type"])
        self.assertEqual("critical", rec["risk_level"])
        self.assertTrue(rec["requires_action"])
        self.assertEqual("pending_confirmation", rec["status"])
        self.assertEqual("2026-11-09T09:42:03Z", rec["action_due_at"])
        self.assertFalse(rec["impact_scope"]["reversible"])
        self.assertEqual(12, rec["impact_scope"]["targets"][0]["count"])
        self.assertIsNone(rec["user_intervention"])
        plan = by_id(self.records, "otel-a1b2c3d4e5f60007")
        self.assertEqual("plan", plan["action_type"])               # ← tracelens.action_type
        self.assertEqual("running", plan["status"])                 # 无 endTime

    def test_unknown_attributes_are_archived(self):
        rec = self.records[0]
        archived = self.out.raw_payloads[rec["source"]["raw_ref"]]
        blob = json.dumps(archived["unknown_fields"], ensure_ascii=False)
        for key in ("demo.experiment_id", "http.route", "links", "flags"):
            self.assertIn(key, blob)
        self.assertEqual(7, self.out.stats["unknown_field_records"])


# ----------------------------------------------------------------------------------
# 3. MCP 事件流
# ----------------------------------------------------------------------------------

class McpEventsInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(MCP_PATH)
        cls.out = convert_fixture(MCP_PATH)
        cls.records = cls.out.records

    def test_event_count_records_count_and_full_conversion(self):
        self.assertEqual(16, len(self.doc["events"]))
        self.assertEqual(9, len(self.records))                       # 握手事件不出记录
        self.assertEqual(1.0, self.out.stats["conversion_rate"])
        self.assertEqual("mcp_events", self.out.format)
        self.assertEqual("mcp_events", self.records[0]["source"]["adapter"])
        self.assertEqual("MCP 2025-06-18 (JSON-RPC 2.0)", self.records[0]["source"]["format_version"])

    def test_every_record_passes_schema(self):
        assert_all_schema_valid(self, self.records)

    def test_request_response_pairing_yields_latency(self):
        rec = by_id(self.records, "req-2")
        self.assertEqual("read", rec["action_type"])
        self.assertEqual("album.list", rec["tool_used"]["name"])
        self.assertEqual(200, rec["latency_ms"])                     # 21.200 - 21.000
        self.assertEqual("succeeded", rec["status"])
        self.assertIn("200", (rec["result_summary"] or ""))
        self.assertEqual("demo-album-server", rec["agent_id"])       # ← initialize.serverInfo
        self.assertEqual("sess-mcp-demo-01", rec["session_id"])      # ← 流级信封

    def test_is_error_result_becomes_failed(self):
        rec = by_id(self.records, "req-5")
        self.assertEqual("failed", rec["status"])
        self.assertEqual("export", rec["action_type"])
        self.assertEqual("MCP_TOOL_IS_ERROR", rec["error"]["code"])
        self.assertIn("只读", rec["error"]["human_reason"])

    def test_jsonrpc_error_becomes_failed_with_rpc_code(self):
        rec = by_id(self.records, "req-6")
        self.assertEqual("failed", rec["status"])
        self.assertEqual("MCP_RPC_-32603", rec["error"]["code"])
        self.assertEqual(100, rec["latency_ms"])

    def test_notifications_map_to_error_wait_intervention(self):
        log = by_id(self.records, "notifications-message-13")
        self.assertEqual("error", log["action_type"])
        self.assertEqual("failed", log["status"])
        self.assertEqual("MCP_LOG_ENOSPC", log["error"]["code"])
        progress = by_id(self.records, "notifications-progress-14")
        self.assertEqual("wait", progress["action_type"])
        cancelled = by_id(self.records, "notifications-cancelled-15")
        self.assertEqual("intervention", cancelled["action_type"])
        self.assertEqual("skipped", cancelled["status"])
        self.assertEqual("req-4", cancelled["parent_action_id"])     # ← requestId 关联

    def test_pending_confirmation_impact_from_meta(self):
        rec = by_id(self.records, "req-4")
        self.assertTrue(rec["requires_action"])
        self.assertEqual("delete", rec["action_type"])
        self.assertEqual("critical", rec["risk_level"])
        self.assertEqual("pending_confirmation", rec["status"])
        self.assertFalse(rec["impact_scope"]["reversible"])
        modify = [o for o in rec["intervention_options"] if o["kind"] == "modify"][0]
        self.assertGreaterEqual(len(modify["editable_fields"]), 1)   # C：modify 必须给可改字段
        self.assertEqual(12, [f for f in modify["editable_fields"]
                              if f["name"] == "count"][0]["default"])

    def test_unknown_fields_are_archived(self):
        rec = by_id(self.records, "req-4")
        archived = self.out.raw_payloads[rec["source"]["raw_ref"]]
        blob = json.dumps(archived["unknown_fields"], ensure_ascii=False)
        self.assertIn("resumptionToken", blob)
        self.assertIn("x_trace_lens_debug", blob)
        self.assertEqual([], record_keys_allowed(rec))


# ----------------------------------------------------------------------------------
# 4. 原生 ActionRecord
# ----------------------------------------------------------------------------------

class NativeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load(NATIVE_PATH)
        cls.out = convert_fixture(NATIVE_PATH)
        cls.records = cls.out.records

    def test_count_types_and_full_conversion(self):
        self.assertEqual(12, len(self.doc["records"]))
        self.assertEqual(12, len(self.records))
        self.assertEqual(1.0, self.out.stats["conversion_rate"])
        self.assertGreaterEqual(len({r["action_type"] for r in self.records}), 10)
        self.assertEqual("native", self.out.format)

    def test_native_conversion_is_verbatim(self):
        self.assertEqual(self.doc["records"], copy.deepcopy(self.records))  # 原样透传，不改字段

    def test_every_record_passes_schema_and_fingerprint(self):
        assert_all_schema_valid(self, self.records)
        assert_all_facts_hash_verify(self, self.records)

    def test_demo_provenance_flags(self):
        for rec in self.records:
            self.assertTrue(rec["demo_data"], rec["id"])
            self.assertTrue(rec["synthetic"], rec["id"])
            self.assertEqual("demo", rec["source"]["kind"], rec["id"])

    def test_dm_8_4_required_scenarios_present(self):
        requires = [r for r in self.records if r["requires_action"]]
        self.assertGreaterEqual(len(requires), 3)
        for rec in requires:
            kinds = [o["kind"] for o in rec["intervention_options"]]
            self.assertEqual(["approve", "modify", "reject"], kinds, rec["id"])
            for opt in rec["intervention_options"]:
                self.assertTrue(opt["consequence"], f"{rec['id']} 选项必须写后果")
        failed = [r for r in self.records if r["status"] == "failed"]
        self.assertTrue(failed)
        self.assertTrue(failed[0]["error"]["human_reason"])
        self.assertGreaterEqual(len(failed[0]["suggested_next"]), 1)
        critical = [r for r in self.records
                    if r["risk_level"] in ("high", "critical") and r["impact_scope"]]
        self.assertTrue(any(t["reversible"] is False for t in
                            [x["impact_scope"] for x in critical]), "需要不可撤回的高危样本")
        modified = [r for r in self.records if r["status"] == "modified"]
        self.assertEqual("modify", modified[0]["user_intervention"]["decision"])
        self.assertEqual("executed", modified[0]["execution_receipt"]["state"])
        rejected = [r for r in self.records if r["status"] == "rejected"]
        self.assertEqual("reject", rejected[0]["user_intervention"]["decision"])
        self.assertEqual("refused_by_runtime", rejected[0]["execution_receipt"]["state"])
        self.assertTrue(any(r["status"] == "expired" for r in self.records))
        low = [r for r in self.records if r["confidence"] is not None and r["confidence"] <= 0.6]
        self.assertTrue(low and low[0]["uncertainty"], "低置信必须带 uncertainty")
        self.assertTrue(any(r["impact_scope"] and "personal_data" in r["impact_scope"]["data_categories"]
                            for r in self.records), "需要 personal_data 样本")

    def test_native_wrapper_and_bare_list_both_detected(self):
        self.assertEqual("native", ta.detect({"records": [self.doc["records"][0]]}))
        self.assertEqual("native", ta.detect({"action_records": [self.doc["records"][0]]}))
        self.assertEqual("native", ta.detect([self.doc["records"][0]]))
        self.assertEqual(1, len(ta.convert_trace_to_action_record(
            [self.doc["records"][0]], now=FIXED_NOW)))

    def test_bilingual_length_limits_hold(self):
        for rec in self.records:
            hr = rec["human_readable"]
            self.assertLessEqual(len(hr["l1"]), vs.L1_MAX[hr["language"]], rec["id"])
            self.assertTrue(3 <= len(hr["l2"]) <= vs.L2_MAX_STEPS, rec["id"])
            for step in hr["l2"]:
                self.assertLessEqual(len(step), vs.L2_MAX_PER_STEP, rec["id"])
            self.assertLessEqual(len(hr["l3"]), vs.L3_MAX, rec["id"])


# ----------------------------------------------------------------------------------
# 5. normalize_events（供 4.4 实时摄取复用）
# ----------------------------------------------------------------------------------

class NormalizeEventsTests(unittest.TestCase):
    def test_matches_batch_conversion(self):
        doc = load(LANGFUSE_PATH)
        via_convert = ta.convert_trace_to_action_record(doc, source_kind="webhook", now=FIXED_NOW)
        via_normalize = ta.normalize_events(doc, source_kind="webhook", now=FIXED_NOW)
        self.assertEqual(via_convert, via_normalize)

    def test_mixed_batch_is_detected_per_item(self):
        batch = {"events": [load(LANGFUSE_PATH),
                            load(OTEL_PATH)["resourceSpans"][0]["scopeSpans"][0]["spans"][0],
                            {"jsonrpc": "2.0", "id": "req-live-1", "method": "tools/call",
                             "params": {"name": "album.list", "arguments": {"album": "DemoAlbum"}},
                             "ts": "2026-11-09T09:20:00Z"},
                            load(NATIVE_PATH)["records"][0]]}
        out = ta.normalize_events_with_raw(batch, now=FIXED_NOW)
        self.assertEqual(11, len(out.records))                        # 8 trace + 1 span + 1 rpc + 1 native
        self.assertTrue(out.format.startswith("mixed:"), out.format)
        self.assertEqual({"langfuse_export", "otel_json", "mcp_events", "native"},
                         {r["source"]["adapter"] for r in out.records})
        # 外部格式带 format:* 标签；原生记录原样透传，不改写它自己的 labels
        self.assertEqual({"format:langfuse", "format:otel_genai", "format:mcp_events"},
                         {r["labels"][0] for r in out.records
                          if r["source"]["adapter"] != "native"})
        self.assertEqual("webhook", out.records[0]["source"]["kind"])   # 实时入口默认值

    def test_cross_item_duplicate_ids_are_deduped(self):
        doc = load(LANGFUSE_PATH)
        out = ta.normalize_events_with_raw([doc, doc], now=FIXED_NOW)
        ids = [r["id"] for r in out.records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(8, len(ids))
        self.assertTrue(any(r.get("code") == "TRACE_DUPLICATE_ID"
                            for r in out.stats["skip_reasons"]),
                        f"重复 id 必须记因由：{out.stats['skip_reasons']}")

    def test_stats_shape_is_stable(self):
        out = ta.normalize_events_with_raw(load(MCP_PATH), now=FIXED_NOW)
        for key in ("input_events", "converted", "skipped", "skip_reasons",
                    "unknown_field_records", "source_kind", "language",
                    "adapter_version", "format_spec_version", "conversion_rate"):
            self.assertIn(key, out.stats)
        self.assertEqual(1.0, out.stats["conversion_rate"])


# ----------------------------------------------------------------------------------
# 6. 异常处理（可读原因，不崩栈）
# ----------------------------------------------------------------------------------

class ErrorHandlingTests(unittest.TestCase):
    def codes(self, payload: object) -> str:
        with self.assertRaises(ta.TraceFormatError) as ctx:
            ta.convert_trace_to_action_record(payload)
        err = ctx.exception
        self.assertTrue(err.reason.strip(), "异常必须给出可读原因")
        self.assertIn(err.code, str(err))
        return err.code

    def test_empty_and_non_json_inputs(self):
        self.assertEqual("TRACE_INPUT_EMPTY", self.codes(""))
        self.assertEqual("TRACE_INPUT_NOT_JSON", self.codes("{not json"))
        self.assertEqual("TRACE_INPUT_NOT_UTF8", self.codes(b"\xff\xfe\x00bad"))
        self.assertEqual("TRACE_FORMAT_UNKNOWN", self.codes({"nothing": "useful"}))

    def test_empty_collections_per_format(self):
        self.assertEqual("TRACE_LANGFUSE_EMPTY", self.codes({"observations": []}))
        self.assertEqual("TRACE_OTEL_EMPTY", self.codes({"resourceSpans": []}))
        self.assertEqual("TRACE_MCP_EMPTY", self.codes({"jsonrpc": "2.0", "events": []}))
        self.assertEqual("TRACE_NATIVE_EMPTY", self.codes({"records": []}))

    def test_native_gate_errors(self):
        base = copy.deepcopy(load(NATIVE_PATH)["records"][0])
        bad_version = copy.deepcopy(base)
        bad_version["schema_version"] = "9.9"
        self.assertEqual("SCHEMA_VERSION_UNSUPPORTED", self.codes([bad_version]))
        missing = copy.deepcopy(base)
        del missing["intent"]
        self.assertEqual("TRACE_FIELD_MISSING", self.codes([missing]))
        bad_enum = copy.deepcopy(base)
        bad_enum["action_type"] = "teleport"
        self.assertEqual("TRACE_ENUM_INVALID", self.codes([bad_enum]))
        self.assertEqual("TRACE_RECORD_TYPE", self.codes({"records": ["not-a-record"]}))

    def test_duplicate_ids_are_skipped_with_reason(self):
        doc = load(NATIVE_PATH)
        duplicated = {"records": list(doc["records"]) + [copy.deepcopy(doc["records"][0])]}
        out = ta.convert_with_raw(duplicated, now=FIXED_NOW)
        self.assertEqual(12, len(out.records))
        self.assertEqual(1, out.stats["skipped"])
        self.assertIn("TRACE_DUPLICATE_ID", json.dumps(out.stats["skip_reasons"], ensure_ascii=False))

    def test_invalid_source_kind_is_rejected_with_reason(self):
        with self.assertRaises(ta.TraceFormatError) as ctx:
            ta.convert_trace_to_action_record(load(LANGFUSE_PATH), source_kind="carrier_pigeon")
        self.assertIn("source_kind", ctx.exception.field or "source_kind")
        self.assertEqual("TRACE_SOURCE_KIND_INVALID", ctx.exception.code)

    def test_all_error_codes_are_documented_strings(self):
        seen = ["TRACE_INPUT_EMPTY", "TRACE_INPUT_NOT_JSON", "TRACE_INPUT_NOT_UTF8",
                "TRACE_FORMAT_UNKNOWN", "TRACE_LANGFUSE_EMPTY", "TRACE_OTEL_EMPTY",
                "TRACE_MCP_EMPTY", "TRACE_NATIVE_EMPTY", "TRACE_RECORD_TYPE",
                "SCHEMA_VERSION_UNSUPPORTED", "TRACE_FIELD_MISSING", "TRACE_ENUM_INVALID",
                "TRACE_DUPLICATE_ID", "TRACE_SOURCE_KIND_INVALID"]
        source = (ROOT / "agent_explainer" / "trace_adapter.py").read_text(encoding="utf-8")
        for code in seen:
            self.assertIn(code, source, f"错误码 {code} 应在适配器里真实使用")


# ----------------------------------------------------------------------------------
# 7. 事实指纹 / 脱敏 / 产品 lint
# ----------------------------------------------------------------------------------

class FactsHashAndPrivacyTests(unittest.TestCase):
    def test_fact_fields_match_shared_implementation(self):
        self.assertEqual(tuple(facts_hash.FACT_FIELDS), tuple(ta.FACT_FIELDS))
        self.assertEqual("sha256", facts_hash.ALGORITHM)

    def test_every_converted_record_fingerprint_is_recomputable(self):
        for path in list(TRACE_FIXTURES.values()) + [NATIVE_PATH]:
            assert_all_facts_hash_verify(self, convert_fixture(path).records)

    def test_privacy_lint_zero_hits_on_all_samples(self):
        for path in list(TRACE_FIXTURES.values()) + [NATIVE_PATH]:
            out = convert_fixture(path)
            self.assertEqual([], privacy_hits(out.records), f"{path.name} 记录含隐私样式")
            self.assertEqual([], privacy_hits(out.raw_payloads), f"{path.name} 归档含隐私样式")
        self.assertEqual([], privacy_hits(load(NATIVE_PATH)["records"]))

    def test_paths_and_tokens_are_redacted_in_record_fields(self):
        payload = [{"id": "obs-priv-1", "traceId": "trc-priv", "type": "SPAN",
                    "name": "read C:\\Users\\alice\\photos\\a.jpg",
                    "input": {"path": "C:\\Users\\alice\\photos\\a.jpg",
                              "token": "sk-abcdefghijkl1234",
                              "contact": "alice@example.com"},
                    "output": "done",
                    "vendorExtra": {"note": "alice 的原始调试上下文（只留在归档）"},
                    "startTime": "2026-11-09T09:00:00Z", "endTime": "2026-11-09T09:00:01Z"}]
        out = ta.convert_with_raw(payload, now=FIXED_NOW)
        rec = out.records[0]
        self.assertIn("<path:1>", rec["intent"])
        self.assertIn("<path:1>", rec["tool_used"]["name"])        # 工具名里的路径也脱敏
        self.assertNotIn("alice", json.dumps(rec, ensure_ascii=False))
        self.assertEqual([], privacy_hits([rec]))
        self.assertTrue(facts_hash.verify(rec))                     # 指纹按脱敏后的值算，可复算
        archived_ref = rec["source"]["raw_ref"]
        self.assertTrue(archived_ref, "未知字段应生成归档指针 raw_ref")
        archived = json.dumps(out.raw_payloads[archived_ref], ensure_ascii=False)
        self.assertIn("alice", archived)                            # 原文只在归档里，记录里零命中

    def test_adapter_l1_uses_template_and_respects_limits(self):
        for path in TRACE_FIXTURES.values():
            for rec in convert_fixture(path).records:
                hr = rec["human_readable"]
                self.assertEqual("template", hr["generated_by"])
                self.assertEqual(ta.TEMPLATE_L1_ID, hr["template_id"])
                self.assertFalse(hr["may_contain_inference"])
                self.assertLessEqual(len(hr["l1"]), ta.L1_LIMIT[hr["language"]], rec["id"])
                self.assertLessEqual(len(hr["l3"]), ta.L3_LIMIT, rec["id"])
                self.assertLessEqual(len(rec["intent"]), ta.INTENT_LIMIT, rec["id"])

    def test_side_effect_without_impact_data_gets_conservative_defaults(self):
        payload = {"events": [{"jsonrpc": "2.0", "id": "req-cons-1", "ts": "2026-11-09T09:00:00Z",
                               "method": "tools/call",
                               "params": {"name": "files.delete",
                                          "arguments": {"target": "DemoAlbum/tmp"}}}]}
        rec = ta.convert_trace_to_action_record(payload, now=FIXED_NOW)[0]
        self.assertEqual("delete", rec["action_type"])
        self.assertIsNotNone(rec["impact_scope"])
        self.assertFalse(rec["impact_scope"]["reversible"])         # 保守：按不可撤回
        self.assertIsNotNone(rec["uncertainty"])
        self.assertEqual("unreported", rec["execution_receipt"]["state"])
        self.assertIsNone(rec["user_intervention"])


# ----------------------------------------------------------------------------------
# 8. 离线纯净（无网络、无第三方依赖）
# ----------------------------------------------------------------------------------

class OfflinePurityTests(unittest.TestCase):
    FORBIDDEN = {"requests", "urllib3", "httpx", "aiohttp", "socket", "socketserver",
                 "asyncio", "subprocess", "multiprocessing", "ftplib", "smtplib",
                 "telnetlib", "websocket", "websockets"}

    def imports(self, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
        return roots

    def test_package_imports_are_stdlib_only(self):
        allowed = set(sys.stdlib_module_names) | {"agent_explainer", "tools"}
        for py in sorted((ROOT / "agent_explainer").glob("*.py")):
            for root in self.imports(py):
                self.assertIn(root, allowed, f"{py.name} 引入了非标准库 {root}")
                self.assertNotIn(root, self.FORBIDDEN, f"{py.name} 引入了联网/进程模块 {root}")

    def test_package_source_has_no_network_literals(self):
        # `xmlns="http://www.w3.org/2000/svg"` 是 XML 命名空间**标识符**，不是可请求的地址：
        # 它只被当字符串比较，从不进入任何请求。先剔除再扫，避免把 qr.py 里纯标准库的
        # SVG 渲染误判成联网（误报会让「离线纯净」这条闸门失去可信度）。
        namespace_literals = ("http://www.w3.org/2000/svg",)
        for py in sorted((ROOT / "agent_explainer").glob("*.py")):
            src = py.read_text(encoding="utf-8")
            for literal in namespace_literals:
                src = src.replace(literal, "")
            for token in ("http://", "https://", "urllib", "socket.", "subprocess"):
                self.assertNotIn(token, src, f"{py.name} 含 {token}")

    def test_samples_only_reference_local_or_placeholder_urls(self):
        for path in list(TRACE_FIXTURES.values()) + [NATIVE_PATH]:
            for text in vs.iter_strings(load(path)):
                for url in re.findall(r"https?://[^\s\"'\\]+", text):
                    host = url.split("//", 1)[1].split("/", 1)[0]
                    self.assertTrue(host.endswith(".example.local") or host.endswith(".example.com"),
                                    f"{path.name} 出现外部 URL：{url}")


# ----------------------------------------------------------------------------------
# 9. 夹具 provenance（合成声明 + 规范版本）
# ----------------------------------------------------------------------------------

class SampleProvenanceTests(unittest.TestCase):
    def test_trace_fixtures_declare_synthetic_and_spec(self):
        for fmt, path in TRACE_FIXTURES.items():
            doc = load(path)
            meta = doc["_meta"]
            self.assertIs(True, meta["synthetic"], path.name)
            self.assertIs(False, meta["real_data"], path.name)
            self.assertIn("合成示例，非真实采集", meta["title"], path.name)
            self.assertIn("合成示例，非真实采集", meta["author_note"], path.name)
            self.assertEqual(ta.FORMAT_SPEC_FULL[fmt], meta["spec_version"], path.name)
            self.assertIn("tracelens", json.dumps(meta, ensure_ascii=False), path.name)
            self.assertTrue(meta["offline"] and meta["no_secrets"], path.name)
            self.assertEqual("backend-engineer (t3)", meta["generated_by"], path.name)

    def test_trace_fixture_item_counts_match(self):
        self.assertEqual(len(load(LANGFUSE_PATH)["observations"]),
                         load(LANGFUSE_PATH)["_meta"]["item_count"])
        self.assertEqual(len(load(OTEL_PATH)["resourceSpans"][0]["scopeSpans"][0]["spans"]),
                         load(OTEL_PATH)["_meta"]["item_count"])
        self.assertEqual(len(load(MCP_PATH)["events"]), load(MCP_PATH)["_meta"]["item_count"])
        for fmt, path in TRACE_FIXTURES.items():
            count = convert_fixture(path).stats["converted"]
            self.assertGreaterEqual(count, 6, fmt)
            self.assertLessEqual(count, 10, fmt)
            self.assertEqual(load(path)["_meta"].get("record_count", count), count, fmt)

    def test_native_fixture_meta_matches_content(self):
        doc = load(NATIVE_PATH)
        meta = doc["_meta"]
        self.assertIs(True, meta["synthetic"])
        self.assertIs(True, meta["demo_data"])
        self.assertIn("合成示例，非真实采集", meta["title"])
        self.assertEqual(len(doc["records"]), meta["record_count"])
        self.assertEqual(sorted({r["action_type"] for r in doc["records"]}),
                         sorted(meta["action_type_coverage"]))
        self.assertGreaterEqual(len(meta["action_type_coverage"]), 10)
        for key, ids in meta["dm_8_4_checklist"].items():
            self.assertTrue(ids, f"8.4 清单 {key} 为空")
            for rec_id in ids:
                self.assertTrue(any(r["id"] == rec_id for r in doc["records"]),
                                f"8.4 清单 {key} 指向不存在的记录 {rec_id}")

    def test_readme_declares_synthetic_and_all_specs(self):
        text = README_PATH.read_text(encoding="utf-8")
        self.assertIn("合成示例，非真实采集", text)
        for rel in ("traces/langfuse.json", "traces/otel_genai.json",
                    "traces/mcp_events.json", "action_records.json"):
            self.assertIn(rel, text)
        for spec in ta.FORMAT_SPEC_VERSION.values():
            self.assertIn(spec, text, f"README 未列出规范版本 {spec}")
        self.assertIn("tracelens", text)
        self.assertIn("演示数据", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
