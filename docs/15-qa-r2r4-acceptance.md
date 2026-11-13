# 总裁办 R2–R4 · QA 独立验收报告（t5）

> 验收人：测试与质量官（QA）· 团队 `tracelens-secretariat-closeout` · 任务 t5
> 验收日期：2026-11-12 · 工作目录 `agent-explainer/`
> 依据：[docs/12](12-secretariat-delta-spec.md)（§6 表 F、§7 A1–A15、§10）、
> [docs/13](13-r2-r3-production-report.md)、[docs/14](14-r4-production-report.md)、
> [r0-spec-conformance](audit/r0-spec-conformance.md)、[r2-review](audit/r2-review.md)、
> [r3-review](audit/r3-review.md)、[r4-review](audit/r4-review.md)
> 写作用域：本文件（新建）+ `.tmp/qa_*` 验收垫片（不进交付物）。**未改**代码 / 测试 / 既有文档 / 契约。
> 环境：`$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"`（workspace-write 垫片）。

---

## 0. 结论

**有条件 PASS（R2–R4 可进入收口；不可宣称"验收映射 15/15 通过"）。**

| 判定 | 数量 | 条目 |
| --- | --- | --- |
| **PASS** | 11 | A1、A2、A4、A5、A6、A7、A8、A10、A11、A12、A14 |
| **PARTIAL** | 4 | A3（行为成立，`IDENTITY_REVOKED` 409 vs 规格 400）、A9（行为成立，`SCOPE_WIDENED`/`DEPTH_EXCEEDED` 409 vs 规格 400）、A13（面板有源码断言+截图，但 A13 指定闸门仍 exit 1 且 0 条 secretariat 断言）、A15（诚实条目成立，`unanchored` 词条不存在） |
| **FAIL** | 0 | — |

- **无 blocker**：没有发现数据丢失、越权、签名可伪造或责任链可替别人派责任的新缺陷；
  密码学闭环与接口面在本会话独立复跑中成立（自写探针 **53/53 PASS**，§2）。
