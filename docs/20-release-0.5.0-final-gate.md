# 0.5.0 终局验收（R4 / t13）：C1 命令可跑 · 总包不变量 · 打 tag 判定

> 复核人：`qa`。被验对象：**t12（R4-1）的 D7/D8 修复 + 最终重打（27 条目态）**。
> 口径：终局窄验——判 D7/D8 修没修好 + 不变量没被重打破坏 + 给可发布结论。
> 每条结论都来自我自己跑的命令与原始输出。时间：2026-11-12 06:33 → 07:00（Asia/Shanghai）。
> 写作用域：**只写本文件**。

---

## 0. 终局结论（三行）

1. **0.5.0 可以打 tag、可以发布。** D7/D8 已闭合（§9.1 的三种 C1 写法在 PowerShell 5.1 **真跑 201**；最终态 **27 条 == 27 文件 / 359,103,302 B / `e8c846db…24a5a` == `release/SHA256SUMS-release.txt` 第 2 行**），不变量（两个已签 exe、`SIGNATURE-STATUS.json` 的 `UnknownError`、0.4.5 行、8 份 audit、`var/features` 905 B）一字未破。
2. **已知边界（写进发布说明，不挡 tag）**：C1 的**便携版 GUI 与 NSIS 安装**仍需用户在桌面会话点一遍（本机无交互桌面）；自签名证书链终止于不受信任的根 ⇒ `UnknownError` + **SmartScreen 会警告**；D9（便携版 `_internal\var` 只能查 `%TEMP%` 解包目录）与 D10（含中文的 UTF-8 无 BOM `.ps1` 被 PS 5.1 按 ANSI 读）为已登记已知问题。
3. **新增缺陷 1 条 low + 2 条 info**（§9）：D11 —— `docs/18` §9.1 写法 B 标注「94 字节」实测是 **92 字节**（状态码 201 正确，仅字节数标错）；D12 —— 卡片所指 `v050-docs-sync.md` §2.2/§2.3 实际不存在，取代标记落在 §5/§6.1/§6.2/§8（内容到位、位置描述不符）；D13 —— §9.1 写法 A 的「期望 92」只在保持示例 `agent_id` 时成立。

| 验收项 | 判定 |
|---|---|
| 1 D8（C1 命令可跑） | **闭合**（A 92 B→201 精确复现；B 201；`--%` 201；旧写法 400 `pos=1`） |
| 2 D9（便携版查法） | **到位**（§9.1 2b) `%TEMP%` 扫描实测 0 命中；安装目录查法为何空转已写明） |
| 3 D10（.ps1 编码） | **入档**（README 限制 12 + §15 同口径；`sign_and_hash.ps1` 仍 BOM=True） |
| 4 D7（取代标记 + 哈希链） | **入档**（标记与 8 行链写全；现行值以 SUMS-release 第 2 行为准，实测一致） |
| 5 总包不变量 | **PASS**（27/27、全 STORED、26 条清单全过、0.4.5 行未变、8/8 SAME、禁入 0、空目录 0） |
| 6 闸门 | **PASS**（954/OK/skipped=3；i18n 5/5；契约唯一 8 端点；selfcheck 92/92；`var/features` 905 B 前后同） |
| 7 范围自证 | **PASS**（10 个文件全在 `docs/**`+`release/**`；产品代码 0 改动） |

---

## 1. D8：C1 命令可跑（**闭合**）——含观测冲突的并列判定

### 1.1 先确认 §9.1 顶部的口径与写法

`docs/18-release-0.5.0-followup.md` §9.1 现在顶部有：

```
> **本节命令已在 PowerShell 5.1 实测（t12 于 2026-11-12 逐条真跑，见 docs/audit/v050-r3-closeout.md §14.4）；
> 旧写法会把 JSON 拆碎（D8）**：内联 -d "{\"a\":\"b\"}" 在 PS 5.1 下服务端只收到半个 JSON，
> 真服务端回 400 INTERVENE_BODY_INVALID / pos=1，登记不到身份。
> 下面第 2) 步给三种写法：A 落文件 --data-binary @、B Invoke-RestMethod、C --% 停止解析符；
> 每种都标了请求体字节数 + 期望状态码。存成 .ps1 时请存 UTF-8 with BOM 或纯 ASCII（D10）。
```

