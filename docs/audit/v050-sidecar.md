# v0.5.0 打包审计 · T1 PyInstaller sidecar 构建 + 真跑实测

任务：t2（T1 PyInstaller sidecar 构建 + 真跑实测，带总裁办端点）
执行：backend（后端工程师：版本口径 / sidecar / 签名 / 镜像）
日期：2026-11-12（构建时间戳 01:51，全部落在 t1 版本口径统一之后）
结论：**通过**。产物是本轮新打的 onedir exe，7 项实测全 PASS；
onefile 形态在本机**不可产且不可跑**（两条独立阻塞，见 §3），已按事实处置。

---

## 1. 前置体检 `tools/build_sidecar.py --check`（留档）

| 字段 | 装 PyInstaller 之前（`.tmp/t2-check-before.log`） | 装完之后（`.tmp/t2-check-after.log`） |
|---|---|---|
| `python_version` | 3.14.7 | 3.14.7 |
| `pyinstaller_available` | **False** | **True**（detail `6.22.3`） |
| `pip_target` | `.tmp\tracelens-build\pyi` | `pip_target_exists = True` |
| 版本号口径 | `--version 默认 0.5.0 / server.py SERVER_VERSION 0.5.0` | 同 |
| hiddenimports（扫盘全量） | **19 个模块（含 5.0 总裁办：True）** | 同 |
| 便携 zip 落点 | `desktop\build\dist\TraceLens-0.5.0-win-x64-portable-src.zip` | 同 |
| 结论 | 构建**不可用** | 构建**可用**（PyInstaller 6.22.3，Python 3.14.7） |
| exit | 0 | 0 |

## 2. PyInstaller 的安装：只装进隔离目录，不碰系统环境

`python tools\build_sidecar.py --install` → `.tmp/t2-install.log`，exit 0：

```
Successfully installed altgraph-0.17.5 packaging-26.3 pefile-2024.8.26
pyinstaller-6.22.3 pyinstaller-hooks-contrib-2026.10 pywin32-ctypes-0.2.3
setuptools-84.2.1
→ PyInstaller 安装完成：6.22.3
```

* 落点：`.tmp\tracelens-build\pyi`（`pip --target`）。
* 系统 site-packages **没有被写入**：`Test-Path D:\python\Lib\site-packages\PyInstaller` → **False**。
* PyPI 可达（探针 0.1 s 返回，最新 `pyinstaller 6.22.3`；`pyinstaller-6.22.3-py3-none-win_amd64.whl`
  属 Python 版本无关的预编译 bootloader wheel，所以 3.14 能装）。
* 未使用 `--vendor`（在线可用，不需要离线轮子）。

## 3. onefile：产得出来、跑不起来（两条独立阻塞，已如实处置）

`python tools\build_sidecar.py --build`（默认 onefile）：
**37.5 MiB（39,323,927 字节）/ 25 s** 构建成功并拷贝到
`desktop/build/resources/sidecar/agent-explainer-sidecar.exe`，
但脚本自带的冒烟（`exe --print-argv --port 8899`）**失败**：

```
{"ok": false, "exit_code": 4294967295, "stdout_tail": "",
 "stderr_tail": "[PYI-19452:ERROR] Failed to create parent directory structure."}
exe 冒烟失败：不得计入交付
```

按 TMP 逐一试过（每条都是真实 exe 调用，不是推测）：

| `TMP/TEMP` 取值 | exe 实测结果 |
|---|---|
| 工作区内 `.tmp`、`.tmp\meipass`（存在且可写） | `[PYI-…:ERROR] Failed to create parent directory structure.` |
| `C:\Windows\Temp` | `[PYI-…:ERROR] Could not create temporary directory!` |
| `%LOCALAPPDATA%\Temp`（ASCII、常规可写） | `[PYI-…:ERROR] Could not create temporary directory!` |

两条**互相独立**的阻塞，合起来使 onefile 在本机无解：

1. **DSH 沙箱不允许写工作区之外**（`C:\Windows\Temp`、`%LOCALAPPDATA%\Temp` 全被拒），
   所以 onefile 的解包目录必须在 `E:\DEEPSEEK 黑奴\…` 之下；
