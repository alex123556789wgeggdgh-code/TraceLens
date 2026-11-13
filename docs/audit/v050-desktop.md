# TraceLens 0.5.0 — T2 桌面安装包与便携版：安装包未构建，根因＝受限沙箱禁 Node 管道 stdio spawn（`nodeModulesCollector.ts:409 streamCollectorCommandToFile` EPERM）

状态（实测时刻 2026-11-12 02:12–02:32，Asia/Shanghai）：

* **F1 必修已修 + 实测通过**（sidecar 级三条：账本落 userData、程序目录零新增、模拟升级不丢账本与密钥库，§4）。
* **F2 必修已修 + 字节层实测通过**（卡内处方 `PYTHONUTF8` 实测对冻结态 exe 无效，队长已收回；改为 `sidecar.js` 的 UTF-8→GBK 流式解码兜底，§5）。
* **桌面产物（NSIS Setup.exe / portable.exe）在本会话不可构建** —— 根因：**受限沙箱禁 Node 的管道 stdio spawn**，`electron-builder` 无法运行（§1：构建实录 + 最小复现三条 + 0.4.5 构建的对照证据）。**产物已由队长在 danger-full-access 会话构建成功**（用户批准，2026-11-12 02:30–03:25）：Setup / portable / portable-src / blockmap 四件齐，F1/F2 已进 asar，`--features` 已进 `start.cmd`，实录见 §3.2；**壳窗口级与便携版启动实测仍受环境限制**（§3.2 末：Electron GUI 在本会话零输出退出；NSIS 安装目标目录属 DSH 服务账号，用户的应用要在他的桌面会话里安装）。
* 全量测试套件在本会话**测不出 t1/t2 基线**（954 / 16 红 / 209 错 / 3 skip，全部是同一件环境事：§3.1 最小复现）。
* 本轮**没有**改动 `desktop/build/dist` 任何内容（队长明令，构建期间不碰）；0.4.5 三份交付件哈希逐条重算一致（§1.3）。

> **读表前必看（2026-11-12 R2-1 追加，防被读成假数据）**：下面这张"任务卡验收对照"是**本会话
> （受限沙箱）那一轮**的结论，其中"两个 exe 未产出 / NSIS 未做构建"两条**已被同日的提权构建取代**——
> 0.5.0 的 `TraceLens-0.5.0-win-x64-Setup.exe` = 151,838,192 字节 /
> `37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d`，
> `TraceLens-0.5.0-win-x64-portable.exe` = 137,883,824 字节 /
> `8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52`（逐字取自 `release/SHA256SUMS.txt`，
> 2026-11-12 复算命中）。构建实录与 asar 内 F1/F2 核对见本文 §3.2；交付指引以 README §12 与
> `docs/05.3-release-checklist.md` §12 为准；本文其余历史记录（含原始报错）保留，那是复现依据。

任务卡验收对照：

| 验收项 | 结论 |
|---|---|
| 两个 exe 产出、字节数只增不减 | **未产出**（环境阻塞，见 §1；不许造假，也不拿 0.4.5 的产物冒充） |
| 文件名版本号 0.5.0 | 构建期口径已核对：`package.json version=0.5.0` → `artifactName` 模板 `TraceLens-${version}-win-${arch}-Setup/portable`（§2） |
| portable 在 ASCII 路径真跑四条 | **未做**（Electron 起 sidecar 需要管道 stdio spawn，本会话 EPERM；ASCII 目录在本会话也不可得，§1.3）。探针已交付：`.tmp/t3-desktop-run.py`（§6） |
| NSIS 只做构建成功性 + 哈希核对，不安装 | 未做（构建未成功）；**未安装实测**这条本来就按卡执行 |
| F1（队长追加，必修） | **PASS**（§4，实测原始输出全贴） |
| F2（队长追加，必修） | **PASS**（§5，字节层证据：修前 699B cp936 / 14 个 U+FFFD → 修后 0 个 U+FFFD、中文逐字可读） |
| 回归 | 见 §3：桌面壳静态契约 7/7 过、ArtifactTests 只剩 t1 在案的已知红；**全量套件在本会话测不出基线**（§3.1，环境阻塞，已在提权会话待复跑） |

---

## 1. 阻塞：electron-builder 在受限沙箱里跑不起来（根因定位到 syscall）

### 1.1 构建实录（`desktop/build/eb-050.log`，26 秒 exit 1）

