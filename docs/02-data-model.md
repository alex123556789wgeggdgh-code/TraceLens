# TraceLens · 数据模型与存储设计（ActionRecord v1.1）

| 项 | 内容 |
|---|---|
| 文档编号 | TL-DM-002 |
| 对应子任务 | 1.2（团队任务 t2） |
| 版本 | v1.1（架构 v2；取代旧 agent-action-list 团队的 v1.0 草案） |
| 负责人 | 架构师（architect） |
| 上游 | [TL-PRD-001 产品定义与用户分层](./01-product-and-users.md)（t1，已验收） |
| 下游消费者 | t3 适配器与演示数据 · t4 翻译引擎 · t5 LLM 增强 · t6/t7 前端 · t8 干预 · t9 审计 · t13 认证 · t14 实时 · t15 设置 · t10/t11 测试 · t12/t16 交付 |
| 配套机器文件 | [action_record.schema.json](./schemas/action_record.schema.json) · [通过示例](./schemas/examples/action_record.examples.json) · [失败示例](./schemas/examples/action_record.invalid.json) · [校验报告](./schemas/validation-report.json) |
| 一句话读法 | 第 2 节字段表 = 数据长什么样；第 4 节状态机 = 什么时候能点什么按钮；第 6 节建表 = 后端照着写；第 8 节映射 = 每个功能落到哪个字段。 |

> **本文的约束力**：字段名、枚举值、状态语义、审计格式是 t3–t17 的**共同契约**。改动任何一个字段名等于让前端、后端、测试三方同时返工，因此任何修改必须在队长处显式变更记录，不接受"实现时顺手改了"。

---

## 0. 给不懂技术的读者：30 秒看懂

一条 **ActionRecord（行动记录）** = AI 助手做的一件事，被整理成一张卡片需要的所有信息。它回答四个问题：

1. **它做了什么**（`intent` 意图、`tool_used` 用了什么工具）
2. **为什么**（`rationale` 原因；来源没说就写"来源未提供"，**不许编**）
3. **结果怎样**（`status` 状态、`result_summary` 结果、`error` 失败原因）
4. **现在需不需要你拍板**（`requires_action` + `intervention_options` 三个选项 + 每个选项的后果）

两条最重要的设计纪律（写在 schema 里，机器会拦）：

- **纪律 P3｜人话不是事实**：界面上那句话（`human_readable`）必须能由结构化字段重新生成，并且带一个"事实指纹"（`source_facts_hash`）。AI 润色只能改措辞，一旦新增事实，指纹对不上 → 校验失败。
- **纪律 P4｜决定 ≠ 执行**：用户点了"同意/改参数/拒绝"，系统只保证**决定被记录了**（`user_intervention`）。AI 是否真的照做，是另一件事，由执行端回执（`execution_receipt`）单独记录。两者永不混写。界面文案默认："决定已记录；是否生效取决于执行端是否接入"。

---

## 1. 命名、版本与校验方式

| 项 | 内容 |
|---|---|
| 记录名 | `ActionRecord`，schema 版本 `1.1`（`schema_version` 字段固定为 `"1.1"`） |
| Schema 文件 | `agent-explainer/docs/schemas/action_record.schema.json`（JSON Schema **draft 2020-12**） |
| 结构规模 | **61 个属性**（基线 41 + 5.0 新增 20），其中 **27 个必填**（可空字段也必须显式写 `null`，杜绝"字段消失"导致的界面空白），**15 条条件约束**（C1–C15），**17 种动作类别** |
| 5.0 增量 | 21 个**全部可选**的签名字段与扩展字段（§2.8）+ `action_type` 新增 `rollback`；`required`、`allOf`、`SCHEMA_VERSION` 均**不变**（加法式演进，旧记录/旧适配器/旧夹具零改动） |
| 通过示例 | 6 条（要求 ≥3）：待确认高危删除 / 失败 / 低置信 LLM 润色 / 改参数放行+已执行 / 拒绝+运行时中止 / 超时挂起 |
| 失败示例 | **39 条**（要求 ≥1），每条标注被破坏的约束与期望命中的关键字 |
| 校验命令 | `python tools/validate_schema.py` → 50 项检查全通过，退出码 0 |
| 离线校验 | `python tools/test_offline_engine.py` → 屏蔽第三方库后仍 50/50 通过（证明纯标准库可判定） |
| 机器报告 | `docs/schemas/validation-report.json`（`python tools/validate_schema.py --json docs/schemas/validation-report.json` 生成） |

**校验器为什么有两个**：核心运行时的依赖政策是"纯标准库 + 离线可用"。所以 schema 的约束必须由标准库实现也能生效。`tools/jsonschema_mini.py` 是纯标准库实现的 Draft 2020-12 子集校验器；`tools/validate_schema.py` 用它和第三方 `jsonschema` 库**各跑一遍并要求判定完全一致**（含负向用例的命中关键字）。

---

## 2. 字段总表

必填列：**●** = 必填（可空字段也必须显式给 `null`）；○ = 可选。
"谁写"列：`adapter`= 接入层归一化时写入；`tl`= TraceLens 自身写入；`ui`= 用户干预时写入；`rt`= 执行端 SDK 回执。

### 2.1 身份与隔离（团队模式的地基）

| 字段 | 类型 | 必填 | 可空 | 谁写 | 含义（非技术） | 机器约束 | 对应功能/指标 |
|---|---|---|---|---|---|---|---|
| `schema_version` | const `"1.1"` | ● | ✗ | adapter | 记录格式版本号 | 非 1.1 → 拒绝写入，返回 `SCHEMA_VERSION_UNSUPPORTED` | M3 接入 |
| `id` | identifier | ● | ✗ | adapter | 这条记录的编号，也是**去重键** | pattern `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` | M3/M4、webhook 幂等 |
| `agent_id` | identifier | ● | ✗ | adapter | 哪个 AI 助手做的 | 同上 pattern | M4 模板选词 |
| `agent_name` | string ≤120 | ○ | ✓ | adapter | 助手展示名（可中文） | 长度上限 | M4 界面 |
| **`actor_id`** | identifier | ● | ✗ | adapter/tl | **这条数据属于谁**：local 模式固定 `local`，团队模式为登录用户 ID | 必填、非空、pattern | M6 权限矩阵、责任归属 |
| **`workspace_id`** | identifier | ● | ✗ | adapter/tl | **数据空间**：v1 单空间固定 `default`（Q3 裁决），字段先落库避免将来改表 | 必填、非空、pattern | M6 数据隔离 |
| `session_id` | identifier | ● | ✗ | adapter | 一次会话/一个任务的容器 | pattern | 三区视图分组、S3 时间轴 |
| `run_id` | string ≤128 | ○ | ✓ | adapter | 同一次会话里的第几次尝试 | — | S1 失败重试分组 |
| `parent_action_id` | string ≤128 | ● | ✓ | adapter | 父动作（构成动作树） | 顶层必须显式 `null`；pattern | S3 层级回放 |
| `depth` | int 0–32 | ○ | ✓ | tl | 层级深度（冗余存，换筛选速度） | 由 parent 链推导 | 筛选 <200ms |

### 2.2 时间

| 字段 | 类型 | 必填 | 可空 | 谁写 | 含义 | 机器约束 | 对应 |
|---|---|---|---|---|---|---|---|
| `timestamp` | 时间戳 | ● | ✗ | adapter | 动作**发生**时间 | 必须带时区偏移（pattern + format 双保险） | 排序、时间轴 |
| `received_at` | 时间戳 | ○ | ✓ | tl | TraceLens **入库**时间 | 同上 | K-04：`received_at - timestamp` = 管道延迟，SSE <1s 的度量口径 |
| `action_due_at` | 时间戳 | ○ | ✓ | tl | 提醒期限 | 到期只产生提醒（`status → expired`），**绝不自动放行** | W-02 护栏 |
| `latency_ms` | int ≥0 | ● | ✓ | adapter | 这一步耗时（毫秒） | 不得为负（N34） | K-04、L3 展示 |
| `sequence_no` | int ≥0 | ○ | ✓ | tl | 会话内单调序号 | 缺失时按 timestamp+id 补齐 | 同毫秒排序稳定 |

### 2.3 事实四要素（翻译层的原始素材）

| 字段 | 类型 | 必填 | 可空 | 含义 | 机器约束 | 对应 |
|---|---|---|---|---|---|---|
| `action_type` | 枚举（16 类，见 3.1） | ● | ✗ | 这件事属于哪类动作 | 必须覆盖 t1 点名的 10 类 | M4 模板选择 |
| `tool_used` | object{name, version, provider, call_id, params_summary, result_ref} | ● | ✓ | 用了什么工具；`params_summary` 是**脱敏摘要** | 非工具动作必须显式 `null`；摘要禁写完整参数明文 | M4、L3 |
| `intent` | string 1–500 | ● | ✗ | 它打算做什么 | 非空 | 四要素① |
| `rationale` | string ≤1000 | ● | ✓ | 它为什么这么做 | 缺失 → 翻译层输出降级文案"来源未提供原因"，**禁止编造** | 四要素② |
| `result_summary` | string ≤1000 | ● | ✓ | 客观结果摘要 | `status=succeeded` 时必须非空（C6） | 四要素③ |
| `human_readable` | object（见 2.4） | ● | ✗ | 界面要显示的人话 | 见 P3 护栏 | 四要素④ |
| `confidence` | number 0–1 | ● | ✓ | 翻译置信度 | ≤0.6 必须带 `uncertainty`（C7） | 护栏：不许把不确定讲成肯定 |
| `uncertainty` | object{reason, what_to_check, affected_fields[]} | ○ | ✓ | 哪里不确定、要去看什么 | C7 条件必填 | K-05 复述测试 |
| `cost` | object{currency, amount, tokens_in, tokens_out, estimated} | ● | ✓ | 花了多少钱/多少 token | 出现即要求 `currency`+`amount`+`estimated`；`estimated=true` 界面必须写"约" | L2 起显示 |
| `context` | object{task_name, audience_profile, default_granularity, user_language} | ○ | ✓ | 给谁看、默认展开到哪级 | 枚举 A/B/C、L1/L2/L3 | 默认 L1，永不默认 L3 |

