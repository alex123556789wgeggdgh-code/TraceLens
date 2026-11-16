#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从代码生成机器可读接口契约 `docs/api_contract.json`，并与 docs/03 §6.5 逐条比对。

为什么要有这个文件（4.7 / t20，闭合 docs/03 差异记录第 16 项）：
docs/03 第 6.3 节是给人读的接口表，6.5 是"文档有没有跟上代码"的人工核对表。
两者都会漂移：路径改名、权限口径变更、新增 verb，人眼看不出来。
本脚本把契约的**唯一来源**定为代码，产出一份机器可读 JSON，
任何一端（前端 `API` 常量、SDK、QA 用例、文档）都能拿它做基准比对。

提取方法（全部是 AST，不做正则抓字符串，不 import 业务代码，不执行任何服务代码）：

1. **路径常量** = `agent_explainer/server.py` 模块级字面量赋值
   （`API_PATH_*`、`API_PATHS_PUBLIC`、`PERMISSION_*` 等）。
2. **路由分发** = `TraceLensHandler.do_GET/do_POST/do_PUT/do_PATCH/do_DELETE/do_OPTIONS/do_HEAD`
   里的 `if` 分支条件：
   - `path == CONST`、`path == CONST + "/"` → 精确路径（末尾斜杠记为 `accepts_trailing_slash`）
   - `path in (A, B)` → 同一分支的多条精确路径（别名）
   - `path.startswith(CONST + "/")` → 资源族，写成 `<前缀>/{id}`
   - `user_route is not None` / `ingest_route is not None` → 由 `ROUTE_HELPERS`
     把路由助手（`_user_route` / `_ingest_token_route`）映射成资源族，
     因为它们把"路径形状"这件事写在函数里而不是分支条件里
   每个分支同时抽取：权限能力（`_require("x.y")` / `_session_and_permission(能力)`，
   能力常量会解析成字符串）、`_login_required()`、`_csrf_guard(...)`、
   机器凭证（`_ingest_secret()`）、调用的 `self.service.<方法>`、
   抛出的码（`_bad("CODE")` / `send_error_body("CODE")`）、是否 SSE（`_serve_stream`）。
3. **角色能力矩阵** = `agent_explainer/auth.py` 的 `ROLE_PERMISSIONS` 字面量（不 import）。
4. **错误码** = 复用 `tools/i18n_lint.py` 的 `collect_error_codes()` 与
   `<域>.error.<code 小写>` 判定，两个工具用同一套口径，QA 只需要看一处。
5. **前端调用面** = `web/app.js` 的 `API_ENDPOINTS` 常量（路径字符串），
   用来发现"后端有路由但界面没调用"和"界面调用了不存在的路由"。

稳定性（可重复生成）：输出按 key 排序、按 (path, verb) 排序、不含时间戳；
源文件用 sha256 记录在 `sources` 里。同一份代码两次生成必须字节一致。

用法：
    python tools/gen_api_contract.py --write    # 生成/更新 docs/api_contract.json
    python tools/gen_api_contract.py --check    # 重新生成并比对（磁盘内容 + docs/03 §6.5）
    python tools/gen_api_contract.py --json PATH  # 把契约另存一份（QA 取证用）

退出码：0 = 契约与磁盘、docs/03 §6.5 全部一致；非 0 = 有差异（打印逐条差异）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "agent_explainer"
SERVER = PKG / "server.py"
AUTH = PKG / "auth.py"
APP_JS = PKG / "web" / "app.js"
DOC = REPO / "docs" / "03-architecture.md"
CONTRACT = REPO / "docs" / "api_contract.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n_lint  # noqa: E402  同目录工具：错误码口径的唯一来源

SCHEMA_VERSION = "1.0"

