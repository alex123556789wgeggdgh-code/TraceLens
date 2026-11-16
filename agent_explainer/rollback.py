"""干预回滚（规格书功能一）—— 逆向操作注册表 + 回滚计划 + 本地回执 + 撤销日志。

为什么要单独一个"回滚"模块
--------------------------
"回滚"最容易做成一句骗人的按钮文案。本模块把规格书功能一拆成三个可分别验证的事实：

1. **机制覆盖**：每个有副作用的步骤，系统是否**登记了**逆向动作（:func:`coverage`
   给出比例；演示集目标 ≥ 90%）。没有登记的步骤绝不是"可以撤销"，而是"我们不知道
   怎么撤销"——界面必须照实说。
2. **影响面判断**：注册表有逆向动作 ≠ 这条记录可以撤销。来源在
   ``impact_scope.reversible`` 里明说"不可逆"时（删除不进回收站、邮件已发出），
   :func:`reversible` 必须返回 ``False``，界面照实显示"此操作不可撤销"（W-03）。
3. **回执边界**：本服务**不真的去调外部系统**（离线、无凭据）。执行只做两件事：
   写 ``<data>/features/rollback/journal.jsonl`` 的**回滚前快照**，以及返回一条
   ``simulated_local`` 的逆向回执。回滚 ActionRecord 的 ``execution_receipt.state``
   如实写 ``unreported``（外部系统没回执），绝不写"已恢复"。

为什么依赖顺序要参与回滚
------------------------
"撤销 A" 之前必须先撤销**依赖 A 的后续动作**，否则撤销顺序反了会留下悬空引用
（例如先恢复文件、再撤销一条引用该文件的笔记）。依赖关系有两条来源，本模块都认：
``parent_action_id``（动作树）与 ``context["depends_on"]``（显式依赖）。深度上限
``max_depth`` 防止一棵异常深的树把局部撤销变成全库回滚。

R1-2a 落地的两条口径（PM-VERDICT-R1-1.md §2）
-------------------------------------------
* **口径 α：回滚状态读时派生，原记录永不回写。** §0.4「历史记录不可原地修改」不可
  协商：已签名记录的 ``entry_hash`` 覆盖整条记录，写一个字段就复算失效。因此
  :meth:`RollbackService.execute` 返回的 ``records`` 是**逐字段等于原记录**的副本，
  绝不写 ``rollback_state`` / ``labels``；"这条已回滚" 由
  ``action_type='rollback'`` + ``rollback_ref``（回滚记录指回被撤销的目标）在读侧派生。
  回滚前状态的证据只有一份：撤销日志里的 ``snapshot``（目标记录整条前态）。
* **闭包治理**：依赖闭包只装"真的需要被撤销的后续动作"——
  ``action_type ∈ {intervention, rollback}`` 的补偿/决定记录**不进闭包**（它们本身
  没有外部副作用，卷进来等于"撤销一条干预决定"）；闭包成员的**可逆性参与
  blockers**：不可逆成员进 ``ROLLBACK_DEPENDENTS_IRREVERSIBLE`` 并要求确认，
  它们**绝不出现在执行范围里**；已回滚（撤销日志在案）的成员不再重入闭包。

R1-4 落地的三条口径（TASK-R1-4 + PM-VERDICT-R1-3 §3.1）
-----------------------------------------------------
* **G-13 收口 · 撤销日志链化**：``journal.jsonl`` 由 ``FeatureStore.put`` 改走
  :class:`feature_store.Ledger` 的链化追加（行带 ``seq`` / ``prev_hash`` /
  ``entry_hash``，与审计链同一口径）。**向后兼容读**：R1-4 之前落盘的
  ``{op, ts, item}`` 历史行**一个字节不改**、不迁移、不补字段——链复算按缺省容忍
  它们，并在历史行之后按写入端的算法重新锚定（:meth:`RollbackService.journal_chain_verify`）。
  审计链（``audit.verify()``）与签名域（``SIGNATURE_FIELDS``）一字未动。
* **TP-06 收口 · 闭包成员前态快照**：``execute()`` 为执行范围（``order`` = 目标 +
  参与闭包的成员）**逐条**落一份回滚前快照，journal 条目的 ``snapshots`` 集合覆盖
  全员；口径 α 不变——成员记录本身零改写，快照只是前态证据。
* **G-15 · 两阶段日志**：先在旁路 WAL（``journal.pending.jsonl``，同为链化账本）
  落一条 ``phase="pending"`` 的前态条目，**再**执行逆向动作，成功后把终态条目
  （``phase="succeeded"``）追加进 ``journal.jsonl``，失败则追加 ``phase="failed"``
  条目并原样抛出错误——任何一条路径都不留"无记录的副作用"，pending 与终态行用
  ``journal_id`` / ``pending_id`` 双向咬合。``failed`` 条目**不参与**幂等屏障
  （失败的逆向动作没造成副作用，目标仍可再回滚），也不参与读侧"已回滚"派生。
* **G-14 · 回执契约**：注入执行器返回的 ``receipt_state`` 必须在
  :data:`RECEIPT_STATES` 白名单里（越界 = ``ROLLBACK_INVERSE_FAILED``，绝不落终态），
  且 ``note`` 与状态一致：``executed_*`` 的真实执行**不得**再带「本地模拟」文案，
  ``simulated_local`` 必须说清是模拟。回滚记录的 ``execution_receipt.state``
  恒为 ``unreported``（外部系统没有回执），文案随 ``receipt_state`` 如实变化。

R1-5 落地的三条口径（TASK-R1-5 + PM-VERDICT-R1-4 §2 第 1/4/6 条）
---------------------------------------------------------------
* **G-19 收口 · 注册表补登有两条方向**：
  * **补登记（可逆）**：LLM 生成类动作（``deepseek-chat`` / ``text-embedding-demo``）
    映射 ``undo_write`` —— 语义是**记录级补偿**：追加一条回滚记录、把该步在系统里的
    事实标成已回滚；**已花掉的 token 与已经生成并外发的内容不可撤回**（在
    :func:`_seed_registry` 的注释里如实写明，不写进文案假装能撤销）。
  * **补登记（显式不可逆）**：``payment.transfer`` / ``contract.sign`` /
    ``refund.payment`` 登记成 ``reversible=False`` + **人话阻断文案**
    （:func:`register_inverse` 的 ``irreversible_reason``）。这类动作**有**外部副作用、
    本系统**没有**单方面撤销通道 —— 让它"落空返回 ``None``"和让它"有逆向动作"
    都是说谎：前者让界面显示"没登记所以不能撤销"，后者直接假装可逆。
* **G-18 口径 · 兜底表把"不可逆"放在可逆兜底之前**：资金/合同类工具名先进
  :data:`_IRREVERSIBLE_FALLBACKS`（保守方向：宁可说"撤销不掉"，绝不假装有回滚路径），
  再进 :data:`_GENERIC_FALLBACKS`。首匹配即终局的语义误判仍在（G-18 原文），
  本轮只补 needle 的**精确动词式**写法（``book_`` / ``book.`` 而不是裸 ``book``），
  避免把 ``notebook.write`` / ``bookmark.create`` 抢成 ``cancel_booking``。
* **C-1 收口 · 覆盖率必须自带明细**：:meth:`RollbackService.coverage` 在既有
  ``{total, reversible, ratio}``（**语义与数值一字不变**）之上纯增量补
  ``uncovered``（逐条 ``{id, tool, reason}``）与 ``buckets``（分桶 +
  **「有外部副作用子集」口径**）。A1 的主口径 = 副作用子集覆盖率，
  副口径 = 演示集回归护栏；明细不进 schema、不进 ``docs/api_contract.json``。

错误纪律
--------
所有对外抛出的错误都带 ``docs/07`` §7 的 ``rollback.*`` 错误码，并可在 ``detail``
里带上完整计划——前端据此展示"为什么不能回滚"，而不是一句 500。撤销日志**读不动**
时同样必须落在 ``rollback.*`` 码族里（``ROLLBACK_JOURNAL_UNREADABLE``），
不得把 ``FEATURE_STORE_*`` 直接抛给服务层。
"""

from __future__ import annotations

import contextlib
import copy
import pathlib
from collections.abc import Callable, Mapping, Iterator, Sequence
from types import MappingProxyType
from typing import Any

from .feature_store import (
    FeaturePaths,
    GENESIS_HASH,
    Ledger,
    hash_object,
    new_id,
    open_ledger,
    open_store,
    utc_stamp,
)

__all__ = [
    "ROLLBACK_VERSION",
    "ROLLBACK_DIR",
    "JOURNAL_FILE_NAME",
    "JOURNAL_PENDING_FILE_NAME",
    "DEFAULT_MAX_DEPTH",
    "NO_EFFECT_ACTIONS",
    "NOT_EXECUTED_STATUSES",
    "REQUIRES_CONFIRMATION_RISKS",
    "INHERITED_RISK_LEVELS",
    "CLOSURE_EXCLUDED_ACTION_TYPES",
    "RECEIPT_STATES",
    "SIMULATED_RECEIPT_STATE",
    "SANDBOX_RECEIPT_STATE",
    "REGISTRY_RECEIPT_STATE",
    "FAILED_RECEIPT_STATE",
    "SANDBOX_PILOT_NOTE",
    "REGISTRY_EXECUTED_NOTE",
    "SIMULATION_NOTE_MARKERS",
    "PHASE_PENDING",
    "PHASE_SUCCEEDED",
    "PHASE_FAILED",
    "TERMINAL_PHASES",
    "JOURNAL_PHASE_EMPTY",
    "CODE_JOURNAL_UNREADABLE",
    "CODE_INVERSE_FAILED",
    "journal_phase",
    "journal_settled_success",
    "RollbackError",
    "register_inverse",
    "unregister_inverse",
    "inverse_of",
    "inverse_entry_of",
    "registered_tools",
    "COVERAGE_BUCKETS",
    "COVERAGE_BUCKET_LABELS",
    "NATURAL_COVERAGE_BUCKETS",
    "COVERED_COVERAGE_BUCKETS",
    "IRREVERSIBLE_FINANCIAL_OP",
    "IRREVERSIBLE_CONTRACT_OP",
    "IRREVERSIBLE_FINANCIAL_REASON",
    "IRREVERSIBLE_CONTRACT_REASON",
    "IRREVERSIBLE_REFUND_REASON",
    "reversible",
    "plan",
    "RollbackService",
]

#: 模块契约版本（写进回滚记录的 ``source.format_version``）
ROLLBACK_VERSION = "1.0"
#: 相对 ``FeaturePaths.root`` 的子目录名（只用于自检/文档，路径由 FeaturePaths 给）
ROLLBACK_DIR = "rollback"
#: 默认依赖递归深度
DEFAULT_MAX_DEPTH = 3
#: 这些动作**本身不产生外部副作用**：没有可撤销的东西，按"天然覆盖"处理
NO_EFFECT_ACTIONS = frozenset({"plan", "read", "confirm", "ask", "wait", "finish", "intervention"})
#: 这些状态说明步骤尚未真正执行：撤销路径 = 取消，而不是"恢复"
NOT_EXECUTED_STATUSES = frozenset({"pending_confirmation", "expired", "rejected", "skipped"})
#: 高危/严重风险的回滚必须再确认一次（产品 W-02 同一口径）
REQUIRES_CONFIRMATION_RISKS = frozenset({"high", "critical"})
#: ``risk_level`` 的合法值闭合集（schema ``docs/schemas/action_record.schema.json``，
#: 与 ``secretariat.RISK_POLICY`` 键同族）。R1-3 / D-risk：补偿记录**继承目标记录的
#: 风险**——"回滚一律 medium"等于让高危补偿绕过四眼闸门（§11.8）。继承值越集/缺失
#: 时回落 ``"medium"``（与旧行为相同）：脏继承值不能直接送进 ``sign_record`` 的
#: 风险策略校验（签名前会炸 ``SECRETARIAT_FIELD_INVALID``，记录落盘时没签名）。
INHERITED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
#: 缺省回执说明（界面必须按这个字显示，不许写"已恢复"）
SIMULATED_NOTE = "本地模拟执行；外部系统未实际调用（离线降级）"

# --------------------------------------------------------------------------- #
# R1-4 · 回执契约（G-14）
# --------------------------------------------------------------------------- #

#: ``receipt_state`` 的枚举白名单（注入执行器的回执越界即拒）：
#:
#: * ``simulated_local``：缺省路径，没有执行器，只写日志 + 模拟回执；
#: * ``executed_sandbox``：**沙箱试点**——真实执行器写进了显式白名单的沙箱根
#:   （R1-4 的 ``restore_file`` 试点），是真实 I/O，但不是外部生产系统；
#: * ``executed_registry``：注册表级真实执行器（外部系统凭据由调用方自备，
#:   本产品默认不注册）；
#: * ``failed``：执行器自报失败——绝不落终态成功，走 :data:`CODE_INVERSE_FAILED`。
#:
#: 三者都**不改变**回滚记录的 ``execution_receipt.state = "unreported"``：
#: 外部系统从未回执（§11.6「决定已记录 ≠ 已执行」）。
RECEIPT_STATES = frozenset(
    {"simulated_local", "executed_sandbox", "executed_registry", "failed"})
SIMULATED_RECEIPT_STATE = "simulated_local"
SANDBOX_RECEIPT_STATE = "executed_sandbox"
REGISTRY_RECEIPT_STATE = "executed_registry"
FAILED_RECEIPT_STATE = "failed"
#: 真实执行回执绝不允许再带的"本地模拟"字样（G-14 一致性校验：真实执行不得伪装成模拟，
#: 反向的伪装——把沙箱试点写成"外部系统已撤销"——由文案纪律与 §10 禁令一起拦）。
SIMULATION_NOTE_MARKERS: tuple[str, ...] = (
    "本地模拟", "离线降级", "外部系统未实际调用", "未调用外部系统",
    "simulated", "simulation", "not called", "no external",
)
#: 沙箱试点的缺省回执文案（UI 必须照实显示「沙箱试点」，不得说成外部系统已撤销）
SANDBOX_PILOT_NOTE = ("沙箱试点：逆向动作已在显式白名单的沙箱根内真实写入"
                      "（见回执 detail.sandbox_root）；外部生产系统未触碰")
#: 注册表级真实执行的缺省文案（如实说清执行器在跑，不宣称外部系统已撤销）
REGISTRY_EXECUTED_NOTE = "注册表级执行器已真实执行逆向动作；外部回执未上报"

#: 撤销日志文件名（链化账本，R1-4 / G-13）
JOURNAL_FILE_NAME = "journal.jsonl"
#: 两阶段日志的旁路 WAL 文件名（先落 pending 前态，再落终态；R1-4 / G-15）
JOURNAL_PENDING_FILE_NAME = "journal.pending.jsonl"
#: 两阶段日志的相位（``phase`` 键）。历史行没有这个键 → 读侧缺省按
#: :data:`PHASE_SUCCEEDED` 容忍（R1-4 之前落盘的条目都是已落终态的成功条目）。
PHASE_PENDING = "pending"
PHASE_SUCCEEDED = "succeeded"
PHASE_FAILED = "failed"
TERMINAL_PHASES = frozenset({PHASE_SUCCEEDED, PHASE_FAILED})
#: 逐相位计数的**空槽模板**（服务层 ``meta.rollback_view.journal.phases`` 用）。
#: 不可变：调用方 ``dict(...)`` 复制后再填数，绝不在热路径上改模块级字典。
JOURNAL_PHASE_EMPTY: Mapping[str, int] = MappingProxyType(
    {PHASE_SUCCEEDED: 0, PHASE_FAILED: 0, PHASE_PENDING: 0})

