# v050-docker 实录（t5 / T4：0.5.0 镜像重建 + 离线包 + 容器内实测）

任务：t5（写作用域 `release/0.5.0/**`、`scripts/docker-entrypoint.sh`、`scripts/docker-run.sh`、
`docker-compose.yml`（第 5 条需要时）、`docs/audit/`、`docs/05.4-docker.md`）。
禁止：改 `web/**`、`agent_explainer` 业务代码、重打桌面件。
时间：2026-11-12。执行人：backend（受限沙箱会话）。

---

## 0. 一句话结论（先说没做到的）

**镜像侧六项里，1 / 2 / 3 / 4（镜像内）/ 6 全部未执行**——不是构建失败，是**本会话的 shell 打不开
docker 引擎的命名管道**（Docker Desktop 我拉起来了，管道也建出来了，沙箱拒绝访问）。
所以：没有 `tracelens:0.5.0` 镜像、没有 `tracelens-0.5.0-image.tar`、没有 `IMAGE-INFO.json`、
没有 `verify-fresh.json`/`verify-restart.json`、没有 `docker load` 复算。
**没有拿 0.4.5 的 63 MB tar 顶替，也没有写「已构建」。**

**第 5 条（镜像侧账本落点）做完了**，而且做成了实打实的东西：默认落点用**与容器 command 同口径的
本机实测**量出来了（18/18 全 PASS，含登记身份→重启→计数不减），并据此把
`docker-compose.yml` 与 `scripts/docker-run.sh` 显式钉上 `--features /app/var/features`。
另发现一条会影响 QA 三形态验收的**鉴权口径差异**（team 模式未登录 `GET /api/secretariat/status` = 401，
不是卡片写的 200）与一条**验收覆盖缺口**（42 项里没有总裁办断言）。

---

## 1. 环境实测（逐条可复现，原文照抄）

| # | 探测 | 实测结果 |
| --- | --- | --- |
| 1 | `Get-Command docker` | `C:\Users\Windows\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe` |
| 2 | `docker version` | `Client: Version 29.9.3 / API 1.56 / Go go1.26.10 / OS/Arch windows/amd64 / Context: desktop-linux`；**Server 段一个字段都没有**，exit 1 |
| 3 | `docker info`（Docker Desktop 未运行那次） | `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` |
| 4 | 拉起 Docker Desktop | `C:\Users\Windows\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`（15,719,856 B）→ 新窗口 `Containers - Docker Desktop`（pid 10108）；轮询 `Test-Path '\\.\pipe\dockerDesktopLinuxEngine'` → **True**（未超 150 s） |
| 5 | `docker info --format …` / `docker images`（守护已运行） | **`permission denied while trying to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`**；harness 同时打出 `[sandbox: file access denied under workspace-write mode]`。本会话批准功能关闭，**不申请提权**（也不该申请） |
| 6 | `wsl.exe -l -v` | `Wsl/ACCESSDENIED`（WSL2 后端在受限令牌下不可用；Docker Desktop 的 linux 引擎正是 WSL2 后端） |
| 7 | `D:\hermes\git\usr\bin\bash.exe -n scripts/*.sh` | `fatal error - couldn't create signal pipe, Win32 error 5` → 连 bash 语法检查都起不来（同一条命名管道边界） |

判定：①Docker 装了、②Docker Desktop 能起、③引擎管道存在、④**受限令牌开不了那个管道**。
结论与卡片的环境提醒一致：「docker 守护不可用就如实 FAIL 并贴 `docker info` 报错」。
Docker Desktop 在这台机器上现在是**运行中**（我拉起的，未退出）——用户自己的终端接着跑 §8 的命令即可。

---

## 2. 版本口径核对（0.5.0 已经就位，不需要我改）

