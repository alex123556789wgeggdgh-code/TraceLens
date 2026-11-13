# 第三方组件与许可清单（NOTICE-third-party）

> 用途：交付包里"我用到了别人的东西"必须写清楚——哪些组件、哪个版本、什么许可证、许可证文本在哪。
> 本产品**本体**（`agent_explainer/` 全部 Python 源码 + `agent_explainer/web/` 界面）依
> **Apache License 2.0** 开源（见仓库根 [LICENSE](../LICENSE) 与 [NOTICE](../NOTICE)）；
> 下表列的是**打包产物里出现的第三方组件**，各组件许可证全文见仓库 `THIRD_PARTY_LICENSES/`
> 并随 Releases 二进制附件一并提供。
>
> 说明（诚实版）：架构文档 §15 原本写"该文件由构建脚本生成"，但当前 `tools/` 里没有生成它的脚本；
> 这份清单是**人工按实际产物核对**写的（核对方法见文末），版本号都从产物本身读出来，不从文档抄。

---

## 1. 总表

| 组件 | 版本（实测来源） | 许可证 | 出现在哪个交付形态 | 许可证文本位置 |
| --- | --- | --- | --- | --- |
| Electron | 44.10.2（`TraceLens.exe --version` / `process.versions.electron`） | MIT | Windows 安装包、便携版 | 随包 `resources/../LICENSE.electron.txt`（`win-unpacked/LICENSE.electron.txt`） |
| Chromium | 152.0.7977.169（同上，`process.versions.chrome`） | BSD-3-Clause 及若干第三方许可 | 同上（Electron 运行时内含） | 随包 `LICENSES.chromium.html`（20.4 MB，含全部第三方声明） |
| Node.js | 24.22.2（同上，`process.versions.node`） | MIT | 同上（Electron 内含） | 同 `LICENSE.electron.txt` / `LICENSES.chromium.html` |
| Python | 3.12.16（Docker 镜像 `python:3.12-slim`）；**3.14**（Release 包内 `python314.dll` 实证） | PSF-2.0 | Docker 镜像、打包版 sidecar、源码便携包（用户自带 Python） | 镜像内 `/usr/local/LICENSE.txt`；仓库副本 `THIRD_PARTY_LICENSES/Python-PSF-2.0.txt`（3.14）与 `Python-PSF-2.0-3.12.txt`（3.12） |
| PyInstaller | 6.22.3（`tools/build_sidecar.py --check` 输出） | GPL-2.0-or-later **附 bootloader 例外** | 打包版 sidecar（`agent-explainer-sidecar.exe`） | `THIRD_PARTY_LICENSES/PyInstaller-COPYING.txt`；bootloader 例外允许分发构建产物 |
| numpy | 2.5.3（应用包 `numpy-2.5.3.dist-info` 实测） | BSD-3-Clause（含其捆绑组件各自许可） | 打包版 sidecar | 随包 `_internal/numpy-2.5.3.dist-info/licenses/`；副本 `THIRD_PARTY_LICENSES/numpy-BSD-3-Clause.txt` |
| reportlab | 4.5.1（Docker 镜像）；**5.0.1**（Release 包 `reportlab-5.0.1.dist-info` 实测） | BSD-3-Clause | Docker 镜像（PDF 导出）、打包版 sidecar | 镜像内 `/opt/deps/reportlab-4.5.1.dist-info/LICENSE`；副本 `THIRD_PARTY_LICENSES/reportlab-BSD.txt`（5.0.1） |
| Pillow | 13.0.0（reportlab 依赖，同一条 pip 日志） | HPND / MIT-CMU | **仅 Docker 镜像** | 镜像内 `/opt/deps/pillow-13.0.0.dist-info/licenses/` |
| charset-normalizer | 3.5.2（reportlab 依赖） | MIT | **仅 Docker 镜像** | 镜像内 `/opt/deps/charset_normalizer-3.5.2.dist-info/licenses/` |
| Bitstream Vera 字体族 | 随 reportlab 提供（`Vera.ttf` / `VeraBd.ttf` / `VeraIt.ttf` / `VeraBI.ttf` 实测） | Bitstream Vera License | 打包版 sidecar、Docker 镜像 | 随包 `reportlab/fonts/bitstream-vera-license.txt`；副本 `THIRD_PARTY_LICENSES/Bitstream-Vera-license.txt` |
| DarkGarden 字体 | 随 reportlab 提供（`DarkGardenMK.pfb` / `.sfd` 实测） | **GPL-2.0-or-later** | 打包版 sidecar、Docker 镜像 | 随包 `reportlab/fonts/DarkGarden-copying.txt`（版权与授权声明）与 `DarkGarden-copying-gpl.txt`（GPL v2 全文）；副本见 `THIRD_PARTY_LICENSES/` |

