# TraceLens 0.5.0 独立验收报告（T5 / 任务 t6）

> 验收人：`qa`（测试与质量官）。**独立验收**：本报告的每一条结论都来自本轮**自己跑出来的命令与原始输出**，
> 不转述 t1–t5 的结论；凡本轮跑不了的项，一律写「未验证 + 原因」，不拿别的形态顶替。
>
> 被测基线：工作区 `E:\DEEPSEEK 黑奴\agent-explainer`（0.5.0 全量发布件，t5 收口后状态）。
> 时间：2026-11-12 04:19 → 04:35（Asia/Shanghai）。本轮**只写本文件**，不改任何代码 / 脚本 / 文档 / 产物（§12 有边界声明）。

---

## 0. 结论

**有条件 PASS。**

- 发布件本体、校验链、签名口径、三处数据库落点、升级持久性、四道闸门**全部实测通过**（954 用例 0 红、4 个 SHA256SUMS 逐条复算全过、签名状态 UnknownError 如实且未被写成 Valid）。
- 三条交付承诺中 2 条**直接通过**，第 1 条（总裁办面板在便携版里可见）**静态 + 服务端资源实证通过、GUI 渲染未验证**（DSH 会话无法起打包态 Electron 窗口，§2.1 有原始输出）。
- 阻断性缺陷：**0 条**。非阻断缺陷 7 条（§11），其中 2 条是**文档口径与事实矛盾**（会给用户错误指引），1 条是**概率性假红测试**，其余为测试/工具卫生问题。附条件（§0.1）。

### 0.1 放行条件（4 条，均为非发布件本体）

| # | 条件 | 依据 |
|---|---|---|
| C1 | 便携版 GUI 与 NSIS 安装/静默安装在**用户桌面会话**补测（含总裁办面板可见性、userData 落点的界面级复核） | §2.1 原始输出：打包态 Electron 在本会话 `EXITED exitcode=0`、零输出、零监听端口 |
| C2 | 修正文档口径矛盾：`README.md` / `docs/06-faq.md` / `docs/06-user-guide.md` / `docs/NOTICE-third-party.md` / `docs/05-real-user-kit.md` 仍以 **0.4.5** 文件名与哈希作为交付指引；`docs/05.4-docker.md:459` 与 `release/0.5.0/DOCKER-BLOCKED.md` 仍声称「0.5.0 镜像未产出」 | F1、F2（§11） |
| C3 | 修正 `tests/test_crypto_sig.py:367` 的概率性断言（末字节为 `0x00` 时变异签名 == 原签名），否则全量套件约 **1/256** 概率假红 | F3（§11） |
| C4 | `tools/sign_and_hash.ps1 -CheckEncoding` 的固定清单仍指向 `release\tracelens-0.4.5-release\`，0.5.0 包未被编码审计覆盖 | F5（§11） |

### 0.2 九项必做实测判定一览

| # | 验收项 | 判定 | 证据位置 |
|---|---|---|---|
| 1 | 源码形态：`/health` 0.5.0、`/api/secretariat/status` 200 enabled=true、`ext/secretariat.js` 200 | **PASS** | §1 |
| 2 | 便携版形态：portable.exe 跑起来 + 三条 + 登记身份 201 | **GUI 未验证**（环境边界）；payload 独立解包 + 打包 sidecar 真跑 **PASS** | §2 |
| 3 | 容器形态：`/health` 0.5.0、**未登录 401 AUTH_REQUIRED**、登录后 status 200、LABEL 0.5.0 | **PASS** | §3 |
| 4 | 升级/重启持久性（三种形态独立重跑）+ `_internal/var` 不存在或为空 | **PASS** | §4 |
| 5 | 校验链：4 个 SHA256SUMS 逐条复算、zip 条目、签名状态逐字一致、0.4.5 未变 | **PASS** | §5 |
| 6 | 闸门：全量 discover / i18n_lint / gen_api_contract --check / web selfcheck | **PASS**（954/OK/3 skip；契约唯一失败=8 个非总裁办端点；92/92） | §6 |
| 7 | 版本单一来源五处 = 0.5.0 + 残留 0.4.5 活动文件清单 | **PASS**（清单见 §9，其中 6 个活动文件为缺陷） | §7、§9 |
| 8 | 三条交付承诺逐条判定 | 2 通过 + 1 有条件通过 | §8 |
| 9 | 遗留风险五条在案 | **PASS**（五条全部复核在案，另新增 4 条） | §10 |

---

## 1. 源码形态（PASS）

环境（每轮命令前统一设置）：

```bash
cd "E:/DEEPSEEK 黑奴/agent-explainer"
export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"
python -V            # Python 3.14.7
rm -rf .tmp/qa-src && mkdir -p .tmp/qa-src/data .tmp/qa-src/features
python -m agent_explainer --mode local --port 8798 --no-browser \
       --data-dir .tmp/qa-src/data --features .tmp/qa-src/features   # 后台
```

### 1.1 `/health`

```bash
curl -s -o .tmp/qa-src/health.json -w "HTTP=%{http_code}\n" http://127.0.0.1:8798/health
```

```
HTTP=200
{"name": "TraceLens", "version": "0.5.0", "status": "ok", "mode": "local", "schema_version": "1.1",
 "endpoints": ["/intervene", "/health", "/api/records", "/api/audit", "/api/auth/login", ...],
 "auth": {"required": false, "mode": "local", ...},
 "stats": {"count": 12, "durable": true, ...}, "audit": {"ok": true, "durable": true, "count": 0, ...}}
```

> 观察（info，非缺陷）：`/health.endpoints` 只列 18 条核心路由，不含 `/api/secretariat/*`（契约里总裁办路由是另 8 条）。
> 本轮验收按任务口径直接打 `/api/secretariat/status`，不依赖这个列表；若后续有客户端「按 `/health.endpoints` 发现能力」，
> 会看不见 5.0 的 8 条路由——建议后续把 secretariat 路由并入该列表或加 `capabilities` 标注。

### 1.2 `/api/secretariat/status`

```bash
curl -s -o .tmp/qa-src/sec-status.json -w "HTTP=%{http_code}\n" http://127.0.0.1:8798/api/secretariat/status
```

```
HTTP=200
{"ok": true,
 "secretariat": {"version": "1.0", "mode": "local", "enabled": true,
   "counts": {"identities": 0, "active_identities": 0, "delegations": 0, "letters": 0, "signed_records": 0},
   "risk_policy": {"low": {"approvers": 0, "seal": false}, "medium": {"approvers": 0, "seal": false},
                   "high": {"approvers": 1, "seal": false}, "critical": {"approvers": 2, "seal": true}},
   "delegation": {"max_depth": 5},
   "chains": {"identities": {"ok": true, "count": 0, "head": "000…000", "problems": [],
                             "path": ".tmp\\qa-src\\features\\secretariat\\identities.jsonl"},
              "delegations": {...}, "letters": {...}},
   "keyring": {"path": ".tmp\\qa-src\\features\\secretariat\\keyring.json", "exists": false, "keys": 0,
               "writes": 0, "mode": "0o600", "source": "local_file"},
   "signature": {"algorithm": "ed25519", "canonical": "sorted-json", "fields": [11 项]},
   "degraded": ["local-tsa", "in-process-keys"],
   "paths": {"root": ".tmp\\qa-src\\features", "exists": true, "layout": "features.v1"}}}
```

### 1.3 `ext/secretariat.js`

```bash
curl -s -o .tmp/qa-src/ext-secretariat.js -w "HTTP=%{http_code} bytes=%{size_download} type=%{content_type}\n" \
     http://127.0.0.1:8798/ext/secretariat.js
```

```
HTTP=200 bytes=24396 type=text/javascript; charset=utf-8
/* TraceLens 5.0 - Secretariat panel (web/ext/secretariat.js) … 五个分区 … */
```

**源码形态：PASS（3/3）。**

---

## 2. 便携版形态（GUI 未验证；payload 与打包 sidecar 已实证）

### 2.1 portable.exe 直接启动 —— 未验证（原始输出，零加工）

```bash
mkdir -p /e/qa-tl050-portable            # 纯 ASCII 路径（README 问题 11 的硬要求）
cp release/TraceLens-0.5.0-win-x64-portable.exe /e/qa-tl050-portable/
```

```powershell
$p = Start-Process -FilePath "E:\qa-tl050-portable\TraceLens-0.5.0-win-x64-portable.exe" -PassThru
$done = $p.WaitForExit(25000)
if ($done) { "EXITED exitcode=" + $p.ExitCode } else { "STILL RUNNING pid=" + $p.Id; $p.Kill() }
```

```
EXITED exitcode=0
```

- `out.txt` / `err.txt`（`-RedirectStandardOutput/-RedirectStandardError` 重定向）：**均为 0 字节**。
- 同一时刻 `Get-NetTCPConnection -State Listen -OwningProcess <pid>`：无输出；`netstat -ano | grep LISTEN` 里没有该进程的任何端口。
- 结论：打包态 Electron 壳在本 DSH 会话里**立刻以 0 退出、零输出、零监听**，与 `docs/audit/v050-desktop.md §3.2` 记录一致。
  **这是环境边界，不是产品结论**：既不判 PASS，也不判 FAIL —— 按任务要求写「未验证」，且**没有拿源码态顶替**。

### 2.2 独立解包便携版（证明「发出去的 exe 里到底是什么」）

用 `desktop/node_modules/electron-winstaller/vendor/7z-x64.exe`（复制为 `7z.exe` + `7z-x64.dll` 复制为 `7z.dll`）解包**交付件本体**：

```
7z l TraceLens-0.5.0-win-x64-portable.exe
  Type = Nsis · SubType = NSIS-3 Unicode · Physical Size = 137843990
  $PLUGINSDIR\System.dll            6,931
  $PLUGINSDIR\app-64.7z       137,543,897
  $PLUGINSDIR\nsis7z.dll          242,382
  $PLUGINSDIR\StdUtils.dll         45,608