三种写法都在，且各自标了「请求体字节数 + 期望状态码」→ **口径在位**。

### 1.2 真跑环境（不是拿源码态顶替）

```
被测二进制：desktop/build/resources/sidecar/agent-explainer-sidecar/agent-explainer-sidecar.exe
            sha256 = f00527ca2c9ac56787b2f8794029013c15422717c371f40c4f982f8e136f093a（10,403,499 B）
启动：      --sidecar --port 18862 --mode local --data-dir .tmp/t13b/data --features .tmp/t13b/features
            → GET /health 200、version=0.5.0、mode=local
PowerShell：5.1.22621.6133 | OutputEncoding=us-ascii | ConsoleCP=gb2312
```

### 1.3 §9.1 的三种写法：逐条真跑（原始输出）

```
--- A) file + --data-binary @ ---
file_bytes = 92                     ← 与文档「请求体 92 字节」一致
A code=201 upload=92                ← 201 复现（seq=1）
--- B) Invoke-RestMethod ---
B code=201 seq=2 body_str_len=92    ← 201 复现；请求体实测 92 B
--- C) --% stop parsing ---
C code=201 upload=85                ← 201 复现（本例 agent_id=c1-c，故 85 B）
--- OLD) inline escaped（文档说会 400 pos=1）---
{"code": "INTERVENE_BODY_INVALID", "message": "Request body is not valid JSON.",
 "detail": {"hint": "Expecting property name enclosed in double quotes", "pos": 1}, "request_id": "req-0.5.0-7"}
OLD code=400 upload=2               ← 与文档「服务端只收到半个 JSON → 400 pos=1」逐字一致
```

用文档示例 `agent_id=c1-portable` 复跑 A：`body_file_bytes = 92`、`A: code=201 upload=92` —— **92 B → 201 精确复现**。
用同一个 `$body` 走本地回显服务测 B 的实际发送长度：`content_length = 92`。

> **D11（low）**：文档写法 B 标注「实测请求体 **94** 字节」，我两次实测都是 **92 字节**（`$body.Length = 92`，回显服务 `content_length = 92`）。
> 状态码 201 正确、命令可跑，**只是字节数标错 2**。建议改成 92（或去掉该数字）。

### 1.4 观测冲突：单引号字面量那条（我 t11 的推荐）——**t12 的 400 复现，我的 92 B 不复现**

我 t11 §9.4 曾推荐 `-d '{"agent_id":"…","owner_did":"…"}'`（单引号字面量）并报「服务端收到 92 字节合法 JSON」。
t12 实测这条得 **400**。本轮我在同一台机器、同一 PS 5.1、用**打包 sidecar 本体**各跑一遍：

```
--- S1) 单引号字面量，无空格（agent_id=c1-s1）---
S1 code=400 upload=78
--- S2) 单引号字面量，冒号后带空格 ---
S2 code=400 upload=81
--- 我 t11 用的原样一行（agent_id=c1-portable）---
T11LINE code=400 upload=84
--- curl --trace-ascii 看实际发出的请求体 ---
> => Send data, 81 bytes (0x51)
  0000: {agent_id:c1-trace,owner_did:did:tracelens:user:jqics2l64nmhcxj2
  0040: csrk3waxysybmuke}
```

**实际发出的字节里，双引号被 PowerShell 5.1 吃掉了**（`{agent_id:…}` 而不是 `{"agent_id":"…"}`）⇒ 真服务端 400 `INTERVENE_BODY_INVALID`。
**结论：t12 的 400 可复现；我 t11 的「92 字节合法 JSON」在本轮环境（打包 sidecar + PS 5.1.22621 + GB2312 控制台）不可复现。**

**两组观测并列（不合并成单一结论）**：

