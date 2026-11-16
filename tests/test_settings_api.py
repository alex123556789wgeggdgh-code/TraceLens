# -*- coding: utf-8 -*-
"""4.5 设置中心与模型配置（t15）验收测试。

覆盖合同里的每一条：

1. **配置读写与掩码**：出厂默认、落盘、原子写、`updated_by/revision`、
   响应只回显掩码（`••••••` + 末 4 位）+ 指纹 + 来源，明文不进响应、不进审计、不进日志。
2. **密钥保管**：Windows 用 DPAPI（`CryptProtectData`，当前用户绑定）加密后落盘；
   非 Windows 平台只留在内存（绝不写明文）；容器 / 团队模式用环境变量注入且优先于文件。
3. **权限**：`GET /api/settings` 读的人要给 `records.read`；
   `PUT /api/settings` 与 `POST /api/settings/test-llm` 要 `settings.write`
   —— t13 权限表里只有 `local` 与 `admin` 有这一项，所以 **team 模式仅 admin 可改**，
   `editor` / `viewer` 到这就是 403。
4. **test-llm 成功 / 失败 mock**：成功、超时、连不上、401/404/429/5xx、空回复、
   地址非法、默认零出网拦截、模板模式拒发 —— 每条都有人能读懂的 `code` + `message`。
5. **引擎按配置生效**：切到 llm 模式后，服务端解释生成走已配置的引擎
   （`generated_by` 变 `hybrid`、`explain()["llm"].used=true`）；
   切回模板逐字回到模板口径（`llm.reason == "mode_template"`）。
6. **纯净性守门**：`settings.py` 只用标准库（`ctypes` / `http.client` 允许），
   不出现 URL 字面量；`server.py` 不出现密钥字段的蛇形写法、不出现服务商档位名、
   不做任何文件写入；盘上的文件里没有明文密钥。

跑法：`cd tests && python -m unittest test_settings_api -q`
"""
from __future__ import annotations

import ast
import contextlib
import http.client
import io
import json
import os
import pathlib
import secrets
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:   # 裸 `python -m unittest tests.<模块>` 时也能 import scratch
    sys.path.insert(0, str(ROOT / "tests"))

from agent_explainer import audit as au                      # noqa: E402
from agent_explainer import auth as au_auth                  # noqa: E402
from agent_explainer import explanations as ex               # noqa: E402
from agent_explainer import server as sv                     # noqa: E402
import http_fixture as fx                                     # noqa: E402  连接加固（tests/http_fixture.py）
from agent_explainer import settings as st                   # noqa: E402

PKG = ROOT / "agent_explainer"
SAMPLE_PATH = ROOT / "samples" / "action_records.json"
NATIVE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))["records"]
from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

TMP = scratch_dir("settings")

PENDING = "act-demo-0004"
#: 测试用的假凭据：刻意不用真实密钥形态（无 `sk-` 前缀），只用来验证掩码与不落盘
SECRET = "sekret-local-000ABCD"
SECRET_TAIL = "ABCD"
ENV_SECRET = "env-injected-secret-9EFA"
LOOPBACK_URL = "http://127.0.0.1:11434/v1"
REMOTE_URL = "https://api.example-llm.test/v1"
MODEL = "qwen2.5:7b"
ITER = 1000
ADMIN_PW = "Bootstrap-Passphrase-2026"
PW_EDITOR = "Editor-Passphrase-2026"
PW_VIEWER = "Viewer-Passphrase-2026"
#: t5 的四道显示闸会拒绝过度自信的文案；这句是已验证会被接受的形态
MODEL_ANSWER = "模型说：这一步要移动 12 个文件，因为要去重，结果是等你确认，需要你点一下。"

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def folder(name: str) -> pathlib.Path:
    """每个用例一个独立目录：`scratch_dir("settings")/<name>/`。"""
    target = TMP / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def store_path(name: str, filename: str = "settings.json") -> pathlib.Path:
    return folder(name) / filename


def open_store(name: str, **over) -> st.SettingsStore:
    """开一个已加载的存储；默认 `env={}`，避免开发机环境变量把测试带偏。"""
    over.setdefault("env", {})
    store = st.SettingsStore(store_path(name), **over)
    store.load()
    return store


def code_of(data: object) -> str:
    return str(data.get("code")) if isinstance(data, dict) else ""


def detail_of(data: object) -> dict:
    return dict(data.get("detail") or {}) if isinstance(data, dict) else {}


def llm_of(data: object) -> dict:
    return dict(data.get("llm") or {}) if isinstance(data, dict) else {}


