"""R8-5（REQ-008 第 5 子任务 / B8-3 按 D-2 决议）验收测试：存证降级态如实展示。

运行（全程离线、只用标准库；不起服务、不出网、不装任何第三方）：

    cd agent-explainer
    python -X utf8 -m unittest tests.test_export_degraded_display -v

钉住的口径（TASK-R8-5 §2.1 / §2.3，D-2 三项约束逐字）：

1. ``ExportRecord.eth_tx_hash`` **恒 ``None``**：账本行、导出记录响应、meta、认定书
   数据本体、``attest`` 的 ``detail`` 五处全为 ``None``；**不得**出现占位值 / 假哈希 /
   空字符串冒充（第 1 组）。离线 CIDv1 可展示，但**必须同时明示「未锚定（no anchor）」**。
2. **CID 与未锚定同屏**：责任认定书正文里 ``ipfs_cid`` 行与「未锚定（no anchor）/ 待接入」
   措辞**相邻成行**，模板**不预填**本文件自身的 CID（预填即自指失真）（第 2 组）。
3. ``attest()`` 的**诚实报错保留**：码名 / 文案主体 / ``http_status=503`` 逐字不变，
   本轮只往 ``hint`` / ``detail`` 里**加**更如实的信息，没有任何一处往成功方向改（第 3 组）。
4. **展示纪律**：完成态禁语（``audit.DISPLAY_FORBIDDEN_WORDS``）与「正在上链…」「已上链」
   「交易哈希已生成」等完成态假象，在导出件、i18n 字典、``app.js`` 三处**零出现**（第 4 组）。
5. **UI 诚实态切片**：导出面板里的锚定区块由后端常量（``unavailable`` / ``None``）+ 四条
   字典文案构成；**零新调用面**（``API_ENDPOINTS`` 不含 ``/api/compliance``，T33 未挂载）、
   零新格式枚举。进度条 / 结果页自 R8-6 起在位，但**必须是不确定态**（同组断言反向守住：
   有挂载点、无定时器、无写宽度、无自算百分比）（第 5 组）。
6. **零回归**：账本实体键 14 个零新增、chain 产物 meta 不含链头键、CSV 表头恒 24 列、
   PDF 既有 8 行逐字不动、两句披露在位、``SIGNATURE_FIELDS`` 11 / ``CSV_COLUMNS`` 24 /
   ``_HTML_COLUMNS`` 14 / ``FORMATS`` 三值 / 锚定态一字未动（第 6 组）。

纪律：临时目录一律 ``tests/scratch.py``（禁 ``tempfile``）；不并发跑全量。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_explainer import audit as au            # noqa: E402
from agent_explainer import compliance as cm       # noqa: E402
from agent_explainer.feature_store import FeaturePaths  # noqa: E402
from scratch import scratch_dir                    # noqa: E402

WEB = ROOT / "agent_explainer" / "web"
ZH = json.loads((WEB / "i18n" / "zh.json").read_text(encoding="utf-8"))
EN = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))
EMBED = (WEB / "i18n" / "embed.js").read_text(encoding="utf-8")
KEYS_MD = (WEB / "i18n" / "keys.md").read_text(encoding="utf-8")
APP_JS = (WEB / "app.js").read_text(encoding="utf-8")

#: 完成态假象（0.4 / D-2 第 2 项）：这些措辞在导出件、字典、界面代码里一律零出现。
COMPLETION_LIES: tuple[str, ...] = ("正在上链", "已上链", "交易哈希已生成", "上链完成",
                                    "已确认在链", "锚定成功", "存证成功")
#: 本轮新增的四条 UI 文案键（i18n 四件套逐字同步）。
ANCHOR_LEAVES: tuple[str, ...] = ("anchor.state", "anchor.cid", "anchor.tx", "anchor.deferred")
#: 导出一条最小可用证据包用的记录（与 R8-3 钉测同形）。
RECORDS: list[dict] = [{"id": "rec-r85", "timestamp": "2026-11-01T08:00:00Z",
                        "agent_id": "agent-a", "action_type": "tool.run", "status": "done",
                        "risk_level": "low", "tool_used": {"name": "probe"}}]


def sealed_log(case: unittest.TestCase):
    """一条真链：3 条决定 + 1 次 ``seal()``（认证书区与闸门都要真数据才走得到）。"""
    room = scratch_dir(f"r8-5-audit-{case.id().rsplit('.', 1)[-1]}")
    case.addCleanup(shutil.rmtree, room, True)
    log = au.AuditLog(room / "audit.jsonl")
    for index in range(3):
        log.append({"event": "decision", "actor_id": "local-user", "actor_role": "local",
                    "action_id": f"act-r85-000{index}", "decision": "approve",
                    "reason": f"R8-5 降级态样片 {index}"})
    seal = log.seal(note="R8-5 封印")
    return log, seal, log.read()


def export_chain(case: unittest.TestCase) -> tuple[dict, list[dict]]:
    """跑一次 ``format="chain"`` 导出（纯标准库，任何环境都出件），返回 (记录, 审计条目)。"""
    room = scratch_dir(f"r8-5-export-{case.id().rsplit('.', 1)[-1]}")
    case.addCleanup(shutil.rmtree, room, True)
    paths = FeaturePaths(room / "features")
    paths.ensure()
    _log, _seal, rows = sealed_log(case)
    service = cm.ComplianceService(paths)
    entry = service.export(records=list(RECORDS), audit_entries=rows, format="chain")
    return entry, rows


def meta_line(entry: dict) -> dict:
    """chain 产物第一行是 meta 段：``{"section": "meta", "content": {…}}``（既有形状）。"""
    first = pathlib.Path(entry["path"]).read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(first)
    content = payload.get("content")
    return content if isinstance(content, dict) else payload


def cert_of(rows: list[dict], status: dict) -> dict:
    return au.build_certificate(rows, status=status, generated_at="2026-11-15T10:00:00Z")


# --------------------------------------------------------------------------- #
# 1. D-2 第 1 项：eth_tx_hash 恒 None / anchor_state 恒 unavailable（五处同值）
# --------------------------------------------------------------------------- #

class HonestFieldTests(unittest.TestCase):

    def setUp(self) -> None:
        self.entry, self.rows = export_chain(self)
        self.meta = meta_line(self.entry)

    def test_eth_tx_hash_is_none_on_every_surface(self) -> None:
        self.assertIsNone(self.entry["eth_tx_hash"], "导出记录：恒 None（D-2 第 1 项）")
        self.assertIn("eth_tx_hash", self.meta, "meta 要显式写出这个键，读的人才看得见")
        self.assertIsNone(self.meta["eth_tx_hash"], "meta：恒 None，不是靠缺键凑出来的")
        cert = au.build_certificate(self.rows, status={"ok": True, "count": len(self.rows)},
                                    generated_at="2026-11-15T10:00:00Z")
        self.assertIsNone(cert["eth_tx_hash"], "认定书数据本体：恒 None")
        self.assertIn("eth_tx_hash: None", "\n".join(au.certificate_text(cert)))

    def test_no_fake_hash_shape_anywhere(self) -> None:
        blob = json.dumps(self.meta, ensure_ascii=False) + json.dumps(
            {k: v for k, v in self.entry.items()}, ensure_ascii=False)
        self.assertNotIn("0x", blob, "没有 EVM 交易哈希可写：不许出现 0x 形状的假哈希")
        self.assertNotRegex(blob, r'"eth_tx_hash"\s*:\s*(?!null)"',
                            "eth_tx_hash 一旦有值（含空字符串）就是冒充")

    def test_anchor_state_is_unavailable_on_every_surface(self) -> None:
        self.assertEqual(cm.ANCHOR_STATE, "unavailable")
        self.assertEqual(au.ANCHOR_STATE_TEXT, cm.ANCHOR_STATE, "两侧同值，钉测逐字核对")
        self.assertEqual(self.entry["anchor_state"], "unavailable")
        self.assertEqual(self.meta["anchor_state"], "unavailable")
        cert = au.build_certificate(self.rows, status={"ok": True, "count": len(self.rows)})
        self.assertEqual(cert["anchor_state"], "unavailable")
        room = scratch_dir(f"r8-5-facts-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        facts = cm.ComplianceService(paths).facts()
        self.assertEqual(facts["anchor_state"], "unavailable")
        self.assertIn("eth_tx_hash 恒空是如实", facts["note"], "既有如实说明不得被改掉")

    def test_ledger_row_keeps_the_same_honest_fields(self) -> None:
        """账本行的键集与 R8-3 基线逐字相同：诚实态字段只进 meta / 文件本体，不新增实体键。"""
        room = scratch_dir(f"r8-5-row-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        entry = service.export(records=list(RECORDS), audit_entries=rows, format="csv")
        text = paths.exports.read_text(encoding="utf-8")
        row = json.loads([ln for ln in text.splitlines() if ln.strip()][-1])
        item = row["item"]
        self.assertIsNone(item["eth_tx_hash"])
        self.assertEqual(item["anchor_state"], "unavailable")
        self.assertEqual(set(item) - {"seq", "prev_hash", "entry_hash"},
                          set(entry) - {"uri"},
                          "账本行键集与响应键集同集：本轮零新增")


# --------------------------------------------------------------------------- #
# 2. D-2 第 1 项后半：CID 与「未锚定（no anchor）/ 待接入」同屏
# --------------------------------------------------------------------------- #

class SameScreenCidTests(unittest.TestCase):

    def setUp(self) -> None:
        self.log, self.seal, self.rows = sealed_log(self)
        self.status = self.log.verify(raise_on_tamper=False)
        self.cert = cert_of(self.rows, self.status)
        self.lines = au.certificate_text(self.cert)

    def test_unanchored_words_are_exact(self) -> None:
        self.assertIn("未锚定（no anchor）", au.CID_NO_ANCHOR_NOTE)
        self.assertIn("待接入", au.CID_NO_ANCHOR_NOTE)
        self.assertIn("无 IPFS pinning", au.CID_NO_ANCHOR_NOTE)
        self.assertIn("离线", au.CID_NO_ANCHOR_NOTE, "说清 CID 的可复算性质，不神化它")
        self.assertIn("不预填", au.CID_SELF_REF_NOTE)
        self.assertIn("自指", au.CID_SELF_REF_NOTE)

    def test_cid_line_and_unanchored_note_are_adjacent(self) -> None:
        idx = [i for i, ln in enumerate(self.lines) if ln.startswith("ipfs_cid:")]
        self.assertEqual(len(idx), 1, "正文只有一行 ipfs_cid，不许多处各写各的")
        note_at = self.lines.index(au.CID_NO_ANCHOR_NOTE)
        self.assertEqual(note_at, idx[0] + 1,
                         "CID 行与「未锚定（no anchor）/ 待接入」必须相邻成行（同屏）")
        self.assertEqual(self.lines.index(au.ANCHOR_TX_HONEST_NOTE),
                         self.lines.index(au.ANCHOR_HONEST_NOTE) + 1,
                         "锚定态与交易哈希两行必须相邻（R8-3 既有形状不回退）")
        self.assertLess(self.lines.index(au.ANCHOR_HONEST_NOTE), idx[0],
                        "先说锚定态、再摆 CID：CID 不许出现在没有任何未锚定措辞的屏上")

    def test_template_never_prefills_a_cid(self) -> None:
        self.assertIsNone(self.cert["ipfs_cid"],
                          "本文件自身的 CID 由导出时对最终字节算得，模板不预填（自指即失真）")
        self.assertIn("不预填", self.lines[self.lines.index(au.CID_SELF_REF_NOTE)])
        self.assertTrue(any(ln.startswith("ipfs_cid: （") for ln in self.lines),
                        "没有 CID 可写时如实写明出处，不留空、不编一个")

    def test_deferred_item_is_written_into_the_export(self) -> None:
        """D-2 第 3 项：「上链 ≤2 分钟」延后而非删除，必须随导出件在案。"""
        joined = "\n".join(self.lines)
        self.assertIn("≤2 分钟", au.ANCHOR_DEFERRED_NOTE)
        self.assertIn("延后而非删除", au.ANCHOR_DEFERRED_NOTE)
        self.assertIn(au.ANCHOR_DEFERRED_NOTE, self.lines)
        self.assertNotIn("已上链", joined, "延后声明本身不许写成完成态")

    def test_meta_carries_the_same_words_as_the_certificate(self) -> None:
        entry, _rows = export_chain(self)
        meta = meta_line(entry)
        for key, const in (("anchor_note", au.ANCHOR_HONEST_NOTE),
                           ("eth_tx_note", au.ANCHOR_TX_HONEST_NOTE),
                           ("cid_anchor_note", au.CID_NO_ANCHOR_NOTE),
                           ("cid_self_ref_note", au.CID_SELF_REF_NOTE),
                           ("anchor_deferred_note", au.ANCHOR_DEFERRED_NOTE)):
            self.assertEqual(meta.get(key), const, f"meta.{key} 必须与 audit 侧措辞逐字同值")
        self.assertEqual(meta.get("ipfs_cid"), None, "meta 不预填 CID（与导出记录同值口径）")
        self.assertEqual(meta.get("note"), "0.6.0 不接真实锚定服务；anchor_state 如实为 unavailable",
                         "R8-3 既有 note 逐字保留（零回归）")

    def test_pdf_lines_keep_legacy_eight_then_the_degraded_block(self) -> None:
        bundle = {"sections": {"records": list(RECORDS), "audit": self.rows, "letters": []},
                  "hashes": {"records": "sha256:r", "audit": "sha256:a", "letters": "sha256:l"}}
        meta = {"period": {"since": "2026-11-01", "until": "2026-11-15"},
                "root_hash": "sha256:" + "7" * 64, "generated_at": "2026-11-15T10:00:00Z",
                "lang": "zh", "anchor_state": cm.ANCHOR_STATE, "eth_tx_hash": None,
                "ipfs_cid": None, "cid_anchor_note": au.CID_NO_ANCHOR_NOTE,
                "counts": {"records": 1, "audit": len(self.rows), "letters": 0}}
        lines = cm.ComplianceService._pdf_lines(meta, bundle)
        self.assertEqual(lines[2],
                         "anchor_state: unavailable (no anchor backend in 0.6.0)",
                         "既有第 3 行逐字不动")
        self.assertEqual(lines[:8][0], "generated_at: 2026-11-15T10:00:00Z")
        joined = "\n".join(lines)
        self.assertIn(au.CID_NO_ANCHOR_NOTE, joined, "PDF 正文里 CID 必须与未锚定同屏")
        self.assertIn(au.ANCHOR_DEFERRED_NOTE, joined)


# --------------------------------------------------------------------------- #
# 3. D-2 第 2 项：attest() 的诚实报错保留（503 恒在，不许伪成功）
# --------------------------------------------------------------------------- #

class AttestHonestTests(unittest.TestCase):

    def setUp(self) -> None:
        self.entry, _rows = export_chain(self)

    def test_attest_always_raises_the_same_503(self) -> None:
        room = scratch_dir(f"r8-5-attest-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        entry = service.export(records=list(RECORDS), audit_entries=rows, format="chain")
        with self.assertRaises(cm.ComplianceError) as ctx:
            service.attest(entry["id"])
        err = ctx.exception
        self.assertEqual(err.code, "COMPLIANCE_ANCHOR_UNAVAILABLE", "码名一字未动")
        self.assertEqual(err.http_status, 503, "503 一字未动（不许伪成功、不许改 200/404）")
        self.assertEqual(err.reason, "0.6.0 没有接入任何锚定服务（TSA/IPFS pinning/以太坊），无法 attest",
                         "文案主体逐字保留（本轮只加不改方向）")
        self.assertIn("未锚定（no anchor）", str(err.hint), "hint 只能更如实：加了未锚定措辞")
        self.assertIn("待接入", str(err.hint))

    def test_attest_detail_keeps_originals_and_adds_honesty(self) -> None:
        room = scratch_dir(f"r8-5-detail-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        entry = service.export(records=list(RECORDS), audit_entries=rows, format="chain")
        with self.assertRaises(cm.ComplianceError) as ctx:
            service.attest(entry["id"])
        detail = ctx.exception.detail
        for key in ("export_id", "ipfs_cid", "anchor_state", "eth_tx_hash"):
            self.assertIn(key, detail, f"原有 detail 键 {key} 不得被删（R8-1 基线）")
        self.assertIsNone(detail["eth_tx_hash"])
        self.assertEqual(detail["anchor_state"], "unavailable")
        self.assertEqual(detail["cid_anchor_note"], au.CID_NO_ANCHOR_NOTE)
        self.assertIs(detail["cid_anchored"], False, "机器可读的「未锚定」旗标")
        self.assertEqual(detail["anchor_deferred_note"], au.ANCHOR_DEFERRED_NOTE)
        self.assertEqual(detail["export_id"], entry["id"])
        self.assertEqual(detail["ipfs_cid"], entry["ipfs_cid"],
                         "detail 里的 CID 就是导出记录那个可离线复算的 CID，不另编一个")

    def test_attest_of_unknown_id_still_reports_not_found(self) -> None:
        room = scratch_dir(f"r8-5-attest404-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        service = cm.ComplianceService(paths)
        with self.assertRaises(cm.ComplianceError) as ctx:
            service.attest("exp-not-there")
        self.assertEqual(ctx.exception.code, "COMPLIANCE_NOT_FOUND")
        self.assertNotEqual(ctx.exception.http_status, 503,
                            "不存在的记录不许被说成锚定服务问题")

    def test_attest_never_returns_a_success_shape(self) -> None:
        src = (ROOT / "agent_explainer" / "compliance.py").read_text(encoding="utf-8")
        body = src.split("def attest", 1)[1].split("def verify", 1)[0]
        self.assertIn("raise ComplianceError", body)
        self.assertIn("http_status=503", body)
        self.assertNotRegex(body, r"return\s+\{[^{}]*\"ok\"\s*:\s*True",
                            "attest 里绝不允许出现 ok=True 的返回形状")


# --------------------------------------------------------------------------- #
# 4. 展示纪律：导出件 / 字典 / 界面代码三处零完成态假象
# --------------------------------------------------------------------------- #

class DisplayDisciplineR85Tests(unittest.TestCase):

    def test_forbidden_words_absent_in_export_surfaces(self) -> None:
        log, _seal, rows = sealed_log(self)
        cert = cert_of(rows, log.verify(raise_on_tamper=False))
        entry, _rows = export_chain(self)
        corpus = "\n".join(au.certificate_text(cert)) + "\n" + json.dumps(
            meta_line(entry), ensure_ascii=False)
        for word in au.DISPLAY_FORBIDDEN_WORDS:
            self.assertNotIn(word, corpus, f"导出件里出现完成态禁语：{word}")
        self.assertEqual(au.DISPLAY_FORBIDDEN_WORDS,
                         ("已恢复", "已撤销", "已上链", "已冲正完成"), "禁语清单一字不加")

    def test_completion_lies_absent_in_dictionaries_and_app(self) -> None:
        corpora = {"zh.json": json.dumps(ZH, ensure_ascii=False),
                   "en.json": json.dumps(EN, ensure_ascii=False),
                   "embed.js": EMBED, "keys.md": KEYS_MD}
        for label, blob in corpora.items():
            for lie in COMPLETION_LIES:
                self.assertNotIn(lie, blob, f"{label} 里出现完成态假象：{lie}")
        for lie in ("正在上链", "已上链"):
            self.assertNotIn(lie, APP_JS, "界面代码里有完成态措辞（app.js 应零 CJK）")
        self.assertNotRegex(APP_JS, r"export\.(state|note|feedback)\.[a-z_]*chain",
                            "导出面板不许出现「上链中 / 已上链」式的键名")

    def test_progress_face_is_indeterminate(self) -> None:
        """R8-6 落地后，R8-5 的「面板里不许有 progress」边界**反向守住**。

        R8-5 时这条边界是「进度条/结果页归 R8-6，本轮不许提前做」；R8-6 把面做出来
        之后，同一条纪律必须换个方向成立：**面可以有，假百分比不可以有**。所以这里
        断言挂载点在位，而「定时器 / 写宽度 / 未挂载的合规路由 / 面板自算百分比」仍然
        一个都不许出现。先剥注释再扫，避免把「说明自己不做假」判成「做了」。
        """
        panel = APP_JS.split("function renderExportPanel", 1)[1].split("function renderAuditPanel", 1)[0]
        code = re.sub(r"/\*.*?\*/", " ", panel, flags=re.S)
        code = re.sub(r"//[^\n]*", " ", code)
        self.assertIn("export-progress-host", code, "R8-6 的进度挂载点丢了")
        self.assertIn("export-result-host", code, "R8-6 的结果挂载点丢了")
        self.assertIn("TraceLensCompliance", code, "面板没有把进度/结果交给 ext/compliance.js")
        for token in ("setInterval", "setTimeout", "style.width", "attest", "/api/compliance"):
            self.assertNotIn(token, code, f"面板代码里不该有 {token}（假进度 / 未挂载路由）")
        self.assertNotIn("percent", code, "面板不得自己算百分比")

    def test_english_copy_has_no_cjk(self) -> None:
        for leaf in ANCHOR_LEAVES:
            value = str(EN["export"][leaf])
            self.assertFalse(re.search(r"[\u3400-\u4dbf]", value), f"en.export.{leaf} 含中文")
            self.assertNotEqual(value, str(ZH["export"][leaf]), f"en.export.{leaf} 与 zh 同值＝漏译")


# --------------------------------------------------------------------------- #
# 5. UI 诚实态切片（app.js + i18n 四件套）
# --------------------------------------------------------------------------- #

class UiDegradedSliceTests(unittest.TestCase):

    def test_panel_renders_the_backend_constants(self) -> None:
        self.assertIn("var EXPORT_ANCHOR_STATE = 'unavailable';", APP_JS,
                      "面板写的是后端常量 compliance.ANCHOR_STATE 的值，两侧钉测同值")
        self.assertIn("var EXPORT_ETH_TX_HASH = 'none';", APP_JS)
        self.assertIn("dataset: { hook: 'export-anchor' }", APP_JS)
        self.assertIn("'data-cid-anchored', 'false'", APP_JS,
                      "CID 的锚定旗标恒 false：UI 侧也不许暗示已锚定")

    def test_panel_uses_four_dictionary_lines(self) -> None:
        for leaf in ANCHOR_LEAVES:
            key = f"export.{leaf}"
            self.assertIn(f"'{key}'", APP_JS, f"app.js 未引用字典键 {key}")
            self.assertTrue(str(ZH["export"][leaf]).strip(), f"zh 缺文案 {key}")
            self.assertTrue(str(EN["export"][leaf]).strip(), f"en 缺文案 {key}")
            self.assertIn(f"`{key}`", KEYS_MD, f"keys.md 未列出 {key}（跑 --sync-keys）")
            self.assertIn(leaf, EMBED, f"embed.js 未同步 {key}（跑 --sync-embed）")

    def test_copy_words_state_the_degraded_facts(self) -> None:
        state = str(ZH["export"]["anchor.state"])
        cid = str(ZH["export"]["anchor.cid"])
        tx = str(ZH["export"]["anchor.tx"])
        deferred = str(ZH["export"]["anchor.deferred"])
        self.assertIn("unavailable", state)
        self.assertIn("待接入", state)
        self.assertIn("未锚定（no anchor）", cid, "CID 与未锚定必须同屏（D-2 第 1 项）")
        self.assertIn("None", tx)
        self.assertIn("不是「待填」", tx)
        self.assertIn("延后而非删除", deferred)

    def test_no_new_call_face_and_no_new_format(self) -> None:
        endpoints = APP_JS.split("API_ENDPOINTS", 1)[1]
        endpoints = endpoints[:endpoints.index("};")] if "};" in endpoints else endpoints[:4000]
        self.assertNotIn("/api/compliance", endpoints,
                         "本轮不挂载合规导出面（T33 未挂载；契约第 2 项 FAIL 不得清零）")
        self.assertNotIn("/api/evidence", endpoints)
        self.assertEqual(cm.FORMATS, ("pdf", "csv", "chain"), "格式枚举零新增")

    def test_app_js_has_no_cjk(self) -> None:
        self.assertFalse(re.search(r"[\u3400-\u4dbf]", APP_JS),
                         "界面代码零 CJK：文案只能来自字典（i18n_lint 同口径）")


# --------------------------------------------------------------------------- #
# 6. 零回归（R8-1 / R8-2 / R8-3 / R8-4 基线不回退）
# --------------------------------------------------------------------------- #

class ZeroRegressionTests(unittest.TestCase):

    def test_constants_are_untouched(self) -> None:
        self.assertEqual(len(au.CSV_COLUMNS), 24, "R8-2 收口：本轮零改动")
        self.assertEqual(len(au._HTML_COLUMNS), 14, "R8-2 遗留第 8 项：本轮不擅自同步")
        self.assertEqual(cm.FORMATS, ("pdf", "csv", "chain"))
        self.assertEqual(cm.ANCHOR_STATE, "unavailable")
        self.assertEqual(len(au.CERT_DISCLOSURE), 2, "两句披露，不少不凑")
        self.assertIn("验签通过≠背书齐备", au.CERT_DISCLOSURE[0])

    def test_csv_header_is_unchanged(self) -> None:
        room = scratch_dir(f"r8-5-csv-{self.id().rsplit('.', 1)[-1]}")
        self.addCleanup(shutil.rmtree, room, True)
        paths = FeaturePaths(room / "features")
        paths.ensure()
        _log, _seal, rows = sealed_log(self)
        service = cm.ComplianceService(paths)
        entry = service.export(records=list(RECORDS), audit_entries=rows, format="csv")
        text = pathlib.Path(entry["path"]).read_text(encoding="utf-8-sig")
        self.assertEqual(text.splitlines()[0],
                         "id,timestamp,agent_id,action_type,status,risk_level,tool,decision,decided_by",
                         "记录侧 CSV 表头逐字不动（R8-3 基线）")

    def test_chain_meta_still_has_no_sealed_head(self) -> None:
        """§11.10 A 案：chain 产物的 meta 不因模板而变（本轮新增字段也不得带进链头键）。"""
        entry, _rows = export_chain(self)
        blob = json.dumps(meta_line(entry), ensure_ascii=False)
        self.assertNotIn("sealed_head", blob)
        self.assertNotIn("seal", blob.lower(), "QR / 封印相关键不进 chain meta（渲染链专属）")

    def test_certificate_disclosure_and_merkle_negation_survive(self) -> None:
        log, _seal, rows = sealed_log(self)
        cert = cert_of(rows, log.verify(raise_on_tamper=False))
        joined = "\n".join(au.certificate_text(cert))
        self.assertIn("验签通过≠背书齐备", joined)
        self.assertIn("不构成 secretariat 签名账本的背书", joined)
        for line in joined.splitlines():
            if "Merkle" in line:
                self.assertTrue(any(n in line for n in ("非", "不是")), f"肯定式 Merkle 根：{line}")

    def test_new_constants_are_exported_and_single_sourced(self) -> None:
        for name in ("CID_NO_ANCHOR_NOTE", "CID_SELF_REF_NOTE", "ANCHOR_DEFERRED_NOTE"):
            self.assertIn(name, au.__all__, "措辞单点在 audit.py，compliance 侧引用")
            self.assertTrue(getattr(au, name).strip(), f"{name} 不许被清空")
        src = (ROOT / "agent_explainer" / "compliance.py").read_text(encoding="utf-8")
        self.assertIn("_audit.CID_NO_ANCHOR_NOTE", src, "compliance 不平行造文案：引用 audit 常量")
        self.assertIn("_audit.ANCHOR_HONEST_NOTE", src)
        self.assertEqual(src.count("未锚定（no anchor）"), 1,
                         "compliance 侧只许在 attest 的 hint 里出现这一句，别处一律引用常量")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
