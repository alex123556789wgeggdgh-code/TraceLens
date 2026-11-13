"""QR 码编解码（纯标准库）：秘书处导出件的「离线签收」载体。

为什么产品里要自带一个 QR 编码器
--------------------------------
责任认定与签名背书的产物最终要离开屏幕：打印出来的处理单、贴在证据袋上的标签、
会议纪要的附录。给一段 base64 没人愿意手抄，给一个 QR 码，手机扫一下就能把
「DID + 根哈希 + 签名」带回去复核。产品是本地优先、无网络的，不能依赖任何在线
QR 服务，也不该为了一个二维码引入第三方运行时依赖——所以这里用标准库实现
ISO/IEC 18004 的编码器，并自带一个**解码器**：解码器不是产品功能，而是测试的
反向校验器（能把自己编出来的码读回来、并且在被改坏时拒绝），也是导出件被截图/
转存后「这码还认不认」的兜底检查。

范围与边界（写清楚，别让调用方猜）
----------------------------------
* **只做字节模式（Byte mode, 0100）**：文本按 UTF-8 编码后原样入码。不做数字/字母
  数字/汉字模式，也不做 ECI：产品里的载荷是 JSON/哈希/签名，字节模式最直白。
* **版本 1–12、纠错 L/M/Q/H**：v12（65×65）字节容量最高约 2.9 KB，足够放一条
  签名记录；再大就该用文件而不是二维码。超界抛 :class:`QRError`，不静默截断。
* **输出矩阵不含静区（quiet zone）**：SVG/ASCII/PBM 由 ``border`` 参数补静区，
  调用方自己排版时也不会被重复留白坑到。
* **解码器只认本模块的产物**：它读格式信息（BCH 15,5）、版本信息（v≥7，BCH 18,6）、
  标准之字形数据区、反交织、逐块校验 RS 伴随式；但它**不纠错**、不支持结构化追加/
  FNC1/ECI，也不做图像识别（输入是 0/1 矩阵，不是照片）。遇到读不懂的结构抛
  :class:`QRError` 而不是猜。
* **二维码内容一律视为不可信输入**：解码只证明「这些模块构成一个自洽的 QR 码」，
  不证明「载荷可信」。签名校验必须另走 :mod:`agent_explainer.crypto_sig`。

实现口径（给后来改这个文件的人）
--------------------------------
* 伽罗华域 GF(256)，本原多项式 ``0x11D``，RS 生成多项式根为 ``α^0..α^(ecc-1)``；
* 数据码字按块切分（块数/每块数据量见 :data:`_BLOCK_TABLE`），数据列优先交织，
  再交织各块 ECC；放置走标准之字形，跳过功能模块，剩余位（remainder bits）补 0；
* 8 种掩码逐一试，按 ISO 的四条罚分规则（N1=3/N2=3/N3=40/N4=10）取最低分，
  平局取编号小者；格式信息随掩码一起写入后再计分（与主流实现一致）；
* 版本信息与格式信息的 BCH 生成多项式分别是 ``0x1F25`` 与 ``0x537``，格式串最后
  XOR ``0x5412``。

自检：``python -m agent_explainer.qr``（编码→解码若干样例，打印矩阵尺寸与掩码选择）。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

__all__ = [
    "QR_VERSION",
    "MIN_VERSION",
    "MAX_VERSION",
    "ECC_LEVELS",
    "QRError",
    "encode",
    "decode",
    "to_svg",
    "to_ascii",
    "to_pbm",
]

#: 本模块的编码口径版本。矩阵布局/纠错表一旦变化必须升版本，
#: 否则旧导出件上的码会被新解码器误判。
QR_VERSION = "1.0"

MIN_VERSION = 1
MAX_VERSION = 12

#: 纠错等级，从低到高；字符串口径固定大写（输入会先 upper()）。
ECC_LEVELS = ("L", "M", "Q", "H")

#: 格式信息里的 2 bit 纠错等级编码（ISO/IEC 18004 表 25）。
ECC_FORMAT_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
_ECC_FROM_BITS = {bits: level for level, bits in ECC_FORMAT_BITS.items()}

#: 每个版本的总码字数（数据 + ECC），ISO/IEC 18004 表 1；v1–v12。
TOTAL_CODEWORDS = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172,
    7: 196, 8: 242, 9: 292, 10: 346, 11: 404, 12: 466,
}

#: 分块表：``版本 -> 等级 -> ((块数, 每块总码字, 每块数据码字), ...)``，
#: 同版本同等级下各块的 ECC 长度相同（= 总码字 - 数据码字）。数值来自
#: ISO/IEC 18004 表 9；``_check_tables()`` 会复核「块数×总码字 == 总码字」。
_BLOCK_TABLE: dict[int, dict[str, tuple[tuple[int, int, int], ...]]] = {
    1: {  # 总码字 26
        "L": ((1, 26, 19),),
        "M": ((1, 26, 16),),
        "Q": ((1, 26, 13),),
        "H": ((1, 26, 9),),
    },
    2: {  # 总码字 44
        "L": ((1, 44, 34),),
        "M": ((1, 44, 28),),
        "Q": ((1, 44, 22),),
        "H": ((1, 44, 16),),
    },
    3: {  # 总码字 70
        "L": ((1, 70, 55),),
        "M": ((1, 70, 44),),
        "Q": ((2, 35, 17),),
        "H": ((2, 35, 13),),
    },
    4: {  # 总码字 100
        "L": ((1, 100, 80),),
        "M": ((2, 50, 32),),
        "Q": ((2, 50, 24),),
        "H": ((4, 25, 9),),
    },
    5: {  # 总码字 134
        "L": ((1, 134, 108),),
        "M": ((2, 67, 43),),
        "Q": ((2, 33, 15), (2, 34, 16)),
        "H": ((2, 33, 11), (2, 34, 12)),
    },
    6: {  # 总码字 172
        "L": ((2, 86, 68),),
        "M": ((4, 43, 27),),
        "Q": ((4, 43, 19),),
        "H": ((4, 43, 15),),
    },
    7: {  # 总码字 196
        "L": ((2, 98, 78),),
        "M": ((4, 49, 31),),
        "Q": ((2, 32, 14), (4, 33, 15)),
        "H": ((4, 39, 13), (1, 40, 14)),
    },
    8: {  # 总码字 242
        "L": ((2, 121, 97),),
        "M": ((2, 60, 38), (2, 61, 39)),
        "Q": ((4, 40, 18), (2, 41, 19)),
        "H": ((4, 40, 14), (2, 41, 15)),
    },
    9: {  # 总码字 292
        "L": ((2, 146, 116),),
        "M": ((3, 58, 36), (2, 59, 37)),
        "Q": ((4, 36, 16), (4, 37, 17)),
        "H": ((4, 36, 12), (4, 37, 13)),
    },
    10: {  # 总码字 346
        "L": ((2, 86, 68), (2, 87, 69)),
        "M": ((4, 69, 43), (1, 70, 44)),
        "Q": ((6, 43, 19), (2, 44, 20)),
        "H": ((6, 43, 15), (2, 44, 16)),
    },
    11: {  # 总码字 404
        "L": ((4, 101, 81),),
        "M": ((1, 80, 50), (4, 81, 51)),
        "Q": ((4, 50, 22), (4, 51, 23)),
        "H": ((3, 36, 12), (8, 37, 13)),
    },
    12: {  # 总码字 466
        "L": ((2, 116, 92), (2, 117, 93)),
        "M": ((6, 58, 36), (2, 59, 37)),
        "Q": ((4, 46, 20), (6, 47, 21)),
        "H": ((7, 42, 14), (4, 43, 15)),
    },
}

#: 校正图形中心坐标（ISO/IEC 18004 附录 E）；v1 没有校正图形。
ALIGNMENT_POSITIONS = {
    1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34),
    7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46), 10: (6, 28, 50),
    11: (6, 30, 54), 12: (6, 32, 58),
}

#: 填充字节：数据不足一个版本容量时循环用 0xEC/0x11（ISO/IEC 18004 §8.4.9）。
_PAD_BYTES = (0xEC, 0x11)
_MODE_BYTE = 0b0100

#: 罚分权重（ISO/IEC 18004 §8.8.2）。
_N1, _N2, _N3, _N4 = 3, 3, 40, 10
#: 规则 3 认的两个 11 模块窗口：1:1:3:1:1 图案左右各 4 个浅色模块。
_RULE3_PATTERNS = (
    (1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0),
    (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1),
)

#: 格式信息 BCH(15,5) 生成多项式（x^10+x^8+x^5+x^4+x^2+x+1）与 XOR 掩码。
_G15 = 0x537
_G15_MASK = 0x5412
#: 版本信息 BCH(18,6) 生成多项式（x^12+x^11+x^10+x^9+x^8+x^5+x^2+1）。
_G18 = 0x1F25


# ---------------------------------------------------------------------------
# 一、错误口径
# ---------------------------------------------------------------------------


class QRError(Exception):
    """二维码层的统一异常：``code`` 稳定、``reason`` 给人看、``as_dict()`` 可直接透传。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.field = field
        self.hint = hint
        self.detail: dict[str, Any] = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"字段 {self.field}")
        if self.hint:
            parts.append(f"建议：{self.hint}")
        return "；".join(parts)


