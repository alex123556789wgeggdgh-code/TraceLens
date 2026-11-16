"""PyInstaller sidecar 构建脚本（子任务 5.3 / t12）。

桌面端要的是"双击就能跑"，所以产品服务必须打成一个**不依赖用户装 Python** 的
单文件 exe。这个脚本做四件事，并且**每件事都如实报告**：

* `--check`：报告这台机器能不能构建（Python 版本、PyInstaller 是否可用、
  产物会落在哪、完整可复现命令）。构建条件不满足时**非零退出**并说清原因，
  绝不打印"构建成功"。
* `--install`：把 PyInstaller 装进一个隔离目录（默认系统临时目录下的
  ``tracelens-build/pyi``），**不污染**仓库、也不写系统 site-packages。
* `--build`：跑 PyInstaller 单文件构建，产出
  ``desktop/build/resources/sidecar/agent-explainer-sidecar.exe``，
  用 ``--add-data`` 打进界面（``agent_explainer/web``）、演示夹具
  （``samples/``）、提示词（``agent_explainer/prompts``），顺手跑一次
  ``--print-argv`` 冒烟，证明 exe 真的能 import 全部模块。
* `--portable-zip`：产出**源码便携版**压缩包（``agent_explainer`` + ``samples``
  + 启动脚本 + 说明），这是"没有 Electron / 没有 exe 时也能拷走就跑"的那条路，
  命名与说明都不冒"安装包"的名。

用法：

    python tools/build_sidecar.py --check
    python tools/build_sidecar.py --install --build
    python tools/build_sidecar.py --portable-zip
    python tools/build_sidecar.py --build --no-reportlab
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = REPO / "agent_explainer"
SAMPLES = REPO / "samples"
WEB = PACKAGE / "web"
PROMPTS = PACKAGE / "prompts"
DESKTOP = REPO / "desktop"
RESOURCES = DESKTOP / "build" / "resources"
SIDECAR_DIR = RESOURCES / "sidecar"
DIST = DESKTOP / "build" / "dist"
SIDECAR_NAME = "agent-explainer-sidecar"
EXE_NAME = f"{SIDECAR_NAME}.exe"
# 单文件模式（任务书要求的形态）与单目录模式（PyInstaller 官方推荐：启动更快、
# 不解压到临时目录、杀毒软件友好）。两种都支持，产物落点不同，检查时都会报。
EXE_ONEFILE = SIDECAR_DIR / EXE_NAME
EXE_ONEDIR = SIDECAR_DIR / SIDECAR_NAME / EXE_NAME
EXE_PATH = EXE_ONEFILE
BUILD_INFO = SIDECAR_DIR / "BUILD-INFO.json"
PORTABLE_NAME = "TraceLens-{version}-win-x64-portable-src.zip"
ENTRY_NAME = "sidecar-entry.py"
# 便携包命名用的版本号。**产品口径的唯一来源是 agent_explainer/server.py:SERVER_VERSION**；
# 这里只是脚本默认值（0.5.0 起与之一致，`--check` 会当场比对并在不一致时报警）。
DEFAULT_VERSION = "0.5.0"
# hiddenimports 的兜底清单：正常路径由 `package_modules()` 扫盘生成，
# 这个集合用来**断言扫全了**（0.5.0 之前这里是唯一清单，只有 8 个模块，
# 5.0 新增的 secretariat / crypto_sig / feature_store / groups / qr / rollback /
# sharing 全部漏掉，静态分析在动态导入处兜不住 → exe 运行时 ModuleNotFoundError）。
REQUIRED_PACKAGE_MODULES = (
    "agent_explainer.audit",
    "agent_explainer.auth",
    "agent_explainer.crypto_sig",
    "agent_explainer.explanations",
    "agent_explainer.feature_store",
    "agent_explainer.groups",
    "agent_explainer.ingest",
    "agent_explainer.qr",
    "agent_explainer.rollback",
    "agent_explainer.secretariat",
    "agent_explainer.server",
    "agent_explainer.settings",
    "agent_explainer.sharing",
    "agent_explainer.store",
    "agent_explainer.trace_adapter",
)


def package_modules() -> list[str]:
    """扫盘枚举 `agent_explainer` 包里全部可导入模块（含 `sdk/` 子包）。

    返回形如 `["agent_explainer", "agent_explainer.__main__", "agent_explainer.audit",
    "agent_explainer.sdk.client", …]`，逐条喂给 PyInstaller `--hidden-import`。
    不靠手写清单：新增模块自动进包，少一个就当场报错。
    """
    names: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    missing = [m for m in REQUIRED_PACKAGE_MODULES if m not in names]
    if missing:
        raise SystemExit(f"构建中止：agent_explainer 包扫描缺模块 {missing}"
                         f"（扫到 {len(names)} 个；PACKAGE={PACKAGE}）")
    return names


def server_version_in_code() -> str | None:
    """正则读 `agent_explainer/server.py` 里的 `SERVER_VERSION`（不 import，纯静态）。"""
    try:
        text = (PACKAGE / "server.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^SERVER_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else None



def find_exe() -> tuple[pathlib.Path | None, str]:
    """返回当前存在的 sidecar exe（优先单文件，其次单目录）。"""
    if EXE_ONEFILE.exists():
        return EXE_ONEFILE, "onefile"
    if EXE_ONEDIR.exists():
        return EXE_ONEDIR, "onedir"
    return None, "none"


def default_workdir() -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / "tracelens-build"


def pyinstaller_version() -> str | None:
    try:
        import PyInstaller

        return str(PyInstaller.__version__)
    except Exception:                                          # noqa: BLE001
        return None


def importable() -> tuple[bool, str]:
    """当前解释器能不能 import PyInstaller（含 sys.path 上已装好的隔离目录）。"""
    version = pyinstaller_version()
    if version:
        return True, version
    return False, "PyInstaller 不可用（未安装，或 Python 版本不被支持）"


def ensure_on_path(target: pathlib.Path) -> None:
    if target.exists() and str(target) not in sys.path:
        sys.path.insert(0, str(target))


def report_env(pip_target: pathlib.Path) -> dict[str, Any]:
    ok, detail = importable()
    exe, layout = find_exe()
    facts = {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "pyinstaller_available": ok,
        "pyinstaller_detail": detail,
        "pip_target": str(pip_target),
        "pip_target_exists": pip_target.exists(),
        "reportlab_available": module_available("reportlab"),
        "exe_onefile": f"{EXE_ONEFILE} 存在={EXE_ONEFILE.exists()}",
        "exe_onedir": f"{EXE_ONEDIR} 存在={EXE_ONEDIR.exists()}",
        "exe_present_layout": layout,
        "web_dir_exists": WEB.exists(),
        "samples_exists": SAMPLES.exists(),
        "prompts_exists": PROMPTS.exists(),
    }
    return facts


def module_available(name: str) -> bool:
    try:
        import importlib.util

        return bool(importlib.util.find_spec(name))
    except Exception:                                          # noqa: BLE001
        return False


def print_check(pip_target: pathlib.Path) -> int:
    facts = report_env(pip_target)
    exe, layout = find_exe()
    print("=== PyInstaller 构建环境检查（tools/build_sidecar.py --check）===")
    for key, value in facts.items():
        print(f"  {key:26s} = {value}")
    print("  单文件产物落点               =", EXE_ONEFILE)
    print("  单目录产物落点               =", EXE_ONEDIR)
    print("  便携 zip 落点               =", DIST / PORTABLE_NAME.format(version=DEFAULT_VERSION))
    code_version = server_version_in_code()
    print("  版本号口径                     =", f"--version 默认 {DEFAULT_VERSION} / "
                                               f"server.py SERVER_VERSION {code_version}")
    if code_version != DEFAULT_VERSION:
        print(f"  ⚠ 版本口径不一致：脚本默认 {DEFAULT_VERSION}，产品 {code_version}。"
              "打包产物的名字会和 `/health` 上报的号对不上，先统一再打包。")
    modules = package_modules()
    print("  hiddenimports（扫盘全量）      =", f"{len(modules)} 个模块",
          f"（含 5.0 总裁办：{'agent_explainer.secretariat' in modules}）")
    print("  可复现命令：")
    print(f"    {sys.executable} tools/build_sidecar.py --vendor --build")
    print(f"    {sys.executable} tools/build_sidecar.py --vendor --build --onedir")
    print(f"    {sys.executable} tools/build_sidecar.py --portable-zip")
    ok, detail = facts["pyinstaller_available"], facts["pyinstaller_detail"]
    if not ok:
        print(f"  结论：构建**不可用** —— {detail}")
        print("  原因常见两种：本机没装 PyInstaller；或当前 Python 版本太新，"
              "PyInstaller 还不支持（此时不得谎报已打包，见 docs/05.3-release-checklist.md）。")
        if exe is not None:
            print(f"  现成产物仍在（{layout}）：{exe}（可用于冒烟，不代表本次构建成功）")
            return 0
        return 1
    print(f"  结论：构建**可用**（PyInstaller {detail}，Python {facts['python_version']}）")
    return 0


def install_pyinstaller(pip_target: pathlib.Path, index: str | None) -> int:
    """装进隔离目录：不写 site-packages，不污染仓库，删目录即卸载。

    先走 pip；沙箱里 pip 常在 ``pip-unpack-*`` 临时目录上因权限失败，
    这时退到**标准库自带的离线轮子安装**（下载 + ``zipfile`` 解压）。
    两条路都失败才报失败，并把真实原因原样打出来。
    """
    pip_target.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(pip_target),
           "--disable-pip-version-check", "--no-cache-dir", "pyinstaller"]
    if index:
        cmd += ["--index-url", index]
    print("→", " ".join(cmd))
    done = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    tail = "\n".join((done.stdout or "").splitlines()[-8:])
    if tail:
        print(tail)
    if done.returncode != 0:
        for line in (done.stderr or done.stdout or "").splitlines():
            if line.strip().startswith("ERROR"):
                print(line.strip()[:300])
        print(f"pip 退出码 {done.returncode} —— 改走标准库离线轮子安装（不依赖 pip）")
    ensure_on_path(pip_target)
    ok, detail = importable()
    if ok:
        print(f"PyInstaller 安装完成：{detail}")
        return 0
    code = vendor_wheels(pip_target, index)
    if code == 0:
        return 0
    print("PyInstaller 装不上（pip 与离线轮子两条路都失败）：不得谎报已打包，"
          "见 docs/05.3-release-checklist.md 的阻塞记录。")
    return code


# --------------------------------------------------------------------------- #
# 离线轮子安装（标准库）：pip 在沙箱里解包失败时的第二条路
# --------------------------------------------------------------------------- #
PYI_DEPS = ("pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
            "packaging", "pefile", "pywin32-ctypes", "setuptools")
WHEEL_SUFFIXES = ("-none-any.whl", "-none-win_amd64.whl")


def pypi_json(name: str, index: str | None) -> dict[str, Any]:
    import urllib.request

    base = (index or "https://pypi.org").rstrip("/")
    req = urllib.request.Request(f"{base}/pypi/{name}/json",
                                 headers={"User-Agent": "tracelens-build/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_wheel(doc: dict[str, Any], index: str | None) -> tuple[str, str]:
    """只接受纯 Python 轮子与本平台的 ``-none-`` 轮子（不要 sdist，不要旧 ABI）。"""
    version = doc["info"]["version"]
    for item in doc["releases"].get(version) or []:
        name = str(item.get("filename") or "")
        if name.endswith(WHEEL_SUFFIXES):
            url = str(item.get("url") or "")
            if not url and index:
                url = f"{index}/{name}"
            return name, url
    raise RuntimeError(f"找不到 {doc['info']['name']} {version} 的可用轮子")


def vendor_wheels(target: pathlib.Path, index: str | None) -> int:
    """下载 + 解压轮子到隔离目录（zipfile 是标准库，全程不需要 pip）。"""
    import urllib.request
    import zipfile

    cache = default_workdir() / "wheels"
    cache.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for name in PYI_DEPS:
        try:
            doc = pypi_json(name, index)
            wheel_name, url = pick_wheel(doc, index)
        except Exception as exc:                                # noqa: BLE001
            print(f"× 查询 {name} 失败：{type(exc).__name__}: {exc}"
                  "（网络不可达时如实报阻塞，不要说已构建）")
            return 1
        local = cache / wheel_name
        if not local.exists():
            print(f"↓ {wheel_name}")
            with urllib.request.urlopen(url, timeout=300) as resp:
                local.write_bytes(resp.read())
        with zipfile.ZipFile(local) as zf:
            zf.extractall(target)
        installed.append(f"{name}=={doc['info']['version']}")
    ensure_on_path(target)
    (target / ".tracelens-vendor.json").write_text(
        json.dumps({"installed": installed, "via": "stdlib urllib + zipfile",
                    "python": platform.python_version(),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ 隔离目录已装：{', '.join(installed)}")
    ok, detail = importable()
    if not ok:
        print(f"装完仍不可导入：{detail}")
        return 1
    for probe in ("altgraph", "packaging"):
        try:
            __import__(probe)
        except Exception as exc:                                # noqa: BLE001
            print(f"× 导入 {probe} 失败：{exc}")
            return 1
    print(f"PyInstaller 离线安装完成：{detail}")
    return 0


def write_entry(work: pathlib.Path, external_runtime: bool = False) -> pathlib.Path:
    """写 PyInstaller 入口。

    默认（`external_runtime=False`）是"把业务包冻进 exe"的老形态：入口直接
    `from agent_explainer.__main__ import main`，业务模块靠 `--hidden-import` 全量进 PYZ。

    `external_runtime=True` 换成**薄启动器**：exe 里只有解释器与第三方依赖，
    `agent_explainer` 整个包以源码形态落在 `runtime/`，运行时从磁盘 import。
    这是更新通道能热更 Python 层的唯一前提——代码冻在 PYZ 里就换不掉
    （见 `docs/21-update-channel.md` §2）。代价是 exe 必须和 `runtime/` 一起分发，
    少了它启动器会明确报 78 而不是假装起得来。
    """
    work.mkdir(parents=True, exist_ok=True)
    entry = work / ENTRY_NAME
    if external_runtime:
        entry.write_text(LAUNCHER_SOURCE, encoding="utf-8")
        return entry
    entry.write_text(
        '"""PyInstaller 入口：把 `python -m agent_explainer` 包成一个 exe。"""\n'
        "from agent_explainer.__main__ import main\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
        encoding="utf-8")
    return entry


# 薄启动器：只认标准库，绝不 import 业务包（一旦 import，PyInstaller 就会把
# 整个 agent_explainer 分析进 PYZ，热更能力当场失效）
LAUNCHER_SOURCE = '''"""PyInstaller 入口：薄启动器（--external-runtime）。

