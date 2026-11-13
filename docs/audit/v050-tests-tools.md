# TraceLens 0.5.0 — R2-2 测试与工具卫生（QA F3/F4/F5）：实录

执行：backend（后端工程师）；任务 t8；日期 2026-11-12。
范围：**只动 `tests/**` 与 `tools/sign_and_hash.ps1`**（+ 本实录）。发布件、产品代码、
t7 重打好的 zip 与两份 SHA256SUMS 一字未动（§5）。测试条数不变（954：未增删用例、未加 skip）。

## 0. 结论

| 项 | 结果 |
| --- | --- |
| F3 `tests/test_crypto_sig.py:367` 概率假红 | **已闭合**：改成确定性（固定 seed + 结构性异或 + 显式守卫），**未放宽**（禁止的三种做法一个都没用）；固定版 50 次连跑 0 红；两条取证各贴原文（旧形状真红 / 空操作被守卫当场抓到） |
| F4 测试把总裁办账本写进仓库 `var/features` | **已闭合**：`tests/test_e2e.py` 的 `Sidecar` 与打包 exe 用例都显式 `--features` 指向本用例临时目录，并在 exe 用例里加"跑前/跑后逐字节指纹相等"的常驻断言；全量套件跑前/跑后 `var/features` 逐文件字节数 + sha256 **完全相同**（§2.4） |
| F5 `sign_and_hash.ps1 -CheckEncoding` 默认清单仍指 0.4.5 | **已闭合**：默认清单＝0.5.0 全套（3 份 `SIGNATURE-STATUS.json` + 4 份文本清单 + 脚本自身 BOM 自报），0.4.5 挪到新增的 `-Include045` 可选参数；两种模式全 `[OK]`、exit 0；脚本保持 **UTF-8 带 BOM**（本轮真剥掉过一次，§3.3 有原始报错与修复） |
| 闸门 | 全量套件 `Ran 954 tests in 197.351s` / `OK (skipped=3)` / exit 0（skipped 仍是 3，没有用它消红） |
| 未动 | 两个已签 exe、`release/**` 全部产物与清单、`release/tracelens-0.5.0-release.zip`、`desktop/**`、`agent_explainer/**`、`web/**`、`scripts/**`、`docs/**`（除本实录） |

---

## 1. F3：`test_public_key_b64_and_verify_b64` 的概率假红

### 1.1 根因（实测，不是估的）

被测输入是"变异签名"。旧写法 `signature[:-1] + b"\x00"` 把 Ed25519 签名的**第 64 字节**
（标量 `s` 的最高字节）置 0，而这个字节**本来就经常是 0**（`s < 2^248` 时必为 0，概率约 1/16）。
这种签名下"变异"是空操作，断言 `assertFalse(verify(篡改))` 等于对着**真签名**求值
→ `AssertionError: True is not false`。

| 谁测的 | 抖动率 |
| --- | --- |
| QA（t6 报告，估） | ≈1/256 |
| backend（t7，单跑 40 次） | 3 红 = **7.5%** |
| backend（本轮 S2 取证） | 第 8 次 / 第 22 次随机生成就拿到末字节 0x00 的签名（≈1/16 一致） |

### 1.2 改法（before → after）

| | 内容 |
| --- | --- |
| 改前 | `seed, pub = cs.generate_keypair()`（每轮新密钥）+ 用 `cs.b64e(signature[:-1] + b"\x00")` 当"变异签名"，且不检查"变异是否真的生效" |
| 改后 | ① `seed = bytes(range(1, 33))` 固定 seed —— Ed25519 是确定性签名，签名逐轮一致，可逐字复现；② 变异改成 `flipped = bytes([signature[0] ^ 0x01]) + signature[1:]`：`x ^ 0x01 != x` 对**任意字节结构性成立**，不存在"改了等于没改"；③ 加显式守卫 `self.assertNotEqual(flipped, signature, "变异签名与原签名相同 = 这条断言在空转（就是 F3 的假红形状）")`，以后谁把变异改成空操作会当场炸，不会静默空转；④ 注释把根因、两条概率实测、为什么固定 seed 全写进代码 |
| 没做什么 | 没有放宽成"允许相等"、没有 `skip`、没有删用例、没动 `assertTrue/assertFalse` 的强度。固定 seed **不损覆盖**：密钥随机性由 `TestKeypair.test_generate_keypair`（两次生成 seed/pub 必不等）钉；逐字节篡改拒绝由 `TestTamper.test_flipped_signature_byte_rejected` 钉 |

