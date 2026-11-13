"""``agent_explainer/groups.py`` 的单测（规格书功能四：多 Agent 任务聚合视图）。

运行（全程离线，只用标准库 unittest）：
    cd agent-explainer
    python -m unittest discover -s tests -p "test_groups.py" -v

覆盖点：建组 / 上限 / 去重 / 关闭 / 聚合排序 / 进度分区 / 资源与窗口 / 卡住判定 /
1000 条 2 秒计时 / 分组准确率 / 落盘重读 / 坏行容错 / 错误码。
数据目录一律 ``tempfile.TemporaryDirectory()``，绝不写仓库 ``var/``。
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - 正常运行已可导入
    sys.path.insert(0, str(ROOT))

from agent_explainer.feature_store import FeaturePaths  # noqa: E402
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
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = pathlib.Path(self._temp.name)
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
        self.assertEqual(groups_self_check(), 0)


if __name__ == "__main__":
    unittest.main()
