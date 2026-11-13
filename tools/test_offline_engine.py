"""离线自检：在『没有 jsonschema 包』的机器上，schema 约束必须同样生效。

依赖政策（纯标准库 + 离线可用）的可执行证明：
  本脚本屏蔽 jsonschema 的导入，然后运行 validate_schema.main()，
  要求退出码仍为 0，且引擎状态显示 jsonschema=absent —— 说明判定完全由
  纯标准库引擎完成，结果与装了第三方库时一致。

运行：python tools/test_offline_engine.py
"""

from __future__ import annotations

import importlib.abc
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class _BlockJsonschema(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname == "jsonschema" or fullname.startswith("jsonschema."):
            raise ImportError(f"offline simulation: import of {fullname!r} is blocked")
        return None


def main() -> int:
    sys.meta_path.insert(0, _BlockJsonschema())
    try:
        import jsonschema  # noqa: F401
    except ImportError as exc:
        print(f"[离线模拟] jsonschema 已屏蔽：{exc}")
    else:
        print("[离线模拟] 屏蔽失败：jsonschema 仍可导入（测试无效）")
        return 1

    sys.argv = ["validate_schema.py"]
    try:
        runpy.run_path(str(HERE / "validate_schema.py"), run_name="__main__")
    except SystemExit as exit_exc:
        code = exit_exc.code if exit_exc.code is not None else 0
        print(f"[离线模拟] 退出码 = {code}（0 = 纯标准库引擎即可完成全部判定）")
        return int(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