| 位置 | 实测值 |
| --- | --- |
| `Dockerfile:62` | `ARG TRACELENS_VERSION=0.5.0` |
| `Dockerfile:77` | `ENV TRACELENS_VERSION="${TRACELENS_VERSION}"` |
| `Dockerfile:65-70` | `org.opencontainers.image.version="${TRACELENS_VERSION}"`、`tracelens.mode="team"`、`tracelens.pdf-export` |
| `docker-compose.yml:24,29` | `image: "tracelens:${TRACELENS_VERSION:-0.5.0}"`、build arg 同值 |
| `scripts/docker-build.sh:35` | 版本默认取 `agent_explainer.server.SERVER_VERSION`（t1 已改 0.5.0），产物落点 `release/${VERSION}` = `release/0.5.0/` |
| 运行侧实测 | 本机 source 模式 `GET /health` → `"version": "0.5.0"`（§4 A 组） |

`t1` 的产物链（sidecar exe `f00527ca…`）与桌面件签名后的字节（`37cf6963…0395d` / `8bdc55a0…fdd52`）
本轮**一个字节都没动**；`web/**`、`agent_explainer/**` 业务代码未动。

---

## 3. 第 5 条的编排修复（改了什么、为什么、怎么验的）

### 3.1 缺陷同源（t2 在桌面侧实测的那条）

`server.py:4826` 定义 `--features`（默认 `None`，帮助文本写明「默认 var/features」），
`server.py:4860` 把它当 `features_root` 传进服务。`agent_explainer/__main__.py:249` 用
`parse_known_args` 把不认识参数收进 `extra`，`:125` 原样转发给 `server.main()`
——所以 `python -m agent_explainer --features DIR` 是**合法**的（`--help` 里看不到它，
`__main__` 的解析器没这个参数，转发链才有）。实测（`--print-argv` 只打印转发后的命令行，不起服务）：

```
# 容器 compose 现状（无 --features）
D:\python\python.exe -m agent_explainer.server --mode team --port 8765 --host 0.0.0.0 \
  --records-store …/var/records.jsonl --audit-log …/var/audit.jsonl --settings … --auth-state … \
  --ingest-tokens … --records …/samples/action_records.json
# 加 --features /app/var/features（末尾原样出现）
… --records …/samples/action_records.json --features /app/var/features      （exit=0）
```

### 3.2 改动（两个文件，均为编排侧）

- `docker-compose.yml`：`command` 末尾加 `"--features", "/app/var/features"`；
  顶部「数据流」清单补 `features/`（5.0 总裁办：identities / keyring / delegations / letters / evidence / rollback）；
  注释写明「默认值是 `<应用根>/var/features` 且**不跟随 `--data-dir`**，显式钉死让不变量与 WORKDIR、`--data-dir` 取值无关」。
- `scripts/docker-run.sh`：`docker run` 的 command 末尾加 `--features /app/var/features`（带同口径注释）；
  收尾提示新增「总裁办：`./data/features` → 容器 `/app/var/features`」+「team 模式 `/api/secretariat/*` 需登录」。
- 未动：`scripts/docker-entrypoint.sh`（它只透传 `"$@"`，无需改）、`Dockerfile`（默认 `ENTRYPOINT` 是规格那条，逐字不动）。

### 3.3 怎么验的（沙箱里能做的都做了）

| 验证 | 结果 |
| --- | --- |
| PyYAML 解析 `docker-compose.yml` 并打印渲染出的 `command` | exit 0：`[…, "--data-dir", "/app/var", "--features", "/app/var/features"]`；`volumes=['./data:/app/var','./logs:/app/logs']`；`user=10001:10001` |
| 编码/行尾（改过的 3 个文件） | `docker-compose.yml` 5,159 B / `scripts/docker-run.sh` 5,761 B / `scripts/docker-entrypoint.sh` 3,654 B：**BOM=False、CRLF=0、U+FFFD=False**（bash 脚本带 BOM 会炸 shebang，带 CRLF 会炸行尾，这条必须绿） |
| `bash -n` 语法检查 | **做不了**：`couldn't create signal pipe, Win32 error 5`（§1 #7）。改动只落在已有引号结构与 heredoc 内，YAML 侧已解析通过；bash 侧请在用户终端跑 `bash -n scripts/docker-run.sh` |
| `docker compose config -q` | **做不了**（同 §1 #5 的管道拒绝） |

