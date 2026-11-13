# TraceLens 0.5.0 — R3-1 闭合 QA 的 D1/D2（契约漂移 + 总包缺件）+ D3/D4/D5 登记：实录

执行：backend（后端工程师）；任务 t10；日期 2026-11-12。
范围（卡片写作用域）：`docs/**`（含 `docs/api_contract.json`、`docs/05.3-release-checklist.md`、
`docs/audit/v050-docs-sync.md`、`docs/audit/v050-release.md`、`docs/audit/v050-tests-tools.md` + 本文）、
`release/tracelens-0.5.0-release/**`、`release/tracelens-0.5.0-release.zip`、
`release/SHA256SUMS-release.txt`、`.tmp/*`（脚本与日志，非交付件）。
**零二进制改动**：两个已签 exe 逐字节未动（§6.3 复算命中）。

## 0. 结论

| 项 | 结果 |
| --- | --- |
| **D1（medium）契约漂移** | **已闭合**：`gen_api_contract.py --write` 重生成契约，`--check` 从 **2 项失败回到 1 项**，且那 1 项就是既有的「前端调用面 = 8 个非总裁办端点」。契约 json 与上一版相比**只变了一个值**（`sources["docs/03-architecture.md"]` 的 sha256），**没有新增任何总裁办端点条目**，未手改 json |
| **D2（medium）总包缺一份实录** | **已闭合**：`docs/audit/v050-tests-tools.md` 入包（逐字节 SAME）→ `SHA256SUMS-bundle.txt` **25 条**（自身不入清单、UTF-8 无 BOM、CRLF 25 行）逐条复算 **25/25 通过** → zip 重打 **26 条目 == 目录 26 文件**、全 `ZIP_STORED`、**359,060,132 B** → `release/SHA256SUMS-release.txt` 第 2 行同步为 `3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990`，**0.4.5 行逐字未变**（与期望串 `0c5d5552…139ab` 比对 True） |
| **D5（low）行号漂移** | **已闭合**：句柄锁缺口按 QA 实测改成 **496 / 502 行**（496=`Set-AuthenticodeSignature @signArgs`、502=`$check = Get-AuthenticodeSignature -LiteralPath $a.FullName`，本轮 `Select-String` 实测行号）；`v050-tests-tools.md` §6 第 1 条改正并给"按锚点找"的核对命令，`v050-release.md` §2.2 的**逐字原文保留**、其后加行号时点更正块。脚本行为一字未动 |
| **D3（low）05.3 §1 缺行内时点** | **已闭合**：§1 表加第 4 列「数据时点」，13 行逐行标 `0.4.5 时点` / `0.5.0 现状`，表下补与 §10.1 顶部时点框、§11 一致的口径说明 |
| **D4（low）打包 exe 开关口径** | **已入档**：`docs/audit/v050-docs-sync.md` 新增 §11，三组观测（A 队长 t3 的 `bad option` 文本 / B QA 的 exit=9 零输出 / C 本机无参数 GUI 也零输出）**并列登记、不合并成结论**，并写死 C1 指引一律走 `TRACELENS_*` 环境变量 |
| 硬纪律 | 两 exe 命中 `37cf6963…0395D`(151,838,192 B) / `8bdc55a0…FDD52`(137,883,824 B)；`SIGNATURE-STATUS.json` 未触碰（`UnknownError` 一字未改）；`tools/sign_and_hash.ps1` 一字未动（本轮只改文档行号）；`release/0.4.5/**` 未动 |
| 闸门 | 见 §4（全量套件 + `var/features` 跑前/跑后指纹，F4 修复不得回退） |

---

## 1. D1：契约漂移（t7 改了 03-architecture 没重跑生成器）

### 1.1 基线（改动前，`--check` 两项失败）

```
[FAIL] 前端调用面 —— 界面调用后端没有的路由：['/api/analytics', '/api/compliance', '/api/evidence',
        '/api/groups', '/api/preferences', '/api/rollback', '/api/shares', '/api/suggestions']
[FAIL] 契约漂移 —— 磁盘与本次生成不同，前 12 行 diff：
      --- docs/api_contract.json (磁盘)
      +++ (本次生成)
      @@ -1407,7 +1407,7 @@
      -    "docs/03-architecture.md": "sha256:41fb68af8e13d3291367980da7dfd32448725302b3654e25b16e4e0efe2f3ed2",
      +    "docs/03-architecture.md": "sha256:ac603bc09a1857eb1f7cdec0bdf30af935d08f833b03a719477b6844339c0575",
[FAIL] 契约检查未通过：2 项（详见上方逐条）
--check exit=1
```

QA 报的「从 1 项失败变成 2 项」逐字对上：新增项就是这条漂移，`41fb68af…` → `ac603bc0…` 与卡片给的两个哈希一致。

### 1.2 `--write` 原文

```
$ python -X utf8 tools\gen_api_contract.py --write
[PASS] 已写入 docs/api_contract.json（39246 字节，41 条路由）
[PASS] 路由抽取 —— 41 条 (path, verb)，43 个 API_PATH 常量，公开路径 5 条，显式 405 组 3 组
[PASS] 权限矩阵 —— 4 个角色 / 8 个能力（路由用到 8 个）：admin=8, editor=5, local=7, viewer=3；矩阵里有但没有专属路由的能力：无
[PASS] 错误码 —— 共 168 个，有 `<域>.error.<code>` 键 75 个，无键 93 个（走 message 回退）
[FAIL] 前端调用面 —— 界面调用后端没有的路由：[8 个端点，同上]
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应，方法列一致，无 ⚠ 行
[FAIL] 契约检查未通过：1 项（详见上方逐条）
write exit=1
```

### 1.3 `--check` 复跑原文（漂移项消失）