def http_request(host: str, port: int, method: str, path: str, body=None,
                 extra_headers: dict | None = None,
                 timeout: float = 8.0) -> tuple[int, dict, object]:
    # M2-PRE 4（PM-VERDICT-R1-9 裁决②）：连接生命周期交给 tests/http_fixture——
    # 端口就绪等待 + 连接级瞬态一次性重试；POST/PUT/PATCH/DELETE 这类非幂等动词
    # 绝不重放（服务端可能已写入），断言级失败绝不重试。请求与解析口径一行不改。
    def once(conn: http.client.HTTPConnection) -> tuple[int, dict, object]:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        headers = {"Connection": "close", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        data: object = raw.decode("utf-8", "replace")
        if "json" in (resp.getheader("Content-Type") or ""):
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


def good_transport(payload: dict) -> dict:
    return {"status": 200,
            "body": {"model": payload.get("model") or MODEL,
                     "choices": [{"message": {"content": MODEL_ANSWER}}]}}


def timeout_transport(payload: dict) -> dict:
    raise TimeoutError("模拟：模型端 30 秒没响应")


def unreachable_transport(payload: dict) -> dict:
    raise OSError(10061, "模拟：目标机器拒绝连接")


def make_transport(status: int, body: object):
    def run(payload: dict) -> dict:
        return {"status": status, "body": body}
    return run


def make_transport_raiser(exc: BaseException):
    def run(payload: dict) -> dict:
        raise exc
    return run


class MockClient(ex.LLMClient):
    """引擎注入用的假客户端：满足 t5 的 `LLMClient` 协议（configured + complete）。"""

    configured = True
    model = "mock-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, prompt: str, **kwargs) -> str | None:
        self.calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        return MODEL_ANSWER


class HttpCase(unittest.TestCase):
    """起一台真服务器（随机端口），跑完停掉。"""

    def start(self, **over) -> sv.TraceLensServer:
        over.setdefault("records_path", SAMPLE_PATH)
        over.setdefault("settings_path", store_path(self.id()))
        over.setdefault("settings_env", {})
        over.setdefault("quiet", True)
        self.server, self.thread = sv.start_background_server("127.0.0.1", 0, **over)
        self.addCleanup(self.stop_server)
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.service = self.server.service
        return self.server

    def stop_server(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:                                   # noqa: BLE001
            pass
        self.thread.join(timeout=5)

    def req(self, method: str, path: str, body=None, headers=None):
        return http_request(self.host, self.port, method, path, body, headers)

    def sign_in(self, username: str, password: str) -> dict:
        status, headers, data = self.req("POST", "/api/auth/login",
                                         {"username": username, "password": password})
        self.assertEqual(status, 200, data)
        body = data if isinstance(data, dict) else {}
        cookie = (body.get("cookie") or {}).get("value") or ""
        return {"cookie": f"{au_auth.SESSION_COOKIE}={cookie}",
                "csrf": str(body.get("csrf_token") or ""),
                "role": str((body.get("user") or {}).get("role") or ""),
                "permissions": list((body.get("user") or {}).get("permissions") or [])}

    def as_user(self, session: dict, csrf: bool = True) -> dict:
        headers = {"Cookie": session["cookie"]}
        if csrf and session.get("csrf"):
            headers[au_auth.CSRF_HEADER] = session["csrf"]
        return headers

    def audit_entries(self) -> list[dict]:
        return list(self.service.audit.entries())

    def settings_audit(self) -> list[dict]:
        return [e for e in self.audit_entries()
                if str(e.get("settings_event") or "").startswith("settings.")]


# --------------------------------------------------------------------------- #
# 1. 出厂默认与视图形状
# --------------------------------------------------------------------------- #

class DefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = open_store("defaults")

    def test_factory_defaults_are_template_and_blocked(self) -> None:
        view = self.store.public()
        self.assertEqual(view["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertEqual(view["llm"]["provider"], st.PROVIDER_DEFAULT)
        self.assertEqual(view["llm"]["timeout_s"], st.PROBE_TIMEOUT_S)
        self.assertFalse(view["llm"]["allow_outbound"])
        self.assertFalse(view["llm"]["api_key_present"])
        self.assertEqual(view["llm"]["api_key_source"], st.KEY_NONE)
        self.assertEqual(view["ui"]["language"], st.LANGUAGE_DEFAULT)
        self.assertEqual(view["security"]["outbound"], st.OUTBOUND_BLOCKED)
        self.assertEqual(view["mode"], "local")
        self.assertEqual(view["revision"], 0)
        self.assertEqual(view["load_errors"], [])

    def test_view_shape_is_stable_for_the_ui(self) -> None:
        view = self.store.public()
        for section in ("llm", "ui", "data", "security"):
            self.assertIn(section, view)
        for key in ("mode", "provider", "base_url", "model", "timeout_s", "temperature",
                    "max_tokens", "allow_outbound", "api_key_present", "api_key_mask",
                    "api_key_fingerprint", "api_key_source", "enabled"):
            self.assertIn(key, view["llm"])
        for key in ("key_storage", "key_storage_available", "secret_in_file", "outbound",
                    "env_overrides", "env_names", "mask", "effect_notice",
                    "file_mode_note", "outbound_note"):
            self.assertIn(key, view["security"])
        self.assertEqual(view["security"]["secret_in_file"], False)
        self.assertEqual(view["security"]["mask"], st.MASK_MARK)
        self.assertEqual(view["ui"]["storage_key"], st.UI_LANGUAGE_STORAGE_KEY)
        self.assertEqual(view["schema_version"], st.SETTINGS_SCHEMA_VERSION)
        self.assertEqual(view["data"]["actions"], list(st.DATA_ACTIONS))

    def test_view_is_json_serialisable_and_carries_no_secret(self) -> None:
        store = open_store("defaults-secret")
        store.apply({"llm": {"mode": "llm", "base_url": LOOPBACK_URL, "model": MODEL,
                             "apiKey": SECRET}}, actor_id="local-user")
        blob = json.dumps(store.public(), ensure_ascii=False)
        self.assertNotIn(SECRET, blob)
        self.assertIn(st.MASK_MARK, blob)
        self.assertIn(SECRET_TAIL, json.loads(blob)["llm"]["api_key_mask"])

    def test_defaults_doc_is_a_fresh_copy(self) -> None:
        first = st.defaults_doc()
        first["llm"]["mode"] = st.MODE_LLM
        first["llm"]["api_key_blob"] = "tampered"
        second = st.defaults_doc()
        self.assertEqual(second["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertEqual(second["llm"]["api_key_blob"], "")

    def test_repr_and_stats_leak_nothing(self) -> None:
        store = open_store("defaults-repr")
        store.apply({"llm": {"mode": "llm", "base_url": LOOPBACK_URL, "model": MODEL,
                             "apiKey": SECRET}}, actor_id="local-user")
        self.assertNotIn(SECRET, repr(store))
        self.assertNotIn(SECRET, json.dumps(store.stats(), ensure_ascii=False))
        self.assertTrue(store.stats()["api_key_present"])
        self.assertEqual(store.stats()["llm_mode"], st.MODE_LLM)


# --------------------------------------------------------------------------- #
# 2. 写入、落盘、掩码
# --------------------------------------------------------------------------- #

class PersistenceTests(unittest.TestCase):
    def test_apply_persists_and_bumps_revision(self) -> None:
        store = open_store("persist")
        view, changed = store.apply({"llm": {"mode": "llm", "base_url": LOOPBACK_URL,
                                             "model": MODEL}}, actor_id="ops-ann")
        self.assertEqual(changed, ["llm.mode", "llm.base_url", "llm.model"])
        self.assertEqual(view["llm"]["mode"], st.MODE_LLM)
        self.assertEqual(view["revision"], 1)
        self.assertEqual(view["updated_by"], "ops-ann")
        self.assertTrue(view["updated_at"])
        disk = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["llm"]["mode"], st.MODE_LLM)
        self.assertEqual(disk["updated_by"], "ops-ann")
        self.assertEqual(disk["schema_version"], st.SETTINGS_SCHEMA_VERSION)
        # 重新加载一次，配置还在（重启不丢）
        again = st.SettingsStore(store.path, env={})
        again.load()
        self.assertEqual(again.llm_view()["model"], MODEL)

    def test_base_url_is_normalised_to_display_form(self) -> None:
        store = open_store("persist-url")
        store.apply({"llm": {"mode": "llm", "base_url": "http://LOCALHOST:11434/v1/",
                             "model": MODEL}}, actor_id="a")
        self.assertIn("localhost:11434/v1", store.llm_view()["base_url"])

    def test_file_contains_no_plaintext_secret(self) -> None:
        store = open_store("persist-secret")
        store.apply({"llm": {"mode": "llm", "base_url": REMOTE_URL, "model": MODEL,
                             "apiKey": SECRET, "allow_outbound": True}}, actor_id="a")
        disk = store.path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, disk)
        doc = json.loads(disk)["llm"]
        if IS_WINDOWS:
            self.assertTrue(doc["api_key_blob"], "Windows 必须有 DPAPI 密文")
            self.assertNotIn(SECRET, doc["api_key_blob"])
            self.assertEqual(doc["api_key_source"], st.KEY_DPAPI)
        else:
            self.assertEqual(doc["api_key_blob"], "", "非 Windows 不许把密钥写进文件")
            self.assertEqual(doc["api_key_source"], st.KEY_MEMORY)
        self.assertEqual(store.reveal_secret(), SECRET)
        self.assertEqual(store.public()["llm"]["api_key_mask"], st.MASK_MARK + SECRET_TAIL)

    @unittest.skipUnless(IS_WINDOWS, "DPAPI 只在 Windows 上可用")
    def test_dpapi_blob_survives_reload(self) -> None:
        store = open_store("dpapi-reload")
        store.apply({"llm": {"mode": "llm", "base_url": REMOTE_URL, "model": MODEL,
                             "apiKey": SECRET, "allow_outbound": True}}, actor_id="a")
        reloaded = st.SettingsStore(store.path, env={})
        reloaded.load()
        self.assertEqual(reloaded.reveal_secret(), SECRET)
        self.assertEqual(reloaded.secret_source(), st.KEY_DPAPI)

    def test_blob_from_another_platform_is_not_a_secret(self) -> None:
        """文件里带着解不开的密文（换机器 / 换平台）：说"没有密钥"，绝不猜明文。"""
        path = store_path("dpapi-foreign")
        path.write_text(json.dumps({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL,
                                            "model": MODEL, "api_key_blob": "Zm9vYmFy",
                                            "api_key_mask": st.MASK_MARK + "bbbb",
                                            "api_key_source": st.KEY_DPAPI,
                                            "allow_outbound": True}}, ensure_ascii=False),
                        encoding="utf-8")
        store = st.SettingsStore(path, env={}, protector=st.DataProtector(force_disabled=True))
        store.load()
        self.assertEqual(store.reveal_secret(), "")
        self.assertFalse(store.public()["llm"]["api_key_present"])
        self.assertEqual(store.secret_source(), st.KEY_NONE)

    def test_keep_and_clear_sentinels(self) -> None:
        store = open_store("sentinel")
        store.apply({"llm": {"mode": "llm", "base_url": LOOPBACK_URL, "model": MODEL,
                             "apiKey": SECRET}}, actor_id="a")
        self.assertTrue(store.secret_present())
        _, changed = store.apply({"llm": {"apiKey": st.KEEP_SECRET, "temperature": 0.7}},
                                 actor_id="a")
        self.assertEqual(changed, ["llm.temperature"])
        self.assertEqual(store.reveal_secret(), SECRET)
        # 哨兵：显式说"这次不动凭据"，且没有任何其他改动 → 按"无变更"拒收（不落盘、不改版本）
        with self.assertRaises(st.SettingsError) as ctx:
            store.apply({"llm": {"apiKey": None}}, actor_id="a")
        self.assertEqual(ctx.exception.code, "SETTINGS_NO_CHANGES")
        self.assertEqual(store.reveal_secret(), SECRET)
        _, changed = store.apply({"llm": {"apiKey": ""}}, actor_id="a")
        self.assertEqual(changed, ["llm.apiKey"])
        self.assertEqual(store.reveal_secret(), "")
        self.assertFalse(store.public()["llm"]["api_key_present"])
        self.assertEqual(store.public()["llm"]["api_key_mask"], "")

    def test_writable_false_keeps_file_untouched(self) -> None:
        path = store_path("readonly")
        store = st.SettingsStore(path, env={}, writable=False)
        store.load()
        store.apply({"ui": {"language": "en"}}, actor_id="a")
        self.assertFalse(path.exists(), "只读模式不许创建/写配置文件")
        self.assertEqual(store.language(), "en")
        self.assertEqual(store.public()["mode"], "local")

    @unittest.skipIf(IS_WINDOWS, "POSIX 位权限只在类 Unix 文件系统上有意义")
    def test_file_mode_is_owner_only_on_posix(self) -> None:
        store = open_store("mode-posix")
        store.apply({"llm": {"mode": "llm", "base_url": LOOPBACK_URL, "model": MODEL}},
                    actor_id="a")
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_missing_parent_directory_is_created(self) -> None:
        """首启时目录还不存在：要能建目录并写进去（Windows 上踩过坑，见 settings._write）。"""
        deep = folder("deep-" + secrets.token_hex(3)) / "var" / "nested" / "settings.json"
        store = st.SettingsStore(deep, env={})
        store.load()
        store.apply({"ui": {"language": "en"}}, actor_id="a")
        self.assertTrue(deep.exists())
        self.assertEqual(store.language(), "en")
        again = st.SettingsStore(deep, env={})
        again.load()
        self.assertEqual(again.language(), "en")

    def test_broken_file_is_reported_not_overwritten(self) -> None:
        path = store_path("broken")
        original = "{ this is not json"
        path.write_text(original, encoding="utf-8")
        store = st.SettingsStore(path, env={})
        store.load()
        self.assertEqual(store.llm_view()["mode"], st.MODE_TEMPLATE)
        codes = [e["code"] for e in store.load_errors]
        self.assertIn("SETTINGS_FILE_INVALID", codes)
        self.assertTrue(store.load_errors[0]["path"].endswith("settings.json"))
        self.assertEqual(path.read_text(encoding="utf-8"), original, "坏文件必须原样留着")

    def test_top_level_must_be_object(self) -> None:
        path = store_path("broken-array")
        path.write_text("[]", encoding="utf-8")
        store = st.SettingsStore(path, env={})
        store.load()
        self.assertIn("SETTINGS_FILE_INVALID", [e["code"] for e in store.load_errors])

    def test_out_of_range_values_in_existing_file_are_clamped(self) -> None:
        path = store_path("clamp")
        path.write_text(json.dumps({"llm": {"mode": "wizard", "provider": "nope",
                                            "timeout_s": 9999, "temperature": 99,
                                            "max_tokens": 1, "allow_outbound": "yes"},
                                    "ui": {"language": "fr", "granularity": "L9"}}),
                        encoding="utf-8")
        store = st.SettingsStore(path, env={})
        store.load()
        view = store.public()
        self.assertEqual(view["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertEqual(view["llm"]["provider"], st.PROVIDER_DEFAULT)
        self.assertLessEqual(view["llm"]["timeout_s"], st.TIMEOUT_MAX)
        self.assertLessEqual(view["llm"]["temperature"], st.TEMPERATURE_MAX)
        self.assertGreaterEqual(view["llm"]["max_tokens"], st.MAX_TOKENS_MIN)
        self.assertEqual(view["ui"]["language"], st.LANGUAGE_DEFAULT)
        self.assertEqual(view["ui"]["granularity"], "L1")
        self.assertTrue(view["llm"]["allow_outbound"])


# --------------------------------------------------------------------------- #
# 3. 校验：非法值必须给可读原因
# --------------------------------------------------------------------------- #

class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = open_store("validation")

    def apply_code(self, patch: dict) -> str:
        try:
            self.store.apply(patch, actor_id="a")
        except st.SettingsError as exc:
            return exc.code
        return "NO_ERROR"

    def test_mode_provider_language_granularity_enums(self) -> None:
        cases = [
            ({"llm": {"mode": "gpt"}}, "SETTINGS_MODE_INVALID"),
            ({"llm": {"mode": ""}}, "SETTINGS_MODE_INVALID"),
            ({"llm": {"provider": "chatgpt"}}, "SETTINGS_PROVIDER_INVALID"),
            ({"ui": {"language": "fr"}}, "SETTINGS_LANGUAGE_INVALID"),
            ({"ui": {"granularity": "L4"}}, "SETTINGS_GRANULARITY_INVALID"),
        ]
        for patch, want in cases:
            self.assertEqual(self.apply_code(patch), want, patch)

    def test_unknown_section_and_field_are_named_back(self) -> None:
        body = st.SettingsError("x", "y")
        self.assertIsNotNone(body)
        self.assertEqual(self.apply_code({"model": "gpt-4"}), "SETTINGS_SECTION_UNKNOWN")
        self.assertEqual(self.apply_code({"llm": {"apiKey2": "x"}}), "SETTINGS_FIELD_UNKNOWN")
        self.assertEqual(self.apply_code({"ui": {"theme": "dark"}}), "SETTINGS_FIELD_UNKNOWN")
        self.assertEqual(self.apply_code("nope"), "SETTINGS_BODY_NOT_OBJECT")
        self.assertEqual(self.apply_code({}), "SETTINGS_BODY_EMPTY")
        self.assertEqual(self.apply_code({"llm": []}), "SETTINGS_FIELD_TYPE")
        self.assertEqual(self.apply_code({"llm": {"apiKey": 12345}}), "SETTINGS_FIELD_TYPE")

    def test_ranges_are_bounded(self) -> None:
        cases = [
            ({"llm": {"timeout_s": 0.1}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"timeout_s": 900}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"temperature": -1}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"temperature": 2.5}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"max_tokens": 9}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"max_tokens": 99999}}, "SETTINGS_FIELD_RANGE"),
        ]
        for patch, want in cases:
            self.assertEqual(self.apply_code(patch), want, patch)

    def test_switching_to_llm_needs_address_and_model(self) -> None:
        self.assertEqual(self.apply_code({"llm": {"mode": st.MODE_LLM}}),
                         "SETTINGS_FIELD_MISSING")
        self.assertEqual(self.apply_code({"llm": {"mode": st.MODE_LLM,
                                                  "base_url": LOOPBACK_URL}}),
                         "SETTINGS_FIELD_MISSING")
        # 模板模式可以先存着空地址（保存草稿），不报错
        self.assertEqual(self.apply_code({"llm": {"mode": st.MODE_TEMPLATE,
                                                  "base_url": LOOPBACK_URL}}), "NO_ERROR")

    def test_url_field_rejects_junk(self) -> None:
        cases = [
            ("ftp://127.0.0.1/v1", "SETTINGS_URL_SCHEME"),
            ("127.0.0.1:11434", "SETTINGS_URL_MALFORMED"),
            ("https://", "SETTINGS_URL_MALFORMED"),
            ("https://h/v1 space", "SETTINGS_URL_MALFORMED"),
            ("http://203.0.113.9/v1", "SETTINGS_URL_INSECURE"),
            ("https://user:pw@host/v1", "SETTINGS_URL_USERINFO"),
            ("https://host/v1?key=value", "SETTINGS_URL_QUERY"),
            ("https://host/v1#frag", "SETTINGS_URL_FRAGMENT"),
            ("https://host:0/v1", "SETTINGS_URL_PORT"),
            ("https://host:notaport/v1", "SETTINGS_URL_PORT"),
            ("https://host:70000/v1", "SETTINGS_URL_PORT"),
            ("https://[::1/v1", "SETTINGS_URL_MALFORMED"),
            # 超长在切分之前就拦下：先报"太长"，别把长度问题说成格式问题
            ("h" * 400, "SETTINGS_FIELD_TOO_LONG"),
        ]
        for raw, want in cases:
            with self.assertRaises(st.SettingsError) as ctx:
                st.parse_base_url(raw)
            self.assertEqual(ctx.exception.code, want, raw)
            self.assertEqual(ctx.exception.field, "base_url")

    def test_long_values_are_capped_with_a_reason(self) -> None:
        self.assertEqual(self.apply_code({"llm": {"model": "m" * 300}}),
                         "SETTINGS_FIELD_TOO_LONG")
        self.assertEqual(self.apply_code({"llm": {"apiKey": "k" * 500}}),
                         "SETTINGS_FIELD_TOO_LONG")
        with self.assertRaises(st.SettingsError) as ctx:
            st.parse_base_url("https://" + ("h" * 400))
        self.assertEqual(ctx.exception.code, "SETTINGS_FIELD_TOO_LONG")

    def test_no_change_is_reported_not_silently_ok(self) -> None:
        store = open_store("validation-noop")
        with self.assertRaises(st.SettingsError) as ctx:
            store.apply({"ui": {"language": st.LANGUAGE_DEFAULT}}, actor_id="a")
        self.assertEqual(ctx.exception.code, "SETTINGS_NO_CHANGES")

    def test_error_shape_matches_the_api_contract(self) -> None:
        err = st.SettingsError("SETTINGS_MODE_INVALID", "看不懂这个模式", field="llm.mode",
                               hint="改成 llm 或 template", status=400)
        body = err.as_dict("req-1")
        self.assertEqual(body["code"], "SETTINGS_MODE_INVALID")
        self.assertEqual(body["message"], "看不懂这个模式")
        self.assertEqual(body["detail"]["field"], "llm.mode")
        self.assertEqual(body["detail"]["hint"], "改成 llm 或 template")
        self.assertEqual(body["request_id"], "req-1")
        self.assertNotIn("value", body)
        self.assertIn("SETTINGS_MODE_INVALID", repr(err))