---

## 4. 容器 command 口径实测（source 模式，18/18 PASS）

Docker 跑不了，但**容器里那条 command 本身**可以在本机 source 模式跑起来：
`python -m agent_explainer --mode team --host 127.0.0.1 --port 8899 --demo zh --no-browser --data-dir <X> --features <Y>`
（镜像 `WORKDIR /app` + 同一条 command，参数一一对应）。脚本 `agent-explainer/.tmp/t5-docker-sim.py`，
日志 `agent-explainer/.tmp/t5-sim.log`，`sim exit=0`。

### A 组：team 模式 + 显式 `--features`（= compose 的 command 口径）

```
[PASS] /health 200 且 version=0.5.0                version=0.5.0
[PASS] 未登录 GET /api/secretariat/status 如实记录（卡片预期 200）  实测 401
[PASS] team 未登录读身份账本 = 401                 实得 401
[PASS] team 模式管理员登录成功                     POST /api/auth/login 200 + csrf_token
[PASS] 已登录 GET /api/secretariat/status 200 且 enabled=true
[PASS] 登记身份 201                                POST /api/secretariat/identities
[PASS] 登记后 count>=1                             count=1
[PASS] identities.jsonl 落在 --features 目录（=卷内）
[PASS] keyring.json 落在 --features 目录（=卷内）
```

`GET /api/secretariat/status`（已登录）实测摘录（路径字段是产品自己报的，不是我推的）：

```json
"secretariat": { "mode": "team", "enabled": true,
  "counts": {"identities": 0, "active_identities": 0, "delegations": 0, "letters": 0, "signed_records": 0},
  "chains": {
    "identities": {"ok": true, "count": 0, "head": "0000…0000", "problems": [],
                   "path": "…\\.tmp\\v050-docker\\features\\secretariat\\identities.jsonl"},
    "delegations": {… "path": "…\\features\\secretariat\\delegations.jsonl"},
    "letters":     {… "path": "…\\features\\secretariat\\letters.jsonl"}},
  "keyring": {"path": "…\\features\\secretariat\\keyring.json", "exists": false,
              "keys": 0, "writes": 0, "mode": "0o600", "source": "local_file"},
  "signature": {"algorithm": "ed25519", "canonical": "sorted-json", …},
  "degraded": ["local-tsa", "in-process-keys"] }
```

登记后目录里真出现的文件（实测清单）：

```
…\v050-docker\features\secretariat\identities.jsonl        547 B
…\v050-docker\features\secretariat\identities.jsonl.lock     0 B
…\v050-docker\features\secretariat\keyring.json            336 B
…\v050-docker\features\secretariat\letters\                  (目录)
…\v050-docker\features\evidence\files\  …\features\rollback\  (目录)
```

POST 返回体：`{"ok": true, "identity": {"id": "agt_20261111195507_ef592c9e", "agent_id": "agent-t5-sim",
"kind": "agent", "pubkey": "z+1bnvbMAD83hYG79d0cxuoHng2Do14s5l/W1rdz9Hw=", "fingerprint": "7d618ce32b19dc87",
"owner_did": "did:tracelens:user:t5simowner0001", …}}`。
（顺手记一条形状约束：`owner_did` 必须是 `did:tracelens:user|agent:<8-64 位小写字母数字>`，
用 `did:key:…` 会被 `SECRETARIAT_FIELD_INVALID` 挡下——我第一版脚本就是这么被 400 的。）

### B 组：停进程再重启（= 删容器重建、卷保留）

```
[PASS] 重启后 /health 200
[PASS] 重启后管理员登录成功
[PASS] 重启后身份计数不减（>=1）        count=1
[PASS] 重启后 identities.jsonl 仍在卷内
[PASS] 重启后 keyring.json 仍在卷内
[PASS] 重启后哈希链自检无 broken 迹象
```

重启后 status 实测：`counts.identities=1`、`active_identities=1`、
`chains.identities = {"ok": true, "count": 1, "head": "e7f31a4fa28c5afbf4bcf3d12ec4a3e7f6d952d7029c2d923fcb416d156e1b36", "problems": []}`
——链头从创世全零变成真哈希，且 `ok=true`。**这就是「换镜像不冲账本、密钥跟着卷走」的等价证据。**

