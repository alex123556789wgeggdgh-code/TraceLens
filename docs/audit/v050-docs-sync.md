# TraceLens 0.5.0 — R2-1 发布件文档口径闭合（QA F1/F2）+ 总包重打：实录

执行：backend（后端工程师）；任务 t7；日期 2026-11-12。
QA 判 0.5.0「有条件 PASS」，放行条件 **C2 = F1 + F2** 由本轮收口。
**只改文档口径，零二进制改动**：两个已签 exe 与镜像 tar 的字节逐条复算未动（§6.4）。

---

## 0. 结论

| 项 | 结果 |
| --- | --- |
| F1（medium）「0.5.0 镜像未产出」的过期实话 | **已闭合**：`docs/05.4-docker.md` 的该行改成实态 + 顶部加"版本时点"标记；`release/0.5.0/DOCKER-BLOCKED.md` 全文改写为「已闭合」并保留环境边界；`docs/audit/v050-desktop.md` 加"读表前必看"取代说明 |
| F2（medium）0.4.5 文件名与哈希当交付指引 | **已闭合**：`README.md`（24 处指引全换成 0.5.0 实测值）、`docs/06-faq.md`(2)、`docs/06-user-guide.md`(2)、`docs/NOTICE-third-party.md`(7)、`docs/05-real-user-kit.md`(8)；历史 0.4.5 段落保留并明确标注"0.4.5 时点" |
| README:356 那组错数字 | 已按实测更正：**356,074,424 字节 / `0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab`**（旧文写 `356,073,874 B / ef837389db3b…`，两个都错） |
| 顺带修的事实性偏差（QA 未点名，本轮在自己改的段落里发现） | `docs/05.3-release-checklist.md` 的 0.4.5 交付表加"版本口径"标记 + 新增 §12 的 0.5.0 产物清单；§10.3「0.4.5 总包仍未产出」加现状更新；`docs/03-architecture.md:679`「签名未产出」标成 0.4.5 时点并加 0.5.0 已签名的现状段 |
| 总包同步 | 6 个改动的入包文档 + `docs/audit/v050-desktop.md` + 本文 → 包内同字节 → 清单重算 23 条 → zip 重打 **ZIP_STORED 24 条目** → `release/SHA256SUMS-release.txt` 同步（**0.4.5 行逐字未变**） |
| 二进制 | **未动**：Setup 151,838,192 B / `37cf6963…0395d`、portable 137,883,824 B / `8bdc55a0…fdd52`、tar 64,029,696 B / `d93d1803…03ab9` 逐条复算命中（§6.4） |
| 回归 | 全量套件 `python -m unittest discover -s tests -p "test_*.py"` 第三次跑：**`Ran 954 tests` / `OK (skipped=3)` / exit 0**；前两次各红 1 条且是**不同测试**，已定位为既有 flaky（§6.5，含 40 次实测抖动率 7.5%） |

---

## 1. 本轮用的权威口径（哈希不手抄）

| 要读的东西 | 权威文件 |
| --- | --- |
| 两个 0.5.0 exe | `release/SHA256SUMS.txt`（同一对值也在 `desktop/build/dist/SHA256SUMS.txt`、包内清单） |
| 镜像离线 tar | `release/0.5.0/SHA256SUMS.txt`（LF 换行；`sha256sum -c` 直接可用） |
| 镜像 id / 镜像字节 | `release/0.5.0/IMAGE-INFO.json`（`image_id` / `image_size_bytes`） |
| 总包每一条目（含源码便携包、包内文档） | `release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt`（23 条，自身不入清单，UTF-8 无 BOM、CRLF） |
| 总包 zip 自身 | `release/SHA256SUMS-release.txt` **第 2 行**（唯一权威；见 §5 自引用说明） |
| 0.4.5 历史值 | `release/tracelens-0.4.5-release/SHA256SUMS.txt`、`release/0.4.5/SHA256SUMS.txt`、`release/SHA256SUMS-release.txt` 第 1 行 |
| 打包版 sidecar（内部组件，不入清单） | `desktop/build/resources/sidecar/BUILD-INFO.json` |

自签名口径保持不变：`status=UnknownError`（根不受信任）、`signatures_applied=2`、`signed_count=0`、
`unsigned=true`、`trust_state=self_signed_root_untrusted` —— **自签名只证明完整性，不证明信任**；
SmartScreen 说明照旧（"更多信息 → 仍要运行"）。

---

## 2. F1 before → after

