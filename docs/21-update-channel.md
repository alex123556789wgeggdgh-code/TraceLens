# 21 · 版本更新通道（UPDATE-CHANNEL-1）

> 状态：0.6.0 实现
> 目标：**加一个功能不再需要重新下载 151 MB 安装包、不需要走一次 NSIS 覆盖安装**。
> 一句话口径：**能热更的层做热更，不能热更的层说清为什么，绝不假装更新成功。**

---

## 1. 现状为什么必须改

0.5.0 的分发形态是「NSIS 安装包 + PyInstaller 单文件 sidecar」，界面资产
（`agent_explainer/web/`）在构建时用 `--add-data` **打进了 exe 内部**。后果：

| 改动内容 | 实际需要的动作 | 代价 |
| --- | --- | --- |
| 改一行 CSS / 加一个面板 | 重打 sidecar → 重打 NSIS → 用户重新下载安装 | 151 MB + 一次 UAC 提权 |
| 加一个导出格式（涉及 `audit.py`） | 同上 | 同上 |
| 改 Electron 主进程 | 同上 | 同上（合理，壳层本来就该走安装包） |

**这不是「不方便」，是迭代速度被分发形态锁死**：一次文案修改的成本等于一次发版。

---

## 2. 三层边界（本设计的核心表）

| 层 | 内容 | 变化频率 | 更新方式 | 需要重装吗 |
| --- | --- | --- | --- | --- |
| **L-web** | `web/`（`app.js` / `styles.css` / `ext/*.js` / `i18n/*` / 静态资源） | **极高** | sidecar 以 `--web <版本>/web` 加载外部目录 | **不需要** |
| **L-runtime** | `runtime/agent_explainer/`（Python 业务包源码） | 高 | sidecar 启动器把 `runtime/` 置于 `sys.path` 首位，业务模块全部从磁盘加载 | **不需要**（需先按 §10 启用） |
| **L-shell** | Electron 主进程、Chromium、Node 运行时 | 极低 | NSIS 覆盖安装 | 需要 |

L-web 能热更，是因为 `server.py` 早就有 `--web <dir>` 参数（`TraceLensServer(web_dir=...)`），
只是打包时把它固定成了 exe 内部路径。**本设计没有新增一个下载器，而是把已有的
「静态目录可外部指定」这件事接进了启动链路。**

L-runtime 能热更，前提是 sidecar 构建时**不把业务代码冻进 PYZ**。0.6.0 的
`tools/build_sidecar.py --external-runtime` 产出薄启动器：exe 里只有 Python 解释器 +
标准库 + 第三方（reportlab/PIL/numpy），`agent_explainer` 整个包以源码形式落在
`resources/runtime/`，运行时由 exe 从磁盘 import。**业务代码在磁盘上，才谈得上替换。**

---

## 3. 目录布局

用户的**数据**与程序的**版本**彻底分开：更新只动 `updates/versions/`，
永不触碰 `data/`（记录、审计链）与 `features/`（身份账本、密钥库）。

```
%APPDATA%/TraceLens/            ← Electron userData
├── data/                       ← 已有：records.jsonl / audit.jsonl / settings.json
├── features/                   ← 已有：secretariat 身份账本 / keyring.json
└── updates/
    ├── config.json             ← 更新源、是否自动检查、通道
    ├── index-cache.json        ← 最近一次成功拉到的 index（离线时用于展示"已知最新版"）
    ├── current.json            ← 激活指针（唯一的真相源）
    ├── staging/<版本>/          ← 下载与解压中的临时区（失败即整目录删除）
    └── versions/
        ├── 0.6.0/
        │   ├── manifest.json
        │   ├── web/…
        │   └── runtime/agent_explainer/…   ← 可选
        └── 0.6.1/
```

`current.json`：

```json
{
  "schema": "tracelens.update-state/1",
  "version": "0.6.1",
  "previous": "0.6.0",
  "activated_at": "2026-11-16T10:00:00Z",
  "pending_confirm": true
}
```

- `pending_confirm: true` = 新版**还没被证明能起来**。启动成功后由主进程改成 `false`；
  启动失败且仍是 `true` → **自动回退 `previous`**（见 §6）。
- 没有 `current.json`（首次安装）= 用安装包自带的内置版本，热更机制处于「未启用」态。

---

## 4. 更新包 `.tlpkg`（其实就是 zip）

选 zip 的理由：Windows 双击能开、7-Zip 能看、Python `zipfile` 与 Node `zlib` 都能读，
不需要发明新格式，也不需要给运行时加依赖。

```
manifest.json          ← 必须，且在包根
web/…                  ← 必须
runtime/agent_explainer/…   ← 可选（capabilities 里有 "runtime" 时才允许）
```

`manifest.json`：

```json
{
  "schema": "tracelens.update-package/1",
  "version": "0.6.1",
  "channel": "stable",
  "min_host": "0.6.0",
  "capabilities": ["web", "runtime"],
  "created_at": "2026-11-16T09:00:00Z",
  "notes": "导出面板支持自定义时间范围",
  "files": [
    { "path": "web/app.js", "size": 377291, "sha256": "031162df…" }
  ]
}
```