2. **工作区根目录本身含非 ASCII（`黑奴`）**，PyInstaller bootloader 用 ANSI 路径 API 建
   `_MEIPASS` 父目录链时建不出来（现象：即使把 TMP 指到工作区内已存在的子目录，
   仍报 `Failed to create parent directory structure`）。
   注意：onefile 的解包是**每次启动都要建目录**，所以这条对 onefile 是致命的；
   onedir 不解包，不受影响（§4 的 onedir exe 在同一沙箱里正常起服务）。

处置（不糊弄）：
* 该 onefile exe **已删除**（39,323,927 字节，sha256 前 16 位 `229ED9046CC779F9`）。
  留着它更坏：`tests/test_e2e.py:66-70` 的 `SIDECAR_CANDIDATES` **优先**取 onefile 路径，
  一个跑不起来的 exe 会让 `test_sidecar_exe_runs` 假失败、也会让 T3 的 electron-builder
  把不可运行产物打进安装包。
* 构建日志留档：`.tmp/t2-build-onefile-fail.log`。
* 0.4.5 的已发布 sidecar 本来就是 **onedir**（旧 BUILD-INFO：`layout onedir`），
  所以本轮交付 onedir 与既有发布口径一致，不是降格。

## 4. onedir 产物（本轮交付物）

`python tools\build_sidecar.py --build --onedir` → exit 0，`.tmp/t2-build-onedir2.log`。
参数里 **19 条 `--hidden-import`** 全量打进（含 `agent_explainer.secretariat`、
`agent_explainer.crypto_sig`、`agent_explainer.feature_store`）+ `--collect-all reportlab`
+ 三条 `--add-data`（web / samples / prompts）。

`desktop/build/resources/sidecar/BUILD-INFO.json` 摘要（**本轮新产出，时间戳晚于 t1**）：

```
built_at                = 2026-11-12T01:51:23      （t1 完成于 2026-11-12 凌晨更早）
python / pyinstaller    = 3.14.7 / 6.22.3
layout                  = onedir
exe                     = desktop/build/resources/sidecar/agent-explainer-sidecar/agent-explainer-sidecar.exe
bytes / sha256          = 10,403,499 / f00527ca2c9ac56787b2f8794029013c15422717c371f40c4f982f8e136f093a
seconds                 = 18.8
hiddenimport_count      = 19
secretariat_in_hiddenimports = True
data_dirs_exist         = {web_dir_exists: True, samples_exists: True, prompts_exists: True}
version_default / server_version_in_code = 0.5.0 / 0.5.0
reportlab_bundled       = True
smoke.ok / stdout_codec / 路径无 U+FFFD = True / gbk / True
```

旧 onedir（0.4.5，`bytes 10155991`、`sha256 0746353c…`、`built_at 2026-11-11T10:11:42`）
已被 `--clean --noconfirm` 的拷贝覆盖，**没有复用**：新 exe 的 sha256 与字节数都不同。

## 5. 真跑实测：把 exe 起成服务，逐条打接口（本轮重点，不是"构建完就算"）

脚本 `.tmp/t2-probe-sidecar.py`（原始输出 `.tmp/t2-probe.log`，UTF-16 原件 + `.utf8` 转码件）。
启动命令（**新目录**，故意让它自己建）：

```
…\agent-explainer-sidecar.exe --sidecar --port 8799 --demo zh --data-dir .tmp\v050-sidecar\data
```

`exe bytes = 10403499  mtime = 2026-11-12T01:51:22` → 起服务后逐条：

### 5.1 `GET /health` → HTTP 200（`version` 必须是 0.5.0）

```json
{ "name": "TraceLens", "version": "0.5.0", "status": "ok", "mode": "local",
  "schema_version": "1.1",
  "auth": { "required": false, "mode": "local", "bootstrap_needed": false,
            "note": "local 模式免登录，身份固定为 local-user（T1 护栏：不要求注册）" },
  "stats": { "count": 12, "decided": 2, "demo_data": 12, "durable": true,
             "source": "E:\\DEEPSEEK 黑奴\\agent-explainer\\.tmp\\v050-sidecar\\data\\records.jsonl（首次启动：写入演示种子）" },
  "audit": { "ok": true, "durable": true, "source": "jsonl",
             "path": "E:\\DEEPSEEK 黑奴\\agent-explainer\\.tmp\\v050-sidecar\\data\\audit.jsonl", "count": 0 } }
```