| 位置 | 改前（会读成假数据） | 改后 |
| --- | --- | --- |
| `docs/05.4-docker.md` §14 行「**0.5.0 镜像与离线 tar**」 | `❌ 未产出（环境阻塞）`：「0.5.0 的 `tracelens:0.5.0` 与 `release/0.5.0/tracelens-0.5.0-image.tar` 在 2026-11-12 那一轮**没有产出**」 | `✅ 已产出（2026-11-12，完全权限会话收口）`：镜像 `tracelens:0.5.0` = `sha256:6cb8417164f30140ba800cb950f23e0679e6983b8c86f91a803cc0d6cd586ff0`、246,561,040 字节、`LABEL …version=0.5.0`；tar 64,029,696 字节 / `d93d1803c6ecff90aabcca5f76536f6329daa84671da695382fefd5b62803ab9`；`docker-verify.sh`「全部通过」；`docker rmi` 后从包内 tar `docker load` 复算通过；原始输出指 `docs/audit/v050-docker.md` §12 |
| 同一行里的环境边界 | 被写成"没产出的原因" | **保留为环境边界**：成员受限会话打不开 `npipe:////./pipe/dockerDesktopLinuxEngine`（`permission denied`）、`wsl.exe` = `Wsl/ACCESSDENIED`、`bash` 连 signal pipe 都建不出来 ⇒ 构建只能在守护可达的终端跑，**不是"哪都能跑"** |
| `docs/05.4-docker.md` §14「0.5.0 总裁办在 42 项验收里的覆盖」 | 「那一轮改用 source 模式实测补齐」 | 改成实态：收口轮 `docker-verify.sh` 全绿，但 `scripts/verify_e2e.py` 里 `secretariat`/`identities`/总裁办 **0 次出现** ⇒ 42 项断言仍不含总裁办；QA 已在容器内独立实测四条 + 持久性 ⇒ **事实覆盖有、脚本化断言没有**（缺口未修，建议见 `docs/audit/v050-docker.md` §7） |
| `docs/05.4-docker.md` 顶部（第 13 行起） | 无版本时点提示 → §2–§13 的 0.4.5 数字像当前交付指引 | 新增「**版本时点（读前必看）**」：§2–§13 是 **0.4.5 时点**实测（保留作历史快照），0.5.0 的实态数字与权威读法直接给在提示里 |
| `docs/05.4-docker.md` §14「跨版本数据升级（0.4.5 → 下一版）」 | 「现在还没有第二个发布版本」 | 「现在有两个发布版本了（0.4.5 与 0.5.0 目录都在），但**容器卷只做过同版本重建不丢**；0.4.5 卷换 0.5.0 镜像的迁移没跑过，升级前备份 `./data`」 |
| `release/0.5.0/DOCKER-BLOCKED.md` | 全文：「本目录里**没有** 0.5.0 镜像、**没有** tar、**没有** IMAGE-INFO.json」 | 整篇重写为「**已闭合**」：§1 实态表（镜像 id / 246,561,040 / LABEL / tar 64,029,696 / `d93d1803…` / verify 退出码 0 / `docker load` 复算 / 总包 `docker/` 三件 + `DOCKER-PENDING.md` 已删）；§2 保留上一轮阻塞的**原始报错**（管道 / wsl / bash signal pipe）并写明"那是执行环境边界，不是产物缺陷"；§3 「别拿 0.4.5 的 tar 当 0.5.0 用」；§4 只读复算命令 |
| `docs/audit/v050-desktop.md` 验收表（行 15/418「两个 exe 未产出」） | 表前无取代说明（在发布包里会被读成"0.5.0 没安装包"） | 表前插入「**读表前必看（R2-1 追加）**」：两条结论已被同日提权构建取代，给出 0.5.0 两件 exe 的字节与 sha256（逐字取自 `release/SHA256SUMS.txt`），指 §3.2 与 README §12；历史记录与原始报错保留作复现依据 |
| `docs/audit/v050-docker.md` §1–§11「镜像未产出」 | 已由队长在 §12 取代（第 329 行「上面 §1–§11 关于"镜像未产出"的结论已被本节取代」） | **本轮未改**（历史 + 取代说明已在，属验收 1 允许的历史标注） |

---

## 3. F2 before → after（逐条）

`README.md`（QA 列的 24 处，全部改完）：

