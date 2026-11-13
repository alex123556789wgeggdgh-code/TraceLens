# 总裁办 R1–R4 收口报告（队长）

> 日期：2026-11-11 → 2026-11-12 · 团队：`tracelens-secretariat-closeout`（4 人，5 个任务，**严格串行**）
> 前置交付：[R1 规格](12-secretariat-delta-spec.md)、[R2/R3 报告](13-r2-r3-production-report.md)、[R4 报告](14-r4-production-report.md)
> 独立验收：[docs/15](15-qa-r2r4-acceptance.md) · 独立复核：[docs/audit/](audit/)

## 1. 团队与模型分工（实际执行）

| 成员 | 角色 | 模型（实际路由） | 任务 |
| --- | --- | --- | --- |
| product-manager | 产品经理 | **strata/swift-iq3_s**（本地） | T0 规格一致性复核 |
| backend | 后端工程师 | **strata/swift-iq3_s**（本地） | T1 R2、T2 R3 |
| architect | 架构师 | **strata/swift-iq3_s**（本地） | T3 R4 集成复核 |
| qa | 测试与质量官 | **deepseek-official/deepseek-flash**（DeepSeek-V4.1-Flash） | T4 独立验收 |

执行顺序（依赖链，无并行）：T0 → T1 → T2 → T3 → T4。每个任务完成并落盘后，调度器才唤醒下一个成员。

## 2. 各任务产出与实质修复

| 任务 | 报告 | 关键结果 |
| --- | --- | --- |
| T0 规格复核 | [docs/audit/r0-spec-conformance.md](audit/r0-spec-conformance.md) | 八表逐表结论 + 22 项不一致；2 项 high（状态码 409 vs 文档 400）；无 blocker |
| T1 R2 | [docs/audit/r2-review.md](audit/r2-review.md) | 修 **8 项**：B05 同 DID 双人→400 FIELD_INVALID、自委托拒、吊销实例不得参与委托（逐代取 revoked_at）、过期父委托 + authorise 祖先过期、ttl 坏类型 400、链根→FIELD_INVALID(human_owner)+链内重复拒、委托签名改用委托人身份密钥、吊销后重登记口径；+10 测试 |
| T2 R3 | [docs/audit/r3-review.md](audit/r3-review.md) | 修 **5 项**：签名前执行 tool≤120/role≤40/approvers≤4 上限、prev_hash 连续性（链头带 ≤500 历史）、认定书缺 audit_head 登记；60/60 探针、四档签名 schema 0 错误、11 域篡改全检出；+5 测试 |
| T3 R4 | [docs/audit/r4-review.md](audit/r4-review.md) | 修 **3 项**：POST /api/secretariat → 显式 405、letters?limit 坏值→400（不再外抛审计码）、面板/样式注释译英→零 CJK；含 team 模式 401/CSRF-403/viewer-403 探针 66/66；测试 11→31 |
| T4 QA | [docs/15](15-qa-r2r4-acceptance.md) | 独立验收：**11 PASS / 4 PARTIAL / 0 FAIL**；PARTIAL = 状态码口径、A13 自动化、A15 unanchored、B08/B11 |

## 3. 队长收口决议（已落文档/代码）

1. **状态码以运行时为准**：`SCOPE_WIDENED` / `DEPTH_EXCEEDED` / `IDENTITY_REVOKED` / `APPROVAL_REQUIRED` = **409**；
   `IDENTITY_MISSING` = **404**；字段类 = 400。已同步 `EXPANSION_ERROR_TABLE`、docs/12 §3/§7/§12、docs/13。
2. **B08 未登记 user DID**：保留 **TOFU**（本地为其生成审批密钥，`signer.kind="local_key"` + note 如实标注）；
   Agent DID 仍必须有出处。收紧需先定义"user 登记"语义，列入后续批次。
3. **B11 T10 盖章**：盖章是**独立凭证**（`digest=approval_digest(record)`，`verify_seal` 可复算），
   不写回已签名记录（避免为盖章作废原签名）；critical 的 `approval.seal` 由签名流程自动生成并验证。
