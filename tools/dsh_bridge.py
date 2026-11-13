"""DSH -> TraceLens bridge (real-time).

Watches DeepSeek Harness session logs (``~/.dsh/sessions/**/session.v4.jsonl.zstd``),
and forwards tool calls / results from sessions that use the local Strata model
(``strata/swift-iq3_s`` by default) to a running TraceLens app via its Python SDK.

Design:
  * read-only on DSH session files (zstd frames, JSONL events);
  * only sessions whose ``request/context`` model matches ``--model-filter``;
  * only *new* events after the bridge starts (``--backfill`` replays history);
  * tool/call -> TraceLens action (running); tool/result -> update (succeeded/failed);
  * sanitizes/truncates arguments so secrets are not forwarded verbatim;
  * TraceLens base URL is auto-discovered from the running sidecar process.

Usage (from agent-explainer/):
    python tools/dsh_bridge.py                 # live, skip history
    python tools/dsh_bridge.py --backfill      # also replay existing session lines
    python tools/dsh_bridge.py --dry-run       # print, do not post
    python tools/dsh_bridge.py --model-filter swift --active-minutes 30
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
import urllib.request

try:
    import zstandard  # type: ignore
except Exception:  # pragma: no cover
    print("需要 zstandard：python -m pip install zstandard", file=sys.stderr)
    raise SystemExit(2)

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_explainer.sdk import TraceLensClient  # noqa: E402

DEFAULT_SESSIONS = pathlib.Path(os.environ.get("USERPROFILE", "")) / ".dsh" / "sessions"
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}|"
                       r"api[_-]?key\s*[=:]\s*[^\s,}]+)", re.IGNORECASE)
WS_RE = re.compile(r"\s+")

TYPE_MAP = {
    "read": "read", "glob": "read", "grep": "read", "ls": "read",
    "web_search": "read", "web_fetch": "read",
    "write": "write", "edit": "write", "apply_patch": "write",
    "todo_write": "plan", "ask_user_question": "ask",
    "bash": "tool_call", "pwsh": "tool_call", "win_bash": "tool_call",
    "subagent": "tool_call", "subagent_fork": "tool_call",
}


def clean(text: object, limit: int = 120) -> str:
    s = str(text or "")
    s = SECRET_RE.sub("[redacted]", s)
    s = WS_RE.sub(" ", s).strip()
    return s[:limit]


def short_args(name: str, raw: object) -> str:
    """Extract a short, safe argument summary for the action intent."""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
    if not isinstance(data, dict):
        return ""
    for key in ("command", "cmd", "file_path", "path", "pattern", "query", "url",
                "prompt", "description", "name"):
        if key in data and data[key]:
            return f"{key}={clean(data[key], 90)}"
    keys = [k for k in data.keys() if not k.startswith("_")][:2]
    if not keys:
        return ""
    return ", ".join(f"{k}={clean(data[k], 60)}" for k in keys)


def action_type_for(name: str) -> str:
    if name.startswith(("mcp__", "agent_teams_")):
        return "tool_call"
    return TYPE_MAP.get(name, "tool_call")


def iso_time(ms: object) -> str:
    try:
        stamp = float(ms) / 1000.0
    except Exception:
        stamp = time.time()
    tz = _dt.datetime.now().astimezone().tzinfo
    return _dt.datetime.fromtimestamp(stamp, tz=tz).isoformat(timespec="milliseconds")


def result_text(payload: dict) -> tuple[str, bool]:
    """Return (text summary, is_error) from a DSH tool/result event."""
    message = payload.get("message") or {}
    is_error = bool(message.get("isError")) or bool(payload.get("isError"))
    text = ""
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        text = " ".join(parts)
    elif isinstance(content, str):
        text = content
    if not text:
        text = json.dumps(payload, ensure_ascii=False)
    return clean(text, 180), is_error


def read_session_lines(path: pathlib.Path) -> list[dict]:
    with open(path, "rb") as fh:
        with zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True) as reader:
            data = reader.read()
    out = []
    for raw in data.decode("utf-8", "replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def discover_base_url(explicit: str | None) -> str | None:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("TRACELENS_URL")
    if env:
        return env.rstrip("/")
    try:
        script = (
            "$p = Get-Process agent-explainer-sidecar -ErrorAction SilentlyContinue | "
            "Select-Object -First 1; if ($p) { "
            "(Get-NetTCPConnection -OwningProcess $p.Id -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty LocalPort) }"
        )
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=8).stdout.strip()
        if out.isdigit():
            return f"http://127.0.0.1:{out}"
    except Exception:
        pass
    return None


class Bridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state: dict[pathlib.Path, dict] = {}
        self.base_url: str | None = discover_base_url(args.tracelens_url)
        self.clients: dict[str, TraceLensClient] = {}
        self._calls: dict[str, dict[str, str]] = {}
        self._pending: dict[str, dict] = {}
        self.last_discovery = 0.0
        self.sent = 0
        self.started_ms = time.time() * 1000

    # -- TraceLens ---------------------------------------------------------
    def ensure_client(self, session_id: str) -> TraceLensClient | None:
        if self.base_url is None or time.time() - self.last_discovery > 20:
            found = discover_base_url(self.args.tracelens_url)
            self.last_discovery = time.time()
            if found and found != self.base_url:
                self.base_url = found
                self.clients.clear()
                print(f"[bridge] TraceLens -> {found}")
        if self.base_url is None:
            return None
        client = self.clients.get(session_id)
        if client is None:
            client = TraceLensClient(
                self.base_url,
                agent_id="deepseek-harness",
                actor_id="local",
                workspace_id="dsh",
                session_id=session_id,
                language="zh",
                source_kind="sdk",
                timeout=8,
                retries=1,
            )
            self.clients[session_id] = client
        return client

    def post_call(self, session_id: str, call: dict, event: dict, model: str) -> None:
        """Remember the call; the visible card is created when the result arrives."""
        call_id = str(call.get("callId") or "")
        if not call_id:
            return
        self._pending[call_id] = {
            "session_id": session_id,
            "name": str(call.get("name") or "tool"),
            "arguments": call.get("arguments"),
            "seq": event.get("seq"),
            "time": event.get("time"),
            "model": model,
        }

    def model_explain(self, name: str, arguments: object, text: str,
                      is_error: bool) -> str | None:
        """Ask the local Strata model for one plain-language sentence."""
        if self.args.no_model_explain:
            return None
        prompt = (
            "你是 TraceLens 的解释器。请用一句简体中文（不超过 45 字），向不懂技术的用户说明"
            "这次 AI 操作做了什么、结果如何；不要解释你自己，不要编造未给出的信息，只输出这一句。\n"
            f"工具：{name}\n参数摘要：{short_args(name, arguments) or '（无）'}\n"
            f"结果：{text or '（没有返回内容）'}\n是否失败：{'是' if is_error else '否'}"
        )
        payload = {
            "model": self.args.explain_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 512,
            "stream": False,
        }
        req = urllib.request.Request(
            self.args.explain_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            content = body["choices"][0]["message"].get("content") or ""
            return clean(content, 90) or None
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] model_explain failed: {type(exc).__name__}: {exc}")
            return None

    def post_result(self, session_id: str, result: dict) -> None:
        payload = result.get("data") or {}
        message = payload.get("message") or {}
        call_id = message.get("toolCallId") or (message.get("source") or {}).get("callId")
        if not call_id:
            return
        pending = self._pending.pop(str(call_id), None)
        if not pending:
            return
        name = pending["name"]
        arguments = pending["arguments"]
        text, is_error = result_text(payload)
        action_id = f"dsh-{pending['session_id'][-8:]}-{pending['seq']}"
        if self.args.dry_run:
            intent = f"调用 {name}（{short_args(name, arguments)}）"
            print(f"[dry] action {action_id} {action_type_for(name)} {clean(intent, 118)} -> {text[:50]}")
            return
        explained = self.model_explain(name, arguments, text, is_error)
        if explained:
            intent = explained
        else:
            extra = short_args(name, arguments)
            intent = f"调用 {name}" + (f"（{extra}）" if extra else "")
        client = self.ensure_client(pending["session_id"])
        if client is None:
            return
        client.log_action(
            clean(intent, 118),
            action_id=action_id,
            action_type=action_type_for(name),
            status="failed" if is_error else "succeeded",
            tool_used=name[:80],
            rationale=clean(f"由本地模型 {pending['model']} 驱动的 DSH Agent 动作", 100),
            result_summary=text,
            confidence=0.6,
            risk_level="low",
            timestamp=iso_time(pending["time"]),
            run_id=pending["session_id"],
            agent_name="DeepSeek Harness",
        )
        print(f"[post] {action_id} {name} {'failed' if is_error else 'ok'} explained={bool(explained)}")

    # -- sessions ----------------------------------------------------------
    def wanted(self, model: str) -> bool:
        needle = self.args.model_filter.lower()
        return needle in model.lower()

    def handle_event(self, path: pathlib.Path, session_id: str, st: dict,
                     event: dict) -> None:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "request/context":
            model = f"{data.get('provider', '')}/{data.get('model', '')}"
            st["model"] = model
            active = self.wanted(model)
            if active != st.get("active"):
                print(f"[session] {session_id} model={model} active={active}")
            st["active"] = active
            return
        if not st.get("active"):
            return
        if etype == "tool/call":
            self.post_call(session_id, data, event, st.get("model", ""))
        elif etype == "tool/result":
            self.post_result(session_id, event)

    def poll_once(self) -> None:
        cutoff = time.time() - max(1, self.args.active_minutes) * 60
        paths = glob.glob(str(self.args.sessions_dir / "**" / "session.v4.jsonl.zstd"),
                          recursive=True)
        for raw in paths:
            path = pathlib.Path(raw)
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                events = read_session_lines(path)
            except Exception:
                continue
            if not events:
                continue
            st = self.state.get(path)
            session_id = str(events[0].get("id") or path.parent.name)
            if st is None:
                model = ""
                for ev in events:
                    if ev.get("type") == "request/context":
                        d = ev.get("data") or {}
                        model = f"{d.get('provider', '')}/{d.get('model', '')}"
                created = float(events[0].get("createdAt") or 0)
                start_at = 0 if (self.args.backfill or created >= self.started_ms) else len(events)
                st = {"offset": start_at,
                      "session_id": session_id, "model": model, "active": False}
                st["active"] = self.wanted(model)
                self.state[path] = st
                if st["active"]:
                    print(f"[session] {session_id} model={model} (start at {st['offset']}/{len(events)})")
            if len(events) < st["offset"]:
                st["offset"] = 0
            new = events[st["offset"]:]
            st["offset"] = len(events)
            for event in new:
                self.handle_event(path, session_id, st, event)

    def run(self) -> None:
        print(f"[bridge] sessions={self.args.sessions_dir}")
        print(f"[bridge] model-filter={self.args.model_filter!r} active-minutes={self.args.active_minutes}")
        print(f"[bridge] tracelens={self.base_url or '(waiting for TraceLens app)'}")
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                print("[bridge] stopped")
                return
            except Exception:
                traceback.print_exc()
            time.sleep(max(1, self.args.poll))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Forward DSH (Strata) tool actions to TraceLens")
    p.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS))
    p.add_argument("--tracelens-url", default=None)
    p.add_argument("--model-filter", default="swift",
                   help="substring match on '<provider>/<model>' (default: swift)")
    p.add_argument("--active-minutes", type=int, default=30)
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--backfill", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-file", default=None)
    p.add_argument("--explain-url", default="http://127.0.0.1:47831/v1",
                   help="OpenAI-compatible endpoint used to generate the one-line explanation")
    p.add_argument("--explain-model", default="swift-iq3_s")
    p.add_argument("--no-model-explain", action="store_true",
                   help="do not call the local model; use the deterministic intent text")
    args = p.parse_args(argv)
    if args.log_file:
        log = open(args.log_file, "a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
    args.sessions_dir = pathlib.Path(args.sessions_dir)
    if not args.sessions_dir.exists():
        print(f"会话目录不存在：{args.sessions_dir}", file=sys.stderr)
        return 2
    Bridge(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