# --------------------------------------------------------------------------- #
# 4. base_url 切分 / 掩码 / 指纹 / 脱敏 / 出网姿态
# --------------------------------------------------------------------------- #

class UrlAndMaskTests(unittest.TestCase):
    def test_parse_base_url_shapes(self) -> None:
        parsed = st.parse_base_url("https://model.example.test:8443/v1")
        self.assertEqual(parsed["scheme"], "https")
        self.assertEqual(parsed["host"], "model.example.test")
        self.assertEqual(parsed["port"], 8443)
        self.assertEqual(parsed["prefix"], "/v1")
        self.assertFalse(parsed["loopback"])
        self.assertEqual(parsed["display"], "https://model.example.test:8443/v1")
        self.assertEqual(st.parse_base_url("https://host")["prefix"], "")
        self.assertEqual(st.parse_base_url("https://host")["port"], st.DEFAULT_PORT_SECURE)
        self.assertEqual(st.parse_base_url("http://127.0.0.1:11434")["port"], 11434)
        loopback6 = st.parse_base_url("http://[::1]:11434/v1")
        self.assertEqual(loopback6["host"], "::1")        # 连接用不带方括号
        self.assertEqual(loopback6["display"], "http://[::1]:11434/v1")   # 显示要带
        self.assertTrue(loopback6["loopback"])
        self.assertEqual(st.parse_base_url("HTTP://LOCALHOST/v1")["host"], "localhost")
        self.assertTrue(st.parse_base_url("http://localhost/v1")["loopback"])
        self.assertTrue(st.parse_base_url("http://LOCALHOST:11434/v1")["loopback"])
        self.assertEqual(st.parse_base_url("HTTPS://Host/V1")["scheme"], "https")
        self.assertEqual(st.parse_base_url("https://Host:8443/V1")["host"], "host")

    def test_display_never_carries_credentials(self) -> None:
        parsed = st.parse_base_url("https://host/v1")
        self.assertNotIn("@", parsed["display"])
        self.assertNotIn("key", parsed["display"].lower())

    def test_mask_secret_forms(self) -> None:
        self.assertEqual(st.mask_secret(""), "")
        self.assertEqual(st.mask_secret(None), "")
        self.assertEqual(st.mask_secret("  "), "")
        self.assertEqual(st.mask_secret("abcd"), st.MASK_MARK)
        self.assertEqual(st.mask_secret("abcdef1234"), st.MASK_MARK + "1234")
        self.assertEqual(st.mask_secret(st.mask_secret("abcdef1234")), st.MASK_MARK + "1234")

    def test_fingerprint_is_short_and_one_way(self) -> None:
        first = st.secret_fingerprint(SECRET)
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(len(first), len("sha256:") + st.FINGERPRINT_CHARS)
        self.assertEqual(first, st.secret_fingerprint(SECRET))
        self.assertNotEqual(first, st.secret_fingerprint(SECRET + "x"))
        self.assertNotIn(SECRET, first)
        self.assertEqual(st.secret_fingerprint(""), "")

    def test_redact_scrubs_secret_and_marker_words(self) -> None:
        message = f"401 unauthorized for key {SECRET} at host"
        cleaned = st.redact(message, secret=SECRET)
        self.assertNotIn(SECRET, cleaned)
        self.assertIn(st.MASK_MARK, cleaned)
        self.assertIn("<redacted:", st.redact("bad Authorization header"))
        self.assertIn("<redacted:", st.redact("password=letmein"))
        self.assertEqual(st.redact("普通中文说明"), "普通中文说明")

    def test_outbound_state_values(self) -> None:
        doc = st.defaults_doc()
        self.assertEqual(st.outbound_state(doc), st.OUTBOUND_BLOCKED)
        doc["llm"]["allow_outbound"] = True
        doc["llm"]["base_url"] = LOOPBACK_URL
        self.assertEqual(st.outbound_state(doc), st.OUTBOUND_LOOPBACK)
        doc["llm"]["base_url"] = REMOTE_URL
        self.assertEqual(st.outbound_state(doc), st.OUTBOUND_ALLOWED)
        doc["llm"]["base_url"] = "ftp://nope"
        self.assertEqual(st.outbound_state(doc), st.OUTBOUND_ALLOWED)

    def test_env_override_names_are_reported(self) -> None:
        store = st.SettingsStore(store_path("env-names"), env={st.ENV_LLM_MODEL: "x",
                                                              st.ENV_UI_LANGUAGE: "en"})
        store.load()
        self.assertEqual(store.env_overrides(), [st.ENV_LLM_MODEL, st.ENV_UI_LANGUAGE])
        self.assertEqual(store.public()["security"]["env_overrides"],
                         [st.ENV_LLM_MODEL, st.ENV_UI_LANGUAGE])


# --------------------------------------------------------------------------- #
# 5. 环境变量注入（容器 / 团队部署）
# --------------------------------------------------------------------------- #

