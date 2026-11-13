# R2 独立复核：文档口径 · 总包一致性 · F3/F4/F5（t9 / QA）

> 复核人：`qa`（独立于 R2 两个实施轮 t7 / t8）。本轮**每一条结论都来自我自己跑的命令与原始输出**，
> 不照抄成员结论；跑不了的写「未验证 + 原因」。
>
> 被复核对象：R2 两轮改动（t7 文档口径闭合 + 总包重打；t8 测试/工具卫生）。
> 时间：2026-11-12 05:28 → 05:45（Asia/Shanghai）。
> 写作用域：**只写 `docs/18-release-0.5.0-followup.md` 与 `docs/17-release-0.5.0-acceptance.md` 的 §13**；
> 代码 / 脚本 / 产物零改动（例外：一处**经队长授权的临时探针**改了 `tests/test_crypto_sig.py` 并在同一分钟内按字节还原，见 §5.3 与 §10）。

---

## 0. 结论

**有条件 PASS（打 tag 前必须闭合 D1、D2）。**

| 项 | 判定 |
|---|---|
| R2 复核总体 | **有条件 PASS** |
| 发布件本体（两个 exe / 镜像 / 离线 tar / 校验链 / 签名口径） | **未被 R2 改动，PASS**（§4） |
| F1（镜像"未产出"口径） | **闭合**（§1） |
| F2（0.4.5 指引与哈希） | **闭合**（§2） |
| 总包一致性（zip / 清单 / 包内文档） | **本体 PASS，但缺 1 份 R2 实录**（D2，§3） |
| F3 / F4 / F5 | **闭合**（§5 / §6 / §7） |
| 闸门 | **不达标**：契约检查出现**第 2 条**失败「契约漂移」（D1，§8） |
| C1（便携 GUI / NSIS 安装） | **未闭合**（环境边界，§9） |
| C2 / C3 / C4 | **全部闭合**（§9） |

**新增缺陷 5 条 + 1 条信息**（§11）：D1 medium（契约漂移，一条命令可修但**必须修**）、D2 medium（总包缺 `docs/audit/v050-tests-tools.md`）、
D3 low（05.3 §1 表内缺行内时点标记）、D4 low（C1 指令里"`--port` 会 bad option"与本机实测不符）、D5 low（遗留风险①行号已从 448 漂到 502）、D6 info（我自己的探针写了一次 mtime）。

### 0.1 「0.5.0 能不能发」的定性（C1 保持未闭合的前提）

- **发布件本体：可以发。** 两个已签 exe 字节未动、哈希与签名状态逐字对得上；镜像/离线 tar 实态与文档一致；
  5 份校验清单逐条复算全过；三形态实测（源码 / 容器 / 便携运行时）在 T5 已闭合。
- **按团队自己的闸门口径：现在不能打最终 tag。** 理由是两条**可一条命令/一次重打解决**的问题：
  D1 让 `gen_api_contract.py --check` 的失败数从 1 变 2（t1 的硬提示⑤明确要求改 `docs/03` 必须 `--write`）；
  D2 让总包少了 R2 自己的测试/工具实录。
- **C1（便携 GUI / NSIS 安装）未闭合 ≠ 发布件缺陷**，它是**验收动作缺位**：只能在有交互桌面的用户会话做。
  在它闭合前，对外只能说「便携版运行时与打包内容已验证，界面与安装流程待用户桌面会话签字」，
  **不能用源码态或打包 sidecar 顶替**（那是 T5 已经明确过的口径）。

---

## 1. F1：镜像 / 离线包「未产出」口径（**闭合**）

### 1.1 `grep -rn "未产出\|DOCKER-BLOCKED"` 逐条判定

命令与判定基准：**历史标注 = 可接受；读起来像事实地否认已产出的镜像 = FAIL；把"未产出"当唯一现况 = FAIL。**

| 命中位置 | 内容 | 判定 |
|---|---|---|
| `docs/05.4-docker.md:467` | **`✅ 已产出（2026-11-12，完全权限会话收口）`** + 镜像 id / 246,561,040 B / LABEL 0.5.0 + tar 64,029,696 B / `d93d1803…` + `docker-verify.sh` 全部通过 + **保留三条环境边界** | **可接受（已改实态）** |
| `release/0.5.0/DOCKER-BLOCKED.md:1` | `# 0.5.0 镜像离线包 —— **已闭合**（本文是上一轮「未产出」记录的历史归档）`，§1 实态表、§2 历史原文与三条环境边界原样保留 | **可接受（历史归档 + 实态置顶）** |
| `docs/05.4-docker.md:3` / `docs/05.3-release-checklist.md:3` | 口径句「跑不动就写"未产出 + 复现命令 + 原始报错"」 | 可接受（纪律条款） |
| `README.md:318` / `CHANGELOG.md:146` / `docs/03:558,564` | 「单文件（onefile）sidecar exe 未产出」 | 可接受（**真实现状**：onefile 本机引导失败；交付布局是 onedir） |
| `README.md:391` | 文档索引描述「含未产出项与原始报错」 | 可接受（描述性） |
| `docs/03-architecture.md:679,686` | 「签名未产出」但同段前有 `> **现状更新（2026-11-12，R2-1）**`，下行标 `本机现状（**0.4.5 时点**…）` | 可接受（行内时点）
| `docs/05.3-release-checklist.md:14,20,21,22,255` | §1 打包状态表仍 `❌ 未产出 / 仍未产出`（NSIS / portable） | **弱标注（D3，low）**：靠顶部「数据截至 2026-11-11」+ §10「5.5 追记…已产出」+ §12「0.5.0 真实产物清单」消歧，表内无行内时点 |
| `docs/05.3-release-checklist.md:276,315,316` | 明确写「那是 5.3 / 5.3b 两次任务当时」 | 可接受（历史标注） |
| `docs/audit/v050-desktop.md:12`（表前） | `> **读表前必看（2026-11-12 R2-1 追加，防被读成假数据）**` + 0.5.0 两个 exe 的字节与哈希 | 可接受（**表前**取代说明，t7 新增） |
| `docs/audit/v050-desktop.md:24,427` | 表中「两个 exe 未产出」/§7 未做项 1 | 可接受（紧邻上一条说明） |
| `docs/audit/v050-docker.md:297,310,329` | §1–§11 历史；:329「上面 §1–§11 关于"镜像未产出"的结论已被本节取代」 | 可接受（历史标注） |
| `docs/17-release-0.5.0-acceptance.md:24,625,678` | T5 报告里**引用**当时的两处缺陷 | 可接受（T5 记录） |
| `docs/audit/v050-docs-sync.md:*`、包内同名副本 | R2-1 自己的 before→after 表，引用旧文字 | 可接受（变更记录） |
| `release/tracelens-0.4.5-release/**`、`release/tracelens-0.4.5-release.zip` | 0.4.5 归档里的历史记录（含 onefile/NSIS 未产出） | 可接受（归档，二进制 zip 命中属 grep 噪声） |

