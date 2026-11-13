"""t9 [t4-2] 验收测试：用户操作审计日志（`agent_explainer/audit.py`）。

运行（全程离线，只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -v
    python tests/test_audit_log.py

覆盖任务验收点：
  1. 追加（append）：九字段齐、seq 递增、哈希链接得上、JSONL 一行一条、只追加不改旧行；
  2. 哈希链校验：`verify()` 逐条复算，正常链通过；
  3. 篡改检测：改内容 / 删行 / 截尾 / 插空行 / 非 JSON / 整段重算历史 → 抛 AuditTamperError；
  4. 过滤查询：actor / task / action / decision / event / 时间范围 / 关键字；
  5. CSV 导出：列顺序、引号转义、参数 JSON、BOM、行数与哈希；
  6. PDF 导出：reportlab 可用 → PDF；不可用 → 可打印 HTML 并在 note 说明（本沙箱无 reportlab）；
  7. 追加式禁令：API 层没有任何更新/删除入口，调用即抛 AuditOperationForbidden；
  8. 隐私与诚实：敏感参数脱敏、IP 到网段、审计不写“已执行”，导出件零外链零脚本。

临时文件只写在系统临时目录（`tests/scratch.py` 的 `scratch_dir`），模块级 tearDown 会整目录删掉。
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import pathlib
import re
import shutil
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_explainer import audit as ad  # noqa: E402
from agent_explainer import server as sv  # noqa: E402  （只用它算决定后的记录，验证桥接）

SAMPLE_PATH = ROOT / "samples" / "action_records.json"
NATIVE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"]
from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

TMP = scratch_dir("audit")
HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
SPEC_SHORT_RE = re.compile(r"^sha256:[a-f0-9]{16}$")
MODIFY_PARAMS = {"max_delete_count": 3, "move_to_trash": True}


def fixture(action_id: str) -> dict:
    return json.loads(json.dumps(next(r for r in NATIVE if r["id"] == action_id)))


def raw_lines(path: pathlib.Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_lines(path: pathlib.Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_line(path: pathlib.Path, index: int) -> dict:
    return json.loads(raw_lines(path)[index])


def rewrite_line(path: pathlib.Path, index: int, mutate) -> dict:
    """篡改第 index 行（返回改后的对象），用于各种破坏场景。"""
    lines = raw_lines(path)
    entry = json.loads(lines[index])
    mutate(entry)
    lines[index] = ad.canonical_json(entry)
    write_lines(path, lines)
    return entry


def sample_event(**over):
    base = dict(actor_id="alice", actor_role="local", task_id="task-demo-1",
                action_id="act-demo-0004", decision="approve", reason="同意这个方案",
                client_info={"channel": "web", "ip": "203.0.113.7",
                             "user_agent": "Mozilla/5.0 (Windows NT 10.0)"})
    base.update(over)
    event = base.pop("event", "decision")
    return ad.build_event(event, **base)


def setUpModule() -> None:  # noqa: N802
    TMP.mkdir(parents=True, exist_ok=True)


def tearDownModule() -> None:  # noqa: N802
    shutil.rmtree(TMP, ignore_errors=True)


class RoomCase(unittest.TestCase):
    """每条测试一间临时目录，互不串味。"""

    def setUp(self) -> None:
        self.room = TMP / self.id().replace(":", "_").replace(".", "_")
        if self.room.exists():
            shutil.rmtree(self.room, ignore_errors=True)
        self.room.mkdir(parents=True, exist_ok=True)
        self.path = self.room / "audit.jsonl"
        self.log = ad.AuditLog(self.path)
        self.addCleanup(lambda: shutil.rmtree(self.room, ignore_errors=True))

    def seed(self, count: int = 3) -> list[dict]:
        out = []
        for index in range(count):
            out.append(self.log.append(sample_event(
                action_id=f"act-demo-000{index + 4}",
                decision=("approve", "modify", "reject")[index % 3],
                reason=f"第 {index + 1} 次决定",
                ts=f"2026-11-10T{8 + index:02d}:00:00Z")))
        return out


# --------------------------------------------------------------------------- #
# 1. 追加
# --------------------------------------------------------------------------- #


class AppendTests(RoomCase):

    def test_entry_carries_the_nine_spec_fields(self) -> None:
        entry = self.log.append(sample_event())
        for field in ad.SPEC_FIELDS:
            self.assertIn(field, entry, f"缺字段 {field}")
        self.assertEqual(entry["ts"][-1], "Z")
        self.assertEqual(entry["actor_id"], "alice")
        self.assertEqual(entry["decision"], "approve")
        self.assertEqual(entry["task_id"], "task-demo-1")
        self.assertEqual(entry["action_id"], "act-demo-0004")
        self.assertEqual(entry["reason"], "同意这个方案")
        self.assertEqual(entry["client_info"]["channel"], "web")

    def test_chain_fields_and_alias(self) -> None:
        first = self.log.append(sample_event())
        self.assertEqual(first["seq"], 1)
        self.assertEqual(first["prev_hash"], ad.GENESIS_HASH)
        self.assertRegex(first["hash"], HASH_RE)
        self.assertEqual(first["entry_hash"], first["hash"], "t8 读的别名必须与 hash 恒等")
        self.assertRegex(first["audit_id"], r"^aud-\d{6}-")
        second = self.log.append(sample_event(decision="reject"))
        self.assertEqual(second["seq"], 2)
        self.assertEqual(second["prev_hash"], first["hash"])

    def test_jsonl_is_one_line_per_entry(self) -> None:
        self.seed(3)
        lines = raw_lines(self.path)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertNotIn("\n", line)
            json.loads(line)

    def test_append_is_the_only_write_and_old_bytes_never_change(self) -> None:
        self.seed(2)
        before = self.path.read_bytes()
        self.log.append(sample_event(decision="reject"))
        after = self.path.read_bytes()
        self.assertTrue(after.startswith(before), "旧字节被改写：不是追加式")
        self.assertGreater(len(after), len(before))

    def test_append_many_chains_in_order(self) -> None:
        entries = self.log.append_many([sample_event(decision="approve"),
                                        sample_event(decision="modify"),
                                        sample_event(decision="reject")])
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3])
        self.assertEqual(entries[1]["prev_hash"], entries[0]["hash"])
        self.assertTrue(self.log.verify()["ok"])

    def test_string_event_form_is_allowed(self) -> None:
        entry = self.log.append("note")
        self.assertEqual(entry["event"], "note")
        self.assertEqual(entry["actor_id"], "system")

    def test_caller_cannot_forge_chain_fields(self) -> None:
        for key in ("seq", "hash", "entry_hash", "prev_hash"):
            with self.subTest(key=key):
                with self.assertRaises(ad.AuditInputError) as ctx:
                    self.log.append({"event": "note", "actor_id": "alice", key: 7})
                self.assertEqual(ctx.exception.code, "AUDIT_CHAIN_FIELD_LOCKED")
                self.assertEqual(ctx.exception.field, key)

    def test_actor_id_is_required_for_multi_user(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ad.AuditInputError) as ctx:
                    ad.build_event("decision", actor_id=value, decision="approve")
                self.assertEqual(ctx.exception.code, "AUDIT_FIELD_REQUIRED")
                self.assertEqual(ctx.exception.field, "actor_id")

    def test_decision_and_event_enums(self) -> None:
        with self.assertRaises(ad.AuditInputError) as ctx:
            ad.build_event("decision", actor_id="alice", decision="force_stop")
        self.assertEqual(ctx.exception.code, "AUDIT_FIELD_ENUM")
        self.assertIn("approve", ctx.exception.detail["allowed"])
        with self.assertRaises(ad.AuditInputError) as ctx:
            ad.build_event("launder_history", actor_id="alice")
        self.assertEqual(ctx.exception.code, "AUDIT_FIELD_ENUM")

    def test_role_enum(self) -> None:
        with self.assertRaises(ad.AuditInputError) as ctx:
            ad.build_event("note", actor_id="alice", actor_role="superuser")
        self.assertEqual(ctx.exception.field, "actor_role")

    def test_ts_forms(self) -> None:
        self.assertEqual(self.log.append(sample_event(ts="2026-11-09T07:00:00Z"))["ts"],
                         "2026-11-09T07:00:00Z")
        naive = self.log.append(sample_event(ts="2026-11-09T07:00:00"))
        self.assertEqual(naive["ts"], "2026-11-09T07:00:00Z", "裸时间按 UTC")
        offset = self.log.append(sample_event(ts="2026-11-09T15:00:00+08:00"))
        self.assertEqual(offset["ts"], "2026-11-09T07:00:00Z", "带偏移换算成 UTC")
        with self.assertRaises(ad.AuditInputError) as ctx:
            self.log.append(sample_event(ts="昨天下午"))
        self.assertEqual(ctx.exception.code, "AUDIT_TIME_INVALID")

    def test_corrects_seq_requires_correction_event(self) -> None:
        with self.assertRaises(ad.AuditInputError) as ctx:
            ad.build_event("decision", actor_id="alice", decision="approve", corrects_seq=1)
        self.assertEqual(ctx.exception.code, "AUDIT_FIELD_CONFLICT")
        with self.assertRaises(ad.AuditInputError):
            ad.build_event("correction", actor_id="alice", reason="改口径", corrects_seq=0)

    def test_reason_is_capped_not_dropped(self) -> None:
        entry = self.log.append(sample_event(reason="长" * (ad.REASON_MAX_CHARS + 500)))
        self.assertEqual(len(entry["reason"]), ad.REASON_MAX_CHARS)
        self.assertTrue(entry["reason"].endswith("…"))

    def test_oversized_entry_is_refused(self) -> None:
        with self.assertRaises(ad.AuditInputError) as ctx:
            self.log.append({"event": "note", "actor_id": "alice",
                             "blob": "x" * (ad.MAX_LINE_BYTES + 10)})
        self.assertEqual(ctx.exception.code, "AUDIT_LINE_TOO_LARGE")

    def test_append_refuses_unserializable_values(self) -> None:
        with self.assertRaises(ad.AuditInputError) as ctx:
            ad.build_event("note", actor_id="alice", extra={"blob": object()})
        self.assertEqual(ctx.exception.code, "AUDIT_VALUE_UNSERIALIZABLE")

    def test_default_log_path_stays_inside_the_project(self) -> None:
        target = ad.default_log_path()
        self.assertEqual(target.name, ad.AUDIT_FILE_NAME)
        self.assertTrue(str(target).startswith(str(ROOT)), f"落点跑出了项目：{target}")

    def test_head_and_stats_after_seeding(self) -> None:
        self.seed(3)
        head = self.log.head()
        self.assertEqual(head["count"], 3)
        self.assertRegex(head["head"], HASH_RE)
        stats = self.log.stats()
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["decisions"]["approve"], 1)
        self.assertEqual(stats["actors"]["alice"], 3)
        self.assertEqual(stats["first_ts"], "2026-11-10T08:00:00Z")


# --------------------------------------------------------------------------- #
# 2. 哈希链校验
# --------------------------------------------------------------------------- #


class ChainTests(RoomCase):

    def test_verify_passes_on_an_honest_log(self) -> None:
        self.seed(4)
        status = self.log.verify()
        self.assertTrue(status["ok"], status["problems"])
        self.assertEqual(status["count"], 4)
        self.assertRegex(status["head"], HASH_RE)
        self.assertEqual(status["problems"], [])
        self.assertRegex(status["file_sha256"], HASH_RE)

    def test_verify_on_empty_and_missing_file(self) -> None:
        empty = self.log.verify()
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["head"], ad.GENESIS_HASH)
        other = ad.AuditLog(self.room / "nope.jsonl")
        self.assertEqual(other.verify()["count"], 0)
        self.assertFalse(other.exists())

    def test_chain_is_reproducible(self) -> None:
        events = [sample_event(ts=f"2026-11-10T{8 + i:02d}:00:00Z") for i in range(3)]
        left = ad.AuditLog(self.room / "left.jsonl")
        right = ad.AuditLog(self.room / "right.jsonl")
        for log in (left, right):
            log.append_many(events)
        self.assertEqual(left.verify()["head"], right.verify()["head"])
        self.assertEqual(left.path.read_bytes(), right.path.read_bytes())

    def test_entry_hash_helper_matches_stdlib_shape(self) -> None:
        payload = {"a": 1, "b": [1, 2]}
        digest = ad.entry_hash(payload, ad.GENESIS_HASH)
        self.assertEqual(digest, ad.entry_hash({"b": [1, 2], "a": 1}, ad.GENESIS_HASH),
                         "键序不能影响哈希")
        self.assertNotEqual(digest, ad.entry_hash(payload, "sha256:" + "1" * 64))
        self.assertEqual(ad.canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')

    def test_seal_records_head_and_count(self) -> None:
        self.seed(3)
        known = self.log.head()
        seal = self.log.seal(note="验收前封印")
        self.assertEqual(seal["event"], "seal")
        self.assertEqual(seal["sealed_head"], known["head"])
        self.assertEqual(seal["sealed_count"], 3)
        status = self.log.verify()
        self.assertTrue(status["ok"], status["problems"])
        self.assertEqual(status["seals"], 1)

    def test_verify_file_helper_is_read_only(self) -> None:
        self.seed(2)
        before = self.path.read_bytes()
        status = ad.verify_file(self.path)
        self.assertTrue(status["ok"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_append_after_seal_stays_consistent(self) -> None:
        self.seed(2)
        self.log.seal()
        self.log.append(sample_event(decision="reject"))
        self.assertTrue(self.log.verify()["ok"])


# --------------------------------------------------------------------------- #
# 3. 篡改检测
# --------------------------------------------------------------------------- #


class TamperTests(RoomCase):

    def seed4(self) -> None:
        for index in range(4):
            self.log.append(sample_event(
                action_id=f"act-demo-000{index + 4}",
                decision=("approve", "modify", "reject")[index % 3],
                ts=f"2026-11-10T{8 + index:02d}:00:00Z"))

    def test_editing_a_reason_breaks_the_hash(self) -> None:
        self.seed4()
        rewrite_line(self.path, 1, lambda e: e.update(reason="偷偷改成同意"))
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify()
        codes = [p["code"] for p in ctx.exception.problems]
        self.assertIn("hash_mismatch", codes)
        self.assertEqual(ctx.exception.problems[0]["index"], 1)
        self.assertEqual(ctx.exception.code, "AUDIT_CHAIN_BROKEN")
        self.assertIn("第 2 条", ctx.exception.render())

    def test_editing_a_param_breaks_the_hash(self) -> None:
        self.log.append(sample_event(decision="modify",
                                     modified_params={"max_delete_count": 12},
                                     ts="2026-11-10T08:00:00Z"))
        rewrite_line(self.path, 0, lambda e: e["modified_params"].update(max_delete_count=999))
        self.assertFalse(self.log.verify(raise_on_tamper=False)["ok"])
        with self.assertRaises(ad.AuditTamperError):
            self.log.verify()

    def test_deleting_a_middle_line_breaks_the_chain(self) -> None:
        self.seed4()
        lines = raw_lines(self.path)
        del lines[2]
        write_lines(self.path, lines)
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify()
        codes = [p["code"] for p in ctx.exception.problems]
        self.assertIn("chain_break", codes)
        self.assertIn("seq_gap", codes)

    def test_truncating_the_tail_needs_an_external_anchor(self) -> None:
        self.seed4()
        known = self.log.head()
        lines = raw_lines(self.path)[:-1]
        write_lines(self.path, lines)
        chain = self.log.verify(raise_on_tamper=False)
        self.assertTrue(chain["ok"], "删掉最后一条对链内是自洽的：只能靠锚点发现")
        self.assertEqual(chain["count"], 3)
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify(expected_head=known["head"])
        self.assertIn("head_mismatch", [p["code"] for p in ctx.exception.problems])
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify(expected_count=4)
        self.assertIn("count_mismatch", [p["code"] for p in ctx.exception.problems])

    def test_self_consistent_rewrite_is_caught_by_the_seal(self) -> None:
        self.seed4()
        self.log.seal(note="封印")
        known = ad.verify_file(self.path)["head"]
        # 攻击者：改掉第 2 条并重算整条链（链内自洽）
        lines = raw_lines(self.path)
        entries = [json.loads(line) for line in lines]
        entries[1]["reason"] = "洗成同意"
        prev = ad.GENESIS_HASH
        for index, entry in enumerate(entries):
            entry["seq"] = index + 1
            entry["prev_hash"] = prev
            entry.pop("hash", None)
            entry.pop("entry_hash", None)
            digest = ad.entry_hash(entry, prev)
            entry["hash"] = digest
            entry["entry_hash"] = digest
            prev = digest
        write_lines(self.path, [ad.canonical_json(e) for e in entries])
        status = self.log.verify(raise_on_tamper=False)
        self.assertFalse(status["ok"])
        self.assertIn("seal_mismatch", [p["code"] for p in status["problems"]])
        with self.assertRaises(ad.AuditTamperError):
            self.log.verify()
        with self.assertRaises(ad.AuditTamperError):
            self.log.verify(expected_head=known)

    def test_blank_line_is_flagged(self) -> None:
        self.seed4()
        lines = raw_lines(self.path)
        lines.insert(2, "")
        write_lines(self.path, lines)
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify()
        self.assertIn("blank_line", [p["code"] for p in ctx.exception.problems])

    def test_non_json_line_is_flagged(self) -> None:
        self.seed4()
        lines = raw_lines(self.path)
        lines[1] = "这条日志我拿橡皮擦了一遍"
        write_lines(self.path, lines)
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify()
        problems = ctx.exception.problems
        self.assertIn("unparsable_line", [p["code"] for p in problems])
        self.assertEqual(problems[0]["index"], 1)

    def test_first_prev_hash_must_be_genesis(self) -> None:
        self.seed4()
        rewrite_line(self.path, 0, lambda e: e.update(prev_hash="sha256:" + "9" * 64))
        with self.assertRaises(ad.AuditTamperError) as ctx:
            self.log.verify()
        self.assertIn("chain_break", [p["code"] for p in ctx.exception.problems])

    def test_hash_alias_tampering_is_flagged(self) -> None:
        self.seed4()
        rewrite_line(self.path, 2, lambda e: e.update(entry_hash="sha256:" + "a" * 64))
        status = self.log.verify(raise_on_tamper=False)
        codes = [p["code"] for p in status["problems"]]
        self.assertIn("alias_mismatch", codes, "别名与 hash 必须恒等")
        self.assertFalse(status["ok"])
        other = ad.AuditLog(self.room / "alias2.jsonl")
        for i in range(2):
            other.append(sample_event(ts=f"2026-11-10T{8 + i:02d}:00:00Z"))
        rewrite_line(other.path, 0, lambda e: e.update(reason="洗白",
                                                       entry_hash="sha256:" + "b" * 64))
        rewrite_line(other.path, 0, lambda e: e.update(hash="sha256:" + "b" * 64))
        status = other.verify(raise_on_tamper=False)
        self.assertIn("hash_mismatch", [p["code"] for p in status["problems"]])

    def test_append_refuses_to_extend_a_broken_chain(self) -> None:
        self.seed4()
        rewrite_line(self.path, 1, lambda e: e.update(reason="动手脚"))
        with self.assertRaises(ad.AuditTamperError):
            self.log.append(sample_event(decision="reject"))
        self.assertEqual(self.log.count(), 4, "断链后不得再追加，避免掩盖篡改")

    def test_opt_out_of_preappend_verification_still_appends(self) -> None:
        loose = ad.AuditLog(self.room / "loose.jsonl", verify_before_append=False)
        loose.append(sample_event())
        rewrite_line(loose.path, 0, lambda e: e.update(reason="动手脚"))
        loose.append(sample_event(decision="reject"))
        self.assertEqual(loose.count(), 2)
        self.assertFalse(loose.verify(raise_on_tamper=False)["ok"])

    def test_correction_is_the_only_after_the_fact_path(self) -> None:
        self.log.append(sample_event(decision="reject", ts="2026-11-10T08:00:00Z"))
        entry = self.log.correction(corrects_seq=1, actor_id="bob",
                                    reason="理由写错了，正确的是收件人未确认",
                                    action_id="act-demo-0004")
        self.assertEqual(entry["event"], "correction")
        self.assertEqual(entry["corrects_seq"], 1)
        self.assertEqual(len(raw_lines(self.path)), 2, "原始条目必须还在")
        self.assertTrue(self.log.verify()["ok"])
        self.assertEqual(parse_line(self.path, 0)["decision"], "reject")


# --------------------------------------------------------------------------- #
# 4. 过滤查询
# --------------------------------------------------------------------------- #


class QueryTests(RoomCase):

    def seed_mix(self) -> None:
        self.log.append_many([
            ad.build_event("decision", actor_id="alice", task_id="task-a",
                           action_id="act-demo-0004", decision="approve",
                           reason="同意整理相册", ts="2026-11-10T08:00:00Z",
                           client_info={"channel": "web", "ip": "203.0.113.1"}),
            ad.build_event("decision", actor_id="bob", task_id="task-a",
                           action_id="act-demo-0005", decision="modify",
                           reason="把删除上限改成 3", ts="2026-11-10T09:00:00Z",
                           modified_params=MODIFY_PARAMS,
                           client_info={"channel": "web", "ip": "203.0.113.2"}),
            ad.build_event("decision", actor_id="alice", task_id="task-b",
                           action_id="act-demo-0008", decision="reject",
                           reason="发信前没让我确认收件人", ts="2026-11-10T10:00:00Z"),
            ad.build_event("note", actor_id="system", task_id="task-b",
                           note="演示存储重置", ts="2026-11-10T11:00:00Z"),
            ad.build_event("access_denied", actor_id="carol", task_id="task-b",
                           action_id="act-demo-0008", reason="非决定态",
                           ts="2026-11-10T12:00:00Z"),
        ])

    def test_filter_by_actor(self) -> None:
        self.seed_mix()
        alice = self.log.query(actor_id="alice", limit=None)
        self.assertEqual([e["action_id"] for e in alice], ["act-demo-0008", "act-demo-0004"])
        self.assertEqual(len(self.log.query(actor_id="bob", limit=None)), 1)
        self.assertEqual(self.log.query(actor_id="nobody", limit=None), [])

    def test_filter_by_task_and_action(self) -> None:
        self.seed_mix()
        self.assertEqual(len(self.log.query(task_id="task-b", limit=None)), 3)
        self.assertEqual(self.log.query(action_id="act-demo-0005", limit=None)[0]["actor_id"], "bob")

    def test_filter_by_decision_and_event(self) -> None:
        self.seed_mix()
        self.assertEqual(len(self.log.query(decision="approve", limit=None)), 1)
        self.assertEqual(len(self.log.query(has_decision=True, limit=None)), 3)
        self.assertEqual(len(self.log.query(event="note", limit=None)), 1)
        self.assertEqual(len(self.log.query(event="access_denied", limit=None)), 1)

    def test_time_range_is_inclusive_and_zone_aware(self) -> None:
        self.seed_mix()
        window = self.log.query(since="2026-11-10T09:00:00Z", until="2026-11-10T10:00:00Z",
                                limit=None)
        self.assertEqual([e["ts"] for e in window],
                         ["2026-11-10T10:00:00Z", "2026-11-10T09:00:00Z"])
        same_instant = self.log.query(since="2026-11-10T17:00:00+08:00", limit=None)
        self.assertEqual([e["ts"] for e in same_instant],
                         ["2026-11-10T12:00:00Z", "2026-11-10T11:00:00Z",
                          "2026-11-10T10:00:00Z", "2026-11-10T09:00:00Z"],
                         "since 是下界：同一时刻的 +08:00 写法要能对齐，且晚于它的都要出现")
        self.assertEqual(len(self.log.query(since="2026-11-01T00:00:00Z", limit=None)), 5)
        self.assertEqual(self.log.query(until="2026-11-01T00:00:00Z", limit=None), [])

    def test_inverted_range_and_bad_time_are_errors(self) -> None:
        self.seed_mix()
        with self.assertRaises(ad.AuditQueryError) as ctx:
            self.log.query(since="2026-11-11T00:00:00Z", until="2026-11-09T00:00:00Z")
        self.assertEqual(ctx.exception.code, "AUDIT_TIME_RANGE_INVERTED")
        with self.assertRaises(ad.AuditQueryError):
            self.log.query(since="not-a-time")

    def test_keyword_search(self) -> None:
        self.seed_mix()
        self.assertEqual(len(self.log.query(q="收件人", limit=None)), 1)
        self.assertEqual(len(self.log.query(q="task-b", limit=None)), 3)
        self.assertEqual(self.log.query(q="不存在的关键词", limit=None), [])

    def test_ordering_limit_offset(self) -> None:
        self.seed_mix()
        newest = self.log.query(limit=2)
        self.assertEqual([e["ts"] for e in newest],
                         ["2026-11-10T12:00:00Z", "2026-11-10T11:00:00Z"])
        page2 = self.log.query(limit=2, offset=2)
        self.assertEqual([e["ts"] for e in page2],
                         ["2026-11-10T10:00:00Z", "2026-11-10T09:00:00Z"])
        oldest = self.log.query(reverse=False, limit=None)
        self.assertEqual(oldest[0]["ts"], "2026-11-10T08:00:00Z")
        self.assertEqual([e["ts"] for e in self.log.tail(2)],
                         ["2026-11-10T12:00:00Z", "2026-11-10T11:00:00Z"])

    def test_query_on_empty_log(self) -> None:
        self.assertEqual(self.log.query(limit=None), [])
        self.assertEqual(self.log.tail(5), [])

    def test_query_results_are_copies(self) -> None:
        self.seed_mix()
        rows = self.log.query(limit=1)
        rows[0]["reason"] = "我在测试里改了"
        self.assertNotEqual(self.log.query(limit=1)[0]["reason"], "我在测试里改了")


# --------------------------------------------------------------------------- #
# 5. CSV 导出
# --------------------------------------------------------------------------- #


class CsvExportTests(RoomCase):

    def test_header_is_the_documented_column_order(self) -> None:
        self.log.append(sample_event())
        result = self.log.export_csv()
        text = result.text
        self.assertIsNotNone(text)
        header = next(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(header, list(ad.CSV_COLUMNS))
        self.assertEqual(result.format, "csv")
        self.assertEqual(result.rows, 1)
        self.assertRegex(result.sha256, SPEC_SHORT_RE)

    def test_rows_round_trip_with_nasty_text(self) -> None:
        self.log.append(sample_event(reason='他说"好, 继续"\n第二行, 带逗号',
                                     original_params={"path": "a,b", "note": "换行\n在里"},
                                     modified_params={"max_delete_count": 3}))
        self.log.append(sample_event(decision="reject", reason="拒绝; 分号"))
        text = self.log.export_csv().text
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 3, "1 表头 + 2 数据")
        idx = {name: i for i, name in enumerate(ad.CSV_COLUMNS)}
        self.assertEqual(rows[1][idx["reason"]], '他说"好, 继续"\n第二行, 带逗号')
        self.assertIn('"path":"a,b"', rows[1][idx["original_params"]])
        self.assertIn("max_delete_count", rows[1][idx["modified_params"]])
        self.assertEqual(rows[2][idx["decision"]], "reject")

    def test_bom_appears_exactly_once(self) -> None:
        self.log.append(sample_event())
        with_bom = self.log.export_csv().text
        without = self.log.export_csv(bom=False).text
        self.assertTrue(with_bom.startswith("\ufeff"))
        self.assertEqual(with_bom.count("\ufeff"), 1)
        self.assertFalse(without.startswith("\ufeff"))

    def test_export_to_disk_returns_path_and_bytes(self) -> None:
        self.seed(3)
        target = self.room / "audit.csv"
        result = self.log.export_csv(target)
        self.assertTrue(target.exists())
        self.assertEqual(result.path, str(target))
        self.assertEqual(result.bytes, target.stat().st_size)
        self.assertIsNone(result.text)
        self.assertEqual(result.rows, 3)
        self.assertIn("alice", target.read_text(encoding="utf-8-sig"))

    def test_chain_columns_travel_with_the_export(self) -> None:
        self.seed(2)
        rows = list(csv.reader(io.StringIO(self.log.export_csv().text.lstrip("\ufeff"))))
        idx = {name: i for i, name in enumerate(ad.CSV_COLUMNS)}
        self.assertEqual(rows[1][idx["hash"]], parse_line(self.path, 0)["hash"])
        self.assertEqual(rows[2][idx["prev_hash"]], parse_line(self.path, 1)["prev_hash"])

    def test_export_respects_filtered_rows(self) -> None:
        self.log.append(sample_event(decision="approve", ts="2026-11-10T08:00:00Z"))
        self.log.append(sample_event(decision="reject", ts="2026-11-10T09:00:00Z"))
        rows = self.log.query(decision="reject", limit=None)
        result = self.log.export_csv(rows=rows)
        parsed = list(csv.reader(io.StringIO(result.text.lstrip("\ufeff"))))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1][list(ad.CSV_COLUMNS).index("decision")], "reject")

    def test_custom_columns(self) -> None:
        self.log.append(sample_event())
        result = self.log.export_csv(columns=("ts", "actor_id", "decision"))
        header = next(csv.reader(io.StringIO(result.text.lstrip("\ufeff"))))
        self.assertEqual(header, ["ts", "actor_id", "decision"])
        self.assertEqual(result.columns, ("ts", "actor_id", "decision"))

    def test_export_of_empty_log_is_header_only(self) -> None:
        result = self.log.export_csv()
        rows = list(csv.reader(io.StringIO(result.text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(result.rows, 0)

    def test_export_write_failure_is_reported(self) -> None:
        blocker = self.room / "blocker"
        blocker.write_text("占位", encoding="utf-8")
        with self.assertRaises(ad.AuditExportError) as ctx:
            self.log.export_csv(blocker / "impossible.csv")
        self.assertEqual(ctx.exception.code, "AUDIT_EXPORT_WRITE_FAILED")


# --------------------------------------------------------------------------- #
# 6. 隐私脱敏
# --------------------------------------------------------------------------- #


class RedactionTests(RoomCase):

    def test_secret_values_are_masked_with_a_fingerprint(self) -> None:
        self.log.append(sample_event(
            original_params={"api_key": "sk-abcdefghijklmnopqrstuvwxyz",
                             "max_delete_count": 12},
            modified_params={"api_key": "sk-zzzzzzzzzzzzzzzzzzzzzzzz",
                             "max_delete_count": 3}))
        entry = self.log.read()[0]
        self.assertIn("<redacted:api_key#", entry["original_params"]["api_key"])
        self.assertRegex(entry["original_params"]["api_key"], r"#sha256:[a-f0-9]{12}>$")
        disk = self.path.read_text(encoding="utf-8")
        # 磁盘上只许出现指纹，不许出现密钥原文
        # （注意 task-demo-1 里也含 "sk-" 三个字符，别拿它当探针——要查就查具体秘密值）
        for secret in ("sk-abcdefghijklmnopqrstuvwxyz", "sk-zzzzzzzzzzzzzzzzzzzzzzzz"):
            self.assertNotIn(secret, disk)
            self.assertNotIn(secret.upper(), disk)
        self.assertEqual(entry["original_params"]["max_delete_count"], 12, "非敏感值照记")

    def test_same_secret_yields_the_same_fingerprint(self) -> None:
        self.log.append(sample_event(original_params={"token": "abc123token"}))
        self.log.append(sample_event(original_params={"token": "abc123token"},
                                     decision="reject"))
        first = self.log.read()[0]["original_params"]["token"]
        second = self.log.read()[1]["original_params"]["token"]
        self.assertEqual(first, second)
        self.assertNotIn("abc123token", self.path.read_text(encoding="utf-8"))

    def test_nested_and_list_secrets_are_masked(self) -> None:
        entry = self.log.append(sample_event(original_params={
            "headers": {"Authorization": "Bearer super-secret-value",
                        "Cookie": "sid=abcdefgh"},
            "items": [{"password": "hunter2"}, {"keep": "ok"}]}))
        text = ad.canonical_json(entry)
        for needle in ("super-secret-value", "abcdefgh", "hunter2"):
            self.assertNotIn(needle, text)
        self.assertEqual(entry["original_params"]["items"][1]["keep"], "ok")

    def test_key_name_variants_are_caught(self) -> None:
        entry = self.log.append(sample_event(original_params={
            "API-KEY": "abc", "accessKey": "def", "session-id": "ghi",
            "access_token": "jkl", "plain": "keepme"}))
        params = entry["original_params"]
        for key in ("API-KEY", "accessKey", "session-id", "access_token"):
            self.assertTrue(str(params[key]).startswith("<redacted:"), key)
        self.assertEqual(params["plain"], "keepme")

    def test_pii_masking(self) -> None:
        entry = self.log.append(sample_event(original_params={
            "email": "zhangsan@example.com", "phone": "13800001111"}))
        params = entry["original_params"]
        self.assertEqual(params["email"], "z***@example.com")
        self.assertEqual(params["phone"], "138******11", "打码保持长度，遮住中间")
        self.assertNotIn("zhangsan", ad.canonical_json(params))

    def test_ip_defaults_to_subnet(self) -> None:
        self.log.append(sample_event(client_info={"channel": "web", "ip": "203.0.113.7"}))
        self.assertEqual(self.log.read()[0]["client_info"]["ip"], "203.0.113.*")
        # 想留全 IP 必须建日志时就声明 ip_scope="exact"（上层已脱敏的值下层还原不了）
        exact = ad.AuditLog(self.room / "exact.jsonl", ip_scope="exact")
        exact.append({"event": "decision", "actor_id": "alice", "decision": "approve",
                      "client_info": {"ip": "203.0.113.7"}})
        self.assertEqual(exact.read()[0]["client_info"]["ip"], "203.0.113.7")
        dropped = ad.AuditLog(self.room / "drop.jsonl", ip_scope="drop")
        dropped.append({"event": "decision", "actor_id": "alice", "decision": "approve",
                        "client_info": {"ip": "203.0.113.7"}})
        self.assertEqual(dropped.read()[0]["client_info"]["ip"], ad.IP_MASKED)

    def test_ipv6_is_masked_to_prefix(self) -> None:
        self.log.append(sample_event(client_info={"ip": "2001:db8:1:2::42"}))
        self.assertTrue(self.log.read()[0]["client_info"]["ip"].endswith("::*"))

    def test_client_info_keeps_only_scalars_and_caps_agent(self) -> None:
        entry = self.log.append(sample_event(client_info={
            "user_agent": "A" * 400, "extra": {"nested": 1}, "attempts": 3}))
        info = entry["client_info"]
        self.assertLessEqual(len(info["user_agent"]), ad.USER_AGENT_MAX_CHARS)
        self.assertEqual(info["extra"], "<dict>")
        self.assertEqual(info["attempts"], 3)

    def test_non_scalar_client_info_and_deep_params_are_contained(self) -> None:
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": "secret"}}}}}}}}
        entry = self.log.append(sample_event(original_params=deep))
        self.assertTrue(ad.canonical_json(entry["original_params"]).startswith("{"))

    def test_redact_can_be_turned_off_explicitly(self) -> None:
        loud = ad.AuditLog(self.room / "loud.jsonl", redact=False)
        loud.append({"event": "decision", "actor_id": "alice", "decision": "approve",
                     "original_params": {"api_key": "sk-visible-because-asked"}})
        self.assertEqual(loud.read()[0]["original_params"]["api_key"],
                         "sk-visible-because-asked")
        self.assertIn("sk-visible-because-asked", loud.path.read_text(encoding="utf-8"))
        quiet = ad.AuditLog(self.room / "quiet.jsonl")
        quiet.append({"event": "decision", "actor_id": "alice", "decision": "approve",
                      "original_params": {"api_key": "sk-visible-because-asked"}})
        self.assertNotIn("sk-visible-because-asked", quiet.path.read_text(encoding="utf-8"))

    def test_diff_params_reports_names_and_redacts_secrets(self) -> None:
        changes = ad.diff_params({"max_delete_count": 12, "move_to_trash": False,
                                  "api_key": "sk-old-value"},
                                 {"max_delete_count": 3, "move_to_trash": False,
                                  "api_key": "sk-new-value"})
        names = [c["name"] for c in changes]
        self.assertEqual(names, ["api_key", "max_delete_count"])
        self.assertNotIn("sk-old-value", ad.canonical_json(changes))
        self.assertNotIn("sk-new-value", ad.canonical_json(changes))
        self.assertEqual(changes[1]["before"], 12)
        self.assertEqual(changes[1]["after"], 3)

    def test_exports_never_leak_secrets(self) -> None:
        self.log.append(sample_event(original_params={"api_key": "sk-leak-check-1234"},
                                     modified_params={"max_delete_count": 3},
                                     client_info={"ip": "198.51.100.9",
                                                  "user_agent": "UA/1.0"}))
        for text in (self.log.export_csv().text, self.log.export_html().text):
            self.assertNotIn("sk-leak-check-1234", text)
            # HTML 会转义 <，所以断言标记本体而不是尖括号
            self.assertIn("redacted:api_key", text)
            self.assertIn("198.51.100.*", text)


# --------------------------------------------------------------------------- #
# 7. 追加式禁令（API 层禁止更新/删除）
# --------------------------------------------------------------------------- #


class AppendOnlyGuardTests(RoomCase):

    MUTATING_NAMES = (
        "update", "update_entry", "update_log", "delete", "delete_entry", "delete_log",
        "remove", "remove_entry", "truncate", "clear", "clear_log", "overwrite",
        "rewrite", "rewrite_history", "patch", "patch_entry", "edit", "edit_entry",
        "prune", "erase", "purge", "drop", "drop_entry", "retract", "rollback",
        "undo", "replace", "replace_entry", "set_entry", "modify_entry",
    )

    def test_no_mutation_attribute_exists_on_the_class(self) -> None:
        public = [name for name in dir(ad.AuditLog) if not name.startswith("_")]
        for name in public:
            for verb in ad.FORBIDDEN_OPERATIONS:
                self.assertNotIn(verb, name.lower(),
                                 f"{type(ad.AuditLog).__name__}.{name} 听起来像更新/删除")
        for name in ("append", "append_many", "query", "read", "verify",
                     "export_csv", "export_pdf", "seal", "correction"):
            self.assertTrue(callable(getattr(ad.AuditLog, name)), name)

    def test_calling_any_mutation_name_is_forbidden_on_the_log(self) -> None:
        self.seed(2)
        for name in self.MUTATING_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(ad.AuditOperationForbidden) as ctx:
                    getattr(self.log, name)
                self.assertEqual(ctx.exception.code, "AUDIT_OPERATION_FORBIDDEN")
                self.assertIn("append-only", ctx.exception.render())
        self.assertEqual(self.log.count(), 2, "被拒的调用不能改动任何东西")

    def test_module_level_guard(self) -> None:
        for name in ("delete_log", "clear_audit", "truncate_audit", "update_entry"):
            with self.subTest(name=name):
                with self.assertRaises(ad.AuditOperationForbidden):
                    getattr(ad, name)
        with self.assertRaises(AttributeError):
            getattr(ad, "frobnicate_unrelated")

    def test_forbidden_operations_list_covers_the_task_words(self) -> None:
        for verb in ("update", "delete", "truncate", "clear", "remove", "overwrite"):
            self.assertIn(verb, ad.FORBIDDEN_OPERATIONS)

    def test_open_call_uses_append_flag_only(self) -> None:
        source = pathlib.Path(ad.__file__).read_text(encoding="utf-8")
        self.assertIn("os.O_APPEND", source)
        self.assertNotIn("os.O_TRUNC", source, "写入句柄不得带截断标志")

    def test_all_disk_writes_live_in_the_two_declared_sinks(self) -> None:
        """追加与导出各只有一个落盘口：日志走 O_APPEND，导出件走 write_bytes。

        出现第三处写盘（尤其写模式 open / O_TRUNC）就意味着有人给审计开了改写的路。
        """
        source = pathlib.Path(ad.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        sinks: dict[str, set[str]] = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                attr = node.func.attr
                owner = node.func.value
                module_owner = isinstance(owner, ast.Name) and owner.id in {"os", "shutil"}
                if attr in ("write_text", "write_bytes", "unlink", "rmtree") \
                        or (module_owner and attr in ("remove", "replace", "truncate", "rename")):
                    sinks.setdefault(fn.name, set()).add(attr)
                if attr == "open" and any(isinstance(a, ast.Constant)
                                          and isinstance(a.value, str)
                                          and re.search(r"[wxa+]", a.value)
                                          for a in node.args[1:]):
                    sinks.setdefault(fn.name, set()).add("open_write_mode")
        self.assertEqual(set(sinks), {"_finish_export"},
                         f"意外的写盘位置：{sinks}")
        self.assertEqual(sinks["_finish_export"], {"write_bytes"})
        self.assertIn("os.O_APPEND", source)
        self.assertNotIn("os.O_TRUNC", source, "写入句柄不得带截断标志")
        self.assertNotIn("os.O_WRONLY | os.O_CREAT | os.O_TRUNC", source)

    def test_dunder_probe_keeps_standard_semantics(self) -> None:
        with self.assertRaises(AttributeError):
            self.log.__deepcopy__
        self.assertFalse(hasattr(self.log, "__deepcopy__"))


# --------------------------------------------------------------------------- #
# 8. 导出：PDF / 可打印 HTML
# --------------------------------------------------------------------------- #

REPORTLAB_NAMES = (
    "reportlab", "reportlab.lib", "reportlab.lib.pagesizes", "reportlab.platypus",
    "reportlab.lib.styles", "reportlab.lib.units", "reportlab.pdfbase",
    "reportlab.pdfbase.cidfonts",
)


class _StubDoc:
    def __init__(self, calls: list, **kwargs) -> None:
        self.calls = calls
        self.kwargs = kwargs

    def build(self, flow) -> None:
        self.calls.append(("build", flow))
        target = self.kwargs.get("_target")
        target.write_bytes(b"%PDF-1.4 stub\n")


class _StubTable:
    def __init__(self, data, calls, **kwargs) -> None:
        calls.append(("table", data))
        self.data = data

    def setStyle(self, style) -> None:  # noqa: N802
        self.calls_style = style


def install_reportlab_stub(case: unittest.TestCase, *, fail_font: bool = False,
                           fail_build: bool = False) -> dict:
    """把 reportlab 的六个子模块塞进 sys.modules，让惰性导入成功。"""
    calls: list = []
    saved = {name: sys.modules.get(name) for name in REPORTLAB_NAMES}

    def restore() -> None:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    case.addCleanup(restore)

    pagesizes = types.ModuleType("reportlab.lib.pagesizes")
    pagesizes.A4 = (595, 842)
    pagesizes.landscape = lambda size: (size[1], size[0])

    platypus = types.ModuleType("reportlab.platypus")

    def doc_factory(target, **kwargs):
        if fail_build:
            def build(flow):
                raise RuntimeError("排版炸了")
            calls.append(("doc", target))
            return types.SimpleNamespace(build=build)
        path = pathlib.Path(str(target))
        calls.append(("doc", path))
        return _StubDoc(calls, _target=path, **kwargs)

    platypus.SimpleDocTemplate = doc_factory
    platypus.Paragraph = lambda text, style: ("p", text)
    platypus.Spacer = lambda w, h: ("spacer", w, h)
    platypus.Table = lambda data, **kw: _StubTable(data, calls, **kw)
    platypus.TableStyle = lambda rules: ("style", rules)

    styles = types.ModuleType("reportlab.lib.styles")
    styles.getSampleStyleSheet = lambda: {"Title": "TitleStyle", "Normal": "NormalStyle"}

    units = types.ModuleType("reportlab.lib.units")
    units.mm = 3.54

    pdfbase = types.ModuleType("reportlab.pdfbase")
    pdfbase.registerFont = lambda name: calls.append(("font", name))

    cidfonts = types.ModuleType("reportlab.pdfbase.cidfonts")

    def unicode_font(name):
        if fail_font:
            raise RuntimeError("没有 CJK 字体")
        return ("cid", name)
    cidfonts.UnicodeCIDFont = unicode_font

    modules = {
        "reportlab": types.ModuleType("reportlab"),
        "reportlab.lib": types.ModuleType("reportlab.lib"),
        "reportlab.lib.pagesizes": pagesizes,
        "reportlab.platypus": platypus,
        "reportlab.lib.styles": styles,
        "reportlab.lib.units": units,
        "reportlab.pdfbase": pdfbase,
        "reportlab.pdfbase.cidfonts": cidfonts,
    }
    for name, module in modules.items():
        sys.modules[name] = module
    return {"calls": calls}


class PdfExportTests(RoomCase):

    def seed3(self) -> list[dict]:
        return self.seed(3)

    def test_reportlab_available_makes_a_real_pdf(self) -> None:
        self.seed3()
        stub = install_reportlab_stub(self)
        target = self.room / "audit.pdf"
        result = self.log.export_pdf(target)
        self.assertEqual(result.format, "pdf")
        self.assertTrue(target.exists())
        self.assertEqual(result.rows, 3)
        self.assertIn("reportlab", result.note)
        kinds = [c[0] for c in stub["calls"]]
        self.assertIn("table", kinds)
        self.assertIn("build", kinds)
        table = next(c[1] for c in stub["calls"] if c[0] == "table")
        self.assertEqual(len(table), 4, "表头 + 3 条")
        self.assertIn("STSong-Light", [c[1] for c in stub["calls"] if c[0] == "font"])

    def test_missing_cjk_font_is_reported_not_silenced(self) -> None:
        self.seed3()
        install_reportlab_stub(self, fail_font=True)
        result = self.log.export_pdf(self.room / "audit.pdf")
        self.assertEqual(result.format, "pdf")
        self.assertIn("Helvetica", result.note)

    def test_build_failure_falls_back_to_printable_html(self) -> None:
        self.seed3()
        install_reportlab_stub(self, fail_build=True)
        target = self.room / "audit.pdf"
        result = self.log.export_pdf(target)
        self.assertEqual(result.format, "html")
        self.assertIn("reportlab_build_failed", str(result.fallback))
        self.assertIn("Ctrl+P", result.note)

    def test_no_reportlab_falls_back_to_printable_html(self) -> None:
        self.seed3()
        namespace, reason = ad._load_reportlab()
        if namespace is not None:                       # 本机装了 reportlab
            self.skipTest(f"本机有 reportlab（{reason}），回退路径由 stub 用例覆盖")
        target = self.room / "audit.html"
        result = self.log.export_pdf(target)
        self.assertEqual(result.format, "html")
        self.assertIn("reportlab_unavailable", str(result.fallback))
        self.assertIn("reportlab", result.note, "note 必须说明为什么不是 PDF")
        self.assertIn("Ctrl+P", result.note)
        self.assertTrue(target.exists())

    def test_prefer_pdf_raises_when_reportlab_is_unavailable(self) -> None:
        self.seed3()
        namespace, _ = ad._load_reportlab()
        if namespace is not None:
            self.skipTest("本机有 reportlab：prefer='pdf' 应当成功出 PDF")
        with self.assertRaises(ad.AuditExportError) as ctx:
            self.log.export_pdf(self.room / "audit.pdf", prefer="pdf")
        self.assertEqual(ctx.exception.code, "AUDIT_REPORTLAB_UNAVAILABLE")

    def test_prefer_html_skips_the_pdf_attempt(self) -> None:
        self.seed3()
        result = self.log.export_html(self.room / "audit.html")
        self.assertEqual(result.format, "html")
        self.assertIn("零外链", result.note)
        pdf_style = self.log.export_pdf(self.room / "audit2.html", prefer="html")
        self.assertEqual(pdf_style.format, "html")
        self.assertIn("按要求", pdf_style.note)

    def test_export_result_shape_is_machine_readable(self) -> None:
        self.seed3()
        payload = self.log.export_csv(self.room / "audit.csv").as_dict()
        for key in ("format", "path", "rows", "columns", "bytes", "sha256", "note"):
            self.assertIn(key, payload)
        self.assertEqual(payload["format"], "csv")
        self.assertEqual(payload["rows"], 3)


class HtmlExportTests(RoomCase):

    def test_honest_banner_and_notes(self) -> None:
        self.seed(3)
        text = self.log.export_html().text
        self.assertIn("哈希链校验", text)
        self.assertIn("通过", text)
        self.assertIn("追加式", text)
        self.assertIn("脱敏", text)
        self.assertIn("不等于已执行", text)
        self.assertIn("<meta charset=\"utf-8\">", text)
        self.assertIn("@page", text, "打印样式要在")
        self.assertIn("谁", text)
        self.assertIn("决定", text)

    def test_broken_chain_banner_warns(self) -> None:
        self.seed(3)
        rewrite_line(self.path, 1, lambda e: e.update(reason="动手脚"))
        text = self.log.export_html().text
        self.assertIn("失败", text)
        self.assertIn("请勿把这份导出当作可信记录", text)
        self.assertIn("内容被改过", text)

    def test_html_escapes_injection_and_has_no_external_refs(self) -> None:
        self.log.append(sample_event(reason="<script>alert(1)</script>",
                                     modified_params={"note": "<img onerror=x>"}))
        text = self.log.export_html().text
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("<script", text.replace("&lt;script", ""))
        for urlish in re.findall(r'(?:src|href)\s*=\s*"[^"]*"', text):
            self.assertNotIn(":", urlish, f"导出件里出现了外部引用：{urlish}")

    def test_html_lists_the_changed_parameters(self) -> None:
        self.log.append(sample_event(decision="modify",
                                     original_params={"max_delete_count": 12},
                                     modified_params={"max_delete_count": 3},
                                     changed_fields=[{"name": "max_delete_count",
                                                      "before": 12, "after": 3}]))
        text = self.log.export_html().text
        self.assertIn("max_delete_count", text)
        self.assertIn("12→3", text)


# --------------------------------------------------------------------------- #
# 9. 与 t8 的 /intervene 桥接
# --------------------------------------------------------------------------- #


class BridgeTests(RoomCase):

    def payload_for_modify(self) -> dict:
        before = fixture("act-demo-0005")
        after = sv.apply_decision(before, "modify", modified_params=MODIFY_PARAMS,
                                  actor_id="local-user", actor_role="local",
                                  request_id="req-t9-1", decided_at="2026-11-10T08:00:00Z")
        return ad.event_from_decision(before, after, actor_id="alice",
                                      actor_role="local", task_id="task-demo-1",
                                      request_id="req-t9-1",
                                      client_info={"channel": "web", "ip": "203.0.113.7"})

    def test_modify_decision_becomes_one_audit_entry(self) -> None:
        entry = self.log.append(self.payload_for_modify())
        self.assertEqual(entry["event"], "decision")
        self.assertEqual(entry["decision"], "modify")
        self.assertEqual(entry["action_id"], "act-demo-0005")
        self.assertEqual(entry["record_status_before"], "pending_confirmation")
        self.assertEqual(entry["record_status_after"], "modified")
        self.assertEqual(entry["request_id"], "req-t9-1")
        self.assertEqual(entry["actor_id"], "alice")
        self.assertTrue(self.log.verify()["ok"])

    def test_changed_fields_survive_to_the_export(self) -> None:
        self.log.append(self.payload_for_modify())
        entry = self.log.read()[0]
        names = sorted(c["name"] for c in entry["changed_fields"])
        self.assertEqual(names, ["max_delete_count", "move_to_trash"])
        self.assertIn("max_delete_count:12→3",
                      ad._changed_text(entry["changed_fields"]))
        self.assertIn("max_delete_count", self.log.export_csv().text)

    def test_original_params_come_from_the_record(self) -> None:
        entry = self.log.append(self.payload_for_modify())
        self.assertIsNotNone(entry["original_params"], "决定前参数必须可复盘")
        self.assertIn("max_delete_count", entry["original_params"])
        self.assertEqual(entry["original_params"]["max_delete_count"], 12)
        self.assertIn("target", entry["original_params"])
        self.assertEqual(entry["modified_params"]["max_delete_count"], 3)

    def test_client_info_is_masked_in_the_bridge(self) -> None:
        entry = self.log.append(self.payload_for_modify())
        self.assertEqual(entry["client_info"]["ip"], "203.0.113.*")

    def test_three_decisions_from_the_golden_cases_are_all_recordable(self) -> None:
        golden = json.loads((ROOT / "agent_explainer" / "web" / "verify" /
                             "intervene-golden.json").read_text(encoding="utf-8"))
        written = 0
        for case in golden["cases"]:
            before = case["input_record"]
            after = case["result_record"]
            payload = ad.event_from_decision(before, after, actor_id="alice",
                                             request_id=case["name"])
            entry = self.log.append(payload)
            self.assertEqual(entry["decision"], case["decision"])
            self.assertEqual(entry["action_id"], before["id"])
            self.assertTrue(entry["reason"], f"{case['name']}：审计页要能看到为什么这么决定")
            self.assertTrue(entry["action_id"])
            written += 1
        self.assertGreaterEqual(written, 3)
        status = self.log.verify()
        self.assertTrue(status["ok"], status["problems"])
        self.assertEqual(status["count"], written)
        decisions = sorted(e["decision"] for e in self.log.read())
        self.assertEqual(decisions, ["approve", "approve", "modify", "reject"][:written])

    def test_audit_never_claims_execution(self) -> None:
        self.log.append(self.payload_for_modify())
        text = ad.canonical_json(self.log.read()) + self.log.export_csv().text \
            + self.log.export_html().text
        for banned in ("已执行完成", "已强制停止", "已停止 AI", "执行成功", "已生效"):
            self.assertNotIn(banned, text)
        self.assertNotIn("execution_receipt", ad.canonical_json(self.log.read()[0]))


# --------------------------------------------------------------------------- #
# 10. 纯净性与离线
# --------------------------------------------------------------------------- #


class PurityTests(unittest.TestCase):

    ALLOWED_IMPORTS = frozenset({
        "__future__", "abc", "ast", "collections", "csv", "dataclasses", "datetime",
        "hashlib", "html", "importlib", "io", "json", "os", "pathlib", "re", "shutil",
        "sys", "tempfile", "threading", "types", "typing", "unittest",
    })
    BANNED_NAMES = frozenset({
        "urlopen", "Request", "build_opener", "urlopen", "Popen", "run", "call",
        "check_output", "create_connection", "socket",
    })

    def tree(self) -> ast.Module:
        return ast.parse(pathlib.Path(ad.__file__).read_text(encoding="utf-8"))

    def test_only_allowed_stdlib_is_imported(self) -> None:
        tree = self.tree()
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
        self.assertTrue(roots, "没解析到导入")
        self.assertEqual(roots - self.ALLOWED_IMPORTS, set(),
                         "引入了白名单外的依赖（审计必须只用标准库）")

    def test_reportlab_is_imported_lazily_inside_a_function(self) -> None:
        tree = self.tree()
        top_level = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                top_level.append(node.module or "")
        self.assertFalse([name for name in top_level if "reportlab" in name],
                         "reportlab 不能在模块顶层导入：不可用时整包就得炸")
        source = pathlib.Path(ad.__file__).read_text(encoding="utf-8")
        self.assertIn('importlib.import_module("reportlab.platypus")', source)

    def test_no_network_literals_or_shell(self) -> None:
        source = pathlib.Path(ad.__file__).read_text(encoding="utf-8")
        for needle in ("http://", "https://", "urllib", "socket", "subprocess",
                       "urlopen", "sk-", "Bearer x"):
            self.assertNotIn(needle, source, f"审计模块里出现了 {needle}")
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotIn(node.value.id, ("socket", "subprocess"),
                                 "不许起进程或开连接")
                if node.value.id == "os":
                    self.assertNotIn(node.attr, ("system", "popen", "spawnl"),
                                     "不许起进程")

    def test_no_writes_outside_the_audit_path(self) -> None:
        tree = self.tree()
        targets: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("write_text", "write_bytes", "mkdir", "remove",
                                      "unlink", "rmtree", "open"):
                    targets.append(node.func.attr)
        # 模块只写日志文件（os.open + O_APPEND）与导出文件；自检入口删自己的临时文件
        self.assertLessEqual(set(targets), {"open", "mkdir", "remove", "write_bytes"},
                             f"意外的写操作：{targets}")

    def test_errors_are_machine_readable(self) -> None:
        for cls, code in ((ad.AuditInputError, None), (ad.AuditQueryError, None),
                          (ad.AuditTamperError, "AUDIT_CHAIN_BROKEN"),
                          (ad.AuditOperationForbidden, "AUDIT_OPERATION_FORBIDDEN"),
                          (ad.AuditExportError, None)):
            self.assertTrue(issubclass(cls, ad.AuditError), cls.__name__)
        payload = ad.AuditInputError("AUDIT_FIELD_REQUIRED", "缺字段", field="actor_id",
                                     hint="服务端写身份").as_dict()
        self.assertEqual(payload["code"], "AUDIT_FIELD_REQUIRED")
        self.assertEqual(payload["field"], "actor_id")
        self.assertIn("message", payload)
        self.assertIn("建议", ad.AuditInputError("X", "y", hint="这样修").render())

    def test_public_surface_is_exported(self) -> None:
        for name in ("AuditLog", "build_event", "event_from_decision", "open_log",
                     "verify_file", "render_html", "entries_to_csv", "ExportResult",
                     "AuditTamperError", "AuditOperationForbidden", "SPEC_FIELDS",
                     "CSV_COLUMNS", "FORBIDDEN_OPERATIONS"):
            self.assertTrue(hasattr(ad, name), name)
        self.assertEqual(ad.SPEC_FIELDS[:3], ("ts", "actor_id", "task_id"))
        self.assertIn("client_info", ad.SPEC_FIELDS)
        self.assertIn("original_params", ad.CSV_COLUMNS)
        self.assertIn("hash", ad.CSV_COLUMNS)


class ProductEntrypointTests(RoomCase):

    def test_open_log_returns_an_append_only_handle(self) -> None:
        log = ad.open_log(self.path)
        self.assertEqual(log.path, self.path)
        entry = log.append(ad.build_event("decision", actor_id="local-user",
                                          action_id="act-demo-0004", decision="approve",
                                          reason="同意"))
        self.assertEqual(entry["actor_id"], "local-user")
        self.assertTrue(log.verify()["ok"])
        with self.assertRaises(ad.AuditOperationForbidden):
            log.delete_entry

    def test_module_main_self_check_leaves_no_scratch_file(self) -> None:
        target = ad.default_export_path("selfcheck-audit.jsonl")
        self.assertFalse(target.exists(), "自检入口用完必须清掉临时文件")

    def test_default_export_path_is_timestamped(self) -> None:
        first = ad.default_export_path("csv")
        self.assertEqual(first.suffix, ".csv")
        self.assertTrue(first.name.startswith("audit-"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
