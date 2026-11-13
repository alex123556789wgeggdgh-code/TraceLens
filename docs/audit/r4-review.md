# R4 架构师复核（接口 / 面板 / 契约集成）— t4

- 复核人：architect（架构师）· 团队 `tracelens-secretariat-closeout` · 任务 t4
- 日期：2026-11-12 · 工作目录 `agent-explainer/`
- 依据：`docs/12-secretariat-delta-spec.md` §5/§6/§7/§10（表 E/F/G + A10–A15）、
  `docs/14-r4-production-report.md`、`docs/api_contract.json`、`docs/03-architecture.md` §6.5
- 写作用域：`agent_explainer/server.py`、`web/ext/secretariat.js|.css`、`tools/gen_api_contract.py`、
  `tools/verify_letter.py`、`docs/api_contract.json`、`docs/03-architecture.md`、
  `tests/test_secretariat_api.py`、`tests/test_secretariat_autosign.py`、本文件。
  `agent_explainer/secretariat.py`、`web/selfcheck.mjs`、`web/app.js`、`docs/12/13/14` 只读。

## 结论

**有条件 PASS。** T1–T10 + 族入口的路径/方法/权限/CSRF/错误体与 docs/12 §6 冻结表逐条一致；
记录级入库签名（开与关）、认定书离线导出与 CLI 退出码、面板五区与双语诚实文案均实测通过。
本轮在写作用域内修 3 项（族入口 405 缺失、T7 列表 `limit` 外抛审计域码、面板零 CJK 未达标），
新增 13 条测试（api 11 条 + autosign 2 条），套件从 11 条涨到 31 条，全绿。
残余缺口 8 项见 §5，其中 A13 的自动化断言缺失需要 lead 授权改 `web/selfcheck.mjs`（本轮在写作用域外）。

---

## ① 接口表核对结果（T1–T10 + 族入口）

核对方法：**不读文档自述**，用真发 HTTP 的探针 `.tmp/r4_probe.py`（66 项断言，66/66 PASS，
退出码 0）+ `docs/api_contract.json`（代码抽取）逐条比对 docs/12 §6 表 F。探针输出脱敏：
不打印密钥 / 签名 / Cookie / 口令，只打印状态码、码名、布尔与长度。

