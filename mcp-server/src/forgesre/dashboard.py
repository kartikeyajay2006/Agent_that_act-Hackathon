"""Mission-control dashboard (read-only). Served by the MCP server at /dashboard.

Everything shown is read live: container state and probes, Prometheus signals,
the audit log written by the MCP tools, and TrueForge's own session events
(for the pending-approval banner). Nothing here can change the system.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .config import Settings
from .ops import VERSIONED, Ops
from .probes import probe
from .prom import render
from .results import ToolError
from .trueforge import AGENT_NAME, MCP_SERVER_NAME, TrueForgeError

PHASES = ["Detect", "Investigate", "Prove", "Act", "Verify", "Recover"]
READ_TOOLS = {
    "get_service_health",
    "get_service_logs",
    "query_metrics",
    "get_database_health",
    "get_recent_deployments",
    "get_active_deployment",
    "list_services",
    "run_synthetic_check",
}

_cache: dict[str, Any] = {"at": 0.0, "state": None}


def _services(ops: Ops) -> list[dict[str, Any]]:
    active = ops.deploy.active_version(VERSIONED)
    out = []
    for inst in ops.catalog.instances():
        st = ops.docker.state(inst)
        row: dict[str, Any] = {"name": inst.name, "version": inst.version}
        if not st.get("exists"):
            row.update(status="absent", detail="not deployed")
        elif not st.get("running"):
            row.update(status="stopped", detail=st.get("status"))
        elif inst.version and inst.version != active:
            row.update(status="standby", detail="running, no traffic")
        elif inst.base_url and inst.name != "prometheus":
            ready = probe(inst, "/ready", timeout_s=1.5)
            row.update(
                status="healthy" if ready.get("ok") else "degraded",
                detail="ready" if ready.get("ok") else ((ready.get("body") or {}).get("reason") or "not ready"),
            )
        else:
            row.update(status="healthy", detail="running")
        row["serving"] = inst.version == active if inst.version else None
        row["restarts"] = st.get("restart_count")
        out.append(row)
    return out


def _series(ops: Ops, signal: str, minutes: int = 10) -> list[list[float]]:
    try:
        end = time.time()
        rows = ops.prom.range(render(signal, "30s"), end - minutes * 60, end, 5)
    except ToolError:
        return []
    if not rows:
        return []
    return [[t, v] for t, v in rows[0]["points"] if v is not None]


def _phase(events: list[dict[str, Any]], incident: dict[str, Any] | None) -> dict[str, Any]:
    if not incident:
        return {"phase": None, "iteration": 0, "note": "Monitoring. No open incident."}
    phase, iteration, note = "Detect", 1, "Alerts firing; incident opened."
    for e in events:
        t, src = e["type"], e["source"]
        if t == "tool_call" and src in READ_TOOLS and phase in ("Detect", "Verify", "Investigate"):
            if phase == "Verify":
                iteration += 1
            phase, note = "Investigate", f"Reading {src.replace('_', ' ')}"
        elif t == "evidence_collected":
            phase, note = "Prove", "Evidence bundle pulled for sandbox diagnosis"
        elif t == "risk_assessed":
            phase, note = "Act", f"Risk assessed: {e['data'].get('action')} → {e['data'].get('risk_level')}"
        elif t == "action_started":
            phase, note = "Act", f"{src} running"
        elif t == "action_completed":
            phase, note = "Act", f"{src} {e['status']}"
        elif t == "verification_started":
            phase, note = "Verify", "Measuring post-action signals"
        elif t == "verification_completed":
            if e["status"] == "RECOVERED":
                phase, note = "Recover", "Verification passed every criterion"
            else:
                phase = "Verify"
                failed = ", ".join(e["data"].get("failed_criteria") or [])
                note = f"Not recovered ({failed}); back to investigation"
        elif t == "approval_missing":
            note = "Rollback refused: no human approval found in TrueForge"
    return {"phase": phase, "iteration": iteration, "note": note}


def _trueforge(ops: Ops) -> dict[str, Any]:
    tf = ops.trueforge
    try:
        sessions = tf._req("GET", "/api/v1/sessions", params={"limit": 10, "order": "desc"}).get("data", [])
    except TrueForgeError:
        return {"reachable": False}
    session = next(
        (s for s in sessions if (s.get("agent") or {}).get("name") == AGENT_NAME or AGENT_NAME in str(s.get("agent"))),
        None,
    )
    if not session:
        return {"reachable": True, "session": None, "url": tf.base_url}
    try:
        events = tf.session_events(session["id"], max_pages=5)
    except TrueForgeError:
        events = []
    calls: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("type") == "model.message":
            for tc in e.get("tool_calls") or []:
                info = tc.get("tool_info") or {}
                if info.get("server_name") == MCP_SERVER_NAME:
                    calls[tc.get("id")] = {
                        "tool": info.get("name"),
                        "arguments": (tc.get("function") or {}).get("arguments"),
                    }
    try:
        decisions = tf.approvals(session["id"], events)
    except TrueForgeError:
        decisions = []
    decided = {d.get("tool_call_id"): (d.get("approval") or {}).get("status") for d in decisions}
    pending, last_decision = None, None
    for e in events:
        if e.get("type") == "tool.approval_required":
            for ref in e.get("tool_calls") or []:
                if ref.get("id") not in decided:
                    pending = calls.get(ref.get("id")) or {"tool": "unknown"}
                else:
                    last_decision = {**(calls.get(ref.get("id")) or {}), "decision": decided[ref.get("id")]}
    tool_count = sum(1 for e in events if e.get("type") == "tool.response")
    return {
        "reachable": True,
        "url": tf.base_url,
        "session": {"id": session["id"], "title": session.get("title"), "tool_responses": tool_count},
        "pending_approval": pending,
        "last_decision": last_decision,
        "sandbox_used": any(e.get("type") == "sandbox.created" for e in events),
    }


def build_state(ops: Ops, settings: Settings) -> dict[str, Any]:
    incident = ops.audit.incident()
    events = ops.audit.events(incident["incident_id"]) if incident else []
    verifications = [e for e in events if e["type"] == "verification_completed"]
    report_file = settings.artifacts_dir / "incidents" / f"{incident['incident_id']}.md" if incident else None
    try:
        alerts = ops._alerts()
    except ToolError:
        alerts = []
    gw = probe(ops.catalog.instance("api-gateway"), "/route", timeout_s=1.5)
    return {
        "environment": settings.environment,
        "generated_at": time.time(),
        "active_version": ops.deploy.active_version(VERSIONED),
        "gateway_route": (gw.get("body") or {}).get("active_version") if gw.get("ok") else None,
        "services": _services(ops),
        "signals": ops.snapshot(),
        "series": {
            "checkout_error_rate": _series(ops, "checkout_error_rate"),
            "checkout_latency_p95": _series(ops, "checkout_latency_p95"),
            "db_connection_utilization": _series(ops, "db_connection_utilization"),
            "checkout_request_rate": _series(ops, "checkout_request_rate"),
        },
        "alerts": [a for a in alerts if a["state"] == "firing"],
        "incident": incident,
        "loop": _phase(events, incident),
        "timeline": [
            {
                "at": e["timestamp"],
                "type": e["type"],
                "source": e["source"],
                "status": e["status"],
                "target": e["data"].get("target") or e["data"].get("to_version"),
            }
            for e in events
            if e["type"] != "tool_call" or e["source"] not in ("get_incident_timeline",)
        ][-40:],
        "verification": verifications[-1]["data"] if verifications else None,
        "verdicts": [v["status"] for v in verifications],
        "before": (incident or {}).get("snapshot_at_open"),
        "after": verifications[-1]["data"].get("signals") if verifications else None,
        "report": str(report_file.relative_to(settings.root))
        if report_file and report_file.exists() and report_file.is_relative_to(settings.root)
        else None,
        "trueforge": _trueforge(ops),
    }


def register(mcp: MCPServer, ops: Ops, settings: Settings) -> None:
    page = (Path(__file__).parent / "dashboard.html").read_text()

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(_: Request) -> HTMLResponse:
        return HTMLResponse(page)

    @mcp.custom_route("/api/state", methods=["GET"])
    async def state(_: Request) -> JSONResponse:
        if _cache["state"] is None or time.monotonic() - _cache["at"] > 1.5:
            _cache["state"] = await anyio.to_thread.run_sync(build_state, ops, settings)
            _cache["at"] = time.monotonic()
        return JSONResponse(_cache["state"])
