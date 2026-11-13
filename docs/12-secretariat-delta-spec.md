# 总裁办 · 增量规格（R1 交付物）

> 状态：**R1 已冻结并落地**（R2/R3/R4 已按本文件实现；证据见交付报告）。本文是 [docs/07](07-secretariat-and-extensions.md)
> 的**增量与偏差修正**，不替代它：docs/07 说"必须做成什么样"，本文说
> "本机现有实现离它差在哪、按什么口径补齐、怎么验证算补齐"。
>
> 编号提醒：本文的 **R1–R4 是"总裁办序列"**，与 `docs/09-subtask-plan.md` 的 R1–R12、
> 规格书第六章的 R1–R12 **不是同一套编号**。
>
> 事实来源（2026-11-11 本机实测，非估计）：
> `secretariat.py`、`crypto_sig.py`、`feature_store.py`、`action_record.schema.json`、
> `docs/02 §2.8`、`docs/07 §2–§5/§8/§10`、`docs/09`、`server.py`、`web/app.js`、
> `web/ext/*`、`web/i18n/parts/secretariat.*`、`tools/gen_api_contract.py`。

---

## 0. 一页结论

**已经做完、不许重写的部分**

| 能力 | 位置 | 实测 |
| --- | --- | --- |
| Ed25519 + 规范 JSON + DID 派生 | `crypto_sig.py` | 自检 `rfc8032_test1: true` |
| 身份签发/轮换/吊销 + append-only 账本 + keyring(0600) | `secretariat.py:381-470` | 40 条单测覆盖 |
| 行为签名 + 逐 Agent 哈希链 + 逐项复算 | `secretariat.py:503-672` | 同上 |
| 委托（收窄、深度 5、过期） | `secretariat.py:702-823` | 同上 |
| 责任认定书 + 离线验签 | `secretariat.py:825-960` | 同上 |
| 状态汇聚（含三条账本链校验） | `secretariat.py:962-989` | 同上 |

**必须补齐的 6 个缺口（本文冻结其口径）**

1. **已签名记录过不了自家 schema**：签名器写出 schema 未声明字段（`tool`、`approval.policy`、
   `approval.local_only`、审批条目 `risk_level/required/fingerprint`）。实测一条合法样例签名后
   触发 8 条 schema 错误，其中 **6 条由签名器造成**。
2. **责任链链尾写的是原始 `agent_id`，不是 DID**：实测 `agent_id="demo-photo-bot"` 签名后
   `delegation_chain[1]` 不匹配 DID pattern；且 `setdefault` 允许入参覆盖 `human_owner`/链，
   导入方可替别人派责任。
3. **审批背书不可信**：同一 DID 传两次即满足 high/critical 的"双人"；审批签名不含记录绑定，
   同一人同一时钟跨记录签名逐字节相同；`verify_record` 只数个数、不验审批签名。
4. **接口层不存在**：T1–T10 常量齐全、分发分支为零 → `/api/secretariat/*` 全部落到静态 404。
5. **面板是占位**：`web/ext/secretariat.js` 227 B、`secretariat.css` 76 B；但 `index.html`
   已引入、`app.js` 已有 `API_ENDPOINTS.secretariat`、i18n 已有 **138 条**文案。
6. **认定书"可离线验证"被高估**：`actions[]` 只带 `signature_ok` 布尔，验不了逐条签名；
   审批签与身份绑定离线也复算不了。

**R2–R4 的分工**：R2 修 2、R3 修 1/2/3，R4 修 4/5/6 并接线；R1 只负责把口径冻结成下面 8 张表。

---

## 1. 冻结表 A：Signed ActionRecord 字段口径

判定优先级：**schema 是机器事实** > `docs/02 §2.8` 是文档事实 > `secretariat.py` 是实现事实。
三者冲突时，本文写明采用哪一方。