---

## 5. 默认落点对照（不传 `--features` 时东西落哪儿）

C 组：CWD = 应用根（= 容器里的 `/app`），只给 `--data-dir .tmp/v050-docker/run-c/data`，**不给** `--features`：

```
默认（不传 --features）实测落点：
  E:\…\agent-explainer\var\features\secretariat\identities.jsonl
  E:\…\agent-explainer\var\features\secretariat\delegations.jsonl
  E:\…\agent-explainer\var\features\secretariat\letters.jsonl
  keyring: E:\…\agent-explainer\var\features\secretariat\keyring.json
本次 --data-dir = E:\…\agent-explainer\.tmp\v050-docker\run-c\data
[PASS] 默认落点 = <应用根>/var/features（容器里 = /app/var/features，落在挂载卷内）
[PASS] 默认落点不跟随 --data-dir（t2 缺陷类：容器里靠 ./data→/app/var 挂载兜住）
```

结论（这段是给部署手册用的，已同步进 `docs/05.4-docker.md` §7）：

1. 总裁办账本的根是**应用根相对**的 `var/features`，`--data-dir` 管不到它；
2. 容器形态恰好被挂载卷兜住：应用根 `/app` + 挂载 `/app/var` ⇒ 默认就在 `./data/features` 里；
3. **桌面形态没有这层兜底**（t2 实测账本落进只读的包内 `_internal/var/features`）——同一条默认值，
   桌面侧靠 `desktop/sidecar.js` 传 `--features <userData>/features` 修，容器侧本轮显式钉 `/app/var/features`；
4. 只要有人改 `WORKDIR`、或把 `/app/var` 改成非挂载路径，账本就会**静默跑出卷外**（升级换镜像 = 账本与私钥蒸发）。
   显式 `--features /app/var/features` 把这颗雷拆了。

---

## 6. 鉴权口径差异（会影响 QA 的三形态验收，必须下传）

卡片写的容器四条里，`GET /api/secretariat/status` 预期「200 且 enabled=true」，
`GET /api/secretariat/identities` 预期「team 未登录 401」。实测（team 模式）：

| 请求 | 实测 |
| --- | --- |
| `GET /health` | 200，`version=0.5.0` ✔ |
| `GET /api/secretariat/status`（未登录） | **401 `AUTH_REQUIRED` / "Not signed in" / hint「登录后带 Cookie 或 X-TraceLens-Token」** |
| `GET /api/secretariat/status`（登录后，Cookie + `X-CSRF-Token`） | 200，`secretariat.enabled=true` ✔ |
| `GET /api/secretariat/identities`（未登录） | 401 ✔（与卡片一致） |
| `POST /api/secretariat/identities`（登录 + CSRF） | 201 ✔ |

原因在代码里：identities 的 POST 走 `settings.write` 域，`/api/secretariat/status` 在 team 模式同域
（`local` 模式才公开——t2 的 sidecar 实测就是 local 模式，所以那边是 200）。
**给 QA 的验收口径**：三形态里凡走 team 模式的（NSIS 装完首次登录、Docker、compose），
总裁办接口要在登录后测；`curl` 直连看到 401 是**预期**，不是缺陷。写请求还须带登录响应里的
`csrf_token`（`X-CSRF-Token`），只带 `X-TraceLens-Token` 的客户端豁免 CSRF。

---

## 7. 验收覆盖缺口（42 项里没有 5.0 总裁办）

`scripts/docker-verify.sh`（313 行）第 5/6 步调 `scripts/verify_e2e.py`（363 行）跑 `fresh` /
`after-restart` 两阶段断言。实测统计：

```
scripts/verify_e2e.py 出现 secretariat 次数 = 0
scripts/verify_e2e.py 出现 identities 次数 = 0
scripts/verify_e2e.py 出现 总裁办 次数 = 0
```

