"""R1-2b 验收测试：``POST /api/rollback`` 挂载 + 回滚记录签名落盘 + 读时派生视图。

运行（全程离线，只用标准库 unittest；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python tests/test_rollback_api.py
    python -m unittest discover -s tests -p "test_rollback_api.py"

钉住的口径（TASK-R1-2b §1/§2 + PM-VERDICT-R1-1 的裁决）：
  1. **挂载**（G-1 / TP-30）：``POST /api/rollback`` 有分发分支接住，权限位与
     ``/intervene`` 同一条 ``intervene.decide``，CSRF 同口径；契约抽取里它是
     ``auth=permission`` 的路由，``/api/rollback`` 不再躺在 ``declared_unmounted``；
  2. **签名落盘**（G-2）：``outcome["rollback_record"]`` 经 ``sign_on_write_record``
     后 ``store.put`` —— 撤销日志的 ``rollback_record_id`` 在 store 里查得到（不再悬空），
     记录自己验签通过、过 schema v1.1，``execution_receipt.state`` 仍是 ``unreported``；
  3. **口径 α（D-α）**：目标与闭包成员**零改写** —— 磁盘上它们一个字节都不变，
     ``rollback_state`` / ``next_ref`` 只出现在响应里，绝不落盘（不落盘 / 不进 schema /
     不进 api_contract，docs/07 §11.2 门禁）；
  4. **白名单**（G-31）：伪造 ``rollback_state`` / ``rollback_ref`` / ``signature`` /
     ``actor`` / ``max_depth`` 一律 400 ``ROLLBACK_FIELD_INVALID``，且**零写入**；
  5. **幂等同 /intervene 口径**：同 key 重放 → 不二次执行、不重复追加日志、不重复签名；
     同 key 指向别的 action_id → 409 ``ROLLBACK_IDEMPOTENCY_MISMATCH``；
  6. **错误表接线**（G-19 / G-16 / TP-19 / TP-26）：闭包成员撤销不掉 → 409
     ``ROLLBACK_DEPENDENTS_IRREVERSIBLE``；撤销日志读不动 → 503
     ``ROLLBACK_JOURNAL_UNREADABLE``（绝不让内部存储码当对外契约）；
  7. **计划侧幂等屏障**（G-6 / TP-20）：``GET /api/rollback/plan`` 走服务层 plan，
     二次回滚时计划与执行报同一个 ``ROLLBACK_ALREADY_ROLLED_BACK``；
  8. **读时派生**（G-3 / G-20）：``/api/records`` 的 ``meta.rollback_view`` 给出
     ``rolled_back`` / ``next_ref``（由 ``rollback_ref`` 反查）/ ``blocked``，
     记录行的 ``rollback_state`` 恒为 schema 枚举值；日志读不动时不派生"可回滚"。

   9. **journal 锚定入审计链**（R1-3 / G-13 / TP-31）：撤销日志条目带前态快照的规范
      JSON ``snapshot_sha256``（新键只追加在末尾，既有键与顺序一字不动）；回滚成功后
      服务端经 ``audit.build_event`` 写一条锚定事件（journal id + 摘要 + 回滚记录 id +
      目标 id + 两阶段相位），持久链落盘形态 = 原生事件名 ``rollback.anchored``
      （R1-4 收口：PM-VERDICT-R1-3 §3.1 批准扩 ``audit.EVENTS`` 封闭集，
      ``extra`` 键照旧落盘进 ``fields`` 桶；``fields.rollback_event`` 标记继续写，
      于是 R1-3 时代的历史行（``note`` + 标记）不改写也照同一条过滤条件可查），
      ``audit.verify()`` 照常通过；幂等重放不追加第二条锚；
  10. **回滚记录风险继承**（R1-3 / D-risk）：``_build_rollback_record`` 的
      ``risk_level`` 继承目标记录（high/critical 继承后 ``verify_record`` 如实报
      ``SECRETARIAT_APPROVAL_REQUIRED`` 属预期：签名有效、链连续）；越集/缺失回落
      ``"medium"``；
  11. **docs 门禁转绿**（R1-3 / R1-2b 遗留①）：docs/03 §6.5 补 10 行后
      ``doc_crosscheck`` 的 ``in_sync`` / ``contract_only_paths`` / ``doc_only_paths`` /
      ``verb_drift`` 全部干净。

临时目录一律走 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）：``features_root`` 也在
scratch 里，撤销日志绝不落进仓库 ``var/``。
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import sys
import unittest
import urllib.error
import urllib.request
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试

import gen_api_contract as gc  # noqa: E402  契约抽取器（路由与权限位都是从 AST 读的）
from agent_explainer import audit as au  # noqa: E402  R1-3：锚定事件复算走 AuditLog.verify
from agent_explainer import feature_store as fs  # noqa: E402  canonical sha256 独立复算
from agent_explainer import rollback as rb  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
TARGET = "act-r12b-target"
CHILD = "act-r12b-child"
IRREVERSIBLE = "act-r12b-irr"
DECLARED = "act-r12b-declared"
DECLARED_IRR = "act-r12b-declared-irr"
ROLLBACK_STATE_ENUM = {"none", "rolled_back", "rollback_blocked"}


def native(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in NATIVE if r["id"] == action_id))


def base_row(action_id: str, *, parent: str | None, stamp: str) -> dict:
    """一条可回滚的种子记录：工具 ``notes.write``（注册表子串兜底 = ``undo_note``）、
    已执行（``succeeded``）、来源未标注不可逆。"""
    row = native("act-demo-0007")
    row["id"] = action_id
    row["status"] = "succeeded"
    row["timestamp"] = stamp
    # schema 的 required 里 parent_action_id / user_intervention 必须在案（可为 null）
    row["parent_action_id"] = parent
    row["user_intervention"] = None
    return row


def seed(*, with_child: bool = True, with_irreversible: bool = False) -> list[dict]:
    rows = [base_row(TARGET, parent=None, stamp="2026-11-01T10:00:00Z")]
    if with_child:
        rows.append(base_row(CHILD, parent=TARGET, stamp="2026-11-01T10:05:00Z"))
    if with_irreversible:
        row = base_row(IRREVERSIBLE, parent=TARGET, stamp="2026-11-01T10:06:00Z")
        # 演示数据里"永久删除、不进回收站"的那份影响面：来源明说 reversible=false（W-03）
        row["impact_scope"] = native("act-demo-0005")["impact_scope"]
        rows.append(row)
    return rows


def declared_seed() -> list[dict]:
    """一条**来源自己声明** ``rollback_state="none"`` 的记录（S0-2b 的 5.0 扩展字段口径），
    它有一个撤不掉的依赖成员：产品判定是 blocked，来源声明是 none —— 谁说了算就看口径。"""
    rows = seed(with_child=False)
    declared = base_row(DECLARED, parent=None, stamp="2026-11-01T10:07:00Z")
    declared["rollback_state"] = "none"
    rows.append(declared)
    irr = base_row(DECLARED_IRR, parent=DECLARED, stamp="2026-11-01T10:08:00Z")
    irr["impact_scope"] = native("act-demo-0005")["impact_scope"]
    rows.append(irr)
    return rows


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
            exc.close()          # 4xx/5xx 是这里的正常断言路径，别让 ResourceWarning 刷屏
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


class RollbackHttpCase(unittest.TestCase):
    """产品口径的持久化服务器：records.jsonl + 撤销日志 + 入库即签名（R4）。

    这里只放公共夹具与读取辅助；用例分在两类：默认种子（依赖都可逆）与
    ``IrreversibleMemberTests`` 的种子（含一个撤不掉的成员），两批互不污染。
    """

    seed_rows: list[dict] | None = None

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-api")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store_path = self.root / "records.jsonl"
        self.features_root = self.root / "features"
        self.server, self.thread = self.start_server()
        self.base = self.server.url()
        self.journal_path = sv.FeaturePaths(self.features_root).rollback_journal

    def start_server(self, *, records: list[dict] | None = None):
        return sv.start_background_server(
            "127.0.0.1", 0,
            records=copy.deepcopy(records if records is not None
                                   else (self.seed_rows if self.seed_rows is not None
                                         else seed())),
            quiet=True,
            records_store_path=self.store_path,
            features_root=self.features_root,
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    # -- 读侧辅助 ---------------------------------------------------------
    def payload(self) -> dict:
        status, _hd, data = call(self.base, "/api/records")
        self.assertEqual(200, status, data)
        return data

    def view(self) -> list[dict]:
        return self.payload()["records"]

    def meta(self) -> dict:
        return self.payload()["meta"]

    def view_of(self, action_id: str) -> dict:
        for row in self.view():
            if row.get("id") == action_id:
                return row
        raise AssertionError(f"视图里找不到目标记录 {action_id}")

    def stored(self, action_id: str) -> dict:
        """``GET /api/records/<id>``：这条路径读的是存储本体（不经读时组装）。"""
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

    def disk_of(self, action_id: str) -> dict:
        rows = [r for r in self.disk_rows() if r.get("id") == action_id]
        self.assertEqual(1, len(rows), f"磁盘上 {action_id} 应当只有一行 put：{len(rows)}")
        return rows[0]

    def journal_entries(self) -> list[dict]:
        """撤销日志的落盘条目（拆掉存储的信封，只看事实本体）。"""
        if not self.journal_path.is_file():
            return []
        rows: list[dict] = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            item = entry.get("item")
            rows.append(item if isinstance(item, dict) else entry)
        return rows

    def block_journal(self) -> None:
        """把日志文件位占成目录：读一定失败（TP-26 的取证方式）。"""
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.mkdir()

    # -- 写侧辅助 ---------------------------------------------------------
    def rollback(self, action_id: str, **extra) -> tuple[int, dict, dict]:
        body: dict = {"action_id": action_id}
        body.update(extra)
        return call(self.base, "/api/rollback", body, "POST")

    def plan(self, action_id: str) -> tuple[int, dict, dict]:
        return call(self.base, f"/api/rollback/plan?action_id={action_id}")

    def verify(self, record: dict) -> dict:
        status, _hd, data = call(self.base, "/api/secretariat/verify",
                                 {"record": record}, "POST")
        self.assertEqual(200, status, data)
        return data["results"][0]

    def assert_error_shape(self, status: int, headers: dict, data: dict, code: str,
                           expect_status: int) -> None:
        """统一错误体（与 /intervene 同形状）：恰好四个键 + ``x-request-id`` 响应头。"""
        self.assertEqual(expect_status, status, data)
        self.assertEqual(code, data.get("code"), data)
        self.assertEqual(["code", "detail", "message", "request_id"], sorted(data))
        self.assertTrue(headers.get("x-request-id"), "错误体必须带 request_id 响应头")

    # -- 通用：种子与派生字段过 schema -------------------------------------
    def test_rows_are_schema_legal_signed_and_unsigned(self) -> None:
        """种子与视图行都过 schema v1.1：派生字段不许把记录改成非法形状。"""
        for row in self.view():
            self.assertEqual([], schema_errors(row), f"{row.get('id')}：{schema_errors(row)}")


class RollbackApiTests(RollbackHttpCase):
    """默认种子：一条可回滚目标 + 一条依赖它的可逆成员。"""

    # -- 1. 挂载 + 签名落盘（G-1 / G-2）------------------------------------
    def test_post_rollback_is_mounted_and_persists_a_signed_record(self) -> None:
        status, headers, data = self.rollback(TARGET, confirm_all=True,
                                              reason="演示：撤销那次写笔记")
        self.assertEqual(200, status, data)                       # 挂载前这句必 404
        self.assertEqual("false", headers.get("x-rollback-replay"))
        record = data["rollback_record"]
        self.assertTrue(headers.get("x-rollback-record-id"), headers)
        self.assertEqual(record["id"], headers.get("x-rollback-record-id"))
        self.assertTrue(headers.get("x-rollback-journal-id"), headers)
        self.assertEqual(data["journal_id"], headers.get("x-rollback-journal-id"))
        self.assertEqual("true", headers.get("x-rollback-signed"),
                         "sign_on_write=True 时响应头要如实说这条记录签了名")
        self.assertTrue(data["persisted"], "回滚记录必须真的落盘，不能只回一个字典")
        self.assertEqual(1, self.server.service.rollback_executions)
        self.assertEqual(1, self.server.service.rollback_records)
        self.assertEqual(0, self.server.service.sign_failures)
        # 补偿记录自己的契约形状（rollback.py 的口径 α 产物）
        self.assertEqual("rollback", record["action_type"])
        self.assertEqual(TARGET, record["rollback_ref"])
        self.assertEqual(TARGET, record["parent_action_id"])
        self.assertEqual("unreported",
                         (record.get("execution_receipt") or {}).get("state"),
                         "外部系统没有回执：execution_receipt.state 恒为 unreported")
        self.assertEqual("simulated_local", (data.get("inverse") or {}).get("receipt_state"),
                         "本地模拟：回执状态如实写 simulated_local，不谎称已恢复")
        self.assertTrue(record.get("signature"), "sign_on_write=True 时必须带签名")
        # 落盘 + journal 的 rollback_record_id 不再悬空（G-2 的正题）
        saved = self.disk_of(record["id"])
        self.assertEqual(saved.get("signature"), record.get("signature"),
                         "响应里的签名必须就是磁盘上那一条")
        entries = self.journal_entries()
        self.assertEqual(1, len(entries), "一次回滚恰好一条撤销日志")
        self.assertEqual(record["id"], entries[0]["rollback_record_id"])
        self.assertEqual(TARGET, entries[0]["action_id"])
        self.assertIsNotNone(self.stored(record["id"]).get("entry_hash"),
                             "journal 指着的这条记录必须在 store 里查得到")
        first = self.verify(saved)
        self.assertTrue(first["ok"], first["problems"])
        self.assertEqual("valid", first["checks"]["signature"])
        self.assertEqual("valid", first["checks"]["entry_hash"])
        self.assertIn(first["checks"]["prev_hash"], ("continuous", "genesis"))
        self.assertEqual([], schema_errors(saved),
                         f"回滚记录必须过 schema v1.1：{schema_errors(saved)}")

    def test_rollback_zero_rewrites_the_target_and_members(self) -> None:
        """口径 α：目标与闭包成员零改写 —— 磁盘字节不变，响应里的副本不落盘。"""
        before_target = self.disk_of(TARGET)
        before_child = self.disk_of(CHILD)
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.assertEqual(2, len(data["affected"]), "闭包 = 目标 + 依赖成员")
        self.assertEqual(before_target, self.disk_of(TARGET),
                         "目标记录一个字都不许改写（写一个字段 entry_hash 就复算失效）")
        self.assertEqual(before_child, self.disk_of(CHILD), "闭包成员同样零改写")
        for row in self.disk_rows():
            self.assertNotIn("rollback_state", row,
                             f"{row.get('id')}：已回滚是读时派生，绝不写回历史行")
            self.assertNotIn("next_ref", row)

    # -- 2. 白名单与授权（G-31）--------------------------------------------
    def test_forged_fields_are_rejected_with_zero_writes(self) -> None:
        forged = {
            "rollback_state": "rolled_back",
            "rollback_ref": CHILD,
            "signature": "ZGVhZGJlZWY=",
            "entry_hash": "0" * 64,
            "actor": "attacker",
            "max_depth": 99,
            "records": [{"id": TARGET}],
        }
        rows_before = len(self.disk_rows())
        for field, value in forged.items():
            status, headers, data = self.rollback(TARGET, **{field: value})
            self.assertEqual(400, status, f"{field} 必须被入参闸门拒：{data}")
            self.assert_error_shape(status, headers, data, "ROLLBACK_FIELD_INVALID", 400)
            self.assertEqual(field, (data.get("detail") or {}).get("field"), data)
            self.assertEqual(list(sv.ALLOWED_ROLLBACK_FIELDS),
                             (data.get("detail") or {}).get("allowed"),
                             "detail 必须把白名单原样说清楚，界面才知道能传什么")
        self.assertEqual(rows_before, len(self.disk_rows()), "被拒的入参零写入")
        self.assertEqual([], self.journal_entries(), "被拒的入参不许留下撤销日志")
        self.assertEqual(0, self.server.service.rollback_executions)

    def test_missing_action_id_is_400(self) -> None:
        status, headers, data = call(self.base, "/api/rollback", {"reason": "没有 id"}, "POST")
        self.assert_error_shape(status, headers, data, "ROLLBACK_FIELD_INVALID", 400)
        self.assertEqual([], self.journal_entries())

    def test_unknown_action_id_is_404_not_500(self) -> None:
        status, headers, data = self.rollback("act-r12b-not-there")
        self.assert_error_shape(status, headers, data, "ROLLBACK_NOT_FOUND", 404)

    def test_get_rollback_is_405(self) -> None:
        status, headers, data = call(self.base, "/api/rollback")
        self.assert_error_shape(status, headers, data, "METHOD_NOT_ALLOWED", 405)

    # -- 3. 幂等键（口径同 /intervene）------------------------------------
    def test_idempotency_key_replays_without_a_second_execution(self) -> None:
        key = "r12b-idem-1"
        status, headers, data = self.rollback(TARGET, confirm_all=True, idempotency_key=key)
        self.assertEqual(200, status, data)
        self.assertEqual("false", headers.get("x-rollback-replay"))
        first_rows = self.disk_rows()
        first_entries = self.journal_entries()
        first_record = data["rollback_record"]

        status, headers, data = self.rollback(TARGET, confirm_all=True, idempotency_key=key)
        self.assertEqual(200, status, data)
        self.assertEqual("true", headers.get("x-rollback-replay"), "同 key 必须命中 replay")
        self.assertEqual(first_record["id"], data["rollback_record"]["id"],
                         "重放必须回到第一次那条补偿记录")
        self.assertEqual(first_record.get("signature"),
                         data["rollback_record"].get("signature"),
                         "重放绝不重签：签名材料一字不变")
        self.assertEqual(first_rows, self.disk_rows(), "重放绝不再追加任何记录")
        self.assertEqual(first_entries, self.journal_entries(), "重放绝不再追加撤销日志")
        self.assertEqual(1, self.server.service.rollback_executions)
        self.assertEqual(1, self.server.service.rollback_records)

    def test_idempotency_key_for_another_action_is_409(self) -> None:
        key = "r12b-idem-2"
        status, _hd, data = self.rollback(TARGET, confirm_all=True, idempotency_key=key)
        self.assertEqual(200, status, data)
        status, headers, data = self.rollback(CHILD, idempotency_key=key)
        self.assert_error_shape(status, headers, data, "ROLLBACK_IDEMPOTENCY_MISMATCH", 409)
        self.assertEqual(TARGET, (data.get("detail") or {}).get("used_for"), data)
        self.assertEqual(1, len(self.journal_entries()), "错用的 key 零写入")

    # -- 4. 计划侧幂等屏障（G-6 / TP-20）----------------------------------
    def test_plan_and_execute_share_the_same_idempotency_barrier(self) -> None:
        status, _hd, data = self.plan(TARGET)
        self.assertEqual(200, status, data)
        self.assertEqual([], (data["plan"] or {}).get("blockers") or [])
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)

        status, _hd, data = self.plan(TARGET)      # 计划层也必须认出"已回滚"（G-6）
        self.assertEqual(200, status, data)
        codes = [b["code"] for b in (data["plan"] or {}).get("blockers") or []]
        self.assertIn(rb.BLOCKER_ALREADY_ROLLED_BACK, codes, data)
        self.assertFalse(data["plan"]["ok"], "已回滚的计划必须 ok=false，界面据此收起按钮")
        status, headers, data = self.rollback(TARGET)   # 执行层：同一个码，不是别的
        self.assert_error_shape(status, headers, data, rb.BLOCKER_ALREADY_ROLLED_BACK, 409)

    def test_plan_endpoint_reads_the_journal(self) -> None:
        """G-6 最后一公里：plan 走服务层，读得到撤销日志（模块级纯函数读不到）。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        status, _hd, data = self.plan(TARGET)
        self.assertEqual(200, status, data)
        plan = data["plan"]
        self.assertEqual(rb.BLOCKER_ALREADY_ROLLED_BACK, plan["blockers"][0]["code"], plan)
        self.assertEqual("journal", plan["blockers"][0].get("source"),
                         "屏障来源必须写清是撤销日志在案（journal），不是记录字段")

    def test_plan_reports_target_without_a_journal_entry_as_rollbackable(self) -> None:
        """反向证据：日志里没有这笔时，计划说"可以回滚"（屏障不是一刀切拒绝）。"""
        status, _hd, data = self.plan(TARGET)
        self.assertEqual(200, status, data)
        self.assertTrue(data["plan"]["ok"], data)
        self.assertEqual([], data["plan"]["skipped_already_rolled_back"], data)

    # -- 5. 依赖确认（TP-19）----------------------------------------------
    def test_dependents_without_confirm_all_is_409_pending(self) -> None:
        status, headers, data = self.rollback(TARGET)     # 种子带一个可逆依赖
        self.assert_error_shape(status, headers, data, "ROLLBACK_DEPENDENTS_PENDING", 409)
        self.assertEqual([CHILD], (data.get("detail") or {}).get("plan", {}).get("dependents"),
                         "409 必须把依赖列表带出来，界面才有的可展示")
        self.assertEqual([], self.journal_entries(), "未确认的回滚零落盘")

    def test_rollback_blocked_view_has_no_producer_for_clean_targets(self) -> None:
        """干净目标（依赖都可逆）不该被标成 blocked：blocked 只由真实 blocker 产生。"""
        view = self.meta()["rollback_view"]
        self.assertEqual([], view["blocked"], view)
        for row in self.view():
            self.assertNotIn("rollback_state", row, f"{row.get('id')} 无 blocker 就不该有状态")

    # -- 6. 撤销日志读不动（TP-26 / G-16）---------------------------------
    def test_journal_unreadable_is_503_on_plan_and_execute(self) -> None:
        self.block_journal()

        status, headers, data = self.plan(TARGET)
        self.assert_error_shape(status, headers, data, rb.CODE_JOURNAL_UNREADABLE, 503)
        self.assertNotEqual("FEATURE_STORE_READ_FAILED", data.get("code"),
                            "TP-26：对外契约码必须是 ROLLBACK_*，内部码只进 detail 供取证")

        status, headers, data = self.rollback(TARGET, confirm_all=True)
        self.assert_error_shape(status, headers, data, rb.CODE_JOURNAL_UNREADABLE, 503)
        self.assertEqual(0, self.server.service.rollback_executions,
                         "读不动时不得执行回滚（rollback.py 的界面纪律）")
        self.assertFalse(self.disk_of(TARGET).get("signature"),
                         "读不动时连签名都不发生过：磁盘还是种子原样")

    def test_journal_unreadable_does_not_derive_rolled_back(self) -> None:
        self.block_journal()
        view = self.meta()["rollback_view"]
        self.assertFalse(view["journal"]["ok"], view)
        self.assertEqual(rb.CODE_JOURNAL_UNREADABLE, view["journal"]["code"], view)
        self.assertEqual([], view["rolled_back"], "读不动时不派生『已回滚』")
        self.assertEqual([], view["blocked"], "读不动时也不派生『被阻断』")
        for row in self.view():
            self.assertNotIn("rollback_state", row, f"{row.get('id')}：读不动时不显示状态")

    def test_reading_records_does_not_create_feature_dirs(self) -> None:
        """读侧纪律：GET /api/records 绝不在数据根里造出 features/rollback。"""
        self.meta()
        self.meta()
        self.assertFalse(self.journal_path.parent.exists(),
                         "没有日志文件就是『没有一笔回滚』，不是顺手建个目录")

    # -- 7. 读时派生视图（G-3 / G-20 / docs/07 §11.2 门禁）----------------
    def test_derived_view_rolls_back_target_and_members(self) -> None:
        before = self.meta()["rollback_view"]
        self.assertEqual([], before["links"], "没回滚时没有正向指针")
        self.assertEqual([], before["rolled_back"])
        self.assertTrue(before["journal"]["ok"], before)

        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        rb_id = data["rollback_record"]["id"]

        view = self.meta()["rollback_view"]
        self.assertTrue(view["derived_at_read_time"], view)
        self.assertIn(TARGET, view["rolled_back"])
        self.assertIn(CHILD, view["rolled_back"], "闭包成员也算已回滚（来源是撤销日志）")
        links = {row["action_id"]: row for row in view["links"]}
        self.assertEqual(rb_id, links[TARGET]["next_ref"],
                         "next_ref 由 rollback_ref 反查得出，指向那条回滚记录")
        self.assertEqual("rolled_back", self.view_of(TARGET)["rollback_state"])
        self.assertEqual("rolled_back", self.view_of(CHILD)["rollback_state"])
        for row in self.view():
            state = row.get("rollback_state")
            if state is not None:
                self.assertIn(state, ROLLBACK_STATE_ENUM, f"派生值必须是 schema 枚举：{state}")
            self.assertEqual([], schema_errors(row), f"{row.get('id')}：视图行必须过 schema")

    def test_derived_view_is_never_persisted(self) -> None:
        """门禁自查（docs/07 §11.2）：派生字段不落盘、不进存储本体、不进 schema。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.meta()
        for row in self.disk_rows():
            self.assertNotIn("next_ref", row)
            if row.get("id") in (TARGET, CHILD):
                self.assertNotIn("rollback_state", row)
            if str(row.get("action_type") or "") == "rollback":
                self.assertIn("rollback_ref", row, "回滚记录自己的 rollback_ref 是契约字段")
        for action_id in (TARGET, CHILD):
            stored = self.stored(action_id)
            self.assertNotIn("next_ref", stored)
            self.assertNotIn("rollback_state", stored,
                             "GET /api/records/<id> 读存储本体：派生字段一个都不许出现")

    def test_compensation_record_itself_gets_no_state(self) -> None:
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        row = self.view_of(data["rollback_record"]["id"])
        self.assertNotIn("rollback_state", row,
                         "补偿记录不进任何闭包（裁决 D-closure），所以它没有回滚状态")
        self.assertEqual([], [link for link in self.meta()["rollback_view"]["links"]
                              if link["action_id"] == row["id"]])

    def test_derived_view_survives_a_restart_from_the_journal(self) -> None:
        """派生视图的真值来自落盘事实：新进程（重读磁盘）仍然认这笔回滚。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        rb_id = data["rollback_record"]["id"]
        # 换一台服务器：同一份 records.jsonl + 同一份撤销日志（重启口径）
        self.stop_server()
        self.server, self.thread = self.start_server(records=None)
        self.base = self.server.url()
        view = self.meta()["rollback_view"]
        self.assertIn(TARGET, view["rolled_back"], "重启后必须还认这笔回滚")
        self.assertEqual(rb_id, {row["action_id"]: row["next_ref"]
                                 for row in view["links"]}[TARGET])
        self.assertEqual("rolled_back", self.view_of(TARGET)["rollback_state"])
        # 重启后二次回滚：计划与执行都说已回滚（屏障在日志里，不在内存里）
        status, headers, data = self.rollback(TARGET, confirm_all=True)
        self.assert_error_shape(status, headers, data, rb.BLOCKER_ALREADY_ROLLED_BACK, 409)

    def test_service_counters_report_persisted_records(self) -> None:
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.assertEqual(1, self.server.service.rollback_executions)
        self.assertEqual(1, self.server.service.rollback_records)
        self.assertEqual(0, self.server.service.sign_failures)


