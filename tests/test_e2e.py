"""子任务 5.3（t12）桌面端打包与端到端联调 —— 全链路 e2e。

这个套件跑的是**真进程**：用 `python -m agent_explainer --sidecar --port 0` 拉起
产品服务（桌面端 Electron 主进程用的就是这条命令），从 stdout 读机器可读的
`{"tracelens_bootstrap": {…}}` 拿端口，再按桌面端真实的调用顺序走一遍：

演示加载 → `GET /api/records`（任务书里的 `/api/actions` 在代码里叫这个名字，
docs/03 §6.5 已核对）→ `POST /intervene` → `GET /api/audit` → CSV 导出
→ SSE 收到事件（`hello` / `action.decision_recorded` / `records.reset`）
→ 导入 → 重启后记录与用户决定仍在 → `reset` 只清记录、审计只追加。

另外钉住：桌面端参数面（`--mode/--port/--demo/--no-browser/--data-dir/--sidecar`）
与"其余参数原样转发"的口径、team 模式必须登录 + CSRF、Electron 壳的接线
（单实例锁、健康检查后才加载 UI、退出清理、`contextIsolation`）、
打包产物的 SHA256 逐条重算与签名状态核对、sidecar exe 真跑一次。
**没有产物就如实 skip 并把原因写进 skip 理由**，绝不假绿。

临时目录一律走 `tests/scratch.py`（系统临时目录优先，绝不落在 `tests/` 下）。
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))

from agent_explainer import __main__ as entry                      # noqa: E402
from agent_explainer import auth as au                              # noqa: E402
from agent_explainer import server as sv                            # noqa: E402

from scratch import scratch_dir                                     # noqa: E402

DEMO_ZH = REPO / "samples" / "action_records.json"
DEMO_RECORDS = json.loads(DEMO_ZH.read_text(encoding="utf-8"))["records"]
DEMO_COUNT = len(DEMO_RECORDS)
PENDING_IDS = [r["id"] for r in DEMO_RECORDS
               if r.get("status") == "pending_confirmation"]
PENDING_ID = PENDING_IDS[0]
# 桌面壳拉起 sidecar 用的环境（`desktop/sidecar.js` 一模一样）：管道块缓冲会把
# 首启摘要与 team 模式的初始口令卡在缓冲区里，逐行读的人拿不到。
PRODUCTION_ENV = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1",
                      PYTHONIOENCODING="utf-8")
PIPE_ENV = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
LANGFUSE_TEXT = (REPO / "samples" / "traces" / "langfuse.json").read_text(encoding="utf-8")
LANGFUSE_COUNT = 8
DESKTOP = REPO / "desktop"
DIST = DESKTOP / "build" / "dist"
RESOURCES = DESKTOP / "build" / "resources"
SIDECAR_CANDIDATES = (
    RESOURCES / "sidecar" / "agent-explainer-sidecar.exe",
    RESOURCES / "sidecar" / "agent-explainer-sidecar" / "agent-explainer-sidecar.exe",
)
SIDECAR_EXE = next((p for p in SIDECAR_CANDIDATES if p.exists()), SIDECAR_CANDIDATES[0])
SUMS = DIST / "SHA256SUMS.txt"
SIGN_REPORT = DIST / "SIGNATURE-STATUS.json"


def spawn_supported() -> tuple[bool, str]:
    """沙箱能不能拉起子进程并读它的水管（不能就整组如实 skip）。"""
    try:
        done = subprocess.run([sys.executable, "-c", "print('ok')"],
                              capture_output=True, text=True, timeout=60)
    except Exception as exc:                                       # noqa: BLE001
        return False, f"子进程拉起失败：{type(exc).__name__}: {exc}"
    if done.returncode != 0 or "ok" not in (done.stdout or ""):
        return False, f"子进程输出不可读：rc={done.returncode} {(done.stderr or '')[:160]}"
    return True, ""


SPAWN_OK, SPAWN_REASON = spawn_supported()


def _ledger_fingerprint(root: pathlib.Path) -> tuple:
    """目录内容的指纹：每个文件的相对路径 + 字节数 + sha256（目录不存在＝空指纹）。

    给 F4 用：真跑子进程的用例跑前跑后各核一次，只要有一个字节的差异就说明账本
    写回了仓库（`var/features`），这条用例就该红。
    """
    if not root.exists():
        return ()
    rows = []
    for item in sorted(root.rglob("*")):
        if item.is_file():
            rows.append((str(item.relative_to(root)).replace("\\", "/"),
                         item.stat().st_size,
                         hashlib.sha256(item.read_bytes()).hexdigest()))
    return tuple(rows)


class Sidecar:
    """`python -m agent_explainer --sidecar` 的真实子进程 + HTTP / SSE 助手。"""

    def __init__(self, name: str, *extra: str, demo: str = "zh",
                 data_dir: pathlib.Path | None = None,
                 env: dict | None = None) -> None:
        self.data_dir = (data_dir or scratch_dir(f"e2e-{name}") / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # F4（QA 0.5.0 验收登记的测试卫生缺陷）：总裁办的账本根目录**不跟随 --data-dir**
        # ——t2/t6 实测：不传 --features 时 server 把账本写在 <应用根>/var/features。
        # 本套件每个 Sidecar 都把账本钉到自己的临时目录里，否则跑一遍全量套件就往
        # 仓库 var/features 里写几 MB 的 heads.jsonl（QA 实测 2,721,000 → 4,171,608 字节）。
        self.features_dir = self.data_dir.parent / "features"
        self.features_dir.mkdir(parents=True, exist_ok=True)
        self.env = env or PRODUCTION_ENV
        self.cmd = [sys.executable, "-X", "utf8", "-m", "agent_explainer",
                    "--sidecar", "--port", "0", "--demo", demo,
                    "--data-dir", str(self.data_dir),
                    "--features", str(self.features_dir),
                    "--stream-heartbeat", "0.4", *extra]
        self.proc: subprocess.Popen[str] | None = None
        self.lines: list[str] = []
        self.bootstrap: dict = {}
        self.port = 0

    # -- 生命周期 ---------------------------------------------------------- #
    def start(self, timeout: float = 60.0) -> "Sidecar":
        self.proc = subprocess.Popen(self.cmd, cwd=str(REPO), env=self.env,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8",
                                     errors="replace", bufsize=1)
        # 后台线程持续排水：Windows 上管道缓冲区写满会把子进程卡住，
        # 而 team 首启的初始口令只打印这一次，漏读等于把口令丢了。
        threading.Thread(target=self._drain, name="e2e-drain", daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self.lines):
                if line.startswith("{") and entry.BOOTSTRAP_KEY in line:
                    self.bootstrap = json.loads(line)[entry.BOOTSTRAP_KEY]
                    self.port = int(self.bootstrap["port"])
                    self.wait_health()
                    return self
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"sidecar 启动失败，退出码 {self.proc.returncode}：\n"
                    + "\n".join(self.lines[-30:]))
            time.sleep(0.05)
        raise RuntimeError("没读到 bootstrap 行：\n" + "\n".join(self.lines[-30:]))

    def _drain(self) -> None:
        stream = self.proc.stdout if self.proc else None
        if not stream:
            return
        for line in stream:
            self.lines.append(line.rstrip("\r\n"))

    def wait_for_line(self, pattern: str, timeout: float = 20.0) -> str:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self.lines):
                if rx.search(line):
                    return line
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"没等到匹配 {pattern!r} 的输出行：\n"
                             + "\n".join(self.lines[-30:]))

    def wait_health(self, timeout: float = 45.0) -> dict:
        deadline = time.monotonic() + timeout
        last: object = None
        while time.monotonic() < deadline:
            try:
                status, _heads, body = self.http("GET", sv.API_PATH_HEALTH)
                if status == 200 and isinstance(body, dict):
                    return body
                last = (status, body)
            except OSError as exc:
                last = str(exc)
            time.sleep(0.15)
        raise RuntimeError(f"/health 一直没就绪：{last}\n" + "\n".join(self.lines[-30:]))

    def stop(self, timeout: float = 15.0) -> int:
        if self.proc is None:
            return 0
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:                          # pragma: no cover
            self.proc.kill()
            self.proc.wait(timeout=timeout)
        for _ in range(40):            # 等排水线程把收尾几行读完
            if self.proc.stdout is None:
                break
            time.sleep(0.05)
        self.proc = None
        return 0

    # -- HTTP -------------------------------------------------------------- #
    def http(self, method: str, path: str, body=None,
             headers: dict | None = None,
             timeout: float = 15.0) -> tuple[int, dict, object]:
        extra = {"Connection": "close"}
        extra.update(headers or {})
        payload = None
        if body is not None:
            # 请求体里有中文：必须自己编成 UTF-8 字节，http.client 默认按 latin-1 编
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            extra.setdefault("Content-Type", "application/json; charset=utf-8")
            extra["Content-Length"] = str(len(payload))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request(method, path, payload, headers=extra)
            resp = conn.getresponse()
            raw = resp.read()
            heads = {str(k).lower(): v for k, v in resp.getheaders()}
            text = raw.decode("utf-8", "replace")
            try:
                parsed: object = json.loads(text)
            except ValueError:
                parsed = text
            return int(resp.status), heads, parsed
        finally:
            conn.close()

    def login_admin(self, password: str, username: str = "admin") -> dict:
        status, _heads, data = self.http("POST", sv.API_PATH_AUTH_LOGIN,
                                        {"username": username, "password": password})
        if status != 200:
            raise AssertionError(f"登录失败 {status}：{data}")
        return {"csrf": str(data.get("csrf_token") or ""),
                "cookie": f"{au.SESSION_COOKIE}={data['cookie']['value']}"}

    # -- SSE --------------------------------------------------------------- #
    def stream(self, frames: int = 2, timeout: float = 20.0, trigger=None) -> list[str]:
        """连上 SSE 收事件名；`trigger` 在连接建立后执行一次（用来产生事件）。"""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(
            f"GET {sv.API_PATH_STREAM} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: keep-alive\r\n\r\n".encode("ascii"))
        events: list[str] = []
        fired = trigger is None
        buf = b""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and len(events) < frames:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                for raw in buf.split(b"\n"):
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("event:"):
                        name = line.split(":", 1)[1].strip()
                        if name not in events:
                            events.append(name)
                if not fired:
                    fired = True
                    if trigger:
                        trigger()
        finally:
            sock.close()
        return events

    def stdout_text(self) -> str:
        return "\n".join(self.lines)


@contextlib.contextmanager
def captured():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def run_entry(*argv: str) -> tuple[int, str]:
    """进程内调用入口 main（只用于 `--print-argv` / `--help` 这类不起服务的分支）。"""
    with captured() as out:
        code = entry.main(list(argv))
    return int(code), out.getvalue()


def editable_param(record: dict) -> tuple[str, str, dict]:
    """从记录自己的可编辑字段里挑一个，返回 (option_id, 字段名, 新值)。

    演示数据每条待确认记录给的可编辑字段都不一样（最多移动数 / 最多删除数 /
    收件人…），所以测试不能把字段名写死；照着记录里的 `editable_fields` 发，
    才是界面真正会发的请求。
    """
    for option in record.get("intervention_options") or []:
        fields = option.get("editable_fields") or []
        if not fields:
            continue
        field = fields[0]
        name = str(field["name"])
        kind = str(field.get("type"))
        default = field.get("default")
        if kind == "integer":
            high = field.get("max")
            low = field.get("min", 0)
            value = int(default if default is not None else low)
            if high is not None and value >= int(high):
                value = int(high) - 1
            else:
                value += 1
            if value == int(default):
                value = int(default) - 1
        elif kind == "boolean":
            value = not bool(default)
        else:
            value = str(default if default is not None else "")
        return str(option["id"]), name, {name: value}
    raise AssertionError(f"记录 {record.get('id')} 没有任何可编辑字段")


# --------------------------------------------------------------------------- #
# A. 桌面端参数面（不起进程也能钉的先钉住）
# --------------------------------------------------------------------------- #
class CommandLineTests(unittest.TestCase):
    """`--mode/--port/--demo/--no-browser/--data-dir/--sidecar` 的翻译与转发。"""

    def test_help_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_entry("--help")
        self.assertEqual(0, ctx.exception.code)

    def test_data_dir_lands_every_state_file_together(self) -> None:
        root = scratch_dir("cli-data-dir") / "My Data"
        code, text = run_entry("--print-argv", "--port", "8899",
                               "--data-dir", str(root))
        self.assertEqual(0, code, text)
        for name in ("records.jsonl", "audit.jsonl", "settings.json",
                     os.path.join("auth", "auth-state.json"),
                     os.path.join("ingest", "tokens.json")):
            self.assertIn(str(root / name), text, f"{name} 必须落在 --data-dir 里")

    def test_demo_choices_pick_the_right_seed(self) -> None:
        zh = run_entry("--print-argv", "--demo", "zh")[1]
        en = run_entry("--print-argv", "--demo", "en")[1]
        self.assertIn(str(entry.DEMO_ZH), zh)
        self.assertIn(str(entry.DEMO_EN), en)
        self.assertNotIn(str(entry.DEMO_EN), zh)
        root = scratch_dir("cli-demo-none")
        code, text = run_entry("--print-argv", "--demo", "none", "--data-dir", str(root))
        self.assertEqual(0, code, text)
        self.assertIn("seed-empty.json", text)
        seed = json.loads((root / "seed-empty.json").read_text(encoding="utf-8"))
        self.assertEqual([], seed["records"], "--demo none 的种子必须真是空清单")

    def test_mode_reset_and_browser_flags(self) -> None:
        code, text = run_entry("--print-argv", "--mode", "team", "--demo-reset")
        self.assertEqual(0, code, text)
        self.assertIn("--mode team", text)
        self.assertIn("--reset-demo", text)
        parser = entry.make_parser()
        base = parser.parse_args([])
        self.assertFalse(base.browser, "默认不开浏览器：Electron / 便携版不能被抢着弹窗")
        self.assertTrue(parser.parse_args(["--browser"]).browser)
        self.assertFalse(parser.parse_args(["--no-browser"]).browser)
        self.assertEqual("zh", base.demo)
        self.assertEqual(8765, base.port)

    def test_unknown_flags_are_forwarded_verbatim(self) -> None:
        code, text = run_entry("--print-argv", "--no-records-store", "--no-audit-log",
                               "--issue-ingest-token", "ci", "--verbose")
        self.assertEqual(0, code, text)
        for flag in ("--no-records-store", "--no-audit-log", "--issue-ingest-token",
                     "ci", "--verbose"):
            self.assertIn(flag, text)

    def test_sidecar_flag_and_helpers(self) -> None:
        args, _extra = entry.make_parser().parse_known_args(["--sidecar"])
        self.assertTrue(args.sidecar)
        self.assertEqual("TRACELNS_SIDECAR", entry.SIDECAR_ENV)
        self.assertGreater(entry.free_port(), 0)
        self.assertEqual(entry.REPO_ROOT / "var", entry.default_data_dir())

    def test_bootstrap_payload_shape(self) -> None:
        args = entry.make_parser().parse_args(["--port", "8899", "--mode", "local"])
        root = scratch_dir("cli-bootstrap")
        payload = entry.bootstrap_payload(args, root, ["--mode", "local"])
        self.assertEqual(8899, payload["port"])
        self.assertEqual("http://127.0.0.1:8899/health", payload["health_url"])
        self.assertTrue(payload["url"].startswith("http://127.0.0.1:8899/"))
        self.assertEqual(os.getpid(), payload["pid"])
        self.assertEqual(sv.SERVER_VERSION, payload["version"])
        self.assertEqual(DEMO_COUNT, payload["seed_records"])
        self.assertTrue(payload["first_run"], "没有 records.jsonl 时首启标志必须为真")
        self.assertFalse(payload["audit_exists"])
        self.assertIn("/health", payload["note"], "验收口径以 GET /health 为准")
        self.assertEqual(str(root / "records.jsonl"), payload["records_store"])
        self.assertEqual(["--mode", "local"], payload["argv"])

    def test_seed_counts_match_the_fixtures(self) -> None:
        self.assertEqual(DEMO_COUNT, entry.seed_record_count("zh"))
        self.assertEqual(0, entry.seed_record_count("none"))
        en = json.loads(entry.DEMO_EN.read_text(encoding="utf-8"))
        self.assertEqual(len(en["records"]), entry.seed_record_count("en"))

    def test_frozen_sidecar_does_not_keep_data_in_the_unpack_dir(self) -> None:
        """PyInstaller 单文件解包目录每次运行都换新，写进去的记录等于丢。"""
        real = entry.default_data_dir()
        self.assertEqual(entry.REPO_ROOT / "var", real)
        frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            home = entry.default_data_dir()
        finally:
            if frozen is None:
                del sys.frozen
            else:
                sys.frozen = frozen
        self.assertNotEqual(real, home)
        self.assertTrue(str(home).startswith(str(pathlib.Path(os.path.expanduser("~")))),
                        f"冻结模式的数据目录必须在用户目录下：{home}")
        self.assertIn(".tracelens", str(home))


# --------------------------------------------------------------------------- #
# B. 真进程端到端：桌面端加载 UI 之前的每一步
# --------------------------------------------------------------------------- #
@unittest.skipUnless(SPAWN_OK, f"沙箱不允许拉起子进程：{SPAWN_REASON}")
class SidecarEndToEndTests(unittest.TestCase):
    """演示加载 → 记录 → 干预 → 审计 → CSV 导出 → SSE → 导入 → 重启 → reset。"""

    def setUp(self) -> None:
        self.sidecars: list[Sidecar] = []

    def tearDown(self) -> None:
        for item in self.sidecars:
            item.stop()
        self.sidecars.clear()

    def boot(self, name: str, *extra: str, demo: str = "zh",
             data_dir: pathlib.Path | None = None) -> Sidecar:
        item = Sidecar(name, *extra, demo=demo, data_dir=data_dir)
        self.sidecars.append(item)
        return item.start()

    # -- 启动协议 ---------------------------------------------------------- #
    def test_bootstrap_line_health_and_first_run(self) -> None:
        app = self.boot("boot")
        for key in ("port", "url", "health_url", "api_base", "data_dir",
                    "records_store", "audit_log", "web_dir", "first_run",
                    "seed_records", "argv", "note", "mode"):
            self.assertIn(key, app.bootstrap, f"bootstrap 行缺字段 {key}")
        self.assertEqual("local", app.bootstrap["mode"])
        self.assertGreater(app.bootstrap["port"], 0)
        self.assertTrue(app.bootstrap["first_run"])
        self.assertEqual(DEMO_COUNT, app.bootstrap["seed_records"])
        _status, _heads, health = app.http("GET", sv.API_PATH_HEALTH)
        self.assertEqual("ok", health["status"])
        self.assertIn(sv.API_PATH_IMPORT, health["endpoints"])
        self.assertTrue(health["records_store"]["durable"])
        self.assertEqual(str(app.data_dir / "records.jsonl"),
                         health["records_store"]["path"])
        # Electron 加载的就是这个地址：静态资源必须取到
        status, heads, text = app.http("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("text/html", heads.get("content-type", ""))
        self.assertIn("<html", str(text))
        for asset in ("/app.js", "/styles.css", "/i18n/embed.js", "/demo-data.js"):
            self.assertEqual(200, app.http("GET", asset)[0], asset)
        # 首启真的把状态文件写进 --data-dir（记录日志立刻落盘；审计链与设置
        # 是首次写条目时才建文件，所以下面产生决定之后再查那两个文件）
        self.assertTrue((app.data_dir / "records.jsonl").exists())
        # 人类 banner 不能被机器行吞掉：端点清单与实际监听地址要给终端上的人看
        text = app.stdout_text()
        self.assertIn(f"127.0.0.1:{app.port}", text, text[-800:])
        self.assertIn("endpoints:", text, "首启摘要要打印给终端上的人看")
        # 界面与打包验收以 GET /health 为准；bootstrap 行只说明这台进程绑哪个端口
        self.assertIn("/health", app.bootstrap["note"])

    @unittest.skipUnless(SPAWN_OK, f"沙箱不允许拉起子进程：{SPAWN_REASON}")
    def test_banner_survives_without_unbuffered_env(self) -> None:
        """产品侧自己逐行刷新：调用方忘了设 PYTHONUNBUFFERED 也读得到摘要。"""
        app = Sidecar("bufprobe", env=PIPE_ENV)
        self.sidecars.append(app)
        app.start()
        line = app.wait_for_line(r"endpoints:", timeout=15.0)
        self.assertIn("GET /health", line)

    def test_full_chain_records_intervene_audit_csv_sse(self) -> None:
        app = self.boot("chain")
        status, _heads, listing = app.http("GET", sv.API_PATH_RECORDS)
        self.assertEqual(200, status, listing)
        self.assertEqual(DEMO_COUNT, len(listing["records"]))
        self.assertTrue(all(r.get("demo_data") for r in listing["records"]),
                        "演示徽标由记录自带字段判定，不看界面")
        zones = [r["status"] for r in listing["records"]]
        self.assertEqual(3, zones.count("pending_confirmation"))
        self.assertGreaterEqual(len(PENDING_IDS), 2, "要够两条待确认做两次决定")
        self.assertTrue(listing["meta"]["synthetic"], "演示数据要在 meta 里标明是合成样例")
        # 干预一：同意（approve 不带参数）
        status, _heads, decided = app.http(
            "POST", sv.API_PATH_INTERVENE,
            {"action_id": PENDING_ID, "decision": "approve", "reason": "e2e 同意"})
        self.assertEqual(200, status, decided)
        self.assertEqual("approve", decided["user_intervention"]["decision"])
        self.assertTrue(decided["user_intervention"]["recorded"], "决定必须记为已落盘")
        after = app.http("GET", sv.API_PATH_RECORDS)[2]
        record = next(r for r in after["records"] if r["id"] == PENDING_ID)
        self.assertEqual("approved", record["status"])
        self.assertFalse(record["requires_action"], "决定之后不再要用户动作")
        # 干预二：改一个可编辑参数并同意（modify 必须带 modified_params + 对应 option）
        target_modify = PENDING_IDS[1]
        listing_now = app.http("GET", sv.API_PATH_RECORDS)[2]
        rec_modify = next(r for r in listing_now["records"] if r["id"] == target_modify)
        option_id, field_name, new_params = editable_param(rec_modify)
        status, _heads, modified = app.http(
            "POST", sv.API_PATH_INTERVENE,
            {"action_id": target_modify, "decision": "modify",
             "option_id": option_id, "modified_params": new_params,
             "reason": "e2e 改参数"})
        self.assertEqual(200, status, modified)
        self.assertEqual("modify", modified["user_intervention"]["decision"])
        echoed = modified["user_intervention"]["modified_params"]
        for key, value in new_params.items():
            self.assertEqual(value, echoed.get(key),
                             f"响应要回显改过的参数：{key}")
        self.assertIn(field_name, modified["user_intervention"]["original_params"])
        after = app.http("GET", sv.API_PATH_RECORDS)[2]
        record2 = next(r for r in after["records"] if r["id"] == target_modify)
        self.assertEqual("modified", record2["status"])
        stored2 = (record2.get("modified_params")
                   or record2["user_intervention"].get("modified_params") or {})
        for key, value in new_params.items():
            self.assertEqual(value, stored2.get(key),
                             f"改过的参数要落到记录上：{key}")
        self.assertIn(field_name, stored2)
        # 审计里查得到这次决定（条目只追加，count 来自文件本身）
        status, _heads, audit = app.http("GET", sv.API_PATH_AUDIT + "?limit=50")
        self.assertEqual(200, status, audit)
        self.assertGreater(audit["meta"]["count"], 0, audit["meta"])
        self.assertIn(PENDING_ID, json.dumps(audit, ensure_ascii=False),
                      "审计条目要指得回被干预的那条记录")
        # CSV 导出：真字节、带 BOM、带下载文件名
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=20)
        conn.request("GET", sv.API_PATH_AUDIT_EXPORT + "?format=csv&limit=200",
                     headers={"Connection": "close"})
        resp = conn.getresponse()
        raw = resp.read()
        heads = {str(k).lower(): v for k, v in resp.getheaders()}
        conn.close()
        self.assertEqual(200, resp.status)
        self.assertIn("text/csv", heads.get("content-type", ""))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 BOM，Excel 才不乱码")
        self.assertIn("attachment", heads.get("content-disposition", ""))
        self.assertIn(PENDING_ID, raw.decode("utf-8-sig"), "导出里要能找到被干预的那条")
        # SSE：hello + 决定事件（第三条待确认，避开前面两次决定）
        target = PENDING_IDS[2]
        seen = app.stream(frames=2, trigger=lambda: app.http(
            "POST", sv.API_PATH_INTERVENE,
            {"action_id": target, "decision": "reject", "reason": "e2e 第三条"}))
        self.assertIn("hello", seen, seen)
        self.assertIn("action.decision_recorded", seen, seen)
        # SSE：reset 事件 + reset 只清记录
        reset_seen = app.stream(frames=2, trigger=lambda: app.http("POST",
                                                                   sv.API_PATH_RESET, {}))
        self.assertIn("records.reset", reset_seen, reset_seen)
        self.assertEqual(DEMO_COUNT, len(app.http("GET", sv.API_PATH_RECORDS)[2]["records"]))

    def test_import_then_restart_keeps_records_and_decisions(self) -> None:
        app = self.boot("persist")
        status, _heads, imported = app.http(
            "POST", sv.API_PATH_IMPORT,
            {"payload": LANGFUSE_TEXT, "filename": "langfuse.json",
             "source_kind": "paste_json"})
        self.assertEqual(200, status, imported)
        self.assertEqual(LANGFUSE_COUNT, imported["imported"])
        self.assertTrue(imported["durable"])
        rec0 = next(r for r in app.http("GET", sv.API_PATH_RECORDS)[2]["records"]
                    if r["id"] == PENDING_ID)
        option_id, field_name, new_params = editable_param(rec0)
        status, _heads, decided = app.http(
            "POST", sv.API_PATH_INTERVENE,
            {"action_id": PENDING_ID, "decision": "modify", "option_id": option_id,
             "modified_params": new_params, "reason": "重启前的决定"})
        self.assertEqual(200, status, decided)
        first = app.http("GET", sv.API_PATH_RECORDS)[2]
        self.assertEqual(DEMO_COUNT + LANGFUSE_COUNT, len(first["records"]))
        self.assertEqual(LANGFUSE_COUNT, first["meta"]["imported_count"])
        self.assertTrue(first["meta"]["records_durable"])
        journal = app.data_dir / "records.jsonl"
        self.assertTrue(journal.exists())
        app.stop()
        # 同一个数据目录重启：桌面端"关掉窗口再打开"就是这条路
        again = self.boot("persist-restart", data_dir=app.data_dir)
        self.assertFalse(again.bootstrap["first_run"], "第二次启动不算首次")
        self.assertEqual(str(journal), again.bootstrap["records_store"])
        second = again.http("GET", sv.API_PATH_RECORDS)[2]
        self.assertEqual(DEMO_COUNT + LANGFUSE_COUNT, len(second["records"]),
                         "重启后记录不能回到演示 12 条")
        survivor = next(r for r in second["records"] if r["id"] == PENDING_ID)
        self.assertEqual("modified", survivor["status"])
        stored = (survivor.get("modified_params")
                  or survivor.get("user_intervention", {}).get("modified_params") or {})
        for key, value in new_params.items():
            self.assertEqual(value, stored.get(key),
                             f"用户改过的参数必须活过重启：{key}")
        self.assertEqual("modify", survivor["user_intervention"]["decision"])
        _status, _heads, health = again.http("GET", sv.API_PATH_HEALTH)
        self.assertEqual(0, health["records_store"]["journal"]["load_problems"])
        self.assertTrue(health["audit"]["ok"], health["audit"])
        self.assertEqual(200, again.http("GET", sv.API_PATH_AUDIT + "?limit=200")[0])

    def test_demo_none_starts_empty_and_demo_badge_stays_field_driven(self) -> None:
        app = self.boot("demo-none", demo="none")
        listing = app.http("GET", sv.API_PATH_RECORDS)[2]
        self.assertEqual(0, len(listing["records"]), "--demo none 真的从 0 条开始")
        self.assertEqual(0, listing["meta"]["count"])
        self.assertEqual(0, app.bootstrap["seed_records"])
        status, _heads, imported = app.http("POST", sv.API_PATH_IMPORT,
                                            {"payload": LANGFUSE_TEXT})
        self.assertEqual(200, status, imported)
        after = app.http("GET", sv.API_PATH_RECORDS)[2]
        self.assertEqual(LANGFUSE_COUNT, len(after["records"]))
        self.assertFalse(any(r.get("demo_data") for r in after["records"]),
                         "导入的记录不得带上演示徽标")

    def test_reset_keeps_audit_history(self) -> None:
        app = self.boot("reset-audit")
        self.assertEqual(200, app.http(
            "POST", sv.API_PATH_INTERVENE,
            {"action_id": PENDING_ID, "decision": "approve", "reason": "先产生审计"}
        )[0])
        before = app.http("GET", sv.API_PATH_AUDIT + "?limit=500")[2]["meta"]["count"]
        self.assertGreater(before, 0)
        self.assertEqual(200, app.http("POST", sv.API_PATH_RESET, {})[0])
        after = app.http("GET", sv.API_PATH_AUDIT + "?limit=500")[2]["meta"]["count"]
        self.assertGreaterEqual(after, before, "reset 只清记录，审计条目只增不减")
        self.assertTrue(app.http("GET", sv.API_PATH_HEALTH)[2]["audit"]["ok"])
        self.assertTrue((app.data_dir / "audit.jsonl").exists())

    def test_team_mode_requires_login_and_password_printed_once(self) -> None:
        app = self.boot("team", "--mode", "team")
        self.assertEqual("team", app.bootstrap["mode"])
        status, _heads, body = app.http("GET", sv.API_PATH_RECORDS)
        self.assertEqual(401, status, body)
        self.assertIn("AUTH_REQUIRED", json.dumps(body, ensure_ascii=False))
        line = app.wait_for_line(r"初始口令")
        password = line.split("初始口令", 1)[1].strip().split()[0]
        self.assertGreaterEqual(len(password), 8, password)
        session = app.login_admin(password)
        status, _heads, listing = app.http("GET", sv.API_PATH_RECORDS,
                                           headers={"Cookie": session["cookie"]})
        self.assertEqual(200, status, listing)
        self.assertEqual(DEMO_COUNT, len(listing["records"]))
        status, _heads, denied = app.http("POST", sv.API_PATH_IMPORT,
                                         {"payload": LANGFUSE_TEXT},
                                         headers={"Cookie": session["cookie"]})
        self.assertEqual(403, status, "带 cookie 的写操作缺 CSRF 头必须被挡")
        self.assertIn("CSRF", json.dumps(denied, ensure_ascii=False))
        status, _heads, ok = app.http(
            "POST", sv.API_PATH_IMPORT, {"payload": LANGFUSE_TEXT},
            headers={"Cookie": session["cookie"], au.CSRF_HEADER: session["csrf"]})
        self.assertEqual(200, status, ok)
        self.assertEqual(LANGFUSE_COUNT, ok["imported"])

    def test_two_processes_share_one_journal(self) -> None:
        first = self.boot("share-a")
        shared = first.data_dir
        status, _heads, imported = first.http("POST", sv.API_PATH_IMPORT,
                                             {"payload": LANGFUSE_TEXT,
                                              "source_kind": "upload"})
        self.assertEqual(200, status, imported)
        peer = self.boot("share-b", data_dir=shared)
        seen = peer.http("GET", sv.API_PATH_RECORDS)[2]
        self.assertEqual(DEMO_COUNT + LANGFUSE_COUNT, len(seen["records"]),
                         "另一个进程要立刻看见同一份日志")
        self.assertEqual(str(shared / "records.jsonl"), peer.bootstrap["records_store"])


# --------------------------------------------------------------------------- #
# C. Electron 壳的接线（Electron 没装也能核对的静态契约）
# --------------------------------------------------------------------------- #
class DesktopShellTests(unittest.TestCase):
    """desktop/：拉 sidecar、等健康检查后加载 UI、单实例锁、退出清理。"""

    def main_js(self) -> str:
        return (DESKTOP / "main.js").read_text(encoding="utf-8")

    def package(self) -> dict:
        return json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))

    def builder_config(self) -> str:
        return (DESKTOP / "electron-builder.yml").read_text(encoding="utf-8")

    def test_files_exist(self) -> None:
        for name in ("main.js", "preload.js", "package.json", "electron-builder.yml",
                     "sidecar.js", "README.md"):
            self.assertTrue((DESKTOP / name).exists(), f"desktop/{name} 缺失")

    def test_package_json_wiring(self) -> None:
        pkg = self.package()
        self.assertEqual("main.js", pkg.get("main"))
        self.assertTrue(pkg.get("private"), "桌面壳不发布到 npm")
        scripts = pkg.get("scripts") or {}
        self.assertEqual("electron .", scripts.get("start"))
        self.assertIn("electron", (pkg.get("devDependencies") or {}))
        self.assertIn("electron-builder", (pkg.get("devDependencies") or {}))
        self.assertFalse(pkg.get("dependencies"),
                         "运行时依赖必须为空：产品本体是 Python sidecar")

    def test_main_js_starts_sidecar_then_loads_ui_after_health(self) -> None:
        text = self.main_js() + (DESKTOP / "sidecar.js").read_text(encoding="utf-8")
        self.assertIn("requestSingleInstanceLock", text, "单实例锁")
        self.assertIn("second-instance", text, "二次启动要把已有窗口唤到前台")
        self.assertIn("--sidecar", text, "开发与打包模式走同一条 sidecar 命令")
        self.assertIn("spawn", text)
        self.assertIn(entry.BOOTSTRAP_KEY, text, "必须读 bootstrap 行拿端口")
        self.assertIn(sv.API_PATH_HEALTH, text, "加载 UI 之前要等健康检查")
        self.assertIn("will-quit", text, "退出时清理子进程")
        self.assertIn("window-all-closed", text)
        self.assertIn("data-dir", text, "用户数据目录必须交给 sidecar")
        self.assertIn("contextIsolation: true", self.main_js())
        self.assertIn("nodeIntegration: false", self.main_js())
        self.assertIn("sandbox: true", self.main_js())
        self.assertNotIn("http://", text.replace("http://127.0.0.1", ""),
                         "只允许回环地址，不得有外部出网点")

    def test_preload_exposes_a_minimal_surface(self) -> None:
        text = (DESKTOP / "preload.js").read_text(encoding="utf-8")
        self.assertIn("contextBridge.exposeInMainWorld", text)
        self.assertEqual(1, text.count("require("),
                         "preload 只允许 require('electron')，别的 Node 能力一律不外泄")
        self.assertIn("require('electron')", text)
        for banned in ("child_process", "fs", "net", "http", "node:", "eval("):
            self.assertNotIn(banned, text, f"preload 里出现了 {banned}")
        self.assertIn("invoke", text, "只暴露白名单 IPC")

    def test_builder_config_produces_both_targets_without_fake_signing(self) -> None:
        text = self.builder_config()
        self.assertIn("nsis", text)
        self.assertIn("portable", text)
        self.assertIn("extraResources", text, "sidecar exe 要进 extraResources")
        self.assertIn("forceCodeSigning: false", text,
                      "自签名阶段不得让构建因缺签名而失败")
        self.assertNotIn("signtoolOptions", text,
                         "不在构建内签名：签名由 tools/sign_and_hash.ps1 事后做")

    def test_sidecar_js_picks_python_in_dev_and_exe_in_production(self) -> None:
        text = (DESKTOP / "sidecar.js").read_text(encoding="utf-8")
        self.assertIn("resourcesPath", text, "打包模式用 resources 里的 exe")
        self.assertIn("agent-explainer-sidecar", text)
        self.assertIn("python", text.lower(), "开发模式用系统 Python")
        self.assertIn("TRACELNS_SIDECAR", text, "环境变量兜底，双保险")

    def test_selfcheck_mode_is_scriptable(self) -> None:
        """无人值守验收：一行 JSON + 退出码，且能从桌面 Shell 直接拉起时把结果写文件。"""
        text = self.main_js()
        self.assertIn("tracelens_desktop_selfcheck", text)
        self.assertIn("--selfcheck", text)
        self.assertIn("app.exit", text, "验收要有退出码，不靠人看窗口")
        for name in ("selfcheck-out", "data-dir", "python", "mode", "demo", "port"):
            self.assertIn(f"flag('{name}')", text, f"缺命令行开关 --{name}")
        self.assertIn("appendFileSync", text, "stdout 抓不到时验收结果要落文件")
        self.assertIn("performance.now", text)


# --------------------------------------------------------------------------- #
# D. 打包产物：有就真验，没有就如实 skip（原因写进 skip 理由）
# --------------------------------------------------------------------------- #
@unittest.skipUnless(SPAWN_OK, f"沙箱不允许拉起子进程：{SPAWN_REASON}")
class ArtifactTests(unittest.TestCase):
    """SHA256SUMS 逐条重算、签名状态如实核对、sidecar exe 真跑一次。"""

    def test_sha256sums_verify(self) -> None:
        if not SUMS.exists():
            self.skipTest(f"未产出 SHA256SUMS（{SUMS}）：安装包未构建，"
                          f"原因见 docs/05.3-release-checklist.md 的阻塞记录")
        lines = [ln for ln in SUMS.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            digest, _, name = line.partition("  ")
            target = DIST / name.strip()
            self.assertTrue(target.exists(), f"SHA256SUMS 里的文件不存在：{target}")
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(digest.lower(), actual.lower(), f"{name} 校验不匹配")

    def test_signature_status_is_honest(self) -> None:
        if not SIGN_REPORT.exists():
            self.skipTest("未产出签名状态文件：签名脚本未跑（见发布清单的阻塞记录）")
        doc = json.loads(SIGN_REPORT.read_text(encoding="utf-8"))
        self.assertIn("certificate", doc)
        self.assertIn("trust_note", doc, "必须写明自签名不受信任")
        self.assertIn("自签名", json.dumps(doc, ensure_ascii=False))
        for item in doc.get("artifacts", []):
            self.assertTrue(pathlib.Path(item["path"]).exists(), item["path"])
            self.assertIn(item["status"], ("Valid", "NotSigned", "HashMismatch",
                                           "NotTrusted", "UnknownError", "NotSupported"),
                          f"签名状态取值不真实：{item}")
        # 自相矛盾检查（PowerShell 5.1 的 `.Count` 在单元素时是 $null，踩过坑）：
        # signed_count 必须等于真正拿到签名的产物数，unsigned 必须与它一致。
        signed = [a for a in doc.get("artifacts", [])
                  if a.get("status") in ("Valid", "NotTrusted")]
        if "signed_count" in doc:
            self.assertEqual(len(signed), doc["signed_count"],
                             f"signed_count 与产物状态自相矛盾：{doc['signed_count']} vs {signed}")
        if "artifact_count" in doc:
            self.assertEqual(len(doc.get("artifacts", [])), doc["artifact_count"])
        if doc.get("artifacts"):
            self.assertEqual(bool(not signed), bool(doc.get("unsigned")),
                             "有签名产物却记 unsigned=true（或反之）= 报告不可信")

    def test_sidecar_exe_runs(self) -> None:
        if not SIDECAR_EXE.exists():
            self.skipTest(f"未产出 sidecar exe（{SIDECAR_EXE}）：PyInstaller 构建未成功，"
                          f"原因见 tools/build_sidecar.py --check 的输出")
        app = Sidecar("exe")
        # 数据目录故意指向一个**还不存在的子目录**：真实安装场景里 Electron 传进去的
        # userData/data 也是不存在的，exe 必须能自己把它建出来并落下记录。
        # （t12 的产物在 Low IL 沙箱里建不出目录：WinError 5 —— 这条断言就是钉住它。）
        fresh = app.data_dir / "product-created"
        self.assertFalse(fresh.exists(), "测试自己不能先把目录建出来")
        # F4：打包 exe 的默认账本根是 <应用根>/var/features（不跟随 --data-dir），
        # 所以这里显式把总裁办账本钉进本用例的临时目录，并在跑前/跑后逐字节核一遍
        # 仓库 var/features —— 写回来就红，不再靠人肉统计字节数。
        feats = app.data_dir.parent / "features-exe"
        app.cmd = [str(SIDECAR_EXE), "--sidecar", "--port", "0", "--demo", "zh",
                   "--data-dir", str(fresh), "--features", str(feats)]
        repo_ledger = _ledger_fingerprint(REPO / "var" / "features")
        app.start(timeout=120.0)
        self.sidecars = [app]
        self.assertEqual(200, app.http("GET", sv.API_PATH_HEALTH)[0])
        self.assertEqual(DEMO_COUNT, len(app.http("GET", sv.API_PATH_RECORDS)[2]["records"]))
        _status, _heads, health = app.http("GET", sv.API_PATH_HEALTH)
        self.assertTrue(health["audit"]["ok"], health["audit"])
        for asset in ("/", "/app.js", "/styles.css"):
            self.assertEqual(200, app.http("GET", asset)[0],
                             f"打包产物里缺界面资源：{asset}")
        self.assertTrue(fresh.is_dir(), "exe 没能创建 --data-dir 指定的数据目录")
        self.assertTrue((fresh / "records.jsonl").exists(),
                        "exe 创建的数据目录里没有落下记录日志（真实用户重启会丢数据）")
        app.stop()
        self.assertEqual(repo_ledger, _ledger_fingerprint(REPO / "var" / "features"),
                         "打包 exe 把总裁办账本写进了仓库 var/features：--features 没生效")

    def test_builder_check_reports_the_real_reason(self) -> None:
        """`tools/build_sidecar.py --check` 必须能跑，并如实说明能不能构建。"""
        done = subprocess.run([sys.executable, "-X", "utf8",
                               str(REPO / "tools" / "build_sidecar.py"), "--check"],
                              cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
        text = (done.stdout or "") + (done.stderr or "")
        self.assertIn("PyInstaller", text, text[:600])
        if done.returncode == 0:
            self.assertIn("可用", text)
        else:
            self.assertIn("不可用", text)
            self.assertTrue(SIDECAR_EXE.exists(),
                            "构建不可用且没有现成 exe 时 --check 必须非零（不许假绿）")


if __name__ == "__main__":     # pragma: no cover
    unittest.main(verbosity=2)