**没有任何一处仍然把「0.5.0 镜像 / 离线 tar」说成未产出** —— F1 的两处点名位置都已改成实态。

### 1.2 `docs/05.4-docker.md` 该节 vs 实态（独立复测）

```
sed -n '467p' docs/05.4-docker.md
| **0.5.0 镜像与离线 tar** | ✅ 已产出（2026-11-12，完全权限会话收口） | `tracelens:0.5.0` =
  `sha256:6cb8417164f30140ba800cb950f23e0679e6983b8c86f91a803cc0d6cd586ff0`，**246,561,040 字节**，
  `LABEL org.opencontainers.image.version=0.5.0`…；离线包 **64,029,696 字节**、sha256 `d93d1803…`…
  `docker-verify.sh` 结论「全部通过」… **环境边界（保留，别删成"哪都能跑"）**：成员受限会话打不开
  `npipe:////./pipe/dockerDesktopLinuxEngine`、`wsl.exe` = `Wsl/ACCESSDENIED`、`bash` 连 signal pipe 都建不出来…
```

我自己跑的对照：

```
docker version                     → Server: Docker Desktop 4.99.0 (245822) / Engine 29.9.3 / linux/amd64
docker image inspect tracelens:0.5.0 --format '{{.Id}} | {{.Size}} | {{index .Config.Labels "org.opencontainers.image.version"}}'
  → sha256:6cb8417164f30140ba800cb950f23e0679e6983b8c86f91a803cc0d6cd586ff0 | 246561040 | 0.5.0
sha256sum release/0.5.0/tracelens-0.5.0-image.tar
  → d93d1803c6ecff90aabcca5f76536f6329daa84671da695382fefd5b62803ab9
ls -la release/0.5.0/tracelens-0.5.0-image.tar  → 64,029,696 B（Nov 12 04:10）
grep "结论" release/0.5.0/docker-verify.log     → 264:结论：全部通过 / 266:结论：全部通过
```

**三条环境边界仍在（不是被删光）**：仓库侧 3 处、包内侧 3 处命中：

```
grep -rn "dockerDesktopLinuxEngine" docs README.md release/0.5.0/DOCKER-BLOCKED.md
  docs/05.4-docker.md:467 / docs/audit/v050-docker.md:32,330 / docs/audit/v050-docs-sync.md:46
  release/0.5.0/DOCKER-BLOCKED.md:29,31          （包内：05.4 / v050-docker / v050-docs-sync 各 1 处）
grep -rn "ACCESSDENIED"  → docs/audit/v050-docker.md:35 / docs/audit/v050-docs-sync.md:46 /
                            release/0.5.0/DOCKER-BLOCKED.md:33 / docs/05.4-docker.md:467