### 2.4 翻译产物 `human_readable`（P3 护栏落地点）

| 子字段 | 类型 | 必填 | 含义与硬约束 |
|---|---|---|---|
| `l1` | string 1–80 | ● | 一句话结论 + 现状 + 需要你做什么。中文 ≤40 字 / 英文 ≤80 字符（A9） |
| `l2` | string[] ≤7 | ○ | 3–7 条步骤，每条一句"为什么"，每条 ≤30 字 |
| `l3` | string ≤2000 | ✓ | 参数级明细（工具/入参/耗时/成本/依据），单条 ≤120 字 |
| `language` | `zh` \| `en` | ● | 本条文案语种；双语 = zh/en **各自原生模板**，切换取对应语种（缺此字段 → N38） |
| `generated_by` | `template`\|`llm`\|`hybrid`\|`human` | ● | 默认 `template`（无 key、无网络也必须可用，K-12） |
| `template_id` / `model` / `generated_at` | — | ○ | 模板号 / 模型名 / 生成时间 |
| `source_facts_hash` | `sha256:<64hex>` | ● | **事实指纹**：对 `intent/rationale/result_summary/status/action_type/error/impact_scope/cost/latency_ms/confidence` 做规范化 JSON 取 sha256（算法见 `tools/facts_hash.py`） |
| `may_contain_inference` | boolean | ○ | `generated_by=llm/hybrid` 时强制 `true`（C8），界面显示"AI 生成"标记 |

**指纹的两条工程含义**（t4/t5 必须遵守）：
1. 事实没变 → 指纹不变：可缓存、可重放；翻译引擎对同一记录重复调用应得同一指纹。
2. 状态变化（如 `pending_confirmation → approved`）会改变指纹 → 干预后必须**重新生成 L1/L2 并更新指纹**（`store` 层在同一次事务里完成）。

### 2.5 状态、卡点与决定（P4 护栏落地点）

| 字段 | 类型 | 必填 | 可空 | 含义 | 机器约束 |
|---|---|---|---|---|---|
| `status` | 枚举（10 值，见 3.2） | ● | ✗ | 三段状态机所在段 | C1/C2/C5/C6/C13/C14 全部作用在它身上 |
| `requires_action` | boolean | ● | ✗ | 是否需要用户拍板（"需要我"区的数据来源） | true → 必须 C1 全套 |
| `requires_action_reason` | string ≤300 | ○ | ✓ | **为什么要你拍板**（T5 锚点：先讲原因再给按钮） | `requires_action=true` 时必须为 ≥4 字字符串（C1，N08） |
| `intervention_options` | option[] ≤6 | ● | ✗(空数组) | 可执行选项数组（**数组不是布尔**，才能呈现"三个选项 + 后果"） | `requires_action=true` → ≥3 项且必须含 `approve`/`modify`/`reject`（C1） |
| `user_intervention` | object | ● | ✓ | 用户的决定（**决定已记录**） | 见下；`pending_confirmation` 时必须为 `null`（C14） |
| `execution_receipt` | object | ● | ✓ | 执行端回执（**是否真的生效**） | 见下；C11/C13 |

`intervention_options[]` 每个选项：

| 子字段 | 必填 | 约束 |
|---|---|---|
| `id` | ● | identifier |
| `kind` | ● | `approve` \| `modify` \| `reject`（三者齐备是 C1 的硬条件） |
| `label` | ● | ≤40 字（防按钮溢出）；文案走 i18n key |
| `consequence` | ● | **≥4 字**：点了会怎样 + 不点会怎样（缺 → N05） |
| `effect_notice` | ○ | 生效边界文案 key，默认 `intervention.effect.recorded_only` |
| `editable_fields[]` | kind=modify 必填 | ≥1 项，含 `name/label/type/required/default/min/max/pattern/unit/help_text`（缺 → N07）；前端按此**动态生成表单**（US-04） |
| `requires_reason` | ○ | `kind=reject` 时强制 `true`（C1 子规则，N06） |
| `dangerous` | ○ | `reject` 强制 `true`（破坏性样式） |
| `risk_note` | ○ | 例如"此操作不可撤销" |

`user_intervention`（决定段）：

| 子字段 | 必填 | 约束与含义 |
|---|---|---|
| `decision` | ● | `approve`/`modify`/`reject`；必须与 `status` 一一对应（C2：approved↔approve、modified↔modify、rejected↔reject，N11） |
| `option_id` | ○ | 用户实际点了哪个选项 |
| `decided_by` | ● | 决定人 `actor_id`（与审计一致，P11"谁批的"） |
| `decided_by_role` | ● | `local`/`admin`/`editor`/`viewer`（记录决定当时的角色） |
| `decided_at` | ● | 决定时间（带时区） |
| `original_params` / `modified_params` | decision=modify 必填 | 两个都必须是**非空对象**（C3，N36）；逐字段核对视图的数据源 |
| `modified_fields[]` | ○ | `{name, before, after}` 列表（C 类用户逐字段核对） |
| `reason` | decision=reject 必填 | ≥2 字（C4，N37）：拒绝必填原因 |
| `request_id` | ○ | 对应 `/intervene` 请求 ID，串起日志与审计（日志中不得含参数明文） |
| `audit_entry_hash` | ○ | 指向审计 JSONL 条目的 `hash`，记录↔审计双向可追 |
| `recorded` | ● | **常量 `true`**：本对象语义就是"决定已记录"，不承诺执行 |

`execution_receipt`（执行回执段）：

| 子字段 | 必填 | 约束与含义 |
|---|---|---|
| `state` | ● | `unreported`（**默认**：执行端没回执 → 决定可能没生效）/ `executed` / `failed` / `refused_by_runtime`（执行端读到 reject 后自行中止）/ `timeout`（执行端在窗口内没读到决定） |
| `reported_by` | 终态必填 | 回执来源（agent 运行时标识 / SDK 会话） |
| `reported_at` | 终态必填 | 回执时间；**没有时间的"已执行"是假闭环，schema 直接拒绝**（C11，N12） |
| `detail` | ○ | 一句话说明（不得含参数明文） |
| `sdk_version` | ○ | 回执来自哪个 SDK 版本 |
| `effect_notice` | ○ | 界面文案 key：`intervention.effect.recorded_only` / `applied_by_runtime` / `pending` |

### 2.6 风险、影响面与失败

| 字段 | 必填 | 含义与约束 | 对应 |
|---|---|---|---|
| `risk_level` | ● | `low`/`medium`/`high`/`critical`；批量确认低危、高危单独停的数据依据 | B 类"别拿噪音烦我" |
| `impact_scope` | ●(可空) | `{targets[]{kind,ref,change,count}, money{currency,amount,direction}, external_endpoint{url,method,audience}, data_categories[], reversible, summary}` | S4 影响面预览 |
| ↑ 约束 | — | `high`/`critical` 必须给 `targets≥1` 且 `requires_human_confirmation=true`（C9，N25/N26/N27）；`delete/send/payment/config_change/export` 必须给影响面（C10，N28）；`reversible=false` → 界面写"此操作不可撤销"（v1 不做自动回滚，W-03） | S4、W-02/W-03 |
| `policy` | ● | `{requires_human_confirmation, auto_approve_forbidden: const true, timeout_behavior: const "keep_pending", queue_serial, min_role_to_decide}` — **把产品护栏写进数据**：允许自动同意（N15）或超时自动同意（N16）的记录是非法记录 | W-02 |
| `error` | ●(可空) | `{code, human_reason, technical_detail_ref, retryable, failed_step}`；`status=failed` 必填（C5，N18） | S1 失败三要素①② |
| `suggested_next[]` | ○ | `{text, kind∈retry/edit_params/provide_input/contact_owner/ignore/other, target_action_id}`；`failed` 时至少 1 条（C5，N19） | S1 三要素③ |

### 2.7 来源、演示标记与隐私

| 字段 | 必填 | 含义与约束 | 对应 |
|---|---|---|---|
| `source` | ● | `{kind∈demo/paste_json/upload/webhook/sdk/file_watch/manual, adapter, format_version, source_uri, ingested_at, raw_ref}`；缺整个 source → N39 | K-03 接入归因 |
| `raw_ref` | ○ | 未识别字段的**指针**（不是明文）。W-10：未知字段不丢，但不参与翻译；日志与导出默认不含 | 隐私 P13 |
| `demo_data` | ● | `true` = 内置演示数据集加载（A2 徽标 + 导出页脚，不可关闭）；true → `source.kind=demo` 且 `synthetic=true`（C12，N29） | A2 徽标 |
| `synthetic` | ● | `true` = 合成数据（非真实采集）。**A1 的机器可校验表达**：本项目没有真实 trace 样本，所有演示与夹具必须 `synthetic=true`；`source.kind=demo` 却 `demo_data=false` → N30 | A1/A2 诚实性 |
| `trace_link` | ○ | `{trace_id, span_id, observation_id, source_url}` 回到原始记录的指针（只读接入，不迁移不改写源） | 1.4 节"零迁移" |
| `labels[]` | ○ | 筛选标签，`uniqueItems`（N31） | 筛选 <200ms |

**隐私 lint（schema 表达不了，由 `tools/validate_schema.py` 承担，且 lint 自身有自检）**：任何记录、日志、导出中不得出现真实用户目录（`C:\Users\…`、`/Users/…`、`/home/…`）、`sk-…` 形式密钥、`AKIA…`、`Bearer <token>`。lint 自检：5 条坏样本全部命中、4 条好样本零误报。