| 字段 | 谁写 | 机器约束（schema 实测） | 验证点 | 现状/动作 |
| --- | --- | --- | --- | --- |
| `schema_version` | 上游 | 常量 `"1.1"`，`required` 27 项不变 | 适配器校验 | 不变 |
| `agent_pubkey` | 签名器 | `string(44)`，pattern `^[A-Za-z0-9+/]{43}=?$` | 等于账本登记公钥；私钥永不出现 | OK |
| `human_owner` | 签名器 | `$defs/did` | **必须等于**账本 `owner_did`；入参自带时**校验而不是保留** | R2/R3 修 |
| `delegation_chain` | 签名器 | `array`，`maxItems: 8`，每项 `$defs/did` | 链根 = `human_owner`；链尾 = 本 Agent 的 DID（`did:tracelens:agent:…`）；长度 = 委托深度 + 1 | R2/R3 修 |
| `input_hash`/`output_hash` | 签名器 | `$defs/hexDigest`（64 位小写 hex，无前缀） | 对 `context.input` / `tool_result`（缺省 `result_summary`）做规范 JSON 后 sha256 复算 | OK |
| `prev_hash` | 签名器 | `hexDigest` | 该 Agent 上一条已签名记录的 `entry_hash`；链根 = `feature_store.GENESIS_HASH`（64 个 `0`） | OK |
| `entry_hash` | 签名器 | `hexDigest` | 覆盖除 `signature`/`tsa_timestamp`/`entry_hash` 外的整条记录；证据/因果字段因此自然进哈希 | OK |
| `signature` | 签名器 | `string`，`maxLength: 128`，base64 字符集 | 对 `SIGNATURE_FIELDS`（见 B 表）验签 | OK |
| `tsa_timestamp` | 签名器 | `string`，`maxLength: 64`，`[A-Za-z0-9:.+-]{4,64}` | `local-tsa:<ISO>` → `verify` 标 `tsa_state="local_only"`；**不得**显示成可信时间戳 | 降级 |
| `approval` | 签名器 | `object`，`required`、`approvers`、`sealed`；`additionalProperties: false`；`required ≤ 2`；`approvers ≤ 4` | 形状见 D 表；多一个键就是违约 | R3 收敛 |
| `risk_level` | 上游/签名器 | enum `low/medium/high/critical` | 签名前**归一化并写回**记录，避免"签的是 high、记录写 low" | R3 修 |
| `tool` | 签名器 | **schema 未声明**（`additionalProperties:false`） | 取自 `tool_used.name`；进入签名域 | **R3 加可选字段**（Q2） |
| 其它 5.0 字段 | 各功能模块 | 见 schema | 本轮不动 | 不变 |

**文档动作**：`docs/02 §2.8` 增补 `tool` 行并把字段数从 20 改为 21；
`docs/07 §3.1` 同步一行。schema 只做**加法**（可选字段），`schema_version` 仍为 `"1.1"`、
`required` 仍 27 项。

---

## 2. 冻结表 B：签名域与规范序列化

`SIGNATURE_FIELDS = (timestamp, agent_id, action_type, tool, input_hash, output_hash,
prev_hash, entry_hash, risk_level, confidence, status)`（`secretariat.py:76`，共 11 域）。

| 域 | 取值来源 | 缺失时 | 备注 |
| --- | --- | --- | --- |
| `timestamp` | 记录事实 | 空串 | 必须带时区 |
| `agent_id` | 记录事实 | 拒绝签名（400） | 身份未登记 → `SECRETARIAT_IDENTITY_MISSING` |
| `action_type` | 记录事实 | 空串 | 必须是枚举值 |
| `tool` | `tool_used.name` → 派生 | 空串 | 非工具动作为空；**由此进签名** |
| `input_hash`/`output_hash` | 签名器复算 | 空串 | 规范 JSON + sha256 |
| `prev_hash`/`entry_hash` | 签名器计算 | — | 见 A 表 |
| `risk_level` | 归一化后 | `"low"` | 签名前写回记录 |
| `confidence` | 记录事实 | `null` | 不把"未测量"写成 1.0 |
| `status` | 记录事实 | 空串 | 记录段状态 |

**序列化规则**：`crypto_sig.signed_payload(record, SIGNATURE_FIELDS)` →
键排序、紧凑分隔符、UTF-8、拒绝 NaN/Inf；**只签这 11 个键**，不签整条记录（否则追加证据/
干预历史会让旧签名永久失效）。`verify_record` 用同一函数复算，禁止第二套序列化。

---

## 3. 冻结表 C：责任链与委托

