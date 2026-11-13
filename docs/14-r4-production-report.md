# 总裁办 R4 制作报告（全栈 / 集成）

> 制作日期：2026-11-11 · 规格依据：[docs/12](12-secretariat-delta-spec.md)
> 对应角色：**全栈（前端 + 集成者）**；模型分工事实见 [docs/13 §0](13-r2-r3-production-report.md)。

## 1. 交付清单

| 层 | 交付 | 文件 |
| --- | --- | --- |
| 接口 | T1–T10 + 族入口 `GET /api/secretariat`；权限沿用既有 8 项能力；写操作 POST + CSRF | [server.py](<../agent_explainer/server.py>) |
| 记录级集成 | 导入 / 上报落库前自动签名（`sign_on_write`），`--no-secretariat` 可关；实现在 server.py 的 `sign_on_write_record`，ingest.py 只注入 `signer` 钩子 | [server.py](<../agent_explainer/server.py>)、[ingest.py](<../agent_explainer/ingest.py>) |
| 前端 | 五区面板：状态与策略 / 身份 / 委托 / 认定书 / 验签·盖章·行为签名 | [secretariat.js](<../agent_explainer/web/ext/secretariat.js>)、[secretariat.css](<../agent_explainer/web/ext/secretariat.css>) |
| 文案 | 中英同构 155 条；全部经 `i18n_merge` + `i18n_lint` | [secretariat.zh.json](<../agent_explainer/web/i18n/parts/secretariat.zh.json>) 等 |
| 离线工具 | `verify_letter.py`：认定书签名 + records_root + 逐条动作签名；BOM 容错 | [tools/verify_letter.py](<verify_letter.py>) |
| 契约 | 生成器登记 4 个路由助手；`api_contract.json` 重新生成；docs/03 §6.5 增补 11 行 | [gen_api_contract.py](<gen_api_contract.py>)、[docs/03](03-architecture.md) |
| 测试 | 接口 7 条 + 自动签名 4 条 | [test_secretariat_api.py](<../tests/test_secretariat_api.py>)、[test_secretariat_autosign.py](<../tests/test_secretariat_autosign.py>) |
| 证据 | 浏览器截图 + 示例认定书 | [截图](05-evidence/r4-secretariat-panel.png)、[认定书](05-evidence/r4-responsibility-letter.json) |

## 2. 接口（T1–T10）

| # | 路径 | 方法 | 权限 |
| --- | --- | --- | --- |
| 族入口 / T1 | `/api/secretariat`、`/api/secretariat/status` | GET | `audit.read` |
| T2 | `/api/secretariat/identities` | GET / POST | `audit.read` / `settings.write` + CSRF |
| T3 | `/api/secretariat/identities/{id}/revoke` | POST | `settings.write` + CSRF |
| T4 | `/api/secretariat/delegations` | GET / POST | `audit.read` / `settings.write` + CSRF |
| T5 | `/api/secretariat/records/{id}/sign` | POST | `settings.write` + CSRF |
| T6 | `/api/secretariat/verify` | POST | `audit.read` |
| T7 | `/api/secretariat/letters` | GET / POST | `audit.read` / `settings.write` + CSRF |
| T8 | `/api/secretariat/letters/{id}` | GET | `audit.read` |
| T9 | `/api/secretariat/letters/{id}/verify` | POST | `audit.read` |
| T10 | `/api/secretariat/seal` | POST | `settings.write` + CSRF |

形状：平铺 JSON（`{"ok": true, <具名载荷>}`），错误体 `{code, message, detail, request_id}`；
动态路由用 4 个路由助手并在 `tools/gen_api_contract.py` 的 `ROUTE_HELPERS` 登记，
生成器可识别（实测抽取 41 条 `(path, verb)`）。

## 3. 记录级集成（本轮补齐的"最后一块"）

- **触发点**：`POST /api/import`、`POST /api/ingest/events` 在 `store.put` 之前；
- **开关**：产品入口默认开，`--no-secretariat` 关；库默认 `sign_on_write=False`（915 条既有测试不受影响）；
- **更新语义**：事实变化后先摘旧签名再按 `previous` 重签；签名失败不丢数据，响应 `signing` 块给计数与原因；
- **诚实边界**：仅 `local` 模式；team 模式 owner 需 actor→DID 映射（后续批次）；高危记录
  `allow_unapproved=True`，审批缺口由 `verify_record` 报 `SECRETARIAT_APPROVAL_REQUIRED`。

## 4. 前端面板

五区（`data-zone`）：`status`（计数 / 三条账本链 / 风险策略表 / 降级项）、`identities`（列表 + 登记 + 吊销）、
`delegations`（列表 + 创建 + 最大深度）、`letters`（列表 + 选记录生成 + 详情 + JSON 下载 + 验签）、
`verify`（粘贴记录复算 + 行为签名 + 总裁办盖章）；底部"本页不假装的事"列出 HSM/TSA/签字意愿/append-only/离线五条边界。

- 只走 `ctx.get/post`（`app.js` 唯一请求门），不自己 fetch；
- 写按钮在 `settings.write` 缺失时禁用（服务端仍 403）；
- 离线/请求失败用 `ctx.renderError` 如实显示，不伪造数据；
- 浏览器实测：登记身份后计数 0→1；认定书列表显示主题/生成时间/涉及记录；
  截图 `docs/05-evidence/r4-secretariat-panel.png`。

## 5. 离线认定书

`tools/verify_letter.py <letter.json>`：验认定书签名、复算 `records_root`、逐条复算动作签名；
可复算/不可复算边界写进输出与 docs/12 §5。示例：
`python tools/verify_letter.py docs/05-evidence/r4-responsibility-letter.json` → PASS；
把 `actions[0].timestamp` 改掉 → FAIL（exit 1）。

## 6. 闸门实测

| 闸门 | 结果 | 说明 |
| --- | --- | --- |
| `test_secretariat_api` | 25/25 OK | T1–T10 真 HTTP |
| `test_secretariat_autosign` | 6/6 OK | 导入 / 更新重签 / 上报 / 关闭 |
| 全量单测 | 954 条，1 条已知红 | 发布件未重签（docs/09 §1.5），非本轮引入 |
| i18n | PASS 5/5 | 1115 键 / 27 域 |
| 契约 | 唯一失败 = 8 个非总裁办端点 | 41 路由、34 行/36 路径一致 |
| 前端自检 | 92/92 PASS | 收口新增面板断言，09/27 两条既有红一并转绿 |

## 7. 残余缺口（明确不假装完成）

1. team 模式入库签名需要 `actor → owner_did` 映射（当前只跳过并说明）。
2. `app.js` 里 8 个非总裁办端点仍指向未挂载路由（后续批次）。
3. 认定书未出 PDF：可离线验证的正式载体是 JSON（PDF 只作展示，需要时再接 reportlab）。
4. 前端 2 条自检失败（未引用键 / 离线纯度）是既有技术债，本轮只保证不新增失败。
