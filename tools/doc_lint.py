"""doc_lint —— 文档质量闸门（纯标准库）：表格列数一致、代码块闭合、本地链接可达、长度统计。

用途：本团队文档以表格为主，列数错位的表格在渲染时会整体错位（读者第一眼就崩）。
本脚本把"看起来没问题"变成可执行断言，t3-t17 的文档同样适用。

运行：
    python tools/doc_lint.py docs/01-product-and-users.md docs/02-data-model.md docs/03-architecture.md
退出码：0 = 全部通过；1 = 有问题（逐条给出文件 + 行号）
"""

from __future__ import annotations

import os
import re
import sys


def lint(path: str) -> list[str]:
    problems: list[str] = []
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    fence = 0
    table_start, table_cols = None, 0
    in_code = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            fence += 1
            if table_start is not None:
                table_start = None
            continue
        if in_code:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            # 转义竖线 \| 是单元格内容，不是列分隔符
            safe = stripped.replace("\\|", "\u0000")
            cols = len([c for c in safe.split("|")[1:-1]])
            if table_start is None:
                table_start, table_cols = i, cols
            elif cols != table_cols:
                problems.append(f"{path}:{i} 表格列数 {cols} 与表头 {table_cols} 不一致（表起 {table_start}）")
        else:
            if stripped.strip() and table_start is not None:
                table_start = None
            continue

    if in_code:
        problems.append(f"{path} 代码块未闭合（``` 计数 {fence}）")

    text = "\n".join(lines)
    base_dir = os.path.dirname(os.path.abspath(path))
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
        if re.match(r"^[a-z]+://", target) or target.startswith("#") or target.startswith("mailto:"):
            continue
        clean = target.split("#")[0]
        if not clean:
            continue
        full = os.path.normpath(os.path.join(base_dir, clean))
        if not os.path.exists(full):
            problems.append(f"{path} 本地链接不可达：{target}（{label}）")

    print(f"[INFO] {path}: {len(text)} 字符 / {len(lines)} 行 / {text.count(chr(10) + '|')} 表格行")
    return problems


def main(argv: list[str]) -> int:
    all_problems: list[str] = []
    for path in argv:
        all_problems.extend(lint(path))
    if all_problems:
        for p in all_problems:
            print(f"[FAIL] {p}")
        print(f"共 {len(all_problems)} 处问题")
        return 1
    print(f"[PASS] {len(argv)} 份文档：表格列数一致、代码块闭合、本地链接全部可达")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
