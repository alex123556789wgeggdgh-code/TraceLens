"""签名与哈希地基：纯标准库 Ed25519 + 规范 JSON + DID + Base64（签名背书层 1/3）。

给谁用、为什么自己写
--------------------
秘书处（责任认定与签名背书）要落三件事：**谁签的**（DID）、**签的是哪些字段**
（:func:`signed_payload` 白名单子集）、**签名对不对**（:func:`verify`）。本模块是这三个
动作的共同底座，``audit.py`` 的哈希链口径（规范 JSON + ``sha256`` 十六进制）也被复用。

产品是「本地优先、标准库 only、无网络」的：不能引 ``cryptography``/``PyNaCl``，也不该
在用户机器上放私钥文件。所以这里放一份**可移植兜底实现**：RFC 8032 的 Ed25519，纯 Python，
标准库只用 ``hashlib/os/secrets/json/base64``。

安全边界（必须如实写在这里，而不是藏在注释里）
----------------------------------------------
1. **不是侧信道加固实现**：点乘是固定窗口的 double-and-add，分支与访存随窗口值变化，
   理论上可被同机的高精度计时/缓存观测利用。它适合本地单用户、离线取证的场景。
   生产级密钥源应把签名动作交给 **TEE / HSM / 系统钥匙串**（例如 Windows DPAPI/CNG、
   macOS Keychain、Linux TPM），本模块只做「没有硬件时也验得动」的兜底。
2. **不生成、不保存私钥文件**：:func:`generate_keypair` 返回 32 字节种子，落盘/加密是
   调用方的责任；模块里没有 ``save_key``/``load_key`` 这类入口。
3. **验签是布尔判断，不抛异常**：坏长度、坏编码、非规范 S 一律返回 ``False``；调用方
   不应靠捕获异常来判断签名有效。
4. **规范 JSON 是 RFC 8785 的子集**，差异只有两处（都写进 :func:`canonical_json`：
   float 用 Python 的最短往返表示、整数用 Python 任意精度整数），另有键排序按 UTF-16
   码元（与 RFC 8785 一致）。做跨语言互验时要认这两条差异。

为什么 Ed25519 而不是 HMAC
--------------------------
HMAC 需要双方共享同一把密钥：验签的人等于能伪造的人，责任认定就失去意义。Ed25519 是
公钥签名，私钥不出本机，公钥（DID）可以公开给审计页、导出件和第三方复核。

接入缝（给 secretary / audit / 导出件）
---------------------------------------
``seed, pub = generate_keypair()`` → 身份 ``did_from_pubkey(pub)`` →
签名 ``sign(seed, signed_payload(record, SIGN_FIELDS))`` →
落盘 ``{"did": did, "pub": public_key_b64(pub), "sig": b64e(sig)}`` →
复核 ``verify_b64(pub_b64, signed_payload(record, SIGN_FIELDS), sig_b64)``。

自检：``python -m agent_explainer.crypto_sig``（跑 RFC 8032 §7.1 TEST 1 与若干本地断言）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import secrets
from typing import Any, Iterable, Mapping

__all__ = [
    "CRYPTO_VERSION",
    "ED25519_SEED_SIZE",
    "ED25519_PUBLIC_KEY_SIZE",
    "ED25519_SIGNATURE_SIZE",
    "CryptoError",
    "generate_keypair",
    "pubkey_from_seed",
    "sign",
    "verify",
    "canonical_json",
    "canonical_bytes",
    "sha256_hex",
    "hash_object",
    "b64e",
    "b64d",
    "did_from_pubkey",
    "did_from_name",
    "signed_payload",
    "verify_b64",
    "public_key_b64",
]

#: 本模块的签名/编码口径版本。签名覆盖的字段一旦变化，必须同时进版本号，
#: 否则旧签名会被新规则误判（或更糟：错误地判为有效）。
CRYPTO_VERSION = "1.0"

ED25519_SEED_SIZE = 32
ED25519_PUBLIC_KEY_SIZE = 32
ED25519_SIGNATURE_SIZE = 64

#: DID 前缀固定成 ``did:tracelens:<kind>:<id>``：kind 是身份类别，不是自由文本，
#: 只允许小写字母开头的 ``[a-z0-9-]{0,31}``，避免拼出含 ``:`` 的歧义 DID。
DID_PREFIX = "did:tracelens:"
DID_KIND_LIMIT = 32
#: ``sha256(pub)[:20]`` 截断到 160 bit：撞库成本足够（生日界 2^80），DID 又短。
DID_HASH_BYTES = 20

# ---------------------------------------------------------------------------
# 一、错误口径（与 audit.py 的 AuditError 同构：code 给程序，reason 给人）
# ---------------------------------------------------------------------------


class CryptoError(Exception):
    """签名/编码层的统一异常。

    ``code`` 是稳定错误码（测试与前端按它分支），``reason`` 是可以直接给人看的中文
    说明；``as_dict()`` 输出与审计模块同构的字段，便于 API 层原样透传。
    """

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: Mapping[str, Any] | None = None) -> None:
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
# 二、Ed25519（RFC 8032）：曲线常数、点运算、编解码、签名与验签
# ---------------------------------------------------------------------------
# 曲线：twisted Edwards ``-x^2 + y^2 = 1 + d*x^2*y^2`` over GF(2^255-19)。
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P          # 曲线参数 d
SQRT_M1 = pow(2, (P - 1) // 4, P)                  # sqrt(-1) mod p

#: 基点 B 的 y 坐标（RFC 8032 §5.1）；x 由曲线方程恢复，取偶校验位（sign bit 0）。
_B_Y = (4 * pow(5, P - 2, P)) % P
_IDENTITY = (0, 1, 1, 0)  # 无穷远点，扩展坐标 (X, Y, Z, T)，T = X*Y/Z


def _recover_x(y: int, sign: int) -> int | None:
    """由 y 与 sign 位恢复 x；不在曲线上或非法编码返回 ``None``。

    RFC 8032 §5.1.3 的 ``decodepoint``：y 必须 ``< p``；恢复出的 x 若为 0 而 sign
    为 1，说明是「-0」这种非规范编码，同样拒绝。这里返回平台无关的 ``None``，
    让验签路径统一得到 ``False``。
    """
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = (x * SQRT_M1) % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _make_base_point() -> tuple[int, int, int, int]:
    x = _recover_x(_B_Y, 0)
    if x is None:  # pragma: no cover - 常数写错才会走到，自检会先炸
        raise CryptoError("CRYPTO_INTERNAL", "Ed25519 基点不在曲线上")
    return (x, _B_Y, 1, x * _B_Y % P)


_BASE = _make_base_point()


def _pt_add(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """扩展坐标点加（EFD ``add-2008-hwcd-3``，a = -1 的完整公式）。

    用扩展坐标而不是仿射坐标，是为了**避免每次点加都做模逆**：模逆要走 255 次
    ``pow``，会把一次签名从毫秒级拖到秒级。代价是每步 9 次模乘，仍然划算。
    """
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    pa = (y1 - x1) * (y2 - x2) % P
    pb = (y1 + x1) * (y2 + x2) % P
    pc = t1 * 2 * D * t2 % P
    pd = z1 * 2 * z2 % P
    pe = pb - pa
    pf = pd - pc
    pg = pd + pc
    ph = pb + pa
    return (pe * pf % P, pg * ph % P, pf * pg % P, pe * ph % P)


def _pt_double(a: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """扩展坐标点倍（EFD ``dbl-2008-hwcd``），a = -1。"""
    x1, y1, z1, _t1 = a
    pa = x1 * x1 % P
    pb = y1 * y1 % P
    pc = 2 * z1 * z1 % P
    pe = ((x1 + y1) * (x1 + y1) - pa - pb) % P   # E = (X+Y)^2 - A - B
    pg = (pb - pa) % P                            # G = -A + B（a = -1）
    pf = (pg - pc) % P                            # F = G - C
    ph = (-pa - pb) % P                           # H = -A - B
    return (pe * pf % P, pg * ph % P, pf * pg % P, pe * ph % P)


def _pt_neg(a: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, z, t = a
    return ((-x) % P, y, z, (-t) % P)


def _encode_point(pt: tuple[int, int, int, int]) -> bytes:
    """点编码：``y`` 小端 32 字节，最高位放 ``x`` 的奇偶（RFC 8032 §5.1.2）。"""
    x, y, z, _t = pt
    zinv = pow(z, P - 2, P)
    x = x * zinv % P
    y = y * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode_point(raw: bytes) -> tuple[int, int, int, int] | None:
    """点解码；非法编码返回 ``None``（严格拒绝非规范 y 与「x=0 但 sign=1」）。"""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    raw = bytes(raw)
    if len(raw) != ED25519_PUBLIC_KEY_SIZE:
        return None
    value = int.from_bytes(raw, "little")
    sign = (value >> 255) & 1
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


_BASE_TABLE: list[tuple[int, int, int, int]] | None = None


def _base_table() -> list[tuple[int, int, int, int]]:
    """``[0B, 1B, ..., 255B]`` 的 8 bit 窗口表；首次用到时算一次（约 255 次点加）。

    表只依赖曲线常数，进程内全局复用；它是公开数据，不涉及私钥，缓存没有泄密面。
    """
    global _BASE_TABLE
    if _BASE_TABLE is None:
        table: list[tuple[int, int, int, int]] = [_IDENTITY]
        acc = _IDENTITY
        for _ in range(1, 256):
            acc = _pt_add(acc, _BASE)
            table.append(acc)
        _BASE_TABLE = table
    return _BASE_TABLE


def _scalar_mult_base(scalar: int) -> tuple[int, int, int, int]:
    """``scalar * B``：8 bit 固定窗口（32 窗），首窗直接取表。"""
    table = _base_table()
    raw = int.to_bytes(scalar % (1 << 256), 32, "little")
    acc = table[raw[31]]
    for index in range(30, -1, -1):
        for _ in range(8):
            acc = _pt_double(acc)
        acc = _pt_add(acc, table[raw[index]])
    return acc


def _scalar_mult_point(point: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    """任意点 ``scalar * point``：4 bit 窗口（16 项表，64 窗），比逐位加少约 1/5 点加。"""
    scalar = scalar % (1 << 256)
    table: list[tuple[int, int, int, int]] = [_IDENTITY]
    acc = _IDENTITY
    for _ in range(15):
        acc = _pt_add(acc, point)
        table.append(acc)
    nibbles = [(scalar >> (4 * i)) & 0xF for i in range(64)]
    acc = table[nibbles[63]]
    for index in range(62, -1, -1):
        acc = _pt_double(_pt_double(_pt_double(_pt_double(acc))))
        value = nibbles[index]
        if value:
            acc = _pt_add(acc, table[value])
    return acc


def _clamp(scalar32: bytes) -> int:
    """RFC 8032 §5.1.5：清低 3 位、清最高位、置次高位。"""
    a = int.from_bytes(scalar32, "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a


def _coerce_bytes(value: Any, *, field: str, expected: int | None = None) -> bytes:
    """把 bytes/bytearray/memoryview 收敛成 bytes；类型或长度不对抛 :class:`CryptoError`。"""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise CryptoError("CRYPTO_TYPE_INVALID",
                          f"{field} 必须是 bytes，实际是 {type(value).__name__}",
                          field=field, hint="str 请先 .encode('utf-8')")
    raw = bytes(value)
    if expected is not None and len(raw) != expected:
        raise CryptoError("CRYPTO_LENGTH_INVALID",
                          f"{field} 长度必须是 {expected} 字节，实际 {len(raw)}",
                          field=field)
    return raw


def generate_keypair() -> tuple[bytes, bytes]:
    """生成一对 ``(seed32, pub32)``。

    seed 就是 RFC 8032 的私钥种子（32 字节随机数，私钥展开由 :func:`sign` 内部完成）。
    用 ``secrets`` 而不是 ``random``：后者是可预测的伪随机，不能碰密钥材料。
    """
    seed = secrets.token_bytes(ED25519_SEED_SIZE)
    return seed, pubkey_from_seed(seed)


def pubkey_from_seed(seed: bytes) -> bytes:
    """由种子算公钥：``A = clamp(SHA-512(seed)[:32]) * B``。"""
    seed = _coerce_bytes(seed, field="seed", expected=ED25519_SEED_SIZE)
    digest = hashlib.sha512(seed).digest()
    return _encode_point(_scalar_mult_base(_clamp(digest[:32])))


def sign(seed: bytes, message: bytes) -> bytes:
    """对任意字节串签 64 字节 Ed25519 签名（RFC 8032 §5.1.6）。

    返回 ``R(32) || S(32)``，S 小端；签名确定性（同一 seed+message 永远同一签名），
    所以测试与审计可以按字节比对，不需要 nonce 管理。
    """
    seed = _coerce_bytes(seed, field="seed", expected=ED25519_SEED_SIZE)
    message = _coerce_bytes(message, field="message")
    digest = hashlib.sha512(seed).digest()
    a = _clamp(digest[:32])
    prefix = digest[32:]
    public = _encode_point(_scalar_mult_base(a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    encoded_r = _encode_point(_scalar_mult_base(r))
    k = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % L
    s = (r + k * a) % L
    return encoded_r + int.to_bytes(s, 32, "little")


def verify(pub: bytes, message: bytes, signature: bytes) -> bool:
    """严格按 RFC 8032 §5.1.7 验签；任何坏输入返回 ``False``，不抛异常。

    「严格」在这里具体指：
    * 长度必须是 32/64，否则 False；
    * A、R 必须能按 §5.1.3 解码（y < p，且不存在「x=0 而 sign=1」的非规范点）；
    * S 必须是规范标量（``0 <= S < L``）——这一条挡掉「S+L 的等价变形签名」；
    * 解得 ``[S]B == R + [k]A``（cofactorless，与 RFC 8032 一致）。

    注意：本实现**不拒绝**小阶公钥/小阶 R（RFC 8032 未要求，且被签消息仍受保护）；
    如果上层要求「拒绝弱公钥」，应在注册 DID 时用 :func:`did_from_pubkey` 之后再加
    白名单校验，而不是在这里悄悄改变验签语义。
    """
    if not isinstance(pub, (bytes, bytearray, memoryview)) or len(bytes(pub)) != ED25519_PUBLIC_KEY_SIZE:
        return False
    if not isinstance(signature, (bytes, bytearray, memoryview)) or len(bytes(signature)) != ED25519_SIGNATURE_SIZE:
        return False
    if not isinstance(message, (bytes, bytearray, memoryview)):
        return False
    pub = bytes(pub)
    signature = bytes(signature)
    message = bytes(message)

    point_a = _decode_point(pub)
    if point_a is None:
        return False
    encoded_r = signature[:32]
    point_r = _decode_point(encoded_r)
    if point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(hashlib.sha512(encoded_r + pub + message).digest(), "little") % L
    left = _encode_point(_scalar_mult_base(s))
    right = _encode_point(_pt_add(point_r, _scalar_mult_point(point_a, k)))
    return left == right


# ---------------------------------------------------------------------------
# 三、规范 JSON / 哈希 / Base64（审计哈希链与签名载荷共用同一套字节）
# ---------------------------------------------------------------------------

#: float 的序列化规则：Python ``repr``（最短可往返十进制），**不做** ECMAScript 的
#: ``1e20 -> 100000000000000000000`` 归一化，也不把 ``1.0`` 压成 ``1``。这条差异
#: 必须让跨语言验签方知道，所以写成模块级常量，测试里也钉住。
FLOAT_RULE = "python-repr-shortest-roundtrip"

_JSON_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _utf16_sort_key(text: str) -> bytes:
    """RFC 8785 的键排序：按 UTF-16 码元比较（Python 默认按码点，星面字符会不同序）。"""
    try:
        return text.encode("utf-16-be")
    except UnicodeEncodeError as exc:
        raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                          "字符串含无法编码的代理项，不能进入规范 JSON",
                          hint="先修掉孤立的 \\ud800-\\udfff 代理字符") from exc


def _json_string(text: str) -> str:
    """JSON 字符串转义：``ensure_ascii=False``，控制字符走短转义或 ``\\u00XX``。"""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                          "字符串不是合法 UTF-8（含孤立代理项）",
                          hint="规范 JSON 要求可编码的 UTF-8 文本") from exc
    pieces: list[str] = ['"']
    for char in text:
        escaped = _JSON_ESCAPES.get(char)
        if escaped is not None:
            pieces.append(escaped)
        elif char < " ":
            pieces.append(f"\\u{ord(char):04x}")
        else:
            pieces.append(char)
    pieces.append('"')
    return "".join(pieces)


def _canonical(value: Any, *, path: str) -> str:
    """递归产出规范 JSON 文本；同时在出错时给出 ``path``（哪个字段坏了）。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, int):  # bool 已在前面拦掉，不会落到这里
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CryptoError("CRYPTO_VALUE_NOT_FINITE",
                              f"{path} 是 NaN/Infinity，规范 JSON 不允许",
                              field=path)
        return repr(value)
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise CryptoError("CRYPTO_KEY_NOT_STRING",
                                  f"{path} 的字典键必须是 str，实际 {type(key).__name__}",
                                  field=path, hint="JSON 对象的键只能是字符串")
        items = sorted(((key, value[key]) for key in value), key=lambda kv: _utf16_sort_key(kv[0]))
        inner = ",".join(f"{_json_string(key)}:{_canonical(item, path=f'{path}.{key}')}"
                         for key, item in items)
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        inner = ",".join(_canonical(item, path=f"{path}[{index}]")
                         for index, item in enumerate(value))
        return "[" + inner + "]"
    # 明确写出这些类型，错误信息才能直接告诉调用方该换成什么
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                          f"{path} 是二进制，规范 JSON 不接受",
                          field=path, hint="先 b64e()/sha256_hex() 再入载荷")
    if isinstance(value, (set, frozenset)):
        raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                          f"{path} 是集合，规范 JSON 不接受（顺序不稳定）",
                          field=path, hint="排序后转 list 再入载荷")
    raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                      f"{path} 的类型 {type(value).__name__} 不能进规范 JSON",
                      field=path, hint="只允许 None/bool/int/float/str/list/tuple/dict")


