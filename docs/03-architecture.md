# TraceLens（行动透镜）· 产品架构 v2

| 项 | 内容 |
|---|---|
| 文档编号 | TL-ARCH-003 |
| 对应子任务 | 1.2（团队任务 t2） |
| 版本 | v2（架构基线；取代 v1.0 草案。旧 `agent-action-list/` 目录是**前一个团队的原型**，本文件是唯一现行架构） |
| 负责人 | 架构师（architect） |
| 上游 | [TL-PRD-001](./01-product-and-users.md)（t1） · [TL-DM-002 数据模型](./02-data-model.md)（同任务产物） |
| 读者 | 全体成员（t3–t17 每人一节可开工）+ 项目发起人 |
| 一句话读法 | 第 1 节交付什么 → 第 2–4 节系统怎么跑 → 第 5–8 节谁能干什么、接口长什么样 → 第 9–17 节工程与合规基线 → **第 18 节是你这个任务的开工单**。 |

> **本文的约束力**：目录名、模块边界、API 路径与错误码、i18n key 规范、密钥存储方式、打包与签名策略、Docker 前置检查，是 t3–t17 的**共同契约**。任何一项被实现时改掉，等于让三方同时返工；变更必须在队长处显式记录。

---

## 0. 30 秒看懂（给不懂技术的读者）

我们要交付一个**能装、能跑、能给人看**的产品，三种形态都要真的能用：

1. **Windows 安装包**（双击安装）+ **便携版 zip**（解压即用，公司电脑装不了软件时用它）；
2. **Docker 镜像**（团队部署，同事用浏览器一起看）；
3. **源码**（可自己构建）。

技术上是一台"翻译机"：桌面版是一个桌面外壳（Electron）在后台拉起一个本机的小服务（Python sidecar，只监听 `127.0.0.1`），界面是网页；团队版是同一个服务跑在 Docker 里，浏览器访问。数据只存本机/自建卷。

三条不可违反的架构纪律（写进代码与文案）：

| 纪律 | 内容 |
|---|---|
| **只读接入** | 不采集、不改写、不迁移原始 trace（与 Langfuse 共存） |
| **决定 ≠ 执行** | 点按钮只保证"决定已记录"；是否生效取决于执行端是否接入 SDK 预检查 |
| **诚实交付** | 构建、安装、`docker build` 必须真实执行；**不许用静态检查冒充构建**；自签名会有 SmartScreen 警告，必须写进文档 |

---

## 1. 交付形态（4 种，缺一不算产品）

| 编号 | 形态 | 产物 | 构建工具 | 目标用户 | 验收判据（真实执行） |
|---|---|---|---|---|---|
| **D-1** | Windows 安装包 | `TraceLens-Setup-<ver>-x64.exe`（NSIS）+ `SHA256SUMS.txt` + `tracelens-code-signing.cer` | `electron-builder`（**NSIS target**，`npm run dist`） | A/B/C 个人桌面 | 干净 Win10/11 x64 双击安装 → 开始菜单启动 → 5 分钟内看到演示清单（K-01）；卸载干净 |
| **D-2** | 便携版 | `TraceLens-Portable-<ver>-x64.zip`（含 `TraceLens.exe`） | `electron-builder`（**portable target**） | 装不了软件的公司电脑、拷给同事演示 | 解压 → 双击 → 浏览器自动打开；数据落在解压目录；**不写注册表**（安装前后注册表快照比对） |
| **D-3** | Docker 镜像（团队模式） | `Dockerfile` + `docker-compose.yml` + 可选 `tracelens-<ver>.tar`（`docker save` 离线包） | 多阶段构建，`python:3.12-slim`，非 root，HEALTHCHECK | C 谨慎、团队管理员 | `docker build` 成功；`docker compose up` 后 `GET /health` 返回 200 且 `status:"ok"`（**现状没有 `/api/health`，也没有 `ready` 字段**，见 6.6）；重启容器数据不丢（M2）；**首步必须 `docker info` 实检**（第 17 节） |
| **D-4** | 源码 | git 仓库 + `LICENSE`(Apache-2.0) + `CHANGELOG.md` + `docs/` + `README.md` | — | 自建构建者、审计方 | 按 README 从零构建出 D-1/D-2/D-3 三产物；离线可构建（依赖已 vendor 或有离线说明） |

**共同要求**：三种形态内含**同一版本**的应用（版本号来自 `agent_explainer/__init__.py:__version__`，三处产物一致）；每种形态都自带**演示数据**（首启不空白）；每个产物都有 SHA256 校验；**没有"必须联网才能第一次打开"**的路径。

---

## 2. 进程模型

### 2.1 桌面模式（local）

```
用户双击 TraceLens.exe
  └─ Electron main（桌面外壳，唯一窗口进程）
       ├─ 单实例锁：app.requestSingleInstanceLock()；二次启动 → 聚焦已有窗口并退出
       ├─ 启动 sidecar：child_process.spawn(<resources>/sidecar/tracelens-sidecar.exe,
       │                    ["--mode","local","--port","<free>","--data-dir","<userData|exe_dir>"])
       │        PyInstaller 单文件可执行，只监听 127.0.0.1:<port>
       │        启动行输出首行握手：TRACELENS_READY {"port":..,"pid":..,"version":..}
       ├─ 就绪探测：轮询 GET /health（≤8s，200ms 间隔；现状无 `/api/health`，见 6.6）；失败 → 窗口显示可读原因 + "打开日志目录"
       ├─ BrowserWindow 加载 http://127.0.0.1:<port>/  （现状：静态根就是界面，见 6.3 Z1；同源，避免 CORS）
       ├─ IPC 面（main ↔ renderer）：窗口控制、打开外部链接（白名单 http/https）、显示日志目录、清空数据确认
       └─ 退出：main 发 SIGTERM 等价（win: 发送 /api/shutdown + 关闭事件）→ sidecar 优雅退出
                （flush 记录存储与审计当前分片（现状 JSON，无 SQLite WAL）、关闭 SSE）→ 超时 5s 才强杀，并写审计 data.shutdown_forced
```

| 要点 | 决策 |
|---|---|
| **端口** | 不固定 8xxx：向系统要空闲端口（sidecar `--port 0` 后由握手回报），避免与用户已有服务冲突 |
| **绑定** | 只绑 `127.0.0.1`（团队模式才允许 `0.0.0.0`，且必须显式 `--mode team`） |
| **token** | 桌面模式启动时生成随机 `X-TraceLens-Token`，写入 sidecar 握手行；渲染进程所有 API 请求带此头 → 本机其他进程/网页无法冒用（团队模式改用 cookie+CSRF） |
| **sidecar 崩溃** | main 监听 exit → 自动重启（最多 3 次/5 分钟），界面显示"服务已重启，数据未丢"；超过则显示诊断面板（不得白屏） |
| **单实例** | 桌面单实例锁；sidecar 自身也用数据目录下的 `tracelens.lock`（O_EXCL + pid）防两个桌面实例共用数据目录 |
| **数据目录** | 安装版 `%APPDATA%/TraceLens`；便携版 `<exe 所在目录>/data`（便携版**不写注册表、不写 APPDATA**） |
| **开机自启** | **不做**（Q1 裁决：避免"它在我看不见的地方跑"）；托盘仅显示状态图标 |
| **优雅退出** | `/api/shutdown` 幂等；SQLite `wal_checkpoint(TRUNCATE)`；审计分片 `fsync`；SSE 发 `server.shutdown` 后关闭 |

### 2.2 团队模式（team）

```
docker compose up
  └─ 容器内：python -m agent_explainer.server --mode team --host 0.0.0.0 --port 8000
       ├─ 无 Electron、无 sidecar、无 PyInstaller：纯 Web 服务
       ├─ 静态 UI 由同一进程托管（现状在根路径 `/`，见 6.3 Z1），API 在 /api/*
       ├─ 首个 admin：环境变量 `TRACELENS_ADMIN_PASSWORD` 提供（代码 `ENV_ADMIN_PASSWORDS` 同时兼容旧写法 TRACELNS_ADMIN_INITIAL_PASSWORD）；未提供则首启在容器日志
       │   打印一次性密码并强制首次登录改密（t17 必须文档说明其可见性）
       └─ 数据卷 /data（现状：记录 JSON + `var/audit.jsonl` + `var/settings.json` + 令牌保管箱；规划的 SQLite + audit/ + raw/ **未实现**，见 6.5 差异 19）；HTTPS 由用户反向代理负责（第 14 节）
```

**同一份代码，两种形态**：`server` 的装配层按 `--mode` 注入认证后端（local = 免登录默认用户 `local`；team = cookie 会话 + 角色）。业务模块（翻译、审计、干预、设置）不感知形态差异 → 桌面测过的逻辑团队模式同样有效。

### 2.3 线程与并发

| 关注点 | 决策 |
|---|---|
| HTTP 服务 | 标准库 `http.server.ThreadingHTTPServer`（每请求一线程，SQLite 连接**线程本地** `threading.local`） |
| 可选 FastAPI | 走同一装配层（`server/app_fastapi.py`），路由表共享定义（见 6 节 `api_contract.json`） |
| SQLite 写入 | 单一写者队列（进程内 `queue` + 一个写线程）避免 `SQLITE_BUSY`；读用 WAL 并发 |
| SSE | 每连接一线程 + 进程内事件总线（`pubsub`，容量 500，慢消费者断开） |
| LLM 调用 | 请求线程内同步 HTTP（标准库 `urllib`）+ 硬超时（连接 3s / 总 20s）；`test-llm` 不排队 |
| 演示数据加载 | 启动后台任务，首屏不等它（首屏 <1.5s 依赖） |

---

## 3. 仓库目录与模块职责

```
agent-explainer/
├─ agent_explainer/                 # Python 包（核心运行时：纯标准库）
│  ├─ __init__.py                   # __version__（唯一版本源）
│  ├─ config.py                     # 模式/端口/数据目录/环境变量解析（--mode/--port/--data-dir）
│  ├─ trace_adapter/                # 【t3】外部格式 → ActionRecord
│  │   base.py native.py langfuse_export.py otel_json.py mcp_events.py detect.py
│  ├─ explanations/                 # 【t4/t5】翻译层
│  │   facts.py（指纹，与 tools/facts_hash.py 同源） templates/zh.py templates/en.py
│  │   render.py（L1/L2/L3 生成 + 降级文案） llm.py（OpenAI 兼容客户端，默认关闭）
│  ├─ audit/                        # 【t9】JSONL 哈希链 + verify + 导出脱敏
│  │   chain.py redact.py export_csv.py verify.py
│  ├─ auth.py                       # 【t13】users/roles/会话/PBKDF2/CSRF/限速；local 短路（单文件）
│  ├─ ingest/                       # 【t14】webhook/SDK 接收、幂等、原始载荷落盘、SSE 广播
│  ├─ settings/                     # 【t15】设置读写 + DPAPI + LLM 连接测试 + purge
│  ├─ server/                       # 装配层 + 路由 + 静态 UI + SSE + 优雅退出
│  │   app_stdlib.py app_fastapi.py routers/ sse.py errors.py（统一错误体）
│  ├─ store/                        # SQLite：连接、迁移、写入队列、平铺列一致性、查询 Q1–Q10
│  │   db.py migrations/ queries.py metrics.py logging/redact.py
│  └─ （接口契约不在包内）           # 4.7 实况：契约生成到 docs/api_contract.json（见差异 16）
├─ web/                             # 【t6/t7】前端（无框架依赖，构建期最小打包）
│  ├─ index.html  app.js  zones.js timeline.js intervene.js settings.js
│  ├─ styles/                       # 颜色语义 + 三区 + 响应式（≤480px 悬浮球/底部面板）
│  ├─ i18n/  zh.json  en.json  keys.md  lint.py
│  └─ vendor/                       # 零运行时依赖（离线可用；无 CDN）
├─ samples/                         # 【t3】演示与夹具（全部 synthetic=true，文件头注明合成）
│  ├─ action_records.json  action_records_en.json
│  ├─ langfuse_trace.json  otel_trace.json  mcp_session.json  README.md
├─ tests/                           # 【t10/t11】单测 + 集成 + e2e + 问卷
│  ├─ unit/ integration/ e2e/ walkthrough/  conftest.py  run_all.py
├─ desktop/                         # 【t12】Electron + 打包
│  ├─ main.js preload.js package.json（npm scripts）electron-builder.yml
│  ├─ build/sidecar.spec（PyInstaller） build/icon.ico  build/installer.nsh
│  └─ sign.ps1                      # 自签名脚本（第 16 节）
├─ tools/                           # 工程脚手架（本任务已建 4 个）
│  ├─ jsonschema_mini.py facts_hash.py validate_schema.py make_invalid_examples.py
│  ├─ test_offline_engine.py gen_api_contract.py i18n_lint.py perf_probe.py sign_and_hash.ps1
├─ docker/                          # 【t17】Dockerfile  docker-compose.yml entrypoint.sh healthcheck.sh
├─ docs/                            # 01–05 + schemas/（本任务 3 份 + schema 与夹具 + 报告）
├─ LICENSE                          # Apache-2.0（第 15 节）
├─ CHANGELOG.md                     # Keep a Changelog + SemVer
├─ README.md                        # 三形态安装 + 已知警告（SmartScreen）+ 干预边界声明
└─ .gitignore                       # 第 15 节
```

**实现进度（4.x 落地现状）**：`auth` 当前是**单文件** `agent_explainer/auth.py`（队长裁决 t18 第 2 条：4.3 的代码量下不拆包，`auth/` 是后续拆分目标）；角色矩阵的**唯一来源**是 [TL-PRD-001 §6.4](./01-product-and-users.md)（§5 的能力表、代码里的矩阵、`tests/test_auth_multituser.py` 里的 `DOCS_MATRIX` 都对着它，改矩阵必须三处一起改）。同理现状 `audit` = `audit.py`（t9 追加式 JSONL 链，4.2b 已接进产品服务）、装配层 = `server.py`（t8 干预闭环 + t13 鉴权 + t18 审计落盘）；拆成 `audit/`、`server/` 属后续任务。

**模块边界纪律**（防"顺手越界"）：

| 模块 | 允许依赖 | 禁止 |
|---|---|---|
| `trace_adapter` | `store`、`explanations.facts` | 直接写 HTTP、直接改审计 |
| `explanations` | 无 IO 依赖（纯函数 + `settings` 只读） | 写库、发网络（LLM 例外，见 t5 契约） |
| `audit` | `store` 只读 + 文件追加 | 任何 UPDATE/DELETE |
| `auth` | `store` | 出现在业务路由内（只能在中间件） |
| `server` | 全部（装配层） | 业务规则（只做路由/鉴权/序列化） |
| `web` | `api_contract.json` + i18n 字典 | 硬编码中文/英文文案（i18n lint 拦截） |

---

## 4. 存储与审计（详表见 [TL-DM-002 第 6 节](./02-data-model.md)）

| 主题 | 基线 |
|---|---|
| 数据库 | SQLite（WAL）；表：`users` `roles` `sessions` `actions` `action_labels` `ingest_events` `settings` `meta` |
| 版本 | `PRAGMA user_version` + `meta.schema_version`；迁移脚本 `agent_explainer/store/migrations/NNNN_*.sql`，启动时顺序应用，单事务、失败拒绝启动 |
| 写入路径 | 单一写线程 + 队列；`record_json` 为唯一事实源，平铺列为缓存（`store.verify_columns` 一致性自检，纳入 t10 回归） |
| 审计 | `<data>/audit/audit-YYYYMMDD.jsonl`，哈希链（`prev_hash`/`hash`，`sha256` 规范 JSON），append-only，`python -m agent_explainer.audit verify` 校验；篡改 1 条必须报错（K-09） |
| 审计（4.2b 落地现状） | 产品服务写**单文件追加式 JSONL 链** `<data>/audit.jsonl`（默认 `var/audit.jsonl`；`--audit-log PATH` 改路径，`--no-audit-log` 只在内存哈希链预览、重启即丢）。条目带 `seq/prev_hash/hash`，`entry_hash == hash` 是 t8 的恒等别名。启动即加载 + `verify()`（含 seal 封印锚点），**只读**：断链时 `/health` 的 `audit.ok=false` 且 stderr 一句人话，`GET /api/audit` 返回 **500 `AUDIT_CHAIN_BROKEN`**（`detail.reason` 中文可读：第几条、为什么、共几处、链头、文件路径），`POST /intervene` 同样 500 且**不落决定**——服务端永不自动清空/截断/重建，也没有任何更新或删除接口（第 6.3 节 append-only）。决定顺序：**先进审计链，再进记录**；鉴权事件（登录/越权）是辅助遥测，链断时降级到内存链 + stderr，不让审计故障把登录页打死。**4.8 的 PII 打码（写盘前，不是导出时）**：`audit.py` 的 `redact_text()` + `PII_PATTERNS`（**13 类模式**：邮箱 / 中国手机号 / 18 位身份证 / 11–20 位长数字 / IPv4 / IPv6 / Windows 绝对路径 / POSIX 绝对路径（`/Users`、`/home`、`/var`…）/ `Bearer <token>` / `key=value` 型密钥（`api_key`、`secret`、`access_token`、`token`、`password`、`authorization`、`cookie`…）/ `sk-`·`sk_` 前缀密钥 / AWS `AKIA…` / PEM 私钥块）把正文里的敏感值替换为 **`<redacted:类型#sha256:前12位>`**——**同值同指纹**（能对齐取证，正文里没有原值），**幂等**（已打码的值不会被二次改写）；作用面是 `reason`/`note`/`changed_fields`/参数与敏感键名。**落盘文件、`GET /api/audit`、CSV/HTML/PDF 导出三处看到的是同一份脱敏后内容**（导出层不复制原文，M6 只加指纹） |
| 审计条目的 `mode` | 只有 `mode ∈ {local, live}`（4.2 数据模型枚举，不新增第三种值）。**team 模式一律记 `live`**：team 的语义就是"多用户实时共用一份服务"（含 Docker/局域网部署），与"免登录单机演示"的 `local` 是两类现场；界面与导出按这两值显示，队长裁决 t18 第 4 条 |
| 审计事件白名单 | `decision` `correction` `seal` `export` `access_denied` `note` `record_reset` + 4.3 的 `auth.login` `auth.login_failed` `auth.logout` `auth.denied`（队长裁决 t18 第 3 条入白名单，`query(event="auth.denied")` 直接可查）。历史 JSONL 文件**不改写**：服务端仍按 `access_denied`/`note` + `fields.auth_event` 的映射写新条目，两条路都验得出 |
| 原始载荷 | `<data>/raw/<action_id>.json`（指针 `raw_ref=local://raw/...`），导出与日志只给指针 |
| 记录持久化（**4.9 / t22 落地现状**） | **追加式 JSONL 日志 + 内存索引**：模块 `agent_explainer/store.py`（`RecordJournal`），默认 `<data>/records.jsonl`（`--records-store PATH` 改路径，`--no-records-store` 回到纯内存 = 重启演示 12 条）。**为什么是 JSONL 不是 SQLite**：本产品的记录量级是"每人每天几十~几千条"，访问形态只有"整条读 / 整条写 / 启动全量装载"，没有需要索引与联表的查询；JSONL 与审计链同构（追加、崩一行只坏一行、可读可取证、便携版整目录拷走即迁移、零依赖），SQLite 的六表 + 迁移脚本（本节旧写法）在本产品里没有消费者，见 6.5 差异 19。**条目**：`op ∈ {header, put, clear}`，`put` 带整条记录；装载 = 逐行重放、同 id **最后写入胜出**。**抗崩**：崩溃/强杀留下的半行尾 → 跳过并报告 `RECORD_STORE_TAIL_TRUNCATED`，下一次追加前封口；行内坏 JSON → `RECORD_STORE_LINE_INVALID` 只跳过这一行，其余照常装载；未知 `op` → `RECORD_STORE_OP_UNKNOWN`；单行 >512 KB → `RECORD_STORE_LINE_TOO_LARGE`（超限的一行都不写）。**并发**：进程内 `RLock` + 跨进程 sidecar 文件锁（Windows `msvcrt` / POSIX `fcntl`；`--records-store-lock` 调超时，默认 5 秒 → 500 `RECORD_STORE_LOCKED`）；压实（`replace_all`）= 临时文件写完 + `fsync` + `os.replace` 原子换名，绝不"先清空再逐条写"。**跨进程立刻可见**：每次读/写前比对日志签名（`mtime_ns,size`），变了就重新装载 → 团队模式多进程共用一份日志时互相看得见。**写了就要落盘**：持久模式下写不进去就 500 `RECORD_STORE_WRITE_FAILED`（内存已改、审计已记，但绝不"导入成功/决定成功、重启就没了"）；实况见 `/health` 的 `records_store` 与 `/api/records` 的 `meta.records_store`（`durable/mode/path/lines/records/problems/write_errors`）。自检：`python -m agent_explainer.store` |
| 密钥 | 桌面：DPAPI（第 8 节）；团队：环境变量，**禁止入库**；实况字段是 `llm.api_key_source ∈ {dpapi,env,memory,none}`（旧名 `settings.secret_kind` 未使用）；`memory` 只存在于非 Windows 开发机（重启即无，不落盘） |
| 清空 | 现状 = **`POST /api/reset`**（需 `settings.write` + CSRF；追加 `record_reset` 审计条目；`--no-reset` 启动 → 403 `RESET_DISABLED`；`settings.data.reset_enabled` 决定界面是否给入口）。**4.9 起**：清空 = 内存回到**演示种子 12 条**（不是清成 0）+ 记录日志压实（先 `append_clear("reset")` 再原子重写种子）；审计文件一字节不删、只追加。启动参数 `--reset-demo` = 带着已有日志启动时直接清回演示种子并重写日志（演示/试用现场的逃生阀）。规划中的 `POST /api/settings/purge`（归档 `.purged-<ts>.tar.gz` 再删）**尚未实现**；**审计侧永不删**（append-only，第 6.3 节） |
| 备份 | 团队模式文档给出 `sqlite3 .backup` + 卷快照两个做法；不自动外发备份 |

---

## 5. 双模式与权限矩阵（服务端强制，前端隐藏只是体验）

| 能力（t1 6.4 逐行） | local（免登录默认用户） | team-admin | team-editor | team-viewer |
|---|---|---|---|---|
| 看清单/时间轴/切语言 | ✅ | ✅ | ✅ | ✅ |
| 粘贴/上传/演示数据 | ✅ | ✅ | ✅ | ❌ `403 ROLE_FORBIDDEN`（4.9 实况：入口 = D3 `POST /api/import`，能力 `data.write` + CSRF） |
| webhook 令牌管理 | ✅ | ✅ | ❌ | ❌ |
| 干预（同意/改/拒绝） | ✅ | ✅ | ✅ | ❌（入口隐藏 + 403） |
| 看审计/导出 CSV | ✅ | ✅ | ✅ | ✅ |
| 模型配置/清空数据 | ✅ | ✅ | ❌ | ❌ |
| 用户增删/改角色/重置密码 | —（无此概念） | ✅ | ❌ | ❌ |

实现要点：

1. `auth` 中间件解析会话 → 注入 `request.actor_id / role / workspace_id`；路由声明所需角色（`api_contract.json` 的 `roles` 字段），由装饰器统一校验，**禁止**在 handler 里写散落的 `if role == "admin"`。
2. 未登录 → `401 AUTH_REQUIRED`；越权 → `403 ROLE_FORBIDDEN`（K-11 的 12 项判定用例）。
3. 每次鉴权结果写审计（`auth.login` / `auth.login_failed` / `auth.logout`），越权尝试写 `auth.denied`。
4. `policy.min_role_to_decide` 在记录级二次收口（例：某条记录要求 `editor` 以上）。
5. local 模式：`auth` 短路返回 `actor_id="local", role="local", workspace_id="default"`，**不出现登录页**（T1 护栏：禁止要求注册）。

---

## 6. API 契约与统一错误码

### 6.1 通用约定

| 项 | 约定 |
|---|---|
| 前缀 | 所有 JSON 接口在 `/api/*`；静态 UI 在 `/ui/*`；`/intervene` 是**唯一无前缀**的动作端点（历史命名，前端与 SDK 已引用，t8/t14 保持） |
| 内容 | 请求/响应均 `application/json; charset=utf-8`；CSV 端点返回 `text/csv` |
| 请求头 | 桌面：`X-TraceLens-Token`；团队：`Cookie: tl_session=…` + `X-CSRF-Token`；写操作可选 `Idempotency-Key` |
| 响应包裹 | `{data, next_cursor?, count, server_time, request_id, degraded?}` |
| 时间 | 一律 RFC3339 带时区偏移 |
| 幂等 | `POST /api/ingest/events` 用 `dedupe_key`/记录 `id` 去重；`POST /intervene` 用 `Idempotency-Key`（重复请求返回首次结果 + `degraded:"idempotent_replay"`） |
| 分页 | `limit`（默认 50，上限 500）+ `cursor` |
| 版本 | `/health`（别名 `/healthz`；现状无 `/api/health`）回报 `version` 与 `schema_version`（现 `0.5.0` / `"1.1"`，字段见 6.6），前端在两者不一致时提示刷新 |
| 契约文件 | **实况（4.7）：`docs/api_contract.json`**，由 `python tools/gen_api_contract.py --write` 从代码生成（路径/方法/权限能力/CSRF/SSE/错误码/前端调用面/角色矩阵），`--check` 检查内容漂移并逐条比对 6.5 表；旧落点 `agent_explainer/api_contract.json` 作废（差异 16）。**前端与 SDK 以它为准**，不靠口口相传 |