| 规则 | 口径 | 现状 → 动作 |
| --- | --- | --- |
| 链的构成 | `[]string`，链根是**人**的 DID，链尾是**本 Agent 实例**的 DID；中间是逐层委托的受托人 | 链尾错用 `agent_id` → R2/R3 修为 `did_from_pubkey(pub, kind="agent")` |
| 链长度 | `maxItems: 8`；委托深度上限 `MAX_DELEGATION_DEPTH = 5` → 链最多 6 个 DID，预留 2 格 | 保持一致 |
| 链根校验 | 必须等于身份账本的 `owner_did`；不等 → 400 `SECRETARIAT_FIELD_INVALID`（field=`human_owner`） | 现在 `setdefault` 不校验 → R2 修 |
| 链尾校验 | 必须等于本 Agent 的 DID；不等 → 400 `SECRETARIAT_DELEGATION_INVALID` | R2 修 |
| 入参伪造 | 调用方自带 `human_owner`/`delegation_chain` 时**校验后采纳**，不合法直接拒；不再"原样保留" | R2/R3 修 |
| 委托收窄 | 子 scope 必须是父 scope 子集（`action_types`/`tools` 子集、`max_risk` 不升档），否则 409 `SECRETARIAT_SCOPE_WIDENED` | 已实现 |
| 委托深度 | `depth > 5` → 409 `SECRETARIAT_DEPTH_EXCEEDED` | 已实现 |
| 委托过期 | 过期后 `authorise()` 返回 `{allowed:false, reason:"SECRETARIAT_DELEGATION_EXPIRED"}` | 已实现 |
| 委托人/受托人合法性 | 委托人与受托人都必须是合法 DID；委托人还必须是**已登记**的 DID（人或 Agent） | 目前只做非空 → R2 修 |
| DID 形状 | `^did:tracelens:(user|agent):[a-z0-9]{8,64}$`（与 schema `$defs/did` 一致） | R2 新增校验函数 |

---

## 4. 冻结表 D：风险分层与审批策略

与 `RISK_POLICY`（`secretariat.py:81`）逐字一致；`approval.required` 是 schema 允许的唯一
"审批数量"来源。

| 风险 | 审批数 | 盖章 | 例子 | 硬约束 |
| --- | --- | --- | --- | --- |
| `low` | 0 | 否 | 读日历、查天气 | Agent 自签 |
| `medium` | 0 | 否 | 写笔记、改草稿 | Agent 自签 + 审计条目 |
| `high` | 1 | 否 | 支付、发邮件、删文件 | 1 个**可验证**的人工审批签 |
| `critical` | 2 | 是 | 转账超限、对外合同、删生产数据 | **两个不同 user DID**（裁决 D8）+ 总裁办盖章 |

**审批对象形状**（以 `docs/02 §2.8` 为准，`additionalProperties: false`）：

```jsonc
"approval": {
  "required": 1,                       // 0/1/2
  "approvers": [                       // ≤4；只允许 did / role / signed_at / signature
    {"did": "did:tracelens:user:…", "role": "approver",
     "signed_at": "2026-11-11T10:00:00Z", "signature": "base64…"}
  ],
  "sealed": false                      // critical 才要求 true
  // "seal": {...} 可选，仅 critical 盖章后出现
}
```

**审批签名 payload（R1 冻结，R3 落地）**：
`canonical_bytes({did, role, signed_at, record_id, action_type, input_hash, output_hash,
risk_level, required})` —— 全部可从记录+审批对象复算，且**绑定本条记录**，跨记录不可复用。
`verify_record` 必须用本地 keyring 里的 `user:<did>` 公钥真实验签；验不过 → `problems` 带
`SECRETARIAT_SIGNATURE_INVALID`（`detail.scope="approval"`）。

**诚实边界**：审批签只证明"这次审批在本地被记录并签名"，不证明真实世界的签字意愿；
`local_only` 不再放进 `approval`（由 `tsa_timestamp` / `tsa_state` 表达）。这条文案必须出现在
面板里（i18n `secretariat.honesty.p1`–`p5`）。

---

## 5. 冻结表 E：认定书契约与"离线验证"的真实边界