| 观测 | 环境/上下文 | 结果 | 能证明 | 不能证明 |
|---|---|---|---|---|
| t11（我） | `.tmp/c1-probe2.ps1`（纯 ASCII 脚本）+ **本地回显服务** 127.0.0.1:18855 | 单引号字面量 → 收到 **92 B 带引号合法 JSON** | 该次调用在**那个探针上下文**里引号没被吃掉 | 不能证明任意脚本/任意上下文都成立 |
| t12（backend） | PS 5.1 + **打包 sidecar 真服务** | 单引号字面量 → **400**（试过带/不带空格） | 该写法在真服务端**不可靠** | 不能证明每次必失败（但足以判定不可交付） |
| t13（本轮） | PS 5.1.22621.6133 / ConsoleCP=gb2312 + **打包 sidecar 真服务**；另有 `--trace-ascii` 抓包 | S1/S2/t11 原样行 全部 **400**；wire 上是 `{agent_id:…}` | **单引号写法不可用**，t12 判定正确 | — |

**我的偏差披露**：t11 把这条列进「实测可用的三种写法」，是**我自己上一轮的判断偏差**——那次只验到「回显服务收到合法 JSON」，没有在**打包产品本体**上验 201；
t12 用真服务端证伪了它，本轮我复现了 t12 的结果，**以实测为准：§9.1 用 A/B/`--%` 而不用单引号，是对的。**

**D8 判定：闭合。**（§9.1 现在写的 A / B / `--%` 三种，在 PS 5.1 + 打包 sidecar 下都能跑通 201；旧内联写法按文档预期 400。）

---

## 2. D9：便携版 `_internal\var` 的正确查法（**到位**）

`docs/18` §9.1 新增了 `2b)`：

```
# 2b) 便携版"程序目录不得有 _internal\var"的**正确查法**（D9；旧的那行 Test-Path 装在 exe 旁边是空转）
#     便携版运行期把负载解包到 %TEMP%，exe 旁根本没有 resources\，所以
#     Test-Path C:\TraceLens-0.5.0\resources\sidecar\... → 永远 False，等于没验。
Get-ChildItem $env:TEMP -Directory -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName "resources\sidecar\agent-explainer-sidecar\_internal\var" } |
  Where-Object { Test-Path $_ }        # 期望：一条都没有（有 = t2 缺陷回归）
```

我自己跑这条（本机 `%TEMP%` 里此刻没有便携版解包目录，正是"干净"状态）：

```
TEMP scan hits = 0
install-dir style check on a portable run would test: C:\TraceLens-0.5.0\resources\sidecar\agent-explainer-sidecar\_internal\var
Test-Path that (portable layout) = False        ← 即使从没跑过便携版也是 False ⇒ 旧的查法确实空转
```

§9.1 第 5) 步也用 `$env:LOCALAPPDATA\Programs\TraceLens*` 发现安装目录，并明确「只有安装版能用这条 `_internal\var` 断言」。
`docs/audit/v050-r3-closeout.md` §15 有 D9 一行（含严重度、影响、处置）✓。

**D9 判定：到位。**

---

## 3. D10：.ps1 编码坑（**入档**）

- `README.md` §11「已知限制」第 **12** 条：*含中文的 UTF-8 无 BOM `.ps1` 会被 PowerShell 5.1 当 ANSI 读 → 解析失败*，附实测两个报错原文与绕法（存 BOM 或纯 ASCII；粘控制台不受影响）✓
- `docs/audit/v050-r3-closeout.md` §15 的 D10 行同口径，并指出 `tools/sign_and_hash.ps1` 已带 BOM、`-CheckEncoding` 会自报 ✓
- 脚本本体复核：`tools/sign_and_hash.ps1` = **35,048 B / BOM=True / mtime 05:18**（t12 没碰它）✓

**D10 判定：入档。**

---

## 4. D7：最终态入档 + 取代标记（**入档**）

### 4.1 取代标记（`docs/audit/v050-docs-sync.md`）