#: 路由助手 → 资源族（形状写在助手函数里，分支条件只看返回值）
ROUTE_HELPERS = {
    "user_route": "/api/users/{id}/{verb}",
    "ingest_route": "/api/ingest/tokens/{id}/revoke",
    "identity_route": "/api/secretariat/identities/{id}/revoke",
    "record_sign_route": "/api/secretariat/records/{id}/sign",
    "letter_route": "/api/secretariat/letters/{id}",
    "letter_verify_route": "/api/secretariat/letters/{id}/verify",
    # 0.6.0：一个助手可能对应**多条**形状（同一段解析同时接住裸 id 与子动作）。
    # `_evidence_route()` 返回 (id, action)，action 为 ""/"file"/"feedback"，
    # 但只有一个分支体，所以此前抽取器完全看不见 `/api/evidence/*`，
    # 常量 `API_PATH_EVIDENCE` 就被判成「声明了没人接住」。这里按形状列出来。
    # 注意：`/feedback` 的 GET 是显式 405、POST 才服务，而 405 分支不算路由，
    # 故不列；T16 的挂载登记在 docs/07 §5 与 docs/03 §6.5 的说明列（与 T29/T30 同例）。
    "evidence_route": ["/api/evidence/{id}", "/api/evidence/{id}/file"],
    # 记录子资源（`_record_sub_route(path, verb)`）：0.6.0 之前这个解析器**定义了但零调用**，
    # 于是 T13（挂证据）与 T17（因果写回）在 HTTP 面上从未存在，契约里也因此两条都没有 ——
    # "抽取看不见"和"路由不存在"刚好长得一样，正是这次漏了这么久的原因。
    "record_evidence_route": "/api/records/{id}/evidence",
    "record_causal_route": "/api/records/{id}/causal",
}
#: 每个助手允许的方法（与 do_* 里出现的分支一致）
ROUTE_HELPER_VERBS = {
    "user_route": {"POST"},
    "ingest_route": {"POST"},
    "identity_route": {"POST"},
    "record_sign_route": {"POST"},
    "letter_route": {"GET"},
    "letter_verify_route": {"POST"},
    "evidence_route": {"GET"},
    "record_evidence_route": {"GET", "POST"},
    "record_causal_route": {"POST"},
}

# --------------------------------------------------------------------------- #
# 登记不修：另外五个助手族**只登记事实，不进数据表**
# （PM-VERDICT-M2-PRE §3-1 / PM-VERDICT-R4-1 §3-3，挂 R8-1 落账；全文见 docs/07 §11.22 第 5 项）
#
# do_GET / do_POST 里还有一批路径解析助手，它们的族名与解析形状是：
#
#   evidence_route     → /api/evidence/{id}            （T14/T15：`_evidence_route`）
#   group_route        → /api/groups/{id}/{verb}       （T19/T20/T21：`_group_route`，close 解析到但无分支）
#   share_revoke_route → /api/shares/{id}/revoke       （T23：`_share_revoke_route`）
#   suggestion_route   → /api/suggestions/{id}/{verb}  （T29/T30：`_suggestion_route`）
#   compliance_route   → /api/compliance/{verb}        （T32/T33/T34：`_compliance_route`）
#
# 这五族的路径**已经**由同一条分支里的字面 `==` / `startswith` 条件进契约（`branch_route`
# 的 startswith 分支）。它们**故意不**登记进上面的 `ROUTE_HELPERS`：一登记，
# `branch_route` 就会给这些分支多产出"助手骨架"，而骨架的方法过滤是**整族**粒度
# （`verb not in ROUTE_HELPER_VERBS[helper]`），405 归集（`method_not_allowed`）又发生在
# 过滤**之前**——于是 `POST /api/groups/{id}/close` 这类今天实测落 404 的家族路径会被写进
# 契约的 405 清单，成为**假事实**。
# 放开某族的正确做法：给该助手补一个"方法→分支"的显式来源（解析器认识每条分支的方法），
# 再登记 + `--write` 重生成；那是路由契约变更，须 PM 批准。
#
# 0.6.0 L1（八功能接线）对本段的修订：登记了**三个** helper，都是"每条分支只服务它自己的
# 方法"的形状，不会产出假事实（登记后 405 组仍是 3 组，实测未变）：
#   evidence_route         → /api/evidence/{id} + /{id}/file   （只挂 GET；/feedback 的 GET 是
#                            显式 405，故意不入 patterns —— 一入就会造出"GET feedback 可服务"的假事实）
#   record_evidence_route  → /api/records/{id}/evidence        （GET + POST，两条分支各自成对）
#   record_causal_route    → /api/records/{id}/causal          （只有 POST）
# 修订前这三个族在契约里**完全隐形**，于是「抽取看不见」与「路由不存在」长得一模一样 ——
# 八功能里 T13/T17 两条断了这么久没被发现，根因就在这里。
# 现状：契约 61 条路由 / 44 常量（全部挂载）/ 5 公开 / 3 组 405 / 前端 30 条全部命中，
# 未挂载常量从 2 条降为 0 条。
# --------------------------------------------------------------------------- #

