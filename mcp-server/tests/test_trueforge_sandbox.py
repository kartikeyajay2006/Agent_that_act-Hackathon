"""TrueForge sandbox end-to-end: skill materialised in the sandbox, analyzer fetches evidence
through the harness-bridged mcp_client, computed JSON returns to the agent.

    uv run pytest -m trueforge -s tests/test_trueforge_sandbox.py

Uses a small inline agent spec so it also runs on small local models; the mechanisms
exercised (sandbox provider, skill mount, Code Mode MCP bridge) are the same ones the
saved `forgesre` agent uses.
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
COMMAND = "python {skills}/incident-diagnostics/scripts/diagnose.py --window 15"


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


if not (_up(f"{TF}/api/v1/capabilities") and _up("http://127.0.0.1:18080/health")):
    pytest.skip("TrueForge and demo stack must be running", allow_module_level=True)


def _daytona_configured() -> bool:
    data = httpx.get(f"{TF}/api/v1/settings/sandbox-providers", timeout=5).json().get("data")
    return bool(data)


def test_sandbox_diagnosis_uses_bridged_evidence():
    from trueforge_sdk import TrueForge
    from trueforge_sdk.events import is_event_delta, merge_event_delta

    ops = Ops(get_settings())
    route = httpx.get("http://127.0.0.1:18080/route", timeout=2).json()["active_version"]
    if route != "v2":
        subprocess.run([str(ROOT / "scripts/trigger-incident.sh")], check=True, cwd=ROOT, capture_output=True)
    subprocess.run([str(ROOT / "scripts/verify-incident.sh"), "150"], check=True, cwd=ROOT, capture_output=True)

    client = TrueForge(base_url=TF, timeout=1200)
    saved = next(a for a in client.agents.list() if a.name == "forgesre")
    spec = {
        "model": {"name": saved.manifest.model.name},
        "instructions": "You run diagnostics. Use the sandbox to execute commands and report their output verbatim.",
        "skills": [{"name": "incident-diagnostics"}],
        "mcp_servers": [
            {
                "name": "forgesre",
                "enable_tools": ["collect_incident_evidence"],
                "require_approval_for_tools": ["rollback_deployment", "@destructive"],
                "preload": True,
            }
        ],
        "config": {
            "sandbox": {"enabled": True},
            "generative_ui": {"enabled": False},
            "ask_user_questions": {"enabled": False},
            "dynamic_sub_agents": {"enabled": False},
            "iteration_limit": 12,
        },
    }
    sid = client.sessions.create(agent={"spec": spec}).data.id
    started = datetime.now(UTC).isoformat()
    skills_dir = "/opt/tfy/skills" if _daytona_configured() else "skills"
    prompt = (
        "Use the exec tool to run this exact shell command in the sandbox, then show its JSON output:\n"
        + COMMAND.format(skills=skills_dir)
    )

    events: dict = {}
    names: list[str] = []
    for ev in client.sessions.create_turn_stream(session_id=sid, input=[{"type": "user.message", "content": prompt}]):
        if is_event_delta(ev):
            if ev.id in events:
                merge_event_delta(events[ev.id], ev)
            continue
        events[ev.id] = ev
        if ev.type == "model.message":
            names += [tc.tool_info.name for tc in ev.tool_calls or []]
    httpx.post(f"{TF}/api/v1/sessions/{sid}/cancel", timeout=10)

    types = [e.type for e in events.values()]
    outputs = [str(e.content) for e in events.values() if e.type == "tool.response"]
    bridged = [
        e
        for e in ops.audit.events(types={"tool_call"})
        if e["source"] == "collect_incident_evidence" and e["timestamp"] >= started
    ]
    print(json.dumps({"session": sid, "tools": names, "sandbox_created": "sandbox.created" in types}, indent=2))
    print("bridged evidence calls:", len(bridged))
    print("analyzer output:", next((o for o in outputs if "evidence_score" in o), "")[:1500])
    assert "sandbox.created" in types
    assert bridged, "collect_incident_evidence never reached the MCP server from the sandbox"
    assert any("evidence_score" in o and "suspect_version" in o for o in outputs)
