"""S0-1 验收测试：``intervention_history`` 的运行时写入 + 读时组装。

运行（全程离线，只用标准库 unittest；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python tests/test_intervention_history.py
    python -m unittest discover -s tests -p "test_intervention_history.py"

钉住的口径（任务 §2 / §3）：
  1. 干预一次 → 恰好一条 ``action_type="intervention"`` 的**独立签名记录**：
     ``secretariat`` 验签通过（signature / entry_hash valid、prev_hash 链连续）、过 schema v1.1；
  2. ``/api/records`` 视图里目标记录的 ``intervention_history`` 恰含该条目；
     **落盘的记录文件里没有任何 ``intervention_history`` 字段**（视图只在响应里组装）；
  3. ``idempotency_key`` 命中 replay → 绝不重复追加干预记录；
  4. 入参伪造 ``intervention_history`` / ``signature`` 被拒（``/intervene`` 字段白名单 +
     导入侧 unknown-key 归档 + 入库重签），记录本体与磁盘都不带伪造值；
  5. 组装是纯函数：上限 50（schema 1398）、时间升序、孤儿干预记录不隐藏、不改写入参；
  6. 纯内存 + 关签名的 t8 口径：记录照追加、如实不带签名（不假装签了）、视图照样组装。
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
from collections.abc import Mapping  # noqa: F401  （与既有测试同一口径：只用标准库）

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试

from agent_explainer import intervention_history as ih  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
DECIDABLE = ("pending_confirmation", "expired")


def native(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in NATIVE if r["id"] == action_id))


def call(base: str, path: str, body: object | None = None,
         method: str = "GET") -> tuple[int, dict, dict]:
    """返回 (status, headers, json body)；4xx/5xx 也把响应体带回来（要看错误码）。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None

    def once():
        request = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, raw, headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            status, raw, headers = exc.code, exc.read(), dict(exc.headers)
            exc.close()          # 4xx 是这里的正常断言路径，别让 ResourceWarning 刷屏
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


def intervention_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("action_type") or "") == ih.INTERVENTION_ACTION_TYPE]


def decidable(rows: list[dict], risks: tuple[str, ...] | None = None) -> list[dict]:
    out = [r for r in rows if r.get("demo_data") and str(r.get("status")) in DECIDABLE
           and not isinstance(r.get("user_intervention"), Mapping)]
    if risks:
        out = [r for r in out if str(r.get("risk_level")) in risks]
    return out


def pick_pending(rows: list[dict]) -> dict:
    rows = decidable(rows)
    assert rows, "演示数据里没有可决记录"
    return rows[0]


def stamp(index: int) -> str:
    """严格递增的合法时间戳（跨月推进，避免"最大 id 的时间戳不是最新"）。"""
    month, day = 1 + (index - 1) // 28, 1 + (index - 1) % 28
    return f"2026-{month:02d}-{day:02d}T10:00:00Z"


def modify_patch(target: dict) -> dict:
    """从被干预记录的 modify 选项里取可改字段，造一个"确实与当前值不同"的补丁。"""
    fields = sv.editable_fields_of(target)
    current = sv.current_params(target, fields)
    for field in fields:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        default = field.get("default")
        if isinstance(default, bool):
            value = not default
        elif isinstance(default, (int, float)):
            value = int(default) + 1
        else:
            value = "tracelens-s0-1"
        if current.get(name) == value:
            continue
        return {name: value}
    return {}


