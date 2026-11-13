# TraceLens（行动透镜）· 5.1 用户测试问卷（中英双语）

| 项 | 内容 |
|---|---|
| 文档编号 | TL-QA-004-Q |
| 对应子任务 | 5.1（团队任务 t10）；配套 [05-test-plan.md](./05-test-plan.md) |
| 版本 | v1.0（16 题 Likert + 7 题开放 + SUS 10 题；中英两份等价表单） |
| 负责人 | 测试与质量官（qa-lead） |
| 适用阶段 | t11 模拟走查、t16 真实用户实测；也用于 5.1 的 5 人用户体验场 |
| 预计填写 | 5–8 分钟（不含任务操作） |
| 一句话读法 | 中文参与者用第 3 节；英文参与者用第 4 节；主持人用第 5 节计分、第 6 节防止引导。 |

> **使用纪律**：问卷不是考试，不评价参与者；填写时主持人**不解释题目、不提示答案**。参与者按第一反应作答；不确定的题可选“不适用（N/A）”，计分时剔除，不按 0 分算。所有题目只针对**今天用过的这个版本**，不针对“AI 一般好不好”。

---

## 0. 30 秒看懂

- **问什么**：可理解性（4 题）、信任度（3 题）、控制感（3 题）、干预易用性（3 题）、整体意愿（3 题），共 **16 题 Likert 5 级**；外加 **7 题开放**、**SUS 10 题**。
- **怎么算**：Likert 1–5，反向题按 `6 − 原分` 换算；维度取平均，并统计“≥4 分”的比例。SUS 按标准公式换算成 0–100。
- **对应 KPI**：K-05 可理解性、K-07 干预易用性、K-13 整体意愿（SUS）、K-14 信任度/控制感。
- **样本边界**：5 人（t11）或 1 人（t16）都只能做**定性/趋势**报告，不写“统计显著”。

---

## 1. 填表说明（请先读给参与者）

> 下面有 16 句话，请用 1–5 表示你有多同意。1＝非常不同意，2＝不同意，3＝一般，4＝同意，5＝非常同意。没有正确答案；你选“不同意”不会让我们不高兴。如果你今天没用过某个功能，可以选“不适用”。后面还有几道开放题和 10 道“好用程度”题，请按第一反应填。

**Likert 5 级标签（中 / 英）**

| 分值 | 中文 | English |
|---|---|---|
| 1 | 非常不同意 | Strongly disagree |
| 2 | 不同意 | Disagree |
| 3 | 一般 | Neither agree nor disagree |
| 4 | 同意 | Agree |
| 5 | 非常同意 | Strongly agree |
| N/A | 不适用（今天没用/没看到） | Not applicable (did not use/see today) |

---

## 2. 维度与题号地图（主持人用）

| 维度 | KPI | Likert 题号 | 开放题 | 其他证据 |
|---|---|---|---|---|
| 可理解性 | K-05 | U1–U4 | O1、O4 | T01 复述测试、3 秒扫读 |
| 信任度 | K-14 | T1–T3（T2 反向） | O2 | T06 回执、T07 审计 |
| 控制感 | K-14 | C1–C3 | O3 | T06 三决定 |
| 干预易用性 | K-07 | I1–I3 | O3、O5 | T06 计时 |
| 整体意愿 | K-13（SUS） | W1–W3 + SUS 10 | O6、O7 | SUS 分数 |

---

## 3. 中文版表单

### 3.1 参与者信息（主持人填，不写姓名）

| 字段 | 填写 |
|---|---|
| 参与者编号 | P____ |
| 画像 | □ A 小白　□ B 普通　□ C 谨慎 |
| 场次 / 日期 | 第 ____ 场 / 2026-____-____ |
| 设备 / 浏览器 / 窗口 | Windows ____ / ____ / ____×____（是否最大化：□是 □否） |
| 完成的模块 | □ T01 □ T02 □ T03 □ T04 □ T05 □ T06 □ T07 □ T08 □ T09 □ T10 |
| 是否录屏 / 录音 | □ 录屏 □ 录音 □ 都没有 |
| 填写用时 | ____ 分钟 |

