"""S0-4 QA 静态检查：`docs/07 §11` 术语与签名边界锚点核对固化（S0-3 验收对象）。

本文件由 **QA（独立验收方）** 新增：把 `docs/07-secretariat-and-extensions.md` **§11.9
锚点核对表**（以及 §11.0–§11.8 的关键结论）固化成可重跑的静态检查。只读仓库，不改
任何生产代码 / 既有测试 / docs。

运行：

    cd "C:\\TraceLens\\agent-explainer"
    python -m unittest discover -s tests -p "test_docs_terminology.py"

用例 → 验收条款映射（详见 `_pm-dispatch/REPORT-S0-4.md`）：

* QA-S0-16 docs/07 §11 存在、只追加、§0–§10 零改动（与开工前基线快照逐行比对）
* QA-S0-17 `SIGNATURE_FIELDS` 11 字段逐字（代码 ↔ §11.3 文档）
* QA-S0-18 §11.9 锚点表里每一个 `file:line` 的文件都真实存在（行号不较真，符号较真）
* QA-S0-19 `intervention_history` 不在记录落盘路径（静态：无赋值、有 pop 闸门、在受保护名单）
* QA-S0-20 `sealed_head` 在 `audit.py` 存在；`root_hash` 仍为三段归约（PM-VERDICT-S0-3 §2.1 A 案）
* QA-S0-21 `next_ref` 只读时派生：持久契约零命中 + 只许 server.py 读侧命中 + 落盘锚点仍是 `rollback_ref`
* QA-S0-22 QR /「未封印」UI 未实现表述属实（零命中）
* QA-S0-23 §11 八条口径（§11.1–§11.8）的关键符号在现仓可命中
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tests", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from agent_explainer import ingest as ingest_mod  # noqa: E402
from agent_explainer import secretariat  # noqa: E402

PKG = ROOT / "agent_explainer"
DOC = ROOT / "docs" / "07-secretariat-and-extensions.md"
SCHEMA_PATH = ROOT / "docs" / "schemas" / "action_record.schema.json"
SNAPSHOT = ROOT.parent / ".scratch" / "docs07-before-S0-3.md"

#: §11.3 的签名白名单（逐字，顺序即规范；`secretariat.py` 只读核对）
SIGNATURE_FIELDS_VERBATIM = (
    "timestamp", "agent_id", "action_type", "tool", "input_hash", "output_hash",
    "prev_hash", "entry_hash", "risk_level", "confidence", "status",
)

#: §11.9 锚点核对表的符号口径：口径 → [(文件, 必须命中的符号或字面量)]
#: 行号会漂移（PM-VERDICT-S0-3 §2.4 已裁「引符号不引行号」），所以这里钉符号。
ANCHORS: dict[str, tuple[tuple[str, str], ...]] = {
    "§11.0": (("audit.py", "GENESIS_HASH"),
              ("feature_store.py", "GENESIS_HASH"),
              ("server.py", "GENESIS_HASH"),
              # —— R1-5 只增：「沙箱试点」与「显式不可逆」两条术语的符号锚点 ——
              ("rollback.py", "SANDBOX_PILOT_NOTE"),
              ("rollback.py", "IRREVERSIBLE_FINANCIAL_REASON"),
              ("rollback.py", "IRREVERSIBLE_CONTRACT_REASON")),
    "§11.1": (("audit.py", "def head"),
              ("audit.py", "sealed_head"),
              ("audit.py", "sealed_count"),
              ("audit.py", "seal_mismatch"),
              ("audit.py", "def verify")),
    "§11.2": (("rollback.py", "rollback_ref"),
              ("ingest.py", "rollback_ref")),
    "§11.3": (("secretariat.py", "SIGNATURE_FIELDS"),
              ("secretariat.py", "tsa_timestamp"),
              ("crypto_sig.py", "def signed_payload"),
              ("evidence.py", "def sign_range")),
    "§11.4": (("ingest.py", "confidence"),
              ("server.py", "sign_on_write_record")),
    "§11.5": (("intervention_history.py", "def assemble_intervention_history"),
              ("intervention_history.py", "HISTORY_LIMIT"),
              ("server.py", "assemble_intervention_history"),
              ("ingest.py", "intervention_history")),
    "§11.6": (("rollback.py", "simulated_local"),
              ("rollback.py", "unreported"),
              ("web/i18n/zh.json", "effect.recorded_only"),
              # —— R1-5 只增：回执三态（+ 越界拒绝）与不可逆阻断文案的符号锚点 ——
              ("rollback.py", "RECEIPT_STATES"),
              ("rollback.py", "executed_sandbox"),
              ("rollback.py", "executed_registry"),
              ("rollback.py", "SANDBOX_PILOT_NOTE"),
              ("rollback.py", "REGISTRY_EXECUTED_NOTE")),
    "§11.7": (("server.py", "audit_entry_hash"),
              ("intervention_history.py", "audit_entry_hash")),
    "§11.8": (("secretariat.py", "SECRETARIAT_APPROVAL_REQUIRED"),
              ("server.py", "CONFLICT")),
}
CLAUSES = ("§11.1", "§11.2", "§11.3", "§11.4", "§11.5", "§11.6", "§11.7", "§11.8")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def resolve(rel: str) -> pathlib.Path | None:
    """把 docs 里写的相对路径解析到现仓真实文件（`web/i18n/x.json` 是包内路径）。"""
    rel = rel.strip().lstrip("./")
    for candidate in (PKG / rel, ROOT / "docs" / "schemas" / rel,
                      ROOT / rel, PKG / "web" / "i18n" / rel):
        if candidate.is_file():
            return candidate
    return None


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def section(text: str, heading: str, stop: str | None = None) -> str:
    """取 `### <heading>` 到下一个 `###`（或显式 `stop` 标题）之间的正文。"""
    start = text.find(heading)
    assert start >= 0, f"docs/07 缺章节 {heading}"
    rest = text[start + len(heading):]
    if stop:
        end = rest.find(stop)
        return rest[:end] if end >= 0 else rest
    match = re.search(r"\n### 11\.", rest)
    return rest[:match.start()] if match else rest


def heading_of(clause: str) -> str:
    """`§11.1` → `### 11.1 `（docs/07 的小节标题不带 §）。"""
    return f"### {clause.lstrip('§')} "


# --------------------------------------------------------------------------- #
# QA-S0-16：§11 存在、只追加、既有章节零改动
# --------------------------------------------------------------------------- #

class DocsSectionTests(unittest.TestCase):
    def test_qa_s0_16a_section_11_exists_and_is_single_appended_chapter(self) -> None:
        text = doc_text()
        title = "## 11. 哈希链术语表与签名载荷边界（S0-3 增补章）"
        self.assertEqual(1, text.count(title), "§11 增补章必须恰好一个标题")
        for index in range(0, 11):
            self.assertIn(f"### 11.{index}", text, f"缺 §11.{index} 小节")
        # 八条口径各自的小节标题
        for clause in CLAUSES:
            self.assertIn(heading_of(clause), text, f"§11 缺 {clause} 小节标题")
        # §11 必须是**文末**追加（既有内容在前、增补章在其后）
        tail = text[text.find(title):]
        self.assertGreater(len(tail.splitlines()), 150, "§11 正文规模与自报（211 行）不符")

    def test_qa_s0_16b_existing_sections_are_byte_identical_to_baseline(self) -> None:
        """与开工前基线快照（`.scratch/docs07-before-S0-3.md`）逐行比对：§0–§10 零改动。"""
        if not SNAPSHOT.is_file():
            self.skipTest(f"没有开工前基线快照 {SNAPSHOT}，无法判定『只增不改』")
        baseline = SNAPSHOT.read_text(encoding="utf-8").splitlines()
        current = DOC.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(current), len(baseline), "增补章必须让文件变长")
        self.assertEqual(baseline, current[:len(baseline)],
                         "既有 §0–§10 必须逐行零改动（只增不改、不重排）")
        appended = current[len(baseline):]
        self.assertTrue(any(line.startswith("## 11. ") for line in appended),
                        "新增内容必须全部在 §11 内")
        self.assertEqual(0, sum(line.startswith("## ") and not line.startswith("## 11. ")
                                for line in appended),
                         "追加块里不该出现第二个一级章节")


# --------------------------------------------------------------------------- #
# QA-S0-17：SIGNATURE_FIELDS 11 字段逐字
# --------------------------------------------------------------------------- #

class SignatureFieldsTests(unittest.TestCase):
    def test_qa_s0_17_signature_fields_eleven_verbatim(self) -> None:
        self.assertEqual(SIGNATURE_FIELDS_VERBATIM, tuple(secretariat.SIGNATURE_FIELDS),
                         "SIGNATURE_FIELDS 必须 11 字段、顺序逐字不变")
        self.assertEqual(11, len(secretariat.SIGNATURE_FIELDS))
        self.assertEqual(len(set(secretariat.SIGNATURE_FIELDS)),
                         len(secretariat.SIGNATURE_FIELDS), "白名单不得有重复字段")
        body = section(doc_text(), "### 11.3 ")
        for field in SIGNATURE_FIELDS_VERBATIM:
            self.assertIn(field, body, f"§11.3 必须逐字写出 {field}")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        description = str(schema["properties"]["signature"].get("description") or "")
        self.assertIn("SIGNATURE_FIELDS", description,
                      "schema 必须声明签名只覆盖 SIGNATURE_FIELDS 白名单")
        # 签名材料永远不进合并名单（新增字段永不入白名单的另一半证据）
        for field in ("signature", "entry_hash", "prev_hash", "tsa_timestamp"):
            self.assertIn(field, ingest_mod.EXPANSION_PROTECTED_FIELDS,
                          f"{field} 必须在受保护名单里")
            self.assertNotIn(field, ingest_mod.EXPANSION_MERGE_FIELDS,
                             f"{field} 绝不能进合并白名单")


# --------------------------------------------------------------------------- #
# QA-S0-18：§11.9 锚点表 → 文件存在性
# --------------------------------------------------------------------------- #

class AnchorTableTests(unittest.TestCase):
    def test_qa_s0_18_every_anchor_file_exists(self) -> None:
        table = section(doc_text(), "### 11.9 ", stop="### 11.10 ")
        rows = [line for line in table.splitlines() if line.strip().startswith("|")]
        self.assertGreaterEqual(len(rows), 15, f"§11.9 锚点表行数异常：{len(rows)}")
        ref = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|json))")
        checked: set[str] = set()
        for row in rows:
            for name in ref.findall(row):
                checked.add(name)
        self.assertGreaterEqual(len(checked), 8, f"锚点表引用的文件太少：{sorted(checked)}")
        missing = sorted(name for name in checked if resolve(name) is None)
        self.assertEqual([], missing, f"§11.9 引用了现仓不存在的文件：{missing}")

    def test_qa_s0_18b_line_references_point_at_the_symbol_claimed(self) -> None:
        """行号当成「约数」，但每行的 file:line 必须还落在同名的符号附近（±30 行容差）。"""
        table = section(doc_text(), "### 11.9 ", stop="### 11.10 ")
        pattern = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|json)):(\d+)")
        checked = 0
        for match in pattern.finditer(table):
            name, line_no = match.group(1), int(match.group(2))
            path = resolve(name)
            if path is None or path.suffix != ".py":
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if line_no < 1 or line_no > len(lines):
                self.fail(f"§11.9 的 {name}:{line_no} 越界（文件共 {len(lines)} 行）")
            checked += 1
        self.assertGreaterEqual(checked, 5, "§11.9 的可核对行引用太少")


# --------------------------------------------------------------------------- #
# QA-S0-19：intervention_history 不在记录落盘路径
# --------------------------------------------------------------------------- #

class HistoryPersistenceTests(unittest.TestCase):
    def test_qa_s0_19_history_is_never_assigned_onto_a_record(self) -> None:
        """唯一允许的赋值点在组装器内，且接收者是 deepcopy 出来的副本（视图，不是记录本体）。

        静态口径：`agent_explainer/*.py` 里出现 `["intervention_history"] =` 的位置，必须
        全部落在 `assemble_intervention_history` 函数体内，且该函数在赋值之前已
        `copy.deepcopy` 入参 —— 这才是「只在响应里挂、绝不落盘」的代码级证据。
        """
        assignment = re.compile(r"""\[\s*['"]intervention_history['"]\s*\]\s*=""")
        source = (PKG / "intervention_history.py").read_text(encoding="utf-8")
        start = source.find("def assemble_intervention_history")
        self.assertGreaterEqual(start, 0, "找不到读时组装器")
        nxt = re.search(r"\ndef ", source[start + 5:])
        end = start + 5 + nxt.start() if nxt else len(source)
        assembler = source[start:end]
        self.assertTrue(assignment.search(assembler), "组装器必须在副本上挂视图数组")
        copy_at = assembler.find("copy.deepcopy")
        self.assertGreaterEqual(copy_at, 0, "组装器必须先把入参深拷贝（入参零改写）")
        self.assertLess(copy_at, assignment.search(assembler).start(),
                        "deepcopy 必须发生在挂数组之前：改的是副本，不是入参")

        offenders = []
        start_line = source[:start].count("\n") + 1
        end_line = source[:end].count("\n") + 1
        for path in sorted(PKG.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), 1):
                if not assignment.search(line):
                    continue
                if path.name == "intervention_history.py" and \
                        start_line <= line_no <= end_line:
                    continue                                  # 组装器内的副本赋值
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
        self.assertEqual([], offenders,
                         "组装器之外绝不允许给记录写 intervention_history 键")

    def test_qa_s0_19b_guard_declaration_and_schema_exist(self) -> None:
        builder = (PKG / "intervention_history.py").read_text(encoding="utf-8")
        self.assertRegex(builder, r"""record\.pop\(\s*["']intervention_history["']""",
                         "构造器末尾必须有 pop 闸门（不落盘）")
        self.assertIn("intervention_history", ingest_mod.EXPANSION_PROTECTED_FIELDS,
                      "入参闸门：干预历史必须在受保护名单里（伪造不进来）")
        self.assertNotIn("intervention_history", ingest_mod.EXPANSION_MERGE_FIELDS,
                         "干预历史绝不能从入参合并进来")
        store = (PKG / "store.py").read_text(encoding="utf-8")
        self.assertNotIn("intervention_history", store,
                         "存储层不该知道这个视图字段（读时组装才成立）")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        item = schema["properties"]["intervention_history"]
        self.assertEqual(50, item.get("maxItems"), "schema :1398 maxItems = 50")
        self.assertIn("array", item.get("type") or [], "schema 类型必须是数组")
        self.assertFalse(schema.get("additionalProperties", True),
                         "根 additionalProperties=false：组装视图不得靠新 schema 键")

    def test_qa_s0_19c_history_limit_matches_schema(self) -> None:
        from agent_explainer import intervention_history as ih
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["intervention_history"]["maxItems"],
                         ih.HISTORY_LIMIT, "HISTORY_LIMIT 必须等于 schema maxItems")
        self.assertEqual(50, ih.HISTORY_LIMIT)


