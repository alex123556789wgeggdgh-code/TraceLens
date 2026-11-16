# TraceLens 前端骨架 · 子任务 3.1 + 3.2（手机 + 桌面，无构建）

把 Agent 的思考过程翻译成**普通人能直接行动**的清单：三个区（待确认 / 进行中 / 已完成）+ 一个悬浮球 + 底部面板；
桌面（≥1024px）再加上**侧栏动作列表 + 状态筛选**、**可拖动的时间轴**与**两次运行的对比**。
本目录是**纯静态单页应用**：没有打包器、没有依赖、没有网络请求，双击 `index.html` 就能跑完整演示。

> 本文档与 `i18n/keys.md` 是中文说明文件。界面文案一律来自 `i18n/*.json`；
> `app.js` / `index.html` / `styles.css` 里**零中文字符**（由自检第 07 条强制）。
> 因此 `tools/i18n_lint.py`（t10）需要把 `*.md` 加入白名单，只扫描代码与字典。

## 1. 文件清单

| 文件 | 手写/生成 | 作用 |
| --- | --- | --- |
| [index.html](agent-explainer/agent_explainer/web/index.html) | 手写 | 结构骨架：`#app` `#topbar` `#demo-banner` `#timeline-main`（桌面时间轴）`#sheet`（三区）`#fab` + 桌面挂载点 `#sidebar` `#timeline` `#compare` |
| [app.js](agent-explainer/agent_explainer/web/app.js) | 手写 | 分区/着色/展开/语言/演示徽标/渲染 + 3.2 桌面层（筛选、倒序列表、时间轴、对比），全局命名空间 `window.TraceLensUI` |
| [styles.css](agent-explainer/agent_explainer/web/styles.css) | 手写 | 设计系统令牌 + 手机（≤480px）+ 平板（≥481px）+ 桌面（≥1024px）+ 暗色 + 减少动效 + 验证台钉框布局 |
| [i18n/zh.json](agent-explainer/agent_explainer/web/i18n/zh.json) · [en.json](agent-explainer/agent_explainer/web/i18n/en.json) | 手写 | **唯一文案来源**，721 个键 / 22 个域，中英双向 100%（4.12 加 `audit.chip.loading`）；4.7 起含 `demo.label.<夹具原名>`（运行名/agent 名的双语，代码只拼前缀，所以 `app.js` 里 0 条 CJK 字面量）与 `compare.field.other`（字段标签兜底） |
| [i18n/embed.js](agent-explainer/agent_explainer/web/i18n/embed.js) | 生成 | 字典内联副本：`file://` 禁止 `fetch`，静态打开必须自带文案 |
| [i18n/keys.md](agent-explainer/agent_explainer/web/i18n/keys.md) | 生成 | 键清单（键 + 中文 + 英文），供 t4/t10 对照 |
| [demo-data.js](agent-explainer/agent_explainer/web/demo-data.js) | 生成 | `samples/action_records.json` 的原样副本（12 条 ActionRecord，含 `_meta`）＝运行 A（中文演示数据） |
| [demo-data-en.js](agent-explainer/agent_explainer/web/demo-data-en.js) | 生成 | 运行 A 的**英文孪生**：`samples/action_records_en.json` 的原样副本（同 id、同机器字段，文案是原生英文，零汉字）。`app.js` 按语言选数据集，所以英文界面不会出现中文数据（4.7） |
| [compare-data.js](agent-explainer/agent_explainer/web/compare-data.js) | 生成 | **运行 B**：由 `selfcheck.mjs` 里的 `COMPARE_VARIANT` 从同一份样例派生的字段级变体（12 条，含 `_meta` 与派生说明），只为演示对比，不是真实数据 |
| [selfcheck.mjs](agent-explainer/agent_explainer/web/selfcheck.mjs) | 手写 | 91 条结构断言 + 生成器；写出 [selfcheck-report.json](agent-explainer/agent_explainer/web/selfcheck-report.json) |
| [README.md](agent-explainer/agent_explainer/web/README.md) | 手写 | 本文 |

## 2. 怎么看

```powershell
# 手机视图（悬浮球 + 底部面板）：直接双击 index.html，或
start msedge --new-window "file:///C:/TraceLens/agent-explainer/agent_explainer/web/index.html"
```

查询参数（给走查、录屏和自动化用）：

