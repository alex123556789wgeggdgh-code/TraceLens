"""R7-1（REQ-007 B7-1）验收测试：自动化建议的**取数接线**（observe/scan ← S0-1 干预事件）。

运行（全程离线，只用标准库；HTTP 只走 127.0.0.1 环回）：
    cd agent-explainer
    python -m unittest tests.test_suggestions_wiring tests.test_intervene_api -v

钉住的口径（PM 裁决 R7-1 §2，落 docs/07 §11.13）：

1. **数据源 = S0-1 运行时干预事件**（``action_type="intervention"`` +
   ``source.adapter="tracelens-intervention"`` 的独立签名记录），
   ``modified_params`` 从那条记录的决定段取；**导入侧的 ``user_intervention`` 不在口径里**。
2. **同一 (tool, param) 修改 ≥3 次（时间序同向）→ 第 3 次之后产出建议**（第 4 次之前就能弹）；
   方向反复 / 枚举换值 / 跨类型 → 单调性破坏，同向段从 1 重新开始。
3. 边界逐条：3 次同向第 3 次后产出、方向反复不计数、3 次同向后第 4 次前 dismiss → 7 天冷却
   不再弹、不同 user 隔离、``modified_params`` 缺失不计数、跨类型不聚合、
   事件缺 actor/target 不静默（登记原因码）、``set_enabled(false)`` 全链路静默、
   ``scan`` 幂等（occurrences 只增计数，重放不涨）。
4. 交付止于「建议对象产出 + 既有端点（T28 GET /api/suggestions）可查」——
   **R7-1 本轮零新增 HTTP 路由**（当时 T29/T30/T31 的 POST 仍未挂载，如实登记现状）。
   **R7-2 已把写侧挂上**（PM-VERDICT-R7-1 §3.1 预批的 L1）：现状钉测改在
   ``test_write_side_mount_is_the_declared_shape``，挂载矩阵在
   ``tests/test_suggestions_mount_http.py``。

临时目录一律 ``tests/scratch.py``（PM-VERDICT-R1-2a §3.3）；HTTP 断言级不重试（``http_fixture``）。
"""
from __future__ import annotations

import copy
import http.client
import json
import pathlib
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import intervention_history as ih  # noqa: E402
from agent_explainer import server as sv  # noqa: E402
from agent_explainer import suggestions as sg  # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 连接级瞬态补一次，断言级不重试
from scratch import scratch_dir, use_scratch_tempfile  # noqa: E402

NATIVE = json.loads((ROOT / "samples" / "action_records.json")
                    .read_text(encoding="utf-8"))["records"]
CONTRACT = json.loads((ROOT / "docs" / "api_contract.json").read_text(encoding="utf-8"))
BASE_TARGET = "act-demo-0005"          # filesystem.remove：max_delete_count / move_to_trash
TOOL = "filesystem.remove"
FIXED_NOW = "2026-11-12T00:00:00Z"     # 固定时钟：冷却窗口可复算
IV_PREFIX = "act-r7w-"                  # 本文件独占的夹具记录 id 前缀


def target(idx: int) -> dict:
    """一条**可决**的目标记录（演示夹具的副本，换 id；决定段清空）。"""
    rec = copy.deepcopy(next(r for r in NATIVE if r["id"] == BASE_TARGET))
    rec["id"] = f"{IV_PREFIX}{idx:02d}"
    rec["sequence_no"] = 900 + idx
    rec["status"] = "pending_confirmation"
    rec["requires_action"] = True
    rec["user_intervention"] = None
    rec["execution_receipt"] = {"state": "unreported", "reported_by": None,
                                "reported_at": None, "detail": "夹具：无回执",
                                "sdk_version": None,
                                "effect_notice": "intervention.effect.recorded_only"}
    return rec


def event(param: str, before, after, *, idx: int = 1, actor: str = "local-user",
          extra: dict | None = None) -> dict:
    """用 **S0-1 真实构造器**造一条干预事件（形状 = 运行时落盘的那条，不手搓）。

    路径就是产品路径：``apply_decision``（决定段）→ ``build_intervention_record``
    （独立签名记录的未签名形状）。
    """
    decided = sv.apply_decision(target(idx), "modify",
                                modified_params={param: after, **(extra or {})},
                                actor_id=actor, actor_role="local")
    return ih.build_intervention_record(decided, decision="modify", actor_id=actor,
                                        actor_role="local", request_id="req-fixture")


def event_with_fields(fields: list[dict], *, idx: int = 1,
                      actor: str = "local-user") -> dict:
    """把决定段的 ``modified_fields`` 换成给定形状：模拟适配器/上游给的事件（跨类型、
    时间型、缺 before 这些夹具没法通过 ``apply_decision`` 的参数校验，只能照形状给）。"""
    rec = event("max_delete_count", 12, 6, idx=idx, actor=actor)
    rec["user_intervention"]["modified_fields"] = copy.deepcopy(fields)
    rec["user_intervention"]["modified_params"] = {
        str(f.get("name")): f.get("after") for f in fields if f.get("name")}
    return rec


def engine(root: pathlib.Path) -> sg.SuggestionService:
    paths = FeaturePaths(root / "features")
    paths.ensure()
    return sg.SuggestionService(paths, now=lambda: FIXED_NOW)


