#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出包独立验证脚本（REQ-008 / B8-4；R8-4 交付物）。

**纯标准库自包含**：只依赖 Python 标准库（argparse / csv / hashlib / io / json /
pathlib / re / sys / datetime）。**不 import 本仓任何模块**（无 ``agent_explainer``、
无 ``from agent_explainer import …``），不把仓库源码当运行时依赖：把本文件单独复制到
仓库外的干净目录，只带标准库即可 ``python verify_export.py --self-test`` 跑通。
禁止第三方包。测试临时目录纪律不适用（本脚本**不写任何文件**，只读 + 打印）。

口径冻结版本（CALIBER，口径一旦冻结不得随报告措辞改动）
----------------------------------------------------------
``CALIBER = "TraceLens export caliber R8-4 / 2026-11-15"``
等价重算的对象是本仓 ``agent_explainer/audit.py`` 的**冻结实现**（只读核对，零改动）：

1. ``canonical_json`` ≡ ``audit.py:330-333`` ——
   ``json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
   default=_json_default)``：
   * **键排序**：``sort_keys=True``（嵌套 dict 同样排序，逐层递归由 json 模块负责）；
   * **分隔符**：``(",", ":")``，即零多余空白（``{"a":1,"b":[1,2]}``）；
   * **编码**：``ensure_ascii=False``，中文 / emoji 原样输出，不落 ``\\uXXXX``；
     字节层是 UTF-8（``sha256(text.encode("utf-8"))``）；
   * **排除键**：算链哈希时先剥掉条目自身的 ``hash`` / ``entry_hash`` / ``prev_hash``
     ≡ ``audit.py:354-356`` ``_chain_payload``；
   * **不可序列化值的兜底** ≡ ``audit.py:336-347`` ``_json_default``：
     ``datetime`` → ``iso_utc``（UTC + ``timespec="seconds"``，``+00:00`` 换成 ``Z``）；
     ``set`` / ``frozenset`` → ``sorted(value, key=str)``；
     ``bytes`` / ``bytearray`` → ``"sha256:" + sha256(字节).hexdigest()``；
     ``pathlib.PurePath`` → ``str(value)``；其余类型 → 抛错（不静默凑一个值）。
2. ``entry_hash`` ≡ ``audit.py:359-361`` ——
   ``"sha256:" + sha256( prev_hash + "\\n" + canonical_json(_chain_payload(payload)) )``；
   前驱常量 ``GENESIS_HASH = "sha256:" + "0"*64``（≡ ``audit.py:92-93``）。
3. 链校验 ≡ ``audit.py:1325-1397`` ``_verify_locked``：逐行读（``utf-8`` 行迭代 +
   ``rstrip("\\n")``，≡ ``audit.py:1275-1282``），逐条复算，**问题码逐字同名**：
   ``blank_line`` / ``unparsable_line`` / ``entry_not_object`` / ``chain_break`` /
   ``hash_mismatch`` / ``alias_mismatch`` / ``seq_gap`` / ``seal_mismatch`` /
   ``seal_count_mismatch`` / ``head_mismatch`` / ``count_mismatch``。
   验收时 PM 用 ``agent_explainer.audit.verify_file`` 交叉复跑一次（PM-PLAN §10.5）：
   本脚本链路段判定与本仓判定必须一致，不一致**如实申报**、不自行改口径。

术语与展示纪律（PM-PLAN §2.2 / §2.3、HANDOFF §4 既定 A 案）
--------------------------------------------------------------
* 「哈希链根哈希」≡ ``sealed_head``：``seal()`` 当时写下的**封印锚点哈希**；
  取**最后一次**封印（它覆盖到导出时的整段历史，是最强锚点；更早的封印仍逐条核对）。
* 链尖（``head`` = 最新一条的哈希）与封印锚点（``sealed_head``）**是两个值**，本脚本
  分行输出，不混用、不拿链尖顶替封印锚点。
* 三段归约 ``root_hash``（records/audit/letters 逐条哈希的归约值）**非链头、非 Merkle 根**：
  本脚本只验审计链导出面，出现 ``root_hash`` 时**必须**带该限定语才算 PASS。
* 诚实态（0.4）：``eth_tx_hash`` 恒 ``None``、``anchor_state`` 恒 ``"unavailable"``。
  本脚本输出里**永不**出现「已恢复 / 已撤销 / 已上链 / 已冲正完成」等完成态措辞，
  「Merkle 根」只允许以否定式（``非 Merkle 根``）出现。
* 两层账本边界：本脚本只验**审计链导出面**，不跨账本逐字比对 secretariat 签名账本
  （``SIGNATURE_FIELDS`` 11 项），不把审计链头说成签名背书，不宣称
  「签名与哈希字段全集已覆盖」。「验签通过≠背书齐备」两句随输出披露。

能验什么 / 不能验什么（不冒充）
--------------------------------
能验：逐条 ``canonical_json`` 重算 → sha256 → 与 ``entry_hash`` 逐字相等；
``prev_hash`` 链连续性（含 seq 跳号）；封印锚点与当时链头、封印条数；外部锚点
（``--expected-head`` / ``--expected-count``）；CSV 表头恒 **24 列**（逐字）+ BOM +
``\\r\\n`` + 逐格与链上同值 + 尾列 ``sealed_head`` 逐字一致；导出包 meta / 责任认定书
正文的「链头行与 QR 载荷同源同值」与诚实态字段；``--self-test`` 内置最小样例
（4 行真实链：3 条业务条目 + 1 条 seal，由本仓 ``audit`` 生成后冻结为字面量）。

不能验（如实申报，不折进「全绿」）：CSV 导入 ELK/Splunk 的实机导入、PDF 二维码的实机
扫码、干净环境（仓库外）实跑、secretariat 签名验签——这些归 R8-5~R8-7（PM 亲自）。

用法
----
::

    python tools/verify_export.py --chain var/log/audit.jsonl
    python tools/verify_export.py --chain audit.jsonl --csv audit.csv \\
        --meta cert.json --expected-head sha256:... --expected-count 4
    python tools/verify_export.py --chain audit.jsonl --json      # 机器读
    python tools/verify_export.py --self-test                     # 内置样例自验
    python tools/verify_export.py --help                          # 参数逐项说明

参数逐项（与 ``--help`` 一致）：
``--chain FILE``  审计链导出件（JSONL，一行一条，链哈希写在行内）；``--self-test`` 时可省。
``--csv FILE``    同一次导出的 CSV（表头须逐字 24 列；给不给都如实登记，不给则该段 SKIP）。
``--meta FILE``   导出包 meta / 责任认定书 JSON（链头行与 QR 载荷同源同值 + 诚实态字段）。
``--expected-head sha256:…``  外部抄存的链头锚点；不符报 ``head_mismatch``（整段重算历史
                              在链内自洽，只有对上外部锚点才算安全）。
``--expected-count N``        外部抄存的条数锚点；不符报 ``count_mismatch``。
``--json``        只打印机器读 JSON（逐条判定可机器复核；退出码不变）。
``--quiet``       只打印汇总行与 FAIL 行（人读模式下的降噪，不改判定）。
``--self-test``   跑内置最小样例 + 边界案例 + 篡改注入自证，不读任何文件。

退出码：``0`` = 全部检查 PASS（SKIP 只登记、不算失败）；``1`` = 任一检查 FAIL
（验证失败，**非零退出 + 明确原因码**，不吞异常、不把 warn 折成 PASS）；
``2`` = 用法/IO 错误（文件读不到、参数不合法——这不是「验证失败」，是「没验成」）。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# 口径冻结（改动即口径漂移，必须回 PM 裁决，不在脚本里自行放宽）
# --------------------------------------------------------------------------- #

CALIBER: str = "TraceLens export caliber R8-4 / 2026-11-15"
CALIBER_SOURCE: str = ("agent_explainer/audit.py:92-93(GENESIS) 330-361"
                       "(canonical_json/_json_default/sha256_ref/_chain_payload/"
                       "entry_hash) 1275-1397(_lines_locked/_verify_locked) "
                       "1417-1429(CSV_COLUMNS) — 只读等价重算，零改动")
HASH_PREFIX: str = "sha256:"
GENESIS_HASH: str = HASH_PREFIX + "0" * 64