即：镜像验收链覆盖登录三角色 / webhook 写入 / 审计导出 / 卷持久化 / 断网运行 / 离线包，
**不覆盖 5.0 新增的总裁办**。团队目标里「补齐 5.0 总裁办模块的打包覆盖」在 Docker 这一形态**尚未闭合**。

本轮不修（`scripts/verify_e2e.py` 与 `scripts/docker-verify.sh` 不在 t5 写作用域内），建议补丁：

- `phase_fresh`：登录后 `GET /api/secretariat/status` → 200 且 `secretariat.enabled=true` 且
  `chains.*.ok=true`；`POST /api/secretariat/identities` → 201 且 `identity.fingerprint` 非空。
- `phase_after_restart`：`counts.identities` 不减、`chains.identities.head` 与重启前逐字相同、
  `keyring.exists=true` 且 `keys>0`。
- `docker-verify.sh` 第 9 步后加一步：`tar` 里 `identities.jsonl` 的宿主路径断言（`./data/features/…`）。

---

## 8. 用户本机终端要跑的确切命令（跑完必须回补总包）

```bash
cd "<仓库根>/agent-explainer"
cp scripts/env.example .env               # 填 TRACELENS_ADMIN_PASSWORD（≥10 位）
docker info | head -5                     # 必须先看到 Server 段，permission denied 就别往下走

bash scripts/docker-build.sh --save       # ① 构建 tracelens:0.5.0 + ② tar + SHA256SUMS
bash scripts/docker-verify.sh             # ② 42 项验收，退出码 0 才算过
#   → release/0.5.0/{verify-fresh.json, verify-restart.json, docker-info.txt,
#                    docker-verify.log, IMAGE-INFO.json, tracelens-0.5.0-image.tar, SHA256SUMS.txt}

bash scripts/docker-run.sh --name t5-secretariat     # ③④ 容器内四条（team 要先登录，见 §6）
ls -l data/features/secretariat                      # ⑤ identities.jsonl / keyring.json 应在宿主 ./data
docker rm -f tracelens && bash scripts/docker-run.sh --name t5-secretariat
#   重启后 GET /api/secretariat/status（带会话）→ counts 不减、chains.*.ok=true

sha256sum -c release/0.5.0/SHA256SUMS.txt            # ⑥ tar 校验
docker rmi tracelens:0.5.0 && docker load -i release/0.5.0/tracelens-0.5.0-image.tar
```

跑完按队长的硬要求回补总包（zip 是内容快照，不重打就是假数据）：
`tracelens-0.5.0-image.tar` + `IMAGE-INFO.json` + `docker/SHA256SUMS.txt` 进
`release/tracelens-0.5.0-release/docker/`，删 `DOCKER-PENDING.md`，重打
`release/tracelens-0.5.0-release.zip`（`ZIP_STORED`），重算 `SHA256SUMS-bundle.txt` 与
`release/SHA256SUMS-release.txt`（0.4.5 那行必须逐字未变），再逐条复算。
**已签名两 exe 的字节不得动**（Setup `37cf6963…0395d` 151,838,192 B / portable `8bdc55a0…fdd52` 137,883,824 B）。

---

## 9. 发布总包这一轮的同步（captain 追加要求的「未产 tar」分支）

tar 没产出 ⇒ 按边界**保留 `DOCKER-PENDING.md` 口径**，不放冒充物。本轮把总包里的
`docker/DOCKER-PENDING.md` 从 7 行占位（425 B）改成写清「为什么没有、怎么补、已实测的口径」的版本，
并同步重打总包（zip 是快照，目录改了 zip 必须跟着改）：

