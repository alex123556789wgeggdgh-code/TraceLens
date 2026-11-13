# v0.5.0 发布件收口实录（T3 自签名 / 校验和 / 发布总包）

* 任务：t4 `T3 自签名、校验和、发布总包与签名状态转绿`
* 执行成员：backend（后端工程师：版本口径 / sidecar / 签名 / 镜像）
* 实测时间：2026-11-12 03:24–03:35（Asia/Shanghai）
* 机器：`<build-host>\windows`，Windows PowerShell 5.1（**本机无 `pwsh`**），Python 3.14.7
* 写作用域：`release/**`、`desktop/build/dist/**`、`tools/sign_and_hash.ps1`（统计/编码缺口）、`docs/audit/`
* 未做（禁止项，遵守）：electron-builder 重建、docker、`web/**`

结论一句话：**两条目标 ArtifactTests 从红转绿，且绿是因为口径如实**（`signed_count=0` + `signatures_applied=2` + `unsigned=true` + `status=UnknownError` 原文保留），签名脚本按口径 `exit 1`（自签名不被 Windows 认可），发布总包已产出并逐条重算通过。

---

## 1. 证书信息（实测读取，非声明）

```
Subject       : CN=TraceLens Self-Signed (Local Only)
Thumbprint    : <redacted-thumbprint>
Serial        : <redacted-serial>
NotBefore     : 2026-11-11T08:28:10
NotAfter      : 2028-11-11T08:38:11  （2028 到期，与任务口径一致）
Store         : Cert:\CurrentUser\My
EKU           : 1.3.6.1.5.5.7.3.2 (Code Signing)
HasPrivateKey : True
```

* 证书**已存在**，脚本走的是「复用现有自签名证书」分支，本轮**没有**新建证书，也**没有**造假签名。
* 脚本的证书选取是 `Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert` 里 Subject 精确匹配 + 未过期的 `existing[0]`。本机另有一张同名旧证书 `CCACB81DE5B7458EDEA31B94B1F08DE9228732C7`（2027 到期，非 Code Signing EKU，因此不进 `-CodeSigningCert` 列表），实测列表里只有一张匹配 → 选中的正是任务指定的 `DF1CB56A…`（脚本日志原文：`复用现有自签名证书：<redacted-thumbprint>（有效期至 2028-11-11）`）。
* `TraceLens-SelfSigned.cer`（814 B，sha256 前缀 `5f8a6044ab2fef48`）与 0.4.5 发布件里的三张 `.cer` **逐字节相同** → 同一张钥匙，导出没有改变历史发布件的信任事实。

## 2. 签名执行实录（含两次真实失败，不粉饰）

### 2.1 第一次：命令行传参坑（我的调用方式，不是脚本缺陷）

`powershell -File tools\sign_and_hash.ps1 -Files @('a','b','c')` 在 `-File` 模式下**不会把 `@(...)` 求值成数组**：`-Files` 只吃掉第一个垃圾 token，剩下的 token 按**位置**绑到了第一个位置参数 `$OutDir` 上，于是 `$OutDir` 变成 `…\dist\TraceLens-0.5.0-win-x64-portable.exe`（一个文件），脚本接着把 `SHA256SUMS.txt` 往这个「目录」里写：

```
Exception calling "WriteAllText" with "3" argument(s): "Could not find a part of the path
'E:\DEEPSEEK 黑奴\agent-explainer\desktop\build\dist\TraceLens-0.5.0-win-x64-portable.exe\SHA256SUMS.txt'."
```

同一次运行里 `New-SelfSignedCertificate` 报 `Access is denied. 0x80070005`、`.NET CertificateRequest` 报 `invalid X500 name attribute`（Subject 里的括号在 .NET X500Name 解析里要转义），脚本因此判定「本机造不出证书」并走 `NotSigned` 阻塞分支 —— **这是假结论**：证书在库里好好的。真因是子进程参数绑定串了 + 那次调用没带上 `-Files` 的真实数组。

