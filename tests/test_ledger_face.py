"""主动作页账目式重排（ledger.css）验收测试。

运行（全程离线、只用标准库）：

    cd agent-explainer
    python -X utf8 -m unittest tests.test_ledger_face -v

钉住的口径（`docs/24-action-ledger.md`）：

1. 新增样式表必须被 `index.html` 加载，且排在 `workspaces.css` 之后
   （它是覆盖层，早于宿主样式就不生效）。
2. 纯 ASCII、零中文字面量、零 `@font-face` / 外部 URL —— 离线桌面端纪律。
3. 每个 `var(--x)` 都必须在 `styles.css` 的 `:root` 里声明；未声明会静默
   回退成透明，是这类覆盖层最容易藏的缺陷。
4. 重排的关键事实：三列账目（宽屏）、两列 + 已完成跨行（1024-1360）、
   窄屏单列；卡片是发丝线账目行而非圆角阴影卡片；桌面只留右侧一份导入面板。
5. 零 `animation` / `@keyframes`：不确定态进度条纪律，覆盖层不得引入动效。
6. 视觉验收用真实页面的无头截图与 `--dump-dom` 几何测量，不靠目测。

纪律：只用标准库；不启动服务；不改动任何文件。
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
WEB = REPO / "agent_explainer" / "web"
EXT = WEB / "ext"

LEDGER = EXT / "ledger.css"
WORKSPACES_CSS = EXT / "workspaces.css"
INDEX = WEB / "index.html"
STYLES = WEB / "styles.css"

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
VAR_USE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
VAR_DEF = re.compile(r"(^|\s)(--[A-Za-z0-9_-]+)\s*:")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_comments(source: str) -> str:
    without_block = re.sub(r"/\*[\s\S]*?\*/", " ", source)
    return re.sub(r"//[^\n]*", " ", without_block)


class AssetsTests(unittest.TestCase):
    """覆盖层必须真的被页面加载，且顺序正确。"""

    def test_stylesheet_is_loaded_after_its_host(self) -> None:
        html = read(INDEX)
        self.assertIn("ext/ledger.css", html, "index.html 没有加载 ext/ledger.css")
        self.assertLess(html.index("ext/workspaces.css"), html.index("ext/ledger.css"),
                        "覆盖层必须排在 workspaces.css 之后")

    def test_stylesheet_is_pure_ascii_with_no_cjk(self) -> None:
        raw = LEDGER.read_bytes()
        self.assertTrue(all(b < 128 for b in raw), "ledger.css 不是纯 ASCII")
        self.assertFalse(CJK.search(read(LEDGER)), "ledger.css 里有中文字面量")

    def test_no_external_fonts_or_urls(self) -> None:
        code = strip_comments(read(LEDGER))
        self.assertNotIn("@font-face", code)
        self.assertNotIn("http://", code)
        self.assertNotIn("https://", code)
        self.assertNotIn("url(", code)


class TokenTests(unittest.TestCase):
    """覆盖层不得引用未声明的令牌（styles.css 或本文件内声明均可）。"""

    def test_every_token_is_declared(self) -> None:
        code = strip_comments(read(LEDGER))
        styles = read(STYLES)
        declared = {m.group(2) for m in VAR_DEF.finditer(styles)}
        # The reading panel declares its own --dp-* geometry tokens on
        # .card.is-expanded; those are real declarations, not dangling uses.
        declared |= {m.group(2) for m in VAR_DEF.finditer(code)}
        used = set(VAR_USE.findall(code))
        missing = sorted(used - declared)
        self.assertEqual([], missing, f"ledger.css 用了未定义的令牌：{missing}")

    def test_uses_the_archive_palette(self) -> None:
        code = read(LEDGER)
        for token in ("--ws-paper", "--ws-ink", "--ws-rule", "--ws-accent",
                      "--ws-serif", "--ws-mono"):
            self.assertIn(token, code, f"没有复用功能中心的令牌 {token}")


class LayoutTests(unittest.TestCase):
    """行动清单的空间重排：三列 / 两列 / 单列，且不是圆角卡片。"""

    def test_three_ruled_columns_on_wide_desktop(self) -> None:
        code = read(LEDGER)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", code)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", code)
        self.assertIn("flex-direction: column", code, "窄屏没有退回单列")

    def test_zones_are_ruled_not_rounded(self) -> None:
        code = read(LEDGER)
        self.assertIn("border-radius: 0", code)
        self.assertIn("box-shadow: none", code)
        self.assertIn("border-bottom: 1px solid var(--ws-rule)", code,
                      "卡片没有变成发丝线账目行")

    def test_each_zone_scrolls_on_its_own(self) -> None:
        code = read(LEDGER)
        self.assertIn("overflow-y: auto", code)
        self.assertIn("max-height: 190px", code,
                      "窄屏断点的限高被高特异性规则盖掉了")
        self.assertIn("position: sticky", code, "表头没有吸顶")

    def test_desktop_shows_only_one_import_panel(self) -> None:
        code = read(LEDGER)
        self.assertIn(".app .sheet > .import", code)
        self.assertIn("display: none", code)

    def test_headless_rig_matches_the_real_layout(self) -> None:
        code = read(LEDGER)
        self.assertIn('[data-frame][data-frame-layout="desktop"]', code,
                      "没有覆盖 headless rig 的高特异性规则")


class DetailPanelTests(unittest.TestCase):
    """展开详情：小滚动栏 -> 大阅读面板（真实窗口实测 52px -> 653px）。"""

    def test_detail_is_a_fixed_reading_panel(self) -> None:
        code = read(LEDGER)
        self.assertIn(".app .card.is-expanded .card__detail {", code)
        self.assertIn("position: fixed", code)
        self.assertIn("--dp-w: min(1040px, 92vw)", code)
        self.assertIn("--dp-h: min(82vh, 780px)", code)
        self.assertIn("overflow-y: auto", code)
        self.assertIn("overscroll-behavior: contain", code,
                      "面板内滚动不能穿透到整页")

    def test_head_is_the_collapse_bar(self) -> None:
        code = read(LEDGER)
        self.assertIn(".app .card.is-expanded .card__toggle {", code)
        self.assertIn("z-index: 89", code)
        self.assertIn("--dp-head: 52px", code)
        self.assertIn("top: calc(var(--dp-top) + var(--dp-head))", code,
                      "详情面板必须紧贴在标题栏下面")

    def test_backdrop_and_readable_kv(self) -> None:
        code = read(LEDGER)
        self.assertIn(".app:has(.card.is-expanded)::before", code)
        self.assertIn("pointer-events: none", code,
                      "遮罩不能吃掉背景里其它卡片的点击")
        self.assertIn("grid-template-columns: minmax(150px, 200px) minmax(0, 1fr)", code)
        self.assertIn("line-height: 1.7", code)


class MotionTests(unittest.TestCase):
    """不确定态进度条纪律：这一层不引入任何动效。"""

    def test_no_animation_or_keyframes(self) -> None:
        code = strip_comments(read(LEDGER))
        self.assertNotIn("animation", code)
        self.assertNotIn("@keyframes", code)

    def test_transition_is_not_reintroduced(self) -> None:
        code = strip_comments(read(LEDGER))
        self.assertNotIn("transition:", code.replace("transition: none", ""))


if __name__ == "__main__":
    unittest.main()