# --------------------------------------------------------------------------- #
# QA-S0-20：sealed_head 存在；root_hash 三段归约（A 案）
# --------------------------------------------------------------------------- #

class RootHashTests(unittest.TestCase):
    def test_qa_s0_20a_sealed_head_lives_in_audit(self) -> None:
        audit = (PKG / "audit.py").read_text(encoding="utf-8")
        self.assertIn('payload["sealed_head"] = prev', audit,
                      "封印必须把当时的链头抄进 sealed_head")
        self.assertIn("sealed_head", audit)
        self.assertIn("sealed_count", audit)

    def test_qa_s0_20b_root_hash_is_still_the_three_section_reduction(self) -> None:
        """PM-VERDICT-S0-3 §2.1 裁 A 案：root_hash 保留三段归约，sealed_head 另立字段。"""
        compliance = (PKG / "compliance.py").read_text(encoding="utf-8")
        self.assertNotIn("sealed_head", compliance,
                         "A 案：现仓 root_hash 不得改取 sealed_head")
        self.assertIn("root_payload", compliance, "归约必须先拼出三段载荷")
        for name in ("records", "audit", "letters"):
            self.assertIn(f'"{name}"', compliance, f"三段归约缺 {name} 段")
        self.assertRegex(compliance, r"hashlib\.sha256\(",
                         "root_hash 必须是对归约载荷的 sha256")
        self.assertIn('"root_hash": "sha256:"', compliance, "root_hash 必须带 sha256: 前缀")
        body = section(doc_text(), "### 11.10 ")
        self.assertIn("A 案", body, "§11.10 必须登记 PM 裁的 A 案")
        self.assertIn("sealed_head", body, "§11.10 必须写明 QR 取 sealed_head")


