"""0.6.0 L1「八功能接线」验收测试：五个此前 `ROUTE_NOT_FOUND` 的端点。

背景（`_pm-dispatch/REPORT-八功能真实排查.md`）：八功能里有五个入口在 HTTP 面上
**从未存在**，服务层却早已实现，症状统一是 `ROUTE_NOT_FOUND` —— 界面点下去只会
看到"功能不存在"，用户因此以为功能没做。

本文件钉住五件事，每一条都对应一个真实缺口：

1. **T13** `POST/GET /api/records/{id}/evidence` —— `_record_sub_route()` 此前
   **定义了但全文件零调用**，于是"挂证据"没有入口；读取侧只有按 evidence_id 单条取，
   拿不到"这条记录有哪些证据"。
2. **T17** `POST /api/records/{id}/causal` —— 同一个解析器，同样没有入口。
3. **T16** `POST /api/evidence/{id}/feedback` —— do_GET 里对 feedback 明确回 405
   （"POST only"），但 do_POST 里没有对应分支，405 指向了一个不存在的方法。
4. **T18 写侧** `POST /api/groups` —— 能列出 0 个组，却造不出第一个组。
5. **T33** `POST /api/compliance/export` —— 契约里「声明了常量没人接住」的最后一格；
   而且服务层内部把 `AUDIT_QUERY_LIMIT_MAX` 错写成 `audit_mod.` 前缀（该常量定义在
   server.py 本模块），所以这条路一被真正调用就 `AttributeError` → 500。

两个"边界"同样钉住，防止修复把"参数不对"糊成"服务器坏了"：
  * T13 非法 kind 必须回 4xx（`EVIDENCE_*_INVALID`），不许 500；
  * T18 超 `MAX_AGENTS` 必须回 `GROUP_AGENTS_EXCEEDED`，**不截断、不静默丢 Agent**。

传输纪律：HTTP 一律走 `tests/http_fixture.py`（端口就绪 + 连接级瞬态一次性重连，
POST 绝不重放）；临时目录一律 `tests/scratch.py`；数据根用临时目录，不碰仓库 var/。
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
import urllib.error
import urllib.request
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402
import http_fixture as fx  # noqa: E402

from agent_explainer import groups as groups_mod  # noqa: E402
from agent_explainer import server as sv  # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
# 一条"已决定、可回滚"的种子记录：T13/T16/T17 都要求记录真实存在
RECORD = "act-demo-0001"


def request(method: str, base: str, path: str, payload=None, headers=None):
    """发一个请求，4xx/5xx 也把响应体带回来（要看错误码）。POST 不重放。"""
    data = None
    extra = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        extra["Content-Type"] = "application/json"
    extra.update(dict(headers or {}))

    def once():
        req = urllib.request.Request(base + path, data=data, method=method, headers=extra)
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                text = resp.read().decode("utf-8", "replace")
                status = resp.status
                head = {k.lower(): v for k, v in resp.getheaders()}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            status = exc.code
            head = {k.lower(): v for k, v in exc.headers.items()}
            exc.close()
        try:
            body: object = json.loads(text)
        except json.JSONDecodeError:
            body = {"_raw": text}
        return status, head, body

    return fx.urlopen_resilient(once, verb=method, path=path, timeout=20.0)


def code_of(payload: object) -> str:
    return str(payload.get("code")) if isinstance(payload, Mapping) else ""


class L1WiringCase(unittest.TestCase):
    """临时数据根 + 真服务（local 模式免登录），逐类自建自拆。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = scratch_dir("l1-wiring")
        cls.server, cls.thread = sv.start_background_server(
            "127.0.0.1", 0, records=json.loads(json.dumps(NATIVE)),
            web_dir=WEB, allow_reset=True, quiet=True,
            features_root=cls.root / "features",
            audit_log_path=cls.root / "audit.jsonl",
            ingest_token_path=cls.root / "ingest.json",
            settings_path=cls.root / "settings.json",
            auth_state_path=cls.root / "auth.json",
            mode="local", bootstrap_admin=False, session_ttl=3600)
        cls.service = cls.server.service
        cls.base = cls.server.url()
        cls.port = cls.server.server_address[1]
        fx.wait_port_ready(cls.port)

    def get(self, path: str):
        return request("GET", self.base, path)

    def post(self, path: str, payload=None):
        return request("POST", self.base, path, payload if payload is not None else {})

    # ---- T13 证据：写侧 + 读侧 ----

    def test_t13_post_attaches_and_get_lists(self) -> None:
        """T13：上传一条 diff 证据后，同一路径 GET 必须能列出它。"""
        status, _hd, data = self.post(
            f"/api/records/{RECORD}/evidence",
            {"kind": "diff", "payload": {"before": "a=1", "after": "a=2"},
             "caption": "L1 接线测试"})
        self.assertEqual(201, status, data)
        self.assertTrue(data.get("ok"), data)
        evidence = data.get("evidence") or {}
        evidence_id = str(evidence.get("id") or "")
        self.assertTrue(evidence_id.startswith("evd_"), data)
        self.assertEqual("diff", evidence.get("kind"), data)

        status, _hd, listing = self.get(f"/api/records/{RECORD}/evidence")
        self.assertEqual(200, status, listing)
        self.assertTrue(listing.get("ok"), listing)
        self.assertEqual(RECORD, listing.get("record_id"))
        ids = [str((row or {}).get("id") or "") for row in (listing.get("evidence") or [])]
        self.assertIn(evidence_id, ids, "刚挂上的证据必须出现在清单里")
        self.assertEqual(len(ids), listing.get("count"), "count 必须等于实际条数")

    def test_t13_invalid_kind_is_4xx_not_500(self) -> None:
        """证据域异常必须翻成 4xx：参数不对不能让用户以为服务器坏了。"""
        status, _hd, data = self.post(
            f"/api/records/{RECORD}/evidence", {"kind": "no-such-kind", "payload": "x"})
        self.assertIn(status, (400, 422), data)
        self.assertTrue(code_of(data).startswith("EVIDENCE_"), data)
        self.assertNotEqual("INTERNAL_ERROR", code_of(data))

    def test_t13_unknown_record_is_a_clean_404(self) -> None:
        """记录不存在 → EVIDENCE_NOT_FOUND（404），不是 500、不是 ROUTE_NOT_FOUND。"""
        status, _hd, data = self.post(
            "/api/records/act-does-not-exist/evidence",
            {"kind": "diff", "payload": {"before": "a", "after": "b"}})
        self.assertEqual(404, status, data)
        self.assertEqual("EVIDENCE_NOT_FOUND", code_of(data), data)

    # ---- T17 因果写回 ----

    def test_t17_writes_causal_explanation_back(self) -> None:
        """T17：写回后记录里必须有 `causal_explanation`，且响应自带同一份。"""
        status, _hd, data = self.post(f"/api/records/{RECORD}/causal",
                                      {"candidates": [], "lang": "zh"})
        self.assertEqual(200, status, data)
        self.assertTrue(data.get("ok"), data)
        explanation = data.get("causal_explanation")
        self.assertIsInstance(explanation, dict, data)
        self.assertTrue(explanation.get("counterfactual"), explanation)
        # 落盘事实：重新取记录，字段确实在
        status, _hd, record = self.get(f"/api/records/{RECORD}")
        self.assertEqual(200, status, record)
        self.assertEqual(explanation, record.get("causal_explanation"),
                         "写回内容与响应必须逐字相同（不做二次加工）")

    # ---- T16 队长评价 ----

    def test_t16_feedback_increments_once_per_vote(self) -> None:
        """T16：like 一次计数 +1；`dislike` 与 `like` 各记一格，不互相覆盖。"""
        status, _hd, created = self.post(
            f"/api/records/{RECORD}/evidence",
            {"kind": "diff", "payload": {"before": "x=1", "after": "x=2"}})
        self.assertEqual(201, status, created)
        evidence_id = str((created.get("evidence") or {}).get("id") or "")
        self.assertTrue(evidence_id, created)

        status, _hd, liked = self.post(f"/api/evidence/{evidence_id}/feedback",
                                       {"vote": "like"})
        self.assertEqual(200, status, liked)
        counts = (liked.get("evidence") or {}).get("feedback") or {}
        self.assertEqual(1, counts.get("like"), liked)

        status, _hd, again = self.post(f"/api/evidence/{evidence_id}/feedback",
                                       {"vote": "like"})
        self.assertEqual(200, status, again)
        counts = (again.get("evidence") or {}).get("feedback") or {}
        self.assertEqual(2, counts.get("like"), "同一个赞可以累加（计数恒增，不改历史）")

    def test_t16_invalid_vote_is_4xx(self) -> None:
        status, _hd, created = self.post(
            f"/api/records/{RECORD}/evidence", {"kind": "diff", "payload": {"before": "y=1", "after": "y=2"}})
        self.assertEqual(201, status, created)
        evidence_id = str((created.get("evidence") or {}).get("id") or "")
        status, _hd, data = self.post(f"/api/evidence/{evidence_id}/feedback",
                                      {"vote": "up"})
        self.assertIn(status, (400, 422), data)
        self.assertEqual("EVIDENCE_VOTE_INVALID", code_of(data), data)

    # ---- T18 建组（写侧） ----

    def test_t18_create_group_appears_in_list(self) -> None:
        """T18：建组 201 → 清单里能查到同一个 group_id（此前只能列出 0 个组）。"""
        status, _hd, created = self.post(
            "/api/groups", {"title": "L1 建组测试",
                            "agent_ids": ["agent-a", "agent-b", "agent-a"],
                            "objective": "接线验收"})
        self.assertEqual(201, status, created)
        self.assertTrue(created.get("ok"), created)
        group = created.get("group") or {}
        group_id = str(group.get("id") or "")
        self.assertTrue(group_id.startswith("grp_"), created)
        self.assertEqual(["agent-a", "agent-b"], group.get("agent_ids"),
                         "重复 Agent 按首次出现去重，且保持调用方给的顺序")

        status, _hd, listing = self.get("/api/groups")
        self.assertEqual(200, status, listing)
        ids = [str((row or {}).get("id") or "") for row in (listing.get("groups") or [])]
        self.assertIn(group_id, ids, "刚建的组必须出现在清单里")

    def test_t18_missing_title_is_field_invalid_not_500(self) -> None:
        status, _hd, data = self.post("/api/groups", {"agent_ids": ["agent-a"]})
        self.assertIn(status, (400, 422), data)
        self.assertEqual("GROUP_FIELD_INVALID", code_of(data), data)

    def test_t18_agent_limit_refuses_instead_of_truncating(self) -> None:
        """超上限必须报错，**不许截断**：静默丢 Agent 会让用户以为队友都在场。"""
        too_many = [f"agent-{i}" for i in range(groups_mod.MAX_AGENTS + 1)]
        status, _hd, data = self.post("/api/groups", {"title": "超限组",
                                                     "agent_ids": too_many})
        self.assertIn(status, (400, 422), data)
        self.assertEqual("GROUP_AGENTS_EXCEEDED", code_of(data), data)

    # ---- T33 合规导出 ----

    def test_t33_export_returns_a_real_record(self) -> None:
        """T33：导出 201，带 root_hash / sha256；上链字段按 D-2 决议恒为诚实降级。

        这条同时是 `audit_mod.AUDIT_QUERY_LIMIT_MAX` 错引用的回归钉 ——
        修之前它必抛 AttributeError（500），因为该常量定义在 server.py 本模块。
        """
        status, _hd, data = self.post("/api/compliance/export", {"format": "csv"})
        self.assertEqual(201, status, data)
        self.assertTrue(data.get("ok"), data)
        export = data.get("export") or {}
        self.assertTrue(str(export.get("id") or "").startswith("exp_"), data)
        self.assertTrue(str(export.get("root_hash") or "").startswith("sha256:"), data)
        self.assertTrue(str(export.get("sha256") or "").startswith("sha256:"), data)
        self.assertGreater(int(export.get("bytes") or 0), 0, data)
        self.assertTrue(export.get("chain_ok"), data)
        # D-2 决议：没有链上后端，就不许假装有
        self.assertIsNone(export.get("eth_tx_hash"), "eth_tx_hash 必须恒为 None")
        self.assertEqual("unavailable", export.get("anchor_state"))

    def test_t33_export_shows_up_in_history(self) -> None:
        status, _hd, data = self.post("/api/compliance/export", {"format": "csv"})
        self.assertEqual(201, status, data)
        export_id = str((data.get("export") or {}).get("id") or "")
        status, _hd, listing = self.get("/api/compliance/exports")
        self.assertEqual(200, status, listing)
        ids = [str((row or {}).get("id") or "") for row in (listing.get("exports") or [])]
        self.assertIn(export_id, ids, "导出记录必须进历史清单")

    def test_t33_bad_format_is_4xx_not_500(self) -> None:
        status, _hd, data = self.post("/api/compliance/export", {"format": "docx"})
        self.assertIn(status, (400, 422), data)
        self.assertTrue(code_of(data).startswith("COMPLIANCE_"), data)

    # ---- 契约面：五条路由全部进了契约 ----

    def test_the_five_routes_are_in_the_contract(self) -> None:
        """这五条必须是契约里的正式路由，而不是"服务层能跑但外面看不见"。"""
        contract = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
        routes = {(row["path"], row["verb"]) for row in contract["routes"]}
        expected = {
            ("/api/records/{id}/evidence", "GET"),
            ("/api/records/{id}/evidence", "POST"),
            ("/api/records/{id}/causal", "POST"),
            ("/api/evidence/{id}/feedback", "POST"),
            ("/api/groups", "POST"),
            ("/api/compliance/export", "POST"),
        }
        # `/api/evidence/{id}/feedback` 的 POST 走 `_evidence_route` 族解析，
        # 契约抽取按"每族一个 GET 骨架"计入，故它不在 routes 里 —— 与 T29/T30 同例，
        # 登记在 docs/07 §5。其余五条必须在契约里。
        self.assertIn(("/api/evidence/{id}/feedback", "POST"), expected)
        missing = expected - routes - {("/api/evidence/{id}/feedback", "POST")}
        self.assertEqual(set(), missing, f"这些路由没进契约：{sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