class IrreversibleMemberTests(RollbackHttpCase):
    """种子里有一个撤不掉的依赖成员（来源明说 reversible=false）：裁决 D-irrev 口径。"""

    def setUp(self) -> None:
        self.seed_rows = seed(with_child=True, with_irreversible=True)
        super().setUp()

    def test_irreversible_member_is_409_conflict_not_500(self) -> None:
        """G-19：这个码在 R1-2b 之前不在 EXPANSION_ERROR_TABLE 里 → 会漏成 500。"""
        status, headers, data = self.rollback(TARGET)
        self.assert_error_shape(status, headers, data, rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, 409)
        self.assertEqual([IRREVERSIBLE], (data.get("detail") or {}).get("members"), data)
        self.assertIn(IRREVERSIBLE, json.dumps(data, ensure_ascii=False),
                      "409 必须逐条点名撤不掉的成员，界面才有的可展示")
        self.assertEqual([], self.journal_entries(), "未确认的不可逆回滚零落盘")

    def test_irreversible_member_is_left_out_with_confirm_all(self) -> None:
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.assertIn(IRREVERSIBLE, data["blocked_members"], "撤不掉的成员必须逐条点名")
        self.assertNotIn(IRREVERSIBLE, data["affected"], "撤不掉的成员绝不进执行范围")
        self.assertIn(CHILD, data["affected"])
        entry = self.journal_entries()[0]
        self.assertNotIn(IRREVERSIBLE, entry["affected"],
                         "日志的 affected 也不许宣称覆盖从未被处理的成员（G-5 假闭环）")
        self.assertIn(IRREVERSIBLE, entry["blocked_members"])
        self.assertNotIn(IRREVERSIBLE, self.disk_rows(), "不可逆成员没有被改写：磁盘上还是种子")

    def test_derived_view_blocked_state_lists_codes_and_members(self) -> None:
        """G-20：``rollback_state="rollback_blocked"`` 这个枚举值终于有生产者了。"""
        view = self.meta()["rollback_view"]
        blocked = {row["action_id"]: row for row in view["blocked"]}
        self.assertIn(TARGET, blocked, view)
        self.assertIn(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, blocked[TARGET]["codes"], blocked)
        self.assertIn(IRREVERSIBLE, blocked[TARGET]["members"])
        self.assertEqual("rollback_blocked", self.view_of(TARGET)["rollback_state"])
        self.assertEqual("rollback_blocked", self.view_of(IRREVERSIBLE)["rollback_state"])
        self.assertEqual([], view["rolled_back"])

    def test_target_with_irreversible_member_is_not_rolled_back_by_confirm_all(self) -> None:
        """确认只回滚可逆部分：目标算已回滚，不可逆成员停在 blocked，不算被撤销。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        view = self.meta()["rollback_view"]
        self.assertIn(TARGET, view["rolled_back"])
        self.assertIn(CHILD, view["rolled_back"])
        self.assertNotIn(IRREVERSIBLE, view["rolled_back"])
        self.assertEqual("rollback_blocked", self.view_of(IRREVERSIBLE)["rollback_state"])


class SourceDeclaredStateTests(RollbackHttpCase):
    """W-03 同口径（R1-2b）：来源自己声明的 ``rollback_state`` 以来源为准，派生只填空位。

    起因是 S0-2b 的端到端门禁：导入面承诺"5.0 扩展字段逐字送达"，读时派生不许把导入方
    写的 ``"none"`` 改写成产品判定；真值（谁被阻断）在 ``meta.rollback_view.blocked`` 里，
    界面两条都不缺。
    """

    def setUp(self) -> None:
        self.seed_rows = declared_seed()
        super().setUp()

    def test_source_declared_state_is_not_overwritten(self) -> None:
        row = self.view_of(DECLARED)
        self.assertEqual("none", row["rollback_state"],
                         "来源声明的 rollback_state 不许被派生改写（导入面逐字送达的承诺）")
        view = self.meta()["rollback_view"]
        self.assertIn(DECLARED, view["kept_declared"], view)
        blocked = {item["action_id"]: item for item in view["blocked"]}
        self.assertIn(DECLARED, blocked, "产品判定照常进 meta：真值不因为让位就消失")
        self.assertIn(rb.BLOCKER_DEPENDENTS_IRREVERSIBLE, blocked[DECLARED]["codes"], blocked)
        self.assertEqual("rollback_blocked", self.view_of(DECLARED_IRR)["rollback_state"],
                         "没有来源声明的行照常派生")
        self.assertNotIn(DECLARED, view["rolled_back"])

    def test_declared_value_is_still_schema_legal(self) -> None:
        for row in self.view():
            self.assertEqual([], schema_errors(row), f"{row.get('id')}：{schema_errors(row)}")
        self.stored(DECLARED)   # 存储本体可读（派生没把字段写回记录）


class RollbackContractRouteTests(unittest.TestCase):
    """契约抽取层（TP-30 / G-27）：路由登记、权限位、声明即挂载、错误码表。"""

    def setUp(self) -> None:
        self.tree = gc.parse(gc.SERVER)
        self.consts = gc.module_constants(self.tree)
        self.routes, self.blocked, self.fallback = gc.routes_from_server(self.tree,
                                                                         self.consts)

    def route_of(self, path: str, verb: str) -> dict:
        for route in self.routes:
            if route["path"] == path and route["verb"] == verb:
                return route
        raise AssertionError(f"契约抽取里没有 {verb} {path}：这条路由没挂载")

    def test_post_rollback_route_is_permission_csrf(self) -> None:
        route = self.route_of("/api/rollback", "POST")
        self.assertEqual("permission", route["auth"], route)
        self.assertEqual(["intervene.decide"], route["abilities"],
                         "权限位必须与 /intervene 同一条矩阵能力")
        self.assertTrue(route["csrf"], "CSRF 双提交与 /intervene 同口径")
        intervene = self.route_of("/intervene", "POST")
        self.assertEqual(sorted(intervene["abilities"]), sorted(route["abilities"]),
                         "两条决定类路由的权限位必须一致")

    def test_rollback_paths_are_no_longer_declared_unmounted(self) -> None:
        paths = sorted({route["path"] for route in self.routes})
        self.assertIn("/api/rollback", paths)
        self.assertIn("/api/rollback/plan", paths)
        mounted = gc.mounted_constants(
            {name: value for name, value in self.consts.items()
             if name.startswith("API_PATH")}, paths)
        self.assertNotIn("API_PATH_ROLBACK=/api/rollback", mounted["unmounted"],
                         "G-27：声明了常量就必须有分发分支接住")
        self.assertNotIn("API_PATH_ROLLBACK_PLAN=/api/rollback/plan", mounted["unmounted"])

    def test_get_rollback_stays_explicit_405(self) -> None:
        self.assertIn("/api/rollback", self.blocked.get("GET", []),
                      "GET /api/rollback 继续显式 405：撤销是写操作，不是查询")
        self.assertTrue(any(r["path"] == "/api/rollback" and r["verb"] == "POST"
                            for r in self.routes),
                        "同一批抽取里 POST 必须是真路由（405 与挂载并存）")

    def test_new_rollback_codes_are_in_the_error_tables(self) -> None:
        codes = dict(sv.EXPANSION_ERROR_TABLE)
        self.assertEqual(409, codes[rb.BLOCKER_DEPENDENTS_IRREVERSIBLE][0], "TP-19：409")
        self.assertEqual(503, codes[rb.CODE_JOURNAL_UNREADABLE][0], "TP-26：503")
        self.assertEqual(409, codes["ROLLBACK_IDEMPOTENCY_MISMATCH"][0], "幂等口径同 /intervene")
        merged = dict(sv.ERROR_TABLE)
        self.assertEqual(503, merged[rb.CODE_JOURNAL_UNREADABLE][0],
                         "ERROR_TABLE 必须合并扩展表，_send_expansion_error 才翻得出 503")
        self.assertEqual(409, merged[rb.BLOCKER_DEPENDENTS_IRREVERSIBLE][0])
        self.assertTrue(str(codes[rb.BLOCKER_DEPENDENTS_IRREVERSIBLE][1]).strip(),
                        "状态码要配可翻译的默认文案")


# --------------------------------------------------------------------------- #
# R1-3：journal 锚定入审计链（G-13 / TP-31）+ 风险继承（D-risk）+ docs 门禁转绿
# --------------------------------------------------------------------------- #

class JournalAnchorTests(RollbackHttpCase):
    """R1-3（G-13 / TP-31）：journal 的 ``snapshot_sha256`` 锚定入审计链。

    钉住四件事：
      1. 撤销日志条目带 ``snapshot_sha256``（前态快照规范 JSON 的 sha256），
         新键只追加在末尾——既有键与顺序一字不动（铁律 2）；
      2. 回滚成功后审计链里恰有一条锚定事件：journal id + 摘要 + 回滚记录 id +
         目标 id 四要素互相咬合，摘要能从落盘快照用独立实现复算；
      3. 这条事件走 ``audit.build_event`` 标准通道：整链 ``audit.verify()`` 照旧通过；
      4. 幂等重放**不追加**第二条锚（一次回滚 = 一条日志 + 一条锚，重放零记账）。
    落盘形态（R1-4 起）：持久链事件 = 原生事件名 ``rollback.anchored``（PM-VERDICT-R1-3
    §3.1 批准把这个名字收进 ``audit.EVENTS``），``extra`` 键落盘进 ``fields`` 桶；
    ``fields.rollback_event="rollback.anchored"`` 标记继续写，让 R1-3 时代
    （``note`` + 标记）的历史行照同一个过滤条件可查。内存链（t8 预览，无枚举）
    从一开始就是真名事件。
    """

    def durable_rows(self) -> list[dict]:
        """落盘审计 JSONL 的行（载荷 dict，链字段平铺）。"""
        path = self.root / "audit.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def anchor_fields(row: dict) -> dict:
        """``build_event(extra=…)`` 的自定义键在落盘行里进 ``fields`` 桶
        （audit.py 写盘口径 ``extra → payload["fields"]``，4.3 auth 域的
        ``fields.auth_event`` 同一形状）；链字段与原生字段仍平铺。"""
        fields = row.get("fields")
        return dict(fields) if isinstance(fields, Mapping) else {}

    def anchors(self) -> list[dict]:
        return [row for row in self.durable_rows()
                if self.anchor_fields(row).get("rollback_event") == "rollback.anchored"]

    def test_journal_entry_key_order_stays_append_only(self) -> None:
        """R1-3 钉的 19 键 + R1-4 追加的 4 键：既有键与顺序一字不动，新键只追加在末尾。

        原名 ``test_journal_entry_carries_snapshot_sha256_as_last_key``；R1-4 之后
        ``snapshot_sha256`` 不再是最后一个键（后面还有 ``snapshots``/``phase``/
        ``pending_id``/``pending_seq``），名字随之改成它真正钉住的东西。
        """
        status, _hd, data = self.rollback(TARGET, confirm_all=True,
                                          reason="取证：锚定入链")
        self.assertEqual(200, status, data)
        entry = self.journal_entries()[0]
        expected = [
            "id", "action_id", "tool", "inverse_op", "snapshot", "created_at",
            "applied_at", "result", "actor", "reason", "affected",
            "rollback_record_id", "language", "max_depth", "blocked_members",
            "skipped_already_rolled_back", "truncated_members", "cycle_detected",
            "snapshot_sha256",
            # R1-4 追加（G-13 / G-15 / TP-06）：只加在末尾，既有 19 键与顺序一字不动
            # ——与 R1-3 同一口径的"纯增量"可机检形态（PM-VERDICT-R1-3 §3.1 批准的
            # 测试同步；老日志行没有这些键，读侧按缺省容忍）。
            "snapshots", "phase", "pending_id", "pending_seq",
        ]
        # 落盘行是规范 JSON（键排序），"新键在末尾"钉的是**构造顺序**：
        # 从源码里的 journal_entry 字面量抽键序——既有键与顺序一字不动、
        # 新键只追加在末尾（铁律 2 的可机检形态：R1-3 的 snapshot_sha256 之后
        # 只允许追加 R1-4 的四个取证键）。
        import inspect
        import re
        src = inspect.getsource(rb.RollbackService.execute)
        start = src.index("journal_entry = {")
        block = src[start:src.index("\n        }", start)]
        literal_keys = re.findall(r'^ {12}"([a-z0-9_]+)":', block, re.M)
        self.assertEqual(expected, literal_keys,
                         "journal 条目构造顺序：既有键不动，R1-4 的四个新键只追加在末尾")
        # R1-4（G-13）：落盘行比构造点多出**链字段** —— 它们是链化账本的写入端补的
        # （``seq`` / ``prev_hash`` / ``entry_hash``，与审计链同一口径），构造点管不到。
        # 键集合钉死为"构造键 + 链字段"：不许多也不许少。
        chain_fields = ["entry_hash", "prev_hash", "seq"]
        self.assertEqual(sorted(expected + chain_fields), sorted(entry.keys()),
                         "落盘 journal 条目的键集合 = 构造键 + 链字段（不许多也不许少）")
        self.assertEqual(1, entry["seq"], "首条链化行的 seq 从 1 起")
        self.assertRegex(str(entry["prev_hash"]), r"^[0-9a-f]{64}$")
        self.assertRegex(str(entry["entry_hash"]), r"^[0-9a-f]{64}$")
        self.assertRegex(entry["snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entry["id"], data["journal_id"])

    def test_snapshot_sha256_matches_independent_recomputation(self) -> None:
        """摘要 = 规范 JSON 的 sha256：独立复算，不信任生产端的自述。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        entry = self.journal_entries()[0]
        recomputed = hashlib.sha256(
            fs.canonical_json(entry["snapshot"]).encode("utf-8")).hexdigest()
        self.assertEqual(recomputed, entry["snapshot_sha256"])
        self.assertEqual(fs.hash_object(entry["snapshot"]), entry["snapshot_sha256"])
        # 快照就是目标的前态：与磁盘上的历史行逐字段相等（口径 α：零改写）
        self.assertEqual(self.disk_of(TARGET), entry["snapshot"])
        # 响应里的摘要与 journal 条目同值（日志是事实源，响应只是直读）
        self.assertEqual(entry["snapshot_sha256"], data["snapshot_sha256"])

    def test_anchor_event_lands_in_chain_and_verify_passes(self) -> None:
        status, _hd, data = self.rollback(TARGET, confirm_all=True,
                                          reason="取证：锚定入链")
        self.assertEqual(200, status, data)
        entry = self.journal_entries()[0]
        rows = self.anchors()
        self.assertEqual(1, len(rows), f"一次回滚恰好一条锚定事件：{len(rows)}")
        anchor = rows[0]
        self.assertEqual("rollback.anchored", anchor.get("event"),
                         "R1-4（PM-VERDICT-R1-3 §3.1 批准的 L1）：audit.EVENTS 已含原生事件名 "
                         "rollback.anchored，持久链不再借 note 表达；fields.rollback_event 标记"
                         "继续写（R1-3 历史行是 note + 标记，不改写、不迁移）")
        fields = self.anchor_fields(anchor)
        self.assertEqual("rollback.anchored", fields.get("rollback_event"))
        # fields 桶过 audit 的 PII 打码（id 里的长数字段被打成星，口径同 fields.auth_event）：
        # 期望值用同一个 redact_params 复算，不拿裸 id 硬比。
        self.assertEqual(au.redact_params(entry["id"]), fields.get("journal_id"),
                         "journal 条目 id 落审计 fields 桶（PII 口径复算后可反查）")
        self.assertEqual(entry["snapshot_sha256"], fields.get("snapshot_sha256"),
                         "64 位 hex 摘要不打码：取证必须能逐字对上快照复算")
        self.assertEqual(au.redact_params(data["rollback_record"]["id"]),
                         fields.get("rollback_record_id"))
        self.assertEqual(TARGET, anchor.get("action_id"),
                         "目标 id 走 build_event 原生字段：query(action_id=…) 直接反查")
        self.assertEqual("local", anchor.get("actor_id"),
                         "build_event 对 local 模式的 actor 统一写 local（4.3 auth 域落盘同口径）")
        self.assertEqual("local", anchor.get("actor_role"))
        # 标准通道：整链复算照旧通过（G-13 不许弄断既有 audit.verify()）
        report = au.open_log(self.root / "audit.jsonl").verify(raise_on_tamper=False)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(len(self.durable_rows()), int(report["count"]),
                         "verify 复算的条数必须与落盘行数一致（锚是文件里唯一/首批事件）")
        self.assertGreaterEqual(int(report["count"]), 1)
        # 内存链（t8 预览）用真名事件，自检/界面可直查
        preview = [row for row in self.server.service.audit.entries(limit=None)
                   if row.get("event") == "rollback.anchored"]
        self.assertEqual(1, len(preview))
        self.assertEqual(entry["snapshot_sha256"], preview[0]["snapshot_sha256"])
        # 链头自证：事件真的落盘（创世 → 非创世）
        head = au.open_log(self.root / "audit.jsonl").head()
        self.assertEqual(au.GENESIS_HASH, head["genesis"])
        self.assertNotEqual(au.GENESIS_HASH, head["head"])

    def test_durable_anchor_event_name_is_native_and_queryable(self) -> None:
        """R1-4（PM-VERDICT-R1-3 §3.1 批准的 L1）：持久链事件名不再借 ``note``。

        ``audit.EVENTS`` 加入 ``rollback.anchored`` 之后，落盘文件里的事件名与内存链
        同名，``query(event="rollback.anchored")`` 在持久链上直接查得到——R1-3 时代
        这个过滤条件会被 ``_clean_enum`` 降级成 ``note``，把整批便签一起捞出来。
        历史兼容：``fields.rollback_event`` 标记继续写，老取证的过滤条件照用。
        """
        status, _hd, data = self.rollback(TARGET, confirm_all=True, reason="原生事件名")
        self.assertEqual(200, status, data)
        entry = self.journal_entries()[0]
        rows = self.anchors()
        self.assertEqual(1, len(rows))
        self.assertEqual("rollback.anchored", rows[0].get("event"))
        self.assertEqual("rollback.anchored", self.anchor_fields(rows[0]).get("rollback_event"),
                         "fields 标记继续写：R1-3 的历史行（note + 标记）与新行同闸可查")
        self.assertEqual("succeeded", self.anchor_fields(rows[0]).get("journal_phase"),
                         "两阶段日志（G-15）：锚只落在终态之后，相位如实入链")
        hits = au.open_log(self.root / "audit.jsonl").query(event="rollback.anchored")
        self.assertEqual(1, len(hits), "按真名过滤必须只命中这条锚（不再被降级成 note）")
        self.assertEqual(entry["snapshot_sha256"],
                         self.anchor_fields(hits[0]).get("snapshot_sha256"))
        self.assertEqual(TARGET, hits[0].get("action_id"))
        report = au.open_log(self.root / "audit.jsonl").verify(raise_on_tamper=False)
        self.assertTrue(report["ok"], report["problems"])

    def test_journal_phases_surface_in_records_meta(self) -> None:
        """R1-4（G-15）：读侧事实块逐相位计数，并指出两阶段 WAL 在哪。"""
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        view = self.meta()["rollback_view"]
        self.assertEqual({"succeeded": 1, "failed": 0, "pending": 0},
                         view["journal"]["phases"], view)
        self.assertIn("journal.pending.jsonl", view["journal"]["pending_log"])
        self.assertIn(str(self.features_root), view["journal"]["pending_log"],
                      "WAL 必须与撤销日志同目录（features_root 内），绝不落进 var/ 别处")
        self.assertIn("failed 条目不参与", view["journal"]["note"])
        self.assertIn("G-15", view["note"], "派生口径写进 note：failed/pending 不派生已回滚")

    def test_idempotent_replay_appends_no_second_anchor(self) -> None:
        key = "r13-anchor-idem"
        status, _hd, data = self.rollback(TARGET, confirm_all=True, idempotency_key=key)
        self.assertEqual(200, status, data)
        self.assertEqual(1, len(self.anchors()))
        rows_before = self.durable_rows()
        status, headers, data = self.rollback(TARGET, confirm_all=True,
                                              idempotency_key=key)
        self.assertEqual(200, status, data)
        self.assertEqual("true", headers.get("x-rollback-replay"))
        self.assertEqual(rows_before, self.durable_rows(),
                         "重放对审计链零追加：一次回滚一条锚，重放不重复记账")
        self.assertEqual(1, len(self.journal_entries()))