（响应体较长，探针按 1600 字符截断打印；截断只影响打印，不影响判定。）

### 5.2 `GET /api/secretariat/status` → HTTP 200，`ok=true`，`secretariat.enabled=true`

```json
{ "ok": true,
  "secretariat": { "version": "1.0", "mode": "local", "enabled": true,
    "counts": { "identities": 0, "active_identities": 0, "delegations": 0,
                "letters": 0, "signed_records": 0 },
    "risk_policy": { "low": {…}, "medium": {…},
                     "high": {"approvers": 1, "seal": false, "describe_key": "secretariat.risk.high", …},
                     "critical": {"approvers": 2, "seal": true, "describe_key": "secretariat.risk.critical", …} },
    "delegation": { "max_depth": 5 },
    "chains": { "identities": { "ok": true, "count": 0,
                "head": "0000000000000000000000000000000000000000000000000000000000000000",
                "problems": [],
                "path": "…\\agent-explainer-sidecar\\_internal\\var\\features\\secretariat\\identities.jsonl" } } } }
```

**这条就是 `secretariat.py` 真被冻进 exe 的证明**：端点存在（不是 404）、
返回 200 且 `enabled=true`、还带着 risk_policy 四档与哈希链自检。

### 5.3 `GET /api/secretariat/identities` → HTTP 200

```json
{ "ok": true, "identities": [], "count": 0 }
```

### 5.4 `POST /api/secretariat/identities` → HTTP 201（登记一个身份）

请求 `agent_id=agent-t2-sidecar`、`owner_did=did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke`
（由 `crypto_sig.did_from_name("owner", kind="user")` 现场生成的人类 DID）、
`image_hash=sha256:t2sidecarprobe`、`boot_params={"argv":["t2-probe"]}`：

```json
{ "ok": true,
  "identity": { "id": "agt_20261111175124_0443acd0", "agent_id": "agent-t2-sidecar",
    "kind": "agent", "pubkey": "5AYOmVmsAWtSTPwvOyouuM3FrdPRFg8uXY9KWMh2ofk=",
    "fingerprint": "0841ac59df6816ce", "image_hash": "sha256:t2sidecarprobe",
    "boot_params_hash": "809dea78741fefe3ad35c3c0b5fd894626ff42559f38cadeaa0bb522a97dc302",
    "owner_did": "did:tracelens:user:jqics2l64nmhcxj2csrk3waxysybmuke",
    "created_at": "2026-11-11T17:51:24Z", "revoked_at": null, "supersedes": null,
    "seq": 1, "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "entry_hash": "fb41ab5d1b46f2d47b10b9fa82fff6bf6871a7bf791a4d9da07c8221b95105f4" } }
```

### 5.5 再查 `GET /api/secretariat/status` → 计数 0 → 1（feature_store 账本在 exe 里可写）

```
计数：登记前 identities=0 → 登记后 identities=1
"counts": { "identities": 1, "active_identities": 1, "delegations": 0, "letters": 0, "signed_records": 0 }
```

`seq: 1` + `prev_hash` 全零创世 + 真实 `entry_hash` → 不是内存里的假象，是链式账本追加成功。

### 5.6 数据目录由 exe 自建

```
数据目录存在=True  文件数=4
样例：auth, auth/auth-state.json, records.jsonl, records.jsonl.lock
```

### 5.7 逐条判定（探针脚本自己打分，exit 0）

```
[PASS] /health 200
[PASS] version == 0.5.0
[PASS] /api/secretariat/status 200
[PASS] ok/secretariat.enabled == true
[PASS] /api/secretariat/identities 200
[PASS] POST 登记身份 201
[PASS] 计数 0 → 1 递增
```

没有出现 404 / ModuleNotFoundError —— hiddenimports 生效，`secretariat` 在 exe 内可用。

---

## 6. 测试口径

| 命令 | 结果 |
|---|---|
| `python -m unittest tests.test_e2e.ArtifactTests.test_sidecar_exe_runs -q` | **Ran 1 test … OK**（2.293 s，**不是 skip**） |
| `python -m unittest tests.test_e2e.ArtifactTests -v` | Ran 4 tests：`test_builder_check_reports_the_real_reason` ok、`test_sha256sums_verify` ok、`test_sidecar_exe_runs` ok、`test_signature_status_is_honest` **FAIL** |

