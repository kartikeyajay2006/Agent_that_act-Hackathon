"""End-to-end approval-gate test through a real TrueForge session.

    uv run pytest -m trueforge -s

Needs: demo stack + MCP server + TrueForge running and ./scripts/setup-trueforge.sh done.
Uses the saved `forgesre` agent. The prompt asks the model to go straight to the RED
tool so the test isolates the harness behaviour (not the model's investigation):

1. TrueForge pauses on rollback_deployment -> MCP server has NOT been called, route unchanged.
2. DENY  -> still v2, no rollback executed, no rollback call ever reached the server.
3. APPROVE (new session) -> rollback executes, server recorded TrueForge's attestation.
"""

from __future__ import annotations

import json
import subprocess
import time

import httpx
import pytest

from forgesre.config import get_settings
from forgesre.ops import Ops

pytestmark = pytest.mark.trueforge
ROOT = get_settings().root
TF = "http://localhost:8790"
PROMPT = (
    "Test of the approval gate. Call the rollback_deployment tool right now, before any other tool, with "
    'service="payment-service", from_version="v2", to_version="v1", '
    'reason="approval gate test: checkout errors after the v2 deployment", '
    'approval_brief="Approval gate test: v2 is serving checkout failures after deployment; v1 is the ready rollback '
    'target. This test verifies that TrueForge holds the production-changing call for explicit human approval.". '
    "If the call is denied, reply with the single word DENIED and stop."
)


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


if not (_up(f"{TF}/api/v1/capabilities") and _up("http://127.0.0.1:18080/health")):
    pytest.skip("TrueForge and demo stack must be running", allow_module_level=True)


@pytest.fixture(scope="module")
def ops():
    return Ops(get_settings())


def route() -> str:
    return httpx.get("http://127.0.0.1:18080/route", timeout=2).json()["active_version"]


def rollback_calls_received(ops) -> int:
    return sum(1 for e in ops.audit.events(types={"tool_call"}) if e["source"] == "rollback_deployment")


def run_until_pause(client, sid: str, input_: list, stop_on_response: str | None = None) -> tuple[dict, list]:
    """Stream one turn. Stops early once `stop_on_response` (a tool_call_id) has its tool.response."""
    from trueforge_sdk.events import is_event_delta, merge_event_delta

    events: dict = {}
    paused = []
    for ev in client.sessions.create_turn_stream(session_id=sid, input=input_):
        if is_event_delta(ev):
            if ev.id in events:
                merge_event_delta(events[ev.id], ev)
            continue
        events[ev.id] = ev
        if ev.type == "tool.approval_required":
            paused.append(ev)
        if stop_on_response and ev.type == "tool.response" and ev.tool_call_id == stop_on_response:
            break
    return events, paused


def cancel(sid: str) -> None:
    httpx.post(f"{TF}/api/v1/sessions/{sid}/cancel", timeout=10)


def pending_rollback(events: dict, paused: list):
    for p in paused:
        for ref in p.tool_calls:
            msg = events[ref.source_event_id]
            for tc in msg.tool_calls or []:
                if tc.id == ref.id and tc.tool_info.name == "rollback_deployment":
                    return p, tc
    return None, None


def ensure_incident(ops):
    if route() != "v2":
        subprocess.run([str(ROOT / "scripts/trigger-incident.sh")], check=True, cwd=ROOT, capture_output=True)
    subprocess.run([str(ROOT / "scripts/verify-incident.sh"), "150"], check=True, cwd=ROOT, capture_output=True)
    ops._ensure_incident()


def test_deny_blocks_rollback(ops):
    from trueforge_sdk import TrueForge

    ensure_incident(ops)
    client = TrueForge(base_url=TF, timeout=1200)
    sid = client.sessions.create(agent={"name": "forgesre"}).data.id
    before_calls = rollback_calls_received(ops)

    events, paused = run_until_pause(client, sid, [{"type": "user.message", "content": PROMPT}])
    pause, call = pending_rollback(events, paused)
    assert call is not None, "TrueForge did not pause on rollback_deployment"
    # Paused inside the harness: the MCP server has not been called and nothing changed.
    assert rollback_calls_received(ops) == before_calls
    assert route() == "v2"

    decision = {"status": "deny", "reason": "Denied by on-call engineer (gate test)."}
    msg = {"type": "user.tool_approval", "thread_id": pause.thread_id, "tool_call_id": call.id, "approval": decision}
    events2, _ = run_until_pause(client, sid, [msg], stop_on_response=call.id)
    cancel(sid)
    time.sleep(2)
    assert route() == "v2"
    assert rollback_calls_received(ops) == before_calls
    responses = [e for e in events2.values() if e.type == "tool.response" and e.tool_call_id == call.id]
    print("\nTrueForge tool.response after deny:", responses[0].content if responses else None)
    print(json.dumps({"session": sid, "decision": "deny", "route": route()}))


def test_approve_executes_rollback_with_attestation(ops):
    from trueforge_sdk import TrueForge

    ensure_incident(ops)
    client = TrueForge(base_url=TF, timeout=1200)
    sid = client.sessions.create(agent={"name": "forgesre"}).data.id
    events, paused = run_until_pause(client, sid, [{"type": "user.message", "content": PROMPT}])
    pause, call = pending_rollback(events, paused)
    assert call is not None
    assert route() == "v2"

    msg = {
        "type": "user.tool_approval",
        "thread_id": pause.thread_id,
        "tool_call_id": call.id,
        "approval": {"status": "allow"},
    }
    run_until_pause(client, sid, [msg], stop_on_response=call.id)
    cancel(sid)
    assert route() == "v1"
    started = [e for e in ops.audit.events(types={"action_started"}) if e["source"] == "rollback_deployment"]
    att = started[-1]["data"]["approval"]["attestation"]
    assert att["session_id"] == sid and att["tool_call_id"] == call.id
    print(json.dumps({"session": sid, "decision": "allow", "attestation": att, "route": route()}))
