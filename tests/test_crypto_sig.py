"""签名地基验收测试：Ed25519（RFC 8032）+ 规范 JSON + DID + Base64。

运行（离线；只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_crypto_sig.py" -v
    python tests/test_crypto_sig.py

覆盖：
  1. RFC 8032 §7.1 测试向量 TEST 1/2/3 + TEST SHA(abc)：公钥派生、签名逐字节一致、验签通过；
  2. 篡改：改消息 / 改签名（R 段与 S 段各改一位）/ 换公钥 → 一律 False；
  3. 非规范 S：``S >= L``（含 ``S+L`` 的等价变形与 ``S == L``）必须拒绝；
  4. 50 组确定性伪随机密钥/消息的签发—验签闭环；
  5. 规范 JSON：键排序、嵌套、unicode、float/int 规则（钉住与 RFC 8785 的差异）、
     拒绝 NaN/Infinity/非 str 键/bytes/set；
  6. DID 稳定性（公钥/名字）；hash_object 稳定性；Base64 与 verify_b64；
  7. 可选交叉校验：``cryptography`` 可用时，100 组随机密钥/签名双向互验并打印组数。

本文件是**测试**，允许 ``unittest.skipUnless`` 的可选导入；被测模块本身只用标准库。
"""
from __future__ import annotations

import hashlib
import math
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_explainer import crypto_sig as cs  # noqa: E402