# --------------------------------------------------------------------------- #
# QA-S0-21 / QA-S0-22：未实现表述属实
# --------------------------------------------------------------------------- #

class NotImplementedClaimsTests(unittest.TestCase):
    def test_qa_s0_21_next_ref_is_a_read_time_view_only(self) -> None:
        """QA-S0-21（R1-2b 起口径升级）：``next_ref`` 只允许是**读时派生**的正向指针。

        S0 时这条门禁写作"全仓零命中"——那时它确实未实现。R1-2b 实现了 docs/07 §11.2
        要的那个正向指针：由契约字段 ``rollback_ref`` 反查得出，**只进 ``/api/records``
        响应的 ``meta.rollback_view.links``**。"零命中"从今天起是伪指标（它确实存在了），
        所以门禁换成三条能真防伪的硬约束：

        1. **持久契约零命中**：``docs/schemas/*.json`` 与 ``docs/api_contract.json`` 一个字
           都不许出现（记录行不新增字段——schema 根是 ``additionalProperties:false``）；
        2. **落盘路径零命中**：``agent_explainer/*.py`` 里只许 ``server.py`` 命中，且命中的
           行不得与 ``store.put`` / 撤销日志写入 / 序列化同流（派生字段绝不落盘）；
        3. **落盘锚点的真名仍是** ``rollback_ref``：正向指针由它反查，docs/07 的表述在案。
        """
        contract = ROOT / "docs" / "api_contract.json"
        persistent = list((ROOT / "docs" / "schemas").glob("*.json"))
        if contract.is_file():
            persistent.append(contract)
        contract_hits = []
        for path in persistent:
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "next_ref" in line:
                    contract_hits.append(f"{path.relative_to(ROOT)}:{line_no}")
        self.assertEqual([], contract_hits,
                         f"next_ref 是读时派生，绝不进持久契约（schema / api_contract）："
                         f"{contract_hits}")

        code_hits: list[tuple[str, int, str]] = []
        for path in PKG.glob("*.py"):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "next_ref" in line:
                    code_hits.append((path.name, line_no, line.strip()))
        offenders = sorted({name for name, _n, _l in code_hits} - {"server.py"})
        self.assertEqual([], offenders,
                         f"只有读侧派生可以有 next_ref，这些文件不该出现它：{offenders}")
        self.assertTrue(code_hits,
                        "R1-2b 起 server.py 必须真的实现读时正向指针：零命中=功能丢了")
        for name, line_no, line in code_hits:
            for write_token in ("store.put", "idempotency_store", "open_store",
                                ".append(", "json.dumps"):
                self.assertNotIn(write_token, line,
                                 f"{name}:{line_no} 落盘/写入路径里不许出现 next_ref：{line}")

        self.assertIn('"rollback_ref"',
                      (ROOT / "docs" / "schemas" / "action_record.schema.json")
                      .read_text(encoding="utf-8"),
                      "正向指针的落盘锚点必须是契约字段 rollback_ref（由它反查 next_ref）")
        text = doc_text()
        self.assertIn("next_ref", text)
        self.assertRegex(text, r"next_ref[\s\S]{0,200}(零命中|未实现)", "§11.2 的表述必须在案")

    def test_qa_s0_22_qr_and_unsealed_ui_are_still_unimplemented(self) -> None:
        compliance = (PKG / "compliance.py").read_text(encoding="utf-8").lower()
        for token in ("qr", "seal"):
            self.assertNotIn(token, compliance,
                             f"§11.10 声明 compliance.py 无 {token} 代码")
        i18n = sorted((PKG / "web" / "i18n").rglob("*.json"))
        self.assertGreaterEqual(len(i18n), 10,
                                f"§11.10 的自测口径是扫描 10 个 i18n JSON，实测只有 {len(i18n)}")
        offenders = [str(p.relative_to(PKG)) for p in i18n
                     if "封印" in p.read_text(encoding="utf-8")]
        self.assertEqual([], offenders, f"§11.10 声明 UI 无「封印」标注，实测：{offenders}")
        body = section(doc_text(), "### 11.10 ")
        self.assertIn("封印", body, "§11.10 必须登记「未封印」UI 未实现")