def rows_of(service: sg.SuggestionService, *, user: str, tool: str = TOOL,
            param: str | None = None) -> list[dict]:
    return [r for r in service.list(user_id=user)
            if r.get("tool") == tool and (param is None or r.get("param") == param)]


def enough(rows: list[dict]) -> list[dict]:
    """够数（``occurrences ≥ threshold``）的行才算"建议已产出"。

    ``list()`` 是 T28 的读侧，按既有契约返回**未达阈值的账目**（在数到第几步一目了然），
    R7-1 不改这个形状（弹不弹的阈值判断归 R7-2 的界面）。所以"没到阈值不该弹"
    在测试里按 occurrences 判，不去要求 ``list()`` 撒一个"只有够数的行"的谎。
    """
    return [r for r in rows if int(r.get("occurrences") or 0) >= sg.DEFAULT_THRESHOLD]


def http_request(host: str, port: int, method: str, path: str, body=None,
                 timeout: float = 8.0) -> tuple[int, dict, object]:
    """一发一收（连接生命周期交给 tests/http_fixture；非幂等动词绝不重放）。"""
    def once(conn: http.client.HTTPConnection) -> tuple[int, dict, object]:
        payload = body
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        headers = {"Connection": "close"}
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        heads = {str(k).lower(): v for k, v in response.getheaders()}
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = raw.decode("utf-8", "replace")
        return response.status, heads, data

    return fx.with_connection(host, port, once, verb=method, path=path, timeout=timeout)


# --------------------------------------------------------------------------- #
# A. 取数口径：intervention_event_view 的形状与登记（纯函数，零 IO）
# --------------------------------------------------------------------------- #

class InterventionEventViewTests(unittest.TestCase):
    """§2.1 取数口径：从**独立干预记录**的决定段还原"改过什么"，缺什么就登记什么。"""

    def test_view_reads_the_intervention_record_and_judges_direction(self) -> None:
        view = sg.intervention_event_view(event("max_delete_count", 12, 6))
        self.assertEqual(TOOL, view["tool"])
        self.assertEqual("local-user", view["actor"])
        self.assertEqual(f"{IV_PREFIX}01", view["target"],
                         "target = 被干预记录（parent_action_id），不是干预记录自己")
        self.assertTrue(view["event_id"], "事件 id = 那条干预记录的 id（幂等锚点）")
        self.assertEqual([], view["skips"])
        self.assertEqual([{"param": "max_delete_count", "before": 12, "after": 6,
                           "direction": "decrease"}], view["events"])

    def test_view_falls_back_to_params_diff_and_skips_unchanged(self) -> None:
        """只有 schema C3 的两个对象时，用 modified_params 与 original_params 的差集取数；
        传了但没改的键不算"修改"，不计数。"""
        rec = event("max_delete_count", 12, 6)
        rec["user_intervention"].pop("modified_fields")
        rec["user_intervention"]["modified_params"] = {"max_delete_count": 6,
                                                      "move_to_trash": True}
        rec["user_intervention"]["original_params"] = {"max_delete_count": 12,
                                                       "move_to_trash": True}
        view = sg.intervention_event_view(rec)
        self.assertEqual(["max_delete_count"], [e["param"] for e in view["events"]])
        self.assertEqual([], view["skips"])

    def test_view_never_invents_a_before_value(self) -> None:
        """original_params 里没有这个键 → 不编造原值：登记 no_value_pair，不计数。"""
        rec = event("max_delete_count", 12, 6)
        rec["user_intervention"].pop("modified_fields")
        rec["user_intervention"]["modified_params"] = {"keep_days": 3}
        rec["user_intervention"]["original_params"] = {"max_delete_count": 12}
        view = sg.intervention_event_view(rec)
        self.assertEqual([], view["events"])
        self.assertIn("no_value_pair", view["skips"])

    def test_view_registers_missing_target_and_actor_and_decision(self) -> None:
        """§2.3：事件缺 target / 缺 actor / 不是 modify 都不静默——登记原因码。"""
        no_target = event("max_delete_count", 12, 6)
        no_target.pop("parent_action_id")
        v1 = sg.intervention_event_view(no_target)
        self.assertIn("missing_target", v1["skips"])
        self.assertEqual([], v1["events"], "没有目标记录的「干预」不计数")

        no_actor = event("max_delete_count", 12, 6)
        no_actor.pop("actor_id")
        no_actor["user_intervention"].pop("decided_by")
        v2 = sg.intervention_event_view(no_actor)
        self.assertIn("missing_actor", v2["skips"])
        self.assertEqual(1, len(v2["events"]), "缺决定人仍按既有 local 缺省计数，不丢数据")

        approve = sv.apply_decision(target(2), "approve", actor_id="local-user")
        v3 = sg.intervention_event_view(ih.build_intervention_record(
            approve, decision="approve", actor_id="local-user", actor_role="local"))
        self.assertIn("not_modify", v3["skips"])
        self.assertEqual([], v3["events"], "approve/reject 没有可学习的参数方向")

    def test_view_registers_missing_changed_params(self) -> None:
        """§2.3：modified_params 缺失的干预不计数，但必须留下登记。"""
        rec = event("max_delete_count", 12, 6)
        rec["user_intervention"]["modified_fields"] = []
        rec["user_intervention"]["modified_params"] = {}
        view = sg.intervention_event_view(rec)
        self.assertEqual([], view["events"])
        self.assertIn("no_changed_params", view["skips"])

    def test_view_shape_is_the_reusable_contract(self) -> None:
        """§2.5：取数口径写成可复用形状（REQ-006/R6 预聚合消费同一个出口，不二次实现）。"""
        view = sg.intervention_event_view(event("max_delete_count", 12, 6))
        self.assertEqual({"tool", "actor", "target", "event_id", "events", "skips"},
                         set(view.keys()))
        self.assertEqual({"param", "before", "after", "direction"}, set(view["events"][0]))
        self.assertIn(sg.INTERVENTION_EVENT_ADAPTER, ("tracelens-intervention",))
        self.assertEqual(sg.INTERVENTION_EVENT_ADAPTER, ih.INTERVENTION_ADAPTER,
                         "落款常量只有一处真相，不在建议模块里另立一套")
        for reason in sg.OBSERVE_SKIP_REASONS:
            self.assertIsInstance(reason, str)