```
$ python -X utf8 tools\gen_api_contract.py --check
[PASS] 路由抽取 —— 41 条 (path, verb)，43 个 API_PATH 常量，公开路径 5 条，显式 405 组 3 组
[PASS] 权限矩阵 —— 4 个角色 / 8 个能力（路由用到 8 个）：admin=8, editor=5, local=7, viewer=3；…
[PASS] 错误码 —— 共 168 个，有 `<域>.error.<code>` 键 75 个，无键 93 个（走 message 回退）
[FAIL] 前端调用面 —— 界面调用后端没有的路由：['/api/analytics', '/api/compliance', '/api/evidence',
        '/api/groups', '/api/preferences', '/api/rollback', '/api/shares', '/api/suggestions']
[PASS] 可重复生成 —— 磁盘内容与本次生成字节一致（39246 字节）
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应，方法列一致，无 ⚠ 行
[FAIL] 契约检查未通过：1 项（详见上方逐条）
check exit=1
```

**唯一失败 = 那 8 个非总裁办端点**（`/api/analytics`、`/api/compliance`、`/api/evidence`、`/api/groups`、
`/api/preferences`、`/api/rollback`、`/api/shares`、`/api/suggestions`），与 t6 的既有口径逐字相同；
exit=1 是这个检查的既定结论码（不是新红）。新增的 `[PASS] 可重复生成` 说明磁盘与再生成字节一致。

### 1.4 契约 json 到底变了什么（逐键比对，不是"看起来一样"）

`--write` 前先把磁盘那份存档（`.tmp/t10-api_contract.before.json`，41,284 B），跑完逐键比对：

```
  字节 = 41284 -> 41284
  顶层键 = True（19 个键，前后同名同集合）
  [同] abilities / doc_crosscheck / error_codes / frontend_endpoints / generated_by / limits /
       local_capabilities / matrix_abilities / matrix_abilities_without_routes / method_fallbacks /
       method_not_allowed / path_constants / public_paths / readme / role_permissions / routes /
       schema_version / verbs_by_path
  [变] sources  键增 = []  键减 = []
        值变: docs/03-architecture.md
```

⇒ **唯一变化就是那个 sha256 指纹**；`routes` / `verbs_by_path` / `path_constants` 一字未变，
所以**没有任何"新增总裁办端点条目"**可贴（卡片那条"若有"的条件不成立）。
总裁办端点在契约里早已存在（01:32 那份就有），本轮照抄登记值：

| 契约里的总裁办路径 | 方法 |
| --- | --- |
| `/api/secretariat` | GET |
| `/api/secretariat/status` | GET |
| `/api/secretariat/identities` | GET POST |
| `/api/secretariat/identities/{id}/revoke` | POST |
| `/api/secretariat/delegations` | GET POST |
| `/api/secretariat/letters` | GET POST |
| `/api/secretariat/letters/{id}` | GET |
| `/api/secretariat/letters/{id}/verify` | POST |
| `/api/secretariat/records/{id}/sign` | POST |
| `/api/secretariat/seal` | POST |
| `/api/secretariat/verify` | POST |

与 `agent_explainer/server.py` 的核对：静态字符串端点在 server.py 里逐字出现（`/api/secretariat`、
`/api/secretariat/status`、`/api/secretariat/identities`、`/api/secretariat/delegations`、
`/api/secretariat/letters`、`/api/secretariat/seal`、`/api/secretariat/verify` 全部 `True`）；
带 `{id}` 的三条在 server.py 里不是这个字面量（按路径段匹配 + id 参数处理），所以字符串比对为 `False`
——**这是抽取器把服务端模式规范化成 `{id}` 的正常结果，不是端点缺失**（契约是生成器从 server.py 抽的，
本轮未手改 json；`--check` 的「路由抽取 41 条 / §6.5 逐条比对一一对应」两条 PASS 是同一事实的独立复核）。

### 1.5 文件实名（卡片用词与本仓库实名的差异，登记）

卡片写的是 `docs/api-contract.json`；本仓库实名为 **`docs/api_contract.json`（下划线）**，
生成器与 `--check` 的报错头也都用后者。本轮只按实名操作，未新建/改名任何文件。

---

## 2. D2：总包缺 `docs/audit/v050-tests-tools.md`

### 2.1 基线（QA 判 D2 的形状，本轮实测复现）

```
  仓库份数 = 7
  [SAME] v050-desktop.md / v050-docker.md / v050-docs-sync.md / v050-release.md / v050-sidecar.md / v050-version.md
  [缺件] v050-tests-tools.md  仓库 18,576 B  包内不存在
  zip = 359,034,202 B  条目 = 25  全 ZIP_STORED = True   目录文件 = 25   条目==目录 = True
  bundle 清单 = 24 条  BOM = False  CRLF 计数 = 24   逐条复算 = 24/24 通过
```

### 2.2 处置与重打原文（脚本 `.tmp/t10-bundle-repack.py`，日志 `.tmp/t10-repack.log`）