处置：固定用 runner 脚本 `.tmp/t4-sign-runner.ps1`（UTF-8 带 BOM）以 `& tools\sign_and_hash.ps1 -Files $files` 方式调用，数组正确绑定。**未为此改脚本**（`-File` 的语义是 PowerShell 的，不是脚本的）。

### 2.2 第二次：`.zip` 不能签，且签完留句柄 → 整个签名流程崩

把 `portable-src.zip` 放进签名清单后，`Set-AuthenticodeSignature` 处理完 `.zip` 留下文件句柄，脚本紧接着的 `Get-AuthenticodeSignature` 直接抛：

```
Get-AuthenticodeSignature : The process cannot access the file
'…\desktop\build\dist\TraceLens-0.5.0-win-x64-portable-src.zip' because it is being used by another process.
At E:\DEEPSEEK 黑奴\agent-explainer\tools\sign_and_hash.ps1:448 char:14
```

脚本头部是 `$ErrorActionPreference='Stop'` → 当场中止，**SHA256SUMS.txt 与 SIGNATURE-STATUS.json 都没写出来**（实测 `desktop\build\dist\SHA256SUMS.txt` 仍是 0.4.5 的 297 B / 11-11 mtime，说明没有半写坏的产物）。

> **行号时点更正（QA D5，本轮 2026-11-12 R3）**：上面那段原文取自 t4 时点，当时这条崩溃报在 **448 行**；
> t8 给脚本增码（新增 `Test-TextFileEncoding` 与 `-Include045`）之后，同一缺口现在漂到
> **496 / 502 行**（496 = `Set-AuthenticodeSignature @signArgs | Out-Null`，502 = `$check = Get-AuthenticodeSignature -LiteralPath $a.FullName`）。
> 原文逐字保留不改史；今后引用请锚函数名而不是行号：
> `Select-String -Path tools/sign_and_hash.ps1 -Pattern 'Set-AuthenticodeSignature|Get-AuthenticodeSignature -LiteralPath \$a'`。
> 缺口本身按本轮口径**仍未修**，详见 `v050-tests-tools.md` §6 第 1 条。

处置（不改脚本，属声明范围外的产物处理缺口）：签名清单只放两个 PE 产物（Setup / portable）；`portable-src.zip` 的完整性由发布总包的校验和逐条覆盖（§6），并在 runner 里写明原因。建议后续给脚本加 `-NoSignExtensions`（默认跳过 `.zip/.7z` 这类 Authenticode 签不了、签了还会留句柄的后缀），本轮**未做**，如实登记。

### 2.3 修掉的两个真缺口（属「统计缺口」授权范围）

1. **`signatures_applied` 恒为 0（统计口径与自签名事实矛盾）**。旧判据：
   `SignatureType -ne 'None' -and SignatureHash -ne ''`。实测签完的 151 MB Setup：

   ```
   Status            = [UnknownError]
   StatusMessage     = [A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider]
   SignatureType     = [Authenticode]
   SignatureHash     = []            ← 链在不受信任的根处终止时 Windows 不给哈希
   SignerCertificate = [CN=TraceLens Self-Signed (Local Only)] / [<redacted-thumbprint>]
   ```

   文件确实长了 1,437 B 签名块、签名人指纹在，却因 `SignatureHash` 空被判「没签名」。判据改为 `SignatureType -ne 'None' -and SignerCertificate.Thumbprint -ne ''`（都是 API 层事实，与 Status 好不好看无关），并在代码注释里钉住「UnknownError 时 SignatureHash 是空串」这条实测依据。
2. **结论行 `-f` 优先级**：`"a" + "b" -f args` 里 `-f` 只作用于第二个字符串，控制台打印出字面 `{0}`/`{1}`（人读会误判统计没填）。加括号后正常输出。控制台展示缺陷，产物 JSON 的数值一直是按 `Get-TrustFacts` 直接算的，未受影响。

改完复核（脚本自身编码是产物中文可读的前提）：

