# R3 独立复核：行为签名与哈希链（后端工程师口径）

> 复核日期：2026-11-12 · 规格依据：[docs/12 §1/§2/§4/§5/§7](../12-secretariat-delta-spec.md)
> （A 表字段口径、B 表 11 签名域、D 表审批策略与审批签 payload、E 表认定书、G 表 A1/A2/A6/A7/A8/A10/A11）
> + [docs/13 §2](../13-r2-r3-production-report.md) + [docs/schemas/action_record.schema.json](../schemas/action_record.schema.json)
> + [docs/02 §2.8](../02-data-model.md) + [docs/audit/r0-spec-conformance.md](r0-spec-conformance.md) + [docs/audit/r2-review.md](r2-review.md)
> 写作用域：`agent_explainer/secretariat.py`、`docs/schemas/action_record.schema.json`（未改）、
> `docs/02 §2.8`、`docs/07 §3.1`、`tests/test_secretariat*.py`、`tools/verify_letter.py`（未改）、本报告。
> **未动** `server.py` / `web`。探针与认定书都在 `.tmp`（垫片不进交付物）。
> 复核对象：`agent_explainer/secretariat.py`（1724 行）签名/审批/链/认定书面 + `tools/verify_letter.py`（156 行）

## 0. 结论

**PASS（有条件）** —— 密码学与契约行为成立，4 项缺口本轮闭合，剩余都是"口径要在文档收口"的条目，无 blocker。

- **必做 1 全绿**：`.tmp/r3_audit.py` 共 **60 条断言 60 PASS / 0 FAIL（exit 0）**，覆盖四档签名 + schema
  `errors=[]`、11 个签名域逐字段篡改全部验不过、同 DID 双人审批被拒、同一审批人两条记录审批签名不同。
- **必做 2 全绿**：用 `samples/action_records.json` 六条记录（接成一条链）生成认定书 →
  `tools/verify_letter.py` **PASS / exit 0**；篡改 `actions[0].timestamp` → **FAIL / exit 1**
  （认定书本体签名 + 该条动作签名双双验不过）。
- **必做 3 全绿**：`python -m unittest tests.test_secretariat tests.test_secretariat_contract -q` =
  `Ran 67 tests` / `OK` / `exit 0`；四套总裁办 49 + 18 + 7 + 4 = **78 条全绿**。
- **本轮修 5 项**（§4）：三处 schema 上限在签名前无人执行（可产出"已签名但不合规"的记录）、
  `prev_hash` 连续性检查事实上永远验不上、认定书缺 `audit_head` 时未按要求登记。
  全部复用既有 `SECRETARIAT_FIELD_INVALID`（**不新增错误码**），并同步 `docs/02 §2.8` / `docs/07 §3.1` 口径。
- **放行条件**：§5 的三条文档收口（A1 的 `tsa_timestamp` 边界、A7 的 `record_id` 前提、
  `docs/13:87` 审批码口径）+ R2 已单列的状态码偏差（B04/B06）由 lead 在收口时改文档，不改运行时。
- 全量 `discover`：**934 条 = 1 条既有已知红**（`test_e2e.ArtifactTests.test_signature_status_is_honest`，
  r0 已登记根因），**非本轮引入**。

---

## 1. 必做 1 的证据（签名域 / 审批 / 四档）

复跑：`python .tmp\r3_audit.py --letter`（cwd=agent-explainer，TMP/TEMP/PYTHONPATH 垫片已设）→
`SUMMARY total=60 pass=60 fail=0`，`EXIT=0`。时钟固定 `2026-11-11T10:00:00Z`（同一审批时钟）。

### 1.1 A1 · 11 个签名域逐字段篡改（`SIGNATURE_FIELDS`）

签名域实测 = `(timestamp, agent_id, action_type, tool, input_hash, output_hash, prev_hash,
entry_hash, risk_level, confidence, status)`，`len=11`（与 docs/12 §2:79-80 逐字一致）。

| 篡改字段 | 实测（`verify_record`） | 判定 |
| --- | --- | --- |
| `timestamp` / `agent_id` / `action_type` / `tool` / `input_hash` / `output_hash` / `prev_hash` / `risk_level` / `confidence` / `status` | `ok=False`，`signature=invalid`，`entry_hash=invalid`，`codes=[SIGNATURE_INVALID, HASH_MISMATCH, SIGNATURE_INVALID(approval)]` | 成立 |
| `entry_hash` | `ok=False`，`signature=invalid`，`entry_hash=invalid`，`codes=[SIGNATURE_INVALID, HASH_MISMATCH]` | 成立 |

