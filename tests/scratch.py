"""测试临时目录：一律优先系统临时目录，绝不在 ``tests/`` 下留 ``tmp-*``。

约定（4.9 / t22 起，QA 5.2 提出）：所有测试的临时文件都写在这里，模块级
``tearDown`` 整目录删除；仓库里不再出现 ``tests/tmp-*`` 残留。

为什么留一个兜底路径：在受限沙箱里（例如 DSH 的一次性只写工作区策略），
``tempfile.gettempdir()`` 可以建目录，但**写文件**会被拒
（``PermissionError: [Errno 13]``），连 ``TemporaryDirectory()`` 的清理也会抛
``[WinError 5]``。测试不能因为环境的 ACL 而变红，所以这里先真写一个探针文件试一次：

* 探针成功 → 用 ``<系统临时目录>/agent-explainer-tests/<name>``（首选，满足约定）；
* 探针失败 → 退回仓库根的 ``<repo>/var/tmp-<name>``（``var/`` 是运行时产物目录，
  与 ``var/records.jsonl``、``var/log/audit.jsonl`` 同级；**永远不是 ``tests/`` 下**），
  并把原因写进 ``scratch_reason()``，让日志里看得见"这台机器的系统临时目录不可写"。

两种落点都用 ``addCleanup(shutil.rmtree(...))`` 清掉；仓库根 ``var/`` 是运行时目录，
不参与打包、不参与文档闸门，留残也不影响交付物。

TMPDIR 覆盖：设置了 ``TMPDIR`` 时它排在 ``tempfile.gettempdir()`` 之前（直接读环境
变量，不经 ``tempfile`` 的进程级缓存）。受限沙箱 / CI 可以把 ``TMPDIR`` 指到一个
可写盘，把整条链定死；指错了也不炸——探针失败照常落到下一个候选或 ``var/``。

标准库替身（M2-PRE 5）：``tempfile.mkdtemp()`` 建完目录就 ``chmod(0o700)``，受限沙箱的
受限令牌对刚建的目录没有写属性权，``TemporaryDirectory`` 连清理都抛 ``[WinError 5]``。
``scratch_tempdir()`` 是同形替身，``use_scratch_tempfile()`` 把进程内的
``TemporaryDirectory`` / ``mkdtemp`` 临时换成它（用例结束还原），
``standard_tempdir_usable()`` 报告这台机器到底跑得通哪一边——被测代码一行不改。
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import secrets
import shutil
import tempfile

__all__ = ["scratch_root", "scratch_dir", "scratch_reason", "system_temp_usable",
           "scratch_tempdir", "standard_tempdir_usable", "use_scratch_tempfile"]

_SUBDIR = "agent-explainer-tests"
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REASON: list[str] = []


def _candidate_bases() -> list[tuple[str, pathlib.Path]]:
    """按优先级返回候选落点：显式 ``TMPDIR``（若设置）优先，其后系统临时目录。"""
    bases: list[tuple[str, pathlib.Path]] = []
    raw = (os.environ.get("TMPDIR") or "").strip()
    if raw:
        bases.append(("TMPDIR", pathlib.Path(raw)))
    bases.append(("系统临时目录", pathlib.Path(tempfile.gettempdir())))
    return bases


def _probe_system_temp() -> tuple[str, pathlib.Path] | None:
    """逐个候选真的写一次：能建目录 + 写文件 + 删掉，才算可用。"""
    for label, root in _candidate_bases():
        base = root / _SUBDIR
        probe = base / ".scratch-probe.txt"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe.write_text("probe", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "probe":
                raise OSError("probe read-back mismatch")
            probe.unlink()
            return (label, base)
        except OSError as exc:  # 沙箱 / ACL / 只读卷都走这条路
            try:
                probe.unlink()
            except OSError:
                pass
            _REASON.append(f"{label} {base} 不可写：{type(exc).__name__}: {exc}")
    return None


def _fallback_root() -> pathlib.Path:
    root = _REPO_ROOT / "var"
    root.mkdir(parents=True, exist_ok=True)
    _REASON.append(
        f"退回仓库运行时目录 {root / 'tmp-*'}（系统临时目录不可写；不是 tests/ 下）"
    )
    return root


def scratch_root() -> pathlib.Path:
    """临时目录的根：优先显式 ``TMPDIR``/系统临时目录，不可用时退回仓库 ``var/``。"""
    chosen = _probe_system_temp()
    if chosen is None:
        base = _fallback_root()
    else:
        label, base = chosen
        line = f"落点：{label} {base} 可写"
        if not _REASON or _REASON[-1] != line:  # 每次调用都会重探，成功行去重
            _REASON.append(line)
    base.mkdir(parents=True, exist_ok=True)
    return base


def scratch_dir(name: str) -> pathlib.Path:
    """返回一个干净的同名临时目录（已存在则先整目录删掉再建）。"""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(name))
    target = scratch_root() / safe
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def system_temp_usable() -> bool:
    """这次运行是否真的落在系统临时目录/显式 TMPDIR 里（用于日志与断言说明）。"""
    return _probe_system_temp() is not None


def scratch_reason() -> str:
    """落点选择的理由，供测试输出/排障时说明。"""
    if not _REASON:
        return "系统临时目录可写，未使用仓库目录"
    return "；".join(_REASON)


# --------------------------------------------------------------------------- #
# 标准库临时目录的同形替身（M2-PRE 5，PM-VERDICT-R2-2 遗留③ / PM-VERDICT-R2-3 ①③）
# --------------------------------------------------------------------------- #

def _make_scratch_dir(prefix: str = "", suffix: str = "",
                      base: pathlib.Path | None = None) -> pathlib.Path:
    """在探测出的落点里建一个唯一的新目录：``mkdir`` 不带 ``exist_ok``，撞名就换 token。"""
    root = pathlib.Path(base) if base else scratch_root()
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(64):
        target = root / ("tmp-%s%s%s" % (prefix, secrets.token_hex(4), suffix))
        try:
            target.mkdir()
            return target
        except FileExistsError:
            continue
    raise RuntimeError("scratch 目录名连续 64 次撞车，落点异常")


@contextlib.contextmanager
def scratch_tempdir(name: str = "tempdir", prefix: str = "", suffix: str = "", dir=None):
    """``tempfile.TemporaryDirectory()`` 的同形替身：进 ``with`` 拿到空目录路径，出 ``with`` 删掉。

    为什么要替身：标准库 ``mkdtemp()`` 建完目录立刻 ``os.chmod(dir, 0o700)`` 收紧权限，
    受限沙箱（DSH 工作区策略的受限令牌）对刚建的目录没有"写属性"权 →
    ``PermissionError: [WinError 5] 拒绝访问``，连清理阶段的 ``_resetperms`` 也抛同一个码。
    那是这台机器的 ACL 事实，不是被测代码的失败。这里只走 ``scratch_root()`` 已探过的
    可写落点 + 普通 ``mkdir`` / ``rmtree(ignore_errors=True)``，不需要任何 ACL 特权，
    落点约定与本文件一致（绝不落在 ``tests/`` 下）。
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(name))
    target = _make_scratch_dir(prefix=f"{safe}-", suffix=suffix, base=dir)
    try:
        yield str(target)
    finally:
        shutil.rmtree(target, ignore_errors=True)