#: 闭包排除的动作类型（PM-VERDICT-R1-1 裁决 **D-closure**）：干预决定与回滚补偿记录
#: 本身没有外部副作用（``intervention`` 已在 :data:`NO_EFFECT_ACTIONS` 里），把它们卷进
#: "撤销闭包"等于宣称要撤销一条人的决定，而且会诱发对已签名记录的原地改写（§0.4）。
#: 它们既不进 ``dependents``/``order``，也永不进执行范围 ``affected``。
CLOSURE_EXCLUDED_ACTION_TYPES = frozenset({"intervention", "rollback"})
#: 新增 blocker 码：**闭包成员撤销不掉**（裁决 D-irrev）。HTTP 映射由 R1-2b 登记。
BLOCKER_DEPENDENTS_IRREVERSIBLE = "ROLLBACK_DEPENDENTS_IRREVERSIBLE"
#: 新增错误码：撤销日志读不动（G-16）。它必须留在 ``rollback.*`` 码族里。
CODE_JOURNAL_UNREADABLE = "ROLLBACK_JOURNAL_UNREADABLE"
#: 逆向执行失败（含两阶段写日志失败、回执越界）共用的码族（G-15 / G-14）：
#: 服务端已有 500 映射（``server.EXPANSION_ERROR_TABLE``），不新增对外码名。
CODE_INVERSE_FAILED = "ROLLBACK_INVERSE_FAILED"
#: 计划层的**非阻断提示**码：不拦执行，但必须显示，绝不静默（§1.2-4）。
NOTICE_CYCLE_DETECTED = "ROLLBACK_CYCLE_DETECTED"
NOTICE_DEPENDENTS_TRUNCATED = "ROLLBACK_DEPENDENTS_TRUNCATED"
NOTICE_MEMBERS_ALREADY_ROLLED_BACK = "ROLLBACK_MEMBERS_ALREADY_ROLLED_BACK"
NOTICE_MEMBERS_EXCLUDED = "ROLLBACK_MEMBERS_EXCLUDED_FROM_CLOSURE"
#: 幂等屏障在 ``plan``/``execute`` 两侧共用的 blocker 码（G-6：同一口径）。
BLOCKER_ALREADY_ROLLED_BACK = "ROLLBACK_ALREADY_ROLLED_BACK"

#: 允许来源标注的影响面"不可逆"。
_IRREVERSIBLE = object()

# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #


class RollbackError(Exception):
    """回滚域错误：机器码 + 人话原因 + 取证细节（与 ``FeatureStoreError`` 同形）。

    前端按 ``code`` 找 i18n 文案（``rollback.error.<code 小写>``）；``detail`` 里
    可以带完整计划，让"为什么不能回滚"变成可展示的数据而不是一句错误 toast。
    """

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 detail: Mapping[str, Any] | None = None, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = str(code)
        self.reason = str(reason)
        self.field = field
        self.detail: dict[str, Any] = dict(detail or {})
        self.hint = hint

    def render(self) -> str:
        tail = f"（{self.field}）" if self.field else ""
        return f"{self.code}: {self.reason}{tail}"

    def as_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "reason": self.reason}
        if self.field:
            payload["field"] = self.field
        if self.detail:
            payload["detail"] = copy.deepcopy(self.detail)
        if self.hint:
            payload["hint"] = self.hint
        return payload


# --------------------------------------------------------------------------- #
# 逆向操作注册表
# --------------------------------------------------------------------------- #
#
# 表项 = {"op": 逆向动作 id, "describe_key": i18n 文案键}（R1-5 起可再多两个键：
# ``reversible`` = 注册表判定这条动作**有没有**单方面撤销通道（缺省 ``True``）、
# ``irreversible_reason`` = 判不可逆时给界面看的**人话阻断文案**）。
# apply（可选）= 调用方注入的真实逆向执行器；缺省 = 本地模拟（只写日志 + 回执）。
# 注册表是进程内全局表：它描述的是"产品知道怎么撤销哪些工具"，不随某次请求变化。

_INVERSE_REGISTRY: dict[str, dict[str, Any]] = {}
_INVERSE_APPLIERS: dict[str, Callable[..., Any]] = {}

# --------------------------------------------------------------------------- #
# R1-5 · 显式不可逆（G-18 / G-19 / TP-04）
# --------------------------------------------------------------------------- #
#
# 规格书点名的两个高危动作（转账、签合同）与语义同类的退款，**必须**是"登记在案且
# 判不可逆"，而不是"注册表里没有"：
#
# * 让它落空（``inverse_of`` 返回 ``None``）→ 界面的话术变成"我们不知道怎么撤销"，
#   听起来像名单没登记的工程缺口，补一个兜底 needle 就能"修好"——这是把产品纪律
#   伪装成名单缺失，下一轮补登记的人有权限"修"掉它（R1-1 G-19 实测就是这么落空的）。
# * 让它有逆向 op → 直接宣称可逆，界面就能显示"回滚这一步"，点了必失败（G-21）。
#
# 登记成 ``reversible=False`` + 阻断文案之后：``reversible()`` 返回 ``False`` 且
# ``reason`` 是给人看的完整原因；``plan()`` 出 ``ROLLBACK_NOT_REVERSIBLE`` blocker
# （``reason`` 原样入 blockers，前端照展示）；``execute()`` 拒绝执行；闭包里它是
# 撤销不掉的成员（``ROLLBACK_DEPENDENTS_IRREVERSIBLE``）；``coverage()`` 把它算进
# **未覆盖**（副作用子集的分母里有它，不能靠兜底表凑指标）。

#: 资金类（转账 / 汇款 / 退款 / 扣款…）的"逆向动作"命名口径：本系统给不出撤销，
#: 给得出的是**升级流程**——所以 op 叫 ``escalate_*``，不叫 ``undo_*`` / ``refund_*``。
IRREVERSIBLE_FINANCIAL_OP = "escalate_financial_reversal"
#: 合同签署类的升级流程（解约 / 作废 / 存证声明），同样不是"撤销签署"。
IRREVERSIBLE_CONTRACT_OP = "escalate_contract_rescission"
#: 阻断文案（界面唯一依据）：说清"为什么撤销不掉"与"该走哪条人工通道"。
#: 措辞纪律：不写"已撤销/已恢复"（docs/07 §10），不写"本地模拟"（G-14 一致性闸门），
#: 也不写"永远无法撤销"这种把外部流程说死的绝对话术。
IRREVERSIBLE_FINANCIAL_REASON = (
    "资金转移不可逆：钱已离开本系统管辖的账户边界，本系统没有单方面撤销转账的通道；"
    "请走支付/银行通道的人工冲正，再追加一条新的补偿记录（不得宣称这一步已被撤销）")
IRREVERSIBLE_CONTRACT_REASON = (
    "签署不可逆：签名后的合同文本已进入合同相对方与存证系统，本系统无法单方面撤回签署；"
    "请走解约 / 作废 / 补充协议流程，并追加一条补偿记录留证（不得宣称签署已撤销）")
IRREVERSIBLE_REFUND_REASON = (
    "退款不可逆：退款本身是一笔新的资金流水，不是原转账的逆操作；"
    "清算完成后本系统无法单方面撤回这笔退款，请走支付通道的人工冲正并追加补偿记录"
    "（不得宣称这笔退款已撤回）")

#: 资金/合同类工具名的**保守兜底**表：``(needles, op, describe_key, 阻断文案)``。
#: 它排在 :data:`_GENERIC_FALLBACKS` **之前**——两个方向里"说撤销不掉"是保守方向，
#: 宁可让界面收起回滚按钮，也绝不让兜底表把一个资金动作凑成"可逆"（G-18 的病根就是
#: 兜底表只朝"可逆"方向凑覆盖率）。needle 写**精确形态**，避免误伤无关工具名：
#: ``transfer`` 裸词会把 ``file.transfer``（文件传输 = 可逆）判成资金动作；
#: ``sign`` / ``esign`` / ``sign.`` / ``.sign`` 这些形态全部会命中 ``design.*``
#: （``design.`` 本身就含有 ``sign.`` 与 ``esign.``）——**都不许用**。
#: 已知残余面（如实登记，不假装干净）：``designing.*`` 会因 ``signing`` 被判不可逆，
#: ``billing.set_permission`` 会因 ``billing`` 被判不可逆——两者都是"说撤销不掉"的
#: 保守方向，代价是界面少一个回滚按钮，绝不是谎称可逆；钉在
#: ``tests/test_rollback_registry_coverage.py::IrreversibleFallbackNeedleTests``。
_IRREVERSIBLE_FALLBACKS: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("payment", "refund", "remit", "withdraw", "payout", "wire_", "wire.",
      "charge_", "billing", "transaction", "bank_", "bank.", "transfer_",
      "fund_transfer", "invoice.pay", "money.", "cash.", "card.", "paypal",
      "alipay", "stripe", "wechat.pay"),
     IRREVERSIBLE_FINANCIAL_OP, "rollback.inverse.irreversible_financial",
     IRREVERSIBLE_FINANCIAL_REASON),
    (("contract", "sign_doc", "sign_docs", "signing", "signature", "e-sign",
      "esign_", "_esign", "esign-", "sign_order", "sign_agreement", "sign_consent",
      "sign_form", "countersign"),
     IRREVERSIBLE_CONTRACT_OP, "rollback.inverse.irreversible_contract",
     IRREVERSIBLE_CONTRACT_REASON),
)

#: 工具名（小写）→ 逆向动作。精确匹配优先，其次按子串兜底。
#: 之所以要有兜底：外部工具名千奇百怪（filesystem.remove / fs.rm / rm_rf），
#: 只认死名单会让覆盖率变成"名单里恰好有几个"的假指标。
_GENERIC_FALLBACKS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("remove", "delete", "trash", "purge", "erase", "rm_"),
     "restore_file", "rollback.inverse.restore_file"),
    (("send", "email", "mail", "sms", "notify", "post_message"),
     "revoke_message", "rollback.inverse.revoke_message"),
    (("set_permission", "permission", "grant", "policy", "setting", "config", "toggle"),
     "undo_config_change", "rollback.inverse.undo_config_change"),
    (("booking", "reserve", "appointment", "schedule", "order"),
     "cancel_booking", "rollback.inverse.cancel_booking"),
    (("note", "memo", "comment", "doc_write"),
     "undo_note", "rollback.inverse.undo_note"),
    (("generate", "preview", "thumbnail", "render", "snapshot", "export"),
     "delete_generated", "rollback.inverse.delete_generated"),
    (("write", "save", "append", "create", "upload", "put_"),
     "undo_write", "rollback.inverse.undo_write"),
    (("read", "scan", "list", "search", "query", "compare", "hash", "fetch", "get_"),
     "noop_read", "rollback.inverse.noop_read"),
    # —— R1-5 / G-19：booking 动词式 needle（原表只有 ``booking``，``book_flight`` 落空）——
    # 放在表**末位**、且只认动词式形态：把裸 ``book`` 塞进原 booking 组会让
    # ``notebook.write`` / ``bookmark.create`` 被 ``cancel_booking`` 抢走（首匹配即终局，
    # 正是 G-18 说的语义误判）。动词式写法实测零误伤，见测试
    # ``tests/test_rollback_registry_coverage.py::NeedleCoverageTests``。
    (("book_flight", "book_room", "book_ticket", "book_restaurant", "book_slot",
      "book_appointment", "book_trips", "book.", "book_", ".book", "bookings"),
     "cancel_booking", "rollback.inverse.cancel_booking"),
)
#: 兜底命中缓存：避免每次 plan 都重复扫一遍表（注册表变动时清空）。
#: 值现在可能带 ``reversible`` / ``irreversible_reason``（:data:`_IRREVERSIBLE_FALLBACKS`
#: 的产物），所以类型放宽到 ``Any``；调用方一律经 :func:`inverse_entry_of` 深拷贝读取。
_FALLBACK_CACHE: dict[str, dict[str, Any] | None] = {}


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tool_key(tool: Any) -> str:
    return _safe_text(tool).lower()


def register_inverse(tool: str, *, op: str, describe_key: str,
                     apply: Callable[..., Any] | None = None,
                     reversible: bool = True,
                     irreversible_reason: str | None = None) -> None:
    """登记"工具 → 逆向动作"。

    参数是关键字契约（``op`` / ``describe_key`` / ``apply``）；字段非法时抛
    ``ROLLBACK_FIELD_INVALID``，而不是静默忽略——注册失败比"以为登记了"安全。

    ``apply`` 是可选的真实逆向执行器，签名约定::

        apply(record, inverse, *, actor, reason) -> Mapping | None

    返回值会并入回执（``{"op","applied","receipt_state","note"}``）。本产品默认
    不注入任何执行器：离线环境没有外部系统凭据，回执如实写 ``simulated_local``。

    **R1-5 · 显式不可逆登记**（``reversible=False``）：注册表不仅能说"我知道怎么撤销"，
    还必须能说"我知道这步撤销不掉、为什么、该走哪条人工通道"。判不可逆时
    **必须**带 ``irreversible_reason``（人话阻断文案，缺它 = ``ROLLBACK_FIELD_INVALID``
    拒绝登记）：只有 ``reversible=False`` 而没有原因的登记，界面只能说"不行"，
    等于把 :data:`BLOCKER_DEPENDENTS_IRREVERSIBLE` 的取证价值丢掉。
    ``reversible=True`` 时传入的 ``irreversible_reason`` 一律**丢弃**（不给"其实可逆"
    的记录留一条自相矛盾的文案）。``reversible`` 传非布尔值同样拒登——"不可逆"
    是产品纪律，不能被真值语义（``0`` / ``""`` / ``None``）意外触发。

    **回执契约（R1-4 / G-14）**：执行器返回的 ``receipt_state`` 必须在
    :data:`RECEIPT_STATES` 白名单里（越界 → :data:`CODE_INVERSE_FAILED`，绝不落终态），
    ``note`` 与状态必须一致：``executed_sandbox`` / ``executed_registry`` 是真实 I/O，
    文案**不得**再出现「本地模拟 / 离线降级 / 未调用外部系统」这类字样；
    ``simulated_local`` 则必须说清是模拟。自报 ``failed`` 同样按失败处理（不落成功终态）。
    注入沙箱执行器时把路径钉死在显式白名单根内（R1-4 的 ``restore_file`` 试点口径），
    越界一律拒绝执行。
    """
    name = _safe_text(tool)
    if not name:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "工具名不能为空", field="tool")
    action = _safe_text(op)
    if not action:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "逆向动作 op 不能为空",
                            field="op", detail={"tool": name})
    describe = _safe_text(describe_key)
    if not describe:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "逆向动作必须带可翻译的文案键",
                            field="describe_key", detail={"tool": name, "op": action})
    if apply is not None and not callable(apply):
        raise RollbackError("ROLLBACK_FIELD_INVALID", "apply 必须是可调用对象",
                            field="apply", detail={"tool": name, "op": action})
    if not isinstance(reversible, bool):
        raise RollbackError("ROLLBACK_FIELD_INVALID",
                            "reversible 必须是布尔值（不可逆是产品纪律，不走真值语义）",
                            field="reversible",
                            detail={"tool": name, "op": action, "reversible": reversible})
    entry: dict[str, Any] = {"op": action, "describe_key": describe,
                             "reversible": reversible}
    if reversible is False:
        reason_text = _safe_text(irreversible_reason)
        if not reason_text:
            raise RollbackError(
                "ROLLBACK_FIELD_INVALID",
                "登记为不可逆时必须给出人话阻断文案（界面与 blockers 只认这个原因）",
                field="irreversible_reason",
                detail={"tool": name, "op": action},
                hint="写清「为什么撤销不掉 + 该走哪条人工通道」；"
                     "措辞禁用「已撤销 / 已恢复」（docs/07 §10）",
            )
        entry["irreversible_reason"] = reason_text
    _INVERSE_REGISTRY[name] = entry
    if apply is not None:
        _INVERSE_APPLIERS[name] = apply
    else:
        _INVERSE_APPLIERS.pop(name, None)
    _FALLBACK_CACHE.clear()


