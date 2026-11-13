# 06 常见问题（FAQ）· TraceLens 行动透镜

> 按场景分组。每条 Q 都给出**能照做**的答案；涉及命令的地方都是本机实测过的命令。
> 面向非技术读者的操作手册见 [06-user-guide.md](./06-user-guide.md)；Docker 的深入排障见 [05.4-docker.md](./05.4-docker.md)。

---

## A. 安装与启动

**A1. 双击安装包，Windows 弹"已保护你的电脑"，能装吗？**
能。这是**自签名**包（我们自己的证书，Windows 还不认识）。点"更多信息 → 仍要运行"。
它只证明"文件出自这份密钥、内容没被改过"，不等于"微软认证软件"；企业 AppLocker/WDAC 仍可能拦。
想彻底消除提示：买 OV/EV 代码签名证书重签，或在**你自己的机器**上把 `release/TraceLens-SelfSigned.cer`
导入"受信任的根证书颁发机构"（仅开发机这么做）。

**A2. 安装包和便携版有什么区别？**

| 项 | 安装包（Setup.exe） | 便携版（portable.exe） |
| --- | --- | --- |
| 是否装进系统 | 是（开始菜单 + 卸载项） | 否（双击即跑） |
| 数据位置 | Electron 用户数据目录下的 `data`（默认 `%APPDATA%\<应用名>\data`） | **同上**（便携版不改数据位置，也不写在 exe 旁边） |
| 换电脑 | 重装 + 拷 `data` | 整个文件夹拷走 |
| 权限 | 可能需要管理员确认 | 一般不需要 |

**A3. 装完第一次打开要等多久？**
首次启动要拉起内置服务并写演示数据，通常几秒。若超过 30 秒还是空白，见 A4。

**A4. 窗口起来但界面空白 / 一闪就退出怎么办？**
先确认不是"还没起完"（等 5–10 秒）。仍不行时按顺序试：① 用便携版再试一次；
② 右键"以管理员身份运行"；③ 看日志（桌面壳会在启动失败时弹框，框里有最后几行输出）；
④ 把 [06-feedback-form.md](./06-feedback-form.md) 的"故障"栏填好发给我们（附截图最有帮助）。
**注意**：安装包/便携版的图形界面**没有**在构建会话里启动验证过（构建沙箱的 Electron 起不来），
所以如果你遇到启动问题，对我们来说就是**第一手信息**，请务必反馈。

**A5. 静默安装怎么做？**
`.\TraceLens-0.5.0-win-x64-Setup.exe /S`。适合批量部署；装完同样从开始菜单启动。
（如实：`/S` 退出码 0 是 **0.4.5 时点**的本机实测；**0.5.0 的 `/S` 安装实测未做**，见
[docs/audit/v050-desktop.md](./audit/v050-desktop.md) 未做项与 [../README.md](../README.md) §3。安装前请把文件放到纯 ASCII 路径。）

**A6. 卸载会把我的数据删掉吗？**
不会。卸载只删程序，用户数据目录（默认 `%APPDATA%\<应用名>\data`）保留。要彻底清干净，卸载后手动删这个目录
（**注意**：审计账本也在里面，删了就没有证据了，先备份）。

**A7. 能不能同时开两个窗口 / 两台电脑用同一份数据？**
同一台机器上不行：程序有单实例锁，第二次启动会把已有窗口叫到前面。
两台机器更不行：这是**单进程**设计，多人请用团队版（Docker），一人一个账号。

---

## B. 登录与账号（团队模式）

**B1. 第一次登录用什么账号？**
用户名 `admin`，口令来自部署时设置的 `TRACELENS_ADMIN_PASSWORD`。若部署时没设，产品会**生成一次性口令**
并打印在容器日志里（`docker logs tracelens`），只显示这一次，登录后必须改密。
注意：这个环境变量**只在用户表为空（首次初始化）时**生效；如果你复用了旧的 `./data`（里面已有账号），
改 `.env` 里的口令不会改变已有账号的口令（本机实测：旧口令 200、新口令 401）。

**B2. 登录后提示必须改密码，能跳过吗？**
不能，也不建议。初始口令是给"第一次进门"用的，改完才安全。