| # | 路径 | 方法 | 能力（实测=契约=docs/12） | CSRF | 实测状态码与码 | 结论 |
|---|---|---|---|---|---|---|
| T1 | `/api/secretariat/status` | GET | `audit.read` | 免 | 200；载荷含 `enabled/mode/version/counts/risk_policy/chains/delegation/signature/keyring/degraded/paths` | ✅ 与 §6 一致 |
| T1 | `/api/secretariat`（族入口） | GET | `audit.read` | 免 | 200，载荷与 `/status` **逐字节相等** | ✅ 等价 T1 |
| T1 | `/api/secretariat` | POST | 只读端点 | — | **修前 404 `ROUTE_NOT_FOUND` → 修后 405 `METHOD_NOT_ALLOWED`** | ⚠ 修复 F1 |
| T2 | `/api/secretariat/identities` | GET | `audit.read` | 免 | 200 `{identities,count}` | ✅ |
| T2 | `/api/secretariat/identities` | POST | `settings.write` | 吃 | 201 `{identity}`；`owner_did` 落账本、`fingerprint` 16 位、响应无 `seed`；坏 `agent_id` → 400 `SECRETARIAT_FIELD_INVALID` | ✅ |
| T3 | `/api/secretariat/identities/{id}/revoke` | POST | `settings.write` | 吃 | 200 + `revoked_at`；未知 id → 404 `SECRETARIAT_IDENTITY_NOT_FOUND`；多一段路径 → 404 `ROUTE_NOT_FOUND`（统一错误体） | ✅ 助手 `identity_route` |
| T4 | `/api/secretariat/delegations` | GET | `audit.read` | 免 | 200 + `max_depth=5` | ✅ |
| T4 | `/api/secretariat/delegations` | POST | `settings.write` | 吃 | 201 + `depth=1`；子委托扩权 → **409** `SECRETARIAT_SCOPE_WIDENED`（见 §5-5 状态码漂移） | ✅ 族助手无 |
| T5 | `/api/secretariat/records/{id}/sign` | POST | `settings.write` | 吃 | 200 + `verification.ok`；重复签 → 400 `SECRETARIAT_FIELD_INVALID(field=signature)`；缺记录 → 404 `SECRETARIAT_RECORD_MISSING` | ✅ 助手 `record_sign_route` |
| T6 | `/api/secretariat/verify` | POST | `audit.read` | 免 | 200，`results[0].checks` = `signature/entry_hash/prev_hash/identity/tsa/chain/approval/seal`，顶层 `tsa_state=local_only`；坏入参 → 400 `SECRETARIAT_FIELD_INVALID(field=records)` | ✅ |
| T6 | `/api/secretariat/verify` | GET | 只读端点 | — | 405 `METHOD_NOT_ALLOWED`（契约 `method_not_allowed.GET` 已登记） | ✅ |
| T7 | `/api/secretariat/letters` | GET | `audit.read` | 免 | 200 `{letters,count,limit}`；列表项是**摘要**：`id/subject/created_at/responsibility/records(条数,int)/records_root/audit_head/signature`，无 `actions` 正文 | ✅ 与面板所需字段完全一致 |
| T7 | `/api/secretariat/letters` | POST | `settings.write` | 吃 | 201 + `verification.ok`；空 `record_ids` → 400 `SECRETARIAT_LETTER_EMPTY`；缺记录 → 404 `SECRETARIAT_RECORD_MISSING`；>200 条 → 400 `SECRETARIAT_FIELD_INVALID` | ✅ |
| T7 | `?limit=` | GET | `audit.read` | 免 | **修前 400 `AUDIT_QUERY_INVALID`（审计域码）→ 修后 400 `SECRETARIAT_FIELD_INVALID(field=limit)`**；`limit=99999` → 200 且 `limit=500`（如实截断） | ⚠ 修复 F2 |
| T8 | `/api/secretariat/letters/{id}` | GET | `audit.read` | 免 | 200 `{letter,verification}`；未知 id → 404 `SECRETARIAT_LETTER_NOT_FOUND` | ✅ 助手 `letter_route` |
| T9 | `/api/secretariat/letters/{id}/verify` | POST | `audit.read` | 免 | 200 `ok=true`；未知 id → 404 同码 | ✅ 助手 `letter_verify_route` |
| T10 | `/api/secretariat/seal` | POST | `settings.write` | 吃 | 200 + `seal.signature` + `verification.ok`；缺 `record_id` → 400 `SECRETARIAT_FIELD_INVALID(field=record_id)` | ✅ |
| T10 | `/api/secretariat/seal` | GET | 只读端点 | — | 405 `METHOD_NOT_ALLOWED` | ✅ |
| — | `PATCH /api/secretariat/identities` | PATCH | 全 verb 兜底 | — | 405 + 统一错误体 | ✅ |

**动态路由助手（docs/12 §6「形状写在助手函数里」）**：4 个全部到位并已登记 ——
`identity_route` / `record_sign_route` / `letter_route` / `letter_verify_route`
（`tools/gen_api_contract.py:69-85`），契约里对应 4 条 `match="family"` 路由
（`agent_explainer/server.py:3997-4040` 定义，`do_GET:4177` / `do_POST:4434,4451,4474` 使用）。

**权限 / CSRF 矩阵（team 模式，真发 HTTP，`SecretariatTeamModeTests`）**：
未登录 GET T1 → 401 `AUTH_REQUIRED`；未登录 POST T2 → 401；admin Cookie GET → 200；
Cookie 写操作缺 CSRF 头 → 403 `CSRF_FAILED`（T2/T4/T7/T10 四条写路径逐条实测）；
带 `X-CSRF-Token` → 201；GET 带 Cookie 免 CSRF → 200；
viewer（有 `audit.read`、无 `settings.write`）GET → 200、POST → 403 `ROLE_FORBIDDEN`；
令牌通道免 CSRF（与既有 4.3 口径一致）。local 模式匿名可读 T1/T2/T4/T6/T7/T8/T9。

**错误体形状**：14 条负例（404/400/405 混合）全部返回
`{code,message,detail,request_id}` 四字段齐备（`test_every_secretariat_error_carries_the_unified_body`）。

**A4 不泄密**：T1 状态与 T2 身份响应内不含 `seed` / 私钥材料（keyring 只出公钥与指纹）。

