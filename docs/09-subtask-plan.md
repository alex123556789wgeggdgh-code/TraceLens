# TraceLens 5.0 · 子任务执行计划（R1–R12）

> 状态：R1–R4 已确认；R5–R12 待开工。本文件是**唯一排期来源**，与 [docs/07](07-secretariat-and-extensions.md)（实现契约）互补：
> `07` 说"必须做成什么样"，本文件说"谁、按什么顺序、做到什么程度算完"。
> 所有"现状"数字均为 2026-11-11 在本机实测，不是估计。

---

## 1. 实测基线

### 1.1 后端模块（`agent_explainer/`）

| 子系统 | 模块 | 状态 | 直接单测 |
| --- | --- | --- | --- |
| secretariat | `secretariat.py` 60KB | ✅ 完成 | `test_secretariat.py` 40 条 |
| rollback | `rollback.py` 42KB | ✅ 完成 | ❌ **无** |
| groups | `groups.py` 30KB | ✅ 完成 | `test_groups.py` 27 条 |
| sharing | `sharing.py` 35KB | ✅ 完成 | ❌ **无** |
| confidence | — | ❌ 缺失 | — |
| evidence | — | ❌ 缺失 | — |
| analytics | — | ❌ 缺失 | — |
| suggestions | — | ❌ 缺失 | — |
| compliance | — | ❌ 缺失 | — |

辅助且已就绪：`crypto_sig.py`（纯标准库 Ed25519）、`feature_store.py`（追加式账本）、`qr.py`、`audit.py`。

### 1.2 路由

- `server.py` 里 **42 个 `API_PATH_*` 常量**，其中 4.x 的 **27 条 (path, verb)** 已真实挂载。
- **5.0 的路由（T1–T38）一条都没挂**：常量在 `server.py:224–243`，错误码表已备，分发分支不存在。
- 前端已经在调 9 个尚不存在的端点（`/api/analytics`、`/api/compliance`、`/api/evidence`、`/api/groups`、`/api/preferences`、`/api/rollback`、`/api/secretariat`、`/api/shares`、`/api/suggestions`）——这是 `gen_api_contract.py --check` 当前 FAIL 的唯一原因。

### 1.3 前端（`web/ext/`）

| 文件 | 归属 | 状态 |
| --- | --- | --- |
| `core.js` / `cards.js` / `insights.js` | 壳 / 卡片装饰（置信度+回滚+证据+因果）/ 分析+建议 | ✅ 完成 |
| `secretariat.js` / `collab.js` / `compliance.js` | 总裁办 / 聚合+协作 / 合规导出 | ⚠️ **占位**（各 ~220B） |

### 1.4 工具闸门（实测）

| 闸门 | 命令 | 状态 |
| --- | --- | --- |
| i18n / 文案 / 错误码 | `python tools/i18n_lint.py` | ✅ **PASS 5/5**（805 键；本轮修好 keys.md 漂移） |
| 契约一致性 | `python tools/gen_api_contract.py --check` | ❌ FAIL（§1.2 的 9 个端点） |
| 单测 | `python -m unittest discover -s tests -p "test_*.py" -q` | 见 §1.5 |
| 纯离线 | `test_trace_adapter.OfflinePurityTests` | ❌ 1 条误报（`qr.py` 的 SVG `xmlns` 含 `http://`） |

### 1.5 单测基线（本轮已修 5 个真 bug，见 §2）

- 全量：**896 条**用例，**1 failure + 1 error**（两条都是发布件用例），3 skipped；单次干净运行耗时约 192s。
- `groups` 的「1000 条聚合 ≤ 2s」实测 **2.9 ms**（此前该用例因 `second=index` 溢出**从未真正执行**）。
- 已知红（**2 条**，都属发布件、与 5.0 无关，且都有明确原因）：

| 用例 | 原因 | 处理 |
| --- | --- | --- |
| `test_e2e.ArtifactTests.test_sidecar_exe_runs` | 现有 sidecar 产物在 `%TEMP%` 下建不出数据目录（WinError 5），本机无 PyInstaller、无法重建 | `pip install pyinstaller` 后 `tools/build_sidecar.py --vendor --build`（**外部依赖**） |
| `test_e2e.ArtifactTests.test_signature_status_is_honest` | 产物声明 `signed_count=2`，但重打包后未重签，已签文件不在 | 重跑 `tools/sign_and_hash.ps1`（发布环节） |

> `tools/build_sidecar.py --check` 的如实结论：**构建不可用（PyInstaller 不可用）**；现成 onedir 产物仍在，但「可用于冒烟，不代表本次构建成功」。
> **不要同时跑两套全量**：`test_auth_multituser` 的 local-mode 对拍用例会因抢占端口/临时目录而偶发 ERROR（本轮实测复现过一次，单独跑即通过）。