grep -rn "signal pipe"   → docs/audit/v050-docker.md:36,94 / release/0.5.0/DOCKER-BLOCKED.md:34 / docs/05.4-docker.md:467
```

**F1 判定：闭合。**（唯一保留意见是 D3 那条 `05.3` §1 表内弱标注。）

---

## 2. F2：0.5.0 哈希/字节逐条独立复算（**闭合**）

### 2.1 方法

把 `release/**`、`desktop/build/dist/**`、`desktop/build/resources/sidecar/**` 里 **859 个文件**的
sha256 与字节数全量算出（479 个唯一哈希），再把文档里出现的每个 64-hex / 12–16 位前缀拿去**反查实测文件**，
**不看文档自己打印的值**。

```
实际文件数: 859  唯一 sha256: 479
全文 64-hex 出现 29 次；命中实测文件 19 次；未命中 21 条
```

### 2.2 未命中的 21 条 —— 逐条定性（全部可解释，无一条是"编出来的 0.5.0 值"）

| 未命中值 | 出现位置 | 定性 |
|---|---|---|
| `6cb8417164f3…ff0` | `docs/05.4-docker.md:15,467`、`release/0.5.0/DOCKER-BLOCKED.md:11`、`docs/audit/v050-docs-sync.md:45,292` | **docker 镜像 config digest**（不是文件哈希）；权威在 `release/0.5.0/IMAGE-INFO.json` 的 `image_id`，我实测 `docker image inspect` 逐字相同 |
| `51d5b69752ce…b0b4b1` | `docs/05.4-docker.md:28,119,322`、`docs/audit/v050-docs-sync.md:286` | **0.4.5 镜像 id（历史）**，本文顶部已标 §2–§13 属 0.4.5 时点 |
| `22f78162…460e`（10,155,966 B） | `docs/05.3…:177`、`docs/audit/v050-docs-sync.md:284` | **0.4.5 时点 sidecar exe**，工作树那份已被 0.5.0 构建替换（现 10,403,499 / `f00527ca…`）；白名单已写明 |
| `ead2058ee0d3e472…`（38,976,123 B） | `docs/audit/v050-docs-sync.md:285` | 0.4.5 时点 onefile 证据件，t2 已删除（白名单已写明） |
| `ef837389db3b…` | `README.md:371`、`docs/audit/v050-docs-sync.md:15,74,100` | **旧版 README 的错值**，只作为「已错」记录保留，同段给出实测 `0c5d5552…` |
| `66a50950…0ee4` / `71232f5f…` / `08a61812…` | `docs/audit/v050-docs-sync.md:112-114` | **总包两轮重打的历史哈希留痕**（t5 收口值 / R1 / R2 前），本文明确写「权威只认 `release/SHA256SUMS-release.txt` 第 2 行」 |
| `288a1bed041bc7fc…` | `docs/audit/v050-tests-tools.md:62` | F3 证据里用的**固定 seed**，不是文件哈希 |
| `ad522fb8…`、`6194d3f4…`、`e7f31a4f…`、`f7786807…`、`e006d4c9…`、`7d618ce3…`、`0841ac59…`、`809dea78…`、`fb41ab5d…` | `docs/audit/v050-docs-sync.md:290` | 该行是**「不可复算白名单」**，逐条注明出处（正文哈希/链头/序号），不是交付件哈希 |

### 2.3 字节数 + 哈希成对核对（12 处命中行）

对「行内有 0.5.0 文件哈希」的 12 处逐行核对字节数：**7 处该行只写哈希、字节数在相邻行**（`05.4:15-17` 的
镜像/tar 块、`DOCKER-BLOCKED:13-15` 实态表、`05-real-user-kit:127-128` 的期望注释），逐条读上下文后**全部正确**；
其余 5 处哈希与字节同处一行，**全部与实际文件一致**。

关键几行原样（与实测一致）：

```
README.md:96   | 大小 / SHA256 | 151,838,192 字节 / `37cf6963…e0395d`（逐字取自 release/SHA256SUMS.txt）
README.md:120  | 免安装可执行 | release/TraceLens-0.5.0-win-x64-portable.exe | 137,883,824 | `8bdc55a089dd678d`
README.md:121  | 源码便携包   | …portable-src.zip | 4,957,816 | `098c3bcd472842c1`
README.md:336  | 打包版 sidecar | …agent-explainer-sidecar.exe | 10,403,499 | `f00527ca2c9ac567`
README.md:337  | Docker 离线包 | release/0.5.0/tracelens-0.5.0-image.tar | 64,029,696 | `d93d1803c6ecff90`
README.md:334  | 校验与签名状态 | SHA256SUMS.txt / SIGNATURE-STATUS.json | 205 / 4,918 | —
```

### 2.4 README:356 那组 0.4.5 错数字

```
README.md:369  > **历史快照（0.4.5 时点，不要拿去当 0.5.0 的校验值）**：
README.md:370  > `release/tracelens-0.4.5-release.zip` 实测 **356,074,424 字节 /
                 `0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab`**
README.md:371  > （逐字取自 `release/SHA256SUMS-release.txt` 第 1 行）。本文旧版写的
                 `356,073,874 B / ef837389db3b…`
```

我实测：`0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab` = `release/tracelens-0.4.5-release.zip`（356,074,424 B）。
**README:356 那组已修成实测值，旧值只作"已错"记录保留。**

### 2.5 「历史 0.4.5 章节必须标注历史快照」

| 文件 | 实测 | 判定 |
|---|---|---|
| `README.md` | :369 `历史快照（0.4.5 时点…）`；:90 `0.4.5 时点实测`；:321/:323 历史收口说明 | 合格 |
| `docs/06-faq.md` | :37 `0.4.5 时点的本机实测`；:211 `0.4.5 的老包…别拿它的 tar 当 0.5.0 用` | 合格 |
| `docs/06-user-guide.md` | `grep "0.4.5"` = **0 命中**（全换 0.5.0） | 合格 |
| `docs/NOTICE-third-party.md` | `grep "0.4.5"` = **0 命中** | 合格 |
| `docs/05-real-user-kit.md` | :66 `0.4.5 时点实测`；:60 历史状态说明 | 合格 |
| `docs/05.3-release-checklist.md` | 顶部「数据截至 2026-11-11」+ §10 追记 + §12 0.5.0 清单；§1 表内无行内时点（D3） | 弱（low） |
| `docs/05.4-docker.md` | 顶部「版本时点（读前必看）」框：§2–§13 属 0.4.5 时点 | 合格 |
| `docs/03-architecture.md` | :679 现状更新框 + `本机现状（0.4.5 时点）` | 合格 |

**F2 判定：闭合**（D3 为低级别改进项，不构成 F2 未闭合）。

---

## 3. 总包一致性（**本体 PASS，缺 1 份 R2 实录 → D2**）

### 3.1 zip ↔ 目录集合

```
zip 条目数 = 25  文件条目 = 25  目录条目 = 0
压缩方式集合 = [0] (0=STORED)
zip 本体 = 359,034,202 B   sha256 = 41b9beab6a96a25d2c19d42e9f6e2f86a39ab83fdc0f6079a44a4af5306db197
目录文件数 = 25
zip 有目录无 = []        目录有 zip 无 = []
包内 vs 仓库同名文件逐字节：相同 25 / 不同 0
```

### 3.2 清单：编码、行尾、逐条复算

```
SHA256SUMS-bundle.txt: bytes=2201  BOM=False  CRLF=24  裸LF=0  U+FFFD=0  条数=24
bundle 逐条：OK 24 / FAIL 0
release/SHA256SUMS-release.txt（cat -A）
  0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip^M$
  41b9beab6a96a25d2c19d42e9f6e2f86a39ab83fdc0f6079a44a4af5306db197  tracelens-0.5.0-release.zip^M$
```

- **0.4.5 行逐字未变**：`0c5d5552…139ab`，与我 T5 记录、`docs/audit/v050-docker.md:281`、`docs/audit/v050-release.md:209/233` 逐字相同。
- **zip 权威哈希只认第 2 行**：第 2 行 `41b9beab…` = 我实测 zip 哈希；且 `grep -rn "41b9beab" README.md docs release/tracelens-0.5.0-release` = **0 命中**（文档里没有自引用）。
- 包内 `SHA256SUMS.txt`（205 B）与 `SIGNATURE-STATUS.json`（4,918 B）与仓库同名文件 `cmp` → **逐字节相同**。
- 两轮历史哈希（`66a50950…`/`71232f5f…`/`08a61812…`）只出现在 `docs/audit/v050-docs-sync.md` 的留痕节，
  并明确写「zip 自身哈希在文档里不自引用，只写权威位置」。

### 3.3 包内文档 ↔ 仓库文档

25 个条目逐字节比对 **25/25 相同**（含 8 份本轮改过的入包文档）。但**清单集合有缺口**：

```
仓库 docs/audit/v050-*.md: desktop, docker, docs-sync, release, sidecar, tests-tools, version   （7 份）
包内 docs/audit/v050-*.md: desktop, docker, docs-sync, release, sidecar, version               （6 份）
包内缺: ['v050-tests-tools.md']
```

`docs/audit/v050-tests-tools.md`（18,576 B，mtime 05:27:32）**晚于**总包重打时间（zip 05:07:54），因此：
- 总包与仓库不同步（包内 25 条、仓库文档多 1 份）；
- `SHA256SUMS-bundle.txt` 的 24 条里没有它；
- 队长口径「新增实录 `v050-docs-sync.md`、`v050-tests-tools.md` 必须存在且**与包内同字节**」→ 后者**不满足**。

→ **D2（medium）**。`docs/audit/v050-docs-sync.md` 在包内且与仓库同字节（`7cb6ede0b26a8e69…` 两侧一致）。

> 说明（避免误判）：包内 **`docs/` 根目录**只收 6 份用户向文档（`05.3/05.4/06-*`/`NOTICE`），
> 仓库 `docs/` 有 24 份 —— 这是既有取舍（`docs/01/02/03/07/09/11/12/13/14/15/16/17` 不入包），**不是本轮缺陷**；
> 但 `docs/audit/v050-*` 一直是「全量入包」，`v050-tests-tools.md` 是唯一例外 → 记为缺陷。

---

## 4. 发布件未被偷改（**PASS**）

```
sha256sum release/TraceLens-0.5.0-win-x64-Setup.exe
  37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d   151,838,192 B  (Nov 12 03:29)
sha256sum release/TraceLens-0.5.0-win-x64-portable.exe
  8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52   137,883,824 B  (Nov 12 03:29)

Get-AuthenticodeSignature
  Setup.exe    Status=UnknownError  Thumbprint=<redacted-thumbprint>
               Subject=CN=TraceLens Self-Signed (Local Only)
  portable.exe Status=UnknownError  Thumbprint=<redacted-thumbprint>
release/SIGNATURE-STATUS.json:
  signed_count=0  signatures_applied=2  unsigned=True  trust_state=self_signed_root_untrusted
  artifacts[0] Setup.exe    151838192  37cf6963…e0395d  UnknownError  signer DF1CB56A…
  artifacts[1] portable.exe 137883824  8bdc55a0…fdd52  UnknownError  signer DF1CB56A…
```

**逐字一致，且 `UnknownError` 没有被写成 `Valid`（任务规定：写成 Valid 直接 FAIL —— 未发生）。**
文件 mtime 仍是 03:29（签名完成时刻），R2 两轮没有碰二进制。

---

## 5. F3：概率性断言（**闭合**）

### 5.1 用例现状（我读原文）

```python
seed = bytes(range(1, 33))          # 固定 seed：签名逐轮一致
signature = cs.sign(seed, message)
flipped = bytes([signature[0] ^ 0x01]) + signature[1:]
self.assertNotEqual(flipped, signature,
                    "变异签名与原签名相同 = 这条断言在空转（就是 F3 的假红形状）")
self.assertFalse(cs.verify_b64(pub_text, message, cs.b64e(flipped)))
```

- 固定 seed（Ed25519 确定性签名）→ 逐轮可复现；
- 变异改成**首字节异或 0x01**（`x ^ 0x01 != x` 结构性成立）；
- 多了一条**显式守卫** `assertNotEqual(flipped, signature)`；
- 没有放宽、没有 skip、没有删用例（`test_public_key_b64_and_verify_b64` 仍在，另两条 `assertFalse` 原样保留）。

### 5.2 连跑 50 次

```
前 30 次：pass=30 fail=0
后 10 次（本轮自跑）：pass=10 fail=0
追加 10 次：pass=10 fail=0
→ 累计 50 次全绿（0 失败）
```

### 5.3 「被异或字节与 mask 相同时确实失败」+ 断言未削平的独立复现

**（a）直接 API 层（不改任何文件）**

```
A. 固定 seed：verify(原签名) = True
B. 首字节 ^0x01（测试实际用的变异）：verify = False
C. 样本 seed 使 signature[0]=0x01（**被异或字节 == mask**）：
   2000 个不同 seed → signature[0] 覆盖 256 个取值、0x01 出现 9 次；
   命中样本：变异首字节=0x00，相同? False，verify = False        ← 篡改被真检出
D. mask == 被异或字节的取值（x ^ x = 0）：mask=0x32 → 变异首字节=0x00，相同? False，verify = False
E. 空操作变异（旧 F3 形状）：verify = True → 若没有 assertNotEqual 守卫就会假红
```

**（b）临时改测试文件做「空操作变异」探针（经队长指令授权，已还原）**

把 seed 换成「末字节 = 0x00 的确定性 seed」（`84f59c02…d2e9`，现场搜索得），变异换回旧写法
`signature[:-1] + b"\x00"`，跑该用例：

```
Traceback (most recent call last):
  File "…\tests\test_crypto_sig.py", line 380, in test_public_key_b64_and_verify_b64
    self.assertNotEqual(flipped, signature,
AssertionError: b'u|#&W\x9b(=XQ…\x05\x00' == b'u|#&W\x9b(=XQ…\x05\x00' :
    变异签名与原签名相同 = 这条断言在空转（就是 F3 的假红形状）
Ran 1 test in 0.006s
FAILED (failures=1)
```

→ **空操作变异现在会让测试直接红**（守卫命中），证明断言**没有被削平**，也不可能再"空转通过"。

**（c）还原与 mtime 证据**

```
探针文件（打补丁时） sha256 = e35b92b7f4edfc4fe91f385d…   bytes = 23,986
还原后            sha256 = cfac73eeff8d811fcc86cdafe3c71c7800617083e93dce0bd5711604efbdc410   bytes = 23,413
与备份逐字节相同 = True ；与打补丁前逐字节相同 = True
还原后 mtime = 05:35:36   mtime_ns = 1794432936988749100
补丁前记录 mtime_ns = 1794431944341114800（t8 的 05:19:04）
还原后再跑 1 次 → OK
```

**内容已按字节还原**（sha256 与备份、与打补丁前完全一致）；mtime 变成 05:35:36 是**还原写入**造成的，
所以做 mtime 越界扫描时会看到 `tests/test_crypto_sig.py` 在 05:35 有一次"改动"——**那是我的探针还原，不是 t8 又改**（D6，info）。

**F3 判定：闭合。**

---

## 6. F4：全量套件不再污染仓库 `var/features`（**闭合**）

### 6.1 跑前（基线 = 905 B / 4 文件，与队长口述一致）

```
du -sb var/features → 905
PRE 文件数 4 总字节 905
           0 e3b0c44298fc1c14… var\features\secretariat\heads.jsonl.lock
         567 10481ef7df19d1b4… var\features\secretariat\identities.jsonl
           0 e3b0c44298fc1c14… var\features\secretariat\identities.jsonl.lock
         338 c71a94128b604b82… var\features\secretariat\keyring.json
```

### 6.2 跑全量套件（954 / OK / skipped=3 / exit 0）

```
python -m unittest discover -s tests -p "test_*.py" -q
EXIT=0
Ran 954 tests in 165.065s
OK (skipped=3)
```

### 6.3 跑后

```
du -sb var/features → 905
POST 文件数 4 总字节 905
文件集合相同: True
  SAME         0 e3b0c44298fc1c14… var\features\secretariat\heads.jsonl.lock
  SAME       567 10481ef7df19d1b4… var\features\secretariat\identities.jsonl
  SAME         0 e3b0c44298fc1c14… var\features\secretariat\identities.jsonl.lock
  SAME       338 c71a94128b604b82… var\features\secretariat\keyring.json
```

**逐文件 sha256 与字节数全部相同，总量仍是 905 B** → 测试不再往仓库账本写入。
（t8 的常驻断言 `_ledger_fingerprint` 也在用例里；我这次是**套件外**独立统计，两者互相印证。）

**F4 判定：闭合。**

---

## 7. F5：`-CheckEncoding` 覆盖 0.5.0 全套 + 脚本 BOM（**闭合**）

```
python -c "读 tools/sign_and_hash.ps1" → bytes=35048  BOM=True  U+FFFD=0
PowerShell Parser::ParseFile → syntax_errors=0
powershell -File tools/sign_and_hash.ps1 -CheckEncoding  → EXIT=0
```

```
--- JSON 清单（要求 UTF-8 无 BOM、中文完好、可解析）
[OK] desktop\build\dist\SIGNATURE-STATUS.json                           4918 字节  BOM=False 中文=True JSON=True
[OK] release\SIGNATURE-STATUS.json                                      4918 字节  BOM=False 中文=True JSON=True
[OK] release\tracelens-0.5.0-release\SIGNATURE-STATUS.json              4918 字节  BOM=False 中文=True JSON=True
--- 文本清单（要求 UTF-8 无 BOM、无 U+FFFD、逐行 sha256 格式、换行口径）
[OK] release/SHA256SUMS-release.txt                                     190 字节  条目=2  换行=CRLF
[OK] release/0.5.0/SHA256SUMS.txt                                        92 字节  条目=1  换行=LF
[OK] release/tracelens-0.5.0-release/SHA256SUMS.txt                     205 字节  条目=2  换行=CRLF
[OK] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt             2201 字节  条目=24 换行=CRLF
[OK] tools/sign_and_hash.ps1                                          35048 字节  BOM=True（脚本自身要求 BOM）
结论：编码审计通过：JSON 与文本清单全部 UTF-8 无 BOM、中文完好、脚本自身 BOM 在位
```

- **0.5.0 全套在列**（`release/tracelens-0.5.0-release/...` 那份在 3 条 JSON 里），不再指向 0.4.5 包；
- 0.4.5 审计改成可选的 `-Include045`（本轮未跑，属可选路径）；
- `ps1` 的 `Set-AuthenticodeSignature` → `Get-AuthenticodeSignature` **句柄缺口仍然存在、只登记不修**：
  现在在 **496 → 502 行**（t8 加代码后从 448 漂移），中间没有释放/重试（D5）。

**F5 判定：闭合。**

---

## 8. 闸门（**不达标：契约多出「契约漂移」**）

| 闸门 | 实测 | 判定 |
|---|---|---|
| 全量 discover | `Ran 954 tests in 165.065s / OK (skipped=3) / EXIT=0` | **PASS**（954 与基线一致；skipped 仍是 3） |
| skipped 来源 | 3 条全为环境条件 skip，**无一是无条件 skip 或新增 skip**（见 8.1） | **PASS** |
| `tools/i18n_lint.py` | `EXIT=0`，5/5 PASS：字典 zh 1115 / en 1115 / 27 域，硬编码 CJK 0，错误码 168（有键 75/回退 93），生成物同步，双语夹具 12×2 | **PASS** |
| `tools/gen_api_contract.py --check` | `EXIT=1`，**失败 2 项**：①前端调用面 8 个非总裁办端点（基线内）②**契约漂移**（新增，D1） | **FAIL（D1）** |
| `node web/selfcheck.mjs` | `self-check: 92/92 passed`，`EXIT=0` | **PASS** |

### 8.1 skipped=3 逐条点名（并证明"没有用 skip 消红"）

```
python -m unittest discover -s tests -p "test_settings_api.py" -v | grep -i skip
  test_file_mode_is_owner_only_on_posix … skipped 'POSIX 位权限只在类 Unix 文件系统上有意义'
  OK (skipped=1)
python -m unittest discover -s tests -p "test_audit_log.py" -v | grep -i skip
  test_no_reportlab_falls_back_to_printable_html … skipped '本机有 reportlab（None），回退路径由 stub 用例覆盖'
  test_prefer_pdf_raises_when_reportlab_is_unavailable … skipped "本机有 reportlab：prefer='pdf' 应当成功出 PDF"
  OK (skipped=2)
python -m unittest discover -s tests -p "test_crypto_sig.py" -v | grep -i skip  → （无）
python -m unittest discover -s tests -p "test_e2e.py" -v | grep -i skip          → （无）
```

- **3 条 skip 全是"平台/可选依赖"条件 skip**（POSIX 位权限 1 条 + 本机装了 reportlab 2 条），与 T4/T5/T6 的 `skipped=3` 逐字一致；
- **无条件 skip 装饰器数量 = 0**（`grep -rn "@unittest\.skip(" tests/*.py | wc -l` → `0`）；
- 装饰器级 `skipUnless/skipIf` 都是**既有**的（cryptography / Windows / SPAWN_OK），本轮 50 次连跑与全量跑均未触发；
- → **出现 skip 消红的嫌疑不成立**。

### 8.2 D1：契约漂移（本轮唯一的硬闸门偏差）

```
[FAIL] 契约漂移 —— 磁盘与本次生成不同，前 12 行 diff：
      -    "docs/03-architecture.md": "sha256:41fb68af8e13d3291367980da7dfd32448725302b3654e25b16e4e0efe2f3ed2",
      +    "docs/03-architecture.md": "sha256:ac603bc09a1857eb1f7cdec0bdf30af935d08f833b03a719477b6844339c0575",
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应，方法列一致，无 ⚠ 行
[FAIL] 契约检查未通过：2 项
```

- `docs/api_contract.json` mtime = **01:32**（T0 时代），`docs/03-architecture.md` mtime = **04:55:53**（t7 的 R2-1 改动）；
- 契约里内嵌了源代码与文档的 sha256 指纹，改 `docs/03` 后必须 `python tools/gen_api_contract.py --write` 重生成 ——
  这正是 T0 给全队的硬提示⑤；t7 改了 `docs/03:679`（现状更新框）**但没有重生成契约**；
- 影响：闸门失败数由「唯一 1 项（8 端点）」变成 **2 项**；契约指纹不再描述随包文档；后续任何人跑 CI 都会红。
- 修法（**不在我的写作用域，只登记**）：`python tools/gen_api_contract.py --write`（只改 `docs/api_contract.json`）；
  修完复跑 `--check` 应回到「唯一失败 = 8 个非总裁办端点」。

**闸门判定：不达标（D1）。**

---

## 9. 放行条件台账（C1 未闭合，C2/C3/C4 闭合）

| 条件 | 状态 | 证据 |
|---|---|---|
| **C1** 便携版 GUI / NSIS 安装实测（用户桌面会话） | **未闭合**（环境边界，只能用户会话做） | 见 §9.1 原始输出与确切命令 |
| **C2** 修 F1/F2 文档口径 | **闭合** | §1（F1 两处改实态 + 环境边界保留）、§2（29 处 64-hex 全部可解释、README:356 改正、历史章节标注） |
| **C3** 修 `test_crypto_sig.py:367` 概率断言 | **闭合** | §5：固定 seed + 首字节异或 + 守卫；50 次连跑 0 红；空操作变异会被守卫判红；已按字节还原 |
| **C4** `-CheckEncoding` 覆盖 0.5.0 包 | **闭合** | §7：3 份 SIGNATURE-STATUS + 4 份清单 + 脚本自身 BOM，全 `[OK]` exit 0 |

### 9.1 C1：用户桌面会话要跑的确切命令（本轮实测依据 + 注意点）

> **本节命令已在 PowerShell 5.1 实测（t12 于 2026-11-12 逐条真跑，见 `docs/audit/v050-r3-closeout.md` §14.4）；
> 旧写法会把 JSON 拆碎（D8）**：内联 `-d "{\"a\":\"b\"}"` 在 PS 5.1 下服务端只收到半个 JSON，
> 真服务端回 `400 INTERVENE_BODY_INVALID / pos=1`，登记不到身份。
> 下面第 2) 步给三种写法：A 落文件 `--data-binary @`、B `Invoke-RestMethod`、C `--%` 停止解析符；
> 每种都标了**请求体字节数 + 期望状态码**。存成 `.ps1` 时请存 **UTF-8 with BOM 或纯 ASCII**（D10）。

本会话对打包 exe 的实测（ASCII 目录 `E:\qa-t9-probe`，跑完已删）：

```
--port 18802                → exit=9   stdout 0 B  stderr 0 B
--user-data-dir E:\qa-t9-probe\ud → exit=9   0 B / 0 B
--selfcheck                 → exit=9   0 B / 0 B
--selfcheck --selfcheck-out …\sc.json → exit=9   0 B / 0 B
--no-sandbox                → exit=9
--enable-logging            → exit=9
--version                   → exit=0
（无参数）                   → exit=0
```

> **对指令的一处更正（D4，low）**：卡片/指令说「`--port`/`--user-data-dir` 会 `bad option` 直接退出」。
> 本机实测是 **exit=9 且 stdout/stderr 全空**，**没有 `bad option` 字样**；而 `desktop/main.js` 明确解析
> `--port / --data-dir / --features-dir / --mode / --demo`（`flag('port')` 等），并有 `TRACELENS_PORT /
> TRACELENS_DATA_DIR / TRACELENS_FEATURES_DIR / TRACELENS_MODE` 环境变量兜底。
> 由于本会话 GUI 根本起不来（无交互桌面），**exit=9 的成因（参数解析 vs 无桌面）不可判** ——
> 所以下面命令**以环境变量为主**，`--selfcheck` 作为可选复核项（用户在桌面会话若仍 exit=9，则是一条真缺陷）。

用户在**自己的桌面会话**里按顺序跑（ASCII 目录，勿在中文路径下跑）：

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

#    写法 B：PowerShell 原生（不经 curl 的参数引号层）—— 实测请求体 94 字节 → 201
$r = Invoke-RestMethod -Method Post -ContentType "application/json" -Body $body `
     -Uri "http://127.0.0.1:18802/api/secretariat/identities"; $r | ConvertTo-Json -Compress

#    写法 C：--% 停止解析符，后面原样交给 curl —— 实测 201（这条是本轮 t12 实测补的）
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

# 6) 可选：无人值守自检（main.js 的 --selfcheck 模式；本会话 exit=9 不可判，桌面会话应打印一行 JSON）
.\TraceLens-0.5.0-win-x64-portable.exe --selfcheck --selfcheck-out .\selfcheck.json
Get-Content .\selfcheck.json    # {"tracelens_desktop_selfcheck":{"ok":true,…}}
```

判定口径：三端点 200 + `version":"0.5.0"` + 登记 201 + 面板可见 + `_internal\var` 不存在 + 重启计数不减
+ `/S` 退出码 0 ⇒ C1 闭合。**这条只能用便携版/安装包本体做，不得用源码态或打包 sidecar 顶替。**

**t12 补记（D8 闭合 + 等价验证的边界，如实标）**：本轮（backend t12）在 PowerShell 5.1.22621 下把上面三种写法
逐条真跑过，服务端是**打包 sidecar 本体**（`desktop/build/resources/sidecar/…/agent-explainer-sidecar.exe`，
`--sidecar --port 18812 --mode local --data-dir … --features …`，`/health` 首 poll 即 200）：
写法 A 请求体 **92 字节 → 201**（`seq=1`、`entry_hash=7ab81c69…`）、写法 B **94 字节 → 201**（`seq=2`）、
写法 C（`--%`）**201**（`seq=3`）；旧的内联 `\"` 写法 → **400**（`pos=1`）。三端点 `curl.exe -o NUL -w %{http_code}`
实测 200/200/200，账本落在 `--features` 指定目录（`identities.jsonl` 1,130 B、`keyring.json` 563 B/`0o600`）。
**这是等价验证，不等于 C1 闭合**：便携版 GUI 在 DSH 会话起不来（见本文 §9.1 开头与 `docs/audit/v050-docs-sync.md` §11
的三组观测），面板可见性、升级持久性、`/S` 静默安装与 `_internal\var` 运行期检查**仍必须在用户桌面会话做**。

**与 QA t11 的一处观测差异（并列登记，不合并）**：QA 在 `docs/19` §9.4 说三种写法都通，其中第三种是
「单引号字面量 JSON」。本轮实测：**单引号字面量（`-d '{...}'`）在本机 PS 5.1 下 → 400**（不带空格与带空格两种都试过，
服务端收到的是丢了引号的 body），所以本节第 3 种改成本轮实测 201 的 `--%` 停止解析符写法。
两组观测的会话与调用方式不同（QA：控制台粘贴 + 本地回显服务器；本轮：`powershell -File` + 真服务端），
**不合并成"哪一种在 PS 5.1 普遍可用"的结论**；要一条稳的，用写法 A（落文件）。

---

## 10. 无 git 的越界核对（mtime 扫描）

`git status --porcelain` → `fatal: not a git repository (or any of the parent directories): .git`（exit 128，与 t8 披露一致）。
改用 **mtime 扫描**（自 2026-11-12 04:40 起，R2 两轮时间窗）：

### 10.1 工作区（排除 `.tmp/ release/ desktop/build/ var/ logs/ node_modules/ __pycache__`）

```
改动 14 个文件：
  04:45:19  38141  docs/05.4-docker.md                  ← t7
  04:51:54  31243  README.md                            ← t7
  04:52:16  15271  docs/06-faq.md                       ← t7
  04:52:19  11036  docs/06-user-guide.md                ← t7
  04:52:32   5558  docs/NOTICE-third-party.md           ← t7
  04:52:58  27456  docs/05-real-user-kit.md             ← t7
  04:55:39  30955  docs/audit/v050-desktop.md           ← t7
  04:55:39  36393  docs/05.3-release-checklist.md       ← t7
  04:55:53 170991  docs/03-architecture.md              ← t7（并触发 D1 契约漂移）
  05:07:48  31363  docs/audit/v050-docs-sync.md         ← t7
  05:18:43  35048  tools/sign_and_hash.ps1              ← t8 ✔ 声明内
  05:19:04  23413  tests/test_crypto_sig.py             ← t8 ✔ 声明内（05:35:36 又写一次 = 我的探针还原，D6）
  05:19:33  46785  tests/test_e2e.py                    ← t8 ✔ 声明内
  05:27:32  18576  docs/audit/v050-tests-tools.md       ← t8 ✔ 声明内
```

### 10.2 产品代码（`agent_explainer/ desktop/ scripts/ Dockerfile docker-compose.yml tools/*.py`）

```
命中 3 个：tools/sign_and_hash.ps1（t8 声明）、tests/test_e2e.py（t8 声明）、tests/test_crypto_sig.py（t8 声明 + 我的探针还原）
→ agent_explainer/** 0 改动、desktop/** 0 改动、scripts/** 0 改动、Dockerfile / docker-compose.yml 0 改动
```

### 10.3 `release/**`（t7 的重打范围）

```
04:45:19   4140  release/0.5.0/DOCKER-BLOCKED.md                    ← t7 声明
04:45:19  38141  release/tracelens-0.5.0-release/docs/05.4-docker.md
04:51:54  31243  release/tracelens-0.5.0-release/README.md
04:52:16  15271  …/docs/06-faq.md    04:52:19 11036 …/docs/06-user-guide.md
04:52:32   5558  …/docs/NOTICE-third-party.md
04:55:39  30955  …/docs/audit/v050-desktop.md   04:55:39 36393 …/docs/05.3-release-checklist.md
05:07:48  31363  …/docs/audit/v050-docs-sync.md
05:07:54    190  release/SHA256SUMS-release.txt      05:07:54  2201 …/SHA256SUMS-bundle.txt
05:07:54 359034202 release/tracelens-0.5.0-release.zip
```

### 10.4 清理项与会话卫生

```
仓库根 tmp* = 0            （ls -d tmp* | wc -l）
.tmp 项数   = 223          （队长口径 221；我的 4 个证据件：t9-actual.json / t9-sizes.json / t9-var-pre.json / t9-discover.log，另 i18n/contract 日志）
E:\qa-tl050-portable      → No such file or directory（已删）
.tmp/qa*                  → No such file or directory（已删）
var/features              → 4 文件 / 905 B（heads.jsonl 已按队长清理）
E:\qa-t9-probe            → 我建了又删（本轮 C1 探针，已确认不存在）
```

**越界判定：t8 只碰了 4 个声明文件；t7 的改动全部落在文档 + 总包重打；产品代码零改动；
唯一"计划外"的写入是 §5.3 那次**经授权的探针改/还原**（内容已按字节还原）与我自己的 `.tmp/t9-*` 证据件。**

---

## 11. R2 缺陷清单（severity + 复现 + 影响）

| ID | severity | 缺陷 | 复现命令 | 影响 | 建议 |
|---|---|---|---|---|---|
| **D1** | **medium（打 tag 前必修）** | **契约漂移**：t7 改了 `docs/03-architecture.md`（04:55:53）但没跑 `gen_api_contract --write`，`docs/api_contract.json` 仍是 01:32 的旧指纹 → `--check` 失败数从 1 变 **2** | `python tools/gen_api_contract.py --check`（`[FAIL] 契约漂移 … docs/03-architecture.md: 41fb68af… → ac603bc0…`） | 闸门红数不符基线（「唯一失败=8 端点」不再成立）；契约指纹不描述随包文档；CI/验收会一直红 | `python tools/gen_api_contract.py --write` 后复跑 `--check`（只改 `docs/api_contract.json`，不动源码） |
| **D2** | **medium** | **总包缺 `docs/audit/v050-tests-tools.md`**：该文件 05:27 产出，晚于 05:07 的总包重打 → 包内 6 份 `v050-*`，仓库 7 份；`SHA256SUMS-bundle.txt` 24 条不含它 | `python -c "比较 docs/audit/v050-*.md 与 release/tracelens-0.5.0-release/docs/audit/"`；`unzip -l release/tracelens-0.5.0-release.zip | grep tests-tools`（无） | R2 的测试/工具实录不随包交付；「新增实录必须与包内同字节」不满足；总包与仓库不同步 | 重打总包（25→26 条目）+ 重算 `SHA256SUMS-bundle.txt` + 更新 `release/SHA256SUMS-release.txt` 第 2 行（zip 哈希会变），或在发布说明里显式声明「测试/工具实录不入包」并同步文档清单 |
| **D3** | low | `docs/05.3-release-checklist.md` §1 打包状态表（行 14/20/21/22/255）仍写 `❌ 未产出 / 仍未产出`，**表内无行内时点标记**；消歧靠顶部「数据截至 2026-11-11」+ §10 追记 + §12 清单 | `sed -n '7,25p' docs/05.3-release-checklist.md` | 快速翻到 §1 的读者可能误读为「0.5.0 的 NSIS/portable 没产出」 | §1 表头加一行「0.4.5 时点；0.5.0 现状见 §12」（含包内副本），再重打总包 |
| **D4** | low | C1 指令里「`--port`/`--user-data-dir` 会 `bad option` 直接退出」与本机实测不符：实测一律 **exit=9、stdout/stderr 0 B、无 `bad option` 字样**；`--version`=0、无参=0；`main.js` 确实解析 `--port/--data-dir/--features-dir/--mode/--demo` | `Start-Process TraceLens-0.5.0-win-x64-portable.exe -ArgumentList '--port','18802' -Wait; $p.ExitCode` → 9 | C1 的验收命令若照抄"开关不可用"的口径，会漏掉 `--selfcheck` 这条无人值守路径；也会把 exit=9 误归因为参数问题 | C1 以 `TRACELENS_*` 环境变量为主（§9.1 已给全命令），`--selfcheck` 列为桌面会话复核项（若桌面会话仍 exit=9 → 另立缺陷） |
| **D5** | low | 遗留风险①的行号已漂移：`Set-AuthenticodeSignature`(496) → `Get-AuthenticodeSignature`(502)，中间无释放/重试；旧文档引用「ps1:448」 | `grep -n "Set-AuthenticodeSignature\|Get-AuthenticodeSignature" tools/sign_and_hash.ps1` | 句柄缺口本身未修（按本轮口径"只登记不修"）；引用 448 行会找不到 | 文档里的引用改成 502（或按函数名引用）；后续加 `-NoSignExtensions` |
| **D6** | info | 我的 F3 探针在 05:35:36 写了一次 `tests/test_crypto_sig.py`（还原写入），mtime 扫描会看到该文件"05:35 又被改" | `ls -l tests/test_crypto_sig.py` | 不影响内容（sha256 与备份/补丁前一致，23,413 B）；只为避免 mtime 审计误判 | 无需动作（本条即披露） |

> 另有两条**非 R2 缺陷、但仍在册**的遗留（本轮复核仍在）：`scripts/verify_e2e.py` 里 `secretariat`/`identities`/`总裁办` 仍 **0 次**（42 项不含总裁办断言）、
> `ps1` 的 `.zip` 句柄锁仍在（D5）。两者都按「只登记不修」处理。

---

## 12. 本轮自身写操作与只读边界

| 动作 | 性质 |
|---|---|
| 新建 `docs/18-release-0.5.0-followup.md`；给 `docs/17-release-0.5.0-acceptance.md` 追加 §13 | **本轮唯一允许的写入** |
| `tests/test_crypto_sig.py` 打临时探针 → 按字节还原 | **经队长指令授权的例外**：sha256 从 `e35b92b7…`（打补丁态）回到 `cfac73ee…`（原始态，23,413 B），与备份逐字节相同；mtime=05:35:36（D6） |
| `node web/selfcheck.mjs` | **在 `.tmp/t9-web/` 的副本里跑**（92/92），仓库 `agent_explainer/web/selfcheck-report.json` 的 sha256 前后均为 `6d52cdd5691cc189…`，**没有被重写**（比 T5 那轮更干净） |
| `.tmp/t9-*.{json,log}`、`.tmp/t9-web/` | 证据件与临时副本（草稿） |
| `E:\qa-t9-probe`（C1 探针目录） | 建了又删，已确认不存在 |
| 发布件 / 校验和 / 签名 / 产品代码 / 文档（除上述两处） | **只读** |

---

## 附录 A：证据落点

| 证据 | 路径 |
|---|---|
| 闸门日志 | `.tmp/t9-discover.log`（954/OK/3 skip）、`.tmp/t9-i18n.log`、`.tmp/t9-contract.log`（2 项失败） |
| F4 跑前/跑后指纹 | `.tmp/t9-var-pre.json` + 本报告 §6 的 POST 输出 |
| 哈希反查底稿 | `.tmp/t9-actual.json`（859 文件 → 479 唯一 sha256）、`.tmp/t9-sizes.json` |
| F3 探针 | `.tmp/t9-f3-seed.txt`、`.tmp/t9-crypto-backup.py`（还原源） |
| selfcheck 副本跑 | `.tmp/t9-web/selfcheck-report.json`（92/92） |

## 附录 B：本轮命令清单（按执行顺序）

```bash
cd "E:/DEEPSEEK 黑奴/agent-explainer"
export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"

# F1
grep -rn "未产出\|DOCKER-BLOCKED" README.md docs release
sed -n '1,30p;455,480p' docs/05.4-docker.md; cat release/0.5.0/DOCKER-BLOCKED.md
docker version; docker image inspect tracelens:0.5.0 --format '{{.Id}} | {{.Size}} | {{index .Config.Labels "org.opencontainers.image.version"}}'
sha256sum release/0.5.0/tracelens-0.5.0-image.tar; grep "结论" release/0.5.0/docker-verify.log

# F2（哈希反查）
python  # 859 文件 sha256 → 反查文档里每个 64-hex / 12-16 位前缀；详见附录 A 底稿
grep -n "356,074,424\|0c5d5552\|356,073,874" README.md

# 总包
python -c "zipfile 比对条目/压缩方式/逐字节"
python -c "SHA256SUMS-bundle.txt 编码/行尾/24 条逐条复算"
cat -A release/SHA256SUMS-release.txt
grep -rn "41b9beab" README.md docs release/tracelens-0.5.0-release   # 期望 0 命中

# 发布件
sha256sum release/TraceLens-0.5.0-win-x64-{Setup,portable}.exe
powershell -NoProfile -Command 'Get-AuthenticodeSignature …'
python -c "json.load(release/SIGNATURE-STATUS.json)"

# F3
python -m unittest …test_public_key_b64_and_verify_b64 -q   # ×50
python  # A–E 五种变异场景
python  # 临时补丁 → 跑 1 次（FAILED，守卫命中）→ 按字节还原 → 复跑 OK

# F4
python  # var/features 指纹（跑前）
python -m unittest discover -s tests -p "test_*.py" -q
python  # var/features 指纹（跑后）

# F5
python -c "读 ps1 BOM/size/U+FFFD"
powershell -NoProfile -Command '[Parser]::ParseFile(...)'      # syntax_errors=0
powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -CheckEncoding

# 闸门
python tools/i18n_lint.py
python tools/gen_api_contract.py --check
cp -r agent_explainer/web .tmp/t9-web && (cd .tmp/t9-web && node selfcheck.mjs)

# 越界核对
git status --porcelain        # fatal: not a git repository（exit 128）
python  # mtime 扫描（04:40 起）
ls -d tmp* | wc -l; ls -A .tmp | wc -l
```
