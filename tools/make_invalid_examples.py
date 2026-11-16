"""生成 action_record.invalid.json —— 负向夹具（必须被 schema 拒绝的用例集）。

设计原则：每条都从正向示例『最小改动』派生，只破坏一个约束，
并声明期望命中的关键字（expect_keywords）。这样能证明：
  - 约束确实生效（不是宽松 schema 永远通过）；
  - 失败原因可定位（前端能把错误翻成『哪里不对、怎么改』，K-03 可读错误要求）。

运行：python tools/make_invalid_examples.py
"""

from __future__ import annotations

import io
import json
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
POS = ROOT / "docs" / "schemas" / "examples" / "action_record.examples.json"
OUT = ROOT / "docs" / "schemas" / "examples" / "action_record.invalid.json"

pos = json.load(POS.open(encoding="utf-8"))
E = {rec["id"]: rec for rec in pos["records"]}
DEL = E["act-0012-del-duplicates"]      # 待确认高危删除
FAIL = E["act-0031-tool-run-tests"]     # 失败
READ = E["act-0004-read-contracts"]     # LLM 润色 + 低置信
CFG = E["act-0058-config-change"]       # 干预放行 + 执行回执
SEND = E["act-0071-send-announce"]      # 拒绝 + 运行时中止
ASK = E["act-0083-ask-missing-info"]    # 超时挂起


def base(rec: dict) -> dict:
    return deepcopy(rec)


cases: list[dict] = []


def case(cid: str, title: str, breaks: str, record: dict, expect: list[str],
         user_fix: str) -> None:
    cases.append({
        "id": cid, "title": title, "breaks_constraint": breaks,
        "expect_keywords": expect, "expected_fix": user_fix, "record": record,
    })


# --- 身份与隔离（6.4 权限矩阵 / 团队模式数据隔离）--------------------------------
m = base(DEL); m.pop("actor_id")
case("N01_missing_actor_id", "缺少 actor_id", "记录必须归属到一个用户身份（团队模式数据隔离）",
     m, ["required"], "补上 actor_id（local 模式填 local，团队模式填用户 ID）")

m = base(DEL); m.pop("workspace_id")
case("N02_missing_workspace_id", "缺少 workspace_id", "数据空间隔离字段不得缺失（v1 固定 default，先落库避免将来改表）",
     m, ["required"], "补上 workspace_id（v1 单空间填 default）")

m = base(DEL); m["parent_action_id"] = "bad id with spaces"
case("N03_bad_id_pattern", "parent_action_id 含空格", "ID 形态统一（pattern），防止渲染与查询歧义",
     m, ["pattern"], "去掉空格，改用 act-0003-plan-dedupe 形态")

# --- 干预选项（T5 锚点：三个选项 + 后果）----------------------------------------
m = base(DEL)
m["intervention_options"] = [o for o in m["intervention_options"] if o["kind"] != "reject"]
case("N04_only_two_options", "需要拍板但只有 2 个选项", "requires_action=true 必须给 ≥3 个选项且含 approve/modify/reject",
     m, ["minItems", "contains"], "补回 reject 选项（含后果文案）")

m = base(DEL)
for o in m["intervention_options"]:
    o.pop("consequence", None)
case("N05_options_without_consequence", "选项没有后果文案", "每个按钮必须写清『点了会怎样』（T5 安全感锚点）",
     m, ["required"], "为每个选项补 consequence 字段")

m = base(DEL)
for o in m["intervention_options"]:
    if o["kind"] == "reject":
        o["requires_reason"] = False
        o["dangerous"] = False
case("N06_reject_not_requiring_reason", "拒绝选项不要求填理由", "拒绝必填原因（责任边界，C 类可自证）",
     m, ["const"], "把 reject 的 requires_reason 与 dangerous 置为 true")

m = base(DEL)
for o in m["intervention_options"]:
    if o["kind"] == "modify":
        o.pop("editable_fields")
case("N07_modify_without_fields", "修改选项没有可编辑字段", "『改参数再放行』必须给出表单定义（US-04）",
     m, ["required", "minItems"], "为 modify 选项补 editable_fields")

m = base(DEL); m["requires_action_reason"] = None
case("N08_no_reason_for_human", "没有『为什么要你拍板』", "requires_action=true 必须解释为什么需要人（先讲原因再给按钮）",
     m, ["type", "minLength", "required"], "补上 requires_action_reason（非空字符串）")

# --- 状态机与 P4（决定 ≠ 执行）--------------------------------------------------
m = base(DEL); m["status"] = "running"
case("N09_pending_but_running", "需要拍板却标成 running", "需要决定的记录只能处于 pending_confirmation / expired",
     m, ["enum"], "把 status 改回 pending_confirmation")

