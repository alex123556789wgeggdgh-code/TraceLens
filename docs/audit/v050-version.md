# v050-version — T0 版本口径统一与构建链缺口闭合（0.4.5 → 0.5.0）

> 执行：backend（后端工程师：版本口径 / sidecar / 签名 / 镜像）· 2026-11-12
> 范围：**只做代码 / 脚本 / 文档改动，不构建任何产物**（未跑 build_sidecar 构建、
> electron-builder、docker、签名脚本；未触碰 `release/**`、`web/**`）。
> 环境：`cwd=agent-explainer`，`$env:TMP=$env:TEMP="$PWD\.tmp"`，
> `$env:PYTHONPATH="$PWD\.tmp\pyfix"`；Python 3.14.7、node v24、Windows PowerShell 5.1
> （本机 **无 `pwsh`**）。

---

## 1. 逐项 before → after

### 1.1 版本口径（单一来源 `server.py:SERVER_VERSION`）

| # | 文件:行 | before | after | 怎么验 |
|---|---|---|---|---|
| 1 | [`agent_explainer/server.py:167`](../../agent_explainer/server.py#L167) | `SERVER_VERSION = "0.4.5"` | `SERVER_VERSION = "0.5.0"`（注释改口径为「5.0 总裁办（R1–R4）」） | §2.5 `python -c` 打印 |
| 2 | [`agent_explainer/__init__.py:87`](../../agent_explainer/__init__.py#L87) | `__version__ = "0.1.0"` | `__version__ = "0.5.0"`（注释标明与 `SERVER_VERSION` 同源） | §2.5 |
| 3 | [`desktop/package.json:4`](../../desktop/package.json#L4) | `"version": "0.4.5"` | `"version": "0.5.0"` | §2.5 `json.load` |
| 3 | [`desktop/package-lock.json:3`](../../desktop/package-lock.json#L3) 与 [:9](../../desktop/package-lock.json#L9) | 两处 `"0.4.5"` | 两处 `"0.5.0"` | 同上 |
| 4 | [`docker-compose.yml:24`](../../docker-compose.yml#L24) 与 [:29](../../docker-compose.yml#L29) | `${TRACELENS_VERSION:-0.4.5}` ×2 | `${TRACELENS_VERSION:-0.5.0}` ×2 | §2.6 pyyaml 解析 |
| 4 | [`Dockerfile:62`](../../Dockerfile#L62) | `ARG TRACELENS_VERSION=0.4.5` | `ARG TRACELENS_VERSION=0.5.0` | §2.6 文本扫描 |
| 5 | [`tools/build_sidecar.py:484`](../../tools/build_sidecar.py#L550)（旧 484） | `--version default "0.4.5"` | `--version default DEFAULT_VERSION`＝`"0.5.0"`（新增模块常量，单一口径） | §2.2 `--check` |
| 5 | `tools/build_sidecar.py:140`（旧 140；现 [:196](../../tools/build_sidecar.py#L196)） | `PORTABLE_NAME.format(version="0.4.5")` 写死 | `.format(version=DEFAULT_VERSION)` → 打印 `…\TraceLens-0.5.0-win-x64-portable-src.zip`；并新增**版本口径比对行**（`--version` 默认 vs `server.py:SERVER_VERSION`，不一致打印 ⚠ 报警） | §2.2 |
| 6 | [`README.md:12`](../../README.md#L12) | 当前版本 `0.4.5`；「721 个文案键、22 个域」 | 当前版本 `0.5.0`；「1115 个文案键、27 个域」（i18n_lint 实测值，见 §2.3） | §2.3 |
| 6 | [`README.md:315`](../../README.md#L315) 已知限制第 9 条 | 「版本号两处不一致 … 以 0.4.5 为准」 | 划掉并标注**已收口（0.5.0）**：两处都是 `0.5.0`，单一来源仍是 `SERVER_VERSION`，`--check` 会比对 | §2.5 |
| 6 | [`docs/03-architecture.md:223`](../03-architecture.md#L223) | `/health` 回报 `version`（现 `0.4.5`） | 现 `0.5.0` | 文本 |
| 6 | [`docs/03-architecture.md:462`](../03-architecture.md#L462) | `SERVER_VERSION`（现 `0.4.5`） | 现 `0.5.0` | 文本 |
| 6 | [`docs/06-user-guide.md:7`](../06-user-guide.md#L7) | `> 版本 0.4.5 · 适用形态…` | `> 版本 0.5.0 · 适用形态…` | 文本 |
| 6 | [`docs/06-faq.md:237`](../06-faq.md#L237) | 「以 `/health` 的 `version` 为准（当前 `0.4.5`）；`__init__.py=0.1.0` 历史遗留」 | 当前 `0.5.0`；`__init__.py` 自 0.5.0 起与之相同（历史不一致已收口） | 文本 |
| 6 | [`docs/06-feedback-form.md:40`](../06-feedback-form.md#L40)（含 :41 示例文件名） | 产品版本 `0.4.5`；示例 `TraceLens-0.4.5-…` | 产品版本 `0.5.0`；示例 `TraceLens-0.5.0-win-x64-Setup.exe` | 文本 |
| 6 | [`CHANGELOG.md`](../../CHANGELOG.md) 顶部 | `当前 0.4.5` + 「`__version__` 仍 0.1.0，本轮不改代码」+ `## [Unreleased]` | 头部改 `0.5.0` + 说明块改成「0.5.0 起两处一致、`--check` 比对」+ **`## [0.5.0] — 2026-11-12`**（总裁办 R1–R4 / 团队收口 / 0.5.0 版本口径与打包链三段齐备），`## [0.4.5] — 2026-11-11` 历史段原样保留 | 文本 |
| 6 | [`docs/05.3-release-checklist.md`](../05.3-release-checklist.md) 新增 §11 | 只记录到 0.4.5 时点（含 §10.3「`__version__` 仍 0.1.0」历史条目） | 追加 §11「0.5.0 口径更新」表（版本 / hiddenimports / 签名统计口径 / 编码 / 调用形态 / 产物清单六项），**不改写历史条目** | 文本 |

**未动的历史快照（按要求）**：`docs/05-test-report.md`、`docs/05-test-plan.md`、
`release/**`（含 `release/tracelens-0.4.5-release/**`）。`tests/**`、`web/**` 一字未改。

### 1.2 打包链缺口

| # | 位置 | before（缺口） | after | 实测 |
|---|---|---|---|---|
| 7 | [`tools/build_sidecar.py:318-321`](../../tools/build_sidecar.py) 旧手写 hiddenimports | 写死 **8 个**：`server audit auth ingest settings store trace_adapter explanations`。**5.0 的 `secretariat` 整包不在清单里**，`crypto_sig`/`feature_store`/`groups`/`qr`/`rollback`/`sharing` 也全缺；新增模块无人补录 | 改成 **扫盘枚举**：`package_modules()` 遍历 `agent_explainer/**/*.py` → **19 个模块**（15 个业务模块 + 包 `__init__` + `__main__` + `sdk` + `sdk.client`），逐条 `--hidden-import`；`REQUIRED_PACKAGE_MODULES` 断言 15 个必备模块一个都不能少，**缺就 `SystemExit` 中止构建**；`--check` 打印模块数与 `secretariat` 命中情况 | §2.2：`19 个模块 （含 5.0 总裁办：True）` |
| 8 | [`tools/sign_and_hash.ps1`](../../tools/sign_and_hash.ps1) 统计口径 | 状态文件只有 `signed_count`（口径＝`Valid`/`NotTrusted`）+ `unsigned`。发布件里写 `signed_count=2`、`unsigned=false`，而两个产物 `Get-AuthenticodeSignature` 状态都是 `UnknownError` → `tests/test_e2e.py:786-795` 算出 `signed=0`，断言 `0 != 2` 长期红（全量套件那条已知红的真因） | 口径拆成**两个互不背书的事实**并全部如实：`signed_count`（只算 `Valid`/`NotTrusted`，自签名典型 **0**）、`signatures_applied`（文件里确实落了签名，看 `SignatureType`+签名人，自签名典型 **2**）、`unsigned`（＝`signed_count==0`，典型 **true**）、新增 `trust_state`（`trusted`/`partially_trusted`/`self_signed_root_untrusted`/`not_signed`）、`signed_by`（Subject+指纹）、`trusted_statuses`、`status_counts`、`encoding`；`status` **保持 `UnknownError` 原样**，`trust_note` 保留「自签名」字样。两条路径（造不出证书的 blocker 路径 / 主路径）同口径 | §2.7 真值表：UnknownError×2 → `signed_count=0 / applied=2 / unsigned=True / self_signed_root_untrusted`；Valid×1 → `1 / trusted`；NotSigned → `0/0/True/not_signed`。Python 侧按 `test_e2e` 原断言逐条复核通过 |
| 9 | 同上，输出编码 | 状态文件 / `SHA256SUMS.txt` 用 `Write-Utf8NoBom` 写，但**没有任何自检**：`.ps1` 源码一旦丢 UTF-8 BOM，Windows PowerShell 5.1 会按 ANSI/GBK 解码脚本里的中文字面量，写出的 `status_message`/`trust_note` 必是乱码（docs/09 §2 登记的历史现象）；`ConvertTo-Json` 在 5.1 上还会把中文转义成 `\uXXXX`（不可读） | `Write-Utf8NoBom` 加**三道自检**：写前剥 U+FEFF；文本含 U+FFFD（＝源码被 ANSI 解码的证据）直接 `throw` 拒绝出文件；写完回读字节断言「首三字节非 EF BB BF」＋「UTF-8 解码逐字还原」。新增 `Write-JsonReport`：`Expand-JsonUnicodeEscape` 把 `\uXXXX` 解回真字符（**逐字符扫描，`\\` 整对吞掉**，所以 `E:\\dir` 不会被解成单反斜杠而把 JSON 解坏；解完若不能回解析则退回转义版），再按 UTF-8 **无 BOM** 落盘。新增只读审计开关 `-CheckEncoding`；脚本启动时自检自身 BOM 并报警 | §2.8：样例 1849 字节、`BOM=False`、`json.loads` 通过、中文与 `E:\DEEPSEEK 黑奴\…` 路径完好；`-CheckEncoding` 三个发布件全 `[OK]` |
| 补 | `Get-Artifacts` 产物清单 | 只排除 `.cer/.signature/.txt/.json`：`-WhatIfPlan` 实测把 `builder-debug.yml`（构建调试输出）与 `tracelens-desktop-*-x64.nsis.7z`（150 MB 安装压缩中间层）当成交付物去签名 + 写进 `SHA256SUMS.txt` | 追加过滤 `.yml/.yaml/.log/.7z/.nupkg/.blockmap`，**跳过的文件名当场打印**（静默过滤＝无法核对清单完整性） | §2.9：产物清单＝安装包 + 便携版 + 源码便携包 3 项，跳过 5 项并列出 |
| 补 | [`desktop/package.json:20`](../../desktop/package.json#L20) `npm run sign` | 调 `pwsh` —— **本机无 `pwsh`**（只有 Windows PowerShell 5.1），命令必失败（`Get-Command pwsh` → CommandNotFoundException） | 改调 `powershell`（5.1 实测可跑本脚本）；脚本 `.EXAMPLE` 同步注明两种 shell | §2.9 |

---

## 2. 闸门输出（本机实测，2026-11-12）

### 2.1 全量套件基线（改动**前**，同一环境同一命令）

```
python -m unittest discover -s tests -p "test_*.py" -q
Ran 954 tests in 195.282s
FAILED (failures=1, skipped=3)
FAIL: test_signature_status_is_honest (test_e2e.ArtifactTests…)
AssertionError: 0 != 2 : signed_count 与产物状态自相矛盾：2 vs []
```
日志：`.tmp/t0-baseline-full.log`

### 2.2 `tools/build_sidecar.py --check`（只读报告，不构建）

```
  便携 zip 落点               = …\desktop\build\dist\TraceLens-0.5.0-win-x64-portable-src.zip
  版本号口径                     = --version 默认 0.5.0 / server.py SERVER_VERSION 0.5.0
  hiddenimports（扫盘全量）      = 19 个模块 （含 5.0 总裁办：True）
  结论：构建**不可用** —— PyInstaller 不可用（未安装，或 Python 版本不被支持）
  现成产物仍在（onedir）：…agent-explainer-sidecar\agent-explainer-sidecar.exe（可用于冒烟，不代表本次构建成功）
check exit=0
```
扫盘清单（19）：`agent_explainer`、`__main__`、`audit`、`auth`、`crypto_sig`、
`explanations`、`feature_store`、`groups`、`ingest`、`qr`、`rollback`、`sdk`、
`sdk.client`、`secretariat`、`server`、`settings`、`sharing`、`store`、`trace_adapter`。
`python -m py_compile tools/build_sidecar.py agent_explainer/__init__.py agent_explainer/server.py` → 退出 0。

### 2.3 `python tools/i18n_lint.py`

```
[PASS] 检查项 5 项：通过 5，失败 0；字典 zh 1115 / en 1115 键；错误码 168 个（有键 75，回退 message 93）；演示集 12 条 x 双语
exit=0
```

### 2.4 `node agent_explainer/web/selfcheck.mjs`

```
self-check: 92/92 passed (report: web/selfcheck-report.json)
exit=0
```

### 2.5 版本打印与 JSON 有效性

```
SERVER_VERSION = 0.5.0
__version__   = 0.5.0
main banner  = 0.5.0
BOTH 0.5.0  OK
desktop/package.json 0.5.0
desktop/package-lock.json 0.5.0
```

### 2.6 配置口径扫描 / YAML 解析

```
### 0.4.5 left in build-chain configs (must be 0)
（空 —— docker-compose.yml / Dockerfile / package.json / package-lock.json /
  build_sidecar.py / server.py / __init__.py 七个文件里已无 0.4.5）
compose parse OK; image = tracelens:${TRACELENS_VERSION:-0.5.0}
build args = {'TRACELENS_VERSION': '${TRACELENS_VERSION:-0.5.0}', 'TRACELENS_WITH_PDF': '${TRACELENS_WITH_PDF:-1}'}
```
（`package-lock.json:3493` 的 `">=20.5.0"` 是依赖的 engines 范围，与产品版本无关。）

### 2.7 签名口径真值表（把 `.ps1` 里的助手函数原样切片跑起来，不签名）

| 输入 | `signed_count` | `signatures_applied` | `unsigned` | `trust_state` | `artifact_count` |
|---|---|---|---|---|---|
| `UnknownError`×2（自签名典型，签名已落） | **0** | 2 | **True** | `self_signed_root_untrusted` | 2 |
| `Valid`×1 | 1 | 1 | False | `trusted` | 1 |
| `NotSigned`×1 | 0 | 0 | True | `not_signed` | 1 |

`signed_by` ＝ `CN=TraceLens Self-Signed (Local Only)（指纹 DF1CB56A）`。
再用 Python 按 `tests/test_e2e.py:786-795` 的**原断言**复核生成的样例：

```
BOM-free utf-8 : OK
json.loads     : OK (keys=13)
signed_count   : 0 == len(signed)=0 -> True
unsigned       : True == not signed -> True
artifact_count : 2 == 2 -> True
trust_note 自签名: True
status_message   : 已处理证书链，但是在不受信任提供程序信任的根证书中终止。
trust_state      : self_signed_root_untrusted | applied: 2
```
即：**新口径产出的状态文件能让那条红转绿**（无需把 `UnknownError` 写成 `Valid`）。

### 2.8 编码：脚本语法与三个发布件审计

```
ps1 BOM=True len=30080
parse errors (UTF8-decoded source): 0
```
```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign_and_hash.ps1 -CheckEncoding
=== SIGNATURE-STATUS.json 编码审计（只读，不改任何文件）===
[OK] …\desktop\build\dist\SIGNATURE-STATUS.json  2035 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\SIGNATURE-STATUS.json             2035 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\tracelens-0.4.5-release\SIGNATURE-STATUS.json  2035 字节  BOM=False 中文完好=True JSON可解析=True
编码审计通过：全部 UTF-8 无 BOM、中文完好、JSON 可解析
exit=0
```
**关键实测结论**：三个历史发布件的字节层**已经没有 BOM、中文完好**（按 UTF-8 解码可读、
按 GBK 解码才乱码）。docs/09 §2 登记的「三个发布件去 UTF-8 BOM」**在产物侧不复现**；
真正的乱码根因是 **`.ps1` 源码丢 BOM**（本轮用编辑工具改 `.ps1` 时**复现了一次**：
BOM 被剥 → 5.1 解析出 22 个「语法错误」、中文全成乱码；补回 BOM → 解析 0 错误）。
脚本现在启动自检 + 写时自检双道，产物侧保证 UTF-8 无 BOM。

### 2.9 产物清单过滤（`-WhatIfPlan`，只读）

```
（跳过 5 个非交付物：builder-debug.yml, SHA256SUMS.txt, SIGNATURE-STATUS.json,
  tracelens-desktop-0.4.5-x64.nsis.7z, TraceLens-SelfSigned.cer）
=== 产物 ===
  TraceLens-0.4.5-win-x64-portable-src.zip    3,483,619 字节
  TraceLens-0.4.5-win-x64-portable.exe      137,607,264 字节
  TraceLens-0.4.5-win-x64-Setup.exe         151,535,616 字节
exit=0
```

### 2.10 `python tools/gen_api_contract.py --check`

改动后第一次跑出现 **2 项失败**：除既有的「前端调用面 8 个端点」外，
新增 `[FAIL] 契约漂移 —— 磁盘与本次生成不同`，diff 只有两行 `sources` 哈希
（`agent_explainer/server.py`、`docs/03-architecture.md` 的 sha256 指纹随本轮编辑变化）。
处置：跑工具自带的 `--write` 重新生成 `docs/api_contract.json`（**未手改**该文件），
复跑 `--check` 后回到**唯一 1 项失败**：

```
[PASS] 路由抽取 —— 41 条 (path, verb)，43 个 API_PATH 常量，公开路径 5 条，显式 405 组 3 组
[PASS] 权限矩阵 —— 4 角色 / 8 能力：admin=8, editor=5, local=7, viewer=3
[PASS] 错误码 —— 共 168 个，有键 75，无键 93
[FAIL] 前端调用面 —— 界面调用后端没有的路由：['/api/analytics', '/api/compliance', '/api/evidence',
        '/api/groups', '/api/preferences', '/api/rollback', '/api/shares', '/api/suggestions']
[PASS] 可重复生成 —— 磁盘内容与本次生成字节一致（39246 字节）
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应
[FAIL] 契约检查未通过：1 项
```
8 个端点全是**非总裁办**端点（known red，docs/12 §11），与验收口径一致。

### 2.11 总裁办四模块

```
python -m unittest tests.test_secretariat tests.test_secretariat_contract \
  tests.test_secretariat_api tests.test_secretariat_autosign -q
Ran 98 tests in 13.173s
OK
exit=0
```

### 2.12 全量套件（改动**后**）

```
python -m unittest discover -s tests -p "test_*.py" -q
Ran 954 tests in 164.268s
FAILED (failures=1, skipped=3)
FAIL: test_signature_status_is_honest (test_e2e.ArtifactTests…)
```
日志：`.tmp/t0-postchange-full.log`
**与基线逐字对齐（954 / 1 failure / 3 skipped，且就是同一条）→ 无新增红。**
红的那条仍是产物侧旧状态文件（`signed_count=2` + `UnknownError`）造成的，
要等 T3 用改好的脚本真签名重打产物、T4 复核才转绿（本任务禁止跑签名与构建）。

---

## 3. 改动文件清单

`agent_explainer/server.py`、`agent_explainer/__init__.py`、`desktop/package.json`、
`desktop/package-lock.json`、`docker-compose.yml`、`Dockerfile`、`tools/build_sidecar.py`、
`tools/sign_and_hash.ps1`、`CHANGELOG.md`、`README.md`、`docs/03-architecture.md`、
`docs/06-user-guide.md`、`docs/06-faq.md`、`docs/06-feedback-form.md`、
`docs/05.3-release-checklist.md`、`docs/audit/v050-version.md`（本文件）。

**由工具/闸门自动重写的生成物（非手工编辑）**：
- `docs/api_contract.json` —— 工具自带 `--write` 重新生成（§2.10，源码哈希指纹变化）。
  ⚠ 该路径**不在 T0 声明的写作用域内**，但不重生成就无法满足「唯一失败是 8 个端点」的验收，
  故按工具的既定再生成步骤执行，特此报备队长。
- `agent_explainer/web/selfcheck-report.json` —— 由验收要求的 `node selfcheck.mjs`
  闸门自己写出（92/92 报告），`web/` 源码一字未改。

---

## 4. 遗留项 / 交接口（给 T1–T5）

1. **那条已知红仍在**（本任务如实记录，不掩盖）：三个 `SIGNATURE-STATUS.json` 是 0.4.5
   时代产物，`tool` 字段是 `electron-builder signtool (self-signed PFX)`（**不是**
   `sign_and_hash.ps1` 写的），内容 `signed_count=2`、`unsigned=false`、状态 `UnknownError`。
   它们在 `release/**`（历史产物，禁改）与 `desktop/build/dist/`（构建输出）。
   T3 真签名重打 0.5.0 产物后，新状态文件形如
   `signed_count=0 / signatures_applied=2 / unsigned=true / trust_state=self_signed_root_untrusted`
   → 该断言在 T4 转绿。**任何情况下都不许把 `UnknownError` 写成 `Valid` 来糊断言。**
2. **签名脚本的退出码语义**：没有任何「被 Windows 认可的签名」时脚本 `exit 1`（自签名
   典型如此，哪怕 2 个产物都落了签名）。T3/T4 的流水线必须把 `exit 1` 读成
   「自签名已落、根不受信任」，不能读成「构建失败」；`-Verify` 同口径。已写进
   `docs/03-architecture.md` §16 与 `docs/05.3-release-checklist.md` §11。
3. **编辑工具会剥掉 `.ps1` 的 UTF-8 BOM**（本轮实测发生）。T3/T4 每次改完
   `tools/sign_and_hash.ps1` 必须确认 `BOM=True`（脚本启动会自己打印 ⚠ 告警），
   否则 5.1 解码中文必乱码。
4. **PyInstaller 本机不可用**（`--check`：`pyinstaller_available=False`，Python 3.14.7
   不被支持；当前只有 `onedir` 现成 exe，`onefile` 缺失）。T3 需 `--install`/`--vendor`
   走离线轮子；跑不起来就按 5.3 纪律写「未产出 + 原始报错」，**不得谎报已打包**。
5. **文档里的 0.4.5 文件名口径保留**：README 交付表/校验命令、`docs/06-faq.md:36,202`、
   `docs/06-user-guide.md` 安装/镜像章节、`docs/05.3-release-checklist.md` §10 等
   写的是**已发布 0.4.5 产物的真实文件名与哈希**。本轮不改（改成 0.5.0 等于宣称
   还不存在的产物）。T4 重打完 0.5.0 产物、拿到真实哈希后统一改口径。
6. **Docker 侧只做静态核对**（YAML 解析 + 文本扫描）。真实 `docker build` /
   `compose up` / `GET /api/secretariat/status` 三形态验收是 T5 的活（本任务禁止跑 docker）。
7. `desktop/node_modules/.package-lock.json:3` 仍是 `"0.4.5"` —— npm 内部生成物，
   T2 跑 `npm install` 时自然刷新；不在「package-lock.json 那两处」口径内。
8. `npm run sign` 现调 `powershell`（5.1）。若某天只在 PS7 环境跑（无 5.1，Win10/11 上罕见），
   需换回 `pwsh`；脚本对两种 shell 都已验证语法（解析 0 错误）。
9. 契约检查的 `--write` 步骤：**任何改 `server.py` 或 `docs/03-architecture.md` 的任务
   都必须跑 `python tools/gen_api_contract.py --write`**，否则 `--check` 会多出一条
   「契约漂移」假红。本轮已替后续任务把基线跑齐。
