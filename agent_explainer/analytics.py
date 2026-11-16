"""分析仪表盘（规格书功能六，docs/07 §4.10 / §5.1-T26 冻结契约）。

为什么聚合只认记录里已经写下来的事实：仪表盘的每个数字都必须能从
``records`` 逐条复算——决策数来自 ``user_intervention``、任务数来自记录本体、
平均批准时长来自 ``decided_at - timestamp``。不引入任何"看起来像"的启发式，
算不出就如实是 0/空，绝不编一个好看的数。

用户脱敏（§3.3）：分析输出里的用户 id 一律 ``sha256(user_id)[:12]``，
``salt`` 由调用方传（HTTP 层可用每实例固定盐），明文 user_id 不出本模块。

错误码是契约：``ANALYTICS_RANGE_INVALID``（区间/粒度/agent 参数不合法）、
``ANALYTICS_FORMAT_INVALID``、``ANALYTICS_EXPORT_FAILED``——HTTP 层照表翻译。

R6-1（REQ-006 B6-2 / D-5 修订口径）：``dashboard(agg=...)`` 走**预聚合优先**——命中区间
从追加式账本 ``agg_daily.jsonl`` 读聚合值（``pre_aggregated=true``）；未预聚合区间、
链被篡改、参数不支持时**流式逐行扫描**原记录（O(1) 内存，逐行消费不整载），并在报告
元数据里如实标注 ``{pre_aggregated, scanned, agg.reason}``——「≤3s」只在预聚合命中时
成立，UI 的「未预聚合，加载较慢」降级标注（R6-4）吃的就是这个数据面。口径定义与
双路径同结果的可复算性见 docs/07 §11.15。

R6-3（REQ-006 B6-3 导出接线）：导出的每个数字都来自上面那一条数据面（预聚合命中或
流式扫描，同一个 ``dashboard`` 调用，不分两套口径），并且**元数据跟着产物走**——
``export_csv`` 末尾的 ``meta`` 段与 ``export_html`` / ``export_pdf`` 的元数据行写的就是
"这份文件怎么来的"（预聚合命中还是流式扫、扫了多少条、回退原因码）。产物离开设服务端
之后，"≤3s 只在预聚合命中时成立"这句话必须还在文件里，否则导出件就在替界面撒谎。
三条硬口径：① **PDF 拿不到就出可打印 HTML**（``reportlab`` 缺失**或构建失败**都走
这条路，与 ``audit.export_pdf`` 既有降级口径逐字一致，不报错、不静默退成 CSV）；
② **实发产物类型如实**（谁出门就写谁：``pdf`` / ``html`` / ``csv``，文件名后缀与
``Content-Type`` 必须对上，"请求 pdf 拿到 CSV"那种文件名是伪造证据）；③ **自由文本
出门前过 PII 防线**（工具名/参数名是从记录里抄来的字符串，统一过 ``audit.redact_text``
单点，与审计导出的「写盘之前先脱敏」同源）。
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import html as _html
import importlib
import io
import sys
import itertools
from typing import Any

__all__ = [
    "ANALYTICS_VERSION", "GRANULARITIES", "anonymise", "trust_health",
    "AnalyticsError", "dashboard", "report_snapshot",
    "export_csv", "export_pdf", "export_html", "export_meta_rows",
]

ANALYTICS_VERSION = "1.0"
GRANULARITIES: tuple[str, ...] = ("day", "week")


class AnalyticsError(Exception):
    """分析域异常：``code`` 对齐 docs/07 §7，HTTP 层照表翻译。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: dict | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.field = field
        self.hint = hint
        self.detail = dict(detail or {})

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.detail:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"field={self.field}")
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return "；".join(parts)


def anonymise(user_id, *, salt: str = "") -> str:
    """``sha256(salt + user_id)[:12]``——分析输出里的用户只用它（§3.3 脱敏）。"""
    return hashlib.sha256(f"{salt}{user_id}".encode("utf-8")).hexdigest()[:12]


def trust_health(intervention_rate) -> float:
    """``1 - rate`` 钳到 0..1：干预率越低，信任健康度越高（规格书功能六）。"""
    try:
        rate = float(intervention_rate)
    except (TypeError, ValueError):
        rate = 0.0
    return round(min(1.0, max(0.0, 1.0 - rate)), 4)


def _decision(record: dict) -> dict:
    view = record.get("user_intervention")
    return view if isinstance(view, dict) else {}


def _ts(record: dict) -> _dt.datetime | None:
    raw = str(record.get("timestamp") or "")
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decided_at(record: dict) -> _dt.datetime | None:
    raw = str(_decision(record).get("decided_at") or "")
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bucket_key(moment: _dt.datetime, granularity: str) -> str:
    if granularity == "week":
        monday = moment - _dt.timedelta(days=moment.weekday())
        return monday.date().isoformat()
    return moment.date().isoformat()