def unregister_inverse(tool: str) -> bool:
    """移除登记（主要给测试/插件卸载用；返回是否真的移除了）。"""
    name = _safe_text(tool)
    removed = _INVERSE_REGISTRY.pop(name, None) is not None
    _INVERSE_APPLIERS.pop(name, None)
    _FALLBACK_CACHE.clear()
    return removed


def _fallback_for(tool: str) -> dict[str, Any] | None:
    """按子串兜底匹配一个逆向动作（**先查不可逆表，再查可逆兜底表**，R1-5 / G-18）。

    顺序就是口径：资金/合同类工具名先进 :data:`_IRREVERSIBLE_FALLBACKS`，
    判"撤销不掉"是保守方向；只有它没命中，才去 :data:`_GENERIC_FALLBACKS`
    找"我们大概知道怎么撤销"。反过来排会让 ``payment.*`` 被 ``write`` 这类
    通用 needle 凑成 ``undo_write``——那正是 G-18 说的"覆盖率话术"。
    """
    key = _tool_key(tool)
    if not key:
        return None
    if key in _FALLBACK_CACHE:
        return copy.deepcopy(_FALLBACK_CACHE[key])
    found: dict[str, Any] | None = None
    for needles, op, describe, reason in _IRREVERSIBLE_FALLBACKS:
        if any(needle in key for needle in needles):
            found = {"op": op, "describe_key": describe,
                     "reversible": False, "irreversible_reason": reason,
                     "source": "fallback-irreversible"}
            break
    if found is None:
        for needles, op, describe in _GENERIC_FALLBACKS:
            if any(needle in key for needle in needles):
                found = {"op": op, "describe_key": describe, "reversible": True,
                         "source": "fallback"}
                break
    _FALLBACK_CACHE[key] = found
    return copy.deepcopy(found)


def inverse_entry_of(tool: str) -> dict[str, Any] | None:
    """返回注册表条目全貌：``{"op","describe_key","reversible"[,"irreversible_reason"]}``。

    与 :func:`inverse_of` 的分工：本函数给**判定**用（``reversible()`` / ``_covered()`` /
    覆盖率分桶都读它，因此看得见"显式不可逆"），:func:`inverse_of` 给**展示**用
    （保持 R1-4 之前的两键形态，老调用方一个键都不用改）。
    """
    name = _safe_text(tool)
    if not name:
        return None
    exact = _INVERSE_REGISTRY.get(name)
    if exact is not None:
        entry = dict(exact)
        entry.setdefault("reversible", True)
        entry["source"] = "exact"
        return entry
    return _fallback_for(name)


def inverse_of(tool: str) -> dict[str, str] | None:
    """返回 ``{"op","describe_key"}``；没有登记则 ``None``（含子串兜底匹配）。

    **两键形态是对外契约**（R1-4 之前定下）：显式不可逆的判定与阻断文案走
    :func:`inverse_entry_of`，本函数不扩键——判"不可逆"的地方必须显式调用
    :func:`inverse_entry_of` 或 :func:`reversible`，避免调用方拿到 ``op`` 就以为可逆。
    """
    entry = inverse_entry_of(tool)
    if entry is None:
        return None
    return {"op": entry["op"], "describe_key": entry["describe_key"]}


def registered_tools() -> list[str]:
    """当前精确登记的工具名（排序后，供接口/自检展示）。"""
    return sorted(_INVERSE_REGISTRY)


def _applier_for(tool: str) -> Callable[..., Any] | None:
    return _INVERSE_APPLIERS.get(_safe_text(tool))


# --------------------------------------------------------------------------- #
# 撤销日志的读侧口径（R1-4：链化行 + 历史行向后兼容 + 两阶段相位）
# --------------------------------------------------------------------------- #


def journal_phase(entry: Any) -> str:
    """一条撤销日志的相位；**历史行按缺省容忍**（向后兼容读，不迁移、不改写）。

    R1-4 之前的行没有 ``phase`` 键：那些条目当时都是"逆向成功 → 落一条日志"，
    所以缺省按 :data:`PHASE_SUCCEEDED` 读，幂等屏障与读侧派生的行为与 R1-3 逐字一致。
    脏值（拼错的相位）不猜：按缺省成功读会谎称已回滚，按 ``pending`` 读最保守——
    终态未确认就是未确认。
    """
    if not isinstance(entry, Mapping):
        return PHASE_PENDING
    raw = _safe_text(entry.get("phase")).lower()
    if raw in TERMINAL_PHASES:
        return raw
    if raw == PHASE_PENDING:
        return PHASE_PENDING
    return PHASE_SUCCEEDED


def journal_settled_success(entry: Any) -> bool:
    """这条日志是否算"已落终态的成功事实"（幂等屏障与读侧派生的唯一口径）。

    * ``succeeded``（含 R1-4 之前的历史行缺省）→ 是：目标与闭包成员都记"已回滚"；
    * ``failed`` → 否：逆向动作失败、按两阶段纪律没留下副作用，目标必须还能再回滚，
      读侧也绝不许把它派生成"已回滚"；
    * ``pending`` → 否：终态未落、结果未知，绝不对外宣称"已回滚"（界面据此收起入口）。
    """
    return journal_phase(entry) == PHASE_SUCCEEDED


def _chain_recompute(item: Mapping[str, Any], seq: int) -> str:
    """复算一条账本行的 ``entry_hash``：与 ``feature_store.Ledger.append`` 逐字同算法。

    为什么要自己复算：历史行没有链字段，链要在它们之后**重新锚定**，锚点必须按写入端
    当时的算法算出来（把历史行的整体规范 JSON 连同补出的 ``seq`` 取哈希），
    才能既不改写历史行、又让新行接得上链。
    """
    body = {key: value for key, value in item.items() if key not in ("seq", "entry_hash")}
    body["seq"] = int(seq)
    return hash_object(body)


def _is_chained(entry: Mapping[str, Any]) -> bool:
    """这行是 R1-4 起的链化行吗（带 64 位 ``entry_hash``）。历史行返回 ``False``。"""
    raw = entry.get("entry_hash")
    return isinstance(raw, str) and len(raw) == 64


def _pre_state_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    """一条记录的**回滚前快照**条目（TP-06）：整条前态 + 规范 JSON 摘要。

    口径 α：快照只是前态**证据**，绝不写回记录本体；成员记录本身一个字节不改。
    ``snapshot_sha256`` = ``sha256(canonical_json(snapshot))``（R1-3 的锚定口径），
    测试与服务端都独立复算，不信任生产端自述。
    """
    snapshot = copy.deepcopy(dict(record))
    return {
        "id": _safe_text(record.get("id")) or None,
        "action_type": _safe_text(record.get("action_type")) or None,
        "tool": _tool_name(record) or None,
        "snapshot": snapshot,
        "snapshot_sha256": hash_object(snapshot),
    }


def _closure_snapshots(index: Mapping[str, Mapping], order: Sequence[str]) -> list[dict[str, Any]]:
    """执行范围（``order``）全员的前态快照集合，顺序与 ``order`` 一致（深→浅，末位=目标）。

    索引里查不到的成员如实标 ``"missing"``：闭包算出来的人快照不到，说明执行范围与
    索引不一致（例如成员记录被 reset 清掉），这必须看得见，不能静默少一份证据。
    """
    out: list[dict[str, Any]] = []
    for member_id in order:
        record = index.get(str(member_id))
        if not isinstance(record, Mapping):
            out.append({"id": str(member_id), "action_type": None, "tool": None,
                        "snapshot": None, "snapshot_sha256": "missing",
                        "note": "该成员在装载索引里查不到：前态快照无法取证"})
            continue
        out.append(_pre_state_snapshot(record))
    return out


def _seed_registry() -> None:
    """预置演示工具、规格书点名的六个逆向动作，与 R1-5 的补登条目（G-17 / G-18 / G-19）。

    分三层：
    * **逆向动作本身也作为工具名登记**（``cancel_booking`` 等六个）。规格书点名的
      默认逆向实现必须"点名可用"，即使用一个叫 ``cancel_booking`` 的工具时也能直接命中。
    * **演示集里的真实工具名**精确登记，配合 ``_GENERIC_FALLBACKS`` 覆盖外部未知工具名。
    * **R1-5 补登（G-17 的未覆盖三条 + G-19 的落空名单）**：LLM 生成类走"记录级补偿"，
      资金/合同类走"显式不可逆 + 阻断文案"。两条都经 :func:`register_inverse` 登记，
      让导入时就跑一遍字段校验（缺阻断文案 → 导入即炸，比运行时才发现安全）。
    """
    defaults = (
        ("cancel_booking", "cancel_booking", "rollback.inverse.cancel_booking"),
        ("restore_file", "restore_file", "rollback.inverse.restore_file"),
        ("undo_write", "undo_write", "rollback.inverse.undo_write"),
        ("revoke_message", "revoke_message", "rollback.inverse.revoke_message"),
        ("undo_config_change", "undo_config_change", "rollback.inverse.undo_config_change"),
        ("undo_note", "undo_note", "rollback.inverse.undo_note"),
        # —— 演示集（samples/action_records.json）里的具体工具 ——
        ("filesystem.remove", "restore_file", "rollback.inverse.restore_file"),
        ("filesystem.write_file", "undo_write", "rollback.inverse.undo_write"),
        ("notes.write", "undo_note", "rollback.inverse.undo_note"),
        ("message.send_email", "revoke_message", "rollback.inverse.revoke_message"),
        ("plugin.set_permission", "undo_config_change", "rollback.inverse.undo_config_change"),
        ("thumbnail.generate_preview", "delete_generated", "rollback.inverse.delete_generated"),
        ("image.hash_compare", "noop_read", "rollback.inverse.noop_read"),
        ("filesystem.scan_album", "noop_read", "rollback.inverse.noop_read"),
    )
    for tool, op, describe in defaults:
        _INVERSE_REGISTRY[tool] = {"op": op, "describe_key": describe,
                                   "reversible": True}

    # —— R1-5 ① · LLM 生成类（G-17 未覆盖的 ``deepseek-chat`` × 2 /
    # ``text-embedding-demo``）：映射 ``undo_write``，**补偿语义 = 记录级回滚**。
    # 如实写清边界：撤销的是"这条动作在本系统里留下的事实"（追加补偿记录 +
    # 读侧派生"已回滚"）；**已经花掉的 token、已经生成并已外发给用户的内容
    # 不可撤回**，也没有任何外部系统会被调用。把它算"有回滚路径"的依据是
    # "本系统一侧的后续引用可以断掉"，不是"生成结果可以收回"——界面必须按
    # ``describe_key`` 的文案展示成"记录级补偿"，不得写成"已撤回生成结果"。
    register_inverse("deepseek-chat", op="undo_write",
                     describe_key="rollback.inverse.undo_write")
    register_inverse("text-embedding-demo", op="undo_write",
                     describe_key="rollback.inverse.undo_write")

    # —— R1-5 ② · 显式不可逆（TP-04 后半句：``payment.transfer`` / ``contract.sign``
    # 必须不可逆 + 有阻断文案）；``refund.payment`` 按语义同类（一笔新的资金流水，
    # 不是原转账的逆操作）判同款。三者都带 ``irreversible_reason``，缺文案会在导入期
    # 直接抛 ``ROLLBACK_FIELD_INVALID`` —— 这条纪律不允许被"忘了写文案"悄悄绕过。
    register_inverse("payment.transfer", op=IRREVERSIBLE_FINANCIAL_OP,
                     describe_key="rollback.inverse.irreversible_financial",
                     reversible=False,
                     irreversible_reason=IRREVERSIBLE_FINANCIAL_REASON)
    register_inverse("contract.sign", op=IRREVERSIBLE_CONTRACT_OP,
                     describe_key="rollback.inverse.irreversible_contract",
                     reversible=False,
                     irreversible_reason=IRREVERSIBLE_CONTRACT_REASON)
    register_inverse("refund.payment", op=IRREVERSIBLE_FINANCIAL_OP,
                     describe_key="rollback.inverse.irreversible_financial",
                     reversible=False,
                     irreversible_reason=IRREVERSIBLE_REFUND_REASON)
    _FALLBACK_CACHE.clear()


_seed_registry()


# --------------------------------------------------------------------------- #
# 单条记录的可逆判断
# --------------------------------------------------------------------------- #


def _tool_name(record: Any) -> str:
    if not isinstance(record, Mapping):
        return ""
    tool = record.get("tool_used")
    if isinstance(tool, Mapping):
        return _safe_text(tool.get("name"))
    return ""


def reversible(record: Mapping) -> dict[str, Any]:
    """判断**这一条记录**能不能撤销：``{"reversible","op","reason","describe_key"}``。

    三个条件同时成立才算可逆（``docs/07`` §4.5）：

    1. 注册表（或子串兜底）里登记了该工具的逆向动作；
    2. 来源的 ``impact_scope.reversible`` **没有明说 False**；
    3. 注册表**自己没有**把这把工具判成显式不可逆（``reversible=False``，R1-5 / TP-04）。

    第 2 条是产品纪律（W-03）：登记表说"删除可以还原"，但来源说"不进回收站"，那
    事实以来源为准——界面必须写"此操作不可撤销"，不能拿兜底表覆盖来源事实。
    第 3 条是同一纪律的注册表侧（R1-5）：``payment.transfer`` 这类动作**有**外部副作用、
    本系统**没有**单方面撤销通道，``reason`` 就是登记时写死的**人话阻断文案**
    （界面据此收起回滚按钮并解释"该走哪条人工通道"，绝不是一句"没有登记"）。
    来源标注优先于注册表判定：来源说不可逆时，原因引用来源（事实源在外部）。
    """
    if not isinstance(record, Mapping):
        return {"reversible": False, "op": None, "describe_key": None,
                "reason": "记录不是对象，无法判断可逆性"}
    tool = _tool_name(record)
    entry = inverse_entry_of(tool) if tool else None
    op = entry["op"] if entry else None
    describe = entry["describe_key"] if entry else None

    if record.get("rollback_state") == "rolled_back":
        return {"reversible": False, "op": op, "describe_key": describe,
                "reason": "该动作已经回滚过，不能重复回滚"}
    scope = record.get("impact_scope")
    if isinstance(scope, Mapping) and scope.get("reversible") is False:
        return {"reversible": False, "op": op, "describe_key": describe,
                "reason": "来源的影响面把该动作标注为不可撤销（impact_scope.reversible=false）"}
    if entry is None:
        reason = ("记录没有工具动作，没有可撤销的外部副作用" if not tool
                  else f"逆向操作注册表里没有工具「{tool}」的逆向动作")
        return {"reversible": False, "op": None, "describe_key": None, "reason": reason}
    if entry.get("reversible") is False:
        return {"reversible": False, "op": op, "describe_key": describe,
                "reason": _safe_text(entry.get("irreversible_reason"))
                or f"注册表把工具「{tool}」判定为不可逆（未登记阻断文案）"}
    return {"reversible": True, "op": op, "describe_key": describe,
            "reason": "已登记逆向动作，且来源与注册表均未标注不可逆"}


# --------------------------------------------------------------------------- #
# 回滚计划
# --------------------------------------------------------------------------- #


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _record_dependencies(record: Mapping) -> set[str]:
    """一条记录显式声明依赖的目标 id 集合（动作树 + context.depends_on）。"""
    deps: set[str] = set()
    parent = record.get("parent_action_id")
    if isinstance(parent, str) and parent:
        deps.add(parent)
    context = record.get("context")
    if isinstance(context, Mapping):
        for item in _as_list(context.get("depends_on")):
            if isinstance(item, str) and item:
                deps.add(item)
    return deps


