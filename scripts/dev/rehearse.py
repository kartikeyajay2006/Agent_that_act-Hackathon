"""Rehearsal mode: try the whole flow without a model API key.

    ./scripts/rehearse.sh            # background; prints the TrueForge session URL to open

Runs the saved `forgesre` agent in TrueForge with the model swapped for the scripted test double
(mcp-server/tests/scripted_model.py). Everything else is real: TrueForge routes the tool calls,
runs the sandbox, and pauses on rollback_deployment. YOU click Allow or Deny in the TrueForge UI;
this process keeps the stand-in model online until the session finishes. For a real run, set
MODEL_API_KEY and use the TrueForge chat directly.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp-server" / "tests"))

import scripted_model  # noqa: E402

TF = os.environ.get("TRUEFORGE_URL", "http://localhost:8790")
PROVIDER = {
    "type": "custom",
    "name": "forgesre-scripted",
    "base_url": "http://127.0.0.1:18990/v1",
    "models": [
        {
            "model_id": "scripted-sre",
            "name": "scripted-sre",
            "properties": {"context_length": 200000},
        }
    ],
}
PROMPT = "Checkout is failing in production. Investigate and recover the service."


def main() -> None:
    from trueforge_sdk import TrueForge

    scripted_model.serve_in_background(18990)
    existing = httpx.get(f"{TF}/api/v1/settings/model-providers", timeout=10).json().get("data", [])
    method = "PUT" if any(p.get("manifest", p).get("name") == PROVIDER["name"] for p in existing) else "POST"
    httpx.request(
        method,
        f"{TF}/api/v1/settings/model-providers",
        json={"manifest": PROVIDER},
        timeout=10,
    ).raise_for_status()

    client = TrueForge(base_url=TF, timeout=900)
    saved = next(a for a in client.agents.list() if a.name == "forgesre")
    spec = saved.manifest.model_dump(mode="json", exclude_none=True)
    spec["model"] = {"name": f"{PROVIDER['name']}/scripted-sre"}
    sid = client.sessions.create(agent={"spec": spec}).data.id
    (ROOT / ".run" / "rehearsal_session").write_text(sid)
    print(f"session {sid}: running investigation…", flush=True)
    for _ in client.sessions.create_turn_stream(session_id=sid, input=[{"type": "user.message", "content": PROMPT}]):
        pass
    print(
        f"PAUSED for approval → open {TF}/sessions/{sid} and click Allow or Deny",
        flush=True,
    )

    # Keep the stand-in model online while the human decides and the agent finishes.
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        turns = httpx.get(f"{TF}/api/v1/sessions/{sid}/turns", params={"limit": 25}, timeout=10).json().get("data", [])
        resumed = [t for t in turns if any(i.get("type") == "user.tool_approval" for i in t.get("input") or [])]
        if resumed and resumed[0].get("state", {}).get("status") in (
            "done",
            "failed",
            "cancelled",
        ):
            decision = next(i for i in resumed[0]["input"] if i.get("type") == "user.tool_approval")["approval"][
                "status"
            ]
            print(f"session finished after human decision: {decision}", flush=True)
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
