# 像素级证据（真实浏览器）：3.1 骨架 + 3.2 桌面端

这些截图是 3.1 骨架在**真实浏览器引擎**（DeepSeek Harness 的浏览器 lane，Microsoft Edge / CDP，
以 `file://` 直接打开，无服务器、无构建）里逐张抓下来的，用来补上 `selfcheck.mjs`（结构断言）
证明不了的那一半：布局、颜色、可读性。

抓法（页面自带验证台，不需要缩放浏览器窗口）：

1. `set_value elementId=new-tab value=<URL>` 打开页面；
2. `screenshot browserTab=<tab>` 抓页面视口；
3. 手机视口用 `?frame=375x812` 把 `#app` 钉成 375×812 盒子，`?probe=1` 让页面把测量结果写成可见文本。

| 文件 | 打开的 URL | 看到什么 |
| --- | --- | --- |
| `index-file-open.png` | `index.html`（**无任何参数**） | 双击文件即可完整演示：侧栏（导航 + 4/1/7 统计 + 设置入口 + `3.1-skeleton`）、顶栏（语言切换、58% 进度、`演示数据 12`）、演示横幅、三区卡片、时间轴刻度、导出/审计占位。语言是英文 = 上次选择的 `localStorage` 恢复成功。 |
| `phone-375x812-closed.png` | `?probe=1&frame=375x812` | 375 宽盒子里的首屏：悬浮球（58% 进度环 + 「任务 / 整理演示相册并去重」+ 演示徽标）、演示横幅三条机器校验（`demo_data=true` / `synthetic=true` / `source.kind=demo`）、语言切换。 |
| `phone-375x812-open.png` | `?probe=1&open=1&frame=375x812` | 底部面板展开（中文）：已完成 7 项绿卡、失败卡为红、拒绝卡为灰、卡片左侧色条 + 状态点 + 一行话 + `演示数据` 徽标 + `展开详情`；悬浮球切成「收起面板」（`aria-expanded=true`）。 |
| `phone-375x812-open-en.png` | `?probe=1&open=1&lang=en&frame=375x812` | 同一屏切到英文：`Needs you / Running / Done`、`Tap to see why, the result and the cost`、`3 more in the queue, shown after this one`。界面文案全部来自 `i18n/en.json`；记录正文仍是中文（数据侧英文属 t4/t7 范围）。 |
| `desktop-1258.png` | `?probe=1&view=desktop&compare=on` | ≥1024 桌面栅格：侧栏｜三区（蓝=等你、黄=进行中并排）｜时间轴（12 个刻度 + 导出/审计挂载点）+ 对比挂载点（`Compare mode … ships in 3.2`）。 |
| `probe-report-375x812.png` | `?probe=1&frame=375x812`（滚到底） | 页面自报的测量结果，逐行可读：<br>`view=mobile`、`dictKeys=193 dictSource=embedded`、<br>`zones needs_me=4 running=1 done=7 \| cards=12 demoBadges=12 demoBadge=true`、<br>`progress=58% i18nNodes=593 missingKeys=0`、<br>`box=373 scrollWidth=373 horizontalScroll=False`、<br>`PROBE: PASS (no horizontal scroll)`、`rigError=none`。 |

关键判定：

- **375 宽无横向滚动**：浏览器实测 `scrollWidth(373) == clientWidth(373)`，报告行 `PROBE: PASS`。
- **三区与数量**：`needs_me=4 / running=1 / done=7`、12 张卡片、12 枚演示徽标、`demoBadge=true`。
- **文案全部来自字典**：`dictKeys=239`（3.2 后从 193 增至 239）、`dictSource=embedded`（`file://` 下用 `i18n/embed.js`，http(s) 下 app.js 会回读 JSON）、`missingKeys=0`。
- **没有静默失败**：验证台把渲染每一步包在 `rigSafe` 里，抛错就打印 `js error: <步骤>` 并判 `PROBE: FAIL`；这些截图里 `rigError=none`。

## 3.2 桌面端证据（同一批抓法）