| 行（改前编号） | 改前 | 改后 |
| --- | --- | --- |
| 39 / 40 | 便携版 / 安装包文件名 `…0.4.5…` | `TraceLens-0.5.0-win-x64-portable.exe` / `…-Setup.exe` |
| 68 | 便携版步骤里的 0.4.5 文件名 | 0.5.0 文件名 |
| 88 / 90 | ASCII 路径示例 `E:\TraceLens-0.4.5\`；「交付目录已附一份 ASCII 副本：`E:\TraceLens-0.4.5\`」 | 示例改 `E:\TraceLens-0.5.0\`；**删掉"已附 ASCII 副本"这句**（本机只有 0.4.5 时点那份 `E:\TraceLens-0.4.5\`，0.5.0 没有；改成给用户的动作指令，并标 `/S` 退出码 0 属 0.4.5 时点） |
| 94 / 95 / 96 | 文件 `release/TraceLens-0.4.5-Setup.exe`；151,535,616 / `78313951…` | `release/TraceLens-0.5.0-win-x64-Setup.exe`；**151,838,192 / `37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d`**（写明逐字取自 `release/SHA256SUMS.txt`）；`/S` 命令换名 |
| 100 | 「本机实测：静默安装 `/S` 退出码 0（队长会话实测）」 | 如实拆成两句：哈希/字节/签名状态逐条复算通过；**0.5.0 的 `/S` 安装未实测**（安装目录属 DSH 服务账号，指 `docs/audit/v050-desktop.md` 未做项），`/S` = 0 是 0.4.5 时点记录 |
| 119 / 120 | portable 137,607,264 / `6394bf6cf5fb1474`；src zip 3,483,619 / `c8aa59068f61b2f4` | portable **137,883,824 / `8bdc55a089dd678d`**；src zip **4,957,816 / `098c3bcd472842c1`**（权威 `SHA256SUMS-bundle.txt` 的 `source/…` 行），并注明 `start.cmd` 已带 `--features` |
| 150 / 155 | `docker load -i release/0.4.5/…tar`；`tracelens:0.4.5` | `release/0.5.0/tracelens-0.5.0-image.tar` + `sha256sum -c release/0.5.0/SHA256SUMS.txt`；`tracelens:0.5.0`，命令补 `--features /app/var/features` 并加"总裁办账本落点"说明 |
| 317 | ASCII 示例 `E:\TraceLens-0.4.5\` | `E:\TraceLens-0.5.0\` + 标出实测时点 |
| 325 / 326 / 329 / 330 / 331 | 交付清单全是 0.4.5 产物与旧哈希；`unsigned=false`、`signed_count=2` | 全表换 0.5.0：Setup / portable / 证书 / 校验件（205 / 4,918）/ 源码便携包 / sidecar（10,403,499 / `f00527ca2c9ac567`，`BUILD-INFO.json`）/ 镜像 tar（64,029,696 / `d93d1803c6ecff90` + 镜像 246,561,040）/ 编排指 `v050-docker.md` §12；签名口径换 `signatures_applied=2`、`signed_count=0`、`unsigned=true`、`trust_state=self_signed_root_untrusted` |
| 332 | 「16 门验收全过」 | 改成 `scripts/docker-verify.sh` 结论「全部通过」并指 §12 |
| 342 / 343 / 346 | 校验命令里的文件名 0.4.5 | 0.5.0 文件名（`certutil` / `Get-FileHash` / `Get-AuthenticodeSignature`），签名注释改成"签名落了、根不受信任，不是没签" |
| 350–354 | `cd release && tr -d '\r' …`（"已实测 3 个文件"）；`cd release/0.4.5 && sha256sum -c` | 保留 CRLF 去 `\r` 的口径，第二条命令换成 `cd release/0.5.0`（0.5.0 tar 命中）；新增包内清单校验行 |
| 356 / 357 | 「总包已产出：`release/tracelens-0.4.5-release.zip`（356,073,874 B，SHA256 `ef837389db3b…`）」 | **0.5.0 总包段**（内容清单 + ZIP_STORED + 条目集合==目录文件集合 + 权威哈希只写 `release/SHA256SUMS-release.txt` 第 2 行，**故意不写自引用数字**）+ **0.4.5 历史快照段**：实测 356,074,424 B / `0c5d5552aa78…`，并写明旧值两个数字都错、已按实测更正；0.4.5 单件哈希逐条实测为真后保留（Setup 151,535,616 / `78313951…`、portable 137,607,264 / `6394bf6c…`、src zip 3,483,619 / `c8aa5906…`、tar 63,299,584 / `6a738832…`） |
| 406 / 407 | 英文段落离线命令 0.4.5 | 0.5.0 路径 + `sha256sum -c` 行 |

其余四份文档：

| 文件 | 改前 | 改后 |
| --- | --- | --- |
| `docs/06-faq.md:36`（A5 静默安装） | `.\TraceLens-0.4.5-win-x64-Setup.exe /S`（本机实测退出码 0） | `.\TraceLens-0.5.0-win-x64-Setup.exe /S` + 如实注明 `/S`=0 属 0.4.5 时点、0.5.0 未做安装实测、装前放纯 ASCII 路径 |
| `docs/06-faq.md:202`（G6 离线安装） | `docker load -i release/0.4.5/tracelens-0.4.5-image.tar` | 0.5.0 tar（64,029,696 字节、校验命令、镜像 `tracelens:0.5.0` = `sha256:6cb84171…`）+ `--network none` 实测指向 §12 + 「别拿 0.4.5 的 tar 当 0.5.0 用」 |
| `docs/06-faq.md:196`（G5 数据与备份） | 只列 `records.jsonl`/`audit.jsonl`/`settings.json`/`auth/`/`ingest/` | 补 **5.0 总裁办账本 `features/`**（`secretariat/identities.jsonl`、`keyring.json` 权限 `0600`、`delegations.jsonl`、`letters.jsonl`、`evidence/`、`rollback/`）+ 为什么显式 `--features`（默认不跟随 `--data-dir`） |
| `docs/06-user-guide.md:31/32` | 两个 0.4.5 文件名 | 0.5.0 文件名 + "路径里别带中文" |
| `docs/NOTICE-third-party.md:35–38` | 交付形态表写 0.4.5 文件名与 `tracelens:0.4.5` | 0.5.0 文件名 + `tracelens:0.5.0` |
| `docs/NOTICE-third-party.md:65/68–70` | `docker run --rm tracelens:0.4.5 …`；BUILD-INFO 路径写成 `…/agent-explainer-sidecar/BUILD-INFO.json` | 命令换 `tracelens:0.5.0`；BUILD-INFO 路径改成实际存在的 `desktop/build/resources/sidecar/BUILD-INFO.json`（复现命令要能跑） |
| `docs/05-real-user-kit.md:57/59/61` | 「16 门验收全过」「已按 0.4.5 产物改写」 | 0.5.0 镜像与离线 tar 已产出 + `docker-verify.sh` 全过；新增「0.5.0 追记（2026-11-12）」段（三条路径的 0.5.0 产物、签名如实口径、哈希只从三份清单读） |
| `docs/05-real-user-kit.md:88` | 预期 `version=0.4.5` | `version=0.5.0` |
| `docs/05-real-user-kit.md:106/112/115/117/127/131` | 0.4.5 文件名 + 期望哈希 `78313951…` | 0.5.0 文件名（含"0.5.0 交付文件名"清单）+ 期望哈希 `37cf6963…`（注明逐字取自 `release/SHA256SUMS.txt`）+ `UnknownError` 口径 + ASCII 路径提示 |
| `docs/05.3-release-checklist.md`（QA 未点名，顺带修） | §10.1/§10.2 的 0.4.5 交付表无时点标注；§10.3「0.4.5 总包仍未产出」 | §10.1 表前加"0.4.5 时点，0.5.0 见 §12"的口径框；§10.3 那条加现状更新（两只总包都已产出，附 0.4.5 zip 实测字节与 sha256）；**新增 §12「0.5.0 真实产物清单」**（8 行产物 + 权威读法 + 0.5.0 未做项） |
| `docs/03-architecture.md:679`（QA 未点名，顺带修） | 「本机现状：签名**未产出**」 | 标成 **0.4.5 时点**（5.3/t12、5.3b/t26 的复现依据保留）+ 新增现状段：0.5.0 已真签名（两个 exe 各一次、`UnknownError`、`signatures_applied=2`、`signed_count=0`） |

---

## 4. 那组错数字（README:356）

| 项 | 旧文档 | 实测权威 |
| --- | --- | --- |
| 0.4.5 总包字节 | `356,073,874 B` | **356,074,424 B** |
| 0.4.5 总包 sha256 | `ef837389db3b…` | **`0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab`** |

权威来源：`release/SHA256SUMS-release.txt` 第 1 行（QA 独立复测同值）。README 现文保留旧值一次，
**写在「本文旧版写的 …两个数字都是错的」这句里**，不能当数据读（验收 1 逐条点名见 §7）。
0.4.5 单件的哈希逐条实测（`certutil`/`Get-FileHash` 同源算法，脚本 §6.3）为真 → 作为老包核对保留。

---

## 5. 总包同步链（三轮重建的留痕）

| 轮 | 触发 | zip 条目 | zip 字节 | 该轮打印的 zip sha256 |
| --- | --- | --- | --- | --- |
| 起点（t5 收口） | 队长回补 docker 三件 | 24 | 358,989,166 | `66a50950f42228417dbc7128662ff018a5f697662600d356bbc46e22d6cc0ee4`（QA 报告引用） |
| 本轮 R1 | F2 的 6 份入包文档同步 | 24 | 359,001,409 | `71232f5f2e57468c5a4d8d9495ed514e060b1b2a80a86c9a088347f74fb2a089` |
| 本轮 R2 | F1 的 `v050-desktop.md` + 05.3 补标注 | 24 | 359,002,611 | `08a61812102cecbaaeef103b883aac8ff72260ad529d1eb54009d378fa0b8b58` |
| **本轮 R3（终）** | 本文入包 | 25（= 目录 25 个文件） | 见 `release/SHA256SUMS-release.txt` 第 2 行 | **只在该清单第 2 行** |
| R4（t10 收口，**已被取代**） | D2 把 `v050-tests-tools.md` 入包 + D3/D4/D5 文档同步 | 26（= 目录 26 个文件） | 359,060,132 | `3257f7e12b7b3f375d47ad61136e7e764c4f325fd5bce41963c1b329e99d9990`（该轮打印值，历史） |
| R5（队长最终收口，**已被取代**） | 第 8 份 audit `v050-r3-closeout.md` 入包 + LICENSE/CHANGELOG + 撤销越界刷入 | **27**（= 目录 27 个文件） | **359,090,440** | `7bd4997507207217006aa61735436b49954aa53d8c309651ed1bc875b7e7ddd4`（该轮打印值，历史；QA t11 按此态复算 26 条清单全过） |
| R6（t12 本轮） | D7/D8 入档：本文时点标记 + `docs/18` §9.1 命令重写 + README 限制 12 | 27（= 目录 27 个文件） | 见 `release/SHA256SUMS-release.txt` 第 2 行 | **只在该清单第 2 行**（自引用不可达） |

> **R3/R4/R5 的定性（QA t11 的 D7）**：上面 R3 行「25 条 / 目录 25 个文件」与本文件 §6.2 的「24 条（R3 之后为 25 条）」
> 都是**该轮时点的历史值**，**已被 R5 的 27 条目 / 359,090,440 B 取代**（包内 `docs/audit/` 从 7 份变 **8 份**）。
> 判定"当前总包是哪一只"只认 `release/SHA256SUMS-release.txt` 第 2 行 + `SHA256SUMS-bundle.txt` 逐条复算；
> 最终态与哈希链全表见 `docs/audit/v050-r3-closeout.md` §14。

**自引用口径**：本文（和 README 一样）在 zip 里面，所以 zip 自己的哈希**不可能**写在正文里成立——
每写一次就多一次重建、哈希就再变一次。因此文档一律只写「权威值见 `release/SHA256SUMS-release.txt` 第 2 行」，
上面表里前几轮的值标成"该轮打印值"（历史），末轮故意不写。写法沿用 `docs/audit/v050-docker.md` §12.5。
**补记**：本文每自更新一次（补 §7.1、§8、§6.5 等）就会再重打一轮（R4…），条目数保持 25；
判定"当前总包是哪一只"只认 `release/SHA256SUMS-release.txt` 第 2 行 + `SHA256SUMS-bundle.txt` 逐条复算，
不要拿本文任何表里的历史值当验收依据。
同步链每一步都可复算：仓库 → 包内**同字节**（`Get-FileHash` 两侧逐字相同）→ 清单重算（23 条，自身不入清单，
UTF-8 无 BOM、CRLF）→ zip `ZIP_STORED` → `release/SHA256SUMS-release.txt`（0.4.5 行逐字未变）。
工具：`.tmp/v050-bundle-repack.py`（队长在 t5 写的，幂等）+ 本轮新增只读复算 `.tmp/t7-verify.py`。

---

## 6. 复算原文（本轮实测输出）

### 6.1 四份清单逐条复算

```
== 1. 四份清单逐条复算 ==
  -> release/SHA256SUMS.txt: 2/2 条命中
  -> release/0.5.0/SHA256SUMS.txt: 1/1 条命中
  -> desktop/build/dist/SHA256SUMS.txt: 2/2 条命中
  -> release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt: 23/23 条命中