class _ScratchTemporaryDirectory:
    """``tempfile.TemporaryDirectory`` 的替身：同样的 ``.name`` / ``__enter__`` / 自动清理。"""

    def __init__(self, suffix=None, prefix=None, name=None, dir=None,
                 ignore_cleanup_errors=True):
        self.ignore_cleanup_errors = ignore_cleanup_errors
        self._dir = _make_scratch_dir(prefix=str(prefix or "tmp"),
                                      suffix=str(suffix or ""), base=dir)

    @property
    def name(self) -> str:
        return str(self._dir)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *exc_info) -> bool:
        self.cleanup()
        return False

    def cleanup(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


def _scratch_mkdtemp(suffix: str = "", prefix: str = "tmp", dir=None) -> str:
    """``tempfile.mkdtemp`` 的替身：建目录、返回路径，不自动清理（口径同标准库）。"""
    return str(_make_scratch_dir(prefix=prefix, suffix=suffix, base=dir))


@contextlib.contextmanager
def use_scratch_tempfile():
    """把进程内的 ``tempfile.TemporaryDirectory`` / ``mkdtemp`` 换成本文件的 scratch 实现。

    给"把模块自检跑一遍"这类用例：自检函数内部自己 ``with tempfile.TemporaryDirectory()``，
    测试侧唯一能落地的接入点就是这两个名字。用例结束（含断言失败）都还原，进程级副作用
    为零，被测函数一行不改。
    """
    saved_dir = tempfile.TemporaryDirectory
    saved_mkdtemp = tempfile.mkdtemp
    tempfile.TemporaryDirectory = _ScratchTemporaryDirectory  # type: ignore[assignment]
    tempfile.mkdtemp = _scratch_mkdtemp                        # type: ignore[assignment]
    try:
        yield
    finally:
        tempfile.TemporaryDirectory = saved_dir                # type: ignore[assignment]
        tempfile.mkdtemp = saved_mkdtemp                       # type: ignore[assignment]


def standard_tempdir_usable() -> bool:
    """这台机器跑得通标准库临时目录的完整生命周期吗（建 → 嵌套建 → 写 → 自动删）。

    报告与排障用：``False`` 说明 ``tempfile.mkdtemp`` 的 chmod 在本地被 ACL 拒，
    依赖它的模块自检走 ``use_scratch_tempfile`` 的替身跑，不记作产品失败。
    """
    try:
        with tempfile.TemporaryDirectory() as temp:
            nested = pathlib.Path(temp) / "nested"
            nested.mkdir()
            (nested / "probe.txt").write_text("probe", encoding="utf-8")
        return True
    except OSError:
        return False


if __name__ == "__main__":  # 手工自检：python tests/scratch.py
    where = scratch_dir("self-check")
    (where / "a.json").write_text("{}", encoding="utf-8")
    print("scratch_root =", scratch_root())
    print("scratch_dir  =", where)
    print("usable       =", system_temp_usable())
    print("reason       =", scratch_reason())
    print("inside tests/ =", str(where).startswith(str(_REPO_ROOT / "tests")))
    shutil.rmtree(where, ignore_errors=True)
    assert not str(scratch_root()).startswith(str(_REPO_ROOT / "tests"))
    # 标准库替身：同形（空目录 + 嵌套可建 + 退出删除），且不动被测代码
    with scratch_tempdir("stand-in") as temp:
        (pathlib.Path(temp) / "nested").mkdir()
        print("stand-in dir   =", temp)
    assert not pathlib.Path(temp).exists(), "替身目录没删掉"
    with use_scratch_tempfile():
        with tempfile.TemporaryDirectory() as patched:
            (pathlib.Path(patched) / "a").mkdir()
            print("patched td     =", patched)
    assert tempfile.TemporaryDirectory is not _ScratchTemporaryDirectory, "替身没还原"
    print("stdlib usable =", standard_tempdir_usable())
    # TMPDIR 覆盖回环：指到可写目录必须真的落进去，并在 reason 里说清来源
    os.environ["TMPDIR"] = str(_REPO_ROOT / "var" / "tmp-selfcheck-override")
    overridden = scratch_dir("self-check-tmpdir")
    try:
        assert str(overridden).startswith(os.environ["TMPDIR"]), overridden
        assert any("TMPDIR" in line for line in _REASON), _REASON
        print("tmpdir override =", overridden)
    finally:
        os.environ.pop("TMPDIR", None)
        shutil.rmtree(_REPO_ROOT / "var" / "tmp-selfcheck-override", ignore_errors=True)
    print("OK")