m = base(DEL)
m["user_intervention"] = {"decision": "approve", "option_id": "opt-approve-del",
                          "decided_by": "local", "decided_by_role": "local",
                          "decided_at": "2026-11-09T09:13:00+08:00", "recorded": True}
case("N10_pending_with_decision", "待确认记录已带决定", "pending_confirmation 不得已有 user_intervention（状态与决定必须一致，K-08）",
     m, ["type"], "把 status 改成 approved，并把 decision 段补齐")

m = base(CFG); m["status"] = "approved"
case("N11_status_decision_mismatch", "status=approved 但决定是 modify", "决定段状态必须与 user_intervention.decision 一致",
     m, ["const"], "status 用 modified，或把 decision 改为 approve")

m = base(CFG); m["execution_receipt"] = {"state": "executed"}
case("N12_executed_without_receipt_time", "声称已执行但没有回执时间", "没有 reported_at/reported_by 的『已执行』= 假闭环，禁止",
     m, ["required"], "补上 reported_at 与 reported_by（由执行端 SDK 回执）")

m = base(CFG); m["execution_receipt"]["state"] = "executed_by_tracelens"
case("N13_invented_execution_state", "自造执行状态取值", "执行回执状态枚举封闭（unreported/executed/failed/refused_by_runtime/timeout）",
     m, ["enum"], "使用合法枚举值")

m = base(DEL); m["status"] = "expired"; m["execution_receipt"] = {
    "state": "executed", "reported_by": "demo-photo-bot", "reported_at": "2026-11-09T09:45:00+08:00"}
case("N14_expired_with_executed", "超时挂起却显示已执行", "expired 只能继续挂起，绝不能自动放行后标已执行（W-02 护栏）",
     m, ["enum", "const"], "把 execution_receipt.state 改为 timeout 或 unreported")

m = base(DEL); m["policy"]["auto_approve_forbidden"] = False
case("N15_auto_approve_allowed", "允许自动同意", "产品护栏常量：任何记录都不得自动同意（W-02）",
     m, ["const"], "把 auto_approve_forbidden 恢复为 true")

m = base(DEL); m["policy"]["timeout_behavior"] = "auto_approve"
case("N16_timeout_auto_approve", "超时行为=自动同意", "超时行为只能是 keep_pending（继续提醒）",
     m, ["const"], "把 timeout_behavior 改为 keep_pending")

m = base(DEL)
m["action_due_at"] = "2026-11-09T09:42:03"
case("N17_timestamp_without_timezone", "时间戳没有时区", "所有时间必须带时区偏移，否则排序与保留期计算会错（团队跨时区）",
     m, ["pattern", "format"], "写成 2026-11-09T09:42:03+08:00")

# --- 失败三要素（S1）-----------------------------------------------------------
m = base(FAIL); m.pop("error")
case("N18_failed_without_error", "失败却没有 error 对象", "status=failed 必须给出 code + 人话原因（不许只丢错误码）",
     m, ["required"], "补上 error.code 与 error.human_reason")

m = base(FAIL); m["suggested_next"] = []
case("N19_failed_without_suggestion", "失败但没有建议动作", "失败三要素之三：必须给出下一步可点的建议",
     m, ["minItems", "required"], "在 suggested_next 至少给 1 条建议")

m = base(FAIL); m["status"] = "succeeded"; m.pop("error"); m["result_summary"] = None
case("N20_succeeded_without_summary", "标成成功但结果为空", "status=succeeded 必须 result_summary 非空（结果导向）",
     m, ["minLength", "type", "required"], "填写客观结果摘要，或把 status 改回 failed")

# --- 事实与置信（P3 / 护栏 P3）--------------------------------------------------
m = base(READ); m["human_readable"]["may_contain_inference"] = False
case("N21_llm_without_inference_flag", "LLM 润色却没打推断标记", "generated_by=llm/hybrid 必须 may_contain_inference=true（界面显示 AI 生成）",
     m, ["const"], "把 may_contain_inference 置为 true")

m = base(READ); m["uncertainty"] = None
case("N22_low_confidence_no_uncertainty", "低置信却没说明不确定", "confidence ≤ 0.6 必须带 uncertainty（禁止把不确定讲成肯定）",
     m, ["type", "required"], "补上 uncertainty.reason")

m = base(READ); m["human_readable"].pop("source_facts_hash")
case("N23_missing_facts_hash", "缺少事实指纹", "P3 护栏：润色是否新增事实必须可被机器验证",
     m, ["required"], "由 tools/facts_hash.py 计算并写入 source_facts_hash")

m = base(DEL); m["human_readable"]["l1"] = "x" * 120
case("N24_l1_too_long", "L1 一句话超过长度上限", "L1 ≤40 字（A9 长度硬约束，防止卡片变成段落墙）",
     m, ["maxLength"], "压缩到 40 字以内")

