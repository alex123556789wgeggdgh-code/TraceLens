#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REQ-004 R4-1 / B4-3：任务组**归属正确率**的带标注样本生成器 + 对账（固定种子可复现）。

老板裁决 `BOSS-REPLY-PM-PLAN-001.md` D-6 把 B4-3「任务组创建准确率 ≥99%」的判定口径
换成**归属正确率**（≥100 条标注样本、≥99%），字段完整率只作参考指标。本工具就是那把尺：

1. **构建时已知所属组**：生成器每造一条记录就同时写下它的标注（属于哪个组 / 无归属 /
   悬空 / 非对象条目），标注与记录一起产出，不存在"先跑完再猜答案"。
2. **固定种子可复现**：只吃 `random.Random(seed)`，默认种子 `20261115`；同一个种子跑两次
   产物逐字节一致（`tests/test_groups.py` 把这条钉成断言），换种子就换样本。
3. **判定用被测实现本身**：一致率比的是 `GroupService.aggregate(gid, records)` 真把这条
   记录收进了哪个组，**不写第二套归属推断**（`groups.py` 文件头：
   ``record["group_id"]`` 是唯一事实源，不猜）。
4. **无归属单列**：`group_id` 缺失 / 空串 / null 三类一律算"被留在组外"，条数照
   `groups.grouping_report()` 的 `declared` / `ungrouped` 口径单列，不进分母、不猜。
5. **分母只算"标注组真实存在"的声明**：标注指向一个**没建出来的组**（悬空）时，
   `aggregate` 无从谈起，这类条数单列（`excluded_from_denominator.dangling_group`），
   不塞进分母凑数，也不塞进"一致"里。
6. **字段完整率单列参考**（D-6）：8 个参考字段**不含** `group_id` —— schema 明写
   `group_id=null` 是"不属于任何任务组"的常态（`action_record.schema.json:1404`），
   把它算进"完整率"会把诚实的空值判成缺陷。

固定样本里额外钉了几条**诚实边界**（不受种子影响，条数写死）：
时间戳坏掉 / 时间戳缺失 / 非对象条目 / 记录了但不在成员表里的 Agent ——
这些正是"归属对了、别的地方容易糊"的位置。

用法::

    python -X utf8 tools/group_attribution_samples.py
    python -X utf8 tools/group_attribution_samples.py --seed 7 --count 150
    python -X utf8 tools/group_attribution_samples.py --json _tmp/r41_attribution.json --md _tmp/r41_attribution.md

产物（默认写仓库 `_tmp/`）：`r41_attribution.json`（机器可读全量证据）、
`r41_attribution.md`（对账表，REPORT-R4-1 直接引用）。临时数据目录只走
``tests/scratch.py``（禁 tempfile），跑完整目录删除，绝不写 ``var/``。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import random
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scratch import scratch_dir, scratch_reason                      # noqa: E402
from agent_explainer.feature_store import FeaturePaths               # noqa: E402
from agent_explainer.groups import MAX_AGENTS, GroupService          # noqa: E402

#: 样本种子（写死 = 可复现；REPORT 登记的种子必须与此一致）
DEFAULT_SEED = 20261115
#: 任务书下限：标注样本不得少于 100 条（低于此直接拒绝，不静默缩小验收）
MIN_SAMPLES = 100
DEFAULT_SAMPLES = 120
#: D-6 验收线：归属正确率 ≥ 99%
ACCURACY_FLOOR = 0.99
#: 参考字段（D-6：字段完整率只作参考，不作验收线）。**故意不含 `group_id`**：
#: schema 明写 `group_id=null` = 不属于任何任务组，是单 Agent 动作的常态，不是缺陷。
REFERENCE_FIELDS = ("id", "agent_id", "agent_name", "action_type", "status",
                    "timestamp", "latency_ms", "cost")
#: 基线时间：所有样本时间戳 = 基线 + 确定性偏移（不读墙上时钟 → 重跑逐字节一致）
BASE_TIME = _dt.datetime(2026, 11, 11, 0, 0, 0, tzinfo=_dt.timezone.utc)
#: 无归属样本占比（"被留在组外"必须真的存在，否则 100% 会被误读成"全分好组了"）
UNGROUPED_RATIO = 0.12
#: 悬空标注占比：标注指向一个**没建出来**的组 → 单列，不进分母
DANGLING_RATIO = 0.03
#: 悬空标注用的组 id（生成器**绝不**建这个组）
DANGLING_GROUP = "grp_ghost_not_created"

