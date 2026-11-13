# TraceLens 5.0 · 总裁办与八大扩展功能 · 实施契约（docs/07）

> 依据：`E:\Tracelens 产品规格说明书.docx`（v1.2，以下简称"规格书"）。
> 本文是**并行开发的唯一契约**：模块 API、数据模型、HTTP 路由、错误码、前端扩展点、
> 文件归属（写作用域）、验收映射全部以此为准。与规格书冲突时，**规格书优先**；
> 与既有文档（`03-architecture.md`）冲突时，以本文为 5.0 增量的口径，并同步更新 `03`。
>
> 产品基线：`0.4.5`（`server.py:SERVER_VERSION`）→ 本轮 **`0.5.0`**；
> 数据契约 `SCHEMA_VERSION=1.1` **不升版**（本轮新增字段全部可选、向后兼容）。

---

## 0. 范围、批次与"什么算做完"

规格书第 5 节给出 5 批推进顺序，第 6 节给出 12 个子任务（R1–R12）。本轮一次做完，
内部按 5 批排依赖，交付时按 R1–R12 汇报进度。

| 批 | 内容 | 规格书章节 | 本轮落地 |
| --- | --- | --- | --- |
| 第一批 | 干预回滚、置信度可视化、多模态解释与因果推理链 | 功能一/二/三 | 后端模块 + 卡片装饰 + API |
| 第二批 | 多 Agent 任务聚合视图 | 功能四 | `groups.py` + 面板 |
| 第三批 | 共享与协作模式 | 功能五 | `sharing.py` + 面板 |
| 第四批 | 自动化改进建议、用户行为分析仪表盘 | 功能六/七 | `suggestions.py` / `analytics.py` + 面板 |
| 第五批 | 合规审计导出增强 | 功能八 | `compliance.py` + 面板 |
| 贯穿 | 总裁办（R1–R3、R12） | 第二节 | `crypto_sig.py` + `secretariat.py` + 面板 |

**"做完"的判定**（三层，缺一层不算）：

1. **能力层**：规格书"数据模型变更"里的字段/表都真实存在并落盘；"技术实现要点"里的机制
   有代码；"验收标准"里的每一条都有**可执行断言**（`tests/test_expansion_*.py`）。
2. **接口层**：§5 的路由全部实现，权限与 CSRF 按表走，错误码进 `docs/api_contract.json`。
3. **界面层**：§6 的扩展点在真实页面上跑通（`node` 语法检查 + 浏览器实测 + 截图）。

诚实边界（写进最终交付报告，不许模糊）：规格书里任何**离线环境无法真实做到**的承诺，
必须显式降级并写明原因（见 §10）。产品自己的纪律是"决定 ≠ 生效"，本轮同样适用：
**做不到的事不假装做到**。

---

## 1. 不可谈判的工程纪律（沿用产品既有规则）

| # | 规则 | 为什么 |
| --- | --- | --- |
| R1 | `agent_explainer/*` 只允许 **Python 标准库**；不得 `import` 第三方库、不得发起网络请求 | 产品基线：离线可用（`docs/03` §9；`tests/test_trace_adapter.py::OfflinePurityTests` 静态扫描） |
| R2 | 密钥/私钥/令牌**永不**出现在 API 响应、审计条目、日志、错误 message 里 | 既有口径：`settings.py` 只回掩码；`audit.py` 写盘前脱敏 |
| R3 | 账本（身份/委托/认定书/协作/导出）**append-only**：不提供删除/截断；撤销用状态字段；断链只报告不重建 | 与审计链同一纪律（`docs/03` §4、§6.3） |
| R4 | 所有非 2xx 走统一错误体 `{code, message, detail, request_id}`；新错误码先进本文 §7 + i18n 双语字典 + 至少一条测试 | `docs/03` §6.4 错误码纪律 |
| R5 | 用户可见文案**只能**来自 `web/i18n/*.json`；`web/` 下的 `.js`/`.css`/`.html` **零 CJK 字面量**，且不出现"句子形状"的英文硬编码 | i18n lint（K-10）；5.0 起 lint 覆盖 `web/ext/**` |
| R6 | 前端零框架、零 CDN、零构建、`file://` 可打开（新面板在离线时必须如实说"需要服务端"，不许伪造数据） | `docs/03` §3 web 模块纪律 |
| R7 | 只加 GET/POST（写操作 POST 子资源）；`PUT` 仅 `/api/settings`；`PATCH/DELETE` 一律 405 | `docs/03` §6.3 方法纪律 |
| R8 | 写操作必须过 CSRF（team）+ 权限能力（服务端强制，前端隐藏只是体验） | `docs/03` §5 |
| R9 | 中文注释/文档解释**为什么**；模块顶部有职责说明、`__all__`、`__main__` 自检（Python）或 IIFE + 头注释（JS） | 仓库既有风格 |
| R10 | 每个成员只改自己写作用域内的文件（§9）；共享文件（`server.py` / `app.js` / `docs/` / `tools/` / `i18n` 字典）各有唯一负责人 | 并行安全 |

---

## 2. 总裁办：责任认定与签名背书

规格书第二节的四件事映射成四个机制，全部落在 `secretariat.py`（+ `crypto_sig.py` 的密码学原语）。

### 2.1 身份确权（规格书 2.1）

- 每个 Agent 实例一枚 **Ed25519** 密钥对（`crypto_sig.generate_keypair()`）。
- 身份记录 `AgentIdentity` 绑定：`agent_id` / `pubkey` / `image_hash`（镜像哈希）/
  `boot_params_hash`（启动参数哈希）/ `owner_did`（所属用户 DID）/ `created_at`。
- 注册表 = `identities.jsonl`（`Ledger`：`seq`/`prev_hash`/`entry_hash`，append-only）。
  轮换 = 追加新记录，旧记录标 `superseded_by`；吊销 = 追加 `revoked_at` 记录。
- **私钥**落 `<data>/features/secretariat/keyring.json`（`chmod 0600`，Windows 上尽力而为），
  **只在本地**，API 响应里只有 `pubkey` 与 `fingerprint`（`sha256(pubkey)` 前 16 位）。

### 2.2 行为签名（规格书 2.2）

- 签名覆盖字段（**顺序固定**，`SIGNATURE_FIELDS`）：
  `timestamp, agent_id, action_type, tool, input_hash, output_hash, prev_hash, entry_hash,
   risk_level, confidence, status`。
- 签名前先 `crypto_sig.signed_payload(record, SIGNATURE_FIELDS)` → 规范 JSON 字节
  （键排序、紧凑分隔符、拒绝 NaN），再 `sign(seed, payload)`。
- `entry_hash` = `sha256(canonical(除 signature/tsa_timestamp 外的整条记录))`；
  `prev_hash` = 该 Agent 上一条已签名记录的 `entry_hash`（每 Agent 一条独立链）。