| 参数 | 作用 |
| --- | --- |
| `?open=1` | 直接展开底部面板（跳过悬浮球首屏） |
| `?lang=en` | 英文界面（选择会写入 `localStorage`，下次自动恢复）；同时加载**英文演示数据** `demo-data-en.js`（数据集选择记在 `localStorage` 的 `tracelens.dataLang`，缺省跟随界面语言） |
| `?view=desktop` | 强制桌面布局（侧栏 + 三区 + 时间轴 + 对比），不改变浏览器窗口 |
| `?filter=needs_me,running` | 状态筛选：`needs_me` / `running` / `done` 任意组合，`all` 表示全开；选择写入 `localStorage`（`tracelens.filter.v1`），下次自动恢复 |
| `?step=5` | 时间轴直接落到第 5 步（1 基），进度条、缩略卡、列表高亮、清单定位一起跟上 |
| `?focus=act-demo-0006` | 按动作 id 定位（同点侧栏那一行） |
| `?compare=on` | 打开对比模式（`data-compare="on"`，仅 ≥1024px 显示面板） |
| `?probe=1` | 页面尾部写出**可见**的探针报告，并写入 `<script type="application/json" id="probe-result">`，供自动化读取 |
| `?frame=375x812` | 把 `#app` 钉成指定宽高的盒子（`1440x900` 桌面、`375x812` 手机、`1440x2600` 看长页面）；布局按钉框宽度重算，视图强制切到对应档 |

组合示例：`index.html?probe=1&open=1&lang=en`（英文 + 面板展开 + 结构探针）、
`index.html?probe=1&frame=1440x900&compare=on`（1440×900 桌面 + 对比）、`index.html?probe=1&frame=1440x900&filter=needs_me,running`（只看等你的）。

## 2b. 桌面端（3.2）多了什么

- **左侧固定侧栏**：品牌 / 导航 / 三区统计 → **状态筛选**（三个按钮 = 三个区，勾谁显示谁，被关掉的区在清单里收成一行「这一区被筛选收起来了（N 项）」，清单上方写「筛选已隐藏 N 项」）→ **动作列表（最新在前）**：12 条按时间倒序，点一条就在右侧清单定位并高亮（`.card.is-focused` + 行高亮 + 时间轴跟上）。筛选状态记在 `localStorage`。
- **主区时间轴**：`input[type=range]` 进度条（`aria-valuetext` = 「第 N 步，共 12 步，时间」）+ 缩略卡（一句话 + 状态/风险/演示徽标 + 颜色角色）+ 刻度尺；右侧时间轴栏是 12 个可点刻度。拖动只重绘一次，列表与卡片同步高亮。
- **对比模式**：两次运行并排（运行 A = 样例本体，运行 B = `compare-data.js` 的字段级变体），按 `sequence_no` 对齐（13 行 = 12 步 + 只在一边出现的步），逐字段比 8 项：状态 / 动作类型 / 风险 / 耗时 / 花费 / 工具 / 执行回执 / 把握度。差异分四类高亮：**两次不一样**（黄）、**只有 A 有**（蓝）、**只有 B 有**（紫）、**相同**（灰），图例带计数，摘要行写「N 处不同」；把 A、B 选成同一个运行时诚实显示「两次一模一样」。
- 桌面网格：`侧栏 | 主区 | 时间轴`，对比面板在主区下方整行展开；`?frame=1440x2600` 可一次截全。

## 3. 产品规则（写死在骨架里，有断言兜底）

- **三个区，顺序固定**：待确认 → 进行中 → 已完成。
- **待确认是串行队列**：同一时刻只摆 1 件（`act-demo-0004`），其余显示「还有 N 件在排队」。
- **颜色语义**：绿=完成、黄=进行中、红=失败、蓝=等你、灰=拒绝/跳过。
- **默认只给一句话**（L1）：花费、耗时、分步说明都在展开后的详情里。
- **决定 ≠ 生效**：干预按钮在 3.1 是 `disabled`，页面写「决定只会记录进审计；是否真的生效，取决于执行端是否接入」，执行回执按原值展示（`data-receipt-state`）。任何地方都不出现「已强制停止」。
- **演示徽标是机器判定的**：同时检查 `demo_data=true`、`synthetic=true`、`source.kind=demo`；命中才出现顶部常驻横幅 + 每条记录一枚徽标。换成不带这三个标记的数据，横幅与徽标全部消失（自检 20/21）。
- **筛选只藏区，不改数据**：进度、统计、时间轴步数始终按 12 条全量计算，被收起来的区显示计数，绝不假装「没有了」（自检 32）。
- **对比只比机器字段**：运行 B 是同一份合成样例的字段级派生（`_meta.variant` 写清怎么派生的），面板不编造中文句子；两边选同一个运行就明说「一模一样」（自检 34、36）。

## 4. 生成器（改字典 / 改样例后必须跑）