class InterventionHistoryTests(unittest.TestCase):
    """产品口径的持久化服务器：records.jsonl + audit.jsonl + 入库即签名（R4）。"""

    def setUp(self) -> None:
        self.root = scratch_dir("intervention-history")
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

    # -- 辅助 --------------------------------------------------------------
    def view(self) -> list[dict]:
        status, _hd, data = call(self.base, "/api/records")
        self.assertEqual(200, status, data)
        return data["records"]

    def view_of(self, action_id: str) -> dict:
        for row in self.view():
            if row.get("id") == action_id:
                return row
        raise AssertionError(f"视图里找不到目标记录 {action_id}")

    def stored(self, action_id: str) -> dict:
        """``GET /api/records/<id>``：这条路径读的是存储本体（不经组装）。"""
        status, _hd, data = call(self.base, f"/api/records/{action_id}")
        self.assertEqual(200, status, data)
        return data

    def disk_rows(self) -> list[dict]:
        """磁盘 ``records.jsonl`` 里真实存在的记录（不是响应视图）。"""
        out: list[dict] = []
        for line in self.store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("op") == "put" and isinstance(entry.get("record"), dict):
                out.append(entry["record"])
        return out

    def intervene(self, action_id: str, decision: str = "approve",
                  **extra) -> tuple[int, dict, dict]:
        body: dict = {"action_id": action_id, "decision": decision}
        body.update(extra)
        return call(self.base, "/intervene", body, "POST")

    def verify(self, record: dict) -> dict:
        status, _hd, data = call(self.base, "/api/secretariat/verify",
                                 {"record": record}, "POST")
        self.assertEqual(200, status, data)
        return data["results"][0]

    # -- 1. 干预 = 一条独立签名记录 ----------------------------------------
    def test_intervention_appends_exactly_one_signed_record(self) -> None:
        target = pick_pending(self.view())
        status, headers, decided = self.intervene(target["id"], "approve",
                                                  idempotency_key="iv-once-1")
        self.assertEqual(200, status, decided)
        self.assertEqual("false", headers.get("x-intervene-replay"))
        audit_hash = headers.get("x-intervene-audit-hash") or ""
        self.assertTrue(audit_hash.startswith("sha256:"), headers)

        mine = intervention_rows(self.disk_rows())
        self.assertEqual(1, len(mine), "干预一次必须恰好追加一条干预记录")
        record = mine[0]
        self.assertEqual(ih.INTERVENTION_ACTION_TYPE, record["action_type"])
        # 契约的 target_action_id 落在 schema 既有链字段 parent_action_id 上
        self.assertEqual(target["id"], record["parent_action_id"])
        self.assertTrue(record.get("signature"), "干预记录必须带签名")
        self.assertTrue(record.get("entry_hash"))
        self.assertTrue(record.get("prev_hash"), "prev_hash 必须接当前记录链头")
        ui = record["user_intervention"]
        self.assertEqual("approve", ui["decision"])
        self.assertEqual(audit_hash, ui.get("audit_entry_hash"),
                         "记录里的审计 entry_hash 必须等于那条审计事件（记录↔审计双向可追）")
        self.assertEqual(target["agent_id"], record["agent_id"], "agent_id 继承被干预记录")
        self.assertEqual(target["risk_level"], record["risk_level"], "risk_level 继承被干预记录")
        self.assertEqual("approved", record["status"], "status 与干预决定一致")
        self.assertEqual(1, self.server.service.intervention_records)
        self.assertEqual(0, self.server.service.intervention_record_failures)
        # 两条记录的本体都不带历史数组（铁律 2/3）
        self.assertNotIn("intervention_history", record)
        self.assertNotIn("intervention_history", self.stored(target["id"]))

    def test_intervention_record_verifies_and_passes_schema(self) -> None:
        target = pick_pending(self.view())
        status, _hd, body = self.intervene(target["id"], "reject", reason="这条不该放行")
        self.assertEqual(200, status, body)
        record = intervention_rows(self.disk_rows())[-1]
        self.assertEqual("rejected", record["status"])
        self.assertEqual([], schema_errors(record),
                         f"干预记录必须过 schema v1.1：{schema_errors(record)}")
        first = self.verify(record)
        self.assertTrue(first["ok"], first["problems"])
        self.assertEqual("valid", first["checks"]["signature"])
        self.assertEqual("valid", first["checks"]["entry_hash"])
        self.assertIn(first["checks"]["prev_hash"], ("continuous", "genesis"),
                      "entry_hash 链必须连续（prev_hash 接得上链头）")

    def test_two_interventions_chain_on_the_same_ledger(self) -> None:
        """两次干预 = 两条独立签名记录，各自接在同一条 entry_hash 链的当时链头上。

        取低/中风险目标：secretariat 对 high/critical 要求两人背书（既有策略），
        高危那一档单独用 ``test_high_risk_target_degrades_honestly`` 钉住。

        D-resign（R1-2b / PM-VERDICT-R1-2a §3.2）之后，两次干预之间多了一次
        **授权写回**：决定流把目标记录重签落盘（同一条 Agent 链），所以干预记录的
        ``prev_hash`` 接的是它写入时刻的链头 = 那次写回的目标记录哈希。链仍然连续，
        而且顺序可追：一次写回一次签名，没有"签了却看不见"的中间态。
        """
        rows = self.view()
        pair = decidable(rows, risks=("low", "medium"))[:2]
        self.assertEqual(2, len(pair), "演示数据里至少要两条低/中风险可决记录")
        self.intervene(pair[0]["id"], "approve")
        status, _hd, body = self.intervene(pair[1]["id"], "reject", reason="第二条也独立成条")
        self.assertEqual(200, status, body)
        mine = intervention_rows(self.disk_rows())
        self.assertEqual(2, len(mine))
        heads = {row.get("entry_hash") for row in self.disk_rows() if row.get("entry_hash")}
        self.assertIn(mine[1]["prev_hash"], heads,
                      "干预记录之间接同一条 entry_hash 链：prev_hash 必须是在案链头")
        for record, target in zip(mine, pair):
            written_back = self.stored(target["id"])
            self.assertTrue(written_back.get("entry_hash"),
                            "被干预的目标记录在授权写回点重签后必须有 entry_hash")
            self.assertEqual(written_back["entry_hash"], record["prev_hash"],
                             f"{target['id']}：干预记录的 prev_hash 接它写入前的链头"
                             "（那次授权写回重签的目标记录）")
        for record in mine:
            self.assertEqual([], schema_errors(record), schema_errors(record))
            first = self.verify(record)
            self.assertTrue(first["ok"], first["problems"])
        # 审计链在追加干预记录后仍自证完整
        status, _hd, audit = call(self.base, "/api/audit?limit=500")
        self.assertEqual(200, status)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])

    def test_high_risk_target_signs_but_needs_two_approvals(self) -> None:
        """高危目标：干预记录继承 risk_level，于是落进 secretariat 的既有四眼闸门。

        实测口径（不是本模块自造）：签名照写（``signature`` 有效、链连续），但
        ``verify`` 对 high/critical 记录要求 2 人背书 + seal → ``ok=false`` +
        ``SECRETARIAT_APPROVAL_REQUIRED``。留这条测试钉住"继承风险 = 继承背书要求"，
        并作为遗留问题报回 PM（干预记录要不要单独一档策略）。
        """
        rows = self.view()
        high = decidable(rows, risks=("high", "critical"))
        if not high:
            self.skipTest("演示数据里没有高危可决记录")
        target = high[0]
        failures_before = self.server.service.sign_failures
        status, _hd, body = self.intervene(target["id"], "approve")
        self.assertEqual(200, status, body)
        mine = intervention_rows(self.disk_rows())
        self.assertEqual(1, len(mine), "签名环节不背书写入也必须把这条干预历史落盘")
        record = mine[0]
        self.assertTrue(record.get("signature"), "签名照写：不因风险等级丢数据")
        self.assertEqual(target["risk_level"], record["risk_level"], "risk_level 继承被干预记录")
        self.assertTrue(record["policy"]["requires_human_confirmation"],
                        "schema C9：高危记录必须人确认")
        self.assertEqual([], schema_errors(record), schema_errors(record))
        self.assertEqual(failures_before, self.server.service.sign_failures,
                         "签名本身没失败，失败点在『背书不足』")
        first = self.verify(record)
        self.assertFalse(first["ok"], first["problems"])
        codes = {str(p.get("code")) for p in first.get("problems") or []}
        self.assertIn("SECRETARIAT_APPROVAL_REQUIRED", codes, first["problems"])
        self.assertEqual("valid", first["checks"]["signature"])
        self.assertEqual("valid", first["checks"]["entry_hash"])
        self.assertIn(first["checks"]["prev_hash"], ("continuous", "genesis"))
        entry = self.view_of(target["id"])["intervention_history"][0]
        self.assertTrue(entry["signed"], "带签名就报 signed=true（背书是另一道闸门）")
        self.assertEqual(record["id"], self.stored(record["id"])["id"], "记录本身仍可逐条取回")

    # -- 2. 读时组装：视图里有、磁盘里没有 ----------------------------------
    def test_records_view_assembles_history_and_disk_stays_clean(self) -> None:
        target = pick_pending(self.view())
        patch = modify_patch(target)
        if patch:
            status, headers, body = self.intervene(target["id"], "modify",
                                                   modified_params=patch,
                                                   reason="改一个参数再放行")
            self.assertEqual(200, status, body)
            expected = "modify"
        else:
            status, headers, body = self.intervene(target["id"], "approve")
            self.assertEqual(200, status, body)
            expected = "approve"

        history = self.view_of(target["id"]).get("intervention_history")
        self.assertIsInstance(history, list, "视图里必须挂上 intervention_history 数组")
        self.assertEqual(1, len(history), history)
        entry = history[0]
        self.assertIsInstance(entry, dict, "schema 1399：条目必须是对象")
        self.assertEqual(target["id"], entry["target_action_id"])
        self.assertEqual(expected, entry["decision"])
        self.assertTrue(entry["actor_id"])
        self.assertEqual(headers.get("x-intervene-audit-hash"), entry["audit_entry_hash"])
        self.assertTrue(entry["entry_hash"], "条目要能追回那条独立签名记录")
        self.assertTrue(entry["signed"])
        if patch:
            self.assertEqual(patch, entry["modified_params"])

        # 干预记录已归组进目标记录：不在顶层重复，但仍可按 id 逐条取回验签
        top_ids = [r["id"] for r in self.view()]
        self.assertNotIn(entry["id"], top_ids)
        standalone = self.stored(entry["id"])
        self.assertEqual(ih.INTERVENTION_ACTION_TYPE, standalone["action_type"])
        self.assertEqual([], schema_errors(standalone), schema_errors(standalone))
        self.assertTrue(self.verify(standalone)["ok"])

        # 磁盘：整份 JSONL 里既没有这个键，任何一条记录对象里也没有
        raw = self.store_path.read_text(encoding="utf-8")
        self.assertNotIn("intervention_history", raw,
                         "落盘的记录文件绝不许带 intervention_history 字段（铁律 3）")
        for row in self.disk_rows():
            self.assertNotIn("intervention_history", row, row.get("id"))
        self.assertNotIn("intervention_history", self.stored(target["id"]),
                         "单条端点读的是存储本体：视图字段不得渗进去")

    def test_history_is_time_ordered_and_capped_at_50(self) -> None:
        """组装侧的上限：超过 50 条时保留最近 50 条（schema 1398）。"""
        service = self.server.service
        target = pick_pending(self.view())
        rows = service.store.all()
        seed = next(r for r in rows if r["id"] == target["id"])
        extras = [self.fake_intervention(target["id"], i) for i in range(1, 61)]
        for row in extras:
            service.store.put(row)
        history = self.view_of(target["id"])["intervention_history"]
        self.assertEqual(ih.HISTORY_LIMIT, len(history), "上限 50 = schema maxItems")
        self.assertEqual([e["timestamp"] for e in history],
                         sorted(e["timestamp"] for e in history), "按时间升序")
        self.assertEqual(extras[-1]["id"], history[-1]["id"], "append-only：留最近的那些")
        self.assertNotIn("intervention_history", self.stored(target["id"]))

    def fake_intervention(self, target_id: str, index: int) -> dict:
        return {
            "id": f"act_iv_probe_{index:03d}",
            "schema_version": "1.1",
            "timestamp": stamp(index),
            "agent_id": "agent-probe",
            "actor_id": "local-user",
            "workspace_id": "default",
            "session_id": "local-session",
            "parent_action_id": target_id,
            "action_type": ih.INTERVENTION_ACTION_TYPE,
            "tool_used": {"name": "probe"},
            "status": "approved",
            "user_intervention": {"decision": "approve", "decided_by": "local-user",
                                  "decided_by_role": "local",
                                  "decided_at": f"2026-11-{(index % 28) + 1:02d}T10:00:00Z",
                                  "recorded": True},
            "source": {"kind": "manual", "adapter": ih.INTERVENTION_ADAPTER},
            "demo_data": False,
            "synthetic": True,
        }

    # -- 3. 幂等 replay 不重复追加 -----------------------------------------
    def test_replay_does_not_append_twice(self) -> None:
        target = pick_pending(self.view())
        key = "iv-replay-1"
        status, _hd, first = self.intervene(target["id"], "approve", idempotency_key=key)
        self.assertEqual(200, status, first)
        status, headers, again = self.intervene(target["id"], "approve", idempotency_key=key)
        self.assertEqual(200, status, again)
        self.assertEqual("true", headers.get("x-intervene-replay"), headers)
        self.assertEqual(1, len(intervention_rows(self.disk_rows())),
                         "replay 命中不得再追加干预记录")
        self.assertEqual(1, len(self.view_of(target["id"])["intervention_history"]))
        self.assertEqual(1, self.server.service.intervention_records)

    # -- 4. 入参伪造被拒（沿用既有 protected 逻辑） -------------------------
    def test_intervene_body_forgery_is_rejected(self) -> None:
        target = pick_pending(self.view())
        for forged in ({"intervention_history": [{"decision": "approve", "decided_by": "x"}]},
                       {"signature": "ZmFrZQ=="},
                       {"entry_hash": "0" * 64},
                       {"user_intervention": {"decision": "approve"}},
                       {"prev_hash": "0" * 64}):
            body = {"action_id": target["id"], "decision": "approve"}
            body.update(forged)
            status, _hd, data = call(self.base, "/intervene", body, "POST")
            self.assertEqual(400, status, (forged, data))
            self.assertEqual("INTERVENE_FIELD_UNKNOWN", data.get("code"), (forged, data))
        self.assertEqual(0, len(intervention_rows(self.disk_rows())),
                         "被拒的伪造请求不得留下任何干预记录")
        self.assertNotIn("intervention_history", self.stored(target["id"]))

    def test_imported_forgery_is_rejected_and_never_lands(self) -> None:
        """导入侧伪造 ``intervention_history`` / ``signature``：适配器闸门直接拒收。

        既有口径（``ADAPTER_OUTPUT_INVALID``）：适配器产出的记录必须字段封闭，
        ``intervention_history`` / ``signature`` / ``entry_hash`` 这类签名与扩展字段
        由服务端独占，入参携带 = 直接 400，严格模式在第一条坏记录就停下；
        ``strict=false`` 也只是"坏的跳过"，绝不把伪造值写进存储。
        """
        forged = ("intervention_history", "signature", "entry_hash")
        for key in forged:
            record = native("act-demo-0001")
            record["id"] = f"act-s01-forged-{key}"
            record[key] = ("ZmFrZQ==" if key == "signature"
                           else ("0" * 64 if key == "entry_hash"
                                 else [{"decision": "approve", "decided_by": "attacker"}]))
            status, _hd, result = call(self.base, "/api/import", {"records": [record]}, "POST")
            self.assertEqual(400, status, (key, result))
            self.assertEqual("ADAPTER_OUTPUT_INVALID", result.get("code"), (key, result))
            self.assertIn(key, str((result.get("detail") or {}).get("message", "")) +
                          json.dumps(result, ensure_ascii=False))
            self.assertNotIn(f"act-s01-forged-{key}",
                             [r.get("id") for r in self.disk_rows()],
                             "被拒的伪造记录不得进存储")
        # 宽松模式：坏的跳过、好的入库 —— 伪造记录依然进不来
        bad = native("act-demo-0001")
        bad["id"] = "act-s01-forged-loose"
        bad["intervention_history"] = [{"decision": "approve", "decided_by": "attacker"}]
        good = native("act-demo-0001")
        good["id"] = "act-s01-good"
        status, _hd, result = call(self.base, "/api/import",
                                   {"records": [bad, good], "strict": False}, "POST")
        self.assertEqual(200, status, result)
        self.assertNotIn("act-s01-forged-loose", [r.get("id") for r in self.disk_rows()],
                         "宽松模式也只是跳过坏记录，绝不落盘伪造的『干预历史』")
        stored = self.stored("act-s01-good")
        self.assertNotIn("intervention_history", stored)
        self.assertTrue(stored.get("signature"), "合法记录入库即签（R4 既有口径）")
        self.assertTrue(self.verify(stored)["ok"])
        self.assertNotIn("intervention_history", self.store_path.read_text(encoding="utf-8"))

    def test_protected_field_list_still_declares_the_field(self) -> None:
        """名单本身是闸门：``intervention_history`` 必须留在禁入参名单里。"""
        self.assertIn("intervention_history", sv.EXPANSION_PROTECTED_FIELDS)
        self.assertNotIn("intervention_history", sv.EXPANSION_MERGE_FIELDS)

    def test_reset_drops_intervention_records_and_audit_keeps_them(self) -> None:
        """演示重置：干预记录随记录一起清掉，审计链只追加不删（既有口径）。"""
        target = pick_pending(self.view())
        self.intervene(target["id"], "approve")
        self.assertEqual(1, len(intervention_rows(self.disk_rows())))
        status, _hd, data = call(self.base, "/api/reset", {}, "POST")
        self.assertEqual(200, status, data)
        self.assertEqual(0, len(intervention_rows(self.disk_rows())))
        self.assertNotIn("intervention_history", self.store_path.read_text(encoding="utf-8"))
        status, _hd, audit = call(self.base, "/api/audit?limit=500")
        self.assertEqual(200, status)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])