- **风险分级签名策略**（规格书 2.5 表，`RISK_POLICY`）：

  | 风险 | 签名要求 | `approval.required` |
  | --- | --- | --- |
  | `low` | Agent 自签 | 0 |
  | `medium` | Agent 自签 + 日志（审计条目） | 0 |
  | `high` | Agent 自签 + 人工审批签 | 1 |
  | `critical` | Agent 自签 + 双人审批签 + 总裁办盖章 | 2 + seal |

  `approval` 对象：`{required, approvers:[{did, role, signed_at, signature}], sealed, seal}`。
  审批人签名同样走 Ed25519（服务端为每个已登记的人类 DID 保存一枚本地密钥，**它只证明
  "这次审批在本地被记录并签名"，不证明真实世界的签字意愿**——这一句写进界面文案）。
- 可信时间戳 `tsa_timestamp`：离线环境**没有** RFC 3161 服务器，本实现写
  `local-tsa:<ISO8601Z>` 并在 `verify` 结果里标 `tsa_state="local_only"`。
  **禁止**把它显示成"可信时间戳已获取"（规格书要求 RFC 3161；这是本轮显式降级项）。

### 2.3 责任归属（规格书 2.3）

- `DelegationRecord`（`delegations.jsonl`，`Ledger`）：`id` / `delegator_did` / `delegatee_did` /
  `scope{action_types[], tools[], max_risk}` / `depth` / `parent_id` / `issued_at` /
  `expires_at` / `signature`。
- **权限只能收窄**：子 scope 必须是父 scope 的子集（`action_types`/`tools` 子集、
  `max_risk` 不升档），否则 400 `SECRETARIAT_SCOPE_WIDENED`。
- **最大深度 5**：`depth > 5` → 400 `SECRETARIAT_DEPTH_EXCEEDED`。
- 记录里的 `delegation_chain` 是"人 → 主 Agent → 子 Agent"的 DID 数组（最长为根的人 DID
  打头），`human_owner` 是链根。

### 2.4 争议裁定（规格书 2.4）

- `build_letter(...)` 生成**责任认定书**：涉案 Agent 身份、责任链、行为摘要、
  签名验证结果、哈希链连续性、责任判定（`technical` / `decision` / `platform`）、
  结论与建议。**由总裁办公钥签名**，可离线验证（`verify_letter()` 只依赖公钥 + 记录本体）。
- 认定书正文落 `<data>/features/secretariat/letters/<id>.json`，索引追加进 `letters.jsonl`。
- 认定书里必须带 `audit_head`（导出时审计链的链头）与 `records_root`（涉及记录 `entry_hash` 的
  归约哈希），使"离线复算"有可比的锚点。

---

## 3. 数据模型

### 3.1 Signed ActionRecord —— 新增字段（全部可选，`action_record.schema.json` 增量）

| 字段 | 类型 | 说明（规格书表 3） |
| --- | --- | --- |
| `agent_pubkey` | string(44) | 该实例 Ed25519 公钥（base64） |
| `human_owner` | string | 对此 Agent 负责的用户 DID |
| `delegation_chain` | array[string] | 责任链（人 → … → 本 Agent） |
| `input_hash` / `output_hash` | string(64) | sha256（规范 JSON） |
| `prev_hash` | string(64) | 本 Agent 上一条签名记录 `entry_hash` |
| `entry_hash` | string(64) | 本条记录哈希 |
| `signature` | string | `sign(SIGNATURE_FIELDS)` 的 base64 |
| `tsa_timestamp` | string | `local-tsa:<ISO>`（§10 降级项） |
| `approval` | object | `{required, approvers[], sealed, seal}` |
| `tool` | string（≤120） | 签名域里的工具名，由 `tool_used.name` 派生 |
| `multimedia_evidence` | string \| array | 证据条目（§3.2） |
| `causal_explanation` | string \| object | 反事实解释（§3.2） |
| `confidence_source` | object | `{method, logprob, tool_success_rate, samples, computed_at}` |
| `intervention_history` | array | 干预次数与每次的摘要（append-only 语义） |
| `group_id` | string | 所属 TaskGroup |
| `shared_users` | array | `[{did, permission, shared_at, invite_id}]` |
| `rollback_ref` | string | 被回滚记录的 id |
| `rollback_state` | enum | `none` / `rolled_back` / `rollback_blocked` |
| `improvement_suggestion` | object \| string | 命中改进建议时的摘要 |
| `evidence_feedback` | object | `{like, dislike, updated_at}`（点赞/点踩计数） |

`action_type` 枚举**新增 `rollback`**（规格书功能一"数据模型变更"）。

> **签名器执行上限（R3 落地）**：`tool ≤120`、`approval.approvers ≤4`、`approvers[].role ≤40`
> 在签名**之前**校验，超限 → 400 `SECRETARIAT_FIELD_INVALID`（`field=tool`/`approvers`），
> 不裁值、不产出"已签名但不合规"的记录；审批签名 payload 绑定本条记录（docs/12 §4），
> 跨记录复用无效。细则见 [docs/02 §2.8](02-data-model.md)。

### 3.2 证据与因果解释的形状

```jsonc
"multimedia_evidence": [
  {"evidence_id":"evd_2026...","kind":"screenshot|diagram|diff",
   "media_type":"image/png|image/svg+xml|text/x-diff",
   "uri":"/api/evidence/evd_.../file","sha256":"…","caption":"…",
   "generated_at":"…","provided_by":"runtime|user|server"}
],
"causal_explanation": {
  "counterfactual":"如果当时选择「B」，结果将是……，因为……",
  "alternatives":[{"option":"A","expected":"…","why":"…"}],
  "model":"template|llm","generated_at":"…","language":"zh"
}
```
证据文件的 sha256 **进入签名字段范围**：`evidence_hash` = 各证据 sha256 的规范归约，
作为 `entry_hash` 的一部分（`entry_hash` 覆盖整条记录，自然包含它们）。

### 3.3 新增实体存储（`feature_store.py`，默认根 `<data>/features/`）

| 实体 | 文件 | 类型 | 字段要点 |
| --- | --- | --- | --- |
| 身份注册表 | `secretariat/identities.jsonl` | Ledger | §2.1 |
| 委托链 | `secretariat/delegations.jsonl` | Ledger | §2.3 |
| 认定书索引 | `secretariat/letters.jsonl` | Ledger | `id, subject, created_at, records, root, signature` |
| 私钥保管箱 | `secretariat/keyring.json` | 文件 0600 | **绝不进 API/审计** |
| TaskGroup | `groups.jsonl` | Store | `id, title, objective, created_at, status, agent_ids[], closed_at` |
| ShareInvitation | `shares.jsonl` | Store | `id, group_id, inviter_did, invitee_did, permission, token_hash, expires_at, status, created_at, accepted_at, revoked_at` |
| 协作事件 | `collab_events.jsonl` | Ledger | `id, actor_did, action, target, at, detail, signature` |
| 改进建议 | `suggestions.jsonl` | Store | `id, user_id, tool, param, direction, from_value, to_value, occurrences, status, created_at, decided_at, cooldown_until` |
| 用户偏好 | `preferences.jsonl` | Store | `id, user_id, tool, param, value, source(manual\|suggestion), created_at, updated_at` |
| 导出记录 | `exports.jsonl` | Ledger | `id, user_id, time_range, format, root_hash, ipfs_cid, eth_tx_hash, anchor_state, path, bytes, sha256, chain_ok, created_at` |
| 证据索引 | `evidence/index.jsonl` | Store | `id, record_id, kind, media_type, sha256, bytes, caption, created_at, feedback` |
| 证据文件 | `evidence/files/<id>.<ext>` | 文件 | 本体（png/svg/diff） |
| 撤销日志 | `rollback/journal.jsonl` | Store | `id, action_id, tool, inverse_op, snapshot, created_at, applied_at, result` |
| 存证锚点 | `anchors.jsonl` | Ledger | `id, export_id, cid, state, anchor, at` |