```
116: | R4（t10 收口，**已被取代**） | … | 26（= 目录 26 个文件） | 359,060,132 | `3257f7e1…9990`（该轮打印值，历史）
117: | R5（队长最终收口，**已被取代**） | … | **27** | **359,090,440** | `7bd49975…ddd4`（该轮打印值，历史）
121: > 都是**该轮时点的历史值**，**已被 R5 的 27 条目 / 359,090,440 B 取代**（包内 docs/audit 从 7 份变 8 份）
175: > **R1–R3 时点的历史值**，**已被最终态取代**：包内文件 27 个、SHA256SUMS-bundle.txt 26 条、
304: | `3257f7e1…`（26 条目 / 359,060,132 B）、`7bd49975…`（27 条目 / 359,090,440 B） | §5 的 R4/R5 行、r3-closeout §14.1 | …**每重打一次就失效**（zip 条目带 mtime），已标「已被取代」；现行权威只认 SUMS-release 第 2 行
```

> **D12（info）**：卡片写的「§2.2/§2.3 有取代标记」在本文件里**没有这两个编号**（`docs-sync` 的 §2/§3/§4 是 F1/F2/错数字的 before→after，无子节）；取代标记实际落在 **§5（R4/R5 行 + 块引用）、§6.1/§6.2（时点标记框）、§8（历史值白名单）**。
> 内容要求满足、位置描述不符 —— 建议下轮把卡片文案改成「§5 / §6 / §8」。

### 4.2 哈希链（`docs/audit/v050-r3-closeout.md` §14.1，八行写全）

| 轮次 | 条目数 | 字节 | 哈希 | 可复算性 |
|---|---|---|---|---|
| 链条起点 `fd0d39e9…` | 未入档 | 未入档 | `fd0d39e9…` | **不可复算**（仓库 `grep` 0 命中） |
| R1（t5） | 24 | 358,989,166 | `66a50950…0ee4` | QA t6 引用过 |
| R2/R3 中间（t7） | 24 | 359,001,409 / 359,002,611 | `71232f5f…` / `08a61812…` | 历史值 |
| R3 终（t7） | 25 | 359,034,202 | `41b9beab…6db197` | QA t9 复算 24/24 全过 |
| R4（t10） | 26 | 359,060,132 | `3257f7e1…9990` | 本文 §2.2（已标「已被取代」） |
| **越界中间态**（队长 05:58） | **59** | **360,828,384** | `3a71fabe…` | 中间态，已逐条撤销（QA t11 §7 复核 0 残留） |
| **R5（= t11 验收态）** | **27** | **359,090,440** | `7bd49975…ddd4` | 历史值 |
| **R6（t12 本轮）** | **27** | **只认清单** | **不打印**（自引用不可达，§14.3） | 权威 = `release/SHA256SUMS-release.txt` 第 2 行 |

现行实测（我自己算的）：

```
zip 条目 27 | 目录文件 27 | 集合相同 True
压缩方式 [0] | zip 字节 359,103,302
zip sha256 e8c846db6beaa2d59d2db9b7b5ef2edce5f4f1cc42d4585b7c84abf15f324a5a
release/SHA256SUMS-release.txt 第 2 行 == 实测 zip sha256 : True
```

> 说明：卡片说「哈希链多了两枚：`7bd49975` → `e8c846db` 现行，§14 应写全」。
> `7bd49975` 已在 §14.1 的 R5 行；`e8c846db` **按自引用规则不能写进包内文档**（本文档就在 zip 里，写完即失效），
> §14.3 已把这条口径写死「现行值只认 SUMS-release 第 2 行」。⇒ 链条在**可写范围内是完整的**，且我实测第 2 行 == 复算值。

**D7 判定：入档（含 D12 位置描述偏差）。**

---

## 5. 总包不变量（重打后必查，**PASS**）

```
zip 条目 = 27   目录文件 = 27   集合相同 = True   压缩方式 = [0]（全 ZIP_STORED）
zip 本体 = 359,103,302 B   sha256 = e8c846db6beaa2d59d2db9b7b5ef2edce5f4f1cc42d4585b7c84abf15f324a5a

SHA256SUMS-bundle.txt: bytes=2397  BOM=False  CRLF=26  LF-only=0  条数=26  含自身=False
逐条复算：OK 26 / FAIL 0

release/SHA256SUMS-release.txt
  0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip   ← 逐字未变
  e8c846db6beaa2d59d2db9b7b5ef2edce5f4f1cc42d4585b7c84abf15f324a5a  tracelens-0.5.0-release.zip

包内 8 份 v050-*.md vs 仓库 8 份：集合相同，逐份 SAME（desktop/docker/docs-sync/r3-closeout/release/sidecar/tests-tools/version）
禁入路径命中：[]      空目录：[]      包内 vs 仓库逐字节：27/27 相同
```