### 3.2 Likert 16 题（请圈一个数字）

| # | 陈述 | 1 | 2 | 3 | 4 | 5 | N/A |
|---|---|---|---|---|---|---|---|
| **U1** | 我一眼就能看懂每条记录在说什么。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **U2** | 不用别人解释，我就知道现在需不需要我处理。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **U3** | 展开以后，我能看懂它为什么这么做。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **U4** | 出错的时候，它给的原因和下一步建议我能看懂。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **T1** | 我相信它显示的“已完成 / 等你确认”状态是真的。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **T2（反向）** | 我常常不确定它到底做完了没有。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **T3** | 我相信它没有做我没批准的事。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **C1** | 我知道自己能决定它下一步做什么。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **C2** | 我觉得决定权始终在我手里，不会被自动放行。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **C3** | 出问题时，我知道去哪里改参数或拒绝。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **I1** | 三个选项（同意 / 改参数 / 拒绝）我一眼能分清。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **I2** | 改参数的表单填起来不费劲。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **I3** | 提交后，我能立刻看出这次决定有没有被记录。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **W1** | 如果同事问我，我愿意推荐它。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **W2** | 我愿意下周继续用它处理真实任务。 | 1 | 2 | 3 | 4 | 5 | N/A |
| **W3** | 它比直接看日志更省时间。 | 1 | 2 | 3 | 4 | 5 | N/A |

### 3.3 开放题（请写原话，写不完整也没关系）

| # | 问题 | 回答 |
|---|---|---|
| **O1** | 今天哪一句话你最看不懂？请抄下原文，再用你自己的话说说你以为它是什么意思。 | |
| **O2** | 今天哪个时刻你最不放心？当时看到了什么？ | |
| **O3** | 如果“修改参数”按钮不存在，你会怎么做？ | |
| **O4** | 卡片上你最想多看到什么？最想少看到什么？ | |
| **O5** | 导出、团队、设置这三件事里，哪一步最麻烦？具体卡在哪？ | |
| **O6** | 如果只能改一件事，你会改什么？ | |
| **O7** | 你会怎么向同事介绍这个工具？（一句话即可） | |

### 3.4 SUS 好用程度 10 题（请圈一个数字）

| # | 陈述 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| **S1** | 我愿意经常使用这个系统。 | 1 | 2 | 3 | 4 | 5 |
| **S2（反向）** | 我觉得这个系统没必要这么复杂。 | 1 | 2 | 3 | 4 | 5 |
| **S3** | 我觉得这个系统容易用。 | 1 | 2 | 3 | 4 | 5 |
| **S4（反向）** | 我觉得需要技术人员帮助才能用这个系统。 | 1 | 2 | 3 | 4 | 5 |
| **S5** | 我觉得系统各个部分整合得很好。 | 1 | 2 | 3 | 4 | 5 |
| **S6（反向）** | 我觉得系统里有很多不一致的地方。 | 1 | 2 | 3 | 4 | 5 |
| **S7** | 我觉得大多数人能很快学会用它。 | 1 | 2 | 3 | 4 | 5 |
| **S8（反向）** | 我觉得用起来很别扭。 | 1 | 2 | 3 | 4 | 5 |
| **S9** | 我对这个系统有信心。 | 1 | 2 | 3 | 4 | 5 |
| **S10（反向）** | 我需要先学很多东西才能上手。 | 1 | 2 | 3 | 4 | 5 |

### 3.5 任务后即时观察（主持人填，不占参与者填写时间）

| 观察 | 记录 |
|---|---|
| **3 秒扫读**：首屏展示 3 秒后遮挡，问“现在需要你做什么？” | 回答：____________________；是否正确：□是 □否 |
| **复述测试**：展示 3 条 L1（各 10 秒），问“它做了什么 / 为什么 / 要不要你” | 正确条数：____ / 3；错在哪：____________________ |
| **T01 首屏时长** | T0 ____:____:____ → T1 ____:____:____ = ____ 分 ____ 秒 |
| **T06 同意 0004 用时** | ____ 秒；提示级别：H0 / H1 / H2 / H3 |
| **T06 修改 0005 用时** | ____ 秒；提示级别：H0 / H1 / H2 / H3；是否遇到范围错误：□是 □否 |
| **T06 拒绝 0008 用时** | ____ 秒；提示级别：H0 / H1 / H2 / H3；是否遇到必填理由：□是 □否 |
| **T07 导出耗时** | ____ 秒；格式：□ CSV □ PDF □ HTML；链校验：□通过 □失败 |
| **T10 Docker**（如执行） | `docker info`：□通过 □失败；health：□200/ok □其他：________ |