def _range_bounds(range_from, range_to) -> tuple[_dt.date, _dt.date]:
    try:
        start = (_dt.date.fromisoformat(str(range_from)) if range_from
                 else _dt.date(2000, 1, 1))
        end = (_dt.date.fromisoformat(str(range_to)) if range_to
               else _dt.date.max)
    except ValueError as exc:
        raise AnalyticsError("ANALYTICS_RANGE_INVALID",
                             "from/to 必须是 YYYY-MM-DD", field="from/to",
                             detail={"error": str(exc)}) from exc
    if start > end:
        raise AnalyticsError("ANALYTICS_RANGE_INVALID", "from 不能晚于 to",
                             field="from/to", detail={"from": str(range_from),
                                                      "to": str(range_to)})
    return start, end


def _latency_micros(decided: _dt.datetime, moment: _dt.datetime) -> int:
    """批准时长 → **整数微秒**：聚合账本按整数求和，杜绝浮点加法顺序造成的尾差。"""
    return int(round((decided - moment).total_seconds() * 1000000))


def _assemble_metrics(counters: dict) -> dict:
    """计数器 → 指标区（**唯一装配点**：流式扫描与预聚合读侧共用，两条路径同公式）。

    入参计数器：``{tasks, interventions, approved, rejected, micros, n_latency,
    users(set), buckets{bucket:[tasks, iv]}, heat{(tool,param):count},
    rejects{tool:count}}``。尾部键 ``range/granularity/generated_at`` 由 :func:`dashboard` 补。
    """
    tasks = int(counters["tasks"])
    interventions = int(counters["interventions"])
    micros = int(counters["micros"])
    n_latency = int(counters["n_latency"])
    users = sorted(counters["users"])
    buckets = counters["buckets"]
    heat = counters["heat"]
    rejects = counters["rejects"]

    rate = round(interventions / tasks, 4) if tasks else 0.0
    avg_seconds = round(micros / n_latency / 1000000.0, 3) if n_latency else 0.0
    trend = [{"bucket": key,
              "tasks": group[0],
              "interventions": group[1],
              "rate": round(group[1] / group[0], 4) if group[0] else 0.0}
             for key, group in sorted(buckets.items())]
    heatmap = [{"tool": tool, "param": param, "count": count}
               for (tool, param), count in sorted(heat.items(),
                                                  key=lambda kv: (-kv[1], kv[0]))]
    top_parameters = [{"tool": item["tool"], "parameter": item["param"],
                       "count": item["count"]} for item in heatmap[:10]]
    top_rejected = [{"tool": tool, "step": "decision", "count": count}
                    for tool, count in sorted(rejects.items(),
                                              key=lambda kv: (-kv[1], kv[0]))]
    return {
        "total_tasks": tasks,
        "total_interventions": interventions,
        "intervention_rate": rate,
        "avg_approval_seconds": avg_seconds,
        "avg_approval_ms": int(avg_seconds * 1000),
        "trust_health": trust_health(rate),
        "users": {"count": len(users), "ids": users},
        "trend": trend,
        "heatmap": heatmap,
        "top_parameters": top_parameters,
        "top_rejected": top_rejected,
        "approved": int(counters["approved"]),
        "rejected": int(counters["rejected"]),
    }


def _stream_scan(records, *, start: _dt.date, end: _dt.date, agent,
                 granularity: str, salt: str) -> tuple[dict, int]:
    """**流式逐行扫描**（未预聚合区间的回退路径）：单遍 ``for``，只留计数器不留记录，
    返回 ``(指标区, scanned=消费条数)``。口径与 :func:`_dashboard_materialized` 逐项一致：
    算不出就如实 0/空，绝不编好看的数。
    """
    counters = {"tasks": 0, "interventions": 0, "approved": 0, "rejected": 0,
                "micros": 0, "n_latency": 0, "users": set(), "buckets": {},
                "heat": {}, "rejects": {}}
    scanned = 0
    for record in (records or []):
        scanned += 1
        if not isinstance(record, dict):
            continue
        if agent and str(record.get("agent_id") or "") != str(agent):
            continue
        moment = _ts(record)
        if moment is None:
            continue
        day = moment.date()
        if day < start or day > end:
            continue
        counters["tasks"] += 1
        view = _decision(record)
        dec = view.get("decision")
        tool = str(((record.get("tool_used") or {}).get("name")) or "—")
        if dec:
            counters["interventions"] += 1
            if dec in ("approve", "modify"):
                counters["approved"] += 1
            elif dec == "reject":
                counters["rejected"] += 1
                counters["rejects"][tool] = counters["rejects"].get(tool, 0) + 1
            counters["users"].add(anonymise(view.get("decided_by") or "local", salt=salt))
            decided = _decided_at(record)
            if decided and moment and decided >= moment:
                counters["micros"] += _latency_micros(decided, moment)
                counters["n_latency"] += 1
        bucket = _bucket_key(moment, granularity)
        group = counters["buckets"].setdefault(bucket, [0, 0])
        group[0] += 1
        if dec:
            group[1] += 1
        for field in (view.get("modified_fields") or []):
            name = str((field or {}).get("name") or "")
            if name:
                key = (tool, name)
                counters["heat"][key] = counters["heat"].get(key, 0) + 1
    return _assemble_metrics(counters), scanned