唯一那条 FAIL 是 t1 就记录在案的**已知红**：三份 `SIGNATURE-STATUS.json` 里 exe 的
Authenticode 状态是自签名 `UnknownError`（`signed_count: 2`，只数 `Valid`/`NotTrusted`）。
它由 **t4**（真签 + 校验和 + 发布总包）转绿，本轮不动签名，也**绝不**把 `UnknownError`
改标成 `Valid` 让它假绿。

`test_sidecar_exe_runs` 现在跑的是本轮 onedir exe（onefile 已删，`SIDECAR_CANDIDATES[0]`
不存在 → 落到候选 2），断言覆盖 `/health` 200、演示 12 条、`audit.ok`、
`/`+`/app.js`+`/styles.css` 静态资源 200、数据目录自建、`records.jsonl` 落盘。

## 7. 源码便携包

`python tools\build_sidecar.py --portable-zip` → exit 0：

```
→ 源码便携版：desktop\build\dist\TraceLens-0.5.0-win-x64-portable-src.zip（4.71 MiB）
```

* 文件名带 **0.5.0**：`TraceLens-0.5.0-win-x64-portable-src.zip`，**4,937,446 字节**（2026-11-12 01:51:37）。
* 145 个条目，其中 **8 条含 secretariat**：`agent_explainer/secretariat.py`、
  `agent_explainer/web/ext/secretariat.{css,js}`、
  `agent_explainer/web/i18n/parts/secretariat.{zh,en}.json`、`docs/05-evidence/r4-secretariat-panel.png` 等
  → 5.0 总裁办模块在便携形态里也在场。
* 同目录仍留着历史件 `TraceLens-0.4.5-win-x64-portable-src.zip`（3,483,619 字节，2026-11-11），
  未被改名冒充新产物；发布总包由 t4 汇总。

## 8. 实测暴露并修掉的脚本缺口（`tools/build_sidecar.py`，属"实测暴露收集缺口"）

1. **BUILD-INFO 之前不记录验收要看的事实**：0.4.5 的 BUILD-INFO 里没有模块清单、
   没有 `web_dir_exists`、没有版本口径 —— 于是"总裁办有没有被打进去"只能靠猜。
   现在多了 `hiddenimports` / `hiddenimport_count` / `secretariat_in_hiddenimports` /
   `data_dirs_exist{web_dir_exists,samples_exists,prompts_exists}` /
   `version_default` / `server_version_in_code` / `smoke.stdout_codec`（§4 已贴实测值）。
2. **构建证据本身有乱码**：`smoke_run` 原先用 `text=True, encoding="utf-8", errors="replace"`
   读子进程输出，而 exe 在非 ASCII 路径下按 **cp936** 吐字节，于是 BUILD-INFO 里的路径
   变成 `DEEPSEEK ��ū`（0.4.5 的 BUILD-INFO 就是这样）。
   改成：**按字节取回** + 解码链 UTF-8 → GBK + 全失败退 `backslashreplace`
   （宁可留下看得见的转义，也不悄悄把路径变成 U+FFFD），并把实际用的解码器记进
   `smoke.stdout_codec`。实测本轮 `stdout_codec = "gbk"`、`路径无 U+FFFD = True`。
3. `py_compile` 复验通过；`--check` 前后两次 exit 0（§1）。

## 9. 交给 T3 / T4 / T5 的硬事实（本轮禁改业务代码，故只记录不修）

**9.1（T3 必修，已实测复现）总裁办账本默认写在"包内"，与 `--data-dir` 无关。**
同一个 exe、同一个 `--data-dir`，两种启动方式的实测落点（`.tmp/t2-features2.log`）：

```
不传 --features：POST=201  账本 path=…\desktop\build\resources\sidecar\agent-explainer-sidecar\
                            _internal\var\features\secretariat\identities.jsonl   chain.ok=True count=1
传 --features DIR：POST=201 账本 path=…\.tmp\v050-sidecar3\data\features\secretariat\identities.jsonl
                            chain.ok=True count=1
```