class EnvInjectionTests(unittest.TestCase):
    def test_env_overrides_win_over_the_file(self) -> None:
        store = open_store("env-win")
        store.apply({"llm": {"mode": st.MODE_LLM, "provider": "ollama",
                             "base_url": LOOPBACK_URL, "model": MODEL,
                             "timeout_s": 3.0},
                     "ui": {"language": "zh"}}, actor_id="a")
        env = {st.ENV_LLM_MODE: st.MODE_TEMPLATE, st.ENV_LLM_PROVIDER: "deepseek",
               st.ENV_LLM_BASE_URL: REMOTE_URL, st.ENV_LLM_MODEL: "deepseek-chat",
               st.ENV_LLM_LANGUAGE: "en", st.ENV_LLM_TIMEOUT_S: "12",
               st.ENV_LLM_ALLOW_OUTBOUND: "true"}
        injected = st.SettingsStore(store.path, env=env)
        injected.load()
        view = injected.public()
        self.assertEqual(view["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertEqual(view["llm"]["provider"], "deepseek")
        self.assertEqual(view["llm"]["base_url"], REMOTE_URL)
        self.assertEqual(view["llm"]["model"], "deepseek-chat")
        self.assertEqual(view["llm"]["timeout_s"], 12.0)
        self.assertTrue(view["llm"]["allow_outbound"])
        self.assertEqual(view["ui"]["language"], "en")
        self.assertIn(st.ENV_LLM_MODE, view["security"]["env_overrides"])

    def test_invalid_env_values_are_ignored_not_fatal(self) -> None:
        store = st.SettingsStore(store_path("env-bad"), env={st.ENV_LLM_MODE: "wizard",
                                                             st.ENV_UI_LANGUAGE: "fr",
                                                             st.ENV_LLM_TIMEOUT_S: "abc"})
        store.load()
        view = store.public()
        self.assertEqual(view["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertEqual(view["ui"]["language"], st.LANGUAGE_DEFAULT)
        self.assertEqual(view["llm"]["timeout_s"], st.PROBE_TIMEOUT_S)

    def test_env_secret_wins_and_never_lands_on_disk(self) -> None:
        path = store_path("env-secret")
        store = st.SettingsStore(path, env={st.ENV_LLM_API_KEY: ENV_SECRET})
        store.load()
        self.assertEqual(store.reveal_secret(), ENV_SECRET)
        self.assertEqual(store.secret_source(), st.KEY_ENV)
        view = store.public()
        self.assertTrue(view["llm"]["api_key_present"])
        self.assertEqual(view["llm"]["api_key_mask"],
                         st.MASK_MARK + ENV_SECRET[-st.MASK_TAIL:])
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL,
                             "model": "deepseek-chat", "allow_outbound": True}},
                    actor_id="ops")
        self.assertNotIn(ENV_SECRET, path.read_text(encoding="utf-8"))
        disk = json.loads(path.read_text(encoding="utf-8"))["llm"]
        self.assertEqual(disk["api_key_blob"], "")
        self.assertEqual(store.reveal_secret(), ENV_SECRET)

    def test_team_mode_reports_env_as_the_key_source(self) -> None:
        store = st.SettingsStore(store_path("env-team"), mode="team",
                                 env={st.ENV_LLM_API_KEY: ENV_SECRET})
        store.load()
        view = store.public()
        self.assertEqual(view["mode"], "team")
        self.assertEqual(view["llm"]["api_key_source"], st.KEY_ENV)
        self.assertIn(st.ENV_LLM_API_KEY, view["security"]["env_overrides"])
        self.assertEqual(store.effective()["_env_taken"].count(st.ENV_LLM_API_KEY), 1)

    def test_key_names_are_the_documented_env_surface(self) -> None:
        self.assertEqual(
            set(st.ENV_NAMES),
            {st.ENV_LLM_MODE, st.ENV_LLM_PROVIDER, st.ENV_LLM_BASE_URL, st.ENV_LLM_MODEL,
             st.ENV_LLM_API_KEY, st.ENV_LLM_LANGUAGE, st.ENV_LLM_ALLOW_OUTBOUND,
             st.ENV_LLM_TIMEOUT_S, st.ENV_UI_LANGUAGE})
        for name in st.ENV_NAMES:
            self.assertTrue(name.startswith(st.ENV_PREFIX))
        self.assertEqual(st.ENV_LLM_API_KEY, "TRACELENS_LLM_API_KEY")


# --------------------------------------------------------------------------- #
# 6. DPAPI 本体（Windows）
# --------------------------------------------------------------------------- #

class DataProtectorTests(unittest.TestCase):
    def test_force_disabled_is_unavailable(self) -> None:
        protector = st.DataProtector(force_disabled=True)
        self.assertFalse(protector.available)
        self.assertEqual(protector.backend, st.KEY_NONE)
        self.assertIsNone(protector.protect("x"))
        self.assertIsNone(protector.unprotect("Zm9vYmFy"))
        self.assertTrue(protector.reason)

    def test_non_windows_reports_a_reason(self) -> None:
        protector = st.DataProtector()
        if IS_WINDOWS:
            self.assertTrue(protector.available)
            self.assertEqual(protector.backend, st.KEY_DPAPI)
        else:
            self.assertFalse(protector.available)
            self.assertIn(protector.backend, (st.KEY_NONE,))
            self.assertTrue(protector.reason)

    @unittest.skipUnless(IS_WINDOWS, "DPAPI 只在 Windows 上可用")
    def test_dpapi_roundtrip_and_entropy(self) -> None:
        protector = st.DataProtector()
        for text in (SECRET, "短文本长度检查", "a" * 400, "带 空格 与 ✅ 的长文本"):
            blob = protector.protect(text)
            self.assertTrue(blob)
            # 单字符的明文可能巧合出现在密文头里（长度 < 4 无统计意义），只检查长文本
            self.assertNotIn(text.encode("utf-8"), blob or b"")
            self.assertEqual(protector.unprotect(st.DataProtector.encode(blob)), text)
        for short in ("x", "短"):
            blob = protector.protect(short)
            self.assertEqual(protector.unprotect(st.DataProtector.encode(blob)), short)
        # 换一份熵（别的程序在同一用户下）解不出来：这里用"不可解的输入"验证不崩
        self.assertIsNone(protector.unprotect("###not-base64###"))
        self.assertIsNone(protector.unprotect(""))
        self.assertIsNone(protector.unprotect(None))
        for i in range(60):
            token = f"loop-{i}-secret"
            self.assertEqual(protector.unprotect(st.DataProtector.encode(
                protector.protect(token))), token)


# --------------------------------------------------------------------------- #
# 7. 连接测试（test-llm）：成功与各种失败都要能读懂
# --------------------------------------------------------------------------- #

class ProbeTests(unittest.TestCase):
    def configured_store(self, name: str, **over) -> st.SettingsStore:
        store = open_store(name, **over)
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        return store

    def test_template_mode_refuses_to_send(self) -> None:
        store = open_store("probe-template")
        result = store.probe(transport=good_transport)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "LLM_MODE_TEMPLATE")
        self.assertIn("模板", result["message"])
        self.assertEqual(result["latency_ms"], 0)

    def test_missing_address_or_model(self) -> None:
        """配置不全时不发消息；说清缺哪一项（保存被闸门拦住，所以直接写文件模拟坏配置）。"""
        path = store_path("probe-incomplete")
        path.write_text(json.dumps({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL}}),
                        encoding="utf-8")
        store = st.SettingsStore(path, env={})
        store.load()
        result = store.probe(transport=good_transport)
        self.assertEqual(result["code"], "LLM_NOT_CONFIGURED")
        self.assertEqual(result["detail"]["missing"], ["model"])
        empty = store_path("probe-empty-url")
        empty.write_text(json.dumps({"llm": {"mode": st.MODE_LLM, "model": MODEL}}),
                         encoding="utf-8")
        other = st.SettingsStore(empty, env={})
        other.load()
        self.assertEqual(other.probe(transport=good_transport)["detail"]["missing"], ["base_url"])

    def test_junk_url_is_reported_before_any_connection(self) -> None:
        store = open_store("probe-junk-url")
        doc = st.defaults_doc()
        doc["llm"]["mode"] = st.MODE_LLM
        doc["llm"]["base_url"] = "http://203.0.113.9/v1"
        doc["llm"]["model"] = MODEL
        path = store.path
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        broken = st.SettingsStore(path, env={})
        broken.load()
        result = broken.probe(transport=good_transport)
        self.assertEqual(result["code"], "SETTINGS_URL_INSECURE")
        self.assertTrue(result["message"])

    def test_remote_without_secret_is_key_missing(self) -> None:
        store = open_store("probe-key")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                             "allow_outbound": True}}, actor_id="a")
        result = store.probe(transport=good_transport)
        self.assertEqual(result["code"], "LLM_KEY_MISSING")
        self.assertEqual(result["detail"]["env_name"], st.ENV_LLM_API_KEY)

    def test_loopback_without_secret_is_allowed(self) -> None:
        store = self.configured_store("probe-loopback-keyless")
        result = store.probe(transport=good_transport)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["config"].get("api_key_note"), "loopback_keyless")

    def test_success_shape(self) -> None:
        store = self.configured_store("probe-ok")
        result = store.probe(transport=good_transport)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], "LLM_OK")
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(result["chars"], len(MODEL_ANSWER))
        self.assertEqual(result["sample"], MODEL_ANSWER[:st.PROBE_SAMPLE_CHARS])
        self.assertIn("毫秒", result["message"])
        self.assertTrue(result["endpoint"].endswith(st.CHAT_COMPLETIONS_PATH))
        self.assertIn("脱敏", result["sent_facts_note"])
        self.assertEqual(result["effect_notice"], "settings.test_llm.probe_only")
        self.assertLessEqual(result["latency_ms"], 5000)

    def test_timeout_is_readable(self) -> None:
        store = self.configured_store("probe-timeout")
        result = store.probe(transport=timeout_transport, timeout_s=1.5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "LLM_TIMEOUT")
        self.assertIn("超时", result["message"])
        self.assertEqual(result["detail"]["timeout_s"], 1.5)
        self.assertIn("hint", result["detail"])

    def test_unreachable_is_readable(self) -> None:
        store = self.configured_store("probe-unreachable")
        result = store.probe(transport=unreachable_transport)
        self.assertEqual(result["code"], "LLM_UNREACHABLE")
        self.assertIn("连不上", result["message"])
        self.assertIn("hint", result["detail"])

    def test_http_status_codes_map_to_reasons(self) -> None:
        cases = [
            (401, "LLM_KEY_REJECTED"),
            (403, "LLM_KEY_REJECTED"),
            (404, "LLM_PATH_NOT_FOUND"),
            (429, "LLM_RATE_LIMITED"),
            (500, "LLM_HTTP_ERROR"),
        ]
        for index, (status, want) in enumerate(cases):
            store = self.configured_store(f"probe-http-{index}")
            result = store.probe(transport=make_transport(status, {"error": {"message": "no"}}))
            self.assertEqual(result["code"], want, status)
            self.assertFalse(result["ok"])
            self.assertTrue(result["message"])

    def test_http_error_body_is_redacted(self) -> None:
        store = self.configured_store("probe-redact")
        store.apply({"llm": {"apiKey": SECRET}}, actor_id="a")
        result = store.probe(transport=make_transport(
            500, {"error": {"message": f"upstream rejected key {SECRET}"}}))
        self.assertEqual(result["code"], "LLM_HTTP_ERROR")
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(SECRET, blob, f"探针结果泄露了密钥：{blob}")

    def test_empty_answer_is_reported(self) -> None:
        store = self.configured_store("probe-empty")
        result = store.probe(transport=make_transport(200, {"choices": [{"message": {
            "content": ""}}]}))
        self.assertEqual(result["code"], "LLM_EMPTY_ANSWER")
        self.assertFalse(result["ok"])

    def test_unexpected_client_exception_is_contained(self) -> None:
        store = self.configured_store("probe-raise")
        result = store.probe(transport=make_transport_raiser(RuntimeError("炸了")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "LLM_CLIENT_ERROR")
        self.assertIn("炸了", result["message"])

    def test_default_zero_outbound_blocks_remote(self) -> None:
        store = open_store("probe-outbound")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                             "apiKey": SECRET}}, actor_id="a")
        result = store.probe(transport=good_transport)
        self.assertEqual(result["code"], "LLM_OUTBOUND_BLOCKED")
        self.assertIn("零出网", result["message"])
        self.assertEqual(result["detail"]["host"], "api.example-llm.test")
        self.assertIn("allow_outbound", result["detail"]["hint"])

    def test_probe_uses_the_configured_prompt(self) -> None:
        store = self.configured_store("probe-prompt")
        seen: dict = {}

        def spy(payload: dict) -> dict:
            seen.update(payload)
            return good_transport(payload)

        store.probe(transport=spy, prompt="只回一句：好", max_tokens=17)
        self.assertEqual(seen["messages"][0]["content"], "只回一句：好")
        self.assertEqual(seen["max_tokens"], 17)
        self.assertEqual(seen["model"], MODEL)
        self.assertFalse(seen["stream"])

    def test_probe_counts_and_stats(self) -> None:
        store = self.configured_store("probe-count")
        store.probe(transport=good_transport)
        store.probe(transport=timeout_transport)
        self.assertEqual(store.stats()["probes"], 2)