两个已签 exe 与签名状态（release 与包内各一份，逐字一致）：

```
37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d  Setup.exe     151,838,192 B
8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52  portable.exe  137,883,824 B
Get-AuthenticodeSignature：两者 Status=UnknownError、Thumbprint=<redacted-thumbprint>
SIGNATURE-STATUS.json：sha256 875f84f0f19aecaf…（t4 原值未变）、signed_count=0、signatures_applied=2、
                       unsigned=True、trust_state=self_signed_root_untrusted；两条 artifact 的字节/状态与实测一致
```

---

## 6. 闸门终局（**不回退**）

```
python -m unittest discover -s tests -p "test_*.py" -q
  EXIT=0    Ran 954 tests in 163.776s    OK (skipped=3)

三条 skip（单跑 -v 点名，与前三轮同一组）：
  test_settings_api.PersistenceTests.test_file_mode_is_owner_only_on_posix      （POSIX 位权限）
  test_audit_log.PdfExportTests.test_no_reportlab_falls_back_to_printable_html  （本机有 reportlab）
  test_audit_log.PdfExportTests.test_prefer_pdf_raises_when_reportlab_is_unavailable（本机有 reportlab）

python tools/i18n_lint.py            → EXIT=0，5/5 PASS（zh/en 1115 键 / 27 域 / 168 错误码 / 12×2 夹具）
python tools/gen_api_contract.py --check → EXIT=1，唯一 FAIL = 前端调用面 8 个非总裁办端点（其余 5 项 PASS）
node .tmp/t13-web/selfcheck.mjs      → self-check: 92/92 passed（副本跑；仓库 report sha 前后均 6d52cdd5… 未被写）

var/features：跑前 4 文件 / 905 B → 跑后 4 文件 / 905 B；集合相同、逐文件 sha256 全同
```

---

## 7. 范围自证（无 git → mtime）

```
git status --porcelain → fatal: not a git repository（exit 128）

t12 时段（06:10 起）改动 10 个文件：
  06:22:08  48117  docs/18-release-0.5.0-followup.md          ← §9.1 重写（D8）
  06:22:27  31786  README.md                                  ← 限制 12（D10）
  06:22:27  31786  release/tracelens-0.5.0-release/README.md
  06:23:48  39555  docs/audit/v050-r3-closeout.md              ← §14/§15（D7/D8/D9/D10）
  06:23:48  39555  release/tracelens-0.5.0-release/docs/audit/v050-r3-closeout.md
  06:31:50  37319  docs/audit/v050-docs-sync.md                ← 取代标记（D7）
  06:31:50  37319  release/tracelens-0.5.0-release/docs/audit/v050-docs-sync.md
  06:31:55   2397  release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt
  06:31:55 359103302 release/tracelens-0.5.0-release.zip
  06:31:55    190  release/SHA256SUMS-release.txt

产品/测试/工具代码改动（agent_explainer/ desktop/ scripts/ tools/ tests/ Dockerfile docker-compose.yml）：0 个
```

（`.tmp/v050-*.py` 在 05:59–06:00 之间已定版；t12 用的重打脚本在 `.tmp/t12-*.py`，不进交付。）

---

## 8. 已知边界清单（发布说明必须带）