```
== 1. 仓库文档 → 包内副本（逐字节同步 + 回读校验）==
  [SAME] README.md  31,243 B
  [SAME] docs/05.3-release-checklist.md  38,603 B     ← 本轮 D3 改过
  [SAME] docs/05.4-docker.md  38,141 B
  [SAME] docs/06-faq.md  15,271 B
  [SAME] docs/06-feedback-form.md  7,588 B
  [SAME] docs/06-user-guide.md  11,036 B
  [SAME] docs/NOTICE-third-party.md  5,558 B
  [SAME] docs/audit/v050-desktop.md  30,955 B
  [SAME] docs/audit/v050-docker.md  28,227 B
  [SAME] docs/audit/v050-docs-sync.md  35,041 B       ← 本轮 D4 新增 §11
  [SAME] docs/audit/v050-release.md  20,588 B         ← 本轮 D5 加行号时点块
  [SAME] docs/audit/v050-sidecar.md  17,886 B
  [SAME] docs/audit/v050-tests-tools.md  19,108 B     ← D2 入包的这份
  [SAME] docs/audit/v050-version.md  20,230 B
== 2. docker 三件重拷（幂等，源=release/0.5.0）==
  docker/tracelens-0.5.0-image.tar  64,029,696 B  源同字节 = True
  docker/IMAGE-INFO.json  759 B  源同字节 = True
  docker/SHA256SUMS.txt  92 B  源同字节 = True
== 3. 重算 SHA256SUMS-bundle.txt ==
  条目 = 25  字节 = 2299  回读一致 = True  BOM = False  CRLF 行 = 25
  目录文件数 = 26（清单自身在目录里，所以条目 = 目录 - 1）
  含 SHA256SUMS-bundle.txt 自身 = False（必须 False）
== 4. 重打 zip（ZIP_STORED）==
  条目 = 26  全 ZIP_STORED = True  字节 = 359,060,132
  sha256 = 3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990
  条目集合 == 目录文件集合 = True
== 5. 同步 release/SHA256SUMS-release.txt ==
  0.4.5 行逐字未变 = True  （期望串比对 = True）
  字节 = 190  BOM = False  CRLF 行 = 2
  0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip
  3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990  tracelens-0.5.0-release.zip
== 6. 复算：清单逐条 / 包内 exe / v050 七份 / 包内==仓库 ==
  清单逐条复算 = 25/25 通过
  zip 条目 = 26 == 目录文件 = 26  集合相同 = True
  包内 TraceLens-0.5.0-win-x64-Setup.exe  151,838,192 B  命中 = True
  dist TraceLens-0.5.0-win-x64-Setup.exe  151,838,192 B  命中 = True
  包内 TraceLens-0.5.0-win-x64-portable.exe  137,883,824 B  命中 = True
  dist TraceLens-0.5.0-win-x64-portable.exe  137,883,824 B  命中 = True
  仓库 v050-*.md = 7 份；包内 = 7 份
  [SAME] v050-desktop.md / v050-docker.md / v050-docs-sync.md / v050-release.md / v050-sidecar.md
         / v050-tests-tools.md / v050-version.md
== 结论：全部通过 ==
repack exit=0
```

**新 zip 上报**：`release/tracelens-0.5.0-release.zip` = **359,060,132 B**，
sha256 `3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990`；权威值写在
`release/SHA256SUMS-release.txt` **第 2 行**（本文在 zip 里面，写自己的哈希必然失效 → 不自引用，
沿用 `v050-docs-sync.md` §5 与 `docs/05.3` §12 的口径）。

上一版 zip 是 `41b9beab6a96a25d2c19d42e9f6e2f86a39ab83fdc0f6079a44a4af5306db197` / 359,034,202 B（`--check` 前的基线实测），
被本轮替换；0.4.5 那一行逐字未变（期望串比对 True）。

> **时点标记（QA t11 的 D7，t12 补）**：本节 §2.2 的 **26 条目 / 359,060,132 B / `3257f7e1…`** 是 **t10 收口那一轮（docs-sync §5 表的 R4）**
> 的打印值，**已被队长最终收口那一轮（R5：27 条目 / 359,090,440 B / `7bd49975…`）取代**；现行为 R6（见 §14）。
> 原文按逐字保留不改史。

### 2.3 本文不入包（口径说明，别让下轮当成新的 D2）

卡片给的目标数就是 **清单 25 条 / zip 26 条目 / 包内 7 份 `v050-*.md`**，所以本轮**故意不把本文**
（`v050-r3-closeout.md`）打进包：它描述的就是这次重打，写进去会自引用；且入包会让条目变成 27/26，
与 QA 已复算的口径不符。⇒ 本轮结束后仓库有 8 份 `v050-*.md`、包内 7 份（7/7 逐字节 SAME）。
**建议**：下一轮若有最终重打，把本文一并入包（并同步清单），别在最终重打之后再产出新实录 —— 这正是 D2 的根因（记录晚于 zip）。

> **本节结论已被取代（QA t11 的 D7，t12 补）**：队长按 §13.1 采纳「入包」，包内 `docs/audit/` 现为 **8 份** `v050-*.md`
> （含本文），zip 由 26 条目变 **27 条目**。本节写的「7 份 / 26 条目」是 t10 时点的口径，保留是为了不改史；
> 现行态见 §14。

---

## 3. D5：句柄锁缺口的行号 448 → 496 / 502

`Select-String` 实测（本轮，脚本未动）：

```
Line 496:         Set-AuthenticodeSignature @signArgs | Out-Null
Line 502:     $check = Get-AuthenticodeSignature -LiteralPath $a.FullName
```

| 文件 | 改动 |
| --- | --- |
| `docs/audit/v050-tests-tools.md` §6 第 1 条 | 「（约 448 行区域）」→ 写明 **496 / 502**、两行原文、"中间没有释放/重试"，并给按锚点核对的命令：`Select-String -Path tools/sign_and_hash.ps1 -Pattern 'Set-AuthenticodeSignature|Get-AuthenticodeSignature -LiteralPath \$a'`；注明 448 是 t4 时点行号、漂因是 t8 增码（新增 `Test-TextFileEncoding` 与 `-Include045`）。文档 18,576 → 19,108 B |
| `docs/audit/v050-release.md` §2.2 | 那段 `sign_and_hash.ps1:448 char:14` 是**逐字原始报错，保留不改史**；紧随其后加「行号时点更正」块（现 496/502 + 锚点命令 + 缺口仍未修）。文档 19,888 → 20,588 B |
| 未动 | `tools/sign_and_hash.ps1` **一字未动**（卡片：只改文档行号，不改脚本行为）；QA 自己的两份报告（`docs/17`、`docs/18`）里已有 496/502 与 D5 条目，属 QA 文档，我不改写他人报告 |

---

## 4. D3：`docs/05.3-release-checklist.md` §1 表补行内「数据时点」列