---

## 2. 本轮已完成的 B0 修补（不需要排期，已落盘）

| # | 修补 | 文件 | 验证 |
| --- | --- | --- | --- |
| 1 | `i18n/keys.md` 从 721 键同步到 805 键 | `web/i18n/keys.md` | `i18n_lint.py` → PASS 5/5 |
| 2 | 三个发布件 `SIGNATURE-STATUS.json` 去 UTF-8 BOM | `desktop/build/dist/`、`release/`、`release/tracelens-0.4.5-release/` | `json.load` 不再抛 BOM 错 |
| 3 | `intervention_history` 并入「禁止合并」 | [server.py:268](../agent_explainer/server.py#L268) | 新增守卫测试 |
| 4 | `human_owner` / `delegation_chain` 一并禁止合并（防导入方派责任） | 同上 | 同上 |
| 5 | 新增字段白名单守卫（7 条不变量） | [tests/test_merge_fields.py](../tests/test_merge_fields.py) | 7 条全绿 |
| 6 | `groups` 自检夹具补一条**真无 `group_id`** 的记录 | [groups.py:651](../agent_explainer/groups.py#L651) | `python -m agent_explainer.groups` → exit 0 |
| 7 | 修 `test_groups.make_record` 的时间构造（`second=index` 溢出） | [tests/test_groups.py:48](../tests/test_groups.py#L48) | 「1000 条 ≤2s」首次真正跑通：2.9 ms |
| 8 | `intervention_history` 口径同步 | [docs/02 §数据模型](02-data-model.md) | 文档与代码一致 |
| 9 | 新增诚实降级清单 | [docs/10-honest-downgrade.md](10-honest-downgrade.md) | — |
| 10 | 新增错误码→文案键映射（生成物） | [docs/11-ux-copy-map.md](11-ux-copy-map.md) + `var/gen_copy_map.py` | 5.0 域缺口 0 |
| 11 | 纯净性白名单改以 `sys.stdlib_module_names` 为准（修 `importlib`/`inspect` 假红） | [tests/test_intervene_api.py](../tests/test_intervene_api.py) | 纯度测试 13 条 OK |
| 12 | 离线扫描剔除 SVG `xmlns` 命名空间（修 `qr.py` 假红） | [tests/test_trace_adapter.py](../tests/test_trace_adapter.py) | 同上 |
| 13 | 单测基线重跑并记录（896 条，仅剩 2 条发布件用例） | — | §1.5 |

---

## 3. 裁决表（此前待拍板的细节，全部落定）

| # | 问题 | 结论 | 依据 |
| --- | --- | --- | --- |
| D1 | `confidence` 缺失是 `null` 还是 `1.0` | **`null`**（未测量 ≠ 高置信） | [schema:290](../docs/schemas/action_record.schema.json) 已写 "缺失 = null"；遵守"不确定不说成肯定" |
| D2 | `confidence` 的来源枚举 | 服从既有枚举 `logprobs / tool_success_rate / hybrid / template / default`，**不得新增 `heuristic`** | schema:1320 |
| D3 | 干预历史能否由导入方提供 | **不能**（已移入 PROTECTED） | 本次修补 #3 |
| D4 | demo/synthetic 数据能否进合规证据包 | **不能**，R11 硬拒 | 伪造证据风险 |
| D5 | 契约文件的维护方式 | **生成**，不手写：加常量+分支 → `gen_api_contract.py --write` → `--check` 绿 | 生成器为唯一来源 |
| D6 | 5.0 写操作的权限 | **不新增能力**，复用 8 项既有能力（映射见 §4.3） | `auth.py:104 ROLE_PERMISSIONS` |
| D7 | 协作人如何进责任链 | 视为一次委托：`sharing` → 生成 `DelegationRecord`，不用旁路字段 | 规格 §5.5「责任链扩展到协作人」 |
| D8 | `critical` 双人审批 | 必须是**两个不同 `user` DID**（同 DID 双钥不算） | 防伪造双人 |
| D9 | 认定书的"责任判定"由谁下 | **人**显式选并签名；系统只整理证据与建议，UI 禁止自动判定 | 「AI 判责」不可辩护 |
| D10 | 回滚记录能否再被回滚 | **不能** | 防环 |
| D11 | v1 签名记录是否重签为 v2 | **不重签**，一经签名即终局 | 避免同一记录两版签名 |
| D12 | 时间字段 | 一律走注入时钟，禁止直调 `datetime.now()` | 7 天冷却 / journal 排序可测 |
| D13 | `agent_id` 形状 | `^[A-Za-z0-9._:-]{1,120}$`，拒绝 Windows 保留名 | 它会变成 keyring 键与文件名 |
| D14 | 单条记录体积 | ≤ 64KB/行；证据只存 `evidence_id`，字节入 `features/evidence/blobs/` | `FEATURE_STORE_LINE_TOO_LARGE` |
| D15 | 密钥库并发 | 写 tmp + 原子 rename + 单写者锁 | keyring 是整文件重写 |
| D16 | 导出验几条链 | 签名链 + 审计链，两条都验并都进包 | 只验一条会漏审计篡改 |
| D17 | 性能口径 | 10 万条 p95 ≤3s 为承诺线；**100 万条不承诺**（纯 Python 单机） | `groups` 实测 1000 条 2.9ms 的量级参考 |
| D18 | 性能数据来源 | 合成数据生成器（标 `synthetic=true`），脚本进 `tools/` | `var/records.jsonl` 仅 40KB/12 条 |
| D19 | 离线可用性 | 卡片装饰可读；三个新面板必须如实提示"需要服务端" | 见 §4.4 |

---

## 4. R5–R12 任务卡（精简版）

### 4.1 总表

| 编号 | 名称 | 负责人 | 人天 | 主要文件 | 核心验收 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| R5 | 置信度可信可视化 | 后端（主）+ 前端（验收） | 2 | 新增 `confidence.py`；`ingest.py` | `<0.5` 自动挂起且 reason 非空；`0.5–0.6` 必须带 `uncertainty` | R3 |
| R6 | 多模态证据与因果链 | 后端 + AI 研究员 | 4 | 新增 `evidence.py`；T14–T16 | 证据 sha256 可验；已签记录不追加证据；反事实结构化三段 | R3 |
| R7 | 多 Agent 聚合视图 | 前端 + 后端 | 3 | 新建 `collab.js`；T18–T21 | 10 Agent×500 条 ≤2s；归组准确率用固定金标准集 | R4、R2 |
| R8 | 共享与协作 | 全栈 | **4.5** | 续写 `collab.js`；T22–T25 | `view` 连按钮都不可用；过期读时判定；协作进审计链 | R7、R4、R3 |
| R9 | 行为分析仪表盘 | 后端 + 前端 | 3 | 新增 `analytics.py`；T26–T27 | 指标与构造数据精确相等；前端零重算；脱敏不可反推 | R3、R4 |
| R10 | 自动化改进建议 | 后端 + 规则引擎 | 2 | 新增 `suggestions.py`；T28–T31 | 3 次同向后第 4 次前出 1 条；拒绝后 7 天冷却 | R9 |
| R11 | 合规审计导出 | 后端 + 区块链集成 | 4 | 新增 `compliance.py`；T32–T37；`compliance.js` | 断链拒导出；二维码可扫回 `root_hash`；离线脚本仅标准库；`eth_tx_hash` 为 null | R3、R4、R6、R9 |
| R12 | 总裁办面板与认定书 | 全栈 | 3 | `secretariat.js`；T7–T9 | 认定书可异地离线验；五区可交互；`audit_head`/`records_root` 齐全 | R2、R3、R4、R8、R11 |

**合计：37 人天**（含 R8 补测试 +0.5）。

### 4.2 每张卡的"不可让步"验收（摘）

- **R5**：`confidence_source.method` 只能取 schema 枚举值；无来源时 `method="default"` 且 UI 不得把数字说成肯定。
- **R6**：证据字节入库、记录只存 id；`record_id` 外键读取时合并且**不触碰已签记录的 `entry_hash`**。
- **R7**：归组准确率必须有**金标准样本文件**（`tests/fixtures/grouping_gold.json`），否则该验收项作废。
- **R8**：`manage` 不得二次转授；邀请 token 只存哈希。
- **R9**：性能声明按 D17 写；导出 CSV 列名固定。
- **R10**：建议只改"默认值"，不自动改写用户参数。
- **R11**：bundle 内 `verify_offline.py` 只用标准库、无网络；`anchor_state="unanchored"` 必须出现在 UI。
- **R12**：认定书必须含 `audit_head` + `records_root`；盖章 digest 服务端算。

### 4.3 权限映射（复用既有 8 项能力，不新增）

| 路由族 | 能力 |
| --- | --- |
| T1 status、T6 verify、T12 rollback/plan、T26 dashboard | `records.read` |
| T2/T3 身份、T10 盖章、T22 分享、T31 偏好 | `settings.write`（`local`/`admin`） |
| T4 委派、T5 签名、T20 关闭组 | `data.write` |
| T11 回滚执行 | `intervene.decide` |
| T27 分析导出、T32–T37 合规导出 | `audit.export` |
| T25 权限查询 | `audit.read` |

> 若某路由在两表之间摇摆，**以"最小权限"落表**，并在 `api_contract.json` 里体现（由集成者统一写）。

### 4.4 离线可用性矩阵

| 功能 | `file://` 直开 | 必须显示的文案 |
| --- | --- | --- |
| 卡片装饰（置信度/回滚/证据） | 可读（只渲染已有数据） | 无 |
| 回滚 / 共享 / 导出 / 总裁办面板 | 不可用 | 「需要服务端」+ 不伪造数据 |
| 分析 / 建议面板 | 不可用 | 同上 |

---

## 5. 批次与写作用域

| 批次 | 内容 | 并行前提 |
| --- | --- | --- |
| B0 | §2 已完成的 10 项 + 3 项待做（§6） | — |
| B1 | R5 + R6 | 两块后端模块不重叠；`cards.js` 不动 |
| B2 | R7 + R9 + R10 | 三个不同文件；R7 先建 `collab.js` |
| B3 | R8 + R11 | R8 依 R7 同文件（**串行**）；R11 依 R6/R9 |
| B4 | R12 | 依赖前全部 |

**写作用域硬约束**：

- `server.py` 与 `docs/api_contract.json`：**唯一集成者**改（其他人交 handler + 一行注册说明）。
- `web/ext/collab.js`：R7 建、R8 续，**禁止并行**。
- `web/ext/cards.js` / `insights.js`：已完成，未经通知不得改。
- i18n：每人只写自己的 `parts/<域>.{zh,en}.json`，收尾统一 `i18n_merge.py` + `selfcheck.mjs --sync-keys --sync-embed`。

---

## 6. B0 待做（3 项，排在 B1 之前）

| # | 缺口 | 现状证据 | 修法 |
| --- | --- | --- | --- |
| 1 | **5.0 字段合并器不存在** | `EXPANSION_MERGE_FIELDS` / `EXPANSION_PROTECTED_FIELDS` 在全仓**无消费者**；`ingest.MERGE_FIELDS` 21 项不含任何 5.0 字段 → `group_id`/`multimedia_evidence`/`confidence_source` 今天根本进不了记录 | 实现 `merge_expansion(record, incoming)`，受两张名单约束；挂 import/ingest 路径；补测试 |
| 2 | `sign_record` 对 `human_owner`/`delegation_chain` 用 `setdefault` | [secretariat.py:529](../agent_explainer/secretariat.py#L529) 若入参自带则**保留入参** | 改为「校验：链根必须等于 owner、链尾必须等于本 agent、长度 ≤8 且 ≤深度 5」，不合法则 400 |
| 3 | 抽取器误报 | `i18n_lint.collect_error_codes()` 把 `seal`/`chain`/`count` 等 12 个小写单词当错误码 | 只接受 `^[A-Z][A-Z0-9_]{3,}$`；误报清单见 [docs/11 §4](11-ux-copy-map.md) |

---

## 7. 统一验收命令（每个人跑同一套）

```powershell
cd agent-explainer
python -m unittest discover -s tests -p "test_*.py" -q   # 单测（896 条基线）
python tools/i18n_lint.py                                # 文案闸门（必须 PASS 5/5）
python tools/gen_api_contract.py --check                 # 契约闸门（B1 起逐批转绿）
node agent_explainer/web/selfcheck.mjs                   # 前端自检
python -m agent_explainer.groups                         # 子模块自检（示例）
```

---

## 8. 残余外部依赖（**唯一需要人介入的部分**）

见 [docs/10-honest-downgrade.md §5](10-honest-downgrade.md)：TSA 服务、HSM/TEE、第二个真人、IPFS/ETH 节点、PyInstaller 安装。
这五项不提供时，系统按"显式降级"运行，**不阻塞任何子任务**。

---

## 9. 总裁办批次（2026-11-11）

总裁办 **R1–R4 已完成**并留证：规格冻结 `docs/12-secretariat-delta-spec.md`；
身份/签名契约修复 + T1–T10 路由 + 面板 + 离线认定书复核。
证据：`tests/test_secretariat*.py`（59 条）、`docs/05-evidence/r4-secretariat-panel.png`、
`docs/05-evidence/r4-responsibility-letter.json`（`tools/verify_letter.py` 可复跑）。
契约闸门仅剩 8 个非总裁办端点（见 docs/12 §11）。

