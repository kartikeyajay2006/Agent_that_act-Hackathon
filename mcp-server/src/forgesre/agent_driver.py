"""Terminal driver for a ForgeSRE session running inside TrueForge.

Uses the official trueforge-sdk. The agent loop, tool routing, sandbox and the
approval pause all happen inside TrueForge; this only streams events, prints a
compact trace, and relays the human's approval decision back to TrueForge as a
user.tool_approval event. Useful for headless demos and automated end-to-end
tests; the TrueForge chat UI is the primary interface.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from trueforge_sdk import TrueForge
from trueforge_sdk.events import is_event_delta, merge_event_delta

from .trueforge import AGENT_NAME

DIM, BOLD, RED, GREEN, YELLOW, CYAN, RESET = (
    "\033[2m",
    "\033[1m",
    "\033[31m",
    "\033[32m",
    "\033[33m",
    "\033[36m",
    "\033[0m",
)


def _dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj if isinstance(obj, dict) else {}


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _summarise_result(content: str) -> str:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return _short(content, 120)
    if isinstance(data, dict) and isinstance(data.get("structuredContent"), dict):
        data = data["structuredContent"]
    if not isinstance(data, dict):
        return _short(content, 120)
    keys = ("verdict", "error_code", "risk_level", "overall", "final_status", "result", "status")
    parts = [f"{k}={data[k]}" for k in keys if k in data]
    if "failed_criteria" in data and data["failed_criteria"]:
        parts.append(f"failed={data['failed_criteria']}")
    return ", ".join(parts) or _short(content, 120)


class Decider:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def decide(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        print(f"\n{YELLOW}{BOLD}⚠  TRUEFORGE TOOL APPROVAL REQUIRED — {tool}{RESET}")
        for k, v in args.items():
            print(f"   {k}: {_short(v, 300)}")
        if self.mode == "approve":
            print(f"   {GREEN}→ APPROVE (--approve){RESET}")
            return {"status": "allow"}
        if self.mode == "deny":
            print(f"   {RED}→ DENY (--deny){RESET}")
            return {"status": "deny", "reason": "Denied by on-call engineer during review."}
        answer = input(f"   {BOLD}[a]pprove / [d]eny? {RESET}").strip().lower()
        if answer.startswith("a"):
            return {"status": "allow"}
        reason = input("   reason for denial (optional): ").strip() or "Denied by on-call engineer."
        return {"status": "deny", "reason": reason}


def run(prompt: str, mode: str, base_url: str, out_dir: Path) -> dict[str, Any]:
    client = TrueForge(base_url=base_url, timeout=900)
    session = client.sessions.create(agent={"name": AGENT_NAME})
    sid = session.data.id
    (out_dir / "last_session").write_text(sid)
    print(f"{CYAN}{BOLD}TrueForge session {sid}{RESET}  ({base_url}/sessions/{sid})")
    decider = Decider(mode)
    events: dict[str, Any] = {}
    calls: dict[str, dict[str, Any]] = {}
    pending_input: list[dict[str, Any]] = [{"type": "user.message", "content": prompt}]
    approvals: list[dict[str, Any]] = []
    final_text, status = "", "unknown"
    started = time.monotonic()

    while pending_input:
        stream = client.sessions.create_turn_stream(session_id=sid, input=pending_input)
        pending_input = []
        paused: list[Any] = []
        for event in stream:
            if is_event_delta(event):
                base = events.get(event.id)
                if base is not None:
                    merge_event_delta(base, event)
                continue
            events[event.id] = event
            et = event.type
            if et == "model.message":
                for tc in getattr(event, "tool_calls", None) or []:
                    d = _dump(tc)
                    info = d.get("tool_info", {})
                    name = info.get("name") or d.get("function", {}).get("name")
                    calls[d["id"]] = {"name": name, "server": info.get("server_name"), "event": event.id}
            elif et == "tool.response":
                d = _dump(event)
                call = calls.get(d.get("tool_call_id", ""), {})
                name = call.get("name") or "?"
                content = d.get("content")
                text = content if isinstance(content, str) else json.dumps(content)
                icon = (
                    f"{RED}✗{RESET}" if '"success": false' in text or '"success":false' in text else f"{GREEN}✓{RESET}"
                )
                print(f"  {icon} {BOLD}{name:<26}{RESET} {DIM}{_summarise_result(text)}{RESET}")
            elif et == "sandbox.created":
                print(f"  {CYAN}▣ TrueForge sandbox created ({_dump(event).get('sandbox_id')}){RESET}")
            elif et == "tool.approval_required":
                paused.append(event)
            elif et == "turn.done":
                d = _dump(event)
                state = d.get("state", {})
                status = state.get("status", "unknown")
                out = state.get("output") or {}
                final_text = out.get("content") or final_text
        for pause in paused:
            for ref in pause.tool_calls:
                msg = events.get(ref.source_event_id)
                tc = next((t for t in (getattr(msg, "tool_calls", None) or []) if t.id == ref.id), None)
                if tc is None:
                    continue
                d = _dump(tc)
                args = json.loads(d.get("function", {}).get("arguments") or "{}")
                decision = decider.decide(d.get("tool_info", {}).get("name", "?"), args)
                approvals.append({"tool": d.get("tool_info", {}).get("name"), "decision": decision["status"]})
                pending_input.append(
                    {
                        "type": "user.tool_approval",
                        "thread_id": pause.thread_id,
                        "tool_call_id": ref.id,
                        "approval": decision,
                    }
                )
    print(f"\n{BOLD}── agent final message ─────────────────────────────{RESET}\n{final_text}\n")
    result = {
        "session_id": sid,
        "status": status,
        "approvals": approvals,
        "tool_calls": [c["name"] for c in calls.values()],
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "final_text": final_text,
    }
    (out_dir / "last_run.json").write_text(json.dumps(result, indent=2))
    return result


def main(prompt: str, mode: str) -> int:
    base = os.environ.get("TRUEFORGE_URL", "http://localhost:8790")
    out_dir = Path(os.environ.get("FORGESRE_RUN_DIR", Path(__file__).resolve().parents[3] / ".run"))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run(prompt, mode, base, out_dir)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    print(
        f"{DIM}session {result['session_id']} · {result['status']} · {len(result['tool_calls'])} tool calls · "
        f"{result['elapsed_seconds']}s{RESET}"
    )
    return 0 if result["status"] == "done" else 1
