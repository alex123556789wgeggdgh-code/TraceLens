# R0 · 规格一致性复核：docs/12 八张表 vs 实际代码

> 复核人：产品经理（总裁办收口批次 R0）· 复核日期：2026-11-11
> 复核对象：[docs/12-secretariat-delta-spec.md](../12-secretariat-delta-spec.md)（R1 冻结稿，A–H 八张表 + §7 A1–A15）
> 对照实现：[docs/schemas/action_record.schema.json](../schemas/action_record.schema.json)、
> [agent_explainer/secretariat.py](../../agent_explainer/secretariat.py)、
> [agent_explainer/server.py](../../agent_explainer/server.py)、
> [agent_explainer/web/ext/secretariat.js](../../agent_explainer/web/ext/secretariat.js)、
> [tools/verify_letter.py](../../tools/verify_letter.py)、[tests/](../../tests) 四个总裁办测试文件。
> 口径：只登记，不改代码、不改规格。每条结论都指到 **文件:行** 或 **可复跑命令**；
> 本报告不使用"基本一致 / 应该没问题"这类判断。
> 本轮实测环境：workspace-write 沙箱，`TMP/TEMP=.tmp`、`PYTHONPATH=.tmp/pyfix`（垫片理由见 §5.6）。

---

## 1. 逐表结论（八张表）

判定用三档：**一致** / **不一致** / **文档缺失**（规格写了但实现里没有对应物，或实现做了但规格没写）。