认定书字段（`build_letter` 输出）：`id / kind / version / subject / created_at / mode /
period / note / responsibility{role,label,basis} / agents[] / delegation_chain[] / actions[] /
verification{signatures_ok, chain_ok, problems, tsa_state} / records_root / audit_head /
determination / public_key / fingerprint / signature`。

**R1 冻结的增强（Q7）**：`actions[]` 条目补齐"验签所需最小字段集"，使逐条 Agent 签名可离线复算：

| 字段 | 用途 |
| --- | --- |
| `id` / `timestamp` / `agent_id` / `action_type` / `status` / `risk_level` / `confidence` | 签名域输入 |
| `tool` / `input_hash` / `output_hash` / `prev_hash` / `entry_hash` | 签名域输入 |
| `signature` / `agent_pubkey` | 离线验签所需的签名与公钥 |
| `result_summary` | 人读摘要（≤300 字，进哈希但不进签名域） |
| `signature_ok` | 服务端当时的校验结论（离线复核会重新算，不只信这个布尔） |

**离线可复算 / 不可复算（必须写进界面与文档）**

| 项 | 离线 | 依据 |
| --- | --- | --- |
| 认定书自身签名 | ✅ 可验 | 只依赖 `public_key` + 正文 |
| `records_root` | ✅ 可复算 | `sha256("\n".join(sorted(entry_hash)))` |
| 逐条动作 Agent 签名 | ✅ 可验（本轮增强后） | `SIGNATURE_FIELDS` + `agent_pubkey` |
| 审批签名 | ⚠️ 需身份账本片段 | 审批人公钥不在认定书正文里 |
| 身份注册表绑定（pubkey ↔ agent_id） | ⚠️ 需账本片段 | 离线没有账本 |
| 可信时间戳 | ❌ 仅 `local_only` | 无 RFC 3161 服务 |

`records_root` 归一化：取各动作 `entry_hash`（缺失时退回整条动作的规范 JSON 哈希），
按字符串升序排序，以 `"\n"` 连接后 sha256。`audit_head` 由调用方（T7 路由）从审计链
当前链头取得；拿不到时写 `""` 并在 `verification.problems` 里如实登记，**不得伪造**。

---

## 6. 冻结表 F：T1–T10 接口表

复用既有 8 项能力（`docs/09` 裁决 D6），不新增能力；写操作 POST + CSRF；非 2xx 走统一
错误体 `{code, message, detail, request_id}`；响应平铺、不套 `data` 信封。

| # | 端点 | 方法 | 权限 | 入参 / 响应要点 | 专属错误码 |
| --- | --- | --- | --- | --- | --- |
| T1 | `/api/secretariat`（族入口） | GET | `audit.read` | 等价 T1 status 摘要（前端 `app.js` 已声明该基路径） | — |
| T1 | `/api/secretariat/status` | GET | `audit.read` | → `{ok, secretariat}`（含 counts/policy/chains/keyring/degraded） | — |
| T2 | `/api/secretariat/identities` | GET | `audit.read` | → `{ok, identities, count}` | — |
| T2 | `/api/secretariat/identities` | POST | `settings.write` + CSRF | `{agent_id, owner_did, image_hash?, boot_params?, kind?, rotate?}` → 201 `{ok, identity}` | `SECRETARIAT_FIELD_INVALID` |
| T3 | `/api/secretariat/identities/{agent_id}/revoke` | POST | `settings.write` + CSRF | `{reason?}` → `{ok, identity}` | `SECRETARIAT_IDENTITY_NOT_FOUND` |
| T4 | `/api/secretariat/delegations` | GET / POST | 读 `audit.read`；写 `settings.write` + CSRF | POST `{delegator_did, delegatee_did, scope, ttl_seconds?, parent_id?}` | `SECRETARIAT_SCOPE_WIDENED` `SECRETARIAT_DEPTH_EXCEEDED` `SECRETARIAT_SCOPE_INVALID` `SECRETARIAT_DELEGATION_INVALID` |
| T5 | `/api/secretariat/records/{id}/sign` | POST | `settings.write` + CSRF | `{approvers?, risk_level?, allow_unapproved?}` → `{ok, record, verification}` | `SECRETARIAT_IDENTITY_MISSING` `SECRETARIAT_IDENTITY_REVOKED` `SECRETARIAT_RECORD_MISSING` |
| T6 | `/api/secretariat/verify` | POST | `audit.read` | `{record?}` 或 `{records?}` → `{ok, results[], summary{total,ok,failed}}` | `SECRETARIAT_FIELD_INVALID` |
| T7 | `/api/secretariat/letters` | GET / POST | 读 `audit.read`；写 `settings.write` + CSRF | POST `{subject, record_ids[], period?, responsibility?, note?}`（≤200 条） | `SECRETARIAT_RECORD_MISSING` `SECRETARIAT_LETTER_EMPTY` |
| T8 | `/api/secretariat/letters/{id}` | GET | `audit.read` | → `{ok, letter, verification}` | `SECRETARIAT_LETTER_NOT_FOUND` |
| T9 | `/api/secretariat/letters/{id}/verify` | POST | `audit.read` | → `{ok, verification}` | 同上 |
| T10 | `/api/secretariat/seal` | POST | `settings.write` + CSRF | `{record_id, reason?}` → `{ok, seal}` | `SECRETARIAT_FIELD_INVALID` `SECRETARIAT_RECORD_MISSING` |

