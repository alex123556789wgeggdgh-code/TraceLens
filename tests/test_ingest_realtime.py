"""t14 [4.4] 实时接入验收：webhook 鉴权 / 幂等 / 非法载荷、SSE 推送顺序、SDK 对本地 mock 服务。

覆盖任务验收线：
* ``POST /api/ingest/events``：单条/批量、Bearer 与 ``X-TraceLens-Token`` 两种令牌通道、
  幂等 id、字段校验、经 ``trace_adapter`` 归一化入库；
* ``GET /api/stream``：帧顺序单调、``since``/``Last-Event-ID`` 补发、类型过滤、心跳、
  订阅上限、停机收尾；
* Python SDK：``log_action`` / ``update_action`` / ``@track`` / ``stream_events``
  对着**真实本地服务**跑通；
* 不变量：上报永不覆盖决定字段（决定 ≠ 生效）、审计只追加不改算法（t9/t18 口径）、
  包内不出现网络字面量（server.py 不写文件，令牌落盘只在 ingest.py）。

约定：所有临时文件都在系统临时目录（``tests/scratch.py`` 的 ``scratch_dir``，tearDown 删除），服务只监听 127.0.0.1
随机端口，不产生外网连接，数据全为合成示例。
"""

from __future__ import annotations

import http.client
import json
import pathlib
import re
import shutil
import sys
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tests") not in sys.path:   # 裸 `python -m unittest tests.<模块>` 时也能 import scratch
    sys.path.insert(0, str(REPO / "tests"))

from agent_explainer import ingest as ing  # noqa: E402
from agent_explainer import server as srv  # noqa: E402
from agent_explainer.sdk import SdkTransportError, TraceLensClient, track  # noqa: E402
import http_fixture as fx  # noqa: E402  连接加固：端口就绪 + 瞬态一次性重试，断言级不重试

from scratch import scratch_dir  # 临时目录一律走系统临时目录（见 tests/scratch.py）

TMP = scratch_dir("ingest")
FIXTURE = REPO / "samples" / "action_records.json"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

class DictStore:
    """最小记录库：与 ``InterveneService.store`` 同接口（get/put），用于纯逻辑测试。"""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def get(self, key: str):
        return self.records.get(str(key))

    def put(self, record) -> None:
        self.records[str(record["id"])] = record

    def all(self):
        return list(self.records.values())


def event(aid: str, **kw):
    kw.setdefault("intent", "上传月度报表到共享盘")
    return ing.make_event(aid, agent_id="t14-agent", actor_id="t14-actor",
                          workspace_id="t14-ws", session_id="t14-sess", **kw)


def grab(fn, *args, **kw):
    """执行并返回捕获到的 IngestError（没有异常返回 None）。"""
    try:
        fn(*args, **kw)
    except ing.IngestError as exc:
        return exc
    return None


def http_request(port: int, method: str, path: str, body=None, headers=None):
    # M2-PRE 4（PM-VERDICT-R1-9 裁决② / R5-1 接线）：连接生命周期交给 tests/http_fixture——
    # 端口就绪等待 + 连接级瞬态一次性重试；POST/PUT/PATCH/DELETE 绝不重放，断言级失败绝不重试。
    # 请求构造与响应解析逐行不动，返回形状一字不改（建连与 close 交给夹具的 finally）。
    def once(conn: http.client.HTTPConnection):
        payload = None
        hd = dict(headers or {})
        if body is not None:
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            payload = text.encode("utf-8")
            hd.setdefault("Content-Type", "application/json")
        conn.request(method, path, payload, hd)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        status, headers_out = resp.status, {k.lower(): v for k, v in resp.getheaders()}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return status, headers_out, data

    return fx.with_connection("127.0.0.1", port, once, verb=method, path=path, timeout=10)


def http_post_events(port: int, body, *, token=None, key=None, extra=None):
    hd = dict(extra or {})
    if token:
        hd[ing.TOKEN_HEADER] = token
    if key:
        hd["Idempotency-Key"] = key
    return http_request(port, "POST", "/api/ingest/events", body, hd)


def http_stream_headers(port: int, path: str, *, headers=None):
    """只读状态行与响应头（不读无限流的水体），用于证明它是真的 SSE。"""
    # 这条辅助只发一个"读状态行 + 响应头"的幂等 GET 短请求（不读无限流水体），可接夹具；
    # 无限流水体的 read_sse_wire 与内联订阅连接不接（夹具的 finally 会在读完前关闭）。
    def once(conn: http.client.HTTPConnection):
        hd = {"Accept": "text/event-stream"}
        hd.update(headers or {})
        conn.request("GET", path, None, hd)
        resp = conn.getresponse()
        return {"status": resp.status,
           "content_type": dict(resp.getheaders()).get("Content-Type",
                                                       dict(resp.getheaders()).get("content-type", ""))}

    return fx.with_connection("127.0.0.1", port, once, verb="GET", path=path, timeout=10)


_CHUNK = re.compile(r"^[0-9a-fA-F]{1,8}$")