# --------------------------------------------------------------------------- #
# B. 引擎侧接线：阈值 / 方向 / 冷却 / 隔离 / 幂等 / 开关
# --------------------------------------------------------------------------- #

class SuggestionWiringEngineTests(unittest.TestCase):
    """§2.2/§2.3 方向判定与阈值：事件从 ``observe_intervention`` 进，规则在引擎里落。"""

    def setUp(self) -> None:
        self.root = scratch_dir("suggestions-wiring-engine")
        self.service = engine(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def feed(self, values, *, param: str = "max_delete_count", user: str = "u-a",
             start: int = 1) -> list[dict | None]:
        """把 ``(before, after)`` 序列逐条喂进引擎。

        决定段照形状给（``event_with_fields``）：方向规则测试不该被 ``apply_decision``
        的参数校验绑住手脚——事件本身仍是 S0-1 构造器的产物。
        """
        out: list[dict | None] = []
        for i, (before, after) in enumerate(values):
            rec = event_with_fields([{"name": param, "before": before, "after": after}],
                                    idx=start + i)
            out.append(self.service.observe_intervention(rec, user_id=user))
        return out

    def test_three_same_direction_events_produce_on_the_third(self) -> None:
        """3 次同向：前两次只累加，第 3 次之后产出建议（threshold=3）。"""
        hits = self.feed([(12, 10), (12, 8), (12, 6)])
        self.assertEqual([None, None], hits[:2], "没到阈值就弹 = 把噪声当建议")
        self.assertIsNotNone(hits[2], "第 3 次同向必须产出建议对象")
        self.assertEqual(3, hits[2]["occurrences"])
        self.assertEqual("decrease", hits[2]["direction"])
        self.assertEqual("open", hits[2]["status"])
        self.assertEqual(1, len(rows_of(self.service, user="u-a")))

    def test_equal_steps_still_count_as_monotone_non_strict(self) -> None:
        """数值型＝时间序单调（**非严格**）同向：步子一样大（12→8, 12→8）也算同向。"""
        hits = self.feed([(12, 8), (12, 8), (12, 8)])
        self.assertIsNotNone(hits[2], hits)
        self.assertEqual(3, hits[2]["occurrences"])

    def test_direction_flip_breaks_the_run_and_yields_nothing(self) -> None:
        """方向反复 → 不计数（单调性破坏即重置），三次里有一次反向就不产建议。"""
        hits = self.feed([(12, 10), (12, 8), (12, 9)])
        self.assertEqual([None, None, None], hits)
        rows = rows_of(self.service, user="u-a")
        self.assertEqual(1, len(rows), "同键只有一条建议账目，不产生第二条")
        self.assertEqual(1, rows[0]["occurrences"], "同向段必须从 1 重新开始")
        self.assertIn("run_break", self.service.observe_skips())

    def test_reverse_run_increases_from_one_after_break(self) -> None:
        """先降后升：升段自己从 1 起算，自己满 3 次才产建议（不被降段凑数、也不被压死）。"""
        hits = self.feed([(12, 10), (12, 8), (12, 6), (6, 9), (6, 11), (6, 12)])
        self.assertEqual([None, None], hits[:2])
        self.assertIsNotNone(hits[2], "降满 3 次产出建议")
        self.assertEqual(3, hits[2]["occurrences"])
        self.assertEqual([None, None], hits[3:5], "升段前两次不产建议（同向段从 1 重新开始）")
        self.assertIsNotNone(hits[5], "升段自己满 3 次，同样产建议")
        self.assertEqual(3, hits[5]["occurrences"])
        self.assertEqual(1, self.service.observe_skips().get("run_break"),
                         "方向翻转只登记一次 run_break")

    def test_enum_converges_to_the_same_value(self) -> None:
        """枚举型＝收敛到同一值：反复改成同一个值算同向，换第二个值即破坏。"""
        hits = self.feed([(False, True)] * 3, param="move_to_trash")
        self.assertEqual([None, None], hits[:2])
        self.assertIsNotNone(hits[2], "枚举收敛到同一值应产建议")
        self.assertEqual(3, hits[2]["occurrences"])
        rows = rows_of(self.service, user="u-a", param="move_to_trash")
        self.assertEqual(1, len(rows))
        self.assertEqual(True, rows[0]["to_value"])
        flip = self.feed([(True, False)], param="move_to_trash", start=9)
        self.assertIsNone(flip[0])
        self.assertEqual(1, rows_of(self.service, user="u-a",
                                    param="move_to_trash")[0]["occurrences"],
                         "换成另一个值 = 收敛段破坏，从 1 重新开始")
        self.assertIn("run_break", self.service.observe_skips())

    def test_cross_type_param_does_not_aggregate(self) -> None:
        """跨类型参数不聚合：同一 (tool, param) 一会儿数字一会儿字符串 → 不合并计数。"""
        recs = [event_with_fields([{"name": "mode", "before": 3, "after": 1}]),
                event_with_fields([{"name": "mode", "before": 3, "after": 1}]),
                event_with_fields([{"name": "mode", "before": "auto", "after": "manual"}])]
        hits = [self.service.observe_intervention(r, user_id="u-a") for r in recs]
        self.assertEqual([None, None, None], hits,
                         "跨类型第 3 次不该把前两次算成同向")
        self.assertEqual(1, rows_of(self.service, user="u-a", param="mode")[0]["occurrences"])

    def test_time_like_values_aggregate_by_convergence(self) -> None:
        """时间型＝偏移符号一致：现有 ``_direction`` 判不出 ISO 串的偏移（非数值 → set），
        所以按 set 的收敛口径落——**不假装能判时间偏移**（docs/07 §11.13 的诚实边界）。"""
        iso = ["2026-11-09T09:31:03+08:00", "2026-11-09T10:31:03+08:00"]
        hits = [self.service.observe_intervention(
            event_with_fields([{"name": "action_due_at", "before": iso[0], "after": iso[1]}],
                              idx=i), user_id="u-a") for i in (1, 2, 3)]
        self.assertIsNotNone(hits[2])
        self.assertEqual("set", hits[2]["direction"])
        self.assertEqual(3, hits[2]["occurrences"])
        back = self.service.observe_intervention(
            event_with_fields([{"name": "action_due_at", "before": iso[1],
                                "after": "2026-11-09T08:00:00+08:00"}], idx=4),
            user_id="u-a")
        self.assertIsNone(back, "换成另一个时间值 = 收敛段破坏，从 1 重新开始")

    def test_missing_params_and_missing_target_are_registered_not_counted(self) -> None:
        """§2.3 边界：modified_params 缺失、缺 target → 不计数 + 留登记（不静默漏算）。"""
        empty = event("max_delete_count", 12, 6)
        empty["user_intervention"]["modified_fields"] = []
        empty["user_intervention"]["modified_params"] = {}
        no_target = event("max_delete_count", 12, 6)
        no_target.pop("parent_action_id")
        self.assertIsNone(self.service.observe_intervention(empty, user_id="u-a"))
        self.assertIsNone(self.service.observe_intervention(no_target, user_id="u-a"))
        skips = self.service.observe_skips()
        self.assertEqual(1, skips.get("no_changed_params"))
        self.assertEqual(1, skips.get("missing_target"))
        self.assertEqual([], rows_of(self.service, user="u-a"))
        self.assertEqual(0, self.service.facts()["suggestions"],
                         "两条都没计数的登记必须可查，且账本里零建议")

    def test_dismiss_before_the_fourth_event_silences_for_seven_days(self) -> None:
        """3 次同向后、第 4 次之前 dismiss → 7 天冷却期内不再弹。"""
        self.feed([(12, 10), (12, 8), (12, 6)])
        row = rows_of(self.service, user="u-a")[0]
        dismissed = self.service.dismiss(row["id"], user_id="u-a", reason="先观察")
        self.assertEqual("dismissed", dismissed["status"])
        self.assertEqual("2026-11-19T00:00:00Z", dismissed["cooldown_until"],
                         "冷却 = 7 天后（COOLDOWN_DAYS 是契约字段）")
        self.assertIsNone(self.feed([(12, 4)], start=4)[0], "冷却期内不弹")
        self.assertEqual(1, self.service.observe_skips().get("cooldown"))

    def test_users_are_isolated(self) -> None:
        """不同 user 隔离：各 2 次同向不产建议，A 的 3 次不算在 B 名下。"""
        self.feed([(12, 10), (12, 8)], user="u-a")
        self.feed([(12, 10), (12, 8)], user="u-b")
        self.assertEqual([], enough(rows_of(self.service, user="u-a")), "各 2 次不产建议")
        self.assertEqual([], enough(rows_of(self.service, user="u-b")))
        self.assertIsNotNone(self.feed([(12, 6)], user="u-a")[0])
        self.assertEqual(1, len(enough(rows_of(self.service, user="u-a"))))
        self.assertEqual([], enough(rows_of(self.service, user="u-b")),
                         "B 的名下不得出现 A 攒出来的建议")
        self.assertEqual(2, self.service.facts()["suggestions"],
                         "同一 (tool, param) 在两个 user 下是两条独立账目")

    def test_scan_is_idempotent_on_event_ids(self) -> None:
        """scan 幂等：重放同一批事件 occurrences 不涨、touched 归 0（只增计数不重复产）。"""
        batch = [event("max_delete_count", 12, n, idx=i) for i, n in ((1, 10), (2, 8), (3, 6))]
        first = self.service.scan(batch, user_id="u-a")
        self.assertEqual(3, first["scanned"])
        self.assertEqual(1, first["suggestions_touched"])
        self.assertEqual(3, rows_of(self.service, user="u-a")[0]["occurrences"])
        again = self.service.scan(batch, user_id="u-a")
        self.assertEqual(0, again["suggestions_touched"])
        self.assertEqual(3, rows_of(self.service, user="u-a")[0]["occurrences"],
                         "重放不得把 occurrences 抬成 6")
        self.assertEqual(1, len(rows_of(self.service, user="u-a")), "账本里只有一条建议")
        self.assertEqual(3, self.service.observe_skips().get("duplicate_event"))

    def test_scan_does_not_consume_import_side_user_intervention(self) -> None:
        """§2.1：导入侧的 user_intervention 不是事件流 → scan 不计数，登记 non_event。"""
        imported = copy.deepcopy(NATIVE)          # 演示夹具：带历史决定段的导入记录
        result = self.service.scan(imported, user_id="u-a")
        self.assertEqual(0, result["suggestions_touched"])
        self.assertEqual(len(imported), result["non_event_records"])
        self.assertEqual(0, self.service.facts()["suggestions"])
        self.assertIn("not_intervention_event", self.service.observe_skips())

    def test_observe_keeps_the_decided_record_contract_and_routes_events(self) -> None:
        """docs/07 §4.11 的 ``observe(record, *, user_id)`` 契约不变：
        「已决定记录」照旧计数；S0-1 落款的记录自动路由到事件口径。"""
        decided = [{"id": f"act-legacy-{i}", "agent_id": "a", "action_type": "write",
                    "status": "modified", "timestamp": FIXED_NOW,
                    "tool_used": {"name": TOOL},
                    "user_intervention": {"decision": "modify", "decided_by": "u-a",
                                          "decided_at": FIXED_NOW,
                                          "modified_fields": [{"name": "max_delete_count",
                                                               "before": 12, "after": 6}]}}
                   for i in (1, 2, 3)]
        hits = [self.service.observe(r, user_id="u-a") for r in decided]
        self.assertEqual([None, None], hits[:2])
        self.assertEqual(3, hits[2]["occurrences"])
        routed = self.service.observe(event("max_delete_count", 12, 6), user_id="u-b")
        self.assertIsNone(routed)
        self.assertEqual(1, len(rows_of(self.service, user="u-b")),
                         "落款记录走事件口径，计在 u-b 名下（不污染 u-a 的账）")

    def test_disabled_engine_is_silent_across_the_whole_chain(self) -> None:
        """set_enabled(false) 全链路静默：零建议、零偏好、零登记，开关如实回读。"""
        self.service.set_enabled(False)
        self.assertFalse(self.service.facts()["enabled"])
        self.assertIsNone(self.service.observe_intervention(
            event("max_delete_count", 12, 6), user_id="u-a"))
        for i, n in ((1, 10), (2, 8), (3, 6)):
            self.assertIsNone(self.service.observe_intervention(
                event("max_delete_count", 12, n, idx=i), user_id="u-a"))
        self.service.scan([event("max_delete_count", 12, 5, idx=9)], user_id="u-a")
        self.assertEqual(0, self.service.facts()["suggestions"])
        self.assertEqual(0, self.service.facts()["preferences"])
        self.assertEqual(0, self.service.facts()["events_observed"])
        self.assertEqual({}, self.service.observe_skips(), "关掉的引擎不记账，不留登记")

    def test_accept_stops_counting_for_the_same_key(self) -> None:
        """已采纳的 (tool, param) 不再重复建议：accept 之后的事件登记 accepted。"""
        self.feed([(12, 10), (12, 8), (12, 6)])
        row = rows_of(self.service, user="u-a")[0]
        accepted = self.service.accept(row["id"], user_id="u-a")
        self.assertEqual("suggestion", accepted["preference"]["source"])
        self.assertIsNone(self.feed([(12, 4)], start=5)[0])
        self.assertEqual(1, self.service.observe_skips().get("accepted"))

    def test_wiring_never_mutates_the_intervention_record(self) -> None:
        """铁律 2/3 复述：建议是读侧账本，绝不在干预记录上追加字段。"""
        rec = event("max_delete_count", 12, 6)
        snapshot = copy.deepcopy(rec)
        self.service.observe_intervention(rec, user_id="u-a")
        self.assertEqual(snapshot, rec, "observe 不得就地改写事件（入参只读）")
        self.assertNotIn("improvement_suggestion", rec)
        self.assertNotIn("intervention_history", rec)

    def test_module_self_check_runs_green(self) -> None:
        """模块自检（阈值→接受→冷却→方向破坏→幂等）跑通；临时目录走 scratch 替身。"""
        with use_scratch_tempfile():
            self.assertEqual(0, sg._self_check())


# --------------------------------------------------------------------------- #
# C. 端到端：/intervene → 建议 → GET /api/suggestions（产品口径的签名服务器）
# --------------------------------------------------------------------------- #

class SuggestionsWiringHttpTests(unittest.TestCase):
    """交付止点（§2.4）：建议对象产出 + 既有端点可查；零新路由、零改写历史。

    每个用例一台**全新的产品口径服务器**（签名入库 + 持久 store + 独立 features 账本）：
    建议按 ``(user, tool, param)`` 聚合，共用一台服务器会让用例互相攒次数，
    所以这里要的是隔离，不是省那几秒启动时间。
    """

    def setUp(self) -> None:
        case = self.id().rsplit(".", 1)[-1]
        self.root = scratch_dir(f"suggestions-wiring-http-{case}")
        records = copy.deepcopy(NATIVE) + [target(i) for i in range(1, 16)]
        self.server, self.thread = sv.start_background_server(
            "127.0.0.1", 0, records=records, quiet=True,
            records_store_path=self.root / "records.jsonl",
            features_root=self.root / "features",
            audit_log_path=self.root / "audit.jsonl",
            ingest_token_path=self.root / "ingest.json",
            settings_path=self.root / "settings.json",
            auth_state_path=self.root / "auth.json",
            sign_on_write=True)
        self.host, self.port = self.server.server_address[:2]
        self.service = self.server.service

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.root, ignore_errors=True)

    def post(self, path: str, body: dict) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "POST", path, body)

    def get(self, path: str) -> tuple[int, dict, object]:
        return http_request(self.host, self.port, "GET", path)

    def modify(self, idx: int, value) -> tuple[int, dict, object]:
        return self.post("/intervene", {"action_id": f"{IV_PREFIX}{idx:02d}",
                                        "decision": "modify",
                                        "modified_params": {"max_delete_count": value}})

    def payload(self) -> dict:
        status, _hd, data = self.get("/api/suggestions")
        self.assertEqual(200, status, data)
        self.assertTrue(data["ok"])
        for key in ("suggestions", "count", "enabled", "threshold", "cooldown_days"):
            self.assertIn(key, data, "T28 的响应形状是契约，接线不得改动它")
        self.assertEqual(3, data["threshold"])
        self.assertEqual(7, data["cooldown_days"])
        return data

    def rows(self, *, param: str = "max_delete_count") -> list[dict]:
        return [r for r in self.payload()["suggestions"]
                if r.get("tool") == TOOL and r.get("param") == param]

    def stored_records(self) -> list[dict]:
        status, _hd, data = self.get("/api/records")
        self.assertEqual(200, status, data)
        return data["records"]

    def test_three_modify_interventions_feed_the_suggestions_endpoint(self) -> None:
        """端到端正题：3 次 modify 干预 → T28 GET 查得到建议对象（方向/次数/取值齐）。"""
        for i, value in ((1, 10), (2, 8), (3, 6)):
            status, _hd, data = self.modify(i, value)
            self.assertEqual(200, status, data)
        rows = self.rows()
        self.assertEqual(1, len(rows), "第 3 次同向必须产出建议对象，且既有端点查得到")
        row = rows[0]
        self.assertEqual(3, row["occurrences"])
        self.assertEqual("decrease", row["direction"])
        self.assertEqual(6, row["to_value"])
        self.assertEqual(12, row["from_value"])
        self.assertEqual("open", row["status"])
        self.assertTrue(str(row.get("user_id") or ""), "建议必须记在某个身份名下")
        self.assertEqual(3, len(row["events"]), "计数锚点 = 三条 S0-1 干预事件 id")
        self.assertEqual(3, self.service.suggestion_wiring_events)
        self.assertEqual(1, self.service.suggestion_wiring_created)
        self.assertEqual(0, self.service.suggestion_wiring_failures)
        self.assertEqual(3, self.service.intervention_records)

    def test_suggestion_is_visible_before_the_fourth_intervention(self) -> None:
        """「第 4 次之前可弹」：第 3 次之后端点就有，第 4 次只把 occurrences 抬到 4。"""
        for i, value in ((5, 10), (6, 8), (7, 6)):
            self.assertEqual(200, self.modify(i, value)[0])
        rows = self.rows()
        self.assertEqual(1, len(rows), "第 3 次之后、第 4 次之前就得弹（端点可查）")
        self.assertEqual(3, rows[0]["occurrences"])
        self.assertEqual("open", rows[0]["status"])
        self.assertEqual(200, self.modify(8, 4)[0])
        after = self.rows()
        self.assertEqual(1, len(after), "同键不产生第二条建议")
        self.assertEqual(4, after[0]["occurrences"])
        self.assertEqual("open", after[0]["status"])

    def test_idempotent_replay_counts_the_decision_once(self) -> None:
        """幂等重放（同 idempotency_key）绝不重复计数：一次决定 = 一次消费。"""
        key = "r7w-replay-1"
        first = self.post("/intervene", {"action_id": f"{IV_PREFIX}09",
                                         "decision": "modify",
                                         "modified_params": {"max_delete_count": 7},
                                         "idempotency_key": key})
        self.assertEqual(200, first[0], first[2])
        events_after_first = self.service.suggestion_wiring_events
        self.assertEqual(1, events_after_first)
        replay = self.post("/intervene", {"action_id": f"{IV_PREFIX}09",
                                          "decision": "modify",
                                          "modified_params": {"max_delete_count": 7},
                                          "idempotency_key": key})
        self.assertEqual(200, replay[0], replay[2])
        self.assertEqual("true", replay[1].get("x-intervene-replay"))
        self.assertEqual(events_after_first, self.service.suggestion_wiring_events,
                         "重放不得再消费一次事件")
        self.assertEqual(1, self.service.intervention_records,
                         "S0-1 的幂等口径同样不重复追加干预记录")

    def test_wiring_leaves_records_and_chain_intact(self) -> None:
        """铁律 1/2/3：干预记录照旧签名可验，被干预记录零追加字段，历史零改写。"""
        base = len(self.service.store.all())
        self.assertEqual(200, self.modify(10, 5)[0])
        stored = self.service.store.all()
        self.assertEqual(base + 1, len(stored), "一次干预 = 一条新增记录，不多不少")
        interventions = [r for r in stored if r.get("action_type") == "intervention"
                         and str(r.get("id", "")).startswith("act_iv_")]
        self.assertEqual(1, len(interventions))
        for rec in interventions:
            status, _hd, data = self.post("/api/secretariat/verify", {"record": rec})
            self.assertEqual(200, status, data)
            result = data["results"][0]
            self.assertEqual("valid", result["checks"]["signature"],
                             "干预事件记录入库即签名：接线不改签名、不改 body")
            self.assertIn(result["checks"]["prev_hash"], ("continuous", "genesis"))
            codes = {str(p.get("code")) for p in result["problems"]}
            self.assertTrue(codes <= {"SECRETARIAT_APPROVAL_REQUIRED"},
                            f"critical 夹具的审批缺口是 R4 登记的既有边界，接线不得新增问题：{codes}")
            self.assertEqual(5, rec["user_intervention"]["modified_params"]["max_delete_count"],
                             "取数面的决定段就是引擎读到的东西：不改写、不补写")
        # 中危目标：整条验签 ok=true —— 接线没有把任何记录弄坏
        self.assertEqual(200, self.post("/intervene", {"action_id": "act-demo-0004",
                                                       "decision": "modify",
                                                       "modified_params":
                                                           {"max_move_count": 120}})[0])
        medium = [r for r in self.service.store.all()
                  if r.get("action_type") == "intervention"
                  and r.get("parent_action_id") == "act-demo-0004"]
        self.assertEqual(1, len(medium))
        status, _hd, data = self.post("/api/secretariat/verify", {"record": medium[0]})
        self.assertEqual(200, status, data)
        result = data["results"][0]
        self.assertTrue(result["ok"], f"中危目标的干预记录必须整条验签通过：{result['problems']}")
        self.assertEqual([], result["problems"])
        for rec in stored:
            self.assertNotIn("intervention_history", rec, "铁律 3：该键永不落盘")
            self.assertNotIn("improvement_suggestion", rec, "建议不写进 ActionRecord")
        # 读侧照旧：S0-1 的读时组装不受接线影响（GET /api/records 里归组到被干预记录名下）
        parents = [r for r in self.stored_records() if r["id"] == f"{IV_PREFIX}10"]
        self.assertEqual(1, len(parents))
        history = parents[0].get("intervention_history") or []
        self.assertEqual(1, len(history), "那条干预事件仍归组在被干预记录的历史视图里")
        self.assertTrue(history[0]["signed"], "历史条目仍指向那条独立签名记录")
        self.assertEqual(0, self.service.intervention_record_failures)
        self.assertEqual(0, self.service.sign_failures)
        self.assertTrue(self.service.require_audit_chain()["ok"], "接线不得动审计链一根手指")

    def test_write_side_mount_is_the_declared_shape(self) -> None:
        """R7-1 的「零新增路由」钉测 → R7-2 挂载后的现状（口径变化在此登记）。

        R7-1 交付时 T29/T30/T31 未挂载，本用例钉的是 ``ROUTE_NOT_FOUND``。
        R7-2（PM-VERDICT-R7-1 §3.1 预批的 L1）把写侧挂上，现状改成：

        * 契约里新增**两条精确路径**的 POST 行（``/api/suggestions`` 开关、
          ``/api/preferences`` 手动偏好）；``/api/suggestions/<id>/accept|dismiss``
          经 ``_suggestion_route`` helper 挂载，**契约抽取看不到 helper 挂载**
          （T21/T23 先例：``test_share_auth.test_contract_surface_gains_no_new_route``），
          所以路由数只从 52 涨到 54，子路由在契约里仍不可见；
        * 打不存在的建议 → 既有码 ``SUGGESTION_NOT_FOUND``（404），不再是没有路由；
        * GET 侧那条 405 逐字保留（挂载不许顺手改掉读侧口径）。
        """
        routes = {(r["path"], r["verb"]) for r in CONTRACT["routes"]}
        self.assertIn(("/api/suggestions", "GET"), routes)
        self.assertIn(("/api/preferences", "GET"), routes)
        self.assertIn(("/api/suggestions", "POST"), routes, "开关专用入口必须进契约")
        self.assertIn(("/api/preferences", "POST"), routes, "T31 写侧必须进契约")
        self.assertEqual(61, len(CONTRACT["routes"]),
                         "0.6.0 L1 八功能接线后的基线：54 → 61（T13/T17/T18/T33 与证据族）")
        for path, _verb in routes:
            self.assertFalse(path.startswith("/api/suggestions/"),
                             "helper 挂载的 accept/dismiss 子路由在契约里必须不可见")
        # 实况：POST 写侧已挂上 → 打不存在的建议出既有码，不再出 ROUTE_NOT_FOUND
        status, _hd, data = self.post("/api/suggestions/sug-x/accept", {})
        self.assertEqual(404, status, data)
        self.assertEqual("SUGGESTION_NOT_FOUND", (data or {}).get("code"),
                         "挂上之后仍回答 ROUTE_NOT_FOUND 就是没挂上")
        status, _hd, data = self.post("/api/preferences", {"tool": TOOL,
                                                           "param": "max_delete_count",
                                                           "value": 6})
        self.assertEqual(201, status, data)
        self.assertEqual("manual", (data or {}).get("preference", {}).get("source"))
        # GET 侧 405 逐字保留
        status, _hd, data = self.get("/api/suggestions/sug-x/accept")
        self.assertEqual(405, status, data)
        self.assertEqual("accept/dismiss are POST only",
                         (data or {}).get("detail", {}).get("hint", ""))

    def test_engine_failure_never_breaks_the_decision(self) -> None:
        """建议是分析面：引擎抛异常时 /intervene 仍 200，决定已落盘，故障如实计数留证。"""
        engine_obj = self.service.suggestion_service
        original = engine_obj.observe_intervention

        def boom(record, *, user_id=None):
            raise RuntimeError("夹具：建议引擎炸了")

        engine_obj.observe_intervention = boom
        try:
            status, _hd, data = self.modify(11, 9)
        finally:
            engine_obj.observe_intervention = original
        self.assertEqual(200, status, data)
        self.assertEqual("modified", data["status"], "决定 ≠ 建议：引擎坏了也要把决定记下来")
        self.assertEqual(1, self.service.suggestion_wiring_failures)
        problem = self.service.suggestion_wiring_problem
        self.assertEqual("SUGGESTION_WIRING_FAILED", problem["code"])
        self.assertIn("夹具：建议引擎炸了", problem["reason"])
        self.assertTrue(problem["intervention_id"].startswith("act_iv_"))
        self.assertEqual(0, self.service.suggestion_wiring_events)
        self.assertEqual([], self.rows(), "引擎失败的这一次不伪造任何建议")

    def test_disabled_switch_silences_the_http_chain(self) -> None:
        """开关（R7-2 已挂 ``POST /api/suggestions {"enabled": bool}``）在引擎侧全链路静默：
        端点如实回读 enabled=false。HTTP 入口的形状钉在 ``test_suggestions_mount_http``。"""
        self.service.suggestion_service.set_enabled(False)
        try:
            for i, value in ((13, 10), (14, 8), (15, 6)):
                status, _hd, data = self.modify(i, value)
                self.assertEqual(200, status, data)
                self.assertEqual("modified", data["status"], "关建议绝不影响决定")
            data = self.payload()
            self.assertFalse(data["enabled"], "开关状态必须如实回读，不假绿")
            self.assertEqual(0, data["count"])
            self.assertEqual(0, self.service.suggestion_wiring_events,
                             "关掉之后连「消费」这个动作都不发生")
            self.assertEqual(0, self.service.suggestion_service.facts()["suggestions"])
            self.assertEqual(3, self.service.intervention_records,
                             "干预事件本身照常落盘：关掉的是分析面，不是事实")
        finally:
            self.service.suggestion_service.set_enabled(True)
        self.assertTrue(self.payload()["enabled"])

    def test_identity_follows_the_read_side_actor(self) -> None:
        """身份口径与 T28 读侧对齐：team 会话下计在 ``user_id`` 名下；
        决定段的 ``actor_id``（team 模式是 username）不参与聚合（遗留事项已登记）。"""
        session = {"user_id": "u-visible", "username": "boss", "role": "editor"}
        for i, value in ((5, 10), (6, 8), (7, 6)):
            self.service.intervene({"action_id": f"{IV_PREFIX}{i:02d}",
                                    "decision": "modify",
                                    "modified_params": {"max_delete_count": value}},
                                   request_id=f"r7w-id-{i}", session=session)
        mine = self.service.suggestions_payload(actor_id="u-visible")["suggestions"]
        mine = [r for r in mine if r.get("param") == "max_delete_count"]
        self.assertEqual(1, len(mine), "计在 user_id 名下，读侧同口径才查得到")
        self.assertEqual(3, mine[0]["occurrences"])
        by_username = [r for r in self.service.suggestions_payload(actor_id="boss")
                       ["suggestions"] if r.get("param") == "max_delete_count"]
        self.assertEqual([], by_username, "不得同时计在 username 名下（身份分裂只留一处）")
        decided = [r for r in self.service.store.all()
                   if r.get("action_type") == "intervention"
                   and r.get("parent_action_id") == f"{IV_PREFIX}05"]
        self.assertEqual(1, len(decided))
        self.assertEqual("boss", decided[0]["user_intervention"]["decided_by"],
                         "决定段仍记真实 actor（审计口径不变），只是不参与建议聚合")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