# ---------------------------------------------------------------------------
# 二、GF(256) 与 Reed–Solomon
# ---------------------------------------------------------------------------
# 指数/对数表：EXP 扩到 510 项，乘/除时下标相加不用再取模（最多 254+254 < 510）。
_EXP = [0] * 510
_LOG = [0] * 256
_value = 1
for _index in range(255):
    _EXP[_index] = _value
    _LOG[_value] = _index
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _index in range(255, 510):
    _EXP[_index] = _EXP[_index - 255]

_RS_GENERATORS: dict[int, list[int]] = {}


def _gf_mul(a: int, b: int) -> int:
    """GF(256) 乘法；0 没有对数，单独短路。"""
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(ecc_len: int) -> list[int]:
    """``g(x) = ∏(x - α^i), i = 0..ecc_len-1``，系数按高次到低次。

    QR 的 RS 码用「根从 α^0 开始」的生成多项式（不是从 α^1 开始），这一条决定了
    解码端伴随式校验必须检查 ``S_0..S_{ecc-1}`` 全零，两边别各改各的。
    """
    cached = _RS_GENERATORS.get(ecc_len)
    if cached is not None:
        return cached
    poly = [1]
    for index in range(ecc_len):
        root = _EXP[index]
        nxt = [0] * (len(poly) + 1)
        for position, coefficient in enumerate(poly):
            nxt[position] ^= coefficient
            nxt[position + 1] ^= _gf_mul(coefficient, root)
        poly = nxt
    _RS_GENERATORS[ecc_len] = poly
    return poly