#: CSV 表头基线 ≡ ``audit.py:1417-1429`` ``CSV_COLUMNS``，**恒 24 列**（R8-2 收口）。
#: 这里冻结一份字面量（不 import 过去），钉测逐字比对两边，防口径漂移。
CSV_COLUMNS_24: tuple[str, ...] = (
    "seq", "ts", "actor_id", "actor_role", "task_id", "action_id", "decision",
    "option_id", "event", "request_id", "original_params", "modified_params",
    "changed_fields", "reason", "client_info", "record_status_before",
    "record_status_after", "mode", "prev_hash", "hash",
    "entry_hash", "record_facts_hash", "source_facts_hash", "sealed_head",
)
CSV_BOM: str = "\ufeff"

#: 链哈希计算时剥掉的键（≡ audit.py:354-356）。
EXCLUDED_HASH_KEYS: tuple[str, ...] = ("hash", "entry_hash", "prev_hash")

#: 展示纪律禁语（0.4 / HANDOFF §4）：导出件正文里出现即判 FAIL。
FORBIDDEN_WORDS: tuple[str, ...] = ("已恢复", "已撤销", "已上链", "已冲正完成")
MERKLE_NOTE: str = "非 Merkle 根"
#: 「Merkle 根」只允许否定式出现：紧邻上文里必须有下面任一个否定标记。
MERKLE_NEGATIONS: tuple[str, ...] = ("非", "不是", "并非", "无", "没有", "未")
#: 三段归约限定语（PM-PLAN §2.2：``root_hash`` 非链头、非 Merkle 根）。
REDUCTION_QUALIFIER: str = "非链头"
#: 「验签通过≠背书齐备」两句（随输出披露，两层账本不混为一谈）。
DISCLOSURE: tuple[str, ...] = (
    "验签通过≠背书齐备。",
    "本文件的哈希与二维码只证明内容可离线复算，不构成 secretariat 签名账本的背书。",
)
HONEST_ANCHOR_STATE: str = "unavailable"

#: 链校验问题码（≡ audit.py:1325-1397 的码名，逐字同名，PM 交叉复跑按名比对）。
CHAIN_CODES: tuple[str, ...] = ("blank_line", "unparsable_line", "entry_not_object",
                                "chain_break", "hash_mismatch", "alias_mismatch",
                                "seq_gap", "seal_mismatch", "seal_count_mismatch",
                                "head_mismatch", "count_mismatch")
#: 判据分组：一条问题只让它该管的那一项 FAIL，不拿锚点问题去脏哈希复算的判定
#: （各判据的 codes 一眼能看出「哪项失败、为什么失败」）。
HASH_CODES: tuple[str, ...] = ("hash_mismatch", "alias_mismatch")
CONTINUITY_CODES: tuple[str, ...] = ("blank_line", "unparsable_line", "entry_not_object",
                                     "chain_break", "seq_gap")
SEAL_CODES: tuple[str, ...] = ("seal_mismatch", "seal_count_mismatch")
HEAD_CODE: str = "head_mismatch"
COUNT_CODE: str = "count_mismatch"


# --------------------------------------------------------------------------- #
# 等价重算：canonical_json / sha256_ref / entry_hash（口径见模块 docstring）
# --------------------------------------------------------------------------- #


class CaliberError(Exception):
    """口径层拒绝：值不可序列化、锚点格式不合法等（不静默凑一个值）。"""


def iso_utc(stamp: datetime) -> str:
    """≡ ``audit.py:368-370``：UTC ISO，``seconds`` 精度，``+00:00`` → ``Z``。"""
    text = stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _json_default(value: object) -> object:
    """≡ ``audit.py:336-347`` 的兜底转换（逐条对齐，不新增类型口子）。"""
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, (bytes, bytearray)):
        return HASH_PREFIX + hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, pathlib.PurePath):
        return str(value)
    raise CaliberError(f"口径不接受 {type(value).__name__} 类型的值（只写标量/list/dict）")


def canonical_json(value: object) -> str:
    """≡ ``audit.py:330-333``：排序键、零多余空白、``ensure_ascii=False``。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=_json_default)


def sha256_ref(text: str) -> str:
    """≡ ``audit.py:350-351``。"""
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_payload(entry: dict) -> dict:
    """≡ ``audit.py:354-356``：剥掉自身/前驱哈希后的载荷。"""
    return {k: v for k, v in entry.items() if k not in EXCLUDED_HASH_KEYS}


def entry_hash(payload: dict, prev_hash: str) -> str:
    """≡ ``audit.py:359-361``：``sha256(prev_hash + "\\n" + canonical_json(payload))``。"""
    return sha256_ref(prev_hash + "\n" + canonical_json(chain_payload(payload)))


# --------------------------------------------------------------------------- #
# 链校验（≡ audit.py:1325-1397；问题码逐字同名，逐条判定可机器复核）
# --------------------------------------------------------------------------- #


def read_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """≡ ``audit.py:1275-1282``：``utf-8`` 行迭代 + ``rstrip("\\n")``，行号从 0 起。"""
    if not path.exists():
        raise CaliberError(f"读不到文件：{path}")
    out: list[tuple[int, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, raw in enumerate(handle):
            out.append((index, raw.rstrip("\n")))
    return out


def verify_chain(lines: list[tuple[int, str]], *, expected_head: str | None = None,
                 expected_count: int | None = None) -> dict:
    """逐条复算哈希链；问题码与顺序逐字对齐本仓 ``_verify_locked``。"""
    problems: list[dict] = []
    prev = GENESIS_HASH
    expect_seq = 1
    count = 0
    head = GENESIS_HASH
    seals = 0
    seals_detail: list[dict] = []
    latest_sealed_head: str | None = None
    latest_sealed_source: str | None = None
    entries: list[dict] = []
    for index, raw in lines:
        if not raw.strip():
            problems.append({"code": "blank_line", "index": index, "seq": None,
                             "reason": "审计文件里出现空行"})
            continue
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            problems.append({"code": "unparsable_line", "index": index, "seq": None,
                             "reason": f"第 {index + 1} 行不是 JSON：{exc}"})
            continue
        if not isinstance(entry, dict):
            problems.append({"code": "entry_not_object", "index": index, "seq": None,
                             "reason": "条目不是对象"})
            continue
        count += 1
        seq = entry.get("seq")
        own = entry.get("hash")
        alias = entry.get("entry_hash")
        if entry.get("prev_hash") != prev:
            problems.append({"code": "chain_break", "index": index, "seq": seq,
                             "reason": f"第 {index + 1} 条的 prev_hash 接不上前一条"})
        recomputed = entry_hash(entry, prev)
        if not isinstance(own, str) or own != recomputed:
            problems.append({"code": "hash_mismatch", "index": index, "seq": seq,
                             "reason": f"第 {index + 1} 条内容被改过（哈希复算不一致）：期望 "
                                       f"{recomputed}，实测 {own!r}"})
        if isinstance(own, str) and isinstance(alias, str) and own != alias:
            problems.append({"code": "alias_mismatch", "index": index, "seq": seq,
                             "reason": "hash 与 entry_hash 别名不一致"})
        if seq != expect_seq:
            problems.append({"code": "seq_gap", "index": index, "seq": seq,
                             "reason": f"序号跳号：期望 {expect_seq}，实际 {seq}"})
        if entry.get("event") == "seal":
            seals += 1
            sealed_head = entry.get("sealed_head")
            sealed_count = entry.get("sealed_count")
            if sealed_head != prev:
                problems.append({"code": "seal_mismatch", "index": index, "seq": seq,
                                 "reason": f"第 {index + 1} 条封印记的链头与当前链不符"
                                           "（历史被整段改写）：期望 {prev}，实测 {sealed_head!r}"})
            if sealed_count != expect_seq - 1:
                problems.append({"code": "seal_count_mismatch", "index": index, "seq": seq,
                                 "reason": f"第 {index + 1} 条封印记的条数与当前不符：期望 "
                                           f"{expect_seq - 1}，实际 {sealed_count}"})
            if isinstance(sealed_head, str) and sealed_head.strip():
                latest_sealed_head = sealed_head.strip()
                latest_sealed_source = (f"第 {seq} 条 seal 事件"
                                        f"（sealed_count={sealed_count}）")
            seals_detail.append({"index": index, "seq": seq, "sealed_head": sealed_head,
                                 "sealed_count": sealed_count,
                                 "matches_prev_at_that_point": sealed_head == prev})
        head = own if isinstance(own, str) else head
        prev = own if isinstance(own, str) else prev
        expect_seq += 1
        entries.append(entry)
    if expected_head and head != expected_head:
        problems.append({"code": "head_mismatch", "index": count - 1, "seq": None,
                         "reason": "链头与外部锚点不符（历史被改写或被截断）："
                                   f"锚点 {expected_head}，实测 {head}"})
    if expected_count is not None and count != int(expected_count):
        problems.append({"code": "count_mismatch", "index": count, "seq": None,
                         "reason": f"条数与外部锚点不符：期望 {expected_count}，实际 {count}"})
    return {"ok": not problems, "count": count, "head": head, "genesis": GENESIS_HASH,
            "lines": len(lines), "seals": seals, "problems": problems,
            "seals_detail": seals_detail, "entries": entries,
            "latest_sealed_head": latest_sealed_head,
            "latest_sealed_source": latest_sealed_source}


# --------------------------------------------------------------------------- #
# CSV 面（表头恒 24 列 / BOM / \\r\\n / 逐格与链上同值 / 尾列 sealed_head 逐字一致）
# --------------------------------------------------------------------------- #


def _cell_text(value: object) -> str:
    """≡ ``audit.py:1440-1447``：链上算出的规范文本（逐格比对用同一口径）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return canonical_json(value)
    return str(value)