### 2.8 5.0 签名与扩展字段（21 个，**全部可选**，`required` 未变）

5.0（[TL-SEC-005 §3.1](./07-secretariat-and-extensions.md)）新增的字段**全部可选**：`required` 仍是 27 项，
`schema_version` 仍是 `"1.1"`。理由：5.0 是**加法**——旧记录、旧适配器、旧夹具一字不改也必须继续
通过校验；所有新能力都有一个"未启用 / 未发生"的合法表达（`null` 或字段缺席），不用
`null` 假装"有但为空"。

| 字段 | 类型 | 谁写 | 含义 | 机器约束 / 诚实边界 |
|---|---|---|---|---|
| `agent_pubkey` | string(44) base64 \| null | secretariat | 该实例的 Ed25519 公钥 | pattern `^[A-Za-z0-9+/]{43}=?$`；**私钥只在本地 keyring.json（0600）**，永不进记录/API/日志 |
| `human_owner` | DID \| null | secretariat | 对此 Agent 行为负责的用户 DID（责任链的根） | `$defs/did`；注意与 `actor_id`（数据归属者）不是一回事 |
| `delegation_chain` | DID[] ≤8 \| null | secretariat | 人 → … → 本 Agent 的责任链 | 每项 `$defs/did`；委托深度上限 5 → 链最多 6 个 DID |
| `input_hash` / `output_hash` | hex64 \| null | secretariat | 入参 / 出参的规范 JSON sha256 | 用 `$defs/hexDigest`（**不带** `sha256:` 前缀）；进签名范围 |
| `tool` | string ≤120 \| null | secretariat | 签名域里的工具名（由 `tool_used.name` 派生后写回） | **不是新的用户输入**；与 `tool_used` 并存，保证"签了名也知道签的是哪个工具调用"，离线复算签名时需要它 |
| `prev_hash` | hex64 \| null | secretariat | 该 Agent 上一条已签名记录的 `entry_hash` | 链根 = 64 个 `0`（`feature_store.GENESIS_HASH`）；断链只报告不重建 |
| `entry_hash` | hex64 \| null | secretariat | 本条记录的哈希 | 覆盖除 `signature`/`tsa_timestamp` 外的整条记录 → 证据哈希与因果解释**自然进签名** |
| `signature` | base64 \| null | secretariat | `SIGNATURE_FIELDS` 白名单的 Ed25519 签名（88 字符） | 只签白名单、不签整条记录；否则追加证据/干预历史会让旧签名永久失效 |
| `tsa_timestamp` | string ≤64 \| null | secretariat | 时间戳证明 | **显式降级项**：写 `local-tsa:<ISO8601Z>`，验签标 `tsa_state="local_only"`；**禁止**宣称"可信时间戳" |
| `approval` | object \| null | secretariat | 审批背书 `{required, approvers[]{did,role,signed_at,signature}, sealed, seal}` | 风险策略：low/medium=0、high=1、critical=2+盖章；审批签名**只证明"这次审批在本地被记录并签名"**，不证明真实世界的签字意愿（这句要进界面文案） |
| `multimedia_evidence` | string \| object[] ≤50 \| null | evidence | 证据 id，或证据条目数组 | 条目字段是 §3.2 规范形状与 `evidence/index.jsonl` 索引形状的**并集**，只强制 `kind`；**截图不是抓屏**：由执行端/用户提供，`provided_by` 如实标注 |
| `causal_explanation` | string \| object \| null | evidence | 反事实解释（"如果选 B 会……，因为……"） | 对象形态必填 `counterfactual`；`model ∈ template/llm`（与 `generated_by` 同一诚实口径） |
| `confidence_source` | object \| null | confidence | 置信度来源 | `method ∈ logprobs/tool_success_rate/hybrid/template/default`；**阈值与颜色带不在 schema**（见下方说明） |
| `intervention_history` | object[] ≤50 \| null | 干预流程（**服务端派生**） | 每次干预的摘要（**只追加**） | 条目形状由干预流程决定，schema 只要求"是对象"（不把动态表单形状钉进数据契约）。**导入/上报不得提供该字段**（`EXPANSION_PROTECTED_FIELDS`）：干预必须每次独立成条 signed 记录，否则入参可伪造的"干预历史"会被 Agent 签进记录，等于替伪造的责任链背书 |
| `group_id` | identifier \| null | groups | 所属 TaskGroup | `null` = 不属于任何任务组（单 Agent 动作的常态，不是错误） |
| `shared_users` | object[] ≤50 \| null | sharing | 共享给谁、什么权限、通过哪次邀请 | `permission ∈ view/approve/manage`（manage ⊃ approve ⊃ view）；**授权以服务端 `sharing.can()` 为准**，数组只是记录副本 |
| `rollback_ref` | identifier \| null | rollback | 被回滚的原记录 id | 回滚记录自身的 `action_type` 必须是 `rollback` |
| `rollback_state` | enum \| null | rollback | `none` / `rolled_back` / `rollback_blocked` | **显式降级项**：回执是本地模拟逆向动作（`receipt_state="simulated_local"`），不是外部系统真的被撤销 |
| `improvement_suggestion` | object \| string \| null | suggestions | 命中改进建议时的摘要 | 建议**不是自动改参数**：采纳（accept）才写 UserPreference，弃用（dismiss）有 7 天冷却 |
| `evidence_feedback` | object \| null | evidence | 证据的点赞 / 点踩计数 | `{like, dislike, updated_at}`；计数只用于证据质量排序，不改事实字段 |

**签名器对这些上限的执行口径（R3 落地，写清楚免得被当成"schema 没管"）**：
`tool ≤ 120`、`approval.approvers ≤ 4`、`approval.approvers[].role ≤ 40` 都是 schema 的机器约束。
`secretariat.sign_record` 在**签名之前**校验它们，超限直接 400 `SECRETARIAT_FIELD_INVALID`
（`field=tool` / `field=approvers`）——**不裁值也不放行**：裁掉的值会出现在签名里、却在记录里看不见，
放行的值会让这条"已签名记录"当场过不了自家 schema（docs/12 §7 A2）。
审批条目的签名 payload 是 `{did, role, signed_at, record_id, action_type, input_hash,
output_hash, risk_level, required}` 的规范 JSON（docs/12 §4 冻结）：绑定本条记录，
所以同一个审批人在两条记录上的审批签名不可能相同，跨记录复用无效。
链头记录里带该 Agent 最近 500 条 `entry_hash`（`HEAD_HISTORY`），`verify_record` 据此把
`prev_hash` 判成 `continuous`；没有这些状态（换机器离线复算）时如实标
`unverifiable_without_chain`，不假装"已验证连续性"。

**为什么"`confidence < 0.5` → 必须触发干预"没写进 schema（别再加回去）**：
schema 已经有 C7（`confidence ≤ 0.6` → 必须带 `uncertainty`）；但"低置信必须要求人拍板"这条在
**现有夹具上不成立**：正向示例 `act-0004-read-contracts` 的 `confidence = 0.45` 且
`requires_action = false`（读类动作只标不确定、不需要人）。若硬加
`if confidence < 0.5 then requires_action = true`，`python tools/validate_schema.py` 的正向校验会
立刻失败——为了写一条约束而破坏"schema 可被机器验证"是更坏的交易。
所以这条规则**在代码层落地**：`confidence.apply_to_record()` 在 `confidence < 0.5` 时写
`requires_action = true` 并补 `requires_action_reason` 与 ≥3 个干预选项，由
`tests/test_expansion_acceptance.py` 断言。**分工写清楚**：schema 保证**形状**（来源可追溯、选项齐备），
代码保证**策略**（多少分要人管）。

---

## 3. 枚举字典（全量，前后端共用，不得各自造词）

### 3.1 `action_type`（17 类，前 10 类为 t1 点名必须覆盖）

`read` 读 · `write` 写 · `plan` 计划 · `confirm` 请求确认 · `error` 来源上报的错误事件 · `ask` 向人提问 · `tool_call` 调用工具 · `wait` 等待 · `finish` 收尾 · `intervention` 干预动作 · **`rollback` 回滚动作（5.0 新增）** · `config_change` 改配置 · `send` 对外发送 · `payment` 付款 · `export` 导出 · `delete` 删除 · `other` 其他

> `write/delete/send/payment/config_change/export` = **副作用类**：C10 要求必须给影响面。
> `error` 是"来源上报了错误事件"这类**动作**；具体失败详情仍看 `status=failed` + `error` 对象。
> `rollback` = 撤销某个已发生动作的**补偿动作**（[TL-SEC-005 功能一](./07-secretariat-and-extensions.md)）：
> 它本身也是一条被审计、被签名的记录，必须用 `rollback_ref` 指明撤销的是谁，用
> `rollback_state` 说明回滚走到了哪一步。**它不参与 C10**（回滚是消除副作用，不是新增副作用）；
> 它是否"真的撤销了"看回执（本轮 = `simulated_local`，见 §2.8）。

### 3.2 `status`（10 值，三段）

| 段 | 值 | 中文含义 | 谁能写 | 界面区 |
|---|---|---|---|---|
| ① 记录段（来源事实） | `observed` | 已发生、无需人参与 | adapter | 已完成 |
| | `running` | 正在跑 | adapter/rt | 进行中 |
| | `succeeded` | 成功（来源上报） | adapter/rt | 已完成（绿） |
| | `failed` | 失败（来源上报） | adapter/rt | 出错（红） |
| | `skipped` | 被跳过 | tl/adapter | 已完成（灰） |
| ② 决定段（TraceLens 写） | `pending_confirmation` | **需要你**：等拍板 | tl | 需要我（蓝，置顶） |
| | `approved` | 决定已记录：同意 | tl | 已完成（带"已同意"标） |
| | `modified` | 决定已记录：改了参数 | tl | 已完成（带"已改参数"标） |
| | `rejected` | 决定已记录：拒绝 | tl | 已完成（带"已拒绝"标） |
| ③ 挂起 | `expired` | 超过提醒时间，**仍在等人** | tl | 需要我（红点提醒） |