| 文件 | 打开的 URL | 看到什么 |
| --- | --- | --- |
| `desktop-1440x900.png` | `?probe=1&frame=1440x900` | 1440×900 桌面三栏：侧栏（状态筛选三个按钮 4/1/7 + 倒序 12 条动作列表）｜主区（时间轴进度条 + 第 1 步缩略卡 + 三区卡片）｜时间轴 12 刻度 + 导出/审计挂载点。破版判定点：`box=1440 scrollWidth=1440 horizontalScroll=False`。 |
| `desktop-timeline-step5-1440x900.png` | `?probe=1&frame=1440x900&step=5` | 进度条拖到第 5 步：`aria-valuetext=第 5 步，共 12 步，11-09 09:01`、缩略卡显示「要删除 12 个重复文件，等你确认」+ 等你确认 / 严重风险 / 演示数据三枚徽标、清单顶部出现「已定位到第 5 步：…」、右侧第 5 个刻度选中。 |
| `desktop-filter-1440x900.png` | `?probe=1&frame=1440x900&filter=needs_me,running` | 关掉「已完成」：按钮变「已完成：已收起，7 项」，倒序列表只剩 5 条（列表标题「共 5 条」），已完成区收成一行「这一区被筛选收起来了（7 项）」，清单上方「筛选已隐藏 7 项」；进度仍是 58%（按 12 条全量算）。探针行：`cards=5 … desk sidebar=5 filterOn=needs_me,running hidden=7`。 |
| `desktop-compare-1440x2600.png` | `?probe=1&frame=1440x2600&compare=on` | 对比面板整行展开：运行 A / B 两个下拉、图例 `两次不一样 11 / 只有 A 有 8 / 只有 B 有 8 / 相同 77`、摘要「27 处不同」，逐步逐字段并排；差异值有底色（`3.6 s` ↔ `5.1 s`、`¥0.03` ↔ `¥0.0375`、`40 ms` ↔ `1.0 s`），两边都有 = 相同则不上色。 |
| `desktop-compare-en-1440x2600.png` | `?probe=1&frame=1440x2600&lang=en&compare=on` | 英文桌面 + 对比：`Compare mode`、`Status filter`、`Actions (newest first)`、`Step 1 / Status / Action type / Risk / Time taken / Cost / Tool used / Execution receipt / Confidence` 全英文；记录正文仍是中文（数据侧英文属 t4/t7）。 |
| `desktop-probe-report-1440x2600.png` | 同上（滚到底） | 1440 档探针报告可读行：<br>`view=desktop … dictKeys=239 dictSource=embedded`、<br>`zones needs_me=4 running=1 done=7 \| cards=5 demoBadges=5`（筛选生效中）、<br>`desk sidebar=5 filterOn=needs_me,running hidden=7 scrub=1/12 focus=none focusedCards=0`、<br>`compare open=true runs=2 steps=13 cells=104 diffs=27 kinds=changed,only_a,only_b,same`、<br>`box=1423 scrollWidth=1423 horizontalScroll=False`、`PROBE: PASS (no horizontal scroll)`、`rigError=none`。 |
| `phone-375x812-open-en-3-2.png` | `?probe=1&frame=375x812&open=1&filter=all` | 3.2 之后的手机回归：视图仍是 `mobile`（侧栏 / 时间轴 / 对比全部隐藏），悬浮球 = 「Close panel」`aria-expanded=true`，底部面板三区 4/1/7、12 张卡片、12 枚徽标、58% 进度，`box=373 scrollWidth=373`。 |

3.2 关键判定：

- **1440×900 无破版**：`box == scrollWidth`、`PROBE: PASS`、`rigError=none`（三档钉框 1440×900 / 820×1180 / 375×812 各测一次，见自检 35）。
- **时间轴拖动联动**：`?step=5` 与真实 `input` 事件都让进度条、缩略卡、刻度、卡片高亮四处同步；越界值钳到 0–11；一次拖动恰好重绘一次（自检 33）。
- **对比差异高亮 ≥3 类**：`kinds=changed,only_a,only_b,same`，104 个格子（13 行 × 8 字段）中 27 处标为差异，图例计数与模型一致（自检 34）。
- **移动端未被破坏**：375 档 `view=mobile`、悬浮球与底部面板照旧、12 张卡片、`cards=12 demoBadges=12`（自检 35 + 上表最后一行）。
- **中英无遗漏**：`dictKeys=239`、`missingKeys=0`，英文桌面下所有界面节点（筛选标签、时间轴标签、图例、字段名、摘要）零中文残留（自检 35 断言）。