def check_csv(path: pathlib.Path | str, chain: dict) -> dict:
    """CSV 面逐条判定；表头逐字 24 列是硬判据，缺表头列一律 FAIL。"""
    problems: list[dict] = []
    path = pathlib.Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    bom = raw.startswith(CSV_BOM.encode("utf-8"))
    crlf = ("\r\n" in text) and ("\n" not in text.replace("\r\n", ""))
    if not bom:
        problems.append({"code": "csv_bom_missing",
                         "reason": "CSV 缺 UTF-8 BOM（R8-2 基线 bom=True，Excel 认中文）"})
    if not crlf:
        problems.append({"code": "csv_line_terminator",
                         "reason": "CSV 行尾不是统一 \\r\\n（R8-2 基线 lineterminator='\\r\\n'）"})
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        problems.append({"code": "csv_empty", "reason": "CSV 是空的，表头都没有"})
        return {"ok": False, "header": [], "columns": 0, "rows": 0,
                "bom": bom, "crlf": crlf, "problems": problems}
    header = rows[0]
    if tuple(header) != CSV_COLUMNS_24:
        missing = [c for c in CSV_COLUMNS_24 if c not in header]
        extra = [c for c in header if c not in CSV_COLUMNS_24]
        moved = [i for i, name in enumerate(header)
                 if i < len(CSV_COLUMNS_24) and name != CSV_COLUMNS_24[i]]
        problems.append({"code": "csv_header_mismatch",
                         "reason": f"CSV 表头与 24 列基线不逐字相等：列数 {len(header)}、"
                                   f"缺 {missing}、多 {extra}、位次不符 {moved}"})
    body = rows[1:]
    if len(body) != chain["count"]:
        problems.append({"code": "csv_row_count",
                         "reason": f"CSV 数据行数 {len(body)} 与链条数 {chain['count']} 不等"})
    by_seq = {e.get("seq"): e for e in chain["entries"]}
    sealed_in_csv: dict[int, str] = {}
    if "sealed_head" in header:
        col = header.index("sealed_head")
        for row in body:
            if len(row) > col and row[col].strip():
                try:
                    seq = int(row[header.index("seq")]) if "seq" in header else None
                except ValueError:
                    seq = None
                sealed_in_csv[seq if seq is not None else -1] = row[col]
    for seq, value in sealed_in_csv.items():
        entry = by_seq.get(seq)
        if not isinstance(entry, dict) or entry.get("event") != "seal":
            problems.append({"code": "csv_sealed_head_orphan",
                             "reason": f"CSV 第 seq={seq} 行的 sealed_head 非空，但链上"
                                       "该 seq 不是 seal 条目（尾列被写过）"})
            continue
        if entry.get("sealed_head") != value:
            problems.append({"code": "csv_sealed_head_mismatch",
                             "reason": f"CSV 尾列 sealed_head 与链上 seal 条目不逐字相等"
                                       f"（seq={seq}）：链上 {entry.get('sealed_head')}，"
                                       f"CSV {value}"})
    for name in ("prev_hash", "hash", "entry_hash"):
        if name not in header:
            continue
        col = header.index(name)
        for row in body:
            if "seq" not in header or len(row) <= col:
                continue
            try:
                seq = int(row[header.index("seq")])
            except ValueError:
                continue
            entry = by_seq.get(seq)
            if not isinstance(entry, dict):
                continue
            expect = _cell_text(entry.get(name))
            if row[col] != expect:
                problems.append({"code": f"csv_{name}_mismatch",
                                 "reason": f"CSV 列 {name} 与链上条目不逐字相等（seq={seq}）："
                                           f"链上 {expect!r}，CSV {row[col]!r}"})
    return {"ok": not problems, "header": header, "columns": len(header),
            "rows": len(body), "bom": bom, "crlf": crlf,
            "sealed_head_values": sorted(sealed_in_csv.items(), key=lambda kv: str(kv[0])),
            "problems": problems}


# --------------------------------------------------------------------------- #
# 导出包 meta / 责任认定书正文（R8-3 产物）：链头行与 QR 载荷同源同值 + 诚实态
# --------------------------------------------------------------------------- #


def _cert_of(meta: dict) -> dict:
    """meta 三种可接受形状里取认定书数据本体（R8-3 ``build_certificate`` 输出同构）。"""
    if isinstance(meta.get("certificate"), dict):
        return meta["certificate"]
    return meta


def _text_lines(meta: dict, cert: dict) -> list[str]:
    for src in (meta.get("certificate_text"), cert.get("certificate_text"),
                meta.get("cert_text"), meta.get("text")):
        if isinstance(src, list) and src:
            return [str(x) for x in src]
    return []