---

## 4. English form

### 4.1 Participant information (facilitator fills; no names)

| Field | Entry |
|---|---|
| Participant ID | P____ |
| Persona | ☐ A Novice　☐ B Regular　☐ C Cautious |
| Session / date | Session ____ / 2026-____-____ |
| Device / browser / window | Windows ____ / ____ / ____×____ (maximized: ☐ yes ☐ no) |
| Modules completed | ☐ T01 ☐ T02 ☐ T03 ☐ T04 ☐ T05 ☐ T06 ☐ T07 ☐ T08 ☐ T09 ☐ T10 |
| Recording | ☐ screen ☐ audio ☐ none |
| Time to complete | ____ minutes |

### 4.2 Likert scale (circle one number)

| # | Statement | 1 | 2 | 3 | 4 | 5 | N/A |
|---|---|---|---|---|---|---|---|
| **U1** | I can understand what each item is saying at a glance. | 1 | 2 | 3 | 4 | 5 | N/A |
| **U2** | Without help, I know whether it needs me right now. | 1 | 2 | 3 | 4 | 5 | N/A |
| **U3** | When I expand an item, I understand why it did that. | 1 | 2 | 3 | 4 | 5 | N/A |
| **U4** | When something fails, the reason and next step are understandable. | 1 | 2 | 3 | 4 | 5 | N/A |
| **T1** | I trust that “done / waiting for you” is accurate. | 1 | 2 | 3 | 4 | 5 | N/A |
| **T2 (reverse)** | I am often unsure whether it has finished. | 1 | 2 | 3 | 4 | 5 | N/A |
| **T3** | I trust that it did not do anything I did not approve. | 1 | 2 | 3 | 4 | 5 | N/A |
| **C1** | I know I can decide what it does next. | 1 | 2 | 3 | 4 | 5 | N/A |
| **C2** | I feel the decision is always mine; nothing is auto-approved. | 1 | 2 | 3 | 4 | 5 | N/A |
| **C3** | When something goes wrong, I know where to edit or reject. | 1 | 2 | 3 | 4 | 5 | N/A |
| **I1** | The three choices (approve / change / reject) are easy to tell apart. | 1 | 2 | 3 | 4 | 5 | N/A |
| **I2** | The parameter form is not hard to fill in. | 1 | 2 | 3 | 4 | 5 | N/A |
| **I3** | After submitting, I can immediately see whether my decision was recorded. | 1 | 2 | 3 | 4 | 5 | N/A |
| **W1** | I would recommend it to a colleague. | 1 | 2 | 3 | 4 | 5 | N/A |
| **W2** | I would use it next week for a real task. | 1 | 2 | 3 | 4 | 5 | N/A |
| **W3** | It saves me time compared with reading logs directly. | 1 | 2 | 3 | 4 | 5 | N/A |

### 4.3 Open questions (use your own words; incomplete is fine)

| # | Question | Answer |
|---|---|---|
| **O1** | Which sentence was hardest to understand today? Copy it, then say what you thought it meant. | |
| **O2** | When did you feel least confident today? What did you see? | |
| **O3** | If the “change parameters” button did not exist, what would you do? | |
| **O4** | What would you like to see more of on a card? Less of? | |
| **O5** | Of export, team, and settings, which step was hardest? Where exactly? | |
| **O6** | If you could change only one thing, what would it be? | |
| **O7** | How would you describe this tool to a colleague? (One sentence.) | |

### 4.4 SUS (circle one number)

