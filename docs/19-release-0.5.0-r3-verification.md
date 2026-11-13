# R3 窄口径复验：D1/D2 闭合与总包不变量（t11 / QA）

> 复核人：`qa`。被验对象：**t10（R3-1）的 D1/D2 闭合 + 队长 05:58–06:00 的最终收口重打**。
> 口径：只验「D1/D2 是否真闭合 + 重打有没有破坏不变量」，不做扩大化复核。
> 每条结论都来自我自己跑的命令与原始输出；跑不了的写「未验证 + 原因」。
> 时间：2026-11-12 05:57 → 06:15（Asia/Shanghai）。写作用域：**只写本文件**。

---

## 0. 结论（三行内）

1. **可以打 tag。** D1（契约漂移）与 D2（总包缺件）均已闭合：`gen_api_contract.py --check` 唯一失败 = 8 个非总裁办端点、漂移项消失；总包 **27 条目 == 目录 27 文件 / 359,090,440 B / `7bd49975…ddd4` == `SHA256SUMS-release.txt` 第 2 行**，`SHA256SUMS-bundle.txt` **26 条逐条复算全过**。
2. **不变量未被重打破坏**：两个已签 exe 字节/哈希/签名状态一字未动（`UnknownError` 未被改成 `Valid`）；0.4.5 行逐字未变；`sign_and_hash.ps1` 未动（BOM=True、语法 0 错、mtime 仍 05:18）；`var/features` 跑前=跑后 905 B。
3. **C1 仍作为发布说明里的已知边界**（不用它挡 tag），但**文档里的 C1 登记命令跑不通**（D8）：PowerShell 5.1 把内联 JSON 拆碎（实测服务端只收到 `{\"` 2 字节），已在 §9 给出三种实测可用的写法；另 D7（最终态条目/字节数未入档）、D9（便携版 `_internal\var` 检查位空转）、D10（C1 脚本编码坑）见 §10。

| 验收项 | 判定 |
|---|---|
| D1 契约 | **闭合**（唯一失败=8 端点；mtime 05:47 > 03 文档 04:55；11 条总裁办路径与 `server.py` 逐条一致） |
| D2 总包 | **闭合**（27/27、全 STORED、26 条清单、8 份 audit SAME、禁入路径 0、空目录 0） |
| 不变量 | **未破**（exe/签名/0.4.5/ps1/`var/features`） |
| D3/D4/D5 入档 | **在档**（05.3 13/13 行时点、docs-sync §11 A/B/C 并列、tests-tools §6 + v050-release 时点块、r3-closeout §1–§5） |
| 闸门 | **不回退**（954 / OK / skipped=3 / exit 0；i18n 5/5；selfcheck 92/92） |
| 范围自证 | **无产品代码改动**；t10 时段 13 文件、队长收口时段 7 文件，全部在 `docs/**` + `release/**` 白名单内 |

---

## 1. D1：契约漂移（**闭合**）

### 1.1 `--check` 最终态（我自己跑）

```bash
python tools/gen_api_contract.py --check    # EXIT=1
```

```
[PASS] 路由抽取 —— 41 条 (path, verb)，43 个 API_PATH 常量，公开路径 5 条，显式 405 组 3 组
[PASS] 权限矩阵 —— 4 个角色 / 8 个能力（路由用到 8 个）：admin=8, editor=5, local=7, viewer=3
[PASS] 错误码 —— 共 168 个，有 `<域>.error.<code>` 键 75 个，无键 93 个
[FAIL] 前端调用面 —— 界面调用后端没有的路由：['/api/analytics', '/api/compliance', '/api/evidence', '/api/groups',
        '/api/preferences', '/api/rollback', '/api/shares', '/api/suggestions']
[PASS] 可重复生成 —— 磁盘内容与本次生成字节一致（39246 字节）
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应，方法列一致，无 ⚠ 行
[FAIL] 契约检查未通过：1 项（详见上方逐条）
```

- **「契约漂移」项已消失**（t9 那轮是 2 项失败），失败重新收敛为**唯一 1 项 = 8 个非总裁办端点**；
- `[PASS] 可重复生成` 说明磁盘 json 与「现在这次生成」字节一致 → 不是手改出来的。

### 1.2 文件实名与时间序

```
ls -la docs/api-contract.json docs/api-contract.json docs/03-architecture.md
  ls: cannot access 'docs/api-contract.json': No such file or directory      ← 卡片里的连字符名字不存在
  -rw-r--r-- 1 Windows 197121 170991 Nov 12 04:55 docs/03-architecture.md
  -rw-r--r-- 1 Windows 197121  41284 Nov 12 05:47 docs/api_contract.json
```

- **实名是下划线 `docs/api_contract.json`**（t10 已登记，本轮实测确认）；
- `api_contract.json` 的 mtime **05:47** 晚于 `docs/03-architecture.md` 的 **04:55** ✓。