```powershell
cd agent-explainer/agent_explainer/web
node selfcheck.mjs --sync-demo     # 中文集 -> demo-data.js，英文集 -> demo-data-en.js（运行 A 的两份语言）
node selfcheck.mjs --sync-compare  # 运行 A + COMPARE_VARIANT -> compare-data.js（运行 B）
node selfcheck.mjs --sync-embed    # i18n/*.json -> i18n/embed.js
node selfcheck.mjs --sync-keys     # i18n/*.json -> i18n/keys.md
node selfcheck.mjs                 # 跑全部断言，退出码 0=通过
```

自检覆盖：三区渲染与数量（4/1/7、12 张卡片、串行队列）、颜色映射、展开/收起（点击 + Enter + Space）、
中英切换（含 `localStorage` 恢复）、演示徽标必现/必隐、令牌与动效 ≤200ms、`var(--token)` 全部有定义、
键盘可达（`tabindex` / `aria-label` / `aria-expanded` / `aria-controls` / skip-link / Esc）、
离线纯度（无 XHR / WebSocket / 远程 URL）、语言与状态每次变更后恰好重绘一次；
3.2 追加：侧栏倒序列表与点击定位、状态筛选（收起/恢复/全关提示/`localStorage` 与 `?filter` 恢复）、
时间轴拖动联动（预览卡 + 刻度 + 卡片高亮、越界钳制、`?step` / `?focus`）、
对比模式（13 行 × 8 字段、四类高亮计数与图例一致、自我对比诚实）、
验证台三档钉框（1440×900 / 820×1180 / 375×812）与英文桌面无中文残留、`compare-data.js` 未漂移。
4.7 追加（双语演示数据）：`demo-data-en.js` 是 `samples/action_records_en.json` 的字节拷贝、两份数据集 id 与
机器字段完全一致（只有文案语言不同）、英文数据与英文卡片与运行名 **CJK=0**、中文界面确有中文数据、
切换语言即换数据集且恰好重绘一次、切回中文恢复中文数据集、运行名/agent 名都出自 `demo.label.*` 字典
（所以 `app.js` 里 0 条 CJK 字面量）、对比表字段标签双语齐平且兜底键 `compare.field.other` 两边都在。

## 5. 像素级验证（真实浏览器 + 手机视口）

结构层面靠 `node selfcheck.mjs`（91 条断言：第 30 条证明「直接双击 `index.html` 也会自启动」，第 31 条证明验证台与钉框，
32–36 条是 3.2 的侧栏 / 时间轴 / 对比 / 三档响应式 / 运行 B 数据完整性，41–44 条是 4.7 的双语演示数据）。像素层面用页面内置的**验证台**：

- `?probe=1`：在页面尾部打印一份可读报告，同时写入 `#probe-result`（JSON）供自动化取。
  渲染过程中任何一步抛错都会被打成 `js error: <步骤>: <消息>` 与 `PROBE: FAIL`，不会白屏。
  3.2 起多出两行：`desk sidebar=N filterOn=… hidden=… scrub=i/12 focus=… focusedCards=…`
  与 `compare open=… runs=… steps=… cells=… diffs=… kinds=…`。
- `?frame=1440x900` / `?frame=375x812`：把 `#app` 钉成盒子并测出 `box / scrollWidth / horizontalScroll`。
  媒体查询跟随的是浏览器窗口，所以 rig 用更高优先级的 `.app[data-frame][data-frame-layout=…]` 规则
  把布局重算成对应档位（手机档隐藏侧栏/时间轴/对比、显示悬浮球；桌面档三栏网格；平板档只留时间轴）。

推荐走查顺序（DSH 浏览器 lane / CDP，无需缩放窗口）：

```text
index.html                                          # 裸文件打开：无任何参数也要能完整演示
index.html?probe=1&frame=375x812                    # 手机首屏：悬浮球 + 进度环 + 任务名 + 演示徽标
index.html?probe=1&open=1&frame=375x812             # 手机展开：三区、串行队列、颜色语义、每条徽标
index.html?probe=1&frame=1440x900                   # 桌面：侧栏（筛选 + 倒序列表）| 三区 | 时间轴
index.html?probe=1&frame=1440x900&step=5            # 拖到第 5 步：预览卡 + 刻度 + 卡片同步高亮
index.html?probe=1&frame=1440x900&filter=needs_me,running   # 关掉「已完成」：清单 5 条 + 收区提示
index.html?probe=1&frame=1440x2600&compare=on       # 对比：13 行 × 8 字段，四类高亮
index.html?probe=1&frame=1440x2600&lang=en&compare=on       # 英文桌面对比：界面文案无中文残留
```