| # | 边界 | 说明 / 处置 |
|---|---|---|
| B1 | **C1：便携版 GUI 与 NSIS 安装未实测** | 只能在用户桌面会话做（本会话无交互桌面，打包 Electron 起不来）。命令见 §10，**不得用源码态顶替** |
| B2 | **自签名不接受信任** | `Get-AuthenticodeSignature` = `UnknownError`（证书链终止于不受信任根）；**SmartScreen / 企业策略仍会警告**。消警需 OV/EV 证书，或管理员把证书导入 `LocalMachine\Root` + `LocalMachine\TrustedPublisher`；未加时间戳 ⇒ 证书过期后签名验证失效 |
| B3 | **D9（已登记）** | 便携版运行期解包到 `%TEMP%`，`_internal\var` 只能查解包目录；安装版才查安装目录 |
| B4 | **D10（已登记）** | 含中文的 UTF-8 无 BOM `.ps1` 被 PS 5.1 按 ANSI 读会解析失败；存 BOM 或纯 ASCII |
| B5 | `tools/sign_and_hash.ps1` 496/502 的 `.zip` 句柄锁缺口 | 只登记不修（当前靠"只签两个 PE 产物"绕开） |
| B6 | `scripts/verify_e2e.py` 无总裁办断言 | 42 项验收不含 5.0 总裁办（事实覆盖由 QA 容器实测补，脚本断言未补） |
| B7 | Docker 验收依赖守护可达 | 受限成员会话打不开 `dockerDesktopLinuxEngine` 管道；镜像与离线包已产出、可 `docker load` |
| B8 | **zip 哈希每轮失效** | zip 条目带文件 mtime ⇒ 两次"幂等"重打的哈希都不同；因此文档不自引用，**权威只认 `release/SHA256SUMS-release.txt` 第 2 行** |

---

## 9. 新增缺陷（本轮）

| ID | severity | 缺陷 | 复现 | 影响 / 建议 |
|---|---|---|---|---|
| **D11** | low | `docs/18` §9.1 写法 B 写「实测请求体 **94** 字节」，实测是 **92** | 同 §1.3：`$body.Length = 92`；回显服务 `content_length = 92`；写法 A 的文件长度也是 92 | 状态码 201 正确、命令可跑；只是数字标错 2，会让用户以为 A/B 发送内容不同。改成 92（或删掉该数字） |
| **D12** | info | 卡片所指 `docs/audit/v050-docs-sync.md` **§2.2/§2.3** 不存在；取代标记实际在 §5（R4/R5 行 + 块引用）、§6.1/§6.2、§8 | `grep -n "^### " docs/audit/v050-docs-sync.md` → 只有 §6.1–§6.5 | 内容要求已满足；只是位置描述不符，建议卡片/文档同步编号 |
| **D13** | info | §9.1 写法 A 的「`(Get-Item …).Length` 期望 92」只在保持示例 `agent_id = c1-portable` 时成立；用户改 `agent_id` 长度就会变（示例 `c1-a` 是 85） | 见 §1.3 两次不同 agent_id 的 file_bytes（92 / 85） | 不是错，建议加一句「数字随 agent_id 长度变，只要 >0 且 201 即可」 |

> 前几轮的 D1–D10 状态：D1/D2（t10）闭合、D3/D4/D5（t10）入档、D7/D8（t12）闭合、D9/D10（t12）入档。
> 无阻断缺陷。

---

## 10. 给用户的 C1 命令（原样贴全，可直接复制执行）

> 以 `docs/18-release-0.5.0-followup.md` §9.1 的实测版为准（本轮已逐条复跑通过）。
> 只需把 `<交付目录>` 换成你解压发布包的位置；**路径保持纯 ASCII**（勿放中文目录）。
> 若要存成 `.ps1` 再 `powershell -File` 跑，请存 **UTF-8 with BOM**（D10）。