**用户 id**：local 模式为 `local`；team 模式为会话 `actor_id`。分析类输出里的用户 id
一律用 `sha256(user_id)[:12]`（规格书功能六"数据脱敏"）。

---

## 4. 模块 API 契约（Python）

每个模块：纯标准库、无网络、可独立单测。**函数名/参数名即为契约**，改动必须通知 lead。

### 4.1 `agent_explainer/crypto_sig.py`（负责人：crypto-core）

见 `crypto-core` 任务说明；关键面：`generate_keypair / pubkey_from_seed / sign / verify /
canonical_json / canonical_bytes / sha256_hex / hash_object / b64e / b64d /
did_from_pubkey / did_from_name / signed_payload / verify_b64 / public_key_b64`。

### 4.2 `agent_explainer/qr.py`（负责人：crypto-core）

`encode(text, *, ecc="M", min_version=1, max_version=12) -> list[list[int]]`、
`decode(matrix) -> str`、`to_svg(matrix, *, scale=4, border=4) -> str`、`to_ascii(...)`。

### 4.3 `agent_explainer/feature_store.py`（已完成，负责人：lead）

`FeaturePaths` / `FeatureStore` / `Ledger` / `open_store` / `open_ledger` /
`utc_stamp` / `new_id` / `canonical_json` / `sha256_hex` / `hash_object` / `GENESIS_HASH`。

### 4.4 `agent_explainer/secretariat.py`（负责人：secretariat）

```python
SECRETARIAT_VERSION = "1.0"
SIGNATURE_FIELDS = ("timestamp","agent_id","action_type","tool","input_hash","output_hash",
                    "prev_hash","entry_hash","risk_level","confidence","status")
RISK_POLICY = {"low": {...}, "medium": {...}, "high": {...}, "critical": {...}}
MAX_DELEGATION_DEPTH = 5
ROLES = ("technical", "decision", "platform")
class SecretariatError(Exception):   # .code/.reason/.field/.detail/.hint/as_dict()
class Secretariat:
    def __init__(self, paths: FeaturePaths, *, mode="local", now=None) -> None
    # 身份
    def register_identity(self, *, agent_id, owner_did, image_hash=None,
                          boot_params=None, kind="agent") -> dict
    def identities(self) -> list[dict]
    def identity(self, agent_id: str) -> dict | None      # 不含私钥
    def revoke_identity(self, agent_id, *, reason="") -> dict
    def fingerprint(self, agent_id) -> str
    # 签名
    def sign_record(self, record: Mapping, *, risk_level=None,
                    approvers: Sequence[Mapping] | None = None,
                    previous: Mapping | None = None) -> dict
    def verify_record(self, record: Mapping) -> dict
        # {"ok", "checks":{"identity","signature","entry_hash","prev_hash","tsa","approval","chain"},
        #  "problems":[{code,detail}], "tsa_state"}
    def sign_chain(self, records: Sequence[Mapping]) -> list[dict]     # 批量签名（导入路径）
    def seal(self, *, subject: str, digest: str, reason="") -> dict    # 总裁办盖章
    # 委托
    def create_delegation(self, *, delegator_did, delegatee_did, scope,
                          ttl_seconds=None, parent_id=None) -> dict
    def delegations(self) -> list[dict]
    def delegation_chain_of(self, delegation_id: str) -> list[dict]
    def authorise(self, delegation_id, *, action_type, tool, risk_level) -> dict
    # 认定书
    def build_letter(self, *, subject, records: Sequence[Mapping], responsibility="technical",
                     period=None, note="", audit_head=None) -> dict
    def letters(self, *, limit=50) -> list[dict]
    def letter(self, letter_id: str) -> dict | None
    def verify_letter(self, letter: Mapping) -> dict
    # 仪表盘
    def status(self) -> dict
def risk_requirements(risk_level: str) -> dict
def default_secretariat(paths: FeaturePaths, *, mode="local") -> Secretariat
```
`sign_record` 返回**新字典**（不就地改入参），补齐 §3.1 的签名字段；
`input_hash`/`output_hash` 由调用方在 `record["context"]["input"]` / `record["tool_result"]`
里给原始载荷，本模块用 `hash_object` 计算（缺失则留 `None` 并在 `problems` 里标）。

### 4.5 `agent_explainer/rollback.py`（负责人：features-core）

```python
ROLLBACK_VERSION = "1.0"
class RollbackError(Exception)
# 逆向操作注册表：工具名 → 逆向动作
def register_inverse(tool: str, *, op: str, describe_key: str, apply=None) -> None
def inverse_of(tool: str) -> dict | None            # {"op","describe_key"} 或 None
def reversible(record: Mapping) -> dict             # {"reversible":bool,"op":..,"reason":..}
def plan(records: Sequence[Mapping], action_id: str, *,
         max_depth: int = 3) -> dict
    # {"ok","action","dependents":[ids],"order":[ids],"reversible","blockers":[{code,...}],
    #  "requires_confirmation":bool}
class RollbackService:
    def __init__(self, paths: FeaturePaths, *, applier=None) -> None
    def execute(self, records, action_id, *, actor, reason="", confirm_all=False,
                lang="zh") -> dict
        # {"ok","rollback_record":{...},"affected":[ids],"inverse":{...},"order":[...]}
    def journal(self) -> list[dict]
    def coverage(self, records) -> dict   # {"total","reversible","ratio"}  验收标准 ≥90%
```
逆向动作默认实现（本地、可审计、幂等）：`cancel_booking` / `restore_file` / `undo_write` /
`revoke_message` / `undo_config_change` / `undo_note`。执行 = 写 journal + 返回回执对象
（`{op, applied, receipt_state, note}`）；**本服务不真的去调外部系统**，回执状态如实写
`simulated_local`，界面必须照这个字显示（不许写"已恢复"）。

### 4.6 `agent_explainer/confidence.py`（负责人：features-core）