| # | Statement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| **S1** | I think I would like to use this system frequently. | 1 | 2 | 3 | 4 | 5 |
| **S2 (reverse)** | I found the system unnecessarily complex. | 1 | 2 | 3 | 4 | 5 |
| **S3** | I thought the system was easy to use. | 1 | 2 | 3 | 4 | 5 |
| **S4 (reverse)** | I think I would need technical support to be able to use it. | 1 | 2 | 3 | 4 | 5 |
| **S5** | I found the various functions well integrated. | 1 | 2 | 3 | 4 | 5 |
| **S6 (reverse)** | I thought there was too much inconsistency. | 1 | 2 | 3 | 4 | 5 |
| **S7** | I would imagine most people learn to use it very quickly. | 1 | 2 | 3 | 4 | 5 |
| **S8 (reverse)** | I found the system very awkward to use. | 1 | 2 | 3 | 4 | 5 |
| **S9** | I felt very confident using the system. | 1 | 2 | 3 | 4 | 5 |
| **S10 (reverse)** | I needed to learn a lot before I could get going. | 1 | 2 | 3 | 4 | 5 |

### 4.5 Post-task observations (facilitator fills)

| Observation | Entry |
|---|---|
| **3-second scan**: show first screen 3 s, hide, ask “What needs you now?” | Answer: ____________________; Correct: ☐ yes ☐ no |
| **Recall**: show 3 L1 items for 10 s, ask “what / why / does it need you” | Correct: ____ / 3; what was wrong: ____________________ |
| **T01 time to first list** | ____ min ____ s |
| **T06 approve 0004** | ____ s; hint level: H0 / H1 / H2 / H3 |
| **T06 modify 0005** | ____ s; hint level: H0 / H1 / H2 / H3; range error: ☐ yes ☐ no |
| **T06 reject 0008** | ____ s; hint level: H0 / H1 / H2 / H3; reason required: ☐ yes ☐ no |
| **T07 export** | ____ s; format: ☐ CSV ☐ PDF ☐ HTML; chain: ☐ pass ☐ fail |

---

## 5. 计分与判定

### 5.1 Likert 计分

1. Likert 量表里的反向题（只有 T2）先换算：`得分 = 6 − 原分`；SUS 的 5 道反向题**不在这里换算**，按 5.2 的奇数/偶数公式直接算，避免重复反转。
2. “不适用/N/A”不计入分母；某维度有效题少于 2 题时，该维度记“证据不足”。
3. 维度分 = 该维度有效题的平均分；同时统计“≥4 分”的题数比例。
4. 报告写法：`可理解性均值 4.25；4 题中 4 题 ≥4；n=5`。不要只写“很好”。

| 维度 | 题号 | 通过线（PRD） | 判定 |
|---|---|---|---|
| 可理解性 | U1–U4 | ≥4 分的题 ≥80%；复述正确 ≥70% | K-05 |
| 信任度 | T1–T3（T2 反向） | ≥4 分比例 ≥70% | K-14 |
| 控制感 | C1–C3 | ≥4 分比例 ≥70% | K-14 |
| 干预易用性 | I1–I3 | ≥4 分比例 ≥70%；T06 三决定 ≤60 秒且成功 ≥90% | K-07 |
| 整体意愿 | W1–W3 | ≥4 分比例 ≥70% | K-13 辅助 |
| SUS | S1–S10 | ≥70（目标 78） | K-13 |

### 5.2 SUS 计分（标准公式）

对每位参与者：

1. 奇数题（S1、S3、S5、S7、S9）：贡献 `原分 − 1`；
2. 偶数题（S2、S4、S6、S8、S10）：贡献 `5 − 原分`；
3. 10 题贡献相加 × 2.5 = 0–100 分。

示例：若奇数题都是 4、偶数题都是 2，则贡献 = 5×(4−1) + 5×(5−2) = 15 + 15 = 30；SUS = 30 × 2.5 = **75**。

报告：每人一个分数 + 中位数 + 范围；**不要**把 5 人平均写成总体均值，也不要做显著性检验。

### 5.3 KPI 汇总表（t11 / t16 用）

