#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线复核责任认定书（`docs/12 §5`；纯标准库，不联网、不读服务端状态）。

用法::

    python tools/verify_letter.py letter.json          # 人读 + 机器读，退出码 0/1
    python tools/verify_letter.py letter.json --json   # 只打印机器读 JSON
    type letter.json | python tools/verify_letter.py -  # 从标准输入读

能复算什么（离线）
------------------
1. 认定书本体的 Ed25519 签名（公钥就在认定书里，不查服务端）；
2. ``records_root``：各动作 ``entry_hash`` 升序以 ``\\n`` 连接后的 sha256；
3. 逐条动作的 Agent 签名：``SIGNATURE_FIELDS`` 的 11 个字段全部在 ``actions[]`` 里。

不能复算什么（必须如实说明）
----------------------------
* 审批人签名与身份注册表的绑定：需要身份账本片段（审批人公钥不在认定书里）；
* 可信时间戳：认定书写的是 ``local-tsa:<ISO>``，没有 RFC 3161 证据。

退出码：0 = 三项全部通过；1 = 任一失败或文件不可读。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_explainer import crypto_sig as cs  # noqa: E402
from agent_explainer.feature_store import sha256_hex  # noqa: E402
from agent_explainer.secretariat import SIGNATURE_FIELDS  # noqa: E402


def verify_letter(letter: object) -> dict:
    """复算一封认定书；返回 ``{ok, checks, problems}``（不抛异常）。"""
    problems: list[dict] = []
    checks: dict = {}
    if not isinstance(letter, dict):
        return {"ok": False, "checks": {}, "problems": [{"code": "SECRETARIAT_FIELD_INVALID",
                                                         "detail": {"reason": "not an object"}}]}

    body = {key: value for key, value in letter.items() if key != "signature"}
    public_key = str(letter.get("public_key") or "")
    signature = str(letter.get("signature") or "")
    try:
        signature_ok = bool(public_key and signature) and cs.verify_b64(
            public_key, cs.canonical_bytes(body), signature)
    except (ValueError, TypeError):
        signature_ok = False
    checks["signature"] = signature_ok
    if not signature_ok:
        problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                         "detail": {"scope": "letter"}})

    actions = letter.get("actions") if isinstance(letter.get("actions"), list) else []
    digests = [str(action.get("entry_hash") or sha256_hex(cs.canonical_json(action)))
               for action in actions if isinstance(action, dict)]
    recomputed_root = sha256_hex("\n".join(sorted(digests)))
    root_ok = recomputed_root == str(letter.get("records_root") or "")
    checks["records_root"] = root_ok
    if not root_ok:
        problems.append({"code": "SECRETARIAT_HASH_MISMATCH",
                         "detail": {"scope": "records_root",
                                    "recorded": letter.get("records_root"),
                                    "recomputed": recomputed_root}})

    action_checks: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            action_checks.append({"id": "", "ok": False})
            problems.append({"code": "SECRETARIAT_FIELD_INVALID",
                             "detail": {"scope": "action", "reason": "not an object"}})
            continue
        action_id = str(action.get("id") or "")
        pub = str(action.get("agent_pubkey") or "")
        sig = str(action.get("signature") or "")
        try:
            ok = bool(pub and sig) and cs.verify_b64(
                pub, cs.signed_payload(action, SIGNATURE_FIELDS), sig)
        except (ValueError, TypeError):
            ok = False
        action_checks.append({"id": action_id, "ok": ok})
        if not ok:
            problems.append({"code": "SECRETARIAT_SIGNATURE_INVALID",
                             "detail": {"scope": "action", "id": action_id}})
    checks["actions"] = action_checks
    checks["actions_ok"] = all(item["ok"] for item in action_checks) if action_checks else False

    approval = letter.get("verification") if isinstance(letter.get("verification"), dict) else {}
    return {
        "ok": not problems,
        "checks": checks,
        "problems": problems,
        "fingerprint": str(letter.get("fingerprint") or ""),
        "letter_id": str(letter.get("id") or ""),
        "subject": str(letter.get("subject") or ""),
        "audit_head": str(letter.get("audit_head") or ""),
        "tsa_state": str(approval.get("tsa_state") or ""),
        "offline_boundary": {
            "verifiable": ["letter_signature", "records_root", "action_signatures"],
            "not_verifiable": ["approver_signatures_without_identity_ledger",
                               "rfc3161_timestamp"],
        },
    }


def _read(path: str) -> object:
    if path == "-":
        return json.load(sys.stdin)
    # utf-8-sig：Windows 记事本 / PowerShell 5 的 UTF-8 会带 BOM，读得动比报错有用。
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/verify_letter.py",
        description="离线复核责任认定书（签名 + records_root + 逐条动作签名）")
    parser.add_argument("letter", help="认定书 JSON 路径，- 表示标准输入")
    parser.add_argument("--json", action="store_true", help="只输出机器读 JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        letter = _read(args.letter)
    except (OSError, json.JSONDecodeError) as exc:
        report = {"ok": False, "problems": [{"code": "SECRETARIAT_FIELD_INVALID",
                                             "detail": {"reason": str(exc)}}]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report = verify_letter(letter)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    print(f"认定书：{report['subject']}（{report['letter_id']}）")
    print(f"  签名        : {'通过' if report['checks'].get('signature') else '失败'}")
    print(f"  records_root: {'通过' if report['checks'].get('records_root') else '失败'}")
    for item in report["checks"].get("actions", []):
        print(f"  动作 {item['id']}: {'通过' if item['ok'] else '失败'}")
    print(f"  audit_head  : {report['audit_head'] or '（未提供）'}")
    print(f"  tsa_state   : {report['tsa_state'] or '（未标注）'}")
    print("离线边界：审批签与身份绑定需身份账本片段；时间戳只有 local-tsa（无 RFC 3161）。")
    if report["problems"]:
        print("问题：")
        for problem in report["problems"]:
            print("  -", json.dumps(problem, ensure_ascii=False))
    print("结论：", "PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
