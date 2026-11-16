"""R6-3（REQ-006 B6-3 导出接线）验收测试：`/api/analytics/export` 消费预聚合数据面。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest tests.test_analytics_export -v

钉死的口径（任务书 TASK-R6-3 §2，落 docs/07 §11.16）：

1. **接线单点**：导出与界面共用 ``analytics.dashboard(agg=FeaturePaths.agg_ledger)``
   这一条数据面——CSV 里每个数字都等于 ``/api/analytics/dashboard`` 的读数；
2. **两路径同数字**：预聚合命中导出件 vs 流式扫描导出件，四个数据段**逐字节相等**；
   元数据段**必须不等**（来源如实：一个写 ``pre_aggregated=true``，一个写回退原因码）；
3. **元数据进产物**：CSV 末尾 ``meta`` 段 / HTML 元数据表 / PDF ``meta …`` 行，
   写的就是"这份文件怎么来的"（``pre_aggregated`` / ``scanned`` / 回退 ``reason``）；
4. **降级如实**（audit.py 既有 fallback 口径）：reportlab **缺失** 与 **构建失败**
   都退**可打印 HTML**（零外链零脚本，Ctrl+P 存 PDF），绝不穿着 ``.pdf`` 的文件名
   发别的格式；``X-TraceLens-Export-Format`` / ``-Fallback`` / 文件名后缀三处一致；
5. **PII 防线**：工具名 / 参数名是记录里的自由文本，导出前过 ``audit.redact_text``
   单点；导出件里扫 ``PII_PATTERNS`` 13 类必须零命中，且夹具明文必须在
   ``dashboard`` 读数里真的存在（否则测试是空跑）；
6. **零回归**：审计导出族（``AuditLog.export_csv/export_html/export_pdf``）、
   analytics 路由面（两条已知 ``path,verb``）、``SIGNATURE_FIELDS`` 11 字段一字未动。

临时目录一律 ``tests/scratch.py``；HTTP 断言级不重试（``http_fixture``）。
"""
from __future__ import annotations

import copy
import csv
import io
import json
import pathlib
import re
import shutil
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, str(_p))

from agent_explainer import analytics as an  # noqa: E402
from agent_explainer import analytics_agg as ag  # noqa: E402
from agent_explainer import audit as ad  # noqa: E402
from agent_explainer import secretariat as sec  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
import http_fixture as fx  # noqa: E402
from scratch import scratch_dir, use_scratch_tempfile  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))

#: CSV 的五个段表头（R6-3 只在末尾加 meta 段，前四段逐字不变）
SECTION_METRICS = ["metric", "value"]
SECTION_TREND = ["bucket", "tasks", "interventions", "rate"]
SECTION_HEAT = ["tool", "param", "count"]
SECTION_REJECTS = ["tool", "rejects"]
SECTION_META = ["meta", "key", "value"]
DATA_SECTIONS = (SECTION_METRICS, SECTION_TREND, SECTION_HEAT, SECTION_REJECTS)

#: 明文 PII 夹具：五类形状各来一发（邮箱 / Windows 路径 / 手机号 / IPv4 / 身份证）
PII_TOOL = r"calendar.read <alice@example.com> C:\Users\alice\notes.txt 13800138000"
PII_PARAM = "ip=10.20.30.40"
PII_NEEDLES = ("alice@example.com", "notes.txt", "13800138000", "10.20.30.40",
               "110101199003071234")


def rec(i: int, *, day: int = 12, decision: str | None = None,
        tool: str = "fs.write", fields: tuple[str, ...] = (),
        decided_minutes: int | None = 60, agent_id: str = "agent-a",
        user: str = "local-user") -> dict:
    """仪表盘形状的 ActionRecord（与 ``tests/test_analytics_agg.py`` 同一夹具口径）。"""
    out: dict = {"id": f"act-r63-{i:04d}", "agent_id": agent_id,
                 "timestamp": f"2026-11-{day:02d}T09:00:00Z",
                 "tool_used": {"name": tool}}
    if decision:
        out["user_intervention"] = {
            "decision": decision, "decided_by": user,
            "decided_at": (f"2026-11-{day:02d}T{9 + decided_minutes:02d}:00:00Z"
                           if decided_minutes is not None else None),
            "modified_fields": [{"name": name} for name in fields]}
    return out


def rich_records(count: int = 48) -> list[dict]:
    """多样夹具：3 工具 × 4 日 × 决定/无决定 × 多参数 × 多用户（与 agg 套件同族）。"""
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
    return rows[:count] if count else rows


def pii_records() -> list[dict]:
    """把五类明文 PII 塞进**自由文本**落点（工具名 / 参数名 / 决定人）。"""
    return [
        rec(1, tool=PII_TOOL, decision="reject",
            fields=(PII_PARAM, "id_card=110101199003071234"), user="alice@example.com"),
        rec(2, tool=PII_TOOL, decision="modify", fields=(PII_PARAM,),
            user="alice@example.com", day=11),
        rec(3, tool="fs.write", decision="approve", fields=("limit",), day=11),
        rec(4, tool="C:\\temp\\raw-trace.jsonl", day=12),
    ]


def ledger_path(root: pathlib.Path) -> pathlib.Path:
    return FeaturePaths(root / "features").agg_ledger


def csv_rows(text: str) -> list[list[str]]:
    """去 BOM → 解析 CSV（表头/数据一律按 CSV 语义读，不做字符串糊弄）。"""
    return [row for row in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if row]


def section_of(text: str, header: list[str]) -> list[list[str]]:
    """取 ``header`` 那一段的数据行（到空行为止）；缺段直接判红。"""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    for index, row in enumerate(rows):
        if row == header:
            out: list[list[str]] = []
            for item in rows[index + 1:]:
                if not item:
                    break
                out.append(item)
            return out
    raise AssertionError(f"CSV 缺段 {header}（前 12 行={rows[:12]}）")


def data_sections(text: str) -> list[list[list[str]]]:
    """四个数据段（指标/趋势/热力图/被拒）——两路径必须逐字节相等的那部分。"""
    return [section_of(text, header) for header in DATA_SECTIONS]


def _flat(sections: list[list[list[str]]]) -> str:
    """把分段行摊成一整段文本（逐字节比对用：行序、列数、空行全在里面）。"""
    return "\r\n".join(",".join(row) for rows in sections for row in rows)


