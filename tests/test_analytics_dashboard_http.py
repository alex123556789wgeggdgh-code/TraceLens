"""R6-4 前置（REQ-006 收口，L1 已批：PM-VERDICT-R6-3 §3.1 遗留 1）验收测试。

钉死的是 ``GET /api/analytics/dashboard``（T26）这个面的两件事：

1. **400 翻译补齐**：分析域 ``AnalyticsError`` 在路由层翻成**统一错误体**
   ``{code, message, detail, request_id}``（与 ``/api/analytics/export`` 侧逐字同款，
   ``ANALYTICS_RANGE_INVALID`` 走 ``ERROR_TABLE`` 既有行）。此前"手滑的日期"漏成
   500 堆栈是既有事实（REPORT-R6-3 遗留 1、PM-VERDICT-R6-3 §3 表第 1 行裁"并入
   R6-4"）——前端要吃这个数据面，缺这条就是把 500 摊给队长看。
2. **T26 既有成功形状与读数一字不变**：``{ok, analytics}`` 的键集与每个数字都等于
   ``analytics.dashboard(records, agg=FeaturePaths.agg_ledger)`` 的本地复算，
   本轮只加"坏参数怎么出门"，不加也不改任何读数（PM-VERDICT-R6-3 §6 的
   "dashboard 面 4 次明文"口径维持：界面脱敏与否属产品裁决，不在本轮）。

前端消费的**元数据三件套**（``pre_aggregated`` / ``scanned`` / ``agg.reason``）在这里
一起钉住：仪表盘的「未预聚合，加载较慢」标注吃的就是这三件，缺一件 R6-4 的面就只能
装快。

运行（全程离线，HTTP 只走 127.0.0.1 环回）：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest tests.test_analytics_dashboard_http -v

临时目录一律 ``tests/scratch.py``；HTTP 断言级不重试（``http_fixture``）。
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, str(_p))

from agent_explainer import analytics as an  # noqa: E402
from agent_explainer import secretariat as sec  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
import http_fixture as fx  # noqa: E402
from scratch import scratch_dir  # noqa: E402

CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))

#: T26 冻结的成功形状（docs/07 §5.1）：R6-1 只加尾部三件元数据，R6-4 一件也不加。
T26_FROZEN_KEYS = {
    "range", "granularity", "total_tasks", "total_interventions", "intervention_rate",
    "avg_approval_seconds", "avg_approval_ms", "trust_health", "users", "trend",
    "heatmap", "top_parameters", "top_rejected", "approved", "rejected", "generated_at",
}
#: R6-1 追加的元数据三件套（「未预聚合，加载较慢」标注的数据来源）
R61_META_KEYS = {"pre_aggregated", "scanned", "agg"}
#: 统一错误体的四个键（docs/03 §统一错误体）
UNIFIED_BODY_KEYS = {"code", "message", "detail", "request_id"}


def rec(i: int, *, day: int = 12, decision: str | None = None,
        tool: str = "fs.write", fields: tuple[str, ...] = (),
        decided_minutes: int | None = 60, agent_id: str = "agent-a",
        user: str = "local-user") -> dict:
    """仪表盘形状的 ActionRecord（与 ``tests/test_analytics_export.py`` 同一夹具口径）。"""
    out: dict = {"id": f"act-r64-{i:04d}", "agent_id": agent_id,
                 "timestamp": f"2026-11-{day:02d}T09:00:00Z",
                 "tool_used": {"name": tool}}
    if decision:
        out["user_intervention"] = {
            "decision": decision, "decided_by": user,
            "decided_at": (f"2026-11-{day:02d}T{9 + decided_minutes:02d}:00:00Z"
                           if decided_minutes is not None else None),
            "modified_fields": [{"name": name} for name in fields]}
    return out


def rich_records() -> list[dict]:
    """多样夹具：3 工具 × 4 日 × 决定/无决定 × 多参数 × 多用户（与 agg/export 套件同族）。"""
    tools = ("fs.write", "calendar.read", "shell.exec")
    decisions = (None, "approve", "modify", "reject")
    users = ("local-user", "team-alice", "team-bob")
    rows: list[dict] = []
    i = 0
    for day in (10, 11, 12, 13):
        for ti, tool in enumerate(tools):
            for di, dec in enumerate(decisions):
                i += 1
                rows.append(rec(i, day=day, decision=dec, tool=tool,
                                fields=("path", "limit") if dec == "modify"
                                else ("max_delete_count",) if dec == "reject" else (),
                                decided_minutes=30 * (di + 1) if dec else None,
                                user=users[(ti + di) % len(users)]))
    return rows


class DashboardHttpTests(unittest.TestCase):
    """产品口径服务器上的 T26 面：400 翻译 + 成功形状零改动 + 元数据三件套。"""

    def records(self) -> list[dict]:
        return rich_records()

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"r64-dash-http-{case}")
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=copy.deepcopy(self.records()), quiet=True,
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

    def raw_get(self, path: str) -> tuple[int, dict, bytes]:
        def once(conn):
            conn.request("GET", path, headers={"Connection": "close"})
            response = conn.getresponse()
            return response.status, {k: v for k, v in response.getheaders()}, response.read()

        return fx.with_connection(self.host, self.port, once, verb="GET", path=path)

    def dashboard(self, query: str = "") -> tuple[int, dict, dict]:
        """返回 ``(status, headers, 解析后的 JSON)``；非 JSON 响应直接判红。"""
        status, headers, body = self.raw_get(f"/api/analytics/dashboard{query}")
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            self.fail(f"响应不是 JSON：status={status} body={body[:200]!r}")
        return status, headers, data

    # ---------------- 400 翻译（本轮新增的行为面） ---------------- #

    def test_bad_range_is_unified_400_not_500(self) -> None:
        """``from=2026-13-99`` → 400 统一错误体，不是 500 堆栈（R6-4 前置的核心）。"""
        status, _headers, data = self.dashboard("?from=2026-13-99")
        self.assertEqual(400, status, data)
        self.assertEqual("ANALYTICS_RANGE_INVALID", data["code"], data)
        self.assertEqual(UNIFIED_BODY_KEYS, set(data), "统一错误体只这四键，不塞堆栈")
        self.assertEqual("from/to", data["detail"]["field"])
        self.assertTrue(str(data["message"]).strip(), "message 必须是给人看的一句话")
        self.assertTrue(str(data["request_id"]), "request_id 必须带上（与导出侧同款）")

    def test_reversed_range_and_bad_granularity_are_unified_400(self) -> None:
        """区间反向与非法粒度同属分析域参数不合法：两处都是 400 + ``detail.field``。"""
        status, _headers, data = self.dashboard("?from=2026-11-20&to=2026-11-10")
        self.assertEqual(400, status, data)
        self.assertEqual("ANALYTICS_RANGE_INVALID", data["code"], data)

        status, _headers, data = self.dashboard("?granularity=hour")
        self.assertEqual(400, status, data)
        self.assertEqual("ANALYTICS_RANGE_INVALID", data["code"], data)
        self.assertEqual("granularity", data["detail"]["field"])
        self.assertEqual(data["detail"].get("received"), "hour")

    def test_translation_is_byte_same_shape_as_export_side(self) -> None:
        """同一个坏参数，dashboard 与 export 两个面出**同一句人话**（同码同 field 同形状）。"""
        dash = self.dashboard("?granularity=hour")[2]
        export_status, _hd, export_body = self.raw_get(
            "/api/analytics/export?format=csv&granularity=hour")
        self.assertEqual(400, export_status)
        export = json.loads(export_body.decode("utf-8"))
        self.assertEqual(dash["code"], export["code"])
        self.assertEqual(dash["message"], export["message"])
        self.assertEqual(dash["detail"], export["detail"])
        self.assertEqual(set(dash), set(export), "两侧错误体键集一致，前端一套文案吃得下")

    def test_error_body_carries_no_stack_hint(self) -> None:
        """400 体里不许有 500 时代的 ``INTERNAL_ERROR`` / 异常类名提示（诚实但不泄内部）。"""
        _status, _headers, data = self.dashboard("?from=2026-13-99")
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("INTERNAL_ERROR", blob)
        self.assertNotIn("Traceback", blob)
        self.assertNotIn("hint", data["detail"], "分析域的 detail 只有 field/received")

    # ---------------- T26 成功形状与读数零改动（回归钉） ---------------- #

    def test_success_shape_and_readings_unchanged(self) -> None:
        """成功形状 = T26 冻结 16 键 + R6-1 三件；每个数字 = 本地 ``dashboard`` 复算。"""
        status, _headers, data = self.dashboard("?granularity=day")
        self.assertEqual(200, status, data)
        self.assertTrue(data["ok"])
        report = data["analytics"]
        self.assertEqual(T26_FROZEN_KEYS | R61_META_KEYS, set(report),
                         "R6-4 不许给 T26 的成功形状加键或减键")
        local = an.dashboard(copy.deepcopy(self.records()))
        for key in sorted(T26_FROZEN_KEYS - {"generated_at", "range"}):
            self.assertEqual(report[key], local[key], key)
        self.assertGreater(report["total_tasks"], 0)
        self.assertGreater(report["total_interventions"], 0)
        self.assertEqual(round(report["total_interventions"] / report["total_tasks"], 4),
                         report["intervention_rate"])
        self.assertEqual(report["users"]["count"], len(report["users"]["ids"]),
                         "用户面维持脱敏 ids 清单：计数与清单同源（界面内网口径未动）")
        self.assertGreaterEqual(report["users"]["count"], 2)
        for digest in report["users"]["ids"]:
            self.assertRegex(digest, r"^[0-9a-f]{12}$")

    def test_metadata_trio_drives_the_slow_label(self) -> None:
        """元数据三件套：首跑聚合命中 ``pre_aggregated=true``；``agent`` 过滤 → 流式 + ``reason``。"""
        status, _headers, data = self.dashboard("?granularity=day")
        self.assertEqual(200, status, data)
        hit = data["analytics"]
        self.assertIs(True, hit["pre_aggregated"], "首跑引导聚合后必须命中（账本真的接上）")
        self.assertIsInstance(hit["scanned"], int)
        self.assertGreater(hit["scanned"], 0)
        self.assertNotIn("reason", hit["agg"], "命中侧不许放假原因")
        self.assertTrue({"newly_aggregated", "ledger_rows", "head"} <= set(hit["agg"]),
                         sorted(hit["agg"]))

        status, _headers, data = self.dashboard("?granularity=day&agent=agent-a")
        self.assertEqual(200, status, data)
        stream = data["analytics"]
        self.assertIs(False, stream["pre_aggregated"], "agent 过滤必须如实标未预聚合")
        self.assertEqual("agent_filter", stream["agg"]["reason"],
                         "「未预聚合，加载较慢」标注的文案来源")
        self.assertGreaterEqual(stream["scanned"], 1)

    def test_week_granularity_success_still_200(self) -> None:
        """粒度 ``week`` 是冻结枚举里的合法值：成功面 200，``granularity`` 回显一致。"""
        status, _headers, data = self.dashboard("?granularity=week")
        self.assertEqual(200, status, data)
        self.assertEqual("week", data["analytics"]["granularity"])
        self.assertTrue(data["analytics"]["trend"], "周桶趋势必须真出点")


class DashboardZeroRegressionTests(unittest.TestCase):
    """铁律侧：零新路由、签名域零改动、分析模块自检绿。"""

    def setUp(self) -> None:
        self.root = scratch_dir(f"r64-dash-regression-{self.id().rsplit('.', 1)[-1]}")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_analytics_routes_stay_two_and_signature_untouched(self) -> None:
        routes = {(r["path"], r["verb"]) for r in CONTRACT["routes"]}
        self.assertEqual({("/api/analytics/dashboard", "GET"),
                          ("/api/analytics/export", "GET")},
                         {(p, v) for (p, v) in routes if p.startswith("/api/analytics")},
                         "R6-4 前置不新增公开路由（400 翻译挂在既有端点上）")
        self.assertEqual(("timestamp", "agent_id", "action_type", "tool", "input_hash",
                          "output_hash", "prev_hash", "entry_hash", "risk_level",
                          "confidence", "status"), tuple(sec.SIGNATURE_FIELDS))

    def test_handler_translates_and_leaves_success_path_untouched(self) -> None:
        """源码级锚点：翻译块只包在 T26 那条分支里，导出分支的翻译一字未动。"""
        source = (ROOT / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        dash = source.split("if path == API_PATH_ANALYTICS_DASHBOARD:", 1)[1].split(
            "if path == API_PATH_ANALYTICS_EXPORT:", 1)[0]
        self.assertIn("except analytics_mod.AnalyticsError as exc", dash)
        self.assertIn('raise _bad(exc.code, field=exc.field, detail=dict(exc.detail),', dash)
        self.assertIn("self.send_json(HTTPStatus.OK, payload)", dash)
        self.assertNotIn("except Exception", dash, "不许在 T26 分支里吞掉一切异常")

    def test_analytics_self_check_still_green(self) -> None:
        from scratch import use_scratch_tempfile
        with use_scratch_tempfile():
            self.assertEqual(0, an._self_check())


if __name__ == "__main__":  # 手工自检：python tests/test_analytics_dashboard_http.py -v
    unittest.main(verbosity=2)