#: 任务组清单：成员数覆盖 1 与 MAX_AGENTS 两端；最后一条是**已关闭**的组，
#: 用来验证响应层 `updated_at` 走 `closed_at` 而不是"最后事件时间"。
GROUP_SPECS: tuple[dict, ...] = (
    {"id": "grp_alpha", "title": "发布检查", "objective": "上线前逐项核对",
     "agents": ["agent-alice", "agent-bob", "agent-carol"], "close": ""},
    {"id": "grp_beta", "title": "数据导入", "objective": "批量导入与校验",
     "agents": ["agent-dave", "agenterin", "agent-frank", "agent-grace", "agent-heidi"],
     "close": ""},
    {"id": "grp_gamma", "title": "十人并发", "objective": f"MAX_AGENTS={MAX_AGENTS} 边界组",
     "agents": [f"agent-i{i:02d}" for i in range(1, MAX_AGENTS + 1)], "close": ""},
    {"id": "grp_delta", "title": "单 Agent 组", "objective": "只有一个成员的组",
     "agents": ["agent-mallory"], "close": ""},
    {"id": "grp_epsilon", "title": "合规审计", "objective": "导出与封箱",
     "agents": ["agent-nina", "agent-oscar", "agent-peggy", "agent-quinn"],
     "close": "completed"},
)
ACTION_TYPES = ("read", "write", "mail.send", "fs.write", "api.call", "shell.exec")
STATUSES = ("succeeded", "failed", "running", "observed", "pending_confirmation",
            "skipped", "expired")
#: 记录了、但**不在任何成员表里**的 Agent（aggregate 按既有口径把它列进 agents 尾部；
#: 归属仍只看 record["group_id"]，不因为"它不是成员"就把它踢出组）
OUTSIDER_AGENT = "agent-outsider"