```
• electron-builder  version=26.18.0 os=10.0.22631
• loaded configuration  file=...\desktop\electron-builder.yml
• skipped dependencies rebuild  reason=npmRebuild is set to false
• packaging       platform=win32 arch=x64 electron=44.10.2 appOutDir=build\dist\win-unpacked
• downloaded electron zip extracted successfully  output=...\build\dist\win-unpacked
• searching for node modules  pm=npm searchDir=...\desktop
• node module collection failed, trying next search directory/approach  pm=npm error=spawn EPERM
• searching for node modules  pm=traversal searchDir=...\desktop
• using manual traversal of node_modules to build dependency tree
• no node modules returned while searching directories  searchDirectories=[""]
⨯ spawn EPERM     failedTask=build stackTrace=Error: spawn EPERM
    at ChildProcess.spawn (node:internal/child_process:458:11)
    ... nodeModulesCollector.ts:409 streamCollectorCommandToFile
ELAPSED=26s EXIT=1
```

调用链读法：`resolveFirstMatchingCollection` 依次试 `[npm, traversal]`。`npm` 那条**抛异常**（EPERM），
`traversal` 返回 0 个模块（本项目 `dependencies: {}`，且 `files` 显式排除 `node_modules`）→
无 fallback → 把第一个异常（EPERM）原样抛出 → 构建终止。0.4.5 那次 `npm` 探针返回 0 模块而**不抛**，
所以走到 `no node modules returned` 正常继续（对照 `desktop/build/eb3.log` 第 7-12 行）。

### 1.2 最小复现（同一会话逐条实测，2026-11-12 02:1x）

| 命令 | 结果 |
|---|---|
| `node -e "execSync('cmd /c ver')"`（默认 pipe） | `pipe EPERM: EPERM` |
| `node -e "spawn(7za.exe, ['i'], {stdio:['ignore','pipe','pipe']})"`（electron-builder 抽 winCodeSign/NSIS 用的就是这条） | `Error: spawn EPERM { errno: -4048, code: 'EPERM', syscall: 'spawn' }` |
| `node -e "spawn('cmd',['/c','echo hi'],{stdio:'inherit'})"` | 正常输出 `hi` |

即 DSH 沙箱文档写明的边界：**受限模式下不允许「捕获子进程输出」的 spawn**（管道）。
electron-builder 全程要抓 `npm` / `7za` / `makensis` 的输出，所以在本会话**结构性不可用**——
不是配置能绕的（`npmRebuild: false` 与 `!node_modules/**` 已经是最小化配置，`forceCodeSigning: false` 也挡不住这一步）。

同一条边界也决定了两件连带事实：

* **打包后的桌面壳在本会话起不来**：`desktop/sidecar.js:149` 用 `stdio: ['ignore','pipe','pipe']`
  读 sidecar 的 bootstrap 行（Node 管道 spawn）→ 本会话必 EPERM。所以「portable 真跑四条」只能在提权会话做。
* **Python 的 `subprocess` 管道不受影响**（实测：§4/§5 的探针全部用 `stdout=PIPE` 跑通）→
  F1/F2 的 **sidecar 级实测在本会话做实**。

### 1.3 附带限制（写清楚，免得下游照抄踩坑）