判定标准（已落在 `verify/` 截图里，可直接读）：
`PROBE: PASS (no horizontal scroll)`、`box=1423 scrollWidth=1423`（1440 档）与 `box=373 scrollWidth=373`（375 档）、
`zones needs_me=4 running=1 done=7`、`cards=12 demoBadges=12`、`dictKeys=239`、`missingKeys=0`、`rigError=none`、
`desk sidebar=12 … scrub=1/12`、`compare open=true runs=2 steps=13 cells=104 diffs=27 kinds=changed,only_a,only_b,same`。
逐张截图见 [verify/README.md](verify/README.md)。

沙箱限制（写给队长和 t10）：受限沙箱里 Chromium 的命名管道 IPC 被拒（`mojo ... platform_channel.cc: Check failed`、
crashpad `OpenProcess: 拒绝访问 (0x5)`、`Multiple targets are not supported in headless mode`），
所以 `msedge --headless=new --dump-dom/--screenshot` 在会话沙箱内跑不通（本会话也无法申请提权）。
需要在非受限终端里跑无头 Edge 时，直接套用上面这些 URL 即可，页面自己会吐报告。

## 6. 给 t7 / t8 的接线点

- 布局容器：`#app[data-view]`（`mobile` / `tablet` / `desktop`）与 `#app[data-compare]`（`on` / `off`）；
  CSS 在 `min-width:1024px` 给出 `sidebar | 主区 | timeline` 三栏网格 + 对比整行；导出 / 审计仍是挂载点。
- 干预入口：`[data-hook="intervene"]` 容器里的 `[data-hook="intervene-button"]`（3.1/3.2 恒为 `disabled`）。
- 数据接入：`TraceLensUI.setRecords(payload)`（数组或 `{_meta, records}`）＝运行 A；
  `TraceLensUI.setCompareData(payload)` ＝ 运行 B（不传就只有一个运行，面板诚实说明）。
- 桌面 API：`filterState()` / `setFilter({needs_me,running,done})` / `toggleFilter(zone)` /
  `selectRecord(id)` / `focusedRecord()` / `setTimelineIndex(i)` / `timelineRecord()` / `runsOf()` /
  `setCompare('a'|'b', key)` / `setCompareOpen(bool)` / `compareOpen()` / `compareModel()`（`{a,b,steps,diffs,classes,counts}`）。
- 其它 API：`setLang` / `setView` / `openSheet` / `toggleCard` / `setGranularity` / `probe()` / `onRender(fn)`。
- 数据集 API（4.7）：`dataLang()`（当前装载哪份演示数据，`zh`/`en`）、`demoDataFor(lang)`、
  `applyDataLang(lang)`（切语言时换数据集，返回是否成功换到）、`datasets`（两份数据集的 lang / 全局名 / 文件名）。
- 骨架版本标记：`TraceLensUI.SKELETON_VERSION === '3.1-skeleton'`（3.2 在同一套文件上增量实现，未改版号）。

## 7. 已知边界（属于后续子任务，不在 3.1 / 3.2 范围）

- 演示记录正文有**两份语言**（4.7）：中文集 `demo-data.js`、英文集 `demo-data-en.js`（英文文案由
  `tools/gen_samples_en.py` 按 198 条对照**原生生成**，不是机翻，零汉字）。英文界面读英文集，
  所以界面文案与数据一起是英文；自检对英文界面直接断言 **数据 + 卡片 + 运行名 CJK=0**
  （原先"剔除记录内容只看界面文案"的老口径仍保留，用来抓界面文案自身的中文残留）。
- 运行 B 是演示用的派生数据（`compare-data.js` 的 `_meta` 写清来源与派生方式）；真实的多运行对比由 t7 后端提供。
  运行 B 只有一份中文数据，它的运行名/agent 名在英文界面下由 `demo.label.*` 字典出英文文案（不新增第二份对照夹具）。
- 设置页与干预写入已由后续子任务落地（4.1 / 4.5）；审计列表与导出见下面第 8 节（4.8）。
- 筛选、语言、数据集都记在 `localStorage`（`tracelens.filter.v1` / `tracelens.ui_language` / `tracelens.dataLang`）：
  走查时用 `?filter=all` / `?lang=zh` 显式复位。
- 时间显示按记录自带时区（`+08:00`）格式化，不随宿主时区漂移，便于测试断言。

## 8. 审计面板与导出（4.8 · t21）

侧栏的两个占位块现在是真面板，数据全部来自服务端，页面自己不算哈希链、不猜权限。

