#!/usr/bin/env python3
"""更新包打包器（UPDATE-CHANNEL-1）。

把一个版本的「界面层（web）」与可选的「服务层（runtime/agent_explainer）」
打成单个 ``.tlpkg``（其实就是 zip），并维护更新源根目录的 ``index.json``。

用法::

    # 只打界面层（最快，加一个面板/改文案用这个）
    python tools/make_update_package.py --version 0.6.1 --notes "导出面板支持时间范围"

    # 界面层 + Python 业务包（改了 audit.py / server.py 用这个）
    python tools/make_update_package.py --version 0.6.2 --with-runtime --min-host 0.6.0

    # 输出到指定目录（例如 U 盘或共享盘）
    python tools/make_update_package.py --version 0.6.1 --out D:/tracelens-updates

设计约束（照 `docs/21-update-channel.md`）：

* **纯标准库**（`zipfile` / `hashlib` / `json`）—— 打包机不需要装任何东西。
* **确定性产物**：同输入必然同 sha256。zip 内时间戳固定为 1980-01-01、条目按
  路径排序、压缩级别固定。否则「同版本两次打包哈希不同」会让校验形同虚设，
  也没法在 CI 里比对。
* **只打声明过的东西**：不递归整个仓库，只收 `web/` 与 `agent_explainer/`，
  且排除 ``__pycache__`` / ``.pyc`` / 测试残留。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
WEB_DIR = REPO / "agent_explainer" / "web"
PKG_DIR = REPO / "agent_explainer"
PACKAGE_SCHEMA = "tracelens.update-package/1"
INDEX_SCHEMA = "tracelens.update-index/1"

# zip 内统一时间戳：让"同输入同输出"成立（zipfile 会写 mtime，不固定就会漂）
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
COMPRESS_LEVEL = 9

# web 层里不该进包的东西：源码映射、调试残留、编辑器临时文件
WEB_EXCLUDE_SUFFIXES = {".map", ".bak", ".orig", ".tmp", ".log"}
WEB_EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}
RUNTIME_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
RUNTIME_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_web_files(root: pathlib.Path) -> list[pathlib.Path]:
    """web 层的全部文件（排序后返回，保证确定性）。"""
    out: list[pathlib.Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name in WEB_EXCLUDE_NAMES or path.suffix.lower() in WEB_EXCLUDE_SUFFIXES:
            continue
        out.append(path)
    return out


def iter_runtime_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Python 业务包（不含 web，web 走单独一层；不含缓存与编译产物）。"""
    out: list[pathlib.Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in RUNTIME_EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in RUNTIME_EXCLUDE_SUFFIXES:
            continue
        if rel.parts and rel.parts[0] == "web":
            continue
        out.append(path)
    return out


def collect(source_root: pathlib.Path, with_runtime: bool) -> list[tuple[str, pathlib.Path]]:
    """返回 [(包内路径, 磁盘路径)]。包内路径一律正斜杠。"""
    items: list[tuple[str, pathlib.Path]] = []
    web_root = source_root / "agent_explainer" / "web"
    if not web_root.is_dir():
        raise SystemExit(f"找不到界面目录：{web_root}")
    for path in iter_web_files(web_root):
        items.append((f"web/{path.relative_to(web_root).as_posix()}", path))
    if with_runtime:
        pkg_root = source_root / "agent_explainer"
        for path in iter_runtime_files(pkg_root):
            items.append((f"runtime/agent_explainer/{path.relative_to(pkg_root).as_posix()}", path))
    items.sort(key=lambda pair: pair[0])
    return items


def build_manifest(items, *, version: str, min_host: str | None, channel: str,
                   notes: str, capabilities: list[str]) -> dict:
    files = []
    for arc, path in items:
        data = path.read_bytes()
        files.append({"path": arc, "size": len(data), "sha256": sha256_bytes(data)})
    return {
        "schema": PACKAGE_SCHEMA,
        "version": version,
        "channel": channel,
        "min_host": min_host,
        "capabilities": sorted(set(capabilities)),
        "created_at": "1970-01-01T00:00:00Z",   # 固定值：确定性产物不带生成时刻
        "notes": notes,
        "files": files,
    }


def write_package(target: pathlib.Path, manifest: dict, items) -> int:
    """写 zip。条目顺序 = manifest 顺序（已排序），时间戳固定。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=COMPRESS_LEVEL) as zf:
        _writestr(zf, "manifest.json", manifest_bytes)
        for arc, path in items:
            _writestr(zf, arc, path.read_bytes())
    return target.stat().st_size


def _writestr(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    info.create_system = 3          # unix，跨平台一致
    zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL)


def update_index(out_dir: pathlib.Path, entry: dict, channel: str) -> dict:
    """把这条 release 合并进 index.json（同版本覆盖，按版本号升序排列）。"""
    index_path = out_dir / "index.json"
    doc = {"schema": INDEX_SCHEMA, "channel": channel, "latest": None, "releases": []}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("schema") == INDEX_SCHEMA:
                doc = loaded
            else:
                raise ValueError("schema 不匹配")
        except Exception as exc:                      # noqa: BLE001
            sys.stderr.write(f"⚠ 现有 index.json 无法解析（{exc}），将重建\n")

    releases = [r for r in doc.get("releases", []) if isinstance(r, dict)
                and r.get("version") != entry["version"]]
    releases.append(entry)
    releases.sort(key=lambda r: _version_key(str(r.get("version", "0"))))
    doc["releases"] = releases
    doc["latest"] = releases[-1]["version"] if releases else None
    doc["channel"] = channel
    index_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def _version_key(text: str) -> tuple:
    core = text.split("-", 1)[0].lstrip("v")
    parts = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    pre = text.split("-", 1)[1] if "-" in text else ""
    # 有预发布标识的排在正式版之前（与 semver 一致）
    return (parts[0], parts[1], parts[2], 0 if pre else 1, pre)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把当前工作树打成 TraceLens 更新包（.tlpkg）并更新 index.json")
    parser.add_argument("--version", required=True, help="包版本，例如 0.6.1")
    parser.add_argument("--out", default=str(REPO / "release" / "updates"),
                        help="输出目录（更新源根目录），默认 release/updates")
    parser.add_argument("--source", default=str(REPO), help="源码根目录，默认本仓库")
    parser.add_argument("--with-runtime", action="store_true",
                        help="同时打 Python 业务包（改了 audit.py / server.py 才需要）")
    parser.add_argument("--min-host", default=None,
                        help="宿主最低版本；不给则不限制")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta"])
    parser.add_argument("--notes", default="", help="这一版做了什么（会显示在界面上）")
    parser.add_argument("--no-index", action="store_true", help="只出包，不动 index.json")
    args = parser.parse_args(argv)

    source_root = pathlib.Path(args.source).resolve()
    out_dir = pathlib.Path(args.out).resolve()
    items = collect(source_root, args.with_runtime)
    if not items:
        raise SystemExit("没有可打包的文件")

    capabilities = ["web"] + (["runtime"] if args.with_runtime else [])
    manifest = build_manifest(items, version=args.version, min_host=args.min_host,
                              channel=args.channel, notes=args.notes,
                              capabilities=capabilities)

    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_name = f"tracelens-{args.version}.tlpkg"
    target = out_dir / pkg_name
    if target.exists():
        target.unlink()
    size = write_package(target, manifest, items)
    digest = sha256_bytes(target.read_bytes())

    web_count = sum(1 for arc, _ in items if arc.startswith("web/"))
    rt_count = sum(1 for arc, _ in items if arc.startswith("runtime/"))
    print(f"包：{target}")
    print(f"  版本 {args.version} · 能力 {','.join(capabilities)} · "
          f"{len(items)} 个文件（web {web_count} / runtime {rt_count}）")
    print(f"  大小 {size:,} 字节 · sha256 {digest}")

    if not args.no_index:
        entry = {
            "version": args.version,
            "file": pkg_name,
            "size": size,
            "sha256": digest,
            "capabilities": capabilities,
            "min_host": args.min_host,
            "notes": args.notes,
            "published_at": "1970-01-01T00:00:00Z",
        }
        doc = update_index(out_dir, entry, args.channel)
        print(f"index：{out_dir / 'index.json'}（latest={doc['latest']}，"
              f"共 {len(doc['releases'])} 个版本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