**B3. 管理员口令忘了怎么办？**
两条路：① 还有别的 admin → 让他登录后在"用户管理"里给你重置；
② 一个 admin 都没有 → 停容器、**先备份**、删掉 `./data/auth/auth-state.json`，再启动：
会按 `TRACELENS_ADMIN_PASSWORD` 重新建一个 admin。**本机实测**：删掉后重启，控制台打印
"首次启动：已创建管理员 admin，初始口令来自环境变量 TRACELENS_ADMIN_PASSWORD"，登录正常，
且 `records.jsonl` 的 13 条记录与审计链**都还在**（丢的只有账号/会话/令牌）。

**B4. 连续输错口令会怎样？**
连续 5 次失败锁定 15 分钟（返回 429 `RATE_LIMITED`，带 `Retry-After`）。等一会儿再试。

**B5. 为什么我看不到"设置""用户管理"？**
权限矩阵：只有 `admin` 能改设置、管用户；`editor` 能决定和导入；`viewer` 只读（连干预按钮都会 403）。
界面上是**隐藏或禁用并写原因**，不是坏了。

**B6. 怎么加人？**
`admin` → 用户管理 → 新建（用户名只能小写字母/数字/`.`/`_`/`-`，2–32 位），选角色、设初始口令。
用户**只禁用不删除**（审计要能追到人）。

---

## C. 界面

**C1. 三个区为什么只有"待确认"里 1 条？**
待确认是**串行队列**：同一时刻只摆 1 件，其余显示"还有 N 件在排队"。这是设计，防止你被淹没。

**C2. 卡片上的"演示数据"徽标什么时候消失？**
机器判定：`demo_data=true`、`synthetic=true`、`source.kind=demo` 同时命中才出现。
换成你自己的真实数据后，横幅和徽标会自动消失（不是手动关的）。

**C3. 切语言会不会丢我导入的数据？**
不会。切语言只换界面文案（英文界面连演示数据也是英文），记录以服务器为准；筛选、展开的卡片、
时间轴位置都保持不动。这条在本版刚修好（见 [CHANGELOG](../CHANGELOG.md) B-01）。

**C4. 手机上找不到设置/审计/导出？**
点顶栏右侧「**更多**」，六个入口都在。灰色按钮旁边会写原因（没权限 / 没连上服务）。

**C5. 顶栏的"实时：live / 降级轮询"是什么意思？**
`live` = 服务端在推送；`降级轮询` = 推送断了，页面每 4 秒主动拉一次（有原因标注）。
推送断开时不会假装"实时"。

---

## D. 导入数据

**D1. 支持哪些格式？**
四种自动识别：**Langfuse 导出**、**OTel GenAI / OTLP 跨度**、**MCP 事件流**、**本产品原生 ActionRecord**。
样例在 `samples/traces/`（8 / 7 / 9 条）与 `samples/action_records.json`（12 条）。
认不出来会**明确报错**，不会"硬按某一种解析"。

**D2. 有大小限制吗？**
一次 ≤ **64 KB**、≤ **200 条**。超了前端直接拦（0 次请求），服务端也会拒绝（413 / 400）。
大批量请分批，或走实时上报。

**D3. 导入会覆盖掉我已经做的决定吗？**
不会。用户的决定（`user_intervention`）与执行回执（`execution_receipt`）是**受保护字段**，
`upsert` / `update` 都覆盖不掉；`create` 撞上已有 id 会计入"重复"而不覆盖。

**D4. 导入的记录重启后还在吗？**
在。记录写进 `records.jsonl`（追加式文件）。界面上的"落盘"chip 会显示当前是不是真落盘
（用 `--no-records-store` 启动的服务只放内存，重启就没了，chip 会如实写"内存"）。

**D5. 导入的数据算"演示数据"吗？**
不算。徽标由服务端下发的 `demo_data` / `synthetic` / `source.kind` 判定，导入的记录不是演示数据。

---

## E. 实时接入（webhook / SDK / SSE）