### 1.3 契约里的文档指纹 == 实测（证明是"重生成"不是"手改"）

```
json.sources["docs/03-architecture.md"] = sha256:ac603bc09a1857eb1f7cdec0bdf30af935d08f833b03a719477b6844339c0575
实测 sha256(docs/03-architecture.md)     = sha256:ac603bc09a1857eb1f7cdec0bdf30af935d08f833b03a719477b6844339c0575
一致 = True
```

### 1.4 总裁办路由：契约 vs `server.py` vs 冻结表（三方对照）

契约里 `secretariat` 路径 **11 条**（= `server.py` 的 8 个 `API_PATH_*` 常量 + 4 条子路由 − 1 条族入口复用），方法逐条与 `server.py` 的 `do_GET`/`do_POST` 分派一致：

| 契约路径（`verbs_by_path`） | 契约方法 | `server.py` 实测（行号） | 冻结表 `FROZEN_SURFACE` |
|---|---|---|---|
| `/api/secretariat` | GET | `4151` GET 走 status 摘要；`4491` POST → 405 | GET |
| `/api/secretariat/status` | GET | `4151` GET；`4491` POST → 405 | GET |
| `/api/secretariat/identities` | GET, POST | `4156` GET；`4426` POST → 201 | GET, POST |
| `/api/secretariat/identities/{id}/revoke` | POST | `3999` 前缀 + `4432` POST → 200 | POST |
| `/api/secretariat/delegations` | GET, POST | `4161` GET；`4442` POST → 201 | GET, POST |
| `/api/secretariat/records/{id}/sign` | POST | `4010` 前缀 + `4450` POST → 200 | POST |
| `/api/secretariat/verify` | POST | `4172` GET → 405；`4459` POST → 200 | POST |
| `/api/secretariat/letters` | GET, POST | `4166` GET；`4465` POST → 201 | GET, POST |
| `/api/secretariat/letters/{id}` | GET | `4021` letter_route → 200 | GET |
| `/api/secretariat/letters/{id}/verify` | POST | `4032` 前缀 + `4474` POST → 200 | POST |
| `/api/secretariat/seal` | POST | `4172` GET → 405；`4480` POST → 200 | POST |

`server.py` 的常量面（`grep -n API_PATH_SECRETARIAT`）：

```
226: API_PATH_SECRETARIAT_STATUS = "/api/secretariat/status"
227: API_PATH_SECRETARIAT        = "/api/secretariat"
228: API_PATH_SECRETARIAT_IDENTITIES = "/api/secretariat/identities"
229: API_PATH_SECRETARIAT_RECORDS    = "/api/secretariat/records"      # + /{id}/sign
230: API_PATH_SECRETARIAT_DELEGATIONS= "/api/secretariat/delegations"
231: API_PATH_SECRETARIAT_VERIFY     = "/api/secretariat/verify"
232: API_PATH_SECRETARIAT_LETTERS    = "/api/secretariat/letters"      # + /{id}、/{id}/verify
233: API_PATH_SECRETARIAT_SEAL       = "/api/secretariat/seal"
```

**对照结论：8 个基础端点 + 4 条子路由的方法集合与 `server.py` 逐条一致，没有"契约里有、代码里没有"或反之的条目。**
（`/api/secretariat` 与 `/status` 的 POST 在代码里是显式 405，契约里不列 POST —— 与冻结表同口径。）

**D1 判定：闭合。**

---

## 2. D2：总包（**闭合**，按最终 27/26 态验）

### 2.1 zip ↔ 目录 ↔ 清单（我自己算的）

```
zip 条目数 = 27  文件条目 = 27  目录条目 = 0
压缩方式集合 = [0] (0=STORED)
zip 本体 = 359,090,440 B   sha256 = 7bd4997507207217006aa61735436b49954aa53d8c309651ed1bc875b7e7ddd4
目录文件数 = 27   zip-only = []   disk-only = []
包内 vs 仓库逐字节：相同 27 / 27

release/SHA256SUMS-release.txt（cat -A）
  0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip^M$
  7bd4997507207217006aa61735436b49954aa53d8c309651ed1bc875b7e7ddd4  tracelens-0.5.0-release.zip^M$
```

- 第 2 行 == 我实测 zip 哈希 ✓；**0.4.5 行 `0c5d5552…139ab` 逐字未变** ✓。

### 2.2 `SHA256SUMS-bundle.txt`

```
bytes=2397  BOM=False  CRLF=26  LF-only=0  条数=26  U+FFFD=0  含自身=False
逐条复算：OK 26 / FAIL 0
```