def dashboard(records, *, audit_entries=None, range_from=None, range_to=None,
              agent=None, granularity: str = "day", salt: str = "", agg=None) -> dict:
    """从记录事实算仪表盘（docs/07 §5.1-T26 的键位就是契约，一一对齐；R6-1 增尾部元数据）。

    ``records`` 是 ActionRecord 数组（内存/存储里的全量）；区间外的记录不进
    统计但 ``total_tasks`` 仍按区间内计数——口径：**看板只回答"这段时间里
    发生了什么"**，区间外的事实不掺进来。

    ``agg`` 给了预聚合账本路径（``FeaturePaths.agg_ledger``）：预聚合优先——水位线外
    的新记录增量聚合追加、被改写的记录追加**补偿行**，报告从账本合并行读出
    （``pre_aggregated=true``）；账本缺失/链被篡改/``agent`` 过滤/自定义 ``salt``/
    一次性迭代器输入 → 流式逐行扫描回退，``agg.reason`` 如实说明。**两条路径同数据
    同结果**（测试钉死），3s 承诺只在命中时成立。
    """
    if granularity not in GRANULARITIES:
        raise AnalyticsError("ANALYTICS_RANGE_INVALID",
                             f"granularity 只支持 {'/'.join(GRANULARITIES)}",
                             field="granularity", detail={"received": granularity})
    start, end = _range_bounds(range_from, range_to)
    if agg is not None:
        # 函数级 import：analytics_agg 顶层复用本模块的口径单点（_ts/_decision/…），
        # 模块级互 import 会成环；仪表盘默认路径也不为账本 IO 付费。
        from . import analytics_agg

        outcome = analytics_agg.merge_dashboard(
            records, path=agg, start=start, end=end,
            granularity=granularity, agent=agent, salt=salt)
        report = outcome["report"]
        meta = outcome["meta"]
    else:
        report, scanned = _stream_scan(records, start=start, end=end, agent=agent,
                                       granularity=granularity, salt=salt)
        meta = {"pre_aggregated": False, "scanned": scanned,
                "reason": "no_ledger_wired"}
    report["pre_aggregated"] = bool(meta.get("pre_aggregated", False))
    report["scanned"] = int(meta.get("scanned", 0))
    report["agg"] = {key: value for key, value in meta.items()
                     if key not in ("pre_aggregated", "scanned")}
    report["range"] = {"from": start.isoformat() if range_from else None,
                       "to": end.isoformat() if range_to else None}
    report["granularity"] = granularity
    report["generated_at"] = _utc_now()
    return report


