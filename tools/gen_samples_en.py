# -*- coding: utf-8 -*-
"""gen_samples_en —— 从中文演示集生成英文原生演示集 samples/action_records_en.json。

为什么不是机翻
--------------
英文界面出现中文演示数据是 QA 发布缺陷（docs/01 K-10：切到 en 后界面 0 条中文残留）。
本工具做的是**原生英文文案**：英文句子的语序、单位、标点由人写，不逐字对译；
结构、id、数值、时间戳、状态、成本、置信度逐字段照抄中文集，
所以两份夹具在任何语言下渲染出的是同一批事实，只有文案换语言。

口径（可执行、可复核）
----------------------
1. 只有含汉字的字符串需要翻译；查表 `EN_TABLE[中文原文]`。
   **查不到就失败**，并打印可直接粘贴的表格行（新增中文文案时强制补英文，不会出现漏译）。
2. 结构性替换：`_meta.language=en`、`_meta.copy_of=samples/action_records.json`、
   `source.source_uri=samples/action_records_en.json`、`human_readable.language=en`。
3. `human_readable.source_facts_hash` 用 `tools/facts_hash.py`（唯一算法来源）按英文事实重算。
4. 输出稳定：同一输入两次生成字节一致（`--check` 就是比这个）。
5. 自检：英文集零汉字；L1 ≤ 80 字符、L2 单步 ≤ 30 字符、L3 ≤ 160 字符
   （与 `tools/validate_schema.py` 的 A9 口径一致，英文集不在它的固定清单里，因此这里自查）。

运行：
    python tools/gen_samples_en.py            # 生成/覆盖 samples/action_records_en.json
    python tools/gen_samples_en.py --check    # 只校验现有文件是否与生成结果一致（CI 用）
退出码：0 = 成功/一致；1 = 有缺译、自检不通过或文件漂移
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "samples" / "action_records.json"
DST = ROOT / "samples" / "action_records_en.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts_hash  # noqa: E402

# 与 web/selfcheck.mjs 的 CJK 口径一致：汉字 + 中文标点 + 全角形式。
# 只查汉字会漏掉 `；（）` 这类全角符号——它们同样是"英文界面里的中文残留"。
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\uac00-\ud7af\uf900-\ufaff]")
L1_MAX_EN, L2_STEP_MAX, L3_MAX = 80, 30, 160

# 中文原文 → 英文原生文案。键是中文集里的原文（含标点），一字不差；
# 新增中文文案时工具会打印缺的行，补进这张表即可（漏译在 CI 就会红）。
EN_TABLE: dict[str, str] = {
    # ---------- 夹具自述 ----------
    "TraceLens 原生演示集（ActionRecord v1.1，合成示例，非真实采集）":
        "TraceLens native demo set (ActionRecord v1.1, synthetic examples, not real captures)",
    "全部 12 条由团队手工编写（synthetic=true / demo_data=true / source.kind=demo），不来自任何真实系统采集：没有真实人名、真实路径、真实金额、真实客户数据。依据 docs/01-product-and-users.md 假设 A1/A2、队长裁决②『不索要真实 trace』与 docs/02-data-model.md 8.4 夹具规格。":
        "All 12 records were written by hand by the team (synthetic=true / demo_data=true / "
        "source.kind=demo). Nothing was captured from a real system: no real names, real paths, "
        "real amounts or real customer data. Basis: docs/01-product-and-users.md assumptions "
        "A1/A2, the captain's ruling that we never ask users for a real trace, and the fixture "
        "spec in docs/02-data-model.md section 8.4.",
    "本文件是中文（language=zh）演示集；英文原生模板渲染（不是中文机翻）由 t6/t7 的 i18n 字典 + 翻译引擎（t4）产出，规范见 docs/03-architecture.md 第 6 节。":
        "This file is the English (language=en) demo set; the Chinese twin is "
        "samples/action_records.json. English copy is authored natively for the English UI, not "
        "machine translated. Spec: docs/03-architecture.md section 6.",
    "human_readable.source_facts_hash 由 tools/validate_schema.py --fix-facts 同源的 tools/facts_hash.py 计算，可用 tools/validate_schema.py 机器复算。":
        "human_readable.source_facts_hash is recomputed by tools/facts_hash.py (the same code "
        "behind tools/validate_schema.py --fix-facts); tools/i18n_lint.py verifies it record by "
        "record for this English set.",
    # ---------- 共用标签 ----------
    "相册助手（演示）": "Album Assistant (demo)",
    "整理演示相册并去重": "Tidy the demo album and remove duplicates",
    "尚未有人决定，执行端也未回执": "Nobody has decided; no execution receipt",
    "个": "files",
    "个月": "months",
    # ---------- 全角标点的技术串（含；（）：，一样算中文残留）----------
    "ActionRecord JSON Schema v1.1（Draft 2020-12）：docs/schemas/action_record.schema.json":
        "ActionRecord JSON Schema v1.1 (Draft 2020-12): docs/schemas/action_record.schema.json",
    "album=DemoAlbum；recursive=true": "album=DemoAlbum; recursive=true",
    "candidates=200；method=sha256": "candidates=200; method=sha256",
    "target=DemoAlbum/duplicates/*；count=12；move_to_trash=false":
        "target=DemoAlbum/duplicates/*; count=12; move_to_trash=false",
    "album=DemoAlbum；size=thumbnail": "album=DemoAlbum; size=thumbnail",
    "path=DemoAlbum/notes/limited.md；bytes=128": "path=DemoAlbum/notes/limited.md; bytes=128",
    "plugin=demo-album-plugin；grant=filesystem.write":
        "plugin=demo-album-plugin; grant=filesystem.write",
    # ---------- record 0 · plan ----------
    "把『整理演示相册并去重』拆成 4 个可核对的步骤":
        'Split "tidy the demo album and remove duplicates" into 4 checkable steps',
    "先出计划，你才能在中途改主意，而不是等它做完":
        "A plan first, so you can change your mind midway, not after it is done",
    "计划 4 步：扫描相册 → 内容哈希比对 → 删除重复项（需确认）→ 汇总报告":
        "4 steps: scan album -> compare content hashes -> delete duplicates (needs approval) -> report",
    "它先做了计划：分 4 步整理相册": "It started with a plan: tidy the album in 4 steps",
    "第 1 步：列出相册里的全部文件": "1 - list all album files",
    "第 2 步：按内容比对找出重复项": "2 - match files by content",
    "第 3 步：删除重复项前会先问你": "3 - ask you before deleting",
    "第 4 步：给出可核对的清单": "4 - output a checkable list",
    "plan v1 · 4 步 · 依据内容哈希（sha256）· 耗时 3620ms · 演示数据":
        "plan v1 - 4 steps - content hashes (sha256) - 3620ms - demo data",
    "计划步骤无需执行端回执": "Plan steps need no execution receipt",
    "展开计划逐条看，或直接看下一步": "Expand the plan step by step, or jump ahead",
    # ---------- record 1 · read ----------
    "列出演示相册里的全部文件": "List every file in the demo album",
    "要知道有多少重复，得先有完整清单": "You need the full list before you can count duplicates",
    "找到 200 个演示文件，共约 812 MB": "Found 200 demo files, about 812 MB in total",
    "它列出了相册：200 个演示文件": "It listed the album: 200 demo files",
    "读取的是演示相册目录，只读不改": "Demo album, read-only",
    "共 200 个文件，约 812 MB": "200 files, about 812 MB",
    "清单已留档，可逐条核对": "List archived, check it",
    "filesystem.scan_album · album=DemoAlbum · 200 个文件 · 740ms · 演示数据":
        "filesystem.scan_album - album=DemoAlbum - 200 files - 740ms - demo data",
    "只读操作无需执行端回执": "Read-only work needs no execution receipt",
    "展开看文件清单": "Expand the file list",
    # ---------- record 2 · tool_call ----------
    "按内容哈希比对，找出重复的演示照片": "Compare content hashes to find duplicate demo photos",
    "内容完全一致才算重复，按文件名判断会误删":
        "Only identical content counts as a duplicate; matching by name deletes the wrong files",
    "判定 12 个文件与已保留文件内容一致（sha256 相同）":
        "12 files match kept files by content (same sha256)",
    "它比对了内容：12 个疑似重复": "It compared content: 12 suspected duplicates",
    "用 sha256 比对文件内容": "sha256 content comparison",
    "12 个文件与保留项完全相同": "12 files match kept ones",
    "置信度不高，建议核对清单": "Low confidence, check the list",
    "image.hash_compare · 200 个候选 · 12 个命中 · 2500ms · LLM 参与文案（事实来自工具输出）":
        "image.hash_compare - 200 candidates - 12 hits - 2500ms - LLM wrote the copy "
        "(facts come from tool output)",
    "比对工具只给了命中数，没给逐对匹配明细":
        "The compare tool returned a hit count, not the per-pair matches",
    "展开 12 个文件的配对清单，确认没有同名不同图":
        "Open the 12 pairs and check for same name, different image",
    "只读比对，无需执行端回执": "Read-only comparison needs no receipt",
    "打开 12 组配对明细核对": "Open the 12 pair details",
    # ---------- record 3 · confirm ----------
    "请求你确认：是否按清单整理演示相册": "Ask you to approve tidying the demo album by the list",
    "整理会移动文件位置，属于需要你点头的操作": "Tidying moves files, so it needs your approval",
    "它请求确认：按清单整理相册": "It asks for confirmation: tidy the album by the list",
    "整理会移动文件位置，不改内容": "Moves files, not content",
    "移动后可以按清单逐项检查": "Check the list after moving",
    "不同意可以只导出清单": "Disagree? Export the list",
    "confirm · 1 个相册目录 · 可撤回（保留原路径映射）· 演示数据":
        "confirm - 1 album directory - reversible (path map kept) - demo data",
    "整理会移动文件位置，需要你先同意才动手":
        "Tidying moves file locations, so it waits for your approval",
    "同意整理": "Approve tidy-up",
    "按清单移动文件": "Move files per the list",
    "按清单整理相册目录，移动 188 个文件位置，内容不变":
        "Reorganise the album folders, moving 188 files; content unchanged",
    "移动会改变目录结构，可据清单还原": "Moving changes the folder layout; restore from the list",
    "改范围后同意": "Approve with a smaller scope",
    "只整理你允许的部分": "Only the part you allow",
    "只整理你设定的范围，其余保持原样并在清单标注已跳过":
        "Only your scope is tidied; the rest stays as is and is marked skipped in the list",
    "最多移动文件数": "Maximum files to move",
    "填 0 表示不移动任何文件": "0 means move no files at all",
    "先不整理": "Do not tidy yet",
    "只出清单，不动文件": "List only, no file changes",
    "跳过整理步骤，只导出清单，相册保持原样":
        "Skip tidying; export the list and leave the album untouched",
    "188 个演示文件的目录位置会变化，内容不变":
        "Folder locations of 188 demo files change; content unchanged",
    "先看清清单再决定": "Read the list before deciding",
    # ---------- record 4 · delete ----------
    "删除 12 个内容重复的演示文件": "Delete 12 demo files that duplicate kept content",
    "这 12 个文件的 sha256 与已保留文件完全一致，删除不改变内容集合":
        "These 12 files match kept files by sha256, so deleting them does not change the content set",
    "要删除 12 个重复文件，等你确认": "12 duplicate files are queued for deletion, awaiting you",
    "12 个文件与保留项内容完全相同": "12 files match kept ones",
    "删除后无法恢复（不进回收站）": "Delete is permanent (no bin)",
    "删除后相册保留 188 个文件": "188 files remain after delete",
    "filesystem.remove · 12 个目标 · 预计释放 46 MB · 依据 sha256 · 不可撤回 · 演示数据":
        "filesystem.remove - 12 targets - est. 46 MB freed - sha256 based - irreversible - demo data",
    "删除不可恢复，且一次涉及 12 个文件，需要你点头":
        "Deletion cannot be undone and touches 12 files at once, so it needs your approval",
    "同意删除": "Approve delete",
    "按原计划删除这 12 个文件": "Delete these 12 files as planned",
    "立即删除 12 个文件，释放约 46 MB，删除后无法恢复":
        "Delete 12 files now, freeing about 46 MB; this cannot be undone",
    "此操作不可撤销": "This action cannot be undone",
    "缩小范围后同意": "Approve with fewer files",
    "只删你允许的部分": "Only delete what you allow",
    "只删除你设定的数量，其余保留并在清单中标为已跳过":
        "Only your count is deleted; the rest is kept and marked skipped in the list",
    "最多删除文件数": "Maximum files to delete",
    "填 0 表示全部保留，等于拒绝本次删除": "0 keeps everything, i.e. refuses this delete",
    "改为移入回收站": "Move to recycle bin instead",
    "勾选后可从回收站恢复": "Tick to allow recovery from the bin",
    "拒绝删除": "Reject delete",
    "全部保留": "Keep everything",
    "跳过删除步骤，200 个演示文件全部保留，任务标记为部分完成":
        "Skip the delete step; all 200 demo files are kept and the task is marked partially done",
    "拒绝只记录决定；是否真的停下取决于执行端是否接入 SDK":
        "A rejection only records a decision; whether it truly stops depends on the runtime SDK",
    "12 个演示照片将被永久删除，不进入回收站":
        "12 demo photos would be deleted permanently, not sent to a recycle bin",
    "先看 12 组配对明细，确认没有同名不同图":
        "Check the 12 pairs for same name, different image",
    "想留底就选『移入回收站』": "Want a safety net? Choose move to recycle bin",
    # ---------- record 5 · error ----------
    "生成缩略图预览，方便人工核对保留项": "Generate a thumbnail preview so people can check what stays",
    "预览图能让你一眼看出删掉了什么": "A preview shows at a glance what was removed",
    "生成预览失败了：磁盘空间不够": "Preview failed: not enough disk space",
    "缩略图缓存写入失败": "Thumbnail cache write failed",
    "不影响已经完成的扫描与比对": "Scan and compare unaffected",
    "任务不会自动重试，等你决定": "No auto retry; your call",
    "thumbnail.generate_preview · ENOSPC · 900ms · 未自动重试 · 演示数据":
        "thumbnail.generate_preview - ENOSPC - 900ms - no auto retry - demo data",
    "失败由来源上报，执行端未额外回执": "Failure reported by the source; no extra receipt",
    "磁盘剩余空间不足，缩略图写不进去（演示场景，非真实机器状态）":
        "Disk space ran out, so thumbnails could not be written (demo scenario, not a real machine state)",
    "生成缩略图预览": "Generate thumbnail preview",
    "清理磁盘空间后让我重试这一步": "Free disk space, then retry this step",
    "跳过预览，直接看文字清单": "Skip the preview, read the text list",
    # ---------- record 6 · write with modified params ----------
    "写入去重说明笔记，记录本次判断依据": "Write a dedupe note recording why this judgement was made",
    "留下书面依据，事后可以核对它为什么这么判断":
        "A written basis lets you check the reasoning afterwards",
    "已按你修改后的参数写入笔记（128 字节）": "Note written with your modified parameters (128 bytes)",
    "按你改的参数写入了说明笔记": "Wrote the note using the parameters you changed",
    "原计划写 512 字节到 all.md": "Plan: 512 bytes to all.md",
    "你改成 128 字节到 limited.md": "You set 128B to limited.md",
    "执行端已回执：写入完成": "Receipt: write finished",
    "notes.write · 128 字节 · 决定=int modified · 回执=executed · request_id int-demo-0007 · 演示数据":
        "notes.write - 128 bytes - decision=int modified - receipt=executed - "
        "request_id int-demo-0007 - demo data",
    "只想留一页摘要，不要把整份清单写进相册":
        "One summary page is enough; do not write the whole list into the album",
    "执行端按修改后的参数执行完成（演示 SDK 回执）":
        "The runtime executed the modified parameters (demo SDK receipt)",
    "打开笔记核对判断依据": "Open the note to check the reasoning",
    # ---------- record 7 · email approval ----------
    "to=3 个演示联系人；attachments=1": "to=3 demo contacts; attachments=1",
    "把整理结果发给 3 个演示联系人": "Send the tidying result to 3 demo contacts",
    "报告需要发给别人看，所以要先让你确认内容":
        "The report is for other people, so you approve the content first",
    "它要发信给 3 个人，等你确认": "It wants to email 3 people, awaiting your approval",
    "收件人是 3 个演示联系人": "Recipients: 3 demo contacts",
    "附件含 12 个文件的清单": "Attachment: list of 12 files",
    "发出后无法收回，先看清内容": "Sent means sent, no recall",
    "message.send_email · 3 个收件人 · 1 个附件 · 含个人数据类别演示 · 不可撤回 · 演示数据":
        "message.send_email - 3 recipients - 1 attachment - personal-data demo - irreversible - demo data",
    "邮件会离开本机并包含个人信息，发出后收不回":
        "The email leaves this machine and contains personal information; sent means sent",
    "同意发送": "Approve send",
    "按现内容发给 3 个联系人": "Send as-is to the 3 contacts",
    "邮件与附件立即发出，本机无法撤回":
        "Email and attachment go out immediately; this machine cannot recall them",
    "包含个人信息，发出后不可收回": "Contains personal information; cannot be recalled once sent",
    "改收件人后同意": "Approve with new recipients",
    "换收件人或去掉个人信息": "Change recipients or strip personal data",
    "只发给你设定的收件人，脱敏后的附件版本会一并替换":
        "Only your recipients get it, and the attachment is replaced by the redacted version",
    "收件人列表": "Recipient list",
    "用分号分隔，留空等于不发送": "Separate with semicolons; empty means do not send",
    "导出时脱敏个人信息": "Redact personal data on export",
    "不要发送": "Do not send",
    "只在本地保留报告": "Keep the report on this machine only",
    "跳过发送，报告只存在本机，收件人收不到任何东西":
        "Skip sending; the report stays on this machine and recipients get nothing",
    "拒绝只记录决定；是否真的不发取决于执行端是否接入 SDK":
        "A rejection only records a decision; whether sending truly stops depends on the runtime SDK",
    "DemoInbox/演示联系人": "DemoInbox/demo-contacts",
    "3 个演示联系人（合成）": "3 demo contacts (synthetic)",
    "3 个演示联系人将收到含个人数据类别标注的邮件与 1 个附件":
        "3 demo contacts would receive an email flagged with personal-data categories plus 1 attachment",
    "先看邮件正文与附件，再决定是否脱敏":
        "Read the email body and attachment, then decide on redaction",
    # ---------- record 8 · ask ----------
    "问你：视频文件要不要一起参与去重": "Ask you whether video files join the dedupe",
    "视频哈希比对很慢，要不要做由你定，它不替你决定":
        "Hashing video is slow; you decide, it does not decide for you",
    "它在问你：视频要不要一起比对": "It is asking: include video in the comparison?",
    "比对视频很慢，大约 20 分钟": "Video hashing: ~20 min",
    "不比对就只处理图片": "Without it, images only",
    "回答前它会一直等着": "It waits until you answer",
    "ask · 视频去重范围 · 预计额外 20 分钟 · 问题不等于需要你拍板的卡片 · 演示数据":
        "ask - video dedupe scope - about 20 extra minutes - a question is not an approval card - demo data",
    "提问在等回答，无需执行端回执": "A pending question needs no execution receipt",
    "回答『要』或『不要』，它继续跑": "Answer yes or no and it continues",
    # ---------- record 9 · permission rejected ----------
    "为演示插件开启写文件权限": "Enable write permission for the demo plugin",
    "开启后它才能自动写清单文件，属于权限扩大":
        "Only then can it write the list file; this widens permissions",
    "你拒绝了这次权限扩大，权限保持原样": "You refused the widening; permissions unchanged",
    "你拒绝了：不新增写文件权限": "You refused: no new write permission",
    "它申请为插件开启写权限": "Plugin asked for write access",
    "你选了拒绝并写了理由": "You rejected with a reason",
    "执行端回执：已按拒绝中止": "Receipt: aborted on rejection",
    "plugin.set_permission · 决定=rejected · 回执=refused_by_runtime · request_id int-demo-0010 · 演示数据":
        "plugin.set_permission - decision=rejected - receipt=refused_by_runtime - "
        "request_id int-demo-0010 - demo data",
    "不希望插件获得写权限，清单用手动导出就够":
        "No write permission for the plugin; manual export is enough",
    "演示 SDK 读到 rejected 决定后中止了这一步（真实阻断需执行端接入）":
        "The demo SDK read the rejected decision and stopped this step "
        "(real blocking needs a runtime SDK)",
    "演示插件权限范围变化（最终未执行）": "Demo plugin permission scope change (ultimately not executed)",
    "保持手动导出，不改权限": "Keep manual export, change no permissions",
    # ---------- record 10 · timeout ----------
    "等你回答视频是否参与去重，超过 30 分钟": "Waiting over 30 minutes for your answer on video dedupe",
    "它不会替你决定，也不会因为超时就自己往下跑":
        "It will not decide for you, and a timeout does not make it run on",
    "超时了：还在等你回答": "Timed out: still waiting for your answer",
    "已等待超过 30 分钟": "Waiting over 30 minutes",
    "它没有替你决定，也没继续跑": "It did not decide or run on",
    "你随时回答，它接着做": "Answer any time and it resumes",
    "wait · 超时 keep_pending · 到期 2026-11-09 09:37 · 未自动执行任何操作 · 演示数据":
        "wait - timeout keep_pending - due 2026-11-09 09:37 - nothing executed automatically - demo data",
    "这一步要你回答，超时也不会自动执行，需要你拍板":
        "This step needs your answer; a timeout executes nothing, you decide",
    "视频一起比对": "Include video",
    "继续做，含视频": "Continue, video included",
    "继续跑视频哈希比对，预计额外 20 分钟，期间可以继续做别的":
        "Run video hashing, about 20 extra minutes; other work can continue meanwhile",
    "只比对最近一个月": "Last month only",
    "缩小范围继续": "Narrow the scope and continue",
    "只比对最近一个月的视频，预计额外 3 分钟": "Only videos from the last month, about 3 extra minutes",
    "回溯月数": "Months to look back",
    "视频不参与": "No video",
    "只处理图片": "Images only",
    "跳过视频比对，任务只处理图片并立即进入汇总":
        "Skip video comparison; images only, straight to the summary",
    "超时挂起，没有任何操作被执行": "Timed out and paused; no action was executed",
    "回答要不要比对视频，它接着跑": "Answer whether to compare video and it resumes",
    # ---------- record 11 · finish ----------
    "汇总本次演示任务的结果并交代后续": "Summarise this demo task and hand over what is left",
    "任务结束时要说清做了什么、没做什么、还有什么在等你":
        "At the end, state what was done, what was not, and what still waits for you",
    "完成扫描与比对；删除被拒绝；笔记已写入；视频比对仍在等你回答":
        "Scan and compare done; delete rejected; note written; video comparison awaits your answer",
    "做完了：删 0 个，1 件事还等你": "Finished: 0 deleted, 1 item waiting for you",
    "扫描 200 个文件，比对出 12 个重复": "200 scanned, 12 duplicates",
    "删除被你拒绝，文件全部保留": "Delete rejected, files kept",
    "视频比对还在等你回答": "Video comparison awaits you",
    "finish · 扫描 200 · 重复 12 · 删除 0（已拒绝）· 待办 1 · 演示数据":
        "finish - scanned 200 - 12 duplicates - 0 deleted (rejected) - 1 open item - demo data",
    "汇总是说明性动作，无需执行端回执": "A summary is explanatory; no execution receipt",
    "回到待办，回答视频比对要不要做": "Back to the open item: answer the video question",
}

META_EN: dict[str, str] = {
    "language": "en",
    "copy_of": "samples/action_records.json",
    "copy_generated_by": "tools/gen_samples_en.py",
    "source_language": "zh",
}


def build() -> tuple[dict[str, Any], list[str]]:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    missing: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                name = key
                if isinstance(name, str) and CJK.search(name):
                    if name not in EN_TABLE:
                        missing.append(f"{path}.<键> {json.dumps(name, ensure_ascii=False)}")
                    else:
                        name = EN_TABLE[name]
                out[name] = walk(value, f"{path}.{name}" if path else name)
            return out
        if isinstance(node, list):
            return [walk(value, f"{path}[{index}]") for index, value in enumerate(node)]
        if isinstance(node, str) and CJK.search(node):
            if node not in EN_TABLE:
                missing.append(f"{path}: {json.dumps(node, ensure_ascii=False)}")
                return node
            return EN_TABLE[node]
        return node

    translated = walk(doc, "")
    meta = translated.get("_meta") or {}
    meta.update(META_EN)
    meta["record_count"] = len(translated.get("records", []))
    for record in translated.get("records", []):
        record.setdefault("source", {})["source_uri"] = "samples/action_records_en.json"
        hr = record.setdefault("human_readable", {})
        hr["language"] = "en"
        hr["source_facts_hash"] = facts_hash.compute_facts_hash(record)
    return translated, missing


def self_check(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    def cjk_count(node: Any) -> int:
        if isinstance(node, dict):
            return sum(cjk_count(v) for v in node.values())
        if isinstance(node, list):
            return sum(cjk_count(v) for v in node)
        if isinstance(node, str):
            return len(CJK.findall(node))
        return 0

    total_cjk = cjk_count(doc)
    if total_cjk:
        problems.append(f"英文集里还有 {total_cjk} 个汉字（缺译）")
    for record in doc.get("records", []):
        rid = str(record.get("id"))
        hr = record.get("human_readable") or {}
        if len(str(hr.get("l1", ""))) > L1_MAX_EN:
            problems.append(f"{rid} 英文 L1 超长 {len(str(hr.get('l1')))}>{L1_MAX_EN}")
        for step in hr.get("l2") or []:
            if len(str(step)) > L2_STEP_MAX:
                problems.append(f"{rid} 英文 L2 单步超长 {len(str(step))}>{L2_STEP_MAX}：{step!r}")
        if len(str(hr.get("l3", ""))) > L3_MAX:
            problems.append(f"{rid} 英文 L3 超长 {len(str(hr.get('l3')))}>{L3_MAX}")
        if str(hr.get("language")) != "en":
            problems.append(f"{rid} 的 human_readable.language 不是 en")
        ok, declared, computed = facts_hash.verify(record)
        if not ok:
            problems.append(f"{rid} facts 指纹不一致：declared={declared} computed={computed}")
    return problems


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成英文原生演示集 samples/action_records_en.json")
    ap.add_argument("--check", action="store_true",
                    help="只校验现有文件是否与生成结果一致（CI 用，不写文件）")
    args = ap.parse_args(argv)

    if not SRC.exists():
        print(f"[FAIL] 找不到中文夹具 {SRC}")
        return 1
    doc, missing = build()
    if missing:
        print(f"[FAIL] {len(missing)} 处中文文案没有英文对照，请把下面这些行补进 EN_TABLE：")
        for item in missing:
            path, text = item.split(": ", 1)
            print(f'    {text}: "",  # {path}')
        return 1
    problems = self_check(doc)
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    payload = render(doc)
    if args.check:
        if not DST.exists():
            print(f"[FAIL] {DST.name} 不存在（跑 python tools/gen_samples_en.py 生成）")
            return 1
        if DST.read_text(encoding="utf-8") != payload:
            print(f"[FAIL] {DST.name} 与生成结果不一致（中文夹具改了，英文集没重新生成）")
            return 1
        print(f"[PASS] {DST.name} 与中文夹具同步（{len(doc.get('records', []))} 条，"
              f"零汉字，facts 指纹一致）")
        return 0
    DST.write_text(payload, encoding="utf-8")
    print(f"[PASS] 已生成 {DST.relative_to(ROOT)}：{len(doc.get('records', []))} 条记录，"
          f"文案 {len(EN_TABLE)} 条英文对照")
    return 0


if __name__ == "__main__":
    sys.exit(main())