* 本会话是 `workspace-write`：实测写 `C:\TraceLens050` 与 `%TEMP%\t3-ascii` 均 `PermissionDenied`；
  `E:` 盘 **8.3 短名未启用**（`dir /X E:\` 无短名列）→ 卡里给的「纯 ASCII 目录」两条路在本会话都不可得。
* 失败构建的副作用（如实记账）：`build/dist/win-unpacked` 里的 0.4.5 内容被 electron-builder 的 packaging 步骤清掉，
  现在是裸 electron 44.10.2（`electron.exe` 246,294,016 B，**无** `resources/app.asar`、**无** `resources/sidecar`）。
  重建会覆盖。**dist 里的 0.4.5 交付件全部未动**——本轮用 `Get-FileHash -Algorithm SHA256` **逐条重算**，
  与 `SHA256SUMS.txt`（t1 留档）逐字一致：

  ```
  78313951bd1baf553a8e90df9c8060421af7b59b6ea517d600aa3befcb8f4edb  TraceLens-0.4.5-win-x64-Setup.exe   (151,535,616 B)
  6394bf6cf5fb1474f3694cea91c704b78ff631d9e8fa6cade9bba357200acf45  TraceLens-0.4.5-win-x64-portable.exe (137,607,264 B)
  5f8a6044ab2fef486ff9abd4bb2d6e03e0cae3eeca0faba506f1df6284483d54  TraceLens-SelfSigned.cer             (814 B)
  09eb4289305ef36d54a5823dbb9809a1c61ee60816c53ca898fc1d9b355f05a8  TraceLens-0.5.0-win-x64-portable-src.zip (4,937,446 B，t2 产物)
  ```

* 0.4.5 当年为什么打得出来（对照证据）：`desktop/build/eb3.log` 里 `appOutDir=C:\tl-dist2`、
  临时目录 `C:\Users\Windows\AppData\Local\Temp\dsh-win-bash-5856-bc3b00c4\...` → 那是在
  **danger-full-access 的 win_bash 会话**里构建的（当时还在构建期用 `C:\tl-dist\tl-selfsigned.pfx` 跑 signtool；
  现在 `electron-builder.yml` 已把签名移出构建，`forceCodeSigning: false`）。

---

## 2. 构建口径核对（不构建也能钉死的部分）

| 项 | 实测值 |
|---|---|
| `desktop/package.json` | `version: "0.5.0"`；`scripts.dist = "electron-builder --win --x64"` |
| `electron-builder.yml` | `win.target = [nsis x64, portable x64]`；`win.artifactName` 与 `nsis.artifactName` = `TraceLens-${version}-win-${arch}-Setup.${ext}`；`portable.artifactName` = `TraceLens-${version}-win-${arch}-portable.${ext}` → 文件名必为 **0.5.0** |
| `extraResources` | `from: build/resources/sidecar → to: sidecar`（t2 的 onedir 在位：347 文件 / 81,308,236 B） |
| 构建期不签名 | `forceCodeSigning: false`，配置里无 `signtoolOptions`（`DesktopShellTests` 钉着这条） |
| 依赖 | `node_modules` 就位：electron **44.10.2**（`dist/version` 实测）、electron-builder **26.18.0**；node v24.21.0 / npm 11.19.0 |
| 离线缓存（构建所需三件套齐） | `%LOCALAPPDATA%\electron\Cache\electron-v44.10.2-win32-x64.zip`（158,203,656 B）、`electron-builder\Cache\nsis-3.0.4.1`、`nsis-resources-3.4.1`、`winCodeSign-2.6.0`、`7zip@1.0.0` |
| 0.4.5 基线字节数（5.0 必须只增不减） | Setup 151,535,616 B / portable 137,607,264 B |

---

## 3. 代码改动与回归

改动清单（本轮全部实测过；`node --check` 两个 JS 文件均 exit 0）：

| 文件 | 改动 | 为什么 |
|---|---|---|
| `desktop/sidecar.js:27-45` | `resolveLaunch` 增加 `featuresDir` → argv 追加 `--features <dir>` | F1：总裁办账本/密钥库必须与安装目录分离 |
| `desktop/sidecar.js:100-131` | 新增 `makeStreamDecoder`（UTF-8 严格流式 → 非法字节切 GBK/cp936，逐流独立、跨块不留 U+FFFD） | F2：冻结态 exe 实测不认 `PYTHONUTF8`/`PYTHONIOENCODING` |
| `desktop/sidecar.js:170-173` | stdout/stderr 不再 `setEncoding('utf8')`，改走解码器 | 同上（修前等价证据见 §5） |
| `desktop/main.js:44-90` | 新增 `featuresDir()`（优先级 `--features-dir` > `TRACELENS_FEATURES_DIR` > `userData/features`，与 `dataDir()` 同口径）并传进 `startSidecar`；`tracelens:info` 多带 `featuresDir` 便于 QA 核对 | F1 |
| `tools/build_sidecar.py:528-545` | 源码便携包的 `start.cmd` / `start-sidecar.cmd` 补 `--features "%~dp0data\features"`（队长放开的一处，只补这一个参数） | 便携形态同口径，否则 `var/features` 落在解包目录内 |

回归（`.tmp/t3-e2e-desktop.log`、`.tmp/t3-full-suite.log`、`.tmp/t3-full-suite-2.log`）：

* `tests.test_e2e.DesktopShellTests`：**Ran 7 tests … OK**（`.tmp/t3-e2e-desktop.log`），
  含会被本轮改动碰到的三条静态契约：`test_main_js_starts_sidecar_then_loads_ui_after_health`
  （`data-dir` 在文本里、只允许回环地址）、`test_builder_config_produces_both_targets_without_fake_signing`、
  `test_selfcheck_mode_is_scriptable`（本轮新增的 `--features-dir` 开关不影响它要求的 6 个开关）。
* `tests.test_e2e.ArtifactTests`：**Ran 4 tests，FAILED (failures=1)** —— 唯一红是 t1 在案的
  已知红 `test_signature_status_is_honest`（`signed_count: 2` vs 三份产物自签名 `UnknownError`，交 t4 转绿）。
  `test_sidecar_exe_runs` **通过**（真跑 onedir exe），`test_sha256sums_verify` **通过**
  （本轮没动 dist，0.4.5 的 sums 逐条重算一致），`test_builder_check_reports_the_real_reason` **通过**。
* 其它闸门（本轮实测）：`tools/gen_api_contract.py --check` = 唯一 FAIL 仍是 t1 在案的
  「前端调用面 8 个端点」（本轮未碰 `server.py` / `docs/03`，**无契约漂移**，§6.5 逐条比对 PASS、
  可重复生成 PASS）；`tools/doc_lint.py` exit 0；`tools/build_sidecar.py --check` exit 0
  （PyInstaller 本机不可用如实报告，onedir 现成产物在位）。

### 3.1 全量套件在本会话测不出基线（环境发现，交队长与 T6）

`python -X utf8 -m unittest discover -s tests` 本会话两次实测：

| 跑法 | 结果 |
|---|---|
| 默认（TEMP = 系统 TEMP） | `Ran 954 tests … FAILED (failures=16, errors=209, skipped=3)`（213.959 s） |
| `TEMP` 改指工作区 `.tmp\t3-pytmp` | `Ran 954 tests … FAILED (failures=16, errors=209, skipped=3)`（190.306 s，**逐字相同**） |

16 条 FAIL + 209 条 ERROR 全部是同一件事：`PermissionError: [WinError 5] 拒绝访问`，
落在 `...\AppData\Local\Temp\dsh-X57EEM\tmpXXXX\secretariat` 一类路径上
（日志里 `WinError 5` / `PermissionError` 各 332 处）。唯一与产品有关的一条红是已知红
`test_signature_status_is_honest`。

最小复现（**纯标准库，不碰任何产品代码**）：

```
A) python -c "tempfile.TemporaryDirectory() 建目录 → 在里面 mkdir('features')"
   mkdtemp ok: C:\Users\Windows\AppData\Local\Temp\dsh-X57EEM\tmppag6w887
   REPRO 失败: PermissionError [WinError 5] 拒绝访问。: '...\tmppag6w887\features'
   （连 os.walk 这个新建目录也是 WinError 5）
