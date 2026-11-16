"""合规导出（规格书功能八，docs/07 §4.12 / §3.3 冻结契约）——**诚实版**。

0.6.0 的边界（先说清楚，免得绿灯骗人）：

* **不接真实锚定服务**。TTS/HSM/IPFS pinning/以太坊都是外部服务与合规决策，
  本模块没有它们的客户端。``ExportRecord.eth_tx_hash`` 恒为 ``None``，
  ``anchor_state`` 恒为 ``"unavailable"``；``POST …/attest``（T36）永远 503
  ``COMPLIANCE_ANCHOR_UNAVAILABLE``——CID 是离线可复算的真值，"锚"没有发生
  就是不许说发生了。``anchors.jsonl`` 在 0.6.0 不会被写入（真锚定属于 0.7.0）。
* **导出前强制双验，断链即拒绝（B8-4 前半，TASK-R8-1 §2.1）**：``export()`` 在**建包
  之前**跑两条链的只读复算——**审计链**（服务端注入的 ``audit_verifier``，
  包的就是既有的 ``audit.verify()`` / ``verify_audit_chain()``）与**本模块的导出账本链**
  （``self._ledger.verify()``，与 T37 报的 ``ledger_ok`` 同一份代码）。任一条不合格
  → 抛 ``COMPLIANCE_CHAIN_BROKEN`` + 中文人话 + 断点（第几条 / 原因 / 链头），
  **零文件产出、零导出记录落账、零成功留痕**；``chain_ok=True`` 也绕不过注入的验链。
  校验本身只复算哈希，不写文件、不改历史、不修链（追加式纪律）。
* **bundle 是离线可复算的证据包**：records / audit / letters 三段各自的规范
  哈希归约成 ``root_hash``；文件字节落盘、``sha256`` 进账本，``verify``（T37）
  逐字节复算。``verify`` 的口径不变：**只报告不阻断**（取证工具要用它看断在哪）。
* ``cid_for`` 是真 CIDv1（raw, sha2-256, base32），纯标准库离线可算——
  它证明"这份字节"是什么，不证明"谁在链上见过它"。两者不许混。

错误码是契约：``COMPLIANCE_FORMAT_INVALID`` / ``COMPLIANCE_CHAIN_BROKEN`` /
``COMPLIANCE_BUNDLE_EMPTY`` / ``COMPLIANCE_NOT_FOUND`` / ``COMPLIANCE_EXPORT_FAILED``
/ ``COMPLIANCE_ANCHOR_UNAVAILABLE``。
"""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import hashlib
import importlib
import io
import json
import pathlib
import shutil
import sys
import threading
from collections.abc import Callable, Mapping

from .feature_store import FeaturePaths, Ledger, canonical_json, new_id, open_ledger, open_store, utc_stamp

__all__ = [
    "COMPLIANCE_VERSION", "FORMATS", "ANCHOR_STATE", "ComplianceError",
    "GATE_SIDES", "cid_for", "ComplianceService", "default_service",
]

COMPLIANCE_VERSION = "1.0"
FORMATS: tuple[str, ...] = ("pdf", "csv", "chain")
#: 0.6.0 唯一诚实的锚定状态；0.7.0 引入真实 Provider 后才有别的取值
ANCHOR_STATE = "unavailable"

# --------------------------------------------------------------------------- #
# 导出前强制闸门（B8-4 前半 / TASK-R8-1 §2.1）：两条链的名字、断点措辞、净效果
# --------------------------------------------------------------------------- #
#: 闸门校验的两条链（顺序即 reason 里的叙述顺序）。
GATE_SIDES: tuple[str, ...] = ("audit", "compliance_ledger")
#: 链的中文名字（进 reason，不进机器码）。
GATE_SIDE_TEXT: dict[str, str] = {"audit": "审计链", "compliance_ledger": "合规导出账本链"}
#: 断点机器码 → 中文小句（逐字取自 ``Ledger.verify`` / ``AuditLogFile.verify`` 的码）。
GATE_PROBLEM_TEXT: dict[str, str] = {
    "ledger_seq_gap": "序号断裂",
    "ledger_prev_mismatch": "前驱哈希不接",
    "ledger_hash_mismatch": "条目哈希复算不符",
    "audit_chain_broken": "审计链自检未通过",
    "audit_verify_error": "审计链复算没跑成",
}
#: 断点预览上限：要说清"第几条、为什么"，但不把整份日志塞进响应。
GATE_PROBLEM_PREVIEW = 5
#: 断链时导出侧的净效果恒为这两个 0（进 detail，机器可查「真拒绝、不是只标记」）。
GATE_REFUSAL_EFFECT: dict[str, int] = {"files_written": 0, "ledger_rows_appended": 0}
#: 审计链的 ``index`` 是 0 基（``audit_broken_reason`` 印 ``index + 1``），
#: 账本链的 ``index`` 是 1 基（``Ledger.verify`` 从 1 起）——两句人话得各自数对。
GATE_INDEX_OFFSET: dict[str, int] = {"audit": 1, "compliance_ledger": 0}