```

```
7z x -o"…" "…\$PLUGINSDIR\app-64.7z"
  Folders: 68  Files: 421  Size: 466,789,487  Compressed: 137,543,897
```

解包后的程序目录（= 便携版运行时真正落地的内容）：

| 检查项 | 实测 |
|---|---|
| `resources\sidecar\agent-explainer-sidecar\agent-explainer-sidecar.exe` sha256 | `f00527ca2c9ac56787b2f8794029013c15422717c371f40c4f982f8e136f093a`（10,403,499 B） |
| 与 `BUILD-INFO.json` 里 `sha256` 字段 | **逐字相同**（f00527ca…093a） |
| `BUILD-INFO.json` 版本/模块口径 | `version_default=0.5.0`、`hiddenimport_count=19`、`secretariat_in_hiddenimports=true`、`pyinstaller=6.22.3`、`layout=onedir`、`built_at=2026-11-12T01:51:23` |
| 程序目录 `_internal\var` | `ls: cannot access '…\_internal\var': No such file or directory`（**不存在**） |
| `resources\app.asar` → `sidecar.js` | `28: … featuresDir } = options` / `37: if (featuresDir) {` / **`38: args.push('--features', featuresDir)`** |
| `resources\app.asar` → `main.js` | `72: function featuresDir() {` / `79: return path.join(app.getPath('userData'), 'features');`（注释 68–70 行写明「默认 `<数据根>/var/features`，不能省」） |
| `resources\app.asar` → `package.json` | `"version": "0.5.0"` |
| `_internal\agent_explainer\web\ext\secretariat.js` | 24,396 B（**在便携版里面**） |
| `_internal\agent_explainer\web\index.html` | `15: <link rel="stylesheet" href="ext/secretariat.css">`、**`103: <script src="ext/secretariat.js"></script>`** |
| `_internal\agent_explainer\web\app.js` | `1090: secretariat: 'api/secretariat',`（面板→端点映射） |

> 这一步的意义：t3 的 asar 行号核对是在 `win-unpacked/` 做的，本报告是在**交付的 portable.exe 内部**重新解包核对，
> 结论一致 —— 发出去的便携版里确实带着 F1/F2 修复（`--features userData/features`）与 5.0 总裁办面板资源。

### 2.3 打包 sidecar 真跑（便携版里那个 Python 运行时）

```bash
cd /e/qa-tl050-portable
cp -r "…\app-64.7z 解包\resources\sidecar\agent-explainer-sidecar" sidecar
mkdir -p userdata/data userdata/features
./sidecar/agent-explainer-sidecar.exe --sidecar --port 18803 --mode local \
    --data-dir "E:/qa-tl050-portable/userdata/data" \
    --features "E:/qa-tl050-portable/userdata/features"      # 后台；stdout 为 GBK
```

| 实测 | 原始输出 |
|---|---|
| `/health` | `HTTP=200` `version= 0.5.0 status= ok mode= local` |
| `/api/secretariat/status` | `HTTP=200` `enabled= True`、`counts= {'identities': 0, …}`、`paths= {"root": "E:\\qa-tl050-portable\\userdata\\features"}` |
| `/ext/secretariat.js` | `HTTP=200 bytes=24396`，与 payload 内文件 `cmp` → **SAME** |
| `POST /api/secretariat/identities` | `HTTP=201`，`seq=1`、`prev_hash=000…000`、`entry_hash=5e1cd2a0a6b03675ad417fa2e08c13525844ae500beb8d4b88a23dd4b91014c9`、`fingerprint=58ea895ba3cd85ca` |
| 登记后 `counts` | `identities: 0 → 1`，`keyring: {"exists": true, "keys": 1, "writes": 1, "mode": "0o600"}` |
| 落点（文件实际路径） | `E:\qa-tl050-portable\userdata\features\secretariat\{identities.jsonl 644 B, identities.jsonl.lock 0 B, keyring.json 337 B, letters\}`；**程序目录 `_internal\var` 仍不存在** |

**便携版形态判定**：GUI 层「未验证 + 原因」（§2.1）；**运行时层 PASS**（§2.2/§2.3：交付件内部资源正确、打包 sidecar 三条端点全通、登记身份 201、账本落在 `--features` 指定目录）。

---

## 3. 容器形态（PASS）

### 3.1 引擎可用性（本会话实测，非转述）

```
docker version
 Client: Version 29.9.3 · API 1.56 · Context desktop-linux
 Server: Docker Desktop 4.99.0 (245822) · Engine 29.9.3 · OS/Arch linux/amd64
docker images
 tracelens:0.4.5  51d5b69752ce  244MB
 tracelens:0.5.0  6cb8417164f3  247MB
```

> 与遗留风险 ⑤ 对照：`release/0.5.0/DOCKER-BLOCKED.md` 记录的是**受限成员会话**打不开 `npipe:////./pipe/dockerDesktopLinuxEngine`（`permission denied`、`wsl.exe ACCESSDENIED`）。
> 本轮 qa 会话拿到了引擎（`Server` 段非空），所以**容器形态本轮真做了**，不是引用 t5 的日志。

### 3.2 镜像 LABEL / ID / 大小

```
docker image inspect tracelens:0.5.0 --format '{{index .Config.Labels "org.opencontainers.image.version"}} | {{.Id}} | {{.Size}}'
0.5.0 | sha256:6cb8417164f30140ba800cb950f23e0679e6983b8c86f91a803cc0d6cd586ff0 | 246561040

labels = {"org.opencontainers.image.description":"…","org.opencontainers.image.version":"0.5.0",
          "tracelens.build.by":"docker-build.sh","tracelens.mode":"team","tracelens.pdf-export":"1", …}
ENTRYPOINT = ["python","-m","agent_explainer","--mode","team","--host","0.0.0.0","--port","8765"]   CMD = null
（对照）tracelens:0.4.5 的 LABEL = 0.4.5
```

### 3.3 起容器（含一个**操作坑**的原始记录）

`ENTRYPOINT` 已含完整命令，再追加 `python -m agent_explainer …` 会被当成位置参数：

```
docker run -d --name qa-tl050 … tracelens:0.5.0 python -m agent_explainer --mode team --host 0.0.0.0 --port 8765 …
python -m agent_explainer.server: error: unrecognized arguments: python -m agent_explainer
```

正确姿势（**只追加**要覆盖的参数）：

```bash
docker run -d --name qa-tl050 -p 18801:8765 \
  -e TRACELENS_ADMIN_PASSWORD='QaPass-050-verify' \
  -v "E:/DEEPSEEK 黑奴/agent-explainer/.tmp/qa-docker/data:/app/var" \
  -v "E:/DEEPSEEK 黑奴/agent-explainer/.tmp/qa-docker/logs:/app/logs" \
  --user 10001:10001 --init tracelens:0.5.0 \
  --data-dir /app/var --features /app/var/features --demo zh
```

```
docker ps → qa-tl050  Up 10 seconds (healthy)
docker logs → records: 12 条演示数据 synthetic=12 mode=team · 持久化=/app/var/records.jsonl
              users: /app/var/auth/auth-state.json 会话 8 小时
              首次启动：已创建管理员 admin，初始口令来自环境变量 TRACELENS_ADMIN_PASSWORD
```

### 3.4 `/health` 与 **未登录 401 AUTH_REQUIRED**

```bash
curl -s -o .tmp/qa-docker/health.json -w "HTTP=%{http_code}\n" http://127.0.0.1:18801/health
```

```
HTTP=200   version= 0.5.0 status= ok mode= team
```

```bash
curl -s -o .tmp/qa-docker/sec-anon.json -w "HTTP=%{http_code}\n" http://127.0.0.1:18801/api/secretariat/status
```

```
HTTP=401
{"code": "AUTH_REQUIRED", "message": "Not signed in",
 "detail": {"hint": "登录后带 Cookie 或 X-TraceLens-Token"}, "request_id": "req-0.5.0-3"}
```

> team 模式鉴权口径复现：**未登录读 status 就是 401**（local 模式才公开）。这条与 t5 记录一致，但本轮是自己打出来的。

### 3.5 登录 → 带 CSRF 写 → 计数 0→1

```bash
curl -s -D login-headers.txt -c cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"QaPass-050-verify"}' http://127.0.0.1:18801/api/auth/login
```

```
HTTP=200
{"user": {"actor_id": "usr-8c6976e5b541", "role": "admin", …, "permissions": ["records.read","data.write",
  "ingest.token.manage","intervene.decide","audit.read","audit.export","settings.write","users.manage"],
  "must_change_password": true},
 "session": {"expires_in": 28800}, "csrf_token": "_-ZXFPs7xvtYRc0-G427nw"}
Set-Cookie: tl_session=…; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800
```

```
登录后 GET /api/secretariat/status      → HTTP=200  enabled=True mode=team counts 全 0
                                          paths={"root": "/app/var/features"}   ← 显式任务落在挂载卷内
POST /api/secretariat/identities         → HTTP=201
  {"identity": {"agent_id": "qa-docker-1", "fingerprint": "a78d88045e32faf6", "seq": 1,
                "prev_hash": "000…000",
                "entry_hash": "0edc670b9c8bc6b1f8c775c76d4a690f8a4bb6fc968fc2e98ecec124072b5b2b"}}
登记后 status                            → counts identities 0 → 1，keyring exists=true keys=1 mode 0o600
```

宿主卷上的实际落点（`Get-ChildItem` 等价）：

```
E:\…\.tmp\qa-docker\data\features\secretariat\
  -rw-r--r-- 640  identities.jsonl
  -rw-r--r--   0  identities.jsonl.lock
  -rw-r--r-- 335  keyring.json
  drwxr-xr-x      letters\
sha256: keyring ed4669cd8c8d8a8627a22a2b5a063755f746de3b8e254b49ee532739a543afdd
        identities 1e5d2707f2f775d739757c75fc4a1b6bc6f9f350f0b0ce0af455a5547144d1a7

docker exec qa-tl050 ls -la /app/var/features/secretariat/
  -rw------- 1 tracelens tracelens 335 keyring.json      ← 容器内 0600
  -rw-r--r-- 1 tracelens tracelens 640 identities.jsonl
```

### 3.6 离线包可用性（真 `docker load`）

```bash
docker load -i release/0.5.0/tracelens-0.5.0-image.tar
Loaded image: tracelens:0.5.0
docker image inspect … → sha256:6cb8417164f30140… | 0.5.0      # 与 IMAGE-INFO.json 的 image_id 逐字一致
IMAGE-INFO: version=0.5.0 size=246561040 tar_bytes=64029696
            tar_sha256=d93d1803c6ecff90aabcca5f76536f6329daa84671da695382fefd5b62803ab9（§5 复算一致）
```

**容器形态：PASS**（LABEL 0.5.0、`/health` 0.5.0、未登录 401、登录后 200、登记 201、落卷、离线 tar 可 load）。

---

## 4. 升级/重启持久性（回归验收，三形态各自独立重跑）

### 4.1 已钉口径的**默认落点**实测（t2 缺陷的根因）

```bash
# 程序目录换成一份新副本（prog2），不传 --features，只传 --data-dir
cd .tmp/qa-src/prog2 && python -m agent_explainer --mode local --port 8800 --no-browser \
    --data-dir "E:/…/.tmp/qa-src/data3"
curl -s http://127.0.0.1:8800/api/secretariat/status
```

```
paths= {"root": "E:\\DEEPSEEK 黑奴\\agent-explainer\\.tmp\\qa-src\\prog2\\var\\features", "exists": true}
identities.path= …\prog2\var\features\secretariat\identities.jsonl
keyring.path=   …\prog2\var\features\secretariat\keyring.json
ls data3/ → auth  records.jsonl  records.jsonl.lock          ← data3 下**没有** features 目录
```

**结论：`--features` 默认 = `<应用根>/var/features`，且不跟随 `--data-dir`** —— 与任务钉的口径逐字一致。
这就是 t2 报的缺陷（安装目录内不可写 / 升级冲掉账本），也是 `desktop/sidecar.js:38` 必须显式传 `--features` 的原因。

### 4.2 源码形态：登记 → 进程重启 + 程序目录整份替换 → 计数不减

```
登记前： count 0，features 目录空
POST 身份 qa-acc-1 → HTTP=201 seq=1 entry_hash=1e05e698f4aa1aeecf1ec73ea4221a13a34607894b1970acf36ade181396bd3e
登记后： counts identities=1；identities.jsonl 634 B；keyring.json 332 B（0600）；keyring sha256=8706ad1ec21d5c8f525aab8c67d1ac1861490a8ff9f3ba476bd603ca6471d5a9
```

```bash
# 模拟升级：程序目录整份换成新副本（.tmp/qa-src/prog2），userData/features 与 data 保持原路径
cd .tmp/qa-src/prog2 && python -m agent_explainer --mode local --port 8799 --no-browser \
    --data-dir "…\.tmp\qa-src\data" --features "…\.tmp\qa-src\features"
```

```
/health                → HTTP=200 version=0.5.0
counts                 → {'identities': 1, 'active_identities': 1, …}          ← 不减
chains.identities      → ok=true count=1 head=1e05e698…bd3e（与升级前**逐字相同**）
keyring                → exists=true keys=1 writes=0 mode=0o600
keyring sha256         → 8706ad1ec21d5c8f525aab8c67d1ac1861490a8ff9f3ba476bd603ca6471d5a9（**逐字相同**）
GET /api/secretariat/identities → count=1 ['qa-acc-1']
程序目录 _internal/var → 源码形态无 _internal；程序目录下 var/ 仅由「默认落点探测」（4.1）在 prog2 下产生，与 userData 无关
```

### 4.3 便携版（打包 sidecar）：程序目录整份替换 → 计数不减、keyring 逐字不变

```bash
# 升级前
E:\qa-tl050-portable\userdata\features\secretariat\{identities.jsonl 644 B, keyring.json 337 B}
keyring sha256 = dd93390c4280fb60aaa57be0e9a883387b5b8832908de67b69288cb4a9f31fc0
程序目录 _internal\var → 不存在

# 模拟升级：rm -rf sidecar 后从便携版 payload 重新整份解包
rm -rf sidecar && cp -r "…payload\resources\sidecar\agent-explainer-sidecar" sidecar
./sidecar/agent-explainer-sidecar.exe --sidecar --port 18803 --mode local \
    --data-dir …\userdata\data --features …\userdata\features
```

```
/health           → version= 0.5.0 status= ok
counts            → {'identities': 1, …}                                   ← 不减
chains.identities → ok=true count=1 head=5e1cd2a0a6b03675ad417fa2e08c13525844ae500beb8d4b88a23dd4b91014c9（不变）
identities 列表   → count=1 ['qa-packaged-1']
keyring sha256    → dd93390c4280fb60aaa57be0e9a883387b5b8832908de67b69288cb4a9f31fc0（**逐字相同**）
程序目录 _internal\var → `ls: cannot access …: No such file or directory`（升级前后都不存在）
```

### 4.4 容器形态：删容器重建、保留卷 → 计数不减

```
删除前： counts.identities=1，head=0edc670b9c8bc6b1f8c775c76d4a690f8a4bb6fc968fc2e98ecec124072b5b2b
docker rm -f qa-tl050  → 卷内文件仍在（identities.jsonl 640 B / keyring.json 335 B）
docker run -d --name qa-tl050b … 同样的 -v data:/app/var --features /app/var/features …
  → Up 10 seconds (healthy)；重新 login HTTP=200
重建后： counts={'identities': 1, …}；head 逐字相同 0edc670b…b2b；keyring exists=true keys=1 writes=0
        keyring sha256 = ed4669cd8c8d8a8627a22a2b5a063755f746de3b8e254b49ee532739a543afdd（逐字相同）
程序目录：docker exec … ls -d /app/agent_explainer/var → No such file or directory（应用根下没有游走的 var）
```

### 4.5 三形态持久性汇总

| 形态 | 升级动作 | counts | 链头 | keyring sha256 | 程序目录 `_internal/var` |
|---|---|---|---|---|---|
| 源码 | 程序目录换新副本 + 进程重启 | 1 → **1** | 1e05e698…bd3e → 同 | 8706ad1e… → 同 | 无 `_internal`（默认落点见 4.1） |
| 便携（打包 sidecar） | 程序目录整份重解包 | 1 → **1** | 5e1cd2a0…14c9 → 同 | dd93390c… → 同 | **不存在**（前后都是） |
| 容器 | 删容器 + 同卷重建 | 1 → **1** | 0edc670b…b2b → 同 | ed4669cd… → 同 | `/app/agent_explainer/var` 不存在 |

**升级/重启持久性：PASS（3/3 形态，独立重跑，未照抄 t5 的 18/18）。**

---

## 5. 校验链（逐条重算，全部以实测哈希为准）

### 5.1 四个 SHA256SUMS 文件逐条复算

| 文件 | 基线目录 | 结果 |
|---|---|---|
| `release/SHA256SUMS.txt` | `release/` | **2/2 通过** |
| `release/0.5.0/SHA256SUMS.txt` | `release/0.5.0/` | **1/1 通过** |
| `release/tracelens-0.5.0-release/SHA256SUMS.txt` | 包根 | **2/2 通过** |
| `release/tracelens-0.5.0-release/SHA256SUMS-bundle.txt` | 包根 | **23/23 通过** |
| `release/SHA256SUMS-release.txt` | `release/` | **2/2 通过** |

关键行（实测值）：

```
37cf69638a21368631eaccee3cb7c180a365f6b4265ecbd25ac7747a60e0395d  TraceLens-0.5.0-win-x64-Setup.exe      151,838,192 B
8bdc55a089dd678d6be282019471798187c6ecb914a89d5900838716558fdd52  TraceLens-0.5.0-win-x64-portable.exe   137,883,824 B
d93d1803c6ecff90aabcca5f76536f6329daa84671da695382fefd5b62803ab9  tracelens-0.5.0-image.tar               64,029,696 B
66a50950f42228417dbc7128662ff018a5f697662600d356bbc46e22d6cc0ee4  tracelens-0.5.0-release.zip            358,989,166 B
0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab  tracelens-0.4.5-release.zip            356,074,424 B
```

### 5.2 0.4.5 行逐字未变

- 实测 `sha256(release/tracelens-0.4.5-release.zip)` = `0c5d5552aa78c6b2ab0c6c2d79ffa3baae1028f191d63bdb74410aff442139ab`，与 `release/SHA256SUMS-release.txt` 第 1 行**逐字相同**；
- 与 t5 落档的 `docs/audit/v050-docker.md:281`、`docs/audit/v050-release.md:209/233` 记录的基线哈希**逐字相同**；
- 文件 mtime 仍是 `Nov 11 09:34`（未被打包脚本重写）。

### 5.3 zip 条目数与目录一致（t5 重打过的那份）

```
release/tracelens-0.5.0-release.zip
  条目总数 = 24（其中文件 24，目录条目 0）；压缩方式 = [0]（全 ZIP_STORED）
  release/tracelens-0.5.0-release/ 目录内文件数 = 24
  仅 zip 有 = []      仅目录有 = []
  总解压字节 = 358,986,196（zip 本体 358,989,166，差值=头/目录开销）
```

> 与任务里「t5 先报 22 条目 / 294,953,425 B、镜像回补后以 `SHA256SUMS-release.txt` 第 2 行为准」对得上：
> 现为 **24 条目**（回补 `docker/{IMAGE-INFO.json,SHA256SUMS.txt,tracelens-0.5.0-image.tar}` 后），
> 第 2 行哈希 `66a50950…0ee4` = 实测 zip 哈希。

### 5.4 两个 exe 的签名状态 vs `SIGNATURE-STATUS.json`（逐字一致）

```
Get-AuthenticodeSignature release/TraceLens-0.5.0-win-x64-Setup.exe
  Status=UnknownError    SignerThumbprint=<redacted-thumbprint>
  SignerSubject=CN=TraceLens Self-Signed (Local Only)    Bytes=151838192
Get-AuthenticodeSignature release/TraceLens-0.5.0-win-x64-portable.exe
  Status=UnknownError    SignerThumbprint=<redacted-thumbprint>
  SignerSubject=CN=TraceLens Self-Signed (Local Only)    Bytes=137883824
（控制台为 GBK，StatusMessage 中文显示为乱码；json 里存的是英文原文，见下）
```

`SIGNATURE-STATUS.json`（4,918 B，`sha256=875f84f0f19aecaf9229c633e07d4d26311e5f5aee2f976d444697a823dbeeae`）：

```
BOM: False            U+FFFD 计数: 0            中文（trust_note/signed_by）可读
signed_count = 0      signatures_applied = 2    unsigned = True
trust_state = 'self_signed_root_untrusted'      artifact_count = 2      status_counts = {'UnknownError': 2}
status 集合 = ['UnknownError']        ← 没有被写成 Valid（写成 Valid 按任务要求直接 FAIL，未发生）
artifacts[0] Setup.exe    151,838,192  37cf69638a213686…0395d  status=UnknownError  signature_present=true
             status_message="A certificate chain processed, but terminated in a root certificate which is not trusted by the trust provider"
artifacts[1] portable.exe 137,883,824  8bdc55a089dd678d…fdd52  status=UnknownError  signature_present=true
两份（release/SIGNATURE-STATUS.json 与包内同名文件）**字节相同**；两个 artifact 的 path 都存在且实测 sha256 与 json 一致
```

**口径自洽性**：`signed_count=0`（只算 Valid/NotTrusted）+ `signatures_applied=2`（确实落了签名）+ `unsigned=true`
三者**不矛盾而是自签名的真相**：文件里签了（SignatureType=Authenticode、签发人指纹在），但证书链终止于不受信任的根，Windows 判 `UnknownError`。

> **基线预期（要写进发布说明的口径）**：自签名证书链终止于不受信任的根 → Windows 判 `UnknownError`（或 `NotTrusted`）是**预期基线**，
> 不是打包失败。消警只有两条路：换 OV/EV 代码签名证书，或以管理员身份把该证书导入 `LocalMachine\Root` + `LocalMachine\TrustedPublisher`。
> 另：默认不加时间戳，证书过期后签名验证会转为失效。

**校验链：PASS。**

---

## 6. 闸门复跑

### 6.1 全量套件（权威跑：前台、`TMP/TEMP/TMPDIR=<repo>\.tmp`）

```bash
export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"
python -m unittest discover -s tests -p "test_*.py" -q
```

```
EXIT=0
Ran 954 tests in 164.039s
OK (skipped=3)
[交叉校验] 100 组随机密钥/签名与 cryptography.hazmat 双向一致
[groups] 1000 条聚合实测：2.8 ms（规格 ≤ 2000 ms）
```

与 t4/t5 的 `954 / OK / skipped=3 / failures=0` **逐字一致，零新增红**。

#### 6.1.1 我在同一任务里先跑出过 3 条红，逐条点名 + 归因（不藏）

第一次是**后台任务**跑同一命令，结果 `FAILED (failures=1, errors=2, skipped=3)`：

| # | 用例 | 报错 | 归因（有实验证据） |
|---|---|---|---|
| 1 | `test_e2e.ArtifactTests.test_sidecar_exe_runs` | `PermissionError: [WinError 5] 拒绝访问: 'C:\Users\Windows\AppData\Local\Temp\dsh-win-bash-9856-acb2ab55\agent-explainer-tests\e2e-exe\data\product-created'` | **环境（临时目录 ACL）**，非产品回归。该次 scratch 根落在 DSH **每次调用私有 TEMP**；打包 exe 在该目录里建子目录被拒。**已确定性复现**：把 TMP/TEMP 指到 `C:\Users\Windows\AppData\Local\Temp\dsh-win-bash-9856-f833f5b3` 单跑该用例 → 同一个 WinError 5；指到工作区 `.tmp` → 通过 |
| 2 | `test_settings_api.SettingsPermissionTests.test_editor_and_viewer_cannot_write` | `ConnectionAbortedError: [WinError 10053] 你的主机中的软件中止了一个已建立的连接` | **瞬时 socket 中断**（当时同机并发：3 个源码服务 + 1 个容器 + 便携 exe + 7z 解包 466MB）。复跑（单独 + 全量）均通过 |
| 3 | `test_crypto_sig.TestPayloadAndBase64.test_public_key_b64_and_verify_b64` | `AssertionError: True is not false` @ `tests/test_crypto_sig.py:367` | **测试自身的概率性断言缺陷**（F3）。断言构造的「变异签名」是 `signature[:-1] + b"\x00"`：当原签名末字节本来就是 `0x00`（≈1/256）时，变异结果**与原签名逐字节相同**，`verify_b64` 当然返回 True。已确定性复现（见下） |

F3 的确定性复现（6000 次签名，命中末字节 `0x00` 即触发）：

```
第 33 次：签名末字节 = 0x00；signature[:-1]+b'\x00' 与原签名逐字节相同 → verify_b64 = True
第 50 次：同上 → True
第 67 次：同上 → True
（同一用例连跑 30 次：失败 0 次 —— 与 1/256 的量级一致）
```

**判定**：这 3 条红**都不是产品回归**（1 条环境、1 条瞬时、1 条测试断言本身）；权威跑 954/OK 已闭合。
但 F3 会在任何一次 CI/验收里随机制造假红，必须修（C3）。

### 6.2 `python tools/i18n_lint.py`

```
[PASS] 字典一致性 —— zh 1115 键 / en 1115 键 / 27 个域；error 键 168 条
[PASS] 禁止硬编码文案 —— 扫描 app.js, index.html, styles.css；CJK 命中 0，句子字面量 0
[PASS] 错误码覆盖 —— 168 个码；有 <域>.error.<code> 键 75 个；无键 93 个（允许回退 message）
[PASS] 生成物一致 —— embed.js / keys.md 与字典同步
[PASS] 双语演示夹具 —— 12 条 x 2 套；英文集中文字符 0
[PASS] 检查项 5 项：通过 5，失败 0
EXIT=0
```

### 6.3 `python tools/gen_api_contract.py --check`

```
[PASS] 路由抽取 —— 41 条 (path, verb)，43 个 API_PATH 常量，公开路径 5 条，显式 405 组 3 组
[PASS] 权限矩阵 —— 4 个角色 / 8 个能力：admin=8, editor=5, local=7, viewer=3
[PASS] 错误码 —— 共 168 个，有键 75 个，无键 93 个
[FAIL] 前端调用面 —— 界面调用后端没有的路由：['/api/analytics', '/api/compliance', '/api/evidence', '/api/groups',
        '/api/preferences', '/api/rollback', '/api/shares', '/api/suggestions']
[PASS] 可重复生成 —— 磁盘内容与本次生成字节一致（39246 字节）
[PASS] docs/03 §6.5 逐条比对 —— 文档 34 行 / 36 条路径 与契约 36 条路径 一一对应，方法列一致，无 ⚠ 行
[FAIL] 契约检查未通过：1 项
EXIT=1
```

与任务要求**逐字对上**：唯一失败 = **8 个非总裁办端点**（均为 5.0 之前就存在的前端「占位调用面」问题，与总裁办无关；总裁办 8 条路由在契约里 6 个检查项全过）。

### 6.4 `node selfcheck.mjs`

```
… [PASS] 92  4.12 H-04: the card hint is its own full-width row, the expand label keeps its column (1400px repro)
self-check: 92/92 passed (report: web/selfcheck-report.json)
EXIT=0
```

> 副作用声明：`selfcheck.mjs` 每次运行都会重写 `agent_explainer/web/selfcheck-report.json`（闸门命令自带行为，不是本轮手改）。
> 与 0.5.0 打包件内的同名副本比对：只有 `generated_at` 与部分 detail 文本变化，`passed=92 / total=92` **结果相同**。详见 §12。

**闸门：PASS（4/4，含对 3 条首跑红的逐条归因）。**

---

## 7. 版本单一来源核对（五处 = 0.5.0）

| # | 位置 | 实测 | 判定 |
|---|---|---|---|
| 1 | `agent_explainer/server.py:167` | `SERVER_VERSION = "0.5.0"` | PASS |
| 2 | `agent_explainer/__init__.py:87` | `__version__ = "0.5.0"`（注释：与 `server.SERVER_VERSION` 同源） | PASS |
| 3 | `desktop/package.json:4` / `desktop/package-lock.json:3,9` | `"version": "0.5.0"` / 两处 `0.5.0` | PASS |
| 4 | 镜像 `LABEL org.opencontainers.image.version` | `0.5.0`（`tracelens:0.5.0`，id `sha256:6cb84171…ff0`） | PASS |
| 5 | `/health` 上报 `version` | 源码 `0.5.0` · 容器 `0.5.0` · 打包 sidecar `0.5.0` | PASS |

附加同源点（都对得上）：`Dockerfile:62 ARG TRACELENS_VERSION=0.5.0`；`docker-compose.yml` build args 默认 `0.5.0`；
`tools/build_sidecar.py:66 DEFAULT_VERSION = "0.5.0"`；打包件 `BUILD-INFO.json version_default=0.5.0`；
包内 `app.asar/package.json version=0.5.0`；产物文件名 `TraceLens-0.5.0-win-x64-{Setup,portable}.exe`。

---

## 8. 三条交付承诺逐条判定

| 承诺 | 判定 | 证据 |
|---|---|---|
| ① 总裁办面板在便携版里**可见** | **有条件通过** | 便携版 payload 内 `web/ext/secretariat.js`（24,396 B）、`index.html:103` 引用它、`index.html:15` 引入 `secretariat.css`、`app.js:1090` 端点映射、i18n `zh.json/en.json` 都在；打包 sidecar 起服务后 `GET /ext/secretariat.js` = 200 且与 payload 文件 `cmp` 相同。**但「渲染出来可见」这一步在 DSH 会话不可判**（§2.1 GUI exit 0），故「有条件」 |
| ② sidecar 收集到 secretariat | **PASS** | `BUILD-INFO.json`：`hiddenimport_count=19`，清单含 `agent_explainer.secretariat` / `crypto_sig` / `feature_store` / `groups` / `qr` / `rollback` / `sharing` / `sdk`，`secretariat_in_hiddenimports=true`；打包 sidecar 真跑 `/api/secretariat/status` 200 enabled=true、登记身份 201（§2.3） |
| ③ 签名状态报告如实 | **PASS** | §5.4：`UnknownError×2`、`signed_count=0`、`signatures_applied=2`、`unsigned=true`、`trust_state=self_signed_root_untrusted`、`status_counts` / `artifact_count` / `signed_by` 齐；与 `Get-AuthenticodeSignature` 逐字一致；无 BOM、无 U+FFFD、中文可读；**没有**把 `UnknownError` 写成 `Valid` |

---

## 9. 残留 0.4.5 的活动文件清单

任务口径：历史快照（`docs/05-test-*`、`release/tracelens-0.4.5-release/**`）不算缺陷。以下按「活动文件」逐条分类。

### 9.1 算缺陷（会给用户/维护者错误指引）

| 文件 | 处数 | 残留内容 | 判定 |
|---|---|---|---|
| `README.md` | 24 | 第 39/40/68/94/96/119/120/325/326/329/331/342/343/346/354 行的**下载与校验指引仍是 0.4.5 产物**（`release/TraceLens-0.4.5-win-x64-Setup.exe` 等）；第 150/155/406/407 行 docker 命令仍是 `tracelens:0.4.5` / `release/0.4.5/…tar`；第 356 行写「`release/tracelens-0.4.5-release.zip`（356,073,874 B，SHA256 `ef837389db3b…`）」——**与实测 356,074,424 B / `0c5d5552…` 都不符** | **缺陷（F2，medium）** |
| `docs/06-faq.md` | 2 | 第 36 行静默安装 `.\TraceLens-0.4.5-win-x64-Setup.exe /S`；第 202 行 `docker load -i release/0.4.5/tracelens-0.4.5-image.tar` | 缺陷（F2，随包发布） |
| `docs/06-user-guide.md` | 2 | 第 31/32 行告诉用户拿 `TraceLens-0.4.5-…Setup.exe` / `…portable.exe` | 缺陷（F2，随包发布） |
| `docs/NOTICE-third-party.md` | 7 | 第 35–38 行、第 68–70 行的第三方清单与命令写的是 0.4.5 产物/镜像 | 缺陷（F2，随包发布） |
| `docs/05-real-user-kit.md` | 8 | 第 88 行预期 `version=0.4.5`；第 112/115/117/127/131 行文件名全是 0.4.5 | 缺陷（F2，实测套件指引） |
| `docs/05.4-docker.md:459` | 1（该行属 27 处中唯一「事实错误」而非历史） | 「**0.5.0 镜像与离线 tar ❌ 未产出（环境阻塞）**」 | **缺陷（F1，medium）**：镜像已产出、已回补进总包、`docker load` 通过，该行与事实矛盾 |
| `release/0.5.0/DOCKER-BLOCKED.md` | 全文 | 「没有 0.5.0 镜像、没有 `tracelens-0.5.0-image.tar`、没有 `IMAGE-INFO.json`」 | **缺陷（F1，medium）**：同目录下三件都在、tar 可 load；该文件是上一轮阻塞记录的**过时残留**（未被收进 zip，但留在发布目录里） |
| `tools/sign_and_hash.ps1:251` | 1 | `-CheckEncoding` 固定清单第三项是 `release\tracelens-0.4.5-release\SIGNATURE-STATUS.json` | **缺陷（F5，low）**：0.5.0 包（`tracelens-0.5.0-release`）不在编码审计范围内 |

### 9.2 不算缺陷（历史记录 / 迁移说明 / 注释 / 快照）

| 文件 | 处数 | 性质 |
|---|---|---|
| `CHANGELOG.md` | 6 | `## [0.4.5]` 历史条目 + 0.5.0 条目里写 `0.4.5 → 0.5.0` 的迁移说明 |
| `docs/05.3-release-checklist.md` | 14 | 0.4.5 时点的实测记录 + §11「0.5.0 口径更新」before→after 表 |
| `docs/05.4-docker.md`（除 459 行外） | 26 | 0.4.5 历史实测（§12 有 0.5.0 记录） |
| `docs/03-architecture.md` | 2 | 第 710 行路径示例、第 825 行「产品基线 0.4.5 → 0.5.0」 |
| `docs/07-secretariat-and-extensions.md:8` | 1 | 版本迁移说明 |
| `docs/09-subtask-plan.md:70` | 1 | 历史任务计划 |
| `docs/05-test-plan.md` / `docs/05-test-report.md` | 1 / 2 | 历史快照（任务明示不算缺陷） |
| `scripts/docker-build.sh:7` | 1 | 注释里的 `TRACELENS_VERSION=0.4.5` 用法示例 |
| `tools/build_sidecar.py:437,465` | 2 | 注释引用 0.4.5 的历史 bug 作为判据依据 |
| `docs/audit/v050-*.md` | 29/17/15/7/7 | 审计实录里的 before→after |
| `.snapshot/R1-R4/*` | 4 文件 | 快照副本（`SERVER_VERSION=0.4.5`），非活动代码；建议后续别入库 |
| `desktop/build/recs.json` | 1 | 构建中间产物里的旧 `request_id: req-0.4.5-23` |
| `release/**` 0.4.5 归档 | — | 任务明示不算缺陷 |

**版本口径小结**：**五处活动版本源全部 0.5.0**；残留 0.4.5 中**只有 §9.1 那 8 项属缺陷**，其余均为历史记录/注释/快照。

---

## 10. 遗留风险清单（五条在案 + 本轮新增四条）

### 10.1 任务要求在案的五条（本轮逐条复核，全部仍然成立）

| # | 风险 | 本轮复核结果 |
|---|---|---|
| ① | `tools/sign_and_hash.ps1:448` —— `Set-AuthenticodeSignature` 处理 `.zip` 后留句柄，紧接着 `Get-AuthenticodeSignature` 抛 IOException「being used by another process」，在 `$ErrorActionPreference=Stop` 下整个签名流程崩在 448 行，`SHA256SUMS.txt` 与 `SIGNATURE-STATUS.json` 都写不出来 | **在案**。`sed -n '440,460p'` 确认 448 行就是 `$check = Get-AuthenticodeSignature -LiteralPath $a.FullName`，位于 `Set-AuthenticodeSignature` 之后、无 retry/释放。当前靠「签名清单只放两个 PE 产物」绕开。建议加 `-NoSignExtensions`（默认 `.zip/.7z`） |
| ② | `powershell -File` 传 `-Files @('a','b','c')` **不求值成数组**，剩余 token 串到第一个位置参数（`$OutDir`），会把 `SHA256SUMS.txt` 当目录写 | **在案**。`desktop/package.json:20` 现为 `powershell -NoProfile -ExecutionPolicy Bypass -File ../tools/sign_and_hash.ps1`（无 `-Files` 数组传参，规避了）；建议 runner 化（PowerShell 脚本内自带清单 / 参数用逗号串解析） |
| ③ | 壳窗口 / 便携版界面 / NSIS 安装的实测必须在**用户桌面会话**做，DSH 会话不可判 | **在案**，本轮再次实证：`EXITED exitcode=0`、`out.txt`/`err.txt` 均 0 字节、无监听端口（§2.1） |
| ④ | `scripts/verify_e2e.py` 里 `secretariat` / `identities` / `总裁办` **各 0 次** ⇒ 首启 42 项验收**不含 5.0 总裁办断言** | **在案**（本轮实测 `grep -c` = 0 / 0 / 0；`release/0.5.0/docker-verify.log:205` 为 `-- fresh：42/42 通过`）。补丁建议仍在 `docs/audit/v050-docker.md §7` |
| ⑤ | 镜像闭合依赖**可达的 docker 守护**：受限成员会话下 docker CLI 对 `npipe:////./pipe/dockerDesktopLinuxEngine` 被拒（`permission denied`），`wsl.exe` 也 `ACCESSDENIED` | **在案，但本轮不适用**：qa 会话 `docker version` 的 `Server` 段非空（Docker Desktop 4.99.0 / Engine 29.9.3），故 §3/§4.4 是**真做**的。风险本身对受限会话依旧成立 |

### 10.2 本轮新增（4 条）

| # | 风险 | 依据 |
|---|---|---|
| ⑥ | **全量套件会把总裁办账本写进仓库 `var/features`**（源模式 `Sidecar` 只传 `--data-dir`、不传 `--features`，默认落点=应用根 `var/features`） | 实测 `var/features/secretariat/heads.jsonl`：`2,721,000 B @04:02` → `4,171,608 B @04:27`（跨我两次全量 discover）。`var/` 不参与打包，但会污染本机账本；建议测试统一传 `features_root` 临时目录（同 F4） |
| ⑦ | **`test_e2e` 的 exe 用例对临时目录 ACL 敏感**：scratch 根落在 DSH 每次调用的私有 TEMP 时，打包 exe 建子目录被拒（WinError 5）→ 环境性假红 | 已确定性复现（§6.1.1 #1）。建议：exe 用例固定把 scratch 指到工作区 `.tmp`，或在 `scratch.py` 探针里让**子进程**也试建一次子目录 |
| ⑧ | **`tests/test_crypto_sig.py:367` 概率性断言**（≈1/256 假红） | §6.1.1 #3 + 确定性复现 |
| ⑨ | **`/health.endpoints` 不含 `/api/secretariat/*`**（18 条核心路由），与契约 41 条路由不一致；客户端若靠它做能力发现会看不到 5.0 路由 | §1.1（info 级，不影响本轮验收路径） |

---

## 11. 缺陷清单（severity / 复现命令 / 影响）

| ID | severity | 缺陷 | 复现命令 | 影响 | 建议 |
|---|---|---|---|---|---|
| F1 | **medium** | 文档与事实矛盾：`docs/05.4-docker.md:459` 与 `release/0.5.0/DOCKER-BLOCKED.md` 声称「0.5.0 镜像/离线 tar 未产出」，而 `release/0.5.0/{tracelens-0.5.0-image.tar,IMAGE-INFO.json,SHA256SUMS.txt}` 都在、`docker load` 通过、总包已回补 | `grep -n "0.5.0 镜像与离线 tar" docs/05.4-docker.md`；`head -12 release/0.5.0/DOCKER-BLOCKED.md`；`docker load -i release/0.5.0/tracelens-0.5.0-image.tar` | 随包发布的文档告诉用户「没有 0.5.0 镜像」，用户可能去用 0.4.5 的 tar；验收人若只读文档会误判发布件缺失 | 更新 `docs/05.4-docker.md` 该行为「已产出 + 实测数字」；删除/归档 `release/0.5.0/DOCKER-BLOCKED.md`（保留 `docs/audit/v050-docker.md` 作为历史） |
| F2 | **medium** | 用户可见文档仍以 **0.4.5** 作为交付指引：`README.md`（下载/校验/版本表/docker 命令 24 处，其中第 356 行的 0.4.5 总包「356,073,874 B / ef837389db3b…」与实测「356,074,424 B / 0c5d5552…」都不符）、`docs/06-faq.md`(2)、`docs/06-user-guide.md`(2)、`docs/NOTICE-third-party.md`(7)、`docs/05-real-user-kit.md`(8) | `grep -n "0\.4\.5" README.md docs/06-faq.md docs/06-user-guide.md docs/NOTICE-third-party.md docs/05-real-user-kit.md` | 用户照 README/用户手册找不到 0.5.0 的文件名与校验值；按 README 第 356 行核对 0.4.5 总包会对不上哈希（README 数字本身就是错的） | 把交付指引段的文件名 / 字节数 / 哈希换成 0.5.0 实测值（`release/SHA256SUMS*.txt` 为准），历史段落保留并标注「0.4.5 时点」 |
| F3 | **low**（影响闸门可信度） | `tests/test_crypto_sig.py:367` `assertFalse(cs.verify_b64(pub_text, message, cs.b64e(signature[:-1] + b"\x00")))`：末字节为 `0x00` 时「变异」签名 == 原签名 | `python -c` 循环签名至末字节 `0x00`（本轮 33/50/67 次命中）→ `verify_b64(...)` 返回 `True`；或全量 discover 反复跑，约 1/256 出现该红 | 随机假红，会把「954 全绿」的验收结论搞脏；也会掩盖真实回归 | 改成翻转**中间**某字节（如 `sig[:16]+bytes([sig[16]^0xFF])+sig[17:]`），或显式断言末字节非 0 再变异 |
| F4 | **low** | 测试污染仓库运行时目录：`tests/test_e2e.py` 的源模式 `Sidecar` 只传 `--data-dir`，`--features` 落到默认 `<repo>/var/features` ⇒ 每次全量套件往 `var/features/secretariat/heads.jsonl` 追加 | 跑全量 discover 前后 `ls -l var/features/secretariat/heads.jsonl`（实测 2,721,000 → 4,171,608 B） | 本机总裁办账本被测试数据污染；与 `scratch.py` 文档承诺（测试不写仓库 var/）不符 | `Sidecar` 增加 `--features <scratch>/features`，与 `--data-dir` 并列 |
| F5 | **low** | `tools/sign_and_hash.ps1:251` `-CheckEncoding` 的固定清单第三项指向 `release\tracelens-0.4.5-release\SIGNATURE-STATUS.json` | `sed -n '245,256p' tools/sign_and_hash.ps1`；`powershell -File tools/sign_and_hash.ps1 -CheckEncoding` | 0.5.0 总包内 `SIGNATURE-STATUS.json` 未被编码审计；若 0.4.5 归档被清理，审计会对缺失文件报错/漏检 | 清单改成按「当前发布目录」枚举（`release\` + `release\tracelens-0.5.0-release\`），或加 `-ArtifactsDir` 参数 |
| F6 | **low**（环境边界，非产品缺陷） | 便携版 GUI / NSIS 安装 / 壳窗口未验证 | `Start-Process TraceLens-0.5.0-win-x64-portable.exe` → `EXITED exitcode=0`，`out/err` 0 字节，无监听端口 | 便携版「界面可用」这条只能等用户桌面会话；本轮以 payload + 打包 sidecar 实证补足运行时部分 | 在用户桌面会话补测；补测清单见 §0.1 C1 |
| F7 | **info** | `/health.endpoints` 不含 `/api/secretariat/*` | `curl -s …/health | python -c "import json,sys;print(len(json.load(sys.stdin)['endpoints']))"` → 18 | 若存在「按 `/health` 发现能力」的客户端，会漏掉 5.0 的 8 条路由 | 把 secretariat 路由并入 `endpoints` 或新增 `capabilities.secretariat` 标注 |

> 说明：F1/F2 属文档；F3/F4/F7 属测试与观测面；F5 属工具；F6 属环境。**没有任何一条落在发布件本体（exe / zip / 镜像 / 校验和 / 签名状态）上。**

---

## 12. 本轮写操作与只读边界声明

本轮**只创建/修改了一个文件**：`docs/17-release-0.5.0-acceptance.md`（本文件）。除此之外：

| 动作 | 性质 | 说明 |
|---|---|---|
| `agent_explainer/web/selfcheck-report.json` 被重写 | **闸门命令自带副作用**（非手改） | 任务要求跑 `node web/selfcheck.mjs`，该脚本总会写报告。与 0.5.0 打包件内副本比对：仅 `generated_at`（`2026-11-11T17:38:27.218Z` → `2026-11-11T20:25:30.365Z`）与部分 detail 文本不同，`passed=92 / total=92` 结果相同；该文件不在任何 `SHA256SUMS*` 里，不影响校验链 |
| `var/features/secretariat/heads.jsonl` 增长 | **测试副作用**（F4） | 2,721,000 → 4,171,608 B；`var/` 是运行时目录，不参与打包、不在校验链内 |
| `.tmp/**` 下新建大量临时目录（源码/打包 sidecar/容器数据/7z 解包/日志） | 测试草稿 | 全部在仓库 `.tmp` 与 `E:\qa-tl050-portable`；`E:\qa-tl050-portable` 为本轮 ASCII 运行目录（便携版边界要求） |
| `docker run/rm/load` | 运行时 | 容器 `qa-tl050`/`qa-tl050b` 已删除；`docker load` 只读取离线 tar，未改镜像内容（id 与 `IMAGE-INFO.json` 逐字一致） |
| 发布件 / 校验和 / 签名 / 文档 | **只读** | 未修改 `release/**`、`desktop/build/**`、`tools/**`、`tests/**`、`docs/**`（除本文件） |

> 环境备注：`docker` 引擎在本 qa 会话**可用**（`Server` 段非空），因此 §3/§4.4 的容器验收是本轮真做，不是引用 t5 的日志；
> 受限成员会话（T1–T4 遇到的那条边界）依旧打不开 npipe —— 见 §10.1 ⑤。

---

---

## 13. R2 复核（追加，2026-11-12；独立复核 t7 文档口径 + t8 测试/工具）

> 本节是 **T5 报告的追加节**，不修改上文任何结论（上文是 0.5.0 首次验收的实录）。
> R2 两轮（t7 文档口径、t8 测试/工具）的独立复核全文见
> [docs/18-release-0.5.0-followup.md](./18-release-0.5.0-followup.md)（含逐条命令与原始输出）。
> 复核人：`qa`，时间 05:28–05:45，**只读复核**（唯一例外：§13.5 的 F3 探针，已按字节还原）。

### 13.1 结论

**R2 判「有条件 PASS」；打 tag 前必须闭合 D1、D2。**

| 项 | 结果 |
|---|---|
| F1（镜像/离线包「未产出」口径） | **闭合**：`docs/05.4-docker.md:467` 改实态（`sha256:6cb84171…` / 246,561,040 B / LABEL 0.5.0 / tar 64,029,696 B / `d93d1803…` / docker-verify 全部通过），`release/0.5.0/DOCKER-BLOCKED.md` 改「已闭合」归档；**三条环境边界（npipe `permission denied` / `wsl ACCESSDENIED` / `bash signal pipe`）原样保留** |
| F2（0.4.5 指引与哈希） | **闭合**：859 个发布文件的 sha256 建索引后反查文档 29 处 64-hex，19 处命中实测文件；未命中的 21 条逐条定性（docker config digest / 0.4.5 历史 / 旧错值 / 两轮重打留痕 / 不可复算白名单）；README:356 旧错值已改实测 **356,074,424 B / `0c5d5552…139ab`** 并标历史快照 |
| 总包一致性 | **本体 PASS**（25 条目 == 目录 25 文件、全 ZIP_STORED、包内 == 仓库逐字节 25/25、`SHA256SUMS-bundle.txt` 24 条全过 / 无 BOM / CRLF、0.4.5 行 `0c5d5552…` 逐字未变、zip 哈希 `41b9beab…` 只认 `release/SHA256SUMS-release.txt` 第 2 行且文档 0 处自引用）；**但包内缺 `docs/audit/v050-tests-tools.md`（D2）** |
| 发布件未被偷改 | **PASS**：两 exe 仍 `37cf6963…e0395d` 151,838,192 B / `8bdc55a0…fdd52` 137,883,824 B，`Get-AuthenticodeSignature` = `UnknownError` + 指纹 `DF1CB56A…`，与 `SIGNATURE-STATUS.json` 逐字一致（**未被写成 Valid**） |
| F3 | **闭合**：`test_crypto_sig` 连跑 **50 次 0 失败**；临时探针把变异改成空操作 → 守卫 `assertNotEqual` 判红（证明断言未削平）→ 按字节还原（sha256 回 `cfac73ee…`，23,413 B） |
| F4 | **闭合**：全量套件跑前/跑后 `var/features` 均 **905 B / 4 文件**，逐文件 sha256 相同 |
| F5 | **闭合**：`-CheckEncoding` 覆盖 3 份 `SIGNATURE-STATUS.json` + 4 份清单 + 脚本自身，全 `[OK]` exit 0；`sign_and_hash.ps1` BOM=True 35,048 B、PS 5.1 语法 0 错 |
| 闸门 | **不达标**：`954 / OK (skipped=3) / exit 0`、i18n 5/5、selfcheck 92/92 均过，但 **契约 `--check` 变成 2 项失败**（新增「契约漂移」= **D1**） |
| C1 / C2 / C3 / C4 | C1 **未闭合**（便携 GUI/NSIS 需用户桌面会话）；**C2 / C3 / C4 全部闭合** |

### 13.2 新增缺陷（全文见 docs/18 §11）

| ID | severity | 一句话 | 复现 |
|---|---|---|---|
| **D1** | **medium（打 tag 前必修）** | t7 改了 `docs/03-architecture.md`（04:55:53）但没 `gen_api_contract --write`，`docs/api_contract.json` 仍是 01:32 旧指纹 → 契约失败数 1 → **2** | `python tools/gen_api_contract.py --check` → `[FAIL] 契约漂移 … docs/03-architecture.md: 41fb68af… → ac603bc0…` |
| **D2** | **medium** | 总包缺 `docs/audit/v050-tests-tools.md`（05:27 产出，晚于 05:07 重打）→ 包内 6 份 `v050-*` vs 仓库 7 份 | `unzip -l release/tracelens-0.5.0-release.zip \| grep tests-tools`（无） |
| **D3** | low | `docs/05.3-release-checklist.md` §1 表内「❌ 未产出」无行内时点标记（靠顶部数据截至 + §10 追记 + §12 清单消歧） | `sed -n '7,25p' docs/05.3-release-checklist.md` |
| **D4** | low | C1 指令「`--port` 会 `bad option` 退出」与本机不符：带开关一律 **exit=9 / 输出 0 B**（无 `bad option`），`main.js` 实际解析 `--port/--data-dir/--features-dir/--mode/--demo` | `Start-Process portable.exe -ArgumentList '--port','18802' -Wait` → exit 9 |
| **D5** | low | 遗留风险①行号漂移：`Set-AuthenticodeSignature`(496) → `Get-AuthenticodeSignature`(502)，旧文引用 448 | `grep -n "Set-AuthenticodeSignature\|Get-AuthenticodeSignature" tools/sign_and_hash.ps1` |
| **D6** | info | 我的 F3 探针在 05:35:36 对 `tests/test_crypto_sig.py` 做了一次**还原写入**（内容与补丁前逐字节相同），mtime 审计别误判 | `ls -l tests/test_crypto_sig.py` |

### 13.3 与上文（T5）结论的关系

- 上文 §11 的 F1/F2 现记为**已闭合**；F3/F4/F5 记为**已闭合**（T5 的 F1–F7 编号保留，闭合状态以本节为准）。
- 上文 §10.1 遗留风险①（`ps1:448`）**行号更新为 502**，缺口仍在（D5）；其余四条遗留仍在册
  （③ GUI/NSIS 需桌面会话 = C1；④ `verify_e2e.py` 总裁办断言 0 次；⑤ docker 管道依赖对受限会话仍成立）。
- 上文 §6 的闸门结论（契约「唯一失败=8 端点」）在 R2 后**已被 D1 打破** —— 这是本轮唯一的硬闸门偏差。

### 13.4 C1 的用户终端命令（摘要，完整版见 docs/18 §9.1）

```powershell
# ASCII 目录；用环境变量钉端口与数据/账本目录（打包 exe 的 CLI 开关本会话不可判）
mkdir C:\TraceLens-0.5.0; Copy-Item <交付目录>\TraceLens-0.5.0-win-x64-portable.exe C:\TraceLens-0.5.0\; cd C:\TraceLens-0.5.0
$env:TRACELENS_MODE="local"; $env:TRACELENS_PORT="18802"
$env:TRACELENS_DATA_DIR="C:\TraceLens-0.5.0\userdata\data"; $env:TRACELENS_FEATURES_DIR="C:\TraceLens-0.5.0\userdata\features"
Start-Process .\TraceLens-0.5.0-win-x64-portable.exe
# 另开窗口：三条端点 + 登记 201 + 面板可见 + _internal\var 不存在 + 重启计数不减
curl.exe -s http://127.0.0.1:18802/health        # 期望 "version":"0.5.0"
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:18802/api/secretariat/status
# NSIS：.\TraceLens-0.5.0-win-x64-Setup.exe /S（期望退出码 0）→ 开始菜单启动 → 同上复核
# 可选：.\TraceLens-0.5.0-win-x64-portable.exe --selfcheck --selfcheck-out .\selfcheck.json
```

### 13.5 只读边界

本轮新增写入：`docs/18-release-0.5.0-followup.md` 与本 §13。
`node web/selfcheck.mjs` 在 `.tmp/t9-web/` 的**副本**里跑（92/92），仓库 `agent_explainer/web/selfcheck-report.json`
sha256 前后均为 `6d52cdd5691cc189…`（**未被重写**，比 T5 那轮更干净）。
唯一例外是 F3 的临时探针改 `tests/test_crypto_sig.py` 后**按字节还原**（sha256 `cfac73eeff8d811f…bdc410`，23,413 B，与备份逐字节相同；mtime 变为 05:35:36，见 D6）。
发布件 / 校验和 / 签名 / 产品代码零改动。

## 附录 A：本轮证据落点（可复查）

| 证据 | 路径 |
|---|---|
| 源码形态 /health、status、ext 原始响应 | `.tmp/qa-src/health.json`、`sec-status.json`、`ext-secretariat.js`、`server.log` |
| 源码形态持久性（升级后） | `.tmp/qa-src/sec-status2.json`、`server2.log`；默认落点探测 `sec-status3.json`、`server3.log` |
| 便携版 payload 解包 | `.tmp/qa/portable-payload/app/**`、`.tmp/qa/7z-extract.log`、`.tmp/qa/asar-extract/{main.js,sidecar.js,package.json}` |
| 打包 sidecar 运行 | `E:\qa-tl050-portable\{sidecar-run.log,sidecar-run2.log,health-packaged.json,sec-status.json,reg.json,userdata/features/secretariat/*}` |
| 容器 | `.tmp/qa-docker/{health.json,sec-anon.json,login.json,login-headers.txt,cookies*.txt,sec-auth*.json,reg1.json,sec-after.json,data/**}` |
| 闸门 | `.tmp/qa/discover2.log`（权威：954/OK/3 skip）、`.tmp/qa/discover.log`（首跑 3 红原始栈）、`.tmp/qa/i18n.log`、`.tmp/qa/contract.log` |
| 校验和 / 签名 | `release/SHA256SUMS*.txt`、`release/SIGNATURE-STATUS.json`（复算脚本见 §5 的 python 片段） |

## 附录 B：本轮命令清单（按执行顺序，可逐条复跑）

```bash
# 0 环境
cd "E:/DEEPSEEK 黑奴/agent-explainer"; export TMP="$PWD/.tmp" TEMP="$PWD/.tmp" TMPDIR="$PWD/.tmp" PYTHONPATH="$PWD/.tmp/pyfix"
python -V; node -v; docker version; docker images

# 1 源码形态
python -m agent_explainer --mode local --port 8798 --no-browser --data-dir .tmp/qa-src/data --features .tmp/qa-src/features
curl -s -w '\nHTTP=%{http_code}\n' http://127.0.0.1:8798/health
curl -s -w '\nHTTP=%{http_code}\n' http://127.0.0.1:8798/api/secretariat/status
curl -s -o /dev/null -w 'HTTP=%{http_code} bytes=%{size_download}\n' http://127.0.0.1:8798/ext/secretariat.js
curl -s -X POST -H 'Content-Type: application/json' -d '{"agent_id":"qa-acc-1","owner_did":"did:tracelens:user:…"}' \
     http://127.0.0.1:8798/api/secretariat/identities      # → 201 seq=1

# 4.1 默认落点（不传 --features）
cd .tmp/qa-src/prog2 && python -m agent_explainer --mode local --port 8800 --no-browser --data-dir …/data3
# → paths.root = …\prog2\var\features（不跟随 --data-dir）

# 4.2 源码升级模拟
cd .tmp/qa-src/prog2 && python -m agent_explainer --mode local --port 8799 --no-browser --data-dir …/data --features …/features

# 2 便携版
cp release/TraceLens-0.5.0-win-x64-portable.exe /e/qa-tl050-portable/ && Start-Process …（见 §2.1，EXITED 0）
.tmp/qa/7z/7z.exe l|x "E:/qa-tl050-portable/TraceLens-0.5.0-win-x64-portable.exe" '$PLUGINSDIR/app-64.7z'
./desktop/node_modules/.bin/asar extract <payload>/resources/app.asar .tmp/qa/asar-extract
./sidecar/agent-explainer-sidecar.exe --sidecar --port 18803 --mode local --data-dir …/userdata/data --features …/userdata/features

# 3 容器
docker image inspect tracelens:0.5.0 --format '{{index .Config.Labels "org.opencontainers.image.version"}} | {{.Id}} | {{.Size}}'
docker run -d --name qa-tl050 -p 18801:8765 -e TRACELENS_ADMIN_PASSWORD=… -v <data>:/app/var -v <logs>:/app/logs \
  --user 10001:10001 --init tracelens:0.5.0 --data-dir /app/var --features /app/var/features --demo zh
curl -s -w '\nHTTP=%{http_code}\n' http://127.0.0.1:18801/api/secretariat/status        # 未登录 → 401 AUTH_REQUIRED
curl -s -c cookies.txt -X POST -H 'Content-Type: application/json' -d '{"username":"admin","password":"…"}' \
     http://127.0.0.1:18801/api/auth/login
curl -s -b cookies.txt -X POST -H 'Content-Type: application/json' -H 'X-CSRF-Token: <csrf>' -d '{…}' \
     http://127.0.0.1:18801/api/secretariat/identities                                  # → 201
docker rm -f qa-tl050 && docker run -d --name qa-tl050b …（同卷）                        # → counts 不减
docker load -i release/0.5.0/tracelens-0.5.0-image.tar

# 5 校验链（python 片段见 §5.1；签名见 §5.4）
Get-AuthenticodeSignature release/TraceLens-0.5.0-win-x64-{Setup,portable}.exe

# 6 闸门
python -m unittest discover -s tests -p "test_*.py" -q
python tools/i18n_lint.py
python tools/gen_api_contract.py --check
cd agent_explainer/web && node selfcheck.mjs
```