```python
CONFIDENCE_VERSION = "1.0"
THRESHOLDS = {"green": 0.9, "light_green": 0.7, "yellow": 0.5, "red": 0.0}
DEFAULT_CONFIDENCE = 1.0
def band(value) -> str                     # green|light_green|yellow|red|unknown
def color(value) -> str                    # 语义色 token：ok|ok_soft|progress|danger|neutral
def normalize(value, *, source="default") -> float | None
def from_logprobs(logprobs, *, tokens=None) -> dict
def combine(logprob_conf, tool_success_rate, *, weights=(0.7, 0.3)) -> dict
def apply_to_record(record, *, computed=None) -> dict
    # 设 confidence / confidence_source；< 0.5 时 requires_action=True 并补 reason 与选项
def policy() -> dict                       # 供 GET /api/confidence/policy
def explain(value, *, lang="zh") -> str
```

### 4.7 `agent_explainer/evidence.py`（负责人：features-core）

```python
EVIDENCE_VERSION = "1.0"
KINDS = ("screenshot", "diagram", "diff")
MAX_BYTES = 4 * 1024 * 1024
class EvidenceError(Exception)
class EvidenceService:
    def __init__(self, paths: FeaturePaths, *, now=None) -> None
    def add(self, record_id, *, kind, payload, caption="", provided_by="runtime") -> dict
    def add_diff(self, record_id, *, before, after, filename="", caption="") -> dict
    def add_diagram(self, record_id, *, title="", nodes, edges, caption="") -> dict
    def list(self, record_id) -> list[dict]
    def get(self, evidence_id) -> dict | None
    def file_path(self, evidence_id) -> pathlib.Path | None
    def feedback(self, evidence_id, *, vote, actor="local") -> dict
    def counterfactual(self, record, candidates, *, lang="zh", llm=None) -> dict
    def sign_range(self, record) -> str     # 证据哈希归约（进 entry_hash）
```
`payload` 形状：`screenshot` = `{"data_uri"|"base64"|"png"|"jpeg"}`（校验魔数）；
`diagram` = `{"nodes":[...],"edges":[...]}`；`diff` = `{"before","after"}`。

### 4.8 `agent_explainer/groups.py`（负责人：features-collab）

```python
GROUPS_VERSION = "1.0"
MAX_AGENTS = 10
class GroupError(Exception)
class GroupService:
    def __init__(self, paths: FeaturePaths, *, now=None) -> None
    def create(self, *, title, agent_ids, objective="", actor="local", group_id=None) -> dict
    def get(self, group_id) -> dict | None
    def list(self, *, status=None) -> list[dict]
    def close(self, group_id, *, status="completed", actor="local") -> dict
    def aggregate(self, group_id, records) -> dict
        # {group, records(sorted by timestamp), agents[{id,name,count,color_index,stuck}],
        #  progress{total,done,running,needs_me,percent}, resources{cost_ms...},
        #  window{started_at,ended_at,total_ms}}
    def grouping_report(self, records) -> dict   # {"declared","grouped","accuracy"}
def default_group_service(paths) -> GroupService
```

### 4.9 `agent_explainer/sharing.py`（负责人：features-collab）

```python
SHARING_VERSION = "1.0"
PERMISSIONS = ("view", "approve", "manage")
DEFAULT_TTL_SECONDS = 86400
class ShareError(Exception)
class ShareService:
    def __init__(self, paths: FeaturePaths, *, now=None) -> None
    def invite(self, *, group_id, inviter_did, permission, invitee_did="",
               ttl_seconds=DEFAULT_TTL_SECONDS) -> dict   # 明文 token 只出现一次
    def accept(self, *, token, invitee_did) -> dict
    def revoke(self, invite_id, *, actor) -> dict
    def get(self, invite_id) -> dict | None
    def list_for(self, actor) -> dict          # {"issued":[...],"shared_with_me":[...]}
    def grant_for(self, actor, group_id) -> dict | None
    def can(self, actor, permission, group_id) -> dict
    def record_event(self, *, actor, action, target, detail=None) -> dict
    def events(self, *, limit=50) -> list[dict]
    def expired(self, invite) -> bool
```
权限级联：`manage ⊃ approve ⊃ view`（高权限包含低权限）。

### 4.10 `agent_explainer/analytics.py`（负责人：features-analysis）

```python
ANALYTICS_VERSION = "1.0"
def anonymise(user_id, *, salt="") -> str          # sha256 前 12 位
def trust_health(intervention_rate) -> float       # 1 - rate，钳到 0..1
def dashboard(records, *, audit_entries=None, range_from=None, range_to=None,
              agent=None, granularity="day", salt="") -> dict
def export_csv(report) -> str
def export_pdf(report, *, lang="zh") -> bytes | None   # reportlab 缺失 → None（调用方回退 HTML）
def report_snapshot(report) -> dict                # 只含指标，不含记录原文
```

### 4.11 `agent_explainer/suggestions.py`（负责人：features-analysis）

```python
SUGGESTIONS_VERSION = "1.0"
DEFAULT_THRESHOLD = 3
COOLDOWN_DAYS = 7
class SuggestionError(Exception)
class SuggestionService:
    def __init__(self, paths: FeaturePaths, *, now=None, enabled=True) -> None
    def scan(self, records, *, user_id) -> dict        # 幂等：重算 + 落盘
    def observe(self, record, *, user_id) -> dict | None
    def list(self, *, user_id, status=None) -> list[dict]
    def get(self, suggestion_id) -> dict | None
    def accept(self, suggestion_id, *, user_id) -> dict
    def dismiss(self, suggestion_id, *, user_id, reason="") -> dict
    def preferences(self, *, user_id) -> list[dict]
    def set_preference(self, *, user_id, tool, param, value, source="manual") -> dict
    def preference_for(self, *, user_id, tool, param) -> dict | None
    def set_enabled(self, enabled: bool) -> None
```

### 4.12 `agent_explainer/compliance.py`（负责人：features-analysis）

```python
COMPLIANCE_VERSION = "1.0"
FORMATS = ("pdf", "csv", "chain")
class ComplianceError(Exception)
def cid_for(data: bytes) -> str        # IPFS CIDv1 (raw, sha2-256, base32) —— 离线可复算
class ComplianceService:
    def __init__(self, paths: FeaturePaths, *, export_dir=None, now=None) -> None
    def build_bundle(self, *, records, audit_entries, letters=None, period=None) -> dict
    def export(self, *, records, audit_entries, letters=None, period=None,
               format="pdf", lang="zh", user_id="local") -> dict
    def attest(self, export_id, *, user_id="local") -> dict   # cid + anchor_state
    def verify(self, export_id) -> dict
    def list(self) -> list[dict]
    def get(self, export_id) -> dict | None
    def file_path(self, export_id) -> pathlib.Path | None
```

---

## 5. HTTP 路由（负责人：integrator）

统一：JSON 包裹与既有端点一致；写操作 `POST` + CSRF；`detail` 里给人话。
权限列用既有 8 个能力（**不新增能力**，避免动 `auth.py` 角色矩阵与三处断言）。