- **Audit log 面板**（`GET /api/audit`，需要 `audit.read`）：每条显示**时间 / 操作人 / 任务 / 决定 / 哈希短码**
  （行上带完整 `data-hash` 供取证），顶部三枚 chip = 哈希链状态（`data-ok`，断链时给 `audit.chip.chain_broken{code}`）、
  服务端条目总数、存储模式（落盘 / 内存）。顶栏 `audit-refresh` 重新读第一页，`audit-more` 按
  `AUDIT_PAGE_SIZE`（默认 8）续页；`audit-filter` 是决定筛选（全部 / 同意 / 拒绝 / 改参数）。
  空态 `audit.empty`（有筛选时 `audit.empty_filtered`）；错误态 `audit.error.*` 走字典文案，
  **不打印服务端原文**（500 `AUDIT_CHAIN_BROKEN` → 「哈希链已断开」那句）；
  没有 `audit.read` 时按钮全禁用并显示 `audit.note.readonly`（只读说明）。
- **Export audit 面板**（`GET /api/audit/export`，需要 `audit.export`）：CSV 直接下载服务端字节
  （文件名取 `Content-Disposition`，行数 / 格式 / 内容指纹取 `x-tracelens-export-*`），
  「打印 / 另存 PDF」打开可打印页（页面自身无脚本，浏览器里 Ctrl+P 存 PDF），PDF 按钮在服务器缺
  reportlab 时自动退回可打印 HTML，并在面板留一行 `export.note.pdf_fallback`。
  按钮的启用 / 禁用只看**服务端下发的权限**（`canPermission('audit.export')`），禁用时旁边的
  `export-need` 行必须写清原因（`export.note.need_permission` / `export.note.offline`），页面上没有
  按角色名写死的分支。导出会带当前筛选（`decision` / `actor_id` / `q` / `since` / `until`）。
- 网络出口只有一个：导出走 `apiRequest(..., {response:'text'})` 这同一道受 allowlist 约束的门
  （自检第 27 项数 `fetch(` 的调用点数，仍是 2 个）；路径只从 `API_ENDPOINTS` 出，查询参数只从
  `AUDIT_QUERY_KEYS` / `EXPORT_QUERY_KEYS` 白名单出。
- 演示 / 无服务（`?api=off`）时面板说人话：`audit.error.offline` + `export.error.offline`，**0 次请求**；
  无头自检里没有 Blob / `window.open`，就如实报 `export.note.no_popup`，不假装下载成功。
- 自检新增第 72–75 项：面板渲染服务端条目（含哈希短码与 chip）、筛选与翻页真实落到
  `limit/offset`、导出按钮与 `audit.export` 的映射（禁用 + 原因 + **0 请求**）、
  空态 / 断链 / 离线文案全部来自字典（zh+en 齐、参数名一致、不泄露服务端原文）。
- 新增 API：`audit()` / `auditRefresh()` / `auditMore()` / `auditFilter(patch)` / `auditSetLimit(n)` /
  `auditPathFor(page)` / `exportAudit('csv'|'pdf'|'html')` / `exportPrint()` / `exportState()` /
  `exportPathFor(format)`；`api.audit` = `api/audit`，`api.auditExport` = `api/audit/export`。

## 9. 导入面板与记录持久化（4.9 · t22）

QA 5.1 量到的 D1「粘贴 JSON / 上传 trace 没有用户可见入口」+「重启回到演示 12 条」，在这一节闭合。

- **两个入口，一份逻辑**：手机视口在底部面板末尾（`#sheet-import`，在"需要我"区之后、`sheet-foot` 之前），
  桌面视口在侧栏（`#import-box`，在导出 / 审计之前）。两端都调 `renderImportPanel(box, {titleId})`，
  状态存在 `state.importPanel`（草稿文本、格式、mode、文件名、结果、反馈）——刷新页面不猜、不重建。
- **只有 `api/import` 这一道门**：`api.import` = `api/import`，请求只走 `authFetch('import', body, {method:'POST'})`
  （自检第 27 项的 allowlist 已加这条路径与这个调用点）；请求体字段固定
  `{payload, filename?, format?, mode?, strict?, lang?}`，页面上没有第二套拼接。
- **粘贴与上传同一条路**：粘贴 = 文本域（`import-text`）实时显示 UTF-8 字节数；上传 = `<input type="file">`
  读**文本内容**填进同一个草稿（`import-read-file`），文件名进 `filename`，页面不发 multipart。
- **前端先拦、服务端是权威**：`importClientCheck()` 在发出前拦"空内容 / 非法 JSON / 超过
  `IMPORT_MAX_BYTES`（65536）"，超限时**0 次请求**并显示 `import.validation.*`（带 `bytes/limit` 参数）；
  服务端仍是权威（413 `INTERVENE_BODY_TOO_LARGE`、413 `IMPORT_TOO_LARGE`、400 `IMPORT_TOO_MANY` 等）。
  条数上限 `IMPORT_MAX_RECORDS = 200` 与 chip 上显示的 `import.chip.limits` 同源。
