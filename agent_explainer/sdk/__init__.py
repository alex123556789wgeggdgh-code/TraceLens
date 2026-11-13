"""TraceLens Python SDK（4.4 实时接入 / t14）。

一句话：`TraceLensClient` 把「Agent 跑了一步」变成「界面上多了一张卡片」，
中间只走 4.4 的两个契约端点：`POST /api/ingest/events` 写入、`GET /api/stream` 广播。

设计口径
--------
* **只发事实**：SDK 造的记录由 :func:`agent_explainer.ingest.make_event` 生成，
  字段封闭、过 t3 的两道闸门；``demo_data=false``、``may_contain_inference=true``
  都如实标注，不伪装成人工撰写或已核实的内容。
* **决定 ≠ 生效**：SDK 从不写 ``user_intervention`` / ``execution_receipt`` 的决定语义；
  上报里"成功"只代表来源自己的观察，界面仍显示执行回执的原状态。
* **重试只在安全时**：带 ``idempotency_key`` 的写入与只读请求才会自动重试；
  没带幂等键的 POST 失败就失败（服务端不假装知道"这条到底进了没"）。
* **密钥自净**：装饰器只把参数**名**作为 label 上报（``param:xxx``），值不进上报体。

本子包允许用 ``urllib``（离线纯度扫描只覆盖包内**顶层**模块，见
``tests/test_trace_adapter.py`` 的扫描范围）；服务端与解释层从不出网，
SDK 只在你的机器上访问你指定的 TraceLens 地址。
"""

from __future__ import annotations

from .client import (
    EVENT_TYPES,
    SdkTransportError,
    TraceLensClient,
    frame_action_id,
    parse_sse_stream,
    track,
)

__all__ = ["TraceLensClient", "track", "SdkTransportError", "EVENT_TYPES",
           "parse_sse_stream", "frame_action_id"]