# --------------------------------------------------------------------------- #
# 8. OpenAI 兼容客户端（t5 协议 + 零出网）
# --------------------------------------------------------------------------- #

class ClientTests(unittest.TestCase):
    def client(self, **over) -> st.OpenAiCompatibleClient:
        over.setdefault("base_url", LOOPBACK_URL)
        over.setdefault("model", MODEL)
        return st.OpenAiCompatibleClient(**over)

    def test_configured_and_endpoint(self) -> None:
        client = self.client()
        self.assertTrue(client.configured)
        # 基址已含 /v1 → 前缀保留、不重复：/v1 + /chat/completions
        self.assertEqual(client.request_path, "/v1/chat/completions")
        self.assertEqual(client.endpoint_display, LOOPBACK_URL + "/chat/completions")
        bare = self.client(base_url="http://127.0.0.1:11434")
        self.assertEqual(bare.request_path, st.CHAT_COMPLETIONS_PATH)
        self.assertEqual(bare.endpoint_display, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertFalse(st.OpenAiCompatibleClient(base_url=LOOPBACK_URL, model="").configured)
        self.assertFalse(st.NullLLMClient().configured)

    def test_guard_blocks_non_loopback_by_default(self) -> None:
        client = self.client(base_url=REMOTE_URL, api_key=SECRET)
        with self.assertRaises(st.LlmUnavailable) as ctx:
            client.guard()
        self.assertEqual(ctx.exception.code, "LLM_OUTBOUND_BLOCKED")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_guard_allows_opt_in_https(self) -> None:
        client = self.client(base_url=REMOTE_URL, api_key=SECRET, allow_outbound=True)
        client.guard()

    def test_build_payload_defaults(self) -> None:
        payload = self.client().build_payload("  提示词  ", max_tokens=999999, temperature=0.9)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "提示词"}])
        self.assertEqual(payload["max_tokens"], st.MAX_TOKENS_MAX)
        self.assertEqual(payload["temperature"], 0.9)
        self.assertEqual(payload["model"], MODEL)

    def test_complete_returns_content_and_meta(self) -> None:
        client = self.client(transport=good_transport)
        answer = client.complete("你好", timeout_s=2.0, max_tokens=32)
        self.assertEqual(answer, MODEL_ANSWER)
        self.assertEqual(client.last_meta["chars"], len(MODEL_ANSWER))
        self.assertEqual(client.last_meta["model"], MODEL)
        self.assertIn("latency_ms", client.last_meta)

    def test_complete_accepts_plain_string_transport(self) -> None:
        client = self.client(transport=lambda p: "好的")
        self.assertEqual(client.complete("你好"), "好的")

    def test_complete_none_when_content_missing(self) -> None:
        client = self.client(transport=lambda p: {"status": 200, "body": {"choices": []}})
        self.assertIsNone(client.complete("你好"))

    def test_timeout_maps_to_readable_code(self) -> None:
        client = self.client(transport=timeout_transport)
        with self.assertRaises(st.LlmUnavailable) as ctx:
            client.complete("你好", timeout_s=0.5)
        self.assertEqual(ctx.exception.code, "LLM_TIMEOUT")
        self.assertIn("超时", str(ctx.exception))

    def test_unreachable_maps_to_llm_unavailable(self) -> None:
        client = self.client(transport=unreachable_transport)
        with self.assertRaises(st.LlmUnavailable) as ctx:
            client.complete("你好")
        self.assertEqual(ctx.exception.code, "LLM_UNREACHABLE")
        self.assertIn("连不上", str(ctx.exception))

    def test_exception_messages_never_carry_the_secret(self) -> None:
        client = self.client(base_url=REMOTE_URL, api_key=SECRET, allow_outbound=True,
                             transport=make_transport(401, {"error": {"message": "bad key"}}))
        with self.assertRaises(st.LlmUnavailable) as ctx:
            client.complete("你好")
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, repr(ctx.exception))

    def test_missing_transport_answer_shape_is_reported(self) -> None:
        client = self.client(transport=lambda p: 42)
        with self.assertRaises(st.LlmUnavailable) as ctx:
            client.complete("你好")
        self.assertEqual(ctx.exception.code, "LLM_TRANSPORT_MISCONFIGURED")

    def test_build_client_falls_back_to_null_when_incomplete(self) -> None:
        store = open_store("client-null")
        self.assertIsInstance(store.build_client(), st.NullLLMClient)
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        client = store.build_client(transport=good_transport)
        self.assertIsInstance(client, st.OpenAiCompatibleClient)
        self.assertTrue(client.configured)

    def test_real_loopback_connection_reports_unreachable(self) -> None:
        """真走一次 `http.client`（端口故意指向没人监听的本机端口）：不崩、原因可读。"""
        store = open_store("client-real-loopback")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": "http://127.0.0.1:9/v1",
                             "model": MODEL, "timeout_s": st.TIMEOUT_MIN}}, actor_id="a")
        result = store.probe()
        self.assertFalse(result["ok"])
        self.assertIn(result["code"], ("LLM_UNREACHABLE", "LLM_TIMEOUT"))
        self.assertTrue(result["message"])


# --------------------------------------------------------------------------- #
# 9. 引擎接线：切 llm 模式后解释引擎按配置生效
# --------------------------------------------------------------------------- #

class EngineWiringTests(unittest.TestCase):
    def record(self) -> dict:
        return json.loads(json.dumps(next(r for r in NATIVE if r["id"] == PENDING)))

    def generator_for(self, store: st.SettingsStore) -> ex.ExplanationGenerator:
        generator = ex.make_generator()
        store.apply_to(generator)
        return generator

    def test_template_gate_reports_mode_template(self) -> None:
        store = open_store("engine-template")
        generator = self.generator_for(store)
        status = generator.llm_status()
        self.assertEqual(status["mode"], st.MODE_TEMPLATE)
        self.assertFalse(status["settings_enabled"])
        self.assertEqual(status["client_type"], "NullLLMClient")
        out = generator.explain(self.record(), store.language())
        self.assertEqual(out["llm"]["mode"], st.MODE_TEMPLATE)
        self.assertFalse(out["llm"]["used"])
        self.assertEqual(out["llm"]["reason"], "mode_template")
        self.assertEqual(out["generated_by"], "template")

    def test_llm_mode_with_client_switches_generated_by(self) -> None:
        store = open_store("engine-llm")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        generator = self.generator_for(store)
        generator.llm_client = MockClient()
        status = generator.llm_status()
        self.assertTrue(status["settings_enabled"])
        self.assertTrue(status["client_configured"])
        out = generator.explain(self.record(), store.language())
        self.assertTrue(out["llm"]["used"], out["llm"])
        self.assertEqual(out["generated_by"], "hybrid")
        self.assertEqual(out["llm"]["model"], MODEL)
        self.assertGreater(out["llm"]["prompt_chars"], 0)
        readable = generator.render_human_readable(self.record(), store.language(),
                                                   generated_by="auto")
        self.assertEqual(readable["generated_by"], "hybrid")
        self.assertTrue(readable["may_contain_inference"])

    def test_apply_to_sets_mode_settings_client_and_language(self) -> None:
        store = open_store("engine-apply")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL, "model": MODEL,
                             "timeout_s": 4.5, "temperature": 0.4, "max_tokens": 300},
                     "ui": {"language": "en"}}, actor_id="a")
        generator = self.generator_for(store)
        self.assertEqual(generator.mode, st.MODE_LLM)
        self.assertTrue(generator.settings.enabled)
        self.assertEqual(generator.settings.model, MODEL)
        self.assertEqual(generator.settings.timeout_s, 4.5)
        self.assertEqual(generator.settings.max_tokens, 300)
        self.assertEqual(generator.settings.temperature, 0.4)
        self.assertFalse(generator.settings.api_key_present)
        self.assertEqual(generator.preferences["language"], "en")
        self.assertEqual(store.language(), "en")
        # 语言切回中文，引擎 preferences 立刻跟着变
        store.apply({"ui": {"language": "zh"}}, actor_id="a")
        store.apply_to(generator)
        self.assertEqual(generator.preferences["language"], "zh")

    def test_switching_back_to_template_restores_template_output(self) -> None:
        store = open_store("engine-back")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        generator = self.generator_for(store)
        client = MockClient()
        generator.llm_client = client
        self.assertEqual(generator.render_human_readable(self.record(), "zh",
                                                          generated_by="auto")["generated_by"],
                          "hybrid")
        store.apply({"llm": {"mode": st.MODE_TEMPLATE}}, actor_id="a")
        store.apply_to(generator)
        client.calls.clear()
        readable = generator.render_human_readable(self.record(), "zh", generated_by="auto")
        self.assertEqual(readable["generated_by"], "template")
        self.assertFalse(readable["may_contain_inference"])
        self.assertEqual(client.calls, [])

    def test_engine_status_gates_are_reported(self) -> None:
        store = open_store("engine-status")
        generator = self.generator_for(store)
        status = store.engine_status(generator)
        self.assertEqual(status["mode"], st.MODE_TEMPLATE)
        self.assertFalse(status["llm_will_run"])
        self.assertFalse(status["gates"]["mode"])
        self.assertEqual(status["client_type"], "NullLLMClient")
        self.assertEqual(status["effect_notice"], st.EFFECT_NOTICE_KEY)
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        store.apply_to(generator)
        status = store.engine_status(generator)
        self.assertTrue(status["llm_will_run"], status)
        self.assertTrue(status["gates"]["client_configured"])
        self.assertIn(st.CHAT_COMPLETIONS_PATH, status["endpoint"])

    def test_client_without_config_falls_back_to_template(self) -> None:
        store = open_store("engine-notconfigured")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        generator = self.generator_for(store)
        generator.llm_client = st.NullLLMClient()
        out = generator.explain(self.record(), "zh")
        self.assertFalse(out["llm"]["used"])
        self.assertEqual(out["llm"]["reason"], "client_not_configured")
        self.assertEqual(out["generated_by"], "template")

    def test_disabled_settings_gate(self) -> None:
        store = open_store("engine-disabled")
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                             "model": MODEL}}, actor_id="a")
        generator = ex.make_generator()
        generator.set_mode(st.MODE_LLM)
        generator.settings = st.LLMSettings(enabled=False, model=MODEL, base_url=LOOPBACK_URL,
                                            temperature=0.2, max_tokens=260, timeout_s=8.0,
                                            api_key_present=False)
        generator.llm_client = MockClient()
        out = generator.explain(self.record(), "zh")
        self.assertEqual(out["llm"]["reason"], "settings_disabled")
        self.assertFalse(out["llm"]["used"])