| KPI | 数据来源 | 目标 | 本次结果 | 结论 |
|---|---|---|---|---|
| K-05 可理解性 | U1–U4 + 复述 | ≥4/5 比例 ≥80%；复述 ≥70% | | |
| K-06 扫读 | 3 秒扫读 | ≥90% 正确 | | |
| K-07 干预 | I1–I3 + T06 计时 | ≥90% 且 ≤60 秒 | | |
| K-09 可证明 | T07 | 链 100%、篡改必败、导出 ≤30 秒 | | |
| K-10 双语 | T05 | en chrome 0 中文残留、持久化 100% | | |
| K-13 SUS | S1–S10 | ≥70（目标 78） | | |
| K-14 信任/控制 | T1–T3、C1–C3 | ≥4/5 比例 ≥70% | | |

### 5.4 小样本报告纪律

- n=5：写“4/5 人”“中位数 4.0”“范围 3–5”，**不写**“80% 用户”。
- n=1（t16）：只写“发起人观察到…”，**不写**百分比、不写“显著”。
- 开放题引用原话时用编号（P1/P2…），不出现姓名/公司/客户。
- 负面反馈必须保留，不得只挑好话；报告要给出“最刺眼的三句原话”。

---

## 6. 主持与质量控制

| 规则 | 做法 |
|---|---|
| 顺序 | 先 Likert，再开放，最后 SUS；不要先讨论再打分（会被引导） |
| 语言 | 中文参与者用中文表；英文参与者用英文表；不要临场翻译题目 |
| 不解释 | 参与者问“这题什么意思”，回答“按你第一反应选” |
| 不引导 | 不说“这个应该选 4”；不替参与者改答案 |
| 独立 | 主持人不在旁边盯答案；可离开 2 米或背对 |
| 时间 | 5–8 分钟；超过 10 分钟记“题目过长”缺陷 |
| 可访问性 | 无法阅读小字时放大纸张/屏幕；仍无法完成记“未覆盖” |
| 缺答 | 允许 N/A；不允许留空后由主持人代填 |
| 保存 | 纸质原件拍照；电子表单导出 CSV；按 P 编号命名 |

---

## 7. 数据管理与隐私

- 表单只写 P 编号，不写姓名、公司、客户名。
- 开放题里若出现真实人名/邮箱/电话，录入时打码；无法打码就删除该句并标注“已脱敏”。
- 纸质原件锁在测试负责人处；电子文件放 `docs/05-evidence/`（t11 建目录，本任务不创建），30 天内归档/删除。
- SUS/Likert 原始分数保留，汇总表可复算；不得只留结论。
- 问卷结果不用于绩效评价；不向参与者所在公司提供个人数据。

---

## 8. 自检清单（对照 5.1 要求）

| 要求 | 是否满足 | 位置 |
|---|---|---|
| Likert 5 级量表 ≥10 题 | ✅ 16 题 | 3.2 / 4.2 |
| 覆盖可理解性 | ✅ U1–U4 | 3.2 / 4.2 |
| 覆盖信任度 | ✅ T1–T3（含 1 道反向） | 3.2 / 4.2 |
| 覆盖控制感 | ✅ C1–C3 | 3.2 / 4.2 |
| 覆盖干预易用性 | ✅ I1–I3 | 3.2 / 4.2 |
| 覆盖整体意愿 | ✅ W1–W3 + SUS | 3.2 / 3.4 |
| 开放式问题 ≥5 题 | ✅ 7 题 | 3.3 / 4.3 |
| SUS 简版 | ✅ 10 题标准 SUS（简化措辞） | 3.4 / 4.4 |
| 中英双语 | ✅ 中文版 3、英文版 4，逐题对应 | 第 3 / 4 节 |
| 可直接计分 | ✅ 反向题、SUS 公式、KPI 表 | 第 5 节 |
| 与测试计划任务对应 | ✅ 题号地图 + 任务观察表 | 第 2 / 3.5 节 |
| 小样本诚实 | ✅ n=5/n=1 报告纪律 | 5.4 |

**自检结论**：问卷覆盖 5 个核心体验维度（16 题 Likert）、7 题开放、标准 SUS 10 题，中英两份等价；计分规则与 K-05/K-07/K-13/K-14 一一对应，可直接用于 t11/t16。