def read_sse_wire(port: int, path: str, count: int, *, timeout: float = 8.0):
    """读够 ``count`` 帧就断开，返回原始帧（event/id/data），顺序 = 服务端推送顺序。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", path, None, {"Accept": "text/event-stream"})
    resp = conn.getresponse()
    frames: list[dict] = []
    current: dict = {}
    data_lines: list[str] = []
    deadline = time.time() + timeout
    while len(frames) < count and time.time() < deadline:
        line = resp.fp.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if _CHUNK.match(text):
            continue
        if text == "":
            if data_lines:
                frames.append({"event": current.get("event", "message"),
                               "id": current.get("id", ""),
                               "data": "\n".join(data_lines)})
                data_lines = []
                current = {}
            continue
        if text.startswith(":"):
            continue
        field, _, value = text.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            current["event"] = value
        elif field == "id":
            current["id"] = value
        elif field == "data":
            data_lines.append(value)
    conn.close()
    return {"status": resp.status, "frames": frames}


def wire_names(result: dict) -> list[str]:
    return [f["event"] for f in result["frames"]]


def wire_ids(result: dict) -> list[int]:
    out = []
    for frame in result["frames"]:
        try:
            out.append(int(frame["id"]))
        except ValueError:
            pass
    return out


def fixture_records() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload["records"] if isinstance(payload, dict) else payload


def drain(sub, limit: int = 30) -> list[dict]:
    """把订阅者当前能取到的帧都取出来（Subscriber 只有 get/offer/close）。"""
    out: list[dict] = []
    while len(out) < limit:
        frame = sub.get(timeout=0.05)
        if not frame:
            break
        out.append(frame)
    return out


def audit_entries(svc) -> list[dict]:
    return list(svc.audit.entries())


class HttpCase(unittest.TestCase):
    """起真实本地服务的基类：每个测试自己起服务，tearDown 统一停机并清临时目录。"""

    def setUp(self) -> None:
        TMP.mkdir(parents=True, exist_ok=True)
        self.servers: list[srv.TraceLensServer] = []
        self._n = 0

    def tearDown(self) -> None:
        for server in self.servers:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        shutil.rmtree(TMP, ignore_errors=True)

    def serve(self, **kw) -> srv.TraceLensServer:
        self._n += 1
        kw.setdefault("quiet", True)
        kw.setdefault("ingest_token_path", TMP / f"tokens-{self._n}.json")
        server, _thread = srv.start_background_server("127.0.0.1", 0, **kw)
        self.servers.append(server)
        return server

    def client_for(self, server: srv.TraceLensServer, **kw) -> TraceLensClient:
        kw.setdefault("timeout", 8.0)
        kw.setdefault("stream_timeout", 5.0)
        kw.setdefault("agent_id", "t14-sdk")
        kw.setdefault("actor_id", "t14-actor")
        kw.setdefault("workspace_id", "t14-ws")
        kw.setdefault("session_id", "t14-sess")
        return TraceLensClient(server.url(), **kw)

    def stream(self, server: srv.TraceLensServer, **kw) -> list:
        """用 SDK 读推送（它是真实客户端的读法），返回帧列表。"""
        kw.setdefault("heartbeat", 0.05)
        kw.setdefault("max_events", 6)
        client = self.client_for(server)
        try:
            return list(client.stream_events(**kw))
        finally:
            client.close()


# ---------------------------------------------------------------------------
# 1. 令牌保管箱
# ---------------------------------------------------------------------------

class TokenVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        TMP.mkdir(parents=True, exist_ok=True)
        self.path = TMP / "vault.json"

    def tearDown(self) -> None:
        shutil.rmtree(TMP, ignore_errors=True)

    def test_create_returns_secret_once_and_stores_only_hash(self):
        vault = ing.TokenVault(self.path)
        secret, record = vault.create("ci-pipeline")
        self.assertTrue(secret)
        self.assertEqual(record["label"], "ci-pipeline")
        self.assertEqual(record["scopes"], [ing.SCOPE_WRITE])
        self.assertTrue(record["token_id"])
        self.assertTrue(record["prefix"] and record["prefix"] in secret)
        # 明文只在返回值里出现一次：文件、列表、统计里都不能有
        on_disk = self.path.read_text(encoding="utf-8")
        self.assertNotIn(secret, on_disk)
        self.assertNotIn(secret, json.dumps(vault.list_records(), ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(vault.stats(), ensure_ascii=False))
        for key in ("salt", "secret", "hash", "sha256"):
            self.assertNotIn(key, ing.public_record(record))
            for item in vault.list_records():
                self.assertNotIn(key, item)

    def test_verify_accepts_the_real_secret_and_rejects_everything_else(self):
        vault = ing.TokenVault(self.path)
        secret, record = vault.create("ci")
        self.assertEqual(vault.verify(secret)["token_id"], record["token_id"])
        self.assertEqual(grab(vault.verify, "").code, "INGEST_TOKEN_MISSING")
        for bad in ("nope", "tls_" + "x" * 30, secret[:-2] + "zz"):
            exc = grab(vault.verify, bad)
            self.assertIsNotNone(exc, f"{bad!r} 应当被拒")
            self.assertEqual(exc.code, "INGEST_TOKEN_INVALID")

    def test_revoked_token_is_rejected_as_disabled(self):
        vault = ing.TokenVault(self.path)
        secret, record = vault.create("ci")
        vault.revoke(record["token_id"], by="operator")
        listed = [r for r in vault.list_records() if r["token_id"] == record["token_id"]]
        self.assertTrue(listed[0]["disabled_at"])
        self.assertEqual(listed[0]["disabled_by"], "operator")
        exc = grab(vault.verify, secret)
        self.assertEqual(exc.code, "INGEST_TOKEN_DISABLED")
        self.assertEqual(ing.ingest_status_of(exc.code), 403)

    def test_scope_is_enforced_on_verify(self):
        vault = ing.TokenVault(self.path)
        secret, record = vault.create("manager-only", scopes=[ing.SCOPE_MANAGE])
        self.assertEqual(vault.verify(secret, scope=ing.SCOPE_MANAGE)["token_id"],
                         record["token_id"])
        exc = grab(vault.verify, secret, scope=ing.SCOPE_WRITE)
        self.assertEqual(exc.code, "INGEST_SCOPE_FORBIDDEN")

    def test_ttl_writes_expiry_and_vault_survives_reload(self):
        vault = ing.TokenVault(self.path)
        secret, record = vault.create("short-lived", ttl_seconds=60)
        self.assertTrue(record["expires_at"])
        reloaded = ing.TokenVault(self.path)
        self.assertEqual(reloaded.verify(secret)["token_id"], record["token_id"])
        self.assertEqual(reloaded.stats()["tokens"], 1)

    def test_unreadable_vault_yields_zero_tokens_instead_of_secrets(self):
        broken = TMP / "broken-vault"
        broken.mkdir(parents=True, exist_ok=True)      # 目录当文件读：必须降级，不能崩
        vault = ing.TokenVault(broken)
        self.assertEqual(vault.stats()["tokens"], 0)
        self.assertEqual(vault.list_records(), [])
        exc = grab(vault.verify, "tls_whatever")
        self.assertEqual(exc.code, "INGEST_TOKEN_INVALID")

    def test_no_vault_in_local_mode_is_anonymous_and_marked(self):
        broker = ing.IngestBroker(DictStore(), mode="local", vault=None)
        token = broker.authenticate(None)
        self.assertTrue(token["anonymous"])
        self.assertEqual(ing.ingest_status_of("INGEST_TOKEN_MISSING"), 401)


# ---------------------------------------------------------------------------
# 2. 载荷校验 / 鉴权 / 幂等（broker 层，无网络）
# ---------------------------------------------------------------------------

class IngestBrokerValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = DictStore()
        self.hub = ing.StreamHub()
        self.broker = ing.IngestBroker(self.store, self.hub, mode="local", language="zh")

    def ingest(self, payload, **kw):
        kw.setdefault("token", {"anonymous": True})
        return self.broker.ingest(payload, **kw)

    def test_single_event_envelope_is_accepted_and_stored(self):
        result = self.ingest({"events": [event("act-t14-0001")]})
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["ids"], ["act-t14-0001"])
        record = self.store.get("act-t14-0001")
        self.assertEqual(record["status"], "running")
        self.assertEqual(record["source"]["kind"], "webhook")
        self.assertIsNone(record["user_intervention"])
        self.assertEqual(record["execution_receipt"]["state"], "unreported")
        self.assertTrue(record["human_readable"]["l1"].strip())

    def test_bare_list_is_accepted_as_batch(self):
        result = self.ingest([event("act-t14-0011"), event("act-t14-0012")])
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(len(result["stream"]["frames"]), 2)

    def test_degraded_shapes_are_rejected_with_codes(self):
        cases = [("not json at all", "INGEST_SCHEMA_INVALID"),
                 ({"no_events": []}, "INGEST_SCHEMA_INVALID"),
                 ({"events": []}, "INGEST_EMPTY"),
                 ({"events": "not-a-list"}, "INGEST_SCHEMA_INVALID"),
                 ({"events": ["not-a-dict"]}, "INGEST_ADAPTER_REJECTED")]
        for payload, code in cases:
            exc = grab(self.ingest, payload)
            self.assertIsNotNone(exc, f"{payload!r} 必须被拒")
            self.assertEqual(exc.code, code, f"{payload!r} -> {exc.code}")

    def test_batch_size_and_byte_limits_are_enforced(self):
        small = ing.IngestBroker(self.store, self.hub, mode="local", max_events=2)
        exc = grab(small.ingest, {"events": [event(f"act-t14-002{i}") for i in range(3)]},
                   token={"anonymous": True})
        self.assertEqual(exc.code, "INGEST_TOO_MANY_EVENTS")
        self.assertEqual(ing.ingest_status_of(exc.code), 413)
        tiny = ing.IngestBroker(self.store, self.hub, mode="local", max_bytes=200)
        exc = grab(tiny.ingest, {"events": [event("act-t14-0030", intent="x" * 400)]},
                   token={"anonymous": True})
        self.assertEqual(exc.code, "INGEST_TOO_LARGE")

    def test_strict_mode_names_the_bad_event_and_lenient_skips_it(self):
        broken = event("act-t14-0040")
        broken["status"] = "not-a-status"
        exc = grab(self.ingest, {"events": [event("act-t14-0041"), broken]})
        self.assertEqual(exc.code, "INGEST_ADAPTER_REJECTED")
        self.assertIsNotNone(exc.index)
        result = self.ingest({"events": [event("act-t14-0042"), broken]}, strict=False)
        self.assertEqual(result["accepted"], 1, result["stats"])
        self.assertEqual(result["ids"], ["act-t14-0042"])      # 坏事件跳过，好事件照常入库
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["stats"]["skip_reasons"][0]["index"], 1)
        self.assertIsNotNone(self.store.get("act-t14-0042"))
        self.assertEqual(self.broker.counters["skipped"], 1)
        self.assertEqual(self.broker.counters["rejected"], 1)  # 只有 strict 那次算拒绝

    def test_mode_create_never_overwrites_and_reports_why(self):
        self.ingest({"events": [event("act-t14-0050", intent="第一版")]})
        result = self.ingest({"events": [event("act-t14-0050", intent="第二版")]})
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["duplicates"], 1)
        self.assertIn("id 已存在", result["duplicate_detail"][0]["reason"])
        self.assertEqual(self.store.get("act-t14-0050")["intent"], "第一版")

    def test_update_merges_facts_and_refreshes_the_copy(self):
        self.ingest({"events": [event("act-t14-0060", status="running",
                                      tool_used="files.upload")]})
        before = self.store.get("act-t14-0060")
        self.assertIn("进行中", before["human_readable"]["l1"])
        result = self.ingest({"mode": "update",
                              "events": [event("act-t14-0060", status="succeeded",
                                               result_summary="已上传 3 个文件",
                                               latency_ms=220)]})
        self.assertEqual(result["updated"], 1)
        after = self.store.get("act-t14-0060")
        self.assertEqual(after["status"], "succeeded")
        self.assertEqual(after["result_summary"], "已上传 3 个文件")
        self.assertIn("已完成", after["human_readable"]["l1"])
        self.assertNotIn("进行中", after["human_readable"]["l1"])
        merged_fields = {c["field"] for c in ing.merge_facts(before, after)[1]}
        self.assertNotIn("human_readable", merged_fields)   # 文案不"合并"，只由服务端按事实重算

    def test_update_never_touches_decision_and_receipt(self):
        self.ingest({"events": [event("act-t14-0070", status="pending_confirmation",
                                      requires_action=True)]})
        record = self.store.get("act-t14-0070")
        decision = {"decision": "reject", "reason": "先别动", "decided_by": "local-user",
                    "decided_at": "2026-01-01T00:00:00Z"}
        receipt = {"state": "refused_by_runtime", "reported_by": "runtime",
                   "reported_at": "2026-01-01T00:00:01Z", "detail": "执行端已挡下",
                   "sdk_version": None, "effect_notice": ing.EFFECT_NOTICE}
        record["user_intervention"] = decision
        record["execution_receipt"] = receipt
        record["intervention_options"] = [{"id": "keep-me", "kind": "reject", "label": "先不执行"}]
        hostile = event("act-t14-0070", status="succeeded",
                        result_summary="上报方自称已完成")
        for key, value in (("user_intervention", {"decision": "approve"}),
                           ("intervention_options", [{"id": "x"}]),
                           ("human_readable", {"l1": "我自己写的文案", "l2": [], "l3": ""}),
                           ("execution_receipt", {"state": "executed"}),
                           ("demo_data", True), ("synthetic", True),
                           ("source", {"kind": "manual"})):
            hostile[key] = value
        self.ingest({"mode": "update", "events": [hostile]})
        after = self.store.get("act-t14-0070")
        self.assertEqual(after["user_intervention"], decision)
        self.assertEqual(after["execution_receipt"], receipt)
        self.assertEqual(after["intervention_options"][0]["id"], "keep-me")
        self.assertNotEqual(after["human_readable"].get("l1"), "我自己写的文案")
        self.assertEqual(after["source"]["kind"], "webhook")
        self.assertFalse(after["demo_data"])
        self.assertEqual(after["status"], "succeeded")        # 事实字段合并了

    def test_idempotency_key_replays_and_conflicts(self):
        payload = {"events": [event("act-t14-0080")]}
        first = self.ingest(payload, idempotency_key="key-1")
        self.assertFalse(first["replay"])
        replay = self.ingest(payload, idempotency_key="key-1")
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["ids"], first["ids"])
        self.assertEqual(self.store.get("act-t14-0080")["status"], "running")
        self.assertEqual(self.broker.counters["replays"], 1)
        self.assertEqual(self.broker.counters["created"], 1)   # 重放没有二次入库
        exc = grab(self.ingest, {"events": [event("act-t14-0081")]}, idempotency_key="key-1")
        self.assertEqual(exc.code, "INGEST_IDEMPOTENCY_CONFLICT")

    def test_rate_limit_reports_retry(self):
        slow = ing.IngestBroker(self.store, self.hub, mode="local",
                                rate_capacity=1, rate_per_minute=6)
        ok = slow.ingest({"events": [event("act-t14-0090")]}, token={"anonymous": True})
        self.assertEqual(ok["accepted"], 1)
        exc = grab(slow.ingest, {"events": [event("act-t14-0091")]}, token={"anonymous": True})
        self.assertEqual(exc.code, "INGEST_RATE_LIMITED")
        self.assertEqual(ing.ingest_status_of(exc.code), 429)
        self.assertEqual(slow.counters["rate_limited"], 1)

    def test_disabled_ingest_refuses_everything(self):
        off = ing.IngestBroker(self.store, self.hub, mode="local", enabled=False)
        exc = grab(off.ingest, {"events": [event("act-t14-0100")]}, token={"anonymous": True})
        self.assertEqual(exc.code, "INGEST_DISABLED")

    def test_team_mode_requires_a_token_and_scopes(self):
        team = ing.IngestBroker(DictStore(), ing.StreamHub(), mode="team", vault=None)
        self.assertEqual(grab(team.ingest, {"events": []}).code, "INGEST_TOKEN_MISSING")
        vault = ing.TokenVault(TMP / "team-vault.json")
        team2 = ing.IngestBroker(DictStore(), ing.StreamHub(), mode="team", vault=vault)
        self.assertEqual(grab(team2.authenticate, None).code, "INGEST_TOKEN_MISSING")
        self.assertEqual(grab(team2.authenticate, "bogus").code, "INGEST_TOKEN_INVALID")
        secret, _ = vault.create("manager", scopes=[ing.SCOPE_MANAGE])
        self.assertEqual(grab(team2.authenticate, secret).code, "INGEST_SCOPE_FORBIDDEN")
        self.assertEqual(team2.authenticate(secret, scope=ing.SCOPE_MANAGE)["label"], "manager")

    def test_sdk_self_report_lands_as_sdk_source(self):
        self.ingest({"format": "sdk", "events": [event("act-t14-0110")]})
        self.ingest({"events": [event("act-t14-0111")]})
        self.assertEqual(self.store.get("act-t14-0110")["source"]["kind"], "sdk")
        self.assertEqual(self.store.get("act-t14-0111")["source"]["kind"], "webhook")
        for aid in ("act-t14-0110", "act-t14-0111"):
            src = self.store.get(aid)["source"]
            self.assertTrue(src["source_uri"].startswith("webhook://ingest/"))
            self.assertTrue(src["ingested_at"])
            # schema 的 source 是封闭对象：归因只能落在这五个键里
            self.assertEqual(set(src), {"kind", "adapter", "format_version",
                                        "source_uri", "ingested_at"})

    def test_counters_and_status_describe_the_pipeline(self):
        self.ingest({"events": [event("act-t14-0120")]})
        status = self.broker.status()
        self.assertEqual(status["counters"]["accepted"], 1)
        self.assertEqual(status["stream"]["head_seq"], 2)   # action.created + ingest.batch
        self.assertEqual(status["event_types"], list(ing.STREAM_EVENT_TYPES))
        self.assertIn("human_readable", status["protected_fields"])
        self.assertIn("user_intervention", status["protected_fields"])
        self.assertIn("status", status["merge_fields"])
        self.assertEqual(status["last_batch"]["accepted"], 1)


# ---------------------------------------------------------------------------
# 3. 广播顺序与补发（hub 层）
# ---------------------------------------------------------------------------

class StreamHubOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = ing.StreamHub(ring_size=5, limit=2)

    def publish(self, n: int) -> list[int]:
        seqs = []
        for i in range(n):
            frame = self.hub.publish(ing.EVENT_CREATED, {"n": i, "record": {"id": f"a{i}"}},
                                     record_id=f"a{i}")
            seqs.append(frame["seq"])
        return seqs

    def test_seq_is_monotonic_and_frames_after_replays_the_ring(self):
        seqs = self.publish(4)
        self.assertEqual(seqs, [1, 2, 3, 4])
        self.assertEqual([f["seq"] for f in self.hub.frames_after(2)], [3, 4])
        self.assertEqual([f["seq"] for f in self.hub.frames_after(0)], [1, 2, 3, 4])
        self.assertEqual(self.hub.frames_after(4), [])
        self.assertEqual(self.hub.snapshot()["head_seq"], 4)

    def test_ring_evicts_old_frames_instead_of_growing_forever(self):
        self.publish(12)
        replay = self.hub.frames_after(0, limit=100)
        self.assertLessEqual(len(replay), 5)
        self.assertEqual(replay[-1]["seq"], 12)

    def test_subscriber_receives_live_frames_and_filters_by_type(self):
        sub = self.hub.subscribe(types=[ing.EVENT_UPDATED])
        self.hub.publish(ing.EVENT_CREATED, {"x": 1})
        self.hub.publish(ing.EVENT_UPDATED, {"y": 2})
        self.assertEqual([f["type"] for f in drain(sub)], [ing.EVENT_UPDATED])
        self.hub.unsubscribe(sub)

    def test_subscriber_replays_from_cursor_then_follows_live(self):
        self.publish(3)
        sub = self.hub.subscribe(since=1)
        self.assertEqual(len(drain(sub)), 2)                    # 2、3 补发
        self.hub.publish(ing.EVENT_UPDATED, {"live": True})
        self.assertEqual([f["seq"] for f in drain(sub)], [4])
        self.hub.unsubscribe(sub)

    def test_subscriber_limit_is_enforced_and_reported(self):
        a = self.hub.subscribe()
        b = self.hub.subscribe()
        exc = grab(self.hub.subscribe)
        self.assertEqual(exc.code, "STREAM_LIMIT_REACHED")
        self.assertEqual(self.hub.snapshot()["rejected"], 1)
        self.hub.unsubscribe(a)
        self.hub.unsubscribe(b)

    def test_stop_releases_subscribers(self):
        sub = self.hub.subscribe()
        self.hub.publish(ing.EVENT_CREATED, {"x": 1})
        self.assertEqual(len(drain(sub)), 1)
        self.hub.stop()
        self.assertTrue(sub.closed)
        self.assertIsNone(sub.get(timeout=0.05))     # 哨兵：读流立刻收尾，不挂在长连接上


# ---------------------------------------------------------------------------
# 4. webhook 接口（真实 HTTP）
# ---------------------------------------------------------------------------

class WebhookHttpTests(HttpCase):
    def test_local_mode_accepts_single_and_batch_without_token(self):
        server = self.serve(records=fixture_records())
        port = server.server_address[1]
        status, _, body = http_post_events(port, {"events": [event("act-http-0001")]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["created"], 1)
        self.assertTrue(body["token"]["anonymous"])
        # 响应只报"这批产生了哪些帧号"；界面用 head_seq 追游标
        self.assertEqual(len(body["stream"]["frames"]), 1)
        self.assertGreater(body["stream"]["head_seq"], body["stream"]["frames"][0])
        self.assertEqual(body["stream"]["subscribers"], 0)
        status, _, body = http_post_events(port, {"events": [event("act-http-0002"),
                                                             event("act-http-0003")]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["accepted"], 2)
        status, _, records = http_request(port, "GET", "/api/records?limit=100")
        ids = {r["id"] for r in records["records"]}
        self.assertTrue({"act-http-0001", "act-http-0002", "act-http-0003"} <= ids)
        self.assertGreaterEqual(records["meta"]["ingested_count"], 3)
        self.assertGreaterEqual(records["meta"]["stream_head_seq"], 5)

    def test_team_mode_needs_a_token_on_both_channels(self):
        server = self.serve(mode="team", auth_state_path=TMP / "auth.json",
                            bootstrap_admin=True, records=[])
        port = server.server_address[1]
        status, _, body = http_post_events(port, {"events": [event("act-team-0001")]})
        self.assertEqual(status, 401, body)
        self.assertEqual(body["code"], "INGEST_TOKEN_MISSING")
        status, _, body = http_post_events(port, {"events": [event("act-team-0001")]},
                                           token="tls_not-a-real-token")
        self.assertEqual(status, 401, body)
        self.assertEqual(body["code"], "INGEST_TOKEN_INVALID")

        issued = server.service.issue_ingest_token({"label": "ci",
                                                    "scopes": [ing.SCOPE_WRITE]},
                                                   actor_id="t14-test")
        secret = issued["token"]
        self.assertTrue(secret)
        self.assertNotIn(secret, json.dumps(issued["record"], ensure_ascii=False))
        status, _, body = http_post_events(port, {"events": [event("act-team-0001")]},
                                           token=secret)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["token"]["label"], "ci")
        status, _, body = http_post_events(port, {"events": [event("act-team-0002")]},
                                           extra={"Authorization": f"{ing.BEARER_PREFIX} {secret}"})
        self.assertEqual(status, 200, body)
        # 读取走 4.3 会话（records.read）：机器令牌只写不读，匿名读在 team 模式必须被拒
        status, _, body = http_request(port, "GET", "/api/records?limit=100")
        self.assertIn(status, (401, 403), body)
        self.assertEqual(server.service.store.get("act-team-0001")["source"]["kind"],
                         "webhook")

        server.service.revoke_ingest_token(issued["record"]["token_id"], actor_id="t14-test")
        status, _, body = http_post_events(port, {"events": [event("act-team-0003")]},
                                           token=secret)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["code"], "INGEST_TOKEN_DISABLED")

    def test_token_without_write_scope_cannot_ingest(self):
        server = self.serve(mode="team", auth_state_path=TMP / "auth2.json",
                            bootstrap_admin=True, records=[])
        port = server.server_address[1]
        issued = server.service.issue_ingest_token({"label": "ops",
                                                    "scopes": [ing.SCOPE_MANAGE]},
                                                   actor_id="t14-test")
        status, _, body = http_post_events(port, {"events": [event("act-scope-0001")]},
                                           token=issued["token"])
        self.assertEqual(status, 403, body)
        self.assertEqual(body["code"], "INGEST_SCOPE_FORBIDDEN")

    def test_no_ingest_switch_refuses_writes(self):
        server = self.serve(ingest_enabled=False, records=[])
        port = server.server_address[1]
        status, _, body = http_post_events(port, {"events": [event("act-off-0001")]})
        self.assertEqual(status, 403, body)
        self.assertEqual(body["code"], "INGEST_DISABLED")
        status, _, health = http_request(port, "GET", "/health")
        self.assertFalse(health["ingest"]["enabled"])

    def test_invalid_payload_is_named_with_index_and_hint(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        status, _, body = http_post_events(port, {"events": []})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "INGEST_EMPTY")
        broken = event("act-bad-0001")
        broken["status"] = "exploded"
        status, _, body = http_post_events(port, {"events": [event("act-bad-0002"), broken]})
        self.assertEqual(status, 422, body)
        self.assertEqual(body["code"], "INGEST_ADAPTER_REJECTED")
        detail = body.get("detail") or {}
        self.assertEqual(detail.get("index"), 0)               # 0 基数组下标：坏的是第 2 条
        self.assertEqual(detail.get("field"), "records[1].status")
        self.assertTrue(detail.get("hint") or detail.get("adapter_code"), body)
        status, _, body = http_post_events(port, "{not json")
        self.assertGreaterEqual(status, 400, body)
        self.assertTrue(body.get("code"))

    def test_idempotency_and_rate_limit_headers_over_http(self):
        server = self.serve(ingest_rate=1, ingest_rate_per_minute=6, records=[])
        port = server.server_address[1]
        payload = {"events": [event("act-idem-0001")]}
        first_status, _, first = http_post_events(port, payload, key="idem-1")
        self.assertEqual(first_status, 200, first)
        replay_status, _, replay = http_post_events(port, payload, key="idem-1")
        self.assertEqual(replay_status, 200, replay)
        self.assertTrue(replay["replay"])
        self.assertEqual(replay["ids"], first["ids"])
        conflict_status, _, conflict = http_post_events(port,
                                                        {"events": [event("act-idem-0002")]},
                                                        key="idem-1")
        self.assertEqual(conflict_status, 409, conflict)
        self.assertEqual(conflict["code"], "INGEST_IDEMPOTENCY_CONFLICT")
        statuses = []
        limited_headers = {}
        for i in range(3):
            status, headers, body = http_post_events(
                port, {"events": [event(f"act-idem-000{3 + i}")]})
            statuses.append(status)
            if status == 429:
                limited_headers = headers
        self.assertIn(429, statuses, statuses)
        self.assertIn("retry-after", limited_headers, limited_headers)

    def test_ingest_status_and_token_routes_work_in_local_mode(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        status, _, issued = http_request(port, "POST", "/api/ingest/tokens",
                                         {"label": "t14-cli", "scopes": [ing.SCOPE_WRITE]})
        self.assertIn(status, (200, 201), issued)
        secret = issued["token"]
        self.assertTrue(secret and issued["record"]["token_id"])
        status, _, listed = http_request(port, "GET", "/api/ingest/status")
        self.assertEqual(status, 200, listed)
        self.assertEqual([t["label"] for t in listed["tokens_detail"]], ["t14-cli"])
        self.assertNotIn(secret, json.dumps(listed, ensure_ascii=False))
        status, _, body = http_request(port, "POST",
                                       f"/api/ingest/tokens/{issued['record']['token_id']}/revoke",
                                       {})
        self.assertEqual(status, 200, body)
        status, _, body = http_post_events(port, {"events": [event("act-tok-0001")]},
                                           token=secret)
        self.assertEqual(status, 403, body)

    def test_ops_routes_need_a_session_in_team_mode(self):
        server = self.serve(mode="team", auth_state_path=TMP / "auth3.json",
                            bootstrap_admin=True, records=[])
        port = server.server_address[1]
        status, _, body = http_request(port, "GET", "/api/ingest/status")
        self.assertIn(status, (401, 403), body)
        self.assertTrue(body.get("code"))
        status, _, body = http_request(port, "POST", "/api/ingest/tokens", {"label": "x"})
        self.assertIn(status, (401, 403), body)
        self.assertTrue(body.get("code"))
        # 令牌接口只接受 POST：GET 打进来不会把已签发令牌列出来
        status, _, body = http_request(port, "GET", "/api/ingest/tokens")
        self.assertEqual(status, 405, body)
        self.assertEqual(body["code"], "METHOD_NOT_ALLOWED")


# ---------------------------------------------------------------------------
# 5. SSE 推送（真实 HTTP）
# ---------------------------------------------------------------------------

class SseHttpTests(HttpCase):
    def seed(self, server: srv.TraceLensServer, n: int = 3) -> None:
        svc = server.service
        for i in range(n):
            svc.ingest_events({"events": [event(f"act-sse-{i:04d}")]}, request_id=f"req-{i}")

    def test_stream_is_really_sse_and_frames_are_ordered(self):
        server = self.serve(records=[])
        self.seed(server)
        port = server.server_address[1]
        meta = http_stream_headers(port, "/api/stream?max_events=1&heartbeat=0.05")
        self.assertEqual(meta["status"], 200, meta)
        self.assertIn("text/event-stream", meta["content_type"])
        wire = read_sse_wire(port, "/api/stream?since=1&max_events=6&heartbeat=0.05", 6)
        self.assertEqual(len(wire["frames"]), 6)
        names = wire_names(wire)
        self.assertIn("action.created", names)
        self.assertIn("ingest.batch", names)
        ids = wire_ids(wire)
        self.assertEqual(ids, sorted(ids))            # 推送顺序 = 服务端 seq 顺序
        self.assertEqual(ids[0], 2)                   # since=1 → 从第 2 帧起补发
        for frame in wire["frames"]:
            self.assertTrue(frame["data"].startswith("{"), frame["data"][:40])
            self.assertTrue(frame["id"], frame["event"])

    def test_live_stream_greets_before_anything_else(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        wire = read_sse_wire(port, "/api/stream?max_events=1&heartbeat=0.05", 1)
        self.assertEqual(wire_names(wire), ["hello"])   # 一连上就知道游标在哪
        self.assertIn("head_seq", wire["frames"][0]["data"])

    def test_since_and_last_event_id_replay_the_ring(self):
        server = self.serve(records=[])
        self.seed(server)
        port = server.server_address[1]
        replay = read_sse_wire(port, "/api/stream?since=2&max_events=4&heartbeat=0.05", 4)
        ids = wire_ids(replay)
        self.assertEqual(ids[0], 3)
        self.assertEqual(ids, sorted(ids))
        resumed = self.stream(server, since=4, max_events=3)
        self.assertTrue(all(f.seq > 4 for f in resumed), [f.seq for f in resumed])

    def test_type_filter_only_delivers_requested_frames(self):
        server = self.serve(records=[])
        self.seed(server)
        frames = self.stream(server, types=["action.created"], since=1, max_events=3)
        names = [f.event for f in frames]
        self.assertIn("action.created", names)
        self.assertNotIn("ingest.batch", names)          # 不想要的帧不推给界面
        seqs = [f.seq for f in frames if f.seq is not None]
        self.assertEqual(seqs, sorted(seqs))
        self.assertTrue(seqs)

    def test_heartbeat_arrives_and_keeps_the_cursor_alive(self):
        server = self.serve(records=[], stream_heartbeat=0.05)
        frames = self.stream(server, max_events=2, heartbeat=0.05)
        names = [f.event for f in frames]
        self.assertIn("hello", names)
        self.assertIn("heartbeat", names)
        hello = [f for f in frames if f.event == "hello"][0]
        self.assertIn("head_seq", hello.data["data"])
        self.assertEqual(hello.seq, 1)
        heartbeat = [f for f in frames if f.event == "heartbeat"][0]
        self.assertIsNone(heartbeat.seq)      # 心跳不带 id：浏览器游标停在最后一条真事件上

    def test_bad_queries_are_refused_not_silently_ignored(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        sse = {"Accept": "text/event-stream"}
        status, _, body = http_request(port, "GET", "/api/stream?since=not-a-number",
                                       headers=sse)
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "STREAM_QUERY_INVALID")
        status, _, body = http_request(port, "GET", "/api/stream?types=nonsense",
                                       headers=sse)
        self.assertIn(status, (400, 422), body)
        self.assertTrue(body.get("code"))
        # 没带 Accept: text/event-stream 的探测不当作流：浏览器地址栏打进来不会挂住
        status, _, body = http_request(port, "GET", "/api/stream")
        self.assertEqual(status, 406, body)
        self.assertEqual(body["code"], "STREAM_ACCEPT_REQUIRED")

    def test_subscriber_limit_returns_503_with_a_reason(self):
        server = self.serve(records=[], stream_limit=1)
        self.seed(server)
        port = server.server_address[1]
        holder = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        holder.request("GET", "/api/stream?heartbeat=0.05", None,
                       {"Accept": "text/event-stream"})
        resp = holder.getresponse()
        self.assertEqual(resp.status, 200)
        resp.fp.readline()                       # 读到 hello：订阅确实登记上了
        try:
            status, _, body = http_request(port, "GET", "/api/stream?max_events=1",
                                           headers={"Accept": "text/event-stream"})
            self.assertEqual(status, 503, body)
            self.assertEqual(body["code"], "STREAM_LIMIT_REACHED")
            health = server.service.stream_health_block()
            self.assertEqual(health["fallback"]["mode"], "polling")
            self.assertGreaterEqual(health["rejected"], 1)
        finally:
            holder.close()

    def test_decision_and_reset_are_broadcast(self):
        server = self.serve(records=fixture_records())
        port = server.server_address[1]
        self.seed(server, 1)          # 先有上报帧，决定帧的游标才在补发窗口里
        record = server.service.store.get("act-demo-0004")
        option_id = record["intervention_options"][0]["id"]
        status, _, body = http_request(port, "POST", "/intervene",
                                       {"action_id": "act-demo-0004", "decision": "approve",
                                        "option_id": option_id, "reason": "t14 验收"})
        self.assertEqual(status, 200, body)
        decision = self.stream(server, types=["action.decision_recorded"], since=2, max_events=1)
        self.assertEqual([f.event for f in decision], ["action.decision_recorded"])
        payload = decision[0].data["data"]
        self.assertEqual(payload["action_id"], "act-demo-0004")
        self.assertEqual(payload["decision"], "approve")
        self.assertTrue(payload["decided_by"])
        self.assertTrue(payload["effect_notice"])          # 决定 ≠ 生效：帧里带着这句提示
        self.assertEqual(decision[0].record_id, "act-demo-0004")
        status, _, body = http_request(port, "POST", "/api/reset", {})
        self.assertEqual(status, 200, body)
        reset = self.stream(server, types=["records.reset"], since=2, max_events=1)
        self.assertEqual([f.event for f in reset], ["records.reset"])

    def test_shutdown_releases_live_streams(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        wire = read_sse_wire(port, "/api/stream?max_events=1&heartbeat=0.05", 1)
        self.assertEqual(wire["status"], 200)
        server.shutdown()
        self.servers.remove(server)
        server.server_close()
        hub = server.service.stream_hub
        deadline = time.time() + 2.0
        while hub.snapshot()["subscribers"] > 0 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(hub.snapshot()["subscribers"], 0)


# ---------------------------------------------------------------------------
# 6. SDK 对着真实本地服务
# ---------------------------------------------------------------------------

class SdkAgainstLocalServerTests(HttpCase):
    def test_log_action_and_update_action_land_in_the_ui_store(self):
        server = self.serve(records=[])
        client = self.client_for(server)
        try:
            created = client.log_action("调用订单汇总接口", action_type="tool_call",
                                        status="running", param_names=["month"], labels=["t14"])
            rid = created["action_id"]
            self.assertTrue(rid.startswith("act-"))
            record = server.service.store.get(rid)
            self.assertEqual(record["source"]["kind"], "sdk")
            self.assertIn("param:month", record["labels"])
            self.assertIn("t14", record["labels"])
            self.assertTrue(record["human_readable"]["l1"].strip())
            self.assertEqual(record["human_readable"]["language"], "zh")
            self.assertIsNone(record["user_intervention"])
            self.assertEqual(record["execution_receipt"]["state"], "unreported")
            updated = client.update_action(rid, status="succeeded",
                                           result_summary="汇总 42 单", latency_ms=140)
            self.assertEqual(updated["updated"], 1)
            after = server.service.store.get(rid)
            self.assertEqual(after["status"], "succeeded")
            self.assertIn("已完成", after["human_readable"]["l1"])
            self.assertTrue(updated["note"])
            self.assertEqual(updated["head_seq"], updated["response"]["stream"]["head_seq"])
        finally:
            client.close()

    def test_track_reports_success_and_failure_without_leaking_arguments(self):
        server = self.serve(records=[])
        client = self.client_for(server)
        try:
            @track(client, action_type="export", intent="导出订单给财务对账")
            def export(limit: int, token: str):
                return [{"id": f"SO-{i}"} for i in range(limit)]

            @track(client, action_type="export", intent="导出上月订单")
            def export_bad(limit: int):
                raise ValueError("上游返回了不认识的字段")

            export(2, "hunter2-secret")
            with self.assertRaises(ValueError):
                export_bad(1)
            good = server.service.store.get(export.trace_ids[0])
            bad = server.service.store.get(export_bad.trace_ids[0])
            self.assertEqual(good["status"], "succeeded")
            self.assertGreaterEqual(good["latency_ms"], 0)
            self.assertTrue(good["result_summary"])
            self.assertEqual(bad["status"], "failed")
            self.assertEqual(bad["error"]["code"], "ValueError")
            self.assertIn("param:limit", good["labels"])
            self.assertNotIn("hunter2-secret", json.dumps(good, ensure_ascii=False))
            self.assertNotIn("hunter2-secret", json.dumps(bad, ensure_ascii=False))
            self.assertEqual(len(export.trace_ids), 1)   # 同一条 id：running→succeeded 合并
            self.assertEqual(client.stats()["sent_events"], 4)
        finally:
            client.close()

    def test_stream_events_reads_frames_in_order_and_can_resume(self):
        server = self.serve(records=[])
        client = self.client_for(server)
        try:
            for i in range(3):
                client.log_action(f"第 {i} 步", action_type="tool_call", status="running")
            live = [f for f in client.stream_events(since=1, max_events=8, heartbeat=0.05)
                    if f.seq is not None]
            self.assertEqual([f.seq for f in live], sorted(f.seq for f in live))
            self.assertIn("action.created", [f.event for f in live])
            self.assertIn("ingest.batch", [f.event for f in live])
            self.assertTrue(all(f.record_id for f in live if f.event == "action.created"))
            resumed = [f for f in client.stream_events(since=2, max_events=3)
                       if f.seq is not None]
            self.assertTrue(all(f.seq > 2 for f in resumed), [f.seq for f in resumed])
            only_created = list(client.stream_events(types=["action.created"], since=1,
                                                     max_events=2))
            self.assertEqual({f.event for f in only_created}, {"action.created"})
        finally:
            client.close()

    def test_token_lifecycle_through_the_sdk(self):
        server = self.serve(records=[])
        client = self.client_for(server)
        port = server.server_address[1]
        try:
            issued = client.issue_ingest_token("sdk-issued")
            secret = issued["token"]
            self.assertTrue(secret)
            listed = client.ingest_status()
            self.assertNotIn(secret, json.dumps(listed, ensure_ascii=False))
            status, _, body = http_post_events(port, {"events": [event("act-sdk-tok-0001")]},
                                               token=secret)
            self.assertEqual(status, 200, body)
            client.revoke_ingest_token(issued["record"]["token_id"])
            status, _, body = http_post_events(port, {"events": [event("act-sdk-tok-0002")]},
                                               token=secret)
            self.assertEqual(status, 403, body)
        finally:
            client.close()

    def test_transport_failure_is_loud_not_silent(self):
        dead = TraceLensClient("http://127.0.0.1:1/", timeout=1.5, retries=0)
        try:
            with self.assertRaises(SdkTransportError):
                dead.health()
            with self.assertRaises(SdkTransportError):
                dead.log_action("上报到不存在的地址")
            self.assertGreaterEqual(dead.stats()["http_calls"], 2)
            self.assertFalse(dead.stats()["sent_events"] == 0 and dead.stats()["http_calls"] == 0)
        finally:
            dead.close()

    def test_records_and_health_expose_the_ingest_surface(self):
        server = self.serve(records=fixture_records())
        client = self.client_for(server)
        try:
            client.log_action("实时上报一条", action_type="tool_call", status="succeeded",
                              result_summary="完成")
            health = client.health()
            self.assertTrue(health["ingest"]["enabled"])
            self.assertIn("/api/ingest/events", health["ingest"]["endpoints"])
            self.assertIn("/api/stream", health["stream"]["endpoint"])
            self.assertEqual(health["stream"]["replay"]["last_event_id_header"], "Last-Event-ID")
            self.assertEqual(health["stream"]["fallback"]["mode"], "polling")
            payload = client.call("GET", "/api/records", query={"limit": 100})
            self.assertGreaterEqual(payload["meta"]["ingested_count"], 1)
            self.assertTrue(payload["meta"]["note"])
            self.assertTrue(payload["meta"]["synthetic"])
        finally:
            client.close()


# ---------------------------------------------------------------------------
# 7. 审计与契约：上报只追加记账，不改算法
# ---------------------------------------------------------------------------

class AuditAndContractTests(HttpCase):
    def test_ingest_appends_one_note_per_batch_without_body_text(self):
        server = self.serve(records=[], audit_log_path=TMP / "audit-t14.jsonl")
        port = server.server_address[1]
        before = len(audit_entries(server.service))
        status, _, body = http_post_events(port, {"events": [event("act-audit-0001",
                                                                  intent="含敏感字样的原始参数")]})
        self.assertEqual(status, 200, body)
        entries = audit_entries(server.service)
        self.assertEqual(len(entries), before + 1)
        entry = [e for e in entries if e.get("ingest_event") == "batch_accepted"][-1]
        self.assertEqual(entry["action_id"], "act-audit-0001")
        self.assertTrue(entry.get("actor_id"))
        self.assertIn(entry.get("event"), ("note", "ingest.batch_accepted"))
        self.assertTrue(entry.get("entry_hash"))
        self.assertNotIn("含敏感字样的原始参数", json.dumps(entry, ensure_ascii=False))
        # 重放不重复记账（t8 口径）
        payload = {"events": [event("act-audit-0002")]}
        status, _, first = http_post_events(port, payload, key="k1")
        self.assertEqual(status, 200, first)
        count = len(audit_entries(server.service))
        status, _, replay = http_post_events(port, payload, key="k1")
        self.assertEqual(status, 200, replay)
        self.assertTrue(replay["replay"])
        self.assertEqual(len(audit_entries(server.service)), count)

    def test_durable_audit_keeps_the_chain_intact(self):
        path = TMP / "audit-chain-t14.jsonl"
        server = self.serve(records=[], audit_log_path=path)
        port = server.server_address[1]
        for i in range(3):
            status, _, body = http_post_events(port, {"events": [event(f"act-chain-{i:04d}")]})
            self.assertEqual(status, 200, body)
        status, _, audit = http_request(port, "GET", "/api/audit?limit=50")
        self.assertEqual(status, 200, audit)
        self.assertTrue(audit["chain"]["ok"], audit["chain"])
        self.assertIn("jsonl", json.dumps(audit))
        self.assertTrue(audit["meta"]["durable"])
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), audit["jsonl"]["count"])
        self.assertIn("batch_accepted", path.read_text(encoding="utf-8"))
        self.assertTrue(server.service.audit.verify()["ok"])

    def test_token_events_are_audited_without_the_secret(self):
        server = self.serve(records=[])
        port = server.server_address[1]
        status, _, issued = http_request(port, "POST", "/api/ingest/tokens", {"label": "audit-me"})
        self.assertIn(status, (200, 201), issued)
        extras = [e.get("ingest_event") for e in audit_entries(server.service)]
        self.assertIn("token_created", extras)
        self.assertNotIn(issued["token"], (TMP / "tokens-1.json").read_text(encoding="utf-8"))
        self.assertNotIn(issued["token"], json.dumps(audit_entries(server.service),
                                                     ensure_ascii=False))


# ---------------------------------------------------------------------------
# 8. 边界守护：包内不出现网络字面量，server.py 不写文件
# ---------------------------------------------------------------------------

class PurityGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ingest_src = (REPO / "agent_explainer" / "ingest.py").read_text(encoding="utf-8")
        self.server_src = (REPO / "agent_explainer" / "server.py").read_text(encoding="utf-8")
        self.sdk_src = (REPO / "agent_explainer" / "sdk" / "client.py").read_text(encoding="utf-8")

    def test_ingest_module_stays_offline_and_secret_clean(self):
        for banned in ("http://", "https://", "urllib", "socket.", "subprocess",
                       "Authorization:", "WebSocket", "requests.", "httpx", "openai"):
            self.assertNotIn(banned, self.ingest_src, f"ingest.py 不应出现 {banned}")
        for banned in (r"sk-[A-Za-z0-9]{12,}",):
            self.assertIsNone(re.search(banned, self.ingest_src),
                              f"ingest.py 泄漏密钥形态：{banned}")
        # 上报体里的密钥形态字段是被脱敏的对象，不是被禁止的单词
        self.assertIn("SECRET_KEY_HINTS", self.ingest_src)
        self.assertIn("api_key", self.ingest_src)

    def test_server_does_not_write_files_so_the_vault_lives_in_ingest(self):
        for banned in ("write_text", "write_bytes", "mkdir", "shutil", "unlink",
                       "open(", "subprocess", "os.system", "Popen",
                       "Authorization:", "api_key"):
            self.assertNotIn(banned, self.server_src, f"server.py 不应出现 {banned}")
        self.assertIn("ingest_mod.TokenVault", self.server_src)

    def test_sdk_is_the_only_place_allowed_to_open_a_connection(self):
        self.assertIn("urllib.request", self.sdk_src)
        self.assertIn("ingest_mod.TOKEN_HEADER", self.sdk_src)   # 专用头常量来自 ingest
        self.assertIn("Authorization", self.sdk_src)             # 兼容只认 Bearer 的框架
        self.assertNotIn("sk-", self.sdk_src)

    def test_protected_fields_are_declared_and_never_merged(self):
        self.assertEqual(set(ing.PROTECTED_FIELDS) & set(ing.MERGE_FIELDS), set())
        for field in ("user_intervention", "intervention_options", "human_readable",
                      "source", "schema_version"):
            self.assertIn(field, ing.PROTECTED_FIELDS)
        self.assertIn("status", ing.MERGE_FIELDS)
        self.assertTrue(ing.READABLE_FACT_FIELDS <= set(ing.MERGE_FIELDS))

    def test_stream_event_names_match_the_documented_set(self):
        self.assertEqual(set(ing.STREAM_EVENT_TYPES),
                         {ing.EVENT_HELLO, ing.EVENT_HEARTBEAT, ing.EVENT_CREATED,
                          ing.EVENT_UPDATED, ing.EVENT_DECISION, ing.EVENT_RESET,
                          ing.EVENT_BATCH, ing.EVENT_STATS})

    def test_samples_demo_is_one_command_and_synthetic_only(self):
        src = (REPO / "samples" / "sdk_demo.py").read_text(encoding="utf-8")
        self.assertIn("def main()", src)
        self.assertIn("start_background_server", src)
        self.assertIn("stream_events", src)
        self.assertNotIn("sk-", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