def _gate_problem_text(side: str, problem: Mapping) -> str:
    """一条断点的中文小句：什么坏了 + 第几条 + 复算给的原因。"""
    code = str(problem.get("code") or "")
    label = GATE_PROBLEM_TEXT.get(code) or code or "链自检未通过"
    index = problem.get("index")
    text = label
    if isinstance(index, int):
        text += f"（第 {index + GATE_INDEX_OFFSET.get(side, 0)} 条）"
    reason = str(problem.get("reason") or "").strip()
    return f"{text}：{reason}" if reason else text


def _gate_problem_detail(side: str, problem: Mapping) -> dict:
    """一条断点的机器可读形状：链名 + 码 + 位置 + 期望/实测（全是哈希，零原文参数）。"""
    out: dict = {"side": side, "code": str(problem.get("code") or ""),
                 "index": problem.get("index")}
    for key in ("expected", "found"):
        if key in problem:
            out[key] = problem.get(key)
    reason = str(problem.get("reason") or "")
    if reason:
        out["reason"] = reason[:200]
    return out


def gate_reason(gate: Mapping) -> str:
    """闸门失败的一句中文人话：哪条链断、断在第几条、后果、下一步。

    诚实优先（口径对齐 ``server.audit_broken_reason``）：说清"拒绝导出 = 一个字都没写"，
    并明确服务端**不会**自动清空、截断或重建任何账本 —— 追加式账本没有"修历史"这条路。
    """
    parts = []
    for side in GATE_SIDES:
        verdict = gate.get(side) or {}
        if verdict.get("ok"):
            continue
        problems = list(verdict.get("problems") or [])
        first = _gate_problem_text(side, problems[0]) if problems else "链自检未通过（无明细）"
        parts.append(f"{GATE_SIDE_TEXT[side]}不可用：{first}（共 {len(problems) or 1} 处）")
    return ("导出前强制校验未通过：" + "；".join(parts) +
            "。已拒绝导出：零证据文件产出、零导出记录落账、零成功留痕。"
            "服务端不会自动清空、截断或重建任何账本（追加式纪律）；"
            "请先取证复算链头（agent_explainer.audit.verify_file / feature_store.Ledger.verify），"
            "按追加式纪律处理完再重试导出。")


def gate_detail(gate: Mapping) -> dict:
    """闸门失败的 ``detail``：断在哪条链 + 断点清单（预览）+ 净效果两个 0。

    脱敏纪律（docs/03 §13）：只给机器码、位置、链头哈希与期望/实测摘要，
    **不给**参数原文、理由原文、密钥，也不给文件路径。
    """
    broken = [side for side in GATE_SIDES if not (gate.get(side) or {}).get("ok")]
    problems: list[dict] = []
    for side in broken:
        problems.extend(_gate_problem_detail(side, item)
                        for item in list((gate[side].get("problems") or []))[:GATE_PROBLEM_PREVIEW])
    return {
        "side": broken[0] if broken else None,
        "broken_sides": list(broken),
        "sides": {side: {"ok": bool((gate[side] or {}).get("ok")),
                         "source": (gate[side] or {}).get("source"),
                         "count": (gate[side] or {}).get("count"),
                         "head": str((gate[side] or {}).get("head") or ""),
                         "problem_count": len((gate[side] or {}).get("problems") or [])}
                  for side in GATE_SIDES},
        "problems": problems,
        "problem_count": len(problems),
        "chain_ok": bool((gate.get("audit") or {}).get("ok")),
        **GATE_REFUSAL_EFFECT,
        "reason": gate_reason(gate),
        "hint": ("append-only: while a chain is broken no evidence file is produced and "
                 "no ledger row is appended; the server never clears, truncates or rebuilds"),
    }


class ComplianceError(Exception):
    """合规域异常：``code`` 对齐 docs/07 §7，HTTP 层照表翻译。"""

    def __init__(self, code: str, reason: str, *, field: str | None = None,
                 hint: str | None = None, detail: dict | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.field = field
        self.hint = hint
        self.detail = dict(detail or {})
        self.http_status = http_status

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.reason}
        if self.field:
            out["field"] = self.field
        if self.detail:
            out["detail"] = self.detail
        if self.hint:
            out["hint"] = self.hint
        return out

    def render(self) -> str:
        parts = [f"{self.code}: {self.reason}"]
        if self.field:
            parts.append(f"field={self.field}")
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return "；".join(parts)


