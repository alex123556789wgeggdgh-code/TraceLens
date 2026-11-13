# samples/ — TraceLens 演示夹具（全部为合成示例）

> **合成示例，非真实采集。** 本目录下所有 JSON 都由团队手工编写，**不来自任何真实系统、真实用户或真实 trace 采集**：没有真实人名、真实路径、真实金额、真实客户数据。
> 依据：`docs/01-product-and-users.md` 假设 A1/A2（用户没有真实 trace，团队必须自造夹具）与队长裁决②（不得要求用户提供真实 trace 才能开发/验收）。
> 界面规则：使用这些数据的任何演示界面**必须常驻"演示数据"标识**（t6/t7）；机器可校验字段：`synthetic=true`、`demo_data=true`、`source.kind="demo"`（schema C12/C15）。

## 文件清单

| 文件 | 输入格式 | 条目数 | 转出的 ActionRecord | 依据规范（写入 `source.format_version`） |
| --- | --- | --- | --- | --- |
| `traces/langfuse.json` | Langfuse 导出（trace + observations） | 8 个 observation | 8 条 | `Langfuse public API v1 (OpenAPI 1.0.0)` |
| `traces/otel_genai.json` | OTLP JSON（resourceSpans→scopeSpans→spans）+ GenAI 语义约定 | 7 个 span | 7 条 | `OTLP JSON v1.3 + GenAI semconv 1.30.0` |
| `traces/mcp_events.json` | MCP 事件流（JSON-RPC 2.0） | 16 个事件（9 条动作 + 握手/配对） | 9 条 | `MCP 2025-06-18 (JSON-RPC 2.0)` |
| `action_records.json` | 产品原生 ActionRecord v1.1 | 12 条记录 | 12 条（原样，不改写） | `ActionRecord schema 1.1` |

规范全文依据（超过 `format_version` 的 40 字符上限时，完整名保留在夹具 `_meta.spec_basis` 与归档的 `source_format_full`）：

- **Langfuse**：Langfuse public API v1（OpenAPI 1.0.0；trace / observation 的 `type`=SPAN·EVENT·GENERATION，`level`、`statusMessage`、`usage`、`metadata`；Ingestion API schema 2.0 的 `{"batch":[{"type","body"}]}` 形态也支持）。
- **OTel**：OpenTelemetry OTLP JSON v1.3（`resourceSpans→scopeSpans→spans`，属性为 AnyValue 形状，`status.code`=`STATUS_CODE_*`，时间 `*UnixNano`）+ GenAI 语义约定 1.30.0（`gen_ai.operation.name`、`gen_ai.provider.name`、`gen_ai.request.model`、`gen_ai.tool.name`、`gen_ai.tool.call.id/arguments/result`、`gen_ai.conversation.id`、`gen_ai.agent.id/name`、`gen_ai.usage.input_tokens/output_tokens`、`gen_ai.response.finish_reasons`）。
- **MCP**：Model Context Protocol 2025-06-18（`initialize` 结果的 `protocolVersion/serverInfo/capabilities`；`tools/list`、`tools/call`（`params.name/arguments/_meta`）、`notifications/message`（`level/logger/data`）、`notifications/progress`、`notifications/cancelled`；`content[{type:"text"}]`、`isError`；JSON-RPC 2.0 的 `id/method/params/result/error`）。
- **原生**：`docs/schemas/action_record.schema.json`（JSON Schema Draft 2020-12，`schema_version` 常量 `"1.1"`）。

三种外部格式的条目数满足任务要求（每个 6–10 条：8 / 7 / 9 条动作记录），原生演示集满足 `docs/02-data-model.md` 8.4 的 12 条 / ≥10 个 `action_type`（实际覆盖 12 个类型）要求。

## 扩展命名空间 `tracelens`

风险等级、影响面、"需要你确认"等语义在 Langfuse / OTLP / MCP 规范里**没有对应字段**。夹具统一通过 `tracelens` 命名空间提供，适配器**只读该命名空间**，绝不从 span 名/工具名猜风险（防止编造事实）：