**A11 审计链头绑定**：`letter["audit_head"] == service.audit_chain_status()["head"]`（签发时刻），
`records_root` 长 64；离线 CLI 复算 `ok=true`（`test_letter_binds_the_live_audit_head`）。

**A10 离线导出**：`python tools/verify_letter.py letter.json` 退出码 0（人读输出含 `PASS`
与 `local-tsa` 边界说明）；篡改 `subject` 后 `--json` 退出码 1，`problems` 含
`SECRETARIAT_SIGNATURE_INVALID`，`offline_boundary.not_verifiable` 含 `rfc3161_timestamp`
（`test_verify_letter_cli_exit_codes`）。

**面板↔服务端字段契约**：`web/ext/secretariat.js:356-362` 的列表列用
`item.subject / item.created_at / item.responsibility / item.records`，服务端列表摘要
逐字提供这些键（探针 #30/#31）。**无漂移**（此前 docs/14 的"生成时间/涉及记录"说法成立）。

**入库签名链（必做 2，`sign_on_write=True`）**：登记身份 → `POST /api/import` →
`signing={enabled:true,signed:1,failures:0}` → `GET /api/records/<id>` 带 `signature`，
且 `agent_pubkey` **等于账本里登记身份的公钥**（不是自动注册的临时自签）、`human_owner`
等于登记的 `owner_did`、`prev_hash` 为创世 64 个 0、记录过 `action_record.schema.json` →
`POST /api/secretariat/verify` `ok=true` 且 `checks.identity="registered"`。
关闭开关：`signing.enabled=false`、记录无 `signature`、verify 如实报
`SECRETARIAT_SIGNATURE_INVALID`（不假装通过）。

---

## ② 命令实测（全部本轮实跑，退出码如实）

| 命令 | 结果 | 退出码 |
|---|---|---|
| `python -m unittest tests.test_secretariat_api tests.test_secretariat_autosign -q` | `Ran 31 tests ... OK`（修前基线 11 条 OK） | 0 |
| `python .tmp/r4_probe.py`（66 项接口/权限/CSRF/签名断言，脱敏输出） | `TOTAL=66 FAIL=0` | 0 |
| `python tools/gen_api_contract.py --check` | 路由 41 条 / 43 常量 / 公开 5 / 显式 405 组 3；权限矩阵 4 角色 8 能力（admin=8, editor=5, local=7, viewer=3）；错误码 168（有键 75 / 回退 93）；**docs/03 §6.5 逐条比对：文档 34 行 / 36 条路径 ↔ 契约 36 条路径，方法列一致，无 ⚠ 行**；可重复生成 39246 字节；**唯一 FAIL = 前端调用面 8 个域外路由** `['/api/analytics','/api/compliance','/api/evidence','/api/groups','/api/preferences','/api/rollback','/api/shares','/api/suggestions']`（均非总裁办族，与基线相同） | 1（预期：域外端点） |
| `python tools/gen_api_contract.py --write` | 契约再生成成功（39220 → **39246** 字节，差值 = 新增 `POST /api/secretariat` 的 405 声明） | 0 |
| `node agent_explainer/web/selfcheck.mjs` | `89/91 passed, 2 FAILED`：FAIL 09 `no orphan dictionary copy`、FAIL 27 `offline purity`（`endpoint allowlist changed`）。**与改动前基线完全同一 89/91、同一两条 FAIL**（面板注释翻译未影响自检结果） | 1 |
| `python tools/i18n_lint.py` | 5 项全通过：zh 1115 / en 1115 键 / 27 域；硬编码文案 CJK 命中 0；错误码 168；embed.js/keys.md 同步；双语夹具 12×2 | 0 |
| `python tools/i18n_merge.py` | `zh=1115 en=1115 added=0`，双语一致，embed.js 已同步 | 0 |
| `python -m unittest discover -s tests -p "test_*.py" -q` | `Ran 954 tests ... FAILED (failures=1, skipped=3)`：唯一红 = 既有 `test_e2e.ArtifactTests.test_signature_status_is_honest`（t3 基线同一条），详见 §2.1 | 1（既有红，非本轮引入） |

### ②.1 全量 discover 实测（本轮补记）

`python -m unittest discover -s tests -p "test_*.py" -q` →
**`Ran 954 tests in 164.243s` / `FAILED (failures=1, skipped=3)`，退出码 1。**

