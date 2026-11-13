# CHANGELOG — TraceLens（行动透镜）

格式遵循 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循语义化版本（SemVer）。**产品版本以 `agent_explainer/server.py:SERVER_VERSION`
为唯一来源**（`/health` 上报的就是它），当前 `0.5.0`。

> 说明（诚实版）：`agent_explainer/__init__.py:__version__` 历史上是 `0.1.0`，与
> `SERVER_VERSION` 不一致（README 已知限制第 9 条）。**0.5.0 起两处都是 `0.5.0`**，
> 单一来源仍是 `server.py:SERVER_VERSION`；`python tools/build_sidecar.py --check`
> 会当场比对两个号并在不一致时报警（防止再漂）。

## [0.5.0] — 2026-11-12

### Added（新增）

- **入库即签名（R4 集成）**：`POST /api/import` 与 `POST /api/ingest/events` 在落库前由该记录
  `agent_id` 的私钥签名；更新已存在记录会先摘掉旧签名再按 `previous` 重签。产品入口默认开
  （`--no-secretariat` 关），`InterveneService(sign_on_write=False)` 为库默认（既有测试与
  嵌入方行为不变）；**仅 local 模式**，team 模式待 actor→DID 映射。高危记录用
  `allow_unapproved=True`：Agent 自签照签，审批缺口交给 `verify_record` 如实报出。
  新增 `tests/test_secretariat_autosign.py`（4 条，导入/更新/上报/关闭四路径）。
- **总裁办（R1–R4）**：身份确权 / 行为签名 / 责任归属 / 争议裁定四支柱补齐到"能被外部复核"。
  - 身份：DID 形状校验、登记幂等、吊销幂等且终局、`agent_id` 规则（docs/09 D13）；
    委托人与受托人必须是合法 DID，Agent 委托人必须有出处（已登记或已受托）。
  - 签名：签名域固定 11 项；`risk_level` 签名前归一化写回；`human_owner`/`delegation_chain`
    改为**校验而非 setdefault**（链根=人、链尾=本实例 DID、长度 ≤8）；责任链链尾改用
    `did_from_pubkey` 派生的 Agent DID（不再是裸 `agent_id`）。
  - 审批：`approval` 收敛到 `docs/02 §2.8` 的形状（多一个键即 schema 违约）；同一 DID 只算
    一个人（D8）；审批签 payload 绑定 `record_id` + 动作事实 + 风险 + 所需人数，跨记录不可复用；
    `verify_record` 真实验签审批签，critical 自动盖章并校验 seal。
  - 契约：`action_record.schema.json` 增补可选 `tool` 字段；已签名记录四档风险 **100% 通过
    schema**（此前 8 条错误，其中 6 条由签名器造成）。
  - 接口：T1–T10 + 族入口 `/api/secretariat`（`GET /api/secretariat/status`）；权限沿用既有
    8 项能力；`server.py` 新增 `--features DIR` 指定总裁办数据目录；`Secretariat` 懒装配。
  - 界面：`web/ext/secretariat.js` 五区面板（状态与策略 / 身份 / 委托 / 认定书 / 验签·盖章·
    行为签名），138→155 条中文文案 + 英文同构；离线时如实提示"需要服务端"。
  - 认定书：`actions[]` 内嵌离线复算所需最小字段集；新增
    `tools/verify_letter.py`（纯标准库，验认定书签名 + records_root + 逐条动作签名，
    BOM 容错，篡改退出码 1）。
  - 证据：`tests/test_secretariat_contract.py`（12）、`tests/test_secretariat_api.py`（7）、
    `docs/05-evidence/r4-secretariat-panel.png`、`docs/05-evidence/r4-responsibility-letter.json`；
    规格见 `docs/12-secretariat-delta-spec.md`。
  - 闸门现状（T0 本轮 2026-11-12 实测，替换收口报告里的旧数字）：全量单测 **954 条 / 1 条已知红**
    （`test_e2e.ArtifactTests.test_signature_status_is_honest`，签名状态口径，见下）；
    `tests.test_secretariat*` 四模块 **98/98 OK**；i18n **PASS 5/5**；契约检查唯一失败仍是
    **8 个非总裁办**前端端点（known red，见 docs/12 §11）；前端自检 **92/92**。
    红的那条要等 T3 真签名重打产物才转绿，本轮只改脚本口径，不改产物、不改状态取值。

### Changed（变更）

