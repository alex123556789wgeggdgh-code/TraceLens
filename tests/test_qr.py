"""QR 码编码/解码验收测试（``agent_explainer/qr.py``）。

运行（离线；只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_qr.py" -v
    python tests/test_qr.py

覆盖：
  1. 往返：≥40 条字符串（空串/ASCII/中文/emoji/64 位十六进制根哈希/JSON/长文本）
     在 L/M/Q/H 四个纠错等级下 ``decode(encode(s)) == s``；
  2. 容量边界：v1–v12 × L/M/Q/H 共 48 组，恰好塞满可编码、多 1 字节必须报错；
  3. 结构：方阵、边长 ``4v+17``、三处定位图形与分隔符、定时图形、固定深色模块、
     校正图形、无静区；
  4. 掩码：8 种数据掩码逐一强制生成并解码（编码器只选最优，解码器必须都读得懂）；
  5. 篡改：翻转数据模块 / 两份格式信息 / 两份版本信息 → 报错或解出不同文本，
     **不允许**静默返回原文；
  6. 渲染：``to_svg`` 自带 ``viewBox``、零脚本零外链；``to_ascii``/``to_pbm`` 基本形状；
  7. 入参错误：等级/版本/文本类型、矩阵非法、超出 v12 等。

本文件只用标准库；解码器就是编码器的反向校验器（``decode`` 不依赖任何第三方）。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_explainer import qr  # noqa: E402


def _filler(size: int) -> str:
    """恰好 ``size`` 个 UTF-8 字节的确定性 ASCII 载荷（用于容量边界）。"""
    return ("tracelens-qr-" * (size // 13 + 1))[:size]


def _version_of(matrix: list[list[int]]) -> int:
    size = len(matrix)
    return (size - 17) // 4


SAMPLE_STRINGS = [
    "",                                             # 空串：零长度载荷
    "0",
    "a",
    "TraceLens",
    "did:tracelens:agent:mmg42klgyqzwneiskrelxms3j72bfje4",
    "sha256:" + "9f" * 32,                          # 64 位十六进制根哈希
    '{"did":"did:tracelens:agent:x","sig":"AAAA","ts":1760000000}',
    "责任认定：签名背书",
    "秘密不是秘密，能复核的才叫证据。",
    "✅🎯🔐 emoji 往返",
    "line1\nline2\ttabbed",
    'quote" backslash\\ slash/',
    "  spaces  ",
    "0" * 64,
    "A" * 100,
    "é" * 50,
    "中" * 30,
    "x" * 120,
    "TraceLens-2026-11-11T20:29:24+08:00",
    "tel:+86-000-0000-0000",
    "http://127.0.0.1:65297/records",               # 只是文本，不会真的访问
    "trace_id=abc123;span_id=def456;decision=approve",
    "The quick brown fox jumps over the lazy dog.",
    "0123456789" * 5,
    "-._~:/?#[]@!$&'()*+,;=",                       # URL 保留字符
    "中文 mixed with ASCII 123",
    "第一行\n第二行\n第三行",
    "责任认定" * 10,
    "证据链根哈希：d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
    " ",
    "\t",
    "null",
    "true",
    "[]",
    "{}",
    "v1",
] + [f"payload-{index:02d}-责任认定-签收" for index in range(20)]


class TestRoundTrip(unittest.TestCase):
    """往返是编码正确性的第一道证据；四个纠错等级都要过。"""

    def test_sample_strings_all_levels(self) -> None:
        self.assertGreaterEqual(len(SAMPLE_STRINGS), 40, "样例必须 ≥40 条")
        for level in qr.ECC_LEVELS:
            for text in SAMPLE_STRINGS:
                with self.subTest(level=level, text=text[:24]):
                    matrix = qr.encode(text, ecc=level)
                    self.assertEqual(qr.decode(matrix), text)

    def test_empty_string(self) -> None:
        for level in qr.ECC_LEVELS:
            matrix = qr.encode("", ecc=level)
            self.assertEqual(qr.decode(matrix), "")
            self.assertEqual(len(matrix), 21)      # 空载荷 v1 就够

    def test_64_hex_root_hash(self) -> None:
        root = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
        self.assertEqual(len(root), 64)
        for level in qr.ECC_LEVELS:
            self.assertEqual(qr.decode(qr.encode(root, ecc=level)), root)

    def test_all_eight_masks_decode(self) -> None:
        # 公开 encode() 只返回罚分最低的那个掩码；这里用内部函数强制 8 种掩码，
        # 证明 decode() 的格式信息读取与反掩码对每个掩码都成立。
        text = "mask-coverage-责任认定"
        for level in qr.ECC_LEVELS:
            version = 4
            payload = text.encode("utf-8")
            data = qr._encode_data_codewords(payload, version, level)
            codewords = qr._interleave(data, version, level)
            for mask in range(8):
                matrix, function = qr._build_function_patterns(version)
                qr._place_data(matrix, function, codewords)
                qr._apply_mask(matrix, function, mask)
                qr._write_format(matrix, len(matrix), level, mask)
                with self.subTest(level=level, mask=mask):
                    self.assertEqual(qr.decode(matrix), text)

    def test_format_bits_bch_roundtrip(self) -> None:
        for level in qr.ECC_LEVELS:
            for mask in range(8):
                bits = qr._format_bits(level, mask)
                self.assertEqual(bits >> 15, 0, "格式信息必须恰好 15 bit")
                unmasked = bits ^ 0x5412
                self.assertEqual(qr._bch_remainder(unmasked, 0x537), 0)
                data = unmasked >> 10
                self.assertEqual(data & 0b111, mask)
                self.assertEqual(qr._ECC_FROM_BITS[(data >> 3) & 0b11], level)

    def test_version_bits_bch(self) -> None:
        for version in range(7, 13):
            bits = qr._version_bits(version)
            self.assertEqual(bits >> 18, 0, "版本信息必须恰好 18 bit")
            self.assertEqual(bits >> 12, version)
            self.assertEqual(qr._bch_remainder(bits, 0x1F25), 0)


class TestCapacityBoundaries(unittest.TestCase):
    """48 组版本×等级：塞满能编码、再多 1 字节必须拒绝。"""

    def test_exact_capacity_and_overflow(self) -> None:
        for version in range(qr.MIN_VERSION, qr.MAX_VERSION + 1):
            for level in qr.ECC_LEVELS:
                capacity = qr.byte_capacity(version, level)
                text = _filler(capacity)
                self.assertEqual(len(text.encode("utf-8")), capacity)
                with self.subTest(version=version, level=level, case="exact"):
                    matrix = qr.encode(text, ecc=level, min_version=version,
                                       max_version=version)
                    self.assertEqual(len(matrix), 4 * version + 17)
                    self.assertEqual(qr.decode(matrix), text)
                with self.subTest(version=version, level=level, case="overflow"):
                    with self.assertRaises(qr.QRError) as ctx:
                        qr.encode(text + "x", ecc=level, min_version=version,
                                  max_version=version)
                    self.assertEqual(ctx.exception.code, "QR_DATA_TOO_LONG")

    def test_utf8_multibyte_boundary(self) -> None:
        # 中文是 3 字节/字：用「中文尽量多 + ASCII 补足」精确塞满，验证按字节而不是按字符算容量
        for version, level in ((1, "M"), (5, "Q"), (9, "H"), (12, "L")):
            capacity = qr.byte_capacity(version, level)
            chinese = capacity // 3
            text = "认" * chinese + "a" * (capacity - chinese * 3)
            self.assertEqual(len(text.encode("utf-8")), capacity)
            matrix = qr.encode(text, ecc=level, min_version=version, max_version=version)
            self.assertEqual(qr.decode(matrix), text)

    def test_version_auto_selection(self) -> None:
        cap_v1_m = qr.byte_capacity(1, "M")
        self.assertEqual(_version_of(qr.encode(_filler(cap_v1_m), ecc="M")), 1)
        self.assertEqual(_version_of(qr.encode(_filler(cap_v1_m + 1), ecc="M")), 2)
        # min_version 必须被尊重（即使内容放得下更小的版本）
        matrix = qr.encode("x", ecc="M", min_version=5)
        self.assertEqual(_version_of(matrix), 5)
        self.assertEqual(qr.decode(matrix), "x")
        # max_version 收紧后放不下必须报错
        with self.assertRaises(qr.QRError) as ctx:
            qr.encode(_filler(qr.byte_capacity(2, "L") + 1), ecc="L", max_version=2)
        self.assertEqual(ctx.exception.code, "QR_DATA_TOO_LONG")


class TestMatrixStructure(unittest.TestCase):
    """矩阵结构是扫码器能认出来的前提，用几何断言钉住。"""

    @staticmethod
    def _check_finder(matrix: list[list[int]], top: int, left: int) -> None:
        size = len(matrix)
        for delta_row in range(7):
            for delta_col in range(7):
                expected = (delta_row in (0, 6) or delta_col in (0, 6)
                            or (2 <= delta_row <= 4 and 2 <= delta_col <= 4))
                assert matrix[top + delta_row][left + delta_col] == int(expected), (
                    f"定位图形 ({top},{left}) 内 ({delta_row},{delta_col}) 不符")
        # 分隔符：紧贴 7×7 的一圈必须是浅色（越界部分跳过）
        for delta in range(-1, 8):
            for row, col in ((top - 1, left + delta), (top + 7, left + delta),
                             (top + delta, left - 1), (top + delta, left + 7)):
                if 0 <= row < size and 0 <= col < size:
                    assert matrix[row][col] == 0, f"分隔符 ({row},{col}) 应为浅色"

    def test_square_size_and_binary(self) -> None:
        for version in (1, 2, 7, 12):
            matrix = qr.encode(_filler(qr.byte_capacity(version, "M")), ecc="M",
                               min_version=version, max_version=version)
            self.assertEqual(len(matrix), 4 * version + 17)
            for row in matrix:
                self.assertEqual(len(row), len(matrix))
                for value in row:
                    self.assertIn(value, (0, 1))

    def test_finder_patterns_at_three_corners(self) -> None:
        for version in (1, 3, 7, 12):
            matrix = qr.encode("finder-test", ecc="M", min_version=version,
                               max_version=version)
            size = len(matrix)
            self._check_finder(matrix, 0, 0)
            self._check_finder(matrix, 0, size - 7)
            self._check_finder(matrix, size - 7, 0)

    def test_timing_pattern_and_dark_module(self) -> None:
        matrix = qr.encode(_filler(60), ecc="Q", min_version=5, max_version=5)
        size = len(matrix)
        for index in range(8, size - 8):
            self.assertEqual(matrix[6][index], 1 if index % 2 == 0 else 0,
                             f"水平定时图形第 {index} 列")
            self.assertEqual(matrix[index][6], 1 if index % 2 == 0 else 0,
                             f"垂直定时图形第 {index} 行")
        self.assertEqual(matrix[size - 8][8], 1, "固定深色模块必须为深色")

    def test_alignment_patterns(self) -> None:
        # v7 的校正图形中心包含 (22,22)（不在定位图形上，必然存在）
        matrix = qr.encode("alignment", ecc="L", min_version=7, max_version=7)
        center = 22
        for delta_row in range(-2, 3):
            for delta_col in range(-2, 3):
                ring = max(abs(delta_row), abs(delta_col))
                self.assertEqual(matrix[center + delta_row][center + delta_col],
                                 1 if ring in (0, 2) else 0)
        # v1 没有校正图形：矩阵里不应出现 5×5 的「黑环+黑心」结构（除定位图形中心外）
        # ——用「编码正确」间接证明即可，这里只断言 v1 尺寸。
        self.assertEqual(len(qr.encode("x", ecc="H")), 21)

    def test_no_quiet_zone(self) -> None:
        matrix = qr.encode("quiet-zone", ecc="M")
        # 无静区：左上角模块是定位图形的深色角，而不是一圈浅色边
        self.assertEqual(matrix[0][0], 1)
        self.assertEqual(matrix[0][len(matrix) - 1], 1)
        self.assertEqual(matrix[len(matrix) - 1][0], 1)


class TestCorruption(unittest.TestCase):
    """被改动的码面必须报错（或至少解出不同文本），不能静默返回原文。"""

    def _data_positions(self, matrix: list[list[int]]) -> list[tuple[int, int]]:
        version = _version_of(matrix)
        _base, function = qr._build_function_patterns(version)
        return qr._data_positions(len(matrix), function)

    def test_single_data_module_flip(self) -> None:
        text = "tamper-evident-责任认定"
        matrix = qr.encode(text, ecc="Q")
        positions = self._data_positions(matrix)
        # 前几个数据模块承载模式/计数/载荷开头，翻转必然破坏码字
        for row, col in positions[:8]:
            corrupted = [line[:] for line in matrix]
            corrupted[row][col] ^= 1
            with self.subTest(module=(row, col)):
                try:
                    decoded = qr.decode(corrupted)
                except qr.QRError:
                    pass
                else:
                    self.assertNotEqual(decoded, text, "翻转数据模块后仍静默返回原文")

    def test_many_flips_rejected(self) -> None:
        matrix = qr.encode(_filler(40), ecc="M", min_version=4, max_version=4)
        positions = self._data_positions(matrix)
        step = max(1, len(positions) // 24)
        corrupted = [line[:] for line in matrix]
        for row, col in positions[::step][:24]:
            corrupted[row][col] ^= 1
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode(corrupted)
        self.assertEqual(ctx.exception.code, "QR_ECC_CORRUPTED")

    def test_format_info_corruption_rejected(self) -> None:
        matrix = qr.encode("format-info", ecc="M")
        size = len(matrix)
        positions = qr._format_positions(size)
        corrupted = [line[:] for line in matrix]
        for index in (0, 15):         # 同一 bit 的两份副本一起翻，BCH 必须发现
            row, col = positions[index]
            corrupted[row][col] ^= 1
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode(corrupted)
        self.assertEqual(ctx.exception.code, "QR_FORMAT_INVALID")

    def test_version_info_corruption_rejected(self) -> None:
        matrix = qr.encode("version-info", ecc="L", min_version=7, max_version=7)
        size = len(matrix)
        corrupted = [line[:] for line in matrix]
        # 两份版本信息的第一位一起翻（bit 0：左上区域 (row 0, col size-11) 与镜像）
        near = size - 11
        corrupted[0][near] ^= 1
        corrupted[near][0] ^= 1
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode(corrupted)
        self.assertEqual(ctx.exception.code, "QR_VERSION_INFO_INVALID")

    def test_structure_damage_rejected(self) -> None:
        matrix = qr.encode("structure", ecc="M")
        # 去掉一行：不再是 4v+17 方阵
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode(matrix[:-1])
        self.assertEqual(ctx.exception.code, "QR_MATRIX_INVALID")
        # 加静区再喂进去：解码器要求「无静区」矩阵
        bordered = [[0] * (len(matrix) + 2)] + [[0] + row + [0] for row in matrix] + \
                   [[0] * (len(matrix) + 2)]
        with self.assertRaises(qr.QRError):
            qr.decode(bordered)


class TestRenderers(unittest.TestCase):
    """导出件要能直接打印/打开：SVG 必须独立，ASCII/PBM 必须尺寸正确。"""

    def setUp(self) -> None:
        self.matrix = qr.encode("render-责任认定", ecc="M")
        self.size = len(self.matrix)
        self.dark = sum(sum(row) for row in self.matrix)

    def test_svg_is_standalone(self) -> None:
        svg = qr.to_svg(self.matrix, scale=4, border=4)
        side = (self.size + 8) * 4
        self.assertIn(f'viewBox="0 0 {side} {side}"', svg)
        self.assertTrue(svg.startswith('<?xml version="1.0"'))
        # 唯一允许出现的 http 是 SVG 命名空间；不得有脚本/图片/外链
        self.assertEqual(svg.count("http"), 1)
        self.assertIn("http://www.w3.org/2000/svg", svg)
        for forbidden in ("<script", "<image", "xlink:href", "href=", "<use", "url("):
            self.assertNotIn(forbidden, svg)
        # 背景 1 个 rect + 每个深色模块 1 个 rect
        self.assertEqual(svg.count("<rect"), self.dark + 1)
        self.assertTrue(svg.rstrip().endswith("</svg>"))

    def test_svg_scale_and_border_affect_viewbox(self) -> None:
        svg = qr.to_svg(self.matrix, scale=2, border=1)
        side = (self.size + 2) * 2
        self.assertIn(f'viewBox="0 0 {side} {side}"', svg)

    def test_ascii_shape(self) -> None:
        ascii_art = qr.to_ascii(self.matrix, border=2)
        lines = ascii_art.rstrip("\n").split("\n")
        self.assertEqual(len(lines), self.size + 4)
        width = 2 * (self.size + 4)
        for line in lines:
            self.assertEqual(len(line), width)
        self.assertIn("██", ascii_art)
        self.assertTrue(ascii_art.endswith("\n"))

    def test_pbm_shape(self) -> None:
        pbm = qr.to_pbm(self.matrix, border=4)
        parts = pbm.split(b"\n", 2)
        self.assertEqual(parts[0], b"P4")
        width = self.size + 8
        self.assertEqual(parts[1], f"{width} {width}".encode())
        body = parts[2]
        self.assertEqual(len(body), ((width + 7) // 8) * width)
        # 左上角静区必须是白（bit=0）
        self.assertEqual(body[0] & 0x80, 0)

    def test_render_rejects_bad_options(self) -> None:
        with self.assertRaises(qr.QRError):
            qr.to_svg(self.matrix, scale=0)
        with self.assertRaises(qr.QRError):
            qr.to_svg(self.matrix, border=-1)
        with self.assertRaises(qr.QRError):
            qr.to_ascii([[0, 1], [1, 0]])


class TestErrors(unittest.TestCase):
    """入参错误必须明确报错，而不是生成一个「看起来像 QR」的矩阵。"""

    def test_bad_ecc(self) -> None:
        for bad in ("X", "", None, 3):
            with self.assertRaises(qr.QRError) as ctx:
                qr.encode("x", ecc=bad)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "QR_ECC_INVALID")

    def test_bad_versions(self) -> None:
        cases = [dict(min_version=0), dict(max_version=13), dict(min_version=5, max_version=3),
                 dict(min_version="1")]  # type: ignore[dict-item]
        for kwargs in cases:
            with self.assertRaises(qr.QRError) as ctx:
                qr.encode("x", **kwargs)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "QR_VERSION_INVALID")

    def test_bad_text(self) -> None:
        for bad in (b"bytes", 123, None):
            with self.assertRaises(qr.QRError) as ctx:
                qr.encode(bad)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "QR_TEXT_INVALID")

    def test_data_too_long(self) -> None:
        with self.assertRaises(qr.QRError) as ctx:
            qr.encode("x" * 10_000, ecc="H")
        error = ctx.exception
        self.assertEqual(error.code, "QR_DATA_TOO_LONG")
        self.assertEqual(error.detail["bytes"], 10_000)
        self.assertGreater(error.detail["capacity"], 0)
        self.assertIn("QR_DATA_TOO_LONG", error.render())

    def test_decode_invalid_matrix(self) -> None:
        bad_matrices = [
            [],
            [[0, 1], [1, 0]],                     # 尺寸不是 4v+17
            [[0] * 21 for _ in range(20)],        # 非方阵
            [[0.0] * 21 for _ in range(21)],      # 非 0/1
            [[2] * 21 for _ in range(21)],
            "not a matrix",
        ]
        for bad in bad_matrices:
            with self.assertRaises(qr.QRError) as ctx:
                qr.decode(bad)
            self.assertEqual(ctx.exception.code, "QR_MATRIX_INVALID")

    def test_decode_unsupported_version(self) -> None:
        # v13 的尺寸（69）合法但超出本实现支持范围
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode([[0] * 69 for _ in range(69)])
        self.assertEqual(ctx.exception.code, "QR_VERSION_UNSUPPORTED")

    def test_decode_rejects_all_zero_matrix(self) -> None:
        # 全零矩阵的格式信息（15 个 0）不是合法 BCH 码字 → QR_FORMAT_INVALID。
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode([[0] * 21 for _ in range(21)])
        self.assertEqual(ctx.exception.code, "QR_FORMAT_INVALID")

    def test_decode_rejects_unsupported_mode(self) -> None:
        # 手工造一个「格式信息合法、反掩码后数据区全零」的矩阵：数据区写成掩码图案，
        # 解码时反掩码得到全零码字，RS 伴随式为 0，但模式位是 0000
        # → 必须报模式不支持，而不是返回空串。
        base, function = qr._build_function_patterns(1)
        qr._apply_mask(base, function, 0)
        qr._write_format(base, 21, "M", 0)
        with self.assertRaises(qr.QRError) as ctx:
            qr.decode(base)
        self.assertEqual(ctx.exception.code, "QR_MODE_UNSUPPORTED")

    def test_table_self_consistency(self) -> None:
        report = qr._check_tables()
        self.assertEqual(report["versions"], list(range(1, 13)))
        self.assertEqual(report["levels"], list(qr.ECC_LEVELS))
        for version in range(1, 13):
            for level in qr.ECC_LEVELS:
                specs = qr._block_spec(version, level)
                self.assertEqual(sum(data + ecc for data, ecc in specs),
                                 qr.TOTAL_CODEWORDS[version])
                # 数据码字总量就是容量公式的基数
                self.assertEqual(sum(data for data, _ecc in specs),
                                 qr._data_codeword_count(version, level))


if __name__ == "__main__":
    unittest.main(verbosity=2)