def _rs_ecc(data: Sequence[int], ecc_len: int) -> list[int]:
    """数据码字求 RS 余数（LFSR 多项式除法），返回 ``ecc_len`` 个校验码字。"""
    generator = _rs_generator(ecc_len)
    remainder = [0] * ecc_len
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            log_factor = _LOG[factor]
            for index in range(ecc_len):
                remainder[index] ^= _EXP[_LOG[generator[index + 1]] + log_factor]
    return remainder


def _rs_syndromes(block: Sequence[int], ecc_len: int) -> list[int]:
    """伴随式 ``S_j = block(α^j)``，``j = 0..ecc_len-1``；全零才说明码字没被改。"""
    syndromes = []
    for index in range(ecc_len):
        point = _EXP[index]  # α^j
        acc = 0
        for byte in block:   # Horner：系数高次在前
            acc = _gf_mul(acc, point) ^ byte
        syndromes.append(acc)
    return syndromes


# ---------------------------------------------------------------------------
# 三、表结构校验与容量
# ---------------------------------------------------------------------------


def _block_spec(version: int, level: str) -> list[tuple[int, int]]:
    """展开分块表为 ``[(每块数据码字, 每块 ECC 码字), ...]``（块的顺序即交织顺序）。"""
    triples = _BLOCK_TABLE[version][level]
    specs: list[tuple[int, int]] = []
    ecc_len: int | None = None
    for count, total, data in triples:
        block_ecc = total - data
        if ecc_len is None:
            ecc_len = block_ecc
        elif ecc_len != block_ecc:
            raise QRError("QR_TABLE_INVALID",
                          f"v{version}-{level} 各块 ECC 长度不一致",
                          detail={"first": ecc_len, "current": block_ecc})
        for _ in range(count):
            specs.append((data, block_ecc))
    return specs


def _data_codeword_count(version: int, level: str) -> int:
    """该版本/等级下所有块的数据码字总数（决定容量）。"""
    return sum(data for data, _ecc in _block_spec(version, level))


def _length_bits(version: int) -> int:
    """字节模式字符计数字段宽度：v1–9 为 8 bit，v10–26 为 16 bit。"""
    return 8 if version <= 9 else 16


def byte_capacity(version: int, level: str) -> int:
    """该版本/等级能放多少字节（不含模式指示符与计数字段）。

    暴露成公开函数（不在 ``__all__`` 里也照样能 import）：秘书处要预估「这条记录
    得用多大码」，测试也要按容量边界造样例。
    """
    _validate_version(version)
    _validate_level(level)
    return (_data_codeword_count(version, level) * 8 - 4 - _length_bits(version)) // 8


def _check_tables() -> dict[str, Any]:
    """自检用：复核分块表与总码字表自洽，返回摘要。"""
    versions = []
    for version in range(MIN_VERSION, MAX_VERSION + 1):
        for level in ECC_LEVELS:
            specs = _block_spec(version, level)
            total = sum(data + ecc for data, ecc in specs)
            if total != TOTAL_CODEWORDS[version]:
                raise QRError("QR_TABLE_INVALID",
                              f"v{version}-{level} 总码字 {total} != {TOTAL_CODEWORDS[version]}")
            if len({ecc for _data, ecc in specs}) != 1:
                raise QRError("QR_TABLE_INVALID", f"v{version}-{level} ECC 长度不一致")
        versions.append(version)
    return {"versions": versions, "levels": list(ECC_LEVELS)}


def _validate_version(version: int) -> None:
    if isinstance(version, bool) or not isinstance(version, int):
        raise QRError("QR_VERSION_INVALID", f"版本必须是整数，实际 {type(version).__name__}",
                      field="version")
    if not MIN_VERSION <= version <= MAX_VERSION:
        raise QRError("QR_VERSION_INVALID",
                      f"版本必须在 {MIN_VERSION}..{MAX_VERSION}，实际 {version}",
                      field="version", hint="更大的载荷请改用文件/链接，不要用二维码")


def _validate_level(level: str) -> str:
    if not isinstance(level, str):
        raise QRError("QR_ECC_INVALID", f"纠错等级必须是字符串，实际 {type(level).__name__}",
                      field="ecc")
    normalised = level.upper()
    if normalised not in ECC_FORMAT_BITS:
        raise QRError("QR_ECC_INVALID",
                      f"纠错等级必须是 {'/'.join(ECC_LEVELS)} 之一，实际 {level!r}",
                      field="ecc")
    return normalised


# ---------------------------------------------------------------------------
# 四、位流与数据码字
# ---------------------------------------------------------------------------