class AssemblyFunctionTests(unittest.TestCase):
    """组装是纯函数：不碰存储、不改入参、上限 50、时间升序、孤儿不隐藏。"""

    def intervention(self, target: str, index: int, *, stamp: str,
                     signed: bool = True) -> dict:
        record = {
            "id": f"act_iv_{index:03d}",
            "action_type": ih.INTERVENTION_ACTION_TYPE,
            "parent_action_id": target,
            "timestamp": stamp,
            "status": "approved",
            "actor_id": "local-user",
            "entry_hash": f"{index:064x}",
            "user_intervention": {"decision": "approve", "decided_by": "local-user",
                                  "decided_by_role": "local", "decided_at": stamp,
                                  "recorded": True,
                                  "audit_entry_hash": f"sha256:{index:064x}"},
            "source": {"kind": "manual", "adapter": ih.INTERVENTION_ADAPTER},
        }
        if signed:
            record["signature"] = "sig"
        return record

    def decided_fixture(self, decision: str = "approve") -> dict:
        """真实口径：``apply_decision`` 的产物才是构造器的入参（决定已落进记录）。"""
        record = native("act-demo-0004")
        return sv.apply_decision(record, decision,
                                 reason=("改一个参数" if decision == "modify" else "该放行"),
                                 modified_params=(modify_patch(record)
                                                  if decision == "modify" else None),
                                 actor_id="local-user", actor_role="local",
                                 request_id="req-unit-1",
                                 audit_entry_hash="sha256:" + "a" * 64)

    def test_build_record_shape_no_history_key_and_passes_schema(self) -> None:
        decided = self.decided_fixture("approve")
        built = ih.build_intervention_record(decided, audit_entry_hash="sha256:" + "b" * 64)
        self.assertEqual(ih.INTERVENTION_ACTION_TYPE, built["action_type"])
        self.assertEqual("act-demo-0004", built["parent_action_id"])
        self.assertEqual("approved", built["status"])
        self.assertEqual("sha256:" + "b" * 64, built["user_intervention"]["audit_entry_hash"])
        self.assertNotIn("intervention_history", built, "构造器绝不产出历史字段（铁律 3）")
        self.assertTrue(ih.is_intervention_record(built))
        self.assertEqual([], schema_errors(built),
                         f"未签名的干预记录也要过 schema：{schema_errors(built)}")
        # 入参不被改写（铁律 2：不往已有记录上动刀）
        self.assertEqual("sha256:" + "a" * 64,
                         decided["user_intervention"]["audit_entry_hash"])

    def test_build_record_for_modify_and_reject(self) -> None:
        modify = self.decided_fixture("modify")
        built = ih.build_intervention_record(modify)
        self.assertEqual("modified", built["status"])
        self.assertEqual("modify", built["user_intervention"]["decision"])
        self.assertTrue(built["user_intervention"]["modified_params"])
        self.assertEqual([], schema_errors(built), schema_errors(built))
        reject = self.decided_fixture("reject")
        built_reject = ih.build_intervention_record(reject)
        self.assertEqual("rejected", built_reject["status"])
        self.assertTrue(built_reject["user_intervention"]["reason"])
        self.assertEqual([], schema_errors(built_reject), schema_errors(built_reject))
        # 非 modify 决定不带参数（"看着像改过" = 假语义）
        self.assertNotIn("modified_params", built_reject["user_intervention"])

    def test_build_record_refuses_dirty_inputs(self) -> None:
        decided = self.decided_fixture("approve")
        with self.assertRaises(ValueError):
            ih.build_intervention_record(decided, decision="yolo")
        decided["user_intervention"]["decision"] = "modify"
        with self.assertRaises(ValueError):
            ih.build_intervention_record(decided, decision="modify")
        with self.assertRaises(TypeError):
            ih.build_intervention_record("not-a-record")

    def test_high_risk_target_gets_human_confirmation_and_impact_scope(self) -> None:
        decided = self.decided_fixture("approve")
        decided["risk_level"] = "critical"
        built = ih.build_intervention_record(decided)
        self.assertEqual("critical", built["risk_level"])
        self.assertTrue(built["policy"]["requires_human_confirmation"],
                        "schema C9：高危记录必须人确认")
        self.assertEqual(1, len(built["impact_scope"]["targets"]))
        self.assertEqual([], schema_errors(built), schema_errors(built))

    def test_assemble_caps_at_schema_limit_and_orders_by_time(self) -> None:
        target = {"id": "act-t", "action_type": "tool_call", "status": "approved"}
        rows = [target] + [self.intervention("act-t", i, stamp=stamp(i))
                           for i in range(1, 61)]
        snapshot = copy.deepcopy(rows)
        out = ih.assemble_intervention_history(rows)
        self.assertEqual(1, len(out), "有归属的干预记录从顶层归组掉")
        history = out[0]["intervention_history"]
        self.assertEqual(ih.HISTORY_LIMIT, len(history), "上限 50 = schema 1398 的 maxItems")
        self.assertEqual([e["timestamp"] for e in history],
                         sorted(e["timestamp"] for e in history), "历史按时间升序")
        self.assertEqual(rows[-1]["id"], history[-1]["id"], "append-only：留最近的那些")
        self.assertEqual(snapshot, rows, "组装不得改写传入的记录对象")
        self.assertNotIn("intervention_history", rows[0])

    def test_assemble_keeps_orphans_and_entries_carry_provenance(self) -> None:
        orphan = self.intervention("act-gone", 7, stamp="2026-11-05T10:00:00Z")
        out = ih.assemble_intervention_history([orphan])
        self.assertEqual([orphan["id"]], [r["id"] for r in out], "孤儿干预记录不得凭空消失")
        self.assertNotIn("intervention_history", out[0])
        entry = ih.history_entry(orphan)
        self.assertEqual("act-gone", entry["target_action_id"])
        self.assertEqual("approve", entry["decision"])
        self.assertEqual("local-user", entry["actor_id"])
        self.assertEqual("local", entry["actor_role"])
        self.assertEqual(f"sha256:{7:064x}", entry["audit_entry_hash"])
        self.assertTrue(entry["signed"])

    def test_adapter_authored_intervention_record_is_not_swallowed(self) -> None:
        """外部适配器自己的 ``action_type="intervention"`` 记录不属于本机干预历史。

        schema 133 的枚举是公开值：langfuse / mcp_events / 原生导入都能产出这类记录。
        读时组装只认 ``source.adapter = tracelens-intervention`` 的落款，否则会把
        别人的记录从列表里吞掉（``test_import_persistence`` 的夹具就踩这条）。
        """
        foreign = self.intervention("act-t", 9, stamp=stamp(9))
        foreign["source"] = {"kind": "upload", "adapter": "mcp_events"}
        rows = [{"id": "act-t", "action_type": "tool_call", "status": "approved"}, foreign]
        out = ih.assemble_intervention_history(rows)
        self.assertEqual([r["id"] for r in rows], [r["id"] for r in out],
                         "适配器产出的 intervention 记录必须留在顶层")
        self.assertFalse(ih.is_intervention_record(foreign))
        self.assertNotIn("intervention_history", out[0])

    def test_unsigned_entry_reports_signed_false(self) -> None:
        entry = ih.history_entry(self.intervention("act-t", 1,
                                                   stamp="2026-11-05T10:00:00Z",
                                                   signed=False))
        self.assertFalse(entry["signed"], "没签名就如实报 false，不许假装签过")