### 6.2 统一错误体（所有非 2xx）

```json
{
  "code": "LLM_UNREACHABLE",
  "message": "模型服务连不上，已自动用模板模式继续显示（不影响其它功能）",
  "detail": { "base_url_host": "api.example.com", "timeout_ms": 3000, "attempt": 2 },
  "request_id": "req-7f3a9c2b1d"
}
```

| 字段 | 规则 |
|---|---|
| `code` | 全大写下划线，取自第 6.3 表；**前端只按 code 查 i18n 文案**，不解析 message |
| `message` | **给人看的一句人话**：说清"发生了什么 + 你现在可以做什么"，禁止只丢技术术语（T3/T6 护栏）；服务端只出中文，前端按 `code` 出双语 |
| `detail` | 结构化诊断；**禁止**含 API key、口令、cookie、trace 原文、参数明文、完整 UA |
| `request_id` | 每个请求生成（uuid4 短串），出现在日志与审计中，供支持沟通定位 |

### 6.3 端点清单（与 `server.py` 的路由分发逐条对照）

**读表纪律**：`现状` 列是 **4.6（t19）从代码提取的实况**（来源 = `server.py` 的 `API_PATH_*` 常量 + `do_GET/do_POST/do_PUT/do_PATCH/do_DELETE` 分发 + 各域错误码表；提取与对照方法见 6.5）。
`✅` = 代码已实现（可直接验收）；`⚠` = 规划项，**当前 HTTP 层不存在该路径**（请求会落到 404 `ROUTE_NOT_FOUND` 或 404 `STATIC_NOT_FOUND`）。QA（5.1）只按 `✅` 行写用例；`⚠` 行是后续任务待办，不得当成已交付。