脚本 `.tmp/t10-d3-053.py`：定位 §1 表（表头第 9 行、13 条数据行），追加第 4 列并在表下加口径行；
行数与标注数不等就**拒绝改动**（防错行）。文档 36,393 → 38,603 B，BOM=False、U+FFFD=False。

| 行（环节） | 补的「数据时点」 |
| --- | --- |
| Python 入口 CLI | 0.4.5 时点（2026-11-11）；0.5.0 由同一用例在本轮全量套件 `954 / OK` 复跑通过 |
| sidecar 模式 bootstrap | 0.4.5 时点；0.5.0 由同一用例在本轮全量套件复跑通过 |
| 端到端业务链路 | 0.4.5 时点（`Ran 28`）；0.5.0 由全量套件 954 条覆盖（§12） |
| PyInstaller 单文件 exe | 0.4.5 时点；0.5.0 同口径（交付布局用 onedir，单文件仍被沙箱拒），见 `v050-sidecar.md` |
| PyInstaller onedir exe | **0.5.0 现状**：10,403,499 B / `f00527ca…`（§12 与 `BUILD-INFO.json`，built_at 2026-11-12T01:51:23） |
| 便携源码包 | **0.5.0 现状**：4,957,816 B / `098c3bcd…`（§12）；正文里的 3,483,619 B 是 0.4.5 时点值 |
| 桌面壳代码静态契约 | 0.4.5 时点；0.5.0 由 `DesktopShellTests` 在本轮套件复跑通过（asar 内行号见 `v050-desktop.md`） |
| `npm install` | 0.4.5 时点（5.3b）；0.5.0 构建复用同一 `node_modules`，未重装 |
| Electron 二进制下载 | 0.4.5 时点（electron 44.10.2）；0.5.0 未重新下载 |
| Electron 运行（GUI） | 0.4.5 时点结论；0.5.0 **仍未窗口级实测**（§12 未做项、`v050-desktop.md`） |
| electron-builder NSIS + portable | 0.4.5 时点＝未产出；**0.5.0 已产出**：Setup 151,838,192 B / portable 137,883,824 B（§12、`v050-desktop.md` §3.2） |
| 代码签名（自签名） | 0.4.5 时点＝未产出 `NotSigned`；**0.5.0 已签**：`UnknownError`×2 + `signed_count=0` + `signatures_applied=2`（§12、`v050-release.md`） |
| `SHA256SUMS.txt` | 0.4.5 时点的机制记录；0.5.0 逐条复算全过（`release/SHA256SUMS.txt` 2 条，`v050-release.md`） |

表下口径行（与 §10.1 顶部时点框、§11 表一致）：
`0.4.5 时点` = 该行证据取自 2026-11-11 那批 0.4.5 文件的历史复核，**别把里面的文件名与哈希当 0.5.0 的校验值**；
`0.5.0 现状` = 取自 2026-11-12 重打的 0.5.0 产物，哈希只从四份清单 / `BUILD-INFO.json` 读（§12「哈希的唯一读法」）。

**D4 的入档在 `docs/audit/v050-docs-sync.md` §11**（A/B/C 三组观测并列、`main.js` 的开关与环境变量代码事实、
C1 一律走 `TRACELENS_PORT / TRACELENS_DATA_DIR / TRACELENS_FEATURES_DIR / TRACELENS_MODE`（+ `TRACELENS_SELFCHECK=1`），
禁止在 C1 里写"`--port` 会 `bad option` 退出"这类因果断言）。

---

## 5. 闸门与复算（本轮实测）

### 5.1 全量套件（验收 5、6）

```
$ python -X utf8 -u .tmp\t10-suite-gate.py     （套件本身：python -m unittest discover -s tests -p "test_*.py" -v）
== var/features 跑前 ==  总字节 = 905  文件数 = 4
         0  e3b0c44298fc1c14  var/features/secretariat/heads.jsonl.lock
       567  10481ef7df19d1b4  var/features/secretariat/identities.jsonl
         0  e3b0c44298fc1c14  var/features/secretariat/identities.jsonl.lock
       338  c71a94128b604b82  var/features/secretariat/keyring.json
--- 套件 exit=0  日志行数=1099  日志字节=128,404
   test_no_reportlab_falls_back_to_printable_html (test_audit_log.PdfExportTests…) ... skipped '本机有 reportlab（None），回…'
   test_prefer_pdf_raises_when_reportlab_is_unavailable (test_audit_log.PdfExportTests…) ... skipped "本机有 repor…"
   test_file_mode_is_owner_only_on_posix (test_settings_api.PersistenceTests…) ... skipped 'POSIX 位权限只在类 Unix 文件系统上有意义'
   Ran 954 tests in 176.793s
   OK (skipped=3)
== var/features 跑后 ==  总字节 = 905  文件数 = 4        （与跑前逐行相同）
== 跑前 == 跑后（逐文件字节数 + sha256）：True ==
== 总字节：905 -> 905 ==
```

- **`Ran 954 tests in 176.793s` / `OK (skipped=3)` / exit 0**：条数不变，**skipped 仍是 3**，本轮零红零抖（没有"重跑到绿"）。
- 三条 skip 这次**逐条点名齐了**（上一轮被 sidecar 子进程输出交织吃掉一条，本轮用 `capture_output` 收全）：
  POSIX 位权限 1 条 + reportlab 2 条，与 QA 在 `docs/18` 的点名完全一致；无条件 skip 0 条。
- **F4 修复未回退**：跑前/跑后 `var/features` 都是 **905 B / 4 文件**，逐文件字节数 + sha256 完全相同
  （QA 在 R2-3 量的基线也是 905 B / 4 文件）。

### 5.2 硬纪律复算（验收 4）

见 §2.2 第 6 段：两 exe 在 `dist/` 与包内都命中期望哈希；`release/SHA256SUMS-release.txt` 的
0.4.5 行与期望串逐字相同；`SIGNATURE-STATUS.json` 三份未触碰（本轮未打开写入路径）；
`tools/sign_and_hash.ps1` 35,048 B / BOM=True 未动。