#: RFC 8032 §7.1 的四条向量：(seed_hex, pub_hex, message, sig_hex)
RFC8032_VECTORS = [
    (
        "TEST 1（空消息）",
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        b"",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "TEST 2（单字节 0x72）",
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        b"\x72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "TEST 3（两字节 0xaf82）",
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        b"\xaf\x82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
    (
        "TEST SHA(abc)（64 字节 SHA-512 摘要）",
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        hashlib.sha512(b"abc").digest(),
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
    ),
]

#: Ed25519 群的阶（RFC 8032 §5.1）；非规范 S 测试要用。
L = 2 ** 252 + 27742317777372353535851937790883648493

try:  # 仅测试可用的可选依赖；被测模块不 import 它
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - 取决于运行环境
    _HAS_CRYPTOGRAPHY = False


class TestRFC8032Vectors(unittest.TestCase):
    """RFC 8032 §7.1 官方向量：这是「实现对不对」的硬证据。"""

    def test_vectors(self) -> None:
        for label, seed_hex, pub_hex, message, sig_hex in RFC8032_VECTORS:
            with self.subTest(vector=label):
                seed = bytes.fromhex(seed_hex)
                pub = bytes.fromhex(pub_hex)
                signature = bytes.fromhex(sig_hex)
                self.assertEqual(cs.pubkey_from_seed(seed), pub, f"{label}: 公钥派生不符")
                self.assertEqual(cs.sign(seed, message), signature, f"{label}: 签名逐字节不符")
                self.assertTrue(cs.verify(pub, message, signature), f"{label}: 验签失败")
                # 确定性：同一 seed/message 再签一次必须完全一样
                self.assertEqual(cs.sign(seed, message), signature)

    def test_vector_public_key_is_32_bytes(self) -> None:
        for _label, seed_hex, _pub_hex, _message, _sig_hex in RFC8032_VECTORS:
            self.assertEqual(len(cs.pubkey_from_seed(bytes.fromhex(seed_hex))), 32)
            self.assertEqual(len(cs.sign(bytes.fromhex(seed_hex), b"x")), 64)


class TestTamper(unittest.TestCase):
    """篡改必须验签失败——这是签名层唯一的实用价值。"""

    def setUp(self) -> None:
        self.seed = bytes.fromhex(RFC8032_VECTORS[1][1])
        self.pub = bytes.fromhex(RFC8032_VECTORS[1][2])
        self.message = b"tracelens:decision:approve:task-0001"
        self.signature = cs.sign(self.seed, self.message)

    def test_roundtrip_baseline(self) -> None:
        self.assertTrue(cs.verify(self.pub, self.message, self.signature))

    def test_flipped_message_byte_rejected(self) -> None:
        for index in (0, len(self.message) // 2, len(self.message) - 1):
            tampered = bytearray(self.message)
            tampered[index] ^= 0x01
            self.assertFalse(cs.verify(self.pub, bytes(tampered), self.signature),
                             f"消息第 {index} 字节被改却仍验签通过")

    def test_flipped_signature_byte_rejected(self) -> None:
        for index in (0, 31, 32, 63):  # R 段两位、S 段两位
            tampered = bytearray(self.signature)
            tampered[index] ^= 0x01
            self.assertFalse(cs.verify(self.pub, self.message, bytes(tampered)),
                             f"签名第 {index} 字节被改却仍验签通过")

    def test_wrong_key_rejected(self) -> None:
        other_seed, other_pub = cs.generate_keypair()
        self.assertNotEqual(other_pub, self.pub)
        self.assertFalse(cs.verify(other_pub, self.message, self.signature),
                         "换一把公钥仍验签通过")
        self.assertFalse(cs.verify(self.pub, b"another message", self.signature))

    def test_malformed_inputs_return_false(self) -> None:
        cases = [
            (b"", self.message, self.signature),          # 公钥太短
            (self.pub, self.message, self.signature[:-1]),  # 签名太短
            (self.pub, self.message, b""),
            (self.pub, "字符串不是 bytes", self.signature),  # 非 bytes 消息
            (self.pub, None, self.signature),
            (bytes(32), self.message, self.signature),    # 全零不是曲线上点
        ]
        for pub, message, signature in cases:
            self.assertFalse(cs.verify(pub, message, signature))

    def test_non_canonical_s_rejected(self) -> None:
        # 取一个合法签名，把 S 换成 S + L（数学上同余，但编码必须拒绝）
        s = int.from_bytes(self.signature[32:], "little")
        self.assertLess(s, L)
        forged = self.signature[:32] + int.to_bytes(s + L, 32, "little")
        self.assertFalse(cs.verify(self.pub, self.message, forged),
                         "S + L 的非规范签名被接受")
        # S == L 本身也非法
        at_limit = self.signature[:32] + int.to_bytes(L, 32, "little")
        self.assertFalse(cs.verify(self.pub, self.message, at_limit),
                         "S == L 的签名被接受")
        # 对照：把 S 改回原值仍然有效（证明拒绝的是编码不是巧合）
        self.assertTrue(cs.verify(self.pub, self.message, self.signature))

    def test_rejects_non_canonical_point_y(self) -> None:
        # y = p 的编码：把公钥 32 字节全部按 p 写，必须被解码拒绝（y >= p）
        raw = int.to_bytes(cs.P, 32, "little")
        self.assertFalse(cs.verify(raw, self.message, self.signature))


class TestRoundTrip(unittest.TestCase):
    """50 组确定性伪随机密钥/消息：覆盖空消息、长消息与各种比特分布。"""

    def test_fifty_deterministic_roundtrips(self) -> None:
        rng = random.Random(0x5EED_2024)  # 固定种子：失败可复现
        for index in range(50):
            seed = rng.randbytes(32)
            message = rng.randbytes(rng.choice([0, 1, 2, 7, 32, 64, 200]))
            pub = cs.pubkey_from_seed(seed)
            signature = cs.sign(seed, message)
            self.assertEqual(len(pub), 32)
            self.assertEqual(len(signature), 64)
            self.assertTrue(cs.verify(pub, message, signature), f"第 {index} 组闭环失败")
            # 同一输入必须产生同一签名（Ed25519 确定性）
            self.assertEqual(cs.sign(seed, message), signature)


class TestCanonicalJSON(unittest.TestCase):
    """规范 JSON 是哈希与签名的字节口径，必须钉死。"""

    def test_key_order_is_sorted(self) -> None:
        left = {"b": 2, "a": 1, "c": {"y": True, "x": None}}
        right = {"c": {"x": None, "y": True}, "a": 1, "b": 2}
        self.assertEqual(cs.canonical_json(left), cs.canonical_json(right))
        self.assertEqual(cs.canonical_json(left), '{"a":1,"b":2,"c":{"x":null,"y":true}}')

    def test_nested_and_unicode(self) -> None:
        value = {"任务": "责任认定", "nested": [1, {"中": "文"}, [True, False, None]]}
        text = cs.canonical_json(value)
        self.assertIn("责任认定", text)       # ensure_ascii=False：中文原样
        self.assertEqual(text, '{"nested":[1,{"中":"文"},[true,false,null]],"任务":"责任认定"}')
        self.assertEqual(cs.hash_object(value), cs.hash_object({"nested": [1, {"中": "文"}, [True, False, None]], "任务": "责任认定"}))

    def test_utf16_code_unit_key_order(self) -> None:
        # U+FFFD 码点大于 U+1F600，但 UTF-16 码元上 0xD83D < 0xFFFD；
        # RFC 8785 按 UTF-16 码元排序，所以 😀 必须在前。
        text = cs.canonical_json({"\ufffd": 1, "\U0001F600": 2})
        self.assertLess(text.index("\U0001F600"), text.index("\ufffd"))
        self.assertEqual(text, '{"\U0001F600":2,"\ufffd":1}')

    def test_float_rule_is_documented_and_stable(self) -> None:
        # 与 RFC 8785 的已知差异（模块 docstring 与 FLOAT_RULE 都有写）：
        # float 用 Python 最短往返 repr：1.0 保留 ".0"，1e20 是 "1e+20"。
        self.assertEqual(cs.FLOAT_RULE, "python-repr-shortest-roundtrip")
        self.assertEqual(cs.canonical_json({"x": 1.0}), '{"x":1.0}')
        self.assertEqual(cs.canonical_json({"x": 0.1}), '{"x":0.1}')
        self.assertEqual(cs.canonical_json({"x": 1e20}), '{"x":1e+20}')
        self.assertEqual(cs.canonical_json({"x": -0.0}), '{"x":-0.0}')
        # int 与 float 是两个不同字节口径（不会把 1 与 1.0 混为一谈）
        self.assertNotEqual(cs.canonical_json({"x": 1}), cs.canonical_json({"x": 1.0}))

    def test_rejects_nan_and_infinity(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            self.assertTrue(math.isnan(bad) if isinstance(bad, float) and bad != bad else True)
            with self.assertRaises(cs.CryptoError) as ctx:
                cs.canonical_json({"x": bad})
            self.assertEqual(ctx.exception.code, "CRYPTO_VALUE_NOT_FINITE")

    def test_rejects_non_string_keys(self) -> None:
        for bad in ({1: "a"}, {None: "a"}, {(1, 2): "a"}):
            with self.assertRaises(cs.CryptoError) as ctx:
                cs.canonical_json(bad)
            self.assertEqual(ctx.exception.code, "CRYPTO_KEY_NOT_STRING")

    def test_rejects_bytes_and_sets(self) -> None:
        for bad in (b"raw", bytearray(b"raw"), {"a"}, frozenset({"a"})):
            with self.assertRaises(cs.CryptoError) as ctx:
                cs.canonical_json({"x": bad})
            self.assertEqual(ctx.exception.code, "CRYPTO_VALUE_UNSERIALIZABLE")
        # bytes 嵌在 list 里同样拒绝
        with self.assertRaises(cs.CryptoError):
            cs.canonical_json([{"ok": 1}, b"nope"])

    def test_rejects_unsupported_objects(self) -> None:
        class Weird:
            pass

        with self.assertRaises(cs.CryptoError) as ctx:
            cs.canonical_json({"x": Weird()})
        self.assertEqual(ctx.exception.code, "CRYPTO_VALUE_UNSERIALIZABLE")

    def test_canonical_bytes_matches_utf8_encoding(self) -> None:
        value = {"z": [1, "é"], "a": True}
        self.assertEqual(cs.canonical_bytes(value), cs.canonical_json(value).encode("utf-8"))

    def test_sha256_hex_matches_hashlib(self) -> None:
        self.assertEqual(cs.sha256_hex("abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(cs.sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(len(cs.sha256_hex("")), 64)


class TestHashAndDID(unittest.TestCase):
    """DID 与对象哈希是落盘/导出后仍然必须复现的值，测的是稳定性。"""

    def test_hash_object_stability(self) -> None:
        record = {"task_id": "t-1", "decision": "approve", "actor": "alice", "n": 3}
        reordered = {"actor": "alice", "n": 3, "decision": "approve", "task_id": "t-1"}
        self.assertEqual(cs.hash_object(record), cs.hash_object(reordered))
        expected = hashlib.sha256(
            b'{"actor":"alice","decision":"approve","n":3,"task_id":"t-1"}').hexdigest()
        self.assertEqual(cs.hash_object(record), expected)
        self.assertEqual(cs.hash_object(record), cs.sha256_hex(cs.canonical_bytes(record)))

    def test_did_from_pubkey_stability_and_format(self) -> None:
        seed, pub = cs.generate_keypair()
        did = cs.did_from_pubkey(pub)
        self.assertEqual(did, cs.did_from_pubkey(pub))          # 稳定
        self.assertTrue(did.startswith("did:tracelens:agent:"))
        body = did.rsplit(":", 1)[1]
        self.assertNotIn("=", body)                              # base32 无填充
        self.assertEqual(body, body.lower())                     # 小写
        # 独立复算一遍 base32(sha256(pub)[:20])，防止实现偷偷改了截断长度
        import base64
        expected = base64.b32encode(hashlib.sha256(pub).digest()[:20]).decode().lower().rstrip("=")
        self.assertEqual(body, expected)
        self.assertNotEqual(cs.did_from_pubkey(pub, kind="user"), did)
        self.assertTrue(cs.did_from_pubkey(pub, kind="user").startswith("did:tracelens:user:"))

    def test_did_from_pubkey_rejects_bad_input(self) -> None:
        with self.assertRaises(cs.CryptoError) as ctx:
            cs.did_from_pubkey(b"\x00" * 31)
        self.assertEqual(ctx.exception.code, "CRYPTO_LENGTH_INVALID")
        with self.assertRaises(cs.CryptoError):
            cs.did_from_pubkey(b"\x00" * 32, kind="Agent")   # 大写 kind 不在口径内
        with self.assertRaises(cs.CryptoError):
            cs.did_from_pubkey(b"\x00" * 32, kind="a:b")     # 冒号会拼出歧义 DID

    def test_did_from_name_stability(self) -> None:
        did = cs.did_from_name("张三")
        self.assertEqual(did, cs.did_from_name("张三"))
        self.assertTrue(did.startswith("did:tracelens:user:"))
        self.assertNotIn("=", did.rsplit(":", 1)[1])
        self.assertNotEqual(cs.did_from_name("张三"), cs.did_from_name("李四"))
        self.assertNotEqual(cs.did_from_name("alice"), cs.did_from_name("Alice"))
        with self.assertRaises(cs.CryptoError):
            cs.did_from_name("")
        with self.assertRaises(cs.CryptoError):
            cs.did_from_name("alice", kind="")


class TestPayloadAndBase64(unittest.TestCase):
    """白名单载荷与 Base64 是秘书处 API 直接调用的两个函数。"""

    def test_signed_payload_whitelist_subset(self) -> None:
        record = {"did": "did:tracelens:agent:x", "task_id": "t-1",
                  "decision": "approve", "reason": "证据齐全", "extra": "不签"}
        payload = cs.signed_payload(record, ("did", "task_id", "decision"))
        self.assertEqual(payload, cs.canonical_bytes(
            {"did": "did:tracelens:agent:x", "task_id": "t-1", "decision": "approve"}))
        self.assertNotIn("证据齐全".encode(), payload)
        self.assertNotIn(b"extra", payload)

    def test_signed_payload_missing_keys_are_keyerror_free(self) -> None:
        record = {"did": "did:tracelens:agent:x"}
        payload = cs.signed_payload(record, ("did", "task_id", "decision"))
        self.assertEqual(payload, cs.canonical_bytes({"did": "did:tracelens:agent:x"}))
        self.assertEqual(cs.signed_payload({}, ("a", "b")), b"{}")

    def test_signed_payload_is_order_independent(self) -> None:
        record = {"a": 1, "b": 2, "c": 3}
        self.assertEqual(cs.signed_payload(record, ("a", "b", "c")),
                         cs.signed_payload(record, ("c", "a", "b")))
        with self.assertRaises(cs.CryptoError):
            cs.signed_payload(record, ("a", 1))  # 非 str 字段名

    def test_signature_over_payload_roundtrip(self) -> None:
        seed, pub = cs.generate_keypair()
        record = {"did": cs.did_from_pubkey(pub), "task_id": "t-9", "decision": "reject",
                  "ts": 1760000000, "meta": {"channel": "web"}}
        fields = ("did", "task_id", "decision", "ts")
        payload = cs.signed_payload(record, fields)
        signature = cs.sign(seed, payload)
        self.assertTrue(cs.verify(pub, payload, signature))
        tampered = dict(record, decision="approve")   # 改一个被签字段
        self.assertFalse(cs.verify(pub, cs.signed_payload(tampered, fields), signature))
        unwhitelisted = dict(record, meta={"channel": "cli"})  # 改不签的字段：仍有效
        self.assertTrue(cs.verify(pub, cs.signed_payload(unwhitelisted, fields), signature))

    def test_b64_roundtrip_and_errors(self) -> None:
        raw = bytes(range(256))
        text = cs.b64e(raw)
        self.assertEqual(cs.b64d(text), raw)
        self.assertEqual(cs.b64d(text.rstrip("=")), raw)   # 容忍缺填充
        self.assertEqual(cs.b64e(b""), "")
        with self.assertRaises(cs.CryptoError) as ctx:
            cs.b64d("不是base64")
        self.assertEqual(ctx.exception.code, "CRYPTO_BASE64_INVALID")
        with self.assertRaises(cs.CryptoError):
            cs.b64d(b"bytes-not-str")

    def test_public_key_b64_and_verify_b64(self) -> None:
        # F3（QA 0.5.0 验收登记的测试卫生缺陷）：这里测的是「公钥 base64 + verify_b64
        # 通路」，不是密钥随机性（随机性由 TestKeypair.test_generate_keypair 钉）。
        # 所以两处都固定成确定性：
        # ① 密钥用固定 seed → 签名逐轮一致（Ed25519 是确定性签名），排障可逐字复现；
        # ② 「变异签名」改成 `signature[0] ^ 0x01`。
        # 旧写法是 `signature[:-1] + b"\x00"`：把签名末字节置 0x00。Ed25519 签名的
        # 第 64 字节是标量 s 的最高字节，本来就经常等于 0x00（s < 2^248 的概率约
        # 1/16）——backend 实测单跑 40 次红 3 次（7.5%），QA 估的 1/256 是同一个根因。
        # 那一次"变异"是空操作，断言等于对着**真签名**求值 → `AssertionError: True
        # is not false` 假红。`x ^ 0x01 != x` 对任意字节结构性成立，所以异或写法
        # 不可能"改了等于没改"；下一行还显式断言变异确实生效（不许削平）。
        seed = bytes(range(1, 33))          # 固定 seed：签名逐轮一致
        pub = cs.pubkey_from_seed(seed)
        message = b"payload"
        signature = cs.sign(seed, message)
        pub_text = cs.public_key_b64(pub)
        self.assertEqual(cs.b64d(pub_text), pub)
        self.assertTrue(cs.verify_b64(pub_text, message, cs.b64e(signature)))
        flipped = bytes([signature[0] ^ 0x01]) + signature[1:]
        self.assertNotEqual(flipped, signature,
                            "变异签名与原签名相同 = 这条断言在空转（就是 F3 的假红形状）")
        self.assertFalse(cs.verify_b64(pub_text, message, cs.b64e(flipped)))
        self.assertFalse(cs.verify_b64("!!!", message, cs.b64e(signature)))
        self.assertFalse(cs.verify_b64(pub_text, message, "%%%"))

    def test_error_shape(self) -> None:
        error = cs.CryptoError("CRYPTO_DEMO", "演示", field="x", hint="改一下",
                               detail={"k": 1})
        data = error.as_dict()
        self.assertEqual(data["code"], "CRYPTO_DEMO")
        self.assertEqual(data["message"], "演示")
        self.assertEqual(data["field"], "x")
        self.assertIn("建议", error.render())
        self.assertEqual(error.reason, "演示")


class TestKeypair(unittest.TestCase):
    def test_generate_keypair(self) -> None:
        seed1, pub1 = cs.generate_keypair()
        seed2, pub2 = cs.generate_keypair()
        self.assertEqual((len(seed1), len(pub1)), (32, 32))
        self.assertNotEqual(seed1, seed2)
        self.assertNotEqual(pub1, pub2)
        self.assertEqual(cs.pubkey_from_seed(seed1), pub1)

    def test_sign_rejects_bad_seed(self) -> None:
        with self.assertRaises(cs.CryptoError) as ctx:
            cs.sign(b"\x00" * 31, b"msg")
        self.assertEqual(ctx.exception.code, "CRYPTO_LENGTH_INVALID")
        with self.assertRaises(cs.CryptoError):
            cs.sign("not-bytes", b"msg")
        with self.assertRaises(cs.CryptoError):
            cs.pubkey_from_seed(b"\x00" * 33)


@unittest.skipUnless(_HAS_CRYPTOGRAPHY, "cryptography 未安装，跳过交叉校验")
class TestCryptographyCrossCheck(unittest.TestCase):
    """可选：与 ``cryptography.hazmat``（OpenSSL 实现）双向交叉校验 100 组。"""

    def test_cross_validate_100_keys(self) -> None:
        rng = random.Random(0xC0DE_2024)
        checked = 0
        for index in range(100):
            seed = rng.randbytes(32)
            message = rng.randbytes(rng.randrange(0, 160))
            pub = cs.pubkey_from_seed(seed)
            signature = cs.sign(seed, message)

            private = Ed25519PrivateKey.from_private_bytes(seed)
            reference_pub = private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self.assertEqual(reference_pub, pub, f"第 {index} 组公钥与 OpenSSL 不符")
            # 我们用 OpenSSL 验我们的签名
            private.public_key().verify(signature, message)
            # 反过来：OpenSSL 签，我们验
            self.assertTrue(cs.verify(pub, message, private.sign(message)),
                            f"第 {index} 组 OpenSSL 签名被我方拒绝")
            checked += 1
        print(f"\n[交叉校验] {checked} 组随机密钥/签名与 cryptography.hazmat 双向一致")
        self.assertEqual(checked, 100)

    def test_cross_check_rejects_forgery(self) -> None:
        seed = rng_seed = bytes.fromhex(RFC8032_VECTORS[0][1])
        private = Ed25519PrivateKey.from_private_bytes(rng_seed)
        signature = private.sign(b"x")
        self.assertTrue(cs.verify(cs.pubkey_from_seed(seed), b"x", signature))
        with self.assertRaises(InvalidSignature):
            private.public_key().verify(signature, b"y")


if __name__ == "__main__":
    unittest.main(verbosity=2)