def check_meta(path: pathlib.Path | str, chain: dict) -> dict:
    """核 meta / 认定书：链头行与 QR 载荷同源同值 + 诚实态 + 禁语 + 三段归约限定语。"""
    problems: list[dict] = []
    notes: list[str] = []
    path = pathlib.Path(path)
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"ok": False, "problems": [{"code": "meta_unparsable",
                                           "reason": f"meta 不是合法 JSON：{exc}"}],
                "cert": {}, "text_lines": 0, "notes": notes}
    if not isinstance(meta, dict):
        return {"ok": False, "problems": [{"code": "meta_not_object",
                                           "reason": "meta 顶层不是对象"}],
                "cert": {}, "text_lines": 0, "notes": notes}
    cert = _cert_of(meta)
    text_lines = _text_lines(meta, cert)

    sealed_head = cert.get("sealed_head")
    qr_payload = cert.get("qr_payload")
    # ① qr_payload 与 sealed_head 同源同值（R8-3 构造：两处由同一个字符串来）
    if qr_payload != sealed_head:
        problems.append({"code": "meta_qr_not_same_source",
                         "reason": f"meta 的 qr_payload 与 sealed_head 不逐字相等："
                                   f"sealed_head={sealed_head!r}，qr_payload={qr_payload!r}"})
    # ② 与链上最后一次封印的锚点比对（链上没有 seal → 必须留空，不得拿别的哈希顶替）
    if chain["latest_sealed_head"] is None:
        if sealed_head not in (None, ""):
            problems.append({"code": "meta_sealed_head_forged",
                             "reason": f"链上无任何 seal 条目，meta 却写了 sealed_head="
                                       f"{sealed_head!r}（不得拿链尖/别的哈希顶替）"})
        notes.append("链上没有 seal 条目：认定书应写「无封印锚点，二维码不可用」")
    elif sealed_head != chain["latest_sealed_head"]:
        problems.append({"code": "meta_sealed_head_mismatch",
                         "reason": f"meta 的 sealed_head 与链上最后一次封印锚点不逐字相等："
                                   f"链上 {chain['latest_sealed_head']}，meta {sealed_head!r}"})
    # ③ 正文行里的链头行与 qr_payload 行逐字相等（「PDF 里的字」==「PDF 里的码」）
    head_line = next((ln for ln in text_lines if ln.startswith("sealed_head: ")), None)
    qr_line = next((ln for ln in text_lines if ln.startswith("qr_payload: ")), None)
    if head_line is None or qr_line is None:
        problems.append({"code": "meta_text_lines_absent",
                         "reason": "meta 未提供 certificate_text 正文行，"
                                   "无法逐字比对链头行与 QR 载荷（本轮未覆盖）"})
    else:
        hv = head_line[len("sealed_head: "):].strip()
        qv = qr_line[len("qr_payload: "):].strip()
        if hv != qv:
            problems.append({"code": "meta_text_head_qr_differ",
                             "reason": f"正文链头行与 QR 载荷行不逐字相等：链头行 {hv!r}，"
                                       f"QR 载荷 {qv!r}（两处不得各写各的）"})
        if chain["latest_sealed_head"] and hv != chain["latest_sealed_head"]:
            problems.append({"code": "meta_text_head_not_chain_anchor",
                             "reason": f"正文链头行与链上最后一次封印锚点不逐字相等："
                                       f"链上 {chain['latest_sealed_head']}，正文 {hv!r}"})
    # ④ 链尖与封印锚点分行呈现、不混用（有 seal 时两者本就不同，写在一起要有限定语）
    chain_head = cert.get("chain_head")
    if chain_head and chain["latest_sealed_head"] and chain_head == chain["latest_sealed_head"]:
        joined = " ".join(text_lines)
        if "链尖" not in joined:
            problems.append({"code": "meta_head_anchor_conflated",
                             "reason": "cert.chain_head 与 sealed_head 同值，正文里却没有"
                                       "「链尖/封印锚点」的区分说明（不得混用）"})
    # ⑤ 三段归约 root_hash 必须带限定语（非链头、非 Merkle 根）
    root_hash = cert.get("root_hash")
    if root_hash:
        joined = " ".join(text_lines) + " " + canonical_json(cert)
        if REDUCTION_QUALIFIER not in joined or MERKLE_NOTE not in joined:
            problems.append({"code": "meta_root_hash_unqualified",
                             "reason": "meta 写了 root_hash，却没有「非链头、非 Merkle 根」"
                                       "限定语（三段归约不得冒充链头/Merkle 根）"})
        if root_hash == sealed_head:
            problems.append({"code": "meta_root_hash_equals_sealed_head",
                             "reason": "root_hash 与 sealed_head 同值：两个值在同一条链上"
                                       "本来长得不一样，同值即有一处是假的（不得互冒充）"})
    # ⑥ 诚实态（0.4）
    if cert.get("eth_tx_hash") is not None:
        problems.append({"code": "meta_eth_tx_hash_not_none",
                         "reason": f"eth_tx_hash 必须恒 None（该值不存在，不是「待填」），"
                                   f"实测 {cert.get('eth_tx_hash')!r}"})
    if str(cert.get("anchor_state", HONEST_ANCHOR_STATE)) != HONEST_ANCHOR_STATE:
        problems.append({"code": "meta_anchor_state_not_unavailable",
                         "reason": f"anchor_state 必须恒 'unavailable'，"
                                   f"实测 {cert.get('anchor_state')!r}"})
    # ⑦ 展示纪律禁语（0.4 / HANDOFF §4）：肯定式完成态措辞一律 FAIL；Merkle 根只许否定式
    blob = "\n".join(text_lines) + "\n" + canonical_json(cert)
    hit = [w for w in FORBIDDEN_WORDS if w in blob]
    if hit:
        problems.append({"code": "meta_forbidden_word",
                         "reason": f"导出件里出现展示纪律禁语（完成态措辞）：{hit}"})
    for occurrence in re.finditer("Merkle 根", blob):
        prefix = blob[max(0, occurrence.start() - 8):occurrence.start()]
        if not any(marker in prefix for marker in MERKLE_NEGATIONS):
            problems.append({"code": "meta_merkle_affirmative",
                             "reason": "出现肯定式「Merkle 根」（本仓无 Merkle 结构，只许"
                                       "「非 Merkle 根 / 不是 Merkle 根」的否定式表述）"})
            break
    # ⑧ 两句披露（随导出件给出）
    disclosure = cert.get("disclosure")
    if not isinstance(disclosure, list) or DISCLOSURE[0] not in disclosure:
        problems.append({"code": "meta_disclosure_missing",
                         "reason": "meta 缺「验签通过≠背书齐备」两句披露（两层账本边界）"})
    return {"ok": not problems, "cert": cert, "text_lines": len(text_lines),
            "sealed_head": sealed_head, "qr_payload": qr_payload,
            "root_hash": root_hash, "problems": problems, "notes": notes}


# --------------------------------------------------------------------------- #
# 内置最小样例（4 行真实链：3 条业务条目 + 1 条 seal；由本仓 audit 生成后冻结为字面量）
# --------------------------------------------------------------------------- #

SELF_TEST_LINES: tuple[str, ...] = (
    '{"action_id":null,"actor_id":"local-user","actor_role":"local","audit_id":"aud-000001-2026-11-10T08:00:00Z","audit_schema_version":"1.0","changed_fields":[],"client_info":null,"decision":null,"entry_hash":"sha256:73d9af27e64388d0c8a420d1adc65b41f1be1aef55b1bda365a0584e68e41f86","event":"note","hash":"sha256:73d9af27e64388d0c8a420d1adc65b41f1be1aef55b1bda365a0584e68e41f86","idempotency_key":null,"mode":"local","modified_params":null,"note":"R8-4 自检起点：链首条（含中文与 emoji \\u2705）","option_id":null,"original_params":null,"prev_hash":"sha256:0000000000000000000000000000000000000000000000000000000000000000","reason":null,"record_facts_hash":null,"record_status_after":null,"record_status_before":null,"request_id":null,"seq":1,"source_facts_hash":null,"task_id":null,"ts":"2026-11-10T08:00:00Z"}',
    '{"action_id":"act-demo-0004","actor_id":"alice","actor_role":"local","audit_id":"aud-000002-2026-11-10T08:05:00Z","audit_schema_version":"1.0","changed_fields":[],"client_info":{"channel":"web","ip":"203.0.113.*","user_agent":"Mozilla/5.0"},"decision":"approve","entry_hash":"sha256:e91b43c38b925977291a5b3838ddeb9f22069b4469827396c9a1567cfda38eb5","event":"decision","hash":"sha256:e91b43c38b925977291a5b3838ddeb9f22069b4469827396c9a1567cfda38eb5","idempotency_key":null,"mode":"local","modified_params":null,"note":null,"option_id":null,"original_params":null,"prev_hash":"sha256:73d9af27e64388d0c8a420d1adc65b41f1be1aef55b1bda365a0584e68e41f86","reason":"自检：同意（Unicode 边界 \\u2705）","record_facts_hash":null,"record_status_after":null,"record_status_before":null,"request_id":null,"seq":2,"source_facts_hash":null,"task_id":null,"ts":"2026-11-10T08:05:00Z"}',
    '{"action_id":"act-demo-0005","actor_id":"bob","actor_role":"local","audit_id":"aud-000003-2026-11-10T08:10:00Z","audit_schema_version":"1.0","changed_fields":[],"client_info":null,"decision":"reject","entry_hash":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29","event":"decision","hash":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29","idempotency_key":null,"mode":"local","modified_params":{"max_delete_count":12,"nested":{"a":[2,3],"b":1},"ratio":0.1,"unicode \\u952e":"\\u503c"},"note":null,"option_id":null,"original_params":null,"prev_hash":"sha256:e91b43c38b925977291a5b3838ddeb9f22069b4469827396c9a1567cfda38eb5","reason":"自检：拒绝（浮点与键序边界）","record_facts_hash":null,"record_status_after":null,"record_status_before":null,"request_id":null,"seq":3,"source_facts_hash":null,"task_id":null,"ts":"2026-11-10T08:10:00Z"}',
    '{"action_id":null,"actor_id":"system","actor_role":"system","audit_id":"aud-000004-2026-11-15T11:56:10Z","audit_schema_version":"1.0","changed_fields":[],"client_info":null,"decision":null,"entry_hash":"sha256:e783bc7a6256265f782f4daee511ed85ee58375b00dfe2874d9ef8c756ffa8aa","event":"seal","hash":"sha256:e783bc7a6256265f782f4daee511ed85ee58375b00dfe2874d9ef8c756ffa8aa","idempotency_key":null,"mode":"local","modified_params":null,"note":"R8-4 自检封印","option_id":null,"original_params":null,"prev_hash":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29","reason":null,"record_facts_hash":null,"record_status_after":null,"record_status_before":null,"request_id":null,"sealed_count":3,"sealed_head":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29","seq":4,"source_facts_hash":null,"task_id":null,"ts":"2026-11-15T11:56:10Z"}',
)
SELF_TEST_HEAD: str = "sha256:e783bc7a6256265f782f4daee511ed85ee58375b00dfe2874d9ef8c756ffa8aa"
SELF_TEST_SEALED_HEAD: str = "sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29"