# --------------------------------------------------------------------------- #
# 10. HTTP：GET /api/settings 与 PUT /api/settings（local 模式）
# --------------------------------------------------------------------------- #

class SettingsApiLocalTests(HttpCase):
    def setUp(self) -> None:
        self.start()

    def test_get_returns_masked_view_and_permissions(self) -> None:
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS)
        self.assertEqual(status, 200, data)
        self.assertEqual(llm_of(data)["mode"], st.MODE_TEMPLATE)
        self.assertEqual(data["permissions"]["permission"], sv.PERMISSION_SETTINGS_WRITE)
        self.assertTrue(data["permissions"]["can_write"])
        self.assertEqual(data["permissions"]["role"], sv.LOCAL_ACTOR_ROLE)
        self.assertEqual(data["engine"]["mode"], st.MODE_TEMPLATE)
        self.assertFalse(data["engine"]["llm_will_run"])
        self.assertEqual(data["team"]["mode"], "local")
        self.assertIn("roles", data["team"]["auth"])
        self.assertEqual(data["llm_test"]["endpoint"], sv.API_PATH_SETTINGS_TEST_LLM)
        self.assertEqual(data["data_management"]["reset"]["endpoint"], sv.API_PATH_RESET)
        self.assertEqual(data["paths"]["settings"], sv.API_PATH_SETTINGS)
        self.assertEqual(data["ui_language_storage_key"], st.UI_LANGUAGE_STORAGE_KEY)
        self.assertNotIn("sk-", json.dumps(data))

    def test_get_never_echoes_the_secret(self) -> None:
        self.req("PUT", sv.API_PATH_SETTINGS,
                 {"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                          "apiKey": SECRET, "allow_outbound": True}})
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS)
        self.assertEqual(status, 200, data)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(SECRET, blob)
        self.assertEqual(llm_of(data)["api_key_mask"], st.MASK_MARK + SECRET_TAIL)
        self.assertTrue(llm_of(data)["api_key_present"])
        self.assertEqual(llm_of(data)["api_key_fingerprint"],
                         st.secret_fingerprint(SECRET))

    def test_put_changes_persists_and_reports_changed(self) -> None:
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                                            "model": MODEL}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["changed"], ["llm.mode", "llm.base_url", "llm.model"])
        self.assertEqual(data["key_action"], "unchanged")
        self.assertEqual(llm_of(data["settings"])["mode"], st.MODE_LLM)
        self.assertEqual(data["engine"]["mode"], st.MODE_LLM)
        self.assertEqual(data["effect_notice"], st.EFFECT_NOTICE_KEY)
        disk = json.loads(self.service.settings.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["llm"]["model"], MODEL)
        self.assertEqual(disk["updated_by"], sv.LOCAL_ACTOR_ID)

    def test_put_accepts_wrapped_settings_object(self) -> None:
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"settings": {"ui": {"language": "en"}}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["changed"], ["ui.language"])
        self.assertEqual(data["settings"]["ui"]["language"], "en")
        self.assertEqual(self.service.generator.preferences["language"], "en")
        self.assertEqual(self.service.settings.language(), "en")

    def test_put_language_switch_changes_the_engine_language(self) -> None:
        self.req("PUT", sv.API_PATH_SETTINGS, {"ui": {"language": "en"}})
        self.assertEqual(self.service.generator.preferences["language"], "en")
        self.assertEqual(self.service.llm_health_block()["language"], "en")
        self.req("PUT", sv.API_PATH_SETTINGS, {"ui": {"language": "zh"}})
        self.assertEqual(self.service.generator.preferences["language"], "zh")

    def test_put_key_actions_are_reported_without_the_value(self) -> None:
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL,
                                            "model": MODEL, "apiKey": SECRET,
                                            "allow_outbound": True}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["key_action"], "set")
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))
        # 再提交一次同一把：报告 same，值不变
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"llm": {"apiKey": SECRET}})
        self.assertEqual(data["key_action"], "same")
        # 不带字段：unchanged
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, {"llm": {"temperature": 0.6}})
        self.assertEqual(data["key_action"], "unchanged")
        # 明确清空
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, {"llm": {"apiKey": ""}})
        self.assertEqual(data["key_action"], "cleared")
        self.assertFalse(llm_of(data["settings"])["api_key_present"])
        self.assertNotIn(SECRET, self.service.settings.path.read_text(encoding="utf-8"))

    def test_put_validation_maps_to_readable_codes(self) -> None:
        cases = [
            ({"llm": {"mode": "wizard"}}, "SETTINGS_MODE_INVALID"),
            ({"llm": {"provider": "chatgpt"}}, "SETTINGS_PROVIDER_INVALID"),
            ({"llm": {"base_url": "http://203.0.113.9/v1", "mode": st.MODE_LLM,
                      "model": MODEL}}, "SETTINGS_URL_INSECURE"),
            ({"llm": {"timeout_s": 900}}, "SETTINGS_FIELD_RANGE"),
            ({"llm": {"nope": 1}}, "SETTINGS_FIELD_UNKNOWN"),
            ({"nope": {}}, "SETTINGS_SECTION_UNKNOWN"),
            ({"ui": {"language": "fr"}}, "SETTINGS_LANGUAGE_INVALID"),
            ({"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL}}, "SETTINGS_FIELD_MISSING"),
            # 非对象请求体在服务端入口就被拦下（统一错误体，口径与 t8 一致）
            ([], "INTERVENE_BODY_NOT_OBJECT"),
        ]
        for patch, want in cases:
            status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, patch)
            self.assertEqual(status, 400, (patch, data))
            self.assertEqual(code_of(data), want, patch)
            self.assertIn("detail", data)

    def test_put_no_changes_is_400(self) -> None:
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"ui": {"language": st.LANGUAGE_DEFAULT}})
        self.assertEqual(status, 400)
        self.assertEqual(code_of(data), "SETTINGS_NO_CHANGES")

    def test_settings_changes_are_audited_without_the_secret(self) -> None:
        self.req("PUT", sv.API_PATH_SETTINGS,
                 {"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                          "apiKey": SECRET, "allow_outbound": True}})
        entries = self.settings_audit()
        self.assertTrue(entries, "设置变更必须进审计")
        entry = entries[-1]
        self.assertEqual(entry["settings_event"], "settings.changed")
        self.assertEqual(entry["key_action"], "set")
        self.assertEqual(entry["key_mask"], st.MASK_MARK + SECRET_TAIL)
        self.assertIn("llm.mode", entry["settings_changed"])
        self.assertEqual(entry["actor_id"], sv.LOCAL_ACTOR_ID)
        blob = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn(SECRET, blob, "审计条目不许带密钥")
        status, _, data = self.req("GET", sv.API_PATH_AUDIT)
        self.assertEqual(status, 200)
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_health_reports_settings_and_llm_block(self) -> None:
        self.req("PUT", sv.API_PATH_SETTINGS,
                 {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL, "model": MODEL}})
        status, _, data = self.req("GET", sv.API_PATH_HEALTH)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["llm"]["mode"], st.MODE_LLM)
        self.assertEqual(data["llm"]["model"], MODEL)
        self.assertTrue(data["llm"]["enabled"])
        self.assertEqual(data["llm"]["outbound"], st.OUTBOUND_BLOCKED)
        self.assertIn("note", data["llm"])
        self.assertIn(sv.API_PATH_SETTINGS, data["endpoints"])
        self.assertIn(sv.API_PATH_SETTINGS_TEST_LLM, data["endpoints"])
        self.assertEqual(data["settings"]["llm_mode"], st.MODE_LLM)
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_method_contract(self) -> None:
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS, {"llm": {"mode": "llm"}})
        self.assertEqual(status, 405, data)
        self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED")
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS_TEST_LLM)
        self.assertEqual(status, 405, data)
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.assertEqual(status, 405, data)
        # t18 的口径不能因为开了 PUT 就松动：其他路径的 PUT/PATCH/DELETE 仍是 405
        for method, path in (("PUT", sv.API_PATH_AUDIT), ("PUT", sv.API_PATH_RECORDS),
                             ("PUT", sv.API_PATH_INTERVENE), ("PATCH", sv.API_PATH_SETTINGS),
                             ("DELETE", sv.API_PATH_SETTINGS), ("PUT", "/api/users")):
            status, _, data = self.req(method, path, {})
            self.assertEqual(status, 405, (method, path, data))
            self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED", (method, path))

    def test_put_on_unknown_route_is_405_not_404(self) -> None:
        status, _, data = self.req("PUT", "/api/nope", {})
        self.assertEqual(status, 405, data)
        self.assertEqual(code_of(data), "METHOD_NOT_ALLOWED")

    def test_readonly_store_writes_nothing(self) -> None:
        path = folder("api-readonly") / "settings.json"
        self.start(settings_path=path, settings_writable=False)
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS)
        self.assertEqual(status, 200, data)
        self.assertFalse(data["team"]["writable"], "视图要如实告诉界面这是只读模式")
        self.assertTrue(str(data["team"].get("note") or ""), "只读模式要给界面一句说明")
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, {"ui": {"language": "en"}})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["settings"]["team"]["writable"])
        self.assertFalse(path.exists(), "--settings-readonly 下不许落盘")
        self.assertFalse(self.service.settings.writable)


# --------------------------------------------------------------------------- #
# 11. HTTP：test-llm
# --------------------------------------------------------------------------- #