- 唯一红 = `test_e2e.ArtifactTests.test_signature_status_is_honest`
  —— 与 t3 交付时登记的基线（`Ran 934, failures=1`）**同一个既有红**，
  与总裁办族无关；本轮没有新增红，也没有把既有红修掉（不在写作用域）。
- 总裁办族内套件实测：`test_secretariat` 49 + `test_secretariat_contract` 18
  + `test_secretariat_api` 25 + `test_secretariat_autosign` 6 = **98** 条
  （前两项为 t3 基线数，本轮未改动那两份文件；后两项本轮实测）。
- 说明：全量条数 934 → 954 的差额含本轮 13 条与同期其他成员新增用例；
  本轮只为自己新增的 13 条负责，实测全绿。
- 注：`python -m unittest discover -s tests -t .` 会因 `tests/` 非包而
  `ImportError: Start directory is not importable`；可用口径是
  `discover -s tests -p "test_*.py"`（登记给 QA，避免重复踩）。

---

## ③ 契约与自检结论

**契约（`docs/api_contract.json`，唯一来源=代码）**：14 条总裁办 `(path, verb)` 与 docs/12 §6
冻结表逐条相等（方法集合、`abilities`、`csrf` 三项都比对，见
`test_contract_matches_frozen_t1_t10_surface`）；4 条动态路由 `match="family"` 且助手名与
`ROUTE_HELPERS` 对齐（`test_dynamic_routes_use_registered_helpers`）；只读族端点的 405 声明齐备
（`test_read_only_family_endpoints_declare_405`）；docs/03 §6.5 与契约路径集合互无多余
（`test_docs_03_section_6_5_is_in_sync`，`contract_only_paths=[]`、`doc_only_paths=[]`）。

**docs/03 §6.5**：34 行 / 36 条路径 ↔ 契约 36 条，方法列一致，无 ⚠ 行（`--check` 实测）。
本轮的 405 补充只进入 `method_not_allowed` 清单，不新增 §6.5 行，因此 34/36 不变。

**自检（`node web/selfcheck.mjs`）：89/91，FAIL 09 与 FAIL 27，改动前后同值。** 根因定位（只读结论）：

1. **FAIL 09「no orphan dictionary copy」把全部 `secretariat.*`（165 叶键）判为孤儿。**
   根因不在字典，而在引用扫描面：`web/selfcheck.mjs:831` 定义
   `collectReferencedKeys(appSource)`，`:927` **只用 app.js 源码调用它**；
   `web/ext/*.js`（core/cards/groups/sharing/secretariat）里的键从不进入扫描面，
   所以 `trust.* / cards.rollback.* / groups.* / sharing.* / secretariat.*` 一起被列成"从未渲染"。
   这是 4.0 ext 化以来的既有结构性缺口，总裁办只是新增的一批受害者。
2. **FAIL 27「offline purity」的 allowlist 是硬编码字面量**：
   `web/selfcheck.mjs:1544-1549` 的 21 项白名单 vs `web/app.js` 的 30 个端点
   （含 `api/secretariat` 与 4.x 的 rollback/evidence/groups/shares/analytics/suggestions/preferences/compliance）。
3. **A13 没有自动化断言**：在 `web/selfcheck.mjs` 全文 grep `secretariat|trust-panel|data-zone`
   共 13 处命中，**其中 0 处含 `secretariat` 或 `trust-panel`** —— 即验收表 A13
   （"`node web/selfcheck.mjs` 通过 + `?probe=1` 显示 `trust-panel` 与 5 个 `[data-zone]`"）
   目前**没有任何机器断言**，docs/14 里"前端自检 89/91"的表格无法证明面板被装配。
   面板侧的可核对锚点已具备（`section(host, key)` 写 `dataset.zone`，五区齐，
   `Ext.register({id:'secretariat', tab:{permission:'audit.read'}})`），本轮以源码结构断言补位
   （`test_panel_renders_five_zones_in_spec_order`）。
   **需要 lead 授权的修复位置**（不在本轮写作用域）：
   - `web/selfcheck.mjs:1544-1549`：`allowedPaths` 追加 `'api/secretariat'`（并同步 4.x 的 8 项）；
   - `web/selfcheck.mjs:831` + `:927`：`collectReferencedKeys` 扩展为扫描 `web/ext/*.js`
     （传入 ext 源码拼接），否则 FAIL 09 永远不可能变绿；
   - 新增一条断言：`?probe=1` 装配后 `#trust-panel` 存在且
     `#trust-body[data-tab="secretariat"]` 内 `[data-zone]` 恰为
     `status/identities/delegations/letters/verify` 五个。
   （`boot`/`makeSandbox` 未从 `selfcheck.mjs:5385` 导出，故本轮无法在 `.tmp` 脚本里做无头面板探针。）