## 4.1 干预闭环证据（`/intervene` 联调）

同一抓法（浏览器 lane，Microsoft Edge / CDP）。这一节证明的是 4.1 新增的那一半：
三个决定按钮真的能用、状态真的流转、API 不在时**降级成有标签的本地模拟**。

| 文件 | 打开的 URL | 看到什么 |
| --- | --- | --- |
| `desktop-intervene-offline-top-1440x900.png` | `file:///…/web/index.html?probe=1&view=desktop&frame=1440x900&filter=all&lang=zh&api=off` | 顶栏多出连接状态徽标 **「离线：本地模拟」**（`data-hook="api-mode"`，`data-mode=offline`）；骨架版本 `4.1-intervene`；三区 4/1/7、12 张卡片、58% 进度与 3.2 一致（回归未被干预层破坏）。 |
| `desktop-intervene-panel-offline-1440x900.png` | 同上（`filter=needs_me`，展开第一条 act-demo-0004，滚到面板） | 面板标题「需要你决定」+ 提示「这里的选择只记录决定，不会替你执行」；三枚按钮按决定着色：**同意这个方案**（绿）/ **改参数后同意**（蓝，下方列出「可以改的参数 max_move_count (integer) required = 188 个」）/ **拒绝**（红，标出「需要你写一句理由」「有风险的操作」）；状态行「等你拍板」；诚实提示「决定只会记录进审计；是否真的生效，取决于执行端是否接入」；**降级提示**（黄）「本地服务不可用（直接用 file:// 打开页面时浏览器禁止请求），已降级为本地模拟：决定记在本页，不会写入服务，也不会执行」+「想写入演示存储：运行 python -m agent_explainer.server 后刷新页面」+ 演示数据说明。 |

浏览器在线模式（同一个 tab 换 `http://127.0.0.1:8765/?probe=1&view=desktop&frame=1440x900&filter=needs_me&lang=zh`）：
无障碍树里顶栏节点读作 **「已连上本地服务」**，探针行从 `dictSource=embedded` 变成 **`dictSource=json`**
（页面真的从服务器回读了 `i18n/zh.json`）；`probe()` 报 `api.mode=live`。截图未单独存档，
判定点由下面的 HTTP 实连记录 + 自检 41/46/48 覆盖。

### 服务器实连记录（`python -m agent_explainer.server --port 8766 --no-reset`，回环 HTTP）

| 请求 | 结果 |
| --- | --- |
| `GET /health` | 200，`mode=local`，`auth.required=false` + 注明「4.3 接入鉴权；local 模式身份固定为 local-user」 |
| `GET /` | 200 `text/html`，含 `app.js`，`Cache-Control: no-store` |
| `POST /intervene` reject（带 reason + idempotency_key） | 200 → `status=rejected`、`requires_action=false`；`execution_receipt.state` 仍是 `unreported`、`effect_notice=intervention.effect.recorded_only`（决定 ≠ 生效） |
| 同一 `idempotency_key` 再提交 | 200 + `X-Intervene-Replay: true`，不新增审计（`/api/audit` chain.count 仍为 1） |
| 之后再 `approve` 同一条 | 409 `INTERVENE_ALREADY_DECIDED` |
| `GET /api/records` | 200，`meta.synthetic=true`，该条已是 `rejected`（存储与端点同源 → 刷新后界面看到服务器已有的决定） |
| `GET /../server.py` | 400（静态越界拒绝） |
| `POST /intervene` `decision:"fly"` | 400 `INTERVENE_DECISION_INVALID` + `request_id` |

### `intervene-golden.json` 的来历（浏览器 ↔ Python 同口径）

- 生成器就是产品代码：`agent_explainer.server.apply_decision`，用一次性内联命令跑的
  （`python -c` 读取 `samples/action_records.json` → 对 4 条记录施加 3 种决定 → 写出金样），
  没有第二份“参考实现”。
- 4 个用例：`approve-0004`、`modify-0005`、`reject-0008`、`approve-expired-0011`；
  合成三元组 `{name, decision, option_id, form_values, reason, decided_at, input_record, result_record}`，
  `decided_at` 固定为 `2026-11-10T08:00:00Z`，保证可复现。
