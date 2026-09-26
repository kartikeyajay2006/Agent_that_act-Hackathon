"""TEST DOUBLE: a scripted OpenAI-compatible model for deterministic harness contract tests.

This is NOT the ForgeSRE agent and is never used in the demo. It stands in for the LLM so the
TrueForge integration (tool routing, sandbox + skill + Code Mode bridge, approval pause, MCP
side effects) can be exercised end to end without depending on a model's reasoning quality.
TrueForge still runs the loop; this server only answers /v1/chat/completions with the next
step of a fixed incident runbook, reading earlier tool results from the conversation.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

DIAG = (
    "if [ -f skills/incident-diagnostics/scripts/diagnose.py ]; "
    "then python skills/incident-diagnostics/scripts/diagnose.py --window 15; "
    "else mcp-client call-tool forgesre get_reference_analyzer '{}' | "
    "python3 -c \"import json,sys; open('diagnose.py','w').write(json.load(sys.stdin)['source'])\" && "
    "python diagnose.py --window 15; fi"
)

# What a model typically writes in step 5a: a small script that pulls evidence through the harness bridge.
GENERATED = """cat > my_diag.py <<'PY'
import asyncio, json
from mcp_client import call_tool

async def main():
    ev = await call_tool("forgesre", "collect_incident_evidence", body={"window_minutes": 15})
    err = ev["metrics"]["checkout_error_rate"][0]["points"]
    start = next((t for t, v in err if v is not None and v > 0.05), None)
    before = [v for t, v in err if start and t < start and v is not None]
    fails = {}
    for s in ev["metrics"]["checkout_errors_by_reason"]:
        ver = s["labels"].get("upstream_version")
        fails[ver] = fails.get(ver, 0) + sum(v for t, v in s["points"] if v is not None)
    print(json.dumps({"incident_start": start, "baseline": sum(before) / len(before) if before else None,
                      "peak": max(v for t, v in err if v is not None), "failures_by_version": fails}))

asyncio.run(main())
PY
python my_diag.py"""
REASON_RESTART = "checkout failing, payment-service-v2 pool exhausted per logs and metrics; reversible restart first"
REASON_ROLLBACK = "restart did not recover: v2 carries all failures, deployed shortly before the spike, pool saturated"
APPROVAL_BRIEF = (
    "⚠ APPROVAL REQUIRED — rollback_deployment\n"
    "Hypothesis: payment-service v2 exhausts its database connection pool.\n"
    "Evidence: checkout failures are concentrated on v2; v2 pool is saturated; logs show db_pool_timeout; "
    "v2 was deployed immediately before the spike.\n"
    "Safe action: restart_service verified NOT_RECOVERED.\n"
    "Blast radius: checkout and api-gateway depend on this service.\n"
    "Target readiness: v1 is ready. Recovery plan: roll forward to v2 only after the defect is fixed."
)

STEPS: list[tuple[str, dict[str, Any], str]] = [
    ("get_incident_context", {}, "Checking alerts and current signals."),
    (
        "get_service_logs",
        {"service": "payment-service", "since_minutes": 10, "level": "ERROR", "limit": 5},
        "Reading errors.",
    ),
    ("get_recent_deployments", {"service": "payment-service"}, "Checking recent changes."),
    ("exec", {"intent": "Run my own diagnostic script", "command": GENERATED}, "Writing and running a diagnostic."),
    ("exec", {"intent": "Cross-check with the reference analyzer", "command": DIAG}, "Cross-checking in the sandbox."),
    ("assess_action_risk", {"action": "restart_service", "service": "payment-service-v2"}, "Assessing restart risk."),
    ("restart_service", {"service": "payment-service-v2", "reason": REASON_RESTART}, "Restarting v2 (YELLOW)."),
    ("verify_recovery", {}, "Verifying the restart."),
    (
        "assess_action_risk",
        {"action": "rollback_deployment", "service": "payment-service", "target_version": "v1"},
        "Assessing rollback risk.",
    ),
    (
        "rollback_deployment",
        {
            "service": "payment-service",
            "from_version": "v2",
            "to_version": "v1",
            "reason": REASON_ROLLBACK,
            "approval_brief": APPROVAL_BRIEF,
        },
        "Requesting approval for the rollback (RED).",
    ),
    ("verify_recovery", {}, "Verifying the rollback."),
    ("generate_incident_report", None, "Writing the incident report."),
]


def _tool_results(messages: list[dict[str, Any]]) -> list[str]:
    out = []
    for m in messages:
        if m.get("role") == "tool":
            c = m.get("content")
            out.append(c if isinstance(c, str) else json.dumps(c))
    return out


def _report_args(results: list[str], denied: bool) -> dict[str, Any]:
    sandbox = next((r for r in results if "evidence_score" in r), None)
    return {
        "summary": "Scripted harness contract test of the ForgeSRE incident lifecycle (test double model).",
        "root_cause": "Hypothesis under test: payment-service v2 exhausts its database connection pool.",
        "evidence": ["See recorded tool results and sandbox output for this scripted test run."],
        "confidence": "MEDIUM",
        "sandbox_analysis": (sandbox or "")[:2900] or None,
        "human_decisions": ["rollback_deployment denied in TrueForge"] if denied else ["rollback_deployment approved"],
        "follow_ups": ["Scripted test run — no follow-ups."],
    }


def next_step(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages", [])
    offered = [t["function"]["name"] for t in body.get("tools") or [] if t.get("type") == "function"]
    done = sum(1 for m in messages if m.get("role") == "assistant" and m.get("tool_calls"))
    results = _tool_results(messages)
    denied = any("User denied tool call" in r for r in results)
    if denied and not any("final_status" in r for r in results):
        step = ("generate_incident_report", None, "Rollback denied; filing the report without changing anything.")
    elif denied or done >= len(STEPS):
        return {"text": "Scripted run complete."}
    else:
        step = STEPS[done]
    name, args, note = step
    if args is None:
        args = _report_args(results, denied)
    resolved = next((o for o in offered if o == name or o.endswith(f"__{name}") or o.endswith(f"-{name}")), None)
    if resolved is None:
        return {"text": f"Scripted model: tool {name} is not offered by the harness (offered: {offered[:40]})."}
    return {"text": note, "tool": resolved, "args": args}


def _chunks(step: dict[str, Any], model: str):
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model}

    def ev(obj: dict[str, Any]) -> str:
        return f"data: {json.dumps({**base, **obj})}\n\n"

    yield ev(
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": step["text"]}, "finish_reason": None}]}
    )
    finish = "stop"
    if "tool" in step:
        call = {
            "index": 0,
            "id": f"call_{uuid.uuid4().hex[:10]}",
            "type": "function",
            "function": {"name": step["tool"], "arguments": json.dumps(step["args"])},
        }
        yield ev({"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]})
        finish = "tool_calls"
    yield ev({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    yield ev({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    yield "data: [DONE]\n\n"


async def completions(request: Request):
    body = await request.json()
    step = next_step(body)
    model = body.get("model", "scripted")
    if body.get("stream"):
        return StreamingResponse(_chunks(step, model), media_type="text/event-stream")
    message: dict[str, Any] = {"role": "assistant", "content": step["text"]}
    if "tool" in step:
        message["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "type": "function",
                "function": {"name": step["tool"], "arguments": json.dumps(step["args"])},
            }
        ]
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if "tool" in step else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


app = Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"])])


_servers: dict[int, uvicorn.Server] = {}


def serve_in_background(port: int = 18990) -> uvicorn.Server:
    if port in _servers:  # one stand-in model per process and port
        return _servers[port]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    _servers[port] = server
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    return server