**记录级挂接（R4 集成，2026-11-11 落地）**

- 写路径：`POST /api/import` 与 `POST /api/ingest/events` 在 `store.put` 之前调用
  `InterveneService.sign_on_write_record(record, previous)`，由该记录 `agent_id` 的私钥签名；
  更新已存在记录时先摘掉旧 `signature`/`entry_hash`/`tsa_timestamp` 再按 `previous` 重签
  （事实变了旧签名必然失效，留着比没有更坏）。
- 开关：**产品入口默认开**（`--no-secretariat` 关）；`InterveneService(sign_on_write=False)`
  是库默认，既有 915 条测试与嵌入方行为不变。
- 范围：**仅 local 模式**。team 模式的 `owner_did` 需要 actor→DID 映射（后续批次），
  当前显式跳过并在响应 `signing.note` 里说明。
- 诚实边界：高危记录用 `allow_unapproved=True`——Agent 自签照签，审批缺口由
  `verify_record` 的 `SECRETARIAT_APPROVAL_REQUIRED` 如实报出，**不替审批人签字**；
  签名失败不丢数据（记录原样入库），响应 `signing.failures` / `last_problem` 给出计数与原因。

**生成器可识别性**：T3/T5/T8/T9 的路径形状写进 `server.py` 的路由助手，并在
`tools/gen_api_contract.py` 的 `ROUTE_HELPERS`/`ROUTE_HELPER_VERBS` 登记（与
`user_route`、`ingest_route` 同一种做法）；其余用 `path == API_PATH_…` 精确比较。
`docs/api_contract.json` 一律由 `--write` 生成，不手改。

---

## 7. 冻结表 G：验收标准 → 断言映射