B) 工作区内新建目录再建子目录 → ok（对照组，证明不是"路径不可写"）
C) 工作区已存在目录里嵌套 mkdir → ok
```

读法：Python 3.14 的 `tempfile` 会给 `mkdtemp` 新建的目录**私有 DACL**，在受限 token 下
本进程反而进不去自己刚建的目录 → 凡是用裸 `tempfile.mkdtemp()` 的用例（`test_groups`、
`test_secretariat_api`、`test_secretariat_autosign` 等）全红；用 e2e 自带测试根目录
（`Temp\dsh-XXXX\agent-explainer-tests\`）的用例（含 `test_sidecar_exe_runs`）不受影响。
TEMP 挪到工作区也一样（`tmpN7qhim_\features` 同样 WinError 5）→ 与路径无关，与 mkdtemp 的 ACL 有关。

结论：**t1/t2 的基线（954 / 1 红 / 3 skip）无法在本会话复现**，这是会话沙箱 + Python 3.14
`tempfile` 的 ACL 交互，不是本轮改动的回归（本轮 Python 侧只改了 `tools/build_sidecar.py` 的
`start.cmd` 字符串，测试不读它）。回归闸门需要在提权会话重跑一次全量，才算闭合。

复现脚本已留档：`.tmp/t3-mkdtemp-repro.py`（A/B/C 三条对照，纯标准库）。
处置口径：按 DSH 的 Windows 沙箱诊断口径，这属于**预期拒绝**（工作区外写入 + 受限 token 下
`tempfile` 自建目录的私有 DACL），不是需要修的 ACL 冲突；诊断/修复脚本必须以非受限方式运行才有意义，
而本会话 approval 关闭 → 该路径按「未诊断」登记，交用户在「完全权限」会话复跑全量套件。
**不要手工改 ACL、不要删 deny 项。**

---

## 3.2 构建实录（队长在 danger-full-access 会话，2026-11-12 02:30–03:25）

用户批准「队长在本会话一次性提权跑构建 + 允许 `C:\TraceLens050\` 做 ASCII 实测目录、用完删掉」。

### 构建：第一次失败的真实原因

输出目录留在工作区（含非 ASCII「黑奴」）时，NSIS 阶段失败：

```
• building  target=nsis  file=…\desktop\build\dist\TraceLens-0.5.0-win-x64-Setup.exe
⨯ …\TraceLens-0.5.0-win-x64-Setup.exe process failed 2
Exit code: 2. Command failed: …\TraceLens-0.5.0-win-x64-Setup.exe
（makensis 无任何输出）
```

与 README 已知问题 11（非 ASCII 路径 → NSIS 失败）一致。0.4.5 的 `eb3.log` 里 `appOutDir=C:\tl-dist2` 就是当年绕开的做法。

### 构建：按 ASCII 输出口径成功

`npx --no-install electron-builder --win --x64 --config.directories.output=C:/tl-dist-050`（`TMP/TEMP` 指 `C:\Users\Windows\AppData\Local\Temp`）→ `EXIT=0`，日志 `desktop/build/eb-050.log`。产物拷回 `desktop/build/dist/`（`win-unpacked` 用 robocopy 镜像）。

| 产物 | 字节 | sha256 |
|---|---|---|
| `TraceLens-0.5.0-win-x64-Setup.exe` | 151,836,755 | `a234cd8e0902de8b69a963664e8adabc061771297bfeeb7f02c95f1f126f228f` |
| `TraceLens-0.5.0-win-x64-portable.exe` | 137,882,390 | `d14088d52429e7453b28e6e01da01f56b8fc06a3feed43c30b6ec29d10daae15` |
| `TraceLens-0.5.0-win-x64-Setup.exe.blockmap` | 159,964 | `9b31479c8720b88ebe9cd3a6ebd1a933618c5be6e5aea44740fc15041cda95b9` |
| `TraceLens-0.5.0-win-x64-portable-src.zip` | 4,957,816 | `098c3bcd472842c17de63323b0868c31226db125b876b380a31f2e7bc666aff3` |

字节数对 0.4.5（151,535,616 / 137,607,264）只增不减，文件名版本号 0.5.0 正确（来自 `package.json` 的 `${version}`）。

`portable-src.zip` 是重打过的（t2 那份 4,937,446 B 不含 `--features`）；实测 zip 内 `start.cmd` 现为：

```
"%PY%" -m agent_explainer --browser --data-dir "%~dp0data" --features "%~dp0data\features" %*
```

### 打包产物里确实带 F1/F2（asar 解包核对）

`npx --no-install asar extract …\resources\app.asar C:\TraceLens050\asar`：

```
main.js:47:  featuresDir: flag('features-dir'),
main.js:72:  function featuresDir() {
sidecar.js:38:  args.push('--features', featuresDir);
sidecar.js:113: function makeStreamDecoder(log) {
sidecar.js:165: const decodeOut = makeStreamDecoder(log);
sidecar.js:166: const decodeErr = makeStreamDecoder(log);
```

打包 sidecar 的 `BUILD-INFO.json`：`built_at=2026-11-12T01:51:23`、`hiddenimports=19` 条、含 `agent_explainer.secretariat`。

### 打包态 sidecar exe 实测：用恒等哈希继承 t2 的 7/7

`win-unpacked\resources\sidecar\…\agent-explainer-sidecar.exe` 与 `build\resources\sidecar\…\agent-explainer-sidecar.exe` **sha256 逐字节相同**（`f00527ca2c9ac56787b2f8794029013c15422717c371f40c4f982f8e136f093a`），即 electron-builder 打进 `extraResources` 的就是 t2 在 8799 端口实测过的那一份（`/health 0.5.0`、`/api/secretariat/status 200+enabled`、`identities 200`、登记 `201` 后计数 0→1）。

在 ASCII 目录直接跑打包态 exe 的尝试**被环境挡住**，原始 stderr：

```
PermissionError: [WinError 5] 拒绝访问。: 'C:\\TraceLens050\\pack-data\\auth'
[PYI-22568:ERROR] Failed to execute script 'sidecar-entry' due to unhandled exception!
```

`icacls … /grant *S-1-1-0:(OI)(CI)F /T` 处理 506 个对象成功后仍复现。落点是 `auth.py:407` 对 auth 目录的私有 DACL 加固，与 §3.1 的 `tempfile` 私有 DACL × token 是同一类预期拒绝（见 `.tmp/t3-mkdtemp-repro.py`），**不是产品缺陷**；同一次运行的 bootstrap 行证明参数面是对的：`"version": "0.5.0"`、`"features"` 出现在 argv（`--features C:\TraceLens050\pack-features`）。

### Electron 壳级（窗口）实测：本会话不可做，如实登记

`win-unpacked\TraceLens.exe` 在提权会话里**立刻退出（exit 0）且零输出、不建 userData**；`--version` 能正常返回，说明不是二进制损坏。探针 `.tmp/v050-shell-probe.py` 的 `alive` 因此不可判。

另外钉住一条命令行事实：打包态 Electron 会把未知开关判成 `bad option` 直接退出（实测 `--port`、`--user-data-dir` 都是），所以壳级参数只能走 `TRACELENS_PORT / TRACELENS_DATA_DIR / TRACELENS_FEATURES_DIR / TRACELENS_MODE` 这套环境变量（`main.js` 认）。

NSIS 静默安装（`Setup.exe /S`）在 `C:\Users\Windows\AppData\Local\Programs\TraceLens` 上**未产生写入**（安装器进程消失、目录仍是 11/11 的 0.4.5：`TraceLens.exe` mtime 2026-11-11 08:39:44、`BUILD-INFO.json built_at=2026-11-11T10:11:42`）。该目录属于 DSH 服务账号 `Windows`，**不是用户本人桌面账号** —— 用户的应用要在**他自己的桌面会话**里双击 `TraceLens-0.5.0-win-x64-Setup.exe` 更新；壳窗口与便携版的界面实测也只能在他那边做。

### 本轮关闭与遗留

* 关闭：桌面产物已产出（Setup / portable / portable-src / blockmap），F1/F2 已进 asar，`--features` 已进 `start.cmd`。
* 遗留（交 QA 与用户）：壳窗口级实测（界面「总裁办」页签可见）与便携版启动实测需在用户桌面会话完成；NSIS 安装实测同。
* 回归闸门：全量套件在提权会话复跑一次才算闭合（t4 产物齐后跑）。

---

## 4. F1 必修：总裁办账本与密钥库落 userData（实测原始输出）

缺陷（t2 在案）：不传 `--features` 时，`server.py` 的默认 `var/features` 在冻结态解析到
**程序目录内部** `_internal\var\features\secretariat\` → 装进 Program Files 不可写（登记身份直接失败）、
升级覆盖安装目录会冲掉身份账本与**密钥库 `keyring.json`**（密钥丢失＝已签记录不可验证）。

探针 `.tmp/t3-f1-features.py`（日志 `.tmp/t3-f1-features.log`）：**不自己编参数**，
argv 由 `.tmp/t3-argv-dump.js` 调产品自己的 `desktop/sidecar.js:resolveLaunch` 导出：

```
argv（产品启动器导出）: --sidecar --port 8793 --mode local
  --data-dir E:\DEEPSEEK 黑奴\agent-explainer\.tmp\t3-f1\installB\data
  --demo zh --features E:\DEEPSEEK 黑奴\agent-explainer\.tmp\t3-f1\userData\features
含 --features: true          ← 修后的启动器口径
```

### 4.1 A 组 = 产品现状（argv 去掉 `--features`）：缺陷复现

```
A POST 登记身份 = 201  seq=1
A status = 200 counts={"identities": 1, "active_identities": 1, ...}
A 账本 path = ...\.tmp\t3-f1\installA\agent-explainer-sidecar\_internal\var\features\secretariat\identities.jsonl
                chain.ok=True count=1
A 运行后程序目录新增文件 3 个：
    + _internal\var\features\secretariat\identities.jsonl        (630 B)
    + _internal\var\features\secretariat\identities.jsonl.lock   (0 B)
    + _internal\var\features\secretariat\keyring.json            (343 B)
```

### 4.2 B 组 = 修好后（argv 原样）：账本在 userData，程序目录零新增

```
启动前 userData/features 存在 = False
启动前程序目录 _internal\var 存在 = False
B 登记前 status=200 账本 path=...\.tmp\t3-f1\userData\features\secretariat\identities.jsonl counts={"identities": 0, ...}
B POST 登记身份 = 201 seq=1 entry_hash=ad522fb8f4d3f777
B 登记后 counts={"identities": 1, "active_identities": 1, ...}
B 账本在 userData 下？ True      B 账本在程序目录内部(_internal)？ False
B 运行后程序目录新增文件 0 个：[]
B 程序目录 _internal\var 存在 = False
```

`userData/features` 实际落盘（与 `Get-ChildItem -Recurse` 等价）：

```
E:\...\t3-f1\userData\features\evidence\files\
E:\...\t3-f1\userData\features\rollback\
E:\...\t3-f1\userData\features\secretariat\identities.jsonl          (632 B)
E:\...\t3-f1\userData\features\secretariat\identities.jsonl.lock     (0 B)
E:\...\t3-f1\userData\features\secretariat\keyring.json              (345 B)
E:\...\t3-f1\userData\features\secretariat\letters\
B keyring.json sha256 = 6194d3f4f328f20ad94a79a9926869fdd51843f0373ba083e8bbcf9b253c9bfd
```

### 4.3 C 组 = 模拟升级（程序目录整份换新＝覆盖安装，userData 不动）

```
升级前 userData 账本存在 = True 行数 = 1
升级前 keyring.json 存在 = True sha256 = 6194d3f4...c9bfd
C 升级后 status=200 counts={"identities": 1, "active_identities": 1, ...}
C 账本 path = ...\.tmp\t3-f1\userData\features\secretariat\identities.jsonl  chain.ok=True count=1
C GET identities = 200 count=1 agent_ids=['agent-t3-withfeatures']
C keyring.json 存在 = True sha256 = 6194d3f4...c9bfd   与升级前一致 = True
C 计数不减（登记后 1 条仍在）= True
C 新装目录相对上一份新增运行期文件 = []
```

**F1 判定：PASS**（探针自带机器可读结论，exit 0）。
Electron 级（装好的壳 + `--user-data-dir` 真跑）由提权会话跑 `.tmp/t3-desktop-run.py alive|selfcheck|upgrade`，同一判据（§6）。

---

## 5. F2 必修：sidecar 输出编码（卡内处方无效，实测改判据）

### 5.1 处方为什么无效（实测）

`desktop/sidecar.js:139-144` **早就**注入了 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`
（`TRACELNS_SIDECAR` / `PYTHONUNBUFFERED` 同批）。实测冻结态 exe **完全不认**（`.tmp/t3-f2-codec2.py`）：

| 场景 | stdout 字节 | 严格 utf-8 | 严格 gbk |
|---|---|---|---|
| exe + `PYTHONIOENCODING=ascii`（且 `PYTHONUTF8=1`） | 699 B | 失败 | 通过 |
| exe + `PYTHONIOENCODING=utf-8` | **699 B（与上完全相同）** | 失败 | 通过 |
| exe + `PYTHONIOENCODING=gbk` | 699 B | 失败 | 通过 |
| exe + 不设 env | 699 B | 失败 | 通过 |
| 源码态 `python -m agent_explainer` + `PYTHONUTF8=1` | **549 B** | **通过** | 通过 |
| 源码态 + 不设 env | 537 B | 失败 | 通过 |

`ascii` 都崩不出 `UnicodeEncodeError`（exit 0，无 stderr）→ 变量根本没进解释器：
**PyInstaller 冻结层吞掉了 `PYTHONUTF8` / `PYTHONIOENCODING`**，stdout 永远按系统代码页 cp936。
源码态认（549 B 严格 utf-8 通过）→ 差异确实出在冻结层，不在产品代码。
（t2 在 `BUILD-INFO.json` 里记的 `smoke.stdout_codec = "gbk"` 与此一致。）

### 5.2 落地修法与字节层证据（修前 / 修后同一份字节）

字节样本 = 冻结态 exe `--print-argv` 的**原始 stdout**（`.tmp/t3-f2/exe-stdout.bin`，699 B，未解码）：

```
原始字节数 = 699
含 GBK 的「黑奴」字节 ba da c5 ab = true
含 UTF-8 的「黑奴」字节 e9 bb 91 e5 a5 b4 = false
```

**修前**（`sidecar.js` 原来的 `proc.stdout.setEncoding('utf8')`，等价于同一份字节 `buf.toString('utf8')`）：

```
U+FFFD 乱码替换符个数 = 14
中文「黑奴」可读 = false
第一行 = E:\DEEPSEEK ��ū\agent-explainer\desktop\build\resources\sidecar\...
```

**修后**（`sidecar.js` 的 `makeStreamDecoder`，同一份字节按 4096 B 分块喂）：

```
[解码器日志] sidecar 输出不是 UTF-8（冻结态 exe 实测不认 PYTHONUTF8），本次输出按 GBK/cp936 解码
U+FFFD 乱码替换符个数 = 0
中文「黑奴」可读 = true
第一行 = E:\DEEPSEEK 黑奴\agent-explainer\desktop\build\resources\sidecar\...
```

单元行为（同一探针，含跨块半字符场景）：UTF-8 块原样通过；GBK 整块可读；
GBK 末字节切到下一块仍无 U+FFFD（`出现 U+FFFD: false`）。stdout / stderr 各用一条独立解码器
（两条流编码可能不同，不能共享状态）。

* 判据（队长已改口径）：打包态 Electron 日志中文逐字可读 + 字节层证据 → 字节层已给足；
  **Electron 壳的日志实测**由提权会话 `.tmp/t3-desktop-run.py alive` 输出（该探针自带
  「GBK 字节 / UTF-8 字节 / U+FFFD 计数 / 可读中文行」四项判定）。
* 冻结侧的替代修法（不推荐在本轮做）：`agent_explainer/__main__.py` 里
  `sys.stdout.reconfigure(encoding='utf-8')` —— 属业务代码，本轮未动，留给队长裁决。

---

## 6. 交给提权会话的清单（构建 + 便携版四条 + 收尾）

按队长裁决，以下由 danger-full-access 会话执行，原始输出交我入档（§2 之后有落档位）。探针已就位、参数已对齐本轮改动。

```pwsh
# 1) 构建（产物落 desktop/build/dist，日志落 desktop/build/eb-050.log）
cd "E:\DEEPSEEK 黑奴\agent-explainer\desktop"
$env:DEBUG="electron-builder:*"
npx --no-install electron-builder --win --x64 *> ..\desktop\build\eb-050.log    # 已留同名日志（本轮为失败记录）

# 2) 便携版/壳真跑四条 + F1(a) 落点 + F2 字节证据（alive 模式跑完自动 taskkill 收树）
cd "E:\DEEPSEEK 黑奴\agent-explainer"
python -X utf8 .tmp\t3-desktop-run.py alive     --app "desktop/build/dist/win-unpacked/TraceLens.exe"
python -X utf8 .tmp\t3-desktop-run.py selfcheck --app "desktop/build/dist/win-unpacked/TraceLens.exe"
# 3) F1(b) 模拟升级：把 win-unpacked 拷成第二份"新装"，同一 userData 再起
python -X utf8 .tmp\t3-desktop-run.py upgrade   --app ".tmp\t3-electron\installC\TraceLens.exe"

# 4) portable.exe 本体（NSIS 7z SFX 解包到 %TEMP%，纯 ASCII 路径；README 记录的中文路径 Error writing 就在这条上）
#    复制到 ASCII 目录后用 TRACELENS_DATA_DIR 指 ASCII 数据目录，再跑同一组四条
# 5) 源码便携包重打（本轮已补 --features，**未重打**：队长令构建期间不得动 dist）
python -X utf8 tools\build_sidecar.py --portable-zip
```

四条判据（与 t2 的 §5 同源）：`/health` 200 且 `version=0.5.0`；`/api/secretariat/status` 200 且 `ok=true`、
`secretariat.enabled=true`；`GET /api/secretariat/identities` 200；`POST` 登记 201 后计数 0→1
（探针额外钉：账本 path 不得含 `_internal`）。

## 7. 未做项（如实登记，不造假）

1. `TraceLens-0.5.0-win-x64-Setup.exe` / `-portable.exe` **未产出**（§1 根因）。
2. portable.exe 的 ASCII 路径真跑四条 **未做**（同上；探针已交）。
3. NSIS 安装包的哈希核对 **未做**（产物不存在）；本轮**未安装**（按卡要求，静默安装风险口径留给 QA）。
4. `TraceLens-0.5.0-win-x64-portable-src.zip` **未重打**（`start.cmd` 已补 `--features`，
   dist 现存的 4,937,446 B 那份是 t2 的、不含该参数）—— 构建期间不得动 dist。
5. 签名 / SHA256SUMS / 发布总包 / Docker：属 t4/t5，未动（`SIGNATURE-STATUS.json` 与 `SHA256SUMS.txt` 保持 t1/t2 状态）。
6. `win-unpacked` 的 0.4.5 内容被失败构建清掉（副作用已记账，§1.3）。
7. **全量套件基线未闭合**：本会话测出 954 / 16 红 / 209 错 / 3 skip，逐条定位为同一环境事（§3.1），
   与 t1/t2 的 954 / 1 / 3 无法比对；需在提权会话重跑一次全量才算闸门闭合。

## 8. 现场清理与留档

* 探针与日志（`.tmp/`）：`t3-f2-codec.py(.log)`、`t3-f2-codec2.py(.log)`、字节样本 `t3-f2/exe-stdout.bin`（699 B，未解码）、
  `t3-f2-beforeafter.js(.log)`、`t3-argv-dump.js` → `t3-argv.json`、`t3-f1-features.py(.log)`、
  `t3-desktop-run.py`（交提权会话：`alive` / `selfcheck` / `upgrade`）、`t3-node-probe.js`（提权会话可用：section C 走 Node 管道）、
  `t3-mkdtemp-repro.py`（§3.1 最小复现）、`t3-e2e-desktop.log`、`t3-full-suite.log`、`t3-full-suite-2.log`、`t3-gbk.bin`。
* 构建日志：`desktop/build/eb-050.log`（本轮为失败记录；构建成功后覆盖为成功记录）。
* 临时安装目录：`.tmp/t3-f1/installA|installB|installC`（每份 81 MB，F1 证据的可复现现场，**保留到本轮验收后再清**）；
  `.tmp/t3-f1/userData/features/`（账本 + 密钥库样本，`keyring.json` sha256 `6194d3f4…c9bfd`）。
* 进程与端口：sidecar 全部 `terminate`；`Get-Process agent-explainer-sidecar,TraceLens,electron` 为空；`8793/8899` 无监听。
* 已清理：`.tmp/t3-mk*`、`.tmp/t3-f2/data`。**清不掉的一个**：`.tmp/t3-pytmp` 里全量套件留下的
  `tmpXXXX\` 目录连本 token 都删不掉（`shutil.rmtree(ignore_errors=True)` 之后仍在）—— 正是 §3.1 那条 ACL 现象的活证据。