- 自检 46 比较 13 个字段（`status / requires_action / ui.decision / option_id / recorded /
  decided_by_role / decided_at / reason / original_params / modified_params / modified_fields /
  receipt.state / receipt.effect_notice`）；排除 `human_readable`（服务器重新生成 L1/L2/L3 文案，
  浏览器只保留服务器返回的副本）、`user_intervention.request_id`、`audit_entry_hash`（会话相关）。
  `modified_fields` 排序后比较（顺序不影响语义）。

### 浏览器侧降级与在线的判定点（自检 41–48）

- 离线（`file://` 或 `?api=off`）：按钮仍可用 → 本地模拟写状态、面板挂「本地模拟」徽标 + 离线说明，
  **0 次网络请求**，`execution_receipt` 一字未改（自检 44）。
- 在线：恰好 1 次 `POST intervene`（相对路径 `intervene`，不带前导 `/`）→ 用服务器返回的记录替换本页记录，
  反馈区标 `mode=live` 并显示审计哈希/时间（自检 41）。
- 服务器拒绝（400/409）时：只在面板内联提示，**不改本地状态**（自检 45：非决定态在发请求之前就被拦下）。
- 在线刷新：`GET api/records` 一次，页面改用服务器已有的记录；已决定的那条只剩禁用的按钮（自检 48）。

> 无头 Edge 的限制见下一节；本会话用浏览器 lane 抓取，URL 原样可复现。


受限沙箱里 Chromium 的命名管道 IPC 被拒：`FATAL: mojo/public/cpp/platform/platform_channel.cc:187] Check failed: 拒绝访问 (0x5)`、
crashpad `OpenProcess: 拒绝访问 (0x5)`，`--headless=new --dump-dom` 输出 0 字节。本会话无法申请提权，
所以像素证据改由浏览器 lane 抓取；在无受限权限的终端里，直接套用表里的 URL 也能用无头 Edge 复现同样的报告。

## 4.10 移动端入口可达性证据（QA 5.1 D-4 · t23）

同一抓法（浏览器 lane，Microsoft Edge / CDP，`file://` 直接打开，无服务器、无构建）。
这一节证明的是：375 宽的手机也能走到**设置 / 审计 / 用户管理 / 导入 / 导出 / 语言切换**，
不可用的入口会写清原因，且打开的是桌面侧栏那块**同一**面板。

