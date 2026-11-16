"""R8-6（REQ-008 第 6 子任务）验收测试：前端导出接面。

运行（全程离线、只用标准库；不起服务、不出网、不装任何第三方）：

    cd agent-explainer
    python -X utf8 -m unittest tests.test_export_face -v

钉住的口径（TASK-R8-6 §1 / §2.1）：

1. **格式与时间范围显式可见**：面板必须把「三个按钮各自是什么格式」和「本次导出会带上
   什么范围」印出来；范围**只读**自 ``state.audit.filters``，面板不得改写筛选、不得另造
   第二套筛选状态（第 1 组）。
2. **进度条必须是不确定态**：``role="progressbar"`` + ``aria-valuetext`` 是句子不是数字、
   ``data-determinate="false"``、``data-percent`` 恒空、**零定时器 / 零写宽度 / 零 animation**
   （第 2 组）。
3. **结果页每个数字都来自响应**：format / rows / sha256 / filename / fallback 五项全部取自
   ``exportPanel.last``；页面**不补时间戳**（浏览器时钟不是事实源）、**不补百分比**（第 3 组）。
4. **降级态同屏不回退**：结果卡自带 ``anchor_state=unavailable`` / ``eth_tx_hash=none``，并
   随卡渲染「未锚定（no anchor）/ 待接入」；验证指引只指向 ``tools/verify_export.py``，
   不宣称「已验真」（第 4 组）。
5. **契约零放宽**：``API_ENDPOINTS`` 不含 ``/api/compliance``；``ext/compliance.js`` 只
   ``Ext.register`` 一次、无 tab、零新调用面；``SIGNATURE_FIELDS`` 11 / ``CSV_COLUMNS`` 24 /
   ``_HTML_COLUMNS`` 14 / ``FORMATS`` 三值一字未动（第 5 组）。
6. **零回归**：R8-5 的 ``data-hook="export-anchor"`` 四旗标与四条 ``export.anchor.*`` 文案
   逐字在位；完成态禁语在 ``app.js`` / ``ext/compliance.js`` / 字典 / embed / keys 零出现
   （第 6 组）。

纪律：临时目录一律 ``tests/scratch.py``（禁 ``tempfile``）；不并发跑全量。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import audit as au            # noqa: E402
from agent_explainer import compliance as cm       # noqa: E402
from agent_explainer import secretariat as sec     # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
APP_JS = (WEB / "app.js").read_text(encoding="utf-8")
EXT_JS = (WEB / "ext" / "compliance.js").read_text(encoding="utf-8")
STYLES = (WEB / "styles.css").read_text(encoding="utf-8")
ZH = json.loads((WEB / "i18n" / "zh.json").read_text(encoding="utf-8"))
EN = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))
EMBED = (WEB / "i18n" / "embed.js").read_text(encoding="utf-8")
KEYS_MD = (WEB / "i18n" / "keys.md").read_text(encoding="utf-8")

R86_LEAVES = [
    "format.current",
    "range.label", "range.all_time", "range.limit", "range.note",
    "progress.busy", "progress.aria",
    "result.title", "result.format", "result.rows", "result.sha", "result.file",
    "result.fallback", "result.verify", "result.anchored", "result.empty",
]
ANCHOR_LEAVES = ["anchor.state", "anchor.cid", "anchor.tx", "anchor.deferred"]
COMPLETION_LIES = ["正在上链", "已上链", "交易哈希已生成", "上链完成", "锚定成功", "存证成功"]


def panel_code() -> str:
    """renderExportPanel 的函数体，剥掉注释（注释里的边界说明不是界面行为）。"""
    panel = APP_JS.split("function renderExportPanel", 1)[1].split("function renderAuditPanel", 1)[0]
    code = re.sub(r"/\*.*?\*/", " ", panel, flags=re.S)
    return re.sub(r"//[^\n]*", " ", code)


def ext_code() -> str:
    return re.sub(r"/\*.*?\*/", " ", EXT_JS, flags=re.S)


# --------------------------------------------------------------------------- #
# 1. 格式与时间范围显式可见，且范围只读
# --------------------------------------------------------------------------- #
class FormatAndRangeTests(unittest.TestCase):
    def test_format_line_is_printed(self) -> None:
        code = panel_code()
        self.assertIn("'export-format'", code, "格式说明行没有挂载")
        self.assertIn("'export.format.current'", code, "格式说明没有走字典")
        for leaf in ("action.csv", "action.pdf", "action.print"):
            self.assertIn(f"'export.{leaf}'", APP_JS, f"三格式按钮丢了 {leaf}")

    def test_range_is_read_from_audit_filters(self) -> None:
        code = panel_code()
        self.assertIn("'export-range'", code)
        self.assertIn("state.audit.filters", code, "范围必须读审计筛选，不能另造事实源")
        self.assertIn(".since", code)
        self.assertIn(".until", code)
        self.assertIn("state.audit.limit", code)
        self.assertIn("'export.range.note'", code)

    def test_panel_never_rewrites_the_filters(self) -> None:
        code = panel_code()
        # 只读口径：面板里不得出现任何 filters 赋值
        self.assertNotRegex(code, r"state\.audit\.filters\s*(=|\.)\s*[a-z]",
                            "导出面板改写了审计筛选（只许读）")
        self.assertNotIn("state.audit.filters.since =", code)
        self.assertNotIn("state.audit.filters.until =", code)

    def test_range_copy_is_bilingual(self) -> None:
        for leaf in ("range.label", "range.all_time", "range.limit", "range.note"):
            self.assertIn(leaf, ZH["export"], f"zh 缺 export.{leaf}")
            self.assertIn(leaf, EN["export"], f"en 缺 export.{leaf}")
            self.assertNotEqual(ZH["export"][leaf], EN["export"][leaf], f"{leaf} 漏译")


# --------------------------------------------------------------------------- #
# 2. 进度条：不确定态，零假百分比
# --------------------------------------------------------------------------- #
class ProgressBarTests(unittest.TestCase):
    def test_declared_indeterminate(self) -> None:
        code = ext_code()
        self.assertIn("'data-determinate', 'false'", EXT_JS)
        self.assertIn("'data-percent', ''", EXT_JS)
        self.assertIn("role: 'progressbar'", EXT_JS)
        self.assertIn("'aria-valuetext'", EXT_JS)
        # aria-valuenow 一旦出现就意味着页面在报一个数
        self.assertNotIn("aria-valuenow", EXT_JS)
        self.assertNotIn("aria-valuemin", EXT_JS)
        self.assertNotIn("aria-valuemax", EXT_JS)
        self.assertTrue(code)

    def test_no_timer_and_no_width_math(self) -> None:
        for token in ("setInterval", "setTimeout", "requestAnimationFrame", "style.width"):
            self.assertNotIn(token, EXT_JS, f"进度条里出现了 {token}：可能是假进度")
        self.assertNotRegex(EXT_JS, r"Math\.round\([^)]*100\s*\)", "进度条在算百分比")
        self.assertNotIn("%", ext_code().replace("%%", ""), "ext/compliance.js 出现百分号")

    def test_progress_only_visible_while_busy(self) -> None:
        self.assertIn("busy", EXT_JS)
        self.assertIn("data-visible", EXT_JS)
        code = panel_code()
        self.assertIn("busy: !!state.exportPanel.busy", code,
                      "面板没有把 busy 传给进度条")

    def test_styles_have_no_long_animation(self) -> None:
        block = STYLES.split(".export__progress", 1)[1][:2000]
        self.assertNotIn("animation:", block, "进度条不得靠动画模拟推进")
        self.assertNotIn("@keyframes export-progress", STYLES)


# --------------------------------------------------------------------------- #
# 3. 结果页：数字全部来自响应
# --------------------------------------------------------------------------- #
class ResultCardTests(unittest.TestCase):
    def test_every_field_comes_from_the_response(self) -> None:
        for field in ("format", "rows", "sha256", "filename", "fallback"):
            self.assertIn(f"last.{field}", EXT_JS, f"结果卡没有消费 last.{field}")
        self.assertIn("'export.result.title'", EXT_JS)
        for leaf in ("format", "rows", "sha", "file", "fallback", "verify", "empty"):
            self.assertIn(f"'export.result.{leaf}'", EXT_JS, f"结果卡缺字典键 result.{leaf}")

    def test_no_browser_clock_in_the_card(self) -> None:
        for token in ("new Date(", "Date.now(", "toLocaleString", "toISOString"):
            self.assertNotIn(token, EXT_JS, f"结果卡用浏览器时钟补数字：{token}")

    def test_empty_state_is_stated_not_faked(self) -> None:
        self.assertIn("'export.result.empty'", EXT_JS)
        self.assertIn("data-visible", EXT_JS)

    def test_panel_delegates_and_degrades_honestly(self) -> None:
        code = panel_code()
        self.assertIn("TraceLensCompliance", code)
        self.assertIn("'export-result-host'", code)
        # 扩展缺失时不许本地兜一个假卡
        self.assertIn("'data-missing'", code)


# --------------------------------------------------------------------------- #
# 4. 降级态同屏不回退
# --------------------------------------------------------------------------- #
class DegradedStateTests(unittest.TestCase):
    def test_result_card_carries_the_anchor_flags(self) -> None:
        self.assertIn("ANCHOR_STATE = 'unavailable'", EXT_JS)
        self.assertIn("ETH_TX_HASH = 'none'", EXT_JS)
        self.assertIn("'data-anchor-state': ANCHOR_STATE", EXT_JS)
        self.assertIn("'data-eth-tx-hash': ETH_TX_HASH", EXT_JS)
        self.assertIn("'export.result.anchored'", EXT_JS)

    def test_verify_guide_is_a_guide_not_a_claim(self) -> None:
        self.assertIn("'export.result.verify'", EXT_JS)
        self.assertIn("tools/verify_export.py", ZH["export"]["result.verify"])
        self.assertIn("tools/verify_export.py", EN["export"]["result.verify"])

    def test_r85_flags_still_present(self) -> None:
        code = panel_code()
        self.assertIn("'export-anchor'", code, "R8-5 的锚定区块被搬走了")
        self.assertIn("EXPORT_ANCHOR_STATE", code)
        self.assertIn("EXPORT_ANCHOR_COPY_KEYS", code)
        self.assertIn("'data-cid-anchored', 'false'", code)

    def test_backend_constants_unchanged(self) -> None:
        self.assertEqual("unavailable", cm.ANCHOR_STATE)
        self.assertEqual(("pdf", "csv", "chain"), tuple(cm.FORMATS))


# --------------------------------------------------------------------------- #
# 5. 契约零放宽
# --------------------------------------------------------------------------- #
class ContractTests(unittest.TestCase):
    def test_no_compliance_call_face(self) -> None:
        code = panel_code()
        self.assertNotIn("/api/compliance", code)
        self.assertNotIn("API_ENDPOINTS.compliance =", APP_JS)

    def test_extension_registers_once_without_a_tab(self) -> None:
        self.assertEqual(1, EXT_JS.count("Ext.register({"), "扩展只能注册一次")
        block = EXT_JS.split("Ext.register({", 1)[1].split("});", 1)[0]
        self.assertNotIn("tab:", block, "本模块不许开第二个导出入口")
        self.assertIn("id: 'compliance'", block)

    def test_ledger_faces_untouched(self) -> None:
        self.assertEqual(11, len(sec.SIGNATURE_FIELDS))
        self.assertEqual(24, len(au.CSV_COLUMNS))
        self.assertEqual(14, len(au._HTML_COLUMNS))

    def test_no_new_endpoint_constant(self) -> None:
        self.assertEqual(1, APP_JS.count("compliance: 'api/compliance'"),
                         "API_ENDPOINTS 里 compliance 条目应保持唯一")


# --------------------------------------------------------------------------- #
# 6. 零回归 + 字典纪律
# --------------------------------------------------------------------------- #
class NoRegressionTests(unittest.TestCase):
    def test_anchor_copy_untouched(self) -> None:
        for leaf in ANCHOR_LEAVES:
            self.assertIn(leaf, ZH["export"], f"R8-5 的字典键丢了：{leaf}")
            self.assertIn(leaf, EN["export"], f"R8-5 的字典键丢了：{leaf}")

    def test_new_keys_are_bilingual_and_synced(self) -> None:
        for leaf in R86_LEAVES:
            self.assertIn(leaf, ZH["export"], f"zh 缺 export.{leaf}")
            self.assertIn(leaf, EN["export"], f"en 缺 export.{leaf}")
            self.assertIn(leaf, EMBED, f"embed.js 未同步：{leaf}")
            self.assertIn(f"export.{leaf}", KEYS_MD, f"keys.md 未同步：{leaf}")

    def test_no_cjk_in_code_faces(self) -> None:
        self.assertFalse(re.search(r"[\u4e00-\u9fff]", EXT_JS), "ext/compliance.js 出现中文字面量")
        panel = panel_code()
        self.assertFalse(re.search(r"[\u4e00-\u9fff]", panel), "导出面板出现中文字面量")

    def test_no_completion_illusion(self) -> None:
        for blob, label in ((APP_JS, "app.js"), (EXT_JS, "ext/compliance.js"),
                            (json.dumps(ZH, ensure_ascii=False), "zh.json"),
                            (json.dumps(EN, ensure_ascii=False), "en.json"),
                            (EMBED, "embed.js"), (KEYS_MD, "keys.md")):
            for lie in COMPLETION_LIES:
                self.assertNotIn(lie, blob, f"{label} 出现完成态假象：{lie}")

    def test_anchored_design_tokens(self) -> None:
        """样式只用 :root 里定义过的 token（selfcheck 第 39 项的同口径静态版）。"""
        block = STYLES.split(".export__format,", 1)[1].split(".audit__chips", 1)[0]
        used = set(re.findall(r"var\((--[a-z0-9-]+)", block))
        root = STYLES.split(":root {", 1)[1].split("}", 1)[0]
        defined = set(re.findall(r"(--[a-z0-9-]+):", root))
        missing = sorted(used - defined)
        self.assertEqual([], missing, f"样式用了未定义 token：{missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
