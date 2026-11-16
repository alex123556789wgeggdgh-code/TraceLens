"""``tests/scratch.py`` 自身的回退链单测（t-0.5.1 起补上的验证能力）。

背景：受限沙箱里（DSH 一次性只写工作区策略）系统临时目录建得开、写不进，
``tempfile.TemporaryDirectory()`` 直接连清理都抛 ``[WinError 5]``——那是
15 failures / 209 errors 的来源。scratch.py 的探针 + ``var/`` 兜底就是为这条
环境准备的，但此前只有人工验证，没有回归锁。

怎么在没有真 ACL 的环境里确定性复现"候选目录拒绝写入"：把候选路径指向一个
**已存在的普通文件**——``mkdir`` 会抛 ``NotADirectoryError``（``OSError`` 子类），
和真实 ACL 拒写走同一条 ``except OSError`` 通路，Windows/Linux 都成立。
真 ACL 实验（``icacls /deny`` 观察到真实 ``WinError 5``）在手工验证里做过。

这里锁三件事：

1. 候选全失败 → 落点退回仓库 ``var/tmp-*``，绝不落在 ``tests/`` 下；
2. 兜底真的可写、可清理，``scratch_dir`` 语义（同名先删再建）不破；
3. ``scratch_reason()`` / ``system_temp_usable()`` 如实报告，不许误报可用。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scratch  # noqa: E402
from scratch import scratch_dir, scratch_reason, system_temp_usable  # noqa: E402

# 一个"看起来像目录、实际是文件"的路径：任何 mkdir 都会 NotADirectoryError。
_BLOCKED = ROOT / "var" / "tmp-sandbox-simulated"


class ScratchFallbackTests(unittest.TestCase):
    """候选全失败 → 仓库 ``var/`` 兜底，且理由说得清。"""

    def setUp(self) -> None:
        _BLOCKED.parent.mkdir(parents=True, exist_ok=True)
        _BLOCKED.write_text("不是目录，是文件——模拟拒绝写入的候选", encoding="utf-8")
        self.addCleanup(_remove_blocked)
        self._saved_gettempdir = tempfile.gettempdir
        self._saved_tmpdir = os.environ.get("TMPDIR")
        tempfile.gettempdir = lambda: str(_BLOCKED)  # type: ignore[assignment]
        os.environ["TMPDIR"] = str(_BLOCKED)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        tempfile.gettempdir = self._saved_gettempdir  # type: ignore[assignment]
        if self._saved_tmpdir is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = self._saved_tmpdir

    def test_all_candidates_blocked_falls_back_to_repo_var(self) -> None:
        base = scratch_dir("fallback-simulated")
        self.addCleanup(_wipe, base)
        self.assertTrue(str(base).startswith(str(ROOT / "var")),
                        f"必须退回仓库 var/，实际落在 {base}")
        self.assertFalse(str(base).startswith(str(ROOT / "tests")),
                         "兜底绝不落在 tests/ 下")
        # 兜底真的能写文件——这正是受限沙箱里炸掉的那一步
        probe = base / "probe.json"
        probe.write_text("{}", encoding="utf-8")
        self.assertEqual("{}", probe.read_text(encoding="utf-8"))

    def test_scratch_dir_wipes_same_name_before_recreate(self) -> None:
        base = scratch_dir("fallback-simulated")
        self.addCleanup(_wipe, base)
        stale = base / "stale.txt"
        stale.write_text("旧现场", encoding="utf-8")
        again = scratch_dir("fallback-simulated")
        self.addCleanup(_wipe, again)
        self.assertEqual(base, again)
        self.assertFalse(stale.exists(), "同名 scratch 必须先删旧目录再建")

    def test_reason_names_the_blocked_candidates(self) -> None:
        base = scratch_dir("fallback-simulated")
        self.addCleanup(_wipe, base)
        reason = scratch_reason()
        self.assertIn("不可写", reason)
        self.assertIn(str(_BLOCKED), reason)
        self.assertIn("var", str(base))

    def test_system_temp_usable_reports_honest_false_when_blocked(self) -> None:
        self.assertFalse(system_temp_usable(),
                         "候选全拒绝时必须如实报 False，不许误报可用")

    def test_tmpdir_override_writable_dir_takes_precedence(self) -> None:
        # 恢复真实 gettempdir，只把 TMPDIR 指到可写目录：必须真的落进去。
        tempfile.gettempdir = self._saved_gettempdir  # type: ignore[assignment]
        override_root = scratch_dir("tmpdir-override-probe").parent.parent
        os.environ["TMPDIR"] = str(override_root)
        base = scratch_dir("override-roundtrip")
        self.addCleanup(_wipe, base)
        self.assertTrue(str(base).startswith(str(override_root)),
                        f"TMPDIR 可写时必须落在 TMPDIR 下，实际 {base}")
        self.assertTrue(system_temp_usable())


def _wipe(path: pathlib.Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _remove_blocked() -> None:
    try:
        _BLOCKED.unlink()
    except OSError:
        shutil.rmtree(_BLOCKED, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover - 手工运行
    unittest.main()
