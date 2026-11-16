# -*- coding: utf-8 -*-
"""i18n_lint —— 双语字典 / 界面文案 / 服务端错误码覆盖 / 双语演示夹具的发布闸门（纯标准库）。

为什么要这个工具
----------------
`selfcheck.mjs` 在浏览器沙箱里跑界面（67 项），它看得见 DOM，看不见 Python：
服务端返回的错误码有没有对应文案、两份字典的键有没有分叉、英文演示数据里有没有
混进中文——这些只能从仓库文件层面查。本工具把 QA 发布清单里的四条口径变成可执行断言：

1. **字典一致性**：`zh.json` 与 `en.json` 键集合完全一致；值不为空、不是占位
   （TODO/TBD/FIXME/占位符）；en 侧不留中文；en 值与 zh 值相同且 zh 含汉字 → 判为漏译；
   `{param}` 占位参数集合两侧必须一致（漏参数 = 切语言后文案缺字）。
2. **禁止硬编码文案**：`app.js` / `index.html` / `styles.css` 零 CJK 字符，
   并且不出现"句子形状"的英文字面量（≥3 个连续单词）——文案只能来自字典。
   白名单：`*.md`、`tests/`、生成物 `i18n/embed.js`、`i18n/keys.md`、`selfcheck-report.json`、
   数据夹具 `demo-data*.js` / `compare-data.js`（数据本身按语言分文件，不是代码文案）、
   以及 `selfcheck.mjs`（工程师看的断言句，不是界面文案）。
3. **错误码覆盖**：从 `agent_explainer/*.py` 的"携带 code 的位置"提取错误码
   （`code=` 关键字、`{"code": ...}`、`raise X("CODE", ...)`、`*_CODES/_CODE/_ERRORS/_STATUS`
   常量与表键、`== "CODE"` 比较、默认参数），逐个查 `<域>.error.<code小写>` 是否存在。
   缺键**不算失败**（允许回退到服务端 message），但必须打印清单让 QA 查得到；
   `app.js` 的 `*_ERROR_KEYS` 映射里出现的码若缺键，则**判失败**（界面会渲染空白）。
4. **双语演示夹具**：`samples/action_records.json` ↔ `samples/action_records_en.json`
   同 id、同结构、同数值，只有文案换语言；英文集零 CJK、中文集含 CJK；
   `human_readable.language` 与 `source_uri` 自证；`source_facts_hash` 由 `tools/facts_hash.py` 复算一致。

运行：
    python tools/i18n_lint.py                # 人读报告
    python tools/i18n_lint.py --json out.json # 另存机器可读报告
    python tools/i18n_lint.py --strict-codes   # 把"错误码缺键"也当失败（收紧时用）
退出码：0 = 全部硬断言通过；1 = 有问题（逐条给文件 + 键 + 原因）
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "agent_explainer"
WEB = PKG / "web"
I18N = WEB / "i18n"
ZH_PATH = I18N / "zh.json"
EN_PATH = I18N / "en.json"
EMBED_PATH = I18N / "embed.js"
KEYS_PATH = I18N / "keys.md"
APP_PATH = WEB / "app.js"
INDEX_PATH = WEB / "index.html"
STYLES_PATH = WEB / "styles.css"
SAMPLES_ZH = ROOT / "samples" / "action_records.json"
SAMPLES_EN = ROOT / "samples" / "action_records_en.json"

sys.path.insert(0, str(ROOT / "tools"))
import facts_hash  # noqa: E402  （同目录复用：事实指纹的唯一算法来源）

OK, NG, INFO, WARN = "[PASS]", "[FAIL]", "[INFO]", "[WARN]"

CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|XXX|FIXME|lorem|ipsum|untranslated|placeholder|占位|待补|未翻译)\b", re.I)
PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SENTENCE_RE = re.compile(r"[A-Za-z][A-Za-z0-9%.'\-]*"
                         r"(?:[ \t]+[A-Za-z][A-Za-z0-9%.'\-]*){2,}")
STRING_LITERAL_RE = re.compile(r"'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\"")
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*|<!--.*?-->", re.S)

# 错误码所属的界面域：与 app.js 的 <域>.error.<code> 命名一致（docs/03 §6.4）
# M2-PRE 2（PM-VERDICT-R1-6 遗留④）：`rollback` 域纳入强制清单。撤销码族 = 服务端
# EXPANSION_ERROR_TABLE 里的 9 个 ROLLBACK_*（含 `rollback_blocked` 视图原样带出的
# 双 code：ROLLBACK_DEPENDENTS_IRREVERSIBLE / ROLLBACK_ALREADY_ROLLED_BACK），
# 从此"缺键只报 INFO"变成"必须存在 <域>.error.<code> 键，缺则 NG"。
# 卡面上的文案仍走 cards.rollback.error.<code>（R1-2b 已建，DOM 自测钉着它），
# 两套键一一对应；这里新增的是契约侧文案，改文案要同步改两处。
ERROR_DOMAINS: tuple[str, ...] = ("intervene", "auth", "settings", "ingest", "stream",
                                  "audit", "export", "desk", "llm", "rollback")
CODE_CARRYING_SUFFIX = ("_CODE", "_CODES", "_ERROR", "_ERRORS", "_STATUS", "_STATUSES")
# 扫描过的服务端模块（agent_explainer 顶层）
CODED_MODULES: tuple[str, ...] = ("server.py", "settings.py", "auth.py", "ingest.py",
                                  "audit.py", "trace_adapter.py", "explanations.py",
                                  "__init__.py")
# 明确排除：码形状但不是错误码（HTTP 头、常量名、枚举值等），逐条给理由
CODE_EXCLUDE: dict[str, str] = {
    "TRACELENS_ADMIN_PASSWORD": "环境变量名",
    "TRACELNS_ADMIN_INITIAL_PASSWORD": "环境变量名（历史拼写，兼容保留）",
    "TRACELENS_LLM_API_KEY": "环境变量名",
    "TRACELENS_ADMIN_PORT": "环境变量名",
    "TRACELENS_ADMIN_HOST": "环境变量名",
    "SEC_WEBSOCKET_KEY": "HTTP 头名",
    "SEC_WEBSOCKET_ACCEPT": "HTTP 头名",
    "SEC_WEBSOCKET_VERSION": "HTTP 头名",
    "CONTENT_TYPE": "HTTP 头名",
    "CONTENT_LENGTH": "HTTP 头名",
    "ACCEPT_ENCODING": "HTTP 头名",
    "CACHE_CONTROL": "HTTP 头名",
    "RETRY_AFTER": "HTTP 头名",
    "WWW_AUTHENTICATE": "HTTP 头名",
    "NO_STORE": "缓存指令值",
    "TEXT_EVENT_STREAM": "媒体类型",
    "TEXT_HTML": "媒体类型",
    "APPLICATION_JSON": "媒体类型",
    "UTF_8": "编码名",
    "ISO_8859_1": "编码名",
    "SHA_256": "算法名",
    "PSEUDO_HEADER": "内部标记",
}
# index.html 的启动诊断：i18n 还没加载就有机会显示，无法走字典（白名单，逐条给理由）
INDEX_COPY_ALLOWLIST: tuple[str, ...] = (
    "TraceLens static demo skeleton",          # <title> 兜底：字典加载前的文档标题
    "boot: inline probe ran, readyState=",     # 启动探针输出（工程师排障用）
    "probe: window.TraceLensUI never appeared",  # app.js 未加载完的兜底提示
)
COPY_SCAN_FILES: tuple[tuple[str, Path], ...] = (("app.js", APP_PATH),
                                                 ("index.html", INDEX_PATH),
                                                 ("styles.css", STYLES_PATH))


def load_pairs(path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """读字典；同时抓出 JSON 里重复的键（重复键会被静默覆盖，是真实隐患）。"""
    dup: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                dup.append(str(key))
            out[key] = value
        return out

    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh, object_pairs_hook=hook)
    return doc, dup


def flatten(doc: dict[str, Any]) -> dict[str, str]:
    """字典是 {域: {键: 文案}}；全键 = 域 + '.' + 键（app.js 的 t() 用的就是这个）。"""
    flat: dict[str, str] = {}
    for domain, inner in doc.items():
        if not isinstance(inner, dict):
            continue
        for key, value in inner.items():
            flat[f"{domain}.{key}"] = value
    return flat


def check_dictionaries(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    zh_doc, zh_dup = load_pairs(ZH_PATH)
    en_doc, en_dup = load_pairs(EN_PATH)
    for label, dup in (("zh.json", zh_dup), ("en.json", en_dup)):
        if dup:
            problems.append(f"{label} 存在重复键（后者静默覆盖前者）：{sorted(set(dup))[:8]}")
    zh, en = flatten(zh_doc), flatten(en_doc)
    only_zh = sorted(set(zh) - set(en))
    only_en = sorted(set(en) - set(zh))
    if only_zh:
        problems.append(f"只有 zh 有的键 {len(only_zh)} 个（切到 en 会渲染空白）：{only_zh[:8]}")
    if only_en:
        problems.append(f"只有 en 有的键 {len(only_en)} 个（中文界面会渲染空白）：{only_en[:8]}")

    empty, placeholders, en_cjk, untranslated, param_diff = [], [], [], [], []
    for key, value in sorted(zh.items()):
        if not isinstance(value, str) or not value.strip():
            empty.append(key)
        if isinstance(value, str) and PLACEHOLDER_RE.search(value):
            placeholders.append(f"zh:{key}")
    for key, value in sorted(en.items()):
        if not isinstance(value, str) or not value.strip():
            empty.append(f"en:{key}")
            continue
        if PLACEHOLDER_RE.search(value):
            placeholders.append(f"en:{key}")
        if CJK.search(value):
            en_cjk.append(key)
    for key in sorted(set(zh) & set(en)):
        zh_v, en_v = zh[key], en[key]
        if isinstance(zh_v, str) and isinstance(en_v, str) and zh_v == en_v and CJK.search(zh_v):
            untranslated.append(key)
        if isinstance(zh_v, str) and isinstance(en_v, str):
            if set(PARAM_RE.findall(zh_v)) != set(PARAM_RE.findall(en_v)):
                param_diff.append(f"{key}（zh={sorted(set(PARAM_RE.findall(zh_v)))} "
                                  f"en={sorted(set(PARAM_RE.findall(en_v)))}）")
    if empty:
        problems.append(f"空文案 {len(empty)} 处：{empty[:8]}")
    if placeholders:
        problems.append(f"占位/未译文案 {len(placeholders)} 处：{placeholders[:8]}")
    if en_cjk:
        problems.append(f"en 字典含中文 {len(en_cjk)} 处（英文界面出现中文＝K-10 破口）：{en_cjk[:8]}")
    if untranslated:
        problems.append(f"en 与 zh 文案完全相同（判为漏译）{len(untranslated)} 处：{untranslated[:8]}")
    if param_diff:
        problems.append(f"占位参数不一致 {len(param_diff)} 处：{param_diff[:6]}")

    domains = sorted({k.split(".")[0] for k in zh})
    report["keys_zh"], report["keys_en"] = len(zh), len(en)
    report["domains"] = domains
    report["error_keys"] = {d: sum(1 for k in zh if k.startswith(f"{d}.error."))
                            for d in domains if any(k.startswith(f"{d}.error.") for k in zh)}
    report["lines"].append(
        f"{OK if not problems else NG} 字典一致性 —— zh {len(zh)} 键 / en {len(en)} 键 / "
        f"{len(domains)} 个域；error 键 {sum(report['error_keys'].values())} 条"
        + ("" if not problems else "；问题 " + str(len(problems)) + " 处"))
    return problems


def check_web_copy(report: dict[str, Any]) -> list[str]:
    """界面三件套零 CJK + 零句子形状英文字面量（文案只能来自字典）。"""
    problems: list[str] = []
    hits: list[str] = []
    sentences: list[str] = []
    for label, path in COPY_SCAN_FILES:
        if not path.exists():
            problems.append(f"{label} 不存在")
            continue
        raw = path.read_text(encoding="utf-8")
        if CJK.search(raw):
            hits.append(label)
        stripped = COMMENT_RE.sub(" ", raw)
        for match in STRING_LITERAL_RE.finditer(stripped):
            literal = match.group(1) or match.group(2) or ""
            if len(literal) < 24:
                continue
            if any(ch in literal for ch in "{};<>|\\"):
                continue
            if "/" in literal or literal.startswith("http"):
                continue
            if any(allow in literal for allow in INDEX_COPY_ALLOWLIST):
                continue
            found = SENTENCE_RE.search(literal)
            if found and len(found.group(0).split()) >= 3:
                sentences.append(f"{label}: {literal[:70]!r}")
    if hits:
        problems.append(f"界面代码里出现中文字面量：{hits}（文案必须走 i18n 字典）")
    if sentences:
        problems.append(f"界面代码里出现硬编码英文句子 {len(sentences)} 处：{sentences[:6]}")
    report["lines"].append(
        f"{OK if not problems else NG} 禁止硬编码文案 —— 扫描 "
        f"{', '.join(label for label, _ in COPY_SCAN_FILES)}；"
        f"CJK 命中 {len(hits)}，句子字面量 {len(sentences)}；"
        "白名单 *.md / tests / embed.js / keys.md / selfcheck-report.json / demo-data*.js / "
        "compare-data.js / selfcheck.mjs")
    report["hardcoded_sentences"] = sentences
    return problems


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            ids.add(id(body[0].value))
    return ids


def _code_strings(node: ast.AST) -> list[str]:
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                and CODE_RE.match(sub.value) and sub.value not in CODE_EXCLUDE:
            out.append(sub.value)
    return out


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _assign_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return set()
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _is_code_literal(value: str, module: str) -> bool:
    """码的形状：SCREAMING_SNAKE；audit 的链问题码是小写 snake（hash_mismatch 等）。"""
    if value in CODE_EXCLUDE:
        return False
    if CODE_RE.match(value) and len(value) >= 5:
        return True
    if module == "audit.py" and re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", value):
        return True
    return False


def collect_error_codes() -> dict[str, set[str]]:
    """只取“携带 code 的位置”的字符串常量，避免把 `__all__` 的导出名当错误码。

    位置清单（每一条都在 agent_explainer 里真实存在）：
    `code=` / `expected_code=` 关键字；`{"code": "X"}`；`raise X("X", ...)` 首参；
    `== "X"` 与 `in ("A", "B")`；`*_CODE/_CODES/_ERROR/_ERRORS/_STATUS` 常量的值、
    容器元素与表键；函数签名里 `code="X"` 的默认值。文档字符串与 `__all__` 不参与。
    """
    found: dict[str, set[str]] = {}

    def add(code: Any, module: str) -> None:
        if isinstance(code, str) and _is_code_literal(code, module):
            found.setdefault(code, set()).add(module)

    for name in CODED_MODULES:
        path = PKG / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        parents = _parents(tree)
        for node in ast.walk(tree):
            # 函数签名默认值 code="X"
            if isinstance(node, ast.arguments):
                for arg, default in zip(node.args, node.defaults):
                    if arg.arg in ("code", "expected_code"):
                        add(getattr(default, "value", None), name)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                const = node
                if id(const) in skip:
                    continue
                value = const.value
                parent = parents.get(id(const))
                if isinstance(parent, ast.keyword) and parent.arg in ("code", "expected_code"):
                    add(value, name)
                elif isinstance(parent, ast.Dict):
                    index = parent.values.index(const) if const in parent.values else None
                    key = parent.keys[index] if index is not None else None
                    if isinstance(key, ast.Constant) and key.value == "code":
                        add(value, name)          # {"code": "X", ...}
                    else:
                        owner = parents.get(id(parent))
                        owner_names = _assign_names(owner) if owner is not None else set()
                        if any(n.upper().endswith(CODE_CARRYING_SUFFIX) for n in owner_names):
                            add(value, name)      # 码表：INGEST_STATUS = {"X": {...}}
                elif isinstance(parent, ast.Call):
                    owner = parents.get(id(parent))
                    first = parent.args[0] if parent.args else None
                    func = getattr(parent, "func", None)
                    func_name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if first is const and isinstance(owner, ast.Raise):
                        add(value, name)          # raise InterveneError("X", ...)
                    elif first is const and isinstance(func_name, str) \
                            and func_name.lower().endswith(("error", "failure")):
                        add(value, name)          # XError("X", ...) 作为表达式传入
                elif isinstance(parent, ast.Compare):
                    if any(comp is const for comp in parent.comparators):
                        add(value, name)          # code == "X"
                    elif any(const is item for comp in parent.comparators
                             if isinstance(comp, (ast.Tuple, ast.List, ast.Set))
                             for item in comp.elts):
                        add(value, name)          # code in ("A", "B")
                elif isinstance(parent, ast.Constant):
                    continue
                else:
                    owner = parents.get(id(const))
                    steps = 0
                    while owner is not None and not isinstance(owner, (ast.Assign, ast.AnnAssign)) \
                            and steps < 4:
                        owner = parents.get(id(owner))
                        steps += 1
                    if isinstance(owner, (ast.Assign, ast.AnnAssign)):
                        names = _assign_names(owner)
                        if any(n.upper().endswith(CODE_CARRYING_SUFFIX) for n in names):
                            add(value, name)      # CODES = "X" / CODES = ("A", "B")
    return found


def _client_error_maps() -> dict[str, dict[str, str]]:
    """app.js 里的 *_ERROR_KEYS 映射：码 → <域>.error.<code> 键。"""
    if not APP_PATH.exists():
        return {}
    text = APP_PATH.read_text(encoding="utf-8")
    maps: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9_]*ERROR_KEYS)\s*=\s*\{", text):
        name = match.group(1)
        start = text.index("{", match.start())
        depth, index = 0, start
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        block = text[start:index + 1]
        pairs = dict(re.findall(r"'([A-Z][A-Z0-9_]+)'\s*:\s*'([a-z_][a-z0-9_.]*)'", block))
        pairs.update(dict(re.findall(r"\b([A-Z][A-Z0-9_]+)\s*:\s*'([a-z_][a-z0-9_.]*)'", block)))
        if pairs:
            maps[name] = pairs
    return maps


def check_error_codes(report: dict[str, Any], strict: bool) -> list[str]:
    zh, _ = flatten(load_pairs(ZH_PATH)[0]), None
    codes = collect_error_codes()
    keys = set(zh)
    with_key, missing = [], []
    for code in sorted(codes):
        if any(f"{domain}.error.{code.lower()}" in keys for domain in ERROR_DOMAINS):
            with_key.append(code)
        else:
            missing.append(code)
    maps = _client_error_maps()
    broken_map: list[str] = []
    for map_name, pairs in maps.items():
        for code, key in sorted(pairs.items()):
            if key not in keys:
                broken_map.append(f"{map_name}[{code}] -> 缺键 {key}")
    problems: list[str] = []
    if broken_map:
        problems.append(f"app.js 错误码映射指向不存在的键 {len(broken_map)} 处："
                        f"{broken_map[:8]}")
    if strict and missing:
        problems.append(f"--strict-codes：{len(missing)} 个码没有 <域>.error.<code> 键")
    report["codes_total"] = len(codes)
    report["codes_with_key"] = len(with_key)
    report["codes_missing"] = missing
    report["client_maps"] = {name: len(pairs) for name, pairs in sorted(maps.items())}
    report["lines"].append(
        f"{OK if not problems else NG} 错误码覆盖 —— 从 {'/'.join(CODED_MODULES)} 取到码 "
        f"{len(codes)} 个；有 <域>.error.<code> 键 {len(with_key)} 个；"
        f"无键 {len(missing)} 个（允许回退 message，清单见 --json 报告）；"
        f"app.js 映射 {', '.join(f'{n}={c}' for n, c in sorted(report['client_maps'].items())) or '无'}")
    report["lines"].append(f"[INFO] 无键码清单（QA 可逐条查回退文案）："
                           f"{', '.join(missing) if missing else '无'}")
    return problems


def _embed_payload(text: str, lang: str) -> dict[str, Any]:
    """embed.js 形如 global.TraceLensDicts = { zh: {...}, en: {...} };（内层是合法 JSON）。"""
    marker = re.search(rf"\b{lang}\s*:\s*\{{", text)
    if not marker:
        raise ValueError(f"embed.js 里找不到 {lang} 块")
    start = text.index("{", marker.start())
    depth, index = 0, start
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                break
        index += 1
    return json.loads(text[start:index + 1])


def check_generated_artifacts(report: dict[str, Any]) -> list[str]:
    """生成物漂移：embed.js 必须等于两份字典；keys.md 必须列出全部键。"""
    problems: list[str] = []
    zh_doc, _ = load_pairs(ZH_PATH)
    en_doc, _ = load_pairs(EN_PATH)
    zh, en = flatten(zh_doc), flatten(en_doc)
    if not EMBED_PATH.exists():
        problems.append("i18n/embed.js 缺失（file:// 离线打开会没有文案）")
    else:
        text = EMBED_PATH.read_text(encoding="utf-8")
        try:
            embed_zh = flatten(_embed_payload(text, "zh"))
            embed_en = flatten(_embed_payload(text, "en"))
        except (ValueError, json.JSONDecodeError) as exc:
            problems.append(f"embed.js 解析失败：{exc}")
            embed_zh = embed_en = {}
        for label, embedded, canonical in (("zh", embed_zh, zh), ("en", embed_en, en)):
            lost = sorted(set(canonical) - set(embedded))
            extra = sorted(set(embedded) - set(canonical))
            diff = [k for k in sorted(set(canonical) & set(embedded))
                    if canonical[k] != embedded[k]]
            if lost:
                problems.append(f"embed.js({label}) 缺 {len(lost)} 键（请跑 "
                                f"node selfcheck.mjs --sync-embed）：{lost[:6]}")
            if extra:
                problems.append(f"embed.js({label}) 多 {len(extra)} 键（字典已删）：{extra[:6]}")
            if diff:
                problems.append(f"embed.js({label}) 有 {len(diff)} 键文案与字典不一致：{diff[:6]}")
    if not KEYS_PATH.exists():
        problems.append("i18n/keys.md 缺失（请跑 node selfcheck.mjs --sync-keys）")
    else:
        text = KEYS_PATH.read_text(encoding="utf-8")
        listed = set(re.findall(r"^\|\s*`([^`]+)`\s*\|", text, re.M))
        missing = sorted(set(zh) - listed)
        ghost = sorted(listed - set(zh))
        if missing:
            problems.append(f"keys.md 缺 {len(missing)} 键（请跑 --sync-keys）：{missing[:6]}")
        if ghost:
            problems.append(f"keys.md 列了字典里不存在的键 {len(ghost)} 个：{ghost[:6]}")
        total = re.search(r"Key 总数：(\d+)", text)
        if total and int(total.group(1)) != len(zh):
            problems.append(f"keys.md 声明 {total.group(1)} 键，字典实际 {len(zh)} 键")
    report["lines"].append(f"{OK if not problems else NG} 生成物一致 —— embed.js / keys.md 与字典同步")
    return problems


def check_bilingual_samples(report: dict[str, Any]) -> list[str]:
    """双语演示夹具：同 id / 同结构 / 同数值，只有文案换语言。"""
    problems: list[str] = []
    if not SAMPLES_ZH.exists():
        return [f"缺少中文夹具 {SAMPLES_ZH.relative_to(ROOT)}"]
    if not SAMPLES_EN.exists():
        return [f"缺少英文夹具 {SAMPLES_EN.relative_to(ROOT)}"
                "（英文界面会退回中文数据）"]
    zh_doc = json.loads(SAMPLES_ZH.read_text(encoding="utf-8"))
    en_doc = json.loads(SAMPLES_EN.read_text(encoding="utf-8"))
    zh_records = zh_doc.get("records", [])
    en_records = en_doc.get("records", [])
    if [r.get("id") for r in zh_records] != [r.get("id") for r in en_records]:
        problems.append("两份演示集的 id 序列不一致（切换语言时卡片会对不上）")
    if len(zh_records) != len(en_records):
        problems.append(f"演示集条数不一致：zh {len(zh_records)} / en {len(en_records)}")

    def cjk_in(node: Any) -> int:
        if isinstance(node, dict):
            return sum(cjk_in(v) for v in node.values())
        if isinstance(node, list):
            return sum(cjk_in(v) for v in node)
        if isinstance(node, str):
            return len(CJK.findall(node))
        return 0

    en_cjk = cjk_in(en_doc)
    zh_cjk = cjk_in(zh_doc)
    if en_cjk:
        problems.append(f"英文演示集含 {en_cjk} 个中文字符（英文界面出现真实中文数据）")
    if not zh_cjk:
        problems.append("中文演示集没有任何中文（夹具被改坏）")
    pairs = list(zip(zh_records, en_records))
    for zh_rec, en_rec in pairs:
        rid = str(en_rec.get("id"))
        if set(zh_rec) != set(en_rec):
            only = sorted(set(zh_rec) ^ set(en_rec))
            problems.append(f"{rid} 字段集合不一致：{only[:6]}")
            continue
        for field in ("schema_version", "status", "action_type", "risk_level", "latency_ms",
                      "confidence", "requires_action", "sequence_no", "timestamp", "received_at",
                      "cost", "labels", "id", "agent_id", "session_id", "run_id", "demo_data",
                      "synthetic", "depth"):
            if zh_rec.get(field) != en_rec.get(field):
                problems.append(f"{rid} 的 {field} 在两份演示集里不同（数据必须一致，只有文案换语言）")
        zh_hr = zh_rec.get("human_readable") or {}
        en_hr = en_rec.get("human_readable") or {}
        if str(zh_hr.get("language")) != "zh" or str(en_hr.get("language")) != "en":
            problems.append(f"{rid} 的 human_readable.language 不是 zh/en 一对")
        ok, declared, computed = facts_hash.verify(en_rec)
        if not ok:
            problems.append(f"{rid} 的 source_facts_hash 与英文事实不一致："
                            f"declared={declared} computed={computed}")
        if en_rec.get("source", {}).get("source_uri") != "samples/action_records_en.json":
            problems.append(f"{rid} 的 source.source_uri 未指向 samples/action_records_en.json")
    meta = en_doc.get("_meta") or {}
    if meta.get("record_count") != len(en_records):
        problems.append(f"英文夹具 _meta.record_count={meta.get('record_count')} 与实际 "
                        f"{len(en_records)} 条不符")
    if str(meta.get("language")) != "en":
        problems.append("英文夹具 _meta.language 不是 en")
    report["sample_records"] = len(zh_records)
    report["sample_en_cjk"] = en_cjk
    report["lines"].append(
        f"{OK if not problems else NG} 双语演示夹具 —— {len(zh_records)} 条 x 2 套；"
        f"英文集中文字符 {en_cjk}，中文集中文字符 {zh_cjk}；facts 指纹逐条复算")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="i18n / 文案 / 错误码覆盖 / 双语夹具 发布闸门")
    ap.add_argument("--json", metavar="PATH", help="把机器可读报告写入该路径")
    ap.add_argument("--strict-codes", action="store_true",
                    help="把「错误码缺 <域>.error.<code> 键」也判为失败")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总行")
    args = ap.parse_args(argv)

    report: dict[str, Any] = {"checks": [], "lines": ["=" * 78,
                                    "i18n / 文案 / 契约 发布闸门（tools/i18n_lint.py）", "=" * 78]}
    checks = [("字典一致性", lambda: check_dictionaries(report)),
              ("禁止硬编码文案", lambda: check_web_copy(report)),
              ("错误码覆盖", lambda: check_error_codes(report, args.strict_codes)),
              ("生成物一致", lambda: check_generated_artifacts(report)),
              ("双语演示夹具", lambda: check_bilingual_samples(report))]
    failed = 0
    for label, fn in checks:
        try:
            problems = fn()
        except Exception as exc:  # noqa: BLE001 - 工具必须把异常变成可读失败
            problems = [f"{type(exc).__name__}: {exc}"]
            report["lines"].append(f"{WARN} {label} 执行异常，按失败处理")
        report["checks"].append({"name": label, "problems": problems,
                                 "passed": not problems})
        if problems:
            failed += 1
            for problem in problems:
                report["lines"].append(f"    · {problem}")
    report["passed"] = failed == 0
    report["summary"] = (f"检查项 {len(checks)} 项：通过 {len(checks) - failed}，失败 {failed}；"
                         f"字典 zh {report.get('keys_zh')} / en {report.get('keys_en')} 键；"
                         f"错误码 {report.get('codes_total')} 个（有键 {report.get('codes_with_key')}，"
                         f"回退 message {len(report.get('codes_missing', []))}）；"
                         f"演示集 {report.get('sample_records')} 条 x 双语")
    if not args.quiet:
        for line in report["lines"]:
            print(line)
    print("-" * 78)
    print(f"{OK if report['passed'] else NG} {report['summary']}")

    if args.json:
        payload = {k: v for k, v in report.items() if k != "lines"}
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"机器可读报告 -> {args.json}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