| # | 端点 | 方法 | 权限 | 入参 / 响应要点 | 专属错误码 |
| --- | --- | --- | --- | --- | --- |
| T1 | `/api/secretariat/status` | GET | `audit.read` | 身份数/委托数/认定书数/链状态/风险策略表/`enabled` | — |
| T2 | `/api/secretariat/identities` | GET/POST | 读 `audit.read`；写 `settings.write` | POST `{agent_id, owner_did, image_hash?, boot_params?, kind?}` → 201 | `SECRETARIAT_FIELD_INVALID` |
| T3 | `/api/secretariat/identities/{agent_id}/revoke` | POST | `settings.write` | `{reason?}` | `SECRETARIAT_IDENTITY_NOT_FOUND` |
| T4 | `/api/secretariat/delegations` | GET/POST | 同上 | POST `{delegator_did, delegatee_did, scope, ttl_seconds?, parent_id?}` | `SECRETARIAT_SCOPE_WIDENED` `SECRETARIAT_DEPTH_EXCEEDED` `SECRETARIAT_DELEGATION_EXPIRED` `SECRETARIAT_SCOPE_INVALID` |
| T5 | `/api/secretariat/records/{id}/sign` | POST | `settings.write` | 用该记录的 `agent_id` 签名并落库；返回签名后的记录 | `SECRETARIAT_IDENTITY_MISSING` |
| T6 | `/api/secretariat/verify` | POST | `audit.read` | `{record?|records?}` → 逐条 `{ok,checks,problems}` | — |
| T7 | `/api/secretariat/letters` | GET/POST | 读 `audit.read`；写 `settings.write` | POST `{subject, record_ids[], period?, responsibility?, note?}` | `SECRETARIAT_RECORD_MISSING` `SECRETARIAT_LETTER_EMPTY` |
| T8 | `/api/secretariat/letters/{id}` | GET | `audit.read` | 正文 + 验签结果 | `SECRETARIAT_LETTER_NOT_FOUND` |
| T9 | `/api/secretariat/letters/{id}/verify` | POST | `audit.read` | 离线复算 | 同上 |
| T10 | `/api/secretariat/seal` | POST | `settings.write` | `{record_id, reason?}` | `SECRETARIAT_FIELD_INVALID` |
| T11 | `/api/rollback` | POST | `intervene.decide` | `{action_id, reason?, confirm_all?}` → `{plan, rollback_record, affected}` | `ROLLBACK_NOT_FOUND` `ROLLBACK_NOT_REVERSIBLE` `ROLLBACK_DEPENDENTS_PENDING` `ROLLBACK_ALREADY_ROLLED_BACK` `ROLLBACK_INVERSE_FAILED` |
| T12 | `/api/rollback/plan` | GET | `records.read` | `?action_id=` → 计划 | `ROLLBACK_NOT_FOUND` |
| T13 | `/api/records/{id}/evidence` | GET/POST | 读 `records.read`；写 `data.write` | POST `{kind, payload, caption?}` 或 `{kind:"diff",before,after}` | `EVIDENCE_KIND_INVALID` `EVIDENCE_PAYLOAD_INVALID` `EVIDENCE_TOO_LARGE` `EVIDENCE_WRITE_FAILED` |
| T14 | `/api/evidence/{id}` | GET | `records.read` | 元数据 | `EVIDENCE_NOT_FOUND` |
| T15 | `/api/evidence/{id}/file` | GET | `records.read` | 字节流（正确 `Content-Type`，`Cache-Control: no-store`） | `EVIDENCE_NOT_FOUND` |
| T16 | `/api/evidence/{id}/feedback` | POST | `intervene.decide` | `{vote:"like"|"dislike"}` | `EVIDENCE_VOTE_INVALID` |
| T17 | `/api/records/{id}/causal` | POST | `data.write` | `{candidates?, lang?}` → 写回 `causal_explanation` | `EVIDENCE_PAYLOAD_INVALID` |
| T18 | `/api/groups` | GET/POST | 读 `records.read`；写 `data.write` | POST `{title, agent_ids[], objective?, group_id?}` | `GROUP_FIELD_INVALID` `GROUP_AGENTS_EXCEEDED` |
| T19 | `/api/groups/{id}` | GET | `records.read` | 聚合视图（记录按时间合并 + 进度 + 资源 + 卡住的 Agent） | `GROUP_NOT_FOUND` |
| T20 | `/api/groups/{id}/close` | POST | `data.write` | `{status?}` | `GROUP_NOT_FOUND` |
| T21 | `/api/groups/{id}/share` | POST | `data.write` | `{permission, invitee_did?, ttl_seconds?}` → 明文链接一次 | `SHARE_PERMISSION_INVALID` `GROUP_NOT_FOUND` `SHARE_FORBIDDEN` |
| T22 | `/api/shares` | GET | `records.read` | `{issued[], shared_with_me[]}` | — |
| T23 | `/api/shares/{id}/revoke` | POST | `data.write` | `{reason?}` | `SHARE_NOT_FOUND` `SHARE_FORBIDDEN` |
| T24 | `/api/shares/accept` | POST | `records.read` | `{token}` → 绑定当前 actor | `SHARE_TOKEN_INVALID` `SHARE_EXPIRED` `SHARE_TOKEN_USED` |
| T25 | `/api/shares/permissions` | GET | `records.read` | `?group_id=` → `{can_view,can_approve,can_manage,via}` | `GROUP_NOT_FOUND` |
| T26 | `/api/analytics/dashboard` | GET | `audit.read` | `?from=&to=&agent=&granularity=day|week&salt=` | `ANALYTICS_RANGE_INVALID` |
| T27 | `/api/analytics/export` | GET | `audit.export` | `?format=csv|pdf&...` → 文件字节流 | `ANALYTICS_FORMAT_INVALID` |
| T28 | `/api/suggestions` | GET | `records.read` | `?status=` | — |
| T29 | `/api/suggestions/{id}/accept` | POST | `intervene.decide` | 生成 UserPreference | `SUGGESTION_NOT_FOUND` `SUGGESTION_STATE_INVALID` |
| T30 | `/api/suggestions/{id}/dismiss` | POST | `intervene.decide` | `{reason?}` → 7 天冷却 | `SUGGESTION_NOT_FOUND` |
| T31 | `/api/preferences` | GET/POST | 读 `records.read`；写 `intervene.decide` | POST `{tool,param,value,source?}` | `PREFERENCE_INVALID` |
| T32 | `/api/compliance/exports` | GET | `audit.export` | 列表 | — |
| T33 | `/api/compliance/export` | POST | `audit.export` | `{format, from?, to?, scope?, lang?}` → ExportRecord | `COMPLIANCE_FORMAT_INVALID` `COMPLIANCE_CHAIN_BROKEN` `COMPLIANCE_BUNDLE_EMPTY` |
| T34 | `/api/compliance/exports/{id}` | GET | `audit.export` | 记录 + 校验结果 + 是否可下载 | `COMPLIANCE_NOT_FOUND` |
| T35 | `/api/compliance/exports/{id}/file` | GET | `audit.export` | 字节流 | `COMPLIANCE_NOT_FOUND` |
| T36 | `/api/compliance/exports/{id}/attest` | POST | `audit.export` | CID + `anchor_state` | `COMPLIANCE_ANCHOR_UNAVAILABLE` |
| T37 | `/api/compliance/exports/{id}/verify` | GET | `audit.export` | 复算文件 `sha256` + 链校验 | `COMPLIANCE_NOT_FOUND` |
| T38 | `/health` 增补 | GET | 公开 | `expansion` 块（各子系统 `enabled`/落盘状态/问题数） | — |