**执行结果不在 `status` 里**：执行端回执只写 `execution_receipt.state`（`unreported`/`executed`/`failed`/`refused_by_runtime`/`timeout`）。记录段出现的 `succeeded/failed` 是**来源系统自己上报的事实**，不是 TraceLens 宣布的"AI 已照办"。

### 3.3 其它枚举

| 枚举 | 取值 |
|---|---|
| `risk_level` | `low` / `medium` / `high` / `critical` |
| `user_intervention.decision` | `approve` / `modify` / `reject` |
| `execution_receipt.state` | `unreported`（默认）/ `executed` / `failed` / `refused_by_runtime` / `timeout` |
| `source.kind` | `demo` / `paste_json` / `upload` / `webhook` / `sdk` / `file_watch` / `manual` |
| `generated_by` | `template`（默认）/ `llm` / `hybrid` / `human` |
| `language` | `zh` / `en` |
| `impact_scope.targets[].kind` | `file` `directory` `table` `record` `endpoint` `service` `mailbox` `payment` `resource` `pii_dataset` |
| `impact_scope.targets[].change` | `create` `update` `delete` `read` `send` `export` |
| `data_categories` | `none` `internal` `personal_data` `financial` `credentials` `health` `contract` `customer_data` |
| `role` | `admin` / `editor` / `viewer`（团队）；`local`（本地默认用户，不参与登录） |
| `suggested_next[].kind` | `retry` `edit_params` `provide_input` `contact_owner` `ignore` `other` |
| **5.0 新增** `rollback_state` | `none` / `rolled_back` / `rollback_blocked` |
| **5.0 新增** `multimedia_evidence[].kind` | `screenshot` / `diagram` / `diff`（对应 `media_type`：`image/png`、`image/svg+xml`、`text/x-diff`） |
| **5.0 新增** `multimedia_evidence[].provided_by` | `runtime` / `user` / `server`（如实标注证据是谁提供的） |
| **5.0 新增** `shared_users[].permission` | `view` / `approve` / `manage`（级联：manage ⊃ approve ⊃ view） |
| **5.0 新增** `confidence_source.method` | `logprobs` / `tool_success_rate` / `hybrid` / `template` / `default` |
| **5.0 新增** `causal_explanation.model` | `template` / `llm` |
| 错误码 | 见 [TL-ARCH-003 第 6 节](./03-architecture.md) 统一错误码表；5.0 新增 8 个域的码见 [TL-SEC-005 §7](./07-secretariat-and-extensions.md) |

---

## 4. 状态机与干预语义（队长裁决 Q2 的落地）

### 4.1 三段状态机

```
【记录段 · 来源事实】                         【决定段 · TraceLens 写】        【执行段 · 执行端回执】
observed ──┐                              pending_confirmation ──┬── approved ─┐
           ├── running ── succeeded                              ├── modified  ├─► executed / failed /
           │                                                     └── rejected  ┘    refused_by_runtime / timeout
           └── failed（error + human_reason + suggested_next≥1）        │
                  └── 重试生成新记录（run_id+1，parent 指向自己）        └── expired（超时提醒，仍等人，绝不自动同意）
```

### 4.2 迁移表（谁能触发、写哪些字段、发什么事件）

| 迁移 | 触发者 | 前置条件 | 写入 | 广播（SSE） | 审计 |
|---|---|---|---|---|---|
| `→ pending_confirmation` | adapter / tl | `requires_action=true`；选项≥3 含三 kind；`requires_action_reason` 非空 | `status, requires_action*, intervention_options, action_due_at, policy` | `action.needs_decision` | `record.needs_decision` 事件 |
| `pending_confirmation → approved` | `POST /intervene` (editor/admin/local) | 选项含 approve；请求 `idempotency_key` | `status=approved` + `user_intervention{decision=approve, decided_by, decided_by_role, decided_at, recorded=true, request_id, audit_entry_hash}` + 重渲染 `human_readable` | `action.decision_recorded` | `intervention.decision`（哈希链） |
| `→ modified` | 同上 | `modified_params` 非空且通过该选项 `editable_fields` 的字段校验 | 同上 + `original_params/modified_params/modified_fields` | `action.decision_recorded` | 同上（含**脱敏后**的改前/改后） |
| `→ rejected` | 同上 | `reason` 非空 | 同上（`decision=reject`） | `action.decision_recorded` | 同上 |
| `pending_confirmation → expired` | 定时器（`action_due_at` 到期） | 无任何决定 | `status=expired`，**仅提醒** | `action.overdue` | `intervention.overdue_reminder` |
| 任意 → `execution_receipt.state=executed/failed/refused_by_runtime` | 执行端 SDK 回执（`POST /api/ingest/events`，`kind=sdk`） | 必须带 `reported_by` + `reported_at`（C11） | 只写 `execution_receipt`；记录段 `status` 由来源事实决定 | `action.execution_receipt` | `intervention.execution_receipt` |

### 4.3 三条不可违反的语义纪律（写进界面文案与 API 文案）

| # | 纪律 | 机器保障 | 界面文案（i18n key） |
|---|---|---|---|
| 1 | **决定已记录 ≠ 已执行**。`/intervene` 只保证记录 + 审计 + 广播；真正生效需要用户的 agent 运行时接入 SDK 预检查（`wait_for_decision` / 回调）后自行执行 | `user_intervention.recorded` 常量 `true`；`execution_receipt.state` 独立字段；无回执时间的 `executed` 被拒绝（N12） | `intervention.effect.recorded_only` = "决定已记录；是否生效取决于执行端是否接入" |
| 2 | **禁止宣称"AI 已被强制停下"**。`unreported` 状态下界面必须显示"执行端未回执" | `state` 枚举封闭，无"stopped"这类值（N13） | `intervention.effect.pending` = "执行端尚未读取这个决定" |
| 3 | **超时绝不自动同意**（W-02） | `policy.auto_approve_forbidden` 常量 `true`、`timeout_behavior` 常量 `keep_pending`（N15/N16）；`expired` 不得带 `executed`（C13，N14） | `intervention.overdue.hint` = "已超时，仍在等您，系统不会替您决定" |

---

## 5. 条件约束表（C1–C15 与负向用例对照）

| 编号 | 规则（一句话） | 动机（来自 1.1） | 机器关键字 | 负向用例 |
|---|---|---|---|---|
| C1 | `requires_action=true` → 选项 ≥3 且含 approve/modify/reject，状态只能 pending_confirmation/expired，必须写"为什么需要你"，且此时不得已有决定 | M6、T5 锚点、护栏"一次只问一件事" | `minItems` `contains` `enum` `required` `const` `type` | N04 N05 N06 N07 N08 N09 N10 |
| C2 | 决定段状态与 `decision` 一一对应 | K-08 干预后状态正确 | `const` | N11 |
| C3 | `modify` 必须有非空 `original_params` + `modified_params` | US-04 逐字段核对 | `required` `minProperties` | N36 |
| C4 | `reject` 必须填原因 | C 类责任边界 | `required` | N37 |
| C5 | `failed` 必须有 `error(code+human_reason)` 且建议动作 ≥1 | S1 三要素、T6"有路可走" | `required` `minItems` | N18 N19 |
| C6 | `succeeded` 必须有非空结果摘要 | 结果导向（不做排版美化） | `type` `minLength` | N20 |
| C7 | `confidence ≤ 0.6` 必须说明哪里不确定 | 护栏 P3、K-05 | `type` `required` | N22 |
| C8 | LLM 润色必须打推断标记 | 防"润色后无法核对" | `const` | N21 |
| C9 | `high/critical` 必须给影响面 + 必须人确认 | S4、W-02 | `type` `required` `const` `minItems` | N25 N26 N27 |
| C10 | 副作用动作类型必须给影响面 | 不可逆副作用可交代 | `required` `type` | N28 |
| C11 | 终态执行回执必须有 `reported_by`+`reported_at` | 假闭环禁令（Q2 裁决） | `required` `type` | N12 N13 |
| C12 | 演示数据 → `source.kind=demo` 且 `synthetic=true` | A2 徽标不可造假 | `const` | N29 |
| C13 | `expired` 必须仍在等人（不得带已执行回执） | W-02 | `const` `enum` | N14 |
| C14 | `pending_confirmation` 不得已有决定 | K-08 | `type` `enum` | N10 |
| C15 | `source.kind=demo` 必须 `demo_data=true` | A1/A2 诚实性 | `const` | N30 |
| 结构 | ID 形态、时间带时区、字段封闭、标签去重、版本常量、耗时非负、cost 完整性 | 查询与渲染可信；拼错字段不得静默丢弃 | `pattern` `additionalProperties` `uniqueItems` `const` `minimum` | N03 N17 N31 N32 N33 N34 N35 |
| 身份 | `actor_id` / `workspace_id` / `source` 必填 | M6 隔离与责任、K-03 归因 | `required` | N01 N02 N39 |
| 翻译 | 渲染必须标语种；事实指纹必填 | 双语（M4）、P3 | `required` | N23 N38 |

---

## 6. SQLite 存储模型

### 6.1 库与版本

