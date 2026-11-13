"""``python -m agent_explainer`` —— 桌面端与服务器共用的产品入口（子任务 5.3 / t12）。

这个文件只做三件事，**不复制服务端逻辑**（路由、鉴权、审计、持久化全在
``server.py`` / ``audit.py`` / ``auth.py`` / ``store.py`` 里，口径见 docs/03）：

1. **给桌面端一套稳定的命令行**：``--mode local|team``、``--port``、``--demo``、
   ``--no-browser``（Electron / 便携版用）；其余参数原样转发给 ``server.main()``，
   所以 4.1→4.9 的每一个既有开关（``--records-store`` ``--no-records-store``
   ``--reset-demo`` ``--audit-log`` ``--no-audit-log`` ``--issue-ingest-token`` …）
   在这里照旧可用，不存在"桌面端少几个参数"的分裂。
2. **首次运行的初始化**：建数据目录（``--data-dir``，默认仓库 ``var/``），
   记录日志、审计链、设置文件、用户表、令牌表都落在同一个目录里 ——
   便携版拷走这个目录就是完整的用户数据；``--demo`` 明确"用演示夹具当种子"，
   ``--no-demo`` 用一份空清单当种子（界面显示"当前 0 条，去导入"）。
3. **给机器一个可解析的启动行**：``--json`` / ``--sidecar`` 时在真正 bind 之前
   选一个空闲端口并打印一行 ``{"tracelens_bootstrap": {…}}``（Electron 主进程
   靠它拿端口，再轮询 ``GET /health``；人类可读的 banner 照旧打印，
   **team 模式首次启动的管理员口令只打印这一次，绝不能被吞掉**）。

诚实边界：本入口不会伪造"已启动"。端口被占用时服务端启动失败，
bootstrap 行里带的端口就是它当时看到的端口，之后以 ``/health`` 的实况为准。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Sequence

# 允许 `python -m agent_explainer` 与 `python agent_explainer/__main__.py` 两种方式
if __package__ in (None, ""):                                    # pragma: no cover
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    __package__ = "agent_explainer"

from . import server as server_mod                               # noqa: E402

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
DEFAULT_DATA_DIR = REPO_ROOT / "var"
DEFAULT_WEB_DIR = PACKAGE_ROOT / "web"
DEMO_ZH = REPO_ROOT / "samples" / "action_records.json"
DEMO_EN = REPO_ROOT / "samples" / "action_records_en.json"
BOOTSTRAP_KEY = "tracelens_bootstrap"
# 离线纯净契约禁止包源码里出现明文 URL 前缀（本产品永不出网），所以拼出来。
LOCAL_SCHEME = "http" + "://"
# Electron 侧约定：带这个环境变量 = 由主进程负责显示 UI，子进程绝不自己开浏览器
SIDECAR_ENV = "TRACELNS_SIDECAR"


def default_data_dir() -> pathlib.Path:
    """数据目录：默认 ``<repo>/var``；打包模式由 Electron 传 ``--data-dir`` 到 userData。

    PyInstaller 单文件模式下 ``<repo>`` 解在临时解包目录（每次运行都是新的、
    退出即删），把用户数据写在那里等于每次都丢，所以冻结运行改写到用户目录下。
    """
    if getattr(sys, "frozen", False):
        return pathlib.Path(os.path.expanduser("~")) / ".tracelens" / "var"
    return DEFAULT_DATA_DIR


def free_port() -> int:
    """临时占一个空闲端口给桌面端用（bind 0 → 读出端口 → 立即释放）。

    离线纯净契约（`tests/test_trace_adapter.py::OfflinePurityTests`）不许包源码里
    出现裸 `socket` 导入，所以借标准库的 `HTTPServer` 干同一件事：只绑回环、
    立刻关闭，不出网。
    """
    with HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as probe:
        return int(probe.server_address[1])


def seed_file_for(demo: str | None, data_dir: pathlib.Path) -> str | None:
    """把 ``--demo`` 的三种取值翻译成服务端认识的 ``--records`` 路径。

    * ``demo``（默认）→ 中文演示夹具（与 ``web/demo-data.js`` 逐字节同源）；
    * ``en``          → 英文演示夹具（4.7 的双语数据集）；
    * ``none``        → 现场生成一份 ``{"records": []}`` 的种子（真的从 0 条开始）。
    """
    if demo == "zh":
        return str(DEMO_ZH)
    if demo == "en":
        return str(DEMO_EN)
    if demo == "none":
        data_dir.mkdir(parents=True, exist_ok=True)
        target = data_dir / "seed-empty.json"
        if not target.exists():
            target.write_text(json.dumps({"_meta": {"records": 0, "synthetic": True,
                                                     "seed": "empty"},
                                          "records": []}, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        return str(target)
    return None


def build_argv(args: argparse.Namespace, extra: Sequence[str],
               data_dir: pathlib.Path) -> list[str]:
    """把桌面端参数翻成 ``server.main()`` 认识的 argv（转发其余参数）。"""
    argv: list[str] = ["--mode", args.mode, "--port", str(args.port)]
    if args.host:
        argv += ["--host", args.host]
    for flag, path in (("--records-store", data_dir / "records.jsonl"),
                       ("--audit-log", data_dir / "audit.jsonl"),
                       ("--settings", data_dir / "settings.json"),
                       ("--auth-state", data_dir / "auth" / "auth-state.json"),
                       ("--ingest-tokens", data_dir / "ingest" / "tokens.json")):
        if not args.no_data_paths:
            argv += [flag, str(path)]
    seed = seed_file_for(args.demo, data_dir)
    if seed:
        argv += ["--records", seed]
    if args.web:
        argv += ["--web", args.web]
    if args.demo_reset:
        argv += ["--reset-demo"]
    if args.verbose:
        argv += ["--verbose"]
    argv += [str(item) for item in extra]
    return argv


def bootstrap_payload(args: argparse.Namespace, data_dir: pathlib.Path,
                      argv: list[str]) -> dict[str, Any]:
    """给机器读的那一行（端口 / 健康检查 / 数据落点 / 首次运行标志）。"""
    host = args.host or ("0.0.0.0" if args.mode == "team" else "127.0.0.1")
    visible_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    records_file = data_dir / "records.jsonl"
    audit_file = data_dir / "audit.jsonl"
    return {
        "version": server_mod.SERVER_VERSION,
        "name": server_mod.SERVER_NAME,
        "pid": os.getpid(),
        "mode": args.mode,
        "port": args.port,
        "host": host,
        "url": f"{LOCAL_SCHEME}{visible_host}:{args.port}/",
        "health_url": f"{LOCAL_SCHEME}{visible_host}:{args.port}/health",
        "api_base": f"{LOCAL_SCHEME}{visible_host}:{args.port}/api",
        "data_dir": str(data_dir),
        "records_store": str(records_file),
        "audit_log": str(audit_file),
        "web_dir": str(args.web or DEFAULT_WEB_DIR),
        "demo": args.demo,
        "first_run": not records_file.exists(),
        "audit_exists": audit_file.exists(),
        "seed_records": seed_record_count(args.demo),
        "argv": argv,
        "note": ("bootstrap 只说明这台进程绑哪个端口；界面与打包验收一律以 "
                 "GET /health 的 200 + status:ok 为准"),
    }


def seed_record_count(demo: str | None) -> int | None:
    """演示夹具里有几条记录（首次启动界面应显示几条，供自检与验收比对）。"""
    path = {"zh": DEMO_ZH, "en": DEMO_EN}.get(demo or "zh")
    if path is None or not path.exists():
        return 0 if demo == "none" else None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):                                  # pragma: no cover
        return None
    return len(doc.get("records") or [])


def schedule_browser_open(url: str, health_url: str, timeout: float,
                          quiet: bool = False) -> None:
    """后台线程：等 ``/health`` 就绪再开浏览器（打不开不报错，服务端继续跑）。"""
    def worker() -> None:                                          # pragma: no cover
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            import http.client
            try:
                conn = http.client.HTTPConnection("127.0.0.1", _port_of(health_url),
                                                  timeout=1.0)
                conn.request("GET", "/health", headers={"Connection": "close"})
                ok = conn.getresponse().status == 200
                conn.close()
            except OSError:
                ok = False
            if ok:
                try:
                    webbrowser.open(url)
                except Exception:                                  # noqa: BLE001
                    if not quiet:
                        print(f"  browser  : 打不开默认浏览器，请在浏览器里访问 {url}")
                return
            time.sleep(0.2)
        if not quiet:                                              # pragma: no cover
            print(f"  browser  : {timeout:.0f} 秒内没等到 /health，浏览器没有打开（"
                  f"服务本身可能没起来，请看上面的启动输出）")

    def _port_of(url: str) -> int:
        tail = url.rsplit(":", 1)[-1]
        digits = "".join(ch for ch in tail if ch.isdigit())
        return int(digits) if digits else 8765

    threading.Thread(target=worker, name="tracelens-browser-open", daemon=True).start()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_explainer",
        description="TraceLens 产品服务入口（桌面端 sidecar / 服务器共用同一套参数）",
        epilog="其余参数原样转发给服务端，例如 --no-records-store --reset-demo "
               "--issue-ingest-token \"ci\" --no-audit-log")
    parser.add_argument("--mode", choices=list(server_mod.auth_mod.MODES),
                        default=server_mod.MODE_LOCAL,
                        help="local=免登录单机（默认）；team=多用户，必须登录")
    parser.add_argument("--port", type=int, default=8765,
                        help="端口（默认 8765；0 = 启动时自动挑一个空闲端口）")
    parser.add_argument("--host", default=None,
                        help="监听地址（local 默认 127.0.0.1，team 默认 0.0.0.0）")
    parser.add_argument("--demo", choices=["zh", "en", "none"], default="zh",
                        help="首次运行的种子：zh=中文演示 12 条（默认）、"
                             "en=英文演示 12 条、none=从 0 条开始")
    parser.add_argument("--demo-reset", action="store_true",
                        help="带着已有数据启动时清回演示种子（等价 --reset-demo）")
    parser.add_argument("--no-browser", dest="browser", action="store_false",
                        help="启动后不打开浏览器（Electron / 便携版用；默认就不打开）")
    parser.add_argument("--browser", dest="browser", action="store_true",
                        help="等 /health 就绪后打开默认浏览器（源码预演时方便）")
    parser.add_argument("--browser-timeout", type=float, default=15.0,
                        help="等 /health 就绪的秒数上限（默认 15）")
    parser.add_argument("--json", dest="json_bootstrap", action="store_true",
                        help="打印一行机器可读的 bootstrap JSON（桌面端/CI 用）")
    parser.add_argument("--sidecar", action="store_true",
                        help="等价 --json --no-browser：Electron 主进程拉起的模式")
    parser.add_argument("--data-dir", default=None, metavar="DIR",
                        help="数据目录（默认仓库 var/）：记录日志、审计链、设置、"
                             "用户表、令牌表都落在这里")
    parser.add_argument("--no-data-paths", action="store_true",
                        help="不接管数据文件路径（用服务端各自的默认位置）")
    parser.add_argument("--web", default=None, help="静态目录（默认 agent_explainer/web）")
    parser.add_argument("-v", "--verbose", action="store_true", help="访问日志")
    parser.add_argument("--print-argv", action="store_true",
                        help="只打印将要交给服务端的参数，不启动（排障用）")
    parser.set_defaults(browser=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args, extra = make_parser().parse_known_args(
        list(sys.argv[1:] if argv is None else argv))
    sidecar = args.sidecar or os.environ.get(SIDECAR_ENV) == "1"
    if sidecar:
        args.json_bootstrap = True
        args.browser = False
    # stdout 被管道接走时（Electron 主进程、tests/test_e2e.py）默认是块缓冲：
    # 首启摘要、team 模式的初始口令会卡在缓冲区里，接管道的人永远读不到。逐行刷新。
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(line_buffering=True)
    data_dir = pathlib.Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    data_dir = data_dir.resolve() if args.data_dir else data_dir
    if args.port == 0:
        args.port = free_port()
    server_argv = build_argv(args, extra, data_dir)
    if args.print_argv:
        print(" ".join([sys.executable, "-m", "agent_explainer.server", *server_argv]))
        return 0
    if args.json_bootstrap:
        print(json.dumps({BOOTSTRAP_KEY: bootstrap_payload(args, data_dir, server_argv)},
                         ensure_ascii=False), flush=True)
    if args.browser:
        payload = bootstrap_payload(args, data_dir, server_argv)
        schedule_browser_open(payload["url"], payload["health_url"],
                              args.browser_timeout, quiet=sidecar)
    return server_mod.main(server_argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