class _BitStream:
    """MSB-first 位流：QR 的模式/计数/数据都是「先写高位」。"""

    __slots__ = ("_buffer", "length")

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.length = 0

    def put(self, value: int, count: int) -> None:
        if count < 0 or value < 0 or value >> count:
            raise QRError("QR_INTERNAL", f"位流写入非法：value={value}, count={count}")
        for shift in range(count - 1, -1, -1):
            if self.length % 8 == 0:
                self._buffer.append(0)
            if (value >> shift) & 1:
                self._buffer[self.length // 8] |= 0x80 >> (self.length % 8)
            self.length += 1

    def put_bit(self, bit: int) -> None:
        self.put(1 if bit else 0, 1)

    def to_bytes(self) -> bytes:
        return bytes(self._buffer)


def _encode_data_codewords(payload: bytes, version: int, level: str) -> list[int]:
    """模式指示符 + 计数 + UTF-8 字节 + 终止符 + 补齐 + 填充字节 → 数据码字。"""
    capacity_bits = _data_codeword_count(version, level) * 8
    stream = _BitStream()
    stream.put(_MODE_BYTE, 4)
    stream.put(len(payload), _length_bits(version))
    for byte in payload:
        stream.put(byte, 8)
    if stream.length > capacity_bits:  # encode() 已经按容量选版本，走到这里说明表/容量算错
        raise QRError("QR_INTERNAL",
                      f"数据位 {stream.length} 超出容量 {capacity_bits}（v{version}-{level}）")
    stream.put(0, min(4, capacity_bits - stream.length))   # 终止符最多 4 bit
    while stream.length % 8:                               # 补齐到字节边界
        stream.put_bit(0)
    pad_index = 0
    while stream.length < capacity_bits:                   # 0xEC/0x11 交替填充
        stream.put(_PAD_BYTES[pad_index % 2], 8)
        pad_index += 1
    return list(stream.to_bytes())


def _interleave(data_codewords: Sequence[int], version: int, level: str) -> list[int]:
    """按块切分、算 ECC、数据列优先交织 → 完整码字序列（写进矩阵的顺序）。"""
    specs = _block_spec(version, level)
    if len(data_codewords) != sum(data for data, _ecc in specs):
        raise QRError("QR_INTERNAL",
                      f"数据码字数 {len(data_codewords)} 与分块表不符（v{version}-{level}）")
    blocks: list[list[int]] = []
    offset = 0
    for data_len, _ecc_len in specs:
        blocks.append(list(data_codewords[offset:offset + data_len]))
        offset += data_len
    ecc_blocks = [_rs_ecc(block, ecc_len) for block, (_data, ecc_len) in zip(blocks, specs)]

    interleaved: list[int] = []
    max_data = max(len(block) for block in blocks)
    for index in range(max_data):                 # 数据：按位置列优先
        for block in blocks:
            if index < len(block):
                interleaved.append(block[index])
    ecc_len = specs[0][1]
    for index in range(ecc_len):                  # ECC：同样列优先
        for ecc_block in ecc_blocks:
            interleaved.append(ecc_block[index])
    if len(interleaved) != TOTAL_CODEWORDS[version]:
        raise QRError("QR_INTERNAL", "交织后码字数与总码字表不符")
    return interleaved


# ---------------------------------------------------------------------------
# 五、矩阵布局：功能图形、格式/版本信息、之字形放置、掩码、罚分
# ---------------------------------------------------------------------------


def _bch_remainder(value: int, generator: int) -> int:
    """GF(2) 多项式取余；BCH 校验就是「余数为 0」。"""
    generator_degree = generator.bit_length() - 1
    while value and value.bit_length() - 1 >= generator_degree:
        value ^= generator << (value.bit_length() - 1 - generator_degree)
    return value


def _format_bits(level: str, mask: int) -> int:
    """15 bit 格式信息：5 bit 数据 + BCH(15,5) 余数，再 XOR ``0x5412``。"""
    data = (ECC_FORMAT_BITS[level] << 3) | mask
    shifted = data << 10
    return (shifted | _bch_remainder(shifted, _G15)) ^ _G15_MASK


def _version_bits(version: int) -> int:
    """18 bit 版本信息：6 bit 版本号 + BCH(18,6) 余数（v≥7 才写）。"""
    shifted = version << 12
    return shifted | _bch_remainder(shifted, _G18)


def _format_positions(size: int) -> list[tuple[int, int]]:
    """30 个格式信息模块位置，前 15 个在左上、后 15 个是镜像副本；下标即 bit 序号。

    与 ISO/IEC 18004 图 25 一致：bit 0 在 ``(row 0, col 8)``，bit 6 绕到 ``(7, 8)``，
    bit 7 在 ``(8, 8)``，bit 8 在 ``(8, 7)``，bit 9–14 沿第 8 行向左；第二份从右上
    行 8 向左，再沿第 8 列从上往下。
    """
    positions: list[tuple[int, int]] = []
    for index in range(6):
        positions.append((index, 8))              # bits 0-5
    positions.append((7, 8))                      # bit 6
    positions.append((8, 8))                      # bit 7
    positions.append((8, 7))                      # bit 8
    for index in range(9, 15):
        positions.append((8, 14 - index))         # bits 9-14 → (8, 5..0)
    for index in range(8):                        # 第二份 bits 0-7：行 8，自右向左
        positions.append((8, size - 1 - index))
    for index in range(8, 15):                    # 第二份 bits 8-14：列 8，自下往上
        positions.append((size - 15 + index, 8))
    return positions


def _draw_finder(modules: list[list[int]], function: list[list[bool]],
                 size: int, row: int, col: int) -> None:
    """7×7 定位图形 + 1 模块宽的浅色分隔符（越界部分自然裁掉）。"""
    for delta_row in range(-1, 8):
        for delta_col in range(-1, 8):
            target_row = row + delta_row
            target_col = col + delta_col
            if not (0 <= target_row < size and 0 <= target_col < size):
                continue
            function[target_row][target_col] = True
            inside = 0 <= delta_row <= 6 and 0 <= delta_col <= 6
            dark = False
            if inside:
                on_border = delta_row in (0, 6) or delta_col in (0, 6)
                in_center = 2 <= delta_row <= 4 and 2 <= delta_col <= 4
                dark = on_border or in_center
            modules[target_row][target_col] = 1 if dark else 0


def _draw_alignment(modules: list[list[int]], function: list[list[bool]],
                    version: int, size: int) -> None:
    """5×5 校正图形；跳过会压到三个定位图形上的中心（左上/右上/左下）。"""
    positions = ALIGNMENT_POSITIONS[version]
    for center_row in positions:
        for center_col in positions:
            if ((center_row == 6 and center_col in (6, size - 7))
                    or (center_row == size - 7 and center_col == 6)):
                continue
            for delta_row in range(-2, 3):
                for delta_col in range(-2, 3):
                    target_row = center_row + delta_row
                    target_col = center_col + delta_col
                    function[target_row][target_col] = True
                    ring = max(abs(delta_row), abs(delta_col))
                    modules[target_row][target_col] = 1 if ring in (0, 2) else 0


def _draw_timing(modules: list[list[int]], function: list[list[bool]], size: int) -> None:
    """第 6 行/列的黑白相间定时图形（两个定位图形之间的部分）。"""
    for index in range(8, size - 8):
        function[6][index] = True
        modules[6][index] = 1 if index % 2 == 0 else 0
        function[index][6] = True
        modules[index][6] = 1 if index % 2 == 0 else 0


def _build_function_patterns(version: int) -> tuple[list[list[int]], list[list[bool]]]:
    """构建只含功能图形的基矩阵，并返回 ``function`` 掩膜（数据区 = False）。

    格式信息区先占位写 0（数值稍后按掩码重写），版本信息区直接写死（与掩码无关）。
    两者的模块都标记为 function，数据放置时跳过。
    """
    size = 4 * version + 17
    modules = [[0] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]
    _draw_finder(modules, function, size, 0, 0)
    _draw_finder(modules, function, size, 0, size - 7)
    _draw_finder(modules, function, size, size - 7, 0)
    _draw_timing(modules, function, size)
    _draw_alignment(modules, function, version, size)
    for row, col in _format_positions(size):      # 格式信息双副本占位
        function[row][col] = True
        modules[row][col] = 0
    function[size - 8][8] = True                   # 固定深色模块
    modules[size - 8][8] = 1
    if version >= 7:                               # 版本信息双副本
        bits = _version_bits(version)
        for index in range(18):
            near = size - 11 + index % 3
            far = index // 3
            bit = (bits >> index) & 1
            for row, col in ((far, near), (near, far)):
                function[row][col] = True
                modules[row][col] = bit
    return modules, function


def _write_format(modules: list[list[int]], size: int, level: str, mask: int) -> None:
    """把选定掩码的 15 bit 格式信息写进两份副本，并补固定深色模块。

    ``_format_positions`` 返回 30 个位置（两份副本首尾相接），每份都按 bit 0→14
    写同一个 15 bit 串，所以下标要 ``% 15``——写错这一处，第二份会写成全零。
    """
    bits = _format_bits(level, mask)
    for index, (row, col) in enumerate(_format_positions(size)):
        modules[row][col] = (bits >> (index % 15)) & 1
    modules[size - 8][8] = 1


def _mask_hit(mask: int, row: int, col: int) -> bool:
    """8 种数据掩码条件（ISO/IEC 18004 表 10）；命中即翻转该数据模块。"""
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row * col) % 3 + (row + col) % 2) % 2 == 0


