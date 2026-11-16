"""R8-4 钉测：`tools/verify_export.py`（REQ-008 / B8-4 独立验证脚本）。

运行（全程离线，只用标准库 unittest；临时目录一律 `tests/scratch.py`，禁 `tempfile`）：
    cd agent-explainer
    python -X utf8 -m unittest tests.test_verify_export -v

钉住的六件事（PM 任务书 TASK-R8-4 §2.2 / §2.3 / §4 交付 2）：
  1. **口径逐字等价**：脚本内重算的 `canonical_json` / `entry_hash` / `GENESIS_HASH` /
     链校验问题码，与本仓 `agent_explainer/audit.py` 的冻结实现逐字相等（键排序 /
     分隔符 / Unicode / 浮点 / 排除键 / 兜底转换各给案例）——两边漂移当场红；
  2. **链连续性**：首条 / 中间 / 末条 / seq 缺口 / 空行 / 非 JSON / 篡改各给案例，
     断裂必须报出序号与原因码，不得静默跳过；
  3. **封印锚点与链尖分别输出**：`sealed_head` ≡ 最后一次 seal 写下的锚点，链尖 = 最新
     一条的哈希，两者同值即判 FAIL（不得拿链尖顶替封印锚点）；
  4. **篡改注入必须被拒绝**：改 1 字节 / 删 1 行 / 换链头值 / 伪造 `sealed_head` /
     截尾 / 整段自洽重算 → 非零退出 + 明确原因码；
  5. **CSV 面**：表头逐字 24 列、BOM、`\\r\\n`、逐格与链上同值、尾列 `sealed_head`
     逐字一致；缺 `--csv` 时该段判 SKIP（不冒充已验）；
  6. **独立运行 + 判定一致**：脚本复制到仓库外目录只带标准库 `--self-test` 通过；
     脚本链路段判定与本仓 `audit.verify_file` 逐例相等（PM-PLAN §10.5 交叉验证）。

纪律侧（§1.1 / §1.3）：`SIGNATURE_FIELDS` 11、`CSV_COLUMNS` 24、`_HTML_COLUMNS` 14、
`FORMATS` 三值逐字不变；脚本零 `agent_explainer` import、零第三方、零 `tempfile`；
脚本输出零展示纪律禁语（0.4）。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from agent_explainer import audit as ad  # noqa: E402
from agent_explainer.secretariat import SIGNATURE_FIELDS  # noqa: E402
from agent_explainer.compliance import ANCHOR_STATE, FORMATS  # noqa: E402
from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

SCRIPT = ROOT / "tools" / "verify_export.py"
TMP = scratch_dir("verify-export")

_spec = importlib.util.spec_from_file_location("verify_export", SCRIPT)
ve = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(ve)

CALIBER = "TraceLens export caliber R8-4 / 2026-11-15"
STDLIB_ALLOWED = frozenset({"__future__", "argparse", "csv", "hashlib", "io", "json",
                            "pathlib", "re", "sys", "datetime"})
CSV_24 = ("seq", "ts", "actor_id", "actor_role", "task_id", "action_id", "decision",
          "option_id", "event", "request_id", "original_params", "modified_params",
          "changed_fields", "reason", "client_info", "record_status_before",
          "record_status_after", "mode", "prev_hash", "hash",
          "entry_hash", "record_facts_hash", "source_facts_hash", "sealed_head")
SIGNATURE_11 = ("timestamp", "agent_id", "action_type", "tool", "input_hash",
                "output_hash", "prev_hash", "entry_hash", "risk_level", "confidence",
                "status")
FORBIDDEN = ("已恢复", "已撤销", "已上链", "已冲正完成")


def run_script(argv: list[str], *, cwd: pathlib.Path | None = None,
               script: pathlib.Path = SCRIPT) -> subprocess.CompletedProcess:
    """子进程跑脚本（独立进程 = 独立解释器上下文，证明不依赖本仓运行时）。"""
    return subprocess.run([sys.executable, "-X", "utf8", str(script), *argv],
                          cwd=str(cwd) if cwd else None,
                          capture_output=True, encoding="utf-8", errors="replace")


def report_of(argv: list[str], *, cwd: pathlib.Path | None = None,
              script: pathlib.Path = SCRIPT) -> dict:
    proc = run_script(argv + ["--json"], cwd=cwd, script=script)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:  # 输出不是机器读 JSON 就是脚本自身的红，不许静默放过
        raise AssertionError(f"--json 输出不可解析 rc={proc.returncode}: {proc.stdout[:400]} "
                             f"({exc})") from exc


def check_ids(report: dict) -> dict[str, dict]:
    return {c["id"]: c for c in report["checks"]}


def seed(name: str, *, seal: bool = True, boundary: bool = True) -> SimpleNamespace:
    """用本仓 audit 造一份**真实链** + 同一次导出的 CSV + 责任认定书 meta。"""
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    path = d / "audit.jsonl"
    log = ad.AuditLog(path)
    log.append(ad.build_event("note", actor_id="local-user", note="链首条",
                              ts="2026-11-10T08:00:00Z"))
    log.append(ad.build_event("decision", actor_id="alice", action_id="act-demo-0004",
                              decision="approve", reason="同意搬家",
                              ts="2026-11-10T08:05:00Z",
                              client_info={"channel": "web", "ip": "203.0.113.7",
                                           "user_agent": "Mozilla/5.0"}))
    if boundary:
        # 边界载荷：浮点 + Unicode 键/值 + 嵌套（键排序口径的活案例）
        log.append(ad.build_event("decision", actor_id="bob", action_id="act-demo-0005",
                                  decision="reject", reason="拒绝并保留证据 ✅",
                                  ts="2026-11-10T08:10:00Z",
                                  extra={"ratio": 0.1, "阈值": "值 ✅",
                                         "nested": {"b": 1, "a": [2, 3]}}))
    sealed_head = None
    if seal:
        sealed_head = log.seal(note="R8-4 钉测封印")["sealed_head"]
    status = log.verify(raise_on_tamper=False)
    csv_path = d / "audit.csv"
    log.export_csv(csv_path)
    cert = ad.build_certificate(log.read(), status=status,
                                generated_at="2026-11-15T12:00:00Z")
    cert["certificate_text"] = list(ad.certificate_text(cert))
    cert_path = d / "cert.json"
    cert_path.write_text(ad.canonical_json({"certificate": cert}), encoding="utf-8")
    return SimpleNamespace(dir=d, log=log, path=path, csv=csv_path, cert=cert_path,
                           cert_dict=cert, head=status["head"], count=status["count"],
                           seals=status["seals"], sealed_head=sealed_head)


def lines_of(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def write_lines(path: pathlib.Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def parse_entry(path: pathlib.Path, index: int) -> dict:
    return json.loads(lines_of(path)[index])


def rewrite_entry(path: pathlib.Path, index: int, mutate) -> None:
    lines = lines_of(path)
    entry = json.loads(lines[index])
    mutate(entry)
    lines[index] = ad.canonical_json(entry)
    write_lines(path, lines)


# --------------------------------------------------------------------------- #
# 1. 口径逐字等价（脚本重算 == 本仓冻结实现）
# --------------------------------------------------------------------------- #

class CaliberEquivalenceTests(unittest.TestCase):
    """脚本内的重算实现与本仓 `audit.py` 逐字等价；两边一漂移就红。"""

    def test_genesis_and_hash_prefix_constants_are_verbatim(self) -> None:
        self.assertEqual(ad.GENESIS_HASH, ve.GENESIS_HASH)
        self.assertEqual(ad.HASH_PREFIX, ve.HASH_PREFIX)
        self.assertEqual("sha256:" + "0" * 64, ve.GENESIS_HASH)
        self.assertEqual(("hash", "entry_hash", "prev_hash"), ve.EXCLUDED_HASH_KEYS)

    def test_csv_columns_24_frozen_literal_equals_repo(self) -> None:
        self.assertEqual(24, len(ad.CSV_COLUMNS), "R8-2 基线：CSV_COLUMNS 恒 24 列")
        self.assertEqual(tuple(ad.CSV_COLUMNS), ve.CSV_COLUMNS_24,
                         "脚本里的 24 列字面量与本仓 CSV_COLUMNS 必须逐字相等")
        self.assertEqual(list(CSV_24), list(ve.CSV_COLUMNS_24))

    def test_canonical_json_matches_repo_on_boundary_payloads(self) -> None:
        payloads = [
            {"b": 1, "a": 2},
            {"z": {"y": [3, {"n": None}], "a": {"deep": {"x": 1}}}},
            {"键": "值", "emoji": "✅", "混合 key": "中文 + ✅ + \n 换行"},
            {"f": 0.1, "g": 1.0, "h": 1e20, "i": -0.0, "j": 3.5e-7},
            {"t": True, "f": False, "n": None, "s": "", "l": [], "d": {}},
            {"列表": [{"b": 1, "a": 2}, {"b": 2, "a": 1}]},
            {"嵌套键序": {"b": {"z": 1, "a": 2}, "a": [3, 1, 2]}},
            [1, 2, 3], "字符串", 12, 0.5, None, True,
        ]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:60]):
                self.assertEqual(ad.canonical_json(payload), ve.canonical_json(payload))

    def test_json_default_fallbacks_match_repo(self) -> None:
        from datetime import datetime, timezone
        cases = [datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc),
                 {3, 1, 2}, frozenset({"b", "a"}), b"\x00\x01\x02",
                 pathlib.PurePath("var/log/audit.jsonl")]
        for value in cases:
            with self.subTest(value=str(value)):
                self.assertEqual(ad.canonical_json({"v": value}),
                                 ve.canonical_json({"v": value}))

    def test_unserializable_value_rejected_by_both(self) -> None:
        self.assertRaises(ad.AuditInputError, ad.canonical_json, {"v": object()})
        self.assertRaises(ve.CaliberError, ve.canonical_json, {"v": object()})

    def test_entry_hash_matches_repo_on_real_entries(self) -> None:
        fixture = seed("caliber-entry-hash")
        prev = ad.GENESIS_HASH
        for index, raw in enumerate(lines_of(fixture.path)):
            entry = json.loads(raw)
            self.assertEqual(ad.entry_hash(entry, prev), ve.entry_hash(entry, prev),
                             f"第 {index + 1} 条 entry_hash 重算与本仓不等价")
            self.assertEqual(entry["hash"], ve.entry_hash(entry, prev),
                             f"第 {index + 1} 条与落盘 hash 不逐字相等")
            self.assertEqual(entry["hash"], ve.entry_hash(dict(entry, hash="sha256:" + "f" * 64,
                                                              entry_hash="sha256:" + "e" * 64,
                                                              prev_hash="sha256:" + "d" * 64),
                                                          prev),
                             "排除键（hash/entry_hash/prev_hash）未生效")
            prev = entry["hash"]

    def test_chain_problem_codes_are_named_verbatim(self) -> None:
        expected = {"blank_line", "unparsable_line", "entry_not_object", "chain_break",
                    "hash_mismatch", "alias_mismatch", "seq_gap", "seal_mismatch",
                    "seal_count_mismatch", "head_mismatch", "count_mismatch"}
        self.assertEqual(expected, set(ve.CHAIN_CODES),
                         "问题码名是本仓与脚本交叉验证的公共语言，不得各起一名")

    def test_caliber_version_frozen_in_script_header(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(f'CALIBER: str = "{CALIBER}"', source)
        self.assertIn(CALIBER, source.split('"""')[1], "口径版本必须写在脚本头 docstring 里")
        self.assertEqual(CALIBER, ve.CALIBER)
        proc = run_script(["--caliber"])
        self.assertEqual(0, proc.returncode)
        self.assertIn(CALIBER, proc.stdout)
        for token in ("canonical_json", "entry_hash", "sha256", "prev_hash", "24"):
            self.assertIn(token, proc.stdout, f"--caliber 必须写出 {token} 的等价重算规则")