| 件 | 本轮值 |
| --- | --- |
| `release/tracelens-0.5.0-release/docker/DOCKER-PENDING.md` | 改写（含 §1 阻塞原文、§8 命令、§5/§6 实测口径） |
| `release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt` | 重算（21 条：新增本文 `docs/audit/v050-docker.md` 后逐条重算，UTF-8 无 BOM、CRLF） |
| `release/tracelens-0.5.0-release.zip` | 重打，`ZIP_STORED`，字节 `294,953,425` （22 条目 = 21 交付件 + 清单） |
| `release/SHA256SUMS-release.txt` | 两行；0.4.5 行 `0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab` **逐字未变** ⇒ 旧包未动 |
| 新 zip 的 sha256 | `136c1d285cae1e967a2ddc7d91debe75b08dea7a5ffd225b043fa01d2c09e49d`（本轮重建脚本打印值；**权威**是 `release/SHA256SUMS-release.txt` 第 2 行——本文在包内，本文最后一次同步会让该值再变一次，自引用不可达，核对以清单为准） |
| 逐条复算 | 4 份清单（bundle / release 根 / dist / release-release）+ zip 内条目全部重算通过，`exit 0` |

**自引用边界（和 t4 同一件事，写明白免得被当成对不上）**：本文与 `DOCKER-PENDING.md` 都在总包里，
所以 zip 自己的哈希**不能**被本文自证——写进本文就改变包内容。上面这对数字是**本轮最后一次重建**
打印的实测值，权威记录是 `release/SHA256SUMS-release.txt` 第 2 行；核对请跑 `python .tmp/t5-verify.py`
（只读复算，逐条重算 + `[0.4.5 旧包未动]` 断言）。

---

## 10. 本轮未做 / 交接口（明确列出，不含糊）

| 卡片项 | 状态 | 交接 |
| --- | --- | --- |
| 1 镜像构建 `tracelens:0.5.0` | ❌ 未做（环境阻塞 §1） | 用户终端跑 `bash scripts/docker-build.sh --save` |
| 2 `docker-verify.sh` 42 项 + 四个产物 | ❌ 未做 | 同上 `bash scripts/docker-verify.sh`；`release/0.5.0/` 现在只有 `DOCKER-BLOCKED.md` |
| 3 `docker save` tar + `SHA256SUMS.txt` + `IMAGE-INFO.json` | ❌ 未做 | 脚本第 9 步自动产出，字段口径已核对（0.4.5 那份是 `version/image/tar/size/sha256`） |
| 4 容器内四条 | ⚠️ 等价实测（source 模式，同 command 口径）+ 一条口径差异 §6 | QA 按 §6 口径验收；镜像内复跑由 §8 命令完成 |
| 5 账本落挂载卷 + 重建持久 | ✅ 实测（§4/§5）+ 编排已修（§3） | 镜像内复跑 `ls -l data/features/secretariat` |
| 6 `docker load` 复算 | ❌ 未做 | 用户终端跑 §8 最后两行 |
| 总包 docker/ 回补 | ⏸ 条件不成立（无 tar），保留占位并改写 | tar 产出后按 §8 末尾四步 |
| 42 项补总裁办断言 | ⚠️ 范围外，建议已给（§7） | 队长排给有 `scripts/verify_e2e.py` 写权限的任务 |

---

## 11. 现场与日志

- 改动文件：`docker-compose.yml`（5,159 B）、`scripts/docker-run.sh`（5,761 B）、
  `docs/05.4-docker.md`（§7 数据卷 + §14 诚实边界）、`release/0.5.0/DOCKER-BLOCKED.md`（新增）、
  `release/tracelens-0.5.0-release/docker/DOCKER-PENDING.md`（改写）、`docs/audit/v050-docker.md`（本文）。
- 探针与日志（`agent-explainer/.tmp/`，PS 5.1 的 `*>` 是 UTF-16）：
  `t5-docker-sim.py`（A/B/C 三组 18 项）、`t5-sim.log`、`t5-verifyprobe.py`（verify_e2e 覆盖统计）。
- 环境副作用一条，如实报备：为了尝试起 docker 守护，我启动了这台机器上的 **Docker Desktop**
  （窗口 pid 10108，标题 `Containers - Docker Desktop`），**没有退出它**——用户终端可直接复用；
  不需要就手动 Quit。
- 未动清单（可核对）：`web/**`、`agent_explainer/**`（业务代码）、`tests/**`、
  两个已签名 exe、`release/0.4.5/**`、`release/tracelens-0.4.5-release/**`、0.4.5 总包 zip。