4. **A13 自动化补齐**：`selfcheck.mjs` 新增 secretariat 面板断言（注册 id / 五区 / CSS 作用域 / 零 CJK），
   ext 源码纳入 i18n 扫描面，放行 8 个未上线端点；**09/27 两条既有红转绿，92/92 PASS**。
5. **B05 收口**：给了审批人但去重后不足 → 400 `FIELD_INVALID`(field=approvers)；完全没传 → 409 `APPROVAL_REQUIRED`。

## 4. 最终闸门（本机实测）

| 闸门 | 结果 |
| --- | --- |
| 总裁办四套单测 | 49 + 18 + 25 + 6 = **98/98 OK** |
| 全量单测 | **954 条，1 条已知红**（`test_e2e.ArtifactTests.test_signature_status_is_honest`：发布件 `signed_count=2` 但已签文件不在，需重跑签名脚本；非本轮引入） |
| 文案闸门 | `i18n_lint` **PASS 5/5**（1115 键 / 27 域 / 错误码 168） |
| 契约闸门 | 路由 41 条；docs/03 §6.5 34 行/36 路径一致；**唯一失败 = 8 个非总裁办前端端点** |
| 前端自检 | **92/92 PASS** |
| 认定书离线复核 | 示例 PASS；篡改 `actions[0].timestamp` → FAIL（exit 1） |
| 浏览器实测 | 面板五区渲染；登记身份后计数 0→1；认定书列表正确；截图 `docs/05-evidence/r4-secretariat-panel.png` |

## 5. 残余风险（如实登记，不在本轮修）

1. **team 模式入库签名未接**：`signing.enabled=false` + note；需要 actor→owner_did 映射（测试已钉住现状）。
2. **8 个前端幻影端点**（analytics/compliance/evidence/groups/preferences/rollback/shares/suggestions）：
   界面调用、后端未挂载；属 4.x 遗留，不在总裁办族。
3. **PDF 导出未接**：认定书正式载体是 JSON（`tools/verify_letter.py` 可离线复核）；PDF 只是展示层。
4. **审批签离线不可复算**：需要身份账本片段；认定书与面板文案已如实标注。
5. **审批人私钥在本地 keyring（0600）**：能读该文件的进程可代签；语义边界"本地记录并签名"不变。
6. **同 id 记录覆盖**：审批签/盖章绑定 `record_id`，前提是 id 唯一（R4 写路径以 id 为幂等键）。
7. **`prev_hash` 连续性依赖本地链头历史**（HEAD_HISTORY=500）；离线复核靠 records_root + 逐条签名。
8. **T6 verify 只报告不拦截**：面板不得把"报告"渲染成"已拦截"。
9. **测试数据副作用**：部分历史 e2e 用例仍会写仓库 `var/`（gitignored）；收口后已清理 `var/features`。
10. **`EXPANSION_ERROR_TABLE` 是翻译表**：改它只影响对外文案/状态，不改 `_status_for` 运行时事实。

## 6. 交付物索引

- 规格：[docs/12](12-secretariat-delta-spec.md)（§12 收口决议）
- 角色报告：[docs/13](13-r2-r3-production-report.md)、[docs/14](14-r4-production-report.md)
- 独立验收：[docs/15](15-qa-r2r4-acceptance.md)
- 独立复核：[r0](audit/r0-spec-conformance.md)、[r2](audit/r2-review.md)、[r3](audit/r3-review.md)、[r4](audit/r4-review.md)
- 证据：[面板截图](05-evidence/r4-secretariat-panel.png)、[示例认定书](05-evidence/r4-responsibility-letter.json)
- 代码：`agent_explainer/secretariat.py`、`server.py`、`ingest.py`、`web/ext/secretariat.*`、`tools/verify_letter.py`、`tools/gen_api_contract.py`
- 测试：`tests/test_secretariat*.py`（4 个文件，98 条）