class RiskInheritanceTests(RollbackHttpCase):
    """R1-3（D-risk / G-28）：回滚记录 ``risk_level`` 继承目标（docs/07 §11.8 口径）。

    三档：high 继承（1 人审批缺口如实上报）、critical 继承（2 人 + 盖章缺口）、
    越集/缺失回落 ``"medium"``（旧行为）。四眼纪律：签名有效、链连续，
    审批缺口只上报、不绕过——``SECRETARIAT_APPROVAL_REQUIRED`` 属预期。
    """

    def serve_with_risk(self, risk: object) -> None:
        """用"目标带指定风险"的种子重启服务器。

        注意夹具的持久化口径：``records_store_path`` 跨重启存在，直接重启会让
        第一台服务器写进日志的种子行胜出（4.9 的装载纪律），改过的行根本进不了
        store——所以这里换新 scratch 根，让 journal 用本用例的种子首建。
        """
        rows = seed(with_child=True)
        for row in rows:
            if row["id"] == TARGET:
                if risk is None:
                    row.pop("risk_level", None)
                else:
                    row["risk_level"] = str(risk)
        self.stop_server()
        shutil.rmtree(self.root, ignore_errors=True)
        self.root = scratch_dir("rollback-api-risk")
        self.store_path = self.root / "records.jsonl"
        self.features_root = self.root / "features"
        self.server, self.thread = self.start_server(records=rows)
        self.base = self.server.url()
        self.journal_path = sv.FeaturePaths(self.features_root).rollback_journal

    def test_high_risk_target_inherits_high(self) -> None:
        self.serve_with_risk("high")
        status, headers, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        record = data["rollback_record"]
        self.assertEqual("high", record["risk_level"],
                         "高危动作的撤销本身就是高危动作：继承目标风险，不再恒 medium")
        self.assertEqual("true", headers.get("x-rollback-signed"),
                         "allow_unapproved=True：Agent 自签照签，缺口留给 verify 报")
        self.assertEqual(1, data["signing"]["signed"])
        self.assertEqual(0, self.server.service.sign_failures)
        saved = self.disk_of(record["id"])
        self.assertEqual("high", saved["risk_level"], "继承必须落盘，不只是响应字段")
        self.assertEqual([], schema_errors(saved),
                         f"继承后的回滚记录仍须过 schema：{schema_errors(saved)}")
        result = self.verify(saved)
        codes = [p.get("code") for p in result["problems"]]
        self.assertIn("SECRETARIAT_APPROVAL_REQUIRED", codes,
                      "high 的 1 人审批缺口必须如实上报（RISK_POLICY 自然生效，属预期）")
        self.assertEqual("valid", result["checks"]["signature"],
                         "缺口只报不绕：签名有效、链连续（§11.8 纪律）")
        self.assertEqual("valid", result["checks"]["entry_hash"])
        self.assertIn(result["checks"]["prev_hash"], ("continuous", "genesis"))

    def test_critical_risk_target_inherits_critical(self) -> None:
        self.serve_with_risk("critical")
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        record = data["rollback_record"]
        self.assertEqual("critical", record["risk_level"])
        saved = self.disk_of(record["id"])
        self.assertEqual([], schema_errors(saved),
                         f"继承 critical 后 schema 联动必须满足（const 口径）："
                         f"{schema_errors(saved)}")
        self.assertTrue((saved.get("policy") or {}).get("requires_human_confirmation"),
                        "critical 继承 → policy.requires_human_confirmation 如实联动 True")
        result = self.verify(saved)
        codes = [p.get("code") for p in result["problems"]]
        self.assertIn("SECRETARIAT_APPROVAL_REQUIRED", codes,
                      "critical = 2 人 + 盖章：缺口如实上报，这是纪律不是缺陷")
        self.assertEqual("valid", result["checks"]["signature"])
        self.assertFalse(result["ok"], "缺审批的 critical 补偿记录 verify 必须 ok=false")
        self.assertEqual("unreported",
                         (record.get("execution_receipt") or {}).get("state"),
                         "审批面继承不碰回执口径：execution_receipt 一字未动")

    def test_missing_or_invalid_target_risk_falls_back_to_medium(self) -> None:
        self.serve_with_risk("mega-hazard")   # schema 枚举外的脏继承值
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.assertEqual("medium", data["rollback_record"]["risk_level"],
                         "越集继承值回落 medium：脏值不能进签名域/风险策略校验")
        self.serve_with_risk(None)   # 目标没写风险：同样回落 medium（旧行为逐字一致）
        status, _hd, data = self.rollback(TARGET, confirm_all=True)
        self.assertEqual(200, status, data)
        self.assertEqual("medium", data["rollback_record"]["risk_level"],
                         "目标没写风险也回落 medium（与旧行为逐字一致）")