| 项 | 决策 |
|---|---|
| 引擎 | SQLite（标准库 `sqlite3`，无外部服务）；启用 `PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`、`PRAGMA foreign_keys=ON` |
| 文件位置 | local/便携版：`<数据目录>/tracelens.db`（便携版落在解压目录，不写注册表）；团队模式：容器卷 `/data/tracelens.db` |
| 版本 | `PRAGMA user_version` = schema 版本整数；表 `meta(key,value)` 存 `schema_version`、`installed_at`、`mode` |
| 迁移 | `agent_explainer/store/migrations/NNNN_简述.sql`（`0001_init.sql` 建全部表与索引，`0002_*.sql` 起增量）；启动时 `store.migrations.apply(db)` 顺序执行未应用的脚本，**每支脚本单事务**，失败回滚并拒绝启动（界面显示可读原因，不得半迁移） |
| 数据量假设 | 单任务 ≤2000 条动作、团队 ≤30 人（A11）；超量则先做分页与索引复审，不换数据库 |

### 6.2 表（与任务契约逐字对齐：users / roles / sessions / actions / ingest_events / settings）

```sql
-- 0001_init.sql（节选；完整脚本由 t13/t15 落地，字段以本文为准）
CREATE TABLE roles (
  role        TEXT PRIMARY KEY CHECK (role IN ('admin','editor','viewer')),
  label_key   TEXT NOT NULL,            -- i18n key，例如 role.admin
  rights_json TEXT NOT NULL             -- 权限矩阵快照，服务端与前端共用同一份
);

CREATE TABLE users (
  id            TEXT PRIMARY KEY,                 -- u-1001（= ActionRecord.actor_id）
  username      TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL REFERENCES roles(role),
  pwk_hash      TEXT NOT NULL,                    -- PBKDF2-HMAC-SHA256，≥210k 迭代，盐 16B
  pwk_params    TEXT NOT NULL,                    -- {"algo":"pbkdf2_sha256","iterations":210000,"salt":"..."}
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  must_change_pw INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,                    -- 以下时间列统一 RFC3339 带时区
  last_login_at TEXT,
  failed_count  INTEGER NOT NULL DEFAULT 0,       -- 连续失败次数（限速）
  locked_until  TEXT
);

CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,                 -- = ActionRecord.session_id
  workspace_id  TEXT NOT NULL DEFAULT 'default',
  actor_id      TEXT NOT NULL,                    -- 归属用户（local 模式 = 'local'）
  agent_id      TEXT NOT NULL,
  task_name     TEXT,
  audience_profile TEXT CHECK (audience_profile IN ('A','B','C')),
  started_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  action_count  INTEGER NOT NULL DEFAULT 0,
  needs_me_count INTEGER NOT NULL DEFAULT 0,      -- 冗余计数：首屏徽标 0 次扫描
  is_demo       INTEGER NOT NULL DEFAULT 0,       -- A2 徽标（会话级）
  FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE actions (
  id                TEXT PRIMARY KEY,              -- = ActionRecord.id（幂等键）
  schema_version    TEXT NOT NULL,
  workspace_id      TEXT NOT NULL,
  actor_id          TEXT NOT NULL,
  agent_id          TEXT NOT NULL,
  session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  run_id            TEXT,
  parent_action_id  TEXT REFERENCES actions(id) ON DELETE SET NULL,
  depth             INTEGER,
  sequence_no       INTEGER,
  timestamp         TEXT NOT NULL,                 -- 'YYYY-MM-DDTHH:MM:SS+08:00'
  received_at       TEXT,
  latency_ms        INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
  action_type       TEXT NOT NULL,
  status            TEXT NOT NULL,
  requires_action   INTEGER NOT NULL DEFAULT 0,
  risk_level        TEXT NOT NULL DEFAULT 'low',
  demo_data         INTEGER NOT NULL DEFAULT 0,
  synthetic         INTEGER NOT NULL DEFAULT 1,
  -- 规范化出来的"热字段"（列表/筛选/排序只读这些，不回 JSON 大字段）
  l1_zh             TEXT, l1_en TEXT,              -- 双语 L1 平铺：切语言不重渲染
  intent_preview    TEXT,                          -- ≤120 字
  tool_name         TEXT,
  cost_amount       REAL, cost_currency TEXT,
  source_kind       TEXT,
  due_at            TEXT,
  decision          TEXT,                          -- approve/modify/reject（决定段快查）
  decided_by        TEXT, decided_at TEXT,
  exec_state        TEXT,                          -- execution_receipt.state 平铺
  facts_hash        TEXT,                          -- source_facts_hash 平铺，供审计比对
  record_json       TEXT NOT NULL,                 -- 完整 ActionRecord（唯一事实源）
  raw_ref           TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE TABLE action_labels (action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
                             label TEXT NOT NULL, PRIMARY KEY (action_id, label));

CREATE TABLE ingest_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key    TEXT NOT NULL UNIQUE,              -- sha256(source.kind + source_uri + 载荷指纹 或 record id)
  source_kind   TEXT NOT NULL,                     -- demo/paste_json/upload/webhook/sdk/file_watch/manual
  adapter       TEXT NOT NULL,
  workspace_id  TEXT NOT NULL,
  actor_id      TEXT,
  request_id    TEXT,
  record_count  INTEGER NOT NULL DEFAULT 0,        -- 一次事件产出多少条记录
  accepted      INTEGER NOT NULL DEFAULT 1,
  error_code    TEXT,                              -- 失败时的统一错误码（不含载荷明文）
  payload_ref   TEXT,                              -- 指针；日志与列表接口不得回显载荷
  received_at   TEXT NOT NULL,
  duration_ms   INTEGER
);

CREATE TABLE settings (
  key         TEXT PRIMARY KEY,                    -- llm.mode / llm.base_url / llm.model / ui.language / retention.days / ingest.token_hash ...
  value_json  TEXT NOT NULL,                       -- 明文值（仅非敏感项）
  secret_enc  BLOB,                                -- 敏感项：DPAPI 密文（桌面）；团队模式禁止落库，只允许环境变量
  secret_kind TEXT CHECK (secret_kind IN ('none','dpapi','env')),
  updated_at  TEXT NOT NULL,
  updated_by  TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- schema_version / installed_at / mode / install_id
```

**为什么把少量字段"平铺"成列**（`l1_zh/l1_en/status/exec_state/…`）：首屏 1000 条要在 1.5 秒内渲染，解析 1000 个 `record_json` 不可行。规则是**列 = 缓存，`record_json` = 唯一事实源**；`store` 层在同一事务里写两者，并保证列值可由 `record_json` 重建（校验脚本 `store.verify_columns(db)` 逐条比对，t10 纳入回归）。

### 6.3 索引（支撑性能基线）

| 索引 | 定义 | 服务的查询 |
|---|---|---|
| `idx_actions_needs` | `CREATE INDEX ... ON actions(workspace_id, due_at, timestamp) WHERE requires_action=1 AND status IN ('pending_confirmation','expired')` | "需要我"区（部分索引，只装待办，常数级） |
| `idx_actions_list` | `ON actions(workspace_id, status, timestamp DESC)` | 三区列表 + 状态筛选 |
| `idx_actions_session` | `ON actions(session_id, sequence_no)` | 时间轴回放 |
| `idx_actions_parent` | `ON actions(parent_action_id)` | 层级展开 |
| `idx_actions_risk` | `ON actions(workspace_id, risk_level, timestamp)` | 高危过滤、影响面预览列表 |
| `idx_actions_demo` | `ON actions(demo_data, synthetic)` | 演示徽标与导出页脚判定 |
| `idx_labels` | `ON action_labels(label, action_id)` | 标签筛选 |
| `idx_ingest_dedupe` | `ingest_events.dedupe_key` UNIQUE（建表即唯一） | webhook 幂等 |
| `idx_ingest_time` | `ON ingest_events(received_at)` | 接入健康度 |
| FTS5（可选） | `CREATE VIRTUAL TABLE actions_fts USING fts5(l1_zh, l1_en, intent_preview, content=actions)` | 关键字搜索；标准库 `sqlite3` 多数构建已带 FTS5，无则降级 `LIKE` 并在界面注明（t10 用例覆盖两条路径） |

### 6.4 审计 JSONL 哈希链（业务表与审计分离，append-only）

| 项 | 决策 |
|---|---|
| 文件 | `<数据目录>/audit/audit-YYYYMMDD.jsonl`（按天分片，一行一条） |
| 条目字段 | `seq`（自增）、`ts`（带时区）、`event`（`intervention.decision` / `intervention.execution_receipt` / `intervention.overdue_reminder` / `record.needs_decision` / `auth.login` / `auth.logout` / `auth.login_failed` / `settings.change` / `settings.llm_test` / `data.purge` / `export.csv` / `ingest.reject`）、`actor_id`、`role`、`workspace_id`、`action_id`、`decision`、`original_params_redacted`、`modified_params_redacted`、`reason`、`request_id`、`client{ip_hash, ua_hash}`、`demo_data`、`prev_hash`、`hash`、`algo="sha256"` |
| 哈希算法 | `hash = sha256(canonical_json(entry 去掉 hash 字段))`；`canonical_json` = 键排序 + 紧凑分隔符 + 保留中文（与 `tools/facts_hash.py` 同一实现，`store/audit.py` 复用） |
| 链 | 首条 `prev_hash = "sha256:0000…"`（创世）；每条 `prev_hash` = 上一条 `hash`；跨文件时首条 `prev_hash` 取上一分片末条 `hash` |
| 校验 | `python -m agent_explainer.audit verify` → 逐条重算 + 链连续性；**任意改动 1 条必须报错**（K-09，t9 用例） |
| 写入纪律 | 只 append：运行账号对该目录**无 UPDATE/DELETE 能力**（SQLite 连接执行 `DELETE FROM` 类语句在代码层禁用；文件目录在团队容器里以只追加方式挂载，README 说明卷权限） |
| 脱敏 | `*_redacted` 字段只写**字段名 + 类型 + 摘要指纹**，不写参数明文；`client.ip_hash` = sha256(ip + 安装随机盐)；UA 只写哈希 |
| 与业务表 | `actions.user_intervention.audit_entry_hash` ↔ 审计条目 `hash` 双向可追（记录→审计、审计→记录） |