class TestLlmApiTests(HttpCase):
    def setUp(self) -> None:
        self.start(llm_transport=good_transport)

    def configure(self, **over) -> None:
        body = {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL, "model": MODEL}}
        body["llm"].update(over)
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, body)
        self.assertEqual(status, 200, data)

    def test_success_is_200_with_readable_report(self) -> None:
        self.configure()
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["code"], "LLM_OK")
        self.assertEqual(data["model"], MODEL)
        self.assertIn("毫秒", data["message"])
        self.assertTrue(data["endpoint"].endswith(st.CHAT_COMPLETIONS_PATH))
        self.assertIn("脱敏", data["sent_facts_note"])
        self.assertEqual(data["engine"]["mode"], st.MODE_LLM)
        self.assertEqual(data["config"]["mode"], st.MODE_LLM)
        self.assertNotIn("Authorization", json.dumps(data, ensure_ascii=False))

    def test_template_mode_reports_mode_template(self) -> None:
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.assertEqual(status, 200, data)
        self.assertFalse(data["ok"])
        self.assertEqual(data["code"], "LLM_MODE_TEMPLATE")

    def test_timeout_report(self) -> None:
        self.start(llm_transport=timeout_transport)
        self.configure()
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM,
                                   {"timeout_s": 2.5})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["code"], "LLM_TIMEOUT")
        self.assertIn("超时", data["message"])

    def test_outbound_blocked_report(self) -> None:
        self.start(llm_transport=good_transport)
        self.configure(base_url=REMOTE_URL, apiKey=SECRET)
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["code"], "LLM_OUTBOUND_BLOCKED")
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_probe_only_effect_notice(self) -> None:
        self.configure()
        _, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {"prompt": "只回：好"})
        self.assertEqual(data["effect_notice"], "settings.test_llm.probe_only")
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_secret_in_body_is_refused(self) -> None:
        self.configure()
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM,
                                   {"apiKey": SECRET})
        self.assertEqual(status, 400, data)
        self.assertEqual(code_of(data), "SETTINGS_FIELD_UNKNOWN")
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))

    def test_probe_parameter_validation(self) -> None:
        self.configure()
        cases = [
            ({"max_tokens": 1}, "SETTINGS_FIELD_RANGE"),
            ({"max_tokens": "32"}, "SETTINGS_FIELD_TYPE"),
            ({"max_tokens": True}, "SETTINGS_FIELD_TYPE"),
            ({"timeout_s": 900}, "SETTINGS_FIELD_RANGE"),
            ({"timeout_s": "fast"}, "SETTINGS_FIELD_TYPE"),
            ({"prompt": 42}, "SETTINGS_FIELD_TYPE"),
            ({"prompt": "字" * 3000}, "SETTINGS_FIELD_TOO_LONG"),
        ]
        for body, want in cases:
            status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, body)
            self.assertEqual(status, 400, (body, data))
            self.assertEqual(code_of(data), want, body)

    def test_last_test_is_visible_in_view_and_health(self) -> None:
        self.configure()
        self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        _, _, view = self.req("GET", sv.API_PATH_SETTINGS)
        self.assertTrue(view["llm_test"]["last"]["ok"])
        self.assertEqual(view["llm_test"]["last"]["code"], "LLM_OK")
        _, _, health = self.req("GET", sv.API_PATH_HEALTH)
        self.assertEqual(health["llm"]["last_test"]["code"], "LLM_OK")

    def test_test_llm_counts_requests_and_audits(self) -> None:
        self.configure()
        self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.assertEqual(self.service.settings_stats()["llm_tests"], 1)
        entries = [e for e in self.settings_audit()
                   if e.get("settings_event") == "settings.test_llm"]
        self.assertTrue(entries)
        self.assertEqual(entries[-1]["test_code"], "LLM_OK")
        self.assertNotIn(SECRET, json.dumps(entries, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 12. 权限：team 模式仅 admin 可改
# --------------------------------------------------------------------------- #

class SettingsPermissionTests(HttpCase):
    def setUp(self) -> None:
        self.state = folder("perm-auth") / au_auth.STATE_FILE_NAME
        self.auth = au_auth.Authenticator("team", state_path=self.state,
                                          iterations=ITER, env={}, bootstrap=True)
        self.boot = self.auth.bootstrap_report or {}
        self.admin_actor = self.auth.authenticate("admin", str(self.boot["password"]))
        self.editor = self.auth.create_user("edi", "editor", PW_EDITOR,
                                            actor=self.admin_actor)
        self.viewer = self.auth.create_user("viewa", "viewer", PW_VIEWER,
                                            actor=self.admin_actor)
        self.start(mode="team", auth=self.auth, llm_transport=good_transport,
                   settings_path=store_path(self.id() + "-settings"))

    def patch_body(self) -> dict:
        return {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL, "model": MODEL}}

    def test_anonymous_cannot_read_or_write(self) -> None:
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS)
        self.assertEqual(status, 401, data)
        self.assertEqual(code_of(data), "AUTH_REQUIRED")
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body())
        self.assertEqual(status, 401, data)

    def test_admin_can_write(self) -> None:
        session = self.sign_in("admin", str(self.boot["password"]))
        self.assertEqual(session["role"], "admin")
        self.assertIn("settings.write", session["permissions"])
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body(),
                                   headers=self.as_user(session))
        self.assertEqual(status, 200, data)
        self.assertEqual(llm_of(data["settings"])["mode"], st.MODE_LLM)
        self.assertTrue(data["settings"]["permissions"]["can_write"])

    def test_editor_and_viewer_cannot_write(self) -> None:
        for username, password in (("edi", PW_EDITOR), ("viewa", PW_VIEWER)):
            session = self.sign_in(username, password)
            self.assertNotIn("settings.write", session["permissions"], username)
            status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body(),
                                       headers=self.as_user(session))
            self.assertEqual(status, 403, (username, data))
            self.assertEqual(code_of(data), "ROLE_FORBIDDEN", username)
            self.assertEqual(llm_of(self.service.settings.public())["mode"], st.MODE_TEMPLATE,
                             f"{username} 的 403 绝不能已经把配置改了")
            _, _, view = self.req("GET", sv.API_PATH_SETTINGS,
                                  headers=self.as_user(session, csrf=False))
            self.assertFalse(view["permissions"]["can_write"])
            self.assertIn("note", view["permissions"])

    def test_editor_and_viewer_can_still_read_masked_view(self) -> None:
        session = self.sign_in("viewa", PW_VIEWER)
        status, _, data = self.req("GET", sv.API_PATH_SETTINGS,
                                   headers=self.as_user(session, csrf=False))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["permissions"]["role"], "viewer")
        self.assertFalse(data["permissions"]["can_write"])
        self.assertIn("llm", data)
        self.assertNotIn("gates", data["engine"], "非写权限的人不需要看引擎内部闸门")
        self.assertEqual(data["engine"]["mode"], st.MODE_TEMPLATE)
        self.assertIn("effect_notice", data["engine"])

    def test_cookie_write_without_csrf_is_refused(self) -> None:
        session = self.sign_in("admin", str(self.boot["password"]))
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body(),
                                   headers={"Cookie": session["cookie"]})
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "CSRF_FAILED")

    def test_test_llm_requires_settings_write(self) -> None:
        editor = self.sign_in("edi", PW_EDITOR)
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {},
                                   headers=self.as_user(editor))
        self.assertEqual(status, 403, data)
        self.assertEqual(code_of(data), "ROLE_FORBIDDEN")
        self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body(),
                 headers=self.as_user(self.sign_in("admin", str(self.boot["password"]))))
        admin = self.sign_in("admin", str(self.boot["password"]))
        status, _, data = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {},
                                   headers=self.as_user(admin))
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])

    def test_denied_attempts_are_audited(self) -> None:
        editor = self.sign_in("edi", PW_EDITOR)
        self.req("PUT", sv.API_PATH_SETTINGS, self.patch_body(),
                 headers=self.as_user(editor))
        events = [str(e.get("auth_event") or "") for e in self.audit_entries()]
        self.assertIn("auth.denied", events)

    def test_team_view_reports_team_mode_and_env_surface(self) -> None:
        session = self.sign_in("admin", str(self.boot["password"]))
        _, _, data = self.req("GET", sv.API_PATH_SETTINGS,
                              headers=self.as_user(session, csrf=False))
        self.assertEqual(data["mode"], "team")
        self.assertEqual(data["team"]["mode"], "team")
        self.assertEqual(data["team"]["roles"], list(au_auth.TEAM_ROLES))
        self.assertEqual(data["security"]["env_names"], list(st.ENV_NAMES))

    def test_env_injected_secret_is_the_source_in_team_mode(self) -> None:
        self.start(mode="team", auth=self.auth,
                   settings_env={st.ENV_LLM_API_KEY: ENV_SECRET, st.ENV_LLM_MODE: st.MODE_LLM,
                                 st.ENV_LLM_BASE_URL: REMOTE_URL,
                                 st.ENV_LLM_MODEL: "deepseek-chat",
                                 st.ENV_LLM_ALLOW_OUTBOUND: "true"},
                   llm_transport=good_transport,
                   settings_path=store_path(self.id() + "-env"))
        session = self.sign_in("admin", str(self.boot["password"]))
        _, _, data = self.req("GET", sv.API_PATH_SETTINGS, headers=self.as_user(session,
                                                                              csrf=False))
        self.assertEqual(llm_of(data)["api_key_source"], st.KEY_ENV)
        self.assertEqual(llm_of(data)["api_key_mask"],
                         st.MASK_MARK + ENV_SECRET[-st.MASK_TAIL:])
        self.assertIn(st.ENV_LLM_API_KEY, data["security"]["env_overrides"])
        status, _, probed = self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {},
                                     headers=self.as_user(session))
        self.assertTrue(probed["ok"], probed)
        self.assertNotIn(ENV_SECRET, json.dumps(probed, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# 13. 端到端：切到 llm 模式后，决定重算走配置好的引擎
# --------------------------------------------------------------------------- #

class EngineTakesEffectOverHttpTests(HttpCase):
    def setUp(self) -> None:
        self.start()

    def intervene(self, action_id: str = PENDING) -> dict:
        """`POST /intervene` 直接返回更新后的记录（含 `human_readable`）。"""
        status, headers, data = self.req("POST", "/intervene",
                                         {"action_id": action_id, "decision": "approve"})
        self.assertEqual(status, 200, (status, data))
        self.assertIn("x-request-id", headers)
        self.assertIsInstance(data, dict)
        return data if isinstance(data, dict) else {}

    def test_template_baseline_is_template_label(self) -> None:
        record = self.intervene()
        self.assertEqual(record.get("id"), PENDING)
        self.assertEqual((record.get("human_readable") or {}).get("generated_by"), "template")

    def test_llm_mode_makes_the_engine_hybrid(self) -> None:
        status, _, data = self.req("PUT", sv.API_PATH_SETTINGS,
                                   {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL,
                                            "model": MODEL}})
        self.assertEqual(status, 200, data)
        client = MockClient()
        self.service.generator.llm_client = client   # 注入假模型（无出网）
        self.assertTrue(self.service.settings.engine_status(self.service.generator)
                        ["llm_will_run"])
        record = self.intervene()
        readable = record.get("human_readable") or {}
        self.assertEqual(readable.get("generated_by"), "hybrid", readable)
        self.assertTrue(readable.get("may_contain_inference"))
        self.assertEqual(readable.get("model"), MODEL)
        self.assertEqual(len(client.calls), 1, "决定重算只调用一次模型")
        self.assertIn("hybrid", json.dumps(readable, ensure_ascii=False))

    def test_switching_back_restores_template_label(self) -> None:
        self.req("PUT", sv.API_PATH_SETTINGS,
                 {"llm": {"mode": st.MODE_LLM, "base_url": LOOPBACK_URL, "model": MODEL}})
        self.service.generator.llm_client = MockClient()
        self.req("PUT", sv.API_PATH_SETTINGS, {"llm": {"mode": st.MODE_TEMPLATE}})
        record = self.intervene()
        self.assertEqual((record.get("human_readable") or {}).get("generated_by"), "template")
        self.assertEqual(self.service.llm_health_block()["mode"], st.MODE_TEMPLATE)

    def test_saved_durable_audit_records_the_change(self) -> None:
        log = folder("durable-audit") / "audit.jsonl"
        self.start(audit_log_path=log)
        self.req("PUT", sv.API_PATH_SETTINGS, {"ui": {"language": "en"}})
        self.req("POST", sv.API_PATH_SETTINGS_TEST_LLM, {})
        self.service.flush_auth_events()
        text = log.read_text(encoding="utf-8")
        self.assertIn("settings.changed", text)
        self.assertIn("settings.test_llm", text)
        self.assertNotIn(SECRET, text)
        _, _, payload = self.req("GET", sv.API_PATH_AUDIT)
        self.assertTrue(payload["jsonl"]["count"] >= 1)
        self.assertTrue(payload["chain"]["ok"])


# --------------------------------------------------------------------------- #
# 14. 命令行入口
# --------------------------------------------------------------------------- #

class CliTests(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = st.main(list(argv))
        return code, out.getvalue()

    def test_show_masks_the_secret(self) -> None:
        path = store_path("cli-show")
        store = st.SettingsStore(path, env={})
        store.load()
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                             "apiKey": SECRET, "allow_outbound": True}}, actor_id="cli")
        code, text = self.run_cli("--settings", str(path), "--show")
        self.assertEqual(code, 0)
        self.assertIn(st.MASK_MARK, text)
        self.assertNotIn(SECRET, text)
        self.assertIn(MODEL, text)
        self.assertIn("出网", text)

    def test_set_and_reject_bad_values(self) -> None:
        path = store_path("cli-set")
        code, text = self.run_cli("--settings", str(path), "--set", "ui.language=en")
        self.assertEqual(code, 0, text)
        self.assertIn("ui.language", text)
        code, text = self.run_cli("--settings", str(path), "--set", "llm.mode=wizard")
        self.assertEqual(code, 2)
        self.assertIn("改设置失败", text)
        code, text = self.run_cli("--settings", str(path), "--set", "llm.mode")
        self.assertEqual(code, 2)
        code, text = self.run_cli("--settings", str(path), "--set", "nope.x=1")
        self.assertEqual(code, 2)

    def test_json_output_has_no_secret(self) -> None:
        path = store_path("cli-json")
        store = st.SettingsStore(path, env={})
        store.load()
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": REMOTE_URL, "model": MODEL,
                             "apiKey": SECRET, "allow_outbound": True}}, actor_id="cli")
        code, text = self.run_cli("--settings", str(path), "--json")
        self.assertEqual(code, 0)
        data = json.loads(text)
        self.assertNotIn(SECRET, text)
        self.assertTrue(data["llm"]["api_key_present"])

    def test_test_llm_exit_code_follows_the_result(self) -> None:
        path = store_path("cli-test")
        code, text = self.run_cli("--settings", str(path), "--test-llm")
        self.assertEqual(code, 1, text)          # 模板模式：没发请求，明确失败
        self.assertIn("LLM_MODE_TEMPLATE", text)
        store = st.SettingsStore(path, env={})
        store.load()
        store.apply({"llm": {"mode": st.MODE_LLM, "base_url": "http://127.0.0.1:9/v1",
                             "model": MODEL, "timeout_s": st.TIMEOUT_MIN}}, actor_id="cli")
        code, text = self.run_cli("--settings", str(path), "--test-llm")
        self.assertEqual(code, 1)
        # 本机没人监听：连不上或超时都算，但必须是"人能读懂的那个 code"
        self.assertTrue("LLM_UNREACHABLE" in text or "LLM_TIMEOUT" in text, text)
        self.assertNotIn("Authorization", text)
        self.assertIn("http://127.0.0.1:9/v1/chat/completions", text)

    def test_team_mode_announces_env_takeover(self) -> None:
        path = store_path("cli-team")
        code, text = self.run_cli("--settings", str(path), "--mode", "team", "--show")
        self.assertEqual(code, 0)
        self.assertIn("team", text)


