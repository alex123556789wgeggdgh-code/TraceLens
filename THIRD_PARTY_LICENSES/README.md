# 第三方许可证全文 / Third-party license texts

本目录随 TraceLens 源码仓库与 GitHub Releases 二进制附件一并提供，用于满足各上游组件的
许可要求（Apache-2.0 分发第三方组件时须保留其版权与许可声明）。组件清单与版本号见
[`../docs/NOTICE-third-party.md`](../docs/NOTICE-third-party.md)。

| 文件 | 组件 | 许可证 | 说明 |
| --- | --- | --- | --- |
| `Electron-MIT.txt` | Electron 44.10.2 | MIT | 取自应用包 `LICENSE.electron.txt` |
| `Node-MIT.txt` | Node.js 24.22.2 | MIT（含其依赖许可汇总） | 取自 nodejs/node 官方 LICENSE |
| `Python-PSF-2.0.txt` | CPython 3.12 / 3.14 | PSF-2.0 | 取自 python/cpython 3.12 分支 LICENSE |
| `PyInstaller-COPYING.txt` | PyInstaller 6.22.3 | GPL-2.0-or-later **含 bootloader 例外** | 例外允许分发由其构建的打包产物而不使产物受 GPL 约束 |
| `numpy-BSD-3-Clause.txt` | numpy 2.5.3 | BSD-3-Clause（含其捆绑组件许可） | 取自应用包 `numpy-2.5.3.dist-info/licenses/LICENSE.txt` |
| `reportlab-BSD.txt` | reportlab | BSD-3-Clause | 取自应用包 `reportlab-5.0.1.dist-info/licenses/LICENSE` |

**Chromium / Chromium 第三方组件**：完整声明（BSD-3-Clause 及数百个第三方许可）随应用包
以 `LICENSES.chromium.html`（约 20 MB）提供，文件已在 `TraceLens-0.5.0-win-x64.zip`
内原样保留；因其体积过大未在本仓库重复收录，可在 Release 附件中查阅。

**Pillow (HPND)、charset-normalizer (MIT)、Bitstream Vera 字体许可**：仅出现在 Docker 镜像 /
打包 sidecar 中，其许可证文本随对应组件的 `dist-info/licenses/` 一并提供，见应用包内
`_internal/` 目录。

本产品本体（`agent_explainer/` 与 `agent_explainer/web/`）依 **Apache License 2.0** 开源，
见仓库根 [`../LICENSE`](../LICENSE) 与 [`../NOTICE`](../NOTICE)。