def canonical_json(obj: Any) -> str:
    """RFC 8785 子集：键排序、紧凑分隔符、``ensure_ascii=False``、UTF-8 可编码。

    与 RFC 8785 的**已知差异**（跨语言互验时必须知道，测试里也钉住）：
    * float 用 :data:`FLOAT_RULE`（Python 最短往返 ``repr``）：``1.0`` 序列化成
      ``1.0`` 而不是 ``1``，``1e20`` 是 ``1e+20`` 而不是 ``100000000000000000000``；
    * int 走 Python 任意精度整数，不限制在 IEEE-754 安全整数内；
    * 键排序已按 UTF-16 码元（与 RFC 8785 一致）。

    拒绝：NaN/Infinity、非 str 的字典键、bytes、set/frozenset、以及任何无法 UTF-8
    编码的字符串。拒绝而不是「尽力序列化」，是因为签名/哈希口径一旦静默漂移，
    复核就会得出「签名无效」而不知道为什么。
    """
    return _canonical(obj, path="$")


def canonical_bytes(obj: Any) -> bytes:
    """规范 JSON 的 UTF-8 字节串：签名与哈希真正吃的就是这段字节。"""
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: str | bytes | bytearray | memoryview) -> str:
    """``sha256`` 小写十六进制；str 按 UTF-8 编码（与审计链的 ``sha256_ref`` 同口径）。"""
    if isinstance(data, str):
        try:
            raw = data.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CryptoError("CRYPTO_VALUE_UNSERIALIZABLE",
                              "字符串不是合法 UTF-8，无法计算哈希") from exc
    else:
        raw = _coerce_bytes(data, field="data")
    return hashlib.sha256(raw).hexdigest()


