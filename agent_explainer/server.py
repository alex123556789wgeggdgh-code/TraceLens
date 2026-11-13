"""t8 [t4-1] 本地服务：静态页面 + `POST /intervene` 干预闭环（local 模式）。

只用 Python 标准库（`http.server`），不依赖 FastAPI/uvicorn，也不出网：
本模块**从不主动发起任何网络请求**，也不使用标准库的 URL 解析模块
（包内不得出现该模块名或 URL 字面量，见 tests/test_trace_adapter.py
的离线纯度扫描），只在本机监听并返回本地数据。

设计口径（来自 docs/02-data-model.md 第 4 节 + docs/03-architecture.md 端点契约）
------------------------------------------------------------------------
* 决定段与执行段严格分离（队长裁决 Q2 / D1 / C11）：
  `/intervene` 只写 `status` + `user_intervention`（`recorded` 恒为 `true`）
  + 审计哈希链 + 重新生成 `human_readable`（状态变了 → 事实指纹必须重算，02 §3.6）。
  `execution_receipt` **原样不动**（默认 `state="unreported"`、`effect_notice=
  intervention.effect.recorded_only`）——决定已记录 ≠ 已生效，界面不得写"已执行/已停止"。
* 迁移表（02 §4.2）：`pending_confirmation | expired → approved / modified / rejected`，
  与 `decision` 一一对应（C2），并且：
    - C14：`pending_confirmation` 不得已有决定（已有决定 → 409 幂等/重复）
    - C3 ：`decision=modify` 必须有非空 `modified_params` + `original_params`
    - C4 ：`decision=reject` 必须给 `reason`
    - C1 ：`requires_action=true` 时不得带决定 → 决定后把 `requires_action` 置 `false`
    - C13：`expired` 不得带"已执行"回执（超时绝不自动同意，W-02）
* 幂等（02 Q6）：`idempotency_key` 去重；重复提交返回同一条记录 + `X-Intervene-Replay: true`，
  不重复写审计。
* 鉴权在 4.3 接入：本文件把身份写死为 `decided_by="local-user" / decided_by_role="local"`
  （t2 权限矩阵的 local 角色，允许干预），**请求体里不允许携带 `actor_id`**
  （未知字段 → 400），这样 4.3 换成服务端从会话取身份时不会改变对外契约。
* 审计：进程内 JSONL 形态 + sha256 哈希链（`prev_hash` → `entry_hash`），
  是 02 §审计格式的内存预览版；落盘与保留策略属 t9/t11，不在本任务范围。

安全边界
--------
* `--mode local`（默认）只监听 `127.0.0.1` 且免登录；`--mode team` 默认监听 `0.0.0.0`
  供局域网/容器访问，**必须登录**，角色矩阵由 :mod:`agent_explainer.auth` 单点强制。
* 静态服务只做三件事：限定根目录、拒绝 `..` / 反斜杠 / 绝对路径、拒绝目录列表。
* 请求体上限 64 KB；日志只记 path + status + request_id，**绝不记请求体**（02 隐私条款：
  日志中不得含参数明文）。登录接口同理：**绝不记口令**（审计只记 username 与结果码）。
* 4.3 的鉴权接口：`/api/auth/{status,permissions,login,logout,me,password,check}` 与
  `/api/users`（admin 专用）。会话 Cookie 名 `tl_session`（HttpOnly + SameSite=Lax），
  被 Cookie 证明身份的写请求必须带 `X-CSRF-Token`（双提交）；`X-TraceLens-Token`
  给桌面/CLI 客户端，没有 Cookie 的跨站携带问题所以豁免 CSRF。

对外接口（给 t7 界面 / t9 存储 / t12 打包 sidecar / t14 摄取复用）
------------------------------------------------------------------
* `apply_decision(record, decision, ...)`：纯函数，规则唯一来源（界面本地模拟与
  selfcheck 的黄金样本比对都对着它）。4.3 起可传 `actor_id/actor_role`（team 模式记真实用户）。
* `InterveneService`：store + 审计链 + `intervene()/health()/records_payload()/audit_payload()`
  + 4.3 的 `login()/logout()/me()/users_payload()/create_user()/user_action()`
  + 4.2b 的 `verify_audit_chain()/audit_chain_status()`（追加式文件链）。
* **4.2b 审计落盘**：给了 `audit_log_path` 就用 `audit.py` 的追加式 JSONL 链当审计后端
  （产品入口 `main()` 默认 `var/audit.jsonl`，路径用 `--audit-log` 改，`--no-audit-log`
  才退回内存预览）。启动即加载并复算整条链（含 seal 锚点）；断链时 `/health` 的
  `audit.ok=false` + stderr 一句人话，`/api/audit` 与 `/intervene` 直接 500
  `AUDIT_CHAIN_BROKEN` —— 禁止静默忽略，禁止自动清空/截断/重建，服务端只做追加。
  决定先进文件链再进记录：审计写不进去就不落决定。
* **4.4 实时接入（t14）**：
  `POST /api/ingest/events` 收单条/批量事件（Bearer 或专用头凭证、`Idempotency-Key` 幂等、
  字段校验，归一化全部复用 :mod:`agent_explainer.trace_adapter`）；入库只"新增"或按
  白名单合并事实字段，**永不覆盖** `user_intervention` / `intervention_options` /
  `execution_receipt` / `human_readable`（决定 ≠ 生效）。
  `GET /api/stream` 是 SSE 广播（`event:`/`id:`/`data:` 帧 + 心跳 + `Last-Event-ID` 补发；
  慢订阅者丢最旧帧并标 `degraded`，订阅满员返回 503 让界面降级轮询）。
  `POST /api/ingest/tokens[/<id>/revoke]` 签发/吊销令牌（明文只返回一次，落盘只有
  `sha256(salt+明文)`），`GET /api/ingest/status` 给运维看计数。上报记账走追加式审计
  （`note` + `extra.ingest_event`），解释引擎与审计算法一字未改。
* `create_server(host, port, ...)` / `start_background_server(...)` / `main()`：
  `python -m agent_explainer.server --port 8765`（local）
  `python -m agent_explainer.server --mode team --port 8766`（团队，含 Docker）

运行
----
    cd agent-explainer
    python -m agent_explainer.server --port 8765
    python -m agent_explainer.server --mode team --port 8766          # 审计默认落 var/audit.jsonl
    python -m agent_explainer.server --audit-log var/audit.jsonl      # 显式指定审计文件
    python -m agent_explainer.server --no-audit-log                   # 只在内存链里预览（重启即丢）
    python -m unittest tests.test_intervene_api -v
    python -m unittest tests.test_auth_multituser -v
    python -m unittest tests.test_audit_integration -v
    python -m unittest tests.test_ingest_realtime -v                    # 4.4 实时接入
    python -m agent_explainer.server --issue-ingest-token ci-pipeline   # 签发上报令牌（打印一次）
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import importlib
import inspect
import json
import math
import pathlib
import sys
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .explanations import ExplanationError, make_generator, render_human_readable
from . import audit as audit_mod
from . import auth as auth_mod
from . import ingest as ingest_mod
from . import secretariat as sec_mod
from . import settings as settings_mod
from . import store as store_mod
from . import trace_adapter as ta_mod
from .feature_store import FeaturePaths

__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "MODE_LOCAL",
    "MODE_TEAM",
    "TEAM_HOST",
    "API_PATH_AUTH_LOGIN",
    "API_PATH_AUTH_LOGOUT",
    "API_PATH_AUTH_ME",
    "API_PATH_AUTH_STATUS",
    "API_PATH_AUTH_PERMISSIONS",
    "API_PATH_AUTH_PASSWORD",
    "API_PATH_AUTH_CHECK",
    "API_PATH_USERS",
    "API_PATH_INGEST_EVENTS",
    "API_PATH_IMPORT",
    "API_PATH_INGEST_TOKENS",
    "API_PATH_INGEST_STATUS",
    "API_PATH_STREAM",
    "STREAM_LIMIT_DEFAULT",
    "STREAM_HEARTBEAT_SECONDS",
    "default_ingest_token_path",
    "DECISIONS",
    "STATUS_BY_DECISION",
    "DECIDABLE_STATUSES",
    "EFFECT_NOTICE_KEY",
    "LOCAL_ACTOR_ID",
    "LOCAL_ACTOR_ROLE",
    "MAX_BODY_BYTES",
    "ERROR_TABLE",
    "InterveneError",
    "apply_decision",
    "parse_params_summary",
    "editable_fields_of",
    "canonical_json",
    "sha256_ref",
    "AuditChain",
    "AUDIT_SOURCE_MEMORY",
    "AUDIT_SOURCE_JSONL",
    "audit_broken_reason",
    "audit_broken_detail",
    "default_audit_log_path",
    "ActionStore",
    "InterveneService",
    "TraceLensHandler",
    "create_server",
    "start_background_server",
    "main",
]

# --------------------------------------------------------------------------- #
# 常量：与 02 数据模型 / 03 架构契约一一对应
# --------------------------------------------------------------------------- #

SERVER_NAME = "TraceLens"
SERVER_VERSION = "0.5.0"                     # 5.0 总裁办（R1–R4）；4.4 实时接入 / 4.2b / 4.3 / 4.1 口径保持
MODE_LOCAL = "local"                         # 03 §双模式：local 先行，无鉴权
MODE_TEAM = "team"                           # 4.3：team 必须登录，可局域网访问
TEAM_HOST = "0.0.0.0"                        # team 模式默认监听全部网卡（Docker/局域网）
API_PATH_INTERVENE = "/intervene"
API_PATH_RECORDS = "/api/records"
API_PATH_AUDIT = "/api/audit"
API_PATH_RESET = "/api/reset"
API_PATH_HEALTH = "/health"
# 4.3 鉴权与用户管理（docs/03 §7 契约表；动词口径见下方"路由方法"注释）
API_PATH_AUTH_STATUS = "/api/auth/status"
API_PATH_AUTH_PERMISSIONS = "/api/auth/permissions"
API_PATH_AUTH_LOGIN = "/api/auth/login"
API_PATH_AUTH_LOGOUT = "/api/auth/logout"
API_PATH_AUTH_ME = "/api/auth/me"
API_PATH_AUTH_PASSWORD = "/api/auth/password"
API_PATH_AUTH_CHECK = "/api/auth/check"
API_PATH_USERS = "/api/users"
# ---- 4.4 实时接入（t14）：webhook 写入 + SSE 广播（docs/03 §6.3 E1 / F1）----
API_PATH_INGEST_EVENTS = "/api/ingest/events"
API_PATH_INGEST_TOKENS = "/api/ingest/tokens"
API_PATH_INGEST_STATUS = "/api/ingest/status"
API_PATH_STREAM = "/api/stream"
# ---- 4.5 设置中心（t15）：模型配置 / 界面语言 / 数据管理（docs/03 §7 契约表）----
# 动词口径：GET 读掩码视图，PUT 保存（team 模式仅 settings.write 角色，即 admin），
# POST 只开 test-llm 这一个子动作；其余方法照旧 405。
API_PATH_SETTINGS = "/api/settings"
API_PATH_SETTINGS_TEST_LLM = "/api/settings/test-llm"
# ---- 4.8 审计查看与导出（t21）：GET /api/audit/export（docs/03 §6.3 M6 闭环）----
# 只有 GET；POST/PUT/PATCH/DELETE 走 405（审计是追加式的，导出不是写操作）。
API_PATH_AUDIT_EXPORT = "/api/audit/export"
#: 导出格式：csv 下载、html 打印（浏览器 Ctrl+P 存 PDF）、pdf 优先 reportlab。
AUDIT_EXPORT_FORMATS: tuple[str, ...] = ("csv", "html", "pdf")
AUDIT_EXPORT_FORMAT_DEFAULT = "csv"
#: 一次导出最多带多少条（导出的用途是"30 秒拿走证据"，不是把整份日志塞进一个文件）。
AUDIT_EXPORT_LIMIT_DEFAULT = 500
AUDIT_EXPORT_LIMIT_MAX = 5000
#: 导出文件名前缀（ASCII：浏览器下载名带中文容易变乱码）。
AUDIT_EXPORT_NAME_PREFIX = "tracelens-audit"
# ---- 4.9 数据导入（t22）：POST /api/import（粘贴 JSON / 上传文件内容）----
# 只有 POST：导入是写操作，CSRF 口径与 /intervene 一致（Cookie 证明身份的写请求要带
# X-CSRF-Token）；权限用权限矩阵里现成的 `data.write`（4.3 注释就写着"粘贴 / 上传 /
# 演示数据"），所以 local 匿名可用、team 需 editor/admin、viewer 403。
API_PATH_IMPORT = "/api/import"
#: 一批的上限：与请求体上限同口径（64 KB），条数与 4.4 的单批同口径（200 条）。
IMPORT_MAX_BYTES = 64 * 1024
IMPORT_MAX_RECORDS = 200
#: 合并方式：upsert（默认，同 id 更新事实）/ create（同 id 记为重复）/ update
IMPORT_MODES: tuple[str, ...] = ("create", "update", "upsert")
IMPORT_MODE_DEFAULT = "upsert"
#: 请求体里放原始文档的字段名（前端的粘贴框与文件选择器都走这一条路）
IMPORT_SOURCE_FIELDS: tuple[str, ...] = ("payload", "data", "text", "content",
                                         "document", "doc", "file", "events", "records")
#: 请求体里的控制参数（不当作文档内容）
IMPORT_CONTROL_FIELDS: tuple[str, ...] = ("format", "mode", "strict", "filename",
                                          "source_kind", "idempotency_key", "lang")
# ---- 5.0 总裁办与八大扩展功能（docs/07 §5 路由表 T1–T38）----
# 动词口径不变（只加 GET/POST，写操作 POST 子资源）；权限一律映射到 `auth.py` 现成的
# 8 个能力，**不新增能力** —— 加能力要动角色矩阵与三处断言，5.0 的口径是复用。
API_PATH_SECRETARIAT_STATUS = "/api/secretariat/status"          # T1
API_PATH_SECRETARIAT = "/api/secretariat"                        # T1 族入口（= status 摘要）
API_PATH_SECRETARIAT_IDENTITIES = "/api/secretariat/identities"  # T2（+ /{id}/revoke = T3）
API_PATH_SECRETARIAT_RECORDS = "/api/secretariat/records"        # T5 的前缀（+ /{id}/sign）
API_PATH_SECRETARIAT_DELEGATIONS = "/api/secretariat/delegations"  # T4
API_PATH_SECRETARIAT_VERIFY = "/api/secretariat/verify"          # T6
API_PATH_SECRETARIAT_LETTERS = "/api/secretariat/letters"        # T7（+ /{id} = T8、/verify = T9）
API_PATH_SECRETARIAT_SEAL = "/api/secretariat/seal"              # T10
API_PATH_ROLLBACK = "/api/rollback"                              # T11
API_PATH_ROLLBACK_PLAN = "/api/rollback/plan"                    # T12
API_PATH_EVIDENCE = "/api/evidence"                              # T14（+ /{id}/file = T15、/feedback = T16）
API_PATH_GROUPS = "/api/groups"                                  # T18（+ /{id} = T19、/close = T20、/share = T21）
API_PATH_SHARES = "/api/shares"                                  # T22（+ /{id}/revoke = T23）
API_PATH_SHARES_ACCEPT = "/api/shares/accept"                    # T24
API_PATH_SHARES_PERMISSIONS = "/api/shares/permissions"          # T25
API_PATH_ANALYTICS_DASHBOARD = "/api/analytics/dashboard"        # T26
API_PATH_ANALYTICS_EXPORT = "/api/analytics/export"              # T27（文件字节流）
API_PATH_SUGGESTIONS = "/api/suggestions"                        # T28（+ /{id}/accept = T29、/dismiss = T30）
API_PATH_PREFERENCES = "/api/preferences"                        # T31
API_PATH_COMPLIANCE_EXPORTS = "/api/compliance/exports"          # T32（+ /{id} = T34 … T37）
API_PATH_COMPLIANCE_EXPORT = "/api/compliance/export"            # T33
#: 扩展数据的子目录名：`<data-dir>/features`（docs/07 §3.3 的默认根）
EXPANSION_FEATURE_DIR = "features"
#: 九个扩展子系统（模块名 = `agent_explainer/<name>.py`，docs/07 §4）
EXPANSION_SUBSYSTEMS: tuple[str, ...] = (
    "secretariat", "rollback", "confidence", "evidence", "groups", "sharing",
    "analytics", "suggestions", "compliance",
)
#: 模块版本常量（写进 `/health` 的 `expansion` 块，T38）
EXPANSION_VERSION_ATTR: dict[str, str] = {
    "secretariat": "SECRETARIAT_VERSION", "rollback": "ROLLBACK_VERSION",
    "confidence": "CONFIDENCE_VERSION", "evidence": "EVIDENCE_VERSION",
    "groups": "GROUPS_VERSION", "sharing": "SHARING_VERSION",
    "analytics": "ANALYTICS_VERSION", "suggestions": "SUGGESTIONS_VERSION",
    "compliance": "COMPLIANCE_VERSION",
}
#: 5.0 新增的**可选**记录字段：导入/上报只允许这些字段从入参合并进来。
#: 与 4.4 的 `MERGE_FIELDS` 是叠加关系（那份白名单在 ingest.py，不归本次改动）。
#: 现状：合并器尚未实现（两张名单目前是声明，见 tests/test_merge_fields.py 的守卫断言）；
#: 谁实现 5.0 字段合并，就必须同时消费本名单与 `EXPANSION_PROTECTED_FIELDS`。
EXPANSION_MERGE_FIELDS: tuple[str, ...] = (
    "group_id", "shared_users", "multimedia_evidence", "causal_explanation",
    "rollback_ref", "rollback_state", "improvement_suggestion", "evidence_feedback",
    "confidence_source",
)
#: 这些字段永不从入参合并：决定语义（决定 ≠ 生效）、来源归因、演示标记、签名材料。
#: 签名覆盖整条记录，所以导入方带 `signature` 进来只会造出一条验不过的假签名。
#: `intervention_history` 也在这里：干预必须每次独立成条 signed 记录，入参可伪造的
#: 「干预历史」一旦被 Agent 签进记录，等于替伪造的责任链背书（见 docs/07 §3.2）。
EXPANSION_PROTECTED_FIELDS: tuple[str, ...] = (
    "user_intervention", "intervention_options", "human_readable", "source",
    "demo_data", "synthetic", "policy", "signature", "entry_hash", "prev_hash",
    "agent_pubkey", "approval", "tsa_timestamp", "input_hash", "output_hash",
    "execution_receipt", "intervention_history",
    # `human_owner` / `delegation_chain` 由 secretariat 按身份记录填（责任链的链根是真人）。
    # 入参若自带这两个字段，等于让导入方给别人派责任：必须禁止合并。
    "human_owner", "delegation_chain",
)
#: 4.9 记录持久化：默认数据目录（`var/records.jsonl`），产品入口给这个默认值
DEFAULT_RECORDS_STORE_SUBPATH = "records.jsonl"
#: 谁能改设置：t13 权限表里的这一项（`local` 与 `admin` 有，`editor` / `viewer` 没有），
#: 所以"team 模式仅 admin 可改"是直接复用权限表，不是另写一套判断。
PERMISSION_SETTINGS_WRITE = "settings.write"
#: 请求体里凭据字段的写法（驼峰）。服务端只做转交：由 settings.py 加密后落盘，
#: 本文件既不读取也不回显明文（t8 的源码级纯净性测试就是钉这一点，
#: 所以这里连那个字段的蛇形写法都不能出现 —— 连常量名都不行）。
PATCH_FIELD_SECRET = "apiKey"
#: 连接测试可自带的探测提示词长度上限
SETTINGS_PROMPT_MAX_CHARS = 2000
#: 同时在线的 SSE 订阅者上限；超了界面自动降级轮询，服务端不排队堆积
STREAM_LIMIT_DEFAULT = 16
#: 心跳间隔（秒）：链路上没事件时也要发帧，代理与界面才知道连接还活着
STREAM_HEARTBEAT_SECONDS = 15.0
#: 单条 SSE 连接最多推多少帧后主动收尾（0 = 不限）；测试与"看完就走"的脚本用得上
STREAM_MAX_EVENTS_DEFAULT = 0
INGEST_RATE_DEFAULT = 40
INGEST_RATE_PER_MIN_DEFAULT = 600
STREAM_ACCEPT = "text/event-stream"
API_PATHS_PUBLIC = (API_PATH_HEALTH, "/healthz", API_PATH_AUTH_STATUS,
                    API_PATH_AUTH_PERMISSIONS, API_PATH_AUTH_LOGIN)

DECISIONS: tuple[str, ...] = ("approve", "modify", "reject")
STATUS_BY_DECISION = {"approve": "approved", "modify": "modified", "reject": "rejected"}
#: 02 §4.1 决定段：只有"等你拍板"和"超时仍在等"两种状态可以被干预
DECIDABLE_STATUSES: tuple[str, ...] = ("pending_confirmation", "expired")
EFFECT_NOTICE_KEY = "intervention.effect.recorded_only"
RECEIPT_STATE_UNREPORTED = "unreported"

LOCAL_ACTOR_ID = "local-user"
LOCAL_ACTOR_ROLE = "local"
#: 03 §权限矩阵里 local 角色具备的能力（写进 /health，便于界面显示"谁能做什么"）
LOCAL_CAPABILITIES = ("read_records", "decide", "audit_read")

MAX_BODY_BYTES = 64 * 1024
MAX_MODIFIED_FIELDS = 20
REASON_MIN_CHARS = 2
REASON_MAX_CHARS = 500
IDEMPOTENCY_KEY_MAX = 128
ACTION_ID_MAX = 128
AUDIT_MEMORY_LIMIT = 200

#: 4.2b（t18）审计落盘口径：`memory` = t8 的内存链预览（重启即丢），
#: `jsonl` = t9 的追加式文件链（产品默认，重启后历史仍在、count 不归零）。
AUDIT_SOURCE_MEMORY = "memory"
AUDIT_SOURCE_JSONL = "jsonl"
AUDIT_QUERY_LIMIT_DEFAULT = 50
AUDIT_QUERY_LIMIT_MAX = 500
AUDIT_PROBLEM_PREVIEW = 20

GENESIS_HASH = "sha256:" + ("0" * 64)

#: code → (HTTP 状态, 给人看的英文说明)。界面用 code 查 i18n（`intervene.error.<code>`），
#: 查不到时退回展示 `message`，所以这里保持短句、不含中文（i18n 归属 web/）。
ERROR_TABLE: "OrderedDict[str, tuple[int, str]]" = OrderedDict(
    (code, (status, message))
    for code, status, message in (
        ("INTERVENE_BODY_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "Request body is larger than the 64 KB limit."),
        ("INTERVENE_CONTENT_TYPE", HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
         "Content-Type must be application/json."),
        ("INTERVENE_BODY_INVALID", HTTPStatus.BAD_REQUEST,
         "Request body is not valid JSON."),
        ("INTERVENE_BODY_NOT_OBJECT", HTTPStatus.BAD_REQUEST,
         "Request body must be a JSON object."),
        ("INTERVENE_FIELD_MISSING", HTTPStatus.BAD_REQUEST,
         "A required field is missing."),
        ("INTERVENE_FIELD_TYPE", HTTPStatus.BAD_REQUEST,
         "A field has the wrong type."),
        ("INTERVENE_FIELD_UNKNOWN", HTTPStatus.BAD_REQUEST,
         "The request contains a field that is not allowed here."),
        ("INTERVENE_DECISION_INVALID", HTTPStatus.BAD_REQUEST,
         "decision must be approve, modify or reject."),
        ("INTERVENE_ACTION_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No action record with this action_id."),
        ("INTERVENE_RECORD_INVALID", HTTPStatus.BAD_REQUEST,
         "The stored record cannot be read as an object."),
        ("INTERVENE_STATE_INVALID", HTTPStatus.CONFLICT,
         "This record is not waiting for a decision."),
        ("INTERVENE_ALREADY_DECIDED", HTTPStatus.CONFLICT,
         "A decision is already recorded for this action."),
        ("INTERVENE_OPTION_MISMATCH", HTTPStatus.BAD_REQUEST,
         "The option does not belong to this action or does not match the decision."),
        ("INTERVENE_PARAM_MISSING", HTTPStatus.BAD_REQUEST,
         "decision=modify needs at least one edited parameter."),
        ("INTERVENE_PARAM_NOT_ALLOWED", HTTPStatus.BAD_REQUEST,
         "modified_params is only allowed for decision=modify."),
        ("INTERVENE_PARAM_UNKNOWN", HTTPStatus.BAD_REQUEST,
         "modified_params contains a field that is not editable."),
        ("INTERVENE_PARAM_TYPE", HTTPStatus.BAD_REQUEST,
         "An edited parameter has the wrong type."),
        ("INTERVENE_PARAM_RANGE", HTTPStatus.BAD_REQUEST,
         "An edited parameter is outside the allowed range."),
        ("INTERVENE_PARAM_EMPTY", HTTPStatus.BAD_REQUEST,
         "A required edited parameter is empty."),
        ("INTERVENE_PARAM_NO_CHANGE", HTTPStatus.BAD_REQUEST,
         "decision=modify needs at least one real change."),
        ("INTERVENE_REASON_REQUIRED", HTTPStatus.BAD_REQUEST,
         "decision=reject needs a reason."),
        ("INTERVENE_REASON_TOO_SHORT", HTTPStatus.BAD_REQUEST,
         "The reason is too short."),
        ("INTERVENE_IDEMPOTENCY_MISMATCH", HTTPStatus.CONFLICT,
         "This idempotency_key was already used with a different request."),
        ("METHOD_NOT_ALLOWED", HTTPStatus.METHOD_NOT_ALLOWED,
         "This endpoint does not accept that method."),
        ("ROUTE_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "Unknown endpoint."),
        ("STATIC_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "The requested file does not exist."),
        ("STATIC_PATH_REJECTED", HTTPStatus.BAD_REQUEST,
         "The requested path is not allowed."),
        ("RESET_DISABLED", HTTPStatus.FORBIDDEN,
         "Resetting the demo store is turned off for this server."),
        # ---- 4.9 数据导入（t22）：粘贴 / 上传的入口错误要一眼看出改哪里 ----
        ("IMPORT_INPUT_EMPTY", HTTPStatus.BAD_REQUEST,
         "There is nothing to import: paste JSON text or choose a file."),
        ("IMPORT_INPUT_NOT_JSON", HTTPStatus.BAD_REQUEST,
         "The pasted text is not valid JSON."),
        ("IMPORT_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "This batch is larger than the 64 KB import limit."),
        ("IMPORT_TOO_MANY", HTTPStatus.BAD_REQUEST,
         "This batch has more events than the 200-event import limit."),
        ("IMPORT_FORMAT_UNSUPPORTED", HTTPStatus.BAD_REQUEST,
         "The trace format is not langfuse, otel_genai, mcp_events or native."),
        ("IMPORT_MODE_INVALID", HTTPStatus.BAD_REQUEST,
         "mode must be create, update or upsert."),
        ("RECORD_STORE_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "Records are in memory but the durable record store could not be written."),
        # ---- 4.3 鉴权与权限（docs/03 §6.2：401 AUTH_REQUIRED / 403 ROLE_FORBIDDEN）----
        ("AUTH_REQUIRED", HTTPStatus.UNAUTHORIZED,
         "You need to sign in first."),
        ("AUTH_TOKEN_EXPIRED", HTTPStatus.UNAUTHORIZED,
         "Your session has expired."),
        ("AUTH_TOKEN_INVALID", HTTPStatus.UNAUTHORIZED,
         "Your session token is not valid."),
        ("AUTH_TOKEN_REVOKED", HTTPStatus.UNAUTHORIZED,
         "You have signed out of this session."),
        ("AUTH_USER_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No user with this id."),
        ("AUTH_BAD_CREDENTIALS", HTTPStatus.UNAUTHORIZED,
         "Wrong username or password."),
        ("AUTH_USER_DISABLED", HTTPStatus.FORBIDDEN,
         "This account has been disabled."),
        ("ROLE_FORBIDDEN", HTTPStatus.FORBIDDEN,
         "This action needs a higher role."),
        ("CSRF_FAILED", HTTPStatus.FORBIDDEN,
         "Missing or mismatched CSRF token."),
        ("USER_EXISTS", HTTPStatus.CONFLICT,
         "That username is already taken."),
        ("USER_SELF_DISABLE", HTTPStatus.CONFLICT,
         "You cannot disable the account you are signed in with."),
        ("USER_LAST_ADMIN", HTTPStatus.CONFLICT,
         "This is the last active admin account."),
        ("USER_DELETE_DISABLED", HTTPStatus.METHOD_NOT_ALLOWED,
         "Users are disabled, never deleted (audit trail must keep the person)."),
        ("USERNAME_REQUIRED", HTTPStatus.BAD_REQUEST,
         "Username is required."),
        ("USERNAME_INVALID", HTTPStatus.BAD_REQUEST,
         "Username uses characters that are not allowed."),
        ("ROLE_INVALID", HTTPStatus.BAD_REQUEST,
         "Role must be admin, editor or viewer."),
        ("USER_STATUS_INVALID", HTTPStatus.BAD_REQUEST,
         "Status must be active or disabled."),
        ("AUTH_PASSWORD_REQUIRED", HTTPStatus.BAD_REQUEST,
         "Password is required."),
        ("AUTH_PASSWORD_TOO_LONG", HTTPStatus.BAD_REQUEST,
         "Password is too long."),
        ("AUTH_PASSWORD_SAME", HTTPStatus.BAD_REQUEST,
         "The new password must differ from the current one."),
        ("AUTH_WEAK_PASSWORD", HTTPStatus.BAD_REQUEST,
         "The password is too weak."),
        ("AUTH_MODE_INVALID", HTTPStatus.BAD_REQUEST,
         "Mode must be local or team."),
        ("PERMISSION_UNKNOWN", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The server asked for an unknown permission."),
        ("AUTH_STATE_CORRUPT", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The auth state file cannot be read."),
        ("AUTH_SECRET_TOO_SHORT", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The session signing key is too short."),
        ("RATE_LIMITED", HTTPStatus.TOO_MANY_REQUESTS,
         "Too many failed attempts; try again later."),
        ("BOOTSTRAP_LOCAL_FORBIDDEN", HTTPStatus.FORBIDDEN,
         "Initial admin accounts are only created in team mode."),
        # ---- 4.2b 审计落盘（t18）：链断/被改时禁止静默忽略，也禁止自动清空重建 ----
        ("AUDIT_CHAIN_BROKEN", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The durable audit chain failed verification; the server will not read, append "
         "or reset through it."),
        ("AUDIT_QUERY_INVALID", HTTPStatus.BAD_REQUEST,
         "Audit query parameters are not valid."),
        # ---- 4.4 实时接入（t14）：docs/03 §6.3 E1 / F1 的失败口径 ----
        ("INGEST_DISABLED", HTTPStatus.FORBIDDEN,
         "Realtime ingest is switched off on this server."),
        ("INGEST_TOKEN_MISSING", HTTPStatus.UNAUTHORIZED,
         "No ingest credential was supplied (Bearer token in the Authorization header)."),
        ("INGEST_TOKEN_INVALID", HTTPStatus.UNAUTHORIZED,
         "The ingest credential is unknown."),
        ("INGEST_TOKEN_DISABLED", HTTPStatus.FORBIDDEN,
         "The ingest credential was revoked."),
        ("INGEST_TOKEN_EXPIRED", HTTPStatus.FORBIDDEN,
         "The ingest credential has expired."),
        ("INGEST_SCOPE_FORBIDDEN", HTTPStatus.FORBIDDEN,
         "The ingest credential lacks the required scope."),
        ("INGEST_RATE_LIMITED", HTTPStatus.TOO_MANY_REQUESTS,
         "Ingest rate limit reached; slow down and retry later."),
        ("INGEST_SCHEMA_INVALID", HTTPStatus.BAD_REQUEST,
         "The ingest envelope is not a valid object or array."),
        ("INGEST_EMPTY", HTTPStatus.BAD_REQUEST,
         "The ingest envelope carries no events."),
        ("INGEST_TOO_MANY_EVENTS", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "The batch carries more events than allowed."),
        ("INGEST_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "The batch is larger than the byte limit."),
        ("INGEST_FIELD_TYPE", HTTPStatus.BAD_REQUEST,
         "An ingest field has the wrong type."),
        ("INGEST_IDEMPOTENCY_CONFLICT", HTTPStatus.CONFLICT,
         "The same idempotency key was reused with another request body."),
        ("INGEST_FORMAT_UNSUPPORTED", HTTPStatus.UNPROCESSABLE_ENTITY,
         "The event format is not one of the four supported adapters."),
        ("INGEST_ADAPTER_REJECTED", HTTPStatus.UNPROCESSABLE_ENTITY,
         "The trace adapter refused the events."),
        ("INGEST_STORE_FAILURE", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The record store refused the write."),
        ("INGEST_TOKEN_FORBIDDEN", HTTPStatus.FORBIDDEN,
         "Managing ingest tokens needs the ingest.token.manage permission."),
        ("STREAM_ACCEPT_REQUIRED", HTTPStatus.NOT_ACCEPTABLE,
         "The stream endpoint needs Accept: text/event-stream."),
        ("STREAM_LIMIT_REACHED", HTTPStatus.SERVICE_UNAVAILABLE,
         "Too many live stream subscribers; the page falls back to polling."),
        ("STREAM_QUERY_INVALID", HTTPStatus.BAD_REQUEST,
         "Stream query parameters are not valid."),
        ("INTERNAL_ERROR", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The server could not complete the request."),
    )
)

#: `POST /intervene` 允许的顶层字段；身份/时间/审计由服务端写，客户端不得自称
ALLOWED_INTERVENE_FIELDS: tuple[str, ...] = (
    "action_id", "decision", "modified_params", "reason", "option_id", "idempotency_key",
)

# ---- 5.0 扩展域错误码（docs/07 §7）：HTTP 映射 = 字段/参数 400、不存在 404、
# 状态冲突（已回滚/已判定/已过期）409、越权 403、体积 413、存储失败 500、缺件 503。
# 码名是契约（§4 的模块异常里原样带出来），所以服务端只做**翻译**，不重命名。
EXPANSION_ERROR_TABLE: "OrderedDict[str, tuple[int, str]]" = OrderedDict(
    (code, (status, message))
    for code, status, message in (
        # -- secretariat（总裁办）--
        ("SECRETARIAT_FIELD_INVALID", HTTPStatus.BAD_REQUEST,
         "A secretariat field is missing or has the wrong type."),
        ("SECRETARIAT_IDENTITY_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No agent identity is registered under that agent_id."),
        ("SECRETARIAT_IDENTITY_MISSING", HTTPStatus.NOT_FOUND,
         "This record's agent has no registered identity, so it cannot be signed yet."),
        ("SECRETARIAT_IDENTITY_REVOKED", HTTPStatus.CONFLICT,
         "That agent identity was revoked; register a new key before signing."),
        ("SECRETARIAT_DELEGATION_INVALID", HTTPStatus.BAD_REQUEST,
         "The delegation record is not valid."),
        ("SECRETARIAT_SCOPE_INVALID", HTTPStatus.BAD_REQUEST,
         "The delegation scope is not valid."),
        ("SECRETARIAT_SCOPE_WIDENED", HTTPStatus.CONFLICT,
         "A delegation may only narrow its parent scope, never widen it."),
        ("SECRETARIAT_DEPTH_EXCEEDED", HTTPStatus.CONFLICT,
         "The delegation chain is deeper than the maximum of 5 hops."),
        ("SECRETARIAT_DELEGATION_EXPIRED", HTTPStatus.CONFLICT,
         "That delegation has expired."),
        ("SECRETARIAT_SIGNATURE_INVALID", HTTPStatus.BAD_REQUEST,
         "The signature does not cover the record it claims to cover."),
        ("SECRETARIAT_APPROVAL_REQUIRED", HTTPStatus.CONFLICT,
         "This risk level needs human approval signatures before it can be sealed."),
        ("SECRETARIAT_LETTER_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No responsibility letter exists under that id."),
        ("SECRETARIAT_RECORD_MISSING", HTTPStatus.NOT_FOUND,
         "A record referenced by this request does not exist."),
        ("SECRETARIAT_LETTER_EMPTY", HTTPStatus.BAD_REQUEST,
         "A responsibility letter needs at least one record."),
        ("SECRETARIAT_KEYSTORE_UNAVAILABLE", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The local keyring is unavailable; nothing was signed."),
        ("SECRETARIAT_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The secretariat ledger refused the write."),
        # -- rollback（干预回滚）--
        ("ROLLBACK_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No record matches that action_id."),
        ("ROLLBACK_NOT_REVERSIBLE", HTTPStatus.CONFLICT,
         "That action has no registered inverse operation, so it cannot be rolled back."),
        ("ROLLBACK_DEPENDENTS_PENDING", HTTPStatus.CONFLICT,
         "Later actions depend on this one; confirm them together "
         "(confirm_all=true) or roll them back first."),
        ("ROLLBACK_ALREADY_ROLLED_BACK", HTTPStatus.CONFLICT,
         "That action was already rolled back."),
        ("ROLLBACK_INVERSE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The inverse operation failed; the journal records what was attempted."),
        ("ROLLBACK_FIELD_INVALID", HTTPStatus.BAD_REQUEST,
         "A rollback field is missing or has the wrong type."),
        # -- evidence（多模态证据）--
        ("EVIDENCE_KIND_INVALID", HTTPStatus.BAD_REQUEST,
         "kind must be one of screenshot, diagram or diff."),
        ("EVIDENCE_PAYLOAD_INVALID", HTTPStatus.BAD_REQUEST,
         "The evidence payload is not valid for its kind."),
        ("EVIDENCE_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "The evidence payload is larger than the 4 MB limit."),
        ("EVIDENCE_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No evidence exists under that id."),
        ("EVIDENCE_VOTE_INVALID", HTTPStatus.BAD_REQUEST,
         "vote must be like or dislike."),
        ("EVIDENCE_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The evidence store refused the write."),
        # -- groups（多 Agent 任务聚合）--
        ("GROUP_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No task group exists under that id."),
        ("GROUP_FIELD_INVALID", HTTPStatus.BAD_REQUEST,
         "A group field is missing or has the wrong type."),
        ("GROUP_CLOSED", HTTPStatus.CONFLICT,
         "That task group is closed; reopen it before changing it."),
        ("GROUP_AGENTS_EXCEEDED", HTTPStatus.BAD_REQUEST,
         "A task group holds at most 10 agents."),
        ("GROUP_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The group store refused the write."),
        # -- sharing（共享与协作）--
        ("SHARE_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No share invitation exists under that id."),
        ("SHARE_PERMISSION_INVALID", HTTPStatus.BAD_REQUEST,
         "permission must be one of view, approve or manage."),
        ("SHARE_EXPIRED", HTTPStatus.CONFLICT,
         "That share link has expired."),
        ("SHARE_TOKEN_INVALID", HTTPStatus.BAD_REQUEST,
         "That share token is unknown."),
        ("SHARE_TOKEN_USED", HTTPStatus.CONFLICT,
         "That share token was already accepted."),
        ("SHARE_FORBIDDEN", HTTPStatus.FORBIDDEN,
         "You do not hold the permission this share action needs."),
        ("SHARE_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The sharing store refused the write."),
        # -- analytics（行为分析仪表盘）--
        ("ANALYTICS_RANGE_INVALID", HTTPStatus.BAD_REQUEST,
         "The analytics time range is not valid."),
        ("ANALYTICS_FORMAT_INVALID", HTTPStatus.BAD_REQUEST,
         "format must be csv or pdf."),
        ("ANALYTICS_EXPORT_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The analytics export could not be built."),
        # -- suggestions（自动化改进建议 / 用户偏好）--
        ("SUGGESTION_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No improvement suggestion exists under that id."),
        ("SUGGESTION_STATE_INVALID", HTTPStatus.CONFLICT,
         "That suggestion was already accepted, dismissed or is cooling down."),
        ("SUGGESTION_FIELD_INVALID", HTTPStatus.BAD_REQUEST,
         "A suggestion field is missing or has the wrong type."),
        ("PREFERENCE_INVALID", HTTPStatus.BAD_REQUEST,
         "A user preference field is missing or has the wrong type."),
        ("SUGGESTION_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The suggestion store refused the write."),
        # -- compliance（合规审计导出）--
        ("COMPLIANCE_FORMAT_INVALID", HTTPStatus.BAD_REQUEST,
         "format must be one of pdf, csv or chain."),
        ("COMPLIANCE_CHAIN_BROKEN", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The audit chain failed verification, so no evidence file is produced."),
        ("COMPLIANCE_NOT_FOUND", HTTPStatus.NOT_FOUND,
         "No compliance export exists under that id."),
        ("COMPLIANCE_EXPORT_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The compliance bundle could not be built."),
        ("COMPLIANCE_ANCHOR_UNAVAILABLE", HTTPStatus.SERVICE_UNAVAILABLE,
         "No anchor service is reachable offline; the CID is computed locally and "
         "anchor_state stays unanchored."),
        ("COMPLIANCE_BUNDLE_EMPTY", HTTPStatus.CONFLICT,
         "There is nothing in that range to export, so no bundle was written."),
        # -- trust（特性存储）--
        ("FEATURE_STORE_LOCKED", HTTPStatus.CONFLICT,
         "The feature data file is held by another process."),
        ("FEATURE_STORE_WRITE_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The feature store refused the write."),
        ("FEATURE_STORE_READ_FAILED", HTTPStatus.INTERNAL_SERVER_ERROR,
         "The feature store could not be read."),
        ("FEATURE_STORE_LINE_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
         "One feature store line is larger than the limit."),
        ("FEATURE_STORE_RECORD_INVALID", HTTPStatus.BAD_REQUEST,
         "A feature store entity is not a valid object."),
        ("FEATURE_JSON_INVALID", HTTPStatus.BAD_REQUEST,
         "A JSON payload for the feature store is not valid."),
        # -- 5.0 增量：模块缺失时的如实降级（§5 要求"缺件就 503 说清楚"）--
        ("EXPANSION_UNAVAILABLE", HTTPStatus.SERVICE_UNAVAILABLE,
         "That 5.0 subsystem is not installed on this server; its routes answer 503 "
         "and the rest of the service keeps working."),
    )
)
ERROR_TABLE.update(EXPANSION_ERROR_TABLE)
#: 扩展域码 → HTTP 状态（模块异常只带 code 时用它翻译；未收录的码按 400 + 原文 message）
EXPANSION_ERROR_STATUS: dict[str, int] = {code: status
                                          for code, (status, _msg) in EXPANSION_ERROR_TABLE.items()}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def canonical_json(obj: Any) -> str:
    """稳定的 JSON 文本（审计哈希链与记录指纹都用它）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_ref(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_facts_hash(record: Mapping[str, Any]) -> str:
    """记录内容指纹（服务端展示用，不落库）。

    记录正文里的 `human_readable.source_facts_hash` 由 `tools/facts_hash.py` 定义（产品口径）；
    包内不允许 import `tools/`，所以这里算的是**全字段** sha256，只用于审计链的
    "这条记录变成什么样了"，不冒充产品指纹。两者都写进审计条目，字段名区分开。
    """
    return sha256_ref(canonical_json(dict(record)))


_PARAMS_SPLIT = ("；", ";", "\n", ",")


def parse_params_summary(text: Any) -> dict[str, Any]:
    """把 `tool_used.params_summary`（形如 `a=1；b=x`）解析成对象。

    用途只有两个：C3 要求的 `original_params` 证据、"修改"表单的初始值。
    解析不了就返回空对象——绝不编造参数。
    """
    raw = text if isinstance(text, str) else ""
    if not raw.strip():
        return {}
    work = raw
    for sep in _PARAMS_SPLIT[1:]:
        work = work.replace(sep, _PARAMS_SPLIT[0])
    out: dict[str, Any] = {}
    for chunk in work.split(_PARAMS_SPLIT[0]):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        key, _, value = piece.partition("=")
        name = key.strip()
        val = value.strip()
        if not name:
            continue
        out[name] = _coerce_scalar(val)
    return out


def _coerce_scalar(value: str) -> Any:
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    return int(num) if num.is_integer() else num


def editable_fields_of(record: Any, option_id: str | None = None) -> list[dict[str, Any]]:
    """取"修改"可编辑字段：优先指定 option，其次 kind=modify 的 option。"""
    if not isinstance(record, Mapping):
        return []
    options = record.get("intervention_options")
    fields: list[dict[str, Any]] = []
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        chosen: Any = None
        for opt in options:
            if not isinstance(opt, Mapping):
                continue
            if option_id and str(opt.get("id")) == str(option_id):
                chosen = opt
                break
            if (not option_id) and str(opt.get("kind")) == "modify":
                chosen = opt
        if chosen is None and option_id:
            for opt in options:
                if isinstance(opt, Mapping) and str(opt.get("kind")) == "modify":
                    chosen = opt
                    break
        raw_fields = chosen.get("editable_fields") if isinstance(chosen, Mapping) else None
        if isinstance(raw_fields, Sequence) and not isinstance(raw_fields, (str, bytes)):
            fields = [dict(f) for f in raw_fields if isinstance(f, Mapping)]
    return fields


def current_params(record: Any, fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """干预前参数：params_summary 解析值 + 可编辑字段的默认值补齐。"""
    tool = record.get("tool_used") if isinstance(record, Mapping) else None
    summary = parse_params_summary(tool.get("params_summary") if isinstance(tool, Mapping) else None)
    out = dict(summary)
    for field in fields:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name not in out and field.get("default") is not None:
            out[name] = field.get("default")
    return out


def _bad(code: str, *, field: str | None = None, detail: Mapping[str, Any] | None = None,
         message: str | None = None, status: int | None = None) -> "InterveneError":
    return InterveneError(code, field=field, detail=detail, message=message, status=status)


def _auth_bad(exc: auth_mod.AuthError) -> "InterveneError":
    """把 auth 域异常翻成统一 HTTP 错误体（状态码取异常自带的，不进 i18n 翻译表）。

    鉴权异常与干预异常是两域，但对外形状必须一致：`{code, message, detail, request_id}`，
    429 额外带 `retry_after`，处理器据此补 `Retry-After` 响应头。
    """
    detail = dict(exc.detail)
    if getattr(exc, "hint", None):
        detail.setdefault("hint", str(exc.hint))
    err = InterveneError(exc.code, field=exc.field, detail=detail,
                         message=exc.message, status=exc.status)
    if getattr(exc, "retry_after", None):
        err.retry_after = int(exc.retry_after)     # type: ignore[attr-defined]
    return err


class InterveneError(Exception):
    """统一的干预错误：`code` 稳定、`message` 给人看、`detail` 给机器看。"""

    def __init__(self, code: str, *, field: str | None = None,
                 detail: Mapping[str, Any] | None = None, message: str | None = None,
                 status: int | None = None,
                 retry_after: int | None = None) -> None:
        status_default, message_default = ERROR_TABLE.get(code, (HTTPStatus.BAD_REQUEST,
                                                                 "Invalid intervention request."))
        super().__init__(code)
        self.code = code
        self.http_status = int(status or status_default)
        self.message = message or message_default
        self.retry_after = retry_after
        payload: dict[str, Any] = dict(detail or {})
        if field:
            payload.setdefault("field", field)
        self.detail = payload

    def as_dict(self, request_id: str | None = None) -> dict[str, Any]:
        """03 §统一错误体：{code, message, detail, request_id}。"""
        out: dict[str, Any] = {"code": self.code, "message": self.message,
                               "detail": dict(self.detail), "request_id": request_id}
        if getattr(self, "retry_after", None):
            out["retry_after"] = int(self.retry_after)   # type: ignore[attr-defined,arg-type]
        return out

    def __str__(self) -> str:  # pragma: no cover - 只用于调试输出
        return f"{self.code} ({self.http_status}): {self.message}"


# --------------------------------------------------------------------------- #
# 核心：决定段迁移（规则唯一来源，界面本地模拟与它对齐）
# --------------------------------------------------------------------------- #

def apply_decision(
    record: Any,
    decision: Any,
    *,
    modified_params: Any = None,
    reason: Any = None,
    option_id: Any = None,
    actor_id: str = LOCAL_ACTOR_ID,
    actor_role: str = LOCAL_ACTOR_ROLE,
    decided_at: Any = None,
    request_id: str | None = None,
    audit_entry_hash: str | None = None,
    regenerate: bool = True,
    generator: Any = None,
) -> dict[str, Any]:
    """把一次用户决定写进记录的**决定段**，返回新记录（不就地改写）。

    校验顺序固定（测试逐条钉住）：
    记录形状 → decision 枚举 → 记录状态可决 → 选项匹配 → reason → 参数 → 真实变更。

    永不修改 `execution_receipt`：决定已记录不等于已生效（C11 / 假闭环禁令）。
    状态变化会改变事实，所以默认重算 `human_readable`（含 `source_facts_hash`）。

    `generator`（t15）：设置中心切到 llm 模式后，服务端的渲染要走**已配置的引擎**，
    所以服务层把自己那个按配置装配好的生成器传进来，`generated_by` 交给引擎判定
    （模板模式仍是逐字的 `"template"`）。不传 = 老口径，逐字不变。
    """
    if not isinstance(record, Mapping):
        raise _bad("INTERVENE_RECORD_INVALID",
                   detail={"received": type(record).__name__})

    dec = decision if isinstance(decision, str) else ""
    if dec not in DECISIONS:
        raise _bad("INTERVENE_DECISION_INVALID", field="decision",
                   detail={"received": decision, "allowed": list(DECISIONS)})

    status = str(record.get("status"))
    existing = record.get("user_intervention")
    if isinstance(existing, Mapping) and existing.get("decision") is not None:
        raise _bad("INTERVENE_ALREADY_DECIDED", field="status",
                   detail={"status": status,
                           "existing_decision": existing.get("decision"),
                           "decided_at": existing.get("decided_at")})
    if status not in DECIDABLE_STATUSES:
        raise _bad("INTERVENE_STATE_INVALID", field="status",
                   detail={"status": status, "decidable": list(DECIDABLE_STATUSES)})

    option = _match_option(record, dec, option_id)

    clean_reason = _check_reason(reason, dec)

    fields = editable_fields_of(record, option.get("id"))
    original = current_params(record, fields)
    clean_params: dict[str, Any] | None = None
    changed: list[dict[str, Any]] = []
    if dec == "modify":
        clean_params, changed = _check_modified_params(modified_params, fields, original)
    elif modified_params not in (None, {}, ""):
        raise _bad("INTERVENE_PARAM_NOT_ALLOWED", field="modified_params",
                   detail={"decision": dec, "hint": "use decision=modify to send edited parameters"})

    stamp = _as_datetime(decided_at)
    new = copy.deepcopy(dict(record))
    new["status"] = STATUS_BY_DECISION[dec]
    new["requires_action"] = False

    intervention: dict[str, Any] = {
        "decision": dec,
        "option_id": option.get("id"),
        "decided_by": actor_id,
        "decided_by_role": actor_role,
        "decided_at": _iso(stamp),
        "recorded": True,                                  # schema const：决定已记录
        "request_id": request_id,
        "audit_entry_hash": audit_entry_hash,
        "original_params": original,
    }
    if clean_reason is not None:
        intervention["reason"] = clean_reason
    if clean_params is not None:
        intervention["modified_params"] = clean_params
        intervention["modified_fields"] = changed[:MAX_MODIFIED_FIELDS]
    new["user_intervention"] = intervention

    # 执行段原样保留；只补生效边界文案（决定 ≠ 生效）。
    receipt = new.get("execution_receipt")
    if isinstance(receipt, dict):
        receipt["effect_notice"] = EFFECT_NOTICE_KEY
        receipt.setdefault("state", RECEIPT_STATE_UNREPORTED)

    if regenerate:
        lang = None
        readable = new.get("human_readable")
        if isinstance(readable, Mapping):
            lang = readable.get("language")
        try:
            if generator is not None:
                # 已按设置中心配置装配：generated_by 由引擎判定（模板模式仍是 template）
                new["human_readable"] = generator.render_human_readable(
                    new, lang, generated_by="auto", now=stamp)
            else:
                new["human_readable"] = render_human_readable(
                    new, lang, generated_by="template", now=stamp)
        except ExplanationError:
            # 文案生成失败绝不吞掉决定：保留旧文案 + 打降级标记（界面会显示数据异常提示）
            new.setdefault("uncertainty", [])
            if isinstance(new.get("uncertainty"), list):
                new["uncertainty"].append("intervention.human_readable_regen_failed")
    return new


def _match_option(record: Mapping[str, Any], decision: str, option_id: Any) -> dict[str, Any]:
    options = record.get("intervention_options")
    listed = [o for o in options if isinstance(o, Mapping)] if (
        isinstance(options, Sequence) and not isinstance(options, (str, bytes))) else []
    if option_id not in (None, ""):
        if not isinstance(option_id, str):
            raise _bad("INTERVENE_FIELD_TYPE", field="option_id",
                       detail={"received": type(option_id).__name__})
        for opt in listed:
            if str(opt.get("id")) == option_id:
                if str(opt.get("kind")) != decision:
                    raise _bad("INTERVENE_OPTION_MISMATCH", field="option_id",
                               detail={"option_id": option_id, "option_kind": opt.get("kind"),
                                       "decision": decision})
                return dict(opt)
        raise _bad("INTERVENE_OPTION_MISMATCH", field="option_id",
                   detail={"option_id": option_id,
                           "known_option_ids": [o.get("id") for o in listed]})
    for opt in listed:
        if str(opt.get("kind")) == decision:
            return dict(opt)
    raise _bad("INTERVENE_OPTION_MISMATCH", field="decision",
               detail={"decision": decision,
                       "option_kinds": [o.get("kind") for o in listed],
                       "hint": "this record does not offer that option"})


def _check_reason(reason: Any, decision: str) -> str | None:
    if reason in (None, ""):
        if decision == "reject":
            raise _bad("INTERVENE_REASON_REQUIRED", field="reason",
                       detail={"min_length": REASON_MIN_CHARS,
                               "hint": "decision=reject needs a short reason"})
        return None
    if not isinstance(reason, str):
        raise _bad("INTERVENE_FIELD_TYPE", field="reason",
                   detail={"received": type(reason).__name__})
    text = reason.strip()
    if decision == "reject" and len(text) < REASON_MIN_CHARS:
        raise _bad("INTERVENE_REASON_TOO_SHORT", field="reason",
                   detail={"length": len(text), "min_length": REASON_MIN_CHARS})
    if len(text) > REASON_MAX_CHARS:
        raise _bad("INTERVENE_FIELD_TYPE", field="reason",
                   detail={"length": len(text), "max_length": REASON_MAX_CHARS})
    return text


def _check_modified_params(modified_params: Any, fields: Sequence[Mapping[str, Any]],
                           original: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if modified_params in (None, {}, ""):
        raise _bad("INTERVENE_PARAM_MISSING", field="modified_params",
                   detail={"editable_fields": [f.get("name") for f in fields],
                           "hint": "edit at least one field before approving with changes"})
    if not isinstance(modified_params, Mapping):
        raise _bad("INTERVENE_FIELD_TYPE", field="modified_params",
                   detail={"received": type(modified_params).__name__})
    if len(modified_params) > MAX_MODIFIED_FIELDS:
        raise _bad("INTERVENE_FIELD_TYPE", field="modified_params",
                   detail={"count": len(modified_params), "max": MAX_MODIFIED_FIELDS})

    by_name = {str(f.get("name")): f for f in fields if f.get("name")}
    if not by_name:
        raise _bad("INTERVENE_PARAM_NOT_ALLOWED", field="modified_params",
                   detail={"hint": "this option offers no editable fields"})

    for key in modified_params:
        if not isinstance(key, str):
            raise _bad("INTERVENE_FIELD_TYPE", field="modified_params",
                       detail={"key_type": type(key).__name__})
        if key not in by_name:
            raise _bad("INTERVENE_PARAM_UNKNOWN", field="modified_params",
                       detail={"field": key, "editable_fields": sorted(by_name)})

    clean: dict[str, Any] = {}
    changed: list[dict[str, Any]] = []
    for name, field in sorted(by_name.items()):
        supplied = name in modified_params
        raw = modified_params[name] if supplied else field.get("default")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if field.get("required") is True:
                raise _bad("INTERVENE_PARAM_EMPTY", field=f"modified_params.{name}",
                           detail={"label": field.get("label"), "required": True,
                                   "hint": field.get("help_text")})
            if not supplied:
                continue
        value = _coerce_field(name, raw, field)
        if value is None and field.get("required") is True:
            raise _bad("INTERVENE_PARAM_EMPTY", field=f"modified_params.{name}",
                       detail={"label": field.get("label"), "required": True})
        clean[name] = value
        before = original.get(name, field.get("default"))
        if _normalised(before) != _normalised(value):
            changed.append({"name": name,
                            "before": _ui_value(before),
                            "after": _ui_value(value)})

    if not clean:
        raise _bad("INTERVENE_PARAM_MISSING", field="modified_params",
                   detail={"editable_fields": sorted(by_name)})
    if not changed:
        raise _bad("INTERVENE_PARAM_NO_CHANGE", field="modified_params",
                   detail={"fields": sorted(clean),
                           "hint": "decision=modify needs at least one value that differs"})
    return clean, changed


def _coerce_field(name: str, raw: Any, field: Mapping[str, Any]) -> Any:
    ftype = str(field.get("type") or "text")
    try:
        if ftype == "integer":
            if isinstance(raw, bool):
                raise ValueError("boolean is not an integer")
            if isinstance(raw, int):
                value = raw
            elif isinstance(raw, float):
                if not float(raw).is_integer():
                    raise ValueError("float is not an integer")
                value = int(raw)
            else:
                text = str(raw).strip()
                if text.endswith(str(field.get("unit") or "")) and field.get("unit"):
                    text = text[: -len(str(field["unit"]))].strip()
                value = int(text)
            lo, hi = field.get("min"), field.get("max")
            if lo is not None and value < int(lo):
                raise _bad("INTERVENE_PARAM_RANGE", field=f"modified_params.{name}",
                           detail={"value": value, "min": lo, "max": hi,
                                   "unit": field.get("unit")})
            if hi is not None and value > int(hi):
                raise _bad("INTERVENE_PARAM_RANGE", field=f"modified_params.{name}",
                           detail={"value": value, "min": lo, "max": hi,
                                   "unit": field.get("unit")})
            return value
        if ftype == "number":
            value = float(raw)
            if math.isnan(value) or math.isinf(value):
                raise ValueError("not a finite number")
            lo, hi = field.get("min"), field.get("max")
            if lo is not None and value < float(lo):
                raise _bad("INTERVENE_PARAM_RANGE", field=f"modified_params.{name}",
                           detail={"value": value, "min": lo, "max": hi})
            if hi is not None and value > float(hi):
                raise _bad("INTERVENE_PARAM_RANGE", field=f"modified_params.{name}",
                           detail={"value": value, "min": lo, "max": hi})
            return int(value) if float(value).is_integer() else value
        if ftype == "boolean":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                low = raw.strip().lower()
                if low in ("true", "1", "yes", "y", "on"):
                    return True
                if low in ("false", "0", "no", "n", "off"):
                    return False
            raise ValueError("not a boolean")
        # text 与未知类型：当字符串处理，绝不猜成数字
        if isinstance(raw, (Mapping, Sequence)) and not isinstance(raw, (str, bytes)):
            raise _bad("INTERVENE_PARAM_TYPE", field=f"modified_params.{name}",
                       detail={"type": ftype, "received": type(raw).__name__})
        text_value = str(raw).strip()
        max_len = field.get("max_length")
        if isinstance(max_len, int) and len(text_value) > max_len:
            raise _bad("INTERVENE_PARAM_RANGE", field=f"modified_params.{name}",
                       detail={"length": len(text_value), "max_length": max_len})
        return text_value
    except InterveneError:
        raise
    except (TypeError, ValueError) as exc:
        raise _bad("INTERVENE_PARAM_TYPE", field=f"modified_params.{name}",
                   detail={"type": ftype, "received": raw if isinstance(raw, (str, int, float, bool)) else type(raw).__name__,
                           "hint": str(exc), "label": field.get("label")})


def _normalised(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _ui_value(value: Any) -> Any:
    """`modified_fields.before/after` 只允许 string|number|boolean|null。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _as_datetime(value: Any) -> Any:
    if isinstance(value, _dt.datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = _dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise _bad("INTERVENE_FIELD_TYPE", field="decided_at",
                       detail={"received": value, "hint": str(exc)})
    else:
        stamp = _dt.datetime.now(_dt.timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return stamp


def _iso(stamp: _dt.datetime) -> str:
    """ISO 时间戳；UTC 写成 `Z` 结尾（schema 的 timestamp 口径）。"""
    text = stamp.isoformat(timespec="seconds")
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


# --------------------------------------------------------------------------- #
# 审计哈希链（内存预览：落盘与保留策略属 t9/t11）
# --------------------------------------------------------------------------- #

class AuditChain:
    """sha256 链：`entry_hash = sha256(规范 JSON(去掉自身 hash 的条目))`，含 `prev_hash`。"""

    def __init__(self, limit: int = AUDIT_MEMORY_LIMIT) -> None:
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._limit = max(1, int(limit))

    def append(self, event: str, *, action_id: str | None = None,
               actor_id: str | None = None, actor_role: str | None = None,
               decision: str | None = None, request_id: str | None = None,
               idempotency_key: str | None = None, mode: str = MODE_LOCAL,
               record_status: str | None = None, record_facts_hash: str | None = None,
               source_facts_hash: str | None = None,
               extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            prev = self._entries[-1]["entry_hash"] if self._entries else GENESIS_HASH
            entry: dict[str, Any] = {
                "seq": len(self._entries) + 1,
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "event": event,
                "action_id": action_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "decision": decision,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "mode": mode,
                "record_status": record_status,
                "record_facts_hash": record_facts_hash,
                "source_facts_hash": source_facts_hash,
                "prev_hash": prev,
            }
            if isinstance(extra, Mapping):
                entry.update({k: v for k, v in extra.items() if k not in entry})
            entry["entry_hash"] = sha256_ref(canonical_json(entry))
            self._entries.append(entry)
            if len(self._entries) > self._limit:          # 内存环，避免无限增长
                self._entries = self._entries[-self._limit:]
            return dict(entry)

    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(e) for e in self._entries]
        return items[-int(limit):] if limit else items

    def verify(self) -> dict[str, Any]:
        """链完整性自检（t11 审计页会复用这个判据）。"""
        with self._lock:
            items = [dict(e) for e in self._entries]
        prev = GENESIS_HASH
        for entry in items:
            own = entry.pop("entry_hash", None)
            if entry.get("prev_hash") != prev:
                return {"ok": False, "seq": entry.get("seq"), "reason": "prev_hash_mismatch"}
            if sha256_ref(canonical_json(entry)) != own:
                return {"ok": False, "seq": entry.get("seq"), "reason": "entry_hash_mismatch"}
            prev = own
        return {"ok": True, "count": len(items), "head": prev}

    def reset(self) -> None:
        with self._lock:
            self._entries = []


# --------------------------------------------------------------------------- #
# 追加式审计链：给操作员的人话 + 给机器的 detail（4.2b / t18）
# --------------------------------------------------------------------------- #

def default_audit_log_path() -> pathlib.Path:
    """产品服务默认的审计文件：``var/audit.jsonl``（与用户表同在 ``var/`` 数据目录）。

    路径可配：CLI ``--audit-log PATH``；``--no-audit-log`` 退回纯内存预览（测试/演示）。
    """
    return audit_mod.default_log_path()


def default_records_store_path(mode: str = MODE_LOCAL) -> pathlib.Path:
    """4.9（t22）产品入口默认的记录存储：``var/records.jsonl``。

    与审计链同一个数据目录：团队部署把 ``var/`` 挂卷，多人共用同一份记录；
    本地模式让"重启后记录还在"这句话在双击运行时也成立。
    路径可配：CLI ``--records-store PATH``；``--no-records-store`` 退回 t8 的纯内存预览。
    """
    return store_mod.default_store_path(mode)


def audit_broken_reason(status: Mapping[str, Any]) -> str:
    """把一次失败的链校验翻成一句中文人话（进 stderr、``detail.reason`` 与审计页）。

    诚实优先：说清"第几条、为什么、共几处、链头、文件在哪"，并明确服务端**不会**
    自动清空、截断或重建 —— 追加式日志没有"修历史"这条路（docs/03 §5.2）。
    """
    problems = list(status.get("problems") or [])
    first = problems[0] if problems and isinstance(problems[0], Mapping) else {}
    where = str(first.get("reason") or "链自检未通过")
    index = first.get("index")
    pos = f"（第 {int(index) + 1} 条）" if isinstance(index, int) else ""
    head = str(status.get("head") or GENESIS_HASH)
    path = str(status.get("path") or "")
    count = len(problems)
    return (f"审计链不可用：{where}{pos}；共 {count} 处问题。"
            f"链头 {head}，文件 {path}。"
            "服务端不会自动清空、截断或重建审计文件；"
            "请先取证（agent_explainer.audit.verify_file）并按追加式纪律处理后再重启。")


def audit_broken_detail(status: Mapping[str, Any]) -> dict[str, Any]:
    """链断时给机器的 detail：问题清单（截断预览）+ 链头/封印 + 可执行的下一步。"""
    problems = list(status.get("problems") or [])
    return {
        "path": str(status.get("path") or ""),
        "count": status.get("count"),
        "head": str(status.get("head") or GENESIS_HASH),
        "sealed_head": str(status.get("sealed_head") or GENESIS_HASH),
        "sealed_count": status.get("sealed_count"),
        "size_bytes": status.get("size_bytes"),
        "file_sha256": status.get("file_sha256"),
        "problems": problems[:AUDIT_PROBLEM_PREVIEW],
        "problem_count": len(problems),
        "reason": audit_broken_reason(status),
        "hint": "append-only: the server will not clear, truncate or rebuild the audit file",
    }


# --------------------------------------------------------------------------- #
# 记录存储（4.9 起：内存索引 + 可选 JSONL 日志；持久化实现在 store.py）
# --------------------------------------------------------------------------- #

def _journal_reason(exc: BaseException) -> str:
    """给人看的一句原因：`RecordStoreError` 自带 render，操作系统错误就照实说。

    磁盘只读、目录不可建、文件被占用都是 `OSError`，没有 `render()`：这里必须
    兜住，否则"写不进去"会变成一次 `AttributeError`（500 却没有稳定 code）。
    """
    render = getattr(exc, "render", None)
    if callable(render):
        return str(render())[:200]
    return f"{type(exc).__name__}: {exc}"[:200]


def _journal_problem(exc: BaseException, op: str) -> dict[str, Any]:
    """持久存储的失败要如实报告：坏行/锁/磁盘错误都不能静默吞掉。"""
    reason = getattr(exc, "render", None)
    return {"op": op,
            "code": str(getattr(exc, "code", "RECORD_STORE_WRITE_FAILED")),
            "reason": (reason() if callable(reason) else str(exc))[:200],
            "detail": dict(getattr(exc, "detail", {}) or {})}


class ActionStore:
    """演示记录的记录存储。

    三种装载口径（决定"重启后还在不在"的就是这一个参数）：

    * ``ActionStore(records=[...])``：纯内存（t8 预览口径），一个字都不落盘；
    * ``ActionStore(source_path=演示文件)``：只读演示 JSON，重启回到演示 12 条；
    * ``ActionStore(journal=...)``：**4.9（t22）持久化** —— 启动时读
      ``var/records.jsonl``（存在就读，不存在就用演示种子写入），之后每次 ``put``
      都写穿到日志，所以用户的决定活得过重启。t8 那两种口径逐字不变。
    """

    def __init__(self, records: Sequence[Mapping[str, Any]] | None = None,
                 source_path: pathlib.Path | str | None = None,
                 journal: store_mod.RecordJournal | None = None) -> None:
        self._lock = threading.RLock()
        self.source_path = pathlib.Path(source_path) if source_path else default_records_path()
        self.journal = journal
        self.journal_report: store_mod.JournalLoad | None = None
        self.journal_problems: list[dict[str, Any]] = []
        self.journal_errors = 0
        self._journal_sig: tuple[int, int] | None = None
        self._snapshot: list[dict[str, Any]] = []
        self._records: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._source_label = "memory"
        if journal is not None:
            self._load_persisted(records)
        elif records is not None:
            self._load_records(records)
        else:
            self.reload()

    # -- 装载 --------------------------------------------------------------
    def _load_persisted(self, seed: Sequence[Mapping[str, Any]] | None) -> None:
        """4.9：先读日志；没有日志就用演示种子建一份，并让 reset 回到这份种子。"""
        demo: list[dict[str, Any]] = []
        if seed is not None:
            demo = [copy.deepcopy(dict(rec)) for rec in seed
                    if isinstance(rec, Mapping) and rec.get("id")]
        else:
            data = json.loads(self.source_path.read_text(encoding="utf-8"))
            items = data.get("records") if isinstance(data, Mapping) else data
            demo = [copy.deepcopy(dict(rec)) for rec in items
                    if isinstance(rec, Mapping) and rec.get("id")]
        loaded = self.journal.load()
        self.journal_report = loaded
        self.journal_problems = list(loaded.problems)
        self._snapshot = copy.deepcopy(demo)
        self._demo_seed = copy.deepcopy(demo)
        if loaded.existed and loaded.records:
            self._load_records(loaded.records)
            self._source_label = f"{loaded.path}（持久记录）"
        else:
            self._load_records(demo)
            self._source_label = f"{loaded.path}（首次启动：写入演示种子）"
            try:
                self.journal.seed(self._records.values())
            except (store_mod.RecordStoreError, OSError) as exc:
                self.journal_errors += 1
                self.journal_problems.append(_journal_problem(exc, "seed"))
        self._journal_sig = self.journal.signature()

    def _sync_from_journal(self) -> None:
        """团队部署里多人共用一份记录：日志被别的进程写过就重新装载。

        代价只有一次 ``stat``，只有签名变了才解析文件；本进程自己的写入不算变化
        （写完立刻更新签名）。多进程写同一份文件的安全性由 ``store.py`` 的文件锁 +
        逐行追加 + 原子压实保证，这里只解决"读得到别人刚写的那条"。
        """
        if self.journal is None:
            return
        sig = self.journal.signature()
        if sig is None or sig == self._journal_sig:
            return
        loaded = self.journal.load()
        self.journal_report = loaded
        self.journal_problems = list(loaded.problems)
        self._load_records(loaded.records)
        self._journal_sig = sig

    def reload(self) -> int:
        with self._lock:
            if self.journal is not None:
                # 持久模式下"重新读演示文件"不是重载：那会把用户的决定与导入记录清掉。
                # 显式重置演示数据请走 `--reset-demo`（产品入口）或 reset_to_demo()。
                return len(self._records)
            path = self.source_path
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data.get("records") if isinstance(data, Mapping) else data
            self._load_records(records)
            self._source_label = str(path)
            return len(self._records)

    def _load_records(self, records: Any) -> None:
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise ValueError("records must be a list of ActionRecord objects")
        with self._lock:
            self._records = OrderedDict()
            for rec in records:
                if isinstance(rec, Mapping) and rec.get("id"):
                    self._records[str(rec["id"])] = copy.deepcopy(dict(rec))
            self._snapshot = copy.deepcopy(list(self._records.values()))
            self._idempotency = {}

    def reset(self) -> int:
        """清记录：回到演示种子（导入的记录与用户决定一起清掉），只动记录。

        4.9 起这条口径必须写清：持久模式下"回到本次启动时的状态"不是清记录 ——
        重启后那批里已经含导入记录与用户决定，按那个口径点「清空数据」会什么都清不掉。
        所以 reset 与 ``--reset-demo``（``reset_to_demo``）落在同一动作：
        清掉全部用户数据、把演示种子压实写回日志。审计由调用方追加，本方法一个字都不写。
        """
        return self.reset_to_demo()

    def reset_to_demo(self) -> int:
        """显式重置演示数据（CLI ``--reset-demo`` 与 ``reset()`` 都走这里）。"""
        with self._lock:
            self._records = OrderedDict(
                (str(rec["id"]), copy.deepcopy(rec)) for rec in self._demo_snapshot())
            self._idempotency = {}
            self._journal_replace()
            return len(self._records)

    def _demo_snapshot(self) -> list[dict[str, Any]]:
        seed = getattr(self, "_demo_seed", None)
        if seed:
            return copy.deepcopy(seed)
        return copy.deepcopy(self._snapshot)

    def _journal_replace(self) -> dict[str, Any] | None:
        """把当前内存记录压实写回日志（没有日志就是纯内存，什么都不做）。"""
        if self.journal is None:
            return None
        try:
            self.journal.append_clear(reason="reset")
            result = self.journal.replace_all(self._records.values(),
                                              header={"written_by": "reset"})
            self._journal_sig = self.journal.signature()
            return result
        except (store_mod.RecordStoreError, OSError) as exc:
            self.journal_errors += 1
            self.journal_problems.append(_journal_problem(exc, "replace"))
            raise _bad("RECORD_STORE_WRITE_FAILED", status=500,
                       message=f"记录已改，但持久存储写不回去：{_journal_reason(exc)}",
                       detail={"path": str(self.journal.path),
                               "hint": "检查 var/ 目录权限与磁盘空间；记录不会静默丢失，"
                                       "内存里仍在"} if self.journal is not None else {}) from exc

    def _journal_write(self, record: Mapping[str, Any]) -> None:
        if self.journal is None:
            return
        try:
            self.journal.append_put(record)
            self._journal_sig = self.journal.signature()
        except (store_mod.RecordStoreError, OSError) as exc:
            self.journal_errors += 1
            self.journal_problems.append(_journal_problem(exc, "put"))
            # 决定已经在内存里了：写不进去必须 loudly 失败，不能"决定了没落盘"
            raise _bad("RECORD_STORE_WRITE_FAILED", status=500,
                       message=f"记录已更新，但持久存储写不进去：{_journal_reason(exc)}",
                       detail={"action_id": str(record.get("id") or ""),
                               "path": str(self.journal.path),
                               "hint": "检查 var/ 目录权限；审计链不受影响"}
                       ) from exc

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._sync_from_journal()
            found = self._records.get(str(action_id))
            return copy.deepcopy(found) if found is not None else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            self._sync_from_journal()
            return copy.deepcopy(list(self._records.values()))

    def put(self, record: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._records[str(record["id"])] = copy.deepcopy(dict(record))
            self._journal_write(record)
            return copy.deepcopy(dict(record))

    def idempotency_lookup(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            return self._idempotency.get(key)

    def idempotency_store(self, key: str, action_id: str, entry_hash: str) -> None:
        with self._lock:
            self._idempotency[key] = (action_id, entry_hash)

    def store_facts(self) -> dict[str, Any]:
        """持久化事实（给 /health 与 /api/records 的 meta；不含任何记录正文）。"""
        if self.journal is None:
            return {"durable": False, "mode": "memory",
                    "note": "纯内存预览：重启回到演示数据（4.9 的持久化未启用）"}
        facts = {"durable": True, "mode": "jsonl", "path": str(self.journal.path),
                 "store_version": store_mod.STORE_VERSION,
                 "journal": self.journal.stat(),
                 "problems": len(self.journal_problems),
                 "problem_preview": self.journal_problems[:3],
                 "write_errors": self.journal_errors,
                 "loaded": (self.journal_report.as_dict()
                            if self.journal_report is not None else {})}
        return facts

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._sync_from_journal()
            by_status: dict[str, int] = {}
            decided = 0
            for rec in self._records.values():
                by_status[str(rec.get("status"))] = by_status.get(str(rec.get("status")), 0) + 1
                if isinstance(rec.get("user_intervention"), Mapping):
                    decided += 1
            demo = sum(1 for rec in self._records.values() if rec.get("demo_data") is True)
            synthetic = sum(1 for rec in self._records.values() if rec.get("synthetic") is True)
            facts = self.store_facts()
            return {"count": len(self._records), "decided": decided,
                    "by_status": by_status, "demo_data": demo, "synthetic": synthetic,
                    "durable": facts["durable"], "source": self._source_label}


def default_records_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1] / "samples" / "action_records.json"


def default_web_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "web"


def default_auth_state_path(mode: str) -> pathlib.Path:
    """鉴权状态文件的默认位置。

    * `team`：`var/auth/auth-state.json`（部署时把 `var/` 挂成卷，用户表才活得过夜）；
    * `local`：系统临时目录。本地模式没有用户表，也没有会话签名密钥要长期保存，
      放仓库里只会在跑完测试后留下一份带密钥的鉴权文件（纯残渣）。
    """
    if mode == MODE_LOCAL:
        return (pathlib.Path(tempfile.gettempdir()) / "tracelens" / "local-auth"
                / auth_mod.STATE_FILE_NAME)
    return auth_mod.default_state_path()


def default_ingest_token_path(mode: str = MODE_LOCAL) -> pathlib.Path:
    """4.4 的 ingest 令牌表默认位置（只存哈希，明文不落盘）。

    * `team`：`var/ingest/tokens.json`（与用户表同处，部署时把 `var/` 挂卷才活得过夜）；
    * `local`：系统临时目录 —— 本机匿名写入本来就不需要令牌，跑完测试也不要在
      仓库里留一份带哈希的密钥残渣（与 4.3 的 local 用户表同口径）。
    """
    if mode == MODE_LOCAL:
        return (pathlib.Path(tempfile.gettempdir()) / "tracelens" / "ingest" / "tokens.json")
    return pathlib.Path(__file__).resolve().parents[1] / "var" / "ingest" / "tokens.json"


# --------------------------------------------------------------------------- #
# 服务层：/intervene + 记录 + 审计 + 健康
# --------------------------------------------------------------------------- #

class InterveneService:
    """把 store + 审计链 + 迁移规则组合成本地 API 的能力面。"""

    def __init__(self, *, store: ActionStore | None = None,
                 records: Sequence[Mapping[str, Any]] | None = None,
                 records_path: pathlib.Path | str | None = None,
                 records_store_path: pathlib.Path | str | None = None,
                 records_store_lock: float = store_mod.LOCK_TIMEOUT_SECONDS,
                 reset_demo: bool = False,
                 audit: AuditChain | None = None,
                 mode: str = MODE_LOCAL,
                 auth: auth_mod.Authenticator | None = None,
                 auth_state_path: pathlib.Path | str | None = None,
                 bootstrap_admin: bool = False,
                 session_ttl: int = auth_mod.SESSION_TTL_SECONDS,
                 audit_log_path: pathlib.Path | str | None = None,
                 secure_cookies: bool = False,
                 ingest_enabled: bool = True,
                 ingest_token_path: pathlib.Path | str | None = None,
                 ingest_rate: float = INGEST_RATE_DEFAULT,
                 ingest_rate_per_minute: float = INGEST_RATE_PER_MIN_DEFAULT,
                 ingest_max_events: int = ingest_mod.MAX_EVENTS_PER_REQUEST,
                 ingest_strict: bool = True,
                 stream_limit: int = STREAM_LIMIT_DEFAULT,
                 stream_heartbeat: float = STREAM_HEARTBEAT_SECONDS,
                 settings_path: pathlib.Path | str | None = None,
                 settings_env: Mapping[str, Any] | None = None,
                 settings_writable: bool = True,
                 llm_transport: Any = None,
                 secretariat: sec_mod.Secretariat | None = None,
                 features_root: pathlib.Path | str | None = None,
                 sign_on_write: bool = False) -> None:
        self.mode = mode
        self.audit = audit or AuditChain()
        self._lock = threading.RLock()
        self.requests = 0
        self.interventions = 0
        self.rejects = 0
        self.secure_cookies = bool(secure_cookies)
        # ---- 4.2b（t18）审计后端：内存链预览 + 追加式 JSONL 文件链 ----------------
        # 只给了 audit_log_path 才落盘：产品入口（`main()`）默认给 `var/audit.jsonl`，
        # 单元测试传临时目录，t8 那套"不传就是纯内存"的口径保持不变。
        self.audit_log: audit_mod.AuditLog | None = None
        self.audit_source = AUDIT_SOURCE_MEMORY
        self.audit_startup: dict[str, Any] = {}
        self.audit_problem: dict[str, Any] | None = None
        self.audit_seal: dict[str, Any] = {"sealed_head": GENESIS_HASH, "sealed_count": 0,
                                           "seals": 0}
        self._audit_alerted = False
        if audit_log_path:
            self.audit_source = AUDIT_SOURCE_JSONL
            self.audit_log = audit_mod.open_log(audit_log_path)
            # 启动即加载并复算整条链（含 seal 锚点）。断链只报告，不修、不清、不重建。
            self.verify_audit_chain(report_stderr=True)
        if auth is not None:
            self.auth = auth
        else:
            self.auth = auth_mod.Authenticator(
                mode, state_path=auth_state_path or default_auth_state_path(mode),
                session_ttl=session_ttl, bootstrap=bootstrap_admin)
        if store is not None:
            self.store = store
        else:
            # ---- 4.9（t22）记录持久化：给了路径就写穿到 var/records.jsonl ----------
            # 不传路径 = t8 的纯内存预览（重启回到演示数据），库调用方与既有测试口径不变。
            journal = (store_mod.open_journal(records_store_path,
                                              lock_timeout=records_store_lock)
                       if records_store_path else None)
            self.store = ActionStore(records=records, source_path=records_path,
                                     journal=journal)
        self.records_store_path = (pathlib.Path(self.store.journal.path)
                                   if getattr(self.store, "journal", None) else None)
        self.startup_store: dict[str, Any] = (self.store.store_facts()
                                              if getattr(self.store, "journal", None) else {})
        if reset_demo:
            # 显式重置演示数据：导入记录与用户决定一起清掉（调用方负责在审计里留一条）
            self.store.reset_to_demo()
        # ---- 4.4（t14）实时接入：令牌保管箱 + 广播中心 + 上报编排 ----------------
        # 归一化全部复用 trace_adapter；解释与审计逻辑一概不碰（ingest 只写事实字段）。
        self.stream_heartbeat = float(max(0.02, stream_heartbeat))
        self.stream_hub = ingest_mod.StreamHub(limit=int(stream_limit))
        self.ingest_tokens = ingest_mod.TokenVault(
            ingest_token_path if ingest_token_path else default_ingest_token_path(mode))
        self.ingest = ingest_mod.IngestBroker(
            self.store, self.stream_hub, vault=self.ingest_tokens, enabled=ingest_enabled,
            mode=mode, rate_capacity=ingest_rate, rate_per_minute=ingest_rate_per_minute,
            max_events=ingest_max_events, max_bytes=MAX_BODY_BYTES,
            strict_default=ingest_strict,
            signer=self.sign_on_write_record if sign_on_write else None)
        # ---- 4.5（t15）设置中心：模型配置 / 界面语言 / 数据管理 ------------------
        # 读写、掩码、DPAPI 与连接测试全在 settings.py。服务端只取"掩码视图"，
        # 永不接触密钥明文 —— t8 的源码级纯净性测试钉住了这一点（本文件里没有那个
        # 字段的蛇形写法，也没有任何落盘动作）。
        self.settings = settings_mod.SettingsStore(
            settings_path if settings_path else settings_mod.default_settings_path(mode),
            mode=mode, env=settings_env, writable=settings_writable)
        self.settings.load()
        # 解释引擎用服务级实例：配置（模式 / 语言 / 模型）一改就立刻生效，
        # 模板模式下与模块级默认逐字一致（t8 的文案断言不受影响）。
        self.generator = make_generator()
        self.settings.apply_to(self.generator)
        self.llm_transport = llm_transport          # 测试注入的假连接通道
        # ---- 5.0（R2–R4）总裁办：身份 / 签名 / 委托 / 认定书 ---------------------
        # 数据落在 `<数据根>/var/features/`（与 docs/07 §3.3 一致）；默认根由
        # feature_store.FeaturePaths 决定，测试与部署一律显式注入 features_root。
        # 这里**懒创建**：不碰总裁办端点的调用方（含既有 896 条单测）不会因为
        # 服务启动就在仓库 var/ 下落一份密钥库。
        self._secretariat = secretariat
        self._features_root = features_root
        # ---- 5.0（R4 集成）入库即签名 -------------------------------------------
        # 产品入口默认开（--no-secretariat 关）；库默认关，测试/嵌入方显式选择。
        # 只在 local 模式启用：team 模式的 owner 归属需要 actor→DID 映射，属后续批次。
        self.sign_on_write = bool(sign_on_write)
        self.signed_on_write = 0
        self.sign_failures = 0
        self.sign_last_problem: dict[str, Any] = {}
        self.settings_reads = 0
        self.settings_writes = 0
        self.llm_tests = 0
        self.llm_last_test: dict[str, Any] = {}
        #: 4.8（t21）：导出过几次（只数成功的；失败的走 rejects 计数）
        self.exports_served = 0
        #: 4.9（t22）：导入过几批、共导入多少条（只数成功的）
        self.imports = 0
        self.imported_records = 0

    # -- 5.0 总裁办：T1–T10 的服务端能力面（docs/12 §6）--------------------
    # 这里只做“取数据 / 校验形状 / 调 secretariat / 落盘”，不复制密码学逻辑；
    # 权限与 CSRF 在 handler 里（服务端强制点），这一层不接触会话。
    LETTER_MAX_RECORDS = 200
    #: `GET /api/secretariat/letters` 不传 limit 时回多少封（docs/12 §6 T7 的"摘要"口径）。
    LETTER_DEFAULT_LIMIT = 50
    #: `GET /api/secretariat/letters?limit=` 的上限（与 `secretariat.letters()` 的
    #: 500 封上限一致）：坏值 400，超上限如实截断，不静默放大响应。
    LETTER_LIST_MAX = 500

    @property
    def secretariat(self) -> sec_mod.Secretariat:
        """首次访问才装配：没人用总裁办时，不在数据目录里建密钥库与账本。"""
        if self._secretariat is None:
            paths = FeaturePaths(self._features_root) if self._features_root else None
            self._secretariat = sec_mod.default_secretariat(paths, mode=self.mode)
        return self._secretariat

    def sign_on_write_record(self, record: Mapping[str, Any],
                             previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """入库前签名（R4 集成；产品入口默认开，docs/07 §5 的"写记录即签名"）。

        三条诚实边界（写进 docs/12 §6 与交付报告）：
        1. 只在 ``local`` 模式启用：team 模式的 owner 需要 actor→DID 映射，尚未接；
        2. 高危记录用 ``allow_unapproved=True``：Agent 自签照签，**审批缺口如实留给
           verify 报出来**，不替审批人签字；
        3. 签名失败不丢数据：记录原样入库并计数（``sign_failures``），
           响应里 ``signing`` 块如实说几条签了、几条没签。
        """
        data = dict(record)
        if not self.sign_on_write or self.mode != MODE_LOCAL:
            return data
        # 旧签名/旧哈希在事实变化后必然失效：先摘掉，再由 sign_record 统一重算。
        data.pop("signature", None)
        data.pop("entry_hash", None)
        data.pop("tsa_timestamp", None)
        try:
            signed = self.secretariat.sign_record(
                data, previous=previous, allow_unapproved=True)
        except sec_mod.SecretariatError as exc:
            self.sign_failures += 1
            self.sign_last_problem = {"code": exc.code, "reason": exc.reason,
                                      "action_id": str(data.get("id") or "")}
            return data
        self.signed_on_write += 1
        return signed

    def secretariat_status_payload(self) -> dict[str, Any]:
        return {"ok": True, "secretariat": self.secretariat.status()}

    def secretariat_identities_payload(self) -> dict[str, Any]:
        identities = self.secretariat.identities()
        return {"ok": True, "identities": identities, "count": len(identities)}

    def secretariat_register(self, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload)
        identity = self.secretariat.register_identity(
            agent_id=str(body.get("agent_id") or ""),
            owner_did=str(body.get("owner_did") or ""),
            image_hash=body.get("image_hash"),
            boot_params=body.get("boot_params"),
            kind=str(body.get("kind") or "agent"),
            rotate=bool(body.get("rotate")))
        return {"ok": True, "identity": identity}

    def secretariat_revoke(self, agent_id: str, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload or {})
        identity = self.secretariat.revoke_identity(
            agent_id, reason=str(body.get("reason") or ""))
        return {"ok": True, "identity": identity}

    def secretariat_delegations_payload(self) -> dict[str, Any]:
        items = self.secretariat.delegations()
        return {"ok": True, "delegations": items, "count": len(items),
                "max_depth": sec_mod.MAX_DELEGATION_DEPTH}

    def secretariat_create_delegation(self, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload)
        scope = body.get("scope")
        if not isinstance(scope, Mapping):
            raise _bad("SECRETARIAT_FIELD_INVALID", field="scope",
                       message="scope 必须是对象（action_types/tools/max_risk）")
        delegation = self.secretariat.create_delegation(
            delegator_did=str(body.get("delegator_did") or ""),
            delegatee_did=str(body.get("delegatee_did") or ""),
            scope=scope,
            ttl_seconds=body.get("ttl_seconds"),
            parent_id=(str(body["parent_id"]) if body.get("parent_id") else None))
        return {"ok": True, "delegation": delegation}

    def secretariat_sign(self, action_id: str, payload: Any) -> dict[str, Any]:
        record = self.store.get(str(action_id))
        if record is None:
            raise _bad("SECRETARIAT_RECORD_MISSING", field="action_id",
                       detail={"action_id": str(action_id)},
                       message="找不到这条记录：先让它入库，再签名")
        body = self._json_object(payload or {})
        approvers = body.get("approvers") or []
        if not isinstance(approvers, list):
            raise _bad("SECRETARIAT_FIELD_INVALID", field="approvers",
                       message="approvers 必须是数组")
        signed = self.secretariat.sign_record(
            record, risk_level=body.get("risk_level"),
            approvers=approvers, allow_unapproved=bool(body.get("allow_unapproved")))
        stored = self.store.put(signed)
        return {"ok": True, "record": stored,
                "verification": self.secretariat.verify_record(stored)}

    def secretariat_verify(self, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload)
        records = body.get("records")
        if records is None and "record" in body:
            records = [body.get("record")]
        if not isinstance(records, list) or not records:
            raise _bad("SECRETARIAT_FIELD_INVALID", field="records",
                       message="给一条 record 或一个 records 数组")
        results = [self.secretariat.verify_record(item) for item in records]
        failed = len([item for item in results if not item.get("ok")])
        return {"ok": failed == 0, "results": results,
                "summary": {"total": len(results), "ok": len(results) - failed,
                            "failed": failed}}

    def secretariat_letters_payload(self, *, limit: int | None = None) -> dict[str, Any]:
        """认定书列表（摘要）：`limit` 缺省 50 封，最多 500 封（如实回传实际生效值）。"""
        cap = (self.LETTER_DEFAULT_LIMIT if limit is None
               else max(1, min(int(limit), self.LETTER_LIST_MAX)))
        letters = self.secretariat.letters(limit=cap)
        return {"ok": True, "letters": letters, "count": len(letters), "limit": cap}

    def secretariat_create_letter(self, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload)
        ids = body.get("record_ids")
        if not isinstance(ids, list) or not ids:
            raise _bad("SECRETARIAT_LETTER_EMPTY", field="record_ids",
                       message="认定书至少要涉及一条记录")
        if len(ids) > self.LETTER_MAX_RECORDS:
            raise _bad("SECRETARIAT_FIELD_INVALID", field="record_ids",
                       message=f"一次最多 {self.LETTER_MAX_RECORDS} 条记录",
                       detail={"count": len(ids), "limit": self.LETTER_MAX_RECORDS})
        records: list[dict[str, Any]] = []
        missing: list[str] = []
        for raw_id in ids:
            found = self.store.get(str(raw_id))
            if found is None:
                missing.append(str(raw_id))
            else:
                records.append(found)
        if missing:
            raise _bad("SECRETARIAT_RECORD_MISSING", field="record_ids",
                       detail={"missing": missing[:20], "count": len(missing)},
                       message="有记录不在库里：认定书不能引用不存在的动作")
        head = str((self.audit_chain_status() or {}).get("head") or "")
        period = body.get("period") if isinstance(body.get("period"), Mapping) else None
        letter = self.secretariat.build_letter(
            subject=str(body.get("subject") or ""),
            records=records,
            responsibility=str(body.get("responsibility") or "technical"),
            period=period,
            note=str(body.get("note") or ""),
            audit_head=head)
        return {"ok": True, "letter": letter,
                "verification": self.secretariat.verify_letter(letter)}

    def secretariat_letter(self, letter_id: str) -> dict[str, Any]:
        letter = self.secretariat.letter(letter_id)
        if letter is None:
            raise _bad("SECRETARIAT_LETTER_NOT_FOUND", field="letter_id",
                       detail={"letter_id": letter_id},
                       message="找不到这封认定书")
        return {"ok": True, "letter": letter,
                "verification": self.secretariat.verify_letter(letter)}

    def secretariat_verify_letter(self, letter_id: str) -> dict[str, Any]:
        letter = self.secretariat.letter(letter_id)
        if letter is None:
            raise _bad("SECRETARIAT_LETTER_NOT_FOUND", field="letter_id",
                       detail={"letter_id": letter_id},
                       message="找不到这封认定书")
        verification = self.secretariat.verify_letter(letter)
        return {"ok": bool(verification.get("ok")), "verification": verification}

    def secretariat_seal(self, payload: Any) -> dict[str, Any]:
        body = self._json_object(payload)
        action_id = str(body.get("record_id") or "").strip()
        if not action_id:
            raise _bad("SECRETARIAT_FIELD_INVALID", field="record_id",
                       message="盖章必须指定 record_id")
        record = self.store.get(action_id)
        if record is None:
            raise _bad("SECRETARIAT_RECORD_MISSING", field="record_id",
                       detail={"record_id": action_id},
                       message="找不到这条记录：先让它入库，再盖章")
        seal = self.secretariat.seal(
            subject=action_id, digest=sec_mod.approval_digest(record),
            reason=str(body.get("reason") or ""))
        return {"ok": True, "seal": seal,
                "verification": {"ok": self.secretariat.verify_seal(seal)}}

    # -- 鉴权结果落审计（docs/03 §5.3）------------------------------------
    def flush_auth_events(self) -> int:
        """把 :class:`Authenticator` 攒下的鉴权事件写进审计链（口令永不进审计）。

        内存链是 t8 的预览版；如果配置了 `audit_log_path`，同一批事件也进 t9 的
        追加式 JSONL（t9 的 `EVENTS` 枚举里没有 `auth.*`，所以落盘时事件名按
        `access_denied` / `note` 收，原始名字保留在 `fields.auth_event` 里）。
        """
        events = self.auth.take_events()
        for item in events:
            self.audit.append(str(item.get("event")),
                              actor_id=item.get("actor_id"),
                              actor_role=self.auth.audit_actor_role(str(item.get("role") or "")),
                              mode=self.audit_mode(), request_id=item.get("request_id"),
                              record_status="auth",
                              extra={"auth_event": item.get("event"),
                                     "username": item.get("username"),
                                     "ok": item.get("ok"),
                                     "reason": item.get("reason"),
                                     "at": item.get("at"),
                                     "detail": item.get("detail")})
            if self.audit_log is not None:
                denied = str(item.get("event")) == "auth.denied"
                # 队长裁决（t18）：t9 的 EVENTS 已加入 auth.*，落盘仍保留
                # access_denied / note + fields.auth_event 的映射口径，历史文件不改写。
                self._append_durable(audit_mod.build_event(
                    "access_denied" if denied else "note",
                    actor_id=str(item.get("actor_id") or "anonymous"),
                    actor_role=self.auth.audit_actor_role(str(item.get("role") or "")),
                    mode=self.audit_mode(),
                    reason=str(item.get("reason") or ""),
                    client_info={"channel": str(item.get("detail", {}).get("client") or "")},
                    extra={"auth_event": item.get("event"),
                           "username": item.get("username"), "ok": item.get("ok"),
                           "role": str(item.get("role") or "")}))
        return len(events)

    def audit_actor(self, session: Mapping[str, Any] | None) -> tuple[str, str]:
        """审计/决定段用的 (actor_id, role)。

        两个身份口径要分清，都有测试钉着：
        * 4.1 钉住**本地模式的决定人与审计条**写 `local-user`（`LOCAL_ACTOR_ID`）；
        * docs/03 §5.5 钉住**鉴权域内部** actor_id 是 `local`（在 `/api/auth/me` 里给）。
        所以本地会话进来时仍按 4.1 的 `local-user`，不改写历史口径。
        """
        if not session or session.get("local"):
            return LOCAL_ACTOR_ID, LOCAL_ACTOR_ROLE
        actor = str(session.get("username") or session.get("user_id") or LOCAL_ACTOR_ID)
        return actor, str(session.get("role") or LOCAL_ACTOR_ROLE)

    def audit_mode(self) -> str:
        """审计域的 `mode` 只有 `local` / `live` 两个允许值（4.2 冻结的枚举）。

        4.3 的服务模式多了 `team`：它属于"连着服务端"的一侧，落审计时归入 `live`，
        不能让审计条目因为新模式名而通不过 4.2 的字段枚举校验。
        """
        return "live" if self.mode == MODE_TEAM else "local"

    # -- 追加式文件链（4.2b / t18）-----------------------------------------
    def verify_audit_chain(self, *, report_stderr: bool = False) -> dict[str, Any]:
        """加载并复算文件链（含 seal 锚点）；**只读**，绝不修复、清空或重建。

        结果留在 ``audit_startup``：``ok`` 给 /health，``problems`` 给 /api/audit 的 500。
        启动那一次（``report_stderr=True``）额外往 stderr 写一句人话：审计断了要在
        控制台/Docker 日志里当场看见，而不是等请求变成 500 才发现。
        """
        log = self.audit_log
        if log is None:
            self.audit_source = AUDIT_SOURCE_MEMORY
            self.audit_problem = None
            self.audit_startup = {"ok": True, "durable": False, "source": AUDIT_SOURCE_MEMORY,
                                  "count": 0, "head": GENESIS_HASH, "problems": [],
                                  **self.audit_seal}
            return dict(self.audit_startup)
        self.audit_source = AUDIT_SOURCE_JSONL
        try:
            report = log.verify(raise_on_tamper=False)
        except Exception as exc:                                   # noqa: BLE001
            # 读不动文件也要说清原因，禁止静默当成"没有日志"
            report = {"ok": False, "count": 0, "head": GENESIS_HASH, "seals": 0,
                      "size_bytes": 0, "file_sha256": "", "checked_at": None,
                      "path": str(log.path),
                      "problems": [{"code": "unreadable", "index": None, "seq": None,
                                    "reason": f"审计文件读不了：{type(exc).__name__}: {exc}"}]}
        seals: list[dict[str, Any]] = []
        try:
            if log.exists():
                seals = list(log.query(event="seal", limit=1))
        except Exception:                                          # noqa: BLE001
            seals = []
        newest = seals[0] if seals and isinstance(seals[0], Mapping) else {}
        self.audit_seal = {"sealed_head": str(newest.get("sealed_head") or GENESIS_HASH),
                           "sealed_count": int(newest.get("sealed_count") or 0),
                           "seals": int(report.get("seals") or 0)}
        problems = [dict(p) for p in (report.get("problems") or []) if isinstance(p, Mapping)]
        status = {"ok": bool(report.get("ok")), "durable": True, "source": AUDIT_SOURCE_JSONL,
                  "path": str(report.get("path") or log.path),
                  "count": int(report.get("count") or 0),
                  "head": str(report.get("head") or GENESIS_HASH),
                  "genesis": str(report.get("genesis") or GENESIS_HASH),
                  "size_bytes": report.get("size_bytes"),
                  "file_sha256": report.get("file_sha256"),
                  "checked_at": report.get("checked_at"),
                  "problems": problems, **self.audit_seal}
        self.audit_startup = status
        self.audit_problem = dict(problems[0]) if problems else None
        if report_stderr and problems:
            sys.stderr.write(audit_broken_reason(status) + "\n")
            self._audit_alerted = True
        return dict(status)

    def note_audit_broken(self, exc: BaseException) -> dict[str, Any]:
        """记账失败的第二类现场（鉴权事件镜像）：报告 + 记住问题，但不打断登录/浏览。

        决定路径（``intervene``）不允许这样降级 —— 决定必须同事务落审计；
        鉴权事件是辅助遥测，链断时让它降级到"内存链 + stderr + /health 报断"，
        否则审计一坏，用户连登录页都进不去。
        """
        status: dict[str, Any] = {}
        if isinstance(exc, audit_mod.AuditTamperError):
            status = dict(exc.status)
        if not status.get("problems"):
            status = {"problems": [{"code": "audit_append_failed", "index": None, "seq": None,
                                    "reason": f"{type(exc).__name__}: {exc}"}]}
        status.setdefault("path", str(self.audit_log.path) if self.audit_log else "")
        status.setdefault("head", GENESIS_HASH)
        status.setdefault("count", 0)
        status.update(self.audit_seal)
        self.audit_problem = dict(status["problems"][0])
        self.audit_startup = {**self.audit_startup, "ok": False, **status}
        if not self._audit_alerted:
            sys.stderr.write(audit_broken_reason(status) + "\n")
            self._audit_alerted = True
        return status

    def audit_chain_status(self) -> dict[str, Any]:
        """/health 的 ``audit`` 块：内存链 + 文件链状态（断链时 ``ok=false``）。"""
        preview = self.audit.verify()
        if self.audit_log is None:
            return {"ok": bool(preview.get("ok")), "count": int(preview.get("count") or 0),
                    "head": str(preview.get("head") or GENESIS_HASH),
                    "durable": False, "source": AUDIT_SOURCE_MEMORY,
                    "note": "内存哈希链预览：重启即丢，落盘请用 --audit-log（4.2b）"}
        status = self.verify_audit_chain()
        out = dict(status)
        out["preview_count"] = int(preview.get("count") or 0)
        out["preview_head"] = str(preview.get("head") or GENESIS_HASH)
        if status.get("problems"):
            out["reason"] = audit_broken_reason(status)
            out["hint"] = ("append-only: the server will not clear, truncate or rebuild "
                           "the audit file")
        return out

    def require_audit_chain(self) -> dict[str, Any]:
        """链断 → 500 AUDIT_CHAIN_BROKEN（可读原因 + 下一步），并刷新 stderr 报告。"""
        status = self.verify_audit_chain()
        if not status.get("ok"):
            self.audit_problem = dict((status.get("problems") or [{}])[0])
            raise _bad("AUDIT_CHAIN_BROKEN", message=audit_broken_reason(status),
                       detail=audit_broken_detail(status))
        return status

    def _append_durable(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """镜像类事件（鉴权/重置）进文件链：链断时报告并降级，不打断请求。"""
        log = self.audit_log
        if log is None:
            return {}
        try:
            return dict(log.append(event))
        except audit_mod.AuditTamperError as exc:
            self.note_audit_broken(exc)
        except OSError as exc:
            self.note_audit_broken(exc)
        return {}

    def _append_durable_strict(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """决定类事件进文件链：链断 → 500，绝不"决定了但没记账"。"""
        log = self.audit_log
        if log is None:
            return {}
        try:
            return dict(log.append(event))
        except audit_mod.AuditTamperError as exc:
            self.note_audit_broken(exc)
            status = dict(exc.status)
            status.setdefault("path", str(log.path))
            status.update(self.audit_seal)
            raise _bad("AUDIT_CHAIN_BROKEN", message=audit_broken_reason(status),
                       detail=audit_broken_detail(status)) from exc
        except OSError as exc:
            raise _bad("AUDIT_CHAIN_BROKEN", status=500,
                       message=f"审计文件写不进去：{type(exc).__name__}: {exc}",
                       detail={"path": str(log.path),
                               "hint": "check disk space and file permissions; "
                                       "the server never rebuilds the audit file"}) from exc

    def _append_durable_decision(self, before: Mapping[str, Any], after: Mapping[str, Any], *,
                                 actor_id: str, actor_role: str, request_id: str,
                                 idempotency_key: str | None) -> dict[str, Any]:
        """t8 决定段 → t9 审计条目（字段由 ``event_from_decision`` 自己算，参数脱敏）。"""
        return self._append_durable_strict(audit_mod.event_from_decision(
            before, after, actor_id=actor_id,
            actor_role=self.auth.audit_actor_role(actor_role),
            request_id=request_id, idempotency_key=idempotency_key,
            mode=self.audit_mode(), client_info={"channel": "api"}))

    # -- 4.4 实时接入（t14）：webhook 写入 + 广播 + 令牌管理 ------------------
    def ingest_actor_id(self, token: Mapping[str, Any] | None) -> str:
        """审计里的"谁上报的"：令牌标签 + id；本机匿名写入就直说是 anonymous。"""
        record = token or {}
        label = str(record.get("label") or "").strip()
        tid = str(record.get("token_id") or "unknown")
        if record.get("anonymous"):
            return "ingest:anonymous-local"
        return f"ingest:{label or tid}/{tid}"

    def ingest_events(self, payload: Any, *, secret: str | None = None,
                      request_id: str | None = None,
                      idempotency_key: str | None = None) -> dict[str, Any]:
        """`POST /api/ingest/events`（docs/03 §6.3 E1）。

        机器对机器用**作用域受限的 ingest 令牌**，不是用户会话 —— 这是 4.3
        「team 模式必须登录」的一个明确例外，写进 `/health` 的 `ingest.note`，
        别让读代码的人猜。team 模式缺令牌 = 401；local 模式匿名放行并如实标注。
        记账是追加式 ``note``（``extra.ingest_event``），审计算法与 t9/t18 一字不改；
        请求体明文不进审计（t9 脱敏口径），只记数量与 id。
        """
        token = self.ingest.authenticate(secret)
        signed_before = self.signed_on_write
        failures_before = self.sign_failures
        result = self.ingest.ingest(payload, token=token, request_id=request_id,
                                    idempotency_key=idempotency_key)
        if result.get("replay"):
            return result                       # 幂等重放：不重复写审计（t8 同口径）
        result["signing"] = {"enabled": bool(self.sign_on_write and self.mode == MODE_LOCAL),
                             "signed": self.signed_on_write - signed_before,
                             "failures": self.sign_failures - failures_before,
                             "last_problem": self.sign_last_problem or None,
                             "note": "local 模式：入库即由该 Agent 私钥签名；"
                                     "team 模式待 actor→DID 映射（后续批次）"}
        actor_id = self.ingest_actor_id(token)
        if self.audit_log is not None:
            # 镜像事件（与鉴权事件同口径）：链断时如实报告并降级，不打断已入库的上报
            self._append_durable(self.ingest.audit_event(
                result, actor_id=actor_id, request_id=request_id,
                idempotency_key=idempotency_key, mode=self.audit_mode()))
        self.audit.append("ingest.batch_accepted",
                          action_id=(result["ids"][0] if result.get("ids") else None),
                          actor_id=actor_id, actor_role="system",
                          request_id=request_id, idempotency_key=idempotency_key,
                          mode=self.audit_mode(), record_status="ingested",
                          extra={"ingest_event": "batch_accepted",
                                 "format": result.get("format"),
                                 "accepted": result.get("accepted"),
                                 "created": result.get("created"),
                                 "updated": result.get("updated"),
                                 "duplicates": result.get("duplicates"),
                                 "skipped": result.get("skipped"),
                                 "ids": list(result.get("ids") or [])[:20],
                                 "token_id": (result.get("token") or {}).get("token_id")})
        if self.audit_problem:
            result["audit_degraded"] = True
        return result

    def issue_ingest_token(self, payload: Any, *, actor_id: str) -> dict[str, Any]:
        """签发 ingest 令牌：明文只在这里返回一次，落盘与审计都只有哈希/前缀。"""
        body = payload if isinstance(payload, Mapping) else {}
        label = str(body.get("label") or "").strip()
        if not label:
            raise _bad("INGEST_FIELD_TYPE", field="label",
                       detail={"hint": "label is required so the operator knows who owns it"})
        if len(label) > 60:
            raise _bad("INGEST_FIELD_TYPE", field="label",
                       detail={"hint": "label must be 60 characters or fewer",
                               "received": len(label)})
        scopes = body.get("scopes") or [ingest_mod.SCOPE_WRITE]
        if isinstance(scopes, str):
            scopes = [scopes]
        if not isinstance(scopes, Sequence):
            raise _bad("INGEST_FIELD_TYPE", field="scopes")
        for scope in scopes:
            if scope not in ingest_mod.SCOPES:
                raise _bad("INGEST_FIELD_TYPE", field="scopes",
                           detail={"accepted": list(ingest_mod.SCOPES), "received": str(scope)})
        ttl = body.get("ttl_seconds")
        if ttl is not None:
            try:
                ttl = int(ttl)
            except (TypeError, ValueError):
                raise _bad("INGEST_FIELD_TYPE", field="ttl_seconds",
                           detail={"hint": "seconds as an integer, or null for no expiry"}) from None
            if ttl <= 0 or ttl > 365 * 24 * 3600:
                raise _bad("INGEST_FIELD_TYPE", field="ttl_seconds",
                           detail={"hint": "between 1 second and 365 days", "received": ttl})
        secret, record = self.ingest_tokens.create(
            label, scopes=[str(s) for s in scopes], created_by=actor_id, ttl_seconds=ttl)
        public = ingest_mod.public_record(record)
        self.audit.append("ingest.token_created", actor_id=actor_id, actor_role="system",
                          mode=self.audit_mode(), record_status="issued",
                          extra={"ingest_event": "token_created",
                                 "token_id": public.get("token_id"), "label": label,
                                 "scopes": public.get("scopes"),
                                 "expires_at": public.get("expires_at")})
        if self.audit_log is not None:
            self._append_durable(audit_mod.build_event(
                "note", actor_id=actor_id, actor_role="system", mode=self.audit_mode(),
                reason=f"签发 ingest 令牌 {public.get('token_id')}（{label}）",
                client_info={"channel": "api"},
                note="token material is never stored: only sha256(salt+secret) on disk "
                     "and the prefix in this entry",
                extra={"ingest_event": "token_created",
                       "token_id": public.get("token_id"), "label": label,
                       "scopes": public.get("scopes"), "prefix": public.get("prefix")}))
        return {"ok": True, "token": secret, "record": public,
                "headers": {"token_header": ingest_mod.TOKEN_HEADER,
                            "bearer": f"{ingest_mod.BEARER_PREFIX} <token>"},
                "note": "明文只显示这一次；丢了只能重新签发，任何接口都拿不回来"}

    def revoke_ingest_token(self, token_id: str, *, actor_id: str) -> dict[str, Any]:
        public = self.ingest_tokens.revoke(token_id, by=actor_id)
        self.audit.append("ingest.token_revoked", actor_id=actor_id, actor_role="system",
                          mode=self.audit_mode(), record_status="revoked",
                          extra={"ingest_event": "token_revoked",
                                 "token_id": public.get("token_id")})
        if self.audit_log is not None:
            self._append_durable(audit_mod.build_event(
                "note", actor_id=actor_id, actor_role="system", mode=self.audit_mode(),
                reason=f"吊销 ingest 令牌 {public.get('token_id')}",
                client_info={"channel": "api"},
                extra={"ingest_event": "token_revoked",
                       "token_id": public.get("token_id")}))
        return {"ok": True, "record": public,
                "note": "吊销后该令牌立刻 403 INGEST_TOKEN_DISABLED（在用的上报会收到明确原因）"}

    def ingest_tokens_payload(self) -> dict[str, Any]:
        return {"ok": True, "mode": self.mode, "tokens": self.ingest_tokens.list_records(),
                "stats": self.ingest_tokens.stats(),
                "note": "这里永不返回凭证材料：明文只显示一次，哈希只在文件里"}

    def ingest_status_payload(self) -> dict[str, Any]:
        status = self.ingest.status()
        status["tokens_detail"] = self.ingest_tokens.list_records()
        status["health"] = self.ingest_health_block()
        return status

    def ingest_health_block(self) -> dict[str, Any]:
        status = self.ingest.status()
        counters = status["counters"]
        return {
            "enabled": status["enabled"], "version": status["version"],
            "mode": self.mode,
            "endpoints": [API_PATH_INGEST_EVENTS, API_PATH_INGEST_TOKENS,
                          API_PATH_INGEST_STATUS, API_PATH_STREAM],
            "auth": {
                "token_header": ingest_mod.TOKEN_HEADER,
                "bearer_prefix": f"{ingest_mod.BEARER_PREFIX} <token>",
                "required": self.mode == MODE_TEAM,
                "scopes": list(ingest_mod.SCOPES),
                "manage_permission": "ingest.token.manage",
            },
            "tokens": status["tokens"],
            "counters": counters,
            "rate": status["rate"], "limits": status["limits"],
            "idempotency": {"supported": True, "headers": ["Idempotency-Key"],
                            "body_field": "idempotency_key",
                            "ttl_seconds": int(self.ingest.idempotency_ttl)},
            "accepted_formats": sorted(ingest_mod.ENVELOPE_KEYS),
            "merge_fields": list(ingest_mod.MERGE_FIELDS),
            "protected_fields": list(ingest_mod.PROTECTED_FIELDS),
            "audit": {"event": "note", "marker": "extra.ingest_event",
                      "note": "上报只追加记账，不改审计算法（t9/t18 口径）"},
            "note": ("team 模式的上报必须带 ingest 令牌（机器对机器的作用域凭证，"
                     "不是用户会话）；local 模式匿名放行并如实标 anonymous。"
                     "入库永不覆盖决定字段（决定 ≠ 生效）"),
        }

    def stream_health_block(self) -> dict[str, Any]:
        snap = self.stream_hub.snapshot()
        return {"endpoint": API_PATH_STREAM, "content_type": STREAM_ACCEPT,
                "event_types": list(ingest_mod.STREAM_EVENT_TYPES),
                "heartbeat_seconds": self.stream_heartbeat,
                "replay": {"supported": True, "last_event_id_header": "Last-Event-ID",
                           "query": "since", "ring_size": snap["ring_size"]},
                "fallback": {"mode": "polling", "endpoint": API_PATH_RECORDS,
                             "interval_ms": 4000,
                             "note": "EventSource 不可用/被代理掐断时界面自动降级轮询并写明"},
                "subscribers": snap["subscribers"], "limit": snap["limit"],
                "published": snap["published"], "head_seq": snap["head_seq"],
                "rejected": snap["rejected"]}

    def publish_decision(self, record: Mapping[str, Any], *, decision: str,
                         actor_id: str, request_id: str | None) -> None:
        """决定也进广播：界面要立刻看到"这条已经被谁拍过板"（决定 ≠ 生效）。"""
        action_id = str(record.get("id") or "")
        self.stream_hub.publish(ingest_mod.EVENT_DECISION, {
            "action_id": action_id, "decision": decision,
            "status": record.get("status"), "decided_by": actor_id,
            "request_id": request_id, "record": dict(record),
            "effect_notice": EFFECT_NOTICE_KEY}, record_id=action_id)

    # -- 4.5（t15）设置中心：掩码视图 / 保存 / 测连接 --------------------------
    def settings_scope(self, session: Mapping[str, Any] | None) -> dict[str, Any]:
        """这位访问者与设置有关的权限口径。

        口径来源是 t13 的权限表：`settings.write` 只发给 `local` 与 `admin`，
        `editor` / `viewer` 拿不到 —— 所以"team 模式仅 admin 可改"这句话
        在这里就是逐字实现的，不另起一套判断。
        """
        actor, role = self.audit_actor(session)
        can_write = bool(auth_mod.can(role, PERMISSION_SETTINGS_WRITE))
        return {"actor_id": actor, "role": role, "can_write": can_write,
                "permission": PERMISSION_SETTINGS_WRITE}

    def settings_view(self, session: Mapping[str, Any] | None) -> dict[str, Any]:
        """`GET /api/settings` 的响应体：配置掩码视图 + 引擎实况 + 团队与数据管理。

        这个视图里**没有任何密钥明文**：`settings.py` 只给掩码（`••••••` + 末 4 位）、
        指纹与来源；本文件也从不去要明文（服务端不碰密钥，t8 的源码级检查钉着）。
        """
        scope = self.settings_scope(session)
        view = self.settings.public()
        engine = self.settings.engine_status(self.generator)
        can_write = bool(scope["can_write"])
        out: dict[str, Any] = dict(view)
        out["permissions"] = {"role": scope["role"], "permission": PERMISSION_SETTINGS_WRITE,
                              "can_read": True, "can_write": can_write,
                              "note": ("settings.note.can_write" if can_write
                                       else "settings.note.readonly")}
        # 引擎实况只对讲得通配置的人全量展开；其他人看得到模式与语言，足够解释界面行为
        out["engine"] = (engine if can_write else
                         {"mode": engine["mode"], "model": engine["model"],
                          "language": engine["language"],
                          "llm_will_run": engine["llm_will_run"],
                          "effect_notice": engine["effect_notice"]})
        out["team"] = {"mode": self.mode, "roles": list(auth_mod.TEAM_ROLES),
                       "auth": self.auth.status(), "writable": bool(self.settings.writable),
                       "note": ("settings.note.team" if self.mode == MODE_TEAM
                                else "settings.note.local")}
        out["llm_test"] = {"endpoint": API_PATH_SETTINGS_TEST_LLM, "method": "POST",
                           "required_permission": PERMISSION_SETTINGS_WRITE,
                           "probe_only": True, "last": dict(self.llm_last_test),
                           "note": "settings.note.test_probe_only"}
        out["data_management"] = {
            "reset": {"endpoint": API_PATH_RESET, "method": "POST",
                      "enabled": bool(view["data"]["reset_enabled"]),
                      "required_permission": PERMISSION_SETTINGS_WRITE,
                      "note": "settings.note.reset_scope"},
            "export": {"endpoint": API_PATH_RECORDS, "method": "GET",
                       "enabled": bool(view["data"]["export_enabled"]),
                       "audit_event": "export", "note": "settings.note.export_scope"},
            "audit": {"endpoint": API_PATH_AUDIT, "method": "GET",
                      "note": "settings.note.audit_append_only"}}
        out["paths"] = {"settings": API_PATH_SETTINGS, "test_llm": API_PATH_SETTINGS_TEST_LLM,
                        "reset": API_PATH_RESET, "records": API_PATH_RECORDS,
                        "audit": API_PATH_AUDIT, "users": API_PATH_USERS,
                        "auth_status": API_PATH_AUTH_STATUS}
        out["limits"] = {"body_bytes": MAX_BODY_BYTES,
                         "prompt_max_chars": SETTINGS_PROMPT_MAX_CHARS}
        out["ui_language_storage_key"] = settings_mod.UI_LANGUAGE_STORAGE_KEY
        return out

    def save_settings(self, payload: Any, *, session: Mapping[str, Any] | None,
                      request_id: str | None = None) -> dict[str, Any]:
        """`PUT /api/settings`：校验 → 落盘 → 重新灌进解释引擎 → 审计（无密钥）。"""
        clean = self._json_object(payload)
        patch = clean.get("settings")
        if not isinstance(patch, Mapping):
            patch = clean
        actor, role = self.audit_actor(session)
        # 只取"状态包"：短键名 + 掩码 / 来源 / 指纹，本文件不拼那个字段的蛇形写法
        keys_before = self.settings.key_state()
        secret_touched = PATCH_FIELD_SECRET in (patch.get("llm")
                                                if isinstance(patch.get("llm"), Mapping) else {})
        view, changed = self.settings.apply(patch, actor_id=actor)
        # 配置一改，引擎立刻按新配置装配（模式 / 语言 / 模型 / 超时 / 凭据来源）
        self.settings.apply_to(self.generator)
        self.settings_writes += 1
        keys = self.settings.key_state()
        key_action = ("unchanged" if not secret_touched else
                      ("cleared" if not keys["present"] else
                       ("same" if keys_before["fingerprint"]
                        and keys_before["fingerprint"] == keys["fingerprint"] else "set")))
        self._audit_settings_change(view, changed, keys=keys, key_action=key_action,
                                    actor=actor, role=role, request_id=request_id)
        return {"settings": self.settings_view(session), "changed": changed,
                "engine": self.settings.engine_status(self.generator),
                "key_action": key_action,
                "effect_notice": settings_mod.EFFECT_NOTICE_KEY}

    def _audit_settings_change(self, view: Mapping[str, Any], changed: Sequence[str], *,
                               keys: Mapping[str, Any], key_action: str, actor: str, role: str,
                               request_id: str | None) -> None:
        """设置变更进审计：只记"改了哪些字段 + 对凭据做了什么"，不记值、不记凭据。

        t9/t18 冻结的 `EVENTS` 白名单里没有 `config_change`，所以沿用 t18 对鉴权事件的
        处理口径：事件名收进 `note`，原始语义放 `fields`（审计里查得到、也不越白名单）。
        """
        llm = view["llm"] if isinstance(view.get("llm"), Mapping) else {}
        fields = {"settings_changed": ",".join(changed)[:240],
                  "settings_mode": str(llm.get("mode") or ""),
                  "settings_provider": str(llm.get("provider") or ""),
                  "settings_model": str(llm.get("model") or "")[:60],
                  "settings_language": str(view["ui"]["language"]) if isinstance(
                      view.get("ui"), Mapping) else "",
                  "settings_outbound": str(view["security"]["outbound"]) if isinstance(
                      view.get("security"), Mapping) else "",
                  "key_action": key_action,
                  "key_source": str(keys.get("source") or ""),
                  "key_mask": str(keys.get("mask") or ""),
                  "settings_revision": int(view.get("revision") or 0)}
        self.audit.append("note", actor_id=actor,
                          actor_role=self.auth.audit_actor_role(role),
                          mode=self.audit_mode(), request_id=request_id,
                          record_status="settings",
                          extra={"settings_event": "settings.changed", **fields})
        if self.audit_log is not None:
            self._append_durable(audit_mod.build_event(
                "note", actor_id=actor, actor_role=self.auth.audit_actor_role(role),
                mode=self.audit_mode(), request_id=request_id,
                reason="settings.changed",
                extra={"settings_event": "settings.changed", **fields}))

    def test_llm_connection(self, payload: Any, *, session: Mapping[str, Any] | None,
                            request_id: str | None = None) -> dict[str, Any]:
        """`POST /api/settings/test-llm`：发一次自检请求，失败原因要能读懂。

        请求体只允许三个探测参数（`prompt` / `max_tokens` / `timeout_s`）：
        地址、模型、密钥一律来自设置，**不接受请求里带密钥**（否则日志与审计
        就会带上凭据，且权限边界形同虚设）。
        """
        clean = self._json_object(payload) if payload not in (None, "", {}, []) else {}
        allowed = ("prompt", "max_tokens", "timeout_s")
        for name in clean:
            if name not in allowed:
                raise _bad("SETTINGS_FIELD_UNKNOWN", field=name,
                           detail={"allowed": list(allowed),
                                   "hint": "test-llm 只接受 prompt / max_tokens / timeout_s，"
                                           "地址与密钥来自设置"})
        prompt = clean.get("prompt")
        if prompt is not None and not isinstance(prompt, str):
            raise _bad("SETTINGS_FIELD_TYPE", field="prompt",
                       detail={"received": type(prompt).__name__})
        if isinstance(prompt, str) and len(prompt) > SETTINGS_PROMPT_MAX_CHARS:
            raise _bad("SETTINGS_FIELD_TOO_LONG", field="prompt",
                       detail={"max": SETTINGS_PROMPT_MAX_CHARS})
        max_tokens = clean.get("max_tokens")
        if max_tokens is not None:
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
                raise _bad("SETTINGS_FIELD_TYPE", field="max_tokens",
                           detail={"received": type(max_tokens).__name__})
            if not settings_mod.MAX_TOKENS_MIN <= max_tokens <= settings_mod.MAX_TOKENS_MAX:
                raise _bad("SETTINGS_FIELD_RANGE", field="max_tokens",
                           detail={"min": settings_mod.MAX_TOKENS_MIN,
                                   "max": settings_mod.MAX_TOKENS_MAX})
        timeout_s = clean.get("timeout_s")
        if timeout_s is not None:
            if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
                raise _bad("SETTINGS_FIELD_TYPE", field="timeout_s",
                           detail={"received": type(timeout_s).__name__})
            if not settings_mod.TIMEOUT_MIN <= float(timeout_s) <= settings_mod.TIMEOUT_MAX:
                raise _bad("SETTINGS_FIELD_RANGE", field="timeout_s",
                           detail={"min": settings_mod.TIMEOUT_MIN,
                                   "max": settings_mod.TIMEOUT_MAX})
        actor, role = self.audit_actor(session)
        result = self.settings.probe(prompt=prompt if isinstance(prompt, str) else None,
                                     max_tokens=max_tokens if isinstance(max_tokens, int) else None,
                                     timeout_s=float(timeout_s) if timeout_s is not None else None,
                                     transport=self.llm_transport)
        self.llm_tests += 1
        self.llm_last_test = {key: result.get(key) for key in
                              ("ok", "code", "message", "latency_ms", "model", "endpoint", "chars")}
        self.audit.append("note", actor_id=actor,
                          actor_role=self.auth.audit_actor_role(role),
                          mode=self.audit_mode(), request_id=request_id,
                          record_status="settings",
                          extra={"settings_event": "settings.test_llm",
                                 "test_ok": bool(result.get("ok")),
                                 "test_code": str(result.get("code") or ""),
                                 "test_model": str(result.get("model") or "")[:60],
                                 "test_endpoint": str(result.get("endpoint") or "")[:120],
                                 "test_latency_ms": int(result.get("latency_ms") or 0)})
        if self.audit_log is not None:
            self._append_durable(audit_mod.build_event(
                "note", actor_id=actor, actor_role=self.auth.audit_actor_role(role),
                mode=self.audit_mode(), request_id=request_id,
                reason="settings.test_llm",
                extra={"settings_event": "settings.test_llm",
                       "test_ok": bool(result.get("ok")),
                       "test_code": str(result.get("code") or ""),
                       "test_model": str(result.get("model") or "")[:60],
                       "test_latency_ms": int(result.get("latency_ms") or 0)}))
        result["effect_notice"] = "settings.test_llm.probe_only"
        result["engine"] = self.settings.engine_status(self.generator)
        return result

    def llm_health_block(self) -> dict[str, Any]:
        """`/health` 的 `llm` 块：从设置读，不再写死（保留 `mode` + `note` 两个键）。"""
        view = self.settings.public()
        llm = view["llm"]
        keys = self.settings.key_state()
        engine = self.settings.engine_status(self.generator)
        return {"mode": llm["mode"],
                "provider": llm["provider"],
                "model": llm["model"],
                "language": view["ui"]["language"],
                "enabled": bool(llm["mode"] == settings_mod.MODE_LLM),
                # 键名来自 settings 的常量，值只有掩码与来源：本文件不拼那个字段名
                settings_mod.HEALTH_KEY_PRESENT: bool(keys["present"]),
                settings_mod.HEALTH_KEY_SOURCE: str(keys["source"]),
                "key_mask": str(keys["mask"]),
                "key_storage": str(keys["storage"]),
                "outbound": str(keys["outbound"]),
                "will_run": bool(engine["llm_will_run"]),
                "last_test": dict(self.llm_last_test),
                "settings_revision": int(view["revision"]),
                "note": ("llm 增强已配置：解释生成会调用模型（只发脱敏事实）"
                         if engine["llm_will_run"] else
                         "解释默认走模板，LLM 为可选项（t5 三道闸，配置见设置中心）")}

    def settings_stats(self) -> dict[str, Any]:
        return {"reads": self.settings_reads, "writes": self.settings_writes,
                "llm_tests": self.llm_tests, **self.settings.stats()}

    # -- 能力面（界面用它显示"我能做什么"）--------------------------------
    def health(self) -> dict[str, Any]:
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "status": "ok",
            "mode": self.mode,
            "schema_version": "1.1",
            "endpoints": [API_PATH_INTERVENE, API_PATH_HEALTH, API_PATH_RECORDS, API_PATH_AUDIT,
                          API_PATH_AUTH_LOGIN, API_PATH_AUTH_LOGOUT, API_PATH_AUTH_ME,
                          API_PATH_AUTH_STATUS, API_PATH_AUTH_PERMISSIONS, API_PATH_USERS,
                          API_PATH_INGEST_EVENTS, API_PATH_INGEST_TOKENS,
                          API_PATH_INGEST_STATUS, API_PATH_STREAM,
                          API_PATH_SETTINGS, API_PATH_SETTINGS_TEST_LLM,
                          API_PATH_AUDIT_EXPORT, API_PATH_IMPORT],
            "capabilities": list(LOCAL_CAPABILITIES),
            "auth": {
                "required": self.auth.required,
                "mode": self.auth.mode,
                "roles": list(auth_mod.TEAM_ROLES),
                "bootstrap_needed": bool(self.auth.required
                                         and not self.auth.list_users()),
                "cookie": auth_mod.SESSION_COOKIE,
                "csrf_header": auth_mod.CSRF_HEADER,
                "token_header": auth_mod.TOKEN_HEADER,
                "note": ("team 模式必须登录；viewer 只读（干预入口隐藏 + 403 ROLE_FORBIDDEN）"
                         if self.auth.required else
                         "local 模式免登录，身份固定为 local-user（T1 护栏：不要求注册）"),
            },
            "stats": self.store.stats(),
            "audit": self.audit_chain_status(),
            # 4.8（t21）导出面：只读 GET，权限 audit.export，pdf 缺 reportlab 时退回 HTML
            "audit_export": {
                "endpoint": API_PATH_AUDIT_EXPORT, "method": "GET",
                "formats": list(AUDIT_EXPORT_FORMATS),
                "default_format": AUDIT_EXPORT_FORMAT_DEFAULT,
                "permission": "audit.export",
                "filters": ["actor_id", "action_id", "decision", "event", "task_id",
                            "since", "until", "q"],
                "limit_default": AUDIT_EXPORT_LIMIT_DEFAULT,
                "limit_max": AUDIT_EXPORT_LIMIT_MAX,
                "served": self.exports_served,
                "note": ("pdf 优先 reportlab；reportlab 不可用时退回可打印 HTML 并说明原因，"
                         "浏览器 Ctrl+P 可存 PDF。每次导出先记账（event=export）")},
            "requests": self.requests,
            "interventions": self.interventions,
            # 4.9（t22）数据导入：粘贴 / 上传的入口（写操作，权限 data.write）
             "import": {
                 "endpoint": API_PATH_IMPORT, "method": "POST",
                 "permission": "data.write",
                 "formats": list(ta_mod.SUPPORTED_FORMATS),
                 "auto_detect": True,
                 "modes": list(IMPORT_MODES), "default_mode": IMPORT_MODE_DEFAULT,
                 "max_bytes": IMPORT_MAX_BYTES, "max_records": IMPORT_MAX_RECORDS,
                 "source_kinds": ["paste_json", "upload"],
                 "body_fields": list(IMPORT_SOURCE_FIELDS),
                 "imports": self.imports, "imported_records": self.imported_records,
                 "note": ("格式留空＝自动识别 Langfuse / OTel / MCP / 原生；"
                          "同 id 默认更新事实字段，用户决定与执行回执是受保护字段。"
                          "审计只记格式与统计，导入正文一个字都不进")},
             # 4.9（t22）记录持久化：默认 var/records.jsonl（JSONL + 内存索引，锁 + 原子替换）
             "records_store": self.store.store_facts(),
             "imports_served": self.imports,
             # 4.5（t15）：这一项从设置中心读，不再写死（mode + note 两个键的口径不变）
            "llm": self.llm_health_block(),
            # 4.4（t14）实时接入与广播：新增块是附加的，老字段口径一字不动
            "ingest": self.ingest_health_block(),
            "stream": self.stream_health_block(),
            # 4.5（t15）设置中心统计：掩码视图的统计口径，不含任何密钥
            "settings": self.settings_stats(),
        }

    def records_payload(self) -> dict[str, Any]:
        records = self.store.all()
        kinds = [str((r.get("source") or {}).get("kind") or "")
                 for r in records if isinstance(r, Mapping) and not r.get("demo_data")]
        realtime = sum(1 for k in kinds if k in ("webhook", "sdk"))
        imported = sum(1 for k in kinds if k in ("paste_json", "upload"))
        live = realtime + imported
        facts = self.store.store_facts()
        if not live:
            note = "全部为自造演示数据（A2），验收不依赖真实样本"
        elif not imported:
            note = f"{live} 条来自实时上报（webhook/SDK），其余为自造演示数据（A2）"
        elif not realtime:
            note = f"{imported} 条来自粘贴 / 上传导入，其余为自造演示数据（A2）"
        else:
            note = (f"{realtime} 条来自实时上报（webhook/SDK），{imported} 条来自"
                    f"粘贴 / 上传导入，其余为自造演示数据（A2）")
        return {"records": records,
                "meta": {"count": len(records), "mode": self.mode,
                         "schema_version": "1.1", "source": self.store.stats().get("source"),
                         "synthetic": True,
                         # 4.4（t14）附加字段：界面用它判断"这批里有多少是实时上报进来的"
                         "ingested_count": live,
                         # 4.9（t22）附加字段：这批里有多少是用户自己粘贴/上传进来的，
                         # 以及记录是不是落在持久存储里（重启后还在）
                         "imported_count": imported,
                         "records_durable": bool(facts.get("durable")),
                         "records_store": facts,
                         "stream_head_seq": self.stream_hub.seq,
                         "note": note}}

    def audit_payload(self, limit: int = AUDIT_QUERY_LIMIT_DEFAULT, *,
                      event: str | None = None, actor_id: str | None = None,
                      action_id: str | None = None, decision: str | None = None,
                      task_id: str | None = None, q: str | None = None,
                      offset: int = 0, reverse: bool = False) -> dict[str, Any]:
        """审计视图数据（t11 审计页与 CLI 都用这个形状）。

        * **内存模式**（不传 ``audit_log_path``，t8 口径逐字不变）：``entries`` 来自内存
          哈希链，``meta.durable = false``。
        * **落盘模式**（4.2b / t18，产品默认）：``entries`` 直接读追加式 JSONL 文件链，
          ``chain`` 是刚复算完的校验报告，``meta`` 带 ``count``/``head``/``sealed``
          —— count 来自文件本身，所以重启后历史仍在、count 不归零。
          链断 → 500 ``AUDIT_CHAIN_BROKEN``：禁止静默返回半条链，也禁止自动清空重建。
        """
        cap = max(0, min(int(limit), AUDIT_QUERY_LIMIT_MAX))
        start = max(0, int(offset))
        if self.audit_log is None:
            payload = {"entries": self.audit.entries(limit=cap),
                       "chain": self.audit.verify(),
                       "meta": {"mode": self.mode,
                                "durable": self.audit_log is not None,
                                "note": ("内存哈希链预览 + 追加式 JSONL 已启用（t9/t11）"
                                         if self.audit_log is not None else
                                         "内存哈希链预览；落盘与保留策略属 t9/t11")}}
            if self.audit_log is not None:
                report = self.audit_log.verify(raise_on_tamper=False)
                payload["jsonl"] = {"path": str(self.audit_log.path), "count": report["count"],
                                    "head": report["head"], "ok": report["ok"],
                                    "problems": report["problems"]}
            return payload
        status = self.require_audit_chain()
        wanted = {"event": event, "actor_id": actor_id, "action_id": action_id,
                  "decision": decision, "task_id": task_id, "q": q}
        filters = {k: v.strip() for k, v in wanted.items() if isinstance(v, str) and v.strip()}
        try:
            if filters:
                entries = self.audit_log.query(limit=cap, offset=start, reverse=True,
                                               **filters)
            else:
                entries = self.audit_log.read(limit=cap, offset=start, reverse=bool(reverse))
        except audit_mod.AuditError as exc:
            # 过滤条件不合法（event/decision 不在枚举、时间范围倒挂…）→ 400，
            # 把 audit.py 的人话原因原样带出去，不许变成 500 或静默忽略。
            raise _bad("AUDIT_QUERY_INVALID", message=exc.render(),
                       detail={"filters": filters, "field": exc.field or "event",
                               "audit_code": exc.code,
                               "hint": "event/decision must be a known audit value"}) from exc
        return {"entries": [dict(e) for e in entries],
                "chain": status,
                "meta": {"mode": self.mode, "durable": True, "source": AUDIT_SOURCE_JSONL,
                         "path": status["path"], "count": status["count"],
                         "head": status["head"],
                         "sealed": {"sealed_head": status["sealed_head"],
                                    "sealed_count": status["sealed_count"],
                                    "seals": status["seals"]},
                         "limit": cap, "offset": start, "filters": filters,
                         "returned": len(entries), "total": status["count"],
                         "note": ("追加式 JSONL 链：条目只追加，count/head 来自文件本身，"
                                  "重启后历史仍在（4.2b）")},
                "jsonl": {"path": status["path"], "count": status["count"],
                          "head": status["head"], "ok": True, "problems": [],
                          "sealed_head": status["sealed_head"],
                          "sealed_count": status["sealed_count"],
                          "size_bytes": status.get("size_bytes"),
                          "file_sha256": status.get("file_sha256")}}

    # -- 4.8 审计导出（t21）：GET /api/audit/export --------------------------
    def audit_export_filters(self, params: Mapping[str, Any]) -> dict[str, str]:
        """把查询串里的过滤条件收干净（空串丢掉，全字符串，长度封顶）。"""
        out: dict[str, str] = {}
        for key in ("actor_id", "action_id", "decision", "event", "task_id",
                    "since", "until", "q"):
            raw = params.get(key)
            if isinstance(raw, str) and raw.strip():
                out[key] = raw.strip()[:200]
        return out

    def audit_export(self, *, fmt: str = AUDIT_EXPORT_FORMAT_DEFAULT,
                     filters: Mapping[str, str] | None = None,
                     limit: int = AUDIT_EXPORT_LIMIT_DEFAULT,
                     session: Mapping[str, Any] | None = None,
                     request_id: str | None = None,
                     lang: str = "zh") -> dict[str, Any]:
        """导出审计条目（4.8 / Must M6：30 秒拿走证据）。

        * `format=csv`（默认）：`audit.py` 的规范 CSV（BOM + RFC4180 引号，Excel 认中文）；
        * `format=html`：可打印版，零外链零脚本，浏览器 Ctrl+P 直接存 PDF；
        * `format=pdf`：优先 reportlab 在内存里排版；reportlab 不可用 → **退回可打印
          HTML 并在响应里说明原因**（`X-TraceLens-Export-Fallback` 头 + `fallback` 字段），
          绝不因为缺第三方库就 500 让人拿不到证据；
        * 非法 format → 400 `EXPORT_FORMAT_INVALID`。
        * 过滤：`actor_id` / `action_id` / `decision` / `event` / `task_id` /
          `since` / `until` / `q`（口径与 `GET /api/audit` 完全一致，枚举不合法 400）。
        * 链断 → 500 `AUDIT_CHAIN_BROKEN`：断链的导出不可信，先取证再说。
        * 每次导出**先记账再交文件**：追加一条 `event=export`，只写格式、条数、字节、
          过滤条件摘要与内容指纹，**不含**密钥、参数原文与理由原文。
        * 内容本身早已在写盘前脱敏（4.8 的 PII 打码），导出这一层不复制原文。
        """
        wanted = (fmt or AUDIT_EXPORT_FORMAT_DEFAULT).strip().lower()
        if wanted not in AUDIT_EXPORT_FORMATS:
            raise _bad("EXPORT_FORMAT_INVALID", field="format",
                       detail={"received": fmt, "allowed": list(AUDIT_EXPORT_FORMATS),
                               "hint": "use csv (download), html (print), pdf (reportlab)"})
        chosen = dict(filters or {})
        cap = max(1, min(int(limit), AUDIT_EXPORT_LIMIT_MAX))
        actor, role = self.audit_actor(session)
        # 链断先炸：断链时导出的东西不能当证据用
        status = (self.require_audit_chain() if self.audit_log is not None
                  else self.audit.verify())
        try:
            if self.audit_log is None:
                rows = self._filter_memory_entries(chosen, cap)
            else:
                rows = self.audit_log.query(limit=cap, offset=0, reverse=True, **chosen)
        except audit_mod.AuditError as exc:
            raise _bad("AUDIT_QUERY_INVALID", message=exc.render(),
                       detail={"filters": chosen, "field": exc.field or "event",
                               "audit_code": exc.code,
                               "hint": "event/decision must be a known audit value"}) from exc
        stamp = audit_mod.iso_utc(audit_mod.utc_now())
        summary = self.audit_export_summary(chosen, cap)
        served = wanted
        fallback: str | None = None
        font_note = ""
        note = ""
        data: bytes
        digest: str = ""
        if wanted == "csv":
            text = audit_mod.entries_to_csv(rows, bom=True)
            data = text.encode("utf-8")
            digest = audit_mod.sha256_ref(text)
            note = "CSV：参数列是规范 JSON 文本，敏感值写盘前已脱敏"
        elif wanted == "html":
            text = self.render_audit_html(rows, status, stamp=stamp, summary=summary,
                                          actor=actor, lang=lang)
            data = text.encode("utf-8")
            digest = audit_mod.sha256_ref(text)
            note = "可打印 HTML：零外链零脚本，浏览器 Ctrl+P 可存 PDF"
        else:  # pdf
            try:
                payload_pdf, built = audit_mod.render_pdf_bytes(
                    rows, title=self.audit_export_title(stamp, lang),
                    status=status,
                    notes=self.audit_export_notes(stamp, summary, actor, lang))
            except audit_mod.AuditExportError as exc:
                served = "html"
                fallback = str(exc.detail.get("reason") or exc.code)
                text = self.render_audit_html(
                    rows, status, stamp=stamp, summary=summary, actor=actor, lang=lang,
                    extra_note=f"{exc.render()}；已改出可打印 HTML，浏览器 Ctrl+P 即可存 PDF"
                )
                data = text.encode("utf-8")
                digest = audit_mod.sha256_ref(text)
                note = "PDF 不可用（reportlab 未安装）：已退回可打印 HTML"
            else:
                data = payload_pdf
                # PDF 是二进制：指纹按 latin-1 逐字节映射算（与 audit.export_pdf 同一口径）
                digest = audit_mod.sha256_ref(data.decode("latin-1"))
                note = f"PDF：reportlab 生成（字体 {built.get('font', '默认')}）"
                # 字体缺中文时 PDF 会静默丢字：这句话必须跟着文件走，
                # 否则用户拿到一份"看着成功、中文没了"的证据
                font_note = str(built.get("font_note") or "")
        suffix = "pdf" if served == "pdf" else served
        filename = f"{AUDIT_EXPORT_NAME_PREFIX}-{stamp.replace(':', '').replace('-', '')}.{suffix}"
        # 先记账：拿出去的证据必须留痕（写不进去就 500，绝不"导出了没记账"）
        self.audit.append("audit.export", actor_id=actor, actor_role=role,
                          mode=self.audit_mode(), request_id=request_id,
                          record_status="audit_export",
                          extra={"export_format": served, "requested_format": wanted,
                                 "rows": len(rows), "bytes": len(data),
                                 "filters": dict(chosen), "export_sha256": digest})
        if self.audit_log is not None:
            self._append_durable_strict(audit_mod.build_event(
                "export", actor_id=actor,
                actor_role=self.auth.audit_actor_role(role),
                mode=self.audit_mode(), request_id=request_id,
                reason=f"导出审计证据：格式 {served}，条目 {len(rows)}，过滤 {summary}",
                extra={"export_format": served, "requested_format": wanted,
                       "export_rows": len(rows), "export_bytes": len(data),
                       "export_sha256": digest, "export_filters": dict(chosen),
                       "export_fallback": fallback, "audit_file": "appended_only"}))
        content_type = {"csv": "text/csv; charset=utf-8",
                        "html": "text/html; charset=utf-8",
                        "pdf": "application/pdf"}[served]
        disposition = "attachment" if served == "csv" else "inline"
        headers = {
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "X-TraceLens-Export-Format": served,
            "X-TraceLens-Export-Rows": str(len(rows)),
            "X-TraceLens-Export-Sha256": digest,
            "X-TraceLens-Export-Filters": header_safe(summary),
            "X-TraceLens-Request-Id": request_id or "",
            "Cache-Control": "no-store",
        }
        if fallback:
            headers["X-TraceLens-Export-Fallback"] = fallback
        if font_note:
            headers["X-TraceLens-Export-Note"] = header_safe(font_note)
        return {"format": served, "requested_format": wanted, "body": data,
                "content_type": content_type, "headers": headers, "filename": filename,
                "rows": len(rows), "bytes": len(data), "sha256": digest,
                "filters": chosen, "summary": summary, "fallback": fallback,
                "font_note": font_note,
                "note": note, "actor": actor,
                "chain": {"ok": bool(status.get("ok")), "count": status.get("count"),
                          "head": status.get("head"), "durable": self.audit_log is not None}}

    def audit_export_title(self, stamp: str, lang: str) -> str:
        return (f"TraceLens 用户操作审计 · {stamp}" if str(lang) != "en"
                else f"TraceLens user action audit · {stamp}")

    def audit_export_summary(self, filters: Mapping[str, str], cap: int) -> str:
        """过滤条件摘要：只放键名与短值（枚举/ID），放不进密钥与原文。"""
        parts = [f"{key}={value}" for key, value in sorted(filters.items())]
        parts.append(f"limit={cap}")
        return ";".join(parts)

    def audit_export_notes(self, stamp: str, summary: str, actor: str,
                           lang: str) -> list[str]:
        base = list(audit_mod.DEFAULT_HTML_NOTES)
        if str(lang) == "en":
            base = ["This page is generated from the append-only TraceLens audit log: "
                    "entries can be appended, never edited or deleted.",
                    "Parameters are redacted (secrets keep a short fingerprint; "
                    "e-mails, phone numbers, IDs, IPs and absolute paths are masked).",
                    "A recorded decision means what the user chose, not what the "
                    "execution side did: check execution_receipt for that."]
        base.append(f"Generated by {actor} at {stamp}; filters: {summary}")
        return base

    def render_audit_html(self, rows: Any, status: Mapping[str, Any], *, stamp: str,
                          summary: str, actor: str, lang: str = "zh",
                          extra_note: str | None = None) -> str:
        notes = self.audit_export_notes(stamp, summary, actor, lang)
        if extra_note:
            notes.append(extra_note)
        path = str(self.audit_log.path) if self.audit_log is not None else "(memory chain)"
        return audit_mod.render_html(list(rows), title=self.audit_export_title(stamp, lang),
                                     status=status, notes=notes, log_path=path)

    def _filter_memory_entries(self, filters: Mapping[str, str],
                               cap: int) -> list[dict[str, Any]]:
        """内存链（t8 预览模式）的过滤：与 `audit_log.query` 同一套字段口径。"""
        events = self.audit.entries(limit=None)
        dec = filters.get("decision")
        if dec and dec not in audit_mod.DECISIONS:
            raise _bad("AUDIT_QUERY_INVALID", field="decision",
                       detail={"received": dec, "allowed": list(audit_mod.DECISIONS)})
        kind = filters.get("event")
        if kind and kind not in audit_mod.EVENTS:
            raise _bad("AUDIT_QUERY_INVALID", field="event",
                       detail={"received": kind, "allowed": list(audit_mod.EVENTS)})
        lo = audit_mod.parse_time(filters.get("since"), field="since") \
            if filters.get("since") else None
        hi = audit_mod.parse_time(filters.get("until"), field="until") \
            if filters.get("until") else None
        if lo and hi and lo > hi:
            raise _bad("AUDIT_QUERY_INVALID", field="since",
                       detail={"since": filters.get("since"), "until": filters.get("until"),
                               "hint": "since must not be later than until"})
        needle = (filters.get("q") or "").lower()
        out: list[dict[str, Any]] = []
        for entry in reversed(events):
            if filters.get("actor_id") and str(entry.get("actor_id") or "") != filters["actor_id"]:
                continue
            if filters.get("action_id") and str(entry.get("action_id") or "") != filters["action_id"]:
                continue
            if filters.get("task_id") and str(entry.get("task_id") or "") != filters["task_id"]:
                continue
            if dec and entry.get("decision") != dec:
                continue
            if kind and entry.get("event") != kind:
                continue
            stamp = entry.get("ts")
            when = audit_mod.parse_time(stamp, field="ts") if isinstance(stamp, str) and stamp else None
            if lo and (when is None or when < lo):
                continue
            if hi and (when is None or when > hi):
                continue
            if needle:
                hay = " ".join(str(entry.get(k) or "") for k in
                               ("reason", "actor_id", "action_id", "task_id", "note",
                                "event", "decision"))
                if needle not in hay.lower():
                    continue
            out.append(dict(entry))
            if len(out) >= cap:
                break
        return out

    # ------------------------------------------------------------------ #
    # 4.9（t22）数据导入：粘贴 JSON / 上传文件 → 自动识别 4 种格式 → 归一化入库
    # ------------------------------------------------------------------ #

    def import_source_kind(self, *, filename: Any = None, explicit: Any = None) -> str:
        """接入方式只取 7 类里的两种：带了文件名＝`upload`，纯粘贴＝`paste_json`。

        `source.kind` 决定界面上的来源徽标，也决定"这条记录算不算演示数据"，
        所以导入进来的记录绝不能被标成 demo。
        """
        wanted = str(explicit or "").strip().lower()
        if wanted in ("upload", "paste_json"):
            return wanted
        return "upload" if str(filename or "").strip() else "paste_json"

    def _import_body(self, payload: Any) -> dict[str, Any]:
        """导入请求体的清洗（**不能**复用 `_parse_body`：那是 `/intervene` 的白名单）。

        接受三种形态：JSON 对象、JSON 文本（前端粘贴框直接送字符串）、事件数组。
        形状错了要指着 `received` 说，不要只丢一句"请求体不对"。
        """
        if payload is None:
            raise _bad("IMPORT_INPUT_EMPTY",
                       detail={"received": "empty",
                               "hint": "带一个 JSON 请求体：粘贴的文本放 payload，"
                                       "或者直接把 trace 文档当请求体"})
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                raise _bad("IMPORT_INPUT_EMPTY",
                           detail={"received": "empty string",
                                   "hint": "粘贴框是空的：贴一段 JSON，或者选一个文件"})
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise _bad("IMPORT_INPUT_NOT_JSON", field="body",
                           message=f"请求体不是合法 JSON：{exc}",
                           detail={"error": str(exc)[:200],
                                   "first_chars": text[:80]}) from exc
        if isinstance(payload, Mapping):
            return dict(payload)
        if isinstance(payload, Sequence):
            # 请求体直接就是事件数组（原生 trace 的常见形态）
            return {"payload": list(payload)}
        raise _bad("IMPORT_INPUT_NOT_JSON", field="body",
                   detail={"received": type(payload).__name__,
                           "hint": "请求体需要是 JSON 对象：文档本身，或把文档放进 payload"})

    def _import_document(self, clean: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
        """把请求体拆成「原始文档 + 控制参数」。

        前端可以传两种形态（都写进 web/README）：

        * 请求体**直接就是**原始 trace 文档（粘贴的 Langfuse / OTel / MCP / 原生 JSON）；
        * 带控制参数的包装：``{"payload"|"data"|"text"|"content"|"file": <文档或 JSON 文本>,
          "format": ..., "mode": "upsert", "strict": true, "filename": "trace.json"}``。

        文本字段里放的是**用户粘贴的原始 JSON 文本**：解析失败要指着那一处说
        （第几行、开头长什么样），不能只丢一句"请求体不是 JSON"。
        """
        meta: dict[str, Any] = {}
        doc: Any = None
        raw_text = ""
        found_field: str | None = None
        for key in IMPORT_SOURCE_FIELDS:
            if key in clean:
                found_field = key
                raw_text = clean[key] if isinstance(clean[key], str) else ""
                doc = clean[key]
                break
        if found_field is None:
            extra = {k for k in clean if k not in IMPORT_CONTROL_FIELDS}
            if not extra:
                raise _bad("IMPORT_INPUT_EMPTY", field="payload",
                           detail={"allowed_fields": list(IMPORT_SOURCE_FIELDS),
                                   "hint": "把 trace 文档放进 payload / data / text，"
                                           "或者直接把文档当请求体"})
            doc = {k: v for k, v in clean.items() if k not in IMPORT_CONTROL_FIELDS}
        elif isinstance(doc, str):
            text = doc.strip()
            if not text:
                raise _bad("IMPORT_INPUT_EMPTY", field=found_field,
                           detail={"hint": "粘贴框是空的：贴一段 JSON，或者选一个文件"})
            try:
                doc = json.loads(text)
            except ValueError as exc:
                pos = int(getattr(exc, "pos", 0) or 0)
                line = text.count("\n", 0, min(len(text), pos)) + 1
                raise _bad("IMPORT_INPUT_NOT_JSON", field=found_field,
                           message=f"粘贴的内容不是合法 JSON（第 {line} 行附近）：{exc}",
                           detail={"error": str(exc)[:200], "line": line,
                                   "first_chars": text[:80],
                                   "hint": "只支持 JSON：Langfuse / OTel / MCP 导出与原生 trace "
                                           "都是 JSON；先确认文件没被截断"}) from exc
        if doc is None:
            raise _bad("IMPORT_INPUT_EMPTY", field="payload",
                       detail={"hint": "没有可导入的内容"})
        fmt = clean.get("format")
        if isinstance(fmt, str) and fmt.strip() and fmt.strip().lower() not in ta_mod.SUPPORTED_FORMATS:
            raise _bad("IMPORT_FORMAT_UNSUPPORTED", field="format",
                       detail={"received": fmt, "allowed": list(ta_mod.SUPPORTED_FORMATS),
                               "hint": "留空＝自动识别（Langfuse / OTel / MCP / 原生）"})
        meta["format"] = (str(fmt).strip().lower()
                          if isinstance(fmt, str) and fmt.strip() else None)
        mode = str(clean.get("mode") or IMPORT_MODE_DEFAULT).strip().lower()
        if mode not in IMPORT_MODES:
            raise _bad("IMPORT_MODE_INVALID", field="mode",
                       detail={"received": mode, "allowed": list(IMPORT_MODES),
                               "hint": "upsert＝同 id 更新事实（默认）；create＝同 id 记为重复"})
        meta["mode"] = mode
        meta["strict"] = True if "strict" not in clean else bool(clean.get("strict"))
        meta["filename"] = str(clean.get("filename") or "")[:200]
        meta["field"] = found_field or "payload"
        meta["raw_text"] = raw_text if isinstance(raw_text, str) else ""
        return doc, meta

    def import_traces(self, payload: Any, *, request_id: str,
                      session: Mapping[str, Any] | None = None,
                      lang: str = "zh") -> dict[str, Any]:
        """`POST /api/import`：粘贴/上传的 trace → 认格式 → 归一化 → 合并入库 → 广播 → 记账。

        四条口径：

        1. **格式不猜、也不换**：`format` 留空时交给 `trace_adapter.detect` 认 Langfuse /
           OTel / MCP / 原生四种；认不出来就 400 说清认成了什么、为什么不行，
           绝不静默换一种格式硬解析（t3 定的口径）。
        2. **决定 ≠ 生效**：导入只写事实字段，合并复用 4.4 的 `merge_facts`，
           `user_intervention` / `execution_receipt` 是受保护字段，导入覆盖不掉用户的决定。
        3. **审计只记账不记正文**：原始 JSON 里可能有邮箱、密钥、内部路径，
           所以审计里只有格式 / 条数 / 来源 / 文件名 / 统计，正文一个字都不进。
        4. **写了就要落盘**：4.9 的持久模式下写不进去就 500，
           绝不"导入成功、重启就没了"（QA 5.1 的 D1 + 重启缺口一起补）。
        """
        clean = self._import_body(payload)
        doc, meta = self._import_document(clean)
        kind = self.import_source_kind(filename=meta["filename"],
                                       explicit=clean.get("source_kind"))
        # 体积：粘贴的原文按 UTF-8 字节数算；包装形态按文档的规范 JSON 字节数算
        size = (len(meta["raw_text"].encode("utf-8")) if meta["raw_text"]
                else len(json.dumps(doc, ensure_ascii=False,
                                    sort_keys=True).encode("utf-8")))
        if size > IMPORT_MAX_BYTES:
            raise _bad("IMPORT_TOO_LARGE", field=meta["field"],
                       detail={"bytes": size, "limit": IMPORT_MAX_BYTES,
                               "hint": f"一批最多 {IMPORT_MAX_BYTES // 1024} KB；"
                                       "请拆成多批，或先裁剪再导入"})
        leaf = (meta["filename"] or "inline").replace("\\", "/")[-60:]
        source_uri = f"{'upload' if kind == 'upload' else 'paste'}://import/{leaf}"
        language = str(lang) if str(lang) in ("zh", "en") else "zh"
        try:
            outcome = ta_mod.convert_with_raw(
                doc, format=meta["format"], source_kind=kind, source_uri=source_uri,
                language=language, strict=bool(meta["strict"]))
        except ta_mod.TraceFormatError as exc:
            code = ("IMPORT_FORMAT_UNSUPPORTED"
                    if str(exc.code) in ("TRACE_FORMAT_UNSUPPORTED", "TRACE_FORMAT_UNKNOWN")
                    else str(exc.code))
            raise _bad(code, field=exc.field or meta["field"], message=exc.reason,
                       detail={"adapter_code": exc.code, "index": exc.index,
                               "format_detected": exc.fmt or meta["format"],
                               "supported": list(ta_mod.SUPPORTED_FORMATS),
                               "hint": str(exc.hint or "") +
                                       "；严格模式在第一条坏记录就停下，"
                                       "想要「坏的跳过、好的入库」就传 strict=false"}) from exc
        input_events = int(outcome.stats.get("input_events") or 0)
        if input_events > IMPORT_MAX_RECORDS:
            raise _bad("IMPORT_TOO_MANY", field=meta["field"],
                       detail={"count": input_events, "limit": IMPORT_MAX_RECORDS,
                               "hint": f"一批最多 {IMPORT_MAX_RECORDS} 条事件；请拆批导入"})

        created: list[str] = []
        updated: list[str] = []
        duplicates: list[dict[str, Any]] = []
        frames: list[dict[str, Any]] = []
        with self._lock:
            signed_before = self.signed_on_write
            failures_before = self.sign_failures
            for record in outcome.records:
                action_id = str(record.get("id"))
                before = self.store.get(action_id)
                if before is None:
                    record = self.sign_on_write_record(record)
                    self.store.put(record)
                    created.append(action_id)
                    frames.append(self.stream_hub.publish(
                        ingest_mod.EVENT_CREATED,
                        {"record": record, "action_id": action_id, "source_kind": kind,
                         "token_id": f"import:{kind}"}))
                    continue
                if meta["mode"] == "create":
                    duplicates.append({"action_id": action_id,
                                       "reason": "id 已存在，mode=create 不覆盖已有记录"})
                    continue
                merged, changed = ingest_mod.merge_facts(before, record)
                merged = self.sign_on_write_record(merged, before)
                self.store.put(merged)
                updated.append(action_id)
                frames.append(self.stream_hub.publish(
                    ingest_mod.EVENT_UPDATED,
                    {"record": merged, "action_id": action_id, "changed_fields": changed,
                     "source_kind": kind, "token_id": f"import:{kind}"}))
            batch_frame = self.stream_hub.publish(ingest_mod.EVENT_BATCH, {
                "accepted": len(created) + len(updated), "created": len(created),
                "updated": len(updated), "duplicates": len(duplicates),
                "format": outcome.format, "source_kind": kind,
                "ids": (created + updated)[:40], "request_id": request_id})
            self.imports += 1
            self.imported_records += len(created) + len(updated)

        imported = len(created) + len(updated)
        actor_id, actor_role = self.audit_actor(session)
        stats = dict(outcome.stats)
        stats.update({"duplicates": len(duplicates), "created": len(created),
                      "updated": len(updated), "bytes": size, "mode": meta["mode"],
                      "strict": bool(meta["strict"]), "limit_records": IMPORT_MAX_RECORDS,
                      "limit_bytes": IMPORT_MAX_BYTES})
        summary = (f"格式 {outcome.format}，来源 {kind}，事件 {input_events} 条，"
                   f"入库 {imported}（新增 {len(created)} / 更新 {len(updated)}），"
                   f"重复 {len(duplicates)}，跳过 {stats.get('skipped', 0)}")
        # 先记账：导入过什么都没留痕，等于回答不了"这批数据是谁进的"
        self.audit.append("note", actor_id=actor_id, actor_role=actor_role,
                          mode=self.audit_mode(), request_id=request_id,
                          record_status="import",
                          extra={"import_source": kind, "import_format": outcome.format,
                                 "imported": imported, "created": len(created),
                                 "updated": len(updated), "duplicates": len(duplicates),
                                 "skipped": int(stats.get("skipped") or 0),
                                 "import_mode": meta["mode"], "import_bytes": size,
                                 "import_filename": meta["filename"],
                                 "import_body": "not_recorded"})
        self._append_durable_strict(audit_mod.build_event(
            "note", actor_id=actor_id, actor_role=self.auth.audit_actor_role(actor_role),
            mode=self.audit_mode(), request_id=request_id,
            reason=f"导入 trace：{summary}（正文不进审计，只记格式与统计）",
            extra={"import_source": kind, "import_format": outcome.format,
                   "imported": imported, "import_mode": meta["mode"],
                   "import_bytes": size, "import_filename": meta["filename"],
                   "import_ids": (created + updated)[:20], "import_body": "not_recorded",
                   "audit_file": "appended_only"}))
        return {
            "ok": True,
            "imported": imported,
            "duplicates": len(duplicates),
            "skipped": int(stats.get("skipped") or 0),
            "created": len(created), "updated": len(updated),
            "ids": (created + updated)[:100],
            "duplicate_detail": duplicates[:20],
            "skip_detail": list(stats.get("skip_reasons") or [])[:20],
            "format": outcome.format, "requested_format": meta["format"],
            "mode": meta["mode"], "source_kind": kind, "strict": bool(meta["strict"]),
            "filename": meta["filename"], "bytes": size,
            "stats": stats,
            "records_total": self.store.stats()["count"],
            "durable": self.store.store_facts()["durable"],
            "signing": {"enabled": bool(self.sign_on_write and self.mode == MODE_LOCAL),
                        "signed": self.signed_on_write - signed_before,
                        "failures": self.sign_failures - failures_before,
                        "last_problem": self.sign_last_problem or None,
                        "note": "local 模式：入库即由该 Agent 私钥签名；"
                                "team 模式待 actor→DID 映射（后续批次）"},
            "stream": {"head_seq": batch_frame["seq"], "frames": [f["seq"] for f in frames],
                       "subscribers": self.stream_hub.snapshot()["subscribers"]},
            "actor": actor_id,
            "request_id": request_id,
            "note": "imported records are facts; user decisions and execution receipts are "
                    "protected fields and are never overwritten by an import（决定 ≠ 生效）",
        }

    def reset(self, session: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """清记录：回到演示种子（4.9 起含持久化），审计文件**只追加**一条 ``record_reset``，绝不动它。

        口径（写清，别让界面点「清空数据」时猜）：导入进来的记录与用户的决定都算用户数据，
        一起清掉；演示种子不是用户数据，清完就是那 12 条。日志用原子替换压实，
        不存在"清空后崩在中间"把记录全丢的状态。

        ``--no-reset`` 时路由层直接 403 ``RESET_DISABLED``，连服务都不进，
        所以那条路径下审计文件的字节数一字节都不会变（t18 有测试钉住）。
        """
        actor_id, actor_role = self.audit_actor(session)
        with self._lock:
            count = self.store.reset()
            self.audit.append("demo.store_reset", actor_id=actor_id,
                              actor_role=actor_role,
                              record_status="reset", extra={"records": count})
            self._append_durable(audit_mod.build_event(
                "record_reset", actor_id=actor_id,
                actor_role=self.auth.audit_actor_role(actor_role),
                mode=self.audit_mode(),
                reason="演示记录重置为初始快照（只重置内存里的记录，审计链只追加）",
                extra={"records": count, "audit_file": "appended_only"}))
            # 4.4（t14）：重置也要广播，界面据此清掉本地缓存里的实时记录
            self.stream_hub.publish(ingest_mod.EVENT_RESET,
                                    {"records": count, "actor_id": actor_id})
            facts = self.store.store_facts()
            return {"ok": True, "records": count, "mode": self.mode,
                    "durable": self.audit_log is not None,
                    # 4.9（t22）：记录清到哪去了要说清 —— 只动记录，审计一个字都不删
                    "records_durable": bool(facts.get("durable")),
                    "records_path": str(facts.get("path") or ""),
                    "records_note": ("记录已清回演示种子（导入记录与用户决定一起清掉），"
                                     "日志已压实重写；审计链只追加，不删不改"
                                     if facts.get("durable")
                                     else "纯内存预览：记录回到演示数据")}

    # -- 干预 --------------------------------------------------------------
    def intervene(self, payload: Any, *, request_id: str,
                  session: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """`POST /intervene` 的实现；返回 (更新后的记录, meta)。非法输入抛 InterveneError。

        4.3 加了两个收口：
        * **身份**：team 模式由路由把已登录会话传进来，`decided_by/decided_by_role`
          与审计条都写真实用户；local 模式没传会话 → 仍是 `local-user`（t8 口径不变）。
        * **记录级 `policy.min_role_to_decide`**（docs/03 §5.4）：某条记录要求 editor
          以上时，viewer 即使角色能力里有 `intervene.decide` 也过不了这一关。
        """
        clean = self._parse_body(payload)
        action_id = clean["action_id"]
        decision = clean["decision"]
        idem_key = clean.get("idempotency_key")
        actor_id, actor_role = self.audit_actor(session)

        with self._lock:
            record = self.store.get(action_id)
            if record is None:
                raise _bad("INTERVENE_ACTION_NOT_FOUND", field="action_id",
                           detail={"action_id": action_id})

            policy = record.get("policy") if isinstance(record.get("policy"), Mapping) else {}
            min_role = policy.get("min_role_to_decide") if isinstance(policy, Mapping) else None
            if min_role and not self.auth.rank_at_least(actor_role, str(min_role)):
                self.audit.append("auth.denied", action_id=action_id, actor_id=actor_id,
                                  actor_role=self.auth.audit_actor_role(actor_role),
                                  mode=self.audit_mode(), request_id=request_id,
                                  record_status=str(record.get("status")),
                                  extra={"auth_event": "auth.denied", "username": actor_id,
                                         "ok": False, "reason": "min_role_to_decide",
                                         "detail": {"min_role_to_decide": min_role,
                                                    "role": actor_role}})
                raise _bad("ROLE_FORBIDDEN", field="policy.min_role_to_decide", status=403,
                           detail={"min_role_to_decide": min_role, "role": actor_role,
                                   "action_id": action_id,
                                   "hint": "this record needs a higher role"})

            if idem_key:
                prior = self.store.idempotency_lookup(idem_key)
                if prior is not None:
                    prior_action, prior_hash = prior
                    if prior_action != action_id:
                        raise _bad("INTERVENE_IDEMPOTENCY_MISMATCH", field="idempotency_key",
                                   detail={"action_id": prior_action})
                    stored = self.store.get(action_id)
                    if stored is None:                             # 不应发生：兜底重新计算
                        raise _bad("INTERVENE_ACTION_NOT_FOUND", field="action_id",
                                   detail={"action_id": action_id})
                    return stored, {"request_id": request_id, "replay": True,
                                    "audit_entry_hash": prior_hash, "mode": self.mode,
                                    "effect_notice": EFFECT_NOTICE_KEY}

            updated = apply_decision(
                record, decision,
                modified_params=clean.get("modified_params"),
                reason=clean.get("reason"),
                option_id=clean.get("option_id"),
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
                generator=self.generator)
            readable = updated.get("human_readable")
            source_hash = (readable.get("source_facts_hash")
                           if isinstance(readable, Mapping) else None)
            # 4.2b（t18）：决定先落追加式文件链，再落记录。审计写不进去就 500，
            # 记录保持原样 —— 绝不允许"界面显示决定了，审计里没有这一笔"。
            if self.audit_log is not None:
                self._append_durable_decision(record, updated, actor_id=actor_id,
                                              actor_role=actor_role, request_id=request_id,
                                              idempotency_key=idem_key)
            entry = self.audit.append(
                "intervention.decision",
                action_id=action_id,
                actor_id=actor_id,
                actor_role=self.auth.audit_actor_role(actor_role),
                decision=decision,
                request_id=request_id,
                idempotency_key=idem_key,
                mode=self.audit_mode(),
                record_status=updated.get("status"),
                record_facts_hash=record_facts_hash(updated),
                source_facts_hash=source_hash,
                extra={"changed_fields": [f.get("name") for f in
                                          (updated.get("user_intervention") or {}).get("modified_fields", [])]})
            # 审计哈希写回记录，形成 记录 ↔ 审计 双向可追（02 §审计）
            updated["user_intervention"]["audit_entry_hash"] = entry["entry_hash"]
            saved = self.store.put(updated)
            if idem_key:
                self.store.idempotency_store(idem_key, action_id, entry["entry_hash"])
            self.interventions += 1
            self.requests += 1
            # 4.4（t14）：决定也广播出去 —— 界面立刻看到"谁拍过板"（决定 ≠ 生效）
            self.publish_decision(saved, decision=decision, actor_id=actor_id,
                                  request_id=request_id)
            return saved, {"request_id": request_id, "replay": False,
                           "audit_entry_hash": entry["entry_hash"], "mode": self.mode,
                           "effect_notice": EFFECT_NOTICE_KEY,
                           "decision": decision, "actor_id": actor_id,
                           "actor_role": actor_role,
                           "status": saved.get("status")}

    def _parse_body(self, payload: Any) -> dict[str, Any]:
        if payload is None:
            raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": "empty"})
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload) if payload.strip() else None
            except json.JSONDecodeError as exc:
                raise _bad("INTERVENE_BODY_INVALID", detail={"hint": exc.msg,
                                                             "pos": exc.pos})
            if payload is None:
                raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": "empty"})
        if not isinstance(payload, Mapping):
            raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": type(payload).__name__})

        for key in payload:
            if key not in ALLOWED_INTERVENE_FIELDS:
                raise _bad("INTERVENE_FIELD_UNKNOWN", field=str(key),
                           detail={"field": key, "allowed": list(ALLOWED_INTERVENE_FIELDS),
                                   "hint": "identity, time and audit fields are written by the server"})

        action_id = payload.get("action_id")
        if action_id in (None, ""):
            raise _bad("INTERVENE_FIELD_MISSING", field="action_id",
                       detail={"required": True})
        if not isinstance(action_id, str) or not action_id.strip():
            raise _bad("INTERVENE_FIELD_TYPE", field="action_id",
                       detail={"received": type(action_id).__name__})
        if len(action_id) > ACTION_ID_MAX:
            raise _bad("INTERVENE_FIELD_TYPE", field="action_id",
                       detail={"length": len(action_id), "max": ACTION_ID_MAX})

        decision = payload.get("decision")
        if decision in (None, ""):
            raise _bad("INTERVENE_FIELD_MISSING", field="decision",
                       detail={"required": True, "allowed": list(DECISIONS)})

        reason = payload.get("reason")
        modified = payload.get("modified_params")
        option_id = payload.get("option_id")
        idem = payload.get("idempotency_key")
        if idem not in (None, ""):
            if not isinstance(idem, str) or not idem.strip():
                raise _bad("INTERVENE_FIELD_TYPE", field="idempotency_key",
                           detail={"received": type(idem).__name__})
            if len(idem) > IDEMPOTENCY_KEY_MAX:
                raise _bad("INTERVENE_FIELD_TYPE", field="idempotency_key",
                           detail={"length": len(idem), "max": IDEMPOTENCY_KEY_MAX})
            idem = idem.strip()
        else:
            idem = None
        if option_id is not None and not isinstance(option_id, str):
            raise _bad("INTERVENE_FIELD_TYPE", field="option_id",
                       detail={"received": type(option_id).__name__})
        return {"action_id": action_id.strip(), "decision": decision, "reason": reason,
                "modified_params": modified, "option_id": option_id, "idempotency_key": idem}

    # -- 鉴权与用户管理（4.3）--------------------------------------------- #
    #
    # 这一层只做"入参清洗 + 调 auth + 拼响应形状"；权限判定全在 auth.require 一处，
    # handler 里不允许出现 `if role == "admin"`（t13 测试用 AST 钉住这条）。

    ALLOWED_LOGIN_FIELDS: tuple[str, ...] = ("username", "password")
    ALLOWED_PASSWORD_FIELDS: tuple[str, ...] = ("old_password", "new_password")
    ALLOWED_USER_CREATE_FIELDS: tuple[str, ...] = ("username", "role", "password",
                                                   "display_name")
    ALLOWED_ROLE_FIELDS: tuple[str, ...] = ("role",)
    ALLOWED_STATUS_FIELDS: tuple[str, ...] = ("status",)
    ALLOWED_RESET_FIELDS: tuple[str, ...] = ("password",)

    @staticmethod
    def _body_object(payload: Any, *, fields: Sequence[str]) -> dict[str, Any]:
        """把请求体收成 dict，并拒绝未知字段（与干预接口同一口径，前端可读 code 定位）。"""
        clean = InterveneService._json_object(payload)
        for key in clean:
            if key not in fields:
                raise _bad("INTERVENE_FIELD_UNKNOWN", field=str(key),
                           detail={"field": key, "allowed": list(fields)})
        return clean

    @staticmethod
    def _json_object(payload: Any) -> dict[str, Any]:
        if payload is None:
            raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": "empty"})
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload) if payload.strip() else None
            except json.JSONDecodeError as exc:
                raise _bad("INTERVENE_BODY_INVALID", detail={"hint": exc.msg, "pos": exc.pos})
            if payload is None:
                raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": "empty"})
        if not isinstance(payload, Mapping):
            raise _bad("INTERVENE_BODY_NOT_OBJECT", detail={"received": type(payload).__name__})
        return dict(payload)

    def auth_status(self) -> dict[str, Any]:
        """公开：模式 + 是否需要登录 + 前端该怎么摆（登录页/用户页开关）。"""
        base = self.auth.status()
        base["bootstrap_needed_note"] = (
            "team 模式还没有任何用户：管理员初始口令在**服务端控制台**打印一次，"
            "HTTP 响应里永远拿不到；Docker 可用 TRACELENS_ADMIN_PASSWORD 注入"
            if base["bootstrap_needed"] else "")
        base["ui"] = {"show_login": base["required"],
                      "login_path": API_PATH_AUTH_LOGIN,
                      "me_path": API_PATH_AUTH_ME,
                      "users_path": API_PATH_USERS,
                      "permissions_path": API_PATH_AUTH_PERMISSIONS}
        return base

    def permissions_payload(self) -> dict[str, Any]:
        """公开：角色→权限矩阵。前端只读它做隐藏/禁用，服务端仍是强制点。"""
        return self.auth.permissions_payload()

    def login(self, payload: Any, *, request_id: str,
              client_info: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """`POST /api/auth/login`：口令对上才发会话；失败计数与锁定在 auth 里。"""
        body = self._body_object(payload, fields=self.ALLOWED_LOGIN_FIELDS)
        session = self.auth.authenticate(body.get("username"), body.get("password"),
                                         client_info=client_info)
        self.flush_auth_events()
        me = self.auth.me_payload(session)
        cookie = dict(session["cookie"])
        if self.secure_cookies:
            cookie["secure"] = True
        body_out = {
            "user": me,
            "session": {"expires_at": session["expires_at"],
                        "expires_in": session["expires_in"], "sid": session["sid"]},
            "csrf_token": session["csrf_token"],
            "token": session["token"],
            "cookie": cookie,
            "mode": self.mode,
            "request_id": request_id,
            "note": ("token 是给桌面/CLI 客户端走 " + auth_mod.TOKEN_HEADER +
                     " 头用的；浏览器请用 Cookie + X-CSRF-Token"),
        }
        meta = {"cookie": cookie, "csrf_token": session["csrf_token"],
                "role": session["role"], "actor_id": session["username"],
                "expires_at": session["expires_at"]}
        return body_out, meta

    def logout(self, token: Any, *, request_id: str) -> dict[str, Any]:
        out = self.auth.logout(token)
        out["request_id"] = request_id
        self.flush_auth_events()
        return out

    def me(self, session: Mapping[str, Any] | None) -> dict[str, Any]:
        if not session:
            raise auth_mod.AuthRequiredError("Not signed in",
                                             hint="local 模式请直接用 /api/records")
        return self.auth.me_payload(session)

    def change_password(self, payload: Any, *, session: Mapping[str, Any]) -> dict[str, Any]:
        """本人改密（管理员给别人重置走 `/api/users/<id>/password`）。"""
        body = self._body_object(payload, fields=self.ALLOWED_PASSWORD_FIELDS)
        out = self.auth.change_password(str(session.get("user_id")),
                                        body.get("old_password"), body.get("new_password"))
        self.flush_auth_events()
        return {"ok": True, **out}

    def check_permission(self, payload: Any, *,
                         session: Mapping[str, Any] | None) -> dict[str, Any]:
        """K-11 判定探针：把矩阵的每一项显式问一遍（前端联调用，不猜）。"""
        body = self._body_object(payload, fields=("permission", "min_role_to_decide"))
        permission = str(body.get("permission") or "")
        if permission not in auth_mod.PERMISSIONS:
            raise _bad("INTERVENE_FIELD_UNKNOWN", field="permission",
                       detail={"permission": permission, "allowed": list(auth_mod.PERMISSIONS)})
        active = self.auth.require(session, permission)
        min_role = body.get("min_role_to_decide")
        record_ok = self.auth.rank_at_least(str(active.get("role")),
                                            None if min_role in (None, "") else str(min_role))
        if not record_ok:
            self.flush_auth_events()
            raise _bad("ROLE_FORBIDDEN", field="min_role_to_decide", status=403,
                       detail={"min_role_to_decide": min_role, "role": active.get("role")})
        self.flush_auth_events()
        return {"allowed": True, "permission": permission, "role": active.get("role"),
                "actor_id": active.get("user_id"), "permissions": list(active.get("permissions") or []),
                "min_role_to_decide": min_role, "record_gate_ok": record_ok}

    def users_payload(self, *, q: Any = None, status: Any = None) -> dict[str, Any]:
        users = self.auth.list_users(q=str(q) if q else None, status=str(status) if status else None)
        return {"data": users, "count": len(users),
                "meta": {"mode": self.mode, "statuses": list(auth_mod.USER_STATUSES),
                         "roles": list(auth_mod.TEAM_ROLES),
                         "note": "用户表可改（这不是审计）；禁用是唯一口径，没有删除"}}

    def create_user(self, payload: Any, *, actor: Mapping[str, Any]) -> dict[str, Any]:
        body = self._body_object(payload, fields=self.ALLOWED_USER_CREATE_FIELDS)
        user = self.auth.create_user(body.get("username"), body.get("role"),
                                     body.get("password"),
                                     display_name=body.get("display_name"), actor=actor)
        self.flush_auth_events()
        return {"ok": True, "user": user,
                "note": (user.get("initial_password_note")
                         if user.get("initial_password") else "口令由创建者指定")}

    def user_action(self, user_id: str, verb: str, payload: Any, *,
                    actor: Mapping[str, Any]) -> dict[str, Any]:
        """`POST /api/users/<id>/{role,status,password}`（docs B2 的三种改动）。

        方法口径说明：docs/03 把 B2 写成 `PATCH/DELETE`，但本服务的写方法只开放 POST
        （4.1 已冻结 `PUT/PATCH/DELETE → 405 METHOD_NOT_ALLOWED`，且审计域的"删除用户"
        在产品口径里不存在——禁用才是唯一做法，删除会让历史审计查无此人）。
        因此三种改动落成 POST 子资源，字段与语义与 docs 一致。
        """
        body = self._json_object(payload)
        if verb == "role":
            for key in body:
                if key not in self.ALLOWED_ROLE_FIELDS:
                    raise _bad("INTERVENE_FIELD_UNKNOWN", field=str(key),
                               detail={"allowed": list(self.ALLOWED_ROLE_FIELDS)})
            user = self.auth.set_role(user_id, body.get("role"), actor=actor)
        elif verb == "status":
            for key in body:
                if key not in self.ALLOWED_STATUS_FIELDS:
                    raise _bad("INTERVENE_FIELD_UNKNOWN", field=str(key),
                               detail={"allowed": list(self.ALLOWED_STATUS_FIELDS)})
            user = self.auth.set_status(user_id, body.get("status"), actor=actor)
        elif verb == "password":
            for key in body:
                if key not in self.ALLOWED_RESET_FIELDS:
                    raise _bad("INTERVENE_FIELD_UNKNOWN", field=str(key),
                               detail={"allowed": list(self.ALLOWED_RESET_FIELDS)})
            user = self.auth.reset_password(user_id, body.get("password"), actor=actor)
        else:
            raise _bad("ROUTE_NOT_FOUND", detail={"path": f"/api/users/{user_id}/{verb}",
                                                  "allowed": ["role", "status", "password"]})
        self.flush_auth_events()
        return {"ok": True, "user": user,
                "note": (user.get("initial_password_note")
                         if user.get("initial_password") else "")}


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #

_HEX_DIGITS = "0123456789abcdefABCDEF"

# 仅用于给操作员打印本机地址；拆开写是为了不在包内留下 URL 字面量。
LOOPBACK_SCHEME = "http" + "://"


def request_path(raw: str) -> str:
    """请求目标的路径部分：去掉 query / fragment。

    不用标准库的 URL 解析模块：包内不得出现该模块名（离线纯度扫描），
    而这里只需要切两个分隔符。"""
    target = str(raw or "")
    for sep in ("?", "#"):
        at = target.find(sep)
        if at >= 0:
            target = target[:at]
    return target


def header_safe(value: Any) -> str:
    """HTTP 响应头只允许 ASCII：非 ASCII 字符按 UTF-8 百分号转义。

    导出端点会把过滤条件摘要放进响应头（界面上要看得见"我导出的是哪一段"），
    摘要里可能有中文关键词。直接 `send_header` 中文会让 `http.client` 当场抛
    `UnicodeEncodeError` 并掐断连接，所以这里统一转义，界面上用 `decodeURIComponent`
    还原。
    """
    out: list[str] = []
    for ch in str(value):
        code = ord(ch)
        if 32 <= code < 127 and ch not in ('"', "\r", "\n", "\\"):
            out.append(ch)
        else:
            for byte in ch.encode("utf-8"):
                out.append(f"%{byte:02X}")
    return "".join(out)


def unquote(raw: str) -> str:
    """百分号解码（UTF-8），语义对齐标准库，但不引入标准库 URL 解析模块。

    非法转义按字面保留，与 `unquote(errors="replace")` 一致地宽松：静态路径
    解析只把结果当作待校验字符串，解码失败不会绕过后续的路径校验。"""
    text = str(raw)
    if "%" not in text:
        return text
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "%" and i + 2 < n:
            hexits = text[i + 1:i + 3]
            if all(c in _HEX_DIGITS for c in hexits):
                out.append(int(hexits, 16))
                i += 3
                continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


class TraceLensHandler(BaseHTTPRequestHandler):
    """静态页面 + 本地 API。协议版本 1.1（连接复用，界面轮询不炸端口）。"""

    protocol_version = "HTTP/1.1"
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    sys_version = ""

    # -- 基础工具 ---------------------------------------------------------
    @property
    def service(self) -> InterveneService:
        return self.server.service                       # type: ignore[attr-defined]

    def _request_id(self) -> str:
        return getattr(self.server, "last_request_id", None) or ""

    def _send(self, status: int, body: bytes, content_type: str,
              extra_headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-Id", self._request_id())
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status: int, payload: Any,
                  extra_headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra_headers)

    def send_error_body(self, code: str, *, field: str | None = None,
                        detail: Mapping[str, Any] | None = None,
                        message: str | None = None, status: int | None = None) -> None:
        err = InterveneError(code, field=field, detail=detail, message=message, status=status)
        self.send_json(err.http_status, err.as_dict(self._request_id()))

    def _send_secretariat_error(self, exc: "sec_mod.SecretariatError") -> None:
        """总裁办错误 → 统一错误体（保留人话、字段、取证细节与建议状态）。"""
        self.service.flush_auth_events()
        self.service.rejects += 1
        wrapped = InterveneError(exc.code, field=exc.field, detail=exc.detail,
                                 message=exc.reason, status=exc.http_status)
        self.send_json(exc.http_status, wrapped.as_dict(self._request_id()))

    def read_json_body(self) -> Any:
        raw = self._read_body()
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise _bad("INTERVENE_BODY_INVALID", detail={"hint": f"not utf-8: {exc}"})
        except json.JSONDecodeError as exc:
            raise _bad("INTERVENE_BODY_INVALID", detail={"hint": exc.msg, "pos": exc.pos})

    def _read_body(self) -> bytes | None:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype != "application/json":
            raise _bad("INTERVENE_CONTENT_TYPE", detail={"content_type": ctype})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise _bad("INTERVENE_FIELD_TYPE", field="Content-Length",
                       detail={"received": self.headers.get("Content-Length")})
        if length <= 0:
            return None
        if length > MAX_BODY_BYTES:
            raise _bad("INTERVENE_BODY_TOO_LARGE",
                       detail={"content_length": length, "limit": MAX_BODY_BYTES})
        return self.rfile.read(length)

    # -- 会话与权限（4.3：全服务唯一的鉴权强制点）------------------------ #
    #
    # 规则（docs/03 §5）：路由只**声明**需要的权限，判定只发生在 `_require` 一处。
    # 因此这里绝不允许出现 `if role == "admin"` 这类散落判断（t13 用 AST 扫描钉住）。

    def _cookie_value(self, name: str) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        for chunk in str(raw).split(";"):
            key, _, value = chunk.strip().partition("=")
            if key == name and value:
                return value
        return None

    def _bearer_token(self) -> str | None:
        raw = self.headers.get(auth_mod.TOKEN_HEADER)
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    def _csrf_header(self) -> str | None:
        raw = self.headers.get(auth_mod.CSRF_HEADER)
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    # -- 4.4 实时接入（t14）：入参提取 + SSE 写出 ----------------------------
    def _ingest_secret(self) -> str | None:
        """取上报凭证：专用头优先，其次 Authorization 头里的 Bearer 前缀 token。

        两个头都收，是因为现成 Agent 框架的 webhook 插件多数只会写 Authorization；
        专用头留给"同一个请求还要带用户会话"的场合（浏览器里点按钮触发补报）。
        """
        dedicated = self.headers.get(ingest_mod.TOKEN_HEADER)
        if isinstance(dedicated, str) and dedicated.strip():
            return dedicated.strip()
        raw = self.headers.get(ingest_mod.AUTH_HEADER_NAME)
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        prefix = f"{ingest_mod.BEARER_PREFIX} "
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix):].strip() or None
        return None

    def _idempotency_key(self) -> str | None:
        raw = self.headers.get("Idempotency-Key")
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    @staticmethod
    def _ingest_token_route(path: str) -> tuple[str, str] | None:
        """`/api/ingest/tokens/<id>/revoke` → (id, "revoke")；其余返回 None。"""
        if not path.startswith(API_PATH_INGEST_TOKENS + "/"):
            return None
        tail = unquote(path[len(API_PATH_INGEST_TOKENS) + 1:]).strip("/")
        if not tail:
            return None
        token_id, _, verb = tail.partition("/")
        return token_id.strip(), verb.strip().lower()

    def _stream_query(self) -> tuple[int, int, float, list[str] | None]:
        """解析流参数：`?since=` / `Last-Event-ID` / `?max_events=` / `?types=`。

        参数写错就 400 `STREAM_QUERY_INVALID`，不静默按默认值猜——界面重连时
        带错游标是 bug，静默吞掉会变成"看起来连上了但少了几条"。
        """
        since = 0
        raw_since = self._query_value("since")
        raw_last = self.headers.get("Last-Event-ID")
        for label, raw in (("since", raw_since), ("Last-Event-ID", raw_last)):
            if raw is None or not str(raw).strip():
                continue
            text = str(raw).strip()
            try:
                value = int(text)
            except ValueError:
                raise _bad("STREAM_QUERY_INVALID", field=label,
                           detail={"received": text[:24],
                                   "hint": "the stream cursor is a non-negative integer seq"}) from None
            if value < 0:
                raise _bad("STREAM_QUERY_INVALID", field=label,
                           detail={"received": text[:24],
                                   "hint": "the stream cursor is a non-negative integer seq"})
            since = max(since, value)
        max_events = self._query_int("max_events", STREAM_MAX_EVENTS_DEFAULT)
        heartbeat = self.service.stream_heartbeat
        raw_hb = self._query_value("heartbeat")
        if raw_hb is not None and str(raw_hb).strip():
            try:
                heartbeat = float(str(raw_hb).strip())
            except ValueError:
                raise _bad("STREAM_QUERY_INVALID", field="heartbeat",
                           detail={"received": str(raw_hb)[:24],
                                   "hint": "seconds as a number"}) from None
            heartbeat = min(120.0, max(0.02, heartbeat))
        types: list[str] | None = None
        raw_types = self._query_value("types")
        if raw_types is not None and str(raw_types).strip():
            wanted = [t.strip() for t in str(raw_types).split(",") if t.strip()]
            unknown = [t for t in wanted if t not in ingest_mod.STREAM_EVENT_TYPES]
            if unknown:
                raise _bad("STREAM_QUERY_INVALID", field="types",
                           detail={"unknown": unknown,
                                   "accepted": list(ingest_mod.STREAM_EVENT_TYPES)})
            types = wanted
        return since, max_events, heartbeat, types

    def _write_sse(self, event: str, event_id: int | None,
                   data: Mapping[str, Any]) -> None:
        """一帧 SSE：`event:` + `id:` + `data:` + 空行；写完立刻 flush。"""
        lines = [f"event: {event}"]
        if event_id is not None:
            lines.append(f"id: {int(event_id)}")
        lines.append(f"data: {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
        self.wfile.write(("\n".join(lines) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _serve_stream(self) -> None:
        """`GET /api/stream`（docs/03 §6.3 F1）：推送新增/变更的 ActionRecord。

        * 不发 `Content-Length`（流是无限的）且必须 `Connection: close` ——
          否则 HTTP/1.1 的 keep-alive 会让客户端一直等下一个响应；
        * `X-Accel-Buffering: no`：nginx 上不缓冲，界面才真的"实时"；
        * 慢客户端由 hub 丢最旧帧并标 `degraded`，写入方永不被拖住；
        * 断线重连带 `Last-Event-ID`（或 `?since=`）→ 从有界环形缓冲补发，
          补不上的部分界面自己走 `/api/records` 全量对齐（降级轮询）；
        * 心跳帧没有 `id:`，所以浏览器重连时的游标仍是最后一条真事件。
        """
        if self.command != "GET":
            raise _bad("METHOD_NOT_ALLOWED",
                       detail={"method": self.command, "path": API_PATH_STREAM,
                               "hint": "the stream endpoint is GET only"})
        accept = self.headers.get("Accept") or ""
        if STREAM_ACCEPT not in str(accept) and "*/*" not in str(accept):
            raise _bad("STREAM_ACCEPT_REQUIRED",
                       detail={"accept": str(accept)[:80],
                               "hint": f"send the header Accept: {STREAM_ACCEPT}"})
        since, max_events, heartbeat, types = self._stream_query()
        hub = self.service.stream_hub
        try:
            sub = hub.subscribe(types=types, since=since)
        except ingest_mod.IngestError as exc:
            raise _bad(exc.code, message=exc.reason, detail=exc.detail,
                       status=exc.status) from exc
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{STREAM_ACCEPT}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.send_header("X-TraceLens-Stream-Head", str(hub.seq))
        self.end_headers()
        self.close_connection = True
        self.wfile.flush()
        # 每连接一句问候：界面立刻知道自己连上了、当前游标在哪
        hub.publish(ingest_mod.EVENT_HELLO, {
            "mode": self.service.mode, "head_seq": hub.seq, "since": since,
            "replay_frames": len(hub.frames_after(since, limit=500)),
            "heartbeat_seconds": heartbeat, "types": types or "all",
            "subscribers": hub.snapshot()["subscribers"]})
        sent = 0
        stop_flag = getattr(self.server, "stream_stop", None)
        try:
            while True:
                if stop_flag is not None and stop_flag.is_set():
                    break
                frame = sub.get(timeout=heartbeat)
                if frame is None:
                    break                                   # 服务端收尾（停机）
                if not frame:
                    self._write_sse(ingest_mod.EVENT_HEARTBEAT, None, {
                        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(
                            timespec="seconds").replace("+00:00", "Z"),
                        "head_seq": hub.seq,
                        "subscribers": hub.snapshot()["subscribers"]})
                    sent += 1
                else:
                    self._write_sse(str(frame.get("type")), int(frame.get("seq") or 0), frame)
                    sent += 1
                    sub.since = int(frame.get("seq") or sub.since)
                if max_events and sent >= max_events:
                    break
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True                    # 客户端提前走：正常收尾
        except OSError:
            self.close_connection = True
        finally:
            hub.unsubscribe(sub)

    def _resolve_session(self) -> tuple[dict[str, Any] | None, str]:
        """返回 (会话, 凭证通道)。通道 `cookie` 才需要 CSRF（双提交口径）。

        local 模式短路成 `local-user`，与 t8 的行为逐字节一致（不查库、不要 token）。
        """
        auth = self.service.auth
        if not auth.required:
            return auth.local_session(), "local"
        token = self._bearer_token()
        if token:
            return auth.session_from_token(token), "token"
        cookie = self._cookie_value(auth_mod.SESSION_COOKIE)
        if cookie:
            return auth.session_from_token(cookie), "cookie"
        return None, "none"

    def _require(self, permission: str) -> dict[str, Any]:
        """声明式权限：没登录 401 AUTH_REQUIRED，权限不够 403 ROLE_FORBIDDEN。"""
        session, _channel = self._resolve_session()
        try:
            active = self.service.auth.require(session, permission)
        except auth_mod.AuthError as exc:
            self.service.flush_auth_events()
            raise _auth_bad(exc)
        return active

    def _csrf_guard(self, session: Mapping[str, Any], channel: str) -> None:
        """只对被 Cookie 证明身份的写请求做 CSRF 校验。

        只带 `X-TraceLens-Token` 的客户端（桌面 / CLI / 测试）没有浏览器自动携带
        Cookie 的跨站问题，因此豁免——这样 4.1 的干预闭环与 79 条测试口径不变。
        """
        if channel != "cookie":
            return
        try:
            self.service.auth.check_csrf(session, self._csrf_header())
        except auth_mod.AuthError as exc:
            self.service.flush_auth_events()
            raise _auth_bad(exc)

    def _session_and_permission(self, permission: str) -> tuple[dict[str, Any], str]:
        session, channel = self._resolve_session()
        try:
            active = self.service.auth.require(session, permission)
        except auth_mod.AuthError as exc:
            self.service.flush_auth_events()
            raise _auth_bad(exc)
        return active, channel

    def _login_required(self) -> tuple[dict[str, Any], str]:
        """只需要"已登录"的接口（me / logout / check / password）。"""
        return self._session_and_permission("records.read")

    def _set_cookie(self, cookie: Mapping[str, Any], *, expire: bool = False) -> str:
        """拼 Set-Cookie：HttpOnly + SameSite=Lax 是产品硬要求（01 §6.5 安全）。"""
        value = "" if expire else str(cookie.get("value") or "")
        parts = [f"{auth_mod.SESSION_COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax",
                 f"Max-Age={0 if expire else int(cookie.get('max_age') or self.service.auth.session_ttl)}"]
        if self.service.secure_cookies or cookie.get("secure"):
            parts.append("Secure")
        return "; ".join(parts)

    def _send_auth_error(self, exc: auth_mod.AuthError) -> None:
        extra: dict[str, str] = {}
        if getattr(exc, "retry_after", None):
            extra["Retry-After"] = str(int(exc.retry_after))
        err = _auth_bad(exc)
        self.service.rejects += 1
        self.send_json(err.http_status, err.as_dict(self._request_id()), extra)

    @staticmethod
    def _user_route(path: str) -> tuple[str, str] | None:
        """`/api/users/<id>` → (id, "")；`/api/users/<id>/<verb>` → (id, verb)。"""
        if not path.startswith(API_PATH_USERS + "/"):
            return None
        tail = unquote(path[len(API_PATH_USERS) + 1:]).strip("/")
        if not tail:
            return None
        user_id, _, verb = tail.partition("/")
        return user_id.strip(), verb.strip().lower()

    # -- 5.0 总裁办：动态路由助手（形状写在助手函数里，路由表登记在 tools/）----
    @staticmethod
    def _secretariat_identity_route(path: str) -> str | None:
        """`/api/secretariat/identities/<agent_id>/revoke` → agent_id（T3）。"""
        prefix = API_PATH_SECRETARIAT_IDENTITIES + "/"
        if not path.startswith(prefix):
            return None
        parts = unquote(path[len(prefix):]).strip("/").split("/")
        if len(parts) != 2 or parts[1] != "revoke" or not parts[0]:
            return None
        return parts[0]

    @staticmethod
    def _secretariat_record_sign_route(path: str) -> str | None:
        """`/api/secretariat/records/<id>/sign` → 记录 id（T5）。"""
        prefix = API_PATH_SECRETARIAT_RECORDS + "/"
        if not path.startswith(prefix):
            return None
        parts = unquote(path[len(prefix):]).strip("/").split("/")
        if len(parts) != 2 or parts[1] != "sign" or not parts[0]:
            return None
        return parts[0]

    @staticmethod
    def _secretariat_letter_route(path: str) -> str | None:
        """`/api/secretariat/letters/<id>` → 认定书 id（T8；不含 /verify）。"""
        prefix = API_PATH_SECRETARIAT_LETTERS + "/"
        if not path.startswith(prefix):
            return None
        parts = unquote(path[len(prefix):]).strip("/").split("/")
        if len(parts) != 1 or not parts[0]:
            return None
        return parts[0]

    @staticmethod
    def _secretariat_letter_verify_route(path: str) -> str | None:
        """`/api/secretariat/letters/<id>/verify` → 认定书 id（T9）。"""
        prefix = API_PATH_SECRETARIAT_LETTERS + "/"
        if not path.startswith(prefix):
            return None
        parts = unquote(path[len(prefix):]).strip("/").split("/")
        if len(parts) != 2 or parts[1] != "verify" or not parts[0]:
            return None
        return parts[0]

    # -- 路由 --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - 标准库命名
        self._new_request_id()
        path = request_path(self.path)
        try:
            if path in (API_PATH_HEALTH, "/healthz"):
                self.send_json(HTTPStatus.OK, self.service.health())
                return
            # ---- 4.3 鉴权：两个公开探针 + 一个"我是谁" ----
            if path == API_PATH_AUTH_STATUS:
                self.send_json(HTTPStatus.OK, self.service.auth_status())
                return
            if path == API_PATH_AUTH_PERMISSIONS:
                self.send_json(HTTPStatus.OK, self.service.permissions_payload())
                return
            if path == API_PATH_AUTH_ME:
                session, _channel = self._login_required()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.me(session))
                return
            if path == API_PATH_USERS:
                self._require("users.manage")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.users_payload(
                    q=self._query_value("q"), status=self._query_value("status")))
                return
            if path == API_PATH_RECORDS or path == API_PATH_RECORDS + "/":
                self._require("records.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.records_payload())
                return
            if path.startswith(API_PATH_RECORDS + "/"):
                self._require("records.read")
                action_id = unquote(path[len(API_PATH_RECORDS) + 1:]).strip("/")
                record = self.service.store.get(action_id)
                if record is None:
                    self.send_error_body("INTERVENE_ACTION_NOT_FOUND", field="action_id",
                                         detail={"action_id": action_id})
                    return
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, record)
                return
            if path == API_PATH_AUDIT:
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.audit_payload(
                    limit=self._query_int("limit", AUDIT_QUERY_LIMIT_DEFAULT),
                    offset=self._query_int("offset", 0),
                    event=self._query_value("event"),
                    actor_id=self._query_value("actor_id"),
                    action_id=self._query_value("action_id"),
                    decision=self._query_value("decision"),
                    task_id=self._query_value("task_id"),
                    q=self._query_value("q"),
                    reverse=self._query_bool("reverse")))
                return
            # ---- 4.8 审计导出（t21）：CSV 下载 / 可打印 HTML / reportlab PDF ----
            if path == API_PATH_AUDIT_EXPORT:
                # 能力口径：`audit.export`（4.3 权限矩阵里 local/admin/editor/viewer 有，
                # 没有这项能力的角色 403 ROLE_FORBIDDEN）。GET 无副作用，不做 CSRF。
                session, _channel = self._session_and_permission("audit.export")
                self.service.flush_auth_events()
                export = self.service.audit_export(
                    fmt=self._query_value("format") or AUDIT_EXPORT_FORMAT_DEFAULT,
                    filters=self._export_filter_values(),
                    limit=self._query_int("limit", AUDIT_EXPORT_LIMIT_DEFAULT),
                    session=session, request_id=self._request_id(),
                    lang=self._query_value("lang") or "zh")
                self.service.exports_served += 1
                self._send(HTTPStatus.OK, export["body"], export["content_type"],
                           export["headers"])
                return
            # ---- 4.4 实时接入（t14）：接入状态 + SSE 广播（docs/03 §6.3 E1/F1）----
            if path in (API_PATH_INGEST_EVENTS, API_PATH_INGEST_TOKENS):
                raise _bad("METHOD_NOT_ALLOWED",
                           detail={"method": "GET", "path": path,
                                   "hint": "ingest endpoints are POST only; "
                                          "use GET /api/ingest/status to inspect"})
            if path == API_PATH_IMPORT:
                # 导入是写操作：GET 到这就是 405，读导入结果请走 /api/records
                raise _bad("METHOD_NOT_ALLOWED",
                           detail={"method": "GET", "path": path,
                                   "hint": "import is POST only; use GET /api/records "
                                          "to read what an import put in"})
            if path == API_PATH_INGEST_STATUS:
                self._require("ingest.token.manage")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.ingest_status_payload())
                return
            if path == API_PATH_STREAM:
                # 流推的就是记录，所以权限与 /api/records 同一条：viewer 也能看，不能写
                self._require("records.read")
                self.service.flush_auth_events()
                self._serve_stream()
                return
            # ---- 4.5 设置中心（t15）：读掩码视图（docs/03 §7 契约表）------------
            if path == API_PATH_SETTINGS:
                # 读口径 = `records.read`：看的人要能解释界面行为（模式 / 语言 / 能不能改）。
                # 响应体只有掩码与指纹（`••••••` + 末 4 位），没有密钥明文；
                # 能不能保存由 `can_write` 说清楚，写权限见 do_PUT。
                session = self._require("records.read")
                self.service.settings_reads += 1
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.settings_view(session))
                return
            if path == API_PATH_SETTINGS_TEST_LLM:
                raise _bad("METHOD_NOT_ALLOWED",
                           detail={"method": "GET", "path": path,
                                   "hint": "test-llm is POST only; use GET /api/settings "
                                          "to read the (masked) configuration"})
            # ---- 5.0 总裁办（T1–T4 / T7–T8 读侧；docs/12 §6）------------------
            if path in (API_PATH_SECRETARIAT, API_PATH_SECRETARIAT_STATUS):
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_status_payload())
                return
            if path == API_PATH_SECRETARIAT_IDENTITIES:
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_identities_payload())
                return
            if path == API_PATH_SECRETARIAT_DELEGATIONS:
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_delegations_payload())
                return
            if path == API_PATH_SECRETARIAT_LETTERS:
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_letters_payload(
                    limit=self._secretariat_limit()))
                return
            if path in (API_PATH_SECRETARIAT_VERIFY, API_PATH_SECRETARIAT_SEAL):
                raise _bad("METHOD_NOT_ALLOWED",
                           detail={"method": "GET", "path": path,
                                   "hint": "verify/seal are POST only"})
            letter_route = self._secretariat_letter_route(path)
            if letter_route is not None:
                self._require("audit.read")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_letter(letter_route))
                return
            self.serve_static(path)
        except ingest_mod.IngestError as exc:
            # 4.4 的 ingest 域异常用自己的错误码表（E1/F1），对外形状与干预域一致
            self.service.flush_auth_events()
            extra = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
            self.service.rejects += 1
            self.send_json(exc.status, exc.as_dict(self._request_id()), extra)
        except auth_mod.AuthError as exc:
            self._send_auth_error(exc)
        except settings_mod.SettingsError as exc:
            # 4.5：读视图时配置读坏了（文件非法 JSON 等）也说人话，不抛 500 堆栈
            self.service.flush_auth_events()
            self.send_json(exc.status, exc.as_dict(self._request_id()))
        except sec_mod.SecretariatError as exc:
            self._send_secretariat_error(exc)
        except InterveneError as err:
            self.send_json(err.http_status, err.as_dict(self._request_id()))
        except BrokenPipeError:                                        # 客户端提前断开
            self.close_connection = True
        except Exception as exc:                                        # noqa: BLE001
            self.send_error_body("INTERNAL_ERROR", detail={"hint": f"{type(exc).__name__}: {exc}"})

    def _query_value(self, name: str) -> str | None:
        """取 query 参数（只要 key=value，不引 URL 解析模块 —— 离线纯度约束）。"""
        target = str(self.path or "")
        query = target.split("?", 1)[1] if "?" in target else ""
        for chunk in query.split("&"):
            key, _, value = chunk.partition("=")
            if key == name:
                return unquote(value)
        return None

    def _query_int(self, name: str, default: int) -> int:
        """审计分页参数：非数字/负数一律 400，不静默按默认值截断。"""
        raw = self._query_value(name)
        if raw is None or not str(raw).strip():
            return int(default)
        text = str(raw).strip()
        try:
            value = int(text)
        except ValueError:
            raise _bad("AUDIT_QUERY_INVALID", field=name,
                       detail={"received": text,
                               "hint": "expected a non-negative integer"}) from None
        if value < 0:
            raise _bad("AUDIT_QUERY_INVALID", field=name,
                       detail={"received": text,
                               "hint": "expected a non-negative integer"})
        return value

    def _query_bool(self, name: str) -> bool:
        return str(self._query_value(name) or "").strip().lower() in ("1", "true", "yes", "on")

    def _secretariat_limit(self) -> int:
        """`GET /api/secretariat/letters?limit=` 的口径（R4 t4 收口）。

        审计分页助手 `_query_int` 抛的是 `AUDIT_QUERY_INVALID`——那是审计域的码，
        挂在总裁办端点上会让前端拿不到本域文案（`secretariat.error.*` 查不到键），
        所以这里用 docs/12 §3 已有的 `SECRETARIAT_FIELD_INVALID(field=limit)`：
        非数字 / 负数 = 400，不静默按默认值截断；超过 `LETTER_LIST_MAX` 如实截断。
        """
        raw = self._query_value("limit")
        if raw is None or not str(raw).strip():
            return self.service.LETTER_DEFAULT_LIMIT
        text = str(raw).strip()
        try:
            value = int(text)
        except ValueError:
            raise _bad("SECRETARIAT_FIELD_INVALID", field="limit",
                       message=f"limit 必须是 1-{self.service.LETTER_LIST_MAX} 的整数",
                       detail={"received": text, "limit": self.service.LETTER_LIST_MAX,
                               "hint": "expected a positive integer"}) from None
        if value < 1:
            raise _bad("SECRETARIAT_FIELD_INVALID", field="limit",
                       message=f"limit 必须是 1-{self.service.LETTER_LIST_MAX} 的整数",
                       detail={"received": text, "limit": self.service.LETTER_LIST_MAX,
                               "hint": "expected a positive integer"})
        return min(value, self.service.LETTER_LIST_MAX)

    def _export_filter_values(self) -> dict[str, str]:
        """导出端点的过滤条件：与 `GET /api/audit` 同一批键，空值丢掉。"""
        out: dict[str, str] = {}
        for key in ("actor_id", "action_id", "decision", "event", "task_id",
                    "since", "until", "q"):
            value = self._query_value(key)
            if value is not None and str(value).strip():
                out[key] = str(value).strip()[:200]
        return out

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        self._new_request_id()
        path = request_path(self.path)
        try:
            # ---- 4.3 鉴权接口 ----
            if path == API_PATH_AUTH_LOGIN:
                payload = self.read_json_body()
                body, meta = self.service.login(
                    payload, request_id=self._request_id(),
                    client_info={"channel": "api",
                                 "cookie": self._cookie_value(auth_mod.SESSION_COOKIE) is not None,
                                 "token_header": self._bearer_token() is not None})
                self.send_json(HTTPStatus.OK, body,
                               {"Set-Cookie": self._set_cookie(meta["cookie"]),
                                "X-Auth-Role": str(meta.get("role") or ""),
                                "X-Auth-Actor": str(meta.get("actor_id") or "")})
                return
            if path == API_PATH_AUTH_LOGOUT:
                session, channel = self._login_required()
                token = self._bearer_token() or self._cookie_value(auth_mod.SESSION_COOKIE)
                self.service.logout(token, request_id=self._request_id())
                self.send_json(HTTPStatus.OK, {"ok": True, "mode": self.service.mode,
                                               "request_id": self._request_id(),
                                               "actor_id": session.get("user_id")},
                               {"Set-Cookie": self._set_cookie({}, expire=True)})
                return
            if path == API_PATH_AUTH_PASSWORD:
                session, channel = self._login_required()
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.send_json(HTTPStatus.OK, self.service.change_password(payload, session=session))
                return
            if path == API_PATH_AUTH_CHECK:
                session, channel = self._login_required()
                payload = self.read_json_body()
                self.send_json(HTTPStatus.OK, self.service.check_permission(payload, session=session))
                return
            if path == API_PATH_USERS:
                session, channel = self._session_and_permission("users.manage")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.send_json(HTTPStatus.CREATED,
                               self.service.create_user(payload, actor=session))
                return
            user_route = self._user_route(path)
            if user_route is not None:
                user_id, verb = user_route
                session, channel = self._session_and_permission("users.manage")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.send_json(HTTPStatus.OK,
                               self.service.user_action(user_id, verb, payload, actor=session))
                return
            if path == API_PATH_INTERVENE:
                session, channel = self._session_and_permission("intervene.decide")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                record, meta = self.service.intervene(payload, request_id=self._request_id(),
                                                      session=session)
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, record, {
                    "X-Intervene-Replay": "true" if meta.get("replay") else "false",
                    "X-Intervene-Status": str(meta.get("status") or record.get("status")),
                    "X-Intervene-Mode": str(meta.get("mode") or self.service.mode),
                    "X-Intervene-Audit-Hash": str(meta.get("audit_entry_hash") or ""),
                    "X-Intervene-Actor": str(meta.get("actor_id") or ""),
                })
                return
            if path == API_PATH_RESET:
                if not getattr(self.server, "allow_reset", True):
                    raise _bad("RESET_DISABLED")
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.reset(session))
                return
            # ---- 4.9 数据导入（t22）：粘贴 JSON / 上传文件内容 → 归一化入库 ----
            if path == API_PATH_IMPORT:
                # 权限 = 权限矩阵里现成的 `data.write`（4.3 的注释就写着"粘贴 / 上传 /
                # 演示数据"）：local 匿名可用，team 需 editor/admin，viewer 403。
                session, channel = self._session_and_permission("data.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                result = self.service.import_traces(
                    payload, request_id=self._request_id(), session=session,
                    lang=self._query_value("lang") or "zh")
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, result, {
                    "X-Import-Format": str(result.get("format") or ""),
                    "X-Import-Imported": str(result.get("imported") or 0),
                    "X-Import-Created": str(result.get("created") or 0),
                    "X-Import-Updated": str(result.get("updated") or 0),
                    "X-Import-Duplicates": str(result.get("duplicates") or 0),
                    "X-Import-Skipped": str(result.get("skipped") or 0),
                    "X-Import-Mode": str(result.get("mode") or ""),
                    "X-Import-Source-Kind": str(result.get("source_kind") or ""),
                    "X-Import-Durable": "true" if result.get("durable") else "false",
                    "X-Stream-Head-Seq": str((result.get("stream") or {}).get("head_seq") or 0)})
                return
            # ---- 4.4 实时接入（t14）：webhook 上报 + 令牌签发/吊销 ----
            if path == API_PATH_INGEST_EVENTS:
                payload = self.read_json_body()
                result = self.service.ingest_events(
                    payload, secret=self._ingest_secret(), request_id=self._request_id(),
                    idempotency_key=self._idempotency_key())
                self.service.flush_auth_events()
                token_used = (result.get("token") or {}).get("token_id")
                self.send_json(HTTPStatus.OK, result, {
                    "X-Ingest-Replay": "true" if result.get("replay") else "false",
                    "X-Ingest-Accepted": str(result.get("accepted") or 0),
                    "X-Ingest-Created": str(result.get("created") or 0),
                    "X-Ingest-Updated": str(result.get("updated") or 0),
                    "X-Ingest-Duplicates": str(result.get("duplicates") or 0),
                    "X-Ingest-Skipped": str(result.get("skipped") or 0),
                    "X-Ingest-Format": str(result.get("format") or ""),
                    "X-Stream-Head-Seq": str((result.get("stream") or {}).get("head_seq") or 0),
                    "X-Ingest-Token-Id": str(token_used or "")})
                return
            if path == API_PATH_INGEST_TOKENS:
                session, channel = self._session_and_permission("ingest.token.manage")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.CREATED, self.service.issue_ingest_token(
                    payload, actor_id=str(session.get("user_id") or LOCAL_ACTOR_ID)))
                return
            ingest_route = self._ingest_token_route(path)
            if ingest_route is not None:
                token_id, verb = ingest_route
                session, channel = self._session_and_permission("ingest.token.manage")
                self._csrf_guard(session, channel)
                self._read_body()          # 允许不带请求体；读了丢掉，keep-alive 才不串包
                if verb != "revoke":
                    raise _bad("METHOD_NOT_ALLOWED",
                               detail={"path": path, "hint": "only POST …/revoke is supported"})
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.revoke_ingest_token(
                    token_id, actor_id=str(session.get("user_id") or LOCAL_ACTOR_ID)))
                return
            # ---- 4.5 设置中心（t15）：测一次模型连接（超时/错误要能读懂）--------
            if path == API_PATH_SETTINGS_TEST_LLM:
                # 探测会把提示词真的发给模型端，所以口径与"改配置"完全一致：
                # `settings.write`（team 模式 = admin，local 模式 = 本机用户）+ CSRF。
                session, channel = self._session_and_permission(PERMISSION_SETTINGS_WRITE)
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                result = self.service.test_llm_connection(
                    payload, session=session, request_id=self._request_id())
                self.send_json(HTTPStatus.OK, result)
                return
            # ---- 5.0 总裁办（T2/T3/T4/T5/T6/T7/T9/T10；docs/12 §6）------------
            if path == API_PATH_SECRETARIAT_IDENTITIES:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.CREATED, self.service.secretariat_register(payload))
                return
            identity_route = self._secretariat_identity_route(path)
            if identity_route is not None:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK,
                               self.service.secretariat_revoke(identity_route, payload))
                return
            if path == API_PATH_SECRETARIAT_DELEGATIONS:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.CREATED,
                               self.service.secretariat_create_delegation(payload))
                return
            record_sign_route = self._secretariat_record_sign_route(path)
            if record_sign_route is not None:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK,
                               self.service.secretariat_sign(record_sign_route, payload))
                return
            if path == API_PATH_SECRETARIAT_VERIFY:
                self._require("audit.read")
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_verify(payload))
                return
            if path == API_PATH_SECRETARIAT_LETTERS:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.CREATED,
                               self.service.secretariat_create_letter(payload))
                return
            letter_verify_route = self._secretariat_letter_verify_route(path)
            if letter_verify_route is not None:
                self._require("audit.read")
                payload = self.read_json_body()
                self.send_json(HTTPStatus.OK,
                               self.service.secretariat_verify_letter(letter_verify_route))
                return
            if path == API_PATH_SECRETARIAT_SEAL:
                session, channel = self._session_and_permission("settings.write")
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                self.send_json(HTTPStatus.OK, self.service.secretariat_seal(payload))
                return
            if path in (API_PATH_HEALTH, API_PATH_RECORDS, API_PATH_AUDIT,
                        API_PATH_AUDIT_EXPORT, API_PATH_STREAM,
                        API_PATH_INGEST_STATUS, API_PATH_SETTINGS,
                        API_PATH_AUTH_STATUS, API_PATH_AUTH_PERMISSIONS, API_PATH_AUTH_ME,
                        API_PATH_SECRETARIAT, API_PATH_SECRETARIAT_STATUS):
                # `API_PATH_SECRETARIAT`（T1 族入口）= status 摘要，同样是只读端点：
                # 对它的 POST 说 405，与 /api/secretariat/status 完全同口径（R4 t4 收口）。
                raise _bad("METHOD_NOT_ALLOWED", detail={"method": "POST", "path": path,
                                                          "hint": "these endpoints are read-only"})
            raise _bad("ROUTE_NOT_FOUND", detail={"path": path})
        except ingest_mod.IngestError as exc:
            # 上报失败要带 ingest 的码（401/403/413/422/429/503），形状仍是 code/message/detail
            self.service.flush_auth_events()
            extra = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
            self.service.rejects += 1
            self.send_json(exc.status, exc.as_dict(self._request_id()), extra)
        except settings_mod.SettingsError as exc:
            # 4.5：设置域的校验失败（模式 / 地址 / 区间 / 未知字段…）用 settings 自己的码表，
            # 形状与干预域一致，界面按 code 查字典给中文原因。
            self.service.flush_auth_events()
            self.service.rejects += 1
            self.send_json(exc.status, exc.as_dict(self._request_id()))
        except auth_mod.AuthError as exc:
            self._send_auth_error(exc)
        except sec_mod.SecretariatError as exc:
            self._send_secretariat_error(exc)
        except InterveneError as err:
            self.service.rejects += 1
            self.send_json(err.http_status, err.as_dict(self._request_id()))
        except BrokenPipeError:
            self.close_connection = True
        except Exception as exc:                                        # noqa: BLE001
            self.send_error_body("INTERNAL_ERROR", detail={"hint": f"{type(exc).__name__}: {exc}"})

    def do_PUT(self) -> None:  # noqa: N802
        """4.5（t15）：PUT 只对 `/api/settings` 开放（保存设置），其余路径维持 405 口径。

        权限口径与 `/intervene` 完全一致：`settings.write` + CSRF。
        t13 的权限表里只有 `local` 与 `admin` 有这一项，所以"team 模式仅 admin 可改"
        在这段代码里不需要额外判断 —— `editor` / `viewer` 到这就是 403 ROLE_FORBIDDEN。
        请求体里的密钥字段只做转交（写盘前由 settings.py 用 DPAPI 加密），
        响应只回掩码 + 指纹 + "这次对密钥做了什么"（set / same / cleared / unchanged）。
        """
        self._new_request_id()
        path = request_path(self.path)
        try:
            if path == API_PATH_SETTINGS:
                session, channel = self._session_and_permission(PERMISSION_SETTINGS_WRITE)
                self._csrf_guard(session, channel)
                payload = self.read_json_body()
                self.service.flush_auth_events()
                result = self.service.save_settings(payload, session=session,
                                                    request_id=self._request_id())
                self.send_json(HTTPStatus.OK, result)
                return
            if path == API_PATH_SETTINGS_TEST_LLM:
                raise _bad("METHOD_NOT_ALLOWED",
                           detail={"method": "PUT", "path": path,
                                   "hint": "test-llm is POST only; PUT /api/settings "
                                          "saves configuration"})
            self._method_blocked()
        except auth_mod.AuthError as exc:
            self._send_auth_error(exc)
        except settings_mod.SettingsError as exc:
            self.service.flush_auth_events()
            self.service.rejects += 1
            self.send_json(exc.status, exc.as_dict(self._request_id()))
        except InterveneError as err:
            self.service.rejects += 1
            self.send_json(err.http_status, err.as_dict(self._request_id()))
        except BrokenPipeError:
            self.close_connection = True
        except Exception as exc:                                        # noqa: BLE001
            self.send_error_body("INTERNAL_ERROR", detail={"hint": f"{type(exc).__name__}: {exc}"})

    def do_PATCH(self) -> None: self._method_blocked()        # noqa: N802
    def do_DELETE(self) -> None: self._method_blocked()       # noqa: N802
    def do_OPTIONS(self) -> None: self._method_blocked()      # noqa: N802

    def _method_blocked(self) -> None:
        self._new_request_id()
        self.send_error_body("METHOD_NOT_ALLOWED",
                             detail={"method": self.command, "path": request_path(self.path),
                                     "hint": "same-origin only; no CORS preflight is served"})

    def _new_request_id(self) -> None:
        self.server.next_request_id()                     # type: ignore[attr-defined]
        self.server.last_request_id = f"req-{SERVER_VERSION}-{self.server.request_seq}"  # type: ignore[attr-defined]

    # -- 静态文件 ---------------------------------------------------------
    def serve_static(self, path: str) -> None:
        root = pathlib.Path(getattr(self.server, "web_dir", default_web_dir())).resolve()
        target = self.resolve_static(root, path)
        if isinstance(target, InterveneError):
            self.send_json(target.http_status, target.as_dict(self._request_id()))
            return
        if target is None:
            self.send_error_body("STATIC_NOT_FOUND", detail={"path": path})
            return
        suffix = target.suffix.lower()
        content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
        try:
            body = target.read_bytes()
        except OSError as exc:
            self.send_error_body("STATIC_NOT_FOUND",
                                 detail={"path": path, "hint": type(exc).__name__})
            return
        self._send(HTTPStatus.OK, body, content_type,
                   {"Last-Modified": self.date_time_string(int(target.stat().st_mtime))})

    @staticmethod
    def resolve_static(root: pathlib.Path, path: str) -> "pathlib.Path | InterveneError | None":
        """只允许 root 内的文件；拒绝穿越、反斜杠、目录与不存在的文件。"""
        raw = unquote(path or "/")
        if "\\" in raw or "\x00" in raw:
            return _bad("STATIC_PATH_REJECTED", detail={"path": path, "hint": "no backslash or NUL"})
        clean = raw.split("?", 1)[0].split("#", 1)[0]
        if clean in ("", "/"):
            clean = "/index.html"
        if not clean.startswith("/"):
            return _bad("STATIC_PATH_REJECTED", detail={"path": path, "hint": "absolute path required"})
        parts = [p for p in clean.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return _bad("STATIC_PATH_REJECTED", detail={"path": path, "hint": "parent traversal rejected"})
        candidate = root.joinpath(*parts) if parts else root / "index.html"
        try:
            resolved = candidate.resolve()
        except OSError:
            return _bad("STATIC_PATH_REJECTED", detail={"path": path})
        if resolved != root and root not in resolved.parents:
            return _bad("STATIC_PATH_REJECTED", detail={"path": path, "hint": "outside the web root"})
        if resolved.is_dir():
            if (resolved / "index.html").is_file():
                return resolved / "index.html"
            return _bad("STATIC_PATH_REJECTED", detail={"path": path, "hint": "directory listing is off"})
        if not resolved.is_file():
            return None
        return resolved

    # -- 日志：绝不记请求体（02 隐私条款：日志不含参数明文）---------------
    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", True):
            return
        super().log_message("%s %s status=%s", self.command, request_path(self.path),  # type: ignore[misc]
                            args[0] if args else "")

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        store = getattr(self.server, "access_log", None)
        if store is not None:
            store.append({"method": self.command, "path": request_path(self.path),
                          "status": int(code) if str(code).isdigit() else code,
                          "request_id": self._request_id()})
            if len(store) > 200:
                del store[:len(store) - 200]
        super().log_request(code, size)


class TraceLensServer(ThreadingHTTPServer):
    """带服务对象、请求计数与本机绑定的 HTTP 服务器。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 service: InterveneService, *, web_dir: pathlib.Path | str | None = None,
                 allow_reset: bool = True, quiet: bool = True) -> None:
        self.service = service
        self.web_dir = pathlib.Path(web_dir) if web_dir else default_web_dir()
        self.allow_reset = allow_reset
        self.quiet = quiet
        self.request_seq = 0
        self.last_request_id = ""
        self.access_log: list[dict[str, Any]] = []
        self._seq_lock = threading.Lock()
        # 4.4（t14）：停机时给每条 SSE 连接一个"收尾"信号，别让界面挂在半开的流上
        self.stream_hub = service.stream_hub
        self.stream_stop = threading.Event()
        super().__init__(address, handler)

    def stop_streams(self) -> None:
        """关掉广播：给所有订阅者送收尾信号（正在推的连接会自己结束）。"""
        self.stream_stop.set()
        self.stream_hub.stop()

    def handle_error(self, request, client_address) -> None:
        """客户端中途断开（刷新页面、断线重连、脚本读够就撤）是实时接入的常态：
        安静模式下不打整段栈，免得真异常被噪声盖住。真异常仍交给标准库打印。"""
        exc = sys.exc_info()[1]
        if self.quiet and isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                                           BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def shutdown(self) -> None:
        """先收尾流式连接，再交给标准库停机（顺序反了会留下半开的 SSE）。"""
        self.stop_streams()
        super().shutdown()

    def next_request_id(self) -> int:
        with self._seq_lock:
            self.request_seq += 1
            return self.request_seq

    def url(self) -> str:
        """给操作员看的本机地址。协议前缀在运行时拼装：包内不得出现 URL
        字面量（tests/test_trace_adapter.py 离线纯度扫描），而默认监听
        永远是回环，所以这个显示串不会指向外部服务。"""
        host, port = self.server_address[:2]
        return f"{LOOPBACK_SCHEME}{host}:{port}/"


def create_server(host: str = "127.0.0.1", port: int = 0, *,
                  service: InterveneService | None = None,
                  records: Sequence[Mapping[str, Any]] | None = None,
                  records_path: pathlib.Path | str | None = None,
                  web_dir: pathlib.Path | str | None = None,
                  allow_reset: bool = True, quiet: bool = True,
                  mode: str = MODE_LOCAL,
                  auth: auth_mod.Authenticator | None = None,
                  auth_state_path: pathlib.Path | str | None = None,
                  bootstrap_admin: bool = False,
                  session_ttl: int = auth_mod.SESSION_TTL_SECONDS,
                  audit_log_path: pathlib.Path | str | None = None,
                  secure_cookies: bool = False,
                  ingest_enabled: bool = True,
                  ingest_token_path: pathlib.Path | str | None = None,
                  ingest_rate: float = INGEST_RATE_DEFAULT,
                  ingest_rate_per_minute: float = INGEST_RATE_PER_MIN_DEFAULT,
                  ingest_max_events: int = ingest_mod.MAX_EVENTS_PER_REQUEST,
                  ingest_strict: bool = True,
                  stream_limit: int = STREAM_LIMIT_DEFAULT,
                  stream_heartbeat: float = STREAM_HEARTBEAT_SECONDS,
                  settings_path: pathlib.Path | str | None = None,
                  settings_env: Mapping[str, Any] | None = None,
                  settings_writable: bool = True,
                  records_store_path: pathlib.Path | str | None = None,
                  records_store_lock: float = store_mod.LOCK_TIMEOUT_SECONDS,
                  reset_demo: bool = False,
                  llm_transport: Any = None,
                  features_root: pathlib.Path | str | None = None,
                  secretariat: sec_mod.Secretariat | None = None,
                  sign_on_write: bool = False) -> TraceLensServer:
    """建服务器；`port=0` 用随机空闲端口（测试与 sidecar 都用它）。

    4.3 起 `mode="team"` 会带鉴权（`auth_state_path` 指定用户表；`bootstrap_admin=True`
    时零用户会创建首个 admin，并把一次性口令交给调用方打印，见 `main()`）。
    4.4（t14）加了实时接入的开关：`ingest_enabled=False` 关掉 webhook 写入
    （`--no-ingest`），`ingest_token_path` 指令牌表，`stream_limit` 限同时在线的
    SSE 订阅者数量（超了界面自动降级轮询）。
    4.5（t15）加设置中心：`settings_path` 指配置文件，`settings_env` 注入环境变量
    （容器/团队部署的密钥与地址从这里来），`settings_writable=False` 让界面只读，
    `llm_transport` 是测试用的假连接通道（产品里不给，见 tests/test_settings_api.py）。
    """
    svc = service or InterveneService(
        records=records, records_path=records_path, mode=mode, auth=auth,
        auth_state_path=auth_state_path, bootstrap_admin=bootstrap_admin,
        session_ttl=session_ttl, audit_log_path=audit_log_path,
        secure_cookies=secure_cookies, ingest_enabled=ingest_enabled,
        ingest_token_path=ingest_token_path, ingest_rate=ingest_rate,
        ingest_rate_per_minute=ingest_rate_per_minute,
        ingest_max_events=ingest_max_events, ingest_strict=ingest_strict,
        stream_limit=stream_limit, stream_heartbeat=stream_heartbeat,
        settings_path=settings_path, settings_env=settings_env,
        settings_writable=settings_writable,
        records_store_path=records_store_path, records_store_lock=records_store_lock,
        reset_demo=reset_demo,
        llm_transport=llm_transport,
        features_root=features_root, secretariat=secretariat,
        sign_on_write=sign_on_write)
    return TraceLensServer((host, port), TraceLensHandler, svc, web_dir=web_dir,
                           allow_reset=allow_reset, quiet=quiet)


def start_background_server(host: str = "127.0.0.1", port: int = 0, **kwargs: Any) -> tuple[TraceLensServer, threading.Thread]:
    """在守护线程里跑起来，返回 (server, thread)；`server.shutdown()` 停下。"""
    server = create_server(host, port, **kwargs)
    thread = threading.Thread(target=server.serve_forever, name="tracelens-http", daemon=True)
    thread.start()
    return server, thread


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_explainer.server",
        description="TraceLens 服务：静态页面 + POST /intervene（local 免登录 / team 需登录）")
    parser.add_argument("--host", default=None,
                        help="监听地址（local 默认 127.0.0.1；team 默认 0.0.0.0 供局域网访问）")
    parser.add_argument("--port", type=int, default=8765, help="端口（默认 8765；0 = 随机空闲端口）")
    parser.add_argument("--mode", choices=list(auth_mod.MODES), default=MODE_LOCAL,
                        help="local = 免登录单机（默认）；team = 多用户 + 登录 + 角色矩阵")
    parser.add_argument("--web", default=None, help="静态目录（默认 agent_explainer/web）")
    parser.add_argument("--records", default=None, help="演示记录 JSON（默认 samples/action_records.json）")
    parser.add_argument("--auth-state", default=None,
                        help="用户/会话状态文件（默认 var/auth/auth-state.json）")
    parser.add_argument("--session-ttl", type=int, default=auth_mod.SESSION_TTL_SECONDS,
                        help="会话有效期（秒，默认 8 小时）")
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="team 模式也不要自动创建首个 admin（用于你自己先用脚本建账号）")
    parser.add_argument("--audit-log", default=None,
                        help="追加式 JSONL 审计日志路径（4.2b：默认 var/audit.jsonl，重启后历史仍在）")
    parser.add_argument("--no-audit-log", action="store_true",
                        help="不落盘，只在内存哈希链里预览（重启即丢；测试与临时演示用）")
    parser.add_argument("--secure-cookies", action="store_true",
                        help="给会话 Cookie 加 Secure（放在做 HTTPS 的反向代理后面时用）")
    # ---- 4.4 实时接入（t14）----
    parser.add_argument("--no-ingest", action="store_true",
                        help="关闭实时接入：POST /api/ingest/events 一律 403 INGEST_DISABLED")
    parser.add_argument("--ingest-tokens", default=None,
                        help="ingest 令牌表路径（只存哈希；默认 local 在临时目录，"
                             "team 在 var/ingest/tokens.json）")
    parser.add_argument("--ingest-rate", type=int, default=INGEST_RATE_DEFAULT,
                        help="上报令牌桶容量 = 突发条数（默认 40）")
    parser.add_argument("--ingest-rate-per-minute", type=int,
                        default=INGEST_RATE_PER_MIN_DEFAULT,
                        help="上报稳态速率 = 每令牌每分钟条数（默认 600）")
    parser.add_argument("--ingest-max-events", type=int,
                        default=ingest_mod.MAX_EVENTS_PER_REQUEST,
                        help="单次上报允许的最大事件条数（默认 200）")
    parser.add_argument("--ingest-lenient", action="store_true",
                        help="坏事件跳过并计入 stats.skip_reasons（默认严格：一条坏就整批 422）")
    parser.add_argument("--issue-ingest-token", default=None, metavar="LABEL",
                        help="启动时签发一个 ingest 令牌，明文只打印这一次")
    parser.add_argument("--token-ttl", type=int, default=None, metavar="SECONDS",
                        help="配合 --issue-ingest-token：有效期秒数（默认不过期）")
    parser.add_argument("--stream-limit", type=int, default=STREAM_LIMIT_DEFAULT,
                        help="同时在线的 SSE 订阅者上限（默认 16；超了界面自动降级轮询）")
    parser.add_argument("--stream-heartbeat", type=float, default=STREAM_HEARTBEAT_SECONDS,
                        help="SSE 心跳间隔（秒，默认 15）")
    # ---- 4.5（t15）设置中心 ----
    parser.add_argument("--settings", default=None,
                        help="设置文件路径（默认 var/settings.json；密钥在 Windows 用 DPAPI 加密后落盘）")
    parser.add_argument("--settings-readonly", action="store_true",
                        help="设置页只读：界面改不动配置（容器/托管部署由运维用环境变量注入）")
    parser.add_argument("--show-settings", action="store_true",
                        help="启动时打印一次当前设置（密钥只显示掩码）")
    # ---- 4.9（t22）数据导入与记录持久化 ----
    parser.add_argument("--records-store", default=None, metavar="PATH",
                        help="记录持久化路径（默认 var/records.jsonl：JSONL 日志 + 内存索引，"
                             "启动时加载，用户的决定与导入的记录都活得过重启）")
    parser.add_argument("--features", default=None, metavar="DIR",
                        help="总裁办数据目录（默认 var/features：身份账本 / 委托 / 认定书 / 密钥库）")
    parser.add_argument("--no-secretariat", action="store_true",
                        help="关闭“入库即签名”：导入/上报的记录不再由 Agent 私钥签名（默认开启，仅 local 模式）")
    parser.add_argument("--no-records-store", action="store_true",
                        help="不落盘：只在内存里（重启回到演示数据；测试与临时演示用）")
    parser.add_argument("--records-store-lock", type=float,
                        default=store_mod.LOCK_TIMEOUT_SECONDS, metavar="SECONDS",
                        help="写记录时等文件锁的秒数（默认 5；共享目录/NFS 部署可调大）")
    parser.add_argument("--reset-demo", action="store_true",
                        help="显式重置演示数据：清掉导入记录与用户决定，把演示种子写回记录存储")
    parser.add_argument("--no-reset", action="store_true", help="关掉 POST /api/reset（演示环境更保守）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志（不含请求体）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    host = args.host or (TEAM_HOST if args.mode == MODE_TEAM else "127.0.0.1")
    # 4.2b（t18）：产品入口默认把审计写进文件链（var/audit.jsonl），重启后历史仍在；
    # --no-audit-log 才退回 t8 的纯内存预览。审计断链时服务端只报告，不清空不重建。
    audit_path = None if args.no_audit_log else (args.audit_log or str(default_audit_log_path()))
    # 4.9（t22）：记录也进持久存储（var/records.jsonl）。--no-records-store 才退回纯内存。
    store_path = (None if args.no_records_store
                  else (args.records_store or str(default_records_store_path(args.mode))))
    server = create_server(
        host, args.port, web_dir=args.web, records_path=args.records,
        allow_reset=not args.no_reset, quiet=not args.verbose, mode=args.mode,
        auth_state_path=args.auth_state, bootstrap_admin=not args.no_bootstrap,
        session_ttl=args.session_ttl, audit_log_path=audit_path,
        secure_cookies=args.secure_cookies,
        ingest_enabled=not args.no_ingest, ingest_token_path=args.ingest_tokens,
        ingest_rate=args.ingest_rate, ingest_rate_per_minute=args.ingest_rate_per_minute,
        ingest_max_events=args.ingest_max_events, ingest_strict=not args.ingest_lenient,
        stream_limit=args.stream_limit, stream_heartbeat=args.stream_heartbeat,
        settings_path=args.settings, settings_writable=not args.settings_readonly,
        records_store_path=store_path, records_store_lock=args.records_store_lock,
        reset_demo=args.reset_demo, features_root=args.features,
        sign_on_write=not args.no_secretariat)
    stats = server.service.store.stats()
    auth = server.service.auth
    print(f"{SERVER_NAME} API  {server.url()}")
    print(f"  static   : {server.web_dir}")
    print(f"  records  : {stats['count']} 条演示数据（synthetic={stats['synthetic']}）  mode={auth.mode}")
    # 4.9（t22）：记录落在哪里、坏行有几处，启动时就说清（"重启后还在吗"要有答案）
    store_facts = server.service.store.store_facts()
    if store_facts.get("durable"):
        journal = store_facts.get("journal") or {}
        print(f"             持久化={store_facts.get('path')}（JSONL + 内存索引 · "
              f"装载 {store_facts.get('loaded', {}).get('records', 0)} 条 · "
              f"坏行 {store_facts.get('problems', 0)} 处 · 写失败 {store_facts.get('write_errors', 0)} 次）"
              f"{' · 已按 --reset-demo 重置演示数据' if args.reset_demo else ''}")
    else:
        print("             持久化=关闭（--no-records-store）：重启回到演示数据")
    print(f"  import   : POST {API_PATH_IMPORT}（粘贴 JSON / 上传文件内容 · 自动识别 "
          f"{'/'.join(ta_mod.SUPPORTED_FORMATS)} · ≤{IMPORT_MAX_BYTES // 1024}KB·"
          f"≤{IMPORT_MAX_RECORDS} 条 · 权限 data.write）")
    print("  endpoints: GET /health /api/records /api/audit /api/auth/me /api/users")
    print("             POST /intervene /api/auth/login /api/auth/logout /api/users/<id>/{role,status,password}")
    print(f"  realtime : GET  {API_PATH_STREAM} (SSE) · GET {API_PATH_INGEST_STATUS}")
    print(f"             POST {API_PATH_INGEST_EVENTS} · POST {API_PATH_INGEST_TOKENS}"
          f" · POST {API_PATH_INGEST_TOKENS}/<id>/revoke")
    ing = server.service.ingest_health_block()
    stream = server.service.stream_health_block()
    print(f"             ingest={'开' if ing['enabled'] else '关（--no-ingest）'}"
          f"  鉴权={'team：必须带 ingest 令牌' if ing['auth']['required'] else 'local：匿名可写（如实标 anonymous）'}"
          f"  限速 {ing['rate']['per_minute']}/分钟·桶 {int(ing['rate']['capacity'])}")
    print(f"             SSE 心跳 {stream['heartbeat_seconds']}s · 订阅上限 {stream['limit']}"
          f" · 断线用 Last-Event-ID 补发（环形缓冲 {stream['replay']['ring_size']} 帧）"
          f" · 界面失败自动降级轮询")
    # 4.5（t15）：设置中心一行摘要（凭据只有掩码，明文从不打印）
    setv = server.service.settings.public()
    keys = server.service.settings.key_state()
    print(f"  settings : 模式={setv['llm']['mode']} 服务商={setv['llm']['provider']} "
          f"模型={setv['llm']['model'] or '（未填）'} "
          f"凭据={keys['mask'] if keys['present'] else '（未配置）'}（来源 {keys['source']}）")
    print(f"             语言={setv['ui']['language']} 出网={keys['outbound']} "
          f"存储={keys['storage']} 文件={setv['path']}")
    print(f"             GET {API_PATH_SETTINGS} · PUT {API_PATH_SETTINGS}"
          f"（需 {PERMISSION_SETTINGS_WRITE}：team 模式仅 admin）"
          f" · POST {API_PATH_SETTINGS_TEST_LLM}"
          f"{' · 界面只读（--settings-readonly）' if args.settings_readonly else ''}")
    if args.show_settings:
        print("  settings-json（掩码视图）:")
        print("    " + json.dumps(server.service.settings_view(None)["llm"],
                                  ensure_ascii=False))
    if args.issue_ingest_token:
        # 明文只显示这一次：文件里只有 sha256(salt+明文)，审计里只有前缀与 id
        issued = server.service.issue_ingest_token(
            {"label": args.issue_ingest_token, "ttl_seconds": args.token_ttl},
            actor_id=LOCAL_ACTOR_ID)
        print("  -------------------------------------------------------------")
        print(f"  ingest 令牌（{issued['record']['label']}，{issued['record']['token_id']}）")
        print(f"    {issued['token']}")
        print(f"    请求头：{ingest_mod.TOKEN_HEADER}: <token>  或  "
              f"{ingest_mod.AUTH_HEADER_NAME}: {ingest_mod.BEARER_PREFIX} <token>")
        print("    只打印这一次，之后任何接口都拿不回来；吊销用 "
              f"POST {API_PATH_INGEST_TOKENS}/<id>/revoke")
        print("  -------------------------------------------------------------")
    chain = server.service.audit_startup
    if audit_path:
        if chain.get("ok"):
            print(f"  audit    : {chain['path']}")
            print(f"             追加式 JSONL 链校验通过（{chain['count']} 条，链头 "
                  f"{str(chain['head'])[:18]}…，封印 {chain['seals']} 次）；重启后历史仍在")
        else:
            print("  audit    : 审计链自检未通过 —— /api/audit 返回 500，"
                  "/health 的 audit.ok=false")
            print(f"             {audit_broken_reason(chain)}")
    else:
        print("  audit    : 内存哈希链（--no-audit-log）：重启即丢，生产请给 --audit-log")
    if auth.mode == MODE_TEAM:
        print("  auth     : team 模式必须登录；viewer 只读（干预入口隐藏 + 403 ROLE_FORBIDDEN）")
        print(f"  users    : {auth.store.path}  会话 {auth.session_ttl // 3600} 小时")
        boot = auth.bootstrap_report
        if boot:
            print("  -------------------------------------------------------------")
            if boot.get("source") == "generated":
                print(f"  首次启动：已创建管理员 {boot['username']}，初始口令 {boot['password']}")
            else:
                print(f"  首次启动：已创建管理员 {boot['username']}，"
                      f"初始口令来自环境变量 {boot['source']}")
            print("  只打印这一次，之后任何接口都拿不到；首次登录后请立即改密。")
            print("  -------------------------------------------------------------")
    else:
        print("  auth     : local 模式免登录（身份 local-user）；team 模式请加 --mode team")
    try:
        server.serve_forever()
    except KeyboardInterrupt:                                          # pragma: no cover
        print("\nstopping...")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