```
脚本字节数=31680  BOM=True
语法错误数=0        （PSParser::Tokenize）
```

## 3. 两个 exe：签名前 / 签名后的 sha256 与状态原文

| 产物 | 签名前字节 | 签名前 sha256 | 签名后字节 | 签名后 sha256 |
|---|---|---|---|---|
| `TraceLens-0.5.0-win-x64-Setup.exe` | 151,836,755 | `a234cd8e0902de8b69a963664e8adabc061771297bfeeb7f02c95f1f126f228f` | 151,838,192（+1,437） | `37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d` |
| `TraceLens-0.5.0-win-x64-portable.exe` | 137,882,390 | `d14088d52429e7453b28e6e01da01f56b8fc06a3feed43c30b6ec29d10daae15` | 137,883,824（+1,434） | `8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52` |
| `TraceLens-0.5.0-win-x64-portable-src.zip` | 4,957,816 | `098c3bcd472842c17de63323b0868c31226db125b876b380a31f2e7bc666aff3` | 未签（§2.2） | 同上（字节未变） |

* 签名前哈希与 `docs/audit/v050-desktop.md` §3.2 表**逐条核对**：portable 与 portable-src 两条完全相同；Setup 那条是 §3.2 的**抄录笔误**（少一个 `e`），见 §7 的修正记录。
* 第二次运行（改判据后重签）产物字节与第一次**完全相同**（同证书、无时间戳 → 签名块确定），所以「最终交付哈希」= 上表签名后一列，可复算。
* 签名状态原文（两份产物一致，取自 `Get-AuthenticodeSignature`，未改写）：

```
Status        = UnknownError
StatusMessage = A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider
SignatureType = Authenticode
Signer        = CN=TraceLens Self-Signed (Local Only)（指纹 <redacted-thumbprint>）
TimeStamping  = （无：本轮未配置 -TimestampServer，本机无可用的免费时间戳服务口径）
```

**没有**把 `UnknownError` 写成 `Valid`；`trust_note` 明写「自签名证书：只能证明"文件没被改过、来自本机这张钥匙"，不是受信任发布者签名……消除报警需要 OV/EV 代码签名证书，或把证书导入 `LocalMachine\Root` + `LocalMachine\TrustedPublisher`（需管理员）」。

## 4. `SIGNATURE-STATUS.json` 如实性核对（4,918 B，UTF-8 无 BOM）

```
bytes 4918  BOM= False  U+FFFD= False
generated_at        = 2026-11-12T03:29:52
encoding            = utf-8-no-bom
product_version     = 0.5.0
trusted_statuses    = ['Valid', 'NotTrusted']
signed_count        = 0            ← 只算 Valid/NotTrusted：自签名典型 0
signatures_applied  = 2            ← 落了签名的产物数（T0 新增字段，本轮修好判据后才如实）
unsigned            = True
trust_state         = self_signed_root_untrusted
signed_by           = CN=TraceLens Self-Signed (Local Only)（指纹 <redacted-thumbprint>）
status_counts       = {'UnknownError': 2}
artifact_count      = 2
sign_failures       = []
```

对照 0.4.5 那份（2,035 B，11-11 生成）：`signed_count: 2`、`unsigned: False`、**无** `signatures_applied/trust_state/signed_by/status_counts` —— 正是 `test_signature_status_is_honest` 长期红着的真因（两个状态都是 `UnknownError` 却记 2 个已签名）。本轮从口径上关掉，**没有改 `tests/`**。

编码审计（脚本自带只读模式，原文）：

```
=== SIGNATURE-STATUS.json 编码审计（只读，不改任何文件）===
[OK] …\desktop\build\dist\SIGNATURE-STATUS.json  4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\SIGNATURE-STATUS.json             4918 字节  BOM=False 中文完好=True JSON可解析=True
[OK] …\release\tracelens-0.4.5-release\SIGNATURE-STATUS.json  2035 字节  BOM=False 中文完好=True JSON可解析=True
编码审计通过：全部 UTF-8 无 BOM、中文完好、JSON 可解析      （exit 0）
```