| 文件 | 打开的 URL | 看到什么 |
| --- | --- | --- |
| `phone-375x812-4.10-topbar.png` | `?probe=1&frame=375x812` | 顶栏末尾多出「更多」胶囊：无障碍树里是按钮 `打开更多入口：设置、审计、用户、导入、导出、语言`（`aria-controls=more-sheet`、`aria-expanded=false`）。悬浮球、三区、演示横幅、58% 进度与 3.2/4.9 一致（新入口没有挤破首屏）。 |
| `phone-375x812-4.10-entries-zh.png` | `?probe=1&frame=375x812&moremenu=1&api=off` | 入口列表（`role=dialog` + 「关闭更多入口」+ 列表 `aria-label=手机端入口列表：按当前权限启用或禁用`）：**设置**（可用，副标题「模型、语言与数据管理（离线时只读）」）、**审计记录 / 用户管理 / 导入数据 / 导出审计** 四条为不可用，逐条写原因：「页面处于离线模式：审计记录需要连接本地服务。」「只有管理员能管理用户：当前角色没有 users.manage 权限。」「离线预览（file:// 或 ?api=off）：不发请求，也不会写入任何数据。」「没有可连接的服务：导出需要连上本地服务后再试。」、**语言切换**（「切换到 English」+「当前界面语言：中文」）。 |
| `phone-375x812-4.10-entries-en.png` | `?probe=1&frame=375x812&moremenu=1&lang=en&api=off` | 同一列表切到英文：`Settings / Audit log / User management / Import data / Export audit / Language`，禁用原因同步英文（`Only an admin can manage users: this role has no users.manage permission.`、`There is no service to talk to: connect the local server and try again.`），按钮 `aria-label` 也整句英文。 |
| `phone-375x812-4.10-audit-drawer.png` | `?probe=1&frame=375x812&more=audit&api=off` | 审计入口打开的抽屉：`dialog "面板：审计记录"` + 「返回更多入口」+「关闭」，抽屉**钉在 375 盒子里**（`width: min(88vw,340px)`，不是铺满浏览器窗口），里面就是桌面侧栏那块审计面板：`审计记录 / 谁在什么时候做了什么决定；审计只能追加，不能修改。`、chip「哈希链完好」「共 0 条」「内存链（演示）」、筛选四枚、刷新 / 加载更多、追加提示。 |
| `phone-375x812-4.10-settings-screen.png` | `?probe=1&frame=375x812&more=settings&api=off` | 设置入口在手机上满屏打开 4.5 的设置中心：说明行 + 「关闭设置」（离线时写入按钮禁用）+ 离线只读说明「现在是离线/演示模式：没有服务端可写，设置页只读。」+「重新读取」。 |
| `phone-375x812-4.10-import-sheet.png` | `?probe=1&frame=375x812&api=off&open=1&more=import` | 导入入口打开的是 4.9 的导入块（在底部面板末尾，`sheetOpen=true`）：格式 / mode 下拉齐平、字节数与上限 65536/200、离线时输入与按钮禁用并写明「不发请求，也不会写入任何数据」。`open=1` 是既有手机启动参数（第 4362 行的手机首屏规则），入口点击路径由自检 83 覆盖。 |
| `phone-375x360-4.10-entries-probe.png` | `?probe=1&frame=375x360&moremenu=1&api=off` | 探针自报（框高收窄让报告行进视口）：<br>`view=mobile … dictKeys=720 dictSource=embedded`、`requests sent 0 - local simulations 0`、<br>`mobile entries=6/6 enabled=2 reason=4 menu=true drawer=off screen=off blocked=0`、<br>`box=358 scrollWidth=358 horizontalScroll=false`、**`PROBE: PASS (no horizontal scroll)`**、`rigError=none`。 |
| `desktop-1440x900-4.10-sidebar-unchanged.png` | `?probe=1&frame=1440x900&api=off` | 桌面回归：侧栏六个入口原样（`Action list / Timeline / Compare two runs / Audit log / Export audit / Open settings`），三栏 + 12 刻度 + 导入 / 导出 / 审计挂载点未变，顶栏**没有**「更多」按钮（`data-visible=false`，`moreMenu(true)` 在桌面返回 `false`）。 |

4.10 关键判定：

- **手机端可达**：6 条入口都在、键盘可达（`tabindex=0`、`type=button`）、每条有 `aria-label`
  （可用 = `打开{title}`，不可用 = `{title}：不可用。{reason}`）。
- **权限驱动、按身份变化**（自检 82 四种身份实测）：`file://` 离线 = 2 可用 / 4 不可用；
  `viewer`（有 `audit.read` + `audit.export`，无 `data.write` / `users.manage`）= 审计、导出可用，
  导入写「需要 data.write」、用户管理写「只有管理员…」；本地档六条全可用；
  有 `users.manage` 但没有会话 = 「先登录」。
- **不假装成功**：点不可用的条目 → 不开任何东西、列表不关、`probe().more.blocked +1`、
  底部一行 `more.feedback.blocked{reason}` 复读原因，**0 次网络请求**（探针 `requests sent 0`）。
- **复用 4.8 / 4.9 / 4.5 的产物**：抽屉里是同一块审计 / 导出面板（DOM 各 1 份），导入是同一个 4.9 块
  （导入编辑器全页恰好 2 个 = 手机 + 桌面），设置 / 用户复用既有面板；自检 83 逐个点名挂载点，
  且 `duplicateIds()` 为空（无重复 id）。
- **375 宽不破版**：探针实测 `box=358 == scrollWidth=358 → PROBE: PASS`；用户表用 `.users__scroll`
  自己横滚，页面不横滚；抽屉 / 列表在钉框档改 `position:absolute` + `height:var(--frame-h)`，
  动效全部 ≤200ms（自检 84 把这条写进 CSS 契约断言）。