**E1. 团队模式上报必须要令牌吗？**
要。`admin` 签发 `ingest:write` 令牌（明文只显示一次），上报时带
`X-TraceLens-Ingest-Token: tlk_…` 或 `Authorization: Bearer tlk_…`。
`local` 模式（只监听回环）可匿名上报，审计里标 `ingest:anonymous-local`。

**E2. 上报会不会替我做决定？**
不会。上报只写**事实字段**（状态、耗时、结果摘要等），永远不写 `user_intervention`，
也不会把"未上报"改成"已执行"。

**E3. 重复上报会写两条吗？**
不会。带同一个 `Idempotency-Key`（或请求体里的 `idempotency_key`）在 15 分钟窗口内重投，
返回首次结果并标 `replay:true`；同 key 不同内容 → 409 `INGEST_IDEMPOTENCY_CONFLICT`。

**E4. 为什么别的窗口做的决定，我这页要等几秒才变？**
本机实测：SSE 显示 `live` 时，别处做的决定**没有帧推到本页**（服务端/环境问题，已记录为已知限制）。
页面侧有两条兜底：决定帧强制合并 + 降级轮询（4 秒），所以最迟 ~4 秒可见。

**E5. 有现成的接入示例吗？**
有：`python samples/sdk_demo.py` —— 一条命令跑完，全程离线，自动起服务、上报、收推送、
校验落库，最后打印 `sdk_demo: OK`（实测 12 帧推送、16 条记录、其中 4 条来自实时上报）。

---

## F. 审计与导出

**F1. 审计能不能删掉几条？**
不能。审计是 **append-only**（只追加），没有任何"删除/清空/截断"的接口；
手改 `audit.jsonl` 会让哈希链断开。

**F2. 链断了会怎样？**
`/health` 里 `audit.ok=false`；`GET /api/audit` 返回 500 `AUDIT_CHAIN_BROKEN`；
导出**拒绝出文件**（不出半条证据）。恢复只能换回备份，系统不提供"修历史"。

**F3. 导出里为什么看不到邮箱/手机号原文？**
写盘前强制打码（13 类模式），形如 `<redacted:email#sha256:ab12cd34ef56>`：
同一个值每次打码结果相同（可对账），但拿不回原文。没有"关闭打码"的开关。

**F4. PDF 导出变成网页了？**
服务器没装 reportlab 时会自动回退成"可打印网页"（浏览器 Ctrl+P 存 PDF），
响应头 `X-TraceLens-Export-Fallback` 会写明原因。Docker 官方镜像默认装了 reportlab。

**F5. 为什么 viewer 也能导出审计？**
规格如此：`audit.export` 四个角色都有。它不是缺陷，但也意味着"只读账号能把审计带走"，
敏感环境请酌情只给需要的人 viewer 账号。

**F6. 导出文件里有"这是合成演示数据"的标记吗？**
**没有**（HTML / PDF 页脚还未实现）。页面上有常驻演示横幅，导出文件里没有——
这一条已记录为已知限制，交付文档不得宣称已具备。

---

## G. Docker / 团队服务器

**G1. 起不来，报 `required variable TRACELENS_ADMIN_PASSWORD is missing a value`？**
没建 `.env` 或没填口令：`cp scripts/env.example .env` 并填一个 ≥10 位的口令（这是故意的，
宁可起不来，也不让管理员口令只出现在滚动日志里）。

**G2. 怎么确认真的起来了？**
`docker compose ps` 看到 `healthy`，并且 `curl -s http://127.0.0.1:8765/health` 返回
`"status": "ok"`。**没有** `/api/health` 这个路径（会 404），也没有 `ready` 字段。

**G3. 容器 `running` 但显示 `unhealthy`？**
`docker inspect --format '{{json .State.Health.Log}}' tracelens` 看最近一次检查输出。
常见原因：数据目录不可写（见 G4）、端口被占、进程崩了（`docker compose logs` 有栈）。

**G4. 启动报"数据目录不可写：/app/var（uid=10001 gid=10001）"？**
宿主目录属主不对。Linux 上执行 `sudo chown -R 10001:10001 ./data ./logs`。
Windows Docker Desktop 的绑定挂载通常直接可写，实测没这个问题。