def _apply_mask(modules: list[list[int]], function: list[list[bool]], mask: int) -> None:
    size = len(modules)
    for row in range(size):
        for col in range(size):
            if not function[row][col] and _mask_hit(mask, row, col):
                modules[row][col] ^= 1


def _data_positions(size: int, function: list[list[bool]]) -> list[tuple[int, int]]:
    """标准之字形读取顺序下的全部数据模块坐标（不含 remainder bits 的处理）。"""
    positions: list[tuple[int, int]] = []
    right = size - 1
    while right >= 1:
        if right == 6:                 # 第 6 列是定时图形，整列跳过
            right = 5
        for vertical in range(size):
            upward = ((right + 1) & 2) == 0
            row = size - 1 - vertical if upward else vertical
            for offset in (0, 1):
                col = right - offset
                if not function[row][col]:
                    positions.append((row, col))
        right -= 2
    return positions


def _place_data(modules: list[list[int]], function: list[list[bool]],
                codewords: Sequence[int]) -> None:
    """把码字位按之字形放进数据模块；放不下的剩余模块（remainder bits）保持 0。"""
    bits: list[int] = []
    for byte in codewords:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    positions = _data_positions(len(modules), function)
    if len(positions) < len(bits):
        raise QRError("QR_INTERNAL", "数据模块不足以容纳全部码字位")
    for index, (row, col) in enumerate(positions):
        modules[row][col] = bits[index] if index < len(bits) else 0