---

## ④ 修复项（全部在写作用域内，全部带测试）

| # | 缺陷（架构师口径） | 位置 | 修法 | 测试 |
|---|---|---|---|---|
| F1 | 族入口 `POST /api/secretariat` 落 404 `ROUTE_NOT_FOUND`，与同族只读端点 `POST /api/secretariat/status` 的 405 不同口径；对"族根"POST 的调用方（脚本/探测）拿到 404 会被误读成端点不存在 | `agent_explainer/server.py:4489-4495`（只读 405 元组） | 把 `API_PATH_SECRETARIAT` 加入只读 405 元组，注释说明"族入口 = status 摘要，只读"；契约 `method_not_allowed.POST` 随之登记并 `--write` 落盘 | `test_family_entry_equals_status_and_rejects_post`、`test_read_only_family_endpoints_declare_405` |
| F2 | `GET /api/secretariat/letters?limit=<坏值>` 复用审计分页助手 `_query_int`，外抛**审计域**码 `AUDIT_QUERY_INVALID`；面板按 `secretariat.error.<code>` 查文案 → 查不到，只能回退裸 message，等于 A14 双语口径被绕过；且 `limit` 无上限语义（服务层悄悄夹到 500） | `agent_explainer/server.py`：新增 `_secretariat_limit()`（`:4235`）、常量 `LETTER_DEFAULT_LIMIT=50` / `LETTER_LIST_MAX=500`（`:1731` 旁）、`secretariat_letters_payload`（`:1853`）夹取并回显 `limit`、GET 路由 `:4170` 改用新助手 | 坏值/负数/`0`/小数 → 400 `SECRETARIAT_FIELD_INVALID(field=limit)`（docs/12 §3 码族内）；`limit=99999` → 如实截断 500 并回显；缺省 50 回显 | `test_letters_limit_bad_value_uses_secretariat_domain_code`（abc/0/-3/1.5 四种坏值 + 统一错误体）、`test_letters_list_matches_panel_contract` |
| F3 | A14 字面「`web/ext/secretariat.js` 零 CJK」未达标：文件内 162 个汉字全在注释（头部纪律块 + 五区分隔注释）；`secretariat.css` 另 22 个汉字。ext 层其余文件（`core.js`、`cards.js`、`core.css`）为 0，属总裁办族自身的不一致 | `web/ext/secretariat.js`（头块 + 5 处分区注释译英）、`web/ext/secretariat.css`（头块 2 行译英） | 注释改英文，语义与 docs/07 §6 / docs/12 §6 纪律保留；不改任何渲染逻辑 | `test_panel_and_css_have_zero_cjk`（实测 CJK 命中：js 162→**0**，css 22→**0**；文件 24224 → 24396 字节） |

新增测试（13 条，写作用域内两份测试文件）：

- `tests/test_secretariat_api.py`（7 → 25）：族入口等价 + 405、T7 列表形状=面板字段 +
  上限截断、`limit` 坏值本域码、14 条负例统一错误体、A11 审计链头绑定、A10 CLI 退出码、
  契约↔冻结表（14 行 / 权限 / CSRF）、动态路由助手登记、405 声明、docs/03 §6.5 同步、
  面板五区顺序 + `data-zone` + 注册权限、单一请求门（无 `fetch`/`XMLHttpRequest`）+
  写按钮 `disabled: !writable`、面板与样式零 CJK、面板引用键在中英文典都存在、
  中英文典键一一对应（zh 有汉字 / en 零汉字）、A15 诚实条目 p1–p5 + 禁宣称扫描
  （`可信时间戳/硬件密钥/已上链/trusted timestamp/hardware key/blockchain/anchored`
  只允许以否定式出现）、team 模式 401/CSRF-403/201 矩阵、viewer 只读 403 `ROLE_FORBIDDEN` +
  team 入库签名诚实关闭。
