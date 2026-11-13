# TraceLens 桌面壳（子任务 5.3 / t12）

这个目录**只是壳**：它不含任何界面代码。界面本体是产品自带的单页应用
（[../agent_explainer/web/](../agent_explainer/web/)），桌面壳负责把它需要的
Python 产品服务拉起来，等健康检查通过后再加载同一个本地地址。
所以桌面端与浏览器端看到的东西、走的数据、留下的审计完全一致。

## 文件

| 文件 | 作用 |
| --- | --- |
| `main.js` | 主进程：单实例锁 → 起 sidecar → 等 `/health` → 建窗口加载 UI → 退出清理 |
| `sidecar.js` | 拉起与看护 Python 产品服务；开发模式用系统 Python，打包模式用 resources 里的 exe |
| `preload.js` | 只暴露 4 个白名单 IPC（信息 / 状态 / 日志 / 退出） |
| `package.json` | `npm start` = `electron .`；运行时依赖为空（产品本体是 Python） |
| `electron-builder.yml` | NSIS 安装包 + portable 压缩包；sidecar 走 `extraResources`；构建期不签名 |
| `build/resources/sidecar/` | `tools/build_sidecar.py` 的产物落点（`BUILD-INFO.json` 记录构建口径） |
| `build/dist/` | electron-builder 与签名脚本的产物落点（含 `SHA256SUMS.txt`、`SIGNATURE-STATUS.json`） |

## 启动流程（与验收口径一致）

1. `requestSingleInstanceLock()`：抢不到锁就把已有窗口唤到前台并退出，
   绝不重复起第二个 sidecar 抢端口。
2. 起 sidecar：
   * 开发模式 `python -m agent_explainer --sidecar --port 0 --data-dir <userData>/data --mode local --demo zh`
     （解释器可用 `TRACELENS_PYTHON` 指定）；
   * 打包模式 `resources/sidecar/agent-explainer-sidecar(.exe)`，参数完全相同。
     两种模式**只有一行差别**，免得"开发能跑、打包起不来"。
3. 读 sidecar 打印的机器可读行 `{"tracelens_bootstrap":{…}}` 拿到真实端口
   （`--port 0` = 让系统挑空闲端口，桌面端最怕"端口被占"）。
4. 轮询 `GET /health` 直到 `200 + status:ok` 才 `loadURL`。
   **只看进程活着不算就绪** —— 界面白屏多半就是跳过了这一步。
5. 退出：`will-quit` / `window-all-closed` 里 terminate 子进程。
   记录日志与审计链都是"追加一行 + fsync"，进程被终止不丢数据。

## 开发模式

```bash
cd desktop
npm install          # 只装 devDependencies（electron、electron-builder）
npm start            # 起窗口；需要系统 Python（3.11+）
npm run selfcheck    # 无人值守验收：起窗口→读页面→打印一行 JSON→按结果 0/1 退出
```

`npm run selfcheck` 是这台机器"到底能不能跑"的证据来源：它不开人工窗口，
把页面标题、卡片数、`/health` 摘要打印成一行 `{"tracelens_desktop_selfcheck":{…}}`。

环境变量：`TRACELENS_PYTHON`（解释器）、`TRACELENS_DATA_DIR`（数据目录）、
`TRACELENS_MODE`（`local`/`team`）、`TRACELENS_DEMO`（`zh`/`en`/`none`）、
`TRACELENS_PORT`（默认 `0` 自动选空闲端口）、`TRACELENS_SELFCHECK=1`。

同名开关（优先级高于环境变量，从桌面 Shell 直接拉起进程传不进环境变量时用）：
`--selfcheck-out <文件>`（把验收 JSON 追加写文件，stdout 拿不到时的兜底）、
`--data-dir`、`--python`、`--mode`、`--demo`、`--port`。

```bash
# 例：从桌面 Shell 拉起无人值守验收（stdout 抓不到，靠文件取结果）
node_modules\electron\dist\electron.exe . --selfcheck ^
  --selfcheck-out "%TEMP%\tracelens-selfcheck.jsonl" ^
  --data-dir "%TEMP%\tracelens-desktop-data" --python D:\python\python.exe
```

## 打包

```bash
python ../tools/build_sidecar.py --vendor --build --onedir   # 产品服务 → exe
npm install                                                  # devDeps：electron + electron-builder
npm run dist                                                 # NSIS + portable
pwsh -NoProfile -File ../tools/sign_and_hash.ps1              # 自签名 + SHA256SUMS
pwsh -NoProfile -File ../tools/sign_and_hash.ps1 -Verify      # 复核
```

打包配置里的两个"受限环境"开关（5.3b / t26 实测加上的，都不是取巧）：

* `npmRebuild: false` —— 本产品**运行时零第三方依赖**（`package.json` 里 `dependencies` 为空），
  electron-builder 默认的 node-gyp 重建毫无意义，而且在受限令牌下它 `fork` 子进程直接
  `spawn EPERM`（`app-builder-lib/util/rebuild.ts`），整场构建还没开始就红。
* Electron 下载缓存默认落在 `%LOCALAPPDATA%\electron\Cache`；那里不可写时构建报
  `EPERM: mkdir '…\electron\Cache\<sha256>'`。加参数指到可写目录即可（实测下载 100%）：

  ```bash
  npx electron-builder --win --x64 --config.electronDownload.cache="D:\tracelens-cache\electron"
  ```

  仍然绕不过的一层：electron-builder 26.18 打包时要 spawn `npm query` 收集 node_modules
  （`node-module-collector`），受限令牌下 `spawn EPERM`，根配置里没有 `nodeModules` 键可关
  （实测报 `unknown property 'nodeModules'`）。这台机器上因此**没有产出 NSIS/portable**；
  正常开发机 `npm install && npm run dist` 即可。
  副作用提醒：构建中途的 `build/dist/win-unpacked` 只有 Electron 运行时（`resources/` 空、
  没有 `app.asar`），**不是可用应用**，别把它当便携版打包交付。

**打包状态以 [../docs/05.3-release-checklist.md](../docs/05.3-release-checklist.md) 为准**：
哪一步真在这台机器上跑通了、哪一步被环境卡住（含可复现命令），清单里逐条写着，
不写"已打包"这种没有证据的话。

## 已知边界（照抄清单，别在代码里假装有）

* PyInstaller **单文件**模式在这台机器的沙箱里跑不起来（引导程序建不出解包目录：
  `[PYI-…:ERROR] Failed to create parent directory structure.`），
  所以交付用 **单目录（onedir）** 布局，由 NSIS 安装包整体打包；
  Electron 两种落点都认。
* 签名是 **自签名**：`Get-AuthenticodeSignature` 状态为 `NotTrusted` 属预期，
  只能证明"文件没被改过"，**不是**受信任发布者签名，SmartScreen 照样报警。
* Electron 二进制要 npm 联网下载；下载不了就是"未产出安装包"，清单里如实标注。

## 安全口径

`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`；
只加载回环地址，窗口内越界导航一律拦下；不调用外部浏览器打开链接；
`package.json` 的运行时依赖为空（产品本体是 Python sidecar，不往 npm 发包，
`private: true`）。