### 5.3 范围自证（验收 7）

`git status --porcelain` 在本工作副本不可用（`fatal: not a git repository`，exit 128，与 t7/t8 同一事实），
给等价 mtime 清单（本轮开工 05:41 之后，排除 `.tmp/`、`.agent-teams/`、`node_modules/`、`__pycache__/`、
`.npm-cache/`、`.venv/`、`.git/`、`.pytest_cache/`；脚本 `.tmp/t10-scope.py`）：

```
  11-12 05:47:35        41,284  docs/api_contract.json                                  ← D1 重生成
  11-12 05:49:13        38,603  docs/05.3-release-checklist.md                          ← D3
  11-12 05:49:13        38,603  release/tracelens-0.5.0-release/docs/05.3-release-checklist.md   ← 包内同步
  11-12 05:49:21        19,108  docs/audit/v050-tests-tools.md                          ← D5
  11-12 05:49:21        19,108  release/tracelens-0.5.0-release/docs/audit/v050-tests-tools.md   ← D2 入包
  11-12 05:49:29        20,588  docs/audit/v050-release.md                              ← D5 行号时点块
  11-12 05:49:29        20,588  release/tracelens-0.5.0-release/docs/audit/v050-release.md       ← 包内同步
  11-12 05:51:59        35,041  docs/audit/v050-docs-sync.md                            ← D4 新增 §11
  11-12 05:51:59        35,041  release/tracelens-0.5.0-release/docs/audit/v050-docs-sync.md     ← 包内同步
  11-12 05:52:29         2,299  release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt    ← 25 条
  11-12 05:52:29   359,060,132  release/tracelens-0.5.0-release.zip                     ← 26 条目
  11-12 05:52:29           190  release/SHA256SUMS-release.txt                          ← 第 2 行同步
  11-12 05:56:02        20,705  docs/audit/v050-r3-closeout.md                          ← 本文（不入包，§2.3）
共 13 个
```

- 全部落在允许写域：`docs/**`（6 个）、`release/tracelens-0.5.0-release/**`（5 个）、
  `release/tracelens-0.5.0-release.zip`、`release/SHA256SUMS-release.txt`。
- **`agent_explainer/**`、`web/**`、`tests/**`、`scripts/**`、`desktop/**`（含两个已签 exe）、
  `tools/**`、`release/0.4.5/**`、`release/0.5.0/**` 均 0 命中** —— 未出现在清单里。
  `release/0.5.0/**` 与包内 `docker/**` 三件是 `shutil.copy2` 重拷（字节与 mtime 都保持源值，
  所以不落在本扫描窗口内；§2.2 第 2 段逐件核对字节相同，清单 25/25 复算通过）。
- 自引用口径：表里本文那一格是**扫描时刻（05:56）的快照**，填完本节它自己又会长一次；
  其余 12 格的 mtime 都早于扫描，不受影响。

### 5.4 发布件不变性的独立复算（t7 写的只读复算器，本轮重跑）

`python -X utf8 -u .tmp/t7-verify.py`（重打之后跑，日志 `.tmp/t10-t7verify.log` 176 行）：

```
== 1. 四份清单逐条复算 ==
  -> release/SHA256SUMS.txt: 2/2 条命中                                   [OK] 全过
  -> release/0.5.0/SHA256SUMS.txt: 1/1 条命中                             [OK] 全过
  -> desktop/build/dist/SHA256SUMS.txt: 2/2 条命中                        [OK] 全过
  -> release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt: 25/25 条命中   [OK] 全过
     （逐条含 docs/audit/v050-tests-tools.md  19,108 B —— D2 新入包那条被独立复算命中）
  权威摘要集合大小 = 31
== 2. zip 条目集合 == 目录文件集合、全 ZIP_STORED ==
  [OK]   条目集合 == 目录文件集合（26 条）
  [OK]   全部 ZIP_STORED（compress_type=0）
  zip 字节 = 359,060,132（权威 sha256 见 release/SHA256SUMS-release.txt 第 2 行）
== 3. 文档里的每个 sha256 片段都能前缀命中权威摘要 ==
== 4. 文档里的每个字节数都等于真实大小 ==
== 5. 两个已签 exe 的字节与摘要 ==
  [OK]   release/TraceLens-0.5.0-win-x64-Setup.exe 未被改动（151,838,192 B）
  [OK]   release/TraceLens-0.5.0-win-x64-portable.exe 未被改动（137,883,824 B）
== 结论：全部通过 ==
verify exit=0
```

这份复算器逐条检查"文档里写的每个 sha256 / 字节数是否命中权威摘要"，本轮改过的
`docs/05.3`（新加的 151,838,192 / 137,883,824 / 10,403,499 / 4,957,816 等数字）与
`docs/audit/v050-docs-sync.md`、`v050-release.md` 都在扫描范围内，全部 `[OK]`，无 `[FAIL]`。

---

## 6. 工具与副作用

- 脚本（`.tmp/`，非交付件）：`.tmp/t10-recon.py`（只读侦察）、`.tmp/t10-d3-053.py`（D3 改表）、
  `.tmp/t10-bundle-repack.py`（重打 + 复算，t5 那份的微调版）、`.tmp/t10-suite-gate.py`（套件 + `var/features` 指纹）、
  `.tmp/t10-scope.py`（范围 mtime 扫描）；
  日志 `.tmp/t10-recon.log`、`.tmp/t10-repack.log`、`.tmp/t10-full-suite.log`（自写 UTF-8，避开 PS 5.1 的 UTF-16 坑）、
  `.tmp/t10-t7verify.log`（`Tee-Object` 落的是 UTF-16，读要 `encoding='utf-16'`）。
