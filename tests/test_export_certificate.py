"""R8-3（REQ-008 B8-1）验收测试：责任认定书模板 + 哈希链二维码。

运行（全程离线，只用标准库；不起服务、不出网、不装任何第三方）：

    cd agent-explainer
    python -m unittest tests.test_export_certificate -v

钉住的口径（TASK-R8-3 §2.1 / §2.2 / §2.3，PM-PLAN §10.2 第 368 行）：

1. **QR 载荷 = ``sealed_head``**：审计账本 ``seal()`` 写进条目里的那个封印锚点哈希
   （HANDOFF §4 既定 A 案）。它**不是** ``compliance.build_bundle()`` 的三段归约
   ``root_hash``。两条链的模板把两个值分行、各带限定语写出来，谁也不冒充谁
   （第 2、3 组用例逐字比对）。
2. **正文与码同源**：PDF/HTML 里展示的链头行与二维码载荷取自**同一个字符串**
   ``cert["qr_payload"]``，所以「逐字相等」由构造保证；用例用 ``qr.decode(qr.encode(…))``
   独立复算一遍（§2.2 第 1、5 条）。
3. **空值不伪造**：没有 seal 条目 / ``sealed_head`` 为空 → 不出码、留空，
   如实写「无封印锚点，二维码不可用」，**不拿链尖哈希或任何别的哈希顶替**（第 4 组）。
4. **两条渲染链都要有模板 + QR**：审计链 ``AuditLog.export_pdf`` → ``_build_pdf``
   （reportlab 逐模块画方块）、回退 ``export_html`` → ``render_html``（内联 SVG）；
   合规链 ``ComplianceService._render_pdf`` / ``_pdf_lines``（第 5、6 组）。
5. **零第三方**：QR 只用仓库自带 ``agent_explainer/qr.py``；PDF 只走既有 reportlab
   ``importlib`` 字符串导入口径。用例用**录制型替身**数 ``rect`` 调用，证明
   「逐模块画方块」这件事真的发生、块数等于深色模块数（第 6 组）。
6. **降级如实**：reportlab 不可用 → 审计链出可打印 HTML + ``fallback`` 说明；
   合规链按既有口径报 ``COMPLIANCE_EXPORT_FAILED`` 并把出路写进 ``hint``
   （**没有**新错误码、**没有**把 HTML 说成 PDF）（第 7 组）。
7. **0.4 诚实口径**：``anchor_state`` 恒 ``unavailable``、``eth_tx_hash`` 恒 ``None``；
   导出件里不许出现「已恢复 / 已撤销 / 已上链 / 已冲正完成」，「Merkle 根」只许
   以否定式出现；「验签通过≠背书齐备」两句随正文给出（第 8 组）。
8. **纪律零越界**：账本实体键零新增、导出账本行数不变、``FORMATS`` 恒 3、
   ``CSV_COLUMNS`` 恒 24、``_HTML_COLUMNS`` 恒 14、``SIGNATURE_FIELDS`` 恒 11、
   既有 8 行 PDF 正文逐字保留（第 9 组）。

纪律：临时目录一律 ``tests/scratch.py``（禁 ``tempfile``）；不并发跑全量。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import audit as au            # noqa: E402
from agent_explainer import compliance as cm       # noqa: E402
from agent_explainer import qr                     # noqa: E402
from agent_explainer import secretariat as sec     # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from scratch import scratch_dir                    # noqa: E402

REPORTLAB = importlib.util.find_spec("reportlab") is not None
RECORDLAB_NAMES = ("reportlab", "reportlab.lib", "reportlab.lib.pagesizes",
                   "reportlab.lib.styles", "reportlab.lib.units", "reportlab.platypus",
                   "reportlab.pdfgen", "reportlab.pdfgen.canvas", "reportlab.pdfbase",
                   "reportlab.pdfbase.cidfonts")
#: 「Merkle 根」允许的否定式（0.4：肯定式即禁语）。
MERKLE_NEGATIONS = ("非 Merkle 根", "不是 Merkle 根")


def sealed_log(case: unittest.TestCase) -> tuple[au.AuditLog, dict, list[dict]]:
    """一条真链：4 条决定 + 1 次 ``seal()`` + 1 条后续追加（封印章是真写进去的）。"""
    room = scratch_dir(f"r8-3-audit-{case.id().rsplit('.', 1)[-1]}")
    case.addCleanup(shutil.rmtree, room, True)
    log = au.AuditLog(room / "audit.jsonl")
    for index in range(4):
        log.append({"event": "decision", "actor_id": "local-user", "actor_role": "local",
                    "action_id": f"act-demo-000{index}", "decision": "approve",
                    "reason": f"同意搬家 {index}"})
    seal = log.seal(note="R8-3 封印")
    log.append({"event": "note", "actor_id": "local-user", "actor_role": "local",
                "note": "封印之后的追加条目"})
    return log, seal, log.read()


def certificate(rows: list[dict], status: dict) -> dict:
    return au.build_certificate(rows, status=status, generated_at="2026-11-15T10:00:00Z")


def line_with(prefix: str, lines: list[str]) -> str:
    return next((line for line in lines if line.startswith(prefix)), "")


# --------------------------------------------------------------------------- #
# reportlab 替身：录制 canvas（数 rect / drawString），零真实 PDF
# --------------------------------------------------------------------------- #

class _RecordingCanvas:
    """录制型 canvas：记 ``rect`` / ``drawString`` / ``showPage``，``getvalue`` 给个 PDF 头。"""

    def __init__(self, sink: dict, *args, **kwargs) -> None:
        self.sink = sink
        self.stream = args[0] if args and hasattr(args[0], "write") else None
        self._font = None

    def setTitle(self, title: str) -> None:
        self.sink["title"] = title

    def setFont(self, font: str, size: float) -> None:
        self._font = (font, size)
        self.sink.setdefault("fonts", []).append(font)

    def drawString(self, x: float, y: float, text: str) -> None:
        self.sink.setdefault("strings", []).append(text)

    def saveState(self) -> None:
        self.sink["save_state"] = self.sink.get("save_state", 0) + 1

    def restoreState(self) -> None:
        self.sink["restore_state"] = self.sink.get("restore_state", 0) + 1

    def setFillColorRGB(self, *args) -> None:
        self.sink["fills"] = self.sink.get("fills", 0) + 1

    def rect(self, x: float, y: float, w: float, h: float, stroke=1, fill=0) -> None:
        self.sink.setdefault("rects", []).append((round(x, 4), round(y, 4),
                                                  round(w, 4), round(h, 4)))

    def showPage(self) -> None:
        self.sink["pages"] = self.sink.get("pages", 0) + 1

    def save(self) -> None:
        self.sink["saved"] = True
        if self.stream is not None:
            # 真 canvas 是往传入的流里写字节；替身如实照做，`buffer.getvalue()` 才拿得到东西
            self.stream.write(b"%PDF-1.4 recording-stub\n")

    def getvalue(self) -> bytes:
        return b"%PDF-1.4 recording-stub\n"


def install_recording_reportlab(case: unittest.TestCase, *, fail_font: bool = False,
                                fail_import: bool = False) -> dict:
    """把 reportlab 子模块换成录制替身；``fail_import`` 模拟「本机没有 reportlab」。"""
    sink: dict = {"rects": [], "strings": [], "fonts": []}
    saved = {name: sys.modules.get(name) for name in RECORDLAB_NAMES}

    def restore() -> None:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    case.addCleanup(restore)
    if fail_import:
        for name in RECORDLAB_NAMES:
            sys.modules[name] = None          # import 一个 None 条目 → ImportError
        return sink

    root = types.ModuleType("reportlab")
    lib = types.ModuleType("reportlab.lib")
    pagesizes = types.ModuleType("reportlab.lib.pagesizes")
    pagesizes.A4 = (595, 842)
    pdfgen = types.ModuleType("reportlab.pdfgen")
    canvas_mod = types.ModuleType("reportlab.pdfgen.canvas")
    canvas_mod.Canvas = lambda *a, **kw: _RecordingCanvas(sink, *a, **kw)
    pdfbase = types.ModuleType("reportlab.pdfbase")

    def register_font(font):
        sink.setdefault("fonts_registered", []).append(getattr(font, "name", str(font)))
    pdfbase.registerFont = register_font
    cidfonts = types.ModuleType("reportlab.pdfbase.cidfonts")

    def unicode_cid_font(name: str):
        if fail_font:
            raise RuntimeError("没有 CJK 字体")
        sink.setdefault("cjk", []).append(name)
        return types.SimpleNamespace(name=name)
    cidfonts.UnicodeCIDFont = unicode_cid_font
    for module in (root, lib, pagesizes, pdfgen, canvas_mod, pdfbase, cidfonts):
        sys.modules[module.__name__] = module
    return sink


# --------------------------------------------------------------------------- #
# 1. QR 编码器往返自证（§2.2 第 1、2 条；B8-1 的实机扫码归 R8-7）
# --------------------------------------------------------------------------- #

class QrRoundtripTests(unittest.TestCase):

    def test_roundtrip_verbatim_over_four_payload_kinds(self) -> None:
        payloads = {"hex64": "a" * 64,
                    "prefixed": "sha256:" + "b" * 64,
                    "chain_head_like": "sha256:" + "0123456789abcdef" * 4,
                    "long_192": ("f" * 64) + ("0" * 64) + ("9" * 64)}
        for label, text in payloads.items():
            with self.subTest(label):
                matrix = qr.encode(text)
                self.assertEqual(qr.decode(matrix), text, "往返必须逐字相等")
                self.assertTrue(all(len(row) == len(matrix) for row in matrix), "必须是方阵")
                self.assertEqual(len(matrix) % 4, 1, "边长必须是 4v+17")

    def test_matrix_square_with_quiet_zone_added_by_renderer(self) -> None:
        matrix = qr.encode("sha256:" + "c" * 64)
        svg = qr.to_svg(matrix, scale=4, border=4)
        self.assertIn("viewBox", svg)
        self.assertNotIn("<script", svg.lower(), "二维码 SVG 不许带脚本")
        side = (len(matrix) + 8) * 4
        self.assertIn(f'width="{side}" height="{side}"', svg, "SVG 边长 = 矩阵 + 静区")

    def test_empty_payload_is_refused_not_encoded(self) -> None:
        self.assertIsNone(au.certificate_qr({"qr_payload": ""}), "空串不许出码")
        self.assertIsNone(au.certificate_qr({}), "没有载荷不许出码")
        self.assertIsNone(au.certificate_qr({"qr_payload": "   "}), "全空白不许出码")
        self.assertIsNone(au.certificate_svg({"qr_payload": ""}))
        self.assertIsNone(au.certificate_inline_svg({"qr_payload": None}))


# --------------------------------------------------------------------------- #
# 2. QR 载荷口径：sealed_head，不是三段归约 root_hash（§1.4 / §2.2 第 5 条）
# --------------------------------------------------------------------------- #

class SealedHeadCaliberTests(unittest.TestCase):

    def test_qr_payload_equals_the_seal_entry_value_verbatim(self) -> None:
        _log, seal, rows = sealed_log(self)
        cert = certificate(rows, _log.verify(raise_on_tamper=False))
        self.assertEqual(cert["sealed_head"], seal["sealed_head"], "必须是 seal() 写入的条目实键值")
        self.assertEqual(cert["qr_payload"], seal["sealed_head"])
        self.assertEqual(cert["qr_field"], "sealed_head")
        self.assertEqual(au.CERT_QR_FIELD, "sealed_head")

    def test_qr_payload_is_not_the_three_section_reduction(self) -> None:
        _log, seal, rows = sealed_log(self)
        cert = certificate(rows, _log.verify(raise_on_tamper=False))
        root = "sha256:" + "7" * 64          # 三段归约的样子货（compliance 才有真值）
        cert["root_hash"] = root
        self.assertNotEqual(cert["qr_payload"], root, "不许拿三段归约冒充链头")
        text = "\n".join(au.certificate_text(cert))
        self.assertIn("root_hash(三段归约)", text)
        self.assertIn("非链头", text)
        self.assertIn("非 Merkle 根", text)
        self.assertIn(f"sealed_head: {cert['sealed_head']}", text, "正文链头行必须逐字给出")
        self.assertIn(f"qr_payload: {cert['sealed_head']}", text, "载荷行与链头行同值")

    def test_printed_head_and_qr_payload_are_the_same_string(self) -> None:
        _log, _seal, rows = sealed_log(self)
        cert = certificate(rows, _log.verify(raise_on_tamper=False))
        lines = au.certificate_text(cert)
        head_line = line_with("sealed_head: ", lines)
        payload_line = line_with("qr_payload: ", lines)
        self.assertEqual(head_line.split(": ", 1)[1], payload_line.split(": ", 1)[1])
        self.assertEqual(qr.decode(au.certificate_qr(cert)), payload_line.split(": ", 1)[1],
                         "解码回来的必须就是正文那行的值（可机器复算）")

    def test_latest_seal_wins_and_count_is_reported(self) -> None:
        room = scratch_dir(f"r8-3-twoseals-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        log = au.AuditLog(room / "audit.jsonl")
        log.append({"event": "note", "actor_id": "local-user", "actor_role": "local",
                    "note": "第一条封印之前的条目"})
        first = log.seal(note="第一次封印")
        log.append({"event": "note", "actor_id": "local-user", "actor_role": "local",
                    "note": "两次封印之间的条目"})
        second = log.seal(note="第二次封印")
        info = au.latest_sealed_head(log.read())
        self.assertEqual(info["sealed_head"], second["sealed_head"],
                         "取最后一次封印：它覆盖到导出时的整段历史（判断口径，报告已申报）")
        self.assertNotEqual(info["sealed_head"], first["sealed_head"])
        self.assertEqual(info["seals"], 2)


# --------------------------------------------------------------------------- #
# 3. 模板六项字段（§2.1）
# --------------------------------------------------------------------------- #

class CertificateTemplateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.log, self.seal, self.rows = sealed_log(self)
        self.cert = au.build_certificate(self.rows, status=self.log.verify(raise_on_tamper=False),
                                         period={"since": "2026-11-01", "until": "2026-11-15"},
                                         generated_at="2026-11-15T10:00:00Z", lang="zh")
        self.lines = au.certificate_text(self.cert)

    def test_title_period_generated_at_lang_present(self) -> None:
        self.assertEqual(self.lines[0], au.CERT_TITLE, "标题是模板第一行，PDF / HTML 同构")
        self.assertIn("period: 2026-11-01 ~ 2026-11-15", "\n".join(self.lines))
        self.assertIn("generated_at: 2026-11-15T10:00:00Z", self.lines)
        self.assertIn("lang: zh", self.lines)

    def test_anchor_state_is_honest_and_unavailable(self) -> None:
        text = "\n".join(self.lines)
        self.assertIn("anchor_state: unavailable", text)
        self.assertIn("未接入", text)
        self.assertIn("eth_tx_hash: None", text)
        self.assertIn("不是「待填」", text)
        self.assertIsNone(self.cert["eth_tx_hash"])
        self.assertEqual(self.cert["anchor_state"], "unavailable")
        self.assertEqual(cm.ANCHOR_STATE, au.ANCHOR_STATE_TEXT, "两侧诚实态逐字同值")

    def test_counts_and_offline_recompute_guide(self) -> None:
        text = "\n".join(self.lines)
        self.assertIn("counts: records=", text)
        self.assertIn("audit=", text)
        self.assertIn("letters=", text)
        self.assertIn("哈希可离线复算", text)
        self.assertIn("canonical_json", text)
        self.assertIn("R8-4", text, "验证脚本归 R8-4：模板只写指引，不写实现")
        self.assertIn(au.CERT_VERIFY_GUIDE, self.lines)

    def test_two_sentences_about_signature_backing(self) -> None:
        text = "\n".join(self.lines)
        self.assertEqual(len(au.CERT_DISCLOSURE), 2, "两句，不少不凑")
        self.assertIn("验签通过≠背书齐备", text)
        self.assertIn("不构成 secretariat 签名账本的背书", text)

    def test_section_hashes_are_labeled_per_chain(self) -> None:
        self.assertIn("records_hash / audit_hash / letters_hash: （不适用：本导出面只有审计链）",
                      "\n".join(self.lines), "审计链没有三段哈希：如实说不适用，不编")
        self.assertIsNone(self.cert["root_hash"])
        text_with = au.certificate_text(au.build_certificate(
            self.rows, status=self.log.verify(raise_on_tamper=False),
            root_hash="sha256:" + "9" * 64,
            section_hashes={"records": "sha256:r", "audit": "sha256:a", "letters": "sha256:l"}))
        joined = "\n".join(text_with)
        for want in ("records_hash: sha256:r", "audit_hash: sha256:a", "letters_hash: sha256:l"):
            self.assertIn(want, joined)


# --------------------------------------------------------------------------- #
# 4. 无封印锚点：如实降级，绝不伪造（§1.4）
# --------------------------------------------------------------------------- #

class NoSealHonestyTests(unittest.TestCase):

    def setUp(self) -> None:
        room = scratch_dir(f"r8-3-noseal-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        self.log = au.AuditLog(room / "plain.jsonl")
        self.log.append({"event": "note", "actor_id": "local-user", "actor_role": "local",
                         "note": "一条没有封印的链"})
        self.rows = self.log.read()
        self.status = self.log.verify(raise_on_tamper=False)
        self.cert = au.build_certificate(self.rows, status=self.status)

    def test_no_seal_head_no_qr_no_fake_value(self) -> None:
        self.assertIsNone(self.cert["sealed_head"])
        self.assertIsNone(self.cert["qr_payload"])
        self.assertIsNone(au.certificate_qr(self.cert))
        self.assertIsNone(au.certificate_svg(self.cert))
        text = "\n".join(au.certificate_text(self.cert))
        self.assertIn(au.NO_SEAL_NOTE, text)
        self.assertIn("sealed_head: （空）", text)
        self.assertNotIn(f"qr_payload: sha256:", text, "不许出现编出来的载荷")

    def test_chain_tip_is_not_promoted_to_qr_payload(self) -> None:
        self.assertIsNotNone(self.status["head"], "链尖哈希是有的")
        self.assertIsNone(self.cert["qr_payload"], "但链尖不许冒充封印锚点进二维码")

    def test_html_export_states_the_degradation_and_draws_no_svg(self) -> None:
        html = au.render_html(self.rows, status=self.status, certificate=self.cert)
        self.assertIn(au.NO_SEAL_NOTE, html)
        self.assertNotIn("<svg", html, "没有载荷就不许画码")
        self.assertIn('<section class="cert">', html)


# --------------------------------------------------------------------------- #
# 5. 审计链：真 PDF（逐模块画方块）+ 回退 HTML（内联 SVG）
# --------------------------------------------------------------------------- #

class AuditChainRenderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.log, self.seal, self.rows = sealed_log(self)
        self.status = self.log.verify(raise_on_tamper=False)
        self.cert = certificate(self.rows, self.status)
        self.matrix = au.certificate_qr(self.cert)
        self.dark = sum(sum(row) for row in self.matrix)

    def test_pdf_export_is_a_real_pdf_with_certificate(self) -> None:
        if not REPORTLAB:
            self.skipTest("本机没有 reportlab：真 PDF 由录制替身用例覆盖，回退由降级用例覆盖")
        room = self.log._path.parent
        result = self.log.export_pdf(room / "cert.pdf")
        self.assertEqual(result.format, "pdf", msg=result.note)
        data = pathlib.Path(result.path).read_bytes()
        self.assertTrue(data.startswith(b"%PDF-"), "PDF 必须真是 PDF 字节")
        self.assertGreater(result.bytes, 1500)
        self.assertIsNone(result.fallback)

    def test_pdf_qr_is_drawn_module_by_module_with_matching_count(self) -> None:
        """审计链的 QR 排版单元逐模块画方块：块数 = 1 白底 + 深色模块数（零图像零第三方）。"""
        namespace, reason = au._load_reportlab()
        if namespace is None:
            self.skipTest(f"本机没有 reportlab（{reason}）：绘制由录制替身用例覆盖，回退另钉")
        flow, info = au.qr_pdf_flowable(namespace["platypus"], self.cert)
        self.assertIsNotNone(flow, "真 reportlab 有 ``platypus.Flowable`` → 必须真画")
        self.assertEqual(info["modules"], self.dark)
        self.assertEqual(info["payload"], self.cert["sealed_head"])
        sink: dict = {"rects": [], "strings": [], "fonts": []}
        flow.canv = _RecordingCanvas(sink)
        flow.draw()
        self.assertEqual(len(sink["rects"]), self.dark + 1, "1 块白底 + 每个深色模块一块")
        self.assertEqual(flow.wrap(500, 500), (info["side_pt"], info["side_pt"]))
        back = au.qr_plan_to_matrix(au.qr_rect_plan(self.matrix, x=0.0, y=info["side_pt"]),
                                    x=0.0, y=info["side_pt"], size=len(self.matrix))
        self.assertEqual(back, self.matrix, "画出去的方块能原样还原成矩阵")

    def test_build_pdf_reports_qr_when_flowable_available(self) -> None:
        namespace, reason = au._load_reportlab()
        if namespace is None:
            self.skipTest(f"本机没有 reportlab（{reason}）：由录制替身与降级用例覆盖")
        _data, info = au.render_pdf_bytes(self.rows, title="probe", status=self.status,
                                          certificate=self.cert)
        cert_info = info["certificate"]
        self.assertTrue(cert_info["enabled"])
        qr_info = cert_info["qr"]
        self.assertTrue(qr_info["drawn"], qr_info)
        self.assertEqual(qr_info["modules"], self.dark, "画的方块数 = 深色模块数")
        self.assertEqual(qr_info["payload"], self.cert["sealed_head"],
                         "PDF 里 QR 的载荷逐字等于正文链头值")
        self.assertEqual(qr_info["size"], len(self.matrix))

    def test_build_pdf_says_so_when_flowable_is_missing(self) -> None:
        """测试替身/精简安装没有 ``platypus.Flowable`` → 如实报「未画」，不画假码。"""
        platypus = types.ModuleType("reportlab.platypus")   # 故意没有 Flowable
        flow, info = au.qr_pdf_flowable(platypus, self.cert)
        self.assertIsNone(flow)
        self.assertFalse(info["drawn"])
        self.assertIn("Flowable", info["reason"])
        self.assertEqual(info["modules"], self.dark)

    def test_rect_plan_and_matrix_are_inverse_of_each_other(self) -> None:
        plan = au.qr_rect_plan(self.matrix, x=50.0, y=700.0)
        self.assertEqual(len(plan), self.dark)
        back = au.qr_plan_to_matrix(plan, x=50.0, y=700.0, size=len(self.matrix))
        self.assertEqual(back, self.matrix, "画出去的方块必须能原样还原成矩阵")
        self.assertEqual(qr.decode(back), self.cert["sealed_head"])

    def test_html_fallback_carries_inline_svg_and_zero_external_refs(self) -> None:
        html = au.render_html(self.rows, status=self.status, certificate=self.cert)
        self.assertIn('<section class="cert">', html)
        self.assertIn(f"sealed_head: {self.seal['sealed_head']}", html, "正文逐字给出链头")
        self.assertIn("<svg viewBox", html, "回退版二维码是内联 SVG")
        self.assertNotIn("http://", html, "零外链闸门：连 xmlns 都不许留下字面 URL")
        self.assertNotIn("<img", html.lower(), "零 <img>：不许用 data URI 图片绕过")
        self.assertNotIn("<script", html.lower(), "零脚本")
        for attr in re.findall(r'(?:src|href)\s*=\s*"[^"]*"', html):
            self.assertNotIn(":", attr, f"外部引用：{attr}")
        self.assertIn("验签通过≠背书齐备", html)

    def test_inline_svg_geometry_equals_to_svg(self) -> None:
        coords = lambda text: sorted(re.findall(r'<rect x="(\d+)" y="(\d+)"', text))  # noqa: E731
        full = au.certificate_svg(self.cert)
        inline = au.certificate_inline_svg(self.cert)
        self.assertIn("xmlns=", full, "qr.to_svg 原产物带命名空间声明")
        self.assertNotIn("xmlns", inline)
        self.assertEqual(coords(full), coords(inline), "只删声明，方块坐标一字不动")
        self.assertEqual(len(coords(inline)), self.dark)

    def test_export_html_auto_includes_certificate(self) -> None:
        result = self.log.export_html()
        self.assertEqual(result.format, "html")
        self.assertIn('<section class="cert">', result.text)
        self.assertIn(self.seal["sealed_head"], result.text)

    def test_export_pdf_and_fallback_share_the_same_certificate(self) -> None:
        """PDF 出件与 HTML 回退用同一份 cert：两处链头值不许各写各的。"""
        sink = install_recording_reportlab(self, fail_import=True)
        result = self.log.export_pdf()
        self.assertEqual(result.format, "html", "reportlab 不可用 → 出可打印 HTML")
        self.assertIsNone(result.path, "降级不落盘：内容在响应里，由调用方决定放哪")
        self.assertTrue(result.fallback, "fallback 必须写明为什么不是 PDF")
        self.assertIn("reportlab", str(result.fallback))
        self.assertIn('<section class="cert">', result.text)
        self.assertIn(f"sealed_head: {self.seal['sealed_head']}", result.text)
        self.assertIn("<svg viewBox", result.text, "回退件也要有二维码（内联 SVG）")
        self.assertNotIn("http://", result.text)


# --------------------------------------------------------------------------- #
# 6. 合规链：``_render_pdf`` / ``_pdf_lines`` 带模板与 QR（§2.1 第 6 项）
# --------------------------------------------------------------------------- #

class ComplianceChainTests(unittest.TestCase):

    def setUp(self) -> None:
        room = scratch_dir(f"r8-3-compliance-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        self.paths = FeaturePaths(room / "features")
        self.paths.ensure()
        _log, self.seal, self.rows = sealed_log(self)
        self.records = [{"id": "rec-1", "timestamp": "2026-11-01T08:00:00Z",
                         "agent_id": "agent-a", "action_type": "tool.run", "status": "done",
                         "risk_level": "low", "tool_used": {"name": "probe"}}]
        self.service = cm.ComplianceService(self.paths)
        self.meta = {"period": {"since": "2026-11-01", "until": "2026-11-15"},
                     "root_hash": "sha256:" + "5" * 64, "generated_at": "2026-11-15T10:00:00Z",
                     "lang": "zh", "anchor_state": cm.ANCHOR_STATE,
                     "counts": {"records": 1, "audit": len(self.rows), "letters": 0}}
        self.bundle = {"sections": {"records": self.records, "audit": self.rows, "letters": []},
                       "hashes": {"records": "sha256:r", "audit": "sha256:a",
                                  "letters": "sha256:l"}}

    def export_pdf(self) -> dict:
        return self.service.export(records=self.records, audit_entries=self.rows,
                                   period=self.meta["period"], format="pdf")

    def test_export_pdf_actually_produces_pdf_bytes(self) -> None:
        if not REPORTLAB:
            self.skipTest("本机没有 reportlab：PM 环境实测 False，走既有报错口径（降级用例已钉）")
        entry = self.export_pdf()
        self.assertEqual(entry["format"], "pdf")
        data = pathlib.Path(entry["path"]).read_bytes()
        self.assertTrue(data.startswith(b"%PDF-"),
                        "修前这里是 NameError（裸 A4 未定义）；修后必须真出 PDF")

    def test_pdf_lines_keep_legacy_eight_verbatim(self) -> None:
        lines = cm.ComplianceService._pdf_lines(self.meta, self.bundle)
        legacy = ["generated_at: 2026-11-15T10:00:00Z",
                  "root_hash: sha256:" + "5" * 64,
                  "anchor_state: unavailable (no anchor backend in 0.6.0)",
                  f"counts: records=1 audit={len(self.rows)} letters=0",
                  "records_hash: sha256:r", "audit_hash: sha256:a", "letters_hash: sha256:l",
                  "note: hashes are recomputable offline; "
                  "verify via POST /api/compliance/exports/<id>/verify"]
        self.assertEqual(lines[:8], legacy, "既有 8 行逐字不动，模板只追加")
        self.assertGreater(len(lines), 8 + 12, "模板区块要真在里面")

    def test_pdf_certificate_carries_sealed_head_and_qr_same_value(self) -> None:
        lines = cm.ComplianceService._pdf_lines(self.meta, self.bundle)
        head = line_with("sealed_head: ", lines)
        payload = line_with("qr_payload: ", lines)
        self.assertEqual(head, f"sealed_head: {self.seal['sealed_head']}")
        self.assertEqual(payload, f"qr_payload: {self.seal['sealed_head']}")
        cert = cm.ComplianceService._certificate(self.meta, self.bundle)
        self.assertEqual(qr.decode(au.certificate_qr(cert)), self.seal["sealed_head"])
        self.assertNotEqual(cert["qr_payload"], self.meta["root_hash"],
                            "合规链的 QR 也不许拿三段归约 root_hash 顶替")
        joined = "\n".join(lines)
        self.assertIn("root_hash(三段归约): " + "sha256:" + "5" * 64, joined)
        self.assertIn("非链头", joined)
        self.assertIn("records_hash: sha256:r", joined)

    def test_pdf_certificate_is_honest_about_anchor_and_backing(self) -> None:
        joined = "\n".join(cm.ComplianceService._pdf_lines(self.meta, self.bundle))
        self.assertIn("anchor_state: unavailable", joined)
        self.assertIn("未接入", joined)
        self.assertIn("eth_tx_hash: None", joined)
        self.assertIn("验签通过≠背书齐备", joined)
        self.assertIn("哈希可离线复算", joined)
        self.assertIn("canonical_json", joined)

    def test_render_pdf_draws_qr_modules_and_prints_the_same_value(self) -> None:
        sink = install_recording_reportlab(self)
        data = self.service._render_pdf(self.meta, self.bundle)
        self.assertTrue(data.startswith(b"%PDF-"))
        matrix = au.certificate_qr(cm.ComplianceService._certificate(self.meta, self.bundle))
        dark = sum(sum(row) for row in matrix)
        self.assertEqual(len(sink["rects"]), dark + 1,
                         "1 块白底 + 每个深色模块一块：逐模块画方块，零图像零第三方")
        strings = "\n".join(sink["strings"])
        self.assertIn(f"sealed_head: {self.seal['sealed_head']}", strings)
        self.assertIn(f"qr_payload: {self.seal['sealed_head']}", strings)
        self.assertIn(self.seal["sealed_head"], strings, "PDF 正文里有可扫码比对的明文链头")

    def test_render_pdf_states_no_qr_when_no_seal_exists(self) -> None:
        sink = install_recording_reportlab(self)
        meta = dict(self.meta)
        bundle = {"sections": {"records": self.records,
                               "audit": [{"seq": 1, "ts": "2026-11-01T08:00:00Z",
                                          "event": "note", "actor_id": "local-user"}],
                               "letters": []},
                  "hashes": dict(self.bundle["hashes"])}
        data = self.service._render_pdf(meta, bundle)
        self.assertTrue(data.startswith(b"%PDF-"))
        strings = "\n".join(sink["strings"])
        self.assertIn(au.NO_SEAL_NOTE, strings)
        self.assertIn("qr: none (no sealed_head in this export context)", strings)
        self.assertEqual(sink["rects"], [], "无封印 → 一块 QR 都不画，不画占位码")

    def test_missing_reportlab_keeps_the_existing_error_caliber(self) -> None:
        install_recording_reportlab(self, fail_import=True)
        with self.assertRaises(cm.ComplianceError) as ctx:
            self.service.export(records=self.records, audit_entries=self.rows, format="pdf")
        self.assertEqual(ctx.exception.code, "COMPLIANCE_EXPORT_FAILED")
        self.assertIn("reportlab", ctx.exception.render())
        self.assertIn("csv", str(ctx.exception.hint), "出路要写进 hint：改 csv / chain")
        self.assertEqual(len(list((self.paths.root / "exports").glob("*"))), 0,
                         "PDF 没出件就不许留半份文件")

    def test_font_note_travels_with_the_pdf_when_cjk_font_is_missing(self) -> None:
        sink = install_recording_reportlab(self, fail_font=True)
        self.service._render_pdf(self.meta, self.bundle)
        strings = "\n".join(sink["strings"])
        self.assertIn("font_note:", strings, "缺 CJK 字体的警告必须随文件本体给出")
        self.assertIn("CJK", strings)


# --------------------------------------------------------------------------- #
# 7. 0.4 展示纪律：禁语与否定式（§1.5）
# --------------------------------------------------------------------------- #

class PdfCjkFontTests(unittest.TestCase):
    """R8-7b 回归：PDF 中文字体必须**真的注册上**、并且优先**嵌入**（PDF 自包含）。

    事故形状（R8-3 起一直存在，R8-7b 实件取证才发现）：``registerFont`` 住在
    ``reportlab.pdfbase.pdfmetrics``，而旧代码写成 ``pdfbase.registerFont`` —— 必然抛
    ``AttributeError``，三个候选字体全在同一个异常上失败 → 永远退回 Helvetica →
    **PDF 里所有中文变成 ``I``**，却仍返回 ``format="pdf"`` / ``fallback=None``（「看着成功」）。
    第二层问题：只挂 reportlab 自带 ``STSong-Light`` 也不够，那是**不嵌入**的 CID 字体，
    对方阅读器缺亚洲字体包就显示方块 —— 对外举证件必须自包含。

    三例分别钉：①真命名空间下字体必须是 CJK 且已注册进 pdfmetrics；
    ②出件字节里必须有嵌入字体流 ``/FontFile2``（或如实自报「未嵌入」）；
    ③缺 ``pdfmetrics`` 的命名空间必须如实退回 Helvetica + 警告，不许抛、不许假装成功。
    """

    @unittest.skipUnless(REPORTLAB, "本机无 reportlab：字体注册路径不可测")
    def test_real_namespace_registers_a_cjk_font(self) -> None:
        namespace, reason = au._load_reportlab()
        self.assertIsNotNone(namespace, reason)
        self.assertIn("pdfmetrics", namespace, "命名空间必须带 pdfmetrics（registerFont 在这儿）")
        self.assertIn("ttfonts", namespace, "命名空间必须带 ttfonts（嵌入 TrueType 用）")
        font, note = au._register_pdf_font(namespace)
        self.assertNotEqual(font, "Helvetica", "必须注册上 CJK 字体；退回 Helvetica = 中文全丢")
        self.assertIn("CJK", note)
        self.assertIsNotNone(namespace["pdfmetrics"].getFont(font),
                             f"{font} 未真正注册进 pdfmetrics")

    @unittest.skipUnless(REPORTLAB, "本机无 reportlab：PDF 字节不可造")
    def test_pdf_bytes_embed_the_font_stream_or_say_it_does_not(self) -> None:
        log, _seal, rows = sealed_log(self)
        status = log.verify(raise_on_tamper=False)
        cert = certificate(rows, status)
        data, info = au.render_pdf_bytes(rows, status=status, certificate=cert)
        self.assertTrue(data.startswith(b"%PDF-"), "得先是真 PDF")
        self.assertNotEqual(info["font"], "Helvetica", "出件字体不能是 Helvetica")
        if info["font"] == "TraceLensCJK":
            self.assertIn(b"/FontFile2", data, "TrueType 未嵌入：对方阅读器可能显示方块")
            self.assertIn("嵌入", info["font_note"])
        else:                                   # CID 兜底：必须自报「未嵌入」
            self.assertIn("未嵌入", info["font_note"],
                          "用了不嵌入的 CID 字体就必须写明，不许让用户以为拿到的是自包含件")

    def test_namespace_without_pdfmetrics_degrades_honestly(self) -> None:
        """旧替身/精简安装：没有 pdfmetrics 就如实退回，不许 AttributeError 冒到调用方。"""
        font, note = au._register_pdf_font({"cidfonts": None, "pdfbase": None})
        self.assertEqual(font, "Helvetica")
        self.assertIn("CJK", note)
        self.assertIn("建议改用可打印 HTML", note)


class DisplayDisciplineTests(unittest.TestCase):

    def test_forbidden_completion_words_never_appear_in_exports(self) -> None:
        log, seal, rows = sealed_log(self)
        status = log.verify(raise_on_tamper=False)
        cert = certificate(rows, status)
        html = au.render_html(rows, status=status, certificate=cert)
        pdf_text = "\n".join(cm.ComplianceService._pdf_lines(
            {"period": {}, "root_hash": cert["sealed_head"], "generated_at": "G", "lang": "zh",
             "anchor_state": cm.ANCHOR_STATE, "counts": {"records": 1, "audit": len(rows),
                                                          "letters": 0}},
            {"sections": {"records": [], "audit": rows, "letters": []},
             "hashes": {"records": "r", "audit": "a", "letters": "l"}}))
        corpus = "\n".join(au.certificate_text(cert)) + "\n" + html + "\n" + pdf_text
        for word in au.DISPLAY_FORBIDDEN_WORDS:
            self.assertNotIn(word, corpus, f"展示禁语出现在导出件里：{word}")
        self.assertEqual(au.DISPLAY_FORBIDDEN_WORDS,
                         ("已恢复", "已撤销", "已上链", "已冲正完成"))

    def test_merkle_root_appears_only_negated(self) -> None:
        _log, _seal, rows = sealed_log(self)
        cert = certificate(rows, _log.verify(raise_on_tamper=False))
        html = au.render_html(rows, status=_log.verify(raise_on_tamper=False), certificate=cert)
        corpus = "\n".join(au.certificate_text(cert)) + "\n" + html
        lines = [line for line in corpus.splitlines() if "Merkle" in line]
        self.assertTrue(lines, "模板里要出现「Merkle 根」这个限定语（否定式）")
        for line in lines:
            self.assertTrue(any(n in line for n in MERKLE_NEGATIONS), f"肯定式 Merkle 根：{line}")

    def test_certificate_never_claims_scan_verified_acceptance(self) -> None:
        _log, _seal, rows = sealed_log(self)
        cert = certificate(rows, _log.verify(raise_on_tamper=False))
        corpus = "\n".join(au.certificate_text(cert)) + au.render_html(
            rows, status=_log.verify(raise_on_tamper=False), certificate=cert)
        for claim in ("已通过扫码验真", "B8-1 已验收", "已验收完成", "已上链存证"):
            self.assertNotIn(claim, corpus, f"导出件里不许自封验收/上链：{claim}")


# --------------------------------------------------------------------------- #
# 8. 纪律读数（§1.1 / §1.3）
# --------------------------------------------------------------------------- #

class DisciplineTests(unittest.TestCase):

    def test_constants_are_untouched(self) -> None:
        self.assertEqual(cm.FORMATS, ("pdf", "csv", "chain"), "格式枚举不新增")
        self.assertEqual(cm.ANCHOR_STATE, "unavailable")
        self.assertEqual(len(au.CSV_COLUMNS), 24, "R8-2 刚收口：本轮零改动")
        self.assertEqual(len(au._HTML_COLUMNS), 14, "R8-2 遗留第 8 项：本轮不擅自同步")
        self.assertEqual(len(sec.SIGNATURE_FIELDS), 11, "签名域 11 字段一字不动")
        self.assertEqual(au.CERT_QR_FIELD, "sealed_head")

    def test_export_entry_keys_are_not_extended(self) -> None:
        if not REPORTLAB:
            self.skipTest("本机没有 reportlab：出件路径由降级用例覆盖")
        room = scratch_dir(f"r8-3-keys-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        entry = service.export(records=[{"id": "rec-1", "timestamp": "2026-11-01T08:00:00Z",
                                         "agent_id": "a", "action_type": "t", "status": "done",
                                         "risk_level": "low"}],
                               audit_entries=rows, format="pdf")
        self.assertEqual(set(entry) - {"uri"},
                         {"id", "user_id", "time_range", "format", "root_hash", "ipfs_cid",
                          "eth_tx_hash", "anchor_state", "path", "bytes", "sha256",
                          "chain_ok", "counts", "created_at"},
                         "账本实体键零新增：模板字段只进文件本体，不进账本")
        rows_on_disk = [json.loads(line) for line in
                        paths.exports.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows_on_disk), 1, "一次导出一行账本：模板不额外加行")
        self.assertEqual(set(rows_on_disk[0]["item"]) - {"seq", "prev_hash", "entry_hash"},
                         set(entry) - {"uri"}, "账本行的键与响应键同集")

    def test_csv_and_chain_outputs_are_unchanged(self) -> None:
        room = scratch_dir(f"r8-3-formats-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        records = [{"id": "rec-1", "timestamp": "2026-11-01T08:00:00Z", "agent_id": "a",
                    "action_type": "t", "status": "done", "risk_level": "low"}]
        csv_entry = service.export(records=records, audit_entries=rows, format="csv")
        chain_entry = service.export(records=records, audit_entries=rows, format="chain")
        csv_text = pathlib.Path(csv_entry["path"]).read_text(encoding="utf-8")
        self.assertEqual(csv_text.splitlines()[0],
                         "id,timestamp,agent_id,action_type,status,risk_level,tool,decision,decided_by")
        first = json.loads(pathlib.Path(chain_entry["path"]).read_text(
            encoding="utf-8").splitlines()[0])
        self.assertEqual(first["section"], "meta")
        self.assertNotIn("sealed_head", json.dumps(first),
                         "chain 产物的 meta 不因模板而变（QR 只进 PDF/HTML 渲染链）")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