def _build_index(records: Sequence[Mapping]) -> dict[str, Mapping]:
    index: dict[str, Mapping] = {}
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        key = record.get("id")
        if isinstance(key, str) and key:
            index[key] = record
    return index


def _action_type(record: Mapping) -> str:
    return _safe_text(record.get("action_type"))


def _excluded_from_closure(record: Mapping) -> bool:
    """干预/回滚记录不参与撤销闭包（裁决 D-closure）。目标自身永远在闭包里。"""
    return _action_type(record) in CLOSURE_EXCLUDED_ACTION_TYPES


def _member_unundoable_reason(record: Mapping) -> str | None:
    """闭包成员"撤销不掉"的原因；可撤销/天然无需撤销则返回 ``None``。

    口径与 :func:`_covered` 对齐（同一套"天然覆盖 / 机制覆盖"）：

    * 尚未执行的步骤 → 撤销路径 = 取消，不构成阻断；
    * 只读/规划/决定类（:data:`NO_EFFECT_ACTIONS`）→ 没有外部副作用，不构成阻断；
    * 其余成员交给 :func:`reversible`：注册表无逆向动作、或来源
      ``impact_scope.reversible=false`` → 该成员撤销不掉，必须进 blockers。
    """
    if record.get("status") in NOT_EXECUTED_STATUSES:
        return None
    if _action_type(record) in NO_EFFECT_ACTIONS:
        return None
    verdict = reversible(record)
    return None if verdict["reversible"] else (verdict["reason"] or "该成员没有可用的逆向动作")


def _detect_cycle(index: Mapping[str, Mapping], root: str, *,
                  participates: Callable[[str], bool]) -> tuple[bool, list[str]]:
    """在「依赖 → 被依赖者」的反向图上做一次三色 DFS，找从 ``root`` 出发的环。

    返回 ``(cycle_detected, cycle_members)``。环不阻断执行（深度上限早已兜住递归），
    但必须**如实报告**：环里谁先谁后没有正确答案，界面得把这件事说出来（G-10）。
    """
    def children_of(node: str) -> list[str]:
        # 环可以穿过目标本身（c1↔c2 互为 parent 就是这种），所以 root 也算一条边的终点。
        found: list[str] = []
        for key, rec in index.items():
            if node not in _record_dependencies(rec):
                continue
            if key == root or participates(key):
                found.append(key)
        return found

    stack: list[tuple[str, list[str], int]] = [(root, children_of(root), 0)]
    path: list[str] = [root]
    on_path: set[str] = {root}
    done: set[str] = set()
    members: list[str] = []
    while stack:
        node, children, cursor = stack[-1]
        if cursor >= len(children):
            stack.pop()
            on_path.discard(node)
            if len(path) > 1:
                path.pop()
            done.add(node)
            continue
        nxt = children[cursor]
        stack[-1] = (node, children, cursor + 1)
        if nxt in on_path:
            start = path.index(nxt)
            for item in path[start:]:
                if item not in members:
                    members.append(item)
            continue
        if nxt in done:
            continue
        done.add(nxt)
        path.append(nxt)
        on_path.add(nxt)
        stack.append((nxt, children_of(nxt), 0))
    return bool(members), members


def _collect_dependents(index: Mapping[str, Mapping], action_id: str,
                        max_depth: int, *,
                        rolled_back_ids: set[str] | None = None) -> dict[str, Any]:
    """收集依赖 ``action_id`` 的后续步骤，并按 R1-2a 口径治理闭包成员。

    返回一个字典（内部函数，字段随治理口径增长，不再扩位置返回值）：

    * ``dependents`` / ``order``：**参与闭包**的成员（浅→深 / 深→浅），目标在 ``order`` 末位；
      其中撤销不掉的成员只出现在 ``dependents``（给界面解释），**绝不进 ``order``**；
    * ``truncated`` / ``truncated_members``：因 ``max_depth`` 被截断的成员（必须如实说）；
    * ``excluded_members``：干预/回滚记录被排除在闭包外的成员（裁决 D-closure）；
    * ``skipped_already_rolled_back``：撤销日志（或历史字段）在案、不再重入的成员（G-7）；
    * ``irreversible_members``：``{id, reason}``——撤销不掉的成员（裁决 D-irrev）；
    * ``cycle_detected`` / ``cycle_members``：环告警（G-10）。
    """
    already = set(rolled_back_ids or ())
    positions = {key: position for position, key in enumerate(index)}

    def participates(key: str) -> bool:
        record = index.get(key)
        return isinstance(record, Mapping) and not _excluded_from_closure(record)

    depth_of: dict[str, int] = {}
    excluded: list[str] = []
    skipped: list[str] = []
    truncated_members: list[str] = []
    seen: set[str] = set()          # 已发现即定格：环与重复依赖都不会让节点二次入队
    queue: list[tuple[str, int]] = [(action_id, 0)]
    truncated = False
    while queue:
        current, depth = queue.pop(0)
        for key, record in index.items():
            if key == action_id or key in seen:
                continue
            if current not in _record_dependencies(record):
                continue
            seen.add(key)
            if not participates(key):
                excluded.append(key)          # 干预决定/回滚补偿：不进闭包
                continue
            if depth >= max_depth:
                truncated = True              # 确有下一层：如实报告，绝不静默丢弃
                truncated_members.append(key)
                continue
            if key in already:
                skipped.append(key)           # 已回滚过：跳过并提示，不重复标记
                continue
            depth_of[key] = depth + 1
            queue.append((key, depth + 1))

    dependents = sorted(depth_of, key=lambda key: (depth_of[key], positions.get(key, 0)))
    irreversible: list[dict[str, Any]] = []
    for key in dependents:
        reason = _member_unundoable_reason(index[key])
        if reason is not None:
            irreversible.append({"id": key, "reason": reason})
    # 裁决 D-irrev：撤销不掉的成员**绝不进入执行范围**——它们留在 ``dependents`` 里给界面
    # 解释"为什么这条不能一起撤销"，但 ``order``（= 执行范围）里一个都不许出现，
    # 否则日志与回滚记录的影响面就会宣称覆盖了从未被处理的成员（G-5 的假闭环）。
    blocked = {str(item["id"]) for item in irreversible}
    order = [key for key in
             sorted(depth_of, key=lambda key: (-depth_of[key], -positions.get(key, 0)))
             if key not in blocked]
    order.append(action_id)
    cycle_detected, cycle_members = _detect_cycle(index, action_id, participates=participates)
    return {
        "dependents": dependents,
        "order": order,
        "truncated": bool(truncated),
        "truncated_members": truncated_members,
        "excluded_members": excluded,
        "skipped_already_rolled_back": skipped,
        "irreversible_members": irreversible,
        "cycle_detected": bool(cycle_detected),
        "cycle_members": cycle_members,
    }


def _safe_id(value: Any, *, fallback: str = "local") -> str:
    """把任意 actor/id 收敛成 schema 的 identifier 形态（禁止空格与路径字符）。"""
    text = _safe_text(value)
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._:-")[:120]
    if not cleaned or not cleaned[0].isalnum():
        return fallback
    return cleaned


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _rolled_back_ids(records: Sequence[Mapping]) -> set[str]:
    """记录字段口径的"已回滚"集合（历史行里可能已有 ``rollback_state=rolled_back``）。

    口径 α 之后新数据不再写这个字段；这里只为**兼容历史数据**而读它。真正的事实源是
    撤销日志，由 :meth:`RollbackService.plan` 经 ``rolled_back_ids`` 传进来（G-6）。
    """
    found: set[str] = set()
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        if record.get("rollback_state") != "rolled_back":
            continue
        key = record.get("id")
        if isinstance(key, str) and key:
            found.add(key)
    return found