> 注：`-CheckEncoding` 的审计清单是写死的三条路径（T0 定的），本轮新增的 `release/tracelens-0.5.0-release/SIGNATURE-STATUS.json` 不在其列 —— 我单独量过：4,918 B、无 BOM、无 U+FFFD、JSON 可解析（与上表同源同字节）。

`-Verify` 原文（**exit 1 是口径，不是构建失败**：哈希全过，签名状态不达「Windows 认可」）：

```
=== 校验 SHA256SUMS + 签名状态（E:\DEEPSEEK 黑奴\agent-explainer\desktop\build\dist）===
[sha256 ok] TraceLens-0.5.0-win-x64-Setup.exe  签名=UnknownError
        签名状态不达意：UnknownError — A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider
[sha256 ok] TraceLens-0.5.0-win-x64-portable.exe  签名=UnknownError
        签名状态不达意：UnknownError — （同上）
校验失败：2 项（不得计入交付）
```

## 5. 两条 ArtifactTests（本轮收口目标）

命令与原始输出：

```
python -m unittest tests.test_e2e.ArtifactTests.test_signature_status_is_honest tests.test_e2e.ArtifactTests.test_sha256sums_verify -q
→ Ran 2 tests in 0.198s
  OK
```

**2/2 OK，且是「口径如实」带来的绿**：断言逐条对应 §4 的事实 —— `signed_count(0) == 状态属于 Valid/NotTrusted 的产物数(0)`、`artifact_count(2) == len(artifacts)(2)`、`unsigned(True) == not signed([])`、每条 `status` 都在允许集合内且 `path` 真实存在、文档里有 `certificate`/`trust_note` 且含「自签名」。

### 全量 discover（回归闸门，目标 0 红）

```
python -m unittest discover -s tests -q
→ Ran 954 tests in 164.349s
  OK (skipped=3)                              ← exit 0
```

对照 t1/t2 基线 `Ran 954 tests … FAILED (failures=1, skipped=3)`：**唯一那条红（`test_signature_status_is_honest`）本轮转绿，failures 由 1 变 0，零新增红**（skip 仍是 3 条）。日志 `agent-explainer/.tmp/t4-full-suite.log`（UTF-16，`*>` 重定向）。

> 说明：架构师会话报的「16 红 / 209 错」是 `tempfile` 私有 DACL × 受限令牌的 `WinError 5`（复现 `.tmp/t3-mkdtemp-repro.py`），不是产品回归；本会话按 t1/t2 的口径先把 `TMP/TEMP` 指到工作区 `.tmp` 后全绿，与那条环境结论一致。


## 6. 发布总包（`release/tracelens-0.5.0-release/`）

对齐 0.4.5 总包结构（`CHANGELOG/README/LICENSE/SHA256SUMS/SIGNATURE-STATUS` + 两 exe + `source/` + `docs/` + `docker/`）：