11/11 全部验不过（`[PASS] A1 篡改签名域 <字段>` × 11）。补测：

| 补测 | 实测 | 判定 |
| --- | --- | --- |
| 非签名域 `human_owner` / `id` / `result_summary` / `intent` 篡改 | `entry_hash=invalid`（`signature=valid`）→ `ok=False` | 成立（docs/12 §1:63：entry_hash 覆盖除 `signature`/`tsa_timestamp`/`entry_hash` 外整条记录） |
| 责任链根尾互换 | `chain=invalid` + `entry_hash=invalid` | 成立 |
| `tsa_timestamp` 改成 RFC3161 样式串 | `tsa=unknown`，`ok=True` | **边界**：该字段不在 entry_hash 域（规格冻结），唯一防线是降级标注 `local_only→unknown`；见 §5-1 |
| 链上改 `prev_hash` 为另一条已知哈希 | `ok=False`（`signature=invalid`；连续性标 `continuous`） | 成立：连续性只是旁证，**密码学由签名域兜住**（`prev_hash` 在 11 域内） |

### 1.2 A6 / A7 / A8 · 审批

| 断言 | 实测 | 判定 |
| --- | --- | --- |
| critical 同 DID 两次 | `400 SECRETARIAT_FIELD_INVALID field=approvers detail={'required':2,'given':1,'supplied':2,'reason':'duplicate approver DID'}` | 成立（Q5；t2 已收口，docs/12:239 A6 字面成立） |
| critical 两个不同 user DID | `required=2`、`sealed=True`、两个 DID，`verify_record.ok=True`、schema `errors=[]` | 成立 |
| high 单人 | `required=1`、`sealed=False`（盖章只属于 critical） | 成立（D 表） |
| low / medium | 记录里**不写** `approval`（`required=0` 不写空对象） | 成立 |
| 同一审批人 + 同一时钟 + 两条记录 | 审批签名 `RTVfWg5QhfqC…` ≠ `Uepomy6IPBPt…`（`record_id` 不同） | 成立（A7：payload 含 `record_id`/`input_hash`/`output_hash`/`risk_level`/`required`） |
| 把 A 的审批签搬进 B | `approval.signatures_ok=False` + `HASH_MISMATCH` → `ok=False` | 成立 |
| 伪造审批签名（64 字节 0） | `problems` 含 `SECRETARIAT_SIGNATURE_INVALID` 且 `detail.scope="approval"` | 成立（A8） |
| 审批条目 `did` / `role` / `signed_at` 改写 | 三条都触发 `scope="approval"` 验签失败（+ `entry_hash=invalid`） | 成立：payload 的 9 个键全部参与绑定 |
| 盖章 `digest` | `seal.digest == approval_digest(record)`（10 域归约，排除 `entry_hash` 防自引用） | 成立 |
| 把 A 的章搬进 B | `seal=invalid`（`digest_ok=False`） | 成立 |
| critical 去章 / `sealed=true` 无 `seal` 对象 | `seal=absent` + `APPROVAL_REQUIRED` / `seal=invalid` | 成立 |

### 1.3 B 表 · 哈希链

| 断言 | 实测 | 判定 |
| --- | --- | --- |
| 链根 `prev_hash` = 64 个 `0`，逐条接上一条 `entry_hash` | `prev=['000000000000','91c0aa19ce6f','2404e861258a']`，`entry=['91c0aa19ce6f','2404e861258a','1e497462e835']` | 成立 |
| 链头 / 中间记录连续性 | `prev_hash=continuous`（**本轮修复后**；修复前中间与链头记录一律 `unverified`） | 已修（§4-4） |
| 链根记录 | `prev_hash=genesis` | 成立 |
| 换一台机器复算（无链头状态） | `signature=valid`、`identity=unregistered`、`prev_hash=unverifiable_without_chain` | 成立（记录自证，不假装连续性已验证） |
| 批量签名乱序入参 | 按 `timestamp` 排序接链，6 条链逐条 `verify.ok=True` | 成立 |

---

## 2. 必做 2 的证据（认定书离线复算）

生成：六条 `samples/action_records.json` 记录（`low/low/low/medium/high+1人/critical+2人`）接成一条链 →
`build_letter(subject="R3 独立复核 · 认定书", period=…, audit_head="")` → `.tmp/r3_letter.json`。