- 改动后复跑的全量测试套件：`python -m unittest discover -s tests -q` →
  `Ran 954 tests in 197.921s` / `OK (skipped=3)` / exit 0（基线 954 全绿，**零回归**；
  compose 与 `docker-run.sh` 是编排侧改动，没有任何测试因此变红）。日志
  `.tmp/t5-full-suite.log`（PS 5.1 的 `*>` 写出来是 UTF-16，读要 `-Encoding Unicode`）。

---

## 12. 镜像侧闭合实录（队长在 danger-full-access 会话，2026-11-12 04:08–04:32，用户批准）

用户在三个选项里选了「批准队长在本会话跑 docker 闭合 t5」，t5 由队长接管（`reassign_task` →
captain）收口。**上面 §1–§11 关于"镜像未产出"的结论已被本节取代**；§2 的阻塞诊断（成员受限会话
打不开 `dockerDesktopLinuxEngine` 管道）仍然成立，是会话权限差异，不是产品问题。

### 12.1 构建与验收脚本（都 exit 0）

```
docker version → 29.9.3 / client 29.9.3 / os=linux/amd64      context = desktop-linux
bash scripts/docker-build.sh --save   → EXIT 0   （日志 release/0.5.0/build-050.log）
bash scripts/docker-verify.sh         → EXIT 0   「结论：全部通过」（日志 release/0.5.0/docker-verify.log）
```

16 组小结全 PASS，含 `GET /health 200 + status=ok`、`Docker HEALTHCHECK → healthy`、
`verify_e2e fresh` / `after-restart`（记录数/用户表/审计链活过重启）、`./logs 落盘`、`断网运行`
（`version=0.5.0 mode=team records=12 durable=True`）、`离线包 sha256 复算一致`、`docker load`。

**两次构建的哈希不同，最终态以 `IMAGE-INFO.json` + `SHA256SUMS.txt` 为准**（如实记，避免复算时对不上）：

| 阶段 | 镜像 ID | tar sha256 |
|---|---|---|
| `docker-build.sh --save` | `7efecf556332…`（246,561,040 B） | `94dac8843be0…`（打印在 `build-050.log`） |
| `docker-verify.sh`（内部再 build 一次并重存 tar） | **`6cb8417164f3…`**（246,561,040 B） | **`d93d1803c6ec…`（64,029,696 B）** |

`release/0.5.0/IMAGE-INFO.json` 记的就是最终那一份（`image_id=sha256:6cb84171…`、
`tar_sha256=d93d1803…`），`release/0.5.0/SHA256SUMS.txt` 与之逐字一致。

### 12.2 容器内总裁办四条（team 模式，真跑）

```
[health]   200 version=0.5.0 mode=team
[anon]     GET /api/secretariat/status = 401 code=AUTH_REQUIRED
[login]    200 role=admin csrf=有
[status]   200 enabled=True counts={"identities":0,…}  账本 path=/app/var/features/secretariat/identities.jsonl
[register] 201 seq=1 entry_hash=f7786807ec9484a0268c…
[after]    counts={"identities":1,"active_identities":1,…}
```

镜像 LABEL 核对：`org.opencontainers.image.version=0.5.0`、`tracelens.mode=team`、
`tracelens.build.by=docker-build.sh`。

**写作用域鉴权口径（下传 QA，已实测）**：未登录读是 401，写请求必须带登录响应里的 `csrf_token`
（`X-CSRF-Token`）+ 会话 Cookie。`admin` 用户名 + `TRACELENS_ADMIN_PASSWORD` 注入（`auth.py:609` bootstrap）。

### 12.3 账本落挂载卷 + 重建持久（本轮的硬要求）

```
容器 A 登记后卷内：
  /app/var/features/secretariat/identities.jsonl   634 B
  /app/var/features/secretariat/keyring.json       344 B  （权限 0600）
  sha256(keyring) = e006d4c9e43299c11730ab0e6cfeebd84ecb8ec791c0e0132d4e4a4107e9fb3f

docker rm -f（卷 tl050var 保留）→ 新容器 tl050d 同卷：
[status] 200 enabled=True counts={"identities":1,"active_identities":1,…}   ← 计数不减
[check]  chain ok=True  head=f7786807ec9484a0268c…（与登记时的 entry_hash 同一个）
卷内文件与 sha256(keyring) 与容器 A 完全一致
```