def _iso(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_record(rid: str, *, agent: str, gid: str, seconds: int,
                 rng: random.Random, **overrides) -> dict:
    status = rng.choice(STATUSES)
    record = {
        "id": rid,
        "group_id": gid,
        "agent_id": agent,
        "agent_name": agent.replace("agent-", "").upper(),
        "action_type": rng.choice(ACTION_TYPES),
        "status": status,
        "requires_action": status in ("pending_confirmation", "expired"),
        "timestamp": _iso(BASE_TIME + _dt.timedelta(seconds=seconds)),
        "latency_ms": 20 + rng.randrange(0, 980),
        "cost": {"currency": "CNY", "amount": round(rng.randrange(0, 120) / 100.0, 2)},
    }
    record.update(overrides)
    return record


def build_samples(seed: int = DEFAULT_SEED, count: int = DEFAULT_SAMPLES
                  ) -> tuple[list, list[dict]]:
    """产出 **(样本记录列表, 标注列表)**：构建时就知道每条属于哪个组。

    ``count`` 是**带组标注 + 无归属**这两类的条数（诚实边界的固定几条另加，
    条数写死并在报告里单列）；少于 :data:`MIN_SAMPLES` 直接报错，不静默缩小样本。
    """
    if count < MIN_SAMPLES:
        raise ValueError(f"标注样本不得少于 {MIN_SAMPLES} 条（D-6），收到 {count}")
    rng = random.Random(int(seed))
    group_ids = [spec["id"] for spec in GROUP_SPECS]
    members = {spec["id"]: list(spec["agents"]) for spec in GROUP_SPECS}
    pool = [agent for agent_list in members.values() for agent in agent_list]

    # 确定性、互不相同的时间偏移（排序后 = 生成顺序即时间顺序，与种子无关的单调性）
    offsets = sorted(rng.sample(range(0, 86400 * 3), int(count)))

    records: list = []
    labels: list[dict] = []
    for index, offset in enumerate(offsets, start=1):
        rid = f"act-{index:05d}"
        roll = rng.random()
        if roll < UNGROUPED_RATIO:
            kind, gid = "ungrouped", None
        elif roll < UNGROUPED_RATIO + DANGLING_RATIO:
            kind, gid = "dangling", DANGLING_GROUP
        else:
            kind, gid = "declared", rng.choice(group_ids)
        # 无归属/悬空的样本**照样有 agent_id**（Agent 在干活是事实，只是没声明属于哪个组）
        agent = rng.choice(members[gid]) if kind == "declared" else rng.choice(pool)
        record = _make_record(rid, agent=agent, gid=gid, seconds=offset, rng=rng)
        if kind == "ungrouped":
            # 三种"无归属"写法都覆盖：缺键 / 空串 / null（schema 明写 null 合法）
            style = rng.choice(("absent", "empty", "null"))
            if style == "absent":
                record.pop("group_id")
            elif style == "empty":
                record["group_id"] = ""
            else:
                record["group_id"] = None
        labels.append({"record_id": rid, "expected_group_id": gid,
                       "declared": kind in ("declared", "dangling"), "kind": kind})
        records.append(record)

    # ---- 诚实边界（条数写死，不受种子影响；每条都在测"归属对了、别处容易糊"）----
    tail = len(offsets)
    # 1) 真实组 + 时间戳坏掉：归属必须照样对（时间解析不了只影响窗口/排序，不影响归属）
    records.append(_make_record("act-90001", agent=members["grp_alpha"][0],
                                gid="grp_alpha", seconds=tail + 1, rng=rng,
                                timestamp="not-a-time"))
    labels.append({"record_id": "act-90001", "expected_group_id": "grp_alpha",
                   "declared": True, "kind": "declared", "note": "timestamp 不可解析"})
    # 2) 真实组 + 完全没有 timestamp：归属必须照样对
    broken = _make_record("act-90002", agent=members["grp_beta"][0], gid="grp_beta",
                          seconds=tail + 2, rng=rng)
    broken.pop("timestamp")
    records.append(broken)
    labels.append({"record_id": "act-90002", "expected_group_id": "grp_beta",
                   "declared": True, "kind": "declared", "note": "timestamp 缺失"})
    # 3) 越权 Agent（不在成员表）：归属照声明，aggregate 把它列进 agents 尾部
    records.append(_make_record("act-90003", agent=OUTSIDER_AGENT, gid="grp_gamma",
                                seconds=tail + 3, rng=rng))
    labels.append({"record_id": "act-90003", "expected_group_id": "grp_gamma",
                   "declared": True, "kind": "declared", "note": "agent 不在成员表"})
    # 4) 两条非对象条目：aggregate / grouping_report 必须**跳过**（不计入 total，不抛异常）
    records.append(None)
    records.append("act-90004-not-a-mapping")
    labels.append({"record_id": None, "expected_group_id": None, "declared": False,
                   "kind": "non_mapping", "note": "None 条目：被测实现应跳过"})
    labels.append({"record_id": None, "expected_group_id": None, "declared": False,
                   "kind": "non_mapping", "note": "字符串条目：被测实现应跳过"})
    return records, labels


def create_groups(service: GroupService) -> list[dict]:
    """按 `GROUP_SPECS` 真建任务组（悬空组 `DANGLING_GROUP` **绝不建**）。"""
    made = []
    for spec in GROUP_SPECS:
        group = service.create(title=spec["title"], agent_ids=list(spec["agents"]),
                               objective=spec["objective"], group_id=spec["id"])
        if spec["close"]:
            group = service.close(spec["id"], status=spec["close"])
        made.append(group)
    return made


def reconcile(service: GroupService, records: list, labels: list[dict]) -> dict:
    """把标注与 `aggregate` / `grouping_report` 的**实际**归属判定逐条对账。

    一致率的分母只含「标注组真实存在」的声明；`ungrouped` / `dangling` / `non_mapping`
    三类**逐类单列**（含条数与口径说明），不折进分母、不折进"一致"。
    """
    known = [group["id"] for group in service.list()]
    views: dict[str, set] = {}
    view_payload: dict[str, dict] = {}
    for gid in known:
        view = service.aggregate(gid, records)
        views[gid] = {str(row.get("id")) for row in view["records"]}
        view_payload[gid] = {
            "id": gid,
            "declared_agents": len(view["group"]["agent_ids"]),
            "records_in_view": len(view["records"]),
            "agents_listed": [row["id"] for row in view["agents"]],
            "window_ended_at": (view["window"] or {}).get("ended_at"),
        }

    matched, denominator = 0, 0
    misses: list[dict] = []
    ungrouped_expected = dangling = non_mapping = 0
    ungrouped_leaked: list[str] = []
    for label in labels:
        kind = label.get("kind")
        rid = label.get("record_id")
        expected = label.get("expected_group_id")
        if kind == "non_mapping":
            non_mapping += 1
            continue
        if kind == "ungrouped":
            ungrouped_expected += 1
            if any(rid in views[gid] for gid in known):
                ungrouped_leaked.append(str(rid))       # 无归属却被归进组 = 猜了
            continue
        if expected not in views:
            dangling += 1                                # 标注组没建出来：不进分母
            continue
        denominator += 1
        homes = [gid for gid in known if rid in views[gid]]
        if homes == [expected]:
            matched += 1
        else:
            misses.append({"record_id": rid, "expected": expected, "actual": homes})

    report = service.grouping_report(records)
    mapping_records = sum(1 for row in records if hasattr(row, "get"))
    accuracy = round(matched / denominator, 6) if denominator else 1.0

    # 参考指标：字段完整率（单列，不作验收线）
    field_hits = {field: 0 for field in REFERENCE_FIELDS}
    complete = 0
    for row in records:
        if not hasattr(row, "get"):
            continue
        ok = True
        for field in REFERENCE_FIELDS:
            value = row.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                ok = False
                continue
            field_hits[field] += 1
        if ok:
            complete += 1

    return {
        "schema": "r41-attribution-1",
        "acceptance": {
            "metric": "attribution_accuracy",
            "clause": "REQ-004 B4-3（D-6 裁定：归属正确率）",
            "floor": ACCURACY_FLOOR,
            "min_samples": MIN_SAMPLES,
            "denominator": denominator,
            "matched": matched,
            "accuracy": accuracy,
            "pass": bool(denominator >= MIN_SAMPLES and accuracy >= ACCURACY_FLOOR),
        },
        "excluded_from_denominator": {
            "ungrouped": ungrouped_expected,
            "dangling_group": dangling,
            "non_mapping": non_mapping,
            "note": ("ungrouped=没有 group_id（缺键/空串/null，groups.grouping_report.ungrouped 口径，不猜）；"
                     "dangling=标注指向没建出来的组（aggregate 无从判定）；"
                     "non_mapping=非对象条目（被测实现跳过，不计入 total）"),
        },
        "ungrouped_ledger": {
            "labels": ungrouped_expected,
            "grouping_report": report["ungrouped"],
            "一致": bool(ungrouped_expected == report["ungrouped"]),
            "leaked_into_a_group": ungrouped_leaked,
            "口径": "groups.grouping_report() 的 declared / ungrouped（declared_only 基线，零推断）",
        },
        "grouping_report": report,
        "self_checks": {
            "mapping_records_match_report_total": bool(mapping_records == report["total"]),
            "declared_labels_match_report_declared": bool(
                sum(1 for row in labels if row.get("declared")) == report["declared"]),
            "group_count_matches_report_groups": bool(
                len({row.get("expected_group_id") for row in labels
                     if row.get("expected_group_id")}) == report["groups"]),
            "records_in_multiple_groups": sorted({row["record_id"] for row in misses}),
            "ungrouped_never_grouped": bool(not ungrouped_leaked),
        },
        "misses": misses,
        "reference_field_completeness": {
            "acceptance_line": False,
            "note": "D-6：字段完整率只作参考指标，不作验收线；参考字段不含 group_id（schema 1404 明写 null 合法）",
            "records": mapping_records,
            "complete_records": complete,
            "complete_rate": round(complete / mapping_records, 6) if mapping_records else 0.0,
            "per_field_coverage": {field: (round(count / mapping_records, 6) if mapping_records else 0.0)
                                   for field, count in field_hits.items()},
        },
        "groups": [view_payload[gid] for gid in known],
        "counts": {"records_generated": len(records), "mapping_records": mapping_records,
                   "labels": len(labels)},
    }


def render_markdown(result: dict, *, seed: int, count: int) -> str:
    acc = result["acceptance"]
    lines = [
        "# R4-1 归属正确率对账（REQ-004 B4-3 · D-6 口径）",
        "",
        f"- 种子：`{seed}`　请求条数：`{count}`　生成记录：{result['counts']['records_generated']} 条"
        f"（对象 {result['counts']['mapping_records']} 条 / 非对象 {result['excluded_from_denominator']['non_mapping']} 条）",
        f"- **归属正确率：{acc['matched']}/{acc['denominator']} = {acc['accuracy']}"
        f"（验收线 ≥{ACCURACY_FLOOR}）→ {'PASS' if acc['pass'] else 'FAIL'}**",
        f"- 分母外单列：无归属 {result['excluded_from_denominator']['ungrouped']} 条 · "
        f"悬空标注 {result['excluded_from_denominator']['dangling_group']} 条 · "
        f"非对象条目 {result['excluded_from_denominator']['non_mapping']} 条",
        f"- `grouping_report`：declared={result['grouping_report']['declared']} · "
        f"ungrouped={result['grouping_report']['ungrouped']} · "
        f"accuracy={result['grouping_report']['accuracy']} · basis={result['grouping_report']['basis']}",
        f"- 字段完整率（参考，不作验收线）：{result['reference_field_completeness']['complete_rate']}"
        f"（{result['reference_field_completeness']['complete_records']}/"
        f"{result['reference_field_completeness']['records']}）",
        "",
        "| 任务组 | 声明成员 | 视图内记录条数 | 视图列出的 Agent 数 | 组内最后事件时间 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for group in result["groups"]:
        lines.append(f"| `{group['id']}` | {group['declared_agents']} | {group['records_in_view']} "
                     f"| {len(group['agents_listed'])} | {group['window_ended_at']} |")
    lines += ["", "## 自检（逐条布尔，全真才算对账成立）", "",
              "```json", json.dumps(result["self_checks"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="样本种子（默认写死）")
    parser.add_argument("--count", type=int, default=DEFAULT_SAMPLES,
                        help=f"带标注样本条数（下限 {MIN_SAMPLES}）")
    parser.add_argument("--json", dest="json_path", default=None, help="机器可读产物路径")
    parser.add_argument("--md", dest="md_path", default=None, help="对账表产物路径")
    parser.add_argument("--data-dir", dest="data_dir", default=None,
                        help="临时数据目录（默认 tests/scratch.py 的 scratch 落点，跑完删除）")
    args = parser.parse_args(argv)

    records, labels = build_samples(seed=args.seed, count=args.count)
    base = pathlib.Path(args.data_dir) if args.data_dir else scratch_dir("r41-attribution")
    created = False
    try:
        if not args.data_dir:
            created = True
        paths = FeaturePaths(base)
        paths.ensure()
        service = GroupService(paths)
        create_groups(service)
        result = reconcile(service, records, labels)
    finally:
        if created:
            shutil.rmtree(base, ignore_errors=True)

    result["generated_with"] = {"seed": args.seed, "count": args.count,
                                "scratch_reason": scratch_reason()}
    print(json.dumps({"acceptance": result["acceptance"],
                      "excluded_from_denominator": result["excluded_from_denominator"],
                      "ungrouped_ledger": {k: v for k, v in result["ungrouped_ledger"].items()
                                           if k != "leaked_into_a_group"},
                      "reference_field_completeness": {
                          "acceptance_line": False,
                          "complete_rate": result["reference_field_completeness"]["complete_rate"]},
                      "self_checks": result["self_checks"]},
                     ensure_ascii=False, indent=2))

    out_dir = ROOT / "_tmp"
    json_path = pathlib.Path(args.json_path) if args.json_path else out_dir / "r41_attribution.json"
    md_path = pathlib.Path(args.md_path) if args.md_path else out_dir / "r41_attribution.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(result, seed=args.seed, count=args.count), encoding="utf-8")
    print(f"产物：{json_path}\n产物：{md_path}")
    return 0 if result["acceptance"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
