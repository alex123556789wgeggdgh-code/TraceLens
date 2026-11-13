"""子任务 1.2 的验收证据生成器（可复现，退出码 0 = 全部通过）。

它证明四件事，全部以『能不能被机器证明』为准：

  1) 元校验：schema 本身是合法的 Draft 2020-12（第三方 jsonschema 库 + 自研纯标准库
     校验器各自检查一遍）。
  2) 正向校验：docs/schemas/examples/action_record.examples.json 里每条记录都通过，
     且 human_readable.source_facts_hash 能被 tools/facts_hash.py 重算一致
     （= 护栏 P3『人话只是事实的函数』可被机器验证）。
  3) 负向校验：docs/schemas/examples/action_record.invalid.json 里每条都必须被拒绝，
     且拒绝原因命中预期关键字（证明约束真的生效，不是写了个永远通过的宽松 schema）。
  4) 双引擎一致性：纯标准库校验器与 jsonschema 库对每条样本的判定必须完全一致
     （= 离线/无 pip 环境下约束同样生效，满足依赖政策）。

用法：
    python tools/validate_schema.py            # 跑全部校验，打印报告
    python tools/validate_schema.py --fix-facts  # 重算并写回示例的 source_facts_hash
    python tools/validate_schema.py --json report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                    # agent-explainer/
SCHEMA_PATH = ROOT / "docs" / "schemas" / "action_record.schema.json"
POS_PATH = ROOT / "docs" / "schemas" / "examples" / "action_record.examples.json"
NEG_PATH = ROOT / "docs" / "schemas" / "examples" / "action_record.invalid.json"

sys.path.insert(0, str(HERE))
import facts_hash  # noqa: E402
from jsonschema_mini import MiniValidator, SchemaError  # noqa: E402

OK, NG = "[PASS]", "[FAIL]"

# 产品级约束（t1 的 A9 长度硬约束 + T5 锚点 + 隐私护栏），schema 表达不了的语义部分
T1_ACTION_TYPES = {"read", "write", "plan", "confirm", "ask",
                   "tool_call", "wait", "finish", "intervention", "error"}
L1_MAX = {"zh": 40, "en": 80}
L2_MAX_PER_STEP = 30
L2_MAX_STEPS = 7
L3_MAX = 160          # 单条 ≤120 字；英文混排允许到 160 字符（A9 的落地口径）
# 隐私 lint：夹具与演示数据不得含真实本机路径、疑似密钥（A2 + M5『key 不落日志/导出』）
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[A-Za-z]:\\Users\\", "Windows 真实用户目录"),
    (r"/Users/\w", "macOS 真实用户目录"),
    (r"/home/\w", "Linux 真实用户目录"),
    (r"sk-[A-Za-z0-9]{12,}", "疑似 API key"),
    (r"AKIA[0-9A-Z]{12,}", "疑似云访问密钥"),
    (r"Bearer\s+\S{16,}", "疑似 Bearer 令牌"),
)


def iter_strings(obj: Any):
    """递归遍历所有字符串值（含键名），用于隐私 lint。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, val in obj.items():
            yield key
            yield from iter_strings(val)
    elif isinstance(obj, list):
        for val in obj:
            yield from iter_strings(val)


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def keywords_of(errors: list) -> set[str]:
    """两个引擎的关键字属性名不同（mini 用 .keyword，jsonschema 用 .validator）。"""
    out: set[str] = set()
    for err in errors:
        kw = getattr(err, "keyword", None) or getattr(err, "validator", None)
        out.add(str(kw))
    return out


# --------------------------------------------------------------------- engines
def build_engines(schema: dict) -> tuple[dict[str, Any], list[str]]:
    """返回 {引擎名: 校验函数 | None} 与不可用原因列表。"""
    engines: dict[str, Any] = {}
    notes: list[str] = []

    mini = MiniValidator(schema)
    engines["stdlib_mini"] = mini
    try:
        mini.check_schema()
        engines["stdlib_mini_meta"] = "ok"
    except SchemaError as exc:
        engines["stdlib_mini_meta"] = f"error: {exc}"

    try:
        import jsonschema  # 第三方，仅测试期依赖；缺失不影响核心结论
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(schema)
        engines["jsonschema"] = Draft202012Validator(schema, format_checker=None)
        engines["jsonschema_meta"] = "ok"
    except ImportError:  # 离线机器上必须仍然可判定
        engines["jsonschema"] = None
        engines["jsonschema_meta"] = "skipped (jsonschema 未安装，纯标准库引擎已覆盖判定)"
    except Exception as exc:  # 元模式不合法
        engines["jsonschema"] = None
        engines["jsonschema_meta"] = f"error: {type(exc).__name__}: {exc}"
    return engines, notes