| # | 规格条目 | 断言 / 命令 | 归属 |
| --- | --- | --- | --- |
| A1 | 每个签名域被篡改都必须验不过 | 对 11 个 `SIGNATURE_FIELDS` 逐个改值 → `verify_record(...)["ok"] is False` | R3 测试 |
| A2 | 已签名记录必须通过 ActionRecord schema | `MiniValidator(schema).collect(signed) == []`（low/medium/high/critical 四档） | R3 测试 |
| A3 | 身份登记/轮换/吊销只追加 | 账本 `verify()["ok"]`；重复登记幂等；吊销后签名 → 409 `SECRETARIAT_IDENTITY_REVOKED` | R2 测试 |
| A4 | 私钥零泄漏 | 身份/状态/认定书响应中不出现 seed、`keyring.json` 内容；`api` 响应序列化后不含 "seed" 键 | R2 测试 |
| A5 | 责任链不可伪造 | 自带 `human_owner`≠owner → 400；链尾≠本 Agent DID → 400；链根≠人 → 400；`delegation_chain` 每项过 DID pattern | R2/R3 测试 |
| A6 | 双人审批必须是两个不同 DID | critical 传同 DID 两次 → 400；传两个不同 user DID → 通过 | R3 测试 |
| A7 | 审批签名绑定记录且不可复用 | 两条记录（同一审批人/同一时钟）的审批签名**不相等**；改动记录 id 后验签失败 | R3 测试 |
| A8 | `verify_record` 真验审批签名 | 伪造审批签名 → `problems` 含 `SECRETARIAT_SIGNATURE_INVALID`（scope=approval） | R3 测试 |
| A9 | 委托只能收窄、深度 ≤5、过期失效 | 越权 scope → `SECRETARIAT_SCOPE_WIDENED`；第 6 层 → `SECRETARIAT_DEPTH_EXCEEDED`；过期 → allowed=false | 已有 + R2 补 DID 校验 |
| A10 | 认定书可离线复算 | 用 `tools/verify_letter.py` 对导出 JSON：验认定书签名 + `records_root` + 逐条动作签名；改动任一字段 → 非零退出 | R4 测试 |
| A11 | 认定书含 `audit_head`/`records_root` | 生成后两字段均非空；`audit_head` 与审计链当前头一致 | R4 测试 |
| A12 | T1–T10 与契约一致 | `python tools/gen_api_contract.py --check`：secretariat 相关行全部 PASS；域外 8 个端点逐条登记 | R4 |
| A13 | 面板五区可交互 | `node web/selfcheck.mjs` 通过；`?probe=1` 输出含 `trust-panel` 与 5 个 `[data-zone]`；截图留证 | R4 |
| A14 | 文案闸门 | `python tools/i18n_lint.py` PASS 5/5；`python tools/i18n_merge.py --check` 通过；`web/ext/secretariat.js` 零 CJK | R4 |
| A15 | 降级项不得伪装 | 面板出现 `local_only`/`unanchored` 文案；无"可信时间戳/硬件密钥/已上链"字样 | R4 |

---

## 8. 冻结表 H：写作用域与依赖

| 子任务 | 写作用域 | 依赖 |
| --- | --- | --- |
| R1 | `docs/12-secretariat-delta-spec.md`（本文件） | — |
| R2 | `agent_explainer/secretariat.py`（身份/委托面）、`tests/test_secretariat.py`（只加不改） | R1 |
| R3 | `agent_explainer/secretariat.py`（签名/审批面）、`docs/schemas/action_record.schema.json`、`docs/02 §2.8`、`docs/07 §3.1`、`tests/test_secretariat.py`、新增 `tests/test_secretariat_contract.py` | R2 |
| R4 | `agent_explainer/server.py`（T1–T10）、`tools/gen_api_contract.py`（ROUTE_HELPERS 登记）、`docs/api_contract.json`（生成）、`docs/03 §6.5`、`web/ext/secretariat.js`、`web/ext/secretariat.css`、`web/i18n/parts/secretariat.{zh,en}.json`、`tools/verify_letter.py`、新增 `tests/test_secretariat_api.py` | R3 |

共享文件唯一负责人：`server.py` 与 `docs/api_contract.json` 在 R4 内只由集成者改；
i18n 只写 `parts/secretariat.*` 后统一 `i18n_merge.py`。

---

## 9. 决策记录 Q1–Q10

| # | 问题 | 结论 | 理由 | 影响 |
| --- | --- | --- | --- | --- |
| Q1 | 签名域是否维持 11 项 | **维持 11 项**；`risk_level` 签名前归一化写回 | 删域会让"高风险审批"签名不含风险等级 | R3 |
| Q2 | `tool` 怎么落 | **schema 增可选 `tool`（string ≤120）** | 签名域需要它；删掉则"签了名却不知签的是哪个工具"；加法向后兼容 | R3 + docs/02/07 |
| Q3 | `approval` 收敛到什么形状 | 收敛到 docs/02 已写形状；`local_only`/`policy`/`fingerprint`/`risk_level`/`required` 从审批对象里移除，必需信息由 `tsa_state`/`record.risk_level`/`approval.required` 表达 | schema `additionalProperties:false` 是硬约束；文档早已这么写 | R3 |
| Q4 | 审批签 payload | `{did, role, signed_at, record_id, action_type, input_hash, output_hash, risk_level, required}` 的规范 JSON | 可复算 + 绑定记录，杜绝跨记录复用 | R3 |
| Q5 | 双人"不同 DID"判定 | 审批条目按 `did` 去重；去重后 < `required` → 400（复用 `SECRETARIAT_FIELD_INVALID`，field=`approvers`） | 不新增错误码，避免动 i18n 与错误码清单 | R3 |
| Q6 | 责任链生成/校验 | 链尾 = `did_from_pubkey(pub, kind="agent")`；链根必须等于 `owner_did`；长度 ≤8；不合法 400 | schema 要求 DID；`setdefault` 可伪造 | R2/R3 |
| Q7 | 认定书动作最小字段集 | 见 E 表；体积上限：单条动作 ≤1.5 KB、整封 ≤200 条 | 离线复算需要；也守住 64KB/行纪律 | R3/R4 |
| Q8 | `records_root`/`audit_head` | 见 E 表；`audit_head` 为空要登记问题，不伪造 | 诚实降级 | R4 |
| Q9 | 路由挂载形式 | 静态路径 `path == 常量`；动态路径用路由助手 + `ROUTE_HELPERS` 登记 | 生成器只认这两类形状 | R4 |
| Q10 | 验收与域外已知红 | 契约闸门：secretariat 行必须 PASS；域外 8 个未挂载端点逐条列入"已知红" | 总裁办范围外，不能靠改文档造假绿 | R4 |