| 表 | 内容 | 结论 | 一句话依据 |
| --- | --- | --- | --- |
| A 字段口径 | Signed ActionRecord 字段 | **一致（1 处文档口径未跟改）** | schema 已加 `tool`（[schema:190](../schemas/action_record.schema.json#L190)），`required` 27 项、`schema_version=1.1`、`additionalProperties:false` 均未变（实测 `properties` 62 项、`required` 27 项）；`docs/02 §2.8` 已改成"21 个"（[docs/02:190](../02-data-model.md#L190)、`tool` 行 [:203](../02-data-model.md#L203)），但同一文档的概览 [:41](../02-data-model.md#L41)、结论表 [:671](../02-data-model.md#L671)、裁决 D9 [:689](../02-data-model.md#L689) 仍写"20 个" → 见 B16 |
| B 签名域 11 项 | `SIGNATURE_FIELDS` | **一致** | [secretariat.py:77-80](../../agent_explainer/secretariat.py#L77-L80) 的 11 项与 docs/12:40 的列表逐字同序同集合；`tsa_timestamp` 写在记录上（[:658](../../agent_explainer/secretariat.py#L658)）但不在签名域；`entry_hash` 计算体排除 `signature/tsa_timestamp/entry_hash`（[:669](../../agent_explainer/secretariat.py#L669)）与 docs/12:60 一致；11/11 字段篡改实测 `ok=False`（§4.2） |
| C 责任链 | 链根/链尾/入参覆盖/DID 形状/收窄/深度/过期 | **不一致（4 项）** | 链尾=实例 DID ✓（[:634-636](../../agent_explainer/secretariat.py#L634-L636)）、入参覆盖改为校验 ✓（[:613-619](../../agent_explainer/secretariat.py#L613-L619)、`setdefault` 只用于 `agent_pubkey` [:629](../../agent_explainer/secretariat.py#L629)）；但①越权 scope 实测 **409**、规格与 `EXPANSION_ERROR_TABLE` 都写 400（B04）；②深度>5 实测 **409**，同上（B04）；③链根校验用 `DELEGATION_INVALID` 而非冻结的 `FIELD_INVALID(field=human_owner)`（B07）；④"委托人必须已登记"只对 `did:tracelens:agent:` 分支生效，未登记的 user DID 可直建根委托（B08） |
| D 审批策略 | 四档 / 审批签 / 盖章 | **不一致（2 项）** | `RISK_POLICY` 与 docs/12:83-86 完全一致（[:82-91](../../agent_explainer/secretariat.py#L82-L91)，且有 [test_secretariat.py:234](../../tests/test_secretariat.py#L234) 钉住）；审批签 payload 9 字段与 Q4 逐字一致（[:1352-1358](../../agent_explainer/secretariat.py#L1352-L1358)）；`verify_record` 真验审批签（[:871-907](../../agent_explainer/secretariat.py#L871-L907)）；critical 自动盖章 + 盖章复算 ✓（[:1384-1388](../../agent_explainer/secretariat.py#L1384-L1388)、[:846-867](../../agent_explainer/secretariat.py#L846-L867)）。偏差：同 DID 双人 → `SECRETARIAT_APPROVAL_REQUIRED`/409，Q5 冻结的是 `FIELD_INVALID`/400（B05）；T10 盖章不写回记录（B11） |
| E 认定书与离线边界 | 16 字段 / 两条根 / 离线工具 | **一致** | `actions[]` 写入的字段与 docs/12:126 的 16 项逐一对应（[:1103-1125](../../agent_explainer/secretariat.py#L1103-L1125)）；`records_root` 两边都用**动作条目**复算（构建 [:1150](../../agent_explainer/secretariat.py#L1150)、复核 [:1225](../../agent_explainer/secretariat.py#L1225)）；`audit_head` 由 T7 注入、取不到时写 `""` 并进 `problems`（[:1170-1176](../../agent_explainer/secretariat.py#L1170-L1176)、[server.py:1878](../../agent_explainer/server.py#L1878)）；`approval_signature_needs_identity_ledger` 固定登记（[:1165-1168](../../agent_explainer/secretariat.py#L1165-L1168)）；离线五项复算 + 篡改 exit 1（§4.5）。仅一处规格未记：`result_summary` 落认定书时截断到 300 字符（B23） |
| F T1–T10 接口 | 端点 / 权限 / 形状 / 错误码 | **一致（1 项状态码外溢）** | 常量在 [server.py:226-233](../../agent_explainer/server.py#L226-L233)（T3/T5/T8/T9 走路由助手 [:3989-4030](../../agent_explainer/server.py#L3989-L4030)）；GET 分发 [:4142-4173](../../agent_explainer/server.py#L4142-L4173)、POST 分发 [:4391-4452](../../agent_explainer/server.py#L4391-L4452)；读 `audit.read`、写 `settings.write`+CSRF 逐条对得上；10 个端点的具名载荷键与 F 表一致（含 T5 `{ok,record,verification}` [:1826](../../agent_explainer/server.py#L1826)）；T3 专属码 `IDENTITY_NOT_FOUND` ✓（[:534](../../agent_explainer/server.py#L534)）。偏差：§3 冻结表里的 400 状态码实测为 409（B04/B05/B06），且 `IDENTITY_MISSING` 在 server 表是 409、运行时是 404（B09） |
| G 验收映射 A1–A15 | 断言/命令归属 | **不一致（4 行）** | A1/A3/A4/A7 的自动化覆盖低于断言字面（B15、§3 表）；A13 的闸门命令 `node web/selfcheck.mjs` 里 **0 处** secretariat 断言且该命令整体 exit 1（89/91）；A14 的"面板零 CJK"不成立（B12）。逐行见 §3 |
| H 写作用域与依赖 | 8 个作用域 + 依赖链 | **一致（1 处归因不精确）** | R3 作用域三项全部落地：schema `tool`（[schema:190](../schemas/action_record.schema.json#L190)）、[docs/02:203](../02-data-model.md#L203)、[docs/07:133](../07-secretariat-and-extensions.md#L133)；R4 作用域全部落地且契约自洽：41 条 `(path, verb)`、docs/03 §6.5 "文档 34 行 / 36 条路径 一一对应"（§4.3 实测输出）；新增文件 [tests/test_secretariat_contract.py](../../tests/test_secretariat_contract.py)、[tests/test_secretariat_api.py](../../tests/test_secretariat_api.py)、[tools/verify_letter.py](../../tools/verify_letter.py) 均在册。偏差：[docs/14:11](../14-r4-production-report.md#L11) 把"记录级集成"归给 `server.py + ingest.py`，但 `sign_on_write` 14 处命中全在 server.py（[server.py:1631/1688/1712/1739/3115/3128/4792/4825](../../agent_explainer/server.py#L1739)），`ingest.py` 零命中，且 `ingest.py` 不在 docs/12:259 的 R4 写作用域里（B14） |

---

## 2. 不一致项清单

无 **blocker**：R2/R3/R4 四套总裁办测试 63/63 全绿（§4.1），已签名记录过 schema 0 错误，认定书离线复核 PASS，未发现数据丢失或越权类缺陷。
以下 **high** 项的性质是"冻结契约与运行时行为互相矛盾"，会在验收与对外话术上直接伤人，建议收口批次内定口径（改规格或改实现，二选一，不要并行两套）。

| ID | 严重度 | 文件 | 行为 | 证据（可复跑） |
| --- | --- | --- | --- | --- |
| B04 | **high** | [secretariat.py:204-207](../../agent_explainer/secretariat.py#L204-L207)（`_status_for`）+ [server.py:544-547](../../agent_explainer/server.py#L544-L547)（`EXPANSION_ERROR_TABLE`）+ [test_secretariat_api.py:107](../../tests/test_secretariat_api.py#L107) | 越权 scope 与深度超限运行时返回 **409**（`SECRETARIAT_SCOPE_WIDENED` / `SECRETARIAT_DEPTH_EXCEEDED`），而冻结规格写 **400**（[docs/12:109-110](../12-secretariat-delta-spec.md#L109-L110)），服务端自己的对外错误表也写 **400**。三份口径互斥，实现测试把 409 钉成契约 | §4.4 探针：`T4 widened scope -> 409 SECRETARIAT_SCOPE_WIDENED`、`T4 depth limit -> 409 SECRETARIAT_DEPTH_EXCEEDED (attempted depth 6)` |
| B05 | **high** | [secretariat.py:1366-1379](../../agent_explainer/secretariat.py#L1366-L1379) + [server.py:552](../../agent_explainer/server.py#L552) | critical 传同一 DID 两次：去重后不足 → 抛 `SECRETARIAT_APPROVAL_REQUIRED` → **409**；Q5 冻结的裁决是"复用 `SECRETARIAT_FIELD_INVALID`，field=approvers，**400**"（[docs/12:266](../12-secretariat-delta-spec.md#L266)）；[docs/13:87](../13-r2-r3-production-report.md#L87) 与 [docs/12:239](../12-secretariat-delta-spec.md#L239) 都写 400 | §4.4 探针：`T5 critical same DID -> 409 SECRETARIAT_APPROVAL_REQUIRED`；库层 `critical with same approver DID twice -> http=409` |
| B06 | medium | [docs/12:236](../12-secretariat-delta-spec.md#L236)（A3）、[docs/12:239](../12-secretariat-delta-spec.md#L239)（A6）、[docs/13:40](../13-r2-r3-production-report.md#L40)、[docs/13:87](../13-r2-r3-production-report.md#L87) | 两份制作报告 + 验收表把"吊销后签名""同 DID 双人"写成 **400**；实测均 **409**（`_status_for` 的 CONFLICT 组 + server 表 [:538/:552](../../agent_explainer/server.py#L538)） | §4.4 探针 + `python -m unittest tests.test_secretariat_api -q`（107 行断言 409 即为契约） |
| B07 | medium | [secretariat.py:700-704](../../agent_explainer/secretariat.py#L700-L704) | 链根校验失败抛 `SECRETARIAT_DELEGATION_INVALID`；docs/12:106 的"链根校验"行写的是 `400 SECRETARIAT_FIELD_INVALID（field=human_owner）`（同一次修复里的"自带 human_owner"确实用了 FIELD_INVALID [:615-618](../../agent_explainer/secretariat.py#L615-L618)，两行被实现成了两个码） | 库层探针：`chain root is not the owner -> http=400 code=SECRETARIAT_DELEGATION_INVALID`；[test_secretariat_contract.py:147-151](../../tests/test_secretariat_contract.py#L147-L151) 断言的就是 `DELEGATION_INVALID` |
| B08 | medium | [secretariat.py:958-966](../../agent_explainer/secretariat.py#L958-L966) | 规格要求"委托人还必须是已登记的 DID（人或 Agent）"（[docs/12:94](../12-secretariat-delta-spec.md#L94)）；实现只在 `delegator_did.startswith("did:tracelens:agent:")` 时查出处（`_known_agent_did` [:1033-1044](../../agent_explainer/secretariat.py#L1033-L1044)），**未登记的 user DID 可直接建根委托**，责任链根无出处 | [test_secretariat_contract.py:171-173](../../tests/test_secretariat_contract.py#L171-L173)：OWNER 从未登记身份仍建出 depth=1 委托并通过 |
| B09 | medium | [secretariat.py:200](../../agent_explainer/secretariat.py#L200) vs [server.py:536](../../agent_explainer/server.py#L536) | `SECRETARIAT_IDENTITY_MISSING`：对外错误表登记 **409 CONFLICT**，运行时 `_status_for` 的 `*_MISSING` 规则给出 **404** | 库层探针：`sign unregistered agent (T5 IDENTITY_MISSING) -> http=404` |
| B10 | medium | [secretariat.py:113-116](../../agent_explainer/secretariat.py#L113-L116) + [docs/12:113](../12-secretariat-delta-spec.md#L113) + [docs/13:95-96](../13-r2-r3-production-report.md#L95-L96) | 记录侧用严格 `RECORD_DID_RE`（与 schema `$defs/did` 完全一致 ✓），委托侧用 `LOOSE_DID_RE`（method 不限 `tracelens`、末段允许大写/下划线）。docs/12 §3 只写了"DID 形状 = 与 schema `$defs/did` 一致"，**没有写"记录严格 / 委托宽松"两档**；而 docs/13 声称"两者口径已在 docs/12 §3 写明"——规格里没有这句话 | 严格式：[schema `$defs/did`](../schemas/action_record.schema.json#L1041) pattern `^did:[a-z0-9]{2,32}:[a-z0-9-]{1,32}:[a-z0-9]{8,64}$`；docs/12:113 写的正则 `^did:tracelens:(user\|agent):[a-z0-9]{8,64}$` 与 schema 并不同集（前者绑定 method，后者不限），括号里的"与 schema 一致"不成立 |
| B11 | medium | [server.py:1899-1914](../../agent_explainer/server.py#L1899-L1914) | T10 `/seal` 只返回独立 seamark，**不写回记录**：盖章后 `record.approval` 仍是 `null`，`verify_record` 的 `checks.seal` 仍为 `not_required`。F 表响应形状 `{ok, seal}` 字面满足（[docs/12:207](../12-secretariat-delta-spec.md#L207)），但 D 表把"盖章"定义成记录上可核验的要求（[docs/12:98](../12-secretariat-delta-spec.md#L98)、[:101](../12-secretariat-delta-spec.md#L101)），面板上"总裁办盖章"因此不改变任何可验证状态 | §4.4 探针：`T10 seal -> 200` / `record.approval after T10 = null` / `verify checks.seal = "not_required"` |
| B12 | medium | [web/ext/secretariat.js:2-10,123,162,232,294,413](../../agent_explainer/web/ext/secretariat.js#L2-L10) + [web/ext/secretariat.css:2-3](../../agent_explainer/web/ext/secretariat.css#L2-L3) | A14 要求"面板 JS/CSS **零 CJK**"（[docs/12:247](../12-secretariat-delta-spec.md#L247)）。实测 secretariat.js 有 12 行含 CJK（文件头纪律注释与 5 个分区标题注释），secretariat.css 有 2 行；且该文件自己的注释写着"本文件零中文"（[:8](../../agent_explainer/web/ext/secretariat.js#L8)），自相矛盾。闸门也照不到：`tools/i18n_lint.py` 的扫描面只有 `app.js, index.html, styles.css`（§4.4 输出行 2） | `python tools/i18n_lint.py` 输出的扫描清单行；grep CJK 命中行号（本表所列） |
| B13 | medium | [selfcheck.mjs](../../agent_explainer/web/selfcheck.mjs)（全文 0 处 `secretariat`）+ [selfcheck-report.json](../../agent_explainer/web/selfcheck-report.json) | A13 的闸门命令是 `node web/selfcheck.mjs` 通过（[docs/12:246](../12-secretariat-delta-spec.md#L246)）。实测该命令 **exit 1（89/91）**，两条失败为 09「未引用字典键」（未引用者含 `trust.*` 一批）与 27「离线纯度」（允许清单变化：新增 `api/secretariat` 及 8 个未挂载端点）；同时该闸门**没有任何一条总裁办断言**，A13 的"五区 / 诚实块 / `?probe=1` 含 trust-panel + 5 个 `[data-zone]`"只有 [docs/05-evidence/r4-secretariat-panel.png](05-evidence/r4-secretariat-panel.png) 一张截图，无自动化 | §4.4 命令与退出码；`grep secretariat web/selfcheck.mjs` 命中 0 |
| B14 | medium | [docs/14:11](../14-r4-production-report.md#L11) vs [docs/12:259](../12-secretariat-delta-spec.md#L259) | 制作报告把"记录级集成"写成 `server.py + ingest.py`；`sign_on_write`/`--no-secretariat` 的实现全在 server.py（14 处命中，`ingest.py` 零命中），而 `ingest.py` 根本不在 R4 冻结写作用域内。归因不精确，且会让人以为 R4 越界改过 `ingest.py` | grep `sign_on_write\|no-secretariat` → 仅 [server.py:1631/1688/1710-1712/1739/1751/2150/3115/3128/3188/4692/4719/4792/4825](../../agent_explainer/server.py#L4825) |
| B15 | medium | [test_secretariat.py:178](../../tests/test_secretariat.py#L178) + [test_secretariat_contract.py:80-85](../../tests/test_secretariat_contract.py#L80-L85) | A1 要求"对 **11 个** `SIGNATURE_FIELDS` 逐个改值 → `ok is False`"（[docs/12:234](../12-secretariat-delta-spec.md#L234)）。入库测试只覆盖 5 个（`timestamp/agent_id/action_type/status/confidence`）+ 契约测试补 1 个（`tool`）= 6/11；`input_hash/output_hash/prev_hash/entry_hash/risk_level` 无逐字段断言。[docs/13:85](../13-r2-r3-production-report.md#L85) 写"11 域篡改检出 11/11"——行为为真（§4.2 实测 11/11），但**没有对应自动化**，回归保护缺一半 | §4.2 探针输出 `detected 11/11 fields -> ok=False` |
| B16 | low | [docs/02:41](../02-data-model.md#L41)、[:671](../02-data-model.md#L671)、[:689](../02-data-model.md#L689) | §2.8 标题已按 docs/12:71 改为"21 个"，同文档其余三处仍写"20 个"（概览行、5.0 增量结论行、裁决 D9）→ 同一文档两种字段计数 | 见行号；schema 实测 `properties` 62 项 |
| B17 | low | [docs/12:24-28](../12-secretariat-delta-spec.md#L24-L28)、[docs/12:37](../12-secretariat-delta-spec.md#L37) | R1 冻结稿的行号引用在 R2/R3 之后已失效：`secretariat.py:381-470` → 实际 `class Secretariat:382` / `register_identity:440-511`；`:503-672` → `sign_record:595-682`；`:702-823` → `create_delegation:938-1003`；`:825-960` → `build_letter:1082-1185`；`:962-989` → `status:1252-1283`；§2 的"`SIGNATURE_FIELDS`（`secretariat.py:76`）"实际在 77、"`RISK_POLICY` … 81"实际在 82 | 行号即证据（本文件全部引用为本次实测行号） |
| B18 | low | [secretariat.py:305-308](../../agent_explainer/secretariat.py#L305-L308) + [docs/12:285](../12-secretariat-delta-spec.md#L285) | docs/12 §10.1 写"密钥在本地 `keyring.json(0600)`"。代码调用 `os.chmod(path, 0o600)` 且注释自认"Windows 上 chmod 只尽力而为"（`except OSError: pass`）→ Windows 下 0600 不构成 ACL，降级口径应写明 | 行号即证据 |
| B19 | low | [docs/12:248](../12-secretariat-delta-spec.md#L248)（A15） | A15 要求面板出现 `local_only` / `unanchored` 文案。`local_only` 有（[secretariat.zh.json:118,123](../../agent_explainer/web/i18n/parts/secretariat.zh.json#L118) + `status.degraded`）；`unanchored` 在总裁办面板文案里不存在（grep `unanchored` 于 `secretariat.{zh,en}.json` 命中 0）——它是存证锚点域的字段，总裁办面板没有锚点面，规格写了实现没有的载体 | grep 命中数；[secretariat.py:399](../../agent_explainer/secretariat.py#L399) `degraded=["local-tsa","in-process-keys"]` |
| B20 | low | [docs/12:42](../12-secretariat-delta-spec.md#L42) vs [docs/14:13](../14-r4-production-report.md#L13) | 文案条数两份文档不一致：R1 说 i18n "已有 138 条"，R4 说"中英同构 155 条"。本轮未逐条清点（时间盒），两数不能同时为真 → 需清点后统一 | 待复跑：`python tools/i18n_merge.py --check` 只报总计（zh=1115 en=1115），不报 secretariat 域条数 |
| B21 | low | [docs/09:58](../09-subtask-plan.md#L58) + [desktop/build/dist/SIGNATURE-STATUS.json](../../desktop/build/dist/SIGNATURE-STATUS.json) | 全量单测唯一红的根因写的是"重打包后未重签，**已签文件不在**"。实测两个产物**都在**（`sha256`/`size` 齐全），状态是 `UnknownError`（"已处理证书链，但是在不受信任提供程序信任的根证书中终止"），而 [test_e2e.py:786-790](../../tests/test_e2e.py#L786-L790) 只把 `Valid/NotTrusted` 计为已签 → `0 != signed_count=2`。根因是"状态取值与测试口径不匹配"，不是"文件缺失" | `python -m unittest discover -s tests -p "test_*.py" -q` → `Ran 919 … FAILED (failures=1, skipped=3)`（§4.1） |
| B22 | low | 5 个 secretariat 错误码无面板字典键：[embed.js:1141-1151](../../agent_explainer/web/i18n/embed.js#L1141-L1151) 只有 11 个 `error.secretariat_*`，[server.py:532-563](../../agent_explainer/server.py#L532-L563) 定义 16 个码 | 面板 toast 走 `secretariat.error.<code 小写>`（[secretariat.js:97-100](../../agent_explainer/web/ext/secretariat.js#L97-L100)）。缺键的恰是最敏感的 5 个：`identity_revoked`、`delegation_invalid`、`signature_invalid`、`approval_required`、`keystore_unavailable`（`i18n_lint` 允许回退 message，93 个码无键）→ 治理关键提示是否显示成人话，取决于回退实现（本轮未逐行核 `core.js` 的缺键渲染，标记为需复核） | `python tools/i18n_lint.py` 的 INFO 无键码清单（含 `SECRETARIAT_FIELD_INVALID` 等）+ 上述两处行号 |

---

## 3. 验收映射 A1–A15：当前证据是否成立

| # | 规格条目 | 结论 | 证据 |
| --- | --- | --- | --- |
| A1 | 11 个签名域逐字段篡改都验不过 | **行为成立 / 自动化覆盖不足（需复核）** | 实测 11/11 `ok=False` 且都带 `SECRETARIAT_SIGNATURE_INVALID`（§4.2）；入库测试只覆盖 6/11（B15） |
| A2 | 签名记录过 ActionRecord schema（四档 0 错误） | **成立（带 1 处需复核）** | [test_secretariat_contract.py:66-78](../../tests/test_secretariat_contract.py#L66-L78) 四档 + `MiniValidator.collect == []`；实测 `tests.test_secretariat_contract` 12/12 OK。需复核点：校验器是自带 `tools/jsonschema_mini.py`，本轮未审计它对 `allOf/if-then`（schema 有 C1–C9+ 条件规则，C9 见 [schema:1900](../schemas/action_record.schema.json#L1900)）的支持度，"0 错误"的强度取决于此 |
| A3 | 身份只追加 / 幂等 / 吊销后拒签 | **成立；"400" 不成立（实为 409）** | [test_secretariat_contract.py:120-130](../../tests/test_secretariat_contract.py#L120-L130)（幂等、账本 `verify().ok`）+ [:97-104](../../tests/test_secretariat_contract.py#L97-L104)（吊销后 `IDENTITY_REVOKED`）；探针 `sign after revoke -> http=409`（B06） |
| A4 | 私钥零泄漏 | **部分成立** | [test_secretariat_api.py:75](../../tests/test_secretariat_api.py#L75) 只对 identity 断言 `notIn "seed"`；[test_secretariat_contract.py:129](../../tests/test_secretariat_contract.py#L129) 只对 `public_identity` 断言。`status` 侧静态可证不含 seed（`_Keyring.facts` 只给 path/exists/keys/writes/mode/source [:372-375](../../agent_explainer/secretariat.py#L372-L375)），认定书侧只含公钥/指纹（[:1162-1163](../../agent_explainer/secretariat.py#L1162-L1163)），但"状态 / 认定书响应整体序列化不含 seed 键"**没有整响应级断言** → 需复核 |
| A5 | 责任链不可伪造 | **成立；链根错误码与冻结表不一致** | [contract:141-151](../../tests/test_secretariat_contract.py#L141-L151)（伪造 human_owner / 链根 / 链尾 / 非法 DID 全 400）+ 链每项过 DID pattern（schema 校验，A2 内）；链根用 `DELEGATION_INVALID`（B07） |
| A6 | 双人必须是两个不同 DID | **行为成立；"400" 不成立（实为 409）** | [contract:132-140](../../tests/test_secretariat_contract.py#L132-L140)；探针 `409 SECRETARIAT_APPROVAL_REQUIRED`（B05/B06） |
| A7 | 审批签绑定记录、不可复用 | **前半成立 / 后半无断言（需复核）** | 跨记录不相等：[contract:153-160](../../tests/test_secretariat_contract.py#L153-L160)。"改动记录 id 后验签失败"没有入库断言；机制上成立（审批 payload 含 `record_id` [:1355](../../agent_explainer/secretariat.py#L1355)，复算取自记录 [:886](../../agent_explainer/secretariat.py#L886)）→ 需复核（建议补一条） |
| A8 | `verify_record` 真验审批签 | **成立** | [contract:162-165](../../tests/test_secretariat_contract.py#L162-L165)；实现 [secretariat.py:871-907](../../agent_explainer/secretariat.py#L871-L907)，`detail.scope="approval"` [:903](../../agent_explainer/secretariat.py#L903) |
| A9 | 只能收窄 / 深度 ≤5 / 过期失效 | **成立（状态码见 B04）** | [contract:167-178](../../tests/test_secretariat_contract.py#L167-L178)、[test_secretariat.py:312/324/336/365](../../tests/test_secretariat.py#L312)；探针：越权 409 `SCOPE_WIDENED`、第 6 层 409 `DEPTH_EXCEEDED` |
| A10 | 认定书可离线复算，改动 → 非零退出 | **成立** | `python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json` → `结论： PASS`（exit 0）；四类篡改均 exit 1：`actions[0].timestamp`、`actions[0].risk_level`、`subject`（认定书签名）、`records_root`（§4.5） |
| A11 | 认定书含 `audit_head` / `records_root` | **部分成立（需复核）** | 证据认定书里两者均非空；`records_root` 离线复算通过（§4.5）。但 `audit_head` = `sha256:0000…0`（空链头），"与审计链当前头一致"无断言，[tools/verify_letter.py](../../tools/verify_letter.py) 只打印不复算 → 需复核 |
| A12 | T1–T10 与契约一致 | **部分成立（命令整体不绿）** | `python tools/gen_api_contract.py --check` exit 1：secretariat 相关 6 行全 PASS（路由 41 条、权限矩阵 8 能力、docs/03 §6.5 "34 行/36 路径一一对应"、可重复生成一致），唯一 FAIL 是"界面调用后端没有的路由"8 个（`/api/analytics /api/compliance /api/evidence /api/groups /api/preferences /api/rollback /api/shares /api/suggestions`）——与 [docs/12:319](../12-secretariat-delta-spec.md#L319) 自述一致；A12 字面写的"PASS"只能在"逐行"意义上成立（B13/B14 同源） |
| A13 | 面板五区可交互 | **需复核（无自动化证据）** | 代码侧五区 + 诚实块存在：[secretariat.js:502-507](../../agent_explainer/web/ext/secretariat.js#L502-L507)（status/identities/delegations/letters/verify）、`dataset.zone` [:31](../../agent_explainer/web/ext/secretariat.js#L31)、`renderHonesty` [:482-489](../../agent_explainer/web/ext/secretariat.js#L482-L489)；`trust-panel` 由 [ext/core.js:338-340](../../agent_explainer/web/ext/core.js#L338-L340) 创建；`index.html:15,103` 引入面板资源、`app.js:1090` 声明端点。但 A13 指定的闸门 `node web/selfcheck.mjs` 无一条总裁办断言且 exit 1（B13），`?probe=1` 的 DOM 主张只有截图 |
| A14 | 文案闸门 | **部分成立；"零 CJK" 不成立** | `python tools/i18n_lint.py` → `PASS … 通过 5，失败 0；zh 1115 / en 1115 键；27 域`；`python tools/i18n_merge.py --check` → `PASS 已合并，无漂移`。面板 JS/CSS 含 CJK 注释（B12），且闸门扫描面不含 `web/ext/`；条数口径两处文档互斥（B20） |
| A15 | 降级项不得伪装 | **成立（1 处措辞载体缺失）** | zh/en 诚实块 p1–p5 明确否认 HSM/TEE、RFC 3161、签字意愿、离线不造假（[secretariat.zh.json:121-126](../../agent_explainer/web/i18n/parts/secretariat.zh.json#L121-L126)）；`local-tsa` 标 `local_only`（[:118](../../agent_explainer/web/i18n/parts/secretariat.zh.json#L118)）；grep 未发现任何肯定式"可信时间戳已获取 / 硬件密钥 / 已上链"字样（命中项全为否定句）。`unanchored` 文案不存在（B19） |

---

## 4. 复跑命令（本轮全部实测通过，可作为收口基线）

前置（本机 workspace-write 沙箱必需，垫片只用于验收、不在交付物里）：

```powershell
cd agent-explainer
$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"
```

### 4.1 单测

| 命令 | 实测 |
| --- | --- |
| `python -m unittest tests.test_secretariat -q` | `Ran 40 tests … OK` |
| `python -m unittest tests.test_secretariat_contract -q` | `Ran 12 tests … OK` |
| `python -m unittest tests.test_secretariat_api -q` | `Ran 7 tests … OK` |
| `python -m unittest tests.test_secretariat_autosign -q` | `Ran 4 tests … OK` |
| `python -m unittest discover -s tests -p "test_*.py" -q` | `Ran 919 tests … FAILED (failures=1, skipped=3)`；唯一红 = `test_e2e.ArtifactTests.test_signature_status_is_honest`（根因见 B21） |

### 4.2 11 个签名域逐字段篡改（补 A1 缺的自动化）

临时脚本（不落仓库）：注册身份 → 签 `act-demo-0008`（high + 1 审批人）→ 对 `SIGNATURE_FIELDS` 逐个改值 → `verify_record`。实测输出：

```
baseline ok = True | signed fields = 11
timestamp / agent_id / action_type / tool / input_hash / output_hash /
prev_hash / entry_hash / risk_level / confidence / status  → 全部 ok=False
codes 均含 SECRETARIAT_SIGNATURE_INVALID（risk_level 额外带 SECRETARIAT_APPROVAL_REQUIRED）
detected 11/11 fields -> ok=False
```

### 4.3 契约与文案闸门

| 命令 | 实测 |
| --- | --- |
| `python tools/gen_api_contract.py --check` | exit 1；`[PASS] 路由抽取 41 条 (path,verb) / 43 个 API_PATH 常量`、`[PASS] 权限矩阵 4 角色 8 能力`、`[PASS] docs/03 §6.5 文档 34 行 / 36 条路径一一对应`、`[PASS] 可重复生成`、`[FAIL] 前端调用面 … 8 个路由`（B13/B14） |
| `python tools/i18n_lint.py` | exit 0；`通过 5，失败 0；zh 1115 / en 1115 键；27 域；错误码 168（有键 75 / 回退 93）` |
| `python tools/i18n_merge.py --check` | exit 0；`keys: zh=1115 en=1115 added=0 [PASS] 已合并，无漂移` |
| `node agent_explainer/web/selfcheck.mjs` | exit 1；`self-check: 89/91 passed, 2 FAILED`（失败项 09 未引用字典键 / 27 离线纯度，均早于 R4 存在，见 B13） |

### 4.4 HTTP 契约探针（补 B04/B05/B06/B09/B11 的实测）

临时脚本（不落仓库）：`start_background_server` + 真发 HTTP，实测输出：

```
T4 root delegation                      -> 201 created
T4 widened scope  [docs/12:109 = 400]   -> 409 SECRETARIAT_SCOPE_WIDENED
T4 depth limit    [docs/12:110 = 400]   -> 409 SECRETARIAT_DEPTH_EXCEEDED (attempted depth 6)
T5 critical same DID  [docs/13:87 = 400] -> 409 SECRETARIAT_APPROVAL_REQUIRED
T10 seal response                       -> 200
     record.approval after T10 = null
     verify checks.seal = "not_required" | ok = False
T1 family entry /api/secretariat        -> 200
```

库层 `SecretariatError.http_status`（即 [server.py:3690-3692](../../agent_explainer/server.py#L3690-L3692) 发出去的码）实测：

```
sign unregistered agent            -> 404 SECRETARIAT_IDENTITY_MISSING
critical same DID twice            -> 409 SECRETARIAT_APPROVAL_REQUIRED
chain root is not the owner        -> 400 SECRETARIAT_DELEGATION_INVALID
forged human_owner                 -> 400 SECRETARIAT_FIELD_INVALID
child widens action_types          -> 409 SECRETARIAT_SCOPE_WIDENED
delegation depth > 5               -> 409 SECRETARIAT_DEPTH_EXCEEDED
sign after revoke                  -> 409 SECRETARIAT_IDENTITY_REVOKED
revoke unknown identity            -> 404 SECRETARIAT_IDENTITY_NOT_FOUND
```

### 4.5 认定书离线复核（A10/A11）

```
python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json
  签名 通过 | records_root 通过 | 动作 act-demo-0001/0002/0003 通过
  audit_head sha256:0000…0 | tsa_state local_only | 结论： PASS      (exit 0)

篡改 actions[0].timestamp  -> RC=1 结论： FAIL
篡改 actions[0].risk_level -> RC=1 结论： FAIL
篡改 subject（认定书签名） -> RC=1 签名：失败 结论： FAIL
篡改 records_root          -> RC=1 records_root：失败 SECRETARIAT_HASH_MISMATCH 结论： FAIL
```

### 4.6 未能读取 / 未逐项核对（如实登记）

- 本轮**未逐行通读**：`agent_explainer/crypto_sig.py`（仅经测试与调用点间接确认 `signed_payload/verify_b64/did_from_pubkey` 行为）、`agent_explainer/ingest.py`、`tools/jsonschema_mini.py`（其 `allOf/if-then` 覆盖度，影响 A2 强度）、`web/ext/core.js` 的缺键渲染分支。
- 本轮**未清点** secretariat 域 i18n 条数（B20 两个数字谁对未定）。
- 没有读不动的文件。

---

## 5. 产品口径结论

1. **六条冻结缺口的"事实层"全部落地**：签名记录过自家 schema、责任链链尾是实例 DID 且入参不能替别人派责任、审批签绑定记录并真验、`/api/secretariat/*` 十个端点真实存在（41 条路由抽到）、面板从 227 B 占位长成 24 KB 五区、认定书离线可复算并拒绝篡改——R1 冻结的六条缺口没有一条是纸面完成。
2. **收口真正的风险不在密码学，在"状态码契约"**：docs/12 §3/§7、`EXPANSION_ERROR_TABLE`、运行时三者对 `SCOPE_WIDENED / DEPTH_EXCEEDED / APPROVAL_REQUIRED / IDENTITY_REVOKED` 的 HTTP 状态各说一套（400 vs 409），且实现测试已把 409 钉成契约（B04/B05/B06/B09）。这是必须在本批次定调的口径问题：建议**以运行时 409（状态冲突语义正确）为准，改 docs/12 §3/§7 与对外错误表**，不要改行为。
3. **验收映射有 4 行证据不足以撑住"已验收"**：A1（覆盖 6/11）、A4/A7/A11（无整响应/整链断言）、A13（闸门 0 条总裁办断言且整体 exit 1）、A14（面板零 CJK 不成立，且闸门扫不到 `web/ext/`）。这 4 行不应被引用为"独立验收通过"，应转为"已实现 + 待补自动化"。
4. **诚实边界这一条守住了**：`local-tsa / local_only`、无 HSM/TEE、审批签不证明签字意愿、离线不造假，中英双语写在面板与代码注释里，grep 找不到任何肯定式越界话术——只有 A15 里的 `unanchored` 是个不存在于该面板的载体（B19），属措辞而非造假。
5. **对外表述建议**：总裁办 R1–R4 可宣布"**功能与密码学闭环完成、契约层有已知偏差待收**"；不可宣布"验收映射 15/15 通过"；全量 919 条里那 1 条红属发布签名环节（根因与文档写的不同，B21），与 5.0 无关，但它会让"全绿"这句话说不出口。