- **桌面未被改动**：≥1024 侧栏六个入口 + 12 刻度原样，手机三层（列表 / 抽屉 / 手机屏幕）在桌面强制关闭。
- **键盘 / Esc**：`Esc` 依次关抽屉 → 列表 → 底部面板，焦点回到「更多」按钮（自检 84）。
- **语言切换同步**：列表内切换 zh↔en，六条标题 / 提示 / 原因 / `aria-label` 与演示数据语言一起变，
  列表保持打开，一次动作恰好一次重绘（自检 85）。
- **用户管理页需要服务 + 登录**：`file://` 下诚实禁用（原因见上），其"点开就是管理员面板"的路径由
  自检 83 用 admin 会话覆盖（`users-panel` 1 个 + `users-scroll` 1 个 + 2 行用户 + 关闭后复位）。
- 结构层对应断言 = 自检第 **81–85** 项（全套 85/85 全绿）；文案 = 字典 `more` 域 31 键
  （720 键 / 22 域），`python ../../tools/i18n_lint.py` 五道闸门全过。

## 4.12 语言切换 / 审计刷新 / 卡片行证据（QA 5.2 B-01 + H-01~H-04 · t25）

同一抓法（浏览器 lane，Microsoft Edge / CDP，`?probe=1&frame=1400x900` 钉框），**这一节是真 HTTP**：
页面由本地服务喂数据，不是 `file://` 演示。无头 Chromium 在本沙箱不可用（mojo 命名管道 EPERM，见上一节），
所以像素来自浏览器 lane；表里的命令与 URL 原样可复现。

### 起服务与灌数据（可复现）

```powershell
# 8731：SSE 正常；8732：--stream-limit 0（逼出降级轮询路径）
$d = Join-Path $env:TEMP 't25-smoke'    # 仓库 var/ 一个字没动
python -m agent_explainer.server --mode local --host 127.0.0.1 --port 8731 `
  --web agent_explainer/web --records-store "$d\records.jsonl" --audit-log "$d\audit.jsonl" --verbose
```

| 请求 | 结果 |
| --- | --- |
| `POST /api/reset` | 200 → 12 条演示种子（`needs_me=4`），审计只追加不清空（`chain.count` 只增） |
| `POST /api/import`（Langfuse trace，8 个 `observations[]`） | 200 `imported=8 created=8 total=20 format=langfuse durable=true`；单条缺 `observations[]` → 400（适配器要求 trace + observations） |
| `POST /intervene`（**不是** `/api/intervene`：后者 `ROUTE_NOT_FOUND`） | 200 `status=approved`、`user_intervention.decision=approve`、`decided_by_role=local`、`audit_entry_hash=sha256:…`；对 `running` 记录 → 400 `INTERVENE_STATE_INVALID`（可决定态只有 `pending_confirmation` / `expired`） |
| `GET /api/audit?limit=8` | 200，`chain.count` 随决定增长（实测 2 → 4 → 7 → 9），首条是导入说明、其余是 `act-demo-0004/0005/0008/0011` 的 `approve` |
| `GET /api/records` | 200，**始终 20 条**（这就是 B-01 的对照：界面切语言期间接口从未少过） |

> pwsh 复用连接会把前一个 4xx 的响应粘到下一个请求（`Unsupported method ('{}POST')`）；
> `-Headers @{Connection='close'}` 被 pwsh 拒绝，所以上表的写入类请求用
> `curl.exe -s -X POST … --data-binary "@文件"`（每次新连接）。

### 截图（浏览器 lane，Microsoft Edge，`http://127.0.0.1:8732/?probe=1&frame=…`）