业务代码不冻进 exe，而是从 exe 同级（或 --runtime / TRACELENS_RUNTIME 指定）的
``runtime/`` 目录按源码加载。更新通道换掉 ``runtime/agent_explainer/`` 就等于换掉
服务层，不需要重新安装。
"""
import os
import runpy
import sys
from pathlib import Path

RUNTIME_ENV = "TRACELENS_RUNTIME"


def runtime_dir():
    argv = sys.argv
    for index, item in enumerate(argv):
        if item == "--runtime" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser()
    env = os.environ.get(RUNTIME_ENV)
    if env:
        return Path(env).expanduser()
    return Path(sys.argv[0]).resolve().parent / "runtime"


def main():
    root = runtime_dir()
    entry = root / "agent_explainer" / "__main__.py"
    if not entry.is_file():
        sys.stderr.write(
            "[launcher] 找不到业务包：%s\\n"
            "[launcher] 这个 exe 是 --external-runtime 构建的，必须和 runtime/ 目录一起分发。\\n"
            % (entry,))
        return 78
    sys.path.insert(0, str(root))
    runpy.run_path(str(entry), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def add_data_arg(local: pathlib.Path, arc: str) -> str:
    return f"{local}{os.pathsep}{arc}"


def copy_runtime(target: pathlib.Path) -> None:
    """把业务包按**源码形态**复制到 `runtime/`（外置模式专用）。

    为什么连 `web/` 与 `samples/` 一起复制：`__main__.py` 里
    `REPO_ROOT = PACKAGE_ROOT.parent`、`DEFAULT_WEB_DIR = PACKAGE_ROOT / "web"`、
    `DEMO_ZH = REPO_ROOT / "samples" / …`。保持同样的相对布局，冻结态与源码态
    的路径解析才会一致——否则"开发时能跑、打包后找不到夹具"。
    """
    def ignore(_dirname, names):
        drop = set()
        for name in names:
            if name in {"__pycache__", ".pytest_cache", ".mypy_cache"}:
                drop.add(name)
            elif name.endswith((".pyc", ".pyo")):
                drop.add(name)
        return drop

    shutil.copytree(PACKAGE, target / "agent_explainer", ignore=ignore)
    if SAMPLES.exists():
        shutil.copytree(SAMPLES, target / "samples", ignore=ignore)


def contract_gate() -> int:
    """发版硬门禁：`gen_api_contract.py --check` 非零退出就拒绝打包（0.5.1）。

    `--check` 同时管四件事：契约与代码可重复生成、docs/03 §6.5 与契约一致、
    **声明 vs 挂载**（`declared_unmounted` 非空 = 有常量没接进分发）、
    **前端调用面**（`unknown_paths` 非空 = 界面在调不存在的路由）。
    两张名单非空就中止构建——"声明 ≠ 挂载"的包不许出厂。
    """
    print("=== 发版硬门禁：tools/gen_api_contract.py --check ===")
    rc = subprocess.run([sys.executable, "-X", "utf8",
                         str(REPO / "tools" / "gen_api_contract.py"), "--check"],
                        cwd=str(REPO)).returncode
    if rc != 0:
        print("打包中止：契约检查未通过（见上方逐条）。先修再发："
              "改代码后 `python tools/gen_api_contract.py --write`；"
              "unknown_paths / declared_unmounted 非空就把端点真的挂上。")
    return rc


def build(args: argparse.Namespace) -> int:
    rc = contract_gate()
    if rc != 0:
        return rc
    ok, detail = importable()
    if not ok:
        print(f"构建中止：{detail}")
        print("先跑：python tools/build_sidecar.py --vendor --build"
              "（把 PyInstaller 装进隔离目录），"
              "或看 --check 里的原因；不要跳过这一步宣称已打包。")
        return 1
    import PyInstaller.__main__ as pyi                          # noqa: N817

    layout = "onedir" if args.onedir else "onefile"
    external = bool(getattr(args, "external_runtime", False))
    work = pathlib.Path(args.workdir).expanduser() / "pyinstaller"
    work.mkdir(parents=True, exist_ok=True)
    entry = write_entry(work, external)
    dist = work / "dist"
    if external:
        # 业务包外置：不打 --add-data，也不 hidden-import 任何 agent_explainer 模块。
        # 第三方（reportlab 及其依赖）仍要显式收集：业务代码里那些 import 是
        # importlib 动态取的，PyInstaller 静态分析看不见。
        data = []
        print("→ 外置 runtime 模式：业务包不进 exe，改由 runtime/ 目录从磁盘加载")
    else:
        data = [add_data_arg(WEB, "agent_explainer/web"),
                add_data_arg(SAMPLES, "samples"),
                add_data_arg(PROMPTS, "agent_explainer/prompts")]
    plan = [
        f"--{layout}",
        "--name", SIDECAR_NAME,
        "--console",
        "--noconfirm",
        "--clean",
        "--paths", str(REPO),
        "--distpath", str(dist),
        "--workpath", str(work / "build"),
        "--specpath", str(work / "spec"),
    ]
    for item in data:
        plan += ["--add-data", item]
    hidden_modules = [] if external else package_modules()
    for hidden in hidden_modules:
        plan += ["--hidden-import", hidden]
    if external:
        print("→ hiddenimports：0 个 agent_explainer 模块（外置模式，全在 runtime/ 里）")
    else:
        print(f"→ hiddenimports：{len(hidden_modules)} 个 agent_explainer 模块"
              f"（secretariat={'有' if 'agent_explainer.secretariat' in hidden_modules else '缺'}、"
              f"crypto_sig={'有' if 'agent_explainer.crypto_sig' in hidden_modules else '缺'}）")
    if not args.no_reportlab and module_available("reportlab"):
        plan += ["--collect-all", "reportlab"]
        print("→ 打包进 reportlab（审计导出 PDF 走原生 PDF；缺它时产品会退回可打印 HTML）")
    else:
        print("→ 不打包 reportlab：审计导出 PDF 会走 X-TraceLens-Export-Fallback 的"
              "可打印 HTML（产品按设计降级，不是坏掉）")
    plan += [str(entry)]
    print("=== PyInstaller 参数 ===")
    for i in range(0, len(plan), 2):
        print("  " + " ".join(plan[i:i + 2]))
    started = time.time()
    pyi.run(plan)
    if layout == "onefile":
        built = dist / EXE_NAME
        target = EXE_ONEFILE
        if built.exists():
            SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, target)
    else:
        built = dist / SIDECAR_NAME
        target = EXE_ONEDIR
        if built.exists():
            final_dir = SIDECAR_DIR / SIDECAR_NAME
            if final_dir.exists():
                shutil.rmtree(final_dir)
            SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(built, final_dir)
    if not target.exists():
        print(f"构建失败：PyInstaller 跑完但没产出 {target}")
        return 1

    runtime_root = None
    if external:
        container = (SIDECAR_DIR / SIDECAR_NAME) if layout == "onedir" else SIDECAR_DIR
        runtime_root = container / "runtime"
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        runtime_root.mkdir(parents=True, exist_ok=True)
        copy_runtime(runtime_root)
        py_files = sum(1 for _ in runtime_root.rglob("*.py"))
        print(f"→ runtime：{runtime_root}（{py_files} 个 .py，exe 从这里加载业务包）")

    size = target.stat().st_size
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"→ 产物：{target}（{size / 1024 / 1024:.1f} MiB，布局={layout}，"
          f"耗时 {time.time() - started:.0f}s）")

    smoke = smoke_run(target, args.smoke_timeout)
    info = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "pyinstaller": pyinstaller_version(),
        "layout": layout,
        "exe": str(target.relative_to(REPO)).replace("\\", "/"),
        "bytes": size,
        "sha256": digest,
        "seconds": round(time.time() - started, 1),
        # 外置模式下 exe 里一个 add-data 都没有、hiddenimports 为空：
        # 验收要能一眼看出"这个 exe 依赖 runtime/ 目录"，而不是靠猜。
        "external_runtime": external,
        "runtime_dir": (str(runtime_root.relative_to(REPO)).replace("\\", "/")
                        if runtime_root else None),
        "add_data": ([] if external else
                     ["agent_explainer/web", "samples", "agent_explainer/prompts"]),
        # 验收要看的就是这两组事实：打进 exe 的模块清单（必须含 secretariat），
        # 以及 --add-data 的三个目录在构建时是否真的存在。0.4.5 的 BUILD-INFO 里
        # 一个都没有，所以「总裁办有没有被打进去」只能靠猜。
        "hiddenimports": hidden_modules,
        "hiddenimport_count": len(hidden_modules),
        "secretariat_in_hiddenimports": "agent_explainer.secretariat" in hidden_modules,
        "data_dirs_exist": {"web_dir_exists": WEB.exists(),
                            "samples_exists": SAMPLES.exists(),
                            "prompts_exists": PROMPTS.exists()},
        "version_default": DEFAULT_VERSION,
        "server_version_in_code": server_version_in_code(),
        "reportlab_bundled": not args.no_reportlab and module_available("reportlab"),
        "smoke": smoke,
        "run_hint": f'"{target}" --sidecar --port 0 --data-dir <用户数据目录>',
    }
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_INFO.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(f"→ 冒烟：{json.dumps(smoke, ensure_ascii=False)}")
    if not smoke.get("ok"):
        print("exe 冒烟失败：不得计入交付（详见 docs/05.3-release-checklist.md）")
        return 1
    print(f"→ 构建信息：{BUILD_INFO}")
    return 0


def smoke_run(exe: pathlib.Path, timeout: float) -> dict[str, Any]:
    """跑一次 `--print-argv`：这条路径要求 exe 真的 import 了 server/store/audit…。"""
    # 子进程按控制台代码页（cp936）吐非 ASCII 路径时，父进程按 UTF-8 解码会得到
    # U+FFFD（0.4.5 的 BUILD-INFO.json 里 smoke.stdout_tail 就是 "DEEPSEEK ��ū"，
    # 也就是**构建证据本身有乱码**）。所以：先请子进程用 UTF-8 输出（PYTHONUTF8 /
    # PYTHONIOENCODING），再**按字节拿**回来按编码链解码，并把实际用的解码器记进
    # BUILD-INFO（smoke.stdout_codec）。全链失败时退 backslashreplace——宁可留下
    # "\udc80" 这种看得见的转义，也不要把路径悄悄变成 U+FFFD 让人以为产物路径坏了。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        done = subprocess.run([str(exe), "--print-argv", "--port", "8899"],
                              cwd=str(REPO), capture_output=True, timeout=timeout,
                              env=env)
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    out, codec = decode_loose(done.stdout)
    err, _ = decode_loose(done.stderr)
    return {"ok": done.returncode == 0 and "--port 8899" in out,
            "exit_code": done.returncode,
            "stdout_codec": codec,
            "stdout_tail": out.strip().splitlines()[-1] if out.strip() else "",
            "stderr_tail": err.strip().splitlines()[-1] if err.strip() else ""}


def decode_loose(raw: bytes | None) -> tuple[str, str]:
    """字节 → 文本：按 UTF-8 → GBK 依次试，全失败退 backslashreplace（不产生 U+FFFD）。"""
    if not raw:
        return "", "empty"
    for codec in ("utf-8", "gbk"):
        try:
            return raw.decode(codec), codec
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "backslashreplace"), "utf-8/backslashreplace"


def portable_zip(args: argparse.Namespace) -> int:
    """源码便携版：拷走目录 + 双击 start.cmd 就能跑（需要系统 Python，说清不藏）。"""
    rc = contract_gate()
    if rc != 0:
        return rc
    version = args.version
    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / PORTABLE_NAME.format(version=version)
    if target.exists():
        target.unlink()
    include = [
        (PACKAGE, "agent_explainer"),
        (SAMPLES, "samples"),
        (REPO / "docs", "docs"),
        (REPO / "tools", "tools"),
    ]
    skip_dirs = {"__pycache__", "build", "dist"}
    skip_suffix = {".pyc", ".pyo"}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for local, arc in include:
            if not local.exists():
                print(f"跳过缺失目录：{local}")
                continue
            for path in sorted(local.rglob("*")):
                rel = path.relative_to(local)
                if any(part in skip_dirs for part in rel.parts):
                    continue
                if path.suffix in skip_suffix:
                    continue
                if path.is_file():
                    zf.write(path, f"{arc}/{rel.as_posix()}")
        zf.writestr("start.cmd",
                    "@echo off\r\n"
                    "rem TraceLens 源码便携版：需要系统 Python（打包版才是免 Python 的 exe）\r\n"
                    "setlocal\r\n"
                    "set PY=python\r\n"
                    "if \"%TRACELENS_PYTHON%\" neq \"\" set PY=%TRACELENS_PYTHON%\r\n"
                    "\"%PY%\" -m agent_explainer --browser --data-dir \"%~dp0data\" "
                    "--features \"%~dp0data\\features\" %*\r\n"
                    "endlocal\r\n")
        zf.writestr("start-sidecar.cmd",
                    "@echo off\r\n"
                    "rem 桌面端 Electron 拉 sidecar 用的同一条命令（不开浏览器）\r\n"
                    "setlocal\r\n"
                    "set PY=python\r\n"
                    "if \"%TRACELENS_PYTHON%\" neq \"\" set PY=%TRACELENS_PYTHON%\r\n"
                    "\"%PY%\" -m agent_explainer --sidecar --data-dir \"%~dp0data\" "
                    "--features \"%~dp0data\\features\" %*\r\n"
                    "endlocal\r\n")
        zf.writestr("README-便携版.txt",
                    "TraceLens 源码便携版（不是安装包）\r\n"
                    "----------------------------------\r\n"
                    "· 需要系统 Python 3.11+（默认 python；可用 TRACELENS_PYTHON 指定）。\r\n"
                    "· start.cmd：本机跑产品界面（--browser 打开默认浏览器）。\r\n"
                    "· start-sidecar.cmd：桌面壳用的 sidecar 模式（机器可读启动行）。\r\n"
                    "· 数据落在同目录 data\\（记录日志、审计链、设置、用户表、令牌表）。\r\n"
                    "· 演示数据是合成样例，不含真实用户数据。\r\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    size = target.stat().st_size
    print(f"→ 源码便携版：{target}（{size / 1024 / 1024:.2f} MiB）")
    print(f"  sha256 = {digest}")
    print("  诚实标注：这条路需要系统 Python，不叫安装包；免 Python 的单文件 sidecar "
          "由 --install --build 产出。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/build_sidecar.py",
                                     description="PyInstaller sidecar 构建（5.3 / t12）")
    parser.add_argument("--check", action="store_true",
                        help="只报告构建条件与可复现命令，不动产物")
    parser.add_argument("--install", action="store_true",
                        help="把 PyInstaller 装进隔离目录（pip 失败时自动走离线轮子）")
    parser.add_argument("--vendor", action="store_true",
                        help="只用标准库离线轮子安装（跳过 pip）")
    parser.add_argument("--build", action="store_true", help="跑 PyInstaller 单文件构建")
    parser.add_argument("--portable-zip", action="store_true", help="产出源码便携版压缩包")
    parser.add_argument("--pip-target", default=None,
                        help="隔离安装目录（默认 <系统临时目录>/tracelens-build/pyi）")
    parser.add_argument("--workdir", default=None,
                        help="PyInstaller 工作目录（默认 <系统临时目录>/tracelens-build）")
    parser.add_argument("--index", default=None, help="pip 索引地址（离线镜像时用）")
    parser.add_argument("--no-reportlab", action="store_true",
                        help="不打包 reportlab（PDF 导出走可打印 HTML 降级）")
    parser.add_argument("--onedir", action="store_true",
                        help="用 PyInstaller 单目录布局（官方推荐：不解压临时目录、"
                             "启动更快；沙箱里单文件模式跑不起来时用它）")
    parser.add_argument("--external-runtime", action="store_true",
                        help="业务包不进 exe：产出薄启动器 + runtime/ 目录（源码形态），"
                             "让更新通道能热更 Python 层。必须与 runtime/ 一起分发。")
    parser.add_argument("--smoke-timeout", type=float, default=90.0)
    parser.add_argument("--version", default=DEFAULT_VERSION,
                        help=f"便携包命名用的版本号（默认 {DEFAULT_VERSION}，"
                             "与 server.py:SERVER_VERSION 同源）")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    pip_target = (pathlib.Path(args.pip_target).expanduser()
                  if args.pip_target else default_workdir() / "pyi")
    if args.workdir is None:
        args.workdir = str(default_workdir())
    ensure_on_path(pip_target)

    did = False
    if args.check:
        did = True
        code = print_check(pip_target)
        if not (args.install or args.vendor or args.build or args.portable_zip):
            return code
    if args.vendor:
        did = True
        code = vendor_wheels(pip_target, args.index)
        if code != 0:
            return code
    if args.install:
        did = True
        code = install_pyinstaller(pip_target, args.index)
        if code != 0:
            return code
    if args.build:
        did = True
        code = build(args)
        if code != 0:
            return code
    if args.portable_zip:
        did = True
        code = portable_zip(args)
        if code != 0:
            return code
    if not did:
        parser.print_help()
        print("\n提示：加 --check 看构建条件，加 --install --build 产出 sidecar exe，"
              "加 --portable-zip 产出源码便携版。")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