| 文件 | 字节 | 来源 |
|---|---|---|
| `TraceLens-0.5.0-win-x64-Setup.exe` | 151,838,192 | `desktop/build/dist` 已签产物（**硬链接**，`st_nlink=2`，sha256 实测与 dist 相同） |
| `TraceLens-0.5.0-win-x64-portable.exe` | 137,883,824 | 同上 |
| `source/TraceLens-0.5.0-win-x64-portable-src.zip` | 4,957,816 | T2 重打过的那份（`start.cmd` 已带 `--features`） |
| `CHANGELOG.md` | 12,442 | 仓库当前 0.5.0 版 |
| `README.md` | 27,931 | 仓库当前 0.5.0 版 |
| `LICENSE` | 6,362 | 仓库 |
| `SHA256SUMS.txt` | 205 | 与 `desktop/build/dist/SHA256SUMS.txt` **同字节**（两处一致） |
| `SIGNATURE-STATUS.json` | 4,918 | 与 dist、release 根**同字节**（三处一致） |
| `TraceLens-SelfSigned.cer` | 814 | 同上 |
| `docs/05.3-release-checklist.md` | 32,893 | 6 份文档，文件名对齐 0.4.5 总包 |
| `docs/05.4-docker.md` | 33,334 | |
| `docs/06-faq.md` | 14,290 | |
| `docs/06-feedback-form.md` | 7,588 | |
| `docs/06-user-guide.md` | 11,008 | |
| `docs/NOTICE-third-party.md` | 5,571 | |
| `docs/audit/v050-version.md` | 20,230 | T0 实录 |
| `docs/audit/v050-sidecar.md` | 17,886 | T1 实录 |
| `docs/audit/v050-desktop.md` | 30,125 | T2 实录（含 §7 哈希笔误修正） |
| `docs/audit/v050-release.md` | 本文 | T3 实录 |
| `docker/DOCKER-PENDING.md` | 425 | **如实占位**：0.5.0 镜像由 T5 产出，不拿 0.4.5 的 tar 冒充 |
| `SHA256SUMS-bundle.txt` | 见下 | 总包全量清单（新增，逐条可复算） |

同步与新旧边界：

* `release/SHA256SUMS.txt`（205 B）、`release/SIGNATURE-STATUS.json`（4,918 B）、`release/TraceLens-SelfSigned.cer`（814 B）→ 更新为 0.5.0 事实，与 dist 同字节（「两处都要一致」）。
* `release/` 根目录补上 0.5.0 两个已签产物（**硬链接**，与 dist 同 inode，零额外磁盘），这样 `release/SHA256SUMS.txt` 的两行能就地复算（0.4.5 的口径就是清单与产物同目录：那份 297 B 三行含 `.cer`，本轮 205 B 两行——脚本 T0 起过滤 `.cer/.txt/.json/.yml/.log/.7z/.nupkg/.blockmap`，所以不再有 `.cer` 行）。0.4.5 的两个 exe 仍在原位。
* **0.4.5 的记录一件没动**：`release/0.4.5/**`、`release/tracelens-0.4.5-release/**`（含其 `SHA256SUMS.txt` 297 B、`SIGNATURE-STATUS.json` 2,035 B）、`release/TraceLens-0.4.5-win-x64-Setup.exe`（151,535,616 B）、`…portable.exe`（137,607,264 B）、`tracelens-0.4.5-release.zip`（356,074,424 B）全部保持 11-11 的字节与 mtime。
* `release/SHA256SUMS-release.txt` 从 1 行（95 B）扩到 2 行（190 B）：

```
0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip
f00fbbf423d5fcebfa1bfaee08d85ffb0ec6b57690cf370677a480673df20927  tracelens-0.5.0-release.zip
```

0.4.5 那行**实测与旧文件记录逐字相同** → 反证旧包未被改写。

### 6.1 逐条重算复核（只读，`python .tmp/t4-verify.py` 原文）

```
[复算] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt  20 条  全部通过
[复算] release/SHA256SUMS.txt                                  2 条  全部通过
[复算] desktop/build/dist/SHA256SUMS.txt                       2 条  全部通过
[复算] release/SHA256SUMS-release.txt                          2 条  全部通过
[zip] 条目 21  清单 20（清单自身也在 zip 内，故 +1）  zip 字节 294,930,157
[zip 复算] 20 个交付条目逐条重算通过（STORED，与 0.4.5 总包同口径）
[0.4.5 旧包未动] sha256 逐字相同
=== 全部复核通过 ===      （exit 0）
```

产物字节实测：`release/tracelens-0.5.0-release.zip` = **294,930,157 B**（21 条，`compress_type=0` 存储；对照 0.4.5 的 356,074,424 B / 18 条，差额就是 0.4.5 里那份 63 MB 的镜像 tar —— 本轮按 §8 不放冒充物）、`SHA256SUMS-bundle.txt` = 1,825 B（20 条，UTF-8 无 BOM、CRLF）。zip 用 `ZIP_STORED` 与 0.4.5 完全同口径（0.4.5 那份压缩前＝压缩后＝356,072,210 B，说明当年也是存储，压缩对 NSIS 已压过的体积无收益）。