```

逐条明细（节选，全 28 条 `[OK]`）：

```
[OK] release/SHA256SUMS.txt :: TraceLens-0.5.0-win-x64-Setup.exe  (151,838,192 B)
[OK] release/SHA256SUMS.txt :: TraceLens-0.5.0-win-x64-portable.exe  (137,883,824 B)
[OK] release/0.5.0/SHA256SUMS.txt :: tracelens-0.5.0-image.tar  (64,029,696 B)
[OK] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt :: README.md  (31,243 B)
[OK] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt :: docs/05.4-docker.md  (38,141 B)
[OK] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt :: docker/tracelens-0.5.0-image.tar  (64,029,696 B)
[OK] release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt :: source/TraceLens-0.5.0-win-x64-portable-src.zip  (4,957,816 B)
```

编码口径：四份清单都 `[OK] 无 BOM`、`[OK] 无 U+FFFD`；`release/SHA256SUMS.txt`、
`desktop/build/dist/SHA256SUMS.txt`、`SHA256SUMS-bundle.txt` 是 CRLF；
`release/0.5.0/SHA256SUMS.txt` 是 **LF**（`scripts/docker-build.sh` 写的，不在本轮写作用域，
`sha256sum -c` 对 LF 直接可用——README 里"先 `tr -d '\r'`"那条只针对 CRLF 清单）。

### 6.2 zip 条目集合

```
[OK]   条目集合 == 目录文件集合（24 条）      # R3 之后为 25 条（本文入包）
[OK]   全部 ZIP_STORED（compress_type=0）
  zip 字节 = 359,002,611（权威 sha256 见 release/SHA256SUMS-release.txt 第 2 行）
