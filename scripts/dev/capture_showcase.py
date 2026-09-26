"""Regenerate the README showcase images from a live run.

    uv run --project mcp-server python scripts/dev/capture_showcase.py [--clean-sessions]

Drives the real stack and a real TrueForge session. The session uses the saved `forgesre` agent
spec with the model swapped for the scripted test double (mcp-server/tests/scripted_model.py) so
the pictures are reproducible without an API key; every tool call, the sandbox, the approval
pause and all system state are real. Images land in docs/assets/showcase/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp-server" / "tests"))

import scripted_model  # noqa: E402

TF = "http://localhost:8790"
DASH = "http://127.0.0.1:18900/dashboard"
OUT = ROOT / "docs" / "assets" / "showcase"
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


def sh(*cmd: str) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def snap(url: str, name: str, *extra: str) -> Path:
    path = OUT / name
    sh("node", str(ROOT / "scripts/dev/snap.mjs"), url, str(path), *extra)
    return path


def stream(client, sid: str, input_: list) -> tuple[dict, list]:
    from trueforge_sdk.events import is_event_delta, merge_event_delta

    events, paused = {}, []
    for ev in client.sessions.create_turn_stream(session_id=sid, input=input_):
        if is_event_delta(ev):
            if ev.id in events:
                merge_event_delta(events[ev.id], ev)
            continue
        events[ev.id] = ev
        if ev.type == "tool.approval_required":
            paused.append(ev)
    return events, paused


def main() -> None:
    from trueforge_sdk import TrueForge

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--clean-sessions",
        action="store_true",
        help="delete earlier TrueForge sessions first",
    )
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    client = TrueForge(base_url=TF, timeout=900)

    if args.clean_sessions:
        for s in client.sessions.list():
            httpx.delete(f"{TF}/api/v1/sessions/{s.id}", timeout=10)

    scripted_model.serve_in_background(18990)
    existing = httpx.get(f"{TF}/api/v1/settings/model-providers", timeout=10).json().get("data", [])
    method = "PUT" if any(p.get("manifest", p).get("name") == PROVIDER["name"] for p in existing) else "POST"
    httpx.request(
        method,
        f"{TF}/api/v1/settings/model-providers",
        json={"manifest": PROVIDER},
        timeout=10,
    ).raise_for_status()

    sh("./scripts/reset-demo.sh")
    time.sleep(20)  # let the healthy sparklines fill in
    snap(DASH, "01-healthy.png", "--height", "900")

    sh("./scripts/trigger-incident.sh")
    sh("./scripts/verify-incident.sh", "150")
    time.sleep(10)
    snap(DASH, "02-incident.png", "--height", "900")

    saved = next(a for a in client.agents.list() if a.name == "forgesre")
    spec = saved.manifest.model_dump(mode="json", exclude_none=True)
    spec["model"] = {"name": f"{PROVIDER['name']}/scripted-sre"}
    sid = client.sessions.create(agent={"spec": spec}).data.id
    _events, paused = stream(
        client,
        sid,
        [
            {
                "type": "user.message",
                "content": "Checkout is failing in production. Investigate and recover the service.",
            }
        ],
    )
    assert paused, "expected TrueForge to pause on rollback_deployment"
    time.sleep(3)
    snap(DASH, "03-approval-dashboard.png", "--height", "900")
    snap(
        f"{TF}/sessions/{sid}", "04-trueforge-approval.png", "--height", "1300", "--wait", "6000",
        "--click", "1 tool needs your input", "--crop", "1104x1300+336+0",  # opens the Allow/Deny panel
    )  # fmt: skip

    approvals = [
        {
            "type": "user.tool_approval",
            "thread_id": p.thread_id,
            "tool_call_id": ref.id,
            "approval": {"status": "allow"},
        }
        for p in paused
        for ref in p.tool_calls
    ]
    stream(client, sid, approvals)
    time.sleep(8)
    snap(DASH, "05-recovered.png", "--height", "900")
    snap(
        f"{TF}/sessions/{sid}", "06-trueforge-trace.png", "--height", "1100", "--wait", "6000",
        "--click", "Agent steps", "--crop", "1104x1000+336+0",
    )  # fmt: skip

    incident = json.loads((ROOT / "state" / "incident.json").read_text())
    report = ROOT / "artifacts" / "incidents" / f"{incident['incident_id']}.md"
    if report.exists():
        note = (
            "<!-- Example output of scripts/dev/capture_showcase.py: TrueForge ran the loop with a scripted "
            "test-double model; every number below comes from the live stack. -->\n\n"
        )
        (ROOT / "artifacts" / "incidents" / "example-contract-test-approved.md").write_text(note + report.read_text())

    frames = [
        OUT / f
        for f in (
            "01-healthy.png",
            "02-incident.png",
            "03-approval-dashboard.png",
            "05-recovered.png",
        )
    ]
    captions = [
        "1  Healthy: payment-service v1, 0% errors",
        "2  Release v2 ships: checkout errors, DB connections exhausted",
        "3  Restart did not fix it. TrueForge holds the rollback for a human",
        "4  Approved: traffic back on v1, verified recovered",
    ]
    if shutil.which("magick"):
        labelled = []
        for f, c in zip(frames, captions, strict=True):
            lf = OUT / f"_gif_{f.name}"
            sh(
                "magick", str(f), "-resize", "1200x", "-gravity", "south", "-background", "#0f1720", "-splice", "0x56",
                "-fill", "#dce4ec", "-pointsize", "26", "-annotate", "+0+14", c, str(lf),
            )  # fmt: skip
            labelled.append(str(lf))
        sh(
            "magick",
            "-delay",
            "260",
            "-loop",
            "0",
            *labelled,
            "-layers",
            "Optimize",
            str(OUT / "walkthrough.gif"),
        )
        for lf in labelled:
            Path(lf).unlink()
    print(f"session {sid}; images in {OUT}")


if __name__ == "__main__":
    main()