- `files[]` 覆盖包里**每一个**文件（不含 `manifest.json` 自身）。安装时逐条核对，
  缺一个、多一个、哈希不对，一律整包拒绝。
- `min_host` 是**宿主能力下限**，不是版本号比较的装饰：宿主低于它时安装器必须拒绝并
  说明「这个功能需要更新版本的 TraceLens 本体」，**不允许装上去再崩**。

`index.json`（更新源根目录）：

```json
{
  "schema": "tracelens.update-index/1",
  "channel": "stable",
  "latest": "0.6.1",
  "releases": [
    { "version": "0.6.1", "file": "tracelens-0.6.1.tlpkg", "size": 812345,
      "sha256": "…", "capabilities": ["web", "runtime"], "min_host": "0.6.0",
      "notes": "…", "published_at": "…" }
  ]
}
```

`latest` 只是给界面显示用的提示；**是否可装一律按 `releases[]` 里那一条的实际字段判定**，
不信任 `latest` 单方面声明（防止 index 写错就把旧包推给新宿主）。

---

## 5. 安全与诚实规则（六条硬约束）

1. **逐文件哈希**：包内每个文件 `sha256` 必须与 manifest 一致，任何一条不符 → 整包拒绝，
   已解压的目录整个删除，`current.json` 一字不动。
2. **包哈希**：下载完成后先比对 index 声明的整包 `sha256`，不符立即中止（**在解压之前**）。
3. **路径穿越**：条目名拒绝绝对路径、盘符（`C:`）、`..` 段、反斜杠混用、
   以及解压后会落在目标目录之外的任何形态（zip slip）。拒绝的包**整包作废**，
   不做「跳过坏条目继续装」。
4. **版本单调**：默认拒绝安装 `<= current` 的版本（幂等安装除外：同版本且哈希一致视为无事发生）。
   允许降级必须显式 `--allow-downgrade`，且 UI 上必须显示这是降级。
5. **能力门禁**：`capabilities` 含 `"runtime"` 但宿主 sidecar 不支持外部 runtime →
   拒绝，并说明原因（**不能装了 web 就宣布更新完成**，那是半装状态）。
6. **不静默**：任何失败都返回结构化原因（`reason` 码 + 人话），不吞异常、不返回假成功。
   没有网络、源不可达、包损坏、签名缺失——各是各的码，界面按码显示。

7. **缓存只救网络，不救手误**：`index.json` 的本地缓存在**仅当源是 `http(s)`** 且不可达时
   才启用（网线断了是临时故障）。**本地目录读不到直接失败**——那几乎只有一个原因：
   路径写错了。此时退缓存会把「配置错了」包装成「源正常（来自缓存）」，用户照着旧版本号
   点安装，只会得到第二个错。

**本版不做签名验证**（诚实申报）：`.tlpkg` 目前只做 sha256 + 传输通道信任。理由是本产品
离线优先、更新源由管理员指定（本地目录或内网），PKI 与密钥轮换的成本与收益不匹配。
未来要加签名，位置已经留好：`manifest.json` 预留 `signature` 字段，校验顺序插在 §5.2 之后。

---

## 6. 激活、确认、自动回滚（最关键的一段）

更新最容易骗自己的地方是「装完了」——装完不等于能跑。所以状态机分三步：

```
校验通过 → 解压到 versions/<新>/ → 写 current.json(pending_confirm=true) → 重启应用
                                                                    ↓
   ┌────────────────────────────────────────────────────────────┐
   │ 新版启动成功（sidecar /health 200 且界面加载完成）           │
   │   → current.json.pending_confirm = false                   │
   └────────────────────────────────────────────────────────────┘
   ┌────────────────────────────────────────────────────────────┐
   │ 新版启动失败，且 current.json.pending_confirm 仍为 true      │
   │   → 自动把 current.json 指回 previous（原子 rename）         │
   │   → 记一条 rollback 事件（版本、原因、时间）                 │
   │   → 用旧版重启一次；旧版再失败就正常报错，不无限重试           │
   └────────────────────────────────────────────────────────────┘
```

- **只在 `pending_confirm` 为真时自动回退**，用户手动回滚走另一条路径，互不干扰。
- 回退次数硬上限 1 次（用 `current.json` 里的 `rollback_attempted` 标记），避免启动风暴。
- 回退后**不删除**坏版本目录：留着取证（这也是为什么 `versions/` 里可以有多个版本，
  激活的永远只是 `current.json` 指的那一个）。

---

## 7. 更新源

| 类型 | 写法 | 用途 |
| --- | --- | --- |
| 本地目录 | `file:///D:/tracelens-updates` 或直接 `D:\tracelens-updates` | U 盘、共享盘、构建机输出目录 |
| 内网 HTTP | `http://10.0.0.8/tracelens/updates/` | 团队内网静态服务器 |