#: 同一条链导出的 CSV 表头（24 列逐字；正文行由 self-test 现场生成后逐格比对）。
SELF_TEST_CERT_JSON: str = (
    '{"anchor_note":"anchor_state: unavailable（未接入锚定后端，待接入）","anchor_state":"unavailable",'
    '"chain_count":4,"chain_head":"sha256:e783bc7a6256265f782f4daee511ed85ee58375b00dfe2874d9ef8c756ffa8aa",'
    '"chain_ok":true,"counts":{"audit":4,"letters":null,"records":null},'
    '"disclosure":["验签通过≠背书齐备。","本文件的哈希与二维码只证明内容可离线复算，'
    '不构成 secretariat 签名账本的背书。"],"eth_tx_hash":null,'
    '"eth_tx_note":"eth_tx_hash: None（该值不存在，不是「待填」）","generated_at":"2026-11-15T12:00:00Z",'
    '"lang":"zh","period":"2026-11-10T08:00:00Z ~ 2026-11-15T11:56:10Z（按条目 ts 极值）",'
    '"qr_field":"sealed_head","qr_note":"QR 载荷 = sealed_head：审计账本 seal() 写入的封印锚点哈希'
    '（哈希链根哈希 A 案口径）；不是三段归约 root_hash，也不是 Merkle 根",'
    '"qr_payload":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29",'
    '"reduction_note":"三段归约（records/audit/letters 逐条哈希的归约值），非链头、非 Merkle 根",'
    '"root_hash":null,"sealed_head":"sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29",'
    '"sealed_head_field":"sealed_head","sealed_head_source":"第 4 条 seal 事件（sealed_count=3）","seals":1,'
    '"section_hashes":{},"title":"TraceLens 责任认定书（模板）",'
    '"verify_guide":"验证指引：哈希可离线复算 —— 逐条 canonical_json → sha256 → 按 prev_hash 续接成链，'
    '封印条目的 sealed_head 应等于当时的链头"}'
)
SELF_TEST_CERT_TEXT: tuple[str, ...] = (
    "TraceLens 责任认定书（模板）",
    "period: 2026-11-10T08:00:00Z ~ 2026-11-15T11:56:10Z（按条目 ts 极值）",
    "generated_at: 2026-11-15T12:00:00Z",
    "lang: zh",
    "sealed_head: sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29",
    "sealed_head_source: 第 4 条 seal 事件（sealed_count=3）",
    "sealed_head_note: QR 载荷 = sealed_head：审计账本 seal() 写入的封印锚点哈希（哈希链根哈希 A 案口径）；不是三段归约 root_hash，也不是 Merkle 根",
    "chain_head: sha256:e783bc7a6256265f782f4daee511ed85ee58375b00dfe2874d9ef8c756ffa8aa（链尖 = 最新一条的哈希；封印锚点 = seal() 当时记下的链头，两者不同）",
    "chain_ok: True · seals: 1 · count: 4",
    "root_hash(三段归约): （不适用：本导出面只有审计链，三段归约在 compliance 导出面）",
    "anchor_state: unavailable（未接入锚定后端，待接入）",
    "eth_tx_hash: None（该值不存在，不是「待填」）",
    "验证指引：哈希可离线复算 —— 逐条 canonical_json → sha256 → 按 prev_hash 续接成链，封印条目的 sealed_head 应等于当时的链头",
    "qr_payload: sha256:edf69805fe0950a495ce08b29f1117b1c4503891aa37cfe3d7fd57c3c29dca29",
    "qr_note: QR 载荷 = sealed_head：审计账本 seal() 写入的封印锚点哈希（哈希链根哈希 A 案口径）；不是三段归约 root_hash，也不是 Merkle 根",
    "验签通过≠背书齐备。",
    "本文件的哈希与二维码只证明内容可离线复算，不构成 secretariat 签名账本的背书。",
)


# --------------------------------------------------------------------------- #
# 判定聚合（stdout 逐条 PASS/FAIL/SKIP，机器可读 JSON）
# --------------------------------------------------------------------------- #


class Verdict:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, cid: str, status: str, *, codes: list[str] | None = None,
            detail: str = "") -> None:
        self.checks.append({"id": cid, "status": status,
                            "codes": codes or [], "detail": detail})

    @property
    def ok(self) -> bool:
        return not any(c["status"] == "FAIL" for c in self.checks)

    def fails(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == "FAIL"]


def _status_of(problems: list[dict]) -> tuple[str, list[str]]:
    codes = [p["code"] for p in problems]
    return ("FAIL" if codes else "PASS"), codes


def _gate(problems: list[dict], codes: tuple[str, ...]) -> tuple[str, list[str]]:
    """只把归属本判据的问题码算进本判据（其它判据的失败不牵连、不掩盖）。"""
    hits = [p["code"] for p in problems if p["code"] in codes]
    return ("FAIL" if hits else "PASS"), hits