# --------------------------------------------------------------------------- #
# 2. 纯标准库自包含 / 不 import 本仓模块（B8-4 最硬判据）
# --------------------------------------------------------------------------- #

class SelfContainedTests(unittest.TestCase):
    def test_imports_are_stdlib_only(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.fail("脚本里不许相对 import（出了仓库就断）")
                roots.add((node.module or "").split(".")[0])
        self.assertTrue(roots)
        self.assertLessEqual(roots, STDLIB_ALLOWED,
                             f"脚本只许依赖标准库，实测导入：{sorted(roots)}")
        self.assertNotIn("agent_explainer", roots, "B8-4 最硬判据：不 import 本仓模块")

    def test_no_dynamic_import_no_tempfile_no_third_party(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for token in ("__import__", "importlib", "tempfile", "subprocess",
                      "requests", "yaml", "cryptography", "reportlab", "qrcode",
                      "sys.path.insert"):
            self.assertNotIn(token, source, f"脚本里不许出现 {token}")
        statements = re.findall(r"(?m)^[ \t]*(?:from|import)[ \t]+\S+", source)
        self.assertTrue(statements)
        for stmt in statements:
            self.assertNotIn("agent_explainer", stmt,
                             f"B8-4 最硬判据：import 语句里不许出现本仓模块：{stmt}")
        self.assertNotIn('sys.path', source.split('"""')[2], "口径 docstring 之外不许动 sys.path")

    def test_script_runs_outside_repo_with_stdlib_only(self) -> None:
        outside = TMP / "outside-repo"
        outside.mkdir(parents=True, exist_ok=True)
        copy = outside / "verify_export.py"
        copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        proc = run_script(["--self-test"], cwd=outside, script=copy)
        self.assertEqual(0, proc.returncode, f"仓库外独立跑 --self-test 必须通过：{proc.stdout}"
                                            f"\n{proc.stderr}")
        self.assertIn(CALIBER, proc.stdout)
        self.assertIn("汇总: PASS", proc.stdout)
        residue = sorted(p.name for p in outside.iterdir() if p.name != "verify_export.py")
        self.assertEqual([], residue, f"--self-test 不许在仓库外留残留：{residue}")

    def test_script_writes_nothing_when_verifying_a_bundle(self) -> None:
        fixture = seed("read-only")
        before = sorted(p.name for p in fixture.dir.iterdir())
        proc = run_script(["--chain", str(fixture.path), "--csv", str(fixture.csv),
                           "--meta", str(fixture.cert),
                           "--expected-head", fixture.head,
                           "--expected-count", str(fixture.count)], cwd=fixture.dir)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(before, sorted(p.name for p in fixture.dir.iterdir()),
                         "验证脚本只读不改：跑一次不许动导出包里任何文件")
        self.assertIn("披露: 验签通过≠背书齐备。", proc.stdout)


# --------------------------------------------------------------------------- #
# 3. 验证判据逐条 PASS/FAIL（§2.2）
# --------------------------------------------------------------------------- #

class ChainVerdictTests(unittest.TestCase):
    def test_clean_bundle_every_check_passes(self) -> None:
        fixture = seed("clean-bundle")
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv),
                            "--meta", str(fixture.cert),
                            "--expected-head", fixture.head,
                            "--expected-count", str(fixture.count)])
        self.assertTrue(report["ok"], json.dumps(report["checks"], ensure_ascii=False))
        ids = check_ids(report)
        self.assertEqual(14, len(ids), "判据项固定：链 6 + CSV 4 + meta 4，少一项就是漏验")
        for cid in ("chain.hash_recompute", "chain.prev_continuity", "chain.seal_anchor",
                    "chain.head_anchor", "chain.count_anchor", "chain.head_not_sealed_head",
                    "csv.header_24_columns", "csv.encoding_bom_crlf",
                    "csv.rows_and_cells_match_chain", "csv.tail_sealed_head_verbatim",
                    "meta.qr_payload_same_source_as_head_line", "meta.root_hash_qualified",
                    "meta.honest_anchor_fields", "meta.display_discipline"):
            self.assertEqual("PASS", ids[cid]["status"], f"{cid} 应 PASS：{ids[cid]}")
        self.assertEqual(fixture.count, report["chain"]["count"])
        self.assertEqual(fixture.head, report["chain"]["head"])
        self.assertEqual(fixture.sealed_head, report["chain"]["latest_sealed_head"])

    def test_head_and_sealed_head_are_two_values_reported_apart(self) -> None:
        fixture = seed("head-vs-seal")
        report = report_of(["--chain", str(fixture.path)])
        self.assertEqual(fixture.head, report["chain"]["head"], "链尖 = 最后一条（seal 条目）的哈希")
        self.assertEqual(fixture.sealed_head, report["chain"]["latest_sealed_head"],
                         "封印锚点 = seal() 当时记下的链头，不是链尖")
        self.assertNotEqual(report["chain"]["head"], report["chain"]["latest_sealed_head"])
        self.assertEqual("PASS", check_ids(report)["chain.head_not_sealed_head"]["status"])

    def test_chain_without_seal_reports_no_anchor_and_no_qr(self) -> None:
        fixture = seed("no-seal", seal=False)
        report = report_of(["--chain", str(fixture.path)])
        self.assertTrue(report["ok"])
        self.assertIsNone(report["chain"]["latest_sealed_head"])
        self.assertEqual(0, report["chain"]["seals"])

    def test_missing_anchor_flags_skip_not_pass(self) -> None:
        fixture = seed("skips")
        report = report_of(["--chain", str(fixture.path)])
        ids = check_ids(report)
        for cid in ("chain.head_anchor", "chain.count_anchor", "csv.header_24_columns",
                    "csv.encoding_bom_crlf", "csv.rows_and_cells_match_chain",
                    "csv.tail_sealed_head_verbatim",
                    "meta.qr_payload_same_source_as_head_line", "meta.root_hash_qualified",
                    "meta.honest_anchor_fields", "meta.display_discipline"):
            self.assertEqual("SKIP", ids[cid]["status"], f"未给输入必须 SKIP，不许折成 PASS：{cid}")
        self.assertTrue(report["ok"], "SKIP 不算失败，但也不算覆盖")

    def test_middle_line_deleted_reports_seq_and_chain_break(self) -> None:
        fixture = seed("delete-line")
        lines = lines_of(fixture.path)
        del lines[1]
        write_lines(fixture.path, lines)
        proc = run_script(["--chain", str(fixture.path)])
        self.assertEqual(1, proc.returncode, proc.stdout)
        report = report_of(["--chain", str(fixture.path)])
        codes = {p["code"] for p in report["chain_problems"]}
        self.assertIn("chain_break", codes)
        self.assertIn("seq_gap", codes)
        gap = next(p for p in report["chain_problems"] if p["code"] == "seq_gap")
        self.assertIsNotNone(gap["seq"], "断裂必须报出序号，不许静默跳过")
        self.assertEqual("FAIL", check_ids(report)["chain.prev_continuity"]["status"])

    def test_one_byte_change_is_rejected(self) -> None:
        fixture = seed("one-byte")
        data = fixture.path.read_bytes()
        needle = b'"decision":"approve"'
        self.assertEqual(1, data.count(needle), "钉测注入点必须唯一")
        fixture.path.write_bytes(data.replace(needle, b'"decision":"approvf"', 1))
        proc = run_script(["--chain", str(fixture.path)])
        self.assertEqual(1, proc.returncode)
        report = report_of(["--chain", str(fixture.path)])
        self.assertIn("hash_mismatch", {p["code"] for p in report["chain_problems"]})
        self.assertEqual("FAIL", check_ids(report)["chain.hash_recompute"]["status"])

    def test_forged_sealed_head_is_rejected(self) -> None:
        fixture = seed("forge-seal")
        index = len(lines_of(fixture.path)) - 1
        forged = "sha256:" + "a" * 64
        rewrite_entry(fixture.path, index, lambda e: e.__setitem__("sealed_head", forged))
        report = report_of(["--chain", str(fixture.path)])
        codes = {p["code"] for p in report["chain_problems"]}
        self.assertIn("seal_mismatch", codes, "封印锚点被改写 → 必须报 seal_mismatch")
        self.assertEqual("FAIL", check_ids(report)["chain.seal_anchor"]["status"])
        self.assertEqual(1, run_script(["--chain", str(fixture.path)]).returncode)

    def test_forged_expected_head_is_rejected(self) -> None:
        fixture = seed("forge-head")
        forged = "sha256:" + "f" * 64
        report = report_of(["--chain", str(fixture.path), "--expected-head", forged])
        self.assertIn("head_mismatch", {p["code"] for p in report["chain_problems"]})
        self.assertEqual("FAIL", check_ids(report)["chain.head_anchor"]["status"])
        self.assertEqual(1, run_script(["--chain", str(fixture.path),
                                        "--expected-head", forged]).returncode)

    def test_forged_expected_count_is_rejected(self) -> None:
        fixture = seed("forge-count")
        report = report_of(["--chain", str(fixture.path),
                            "--expected-count", str(fixture.count + 1)])
        self.assertIn("count_mismatch", {p["code"] for p in report["chain_problems"]})
        self.assertEqual(1, run_script(["--chain", str(fixture.path),
                                        "--expected-count", str(fixture.count + 1)]).returncode)

    def test_truncation_is_invisible_in_chain_and_visible_via_anchor(self) -> None:
        fixture = seed("truncated")
        known = fixture.head
        write_lines(fixture.path, lines_of(fixture.path)[:-1])
        clean = report_of(["--chain", str(fixture.path)])
        self.assertTrue(clean["ok"], "删掉最后一条对链内是自洽的：只能靠外部锚点发现")
        self.assertEqual(fixture.count - 1, clean["chain"]["count"])
        anchored = report_of(["--chain", str(fixture.path), "--expected-head", known])
        self.assertIn("head_mismatch", {p["code"] for p in anchored["chain_problems"]})
        self.assertEqual(1, run_script(["--chain", str(fixture.path),
                                        "--expected-head", known]).returncode)

    def test_self_consistent_rewrite_is_caught_by_the_seal(self) -> None:
        """与本仓 test_audit_log.py:388 同一攻击：整段重算历史（链内自洽）→ 封印揭穿。"""
        fixture = seed("rewrite")
        entries = [json.loads(line) for line in lines_of(fixture.path)]
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
        write_lines(fixture.path, [ad.canonical_json(e) for e in entries])
        report = report_of(["--chain", str(fixture.path)])
        self.assertFalse(report["ok"])
        codes = {p["code"] for p in report["chain_problems"]}
        self.assertIn("seal_mismatch", codes)
        self.assertEqual(1, run_script(["--chain", str(fixture.path)]).returncode)

    def test_blank_line_and_unparsable_line_are_reported(self) -> None:
        fixture = seed("blank-line")
        lines = lines_of(fixture.path)
        lines.insert(1, "")
        write_lines(fixture.path, lines)
        report = report_of(["--chain", str(fixture.path)])
        self.assertIn("blank_line", {p["code"] for p in report["chain_problems"]})
        fixture2 = seed("unparsable")
        lines = lines_of(fixture2.path)
        lines[2] = "这不是 JSON"
        write_lines(fixture2.path, lines)
        report2 = report_of(["--chain", str(fixture2.path)])
        self.assertIn("unparsable_line", {p["code"] for p in report2["chain_problems"]})
        self.assertEqual(1, run_script(["--chain", str(fixture2.path)]).returncode)

    def test_alias_mismatch_between_hash_and_entry_hash_reported(self) -> None:
        fixture = seed("alias")
        rewrite_entry(fixture.path, 0, lambda e: e.__setitem__("entry_hash", "sha256:" + "c" * 64))
        report = report_of(["--chain", str(fixture.path)])
        codes = {p["code"] for p in report["chain_problems"]}
        self.assertIn("alias_mismatch", codes)

    def test_usage_errors_exit_two_not_one(self) -> None:
        fixture = seed("usage")
        self.assertEqual(2, run_script([]).returncode, "没给 --chain 是用法错误，不是验证失败")
        self.assertEqual(2, run_script(["--chain", str(fixture.dir / "nope.jsonl")]).returncode)
        self.assertEqual(2, run_script(["--chain", str(fixture.path),
                                        "--expected-head", "not-a-hash"]).returncode)
        self.assertEqual(2, run_script(["--chain", str(fixture.path),
                                        "--csv", str(fixture.dir / "nope.csv")]).returncode)

    def test_self_test_flags_failures_with_exit_code(self) -> None:
        work = TMP / "self-test"
        work.mkdir(parents=True, exist_ok=True)
        proc = run_script(["--self-test"], cwd=work, script=SCRIPT)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("汇总: PASS", proc.stdout)
        self.assertNotIn("[FAIL]", proc.stdout)
        for cid in ("self_test.chain_sample_clean", "self_test.sealed_head_anchor",
                    "self_test.canonical_json_boundary", "self_test.tamper_rejected",
                    "self_test.csv_clean", "self_test.csv_header_23_columns_rejected",
                    "self_test.meta_clean", "self_test.meta_qr_forgery_rejected",
                    "self_test.meta_root_hash_unqualified_rejected",
                    "self_test.meta_honesty_fields_rejected"):
            self.assertIn(f"[PASS] {cid}", proc.stdout, f"--self-test 缺判据 {cid}")