```powershell
# 0) 准备：交付目录（含两个 exe 与本清单）拷到纯 ASCII 路径
mkdir C:\TraceLens-0.5.0
Copy-Item <交付目录>\TraceLens-0.5.0-win-x64-portable.exe C:\TraceLens-0.5.0\
Copy-Item <交付目录>\TraceLens-0.5.0-win-x64-Setup.exe    C:\TraceLens-0.5.0\
Set-Location C:\TraceLens-0.5.0

# 1) 便携版：用环境变量钉住端口与数据/账本目录（打包 exe 的 CLI 开关本会话不可判，改用 env）
$env:TRACELENS_MODE        = "local"
$env:TRACELENS_PORT        = "18802"
$env:TRACELENS_DATA_DIR    = "C:\TraceLens-0.5.0\userdata\data"
$env:TRACELENS_FEATURES_DIR= "C:\TraceLens-0.5.0\userdata\features"
Start-Process .\TraceLens-0.5.0-win-x64-portable.exe

# 2) 另一个 PowerShell 窗口：三条端点 + 登记一次身份（GUI 应显示"总裁办"面板）
curl.exe -s -o NUL -w "health=%{http_code}`n"      http://127.0.0.1:18802/health
curl.exe -s -o NUL -w "secretariat=%{http_code}`n" http://127.0.0.1:18802/api/secretariat/status
curl.exe -s -o NUL -w "panel=%{http_code}`n"       http://127.0.0.1:18802/ext/secretariat.js
curl.exe -s http://127.0.0.1:18802/health   # 期望 "version":"0.5.0"

#    登记身份（D8 已修：本节三种写法在 PowerShell 5.1 实测；旧的 -d "{\"a\":\"b\"}" 会被拆碎）
$owner = "did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke"

#    写法 A（推荐）：ConvertTo-Json 落文件 + curl --data-binary @文件 —— 实测请求体 92 字节 → 201
$body = @{ agent_id = "c1-portable"; owner_did = $owner } | ConvertTo-Json -Compress
Set-Content -LiteralPath "$env:TEMP\c1-body.json" -Value $body -Encoding ASCII -NoNewline
(Get-Item "$env:TEMP\c1-body.json").Length        # 期望 92（请求体字节数）
curl.exe -s -X POST -H "Content-Type: application/json" `
  --data-binary "@$env:TEMP\c1-body.json" `
  http://127.0.0.1:18802/api/secretariat/identities     # 期望 201 且 seq=1

#    写法 B：PowerShell 原生（不经 curl 的参数引号层）—— 实测请求体 92 字节 → 201
$r = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $body `
     -Uri "http://127.0.0.1:18802/api/secretariat/identities"; $r | ConvertTo-Json -Compress

#    写法 C：--% 停止解析符，后面原样交给 curl —— 实测 201
curl.exe --% -s -X POST -H "Content-Type: application/json" -d "{\"agent_id\":\"c1-portable\",\"owner_did\":\"did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke\"}" http://127.0.0.1:18802/api/secretariat/identities

#    ✗ 不要再用旧的写法（t12 实测：服务端只收到半个 JSON → 400 INTERVENE_BODY_INVALID pos=1）：
#      curl.exe -X POST -d "{\"agent_id\":\"c1-portable\",\"owner_did\":\"$owner\"}" …

Get-ChildItem C:\TraceLens-0.5.0\userdata\features\secretariat     # identities.jsonl + keyring.json + letters

# 2b) 便携版"程序目录不得有 _internal\var"的**正确查法**（D9；旧的那行 Test-Path 装在 exe 旁边是空转）
#     便携版运行期把负载解包到 %TEMP%，exe 旁根本没有 resources\，所以
#     Test-Path C:\TraceLens-0.5.0\resources\sidecar\... → 永远 False，等于没验。
Get-ChildItem $env:TEMP -Directory -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName "resources\sidecar\agent-explainer-sidecar\_internal\var" } |
  Where-Object { Test-Path $_ }        # 期望：一条都没有（有 = t2 缺陷回归）

# 3) 界面可见性：确认页面上有"总裁办"分区（五个分区：状态与策略 / 身份 / 责任链 / 认定书 / 验证与封印）

# 4) 升级持久性（界面级）：关窗口 → 把 C:\TraceLens-0.5.0 的 exe 换成新解压的一份（userData 不动）→ 再启动
#    → /api/secretariat/status 的 counts.identities 仍为 1，keyring.json 仍在

# 5) NSIS 安装与安装态复核（perMachine:false → 默认装到 %LOCALAPPDATA%\Programs\TraceLens*）
.\TraceLens-0.5.0-win-x64-Setup.exe /S        # 静默安装；期望退出码 0
$appDir = Get-ChildItem "$env:LOCALAPPDATA\Programs" -Directory -Filter "TraceLens*" |
          Select-Object -First 1 -ExpandProperty FullName
"appDir = $appDir"
Test-Path "$appDir\resources\sidecar\agent-explainer-sidecar\_internal\var"     # 安装版这条必须 False（D9：只有安装版能这样查）
# 开始菜单启动 TraceLens → 同 1)–4) 的端点与面板检查
Get-ChildItem "$env:APPDATA\TraceLens 桌面端\features\secretariat"     # 账本落用户目录
Test-Path "$env:APPDATA\TraceLens 桌面端\data\records.jsonl"           # 记录持久化
# 安装目录内不得出现 _internal\var\features（若出现 = t2 缺陷回归）
```