### 6.5 5.0 新增实体存储（`feature_store.py`，默认根 `<数据目录>/features/`）

设计基线见 [TL-SEC-005 §3.3](./07-secretariat-and-extensions.md)。**默认根**由
`feature_store.default_feature_root()` 给出 = `<仓库>/var/features`；测试一律用
`tempfile.TemporaryDirectory()`，**绝不写仓库 `var/`**。这些集合与 `store.py` 的
`RecordJournal`（ActionRecord 的唯一事实源）、`audit.py` 的审计链（取证用、只记"谁做了什么
决定"）**三套分开**，不互相塞。

| 实体 | 文件（相对 `features/`） | 类型 | 键 / 字段要点 |
|---|---|---|---|
| 身份注册表 | `secretariat/identities.jsonl` | **Ledger** | 主键 `agent_id`；`pubkey` / `fingerprint` / `image_hash` / `boot_params_hash` / `owner_did` / `created_at` / `superseded_by` / `revoked_at` |
| 委托链（**DelegationRecord**） | `secretariat/delegations.jsonl` | **Ledger** | 主键 `id`；`delegator_did` / `delegatee_did` / `scope{action_types[],tools[],max_risk}` / `depth` / `parent_id` / `issued_at` / `expires_at` / `signature` |
| 认定书索引 | `secretariat/letters.jsonl` | **Ledger** | 主键 `id`；`subject` / `created_at` / `records` / `root` / `signature` / `audit_head`；正文落 `secretariat/letters/<id>.json` |
| 私钥保管箱 | `secretariat/keyring.json` | 文件（`0600`） | **绝不进 API / 审计 / 导出**；缺失 → `SECRETARIAT_KEYSTORE_UNAVAILABLE` |
| 任务组（**TaskGroup**） | `groups.jsonl` | Store | 主键 `id`；`title` / `objective` / `created_at` / `status` / `agent_ids[]`（**上限 10**）/ `closed_at` |
| 分享邀请（**ShareInvitation**） | `shares.jsonl` | Store | 主键 `id`；`group_id` / `inviter_did` / `invitee_did` / `permission` / `token_hash` / `expires_at` / `status` / `created_at` / `accepted_at` / `revoked_at`；**明文 token 只返回一次**，落盘的只有 `token_hash` |
| 协作事件 | `collab_events.jsonl` | **Ledger** | 主键 `id`；`actor_did` / `action` / `target` / `at` / `detail` / `signature` |
| 改进建议（**ImprovementSuggestion**） | `suggestions.jsonl` | Store | 主键 `id`；`user_id` / `tool` / `param` / `direction` / `from_value` / `to_value` / `occurrences` / `status` / `created_at` / `decided_at` / `cooldown_until` |
| 用户偏好（**UserPreference**） | `preferences.jsonl` | Store | 主键 `id`；`user_id` / `tool` / `param` / `value` / `source(manual\|suggestion)` / `created_at` / `updated_at` |
| 导出记录（**ExportRecord**） | `exports.jsonl` | **Ledger** | 主键 `id`；`user_id` / `time_range` / `format` / `root_hash` / `ipfs_cid` / `eth_tx_hash` / `anchor_state` / `path` / `bytes` / `sha256` / `chain_ok` / `created_at` |
| 证据索引 | `evidence/index.jsonl` | Store | 主键 `id`；`record_id` / `kind` / `media_type` / `sha256` / `bytes` / `caption` / `created_at` / `feedback`；本体落 `evidence/files/<id>.<ext>` |
| 撤销日志 | `rollback/journal.jsonl` | Store | 主键 `id`；`action_id` / `tool` / `inverse_op` / `snapshot` / `created_at` / `applied_at` / `result`；`snapshot` 是"回滚后状态 == 回滚前"的比对基准 |
| 存证锚点 | `anchors.jsonl` | **Ledger** | 主键 `id`；`export_id` / `cid` / `state` / `anchor` / `at`；`state` 默认 `unanchored`（离线降级，见 §2.8/§10） |

**账本纪律（`Ledger` 与 `Store` 都不允许删行）**：

1. **Ledger**（身份 / 委托 / 认定书索引 / 协作事件 / 导出记录 / 锚点）：每次追加都写 `seq`、
   `prev_hash`、`entry_hash`；`entry_hash = sha256(canonical_json(entry 去掉 entry_hash))`，
   `prev_hash` = 上一条的 `entry_hash`，首条 = 64 个 `0`（`GENESIS_HASH`）。`verify()` 离线复算
   整条链；**断链只报告、绝不自动清空或重建**（与 §6.4 审计链同一口径）。
2. **Store**（其余实体）：一行一个实体；同 `id` 最后写入胜出（`put`），`append` 只追加；
   跨进程文件锁（Windows `msvcrt` / POSIX `fcntl`，锁旁车 `*.lock`）；压实用临时文件 +
   `fsync` + `os.replace`，不存在"清空后崩在中间"。
3. **不提供 delete**：撤销用状态字段（`revoked_at` / `status` / `closed_at`），不抹行——
   "删号等于毁证"是产品纪律（`auth.py` 只禁用不删除）。
4. 单行上限 `MAX_LINE_BYTES = 256 KiB`；崩溃留下的半行只坏一行，不拖垮整个集合。
5. **用户 id**：local 模式为 `local`；team 模式为会话 `actor_id`。分析类输出（仪表盘/导出件）
   里的用户 id 一律用 `sha256(user_id)[:12]`（`analytics.anonymise`），**原始 id 不出现在
   仪表盘与导出里**（功能六"数据脱敏"）。
6. 5.0 的实体**不写 SQLite**：`var/features/` 下一律 JSONL + 文件，随数据目录一起备份/清空。

---

## 7. 查询契约（后端实现口径，前端可照此写请求）

所有列表接口统一参数：`workspace_id`（默认 `default`）、`session_id`、`status`（多值逗号）、`risk_level`、`action_type`、`label`、`q`（关键字）、`demo_only`、`from`/`to`（ISO 时间）、`limit`（默认 50，上限 500）、`cursor`（分页游标 = `timestamp|id`，稳定排序）。
统一响应包裹：`{data, next_cursor, count, server_time, request_id, degraded?}`。

| # | 查询 | 接口 | SQL 形状（要点） | 性能预算 |
|---|---|---|---|---|
| Q1 | 三区首屏（已完成/进行中/需要我） | `GET /api/actions?view=zones&limit=1000` | 三条 `SELECT` 走 `idx_actions_needs` + `idx_actions_list`，只取平铺列 | 1000 条 **<1.5s**（含网络）；`needs_me` 常数级 |
| Q2 | 筛选（状态/风险/类型/标签/时间/关键字） | `GET /api/actions?...&cursor=…` | 复合索引 + 覆盖列；FTS5 或 `LIKE` | **<200ms** |
| Q3 | 单条详情（L2/L3/影响面/原始指针） | `GET /api/actions/{id}` | 取 `record_json` | <80ms |
| Q4 | 时间轴（S3） | `GET /api/traces/{session_id}/timeline?bucket=auto` | 按 `sequence_no` 排序，返回 `{id,l1,timestamp,status,latency_ms}` 摘要 + 层级 | 2000 点 <300ms |
| Q5 | 需要我队列（串行，一次只出一个） | `GET /api/actions?needs_me=1&limit=1` | 部分索引 + `policy.queue_serial` | <50ms |
| Q6 | 干预写入（幂等） | `POST /intervene` | 单事务：更新 `actions` + 追加审计 + 提交后广播 SSE；`idempotency_key` 去重 | <150ms + SSE <1s |
| Q7 | 审计查询/校验 | `GET /api/audit?action_id=&from=&to=` · `GET /api/audit/verify` | 流式读 JSONL，不整文件载入 | verify 10 万条 <20s（t9 口径） |
| Q8 | 导出 CSV | `GET /api/export.csv?scope=interventions&redact=1` | 游标流式写；页脚写"合成演示数据 · 非真实采集"（若含 demo 记录） | **≤30 秒**（K-09） |
| Q9 | SSE 事件流 | `GET /api/stream?session_id=&workspace_id=` | 事件类型：`action.created` / `action.needs_decision` / `action.decision_recorded` / `action.execution_receipt` / `action.overdue` / `ingest.rejected` / `settings.changed` / `heartbeat`(15s) | 端到端 **<1s** |
| Q10 | 健康检查 | `GET /api/health` | `{status:"ok",version,schema_version,mode,db:"ok",db_size,audit:"ok",uptime_s}` | 团队模式 `compose up` 判据（M2） |

**实况（4.6 / t19 与 `server.py` 逐条对照；QA 的 5.1 用例按这一张表写，不要按上面的规划路径写）**