- **两条放行条件（必须由 lead 收口，不在 QA 写作用域）**：
  1. **状态码口径**：`SCOPE_WIDENED` / `DEPTH_EXCEEDED` / `IDENTITY_REVOKED` 运行时 **409**，
     而 [docs/12:109-110](12-secretariat-delta-spec.md#L109-L110)（A9）、[:236](12-secretariat-delta-spec.md#L236)（A3）
     与 `server.py` 的 `EXPANSION_ERROR_TABLE` 写 **400**；实现测试把 409 钉成契约。
     **建议以运行时 409 为准改规格与对外表**（不改为行为），同时改 [docs/13:87](13-r2-r3-production-report.md#L87)
     的"400 `SECRETARIAT_APPROVAL_REQUIRED`"（实际已是 400 `FIELD_INVALID`/field=approvers）。
  2. **A13 闸门缺口**：`node web/selfcheck.mjs` 仍是 **89/91、exit 1**，且 `selfcheck.mjs` 全文
     **0 处** `secretariat` / `trust-panel` 断言；A13 的"闸门通过"字面不成立。面板装配目前只有
     源码结构断言（6/6 通过）与截图，需要 lead 授权改 `web/selfcheck.mjs`（扫描面 + allowlist + 一条面板装配断言）。
- **产品口径**：可宣布"**功能与密码学闭环完成、R2–R4 交付可验收；契约层有 2 组状态码偏差（A3/A9）、1 项
  闸门缺口与若干文档口径待收口**"；**不可**宣布"验收映射 15/15 通过"或"全量测试全绿"
  （954 条中 1 条既有红，见 §2.2）。

---

## 1. 模型与独立性声明

- **模型**：本会话以 `deepseek-official/deepseek-flash`（DeepSeek-V4.1-Flash）运行，与
  [docs/13 §0](13-r2-r3-production-report.md) 登记的"测试与质量官"一致。R2/R3/R4 代码由 lead 以
  其他角色口径制作，QA **不是**这些文件的作者。
- **独立性做法**：不采信报告自述。8 项必跑命令全部在本会话重新执行并记录真实输出与退出码（§2）；
  另写 `.tmp/qa_accept_probe.py`（53 条断言）直接对运行时下断言，覆盖 A1/A2/A3/A4/A5/A6/A7/A8/A9/A10/A11；
  A12 用 `docs/api_contract.json` 与 docs/12 §6 冻结表逐条比对（14/14），A13/A14/A15 用源码/字典/Python
  UTF-8 解码独立核对。**引证但未复跑**的部分已在条目中注明（如 t4 的 66 项 HTTP 探针、t3 的 60 项探针、
  team 模式 401/CSRF/403 矩阵——这些由既有测试套件与本会话套件全绿间接覆盖）。
- **只读边界**：未改任何代码、测试、既有文档、契约；唯一新增交付物是本文件。`.tmp/qa_*` 是验收垫片，不进交付物。
- **副作用如实登记（工具行为，不是编辑）**：
  1. 运行 `node web/selfcheck.mjs` 会按设计重写 `agent_explainer/web/selfcheck-report.json` 的
     `generated_at`（内容仍为 89/91、同两条 FAIL）；源码/测试/文档/契约均未触碰。
  2. 全量 `unittest discover` 会向仓库运行时目录 `var/features/secretariat/heads.jsonl` 追加记录
     （本会话 192 行 / 661,632 B，均为 `demo-photo-bot` 的链头，时间戳 16:57:41Z）；
     这是测试未完全隔离到临时目录 + `HEAD_HISTORY=500` 的既有行为，见 §4-M5。
  3. 其余临时产物（探针、日志、篡改副本）都在 `.tmp/`。

---

## 2. 命令实测（本会话真实输出与退出码）

### 2.1 必跑 8 项

| # | 命令 | 实测输出摘要 | 退出码 | 与任务预期 |
| --- | --- | --- | --- | --- |
| 1 | `python -m unittest tests.test_secretariat -q` | `Ran 49 tests in 1.220s` / `OK` | **0** | 预期 40；**49 = 40 + t2 新增 9**，全绿 |
| 2 | `python -m unittest tests.test_secretariat_contract -q` | `Ran 18 tests in 0.608s` / `OK` | **0** | 预期 12；**18 = 12 + t2 1 + t3 5**，全绿 |
| 3 | `python -m unittest tests.test_secretariat_api -q` | `Ran 25 tests in 8.587s` / `OK` | **0** | 预期 7；**25 = 7 + t4 18**，全绿 |
| 4 | `python -m unittest tests.test_secretariat_autosign -q` | `Ran 6 tests in 3.397s` / `OK` | **0** | 预期 4；**6 = 4 + t4 2**，全绿 |
| 5 | `python tools/i18n_lint.py` | `PASS 5 项：通过 5，失败 0；zh 1115 / en 1115 键；27 域；错误码 168（有键 75 / 回退 93）` | **0** | 与预期一致 |
| 6 | `python tools/gen_api_contract.py --check` | `[PASS] 路由 41 条 / 43 常量 / 公开 5 / 405 组 3`；`[PASS] 权限矩阵 4 角色 8 能力`；`[PASS] 错误码 168`；`[PASS] docs/03 §6.5 文档 34 行 / 36 条路径 ↔ 契约 36 条，方法列一致，无 ⚠`；`[PASS] 可重复生成 39246 字节`；**`[FAIL] 前端调用面` 8 个域外路由**（analytics/compliance/evidence/groups/preferences/rollback/shares/suggestions） | **1**（预期：域外端点） | **唯一失败 = 8 个非总裁办端点**，secretariat 相关全 PASS |
| 7 | `python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json --json` | `ok=true`；`signature=true`、`records_root=true`、`actions_ok=true`（3 条动作全 true）；`audit_head=sha256:000…0`；`tsa_state=local_only`；`problems=[]` | **0** | 与预期一致 |
| 8 | 篡改 `.tmp/qa-tampered-letter.json` 的 `actions[0].timestamp` 后复算 | `ok=false`；`signature=false`、`actions[0].ok=false`；`problems=[SIGNATURE_INVALID(scope=letter), SIGNATURE_INVALID(scope=action,id=act-demo-0001)]` | **1** | 与预期一致；`records_root` 仍 true（机制见 §3-A10） |

**计数说明（必须写清，否则会误判）**：任务卡里的 40/12/7/4 是 [docs/12 §11](12-secretariat-delta-spec.md#L299-L309) 的
R1 时代数字；t2 给 `test_secretariat` 加 9、给 contract 加 1，t3 给 contract 加 5，t4 给 api 加 18、给 autosign 加 2。
本会话实测 **98 条总裁办测试全绿**（49+18+25+6），没有出现新的红。docs/12 §11 尚未随之下调/上调，
属文档口径待收（§4-L1）。

### 2.2 补充闸门（本会话另跑，用于独立判定）

| 命令 | 实测 | 退出码 |
| --- | --- | --- |
| `python -m unittest discover -s tests -p "test_*.py" -q`（干净环境重跑） | `Ran 954 tests in 164.387s` / `FAILED (failures=1, skipped=3)`；唯一红 = `test_e2e.ArtifactTests.test_signature_status_is_honest`（`0 != 2`，发布件状态取值口径） | 1（既有红） |
| 同上（首次，与其它 Python 命令并发） | `Ran 954` / `FAILED (failures=1, errors=1, skipped=3)`；多出的 ERROR = `test_settings_api.SettingsApiLocalTests.test_method_contract` 的 `ConnectionAbortedError [WinError 10053]`。单跑该条 `Ran 1 / OK`，干净重跑无 error → **并发负载下的环境抖动，非 R2–R4 缺陷** | 1 |
| `node agent_explainer/web/selfcheck.mjs` | `89/91 passed, 2 FAILED`：FAIL 09（`secretariat.*` 165 个叶键全被列为"从未渲染"）、FAIL 27（offline purity：allowlist 21 项 vs app.js 30 端点）；全文 `secretariat|trust-panel` 命中 **0** | 1 |
| `python .tmp/qa_accept_probe.py`（QA 自写，53 断言） | `SUMMARY total=53 pass=53 fail=0`；附注 3 条：撤销后签名 409、越权 scope 409、深度 6 409 | 0 |
| `python tools/i18n_merge.py --check` | `keys: zh=1115 en=1115 added=0` / `[PASS] 已合并，无漂移` | 0 |
| 契约 14 条逐条比对（QA 脚本） | secretariat `(verb,path)` **14/14 与 docs/12 §6 相等**；`only_contract=[]`、`only_spec=[]` | 0 |
| `SecretariatPanelTests`（6 条）+ `test_letter_binds_the_live_audit_head` | 6/6 `OK`；A11 绑定测试 `OK` | 0 |
| Python UTF-8 解码统计面板 CJK | `secretariat.js` 24,396 B / **0** 汉字；`secretariat.css` 3,607 B / **0** 汉字 | — |
| 字典叶键清点 | zh 165 / en 165 叶键，键集合完全一致；`honesty.p1–p5` 齐备 | — |

---

## 3. A1–A15 逐条判定

判定口径：**PASS** = 本会话有可复跑证据且与规格一致；**PARTIAL** = 行为成立但有一项口径/证据缺口；
**FAIL** = 不成立。所有"实测"都是本会话命令或自写探针的结果。

| # | 判定 | 本会话证据 | 说明 / 缺口 |
| --- | --- | --- | --- |
| **A1** 11 个签名域逐个篡改都验不过 | **PASS** | QA 探针：`SIGNATURE_FIELDS` len=11；对 11 域逐个改值 → `ok=False` 且 `problems` 含 `SECRETARIAT_SIGNATURE_INVALID`，计 **11/11** | 行为成立；但仓库内持久断言只覆盖 5 域（`test_secretariat.py:175-190`）+ `tool`（`test_secretariat_contract.py:81-86`）= **6/11**，其余靠 `.tmp` 探针（r0/r3/t5）→ §4-M2 |
| **A2** 已签名记录过 schema（四档） | **PASS** | QA 探针：low/medium/high/critical 四档 `MiniValidator.collect == []` 且 `verify_record.ok=True`；`tests.test_secretariat_contract` 18/18 | 校验器是自带 `tools/jsonschema_mini.py`，其 `allOf/if-then` 覆盖度本轮未审计（边界 §5） |
| **A3** 身份只追加/幂等/吊销后拒签 | **PARTIAL** | QA 探针：重复登记账本 0 新增；吊销追加 `revoked_at`；`identities_ledger.verify().ok=True`；吊销后签名 → `SECRETARIAT_IDENTITY_REVOKED` | **状态码不符规格**：运行时 `http_status=409`，[docs/12:236](12-secretariat-delta-spec.md#L236) 写 400（B06）。行为正确，口径未收 |
| **A4** 私钥零泄漏 | **PASS** | QA 探针：对 `public_identity` / `status` / `letter` / `identities` 四个整响应做递归键扫描 + JSON 文本扫描，`seed/private_key/privkey/secret` 命中 **0** | 比 r0 的"仅 identity 断言"更严；`keyring` 只出 facts（path/exists/keys/writes/mode），含服务端路径（与既有 `/health` 同口径，非新增泄漏面） |
| **A5** 责任链不可伪造 | **PASS** | QA 探针四例均 400：伪造 `human_owner`→`FIELD_INVALID/field=human_owner`；链根≠owner→同码；链尾≠实例 DID→`DELEGATION_INVALID/field=delegation_chain`；链内非 DID→`FIELD_INVALID` | 链每项过 schema `$defs/did`；中间段是否真来自委托账本**不校验**（C7，离线验签只需形状，边界 §5） |
| **A6** 双人必须是两个不同 DID | **PASS** | QA 探针：同 DID 两次 → **400 `FIELD_INVALID`(field=approvers, given=1)**；两个不同 user DID → `required=2`、`sealed=true`、verify ok | t2 已按 Q5 收口；"一个都没给"仍 409 `APPROVAL_REQUIRED`（另一支，有测试） |
| **A7** 审批签绑定记录、不可复用 | **PASS** | QA 探针：同审批人/同时钟/两条不同记录 → 审批签名不相等；签后改 `id` → verify 失败 | 前提是 `record_id` 唯一；同 id 同事实的复制记录可复用审批签与章（r3 §5-2）。`secretariat` 不负责去重，R4 写路径才管 |
| **A8** `verify_record` 真验审批签 | **PASS** | QA 探针：伪造审批签（64 字节 0x01）→ `ok=False`，`problems` 含 `SIGNATURE_INVALID` 且 `detail.scope="approval"` | 审批人公钥取本地 keyring；缺公钥时如实报"no local key"（不判合法） |
| **A9** 只能收窄/深度≤5/过期失效 | **PARTIAL** | QA 探针：子委托扩权 → `SCOPE_WIDENED`；depth 5 可建、第 6 层 → `DEPTH_EXCEEDED`；TTL 60s 后 `authorise` → `allowed=false, reason=DELEGATION_EXPIRED` | **状态码不符规格**：`SCOPE_WIDENED`/`DEPTH_EXCEEDED` 运行时 409，[docs/12:109-110](12-secretariat-delta-spec.md#L109-L110) 写 400（B04）。另：未登记 user DID 可建根委托（B08，§4-M3） |
| **A10** 认定书可离线复算，改动→非零退出 | **PASS** | 必跑 7：`ok=true`/exit 0；必跑 8：篡改 `actions[0].timestamp` → exit 1，`problems` 同时含 letter 与 action 两个 scope | `records_root` 只锚各条 `entry_hash`，单字段篡改由"逐条动作签名 + 认定书本体签名"检出（实测 records_root 仍 true）；验收文案不应只测 root |
| **A11** 认定书含 audit_head/records_root | **PASS** | `test_letter_binds_the_live_audit_head`（真实 HTTP）通过：`letter.audit_head == server.audit_chain_status()["head"]`、`len(records_root)==64`、离线复核同值；无链头时写 `""` 并登记 `scope=audit_head` | 示例认定书的 `audit_head` 是 `sha256:000…0`，因为生成时审计链为空（GENESIS 即当时的头），不是伪造；该示例不能证明"非零链头"绑定，靠上面的 HTTP 测试补足 |
| **A12** T1–T10 与契约一致 | **PASS** | 契约 14 条 `(verb,path)` 与 docs/12 §6 **14/14 相等**，abilities/CSRF 逐条一致；`--check` 的 secretariat 行全 PASS；docs/03 §6.5 34 行/36 路径与契约 36 条一一对应 | `--check` 整体 exit 1 只因 **8 个域外前端端点**（Q10 已登记为已知红，非总裁办） |
| **A13** 面板五区可交互 | **PARTIAL** | `SecretariatPanelTests` 6/6：五区顺序 + `data-zone`、单一请求门、写按钮 gating、零 CJK、字典键齐备、诚实文案；截图 `docs/05-evidence/r4-secretariat-panel.png`（1400×912，实拍状态总览/风险策略/身份确权/诚实块） | **A13 指定的闸门不成立**：`node web/selfcheck.mjs` = 89/91、exit 1；`selfcheck.mjs` 全文 0 处 secretariat/trust-panel（`collectReferencedKeys` 只扫 app.js，allowlist 硬编码 21 项）；`?probe=1` 的 DOM 断言仍无自动化 |
| **A14** 文案闸门 | **PASS** | 必跑 5：i18n_lint PASS 5/5；`i18n_merge --check` PASS（zh=en=1115，added=0）；Python UTF-8 实测面板 js/css **0/0** 汉字；zh/en 各 165 叶键、键集合一致 | 注意：`i18n_lint.py` 的扫描面是 `app.js/index.html/styles.css`，**不扫** `web/ext/`；A14 的零 CJK 由测试 `test_panel_and_css_have_zero_cjk` 兜住（§4-L8） |
| **A15** 降级项不得伪装 | **PARTIAL** | `honesty.p1–p5` 齐备：明说无 HSM/TEE、`local-tsa` 非 RFC 3161、审批签不证明真实意愿、append-only、离线不造假；`local_only`/`local-tsa` 文案存在；中英文对"可信时间戳/硬件密钥/已上链"只有否定式 | **`unanchored` 在总裁办面板字典中 0 命中**（B19）：该词属于存证锚点域，面板没有锚点面；A15 字面要求未完全满足，属措辞载体缺失（low） |

### 3.1 四个 PARTIAL 的定性

- **A3 / A9**：不是功能失败，是"冻结规格 vs 运行时"的 HTTP 状态码冲突。实现测试已把 409 钉住，
  改运行时会影响已签收断言；**建议改规格与对外错误表**（r0/r2/r4 同一结论）。在改完之前，QA 不能签"15/15"。
- **A13**：面板真实存在（截图 + 6 条源码断言），但 A13 点名的闸门命令是红的且与面板无关；
  这是"证据形态"缺口，不是"面板没做"。修复点在 `web/selfcheck.mjs`，需 lead 授权。
- **A15**：诚实边界实质成立；只差 `unanchored` 这个在该面板没有载体的词。应改规格措辞（说明锚点面不在本面板）
  或在面板补一条对应文案，二选一。

---

## 4. 问题分级与建议

| ID | 级别 | 问题（证据） | 建议动作 |
| --- | --- | --- | --- |
| **H1** | **high** | 状态码口径三方互斥：运行时 409（[secretariat.py:211-222](../agent_explainer/secretariat.py#L211-L222)）vs [docs/12:109-110/236](12-secretariat-delta-spec.md#L109-L110) 的 400 vs `server.py` `EXPANSION_ERROR_TABLE`（[:528-562](../agent_explainer/server.py#L528-L562)）的 400；t2/t4 的测试把 409 钉成契约 | 以运行时 **409** 为准改 docs/12 §3/§7 与对外错误表；不改行为。改完 A3/A9 可转 PASS |
| **M1** | medium | [docs/13:87](13-r2-r3-production-report.md#L87) 仍写"400 `SECRETARIAT_APPROVAL_REQUIRED`"；实际同 DID 双人已是 **400 `FIELD_INVALID`(field=approvers)**，"一个都没给"才是 409 | 随 H1 一起改文档；R2 报告 §5 已列，但源文件未改 |
| **M2** | medium | A1 的 11 域篡改只有 6/11 进入仓库测试（5 域 + `tool`），其余仅 `.tmp` 探针；回归保护缺一半（B15） | 把 11 域循环写成 `tests/test_secretariat.py` 一条参数化断言（属测试文件，需授权） |
| **M3** | medium | **B08 未修**：未登记的 user DID 可直建根委托。QA 探针里 `OWNER` 只作为 `owner_did` 出现、从未登记身份，`create_delegation(OWNER→agent)` 仍成功（depth=1） | 先定"user 登记"语义；最省事：根委托要求 `delegator_did` 出现在身份账本任一 `owner_did`，并改 api T4 流程 |
| **M4** | medium | **B11 未修**：T10 `/seal` 只返回独立章（[server.py:1907-1922](../agent_explainer/server.py#L1907-L1922)），不写回记录；盖章后 `record.approval` 仍为 null、`verify_record.checks.seal` 仍 `not_required` | 若"总裁办盖章"要成为记录上可核验的事实，需把 seal 写进记录并重签/重算 `entry_hash`；否则面板文案应降级为"生成独立盖章凭据" |
| **M5** | medium | **存储放大**：`_set_head` 每次把最近 ≤500 条 `entry_hash` 塞进链头记录（[secretariat.py:1476-1513](../agent_explainer/secretariat.py#L1476-L1513)、`HEAD_HISTORY=500`），append-only 下每条签名记录产生 ~3.4 KB 链头行。本会话一次全量测试即让 `var/features/secretariat/heads.jsonl` 到 **192 行 / 661,632 B**；同时说明测试写到了仓库 `var/`（未完全隔离） | 评估改为"链头 + 增量索引/定期压缩"，或把 `HEAD_HISTORY` 降到可接受上限；测试侧应把运行时目录指到临时目录 |
| **L1** | low | docs/12 §11 的实测数字已过期：40/12/7/4（现 49/18/25/6）、915/919（现 954）；[docs/12:305](12-secretariat-delta-spec.md#L305) 的 89/91 仍准确 | 收口时更新 §11 数字与日期 |
| **L2** | low | docs/12:24-28/37 的 `secretariat.py` 行号引用在 R2/R3 后失效（B17） | 收口时改为符号名或重新标注行号 |
| **L3** | low | [docs/02:41/671/689](02-data-model.md#L41) 仍写"20 个"字段，§2.8 已是 21 个（B16） | 统一为 21 |
| **L4** | low | docs/12:113 的 DID 正则写"与 schema `$defs/did` 一致"，但记录侧 `RECORD_DID_RE` 是 `did:<method>:<type>:<id>`、委托侧更宽松；规格没有写"严格/宽松两档"（B10） | 规格补两档口径，或收紧措辞 |
| **L5** | low | A15 的 `unanchored` 在 `secretariat.{zh,en}.json` 中 0 命中（B19） | 改规格措辞（本面板无锚点面）或补文案 |
| **L6** | low | 文案条数口径互相矛盾：docs/12:42 写 138、docs/14:13 写 155；实测 `secretariat.*` **165 叶键**（zh/en 一致）（B20） | 统一为 165（或说明统计口径） |
| **L7** | low | [docs/14:11](14-r4-production-report.md#L11) 把"记录级集成"归给 `server.py + ingest.py`，实际 `sign_on_write` 全在 server.py，`ingest.py` 零命中且不在 R4 写作用域（B14） | 修正归因 |
| **L8** | low | `i18n_lint.py` 扫描面不含 `web/ext/`，A14 的"零 CJK"实际由测试而非 linter 保证；同一闸门对 `secretariat.error.*` 报"无键"但键其实存在（报告口径噪声） | 收口时把 `web/ext/*.js|css` 纳入 lint，或把 A14 的证据明确指到测试 |

**未发现**：签名可伪造、审批签可跨记录复用、责任链可替别人派责任、私钥进响应、CSRF/权限矩阵缺口（后者由 t4 的 66 项探针与本会话套件覆盖，未逐条复跑）。

---

## 5. 不能验的边界（本轮如实登记）

1. **team 模式入库签名**：owner→DID 映射未接，`signing.enabled=false`、记录不签名；测试只钉住"如实关闭"，
   无法验"team 模式签出的记录"。属 docs/14 §7 后续批次。
2. **HSM/TEE**：不存在。私钥在本地 `keyring.json`（Windows 上 chmod 0600 仅尽力），
   只有进程内 Ed25519 与 `degraded=["local-tsa","in-process-keys"]`。
3. **RFC 3161 / 可信时间戳**：不存在。`tsa_timestamp=local-tsa:<ISO>`、`tsa_state=local_only`；
   `tsa_timestamp` 不在 `entry_hash` 域，改值后只能靠 `tsa_state` 降级标注（r3 §5-1）。
4. **上链 / 锚点**：无。`unanchored` 面板文案缺失；无法验任何链上锚定。
5. **真实世界审批意愿**：审批人私钥就在本机 keyring，能读文件的进程可代签；审批签只证明
   "这次审批在本地被记录并签名"，不证明真实签字意愿。
6. **8 个域外端点**：analytics/compliance/evidence/groups/preferences/rollback/shares/suggestions
   前端有调用、后端无路由；契约因此整体 exit 1，不属总裁办，无法在本轮验。
7. **PDF 导出**：未接；可离线验证的正式载体是 JSON，PDF 只作展示。
8. **A13 的 `?probe=1` DOM 断言**：`selfcheck.mjs` 未导出 `boot`/`makeSandbox`，本轮无法在 `.tmp` 做无头面板探针；
   只有源码结构断言 + 截图，没有浏览器级自动断言。
9. **MiniValidator 的强度**：A2 的"0 错误"依赖自带校验器；其 `allOf/if-then` 覆盖度未审计，
   "0 错误"不等于"与完整 JSON Schema 实现等价"。
10. **责任链中间段**：只验形状/根/尾/长度/不重复，不证明每段真有一条委托账本记录（C7）；
    认定书/面板不得暗示"每段都已对账"。
11. **浏览器交互**：本会话只读截图（1400×912，真实渲染）与源码断言，未逐按钮点击复跑。

---

## 6. 复跑清单（QA 本轮全部证据）

```powershell
cd agent-explainer
$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"

python -m unittest tests.test_secretariat -q                  # Ran 49 / OK / 0
python -m unittest tests.test_secretariat_contract -q         # Ran 18 / OK / 0
python -m unittest tests.test_secretariat_api -q              # Ran 25 / OK / 0
python -m unittest tests.test_secretariat_autosign -q         # Ran 6  / OK / 0
python tools/i18n_lint.py                                     # PASS 5/5 / 0
python tools/gen_api_contract.py --check                      # 唯一 FAIL=8 域外 / 1
python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json --json   # ok=true / 0
python tools/verify_letter.py .tmp/qa-tampered-letter.json --json                     # ok=false / 1
python .tmp/qa_accept_probe.py                                # 53/53 PASS / 0
python tools/i18n_merge.py --check                            # PASS / 0
node agent_explainer/web/selfcheck.mjs                        # 89/91 / 1（既有 FAIL 09/27）
python -m unittest discover -s tests -p "test_*.py" -q        # 954 / failures=1 / 1（既有红）
```

- 垫片 `tests` 路径跑单条面板测试时用 `$env:PYTHONPATH="$PWD\tests;$PWD\.tmp\pyfix"`。
- `.tmp/qa_accept_probe.py`、`.tmp/qa_contract_check.py`、`.tmp/qa_i18n_count.py` 与日志
  `.tmp/qa-*.log` 是验收垫片，**不进交付物**；篡改副本 `.tmp/qa-tampered-letter.json` 同理。
- 示例认定书 `docs/05-evidence/r4-responsibility-letter.json` 全程只读（篡改在副本上做）。

---

## 7. 一句话给 lead

**R2–R4 的功能与密码学证据成立、98 条总裁办测试全绿、独立探针 53/53；可以进入收口，
但必须先按 H1 改状态码口径、按 M1 改报告、按 A13 补闸门断言，并如实保留 A3/A9/A13/A15 四个 PARTIAL。**