- 复用 t5 的 `.tmp/v050-bundle-repack.py` 时发现它的 **CRLF/BOM 打印永假**
  （源码里写的是 `b'\\r\\n'` 这种字面反斜杠），所以 t5 实录里「CRLF = False」那格是**打印 bug，不是产物缺陷**
  ——本轮实测同一份清单 CRLF 行 = 条数（24/24、25/25）。本轮脚本用 `bytes([13,10])` 与 `codecs.BOM_UTF8` 实算。
- 未动：`agent_explainer/**`、`web/**`、`tests/**`、`scripts/**`、`desktop/**` 源码与产物、
  两个已签 exe、`tools/sign_and_hash.ps1`、`release/0.4.5/**`、`release/0.5.0/**`（只读源）、`.agent-teams/**`。

---

## 7. 终态复检（本轮最后一步实测，全部 exit 0）

**包内副本逐字节 == 仓库**（14 个映射位，重打之后仓库只新增了本文，所以同步关系未破坏）：

```
  [SAME] README.md 31,243 / docs/05.3-release-checklist.md 38,603 / docs/05.4-docker.md 38,141
  [SAME] docs/06-faq.md 15,271 / docs/06-feedback-form.md 7,588 / docs/06-user-guide.md 11,036
  [SAME] docs/NOTICE-third-party.md 5,558
  [SAME] docs/audit/v050-desktop.md 30,955 / v050-docker.md 28,227 / v050-docs-sync.md 35,041
  [SAME] docs/audit/v050-release.md 20,588 / v050-sidecar.md 17,886 / v050-tests-tools.md 19,108
  [SAME] docs/audit/v050-version.md 20,230
  [包内无] docs/audit/v050-r3-closeout.md  仓库 24,662 B   ← 本文，按 §2.3 口径故意不入包
  DIFF 数 = 0
```

**编码口径**（本轮改过的每个文件都量）：

```
  docs/audit/v050-r3-closeout.md      字节=24,662  BOM=False  U+FFFD=False
  docs/audit/v050-docs-sync.md        字节=35,041  BOM=False  U+FFFD=False
  docs/audit/v050-release.md          字节=20,588  BOM=False  U+FFFD=False
  docs/audit/v050-tests-tools.md      字节=19,108  BOM=False  U+FFFD=False
  docs/05.3-release-checklist.md      字节=38,603  BOM=False  U+FFFD=False
  docs/api_contract.json              字节=41,284  BOM=False  U+FFFD=False
```

**终态 `gen_api_contract --check`**：`[FAIL] 前端调用面`（8 端点）+ `[FAIL] 契约检查未通过：1 项`，
`[PASS] 可重复生成`、`[PASS] docs/03 §6.5 逐条比对` 都在，exit=1 = 既有既定口径。

**终态 `tools/sign_and_hash.ps1 -CheckEncoding`**（脚本一字未动，清单已换 25 条）：

```
[OK] …\desktop\build\dist\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\tracelens-0.5.0-release\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\SHA256SUMS-release.txt  190 字节  BOM=False 中文完好=True 条目=2 换行=CRLF
[OK] …\release\0.5.0\SHA256SUMS.txt  92 字节  BOM=False 中文完好=True 条目=1 换行=LF
[OK] …\release\tracelens-0.5.0-release\SHA256SUMS.txt  205 字节  BOM=False 中文完好=True 条目=2 换行=CRLF
[OK] …\release\tracelens-0.5.0-release\SHA256SUMS-bundle.txt  2299 字节  BOM=False 中文完好=True 条目=25 换行=CRLF
[OK] tools/sign_and_hash.ps1  35048 字节  BOM=True（脚本自身要求带 BOM）
编码审计通过：JSON 与文本清单全部 UTF-8 无 BOM、中文完好；脚本自身 BOM 在位
exit=0
```

⇒ 验收 1（唯一失败=8 端点）、2（25 条 + 26 条目 + 新 zip 上报 + 0.4.5 未变）、3（7 份 `v050-*.md` SAME）、
4（两 exe 命中）、5（954 / `OK (skipped=3)` / exit 0）、6（`var/features` 905 B 跑前=跑后）、
7（范围 13 个文件全在写作用域）逐项对上。

---

## 13. 队长收口决议（2026-11-12 05:58，t10 上报后由队长直接执行）

t10 报上来两个待决事项，队长按"推荐方案自主推进"授权定案：

### 13.1 第 8 份 audit 记录的收录范围（决议：入包）

t10 把本文件按 25/26 口径留在仓库外（入包会让条目变 27/26 且自引用）。D2 的根因正是
「记录晚于 zip」，所以**决定入包**，并把"自引用不可达"的口径一次性写死：

* 包内 `docs/audit/` 收录 **8 份** `v050-*.md`（到本文件为止）；`docs/05.3-release-checklist.md` §12
  增加『收录范围』声明；
* **最终这次重打的 zip 哈希不在任何包内文档里**（自引用不可达，§12 早就写了这条）——
  权威值只认 `release/SHA256SUMS-release.txt` 第 2 行；
* 本文件的文字在重打前定稿，重打只改哈希清单，不改本文件内容 → 复算时"包内==仓库"必然成立。

### 13.2 t5 那台重打包脚本的打印 bug（t10 顺带揪出来的，队长的脚本队长的账）

t10 实测发现：`.tmp/v050-bundle-repack.py` 打印 `BOM = False / CRLF = False` 两格**恒为 False**，
原因是脚本源码 f-string 里写的是字面量 `b'\\xef\\xbb\\xbf'` / `b'\\r\\n'`（双重转义），
判据拿的是 4 字节的 `\r\n` 文本去比对真 CRLF。

* **性质：打印 bug，不是产物缺陷** —— 产物侧 t10 与 QA 都独立复算过：CRLF 行数 == 条目数、
  前 3 字节非 BOM、逐条 sha256 全过；