**默认不配置任何源**（`config.json` 里 `source: null`）。这与产品的「离线纯净契约」
一致：`agent_explainer` 包源码里禁止出现明文 URL（`tests/test_trace_adapter.py::OfflinePurityTests`），
所以**更新模块不放 Python 包内**，放 Electron 壳（Node 侧），且**不硬编码任何默认地址**。
地址由用户/管理员显式填写，界面上的输入框是唯一入口。

---

## 8. 明确不做的事

| 不做 | 为什么 |
| --- | --- |
| 后台静默自动安装 | 与「用户知情」冲突；本版只在用户点「检查/安装」时动作，自动检查也只在界面上提示 |
| 增量二进制 diff | Python 源码层热更已经不需要 diff；壳层变更频率极低，做 diff 的复杂度不划算 |
| 签名与证书链 | 见 §5 末尾，已留字段但不实现 |
| 更新失败时"尽力恢复" | 不做任何猜测性修复；要么回退到已知可用版本，要么原样报错 |
| 改用户数据目录 | 更新只写 `updates/versions/`，`data/` 与 `features/` 一行不碰 |

---

## 9. 验收口径

一条更新通道算「能用」，必须同时满足：

1. `web` 包装上后，**不重启安装包**、只重启应用，界面出现新功能（可用 `--web` 指向证明）。
2. 篡改包内任意一个字节 → 安装被拒，`current.json` 未变，旧版本照常可启动。
3. 手工改 `current.json` 指向一个坏目录 → 应用自动回退上一版并留下 `rollback` 记录。
4. 降级需要显式开关；不带开关的降级被拒。
5. 无网络 / 源不存在 → 返回明确原因码，界面显示人话，**不显示「已是最新」**。
6. 全部校验逻辑有测试覆盖（Python 打包器/安装器 + Node 引擎），且在无 GUI 环境下可跑。

---

*0.6.0 · 更新通道设计 · 与 `desktop/updater.js`、`tools/make_update_package.py`、
`desktop/updater-cli.js` 三处实现对读*

---

## 10. 当前生效范围（诚实申报，勿删）

写完 §1–§9 之后必须回答一个问题：**这套东西现在到底能干什么。** 实测结论：

| 层 | 现在能用吗 | 依据 / 阻塞 |
| --- | --- | --- |
| **L-web** | **能用** | `server.py --web <dir>` 是既有能力；Electron 启动时把「已激活版本」的 `web/` 传下去；`desktop/lib/selftest.mjs` 54 项断言 + `tests/test_update_face.py` 30 项覆盖安装/校验/回退全链 |
| **L-runtime** | **暂未生效** | 工具链已就绪：`tools/build_sidecar.py --external-runtime` + 薄启动器（`LAUNCHER_SOURCE`）。启动器逻辑已在源码态实测通过（`launcher.py --sidecar --port 0` 能从磁盘加载 `runtime/agent_explainer` 并打出 bootstrap 行）。**但重新构建 sidecar 被既有契约门禁拦住**：`gen_api_contract.py --check` 当前有 2 项**历史遗留 FAIL**（`API_PATH_COMPLIANCE_EXPORT` / `API_PATH_EVIDENCE` 声明未挂载；`app.js` 调用 `/api/analytics` `/api/compliance` `/api/evidence`），而 `build_sidecar.contract_gate()` 要求 0 FAIL。这两项与更新通道无关（0.5.0 就存在，R8 期间已由 PM 裁决为「恰 2 项业务 FAIL」基线），**本版不擅自放宽门禁、也不越权挂新路由** |
| **L-shell** | 需要重装 | Electron 主进程与 Chromium 不走热更 |

**这意味着什么**：本版交付后，「加一个面板 / 改文案 / 换导出说明 / 调交互」这类改动**不再需要重装**；
「改 `audit.py` / `server.py`」这类改动**仍然需要**。不要对外宣称「以后任何改动都不用重装」——那是假的。

**启用 L-runtime 的完整条件**（按顺序）：

1. 把 `gen_api_contract.py --check` 的两项历史 FAIL 处理掉（真挂上端点，或按治理流程登记基线；
   **不要**改 `contract_gate()` 去容忍，那等于把发版门禁变成装饰）。
2. `python tools/build_sidecar.py --build --onedir --external-runtime` → 产物为「薄 exe + runtime/」。
3. 把 `desktop/main.js` 里 `updater()` 的 `capabilities: ['web']` 改为 `['web', 'runtime']`。
4. 打一次完整安装包（**这一次要重装**），此后 Python 层即可热更。
5. 验证：`python tools/make_update_package.py --with-runtime` 出的包能被装上，
   且 `runtime/agent_explainer/server.py` 的改动在重启后生效。

`main.js` 里现在写的是 `capabilities: ['web']` —— 这是**如实声明**，不是遗漏。虚报的后果是
管理端推了一个需要 runtime 的包，装上去只换了界面，功能半残，而界面还说"更新成功"。