**自引用边界（如实写明，不装作能自证）**：本文就在总包里，所以 zip 自身的 sha256 **不能**写进本文——写进去就改变本文、进而改变 zip。zip 的哈希唯一权威记录是 `release/SHA256SUMS-release.txt`（每次重建后由 `t4-bundle.py` 重算，`t4-verify.py` 逐条复算）。该文件当前两行的第一行（0.4.5 旧包）逐字稳定：

```
0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip
<0.5.0 行由脚本在本次重建后写入，核对以文件为准>  tracelens-0.5.0-release.zip
```



## 7. 修正 T2 实录里的哈希抄录笔误（`docs/audit/v050-desktop.md` §3.2）

本轮签名前逐条复算哈希，与 §3.2 表比对：

| 产物 | §3.2 原记录（11-12 03:25） | 实测（签名前） | 判定 |
|---|---|---|---|
| Setup | `…97bfe`**`f`**`b7f02c95f1f126f228f` | `…97bfe`**`eb`**`7f02c95f1f126f228f` | 记录少一个 `e`（笔误），**字节数 151,836,755 与实测完全一致** |
| portable | `d14088d5…daae15` | 相同 | 一致 |
| portable-src.zip | `098c3bcd…666aff3` | 相同 | 一致 |

即：**文件没变，是记录串少写一个字符**（字节数一致可排除产物被替换）。已就地改表（`docs/audit/` 属本轮写作用域），并在本文件留这条修正记录。

## 8. 交给 T5 / QA 的事实

* `docker/DOCKER-PENDING.md` 已在总包里说明要补的三件：`tracelens-0.5.0-image.tar`、`IMAGE-INFO.json`、`docker/SHA256SUMS.txt`；补完后**必须重打** `tracelens-0.5.0-release.zip` 并同步 `release/SHA256SUMS-release.txt` 与 `SHA256SUMS-bundle.txt`（zip 是内容快照，不会自动跟着目录变）。本轮 zip 的清单＝§6 表（不含镜像 tar）。
* 已签产物哈希（T5 若重建镜像不要动这两个文件的字节）：Setup `37cf6963…0395d`、portable `8bdc55a0…fdd52`。
* QA 三形态验收时，安装包的**签名状态在 Windows 眼里就是 UnknownError**（链终止于不受信任根），这是自签名基线的预期，不是打包缺陷；要消警需要 OV/EV 证书或管理员导入 `LocalMachine\TrustedPublisher`。
* 脚本按口径 `exit 1`（无 Windows 认可的签名）；流水线不要把这条读成构建失败（`-Verify` 同口径）。

## 9. 清理与日志清单

* 本轮没有启动任何常驻服务（签名/校验全是前台短命令）；`Get-Process` 无残留签名进程。
* 磁盘：两个 exe 在总包与 `release/` 根都用**硬链接**（同卷 NTFS，`st_nlink` 递增，sha256 实测与 dist 一致），新增物理字节≈zip 一份（zip 用 `ZIP_STORED`，与 0.4.5 总包 zip 的 `compress_type=0` 一致：356,072,210 B 未压 == 压缩后）。
* 日志与探针（`agent-explainer/.tmp/`，PS 5.1 的 `*>` 是 UTF-16）：
  `t4-sign.log`（首次含 zip 的崩溃实录）、`t4-sign2.log`（成功那次）、`t4-artifact-tests.log`、`t4-full-suite.log`（全量 discover）、`t4-cert-probe.ps1`、`t4-sign-runner.ps1`（正确传参的 runner）、`t4-bundle.py`（清单 + zip + release 校验和）、`t4-verify.py`（只读逐条复算）。
* 未改：`tests/**`、`web/**`、`desktop/**`、`Dockerfile`、`docker-compose.yml`、`agent_explainer/**`。