class MemoryModeTests(unittest.TestCase):
    """纯内存 + 关签名（t8 口径）：记录照追加、如实不带签名、视图照样组装。"""

    def setUp(self) -> None:
        self.root = scratch_dir("intervention-history-memory")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.service = sv.InterveneService(
            records=copy.deepcopy(NATIVE), mode=sv.MODE_LOCAL, sign_on_write=False,
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            features_root=self.root / "features")
        self.service.store.reset_to_demo()

    def test_unsigned_intervention_record_still_appends_and_assembles(self) -> None:
        target = pick_pending(self.service.records_payload()["records"])
        _record, meta = self.service.intervene(
            {"action_id": target["id"], "decision": "approve"}, request_id="req-mem-1")
        self.assertFalse(meta["replay"])
        stored = intervention_rows(self.service.store.all())
        self.assertEqual(1, len(stored))
        self.assertFalse(stored[0].get("signature"),
                         "签名开关关闭时不签名，也不许假装签了（如实口径）")
        view = self.service.records_payload()["records"]
        target_view = next(r for r in view if r["id"] == target["id"])
        history = target_view["intervention_history"]
        self.assertEqual(1, len(history))
        self.assertFalse(history[0]["signed"])
        for row in self.service.store.all():
            self.assertNotIn("intervention_history", row)

    def test_replay_in_memory_mode_appends_once(self) -> None:
        target = pick_pending(self.service.records_payload()["records"])
        self.service.intervene({"action_id": target["id"], "decision": "approve",
                                "idempotency_key": "mem-idem"}, request_id="req-mem-2")
        _record, meta = self.service.intervene(
            {"action_id": target["id"], "decision": "approve",
             "idempotency_key": "mem-idem"}, request_id="req-mem-3")
        self.assertTrue(meta["replay"])
        self.assertEqual(1, len(intervention_rows(self.service.store.all())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