- **格式与 mode 是选择项，不是页面猜的**：格式下拉 = 自动识别 + `langfuse / otel_genai / mcp_events / native`
  （`IMPORT_FORMATS`，与服务端 `/health.import.formats` 一致）；mode 下拉 = `upsert / create / update`
  （`IMPORT_MODES`），选完在下面一行显示 `import.mode.note.<mode>` 的中文/英文说明（默认 `upsert`：
  撞 id 就更新事实字段，**用户的决定与执行回执是受保护字段，导入覆盖不掉**）。
- **结果必须说清**：成功后摘要 8 枚 fact = 入库 / 新增 / 更新 / 重复 / 跳过 / 总计 /
  落盘（`import.summary.durable` + `data-durable` 真值）/ 演示徽标数；列表立刻出现新记录，
  徽标仍由服务端下发的 `demo_data` / `synthetic` / `source.kind` 判定（导入的记录不是演示数据）。
  失败一律映射到 `import.error.<码名>`（27 个键的 `IMPORT_ERROR_KEYS` 表）+ 结构化参数，
  **页面从不打印服务端原文**。
- **权限与离线不装样子**：`viewer`（无 `data.write`）与离线（`?api=off`）时按钮禁用、
  显示 `import.note.need_permission` / `import.note.offline`，**0 次请求**；缺 CSRF 的 403 有独立文案。
- **持久化的可见性**：chip `import.chip.durable` 读 `meta.records_durable` / `meta.records_store.durable`
  （默认写 `var/records.jsonl`；`--no-records-store` 时显示 `import.chip.memory` = 这台服务不落盘）；
  写失败（500 `RECORD_STORE_WRITE_FAILED`）如实报，不记进"导入成功"计数。
- 新增 API：`importState()` / `importSet(patch)` / `importRun()` / `importClear()` / `importCheck()` /
  `importBlocked()` / `importBytesOf(text)`，常量 `IMPORT_MAX_BYTES` / `IMPORT_MAX_RECORDS` /
  `IMPORT_FORMATS` / `IMPORT_MODES`；`probe().import` 暴露面板快照（status / bytes / format / mode /
  canWrite / result / requests…）给自检与排障。
- 自检新增第 **76–80** 项（共 80 项全绿）：入口在两个视口都挂载且选项齐平、粘贴一批 → 1 次 POST →
  列表 12→20 且新记录不是演示数据、上传文件名进请求体且 70 KB 时 0 请求、字典 73 键 zh/en 齐平 +
  28 个错误码逐个"服务端真有这个码"、viewer / 离线 / 空内容的 0 请求与 400/413/500 的文案与参数。

## 10. 移动端入口可达性（4.10 · t23）

QA 5.1 量到的 D-4：设置、审计、用户管理、导入、导出、语言切换只存在于 ≥1024px 的侧栏，
375 宽的手机**没有任何路径**能到达它们。这一节把这条路补上，且不重写任何后端。

- **一个「更多」入口，一份列表**：顶栏 `topbar__more` 按钮（`aria-controls="more-sheet"`、`aria-expanded`、
  `aria-label` 逐条念出六个入口）打开底部 `#more-sheet`（`role="dialog"` + `aria-modal` +
  `aria-labelledby="more-title"`）。列表固定 6 条、顺序固定（`MORE_TARGETS`）：
  设置 → 审计记录 → 用户管理 → 导入数据 → 导出审计 → 语言切换。
- **不可用不隐藏，写清原因**：每条算一次 `moreEntryState()`——设置 / 语言永远可用；导入看 `data.write` + 在线
  （`import.note.need_permission` / `import.note.offline`）；审计 / 导出看 `audit.read` / `audit.export` + 在线
  （`audit.note.readonly` / `audit.error.offline` / `export.note.need_permission` / `export.note.offline`）；
  用户管理看 `users.manage`、在线、会话（`more.reason.users` / `more.reason.offline` / `more.reason.signin`）。
  禁用项用 `aria-disabled="true"` + 可见原因节点（`more-reason`，其 `data-i18n` 就是那句字典键），
  **不用 `disabled` 属性**，键盘和读屏都能停在它上面听到原因；点它不打开任何东西，只在列表底部落一行
  `more.feedback.blocked{reason}` 并计数（`probe().more.blocked`）。
- **打开的是同一块面板，不是副本**：审计 / 导出 → 侧栏 `#timeline` 里 4.8 的**同一块**面板，手机上以抽屉出现
  （`.rail__drawer` 标题「面板：{title}」+「返回更多入口」+「关闭」，打开时 rail `aria-hidden=false` +
  `role="dialog"` + `aria-labelledby="more-drawer-title"`），抽屉宽度 `min(88vw,340px)`，只出现在手机 / 平板档；
  导入 → 4.9 的 `#sheet-import`（打开后 `data-more-focus="import"` 高亮那块）；设置 / 用户 → 4.5 / 4.3 的
  `#settings-center` 与用户面板（`data-more-screen`）。自检第 83 项逐个点名挂载点：`audit` / `export` /
  `export-buttons` / `audit-list` / `settings-center` / `users-panel` / `users-scroll` 各恰好 1 个，
  导入编辑器全页恰好 2 个（手机 + 桌面，没有第三份）。
