"""facts_hash —— 计算 ActionRecord 的『事实指纹』（纯标准库）。

护栏 P3 的机器化：human_readable 只是结构化事实的函数。
把事实字段做规范化 JSON（键排序、去空白、保留中文）后取 sha256。
- 模板翻译：事实不变 → 指纹不变（可重放、可缓存）。
- LLM 润色：文案变、事实不变 → 指纹不变；一旦润色新增了事实 → 指纹变 → 校验失败。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# 参与指纹的事实字段（顺序无关，由 sort_keys 保证）
FACT_FIELDS: tuple[str, ...] = (
    "action_type",
    "confidence",
    "cost",
    "error",
    "impact_scope",
    "intent",
    "latency_ms",
    "rationale",
    "result_summary",
    "status",
)

ALGORITHM = "sha256"


def canonical_json(obj: Any) -> str:
    """规范化 JSON：排序键、紧凑分隔符、保留非 ASCII（中文）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def facts_of(record: dict[str, Any]) -> dict[str, Any]:
    """取出事实子集；缺字段以 None 表示（显式参与指纹，避免缺省漂移）。"""
    return {key: record.get(key) for key in FACT_FIELDS}


def compute_facts_hash(record: dict[str, Any]) -> str:
    payload = canonical_json({"schema_version": record.get("schema_version"),
                              "facts": facts_of(record)}).encode("utf-8")
    return f"{ALGORITHM}:" + hashlib.sha256(payload).hexdigest()


def verify(record: dict[str, Any]) -> tuple[bool, str, str]:
    """返回 (是否一致, 期望值, 实际值)。"""
    declared = (record.get("human_readable") or {}).get("source_facts_hash")
    computed = compute_facts_hash(record)
    return declared == computed, str(declared), computed


if __name__ == "__main__":  # pragma: no cover
    import sys

    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    records = doc if isinstance(doc, list) else doc.get("records", [])
    for rec in records:
        ok, declared, computed = verify(rec)
        print(f"{rec.get('id', '?'):24s} {'OK' if ok else 'MISMATCH'} {computed}")