| 文件 | 打开的 URL | 看到什么 |
| --- | --- | --- |
| `desktop-1400x900-4.12-boot-top.png` | `?probe=1&frame=1400x900` | 真服务首屏（zh）：顶栏「已连上本地服务」+「实时：降级轮询（每 4000 毫秒）」、演示横幅、`80%`、`演示数据 20`；左栏三区计数 `待确认4 / 进行中1 / 已完成15`、动作列表 `共 20 条`（含导入的 8 条「读取：list photos #1…#8」）。 |
| `desktop-1400x900-4.12-card-hint.png` | 同上（页内下滚到三区） | **H-04 主证据**：三张卡各自是「状态点 + 一行标题（按词折行）+ 右上角`展开详情`」，提示语「点这里看为什么、结果和花费」在**自己那一行**、整宽、灰色，不与`展开详情`重叠、不逐字竖排。探针尾部同框可见：`audit loaded=true rows=7 chips=3 store=1 pending=0 auto=1/0 reason=none`、`lang swaps=0 kept=0`、`box=1383 scrollWidth=1383 horizontalScroll=false`、**`PROBE: PASS (no horizontal scroll)`**。 |
| `desktop-1400x900-4.12-remote-decision.png` | 同上 | **H-03 主证据**：在页面外用 `POST /intervene` 决定 `act-demo-0004` 后，本页在 4 秒轮询内自己刷新那条 —— 卡片标题变「请求确认 DemoAlbum/**｜已同意」、从左栏「待确认」区移走（`待确认4` → `3`），`实时状态：poll … 游标 10 → 11`。 |
| `desktop-1400x900-4.12-lang-switch-kept.png` | `?probe=1&frame=1400x900`（切到 English 后重开，语言已持久化） | **B-01 主证据**：英文界面 + 同一条数据 —— `cards=20`、`Needs you3 / Running1 / Done16`、`act-demo-0004` 在时间轴与卡片上都是 `Approved`，审计面板首屏「哈希链完好 / 8 entries / Stored on disk (append-only file)」+ 8 行 +「已刷新：显示最近 8 条。」；记录句子仍是服务器给的中文原文，卡片诚实标「This record is written in zh while the interface is en」。 |
| `desktop-1400x760-4.12-narrow-card.png` | `?probe=1&frame=1400x760` | 同宽更矮时区域变窄（卡片 ~250px）：标题按词折三行，`Expand details` 在标题行右上，提示行在它下面一行，**仍无重叠 / 无裁剪**（自检 91 的 grid 契约在窄卡下同样成立）。 |

### 关键判定（逐条对着 QA 5.2 的复现步骤）

- **B-01（阻断）＝已修**：live 模式点顶栏「切换到English」→ 探针 `lang swaps=1 kept=20 refreshes=1 merged=20`，
  界面变英文、`cards=20`（导入的 8 条一条不少）、`act-demo-0004` 仍 `已同意`/`Approved`、
  切前展开的卡片仍展开、状态筛选三档原样、时间轴位置不变、审计面板不退回内存链。
  离线路径（`file://` / `?api=off`）由自检 87 覆盖：12 条本地化演示 + 8 条算子行、
  `carried=11`（8 条算子行 + 3 条已记录决定）、英文界面零中文、**恰好一次重绘**（4.7 的约束没被破坏）。
- **H-01＝已修**：首屏**不点任何按钮**就有真审计（7 / 8 行 + 三枚 chip +「已刷新：显示最近 N 条。」）；
  读之前页面不吹牛（无 `共 N 条`、无 `已落盘` chip，链 chip 虚线 `data-ok="pending"` +「哈希链：正在读取…」）。
  自检 88 同时钉住离线：0 次 `api/audit` 请求 + 诚实提示行。
- **H-02＝已修**：本页点「同意这个方案」→ 审计自己从 2 条变 3 条（`auto=1/1 reason=decision:act-demo-0005`），
  导出成功后再自动一次；「刷新」按钮仍是手动（自检 89：`auto` 只到 2）。
- **H-03＝已修（页面侧）**：`action.decision_recorded` 帧强制合并 + 卡片 `data-decided` 记号（状态点外圈 / 箭头转绿）。
  降级轮询实测 4 秒内刷新；SSE 实测在这台机器上「连接活着但收不到别的客户端的帧」，
  页面如实显示游标与降级状态，**未改后端**，已把这一条上报队长（服务端 / 环境侧）。
- **H-04＝已修**：卡片头 grid + 提示独占第二行；DOM 层 12 张卡 = 12 行提示（自检 91），
  1400×900 与 1400×760 两张真像素均无重叠 / 无逐字竖排；1024–1440 档区域列最小 320px。
- 结构层对应断言 = 自检第 **86–91** 项（全套 **91/91** 全绿）；新增文案 1 键
  （`audit.chip.loading`，字典 **721 键 / 22 域**），`python ../../tools/i18n_lint.py` 五道闸门全过。