```

> **时点标记（QA t11 的 D7，本轮 t12 补）**：§6.1 的「23/23 条命中」与本段的「24 条 / 25 条」都是
> **R1–R3 时点的历史值**，**已被最终态取代**：包内文件 **27 个**、`SHA256SUMS-bundle.txt` **26 条**、
> zip **27 条目**、R5 那次 **359,090,440 B**（R6 本轮字节与哈希只认 `release/SHA256SUMS-release.txt` 第 2 行）。
> 全链见 `docs/audit/v050-r3-closeout.md` §14；本文件保留原文是为了不改史。

### 6.3 文档里的每个 sha256 / 字节数都命中权威值

```
== 3. 文档里的每个 sha256 片段都能前缀命中权威摘要 ==
  [OK]   README.md: 14 个摘要片段全部命中权威值
  [OK]   docs/05.3-release-checklist.md: 13 个摘要片段全部命中权威值
  [OK]   docs/05.4-docker.md: 3 个摘要片段全部命中权威值
  [OK]   docs/06-faq.md / 06-user-guide.md / NOTICE-third-party.md: 0 个（不含哈希，合规）
  [OK]   docs/05-real-user-kit.md: 1 个摘要片段全部命中权威值
  [OK]   release/0.5.0/DOCKER-BLOCKED.md: 1 个摘要片段全部命中权威值
```

字节数逐条对真实大小（节选，全部 `[OK]`）：

```
[OK] 151,838,192 B == release/TraceLens-0.5.0-win-x64-Setup.exe 的真实大小
[OK] 137,883,824 B == release/TraceLens-0.5.0-win-x64-portable.exe 的真实大小
[OK] 4,957,816 B  == desktop/build/dist/TraceLens-0.5.0-win-x64-portable-src.zip
[OK] 64,029,696 B == release/0.5.0/tracelens-0.5.0-image.tar
[OK] 246,561,040 B == IMAGE-INFO.json 的 image_size_bytes
[OK] 10,403,499 B == 打包版 sidecar exe
[OK] 356,074,424 B == release/tracelens-0.4.5-release.zip
[OK] 151,535,616 / 137,607,264 / 3,483,619 / 63,299,584 B == 0.4.5 四件的真实大小
[OK] README.md 里 356,073,874 只作为"旧值已错"的记录出现
[OK] README.md 里 ef837389 只作为"旧值已错"的记录出现
```

### 6.4 二进制未动（验收 4）

```
[OK] release/TraceLens-0.5.0-win-x64-Setup.exe 未被改动（151,838,192 B）
     sha256 = 37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d
[OK] release/TraceLens-0.5.0-win-x64-portable.exe 未被改动（137,883,824 B）
     sha256 = 8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52