> 顺带把队长点名的那条**打印 bug** 在产物侧复算清楚：`CRLF 行数（26）== 条目数（26）`、`LF-only = 0`、前 3 字节非 BOM、26 条 sha256 全过。
> 结论：**产物一直是好的**；历史实录里那句 `CRLF = False` 是脚本 f-string 双重转义（`b'\\r\\n'` 4 字节）造成的**打印假值**。
> 现行 `.tmp/v050-bundle-repack.py` 已用真字节判据（`back.count(b"\r\n")`），实测输出与产物一致。

### 2.3 27 条目逐项（递归列，按队长要求核"精选口径"）

```
12442     CHANGELOG.md
6362      LICENSE
31243     README.md
2397      SHA256SUMS-bundle.txt
205       SHA256SUMS.txt
4918      SIGNATURE-STATUS.json
151838192 TraceLens-0.5.0-win-x64-Setup.exe
137883824 TraceLens-0.5.0-win-x64-portable.exe
814       TraceLens-SelfSigned.cer
759       docker/IMAGE-INFO.json
92        docker/SHA256SUMS.txt
64029696  docker/tracelens-0.5.0-image.tar
39163     docs/05.3-release-checklist.md
38141     docs/05.4-docker.md
15271     docs/06-faq.md
7588      docs/06-feedback-form.md
11036     docs/06-user-guide.md
5558      docs/NOTICE-third-party.md
30955     docs/audit/v050-desktop.md
28227     docs/audit/v050-docker.md
35041     docs/audit/v050-docs-sync.md
29514     docs/audit/v050-r3-closeout.md
20588     docs/audit/v050-release.md
17886     docs/audit/v050-sidecar.md
19108     docs/audit/v050-tests-tools.md
20230     docs/audit/v050-version.md
4957816   source/TraceLens-0.5.0-win-x64-portable-src.zip
```

**禁入路径扫描（包内不得有）**：

```
docs/05-evidence · docs/schemas · docs/17- · docs/18- · docs/api_contract · docs/api-contract
docs/audit/r0- r2- r3- r4- · docs/01- 02- 03- 07- 09- 10- 11- 12- 13- 14- 15- 16- 2.3-
docs/05-test* · docs/05-questionnaire · docs/05-real*
→ 命中 0 条
```

**空目录残留**：`0 个`（包内磁盘侧 4 个目录：`docs/`、`docs/audit/`、`docker/`、`source/`）。
**结论：队长的「全量 docs 刷入」越界已被完整撤销，最终是 t4 定的精选口径 + 8 份 audit。**

### 2.4 包内 8 份 `v050-*.md` 与仓库逐字节

```
包内 8: desktop, docker, docs-sync, r3-closeout, release, sidecar, tests-tools, version
仓库 8: 同名同集合（包内缺=[]，包内多=[]）
  SAME v050-desktop.md        30,955 B
  SAME v050-docker.md         28,227 B
  SAME v050-docs-sync.md      35,041 B
  SAME v050-r3-closeout.md    29,514 B     ← 之前缺的那份现在在包里
  SAME v050-release.md        20,588 B
  SAME v050-sidecar.md        17,886 B
  SAME v050-tests-tools.md    19,108 B
  SAME v050-version.md        20,230 B
```

### 2.5 收录范围声明（队长要求核实的两处）

- `docs/05.3-release-checklist.md` §12：**`本包收录范围`** 块在位——「包内 `docs/audit/` 收录 **8 份** `v050-*.md`（逐字列出 8 个名字），与仓库逐字节相同；`SHA256SUMS-bundle.txt` 逐条可复算；**最后一轮重打的 zip 哈希不在包内任何文档里**（自引用不可达）——权威值只认 `release/SHA256SUMS-release.txt` 第 2 行」✓
- `docs/audit/v050-r3-closeout.md` §13.1：决议「入包」+ 三条口径（8 份、最终哈希不入文档、本文在重打前定稿）✓
- **两处口径一致**（都写 8 份 + 自引用不可达 + 权威只认 SUMS-release 第 2 行）。

**D2 判定：闭合。**

---

## 3. 不变量未破（**PASS**）

```
sha256sum release/TraceLens-0.5.0-win-x64-Setup.exe
  37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d   151,838,192 B  (mtime Nov 12 03:29)
sha256sum release/TraceLens-0.5.0-win-x64-portable.exe
  8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52   137,883,824 B  (mtime Nov 12 03:29)
包内同名两件：哈希与上面逐字相同

Get-AuthenticodeSignature
  Setup.exe    Status=UnknownError  Thumbprint=<redacted-thumbprint>  Bytes=151838192
  portable.exe Status=UnknownError  Thumbprint=<redacted-thumbprint>  Bytes=137883824

release/SIGNATURE-STATUS.json（sha256=875f84f0f19aecaf…dbeeae，与包内逐字节相同）
  signed_count=0  signatures_applied=2  unsigned=True  trust_state=self_signed_root_untrusted
  artifacts[0] Setup.exe    151,838,192  37cf6963…0395d  UnknownError  DF1CB56A…
  artifacts[1] portable.exe 137,883,824  8bdc55a0…fdd52  UnknownError  DF1CB56A…

tools/sign_and_hash.ps1: 35,048 B  BOM=True  U+FFFD=0  mtime=Nov 12 05:18（t8 之后未再被改）
  PowerShell Parser::ParseFile → syntax_errors=0
  496: Set-AuthenticodeSignature @signArgs | Out-Null
  502: $check = Get-AuthenticodeSignature -LiteralPath $a.FullName      ← 缺口仍在（只登记不修）
```