| 格式 | 位置 | 键 |
| --- | --- | --- |
| Langfuse | `metadata.tracelens`（trace 与 observation 均可） | `agent_id`、`agent_name`、`requires_confirmation`、`requires_action_reason`、`risk_level`、`due_at`、`impact{targets,reversible,data_categories,summary}`、`confidence`、`rationale` |
| OTel | span 属性 `tracelens.*` | `tracelens.action_type`、`tracelens.task_name`、`tracelens.rationale`、`tracelens.confidence`、`tracelens.requires_confirmation`、`tracelens.requires_action_reason`、`tracelens.risk.level`、`tracelens.impact.targets/reversible/data_categories/summary`、`tracelens.due_at` |
| MCP | `params._meta.tracelens` | `requires_confirmation`、`requires_action_reason`、`risk_level`、`due_at`、`impact{...}` |

## 怎么跑（全程离线）

在 `agent-explainer/` 目录下执行（Python ≥3.10，只用标准库，无网络、无第三方依赖）：

```bash
# 单个夹具 → ActionRecord 列表
python -c "import json; from agent_explainer import convert_trace_to_action_record as c; \
print(len(c(json.load(open('samples/traces/langfuse.json', encoding='utf-8')), source_uri='samples/traces/langfuse.json')))"

# 带归档（未知字段不丢：记录本身字段封闭，原文进 raw_payloads[raw_ref]）
python -c "import json; from agent_explainer import convert_with_raw as c; \
o=c(json.load(open('samples/traces/mcp_events.json', encoding='utf-8'))); \
print(o.format, o.stats['conversion_rate'], list(o.raw_payloads)[0])"

# 4.4 实时摄取复用入口：混合批次逐条识别 + 跨条去重
python -c "import json; from agent_explainer import normalize_events as n; \
print(len(n(json.load(open('samples/traces/otel_genai.json', encoding='utf-8')))))"

# 全部断言（schema + 事实指纹 + 隐私 lint + 100% 转换率）
python -m unittest discover -s tests -v
```

`tools/validate_schema.py` 校验的是 `docs/` 内嵌示例；`samples/` 由 `tests/test_trace_adapter.py` 用同一套 `tools/jsonschema_mini.MiniValidator` + `tools/facts_hash.py` + 同一份隐私 lint 规则校验，标准一致。

## 适配器公开约定（与夹具行为一致）

1. **禁止编造事实**：没有来源依据就不写。只有 token 数、没有价格字段时 `cost=null`（token 数原样归档，不换算金额）；`rationale`/`result_summary` 缺失就留空，不用 LLM 补全。
2. **未知字段不丢**：记录受 `additionalProperties:false` 约束，因此未识别字段归档在 `raw_payloads[source.raw_ref]`（`unknown_fields` / `archived_known_fields`），可用 `convert_with_raw()` / `normalize_events_with_raw()` 取回；转换统计里的 `unknown_field_records` 计数有多少记录带归档。
3. **保守默认**：`delete/send/payment/config_change/export` 这类有副作用的动作，若来源没给影响面，则写入指针型 `impact_scope`（`reversible=false`、`data_categories=["internal"]`）并在 `uncertainty` 说明"来源未提供影响面，按不可撤回处理"；风险等级只因来源证据升高，绝不由工具名推断升高。
4. **决定 ≠ 生效**：适配器一律写 `user_intervention=null`、`execution_receipt.state="unreported"` 并带 `effect_notice="intervention.effect.recorded_only"`；界面显示"决定已记录，是否生效取决于执行端"（禁止显示"已停止 AI"）。
5. **时间戳**：无时区的裸时间按 **UTC** 处理（统一输出带时区的 ISO8601，`Z` 后缀）；OTLP 纳秒按整数换算，不丢精度。
6. **隐私脱敏**：写进记录的 `intent`/`rationale`/`result_summary`/摘要类字段先脱敏（`C:\Users\...`→`<path:1>`、`sk-...`→`<token>`、`Bearer ...`→`Bearer <token>`、邮箱→`<email>`），事实指纹按**脱敏后的值**计算，可复算。
7. **原生格式是镜像**：`schema_version="1.1"` 的原生记录原样透传（deep-copy，不改字段），只做门禁校验：版本、必填标识字段、枚举合法、id 去重。