def _dashboard_materialized(records, *, audit_entries=None, range_from=None,
                            range_to=None, agent=None, granularity: str = "day",
                            salt: str = "") -> dict:
    """**旧物化算法**（整载 ``rows`` 列表那版）——R6-1 起降级为**复算参照**保留。

    公开路径不再调用它；它存在的唯一理由：给双路径同结果测试当**参照实现**
    （``test_stream_matches_the_materialized_reference``），把「流式扫描逐键等于旧
    算法」钉成可重跑的证据。别删它，删了 B6-1 的抽样核对就少一条基线。
    """
    if granularity not in GRANULARITIES:
        raise AnalyticsError("ANALYTICS_RANGE_INVALID",
                             f"granularity 只支持 {'/'.join(GRANULARITIES)}",
                             field="granularity", detail={"received": granularity})
    start, end = _range_bounds(range_from, range_to)
    rows: list[dict] = []
    for record in (records or []):
        if not isinstance(record, dict):
            continue
        if agent and str(record.get("agent_id") or "") != str(agent):
            continue
        moment = _ts(record)
        if moment is None:
            continue
        day = moment.date()
        if day < start or day > end:
            continue
        rows.append(record)

    decision_rows = [r for r in rows if _decision(r).get("decision")]
    approved_rows = [r for r in decision_rows
                     if _decision(r).get("decision") in ("approve", "modify")]
    rejected_rows = [r for r in decision_rows
                     if _decision(r).get("decision") == "reject"]

    # 平均批准时长：decided_at - timestamp（有 decided_at 才算，不猜）
    latencies: list[float] = []
    for record in decision_rows:
        decided = _decided_at(record)
        moment = _ts(record)
        if decided and moment and decided >= moment:
            latencies.append((decided - moment).total_seconds())
    avg_seconds = round(sum(latencies) / len(latencies), 3) if latencies else 0.0

    # 用户脱敏计数：只数"真的拍过板"的人
    users = sorted({anonymise(_decision(r).get("decided_by") or "local", salt=salt)
                    for r in decision_rows})

    # 趋势：按桶给 intervention_rate（桶内干预数 / 桶内记录数；空桶不放假点）
    buckets: dict[str, list[dict]] = {}
    for record in rows:
        moment = _ts(record)
        buckets.setdefault(_bucket_key(moment, granularity), []).append(record)
    trend = []
    for key in sorted(buckets):
        group = buckets[key]
        decided = sum(1 for r in group if _decision(r).get("decision"))
        trend.append({"bucket": key,
                      "tasks": len(group),
                      "interventions": decided,
                      "rate": round(decided / len(group), 4) if group else 0.0})

    # 热力图 / 常改参数：改了哪些 (tool, param)，次数来自 modified_fields
    heat: dict[tuple[str, str], int] = {}
    for record in rows:
        view = _decision(record)
        tool = str(((record.get("tool_used") or {}).get("name")) or "—")
        for field in (view.get("modified_fields") or []):
            name = str((field or {}).get("name") or "")
            if name:
                heat[(tool, name)] = heat.get((tool, name), 0) + 1
    heatmap = [{"tool": tool, "param": param, "count": count}
               for (tool, param), count in sorted(heat.items(),
                                                  key=lambda kv: (-kv[1], kv[0]))]
    top_parameters = [{"tool": item["tool"], "parameter": item["param"],
                       "count": item["count"]} for item in heatmap[:10]]

    # 被拒清单：按 tool 聚合 reject 决定（谁在拒绝什么）
    rejects: dict[str, int] = {}
    for record in rejected_rows:
        tool = str(((record.get("tool_used") or {}).get("name")) or "—")
        rejects[tool] = rejects.get(tool, 0) + 1
    top_rejected = [{"tool": tool, "step": "decision", "count": count}
                    for tool, count in sorted(rejects.items(),
                                              key=lambda kv: (-kv[1], kv[0]))]

    total_tasks = len(rows)
    total_interventions = len(decision_rows)
    rate = round(total_interventions / total_tasks, 4) if total_tasks else 0.0
    report = {
        "range": {"from": start.isoformat() if range_from else None,
                  "to": end.isoformat() if range_to else None},
        "granularity": granularity,
        "total_tasks": total_tasks,
        "total_interventions": total_interventions,
        "intervention_rate": rate,
        "avg_approval_seconds": avg_seconds,
        "avg_approval_ms": int(avg_seconds * 1000),
        "trust_health": trust_health(rate),
        "users": {"count": len(users), "ids": users},
        "trend": trend,
        "heatmap": heatmap,
        "top_parameters": top_parameters,
        "top_rejected": top_rejected,
        "approved": len(approved_rows),
        "rejected": len(rejected_rows),
        "generated_at": _utc_now(),
    }
    return report


def report_snapshot(report: dict) -> dict:
    """只含指标、不含记录原文的快照（写审计/导出记录时用这一份）。"""
    keep = ("range", "granularity", "total_tasks", "total_interventions",
            "intervention_rate", "avg_approval_seconds", "avg_approval_ms",
            "trust_health", "approved", "rejected", "generated_at")
    snapshot = {key: report.get(key) for key in keep}
    snapshot["trend"] = list(report.get("trend") or [])
    snapshot["users"] = {"count": (report.get("users") or {}).get("count", 0)}
    return snapshot