**`UnknownError` 没有被写成 `Valid`；两个 exe 的字节/哈希/签名三件套一字未动。**

---

## 4. D3 / D4 / D5 入档

| 项 | 实测 | 判定 |
|---|---|---|
| `docs/audit/v050-r3-closeout.md` | 存在；**437 行 / 29,514 B**；§1.1 基线(2 项 FAIL) → §1.2 `--write` 原文 → §1.3 `--check` 复跑(漂移消失) → §1.4 逐键比对 → §2.1 基线 → §2.2 处置与重打原文 → §3 D5 → §4 D3 → §5 闸门 → §7 终态复检 | **在档，before→after 齐全** |
| D3 行内时点 | `docs/05.3-release-checklist.md` §1 表新增第 4 列 `数据时点`；**13 行表格行，13 行都带「时点 / 0.5.0」标记**；表下有「『数据时点』列口径」说明（与 §10.1、§11 一致） | **闭合** |
| D4 两组观测并列 | `docs/audit/v050-docs-sync.md` §11 三行表：**A** = 队长 t3 直连控制台的 `TraceLens.exe: bad option: --user-data-dir` 文本；**B** = 我 t9 的 `exit=9 / stdout+stderr 0 B / 无 bad option`；**C** = 本机无参 GUI 也起不来（`4294930435`）。明确写「**两组观测都登记、不合并成一个结论**」，并列出「能证明/不能证明」与差异点；`--user-data-dir`/`--no-sandbox` 不在 `main.js` 开关表内这一点也写进"代码事实" | **并列在档，未合并** |
| D5 行号 | `docs/audit/v050-tests-tools.md:269-274`「**现位于 496 / 502 行**」+ 按锚点核对的命令；`docs/audit/v050-release.md:58-61` 原文保留 + 「行号时点更正」块（448 → 496/502）；脚本本身一字未动（35,048 B / mtime 05:18） | **闭合** |

---

## 5. 闸门不回退

### 5.1 全量套件

```bash
python -m unittest discover -s tests -p "test_*.py" -q
EXIT=0
Ran 954 tests in 191.852s
OK (skipped=3)
```

三条 skip 逐条点名（单跑两模块 `-v` 抓的原文，与前两轮同一组）：

```
test_settings_api.PersistenceTests.test_file_mode_is_owner_only_on_posix ... skipped 'POSIX 位权限只在类 Unix 文件系统上有意义'   → skipped=1
test_audit_log.PdfExportTests.test_no_reportlab_falls_back_to_printable_html ... skipped '本机有 reportlab（None）…'              → skipped=2
test_audit_log.PdfExportTests.test_prefer_pdf_raises_when_reportlab_is_unavailable ... skipped "本机有 reportlab…"
```

→ **skipped 仍是 3（POSIX 1 + reportlab 2），没有新增 skip、没有用 skip 消红。**

### 5.2 i18n / selfcheck

```
python tools/i18n_lint.py  → EXIT=0，5/5 PASS（字典 zh 1115 / en 1115 / 27 域；错误码 168；演示集 12×2）

cp -r agent_explainer/web .tmp/t11-web && (cd .tmp/t11-web && node selfcheck.mjs)
  self-check: 92/92 passed (report: web/selfcheck-report.json)
仓库 report sha256 前/后：6d52cdd5691cc189 / 6d52cdd5691cc189  相同=True   ← 在副本跑，没写仓库
```

### 5.3 F4：`var/features` 跑前/跑后

```
跑前：du -sb → 905；4 文件
  0        e3b0c44298fc1c14…  heads.jsonl.lock
  567      10481ef7df19d1b4…  identities.jsonl
  0        e3b0c44298fc1c14…  identities.jsonl.lock
  338      c71a94128b604b82…  keyring.json
跑后：du -sb → 905；4 文件；集合相同；逐文件 sha256 全同（SAME ×4）
```

---

## 6. 范围自证（无 git，用 mtime）

`git status --porcelain` → `fatal: not a git repository (exit 128)`（与 t8/t9 一致）。

### 6.1 t10 时段（05:45 起）13 个文件 —— 全部在 `docs/**` + `release/**` 白名单