def hash_object(obj: Any) -> str:
    """对象哈希：``sha256_hex(canonical_bytes(obj))``，与字段顺序/空白无关。"""
    return sha256_hex(canonical_bytes(obj))


def b64e(raw: bytes) -> str:
    """标准 Base64（带 ``=`` 填充）；公钥/签名落 JSON 时用它。"""
    return base64.b64encode(_coerce_bytes(raw, field="raw")).decode("ascii")


def b64d(text: str) -> bytes:
    """标准 Base64 解码；容忍缺失的 ``=`` 填充，其它非法字符一律 :class:`CryptoError`。

    之所以容忍缺填充：DID 文档、URL 查询串、手工粘贴时常被去掉尾部 ``=``；
    但**不**容忍 URL-safe 的 ``-``/``_``（那会让同一串字符有两种含义）。
    """
    if not isinstance(text, str):
        raise CryptoError("CRYPTO_TYPE_INVALID",
                          f"b64d 需要 str，实际是 {type(text).__name__}",
                          field="text")
    cleaned = text.strip()
    if len(cleaned) % 4:
        cleaned += "=" * (4 - len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise CryptoError("CRYPTO_BASE64_INVALID",
                          "不是合法的标准 Base64 文本",
                          field="text", hint="确认没有 URL-safe 字符或换行") from exc


# ---------------------------------------------------------------------------
# 四、DID 与签名载荷
# ---------------------------------------------------------------------------


def _normalize_kind(kind: str) -> str:
    """校验 DID 的 kind 段：小写字母开头，``[a-z0-9-]``，最长 32。"""
    if not isinstance(kind, str) or not kind:
        raise CryptoError("CRYPTO_DID_KIND_INVALID", "DID 的 kind 必须是非空字符串",
                          field="kind")
    if len(kind) > DID_KIND_LIMIT:
        raise CryptoError("CRYPTO_DID_KIND_INVALID",
                          f"DID 的 kind 最长 {DID_KIND_LIMIT} 个字符，实际 {len(kind)}",
                          field="kind")
    if not ("a" <= kind[0] <= "z") or any(not ("a" <= ch <= "z" or "0" <= ch <= "9" or ch == "-")
                                          for ch in kind):
        raise CryptoError("CRYPTO_DID_KIND_INVALID",
                          "DID 的 kind 只能是小写字母开头的 [a-z0-9-]",
                          field="kind", hint="例如 agent / user / task")
    return kind


def _did_digest(raw: bytes) -> str:
    """``base32(sha256(raw)[:20])``，小写、无填充（DID 里 ``=`` 会被转义）。"""
    digest = hashlib.sha256(raw).digest()[:DID_HASH_BYTES]
    return base64.b32encode(digest).decode("ascii").lower().rstrip("=")


def did_from_pubkey(pub: bytes, *, kind: str = "agent") -> str:
    """公钥 → ``did:tracelens:<kind>:<base32(sha256(pub)[:20])>``。

    用公钥而不是名字派生 DID：名字可以改、可以重名，公钥不能。截断到 160 bit 是
    DID 长度与撞库成本的折中；需要完整绑定时，DID 文档里另存 ``public_key_b64``。
    """
    pub = _coerce_bytes(pub, field="pub", expected=ED25519_PUBLIC_KEY_SIZE)
    return DID_PREFIX + _normalize_kind(kind) + ":" + _did_digest(pub)


def did_from_name(name: str, *, kind: str = "user") -> str:
    """名字 → ``did:tracelens:<kind>:<base32(sha256(name)[:20])>``。

    这是「人」的身份：本地用户没有密钥对时，至少能有一个稳定、不可读的标识。
    名字按 UTF-8 编码后哈希，大小写与空白不做归一化（调用方先 ``strip()``）。
    """
    if not isinstance(name, str) or not name:
        raise CryptoError("CRYPTO_DID_NAME_INVALID", "名字必须是非空字符串", field="name")
    try:
        raw = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CryptoError("CRYPTO_DID_NAME_INVALID", "名字不是合法 UTF-8") from exc
    return DID_PREFIX + _normalize_kind(kind) + ":" + _did_digest(raw)


def signed_payload(fields: Mapping[str, Any], sign_fields: Iterable[str]) -> bytes:
    """签名载荷：只把 ``sign_fields`` 白名单里**存在**的字段拼成规范 JSON 字节。

    设计要点：
    * **白名单**：不签整个记录。记录会增长（附加证据、重试信息），全量签会让旧签名
      永久失效；只签责任认定真正依赖的字段（谁、什么决定、针对哪条、何时）。
    * **缺字段不报错**（``KeyError-free``）：历史记录可能没有新字段。但「字段缺失」
      与「字段为空」在语义上不同——调用方若在意「该字段必须存在」，应显式传
      ``None``/空串，而不是依赖本函数抛错。
    * **顺序无关**：规范 JSON 会排序键，所以 ``sign_fields`` 的顺序不影响结果。
    * 出现非 str 的字段名直接 :class:`CryptoError`，避免拼出奇怪的载荷。
    """
    if not isinstance(fields, Mapping):
        raise CryptoError("CRYPTO_TYPE_INVALID",
                          f"fields 需要 Mapping，实际是 {type(fields).__name__}",
                          field="fields")
    selected: list[str] = []
    for name in sign_fields:
        if not isinstance(name, str):
            raise CryptoError("CRYPTO_TYPE_INVALID",
                              "sign_fields 里只能有字符串字段名",
                              field="sign_fields")
        selected.append(name)
    subset = {name: fields[name] for name in selected if name in fields}
    return canonical_bytes(subset)


def verify_b64(pub_b64: str, message: bytes, sig_b64: str) -> bool:
    """Base64 口径的验签：解码失败 → ``False``（不抛异常，见 :func:`verify`）。"""
    try:
        pub = b64d(pub_b64)
        signature = b64d(sig_b64)
    except CryptoError:
        return False
    return verify(pub, message, signature)


def public_key_b64(pub: bytes) -> str:
    """公钥的 Base64 文本；DID 文档与导出件里与 DID 并列存放。"""
    return b64e(_coerce_bytes(pub, field="pub", expected=ED25519_PUBLIC_KEY_SIZE))


# ---------------------------------------------------------------------------
# 五、自检
# ---------------------------------------------------------------------------


def _self_check() -> int:  # pragma: no cover - 人工自检入口
    """跑 RFC 8032 §7.1 TEST 1 + 本地闭环，打印一行 JSON 摘要。"""
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    expected = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    signature = sign(seed, b"")
    assert pubkey_from_seed(seed) == pub, "pubkey_from_seed 与 RFC 8032 TEST 1 不符"
    assert signature == expected, "sign 与 RFC 8032 TEST 1 不符"
    assert verify(pub, b"", signature) is True, "verify 拒绝了自己的签名"
    assert verify(pub, b"\x00", signature) is False, "verify 接受了被篡改的消息"
    assert verify(pub, b"", expected[:-1] + bytes([expected[-1] ^ 1])) is False, "verify 接受了坏签名"
    fresh_seed, fresh_pub = generate_keypair()
    demo = {"did": did_from_pubkey(fresh_pub), "task": "t-demo", "decision": "approve"}
    demo_sig = sign(fresh_seed, signed_payload(demo, ("did", "task", "decision")))
    assert verify(fresh_pub, signed_payload(demo, ("did", "task", "decision")), demo_sig)
    print(json.dumps({
        "ok": True,
        "crypto_version": CRYPTO_VERSION,
        "rfc8032_test1": True,
        "did": did_from_pubkey(fresh_pub),
        "hash": hash_object(demo),
        "float_rule": FLOAT_RULE,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(_self_check())