# --------------------------------------------------------------------------- #
# 4. 与 CSV / meta 导出面的交叉判定
# --------------------------------------------------------------------------- #

class CsvMetaTests(unittest.TestCase):
    def test_csv_header_must_be_verbatim_24_columns(self) -> None:
        fixture = seed("csv-header")
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv)])
        self.assertEqual("PASS", check_ids(report)["csv.header_24_columns"]["status"])
        header = fixture.csv.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertEqual(",".join(CSV_24), header, "CSV 表头与 R8-2 基线逐字一致")

    def test_csv_dropping_tail_column_fails(self) -> None:
        fixture = seed("csv-23")
        lines = lines_of(fixture.csv)
        write_lines(fixture.csv, [",".join(c for c in lines[0].split(",") if c != "sealed_head")]
                    + lines[1:])
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv)])
        self.assertEqual("FAIL", check_ids(report)["csv.header_24_columns"]["status"])
        self.assertEqual(1, run_script(["--chain", str(fixture.path),
                                        "--csv", str(fixture.csv)]).returncode)

    def test_csv_tail_sealed_head_must_match_chain(self) -> None:
        fixture = seed("csv-sealed-head")
        lines = lines_of(fixture.csv)
        for index, line in enumerate(lines):
            if line.startswith(f"{fixture.count},"):
                lines[index] = line.replace(fixture.sealed_head, "sha256:" + "9" * 64)
        write_lines(fixture.csv, lines)
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv)])
        self.assertEqual("FAIL", check_ids(report)["csv.tail_sealed_head_verbatim"]["status"])
        codes = [p["code"] for p in report["csv_problems"]]
        self.assertIn("csv_sealed_head_mismatch", codes)

    def test_csv_hash_columns_must_match_chain_cells(self) -> None:
        fixture = seed("csv-cells")
        lines = lines_of(fixture.csv)
        head = lines[0].split(",")
        col = head.index("prev_hash")
        row = lines[fixture.count].split(",")
        row[col] = "sha256:" + "7" * 64
        lines[fixture.count] = ",".join(row)
        write_lines(fixture.csv, lines)
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv)])
        self.assertEqual("FAIL", check_ids(report)["csv.rows_and_cells_match_chain"]["status"])

    def test_csv_without_bom_fails(self) -> None:
        fixture = seed("csv-bom")
        data = fixture.csv.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "R8-2 基线是带 BOM 的")
        fixture.csv.write_bytes(data[3:])
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv)])
        self.assertEqual("FAIL", check_ids(report)["csv.encoding_bom_crlf"]["status"])

    def test_meta_qr_payload_must_equal_head_line(self) -> None:
        fixture = seed("meta-qr")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        self.assertEqual("PASS",
                         check_ids(report)["meta.qr_payload_same_source_as_head_line"]["status"])
        self.assertEqual(fixture.sealed_head, fixture.cert_dict["qr_payload"])
        # 注入：qr_payload 与链头行分家（R8-3 构造保证被打破）
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        cert["certificate"]["qr_payload"] = "sha256:" + "b" * 64
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report2 = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        self.assertEqual("FAIL",
                         check_ids(report2)["meta.qr_payload_same_source_as_head_line"]["status"])
        codes = [p["code"] for p in report2["meta_problems"]]
        self.assertIn("meta_qr_not_same_source", codes)
        self.assertEqual(1, run_script(["--chain", str(fixture.path),
                                        "--meta", str(fixture.cert)]).returncode)

    def test_meta_sealed_head_forged_against_chain_fails(self) -> None:
        fixture = seed("meta-head")
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        cert["certificate"]["sealed_head"] = "sha256:" + "e" * 64
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        codes = [p["code"] for p in report["meta_problems"]]
        self.assertIn("meta_sealed_head_mismatch", codes)

    def test_meta_without_text_lines_reports_not_covered(self) -> None:
        fixture = seed("meta-no-text")
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        cert["certificate"].pop("certificate_text")
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        codes = [p["code"] for p in report["meta_problems"]]
        self.assertIn("meta_text_lines_absent", codes,
                      "正文行没给就判 FAIL 并写明本轮未覆盖，不许折进 PASS")

    def test_meta_root_hash_needs_the_reduction_qualifier(self) -> None:
        fixture = seed("meta-root")
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        inner = cert["certificate"]
        inner["root_hash"] = ad.sha256_ref("三段归约演示")
        inner["reduction_note"] = "三段归约"
        inner["certificate_text"] = ["root_hash(三段归约): " + inner["root_hash"],
                                     "root_hash_note: 三段归约"]
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        self.assertEqual("FAIL", check_ids(report)["meta.root_hash_qualified"]["status"])
        codes = [p["code"] for p in report["meta_problems"]]
        self.assertIn("meta_root_hash_unqualified", codes)

    def test_meta_root_hash_equals_sealed_head_is_conflation(self) -> None:
        fixture = seed("meta-root-equal")
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        inner = cert["certificate"]
        inner["root_hash"] = inner["sealed_head"]
        inner["certificate_text"] = ["root_hash(三段归约): " + inner["root_hash"],
                                     "root_hash_note: " + ad.REDUCTION_NOTE,
                                     "sealed_head: " + inner["sealed_head"],
                                     "qr_payload: " + inner["sealed_head"]]
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        codes = [p["code"] for p in report["meta_problems"]]
        self.assertIn("meta_root_hash_equals_sealed_head", codes,
                      "三段归约与封印锚点同值 = 有一处是假的，不得互冒充")

    def test_meta_honest_fields_and_forbidden_words(self) -> None:
        fixture = seed("meta-honest")
        cert = json.loads(fixture.cert.read_text(encoding="utf-8"))
        inner = cert["certificate"]
        inner["eth_tx_hash"] = "0x" + "ab" * 32
        inner["anchor_state"] = "anchored"
        inner["certificate_text"] = list(inner["certificate_text"]) + ["链上已确认，已上链。"]
        fixture.cert.write_text(ad.canonical_json(cert), encoding="utf-8")
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        codes = set(p["code"] for p in report["meta_problems"])
        self.assertIn("meta_eth_tx_hash_not_none", codes, "eth_tx_hash 必须恒 None（0.4）")
        self.assertIn("meta_anchor_state_not_unavailable", codes)
        self.assertIn("meta_forbidden_word", codes, "展示纪律禁语出现即 FAIL")
        self.assertEqual("FAIL", check_ids(report)["meta.honest_anchor_fields"]["status"])
        self.assertEqual("FAIL", check_ids(report)["meta.display_discipline"]["status"])

    def test_repo_cert_is_honest_by_construction(self) -> None:
        """R8-3 交付的认定书数据本身是诚实态：脚本对它 PASS，不靠改脚本凑绿。"""
        fixture = seed("meta-clean")
        self.assertIsNone(fixture.cert_dict["eth_tx_hash"])
        self.assertEqual(ANCHOR_STATE, fixture.cert_dict["anchor_state"])
        self.assertEqual("unavailable", ANCHOR_STATE)
        report = report_of(["--chain", str(fixture.path), "--meta", str(fixture.cert)])
        self.assertTrue(report["ok"], json.dumps(report["meta_problems"], ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 5. PM-PLAN §10.5 交叉验证：脚本判定 == 本仓 audit.verify_file 判定
# --------------------------------------------------------------------------- #

class CrossCheckWithVerifyFileTests(unittest.TestCase):
    """PM 验收命令的本地预演：同一份文件，两个实现的逐例判定必须相等。"""

    def _parity(self, path: pathlib.Path, *, expected_head: str | None = None,
                expected_count: int | None = None) -> None:
        repo = ad.verify_file(path, expected_head=expected_head,
                              expected_count=expected_count)
        argv = ["--chain", str(path)]
        if expected_head:
            argv += ["--expected-head", expected_head]
        if expected_count is not None:
            argv += ["--expected-count", str(expected_count)]
        mine = report_of(argv)
        self.assertEqual(repo["ok"], mine["chain"]["ok"], f"ok 判定不等：{path.name}")
        self.assertEqual(repo["count"], mine["chain"]["count"], f"count 不等：{path.name}")
        self.assertEqual(repo["head"], mine["chain"]["head"], f"head 不等：{path.name}")
        self.assertEqual(repo["seals"], mine["chain"]["seals"], f"seals 不等：{path.name}")
        self.assertEqual(sorted(p["code"] for p in repo["problems"]),
                         sorted(p["code"] for p in mine["chain_problems"]),
                         f"问题码集合不等：{path.name}")

    def test_parity_on_clean_and_six_tampered_fixtures(self) -> None:
        clean = seed("parity-clean")
        self._parity(clean.path, expected_head=clean.head, expected_count=clean.count)

        one_byte = seed("parity-one-byte")
        data = one_byte.path.read_bytes()
        one_byte.path.write_bytes(data.replace(b'"decision":"approve"',
                                               b'"decision":"approvf"', 1))
        self._parity(one_byte.path)

        deleted = seed("parity-delete")
        lines = lines_of(deleted.path)
        del lines[1]
        write_lines(deleted.path, lines)
        self._parity(deleted.path)

        forged_seal = seed("parity-forge-seal")
        rewrite_entry(forged_seal.path, len(lines_of(forged_seal.path)) - 1,
                      lambda e: e.__setitem__("sealed_head", "sha256:" + "a" * 64))
        self._parity(forged_seal.path)

        truncated = seed("parity-truncate")
        write_lines(truncated.path, lines_of(truncated.path)[:-1])
        self._parity(truncated.path)
        self._parity(truncated.path, expected_head=truncated.head)

        blank = seed("parity-blank")
        lines = lines_of(blank.path)
        lines.insert(2, "")
        write_lines(blank.path, lines)
        self._parity(blank.path)

        rewritten = seed("parity-rewrite")
        entries = [json.loads(line) for line in lines_of(rewritten.path)]
        entries[0]["note"] = "整段重算"
        prev = ad.GENESIS_HASH
        for index, entry in enumerate(entries):
            entry["seq"] = index + 1
            entry["prev_hash"] = prev
            digest = ad.entry_hash(entry, prev)
            entry["hash"] = digest
            entry["entry_hash"] = digest
            prev = digest
        write_lines(rewritten.path, [ad.canonical_json(e) for e in entries])
        self._parity(rewritten.path)
        self._parity(rewritten.path, expected_head=rewritten.head,
                     expected_count=rewritten.count)

    def test_head_mismatch_and_seal_mismatch_codes_reproducible(self) -> None:
        """PM 任务书验收命令点名的三个码：verify_file 与脚本都给得出、名字逐字一致。"""
        fixture = seed("codes")
        self.assertIn("head_mismatch",
                      [p["code"] for p in ad.verify_file(fixture.path, expected_head="sha256:" + "f" * 64)["problems"]])
        self.assertIn("head_mismatch",
                      [p["code"] for p in report_of(["--chain", str(fixture.path),
                                                     "--expected-head", "sha256:" + "f" * 64])["chain_problems"]])
        rewrite_entry(fixture.path, len(lines_of(fixture.path)) - 1,
                      lambda e: e.__setitem__("sealed_head", "sha256:" + "d" * 64))
        self.assertIn("seal_mismatch",
                      [p["code"] for p in ad.verify_file(fixture.path)["problems"]])
        self.assertIn("seal_mismatch",
                      [p["code"] for p in report_of(["--chain", str(fixture.path)])["chain_problems"]])


# --------------------------------------------------------------------------- #
# 6. CLI 与展示纪律
# --------------------------------------------------------------------------- #

class CliAndDisciplineTests(unittest.TestCase):
    def test_help_documents_every_flag(self) -> None:
        proc = run_script(["--help"])
        self.assertEqual(0, proc.returncode)
        for flag in ("--chain", "--csv", "--meta", "--expected-head", "--expected-count",
                     "--json", "--quiet", "--self-test", "--caliber"):
            self.assertIn(flag, proc.stdout, f"--help 必须逐项写明 {flag}")
        self.assertIn(CALIBER, " ".join(proc.stdout.split()),
                      "--help 里要能看到口径冻结版本（argparse 会折行，故按空白归一比对）")

    def test_json_output_is_machine_checkable(self) -> None:
        fixture = seed("json-out")
        report = report_of(["--chain", str(fixture.path), "--csv", str(fixture.csv),
                            "--meta", str(fixture.cert)])
        self.assertEqual(CALIBER, report["caliber"])
        self.assertEqual(list(CSV_24), report["csv_columns_24"])
        for check in report["checks"]:
            self.assertIn(check["status"], ("PASS", "FAIL", "SKIP"))
            self.assertTrue(check["detail"], f"{check['id']} 判定必须写清依据")
        self.assertEqual(24, report["csv"]["columns"])
        self.assertEqual({"ok", "count", "head", "genesis", "lines", "seals",
                          "latest_sealed_head", "latest_sealed_source"},
                         set(report["chain"]))

    def test_quiet_keeps_fails_and_summary(self) -> None:
        fixture = seed("quiet")
        lines = lines_of(fixture.path)
        del lines[1]
        write_lines(fixture.path, lines)
        proc = run_script(["--chain", str(fixture.path), "--quiet"])
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertIn("[FAIL]", proc.stdout)
        self.assertIn("汇总: FAIL", proc.stdout)
        self.assertNotIn("[PASS]", proc.stdout, "--quiet 只留汇总与 FAIL")
        self.assertIn("chain_break", proc.stdout, "quiet 模式也必须给出原因码")

    def test_stdout_carries_no_forbidden_completion_words(self) -> None:
        fixture = seed("clean-stdout")
        proc = run_script(["--chain", str(fixture.path), "--csv", str(fixture.csv),
                           "--meta", str(fixture.cert)])
        self.assertEqual(0, proc.returncode)
        for word in FORBIDDEN:
            self.assertNotIn(word, proc.stdout, f"输出里出现展示纪律禁语：{word}")
        for line in proc.stdout.splitlines():
            if "Merkle 根" in line:
                self.assertIn("非", line, f"肯定式 Merkle 根表述：{line}")
        self.assertIn("验签通过≠背书齐备。", proc.stdout)
        self.assertIn("非链头", proc.stdout, "三段归约的限定语必须随输出给出（非链头、非 Merkle 根）")

    def test_self_test_output_is_also_discipline_clean(self) -> None:
        work = TMP / "self-test-clean"
        work.mkdir(parents=True, exist_ok=True)
        proc = run_script(["--self-test"], cwd=work)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        for word in FORBIDDEN:
            self.assertNotIn(word, proc.stdout)
        self.assertIn("R8-7", proc.stdout, "干净环境实跑/实机扫码归 R8-7 的边界必须写在输出里")

    def test_signature_fields_and_columns_untouched(self) -> None:
        """§1.3 纪律：签名域与列面本轮零改动（脚本只读，不跨账本逐字比对）。"""
        self.assertEqual(11, len(SIGNATURE_FIELDS))
        self.assertEqual(SIGNATURE_11, tuple(SIGNATURE_FIELDS))
        self.assertEqual(24, len(ad.CSV_COLUMNS))
        self.assertEqual(14, len(ad._HTML_COLUMNS),
                         "R8-2 遗留第 8 项：_HTML_COLUMNS 14 列本轮不擅自同步")
        self.assertEqual(("pdf", "csv", "chain"), tuple(FORMATS), "不得新增格式枚举值")
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("SIGNATURE_FIELDS", source.split('"""')[2],
                         "脚本不验签名账本（两层账本边界），源码里不该出现 SIGNATURE_FIELDS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