def _penalty_score(modules: list[list[int]]) -> int:
    """ISO/IEC 18004 §8.8.2 的四条罚分，越低越好。"""
    size = len(modules)
    score = 0

    # 规则 1：同色连续 ≥5，罚 3 + (长度-5)
    lines: list[Iterable[int]] = list(modules) + [list(column) for column in zip(*modules)]
    for line in lines:
        values = list(line)
        run = 1
        for index in range(1, size):
            if values[index] == values[index - 1]:
                run += 1
            else:
                if run >= 5:
                    score += _N1 + (run - 5)
                run = 1
        if run >= 5:
            score += _N1 + (run - 5)

    # 规则 2：2×2 同色块，每块罚 3
    for row in range(size - 1):
        upper = modules[row]
        lower = modules[row + 1]
        for col in range(size - 1):
            value = upper[col]
            if value == upper[col + 1] == lower[col] == lower[col + 1]:
                score += _N2

    # 规则 3：1:1:3:1:1 图案（两侧各 4 浅色），每次罚 40
    for line in lines:
        values = tuple(line)
        for start in range(size - 10):
            window = values[start:start + 11]
            if window == _RULE3_PATTERNS[0] or window == _RULE3_PATTERNS[1]:
                score += _N3

    # 规则 4：深色比例偏离 50%，每 5% 罚 10（整数运算，避免浮点口径漂移）
    dark = sum(sum(row) for row in modules)
    total = size * size
    score += _N4 * (abs(dark * 100 - total * 50) // (total * 5))
    return score


def _build_matrix(codewords: Sequence[int], version: int, level: str) -> list[list[int]]:
    """选掩码并返回最终矩阵（0/1，无静区）。"""
    base, function = _build_function_patterns(version)
    _place_data(base, function, codewords)
    best_score: int | None = None
    best_matrix: list[list[int]] | None = None
    for mask in range(8):
        candidate = [row[:] for row in base]
        _apply_mask(candidate, function, mask)
        _write_format(candidate, len(candidate), level, mask)
        score = _penalty_score(candidate)
        if best_score is None or score < best_score:
            best_score, best_matrix = score, candidate
    assert best_matrix is not None
    return best_matrix


# ---------------------------------------------------------------------------
# 六、编码入口
# ---------------------------------------------------------------------------


def encode(text: str, *, ecc: str = "M", min_version: int = 1,
           max_version: int = 12) -> list[list[int]]:
    """把字符串编码成 QR 矩阵（0/1，**不含静区**）。

    版本在 ``[min_version, max_version]`` 内按 UTF-8 字节数自动选最小可容纳者；
    放不下就抛 :class:`QRError`（``QR_DATA_TOO_LONG``），绝不截断——一条被截断的
    签名记录比没有更危险。
    """
    if not isinstance(text, str):
        raise QRError("QR_TEXT_INVALID", f"text 必须是 str，实际 {type(text).__name__}",
                      field="text")
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise QRError("QR_TEXT_INVALID", "文本含孤立代理项，不是合法 UTF-8",
                      field="text") from exc
    level = _validate_level(ecc)
    for label, value in (("min_version", min_version), ("max_version", max_version)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise QRError("QR_VERSION_INVALID", f"{label} 必须是整数", field=label)
    if not MIN_VERSION <= min_version <= max_version <= MAX_VERSION:
        raise QRError("QR_VERSION_INVALID",
                      f"必须满足 {MIN_VERSION} <= min_version <= max_version <= {MAX_VERSION}，"
                      f"实际 {min_version}..{max_version}",
                      field="min_version")

    chosen: int | None = None
    for version in range(min_version, max_version + 1):
        if len(payload) <= byte_capacity(version, level):
            chosen = version
            break
    if chosen is None:
        capacity = byte_capacity(max_version, level)
        raise QRError("QR_DATA_TOO_LONG",
                      f"内容 {len(payload)} 字节，v{max_version}-{level} 最多 {capacity} 字节",
                      field="text", hint="缩短内容、提高 max_version 或降低纠错等级",
                      detail={"bytes": len(payload), "capacity": capacity,
                              "max_version": max_version, "ecc": level})

    data_codewords = _encode_data_codewords(payload, chosen, level)
    codewords = _interleave(data_codewords, chosen, level)
    return _build_matrix(codewords, chosen, level)


# ---------------------------------------------------------------------------
# 七、解码器：读格式/版本 → 反掩码 → 之字形 → 反交织 → RS 校验 → 解析
# ---------------------------------------------------------------------------


def _normalise_matrix(matrix: Any) -> list[list[int]]:
    """校验输入是「无静区的方形 0/1 矩阵」，返回一份独立的 int 副本。"""
    if not isinstance(matrix, (list, tuple)) or len(matrix) == 0:
        raise QRError("QR_MATRIX_INVALID", "矩阵必须是非空的行列表")
    size = len(matrix)
    rows: list[list[int]] = []
    for row_index, row in enumerate(matrix):
        if not isinstance(row, (list, tuple)):
            raise QRError("QR_MATRIX_INVALID", f"第 {row_index} 行不是列表")
        if len(row) != size:
            raise QRError("QR_MATRIX_INVALID",
                          f"第 {row_index} 行有 {len(row)} 个模块，应为 {size}（矩阵必须是方的）")
        values: list[int] = []
        for col_index, value in enumerate(row):
            if isinstance(value, bool):
                values.append(int(value))
            elif isinstance(value, int) and value in (0, 1):
                values.append(value)
            else:
                raise QRError("QR_MATRIX_INVALID",
                              f"模块 ({row_index}, {col_index}) 必须是 0/1，实际 {value!r}")
        rows.append(values)
    if size < 21 or (size - 17) % 4:
        raise QRError("QR_MATRIX_INVALID",
                      f"矩阵边长 {size} 不是合法的 QR 尺寸（4v+17，v≥1）")
    version = (size - 17) // 4
    if version > MAX_VERSION:
        raise QRError("QR_VERSION_UNSUPPORTED",
                      f"矩阵是 v{version}，本实现只支持 v{MIN_VERSION}–v{MAX_VERSION}",
                      hint="用 min_version/max_version 限制编码版本")
    return rows


def _read_format(modules: list[list[int]], size: int) -> tuple[str, int]:
    """读两份格式信息，BCH 校验后返回 ``(纠错等级, 掩码号)``。

    两份都坏 → 拒绝；一份好一份坏 → 用好的（真实打印件常有局部污损）；两份都好但
    不一致 → 拒绝（这说明其中一份被改成了另一个合法码字，不能猜）。
    """
    positions = _format_positions(size)
    copies = []
    for start in (0, 15):
        bits = 0
        for index in range(15):
            row, col = positions[start + index]
            bits |= modules[row][col] << index
        copies.append(bits)

    decoded: list[tuple[str, int]] = []
    for bits in copies:
        unmasked = bits ^ _G15_MASK
        if _bch_remainder(unmasked, _G15) != 0:
            continue
        data = unmasked >> 10
        level_bits = (data >> 3) & 0b11
        mask = data & 0b111
        level = _ECC_FROM_BITS.get(level_bits)
        if level is None:  # pragma: no cover - 2 bit 全覆盖，防御性写法
            continue
        decoded.append((level, mask))
    if not decoded:
        raise QRError("QR_FORMAT_INVALID", "两份格式信息都没通过 BCH(15,5) 校验",
                      hint="码面可能被污损，或不是标准 QR 码")
    if len(decoded) == 2 and decoded[0] != decoded[1]:
        raise QRError("QR_FORMAT_CONFLICT",
                      "两份格式信息都合法但互相矛盾，无法判定掩码",
                      detail={"copies": [list(item) for item in decoded]})
    return decoded[0]


def _read_version_info(modules: list[list[int]], size: int, version: int) -> None:
    """v≥7 时校验两份版本信息：至少一份通过 BCH(18,6) 且版本号与尺寸一致。"""
    for start in (0, 1):
        bits = 0
        for index in range(18):
            near = size - 11 + index % 3
            far = index // 3
            row, col = (far, near) if start == 0 else (near, far)
            bits |= modules[row][col] << index
        if _bch_remainder(bits, _G18) == 0 and (bits >> 12) == version:
            return
    raise QRError("QR_VERSION_INFO_INVALID",
                  f"v{version} 的版本信息未通过 BCH(18,6) 校验",
                  hint="码面损坏，或矩阵尺寸与版本信息不一致")


def _deinterleave(codewords: Sequence[int], version: int, level: str) -> list[int]:
    """反交织 + 逐块 RS 伴随式校验，返回按块顺序拼接的数据码字。"""
    specs = _block_spec(version, level)
    total = TOTAL_CODEWORDS[version]
    if len(codewords) != total:
        raise QRError("QR_INTERNAL",
                      f"读到 {len(codewords)} 个码字，v{version} 应有 {total} 个")
    blocks_data: list[list[int]] = [[] for _ in specs]
    blocks_ecc: list[list[int]] = [[] for _ in specs]
    position = 0
    max_data = max(data for data, _ecc in specs)
    for index in range(max_data):
        for block_index, (data_len, _ecc_len) in enumerate(specs):
            if index < data_len:
                blocks_data[block_index].append(codewords[position])
                position += 1
    ecc_len = specs[0][1]
    for index in range(ecc_len):
        for block_index in range(len(specs)):
            blocks_ecc[block_index].append(codewords[position])
            position += 1
    if position != total:
        raise QRError("QR_INTERNAL", "反交织后码字数与总码字表不符")

    for block_index, (_data_len, block_ecc_len) in enumerate(specs):
        block = blocks_data[block_index] + blocks_ecc[block_index]
        syndromes = _rs_syndromes(block, block_ecc_len)
        if any(syndromes):
            raise QRError("QR_ECC_CORRUPTED",
                          f"第 {block_index + 1} 块 Reed–Solomon 伴随式非零，码字被改动或污损",
                          detail={"block": block_index + 1,
                                  "syndromes": [int(value) for value in syndromes[:4]]})
    return [byte for block in blocks_data for byte in block]


def _parse_data(data: Sequence[int], version: int, level: str) -> str:
    """解析字节模式：模式指示符(0100) + 计数 + UTF-8 载荷；其余位（终止/填充）忽略。"""
    total_bits = len(data) * 8
    cursor = 0

    def take(count: int) -> int:
        nonlocal cursor
        if cursor + count > total_bits:
            raise QRError("QR_PAYLOAD_INVALID",
                          "载荷在字段中途结束（模式/计数/数据不完整）")
        value = 0
        for _ in range(count):
            byte = data[cursor // 8]
            value = (value << 1) | ((byte >> (7 - cursor % 8)) & 1)
            cursor += 1
        return value

    mode = take(4)
    if mode != _MODE_BYTE:
        raise QRError("QR_MODE_UNSUPPORTED",
                      f"只支持字节模式（0100），读到 {mode:04b}",
                      hint="本产品只生成字节模式；别的编码器可能用数字/汉字模式")
    count = take(_length_bits(version))
    if cursor + count * 8 > total_bits:
        raise QRError("QR_PAYLOAD_INVALID",
                      f"声明 {count} 字节，超过码字剩余容量",
                      detail={"declared": count, "available": (total_bits - cursor) // 8})
    raw = bytes(take(8) for _ in range(count))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QRError("QR_PAYLOAD_INVALID", "载荷不是合法 UTF-8",
                      hint="这不是本模块编出的字节模式文本") from exc


def decode(matrix: Any) -> str:
    """把 0/1 矩阵解码回 UTF-8 字符串；读不懂/被改坏一律 :class:`QRError`。

    校验链：格式信息 BCH → （v≥7）版本信息 BCH → 逐块 RS 伴随式全零 → 模式/计数/
    UTF-8。任何一环失败都拒绝，不返回「猜出来的文本」。
    """
    modules = _normalise_matrix(matrix)
    size = len(modules)
    version = (size - 17) // 4
    level, mask = _read_format(modules, size)
    if version >= 7:
        _read_version_info(modules, size, version)
    _base, function = _build_function_patterns(version)

    plain = [row[:] for row in modules]
    for row in range(size):
        for col in range(size):
            if not function[row][col] and _mask_hit(mask, row, col):
                plain[row][col] ^= 1

    total_bits = TOTAL_CODEWORDS[version] * 8
    positions = _data_positions(size, function)
    if len(positions) < total_bits:
        raise QRError("QR_MATRIX_INVALID", "数据模块数量不足，矩阵不完整")
    codewords = []
    for byte_index in range(TOTAL_CODEWORDS[version]):
        value = 0
        for offset in range(8):
            row, col = positions[byte_index * 8 + offset]
            value = (value << 1) | plain[row][col]
        codewords.append(value)

    data = _deinterleave(codewords, version, level)
    return _parse_data(data, version, level)


# ---------------------------------------------------------------------------
# 八、渲染：SVG / ASCII / PBM（都自己补静区，不依赖外部资源）
# ---------------------------------------------------------------------------


def _validate_render_options(matrix: Any, scale: int, border: int) -> tuple[list[list[int]], int]:
    """渲染共用的入参检查；返回规范化矩阵与边长。"""
    grid = _normalise_matrix(matrix)
    if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
        raise QRError("QR_RENDER_INVALID", f"scale 必须是正整数，实际 {scale!r}",
                      field="scale")
    if isinstance(border, bool) or not isinstance(border, int) or border < 0:
        raise QRError("QR_RENDER_INVALID", f"border 必须是非负整数，实际 {border!r}",
                      field="border")
    return grid, len(grid)


def to_svg(matrix: Any, *, scale: int = 4, border: int = 4) -> str:
    """独立 SVG 字符串：只有白色底与黑色方块，无脚本、无外链、带 ``viewBox``。"""
    grid, size = _validate_render_options(matrix, scale, border)
    side = (size + 2 * border) * scale
    rectangles: list[str] = []
    for row_index, row in enumerate(grid):
        y = (row_index + border) * scale
        for col_index, value in enumerate(row):
            if value:
                x = (col_index + border) * scale
                rectangles.append(
                    f'<rect x="{x}" y="{y}" width="{scale}" height="{scale}"/>')
    body = "".join(rectangles)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
        f'width="{side}" height="{side}" shape-rendering="crispEdges">'
        f'<rect width="{side}" height="{side}" fill="#ffffff"/>'
        f'<g fill="#000000">{body}</g>'
        "</svg>\n"
    )


def to_ascii(matrix: Any, *, border: int = 2) -> str:
    """终端友好的 ASCII 版：每个模块两个字符宽（``██`` 深 / 两个空格浅），保持正方形。"""
    grid, size = _validate_render_options(matrix, 1, border)
    lines = ["  " * (size + 2 * border)] * border
    for row in grid:
        line = "  " * border
        line += "".join("██" if value else "  " for value in row)
        line += "  " * border
        lines.append(line)
    lines.extend(["  " * (size + 2 * border)] * border)
    return "\n".join(lines) + "\n"


def to_pbm(matrix: Any, *, border: int = 4) -> bytes:
    """PBM（P4 二进制位图）：调试/打印用，1 = 黑（就是深色模块）。"""
    grid, size = _validate_render_options(matrix, 1, border)
    width = size + 2 * border
    header = f"P4\n{width} {width}\n".encode("ascii")
    body = bytearray()
    blank = b"\x00" * ((width + 7) // 8)
    for _ in range(border):
        body += blank
    for row in grid:
        bits = bytearray((width + 7) // 8)
        for col_index, value in enumerate(row):
            if value:
                position = col_index + border
                bits[position // 8] |= 0x80 >> (position % 8)
        body += bits
    for _ in range(border):
        body += blank
    return header + bytes(body)


# ---------------------------------------------------------------------------
# 九、自检
# ---------------------------------------------------------------------------


def _self_check() -> int:  # pragma: no cover - 人工自检入口
    """编码→解码若干样例，打印版本/尺寸/掩码信息，顺便复核分块表。"""
    import json

    table = _check_tables()
    samples = ["", "TraceLens", "责任认定：签名背书", "did:tracelens:agent:demo"]
    report = []
    for index, text in enumerate(samples):
        level = ECC_LEVELS[index % len(ECC_LEVELS)]
        matrix = encode(text, ecc=level)
        size = len(matrix)
        version = (size - 17) // 4
        decoded = decode(matrix)
        assert decoded == text, f"自检失败：{text!r} → {decoded!r}"
        report.append({"ecc": level, "version": version, "size": size,
                       "modules": size * size})
    print(json.dumps({"ok": True, "qr_version": QR_VERSION, "table": table,
                      "samples": report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(_self_check())