def plan(records: Sequence[Mapping], action_id: str, *,
         max_depth: int = DEFAULT_MAX_DEPTH,
         rolled_back_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """生成回滚计划（不改任何东西）。

    返回 ``{"ok","action","target","dependents","order","reversible","blockers",
    "requires_confirmation","truncated", ...}``：

    * ``action`` = 目标 id（前端直接用来高亮）；
    * ``target`` = 目标记录摘要（动作类型/工具/状态/风险）；
    * ``dependents`` = 依赖该步骤、**参与闭包**的后续动作 id（浅→深）；
    * ``order`` = 回滚执行顺序（深→浅，目标在最后）；
    * ``blockers`` = ``[{code, action_id, reason, ...}]``，非空时 ``ok=False``；
    * ``requires_confirmation`` = 有依赖、有截断或高危时必须二次确认（G-9：
      ``truncated=true`` 必然 ``requires_confirmation=true``，预览与执行同口径）；
    * ``notices`` = 非阻断提示（闭包排除、跳过已回滚、截断、环），前端必须显示；
    * ``cycle_detected`` / ``cycle_members`` = 循环依赖告警（G-10，不静默）。

    ``rolled_back_ids`` 是**纯增量**参数：撤销日志在案的 id（由
    :meth:`RollbackService.plan` 传入）。它让 plan 与 execute 共用同一条幂等屏障
    （G-6），也让这些成员不再重入闭包（G-7）。
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "max_depth 必须是非负整数",
                            field="max_depth", detail={"max_depth": max_depth})
    key = _safe_text(action_id)
    if not key:
        raise RollbackError("ROLLBACK_FIELD_INVALID", "action_id 不能为空",
                            field="action_id")
    index = _build_index(records)
    target = index.get(key)
    if target is None:
        raise RollbackError("ROLLBACK_NOT_FOUND", f"找不到要回滚的动作：{key}",
                            field="action_id", detail={"action_id": key},
                            hint="先确认记录是否已导入；回滚只对已存在的记录生效")
    journal_ids = {str(item) for item in (rolled_back_ids or []) if str(item)}
    already = journal_ids | _rolled_back_ids(records)
    closure = _collect_dependents(index, key, max_depth, rolled_back_ids=already)
    dependents = closure["dependents"]
    order = closure["order"]
    truncated = closure["truncated"]
    verdict = reversible(target)
    target_rolled_back = key in already
    member_ids = {str(item["id"]) for item in closure["irreversible_members"]}

    blockers: list[dict[str, Any]] = []
    if target_rolled_back:
        source = "journal" if key in journal_ids else "record"
        blockers.append({"code": BLOCKER_ALREADY_ROLLED_BACK, "action_id": key,
                         "source": source,
                         "reason": "该动作已经回滚过，撤销日志里也有对应条目"})
    if not verdict["reversible"]:
        blockers.append({"code": "ROLLBACK_NOT_REVERSIBLE", "action_id": key,
                         "reason": verdict["reason"]})
    if closure["irreversible_members"]:
        blockers.append({
            "code": BLOCKER_DEPENDENTS_IRREVERSIBLE, "action_id": key,
            "members": sorted(member_ids),
            "members_detail": list(closure["irreversible_members"]),
            "reason": "依赖它的后续步骤里有撤销不掉的成员：" +
                      "；".join(f"{item['id']}（{item['reason']}）"
                                for item in closure["irreversible_members"]),
            "hint": "这些成员绝不会被标记为已回滚，也不会进入执行范围；"
                    "确认请带 confirm_all=true 重试，界面必须逐条说明原因",
        })
    risk = target.get("risk_level")
    requires_confirmation = (bool(dependents) or bool(truncated)
                             or risk in REQUIRES_CONFIRMATION_RISKS)

    notices: list[dict[str, Any]] = []
    if closure["excluded_members"]:
        notices.append({"code": NOTICE_MEMBERS_EXCLUDED, "action_ids": closure["excluded_members"],
                        "reason": "干预决定与回滚补偿记录不进撤销闭包（裁决 D-closure）"})
    if closure["skipped_already_rolled_back"]:
        notices.append({"code": NOTICE_MEMBERS_ALREADY_ROLLED_BACK,
                        "action_ids": closure["skipped_already_rolled_back"],
                        "reason": "这些成员此前已在回滚范围内，本次跳过，不重复处理（G-7）"})
    if truncated:
        notices.append({"code": NOTICE_DEPENDENTS_TRUNCATED,
                        "action_ids": closure["truncated_members"],
                        "count": len(closure["truncated_members"]),
                        "max_depth": max_depth,
                        "reason": f"依赖比 max_depth={max_depth} 更深，"
                                  f"{len(closure['truncated_members'])} 个成员未纳入本次范围，"
                                  "必须经用户确认（G-9）"})
    if closure["cycle_detected"]:
        notices.append({"code": NOTICE_CYCLE_DETECTED, "action_ids": closure["cycle_members"],
                        "reason": "检测到循环依赖：环内成员的先后顺序没有正确答案（G-10）"})

    return {
        "ok": not blockers,
        "action": key,
        "target": {
            "id": key,
            "action_type": target.get("action_type"),
            "tool": _tool_name(target) or None,
            "status": target.get("status"),
            "risk_level": risk,
            "rollback_state": target.get("rollback_state"),
        },
        "dependents": dependents,
        "order": order,
        "reversible": bool(verdict["reversible"]),
        "blockers": blockers,
        "requires_confirmation": bool(requires_confirmation),
        "truncated": bool(truncated),
        # —— 以下为 R1-2a 纯增量字段 ——
        "notices": notices,
        "irreversible_dependents": sorted(member_ids),
        "skipped_already_rolled_back": list(closure["skipped_already_rolled_back"]),
        "excluded_members": list(closure["excluded_members"]),
        "truncated_members": list(closure["truncated_members"]),
        "cycle_detected": bool(closure["cycle_detected"]),
        "cycle_members": list(closure["cycle_members"]),
    }


# --------------------------------------------------------------------------- #
# 覆盖率（C-1：明细与分桶是返回的一部分，不靠外部脚本现算）
# --------------------------------------------------------------------------- #

#: 覆盖率分桶的稳定键（顺序 = 天然覆盖在前、机制覆盖居中、未覆盖在后）。
#: 键名是对外口径（报告与界面按它聚合），中文标签见 :data:`COVERAGE_BUCKET_LABELS`。
#: R1-5 新增 ``uncovered_registry_irreversible``（注册表判显式不可逆）；
#: 既有的桶名一个没改，老报告行照读。
COVERAGE_BUCKETS: tuple[str, ...] = (
    "natural_not_executed", "natural_no_effect", "mechanism",
    "uncovered_no_tool", "uncovered_no_inverse",
    "uncovered_source_irreversible", "uncovered_registry_irreversible",
)
#: **天然覆盖**的桶：没有外部副作用 / 尚未执行（撤销路径 = 什么都不用做 / 取消）。
#: 「有外部副作用子集」（A1 主口径的分母）= 全体 − 这两个桶。
NATURAL_COVERAGE_BUCKETS: tuple[str, ...] = ("natural_not_executed", "natural_no_effect")
#: 算"有回滚路径"的桶 = 天然覆盖 + 机制覆盖（与 :func:`_covered` 逐字等价，测试钉住）。
COVERED_COVERAGE_BUCKETS: frozenset[str] = frozenset(
    NATURAL_COVERAGE_BUCKETS + ("mechanism",))
#: 中文标签（界面与报告直接展示；措辞不写"已恢复/已撤销"）
COVERAGE_BUCKET_LABELS: Mapping[str, str] = MappingProxyType({
    "natural_not_executed": "天然覆盖/未执行",
    "natural_no_effect": "天然覆盖/只读规划",
    "mechanism": "机制覆盖",
    "uncovered_no_tool": "未覆盖/无工具动作",
    "uncovered_no_inverse": "未覆盖/注册表与兜底都没命中",
    "uncovered_source_irreversible": "未覆盖/来源标注不可逆",
    "uncovered_registry_irreversible": "未覆盖/注册表判定不可逆",
})


def _coverage_bucket(record: Mapping) -> str:
    """一条记录落在哪个覆盖桶（:func:`_covered` 与 ``coverage()`` 的**唯一**判据源）。

    判据与 R1-1 时代的 :func:`_covered` 逐条等价，只多了 R1-5 的注册表不可逆一桶：

    * 尚未执行 → 撤销路径 = 取消（天然覆盖）；
    * 只读/规划/决定类 → 没有外部副作用（天然覆盖）；
    * 没有工具动作 → 没有可撤销的东西，**也不假装覆盖**；
    * 注册表与兜底都没命中 → 我们不知道怎么撤销（未覆盖，绝不靠兜底凑指标）；
    * 来源标注 ``impact_scope.reversible=false`` → 来源事实优先（未覆盖）；
    * 注册表判显式不可逆（R1-5）→ 有副作用但没有单方面撤销通道（未覆盖）；
    * 其余 → 机制覆盖。
    """
    if record.get("status") in NOT_EXECUTED_STATUSES:
        return "natural_not_executed"
    if record.get("action_type") in NO_EFFECT_ACTIONS:
        return "natural_no_effect"
    tool = _tool_name(record)
    if not tool:
        return "uncovered_no_tool"
    entry = inverse_entry_of(tool)
    if entry is None:
        return "uncovered_no_inverse"
    scope = record.get("impact_scope")
    if isinstance(scope, Mapping) and scope.get("reversible") is False:
        return "uncovered_source_irreversible"
    if entry.get("reversible") is False:
        return "uncovered_registry_irreversible"
    return "mechanism"


def _coverage_reason(record: Mapping) -> str:
    """未覆盖的原因（逐条给界面/报告用）：直接取 :func:`reversible` 的人话 ``reason``。"""
    return _safe_text(reversible(record).get("reason")) or "该步骤没有可用的回滚路径"


def _covered(record: Mapping) -> bool:
    """一步是否"有回滚路径"（见 :meth:`RollbackService.coverage` 的口径说明）。"""
    return _coverage_bucket(record) in COVERED_COVERAGE_BUCKETS


# --------------------------------------------------------------------------- #
# 回滚服务
# --------------------------------------------------------------------------- #


class RollbackService:
    """回滚执行 + 撤销日志（本地模拟回执）。

    用法::

        svc = RollbackService(paths)
        info = svc.plan(records, "act-demo-0007")     # 只读，含撤销日志幂等屏障
        out = svc.execute(records, "act-demo-0007", actor="local", reason="...")
        out["records"]                                # 逐字段等于原记录的副本（口径 α：零改写）
        out["rollback_record"]                        # 新的回滚 ActionRecord
        svc.journal()                                 # 撤销日志（含回滚前快照）

    ``paths`` 是 :class:`FeaturePaths`；本模块只写 ``<data>`` 下的证据/日志，绝不
    写仓库 ``var/``（调用方传临时目录即可单测）。

    口径 α（PM-VERDICT-R1-1 裁决 D-α）：**"已回滚" 是读时派生的状态，不是写回记录
    的字段**。已签名记录的 ``entry_hash`` 覆盖整条记录，写一个字段就复算失效，
    §0.4「历史记录不可原地修改」没有例外。因此 ``execute()`` 只做两件事——
    追加一条撤销日志、产出一条新的回滚记录（``action_type='rollback'`` +
    ``rollback_ref``）；返回的 ``records`` 副本**逐字段等于原记录**，落不落盘都不改变
    原记录字节。
    """

    def __init__(self, paths: FeaturePaths, *,
                 applier: Callable[..., Any] | None = None) -> None:
        if not isinstance(paths, FeaturePaths):
            raise TypeError("paths 必须是 feature_store.FeaturePaths")
        if applier is not None and not callable(applier):
            raise TypeError("applier 必须可调用")
        self.paths = paths
        self.paths.ensure()
        #: 调用方注入的真实逆向执行器；缺省 = 本地模拟（simulated_local）
        self.applier = applier

    # -- 读 ---------------------------------------------------------------
    @property
    def journal_pending_path(self) -> pathlib.Path:
        """两阶段日志的旁路 WAL 路径（与 ``journal.jsonl`` 同目录，R1-4 / G-15）。"""
        return self.paths.rollback_dir / JOURNAL_PENDING_FILE_NAME

    def _journal(self) -> Any:
        """打开并装载撤销日志（链化账本）；读不动就包装成 ``rollback.*`` 码（G-16）。

        R1-4 / G-13：这里从 ``open_store`` 换成 :func:`feature_store.open_ledger`——
        同一个文件、同一套回放语义（历史行照读），只是写入端改走链化追加，
        行里多出 ``seq`` / ``prev_hash`` / ``entry_hash``。

        存储层的 ``FEATURE_STORE_READ_FAILED`` / ``PermissionError`` 直接逃出去会
        越过服务层的错误码表变成一句 500，而模块 docstring 承诺"所有对外错误都带
        ``rollback.*`` 码"——幂等屏障与回滚前证据全在这一份日志里，读不动必须如实说。
        """
        path = self.paths.rollback_journal
        try:
            store = open_ledger(path)
            store.load()
        except RollbackError:  # 已经是本域错误码，原样透传
            raise
        except Exception as exc:  # FeatureStoreError / OSError 等统一收敛
            raise RollbackError(
                CODE_JOURNAL_UNREADABLE,
                f"撤销日志读不动，无法判定回滚状态：{exc}",
                field="journal",
                detail={"path": str(path), "cause": type(exc).__name__,
                        "cause_code": getattr(exc, "code", None)},
                hint="检查数据目录可读性与文件形态（被目录/只读占用都会走到这里）；"
                     "读不动时不得向用户显示『可回滚』，也不得执行回滚",
            ) from exc
        return store

    def _journal_pending(self) -> Any:
        """打开并装载两阶段日志的旁路 WAL；读不动同样收敛成 ``rollback.*`` 码。"""
        path = self.journal_pending_path
        try:
            store = open_ledger(path)
            store.load()
        except RollbackError:
            raise
        except Exception as exc:
            raise RollbackError(
                CODE_JOURNAL_UNREADABLE,
                f"两阶段日志的 pending 账本读不动：{exc}",
                field="journal_pending",
                detail={"path": str(path), "cause": type(exc).__name__,
                        "cause_code": getattr(exc, "code", None)},
                hint="pending 账本与撤销日志同目录；读不动时结果未知，不得宣称已回滚",
            ) from exc
        return store

    def journal(self) -> list[dict[str, Any]]:
        """撤销日志的**终态条目**（按写入顺序；每条含 ``snapshot`` 回滚前快照）。

        R1-4 起每行还带链字段（``seq`` / ``prev_hash`` / ``entry_hash``）与
        ``phase`` / ``snapshots``；R1-4 之前的历史行原样读出了什么就是什么
        （缺省按 ``succeeded`` 容忍，见 :func:`journal_phase`）。
        """
        store = self._journal()
        try:
            return store.items()
        except RollbackError:
            raise
        except Exception as exc:  # 同 :meth:`_journal`：读侧一律 rollback.* 码
            raise RollbackError(
                CODE_JOURNAL_UNREADABLE,
                f"撤销日志条目解析失败：{exc}",
                field="journal",
                detail={"path": str(self.paths.rollback_journal),
                        "cause": type(exc).__name__,
                        "cause_code": getattr(exc, "code", None)},
                hint="撤销日志是追加式账本：坏一行只坏一行，整表读不动时如实报错，不重建",
            ) from exc

    def journal_pending(self) -> list[dict[str, Any]]:
        """旁路 WAL 里的 ``pending`` 前态条目（两阶段日志的第一段，取证用）。"""
        store = self._journal_pending()
        try:
            return store.items()
        except RollbackError:
            raise
        except Exception as exc:
            raise RollbackError(
                CODE_JOURNAL_UNREADABLE,
                f"pending 账本条目解析失败：{exc}",
                field="journal_pending",
                detail={"path": str(self.journal_pending_path),
                        "cause": type(exc).__name__,
                        "cause_code": getattr(exc, "code", None)},
                hint="pending 账本只追加不改写：坏一行只坏一行，绝不重建",
            ) from exc

    def unresolved_pending(self) -> list[dict[str, Any]]:
        """落了 ``pending`` 但撤销日志里**没有对应终态行**的条目（结果未知，必须可见）。

        正常路径下每笔回滚都以终态收尾，这个列表是空的；非空说明进程在"执行逆向
        之后、落终态之前"被打断过——这时外部副作用可能已经发生，界面绝不能按
        "没回滚过"处理，也不能按"已回滚"处理，只能如实报"结果待人工核对"。
        """
        settled = {str(entry.get("id") or "") for entry in self.journal()
                   if isinstance(entry, Mapping)}
        out: list[dict[str, Any]] = []
        for entry in self.journal_pending():
            if not isinstance(entry, Mapping):
                continue
            if journal_phase(entry) != PHASE_PENDING:
                continue
            if str(entry.get("journal_id") or "") in settled:
                continue
            out.append(entry)
        return out

    def journal_chain_verify(self, *, path: pathlib.Path | str | None = None) -> dict[str, Any]:
        """复算撤销日志的哈希链：**历史行按缺省容忍**（向后兼容读，不迁移、不改写）。

        返回 ``{ok, count, head, problems, chained, legacy}``。口径：

        * R1-4 起的链化行（带 ``entry_hash``）逐条核 ``seq`` 递增、``prev_hash`` 咬合、
          ``entry_hash`` 复算相等；
        * R1-4 之前的 ``{op, ts, item}`` 历史行没有任何链字段：**不算篡改、不算断链**，
          只是"链还没出生"。链在历史行之后按写入端算法重新锚定（:func:`_chain_recompute`），
          所以新旧混排的日志照样能复算通过；
        * 只报告，绝不修、不清、不重建（与审计链同一口径：篡改必须报错）。
        """
        target = pathlib.Path(path) if path else self.paths.rollback_journal
        store = open_ledger(target)
        try:
            store.load(force=True)
            items = list(store.items())
        except RollbackError:
            raise
        except Exception as exc:
            raise RollbackError(
                CODE_JOURNAL_UNREADABLE,
                f"撤销日志链复算读不动：{exc}",
                field="journal",
                detail={"path": str(target), "cause": type(exc).__name__,
                        "cause_code": getattr(exc, "code", None)},
            ) from exc
        problems: list[dict[str, Any]] = []
        prev_expected = GENESIS_HASH
        expected_seq = 1
        chained = 0
        legacy = 0
        for position, item in enumerate(items, start=1):
            if not _is_chained(item):
                # 历史行：没有链字段就是"链还没出生"，容忍它并为后面的链化行重算锚点。
                # 锚点算法与写入端逐字相同（``Ledger.append`` 对无链字段的末行用
                # ``_recompute_hash(last, len(items))``），所以这里 seq 的下一格是
                # ``position + 1``：历史行不占 seq，但它占位置。
                legacy += 1
                prev_expected = _chain_recompute(item, position)
                expected_seq = position + 1
                continue
            chained += 1
            seq = item.get("seq")
            recorded = item.get("entry_hash")
            recorded_prev = item.get("prev_hash")
            if seq != expected_seq:
                problems.append({"code": "ledger_seq_gap", "index": position,
                                 "expected": expected_seq, "found": seq})
            if recorded_prev != prev_expected:
                problems.append({"code": "ledger_prev_mismatch", "index": position,
                                 "expected": prev_expected, "found": recorded_prev})
            recomputed = _chain_recompute(item, seq if isinstance(seq, int) else position)
            if recorded != recomputed:
                problems.append({"code": "ledger_hash_mismatch", "index": position,
                                 "recorded": recorded, "recomputed": recomputed})
            prev_expected = str(recorded)
            expected_seq = (seq if isinstance(seq, int) else position) + 1
        return {
            "ok": not problems,
            "count": len(items),
            "chained": chained,
            "legacy_tolerated": legacy,
            "head": prev_expected,
            "problems": problems,
            "path": str(target),
            "note": "历史行（R1-4 之前，无链字段）按缺省容忍：不迁移、不改写；"
                    "链化行逐条复算 seq/prev_hash/entry_hash",
        }

    def journal_pending_chain_verify(self) -> dict[str, Any]:
        """旁路 WAL 的链复算（整份都是 R1-4 新写的行，直接用账本自己的 ``verify``）。"""
        store = self._journal_pending()
        return store.verify()

    def _touched_ids(self) -> set[str]:
        """撤销日志在案的**成功终态** id：目标（``action_id``）+ 当时纳入范围的成员。

        这是幂等屏障（G-6）与"已回滚成员不重入"（G-7）的**唯一事实源**：
        记录字段里的 ``rollback_state`` 在口径 α 之后不再被写入，日志才是事实。

        R1-4 / G-15：``failed`` 与 ``pending`` 条目**不计入**——失败的逆向动作按两阶段
        纪律没有留下副作用，目标必须可以再回滚；``pending``（终态未落）结果未知，
        既不记"已回滚"也不假装什么都没发生（:meth:`unresolved_pending` 把它摆出来）。
        历史行缺省 = ``succeeded`` → 与 R1-3 行为逐字一致。
        """
        touched: set[str] = set()
        for entry in self.journal():
            if not isinstance(entry, Mapping):
                continue
            if not journal_settled_success(entry):
                continue
            head = entry.get("action_id")
            if isinstance(head, str) and head:
                touched.add(head)
            for item in _as_list(entry.get("affected")):
                if isinstance(item, str) and item:
                    touched.add(item)
        return touched

    def _already_logged(self, action_id: str) -> bool:
        return action_id in self._touched_ids()

    def plan(self, records: Sequence[Mapping], action_id: str, *,
             max_depth: int = DEFAULT_MAX_DEPTH) -> dict[str, Any]:
        """带撤销日志幂等屏障的回滚计划（G-6：与 :meth:`execute` 同口径）。

        模块级 :func:`plan` 是纯函数（不认数据目录，便于服务层与脚本复用）；本方法
        把撤销日志在案的 id 作为 ``rolled_back_ids`` 传进去，于是"二次回滚"在计划层
        与执行层报同一个 ``ROLLBACK_ALREADY_ROLLED_BACK``——前端按 plan 渲染按钮，
        就不该在点击后吃一句 409。
        """
        return plan(records, action_id, max_depth=max_depth,
                    rolled_back_ids=self._touched_ids())

    def coverage(self, records: Sequence[Mapping]) -> dict[str, Any]:
        """可回滚步骤覆盖率：``{"total","reversible","ratio"}``（验收线 ≥ 0.90）
        **+ R1-5 的明细与分桶**（C-1）。

        口径（必须写清楚，否则指标会变成话术）：

        * **天然覆盖**：只读/规划类步骤没有外部副作用；``pending_confirmation`` /
          ``expired`` / ``rejected`` / ``skipped`` 的步骤还没执行——这两类的撤销路径
          分别是"什么都不用做"和"取消"，因此算覆盖；
        * **机制覆盖**：其余步骤要求注册表里有逆向动作，**且**来源未标注
          ``impact_scope.reversible=false``、注册表也未判显式不可逆。已执行又没有
          回滚路径的步骤不计入覆盖——它确实没有，不能靠兜底表凑指标。

        **兼容纪律（R1-5 / C-1）**：``total`` / ``reversible`` / ``ratio`` 三个键的
        语义与数值**一字不变**（演示集仍 12/12/1.0，测试逐字钉住）；新增的两个键是
        纯增量，且都只在这份返回值里存在——**不进 schema、不进
        ``docs/api_contract.json``、不进记录行**（§0.4 与签名域零相关）。

        新增键：

        * ``uncovered``：未覆盖明细，逐条 ``{"id","tool","reason"}``（外加
          ``action_type`` / ``status`` / ``bucket`` 便于取证）。这就是 A1/C-1 要的
          "未覆盖项逐条列原因"——以前只能靠外部脚本现算（REPORT-R1-1 §3），
          现在指标自带可审计的分母。
        * ``buckets``：分桶统计 ``{"counts","labels","side_effect_subset"}``。
          ``side_effect_subset`` 就是 **A1 主口径**：分母只含"有外部副作用"的记录
          （全体 − 天然覆盖两桶）。演示集 9/12 是天然覆盖 → 整集 ``ratio=1.0``
          对 A1 没有判别力（G-17），因此主口径看子集、整集只做回归护栏。
        """
        rows = [row for row in (records or []) if isinstance(row, Mapping)]
        total = len(rows)
        covered = sum(1 for row in rows if _covered(row))
        ratio = (covered / total) if total else 0.0

        # —— R1-5 纯增量：明细 + 分桶（既有三键在上面，一个字节没动）——
        counts: dict[str, int] = {key: 0 for key in COVERAGE_BUCKETS}
        uncovered: list[dict[str, Any]] = []
        natural = 0
        for row in rows:
            bucket = _coverage_bucket(row)
            counts[bucket] = counts.get(bucket, 0) + 1
            if bucket in NATURAL_COVERAGE_BUCKETS:
                natural += 1
                continue
            if bucket in COVERED_COVERAGE_BUCKETS:
                continue
            tool = _tool_name(row)
            uncovered.append({
                "id": _safe_text(row.get("id")) or None,
                "tool": tool or None,
                "reason": _coverage_reason(row),
                "bucket": bucket,
                "bucket_label": str(COVERAGE_BUCKET_LABELS.get(bucket) or bucket),
                "action_type": _safe_text(row.get("action_type")) or None,
                "status": _safe_text(row.get("status")) or None,
            })
        side_total = total - natural
        side_covered = counts.get("mechanism", 0)
        side_ratio = (side_covered / side_total) if side_total else 0.0
        return {
            "total": total,
            "reversible": covered,
            "ratio": round(ratio, 4),
            # —— 以下为 R1-5 纯增量键（C-1）——
            "uncovered": uncovered,
            "buckets": {
                "counts": counts,
                "labels": dict(COVERAGE_BUCKET_LABELS),
                "side_effect_subset": {
                    "total": side_total,
                    "covered": side_covered,
                    "uncovered": side_total - side_covered,
                    "ratio": round(side_ratio, 4),
                    "note": "分母 = 全体 − 天然覆盖（只读规划 + 未执行）；"
                            "A1 主口径看这个 ratio，整集 ratio 只做回归护栏（G-17）",
                },
            },
        }

    # -- 写 ---------------------------------------------------------------
    def execute(self, records: Sequence[Mapping], action_id: str, *,
                actor: str, reason: str = "", confirm_all: bool = False,
                lang: str = "zh", max_depth: int = DEFAULT_MAX_DEPTH) -> dict[str, Any]:
        """执行回滚：追加撤销日志 + 产出回滚记录；**原记录零改写**（口径 α）。

        语义边界（产品纪律，界面照此显示）：

        * 本服务**不调用外部生产系统**：没有注入执行器时 ``receipt_state`` =
          ``simulated_local``；注入沙箱/注册表执行器时如实回报 ``executed_sandbox`` /
          ``executed_registry``（白名单见 :data:`RECEIPT_STATES`，越界拒收）。
          回滚记录的 ``execution_receipt.state`` 恒为 ``unreported``（外部系统没有回执）。
          任何路径都绝不写"已恢复"；
        * 返回 ``records``：本次范围内记录（目标 + 参与闭包的依赖）的副本，
          **逐字段等于原记录**——``rollback_state`` / ``labels`` 一个都不写（裁决 D-α：
          已签名记录不可原地修改，写一个字段 ``entry_hash`` 就复算失效）。"已回滚"由
          ``action_type='rollback'`` + ``rollback_ref`` 与撤销日志在读侧派生；
          传入的 ``records`` 同样不会被就地修改；
        * 回滚前状态的证据：目标的整条前态在 ``snapshot`` / ``snapshot_sha256``，
          执行范围**全员**的逐条前态在 ``snapshots``（R1-4 / TP-06 收口，顺序与
          ``order`` 一致，每条带自己的 ``snapshot_sha256``）。服务端在回滚成功后把
          "journal 条目 id + 目标摘要 + 回滚记录 id"经 ``audit.build_event`` 写成一条
          审计锚定事件（原生事件名 ``rollback.anchored``，R1-4 起入 ``audit.EVENTS``
          白名单，见 server 接线）；
        * 幂等屏障与 :meth:`plan` 共用撤销日志（G-6）：重复回滚 →
          ``ROLLBACK_ALREADY_ROLLED_BACK``，且 ``detail.plan.blockers`` 与之一致；
          日志在案的成员不再重入本次闭包（G-7）；
        * ``max_depth`` 与 :meth:`plan` 同口径（G-9）：``truncated=true`` 必须带
          ``confirm_all=true`` 才继续，绝不静默丢依赖；
        * 闭包成员撤销不掉 → ``ROLLBACK_DEPENDENTS_IRREVERSIBLE``（裁决 D-irrev）：
          不带 ``confirm_all`` 直接拒绝；带了也**只回滚可逆成员**，不可逆成员落在
          ``blocked_members`` 里，永不进入执行范围、日志 ``affected`` 与回滚记录的影响面；
        * 干预/回滚记录不进闭包（裁决 D-closure），因此"撤销一条干预决定"不可能发生。
        """
        if not isinstance(actor, str) or not _safe_text(actor):
            raise RollbackError("ROLLBACK_FIELD_INVALID", "actor 不能为空", field="actor")
        plan_info = self.plan(records, action_id, max_depth=max_depth)
        key = plan_info["action"]
        rows = [row for row in (records or []) if isinstance(row, Mapping)]
        index = _build_index(rows)
        target = index[key]
        tool = _tool_name(target)

        codes = {item["code"] for item in plan_info["blockers"]}
        if BLOCKER_ALREADY_ROLLED_BACK in codes or self._already_logged(key):
            raise RollbackError(
                BLOCKER_ALREADY_ROLLED_BACK,
                f"动作 {key} 已经回滚过，不能重复回滚",
                field="action_id",
                detail={"action_id": key, "plan": plan_info},
                hint="撤销日志是追加式的：重复回滚会掩盖第一次的回执，因此直接拒绝",
            )
        if not plan_info["reversible"]:
            verdict = reversible(target)
            raise RollbackError(
                "ROLLBACK_NOT_REVERSIBLE",
                verdict["reason"] or "该动作没有可用的逆向动作",
                field="action_id",
                detail={"action_id": key, "plan": plan_info,
                        "impact_scope": target.get("impact_scope")},
                hint="来源把影响面标为不可逆时，界面应显示『此操作不可撤销』",
            )
        blocked = list(plan_info["irreversible_dependents"])
        if blocked and not confirm_all:
            raise RollbackError(
                BLOCKER_DEPENDENTS_IRREVERSIBLE,
                f"动作 {key} 的依赖里有 {len(blocked)} 个撤销不掉的成员：" +
                "、".join(blocked),
                field="action_id",
                detail={"action_id": key, "plan": plan_info, "members": blocked},
                hint="这些成员绝不会被标成已回滚，也不会被撤销；界面必须逐条说明为什么撤销不掉，"
                     "并让用户确认『只回滚可逆部分』（confirm_all=true）",
            )
        if plan_info["dependents"] and not confirm_all:
            raise RollbackError(
                "ROLLBACK_DEPENDENTS_PENDING",
                f"动作 {key} 还有 {len(plan_info['dependents'])} 个依赖它的后续步骤，"
                "需要确认一并回滚",
                field="action_id",
                detail={"action_id": key, "plan": plan_info},
                hint="先展示依赖列表，用户确认后再带 confirm_all=true 重试",
            )
        if plan_info["truncated"] and not confirm_all:
            dropped = len(plan_info["truncated_members"])
            raise RollbackError(
                "ROLLBACK_DEPENDENTS_PENDING",
                f"动作 {key} 的依赖比 max_depth={max_depth} 更深，"
                f"{dropped} 个成员未纳入本次范围，需要确认",
                field="action_id",
                detail={"action_id": key, "plan": plan_info,
                        "max_depth": max_depth,
                        "truncated_members": list(plan_info["truncated_members"])},
                hint="计划预览与执行同一口径：截断必须确认，绝不静默丢依赖（G-9）",
            )

        verdict = reversible(target)
        inverse = {"op": verdict["op"], "describe_key": verdict["describe_key"],
                   "tool": tool or None}
        started = utc_stamp()
        affected = list(plan_info["order"])
        # —— R1-4（TP-06 收口）：执行范围（order = 目标 + 参与闭包的成员）**逐条**落前态
        # 快照。口径 α 不变：快照只是前态证据，成员记录本体一个字节都不改写。
        snapshots = _closure_snapshots(index, affected)
        target_row = next((row for row in snapshots if row.get("id") == key), None)
        snapshot = (target_row or _pre_state_snapshot(target))["snapshot"]
        snapshot_sha256 = hash_object(snapshot)
        # 终态条目的 id 先算出来：pending 行靠 ``journal_id`` 指向它，两段双向咬合。
        journal_entry_id = new_id("rbj")

        # —— 第一段（G-15）：终态日志先探一次读得动（读不动 = 状态未知，绝不先执行副作用），
        # 再把"执行前"的 pending 条目写进旁路 WAL。两件事都发生在逆向动作**之前**。
        store = self._journal()
        pending_entry = {
            "id": new_id("rbjp"),
            "journal_id": journal_entry_id,
            "action_id": key,
            "tool": tool or None,
            "inverse_op": verdict["op"],
            "phase": PHASE_PENDING,
            "snapshot": snapshot,
            "snapshots": snapshots,
            "snapshot_sha256": snapshot_sha256,
            "created_at": started,
            "actor": _safe_id(actor),
            "reason": reason or "",
            "affected": affected,
            "max_depth": max_depth,
            "blocked_members": blocked,
            "note": ("两阶段日志第一段：逆向动作尚未执行，本行只是前态取证；"
                     "终态以 journal.jsonl 里同 journal_id 的条目为准"),
        }
        pending_store = self._journal_pending()
        try:
            pending = pending_store.append(pending_entry)
        except RollbackError:
            raise
        except Exception as exc:  # 写不进 pending 就绝不执行逆向：不留"无记录的副作用"
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"两阶段日志的第一段（pending）写入失败，逆向动作未执行：{exc}",
                detail={"action_id": key, "op": verdict["op"],
                        "path": str(self.journal_pending_path),
                        "side_effect": "none"},
                hint="pending 前态写不进账本就执行，等于制造无法取证的副作用；"
                     "检查数据目录可写与磁盘空间，修复后重试（本次零副作用，可安全重放）",
            ) from exc

        # —— 第二段：执行逆向动作；失败必须留下 failed 终态条目，绝不静默 ——
        try:
            receipt = self._apply_inverse(target, inverse, actor=actor, reason=reason)
        except RollbackError as exc:
            outcome = self._append_failed_terminal(
                store, journal_entry_id=journal_entry_id, pending=pending, key=key,
                tool=tool, op=verdict["op"], snapshot=snapshot,
                snapshot_sha256=snapshot_sha256, snapshots=snapshots, started=started,
                actor=actor, reason=reason, affected=affected, max_depth=max_depth,
                blocked=blocked, plan_info=plan_info, lang=lang, exc=exc,
            )
            if not outcome.get("ok"):
                # failed 条目也写不进去：这是取证缺口，必须挂在原错误上如实上报。
                exc.detail["failed_entry_write"] = outcome
            raise

        rollback = self._build_rollback_record(
            target, action_id=key, actor=actor, reason=reason, receipt=receipt,
            affected=affected, lang=lang,
        )
        # R1-3（G-13）：前态快照先落进局部变量，日志条目与返回值共用同一份事实；
        # ``snapshot_sha256`` = 规范 JSON（``canonical_json`` 键排序/紧凑分隔符）的
        # sha256，审计链的锚定事件引用"journal 条目 id + 这个摘要"，取证时不必
        # 重读整份日志逐条复算。
        # R1-4 链化（G-13 收口）：写路径是链化账本的 ``append``，落盘行由写入端补
        # ``seq`` / ``prev_hash`` / ``entry_hash``（与审计链同一口径，不改写历史行）。
        journal_entry = {
            "id": journal_entry_id,
            "action_id": key,
            "tool": tool or None,
            "inverse_op": verdict["op"],
            "snapshot": snapshot,
            "created_at": started,
            "applied_at": utc_stamp(),
            "result": dict(receipt),
            "actor": _safe_id(actor),
            "reason": reason or "",
            "affected": affected,
            "rollback_record_id": rollback["id"],
            "language": rollback["human_readable"]["language"],
            # —— R1-2a 纯增量取证字段（日志是闭包的真实边界，必须说清谁没被处理）——
            "max_depth": max_depth,
            "blocked_members": blocked,
            "skipped_already_rolled_back": list(plan_info["skipped_already_rolled_back"]),
            "truncated_members": list(plan_info["truncated_members"]),
            "cycle_detected": bool(plan_info["cycle_detected"]),
            # —— R1-3 取证锚（G-13）：前态快照的规范 JSON sha256。铁律 2：既有键与
            # 顺序一字不动，新键只追加在末尾（老日志行没有这个键，读侧按缺省处理）。
            "snapshot_sha256": snapshot_sha256,
            # —— R1-4 纯增量键（G-13 / G-15 / TP-06），同样只追加在末尾 ——
            # 闭包全员前态快照（TP-06 收口）：目标 + 每个参与执行的成员，逐条可复算。
            "snapshots": snapshots,
            # 两阶段日志的终态相位（G-15）；历史行没这个键 → 读侧缺省按 succeeded 容忍。
            "phase": PHASE_SUCCEEDED,
            # 指向先落的 pending 行（旁路 WAL）：两段证据双向咬合，取证不用猜。
            "pending_id": pending["id"],
            "pending_seq": pending["seq"],
        }
        try:
            committed = store.append(journal_entry)
        except Exception as exc:  # FeatureStoreError 等存储异常统一收敛
            # 最严重的取证缺口：逆向动作已经执行，终态条目却写不进去。
            # 不静默、不谎称成功：把同一份终态原样落到 pending 账本（相位仍是
            # pending + 失败原因），于是 :meth:`unresolved_pending` 会把它当作
            # "结果未知"摆出来，等人工核对；对外只报 ROLLBACK_INVERSE_FAILED 族。
            fallback = self._record_terminal_fallback(journal_entry, cause=exc)
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"撤销日志终态条目写入失败，回滚未落盘：{exc}",
                detail={"action_id": key, "path": str(self.paths.rollback_journal),
                        "side_effect": "executed", "journal_id": journal_entry_id,
                        "pending_id": pending["id"],
                        "terminal_fallback": fallback},
                hint="检查数据目录可写与磁盘空间；失败时不要向用户显示『已回滚』。"
                     "逆向动作可能已执行：系统不自动重建日志，未解 pending 条目须人工核对",
            ) from exc

        # 口径 α：零改写。副本**逐字段等于原记录**（落不落盘都不改历史字节），
        # "已回滚"由读侧从 action_type='rollback' + rollback_ref 派生。
        updated: list[dict[str, Any]] = [copy.deepcopy(dict(index[record_id]))
                                         for record_id in affected]

        return {
            "ok": True,
            "rollback_record": rollback,
            "affected": affected,
            "inverse": receipt,
            "order": list(plan_info["order"]),
            "records": updated,
            "snapshot": copy.deepcopy(snapshot),
            "journal_id": committed["id"],
            # R1-3（G-13）纯增量返回字段：服务层写审计锚定事件直接引用它，
            # 不必回读撤销日志（日志仍是事实源，两边必须同值，测试钉住）。
            "snapshot_sha256": snapshot_sha256,
            "plan": plan_info,
            # —— R1-2a 纯增量返回字段 ——
            "blocked_members": blocked,
            "skipped_already_rolled_back": list(plan_info["skipped_already_rolled_back"]),
            "excluded_members": list(plan_info["excluded_members"]),
            "cycle_detected": bool(plan_info["cycle_detected"]),
            "max_depth": max_depth,
            "notices": list(plan_info["notices"]),
            # —— R1-4 纯增量返回字段（链化 + 全员快照 + 两阶段）——
            # 闭包全员前态快照（TP-06）：与日志条目同源，测试据此逐条 diff。
            "snapshots": copy.deepcopy(snapshots),
            # 链化事实（G-13）：终态行在账本里的位置与链字段，独立复算用。
            "journal_chain": {"seq": committed["seq"],
                              "prev_hash": committed["prev_hash"],
                              "entry_hash": committed["entry_hash"]},
            # 两阶段日志（G-15）：终态相位 + 先落的 pending 行指针。
            "journal_phase": PHASE_SUCCEEDED,
            "pending_journal_id": pending["id"],
            "pending_seq": pending["seq"],
        }

    # -- 内部 -------------------------------------------------------------
    def _append_failed_terminal(self, store: Any, *, journal_entry_id: str,
                                pending: Mapping[str, Any], key: str,
                                tool: str | None, op: str,
                                snapshot: Any, snapshot_sha256: str,
                                snapshots: Sequence[Mapping[str, Any]],
                                started: str, actor: str, reason: str,
                                affected: Sequence[str], max_depth: int,
                                blocked: Sequence[Mapping[str, Any]],
                                plan_info: Mapping[str, Any], lang: str,
                                exc: BaseException) -> dict[str, Any]:
        """逆向动作失败 → 追加 ``phase="failed"`` 终态条目（G-15 的第二段失败路径）。

        返回 ``{"ok", ...}``；**绝不抛新错**——原始失败才是主角，本方法只负责把
        "我们试过、失败了、没有副作用记录"这件事落进链化账本，写不进去就如实报缺口。
        """
        language = "en" if str(lang).lower().startswith("en") else "zh"
        code = str(getattr(exc, "code", "") or CODE_INVERSE_FAILED)
        reason_text = str(getattr(exc, "reason", "") or exc)
        entry = {
            "id": journal_entry_id,
            "action_id": key,
            "tool": tool,
            "inverse_op": op,
            "snapshot": snapshot,
            "created_at": started,
            "applied_at": utc_stamp(),
            "result": {"op": op, "applied": False,
                       "receipt_state": FAILED_RECEIPT_STATE, "note": reason_text},
            "actor": _safe_id(actor),
            "reason": reason or "",
            "affected": list(affected),
            "rollback_record_id": None,
            "language": language,
            "max_depth": max_depth,
            "blocked_members": list(blocked),
            "skipped_already_rolled_back": list(plan_info.get("skipped_already_rolled_back") or []),
            "truncated_members": list(plan_info.get("truncated_members") or []),
            "cycle_detected": bool(plan_info.get("cycle_detected")),
            "snapshot_sha256": snapshot_sha256,
            "snapshots": list(snapshots),
            "phase": PHASE_FAILED,
            "pending_id": pending.get("id"),
            "pending_seq": pending.get("seq"),
            # 取证细节：错误码原样带出（不重命名），字段/提示/细节都在案。
            "failure": {"code": code, "reason": reason_text,
                        "field": getattr(exc, "field", None),
                        "hint": getattr(exc, "hint", None),
                        "detail": dict(getattr(exc, "detail", None) or {})},
        }
        try:
            store.append(entry)
        except Exception as write_exc:  # noqa: BLE001 - 只报缺口，不掩盖原失败
            return {"ok": False, "code": CODE_INVERSE_FAILED,
                    "reason": f"failed 终态条目写入失败：{type(write_exc).__name__}: {write_exc}",
                    "path": str(self.paths.rollback_journal),
                    "journal_id": journal_entry_id,
                    "hint": "failed 条目写不进 = 副作用有无无法取证；数据目录修好前不要重试回滚"}
        return {"ok": True, "journal_id": journal_entry_id, "phase": PHASE_FAILED,
                "seq": entry.get("seq")}

    def _record_terminal_fallback(self, journal_entry: Mapping[str, Any], *,
                                  cause: BaseException) -> dict[str, Any]:
        """终态条目写不进主日志时，把同一份事实落到旁路 WAL（相位仍标 pending）。

        目的只有一个：让 :meth:`unresolved_pending` 之后能把它当"结果未知"摆出来，
        而不是让副作用消失在取证盲区里。系统绝不自动重建日志、绝不改写任何历史行。
        """
        row = dict(journal_entry)
        row["phase"] = PHASE_PENDING
        row["journal_id"] = str(journal_entry.get("id") or "")
        row["id"] = new_id("rbjp")
        row["terminal_write_failed"] = {
            "code": CODE_INVERSE_FAILED,
            "reason": f"{type(cause).__name__}: {cause}",
            "path": str(self.paths.rollback_journal),
        }
        try:
            self._journal_pending().append(row)
        except Exception as exc:  # noqa: BLE001 - 只报缺口
            return {"ok": False, "reason": f"pending 兜底也写不进：{type(exc).__name__}: {exc}"}
        return {"ok": True, "path": str(self.journal_pending_path),
                "pending_id": row["id"], "journal_id": row["journal_id"]}

    def _apply_inverse(self, target: Mapping, inverse: Mapping[str, Any], *,
                       actor: str, reason: str) -> dict[str, Any]:
        """执行逆向动作：注入的执行器优先，否则如实返回本地模拟回执。

        **回执契约（R1-4 / G-14）**：执行器返回的 ``receipt_state`` 必须在
        :data:`RECEIPT_STATES` 白名单里，越界 → :data:`CODE_INVERSE_FAILED`（绝不落终态）；
        自报 ``failed`` 同样按失败抛出。``note`` 与状态不一致（真实执行却写着"本地模拟"，
        或模拟回执冒充真实执行）同样拒收——文案是界面唯一依据，撒谎一次就再也收不回来。
        """
        tool = _tool_name(target)
        applier = self.applier or _applier_for(tool)
        if applier is None:
            return {"op": inverse["op"], "applied": True,
                    "receipt_state": SIMULATED_RECEIPT_STATE, "note": SIMULATED_NOTE}
        try:
            result = applier(target, dict(inverse), actor=actor, reason=reason)
        except RollbackError:
            raise
        except Exception as exc:  # 执行器自己的异常 → 统一错误码
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"逆向动作 {inverse['op']} 执行失败：{exc}",
                detail={"action_id": target.get("id"), "op": inverse["op"]},
                hint="执行器应幂等；失败时终态日志落 failed 条目，用户不会看到『已回滚』",
            ) from exc
        receipt: dict[str, Any] = {"op": inverse["op"], "applied": True}
        if isinstance(result, Mapping):
            receipt.update(dict(result))
        receipt.setdefault("op", inverse["op"])
        declared = _safe_text(receipt.get("receipt_state")).lower()
        if not declared or declared == SIMULATED_RECEIPT_STATE:
            # 没自报状态 = 本地模拟；缺省文案必须自己说清是模拟（不许留空当真实执行）
            receipt["receipt_state"] = SIMULATED_RECEIPT_STATE
            receipt.setdefault("note", SIMULATED_NOTE)
        # 真实执行（executed_*）：绝不塞模拟缺省文案，note 由契约校验按状态补齐
        return self._validate_receipt(receipt, op=inverse["op"], target=target)

    def _validate_receipt(self, receipt: dict[str, Any], *, op: str,
                          target: Mapping) -> dict[str, Any]:
        """按 :data:`RECEIPT_STATES` 白名单与 ``note`` 一致性校验回执（G-14）。"""
        state = _safe_text(receipt.get("receipt_state")).lower()
        where = {"action_id": target.get("id"), "op": op}
        if state not in RECEIPT_STATES:
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"回执 receipt_state={state!r} 不在白名单里，逆向结果不可信",
                field="receipt_state",
                detail={**where, "received": state, "allowed": sorted(RECEIPT_STATES)},
                hint="注入执行器只能回报 "
                     "simulated_local / executed_sandbox / executed_registry / failed；"
                     "白名单外的状态一律按失败处理，绝不落成功终态",
            )
        note = _safe_text(receipt.get("note"))
        lowered = note.lower()
        if state == FAILED_RECEIPT_STATE:
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"逆向动作 {op} 的执行器自报失败：{note or '（执行器未给出原因）'}",
                field="note",
                detail={**where, "receipt_state": state,
                        "receipt": {key: value for key, value in receipt.items()}},
                hint="执行器自报失败 → 撤销日志落 failed 条目，目标仍可再次回滚",
            )
        if state == SIMULATED_RECEIPT_STATE:
            if not note or not any(marker in lowered for marker in SIMULATION_NOTE_MARKERS):
                # 模拟回执必须自己说清是模拟：缺文案就按缺省文案兜，绝不让它冒充真实执行。
                receipt["note"] = SIMULATED_NOTE
            return receipt
        # 真实执行（沙箱试点 / 注册表执行器）：不得再带"本地模拟"这类文案
        hit = [marker for marker in SIMULATION_NOTE_MARKERS if marker in lowered]
        if hit:
            raise RollbackError(
                CODE_INVERSE_FAILED,
                f"回执状态是真实执行 {state}，note 却带着模拟文案（{'、'.join(hit)}）",
                field="note",
                detail={**where, "receipt_state": state, "note": note,
                        "forbidden_markers": sorted(SIMULATION_NOTE_MARKERS)},
                hint="真实执行与模拟回执是两件事：把模拟文案贴在真实执行上，"
                     "界面就会谎称『外部系统未调用』；请把 note 改成如实描述",
            )
        if not note:
            receipt["note"] = (SANDBOX_PILOT_NOTE if state == SANDBOX_RECEIPT_STATE
                               else REGISTRY_EXECUTED_NOTE)
        return receipt

    def _build_rollback_record(self, target: Mapping, *, action_id: str, actor: str,
                               reason: str, receipt: Mapping[str, Any],
                               affected: Sequence[str], lang: str) -> dict[str, Any]:
        language = "en" if str(lang).lower().startswith("en") else "zh"
        # D-risk（G-28，PM-VERDICT-R1-2b §2 + docs/07 §11.8 跨任务事项）：补偿记录
        # **继承目标记录的 ``risk_level``**——高危动作的撤销本身就是高危动作，
        # "回滚一律 medium"等于让高危补偿绕过 ``RISK_POLICY`` 的四眼闸门
        # （high 1 人、critical 2 人 + 盖章）。继承值越集/缺失回落 ``"medium"``
        # （旧行为）；``execution_receipt``/审批路径一字不动：签完照签
        # （``allow_unapproved=True``），审批缺口由 ``verify_record`` 如实报
        # ``SECRETARIAT_APPROVAL_REQUIRED``——签名有效、链连续，这是纪律不是缺陷。
        target_risk = str(target.get("risk_level") or "").strip().lower()
        risk_level = target_risk if target_risk in INHERITED_RISK_LEVELS else "medium"
        generated_at = utc_stamp()
        tool = _tool_name(target) or str(target.get("action_type") or "action")
        op = str(receipt.get("op") or "rollback")
        label = _clip(tool, 28)
        dependents = max(0, len(affected) - 1)
        # R1-4（G-14 · note 与 receipt_state 一致性）：文案跟着**真实回执状态**走。
        # 沙箱试点/注册表执行器的补偿记录绝不再写"本地模拟"——真实 I/O 发生过，
        # 把它写成模拟同样是说谎（反向的说谎：说外部系统已撤销，由 §10 禁令拦）。
        # 缺省（simulated_local）文案逐字保持 R1-3 原样，存量断言与界面零变化。
        state = _safe_text(receipt.get("receipt_state")).lower()
        if state not in RECEIPT_STATES:
            state = SIMULATED_RECEIPT_STATE
        note = _safe_text(receipt.get("note"))
        real = state in (SANDBOX_RECEIPT_STATE, REGISTRY_RECEIPT_STATE)
        pilot = state == SANDBOX_RECEIPT_STATE
        if language == "en":
            if real:
                mode_tag = "sandbox pilot" if pilot else "real executor"
                l1 = _clip(f"Rolled back: {label} ({mode_tag})", 80)
                l2 = [f"Inverse op: {op}",
                      f"{dependents} dependent step(s) included in the rollback scope",
                      _clip(f"{state}: {note or 'no note from the executor'}", 80)]
                l3 = (f"{tool} · op={op} · affected={len(affected)} · "
                      f"{state} · execution_receipt=unreported（no external receipt）")
                result_summary = (f"Rollback recorded for {action_id}; inverse {op} ran "
                                  f"under {mode_tag} ({state}); no external receipt")
            else:
                l1 = f"Rolled back: {label} (local simulation)"
                l2 = [f"Inverse op: {op}",
                      f"{dependents} dependent step(s) included in the rollback scope",
                      "External systems were not called; receipt is simulated_local"]
                l3 = (f"{tool} · op={op} · affected={len(affected)} · "
                      "execution_receipt=unreported（no external receipt）")
                result_summary = (f"Rollback recorded for {action_id}; local inverse "
                                  f"{op} simulated (external systems not called)")
            intent = f"Roll back the effect of action {action_id}"
            rationale = reason or "The user asked to undo this action; the original step depended on it."
            reason_text = (f"Rollback requested for action {action_id}; see undo journal for the "
                           "pre-state snapshot.")
        else:
            if real:
                mode_tag = "沙箱试点：真实执行" if pilot else "真实执行"
                l1 = _clip(f"已回滚：{label}（{mode_tag}）", 80)
                l2 = [f"逆向动作：{op}",
                      f"纳入回滚范围的后续依赖 {dependents} 个",
                      _clip(f"{state}：{note or '执行器未给出文案'}", 80)]
                l3 = (f"{tool} · 逆向动作={op} · 影响 {len(affected)} 条 · {state} · "
                      "执行端未回执（unreported）")
                result_summary = (f"已记录对 {action_id} 的回滚；逆向动作 {op} 以"
                                  f"{mode_tag}（{state}）执行；外部系统未回执")
            else:
                l1 = f"已回滚：{label}（本地模拟）"
                l2 = [f"逆向动作：{op}",
                      f"纳入回滚范围的后续依赖 {dependents} 个",
                      "未调用外部系统，回执状态 simulated_local"]
                l3 = (f"{tool} · 逆向动作={op} · 影响 {len(affected)} 条 · "
                      "执行端未回执（unreported）")
                result_summary = (f"已记录对 {action_id} 的回滚；本地模拟执行 {op}，"
                                  "外部系统未实际调用")
            intent = f"回滚动作 {action_id} 的影响"
            rationale = reason or "用户要求撤销该动作；它影响的后续步骤需要一并回滚。"
            reason_text = f"对动作 {action_id} 执行回滚；回滚前快照见撤销日志。"
        if real:
            # 沙箱试点必须逐字带「沙箱试点」字样（界面唯一依据），且不出现"已恢复"。
            labels = (["rollback", "executed-sandbox", "sandbox-pilot"] if pilot
                      else ["rollback", "executed-registry"])
            spoken = note or ("the executor returned no note" if language == "en"
                              else "执行器未给出文案")
            if language == "en":
                receipt_detail = _clip(
                    (f"Sandbox pilot: inverse {op} really wrote inside the whitelisted "
                     f"sandbox root ({state}); {spoken}" if pilot else
                     f"Registry executor really ran inverse {op} ({state}); {spoken}")
                    + "; execution_receipt.state=unreported (no external receipt)", 480)
            else:
                receipt_detail = _clip(
                    (f"沙箱试点：逆向动作 {op} 已在白名单沙箱根内真实写入（{state}）；{spoken}"
                     if pilot else
                     f"注册表级执行器已真实执行逆向动作 {op}（{state}）；{spoken}")
                    + "；execution_receipt.state=unreported（外部系统未回执）", 480)
        else:
            labels = ["rollback", "simulated-local"]
            receipt_detail = ("本地逆向动作已记录（simulated_local）；外部系统未实际调用；"
                              "execution_receipt.state=unreported" if language == "zh" else
                              "Local inverse recorded (simulated_local); external systems "
                              "not called; execution_receipt.state=unreported")

        source = target.get("source") if isinstance(target.get("source"), Mapping) else {}
        demo = bool(target.get("demo_data"))
        synthetic = bool(target.get("synthetic"))
        source_kind = "demo" if demo else "manual"
        # D-risk 的联动面（schema `action_record.schema.json` 对 risk 口径的原文：
        # "high 与 critical 必须给出 impact_scope 且 policy.requires_human_confirmation
        # =true"）：继承出高危/严重风险的补偿记录，确认政策如实跟着置 True——
        # 高危回滚同样要停一次人工确认（W-02 同口径）。medium/low 保持 False，
        # 与旧行为逐字一致；``execution_receipt``/审批路径不在此块，一字未动。
        policy = {
            "requires_human_confirmation": risk_level in REQUIRES_CONFIRMATION_RISKS,
            "auto_approve_forbidden": True,
            "timeout_behavior": "keep_pending",
            "queue_serial": False,
            "min_role_to_decide": "local",
        }
        human_readable: dict[str, Any] = {
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "language": language,
            "generated_by": "template",
            "template_id": "rollback.hr.v1",
            "model": None,
            "generated_at": generated_at,
            "may_contain_inference": False,
        }
        facts = {
            "action_id": action_id,
            "op": op,
            "affected": list(affected),
            "status": "succeeded",
            "language": language,
            # R1-4（G-14）：文案跟着真实回执状态走 → 回执状态进事实哈希，
            # 让"模拟/沙箱试点/真实执行"三种文案各自绑到自己的事实上。
            # 这是**新记录自己的**哈希，不改写任何历史行（§0.4 无例外）。
            "receipt_state": state,
        }
        human_readable["source_facts_hash"] = "sha256:" + hash_object(facts)
        rollback_id = new_id("act-rb")
        record: dict[str, Any] = {
            "schema_version": "1.1",
            "id": rollback_id,
            "timestamp": generated_at,
            "received_at": generated_at,
            "sequence_no": None,
            "agent_id": _safe_id(target.get("agent_id"), fallback="tracelens-rollback"),
            "agent_name": target.get("agent_name"),
            "actor_id": _safe_id(target.get("actor_id"), fallback="local"),
            "workspace_id": _safe_id(target.get("workspace_id"), fallback="default"),
            "session_id": _safe_id(target.get("session_id"), fallback="local-session"),
            "run_id": target.get("run_id"),
            "parent_action_id": action_id,
            "depth": (int(target.get("depth") or 0) + 1),
            "action_type": "rollback",
            "tool_used": {
                "name": op,
                "version": ROLLBACK_VERSION,
                "provider": "local",
                "call_id": None,
                "params_summary": _clip(f"rollback {action_id}", 500),
                "result_ref": None,
            },
            "intent": intent,
            "rationale": rationale,
            "result_summary": result_summary,
            "human_readable": human_readable,
            "confidence": 1.0,
            "confidence_source": {
                "method": "template",
                "logprob": None,
                "tool_success_rate": None,
                "samples": 1,
                "computed_at": generated_at,
            },
            "uncertainty": None,
            "cost": None,
            "latency_ms": 0,
            "status": "succeeded",
            "requires_action": False,
            "requires_action_reason": None,
            "action_due_at": None,
            "intervention_options": [],
            "user_intervention": {
                "decision": "approve",
                "option_id": None,
                "decided_by": _safe_id(actor),
                "decided_by_role": "local",
                "decided_at": generated_at,
                "reason": reason_text,
                "recorded": True,
            },
            "execution_receipt": {
                # 外部系统没有回执：如实写 unreported（铁律 2，R1-4 不变）。
                # R1-4（G-14）：detail 文案跟着**真实回执状态**走——沙箱试点/注册表
                # 执行器绝不再写"本地模拟"；缺省模拟路径的文案与 R1-3 逐字一致。
                "state": "unreported",
                "reported_by": None,
                "reported_at": None,
                "detail": receipt_detail,
                "sdk_version": None,
                "effect_notice": "intervention.effect.recorded_only",
            },
            "risk_level": risk_level,
            "impact_scope": {
                "targets": [{"kind": "record", "ref": action_id,
                             "change": "update", "count": len(affected)}],
                "money": None,
                "external_endpoint": None,
                "data_categories": ["none"],
                "reversible": True,
                "summary": (f"撤销动作 {action_id} 及其 {max(0, len(affected) - 1)} 个后续步骤"
                            if language == "zh" else
                            f"Undo action {action_id} and {max(0, len(affected) - 1)} dependent step(s)"),
            },
            "policy": policy,
            "error": None,
            "suggested_next": [],
            "context": {
                "task_name": (target.get("context") or {}).get("task_name")
                if isinstance(target.get("context"), Mapping) else None,
                "audience_profile": "C",
                "default_granularity": "L1",
                "user_language": language,
            },
            "trace_link": target.get("trace_link") if isinstance(target.get("trace_link"), Mapping) else None,
            "source": {
                "kind": source_kind,
                "adapter": source.get("adapter") if demo and isinstance(source, Mapping) else "tracelens-rollback",
                "format_version": ROLLBACK_VERSION,
                "source_uri": source.get("source_uri") if demo and isinstance(source, Mapping) else None,
                "ingested_at": None,
                "raw_ref": None,
            },
            "demo_data": demo,
            "synthetic": synthetic,
            # R1-4（G-14）：标签跟着真实回执状态走（缺省仍是 simulated-local）。
            "labels": labels,
            "rollback_ref": action_id,
        }
        return record


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _self_check_root() -> Iterator[pathlib.Path]:
    """自检用的可写根目录：系统临时目录优先，写不动就按 ``tests/scratch.py`` 的口径回落。

    为什么要回落：Windows 上 ``tempfile.mkdtemp`` 会给新目录套一层私有 ACL，
    受限令牌（沙箱）下再往里建子目录就是 ``WinError 5 拒绝访问``——这是环境限制，
    不是回滚逻辑的缺陷（``tests/scratch.py`` 遇到同一堵墙时回落到仓库 ``var/``）。
    自检宁可回落并如实打印原因，也绝不把"跑不起来"说成"通过"。
    """
    import os
    import shutil
    import tempfile

    temp = pathlib.Path(tempfile.mkdtemp())
    usable = True
    try:
        (temp / "probe").mkdir()
    except OSError as exc:
        usable = False
        print(f"system temp not usable under this sandbox "
              f"({type(exc).__name__}: {exc}); falling back to repo var/ "
              f"— same rule as tests/scratch.py")
    finally:
        shutil.rmtree(temp, ignore_errors=True)   # 清理尽力而为：受限 ACL 下清不掉也不炸
    if usable:
        run_root = pathlib.Path(tempfile.mkdtemp())
        try:
            yield run_root
        finally:
            shutil.rmtree(run_root, ignore_errors=True)
        return

    repo = pathlib.Path(__file__).resolve().parents[1]
    root = repo / "var" / f"rollback-selfcheck-{os.getpid()}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.rollback``：临时目录跑一遍计划 + 执行 + 覆盖率。"""
    import json

    repo = pathlib.Path(__file__).resolve().parents[1]
    sample = repo / "samples" / "action_records.json"
    with _self_check_root() as temp:
        paths = FeaturePaths(temp)
        records: list[dict[str, Any]] = []
        if sample.exists():
            records = json.loads(sample.read_text(encoding="utf-8")).get("records", [])
        svc = RollbackService(paths)
        cov = svc.coverage(records)
        print(f"coverage: total={cov['total']} reversible={cov['reversible']} "
              f"ratio={cov['ratio']}")
        if records:
            target = "act-demo-0007"
            info = plan(records, target)
            print(f"plan({target}): ok={info['ok']} dependents={info['dependents']} "
                  f"order={info['order']} reversible={info['reversible']}")
            out = svc.execute(records, target, actor="local", reason="self-check")
            rb = out["rollback_record"]
            print(f"rollback: id={rb['id']} action_type={rb['action_type']} "
                  f"ref={rb['rollback_ref']} status={rb['status']} "
                  f"receipt={rb['execution_receipt']['state']} "
                  f"inverse={out['inverse']['receipt_state']}")
            print(f"journal entries: {len(svc.journal())}")
            replayed = RollbackService(paths)
            print(f"journal reload: {len(replayed.journal())}")
            # —— R1-4 活体自证 ①：链化复算（G-13 收口；历史行按缺省容忍）——
            chain = svc.journal_chain_verify()
            print(f"journal chain verify: ok={chain['ok']} count={chain['count']} "
                  f"chained={chain['chained']} legacy_tolerated={chain['legacy_tolerated']} "
                  f"head={chain['head'][:12]}... problems={chain['problems']}")
            if not chain["ok"]:  # pragma: no cover
                print("ERROR: chained undo journal does not verify (G-13)")
                return 1
            entry = svc.journal()[-1]
            print(f"journal row: seq={entry.get('seq')} "
                  f"prev_hash={str(entry.get('prev_hash') or '')[:12]}... "
                  f"entry_hash={str(entry.get('entry_hash') or '')[:12]}... "
                  f"phase={entry.get('phase')} pending_id={entry.get('pending_id')}")
            print(f"closure snapshots (TP-06): "
                  f"{[(row['id'], str(row['snapshot_sha256'])[:8]) for row in entry['snapshots']]}")
            if not entry.get("snapshots"):  # pragma: no cover
                print("ERROR: journal entry carries no closure snapshot set (TP-06)")
                return 1
            print(f"pending WAL: rows={len(svc.journal_pending())} "
                  f"unresolved={len(svc.unresolved_pending())} "
                  f"chain_ok={svc.journal_pending_chain_verify()['ok']}")
            # 口径 α：execute 返回的副本必须逐字段等于原记录（零改写）
            by_id = {row["id"]: row for row in records if isinstance(row, dict)}
            rewritten = [row["id"] for row in out["records"]
                         if row != by_id.get(row["id"])]
            print(f"zero-rewrite copies (α): rewritten={rewritten or 'none'}")
            if rewritten:  # pragma: no cover
                print("ERROR: execute rewrote the original records")
                return 1
            # G-6：计划层与执行层同一条幂等屏障（撤销日志在案 → 两侧都报 ALREADY）
            barrier = replayed.plan(records, target)
            print(f"plan barrier after journal: ok={barrier['ok']} "
                  f"codes={[b['code'] for b in barrier['blockers']]}")
            if barrier["ok"]:  # pragma: no cover
                print("ERROR: plan() does not see the undo journal (G-6)")
                return 1
            try:
                svc.execute(records, target, actor="local")
            except RollbackError as exc:
                print(f"duplicate rollback rejected: {exc.code}")
            else:  # pragma: no cover
                print("ERROR: duplicate rollback was accepted")
                return 1

            # —— R1-4 活体自证 ②：两阶段失败路径（G-15）——
            victim = "act-demo-0003"

            def _boom(record, inverse, *, actor, reason):  # 故意失败的执行器
                raise RuntimeError("self-check: 逆向执行器故意失败")

            failing = RollbackService(paths, applier=_boom)
            try:
                failing.execute(records, victim, actor="local",
                                reason="self-check 两阶段失败路径")
            except RollbackError as exc:
                print(f"two-phase failure path: raised={exc.code}")
            else:  # pragma: no cover
                print("ERROR: a failing inverse applier was accepted")
                return 1
            last = svc.journal()[-1]
            print(f"failed terminal row: phase={last.get('phase')} "
                  f"code={(last.get('failure') or {}).get('code')} "
                  f"receipt_state={(last.get('result') or {}).get('receipt_state')} "
                  f"rollback_record_id={last.get('rollback_record_id')}")
            if last.get("phase") != PHASE_FAILED:  # pragma: no cover
                print("ERROR: the failure left no failed terminal entry (G-15)")
                return 1
            retry = failing.plan(records, victim)
            retry_codes = sorted({item["code"] for item in retry["blockers"]})
            print(f"failed rollback keeps the target retryable: codes={retry_codes or '[]'}")
            if BLOCKER_ALREADY_ROLLED_BACK in retry_codes:  # pragma: no cover
                print("ERROR: the failed entry poisoned the idempotency barrier (G-15)")
                return 1
            print(f"unresolved pending after failure: {len(svc.unresolved_pending())}")

            # —— R1-4 活体自证 ③：回执白名单（G-14）——
            def _liar(record, inverse, *, actor, reason):
                return {"receipt_state": "executed_external_system",
                        "note": "外部系统已撤销（越界状态 + 谎称已恢复）"}

            liar = RollbackService(paths, applier=_liar)
            try:
                liar.execute(records, "act-demo-0006", actor="local")
            except RollbackError as exc:
                print(f"receipt whitelist rejects an out-of-contract state: "
                      f"{exc.code} field={exc.field}")
            else:  # pragma: no cover
                print("ERROR: a receipt_state outside the whitelist was accepted (G-14)")
                return 1
            if svc.journal()[-1].get("phase") != PHASE_FAILED:  # pragma: no cover
                print("ERROR: the rejected receipt left no failed entry (G-14/G-15)")
                return 1

            # —— R1-5 活体自证 ④：注册表补登 + 显式不可逆 + 覆盖率明细（G-17/18/19 · C-1）——
            for probe in ("deepseek-chat", "text-embedding-demo", "book_flight",
                          "payment.transfer", "contract.sign", "refund.payment"):
                entry = inverse_entry_of(probe) or {}
                probe_row = {"id": f"probe-{probe}", "action_type": "tool_call",
                             "status": "succeeded", "tool_used": {"name": probe}}
                verdict = reversible(probe_row)
                print(f"registry probe {probe}: op={entry.get('op')} "
                      f"source={entry.get('source')} "
                      f"reversible={verdict['reversible']} "
                      f"bucket={_coverage_bucket(probe_row)} "
                      f"reason={_clip(verdict['reason'], 26)}")
            try:
                register_inverse("self-check.irreversible", op="escalate_x",
                                 describe_key="rollback.inverse.x", reversible=False)
            except RollbackError as exc:
                print(f"irreversible registration without a blocking note is refused: "
                      f"{exc.code} field={exc.field}")
            else:  # pragma: no cover
                print("ERROR: an irreversible entry without a note was accepted (R1-5)")
                return 1
            if cov.get("uncovered") is None or "buckets" not in cov:  # pragma: no cover
                print("ERROR: coverage() lost the C-1 detail keys (uncovered/buckets)")
                return 1
            side = cov["buckets"]["side_effect_subset"]
            print(f"coverage detail (C-1): keys={sorted(cov)} "
                  f"uncovered={len(cov['uncovered'])} "
                  f"side_effect_subset={side['covered']}/{side['total']}={side['ratio']}")
            print(f"coverage three legacy keys (兼容): total={cov['total']} "
                  f"reversible={cov['reversible']} ratio={cov['ratio']}")
        print("rollback self-check ok")
        return 0


if __name__ == "__main__":  # pragma: no cover - 人工自检
    raise SystemExit(_self_check())