### 5.1 响应形状（与既有端点一致：**平铺 JSON，不套 data 信封**）

既有端点返回 `{"records": [...], "meta": {...}}` / `{"entries": [...], "chain": {...}}`，
5.0 的新端点沿用同一形状：**`{"ok": true, <具名载荷>}`**，列表用具名复数键，前端读
`res.data.<键>`（`ctx.call` 已经把响应体放进 `data`）。逐条：

| 端点 | 成功响应键 |
| --- | --- |
| T1 status | `ok, secretariat` |
| T2 identities | `ok, identities, count` / POST：`ok, identity` |
| T3 revoke | `ok, identity` |
| T4 delegations | `ok, delegations, count, max_depth` / POST：`ok, delegation` |
| T5 sign | `ok, record, verification` |
| T6 verify | `ok, results[], summary{total,ok,failed}` |
| T7 letters | `ok, letters, count` / POST：`ok, letter, verification` |
| T8 letter | `ok, letter, verification` |
| T9 letter verify | `ok, verification` |
| T10 seal | `ok, seal` |
| T11 rollback | `ok, plan, rollback_record, affected[], original, inverse` |
| T12 rollback plan | `ok, plan` |
| T13 record evidence | GET `ok, record_id, evidence[], count`；POST `ok, evidence, record` |
| T14 evidence | `ok, evidence` |
| T15 evidence file | 字节流（`Content-Type` = 证据的 media_type） |
| T16 feedback | `ok, evidence` |
| T17 causal | `ok, causal_explanation, record` |
| T18 groups | GET `ok, groups, count`；POST `ok, group` |
| T19 aggregate | `ok, group, records, agents, progress, resources, window, stuck` |
| T20 close | `ok, group` |
| T21 share | `ok, invite, token, url, note`（`token` 只在这一次出现） |
| T22 shares | `ok, issued, shared_with_me, count` |
| T23 revoke share | `ok, invite` |
| T24 accept share | `ok, invite, group_id` |
| T25 permissions | `ok, group_id, can_view, can_approve, can_manage, via` |
| T26 analytics | `ok, analytics{range,granularity,total_tasks,total_interventions,intervention_rate,avg_approval_seconds,avg_approval_ms,trust_health,users,trend[],heatmap[],top_parameters[],top_rejected[],generated_at}` |
| T27 analytics export | 字节流（csv / pdf） |
| T28 suggestions | `ok, suggestions, count, enabled` |
| T29 accept suggestion | `ok, suggestion, preference` |
| T30 dismiss suggestion | `ok, suggestion`（含 `cooldown_until`） |
| T31 preferences | GET `ok, preferences, count`；POST `ok, preference` |
| T32 exports | `ok, exports, count` |
| T33 export | `ok, export` |
| T34 export detail | `ok, export, verification` |
| T35 export file | 字节流 |
| T36 attest | `ok, export, attestation{cid, anchor_state, eth_tx_hash, checked_at, note}` |
| T37 verify export | `ok, verification{sha256_match, chain_ok, problems, anchor_state}` |

错误一律是既有统一错误体：`{code, message, detail, request_id}`（HTTP 非 2xx）。

**记录级挂接（integrator 负责）**：导入/上报/重置写入记录时依次应用
`confidence.apply_to_record` → `secretariat.sign_record`（`--no-secretariat` 可关）→
保留 `group_id` / `shared_users` / `multimedia_evidence` 等新字段（`MERGE_FIELDS` 扩展）。
**受保护字段**（`user_intervention` / `intervention_options` / `human_readable` / `source` /
`demo_data` / `synthetic` / `policy`）口径不变。

---

## 6. 前端扩展契约（`web/ext/*`）

### 6.1 机制

- `index.html` 已按顺序引入 `ext/core.js` 与五个功能文件及各自 CSS（**新增文件不用再改
  `index.html`**；只在 `web/ext/` 内增删由各功能负责人决定，且必须同步 `index.html` 的
  负责人只有 lead）。
- `app.js` 暴露的 5.0 接口（已实现）：`TraceLensUI.onRender(fn)`（每次整页重绘后回调，
  参数是内部 `state`：`records` 等）、`t(key,params)`、`lang()`、`formatTimestamp()`、
  `formatConfidence()`、`zoneOf()`、`recordById()`、`renderAll()`、
  `extRequest(target, body, opts)`（唯一请求门：CSRF/超时/错误体统一）、
  `authInfo()`、`can(permission)`。
- `ext/core.js` 提供 `window.TraceLensExt`：`register(plugin)` / `open(tabId)` / `close()` /
  `toggle()` / `t()` / `ctx` / `state()`。插件形状：

```js
TraceLensExt.register({
  id: 'secretariat',                     // 唯一
  version: '1.0',
  tab: {                                  // 面板页签（可选）
    titleKey: 'secretariat.tab',          // 字典 key
    order: 10,                            // 排序
    permission: 'audit.read',             // 缺失则页签不出现
    render: function (host, ctx) {},      // 首次进入该页签时渲染
    repaint: function (host, ctx) {}      // （可选）每次整页重绘后刷新
  },
  decorate: function (cardEl, record, ctx) {},  // （可选）卡片装饰，必须幂等
  onState: function (state, ctx) {},            // （可选）每次重绘后的轻量通知
  boot: function (ctx) {}                       // （可选）一次性初始化
});
```

### 6.2 `ctx` 表面（core.js 提供）

`t / stamp / lang / can / call / get / post / el / add / clear / textNode / esc /
fmtTime / fmtNumber / fmtPercent / fmtBytes / shortHash / toast / openModal / closeModal /
confirmDialog / renderLoading / renderError / renderEmpty / panelError / open / close /
showTab / refresh / download / records / recordById / apiState`。

请求约定：`ctx.get('/api/groups', {status:'open'})`、`ctx.post('/api/rollback', {...})`
返回 `{ok,status,data,code,message,requestId}`；**任何失败都必须用
`ctx.renderError(host, ctx.panelError(res, retry))` 呈现**，禁止白屏、禁止把服务端原文
当产品文案（`message` 可以显示，但要带请求编号）。

### 6.3 文件与页签归属