---

## 10. 残余风险与已知限制（交付报告必须出现）

1. **HSM/TEE 不存在**：密钥在本地 `keyring.json`(0600)，`degraded=["local-tsa","in-process-keys"]`。
2. **RFC 3161 不存在**：`tsa_timestamp = local-tsa:<ISO>`，`tsa_state=local_only`。
3. **审批人身份**：本地 keyring 里的 user 密钥，只证明"本地记录并签名"。
4. **认定书离线边界**：审批签与身份账本绑定需要账本片段，见 E 表。
5. **接口层上线前**：`/api/secretariat/*` 在 R4 之前一律 404，属预期。
6. **测试环境**：workspace-write 下 `tempfile` 0700 目录会被沙箱拒绝，需测试垫片或全权限；
   垫片不进交付物。

---

## 11. 交付证据（R2–R4 实测，2026-11-11）

| 项 | 实测结果 | 复跑命令 |
| --- | --- | --- |
| 总裁办单测 | **49/49 OK**（R2/R3 收口后；R1 基线 40） | `python -m unittest tests.test_secretariat -q` |
| 契约/加固单测 | **18/18 OK**（schema 0 错误、责任链 DID、双人不同 DID、审批签绑定记录、篡改检出） | `python -m unittest tests.test_secretariat_contract -q` |
| 接口单测 | **25/25 OK**（T1–T10 真发 HTTP、签名落盘、认定书生成/读取/复算/盖章/405） | `python -m unittest tests.test_secretariat_api -q` |
| 全量单测 | **954 条，1 条已知红**（`test_e2e.ArtifactTests.test_signature_status_is_honest`，发布件未重签；与 5.0 无关，见 docs/09 §1.5） | `python -m unittest discover -s tests -p "test_*.py" -q` |
| 文案闸门 | **PASS 5/5**（1115 键 / 27 域 / 错误码 168） | `python tools/i18n_lint.py` |
| 契约闸门 | 路由 41 条、文档 34 行/36 路径一致；**唯一失败项 = 8 个总裁办范围外的前端端点**（analytics/compliance/evidence/groups/preferences/rollback/shares/suggestions） | `python tools/gen_api_contract.py --check` |
| 前端自检 | **92/92 PASS**（收口新增 secretariat 面板断言；09/27 两条既有红随扫描面与放行清单修复一起转绿） | `node agent_explainer/web/selfcheck.mjs` |
| 浏览器实测 | 面板渲染 + 登记身份（计数 0→1）+ 认定书列表（1 封，主题/生成时间/涉及记录正确）；截图 `docs/05-evidence/r4-secretariat-panel.png` | `?probe=1` + 截图 |
| 认定书离线复核 | 示例认定书三动作签名 + records_root 全通过；篡改 `timestamp` 后退出码 1 | `python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json` |
| 入库即签名（R4 集成） | 6/6 OK：导入签名 + schema 0 错误、更新重签、上报签名、关闭后不签 | `python -m unittest tests.test_secretariat_autosign -q` |