| # | 现状接口（代码实测） | 说明 |
|---|---|---|
| Q1 / Q2 / Q5 | `GET /api/records` | 服务端**不做**三区/筛选/关键字：`/api/actions` 未实现，三区分类与搜索在前端（t7）计算；性能预算（首屏 1000 条 <1.5s、筛选 <200ms）测这条 |
| Q3 | `GET /api/records/{id}` | `/api/actions/{id}` 未实现（404 `ROUTE_NOT_FOUND`） |
| Q4 | `GET /api/records` + 前端时间轴 | `/api/traces/{session_id}/timeline` 未实现；层级/顺序由前端按 `sequence_no`/`trace_link` 组装 |
| Q6 | `POST /intervene` | ✅ 与契约一致（幂等 `Idempotency-Key`、单事务写记录 + 追加审计、提交后广播 SSE、重渲染 L1） |
| Q7 | `GET /api/audit`（过滤 `limit,offset,reverse,event,actor_id,action_id,decision,task_id,q`） | **`/api/audit/verify` 未实现**：链校验随响应 `chain` 块与 `/health` 的 `audit` 块返回，命令行走 `python -m agent_explainer.audit`；断链 → 500 `AUDIT_CHAIN_BROKEN` |
| Q8 | 审计导出 = **`GET /api/audit/export?format=csv\|html\|pdf`**（**4.8 / t21 已实现**，默认 `csv`，`limit` 默认 500 钳到 5000，过滤与 Q7 同批键 `actor_id/action_id/decision/event/task_id/q/since/until`）；Python API 保留给 CLI/测试：`audit.export_csv()/export_html()/export_pdf()`；**记录**导出 = `GET /api/records` | **`/api/export.csv` 这个路径仍未实现**（GET → `STATIC_NOT_FOUND`）；脱敏是**写盘前强制**（13 类 PII → `<redacted:类型#sha256:前12位>`，**没有 `?redact=1` 参数**）；链断 → 500 `AUDIT_CHAIN_BROKEN`（**断链不出证据**）；每次导出记审计 `event="export"`（内存链名 `audit.export`，只记格式/条数/字节/过滤摘要/`sha256` 指纹，**不含原文与密钥**）；"合成演示数据"页脚**尚未实现**（见 03 §13 的未交付项）；契约细节见 03 §6.3 M6 |
| Q9 | `GET /api/stream`（必须 `Accept: text/event-stream`） | 事件类型**实况 8 个**：`hello`、`heartbeat`、`action.created`、`action.updated`、`action.decision_recorded`、`records.reset`、`ingest.batch`、`ingest.stats`；旧名 `action.needs_decision`/`action.execution_receipt`/`action.overdue`/`ingest.rejected`/`settings.changed` **未使用** |
| Q10 | `GET /health`（别名 `/healthz`） | 无 `/api/health`；字段逐条见 [TL-ARCH-003 §6.6](./03-architecture.md)，**没有 `db`/`db_size`/`ready`/`uptime_s`**；断链时 `audit.ok=false` 且 `status=degraded`（HTTP 仍 200） |
| 新增（4.4） | `POST /api/ingest/events`、`POST /api/ingest/tokens`（+`/{id}/revoke`）、`GET /api/ingest/status` | 上报与令牌：合并白名单 22 字段 / 保护 9 字段（**上报永不覆盖决定与文案**）、幂等 900s、限流 40/600、宽容模式 `stats.skip_reasons`；契约见 03 §6.3 E1/E3/E4/E5 |
| 新增（4.5） | `GET/PUT /api/settings`、`POST /api/settings/test-llm`、`POST /api/reset` | 设置中心（掩码 `••••••`+末 4 位、`api_key_source`、probe-only 探测、清空写 `record_reset`）；契约见 03 §6.3 J1/J2/S1 |

> 本节与 §6 的 SQL DDL（六表、`settings.secret_kind`）与审计 `event` 枚举属**设计基线**：实况是 JSON 记录存储 + `var/settings.json`（密钥字段名 `llm.api_key_source ∈ {dpapi,env,memory,none}`），审计事件白名单见 03 §4（`decision`/`correction`/`seal`/`export`/`access_denied`/`note`/`record_reset` + `auth.*`；设置与上报以 `note` + `settings_*` / `extra.ingest_event` 记账）。差异全表见 03 §6.5，**以代码为准**。

**降级口径（实况，4.4 已落地）**：SSE 断线 → 带 `Last-Event-ID`（或 `?since=`）自动重连，服务端从**环形 200 帧**补发；前端退避 800ms→上限 12000ms、最多 **4 次**；之后降级为**轮询 `GET /api/records`，默认 4000ms**，顶栏徽标写明降级原因（i18n `realtime.mode.*`，8 键双语）。旧口径"3 次失败 → 2 秒轮询 `GET /api/actions?since=`"**作废**（该路径不存在）。API 不可用时前端本地模拟干预并显式提示"未写入审计"（t1 9.2 对 t8 的要求）。

---

## 8. 与 1.1 产品定义的映射（逐条对得上，不做"顺手扩展"）

### 8.1 字段需求 → 落地位置（t1 第 9.1 节逐行核对）

| t1 要求的字段能力 | 本模型落地 | 状态 |
|---|---|---|
| `id/session_id/parent_action_id` | 同名字段 + `depth`/`sequence_no` 冗余 | ✅ |
| `actor_id/workspace_id` | 同名字段，**必填非空**；SQLite 全表带这两列 | ✅ |
| `timestamp/latency_ms/cost` | 同名 + `received_at`（管道延迟度量） | ✅ |
| `agent_id/action_type/tool_used` | 同名；`action_type` 17 类覆盖 t1 点名的 10 类 | ✅ |
| `intent/rationale/result_summary/human_readable` | 同名；`human_readable` 为带指纹的结构化对象 | ✅ |
| `confidence` | 同名 + `uncertainty`（C7 低置信透出） | ✅ |
| `status` 状态机 | 三段状态机（第 4 节），比 t1 建议的链路多出 `observed/expired` 与执行回执分离 | ✅（增强，见第 11 节决策 D3） |
| `requires_action/intervention_options[带 consequence]` | 同名；C1 强制三选项 + 后果文案 | ✅ |
| `user_intervention`（决策+改前改后+原因+时间+人） | 同名对象，含 `recorded=true` 与审计指纹指针 | ✅ |
| `impact_scope`（文件/表/金额/外部接口） | 同名对象，含 `data_categories` 与 `reversible` | ✅ |
| `error.code/human_reason/suggested_next` | 同名（`error` 对象 + `suggested_next[]`） | ✅ |
| `raw_ref` | 同名，指针形态 | ✅ |
| 审计事件字段 + `prev_hash/hash` | 第 6.4 节 JSONL 哈希链 | ✅ |

### 8.2 功能与指标 → 数据支撑

| 1.1 项 | 数据支撑点 |
|---|---|
| M1 桌面/便携 | `settings` 便携数据目录；`meta.mode=local`；无登录表依赖 |
| M2 Docker 团队 | `users/roles` + `workspace_id` 隔离；`/api/health`（Q10） |
| M3 演示/粘贴/上传 | `source.kind` 三类 + `demo_data`/`synthetic` + `ingest_events` 幂等与失败码 |
| M4 翻译层 + 双语 | `human_readable`(L1/L2/L3 + `language` + 指纹) + 平铺 `l1_zh/l1_en` + `action_type` |
| M5 模型配置页 | `settings`（`secret_kind=dpapi/env`，明文不落库）；`generated_by/model` |
| M6 登录角色 + 干预 + 审计 | `users/roles` + 决定段字段 + 审计哈希链 + Q8 导出 |
| S1 失败解释 | `error.human_reason` + `suggested_next[]` + `retryable` |
| S3 时间轴 | `session_id/sequence_no/parent_action_id/depth` + Q4 |
| S4 影响面预览 | `impact_scope` + C9/C10 + Q3 |
| K-04 性能 | 平铺列 + 6.3 索引 + Q1/Q2 预算 + `received_at-timestamp` |
| K-05/K-06 可理解性 | `human_readable.l1/l2` 长度硬约束 + 校验脚本度量 |
| K-07/K-08 干预 | 迁移表（4.2）+ `request_id` + 审计比对 |
| K-09 可证明性 | 哈希链 + Q7 verify + Q8 导出 ≤30s |
| K-10 双语 | `language` 必填 + `l1_zh/l1_en` + i18n key（第 7 节架构文档） |
| K-11 权限 | `role` + `policy.min_role_to_decide` + `decided_by_role` |
| K-12 无 key 可用 | `generated_by=template` 默认；`cost` 可空 |
| K-13/K-14 主观指标 | 不入库（问卷在 t10/t11），数据层只提供干预耗时/成功率埋点 |

### 8.3 断层与旅程 → 字段

| 断层/触点 | 支撑 |
|---|---|
| D1 看不见 | `status/running` + Q1 三区 + SSE `action.created` |
| D2 看不懂 | `human_readable` 四要素 + 降级文案规则（`rationale=null` → "来源未提供原因"） |
| D3 插不上手 | `requires_action_reason` + 三选项 + `consequence` + `editable_fields` |
| D4 说不清 | 审计哈希链 + `decided_by/decided_at` + 导出 |
| T5 情绪锚点 | "为什么需要你"字段 + 每选项后果 + 超时挂起文案 |
| T6 出岔子 | `error.human_reason` + `suggested_next` |
| T7 事后交代 | Q7/Q8 + 哈希链校验说明 + 页脚合成标注 |

### 8.4 给 t3 的演示/夹具规格（写死，避免 t3 再问）

| 要求 | 规格 |
|---|---|
| 原生演示集 | `samples/action_records.json` ≥12 条，`action_type` 覆盖 ≥10 类（枚举 16 类里至少 10 类各 ≥1）；全部 `demo_data=true, synthetic=true, source.kind=demo` |
| 必须包含 | ≥3 条 `requires_action=true` 且三选项带后果；≥1 条 `failed`（`error.human_reason` + `suggested_next`）；≥1 条 `high/critical` 且带 `impact_scope`（含 `reversible=false` 的不可逆项）；≥1 条 `modified` 决定 + `executed` 回执；≥1 条 `rejected` + `refused_by_runtime`；≥1 条 `expired`；≥1 条 `confidence≤0.6` + `uncertainty`；≥1 条含 `data_categories:["personal_data"]`（脱敏导出演示） |
| 双语 | 每条记录提供 zh 渲染；`samples/action_records_en.json` 提供 en 原生模板渲染（不是中文机翻） |
| 外部格式夹具 | `samples/langfuse_trace.json` / `otel_trace.json` / `mcp_session.json` 各 6–10 条；文件头与 `samples/README.md` 写明"合成示例，非真实采集"+ 所依据规范版本（`source.format_version`） |
| 校验 | 演示集与夹具必须通过 `python tools/validate_schema.py` 同等校验（t3 的测试脚本复用本 schema 与 `facts_hash`）；隐私 lint 必须零命中 |