def export_meta_rows(report: dict) -> list[tuple[str, str]]:
    """导出件里的**如实元数据**（R6-3：``pre_aggregated`` 标注进产物，不只留在 HTTP 面）。

    顺序固定、值全是字符串，三条口径：

    * **来源如实**：``source`` / ``pre_aggregated`` / ``scanned`` 说清这份数字是
      账本合并行读的、还是流式逐行扫的；回退时 ``reason`` 带原因码（``agent_filter``
      / ``custom_salt`` / ``chain_tampered`` / …），命中时 ``newly_aggregated`` /
      ``compensation_keys`` / ``ledger_rows`` / ``head`` 说清账本推进到哪。
    * **用户面只有计数**：``users_count`` 是脱敏后的去重人数；脱敏 id 属于界面
      （``report_snapshot`` 同口径：产物里只留 count，不是一串可关联的指纹清单）。
    * **结构态不打码、自由文本打码**：计数 / 原因码 / ``sha256:`` 链头是结构态，
      原样写；唯一可能夹带自由文本的是 ``problems`` 里的异常原文（绝对路径、邮箱
      都进得来），过 :func:`_redact` 之后再出门。
    """
    agg = report.get("agg") or {}
    pre = bool(report.get("pre_aggregated"))
    rng = report.get("range") or {}
    rows: list[tuple[str, str]] = [
        ("source", "pre_aggregated" if pre else "streaming"),
        ("pre_aggregated", "true" if pre else "false"),
        ("scanned", str(report.get("scanned", 0))),
        ("granularity", str(report.get("granularity") or "")),
        ("range_from", str(rng.get("from") or "")),
        ("range_to", str(rng.get("to") or "")),
        ("generated_at", str(report.get("generated_at") or "")),
        ("analytics_version", ANALYTICS_VERSION),
        ("users_count", str((report.get("users") or {}).get("count", 0))),
    ]
    for key in ("reason", "newly_aggregated", "compensation_keys", "ledger_rows",
                "durable", "head", "problems_count"):
        if key not in agg:
            continue
        value = agg[key]
        rows.append((key, "true" if value is True else "false" if value is False
                     else str(value)))
    problems = agg.get("problems")
    if problems:
        codes = "; ".join(str((item or {}).get("code") or "") for item in problems)
        rows.append(("problems", _redact(codes)))
    return rows


def _redact(value: Any) -> str:
    """导出件里的自由文本过既有 PII 防线（``audit.redact_text`` 单点）。

    为什么函数级 import：仪表盘默认路径不为 PII 正则付费，也不与 ``audit`` 的模块级
    依赖成环。导出是"东西要出门"的动作——审计域那侧的口径就是**写盘之前先脱敏**
    （docs/03 §4.11：``redact_text()`` + ``PII_PATTERNS`` 13 类，落盘文件 /
    ``GET /api/audit`` / CSV·HTML·PDF 三处同源），导出侧同源同函数。
    """
    from . import audit as _audit

    return str(_audit.redact_text("" if value is None else str(value)))


