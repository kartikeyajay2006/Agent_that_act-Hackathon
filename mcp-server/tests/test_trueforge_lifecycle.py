"""Full incident lifecycle through TrueForge with a scripted model (harness contract test).

    uv run pytest -m trueforge -s tests/test_trueforge_lifecycle.py

The saved `forgesre` agent spec is reused as-is (instructions, MCP tools, approval gate, skill,
sandbox) with only the model swapped for tests/scripted_model.py, so the result depends on
TrueForge and ForgeSRE — not on an LLM's reasoning. Checks, in order:

  sandbox created, skill analyzer ran in it, evidence reached it via the Code Mode bridge
  restart executed autonomously, verification said NOT_RECOVERED
  TrueForge paused rollback; the MCP server had not seen it; human approves
  rollback executed with attestation; verification RECOVERED; report RESOLVED
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import httpx
import pytest

from forgesre.config import get_settings
from forgesre.ops import Ops

pytestmark = pytest.mark.trueforge
ROOT = get_settings().root
TF = "http://localhost:8790"
STUB_PORT = 18990
PROVIDER = {
    "type": "custom",
    "name": "forgesre-scripted",
    "base_url": f"http://127.0.0.1:{STUB_PORT}/v1",
    "models": [{"model_id": "scripted-sre", "name": "scripted-sre", "properties": {"context_length": 200000}}],
}


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


if not (_up(f"{TF}/api/v1/capabilities") and _up("http://127.0.0.1:18080/health")):
    pytest.skip("TrueForge and demo stack must be running", allow_module_level=True)


def route() -> str:
    return httpx.get("http://127.0.0.1:18080/route", timeout=2).json()["active_version"]


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_full_lifecycle_through_trueforge(decision):
    import scripted_model
    from trueforge_sdk import TrueForge
    from trueforge_sdk.events import is_event_delta, merge_event_delta

    scripted_model.serve_in_background(STUB_PORT)
    existing = httpx.get(f"{TF}/api/v1/settings/model-providers", timeout=10).json().get("data", [])
    method = "PUT" if any(p.get("manifest", p).get("name") == PROVIDER["name"] for p in existing) else "POST"
    httpx.request(
        method, f"{TF}/api/v1/settings/model-providers", json={"manifest": PROVIDER}, timeout=10
    ).raise_for_status()

    assert subprocess.run([str(ROOT / "scripts/reset-demo.sh"), "150"], cwd=ROOT, capture_output=True).returncode == 0
    assert subprocess.run([str(ROOT / "scripts/trigger-incident.sh")], cwd=ROOT, capture_output=True).returncode == 0
    assert (
        subprocess.run([str(ROOT / "scripts/verify-incident.sh"), "150"], cwd=ROOT, capture_output=True).returncode == 0
    )

    client = TrueForge(base_url=TF, timeout=900)
    saved = next(a for a in client.agents.list() if a.name == "forgesre")
    spec = saved.manifest.model_dump(mode="json", exclude_none=True)
    spec["model"] = {"name": f"{PROVIDER['name']}/scripted-sre"}
    sid = client.sessions.create(agent={"spec": spec}).data.id
    started = datetime.now(UTC).isoformat()
    ops = Ops(get_settings())

    events: dict = {}
    calls: dict[str, str] = {}
    approvals_seen = 0
    pending_input: list = [
        {"type": "user.message", "content": "Checkout is failing in production. Investigate and recover."}
    ]
    while pending_input:
        paused = []
        for ev in client.sessions.create_turn_stream(session_id=sid, input=pending_input):
            if is_event_delta(ev):
                if ev.id in events:
                    merge_event_delta(events[ev.id], ev)
                continue
            events[ev.id] = ev
            if ev.type == "tool.approval_required":
                paused.append(ev)
        for e in events.values():  # tool calls stream as deltas; read them once merged
            if e.type == "model.message":
                for tc in e.tool_calls or []:
                    calls[tc.id] = tc.tool_info.name
        pending_input = []
        for p in paused:
            for ref in p.tool_calls:
                assert calls.get(ref.id) == "rollback_deployment"
                # Paused in the harness: the server has not seen the call, production unchanged.
                assert route() == "v2"
                assert not [e for e in ops.audit.events(types={"tool_call"}) if e["source"] == "rollback_deployment"]
                approvals_seen += 1
                pending_input.append(
                    {
                        "type": "user.tool_approval",
                        "thread_id": p.thread_id,
                        "tool_call_id": ref.id,
                        "approval": {"status": "allow"}
                        if decision == "allow"
                        else {"status": "deny", "reason": "Denied by on-call engineer (contract test)."},
                    }
                )

    types = [e.type for e in events.values()]
    responses = {e.tool_call_id: str(e.content) for e in events.values() if e.type == "tool.response"}
    by_tool = {}
    for cid, name in calls.items():
        by_tool.setdefault(name, []).append(responses.get(cid, ""))

    bridged = [
        e for e in ops.audit.events(types={"tool_call"})
        if e["source"] == "collect_incident_evidence" and e["timestamp"] >= started
    ]  # fmt: skip
    verdicts = [e["status"] for e in ops.audit.events(types={"verification_completed"})]
    rollback = [e for e in ops.audit.events(types={"action_started"}) if e["source"] == "rollback_deployment"]
    report_out = by_tool.get("generate_incident_report", [""])[-1]
    summary = {
        "session": sid,
        "tool_sequence": list(calls.values()),
        "sandbox_created": "sandbox.created" in types,
        "sandbox_output_head": by_tool.get("exec", [""])[0][:600],
        "bridged_evidence_calls": len(bridged),
        "verdicts": verdicts,
        "approvals": approvals_seen,
        "attestation": rollback[-1]["data"]["approval"]["attestation"] if rollback else None,
        "route": route(),
    }
    print(json.dumps(summary, indent=2))

    assert "sandbox.created" in types
    assert "evidence_score" in by_tool["exec"][0] and "suspect_version" in by_tool["exec"][0]
    assert bridged, "evidence did not reach the MCP server through the sandbox bridge"
    assert approvals_seen == 1
    if decision == "allow":
        assert verdicts[:2] == ["NOT_RECOVERED", "RECOVERED"]
        assert summary["attestation"]["session_id"] == sid
        assert route() == "v1"
        assert "RESOLVED" in report_out and "UNRESOLVED" not in report_out
    else:
        assert verdicts == ["NOT_RECOVERED"]
        assert not rollback, "a denied rollback must never reach the MCP server"
        assert route() == "v2"
        assert "UNRESOLVED" in report_out