---

## 9. 保留、清空与脱敏（隐私即字段）

| 主题 | 决策 |
|---|---|
| 数据去向 | 只存本机 / 自建卷；**没有任何云端上传**；LLM 调用由用户自己配的 baseURL 决定，界面在启用 LLM 时显式提示"这一步会把 L1 摘要发往你配置的地址" |
| 保留期 | 默认永久保留（Q4）；`settings.retention.days`（0=永久）可开自动清理，清理本身写审计 `data.purge` |
| 一键清空 | `POST /api/settings/purge {scope:"actions"\|"audit"\|"all", confirm:"DELETE"}`；仅 local 与 team-admin；实现 = 按 `workspace_id` 批量删 `actions/sessions/ingest_events` + 归档清空审计目录（**先导出 `.purged-<ts>.tar.gz` 到数据目录**，再删，并写审计） |
| 导出脱敏 | **实况（4.8 / t21，以代码为准）**：设计里的 `GET /api/export.csv?redact=1` 是**规划形状**（该路径未实现，真实入口 = `GET /api/audit/export`）。**脱敏不在导出时、也没有开关**：`audit.py` 的 `redact_text()` 在**写盘前**跑 `PII_PATTERNS` **13 类**（邮箱 / 手机号 / 身份证 / 11–20 位长数字 / IPv4 / IPv6 / Windows 与 POSIX 绝对路径 / `Bearer …` / `key=value` 型密钥 / `sk-`·`sk_` 密钥 / AWS `AKIA…` / PEM 私钥块），替换为 **`<redacted:类型#sha256:前12位>`**（同值同指纹 → 仍可对齐取证，正文无原值；**幂等**，已脱敏值不被二次改写）。作用字段：`reason`/`note`/`changed_fields`/参数与敏感键名；**落盘 JSONL、`GET /api/audit`、CSV/HTML/PDF 三处内容一致**。设计里"`<path:1>`/`<amount>` 金额占位 + 计数与指纹"的意图由"类型 + 指纹"实现（金额类只保留指纹，不保留数额） |
| 日志脱敏（硬规则） | 日志行只允许：`request_id`、`code`、耗时、计数、ID。**禁止**出现：trace 原文、参数明文、API key、口令、cookie、完整 UA、真实文件路径。由 `store/logging/redact.py` 统一过滤 + `tools/` 日志 lint 测试（t10 纳入） |
| 指标埋点 | t1 7.3 的埋点写 `metrics` 表（`store/metrics.py`，与审计分离，同样禁含 trace 原文/key），本机计算、一键清空覆盖 |

---

## 10. 验收自检（本文 + schema 的实际执行结果）

| 任务验收项 | 结果 | 证据 |
|---|---|---|
| schema 文件存在且为 draft 2020-12，含 `actor_id`、`workspace_id` | ✅ | [action_record.schema.json](./schemas/action_record.schema.json)（`$schema` 为 2020-12；两字段均必填非空） |
| schema **通过实际校验脚本** | ✅ | `python tools/validate_schema.py` → 50 项检查全部通过，退出码 0；报告 [validation-report.json](./schemas/validation-report.json) |
| 正向示例 ≥3 条 | ✅ 6 条 | 待确认高危删除 / 失败 / 低置信 LLM / 改参数放行+已执行 / 拒绝+运行时中止 / 超时挂起 |
| 失败示例 ≥1 条 | ✅ 39 条 | 每条含 `breaks_constraint`、`expect_keywords`、`expected_fix`；两引擎均拒绝且命中预期关键字 |
| 事实可重算（P3 可验证） | ✅ | 6 条示例 `source_facts_hash` 与 `tools/facts_hash.py` 重算一致（`--fix-facts` 写回后 0 差异） |
| 约束真的生效 | ✅ | 15 条条件约束 + 结构约束全部有对应负向用例（第 5 节对照表） |
| 离线可判定（依赖政策） | ✅ | `tools/test_offline_engine.py` 屏蔽 `jsonschema` 后仍 50/50 通过 |
| 与 1.1 映射无遗漏 | ✅ | 8.1 逐行（13/13）、8.2 M1–M6 + S1/S3/S4、8.3 断层与触点、K-04/05/07/08/09/10/11/12 全覆盖 |
| 干预语义按 Q2 裁决落地 | ✅ | 4.2 迁移表 + 4.3 三条纪律 + C11/C13/C14 + `recorded` 常量 + `effect_notice` 文案 key |
| 双语（Must）落地 | ✅ | `human_readable.language` 必填、zh/en 原生模板、平铺 `l1_zh/l1_en`、N38 负向用例 |
| **5.0** schema 增量 | ✅ | 21 个可选字段 + `action_type.rollback`；`required` 27 / `allOf` 15 / `schema_version` 1.1 **均未变**；`python tools/validate_schema.py` 仍 **50 项全通过**（正向 6 / 负向 39） |
| **5.0** 新字段约束真的生效 | ✅ | 负向探针：非法 DID（`did:bad`）、带 `sha256:` 前缀的 `entry_hash`、`rollback_state="reverted"`、非 base64 的 `agent_pubkey` 均被拒（`pattern` / `minLength` / `enum`） |
| **5.0** 诚实边界写进数据契约 | ✅ | `tsa_timestamp` = `local-tsa:…` 且验签标 `local_only`；`rollback_state` 说明回执是 `simulated_local`；`anchor_state` 默认 `unanchored`（§2.8 / §6.5） |

---

## 11. 架构决策记录（本任务新增，供队长复核）

| 编号 | 决策 | 理由 | 代价 |
|---|---|---|---|
| D1 | 决定段与执行段**分成两个字段**（`user_intervention` vs `execution_receipt`），执行结果不进 `status` 枚举 | 队长 Q2 裁决：决定 ≠ 执行；混写会造成"AI 已被停下"的假承诺 | 前端需要两块文案；SDK 未接入时界面要显示"执行端未回执"（诚实但不够漂亮） |
| D2 | 事实指纹覆盖 `status` 等字段 | 干预后卡片话术必然变化（"需要您确认"→"已按您改的参数执行"），指纹必须反映新事实 | 状态变化要触发重新渲染（store 层同事务完成） |
| D3 | `status` 增加 `observed`（无需人的普通动作）与 `expired`（超时挂起） | t1 的链路只描述了确认链路；实际绝大多数动作不需要人，且超时必须"仍在等人"（W-02） | 枚举多 2 个值，前端映射表加 2 行（第 3.2 节已给） |
| D4 | `cost` 出现即要求 `currency+amount+estimated` | 缺单位的金额无法渲染成"约 ¥0.02"，会退化成噪音 | 来源不含成本时整对象写 `null`（已有示例 E6） |
| D5 | 列表用平铺列 + `record_json` 为唯一事实源 | 1.5s 首屏与 200ms 筛选预算下，不能逐条解析 JSON | 需要 `store.verify_columns` 一致性校验防漂移 |
| D6 | 审计走 JSONL 哈希链，业务表在 SQLite | append-only + 可验证篡改，SQLite 行更新做不到"不可改" | 审计查询要流式读，不做全表 SQL |
| D7 | `workspace_id` v1 固定 `default` 但**必填落库** | Q3 裁决：单空间共享；字段先占位，避免团队模式上线后改表 | 多空间能力延后（v1.x） |
| D8 | 校验器自带纯标准库实现 | 依赖政策 + 离线机器必须能判定 schema | 只支持关键字子集（文件头列出），新增关键字需同步实现 |
| D9 | 5.0 的 21 个字段**全部可选**、`schema_version` **不升版**（仍 1.1） | 5.0 是加法：旧记录/旧适配器/旧夹具零改动；"未启用"有 `null`/缺席两种合法表达 | schema 无法强制"新能力必须启用"，只能保证形状；`required` 不变意味着新字段缺了也不能被 schema 拦住 |
| D10 | "`confidence < 0.5` → 触发干预"**不写进 schema**，只写在 `confidence.apply_to_record()` | 现有正向夹具 `act-0004`（0.45 / `requires_action=false`）会立刻被硬约束打死，等于用"可验证性"换"看起来更严" | schema 单独不能证明策略，必须靠 `tests/test_expansion_acceptance.py` 的断言；文档与代码两处都要说明 |
| D11 | 证据条目 / 干预历史 / 改进建议摘要等模块自定形状的对象，schema 只约束到"是对象 + 少数关键键" | 并行开发期把模块内部字段抄进数据契约，等于让"实现改一个键"就变成"契约破坏" | 这些对象内部的拼写错误 schema 抓不到，靠模块单测与 `test_expansion_acceptance.py` 兜 |

---

## 12. 开放问题（不阻塞下游，按默认值即可开工）

| 编号 | 问题 | 本文默认（已按此实现） | 何时需要决定 |
|---|---|---|---|
| DM-1 | `metrics` 埋点表是否纳入 t2 的 6 张表清单 | 不纳入：埋点与审计分离，由 t15/t8 写 `metrics` 表（9 节） | t15 开工前 |
| DM-2 | 审计分片粒度（按天 vs 按大小） | 按天；单日 >50MB 时按 10 万行切片 | t9 开工前 |
| DM-3 | `impact_scope.targets` 上限 200 是否够（批量 10 万文件的场景） | 够：超过 200 项时写 `count` 聚合 + 前 20 项样本 + `summary` | t4 翻译模板（"共 12,345 个文件，示例 20 个"） |
| DM-4 | FTS5 不可用时的搜索体验 | 降级 `LIKE` 并在界面注明"关键字搜索为简化模式" | t7 界面 |