def cid_for(data: bytes) -> str:
    """IPFS CIDv1（raw 0x55, sha2-256 0x12/0x20, base32 小写无填充）——离线复算。

    为什么是真 CID 而不是"长得像 CID 的哈希"：拿到这份导出的人可以在任何
    CID 计算器里粘进去验（``b<base32>``），对得上就是这份字节，对不上就是
    被换过——证明力来自可复算，不来自我们自称。
    """
    digest = hashlib.sha256(data).digest()
    prefix = b"\x01\x55\x12\x20"                     # version=1, codec=raw, sha2-256, len=32
    encoded = base64.b32encode(prefix + digest).decode("ascii").rstrip("=").lower()
    return "b" + encoded


def _section_hash(items) -> str:
    """一段内容的规范哈希：逐条 canonical_json 行拼起来再 sha256（顺序即内容）。"""
    payload = "\n".join(canonical_json(item) for item in (items or []))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _records_csv(records) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    columns = ["id", "timestamp", "agent_id", "action_type", "status", "risk_level",
               "tool", "decision", "decided_by"]
    writer.writerow(columns)
    for record in (records or []):
        if not isinstance(record, dict):
            continue
        view = record.get("user_intervention") if isinstance(record.get("user_intervention"), dict) else {}
        writer.writerow([
            record.get("id"), record.get("timestamp"), record.get("agent_id"),
            record.get("action_type"), record.get("status"), record.get("risk_level"),
            (record.get("tool_used") or {}).get("name") if isinstance(record.get("tool_used"), dict) else "",
            view.get("decision"), view.get("decided_by"),
        ])
    return out.getvalue().encode("utf-8")


def _chain_jsonl(meta: dict, sections: dict) -> bytes:
    lines = [{"section": "meta", "content": meta}]
    for name in ("records", "audit", "letters"):
        lines.append({"section": name, "hash": _section_hash(sections.get(name)),
                      "count": len(sections.get(name) or [])})
    lines.append({"section": "entries", "content": {k: list(v or []) for k, v in sections.items()}})
    return ("\n".join(json.dumps(line, ensure_ascii=False, sort_keys=True)
                      for line in lines) + "\n").encode("utf-8")


def _now() -> str:
    return utc_stamp()