```
05:47:35  41284      docs/api_contract.json
05:49:13  38603      docs/05.3-release-checklist.md
05:49:13  38603      release/tracelens-0.5.0-release/docs/05.3-release-checklist.md
05:49:21  19108      docs/audit/v050-tests-tools.md
05:49:21  19108      release/tracelens-0.5.0-release/docs/audit/v050-tests-tools.md
05:49:29  20588      docs/audit/v050-release.md
05:49:29  20588      release/tracelens-0.5.0-release/docs/audit/v050-release.md
05:51:59  35041      docs/audit/v050-docs-sync.md
05:51:59  35041      release/tracelens-0.5.0-release/docs/audit/v050-docs-sync.md
05:52:29   2299      release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt
05:52:29  359060132 release/tracelens-0.5.0-release.zip
05:52:29    190      release/SHA256SUMS-release.txt
05:57:13  27753      docs/audit/v050-r3-closeout.md
```

### 6.2 队长最终收口时段（05:57 起）7 个文件

```
05:59:21  29514      docs/audit/v050-r3-closeout.md
05:59:21  29514      release/tracelens-0.5.0-release/docs/audit/v050-r3-closeout.md
05:59:28  39163      docs/05.3-release-checklist.md
05:59:28  39163      release/tracelens-0.5.0-release/docs/05.3-release-checklist.md
06:00:09   2397      release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt
06:00:09  359090440  release/tracelens-0.5.0-release.zip
06:00:09    190      release/SHA256SUMS-release.txt
```

### 6.3 产品代码 0 改动（05:45 起，`agent_explainer/ desktop/ scripts/ tools/ tests/ Dockerfile docker-compose.yml`）

```
产品/测试/工具改动: 0 个
```

`tools/sign_and_hash.ps1` mtime 仍是 **05:18（t8）**，t10 与队长收口都没碰脚本。
**范围判定：t10 + 队长收口只动了 `docs/**` 与 `release/**`；产品代码 0 改动；无第三方文件被改。**

---

## 7. 队长收口那次越界与撤销（队长的账，不是产品的缺陷）

队长在 05:58 的同步里，`.tmp/v050-final-sync.py` **一度把仓库 `docs/**` 全量刷进包**
（`for p in (ROOT/"docs").rglob("*")` 全量复制），造成中间态：

```
59 条目 / 360,828,384 B / sha256 3a71fabe…
```

随后用 `.tmp/v050-final-revert.py` 逐条撤销（`KEEP_AUDIT` = 8 份 `v050-*`，其余 `docs/**` 全删），回收为
**27 条目 / 359,090,440 B / 7bd49975…**（= 现行态）。我的独立核实：

| 核实项 | 实测 |
|---|---|
| 中间态是否留痕 | 包内/仓库文档里 `grep 3a71fabe` = **0 命中**；`.tmp` 日志里也没有（只有脚本与队长口述） |
| 越界内容是否残留 | **禁入路径 0 条**（§2.3 的全表扫描，含 `05-evidence`/`schemas`/`17-`/`18-`/`api_contract`/`r0|r2|r3|r4-`/`01-`~`16-`） |
| 空目录 | **0 个** |
| 撤销后的 docs 面 | 6 份用户向 + 8 份 audit（`find` 逐一列出，与 §2.3 的 27 条目不差一条） |
| 权威哈希 | 现行 `7bd49975…` 只出现在 `release/SHA256SUMS-release.txt` 第 2 行，**没有进任何文档**（自引用口径正确） |

**判定：越界已被完整撤销，最终产物与精选口径一致；这是流程账（同步脚本写宽了），不是产品缺陷。**
建议：把 `.tmp/v050-final-sync.py` 的 `docs/**` 全量遍历改成白名单，避免下次重打再次扫宽（脚本在 `.tmp`，不进交付）。

---

## 8. 打印 bug 的产物侧复算（t10 上报、队长记账那条）

```
SHA256SUMS-bundle.txt: bytes=2397  BOM=False  CRLF=26  LF-only=0  条数=26  含自身=False
逐条复算 26/26 通过
.z 现行 repack 脚本判据（.tmp/v050-bundle-repack.py:58）：crlf_lines = back.count(b"\r\n")   ← 真字节，不是双重转义
```

→ **产物侧一直是好的**（CRLF 行数 == 条目数、无 BOM、逐条 sha 全过）；t5/t6 实录里 `CRLF = False` 那格是**打印假值**，
与产物无关。现行脚本已改真字节判据，输出与实测一致。

---

## 9. C1 的确切用户命令：逐字复核（**原文跑不通 → D8**）

### 9.1 待核原文（`docs/18-release-0.5.0-followup.md` §9.1 的关键两行，逐字）