配套角色报告：[docs/13](13-r2-r3-production-report.md)（R2/R3 后端）、[docs/14](14-r4-production-report.md)（R4 全栈）、
[docs/15](15-qa-r2r4-acceptance.md)（测试与质量官独立验收，DeepSeek-V4.1-Flash）、
[docs/16](16-closeout-report.md)（队长收口：团队执行记录、决议与残余风险）、
[docs/audit/](audit/)（R0/R2/R3/R4 四份独立复核报告）。

**已知缺口（不在本轮"总裁办 R1–R4"范围内，交由后续批次）**

- 记录写入挂接已在 R4 补齐（local 模式默认开）；team 模式仍需 actor→DID 映射（见 docs/14 §7）。
- 审批签与身份绑定无法离线复算（需要身份账本片段）；认定书里已如实标注。
- TSA / HSM / 上链三项仍是显式降级（local-tsa、本地 keyring、无 anchor）。
- `app.js` 里 8 个非总裁办端点仍指向未挂载路由（契约闸门的已知红）。

---

## 12. 收口决议（团队收口批次，2026-11-12）

本批由 4 人团队按"产品经理 / 后端 / 架构师 = strata 本地模型，测试与质量官 = DeepSeek-V4.1-Flash"
分工、**严格串行**执行；四份复核报告见 [docs/audit/](audit/)，独立验收见 [docs/15](15-qa-r2r4-acceptance.md)。

### 12.1 状态码以运行时为准（已同步 EXPANSION_ERROR_TABLE 与本文件）

| 错误 | 状态 | 语义 |
| --- | --- | --- |
| `SECRETARIAT_FIELD_INVALID` | 400 | 字段/参数/审批人去重后不足（field=approvers） |
| `SECRETARIAT_IDENTITY_MISSING` / `RECORD_MISSING` / `LETTER_NOT_FOUND` | 404 | 不存在 |
| `SECRETARIAT_SCOPE_WIDENED` / `DEPTH_EXCEEDED` / `APPROVAL_REQUIRED` / `IDENTITY_REVOKED` / `DELEGATION_EXPIRED` | 409 | 状态/策略冲突（不是参数写法错） |

> 收口前本文件与 server 对外表把前三项写成 400；经 R0/R2/R3/R4 四轮核对，**以运行时 409 为事实来源**，
> 改文档与对表，不改已签收的行为与断言。

### 12.2 四项口径裁决

1. **B08 未登记 user DID 建根委托**：保留 **TOFU**（首次出现即生成该 DID 的本地审批密钥，`signer.kind="local_key"` + `note` 如实标注）；
   Agent DID 仍必须有出处（已登记实例或某条委托的受托人）。要收紧成"必须先登记用户"需要先定义用户登记端点，列入后续批次。
2. **B11 T10 盖章不写回记录**：盖章是**独立凭证**（`seal` 对象，`digest = approval_digest(record)`，可 `verify_seal` 复算）；
   不写回已签名记录，避免为了盖一个章把原签名作废。critical 的 `approval.seal` 由签名流程自动生成并随记录验证。
3. **team 模式入库签名**：如实关闭（`signing.enabled=false` + `note`），测试钉住现状；owner→DID 映射列入后续批次。
4. **A13 自动化**：`selfcheck.mjs` 新增 secretariat 面板断言（注册 id / 五区 / CSS 作用域 / 零 CJK），
   并把 ext 源码纳入 i18n 扫描面、放行 8 个未上线端点，**92/92 PASS**。

### 12.3 验收口径补注

- A1：`tsa_timestamp` 不参与 `entry_hash`/签名（可后补时间戳），11 个签名域逐字段篡改已自动化覆盖。
- A7：审批签绑定 `record_id`；**前提是记录 id 唯一**（同 id 覆盖会让旧审批签语义变味，R4 写路径以 id 为幂等键）。
- A10：离线复核要**三项一起看**（认定书本体签名 + `records_root` + 逐条动作签名）；单字段篡改由后两项检出，只比 `records_root` 会漏。
- A14：`web/ext/secretariat.js/.css` 零 CJK（面板注释已译英），`i18n_lint` 扫描面仍不含 `web/ext`，由 selfcheck 面板断言兜底。

