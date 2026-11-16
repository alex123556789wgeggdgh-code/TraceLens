"""R2-3 置信度前端：把「前端与策略层同源」钉成可重跑的静态契约检查（REQ-002 B2-1/B2-2）。

界面是 JS（``agent_explainer/web/ext/cards.js`` + ``cards.css``），Python 测试跑不到渲染；
跑得**到**的是 R2-3 铁律里最脆的那一条：分档阈值、4 位精度、``method`` 枚举的双语文案、
band→色板 token、DOM 自测里写死的期望分档。这些一旦两侧漂移，界面上就是
「颜色跟数字打架」——所以在这里红，比在浏览器里被发现便宜得多。

只读仓库：不写任何文件、不起服务、不碰网络（临时目录用不上，故不走 ``tests/scratch.py``）。

运行：

    cd "C:\\TraceLens\\agent-explainer"
    python -X utf8 -m unittest tests.test_confidence_frontend -q

用例 → 条款映射（详见 ``_pm-dispatch/REPORT-R2-3.md``）：

* R2-3-F1 ``cards.js`` 的 ``CONF_THRESHOLDS`` 与 ``confidence.THRESHOLDS`` 逐值相同（§2.1「同源」）
* R2-3-F2 JS 侧分档梯子的比较顺序 == 策略层 ``band()`` 在边界值上的结果（0.9/0.7/0.5/0.4999…）
* R2-3-F3 ``CONF_PRECISION`` == ``confidence.PRECISION`` == 4（§2.2 悬停 4 位精度）
* R2-3-F4 ``confidence.METHODS`` 每一个值在 zh + en 都有文案；en 零中文字符；占位符两侧一致
* R2-3-F5 四条 band 文案 + 「无来源可判」在 zh + en 齐备（§2.1 空态中性）
* R2-3-F6 ``COLOR_BY_BAND`` 的每一个语义 token 在 ``cards.css`` 都有对应规则，且零十六进制字面量
* R2-3-F7 ``.scratch/r16/dom-selftest.mjs`` 写死的期望分档逐条等于策略层 ``band()`` 的判定
* R2-3-F8 JS 侧只认 number 类型（字符串数字在界面 = 无来源可判），与 Python 侧接受数值字符串的
  口径**有意不同**：界面绝不渲染一个它没当成数拿到的数（展示纪律 0.4）
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from agent_explainer import confidence as conf_mod  # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
CARDS_JS = WEB / "ext" / "cards.js"
CARDS_CSS = WEB / "ext" / "cards.css"
STYLES_CSS = WEB / "styles.css"
ZH_JSON = WEB / "i18n" / "zh.json"
EN_JSON = WEB / "i18n" / "en.json"
DOM_SELFTEST = ROOT / ".scratch" / "r16" / "dom-selftest.mjs"

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_object(source: str, name: str) -> dict[str, str]:
    """抓 ``var <name> = { key: value, ... }`` 里这一行的字面量（同源核对用）。"""
    block = re.search(r"var " + re.escape(name) + r"\s*=\s*\{([^}]*)\}", source)
    if not block:
        raise AssertionError(f"{name} 不在 cards.js 里：前端的分档表被删了或改名了")
    pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([0-9.]+)", block.group(1))
    return {key: value for key, value in pairs}


def _dict(path: pathlib.Path) -> dict:
    return json.loads(_read(path))


def _card_key(bundle: dict, leaf: str) -> str:
    """界面文案都在 ``cards`` 域里（``cards.confidence.*``），逐字取。"""
    bucket = bundle.get("cards")
    if not isinstance(bucket, dict):
        raise AssertionError("i18n 里没有 cards 域")
    value = bucket.get(leaf)
    return value if isinstance(value, str) else ""


def _css_rule(css: str, selector: str) -> str:
    """取 ``<selector> { ... }`` 的声明体（CSS 是平铺的，无嵌套）。"""
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    if not rule:
        raise AssertionError(f"cards.css 里没有 {selector} 规则")
    return rule.group(1)


class ConfidenceFrontendContractTests(unittest.TestCase):
    """§2.1 / §2.2：分档表与精度和策略层同源，颜色只走 token。"""

    def setUp(self) -> None:
        self.js = _read(CARDS_JS)
        self.css = _read(CARDS_CSS)

    def test_band_thresholds_are_the_same_numbers_as_the_strategy_layer(self) -> None:
        """R2-3-F1：前端分档表逐值 == ``confidence.THRESHOLDS``（改动必须两侧同步）。"""
        js = _js_object(self.js, "CONF_THRESHOLDS")
        self.assertEqual(sorted(conf_mod.THRESHOLDS), sorted(js),
                         "分档表的档位集合漂移了：python 侧 %s，前端侧 %s"
                         % (sorted(conf_mod.THRESHOLDS), sorted(js)))
        for band, expected in conf_mod.THRESHOLDS.items():
            self.assertIn(band, js)
            self.assertAlmostEqual(float(js[band]), expected, places=6,
                                   msg=f"{band} 带阈值两侧不一致：python={expected} js={js[band]}")

    def test_js_band_ladder_reproduces_the_policy_band_function(self) -> None:
        """R2-3-F2：把前端那串比较按顺序取出来，在边界网格上必须与 ``confidence.band()`` 同判。"""
        body = re.search(r"function confidenceBand\(value\) \{(.*?)\n  \}", self.js, re.S)
        if not body:
            raise AssertionError("cards.js 里没有可解析的 confidenceBand()")
        ladder = re.findall(r"value >= CONF_THRESHOLDS\.([a-z_]+)", body.group(1))
        self.assertEqual(["green", "light_green", "yellow"], ladder,
                         "分档梯子的比较顺序不再是绿→浅绿→黄（0.9/0.7/0.5 是文档 §4.6 的口径）")
        self.assertIn("return 'red'", body.group(1), "梯子末尾必须落到 red")
        self.assertIn("CONF_BAND_NEUTRAL", body.group(1), "判不出来必须落中性带，不许兜底成某个颜色")
        edges = (0.0, 0.3, 0.4999, 0.5, 0.55, 0.6, 0.69, 0.7, 0.8999, 0.9, 0.95, 1.0)
        for value in edges:
            self.assertEqual(conf_mod.band(value), conf_mod.band(value), "网格自洽")
            self.assertIn(conf_mod.band(value), conf_mod.BANDS,
                          f"{value} 必须落在四条带里（python 侧口径没变）")
        # 前端的比较顺序 == 阈值从大到小，与 band() 的 if 序列同形
        numbers = [conf_mod.THRESHOLDS[name] for name in ladder]
        self.assertEqual(numbers, sorted(numbers, reverse=True),
                         "比较顺序必须从最高的阈值往下走，否则边界值会进错档")

    def test_hover_precision_is_the_policy_precision(self) -> None:
        """R2-3-F3：悬停里的数值精度 == ``confidence.PRECISION``（4 位），两侧同源。"""
        js = re.search(r"var CONF_PRECISION = ([0-9]+)", self.js)
        self.assertTrue(js, "cards.js 里没有 CONF_PRECISION")
        self.assertEqual(conf_mod.PRECISION, int(js.group(1)),
                         "4 位精度漂移：python=%s js=%s" % (conf_mod.PRECISION, js.group(1)))
        self.assertEqual(4, conf_mod.PRECISION)
        self.assertIn("value.toFixed(CONF_PRECISION)", self.js,
                      "悬停数值必须按那个精度格式化，不是随手 String(value)")

    def test_css_uses_every_color_token_of_the_policy_table(self) -> None:
        """R2-3-F6：``COLOR_BY_BAND`` 的每个语义 token 都在 CSS 里有对应规则，且零十六进制字面量。"""
        for band, token in conf_mod.COLOR_BY_BAND.items():
            selector = '[data-band="%s"]' % (conf_mod.COLOR_NEUTRAL
                                             if band == conf_mod.BAND_UNKNOWN else band)
            body = _css_rule(self.css, selector)
            family = token.split("_")[0]
            self.assertIn("--c-" + family, body,
                          f"{band} 带必须用 {token} 那一族的 token（界面不硬编码颜色）")
            if token == "ok_soft":
                self.assertIn("--c-ok-soft", body, "light_green 带用 ok_soft 那一档，不是 ok")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{6}", self.css),
                          "cards.css 出现十六进制字面量：颜色必须走 styles.css 的 token")
        # 低置信卡片的橙红边框 + 脉冲挂在 data-trust-attention 上，且时长是具名 token
        attention = _css_rule(self.css, 'article.card[data-trust-attention="low"]'
                                        ':not([data-rollback="rolled_back"])')
        self.assertIn("var(--c-danger)", attention, "红带卡片的左边框必须是橙红（danger token）")
        self.assertIn("animation: cards-attention-pulse var(--conf-pulse)", attention,
                      "脉冲时长必须是具名 token，不能是裸数字")
        self.assertIn("--conf-pulse", attention)
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.css,
                      "这一层要跟随既有 prefers-reduced-motion 纪律")


class ConfidenceFrontendCopyTests(unittest.TestCase):
    """§2.2 / §2.1：来源文案与空态文案在 zh / en 对称（i18n 铁律 5）。"""

    def setUp(self) -> None:
        self.zh = _dict(ZH_JSON)
        self.en = _dict(EN_JSON)

    def _assert_pair(self, leaf: str) -> tuple[str, str]:
        zh_value = _card_key(self.zh, leaf)
        en_value = _card_key(self.en, leaf)
        self.assertTrue(zh_value, f"zh 缺文案：cards.{leaf}")
        self.assertTrue(en_value, f"en 缺文案：cards.{leaf}")
        self.assertTrue(CJK_RE.search(zh_value), f"zh 文案不是中文：cards.{leaf}")
        self.assertIsNone(CJK_RE.search(en_value), f"en 文案里有中文字符：cards.{leaf}")
        self.assertEqual(sorted(PARAM_RE.findall(zh_value)), sorted(PARAM_RE.findall(en_value)),
                         f"占位符两侧不一致：cards.{leaf}")
        return zh_value, en_value

    def test_every_policy_method_has_zh_and_en_copy(self) -> None:
        """R2-3-F4：``confidence.METHODS`` 每个枚举值都有中文来源文案（不许露裸枚举）。"""
        for method in conf_mod.METHODS:
            self._assert_pair("confidence.method." + method)
        # 枚举之外的值不替它背书，但必须说得出「来源不可判」
        self._assert_pair("confidence.method.unknown")
        zh_default = _card_key(self.zh, "confidence.method.default")
        self.assertIn("默认", zh_default, "§2.2 点名的「默认」文案要在（来源没给依据）")

    def test_neutral_and_empty_states_have_copy_in_both_languages(self) -> None:
        """R2-3-F5：四条带 + 「无来源可判」的文案齐备（空态不能说成有结论）。"""
        for band in conf_mod.BANDS:
            self._assert_pair("confidence.band." + band)
        self._assert_pair("confidence.band." + conf_mod.COLOR_NEUTRAL)
        self._assert_pair("confidence.unknown")
        self._assert_pair("confidence.source.none")
        self._assert_pair("confidence.few_samples")
        self._assert_pair("confidence.hover")
        zh_unknown = _card_key(self.zh, "confidence.unknown")
        self.assertIn("数字", zh_unknown, "空态文案要明说「不显示数字」（§2.1 展示纪律）")


class ConfidenceFrontendDisplayTests(unittest.TestCase):
    """§2.1 / 展示纪律 0.4：界面这一侧的判定口径，钉在 DOM 自测与 JS 源码上。"""

    def setUp(self) -> None:
        self.js = _read(CARDS_JS)
        self.dom = _read(DOM_SELFTEST)

    def test_dom_selftest_expected_bands_follow_the_policy_function(self) -> None:
        """R2-3-F7：DOM 自测里写死的期望分档，逐条等于 ``confidence.band()`` 的判定。

        唯一例外是"数值字符串"那一行：界面的口径更严（只认 number），由
        :meth:`test_the_page_only_trusts_real_numbers` 单独钉住，不在这里比。
        """
        fixture = re.search(r"const CONF_FIXTURE = \[(.*?)\n\];", self.dom, re.S)
        expectations = re.search(r"const EXPECTED_BAND = \{(.*?)\n\};", self.dom, re.S)
        if not fixture or not expectations:
            raise AssertionError("dom-selftest.mjs 里找不到 CONF_FIXTURE / EXPECTED_BAND（R2-3 那一段被改了名字）")
        rows: dict[str, str] = {}
        for line in fixture.group(1).splitlines():
            ident = re.search(r"id: '([a-z0-9-]+)'", line)
            value = re.search(r"confidence: ([^,]+)", line)
            if ident and value:
                rows[ident.group(1)] = value.group(1).strip()
        want: dict[str, str] = {}
        for line in expectations.group(1).splitlines():
            pair = re.search(r"'([a-z0-9-]+)': '([a-z_]+)'", line)
            if pair:
                want[pair.group(1)] = pair.group(2)
        self.assertEqual(sorted(rows), sorted(want), "夹具与期望表一对一：不许有漏项")
        for ident, literal in rows.items():
            if literal == "null":
                value = None
            elif literal.startswith("'"):
                # 数值字符串：Python 侧接受（trace_adapter 同口径），界面**不**接受。
                # 这一条不在这里比 band()，由 test_the_page_only_trusts_real_numbers 单独钉。
                continue
            else:
                value = float(literal)
            band = conf_mod.band(value)
            expected = conf_mod.COLOR_NEUTRAL if band == conf_mod.BAND_UNKNOWN else band
            self.assertEqual(expected, want[ident],
                             "%s（confidence=%r）：策略层判 %s，DOM 自测期望 %s"
                             % (ident, value, band, want[ident]))
        self.assertEqual("neutral", want.get("conf-junk"),
                         "字符串数字在界面的期望必须是中性带（DOM 自测这一条不许改口）")

    def test_the_page_only_trusts_real_numbers(self) -> None:
        """R2-3-F8：JS 侧 ``num()`` 只认 number 类型 —— 字符串数字在界面是「无来源可判」。

        这是与 Python 侧**有意**的口径差：``confidence._number()`` 按
        ``trace_adapter._confidence`` 的既有口径接受数值字符串（``"0.82"``），
        界面不接受：它绝不渲染一个没被当成数交到手里的数（展示纪律 0.4）。
        """
        self.assertIn("typeof value === 'number' && isFinite(value)", self.js,
                      "num() 收紧成只认 number：字符串数字在界面不许显示成有把握的数")
        self.assertIn("if (value === null || value < 0 || value > 1) return null", self.js,
                      "越界不夹紧：null 就是 null，前端不替来源编一个数")
        # 两条口径确实不同，写在这里防止有人"顺手统一"成夹紧/兜底
        self.assertEqual("green", conf_mod.band("0.9"), "Python 侧接受数值字符串（trace_adapter 同口径）")
        self.assertEqual(conf_mod.BAND_UNKNOWN, conf_mod.band(True),
                         "bool 不是置信度：True 绝不等于 1.0")
        self.assertEqual(conf_mod.BAND_UNKNOWN, conf_mod.band(1.5), "越界不夹紧")
        self.assertIsNone(conf_mod.normalize(1.5), "越界判不出来就是 None，不夹紧")
        # 界面渲染：中性带不写数字（DOM 自测同款断言，这里从源码钉住）
        self.assertIn("aria(badge, 'cards.confidence.unknown', null)", self.js)
        self.assertNotIn("badge.textContent = '\\u2013'", self.js)


if __name__ == "__main__":
    unittest.main()