| 文件 | 页签 / 装饰 | 负责人 |
| --- | --- | --- |
| `ext/core.js` + `core.css` | 壳（启动器 / 面板 / toast / modal / 请求门） | lead（已完成，改动须通知） |
| `ext/cards.js` + `cards.css` | 卡片装饰：置信度圆点+脉冲、回滚按钮+确认+删除线、证据缩略图+放大+"为什么"气泡+点赞/点踩 | frontend-core |
| `ext/secretariat.js` + `.css` | 总裁办页签（身份/委托/认定书/验签/盖章/策略表） | frontend-panels |
| `ext/collab.js` + `.css` | 多 Agent 聚合视图 + 共享与协作 | frontend-panels |
| `ext/insights.js` + `.css` | 用户行为分析仪表盘 + 自动化改进建议 | frontend-data |
| `ext/compliance.js` + `.css` | 合规审计导出（格式/时间范围/进度/CID/交易哈希/校验） | frontend-data |

### 6.4 i18n 工作流（每个前端成员都要用）

1. 写 `agent_explainer/web/i18n/parts/<feature>.zh.json` 与 `<feature>.en.json`：
   `{"<域>": {"<域名之后的后缀>": "文案"}}`（例 `{"secretariat": {"tab": "总裁办"}}` → key `secretariat.tab`）。
2. 运行 `python tools/i18n_merge.py`（会校验双语一致/英文零中文/占位参数，并重生成 `embed.js`）。
3. 代码里只用 `ctx.t('secretariat.tab')` 引用；节点用 `ctx.stamp(node, key)` 打
   `data-i18n`（自检与 lint 靠它断言"没有残留中文"）。
4. **禁止**在 JS/CSS/HTML 里写中文或整句英文；`json` 片段文件除外。

**新增域**：`trust`（壳，已完成）、`secretariat`、`rollback`、`confidence`、`evidence`、
`groups`、`sharing`、`analytics`、`suggestions`、`compliance`。
错误码文案键：`<域>.error.<code 小写>`（例 `rollback.error.rollback_not_reversible`）。

---

## 7. 错误码清单（新增，需 i18n 双语键 + 至少一条测试）

| 域 | 码 |
| --- | --- |
| `secretariat` | `SECRETARIAT_FIELD_INVALID` `SECRETARIAT_IDENTITY_NOT_FOUND` `SECRETARIAT_IDENTITY_MISSING` `SECRETARIAT_IDENTITY_REVOKED` `SECRETARIAT_DELEGATION_INVALID` `SECRETARIAT_SCOPE_INVALID` `SECRETARIAT_SCOPE_WIDENED` `SECRETARIAT_DEPTH_EXCEEDED` `SECRETARIAT_DELEGATION_EXPIRED` `SECRETARIAT_SIGNATURE_INVALID` `SECRETARIAT_APPROVAL_REQUIRED` `SECRETARIAT_LETTER_NOT_FOUND` `SECRETARIAT_RECORD_MISSING` `SECRETARIAT_LETTER_EMPTY` `SECRETARIAT_KEYSTORE_UNAVAILABLE` `SECRETARIAT_WRITE_FAILED` |
| `rollback` | `ROLLBACK_NOT_FOUND` `ROLLBACK_NOT_REVERSIBLE` `ROLLBACK_DEPENDENTS_PENDING` `ROLLBACK_ALREADY_ROLLED_BACK` `ROLLBACK_INVERSE_FAILED` `ROLLBACK_FIELD_INVALID` |
| `evidence` | `EVIDENCE_KIND_INVALID` `EVIDENCE_PAYLOAD_INVALID` `EVIDENCE_TOO_LARGE` `EVIDENCE_NOT_FOUND` `EVIDENCE_VOTE_INVALID` `EVIDENCE_WRITE_FAILED` |
| `groups` | `GROUP_NOT_FOUND` `GROUP_FIELD_INVALID` `GROUP_CLOSED` `GROUP_AGENTS_EXCEEDED` `GROUP_WRITE_FAILED` |
| `sharing` | `SHARE_NOT_FOUND` `SHARE_PERMISSION_INVALID` `SHARE_EXPIRED` `SHARE_TOKEN_INVALID` `SHARE_TOKEN_USED` `SHARE_FORBIDDEN` `SHARE_WRITE_FAILED` |
| `analytics` | `ANALYTICS_RANGE_INVALID` `ANALYTICS_FORMAT_INVALID` `ANALYTICS_EXPORT_FAILED` |
| `suggestions` | `SUGGESTION_NOT_FOUND` `SUGGESTION_STATE_INVALID` `SUGGESTION_FIELD_INVALID` `PREFERENCE_INVALID` `SUGGESTION_WRITE_FAILED` |
| `compliance` | `COMPLIANCE_FORMAT_INVALID` `COMPLIANCE_CHAIN_BROKEN` `COMPLIANCE_NOT_FOUND` `COMPLIANCE_EXPORT_FAILED` `COMPLIANCE_ANCHOR_UNAVAILABLE` `COMPLIANCE_BUNDLE_EMPTY` |
| `trust` | `FEATURE_STORE_LOCKED` `FEATURE_STORE_WRITE_FAILED` `FEATURE_STORE_READ_FAILED` `FEATURE_STORE_LINE_TOO_LARGE` `FEATURE_STORE_RECORD_INVALID` `FEATURE_JSON_INVALID` |

HTTP 映射：字段/参数类 400；不存在 404；状态冲突（已回滚/已判定/已过期）409；
越权 403；体积 413；存储失败 500。**断链一律 500 且不出证据**（沿用 M6 口径）。

---

## 8. 测试与验收

### 8.1 每个模块自带单测（`tests/test_<module>.py`，stdlib `unittest`）

最低要求：正常路径、边界、错误码、幂等、落盘后重读、并发/损坏行容错（存储类）。
数据目录一律 `tempfile.TemporaryDirectory()`，**绝不写仓库 `var/`**。

### 8.2 规格书"验收标准"→ 断言映射（QA 汇总到 `tests/test_expansion_acceptance.py`）