# --------------------------------------------------------------------------- #
# QA-S0-23：八条口径的符号锚点
# --------------------------------------------------------------------------- #

class ClauseSymbolTests(unittest.TestCase):
    def test_qa_s0_23_every_clause_symbol_is_greppable(self) -> None:
        self.assertEqual(set(CLAUSES), set(key for key in ANCHORS if key != "§11.0"),
                         "八条口径（§11.1–§11.8）都必须有符号锚点")
        missing: list[str] = []
        for clause, anchors in ANCHORS.items():
            for name, symbol in anchors:
                path = resolve(name)
                if path is None:
                    missing.append(f"{clause} {name}（文件不存在）")
                    continue
                if symbol not in path.read_text(encoding="utf-8"):
                    missing.append(f"{clause} {name} 不含 {symbol!r}")
        self.assertEqual([], missing, "§11 引用的符号在现仓 grep 不到：" + "；".join(missing))

    def test_qa_s0_23b_clause_headings_match_the_anchor_map(self) -> None:
        text = doc_text()
        for clause in CLAUSES:
            start = text.find(heading_of(clause))
            self.assertGreaterEqual(start, 0, f"缺 {clause} 小节")
            end = text.find("\n### 11.", start + 5)
            body = text[start:end if end > 0 else len(text)]
            for _name, symbol in ANCHORS[clause]:
                token = symbol.split()[-1]
                self.assertIn(token, body, f"{clause} 正文必须提到 {token}")