def meta_map(text: str) -> dict[str, str]:
    """``meta`` 段 → 字典（元数据如实断言的读法）。"""
    return {row[1]: row[2] for row in section_of(text, SECTION_META)}


def pii_hits(text: str) -> list[str]:
    """用**既有** PII 防线（``audit.PII_PATTERNS`` 13 类）反扫导出件。"""
    hits: list[str] = []
    for name, pattern in ad.PII_PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append(f"{name}={match.group(0)[:48]}")
    return hits


#: 纯数字格与 sha256 摘要**不算自由文本**：``avg_approval_ms`` 实测能到 11 位，
#: 账本 ``head`` 是 64 位十六进制串（``audit.py`` 的锚定摘要豁免小节就是为这个坑写的，
#: 实测 20000 个真摘要里 11% 被 ``long_digits`` 啃过——本仓也实测翻过车：某次导出的
#: 链头里正好有一段 12 位纯数字，被 ``long_digits`` 咬住，每次运行随链头变化而偶发）。
#: 把这两类算进 PII 反扫是测试自己造出来的假红——13 条规则是给自由文本准备的。
#: 明文形状（邮箱 / 手机号 / 路径 / IPv4 / 身份证）在**整份**导出件里照旧逐个扫，
#: 见 ``PII_NEEDLES`` 断言；打码标记本身在 HTML 里以 ``&lt;redacted:path`` 形态断言存在。
NUMERICISH = re.compile(r"^-?[\d.]+$")
DIGESTISH = re.compile(r"^sha256:[0-9a-f]{6,}$")
NUM_CELL = re.compile(r'<td class="num">[^<]*</td>')
DIGEST_CELL = re.compile(r"<td>\s*sha256:[0-9a-f]{6,}\s*</td>")
NON_TEXT_META_KEYS = frozenset({
    "scanned", "users_count", "newly_aggregated", "compensation_keys",
    "ledger_rows", "problems_count", "head", "durable"})


def free_text_of_csv(text: str) -> str:
    """CSV 里承载自由文本的格子：热力图 / 被拒两段的每一格 + 非数字非摘要元数据值。"""
    cells: list[str] = []
    for row in section_of(text, SECTION_HEAT) + section_of(text, SECTION_REJECTS):
        cells.extend(row)
    for key, value in meta_map(text).items():
        if key not in NON_TEXT_META_KEYS and not DIGESTISH.match(value.strip()):
            cells.append(value)
    return "\n".join(cells)


def free_text_of_html(page: str) -> str:
    """HTML 去掉数字格与摘要格后的正文（样式与标签留着，扫的是文字面）。"""
    return DIGEST_CELL.sub("<td/>", NUM_CELL.sub('<td class="num"/>', page))


def free_text_of_pdf_lines(lines: list[str]) -> str:
    """PDF 行里只留"值既不是纯数字、也不是 sha256 摘要"的那些（值来自 ``export_meta_rows``）。"""
    kept = []
    for line in lines:
        _key, _sep, value = line.partition(": ")
        value = value.strip()
        if not NUMERICISH.match(value) and not DIGESTISH.match(value):
            kept.append(line)
    return "\n".join(kept)


def block_reportlab(reason: str = "reportlab_unavailable:ImportError"):
    """把 reportlab 打成"导入不了"（``_load_reportlab`` 的返回口径，不动 sys.modules）。"""
    return mock.patch.object(an, "_load_reportlab", lambda: (None, reason))


def break_reportlab_build():
    """reportlab 能导入、构建阶段炸（字体/版本/内存差异都在这条路径上）。"""
    class _BoomCanvas:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("canvas boom")

    namespace = {"pagesizes": types.SimpleNamespace(A4=(595.0, 842.0)),
                 "pdf_canvas": types.SimpleNamespace(Canvas=_BoomCanvas)}
    return mock.patch.object(an, "_load_reportlab", lambda: (namespace, None))


# --------------------------------------------------------------------------- #
# A. 产物格式：表头 / 行数 / 抽样字段值 / 元数据如实 / 空区间边界
# --------------------------------------------------------------------------- #

class CsvArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = rich_records()
        self.report = an.dashboard(self.records, range_from="2026-11-01",
                                   range_to="2026-11-30", granularity="day",
                                   salt="")
        self.csv = an.export_csv(self.report)

    def test_csv_sections_headers_and_row_counts(self) -> None:
        """§2.3-1：五段表头齐（前四段逐字不变）、每段行数 = 读数长度、BOM/CRLF 口径。"""
        for header in DATA_SECTIONS + (SECTION_META,):
            self.assertIn(",".join(header) + "\r\n", self.csv, f"缺表头 {header}")
        self.assertEqual(len(self.csv.split("\r\n\r\n")), 5, "段数必须是 5（四个数据段 + meta）")
        self.assertEqual(len(section_of(self.csv, SECTION_TREND)), len(self.report["trend"]))
        self.assertEqual(len(section_of(self.csv, SECTION_HEAT)), len(self.report["heatmap"]))
        self.assertEqual(len(section_of(self.csv, SECTION_REJECTS)),
                         len(self.report["top_rejected"]))
        self.assertEqual(len(section_of(self.csv, SECTION_METRICS)), 9)
        self.assertEqual(len(section_of(self.csv, SECTION_META)),
                         len(an.export_meta_rows(self.report)))
        self.assertTrue(self.csv.startswith("metric,value\r\n"), "首段首行必须还是 metric,value")

    def test_csv_sampled_values_match_dashboard_readings(self) -> None:
        """§2.3-1：抽样字段值逐条 = dashboard 读数（数字一个都不许多加工）。"""
        metrics = {row[0]: row[1] for row in section_of(self.csv, SECTION_METRICS)}
        for key in ("granularity", "total_tasks", "total_interventions",
                    "intervention_rate", "avg_approval_seconds", "avg_approval_ms",
                    "trust_health", "approved", "rejected"):
            self.assertEqual(metrics[key], str(self.report[key]), key)
        heat = [tuple(row) for row in section_of(self.csv, SECTION_HEAT)]
        self.assertEqual(heat, [(item["tool"], item["param"], str(item["count"]))
                               for item in self.report["heatmap"]])
        trend = [tuple(row) for row in section_of(self.csv, SECTION_TREND)]
        self.assertEqual(trend, [(point["bucket"], str(point["tasks"]),
                                 str(point["interventions"]), str(point["rate"]))
                                for point in self.report["trend"]])
        self.assertEqual(self.report["total_tasks"], 48)
        self.assertGreater(len(self.report["heatmap"]), 0, "夹具必须真产出热力图，否则抽样是空跑")

    def test_csv_meta_marks_preaggregated_hit(self) -> None:
        """§2.3-5：预聚合命中 → meta 段写 ``pre_aggregated=true`` + 账本态（head/rows/newly）。"""
        root = scratch_dir(f"r63-csv-meta-{self.id().rsplit('.', 1)[-1]}")
        try:
            report = an.dashboard(self.records, agg=ledger_path(root))
            self.assertTrue(report["pre_aggregated"], report["agg"])
            meta = meta_map(an.export_csv(report))
            self.assertEqual(meta["pre_aggregated"], "true")
            self.assertEqual(meta["source"], "pre_aggregated")
            self.assertEqual(meta["scanned"], str(len(self.records)))
            self.assertIn("head", meta)
            self.assertTrue(meta["head"].startswith("sha256:"), meta.get("head"))
            self.assertEqual(int(meta["ledger_rows"]), report["agg"]["ledger_rows"])
            self.assertGreater(int(meta["newly_aggregated"]), 0)
            self.assertNotIn("reason", meta, "命中路径不许写回退原因码")
            # 账本真的落盘了（导出件说的命中必须在磁盘上对得上）
            self.assertTrue(ledger_path(root).exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_csv_meta_marks_streaming_fallback_reason(self) -> None:
        """§2.3-5：回退路径 → meta 段写 ``pre_aggregated=false`` + 原因码（不装成命中）。"""
        root = scratch_dir(f"r63-csv-fallback-{self.id().rsplit('.', 1)[-1]}")
        try:
            hit = an.dashboard(self.records, agg=ledger_path(root))
            self.assertTrue(hit["pre_aggregated"])
            fallback = an.dashboard(self.records, agent="agent-a", agg=ledger_path(root))
            self.assertFalse(fallback["pre_aggregated"])
            meta = meta_map(an.export_csv(fallback))
            self.assertEqual(meta["pre_aggregated"], "false")
            self.assertEqual(meta["source"], "streaming")
            self.assertEqual(meta["reason"], "agent_filter")
            self.assertEqual(meta["analytics_version"], an.ANALYTICS_VERSION)
            self.assertEqual(meta["granularity"], "day")
            self.assertEqual(meta["range_from"], "", "没传区间就要如实留空（不许编一个）")
            self.assertEqual(meta["range_to"], "")
            self.assertEqual(meta["users_count"],
                             str(self.report["users"]["count"]))
            self.assertNotIn("head", meta, "流式路径没有账本链头可写")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_csv_empty_range_is_honest_zero(self) -> None:
        """§2.3-7：空区间 → 数字如实 0、表头不缺席、meta 的 from/to 如实、趋势段空。"""
        empty = an.dashboard(self.records, range_from="2027-01-01", range_to="2027-01-31")
        text = an.export_csv(empty)
        metrics = {row[0]: row[1] for row in section_of(text, SECTION_METRICS)}
        for key in ("total_tasks", "total_interventions", "approved", "rejected"):
            self.assertEqual(metrics[key], "0", key)
        self.assertEqual(metrics["intervention_rate"], "0.0")
        self.assertEqual(metrics["avg_approval_seconds"], "0.0")
        self.assertEqual(metrics["trust_health"], "1.0")   # 0 干预 → 信任健康度满分（公式口径）
        self.assertEqual(section_of(text, SECTION_TREND), [])
        self.assertIn(",".join(SECTION_TREND) + "\r\n", text, "空趋势也要留着表头")
        meta = meta_map(text)
        self.assertEqual(meta["range_from"], "2027-01-01")
        self.assertEqual(meta["range_to"], "2027-01-31")
        self.assertEqual(meta["scanned"], str(len(self.records)))
        self.assertEqual(meta["pre_aggregated"], "false")
        self.assertEqual(meta["reason"], "no_ledger_wired")

    def test_csv_served_bytes_carry_bom_and_digest_of_same_bytes(self) -> None:
        """BOM 由调用方决定（与审计 CSV 同口径，Excel 认中文）；指纹按实发字节算。"""
        served = ("\ufeff" + self.csv).encode("utf-8")
        self.assertEqual(served[:3], b"\xef\xbb\xbf")
        self.assertEqual(served.decode("utf-8-sig"), self.csv)
        digest = ad.sha256_ref(served.decode("latin-1"))
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(digest, ad.sha256_ref(served.decode("latin-1")),
                         "同一份字节必须算出同一个指纹（接收方复核的根据）")


# --------------------------------------------------------------------------- #
# B. 降级路径：reportlab 缺失 / 构建失败 → 可打印 HTML（如实标注产物类型）
# --------------------------------------------------------------------------- #

class DegradationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = an.dashboard(rich_records(16), range_from="2026-11-01",
                                   range_to="2026-11-30")

    def test_pdf_is_real_pdf_when_reportlab_present(self) -> None:
        """reportlab 在场 → 真 PDF（``%PDF-`` 头）+ outcome.ok=true + 无降级原因。"""
        namespace, reason = an._load_reportlab()
        if namespace is None:                       # 环境事实：这台机器没装 reportlab
            self.skipTest(f"本机 reportlab 不可用：{reason}")
        outcome: dict = {}
        pdf = an.export_pdf(self.report, lang="zh", outcome=outcome)
        self.assertIsNotNone(pdf, f"本机 reportlab 可用却拿不到 PDF：{outcome}")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(outcome["ok"], outcome)
        self.assertIsNone(outcome["reason"])
        self.assertIn("meta pre_aggregated: false", an._pdf_lines(self.report))

    def test_pdf_missing_reportlab_reports_reason(self) -> None:
        """reportlab 导入不了 → ``None`` + ``reportlab_unavailable:<类型>``（不抛、不 500）。"""
        namespace, reason = an._load_reportlab()
        if namespace is None:
            self.skipTest(f"本机 reportlab 不可用：{reason}")
        self.assertIsNone(reason)
        with block_reportlab():
            outcome: dict = {}
            self.assertIsNone(an.export_pdf(self.report, outcome=outcome))
            self.assertFalse(outcome["ok"])
            self.assertEqual(outcome["reason"], "reportlab_unavailable:ImportError")

    def test_reportlab_detection_honours_sys_modules(self) -> None:
        """检测口径本身：真把 reportlab 从 ``sys.modules`` 摘掉，也必须报"不可用"。"""
        namespace, reason = an._load_reportlab()
        if namespace is None:
            self.skipTest(f"本机 reportlab 不可用：{reason}")
        # 上一行刚把 reportlab 家族导入进 sys.modules，此刻摘掉才测得出"导入不了"
        saved = {key: value for key, value in sys.modules.items()
                 if key == "reportlab" or key.startswith("reportlab.")}
        self.assertTrue(saved, "夹具前提：reportlab 家族必须已进 sys.modules")
        try:
            for key in list(sys.modules):
                if key == "reportlab" or key.startswith("reportlab."):
                    sys.modules[key] = None          # 导入系统对 None 占位直接 ImportError
            namespace, reason = an._load_reportlab()
            self.assertIsNone(namespace)
            self.assertTrue(str(reason).startswith("reportlab_unavailable:"), reason)
            self.assertIsNone(an.export_pdf(self.report))
        finally:
            for key in list(sys.modules):
                if key == "reportlab" or key.startswith("reportlab."):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_pdf_build_failure_also_degrades(self) -> None:
        """reportlab 可用但构建炸 → 同样降级，原因码 ``reportlab_build_failed:<类型>``。"""
        with break_reportlab_build():
            outcome: dict = {}
            self.assertIsNone(an.export_pdf(self.report, outcome=outcome))
            self.assertFalse(outcome["ok"])
            self.assertTrue(str(outcome["reason"]).startswith("reportlab_build_failed:"),
                            outcome["reason"])
            self.assertIn("构建 PDF 失败", outcome["note"])

    def test_export_pdf_signature_stays_back_compatible(self) -> None:
        """老调用点不传 ``outcome`` 逐字可用：新增的是**可选**出参，返回值语义不变。"""
        with block_reportlab():
            self.assertIsNone(an.export_pdf(self.report))               # 位置参数、无出参
            self.assertIsNone(an.export_pdf(self.report, lang="en"))    # 老关键字调用
            outcome: dict = {}
            self.assertIsNone(an.export_pdf(self.report, outcome=outcome))
            self.assertFalse(outcome["ok"])
        namespace, _reason = an._load_reportlab()
        served = an.export_pdf(self.report)
        self.assertEqual(namespace is not None, served is not None,
                         "返回值必须与本机 reportlab 的可用性一致（没装也不许装绿）")

    def test_html_is_printable_self_contained(self) -> None:
        """§2.3-2：HTML 降级件零外链零脚本、charset 在位、元数据与指标都在。"""
        page = an.export_html(self.report,
                              extra_note="reportlab_unavailable:ImportError：已生成可打印 HTML")
        for needle in ("<script", "</script", "src=", "href=", "http://", "https://",
                       "@import", "url("):
            self.assertNotIn(needle, page, f"可打印 HTML 出现了 {needle}")
        self.assertIn('<meta charset="utf-8">', page)
        self.assertIn("reportlab_unavailable:ImportError", page)
        self.assertIn("<td>pre_aggregated</td><td>false</td>", page)
        self.assertIn(f"<td>total_tasks</td><td class=\"num\">{self.report['total_tasks']}</td>",
                      page)
        self.assertIn("Ctrl+P", page)
        self.assertEqual(page.count("<table>"), 5, "元数据+指标+趋势+热力图+被拒 = 5 张表")

    def test_html_escapes_record_text(self) -> None:
        """记录里的自由文本进 HTML 必须转义（导出的东西会被浏览器打开）。"""
        report = an.dashboard([rec(1, tool="<img onerror=alert(1)>", decision="reject",
                                   fields=("<svg onload=1>",))])
        page = an.export_html(report)
        self.assertNotIn("<img", page)
        self.assertNotIn("<svg", page)
        self.assertIn("&lt;img", page)

    def test_pdf_lines_are_ascii_only_and_carry_metadata(self) -> None:
        """PDF 正文只走 ASCII（Helvetica 无中文字形，中文会静默丢）+ 元数据行在位。"""
        lines = an._pdf_lines(self.report)
        for line in lines:
            self.assertTrue(line.isascii(), line)
        joined = "\n".join(lines)
        for key in ("meta source:", "meta pre_aggregated:", "meta scanned:",
                    "meta analytics_version:", "meta users_count:"):
            self.assertIn(key, joined, key)
        for point in self.report["trend"]:
            self.assertIn(f"trend {point['bucket']}:", joined)


# --------------------------------------------------------------------------- #
# C. 两路径产物一致 + 脱敏一致性（单元级，不依赖 HTTP）
# --------------------------------------------------------------------------- #

class TwoPathArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = rich_records()
        self.root = scratch_dir(f"r63-two-path-{self.id().rsplit('.', 1)[-1]}")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _reports(self) -> tuple[dict, dict]:
        hit = an.dashboard(copy.deepcopy(self.records), agg=ledger_path(self.root),
                           range_from="2026-11-01", range_to="2026-11-30")
        stream = an.dashboard(copy.deepcopy(self.records), agent="agent-a",
                              agg=ledger_path(self.root),
                              range_from="2026-11-01", range_to="2026-11-30")
        self.assertTrue(hit["pre_aggregated"], hit["agg"])
        self.assertFalse(stream["pre_aggregated"])
        self.assertEqual(stream["agg"]["reason"], "agent_filter")
        return hit, stream

    def test_csv_data_sections_identical_across_paths(self) -> None:
        """§2.3-3：预聚合命中 vs 流式扫描 → CSV 四个数据段逐字节相等（数字不分两套口径）。"""
        hit, stream = self._reports()
        hit_csv, stream_csv = an.export_csv(hit), an.export_csv(stream)
        self.assertEqual(data_sections(hit_csv), data_sections(stream_csv))
        self.assertEqual(_flat(data_sections(hit_csv)), _flat(data_sections(stream_csv)),
                         "整段文本也比一遍：连空行与列数都不许多出来")

    def test_csv_meta_sections_differ_honestly(self) -> None:
        """§2.3-3：元数据段必须**不等**——同一份数字，来源两条都要如实写。"""
        hit, stream = self._reports()
        hit_meta = meta_map(an.export_csv(hit))
        stream_meta = meta_map(an.export_csv(stream))
        self.assertEqual(hit_meta["pre_aggregated"], "true")
        self.assertEqual(stream_meta["pre_aggregated"], "false")
        self.assertEqual(hit_meta["scanned"], stream_meta["scanned"])
        self.assertIn("reason", stream_meta)
        self.assertNotIn("reason", hit_meta)

    def test_html_and_pdf_lines_identical_across_paths(self) -> None:
        """HTML 去掉元数据表与时间戳后逐字节相等；PDF 行同理（元数据/时刻各归元数据）。"""
        hit, stream = self._reports()

        def stable_html(report: dict) -> str:
            text = an.export_html(report)
            text = re.sub(r"<h2>导出元数据</h2>\n<table>.*?</table>\n", "", text, flags=re.S)
            return re.sub(r'<p class="note">.*?</p>\n', "", text, flags=re.S)

        self.assertEqual(stable_html(hit), stable_html(stream))
        self.assertNotEqual(an.export_html(hit), an.export_html(stream),
                           "元数据表本身必须不同（来源如实）")

        def stable_pdf_lines(report: dict) -> list[str]:
            return [line for line in an._pdf_lines(report)
                    if not line.startswith("meta ") and not line.startswith("generated_at")]

        self.assertEqual(stable_pdf_lines(hit), stable_pdf_lines(stream))

    def test_exported_bytes_carry_no_plaintext_pii(self) -> None:
        """§2.3-4：导出件里 PII 13 类零命中；明文在夹具读数里必须真存在（不是空跑）。"""
        report = an.dashboard(pii_records(), range_from="2026-11-01",
                              range_to="2026-11-30")
        # 夹具前提：明文确实进了读数（未脱敏的原始面），导出侧才有活要干
        hay = json.dumps(report, ensure_ascii=False)
        self.assertIn("alice@example.com", hay, "夹具的明文没进读数 = 这条测试是空跑")
        self.assertIn("notes.txt", hay)

        text = an.export_csv(report)
        self.assertEqual(pii_hits(free_text_of_csv(text)), [],
                         pii_hits(free_text_of_csv(text)))
        for needle in PII_NEEDLES:
            self.assertNotIn(needle, text, needle)
        self.assertIn("<redacted:path", text, "Windows 路径必须被打码成既有标记")
        self.assertIn("10.20.*.*", text, "IPv4 按既有口径只到 /16")
        self.assertIn("138****8000", text, "手机号按既有口径前 3 后 4")
        self.assertNotIn("local-user", text)

        page = an.export_html(report)
        self.assertEqual(pii_hits(free_text_of_html(page)), [],
                         pii_hits(free_text_of_html(page)))
        for needle in PII_NEEDLES:
            self.assertNotIn(needle, page, needle)
        self.assertIn("&lt;redacted:path", page,
                      "HTML 里打码标记以转义形态存在（redact_text + html.escape 两道都在干活）")

        lines = an._pdf_lines(report)
        self.assertEqual(pii_hits(free_text_of_pdf_lines(lines)), [],
                         pii_hits(free_text_of_pdf_lines(lines)))
        # 反扫不许是空跑：未脱敏的原始读数里必须真的扫得到 PII 形状
        raw = json.dumps([report["heatmap"], report["top_rejected"]], ensure_ascii=False)
        self.assertTrue(pii_hits(raw), f"夹具自由文本里没有 PII 形状：{raw[:200]}")

    def test_users_surface_is_count_only_and_ids_anonymised(self) -> None:
        """§2.3-4：导出件用户面只有计数；界面侧 id 是 ``sha256(salt+id)[:12]``，无明文。"""
        report = an.dashboard(pii_records(), range_from="2026-11-01", range_to="2026-11-30",
                              salt="instance-salt")
        text = an.export_csv(report)
        self.assertNotIn("alice@example.com", text)
        self.assertEqual(meta_map(text)["users_count"], str(report["users"]["count"]))
        self.assertNotIn("users", dict(an.export_meta_rows(report)),
                         "产物里不列用户 id 清单（report_snapshot 同口径）")
        for user_id in report["users"]["ids"]:
            self.assertEqual(12, len(user_id))
            self.assertTrue(re.fullmatch(r"[0-9a-f]{12}", user_id), user_id)
        self.assertIn(an.anonymise("alice@example.com", salt="instance-salt"),
                      report["users"]["ids"], "界面侧 id 必须是 sha256(salt+id)[:12] 那一个")
        self.assertEqual(len(set(report["users"]["ids"])), report["users"]["count"])
        for key, value in an.export_meta_rows(report):
            self.assertNotRegex(str(value), r"\b[0-9a-f]{12}\b",
                                f"元数据 {key} 里冒出了脱敏用户 id（产物只该有计数）")


# --------------------------------------------------------------------------- #
# D. HTTP 接线：/api/analytics/export 实发面（产品服务器，127.0.0.1 环回）
# --------------------------------------------------------------------------- #

class ExportHttpTests(unittest.TestCase):
    """产品口径服务器上的导出接线：CSV 与 dashboard 同读数、头如实、降级如实。"""

    def records(self) -> list[dict]:
        return rich_records()

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"r63-export-http-{case}")
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

    def export(self, query: str = "format=csv") -> tuple[int, dict, bytes]:
        return self.raw_get(f"/api/analytics/export?{query}")

    def analytics(self, query: str = "") -> dict:
        status, _hd, raw = self.raw_get(f"/api/analytics/dashboard{query}")
        self.assertEqual(200, status, raw[:200])
        return json.loads(raw.decode("utf-8"))["analytics"]

    def test_csv_matches_dashboard_readings_over_http(self) -> None:
        """§2.3-3：同一批参数下，CSV 每个指标 = dashboard JSON 读数（同一个数）。"""
        report = self.analytics("?granularity=day")
        status, headers, body = self.export("format=csv&granularity=day")
        self.assertEqual(200, status)
        self.assertEqual("text/csv; charset=utf-8", headers["Content-Type"])
        self.assertEqual("csv", headers["X-TraceLens-Export-Format"])
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        text = body.decode("utf-8-sig")
        metrics = {row[0]: row[1] for row in section_of(text, SECTION_METRICS)}
        for key in ("total_tasks", "total_interventions", "intervention_rate",
                    "avg_approval_seconds", "avg_approval_ms", "trust_health",
                    "approved", "rejected", "granularity"):
            self.assertEqual(metrics[key], str(report[key]), key)
        self.assertEqual([tuple(row) for row in section_of(text, SECTION_TREND)],
                         [(point["bucket"], str(point["tasks"]),
                           str(point["interventions"]), str(point["rate"]))
                          for point in report["trend"]])
        self.assertEqual(meta_map(text)["pre_aggregated"], "true",
                         "首跑引导聚合后必须命中（预聚合数据面在 HTTP 上真的接上）")
        self.assertEqual(headers["X-TraceLens-Export-Pre-Aggregated"], "true")

    def test_headers_are_honest_self_consistent(self) -> None:
        """§2.3-2：文件名后缀 = 实发格式 = Content-Type；指纹按实发字节算；无 store。"""
        status, headers, body = self.export("format=csv")
        self.assertEqual(200, status)
        self.assertTrue(headers["Content-Disposition"].endswith('.csv"'),
                        headers["Content-Disposition"])
        self.assertEqual("attachment", headers["Content-Disposition"].split(";")[0].strip())
        self.assertEqual(ad.sha256_ref(body.decode("latin-1")),
                         headers["X-TraceLens-Export-Sha256"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertNotIn("X-TraceLens-Export-Fallback", headers, "没降级就不许发降级头")
        self.assertTrue(headers["X-TraceLens-Export-Note"].isascii(),
                        "响应头必须 ASCII（中文按 UTF-8 百分号转义）")
        self.assertGreaterEqual(int(headers["X-TraceLens-Export-Scanned"]),
                                len(self.records()))

    def test_pdf_request_serves_pdf_when_reportlab_available(self) -> None:
        """reportlab 在场：请求 pdf 就出真 PDF，inline + ``.pdf`` 后缀 + 无降级头。"""
        namespace, reason = an._load_reportlab()
        if namespace is None:                       # 环境事实：这台机器没装 reportlab
            self.skipTest(f"本机 reportlab 不可用：{reason}")
        status, headers, body = self.export("format=pdf")
        self.assertEqual(200, status)
        self.assertEqual("application/pdf", headers["Content-Type"])
        self.assertEqual("pdf", headers["X-TraceLens-Export-Format"])
        self.assertTrue(body.startswith(b"%PDF-"))
        self.assertTrue(headers["Content-Disposition"].endswith('.pdf"'))
        self.assertIn("inline", headers["Content-Disposition"])
        self.assertNotIn("X-TraceLens-Export-Fallback", headers)

    def test_pdf_request_degrades_to_honest_html_over_http(self) -> None:
        """§2.3-2：reportlab 不可用 → 实发 HTML，三处标注一致，绝不用 .pdf 文件名。"""
        with block_reportlab():
            status, headers, body = self.export("format=pdf")
        self.assertEqual(200, status, body[:200])
        self.assertEqual("text/html; charset=utf-8", headers["Content-Type"])
        self.assertEqual("html", headers["X-TraceLens-Export-Format"])
        self.assertEqual("reportlab_unavailable:ImportError",
                         headers["X-TraceLens-Export-Fallback"])
        self.assertTrue(headers["Content-Disposition"].endswith('.html"'),
                        "文件名后缀必须跟着实发格式走（旧行为发的是 .pdf 文件名 + CSV 字节）")
        self.assertNotIn(".pdf", headers["Content-Disposition"])
        text = body.decode("utf-8")
        self.assertIn('<meta charset="utf-8">', text)
        self.assertIn("reportlab_unavailable:ImportError", text, "降级说明要跟着文件走")
        self.assertIn(f"<td>total_tasks</td><td class=\"num\">{len(self.records())}</td>", text)
        self.assertIn("<td>pre_aggregated</td>", text)
        for needle in ("<script", "src=", "href=", "http://"):
            self.assertNotIn(needle, text)

    def test_pdf_build_failure_degrades_too(self) -> None:
        """构建失败也走同一条降级路（旧行为会 500：reportlab 版本/字体坑不该炸 GET）。"""
        with break_reportlab_build():
            status, headers, body = self.export("format=pdf")
        self.assertEqual(200, status, body[:200])
        self.assertEqual("html", headers["X-TraceLens-Export-Format"])
        self.assertTrue(headers["X-TraceLens-Export-Fallback"]
                        .startswith("reportlab_build_failed:"),
                        headers["X-TraceLens-Export-Fallback"])

    def test_format_param_shape_stays_frozen(self) -> None:
        """T27 冻结形状：``format`` 只认 csv/pdf（大小写与空格宽容），其余 400 如实。"""
        status, headers, _body = self.export("format=PDF&granularity=day")
        self.assertEqual(200, status)
        self.assertIn(headers["X-TraceLens-Export-Format"], ("pdf", "html"))
        status, _hd, body = self.export("format=html")
        self.assertEqual(400, status)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual("ANALYTICS_FORMAT_INVALID", data["code"])
        self.assertEqual("format", data["detail"]["field"])
        self.assertEqual(["csv", "pdf"], data["detail"]["allowed"])
        status, _hd, body = self.export("format=json")
        self.assertEqual(400, status, body[:200])
        self.assertEqual("ANALYTICS_FORMAT_INVALID",
                         json.loads(body.decode("utf-8"))["code"])
        status, headers, body = self.export("format=")
        self.assertEqual(200, status, body[:200])
        self.assertEqual("csv", headers["X-TraceLens-Export-Format"], "空值＝默认 csv 不报错")

    def test_bad_range_is_honest_400_not_500(self) -> None:
        """参数不合法 → 400 ``ANALYTICS_RANGE_INVALID``（分析域错误码是契约，不是 500）。"""
        status, _hd, body = self.export("format=csv&from=2026-13-99")
        self.assertEqual(400, status, body[:200])
        data = json.loads(body.decode("utf-8"))
        self.assertEqual("ANALYTICS_RANGE_INVALID", data["code"])
        self.assertEqual("from/to", data["detail"]["field"])
        status, _hd, body = self.export("format=csv&from=2026-11-20&to=2026-11-10")
        self.assertEqual(400, status, body[:200])
        self.assertEqual("ANALYTICS_RANGE_INVALID",
                         json.loads(body.decode("utf-8"))["code"])

    def test_empty_range_export_over_http_is_honest(self) -> None:
        """§2.3-7：HTTP 上空区间 → 200 + 全 0 + meta 如实（不报"导出成功"的假绿数字）。"""
        report = self.analytics("?from=2027-01-01&to=2027-01-31")
        self.assertEqual(0, report["total_tasks"])
        status, _hd, body = self.export("format=csv&from=2027-01-01&to=2027-01-31")
        self.assertEqual(200, status)
        text = body.decode("utf-8-sig")
        metrics = {row[0]: row[1] for row in section_of(text, SECTION_METRICS)}
        self.assertEqual("0", metrics["total_tasks"])
        self.assertEqual(section_of(text, SECTION_TREND), [])
        meta = meta_map(text)
        self.assertEqual(meta["range_from"], "2027-01-01")
        self.assertEqual(meta["granularity"], "day")

    def test_agent_filtered_export_matches_dashboard_http(self) -> None:
        """agent 过滤走回退：导出件的数 = dashboard 同参数读数，元数据如实标回退。"""
        agents = {str(row.get("agent_id") or "") for row in self.records()}
        agent = sorted(agents)[-1]
        report = self.analytics(f"?agent={agent}")
        status, headers, body = self.export(f"format=csv&agent={agent}")
        self.assertEqual(200, status)
        text = body.decode("utf-8-sig")
        metrics = {row[0]: row[1] for row in section_of(text, SECTION_METRICS)}
        self.assertEqual(metrics["total_tasks"], str(report["total_tasks"]))
        self.assertEqual("false", headers["X-TraceLens-Export-Pre-Aggregated"])
        self.assertEqual("false", meta_map(text)["pre_aggregated"])
        self.assertEqual("agent_filter", meta_map(text)["reason"])

    def test_two_paths_produce_identical_bytes_over_http(self) -> None:
        """§2.3-3：同一数据两条路径的导出件（去掉时刻行）逐字节相等——数字不分两套口径。"""
        hit = self.export("format=csv&granularity=day")[2].decode("utf-8-sig")
        stream = self.export("format=csv&granularity=day&agent=agent-a")[2].decode("utf-8-sig")
        strip = lambda text: "\r\n".join(  # noqa: E731 - 局部小函数，只剥掉时刻行
            line for line in text.split("\r\n") if "meta,generated_at," not in line)
        self.assertEqual(data_sections(hit), data_sections(stream))
        self.assertNotEqual(strip(hit), strip(stream), "meta 段必须不同（来源如实）")

    def test_export_is_read_only(self) -> None:
        """铁律：导出只读——导出前后账本字节前缀不变、记录与审计文件零增长。"""
        ledger = FeaturePaths(self.root / "features").agg_ledger
        self.export("format=csv")
        before = ledger.read_bytes() if ledger.exists() else b""
        audit_path = self.root / "audit.jsonl"
        audit_before = audit_path.stat().st_size if audit_path.exists() else 0
        records_path = self.root / "records.jsonl"
        records_before = records_path.stat().st_size
        for query in ("format=csv", "format=pdf", "format=csv&granularity=week",
                      "format=csv&from=2027-01-01"):
            self.assertEqual(200, self.export(query)[0], query)
        after = ledger.read_bytes() if ledger.exists() else b""
        self.assertTrue(after.startswith(before), "账本必须只追加（前缀逐字不变）")
        self.assertEqual(audit_before, audit_path.stat().st_size if audit_path.exists() else 0,
                         "导出不得追加审计记录（R6-3 铁律：导出只读）")
        self.assertEqual(records_before, records_path.stat().st_size)

    def test_export_never_lies_about_artifact_type(self) -> None:
        """不变量：任何 format 请求下，文件名后缀 / Content-Type / Format 头三处必须一致。"""
        for query in ("format=csv", "format=pdf", "format=CSV", "format=Pdf"):
            status, headers, body = self.export(query)
            self.assertEqual(200, status, f"{query}: {body[:120]}")
            served = headers["X-TraceLens-Export-Format"]
            self.assertTrue(headers["Content-Disposition"].endswith(f'.{served}"'),
                            f"{query} → {headers['Content-Disposition']} vs {served}")
            expected = {"csv": "text/csv; charset=utf-8",
                        "html": "text/html; charset=utf-8",
                        "pdf": "application/pdf"}[served]
            self.assertEqual(expected, headers["Content-Type"], query)
            self.assertEqual(ad.sha256_ref(body.decode("latin-1")),
                             headers["X-TraceLens-Export-Sha256"], query)


class PiiHttpTests(ExportHttpTests):
    """明文 PII 夹具下的导出面（继承 HTTP 接线口径，逐条在脏数据上再跑一遍）。"""

    def records(self) -> list[dict]:
        return pii_records()

    def test_no_plaintext_pii_leaves_through_http(self) -> None:
        """§2.3-4：CSV / HTML 降级件零明文 PII；PII 13 类反扫零命中。"""
        status, _hd, body = self.export("format=csv")
        self.assertEqual(200, status)
        text = body.decode("utf-8-sig")
        self.assertIn("alice@example.com",
                      json.dumps(self.analytics(), ensure_ascii=False),
                      "夹具明文必须进了读数（否则下面的零命中断言是空跑）")
        self.assertEqual(pii_hits(free_text_of_csv(text)), [],
                         pii_hits(free_text_of_csv(text)))
        for needle in PII_NEEDLES:
            self.assertNotIn(needle, text, needle)
        self.assertIn("<redacted:path", text)
        with block_reportlab():
            status, _hd, raw = self.export("format=pdf")
        page = raw.decode("utf-8")
        self.assertEqual(200, status)
        self.assertEqual(pii_hits(free_text_of_html(page)), [],
                         pii_hits(free_text_of_html(page))[:3])
        for needle in PII_NEEDLES:
            self.assertNotIn(needle, page, needle)


class RegressionTests(unittest.TestCase):
    """§2.3-8：既有导出族 / 签名面 / 路由面零回归。"""

    def setUp(self) -> None:
        self.root = scratch_dir(f"r63-regression-{self.id().rsplit('.', 1)[-1]}")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_audit_export_family_untouched(self) -> None:
        """审计导出三件套（CSV/HTML/PDF + 降级）逐字可用：R6-3 没碰 audit.py。"""
        log = ad.open_log(self.root / "audit.jsonl")
        log.append(ad.build_event(
            "note", actor_id="local", actor_role="local",
            reason="改一行 C:\\Users\\alice\\notes.txt alice@example.com 13800138000"))
        rows = log.read()
        self.assertEqual(1, len(rows))
        csv_result = log.export_csv(self.root / "a.csv", rows=rows)
        self.assertEqual("csv", csv_result.format)
        self.assertEqual(b"\xef\xbb\xbf", (self.root / "a.csv").read_bytes()[:3])
        self.assertEqual(1, csv_result.rows)
        html_result = log.export_html(self.root / "a.html", rows=rows)
        self.assertEqual("html", html_result.format)
        page = (self.root / "a.html").read_text(encoding="utf-8")
        for needle in ("<script", "http://", "alice@example.com", "notes.txt"):
            self.assertNotIn(needle, page, needle)
        pdf_result = log.export_pdf(self.root / "a.pdf", rows=rows)
        namespace, reason = ad._load_reportlab()
        if namespace is None:
            self.assertEqual("html", pdf_result.format, reason)
            self.assertTrue(pdf_result.fallback)
        else:
            self.assertEqual("pdf", pdf_result.format)
            self.assertIsNone(pdf_result.fallback)
            self.assertTrue((self.root / "a.pdf").read_bytes().startswith(b"%PDF-"))
        with mock.patch.object(ad, "_load_reportlab", lambda: (None, "reportlab_unavailable:X")):
            degraded = log.export_pdf(self.root / "b.pdf", rows=rows)
        self.assertEqual("html", degraded.format)
        self.assertIn("reportlab_unavailable:X", str(degraded.fallback))
        # 审计侧的 PII 防线在**写盘之前**（既有口径）：读回来的条目本身就该是打码后的
        self.assertIn("<redacted:path", json.dumps(rows, ensure_ascii=False), rows)
        self.assertNotIn("alice@example.com", json.dumps(rows, ensure_ascii=False))

    def test_signature_fields_and_routes_untouched(self) -> None:
        """``SIGNATURE_FIELDS`` 11 字段逐字；analytics 路由面仍是两条已知 (path,verb)。"""
        self.assertEqual(("timestamp", "agent_id", "action_type", "tool", "input_hash",
                          "output_hash", "prev_hash", "entry_hash", "risk_level",
                          "confidence", "status"), tuple(sec.SIGNATURE_FIELDS))
        routes = {(route["path"], route["verb"]) for route in CONTRACT["routes"]}
        self.assertEqual({("/api/analytics/dashboard", "GET"),
                          ("/api/analytics/export", "GET")},
                         {(p, v) for (p, v) in routes if p.startswith("/api/analytics")},
                         "R6-3 不新增公开路由（导出接线挂在既有端点上）")

    def test_modules_self_check_green(self) -> None:
        """analytics / analytics_agg 自检在替身临时目录下全绿（含新增 HTML/元数据断言）。"""
        with use_scratch_tempfile():
            self.assertEqual(0, an._self_check())
            self.assertEqual(0, ag._self_check())

    def test_native_sample_export_end_to_end(self) -> None:
        """真夹具（samples/action_records.json）跑一遍：CSV 可解析、数与 dashboard 一致。"""
        report = an.dashboard(copy.deepcopy(NATIVE))
        text = an.export_csv(report)
        self.assertEqual(report["total_tasks"],
                         int({r[0]: r[1] for r in section_of(text, SECTION_METRICS)}
                             ["total_tasks"]))
        self.assertEqual(pii_hits(free_text_of_csv(text)), [],
                         pii_hits(free_text_of_csv(text))[:3])
        self.assertEqual(meta_map(text)["pre_aggregated"], "false",
                         "没接账本就必须如实标流式")

    def test_pii_sweep_scope_is_a_surgical_cut(self) -> None:
        """反扫范围的两条豁免（数字格 / sha256 摘要）本身要有锚，而且必须是精准切除。

        为什么单独立例：链头是 64 位十六进制，实测会咬出 12 位纯数字
        （``long_digits=756725011925``，某次导出实测复现），每次运行随链头变化而偶发；
        ``avg_approval_ms`` 也能到 11 位。豁免它们是对的，但豁免不许把真 PII 一起关掉——
        所以这里同时断言"整页扫扫得到"与"明文邮箱扫得到"。
        """
        digest = "sha256:" + "756725011925" * 5 + "abcd"      # 12 位纯数字段：long_digits 咬得住
        masked = ("calendar.read &lt;a***@example.com&gt; "
                  "&lt;redacted:path#sha256:9333d196b9c8&gt; 138****8000")
        page = ("<table>"
                f"<tr><td>head</td><td>{digest}</td></tr>"
                f"<tr><td>tool</td><td>{masked}</td></tr>"
                '<tr><td>num</td><td class="num">25942449397</td></tr>'
                "</table>")
        self.assertEqual(pii_hits(free_text_of_html(page)), [],
                         pii_hits(free_text_of_html(page))[:3])
        self.assertTrue(pii_hits(page),
                        "整页必须扫得到摘要数字：证明豁免是精准切除，不是把 13 类整体关掉")
        self.assertEqual(pii_hits(free_text_of_pdf_lines(
            [f"head: {digest}", "avg_approval_ms: 25942449397"])), [])
        self.assertIn("email=alice@example.com", pii_hits(free_text_of_pdf_lines(
            ["tool: alice@example.com"])),
            "明文邮箱在自由文本面必须扫得到（豁免规则不许把真 PII 一起豁免掉）")
        self.assertEqual(pii_hits(free_text_of_pdf_lines(["tool: a***@example.com"])), [],
                         "既有打码形态 ``a***@example.com`` 不该被 email 规则再咬（幂等口径）")


if __name__ == "__main__":  # 手工自检：python tests/test_analytics_export.py -v
    unittest.main(verbosity=2)