### 1.3 验收证据

**① 固定版连跑 50 次 0 失败**（7.5% 抖动下 50 连跑全绿的概率 ≈2%，是硬证据）：

```
$ 循环 50 次：python -m unittest tests.test_crypto_sig.TestPayloadAndBase64.test_public_key_b64_and_verify_b64
W F3 固定版：50 次连跑 失败 0 次
```

**② 人为把被异或字节设成"与 mask 相同"（空操作）→ 用例确实失败**（证明篡改检测没被削平）。
把新写法的 `^ 0x01` 换成 `^ 0x00`（变异＝空操作），显式守卫当场炸：

```
[S1] 空操作 mask 被显式守卫抓到：b'2\x0b\xb9|…\r' == b'2\x0b\xb9|…\r' :
     变异签名与原签名相同 = 这条断言在空转（就是 F3 的假红形状）
```

**③ 人为把末字节设成 0x00 时的失败输出**（逐字照抄修复前那行断言，跑在末字节天然是 0x00 的固定 seed 上）：

```
签名末字节=0x00 的固定 seed = 288a1bed041bc7fc…
FAIL: test_public_key_b64_and_verify_b64_OLD (tests.zz_t8_f3_oldshape.OldShapeTests…)
  File "…tests/zz_t8_f3_oldshape.py", line 20, in test_public_key_b64_and_verify_b64_OLD
    self.assertFalse(cs.verify_b64(pub_text, message, cs.b64e(signature[:-1] + b"\x00")))
AssertionError: True is not false
Ran 1 test in 0.007s
FAILED (failures=1)
```

同一份末字节 0x00 的签名，改用**新写法**（异或首字节）后 `assertFalse(verify(flipped))` 通过 ——
变异被真实拒掉，说明"拒篡改"通路完好，F3 修的只是被测输入的构造方式。
取证用的两个临时文件（`tests/zz_t8_f3_oldshape.py`、`tests/zz_t8_f3_sabotage.py`）**跑完即删**，
不进全量套件（测试条数因此仍是 954）。

---

## 2. F4：测试把总裁办账本写进仓库 `var/features`

### 2.1 根因

`feature_store.FeaturePaths` 的默认账本根是 **`<应用根>/var/features`**，**不跟随 `--data-dir`**
（t2 首次实测、t6 独立复现钉死；`agent_explainer/server.py:1704` 的注释就写着"测试与部署一律显式注入 features_root"）。
`tests/test_e2e.py` 的 `Sidecar` 只传 `--data-dir`，于是每起一个真进程 sidecar，签名头链就 append 进
仓库的 `var/features/secretariat/heads.jsonl` —— QA 实测 2,721,000 → 4,171,608 字节。

### 2.2 改法（只在 `tests/`）

| 位置 | 改动 |
| --- | --- |
| `tests/test_e2e.py` `Sidecar.__init__` | 新增 `self.features_dir = self.data_dir.parent / "features"`（由 `tests/scratch.py` 派生：系统临时目录优先，本机落点是 `.tmp\agent-explainer-tests\…`），命令行加 `--features <该目录>`；注释写明根因 + QA 实测字节数 |
| `tests/test_e2e.py` `test_sidecar_exe_runs` | 打包 exe 的命令行同样加 `--features <临时目录>\features-exe`；并加**常驻断言**：跑前/跑后对仓库 `var/features` 取指纹（相对路径 + 字节数 + 每文件 sha256），不相等就红，理由「打包 exe 把总裁办账本写进了仓库 var/features：`--features` 没生效」 |
| 新增模块级 helper `_ledger_fingerprint(root)` | 指纹函数（不是用例，测试条数不变） |
| 未动 | `agent_explainer/**`（默认落点是产品行为，部署侧由 `desktop/sidecar.js`、compose、启动脚本钉 `--features`，t2/t5 已做）；`scripts/verify_e2e.py`（不在本卡）；`tests/scratch.py` 的兜底落点（它写 `var/tmp-*`，不碰 `var/features`，且优先系统临时目录） |

### 2.3 已有污染数据（按要求只上报，**未删**）

本轮基线（跑前）`var/features` = **5 个文件 / 6,599,005 字节**：

| 字节 | 修改时刻 | 文件 |
| --- | --- | --- |
| 6,598,100 | 11-12 05:05:46 | `var/features/secretariat/heads.jsonl` |
| 567 | 11-12 01:25:04 | `var/features/secretariat/identities.jsonl` |
| 338 | 11-12 01:25:04 | `var/features/secretariat/keyring.json` |
| 0 | 11-12 01:25:04 | `var/features/secretariat/heads.jsonl.lock` |
| 0 | 11-12 01:25:04 | `var/features/secretariat/identities.jsonl.lock` |

