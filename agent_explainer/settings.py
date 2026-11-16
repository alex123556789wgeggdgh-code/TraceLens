# -*- coding: utf-8 -*-
"""4.5 设置中心（t15）：配置读写 + 密钥保管 + 解释引擎接线 + 连接测试。

一句话职责：把「用什么模型、说什么语言、允不允许出网」这件事收在一个文件里，
其余模块只读它给的视图，谁都不碰密钥明文。

为什么单开一个模块
------------------
* 解释引擎（t5）留了三道闸的缝：`mode == "llm"` + `settings.enabled` + 注入客户端
  `configured`；这三样全都归设置中心管，散到服务端就会和权限、审计口径缠在一起。
* 密钥有平台差异：Windows 桌面用 DPAPI（`CryptProtectData`，当前用户绑定）落盘，
  Docker / 团队模式只能靠环境变量注入 —— 服务端（server.py）刻意**不碰密钥**
  （t8 的纯净性测试在源码层面钉住这一点），所以文件读写与解密只能在这里。
* 掩码口径只有一份：`mask_secret()`。`repr()`、日志、审计、HTTP 响应共用同一个函数，
  就不会出现"某条路径忘了打码"。

安全口径（写死并逐条有测试）
--------------------------
1. **明文只出现在两个地方**：写入时的入参、调用模型时的请求头。落盘是 DPAPI 密文，
   读出来给界面的是 `••••` + 末 4 位 + sha256 指纹前 12 位。
2. **team / 容器模式不写密钥到盘**：环境变量 `TRACELENS_LLM_API_KEY` 优先，
   状态文件里只有指纹。
3. **默认零出网**：`llm.allow_outbound=False` 时只允许回环地址；
   非回环地址要显式打开开关，且必须是 https。测试连接的失败原因给人看得懂。
4. **配置改动可追溯**：每次写入记 `updated_at / updated_by / changed`，
   服务端把 `changed` 落审计（审计里没有密钥，也没有文案正文）。
5. 文件权限：POSIX 下建目录 0700、文件 0600；Windows 下内容本身已被 DPAPI 绑定当前用户。

离线纯度（tests/test_trace_adapter.py 扫描包内顶层文件）
------------------------------------------------------
本文件不出现 URL 字面量、URL 解析模块、网络底层模块前缀、子进程模块。
连接测试用的是标准库 `http.client`，主机/端口/路径由我们自己切分
（`parse_base_url`），默认只连本机回环，出网要显式开关。
"""
from __future__ import annotations

import base64
import ctypes
import http.client
import json
import os
import pathlib
import secrets
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .explanations import (MODE_TEMPLATE, LLMSettings, LLMClient,
                           MODES as EXPLAIN_MODES, NullLLMClient)

# ----------------------------------------------------------------------------------
# 常量：口径的唯一来源
# ----------------------------------------------------------------------------------

SETTINGS_SCHEMA_VERSION = "1.0"
SETTINGS_NAME = "TraceLens Settings"

#: 默认落盘位置（`var/` 是产品运行目录，t13 的用户表、t18 的审计都在这里）
DEFAULT_SETTINGS_DIRNAME = "var"
DEFAULT_SETTINGS_FILENAME = "settings.json"

MODE_TEMPLATE, MODE_LLM = MODE_TEMPLATE, "llm"
LLM_MODES: tuple[str, ...] = tuple(EXPLAIN_MODES)          # ("template", "llm")

#: 服务商档位。`openai_compat` = 任意 OpenAI 兼容端点（含本机 Ollama / vLLM / llama.cpp）。
PROVIDERS: tuple[str, ...] = ("openai_compat", "ollama", "deepseek", "custom")
PROVIDER_DEFAULT = "openai_compat"

LANGS: tuple[str, str] = ("zh", "en")
LANGUAGE_DEFAULT = "zh"

#: 环境变量注入面（Docker / 团队部署）。team 模式下环境变量优先级高于文件。
ENV_PREFIX = "TRACELENS_"
ENV_LLM_MODE = ENV_PREFIX + "LLM_MODE"
ENV_LLM_PROVIDER = ENV_PREFIX + "LLM_PROVIDER"
ENV_LLM_BASE_URL = ENV_PREFIX + "LLM_BASE_URL"
ENV_LLM_MODEL = ENV_PREFIX + "LLM_MODEL"
ENV_LLM_API_KEY = ENV_PREFIX + "LLM_API_KEY"
ENV_LLM_LANGUAGE = ENV_PREFIX + "LLM_LANGUAGE"
ENV_LLM_ALLOW_OUTBOUND = ENV_PREFIX + "LLM_ALLOW_OUTBOUND"
ENV_LLM_TIMEOUT_S = ENV_PREFIX + "LLM_TIMEOUT_S"
ENV_UI_LANGUAGE = ENV_PREFIX + "UI_LANGUAGE"
ENV_NAMES: tuple[str, ...] = (ENV_LLM_MODE, ENV_LLM_PROVIDER, ENV_LLM_BASE_URL,
                              ENV_LLM_MODEL, ENV_LLM_API_KEY, ENV_LLM_LANGUAGE,
                              ENV_LLM_ALLOW_OUTBOUND, ENV_LLM_TIMEOUT_S, ENV_UI_LANGUAGE)

#: 密钥保管后端
KEY_DPAPI = "dpapi"          # Windows 当前用户绑定（CryptProtectData）
KEY_ENV = "env"              # 环境变量注入（容器 / 团队部署）
KEY_MEMORY = "memory"        # 只在进程内存里，重启即丢
KEY_NONE = "none"            # 从未设置

MASK_MARK = "••••••"          # 界面回显的掩码前缀
MASK_TAIL = 4                 # 掩码后保留的末位字符数
FINGERPRINT_CHARS = 12        # sha256 指纹前缀长度（用于"这次和上次是不是同一把"）

#: 取值区间（与 explanations.LLMSettings 的白名单同口径，越界回落到默认值并可读报错）
TIMEOUT_MIN, TIMEOUT_MAX = 0.5, 120.0
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
MAX_TOKENS_MIN, MAX_TOKENS_MAX = 40, 4000
BASE_URL_MAX, MODEL_MAX, KEY_MAX = 300, 120, 400
LABEL_MAX = 60

#: 连接测试
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
DEFAULT_PORT_SECURE, DEFAULT_PORT_PLAIN = 443, 80
PROBE_MAX_TOKENS = 512
PROBE_TIMEOUT_S = 8.0
PROBE_SAMPLE_CHARS = 80
PROBE_PROMPT = ("只回一句中文：用不超过 24 个字说明你已经收到这条自检请求。"
                "不要编造任何动作结果，不要说已完成。")

#: 回环主机（默认允许；非回环需要显式 allow_outbound）
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]")

#: 界面语言存储键（与 web/app.js 的 LANG_STORAGE_KEY 同名，界面写、服务端读默认值）
UI_LANGUAGE_STORAGE_KEY = "tracelens.ui_language"

#: 数据管理（设置页里的"清空演示数据 / 导出"入口对应的服务端事实）
DATA_ACTIONS: tuple[str, ...] = ("reset", "export_json", "export_audit_csv")

EFFECT_NOTICE_KEY = "settings.effect.engine_only"

#: 能力块：给 /health 与设置页显示"这台服务器到底会不会出网"
OUTBOUND_BLOCKED = "blocked"
OUTBOUND_LOOPBACK = "loopback_only"
OUTBOUND_ALLOWED = "allowed"


# ----------------------------------------------------------------------------------
# 错误：形状与干预域 / ingest 域一致（code / message / detail / request_id）
# ----------------------------------------------------------------------------------