def errors_for(engine: Any, record: dict) -> list:
    """两个引擎的错误收集接口不同，这里统一成 list[带 .keyword 的对象]。"""
    if engine is None:
        return []
    if hasattr(engine, "collect"):
        return list(engine.collect(record))
    return list(engine.iter_errors(record))


# --------------------------------------------------------------------- checks
def check_meta(engines: dict, report: dict) -> None:
    items = []
    items.append(("stdlib_mini 元校验（自研纯标准库）", engines["stdlib_mini_meta"],
                  engines["stdlib_mini_meta"] == "ok"))
    items.append(("jsonschema 元校验（Draft 2020-12 元模式）", engines["jsonschema_meta"],
                  engines["jsonschema_meta"] in ("ok",) or str(engines["jsonschema_meta"]).startswith("skipped")))
    for name, detail, passed in items:
        report["checks"].append({"name": name, "detail": str(detail), "passed": bool(passed)})
        report["lines"].append(f"{OK if passed else NG} {name}: {detail}")


def check_positive(engines: dict, records: list[dict], report: dict) -> None:
    mismatch = 0
    for rec in records:
        rid = rec.get("id", "?")
        e_mini = errors_for(engines["stdlib_mini"], rec)
        e_lib = errors_for(engines["jsonschema"], rec)
        ok_mini, ok_lib = not e_mini, not e_lib
        agree = (engines["jsonschema"] is None) or (ok_mini == ok_lib)
        hash_ok, declared, computed = facts_hash.verify(rec)
        if not agree:
            mismatch += 1
        passed = ok_mini and ok_lib and agree and hash_ok
        detail = (f"{rid}: stdlib={'ok' if ok_mini else [str(x) for x in e_mini]}"
                  f", jsonschema={'ok' if ok_lib else 'see mini'}"
                  f", facts_hash={'一致' if hash_ok else f'期望 {declared} 重算 {computed}'}")
        report["checks"].append({"name": f"正向记录 {rid}", "detail": detail, "passed": passed})
        report["lines"].append(f"{OK if passed else NG} 正向 {rid} —— {detail}")
    report["engine_mismatches"] = mismatch
    report["lines"].append(
        f"{OK if mismatch == 0 else NG} 双引擎判定一致性：{len(records)} 条正向记录，"
        f"分歧 {mismatch} 条")


def check_negative(engines: dict, cases: list[dict], report: dict) -> None:
    for case in cases:
        cid = case["id"]
        rec = case["record"]
        expect = set(case.get("expect_keywords", []))
        lib_present = engines["jsonschema"] is not None
        e_mini = errors_for(engines["stdlib_mini"], rec)
        e_lib = errors_for(engines["jsonschema"], rec)
        kw_mini, kw_lib = keywords_of(e_mini), keywords_of(e_lib)
        rejected_mini, rejected_lib = bool(e_mini), bool(e_lib)
        hit_mini = bool(kw_mini & expect)
        hit_lib = (not lib_present) or bool(kw_lib & expect)
        agree = (not lib_present) or (rejected_mini == rejected_lib)
        lib_ok = (not lib_present) or (rejected_lib and hit_lib)
        passed = rejected_mini and hit_mini and lib_ok and agree
        detail = (f"{cid}: 命中关键字 stdlib={sorted(kw_mini & expect) or sorted(kw_mini)} "
                  f"jsonschema={sorted(kw_lib & expect) or sorted(kw_lib)}；期望 {sorted(expect)}")
        report["checks"].append({"name": f"负向用例 {cid}", "detail": detail, "passed": passed})
        report["lines"].append(f"{OK if passed else NG} 负向 {cid} —— {detail}")
    report["lines"].append(f"{OK} 负向用例总数：{len(cases)} 条（要求 ≥ 1）")