落点由 `docker-compose.yml` 与 `scripts/docker-run.sh` 显式钉的 `--features /app/var/features` 决定
（backend 在 §4 实测：默认落点 = `<应用根>/var/features` 且**不跟随 `--data-dir`**，就是桌面侧 t2 栽的那条默认值）。

### 12.4 一条必须记下来的操作坑（我第一次就踩）

镜像的 `ENTRYPOINT` **已经是完整命令**（`python -m agent_explainer --mode team --host 0.0.0.0 --port 8765`）。
直接 `docker run … tracelens:0.5.0 python -m agent_explainer … --features …` 会把这串**追加**到
entrypoint 后面 → 参数重复 → 容器**立刻退出**，`docker inspect` 报 `container … is not running`。
正确做法与 `scripts/docker-run.sh:73` 一致：`--entrypoint /usr/local/bin/tracelens-entrypoint` 之后再传完整命令。

### 12.5 离线包与总包回补（队长的硬要求）

```
docker 三件入包：docker/tracelens-0.5.0-image.tar（64,029,696 B）
                docker/IMAGE-INFO.json（759 B）、docker/SHA256SUMS.txt（92 B）
删除占位 docker/DOCKER-PENDING.md（镜像已产出，留着就是假数据）
SHA256SUMS-bundle.txt 重算：23 条（自身不入清单），逐条复算「全部通过」，UTF-8 无 BOM、CRLF
zip 重打：ZIP_STORED（全条目 compress_type=0）、24 条目、358,982,619 B
          sha256 = 0830f61b40c06b92722b5777610d7f5f27257fd37d2d4c451cf703b61a5a22c2
release/SHA256SUMS-release.txt：0.4.5 行逐字未变 = True
docker load 独立复算：docker rmi -f 后从「包内 tar」load → Loaded image: tracelens:0.5.0
                      id=sha256:6cb84171…  size=246,561,040  version label=0.5.0
zip 条目集合 == 目录文件集合 = True
```

**zip 哈希的自引用（必须写明，否则复算会对不上）**：总包里带着本文档，所以「把本文改成最终版」这一步
之后必须再重打一次 zip，文档里就不可能印出自己的哈希。实测两轮：

| 轮次 | zip 字节 | sha256 |
|---|---|---|
| 回补 docker 后（本文 §12.1–§12.4 已同步） | 358,982,619 | `0830f61b40c0…a5a22c2` |
| 本文同步进包后重打（最后一次） | 358,988,434 | `c18afad78b05…354fab61` |

**权威值以 `release/SHA256SUMS-release.txt` 第 2 行为准**（`SHA256SUMS-bundle.txt` 的 23 条逐条复算
全部通过，`zip 条目集合 == 目录文件集合 = True`）。QA 复算时读清单文件，不要读文档里的打印值。

### 12.6 边界与现场

* 沙箱边界不变：**成员受限会话仍打不开 docker 引擎管道**，镜像构建只能跑在完全权限会话（本轮队长）。
* `scripts/verify_e2e.py` 里总裁办断言仍为 **0 次**（§7 的补丁建议保留，交 QA 记遗留）。
* 探针（保留可复跑）：`.tmp/v050-docker-persistence.py`（register / check 两模式）、
  `.tmp/v050-bundle-repack.py`（回补 + 重打 + 同步两份清单）。
* 现场清理：容器 `tl050*` 全部 `docker rm`、命名卷 `tl050var` 已删；**镜像 `tracelens:0.5.0` 保留**
  在本机；Docker Desktop 仍在运行（pid 10108，backend 启动、未退出）。
* 本轮镜像侧未改任何产品代码/测试，故未重跑全量套件（§11 的 954 全绿仍是最新有效结论；
  下一次代码改动由 t6 复跑闸门）。
