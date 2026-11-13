#!/usr/bin/env python3
"""容器健康检查（子任务 5.4 / t17）。

口径（docs/03 §6.6 + §17"验收（必须真实）"）：**不是**看端口通不通，而是
真的发一次 `GET /health`，要求 HTTP 200 且 `status == "ok"`。
产品没有 `/api/health`，也没有 `ready` 字段 —— 这两点在本脚本里都是硬约束：
如果哪天有人把路径改成 `/api/health`，这个检查会立刻变红，而不是假装健康。

退出码：0 = 健康；1 = 不健康（连不上 / 非 200 / status 不是 ok）。原因打到
stdout，`docker inspect --format '{{json .State.Health.Log}}'` 能直接看到。

只用标准库：镜像里没有 curl，也不打算为此装一个。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = "8765"
HEALTH_PATH = "/health"          # 不是 /api/health（docs/03 §6.6）
TIMEOUT_S = 4.0


def main() -> int:
    port = os.environ.get("TRACELENS_HEALTH_PORT", DEFAULT_PORT).strip() or DEFAULT_PORT
    url = "http://127.0.0.1:%s%s" % (port, HEALTH_PATH)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:                    # 4xx/5xx：服务在但不健康
        print("unhealthy: %s -> HTTP %s" % (url, exc.code))
        return 1
    except Exception as exc:                                 # 连不上 / 超时 / 域名解析
        print("unhealthy: %s -> %s: %s" % (url, type(exc).__name__, exc))
        return 1
    if status != 200:
        print("unhealthy: %s -> HTTP %s" % (url, status))
        return 1
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        print("unhealthy: %s -> 响应不是 JSON（%s）" % (url, type(exc).__name__))
        return 1
    if not isinstance(payload, dict):
        print("unhealthy: %s -> 响应顶层不是对象" % url)
        return 1
    if payload.get("status") != "ok":
        print("unhealthy: %s -> status=%r（要求 'ok'）" % (url, payload.get("status")))
        return 1
    stats = payload.get("stats") or {}
    print("healthy: %s -> status=ok version=%s mode=%s records=%s durable=%s"
          % (url, payload.get("version"), payload.get("mode"),
             stats.get("count"), stats.get("durable")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