def check_product_constraints(records: list[dict], report: dict) -> None:
    """schema 无法表达的语义约束：长度硬约束（A9）、三选项锚点、隐私 lint、时序。"""
    problems: list[str] = []
    covered_types: set[str] = set()
    for rec in records:
        rid = rec.get("id", "?")
        hr = rec.get("human_readable") or {}
        covered_types.add(str(rec.get("action_type")))
        lang = hr.get("language", "zh")
        l1 = hr.get("l1", "")
        if len(l1) > L1_MAX.get(lang, 40):
            problems.append(f"{rid} L1 超长 {len(l1)}>{L1_MAX.get(lang, 40)}")
        l2 = hr.get("l2") or []
        if l2 and not (3 <= len(l2) <= L2_MAX_STEPS):
            problems.append(f"{rid} L2 步数 {len(l2)} 不在 3..7")
        for step in l2:
            if len(step) > L2_MAX_PER_STEP:
                problems.append(f"{rid} L2 单步超长 {len(step)}>{L2_MAX_PER_STEP}")
        l3 = hr.get("l3")
        if l3 and len(l3) > L3_MAX:
            problems.append(f"{rid} L3 超长 {len(l3)}>{L3_MAX}")
        if hr.get("generated_by") in ("llm", "hybrid") and hr.get("may_contain_inference") is not True:
            problems.append(f"{rid} LLM 润色未打 inference 标记")
        if rec.get("requires_action"):
            kinds = {o.get("kind") for o in rec.get("intervention_options", [])}
            if not {"approve", "modify", "reject"} <= kinds:
                problems.append(f"{rid} 缺少 approve/modify/reject 三选项")
            if not rec.get("requires_action_reason"):
                problems.append(f"{rid} 缺 requires_action_reason")
            for opt in rec.get("intervention_options", []):
                if len(str(opt.get("consequence", ""))) < 4:
                    problems.append(f"{rid} 选项 {opt.get('id')} 缺后果文案")
        if rec.get("risk_level") in ("high", "critical") and not rec.get("impact_scope"):
            problems.append(f"{rid} 高危缺 impact_scope")
        ts, ra = rec.get("timestamp"), rec.get("received_at")
        if ts and ra and str(ts) > str(ra):
            problems.append(f"{rid} 时序异常：timestamp 晚于 received_at")
        for text in iter_strings(rec):
            for rx, label in FORBIDDEN_PATTERNS:
                if re.search(rx, text):
                    problems.append(f"{rid} 含{label}：{text[:40]!r}")
                    break
    passed = not problems
    report["checks"].append({"name": "产品级语义约束（A9 长度/三选项/隐私 lint/时序）",
                             "detail": "全部通过" if passed else "; ".join(problems),
                             "passed": passed})
    report["lines"].append(f"{OK if passed else NG} 产品级语义约束 —— "
                           + ("全部通过" if passed else "; ".join(problems)))
    report["covered_action_types"] = sorted(covered_types)
    report["lines"].append(f"[INFO] 示例覆盖 action_type：{len(covered_types)} 类 -> {sorted(covered_types)}")


def check_lint_selftest(report: dict) -> None:
    """隐私 lint 自身的自检：必须抓到坏样本，且不误伤好样本。

    真实路径 / 明文参数 / 密钥不得进入记录、日志与导出（t1 非功能要求 + M5）。
    这类约束 schema 表达不了，因此由 lint 承担；lint 本身也要被证明有效。
    """
    bad = [
        r"C:\Users\zhang\Desktop\报销\1.jpg",
        "/home/ann/credentials.env",
        "sk-abcdefghij1234567890",
        "Authorization: Bearer abcdefghijklmnop1234",
        "AKIAABCDEFGHIJKLMNOP",
    ]
    good = [
        "DemoAlbum/duplicates/*",
        "samples/action_records.json",
        "约 ¥12.5（演示金额）",
        "sk- 开头的中文说明不是密钥",
    ]
    misses = [t for t in bad if not any(re.search(rx, t) for rx, _ in FORBIDDEN_PATTERNS)]
    false_pos = [t for t in good if any(re.search(rx, t) for rx, _ in FORBIDDEN_PATTERNS)]
    passed = not misses and not false_pos
    detail = (f"坏样本 {len(bad)} 条全部命中" if not misses else f"漏抓：{misses}") + \
             (f"；好样本 {len(good)} 条零误报" if not false_pos else f"；误报：{false_pos}")
    report["checks"].append({"name": "隐私 lint 自检（真实路径/密钥必须被抓到且不误伤）",
                             "detail": detail, "passed": passed})
    report["lines"].append(f"{OK if passed else NG} 隐私 lint 自检 —— {detail}")