**判定口径**：三条端点 200（`/health` 里 `"version":"0.5.0"`）+ 登记 201 + 界面有"总裁办"分区 + `%TEMP%` 解包目录无 `_internal\var`
+ 重启后 `counts.identities` 不减 + `/S` 退出码 0 ⇒ C1 闭合。
**注意（D11）**：上面写法 B 的注释字节数我已按实测改回 **92**（原文档写 94）。

---

## 11. 只读边界

| 动作 | 性质 |
|---|---|
| 新建 `docs/20-release-0.5.0-final-gate.md` | **本轮唯一允许的写入** |
| `.tmp/t13*`（C1 探针脚本、sidecar 日志、`var/features` 指纹、discover/i18n/contract 日志、`t13-web` 副本） | 证据与临时副本 |
| 端口 18862 的打包 sidecar | 用完已 kill；两轮 C1 探针目录 `.tmp/t13`、`.tmp/t13b` 均在工作区内 |
| 发布件 / 校验和 / 签名 / 产品代码 / 他人文档 | **只读**（`release/**`、`docs/18`、`docs/audit/**`、`tools/**`、`tests/**` 均未被我写） |

---

## 附录 A：本轮命令清单

```bash
cd "E:/DEEPSEEK 黑奴/agent-explainer"
export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"

# D8：起打包 sidecar（真实产品）→ 三种写法 + 旧写法 + 单引号字面量 + --trace-ascii
desktop/build/resources/sidecar/agent-explainer-sidecar/agent-explainer-sidecar.exe \
  --sidecar --port 18862 --mode local --data-dir .tmp/t13b/data --features .tmp/t13b/features
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp/t13b/forms.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp/t13b/logline.ps1     # 单引号 + --trace-ascii

# D9：%TEMP% 扫描
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp/t13b/d9.ps1

# D7 / 总包 / 不变量（python 片段见报告正文）
python -c "zipfile 27 条目/全 STORED/27==27/逐字节"
python -c "SHA256SUMS-bundle.txt 26 条复算 + BOM/CRLF/LF-only"
cat release/SHA256SUMS-release.txt
powershell -NoProfile -Command 'Get-AuthenticodeSignature …'

# 闸门
python -m unittest discover -s tests -p "test_*.py" -q
python tools/i18n_lint.py
python tools/gen_api_contract.py --check
cp -r agent_explainer/web .tmp/t13-web && (cd .tmp/t13-web && node selfcheck.mjs)

# 范围
git status --porcelain          # fatal: not a git repository（exit 128）
python -c "mtime 扫描（06:10 起）"
```

## 附录 B：证据落点

| 证据 | 路径 |
|---|---|
| C1 三种写法 + 旧写法 | `.tmp/t13b/forms.ps1` + 本报告 §1.3 原始输出 |
| 单引号字面量 + trace | `.tmp/t13b/logline.ps1`、`%TEMP%\c1-trace.txt` |
| 打包 sidecar 日志 | `.tmp/t13b/sidecar.log`（GBK） |
| D9 扫描 | `.tmp/t13b/d9.ps1` + 本报告 §2 输出 |
| 闸门 | `.tmp/t13-discover.log`、`.tmp/t13-i18n.log`、`.tmp/t13-contract.log`、`.tmp/t13-web/selfcheck-report.json` |
| F4 跑前指纹 | `.tmp/t13-var-pre.json` |
