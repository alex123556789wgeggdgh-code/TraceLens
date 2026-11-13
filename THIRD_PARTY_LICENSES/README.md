# 第三方许可证全文 / Third-party license texts

本目录随 TraceLens 源码仓库与 GitHub Releases 二进制附件一并提供，用于满足各上游组件的
许可要求（Apache-2.0 分发第三方组件时须保留其版权与许可声明）。组件清单与版本号见
[`../docs/NOTICE-third-party.md`](../docs/NOTICE-third-party.md)。

| 文件 | 组件 | 许可证 | 说明 |
| --- | --- | --- | --- |
| `Electron-MIT.txt` | Electron 44.10.2 | MIT | 取自应用包 `LICENSE.electron.txt` |
| `Node-MIT.txt` | Node.js 24.22.2 | MIT（含其依赖许可汇总） | 取自 nodejs/node 官方 LICENSE |
| `Python-PSF-2.0.txt` | CPython **3.14**（Release 包内 `python314.dll`） | PSF-2.0 | 取自 python/cpython `3.14` 分支 LICENSE |
| `Python-PSF-2.0-3.12.txt` | CPython 3.12.16（Docker 镜像） | PSF-2.0 | 取自 python/cpython `3.12` 分支 LICENSE；与 3.14 版仅版权年份列表不同 |
| `PyInstaller-COPYING.txt` | PyInstaller 6.22.3 | GPL-2.0-or-later **含 bootloader 例外** | 例外允许分发由其构建的打包产物而不使产物受 GPL 约束 |
| `numpy-BSD-3-Clause.txt` | numpy 2.5.3 | BSD-3-Clause（含其捆绑组件许可） | 取自应用包 `numpy-2.5.3.dist-info/licenses/LICENSE.txt` |
| `reportlab-BSD.txt` | reportlab 5.0.1（sidecar） | BSD-3-Clause | 取自应用包 `reportlab-5.0.1.dist-info/licenses/LICENSE` |
| `Bitstream-Vera-license.txt` | Bitstream Vera 字体族 `Vera*.ttf` | Bitstream Vera License | 取自应用包 `reportlab/fonts/` |
| `DarkGarden-COPYRIGHT.txt` | DarkGarden 字体 | 版权与授权声明（GPL-2.0-or-later） | 取自应用包 `reportlab/fonts/DarkGarden-copying.txt` |
| `DarkGarden-GPL-2.0.txt` | DarkGarden 字体 | GPL-2.0-or-later 全文 | 取自应用包 `reportlab/fonts/DarkGarden-copying-gpl.txt` |

**Chromium / Chromium 第三方组件**：完整声明（BSD-3-Clause 及数百个第三方许可）随应用包
以 `LICENSES.chromium.html`（约 20 MB）提供，文件已在 `TraceLens-0.5.0-win-x64.zip`
内原样保留；因其体积过大未在本仓库重复收录，可在 Release 附件中查阅。

**Docker 镜像专有组件**：Pillow 13.0.0 (HPND / MIT-CMU)、charset-normalizer 3.5.2 (MIT)、
reportlab 4.5.1 (BSD-3-Clause) 仅出现在 Docker 交付形态，其许可证文本随镜像内
`/opt/deps/*.dist-info/licenses/` 提供；本源码仓库不含这些二进制。

**GPL 字体的边界**：DarkGarden 字体按 GPL-2.0-or-later 授权，是独立作品，仅作为数据文件与
应用聚合分发，不与产品代码链接、不构成衍生作品，因此不改变本产品本体的 Apache-2.0 授权。
再分发该字体时仍须遵守其 GPL 条款，并保留上述版权声明与许可全文；字体文件与原始许可文本
已在 Release 附件的 `reportlab/fonts/` 内原样提供。

**版本说明**：Release 附件内的 `THIRD_PARTY_LICENSES/` 是发布时快照，附件自身的原始许可证
文件（含字体许可）已完整随包提供；最新清单以本仓库 `main` 分支为准。

本产品本体（`agent_explainer/` 与 `agent_explainer/web/`）依 **Apache License 2.0** 开源，
见仓库根 [`../LICENSE`](../LICENSE) 与 [`../NOTICE`](../NOTICE)。