OK = "[PASS]"
NG = "[FAIL]"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def module_constants(tree: ast.Module) -> dict[str, Any]:
    """模块级字面量赋值（含类型注解赋值），只收 str / 数字 / tuple / list / dict / set。

    第二遍把 `API_PATHS_PUBLIC = (API_PATH_HEALTH, "/healthz", …)` 这种"名字组成的
    容器"解析成字符串容器：白名单本身就是契约的一部分，不能因为用了常量名就看不见。
    """
    consts: dict[str, Any] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        try:
            literal = ast.literal_eval(value)
        except Exception:
            literal = None
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else \
                getattr(target, "attr", None)
            if isinstance(name, str):
                consts.setdefault(name, literal)
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            strings = resolve_strings(value, consts)
            if strings and len(strings) == len(value.elts):
                for target in targets:
                    name = target.id if isinstance(target, ast.Name) else \
                        getattr(target, "attr", None)
                    if isinstance(name, str):
                        consts[name] = tuple(strings)
    return consts


def resolve_strings(node: ast.expr, consts: dict[str, Any]) -> list[str]:
    """把 `CONST` / `"/lit"` / `CONST + "/suffix"` / `(A, B)` 解析成字符串清单。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        value = consts.get(node.id)
        return [value] if isinstance(value, str) else []
    if isinstance(node, ast.Attribute):
        value = consts.get(node.attr)
        return [value] if isinstance(value, str) else []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = resolve_strings(node.left, consts)
        right = resolve_strings(node.right, consts)
        if left and right:
            return [a + b for a in left for b in right]
        return left or right
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for item in node.elts:
            out.extend(resolve_strings(item, consts))
        return out
    return []


def normalize(pattern: str) -> str:
    text = pattern.strip()
    if text.endswith("/") and not text.endswith("/{"):
        text = text.rstrip("/")
    return text or "/"


def calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def keyword_code(call: ast.Call) -> str | None:
    for const in (call.args[0] if call.args else None,
                  *(kw.value for kw in call.keywords if kw.arg == "code")):
        if isinstance(const, ast.Constant) and isinstance(const.value, str):
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", const.value):
                return const.value
    return None


# --------------------------------------------------------------------------- #
# 路由抽取
# --------------------------------------------------------------------------- #
def branch_route(test: ast.expr, consts: dict[str, Any]) -> list[dict[str, Any]]:
    """把一个 `if` 条件解析成 0..n 条路由骨架（不含 verb）。"""
    out: list[dict[str, Any]] = []

    def exact(node: ast.expr, trailing: bool) -> None:
        for value in resolve_strings(node, consts):
            out.append({"pattern": normalize(value), "match": "exact",
                        "trailing_slash": trailing})

    if isinstance(test, ast.Compare):
        ops = list(zip(test.ops, test.comparators))
        left = test.left
        left_name = left.id if isinstance(left, ast.Name) else \
            getattr(left, "attr", None)
        for op, comp in ops:
            if isinstance(op, ast.Eq) and left_name == "path":
                exact(comp, isinstance(comp, ast.BinOp)
                      and comp.op.__class__.__name__ == "Add"
                      and resolve_strings(comp, consts)
                      and any(s.endswith("/") for s in resolve_strings(comp, consts)))
            elif isinstance(op, ast.In) and left_name == "path":
                exact(comp, False)
            elif isinstance(op, (ast.Is, ast.IsNot)) and isinstance(left, ast.Name) \
                    and left.id in ROUTE_HELPERS \
                    and isinstance(comp, ast.Constant) and comp.value is None:
                # 一个助手可以声明多条形状（见 ROUTE_HELPERS 的 evidence_route）。
                declared = ROUTE_HELPERS[left.id]
                for pattern in ([declared] if isinstance(declared, str) else declared):
                    out.append({"pattern": pattern, "match": "family",
                                "helper": left.id, "trailing_slash": False})
    elif isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) \
            and test.func.attr == "startswith" and test.args:
        base = test.func.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if base_name == "path":
            for value in resolve_strings(test.args[0], consts):
                out.append({"pattern": normalize(value) + "/{id}", "match": "family",
                            "trailing_slash": False})
    elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        seen = set()
        for value in test.values:
            for route in branch_route(value, consts):
                key = (route["pattern"], route["match"])
                if key not in seen:
                    seen.add(key)
                    out.append(route)
    return out


def branch_facts(body: list[ast.stmt], consts: dict[str, Any]) -> dict[str, Any]:
    """抽取分支内的权限口径 / CSRF / 处理器 / 抛出的码。"""
    facts: dict[str, Any] = {"abilities": [], "login": False, "csrf": False,
                            "machine_credential": False, "handler": "", "sse": False,
                            "codes": [], "rate_limited": False}
    for call in calls_in(ast.Module(body=body, type_ignores=[])):
        name = call_name(call)
        if name in ("_require", "_session_and_permission") and call.args:
            for value in resolve_strings(call.args[0], consts):
                if "." in value and value not in facts["abilities"]:
                    facts["abilities"].append(value)
        if name == "_login_required":
            facts["login"] = True
        if name == "_csrf_guard":
            facts["csrf"] = True
        if name in ("_ingest_secret", "_bearer_token"):
            facts["machine_credential"] = True
        if name == "_serve_stream":
            facts["sse"] = True
            facts["handler"] = facts["handler"] or "stream (SSE)"
        if name == "serve_static":
            facts["handler"] = facts["handler"] or "web static files"
        if name == "_method_blocked":
            facts["codes"].append("METHOD_NOT_ALLOWED")
        code = keyword_code(call)
        if name in ("_bad", "send_error_body", "raise") and code:
            if code not in facts["codes"]:
                facts["codes"].append(code)
            if code == "RATE_LIMITED":
                facts["rate_limited"] = True
        for node in ast.walk(call):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                if isinstance(node.value.value, ast.Name) and node.value.value.id == "self" \
                        and node.value.attr == "service" and node.attr != "flush_auth_events":
                    facts["handler"] = facts["handler"] or f"service.{node.attr}"
    return facts


def routes_from_server(tree: ast.Module, consts: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                                          dict[str, list[str]],
                                                                          dict[str, Any]]:
    """遍历 do_*，产出路由清单 + 各 verb 的显式 405 清单 + 兜底说明。"""
    routes: list[dict[str, Any]] = []
    method_not_allowed: dict[str, list[str]] = {}
    fallback: dict[str, Any] = {}
    public = {normalize(p) for p in consts.get("API_PATHS_PUBLIC", ()) if isinstance(p, str)}

    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("do_"):
                continue
            verb = fn.name[3:].upper()
            blocked = [c for c in ast.walk(fn)
                       if isinstance(c, ast.Call) and call_name(c) == "_method_blocked"]
            if blocked and not any(isinstance(n, ast.Compare) for n in ast.walk(fn)):
                fallback[verb] = "405 METHOD_NOT_ALLOWED（`_method_blocked()`，整 verb 关闭）"
                continue
            if fn.name == "do_HEAD":
                fallback["HEAD"] = "委托 do_GET（同一份静态/JSON 响应，无 body）"
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                skeletons = branch_route(node.test, consts)
                if not skeletons:
                    continue
                facts = branch_facts(node.body, consts)
                listed = sorted({s["pattern"] for s in skeletons})
                served = any(call_name(c) == "send_json"
                             for c in calls_in(ast.Module(body=node.body, type_ignores=[])))
                if "METHOD_NOT_ALLOWED" in facts["codes"] and not served:
                    # 这条分支只声明"此路径 + 此方法 = 405"，本身不是可调用的路由
                    if listed:
                        method_not_allowed.setdefault(verb, []).extend(listed)
                    continue
                for skeleton in skeletons:
                    pattern = skeleton["pattern"]
                    auth = "public"
                    if skeleton.get("helper") and verb not in ROUTE_HELPER_VERBS[skeleton["helper"]]:
                        continue
                    if facts["abilities"]:
                        auth = "permission"
                    elif facts["login"]:
                        auth = "login"
                    elif facts["machine_credential"]:
                        auth = "machine_token"
                    elif pattern in public:
                        auth = "public"
                    route = {
                        "path": pattern,
                        "verb": verb,
                        "match": skeleton["match"],
                        "auth": auth,
                        "abilities": sorted(facts["abilities"]),
                        "csrf": facts["csrf"],
                        "login_required": facts["login"],
                        "machine_credential": facts["machine_credential"],
                        "rate_limited": facts["rate_limited"],
                        "sse": facts["sse"],
                        "handler": facts["handler"],
                        "codes": sorted(facts["codes"]),
                        "line": node.lineno,
                    }
                    if skeleton["trailing_slash"]:
                        route["accepts_trailing_slash"] = True
                    routes.append(route)
                if "METHOD_NOT_ALLOWED" in facts["codes"] and served:
                    # 同一条分支既服务又对某个子动作说 405（如 tokens/<id>/<verb>）
                    if listed:
                        method_not_allowed.setdefault(verb, []).extend(listed)
                for code in facts["codes"]:
                    if code == "ROUTE_NOT_FOUND":
                        fallback["not_found"] = "404 ROUTE_NOT_FOUND（所有 do_* 末尾兜底）"
    routes = merge_routes(routes)
    routes.sort(key=lambda r: (r["path"], r["verb"]))
    for verb in method_not_allowed:
        method_not_allowed[verb] = sorted(set(method_not_allowed[verb]))
    return routes, method_not_allowed, fallback


def merge_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 (path, verb, match) 的多条分支合并成一条：能力/码取并集，标志取或。

    典型例子是 `path == API_PATH_RECORDS or path == API_PATH_RECORDS + "/"`：
    两条模式规范化后相同，只应留一条路由，末尾斜杠记成 `accepts_trailing_slash`。
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for route in routes:
        key = (route["path"], route["verb"], route["match"])
        entry = merged.get(key)
        if entry is None:
            merged[key] = json.loads(json.dumps(route))
            continue
        entry["abilities"] = sorted(set(entry["abilities"]) | set(route["abilities"]))
        entry["codes"] = sorted(set(entry["codes"]) | set(route["codes"]))
        for flag in ("csrf", "login_required", "machine_credential", "rate_limited", "sse",
                     "accepts_trailing_slash"):
            if route.get(flag):
                entry[flag] = True
        if not entry.get("handler") and route.get("handler"):
            entry["handler"] = route["handler"]
        entry["line"] = min(entry["line"], route["line"])
    return list(merged.values())


# --------------------------------------------------------------------------- #
# 其它来源
# --------------------------------------------------------------------------- #
def role_permissions() -> dict[str, list[str]]:
    tree = parse(AUTH)
    consts = module_constants(tree)
    matrix = consts.get("ROLE_PERMISSIONS")
    if not isinstance(matrix, dict):
        raise SystemExit("auth.py 里没有 ROLE_PERMISSIONS 字面量，契约无法生成")
    out: dict[str, list[str]] = {}
    for role, abilities in matrix.items():
        if isinstance(abilities, (list, tuple, set, frozenset)):
            out[str(role)] = sorted(str(a) for a in abilities)
    return dict(sorted(out.items()))


def frontend_endpoints() -> dict[str, Any]:
    """`web/app.js` 的 `API_ENDPOINTS` 白名单（界面唯一的路径来源）。

    app.js 是 JS，不能用 Python 的 ast 解析：这里做花括号配对取出那段对象字面量，
    再用正则取 `name: 'relative/path'`。表里少一条 = 界面调不到；
    多一条 = 界面在调用一个后端没有的路由。两种情况都由 `unknown_paths` 报出来。
    """
    text = APP_JS.read_text(encoding="utf-8")
    marker = "var API_ENDPOINTS = {"
    if marker not in text:
        raise SystemExit("web/app.js 里找不到 API_ENDPOINTS，契约无法生成")
    start = text.index(marker) + len(marker)
    depth, index = 1, start
    while index < len(text) and depth > 0:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    block = text[start:index - 1]
    pairs = re.findall(r"([A-Za-z][A-Za-z0-9_]*)\s*:\s*'([^']+)'", block)
    return {
        "source": "agent_explainer/web/app.js: var API_ENDPOINTS",
        "count": len(pairs),
        "endpoints": {name: "/" + value.lstrip("/") for name, value in sorted(pairs)},
    }


def error_code_inventory() -> dict[str, Any]:
    codes = i18n_lint.collect_error_codes()
    zh, _ = i18n_lint.flatten(i18n_lint.load_pairs(i18n_lint.ZH_PATH)[0]), None
    keys = set(zh)
    with_key, missing = [], []
    for code in sorted(codes):
        if any(f"{domain}.error.{code.lower()}" in keys for domain in i18n_lint.ERROR_DOMAINS):
            with_key.append(code)
        else:
            missing.append(code)
    by_module: dict[str, list[str]] = {}
    for code, modules in sorted(codes.items()):
        for module in sorted(modules):
            by_module.setdefault(module, []).append(code)
    return {
        "method": "tools/i18n_lint.py:collect_error_codes()（AST，只取携带 code 的位置）"
                  " + `<域>.error.<code 小写>` 查键",
        "domains": sorted(i18n_lint.ERROR_DOMAINS),
        "excluded_non_codes": {k: v for k, v in sorted(i18n_lint.CODE_EXCLUDE.items())},
        "total": len(codes),
        "with_key": len(with_key),
        "missing_count": len(missing),
        "missing": missing,
        "by_module": {k: sorted(v) for k, v in sorted(by_module.items())},
        "client_maps": {name: len(pairs)
                        for name, pairs in sorted(i18n_lint._client_error_maps().items())},
        "fallback_rule": "没有 code 键的码必须用响应里的 `message` 回退显示（服务端这些码的 "
                         "message 是人话），界面不得白屏（docs/03 §6.5 QA 结论）。",
    }


# --------------------------------------------------------------------------- #
# docs/03 §6.5 交叉核对
# --------------------------------------------------------------------------- #
def section_6_5(text: str) -> str:
    start = text.index("### 6.5")
    rest = text[start + len("### 6.5"):]
    end = rest.find("\n### ")
    return rest if end < 0 else rest[:end]


def doc_rows(text: str) -> list[dict[str, Any]]:
    """§6.5 的"逐条核对结论"表：每行 = {paths, verbs, verdict}。"""
    section = section_6_5(text)
    rows: list[dict[str, Any]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("代码路径", "---") or set(cells[0]) <= {"-"}:
            continue
        # 只读"代码路径"列：文档自己规定这一列才是 server.py 的常量值。
        # 其余列里的反引号是历史写法（旧名 `/api/actions*`），不能当现存路由。
        paths = [normalize(p) for p in re.findall(r"`(/[^`]+)`", cells[0])
                 if "*" not in p
                 and (p.startswith("/api/") or p in ("/health", "/healthz", "/intervene", "/api"))]
        verbs = sorted(set(re.findall(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
                                      cells[1])))
        rows.append({"paths": paths, "verbs": verbs, "verdict": cells[-1],
                     "label": cells[0][:60]})
    return rows


def matches(pattern: str, path: str) -> bool:
    """`/api/users/{id}/{verb}` 匹配 `/api/users/u-1/role`。"""
    a, b = pattern.split("/"), path.split("/")
    if len(a) != len(b):
        return False
    return all(x == y or (x.startswith("{") and x.endswith("}")) for x, y in zip(a, b))


def endpoint_is_served(endpoint: str, pattern: str) -> bool:
    """界面白名单里的路径是否真有后端路由接住。

    `API_ENDPOINTS` 存的是**族根前缀**（`/api/analytics`、`/api/evidence`），后端存的
    是具体路由（`/api/analytics/dashboard`）。`matches()` 要求段数相等，于是这些前缀
    一直被报成「界面调用了后端没有的路由」—— 那是抽取口径的错，不是界面的错
    （界面用前缀拼子路径，`extRequest` 的路径只能由这张表派生）。

    判定：段数相等按 `matches()`；否则只要具体路由落在这个前缀之下，就说明该前缀
    确实指向一组真路由。
    """
    if matches(pattern, endpoint):
        return True
    base = endpoint.rstrip("/")
    return pattern == base or pattern.startswith(base + "/")


def crosscheck_doc(doc_text: str, routes: list[dict[str, Any]]) -> dict[str, Any]:
    rows = doc_rows(doc_text)
    doc_paths = sorted({p for row in rows for p in row["paths"]})
    contract_paths = sorted({r["path"] for r in routes if r["path"].startswith("/")})
    verbs_by_path: dict[str, set[str]] = {}
    for route in routes:
        if route["path"].startswith("/"):
            verbs_by_path.setdefault(route["path"], set()).add(route["verb"])
    doc_only = [p for p in doc_paths
                if not any(matches(pattern, p) for pattern in contract_paths)]
    contract_only = [p for p in contract_paths
                     if not any(matches(p, docp) for docp in doc_paths)]
    verb_drift: list[str] = []
    for row in rows:
        doc_verbs = set(row["verbs"])
        for path in row["paths"]:
            for pattern, verbs in verbs_by_path.items():
                if matches(pattern, path):
                    extra = sorted(verbs - doc_verbs)
                    if extra:
                        verb_drift.append(f"{row['label']}: 代码还有 {extra}，文档方法列是 "
                                          f"{'/'.join(sorted(doc_verbs)) or '—'}")
    warn_rows = [row["label"] for row in rows
                 if not row["verdict"].startswith("✅")]
    return {
        "section": "docs/03-architecture.md §6.5 路由实况核对表",
        "doc_rows": len(rows),
        "doc_paths": doc_paths,
        "contract_paths": contract_paths,
        "doc_only_paths": doc_only,
        "contract_only_paths": contract_only,
        "verb_drift": sorted(set(verb_drift)),
        "rows_not_aligned": warn_rows,
        "in_sync": not (doc_only or contract_only or verb_drift or warn_rows),
    }


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def mounted_constants(path_constants: dict[str, Any],
                      contract_paths: list[str]) -> dict[str, Any]:
    """声明 vs 挂载：每个 API_PATH 常量都必须真的接进分发。

    挂载判定：常量值被某条路由**精确**命中，或是某条资源族路由的前缀
    （docs/07 的 T5"只做前缀"这类记法算合法挂载）。两条都不满足 = 声明了
    却没有任何分支接住——这就是"声明 ≠ 挂载"（known red 的根因），
    写进 `declared_unmounted`，`--check` 对它硬门禁。
    """
    unmounted: list[str] = []
    for name, value in path_constants.items():
        if not isinstance(value, str):
            continue
        normalized = normalize(value)
        if not any(p == normalized or p.startswith(normalized + "/")
                   for p in contract_paths):
            unmounted.append(f"{name}={value}")
    return {
        "mounted": len(path_constants) - len(unmounted),
        "declared": len(path_constants),
        "unmounted": sorted(unmounted),
    }


def build_contract() -> dict[str, Any]:
    tree = parse(SERVER)
    consts = module_constants(tree)
    routes, method_not_allowed, fallback = routes_from_server(tree, consts)
    doc_text = DOC.read_text(encoding="utf-8")
    path_constants = {name: value for name, value in sorted(consts.items())
                      if name.startswith("API_PATH")}
    public_paths = [normalize(p) for p in consts.get("API_PATHS_PUBLIC", ()) if isinstance(p, str)]
    matrix = role_permissions()
    abilities: dict[str, dict[str, Any]] = {}
    for route in routes:
        for ability in route["abilities"]:
            entry = abilities.setdefault(ability, {"routes": [], "roles": []})
            label = f'{route["verb"]} {route["path"]}'
            if label not in entry["routes"]:
                entry["routes"].append(label)
    for ability, entry in abilities.items():
        entry["routes"] = sorted(set(entry["routes"]))
        entry["roles"] = sorted(role for role, perms in matrix.items() if ability in perms)
    endpoints = frontend_endpoints()
    contract_paths = sorted({r["path"] for r in routes if r["path"].startswith("/")})
    endpoints["unknown_paths"] = sorted(
        path for path in endpoints["endpoints"].values()
        if not any(endpoint_is_served(path, pattern) for pattern in contract_paths))
    declared = mounted_constants(path_constants, contract_paths)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "tools/gen_api_contract.py",
        "readme": "机器可读接口契约：唯一来源是代码（server.py 分发分支 + auth.py 权限矩阵 + "
                  "i18n_lint 的错误码口径），docs/03 §6.5 是它的人类可读核对表。"
                  "改代码后运行 `python tools/gen_api_contract.py --write`，"
                  "CI/发布前运行 `--check`（内容漂移或与文档不一致都会非零退出）。"
                  "`declared_unmounted` 是「声明了常量但没有任何分发分支接住」的路径："
                  "启动自检与发版闸门都对它硬门禁，不许「声明 ≠ 挂载」再长出来。",
        "sources": {str(p.relative_to(REPO)).replace("\\", "/"): sha256_of(p)
                    for p in (SERVER, AUTH, APP_JS, DOC,
                              REPO / "tools" / "gen_api_contract.py",
                              REPO / "tools" / "i18n_lint.py")},
        "path_constants": path_constants,
        "declared_vs_mounted": declared,
        "public_paths": sorted(set(public_paths)),
        "routes": routes,
        "verbs_by_path": {path: sorted({r["verb"] for r in routes if r["path"] == path})
                          for path in sorted({r["path"] for r in routes})},
        "method_not_allowed": {verb: paths for verb, paths in
                               sorted(method_not_allowed.items())},
        "method_fallbacks": {k: v for k, v in sorted(fallback.items())},
        "role_permissions": matrix,
        "matrix_abilities": sorted({ability for perms in matrix.values() for ability in perms}),
        "matrix_abilities_without_routes": sorted(
            {ability for perms in matrix.values() for ability in perms} - set(abilities)),
        "abilities": {k: v for k, v in sorted(abilities.items())},
        "local_capabilities": [c for c in consts.get("LOCAL_CAPABILITIES", ())],
        "limits": {name: consts[name] for name in ("MAX_BODY_BYTES", "AUDIT_QUERY_LIMIT_DEFAULT")
                   if name in consts},
        "error_codes": error_code_inventory(),
        "frontend_endpoints": endpoints,
        "doc_crosscheck": crosscheck_doc(doc_text, routes),
    }


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def unified(text: str, other: str) -> list[str]:
    import difflib
    return [line for line in difflib.unified_diff(
        text.splitlines(), other.splitlines(),
        fromfile="docs/api_contract.json (磁盘)", tofile="(本次生成)", lineterm="")][:60]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="写入 docs/api_contract.json")
    parser.add_argument("--check", action="store_true", help="比对磁盘与 docs/03 §6.5（默认动作）")
    parser.add_argument("--json", metavar="PATH", help="把契约另存到 PATH")
    args = parser.parse_args(argv)

    contract = build_contract()
    text = render(contract)
    lines: list[str] = []
    problems: list[str] = []

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        io.open(args.json, "w", encoding="utf-8").write(text)
        lines.append(f"[INFO] 契约已另存 {args.json}（{len(text)} 字节）")

    if args.write:
        CONTRACT.parent.mkdir(parents=True, exist_ok=True)
        io.open(CONTRACT, "w", encoding="utf-8").write(text)
        lines.append(f"{OK} 已写入 docs/api_contract.json（{len(text)} 字节，"
                     f"{len(contract['routes'])} 条路由）")

    cross = contract["doc_crosscheck"]
    lines.append(f"{OK} 路由抽取 —— {len(contract['routes'])} 条 (path, verb)，"
                 f"{len(contract['path_constants'])} 个 API_PATH 常量，"
                 f"公开路径 {len(contract['public_paths'])} 条，"
                 f"显式 405 组 {len(contract['method_not_allowed'])} 组")
    declared = contract["declared_vs_mounted"]
    if declared["unmounted"]:
        problems.append(f"声明了常量但没有任何分发分支接住的路径：{declared['unmounted']}")
        lines.append(f"{NG} 声明 vs 挂载 —— {declared['mounted']}/{declared['declared']} 个常量挂载，"
                     f"未挂载：{declared['unmounted']}")
    else:
        lines.append(f"{OK} 声明 vs 挂载 —— {declared['declared']} 个 API_PATH 常量"
                     f"全部接进分发（声明即挂载）")
    lines.append(f"{OK} 权限矩阵 —— {len(contract['role_permissions'])} 个角色 / "
                 f"{len(contract['matrix_abilities'])} 个能力（路由用到 "
                 f"{len(contract['abilities'])} 个）：" +
                 ", ".join(f"{role}={len(perms)}"
                           for role, perms in contract["role_permissions"].items()) +
                 f"；矩阵里有但没有专属路由的能力："
                 f"{', '.join(contract['matrix_abilities_without_routes']) or '无'}")
    codes = contract["error_codes"]
    lines.append(f"{OK} 错误码 —— 共 {codes['total']} 个，有 `<域>.error.<code>` 键 "
                 f"{codes['with_key']} 个，无键 {codes['missing_count']} 个（走 message 回退）")
    front = contract["frontend_endpoints"]
    if front["unknown_paths"]:
        problems.append(f"app.js 调用了契约里不存在的路由：{front['unknown_paths']}")
        lines.append(f"{NG} 前端调用面 —— 界面调用后端没有的路由：{front['unknown_paths']}")
    else:
        lines.append(f"{OK} 前端调用面 —— app.js API_ENDPOINTS {front['count']} 条，"
                     f"全部命中契约路由")

    if not args.write:
        if not CONTRACT.exists():
            problems.append("docs/api_contract.json 不存在（先运行 --write）")
            lines.append(f"{NG} 契约文件缺失 —— docs/api_contract.json 不存在")
        else:
            disk = io.open(CONTRACT, encoding="utf-8").read()
            if disk == text:
                lines.append(f"{OK} 可重复生成 —— 磁盘内容与本次生成字节一致（{len(text)} 字节）")
            else:
                problems.append("docs/api_contract.json 与本次生成不一致（代码改了但没 --write）")
                lines.append(f"{NG} 契约漂移 —— 磁盘与本次生成不同，前 12 行 diff：")
                lines.extend("      " + d for d in unified(disk, text))

    if cross["in_sync"]:
        lines.append(f"{OK} docs/03 §6.5 逐条比对 —— 文档 {cross['doc_rows']} 行 / "
                     f"{len(cross['doc_paths'])} 条路径 与契约 {len(cross['contract_paths'])} 条路径"
                     f" 一一对应，方法列一致，无 ⚠ 行")
    else:
        problems.append("docs/03 §6.5 与契约不一致")
        lines.append(f"{NG} docs/03 §6.5 比对 —— 有差异：")
        for label, items in (("文档有、代码没有", cross["doc_only_paths"]),
                             ("代码有、文档没写", cross["contract_only_paths"]),
                             ("方法列与代码不符", cross["verb_drift"]),
                             ("结论列不是 ✅ 的行", cross["rows_not_aligned"])):
            for item in items:
                lines.append(f"      - {label}：{item}")

    for line in lines:
        print(line)
    if problems:
        print(f"[FAIL] 契约检查未通过：{len(problems)} 项（详见上方逐条）")
        return 1
    print(f"[PASS] 契约检查通过 —— 路由/权限/错误码/文档四者同源"
          f"（{len(contract['routes'])} 条路由，{codes['total']} 个错误码）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