判读：`heads.jsonl` 的 6.6 MB 与 05:05 的 mtime 是**测试污染**（t7 那两轮全量套件写的）；
01:25 那三份（identities + keyring）时点与 t2/t3 吻合，**可能是架构师留的取证样本** → 按要求原样保留。
本轮之后套件不再往里写（§2.4）；要清理由队长决定（一并会丢掉那份取证样本）。

### 2.4 全量套件跑前 / 跑后 stat 原文（逐字节相同）

```
== var/features 跑前 stat ==
  总字节 = 6,599,005  文件数 = 5
     6,598,100  1E174CEF9DACDE82  heads.jsonl
             0  E3B0C44298FC1C14  heads.jsonl.lock
           567  10481EF7DF19D1B4  identities.jsonl
             0  E3B0C44298FC1C14  identities.jsonl.lock
           338  C71A94128B604B82  keyring.json
W suite exit=0
== var/features 跑后 stat ==
  总字节 = 6,599,005  文件数 = 5
     6,598,100  1E174CEF9DACDE82  heads.jsonl
             0  E3B0C44298FC1C14  heads.jsonl.lock
           567  10481EF7DF19D1B4  identities.jsonl
             0  E3B0C44298FC1C14  identities.jsonl.lock
           338  C71A94128B604B82  keyring.json
```

16 位前缀是文件 sha256；`E3B0C44298FC1C14…` 是 0 字节文件的 sha256 前缀（`.lock` 为空）。
跑前/跑后**每个文件的字节数与 sha256 都相同**，总字节 6,599,005 → 6,599,005。

账本改落在临时目录的取证（跑后残留）：

```
.tmp/agent-explainer-tests 下 features 文件数 = 15  字节 = 20,499
      5928  e2e-demo-none\features\secretariat\heads.jsonl
         0  e2e-demo-none\features\secretariat\heads.jsonl.lock
       567  e2e-demo-none\features\secretariat\identities.jsonl
         0  e2e-demo-none\features\secretariat\identities.jsonl.lock
       338  e2e-demo-none\features\secretariat\keyring.json
      5928  e2e-persist\features\secretariat\heads.jsonl
```

---

## 3. F5：`tools/sign_and_hash.ps1 -CheckEncoding` 覆盖 0.5.0

### 3.1 清单 before → after

| | 内容 |
| --- | --- |
| 改前 | 只审计 3 个 JSON：`desktop/build/dist/SIGNATURE-STATUS.json`、`release/SIGNATURE-STATUS.json`、**`release/tracelens-0.4.5-release/SIGNATURE-STATUS.json`**（0.4.5 包），且只有 JSON 检查器 |
| 改后 | **JSON 组（默认）**：`desktop/build/dist/`、`release/`、`release/tracelens-0.5.0-release/` 三份 `SIGNATURE-STATUS.json`；**新增文本组**（新函数 `Test-TextFileEncoding`：UTF-8 无 BOM + 无 U+FFFD + 逐行 `<64位小写十六进制><两空格><路径>` 格式 + 换行口径打印）：`release/SHA256SUMS-release.txt`、`release/0.5.0/SHA256SUMS.txt`、`release/tracelens-0.5.0-release/SHA256SUMS.txt`、`release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt`；**末尾自报脚本自身 BOM**（缺 BOM 计入失败）；**0.4.5 挪到 `-Include045`**（新增参数）：加开关才扫 0.4.5 的 `SIGNATURE-STATUS.json` + 两份 SHA256SUMS |
| 口径未动 | `status=UnknownError` 一个字没改成 `Valid`；签名不被 Windows 认可时 `exit 1` 的既定口径没动；产物侧「UTF-8 无 BOM」写入口径没动（本轮只加只读审计 + 一个参数） |

### 3.2 `-CheckEncoding` 原文输出

默认（0.5.0 全套，exit 0）：