* 记录留痕：`docs/audit/v050-docker.md` §12.5 里那格 `CRLF = False` 是打印 bug，
  以 QA t9 实测「bundle 清单 CRLF 正确」为准；
* 脚本已修（判据用真字节常量），修后重打输出 `CRLF=True BOM=False` 与实测一致。

---

## 14. 最终态与哈希链（t12 补齐 QA 的 D7）

### 14.1 总包哈希链（每段：条目数 / 字节 / 成因 / 能不能复算）

轮次标号沿用 `docs/audit/v050-docs-sync.md` §5 的表（R1…R6）。

| 轮次（时点） | 成因 | zip 条目 | zip 字节 | 该轮打印的 sha256 | 清单条数 | 可复算性 |
| --- | --- | --- | --- | --- | --- | --- |
| 链条起点 `fd0d39e9…` | 卡片给的更早一次打印值 | 未入档 | 未入档 | `fd0d39e9…` | 未入档 | **不可复算**：本仓库 `grep fd0d39e9` = **0 命中**（`docs/**` + `release/**` + `.tmp/**` 全扫），出处不在本仓库 → 登记进显式白名单，不作为验收依据 |
| R1（t5 收口） | 队长回补 docker 三件 | 24 | 358,989,166 | `66a50950f42228417dbc7128662ff018a5f697662600d356bbc46e22d6cc0ee4` | 23 | 历史值（QA t6 引用过） |
| R2 / R3 中间（t7） | F2 文档同步 / F1 + `v050-desktop.md` 补标注 | 24 | 359,001,409 / 359,002,611 | `71232f5f…` / `08a61812…` | 23 | 历史值（`v050-docs-sync.md` §5） |
| R3 终（t7 收口） | 本文档链 + `v050-docs-sync.md` 入包 | 25 | 359,034,202 | `41b9beab6a96a25d2c19d42e9f6e2f86a39ab83fdc0f6079a44a4af5306db197` | 24 | QA t9 按此态复算 24/24 全过 |
| R4（t10 收口） | D2 把 `v050-tests-tools.md` 入包 + D3/D4/D5 | 26 | 359,060,132 | `3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990` | 25 | 本文 §2.2（已标「已被取代」） |
| 越界中间态（队长 05:58） | `.tmp/v050-final-sync.py` 一度把仓库 `docs/**` 全量刷进包 | **59** | **360,828,384** | `3a71fabe…` | — | 中间态，**已逐条撤销**（QA t11 §7：禁入 0 条、空目录 0 个、包内外 `grep 3a71fabe` = 0 命中） |
| **R5（队长最终收口 = QA t11 的验收态）** | 第 8 份 audit（本文）入包 + LICENSE/CHANGELOG + 撤销越界 | **27** | **359,090,440** | `7bd4997507207217006aa61735436b49954aa53d8c309651ed1bc875b7e7ddd4` | **26** | QA t11 独立复算 26/26 全过；**本文写它 = 写上一只包的哈希**（合法历史值） |
| **R6（t12 本轮）** | D7/D8 入档：`docs/18` §9.1 命令重写、README 限制 12、本文 §14/§15、`v050-docs-sync.md` 时点标记 | **27**（= 目录 27 个文件） | **只认 `release/SHA256SUMS-release.txt` 第 2 行** | 同左（自引用不可达，见 §14.3） | **26** | 复算原文落 `.tmp/t12-repack.log`，不落本文 |

### 14.2 现行态（R6）的口径

* 包内 **27 个文件** = `README.md` + `LICENSE` + `CHANGELOG.md` + `SHA256SUMS.txt` + `SHA256SUMS-bundle.txt`
  + `SIGNATURE-STATUS.json` + `TraceLens-SelfSigned.cer` + 两个已签 exe
  + `docker/` 三件 + `source/` 一份 + `docs/` 6 份用户向 + `docs/audit/` **8 份** `v050-*.md`；
* `SHA256SUMS-bundle.txt` **26 条**（清单自身不入清单）、UTF-8 无 BOM、CRLF 行数 == 条数；
* **禁入清单（本轮实测 0 命中）**：`docs/05-evidence/`、`docs/schemas/`、`docs/17-*`、`docs/18-*`、
  `docs/19-*`、`docs/api_contract.json`、`docs/audit/r0-*`/`r2-*`/`r3-*`/`r4-*`、`docs/01-*`~`16-*`；空目录 **0 个**；
* QA 建议已登记：`.tmp/v050-final-sync.py` 的 `docs/**` 全量遍历应改白名单（脚本在 `.tmp`，不进交付）。

### 14.3 自引用口径（为什么 R6 的哈希不在本文里）

本文在 zip 里面：写它自己的哈希必然失效（写完就变），所以链条上**只有 R6 之前**的各轮哈希能落文档；
现行值只认 `release/SHA256SUMS-release.txt` 第 2 行（`0.4.5` 那一行逐字未变，期望串
`0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip`）。
本轮重打的复算原文（清单 26 条逐条复算、zip 27 条目 == 目录 27 文件、全 `ZIP_STORED`、两 exe 命中）
落 `.tmp/t12-repack.log` 与验收记录，不落本文。

### 14.4 D8：C1 命令在 PowerShell 5.1 实测（t12 真跑，打包 sidecar 本体）

服务端 = `desktop/build/resources/sidecar/agent-explainer-sidecar/agent-explainer-sidecar.exe`
（argv 数组直起，`--sidecar --port 18812 --mode local --data-dir .tmp\t12-c1\data --features .tmp\t12-c1\features --demo zh`；
`READY polls=1`，`/health` 返回 `"version": "0.5.0"`）。PS 版本 `5.1.22621.6133`（FullLanguage）。

