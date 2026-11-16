# 22 · 功能中心（八功能独立界面）

> 目的：把散落在「信任中心」标签页、记录卡片和审计侧栏里的八项能力，收成
> **一个入口 + 八个地址**。每一页只做它自己的事，数据、判定与导出口径一律
> 沿用原实现，不新增长期并存的第二套逻辑。

相关文件：

| 文件 | 作用 |
| --- | --- |
| `agent_explainer/web/ext/workspaces.js` | 功能中心外壳 + 八个页面 |
| `agent_explainer/web/ext/workspaces.css` | 浮层、导航、页面与窄屏布局 |
| `agent_explainer/web/i18n/parts/workspaces.{zh,en}.json` | 文案（74 键，zh/en 对齐） |
| `agent_explainer/web/app.js` → `TraceLensUI.renderExportPanel` | 让合规页挂载**同一个**导出面板 |
| `agent_explainer/web/ext/cards.js` → `TraceLensCards` | 回滚计划、置信度模型、证据渲染的复用出口 |
| `tests/test_workspaces_face.py` | 17 项静态门禁 |
| `agent_explainer/web/selfcheck.mjs` 第 121 项 | 真起页面、真走八个路由的运行时门禁 |

> 视觉方向已于 UI-REDESIGN-1 整体重做为「档案 / 案卷」：纸墨配色、宋体标题、
> 发丝线账目与朱砂索引。本文只描述结构与交互，样式决策见 `docs/23-interface-redesign.md`。

## 1. 为什么不是「再加一个标签页」

信任中心原本是**一个面板 + 若干标签页**：四个功能（多 Agent 聚合、共享与协作、
队长仪表盘、自动化建议）各有一页，另外四项（回滚、置信度、因果证据、合规导出）
分别长在记录卡片和审计侧栏上。后果是：

- 八个功能没有一张能看全的清单，用户记不住「第 5 个功能在哪」；
- 点进去之后四个页面的标题层级、返回方式和空状态各不相同；
- 合规导出在侧栏、回滚在卡片里，两者都不像「一个功能」。

功能中心只解决**入口与页面**，不动任何一个功能的实现。

## 2. 八个功能与八个页面

| # | 功能 | 页面 id | 内容来自 |
| --- | --- | --- | --- |
| 01 | 干预回滚 | `rollback` | 记录列表 + `TraceLensCards.openRollbackPlan`（先读计划再确认） |
| 02 | 置信度可视化 | `confidence` | `TraceLensCards.confidence` 的分层模型 + 本页筛选 |
| 03 | 因果证据链 | `evidence` | `TraceLensCards.renderEvidenceAndCausal` + 上传表单 |
| 04 | 多 Agent 聚合 | `groups` | 插件 `groups` 的 `tab.render` |
| 05 | 共享与协作 | `collab` | 插件 `collab` 的 `tab.render` |
| 06 | 队长仪表盘 | `analytics` | 插件 `insights` 的 `tab.render` |
| 07 | 自动化建议 | `suggestions` | 插件 `suggestions` 的 `tab.render` |
| 08 | 合规审计导出 | `compliance` | `TraceLensUI.renderExportPanel`（审计侧栏那份面板本体） |

插件页在状态变化（记录刷新、语言切换）时调 `tab.repaint`，不重建骨架；
卡片页与合规页在每次渲染通知后重绘本页，保证不会显示过期数字。

## 3. 地址就是一个功能

```
index.html#feature=rollback      # 干预回滚
index.html#feature=confidence    # 置信度
index.html#feature=evidence      # 因果证据
index.html#feature=groups        # 多 Agent 聚合
index.html#feature=collab        # 共享与协作
index.html#feature=analytics     # 队长仪表盘
index.html#feature=suggestions   # 自动化建议
index.html#feature=compliance    # 合规审计导出
```

- 打开浮层时先记下进入前的地址；关闭时**原样恢复**，不留在 `#feature=…`。
- `hashchange` 只处理 `#feature=<已知 id>`；其它 hash 一律不碰，避免和页面里
  原有的锚点行为打架。
- `Esc` 关闭浮层，但信任中心的模态优先（模态开着时不抢）。

## 4. 三条硬规矩

1. **没有第二套实现。** 合规页不自己发 `compliance/export`，不自己算哈希；
   它挂载的是审计侧栏那份面板本体。回滚页不自己拼时间线，调的是卡片插件的
   计划对话框。证据页不自己解析证据结构，调的是卡片的渲染函数。
   门禁：`tests/test_workspaces_face.py::NoSecondImplementationTests`。
2. **不造数字。** 源码零定时器；置信度、记录数、导出结果全部读自响应。
   缺数据就显示空状态并说明原因（离线 / 无权限 / 还没有记录）。
3. **文案在字典里。** `ext/workspaces.js`、`ext/workspaces.css` 零中文字面量；
   74 个键在 zh/en 两侧逐键对齐（`tools/i18n_lint.py` 第 1、2 项把关）。

## 5. 界面结构

```
section#feature-center[role=dialog]     浮层
├─ aside.workspace__sidebar             品牌 + 三组导航（记录与判定 / 协作与分析 / 对外产出）
└─ main.workspace__main
   ├─ header                            返回 / 标题 / 一句话说明 / 关闭
   └─ div#workspace-content[aria-live]  当前功能页
```

- 顶栏新增「功能中心」入口（`08` 徽标），原来右下角的悬浮按钮随之隐藏，
  避免同一件事两个入口。
- 宽度 ≤ 1080px：记录布局与证据布局改单列。
- 宽度 ≤ 820px：侧栏变成横向可滚动的功能条，顶部标题区压缩。
- 宽度 ≤ 560px：功能卡片单列，筛选控件占满整行。

## 6. 什么时候该用哪条路

| 场景 | 走哪里 |
| --- | --- |
| 想找「某个功能在哪」 | 顶栏「功能中心」→ 总览 |
| 想深链到某个功能 | `#feature=<id>` |
| 想知道某个动作的原始记录 | 功能页里的「在原记录中查看」跳回卡片 |
| 想看签名与身份 | 总览底部的「打开总裁办」（总裁办不做成第九个功能页） |
| 想导出审计包 | 功能 08，或审计侧栏的同名面板（同一份实现） |

## 7. 已知边界

- 功能页**不替代**总裁办：身份、签名、责任链仍在总裁办的账本里。
- 插件页的空状态由插件自己给出；功能中心只在插件未加载时说明"模块未加载"。
- 合规页挂载的面板与侧栏共用同一份状态，因此**两处同时打开会同步变化**，
  这是刻意的：同一份事实不应有两个版本。