- **键盘与 Esc**：`Esc` 依次关抽屉 → 列表 → 底部面板，并把焦点交回「更多」按钮；抽屉的返回 / 关闭与设置 /
  用户的关闭按钮都会把 `data-more-screen` 复位（自检 84）。
- **语言切换就在列表里**：点「语言切换」界面整体换语言，列表**保持打开**，六条标题、提示、禁用原因、
  `aria-label` 与演示数据语言（`dataLang`）同步重绘，一次动作恰好一次重绘（自检 85）。
- **不破版、不破桌面**：手机各层都定位在 `#app` 盒子内（钉框档 `.app[data-frame]` 下改 `position:absolute` +
  `height:var(--frame-h)`），用户表加 `.users__scroll` 自己横滚 → 手机盒实测 `box=358 scrollWidth=358
  horizontalScroll=false`（截图里探针 `PROBE: PASS`）；≥1024 档 `applyMoreChrome` 把 `menu/open/screen`
  全部强制关闭，侧栏六个入口原样（自检 81 / 84 + 桌面截图）。
- 新增 API：`MORE_TARGETS` / `moreState()` / `moreEntries()` / `moreMenu(open)` / `moreOpen(target)` /
  `moreClose(keepMenu)` / `moreSwitchLanguage()`；`probe().more` = 视图 + 三层开关 + 六条的 enabled/reason +
  五个计数（opens / closes / blocked / switches / notice）；`probe().rendered` 增加 `moreButtons` /
  `moreSheet` / `moreEntries*` / `moreReasons` / `moreDrawerHead` / `moreDrawerButtons` / `usersScroll`。
- 自检新增第 **81–85** 项（共 85 项全绿）：入口存在 + 桌面拒绝、按权限禁用并给原因（离线 / viewer / 本地 /
  无会话四种身份）、每条真的打开对应面板且全局只有一份、375×812 盒内布局 + 键盘 / Esc + 桌面侧栏不变、
  语言切换后文案与原因同步 + `more` 域 31 键 zh/en 齐平（en 零中文、参数名一致）。
- 文案：`i18n/{zh,en}.json` 新增 `more` 域 31 键（字典 689 → **720 键 / 22 域**），`i18n/embed.js` 与
  `i18n/keys.md` 已用 `node selfcheck.mjs --sync-embed --sync-keys` 重新生成，
  `python ../../tools/i18n_lint.py` 五道闸门全过。
- 逐张截图见 [verify/README.md](verify/README.md) 的「4.10 移动端入口可达性证据」。

## 11. 语言切换不丢数据、审计面板说实话、卡片行不重叠（4.12 · t25）

QA 5.2 模拟走查出的 1 个阻断 + 4 个高优先级，全部只改 `web/`，没有动后端
（`GET /api/records` 本来就没有 `lang` 参数，所以无需改 API）：

- **B-01（阻断）**：`setLang` 过去把 `state.records` 换成当前语言的 12 条演示集 → 导入的 8 条消失、
  已批准的那条回到「等你确认」。现在分两条路：
  - 连上服务（live）：`state.records` 一个字不动，`applyDataLang` 只 `renderAll()` 一次，然后
    `refreshRecordsFromApi({reason:'lang', force:true})` 重新 `GET api/records` 并按 id 强制合并 ——
    服务器是数据的唯一来源，语言只影响界面；
  - 离线 / `file://`：`carryUserRecords` 把**非合成**记录（导入的、本地改过的）原样带到目标语言，
    已记录的决定按 id 平移到目标语言的孪生记录（浅拷贝，绝不改 `demo-data.js` 里的对象），
    再快照 → 恢复视图（状态筛选、展开的卡片、时间轴位置）；探针里 `dataLangOwn / dataLangDecided /
    dataLangCarried` 就是这笔账，且整次切换只重绘一次（4.7 的「切语言恰好重绘一次」仍然成立）。
  - 真浏览器 + 真服务实测：zh→en 之后 `lang swaps=1 kept=20 refreshes=1 merged=20`、`cards=20`、
    `act-demo-0004` 仍是 `Approved`、切前展开的那张卡片仍展开、审计面板仍是已加载的那几条；
    自检 86（live）/ 87（离线）把这条钉住。