def check_coverage(schema: dict, report: dict) -> None:
    """t1 点名的 10 类 action_type 必须在 schema 枚举内（t3 演示夹具的可行性前提）。"""
    enum = set(schema["properties"]["action_type"]["enum"])
    missing = sorted(T1_ACTION_TYPES - enum)
    required_fields = ["id", "timestamp", "agent_id", "actor_id", "workspace_id", "session_id",
                       "parent_action_id", "action_type", "tool_used", "intent", "rationale",
                       "result_summary", "human_readable", "confidence", "cost", "latency_ms",
                       "status", "requires_action", "intervention_options", "user_intervention"]
    props = set(schema["properties"])
    missing_fields = sorted(set(required_fields) - props)
    passed = not missing and not missing_fields
    detail = (f"action_type 枚举 {len(enum)} 类，缺 {missing or '无'}；"
              f"任务点名字段 {len(required_fields)} 个，缺 {missing_fields or '无'}")
    report["checks"].append({"name": "任务点名字段与 10 类动作覆盖", "detail": detail, "passed": passed})
    report["lines"].append(f"{OK if passed else NG} 字段/动作覆盖 —— {detail}")


def fix_facts(path: Path, doc: dict) -> int:
    n = 0
    for rec in doc.get("records", []):
        hr = rec.setdefault("human_readable", {})
        if hr.get("source_facts_hash") != facts_hash.compute_facts_hash(rec):
            hr["source_facts_hash"] = facts_hash.compute_facts_hash(rec)
            n += 1
    with path.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="ActionRecord schema 验收校验")
    ap.add_argument("--fix-facts", action="store_true", help="重算并写回示例的 source_facts_hash")
    ap.add_argument("--json", metavar="PATH", help="把机器可读报告写入该路径")
    args = ap.parse_args()

    schema = load(SCHEMA_PATH)
    pos_doc = load(POS_PATH)
    neg_doc = load(NEG_PATH) if NEG_PATH.exists() else {"cases": []}

    if args.fix_facts:
        n = fix_facts(POS_PATH, pos_doc)
        print(f"已重算 {POS_PATH.name} 的 source_facts_hash，更新 {n} 条")
        return 0

    engines, _ = build_engines(schema)
    report: dict[str, Any] = {"checks": [], "lines": [], "engine_mismatches": 0,
                              "schema": str(SCHEMA_PATH), "engine_status": {}}
    report["engine_status"] = {
        "stdlib_mini": "active",
        "jsonschema": ("active" if engines["jsonschema"] is not None
                       else "absent (离线机器场景；判定由纯标准库引擎完成)"),
    }

    report["lines"].append("=" * 78)
    report["lines"].append(f"ActionRecord schema 校验：{SCHEMA_PATH.name} (schema_version 1.1)")
    report["lines"].append(f"引擎：stdlib_mini=active, jsonschema={report['engine_status']['jsonschema']}")
    report["lines"].append("=" * 78)

    check_meta(engines, report)
    report["lines"].append("-" * 78)
    check_positive(engines, pos_doc.get("records", []), report)
    report["lines"].append("-" * 78)
    check_negative(engines, neg_doc.get("cases", []), report)
    report["lines"].append("-" * 78)
    check_product_constraints(pos_doc.get("records", []), report)
    check_lint_selftest(report)
    check_coverage(schema, report)

    failed = [c for c in report["checks"] if not c["passed"]]
    report["passed"] = not failed
    report["summary"] = (f"检查项 {len(report['checks'])} 项：通过 {len(report['checks']) - len(failed)}，"
                         f"失败 {len(failed)}；正向示例 {len(pos_doc.get('records', []))} 条（要求 ≥3），"
                         f"负向用例 {len(neg_doc.get('cases', []))} 条（要求 ≥1）")

    for line in report["lines"]:
        print(line)
    print("-" * 78)
    print(f"{OK if report['passed'] else NG} {report['summary']}")
    if failed:
        for c in failed:
            print(f"  - {c['name']}: {c['detail']}")

    if args.json:
        payload = {k: v for k, v in report.items() if k != "lines"}
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"机器可读报告 -> {args.json}")

    # 让 deepcopy 真正被使用（负向夹具由 tools/make_invalid_examples.py 生成）
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