```powershell
$owner = "did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke"
curl.exe -s -X POST -H "Content-Type: application/json" `
  -d "{\"agent_id\":\"c1-portable\",\"owner_did\":\"$owner\"}" `
  http://127.0.0.1:18802/api/secretariat/identities        # 期望 201 且 seq=1
```

### 9.2 实测（本机 PowerShell 5.1 + 一个本地回显服务器，把"服务端收到什么"打出来）

```
=== A) 文档原样写法 ===
argv（python 打印）: ["-d", "{\\", "agent_id\\:\\c1-portable\\, ..."]
服务端收到: POST /api/secretariat/identities  len=2  body="{\""
```

→ **body 只有 2 字节 `{"`，其余 token 被当成额外参数**；真服务端会回 400/422，**登记不到身份** ⇒ 这条命令**跑不通**。
（原因：PowerShell 5.1 里 `\` 不是转义符，`\"` 会提前结束字符串。）

### 9.3 另外两种"看起来对"的写法也不通（同样实测）

```
=== B) ConvertTo-Json + --data-raw $body ===
服务端收到: len=84  body="{agent_id:c1-portable,owner_did:did:tracelens:user:…}"     ← 引号被吃掉，非法 JSON
```

### 9.4 三种实测可用的写法（服务端都收到 **92 字节合法 JSON**）

```powershell
# 推荐：ConvertTo-Json 落文件 + --data-binary @文件（变量可插值、不依赖引号转义）
$owner = "did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke"
$body  = @{ agent_id = "c1-portable"; owner_did = $owner } | ConvertTo-Json -Compress
Set-Content -LiteralPath "$env:TEMP\c1-body.json" -Value $body -Encoding ASCII -NoNewline
curl.exe -s -X POST -H "Content-Type: application/json" `
  --data-binary "@$env:TEMP\c1-body.json" http://127.0.0.1:18802/api/secretariat/identities

# 或者用 PowerShell 原生（不经过 curl 的参数引号层）
$r = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $body `
     -Uri "http://127.0.0.1:18802/api/secretariat/identities"; $r | ConvertTo-Json -Compress

# 或者单引号字面量 JSON（不能插值，DID 写死）
curl.exe -s -X POST -H "Content-Type: application/json" `
  -d '{"agent_id":"c1-portable","owner_did":"did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke"}' `
  http://127.0.0.1:18802/api/secretariat/identities
```

实测回显：`{"ok":true,"identity":{"seq":1}}`（三种都通）。

### 9.5 C1 其余命令的可跑性（逐条）

| 命令 | 可跑性 | 依据 |
|---|---|---|
| `mkdir C:\TraceLens-0.5.0` / `Copy-Item` / `Set-Location` | 可跑 | 纯 ASCII 路径，符合 README 问题 11 |
| `$env:TRACELENS_MODE/PORT/DATA_DIR/FEATURES_DIR` | 可跑（**变量名正确**） | `desktop/main.js:59-77, 252-256` 确实读这四个；`TRACELENS_SELFCHECK` 也存在 |
| `Start-Process .\TraceLens-0.5.0-win-x64-portable.exe` | 可跑（在用户桌面会话）；本会话 GUI 起不来（exit 9 / 4294930435） | t9/t10 观测，成因不可判 |
| `curl.exe -s -o NUL -w "…%{http_code}`n"` | **可跑** | 实测 `health=200`（回显服务器） |
| `Get-ChildItem C:\TraceLens-0.5.0\userdata\features\secretariat` | 可跑，路径正确 | `TRACELENS_FEATURES_DIR` 指哪就落哪（t6 已验证） |
| `Test-Path C:\TraceLens-0.5.0\resources\...\_internal\var` | **对便携版是空转（D9）** | 便携版运行期解包到 `%TEMP%`，exe 旁没有 `resources\`；该断言永远 False，等于没验 |
| `.\TraceLens-0.5.0-win-x64-Setup.exe /S` + `$env:APPDATA\TraceLens 桌面端\...` | 可跑（用户会话） | `userData` 实测路径来自 t2 的 `recs.json` meta：`C:\Users\Windows\AppData\Roaming\TraceLens 桌面端\data\records.jsonl` |
| `--selfcheck --selfcheck-out` | 桌面会话复核项 | 本会话 exit=9（不可判） |

### 9.6 修正后的 C1 关键段（可直接给用户；替换 §9.1 那两行）

```powershell
# 1) 便携版（ASCII 目录；环境变量钉端口/数据/账本）
$env:TRACELENS_MODE="local"; $env:TRACELENS_PORT="18802"
$env:TRACELENS_DATA_DIR="C:\TraceLens-0.5.0\userdata\data"
$env:TRACELENS_FEATURES_DIR="C:\TraceLens-0.5.0\userdata\features"
Start-Process .\TraceLens-0.5.0-win-x64-portable.exe