- **团队收口（2026-11-12，严格串行 5 任务）**：产品经理 / 后端 / 架构师跑 strata 本地模型，
  测试与质量官跑 DeepSeek-V4.1-Flash。R2 修 8 项身份/委托缺口，R3 修 5 项签名/认定书缺口，
  R4 修 3 项路由/文案缺口；状态码统一以运行时为准（SCOPE_WIDENED / DEPTH_EXCEEDED /
  IDENTITY_REVOKED = 409，IDENTITY_MISSING = 404），`EXPANSION_ERROR_TABLE` 同步；
  `selfcheck.mjs` 纳入 web/ext 扫描面并新增 secretariat 面板断言（92/92 PASS）。
  证据与决议见 `docs/16-closeout-report.md`、`docs/15-qa-r2r4-acceptance.md`、`docs/audit/`。
- `approval` 不再包含 `policy`/`local_only`/`fingerprint` 等未声明键（旧字段信息由
  `record.risk_level`、`approval.required`、`tsa_state` 表达，见 docs/12 §4）。

### 0.5.0 版本口径与打包链（T0，本轮）

- **版本口径统一到 `0.5.0`**（单一来源 `server.py:SERVER_VERSION`）：`SERVER_VERSION`
  `0.4.5 → 0.5.0`、`__init__.py:__version__` `0.1.0 → 0.5.0`（收掉 README 已知限制第 9 条）、
  `desktop/package.json` + `package-lock.json` `0.4.5 → 0.5.0`（electron-builder 的
  `artifactName` 用 `${version}`，产物名随之变 `TraceLens-0.5.0-win-x64-Setup.exe` /
  `-portable.exe`）、`docker-compose.yml` 两处默认与 `Dockerfile` 的
  `ARG TRACELENS_VERSION` `0.4.5 → 0.5.0`、`tools/build_sidecar.py` 的 `--version` 默认
  与 `--check` 打印行统一到 `0.5.0`，文档口径同步（README / `docs/03` / `docs/06-*`）。
  历史快照不动：`docs/05-test-report.md`、`docs/05-test-plan.md`、
  `release/tracelens-0.4.5-release/**`。
- **sidecar hiddenimports 不再手写**：`tools/build_sidecar.py` 改为**扫盘枚举**
  `agent_explainer` 包全部模块（19 个，含 5.0 的 `secretariat`、`crypto_sig`、`feature_store`、
  `groups`、`qr`、`rollback`、`sharing` 与 `sdk/` 子包）并逐条 `--hidden-import`；旧清单只有
  8 个模块，总裁办模块整包缺失，静态分析在动态导入处兜不住。清单不全时脚本**当场中止**，
  `--check` 打印模块数与版本号比对。
- **签名状态如实口径**（`tools/sign_and_hash.ps1`）：`signed_count`（只算 `Valid`/`NotTrusted`）
  与新增的 `signatures_applied`（文件里确实落了签名）分开写，并新增 `trust_state`
  （自签名典型 `self_signed_root_untrusted`）、`signed_by`、`status_counts`、`trusted_statuses`。
  全量单测那条长期红的真因就是旧口径把两件事混成一个数（`signed_count=2` vs 状态 `UnknownError`）。
  `status` 保持 `Get-AuthenticodeSignature` 原样取值，**不为绿断言改状态**。
- **编码口径**：`SIGNATURE-STATUS.json` / `SHA256SUMS.txt` 一律 **UTF-8 无 BOM**、中文原样落盘；
  写前剥 U+FEFF、发现 U+FFFD（脚本源码被按 ANSI 解码的证据）拒绝出文件、写完回读字节复核；
  新增只读审计开关 `-CheckEncoding`。脚本源文件自身必须 **UTF-8 带 BOM**（Windows PowerShell
  5.1 只认 BOM），启动时自检。
- **签名产物清单过滤**：`builder-debug.yml`、`tracelens-desktop-*-x64.nsis.7z` 等构建中间产物
  不再进签名与 `SHA256SUMS.txt`（跳过清单会打印出来）；`desktop/package.json` 的 `npm run sign`
  改调 `powershell`（本机只有 Windows PowerShell 5.1，`pwsh` 不存在 → 原来这条脚本命令必失败）。
  逐项 before→after 与闸门输出见 `docs/audit/v050-version.md`。

## [0.4.5] — 2026-11-11

### Added（新增）

- **行动清单主界面**：三区（待确认 / 进行中 / 已完成）+ 悬浮球 + 底部面板；桌面 ≥1024px 追加
  侧栏动作列表、状态筛选、可拖动时间轴、两次运行对比（12 步 × 8 字段）。
- **人话翻译引擎**：L1 一句话 / L2 分步 / L3 技术详情三级粒度；模板模式离线可用，
  可选 LLM 润色（`mode=llm`，密钥不落盘）。
- **干预闭环**：`POST /intervene` 记录同意 / 改参数 / 拒绝；**决定 ≠ 生效**——
  只写 `user_intervention`，执行回执 `execution_receipt` 原样展示。