[OK] release/0.5.0/tracelens-0.5.0-image.tar 64,029,696 B = d93d1803c6ecff90aabcca5f76536f6329daa84671da695382fefd5b62803ab9（清单命中）
```

### 6.5 全量套件（验收 5）

命令：`python -m unittest discover -s tests -p "test_*.py"`，在文档与总包全部落盘之后跑：

| 次 | 日志时刻 | 结果 |
| --- | --- | --- |
| R1 | 04:58:25 | `Ran 954 tests in 197.737s` / `FAILED (errors=1, skipped=3)` / exit 1 — `test_settings_api.SettingsApiLocalTests.test_method_contract` → `ConnectionAbortedError [WinError 10053]`（本机回环连接被中止） |
| R2 | 05:04:03 | `Ran 954 tests in 192.197s` / `FAILED (failures=1, skipped=3)` / exit 1 — `test_crypto_sig.TestPayloadAndBase64.test_public_key_b64_and_verify_b64` → `AssertionError: True is not false`（`tests/test_crypto_sig.py:367`） |
| **R3** | **05:06:57** | **`Ran 954 tests in 165.246s` / `OK (skipped=3)` / exit 0** ✅（与 QA t6 记的基线同形） |

**两次红的是"单条抖动"，且不是同一条测试**；本轮**零 Python 改动**（只改文档、包内文档副本、清单与 zip），
不可能影响这两条：

- `test_settings_api`：本机 socket 栈中止已建立的连接（10053），环境性抖动。
- `test_crypto_sig.py:367` 是**测试设计导致的概率抖动**：它把签名最后一字节改写成 `0x00` 当作"篡改后的签名"，
  而 Ed25519 签名里 `s` 的最高字节本来就是 `0x00` 的概率约 1/16 —— 那一次"篡改"是**空操作**，
  于是"篡改签名应验不过"的断言对着**真签名**求值 ⇒ `True is not false`。
  单独把这一条跑 40 次实测：**失败 3 次（7.5%）**，与上面的概率吻合 ⇒ 判定为既有 flaky，非本轮回归。
  修法建议（`tests/**` 不在 t7 写作用域，**未改**）：把篡改改成保证改变字节的写法，例如
  `signature[0] ^ 0x01`，或断言"篡改任意一字节都应验不过"时先断言该字节确实变了。
- 日志：`.tmp/t7-full-suite.log` / `.tmp/t7-full-suite2.log` / `.tmp/t7-full-suite3.log`
  （PS 5.1 的 `*>` 重定向是 UTF-16，读要 `-Encoding Unicode`）。

---

## 7. 验收 1 的 grep 命中逐条点名

命令（本轮用等价 python 全量扫过，57 条命中 / 58 个文件）：
`grep -rn "未产出\|DOCKER-BLOCKED\|356,073,874\|ef837389" README.md docs release/tracelens-0.5.0-release`

| 命中 | 判定 |
| --- | --- |
| `README.md:318`（+ 包内副本）「单文件 sidecar exe 未产出」 | **真实现状**（onefile 本机引导失败），非假数据 |
| `README.md:391`（+ 包内）文档索引描述"含未产出项与原始报错" | 描述性文字，合规 |
| `README.md:371`（+ 包内）`356,073,874` / `ef837389` | **本轮新写的"旧值已错"记录**：同段给出实测 356,074,424 / `0c5d5552…`，句子明确写「两个数字都是错的」→ 不能当数据读（逐条点名在此） |
| `CHANGELOG.md:146`（+ 包内）「单文件 sidecar exe 未产出」 | 0.4.5/0.5.0 的已知限制，真实 |
| `docs/03-architecture.md:558/564` 单文件 exe 未产出 | 真实；`:679` 签名未产出已标 **0.4.5 时点** + 加 0.5.0 已签名现状段（§3 表末行） |
| `docs/05.3-release-checklist.md:3/9/14/20/21/22/137/220/255/276/315/316` | 全部属 §1/§9/§10 的 5.3/5.3b/5.5 历史记录，**已有"那是当时结论"的框**（行 276）+ 本轮新增 §10.1 版本口径框 + §10.3 现状更新 + §12 的 0.5.0 清单 |
| `docs/05.4-docker.md:3`（口径句）/`:467`（已产出） | 口径句合规；467 是本轮改成的实态行 |
| `docs/audit/v050-desktop.md:15/418` | 本会话那一轮的结论，**表前已加"读表前必看"取代说明**（§2 表末行） |
| `docs/audit/v050-docker.md:297/310/329` | 第 329 行已写「§1–§11 关于"镜像未产出"的结论已被 §12 取代」→ 历史标注在位 |
| `docs/audit/v050-version.md:251` | 纪律条款（"跑不起来就写未产出，不得谎报"），合规 |
| `docs/17-release-0.5.0-acceptance.md:24/212/620/625/626/678/679` | QA 自己的缺陷记录（引用被缺陷文字），合规 |
| 包内 `docs/05.3` / `docs/05.4` / `CHANGELOG` / `README` 的同名行 | 与仓库侧同字节，判定同上 |

**F1 的两处（QA 点名的 `docs/05.4-docker.md:459` 与 `release/0.5.0/DOCKER-BLOCKED.md`）已无"未产出"字样**：
前者是 `✅ 已产出` + 实测数字；后者首行 `# 0.5.0 镜像离线包 —— **已闭合**`。

### 7.1 终态的分文件命中数（统计时刻 2026-11-12 05:10，共 114 条 / 60 个文件）

> 这个总数只对该时刻有效：本文每自更新一次，命中数就 +2（仓库侧 + 包内副本各一条）。
> 判定按下面的分文件表逐类看，不按总数看。

| 命中数 | 文件 | 判定 |
| --- | --- | --- |
| 27 + 27 | `docs/audit/v050-docs-sync.md` + 包内副本 | **本文自己**：§2/§3/§7 的 before→after 对照必须引用改前原句 ⇒ 全部是"点名"用途，同段都跟着实测真值 |
| 12 + 12 | `docs/05.3-release-checklist.md` + 包内副本 | 5.3/5.3b/5.5 历史行；已有时点框（行 276）+ 本轮 §10.1 口径框 + §10.3 现状更新 + §12 的 0.5.0 清单 |
| 7 | `docs/17-release-0.5.0-acceptance.md` | QA 缺陷记录本体（引用被缺陷文字），合规 |
| 4 | `docs/03-architecture.md` | 2 条单文件 exe（真实限制）+ 1 条口径句 + 1 条本轮新写的"0.4.5 时点"标注 |
| 3 + 3 | `README.md` + 包内副本 | 单文件 sidecar（真实）+ 文档索引描述 + "旧值已错"记录 |
| 3 + 3 / 3 + 3 | `docs/audit/v050-desktop.md`、`docs/audit/v050-docker.md` + 包内副本 | 前几轮实录，**已各带取代说明**（desktop 表前框 / docker §12 行 329） |
| 2 + 2 | `docs/05.4-docker.md` + 包内副本 | 口径句（"跑不动就写未产出"）+ 本轮改成的 `✅ 已产出` 行 |
| 1 + 1 / 1 | `docs/audit/v050-version.md` ×2、`CHANGELOG.md`（包内） | 纪律条款与已知限制，合规 |

**零命中的交付指引文件**：`docs/06-faq.md`、`docs/06-user-guide.md`、`docs/NOTICE-third-party.md`、
`docs/05-real-user-kit.md`、`docs/06-feedback-form.md`、`release/0.5.0/` 三件套（tar / `SHA256SUMS.txt` / `IMAGE-INFO.json`）。
增量说明：起点（F1/F2 修完、本文未写）57 条 → 终态 114 条，**增量全部来自"点名"文档本身**
（本文 54 条，仓库侧 + 包内副本各一半）与 `docs/03-architecture.md` 新增的那条"0.4.5 时点"标注（+1）；
被点名的原句之所以保留，是因为 F1/F2 的修法要求写明"改前是什么、实测真值是什么"。

---

## 8. 保留的历史值与"不可复算"清单（复算器的显式白名单）

复算器 `.tmp/t7-verify.py` 分两类：**本轮改过的交付指引文档**（必须 100% 命中当前权威清单，见 §6.3）与
**前几轮的 audit 实录**（`v050-desktop/docker/release/sidecar/version`、`03-architecture`、`CHANGELOG`；
允许出现"当轮中间产物 / 链内部哈希"，只报 `[HIST]` 不计失败）。实录类共 23 条这类值
（desktop 6 / docker 7 / release 5 / sidecar 5），逐类点名如下：

| 值 | 出处 | 为什么不再命中当前文件 |
| --- | --- | --- |
| `22f78162d42a7d36…`（10,155,966 B） | `docs/05.3-release-checklist.md` §9.5 / §10.1 | 0.4.5 时点的 sidecar exe；工作树里那份已被 0.5.0 构建替换（现为 10,403,499 B / `f00527ca2c9ac567…`） |
| `ead2058ee0d3e472…`（38,976,123 B） | 同文件 §9.5 表 | 0.4.5 时点的 onefile 证据件，t2 已删除（留着会被 `SIDECAR_CANDIDATES` 优先选中且跑不起来） |
| `51d5b69752ce…b0b4b1`（243,566,055 B） | `docs/05.4-docker.md` §4/§5 | 0.4.5 镜像 id（历史行；本文顶部已标 §2–§13 属 0.4.5 时点） |
| `a234cd8e…`（Setup 151,836,755）、`d14088d5…`（portable 137,882,390）、`09eb4289…`（src zip 4,937,446） | `v050-desktop.md` §3.2/§5、`v050-release.md` §2 | **t3/t4 当轮**的字节与哈希；同轮 F1/F2 修复重新盖章后刷新成终值 151,838,192 / 137,883,824 / 4,957,816（终值权威在 `release/SHA256SUMS.txt` 与包内清单） |
| `9b31479c…`（159,964 B） | `v050-desktop.md` §5 | `Setup.exe.blockmap`：electron-builder 副产物，t4 起签名脚本按口径过滤 `.blockmap/.yml/.log/.7z/.nupkg`，不是交付件、不入清单 |
| `f00fbbf4…`、`136c1d28…`、`0830f61b…`、`66a50950…`、`71232f5f…`、`08a61812…` | `v050-release.md` 行 210、`v050-docker.md` 行 282/415、本文 §5 | 各轮**重建脚本打印**的总包 zip 值（逐轮失效）；每版终值只在该版 `release/SHA256SUMS-release.txt` 的行内 |
| `ad522fb8f4d3f777…`、`6194d3f4…`、`e7f31a4f…`、`f7786807ec9484a0268c…`、`e006d4c9…`、`7d618ce32b19dc87`、`0841ac59df6816ce`、`809dea78…`、`fb41ab5d…`、`0000000…0000` | `v050-docker.md` §6/§12.4、`v050-sidecar.md` §4/§5、`v050-desktop.md` §4 | 密码学链**内部哈希**（`entry_hash` / `head` / `prev_hash`（创世全零占位）/ 身份 `fingerprint` / `boot_params_hash` / 样本 `keyring.json` 的 sha256）：随样本账本生成，落在 `.tmp/**` 样本目录，不是文件摘要、不作交付校验 |
| `3257f7e12b7b3f37…9990`（26 条目 / 359,060,132 B）、`7bd4997507207217…ddd4`（27 条目 / 359,090,440 B） | 本文 §5 的 R4 / R5 行、`docs/audit/v050-r3-closeout.md` §14.1 | **t10 收口**与**队长最终收口**那两轮重建脚本打印的总包 zip 值：每重打一次就失效（zip 条目里带文件 mtime，两次幂等重打的哈希都不相同），已标「已被取代」；现行权威只认 `release/SHA256SUMS-release.txt` 第 2 行 |
| `<redacted-thumbprint>` | 多处 | 证书**指纹**，不是文件摘要 |
| `6cb8417164f30140ba800cb950f23e0679e6983b8c86f91a803cc0d6cd586ff0` | 多处 | 镜像 **config digest**，权威在 `release/0.5.0/IMAGE-INFO.json`（已在权威集合内） |

**本轮改过的交付指引文档（README / 05.3 / 05.4 / 06-faq / 06-user-guide / NOTICE / 05-real-user-kit /
`release/0.5.0/DOCKER-BLOCKED.md` / 本文）里没有这类"不可复算值"**：复算器对它们要求 100% 命中，全部 `[OK]`（§6.3）。


---

## 9. 本轮未做 / 建议（不在 C2 范围）

1. `scripts/verify_e2e.py` 的总裁办断言补丁（`docs/audit/v050-docker.md` §7 已给建议）——文件不在本轮写作用域。
2. `docs/NOTICE-third-party.md` 的"构建脚本自动生成"仍缺（05.3 §10.3 那条现状更新里保留了）。
3. `release/0.4.5/` 与 `release/tracelens-0.4.5-release/` 一字未动（QA F2 只要求"标成历史 + 修错数字"，已做到；
   历史包内的 0.4.5 文档是当时快照，不回改——回改会让历史不可信）。
4. 本机 ASCII 交付副本 `E:\TraceLens-0.5.0\` **没有创建**：写工作区外目录不在沙箱允许范围，README 已改成给用户的动作指令。
5. `docs/05.4-docker.md` §2–§13 的 0.4.5 数字（构建耗时、镜像 243,566,055、tar 63,299,584 等）保留为历史，
   由顶部"版本时点"框限定；若要出 0.5.0 版全文实测记录，需要守护可达的会话再跑一遍 `docker-verify.sh` 的完整叙述。

## 10. 环境与副作用

- 改动文件（本轮写作用域内）：`README.md`、`docs/03-architecture.md`、`docs/05.3-release-checklist.md`、
  `docs/05.4-docker.md`、`docs/06-faq.md`、`docs/06-user-guide.md`、`docs/NOTICE-third-party.md`、
  `docs/05-real-user-kit.md`、`docs/audit/v050-desktop.md`、`release/0.5.0/DOCKER-BLOCKED.md`、
  `docs/audit/v050-docs-sync.md`（本文）、包内 8 份文档副本 + `SHA256SUMS-bundle.txt` +
  `release/tracelens-0.5.0-release.zip` + `release/SHA256SUMS-release.txt`。
- 未动：`agent_explainer/**`、`web/**`、`tests/**`、`desktop/**` 源码与产物、两个已签 exe（§6.4 复算）、
  0.4.5 全部产物、`.agent-teams/**`。
- 工具脚本（`.tmp/`，非交付件）：`.tmp/t7-probe.py`、`.tmp/t7-read.py`、`.tmp/t7-verify.py`、
  `.tmp/t7-grep.py`、复用 `.tmp/v050-bundle-repack.py`；套件日志 `.tmp/t7-full-suite.log`（PS 5.1 的 `*>` 是 UTF-16，读要 `-Encoding Unicode`）。

## 11. D4 入档：打包 exe 的命令行开关，两组观测并列登记（R3-1，2026-11-12）

QA 在 R2-3（`docs/18-release-0.5.0-followup.md` D4，行 478-479 / 604）判「C1 指令里『`--port`/`--user-data-dir`
会 `bad option` 直接退出』与本机实测不符」。本文（t7 那份口径记录）当时把队长 t3 的观感写进了
`v050-desktop.md:254`，语气是断言式的。**结论：两组观测都登记、不合并成一个结论**，C1 的验收命令一律走环境变量。

| # | 观测（原样） | 来源与时点 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- | --- |
| A | 直接控制台运行打包 exe 带壳级开关时，控制台出现 **`TraceLens.exe: bad option: --user-data-dir`** 这类文本 | 队长 t3 会话（收口时口述，被 t7 写进 `v050-desktop.md:254`，写成"实测 `--port`、`--user-data-dir` 都是"） | 至少 `--user-data-dir` 这一次运行**有可见的 `bad option` 文本输出** | 没有留原始输出与退出码；`--port` 是否同样有该文本，在 A 里只是转述 |
| B | `Start-Process TraceLens-0.5.0-win-x64-portable.exe -ArgumentList '--port','18802' -Wait` → **exit=9、stdout/stderr 全 0 字节、无 `bad option` 字样**；`--user-data-dir`、`--selfcheck`、`--no-sandbox` 同样 exit=9/0 B；**无参与 `--version` 都 exit=0** | QA R2-3 会话（`18-release-0.5.0-followup.md:604`，带复现命令） | 那台机器上带这些开关的运行**确实是 exit=9 且零输出**，`bad option` 文本没出现 | QA 自己写明「成因（参数解析 vs 无桌面会话）在其会话不可判」→ **不能**据此断言"开关被参数解析层拒掉" |
| C | 本机**无参数**跑 GUI 也起不来：0.1 秒退出、退出码 `4294930435`（`0xffff7003`），stderr 只有一行 crashpad `not connected`；`Start-Process` 拉 GUI 报 `Access is denied`（受限沙箱不允许开命名管道） | 本文 §1 表第 10 行 + `docs/05.3-release-checklist.md` §1/§9（0.4.5 时点，0.5.0 未窗口级实测） | "零输出退出"在本机**并非只有参数一条路径**；无参数也会零输出 | 退出码与 B 的 9 不同 → C 不能直接解释 B；只能说明"零输出不足以归因" |

**代码事实（读源码，不是观测）**：`desktop/main.js` 第 44-51 行的 `FLAGS` 解析 `--selfcheck-out /
--data-dir / --features-dir / --python / --mode / --demo / --port`，第 29 行用
`process.argv.includes('--selfcheck')` 认 `--selfcheck`；第 59-77、252-256 行的环境变量是
**`TRACELENS_DATA_DIR`、`TRACELENS_FEATURES_DIR`、`TRACELENS_MODE`、`TRACELENS_DEMO`、
`TRACELENS_PORT`、`TRACELENS_PYTHON`、`TRACELENS_SELFCHECK`**。`--user-data-dir` 与 `--no-sandbox`
**不在** `main.js` 的开关表里（是 Chromium 层开关）。

**两组观测的差异点（不合并）**：A 有文本、B 零输出；A 的样本是 `--user-data-dir`（不在 main.js 开关表内），
B 的样本覆盖 `--port`/`--selfcheck`（在 main.js 开关表内）；A 无退出码记录，B 无文本记录。
⇒ 二者不构成同一事实的重复，也不能互相推翻。

**C1 指引口径（本轮定死的写法）**：验收命令**一律走环境变量**
`TRACELENS_MODE` / `TRACELENS_PORT` / `TRACELENS_DATA_DIR` / `TRACELENS_FEATURES_DIR`
（+ 无人值守用 `TRACELENS_SELFCHECK=1`），**不得**在 C1 里写"带 `--port` 会 `bad option` 退出"这类
因果断言；`--selfcheck` 这类壳级开关列为**桌面会话内的复核项**（桌面会话里若仍 exit=9 → 另立缺陷）。
`v050-desktop.md:254` 那句话的断言语气由本轮（R3-1）以本节为准收敛，原文按逐字保留不改史。
