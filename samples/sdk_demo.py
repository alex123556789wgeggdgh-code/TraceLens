"""4.4 实时上报演示：一边跑 Agent，一边把动作写进 TraceLens（合成示例）。

一条命令跑完（在 ``agent-explainer/`` 目录下）：

    python samples/sdk_demo.py

它做四件事，全程离线、只监听回环地址、跑完自动关服务：

1. 用 ``agent_explainer.server`` 在 ``127.0.0.1:0``（随机空闲端口）起一个进程内服务，
   初始数据就是 ``samples/action_records.json`` 那 12 条合成演示记录；
2. 用 Python SDK（``agent_explainer.sdk``）在 Agent"执行"的过程中实时上报：
   ``log_action`` 报"开始"、``update_action`` 报"结果"、``@track`` 装饰器包一个真实函数；
3. 同时用 SDK 的 ``stream_events`` 读服务端推送（SSE，``GET /api/stream``），
   证明"边跑边出现"不是刷新出来的，是推过来的（每帧带单调 seq，断线可按游标补发）；
4. 校验落库结果：新记录进了清单、文案是中文 L1、来源标成 ``sdk``，
   而 ``user_intervention`` 仍是 ``null``、``execution_receipt.state`` 仍是
   ``unreported`` —— 上报只写事实，绝不替用户做决定，也不谎报"已生效"。

数据全部为合成示例（无真实人名/路径/金额）；见 ``samples/README.md``。
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_explainer import server as srv  # noqa: E402
from agent_explainer.sdk import TraceLensClient, track  # noqa: E402

FIXTURE = REPO / "samples" / "action_records.json"
#: 令牌表位置（本演示在 local 模式跑，不需要令牌，所以这个文件不会被写入；
#: 放在仓库内的 var/ 下，演示不会往系统临时目录写任何东西）
DEMO_TOKENS = REPO / "var" / "sdk-demo-tokens.json"
EXPECTED_FRAMES = 12


def load_fixture_records() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records:
        raise SystemExit("sdk_demo: 夹具里没有记录，演示无法开始")
    return records


def export_orders(limit: int) -> list[dict]:
    """一个"业务函数"：模拟导出订单，返回条数就是上报里的 result_summary。"""
    time.sleep(0.05)
    return [{"order_id": f"SO-DEMO-{i:03d}", "amount": None} for i in range(limit)]


def export_orders_bad(limit: int) -> list[dict]:
    """同一个函数的失败路径：@track 会把异常原样上报，然后原样抛出。"""
    time.sleep(0.02)
    raise ValueError("orders.export: 上游返回了不认识的字段（合成故障）")


def read_frames(client: TraceLensClient, sink: list[dict], stop: threading.Event) -> None:
    """后台线程：读推送流。每帧存 (event, seq, action_id)，心跳帧没有 id。"""
    try:
        for frame in client.stream_events(max_events=EXPECTED_FRAMES, heartbeat=1.5):
            sink.append({"event": frame.event, "seq": frame.seq,
                         "record_id": frame.record_id,
                         "changed": list((frame.data.get("changed_fields") or []))})
            if len(sink) >= EXPECTED_FRAMES:
                break
    except Exception as exc:  # 演示里把失败如实记下来，不吞掉
        sink.append({"event": "reader.error", "seq": None, "record_id": repr(exc)})
    stop.set()


def main() -> int:
    records = load_fixture_records()
    server, _thread = srv.start_background_server(
        "127.0.0.1", 0, records=records,
        ingest_token_path=DEMO_TOKENS, stream_heartbeat=0.5)
    base = server.url()          # 协议前缀由服务端拼装：包里不出现 URL 字面量
    client = TraceLensClient(
        base, agent_id="demo-agent", actor_id="demo-run",
        workspace_id="local", session_id="sdk-demo-run", language="zh",
        stream_timeout=8.0)
    print(f"sdk_demo: 服务已启动 {base}（进程内、回环、随机端口，跑完自动关）")

    frames: list[dict] = []
    stop = threading.Event()
    reader = threading.Thread(target=read_frames, args=(client, frames, stop),
                              name="sdk-demo-sse", daemon=True)
    reader.start()
    time.sleep(0.2)              # 让订阅先连上，之后的帧才是"边跑边推"

    tracked_ok = track(client, action_type="export", intent="把本月订单导出给财务对账")(export_orders)
    tracked_bad = track(client, action_type="export", intent="把上月订单导出给财务对账")(export_orders_bad)

    try:
        started = client.log_action("调用订单汇总接口", action_type="tool_call",
                                    status="running", param_names=["month"])
        client.update_action(started["action_id"], status="succeeded",
                             result_summary="汇总 2026-10 共 42 单", latency_ms=180)
        client.log_action("把汇总结果发到财务群", action_type="send", status="pending_confirmation",
                          requires_action=True, risk_level="medium",
                          result_summary="等待你确认：消息一旦发出不可撤回")
        tracked_ok(3)
        try:
            tracked_bad(2)
        except ValueError as exc:
            print(f"sdk_demo: @track 捕获到失败并上报：{type(exc).__name__}: {exc}")

        reader.join(timeout=10)
        seqs = [f["seq"] for f in frames if f["seq"] is not None]
        kinds = [f["event"] for f in frames]
        print(f"sdk_demo: 收到 {len(frames)} 帧推送：{', '.join(kinds)}")
        print(f"sdk_demo: 帧游标 {seqs}（单调递增，断线后可按 Last-Event-ID 补发）")

        payload = client.call("GET", "/api/records", allow_retry=True, query={"limit": 100})
        by_id = {str(r.get("id")): r for r in (payload.get("records") or [])}
        new_ids = list(dict.fromkeys(str(f["record_id"]) for f in frames
                                     if f["record_id"] and str(f["record_id"]) in by_id))
        meta = payload.get("meta") or {}
        print(f"sdk_demo: 服务端现有 {len(by_id)} 条记录，其中 {meta.get('ingested_count')} 条来自实时上报"
              f"（推送流游标 stream_head_seq={meta.get('stream_head_seq')}）")

        problems = []
        if len(frames) < 6:
            problems.append(f"推送帧太少：{len(frames)}")
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            problems.append(f"帧顺序不单调：{seqs}")
        for key in ("action.created", "action.updated", "ingest.batch"):
            if key not in kinds:
                problems.append(f"缺少推送事件类型：{key}")
        for rid in new_ids[:4]:
            rec = by_id[rid]
            hr = rec.get("human_readable") or {}
            src = rec.get("source") or {}
            receipt = rec.get("execution_receipt") or {}
            print(f"  · {rid}: [{src.get('kind')}] {hr.get('l1')} / 状态 {rec.get('status')}"
                  f" / 决定 {rec.get('user_intervention')!r} / 回执 {receipt.get('state')}")
            if src.get("kind") != "sdk":
                problems.append(f"{rid} 的来源没有标成 sdk：{src.get('kind')}")
            if not str(hr.get("l1") or "").strip():
                problems.append(f"{rid} 没有中文 L1 文案")
            if rec.get("status") == "succeeded" and "进行中" in str(hr.get("l1") or ""):
                problems.append(f"{rid} 的文案停留在旧状态：{hr.get('l1')}")
            if rec.get("user_intervention") is not None:
                problems.append(f"{rid} 的 user_intervention 被上报写掉了（决定≠上报）")
            if receipt.get("state") not in (None, "unreported"):
                problems.append(f"{rid} 的回执被上报改成了 {receipt.get('state')}（不得谎报生效）")
        if len(new_ids) < 3:
            problems.append(f"新记录没有全部进清单：{new_ids}")

        health = client.health()
        ingest = client.ingest_status()
        print(f"sdk_demo: /health ingest.enabled={health['ingest']['enabled']} "
              f"stream.endpoint={health['stream']['endpoint']} "
              f"（界面用 EventSource 打开这个地址）")
        counters = ingest.get("counters") or {}
        rate = ingest.get("rate") or {}
        tokens = ingest.get("tokens") or {}
        print(f"sdk_demo: /api/ingest/status batches={counters.get('batches')} "
              f"accepted={counters.get('accepted')} created={counters.get('created')} "
              f"updated={counters.get('updated')} rejected={counters.get('rejected')} "
              f"sdk_events={counters.get('sdk_events')} "
              f"限流={rate.get('capacity')}/s 令牌表={tokens.get('storage')}({tokens.get('tokens')} 条)")
        print(f"sdk_demo: 打开 {base}?api=live 就能看到这些动作实时出现在界面上"
              f"（推送断开时界面会自己降级为轮询，并在顶栏写清原因）")
        if problems:
            print("sdk_demo: 失败 -> " + "；".join(problems))
            return 1
        print("sdk_demo: OK —— 边跑边上报、实时推送、落库事实都符合约定")
        return 0
    finally:
        client.close()
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