# 2) 探活（另开窗口）
curl.exe -s -o NUL -w "health=%{http_code}`n"      http://127.0.0.1:18802/health
curl.exe -s -o NUL -w "secretariat=%{http_code}`n" http://127.0.0.1:18802/api/secretariat/status
curl.exe -s -o NUL -w "panel=%{http_code}`n"       http://127.0.0.1:18802/ext/secretariat.js
curl.exe -s http://127.0.0.1:18802/health            # 期望 "version":"0.5.0"

# 3) 登记一次身份（★ 用落文件写法，别用内联 \" 转义）
$owner = "did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke"
$body  = @{ agent_id = "c1-portable"; owner_did = $owner } | ConvertTo-Json -Compress
Set-Content -LiteralPath "$env:TEMP\c1-body.json" -Value $body -Encoding ASCII -NoNewline
curl.exe -s -X POST -H "Content-Type: application/json" --data-binary "@$env:TEMP\c1-body.json" `
  http://127.0.0.1:18802/api/secretariat/identities      # 期望 201、seq=1
Get-ChildItem C:\TraceLens-0.5.0\userdata\features\secretariat   # identities.jsonl + keyring.json + letters

# 4) 便携版"程序目录不得有 _internal\var"的正确查法（D9）：
#    便携版运行期解包在 %TEMP%，exe 旁没有 resources\；要查就查解包目录
Get-ChildItem $env:TEMP -Directory -Filter "*" -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName "resources\sidecar\agent-explainer-sidecar\_internal\var" } |
  Where-Object { Test-Path $_ }        # 期望：一条都没有

# 5) NSIS 安装（perMachine:false → 默认装到 %LOCALAPPDATA%\Programs\TraceLens*）
.\TraceLens-0.5.0-win-x64-Setup.exe /S       # 期望退出码 0
$appDir = Get-ChildItem "$env:LOCALAPPDATA\Programs" -Directory -Filter "TraceLens*" |
          Select-Object -First 1 -ExpandProperty FullName
"appDir = $appDir"
Test-Path "$appDir\resources\sidecar\agent-explainer-sidecar\_internal\var"     # 必须 False
Get-ChildItem "$env:APPDATA\TraceLens 桌面端\features\secretariat"              # 账本在用户目录
```

**写法注意（D10）**：上面这些命令**直接粘贴进 PowerShell 控制台**没问题；如果要存成 `.ps1` 再 `powershell -File` 跑，
必须存成 **UTF-8 with BOM**（或纯 ASCII）——本机实测：UTF-8 **无 BOM** 的 `.ps1` 里只要带中文注释，PS 5.1 按 ANSI 解码，
会报 `MissingArgument` / 乱码式 `ParserError`（我第一版探针就撞了这个，第二次改成纯 ASCII 才跑通）。

---

## 10. 本轮新增缺陷