def export_csv(report: dict) -> str:
    """仪表盘 → CSV（BOM 由调用方决定；这里出规范文本，与审计 CSV 同口径）。

    段序固定（R6-3 只在**末尾**加一段，前四段逐字不变）：
    ``metric,value`` → ``bucket,tasks,interventions,rate`` → ``tool,param,count``
    → ``tool,rejects`` → ``meta,key,value``。

    工具名 / 参数名是从记录里抄来的自由文本（trace 适配器什么都能塞进来），
    出门前过 :func:`_redact`；数字一个都不改——导出件与 ``dashboard`` 读数
    逐字段相等是 R6-3 的钉子（两条路径都钉）。
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["metric", "value"])
    for key in ("granularity", "total_tasks", "total_interventions",
                "intervention_rate", "avg_approval_seconds", "avg_approval_ms",
                "trust_health", "approved", "rejected"):
        writer.writerow([key, report.get(key, "")])
    writer.writerow([])
    writer.writerow(["bucket", "tasks", "interventions", "rate"])
    for point in report.get("trend") or []:
        writer.writerow([point.get("bucket"), point.get("tasks"),
                         point.get("interventions"), point.get("rate")])
    writer.writerow([])
    writer.writerow(["tool", "param", "count"])
    for item in report.get("heatmap") or []:
        writer.writerow([_redact(item.get("tool")), _redact(item.get("param")),
                         item.get("count")])
    writer.writerow([])
    writer.writerow(["tool", "rejects"])
    for item in report.get("top_rejected") or []:
        writer.writerow([_redact(item.get("tool")), item.get("count")])
    writer.writerow([])
    writer.writerow(["meta", "key", "value"])
    for key, value in export_meta_rows(report):
        writer.writerow(["meta", key, value])
    return out.getvalue()


def export_html(report: dict, *, lang: str = "zh",
                extra_note: str | None = None) -> str:
    """仪表盘 → 可打印 HTML（**零外链、零脚本**，浏览器 Ctrl+P 存 PDF）。

    它是 PDF 的降级产物（``reportlab`` 缺失或构建失败），口径逐字对齐
    ``audit.render_html``：``<!doctype html>`` + ``<meta charset="utf-8">``、
    样式全内联、无 ``<script>``、无 ``src=`` / ``href=`` 外链、中文不依赖任何字体。
    降级不是失败，所以这句话要跟着文件走：``extra_note`` 写"为什么不是 PDF"，
    用户打开文件第一眼就看得见，不用回去翻响应头。
    """
    en = str(lang) == "en"
    title = ("TraceLens Analytics Dashboard" if en else "TraceLens 分析仪表盘")
    labels = _html_labels(en)
    rows = export_meta_rows(report)
    lines: list[str] = ["<!doctype html>",
                        f'<html lang="{"en" if en else "zh-CN"}">',
                        "<head>", '<meta charset="utf-8">',
                        '<meta name="viewport" content="width=device-width, initial-scale=1">',
                        f"<title>{_html.escape(title)}</title>",
                        "<style>",
                        "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
                        "margin:24px;color:#111;background:#fff;font-size:13px}",
                        "h1{font-size:20px;margin:0 0 4px}",
                        "h2{font-size:15px;margin:22px 0 6px}",
                        "p.note{color:#555;margin:0 0 14px}",
                        "p.warn{color:#8a4b00;background:#fff7e6;border:1px solid #f0c36d;"
                        "padding:8px 10px;margin:0 0 14px}",
                        "table{border-collapse:collapse;margin:0 0 10px;max-width:100%}",
                        "th,td{border:1px solid #bbb;padding:4px 8px;text-align:left;"
                        "vertical-align:top}",
                        "th{background:#f2f2f2;font-weight:600}",
                        "td.num{text-align:right;font-variant-numeric:tabular-nums}",
                        "footer{margin-top:18px;color:#666;font-size:11px}",
                        "@media print{body{margin:8mm;font-size:11px}"
                        "p.warn{background:#fff;border-color:#999}}",
                        "</style>",
                        "</head>", "<body>",
                        f"<h1>{_html.escape(title)}</h1>",
                        f'<p class="note">{_html.escape(labels["subtitle"])}'
                        f'{_html.escape(str(report.get("generated_at") or ""))}</p>']
    if extra_note:
        lines.append(f'<p class="warn">{_html.escape(str(extra_note))}</p>')

    lines.append(f'<h2>{_html.escape(labels["meta"])}</h2>')
    lines.append("<table>")
    lines.append(f"<tr><th>{_html.escape(labels['key'])}</th>"
                 f"<th>{_html.escape(labels['value'])}</th></tr>")
    for key, value in rows:
        lines.append(f"<tr><td>{_html.escape(key)}</td>"
                     f"<td>{_html.escape(value)}</td></tr>")
    lines.append("</table>")

    lines.append(f'<h2>{_html.escape(labels["metrics"])}</h2>')
    lines.append("<table>")
    lines.append(f"<tr><th>{_html.escape(labels['metric'])}</th>"
                 f"<th>{_html.escape(labels['value'])}</th></tr>")
    for key, label in _METRIC_HTML_LABELS:
        value = report.get(key, "")
        lines.append(f"<tr><td>{_html.escape(label if label else str(key))}</td>"
                     f'<td class="num">{_html.escape("" if value is None else str(value))}'
                     "</td></tr>")
    lines.append("</table>")

    lines.append(f'<h2>{_html.escape(labels["trend"])}</h2>')
    lines.append("<table>")
    lines.append(f"<tr><th>{_html.escape(labels['bucket'])}</th>"
                 f"<th>{_html.escape(labels['tasks'])}</th>"
                 f"<th>{_html.escape(labels['interventions'])}</th>"
                 f"<th>{_html.escape(labels['rate'])}</th></tr>")
    for point in report.get("trend") or []:
        lines.append(f"<tr><td>{_html.escape(str(point.get('bucket')))}</td>"
                     f'<td class="num">{_html.escape(str(point.get("tasks")))}</td>'
                     f'<td class="num">{_html.escape(str(point.get("interventions")))}</td>'
                     f'<td class="num">{_html.escape(str(point.get("rate")))}</td></tr>')
    lines.append("</table>")

    lines.append(f'<h2>{_html.escape(labels["heatmap"])}</h2>')
    lines.append("<table>")
    lines.append(f"<tr><th>{_html.escape(labels['tool'])}</th>"
                 f"<th>{_html.escape(labels['param'])}</th>"
                 f"<th>{_html.escape(labels['count'])}</th></tr>")
    for item in report.get("heatmap") or []:
        lines.append(f"<tr><td>{_html.escape(_redact(item.get('tool')))}</td>"
                     f"<td>{_html.escape(_redact(item.get('param')))}</td>"
                     f'<td class="num">{_html.escape(str(item.get("count")))}</td></tr>')
    lines.append("</table>")

    lines.append(f'<h2>{_html.escape(labels["rejected"])}</h2>')
    lines.append("<table>")
    lines.append(f"<tr><th>{_html.escape(labels['tool'])}</th>"
                 f"<th>{_html.escape(labels['count'])}</th></tr>")
    for item in report.get("top_rejected") or []:
        lines.append(f"<tr><td>{_html.escape(_redact(item.get('tool')))}</td>"
                     f'<td class="num">{_html.escape(str(item.get("count")))}</td></tr>')
    lines.append("</table>")

    lines.append(f'<footer>{_html.escape(labels["footer"])}</footer>')
    lines.append("</body>")
    lines.append("</html>")
    return "\n".join(lines) + "\n"


_METRIC_HTML_LABELS: tuple[tuple[str, str], ...] = (
    ("total_tasks", ""), ("total_interventions", ""), ("intervention_rate", ""),
    ("avg_approval_seconds", ""), ("avg_approval_ms", ""), ("trust_health", ""),
    ("approved", ""), ("rejected", ""),
)


def _html_labels(en: bool) -> dict[str, str]:
    """HTML 页面标题（内联文案，不走 i18n：产物是文件，不是界面；zh/en 两套写死）。"""
    if en:
        return {"subtitle": "Generated at ", "meta": "Export metadata",
                "metrics": "Metrics", "trend": "Trend", "heatmap": "Modified parameters",
                "rejected": "Rejected tools", "key": "key", "value": "value",
                "metric": "metric", "bucket": "bucket", "tasks": "tasks",
                "interventions": "interventions", "rate": "rate", "tool": "tool",
                "param": "param", "count": "count",
                "footer": "Every number here is recomputable from the action records; "
                          "print (Ctrl+P) to keep this as a PDF."}
    return {"subtitle": "生成时间 ", "meta": "导出元数据", "metrics": "指标",
            "trend": "趋势", "heatmap": "常改参数", "rejected": "被拒工具",
            "key": "键", "value": "值", "metric": "指标", "bucket": "时间桶",
            "tasks": "任务数", "interventions": "干预数", "rate": "干预率",
            "tool": "工具", "param": "参数", "count": "次数",
            "footer": "本文档每个数字都可从动作记录逐条复算；浏览器 Ctrl+P 即可存为 PDF。"}


def _load_reportlab() -> tuple[dict[str, Any] | None, str | None]:
    """惰性导入 reportlab 的 PDF 两件；任何失败都当作"不可用"，并带上原因。

    口径与 ``audit._load_reportlab`` 同款：``importlib`` 字符串导入（包里不出现
    ``reportlab`` 的 import 语句，离线纯度扫描只认标准库），拿不到就返回 ``(None, 原因码)``，
    绝不因为第三方库缺失把一次 GET 变成 500。
    """
    try:
        return ({"pagesizes": importlib.import_module("reportlab.lib.pagesizes"),
                 "pdf_canvas": importlib.import_module("reportlab.pdfgen.canvas")},
                None)
    except Exception as exc:                                          # noqa: BLE001
        return None, f"reportlab_unavailable:{type(exc).__name__}"


def export_pdf(report: dict, *, lang: str = "zh",
               outcome: dict | None = None) -> bytes | None:
    """仪表盘 → PDF 字节；拿不到就 ``None``（调用方按设计降级，不报错）。

    两条"拿不到"都算，且都如实写进 ``outcome``（R6-3 新增的**可选出参**，
    老调用点不传 = 行为逐字不变）：

    * ``reportlab`` 导入不了 → ``reportlab_unavailable:<类型>``；
    * 导入成功但**构建失败** → ``reportlab_build_failed:<类型>``（字体、内存、
      版本差异都在这条里；审计导出对同一个坑的口径就是"构建失败也不让内容丢"）。

    ``outcome`` 收到 ``{ok, reason, note}``——调用方据此如实标注**实发产物类型**
    （降级出可打印 HTML，文件名后缀 ``.html``），而不是悄悄塞一份别的格式。
    """
    namespace, reason = _load_reportlab()
    if namespace is None:
        _set_outcome(outcome, ok=False, reason=reason,
                     note=f"reportlab 不可用（{reason}）")
        return None
    buffer = io.BytesIO()
    try:
        pagesizes = namespace["pagesizes"]
        pdf_canvas = namespace["pdf_canvas"]
        canvas = pdf_canvas.Canvas(buffer, pagesize=pagesizes.A4)
        width, height = pagesizes.A4
        title = "TraceLens Analytics" if lang == "en" else "TraceLens 分析仪表盘"
        # Helvetica 没有中文字形：中文标题只进 PDF 元数据（setTitle/大纲），
        # 正文行一律 ASCII——静默丢字比换个标题严重得多。
        canvas.setTitle(_pdf_safe(title))
        y = height - 60
        canvas.setFont("Helvetica", 14)
        canvas.drawString(50, y, _pdf_safe("TraceLens Analytics"))
        canvas.setFont("Helvetica", 9)
        for line in _pdf_lines(report):
            y -= 16
            if y < 40:
                canvas.showPage()
                canvas.setFont("Helvetica", 9)
                y = height - 60
            canvas.drawString(50, y, line)
        canvas.showPage()
        canvas.save()
    except Exception as exc:                                          # noqa: BLE001
        build = f"reportlab_build_failed:{type(exc).__name__}"
        _set_outcome(outcome, ok=False, reason=build,
                     note=f"reportlab 构建 PDF 失败（{build}）")
        return None
    _set_outcome(outcome, ok=True, reason=None, note="PDF：reportlab 生成")
    return buffer.getvalue()


def _set_outcome(outcome: dict | None, *, ok: bool, reason: str | None,
                 note: str) -> None:
    """把降级原因如实写进出参（调用方给 ``None`` 就什么都不做，老签名行为不变）。"""
    if outcome is None:
        return
    outcome.clear()
    outcome.update({"ok": ok, "reason": reason, "note": note})


def _pdf_lines(report: dict) -> list[str]:
    """PDF 只放 ASCII 指标行 + **如实元数据行**（Helvetica 没有中文字形）。

    R6-3：元数据行进 PDF 是硬要求——导出件被转发出去之后，"这份数字来自预聚合命中
    还是流式扫描"必须还在纸上。值先过 :func:`_pdf_safe`：非 ASCII 直接剥掉，
    宁缺字也不静默把中文塞进没有字形的字体。
    """
    lines = [f"generated_at: {report.get('generated_at')}",
             f"granularity: {report.get('granularity')}",
             f"total_tasks: {report.get('total_tasks')}",
             f"total_interventions: {report.get('total_interventions')}",
             f"intervention_rate: {report.get('intervention_rate')}",
             f"avg_approval_seconds: {report.get('avg_approval_seconds')}",
             f"trust_health: {report.get('trust_health')}"]
    for point in report.get("trend") or []:
        lines.append(f"trend {point.get('bucket')}: tasks={point.get('tasks')} "
                     f"interventions={point.get('interventions')} "
                     f"rate={point.get('rate')}")
    for key, value in export_meta_rows(report):
        lines.append(f"meta {key}: {_pdf_safe(value)}")
    return [_pdf_safe(line) for line in lines]


def _pdf_safe(text: Any) -> str:
    """PDF 正文只走 ASCII：非 ASCII 字符剥掉（Helvetica 无中文字形，中文会静默丢）。"""
    return str(text).encode("ascii", "ignore").decode("ascii")



def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds") \
        .replace("+00:00", "Z")


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.analytics``：合成记录跑一遍全指标。"""
    import sys

    def record(idx: int, tool: str, decision: str | None, hour: int) -> dict:
        out = {"id": f"act-{idx:04d}", "agent_id": "agent-a",
               "action_type": "read", "status": "succeeded",
               "timestamp": f"2026-11-1{idx % 9}T{hour:02d}:00:00Z",
               "tool_used": {"name": tool}}
        if decision:
            out["user_intervention"] = {
                "decision": decision, "decided_by": "local-user",
                "decided_at": f"2026-11-1{idx % 9}T{hour + 1:02d}:30:00Z",
                "modified_fields": [{"name": "max_move_count"}]}
        return out

    records = [record(1, "calendar.read", "approve", 9),
               record(2, "fs.write", "modify", 10),
               record(3, "fs.write", "reject", 11),
               record(4, "mail.send", None, 12)]
    report = dashboard(records, range_from="2026-11-01", range_to="2026-11-30",
                       granularity="day", salt="demo-salt")
    assert report["total_tasks"] == 4 and report["total_interventions"] == 3
    assert report["top_rejected"] and report["top_rejected"][0]["tool"] == "fs.write"
    assert report["trend"] and report["trend"][0]["bucket"].startswith("2026-11-")
    assert report["users"]["ids"][0] == anonymise("local-user", salt="demo-salt")
    text = export_csv(report)
    assert "total_tasks" in text and "bucket" in text
    assert "meta,pre_aggregated,false" in text.replace("\r\n", "\n"), \
        "CSV 末尾必须自带如实元数据（R6-3：产物离开设服务端也要说清数字怎么来的）"
    pdf = export_pdf(report)
    assert pdf is None or pdf.startswith(b"%PDF-")
    page = export_html(report)
    # 离线纯度扫描扫的是**源码字面量**：外链前缀拆两段写（audit.py 的 ``_KEY_INFIX`` 同款手法），
    # 断言本身照样在运行时拦完整前缀。
    for needle in ("<script", "src=", "href=", "http" + "://", "https" + "://"):
        assert needle not in page, f"可打印 HTML 不许出现 {needle}（零外链零脚本口径）"
    assert "<td>pre_aggregated</td><td>false</td>" in page, \
        "可打印 HTML 必须带如实元数据行（降级时也要说清数字来源）"
    assert "total_tasks" in page
    snapshot = report_snapshot(report)
    assert "trend" in snapshot and "heatmap" not in snapshot
    print(f"analytics self-check ok: tasks={report['total_tasks']} "
          f"interventions={report['total_interventions']} "
          f"rate={report['intervention_rate']} trend_buckets={len(report['trend'])} "
          f"pdf={'reportlab' if pdf else 'fallback->html'} "
          f"html_bytes={len(page.encode('utf-8'))}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_self_check())
