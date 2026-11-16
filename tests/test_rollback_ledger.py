"""R1-4 验收测试：撤销日志链化 + 闭包成员前态快照 + 两阶段日志 + 回执契约 + 沙箱试点。

运行（全程离线，只用标准库 unittest；HTTP 不在本文件）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_rollback_ledger.py"

钉住的五件事（TASK-R1-4 §1 的第 1/2/3/4/6 条）：

  1. **journal 链化（G-13 收口）**：写路径从 ``FeatureStore.open_store`` 换到链化账本
     （``feature_store.Ledger`` 口径），落盘行带 ``seq`` / ``prev_hash`` / ``entry_hash``；
     ``journal_chain_verify()`` 逐条复算，改一行必须报 ``ledger_hash_mismatch``
     （篡改要报错、只报告不修，与审计链同一口径）。**向后兼容读**：R1-4 之前的
     ``{op, ts, item}`` 历史行没有任何链字段——照读、不迁移、不改写，链在历史行之后
     按**写入端当时的算法**重新锚定，混排的日志复算照样通过，历史行的目标照样被
     幂等屏障认账；
  2. **闭包成员前态快照（TP-06 收口）**：``execute()`` 为执行范围（目标 + 每个参与
     闭包的成员）逐条留前态快照，日志 ``snapshots`` 覆盖 ``affected`` 全员，每条摘要
     可独立复算；口径 α 不变——成员记录本体一个字节都不改写；
  3. **两阶段日志（G-15）**：先把 ``pending`` 前态写进旁路 WAL
     （``rollback/journal.pending.jsonl``），再执行逆向动作；成功落 ``phase=succeeded``
     终态、失败落 ``phase=failed`` 终态（带原错误码，不新增对外码名）。``failed`` 条目
     不产生回滚记录、不参与幂等屏障、不派生"已回滚"；两段用 ``journal_id`` /
     ``pending_id`` 双向咬合；
  4. **``receipt_state`` 契约（G-14）**：注入执行器的回执状态必须在
     ``{simulated_local, executed_sandbox, executed_registry, failed}`` 白名单里，
     越界拒收；自报 ``failed`` 按失败抛出；``note`` 与状态不一致（真实执行还写着
     「本地模拟」）拒收。回滚记录的文案跟着状态走（沙箱试点逐字带「沙箱试点」），
     ``execution_receipt.state`` 恒为 ``unreported``；
  5. **``restore_file`` 沙箱试点（D-1 第二份证据）**：服务级注入的真实执行器只在
     显式白名单沙箱根 ``.scratch/r14_sandbox/`` 内写文件，回执 ``executed_sandbox``，
     文案如实写「沙箱试点」；双证据 = 沙箱内文件的前后对照（改坏 → 撤销 → 复原）
     + 撤销日志与回滚记录里的回执/文案。越出沙箱根的写入必须被拒。取证落在
     ``.scratch/``，绝不写进产品树，也绝不宣称外部系统已被撤销。

临时目录一律走 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）；沙箱试点根按 PM 派单
指定放在 ``.scratch/r14_sandbox/``（取证区，不是产品树，也不是 ``var/``）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import sys
import unittest
from collections.abc import Callable, Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from scratch import scratch_dir  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）

from agent_explainer import feature_store as fs  # noqa: E402
from agent_explainer import rollback as rb  # noqa: E402
from jsonschema_mini import MiniValidator  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "schemas" / "action_record.schema.json")
                    .read_text(encoding="utf-8"))
VALIDATOR = MiniValidator(SCHEMA)
NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]

TARGET = "act-r14-target"
CHILD = "act-r14-child"
OTHER = "act-r14-other"
LEGACY = "act-r14-legacy"
#: PM 派单指定的沙箱试点根（取证区，不入库、不进产品树）
SANDBOX_ROOT = ROOT / ".scratch" / "r14_sandbox"


def native(action_id: str) -> dict:
    return copy.deepcopy(next(r for r in NATIVE if r["id"] == action_id))


def base_row(action_id: str, *, parent: str | None, stamp: str,
             tool: str = "notes.write") -> dict:
    """一条可回滚的种子记录：已执行（``succeeded``）、来源未标注不可逆。"""
    row = native("act-demo-0007")
    row["id"] = action_id
    row["status"] = "succeeded"
    row["timestamp"] = stamp
    row["parent_action_id"] = parent
    row["user_intervention"] = None
    row["tool_used"] = dict(row.get("tool_used") or {})
    row["tool_used"]["name"] = tool
    return row


def seed() -> list[dict]:
    """目标 + 一个可逆依赖成员 + 一条独立目标（做"链接着长"的第二笔）。"""
    return [base_row(TARGET, parent=None, stamp="2026-11-01T10:00:00Z"),
            base_row(CHILD, parent=TARGET, stamp="2026-11-01T10:05:00Z"),
            base_row(OTHER, parent=None, stamp="2026-11-01T10:10:00Z")]


def rows_with_legacy() -> list[dict]:
    return seed() + [base_row(LEGACY, parent=None, stamp="2026-11-01T09:00:00Z")]


def raw_items(path: pathlib.Path) -> list[dict]:
    """直读落盘行里的实体（不经服务层），链字段以磁盘上的字节为准。"""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(row.get("item", row))
    return out


def write_legacy_journal(path: pathlib.Path, items: list[dict]) -> None:
    """手写 R1-4 **之前**的撤销日志：``{op, ts, item}``，item 里没有任何链字段。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [fs.canonical_json({"op": "put", "ts": "2026-11-01T09:30:00Z", "item": item})
             for item in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256_canonical(value: object) -> str:
    """独立实现：规范 JSON 的 sha256（不信任生产端自述的摘要）。"""
    return hashlib.sha256(fs.canonical_json(value).encode("utf-8")).hexdigest()


def schema_errors(record: dict) -> list[str]:
    return [f"{e.keyword}: {e.message} @ {e.data_path}" for e in VALIDATOR.collect(record)]


def registry_applier(record: Mapping, inverse: Mapping, *, actor: str,
                     reason: str) -> dict:
    """注册表级"真实执行"的替身：如实回报 executed_registry。"""
    return {"receipt_state": rb.REGISTRY_RECEIPT_STATE,
            "note": rb.REGISTRY_EXECUTED_NOTE}


# --------------------------------------------------------------------------- #
# 1. journal 链化（G-13 收口 + 向后兼容读）
# --------------------------------------------------------------------------- #


class ChainedJournalTests(unittest.TestCase):
    """写路径链化、复算通过、篡改报错；历史行不迁移也能接上链。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-ledger-chain")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = fs.FeaturePaths(self.root / "features")
        self.svc = rb.RollbackService(self.paths)
        self.rows = rows_with_legacy()

    def test_execute_lands_one_chained_terminal_row(self) -> None:
        out = self.svc.execute(self.rows, TARGET, actor="local",
                               reason="链化自证", confirm_all=True)
        items = raw_items(self.paths.rollback_journal)
        self.assertEqual(1, len(items), "一次回滚恰好一条终态日志（两阶段不把终态写两遍）")
        item = items[0]
        self.assertEqual(1, item["seq"])
        self.assertEqual(fs.GENESIS_HASH, item["prev_hash"],
                         "首条链化行的链头必须是创世哈希")
        self.assertRegex(str(item["entry_hash"]), r"^[0-9a-f]{64}$")
        self.assertEqual(rb.PHASE_SUCCEEDED, item["phase"])
        # 摘要独立复算：与 Ledger.append 同算法（体去掉 entry_hash）
        body = {key: value for key, value in item.items() if key != "entry_hash"}
        self.assertEqual(fs.hash_object(body), item["entry_hash"])
        self.assertEqual(item["entry_hash"], out["journal_chain"]["entry_hash"],
                         "响应里回报的链字段必须与落盘字节同值")
        report = self.svc.journal_chain_verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual((1, 0), (report["chained"], report["legacy_tolerated"]))

    def test_second_rollback_continues_the_chain(self) -> None:
        first = self.svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        second = self.svc.execute(self.rows, OTHER, actor="local", confirm_all=True)
        items = raw_items(self.paths.rollback_journal)
        self.assertEqual([1, 2], [item["seq"] for item in items])
        self.assertEqual(items[0]["entry_hash"], items[1]["prev_hash"],
                         "第二条必须咬住第一条的链尾")
        self.assertEqual(second["journal_chain"]["prev_hash"],
                         first["journal_chain"]["entry_hash"])
        report = self.svc.journal_chain_verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual(2, report["count"])

    def test_tampering_a_landed_row_is_detected(self) -> None:
        self.svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        path = self.paths.rollback_journal
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["item"]["snapshot"]["result_summary"] = "伪造的前态"
        path.write_text("".join(fs.canonical_json(row) + "\n" for row in rows),
                        encoding="utf-8")
        report = self.svc.journal_chain_verify()
        self.assertFalse(report["ok"], "改了落盘行必须复算出链断，不许静默通过")
        self.assertIn("ledger_hash_mismatch", {p["code"] for p in report["problems"]})
        # 只报告，不修：文件字节保持被改后的样子
        self.assertIn("伪造的前态", path.read_text(encoding="utf-8"))

    def test_legacy_rows_are_read_without_migration_and_reanchor_the_chain(self) -> None:
        """R1-4 之前的历史行：照读、不迁移、不改写，链在其后按写入端算法重新锚定。"""
        legacy_item = {"id": "rbj-legacy-1", "action_id": LEGACY, "tool": "notes.write",
                       "inverse_op": "undo_note", "snapshot": {"id": LEGACY},
                       "created_at": "2026-11-01T09:30:00Z",
                       "result": {"receipt_state": "simulated_local"},
                       "affected": [LEGACY]}
        write_legacy_journal(self.paths.rollback_journal, [legacy_item])
        before_bytes = self.paths.rollback_journal.read_bytes()

        loaded = self.svc.journal()
        self.assertEqual(1, len(loaded), "历史行必须照常读出来（向后兼容读）")
        self.assertEqual(rb.PHASE_SUCCEEDED, rb.journal_phase(loaded[0]),
                         "历史行没有 phase 键 → 缺省按已落终态成功读")
        self.assertTrue(rb.journal_settled_success(loaded[0]))
        # 幂等屏障认历史行：plan 与 execute 同口径拒绝二次回滚
        plan = self.svc.plan(self.rows, LEGACY)
        codes = {item["code"] for item in plan["blockers"]}
        self.assertIn(rb.BLOCKER_ALREADY_ROLLED_BACK, codes)
        with self.assertRaises(rb.RollbackError) as ctx:
            self.svc.execute(self.rows, LEGACY, actor="local")
        self.assertEqual(rb.BLOCKER_ALREADY_ROLLED_BACK, ctx.exception.code)

        out = self.svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        self.assertEqual(before_bytes, self.paths.rollback_journal.read_bytes()
                         [:len(before_bytes)], "写新行只能追加，绝不重写历史行字节")
        items = raw_items(self.paths.rollback_journal)
        self.assertEqual(2, len(items))
        chained = items[1]
        self.assertEqual(2, chained["seq"],
                         "历史行不占 seq 但占位置：新行 seq = 历史行数 + 1")
        anchor = fs.hash_object({**legacy_item, "seq": 1})
        self.assertEqual(anchor, chained["prev_hash"],
                         "锚点必须按写入端对无链字段末行的算法复算（不改写历史行）")
        report = self.svc.journal_chain_verify()
        self.assertTrue(report["ok"], f"历史行 + 链化行混排必须复算通过：{report}")
        self.assertEqual((1, 1), (report["chained"], report["legacy_tolerated"]))
        self.assertEqual(2, report["count"])
        self.assertEqual(out["journal_chain"]["seq"], chained["seq"])

    def test_unreadable_journal_surfaces_as_rollback_code(self) -> None:
        """链化不改 G-16：读不动 → ``ROLLBACK_JOURNAL_UNREADABLE``，不逃逸存储码。"""
        self.paths.rollback_journal.parent.mkdir(parents=True, exist_ok=True)
        self.paths.rollback_journal.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(rb.RollbackError) as ctx:
            self.svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        self.assertEqual(rb.CODE_JOURNAL_UNREADABLE, ctx.exception.code)


# --------------------------------------------------------------------------- #
# 2. 闭包成员前态快照（TP-06 收口）
# --------------------------------------------------------------------------- #


class ClosureSnapshotTests(unittest.TestCase):
    """执行范围全员逐条前态快照；口径 α：成员记录一个字节都不改写。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-ledger-snapshots")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = fs.FeaturePaths(self.root / "features")
        self.svc = rb.RollbackService(self.paths)
        self.rows = seed()

    def test_snapshots_cover_every_affected_member(self) -> None:
        out = self.svc.execute(self.rows, TARGET, actor="local",
                               reason="TP-06 收口", confirm_all=True)
        entry = self.svc.journal()[-1]
        self.assertEqual([CHILD, TARGET], out["affected"],
                         "夹具本意：目标 + 一个参与闭包的成员（执行顺序深→浅，目标在末位）")
        self.assertEqual(list(out["affected"]), [row["id"] for row in entry["snapshots"]],
                         "snapshots 必须按执行范围逐条覆盖，一个成员都不许漏")
        by_id = {row["id"]: row for row in self.rows}
        for snap in entry["snapshots"]:
            self.assertEqual(by_id[snap["id"]], snap["snapshot"],
                             f"成员 {snap['id']} 的快照必须逐字段等于它回滚前的整条记录")
            self.assertEqual(snap["snapshot_sha256"], sha256_canonical(snap["snapshot"]),
                             "每条快照的摘要都要能独立复算，不只目标那条")
            self.assertEqual(snap["snapshot_sha256"], fs.hash_object(snap["snapshot"]))
        self.assertEqual(entry["snapshots"], out["snapshots"],
                         "响应里的快照与日志条目同源（日志是事实源）")
        self.assertEqual(by_id[TARGET], entry["snapshot"],
                         "目标快照与 snapshots 里目标那条同值（既有键语义不动）")
        self.assertEqual(entry["snapshot_sha256"], sha256_canonical(entry["snapshot"]),
                         "R1-3 的锚定摘要口径照旧成立")

    def test_member_records_are_never_rewritten(self) -> None:
        """α 口径：全员快照只是取证；入参记录与返回副本都逐字段等于原记录。"""
        by_id = {row["id"]: copy.deepcopy(row) for row in self.rows}
        out = self.svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        self.assertEqual({key: fs.canonical_json(row) for key, row in by_id.items()},
                         {row["id"]: fs.canonical_json(row) for row in self.rows},
                         "传入的记录不能被就地改写")
        self.assertEqual([by_id[row["id"]] for row in out["records"]], out["records"],
                         "返回的 records 必须逐字段等于原记录（零改写）")
        entry = self.svc.journal()[-1]
        for snap in entry["snapshots"]:
            self.assertNotIn("rollback_state", snap["snapshot"],
                             "前态快照里不许出现派生状态字段（读时派生，不落盘）")

    def test_single_target_closure_snapshots_one_row(self) -> None:
        out = self.svc.execute(self.rows, OTHER, actor="local")
        entry = self.svc.journal()[-1]
        self.assertEqual([OTHER], entry["affected"])
        self.assertEqual([OTHER], [row["id"] for row in entry["snapshots"]])

    def test_untraceable_member_is_flagged_not_silent(self) -> None:
        """索引里查不到的成员：如实标 missing，绝不静默少一份证据。"""
        snaps = rb._closure_snapshots({}, [CHILD])
        self.assertEqual(1, len(snaps))
        self.assertEqual(CHILD, snaps[0]["id"])
        self.assertIsNone(snaps[0]["snapshot"])
        self.assertEqual("missing", snaps[0]["snapshot_sha256"])
        self.assertIn("查不到", snaps[0]["note"])


# --------------------------------------------------------------------------- #
# 3. 两阶段日志（G-15）
# --------------------------------------------------------------------------- #


class TwoPhaseJournalTests(unittest.TestCase):
    """pending 先落 → 执行逆向 → 成功/失败都落终态；失败不留无记录的副作用。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-ledger-twophase")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = fs.FeaturePaths(self.root / "features")
        self.rows = seed()

    def pending_items(self) -> list[dict]:
        return raw_items(self.paths.rollback_dir / rb.JOURNAL_PENDING_FILE_NAME)

    def test_pending_row_lands_before_the_inverse_runs(self) -> None:
        seen: dict[str, object] = {}

        def applier(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
            svc = rb.RollbackService(self.paths)
            seen["pending"] = svc.journal_pending()
            seen["terminal"] = svc.journal()
            seen["path"] = svc.journal_pending_path
            return {"receipt_state": rb.REGISTRY_RECEIPT_STATE,
                    "note": rb.REGISTRY_EXECUTED_NOTE}

        svc = rb.RollbackService(self.paths, applier=applier)
        out = svc.execute(self.rows, TARGET, actor="local",
                          reason="两阶段自证", confirm_all=True)
        pending = seen["pending"]
        assert isinstance(pending, list)
        self.assertEqual(1, len(pending), "执行逆向之前，pending 前态必须已经落盘")
        self.assertEqual(rb.PHASE_PENDING, pending[0]["phase"])
        self.assertEqual(out["journal_id"], pending[0]["journal_id"],
                         "pending 行用 journal_id 指向将要落的终态行")
        self.assertEqual(out["affected"], [row["id"] for row in pending[0]["snapshots"]],
                         "第一段就带齐执行范围全员的前态（TP-06 + G-15）")
        terminal = seen["terminal"]
        assert isinstance(terminal, list)
        self.assertEqual([], terminal, "执行逆向之前终态日志必须还是空的（顺序自证）")
        # 两段双向咬合
        row = svc.journal()[-1]
        self.assertEqual(rb.PHASE_SUCCEEDED, row["phase"])
        self.assertEqual(pending[0]["id"], row["pending_id"])
        self.assertEqual(pending[0]["seq"], row["pending_seq"])
        self.assertEqual(pending[0]["id"], out["pending_journal_id"])
        self.assertEqual(1, len(self.pending_items()))
        self.assertEqual([], svc.unresolved_pending(), "正常收尾不该留下未解 pending")
        self.assertTrue(svc.journal_pending_chain_verify()["ok"],
                        "旁路 WAL 同样链化，逐条可复算")
        self.assertTrue(svc.journal_chain_verify()["ok"])

    def test_failed_inverse_lands_failed_entry_with_no_side_effect_record(self) -> None:
        def boom(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
            raise RuntimeError("试点执行器故意失败")

        svc = rb.RollbackService(self.paths, applier=boom)
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(self.rows, TARGET, actor="local",
                        reason="两阶段失败路径", confirm_all=True)
        self.assertEqual(rb.CODE_INVERSE_FAILED, ctx.exception.code)
        rows = svc.journal()
        self.assertEqual(1, len(rows), "失败也要留下一条终态日志，绝不静默")
        row = rows[-1]
        self.assertEqual(rb.PHASE_FAILED, row["phase"])
        self.assertEqual(rb.FAILED_RECEIPT_STATE, row["result"]["receipt_state"])
        self.assertIsNone(row["rollback_record_id"], "失败的逆向不产生回滚记录")
        self.assertEqual(rb.CODE_INVERSE_FAILED, row["failure"]["code"],
                         "可观测性：原错误码族原样入日志，不重命名")
        self.assertIn("故意失败", row["failure"]["reason"])
        self.assertEqual([CHILD, TARGET], [snap["id"] for snap in row["snapshots"]],
                         "失败条目同样带执行范围全员的前态（可复核当时打算做什么）")
        self.assertEqual(sha256_canonical(row["snapshot"]), row["snapshot_sha256"])
        # failed 不参与幂等屏障：目标仍可再回滚，且这次能成功
        good = rb.RollbackService(self.paths, applier=registry_applier)
        plan = good.plan(self.rows, TARGET)
        codes = {item["code"] for item in plan["blockers"]}
        self.assertNotIn(rb.BLOCKER_ALREADY_ROLLED_BACK, codes,
                         "failed 条目绝不能把目标记成已回滚")
        out = good.execute(self.rows, TARGET, actor="local", confirm_all=True)
        self.assertEqual(2, len(good.journal()))
        self.assertEqual(rb.PHASE_SUCCEEDED, good.journal()[-1]["phase"])
        self.assertEqual(rb.PHASE_FAILED, good.journal()[0]["phase"])
        self.assertEqual([], good.unresolved_pending())
        self.assertTrue(good.journal_chain_verify()["ok"],
                        f"failed + succeeded 混排的链必须复算通过：{out}")

    def test_applier_self_reported_failure_lands_failed_entry(self) -> None:
        def liar(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
            return {"receipt_state": rb.FAILED_RECEIPT_STATE,
                    "note": "沙箱根内写入被拒绝"}

        svc = rb.RollbackService(self.paths, applier=liar)
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(self.rows, OTHER, actor="local")
        self.assertEqual(rb.CODE_INVERSE_FAILED, ctx.exception.code)
        self.assertEqual("note", ctx.exception.field)
        row = svc.journal()[-1]
        self.assertEqual(rb.PHASE_FAILED, row["phase"])
        self.assertIn("沙箱根内写入被拒绝", row["failure"]["reason"])

    def test_pending_write_failure_blocks_execution(self) -> None:
        """第一段写不进去 → 绝不执行逆向（不留无记录的副作用），错误留在码族里。"""
        sentinel: list[str] = []

        def spy(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
            sentinel.append("executed")
            return {"receipt_state": rb.REGISTRY_RECEIPT_STATE,
                    "note": rb.REGISTRY_EXECUTED_NOTE}

        svc = rb.RollbackService(self.paths, applier=spy)
        # 把 WAL 路径占成目录：pending 写不进
        wal = svc.journal_pending_path
        wal.parent.mkdir(parents=True, exist_ok=True)
        wal.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute(self.rows, TARGET, actor="local", confirm_all=True)
        # 写不进 / 读不动都落在 rollback.* 码族里：WAL 被目录占住是读不动的一种
        self.assertIn(ctx.exception.code,
                      {rb.CODE_INVERSE_FAILED, rb.CODE_JOURNAL_UNREADABLE})
        self.assertEqual([], sentinel,
                         "pending 前态写不进去就不许执行逆向动作（G-15 的硬边界）")
        self.assertEqual([], svc.journal(), "第一段没落成就什么都不许留下")


# --------------------------------------------------------------------------- #
# 4. receipt_state 契约（G-14）
# --------------------------------------------------------------------------- #


class ReceiptContractTests(unittest.TestCase):
    """白名单 + note 一致性 + 记录文案跟着真实回执状态走。"""

    def setUp(self) -> None:
        self.root = scratch_dir("rollback-ledger-receipt")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = fs.FeaturePaths(self.root / "features")
        self.rows = seed()

    def run_with(self, receipt: Mapping | None) -> dict:
        def applier(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
            return dict(receipt or {})

        svc = rb.RollbackService(self.paths, applier=applier)
        return svc.execute(self.rows, OTHER, actor="local", reason="回执契约")

    def test_whitelist_is_the_documented_enum(self) -> None:
        self.assertEqual({"simulated_local", "executed_sandbox", "executed_registry",
                          "failed"}, set(rb.RECEIPT_STATES))
        self.assertEqual(rb.SIMULATED_RECEIPT_STATE, "simulated_local")
        self.assertEqual(rb.SANDBOX_RECEIPT_STATE, "executed_sandbox")
        self.assertEqual(rb.REGISTRY_RECEIPT_STATE, "executed_registry")
        self.assertEqual(rb.FAILED_RECEIPT_STATE, "failed")

    def test_out_of_range_receipt_state_is_rejected(self) -> None:
        with self.assertRaises(rb.RollbackError) as ctx:
            self.run_with({"receipt_state": "executed_external_system",
                           "note": "外部系统已撤销"})
        self.assertEqual(rb.CODE_INVERSE_FAILED, ctx.exception.code)
        self.assertEqual("receipt_state", ctx.exception.field)
        self.assertEqual(sorted(rb.RECEIPT_STATES),
                         sorted(ctx.exception.detail["allowed"]))
        svc = rb.RollbackService(self.paths)
        row = svc.journal()[-1]
        self.assertEqual(rb.PHASE_FAILED, row["phase"],
                         "越界回执绝不落成功终态")

    def test_real_execution_note_must_not_carry_simulation_wording(self) -> None:
        for note in ("本地模拟执行；外部系统未实际调用（离线降级）",
                     "simulated only, external systems not called",
                     "离线降级，未调用外部系统"):
            with self.subTest(note=note):
                with self.assertRaises(rb.RollbackError) as ctx:
                    self.run_with({"receipt_state": rb.SANDBOX_RECEIPT_STATE, "note": note})
                self.assertEqual(rb.CODE_INVERSE_FAILED, ctx.exception.code)
                self.assertEqual("note", ctx.exception.field)
                self.assertEqual(rb.SANDBOX_RECEIPT_STATE,
                                 ctx.exception.detail["receipt_state"])

    def test_simulated_note_without_simulation_wording_is_normalised(self) -> None:
        """模拟回执缺文案 → 按缺省文案兜住，绝不让它冒充真实执行。"""
        out = self.run_with({"receipt_state": rb.SIMULATED_RECEIPT_STATE,
                             "note": "已处理"})
        self.assertEqual(rb.SIMULATED_NOTE, out["inverse"]["note"])
        record = out["rollback_record"]
        self.assertIn("本地模拟", record["human_readable"]["l1"])
        self.assertIn("simulated-local", record["labels"])

    def test_sandbox_receipt_produces_honest_pilot_wording(self) -> None:
        out = self.run_with({"receipt_state": rb.SANDBOX_RECEIPT_STATE,
                             "note": "沙箱试点：已在白名单沙箱根内真实写入；外部生产系统未触碰",
                             "detail": {"sandbox_root": "E:/x/.scratch/r14_sandbox"}})
        record = out["rollback_record"]
        text = json.dumps(record, ensure_ascii=False)
        self.assertIn("沙箱试点", record["human_readable"]["l1"])
        self.assertIn("沙箱试点", record["execution_receipt"]["detail"])
        self.assertIn("executed_sandbox", text)
        self.assertNotIn("本地模拟", text, "真实执行不能顶着模拟文案（G-14 一致性）")
        self.assertNotIn("已恢复", text, "§10 禁令：不许宣称外部系统已撤销/已恢复")
        self.assertEqual("unreported", record["execution_receipt"]["state"],
                         "铁律 2：execution_receipt.state 恒为 unreported")
        self.assertIn("executed-sandbox", record["labels"])
        self.assertIn("sandbox-pilot", record["labels"])
        self.assertEqual([], schema_errors(record), "沙箱试点的补偿记录必须过 schema v1.1")

    def test_registry_receipt_wording(self) -> None:
        out = self.run_with({"receipt_state": rb.REGISTRY_RECEIPT_STATE})
        record = out["rollback_record"]
        self.assertEqual(rb.REGISTRY_EXECUTED_NOTE, out["inverse"]["note"],
                         "缺省文案按状态补齐，不空着")
        self.assertIn("真实执行", record["human_readable"]["l1"])
        self.assertNotIn("沙箱试点", json.dumps(record, ensure_ascii=False),
                         "注册表级执行器不是沙箱试点，不许蹭标签")
        self.assertEqual(["rollback", "executed-registry"], record["labels"])
        self.assertEqual("unreported", record["execution_receipt"]["state"])
        self.assertEqual([], schema_errors(record))

    def test_default_simulated_copy_stays_as_r1_3(self) -> None:
        """缺省路径（没注入执行器）的文案与 R1-3 逐字一致：界面零变化。"""
        svc = rb.RollbackService(self.paths)
        out = svc.execute(self.rows, OTHER, actor="local")
        record = out["rollback_record"]
        self.assertEqual(rb.SIMULATED_NOTE, out["inverse"]["note"])
        self.assertEqual("已回滚：notes.write（本地模拟）", record["human_readable"]["l1"])
        self.assertEqual("未调用外部系统，回执状态 simulated_local",
                         record["human_readable"]["l2"][2])
        self.assertEqual(["rollback", "simulated-local"], record["labels"])
        self.assertIn("本地逆向动作已记录（simulated_local）",
                      record["execution_receipt"]["detail"])
        self.assertEqual("unreported", record["execution_receipt"]["state"])
        self.assertEqual(rb.PHASE_SUCCEEDED, svc.journal()[-1]["phase"])


# --------------------------------------------------------------------------- #
# 5. restore_file 沙箱试点（D-1 第二份证据）
# --------------------------------------------------------------------------- #


def make_pilot_applier(root: pathlib.Path) -> Callable[..., dict]:
    """试点执行器：真实写文件，但**只允许**在白名单沙箱根 ``root`` 内写。

    这就是"沙箱试点"的全部含义：I/O 真的发生（写的是沙箱里的文件），
    外部生产系统一台都没碰；越出沙箱根的写入直接拒绝。执行器按幂等口径写：
    文件已是前态就不再写第二次。
    """
    allowed = root.resolve()

    def apply(record: Mapping, inverse: Mapping, *, actor: str,
              reason: str) -> dict:
        target = pathlib.Path(str(record.get("pilot_file") or "")).resolve()
        if target != allowed and allowed not in target.parents:
            raise rb.RollbackError(
                rb.CODE_INVERSE_FAILED,
                f"试点执行器拒绝在沙箱根之外写入：{target}",
                field="sandbox_root",
                detail={"sandbox_root": str(allowed), "target": str(target)},
                hint="沙箱试点只允许写白名单根内的文件；越界写入 = 真实副作用，必须拒绝",
            )
        before = str(record.get("pilot_before") or "")
        after = str(record.get("pilot_after") or "")
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == before:
            return {"receipt_state": rb.SANDBOX_RECEIPT_STATE,
                    "note": rb.SANDBOX_PILOT_NOTE,
                    "detail": {"sandbox_root": str(allowed), "path": str(target),
                               "idempotent": True,
                               "restored_sha256": hashlib.sha256(
                                   before.encode("utf-8")).hexdigest()}}
        if current != after:
            raise rb.RollbackError(
                rb.CODE_INVERSE_FAILED,
                "沙箱内的文件既不是前态也不是后态：拒绝覆盖未知状态",
                field="pilot_file",
                detail={"path": str(target), "sandbox_root": str(allowed)})
        target.write_text(before, encoding="utf-8")
        return {"receipt_state": rb.SANDBOX_RECEIPT_STATE,
                "note": rb.SANDBOX_PILOT_NOTE,
                "detail": {"sandbox_root": str(allowed), "path": str(target),
                           "restored_sha256": hashlib.sha256(
                               before.encode("utf-8")).hexdigest()}}

    return apply


def pilot_record(case_root: pathlib.Path, target_file: pathlib.Path, *,
                 before: str, after: str) -> dict:
    """试点夹具记录：工具 ``filesystem.remove``（兜底逆向 = ``restore_file``）。"""
    row = base_row("act-r14-pilot", parent=None, stamp="2026-11-01T11:00:00Z",
                   tool="filesystem.remove")
    row["impact_scope"] = dict(row.get("impact_scope") or {})
    row["impact_scope"]["reversible"] = True
    row["pilot_file"] = str(target_file)
    row["pilot_before"] = before
    row["pilot_after"] = after
    row["pilot_case"] = str(case_root)
    return row


class SandboxPilotTests(unittest.TestCase):
    """沙箱试点真实落盘 + 前后对照自证；越界写入被拒。取证落在 ``.scratch/``。"""

    case_name = "pilot-case"

    def setUp(self) -> None:
        SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        self.case_root = SANDBOX_ROOT / self.case_name
        shutil.rmtree(self.case_root, ignore_errors=True)
        self.case_root.mkdir(parents=True)
        self.features = fs.FeaturePaths(scratch_dir("rollback-ledger-pilot") / "features")

    def tearDown(self) -> None:
        # 取证文件留在 .scratch 里（派单要求），只清夹具本体不合适的东西：保留证据
        pass

    def test_restore_file_pilot_restores_the_file_inside_the_sandbox(self) -> None:
        target = self.case_root / "notes.txt"
        before, after = "沙箱前态：原始内容\n", "正向动作改坏后的内容\n"
        target.write_text(before, encoding="utf-8")
        # 正向动作把文件改坏（真实写入，就在沙箱根内）
        target.write_text(after, encoding="utf-8")
        self.assertEqual(after, target.read_text(encoding="utf-8"))

        record = pilot_record(self.case_root, target, before=before, after=after)
        svc = rb.RollbackService(self.features,
                                 applier=make_pilot_applier(SANDBOX_ROOT))
        out = svc.execute([record], record["id"], actor="local",
                          reason="restore_file 沙箱试点")

        # 证据 ①：沙箱内文件的前后对照（撤销后逐字节回到前态）
        restored = target.read_text(encoding="utf-8")
        self.assertEqual(before, restored, "沙箱试点必须真的把文件恢复成前态")
        self.assertNotEqual(after, restored)
        receipt = out["inverse"]
        self.assertEqual(rb.SANDBOX_RECEIPT_STATE, receipt["receipt_state"])
        self.assertIn("沙箱试点", receipt["note"])
        self.assertEqual(str(SANDBOX_ROOT.resolve()), receipt["detail"]["sandbox_root"])
        self.assertEqual(hashlib.sha256(before.encode("utf-8")).hexdigest(),
                         receipt["detail"]["restored_sha256"])

        # 证据 ②：撤销日志与回滚记录里的回执/文案
        entry = svc.journal()[-1]
        self.assertEqual(rb.PHASE_SUCCEEDED, entry["phase"])
        self.assertEqual(rb.SANDBOX_RECEIPT_STATE, entry["result"]["receipt_state"])
        self.assertIn("沙箱试点", entry["result"]["note"])
        self.assertNotIn("本地模拟", json.dumps(entry, ensure_ascii=False))
        self.assertEqual([record["id"]], [snap["id"] for snap in entry["snapshots"]])
        self.assertEqual(before, entry["snapshot"]["pilot_before"],
                         "前态快照里带着试点当时的文件内容，可复核")
        comp = out["rollback_record"]
        self.assertEqual("unreported", comp["execution_receipt"]["state"])
        self.assertIn("沙箱试点", comp["human_readable"]["l1"])
        self.assertIn("executed_sandbox", comp["execution_receipt"]["detail"])
        self.assertNotIn("已恢复", json.dumps(comp, ensure_ascii=False))
        self.assertEqual([], schema_errors(comp))

        # 幂等：同一目标再回滚 → 幂等屏障，文件保持前态（执行器幂等口径）
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute([record], record["id"], actor="local")
        self.assertEqual(rb.BLOCKER_ALREADY_ROLLED_BACK, ctx.exception.code)
        self.assertEqual(before, target.read_text(encoding="utf-8"))

        # 取证落 .scratch（不进产品树）
        evidence = {
            "case": self.case_name,
            "sandbox_root": str(SANDBOX_ROOT),
            "pilot_file": str(target),
            "sha_before": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            "sha_after_forward_action": hashlib.sha256(after.encode("utf-8")).hexdigest(),
            "sha_restored": hashlib.sha256(restored.encode("utf-8")).hexdigest(),
            "restored_equals_before": restored == before,
            "receipt": {key: value for key, value in receipt.items()},
            "journal_phase": entry["phase"],
            "journal_seq": entry.get("seq"),
            "journal_entry_hash": entry.get("entry_hash"),
            "compensation_record": {"id": comp["id"],
                                    "l1": comp["human_readable"]["l1"],
                                    "labels": comp["labels"],
                                    "receipt_state_field":
                                        comp["execution_receipt"]["state"]},
            "chain_verify": svc.journal_chain_verify(),
            "pending_rows": len(svc.journal_pending()),
            "unresolved_pending": len(svc.unresolved_pending()),
        }
        (self.case_root / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertTrue((self.case_root / "evidence.json").is_file())
        self.assertTrue(svc.journal_chain_verify()["ok"])

    def test_pilot_refuses_writes_outside_the_whitelist_root(self) -> None:
        outside = ROOT / "samples" / "pilot_should_never_touch.json"
        self.assertFalse(outside.exists(), "夹具前提：产品树里不存在这个文件")
        record = pilot_record(self.case_root, outside, before="A", after="B")
        svc = rb.RollbackService(self.features,
                                 applier=make_pilot_applier(SANDBOX_ROOT))
        with self.assertRaises(rb.RollbackError) as ctx:
            svc.execute([record], record["id"], actor="local", reason="越界自证")
        self.assertEqual(rb.CODE_INVERSE_FAILED, ctx.exception.code)
        self.assertEqual("sandbox_root", ctx.exception.field)
        self.assertFalse(outside.exists(), "越界写入必须被拒：产品树一个字节都不许多出来")
        row = svc.journal()[-1]
        self.assertEqual(rb.PHASE_FAILED, row["phase"],
                         "被拒的试点执行器同样要留下 failed 终态条目（G-15）")
        self.assertEqual("sandbox_root", row["failure"]["field"])
        self.assertEqual([], svc.unresolved_pending())

    def test_pilot_note_must_stay_honest(self) -> None:
        """试点文案的两条线都要守住：说清是沙箱试点，也不写「外部系统未实际调用」。"""
        (self.case_root / "notes.txt").write_text("B", encoding="utf-8")
        for marker in rb.SIMULATION_NOTE_MARKERS:
            with self.subTest(marker=marker):
                def applier(record: Mapping, inverse: Mapping, **_kw: object) -> dict:
                    return {"receipt_state": rb.SANDBOX_RECEIPT_STATE,
                            "note": f"沙箱试点：{marker}"}

                svc = rb.RollbackService(self.features, applier=applier)
                record = pilot_record(self.case_root, self.case_root / "notes.txt",
                                      before="A", after="B")
                with self.assertRaises(rb.RollbackError) as ctx:
                    svc.execute([record], record["id"], actor="local")
                self.assertEqual("note", ctx.exception.field)
        self.assertIn("外部生产系统未触碰", rb.SANDBOX_PILOT_NOTE,
                      "缺省试点文案必须诚实：只说外部生产系统未触碰，不写「未实际调用」")
        for marker in rb.SIMULATION_NOTE_MARKERS:
            self.assertNotIn(marker, rb.SANDBOX_PILOT_NOTE)
            self.assertNotIn(marker, rb.SANDBOX_PILOT_NOTE.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