```text
$ python tools/verify_letter.py .tmp/r3_letter.json
认定书：R3 独立复核 · 认定书（letter_20261111162029_cb108b50）
  签名        : 通过
  records_root: 通过
  动作 act-demo-0001: 通过
  动作 act-demo-0002: 通过
  动作 act-demo-0003: 通过
  动作 act-demo-0004: 通过
  动作 act-demo-0008: 通过
  动作 act-demo-0005: 通过
  audit_head  : （未提供）
  tsa_state   : local_only
离线边界：审批签与身份绑定需身份账本片段；时间戳只有 local-tsa（无 RFC 3161）。
结论： PASS            # EXIT=0
```

篡改 `actions[0].timestamp = "2020-01-01T00:00:00Z"`（只改一个字段）：

```text
$ python tools/verify_letter.py .tmp/r3_letter_tampered.json
  签名        : 失败
  records_root: 通过
  动作 act-demo-0001: 失败
  动作 act-demo-0002..0005: 通过
问题：
  - {"code": "SECRETARIAT_SIGNATURE_INVALID", "detail": {"scope": "letter"}}
  - {"code": "SECRETARIAT_SIGNATURE_INVALID", "detail": {"scope": "action", "id": "act-demo-0001"}}
结论： FAIL            # EXIT=1
```

**关键观察**：`records_root` 在单字段篡改下仍"通过"——归一化只锚各条 `entry_hash`
（docs/12 §5:183），`timestamp` 变了 `entry_hash` 字段值本身没变。防线是另外两条：
该条动作签名（`timestamp` 在 11 域内）+ 认定书本体签名（正文含 `actions`）。两条都失败 → exit 1，
A10 成立；但 §7 A10 的措辞（"改动任一字段 → 非零退出"）建议补一句机制说明，见 §5-4。

认定书自身离线边界（工具与 `offline_boundary` 字段都如实写）：审批签与身份绑定需账本片段、
无 RFC 3161 → 不宣称可信时间戳。`tools/verify_letter.py` 本轮无需修改（复算口径与 `verify_letter` 一致，
两条实现的 root/action 复算逐字相同）。

---

## 3. schema 四档结果（A2 · `MiniValidator(schema).collect(signed) == []`）

| 档 | 入参 | `approval` 落地 | MiniValidator | `verify_record.ok` |
| --- | --- | --- | --- | --- |
| `low` | `act-demo-0001` | 字段缺席（不写空对象） | `errors=[]` | `True`（identity registered / signature valid / entry_hash valid / prev_hash genesis / tsa local_only / chain valid / approval required=0 given=0 ok=true） |
| `medium` | `act-demo-0004` | 字段缺席 | `errors=[]` | `True` |
| `high` | `act-demo-0008` + `approvers=[BOSS]` | `required=1`、1 个 user DID、`sealed=false` | `errors=[]` | `True`（approval `given=1 distinct=1 signatures_ok=true`） |
| `critical` | `act-demo-0005` + `approvers=[BOSS, CFO]` | `required=2`、两个**不同** user DID、`sealed=true` + `seal` 对象 | `errors=[]` | `True`（approval `given=2 distinct=2`、seal valid） |

形状实测：`agent_pubkey` 44 字符（pattern 合规）、`signature` 88 字符（≤128）、
`delegation_chain` 长度 2（≤8，每项过 `$defs/did`）、审批条目只含 `did/role/signed_at/signature`
（`additionalProperties:false` 成立）、`approval.required ≤2`。

---

## 4. 修复项（文件 + 行为 + 测试）

全部在写作用域内；**不新增错误码**（复用 `SECRETARIAT_FIELD_INVALID`，docs/12 §3 既有码族）；**未动 `server.py`**。