| ID | severity | 缺陷 | 复现 | 影响 / 建议 |
|---|---|---|---|---|
| **D8** | **medium（C1 可用性）** | C1 文档里的**登记身份命令跑不通**：PowerShell 5.1 把内联 `-d "{\"a\":\"b\"}"` 拆成多参数，服务端只收到 `{\"` 2 字节；`ConvertTo-Json` + `--data-raw $body` 也会被吃掉引号（84 B 非法 JSON） | §9.2/§9.3 原文（本地回显服务器实测） | 用户照抄会登记失败 → 误判"总裁办坏了"。修法：改用 §9.4 的三种写法（落文件 `--data-binary @`、`Invoke-RestMethod`、单引号字面量），并回填 `docs/18` §9.1 |
| **D7** | low | **最终态没入档**：文档里 `grep 27 条目 / 359,090,440 / 7bd49975` = **0 命中**；包内 `v050-r3-closeout.md` §2.2 仍写「新 zip 上报 26 条目 / 359,060,132 B / `3257f7e1…`」、§2.3 写「包内 7 份」，**未标注已被队长 27 条目重打取代**（§13 只说"最终哈希不入文档"） | `unzip -l release/tracelens-0.5.0-release.zip \| wc -l` → 27；`grep -rn "27 条目" docs` → 0 | 读者按文档核对会与包不符（26 vs 27、7 份 vs 8 份）。**不影响校验**（权威哈希在 SUMS-release 第 2 行，且 8 份 audit 确实 SAME）。建议：在 §13 加「现行态：27 条目 / 359,090,440 B（哈希见 SUMS-release 第 2 行）；§2.2/§2.3 是 t10 时点」 |
| **D9** | low | C1 的「程序目录不得有 `_internal\var`」检查**对便携版空转**：便携版运行期解包到 `%TEMP%`，`C:\TraceLens-0.5.0\resources\...` 根本不存在 → `Test-Path` 永远 False，等于没验 | `Test-Path C:\TraceLens-0.5.0\resources\sidecar\...\_internal\var`（便携版目录里没有 `resources\`） | 会给出"通过"的假安全感。修法见 §9.6 第 4/5 段（便携版查解包目录；安装版查 `%LOCALAPPDATA%\Programs\TraceLens*`） |
| **D10** | info | C1 命令块含中文注释；存成 **UTF-8 无 BOM** 的 `.ps1` 再由 `powershell -File` 跑会解析失败（PS 5.1 按 ANSI 读） | 本轮探针第一版：`MissingArgument` + 中文乱码 ParseError；改纯 ASCII 后通过 | 复制粘贴进控制台不受影响；给用户的 `.ps1` 要存 BOM。建议在 C1 段加一行提示 |

> 上一轮（t9）的 D1/D2 记为**已闭合**；D3/D4（并列）/D5 记为**已入档**。
> 仍开放且不在本轮范围的：C1 本身（需用户桌面会话）、`scripts/verify_e2e.py` 无总裁办断言、`ps1` 的 `.zip` 句柄锁（496/502）。

---

## 11. 只读边界

| 动作 | 性质 |
|---|---|
| 新建 `docs/19-release-0.5.0-r3-verification.md` | **本轮唯一允许的写入** |
| `.tmp/t11-*`（契约/套件/i18n 日志、`var/features` 指纹）、`.tmp/t11-web/`（selfcheck 副本） | 证据与临时副本（草稿） |
| `.tmp/c1-probe.ps1`、`.tmp/c1-probe2.ps1`、`.tmp/c1-echo.log` | C1 命令可跑性探针（起了一个 127.0.0.1:18855 的本地回显服务器，用完已关） |
| 发布件 / 校验和 / 签名 / 产品代码 / 他人文档 | **只读**（`docs/api_contract.json`、`release/**`、`tools/**`、`tests/**` 均未被我写） |

---

## 附录 A：本轮命令清单

```bash
cd "E:/DEEPSEEK 黑奴/agent-explainer"
export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"

# 1 D1
python tools/gen_api_contract.py --check
ls -la docs/api_contract.json docs/03-architecture.md
python -c "json.sources['docs/03-architecture.md'] vs sha256(docs/03-architecture.md)"
python -c "verbs_by_path 里 secretariat 的 11 条路径"
grep -n "API_PATH_SECRETARIAT" agent_explainer/server.py
sed -n '4148,4185p;4424,4500p' agent_explainer/server.py

# 2 D2
python -c "zipfile 27 条目 / 全 STORED / 27==27 / 逐字节 27/27"
python -c "bundle 26 条复算 + BOM/CRLF/LF-only"
cat -A release/SHA256SUMS-release.txt
python -c "禁入路径扫描 + 空目录扫描 + 8 份 audit SAME"

# 3 不变量
sha256sum release/TraceLens-0.5.0-win-x64-{Setup,portable}.exe release/tracelens-0.5.0-release/TraceLens-0.5.0-win-x64-*.exe
powershell -NoProfile -Command 'Get-AuthenticodeSignature …'
python -c "SIGNATURE-STATUS.json 摘要"
python -c "ps1 BOM/大小"; powershell -NoProfile -Command '[Parser]::ParseFile(…)'

# 4 D3/D4/D5
sed -n '7,25p' docs/05.3-release-checklist.md
sed -n "$(grep -n '^## 11' docs/audit/v050-docs-sync.md | cut -d: -f1),+40p" docs/audit/v050-docs-sync.md
grep -n "496\|502" docs/audit/v050-tests-tools.md docs/audit/v050-release.md
grep -n "^## " docs/audit/v050-r3-closeout.md

# 5/6 闸门 + F4
python -c "var/features 指纹(PRE)"
python -m unittest discover -s tests -p "test_*.py" -q
python -c "var/features 指纹(POST)"
python tools/i18n_lint.py
cp -r agent_explainer/web .tmp/t11-web && (cd .tmp/t11-web && node selfcheck.mjs)

# 6 范围
python -c "mtime 扫描（05:45 / 05:57 两窗）"

# 9 C1 可跑性
python -c "本地回显 HTTP 服务 127.0.0.1:18855"
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp/c1-probe.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp/c1-probe2.ps1
```

## 附录 B：证据落点

| 证据 | 路径 |
|---|---|
| 契约 `--check` | `.tmp/t11-contract.log`、`.tmp/t11-contract2.log` |
| 全量套件 | `.tmp/t11-discover.log` |
| i18n | `.tmp/t11-i18n.log` |
| F4 跑前指纹 | `.tmp/t11-var-pre.json`（跑后对照见本报告 §5.3） |
| selfcheck 副本 | `.tmp/t11-web/selfcheck-report.json`（92/92，仓库 report 未写） |
| C1 探针 | `.tmp/c1-probe.ps1`、`.tmp/c1-probe2.ps1`、`.tmp/c1-echo.log` |