- **团队模式与权限**：`--mode team` 多用户登录（admin / editor / viewer）、CSRF 双提交、
  会话 8 小时、连续 5 次失败锁 15 分钟；用户只禁用不删除（审计可追）。
- **审计链**：追加式 JSONL 哈希链（`audit.jsonl`），断链 500 且**不输出半条证据**；
  PII 写盘前强制打码（13 类模式，`<redacted:类型#sha256:前12位>`）。
- **审计导出**：`GET /api/audit/export` 支持 CSV / HTML / PDF（reportlab 缺失自动回退可打印
  HTML），带内容指纹响应头，导出本身也记账。
- **实时接入**：`POST /api/ingest/events`（webhook，≤64KB/批、≤200 条/批、幂等窗口 15 分钟、
  按令牌限流）、`GET /api/stream`（SSE，环形缓冲 200 帧、心跳 15s、断线按游标补发）、
  Python SDK（`agent_explainer.sdk`：`log_action` / `update_action` / `@track` / `stream_events`）。
- **数据导入与持久化**：粘贴 JSON 或上传文件（同一入口）、四种格式自动识别
  （Langfuse / OTel GenAI / MCP 事件 / 原生 ActionRecord）、`upsert/create/update` 三种模式；
  记录落 `records.jsonl`（追加式 + 跨进程文件锁），**重启不丢**。
- **桌面端**：Electron 壳 + PyInstaller sidecar（启动流程：单实例锁 → sidecar → bootstrap 行 →
  `/health` 门槛 → 加载界面）；NSIS 安装包与便携版 exe（自签名）。
- **Docker 团队部署**：多阶段镜像（`python:3.12-slim`、非 root uid 10001、HEALTHCHECK 打
  `/health`）、`docker-compose.yml`（数据卷 `./data` `./logs`、`restart: unless-stopped`、
  healthcheck）、一键构建/运行/验收脚本与离线镜像 tar。
- **移动端入口**：顶栏「更多」层，六个入口（设置 / 审计 / 用户 / 导入 / 导出 / 语言）在手机上全部可达，
  按权限与在线状态禁用并写明原因。
- **双语**：中英界面 721 键 / 22 域，`zh`/`en` 各 100%；演示数据也有英文孪生数据集。

### Changed（变更）

- 界面与文档统一健康检查口径：真实端点 `GET /health`（别名 `/healthz`），
  **没有** `/api/health`、没有 `ready` 字段。
- 审计面板首屏自动读一次；本页决定 / 导出 / 导入成功后自动重读（不再要求手动点刷新）。
- 语言切换不再替换记录（服务器是唯一数据源），视图（筛选 / 展开卡 / 时间轴位置）保持不变。

### Fixed（修复）

- **B-01**：切语言后导入记录消失、已批准记录回到"待确认"。
- **H-01**：审计面板首屏 0 条、链状态显示为内存链。
- **H-02**：决定 / 导出之后审计列表不更新。
- **H-03**：其它窗口或 API 做的决定不会更新本页对应卡片。
- **H-04**：1400px 宽度下卡片提示竖排并与"展开详情"重叠。

### Known Limitations（已知限制，随 0.4.5 交付）

- **SSE 跨窗口推送**：本机上 SSE 显示 `live` 时，别的客户端做出的决定没有帧推到本页
  （页面有"决定帧强制合并 + 降级轮询 4 秒兜底"两条路，但不是服务端推送的）。属服务端/环境侧。
- **导入记录的任务名**：部分运行名缺 `demo.label.*` 字典键时，界面显示"文案缺失：demo.label.…"。
- **导出页脚**：审计导出 HTML / PDF 的"合成演示数据"页脚**未实现**（演示数据页面上有常驻横幅，
  导出文件里没有）。QA 不得宣称已具备。
- **viewer 可导出审计**：`audit.export` 四个角色都有（规格如此，非缺陷）。
- **桌面 GUI 未在构建会话内启动验证**：安装包与便携版已产出并通过哈希/签名/静默安装检查，
  界面需在真实桌面会话里双击安装后由用户自测（构建沙箱的 Electron crashpad 受限）。
- **单文件 sidecar exe 未产出**：PyInstaller 单文件在本机引导阶段建不出解包目录，
  交付布局为 onedir。
- **单进程设计**：不支持多副本共享同一数据目录；TLS / 反向代理未随包提供（需自备 nginx 等）。

## [0.1.0] — 2026-11-09

- 初始骨架：数据契约（ActionRecord v1.1 + JSON Schema）、三区界面骨架、演示夹具（12 条合成记录 +
  三种外部格式样例）、测试与文档基线。