* 机制：`--features DIR` 是 **server.py** 的选项（`python -m agent_explainer.server --help`：
  `--features DIR  总裁办数据目录（默认 var/features：身份账本 / 委托 / 认定书 / 密钥库）`）；
  sidecar 启动器把它转发给 server。**冻结态下默认 `var/features` 解析到 `_internal\var\features`**，
  也就是安装目录内部。实测该目录被写出 3 个文件：`identities.jsonl`(650B)、
  `identities.jsonl.lock`(0B)、`keyring.json`(340B)。
* 后果（真实安装场景）：装进 `Program Files` 后包内目录**不可写** → 登记身份直接失败；
  即便可写，应用升级会冲掉身份账本与**密钥库 `keyring.json`**（密钥丢失＝已签记录不可验证），
  且多用户共用一台机器会互相踩同一份账本。
* 建议修法（T3 的位置很明确）：`desktop/sidecar.js:30` 当前只传
  `--sidecar --port 0 --data-dir <userData>/data --demo zh`，追加
  `'--features', path.join(userData, 'features')`；实测该参数被 exe 接受且账本随之落到用户目录。
  建议同一条参数写进 `docker-compose.yml` / 容器启动（T5）与源码便携包的 `start.cmd`。
* 顺带：`python -m agent_explainer --help`（源码态与 exe 态都验过）**不列 `--features`**，
  参数不可发现；至少要在 `desktop/README.md` 与发布文档写明，QA（T6）按"升级后身份还在不在"
  这条来验。

**9.2（T3）exe 的 stdout 是 GBK/cp936。**`--help`、`--print-argv` 在非 ASCII 路径下都按 cp936
输出（本轮 §8.2 实测 `codec=gbk`）。Electron 侧读 pipe 拿 `--json` bootstrap 时若按 UTF-8 解码，
含中文路径的字段会坏。`PIPE_ENV` 里剔掉 `PYTHONUNBUFFERED` 的既有处理不动，
加 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8` 到 sidecar 启动 env 是最省事的解法（实测对
`--print-argv` 输出有效）。

**9.3（T3）sidecar 形态用 onedir，不要用 onefile。**onefile 在"工作区根含非 ASCII + 沙箱禁写
工作区外"这台机器上产得出、跑不起（§3）。0.4.5 发布件也是 onedir，electron-builder 的
`extraResources` 已覆盖 `resources/sidecar` 整目录；若将来要在真机（ASCII 路径、无沙箱）
产 onefile，请先跑一次冒烟再计入交付 —— 脚本已经会因冒烟失败 exit 1。

**9.4（T4）** 唯一红测 `test_signature_status_is_honest` 的成因与转绿路径见 §6；
签完要重新生成 `desktop/build/dist/SIGNATURE-STATUS.json` 与 `SHA256SUMS.txt`
（`test_sha256sums_verify` 现在验的是 0.4.5 时代留下的 sums，t3 重打安装包后必须重算）。

**9.5（T5）** 镜像里的 0.5.0 口径已统一（`Dockerfile` ARG / compose 默认值），
镜像内 sidecar 不适用；但要带上 9.1 的 `--features`，否则容器里 `var/features` 落在镜像层内。

## 10. 收尾（现场已清）

* 临时服务全部停掉：`Get-Process *agent-explainer-sidecar*` → **空**（探针用 terminate，退出码 1，
  属被终止的正常表现）。
* 数据目录已删：`.tmp/v050-sidecar`、`.tmp/v050-sidecar2`、`.tmp/v050-sidecar3` → 均 `False`。
* 包内运行期写出的文件已删：`agent-explainer-sidecar/_internal/var/`
  （`identities.jsonl`、`identities.jsonl.lock`、`keyring.json`）→ 不留进 T3 的安装包。
* 交付面：`desktop/build/resources/sidecar/{agent-explainer-sidecar/, BUILD-INFO.json}`
  + `desktop/build/dist/TraceLens-0.5.0-win-x64-portable-src.zip`。
* 留档日志：`t2-check-before.log`、`t2-install.log`、`t2-check-after.log`、
  `t2-build-onefile-fail.log`、`t2-build-onedir2.log`、`t2-probe.log(.utf8)`、
  `t2-features2.log`、`t2-portable.log`、`t2-e2e-sidecar.log`、`t2-e2e-artifacts.log`。