> **关于 GPL 字体的合规边界**：`DarkGarden` 字体依 GPL-2.0-or-later 授权，是**独立作品**，
> 仅作为数据文件与本产品聚合（mere aggregation）分发，不与产品代码链接、不构成衍生作品，
> 因此不改变本产品本体的 Apache-2.0 授权。其版权声明与 GPL 全文已随应用包原样提供，
> 并在 `THIRD_PARTY_LICENSES/` 存有副本；再分发该字体时仍须遵守其 GPL 条款。
> Bitstream Vera 字体族按其自有许可分发，许可文本同样随包提供。

**产品本体零第三方运行时依赖**：`agent_explainer/` 只用 Python 标准库（由
`tests/test_trace_adapter.py::OfflinePurityTests` 与 `tools/test_offline_engine.py` 静态强制），
所以源码形态不需要 `pip install` 任何东西。

---

## 2. 各形态的第三方边界

| 交付形态 | 随包第三方组件 | 备注 |
| --- | --- | --- |
| Windows 安装包（`TraceLens-0.5.0-win-x64-Setup.exe`） | Electron 44.10.2（含 Chromium 152、Node 24.22.2）、PyInstaller 打包的 sidecar（内含 Python 3.14.7 运行时） | 安装包为自签名（签名材料私有，见仓库根 `NOTICE`），与第三方许可无关 |
| 便携版（`TraceLens-0.5.0-win-x64-portable.exe`） | 同上 | 首次运行解压到自身目录 |
| 源码便携包（`TraceLens-0.5.0-win-x64-portable-src.zip`） | **无**（要求用户自备 Python 3.12+） | 只有本产品源码 + 样例 + 文档 |
| Docker 镜像（`tracelens:0.5.0`） | python:3.12-slim（PSF-2.0）、reportlab 4.5.1 / Pillow 13.0.0 / charset-normalizer 3.5.2（BSD / HPND / MIT） | 非 root 运行；PDF 导出可用；`--build-arg TRACELENS_WITH_PDF=0` 可去掉后三个 |
| GitHub Release 便携包（`TraceLens-0.5.0-win-x64.zip`） | 便携版全部内容，外加 numpy 2.5.3、reportlab 5.0.1（含 Bitstream Vera / DarkGarden 字体）、`LICENSE` / `NOTICE` / `THIRD_PARTY_LICENSES/` | 本公开仓库当前对应的实际发布形态 |
| 源码仓库 | 仅用于构建（Electron 44.10.2、electron-builder 26.x、PyInstaller 6.22.3），**不进运行时** | 见 `desktop/package.json` 与 `docs/03-architecture.md` §9 |

---

## 3. 商标与数据声明

- "Electron"、"Chromium"、"Node.js"、"Python"、"Docker" 等名称是各自权利人的商标，
  本产品只在"说明技术构成"的意义上使用，不表示任何隶属或背书关系。
- 演示数据（`samples/`、`web/demo-data*.js`）是团队手写的**合成样例**，来自公开规范（Langfuse / OTLP /
  MCP / 本产品 schema），**不包含**任何真实用户、真实系统或第三方受版权保护的数据集。
- **Web 界面**字体使用系统字体栈，不内嵌第三方字体文件（`styles.css` 仅有 font-family 列表）；
  **PDF 导出**所用的 reportlab 自带字体（Bitstream Vera、DarkGarden）是另一回事，已在第 1 节总表列出。

---

## 4. 怎么核对这张表（可复现）

```bash
# ① Electron / Chromium / Node 版本（从产物里读，不猜）
ELECTRON_RUN_AS_NODE=1 ./desktop/build/dist/win-unpacked/TraceLens.exe \
  -e "console.log(process.versions.electron, process.versions.chrome, process.versions.node)"

# ② 随包许可证文本确实在
ls desktop/build/dist/win-unpacked/LICENSE.electron.txt desktop/build/dist/win-unpacked/LICENSES.chromium.html

# ③ PyInstaller 版本与 sidecar 构建信息
python tools/build_sidecar.py --check
cat desktop/build/resources/sidecar/BUILD-INFO.json

# ④ Docker 镜像里的 Python 与 reportlab 版本（0.5.0）
docker run --rm tracelens:0.5.0 python -V
docker run --rm tracelens:0.5.0 python -c "import reportlab; print(reportlab.Version)"
docker run --rm tracelens:0.5.0 sh -c "ls /opt/deps | head"

# ⑤ 本体零第三方依赖（静态检查）
python tools/test_offline_engine.py
```

> 变更纪律：新增/升级任何一个第三方组件，必须同时更新本表与 `CHANGELOG.md`；
> 若引入与 Apache-2.0 不兼容的许可（如 GPL 系），须先确认其与本产品为独立聚合而非链接衍生，
> 并随分发提供该组件的版权声明、完整许可证文本与源码获取途径。