class ContractGateGreenTests(unittest.TestCase):
    """R1-3（R1-2b 遗留①）：docs/03 §6.5 补 10 行 + 契约再生后的门禁绿断言。

    两条门禁本体在 ``test_secretariat_api.test_docs_03_section_6_5_is_in_sync`` 与
    ``test_import_persistence.test_cli_flags_docs_and_contract_all_agree``（都读盘上的
    ``docs/api_contract.json``）；这里在 rollback 套件内把"红归零"的判据再钉一份：
    10 条欠账路径必须同时出现在契约与文档两侧，且交叉核对全干净。
    """

    R13_PATHS = (
        "/api/analytics/dashboard", "/api/analytics/export",
        "/api/compliance/exports", "/api/groups", "/api/preferences",
        "/api/rollback", "/api/rollback/plan", "/api/shares",
        "/api/shares/permissions", "/api/suggestions",
    )

    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "docs" / "api_contract.json")
                                   .read_text(encoding="utf-8"))

    def test_docs_03_gate_reds_are_zeroed(self) -> None:
        cross = self.contract["doc_crosscheck"]
        self.assertEqual([], cross["contract_only_paths"],
                         "R1-2b 遗留①必须归零：契约里有 docs/03 §6.5 没写的路径")
        self.assertEqual([], cross["doc_only_paths"])
        self.assertEqual([], cross["verb_drift"])
        self.assertEqual([], cross["rows_not_aligned"])
        self.assertTrue(cross["in_sync"], cross)
        for path in self.R13_PATHS:
            self.assertIn(path, cross["contract_paths"], f"契约缺路由：{path}")
            self.assertIn(path, cross["doc_paths"], f"docs/03 §6.5 缺行：{path}")

    def test_doc_section_6_5_table_carries_the_ten_rows(self) -> None:
        docs = (ROOT / "docs" / "03-architecture.md").read_text(encoding="utf-8")
        rows = gc.doc_rows(docs)
        listed = {p for row in rows for p in row["paths"]}
        for path in self.R13_PATHS:
            self.assertIn(path, listed, f"§6.5 核对表缺行：{path}")
        for row in rows:
            if set(row["paths"]) & set(self.R13_PATHS):
                self.assertTrue(row["verdict"].startswith("✅"),
                                f"§6.5 的 {row['label']} 行结论列必须是 ✅："
                                f"{row['verdict']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