```
form-OLD (inline backslash-quote, the broken one):  http=400
  {"code": "INTERVENE_BODY_INVALID", "message": "Request body is not valid JSON.",
   "detail": {"hint": "Expecting property name enclosed in double quotes", "pos": 1}}
form-A (file + --data-binary):  body-bytes=92  http=201
  {"ok": true, "identity": {"agent_id": "c1-portable", "seq": 1,
   "entry_hash": "7ab81c69a23d10c686c321ce976f435782f9bac60bdcd1117c59776f06649602", ...}}
form-B (Invoke-WebRequest / Invoke-RestMethod):  body-bytes=94  http=201
  {"ok": true, "identity": {"agent_id": "c1-portable-b", "seq": 2, "prev_hash": "7ab81c69…", ...}}
form-C (--% 停止解析符):  http=201        → seq=3（counts.identities 0 → 3，chain head 035bde1e…）
form-C′（QA §9.4 的单引号字面量 -d '{...}'）:  http=400（不带空格、带空格两种都试过，pos=1）
三端点：curl.exe -s -o NUL -w %{http_code}  /health 200  /api/secretariat/status 200  /ext/secretariat.js 200
账本落点（--features 指哪落哪）：identities.jsonl 1,130 B、identities.jsonl.lock 0 B、keyring.json 563 B（0o600）
解包目录 _internal\var 命中 = 0
```

结论写进 `docs/18` §9.1：写法 A / B / `--%` 三种实测可用（92/94 字节 → 201），
**QA 的"单引号字面量"写法本轮实测 400** ⇒ 两组观测并列（QA：控制台粘贴 + 回显服务器；本轮：`powershell -File` + 真服务端），
**不合并成"PS 5.1 普遍可用哪一种"的结论**；要给用户一条稳的就用 A（落文件）。
**等价验证边界**：本轮跑的是打包 sidecar 命令行，**不是便携版 GUI**；C1 的面板可见性、升级持久性、
`/S` 静默安装与便携版运行期 `_internal\var` 检查仍必须在**用户桌面会话**做。

顺带记两条本机坑（给用户写脚本的提醒，非产品缺陷）：

* `Start-Process -ArgumentList @('--data-dir', 'E:\DEEPSEEK 黑奴\…')` 会把含空格路径**拆成两个参数**：
  探针第一次因此让服务端收到 `--data-dir E:\DEEPSEEK`，内层 argparse 直接 usage 报错、服务没起，
  **未落任何文件**（复查 `E:\DEEPSEEK` 不存在 ⇒ 无越界写入）。桌面壳不受影响（node `spawn` 传 argv 数组），
  给用户脚本的建议：用 argv 数组（node / python subprocess），或给每个参数显式加引号。
* 含中文注释的 **UTF-8 无 BOM** `.ps1` 经 `powershell -File` 跑 → PS 5.1 按 ANSI 解码：
  `Unexpected token 'W' in expression or statement` + `The string is missing the terminator: ".`
  （这就是 D10，本轮在同一条命令上复现；改成纯 ASCII 后同一探针跑通）。

---

## 15. 已知问题登记（t12：D9 / D10 + 既有开放项）

| ID | severity | 问题 | 本轮处置 | 状态 |
| --- | --- | --- | --- | --- |
| **D9** | low | C1 的「程序目录不得有 `_internal\var`」检查**对便携版空转**：便携版运行期解包到 `%TEMP%`，exe 旁没有 `resources\`，`Test-Path C:\TraceLens-0.5.0\resources\…` 永远 False = 没验 | `docs/18` §9.1 加了 **2b)** 便携版正确查法（扫 `%TEMP%` 下各解包目录）+ 第 5) 步改用 `$appDir = %LOCALAPPDATA%\Programs\TraceLens*` 发现安装目录（只有安装版能用 `_internal\var` 这条断言）；本轮实测打包 sidecar 跑起来后 `_internal\var` 命中 0 | 文档口径已修；**便携版 GUI 运行期检查仍待用户桌面会话** |
| **D10** | info | 含中文的 UTF-8 **无 BOM** `.ps1` 被 PS 5.1 按 ANSI 读 → 解析失败（QA 复现过，本轮在同一命令上再复现） | 写进 `README.md` §11 已知限制第 **12** 条（存 UTF-8 with BOM 或纯 ASCII；粘贴进控制台不受影响）；本仓库 `tools/sign_and_hash.ps1` 已带 BOM，`-CheckEncoding` 自报 `BOM=True` | 已入档（不改脚本，`tools/**` 本轮禁改） |
| C1 | 开放 | 便携版 GUI / NSIS `/S` 的桌面会话实测（面板可见、重启计数不减、`_internal\var` 运行期为空） | 本轮只补命令可跑性与查法（§14.4 是等价验证） | **未闭合**，需用户桌面会话 |
| — | low | `tools/sign_and_hash.ps1` 第 **496 / 502** 行 `.zip` 句柄锁缺口（签名后立刻读签名，中间无释放/重试） | 只登记（本轮禁改 `tools/**`）；行号口径见 `v050-tests-tools.md` §6 第 1 条 | 开放 |
| — | low | `scripts/verify_e2e.py` 无总裁办断言；`docs/NOTICE-third-party.json` 自动生成脚本缺 | 只登记（本轮禁改 `scripts/**`） | 开放 |
| — | info | WinError 10053 偶发（`test_settings_api…test_method_contract`）；Docker Desktop 由 t5 拉起（pid 10108）；`.tmp/t7-*`、`.tmp/t8-*`、`.tmp/t10-*`、`.tmp/t12-*`、仓库根与 `.tmp` 下私有 DACL 的 `tmp*` 残留 | 只登记 | 开放 |
| — | 建议 | `.tmp/v050-final-sync.py` 的 `docs/**` 全量遍历改白名单（QA t11 §7 的建议，越界根因） | 本轮重打脚本 `.tmp/t12-bundle-repack.py` 用**精选白名单 14 位**（README + 6 份 docs + 8 份 audit），不做全量刷入 | 已按建议口径执行（脚本在 `.tmp`，不进交付） |