class ComplianceService:
    """导出账本（``exports.jsonl`` Ledger）+ 证据包文件的唯一写入者。

    线程安全：Ledger 追加与文件写入持 ``RLock``；账本本身是哈希链
    （``verify_events`` 同思路，只报告不重建）。
    """

    def __init__(self, paths: FeaturePaths, *, export_dir=None, now=None,
                 audit_verifier: Callable[[], Mapping] | None = None) -> None:
        if not isinstance(paths, FeaturePaths):
            raise TypeError("ComplianceService 需要 FeaturePaths（见 feature_store.py）")
        self.paths = paths
        self._now = now or _now
        self._lock = threading.RLock()
        #: 证据包落盘目录：0.6.0 放 features 根下 ``exports/``（§3.3 只定义了账本
        #: 路径；文件本体与审计产物同级放，拷目录即整包迁移）。
        self.export_dir = pathlib.Path(export_dir) if export_dir else paths.root / "exports"
        self._ledger: Ledger = open_ledger(paths.exports)
        #: 审计链复算的**注入钩子**（TASK-R8-1 §2.1「二选一」的取法：强制点落在服务层）。
        #: 服务端把既有的 ``audit.verify()`` / ``verify_audit_chain()`` 包成一个**只读**
        #: 函数递进来，于是 ``export()`` 建包之前的双验**调用方绕不过**——传
        #: ``chain_ok=True`` 也拦得住。不注入时（单测 / 模块自检直接构造）审计一侧
        #: 如实记为 ``source="caller"``：由调用方传进来的 ``chain_ok`` 兜住，
        #: ``reason`` 与 ``detail.sides.audit.source`` 都写明这一侧是谁验的。
        self._audit_verifier = audit_verifier

    # -- 组包 -------------------------------------------------------------- #
    def build_bundle(self, *, records, audit_entries, letters=None,
                     period=None) -> dict:
        """三段内容 → ``root_hash``。空 records/audit 都算空包 → BUNDLE_EMPTY。

        为什么三段哈希而不是一个大哈希：对账时可以只复算可疑的一段；
        ``root_hash`` 仍然是三段哈希的归约，单段被改整体必炸。
        """
        sections = {
            "records": [r for r in (records or []) if isinstance(r, dict)],
            "audit": [a for a in (audit_entries or []) if isinstance(a, dict)],
            "letters": [l for l in (letters or []) if isinstance(l, dict)],
        }
        if not sections["records"] and not sections["audit"]:
            raise ComplianceError("COMPLIANCE_BUNDLE_EMPTY",
                                  "records 与 audit 都是空的：没有可导出的证据",
                                  hint="先导入记录或生成审计事件，再来打包")
        hashes = {name: _section_hash(items) for name, items in sections.items()}
        root_payload = "\n".join(f"{name}={hashes[name]}" for name in
                                 ("records", "audit", "letters"))
        return {"sections": sections, "hashes": hashes,
                "root_hash": "sha256:" + hashlib.sha256(
                    root_payload.encode("utf-8")).hexdigest(),
                "period": dict(period or {})}

    # -- 导出前强制双验（B8-4 前半 / TASK-R8-1 §2.1）-------------------------- #
    def _audit_side(self, chain_ok: bool) -> dict:
        """审计链一侧的判据：注入的复算钩子优先，没注入时如实记「调用方声明」。

        钩子抛异常 = **不合格**（``audit_verify_error``），绝不把"验不了"当成"验过了"。
        判据里不带文件路径（docs/03 §13：日志与响应不写完整路径）。
        """
        verifier = self._audit_verifier
        if verifier is None:
            problems = [] if chain_ok else [{
                "code": "audit_chain_broken", "index": None,
                "reason": "调用方声明审计链不合格（chain_ok=False），本服务实例未注入复算钩子"}]
            return {"ok": bool(chain_ok), "source": "caller",
                    "count": None, "head": "", "problems": problems}
        try:
            raw = dict(verifier() or {})
        except Exception as exc:            # noqa: BLE001 - 验不了就是断链，不许放行
            return {"ok": False, "source": "injected", "count": None, "head": "",
                    "problems": [{"code": "audit_verify_error", "index": None,
                                  "reason": f"{type(exc).__name__}: {exc}"[:200]}]}
        problems: list[dict] = []
        for item in list(raw.get("problems") or []):
            if not isinstance(item, Mapping):
                continue
            problems.append({"code": str(item.get("code") or "audit_problem"),
                             "index": item.get("index"),
                             "reason": str(item.get("reason") or "")[:200]})
        ok = bool(raw.get("ok"))
        if not ok and not problems:
            problems.append({"code": "audit_chain_broken", "index": None,
                             "reason": "链自检未通过，判据里没有断点明细"})
        return {"ok": ok, "source": "injected", "count": raw.get("count"),
                "head": str(raw.get("head") or ""), "problems": problems[:GATE_PROBLEM_PREVIEW]}

    def _ledger_side(self) -> dict:
        """本模块导出账本链一侧的判据：``Ledger.verify()`` 逐条复算，**只报告不修**。

        与 T37（``verify``）报的 ``ledger_ok`` / ``ledger_problems`` 是同一份代码——
        导出前闸门与取证复算之间没有第二套口径。
        """
        report = dict(self._ledger.verify())
        problems: list[dict] = []
        for item in list(report.get("problems") or []):
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code") or "ledger_problem")
            problem: dict = {"code": code, "index": item.get("index")}
            if code == "ledger_seq_gap":
                problem["expected"], problem["found"] = item.get("expected"), item.get("found")
            elif code == "ledger_prev_mismatch":
                problem["expected"], problem["found"] = item.get("expected"), item.get("found")
            elif code == "ledger_hash_mismatch":
                problem["expected"], problem["found"] = (item.get("recomputed"),
                                                         item.get("recorded"))
            problems.append(problem)
        return {"ok": bool(report.get("ok")), "source": "ledger_verify",
                "count": report.get("count"), "head": str(report.get("head") or ""),
                "problems": problems[:GATE_PROBLEM_PREVIEW]}

    def verify_export_chains(self, *, chain_ok: bool = True) -> dict:
        """导出前的**只读**双验：审计链 + 合规导出账本链，逐条复算哈希。

        纪律（B8-4 前半）：只复算、只报告——不写任何文件、不追加任何账本行、
        不改历史、不修链。返回 ``{ok, audit{...}, compliance_ledger{...}}``；
        ``ok`` 是两条链的与，任一条断即 ``False``（``export()`` 据此拒绝建包）。
        """
        audit = self._audit_side(chain_ok)
        ledger = self._ledger_side()
        return {"ok": bool(audit["ok"]) and bool(ledger["ok"]),
                "audit": audit, "compliance_ledger": ledger,
                "checked_at": self._now()}

    # -- 导出（T33） ------------------------------------------------------- #
    def export(self, *, records, audit_entries, letters=None, period=None,
               format: str = "pdf", lang: str = "zh", user_id="local",
               chain_ok: bool = True) -> dict:
        """打包一份证据（T33），**建包之前**先过导出闸门（B8-4 前半）。

        闸门顺序是刻意的：双验排在格式校验与 ``build_bundle`` **之前**——链断了，
        连"你要什么格式"都不必问，直接 ``COMPLIANCE_CHAIN_BROKEN``。
        于是断链时**零文件产出、零账本行、零成功留痕**（``detail`` 里带
        ``files_written=0`` / ``ledger_rows_appended=0`` 两个 0 供机器核对），
        ``chain_ok=False`` 与空包（``COMPLIANCE_BUNDLE_EMPTY``）都在闸门之后才可能触发。
        """
        gate = self.verify_export_chains(chain_ok=chain_ok)
        if not gate["ok"]:
            raise ComplianceError("COMPLIANCE_CHAIN_BROKEN", gate_reason(gate),
                                  field="chain_ok",
                                  hint="断链的导出不能当证据：先按 t11 取证流程核对原始链",
                                  detail=gate_detail(gate), http_status=500)
        wanted = (format or "").strip().lower()
        if wanted not in FORMATS:
            raise ComplianceError("COMPLIANCE_FORMAT_INVALID",
                                  f"format 只支持 {'/'.join(FORMATS)}",
                                  field="format",
                                  detail={"received": format, "allowed": list(FORMATS)})
        bundle = self.build_bundle(records=records, audit_entries=audit_entries,
                                   letters=letters, period=period)
        # R8-5（D-2）：降级态措辞的**单点在 audit.py**（R8-3 的既有口径：compliance 侧不平行
        # 造文案）。这里只是把同一组常量随 meta 带给两条渲染链与 chain 产物，
        # 账本实体键**一个不增**（§1.5）——诚实态字段只进 meta / 文件本体，不进 exports 条目。
        from . import audit as _audit
        meta = {"period": bundle["period"], "root_hash": bundle["root_hash"],
                "generated_at": self._now(), "lang": lang,
                "counts": {k: len(v) for k, v in bundle["sections"].items()},
                "anchor_state": ANCHOR_STATE,
                "eth_tx_hash": None,          # 恒 None（D-2 第 1 项）：不是「待填」，是不存在
                "ipfs_cid": None,             # 本包字节的 CID 在渲染后算得，见导出记录同名字段
                "anchor_note": _audit.ANCHOR_HONEST_NOTE,
                "eth_tx_note": _audit.ANCHOR_TX_HONEST_NOTE,
                "cid_anchor_note": _audit.CID_NO_ANCHOR_NOTE,
                "cid_self_ref_note": _audit.CID_SELF_REF_NOTE,
                "anchor_deferred_note": _audit.ANCHOR_DEFERRED_NOTE,
                "note": "0.6.0 不接真实锚定服务；anchor_state 如实为 unavailable"}
        if wanted == "csv":
            data = _records_csv(bundle["sections"]["records"])
        elif wanted == "chain":
            data = _chain_jsonl(meta, bundle["sections"])
        else:  # pdf
            data = self._render_pdf(meta, bundle)
        with self._lock:
            export_id = new_id("exp")
            target = self.export_dir / f"{export_id}.{wanted}"
            self.export_dir.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(data)
            except OSError as exc:
                raise ComplianceError("COMPLIANCE_EXPORT_FAILED", "证据包写盘失败",
                                      detail={"error": str(exc)}) from exc
            entry = {
                "id": export_id,
                "user_id": str(user_id if user_id not in (None, "") else "local"),
                "time_range": bundle["period"],
                "format": wanted,
                "root_hash": bundle["root_hash"],
                "ipfs_cid": cid_for(data),
                "eth_tx_hash": None,             # 0.6.0 不上链；字段留空不是"待填"，是不存在
                "anchor_state": ANCHOR_STATE,
                "path": str(target),
                "bytes": len(data),
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "chain_ok": bool(gate["audit"]["ok"]),
                "counts": meta["counts"],
                "created_at": self._now(),
            }
            self._ledger.append(entry)
            return self._public(entry)

    def _render_pdf(self, meta: dict, bundle: dict) -> bytes:
        try:
            # 同 analytics：reportlab 走 importlib 字符串导入，包源码零第三方 import 语句
            pagesizes = importlib.import_module("reportlab.lib.pagesizes")
            pdf_canvas = importlib.import_module("reportlab.pdfgen.canvas")
            pdfbase = importlib.import_module("reportlab.pdfbase")
            cidfonts = importlib.import_module("reportlab.pdfbase.cidfonts")
        except Exception as exc:                                      # noqa: BLE001
            raise ComplianceError(
                "COMPLIANCE_EXPORT_FAILED",
                "PDF 证据包需要 reportlab，当前环境没有",
                hint="改用 format=csv 或 format=chain（纯标准库，任何环境都能出）",
                detail={"error": f"{type(exc).__name__}: {exc}"}) from exc
        # 责任认定书区块与中文字体口径都复用审计侧的既有实现（零平行实现、零第三方）。
        from . import audit as _audit
        font, font_note = _audit._register_pdf_font({"pdfbase": pdfbase, "cidfonts": cidfonts})
        cert = self._certificate(meta, bundle)
        lines = self._pdf_lines(meta, bundle)
        if font == "Helvetica" and font_note:
            # 既有字体口径（§2.3）：``_register_pdf_font`` 注册不到 CJK 字体时退回 Helvetica，
            # 中文会被静默丢掉。这句 ASCII 警告必须**随文件本体**给出，不能只留在响应里。
            lines = lines + ["font_note: no CJK font registered -> Chinese glyphs may be dropped; "
                             "use printable HTML, or format=csv / format=chain for the data"]
        buffer = io.BytesIO()
        canvas = pdf_canvas.Canvas(buffer, pagesize=pagesizes.A4)
        # 修既有 NameError：这一行原来写的是裸 ``A4``，本模块里没有这个名字，于是
        # ``format="pdf"`` 在**装了 reportlab** 的环境当场炸（R8-3 实测：
        # ``NameError: name 'A4' is not defined``）。PM 环境 reportlab 缺失、走不到这一行，
        # 所以此前没人撞见。改动只在既有渲染链内：零新面、零新码、零响应形状变化。
        width, height = pagesizes.A4
        y = height - 60
        canvas.setTitle("TraceLens compliance bundle")
        canvas.setFont(font, 13)
        canvas.drawString(50, y, "TraceLens compliance bundle (evidence)")
        canvas.setFont(font, 8)
        for line in lines:
            y -= 14
            if y < 40:
                canvas.showPage()
                canvas.setFont(font, 8)
                y = height - 60
            canvas.drawString(50, y, line)
        # 责任认定书的图形区（§2.1 第 6 项）：把画布交给审计侧的**单点**渲染器逐模块画方块。
        # 锚点取值、编码与绘制全部住在 audit.py（§11.10 的口径保持为真：本模块零相关代码）；
        # 没有封印锚点时那边一块都不画，只写一行诚实说明（§1.4：不伪造、不顶替、不画占位码）。
        y = _audit.draw_certificate_block(canvas, cert, font=font, x=50, y=y - 16,
                                          page_height=height)
        canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    @staticmethod
    def _certificate(meta: dict, bundle: dict) -> dict:
        """责任认定书的取数（R8-3 / B8-1 §2.1）：两条渲染链共用审计侧实现。

        * 链头 = 审计条目里**最后一次**封印写下的锚点哈希（PM 裁的 A 案口径）。取值、
          措辞与字段名全部住在 ``audit.py`` 单点，本模块不碰那个字段名；它同时是
          二维码载荷 —— 正文那行与码里的串出自同一个值。取不到就留空并如实写
          「无封印锚点，二维码不可用」，**绝不拿** ``root_hash`` **或链尖哈希顶替**。
        * 三段归约 ``root_hash`` 另起一行，带「非链头、非 Merkle 根」限定语
          （PM-VERDICT-S0-3 的 A 案：本模块的 ``root_hash`` 仍是三段归约，一字未动）。
        * ``status.ok``：能走到渲染这一步，说明 R8-1 的导出前双验已在**建包之前**把
          两条链都复算通过（``export()`` 里闸门排在 ``build_bundle`` 之前，顺序钉着）；
          这里如实转记，不复算、不宣称签名背书（两层账本边界，§1.8）。
        """
        from . import audit as _audit
        counts = meta.get("counts") or {}
        sections = bundle.get("sections") or {}
        return _audit.build_certificate(
            sections.get("audit") or [],
            title=_audit.CERT_TITLE,
            period=meta.get("period"),
            generated_at=meta.get("generated_at"),
            lang=meta.get("lang") or "zh",
            root_hash=meta.get("root_hash"),
            section_hashes=bundle.get("hashes"),
            counts={"records": counts.get("records"), "audit": counts.get("audit"),
                    "letters": counts.get("letters")},
            status={"ok": True, "count": counts.get("audit")})

    @staticmethod
    def _pdf_lines(meta: dict, bundle: dict) -> list[str]:
        from . import audit as _audit
        counts = meta.get("counts") or {}
        lines = [f"generated_at: {meta.get('generated_at')}",
                 f"root_hash: {meta.get('root_hash')}",
                 f"anchor_state: {meta.get('anchor_state')} (no anchor backend in 0.6.0)",
                 f"counts: records={counts.get('records', 0)} "
                 f"audit={counts.get('audit', 0)} letters={counts.get('letters', 0)}",
                 f"records_hash: {bundle['hashes']['records']}",
                 f"audit_hash: {bundle['hashes']['audit']}",
                 f"letters_hash: {bundle['hashes']['letters']}",
                 "note: hashes are recomputable offline; verify via POST /api/compliance/exports/<id>/verify"]
        # R8-3（B8-1）：责任认定书模板在既有 8 行**之后追加**，上面逐字不动（零回归）。
        # 正文里的链头行与二维码载荷同源：两处由审计侧生成的**同一个字符串**，
        # 不存在「PDF 里的码」与「PDF 里的字」各写各的（取数与措辞单点在 audit.py）。
        cert = ComplianceService._certificate(meta, bundle)
        lines.extend(_audit.certificate_text(cert, include_title=True))
        return lines

    # -- 查询 / 校验 / 锚定（T32 / T34 / T35 / T36 / T37） ------------------ #
    def _public(self, entry: dict) -> dict:
        view = dict(entry)
        view["uri"] = f"/api/compliance/exports/{entry['id']}/file"
        return view

    def list(self) -> list[dict]:
        with self._lock:
            rows = [self._public(dict(item)) for item in self._ledger.items()
                    if isinstance(item, dict)]
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows

    def get(self, export_id: str) -> dict | None:
        with self._lock:
            for item in self._ledger.items():
                if isinstance(item, dict) and item.get("id") == export_id:
                    return self._public(dict(item))
        return None

    def file_path(self, export_id: str) -> pathlib.Path | None:
        entry = self.get(export_id)
        if not entry:
            return None
        target = pathlib.Path(str(entry.get("path") or ""))
        return target if target.exists() else None

    def attest(self, export_id: str, *, user_id="local") -> dict:
        """T36：0.6.0 没有锚定后端，**永远**抛 ``COMPLIANCE_ANCHOR_UNAVAILABLE``。

        为什么不返回"伪成功"：attest 的语义是"第三方在某一时刻见过这份字节"。
        没有第三方，编一个 green ``anchor_state`` 就是伪造证据——宁可不支持。
        CID 早已在 ``export`` 时算好（``ipfs_cid`` 字段），复算走 T37。

        R8-5（D-2 第 2 项：诚实报错**保留**）：码名、文案主体、``http_status=503`` 一字未动；
        本轮只往 ``hint`` / ``detail`` 里**加**更如实的信息（未锚定同屏措辞 + 「≤2 分钟」
        延后声明），没有任何一处往成功方向改。改动行见 REPORT-R8-5 §attest。
        """
        entry = self.get(export_id)
        if not entry:
            raise ComplianceError("COMPLIANCE_NOT_FOUND",
                                  f"导出记录 {export_id} 不存在",
                                  detail={"export_id": export_id})
        from . import audit as _audit          # 措辞单点：与导出件 / UI 同一句
        raise ComplianceError(
            "COMPLIANCE_ANCHOR_UNAVAILABLE",
            "0.6.0 没有接入任何锚定服务（TSA/IPFS pinning/以太坊），无法 attest",
            hint="记录里的 ipfs_cid 可离线复算这份字节，但**未锚定（no anchor）/ 待接入**；"
                 "真锚定属于 0.7.0 的 AnchorProvider 接口",
            detail={"export_id": export_id, "ipfs_cid": entry.get("ipfs_cid"),
                    "anchor_state": ANCHOR_STATE, "eth_tx_hash": None,
                    "cid_anchor_note": _audit.CID_NO_ANCHOR_NOTE,
                    "cid_self_ref_note": _audit.CID_SELF_REF_NOTE,
                    "anchor_deferred_note": _audit.ANCHOR_DEFERRED_NOTE,
                    "cid_anchored": False,
                    "deferred_item": "B8-3"},
            http_status=503)

    def verify(self, export_id: str) -> dict:
        """T37：文件逐字节复算 + root_hash 复算 + 账本链复核，结果只报告。"""
        with self._lock:
            entry = self.get(export_id)
            if not entry:
                raise ComplianceError("COMPLIANCE_NOT_FOUND",
                                      f"导出记录 {export_id} 不存在",
                                      detail={"export_id": export_id})
            target = pathlib.Path(str(entry.get("path") or ""))
            file_intact = target.exists()
            actual_sha = ""
            actual_cid = ""
            if file_intact:
                data = target.read_bytes()
                actual_sha = "sha256:" + hashlib.sha256(data).hexdigest()
                actual_cid = cid_for(data)
            root_ok = None
            if file_intact and entry.get("format") == "chain":
                root_ok = self._recompute_root(target)
            events = self._ledger.verify()
            return {
                "ok": bool(file_intact and (not actual_sha or actual_sha == entry.get("sha256"))
                           and (actual_cid == entry.get("ipfs_cid"))),
                "file_exists": file_intact,
                "sha256_expected": entry.get("sha256"),
                "sha256_actual": actual_sha,
                "sha256_ok": bool(actual_sha and actual_sha == entry.get("sha256")),
                "ipfs_cid_expected": entry.get("ipfs_cid"),
                "ipfs_cid_actual": actual_cid,
                "ipfs_cid_ok": bool(actual_cid and actual_cid == entry.get("ipfs_cid")),
                "root_hash": entry.get("root_hash"),
                "root_hash_ok": root_ok,
                "ledger_ok": bool(events.get("ok")),
                "ledger_problems": events.get("problems") or [],
                "anchor_state": entry.get("anchor_state"),
                "eth_tx_hash": None,
                "note": "文件与 CID 可离线复算；anchor_state 在 0.6.0 恒为 unavailable（没有锚定后端）",
            }

    def _recompute_root(self, target: pathlib.Path) -> bool | None:
        try:
            lines = [json.loads(line) for line in
                     target.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return None
        entries = {}
        header = {}
        for line in lines:
            section = line.get("section")
            if section == "meta":
                header = line.get("content") or {}
            elif section == "entries":
                entries = line.get("content") or {}
        if not header or not entries:
            return None
        payload = "\n".join(f"{name}={_section_hash(entries.get(name))}"
                            for name in ("records", "audit", "letters"))
        expected = header.get("root_hash")
        return expected == ("sha256:" + hashlib.sha256(
            payload.encode("utf-8")).hexdigest())

    def facts(self) -> dict:
        with self._lock:
            events = self._ledger.verify()
            return {"version": COMPLIANCE_VERSION, "exports": self._ledger.count(),
                    "ledger_ok": bool(events.get("ok")),
                    "anchor_state": ANCHOR_STATE,
                    "formats": list(FORMATS),
                    "export_dir": str(self.export_dir),
                    "note": "0.6.0 无锚定后端；eth_tx_hash 恒空是如实，不是缺陷"}


def default_service(paths: FeaturePaths, *, now=None, export_dir=None) -> ComplianceService:
    return ComplianceService(paths, export_dir=export_dir, now=now)


def _self_check() -> int:  # pragma: no cover - 人工自检
    """``python -m agent_explainer.compliance``：导出→复算→attest 诚实 503。"""
    import tempfile

    def record(idx: int) -> dict:
        return {"id": f"act-{idx:04d}", "timestamp": "2026-11-11T10:00:00Z",
                "agent_id": "agent-a", "action_type": "read", "status": "succeeded",
                "risk_level": "low", "tool_used": {"name": "calendar.read"}}

    audit_entry = {"id": "aud-1", "event": "intervention.decision",
                   "actor_id": "local-user", "action_id": "act-0001",
                   "entry_hash": "sha256:" + "a" * 64}
    with tempfile.TemporaryDirectory() as temp:
        paths = FeaturePaths(temp)
        paths.ensure()
        service = ComplianceService(paths)
        bundle = service.build_bundle(records=[record(1), record(2)],
                                      audit_entries=[audit_entry])
        assert bundle["root_hash"].startswith("sha256:")
        for fmt in ("csv", "chain"):
            entry = service.export(records=[record(1)], audit_entries=[audit_entry],
                                   format=fmt, user_id="local-user")
            assert entry["anchor_state"] == ANCHOR_STATE
            assert entry["eth_tx_hash"] is None
            verdict = service.verify(entry["id"])
            assert verdict["ok"] and verdict["sha256_ok"] and verdict["ipfs_cid_ok"], verdict
        chain_entry = service.export(records=[record(1)], audit_entries=[audit_entry],
                                     format="chain")
        assert service.verify(chain_entry["id"])["root_hash_ok"] is True
        assert len(service.list()) == 3
        try:
            service.attest(chain_entry["id"])
            raise SystemExit("attest 不该在 0.6.0 成功")
        except ComplianceError as exc:
            assert exc.code == "COMPLIANCE_ANCHOR_UNAVAILABLE" \
                and exc.http_status == 503 and exc.detail["eth_tx_hash"] is None
        print(f"compliance self-check ok: exports={len(service.list())} "
              f"cid_sample={chain_entry['ipfs_cid'][:14]}… "
              f"anchor_state={ANCHOR_STATE} (attest 503 如实)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_self_check())