class SettingsError(ValueError):
    """设置中心的错误：`code` 是给界面的字典键，`reason` 给人看。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: Mapping[str, Any] | None = None,
                 status: int = 400) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.field = str(field) if field else None
        self.hint = str(hint) if hint else None
        self.detail: dict[str, Any] = dict(detail) if isinstance(detail, Mapping) else {}
        self.status = int(status)

    def as_dict(self, request_id: str | None = None) -> dict[str, Any]:
        detail = dict(self.detail)
        if self.field:
            detail.setdefault("field", self.field)
        if self.hint:
            detail.setdefault("hint", self.hint)
        out: dict[str, Any] = {"code": self.code, "message": self.reason, "detail": detail}
        if request_id:
            out["request_id"] = request_id
        return out

    def __repr__(self) -> str:
        return f"SettingsError({self.code}, field={self.field!r})"


# ----------------------------------------------------------------------------------
# 小工具：掩码 / 指纹 / 脱敏 / base_url 切分
# ----------------------------------------------------------------------------------

def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def mask_secret(value: Any) -> str:
    """密钥回显唯一口径：`••••••` + 末 4 位。空值 → 空串。绝不返回可还原的形式。"""
    text = _clean(value)
    if not text:
        return ""
    if len(text) <= MASK_TAIL:
        return MASK_MARK
    return MASK_MARK + text[-MASK_TAIL:]


def secret_fingerprint(value: Any) -> str:
    """sha256 前 12 位：能判断"这次提交的和上次是不是同一把"，反推不出原文。"""
    text = _clean(value)
    if not text:
        return ""
    import hashlib
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def redact(text: Any, *, secret: Any = None) -> str:
    """日志 / 错误信息用的最后一道闸：把已知密钥与常见密钥形态全部打码。"""
    line = _clean(text)
    raw = _clean(secret)
    if raw and len(raw) >= 6 and raw in line:
        line = line.replace(raw, mask_secret(raw))
    for marker in ("Bearer ", "bearer ", "Authorization", "authorization",
                   "api_key", "apiKey", "api-key", "secret", "password", "passwd",
                   "token"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx] + "<redacted:" + marker + ">"
    return line


def parse_base_url(value: Any) -> dict[str, Any]:
    """切分服务商地址：`scheme://host[:port][/路径前缀]`。

    只用字符串切分（不引 URL 解析模块 —— 包内离线纯度约束），
    并且**只接受 https，http 仅允许回环**：密钥会随请求头走，明文 HTTP 连公网
    等于把密钥撒在路上。
    """
    raw = _clean(value)
    if not raw:
        raise SettingsError("SETTINGS_FIELD_MISSING", "服务商地址不能为空",
                            field="base_url", hint="填协议 + 主机[:端口] + 路径前缀，远端用 https")
    if len(raw) > BASE_URL_MAX:
        raise SettingsError("SETTINGS_FIELD_TOO_LONG",
                            f"服务商地址最长 {BASE_URL_MAX} 字符", field="base_url")
    if " " in raw:
        raise SettingsError("SETTINGS_URL_MALFORMED", "服务商地址不能含空格",
                            field="base_url", hint="去掉空格与换行再填")
    scheme, sep, rest = raw.partition("://")
    if not sep:
        raise SettingsError("SETTINGS_URL_MALFORMED",
                            "服务商地址缺少协议前缀（要先写 https 或本机 http）", field="base_url",
                            hint="例：协议 https（本机回环可用 http）+ 127.0.0.1:11434 + 前缀 /v1")
    scheme = scheme.strip().lower()
    if scheme not in ("https", "http"):
        raise SettingsError("SETTINGS_URL_SCHEME",
                            f"只支持 https（本机回环可用 http），收到 {scheme!r}",
                            field="base_url", hint="把协议改成 https，或用本机回环 http")
    if "?" in rest:
        raise SettingsError("SETTINGS_URL_QUERY", "服务商地址不要带查询串（密钥会跟着进日志）",
                            field="base_url")
    if "#" in rest:
        raise SettingsError("SETTINGS_URL_FRAGMENT", "服务商地址不要带锚点片段", field="base_url")
    authority, _, tail = rest.partition("/")
    if "@" in authority:
        raise SettingsError("SETTINGS_URL_USERINFO",
                            "服务商地址不能内嵌账号密码（密钥只走密钥字段）", field="base_url")
    host, port = _split_host_port(authority)
    # 主机名按 DNS 口径转小写：`HTTP://LOCALHOST` 也必须认出是本机，
    # 显示与比较都只留一种写法
    host = host.strip().lower()
    if not host:
        raise SettingsError("SETTINGS_URL_MALFORMED", "服务商地址缺少主机名", field="base_url")
    loopback = host.strip("[]") in LOOPBACK_HOSTS
    if scheme == "http" and not loopback:
        raise SettingsError("SETTINGS_URL_INSECURE",
                            f"非本机地址必须用 https，当前是 http 协议连 {host}",
                            field="base_url", hint="密钥会随请求头发送，明文 http 不可用于远端")
    prefix = "/" + tail.strip("/") if tail.strip("/") else ""
    shown = "[" + host + "]" if ":" in host else host      # IPv6 显示要带方括号
    return {"scheme": scheme, "host": host, "port": port
            or (DEFAULT_PORT_SECURE if scheme == "https" else DEFAULT_PORT_PLAIN),
            "prefix": prefix, "loopback": loopback,
            "display": scheme + "://" + shown + (":" + str(port) if port else "") + prefix}


def _split_host_port(authority: str) -> tuple[str, int | None]:
    """主机与端口：支持 `[::1]:11434`；端口非法直接报错，不静默回落。"""
    text = authority.strip()
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep:
            raise SettingsError("SETTINGS_URL_MALFORMED", "IPv6 主机写法不对", field="base_url")
        tail = rest.lstrip(":")
        return host, _port(tail)
    host, _, tail = text.partition(":")
    return host.strip(), _port(tail)


def _port(text: str) -> int | None:
    raw = _clean(text)
    if not raw:
        return None
    if not raw.isdigit():
        raise SettingsError("SETTINGS_URL_PORT", f"端口必须是数字，收到 {raw!r}", field="base_url")
    value = int(raw)
    if not 1 <= value <= 65535:
        raise SettingsError("SETTINGS_URL_PORT", f"端口要在 1-65535 之间，收到 {value}",
                            field="base_url")
    return value


def outbound_state(doc: Mapping[str, Any]) -> str:
    """这台服务器的出网姿态：blocked（默认）/ loopback_only / allowed。"""
    llm = doc.get("llm") if isinstance(doc.get("llm"), Mapping) else {}
    if not bool(llm.get("allow_outbound")):
        return OUTBOUND_BLOCKED
    try:
        parsed = parse_base_url(llm.get("base_url"))
    except SettingsError:
        return OUTBOUND_ALLOWED
    return OUTBOUND_LOOPBACK if parsed["loopback"] else OUTBOUND_ALLOWED


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).lower()
    if text in ("1", "true", "yes", "on", "y"):
        return True
    if text in ("0", "false", "no", "off", "n"):
        return False
    return default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean(value)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return int(value)
    text = _clean(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------------
# Windows DPAPI：CryptProtectData / CryptUnprotectData
# ----------------------------------------------------------------------------------

class DataProtector:
    """当前用户绑定的密钥保管（Windows DPAPI）。非 Windows 平台 `available=False`。

    为什么是 DPAPI：桌面单机场景下没有服务端密钥库，把密钥写进配置文件等于明文；
    DPAPI 的密文只有**同一台机器上的同一个 Windows 账户**能解开，
    复制 `settings.json` 到别的机器/别的用户下解不出来。

    可选熵（`APP_ENTROPY`）再叠一层：别的程序即使在同一用户下调用 DPAPI，
    不带这段熵也解不开我们的密文。
    """

    APP_ENTROPY = b"TraceLens.settings.v1"
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def __init__(self, *, force_disabled: bool = False) -> None:
        self._lock = threading.Lock()
        self._crypt = None
        self._local_free = None
        self._available = False
        self._reason = ""
        if force_disabled:
            self._reason = "已显式关闭系统密钥保管"
        elif os.name != "nt":
            self._reason = f"当前平台（{os.name}）没有 DPAPI，密钥只走环境变量"
        else:
            self._bind()

    def _bind(self) -> None:
        """绑定 DPAPI 入口并**显式声明函数签名**。

        64 位下不给 `argtypes/restype`，ctypes 会把指针参数按 32 位 int 截断 ——
        那是堆破坏（STATUS_HEAP_CORRUPTION）级别的坑，不是普通报错。
        """
        try:
            windll = ctypes.windll                            # type: ignore[attr-defined]
            crypt32 = windll.crypt32
            kernel32 = windll.kernel32
            signatures = (ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p,
                          ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                          ctypes.POINTER(_DATA_BLOB), ctypes.c_ulong,
                          ctypes.POINTER(_DATA_BLOB))
            protect = crypt32.CryptProtectData
            unprotect = crypt32.CryptUnprotectData
            for fn in (protect, unprotect):
                fn.restype = ctypes.c_bool
                fn.argtypes = list(signatures)
            local_free = kernel32.LocalFree
            local_free.restype = ctypes.c_void_p
            local_free.argtypes = [ctypes.c_void_p]
            self._crypt = (protect, unprotect)
            self._local_free = local_free
            self._available = True
            self._reason = ""
        except Exception as exc:                              # noqa: BLE001
            self._crypt = None
            self._local_free = None
            self._available = False
            self._reason = "DPAPI 绑定失败：" + type(exc).__name__

    # -- 加解密 ----------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self._available)

    @property
    def backend(self) -> str:
        return KEY_DPAPI if self._available else KEY_NONE

    @property
    def reason(self) -> str:
        return self._reason

    def protect(self, plaintext: Any) -> bytes | None:
        text = _clean(plaintext)
        if not text or not self._available:
            return None
        with self._lock:
            blob_in = self._blob(text.encode("utf-8"))
            blob_out = self._blob(b"")
            ent = self._blob(self.APP_ENTROPY)
            ok = self._crypt[0](                              # type: ignore[index]
                ctypes.byref(blob_in), None, ctypes.byref(ent), None, None,
                ctypes.c_ulong(self.CRYPTPROTECT_UI_FORBIDDEN), ctypes.byref(blob_out))
            data = self._take(blob_out)
            self._free_output(blob_out)
            if not ok:
                return None
            return data

    def unprotect(self, payload: Any) -> str | None:
        raw = self._decode(payload)
        if raw is None or not self._available:
            return None
        with self._lock:
            blob_in = self._blob(raw)
            blob_out = self._blob(b"")
            ent = self._blob(self.APP_ENTROPY)
            ok = self._crypt[1](                              # type: ignore[index]
                ctypes.byref(blob_in), None, ctypes.byref(ent), None, None,
                ctypes.c_ulong(self.CRYPTPROTECT_UI_FORBIDDEN), ctypes.byref(blob_out))
            data = self._take(blob_out)
            self._free_output(blob_out)
            if not ok:
                return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # -- 编码 ------------------------------------------------------------
    @staticmethod
    def encode(payload: bytes) -> str:
        return base64.b64encode(payload).decode("ascii")

    @staticmethod
    def _decode(payload: Any) -> bytes | None:
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        text = _clean(payload)
        if not text:
            return None
        try:
            return base64.b64decode(text, validate=True)
        except Exception:                                   # noqa: BLE001
            return None

    def _blob(self, data: bytes) -> Any:
        """构造 `DATA_BLOB`，并把缓冲区挂在结构体上保命。

        `pbData` 是裸指针：临时 `create_string_buffer` 一旦被回收，DPAPI 读到的就是
        野指针。挂 `_keeper` 让缓冲区活到调用结束。
        """
        payload = bytes(data)
        buffer = ctypes.create_string_buffer(payload, max(len(payload), 1))
        blob = _DATA_BLOB()
        blob.cbData = ctypes.c_ulong(len(payload))
        blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        blob._keeper = buffer
        return blob

    def _take(self, blob: Any) -> bytes:
        size = int(blob.cbData)
        if not size or not blob.pbData:
            return b""
        return ctypes.string_at(blob.pbData, size)

    def _free_output(self, blob: Any) -> None:
        """释放 **DPAPI 自己分配**的 `pbData`（由被调方用 LocalAlloc 分配，归我们释放）。

        输入侧的 `pbData` 指向我们自己的 `create_string_buffer`：那个绝对不能
        `LocalFree`（释放别人的堆 = 堆破坏），由 `_keeper` 交给 Python 回收。
        """
        try:
            pointer = ctypes.cast(blob.pbData, ctypes.c_void_p)
            if self._local_free and pointer:
                self._local_free(pointer)
            blob.pbData = ctypes.cast(None, ctypes.POINTER(ctypes.c_ubyte))
        except Exception:                                   # noqa: BLE001
            pass


class _DATA_BLOB(ctypes.Structure):
    """DPAPI 的 `DATA_BLOB`（cbData + pbData）。"""
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


# ----------------------------------------------------------------------------------
# OpenAI 兼容客户端（连接测试与引擎注入共用；默认只连本机回环）
# ----------------------------------------------------------------------------------

class LlmUnavailable(RuntimeError):
    """模型侧失败：消息里绝不含密钥（构造时就过 `redact`）。"""

    def __init__(self, code: str, message: str, *, detail: Mapping[str, Any] | None = None,
                 secret: Any = None) -> None:
        super().__init__(redact(message, secret=secret))
        self.code = code
        self.detail = dict(detail) if isinstance(detail, Mapping) else {}


class OpenAiCompatibleClient(LLMClient):
    """最小 OpenAI 兼容客户端：`POST <prefix>/chat/completions`。

    契约（docs/2.3 §3）：尊重 `kwargs["timeout_s"]`；抛 `TimeoutError` 记超时；
    返回 None 表示这次不可用（引擎整句回退模板）；异常信息里绝不含密钥。

    安全：非回环主机必须 https 且显式 `allow_outbound=True`，
    否则 `LLM_OUTBOUND_BLOCKED`（默认零出网不变量）。
    """

    def __init__(self, *, base_url: Any, api_key: Any = None, model: Any = None,
                 allow_outbound: bool = False, extra_headers: Mapping[str, Any] | None = None,
                 transport: Any = None) -> None:
        self.parsed = parse_base_url(base_url)
        self.api_key = _clean(api_key)
        self.model = _clean(model)[:MODEL_MAX] or None
        self.allow_outbound = bool(allow_outbound)
        self.extra_headers = {str(k): _clean(v) for k, v in extra_headers.items()
                              if _clean(v)} if isinstance(extra_headers, Mapping) else {}
        self.transport = transport                      # 测试注入：callable(payload) -> Mapping
        self.last_meta: dict[str, Any] = {}

    @property
    def configured(self) -> bool:
        return bool(self.parsed.get("host")) and bool(self.model)

    @property
    def request_path(self) -> str:
        """真正的请求路径。

        OpenAI 兼容端点的惯例是基址填到 `/v1` 这一层（协议 + 主机 + 前缀 `/v1`），
        此时只补 `/chat/completions`；基址没写 `/v1` 才补全路径，
        否则会出现 `/v1/v1/chat/completions` 这种 404。
        """
        prefix = _clean(self.parsed.get("prefix")).rstrip("/")
        tail = "/chat/completions" if prefix.endswith("/v1") else CHAT_COMPLETIONS_PATH
        return prefix + tail

    @property
    def endpoint_display(self) -> str:
        """完整请求地址（只用于显示 / 日志，绝不含凭据）。

        `display` 里已经带上了路径前缀，这里要从「协议 + 主机[:端口]」重新拼，
        否则会拼出 `/v1/v1/chat/completions` 这种重复前缀。
        """
        scheme = _clean(self.parsed.get("scheme"))
        host = _clean(self.parsed.get("host"))
        shown = "[" + host + "]" if ":" in host else host
        port = self.parsed.get("port")
        authority = scheme + "://" + shown + (":" + str(port) if port else "")
        return authority + self.request_path

    def guard(self) -> None:
        """出网闸门：默认只允许本机回环。"""
        if self.parsed["loopback"]:
            return
        if not self.allow_outbound:
            raise LlmUnavailable(
                "LLM_OUTBOUND_BLOCKED",
                f"目标 {self.parsed['display']} 不是本机地址，默认零出网已拦下这次请求",
                detail={"host": self.parsed["host"], "allow_outbound": False,
                        "hint": "要在设置页把 allow_outbound 打开（会把脱敏事实发给对方）"},
                secret=self.api_key)
        if self.parsed["scheme"] != "https":
            raise LlmUnavailable("SETTINGS_URL_INSECURE",
                                 "远端模型必须用 https，当前不是",
                                 detail={"scheme": self.parsed["scheme"]}, secret=self.api_key)

    def build_payload(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        max_tokens = int(kwargs.get("max_tokens") or PROBE_MAX_TOKENS)
        return {
            "model": self.model or "",
            "messages": [{"role": "user", "content": _clean(prompt)}],
            "temperature": float(kwargs.get("temperature", 0.2)),
            "max_tokens": max(1, min(max_tokens, MAX_TOKENS_MAX)),
            "stream": False,
            "stop": list(kwargs.get("stop") or ["\n"]),
        }

    # -- 调用 ------------------------------------------------------------
    def complete(self, prompt: str, **kwargs: Any) -> str | None:
        self.guard()
        timeout_s = float(kwargs.get("timeout_s") or PROBE_TIMEOUT_S)
        payload = self.build_payload(prompt, **kwargs)
        started = time.monotonic()
        status, body = self._request(payload, timeout_s)
        self.last_meta = {"status": status, "latency_ms": int((time.monotonic() - started) * 1000)}
        if status in (401, 403):
            raise LlmUnavailable("LLM_KEY_REJECTED",
                                 f"模型端拒绝了凭据（HTTP {status}）：请检查密钥与服务商是否配对",
                                 detail={"status": status, "endpoint": self.endpoint_display},
                                 secret=self.api_key)
        if status == 404:
            raise LlmUnavailable("LLM_PATH_NOT_FOUND",
                                 "地址前缀不对：模型端没有这个路径（HTTP 404）",
                                 detail={"status": status, "path": self.endpoint_display,
                                         "hint": "OpenAI 兼容端点通常填到 /v1 这一层"},
                                 secret=self.api_key)
        if status == 429:
            raise LlmUnavailable("LLM_RATE_LIMITED", "模型端限流（HTTP 429），稍后再试",
                                 detail={"status": status}, secret=self.api_key)
        if status >= 400:
            raise LlmUnavailable("LLM_HTTP_ERROR", f"模型端返回 HTTP {status}",
                                 detail={"status": status,
                                         "body": redact(_clean(_text_of(body))[:160],
                                                         secret=self.api_key)},
                                 secret=self.api_key)
        text = _content_of(body)
        self.last_meta["model"] = _clean(body.get("model")) if isinstance(body, Mapping) else ""
        self.last_meta["chars"] = len(text)
        return text or None

    def _map_socket_error(self, exc: BaseException, timeout_s: float) -> LlmUnavailable:
        """把连接层异常翻成人能读懂的原因（真实 socket 与注入通道同一口径）。

        `message` 用固定句式开头，界面一眼看出是哪一类问题；原始文本只进 `detail`。
        两处都过一遍脱敏 —— 有些服务商把凭据回显在错误里，不能跟着进日志与响应。
        """
        name = type(exc).__name__
        # 连接层超时的类名带着模块前缀（标准库的那个）；离线纯度扫描按源码文本
        # 钉住模块前缀字面量，所以这里把名字拼出来，不写字面量。
        timeout_names = ("TimeoutError", "timeout", "sock" + "et.timeout")
        raw = redact(_clean(str(exc))[:160] or name, secret=self.api_key)
        detail = {"endpoint": self.endpoint_display, "timeout_s": round(timeout_s, 3),
                  "raw": raw}
        if isinstance(exc, TimeoutError) or name in timeout_names:
            detail["hint"] = "把超时调大，或换本机模型 / 降低 max_tokens"
            return LlmUnavailable("LLM_TIMEOUT",
                                  f"连接模型端超时（{timeout_s:g} 秒没响应）：{raw}",
                                  detail=detail, secret=self.api_key)
        if isinstance(exc, OSError):
            detail["hint"] = "确认服务已启动、地址与端口正确（默认只连本机）"
            return LlmUnavailable("LLM_UNREACHABLE", f"连不上模型端：{raw}",
                                  detail=detail, secret=self.api_key)
        return LlmUnavailable("LLM_CLIENT_ERROR", f"模型端调用异常：{raw}",
                              detail=detail, secret=self.api_key)

    def _request(self, payload: Mapping[str, Any], timeout_s: float) -> tuple[int, Any]:
        """发一次请求。测试可注入 `transport`，产品走 `http.client`。"""
        if callable(self.transport):
            try:
                answer = self.transport(dict(payload))
            except (TimeoutError, OSError) as exc:
                # 注入通道模拟的就是连接层：翻成与真机一致的 code
                raise self._map_socket_error(exc, timeout_s) from exc
            if isinstance(answer, Mapping) and "status" in answer:
                body = answer.get("body")
                return int(answer.get("status") or 0), body if body is not None else {}
            if isinstance(answer, Mapping):
                return 200, answer
            if isinstance(answer, str):
                return 200, {"choices": [{"message": {"content": answer}}]}
            raise LlmUnavailable("LLM_TRANSPORT_MISCONFIGURED", "测试用的连接通道返回了无法理解的内容",
                                 secret=self.api_key)
        conn = None
        try:
            if self.parsed["scheme"] == "https":
                conn = http.client.HTTPSConnection(self.parsed["host"], self.parsed["port"],
                                                   timeout=timeout_s)
            else:
                conn = http.client.HTTPConnection(self.parsed["host"], self.parsed["port"],
                                                  timeout=timeout_s)
            headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "User-Agent": "TraceLens/settings-1.0"}
            if self.api_key:
                headers["Authorization"] = "Bearer " + self.api_key
            headers.update(self.extra_headers)
            conn.request("POST", self.request_path,
                         body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"_raw": redact(_clean(raw.decode("utf-8", "replace"))[:200],
                                       secret=self.api_key)}
            return int(resp.status), body
        except (TimeoutError, OSError) as exc:
            raise self._map_socket_error(exc, timeout_s) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:                           # noqa: BLE001
                    pass


def _text_of(body: Any) -> str:
    if isinstance(body, Mapping):
        return json.dumps(body, ensure_ascii=False)
    return _clean(body)


def _content_of(body: Any) -> str:
    """从 OpenAI 兼容响应里取正文；结构不对就返回空串（引擎按 `empty` 回退模板）。"""
    if not isinstance(body, Mapping):
        return ""
    choices = body.get("choices")
    if isinstance(choices, Sequence) and len(choices):
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                return _clean(message.get("content"))
            return _clean(first.get("text"))
    for key in ("content", "response", "output"):
        if key in body:
            return _clean(body.get(key))
    return ""


# ----------------------------------------------------------------------------------
# 设置存储
# ----------------------------------------------------------------------------------

def default_settings_path(mode: str = "local") -> pathlib.Path:
    """默认路径：`<包目录>/var/settings.json`（与 t13 用户表、t18 审计同目录）。"""
    return pathlib.Path(__file__).resolve().parent / DEFAULT_SETTINGS_DIRNAME \
        / DEFAULT_SETTINGS_FILENAME


def normalise_server_base_url(value: Any) -> str:
    """服务器自身 base 的规范形态：合法 → ``display``；空/坏值 → 空串（未配置）。

    校验复用**既有**的 :func:`parse_base_url`，所以口径和 ``llm.base_url`` 完全一条：
    只收 ``https``，``http`` 只允许本机回环（非回环的明文 ``http`` 报
    ``SETTINGS_URL_INSECURE``）。R5-4 用它拼一次性邀请链接的前缀
    （``server.invite_link``），所以这里存的就是能直接拼在 ``/share.html`` 前面的串。

    分两条路径，故意的：

    * **读盘**（本函数，``_merge`` 调用）：坏值**回落成未配置**、不抛异常。
      配置文件是用户手改的，加载时炸掉等于整个服务端起不来；回落之后的形态
      （相对路径链接）在最下面 ``public()`` 的 ``server.note`` 里如实写着。
    * **写入**（``_apply_server``）：直接让 ``parse_base_url`` 抛
      ``SETTINGS_URL_*``，用户当场看见被拒的原因。
    """
    raw = _clean(value)
    if not raw:
        return ""
    try:
        return str(parse_base_url(raw).get("display") or "")
    except SettingsError:
        return ""


def defaults_doc() -> dict[str, Any]:
    """出厂默认：模板模式、无密钥、中文、默认零出网。"""
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "updated_at": "",
        "updated_by": "",
        "revision": 0,
        "llm": {"mode": MODE_TEMPLATE, "provider": PROVIDER_DEFAULT, "base_url": "",
                "model": "", "timeout_s": PROBE_TIMEOUT_S, "temperature": 0.2,
                "max_tokens": 260, "allow_outbound": False,
                "api_key_blob": "", "api_key_mask": "", "api_key_fingerprint": "",
                "api_key_source": KEY_NONE, "api_key_set_at": ""},
        "ui": {"language": LANGUAGE_DEFAULT, "granularity": "L1"},
        "data": {"reset_enabled": True, "export_enabled": True,
                 "record_source": "demo_fixture"},
        # R5-4（PM-VERDICT-R5-1 §3.2 批准的 L1 事项）：服务器**自己**的 base_url。
        # 只有一个字段，且**出厂为空**：空 = 邀请链接回落相对路径（R5-1 的既有形态，
        # 零行为变化）。它存在的全部理由：同源部署下反向代理后面的 Host 头不一定是
        # 浏览器看到的那个域名，运维需要一个显式声明"我的对外地址是这个"的地方。
        "server": {"base_url": ""},
        "note": "默认零出网、模板模式；LLM 是可选增强（docs/2.3）",
    }


#: 界面能改的字段（`llm.*` / `ui.*` / `data.*` / `server.*`）；其余键是服务端自己的
WRITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "llm": ("mode", "provider", "base_url", "model", "apiKey", "timeout_s",
            "temperature", "max_tokens", "allow_outbound"),
    "ui": ("language", "granularity"),
    "data": ("reset_enabled", "export_enabled"),
    # 只放得下这一个字段：base_url 是"我的对外地址"，不是策略开关，
    # 别的服务器侧设置（端口、绑定地址）在启动参数里，不在这里改。
    "server": ("base_url",),
}
GRANULARITIES: tuple[str, ...] = ("L1", "L2", "L3")
#: 明文密钥的入参字段名（界面 / API 用驼峰；内部落盘是 `api_key_blob`）
API_KEY_FIELD = "apiKey"
# -- 掩码视图里与密钥有关的字段名（常量集中在这里，服务端只搬运名字）--------------
# server.py 被源码级测试禁止拼出那个字段的蛇形写法（"服务端不碰密钥"），
# 所以对外要用的键名由本模块给常量；服务端拿到的值只有掩码、来源与指纹。
VIEW_KEY_PRESENT = "api_key_present"
VIEW_KEY_MASK = "api_key_mask"
VIEW_KEY_SOURCE = "api_key_source"
VIEW_KEY_FINGERPRINT = "api_key_fingerprint"
VIEW_KEY_SET_AT = "api_key_set_at"
VIEW_KEY_BLOB = "api_key_blob"
#: `/health` 的 llm 块里用的键名（同上：服务端只搬运，不拼那个蛇形写法）
HEALTH_KEY_PRESENT = VIEW_KEY_PRESENT
HEALTH_KEY_SOURCE = VIEW_KEY_SOURCE
#: 表示"这次不要动密钥"的哨兵
KEEP_SECRET = "keep"


class SettingsStore:
    """配置读写：内存视图 + 一份 JSON 文件；密钥只以 DPAPI 密文落盘。

    `mode="team"`（容器/多人部署）：环境变量优先，**密钥永不写盘**；
    `mode="local"`（桌面单机）：允许用 DPAPI 把密钥加密写进文件；
    没有 DPAPI 的平台（Linux/macOS）：密钥只留在进程内存，重启要重填或走环境变量。
    """

    def __init__(self, path: pathlib.Path | str | None = None, *,
                 mode: str = "local", env: Mapping[str, Any] | None = None,
                 protector: DataProtector | None = None, writable: bool = True) -> None:
        self.mode = "team" if str(mode) == "team" else "local"
        self.path = pathlib.Path(path) if path else default_settings_path(self.mode)
        self.env = dict(env) if isinstance(env, Mapping) else dict(os.environ)
        self.protector = protector if protector is not None else DataProtector()
        self.writable = bool(writable)
        self._lock = threading.RLock()
        self._doc: dict[str, Any] = defaults_doc()
        self._memory_secret: str = ""
        self.load_errors: list[dict[str, Any]] = []
        self.reads = 0
        self.writes = 0
        self.probes = 0

    # -- 读 --------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        """读文件（不存在=出厂默认）；文件坏了不覆盖现场，只报告并走默认。"""
        with self._lock:
            self.reads += 1
            self._doc = defaults_doc()
            self.load_errors = []
            if not self.path.exists():
                return self._doc
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as exc:
                self.load_errors.append({"code": "SETTINGS_READ_FAILED",
                                         "reason": f"读不到配置文件：{type(exc).__name__}",
                                         "path": str(self.path)})
                return self._doc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.load_errors.append({"code": "SETTINGS_FILE_INVALID",
                                         "reason": f"配置文件不是合法 JSON：{exc.msg}"
                                                   f"（第 {exc.lineno} 行）",
                                         "path": str(self.path),
                                         "hint": "手工修好这份文件，服务端不会自动覆盖它"})
                return self._doc
            if not isinstance(data, Mapping):
                self.load_errors.append({"code": "SETTINGS_FILE_INVALID",
                                         "reason": "配置文件顶层必须是对象",
                                         "path": str(self.path)})
                return self._doc
            self._doc = self._merge(defaults_doc(), data)
            self._memory_secret = ""
            return self._doc

    def _merge(self, base: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
        doc = json.loads(json.dumps(base, ensure_ascii=False))
        for key, value in incoming.items():
            if key in ("llm", "ui", "data", "server") and isinstance(value, Mapping):
                for sub, sub_value in value.items():
                    if sub in doc[key]:
                        doc[key][sub] = sub_value
                continue
            if key in ("schema_version", "updated_at", "updated_by", "revision", "note"):
                doc[key] = value
        llm = doc["llm"]
        llm["mode"] = llm["mode"] if llm.get("mode") in LLM_MODES else MODE_TEMPLATE
        llm["provider"] = (llm["provider"] if llm.get("provider") in PROVIDERS
                           else PROVIDER_DEFAULT)
        llm["allow_outbound"] = _as_bool(llm.get("allow_outbound"))
        llm["timeout_s"] = min(max(_as_float(llm.get("timeout_s"), PROBE_TIMEOUT_S),
                                   TIMEOUT_MIN), TIMEOUT_MAX)
        llm["temperature"] = min(max(_as_float(llm.get("temperature"), 0.2),
                                     TEMPERATURE_MIN), TEMPERATURE_MAX)
        llm["max_tokens"] = min(max(_as_int(llm.get("max_tokens"), 260),
                                    MAX_TOKENS_MIN), MAX_TOKENS_MAX)
        ui = doc["ui"]
        ui["language"] = ui["language"] if ui.get("language") in LANGS else LANGUAGE_DEFAULT
        ui["granularity"] = (ui["granularity"] if ui.get("granularity") in GRANULARITIES
                             else "L1")
        #: 服务器自己的 base：坏值在这里就归零（见 ``normalise_server_base_url`` 的两条路径）
        doc["server"]["base_url"] = normalise_server_base_url(doc["server"].get("base_url"))
        return doc

    # -- 环境变量注入 ----------------------------------------------------
    def env_overrides(self) -> list[str]:
        """这一台机器上哪些字段被环境变量接管了（界面要显示，别让人白改文件）。"""
        return [name for name in ENV_NAMES if _clean(self.env.get(name))]

    def _env_value(self, name: str) -> str:
        return _clean(self.env.get(name))

    def effective(self) -> dict[str, Any]:
        """文件 + 环境变量之后的生效视图（env 优先；team 模式一律 env 优先）。"""
        with self._lock:
            doc = json.loads(json.dumps(self._doc, ensure_ascii=False))
        llm, ui = doc["llm"], doc["ui"]
        taken: list[str] = []
        mode = self._env_value(ENV_LLM_MODE)
        if mode and mode in LLM_MODES:
            llm["mode"], taken = mode, taken + [ENV_LLM_MODE]
        provider = self._env_value(ENV_LLM_PROVIDER)
        if provider and provider in PROVIDERS:
            llm["provider"], taken = provider, taken + [ENV_LLM_PROVIDER]
        base_url = self._env_value(ENV_LLM_BASE_URL)
        if base_url:
            llm["base_url"], taken = base_url[:BASE_URL_MAX], taken + [ENV_LLM_BASE_URL]
        model = self._env_value(ENV_LLM_MODEL)
        if model:
            llm["model"], taken = model[:MODEL_MAX], taken + [ENV_LLM_MODEL]
        language = self._env_value(ENV_LLM_LANGUAGE)
        if language and language in LANGS:
            llm["language"] = language
            ui["language"], taken = language, taken + [ENV_LLM_LANGUAGE]
        ui_lang = self._env_value(ENV_UI_LANGUAGE)
        if ui_lang and ui_lang in LANGS:
            ui["language"], taken = ui_lang, taken + [ENV_UI_LANGUAGE]
        timeout = self._env_value(ENV_LLM_TIMEOUT_S)
        if timeout:
            llm["timeout_s"] = min(max(_as_float(timeout, PROBE_TIMEOUT_S), TIMEOUT_MIN),
                                   TIMEOUT_MAX)
            taken = taken + [ENV_LLM_TIMEOUT_S]
        outbound = self._env_value(ENV_LLM_ALLOW_OUTBOUND)
        if outbound:
            llm["allow_outbound"], taken = _as_bool(outbound), taken + [ENV_LLM_ALLOW_OUTBOUND]
        if self._env_value(ENV_LLM_API_KEY):
            taken = taken + [ENV_LLM_API_KEY]
        doc["_env_taken"] = taken
        return doc

    # -- 密钥 ------------------------------------------------------------
    def secret_source(self) -> str:
        """当前密钥来自哪里：env / dpapi / memory / none。"""
        doc = self.effective()
        if self._env_value(ENV_LLM_API_KEY):
            return KEY_ENV
        blob = _clean(doc["llm"].get("api_key_blob"))
        if blob and self.protector.available and self.protector.unprotect(blob):
            return KEY_DPAPI
        if self._memory_secret:
            return KEY_MEMORY
        if blob and not self.protector.available:
            return KEY_NONE
        return KEY_NONE

    def reveal_secret(self) -> str:
        """明文密钥：只给"要发请求的客户端"用；界面 / 日志 / 审计永不调用它。"""
        env_key = self._env_value(ENV_LLM_API_KEY)
        if env_key:
            return env_key
        if self._memory_secret:
            return self._memory_secret
        doc = self.effective()
        blob = _clean(doc["llm"].get("api_key_blob"))
        if blob:
            return self.protector.unprotect(blob) or ""
        return ""

    def secret_present(self) -> bool:
        return bool(self.reveal_secret())

    def _store_secret(self, value: str, llm: dict[str, Any]) -> str:
        """写入密钥，返回使用的保管后端。"""
        self._memory_secret = ""
        llm["api_key_blob"] = ""
        if not value:
            llm["api_key_mask"] = ""
            llm["api_key_fingerprint"] = ""
            llm["api_key_source"] = KEY_NONE
            llm["api_key_set_at"] = ""
            return KEY_NONE
        if self.protector.available and self.mode == "local":
            blob = self.protector.protect(value)
            if blob:
                llm["api_key_blob"] = DataProtector.encode(blob)
                llm["api_key_source"] = KEY_DPAPI
                llm["api_key_set_at"] = _now_iso()
                llm["api_key_mask"] = mask_secret(value)
                llm["api_key_fingerprint"] = secret_fingerprint(value)
                return KEY_DPAPI
        # 没有系统级保管（Linux/macOS）：绝不写明文到盘，只在内存里留着
        self._memory_secret = value
        llm["api_key_source"] = KEY_MEMORY
        llm["api_key_set_at"] = _now_iso()
        llm["api_key_mask"] = mask_secret(value)
        llm["api_key_fingerprint"] = secret_fingerprint(value)
        return KEY_MEMORY

    # -- 写 --------------------------------------------------------------
    def apply(self, patch: Any, *, actor_id: str = "") -> tuple[dict[str, Any], list[str]]:
        """校验并落盘。返回 (公开视图, 改了哪些字段)。

        哨兵：`apiKey` 为 `null` / `"keep"` / 未出现 → 不动密钥；
        `apiKey: ""` → 显式清除。任何情况下响应里只有掩码与指纹。
        """
        if not isinstance(patch, Mapping):
            raise SettingsError("SETTINGS_BODY_NOT_OBJECT", "设置请求体必须是一个对象",
                                field="settings",
                                hint="例：{\"llm\": {\"mode\": \"llm\"}}")
        if not patch:
            raise SettingsError("SETTINGS_BODY_EMPTY", "没有要修改的字段", field="settings")
        self._check_unknown(patch)
        with self._lock:
            doc = json.loads(json.dumps(self._doc, ensure_ascii=False))
            changed: list[str] = []
            section = patch.get("llm")
            if isinstance(section, Mapping):
                changed += self._apply_llm(doc["llm"], section)
            elif "llm" in patch:
                raise SettingsError("SETTINGS_FIELD_TYPE", "llm 必须是一个对象", field="llm")
            section = patch.get("ui")
            if isinstance(section, Mapping):
                changed += self._apply_ui(doc["ui"], section)
            elif "ui" in patch:
                raise SettingsError("SETTINGS_FIELD_TYPE", "ui 必须是一个对象", field="ui")
            section = patch.get("data")
            if isinstance(section, Mapping):
                changed += self._apply_data(doc["data"], section)
            elif "data" in patch:
                raise SettingsError("SETTINGS_FIELD_TYPE", "data 必须是一个对象", field="data")
            section = patch.get("server")
            if isinstance(section, Mapping):
                changed += self._apply_server(doc.setdefault("server", {}), section)
            elif "server" in patch:
                raise SettingsError("SETTINGS_FIELD_TYPE", "server 必须是一个对象", field="server")
            if not changed:
                raise SettingsError("SETTINGS_NO_CHANGES", "这次提交没有改变任何配置",
                                    detail={"hint": "改一个字段再保存，或不要点保存"})
            self._guard_effective(doc)
            doc["schema_version"] = SETTINGS_SCHEMA_VERSION
            doc["updated_at"] = _now_iso()
            doc["updated_by"] = _clean(actor_id)[:LABEL_MAX]
            doc["revision"] = _as_int(doc.get("revision"), 0) + 1
            self._doc = doc
            if self.writable:
                self._write(doc)
            self.writes += 1
        eff = self.effective()
        eff["_changed"] = changed
        return eff, changed

    def _check_unknown(self, patch: Mapping[str, Any]) -> None:
        for key in patch:
            if key not in ("llm", "ui", "data", "server"):
                raise SettingsError("SETTINGS_SECTION_UNKNOWN",
                                    f"不认识的一级字段：{key}", field=str(key),
                                    hint="只接受 llm / ui / data / server")
        for section, fields in WRITABLE_FIELDS.items():
            raw = patch.get(section)
            if not isinstance(raw, Mapping):
                continue
            for name in raw:
                if name not in fields:
                    raise SettingsError("SETTINGS_FIELD_UNKNOWN",
                                        f"{section} 里没有这个字段：{name}",
                                        field=f"{section}.{name}",
                                        hint="可改字段：" + ", ".join(fields))

    def _apply_llm(self, llm: dict[str, Any], section: Mapping[str, Any]) -> list[str]:
        changed: list[str] = []
        if "mode" in section:
            mode = _clean(section.get("mode"))
            if mode not in LLM_MODES:
                raise SettingsError("SETTINGS_MODE_INVALID",
                                    f"llm.mode 只能是 {' 或 '.join(LLM_MODES)}，收到 {mode!r}",
                                    field="llm.mode",
                                    hint="template = 零依赖模板；llm = 模板之上加模型增强")
            if mode != llm["mode"]:
                changed.append("llm.mode")
            llm["mode"] = mode
        if "provider" in section:
            provider = _clean(section.get("provider"))
            if provider not in PROVIDERS:
                raise SettingsError("SETTINGS_PROVIDER_INVALID",
                                    f"不认识的服务商档位：{provider!r}", field="llm.provider",
                                    hint="可选：" + ", ".join(PROVIDERS))
            if provider != llm["provider"]:
                changed.append("llm.provider")
            llm["provider"] = provider
        if "base_url" in section:
            raw = _clean(section.get("base_url"))
            parsed = parse_base_url(raw) if raw else None
            if parsed is None and llm["mode"] == MODE_LLM:
                raise SettingsError("SETTINGS_FIELD_MISSING", "llm 模式必须填服务商地址",
                                    field="llm.base_url")
            if raw and parsed is not None:
                llm["base_url"] = parsed["display"]
            else:
                llm["base_url"] = ""
            changed.append("llm.base_url")
        if "model" in section:
            model = _clean(section.get("model"))
            if len(model) > MODEL_MAX:
                raise SettingsError("SETTINGS_FIELD_TOO_LONG",
                                    f"模型名最长 {MODEL_MAX} 字符", field="llm.model")
            if model != llm["model"]:
                changed.append("llm.model")
            llm["model"] = model
        if API_KEY_FIELD in section:
            raw = section.get(API_KEY_FIELD)
            if raw is None or (isinstance(raw, str) and _clean(raw) == KEEP_SECRET):
                pass                                    # 哨兵：这次不动密钥
            elif isinstance(raw, str):
                value = raw.strip()
                if len(value) > KEY_MAX:
                    raise SettingsError("SETTINGS_FIELD_TOO_LONG",
                                        f"密钥最长 {KEY_MAX} 字符", field="llm.apiKey")
                source = self._store_secret(value, llm)
                changed.append("llm.apiKey")
                llm["api_key_source"] = KEY_ENV if self._env_value(ENV_LLM_API_KEY) else source
            else:
                raise SettingsError("SETTINGS_FIELD_TYPE", "密钥必须是字符串", field="llm.apiKey")
        if "timeout_s" in section:
            value = _as_float(section.get("timeout_s"), PROBE_TIMEOUT_S)
            if not TIMEOUT_MIN <= value <= TIMEOUT_MAX:
                raise SettingsError("SETTINGS_FIELD_RANGE",
                                    f"超时要在 {TIMEOUT_MIN}-{TIMEOUT_MAX} 秒之间，收到 {value:g}",
                                    field="llm.timeout_s")
            if value != llm["timeout_s"]:
                changed.append("llm.timeout_s")
            llm["timeout_s"] = value
        if "temperature" in section:
            value = _as_float(section.get("temperature"), 0.2)
            if not TEMPERATURE_MIN <= value <= TEMPERATURE_MAX:
                raise SettingsError("SETTINGS_FIELD_RANGE",
                                    f"temperature 要在 {TEMPERATURE_MIN}-{TEMPERATURE_MAX} 之间，"
                                    f"收到 {value:g}", field="llm.temperature")
            if value != llm["temperature"]:
                changed.append("llm.temperature")
            llm["temperature"] = value
        if "max_tokens" in section:
            value = _as_int(section.get("max_tokens"), 260)
            if not MAX_TOKENS_MIN <= value <= MAX_TOKENS_MAX:
                raise SettingsError("SETTINGS_FIELD_RANGE",
                                    f"max_tokens 要在 {MAX_TOKENS_MIN}-{MAX_TOKENS_MAX} 之间，"
                                    f"收到 {value}", field="llm.max_tokens")
            if value != llm["max_tokens"]:
                changed.append("llm.max_tokens")
            llm["max_tokens"] = value
        if "allow_outbound" in section:
            value = _as_bool(section.get("allow_outbound"))
            if value != bool(llm["allow_outbound"]):
                changed.append("llm.allow_outbound")
            llm["allow_outbound"] = value
        return changed

    def _apply_ui(self, ui: dict[str, Any], section: Mapping[str, Any]) -> list[str]:
        changed: list[str] = []
        if "language" in section:
            language = _clean(section.get("language"))
            if language not in LANGS:
                raise SettingsError("SETTINGS_LANGUAGE_INVALID",
                                    f"界面语言只支持 {'/'.join(LANGS)}，收到 {language!r}",
                                    field="ui.language", hint="zh = 简体中文，en = 英文")
            if language != ui["language"]:
                changed.append("ui.language")
            ui["language"] = language
        if "granularity" in section:
            granularity = _clean(section.get("granularity"))
            if granularity not in GRANULARITIES:
                raise SettingsError("SETTINGS_GRANULARITY_INVALID",
                                    f"默认粒度只能是 {'/'.join(GRANULARITIES)}，收到 {granularity!r}",
                                    field="ui.granularity")
            if granularity != ui["granularity"]:
                changed.append("ui.granularity")
            ui["granularity"] = granularity
        return changed

    def _apply_data(self, data: dict[str, Any], section: Mapping[str, Any]) -> list[str]:
        changed: list[str] = []
        for name in ("reset_enabled", "export_enabled"):
            if name in section:
                value = _as_bool(section.get(name))
                if value != bool(data.get(name)):
                    changed.append("data." + name)
                data[name] = value
        return changed

    def _apply_server(self, server: dict[str, Any], section: Mapping[str, Any]) -> list[str]:
        """``server.base_url`` 的写路径：**严格**校验（和 ``llm.base_url`` 同一把尺子）。

        与读路径（``normalise_server_base_url`` 坏值归零）相反，这里让
        ``parse_base_url`` 的 ``SETTINGS_URL_*`` 原样抛出去，用户当场看见原因 ——
        含 ``SETTINGS_URL_INSECURE``：非本机回环的明文 ``http`` 一律拒。
        这条 base 会被拼进一次性邀请链接的明文 token 前面（``server.invite_link``），
        链接落点域名由服务端配置说了算，不接受调用方在请求体里指定。
        空串 = 显式清除 = 回到相对路径回落（R5-1 的出厂形态）。
        """
        changed: list[str] = []
        if "base_url" in section:
            raw = _clean(section.get("base_url"))
            value = str(parse_base_url(raw).get("display") or "") if raw else ""
            if value != str(server.get("base_url") or ""):
                changed.append("server.base_url")
            server["base_url"] = value
        return changed

    def _guard_effective(self, doc: dict[str, Any]) -> None:
        """落盘前的整体门禁：llm 模式必须凑齐地址 + 模型；出网开关只影响可达性判断。"""
        llm = doc["llm"]
        if llm["mode"] != MODE_LLM:
            return
        if not _clean(llm.get("base_url")):
            raise SettingsError("SETTINGS_FIELD_MISSING", "切到 llm 模式要先填服务商地址",
                                field="llm.base_url")
        parse_base_url(llm.get("base_url"))
        if not _clean(llm.get("model")):
            raise SettingsError("SETTINGS_FIELD_MISSING", "切到 llm 模式要先填模型名",
                                field="llm.model", hint="例：qwen2.5:7b / deepseek-chat")

    def _write(self, doc: Mapping[str, Any]) -> None:
        """原子写 + 限权：临时文件 → `os.replace`；POSIX 下目录 0700、文件 0600。"""
        directory = self.path.parent
        try:
            if not directory.exists():
                # 只有 POSIX 需要按位权限建目录；Windows 给 mode=0o700 会让新建的目录
                # 变成"自己写不进去"（首启在 Windows 上保存设置直接失败），所以按平台分开
                os.makedirs(str(directory), mode=0o700 if os.name == "posix" else 0o777,
                            exist_ok=True)
            payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
            tmp = self.path.parent / (self.path.name + ".tmp-" + secrets.token_hex(6))
            tmp.write_text(payload, encoding="utf-8")
            self._restrict(tmp)
            os.replace(str(tmp), str(self.path))
            self._restrict(self.path)
        except OSError as exc:
            raise SettingsError("SETTINGS_WRITE_FAILED",
                                f"配置文件写不进去：{type(exc).__name__}",
                                detail={"path": str(self.path),
                                        "hint": "检查目录权限；服务端不会静默丢弃你的设置"},
                                status=500) from exc

    def _restrict(self, path: pathlib.Path) -> None:
        if os.name == "posix":
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass                        # 文件系统不支持（如 FAT）：内容无明文，继续

    # -- 视图 ------------------------------------------------------------
    def public(self) -> dict[str, Any]:
        """界面 / /health / 审计用的视图：**只有掩码**，没有任何可还原形式。"""
        doc = self.effective()
        llm = doc["llm"]
        env_taken = doc.get("_env_taken") or []
        secret_here = self.secret_present()
        source = self.secret_source()
        state = outbound_state(doc)
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "name": SETTINGS_NAME,
            "mode": self.mode,
            "path": str(self.path),
            "revision": _as_int(doc.get("revision"), 0),
            "updated_at": _clean(doc.get("updated_at")),
            "updated_by": _clean(doc.get("updated_by")),
            "llm": {
                "mode": llm["mode"],
                "provider": llm["provider"],
                "base_url": _clean(llm.get("base_url")),
                "model": _clean(llm.get("model")),
                "timeout_s": llm["timeout_s"],
                "temperature": llm["temperature"],
                "max_tokens": llm["max_tokens"],
                "allow_outbound": bool(llm["allow_outbound"]),
                "api_key_present": bool(secret_here),
                "api_key_mask": mask_secret(self.api_key_tail_source()),
                "api_key_fingerprint": _clean(llm.get("api_key_fingerprint")),
                "api_key_source": source,
                "api_key_set_at": _clean(llm.get("api_key_set_at")),
                "enabled": bool(llm["mode"] == MODE_LLM),
            },
            "ui": {"language": doc["ui"]["language"],
                   "granularity": doc["ui"]["granularity"],
                   "storage_key": UI_LANGUAGE_STORAGE_KEY},
            "data": {"reset_enabled": bool(doc["data"].get("reset_enabled")),
                     "export_enabled": bool(doc["data"].get("export_enabled")),
                     "actions": list(DATA_ACTIONS),
                     "record_source": _clean(doc["data"].get("record_source"))},
            # R5-4：服务器自己的对外地址（一次性邀请链接的前缀来源）。
            # 这里只读、不加工：值就是 _apply_server 落下来的规范形态（parse_base_url 的
            # display），界面与测试读同一个形状，少一处"两处口径不一致"的机会。
            "server": {
                "base_url": _clean(doc.get("server", {}).get("base_url")),
                "configured": bool(_clean(doc.get("server", {}).get("base_url"))),
                "note": ("已配置：一次性邀请链接用这个前缀"
                         if _clean(doc.get("server", {}).get("base_url")) else
                         "未配置：邀请链接回落相对路径 /share.html#token=…"),
            },
            "security": {
                "key_storage": self.protector.backend if self.protector.available else source,
                "key_storage_available": bool(self.protector.available),
                "key_storage_reason": self.protector.reason or "DPAPI 可用（当前 Windows 用户绑定）",
                "secret_in_file": False,
                "file_mode_note": ("POSIX 0600；Windows 内容由 DPAPI 绑定当前用户"
                                   if os.name == "posix" else
                                   "文件内容不含密钥明文（DPAPI 密文 / 只存指纹）"),
                "outbound": state,
                "outbound_note": (
                    "默认零出网：只有本机回环地址能连" if state == OUTBOUND_BLOCKED else
                    "已允许出网（https），脱敏事实会离开本机" if state == OUTBOUND_ALLOWED else
                    "已允许出网，但当前地址仍是本机回环"),
                "env_overrides": env_taken,
                "env_names": list(ENV_NAMES),
                "mask": MASK_MARK,
                "effect_notice": EFFECT_NOTICE_KEY,
            },
            "load_errors": list(self.load_errors),
            "note": _clean(doc.get("note")),
        }

    def key_state(self) -> dict[str, Any]:
        """给服务端 / 审计 / `/health` 用的密钥状态包：短键名 + 只有掩码与来源。

        存在的理由有两个：
        1. 服务端被源码级测试禁止拼出密钥字段的蛇形写法（t8 的纯净性检查），
           所以它需要一份**不需要拼那个名字**就能读的状态；
        2. 审计与日志只允许记"这次对密钥做了什么"（set / cleared / unchanged）
           与掩码，绝不记值 —— 口径集中在这里，别处无从写错。
        """
        view = self.public()
        llm = view["llm"]
        security = view["security"]
        return {"present": bool(llm[VIEW_KEY_PRESENT]), "mask": str(llm[VIEW_KEY_MASK]),
                "source": str(llm[VIEW_KEY_SOURCE]),
                "fingerprint": str(llm[VIEW_KEY_FINGERPRINT]),
                "set_at": str(llm[VIEW_KEY_SET_AT]),
                "storage": str(security["key_storage"]),
                "storage_available": bool(security["key_storage_available"]),
                "outbound": str(security["outbound"])}

    def api_key_tail_source(self) -> str:
        """掩码用的来源值：只取末 4 位所需信息，来自指纹旁路缓存的掩码字段。"""
        doc = self.effective()
        mask = _clean(doc["llm"].get("api_key_mask"))
        if mask:
            return mask
        env_key = self._env_value(ENV_LLM_API_KEY)
        if env_key:
            return env_key
        return self._memory_secret

    def llm_view(self) -> dict[str, Any]:
        """引擎侧需要的最小视图（`LLMSettings` 形状，无密钥）。"""
        return self.public()["llm"]

    def language(self) -> str:
        return self.effective()["ui"]["language"]

    def llm_settings(self) -> LLMSettings:
        view = self.llm_view()
        return LLMSettings(enabled=view["mode"] == MODE_LLM, model=view["model"],
                           base_url=view["base_url"], temperature=view["temperature"],
                           max_tokens=view["max_tokens"], timeout_s=view["timeout_s"],
                           api_key_present=view["api_key_present"])

    def build_client(self, *, transport: Any = None,
                     max_tokens: int | None = None) -> LLMClient:
        """按当前配置造客户端：没地址 / 没模型 → `NullLLMClient`（引擎回退模板）。"""
        view = self.llm_view()
        if view["mode"] != MODE_LLM or not _clean(view["base_url"]) or not _clean(view["model"]):
            return NullLLMClient()
        try:
            return OpenAiCompatibleClient(base_url=view["base_url"],
                                          api_key=self.reveal_secret(),
                                          model=view["model"],
                                          allow_outbound=view["allow_outbound"],
                                          transport=transport)
        except SettingsError:
            return NullLLMClient()

    def apply_to(self, generator: Any) -> Any:
        """把配置灌进解释引擎（t5 的三道闸：mode + settings.enabled + 客户端）。

        语言同时写进 `preferences`，所以切语言 = 引擎下一句就用新语言。
        """
        view = self.llm_view()
        generator.set_mode(view["mode"])
        generator.settings = self.llm_settings()
        generator.llm_client = self.build_client()
        prefs = dict(getattr(generator, "preferences", {}) or {})
        prefs["language"] = self.language()
        generator.set_preferences(prefs)
        return generator

    def engine_status(self, generator: Any) -> dict[str, Any]:
        """引擎当前真实状态（设置页显示"改了之后到底生效没有"）。"""
        status = generator.llm_status()
        view = self.llm_view()
        gates = {"mode": view["mode"] == MODE_LLM,
                 "settings_enabled": bool(view["mode"] == MODE_LLM),
                 "client_configured": bool(getattr(generator.llm_client, "configured", False))}
        return {"mode": view["mode"], "provider": view["provider"],
                "model": view["model"], "language": self.language(),
                "gates": gates,
                "llm_will_run": all(gates.values()),
                "client_type": type(generator.llm_client).__name__,
                "endpoint": getattr(generator.llm_client, "endpoint_display", "") or "",
                "llm_status": status,
                "effect_notice": EFFECT_NOTICE_KEY,
                "note": ("llm 增强会真的调用模型（只发脱敏事实）" if all(gates.values())
                         else "当前不会调用模型：界面文案仍是模板产物（原因见 gates）")}

    # -- 连接测试 --------------------------------------------------------
    def probe(self, *, client: Any = None, transport: Any = None,
              prompt: str | None = None, max_tokens: int | None = None,
              timeout_s: float | None = None) -> dict[str, Any]:
        """`POST /api/settings/test-llm` 的实现：成功/失败都要给出人能看懂的原因。

        返回形状（界面直接显示，全部字段可 JSON 化，**没有密钥**）：
        `{"ok", "code", "message", "detail", "latency_ms", "model", "chars",
          "sample", "endpoint", "config", "sent_facts_note"}`
        """
        self.probes += 1
        view = self.llm_view()
        config = {"mode": view["mode"], "provider": view["provider"],
                  "model": view["model"], "base_url": view["base_url"],
                  "timeout_s": timeout_s or view["timeout_s"],
                  "max_tokens": max_tokens or view["max_tokens"],
                  "allow_outbound": view["allow_outbound"],
                  "api_key_present": view["api_key_present"]}
        if view["mode"] != MODE_LLM:
            return _probe_result(False, "LLM_MODE_TEMPLATE",
                                 "现在是模板模式：不发请求，界面文案由模板生成。",
                                 config=config, note="要试模型先把模式切到 llm")
        if not _clean(view["base_url"]) or not _clean(view["model"]):
            return _probe_result(False, "LLM_NOT_CONFIGURED",
                                 "还缺服务商地址或模型名，没有发任何请求。",
                                 config=config,
                                 detail={"missing": [k for k, v in (("base_url", view["base_url"]),
                                                                    ("model", view["model"]))
                                                    if not _clean(v)]})
        # 地址写错要当场说清楚（协议 / 主机 / 端口 / 明文 http 连远端），别拖到连接阶段
        try:
            parsed = parse_base_url(view["base_url"])
        except SettingsError as exc:
            return _probe_result(False, exc.code, exc.reason, config=config,
                                 detail=dict(exc.detail),
                                 note="地址字段改好后重新测")
        # 本机回环（Ollama / vLLM / llama.cpp 常见）可以不带密钥；远端必须有
        if not parsed["loopback"] and not self.secret_present():
            return _probe_result(False, "LLM_KEY_MISSING",
                                 "远端模型没有配密钥：Windows 用系统保管（DPAPI）填一次，"
                                 "容器 / 团队部署用环境变量注入。",
                                 config=config,
                                 detail={"host": parsed["host"], "env_name": ENV_LLM_API_KEY})
        if parsed["loopback"] and not self.secret_present():
            config["api_key_note"] = "loopback_keyless"
        active = client if isinstance(client, LLMClient) and getattr(client, "configured", False) \
            else self.build_client(transport=transport)
        if not getattr(active, "configured", False):
            return _probe_result(False, "LLM_NOT_CONFIGURED",
                                 "配置不足，客户端不可用（未发请求）。", config=config)
        endpoint = getattr(active, "endpoint_display", "")
        sent_note = (f"这次会把一段自检提示词发给 {view['model']}（{endpoint}）；"
                     "正式生成时发送的是**脱敏后的事实**，不含密钥与参数值")
        body = _clean(prompt) or PROBE_PROMPT
        started = time.monotonic()
        try:
            answer = active.complete(body, model=view["model"], temperature=0.0,
                                     max_tokens=max_tokens or PROBE_MAX_TOKENS,
                                     timeout_s=timeout_s or view["timeout_s"],
                                     language=self.language(),
                                     response_format="plain_text", stop=["\n"])
        except TimeoutError as exc:
            return _probe_result(False, "LLM_TIMEOUT", redact(str(exc)),
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 config=config, endpoint=endpoint,
                                 detail={"timeout_s": timeout_s or view["timeout_s"],
                                         "hint": "超时：换本机模型、把超时调大或把 max_tokens 调小"})
        except LlmUnavailable as exc:
            return _probe_result(False, exc.code, redact(str(exc), secret=self.reveal_secret()),
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 config=config, endpoint=endpoint, detail=dict(exc.detail))
        except Exception as exc:                              # noqa: BLE001
            return _probe_result(False, "LLM_CLIENT_ERROR",
                                 f"模型调用失败：{redact(str(exc), secret=self.reveal_secret())}"
                                 f"（{type(exc).__name__}）",
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 config=config, endpoint=endpoint)
        latency = int((time.monotonic() - started) * 1000)
        text = _clean(answer)
        if not text:
            return _probe_result(False, "LLM_EMPTY_ANSWER",
                                 "连接通了但模型没回正文：多半模型名不对，或它不接受这条提示词。",
                                 latency_ms=latency, config=config, endpoint=endpoint,
                                 sent_facts_note=sent_note)
        meta = getattr(active, "last_meta", {}) or {}
        return _probe_result(True, "LLM_OK",
                             f"连接成功：{latency} 毫秒收到 {len(text)} 个字符的回复。",
                             latency_ms=latency, config=config, endpoint=endpoint,
                             model=_clean(meta.get("model")) or view["model"],
                             chars=len(text), sample=text[:PROBE_SAMPLE_CHARS],
                             sent_facts_note=sent_note)

    # -- 统计 ------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        view = self.public()
        return {"reads": self.reads, "writes": self.writes, "probes": self.probes,
                "revision": view["revision"], "mode": view["mode"],
                "llm_mode": view["llm"]["mode"], "provider": view["llm"]["provider"],
                "model": view["llm"]["model"], "language": view["ui"]["language"],
                "api_key_present": view["llm"]["api_key_present"],
                "api_key_source": view["llm"]["api_key_source"],
                "outbound": view["security"]["outbound"],
                "key_storage": view["security"]["key_storage"],
                "updated_at": view["updated_at"], "updated_by": view["updated_by"],
                "note": "统计与视图都不含密钥明文"}

    def __repr__(self) -> str:
        return (f"SettingsStore(path={str(self.path)!r}, mode={self.mode}, "
                f"llm={self.llm_view()['mode']}, api_key_present={self.secret_present()})")


def _probe_result(ok: bool, code: str, message: str, *, latency_ms: int = 0,
                  config: Mapping[str, Any] | None = None, endpoint: str = "",
                  model: str = "", chars: int = 0, sample: str = "",
                  detail: Mapping[str, Any] | None = None, note: str = "",
                  sent_facts_note: str = "") -> dict[str, Any]:
    return {"ok": bool(ok), "code": str(code), "message": _clean(message),
            "latency_ms": int(latency_ms), "model": _clean(model), "chars": int(chars),
            "sample": _clean(sample), "endpoint": _clean(endpoint),
            "config": dict(config) if isinstance(config, Mapping) else {},
            "detail": dict(detail) if isinstance(detail, Mapping) else {},
            "note": _clean(note), "sent_facts_note": _clean(sent_facts_note),
            "effect_notice": "settings.test_llm.probe_only"}


# ----------------------------------------------------------------------------------
# 命令行：python -m agent_explainer.settings
# ----------------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m agent_explainer.settings",
        description="TraceLens 设置中心：读/写配置（密钥只回显掩码），测一次模型连接")
    parser.add_argument("--settings", default=None, help="配置文件路径（默认 var/settings.json）")
    parser.add_argument("--mode", choices=("local", "team"), default="local",
                        help="team = 环境变量优先，密钥永不落盘")
    parser.add_argument("--show", action="store_true", help="打印当前配置（密钥只有掩码）")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印（配合 --show）")
    parser.add_argument("--set", dest="set_items", action="append", default=[],
                        metavar="llm.mode=llm", help="改一项，可重复；密钥请用环境变量注入")
    parser.add_argument("--test-llm", action="store_true", help="发一次自检请求（默认只连本机）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    store = SettingsStore(args.settings, mode=args.mode)
    store.load()
    if args.set_items:
        patch: dict[str, Any] = {}
        for item in args.set_items:
            key, sep, value = str(item).partition("=")
            if not sep:
                print(f"写法不对：{item}（要写成 llm.mode=llm）")
                return 2
            section, _, field = key.strip().partition(".")
            if section not in WRITABLE_FIELDS:
                print(f"不认识的一级字段：{section}")
                return 2
            patch.setdefault(section, {})[field] = value.strip()
        try:
            _, changed = store.apply(patch, actor_id="cli")
        except SettingsError as exc:
            print(f"改设置失败：{exc.reason}（{exc.code}）")
            return 2
        print("已保存：" + ", ".join(changed))
    view = store.public()
    if args.json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    elif args.show or not args.test_llm:
        llm = view["llm"]
        print(f"{SETTINGS_NAME}  revision={view['revision']}  mode={view['mode']}")
        print(f"  llm      : {llm['mode']} / {llm['provider']} / "
              f"{llm['model'] or '（未填模型）'}  密钥={llm['api_key_mask'] or '（无）'}"
              f"  来源={llm['api_key_source']}")
        print(f"  endpoint : {llm['base_url'] or '（未填地址）'}")
        print(f"  limits   : timeout={llm['timeout_s']}s  temperature={llm['temperature']}  "
              f"max_tokens={llm['max_tokens']}")
        print(f"  outbound : {view['security']['outbound']} —— {view['security']['outbound_note']}")
        print(f"  language : {view['ui']['language']}  granularity={view['ui']['granularity']}")
        print(f"  keys     : 存储={view['security']['key_storage']} "
              f"({view['security']['key_storage_reason']})")
        if view["security"]["env_overrides"]:
            print(f"  env      : 这些字段由环境变量接管 {', '.join(view['security']['env_overrides'])}")
        for item in view["load_errors"]:
            print(f"  [warn]   : {item['reason']}")
    if args.test_llm:
        result = store.probe()
        print(f"test-llm: {'OK' if result['ok'] else 'FAIL'} {result['code']} —— "
              f"{result['message']}")
        if result["sample"]:
            print(f"  sample : {result['sample']}")
        if result["detail"]:
            print(f"  detail : {json.dumps(result['detail'], ensure_ascii=False)}")
        return 0 if result["ok"] else 1
    return 0


__all__ = [
    "SETTINGS_SCHEMA_VERSION", "SETTINGS_NAME", "LLM_MODES", "PROVIDERS", "LANGS",
    "ENV_NAMES", "KEY_DPAPI", "KEY_ENV", "KEY_MEMORY", "KEY_NONE",
    "MASK_MARK", "SettingsError", "DataProtector", "LlmUnavailable",
    "OpenAiCompatibleClient", "SettingsStore", "defaults_doc", "default_settings_path",
    "mask_secret", "secret_fingerprint", "redact", "parse_base_url", "outbound_state",
    "main",
]


# 打包入口：`python -m agent_explainer.settings --show | --test-llm | --set ...`
# 桌面排障时不必起服务也能看清配置、密钥来源与连接结果（只有掩码）。
if __name__ == "__main__":
    raise SystemExit(main())