| # | 端点 | 方法 | 角色/凭证 | 入参要点 | 成功响应 | 专属错误码（通用码见 6.4） | 现状（代码实测） |
|---|---|---|---|---|---|---|---|
| A1 | `/api/auth/login` | POST | 公开（team 模式） | `username,password` | `{user,role,csrf_token,expires_at}` + 头 `Set-Cookie: tl_session`（HttpOnly/SameSite=Lax）、`X-Auth-Role`、`X-Auth-Actor` | `AUTH_BAD_CREDENTIALS` `RATE_LIMITED`（5 次失败锁 15 分钟，带 `Retry-After`）`AUTH_USER_DISABLED` `AUTH_USER_NOT_FOUND` | ✅ 已实现（t13） |
| A2 | `/api/auth/logout` | POST | 登录 | — | `{ok:true,mode,request_id,actor_id}` | `AUTH_REQUIRED` `AUTH_TOKEN_INVALID` | ✅ 已实现 |
| A3 | `/api/auth/me` | GET | 登录 | — | `{actor_id,role,workspace_id,mode,language,...}` | `AUTH_REQUIRED` `AUTH_TOKEN_EXPIRED` `AUTH_TOKEN_REVOKED` | ✅ 已实现 |
| A3b | `/api/auth/status`、`/api/auth/permissions` | GET | **公开**（两个探针） | — | 模式/是否需要登录/角色矩阵（前端只读不判） | — | ✅ 已实现（`API_PATHS_PUBLIC` 白名单） |
| A4 | `/api/auth/password` | POST | 登录 + CSRF | `old_password,new_password` | `{ok:true,...}` | `AUTH_WEAK_PASSWORD`（≥10 位）`AUTH_PASSWORD_SAME` `AUTH_PASSWORD_TOO_LONG` `AUTH_BAD_CREDENTIALS` | ✅ 已实现 |
| A5 | `/api/auth/check` | POST | 登录 | `{permission}` | 该会话是否有某能力（前端只用于隐藏入口） | `PERMISSION_UNKNOWN` | ✅ 已实现 |
| B1 | `/api/users` | GET/POST | 需 `users.manage`（team-admin）+ CSRF | `role,username,display_name,password?` / GET: `q,status` | 201 `{id,username,role,status,...}`；GET `{users:[…],...}` | `USER_EXISTS` `USERNAME_REQUIRED` `USERNAME_INVALID` `ROLE_INVALID` `ROLE_FORBIDDEN` | ✅ 已实现（用户**只禁用不删除**） |
| B2 | `/api/users/{id}/role` `/status` `/password` | POST | 需 `users.manage` + CSRF | `{role}` / `{status}` / `{old_password,new_password}` | `{ok:true,user}` | `USER_LAST_ADMIN` `USER_SELF_DISABLE` `USER_DELETE_DISABLED` `USER_STATUS_INVALID` `ROUTE_NOT_FOUND`（未知子动作） | ✅ 已实现 |
| C1 | `/api/actions` | GET | （规划） | 见 DM 第 7 节 Q1/Q2/Q5 | — | — | ⚠ 未实现；**实际数据源是 R1 `/api/records`**（三区/筛选/时间轴全用它，平铺在前端 t7 计算） |
| C2 | `/api/actions/{id}` | GET | （规划） | — | — | — | ⚠ 未实现；实际是 R2 `/api/records/{id}` |
| D1 | `/api/traces` | POST | （规划） | `{format:"auto\|native\|langfuse\|otel\|mcp", text?}` 或 multipart `file` | — | — | ⚠ 未实现（HTTP 层）。**4.9 起用户可见的导入入口 = D3 `POST /api/import`**（粘贴文本与上传文件内容同一条路，无 multipart）；适配器仍是 Python API `trace_adapter.convert_with_raw(doc, format=…, source_kind=…, source_uri=…, language=…, strict=…)`，机器实时上报走 **E1** |
| D2 | `/api/traces/{session_id}/timeline` | GET | （规划） | `bucket` | — | — | ⚠ 未实现；时间轴 = R1 全量 + 前端（t7），游标在 `meta.stream_head_seq` |
| D3 | `/api/import` | POST | 需 `data.write`（local / team-admin / team-editor）+ CSRF；`viewer` 403 | `{payload（必填：粘贴的 JSON 文本 **或** 已解析的文档对象；也接受 `data/text/content/document/doc/file/events/records` 任一包装字段）、`filename?`（有值即按"上传文件"标注来源）、`format?`（留空＝自动识别；可显式 `langfuse\|otel_genai\|mcp_events\|native`）、`mode?`（`create\|update\|upsert`，默认 `upsert`＝撞 id 就按白名单合并）、`strict?`（默认 `true`＝整批拒绝；`false`＝坏的跳过、好的入库）、`lang?`（`zh\|en`，默认 zh）、`source_kind?`}；上限 **≤64 KB/批**（`IMPORT_MAX_BYTES`）、**≤200 条事件/批**（`IMPORT_MAX_RECORDS`，与 E1 同口径） | 200 `{ok,imported,created,updated,duplicates,skipped,ids[:40],duplicate_detail[:20],skip_detail[:20],format,requested_format,mode,strict,source_kind("paste_json"\|"upload"),filename,bytes,stats{…含 skip_reasons[:20]、limit_bytes、limit_records},records_total,durable,stream{head_seq,frames,subscribers},actor,request_id,note}`；响应头 `X-Import-Format/Imported/Created/Updated/Duplicates/Skipped/Mode/Source-Kind/Durable`、`X-Stream-Head-Seq`、`X-Request-Id` | `IMPORT_INPUT_EMPTY`(400) `IMPORT_INPUT_NOT_JSON`(400，`detail.line/first_chars`) `IMPORT_MODE_INVALID`(400) `IMPORT_TOO_MANY`(400，`detail.count/limit`) `IMPORT_TOO_LARGE`(413，`detail.bytes/limit`) `INTERVENE_BODY_TOO_LARGE`(413，请求体本身超 64 KB) `IMPORT_FORMAT_UNSUPPORTED`(400) / 适配器码原样返回（`TRACE_*`，带 `detail.adapter_code/index/format_detected/supported`） `RECORD_STORE_WRITE_FAILED`(500) `AUTH_REQUIRED`(401) `ROLE_FORBIDDEN`(403) `CSRF_FAILED`(403) `METHOD_NOT_ALLOWED`(405，`GET /api/import`) | ✅ 已实现（**4.9 / t22**）：四种格式自动识别（Langfuse 导出 / OTel GenAI·OTLP 跨度 / MCP 事件流 / 原生 ActionRecord），认出来就**照这种格式解析、绝不静默换一种硬解析**；合并复用 E1 的 `merge_facts`，`user_intervention`/`execution_receipt` 是受保护字段 → **导入覆盖不掉用户的决定**；审计只记格式/条数/来源/文件名/统计，**粘贴正文一个字都不进审计**（`fields.import_body="not_recorded"`）；落盘见第 4 节"记录持久化" |
| E1 | `/api/ingest/events` | POST | **机器令牌**（team 必需；local 匿名放行并如实标 `anonymous`） | 信封三写法：`{"events":[…]}` / `{"records":[…]}`（可带控制字段）/ 裸数组 / 单条原生事件（带 `schema_version`+`action_type`）。控制字段：`mode: create\|update\|upsert`（默认 `create`）、`strict`（默认 `false`＝坏事件跳过、好事件照常入库）、`format`（`webhook\|sdk\|otel\|langfuse\|mcp`）、`language`、`idempotency_key`；头 `Idempotency-Key` 优先于请求体；凭证走 `X-TraceLens-Ingest-Token`，其次 `Authorization: Bearer <token>` | 200 `{ok,accepted,created,updated,duplicates,skipped,ids,duplicate_detail[:20],format,mode,source_kind,token:{token_id,label,scopes,anonymous},stats:{input_events,converted,skipped,skip_reasons[:20],…},raw_payloads,stream:{head_seq,frames,subscribers},idempotency,replay,request_id,ingest_version,received_at,note}`；响应头 `X-Ingest-Replay/Accepted/Created/Updated/Duplicates/Skipped/Format/Token-Id`、`X-Stream-Head-Seq` | `INGEST_DISABLED`(403) `INGEST_TOKEN_MISSING`(401) `INGEST_TOKEN_INVALID`(401) `INGEST_TOKEN_DISABLED`(403) `INGEST_TOKEN_EXPIRED`(403) `INGEST_SCOPE_FORBIDDEN`(403) `INGEST_RATE_LIMITED`(429+`Retry-After`) `INGEST_SCHEMA_INVALID` `INGEST_EMPTY` `INGEST_TOO_MANY_EVENTS`(413) `INGEST_TOO_LARGE`(413) `INGEST_FIELD_TYPE` `INGEST_IDEMPOTENCY_CONFLICT`(409) `INGEST_FORMAT_UNSUPPORTED`(422) `INGEST_ADAPTER_REJECTED`(422) `INGEST_STORE_FAILURE`(500) | ✅ 已实现（t14）：单条/批量（≤200 条、≤64 KB）、幂等窗口 15 分钟（同 key 同体＝重放，不重复写审计；同 key 异体＝409）、每令牌令牌桶（突发 40 / 稳态 600 条每分钟） |
| E2 | `/api/ingest/events/replay` | POST | （规划） | `{from_event_id}` | — | — | ⚠ 未实现。重放口径 = **同一 `Idempotency-Key` 重投**（返回首次结果 + `replay:true` + 头 `X-Ingest-Replay: true`） |
| E3 | `/api/ingest/tokens` | POST | 需 `ingest.token.manage`（local/admin）+ CSRF | `{label(必填,≤60),scopes?(默认 \`ingest:write\`；可选 \`tokens:manage\`),ttl_seconds?(1 秒–365 天，null=不过期)}` | 201 `{ok,token(**明文只在这里出现一次**),record:{token_id,label,scopes,prefix,created_at,created_by,expires_at,disabled_at,last_used_at,uses},headers,note}` | `INGEST_FIELD_TYPE`（label/scopes/ttl 不合法）`ROLE_FORBIDDEN` `CSRF_FAILED` | ✅ 已实现（t14）：落盘只存 `sha256(salt+明文)` + 前缀，文件 `chmod 0600`；**任何接口都不返回凭证材料**（`public_record` 去掉 `salt`/`hash`） |
| E4 | `/api/ingest/tokens/{id}/revoke` | POST | 需 `ingest.token.manage` + CSRF | 路径里的 `token_id`；请求体可空 | `{ok,record,note}` | `INGEST_TOKEN_INVALID`（id 不存在）`METHOD_NOT_ALLOWED`（非 `revoke` 子动作） | ✅ 已实现：吊销后该令牌立刻 **403 `INGEST_TOKEN_DISABLED`**，在用的上报收到明确原因 |
| E5 | `/api/ingest/status` | GET | 需 `ingest.token.manage` | — | `{enabled,version,schema,mode,counters,last_batch,stream,tokens,tokens_detail,rate,limits,merge_fields,protected_fields,event_types,health}` | `ROLE_FORBIDDEN` | ✅ 已实现（**列令牌清单只能走这里**：`GET /api/ingest/events`、`GET /api/ingest/tokens` 一律 405，`detail.hint` 直接指向本行） |
| F1 | `/api/stream` | GET(SSE) | 需 `records.read`（与 R1 同一条权限；team 未登录 401） | 必须 `Accept: text/event-stream`；`?since=<seq>` 与头 `Last-Event-ID` 取较大者作游标；`?max_events=`（默认 0＝不限）、`?types=`（逗号分隔，越界 400）、`?heartbeat=`（秒，钳到 0.02–120，默认 15） | 200 `text/event-stream`：帧 = `event:` + `id: <seq>` + `data: <规范 JSON>` + 空行；事件类型 8 种 `hello`、`heartbeat`、`action.created`、`action.updated`、`action.decision_recorded`、`records.reset`、`ingest.batch`、`ingest.stats`；响应头 `X-TraceLens-Stream-Head`、`X-Accel-Buffering: no`、`Connection: close` | `STREAM_ACCEPT_REQUIRED`(406) `STREAM_QUERY_INVALID`(400) `STREAM_LIMIT_REACHED`(503) `AUTH_REQUIRED` `ROLE_FORBIDDEN` | ✅ 已实现（t14）：`seq` 单调、**环形缓冲 200 帧**补发、心跳帧无 `id:`（不污染游标）、慢客户端丢最旧帧并标 `degraded`、订阅上限 16 |
| G1 | `/intervene` | POST | 需 `intervene.decide` + CSRF | `{action_id,decision:"approve\|modify\|reject",option_id?,modified_params?,reason?,idempotency_key}` | 更新后的 ActionRecord + 头 `X-Intervene-Replay/-Status/-Mode/-Audit-Hash/-Actor`；决定只写 `user_intervention`，`execution_receipt` 原样不动 | `INTERVENE_ACTION_NOT_FOUND` `INTERVENE_STATE_INVALID` `INTERVENE_ALREADY_DECIDED` `INTERVENE_DECISION_INVALID` `INTERVENE_PARAM_*` `INTERVENE_REASON_REQUIRED` `ROLE_FORBIDDEN` `AUDIT_CHAIN_BROKEN` | ✅ 已实现（4.1/4.2b/4.3）；旧名 `NOT_PENDING`/`PARAMS_INVALID`/`REASON_REQUIRED` 见 6.4 差异 |
| H1 | `/api/audit` | GET | 需 `audit.read` | `limit,offset,reverse` + 过滤 `event,actor_id,action_id,decision,task_id,q`（4.2b） | `{entries:[审计条目],chain:{ok,count,head,problems},meta:{mode,durable,source,path,count,head,sealed,limit,offset,total},jsonl:{path,count,head,ok}}` | `AUDIT_QUERY_INVALID` `AUDIT_CHAIN_BROKEN`（链断 500，禁止静默返回半条链） | ✅ 已实现（4.2b；`POST/PUT/PATCH/DELETE /api/audit` → 405） |
| M6 | `/api/audit/export` | **GET**（`do_HEAD` 转发到 `do_GET`） | 需 `audit.export`（`local`/`admin`/`editor`/`viewer` **四个角色都有**这项能力；没有它的角色 → 403 `ROLE_FORBIDDEN`）+ 登录（team 模式 401 `AUTH_REQUIRED`）；**GET 无副作用 → 不做 CSRF**（代码注释原话） | `format?`（`csv\|html\|pdf`，默认 **`csv`**，`strip().lower()` 大小写不敏感）、`limit?`（默认 **500**，钳到 1–**5000**）、`lang?`（`zh\|en`，默认 zh，只影响导出标题/HTML 文案）、过滤条件与 H1 **同一批键**：`actor_id,action_id,decision,event,task_id,q,since,until`（各截断 200 字、空值丢弃） | 200 = **直接回字节流**（不是 JSON）：`Content-Type` = `text/csv;charset=utf-8` / `text/html;charset=utf-8` / `application/pdf`；`Content-Disposition: attachment; filename="tracelens-audit-<UTC戳>.csv"`（csv）或 `inline`（html/pdf）；响应头 `X-TraceLens-Export-Format`（**实发**格式，回退时是 `html`）、`-Rows`、`-Sha256`（内容指纹，PDF 按 latin-1 逐字节算）、`-Filters`（过滤摘要）、`X-TraceLens-Request-Id`、`Cache-Control: no-store`；`pdf` 退回时加 `X-TraceLens-Export-Fallback`（原因），字体缺中文时加 `X-TraceLens-Export-Note`。csv = 规范 CSV（BOM + RFC4180 引号，Excel 认中文）；html = **零外链零脚本**可打印页（浏览器 Ctrl+P 存 PDF）；pdf = reportlab **内存排版**（`server.py` 零落盘） | `EXPORT_FORMAT_INVALID`(400，`detail.field="format"` + `detail.allowed=[csv,html,pdf]` + hint) `AUDIT_QUERY_INVALID`(400，过滤枚举/时间不合法，`detail.audit_code` 带 `AUDIT_TIME_RANGE_INVERTED`/`AUDIT_TIME_INVALID`/`AUDIT_FIELD_ENUM` 等细分) `AUDIT_CHAIN_BROKEN`(500，**断链不出证据**) `ROLE_FORBIDDEN`(403) `AUTH_REQUIRED`(401)；`POST /api/audit/export` → 405 `METHOD_NOT_ALLOWED` | ✅ 已实现（**4.8 / t21**）：**先记账再交文件**——每次成功导出追加审计（内存链 `audit.export` + 落盘 `event="export"`），只写格式/请求格式/条数/字节/过滤摘要/内容 `sha256` 指纹，**不含理由原文、参数原文与密钥**；写不进去就 500，绝不"导出了没记账"。reportlab 缺失 → 退回可打印 HTML 并说明原因，**绝不因缺第三方库 500**；导出内容早在写盘前已脱敏（第 4 节 PII 打码），导出层不复制原文；`/health` 的 `audit_export` 块现场披露本行能力（见 6.6） |
| H2 | `/api/audit/verify` | GET | （规划） | `from,to` | — | — | ⚠ 未实现（GET 该路径 → `STATIC_NOT_FOUND`）：链校验随 H1 的 `chain` 块与 H0 的 `audit` 块返回，命令行走 `python -m agent_explainer.audit`；**导出侧的链校验在 M6 内部强制**（断链 500） |
| I1 | `/api/export.csv` | GET | （规划） | `scope/redact/from/to/lang` | — | — | ⚠ **该路径本身仍未实现**（GET → `STATIC_NOT_FOUND`）。**4.8 起审计导出的真实入口 = M6 `GET /api/audit/export?format=csv`**（含 `html`/`pdf`）；**记录导出 = GET R1（JSON）**；Python API 仍在（`audit.export_csv()/export_html()/export_pdf()`，供 CLI 与测试直用）。旧口径"审计导出只有 Python API"**作废**（见 6.5 差异 11） |
| J1 | `/api/settings` | **GET / PUT** | GET＝需 `records.read`（人人可读掩码视图）；PUT＝需 `settings.write` + CSRF（team 模式＝admin，`editor`/`viewer` 403） | PUT 体：`{"settings":{"llm":{…},"ui":{…},"data":{…}}}`（也接受顶层 `llm/ui/data`）。可写字段：`llm.mode\|provider\|base_url\|model\|apiKey\|timeout_s\|temperature\|max_tokens\|allow_outbound`、`ui.language\|granularity`、`data.reset_enabled\|export_enabled`。密钥哨兵：不出现/`null`/`"keep"`＝不动；`""`＝清除 | `{schema_version,mode,path,revision,updated_at,updated_by,llm{…含 api_key_present/mask/source/fingerprint/set_at},ui{language,granularity,storage_key},data{reset_enabled,export_enabled,actions},security{key_storage,outbound,env_names,mask,effect_notice},permissions{role,permission,can_read,can_write,note},engine,team,llm_test,data_management{reset,export,audit},paths,limits,ui_language_storage_key}`；PUT 另回 `{changed:[字段],engine,key_action:set\|same\|cleared\|unchanged,effect_notice}` | `SETTINGS_BODY_NOT_OBJECT` `SETTINGS_BODY_EMPTY` `SETTINGS_NO_CHANGES` `SETTINGS_SECTION_UNKNOWN` `SETTINGS_FIELD_UNKNOWN` `SETTINGS_FIELD_TYPE` `SETTINGS_FIELD_MISSING` `SETTINGS_FIELD_TOO_LONG` `SETTINGS_FIELD_RANGE` `SETTINGS_MODE_INVALID` `SETTINGS_PROVIDER_INVALID` `SETTINGS_LANGUAGE_INVALID` `SETTINGS_GRANULARITY_INVALID` `SETTINGS_URL_*`（7 个）`SETTINGS_WRITE_FAILED` `ROLE_FORBIDDEN` `CSRF_FAILED` | ✅ 已实现（t15）。**动词口径**：`PATCH`/`DELETE`/`POST /api/settings` → 405 `METHOD_NOT_ALLOWED`；写入只有 `PUT` 一种形状 |
| J2 | `/api/settings/test-llm` | POST | 需 `settings.write` + CSRF（探测会把提示词真发给模型端，所以与"改配置"同权限） | **只允许三个探测参数** `{prompt?(≤2000 字),max_tokens?(40–4000),timeout_s?(0.5–120)}`；地址/模型/密钥一律来自设置，**请求体不接受密钥** | `{ok,code,message,latency_ms,model,chars,sample,endpoint,config,detail,note,sent_facts_note,effect_notice:"settings.test_llm.probe_only"}`（失败也是 200 + `ok:false` + 可读 `message`） | `SETTINGS_FIELD_UNKNOWN`（多带字段）`SETTINGS_FIELD_TYPE` `SETTINGS_FIELD_TOO_LONG` `SETTINGS_FIELD_RANGE` `LLM_NOT_CONFIGURED` `LLM_MODE_TEMPLATE` `LLM_KEY_MISSING` `LLM_KEY_REJECTED` `LLM_OUTBOUND_BLOCKED` `LLM_TIMEOUT` `LLM_UNREACHABLE` `LLM_HTTP_ERROR` `LLM_PATH_NOT_FOUND` `LLM_RATE_LIMITED` `LLM_CLIENT_ERROR` `LLM_EMPTY_ANSWER` `LLM_TRANSPORT_MISCONFIGURED` `LLM_OK` | ✅ 已实现（t15）；探测是 **probe-only**（只发一条固定自检提示，不产生业务解释） |
| J3 | `/api/settings/purge` | POST | （规划） | `{scope,confirm:"DELETE"}` | — | — | ⚠ 未实现。现状清空 = **S1 `POST /api/reset`**（追加 `record_reset`，审计文件不动）；审计侧永不删（append-only） |
| S1 | `/api/reset` | POST | 需 `settings.write` + CSRF | 无请求体 | `{ok:true,…,records,records_durable,records_path,records_note}`（记录清回**演示种子 12 条** + 日志压实 + 追加一条 `record_reset` 审计） | `RESET_DISABLED`（`--no-reset` 启动，403）`ROLE_FORBIDDEN` `CSRF_FAILED` | ✅ 已实现（4.2b/4.5/**4.9**）；`settings.data.reset_enabled` 决定界面是否给入口；**4.9 起只清记录、审计只追加**（QA 5.1 的 reset 语义口径） |
| K1 | `/api/health` | GET | （规划） | — | — | — | ⚠ 未实现；**真实健康检查 = H0 `/health`（别名 `/healthz`）**，字段见 6.6；无 `ready`/`db`/`uptime_s` 字段 |
| Z1 | `GET /*`（静态站点） | GET/HEAD | 公开 | 路径限定在 `agent_explainer/web/` 根内 | `index.html`/`app.js`/`styles.css`/`i18n/*` 等，`Cache-Control: no-store` | `STATIC_NOT_FOUND` `STATIC_PATH_REJECTED`（`..`、目录列表） | ✅ 已实现（t8）；同进程托管，桌面与团队部署共用 |
| — | 其它路径 / 其它方法 | 任意 | — | — | — | 404 `ROUTE_NOT_FOUND`（API 前缀）/`STATIC_NOT_FOUND`（静态）；405 `METHOD_NOT_ALLOWED`（`PUT`/`PATCH`/`DELETE`/`OPTIONS`；`PUT` 只对 J1 开放） | ✅ 已实现 |

**实现口径（4.1 / 4.3 / 4.2b 已落地，队长裁决 t18 第 1 条）**：

- 用户写操作走 **POST 子资源**（B2 的 `/role`、`/status`、`/password`）；`PUT` / `PATCH` / `DELETE`（含 `/api/users/{id}`、`/api/audit`）一律 **405 `METHOD_NOT_ALLOWED`** —— 写接口只有一种形状，前端不容易"猜方法"，日志也更容易归因。
- 用户**只禁用不删除**：`status=disabled` 立刻吊销会话与能力，但账号记录保留。理由很硬：审计条目里的 `actor_id` 必须永远能追到一个真实存在的人，**删号等于毁证**；最后一名 admin 不可降级、不可自禁用 → **409** `USER_LAST_ADMIN` / `USER_SELF_DISABLE`。
- 审计是 **append-only**：`/api/audit` 只有 GET，服务端不存在"删除/清空/截断/重置审计"的任何端点（`POST /api/audit` → 405，`POST /api/audit/delete|clear|truncate|reset` → `ROUTE_NOT_FOUND`）。`--no-reset` 启动时 `POST /api/reset` 直接 403 `RESET_DISABLED`，审计文件一字节都不动；允许重置时只**追加**一条 `record_reset`。
- H2 `/api/audit/verify` 尚未实现（GET 该路径 → `STATIC_NOT_FOUND`）：4.2b 的链校验随 `/api/audit` 的 `chain` 块和 `/health` 的 `audit` 块返回，命令行校验走 `python -m agent_explainer.audit`。
- K1 现状：健康检查路径是 `/health`（别名 `/healthz`），`audit` 是对象 `{ok,count,head,durable,source,path,problems,sealed_head,sealed_count,seals}`，不是字符串 `"ok"`；审计断链时健康检查本身仍是 200（进程可用），由 `audit.ok=false` 表达"审计不可信"。

**4.4 / 4.5 落地口径（t19 从 `ingest.py`、`settings.py`、`server.py` 逐条核对后补写）**：

- **方法纪律（4.5 更新）**：`PUT` 现在**只对 `J1 /api/settings` 开放**（保存配置，权限 `settings.write` + CSRF）；其余路径的 `PUT/PATCH/DELETE/OPTIONS` 仍是 405 `METHOD_NOT_ALLOWED`，`POST /api/settings`、`PUT /api/settings/test-llm` 都有明确 `detail.hint` 指路。
- **上报鉴权例外（4.4，写给 QA 与安全走查）**：team 模式 `POST /api/ingest/events` **不要求用户会话**，改要求**作用域受限的机器令牌**（`X-TraceLens-Ingest-Token: tlk_…` 与 `Authorization: Bearer tlk_…` 等价，专用头优先）。这是对第 5 节"team 必须登录"的**已披露例外**，理由：上报是机器对机器（Agent 框架的 webhook 插件只会写 `Authorization`，SDK 无交互登录能力），把机器接入做成"必须登录的用户会话"会逼团队把真人口令塞进 Agent 配置，风险更大。边界逐条可测：① 令牌只有 `ingest:write`（可选 `tokens:manage`），**读不到用户表、看不到审计、不能替人决定**；② 令牌管理端点（E3/E4/E5）必须真人会话 + `ingest.token.manage` + CSRF；③ 明文只签发时返回一次，落盘是 `sha256(salt+明文)` + 前缀，文件 `chmod 0600`；④ 吊销立即 403 `INGEST_TOKEN_DISABLED`；⑤ 限流按令牌分桶（突发 40 / 稳态 600 条每分钟，429 带 `Retry-After`）；⑥ 审计 actor 写 `ingest:<label>/<token_id>`，本机匿名写 `ingest:anonymous-local` 且 `token.anonymous=true` 如实标注。
- **上报的字段边界（4.4，"决定 ≠ 生效"在写入路径的落地）**：合并白名单 `MERGE_FIELDS` = 22 个事实字段（`status`、`timestamp`、`result_summary`、`tool_used`、`latency_ms`、`cost`、`confidence`、`risk_level`、`impact_scope`、`error`、`requires_action`、`requires_action_reason`、`action_due_at`、`uncertainty`、`suggested_next`、`labels`、`sequence_no`、`context`、`trace_link`、`run_id`、`agent_name`）；保护字段 `PROTECTED_FIELDS` = `id`、`schema_version`、`user_intervention`、`intervention_options`、`human_readable`、`source`、`demo_data`、`synthetic`、`policy` —— **上报永不覆盖决定、选项与文案**。`human_readable` 只由接入层按合并后的事实用服务端模板**重算**（状态变"已完成"时 L1 同步为（已完成），实测过），避免出现"角标已完成、文案还写着进行中"。`mode=create` 撞上已存在 id → 计入 `duplicates`（带原因）不覆盖；只有 `update/upsert` 按白名单合并。
- **宽容模式语义（4.4）**：`strict=false`（默认）＝**坏事件跳过、好事件照常入库**；因为适配器整批转换会在第一条坏事件处停住，接入层逐条重放一次，跳过原因按**原始下标**进 `stats.skip_reasons[:20]`，调用方才知道改哪一条。`strict=true` 才整批拒绝（422 `INGEST_ADAPTER_REJECTED` / `INGEST_FORMAT_UNSUPPORTED`）。
- **SSE 断线口径（4.4，界面必须照此实现）**：`seq` 单调；环形缓冲 **200 帧**（有界内存），重连带 `Last-Event-ID` 或 `?since=` 补发；订阅上限 **16**（超出 503 `STREAM_LIMIT_REACHED`，界面自动降级）；心跳 15 秒且**心跳帧不带 `id:`**（重连游标仍是最后一条真事件）；慢客户端丢最旧帧并标 `degraded`，写入方永不被拖住；响应必须 `Connection: close` + `X-Accel-Buffering: no`（nginx 上不缓冲，"实时"才成立）。前端实况（`web/app.js`）：退避 800ms→上限 12000ms、最多 4 次重连，之后转**标注原因的轮询**（默认 4000ms，取 `GET /api/records` 全量对齐），顶栏徽标 6 态 `live/reconnect/poll/off/blocked/idle`（i18n 域 `realtime`，8 键 zh/en 齐平）。
- **设置的读写口径（4.5）**：`GET /api/settings` 只需 `records.read`（人人可读**掩码视图**：密钥只有 `api_key_mask = "••••••" + 末 4 位`、`api_key_fingerprint = sha256 前 12 位`、`api_key_source ∈ {dpapi,env,memory,none}`、`set_at`；**响应中无任何可还原形式**）；`PUT` 需 `settings.write` + CSRF（t13 权限表里只有 `local` 与 `admin` 有这项 → team 模式 `editor/viewer` 直接 403，**代码里没有第二套角色判断**）。写入后立刻 `apply_to(generator)`，响应 `engine` 块给引擎实况 + `effect_notice = settings.effect.engine_only`；对密钥只回 `key_action ∈ {set,same,cleared,unchanged}`。
- **设置的取值与护栏（4.5）**：可写字段只有 `WRITABLE_FIELDS` 三节（`llm`：`mode/provider/base_url/model/apiKey/timeout_s/temperature/max_tokens/allow_outbound`；`ui`：`language/granularity`；`data`：`reset_enabled/export_enabled`）。区间：`timeout_s 0.5–120`、`temperature 0.0–2.0`、`max_tokens 40–4000`；长度：`base_url ≤300`、`model ≤120`、密钥 ≤400；`provider ∈ {openai_compat,ollama,deepseek,custom}`；`language ∈ {zh,en}`、`granularity ∈ {L1,L2,L3}`。`base_url` 校验有 7 个专属码（空白/空格/协议/查询串/锚点/用户名密码/端口），**非回环主机必须 `https` 且显式 `allow_outbound=true`**，否则 403 `LLM_OUTBOUND_BLOCKED`（默认只连本机：Ollama/vLLM/llama.cpp）。密钥字段名在 API 里是驼峰 `apiKey`，哨兵：不出现 / `null` / `"keep"` ＝不动，`""` ＝清除；`server.py` 被源码级测试禁止拼出密钥字段的蛇形写法，只转交 `settings.py` 加密（Windows DPAPI 落盘；非 Windows 只驻内存；team/容器优先 `TRACELENS_LLM_API_KEY` 且**不落盘**）。
- **`test-llm` 是 probe-only（4.5）**：只允许 `{prompt?≤2000, max_tokens?(40–4000), timeout_s?(0.5–120)}` 三个探测参数，**请求体不接受密钥/地址**（否则日志与审计会带上凭据、权限边界形同虚设）；固定探测语要求模型"不要编造动作结果、不要说已完成"；响应带 `effect_notice = settings.test_llm.probe_only` 与 `sent_facts_note`。
- **审计接入面（4.4/4.5 不改审计算法）**：上报与令牌事件以 `event="note"` + `extra.ingest_event`（`batch_accepted` / `token_issued` / `token_revoked`）追加记账，**请求体明文不进审计**（t9 脱敏口径），只记数量与 id；设置变更以 `note` 事件带 `settings_*` 字段（不含密钥）。这两条是 t9/t18 口径的**追加式扩展**，`audit.py` 只扩了 `EVENTS` 白名单，链算法一字未改。


**4.9 落地口径（t22：数据导入与记录持久化，补 QA 5.1 的 D1「没有用户可见的导入入口」+「重启回到演示 12 条」）**：

- **一条门，两种来源**：粘贴文本与上传文件走同一个 `POST /api/import`（前端读文件内容再发，服务端不接受 multipart，也就不需要第二套解析路径）；`filename` 有值只代表"来自文件"，用于把 `source.kind` 标成 `upload`（粘贴是 `paste_json`），**不影响解析**。入口在界面上有两处：手机视口是底部面板里的导入区（`#sheet-import`），桌面视口是侧栏（`#import-box`），两端同一份逻辑。
- **格式不猜、也不换**：`format` 留空交给 `trace_adapter.detect` 认 Langfuse / OTel GenAI / MCP / 原生四种；显式声明与内容对不上时**把适配器的码原样返回**（`detail.adapter_code` + `index` + `supported`），绝不"换一种格式硬解析"，也不静默丢记录（K-03：说清第几条、为什么、怎么改）。
- **上限与 E1 对齐**：≤64 KB/批、≤200 条事件/批（`IMPORT_MAX_RECORDS == ingest.MAX_EVENTS_PER_REQUEST`，两侧由测试钉住不得漂移）；请求体本身超限是 413 `INTERVENE_BODY_TOO_LARGE`，文档超限是 413 `IMPORT_TOO_LARGE`，条数超限是 400 `IMPORT_TOO_MANY`（带 `detail.count/limit` 与"请拆批"的建议）。
- **决定 ≠ 生效、导入 ≠ 覆盖决定**：合并复用 4.4 的 `merge_facts`，受保护字段 `PROTECTED_FIELDS` 一字不改 → 用户已记录的决定（`user_intervention`）活得过再次导入；`mode=create` 撞 id → 计入 `duplicates`（逐条带原因）不覆盖。
- **审计只记账不记正文**：以 `event="note"` 追加，字段 `import_source/import_format/imported/created/updated/duplicates/skipped/import_mode/import_bytes/import_filename/import_ids/import_body="not_recorded"`；粘贴与上传的**原文一个字都不进审计**（原文里常有邮箱、密钥、内部路径，第 13 节脱敏口径）。
- **导出面（4.8 已落地，M6）**：`GET /api/audit/export` 是唯一证据出口，三种格式各管一段用途——`csv`（下载，Excel 认中文）/ `html`（零外链零脚本，Ctrl+P 存 PDF）/ `pdf`（reportlab 内存排版）。三条硬口径：① **链断不出证据**（先 `require_audit_chain()`，断链 500 `AUDIT_CHAIN_BROKEN`，避免"拿一份不可信的东西当证据"）；② **先记账再交文件**（`event=export`，只记格式/条数/字节/过滤摘要/`sha256` 指纹，不记原文与密钥；记账失败 → 500）；③ **第三方库缺失不炸**（reportlab 不可用 → 退回可打印 HTML + `X-TraceLens-Export-Fallback`；字体缺中文 → 额外 `X-TraceLens-Export-Note`，因为 PDF 会**静默丢中文**，这句话必须跟着文件走）。响应 `Cache-Control: no-store`（证据不落浏览器缓存）；`X-TraceLens-Export-Sha256` 让接收方能证明"拿到手的内容就是当时导出的内容"。
- **写了就要落盘**：持久模式下记录写不进 `var/records.jsonl` 就 500 `RECORD_STORE_WRITE_FAILED`（`store_facts().write_errors/problems` 累计，`/health` 现报），绝不做"导入成功、重启就没了"；`/api/records` 的 `meta.imported_count/records_durable/records_store` 让界面能诚实显示"这批在不在磁盘上"。
- **权限**：`data.write`（`local`/`admin`/`editor` 有，`viewer` 无 → 403 `ROLE_FORBIDDEN`）+ 写操作 CSRF；`GET /api/import` → 405 `METHOD_NOT_ALLOWED`；local 模式匿名放行并如实记 actor `local`。

### 6.4 通用错误码表（全端点适用）

| HTTP | code | 场景 | 前端动作（人话） | 现状（代码实测） |
|---|---|---|---|---|
| 400 | `INTERVENE_BODY_INVALID` | JSON 语法错误 | "这段内容格式不对，检查第 N 行" | ✅（旧名 `REQUEST_MALFORMED` 未使用） |
| 400 | `INTERVENE_BODY_NOT_OBJECT` | 请求体是合法 JSON 但不是对象 | "这里要的是一个对象" | ✅ |
| 400 | `INTERVENE_FIELD_MISSING` / `INTERVENE_FIELD_TYPE` / `INTERVENE_FIELD_UNKNOWN` | 必填缺失 / 类型不对 / 出现不认识的字段（`detail.field`、`detail.accepted`） | 表单内联提示到具体字段 | ✅（旧名 `PARAMS_INVALID` 未使用） |
| 400 | `INTERVENE_DECISION_INVALID` | `decision` 不在 `approve/modify/reject` | "请选择同意 / 修改 / 拒绝" | ✅ |
| 400 | `INTERVENE_REASON_REQUIRED` / `INTERVENE_REASON_TOO_SHORT` | 拒绝未填理由 / 理由 <2 字（≤500 字） | 焦点回到理由框 | ✅（旧名 `REASON_REQUIRED` 未使用，实际带前缀） |
| 400 | `INTERVENE_PARAM_*`（`MISSING/EMPTY/TYPE/RANGE/UNKNOWN/NOT_ALLOWED/NO_CHANGE`） | 修改参数的七种情形（缺值/空/类型/越界/不可编辑/modify 才允许/与当前值相同） | 逐条标到对应输入框 | ✅ |
| 400 | `INTERVENE_OPTION_MISMATCH` / `INTERVENE_IDEMPOTENCY_MISMATCH` | 选项不属于本动作 / 同一幂等键换了请求体 | "刷新后重试" | ✅ |
| 400 | `INTERVENE_RECORD_INVALID` | 存储里的记录读不出对象 | "这条记录已损坏，导出编号 req-…" | ✅ |
| 400 | `AUDIT_QUERY_INVALID` | 审计查询参数不合法（`limit`/`offset` 非数字或负数、`event`/`decision` 不在枚举、`since` 晚于 `until`）——**H1 列表与 M6 导出共用同一批校验**（导出的 `limit` 会被钳到 1–5000，不报错） | "筛选条件写错了"（附 `detail.reason` 人话），保留原表单 | ✅（4.2b；**4.8 起 M6 也用它**：`detail.audit_code` 给细分码 `AUDIT_TIME_RANGE_INVERTED`/`AUDIT_TIME_INVALID`/`AUDIT_FIELD_ENUM`/`AUDIT_FIELD_TYPE`，`detail.filters` 回显本次过滤） |
| 400 | `EXPORT_FORMAT_INVALID` | `format` 不在 `csv\|html\|pdf`（大小写不敏感、会先 `strip()`；空值＝默认 `csv` 不报错） | 界面按 `export.error.format_invalid` 显示一句人话（**不打印服务端原文**）；`detail.allowed` 给出允许值，便于复制 | ✅（**4.8 / t21**）。**i18n 实况**：字典里**没有** `audit.error.export_format_invalid` 这类 `<域>.error.<code>` 键（在 `tools/i18n_lint.py` 的无键清单里），前端靠 `app.js` 的 `EXPORT_ERROR_KEYS`（7 条显式映射）出文案；无映射的码必须走 `message` 回退 |
| 400 | `IMPORT_INPUT_EMPTY` / `IMPORT_INPUT_NOT_JSON` | 粘贴框是空的 / 粘进来的不是合法 JSON（`detail.line`、`detail.first_chars`） | 导入面板内联提示"第 N 行解析不过"，**不清空用户已粘的内容**（i18n `import.validation.*` / `import.error.input_*`） | ✅（**4.9**） |
| 400 | `IMPORT_MODE_INVALID` / `IMPORT_TOO_MANY` / `IMPORT_FORMAT_UNSUPPORTED` | `mode` 不在 `create/update/upsert` / 单批超过 200 条（`detail.count/limit`）/ 声明的格式与内容对不上 | 逐条说清"这批 300 条 > 上限 200，请拆批"、"支持 langfuse/otel_genai/mcp_events/native"；格式对不上时服务端把适配器码原样返回（`TRACE_*` + `detail.adapter_code`），**不静默换格式** | ✅（**4.9**；`IMPORT_FORMAT_UNSUPPORTED` 只在"内容与四种格式都不像"时出现） |
| 413 | `IMPORT_TOO_LARGE` | 粘贴/上传的一批超过 **64 KB**（`IMPORT_MAX_BYTES`，与 `MAX_BODY_BYTES` 同口径；请求体本身超限则是 413 `INTERVENE_BODY_TOO_LARGE`） | 引导拆批（面板显示 `字节/上限`，i18n `import.validation.too_large`） | ✅（**4.9**） |
| 500 | `RECORD_STORE_WRITE_FAILED` | 持久模式下记录写不进 `var/records.jsonl`（目录不可建、磁盘只读、文件被占用） | "记录已经在内存改好了，但没写进文件：检查 `var/` 权限与磁盘空间；审计链不受影响"（绝不静默报成功） | ✅（**4.9**；细分码 `RECORD_STORE_LOCKED/LINE_INVALID/LINE_TOO_LARGE/RECORD_INVALID/TAIL_TRUNCATED/OP_UNKNOWN` 在 `store_facts().problems`、`/health` 的 `records_store` 与 `GET /api/records` 的 `meta.records_store`） |
| 400 | `INGEST_SCHEMA_INVALID` / `INGEST_EMPTY` / `INGEST_FIELD_TYPE` | 信封不是对象/数组、没有事件、`mode`/`strict`/`label`/`scopes`/`ttl_seconds` 不合法 | 机器侧：按 `detail.field`/`detail.hint` 改投递脚本 | ✅（t14；旧表把 `INGEST_SCHEMA_INVALID` 记成 422，实际 **400**） |
| 400 | `STREAM_QUERY_INVALID` | `?since`/`?max_events`/`?types`/`?heartbeat` 写错 | 界面重连带错游标是 bug：**必须报警**，不许静默按默认值猜 | ✅（t14） |
| 400 | `SETTINGS_*`（21 个：`BODY_NOT_OBJECT/BODY_EMPTY/NO_CHANGES/SECTION_UNKNOWN/FIELD_UNKNOWN/FIELD_TYPE/FIELD_MISSING/FIELD_TOO_LONG/FIELD_RANGE/MODE_INVALID/PROVIDER_INVALID/LANGUAGE_INVALID/GRANULARITY_INVALID/URL_MALFORMED/URL_SCHEME/URL_QUERY/URL_FRAGMENT/URL_USERINFO/URL_PORT/WRITE_FAILED/READ_FAILED`+`FILE_INVALID`） | 设置写入的逐字段问题（含 `base_url` 的 7 个专属码） | 设置页逐字段内联提示（i18n `settings.error.*` 38 条与服务端码一一对应） | ✅（t15） |
| 401 | `AUTH_REQUIRED` | 未登录/会话过期（team） | 跳登录页，保留回跳 | ✅（t13） |
| 401 | `AUTH_TOKEN_EXPIRED` / `AUTH_TOKEN_INVALID` / `AUTH_TOKEN_REVOKED` / `AUTH_USER_DISABLED` / `AUTH_USER_NOT_FOUND` / `AUTH_BAD_CREDENTIALS` | 会话与凭据的六种失效 | 分别说清"过期/无效/已被吊销/账号已禁用/无此账号/口令不对" | ✅（t13） |
| 401 | `INGEST_TOKEN_MISSING` / `INGEST_TOKEN_INVALID` | team 模式上报没带令牌 / 令牌不认识 | 机器侧：补 `X-TraceLens-Ingest-Token`（E1） | ✅（t14） |
| 403 | `ROLE_FORBIDDEN` | 越权（viewer 干预、`editor` 改设置、非 `ingest.token.manage` 建令牌） | "这个操作需要管理员" + 入口隐藏 | ✅ |
| 403 | `CSRF_FAILED` | 缺/错 CSRF token（写操作） | 提示刷新页面 | ✅ |
| 403 | `RESET_DISABLED` | 用 `--no-reset` 启动后调用演示重置 | "演示重置在这台服务上已关闭"（审计文件不受影响） | ✅（4.2b） |
| 403 | `INGEST_DISABLED` | 服务端 `--no-ingest` 关闭了接入 / team 无令牌保管箱（fail closed） | "这台服务没打开实时接入" | ✅（t14） |
| 403 | `INGEST_TOKEN_DISABLED` / `INGEST_TOKEN_EXPIRED` / `INGEST_SCOPE_FORBIDDEN` | 令牌被吊销 / 已过期 / 作用域不够 | 机器侧：换令牌或补 scope | ✅（t14） |
| 403 | `LLM_OUTBOUND_BLOCKED` | 非回环地址且未开 `allow_outbound`（或未用 `https`） | "默认只连本机；要连外网请在设置里显式打开开关" | ✅（t15） |
| 403 | `BOOTSTRAP_LOCAL_FORBIDDEN` / `PERMISSION_UNKNOWN` | local 模式调用建账号 / 查询不存在的能力名 | 配置类错误，按 `detail` 修 | ✅（t13） |
| 404 | `INTERVENE_ACTION_NOT_FOUND` | 动作不存在（含 `GET /api/records/{id}`） | "这条记录已被清空或属于其他空间" | ✅（旧名 `ACTION_NOT_FOUND` 未使用） |
| 404 | `ROUTE_NOT_FOUND` / `STATIC_NOT_FOUND` | API 路径未注册 / 静态文件不存在（含 6.3 标 ⚠ 的规划路径） | 404 页；测试脚本按 6.5 核对表判定 | ✅ |
| 405 | `METHOD_NOT_ALLOWED` | 对只读端点用 `PUT/PATCH/DELETE/OPTIONS`（`PUT` 只对 `/api/settings` 开放）；`GET /api/ingest/events\|tokens`；`PUT /api/settings/test-llm` | 按 6.3 的方法列改调用（`detail.hint` 会指路） | ✅ |
| 406 | `STREAM_ACCEPT_REQUIRED` | 请求 `/api/stream` 未带 `Accept: text/event-stream` | 客户端补头（`EventSource` 自带） | ✅（t14） |
| 409 | `INTERVENE_STATE_INVALID` / `INTERVENE_ALREADY_DECIDED` | 该记录不在待决状态 / 已有决定（`detail.decided_by/decided_at`） | "这条已经被处理过（谁/何时）" | ✅（旧名 `NOT_PENDING` 未使用） |
| 409 | `INGEST_IDEMPOTENCY_CONFLICT` | 同一 `Idempotency-Key` 换了请求体 | 机器侧：一次上报一个 key | ✅（t14；**旧表口径修正**：幂等"命中"是 200 重放 + `replay:true`，**不是** 409） |
| 409 | `USER_LAST_ADMIN` / `USER_SELF_DISABLE` / `USER_EXISTS` / `USER_DELETE_DISABLED` / `USER_STATUS_INVALID` | 用户管理五情形（删号=毁证，一律禁用） | 说清"最后一名管理员不能动 / 不能自禁用 / 已存在 / 只禁用不删除" | ✅（t13） |
| 413 | `INTERVENE_BODY_TOO_LARGE` / `INGEST_TOO_LARGE` | 请求体超过 **64 KB**（`MAX_BODY_BYTES`，两域同口径） | 引导分片；旧口径"8MB"**作废** | ✅ |
| 413 | `INGEST_TOO_MANY_EVENTS` | 单批事件超过 200 条 | 机器侧：拆批（≤200/批） | ✅（t14） |
| 415 | `INTERVENE_CONTENT_TYPE` | `Content-Type` 不是 `application/json` | 客户端修头 | ✅ |
| 422 | `INGEST_FORMAT_UNSUPPORTED` / `INGEST_ADAPTER_REJECTED` | 事件格式不在四类支持内 / 适配器拒绝（`strict=true` 整批；`strict=false` 时坏事件进 `stats.skip_reasons`，好事件照常入库，HTTP 200） | 逐条说"第 N 条、为什么、怎么改"（K-03） | ✅（t14；逐条 schema 错误明细目前是**适配器 Python API** 的 `ConversionOutcome.errors`，HTTP 响应里以 `stats.skip_reasons[:20]` 呈现） |
| 429 | `RATE_LIMITED` | 登录失败 5 次锁 15 分钟（`Retry-After`） | "请 N 秒后再试" | ✅（t13） |
| 429 | `INGEST_RATE_LIMITED` | 每令牌令牌桶耗尽（突发 40 / 稳态 600 条每分钟，`Retry-After` + `detail.capacity/per_minute/events_in_batch`） | 机器侧按建议等待 | ✅（t14） |
| 500 | `INTERNAL_ERROR` | 未预期错误 | "已生成编号 req-…，日志里没有你的数据" | ✅（旧名 `INTERNAL` 未使用） |
| 500 | `AUDIT_CHAIN_BROKEN` | 追加式审计链校验失败（被改、被截、断链、封印对不上）——触面有三处：`GET /api/audit`（H1）、`POST /intervene`（G1，**且不落决定**）、**`GET /api/audit/export`（M6，4.8 起：断链的导出不能当证据，直接 500）** | "审计文件被动过：先取证（`python -m agent_explainer.audit`），服务端不会自动清空或重建"；显示 `detail.reason` 中文原文 | ✅（4.2b；细分码 `chain_break/hash_mismatch/alias_mismatch/seq_gap/blank_line/unparsable_line/seal_mismatch/head_mismatch/count_mismatch` 在 `problems[]` 与 `AuditTamperError` 里，旧名 `AUDIT_BROKEN_CHAIN` 未使用）；界面文案走 `app.js` 显式映射 `audit.error.chain_broken` / `export.error.chain_broken`（无 `<域>.error.*` 键，**禁止把服务端原文直接打印给用户**） |
| 500 | `INGEST_STORE_FAILURE` / `SETTINGS_WRITE_FAILED` | 存储写入失败 / 设置落盘失败 | "保存失败，已生成编号 req-…；配置未改变" | ✅（t14/t15） |
| 500 | `AUDIT_EXPORT_WRITE_FAILED` / `AUDIT_PDF_EMPTY` / `AUDIT_REPORTLAB_UNAVAILABLE` | 审计导出层（`audit.py` 的 `AuditExportError`）：写文件失败 / PDF 构建结果为空 / **reportlab 未安装** | 前两者界面显示一句人话 + 生成编号；**第三者不是错误**：M6 自动退回可打印 HTML（`X-TraceLens-Export-Fallback` + `export.error.reportlab_missing` 文案 + 建议"改用打印"），用户照样拿到证据 | ✅（**4.8 / t21**）；三个码都在 `i18n_lint` 无键清单里，`AUDIT_REPORTLAB_UNAVAILABLE` 另有 `export.error.reportlab_missing` 显式映射 |
| 503 | `STREAM_LIMIT_REACHED` | 同时在线订阅者超过 16 | 界面自动降级轮询并**写明原因**（记录端点仍是权威） | ✅（t14；旧名 `STREAM_LIMIT` 未使用） |
| 200 | `LLM_*`（`OK/MODE_TEMPLATE/NOT_CONFIGURED/KEY_MISSING/KEY_REJECTED/UNREACHABLE/TIMEOUT/HTTP_ERROR/PATH_NOT_FOUND/RATE_LIMITED/CLIENT_ERROR/EMPTY_ANSWER/TRANSPORT_MISCONFIGURED`） | 模型连接探测结果 | 设置页显示可读原因（模板模式继续可用，K-12） | ✅（t15）；**修正**：这些是 `test-llm` 响应体里的 `code`（HTTP 200 + `ok:false`），不是 HTTP 503；旧行的 `NOT_READY` 未使用 |
| — | `DB_UNAVAILABLE` / `DB_LOCKED` / `QUERY_TOO_BROAD` / `CURSOR_INVALID` / `SCHEMA_VERSION_UNSUPPORTED` / `SESSION_NOT_FOUND` / `EXPORT_TOO_LARGE` / `EXPORT_RANGE_INVALID` / `PURGE_CONFIRM_REQUIRED` / `SETTING_READONLY` / `VALUE_OUT_OF_RANGE` / `AUTH_LOCKED` / `INGEST_DUPLICATE` / `INGEST_NOT_REPLAYABLE` / `INGEST_FORMAT_UNKNOWN` | 属于未实现子系统（SQLite 存储层、游标分页、导出端点、purge、replay） | — | ⚠ 代码中**不会出现**这些码；对应端点见 6.3 的 ⚠ 行。`AUTH_LOCKED` 的实际码名是 `RATE_LIMITED`（429）。**4.11 复核**：导出端点已由 M6 实现，但**没有** `EXPORT_TOO_LARGE` / `EXPORT_RANGE_INVALID` 这两个码——导出体积靠 `limit`（钳到 1–5000）+ 过滤条件控制，时间区间非法统一走 `AUDIT_QUERY_INVALID`（`detail.audit_code=AUDIT_TIME_RANGE_INVERTED`）；新增的导出码只有 `EXPORT_FORMAT_INVALID` |

**错误码纪律**：新增 code 必须先进本表 + i18n 双语字典 + 至少一条测试用例；前端遇到未知 code 必须显示 `message` 原文并提示"编号 req-…"，禁止白屏。

**i18n 实况（t19 核对）**：字典里 code 键的**实际命名是 `<域>.error.<code 小写>`**（例 `intervene.error.intervene_state_invalid`、`settings.error.settings_url_scheme`、`auth.error.auth_bad_credentials`），不是旧文写的顶层 `error.<CODE>`。实况统计（脚本从 `zh.json` 与五个模块的码表交叉核对）：代码里共 **128 个码**，其中 **85 个有 code 键**（`intervene.error.*` 31 条、`settings.error.*` 38 条、`auth.error.*` 若干），**43 个没有 code 键** —— 全部集中在 ingest/stream（19 个）、audit 域（16 个）与服务端内部码（8 个），因为这些是**机器对机器 / CLI** 场景，界面不直接展示；界面实时域另有 `realtime.*` 8 键（含降级原因）。**QA 结论要求**：无 code 键的码必须走 `message` 回退显示（服务端这些码的 `message` 是人话），不得白屏。
**4.7 复核口径（可复现，以工具为准）**：`python tools/i18n_lint.py` 用 AST 从 8 个模块（`server/settings/auth/ingest/audit/trace_adapter/explanations/__init__`）**只取"携带 code 的位置"**（`code=` 关键字、`{"code": …}`、`*_CODE*/_STATUS` 表键与值、`XError("CODE", …)`、`== "CODE"`、签名默认值；排除文档字符串、`__all__` 导出名、环境变量名、HTTP 头名、媒体类型等 23 项非码字面量），得 **155 个码 / 75 个有 `<域>.error.<code>` 键 / 80 个无键走 `message` 回退**，并打印无键清单供 QA 逐条核对（`--json` 出报告，`--strict-codes` 可把"无键"升级为失败）。上面的 128 / 85 / 43 是 t19 的人工计数，覆盖面小（漏了 `audit.py` 的小写链码、`trace_adapter.py`/`explanations.py` 域与部分服务端内部码），**已由工具口径取代**。
**4.11 复核（以工具为准，`python tools/i18n_lint.py` + `tools/gen_api_contract.py --check` 同源）**：字典 **zh 689 键 / en 689 键 / 21 域**（4.9 加了 `import` 域 73 键），其中 `error` 键 **144 条**；码表现共 **164 个码**（4.8/4.9 新增导出与持久化码后由 155 涨到 164），**有 `<域>.error.<code>` 键 75 个 / 无键 89 个**。界面上的真实文案来源是**两张表叠加**：① 约定式 `<域>.error.<code 小写>`；② `app.js` 里 **6 张显式映射表**（`AUDIT_ERROR_KEYS=5`、`AUTH_ERROR_KEYS=32`、`EXPORT_ERROR_KEYS=7`、`IMPORT_ERROR_KEYS=27`、`INTERVENE_ERROR_KEYS=31`、`SETTINGS_ERROR_KEYS=38`）。**审计导出这一族**（`EXPORT_FORMAT_INVALID`、`AUDIT_QUERY_INVALID`、`AUDIT_CHAIN_BROKEN`、`AUDIT_REPORTLAB_UNAVAILABLE`）都**没有**约定式 code 键，只靠 ②（`export.error.format_invalid` / `export.error.chain_broken` / `export.error.query_invalid` / `export.error.reportlab_missing`）+ 兜底 `export.error.generic` / `export.error.network` 出文案；QA 判据不变：**无键码必须走 `message` 回退且禁止白屏，禁止把服务端原文直接当用户文案打印**（无键清单可用 `i18n_lint --json` 逐条查）。
`tools/gen_api_contract.py` 与契约文件 **4.7 已就位**：生成物落点是 **`docs/api_contract.json`**（不在包内，避免与"包内只放运行时代码"的落点混淆，见差异 16），内容为 25 条 `(path, verb)` 路由、20 个 `API_PATH_*` 常量、5 条公开路径、4 角色 / 8 能力矩阵、显式 405 清单、155 个错误码及其 i18n 覆盖、`web/app.js` 的 18 条 `API_ENDPOINTS` 调用面，以及本节 §6.5 的交叉核对结果；`python tools/gen_api_contract.py --check` 逐条比对代码与 6.5 表，漂移即非零退出。契约来源 = 该文件 + 本节 + 6.5 核对表 + `web/app.js` 的 `API_ENDPOINTS` 常量表 + `sdk/client.py`。

### 6.5 路由实况核对表（4.6 / t19：从代码提取，逐条比对）

**提取方法（可复现，QA 与后续任务照此复核，别凭记忆写文档）**：

1. **路由清单** = `server.py` 顶部常量块 `API_PATH_*`（16 条已注册路径 + `API_PATHS_PUBLIC` 公开白名单），再与 `do_GET` / `do_POST` / `do_PUT` / `do_PATCH` / `do_DELETE` / `do_OPTIONS` / `do_HEAD` 的分发分支交叉核对（只有常量没有分支 = 现状 404；只有分支没有常量 = 代码里手写路径串，必须补常量）。
2. **权限口径** = 各分支里的 `_session_and_permission("<能力>")` 与 `_csrf_guard(session, channel)`；角色→能力矩阵的唯一来源是 `auth.py` 的 `ROLE_PERMISSIONS`（8 个能力：`records.read`、`data.write`、`ingest.token.manage`、`intervene.decide`、`audit.read`、`audit.export`、`settings.write`、`users.manage`；`local` 7 项＝除 `users.manage`，`admin` 8，`editor` 5，`viewer` 3）。新增两个能力（`ingest.token.manage`、`settings.write`）是 4.4/4.5 引入的，[TL-PRD-001 §6.4](./01-product-and-users.md) 矩阵已同步。
3. **错误码** = 四张表逐一取：`ingest.py` 的 `INGEST_STATUS`（19 条：码 → HTTP + 原因 + 建议）、`settings.py` 的写入校验码（21）与 LLM 探测码（14）、`auth.py` 的 `AUTH_*`/`USER_*`/`ROLE_*`/`CSRF_FAILED`/`RATE_LIMITED`/`BOOTSTRAP_LOCAL_FORBIDDEN`、`audit.py` 的 `AUDIT_*` 与篡改细分码；HTTP 层从未注册的规划码在 6.4 标 ⚠。
4. **字段清单** = `/health` 取 `health()` 与 `ingest_health_block()` / `stream_health_block()`；设置取 `settings.py` 的 `public()`；SSE 事件名取 `ingest.py` 的 `EVENT_*` 常量；前端调用取 `web/app.js` 的 `API` 常量表（17 条键，与 6.3 的 ✅ 行一一对应）。
5. **机器复核（4.7 新增，别再靠人眼）**：`python tools/gen_api_contract.py --write` 用 AST 重复上面 1–3 步（只取 `do_GET/do_POST/do_PUT/do_PATCH/do_DELETE/do_OPTIONS/do_HEAD` 的分支条件与分支内的权限/CSRF/处理器调用，`_user_route`、`_ingest_token_route` 这类"形状写在助手函数里"的路由由工具里的映射表显式登记），写出 `docs/api_contract.json`；`--check` 把生成结果与本表的 **"代码路径"列 + 方法列 + 结论列** 逐条比对——文档有代码没有、代码有文档没写、方法列缺项、结论列不是 ✅ 都会打印逐条差异并非零退出。实测（**4.11 / t24 复跑**）：**27 条 `(path, verb)` ↔ 22 个 `API_PATH_*` 常量 ↔ 本表 23 行 / 25 条文档路径**，方法列一致、**0 条 ⚠**、退出码 **0**（`/api/import`＝4.9；`/api/audit/export`＝4.8 的路由于 4.9 补进本表，**4.11 把该行的权限、格式集合、过滤键名、链断与回退口径按 `server.py` 逐字改正**——旧行写了代码里不存在的 `json` 格式、`from/to` 键名与"签名链接/过期码"，并**在 6.3 补出 M6 行**：此前只有本表有这条路由、6.3 端点表没有，现已闭合）。

**逐条核对结论**（`代码路径` 列就是 `server.py` 的常量值；`结论` = 文档是否已按实况改好）：

| 代码路径 | 方法 | 权限/凭证（代码实测） | 文档 6.3 行 | 结论 |
|---|---|---|---|---|
| `/health`（别名 `/healthz`） | GET | 公开 | H0 | ✅ 已对齐；字段见 6.6 |
| `/api/records`、`/api/records/{id}` | GET | `records.read` | R1 / R2 | ✅ 已对齐（旧名 `/api/actions*` 保留为 ⚠ 行） |
| `/intervene` | POST | `intervene.decide` + CSRF | G1 | ✅ 已对齐（码表已改 `INTERVENE_*`） |
| `/api/import` | POST | `data.write` + CSRF（`viewer` 403；`GET` → 405） | D3 | ✅ 新增行（**4.9 / t22**：粘贴/上传导入入口、四种格式自动识别、64 KB / 200 条上限、审计不记正文） |
| `/api/audit/export` | GET | `audit.export`（`local`、`admin`、`editor`、`viewer` 四角色都有；无此能力 403 `ROLE_FORBIDDEN`；team 未登录 401）；**GET 无副作用 → 免 CSRF**（代码注释原话） | **M6**（4.11 新补进 6.3 端点表） | ✅ 已对齐（**4.8 / t21 的路由；4.11 / t24 把行内事实按代码逐字改对**：`format` 三选一 `csv` / `html` / `pdf`，默认 `csv`、大小写不敏感、非法 400 `EXPORT_FORMAT_INVALID`；**没有 `json`**（JSON 读记录走 R1）；`limit` 默认 500、钳到 1–5000；`lang` 取 `zh` / `en`；过滤与 H1 同批键 `actor_id` `action_id` `decision` `event` `task_id` `q` `since` `until`（**是 `since` 与 `until`，不是 `from` 与 `to`**）；链断 500 `AUDIT_CHAIN_BROKEN` 不出证据；reportlab 缺失 → 退回可打印 HTML + 头 `X-TraceLens-Export-Fallback`；每次导出**先记账** `event=export`（只记格式、条数、字节、过滤摘要、`sha256` 指纹）；`POST` → 405。**旧行写的"csv、json、html 三种"、"过滤参数 `from` 与 `to`"、"签名链接与过期码"与代码不符，已删**——代码里没有签名链接、没有过期码，`EXPORT_TOO_LARGE` 与 `EXPORT_RANGE_INVALID` 也不存在） |
| `/api/audit` | GET | `audit.read` | H1 | ✅ 已对齐（t18） |
| `/api/auth/status`、`/api/auth/permissions` | GET | 公开探针 | A3b | ✅ 已对齐 |
| `/api/auth/login` | POST | 公开 + 限速 | A1 | ✅ |
| `/api/auth/logout`、`/api/auth/check` | POST | 登录 | A2 / A5 | ✅ |
| `/api/auth/me` | GET | 登录 | A3 | ✅ |
| `/api/auth/password` | POST | 登录 + CSRF | A4 | ✅ |
| `/api/users` | GET / POST | `users.manage` + CSRF | B1 | ✅ |
| `/api/users/{id}/role`、`/status`、`/password` | POST | `users.manage` + CSRF | B2 | ✅ |
| `/api/ingest/events` | POST | 机器令牌（team 必需 / local 匿名） | E1 | ✅ 已对齐（响应形状、限流、幂等、宽容模式全按代码写） |
| `/api/ingest/tokens` | POST | `ingest.token.manage` + CSRF | E3 | ✅ 新增行 |
| `/api/ingest/tokens/{id}/revoke` | POST | `ingest.token.manage` + CSRF | E4 | ✅ 新增行 |
| `/api/ingest/status` | GET | `ingest.token.manage` | E5 | ✅ 新增行（列清单唯一入口；GET events/tokens → 405） |
| `/api/stream` | GET(SSE) | `records.read` | F1 | ✅ 已对齐（8 个事件名、环形 200、上限 16、心跳 15s） |
| `/api/settings` | GET / PUT | GET `records.read`；PUT `settings.write` + CSRF | J1 | ✅ 已对齐（PATCH 已改 PUT） |
| `/api/settings/test-llm` | POST | `settings.write` + CSRF | J2 | ✅ 已对齐（参数收敛为 3 个） |
| `/api/reset` | POST | `settings.write` + CSRF | S1 | ✅ 新增行（旧 `purge` 标 ⚠） |
| `/api/secretariat` | GET | `audit.read`（族入口，等价 T1 status 摘要） | T1 | ✅ 5.0 总裁办新增行（R4；见 docs/12 §6） |
| `/api/secretariat/status` | GET | `audit.read` | T1 | ✅ 5.0 新增行（身份/委托/认定书计数 + 三条账本链校验 + 风险策略 + degraded） |
| `/api/secretariat/identities` | GET / POST | 读 `audit.read`；写 `settings.write` + CSRF | T2 | ✅ 5.0 新增行（注册/轮换；201；私钥永不出现） |
| `/api/secretariat/identities/{id}/revoke` | POST | `settings.write` + CSRF | T3 | ✅ 5.0 新增行（吊销为追加记录，幂等） |
| `/api/secretariat/delegations` | GET / POST | 读 `audit.read`；写 `settings.write` + CSRF | T4 | ✅ 5.0 新增行（收窄校验、深度 ≤5、过期读时判定） |
| `/api/secretariat/records/{id}/sign` | POST | `settings.write` + CSRF | T5 | ✅ 5.0 新增行（用记录所属 Agent 私钥签名并落盘，返回复算结果） |
| `/api/secretariat/verify` | POST | `audit.read`（GET 无副作用，免 CSRF；`GET` → 405） | T6 | ✅ 5.0 新增行（逐项复算：身份/签名/记录哈希/前序/时间戳/责任链/审批/盖章） |
| `/api/secretariat/letters` | GET / POST | 读 `audit.read`；写 `settings.write` + CSRF | T7 | ✅ 5.0 新增行（POST 生成认定书，≤200 条记录；record_ids 缺失 → 404） |
| `/api/secretariat/letters/{id}` | GET | `audit.read` | T8 | ✅ 5.0 新增行（正文 + 离线复核结果） |
| `/api/secretariat/letters/{id}/verify` | POST | `audit.read` | T9 | ✅ 5.0 新增行（离线复算入口：签名 + records_root + 逐条动作签名） |
| `/api/secretariat/seal` | POST | `settings.write` + CSRF | T10 | ✅ 5.0 新增行（总裁办盖章；GET → 405） |
| web/ 静态根 | GET/HEAD | 公开（限定根目录） | Z1 | ✅ 新增行 |
| 其它路径 / `PATCH`/`DELETE`/`OPTIONS` | — | — | 末行 | ✅ 404 `ROUTE_NOT_FOUND`/`STATIC_NOT_FOUND`、405 `METHOD_NOT_ALLOWED` |

**5.0 总裁办增量（R4，2026-11-11）**：新增 `GET /api/secretariat`（族入口）与
`/api/secretariat/{status,identities,identities/{id}/revoke,delegations,records/{id}/sign,verify,letters,letters/{id},letters/{id}/verify,seal}`。
契约抽取实测：**41 条 `(path, verb)` / 43 个常量 / 本表 34 行·36 条路径**，方法列一致、无 ⚠ 行；
`--check` 的唯一失败项是 `app.js` 里 **8 个非总裁办端点**（analytics / compliance / evidence /
groups / preferences / rollback / shares / suggestions），属后续批次范围，不在本节造假绿。

**代码与文档的差异记录**（**一律以代码为准**；文档旧写法已在本节作废并保留可追溯，便于队长复核"改了什么、为什么"）：

| # | 项 | 文档旧写法（已废） | 代码实况（以此为准） |
|---|---|---|---|
| 1 | 清单数据源 | `GET /api/actions`、`/api/actions/{id}` | `GET /api/records`、`/api/records/{id}`（R1/R2）；旧路径现状 404 |
| 2 | 干预错误码 | `NOT_PENDING`/`PARAMS_INVALID`/`REASON_REQUIRED`/`DECISION_INVALID`/`ACTION_NOT_FOUND` | `INTERVENE_STATE_INVALID`(409)/`INTERVENE_PARAM_*`(400)/`INTERVENE_REASON_REQUIRED`(400)/`INTERVENE_DECISION_INVALID`(400)/`INTERVENE_ACTION_NOT_FOUND`(404) |
| 3 | 上报响应形状 | `{accepted,rejected,dedupe,errors}` | `{ok,accepted,created,updated,duplicates,skipped,ids,duplicate_detail,stats{skip_reasons[:20]},token,stream,idempotency,replay,request_id,…}`；"去重"概念改名 `duplicates` |
| 4 | 幂等命中语义 | 409 `INGEST_DUPLICATE` | **200 重放** + `replay:true` + 头 `X-Ingest-Replay: true`；409 只用于 `INGEST_IDEMPOTENCY_CONFLICT`（同键换体） |
| 5 | 请求体上限 | 8 MB | **64 KB**（`MAX_BODY_BYTES`，干预/上报同口径）+ 单批 ≤200 事件（413 `INGEST_TOO_MANY_EVENTS`） |
| 6 | SSE 事件名 | `action.needs_decision`/`action.execution_receipt`/`action.overdue`/`ingest.rejected`/`settings.changed` + `STREAM_LIMIT` | 8 个真实事件：`hello`、`heartbeat`、`action.created`、`action.updated`、`action.decision_recorded`、`records.reset`、`ingest.batch`、`ingest.stats`；码为 `STREAM_ACCEPT_REQUIRED`/`STREAM_QUERY_INVALID`/`STREAM_LIMIT_REACHED` |
| 7 | 设置写入 | `PATCH /api/settings` + `{base_url,model,api_key_masked}`；码 `SETTING_READONLY`/`VALUE_OUT_OF_RANGE` | `PUT /api/settings`（三节可写字段 + 掩码视图）+ `POST /api/settings/test-llm`；码 `SETTINGS_*`；`PATCH` → 405 |
| 8 | 清空数据 | `POST /api/settings/purge` + `{removed,archive}` | 未实现；现状 `POST /api/reset`（追加 `record_reset`，审计文件不动）；审计侧无删除接口 |
| 9 | 健康检查 | `GET /api/health` + `{status,db,db_size_bytes,audit:"ok",uptime_s,ready:true}` | `GET /health`（别名 `/healthz`）+ `endpoints[16]` + 能力块（6.6）；**无 `ready`/`db` 字段**，Docker healthcheck 用"`/health` 200 且 `status=ok`" |
| 10 | 审计校验端点 | `GET /api/audit/verify` | 未实现；校验随 `GET /api/audit` 的 `chain` 块与 `/health` 的 `audit` 块返回；命令行 `python -m agent_explainer.audit` |
| 11 | CSV 导出端点 | `GET /api/export.csv` | **4.8 起审计导出已有 HTTP 端点 = M6 `GET /api/audit/export?format=csv`**（另有 `html` 可打印页 / `pdf` reportlab）；**I1 的 `/api/export.csv` 路径本身仍未实现**（GET → `STATIC_NOT_FOUND`），**记录**导出仍走 `GET /api/records`；Python API `audit.export_csv()/export_html()/export_pdf()` 保留给 CLI 与测试直用。4.11（t24）已把 6.3/6.4/6.5/§4/§13 的导出口径统一到 M6 |
| 12 | 环境变量前缀 | `TRACELNS_LLM_API_KEY/BASE_URL/MODEL`（少一个 L，拼错） | 代码前缀 `TRACELENS_`：`TRACELENS_LLM_API_KEY`、`TRACELENS_LLM_BASE_URL`、`TRACELENS_LLM_MODEL`、`TRACELENS_LLM_PROVIDER`、`TRACELENS_LLM_MODE`、`TRACELENS_LLM_TIMEOUT_S`、`TRACELENS_LLM_LANGUAGE`、`TRACELENS_LLM_ALLOW_OUTBOUND`、`TRACELENS_UI_LANGUAGE`；名单由 `GET /api/settings` 的 `security.env_names` 现报 |
| 13 | 密钥掩码格式 | `sk-ab…(len=35)` | `••••••` + 末 4 位（`MASK_MARK`/`MASK_TAIL`）+ `api_key_fingerprint = sha256 前 12 位` |
| 14 | 模块落点 | `settings/dpapi.py`、`ingest/*`、`server/sse.py`、`server/routers/intervene.py`、`store/queries.py`、`explanations/{facts,render,templates}`、`audit/*` | 实况是**单文件模块**：`settings.py`（含 DPAPI ctypes）、`ingest.py`（含 SSE hub + 令牌保管箱）、`server.py`（装配层 + `ActionStore`，无 `store.py`）、`audit.py`、`explanations.py`、`auth.py`、`trace_adapter.py`、`sdk/client.py`、`web/` |
| 15 | 审计事件名 | `settings.change`、`settings.llm_test` | 实况 `event="note"` + `settings_*` 字段；上报/令牌事件 `event="note"` + `extra.ingest_event`（`batch_accepted`/`token_issued`/`token_revoked`）；请求体明文不进审计 |
| 16 | 接口契约产物 | `agent_explainer/api_contract.json`（`tools/gen_api_contract.py` 生成）+ `tools/i18n_lint.py` | **4.7 已就位**：`tools/gen_api_contract.py` → **`docs/api_contract.json`**（落点从包内改到 `docs/`，理由：契约是"前后端 + QA + 文档"三方共用的核对物，不是运行时依赖；`--check` 已接入发布清单），`tools/i18n_lint.py` 已实现 5 项检查（字典一致 / 禁硬编码 / 错误码覆盖 / 生成物 embed.js+keys.md 同步 / 双语夹具）；契约来源另加 `web/app.js` 的 `API_ENDPOINTS` 与 `sdk/client.py` |
| 17 | i18n code 键命名 | 顶层 `error.<CODE>` | 实况 `<域>.error.<code 小写>`（`intervene.error.*` 31、`settings.error.*` 38、`auth.error.*` 33，共 102 条 code 键）；**码数与覆盖以 `tools/i18n_lint.py` 为准：155 码 / 75 有键 / 80 无键**（旧 128/85/43 是 t19 人工计数，覆盖面小，已被工具取代）→ 无键码必须 `message` 回退 |
| 18 | 性能基线引用的端点 | `GET /api/actions`（第 11 节表内） | 用 `GET /api/records`（首屏 1000 条 <1.5s 的测量对象就是它）；SSE 首帧延迟基线见 6.6 |
| 19 | 持久化落点 | 第 4 节：SQLite（WAL）六表 + `agent_explainer/store/migrations/NNNN_*.sql` 迁移 + `store.verify_columns` 自检 | 实况：**没有 SQLite、没有迁移脚本**。4.1→4.8 期间记录全在 `server.py` 的 `ActionStore`（内存 + 演示 JSON 只读装载），另有 `var/audit.jsonl`（哈希链）、`var/settings.json`、令牌保管箱文件。**4.9 / t22 起新增 `agent_explainer/store.py`**：追加式 JSONL 记录日志 + 内存索引（默认 `var/records.jsonl`，逐行重放 / 最后写入胜出 / 原子压实 / 跨进程锁与签名同步），选型理由见第 4 节"记录持久化"行。"数据只在本机/自建卷、默认不出网"的隐私承诺**仍然成立**（无外发路径）；`DB_LOCKED` 一类的实况码是 `RECORD_STORE_LOCKED` |
| 20 | 演示与夹具落点 | `samples/action_records_en.json` + `samples/traces/*`（≥3 类外部格式夹具，DM 8.4 规格） | **en 夹具 4.7 已交付**：`samples/action_records_en.json`（与中文集同 id / 同机器字段，文案由 `tools/gen_samples_en.py` 按 198 条英文对照**原生生成**，零 CJK、facts 指纹逐条复算、`--check` 保证可重复生成），装载为 `web/demo-data-en.js`（`node selfcheck.mjs --sync-demo` 的字节拷贝）。**`samples/traces/*` 仍未交付**：外部格式夹具只在 `tests/test_trace_adapter.py` 内，**5.1 实测套件不得声称 `samples/traces/*` 夹具已交付** |
| 21 | 演示数据的语言与字段标签兜底 | 旧写法：界面语言只切文案、演示数据是中文一份；`fieldLabel()` 兜底键 `compare.field.l1` | 4.7 实况：演示数据有**两份**（`demo-data.js` 中文 / `demo-data-en.js` 英文），`app.js` 按 `tracelens.dataLang`（无则跟随界面语言）选择装载，英文界面读英文集，**运行名称/详情不再出现中文数据**；运行名与 agent 名是字典键 `demo.label.<夹具原名>`（6 条，zh/en 各一份），所以 `app.js` 里 0 条 CJK 字面量；`fieldLabel()` 的兜底键改为双语都在的 **`compare.field.other`**，3.2 遗留的 `compare.field.l1` 已从字典删除且代码不再引用。selfcheck 断言：英文模式数据/卡片/运行名 CJK=0、中文模式确有中文、两份数据集 id 与机器字段一致、字段标签双语齐平（71 项全绿） |
| 22 | 导入入口与记录持久化（4.9 实况） | 第 4 节"SQLite 六表 + 迁移脚本"、6.3 的 **D1 `POST /api/traces`（⚠ 规划）**、S1 只写"清空记录"、第 5 节"粘贴/上传"没有对应端点 | 实况：用户可见入口 = **D3 `POST /api/import`**（粘贴文本与上传文件内容同一条路，`data.write` + CSRF，≤64 KB / ≤200 条，四种格式自动识别，`GET` → 405）；D1 仍是 ⚠ 未实现。记录持久化 = **`agent_explainer/store.py` 的追加式 JSONL 日志 + 内存索引**（默认 `var/records.jsonl`；跨进程 sidecar 锁 + 签名同步；启动加载；`--records-store/--no-records-store/--records-store-lock/--reset-demo`）。`POST /api/reset` = 清回演示种子 12 条 + 日志压实，**审计只追加**。`meta.demo_data`/`source.kind` 仍是演示徽标的唯一判据（导入的记录不是演示数据）。i18n 新增 `import` 域 73 键（zh/en 齐平）；`IMPORT_*`/`RECORD_STORE_*` 码已进 6.4 与 `import.error.*`；selfcheck 80 项（新增 5 项 4.9 断言） |

### 6.6 `/health` 字段说明（公开，无 PII；4.6 要求逐块写清）

`GET /health`（别名 `/healthz`）响应 = 一台服务器"现在能干什么"的自述，**不含任何记录内容、密钥、口令、路径穿越信息**。字段随 4.1→4.3→4.2b→4.4→4.5 逐块增加，**老字段口径不变**（前端与 Docker healthcheck 只依赖 `status`）。

| 字段 | 内容（代码实测） | 口径 / 为什么 |
|---|---|---|
| `name`、`version` | `"TraceLens"`、`SERVER_VERSION`（现 `0.5.0`） | 与界面页脚同源，支持沟通用 |
| `status` | `ok` / `degraded` | 断链、审计不可写等 → `degraded`（HTTP 仍 200，见下） |
| `mode` | `local` / `team` | 决定前端是否显示登录页；**不由前端猜** |
| `schema_version` | `"1.1"` | 与 `docs/schemas/action_record.schema.json` 一致；前端比对不一致时提示刷新 |
| `endpoints` | 18 条已注册路由的清单 | 让接入方**不用读源码**就知道能调什么；与 6.5 核对表同源 |
| `capabilities` | `read_records`、`decide`、`audit_read`（+ 接入/设置能力块各自表达） | 能力面而非角色面 |
| `auth` | `{required,mode,roles,bootstrap_needed,cookie,csrf_header,token_header,note}` | 明确"这台要不要登录"；`note` 写明"上报走 ingest 令牌，不是用户会话"（6.3 的例外） |
| `stats` | 记录数、演示/接入条数、`mode` 等计数 | 只计数，不内容 |
| `audit` | `{ok,count,head,durable,source,path,problems,sealed_head,sealed_count,seals}` | 断链 → `ok=false` 且 `status=degraded`，**健康检查本身仍 200**（进程可用 ≠ 审计可信） |
| `audit_export`（**4.8**） | `{endpoint:"/api/audit/export",method:"GET",formats:["csv","html","pdf"],default_format:"csv",permission:"audit.export",filters[8]:["actor_id","action_id","decision","event","task_id","since","until","q"],limit_default:500,limit_max:5000,served,note}` | 4.8 的**可验收面**：导出入口、要哪条能力、能出哪三种格式、默认格式与条数上限、累计已发出几次（`served`）现场可查；`note` 一句话写明"pdf 优先 reportlab；reportlab 不可用时退回可打印 HTML 并说明原因，浏览器 Ctrl+P 可存 PDF。每次导出先记账（`event=export`）"。**这里没有 `redact` 开关**：脱敏在写盘前强制（第 13 节），健康检查与导出响应都不带原文与密钥 |
| `requests` | 累计请求数、拒绝数、按状态码计数 | 现场排障："有没有打到这台机器" |
| `interventions` | 决定计数（按决策类型）+ 幂等重放计数 | 与 4.1 闭环对账 |
| `llm` | `{mode,provider,model,language,will_run,engine,note}` | 说清"这台会不会出网、跑的是模板还是模型"（K-12 的可验证面） |
| `ingest` | `{enabled,version,mode,endpoints[4],auth{token_header,bearer_prefix,required,scopes,manage_permission},tokens,counters{batches,accepted,created,updated,duplicates,skipped,rejected,replays,rate_limited,sdk_events,webhook_events},rate{capacity,per_minute},limits{max_events_per_request,max_envelope_bytes},idempotency{supported,headers:["Idempotency-Key"],body_field,ttl_seconds},accepted_formats,merge_fields,protected_fields,audit{event,marker},note}` | 4.4 的**可验收面**：接入开没开、鉴权口径（`required = mode=="team"`）、限流与幂等参数、能收哪些格式、哪些字段会被合并/保护，全部现场可查；`note` 一句话写明"team 模式的上报必须带 ingest 令牌…local 模式匿名放行并如实标 anonymous"，**主动披露**例外 |
| `stream` | `{endpoint,content_type,event_types[8],heartbeat_seconds,replay{supported,last_event_id_header,query,ring_size},fallback{mode:"polling",endpoint:"/api/records",interval_ms:4000}}` | 4.4 的降级承诺是**服务端声明**，不是前端自说自话：断线补发（环形 200 帧）+ 降级轮询 `/api/records`（4000ms） |
| `settings` | `settings.stats()`：`{reads,writes,probes,revision,mode,llm_mode,provider,model,language,api_key_present,api_key_source,outbound,key_storage,updated_at,updated_by,note}` | 4.5 的能力面；`note = "统计与视图都不含密钥明文"`，`api_key_present` 只说有无，**永不**带明文/掩码/指纹 |
| `import`（**4.9**） | `{endpoint:"/api/import",method:"POST",permission:"data.write",formats[4],auto_detect:true,modes[3],default_mode:"upsert",max_bytes:65536,max_records:200,source_kinds:["paste_json","upload"],body_fields[9],imports,imported_records,note}` | 4.9 的**可验收面**：导入入口在哪、要什么权限、认哪四种格式、上限多少（64 KB / 200 条）、累计导入了几批几条，现场可查；`note` 一句话写明"同 id 默认更新事实字段，用户决定与执行回执是受保护字段；审计只记格式与统计，导入正文一个字都不进" |
| `records_store`（**4.9**） | `{durable,mode:"jsonl"\|"memory",path,store_version,journal{path,exists,store_version,appends,compactions,loaded_records,load_problems,bytes,lines},problems,problem_preview[:20],write_errors,loaded{existed,lines,records,put_lines,seeded,bytes,problems,problem_count}}` | "重启后还在不在"的自述：`durable=false` 就是纯内存（`--no-records-store`），界面必须显示"这台服务不落盘"；`problems`/`write_errors` 非零 → 日志有坏行或写不进去（**不静默修、不静默成功**）；`/api/records` 的 `meta.records_store` 与 `meta.imported_count`、`meta.records_durable` 同源 |

**给 t17（Docker）的硬口径**：healthcheck 用 `GET /health` 200 + `status:"ok"`（**没有** `ready` 字段，也**没有** `/api/health` 路径）；`--no-ingest`、`--no-reset`、`--no-bootstrap` 都会如实反映在 `ingest.enabled`、`settings`、`auth.bootstrap_needed` 上，验收时按这些字段判读，不要靠"看起来起来了"。

---

## 7. 国际化（i18n）— **双语是 Must，不是 Should**（队长已确认在 MVP 内）

| 项 | 基线 |
|---|---|
| 字典文件 | `web/i18n/zh.json`、`web/i18n/en.json`；**同一 key 集，双向覆盖率 100%**（K-10：en 界面中文残留 **0 条**）。实况（**4.9 / t22 后**，`tools/i18n_lint.py` 现场给出）：**zh 689 键 / en 689 键，21 个域逐域齐平**（域清单以 lint 输出为准；4.9 新增 **`import` 域 73 键** = 导入面板标题/提示/粘贴框/文件选择/四种格式名/三种 mode 及说明/上限与状态/结果摘要/校验与错误文案，zh 含汉字、en 全 ASCII、参数集一致）；4.7 的 `demo.label.<夹具原名>` 6 条与 `compare.field.other` 兜底键口径不变，3.2 遗留的 `compare.field.l1` 已删除 |
| key 命名 | `<域].[对象].[动作\|状态\|字段]` 全小写点分。**实况 20 个域**：`nav zones card intervene settings audit auth desk realtime compare demo timeline export error role status action_type risk level time`（4.3 新增 `auth`+`desk`、4.4 新增 `realtime`、4.5 扩 `settings`）。例：`intervene.button.approve`、`intervene.effect.recorded_only`、`card.status.expired`、`realtime.mode.poll`、`settings.section.llm` |
| 错误码文案 | **实况命名是 `<域>.error.<code 小写>`**（不是顶层 `error.<CODE>`）：`intervene.error.*` 31 条、`settings.error.*` 38 条、`auth.error.*` 33 条、**4.9 新增 `import.error.*` 27 条**（`IMPORT_*` 6 个入口码 + `RECORD_STORE_*` 7 个存储码 + 鉴权/网络/超时/HTTP 状态等 14 个通用码，前端 `IMPORT_ERROR_KEYS` 一一对应）；`realtime.*` 8 条覆盖降级原因。**码数与覆盖以 `python tools/i18n_lint.py` 现场口径为准**（现 164 码 / 75 有键 / 89 无键走 `message` 回退；`IMPORT_*`/`RECORD_STORE_*` 落在 INFO 的无键清单，因为 lint 的 `ERROR_DOMAINS` 不含 `import` 域 —— 属**已披露**的 lint 口径，不是缺文案：`import.error.*` 27 键确实存在，selfcheck 第 79 项逐码验"字典有键 + 服务端真有这个码"） |
| 全量 key 清单 | `web/i18n/keys.md`（**已存在**，与两份字典同源维护）；新增文案必须先加 key，再在代码引用。`web/i18n/embed.js` 是演示/离线注入用的字典装载器 |
| 语言切换 | 顶部语言按钮即时切换（**不刷新页面**）；持久化两处：浏览器 `localStorage["tracelens.ui_language"]`（键名由服务端常量 `UI_LANGUAGE_STORAGE_KEY` 给出，`GET /api/settings` 的 `ui_language_storage_key` 字段现报，**前端不要自己拼字符串**）+ 服务端 `PUT /api/settings {"ui":{"language":"en"}}`（团队模式跟账号走，local 跟本机走；`settings.ui.language ∈ {zh,en}`，越界 400 `SETTINGS_LANGUAGE_INVALID`）→ K-10 切换 100% 持久化 |
| 数据层双语 | ActionRecord 的 `human_readable.language` 必填；翻译层为 zh/en 各一套**原生模板**（不是机翻，A9/A10）；`actions.l1_zh/l1_en` 平铺列，切换零重算。**4.4 实况**：上报合并事实后由接入层按 `language` **重算** `human_readable`（同一套模板），所以接入侧的语言与界面语言一致 |
| 数字/日期 | 日期时间显示跟随浏览器 locale + 原始时区偏移；金额带货币码；不使用英文缩写日期 |
| **lint 规则（禁止硬编码）** | 规则 ①-⑤ 已实现，闸门 = `python tools/i18n_lint.py`（退出码非 0 阻断）：① `app.js` / `index.html` / `styles.css` 出现 CJK 字面量或"句子形状"的英文字面量 → 失败（白名单：`*.md`、`tests/`、生成物 `embed.js`/`keys.md`/`selfcheck-report.json`/`demo-data*.js`/`compare-data.js`/`selfcheck.mjs`，以及 3 条只在 boot 诊断里出现的探针文案）；② 两份字典键集双向不一致 → 失败（并查重复键、空值、占位符、`en` 未翻译 = 与 zh 同串、`{参数}` 集合不一致、**en 值含 CJK**）；③ 代码引用的 key 不在字典 → 失败（`app.js` 的 `*_ERROR_KEYS` 映射同样逐条查键）；④ 字典里的 key 未被引用 → 由 `web/selfcheck.mjs` 的引用扩展（枚举 + 前缀 + 4.7 的 `demo.label.` 夹具扩展）判定，孤儿键即失败；⑤ 模板长度上限 → 由 `tools/validate_schema.py` 的 A9 上限（L1≤40/80、L2 每步≤30、L3≤160）与 `tools/gen_samples_en.py` 的英文长度自检覆盖。另有两项 4.7 追加：生成物 `embed.js` / `keys.md` 与字典漂移 → 失败；双语演示夹具（id/结构/机器字段一致、en 零 CJK、zh 确为中文、facts 指纹复算、`copy_of` 与 `record_count`）→ 失败 |
| 集成 | 发布清单（第 17 节 CI 步骤）里的 i18n/契约闸门实到即用：`python tools/i18n_lint.py`（5 项检查，退出码即闸门；`--strict-codes` 可选）、`python tools/gen_api_contract.py --check`（契约与代码、docs/03 §6.5 三方一致）、`node web/selfcheck.mjs`（**80 项**：4.7 的双语数据集断言 + 4.9 的 5 项导入面板断言）、`python tools/validate_schema.py`（50 项夹具校验）、`python tools/doc_lint.py docs/*.md`。`tests/run_all.py` 第一步跑 i18n lint 的规划不变（待 5.1 接入，避免 QA 侧改测试装配顺序时与本轮冲突） |
| 演示数据 | 双语演示集 **4.7 已到位**：`samples/action_records.json`（zh，12 条）+ `samples/action_records_en.json`（en，12 条**原生英文**，由 `tools/gen_samples_en.py` 生成，同 id / 同机器字段 / 文案语言不同，零 CJK，指纹复算）；装载层 `web/demo-data.js` + `web/demo-data-en.js` 都是对应夹具的**字节拷贝**（`node selfcheck.mjs --sync-demo` 一次生成两份），`app.js` 按 `localStorage["tracelens.dataLang"]`（缺省时跟随界面语言）选数据集，切换语言即换数据集并重绘一次。**英文界面下的运行名、卡片详情、对照表标签均无中文数据**（selfcheck 断言 CJK=0），中文界面保持中文数据；对照运行 B 的中文运行名经 `demo.label.*` 出英文文案，无需第二份对照夹具。`samples/traces/*` 外部格式夹具仍未交付（在 `tests/test_trace_adapter.py` 内） |

---

## 8. 模型配置与密钥

| 项 | 基线 |
|---|---|
| 协议 | **OpenAI 兼容**：`POST <base_url>/v1/chat/completions`（代码常量 `CHAT_COMPLETIONS_PATH`，`base_url` 带不带 `/v1` 都能归一），`Authorization: Bearer <key>`；`provider ∈ {openai_compat,ollama,deepseek,custom}` 只决定默认端点与提示写法；模型名可填（≤120 字，界面提示"填你供应商的模型名"） |
| 默认 | `llm.mode="template"`（**模板模式全功能，无 key、无网络不崩溃**，K-12/A7）；LLM 只允许改措辞，不得新增事实（`source_facts_hash` 校验 + `may_contain_inference=true` 标记）。实况：`GET /api/settings` 的 `engine` 块回显引擎**此刻**真在跑什么（`mode/model/language/llm_will_run/effect_notice`），界面照此显示，不自己猜 |
| 桌面密钥 | **Windows DPAPI**（当前用户范围）加密：`ctypes` 调 `crypt32.CryptProtectData` / `CryptUnprotectData`，`CRYPTPROTECT_UI_FORBIDDEN`；**实况落点 = `agent_explainer/settings.py` 内**（t15 未拆 `settings/dpapi.py`，见 6.5 差异 14）；密文以 `base64` 存 `var/settings.json` 的 `llm.api_key_blob`，`api_key_source="dpapi"`；**只有同机同用户可解**（拷贝数据目录到别的机器 → 提示"密钥不可用，请重填"）；**非 Windows（开发机/Docker）不存盘**：只驻内存（`api_key_source="memory"`，重启即无），**禁止退化成明文存储** |
| 团队密钥 | **只从环境变量读**（前缀常量 `ENV_PREFIX="TRACELENS_"`，注意旧文档拼成 `TRACELNS_` 少一个 L，已作废）：`TRACELENS_LLM_API_KEY`、`TRACELENS_LLM_BASE_URL`、`TRACELENS_LLM_MODEL`、`TRACELENS_LLM_PROVIDER`、`TRACELENS_LLM_MODE`、`TRACELENS_LLM_TIMEOUT_S`、`TRACELENS_LLM_LANGUAGE`、`TRACELENS_LLM_ALLOW_OUTBOUND`、`TRACELENS_UI_LANGUAGE`。优先级 **env > 配置文件**，此时 `api_key_source="env"`、`settings.writable=false`（界面显示"这台服务器的密钥由环境变量管理，改不了"，写入被拒），明文**不落盘**。名单由 `GET /api/settings` 的 `security.env_names` 现场报出，运维不用读源码 |
| 回显 | 任何接口/界面只回显掩码：`api_key_mask = "••••••" + 末 4 位`（常量 `MASK_MARK`/`MASK_TAIL`）+ `api_key_fingerprint = sha256 前 12 位` + `set_at`（旧格式 `sk-ab…(len=35)` 作废）。写入哨兵：字段不出现 / `null` / `"keep"` ＝**不修改**；`""` ＝清除。PUT 响应只回 `key_action ∈ {set,same,cleared,unchanged}`，**不回任何凭据** |
| 禁止项 | 明文入库、明文入日志、明文入审计、明文入导出、明文入错误体 `detail`；禁止把 key 写进崩溃报告。实况护栏：`server.py` 被源码级测试**禁止拼出密钥字段的蛇形名**（只把请求转交 `settings.py` 加密），日志与错误体统一 `redact()` 打码 |
| 调用护栏 | 实况（t15）：`llm.timeout_s` 可配 **0.5–120**（越界 400 `SETTINGS_FIELD_RANGE`）；`temperature 0.0–2.0`、`max_tokens 40–4000`；`allow_outbound` **默认 `false`** → 只允许回环地址（Ollama/vLLM/llama.cpp 本机跑），非回环主机必须 `https` 且显式打开开关，否则 403 `LLM_OUTBOUND_BLOCKED`；`base_url` 校验 7 码（`SETTINGS_URL_MALFORMED/SCHEME/QUERY/FRAGMENT/USERINFO/PORT/INSECURE`）；失败 → 自动回退模板模式并显示降级徽标（不弹长错误，K-12）；请求体只包含必要上下文（L1 摘要 + 任务名 + 输出语言），**不包含**完整 trace 原文与参数明文 |
| `test-llm` | 发一条固定探测请求（**probe-only**：固定探测语要求模型"不要编造动作结果、不要说已完成"）。**只接受三个参数** `{prompt?(≤2000 字),max_tokens?(40–4000),timeout_s?(0.5–120)}`，多带字段 400 `SETTINGS_FIELD_UNKNOWN`，**请求体不接受密钥/地址**（否则日志与审计会带上凭据、权限边界形同虚设）。返回 `{ok,code,message,latency_ms,model,chars,sample,endpoint,config,detail,note,sent_facts_note,effect_notice:"settings.test_llm.probe_only"}`（失败也是 200 + `ok:false` + 人话 `message`）。码：`LLM_OK/MODE_TEMPLATE/NOT_CONFIGURED/KEY_MISSING/KEY_REJECTED/UNREACHABLE/TIMEOUT/HTTP_ERROR/PATH_NOT_FOUND/RATE_LIMITED/CLIENT_ERROR/EMPTY_ANSWER/TRANSPORT_MISCONFIGURED`（旧名 `LLM_AUTH_FAILED` 未使用 → 实际是 `LLM_KEY_REJECTED`）；探测默认 `PROBE_TIMEOUT_S=8.0`、`PROBE_MAX_TOKENS=64`；响应不含 key |
| 审计 | 实况：设置变更以 `event="note"` 追加记账，字段名带 `settings_*`（只记**字段名与掩码**，不记明文），`test-llm` 只累计 `settings.stats().probes`；**旧事件名 `settings.change` / `settings.llm_test` 未使用**（见 6.5 差异 15）。审计链算法一字未改（t9/t18 口径） |
| 命令行 | `python -m agent_explainer.settings [--settings PATH] [--mode local\|team] [--show] [--json] [--set llm.mode=llm]… [--test-llm]`：`--set` 只能改 `WRITABLE_FIELDS` 里的字段（密钥请用环境变量注入），输出里密钥只有掩码；`--test-llm` 默认只连本机 |

DPAPI 调用要点（实况在 `agent_explainer/settings.py` 内，纯标准库 `ctypes`；拆成 `settings/dpapi.py` 属后续任务）：

```python
crypt32.CryptProtectData.argtypes = [POINTER(BLOB), c_wchar_p, POINTER(BLOB),
                                     c_void_p, POINTER(BLOB), DWORD, POINTER(BLOB)]
flags = CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE  # 默认只用 UI_FORBIDDEN
# 非 Windows（开发机/Docker）→ 明确报"该环境不支持 DPAPI"，密钥退回环境变量，禁止退化为明文存储
```

---

## 9. 依赖政策（决定"能不能离线跑起来"）

| 层 | 政策 |
|---|---|
| **核心运行时** | **纯标准库**：`json sqlite3 http.server wsgiref urllib socket ssl threading hashlib hmac secrets base64 ctypes datetime pathlib argparse logging zipfile`。无 pip 依赖 = 便携版/Docker/离线机器都能跑 |
| **Web 层可选 FastAPI** | 允许 `fastapi`+`uvicorn` 作为**可选加速层**（`app_fastapi.py`），标准库 `app_stdlib.py` 是默认实现。若启用：必须**打进安装包与镜像**（PyInstaller `--collect-all fastapi`；Dockerfile `pip install` 后 `pip download` 缓存留档），**离线可运行**，且路由行为与 `app_stdlib.py` 完全一致（`tests/integration/test_two_servers_parity.py` 用同一份 `api_contract.json` 跑双服务对拍）。**现状（t19）**：只有标准库单实现 `agent_explainer/server.py`（无 `app_stdlib.py`/`app_fastapi.py` 文件拆分，**`fastapi` 未引入依赖**，`server.py` 全文 `http.server` 手写路由）；"核心纯标准库、离线可跑"已实测（`tools/test_offline_engine.py`），双服务对拍与 FastAPI 层属**未启动的规划**（`api_contract.json` 也未生成，见 6.5 差异 16） |
| **构建期依赖（不进运行时）** | `electron`、`electron-builder`、`pyinstaller`、`pytest`、`jsonschema`（仅测试）、`node`。发布包中**不得**出现这些 |
| **前端** | 零运行时依赖、无 CDN、`web/vendor/` 全本地；构建期允许 `esbuild` 做最小打包（可离线，产物入 `web/dist/`） |
| **Python 版本** | 兼容 **3.12+**；本机已验证 **3.14.7**（本任务校验脚本在 3.14.7 实跑通过）。禁止使用 3.12 已弃用 API（`datetime.utcnow` 等）；时区一律 `datetime.now(timezone.utc)` |
| **Node 版本** | Electron 主/构建脚本用 Node 20 LTS+；**包管理只用 npm**（`package-lock.json` 入库），**禁止 pnpm**（发起人环境无 pnpm） |
| **离线自测** | `tools/test_offline_engine.py` 屏蔽第三方库跑通全部校验（已实跑：50/50 通过）；`tests/integration/test_no_network.py` 断无网场景（模板模式全流程可用） |
| **许可证扫描** | 第三方（含可选 FastAPI）均为 MIT/BSD/Apache-2.0；构建脚本输出 `docs/NOTICE-third-party.md`（产品本体为 Apache-2.0，见第 15 节） |

---

## 10. 打包与发布

| 步骤 | 内容 | 产物 |
|---|---|---|
| 1. 版本 | 单一版本源 `agent_explainer/__init__.py:__version__`；SemVer `MAJOR.MINOR.PATCH`；构建脚本注入到 Electron/镜像标签 | — |
| 2. Sidecar | `python tools/build_sidecar.py`（PyInstaller，脚本内生成入口 spec/参数，含 `agent_explainer` 全部包 + `web/` + `samples/` + `prompts/`）→ 落 `desktop/build/resources/sidecar/`；启动冒烟：跑 `--print-argv --port 8899` 要求输出含该端口才算通过；`BUILD-INFO.json` 记 layout/字节/sha256/冒烟结果 | `agent-explainer-sidecar.exe`（**单目录 onedir 为本机交付布局**；普通机器 `--build` 不带 `--onedir` 即单文件） |
| 3. 前端 | 产品单页应用 `agent_explainer/web/`（零运行时依赖、零构建）由 sidecar 静态托管 | 静态资源 |
| 4. 桌面构建 | `cd desktop && npm install && npm run dist`（**npm**，非 pnpm）→ `electron-builder`（`desktop/electron-builder.yml`）targets：`nsis` + `portable`；`extraResources` 把 `build/resources/sidecar` 打成 `resources/sidecar`；`forceCodeSigning: false`（构建期不签名，缺签名也照常出包，不为"看起来已签名"造假） | `TraceLens-Setup-<ver>-x64.exe`、`TraceLens-Portable-<ver>-x64.zip` |
| 5. 签名 | `tools/sign_and_hash.ps1`（第 16 节自签名口径）→ 构建**之后**对安装包/便携版签名、导出 `.cer`、写 `SIGNATURE-STATUS.json`（证书信息 + 每个产物的 `Get-AuthenticodeSignature` 状态 + `trust_note`）；造不出证书时如实写 `status=NotSigned`、`unsigned=true` 并退出码 1 | 签名产物 + 证书 + 状态报告 |
| 6. 哈希 | 同一脚本写 `desktop/build/dist/SHA256SUMS.txt`（coreutils 两空格格式）；`-Verify` 逐条重算并复核签名状态；测试侧用 `hashlib` 独立复算 | 校验文件 |
| 7. Docker | `docker build -t tracelens:<ver> docker/`；`docker save` 出离线 tar；`docker-compose.yml` 配端口/卷/`env_file` | 镜像 + compose |
| 8. 发布包 | `release/<ver>/` = 安装包 + 便携版 zip + 镜像 tar + `SHA256SUMS.txt` + `tracelens-code-signing.cer` + `docs/05-real-user-kit.md` + `CHANGELOG.md` 片段；再打成 `tracelens-<ver>-release.zip` | 交付包（t12/t16 使用） |
| 9. CI | `tools/ci.ps1`（脚本由 t12/t16 落地）：**现在就能跑的四道闸门** = `python tools/validate_schema.py`（50 项夹具）→ `python tools/i18n_lint.py`（字典/硬编码/错误码/生成物/双语夹具，4.7）→ `python tools/gen_api_contract.py --check`（契约与代码、6.5 三方一致，4.7）→ `node agent_explainer/web/selfcheck.mjs`（**80 项**）→ `python tools/doc_lint.py docs/*.md`；其后 `python tests/run_all.py` → `npm run dist` → `docker build` → `sign_and_hash`。**每一步真实执行**，失败即红，不许跳过（阶段 0 闸门） | 全绿构建 + 各步退出码日志 |
| 10. 变更日志 | `CHANGELOG.md`（Keep a Changelog 格式）每个 `vX.Y.Z` 列出新增/变更/修复/已知限制；打包脚本要求版本号与 CHANGELOG 条目一致，否则构建失败 | `CHANGELOG.md` 条目 |

**本机实测状态（5.3 / t12 + 5.3b / t26 复核；逐项证据与未产出原因见 [05.3 发布清单](./05.3-release-checklist.md) §9）**：
入口 CLI、sidecar 协议、端到端链路（28 条 `tests/test_e2e.py`，0 skip）、PyInstaller onedir exe、
便携源码包、`SHA256SUMS.txt` 全部通过。t26 把 exe 验收钉到"真实安装场景"：
`--data-dir` 指向**不存在的子目录**，要求 exe 自己创建并落下 `records.jsonl`；
`--sidecar --port 0 --demo zh` 实测 `/health` 200、`/api/records` 12 条、审计链 `ok`、
`/` `/app.js` `/styles.css` 均 200（onedir 重建后 10,155,966 字节，sha256 `22f78162…`，见 `BUILD-INFO.json`）。
**未产出**：单文件 exe（构建成功 38,974,999 字节，引导阶段 `Failed to create parent directory structure`；
`--runtime-tmpdir` 三种取值——默认 TEMP、工作区含中文路径、纯 ASCII 可写目录——全部同一报错，
onedir 是交付布局）、Electron 开发模式窗口与 NSIS/portable 安装包、代码签名。
沙箱约束实测（t26）：进程完整性级别 `Low Mandatory Level (S-1-16-4096)`；
`%TEMP%` 根与盘根写入 `PermissionError [WinError 5]`；命名管道禁开（→ Electron/crashpad 起不来，
GUI 0.1 秒退出 `0x80000003` / `0xffff7003`）；`icacls /reset` 被拒 → 继承的沙箱 ACL 去不掉。
electron-builder 在 t26 推进到第 3 层：`npmRebuild: false` 解掉 node-gyp 的 `fork EPERM`，
`--config.electronDownload.cache=<可写目录>` 解掉 AppData 缓存 `mkdir EPERM`，
最后卡在 `NpmNodeModulesCollector` spawn `npm query` 的 `EPERM`（26.18 无 `nodeModules` 配置键可绕）。
完整 `npm install` 在 t26 已 **exit 0**（npm 11 只警告生命周期脚本未跑）。
跑不起来的单文件 exe 已从 `build/resources/sidecar/` 移出，避免被 `extraResources` 打进安装包。
沙箱里 `pip install --target` 被拒，脚本内置标准库 wheel 下载兜底（`--vendor`）。
桌面壳的启动顺序（单实例锁 → sidecar → bootstrap 行 → `/health` → 加载 UI → 退出清理）
由 `DesktopShellTests` 静态契约 + sidecar 真跑共同覆盖。

---

## 11. 性能基线与测量方法

| 基线（t1 6.5 + 任务要求） | 目标 | 测量口径（t10/t12 必须实现） |
|---|---|---|
| 首屏（1000 条动作） | **<1.5 s** | 从 sidecar 就绪（探测 `GET /health`，现状无 `/api/health`）→ UI 三区首帧（`performance.now()` + Playwright `waitForSelector`）；连续 3 次取 P95。sidecar 由 t12 打包，现状由 `python -m agent_explainer.server` 直跑 |
| 筛选响应 | **<200 ms** | `GET /api/records`（**现状没有 `/api/actions`，也没有服务端筛选参数**：三区分类/搜索在前端做，测服务端 `duration_ms` 用 `/api/records` 与 `POST /api/ingest/events` 的响应耗时；`tools/perf_probe.py` 待 t10） |
| SSE 端到端 | **<1 s** | 客户端 `POST /api/ingest/events` 返回 → 浏览器收到 `action.created` 的时间差 P95 |
| 干预写入 | <150 ms + SSE 广播 | `/intervene` 服务端耗时 + `action.decision_recorded` 到达 |
| 审计校验 | 10 万条 <20 s | `python -m agent_explainer.audit verify --bench` |
| CSV 导出 | **≤30 s** | `curl -o` 计时（K-09） |
| 数据规模假设 | 单任务 ≤2000 条、团队 ≤30 人（A11） | 压测夹具 `tests/perf/dataset_2000.json` 由 `tools/gen_perf_dataset.py` 生成 |
| 测量脚本 | `tools/perf_probe.py`（起服务 → 灌 1000 条 → 打三个指标 → 打印 P50/P95 与 pass/fail），退出码非 0 表示基线不达标 | 结果写 `docs/perf-report.json` |

支撑手段：平铺列 + 6.3 索引 + 单一写者队列 + 首屏只取摘要列 + 演示数据后台加载。超量时先复审索引/分页（A11 复审点），不换数据库。

---

## 12. 支持矩阵

| 维度 | 支持 | 不支持/降级 |
|---|---|---|
| 桌面 OS | **Windows 10（1809+）x64、Windows 11 x64** | Windows ARM64（可运行在模拟层，不承诺）；macOS/Linux 桌面（仅开发者模式 `python -m agent_explainer.server`） |
| 桌面运行时 | Electron（随包分发，用户无需安装 Node/Python） | 32 位系统 |
| 团队 Web 浏览器 | **Chrome 最新版、Edge 最新版**（桌面 + 移动浏览器响应式） | Safari/Firefox 尽力而为（不在验收范围）；IE 不支持 |
| 视口 | ≥1024px 桌面三区 + 侧边栏 + 时间轴；≤480px 悬浮球 + 底部面板 | 640–1024px 平板布局 v1.x |
| Python | 3.12 / 3.13 / **3.14.7（本机实测）** | 3.11 及以下 |
| Docker | Docker Desktop（Windows，WSL2 后端）或任意 Linux Docker Engine 24+ | rootless 特殊环境未验证 |
| 可访问性 | 键盘可达（tabindex/aria-label）、颜色不作为唯一信息载体、对比度 AA | 读屏软件完整支持在 C5（v1.x） |

---

## 13. 隐私与保留（数据不出用户环境）

| 主题 | 基线 |
|---|---|
| 数据去向 | 本机（安装版 `%APPDATA%/TraceLens`、便携版 `<exe>/data`）或自建卷 `/data`。**产品没有任何云端**；唯一出网 = 用户自配 LLM 的 `base_url`（启用时在界面显式提示会发送哪些内容） |
| 一键清空 | **现状 = `POST /api/reset`**（scope 只有"记录"这一层：清空演示/接入记录并追加 `record_reset`；`settings.data.reset_enabled` 控制入口，`--no-reset` 直接 403）。规划中的 `POST /api/settings/purge`（scope: actions/audit/all，先归档 `.purged-<ts>.tar.gz` 再删，写审计 `data.purge`）**未实现**；**审计链侧永远不给删除接口**（append-only 是产品承诺，"清空数据"不等于"清空审计"——界面与 README 必须把这句话写出来）。便携版可整目录删除 |
| 导出脱敏 | **实况（4.8 / M6）**：脱敏**发生在写盘前、不可关闭**——没有 `?redact=1` 这个参数（目标契约里的可选开关**未实现**，实况是**隐私默认打开**）：`audit.py` 的 13 类 `PII_PATTERNS` + `redact_text()` 把邮箱/手机号/身份证/长数字/IPv4/IPv6/绝对路径/Bearer/`key=value` 密钥/`sk-` 密钥/AWS key/PEM 块替换成 `<redacted:类型#sha256:前12位>`（**同值同指纹**，可对齐取证；幂等）。三种格式（CSV / 可打印 HTML / PDF）与 `GET /api/audit` 输出的是**同一份脱敏后内容**，导出层不复制原文；导出响应 `Cache-Control: no-store`（证据不落浏览器缓存）。**尚未闭合**：目标要求的"导出页脚在含演示数据时**强制**写'合成演示数据 · 非真实采集'"在**审计导出 HTML/PDF 里还没有页脚实现**（现状演示标注在记录侧：`demo_data` 标记 + `/api/records` 统计的 `demo_data`/`synthetic` 计数 + 前端卡片徽标）→ 已列入 5.1 之后的待办，**QA 不得宣称该页脚已交付** |
| 导出记账（4.8） | 每次成功导出**先记账再交文件**：追加 `event=export`（内存链里叫 `audit.export`），字段只有 `export_format`/`requested_format`/`export_rows`/`export_bytes`/`export_sha256`/`export_filters`/`export_fallback`/`audit_file="appended_only"`——**不含理由原文、参数原文与密钥**；记账写不进去就 500，绝不出现"导出了没留痕"。`X-TraceLens-Export-Sha256` 让接收方证明拿到手的内容就是当时那份（PDF 按 latin-1 逐字节算指纹，与 `audit.export_pdf` 同口径） |
| 日志脱敏（硬规则） | 日志只允许 `request_id / code / 耗时 / 计数 / ID`。**禁止**出现 trace 原文、参数明文、API key、口令、cookie、完整 UA、真实文件路径 → `store/logging/redact.py` 统一过滤 + lint 测试（第 6.4 节 lint 自检 5 坏 4 好） |
| 审计内容 | 决定与理由必填，参数只写**脱敏摘要 + 指纹**；IP/UA 只写加盐哈希。**4.8 起补一层硬脱敏**：`reason`/`note`/`changed_fields`/参数在**写盘前**过 `redact_text()`（13 类 PII → `<redacted:类型#sha256:前12位>`，同值同指纹、幂等），所以"落盘文件 = 列表接口 = 导出文件"三处内容一致，谁拿文件都拿不到邮箱/手机号/身份证/密钥/绝对路径。**4.9 的导入同理**：审计只记格式 / 条数 / 来源 / 文件名 / 统计（`fields.import_body = "not_recorded"`），**粘贴与上传的原文一个字都不进审计**（原文里常见邮箱、密钥、内部路径），记录本体只落 `var/records.jsonl`（第 4 节） |
| 演示数据 | 全部合成（A1/A2）：无真实人名、真实路径、真实金额、真实客户数据；界面常驻徽标，schema 字段 `demo_data`/`synthetic` 机器可校验 |
| 保留期 | 默认永久（Q4）；`retention.days` 可开自动清理；团队模式文档写明卷权限与备份责任在用户侧 |
| 真实样本策略 | **发起人确认没有 Langfuse/OTel/MCP 真实样本，也不会提供**（队长裁决）：所有演示与夹具自造合成，`synthetic=true`；验收**不依赖**真实样本，也不得向发起人索要（A1 的处置见 t1 第 8 节） |
| 团队安全 | 见第 14 节 |

---

## 14. 团队模式安全

| 项 | 基线 |
|---|---|
| 会话 Cookie | `tl_session`：**HttpOnly**、**SameSite=Lax**、`Path=/`、`Secure`（经反代 HTTPS 时）、`Max-Age=12h` 滑动续期；服务端存会话表（支持吊销） |
| CSRF | 双提交 token：登录返回 `csrf_token` 存 `localStorage`，所有写操作带 `X-CSRF-Token`；服务端校验不匹配 → `403 CSRF_FAILED`；`/api/ingest/events` 用**独立令牌**（`X-TraceLens-Ingest-Token`）豁免 CSRF |
| 口令 | PBKDF2-HMAC-SHA256，≥210k 迭代，16B 随机盐，参数存 `pwk_params`；首次登录强制改密（`must_change_pw`） |
| 本地状态文件放在哪（4.3 现状，队长裁决 t18 第 5 条） | team 模式的用户/会话状态在数据目录 `var/auth/`（`--auth-state` 可改）；**local 模式（免登录）故意不用 `var/`**，状态写在系统临时目录 `tempfile.gettempdir()/tracelens/local-auth/`。理由：本地模式没有真实用户表，却必须有一把会话签名密钥；把带密钥的状态文件写进仓库会污染工作区、极易被 `git add .` 提交，**签名密钥绝不进仓库**是硬约束（第 13 节隐私、第 15 节仓库纪律）。`tempfile` 因此在 `server.py` 的"只用标准库"白名单里（`tests/test_intervene_api.py` 的导入纯度检查） |
| 限速 | 登录：同用户名 5 次失败 → 锁定 15 分钟（`locked_until`）+ 审计 `auth.login_failed`；写接口令牌桶 20 rps/actor |
| 传输 | **HTTPS 由用户的反向代理负责**（A8）：文档 `docker/README.md` 提供 nginx/Caddy 示例（含 `X-Forwarded-Proto`、`X-Forwarded-For` 处理与 `client_max_body_size`）；应用检测 `X-Forwarded-Proto=https` 时才给 cookie 加 `Secure` |
| 绑定纪律 | 容器 `0.0.0.0:8000` 仅在内网/反代后暴露；README **明令禁止**把 HTTP 直接暴露公网；启动时若 `--mode team` 且非回环绑定且无 `TRACELENS_TRUSTED_PROXY`（**前缀是 `TRACELENS_`，旧文档 `TRACELNS_` 拼错作废**，见 6.5 差异 12）→ 日志显著警告。**现状（t19）**：该警告逻辑尚未实现（t17 待办），容器绑定与非回环暴露的验收在 t17 做 |
| 头部 | `X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`、`X-Frame-Options: DENY`、CSP `default-src 'self'`（无 CDN）、`Cache-Control: no-store`（API） |
| 令牌 | webhook/SDK 令牌只存 `sha256(salt + 明文)`（每令牌独立随机 salt）+ 前缀，明文**只在创建时返回一次**；保管箱文件 `chmod 0600`；作用域 `ingest:write` / `tokens:manage`；请求头 `X-TraceLens-Ingest-Token` 或 `Authorization: Bearer`（实况见 6.3 E1/E3/E4，例外理由与边界见 6.3"上报鉴权例外"） |
| 越权 | 服务端强制（第 5 节 + 6.5 的权限列），鉴权事件按 `access_denied` / `note` + `fields.auth_event` 落审计（t18 裁决：白名单含 `auth.denied`） |
| 已知限制（必须写进 README） | v1 无 SSO/LDAP、无 2FA；口令策略最小 10 位；HTTPS 不由本程序提供 |

---

## 15. 仓库、许可与版本纪律

| 项 | 基线 |
|---|---|
| 初始化 | `git init`（工作目录 `agent-explainer/`）；`main` 分支；每个子任务一次提交，message 前缀 `t<N>:`；打 tag `v<ver>` |
| `.gitignore` | `__pycache__/`、`*.pyc`、`node_modules/`、`desktop/dist/`、`desktop/release/`、`web/dist/`、`release/`、`docker/*.tar`、`data/`、`*.db`、`*.db-wal`、`*.db-shm`、`audit/`、`raw/`、`.env`、`*.key`、`*.pfx`、`.DS_Store` |
| **LICENSE** | **Apache License 2.0**（OSI 认证开源许可证）：允许商用、修改、再分发与对外提供服务；须保留版权与许可声明、标注修改、随附 `NOTICE`，并含明确专利授权。全文取自 apache.org 官方文本；归属与声明见 `NOTICE` |
| 第三方 | `docs/NOTICE-third-party.md` 列 Electron/PyInstaller/FastAPI 等许可（构建脚本生成） |
| 版本 | SemVer：破坏性数据模型变更 = MAJOR（schema `1.1 → 2.0`，必须带迁移脚本）；新增字段 = MINOR；修复 = PATCH。schema `schema_version` 与应用版本解耦（应用版本更频繁） |
| CHANGELOG | Keep a Changelog 分节（Added/Changed/Fixed/Removed/Known Limitations）；打包脚本校验版本与 CHANGELOG 条目存在，否则构建失败 |
| 归属声明 | 桌面"关于"页与导出页脚显示版本、构建号、许可声明、"数据仅存本机/自建卷" |
| 数据模型变更纪律 | 任何字段/枚举/端点/错误码/i18n key 规范改动 → 在 `docs/ADR/` 加一条决策 + 队长确认；不接受实现时顺手改 |

---

## 16. 代码签名策略（诚实版：只做完整性，不装"受信任发布者"）

| 步骤 | 命令（PowerShell；实现落在 `tools/sign_and_hash.ps1`，构建**之后**独立运行，`electron-builder.yml` 不配任何签名选项） |
|---|---|
| 0 前置 | 本机 Windows PowerShell 5.1（无 `pwsh`）且未签名脚本被拒时的可复现调用形态：`& "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File tools\sign_and_hash.ps1`；脚本文件必须是 **UTF-8 带 BOM**，否则 5.1 把中文注释按 ANSI 解析成语法错误 |
| 1 建证书 | 复用 `Cert:\CurrentUser\My -CodeSigningCert` 里未过期的同 Subject 证书；没有则 `New-SelfSignedCertificate -Type CodeSigningCert -KeyUsage DigitalSignature -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 -CertStoreLocation Cert:\CurrentUser\My -NotAfter <+1 年> -TextExtension '2.5.29.37={text}1.3.6.1.5.5.7.3.2'`（自建，**不购买商业证书**）；受限环境下这条路会被 CertEnroll 拒绝（`Access is denied 0x80070005`），脚本自动降级到 .NET `CertificateRequest` 现造证书，两条路的原始报错都写进 `SIGNATURE-STATUS.json` 的 `certificate.attempts` |
| 2 签名 | `Set-AuthenticodeSignature -FilePath <exe/msi> -Certificate <cert>`（可选 `-TimestampServer`）；签名动作被拒 → 该产物状态写 `NotSigned` + 原始报错，**绝不写"已签名"**；全部未签名 → `unsigned=true` 且脚本退出码 1 |
| 3 导出证书 | `Export-Certificate -Cert <cert> -FilePath tracelens-code-signing.cer`（供用户核对签名者指纹；无该 cmdlet 时用 `$cert.Export('Cert')` 落盘） |
| 4 校验 | `Get-AuthenticodeSignature <file>` → 用户核对 `Status` 与 `SignerCertificate.Thumbprint` 与 `.cer` 一致；`-Verify` 逐条重算哈希并复核状态（接受 `Valid`/`NotTrusted`，其余判失败） |
| 5 哈希 | `SHA256SUMS.txt`（`Get-FileHash -Algorithm SHA256`，coreutils 两空格格式）；用户端 `Get-FileHash` 或 `certutil -hashfile` 比对；测试侧 `hashlib` 独立复算 |

**必须写进 README 与安装指引的诚实声明**（A6/K-01）：

> 本产品的代码签名是**自签名证书**（我们给自己签发），仅用于**完整性校验**——证明包未被篡改。它**不是**受信任发布者证书，因此 Windows **SmartScreen 仍可能弹出"未知发布者 / Windows 已保护你的电脑"**。此时请：① 核对 `SHA256SUMS.txt`；② 核对签名者指纹与 `tracelens-code-signing.cer` 一致；③ 确认无误后选择"更多信息 → 仍要运行"。我们**不承诺**"签名后即可无缝安装"，任何这类说法都是虚假承诺。

补充纪律：签名私钥**不入仓库**（`.gitignore` 已排除 `*.pfx/*.key`）；便携版 zip 内的 exe 逐个签名；`SHA256SUMS.txt` 覆盖全部交付物；t16 的实测套件必须包含"SmartScreen 预期警告 + 核对步骤"页面。

> **现状更新（2026-11-12，R2-1）**：下面那段"签名未产出"是 **0.4.5 时点**（5.3 / t12、5.3b / t26）
> 的复现依据，保留不改。0.5.0 已真实签名：`powershell tools/sign_and_hash.ps1` 对
> `TraceLens-0.5.0-win-x64-Setup.exe`（151,838,192 字节）与 `TraceLens-0.5.0-win-x64-portable.exe`
> （137,883,824 字节）各落一次签名，状态如实记 `UnknownError`（根证书不受信任）、
> `signatures_applied=2`、`signed_count=0`、`trust_state=self_signed_root_untrusted`
> （权威：`release/SIGNATURE-STATUS.json`；哈希权威：`release/SHA256SUMS.txt`；实录 `docs/audit/v050-release.md`）。

本机现状（**0.4.5 时点**，5.3 / t12，5.3b / t26 复核）：签名**未产出** —— 受限令牌下 CertEnroll 被拒
（t12 实测 `0x80070005 ERROR_ACCESS_DENIED`，t26 实测 `0x80090010 NTE_PERM`，两种都出现过），
本机无 `signtool.exe`、`Cert:\CurrentUser\My -CodeSigningCert` 0 张；
.NET `CertificateRequest` 能现造带 Code Signing EKU 的证书并导出 `.cer`（762 字节），
但 `Set-AuthenticodeSignature` 报 `The specified certificate is not suitable for code signing`；
`SIGNATURE-STATUS.json` 如实记 `status=NotSigned`、`unsigned=true`、`signed_count=0`、
`artifact_count=1`，脚本退出码 1；`SHA256SUMS.txt`（完整性）已产出并通过 `-Verify` 与测试双向复核
（`-Verify` 因签名状态不达意退出码 1，sha256 本身 `[sha256 ok]`）。
t26 修掉一处**会导致谎报的脚本 bug**：PowerShell 5.1 下管道单元素结果的 `.Count` 是 `$null`，
`build/dist` 只剩一个产物时"全未签名"被判成 `unsigned=false` 且退出码 0。
现在以 `signed_count`（只统计 `Valid` / `NotTrusted`）为唯一口径写进状态文件，
并由 `tests/test_e2e.py::ArtifactTests::test_signature_status_is_honest` 断言三者不自相矛盾。
**0.5.0 口径细化**（`tools/sign_and_hash.ps1`，全量单测那条红的真因）：过去状态文件只有一个
`signed_count`，发布件里写 `signed_count=2` 而两个产物的 `Get-AuthenticodeSignature` 状态都是
`UnknownError`（签名在、链在不受信任的根处终止），测试按"只有 `Valid`/`NotTrusted` 算已签"算出 0，
于是 `0 != 2` 长期红。现在两个事实**分开写**，谁都不许替谁背书：

| 字段 | 口径 | 自签名实测典型值 |
|---|---|---|
| `signed_count` | 状态属于 `Valid` / `NotTrusted` 的产物数（＝测试口径，不许掺水） | `0` |
| `signatures_applied` | 文件里**确实落了签名**的产物数（看 `SignatureType` + 签名人指纹，与是否受信任无关） | `2` |
| `unsigned` | 没有任何"被 Windows 认可的签名"（与 `signed_count -eq 0` 等价） | `true` |
| `trust_state` | `trusted` / `partially_trusted` / `self_signed_root_untrusted` / `not_signed` | `self_signed_root_untrusted` |
| `signed_by` | 签名人 Subject + 指纹 | `CN=TraceLens Self-Signed (Local Only)（指纹 …）` |
| `status_counts` | 逐状态计数（可核对，不是一句话口径） | `{"UnknownError": 2}` |

`status` 字段保持 `Get-AuthenticodeSignature` 的**原样取值**（自签名典型为 `UnknownError`），
**绝不为了让断言变绿写成 `Valid`**；`trust_note` 保留"自签名"字样。产物编码口径同时写死：
`SIGNATURE-STATUS.json` / `SHA256SUMS.txt` 一律 **UTF-8 无 BOM**、中文原样落盘，脚本写之前剥
U+FEFF、发现 U+FFFD（源码被按 ANSI 解码的证据）直接拒绝出文件、写完回读字节复核"无 BOM +
逐字还原"；`-CheckEncoding` 是只读审计开关。本机实测（2026-11-12）：现存三个发布件
（`desktop/build/dist/`、`release/`、`release/tracelens-0.4.5-release/`）已是 UTF-8 无 BOM、
中文完好、JSON 可解析，历史登记的"去 BOM"项**在产物侧不复现**；真正的乱码根因是脚本源码
丢 BOM（见上表第 0 步），脚本现在启动时自检并报警。
另：上文"`.gitignore` 已排除 `*.pfx/*.key`"在 t12 时并不成立（仓库根当时没有 `.gitignore`），
t26 已补齐该文件（含 `node_modules/`、`desktop/build/`、`var/`、`.cache/`、`*.pfx`、`*.key`）并在文件内写明这条更正。

---

## 17. Docker 前置条件（t17 的硬闸门，禁止用静态校验糊弄）

| 项 | 要求 |
|---|---|
| 前置（**用户动作，须早于 t17**） | 发起人需在 **5.4 之前**安装 **Docker Desktop**（Windows + WSL2 后端），并确保 `docker` CLI 在 PATH 中。此事项已在 t1 A5 记为显式假设，需队长在排期上置顶 |
| t17 第一步 | **必须真实执行 `docker info`**（`docker/healthcheck` 之前的 preflight 脚本 `docker/preflight.ps1`）：`docker version` + `docker info` + `docker compose version` 三条命令的真实输出（含 Server Version、OS/Arch、操作系统容器模式）写入 `docs/docker-preflight.md` |
| 不可用时 | **立即停下并报告发起人**，请求安装/启动 Docker Desktop；在得到回应前：**不得**继续 Docker 验收，**不得**改用"检查 Dockerfile 语法"、"YAML 校验"、"镜像清单静态检查"等手段**替代**真实构建来宣称通过 |
| 验收（必须真实） | `docker build` 真实成功（记录镜像 ID 与耗时）→ `docker compose up -d` → `GET /health` 200 且 `status:"ok"`（**没有 `/api/health` 路径，也没有 `ready` 字段**；字段口径见 6.6，`ingest.enabled`/`settings`/`auth.bootstrap_needed` 按启动参数如实反映）→ 浏览器登录三角色 → 重启容器后数据仍在（卷持久化，记录前后 `count`）→ `docker save` 离线 tar + `SHA256SUMS` |
| 报告要求 | 失败时如实记录：命令、完整错误输出、退出码、已尝试的排障步骤、当前阻塞点、可用的两条降级路径（桌面 + 便携版先过实测） |
| 记录格式 | `docs/docker-preflight.md` 固定表头：`命令 / 实际输出摘要 / 退出码 / 结论`，禁止只写"通过" |

---

## 18. 集成契约表（每个后续任务的开工单：负责什么、消费什么、产出什么、怎么算过）

**共同约定**（所有任务适用）：
① 数据契约 = [TL-DM-002](./02-data-model.md) + `docs/schemas/action_record.schema.json`，字段与枚举**不得改名**；
② 接口契约 = 本文第 6 节（6.3 现状列 + 6.4 码表 + **6.5 核对表/差异记录** + **6.6 `/health` 字段**）+ 机器可读的 **`docs/api_contract.json`**（4.7 起由 `tools/gen_api_contract.py` 从代码生成，`--check` 逐条比对本文与代码；旧落点 `agent_explainer/api_contract.json` 作废，见 6.5 差异 16）；
③ 文案一律走 i18n key（第 7 节），双语覆盖率 100%；
④ 验收必须**真实执行**，禁止静态检查冒充；
⑤ 每个任务结束时提交"命令 + 退出码 + 输出摘要"到队长，并在 `docs/` 留可复现脚本。

| 任务 | 负责的模块/文件 | 消费（上游契约） | 对外产出（接口/数据流） | 必须复用 | 验收要点（真实执行） |
|---|---|---|---|---|---|
| **t3 适配器 + 演示数据** | `agent_explainer/trace_adapter/*`、`samples/*` | schema v1.1；`source.kind` 7 类；`raw_ref` 语义 | `trace_adapter.convert(doc, format)->list[ActionRecord]`；`detect(bytes)->format`；`samples/*.json` 夹具 + `samples/README.md` | `tools/facts_hash.py` 生成指纹；隐私 lint | 3 种外部格式夹具转换成功率 100%（自造样本）；演示集 ≥12 条 / ≥10 类 / 含失败+高危+敏感路径（DM 8.4 规格）；全部通过 `validate_schema.py` 同等校验；未知字段进 `raw_ref` 不丢 |
| **t4 翻译引擎** | `agent_explainer/explanations/{facts,render,templates}` | 四要素字段；A9 长度；降级规则 | `render(record, lang)->human_readable`；模板 ID 命名 `<action_type>.<variant>.v1` | `facts_hash`（模板与 LLM 共用指纹） | 四要素齐全；zh/en **各自原生模板**（非机翻）；覆盖 ≥10 类 × 2 语言；缺失字段出降级文案（"来源未提供原因"）不崩溃；L1≤40 字（脚本度量）；`source_facts_hash` 重算一致 |
| **t5 LLM 增强** | `explanations/llm.py`、提示词目录 | `settings.llm.*`、DPAPI/env 密钥 | `enhance(record, base_readable)->readable(may_contain_inference=true)` | 第 8 节密钥基线、`facts_hash` | 默认 `template`；无 key/断网不崩溃（K-12）；润色后指纹一致（**新增事实必须被拒收并回退模板**）；超时/401/拒连返回可读错误码 |
| **t6 前端骨架（三区+双语）** | `web/{index.html,app.js,zones.js,styles}`、`web/i18n/{zh,en}.json` | Q1/Q2/Q3/Q9 查询；颜色语义（绿完成/黄进行/红出错/蓝待确认）；三区结构 | 组件级 key 清单 `web/i18n/keys.md`；静态演示模式（内嵌 ≥12 条，无后端可演示） | `api_contract.json`；i18n lint | `i18n_lint.py` 零硬编码；切 en 中文残留 0（K-10）；"需要我"置顶且一次只出一个；首屏 1000 条 <1.5s（`tools/perf_probe.py`） |
| **t7 前端交互（干预+时间轴+设置+响应式）** | `web/{intervene.js,timeline.js,settings.js}` | `/intervene`、Q4 时间轴、`editable_fields` 动态表单 | 干预表单（按 `editable_fields` 生成）、错误内联提示、降级提示（API 不可用→本地模拟 + 显式"未写入审计"） | 错误码表 + `effect_notice` 文案 | 三操作各 3 次成功（K-07 60s 内）；非法参数逐条内联提示；文案**不得**出现"已强制停止"，必须显示"决定已记录；是否生效取决于执行端是否接入"；≤480px 悬浮球可用 |
| **t8 干预 API** | `server/routers/intervene.py`、`store/queries.py` | 迁移表 4.2；`policy.min_role_to_decide` | `POST /intervene` 契约（含幂等、审计、SSE 广播、重渲染 L1） | 第 4.3 三纪律；审计链 | 状态迁移与审计完全一致（K-08，3/3）；重复 `Idempotency-Key` 返回首次结果；viewer 403；实况码名 `INTERVENE_STATE_INVALID`/`INTERVENE_PARAM_*`/`INTERVENE_REASON_REQUIRED` 可读（旧名见 6.4）；写入 <150ms；**实况落点 = `server.py`（无 `server/routers/`、`store/queries.py`）** |
| **t9 审计与导出**（导出面 **4.8 补齐**） | `agent_explainer/audit.py`（**单文件**，未拆 `audit/*`） | JSONL 哈希链规范；脱敏字段表 | `audit.append(event)`、`audit verify`（CLI）、`GET /api/audit`（H1）、**`GET /api/audit/export`（M6，4.8 已实现：csv / 可打印 html / reportlab pdf）**；`/api/audit/verify` 与 `/api/export.csv` 这两个**路径**仍未实现（链校验随 `chain` 块与 CLI；记录导出走 R1） | `canonical_json` 与 facts 同一实现 | 篡改任意 1 条 → verify 必须失败并给 `first_bad_seq`（K-09）；导出 ≤30s；**脱敏是写盘前强制的 13 类 PII（没有 `redact=1` 参数）**；"含演示数据页脚强制标注"在**审计导出 HTML/PDF 里尚未实现**（待办，见第 13 节，QA 不得宣称已交付）；运行账号无 UPDATE/DELETE（`FORBIDDEN_OPERATIONS` 20 个动词） |
| **t10 测试套件** | `tests/{unit,integration,e2e,perf,walkthrough}`、`tests/run_all.py` | 全部契约 + 问卷维度（K-05/K-07/K-13/K-14） | `run_all.py`（顺序：i18n lint → 单测 → 集成 → e2e → perf → schema 校验）；11 个必测任务 | `validate_schema.py`、`perf_probe.py`、隐私 lint | 权限矩阵 12 项判定（401/403）；webhook 幂等；SSE 断线降级；三形态安装冒烟；隐私 lint 自检通过；全绿才可进阶段 1 |
| **t11 模拟走查报告** | `docs/05-test-report.md` | K-05/06/07/09/13、H-A~H-E、t1 7.2 阶段 1 | 定量结果 + 按严重度缺陷清单 + ≥10 条可执行改进 + 假设逐条结论 | t1 3.5 假设表、t10 问卷 | 首页显著标注"**合成数据 + 内部扮演，非真人测试**"；阻断 0、高 ≤3；3 秒扫读与逐条复述测试有原始记录 |
| **t12 桌面打包** | `desktop/*`、`tools/sign_and_hash.ps1`、`tools/ci.ps1` | 第 10 节步骤；第 16 节签名 | `TraceLens-Setup-*.exe`、`TraceLens-Portable-*.zip`、`SHA256SUMS.txt`、`.cer`、`release/<ver>/` | sidecar self-check、electron-builder 配置 | `npm run dist` 真实成功（npm，非 pnpm）；干净机安装 ≤5 分钟（K-01 录屏）；便携版不写注册表（前后快照比对）；sidecar 崩溃自动重启验证；单实例锁验证 |
| **t13 认证与角色** | `agent_explainer/auth/*` | 第 5 节矩阵；PBKDF2/CSRF/限速基线 | `/api/auth/*`、`/api/users*`；会话吊销 | `api_contract.json` 角色字段 | local 模式无登录页且全功能；三角色 12 项判定通过（K-11）；口令不可逆（导出数据库看不到明文）；锁定与审计 |
| **t14 实时接入**（**已落地**，实况见 6.3 E1–E5/F1 与 6.6） | `agent_explainer/ingest.py`（**单文件**：接入 + 令牌保管箱 + SSE hub；未拆 `ingest/*`、无 `server/sse.py`）、`sdk/client.py`、`samples/sdk_demo.py` | `source.kind=webhook/sdk`；幂等键 `Idempotency-Key` / `idempotency_key`（**实况不是 `dedupe_key`**） | `POST /api/ingest/events`、`POST /api/ingest/tokens`(+`/{id}/revoke`)、`GET /api/ingest/status`、`GET /api/stream`、**SDK 示例**（`log_action/emit_event/update_action/stream_events/issue_token/revoke_token` + `wait_for_decision(action_id, timeout)->decision\|None`，纯标准库 `urllib`，一键可跑） | 幂等表（TTL 900s）、SSE 8 个事件名 | 重复上报 → 计入 `duplicates` / 幂等重放 200（**不是 409**）；SSE 端到端 <1s（perf_probe，t10 测）；断线重连（`Last-Event-ID`/`?since=`，环形 200 帧）+ 降级轮询（服务端声明 `fallback.interval_ms=4000`）；SDK 示例演示"读到 reject 后中止"→ 写 `refused_by_runtime` 回执；**示例注释必须写明 v1 不承诺跨语言实时阻断（A12）** |
| **t15 设置页与密钥**（**已落地**，实况见 6.3 J1/J2 与第 8 节） | `agent_explainer/settings.py`（**单文件**，含 DPAPI ctypes；未拆 `settings/*`）、`web/app.js` 的 settings 中心（未拆 `web/settings.js`） | 第 8 节密钥基线；`var/settings.json`（无 `settings` 表） | `GET/PUT /api/settings`、`POST /api/settings/test-llm`（**`purge` 未实现**，清空 = `POST /api/reset`） | DPAPI ctypes、掩码规则（`••••••`+末 4 位、`sha256[:12]` 指纹） | 无 key 全流程可用（K-12）；保存后 `engine` 块即时反映引擎生效；key 只回显掩码 + `key_action`；团队模式密钥来自 `TRACELENS_LLM_API_KEY` 且只读（**实况字段 `api_key_source="env"`，不是 `secret_kind`**）；`test-llm` 的 14 个 `LLM_*` 码可读；清空写 `record_reset`，审计不删 |
| **t16 交付与真实实测套件** | `docs/05-real-user-kit.md`、`release/<ver>/`、README 安装指引 | 第 16 节诚实声明、K-01/K-16、t1 7.2 阶段 2 | 安装指引（含 SmartScreen 预期警告与核对步骤）+ 10 分钟脚本 + 反馈表单 + 三形态计时表 | `SHA256SUMS.txt`、`.cer`、LICENSE | 三形态至少两种在发起人机器成功；n=1 结论按定性表述（不得称统计显著）；K-05/07/13 复测同口径 |
| **t17 Docker 团队部署** | `docker/{Dockerfile,docker-compose.yml,preflight.ps1,README.md}` | 第 17 节闸门；团队安全第 14 节 | 镜像、compose、卷与 `env_file` 说明、反向代理示例（nginx/Caddy） | `entrypoint.sh --mode team`、healthcheck | **首步真实 `docker info`**（不可用→停下问发起人，禁止静态校验糊弄）；`docker build` 成功；`compose up` 后 `GET /health` 200 且 `status:"ok"`（**没有 `/api/health`，也没有 `ready` 字段**，见 6.6）；三角色登录；重启容器数据不丢；非 root 运行 |

---

## 19. 风险与回退

| 风险 | 概率 | 回退 |
|---|---|---|
| PyInstaller 单文件被杀软误报 / 启动慢 | 中 | 备选：sidecar 改为目录形态（`--onedir`）+ 便携 zip 打包；两形态在 t12 都构建，安装版择优选 |
| Electron 包体过大（>200MB） | 中 | 裁剪 `extraResources`（只留 zh/en 必需资源）；便携版优先保证功能，安装版做组件化 |
| 标准库 HTTP 层性能不足（SSE 并发） | 中 | 启用可选 FastAPI 层（已打包进产物，行为对拍测试保障），文档标注差异 |
| 双服务实现漂移（stdlib vs FastAPI） | 中 | `tests/integration/test_two_servers_parity.py` 用 `api_contract.json` 生成用例，CI 双跑 |
| 自签名 + SmartScreen 导致安装流失 | 高 | 便携版作为主逃生通道（不触发安装警告）；README 预警 + t16 引导核对哈希 |
| 干预"假闭环"预期落空（用户以为 AI 已停） | 高 | 4.3 三纪律写进每一处文案；SDK 示例演示真实阻断路径；界面常显"执行端未回执"状态 |
| 双语成本吃掉主线 | 中 | i18n lint 从 t6 第一行代码起强制；`l1_zh/l1_en` 平铺避免二次返工；双语为 Must，不降级（A10/队长确认） |
| Docker 环境缺失阻塞 t17 | 中 | 第 17 节闸门：停下报告；桌面 + 便携版两形态先通过实测（t1 A5 处置） |

---

## 20. 自检清单（逐条对照 t2 任务要求）

| # | 任务要求 | 位置 | 状态 |
|---|---|---|---|
| 1 | 交付形态：Windows NSIS 安装包（自签名）+ 便携版 zip + Docker 镜像 + 源码 | 第 1 节（4 形态 + 真实验收判据） | ✅ |
| 2 | 进程模型：Electron main 托管 PyInstaller sidecar（127.0.0.1）、单实例锁、优雅退出；团队模式 Web 部署 | 第 2.1 / 2.2 节 | ✅ |
| 3 | 目录与模块：`agent_explainer/{trace_adapter,explanations,audit,auth,ingest,settings,server,store}`、`web/`、`samples/`、`tests/`、`desktop/`、`tools/`、`docs/` | 第 3 节（逐目录点名，含 i18n 子目录）。**实况差异见 6.5 差异 14**：模块是单文件（`ingest.py`/`settings.py`/`audit.py`/`auth.py`/`server.py`/`explanations.py`/`trace_adapter.py` + `sdk/` + `web/`），**没有 `store.py`**（`ActionStore` 在 `server.py`），`desktop/`、`docker/` 尚未创建（t12/t17） | ✅ |
| 4 | 存储：SQLite 六表 + `schema_version` 与迁移脚本；审计 JSONL 哈希链 | 第 4 节 + DM 6.2/6.3/6.4。**实况**：4.x 落地的持久化是 **JSON 记录存储（`ActionStore`）+ 追加式审计 JSONL 链 + `var/settings.json` + 令牌保管箱**；SQLite 六表与迁移脚本属后续任务（见 6.5 差异 19），审计链已实测（t9/t18） | ✅ |
| 5 | 双模式与权限矩阵（local 免登录 / team admin-editor-viewer） | 第 5 节（矩阵 + 服务端强制点） | ✅ |
| 6 | API 契约与统一错误码（12 组端点 + `{code,message,detail,request_id}` + 错误码表） | 第 6.1–6.6 节：6.3 端点表（**已加"现状（代码实测）"列**，逐条 ✅/⚠）、6.4 码表（**已加现状列**，155 码按域分组，75 有 i18n 键 / 80 走 `message` 回退）、6.5 路由核对表 + 差异记录（以代码为准，**4.7 起可用 `tools/gen_api_contract.py --check` 机器复核**）、6.6 `/health` 字段说明 | ✅ |
| 7 | i18n：zh/en 字典、全量 key、切换持久化、i18n lint 禁止硬编码 | 第 7 节（**双语为 Must**，队长确认） | ✅ |
| 8 | 模型配置与密钥：OpenAI 兼容、DPAPI（ctypes）、团队环境变量、禁止明文入库/入日志 | 第 8 节（含 ctypes 调用要点与掩码/审计规则） | ✅ |
| 9 | 依赖政策：核心纯标准库；FastAPI 可选且打进产物离线可运行；Electron/PyInstaller 仅构建期；Python 3.12+（本机 3.14.7） | 第 9 节 + `tools/test_offline_engine.py` 实跑 50/50 | ✅ |
| 10 | 打包与发布：electron-builder（NSIS+portable，**npm 无 pnpm**）、PyInstaller sidecar、Dockerfile/compose、release zip + SHA256SUMS、自签名 | 第 10 节（10 步 CI 流水线） | ✅ |
| 11 | 性能基线：1000 条首屏 <1.5s、筛选 <200ms、SSE <1s | 第 11 节（含测量口径与 `tools/perf_probe.py`） | ✅ |
| 12 | 支持矩阵：Win10/11 x64；团队 Chrome/Edge 最新版 | 第 12 节 | ✅ |
| 13 | 隐私与保留：数据仅本机/自建卷、一键清空、导出可选脱敏、日志脱敏 | 第 13 节 + DM 第 9 节（含 lint 自检证据） | ✅ |
| 14 | 团队安全：httpOnly+SameSite、CSRF、HTTPS 反代并文档说明 | 第 14 节（含 nginx/Caddy 要求与禁止公网直暴） | ✅ |
| 15 | 仓库与许可：`git init`、`.gitignore`、`CHANGELOG`、SemVer、LICENSE Apache-2.0 | 第 15 节 | ✅ |
| 16 | 签名策略：`New-SelfSignedCertificate` + `Set-AuthenticodeSignature` + SHA256SUMS，仅作完整性校验，SmartScreen 仍会提示 | 第 16 节（含必须写进 README 的诚实声明） | ✅ |
| 17 | Docker 前置：发起人须在 5.4 前装 Docker Desktop；t17 首步 `docker info` 实检；不可用则停下问用户；禁止静态校验糊弄 | 第 17 节 | ✅ |
| 18 | 集成契约表：每个后续任务负责的文件/接口/数据流 | 第 18 节（t3–t17 全 15 个任务 + 5 条共同约定） | ✅ |
| — | schema draft 2020-12 且含 `actor_id`/`workspace_id` | `docs/schemas/action_record.schema.json`（两字段必填非空） | ✅ |
| — | ActionRecord 至少含 20 个指定字段 | DM 第 2 节逐字段（20/20 全部为必填字段） | ✅ |
| — | schema 通过实际校验脚本 | `python tools/validate_schema.py` → **50 项检查全部通过，exit 0** | ✅ |
| — | ≥3 条通过示例 + 1 条失败示例 | 6 条正向 + **39 条负向**（均带期望关键字与修复建议） | ✅ |
| — | 集成契约让后续任务无需开会即可开工 | 第 18 节 + DM 8.4 夹具规格 + 第 6 节端点表 | ✅ |
| 队长补充 | ① 双语 Must；② 无真实 trace 样本（自造合成、不索要）；③ 干预语义三段分离 + 文案禁令 | 第 7 节 / 第 13 节 / 第 4.3 节（DM 4.2–4.3、schema C11/C13/C14、`effect_notice` key） | ✅ |
| 4.6（t19） | 架构与接口契约文档**与 4.4/4.5 代码实况逐条同步**：E1/F1 文案与形状改写、新增令牌端点、披露"team 必须登录"的机器令牌例外（含理由与边界）、`/api/settings` 按 t15 实况（GET/PUT/test-llm/掩码/权限口径）、同步错误码表与 `/health` 的 ingest 字段说明、列出核对表与差异记录、**只改 `docs/`，不改代码** | 第 6.3（现状列）+ 6.4（现状列）+ **6.5 路由核对表 / 差异 20 条** + **6.6 `/health` 字段** + 第 4/5/7/8/11/14/17/18 节实况注 | ✅ |
| 4.6 纪律 | 写文档前**先从代码提取**真实路由与码表（方法见 6.5），**代码与文档冲突时以代码为准并留痕**；禁止凭记忆写接口 | 第 6.5 节"提取方法"+ 差异记录表 | ✅ |
| **4.11（t24）** | 把 4.8 的审计导出接口**写进契约文档**：6.3 补出 **M6** 行（GET / 能力 `audit.export` / **GET 免 CSRF** / `format=csv\|html\|pdf` 默认 csv、非法 400 / 过滤 8 键与 H1 同源 / `limit` 500→5000 / 链断 500 **不出证据** / reportlab 缺失回退 + 头 / **先记账 `event=export`**）；6.4 加 `EXPORT_FORMAT_INVALID` 与审计导出三码（`AUDIT_REPORTLAB_UNAVAILABLE`/`AUDIT_PDF_EMPTY`/`AUDIT_EXPORT_WRITE_FAILED`）、补 `AUDIT_QUERY_INVALID`/`AUDIT_CHAIN_BROKEN` 的导出触面；6.5 导出行**按代码改正**（删掉代码里不存在的 `json` 格式、`from/to` 键名、"签名链接与过期码"）+ 差异 11 改口径；§4/§13 写入 **PII 打码 13 类模式 + `<redacted:类型#sha256:前12位>` 指纹**（写盘前强制、幂等、落盘/接口/导出三处同源）与"导出记账"行；6.6 加 `audit_export` 块；§18 t9 行改口径。**只改 `docs/`（`docs/02`、`docs/03`、`docs/05`），另按授权跑 `--write` 重生成生成物 `docs/api_contract.json`（内容不变：27 条路由 / 164 码 / 29296 字节，只有 `docs/03` 哈希与同步标记刷新），未动任何代码** | 第 6.3 **M6** + 6.4 新码行 + 6.5 导出行与差异 11 + 6.6 `audit_export` + 第 4 / 13 / 18 节 + DM 第 7 节 Q8 实况行 | ✅ |
| **4.9 / t22** | 用户有可见的导入入口（粘贴 / 上传），记录与决定**活得过重启**，超限与非法有可读错误，`viewer` 越权 403，`reset` 只清记录、审计只追加，并发写不丢行 | 第 4 节"记录持久化"行 + 6.3 **D3** + 6.4 新增码行 + 6.5 差异 22 + 6.6 `import`/`records_store` 块；证据 = `tests/test_import_persistence.py` + selfcheck 76–80 项 + 真实冒烟（粘贴→列表→重启→仍在） | ✅ |

**自检结论**：任务点名的 18 项架构内容与 4 项验收判据全部落地并有可执行证据；schema 校验、离线引擎、双向夹具全部通过。**可进入 t3（后端：适配器与演示夹具）**。

**4.6（t19）同步结论**：本文第 6 节已按 4.1→4.3→4.2b→**4.4→4.5** 的代码实况逐条重述 —— 端点表加了"现状（代码实测）"列（✅ 已实现 / ⚠ 未实现，规划项一律显式标注，禁止当作可用接口测试），错误码表按四个域重排并标注 128 个码的真实出处与 i18n 覆盖实况（85 有键 / 43 走 `message` 回退），新增 6.5（可复现的提取方法 + 逐条路由核对表 + **20 条差异记录，一律以代码为准**）与 6.6（`/health` 字段说明，含 4.4 的 `ingest`/`stream` 块与 4.5 的 `settings` 块）。第 4/5/7/8/11/14/17/18 节里会被 QA 与后续任务直接引用的端点名、环境变量名、掩码格式、审计事件名、模块落点、健康检查口径已全部改到与代码一致。**本轮只改 `docs/`，未动任何代码**（验收方式：`python tools/doc_lint.py docs/01-product-and-users.md docs/02-data-model.md docs/03-architecture.md`）。**可进入 5.1（QA：测试套件与实测清单）**——QA 的用例基线 = 6.3 的 ✅ 行 + 6.5 核对表；`⚠` 行与差异记录里的未实现项**不得**写成已交付。

**4.7（t20）同步结论**：把"以代码为准"从口号变成命令 —— ① `tools/i18n_lint.py`（字典双向一致 / 禁硬编码 / 错误码覆盖 / `embed.js`+`keys.md` 生成物同步 / 双语夹具，5 项全绿退出 0）；② `tools/gen_api_contract.py` → **`docs/api_contract.json`**（AST 从 `server.py` 分发分支 + `auth.py` 角色矩阵 + `app.js` 调用面 + `i18n_lint` 码表生成，排序稳定、可重复生成，`--check` 与 §6.5 的 21 行 / 23 条路径逐条比对，漂移即非零退出）；③ 双语演示数据 `samples/action_records_en.json`（`tools/gen_samples_en.py` 原生英文生成，零 CJK、指纹复算、`--check` 稳定）+ `web/demo-data-en.js` 装载 + `app.js` 按语言选数据集，英文界面 0 条中文数据（selfcheck 71 项含 4 项 4.7 新断言全绿）；④ 3.2 遗留的 `fieldLabel()` 兜底键隐患闭合（`compare.field.l1` 删除、改用双语都在的 `compare.field.other`）。**6.5 的差异 16 / 17 / 20 已闭合，新增差异 21（演示数据语言与字段标签兜底）；`samples/traces/*` 与 `tests/run_all.py` 接入 i18n lint 仍是未交付项。**本轮只改 `tools/`、`samples/`、`agent_explainer/web/`、`docs/03`，未动任何服务端模块与测试装配。

**4.9（t22）同步结论**（数据导入与记录持久化，补 QA 5.1 的 D1 与"重启回到演示 12 条"）：① 新增 **D3 `POST /api/import`**（6.3 行 + 6.4 码表 + 6.6 健康块 + §4"记录持久化"行 + §5 权限行 + §13 导入正文不进审计 + 6.5 差异 22，全部按代码实况写）；② 记录持久化落点改为**实况**：新模块 `agent_explainer/store.py`（追加式 JSONL + 内存索引，默认 `var/records.jsonl`），§4 写清"为什么是 JSONL 不是 SQLite"、崩行/尾截断/锁/原子压实/跨进程签名同步的口径，差异 19 的"没有 `store.py`"已按实况改写；③ `POST /api/reset` 的语义写清 = 清回演示种子 12 条 + 日志压实 + 审计只追加，启动参数 `--records-store / --no-records-store / --records-store-lock / --reset-demo` 入档；④ i18n 实况 **689 键 / 21 域**（新增 `import` 域 73 键，`import.error.*` 27 键覆盖 `IMPORT_*` 与 `RECORD_STORE_*`），selfcheck **80 项**（新增 5 项导入断言：入口挂载两处/粘贴入库/上传与上限/文案与码表契约/失败与权限状态）；⑤ §6.5 核对表补两行（`/api/import`、`/api/audit/export`），`python tools/gen_api_contract.py --check` 由"1 项差异（导出路由缺行）"变为 **0 项、退出 0**。**本轮只改 `agent_explainer/server.py`、新模块 `agent_explainer/store.py`、`agent_explainer/web/`、`tests/`、`docs/03` 与生成物 `docs/api_contract.json`**，未动 `trace_adapter.py`、`ingest.py`、`audit.py`、`auth.py`、`settings.py` 的算法与既有测试断言。

**4.11（t24）文档同步结论**（**只改 `docs/`**：`docs/02`、`docs/03`、`docs/05`；生成物 `docs/api_contract.json` 按任务授权用 `--write` 重生成——实质内容不变（27 条路由 / 164 码 / 29296 字节），只有 `docs/03` 的哈希与同步标记刷新；**未动任何代码**）：4.8 的审计导出接口现在在契约文档里是**完整且与代码一致**的一条 —— ① 6.3 补出 **M6** 行（此前 6.5 有这行、6.3 端点表没有，属"文档内部也不一致"）；② 6.4 补 `EXPORT_FORMAT_INVALID` + 审计导出三码，并把 `AUDIT_QUERY_INVALID`/`AUDIT_CHAIN_BROKEN` 的**导出触面**写清（链断不出证据）；③ 6.5 的导出行**按代码改正**：`format` 是 `csv|html|pdf`（**没有 json**）、过滤键是 `since/until`（不是 `from/to`）、`limit` 500→5000、**代码里没有"签名链接与过期码"**，差异 11 同步改口径；④ §4 与 §13 写入 **PII 打码实况**：`redact_text()` + `PII_PATTERNS` **13 类模式**，写盘前强制、**没有 `?redact=1` 开关**、`<redacted:类型#sha256:前12位>` 同值同指纹且幂等，落盘文件 / `GET /api/audit` / CSV·HTML·PDF 三处同源；⑤ 6.6 加 `audit_export` 块（含 `served` 计数与 reportlab 回退声明）；⑥ §18 t9 行改口径。**两条如实记录的未交付项**（QA 不得当作已完成）：审计导出 HTML/PDF 的"**含演示数据 → 页脚强制标注**"尚未实现（现状标注在记录侧 `demo_data`/`synthetic` 计数与前端徽标）；`EXPORT_*` / `AUDIT_*` 这一族**没有** `<域>.error.<code>` 键，界面文案靠 `app.js` 的 6 张显式映射表（`AUDIT_ERROR_KEYS=5`、`EXPORT_ERROR_KEYS=7`…）+ `message` 回退，顺带纠正 4.9 结论里"`import.error.*` 27 键覆盖 `IMPORT_*`"的表述（那 27 条是 **app.js 映射表**，`IMPORT_*` 与 `RECORD_STORE_WRITE_FAILED` 在 `i18n_lint` 的**无键清单**里）。**闸门**：`python tools/gen_api_contract.py --check` 退出 **0**（27 条 `(path, verb)` ↔ 22 个 `API_PATH_*` 常量 ↔ 6.5 表 23 行 / 25 条路径，方法列一致、0 条 ⚠；本轮开始前它已由 4.9 转绿，本轮的真实缺口是**行内事实错误与 6.3/6.4 缺行**，现已闭合）、`python tools/i18n_lint.py` 5 项 PASS（字典 689/689 键 21 域、144 条 error 键、**164 码 / 75 有键 / 89 走 `message` 回退**）、`python tools/doc_lint.py docs/01… docs/02… docs/03…` PASS。**可进入 5.1 之后的复测**：QA 按 6.3 M6 行 + 6.5 核对表写导出用例，`EXPORT_ERROR_KEYS` 的 7 条映射逐条命中，链断场景必须断言"不出文件 + 500"。

---

## 21. 5.0 增量（总裁办与八大扩展功能）

> 本节是 5.0 的**增量**说明，不改动第 1–20 节的既有口径。完整契约（字段/路由/错误码/验收映射/
> 诚实边界）的**唯一来源**是 [TL-SEC-005 `docs/07-secretariat-and-extensions.md`](./07-secretariat-and-extensions.md)；
> 本节只回答"5.0 在架构上新增了什么、落在哪里、哪些是降级"。
> 产品基线 `0.4.5` → **`0.5.0`**；数据契约 `SCHEMA_VERSION=1.1` **不升版**（新增字段全部可选）。

### 21.1 模块清单（`agent_explainer/`，仍然**纯标准库、无网络**）

| 模块 | 职责 | 对外关键面 | 契约出处 |
|---|---|---|---|
| `crypto_sig.py` | Ed25519 签名/验签、规范 JSON、哈希、DID、签名载荷 | `generate_keypair / sign / verify / canonical_json / sha256_hex / hash_object / b64e / b64d / did_from_pubkey / did_from_name / signed_payload / verify_b64` | 07 §4.1 |
| `qr.py` | 纯标准库 QR 编解码（ISO/IEC 18004 字节模式，v1–12）+ SVG/ASCII/PBM | `encode / decode / to_svg / to_ascii` | 07 §4.2 |
| `feature_store.py` | 5.0 实体存储：**Ledger**（哈希链账本）+ **Store**（实体集合）+ 文件锁/原子压实 | `FeaturePaths / FeatureStore / Ledger / open_store / open_ledger / GENESIS_HASH` | 07 §3.3 / 4.3 |
| `secretariat.py` | 总裁办：身份确权、行为签名、风险分级审批、委托链、责任认定书、盖章 | `Secretariat` / `SIGNATURE_FIELDS` / `RISK_POLICY` / `MAX_DELEGATION_DEPTH` | 07 §2 / §4.4 |
| `rollback.py` | 干预回滚：逆向动作注册表、依赖分析、执行与撤销日志、覆盖率 | `plan / RollbackService.execute / coverage / register_inverse` | 07 §4.5 |
| `confidence.py` | 置信度：颜色带、来源追溯、logprob×成功率合成、低置信触发干预 | `band / color / combine / apply_to_record / policy` | 07 §4.6 |
| `evidence.py` | 多模态证据（截图/示意图/diff）、证据哈希归约、因果解释、反馈 | `EvidenceService.add / add_diff / add_diagram / counterfactual / sign_range` | 07 §4.7 |
| `groups.py` | 多 Agent 任务聚合（≤10 Agent）、进度/资源/卡住检测、分组准确率 | `GroupService.aggregate / grouping_report` | 07 §4.8 |
| `sharing.py` | 共享邀请（view/approve/manage 级联）、过期/撤销、协作事件账本 | `ShareService.invite / accept / can / record_event / events` | 07 §4.9 |
| `analytics.py` | 用户行为仪表盘（脱敏 id）、CSV/PDF 导出、快照 | `dashboard / export_csv / export_pdf / anonymise` | 07 §4.10 |
| `suggestions.py` | 改进建议（3 次阈值、7 天冷却）、用户偏好落盘 | `SuggestionService.scan / accept / dismiss / preference_for` | 07 §4.11 |
| `compliance.py` | 合规导出（PDF/CSV/chain）、CID 复算、存证锚点与离线校验 | `ComplianceService.export / attest / verify / cid_for` | 07 §4.12 |

**边界纪律（与第 3 节同一条）**：新模块只依赖标准库与 `feature_store`；**不 import `server.py`**
（装配方向只能是 `server.py → 模块`）；`secretariat` 的私钥只进本地 keyring，任何模块都不得把
私钥写进记录/审计/日志/API 响应。

**前端扩展**：`web/ext/` 按插件注册（`TraceLensExt.register`），`index.html` 已一次性引入
`ext/core.js` 与各功能文件；新增面板不改 `index.html`。6.0 接口（`onRender / t / formatConfidence /
extRequest / can`）由 `app.js` 提供，请求统一走 `extRequest`（CSRF/超时/错误体一处收口）。

### 21.2 存储布局（`var/features/`，与既有三套存储分开）

| 层 | 位置 | 纪律 |
|---|---|---|
| ActionRecord | `var/records.jsonl`（既有 `store.py`） | 不变：ActionRecord 的唯一事实源 |
| 审计链 | `var/audit/audit-YYYYMMDD.jsonl`（既有 `audit.py`） | 不变：只记"谁做了什么决定"，白名单事件 |
| 5.0 实体 | `var/features/…`（新 `feature_store.py`） | 见下；**不写 SQLite** |

`var/features/` 下：`secretariat/{identities,delegations,letters}.jsonl`（Ledger）、
`secretariat/letters/<id>.json`、`secretariat/keyring.json`（0600，私钥）；
`groups.jsonl`、`shares.jsonl`、`collab_events.jsonl`（Ledger）、`suggestions.jsonl`、
`preferences.jsonl`、`exports.jsonl`（Ledger）、`anchors.jsonl`（Ledger）；
`evidence/index.jsonl` + `evidence/files/<id>.<ext>`；`rollback/journal.jsonl`。
逐文件字段与键见 [TL-DM-002 §6.5](./02-data-model.md)。

**账本纪律**（R3，与审计链同一口径）：

1. **Ledger**（身份/委托/认定书索引/协作事件/导出记录/锚点）每条带 `seq` / `prev_hash` /
   `entry_hash`；`entry_hash = sha256(canonical_json(entry 去掉 entry_hash))`；首条 `prev_hash` =
   64 个 `0`（`GENESIS_HASH`）；`verify()` 离线复算，**断链只报告、绝不重建**。
2. **Store**（其余实体）一行一个实体、同 `id` 最后写入胜出、`append` 只追加；跨进程文件锁
   （Windows `msvcrt` / POSIX `fcntl`）；压实用临时文件 + `fsync` + `os.replace`。
3. **没有 delete**：撤销一律用状态字段（`revoked_at` / `status` / `closed_at`），不抹行。
4. 单行上限 256 KiB；崩溃半行只坏一行；用户 id 在分析/导出里用 `sha256(id)[:12]`。

### 21.3 HTTP 路由

5.0 新增 **37 个端点（T1–T37）**，另加 **T38：`/health` 的 `expansion` 块增补**，全部按既有纪律：只加 `GET`/`POST`、写操作 `POST` + CSRF、
权限用既有 **8 个能力**（不新增能力、不动 `auth.py` 角色矩阵）、非 2xx 走统一错误体
`{code, message, detail, request_id}`。**逐条端点表（方法/权限/入参/响应/专属错误码）的唯一来源**
是 [TL-SEC-005 §5](./07-secretariat-and-extensions.md)；机器可复核的版本是
`docs/api_contract.json`（`python tools/gen_api_contract.py --check` 必须退出 0）。

错误码：5.0 新增 8 个运营域共 **54 个码**（`secretariat` / `rollback` / `evidence` / `groups` /
`sharing` / `analytics` / `suggestions` / `compliance`，另 `trust` 域 6 个存储码），清单见
[07 §7](./07-secretariat-and-extensions.md)。口径：字段/参数 400、不存在 404、状态冲突 409、
越权 403、体积 413、存储失败 500；**断链一律 500 且不出证据**（沿用第 6.4 节 M6 口径）。
每个码要有 `<域>.error.<code 小写>` 双语键 + 至少一条测试（`tools/i18n_lint.py` 与
`tests/test_expansion_i18n.py` 各查一面）。

### 21.4 `/health.expansion`（扩展块）

`GET /health`（以及 `/healthz`）在既有字段之外新增 **`expansion`** 块，逐子系统如实报告
"启用了没有、落盘成不成立、有几个问题"，**不报假健康**：

```jsonc
"expansion": {
  "enabled": true,                 // 服务端是否装配了 5.0 子系统（--no-* 关掉时为 false）
  "features_root": "var/features", // 落盘根（相对仓库/数据目录）
  "secretariat": {"enabled": true, "identities": 1, "delegations": 0, "letters": 0,
                  "keyring": "ok", "chain": {"ok": true, "problems": 0}},
  "groups": {"enabled": true, "open": 1},
  "sharing": {"enabled": true, "active": 2},
  "suggestions": {"enabled": true, "pending": 3, "preferences": 1},
  "analytics": {"enabled": true, "records": 42},
  "compliance": {"enabled": true, "exports": 2, "anchored": 0, "unanchored": 2},
  "evidence": {"enabled": true, "items": 5},
  "rollback": {"enabled": true, "journal": 1},
  "problems": []                   // 逐条人话；非空时 status 应降级（沿用第 6.6 节口径）
}
```

原则：**块里只放计数与状态，不放密钥、不放记录正文**；`anchor_state` 默认 `unanchored`
（离线降级，见 21.5）；链断时 `chain.ok=false` 且 `problems` 写明"哪条账本第几条断"。

### 21.5 本轮显式降级与已知限制（与 07 §10 同一张表，交付报告必须出现）

| 规格书要求 | 本轮实际 | 为什么 / 界面纪律 |
|---|---|---|
| HSM / TEE 提供 Ed25519 密钥 | 纯 Python Ed25519 + 本地密钥库 `keyring.json`（0600） | 离线与零依赖；密钥库是可替换的抽象（生产应换 TEE/HSM 提供 seed） |
| RFC 3161 可信时间戳 | `tsa_timestamp = "local-tsa:<ISO>"`，`verify` 标 `tsa_state="local_only"` | 离线无 TSA 服务；**禁止**界面宣称"可信时间戳已获取" |
| 截图 API 抓屏 | 执行端/用户提供图片（`data_uri`），服务端校验魔数并落盘 | 无网络、无浏览器自动化；`provided_by` 如实标注来源 |
| Graphviz / Mermaid 渲染 | 服务端生成 Mermaid 文本 + 自绘 SVG（纯标准库） | 不引入第三方渲染器 |
| 区块链上链（IPFS + 以太坊） | `cid_for()` 真实可复算 CIDv1；`anchor_state ∈ {unanchored, anchored}`，默认 `unanchored` | 无网络、无钱包；**禁止**伪造交易哈希（决定 ≠ 生效） |
| 回滚真的撤销外部系统影响 | 回滚记录 + 本地逆向动作回执 `simulated_local` | 没有外部系统凭据；回执状态如实显示，**不许写"已恢复"** |
| 百万记录 ≤3s | 20 万条基准实测（CI 放宽到 6s），报告实测值 | JSONL + 内存索引的既定架构；产品定位每天几十~几千条 |
| 桌面 GUI 重新打包 | 不重打包；源码形态下界面实测通过 | 打包需网络/签名链，超出本轮；旧包在 `release/` |

**"做不到就说做不到"仍是 5.0 的第一条文案纪律**：任何面板不得把上表任何一行显示成已具备的能力。

### 21.6 5.0 的验证闸门（文档不代替测试）

| 闸门 | 命令 | 通过标准 |
|---|---|---|
| schema | `python tools/validate_schema.py` | 50 项检查全通过、退出 0（20 个新字段 + `rollback` 不破坏既有夹具） |
| i18n / 文案 / 错误码 | `python tools/i18n_lint.py` | 退出 0；硬编码扫描覆盖 `web/ext/*.js` / `*.css`；新增 8 域双语键齐备 |
| 接口契约 | `python tools/gen_api_contract.py --check` | 退出 0（T1–T38 与 `docs/api_contract.json` 一致） |
| 规格书验收 | `python -m unittest tests.test_expansion_acceptance -v` | 每条规格书验收项一条断言，**打印实测值**（覆盖率、成功率、耗时、准确率） |
| 5.0 i18n 细节 | `python -m unittest tests.test_expansion_i18n -v` | 无 parts 漂移、新域双语齐备、新错误码有键、`web/ext/*.js` 零 CJK |