- `tests/test_secretariat_autosign.py`（4 → 6）：登记身份拥有入库签名（公钥/owner 来自账本 +
  schema + verify `identity="registered"`）、关开关后 verify 如实报 `SECRETARIAT_SIGNATURE_INVALID`。

---

## ⑤ 残余缺口（如实登记，不在本轮修）

1. **team 模式入库签名 owner→DID 未接（已知边界，本轮未改代码）**：team 模式
   `signing.enabled` 如实为 `false`，记录不签名，`signing.note` 写明"team 模式待
   actor→DID 映射（后续批次）"；面板/接口没有任何地方宣称 team 模式已签名。
   已用测试钉住现状（`test_team_mode_viewer_read_only_and_honest_signing`）。
2. **契约 FAIL 的 8 个前端幻影端点**：`/api/analytics`、`/api/compliance`、`/api/evidence`、
   `/api/groups`、`/api/preferences`、`/api/rollback`、`/api/shares`、`/api/suggestions`
   —— 界面调用、后端没有（4.x 遗留，非总裁办族）。修它需要改 `web/app.js`
   或补服务端路由，不在本轮写作用域；契约的"唯一失败"就是这一项，与 docs/14 说法一致。
3. **A13 自动化断言缺失**（§3 详述修复位置）：`selfcheck.mjs` 零条 secretariat 断言，
   FAIL 09/27 因此长期红且无法靠字典侧修好；需要 lead 授权改 `web/selfcheck.mjs`。
4. **状态码漂移（B04/B06 同族，归 lead 收口）**：运行时 `SECRETARIAT_SCOPE_WIDENED` /
   `DEPTH_EXCEEDED` / `IDENTITY_REVOKED` 返回 **409**，而 docs/12 §6 与
   `EXPANSION_ERROR_TABLE`（`server.py:528-562`）把这三项标成 400（`IDENTITY_MISSING` 为 409）。
   本轮**不改运行时**（改码会影响 R2/R3 已签收的断言），建议以运行时 409 为事实来源，
   由 lead 决定改文档还是改表；本轮只在测试与探针里如实登记 409。
5. **GET 落在 POST-only 动态路由上返回 404 `STATIC_NOT_FOUND`**（
   `/api/secretariat/records/{id}/sign`、`/api/secretariat/letters/{id}/verify`）：
   与既有先例 `/api/ingest/tokens/{id}/revoke` 同口径（助手族不匹配 → 静态兜底 404），
   本轮**保留行为**并登记为低severity观察；若要统一成 405，需要在四个助手后加 verb 判定，
   属服务端路由形状改动，建议与 §5-4 一并由 lead 定口径。
6. **PDF 导出未接**：认定书导出仍是"下载 JSON + 本地 CLI 复核"（面板
   `secretariat.detail.download*` 文案 + `tools/verify_letter.py`）；docs/12 §5 的离线口径
   成立，但"打印成 PDF 给总裁签"这一步没有实现。
7. **面板文件字节数变化（占位替换风险自查）**：`web/ext/secretariat.js` 由 **24224 B → 24396 B**，
   这是 F3 的注释翻译（新增英文字节），**不是** `.tmp` 占位替换。`.tmp/qa_backup_secretariat.js`
   （227 B，早前 QA 留下的占位备份）本轮未被复制回工作树，真实文件全程是唯一被编辑对象；
   编辑前后都用同一文件跑了 `node web/selfcheck.mjs`（两次都是 89/91）与零 CJK 断言。
8. **观察（不算缺陷）**：`GET /api/secretariat/status` 的 `keyring` / `paths` 块含服务端绝对路径
   （`secretariat.py:413-416` 的 `facts()` 返回 `path`）。这与既有 `/health` 的
   `audit.path`（docs/03 §6.6，公开端点）同口径，且 T1 需要 `audit.read`，
   不构成新增泄漏面；若要收紧，应在服务端（写作用域内）做 team 模式路径脱敏，建议 lead 定级。
   同类口径噪声：`tools/i18n_lint.py` 的"无键码"清单列出了
   `SECRETARIAT_FIELD_INVALID/LETTER_EMPTY/LETTER_NOT_FOUND/RECORD_MISSING`，而
   `secretariat.error.secretariat_field_invalid` 等键实际存在（面板能查到文案）——
   属 linter 报告口径，不影响运行，且 `i18n_lint.py` 不在本轮写作用域。