| 规格书条目 | 断言 |
| --- | --- |
| 功能一：可回滚步骤覆盖率 ≥ 90% | `RollbackService.coverage(demo_records)["ratio"] >= 0.9` |
| 功能一：回滚后状态与回滚前一致 | journal 快照 `==` 回滚后记录字段 |
| 功能一：回滚记录签名有效、哈希链连续 | `secretariat.verify_record(rollback_record)["ok"]` + `prev_hash` 衔接 |
| 功能一：确认弹窗响应 ≤ 1s | 前端：`confirmDialog` 在同帧返回（无网络调用）；记 `performance.now()` |
| 功能二：置信度来源可追溯 | `record.confidence_source.method ∈ {logprobs,tool_success_rate,hybrid,template,default}` |
| 功能二：颜色映射符合区间 | `band(0.95)=="green"`、`band(0.8)=="light_green"`、`band(0.6)=="yellow"`、`band(0.4)=="red"` |
| 功能二：低置信自动触发干预 | `confidence < 0.5 → requires_action is True` 且至少 3 个干预选项 |
| 功能三：多媒体生成成功率 ≥ 95% | 100 次 evidence 生成（三种 kind）成功 ≥ 95 |
| 功能三：签名覆盖证据哈希 | 篡改证据文件 → `verify_record` 失败 |
| 功能四：同组 2s 内聚合 | `aggregate(1000 条)` 计时 < 2.0s |
| 功能四：最多 10 Agent 并发 | `create(agent_ids=10)` 通过；11 个 → `GROUP_AGENTS_EXCEEDED` |
| 功能四：分组准确率 ≥ 99% | `grouping_report()["accuracy"] >= 0.99` |
| 功能五：链接有效期内可用、过期失效 | `accept` 成功 / 过期 → `SHARE_EXPIRED` |
| 功能五：view 无法点击干预 | `sharing.can(actor,"approve",gid)["allowed"] is False` + 服务端 403 |
| 功能五：协作操作完整可审计 | `collab_events` 链 `verify()["ok"]` + 每条含 actor/action/target/at/signature |
| 功能六：指标与原始数据核对 | 手工构造 10 条记录，逐项比对 `total/interventions/rate/avg_time/top_params` |
| 功能六：百万记录内加载 ≤ 3s | 20 万条基准（CI 放宽到 6s，报告实测值） |
| 功能六：支持导出 PDF/CSV | CSV 有表头且可被 `csv.DictReader` 读；PDF 以 `%PDF` 开头（reportlab 存在时） |
| 功能七：3 次后第 4 次前弹出 | 第 3 次 `scan` 后列表出现建议；第 4 条记录出现时建议已存在 |
| 功能七：采纳后自动使用偏好 | `preference_for()` 返回采纳值；记录写入时带 `applied_preference` |
| 功能七：拒绝后 7 天不重复 | `cooldown_until = now + 7d`；期间 `scan` 不重新生成 |
| 功能八：PDF 美观、二维码可扫 | PDF 内含 QR SVG/矢量；`qr.decode` 回读 == 根哈希 |
| 功能八：CSV 可导入 Splunk/ELK | 固定表头 + RFC4180 引号 + UTF-8 BOM 可选 |
| 功能八：上链 ≤ 2 分钟 | **离线降级**：`cid_for()` 本地可算（真实 CIDv1）；`anchor_state="unanchored"` 并写明原因 |
| 功能八：验证脚本可独立运行 | `python -m agent_explainer.compliance verify <file>` 或 `tests` 独立复算 |

### 8.3 三层复核（lead 收口）

1. `python -m unittest discover -s tests -v`（全量，基线 754 项 / 2 项环境性错误，见 §10）
2. `python tools/i18n_lint.py` + `python tools/i18n_merge.py --check` +
   `python tools/gen_api_contract.py --check`
3. 浏览器实测：启动服务 → 打开页面 → 逐个页签截图（桌面 + 手机视口）

---

## 9. 文件归属（写作用域，互不重叠）

| 负责人 | 写作用域（只改这些） |
| --- | --- |
| lead | `agent_explainer/feature_store.py`、`web/ext/core.js`、`web/ext/core.css`、`web/index.html`、`web/app.js`（5.0 接口）、`tools/i18n_merge.py`、`web/i18n/parts/core.*`、`docs/07-*.md` |
| crypto-core | `agent_explainer/crypto_sig.py`、`agent_explainer/qr.py`、`tests/test_crypto_sig.py`、`tests/test_qr.py` |
| secretariat | `agent_explainer/secretariat.py`、`tests/test_secretariat.py` |
| features-core | `agent_explainer/rollback.py`、`confidence.py`、`evidence.py` + 三个测试 |
| features-collab | `agent_explainer/groups.py`、`sharing.py` + 两个测试 |
| features-analysis | `agent_explainer/analytics.py`、`suggestions.py`、`compliance.py` + 三个测试 |
| integrator | `agent_explainer/server.py`、`tests/test_expansion_api.py`、`docs/api_contract.json`、`tools/gen_api_contract.py` |
| frontend-core | `web/ext/cards.js`、`web/ext/cards.css`、`web/i18n/parts/cards.*` |
| frontend-panels | `web/ext/secretariat.js`、`collab.js`、对应 2 个 CSS、2 个 parts 文件 |
| frontend-data | `web/ext/insights.js`、`compliance.js`、对应 2 个 CSS、2 个 parts 文件 |
| qa | `docs/schemas/action_record.schema.json`、`docs/02-data-model.md`、`docs/03-architecture.md`（增量节）、`tools/i18n_lint.py`、`tests/test_expansion_acceptance.py`、`tests/test_expansion_i18n.py`、`README.md`、`CHANGELOG.md` |

共享文件约定：`server.py` / `app.js` / `docs/` / `tools/` / i18n 字典**只有一个负责人**；
其他人只读。需要改动时用 `send_message` 提给负责人。

---

## 10. 本轮显式降级与已知限制（必须在交付报告里出现）

| 规格书要求 | 本轮实际 | 为什么 |
| --- | --- | --- |
| HSM / TEE 提供 Ed25519 密钥 | 纯 Python Ed25519 + 本地密钥库（`keyring.json` 0600） | 离线与零依赖约束；密钥库是可替换的抽象（生产环境应换 TEE/HSM 提供 seed） |
| RFC 3161 可信时间戳 | `tsa_timestamp = "local-tsa:<ISO>"`，`verify` 标 `tsa_state="local_only"` | 离线无 TSA 服务；**禁止**界面宣称"可信时间戳" |
| 截图 API 抓屏 | 由执行端/用户提供图片（`data_uri`），服务端校验魔数并落盘 | 无网络、无浏览器自动化；`provided_by` 字段如实标注来源 |
| Graphviz / Mermaid 渲染 | 服务端生成 Mermaid 文本 + 自绘 SVG（纯标准库） | 不引入第三方渲染器 |
| 区块链上链（IPFS + 以太坊） | `cid_for()` 真实可复算 CIDv1；`anchor_state ∈ {unanchored, anchored}`，默认 `unanchored` | 无网络、无钱包；**禁止**伪造交易哈希（产品纪律：决定 ≠ 生效） |
| 回滚真的撤销外部系统影响 | 回滚记录 + 本地逆向动作回执 `simulated_local` | 没有外部系统凭据；回执状态如实显示 |
| 百万记录 ≤3s | 内存聚合实测（记录量级产品定位为每天几十~几千条）；报告实测值 | JSONL + 内存索引的既定架构 |
| 桌面 GUI（Electron 安装包）重新打包 | 不重打包；源码形态下界面实测通过 | 打包需要网络/签名链，超出本轮；旧包留在 `release/` |

**"做不到就说做不到"是本产品的核心文案纪律**（README §0、`docs/03` §6.4）。任何面板
不得把上述降级项显示成已具备的能力。
