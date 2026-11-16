"""``agent_explainer/groups.py`` 的单测（规格书功能四：多 Agent 任务聚合视图）。

运行（全程离线，只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_groups.py" -v

覆盖点：建组 / 上限 / 去重 / 关闭 / 聚合排序 / 进度分区 / 资源与窗口 / 卡住判定 /
1000 条 2 秒计时 / 分组准确率 / 落盘重读 / 坏行容错 / 错误码。

R4-1（REQ-004 补做 · 第 1 子任务）新增三段：

* ``ResponseAliasTests`` —— `/api/groups`（T18）与 `/api/groups/<id>`（T19）响应层的
  三个**只读派生别名**（`group_id` / `user_id` / `updated_at`）逐条口径，
  外加「别名**没进账本**」的落盘证据（`groups.jsonl` 实体键一字未增）。
* ``AttributionSamplesTests`` —— B4-3 按 D-6 的**归属正确率**：固定种子 ≥100 条带标注
  样本（`tools/group_attribution_samples.py`）与 `aggregate` / `grouping_report` 逐条对账，
  并把「无 `group_id`、被留在组外」的条数单列（不猜）；字段完整率只作参考。
* ``D6CreateInterfaceTests`` —— D-6 附加的**创建接口用例**：重复请求幂等、并发创建、
  非法 `agent_id` 拒绝，成功率 100%；`MAX_AGENTS=10` 边界钉住。
  口径如实说明：建组**没有 HTTP 路由**（契约 54 条里只有 GET `/api/groups` 与
  `POST /api/groups/<id>/share`），所以这三组用例打在 `GroupService.create`
  这个**创建接口本体**上，不冒充「HTTP 端点用例」。

数据目录一律走 ``tests/scratch.py`` 的 ``scratch_dir()``：系统临时目录优先（显式
``TMPDIR`` 可覆盖），不可写时按约定退回仓库 ``var/``，绝不落在 ``tests/`` 下。
``groups._self_check`` 内部用标准库临时目录，受限沙箱里它的 ``chmod`` 会被 ACL 拒，
用例用 ``use_scratch_tempfile()`` 把 tempfile 接到同一套 scratch 落点（跑自检真身）。
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import pathlib
import shutil
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor  # R4-1：并发创建用例

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - 正常运行已可导入
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:  # pragma: no cover - 正常运行已可导入
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "tools") not in sys.path:  # pragma: no cover - R4-1 样本生成器在 tools/
    sys.path.insert(0, str(ROOT / "tools"))

from scratch import scratch_dir, use_scratch_tempfile  # noqa: E402  临时目录一律走 scratch（见 tests/scratch.py）
from agent_explainer.feature_store import FeaturePaths  # noqa: E402

from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from agent_explainer.feature_store import canonical_json  # noqa: E402  逐字节比落盘行用
from agent_explainer import server as sv  # noqa: E402  R4-1：响应层别名住在 server.py
import group_attribution_samples as gas  # noqa: E402  R4-1：B4-3 归属正确率样本生成器
from agent_explainer.groups import (  # noqa: E402
    CLOSE_STATUSES,
    GROUP_STATUSES,
    MAX_AGENTS,
    STUCK_AFTER_SECONDS,
    GroupError,
    GroupService,
    _self_check as groups_self_check,
    color_for,
    color_index_for,
    zone_of,
)

UTC = _dt.timezone.utc


def _iso(moment: _dt.datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_record(index: int, **overrides) -> dict:
    """构造一条 ActionRecord 形状的字典（只含聚合视图读得到的字段）。

    ``index`` 只用来派生 id 与时间偏移：**不能直接塞进 ``second=``**，
    否则 1000 条的性能用例会在 index ≥ 60 时抛 ``ValueError: second must be in 0..59``，
    让「1000 条 ≤ 2 秒」这条验收从来没真正跑过。
    """
    record = {
        "id": f"act-{index:04d}",
        "group_id": "grp_test",
        "agent_id": "agent-a",
        "agent_name": "Agent A",
        "action_type": "read",
        "status": "succeeded",
        "requires_action": False,
        "timestamp": _iso(_dt.datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC)
                          + _dt.timedelta(seconds=index)),
        "latency_ms": 100,
        "cost": {"currency": "CNY", "amount": 0.01},
    }
    record.update(overrides)
    return record


class GroupCase(unittest.TestCase):
    """公共夹具：临时数据目录 + 冻住的时钟 + 一个默认任务组。"""

    def setUp(self) -> None:
        self.base = scratch_dir("groups")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.clock = {"now": _dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)}
        self.service = self.make_service()

    def make_service(self) -> GroupService:
        return GroupService(self.paths, now=lambda: self.clock["now"])

    def new_group(self, agents=("agent-a", "agent-b"), **kwargs) -> dict:
        kwargs.setdefault("title", "发布检查")
        return self.service.create(agent_ids=list(agents), **kwargs)

    def aggregate(self, group_id: str, records) -> dict:
        return self.service.aggregate(group_id, records)


# --------------------------------------------------------------------------- #
# 一、建组与边界
# --------------------------------------------------------------------------- #

class CreateGroupTests(GroupCase):
    def test_create_returns_contract_fields_and_open_status(self) -> None:
        group = self.new_group(objective="把发布检查拆给两个 Agent")
        self.assertTrue(group["id"].startswith("grp_"), group["id"])
        self.assertEqual(group["title"], "发布检查")
        self.assertEqual(group["objective"], "把发布检查拆给两个 Agent")
        self.assertEqual(group["status"], "open")
        self.assertEqual(group["agent_ids"], ["agent-a", "agent-b"])
        self.assertEqual(group["created_at"], "2026-11-11T12:00:00Z")
        self.assertIsNone(group["closed_at"])
        self.assertEqual(group["created_by"], "local")
        self.assertEqual(self.service.get(group["id"]), group)

    def test_create_requires_non_empty_title(self) -> None:
        for bad in ("", "   ", None, 123):
            with self.assertRaises(GroupError) as caught:
                self.service.create(title=bad, agent_ids=["agent-a"])
            self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
            self.assertEqual(caught.exception.field, "title")

    def test_create_requires_agent_array(self) -> None:
        for bad in ([], None, "agent-a", {"id": "agent-a"}):
            with self.assertRaises(GroupError) as caught:
                self.service.create(title="x", agent_ids=bad)
            self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
            self.assertEqual(caught.exception.field, "agent_ids")

    def test_create_deduplicates_and_preserves_caller_order(self) -> None:
        group = self.service.create(title="x", agent_ids=["b", "a", "b", "c", "a"])
        self.assertEqual(group["agent_ids"], ["b", "a", "c"])

    def test_create_accepts_id_objects(self) -> None:
        group = self.service.create(
            title="x",
            agent_ids=[{"id": "agent-a", "name": "A"}, {"agent_id": "agent-b"}],
        )
        self.assertEqual(group["agent_ids"], ["agent-a", "agent-b"])

    def test_create_max_agents_boundary_and_exceeded(self) -> None:
        exact = [f"agent-{i}" for i in range(MAX_AGENTS)]
        group = self.service.create(title="刚好十个", agent_ids=exact)
        self.assertEqual(len(group["agent_ids"]), MAX_AGENTS)

        too_many = [f"agent-{i}" for i in range(MAX_AGENTS + 1)]
        with self.assertRaises(GroupError) as caught:
            self.service.create(title="十一个", agent_ids=too_many)
        error = caught.exception
        self.assertEqual(error.code, "GROUP_AGENTS_EXCEEDED")
        self.assertEqual(error.field, "agent_ids")
        self.assertEqual(error.detail["max"], MAX_AGENTS)
        self.assertEqual(error.detail["requested"], MAX_AGENTS + 1)
        # 超限时**一条都不写**：不能留下"半成品组"
        self.assertEqual(len(self.service.list()), 1)

    def test_create_custom_id_and_duplicate_rejected(self) -> None:
        group = self.new_group(group_id="grp_fixed")
        self.assertEqual(group["id"], "grp_fixed")
        with self.assertRaises(GroupError) as caught:
            self.new_group(group_id="grp_fixed")
        self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
        self.assertEqual(caught.exception.field, "group_id")

    def test_get_and_list_filter_by_status(self) -> None:
        first = self.new_group()
        second = self.new_group(title="第二个")
        self.assertIsNone(self.service.get("grp_missing"))
        self.assertEqual([g["id"] for g in self.service.list()], [first["id"], second["id"]])
        self.service.close(second["id"])
        self.assertEqual([g["id"] for g in self.service.list(status="open")], [first["id"]])
        self.assertEqual([g["id"] for g in self.service.list(status="completed")],
                         [second["id"]])
        with self.assertRaises(GroupError) as caught:
            self.service.list(status="bogus")
        self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
        self.assertIn("open", GROUP_STATUSES)

    def test_close_sets_terminal_status_and_second_close_is_conflict(self) -> None:
        group = self.new_group()
        closed = self.service.close(group["id"], status="cancelled")
        self.assertEqual(closed["status"], "cancelled")
        self.assertEqual(closed["closed_at"], "2026-11-11T12:00:00Z")
        self.assertEqual(closed["closed_by"], "local")
        self.assertIn(closed["status"], CLOSE_STATUSES)
        with self.assertRaises(GroupError) as caught:
            self.service.close(group["id"])
        self.assertEqual(caught.exception.code, "GROUP_CLOSED")
        with self.assertRaises(GroupError) as caught:
            self.service.close("grp_missing")
        self.assertEqual(caught.exception.code, "GROUP_NOT_FOUND")

    def test_close_rejects_unknown_status(self) -> None:
        group = self.new_group()
        with self.assertRaises(GroupError) as caught:
            self.service.close(group["id"], status="open")
        self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
        self.assertEqual(caught.exception.field, "status")


# --------------------------------------------------------------------------- #
# 二、聚合：排序 / 进度 / Agent / 资源 / 窗口 / 卡住
# --------------------------------------------------------------------------- #

class AggregateTests(GroupCase):
    def test_filters_by_group_and_sorts_by_absolute_time(self) -> None:
        group = self.new_group()
        gid = group["id"]
        records = [
            make_record(2, group_id=gid, timestamp="2026-11-11T18:00:05+08:00"),
            make_record(1, group_id=gid, timestamp="2026-11-11T10:00:00Z"),
            make_record(9, group_id="grp_other", timestamp="2026-11-11T10:00:01Z"),
            make_record(3, group_id=gid, timestamp="2026-11-11T10:00:02Z"),
            make_record(4, group_id=gid, timestamp="not-a-time"),
        ]
        view = self.aggregate(gid, records)
        self.assertEqual([r["id"] for r in view["records"]],
                         ["act-0001", "act-0003", "act-0002", "act-0004"])
        self.assertEqual(view["progress"]["total"], 4)
        self.assertEqual(view["group"]["id"], gid)

    def test_aggregate_does_not_mutate_input_records(self) -> None:
        gid = self.new_group()["id"]
        records = [make_record(i, group_id=gid) for i in range(1, 5)]
        before = copy.deepcopy(records)
        self.aggregate(gid, records)
        self.assertEqual(records, before)  # 一个字节都没改
        # 返回的记录是顶层浅拷贝：改返回体不会改入参
        view = self.aggregate(gid, records)
        view["records"][0]["status"] = "failed"
        self.assertEqual(records[0]["status"], "succeeded")

    def test_progress_zones_match_frontend_rule(self) -> None:
        gid = self.new_group()["id"]
        records = [
            make_record(1, group_id=gid, status="succeeded"),
            make_record(2, group_id=gid, status="failed"),
            make_record(3, group_id=gid, status="skipped"),
            make_record(4, group_id=gid, status="running"),
            make_record(5, group_id=gid, status="observed"),
            make_record(6, group_id=gid, status="pending_confirmation",
                        requires_action=True),
            make_record(7, group_id=gid, status="expired", requires_action=True),
        ]
        view = self.aggregate(gid, records)
        progress = view["progress"]
        self.assertEqual(
            {k: progress[k] for k in ("total", "done", "running", "needs_me")},
            {"total": 7, "done": 3, "running": 2, "needs_me": 2},
        )
        self.assertEqual(progress["percent"], 43)  # 3/7 → 42.86 → Math.round → 43
        # 后端分区口径必须和前端 zoneOf() 一致（否则三区数字会互相打脸）
        for record in records:
            self.assertEqual(zone_of(record), _frontend_zone(record))

    def test_agents_count_name_and_order(self) -> None:
        gid = self.new_group(agents=("agent-a", "agent-b"))["id"]
        records = [
            make_record(1, group_id=gid, agent_id="agent-b", agent_name="Agent B"),
            make_record(2, group_id=gid, agent_id="agent-a", agent_name="Agent A"),
            make_record(3, group_id=gid, agent_id="agent-a", agent_name="Agent A"),
            make_record(4, group_id=gid, agent_id="agent-x", agent_name="外援"),
        ]
        agents = self.aggregate(gid, records)["agents"]
        self.assertEqual([a["id"] for a in agents], ["agent-a", "agent-b", "agent-x"])
        by_id = {a["id"]: a for a in agents}
        self.assertEqual(by_id["agent-a"]["count"], 2)
        self.assertEqual(by_id["agent-a"]["name"], "Agent A")
        self.assertEqual(by_id["agent-x"]["name"], "外援")
        for agent in agents:
            self.assertEqual(agent["color_index"], color_index_for(agent["id"]))
            self.assertIs(agent["stuck"], False)

    def test_resources_and_window(self) -> None:
        gid = self.new_group()["id"]
        records = [
            make_record(1, group_id=gid, agent_id="agent-a", latency_ms=100,
                        cost={"amount": 0.25},
                        timestamp="2026-11-11T10:00:00Z"),
            make_record(2, group_id=gid, agent_id="agent-b", latency_ms=400,
                        cost={"amount": 0.50},
                        timestamp="2026-11-11T10:00:03Z"),
            make_record(3, group_id=gid, agent_id="agent-a", latency_ms=50, cost=None,
                        timestamp="2026-11-11T10:00:05Z"),
        ]
        view = self.aggregate(gid, records)
        resources = view["resources"]
        self.assertEqual(resources["cost"], 0.75)
        self.assertEqual(resources["latency_ms"], 550)
        self.assertEqual(resources["cost_by_agent"], {"agent-a": 0.25, "agent-b": 0.5})
        self.assertEqual(resources["latency_by_agent"],
                         {"agent-a": 150, "agent-b": 400})
        self.assertEqual(view["window"],
                         {"started_at": "2026-11-11T10:00:00Z",
                          "ended_at": "2026-11-11T10:00:05Z",
                          "total_ms": 5000.0})

    def test_stuck_requires_running_only_and_ten_minutes_of_silence(self) -> None:
        gid = self.new_group(agents=("stuck", "recent", "done", "no-data"))["id"]
        now = self.clock["now"]
        records = [
            # 只有 running/observed，且最后一条在 20 分钟前 → 卡住
            make_record(1, group_id=gid, agent_id="stuck", status="running",
                        timestamp=_iso(now - _dt.timedelta(minutes=20))),
            make_record(2, group_id=gid, agent_id="stuck", status="observed",
                        timestamp=_iso(now - _dt.timedelta(minutes=25))),
            # 同样状态但刚刚还在动 → 不算卡住
            make_record(3, group_id=gid, agent_id="recent", status="running",
                        timestamp=_iso(now - _dt.timedelta(seconds=30))),
            # 已经是终态 → 不叫卡住，叫做完了
            make_record(4, group_id=gid, agent_id="done", status="succeeded",
                        timestamp=_iso(now - _dt.timedelta(hours=3))),
        ]
        view = self.aggregate(gid, records)
        self.assertEqual(view["stuck"], ["stuck"])
        by_id = {a["id"]: a for a in view["agents"]}
        self.assertTrue(by_id["stuck"]["stuck"])
        self.assertFalse(by_id["recent"]["stuck"])
        self.assertFalse(by_id["done"]["stuck"])
        # 一条记录都没有的成员：没数据 ≠ 卡住
        self.assertFalse(by_id["no-data"]["stuck"])
        self.assertEqual(by_id["no-data"]["count"], 0)
        self.assertGreater(STUCK_AFTER_SECONDS, 60)

    def test_aggregate_unknown_group_raises(self) -> None:
        with self.assertRaises(GroupError) as caught:
            self.aggregate("grp_missing", [])
        self.assertEqual(caught.exception.code, "GROUP_NOT_FOUND")

    def test_empty_records_still_returns_view(self) -> None:
        gid = self.new_group(agents=("agent-a",))["id"]
        view = self.aggregate(gid, [])
        self.assertEqual(view["records"], [])
        self.assertEqual(view["progress"], {"total": 0, "done": 0, "running": 0,
                                            "needs_me": 0, "percent": 0})
        self.assertEqual(view["window"], {"started_at": None, "ended_at": None,
                                          "total_ms": 0})
        self.assertEqual(view["stuck"], [])
        self.assertEqual(view["resources"]["cost"], 0.0)

    def test_aggregate_one_thousand_records_under_two_seconds(self) -> None:
        gid = self.new_group(agents=("agent-a", "agent-b"))["id"]
        base = _dt.datetime(2026, 11, 11, 9, 0, 0, tzinfo=UTC)
        statuses = ("succeeded", "failed", "running", "pending_confirmation",
                    "observed", "skipped")
        records = []
        for i in range(1000):
            status = statuses[i % len(statuses)]
            records.append(make_record(
                i,
                id=f"act-{i:05d}",
                group_id=gid,
                agent_id="agent-a" if i % 2 else "agent-b",
                agent_name="Agent A" if i % 2 else "Agent B",
                status=status,
                requires_action=status == "pending_confirmation",
                timestamp=_iso(base + _dt.timedelta(seconds=i)),
                latency_ms=10 + (i % 7),
                cost={"amount": round(0.001 * (i % 5), 4)},
            ))
        started = time.perf_counter()
        view = self.aggregate(gid, records)
        elapsed = time.perf_counter() - started
        print(f"\n[groups] 1000 条聚合实测：{elapsed * 1000:.1f} ms（规格 ≤ 2000 ms）")
        self.assertEqual(len(view["records"]), 1000)
        self.assertEqual(view["progress"]["total"], 1000)
        self.assertEqual(len({record["id"] for record in view["records"]}), 1000)
        self.assertEqual(sum(a["count"] for a in view["agents"]), 1000)
        self.assertLess(elapsed, 2.0, f"1000 条聚合耗时 {elapsed:.3f}s 超过 2s")


def _frontend_zone(record: dict) -> str:
    """``web/app.js`` 的 ``zoneOf()`` 逐条复刻，用来断言前后端口径一致。"""
    status = record.get("status")
    if record.get("requires_action") is True and status not in ("succeeded", "failed"):
        return "needs_me"
    if status in ("running", "observed"):
        return "running"
    return "done"


# --------------------------------------------------------------------------- #
# 三、分组准确率报告
# --------------------------------------------------------------------------- #

class GroupingReportTests(GroupCase):
    def test_accuracy_counts_declared_and_reports_ungrouped(self) -> None:
        records = [
            make_record(1, group_id="grp_a"),
            make_record(2, group_id="grp_a"),
            make_record(3, group_id="grp_b"),
            make_record(4, group_id=""),
            make_record(5, group_id=None),
            make_record(6, group_id="grp_b"),
        ]
        report = self.service.grouping_report(records)
        self.assertEqual(report["declared"], 4)
        self.assertEqual(report["grouped"], 4)
        self.assertEqual(report["matched"], 4)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["ungrouped"], 2)  # 没有 group_id 的一律不猜
        self.assertEqual(report["groups"], 2)
        self.assertEqual(report["basis"], "declared_only")

    def test_accuracy_is_vacuous_without_declarations_not_zero(self) -> None:
        report = self.service.grouping_report([make_record(1, group_id=None)])
        self.assertEqual(report["declared"], 0)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["accuracy_basis"], "vacuous")
        self.assertEqual(report["ungrouped"], 1)

    def test_report_matches_aggregate_grouping(self) -> None:
        """报告说"声明了就归得进去"，聚合就必须真的把这些记录收进视图。"""
        gid = self.new_group()["id"]
        records = [make_record(1, group_id=gid), make_record(2, group_id=gid),
                   make_record(3, group_id=None)]
        report = self.service.grouping_report(records)
        view = self.aggregate(gid, records)
        self.assertEqual(report["declared"], len(view["records"]))
        self.assertEqual(report["accuracy"], 1.0)


# --------------------------------------------------------------------------- #
# 四、落盘重读 / 坏行 / 颜色 / 自检
# --------------------------------------------------------------------------- #

class PersistenceTests(GroupCase):
    def test_reload_from_disk_sees_same_groups_without_duplication(self) -> None:
        group = self.new_group()
        self.service.close(group["id"], status="completed")
        reloaded = self.make_service()
        self.assertEqual(reloaded.get(group["id"])["status"], "completed")
        self.assertEqual(len(reloaded.list()), 1)
        # 再 reload 一次也不长出新条目（没有"隐藏的内存副本"）
        self.assertEqual(len(self.make_service().list()), 1)
        self.assertEqual(reloaded.facts()["store"]["items"], 1)
        # 新建的组在第二个实例里可见（文件是唯一事实源）
        self.new_group(title="第三个")
        self.assertEqual(len(self.make_service().list()), 2)

    def test_corrupt_line_is_reported_but_does_not_kill_the_store(self) -> None:
        group = self.new_group()
        with open(self.paths.groups, "a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        reloaded = self.make_service()
        self.assertIsNotNone(reloaded.get(group["id"]))
        self.assertTrue(reloaded.facts()["store"]["problems"])
        # 坏行不影响继续写
        fresh = reloaded.create(title="坏行之后", agent_ids=["agent-z"])
        self.assertEqual(self.make_service().get(fresh["id"])["title"], "坏行之后")

    def test_color_index_is_stable_bounded_and_hash_based(self) -> None:
        self.assertEqual(color_index_for("agent-a"), color_index_for("agent-a"))
        self.assertNotEqual(color_index_for("agent-a"), color_index_for("agent-b"))
        for agent in ("a", "b", "c", "demo-photo-bot", "中文-Agent"):
            index = color_index_for(agent)
            self.assertIsInstance(index, int)
            self.assertGreaterEqual(index, 0)
            self.assertLess(index, 8)
            self.assertEqual(color_for(agent), color_for(agent))
        # 与进程无关的稳定值：换进程重算必须一样（内建 hash 做不到）
        self.assertEqual(color_index_for("agent-a"),
                         int(__import__("hashlib").sha256("agent-a".encode()).hexdigest(), 16) % 8)

    def test_error_shape_is_serialisable(self) -> None:
        with self.assertRaises(GroupError) as caught:
            self.new_group(agents=[f"agent-{i}" for i in range(MAX_AGENTS + 1)])
        payload = caught.exception.as_dict()
        json.dumps(payload)  # 必须能直接进错误响应
        self.assertEqual(payload["code"], "GROUP_AGENTS_EXCEEDED")
        self.assertIn("reason", payload)
        self.assertIn("GROUP_AGENTS_EXCEEDED", caught.exception.render())

    def test_self_check_runs(self) -> None:
        # 自检（groups._self_check）内部用标准库临时目录；受限沙箱里 mkdtemp 的
        # chmod(0o700) 会被 ACL 拒（[WinError 5]，PM-VERDICT-R2-2 遗留③），那是这台
        # 机器的权限事实，不是聚合视图的失败。测试侧把 tempfile 接到 scratch 的探测
        # 落点（tests/scratch.py：同形替身，用完还原），跑的是自检真身，零改写。
        with use_scratch_tempfile():
            self.assertEqual(groups_self_check(), 0)


# --------------------------------------------------------------------------- #
# 四、R4-1 · `/api/groups` 响应层三别名（只读派生：账本一字未动）
# --------------------------------------------------------------------------- #

def ledger_rows(paths: FeaturePaths) -> list[dict]:
    """读 ``groups.jsonl`` 的追加行（信封 ``{op, ts, item}``）。

    「别名没进账本」「重复请求没留第二行」这两条都得看落盘，不能只看返回值。
    """
    text = pathlib.Path(paths.groups).read_text(encoding="utf-8") if \
        pathlib.Path(paths.groups).exists() else ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class ResponseAliasTests(unittest.TestCase):
    """T18 / T19 的响应层别名逐条口径 + 「别名不进账本」的落盘证据。

    夹具：一个未关闭组（含一条坏时间戳事件）、一个已关闭组（组内也有事件）、
    一个零记录空组，外加一条无归属记录与一条指向**没建出来的组**的记录。
    ``InterveneService`` 离线组装：T18 的 HTTP 响应体就是 ``groups_payload()`` 的逐字
    ``send_json``（``do_GET`` 里那一行），所以这里测的形状 == 界面读到的形状。
    """

    def setUp(self) -> None:
        self.base = scratch_dir("groups-alias")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.paths = FeaturePaths(self.base)
        self.paths.ensure()
        self.records = [
            make_record(1, group_id="grp_open", timestamp="2026-11-11T10:00:00Z"),
            make_record(2, group_id="grp_open", timestamp="2026-11-11T10:00:07Z"),
            make_record(3, group_id="grp_open", timestamp="not-a-time"),
            make_record(4, group_id="grp_closed", timestamp="2026-11-11T09:00:00Z"),
            make_record(5, group_id="grp_ghost", timestamp="2026-11-11T11:00:00Z"),
            make_record(6, group_id=None, timestamp="2026-11-11T12:00:00Z"),
        ]
        self.service = sv.InterveneService(records=[dict(row) for row in self.records],
                                          mode="local", features_root=self.base)
        self.service.group_service.create(title="开放组", agent_ids=["agent-a", "agent-b"],
                                         actor="captain", group_id="grp_open")
        self.service.group_service.create(title="已关闭组", agent_ids=["agent-a"],
                                         actor="captain-2", group_id="grp_closed")
        self.service.group_service.close("grp_closed", status="completed", actor="captain-2")
        self.service.group_service.create(title="零记录组", agent_ids=["agent-z"],
                                         group_id="grp_empty")

    # -- T18 ----------------------------------------------------------------
    def test_t18_aliases_are_views_of_ledger_facts(self) -> None:
        payload = self.service.groups_payload()
        self.assertEqual(payload["alias_fields"], ["group_id", "user_id", "updated_at"])
        # 随响应带一句人话：别名是响应层派生，不是账本已迁移（0.4 展示纪律）
        self.assertIn("响应层的只读派生别名", payload["note"])
        by_id = {group["id"]: group for group in payload["groups"]}
        self.assertEqual(sorted(by_id), ["grp_closed", "grp_empty", "grp_open"])

        opened = by_id["grp_open"]
        self.assertEqual(opened["group_id"], opened["id"])
        self.assertEqual(opened["user_id"], "captain")
        self.assertEqual(opened["user_id"], opened["created_by"])
        # 未关闭 → 组内最后事件时间（可解析的最大 timestamp；坏时间戳那条不参与）
        self.assertEqual(opened["updated_at"], "2026-11-11T10:00:07Z")
        self.assertEqual(opened["updated_at_basis"], sv.UPDATED_AT_BASIS_LAST_EVENT)

        closed = by_id["grp_closed"]
        self.assertEqual(closed["user_id"], "captain-2")
        self.assertEqual(closed["updated_at"], closed["closed_at"])
        self.assertEqual(closed["updated_at_basis"], sv.UPDATED_AT_BASIS_CLOSED)

    def test_t18_status_filter_still_carries_aliases(self) -> None:
        rows = self.service.groups_payload(status="open")["groups"]
        self.assertEqual(sorted(row["group_id"] for row in rows), ["grp_empty", "grp_open"])
        for row in rows:
            self.assertEqual(row["group_id"], row["id"])
            self.assertIn(row["updated_at_basis"],
                          (sv.UPDATED_AT_BASIS_LAST_EVENT, sv.UPDATED_AT_BASIS_CREATED))

    def test_last_event_index_never_guesses_attribution(self) -> None:
        # 「组内最后事件时间」只认 record["group_id"]：无归属记录、指向悬空组的记录，
        # 都不许被"看起来像"某个组的记录填进来（groups.py 文件头的唯一事实源口径）。
        index = sv.group_last_event_index(self.records)
        self.assertEqual(index, {"grp_open": "2026-11-11T10:00:07Z",
                                "grp_closed": "2026-11-11T09:00:00Z",
                                "grp_ghost": "2026-11-11T11:00:00Z"})
        self.assertNotIn("", index)
        self.assertNotIn(None, index)
        self.assertEqual(sv.group_last_event_index([]), {})
        self.assertEqual(sv.group_last_event_index([None, "not-a-mapping", {}]), {})

    # -- T19 ----------------------------------------------------------------
    def test_t19_group_section_reuses_the_window_end(self) -> None:
        view = self.service.group_aggregate("grp_open")
        self.assertEqual(view["group"]["updated_at"], view["window"]["ended_at"])
        self.assertEqual(view["group"]["updated_at"], "2026-11-11T10:00:07Z")
        self.assertEqual(view["group"]["updated_at_basis"], sv.UPDATED_AT_BASIS_LAST_EVENT)
        self.assertEqual(view["group"]["group_id"], "grp_open")
        self.assertEqual(view["group"]["user_id"], "captain")
        self.assertEqual(view["alias_fields"], ["group_id", "user_id", "updated_at"])
        # 记录列表一个字没加：别名只加在 group 段
        for row in view["records"]:
            for alias in ("group_id_alias", "user_id", "updated_at", "updated_at_basis"):
                self.assertNotIn(alias, row)

    def test_t19_closed_group_prefers_closed_at_over_its_events(self) -> None:
        view = self.service.group_aggregate("grp_closed")
        self.assertEqual(view["window"]["ended_at"], "2026-11-11T09:00:00Z")
        self.assertEqual(view["group"]["updated_at"], view["group"]["closed_at"])
        self.assertEqual(view["group"]["updated_at_basis"], sv.UPDATED_AT_BASIS_CLOSED)
        self.assertNotEqual(view["group"]["updated_at"], view["window"]["ended_at"])

    def test_group_with_no_events_never_invents_now(self) -> None:
        # 零事件组：updated_at = created_at（账本里真实存在的时刻），basis 说清来源
        sections = [self.service.group_aggregate("grp_empty")["group"],
                    {row["id"]: row for row in self.service.groups_payload()["groups"]}["grp_empty"]]
        for view in sections:
            self.assertEqual(view["updated_at"], view["created_at"])
            self.assertEqual(view["updated_at_basis"], sv.UPDATED_AT_BASIS_CREATED)

    def test_alias_view_is_a_read_only_copy(self) -> None:
        entity = {"id": "g1", "title": "T", "created_by": "me", "created_at": "2026-11-11T00:00:00Z",
                  "closed_at": None, "agent_ids": ["a"], "status": "open"}
        snapshot = copy.deepcopy(entity)
        opened = sv.group_alias_view(entity, last_event="2026-11-11T08:00:00Z")
        self.assertEqual(entity, snapshot, "别名视图改写了入参实体")
        self.assertEqual(opened["updated_at"], "2026-11-11T08:00:00Z")
        self.assertEqual(opened["updated_at_basis"], sv.UPDATED_AT_BASIS_LAST_EVENT)
        self.assertEqual(opened["agent_ids"], entity["agent_ids"])
        # 没有 last_event 也没关系：回落 created_at（真实落盘事实），不用 now() 冒充
        fallback = sv.group_alias_view(entity)
        self.assertEqual(fallback["updated_at"], entity["created_at"])
        self.assertEqual(fallback["updated_at_basis"], sv.UPDATED_AT_BASIS_CREATED)
        closed = sv.group_alias_view(dict(entity, closed_at="2026-11-12T00:00:00Z"),
                                    last_event="2026-11-11T08:00:00Z")
        self.assertEqual(closed["updated_at"], "2026-11-12T00:00:00Z")
        self.assertEqual(sv.group_alias_view(None)["group_id"], None)

    def test_aliases_never_enter_the_ledger(self) -> None:
        # 读过了 T18 + T19 之后再看落盘：别名若被写回，这里立刻红
        self.service.groups_payload()
        self.service.group_aggregate("grp_open")
        rows = ledger_rows(self.paths)
        self.assertEqual(len(rows), 4, "3 建 + 1 关 = 4 行追加（关组是追加，不是原地改写）")
        for row in rows:
            self.assertEqual(row["op"], "put")
            for alias in ("group_id", "user_id", "updated_at", "updated_at_basis"):
                self.assertNotIn(alias, row["item"], f"别名 {alias} 漏进了 groups.jsonl")
        self.assertEqual(sorted({key for row in rows for key in row["item"]}),
                         ["agent_ids", "closed_at", "closed_by", "created_at", "created_by",
                          "id", "objective", "status", "title"],
                         "任务组账本实体字段变了：R4-1 要求零迁移")

    def test_response_aliases_never_write_back_into_records(self) -> None:
        self.service.groups_payload()
        view = self.service.group_aggregate("grp_open")
        self.assertEqual(view["group"]["user_id"], "captain")
        after = {row["id"]: row.get("group_id") for row in self.service.store.all()}
        for row in self.records:
            self.assertEqual(after.get(row["id"]), row.get("group_id"),
                             f"记录 {row['id']} 的 group_id 被响应层别名改写了")

    def test_alias_helpers_are_exported_with_a_stable_shape(self) -> None:
        self.assertEqual(sv.GROUP_ALIAS_FIELDS, ("group_id", "user_id", "updated_at"))
        self.assertEqual((sv.UPDATED_AT_BASIS_CLOSED, sv.UPDATED_AT_BASIS_LAST_EVENT,
                          sv.UPDATED_AT_BASIS_CREATED),
                         ("closed_at", "last_event", "created_at_fallback"))


# --------------------------------------------------------------------------- #
# 五、R4-1 · B4-3 归属正确率（D-6 口径）：固定种子标注样本 + 逐条对账
# --------------------------------------------------------------------------- #

class AttributionSamplesTests(GroupCase):
    """``tools/group_attribution_samples.py`` 的样本与 `aggregate` / `grouping_report` 对账。

    B4-3 的判定线（D-6）：**归属正确率 ≥99%**，样本 ≥100 条。
    字段完整率单列参考，不进验收线。
    """

    def setUp(self) -> None:
        super().setUp()
        self.records, self.labels = gas.build_samples()
        gas.create_groups(self.service)
        self.result = gas.reconcile(self.service, self.records, self.labels)
        self.views = {spec["id"]: {row["id"] for row in
                                   self.service.aggregate(spec["id"], self.records)["records"]}
                      for spec in gas.GROUP_SPECS}

    def test_generator_is_reproducible_with_a_fixed_seed(self) -> None:
        first_records, first_labels = gas.build_samples()
        second_records, second_labels = gas.build_samples()
        plain = lambda rows: [dict(row) if isinstance(row, dict) else row for row in rows]
        self.assertEqual(plain(first_records), plain(second_records))
        self.assertEqual(first_labels, second_labels)
        self.assertEqual(gas.DEFAULT_SEED, 20261115, "种子写死在报告里，换号要升报告")

    def test_changing_the_seed_changes_the_samples(self) -> None:
        other, other_labels = gas.build_samples(seed=7)
        baseline = json.dumps([row for row in self.records if isinstance(row, dict)],
                              ensure_ascii=False, sort_keys=True, default=str)
        changed = json.dumps([row for row in other if isinstance(row, dict)],
                             ensure_ascii=False, sort_keys=True, default=str)
        self.assertNotEqual(baseline, changed, "换种子必须换样本，否则「可复现」没有反证")
        self.assertEqual(len(other_labels), len(self.labels))

    def test_sample_floor_is_enforced_not_silently_shrunk(self) -> None:
        self.assertGreaterEqual(gas.MIN_SAMPLES, 100, "D-6：标注样本不得少于 100 条")
        self.assertGreaterEqual(gas.DEFAULT_SAMPLES, gas.MIN_SAMPLES)
        with self.assertRaises(ValueError):
            gas.build_samples(count=gas.MIN_SAMPLES - 1)

    def test_attribution_accuracy_meets_b4_3(self) -> None:
        acceptance = self.result["acceptance"]
        self.assertEqual(acceptance["metric"], "attribution_accuracy")
        self.assertGreaterEqual(acceptance["denominator"], gas.MIN_SAMPLES)
        self.assertGreaterEqual(acceptance["accuracy"], gas.ACCURACY_FLOOR)
        self.assertTrue(acceptance["pass"], self.result["misses"])
        self.assertEqual(self.result["misses"], [])

    def test_self_checks_all_hold(self) -> None:
        checks = self.result["self_checks"]
        self.assertEqual(checks["records_in_multiple_groups"], [], "一条记录同时进两个组")
        self.assertTrue(checks["ungrouped_never_grouped"], "无归属记录被归进了组（= 猜了）")
        self.assertTrue(checks["mapping_records_match_report_total"])
        self.assertTrue(checks["declared_labels_match_report_declared"])
        self.assertTrue(checks["group_count_matches_report_groups"])

    def test_ungrouped_count_is_reported_not_guessed(self) -> None:
        ledger = self.result["ungrouped_ledger"]
        report = self.result["grouping_report"]
        self.assertGreater(ledger["labels"], 0, "样本里必须真的有一批「没声明归属」的记录")
        self.assertEqual(ledger["labels"], ledger["grouping_report"])
        self.assertEqual(ledger["labels"], report["ungrouped"])
        self.assertEqual(ledger["leaked_into_a_group"], [])
        # groups.py 的既有口径：accuracy = matched/declared，按构造恒 1.0，基线写明 declared_only
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["basis"], "declared_only")
        self.assertEqual(report["accuracy_basis"], "declared_only")
        self.assertEqual(report["declared"] + report["ungrouped"], report["total"])

    def test_dangling_and_non_mapping_stay_outside_the_denominator(self) -> None:
        excluded = self.result["excluded_from_denominator"]
        self.assertGreater(excluded["dangling_group"], 0)
        self.assertGreater(excluded["non_mapping"], 0)
        declared_labels = sum(1 for label in self.labels if label["declared"])
        self.assertEqual(self.result["acceptance"]["denominator"],
                         declared_labels - excluded["dangling_group"])
        self.assertEqual(self.result["grouping_report"]["total"],
                         self.result["counts"]["mapping_records"])

    def test_field_completeness_is_a_reference_metric_only(self) -> None:
        reference = self.result["reference_field_completeness"]
        self.assertIs(reference["acceptance_line"], False, "D-6：字段完整率不得当验收线")
        self.assertEqual(self.result["acceptance"]["metric"], "attribution_accuracy")
        self.assertGreater(reference["complete_rate"], 0.0)
        self.assertLessEqual(reference["complete_rate"], 1.0)
        self.assertNotIn("group_id", gas.REFERENCE_FIELDS,
                         "schema 明写 group_id=null 合法，不许把空归属算成缺陷")

    def test_honest_edge_cases_are_actually_covered(self) -> None:
        self.assertIn("act-90001", self.views["grp_alpha"], "坏时间戳的记录被归属丢了")
        self.assertIn("act-90002", self.views["grp_beta"], "缺时间戳的记录被归属丢了")
        self.assertIn("act-90003", self.views["grp_gamma"])
        gamma = self.service.aggregate("grp_gamma", self.records)
        listed = {row["id"] for row in gamma["agents"]}
        self.assertIn(gas.OUTSIDER_AGENT, listed, "非成员 Agent 没被如实列出")
        self.assertNotIn(gas.OUTSIDER_AGENT,
                         set(gamma["group"]["agent_ids"]), "越权 Agent 不该出现在成员声明里")
        ghost = [label for label in self.labels if label["kind"] == "dangling"]
        self.assertTrue(ghost)
        with self.assertRaises(GroupError) as caught:
            self.service.aggregate(gas.DANGLING_GROUP, self.records)
        self.assertEqual(caught.exception.code, "GROUP_NOT_FOUND")

    def test_reconcile_is_stable_across_two_runs(self) -> None:
        again = gas.reconcile(self.service, self.records, self.labels)
        self.assertEqual(again, self.result, "同一份样本对账两次结果不同 = 判定不可复现")

    def test_markdown_report_names_the_numbers(self) -> None:
        text = gas.render_markdown(self.result, seed=gas.DEFAULT_SEED, count=gas.DEFAULT_SAMPLES)
        for needle in ("归属正确率", "无归属", "字段完整率（参考，不作验收线）", "悬空标注"):
            self.assertIn(needle, text)
        self.assertIn(f"{self.result['acceptance']['matched']}/{self.result['acceptance']['denominator']}",
                      text)


# --------------------------------------------------------------------------- #
# 六、R4-1 · D-6 附加：创建接口用例（幂等 / 并发 / 非法 agent_id / MAX_AGENTS 边界）
# --------------------------------------------------------------------------- #

class D6CreateInterfaceTests(GroupCase):
    """`GroupService.create` 这个**创建接口本体**的三组用例（D-6：成功率 100%）。

    口径如实写明（HTTP 面实探：``.scratch/r41/probe_group_routes.py``）：groups 域挂着的
    HTTP 入口只有 ``GET /api/groups``（T18）、``GET /api/groups/<id>``（T19）、
    ``POST /api/groups/<id>/share``（实测 403 SHARE_FORBIDDEN，非组长被拒）；
    ``POST /api/groups``（建组）与 ``POST /api/groups/<id>/close``（关组）都是
    **404 ROUTE_NOT_FOUND** —— 建组**没有 HTTP 入口**，`GroupService.create` 就是唯一入口。
    所以这三组用例打在 create 本体上，**不冒充**「HTTP 端点用例」；本轮零新路由（铁律 1）。
    """

    def ledger(self) -> list[dict]:
        return ledger_rows(self.paths)

    # -- 1) 重复请求幂等 ----------------------------------------------------
    def test_duplicate_create_conflicts_and_writes_exactly_one_row(self) -> None:
        first = self.service.create(title="发布检查", agent_ids=["agent-a", "agent-b"],
                                   objective="同一份请求重放", group_id="grp_idem")
        with self.assertRaises(GroupError) as caught:
            self.service.create(title="发布检查", agent_ids=["agent-a", "agent-b"],
                               objective="同一份请求重放", group_id="grp_idem")
        self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID")
        self.assertEqual(caught.exception.field, "group_id")
        # 首行一字未改、逻辑实体只有一个、落盘只有一行：重复请求没留下第二行
        self.assertEqual(self.service.get("grp_idem"), first)
        self.assertEqual(len(self.service.list()), 1)
        rows = self.ledger()
        self.assertEqual(len(rows), 1, "重放留下了第二行")
        self.assertEqual(rows[0]["item"], first)
        # 幂等口径（写死，不许各自解释）：重放 = **冲突**，不是"200 返回同一实体"
        with self.assertRaises(GroupError):
            self.service.create(title="发布检查", agent_ids=["agent-a"], group_id="grp_idem")
        self.assertEqual(len(self.ledger()), 1)

    def test_idempotent_replay_survives_a_reload(self) -> None:
        self.service.create(title="重放核对", agent_ids=["agent-a"], group_id="grp_replay")
        for _ in range(3):
            with self.assertRaises(GroupError):
                self.service.create(title="重放核对", agent_ids=["agent-a"], group_id="grp_replay")
        reloaded = self.make_service()
        self.assertEqual(len(reloaded.list()), 1)
        self.assertEqual(reloaded.get("grp_replay")["agent_ids"], ["agent-a"])
        self.assertEqual(len(self.ledger()), 1)

    # -- 2) 并发创建 --------------------------------------------------------
    def test_concurrent_create_all_succeed_with_no_loss_no_duplicate(self) -> None:
        threads, per_call = 8, 5

        def burst(index: int) -> list[str]:
            return [self.service.create(title=f"并发-{index}-{tail}",
                                        agent_ids=[f"agent-a{index}"])["id"]
                    for tail in range(per_call)]

        with ThreadPoolExecutor(max_workers=threads) as pool:
            chunks = list(pool.map(burst, range(threads)))   # 异常会在这里抛回来
        created = [group_id for chunk in chunks for group_id in chunk]
        self.assertEqual(len(created), threads * per_call, "并发建组有调用失败 = 成功率不到 100%")
        self.assertEqual(len(set(created)), threads * per_call, "并发建组出现重复 id")
        self.assertEqual(len(self.service.list()), threads * per_call)
        reloaded = self.make_service()   # 落盘重读：内存里的 40 条真在盘上
        self.assertEqual({group["id"] for group in reloaded.list()}, set(created))
        self.assertEqual(len(self.ledger()), threads * per_call)

    def test_concurrent_same_id_keeps_exactly_one_entity(self) -> None:
        def worker(index: int) -> tuple[str, str]:
            try:
                return ("ok", self.service.create(title="并发同 id", agent_ids=["agent-a"],
                                                 group_id="grp_race")["id"])
            except GroupError as exc:
                return ("conflict", exc.code)

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(worker, range(6)))
        # 每个调用的结局只有两种：建成（同一个 id）或显式冲突，绝不出现第三种半成品
        self.assertTrue(all(kind == "ok" and group_id == "grp_race"
                            or kind == "conflict" and group_id == "GROUP_FIELD_INVALID"
                            for kind, group_id in outcomes), outcomes)
        winners = [item for item in outcomes if item[0] == "ok"]
        self.assertGreaterEqual(len(winners), 1, "六个线程全被判冲突 = 建不出来")
        # 逻辑实体恒为一个（同 id 后读胜出），且每个成功调用恰好追加一行
        entities = [group for group in self.service.list() if group["id"] == "grp_race"]
        self.assertEqual(len(entities), 1)
        rows = [row for row in self.ledger() if row["item"]["id"] == "grp_race"]
        self.assertEqual(len(rows), len(winners), "成功调用与追加行数不等：有写入丢失或多写")
        self.assertEqual({canonical_json(row["item"]) for row in rows},
                         {canonical_json(entities[0])}, "并发写留下了内容不一致的同 id 行")
        self.assertEqual(len(self.service.list()), 1)

    # -- 3) 非法 agent_id 拒绝（成功率 100%）-------------------------------
    ILLEGAL_AGENT_IDS = (
        ([], "空数组"),
        ("agent-a", "agent_ids 是字符串不是数组"),
        ({"id": "agent-a"}, "agent_ids 是对象不是数组"),
        (None, "agent_ids 缺失"),
        (5, "agent_ids 是数字"),
        ([""], "数组里是空字符串"),
        (["   "], "数组里是纯空白"),
        ([None], "数组里是 null"),
        ([123], "数组里是数字"),
        ([{"name": "x"}], "对象里没有 id / agent_id"),
        ([{"id": "  "}], "对象里的 id 是空白"),
        ([["agent-a"]], "数组里嵌数组"),
        (["agent-a", ""], "合法项后面跟着空串"),
        ([True], "数组里是布尔"),
    )

    def test_every_illegal_agent_id_is_rejected(self) -> None:
        rejections = 0
        for raw, why in self.ILLEGAL_AGENT_IDS:
            with self.subTest(case=why):
                with self.assertRaises(GroupError) as caught:
                    self.service.create(title="非法成员", agent_ids=raw)
                self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID", why)
                self.assertEqual(caught.exception.field, "agent_ids", why)
                rejections += 1
        self.assertEqual(rejections, len(self.ILLEGAL_AGENT_IDS),
                         "非法 agent_id 拒绝率必须 100%")
        self.assertEqual(self.service.list(), [], "非法请求留下了落盘痕迹")
        self.assertEqual(self.ledger(), [], "非法请求写了账本行")

    def test_illegal_title_and_group_id_are_rejected_too(self) -> None:
        for kwargs, why in (
            ({"title": "  ", "agent_ids": ["a"]}, "空标题"),
            ({"title": 5, "agent_ids": ["a"]}, "标题不是字符串"),
            ({"title": "T", "agent_ids": ["a"], "group_id": "  "}, "group_id 是空白"),
        ):
            with self.subTest(case=why):
                with self.assertRaises(GroupError) as caught:
                    self.service.create(**kwargs)
                self.assertEqual(caught.exception.code, "GROUP_FIELD_INVALID", why)
        self.service.create(title="已存在", agent_ids=["a"], group_id="grp_exists")
        with self.assertRaises(GroupError) as caught:
            self.service.create(title="撞号", agent_ids=["b"], group_id="grp_exists")
        self.assertEqual(caught.exception.detail, {"group_id": "grp_exists"})
        self.assertEqual(len(self.service.list()), 1)

    def test_create_success_rate_is_100_percent_over_100_calls(self) -> None:
        attempts, successes = 100, 0
        for index in range(attempts):
            try:
                if index % 2:
                    self.service.create(title=f"批量-{index}", agent_ids=[f"agent-{index % 7}"],
                                        group_id=f"grp-batch-{index:03d}")
                else:
                    self.service.create(title=f"批量-{index}",
                                        agent_ids=[f"agent-{index % 7}", f"agent-{index % 7}"])
                successes += 1
            except GroupError:
                pass
        self.assertEqual(successes, attempts, "D-6：创建接口成功率必须 100%")
        self.assertEqual(len(self.service.list()), attempts)
        self.assertEqual(len(self.make_service().list()), attempts, "落盘条数与内存不等")
        self.assertEqual(len(self.ledger()), attempts)

    # -- 4) MAX_AGENTS 边界（钉住 =10）------------------------------------
    def test_max_agents_boundary_is_pinned_at_ten(self) -> None:
        self.assertEqual(MAX_AGENTS, 10, "B4-2 的 10 Agent 上限被改动：REQ-004 口径要重裁")
        ten = self.service.create(title="十人组",
                                 agent_ids=[f"agent-{index}" for index in range(1, MAX_AGENTS + 1)])
        self.assertEqual(len(ten["agent_ids"]), MAX_AGENTS)
        # 去重**之后**才比上限：11 项里有 1 项重复 → 仍是 10 个成员，合法
        dedup = self.service.create(title="去重后十人",
                                    agent_ids=[f"agent-{index}" for index in range(1, MAX_AGENTS + 1)]
                                    + ["agent-1"])
        self.assertEqual(len(dedup["agent_ids"]), MAX_AGENTS)
        with self.assertRaises(GroupError) as caught:
            self.service.create(title="十一人",
                                agent_ids=[f"agent-{index}" for index in range(1, MAX_AGENTS + 2)])
        self.assertEqual(caught.exception.code, "GROUP_AGENTS_EXCEEDED")
        self.assertEqual(caught.exception.detail, {"requested": MAX_AGENTS + 1, "max": MAX_AGENTS})
        # 超限不截断、不落盘：既不悄悄丢 Agent，也不留半条组
        self.assertEqual([group["id"] for group in self.service.list()],
                         [ten["id"], dedup["id"]])
        self.assertEqual(len(self.ledger()), 2)


if __name__ == "__main__":
    unittest.main()