- **H-01（审计首屏 0 条）**：启动渲染里的 `flushAuditAutoLoad()` 自动读一次 `GET api/audit`
  （每会话最多一次，探针 `auto=1/0`）。读之前不吹牛：不渲染「共 N 条」「已落盘（追加式文件）」两枚 chip，
  链 chip 是虚线 `data-ok="pending"` + 「哈希链：正在读取…」，外加一行诚实提示。
  实测首屏（不点任何按钮）：`audit loaded=true rows=7 chips=3 store=1 pending=0`，数据来自真 `.jsonl` 文件。
- **H-02（决定 / 导出后审计不动）**：`auditAfterWrite(reason)` 挂在本页写出成功的三处（决定、导出、导入），
  自动重读一次审计并记下 `lastWriteReason`。实测点「同意这个方案」→ 面板自己从 2 条变 3 条、
  `auto=1/1 reason=decision:act-demo-0005`、底部一行「已刷新：显示最近 3 条。」，全程没点过「刷新」。
- **H-03（别处做的决定不刷新这一条）**：根因是 `mergeIncomingRecords` 的「没变就跳过」只看 running 字段
  （时间戳 / 状态 / 摘要 / 耗时 / 决定），而另一窗口发来的决定可以只动 `user_intervention`。
  现在 `action.decision_recorded` 帧强制合并（`{force:true}`），并给卡片可看见的记号：
  `<article data-decided>` + 状态点外圈、展开箭头转绿。实测在另一侧 `POST /intervene` 决定后，
  本页那条卡片变成「请求确认 DemoAlbum/**｜已同意」并从「待确认」区移走（降级轮询 4 秒内到；SSE 见下面边界）。
- **H-04（1400px 提示竖排、与「展开详情」打架）**：卡片头部改成 grid —— `状态点 | 一行标题 | 展开详情`，
  提示语「点这里看为什么、结果和花费」落在**第二行独占整宽**（`grid-column:2;grid-row:2` +
  `overflow-wrap:normal`，中文不再一个字一行）；1024–1440 档区域列最小 320px。1400×900 真浏览器截图见 verify。
- 新增 API：`ui.refreshRecords(opts)` / `ui.mergeIncomingRecords(incoming, opts)`；`probe().audit` =
  `{loaded, rows, chips, store, pending, loading, autoLoads, autoRefreshes, autoTried, lastWriteReason}`；
  `probe().rendered` 增加 `auditChainChips / auditChainPending / auditCountChip / auditStoreChip /
  auditPendingLines / auditLoadingLines / cardToggles / cardHints`；探针多两行
  `audit loaded=… rows=… chips=… store=… pending=… auto=…/… reason=…` 与
  `lang swaps=… kept=… carried=…/… refreshes=… merged=…`。
- 自检新增第 **86–91** 项（全套 **91/91** 全绿）：live 切语言不丢导入与决定（切前 4 次 + 切后 1 次
  `api/records` 读，筛选 / 展开卡 / 时间轴位置保持）、离线切语言带走 8 条算子行 + 3 条已记录决定
  （carried=11）且英文界面零中文、审计首屏自动读一次（离线 0 请求）与读前的诚实提示、
  决定与导出各触发一次自动重读（手动刷新仍算手动）、只改 `user_intervention` 的决定帧也要 `updated=1`
  并把卡片 `data-decided` 从 `false` 翻成 `true`、卡片头 grid 与提示行（12 张卡 = 12 行提示，中文不逐字竖排）。
- 文案：新增 `audit.chip.loading`（「哈希链：正在读取…」/ `Hash chain: reading...`），
  字典 720 → **721 键 / 22 域**，`i18n/embed.js` 与 `i18n/keys.md` 已用
  `node selfcheck.mjs --sync-embed --sync-keys` 重新生成，`python ../../tools/i18n_lint.py` 五道闸门全过。
- **边界（诚实记录，未改后端）**：这台机器上，SSE 连接显示活着（`live (streaming)`）时，别的客户端做出的决定
  **没有帧推到本页**（本页只在启动补发与自己写成功时收到帧）；把服务改到 `--stream-limit 0` 走降级轮询后，
  4 秒内就刷新了该条记录。web 侧两条路都已修（帧强制合并 + 按 id 合并 + 卡片记号），
  但「帧根本没到」不是页面能凭空造出来的 —— 已报告队长，属服务端 / 环境侧；
  4.4 的游标补发与心跳重连在页面侧照旧（探针 `cursor` 与 `实时：降级轮询（每 4000 毫秒）` 都如实显示）。
- 逐张截图与 HTTP 逐条实录见 [verify/README.md](verify/README.md) 的「4.12 语言切换 / 审计刷新 / 卡片行证据」。