| # | 缺陷（严重度） | 文件 / 位置 | 改了什么行为 | 补的测试 |
| --- | --- | --- | --- | --- |
| 1 | **签名器不执行 `tool` 上限**：`tool_used.name` 长度 >120 时照抄进 `tool`，签出一条 `$.tool length 160 > 120` 的不合规已签名记录，且超限值进了签名域与 `entry_hash`（medium） | [secretariat.py:702-712](../../agent_explainer/secretariat.py#L702-L712) + 常量 [TOOL_NAME_MAX:104](../../agent_explainer/secretariat.py#L104) | 签前校验：`tool` 超 120 → `SECRETARIAT_FIELD_INVALID`/400（`field=tool`，`detail={length,limit}`）；**不裁值**（裁掉的值只出现在签名里、记录里看不见） | contract [test_tool_over_schema_limit_is_refused_before_signing](../../tests/test_secretariat_contract.py)（含 120 字符边界正例：签得出 + `errors=[]`） |
| 2 | **签名器不执行 `approvers[].role` 上限（40）**：`role="r"*60` 直接签进记录 → `$.approval.approvers[0].role length 60 > 40`（medium） | [secretariat.py:1556-1563](../../agent_explainer/secretariat.py#L1556-L1563) + [APPROVER_ROLE_MAX:102](../../agent_explainer/secretariat.py#L102) | 越界 `role` → `FIELD_INVALID`/400（`field=approvers`，`detail={role_length,limit,did}`） | contract `test_approver_role_over_schema_limit_is_refused`（含 40 字符边界正例） |
| 3 | **签名器不执行 `approvers` 数量上限（4）**：传 6 个不同 DID → 已签名记录违反 `maxItems: 4`，且为 6 个 DID 白白 mint 本地密钥（medium） | [secretariat.py:1531-1541](../../agent_explainer/secretariat.py#L1531-L1541) + [MAX_APPROVERS:100](../../agent_explainer/secretariat.py#L100) | **签前**按去重后的 DID 集合校验：>4 → `FIELD_INVALID`/400（`field=approvers`，`detail={unique,limit,risk_level}`）；不再生成多余用户密钥 | contract `test_too_many_approvers_are_refused`（含 4 个正例：`approvers=4`、`required=2`、`errors=[]`、`verify.ok=True`） |
| 4 | **`prev_hash` 连续性检查事实上验不上**：`_known_hashes` 只认"当前链头 + 身份账本 entry_hash"，链头记录自己的 `prev_hash` 永远不等于链头 → 实测全链只能报 `unverified`，`continuous` 分支基本不可达（medium，A1/B 表可验证性） | [secretariat.py:1476-1494](../../agent_explainer/secretariat.py#L1476-L1494) `_set_head` + [:1506-1513](../../agent_explainer/secretariat.py#L1506-L1513) `_known_hashes` + [HEAD_HISTORY:107](../../agent_explainer/secretariat.py#L107) | 链头记录里带上该 Agent 最近 `500` 条 `entry_hash`；连续性据此对**整条链**判 `continuous`；超出上限或换机器无状态时如实 `unverified` / `unverifiable_without_chain`（不新增文件、不改记录形状） | contract `test_prev_hash_continuity_is_verifiable_for_whole_chain`（链根 genesis、链头与中间 continuous、换机器 self-signed valid + `unverifiable_without_chain`）；探针 3 条 `[PASS] B …` |
| 5 | **认定书缺 `audit_head` 未登记**：docs/12:184-185 要求"拿不到时写 `""` 并在 `verification.problems` 里如实登记"，实测只写空串、不登记（low，诚实口径） | [secretariat.py:1302-1308](../../agent_explainer/secretariat.py#L1302-L1308) `build_letter` | 链头为空时在 `verification.problems` 追加 `{code: SECRETARIAT_FIELD_INVALID, detail:{scope:"audit_head", reason:"调用方未提供审计链头：留空并登记，不伪造"}}`（在签名之前写进正文 → 认定书签名自洽）；有链头时不多写一条 | contract `test_letter_registers_missing_audit_head`（空链头 → 登记项存在 + 认定书签名自验通过；有链头 → 无该登记项） |

**文档同步**（同一口径写进被指派的文档，免得只留在代码注释里）：
[docs/02 §2.8](../02-data-model.md) 新增"签名器对这些上限的执行口径（R3 落地）"段
（三上限 + 签前拒 + 不裁值理由 + 审批签 payload 绑定 + `HEAD_HISTORY` 连续性口径）；
[docs/07 §3.1](../07-secretariat-and-extensions.md) 同步一行提示并指回 docs/02 §2.8。

**测试账目**：`tests/test_secretariat_contract.py` 13 → **18**（新增 5 条；helper `approvers` 类型放宽为 `list[Any]`）。
`tests/test_secretariat.py` 本轮零改动（A1/A2/A6/A7/A8 在 R2/R3 交付时已有测试，本轮由探针独立复现）。

---

## 5. 契约偏差与建议（不改运行时，交 lead 收口时改文档）

1. **A1 的覆盖面要说清 `tsa_timestamp`**：docs/12:234 的 A1 只覆盖 11 个签名域；`tsa_timestamp`
   按 §1:63 被排除在 `entry_hash` 之外。实测把它改成 RFC3161 样式串 → `tsa=unknown` 且 `ok=True`。
   建议 A1 增注："`tsa_timestamp` 不靠哈希保护，由 `tsa_state` 降级标注兜住；
   界面遇到非 `local-tsa:` 值必须显示 `unknown`，禁止升格为可信时间戳。"（面板文案属 R4/A15）
2. **A7 需要一个前提**：docs/12:240"两条记录（同一审批人/同一时钟）的审批签名不相等"成立于
   `record_id` 不同。若两条记录 `id` 相同且事实字段相同（导入重复 id、复制记录），审批签名会逐字节相同，
   `seal` 同理（`seal.subject=id`、digest 用 10 域归约不含 `id`）。建议 A7 补"前提：`record_id` 唯一，
   由 ingest 去重/拒绝重复 id 保证"（该保证在 R4 的写路径上）。
3. **`docs/13:87` 审批码口径**：R2 收口后运行时是 `400 SECRETARIAT_FIELD_INVALID(field=approvers)`
   （探针已实测），docs/13 §2 那句"400 `SECRETARIAT_APPROVAL_REQUIRED`"要改；
   "一个审批人都没传"仍是 `409 APPROVAL_REQUIRED`。B04 / B06 状态码偏差沿用
   [r2-review.md §5](r2-review.md)，本轮同样**未改运行时**。
4. **A10 的措辞**：docs/12:243"改动任一字段 → 非零退出"成立，但机制要写清（§2 实测）：
   `records_root` 只锚 `entry_hash` 集合，单字段篡改由"逐条动作签名 + 认定书本体签名"检出。
   建议加一句，避免收口时只测 `records_root` 一项就宣称 A10 通过。
5. **码表补注**：本轮三处上限守卫复用 `SECRETARIAT_FIELD_INVALID`；建议 docs/12 §3 码表加一行
   "字段级 schema 上限（tool/role/approvers 数量）复用 `FIELD_INVALID`，不新增码"。
6. `docs/02` 另有三处（[L41](../02-data-model.md#L41)、[L671](../02-data-model.md#L671)、[L689](../02-data-model.md#L689)）
   仍写"20 个"字段（§2.8 已是 21 个），属 r0 已登记的 low 项；**不在 R3 的 §2.8 写作用域**，留给文档收口。

---

## 6. 残余风险

1. **审批签离线复算需要账本片段**（docs/12:179）：`tools/verify_letter.py` 的
   `offline_boundary.not_verifiable` 已如实列出（审批签与身份绑定、RFC 3161）。面板/认定书文案不得
   暗示"离线可验审批真伪"。
2. **审批人私钥就在本机 keyring（0600）**：能读该文件的进程可代签任意审批。审批签的语义边界是
   "这次审批在本地被记录并签名"，不是真实意愿；这句必须在界面出现（i18n `secretariat.honesty.*`）。
3. **`records_root` 粒度**：增删整条动作会改根；单字段篡改靠逐条签名 + 本体签名检出（实测）。
   第三方若只比对 `records_root` 会漏检单字段篡改 → 收口文案要写"三项一起看"。
4. **同 `record_id` 的两条记录可复用审批签与盖章**（§5-2）：需要 R4 写路径保证 id 唯一。
5. **连续性依赖本地链头状态**：`HEAD_HISTORY=500`，老链如实 `unverified`；离线方靠
   `records_root` + 逐条签名，不靠 `prev_hash` 标签。
6. **T6 `verify` 只报告不拦截**：`verify_record` 对不合规已签名记录（例如历史脏数据里的
   `approvers=6`）给 `problems`，不会拒绝存在。R4 面板不得把 T6 的"报告"渲染成"已拦截"。
7. **导入记录的审批条目由导入方提供**：验签会用本地 `user:<did>` 公钥，缺公钥时如实
   `no local key for approver`（不判"合法"）。若未来允许导入已签名记录，需要在文档写明
   "导入记录的审批真伪取决于本机是否持有审批人公钥"。

---

## 7. 复跑清单（本轮全部证据）

```text
# 环境（cwd=agent-explainer）
$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"

python .tmp\r3_audit.py --letter            # 必做 1 + 认定书生成：60/60 PASS，exit 0
python tools/verify_letter.py .tmp/r3_letter.json            # 必做 2：结论 PASS，exit 0
python tools/verify_letter.py .tmp/r3_letter_tampered.json   # 篡改 timestamp：结论 FAIL，exit 1
python -m unittest tests.test_secretariat tests.test_secretariat_contract -q   # Ran 67 / OK / exit 0
python -m unittest tests.test_secretariat -q                 # Ran 49 / OK
python -m unittest tests.test_secretariat_contract -q        # Ran 18 / OK（+5 本轮新增）
python -m unittest tests.test_secretariat_api -q             # Ran 7 / OK
python -m unittest tests.test_secretariat_autosign -q        # Ran 4 / OK
python -m unittest discover -s tests -p "test_*.py" -q       # Ran 934 / failures=1, skipped=3（唯一红=既有已知红）
```