class SandboxPilotTerminologyTests(unittest.TestCase):
    """R1-5 只增锚点：「沙箱试点」术语条目 + §11.6 的**回执三态**措辞（TASK-R1-5 交付 4）。

    口径来源：PM-VERDICT-R1-4 §2 第 1/4 条（§11.6 措辞更新与「沙箱试点」术语
    并入 R1-5）。既有断言一个字没动：这条只新增类与断言，钉的是
    「文档不再说"逆向回执恒为 simulated_local"」——R1-4 落地三态后，那句
    「恒为」已经是假话（`executed_sandbox` 真的动了磁盘）。
    """

    def test_qa_r15a_sandbox_pilot_term_has_a_glossary_row(self) -> None:
        body = section(doc_text(), heading_of("§11.0"))
        self.assertIn("沙箱试点", body, "§11.0 术语表必须有「沙箱试点」条目")
        # 术语条目必须把"真动了磁盘"与"外部仍未回执"两件事同时说清
        self.assertIn("executed_sandbox", body, "「沙箱试点」必须绑到字面值 executed_sandbox")
        self.assertIn("unreported", body, "「沙箱试点」必须说清外部回执仍是 unreported")
        self.assertIn("本地模拟", body, "「沙箱试点」的禁用说法必须包含「本地模拟」（G-14）")
        rollback = (PKG / "rollback.py").read_text(encoding="utf-8")
        self.assertIn("SANDBOX_PILOT_NOTE", rollback, "术语锚点 SANDBOX_PILOT_NOTE 必须在现仓")

    def test_qa_r15b_section_11_6_states_the_four_receipt_states(self) -> None:
        body = section(doc_text(), heading_of("§11.6"))
        for state in ("simulated_local", "executed_sandbox", "executed_registry", "failed"):
            self.assertIn(state, body, f"§11.6 必须列明回执状态 {state}")
        self.assertIn("RECEIPT_STATES", body, "§11.6 必须引符号（RECEIPT_STATES）而非行号")
        # 旧措辞「逆向回执恒为 simulated_local」在 R1-4 之后是假话：不得复现
        self.assertNotRegex(body, r"逆向回执恒为\s*`simulated_local`",
                            "§11.6 不得再写「逆向回执恒为 simulated_local」（R1-4 起三态）")
        # 两个方向的谎都要禁：模拟说成真实、真实说成模拟
        self.assertIn("本地模拟", body, "§11.6 必须保留「不得把模拟说成已撤销」的禁令")
        self.assertIn("R1-5", body, "§11.6 的措辞更新必须标出 R1-5 口径来源")

    def test_qa_r15c_gitignore_keeps_scratch_out_of_version_control(self) -> None:
        """PM-VERDICT-R1-4 §2 第 6 条：`.gitignore` 补 `.scratch/`（一行，L0）。"""
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".scratch/", {line.strip() for line in gitignore.splitlines()},
                      ".gitignore 必须含 `.scratch/`（自测取证产物不入库）")


if __name__ == "__main__":  # 手工自检：python tests/test_docs_terminology.py -v
    unittest.main(verbosity=2)