```
=== 发布件编码审计（只读，不改任何文件）===
--- JSON 产物（要求 UTF-8 无 BOM、中文完好、可解析）
[OK] E:\DEEPSEEK 黑奴\agent-explainer\desktop\build\dist\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\tracelens-0.5.0-release\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
--- 文本清单（要求 UTF-8 无 BOM、无 U+FFFD、逐行 sha256 格式）
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\SHA256SUMS-release.txt  190 字节  BOM=False 中文完好=True 条目=2 换行=CRLF
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\0.5.0\SHA256SUMS.txt  92 字节  BOM=False 中文完好=True 条目=1 换行=LF
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\tracelens-0.5.0-release\SHA256SUMS.txt  205 字节  BOM=False 中文完好=True 条目=2 换行=CRLF
[OK] E:\DEEPSEEK 黑奴\agent-explainer\release\tracelens-0.5.0-release\SHA256SUMS-bundle.txt  2201 字节  BOM=False 中文完好=True 条目=24 换行=CRLF
[OK] tools/sign_and_hash.ps1  35048 字节  BOM=True（脚本自身要求带 BOM）
编码审计通过：JSON 与文本清单全部 UTF-8 无 BOM、中文完好；脚本自身 BOM 在位
exit=0
```

`-Include045`（0.4.5 走可选参数，exit 0；多出的 4 行）：

```
[OK] …\release\tracelens-0.4.5-release\SIGNATURE-STATUS.json  2035 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\0.4.5\SHA256SUMS.txt  92 字节  BOM=False 中文完好=True 条目=1 换行=LF
[OK] …\release\tracelens-0.4.5-release\SHA256SUMS.txt  297 字节  BOM=False 中文完好=True 条目=3 换行=CRLF
[OK] tools/sign_and_hash.ps1  35048 字节  BOM=True（脚本自身要求带 BOM）
```

顺带把 t7 定的换行口径打印出来并对上：`SHA256SUMS-release.txt` CRLF 2 条、
`release/0.5.0/SHA256SUMS.txt` LF 1 条（docker-build.sh 产出）、bundle 内 `SHA256SUMS.txt` CRLF 2 条、
`SHA256SUMS-bundle.txt` CRLF 24 条（自排除）。

### 3.3 本轮真踩到的 BOM 坑（实证，不是引用历史）

`edit` 工具改完 `sign_and_hash.ps1` 后 **BOM 被剥掉**（实测 `has BOM before = False  35,045 字节`），
Windows PowerShell 5.1 立刻按 GBK 解码脚本里的中文字面量，抛出一串幻影语法错（与 t1 在案的"22 处"同一形状）：

```
At …tools/sign_and_hash.ps1:41 char:28   Missing expression after ','.
At …tools/sign_and_hash.ps1:53 char:24   Missing closing '}' in statement block or type definition.
At …tools/sign_and_hash.ps1:84 char:16   Unexpected token '鏈?.ps1' …
At …tools/sign_and_hash.ps1:256 char:21  Unexpected token '缂栫爜瀹¤澶辫触锛?bad …'
Not all parse errors were reported.  Correct the reported errors and try again.
```

处置与自证：按 UTF-8 重新加回 BOM（`has BOM after = True  35,048 字节`，正文 UTF-8 字节完好、`U+FFFD=0`），
语法立刻正常、两种模式都 exit 0；并让 `-CheckEncoding` **自己**打印 `[OK] tools/sign_and_hash.ps1 … BOM=True`，
以后谁剥了 BOM 会在审计输出里当场看到（缺 BOM 计入 `$bad` → exit 1）。

---

## 4. 闸门：全量套件

```
$ python -m unittest discover -s tests -p "test_*.py" -v
Ran 954 tests in 197.351s
OK (skipped=3)
suite exit=0
```

- 条数 **954**（与 t7 基线一致：本轮没加用例、没删用例；取证用的临时模块跑完即删）。
- **`skipped=3` 与基线完全相同**，本轮没有新增 skip：静态扫描装饰器级 skip = 0 条；
  `-v` 日志里点名的 2 条是 reportlab 相关（本机装了 reportlab，故 PDF 回退用例与
  `prefer='pdf'` 失败用例跳过）：
  - `test_audit_log.PdfExportTests.test_no_reportlab_falls_back_to_printable_html` → `本机有 reportlab（None），回退路径由 stub 用例覆盖`
  - `test_audit_log.PdfExportTests.test_prefer_pdf_raises_when_reportlab_is_unavailable` → `本机有 reportlab：prefer='pdf' 应当成功出 PDF`
  - 第 3 条 skip 的行被 sidecar 子进程输出交织打断，合并日志里只留下 2 条完整 `... skipped` 行
    （`... ok` 908 条、`FAIL/ERROR` 0 条，954 条里约 44 条被交织吃掉）—— **如实披露，不猜**；
    条数与 t7 基线（同为 `skipped=3`）逐位吻合，说明不是本轮新引入。