**G5. 数据放哪？怎么备份/升级？**
数据全在宿主 `agent-explainer/data`（= 容器 `/app/var`）：`records.jsonl`、`audit.jsonl`、`settings.json`、
`auth/`、`ingest/`，以及 **5.0 总裁办账本 `features/`**（`secretariat/identities.jsonl`、`keyring.json`（权限 `0600`）、
`delegations.jsonl`、`letters.jsonl`、`evidence/`、`rollback/`）——它就是靠显式 `--features /app/var/features`
钉在这个卷里的，默认值 `<应用根>/var/features` **不跟随 `--data-dir`**（换镜像不冲掉、删容器同卷重建数据不丢）。
备份 = `docker compose stop` → 拷 `data logs` → `start`；
升级 = 换镜像标签 → `docker compose up -d`（数据目录不动）。完整步骤见 [05.4-docker.md](./05.4-docker.md) §7–§8。

**G6. 没有外网怎么装？**
用离线镜像包：`docker load -i release/0.5.0/tracelens-0.5.0-image.tar`（64,029,696 字节；
校验 `cd release/0.5.0 && sha256sum -c SHA256SUMS.txt` → `OK`；镜像 `tracelens:0.5.0` =
`sha256:6cb84171…`，见 `release/0.5.0/IMAGE-INFO.json`），再 `docker compose up -d`。
镜像里没有需要联网的依赖；实测 `docker run --network none` 也能跑（`docs/audit/v050-docker.md` §12）。
0.4.5 的老包在 `release/0.4.5/`，**别拿它的 tar 当 0.5.0 用**（哈希与标签都不同）。

**G7. 能开多个副本吗？**
不要。单进程设计 + 单数据卷；多副本共享同一 `./data` 没有主从与冲突合并。

---

## H. 数据与隐私

**H1. 数据会出网吗？**
默认不会。模型默认是模板模式（离线可用）；只有你显式配置了 LLM 地址/密钥（非回环还必须
`ALLOW_OUTBOUND=1`）才会连模型端，而且那也只发"要润色的这条事实"，不发全库。

**H2. 模型密钥存在哪？**
team / 容器模式：只在环境变量里，**不落盘**；local 模式在 Windows 上可用 DPAPI 加密存盘，
非 Windows 只驻内存。接口永远只回掩码与指纹。

**H3. 怎么彻底清空数据？**
停服务 → 删数据目录（两个桌面形态都是 Electron 用户数据目录下的 `data`，默认 `%APPDATA%\<应用名>\data`、
Docker 下 `agent-explainer/data`）→ 重启。**注意**：审计也会一起没，先备份。
Docker 里也可以用界面"数据管理 → 重置"（只清记录、审计只追加一条 `record_reset`）。

**H4. 记录里的个人信息怎么处理？**
写盘前强制打码（邮箱/手机号/身份证/银行卡等 13 类），审计、查看、导出三处同源。

---

## I. 排障速查

| 现象 | 先查这个 |
| --- | --- |
| 端口被占（`bind failed` / 页面打不开） | 换端口：`--port 8899`（源码）或 `.env` 里 `TRACELENS_PORT=8899`（Docker） |
| 页面 404 / 找不到资源 | 确认访问的是 `/`（不是 `/index.html` 之外的路径）；静态资源只从产品目录托管 |
| `GET /api/health` 返回 404 | 正常：真实健康检查是 `GET /health`（别名 `/healthz`） |
| 版本号对不上 | 以 `/health` 的 `version` 为准（当前 `0.5.0`）；`__init__.py:__version__` 自 0.5.0 起与之相同（历史上的 `0.1.0` 不一致已收口，单一来源仍是 `server.py:SERVER_VERSION`） |
| 想知道服务器到底在干什么 | `docker compose logs -f tracelens`；文件日志在 `./logs/tracelens-YYYYMMDD.log` |
| 记录/审计文件能不能删 | 记录能（会丢数据），审计**不能**（append-only，删了等于毁证据） |
| 反馈问题要带什么 | 版本号、形态（安装/便携/Docker）、操作系统、做了什么、看到的原话、截图、日志最后 20 行 |