def build_report(chain_path: pathlib.Path | None, csv_path: pathlib.Path | None,
                 meta_path: pathlib.Path | None, *, expected_head: str | None,
                 expected_count: int | None, lines: list[tuple[int, str]]) -> dict:
    v = Verdict()
    chain = verify_chain(lines, expected_head=expected_head, expected_count=expected_count)
    problems = chain["problems"]
    status, codes = _gate(problems, HASH_CODES)
    v.add("chain.hash_recompute", status, codes=codes,
          detail=f"{chain['count']} 条逐条 canonical_json → sha256 → 与 entry_hash 逐字比对")
    status, codes = _gate(problems, CONTINUITY_CODES)
    v.add("chain.prev_continuity", status, codes=codes,
          detail=f"prev_hash 续接 + seq 连续（{chain['lines']} 行 / {chain['count']} 条）")
    status, codes = _gate(problems, SEAL_CODES)
    v.add("chain.seal_anchor", status, codes=codes,
          detail=(f"封印 {chain['seals']} 次；最后一次锚点 "
                  f"{chain['latest_sealed_head'] or '（无 seal）'}"
                  f"｜来源 {chain['latest_sealed_source'] or '—'}"))
    status, codes = _gate(problems, (HEAD_CODE,))
    v.add("chain.head_anchor", "SKIP" if not expected_head else status,
          codes=codes if expected_head else [],
          detail=(f"外部锚点 {expected_head} vs 链尖 {chain['head']}" if expected_head
                  else "未给 --expected-head：链尖只输出、不与外部锚点比对（本轮未覆盖）"))
    status, codes = _gate(problems, (COUNT_CODE,))
    v.add("chain.count_anchor", "SKIP" if expected_count is None else status,
          codes=codes if expected_count is not None else [],
          detail=(f"外部条数锚点 {expected_count} vs 实测 {chain['count']}"
                  if expected_count is not None
                  else "未给 --expected-count：条数只输出、不比对（本轮未覆盖）"))
    # 链尖 ≠ 封印锚点：术语边界自检（有 seal 且无篡改时两者本就该不同）
    if chain["latest_sealed_head"] and chain["head"] == chain["latest_sealed_head"]:
        v.add("chain.head_not_sealed_head", "FAIL", codes=["head_anchor_conflated"],
              detail="链尖与最后一次封印锚点同值：seal 条目本身入链后链尖必然前进，"
                     "同值说明链被截断或封印被伪造（不得拿链尖顶替封印锚点）")
    else:
        v.add("chain.head_not_sealed_head", "PASS",
              detail=f"链尖 {chain['head']} 与封印锚点 {chain['latest_sealed_head'] or '（无 seal）'} "
                     f"分别输出、不混用")

    if csv_path is not None:
        csv_res = check_csv(csv_path, chain)
        status, codes = _status_of(csv_res["problems"])
        v.add("csv.header_24_columns",
              "PASS" if tuple(csv_res["header"]) == CSV_COLUMNS_24 else "FAIL",
              codes=[c for c in codes if c == "csv_header_mismatch"],
              detail=f"表头 {csv_res['columns']} 列，逐字对齐 24 列基线"
                     f"（_HTML_COLUMNS 14 列不在 CSV 面、不擅自同步）")
        v.add("csv.encoding_bom_crlf",
              "PASS" if csv_res["bom"] and csv_res["crlf"] else "FAIL",
              codes=[c for c in codes if c in ("csv_bom_missing", "csv_line_terminator")],
              detail=f"BOM={csv_res['bom']} 行尾 \\r\\n={csv_res['crlf']}")
        v.add("csv.rows_and_cells_match_chain",
              "PASS" if not [c for c in codes
                             if c.startswith(("csv_prev_hash", "csv_hash", "csv_entry_hash",
                                              "csv_row_count"))] else "FAIL",
              codes=[c for c in codes
                     if c.startswith(("csv_prev_hash", "csv_hash", "csv_entry_hash",
                                      "csv_row_count"))],
              detail=f"数据行 {csv_res['rows']} 条；prev_hash/hash/entry_hash 逐格与链上同值")
        v.add("csv.tail_sealed_head_verbatim",
              "PASS" if not [c for c in codes
                             if c.startswith("csv_sealed_head")] else "FAIL",
              codes=[c for c in codes if c.startswith("csv_sealed_head")],
              detail=f"尾列 sealed_head 非空值 {csv_res['sealed_head_values'] or '（无 seal 行）'}")
    else:
        for cid in ("csv.header_24_columns", "csv.encoding_bom_crlf",
                    "csv.rows_and_cells_match_chain", "csv.tail_sealed_head_verbatim"):
            v.add(cid, "SKIP", detail="未给 --csv：本轮未覆盖（不冒充已验）")
        csv_res = None

    if meta_path is not None:
        meta_res = check_meta(meta_path, chain)
        status, codes = _status_of(meta_res["problems"])
        v.add("meta.qr_payload_same_source_as_head_line",
              "PASS" if not [c for c in codes
                             if c.startswith(("meta_qr", "meta_text", "meta_sealed_head"))] else "FAIL",
              codes=[c for c in codes
                     if c.startswith(("meta_qr", "meta_text", "meta_sealed_head"))],
              detail=f"sealed_head={meta_res.get('sealed_head')!r} qr_payload="
                     f"{meta_res.get('qr_payload')!r} 正文行 {meta_res['text_lines']} 行")
        v.add("meta.root_hash_qualified",
              "PASS" if not [c for c in codes if c.startswith("meta_root_hash")] else "FAIL",
              codes=[c for c in codes if c.startswith("meta_root_hash")],
              detail=f"root_hash={meta_res.get('root_hash')!r}"
                     f"（三段归约：{REDUCTION_QUALIFIER}、{MERKLE_NOTE}）")
        v.add("meta.honest_anchor_fields",
              "PASS" if not [c for c in codes
                             if c.startswith(("meta_eth_tx", "meta_anchor_state"))] else "FAIL",
              codes=[c for c in codes
                     if c.startswith(("meta_eth_tx", "meta_anchor_state"))],
              detail="eth_tx_hash 恒 None、anchor_state 恒 unavailable（0.4 诚实口径）")
        v.add("meta.display_discipline",
              "PASS" if not [c for c in codes
                             if c.startswith(("meta_forbidden_word", "meta_merkle",
                                              "meta_disclosure"))] else "FAIL",
              codes=[c for c in codes
                     if c.startswith(("meta_forbidden_word", "meta_merkle", "meta_disclosure"))],
              detail="禁语扫描 + 两句披露在位（两层账本边界）")
    else:
        for cid in ("meta.qr_payload_same_source_as_head_line", "meta.root_hash_qualified",
                    "meta.honest_anchor_fields", "meta.display_discipline"):
            v.add(cid, "SKIP", detail="未给 --meta：本轮未覆盖（不冒充已验）")
        meta_res = None

    return {"caliber": CALIBER, "caliber_source": CALIBER_SOURCE,
            "ok": v.ok, "checks": v.checks,
            "chain": {k: chain[k] for k in ("ok", "count", "head", "genesis", "lines",
                                            "seals", "latest_sealed_head",
                                            "latest_sealed_source")},
            "chain_problems": chain["problems"],
            "seals_detail": chain["seals_detail"],
            "csv": ({k: csv_res[k] for k in ("ok", "columns", "rows", "bom", "crlf",
                                             "sealed_head_values")} if csv_res else None),
            "csv_problems": (csv_res["problems"] if csv_res else []),
            "meta": ({"ok": meta_res["ok"], "text_lines": meta_res["text_lines"],
                      "sealed_head": meta_res.get("sealed_head"),
                      "qr_payload": meta_res.get("qr_payload"),
                      "root_hash": meta_res.get("root_hash"),
                      "notes": meta_res["notes"]} if meta_res else None),
            "meta_problems": (meta_res["problems"] if meta_res else []),
            "csv_columns_24": list(CSV_COLUMNS_24),
            "chain_codes": list(CHAIN_CODES),
            "inputs": {"chain": str(chain_path) if chain_path else None,
                       "csv": str(csv_path) if csv_path else None,
                       "meta": str(meta_path) if meta_path else None,
                       "expected_head": expected_head,
                       "expected_count": expected_count},
            "disclosure": list(DISCLOSURE)}


def print_report(report: dict, *, quiet: bool = False) -> None:
    print(f"# {report['caliber']}")
    print(f"口径来源（只读等价重算）: {report['caliber_source']}")
    chain = report["chain"]
    print(f"链: {chain['count']} 条 / {chain['lines']} 行 / 封印 {chain['seals']} 次 "
          f"ok={chain['ok']}")
    print(f"链尖 head: {chain['head']}")
    print(f"封印锚点 sealed_head（最后一次）: {chain['latest_sealed_head'] or '（无 seal 条目）'}"
          f"  来源: {chain['latest_sealed_source'] or '—'}")
    print(f"三段归约 root_hash: 非链头、{MERKLE_NOTE}"
          f"（本脚本只验审计链导出面，不验 compliance 三段归约面）")
    for check in report["checks"]:
        if quiet and check["status"] != "FAIL":
            continue
        codes = f" 原因码={','.join(check['codes'])}" if check["codes"] else ""
        print(f"[{check['status']}] {check['id']} —— {check['detail']}{codes}")
    for section, key in (("链", "chain_problems"), ("CSV", "csv_problems"),
                         ("meta", "meta_problems")):
        for p in report.get(key) or []:
            seq = p.get("seq")
            where = f" index={p.get('index')}" if p.get("index") is not None else ""
            where += f" seq={seq}" if seq is not None else ""
            print(f"    · {section} {p['code']}{where}：{p['reason']}")
    print(f"汇总: {'PASS' if report['ok'] else 'FAIL'}"
          f"（{sum(1 for c in report['checks'] if c['status'] == 'PASS')} PASS / "
          f"{sum(1 for c in report['checks'] if c['status'] == 'FAIL')} FAIL / "
          f"{sum(1 for c in report['checks'] if c['status'] == 'SKIP')} SKIP）")
    for line in report["disclosure"]:
        print(f"披露: {line}")


# --------------------------------------------------------------------------- #
# --self-test：内置最小样例 + 口径边界案例 + 篡改注入自证（不读任何文件）
# --------------------------------------------------------------------------- #