- 前序披露沿用在案：t7 三跑里前两轮各红 1 条（`test_settings_api…test_method_contract` 的
  `ConnectionAbortedError [WinError 10053]` 环境抖动 / F3 本体）。本轮 F3 修好后**一跑即绿**，没有"重跑到绿"。
  10053 那条属环境抖动，复现命令：`python -m unittest test_settings_api.SettingsApiLocalTests.test_method_contract`。

---

## 5. 范围核对

卡片验收第 ⑤ 条要求 `git status --porcelain`。本工作副本**不是 git 仓库**：

```
$ git status --porcelain
fatal: not a git repository (or any of the parent directories): .git
git exit=128
```

等价核对（仓库内 mtime 晚于本轮开工 05:16 的全部文件，排除 `.tmp\`、`.agent-teams\`、
`node_modules\`、`__pycache__\`）：

```
  11-12 05:22:15  10,894  docs\audit\v050-tests-tools.md   （新建实录）
  11-12 05:19:04  23,413  tests\test_crypto_sig.py
  11-12 05:19:33  46,785  tests\test_e2e.py
  11-12 05:18:43  35,048  tools\sign_and_hash.ps1
```

> 注（自引用口径）：表里的字节数是**扫描时刻的快照**，本文每自更新一次，第一行那一格就随之变化
> （扫描时 10,894，写完本节即为终值）；其余三行的 mtime 都**早于全量套件开跑（05:19:5x）**，
> 所以 §4 那一轮已包含全部 `tests/` 与 `tools/` 改动。

只出现 `tests/`、`tools/` 与本实录 —— 与允许写入域一致，`release/**`、`desktop/**`、`agent_explainer/**`、
`web/**`、`scripts/**` 一个字节都没动；权威发布件逐字节未动由
`python -X utf8 -u .tmp/t7-verify.py`（t7 写的只读复算器）**exit 0** 复证：四份清单
2/2 · 1/1 · 2/2 · 24/24 全 OK、zip 25 条目 == 目录 25 且全 `ZIP_STORED`、两个已签 exe
的 sha256 与 `release/SHA256SUMS.txt` 逐字一致、0.4.5 行逐字未变。

---

## 6. 只登记不修（本轮按卡片要求原样记下）

1. **`tools/sign_and_hash.ps1` 的 `.zip` 句柄锁缺口仍在**（**现位于 496 / 502 行**：
   496 = `Set-AuthenticodeSignature @signArgs | Out-Null`，502 = `$check = Get-AuthenticodeSignature -LiteralPath $a.FullName`，
   中间没有释放/重试）：`Set-AuthenticodeSignature` 处理 `.zip` 后紧接 `Get-AuthenticodeSignature`，在
   `$ErrorActionPreference='Stop'` 下偶发 `IOException`（句柄未释放）。未修。
   行号口径（QA D5）：本轮之前多处写的是 **448**，那是 t4 时点的行号；t8 给脚本加了
   `Test-TextFileEncoding` 与 `-Include045` 之后漂到 496/502。核对命令（按锚点找，不靠行号）：
   `Select-String -Path tools/sign_and_hash.ps1 -Pattern 'Set-AuthenticodeSignature|Get-AuthenticodeSignature -LiteralPath \$a'`。
2. **`npm run sign` 的数组传参已在 t4 闭合**：现在不再用 `-Files @(...)` 传参。本轮只登记，未重验前端脚本。
3. **`scripts/verify_e2e.py` 没有总裁办断言**（QA 编号里的另一条低优项）：不在本卡范围，
   `scripts/**` 一字未动。
4. **`docs/NOTICE-third-party.json` 仍是手写副本**，缺由 `desktop` 依赖自动生成 NOTICE 的脚本（t7 已登记，状态不变）。
5. **环境抖动在案**：`ConnectionAbortedError [WinError 10053]`（本机回环）偶发红，属环境类；
   复现命令见 §4。F3 修好后本轮全量一跑即绿。
6. **受限会话副作用（未清理，交队长定）**：仓库根与 `.tmp\` 下留有测试在私有 DACL 目录建不出的
   `tmp*` 空目录（`.tmp\tmp*`、根目录 `tmp*`，扫描时报 `UnauthorizedAccessException`）；
   `.tmp\agent-explainer-tests\e2e-*\features\` 留有本轮账本（15 文件 / 20,499 B，正是 F4 的落点证据）；
   `.tmp\t7-*`、`.tmp\t8-*` 是本轮脚本与日志；`var/features` 的污染数据按 §2.3 原样保留。
