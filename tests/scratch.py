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
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

__all__ = ["scratch_root", "scratch_dir", "scratch_reason", "system_temp_usable"]

_SUBDIR = "agent-explainer-tests"
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REASON: list[str] = []


def _probe_system_temp() -> pathlib.Path | None:
    """真的写一次：能建目录 + 写文件 + 删掉，才算系统临时目录可用。"""
    base = pathlib.Path(tempfile.gettempdir()) / _SUBDIR
    probe = base / ".scratch-probe.txt"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "probe":
            raise OSError("probe read-back mismatch")
        probe.unlink()
        return base
    except OSError as exc:  # 沙箱 / ACL / 只读卷都走这条路
        try:
            probe.unlink()
        except OSError:
            pass
        _REASON.append(f"系统临时目录 {base} 不可写：{type(exc).__name__}: {exc}")
        return None


def _fallback_root() -> pathlib.Path:
    root = _REPO_ROOT / "var"
    root.mkdir(parents=True, exist_ok=True)
    _REASON.append(
        f"退回仓库运行时目录 {root / 'tmp-*'}（系统临时目录不可写；不是 tests/ 下）"
    )
    return root


def scratch_root() -> pathlib.Path:
    """临时目录的根：优先系统临时目录，不可用时退回仓库 ``var/``。"""
    base = _probe_system_temp()
    if base is None:
        base = _fallback_root()
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
    """这次运行是否真的落在系统临时目录里（用于日志与断言说明）。"""
    return _probe_system_temp() is not None


def scratch_reason() -> str:
    """落点选择的理由，供测试输出/排障时说明。"""
    if not _REASON:
        return "系统临时目录可写，未使用仓库目录"
    return "；".join(_REASON)


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
    print("OK")