## 演示集覆盖（`action_records.json`，对应 8.4 夹具规格）

12 条记录覆盖 12 个 `action_type`：`plan / read / tool_call / confirm / delete / error / write / send / ask / config_change / wait / finish`。

必含场景（每条记录 `demo_data=true`、`synthetic=true`、`source.kind="demo"`）：

- 需要确认（3 个选项 + 后果说明 + 到期时间）：`act-demo-0004`（整理/移动文件）、`act-demo-0005`（删除 12 个文件，critical + 不可撤回）、`act-demo-0011`（超时挂起，expired）
- 失败（`error.human_reason` + `suggested_next`）：`act-demo-0006`
- 高危带影响面且 `reversible=false`：`act-demo-0005`、`act-demo-0008`
- 修改后执行（decision=modify + receipt=executed）：`act-demo-0007`
- 拒绝并被执行端挡下（decision=reject + receipt=refused_by_runtime）：`act-demo-0010`
- 低置信（≤0.6）带 `uncertainty`：`act-demo-0003`
- `data_categories` 含 `personal_data`：`act-demo-0008`
- LLM 参与文案（`generated_by="hybrid"` + `may_contain_inference=true`）：`act-demo-0003`

`human_readable.source_facts_hash` 与 `tools/facts_hash.py` 同算法计算，可用 `tools/validate_schema.py`（同一实现）复算。

## 一键演示：边跑 Agent 边上报（4.4 实时接入）

```bash
python samples/sdk_demo.py
```

一条命令做完四件事，脚本自带断言，任何一条不符合就非零退出：

1. 在 `127.0.0.1` 的随机端口起服务（加载本目录夹具；令牌表落在 `var/sdk-demo-tokens.json`，只在签发令牌时才写，盘上只有哈希，明文只打印一次）；
2. 用 SDK 的 `@track` 装饰器与 `log_action` / `update_action` 实时上报动作：包含一个成功动作、一个失败动作（`status=failed` + `error.code`）、一个"需要用户决定"的发送动作；
3. 另一个线程订阅 `GET /api/stream`，断言帧号单调递增，且 `action.created` / `action.updated` / `ingest.batch` 都按序到达；
4. 打印 `/health` 的 ingest 与 stream 能力面、`/api/ingest/status` 的批次数与限流参数，并给出 `http://127.0.0.1:<port>/?api=live` —— 打开这个地址就能看到动作实时变成卡片（推送断开时界面自己降级为**写明原因**的轮询）。

口径与夹具一致：上报只带事实字段，入库后 `user_intervention=null`、`execution_receipt.state="unreported"`（决定 ≠ 生效），`source.kind="sdk"`，参数值不进上报体（只有 `param:参数名` 这类标签），文案由服务端按合并后的事实重算。演示数据全部是合成示例，只监听本机。

## 双语说明

本目录是**中文（zh）演示集**。英文界面文案由 t4 的原生英文模板 + i18n 字典（`web/src/i18n/dict.en.ts`）生成，**不是中文机翻**（`docs/03-architecture.md` 第 6 节）；`human_readable.language` 记录语言，`L1` 长度上限按语言取 zh 40 / en 80 字符。

## 来源与变更

- 生成者：backend-engineer（任务 t3 [t2-1]），生成时间 2026-11-10。
- 变更这些数据时：保持条目数区间、保持 `_meta` 的"合成示例，非真实采集"声明与 `spec_version`、保持隐私 lint 零命中，并同步更新 `tests/test_trace_adapter.py` 的期望值。
