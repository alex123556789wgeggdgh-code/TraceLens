"""功能中心（八功能独立界面）验收测试：一页一功能、零第二套实现、零假数字。

运行（全程离线、只用标准库）：

    cd agent-explainer
    python -X utf8 -m unittest tests.test_workspaces_face -v

钉住的口径（`docs/22-feature-centre.md`）：

1. **八个功能、八个页面**：``ext/workspaces.js`` 的功能表恰 8 条且 id 不重复，
   每条在 zh/en 两侧都有标题与说明；少一条就是"某个功能没有独立界面"。
2. **一页一地址**：地址栏写 ``#feature=<id>``，八个 id 都能被路由解析；关闭时
   恢复进入前的地址，不做"清空地址"这种副作用。
3. **没有第二套实现**：合规页挂载 ``app.js`` 自己的导出面板
   （``TraceLensUI.renderExportPanel``），不自己发 ``compliance/export``；插件页调
   已注册 tab 的 ``render`` / ``repaint``；回滚页调卡片插件的 ``openRollbackPlan``；
   证据页调卡片的 ``renderEvidenceAndCausal``。
4. **不造数**：源码里零定时器（``setInterval`` / ``setTimeout`` /
   ``requestAnimationFrame``），置信度、记录数、导出结果全部读自响应。
5. **文案在字典里**：``ext/workspaces.js`` 与 ``ext/workspaces.css`` 零中文字面量；
   文案走 ``workspaces.*`` 键，zh/en 两侧逐键对齐。
6. **样式只用设计令牌**：``workspaces.css`` 里 ``var(--x)`` 的每个 ``--x`` 都能在
   ``styles.css`` 里找到定义（未定义会静默回退成透明），并有窄屏布局。

纪律：只用标准库；不启动服务；不改动任何文件。
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
WEB = REPO / "agent_explainer" / "web"
EXT = WEB / "ext"

WORKSPACES_JS = EXT / "workspaces.js"
WORKSPACES_CSS = EXT / "workspaces.css"
INDEX = WEB / "index.html"
APP_JS = WEB / "app.js"
STYLES = WEB / "styles.css"
ZH = WEB / "i18n" / "zh.json"
EN = WEB / "i18n" / "en.json"
PARTS = WEB / "i18n" / "parts"

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def strip_comments(source: str) -> str:
    """去掉块注释与行注释：注释里点名解释某个规则，不算违规。"""
    without_block = re.sub(r"/\*[\s\S]*?\*/", " ", source)
    return re.sub(r"//[^\n]*", " ", without_block)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def feature_ids(js: str) -> list[str]:
    block = re.search(r"var FEATURES = \[([\s\S]*?)\n  \];", js)
    assert block is not None, "ext/workspaces.js 丢失了功能表"
    return re.findall(r"id:\s*'([a-z]+)'", block.group(1))


class AssetsTests(unittest.TestCase):
    """两个资源都要真的被页面加载，且顺序不早于它托管的插件。"""

    def test_assets_are_loaded_and_ordered_last(self) -> None:
        html = read(INDEX)
        self.assertIn("ext/workspaces.js", html, "index.html 没有加载 ext/workspaces.js")
        self.assertIn("ext/workspaces.css", html, "index.html 没有加载 ext/workspaces.css")
        self.assertLess(html.index("ext/update.js"), html.index("ext/workspaces.js"),
                        "workspaces.js 必须最后加载：它要托管前面注册的插件")

    def test_entry_point_is_a_dialog_button(self) -> None:
        js = read(WORKSPACES_JS)
        self.assertIn("'aria-haspopup': 'dialog'", js, "入口没有声明它是对话框")
        self.assertIn("feature-center-entry", js, "顶栏入口的 hook 不见了")


class FeatureTableTests(unittest.TestCase):
    """功能表就是"八个功能"这件事的唯一事实来源。"""

    def test_exactly_eight_unique_ids(self) -> None:
        ids = feature_ids(read(WORKSPACES_JS))
        self.assertEqual(8, len(ids), f"功能表有 {len(ids)} 条，应为 8")
        self.assertEqual(8, len(set(ids)), "功能 id 有重复")

    def test_expected_features_present(self) -> None:
        ids = set(feature_ids(read(WORKSPACES_JS)))
        self.assertEqual(
            {"rollback", "confidence", "evidence", "groups", "collab",
             "analytics", "suggestions", "compliance"},
            ids,
        )

    def test_every_feature_has_copy_on_both_sides(self) -> None:
        zh = json.loads(read(ZH))["workspaces"]
        en = json.loads(read(EN))["workspaces"]
        for fid in feature_ids(read(WORKSPACES_JS)):
            for leaf in ("title", "desc"):
                key = f"feature.{fid}.{leaf}"
                self.assertIn(key, zh, f"zh.json 缺 workspaces.{key}")
                self.assertIn(key, en, f"en.json 缺 workspaces.{key}")

    def test_parts_have_parity(self) -> None:
        zh_part = json.loads(read(PARTS / "workspaces.zh.json"))["workspaces"]
        en_part = json.loads(read(PARTS / "workspaces.en.json"))["workspaces"]
        self.assertEqual(set(zh_part), set(en_part), "zh 与 en 片段的键不一致")
        for key, value in en_part.items():
            self.assertFalse(CJK.search(value), f"en 片段里出现中文：{key}")
            self.assertTrue(value.strip(), f"en 片段有空值：{key}")


class NoSecondImplementationTests(unittest.TestCase):
    """功能中心只是壳：每个功能仍然由原来那份实现负责。"""

    def setUp(self) -> None:
        self.js = strip_comments(read(WORKSPACES_JS))

    def test_compliance_mounts_the_audit_panel(self) -> None:
        self.assertIn("renderExportPanel", self.js,
                      "合规页没有挂载审计导出面板")
        self.assertNotIn("compliance/export", self.js,
                         "ext/workspaces.js 长出了自己的导出请求")

    def test_app_exposes_one_export_panel(self) -> None:
        app = read(APP_JS)
        self.assertIn("renderExportPanel: function (host)", app,
                      "app.js 没有把导出面板暴露成可挂载的渲染器")

    def test_plugin_pages_delegate(self) -> None:
        self.assertIn("plugin.tab.render(mount, C)", self.js,
                      "插件页没有调用已注册 tab 的 render")
        self.assertIn("plugin.tab.repaint(", self.js,
                      "插件页在状态变化后没有重绘")

    def test_card_pages_reuse_the_card_plugin(self) -> None:
        self.assertIn("openRollbackPlan", self.js,
                      "回滚页没有复用卡片插件的计划对话框")
        self.assertIn("renderEvidenceAndCausal", self.js,
                      "证据页没有复用卡片的证据渲染")

    def test_route_pattern_is_declared(self) -> None:
        self.assertIn("#feature=([a-z]+)", self.js, "地址路由的正则不见了")


class HonestyTests(unittest.TestCase):
    """不造数字：没有定时器、没有中文字面量。"""

    def setUp(self) -> None:
        self.js = strip_comments(read(WORKSPACES_JS))
        self.css = strip_comments(read(WORKSPACES_CSS))

    def test_no_timers(self) -> None:
        for token in ("setInterval", "setTimeout", "requestAnimationFrame"):
            self.assertNotIn(token, self.js, f"ext/workspaces.js 里有定时器：{token}")

    def test_no_cjk_literals(self) -> None:
        self.assertFalse(CJK.search(self.js), "ext/workspaces.js 里有中文字面量")
        self.assertFalse(CJK.search(self.css), "ext/workspaces.css 里有中文字面量")

    def test_css_is_ascii(self) -> None:
        raw = read(WORKSPACES_CSS)
        self.assertTrue(all(ord(ch) < 128 for ch in raw), "workspaces.css 不是纯 ASCII")


class StyleTests(unittest.TestCase):
    """样式：[PASS] 令牌全部有定义；窄屏与滚动锁必须有。"""

    def setUp(self) -> None:
        self.css = read(WORKSPACES_CSS)

    def test_tokens_are_defined(self) -> None:
        defined = {t.rstrip().rstrip(":") for t in re.findall(r"--[a-z0-9-]+\s*:", read(STYLES))}
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", self.css))
        missing = sorted(used - defined)
        self.assertEqual([], missing, f"workspaces.css 用了未定义的令牌：{missing}")

    def test_narrow_layout_exists(self) -> None:
        self.assertIn("@media (max-width: 820px)", self.css, "没有窄屏布局")

    def test_overlay_locks_page_scroll(self) -> None:
        self.assertIn('body[data-workspace="open"]', self.css,
                      "浮层没有锁住底层页面滚动")


if __name__ == "__main__":
    unittest.main()