# --------------------------------------------------------------------------- #
# 15. 纯净性守门：这一层是"服务端不碰密钥"的书面合同
# --------------------------------------------------------------------------- #

FORBIDDEN_ROOTS = {"requests", "urllib3", "httpx", "aiohttp", "socket", "socketserver",
                   "asyncio", "subprocess", "multiprocessing", "ftplib", "smtplib",
                   "telnetlib", "websocket", "websockets"}
BANNED_TOKENS = ("http://", "https://", "urllib", "socket.", "subprocess")


def import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if getattr(node, "level", 0) > 0:
                continue
            roots.add(str(node.module or "").split(".")[0])
    return roots


class SettingsPurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PKG / "settings.py").read_text(encoding="utf-8")

    def test_imports_are_stdlib_only(self) -> None:
        for root in import_roots(self.source):
            if root == "agent_explainer" or not root:
                continue
            self.assertIn(root, sys.stdlib_module_names, root)
            self.assertNotIn(root, FORBIDDEN_ROOTS, root)
        self.assertIn("ctypes", import_roots(self.source))
        self.assertIn("http", import_roots(self.source))

    def test_no_network_literals_in_source(self) -> None:
        for token in BANNED_TOKENS:
            self.assertNotIn(token, self.source, token)

    def test_dpapi_and_client_are_present(self) -> None:
        for needle in ("CryptProtectData", "CryptUnprotectData", "LocalFree",
                       "http.client", "HTTPSConnection", "HTTPConnection",
                       "CHAT_COMPLETIONS_PATH", "APP_ENTROPY"):
            self.assertIn(needle, self.source, needle)
        self.assertIn("allow_outbound", self.source)

    def test_module_entry_point_is_wired(self) -> None:
        # 打包入口：桌面排障要能不启动服务就 python -m agent_explainer.settings --show
        self.assertIn('if __name__ == "__main__":', self.source)
        self.assertIn("raise SystemExit(main())", self.source)

    def test_secret_never_in_public_view_helpers(self) -> None:
        """`reveal_secret` 只有一处调用者（客户端），公开视图一律用 key_state/public。"""
        self.assertIn("def reveal_secret", self.source)
        self.assertIn("def key_state", self.source)
        self.assertIn("def mask_secret", self.source)
        self.assertIn("def redact", self.source)

    def test_server_py_never_spells_the_secret_field(self) -> None:
        source = (PKG / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("api_key", source.lower())
        self.assertNotIn("openai", source.lower())
        self.assertNotIn("Authorization:", source)
        self.assertNotIn("sk-", source)
        for needle in ("API_PATH_SETTINGS", "API_PATH_SETTINGS_TEST_LLM",
                       "PERMISSION_SETTINGS_WRITE", "PATCH_FIELD_SECRET",
                       "settings_mod.SettingsStore", "def do_PUT", "_method_blocked",
                       "def llm_health_block", "def save_settings",
                       "def test_llm_connection", "def settings_view"):
            self.assertIn(needle, source, needle)

    def test_server_py_does_no_file_writes(self) -> None:
        source = (PKG / "server.py").read_text(encoding="utf-8")
        for token in ("write_text", "write_bytes", "open(", ".unlink", "mkdir", "shutil"):
            self.assertNotIn(token, source, token)

    def test_settings_module_is_pure_for_offline_mode(self) -> None:
        """设置中心默认零出网：`allow_outbound` 默认 False，非回环必须显式打开。"""
        self.assertFalse(st.defaults_doc()["llm"]["allow_outbound"])
        store = open_store("purity-outbound")
        self.assertEqual(store.public()["security"]["outbound"], st.OUTBOUND_BLOCKED)
        self.assertIn("零出网", store.public()["security"]["outbound_note"])

    def test_i18n_covers_every_settings_error_code(self) -> None:
        """界面要能把每个 code 翻译成人话：zh 与 en 都得有键（t15 的自检也钉这条）。"""
        web = PKG / "web" / "i18n"
        zh = json.loads((web / "zh.json").read_text(encoding="utf-8"))
        en = json.loads((web / "en.json").read_text(encoding="utf-8"))
        codes = [
            "SETTINGS_MODE_INVALID", "SETTINGS_PROVIDER_INVALID", "SETTINGS_URL_SCHEME",
            "SETTINGS_URL_MALFORMED", "SETTINGS_URL_INSECURE", "SETTINGS_URL_USERINFO",
            "SETTINGS_URL_QUERY", "SETTINGS_URL_FRAGMENT", "SETTINGS_URL_PORT",
            "SETTINGS_FIELD_RANGE", "SETTINGS_FIELD_MISSING", "SETTINGS_FIELD_TOO_LONG",
            "SETTINGS_FIELD_TYPE", "SETTINGS_FIELD_UNKNOWN", "SETTINGS_FIELD_EMPTY",
            "SETTINGS_SECTION_UNKNOWN", "SETTINGS_BODY_NOT_OBJECT", "SETTINGS_BODY_EMPTY",
            "SETTINGS_NO_CHANGES", "SETTINGS_LANGUAGE_INVALID", "SETTINGS_GRANULARITY_INVALID",
            "SETTINGS_WRITE_FAILED", "SETTINGS_READ_FAILED", "SETTINGS_FILE_INVALID",
            "LLM_MODE_TEMPLATE", "LLM_NOT_CONFIGURED", "LLM_KEY_MISSING",
            "LLM_OUTBOUND_BLOCKED", "LLM_TIMEOUT", "LLM_UNREACHABLE", "LLM_HTTP_ERROR",
            "LLM_KEY_REJECTED", "LLM_PATH_NOT_FOUND", "LLM_RATE_LIMITED",
            "LLM_EMPTY_ANSWER", "LLM_CLIENT_ERROR", "LLM_OK",
        ]
        for code in codes:
            # i18n 的键是分组的：settings 域里的键 = "error.<code>"（界面拼成 settings.error.<code>）
            key = f"error.{code.lower()}"
            self.assertIn(key, zh.get("settings", {}), key)
            self.assertIn(key, en.get("settings", {}), key)
            self.assertTrue(str(zh["settings"][key]).strip())
            self.assertTrue(str(en["settings"][key]).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