def self_test() -> int:
    v = Verdict()
    lines = [(i, raw) for i, raw in enumerate(SELF_TEST_LINES)]

    base = verify_chain(lines, expected_head=SELF_TEST_HEAD, expected_count=len(SELF_TEST_LINES))
    v.add("self_test.chain_sample_clean", "PASS" if base["ok"] else "FAIL",
          codes=[p["code"] for p in base["problems"]],
          detail=f"最小样例 4 行（3 条业务 + 1 条 seal）全链复算通过；head={base['head']}")
    v.add("self_test.sealed_head_anchor",
          "PASS" if base["latest_sealed_head"] == SELF_TEST_SEALED_HEAD
          and all(s["matches_prev_at_that_point"] for s in base["seals_detail"]) else "FAIL",
          detail=f"最后一次封印锚点 {base['latest_sealed_head']} 与当时链头逐字相等")

    # 口径边界：键序 / Unicode / 浮点 / 排除键 / 兜底转换
    boundary: list[str] = []
    if canonical_json({"b": 1, "a": {"y": 2, "x": [1, 2]}}) != '{"a":{"x":[1,2],"y":2},"b":1}':
        boundary.append("键排序/分隔符不符口径")
    if canonical_json({"键": "值 ✅"}) != '{"键":"值 ✅"}':
        boundary.append("ensure_ascii=False 未生效（中文/emoji 被转义）")
    if canonical_json({"v": 0.1, "w": 1.0, "e": 1e20}) != '{"e":1e+20,"v":0.1,"w":1.0}':
        boundary.append("浮点序列化与本仓 json.dumps(repr) 口径不一致")
    entry = json.loads(SELF_TEST_LINES[1])
    if entry_hash(entry, entry["prev_hash"]) != entry["hash"]:
        boundary.append("样例条目 entry_hash 复算不等")
    padded = dict(entry)
    padded["hash"] = "sha256:" + "f" * 64
    padded["entry_hash"] = "sha256:" + "e" * 64
    padded["prev_hash"] = "sha256:" + "d" * 64
    if entry_hash(padded, entry["prev_hash"]) != entry["hash"]:
        boundary.append("排除键（hash/entry_hash/prev_hash）未生效")
    try:
        canonical_json({"x": object()})
        boundary.append("不可序列化值被静默放过")
    except CaliberError:
        pass
    if canonical_json({"d": datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)}) != '{"d":"2026-11-15T12:00:00Z"}':
        boundary.append("datetime 兜底不是 iso_utc 口径")
    blob = b"\x00\x01"
    bytes_expect = '{"b":"' + HASH_PREFIX + hashlib.sha256(blob).hexdigest() + '"}'
    if canonical_json({"b": blob}) != bytes_expect:
        boundary.append("bytes 兜底不是 sha256: 摘要口径")
    if canonical_json({"s": {3, 1, 2}}) != '{"s":[1,2,3]}':
        boundary.append("set 兜底不是 sorted(key=str) 口径")
    v.add("self_test.canonical_json_boundary", "PASS" if not boundary else "FAIL",
          codes=[] if not boundary else ["caliber_boundary_mismatch"],
          detail="键排序 / Unicode 原样 / 浮点 repr / 排除键 / datetime+bytes+set 兜底 / 不可序列化拒绝"
                 + ("" if not boundary else f"：{boundary}"))

    # 篡改注入自证（内存副本；每条都必须被拒绝并给出原因码）
    tamper = []

    def codes_of(mutated: list[tuple[int, str]], **kw: object) -> list[str]:
        res = verify_chain(mutated, **kw)  # type: ignore[arg-type]
        return [p["code"] for p in res["problems"]]

    one_byte = list(lines)
    one_byte[1] = (1, one_byte[1][1].replace('"decision":"approve"', '"decision":"approvf"'))
    got = codes_of(one_byte)
    if "hash_mismatch" not in got:
        tamper.append(f"改 1 字节未被拒绝（codes={got}）")

    deleted = [(i, raw) for i, raw in lines if i != 2]
    got = codes_of(deleted)
    if not ({"chain_break", "seq_gap"} & set(got)):
        tamper.append(f"删 1 行未被拒绝（codes={got}）")

    got = codes_of(list(lines), expected_head="sha256:" + "f" * 64)
    if "head_mismatch" not in got:
        tamper.append(f"换链头锚点未被拒绝（codes={got}）")

    forged = [raw for _, raw in lines]
    target = json.loads(forged[3])
    target["sealed_head"] = "sha256:" + "a" * 64
    forged[3] = canonical_json(target)
    got = codes_of([(i, raw) for i, raw in enumerate(forged)])
    if "seal_mismatch" not in got:
        tamper.append(f"伪造 sealed_head 未被拒绝（codes={got}）")

    trunc = [raw for _, raw in lines][:3]
    got = codes_of([(i, raw) for i, raw in enumerate(trunc)], expected_head=SELF_TEST_HEAD)
    if "head_mismatch" not in got:
        tamper.append(f"截尾（链内自洽）未被外部锚点拒绝（codes={got}）")
    v.add("self_test.tamper_rejected", "PASS" if not tamper else "FAIL",
          codes=[] if not tamper else ["tamper_not_rejected"],
          detail="改 1 字节 / 删 1 行 / 换链头值 / 伪造 sealed_head / 截尾 五例全部拒绝"
                 + ("" if not tamper else f"：{tamper}"))

    # CSV 面自证：现场生成 24 列 CSV（bom + \r\n），逐格比对；再抽掉尾列 → 必须 FAIL
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(list(CSV_COLUMNS_24))
    for e in base["entries"]:
        writer.writerow([_cell_text(e.get(col)) for col in CSV_COLUMNS_24])
    csv_text = CSV_BOM + buffer.getvalue()
    tmp = pathlib.Path("verify_export_selftest.csv")
    wrote = True
    try:
        tmp.write_bytes(csv_text.encode("utf-8"))
        res = check_csv(tmp, base)
        v.add("self_test.csv_clean", "PASS" if res["ok"] else "FAIL",
              codes=[p["code"] for p in res["problems"]],
              detail=f"24 列 / BOM={res['bom']} / \\r\\n={res['crlf']} / 行 {res['rows']} / "
                     f"尾列 sealed_head {res['sealed_head_values']}")
        broken_text = CSV_BOM + "\r\n".join(
            [",".join(c for c in CSV_COLUMNS_24 if c != "sealed_head")] +
            [ln for ln in csv_text[len(CSV_BOM):].split("\r\n") if ln]) + "\r\n"
        tmp.write_text(broken_text, encoding="utf-8")
        res2 = check_csv(tmp, base)
        codes2 = [p["code"] for p in res2["problems"]]
        v.add("self_test.csv_header_23_columns_rejected",
              "PASS" if "csv_header_mismatch" in codes2 else "FAIL", codes=codes2,
              detail="抽掉尾列（23 列）→ 表头逐字比对必须 FAIL")
    except OSError as exc:  # 只写当前目录一个自检文件；写不了就如实登记 SKIP
        wrote = False
        v.add("self_test.csv_clean", "SKIP", detail=f"当前目录不可写，CSV 面自检未跑：{exc}")
        v.add("self_test.csv_header_23_columns_rejected", "SKIP", detail="同上")
    finally:
        if wrote:
            try:
                tmp.unlink()
            except OSError:
                pass

    # meta 面自证：链头行与 QR 载荷同源同值 + 禁语 + 诚实态
    meta_dir = pathlib.Path(".")
    meta_path = meta_dir / "verify_export_selftest_meta.json"
    wrote_meta = True
    try:
        cert = json.loads(SELF_TEST_CERT_JSON)
        cert["certificate_text"] = list(SELF_TEST_CERT_TEXT)
        meta_path.write_text(canonical_json({"certificate": cert}), encoding="utf-8")
        res = check_meta(meta_path, base)
        v.add("self_test.meta_clean", "PASS" if res["ok"] else "FAIL",
              codes=[p["code"] for p in res["problems"]],
              detail="链头行 == QR 载荷 == 链上最后一次封印锚点；root_hash 限定语/禁语/披露齐备")
        cert2 = json.loads(SELF_TEST_CERT_JSON)
        cert2["qr_payload"] = "sha256:" + "b" * 64
        cert2["certificate_text"] = list(SELF_TEST_CERT_TEXT)
        meta_path.write_text(canonical_json({"certificate": cert2}), encoding="utf-8")
        res2 = check_meta(meta_path, base)
        codes2 = [p["code"] for p in res2["problems"]]
        v.add("self_test.meta_qr_forgery_rejected",
              "PASS" if "meta_qr_not_same_source" in codes2 else "FAIL", codes=codes2,
              detail="伪造 qr_payload（与链头行不同源）→ 必须 FAIL")
        cert3 = json.loads(SELF_TEST_CERT_JSON)
        cert3["root_hash"] = SELF_TEST_SEALED_HEAD
        cert3["reduction_note"] = "三段归约"
        cert3["certificate_text"] = ["root_hash(三段归约): " + SELF_TEST_SEALED_HEAD,
                                     "root_hash_note: 三段归约"]
        meta_path.write_text(canonical_json({"certificate": cert3}), encoding="utf-8")
        res3 = check_meta(meta_path, base)
        codes3 = [p["code"] for p in res3["problems"]]
        v.add("self_test.meta_root_hash_unqualified_rejected",
              "PASS" if "meta_root_hash_unqualified" in codes3 and
              "meta_root_hash_equals_sealed_head" in codes3 else "FAIL", codes=codes3,
              detail="root_hash 缺限定语 / 拿封印锚点冒充三段归约 → 必须 FAIL")
        cert4 = json.loads(SELF_TEST_CERT_JSON)
        cert4["certificate_text"] = ["已上链，冲正完成。"]
        cert4["eth_tx_hash"] = "0x" + "ab" * 32
        cert4["anchor_state"] = "anchored"
        meta_path.write_text(canonical_json({"certificate": cert4}), encoding="utf-8")
        res4 = check_meta(meta_path, base)
        codes4 = [p["code"] for p in res4["problems"]]
        need = {"meta_forbidden_word", "meta_eth_tx_hash_not_none",
                "meta_anchor_state_not_unavailable"}
        v.add("self_test.meta_honesty_fields_rejected",
              "PASS" if need <= set(codes4) else "FAIL", codes=codes4,
              detail="完成态禁语 / eth_tx_hash 非 None / anchor_state 非 unavailable → 必须 FAIL")
    except OSError as exc:
        wrote_meta = False
        for cid in ("self_test.meta_clean", "self_test.meta_qr_forgery_rejected",
                    "self_test.meta_root_hash_unqualified_rejected",
                    "self_test.meta_honesty_fields_rejected"):
            v.add(cid, "SKIP", detail=f"当前目录不可写，meta 面自检未跑：{exc}")
    finally:
        if wrote_meta:
            try:
                meta_path.unlink()
            except OSError:
                pass

    checks = {"caliber": CALIBER, "caliber_source": CALIBER_SOURCE, "ok": v.ok,
              "checks": v.checks, "sample": {"lines": len(SELF_TEST_LINES),
                                             "head": SELF_TEST_HEAD,
                                             "sealed_head": SELF_TEST_SEALED_HEAD},
              "disclosure": list(DISCLOSURE)}
    print(f"# {CALIBER} — --self-test")
    for c in v.checks:
        codes = f" 原因码={','.join(c['codes'])}" if c["codes"] else ""
        print(f"[{c['status']}] {c['id']} —— {c['detail']}{codes}")
    print(f"汇总: {'PASS' if v.ok else 'FAIL'}"
          f"（{sum(1 for c in v.checks if c['status'] == 'PASS')} PASS / "
          f"{sum(1 for c in v.checks if c['status'] == 'FAIL')} FAIL / "
          f"{sum(1 for c in v.checks if c['status'] == 'SKIP')} SKIP）")
    print("说明: 干净环境（仓库外）实跑、QR 实机扫码、ELK/Splunk 实机导入归 R8-7（PM 亲自），"
          "本轮 self-test 只证明口径自洽与篡改注入被拒绝。")
    for line in DISCLOSURE:
        print(f"披露: {line}")
    print("JSON " + canonical_json(checks))
    return 0 if v.ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_export.py",
        description=("导出包独立验证（REQ-008 / B8-4）。纯标准库、不 import 本仓模块；"
                     f"口径冻结版本 {CALIBER}。逐条 canonical_json → sha256 → 与 entry_hash "
                     "逐字相等，prev_hash 链连续，封印锚点与链尖分别输出，CSV 表头逐字 24 列，"
                     "meta 的链头行与 QR 载荷同源同值。退出码 0=全部 PASS，1=任一 FAIL，"
                     "2=用法/IO 错误。"),
        epilog=("示例: python verify_export.py --chain audit.jsonl --csv audit.csv "
                "--meta cert.json --expected-head sha256:... --expected-count 4；"
                "python verify_export.py --self-test"))
    p.add_argument("--chain", metavar="FILE",
                   help="审计链导出件（JSONL）：一行一条，链哈希写在行内")
    p.add_argument("--csv", metavar="FILE",
                   help="同一次导出的 CSV：表头须逐字 24 列，尾列 sealed_head 与链上 seal 逐字相等")
    p.add_argument("--meta", metavar="FILE",
                   help="导出包 meta / 责任认定书 JSON：核链头行与 QR 载荷同源同值、"
                        "三段归约限定语、诚实态字段与展示禁语")
    p.add_argument("--expected-head", metavar="sha256:…",
                   help="外部抄存的链头锚点；不符报 head_mismatch（整段重算历史只能靠锚点发现）")
    p.add_argument("--expected-count", type=int, metavar="N",
                   help="外部抄存的条数锚点；不符报 count_mismatch")
    p.add_argument("--json", action="store_true",
                   help="只打印机器读 JSON（逐条判定可机器复核；退出码不变）")
    p.add_argument("--quiet", action="store_true", help="人读模式下只打印汇总与 FAIL 行")
    p.add_argument("--self-test", action="store_true",
                   help="跑内置最小样例（4 行真实链：3 条业务 + 1 条 seal）+ 口径边界 + "
                        "篡改注入自证，不读任何外部文件")
    p.add_argument("--caliber", action="store_true", help="只打印口径冻结版本与等价重算规则摘要")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.caliber:
        print(CALIBER)
        print(CALIBER_SOURCE)
        print("canonical_json: sort_keys=True, separators=(\",\",\":\"), ensure_ascii=False；"
              "排除键 hash/entry_hash/prev_hash；兜底 datetime→iso_utc、set→sorted(key=str)、"
              "bytes→sha256:摘要、PurePath→str，其余拒绝")
        print('entry_hash: "sha256:" + sha256(prev_hash + "\\n" + canonical_json(payload))；'
              f"GENESIS={GENESIS_HASH}")
        print("CSV 基线: 恒 24 列 + BOM + \\r\\n（_HTML_COLUMNS 14 列不擅自同步）")
        print(f"链校验问题码: {', '.join(CHAIN_CODES)}")
        for line in DISCLOSURE:
            print(f"披露: {line}")
        return 0
    if args.self_test:
        return self_test()
    if not args.chain:
        print("用法错误：--chain FILE 是必填（或使用 --self-test / --caliber / --help）",
              file=sys.stderr)
        return 2
    chain_path = pathlib.Path(args.chain)
    if not chain_path.is_file():
        print(f"用法错误：--chain 读不到文件 {chain_path}", file=sys.stderr)
        return 2
    for name, value in (("--csv", args.csv), ("--meta", args.meta)):
        if value and not pathlib.Path(value).is_file():
            print(f"用法错误：{name} 读不到文件 {value}", file=sys.stderr)
            return 2
    if args.expected_head and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expected_head):
        print("用法错误：--expected-head 必须是 sha256:<64 hex>", file=sys.stderr)
        return 2
    if args.expected_count is not None and args.expected_count < 0:
        print("用法错误：--expected-count 不能是负数", file=sys.stderr)
        return 2
    try:
        lines = read_lines(chain_path)
    except (CaliberError, OSError) as exc:
        print(f"用法错误：链文件读取失败 {exc}", file=sys.stderr)
        return 2
    report = build_report(chain_path, args.csv, args.meta,
                          expected_head=args.expected_head,
                          expected_count=args.expected_count, lines=lines)
    if args.json:
        print(canonical_json(report))
    else:
        print_report(report, quiet=args.quiet)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