# --- 高危与影响面（S4 / W-02）--------------------------------------------------
m = base(DEL); m["impact_scope"] = None
case("N25_critical_without_impact", "critical 风险但没有影响面", "high/critical 必须给出将影响什么（S4 预览的证据基础）",
     m, ["type", "required"], "补上 impact_scope.targets 与 reversible")

m = base(DEL); m["policy"]["requires_human_confirmation"] = False
case("N26_critical_without_human", "critical 风险标成不需要人确认", "高危必须人确认（护栏：绝不允许自动同意）",
     m, ["const"], "把 requires_human_confirmation 置为 true")

m = base(DEL); m["impact_scope"]["targets"] = []
case("N27_impact_targets_empty", "影响面 targets 为空", "高危必须至少列出 1 个受影响对象",
     m, ["minItems"], "列出受影响对象，或降低 risk_level（需产品确认）")

m = base(SEND); m.pop("impact_scope")
case("N28_external_send_no_impact", "对外发送没有影响面", "delete/send/payment/config_change/export 必须给影响面（不可逆副作用）",
     m, ["required", "type"], "补上 impact_scope（含 external_endpoint 与收件人数）")

# --- 演示数据诚实性（A1/A2）----------------------------------------------------
m = base(DEL); m["source"]["kind"] = "webhook"
case("N29_demo_from_webhook", "demo_data=true 但来源是 webhook", "内置演示数据必须来源可追（A2 徽标不得造假）",
     m, ["const"], "演示数据 source.kind=demo；真实接入记录 demo_data=false")

m = base(DEL); m["demo_data"] = False
case("N30_demo_flag_lost", "演示数据未标 demo_data", "synthetic + source.kind=demo 时 demo_data 必须为 true（防合成数据伪装真实采集）",
     m, ["const"], "把 demo_data 置为 true")

m = base(DEL); m["labels"] = ["demo", "demo", "delete"]
case("N31_duplicate_labels", "labels 出现重复项", "标签集合去重（筛选索引唯一性，重复标签会让筛选结果重复计数）",
     m, ["uniqueItems"], "去掉重复标签")

m = base(DEL); m.pop("source")
case("N39_missing_source", "缺少 source 来源元数据", "每条记录必须可追溯到入口（演示/粘贴/上传/webhook/SDK），K-03 归因依赖它",
     m, ["required"], "补上 source.kind 与 source.adapter")

# --- 结构与版本 ---------------------------------------------------------------
m = base(DEL); m["unexpected_field"] = "x"
case("N32_unknown_top_level_field", "出现 schema 未定义字段", "additionalProperties=false：拼错字段必须被发现，不得静默丢弃",
     m, ["additionalProperties"], "删除该字段，或走 schema 升级流程")

m = base(DEL); m["schema_version"] = "1.0"
case("N33_old_schema_version", "schema_version 不是 1.1", "写入前必须校验版本，禁止静默兼容旧格式",
     m, ["const"], "手工升级到 1.1（仓库没有自动迁移脚本）")

m = base(DEL); m["latency_ms"] = -5
case("N34_negative_latency", "耗时为负数", "latency_ms ≥ 0（K-04 性能指标的输入必须可信）",
     m, ["minimum"], "填真实耗时，未知则填 null")

m = base(DEL); m["cost"]["estimated"] = False; m["cost"].pop("currency")
case("N35_cost_missing_currency", "成本缺货币单位", "cost 对象若存在，currency 为 3 位大写代码",
     m, ["required"], "补上 currency（如 CNY）")

m = base(CFG); m["user_intervention"].pop("modified_params")
case("N36_modify_without_params", "改参数放行但没有新参数", "decision=modify 必须带非空 modified_params 与 original_params（逐字段核对）",
     m, ["required"], "补上 original_params 与 modified_params")

m = base(SEND); m["user_intervention"].pop("reason")
case("N37_reject_without_reason", "拒绝没写理由", "decision=reject 必须填写原因",
     m, ["required"], "补上 reason")

m = base(DEL); m["human_readable"].pop("language")
case("N38_rendering_no_language", "渲染结果没有语种", "双语要求：每条渲染必须标语种，切换语言才能命中对应文案",
     m, ["required"], "补上 language: zh 或 en")

doc = {
    "_meta": {
        "title": "ActionRecord 失败示例集（负向夹具，全部必须被拒绝）",
        "schema": "../action_record.schema.json",
        "schema_version": "1.1",
        "author_note": "全部由正向夹具最小改动派生（tools/make_invalid_examples.py），合成数据，不含真实路径/客户数据。",
        "expected": "本文件每条 record 必须被 schema 拒绝，且拒绝关键字命中 expect_keywords。",
        "count": len(cases),
    },
    "cases": cases,
}
with OUT.open("w", encoding="utf-8") as fh:
    json.dump(doc, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
print(f"生成 {OUT}：{len(cases)} 条负向用例")
