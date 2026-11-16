"""0.6.0 更新通道验收测试：打包器 / 更新引擎 / 前端接面 / 桌面壳接线。

运行（全程离线、只用标准库；不装第三方、不出网）：

    cd agent-explainer
    python -X utf8 -m unittest tests.test_update_face -v

钉住的口径（`docs/21-update-channel.md`）：

1. **包是确定性的**：同输入两次打包 sha256 相同。做不到这条，逐文件哈希就只是
   自我安慰——「同版本两个包」会让任何比对都失去意义。
2. **包里只有申报过的东西**：``manifest.files`` 与 zip 实际条目一一对应；
   ``__pycache__`` / ``*.pyc`` / ``*.map`` 不进包；默认只打 web 层。
3. **engine 不假成功**：篡改、少文件、多文件、降级、宿主缺能力、源不可达，
   各有各的 reason 码；失败时激活指针一字不动（这条由 Node 自检端到端覆盖）。
4. **前端不许自己造进度**：不确定态、零定时器、零写宽度；浏览器模式先说明再退出，
   不画死按钮。
5. **渲染端拿不到更新源**：``install`` 的 IPC 不接受渲染端传的 source，只认
   ``config.json``；否则任何能执行 JS 的页面都能把应用指向任意下载地址。
6. **回退只做一次**：启动失败回退有硬上限，避免启动风暴。

纪律：临时目录一律 ``tests/scratch.py``（禁 ``tempfile``）；不并发跑全量。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))

import scratch                                                      # noqa: E402

TOOLS = REPO / "tools"
DESKTOP = REPO / "desktop"
WEB = REPO / "agent_explainer" / "web"
PACKAGER = TOOLS / "make_update_package.py"
SELFTEST = DESKTOP / "lib" / "selftest.mjs"

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

NODE = shutil.which("node")


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_packager(out_dir: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(PACKAGER), "--out", str(out_dir), *extra],
        cwd=str(REPO), capture_output=True, text=True, timeout=300,
    )


class PackagerTests(unittest.TestCase):
    """打包器：确定性、清单完整、排除规则、index 合并。"""

    def setUp(self) -> None:
        # contextmanager 手动进出：scratch 的替身是为了绕开受限沙箱里
        # `mkdtemp()` 建完目录立刻 chmod 700 导致 WinError 5 的那个坑
        self._tmp = scratch.scratch_tempdir("update-pkg")
        self.root = pathlib.Path(self._tmp.__enter__())

    def tearDown(self) -> None:
        self._tmp.__exit__(None, None, None)

    def test_package_is_byte_identical_on_a_second_run(self) -> None:
        first = self.root / "a"
        second = self.root / "b"
        r1 = run_packager(first, "--version", "0.6.1", "--notes", "determinism")
        r2 = run_packager(second, "--version", "0.6.1", "--notes", "determinism")
        self.assertEqual(0, r1.returncode, r1.stderr)
        self.assertEqual(0, r2.returncode, r2.stderr)
        a = sha256_of(first / "tracelens-0.6.1.tlpkg")
        b = sha256_of(second / "tracelens-0.6.1.tlpkg")
        self.assertEqual(a, b, "同输入必须产出同一个字节序列")

    def test_manifest_lists_exactly_what_the_zip_contains(self) -> None:
        out = self.root / "pkg"
        done = run_packager(out, "--version", "0.6.2")
        self.assertEqual(0, done.returncode, done.stderr)
        with zipfile.ZipFile(out / "tracelens-0.6.2.tlpkg") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        declared = {item["path"] for item in manifest["files"]}
        self.assertEqual(set(names) - {"manifest.json"}, declared,
                         "manifest 与实际条目必须一一对应（多一个或少一个都不行）")
        for item in manifest["files"]:
            with zipfile.ZipFile(out / "tracelens-0.6.2.tlpkg") as zf:
                digest = hashlib.sha256(zf.read(item["path"])).hexdigest()
            self.assertEqual(item["sha256"], digest, f"{item['path']} 的哈希与内容不一致")

    def test_default_package_carries_the_web_layer_only(self) -> None:
        out = self.root / "webonly"
        self.assertEqual(0, run_packager(out, "--version", "0.6.3").returncode)
        with zipfile.ZipFile(out / "tracelens-0.6.3.tlpkg") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        self.assertEqual(["web"], manifest["capabilities"])
        self.assertTrue(all(item["path"].startswith("web/") for item in manifest["files"]))

    def test_runtime_package_carries_python_sources_but_not_the_web_layer_twice(self) -> None:
        out = self.root / "runtime"
        done = run_packager(out, "--version", "0.6.4", "--with-runtime", "--min-host", "0.6.0")
        self.assertEqual(0, done.returncode, done.stderr)
        with zipfile.ZipFile(out / "tracelens-0.6.4.tlpkg") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        self.assertEqual(["runtime", "web"], manifest["capabilities"])
        self.assertEqual("0.6.0", manifest["min_host"])
        runtime = [i["path"] for i in manifest["files"] if i["path"].startswith("runtime/")]
        self.assertIn("runtime/agent_explainer/server.py", runtime)
        self.assertIn("runtime/agent_explainer/__main__.py", runtime)
        web = [i["path"] for i in manifest["files"] if i["path"].startswith("web/")]
        self.assertTrue(web, "web 层必须仍在包里")
        self.assertFalse(any(p.startswith("runtime/agent_explainer/web/") for p in runtime),
                         "web 资产只该出现在 web/ 下，不该在 runtime 里再打一遍")

    def test_build_artifacts_and_source_maps_are_excluded(self) -> None:
        out = self.root / "clean"
        self.assertEqual(0, run_packager(out, "--version", "0.6.5", "--with-runtime").returncode)
        with zipfile.ZipFile(out / "tracelens-0.6.5.tlpkg") as zf:
            names = zf.namelist()
        for bad in ("__pycache__", ".pyc", ".map", "Thumbs.db", ".DS_Store"):
            hit = [n for n in names if bad in n]
            self.assertEqual([], hit, f"包里不该有 {bad}：{hit[:3]}")

    def test_index_merges_versions_and_keeps_the_highest_as_latest(self) -> None:
        out = self.root / "index"
        for version in ("0.6.0", "0.6.10", "0.6.2"):
            done = run_packager(out, "--version", version)
            self.assertEqual(0, done.returncode, done.stderr)
        doc = json.loads((out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual("tracelens.update-index/1", doc["schema"])
        self.assertEqual("0.6.10", doc["latest"], "0.6.10 必须排在 0.6.2 之后（不是字符串排序）")
        self.assertEqual(["0.6.0", "0.6.2", "0.6.10"], [r["version"] for r in doc["releases"]])

    def test_repackaging_the_same_version_replaces_its_row(self) -> None:
        out = self.root / "dup"
        self.assertEqual(0, run_packager(out, "--version", "0.6.6", "--notes", "first").returncode)
        second = run_packager(out, "--version", "0.6.6", "--notes", "second")
        self.assertEqual(0, second.returncode, second.stderr)
        doc = json.loads((out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(doc["releases"]), "同版本不该在 index 里出现两行")
        self.assertEqual("second", doc["releases"][0]["notes"])

    def test_no_index_flag_leaves_the_index_alone(self) -> None:
        out = self.root / "noindex"
        self.assertEqual(0, run_packager(out, "--version", "0.6.7", "--no-index").returncode)
        self.assertTrue((out / "tracelens-0.6.7.tlpkg").exists())
        self.assertFalse((out / "index.json").exists())


@unittest.skipUnless(NODE, "没有 node，跳过引擎端到端自检")
class EngineTests(unittest.TestCase):
    """Node 引擎自检：安装 / 幂等 / 降级 / 篡改 / 回滚 / 能力门禁。"""

    def test_engine_selftest_passes(self) -> None:
        done = subprocess.run([NODE, str(SELFTEST)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=600)
        tail = (done.stdout or "")[-1500:]
        self.assertEqual(0, done.returncode, f"引擎自检失败：\n{tail}\n{done.stderr[-800:]}")
        self.assertIn("全部通过", done.stdout)

    def test_engine_modules_have_no_third_party_imports(self) -> None:
        allowed = {"node:fs", "node:path", "node:http", "node:https", "node:url",
                   "node:crypto", "node:zlib", "node:module", "node:child_process"}
        for name in ("desktop/updater.js", "desktop/lib/zip.js", "desktop/lib/version.js",
                     "desktop/lib/manifest.js"):
            text = (REPO / name).read_text(encoding="utf-8")
            for match in re.findall(r"require\(['\"]([^'\"]+)['\"]\)", text):
                if match.startswith("."):
                    continue
                self.assertIn(match, allowed, f"{name} 引入了第三方依赖 {match}")


class FrontEndTests(unittest.TestCase):
    """前端接面：扩展存在、挂载点、进度诚实、浏览器模式、文案覆盖。"""

    def setUp(self) -> None:
        self.ext = (WEB / "ext" / "update.js").read_text(encoding="utf-8")
        self.css = (WEB / "ext" / "update.css").read_text(encoding="utf-8")
        self.app = (WEB / "app.js").read_text(encoding="utf-8")
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.zh = json.loads((WEB / "i18n" / "zh.json").read_text(encoding="utf-8"))
        self.en = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))

    def test_assets_are_loaded_by_the_page(self) -> None:
        self.assertIn("ext/update.js", self.html)
        self.assertIn("ext/update.css", self.html)

    def test_extension_registers_once_without_a_new_tab(self) -> None:
        self.assertEqual(1, self.ext.count("Ext.register("), "只许注册一次")
        self.assertNotIn("addTab", self.ext, "更新面板挂在设置页里，不开第二个入口")
        self.assertIn("TraceLensUpdate", self.ext, "要给 app.js 一个挂载入口")

    def test_settings_panel_mounts_the_host_and_marks_a_missing_extension(self) -> None:
        block = self.app.split("function renderSettingsPanel")[1].split("function auditErrorKey")[0]
        self.assertIn("settings-update-host", block)
        self.assertIn("TraceLensUpdate", block)
        self.assertIn("data-missing", block, "扩展缺失时挂标记，不留空白")

    def test_progress_bar_can_never_report_a_number(self) -> None:
        for token in ("setInterval", "setTimeout", "requestAnimationFrame", "style.width",
                      "aria-valuenow"):
            self.assertNotIn(token, self.ext, f"更新进度不该出现 {token}")
        self.assertIn("data-determinate", self.ext)
        self.assertIn("'data-percent', ''", self.ext)

    def test_css_has_no_motion(self) -> None:
        code = re.sub(r"/\*[\s\S]*?\*/", " ", self.css)
        for token in ("@keyframes", "animation", "transition"):
            self.assertNotIn(token, code, f"update.css 不该有 {token}")

    def test_css_only_uses_declared_tokens(self) -> None:
        styles = (WEB / "styles.css").read_text(encoding="utf-8")
        defined = {m.rstrip(":").strip() for m in re.findall(r"--[a-z0-9-]+\s*:", styles)}
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", self.css))
        missing = sorted(used - defined)
        self.assertEqual([], missing, f"update.css 用了未定义的 token：{missing}")

    def test_browser_mode_explains_itself_before_drawing_controls(self) -> None:
        guard = self.ext.index("update.hint.browser")
        first_button = self.ext.index("update.action.check")
        self.assertLess(guard, first_button, "浏览器模式必须先给一句人话，再谈控件")
        self.assertIn("if (!api)", self.ext)

    def test_no_cjk_literal_in_the_extension(self) -> None:
        code = re.sub(r"/\*[\s\S]*?\*/", " ", self.ext)
        code = re.sub(r"//[^\n]*", " ", code)
        self.assertEqual([], CJK.findall(code), "ext/update.js 不得有中文硬编码")

    def test_every_reason_code_the_ui_claims_to_know_has_copy(self) -> None:
        block = re.search(r"var KNOWN_REASONS = \{([\s\S]*?)\};", self.ext)
        self.assertIsNotNone(block, "找不到 KNOWN_REASONS")
        codes = re.findall(r"([a-z_]+):\s*1", block.group(1))
        self.assertGreaterEqual(len(codes), 15)
        for code in codes:
            self.assertIn(f"reason.{code}", self.zh["update"], f"zh 缺 update.reason.{code}")
            self.assertIn(f"reason.{code}", self.en["update"], f"en 缺 update.reason.{code}")
        self.assertIn("reason.other", self.zh["update"])
        self.assertIn("reason.other", self.en["update"])

    def test_zh_and_en_expose_the_same_update_keys(self) -> None:
        self.assertEqual(sorted(self.zh["update"]), sorted(self.en["update"]))

    def test_copy_carries_no_completion_illusion(self) -> None:
        banned = ("正在上链", "已验真", "更新成功", "100%")
        for lang, table in (("zh", self.zh["update"]), ("en", self.en["update"])):
            blob = json.dumps(table, ensure_ascii=False)
            for word in banned:
                self.assertNotIn(word, blob, f"{lang} 字典出现禁语：{word}")


class DesktopWiringTests(unittest.TestCase):
    """桌面壳接线：只认配置的源、回退只一次、端口/资源路径不回归。"""

    def setUp(self) -> None:
        self.main = (DESKTOP / "main.js").read_text(encoding="utf-8")
        self.preload = (DESKTOP / "preload.js").read_text(encoding="utf-8")
        self.sidecar = (DESKTOP / "sidecar.js").read_text(encoding="utf-8")
        self.builder = (DESKTOP / "electron-builder.yml").read_text(encoding="utf-8")

    def test_boot_hands_the_active_version_to_the_sidecar(self) -> None:
        self.assertIn("activeDirs()", self.main)
        self.assertIn("webDir: active.webDir", self.main)
        self.assertIn("runtimeDir: active.runtimeDir", self.main)
        self.assertIn("if (webDir)", self.sidecar, "sidecar 必须真的把 --web 传下去")
        self.assertIn("'--web', webDir", self.sidecar)

    def test_renderer_cannot_choose_the_update_source(self) -> None:
        handler = self.main.split("tracelens:update-install")[1].split("ipcMain.handle")[0]
        self.assertNotIn("opts.source", handler, "渲染端传的 source 必须被丢掉")
        self.assertNotIn("options.source", handler)
        self.assertIn("updater().install(request)", handler)

    def test_config_writes_are_key_limited(self) -> None:
        self.assertIn("UPDATE_CONFIG_KEYS", self.main)
        for key in ("source", "autoCheck", "channel"):
            self.assertIn(f"'{key}'", self.main)
        self.assertNotIn("'capabilities'", self.main, "能力不归渲染端改")

    def test_rollback_is_attempted_at_most_once(self) -> None:
        self.assertIn("state.rollbackTried", self.main)
        block = self.main.split("function attemptRollback")[1].split("async function boot")[0]
        self.assertIn("if (state.rollbackTried)", block)
        self.assertIn("state.rollbackTried = true", block)

    def test_healthy_start_confirms_the_pending_version(self) -> None:
        # 按回调起点切，不用 split('did-finish-load')：那个字符串在紧邻的
        # 注释里也出现过一次，切出来会是半句话（本测试首版就踩了）
        start = self.main.index("once('did-finish-load'")
        block = self.main[start:start + 2500]
        self.assertIn("confirmHealthy", block, "界面加载完成后才确认版本可用")
        self.assertLess(block.index("confirmHealthy"), block.index("runSelfCheck"),
                        "先确认版本可用，再跑自检退出")

    def test_preload_exposes_update_surface_without_leaking_node(self) -> None:
        self.assertEqual(1, self.preload.count("require("), "preload 只许 require('electron')")
        for name in ("update.status", "update.check", "update.install",
                     "update.config", "update.rollback", "update.restart"):
            self.assertIn(name.split(".")[1], self.preload, f"缺 {name}")
        for banned in ("child_process", "fs", "net", "http", "node:", "eval("):
            self.assertNotIn(banned, self.preload, f"preload 出现 {banned}")

    def test_builder_ships_the_update_engine_and_skips_its_test(self) -> None:
        self.assertIn("updater.js", self.builder)
        self.assertIn("lib/**/*", self.builder)
        self.assertIn("!lib/selftest.mjs", self.builder)


class DocsTests(unittest.TestCase):
    def test_update_channel_doc_exists_and_states_the_boundaries(self) -> None:
        doc = (REPO / "docs" / "21-update-channel.md").read_text(encoding="utf-8")
        for token in ("L-web", "L-runtime", "L-shell", "pending_confirm",
                      "tlpkg", "index.json", "min_host"):
            self.assertIn(token, doc, f"设计文档缺 {token}")
        self.assertIn("不做签名验证", doc, "签名缺失必须如实申报")
        self.assertIn("不做的事", doc)

    def test_doc_does_not_claim_shell_hot_update(self) -> None:
        doc = (REPO / "docs" / "21-update-channel.md").read_text(encoding="utf-8")
        self.assertIn("需要重装", doc, "壳层必须明确说需要重装")


if __name__ == "__main__":
    unittest.main()
