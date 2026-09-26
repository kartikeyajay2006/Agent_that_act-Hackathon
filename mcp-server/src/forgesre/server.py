"""ForgeSRE MCP server (streamable HTTP) — the tool surface TrueForge orchestrates.

Tool annotations are set honestly so TrueForge's @read-only / @write /
@destructive selectors resolve correctly; rollback_deployment is additionally
named explicitly in the agent's require_approval_for_tools.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import get_settings
from .ops import Ops
from .prom import SIGNALS
from .report import build_report
from .results import ToolError, from_error, ok

settings = get_settings()
ops = Ops(settings)

mcp = MCPServer(
    name="forgesre",
    title="ForgeSRE production operations",
    instructions=(
        "Tools for investigating and remediating incidents in the demo-production environment. "
        "Read tools are safe. restart_service is a reversible YELLOW action. rollback_deployment is a RED "
        "action gated by human approval. A successful action never implies recovery: always call "
        "verify_recovery afterwards."
    ),
    version="0.1.0",
)

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
PROBE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
YELLOW = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
RED = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

SignalName = Literal[tuple(SIGNALS)]  # type: ignore[valid-type]
ServiceName = Annotated[
    str,
    Field(description="Service or instance name, e.g. api-gateway, payment-service, payment-service-v2, postgres"),
]


async def _call(tool: str, fn: Callable[[], Any], args: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = await anyio.to_thread.run_sync(fn)
    except ToolError as exc:
        ops.audit.record("tool_call", tool, exc.code, {"args": args, "ms": _ms(started)})
        return from_error(exc)
    status = result.get("status", "success") if isinstance(result, dict) else "success"
    ops.audit.record("tool_call", tool, status, {"args": args, "ms": _ms(started)})
    return result


async def _acall(tool: str, coro_fn: Callable[[], Any], args: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = await coro_fn()
    except ToolError as exc:
        ops.audit.record("tool_call", tool, exc.code, {"args": args, "ms": _ms(started)})
        return from_error(exc)
    ops.audit.record("tool_call", tool, result.get("status", "success"), {"args": args, "ms": _ms(started)})
    return result


def _as_text(value: Any, limit: int = 6000) -> str | None:
    """Sandbox output arrives as text or (after the SDK pre-parses JSON-looking strings) as an object,
    sometimes still wrapped in the exec envelope. Normalise to readable text."""
    if value is None:
        return None
    if isinstance(value, dict) and isinstance((value.get("response") or {}).get("result"), str):
        value = value["response"]["result"]
    if isinstance(value, str):
        kept = [ln for ln in value.splitlines() if not ln.startswith("<frozen site>")]
        value = "\n".join(kept).strip()
        try:
            value = json.loads(value)
        except ValueError:
            return value[:limit]
    return ("```json\n" + json.dumps(value, indent=2, default=str) + "\n```")[:limit]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# --------------------------------------------------------------------------- GREEN: observation


@mcp.tool(annotations=READ)
async def list_services() -> dict[str, Any]:
    """List every service/instance in the environment with container status, which payment-service
    version receives traffic, and whether policy allows the agent to restart it."""
    return await _call("list_services", ops.list_services, {})


@mcp.tool(annotations=READ)
async def get_incident_context() -> dict[str, Any]:
    """Start here. Returns Prometheus alerts (firing/pending), the current key production signals, and
    the open incident (an incident is opened automatically while alerts are firing)."""
    return await _call("get_incident_context", ops.incident_context, {})


@mcp.tool(annotations=READ)
async def get_service_health(service: ServiceName) -> dict[str, Any]:
    """Container state (status, restarts, uptime, image), liveness (/health) and readiness (/ready) for a
    service. For payment-service returns every deployed version and marks which one receives traffic."""
    return await _call("get_service_health", lambda: ops.service_health(service), {"service": service})


@mcp.tool(annotations=READ)
async def get_service_logs(
    service: ServiceName,
    since_minutes: Annotated[int, Field(ge=1, le=60, description="Look-back window in minutes")] = 10,
    level: Annotated[
        Literal["DEBUG", "INFO", "WARN", "ERROR"] | None, Field(description="Minimum severity to return")
    ] = "WARN",
    query: Annotated[
        str | None, Field(max_length=120, description="Space-separated terms that must all appear")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max records returned (most recent)")] = 30,
) -> dict[str, Any]:
    """Structured JSON logs from the service's containers, filtered and bounded. Includes per-event
    counts with first/last seen timestamps across ALL matches, plus the most recent `limit` records."""
    args = {"service": service, "since_minutes": since_minutes, "level": level, "query": query, "limit": limit}
    return await _call("get_service_logs", lambda: ops.service_logs(service, since_minutes, level, query, limit), args)


@mcp.tool(annotations=READ)
async def query_metrics(
    signal: Annotated[SignalName, Field(description="Named metric signal (see tool description)")],
    window_minutes: Annotated[int, Field(ge=1, le=60, description="How far back to look")] = 10,
    rate_window: Annotated[str, Field(pattern=r"^[0-9]{1,3}[sm]$", description="PromQL rate window")] = "30s",
) -> dict[str, Any]:
    """Query Prometheus for a named production signal. Returns each series with current/first/last/min/
    max/avg and up to 30 timestamped points. Signals:"""
    args = {"signal": signal, "window_minutes": window_minutes, "rate_window": rate_window}
    return await _call("query_metrics", lambda: ops.query_metrics(signal, window_minutes, rate_window), args)


query_metrics.__doc__ = (query_metrics.__doc__ or "") + "; ".join(
    f"{k} ({v['unit']}): {v['description']}" for k, v in SIGNALS.items()
)


@mcp.tool(annotations=READ)
async def get_database_health() -> dict[str, Any]:
    """PostgreSQL connection health from pg_stat_activity: max_connections, total/available connections,
    utilization, and connections broken down by client application and state (e.g. idle in transaction)."""
    return await _call("get_database_health", ops.db_health, {})


@mcp.tool(annotations=READ)
async def get_recent_deployments(
    service: Annotated[str | None, Field(description="Filter to one service, e.g. payment-service")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Deployment history (newest first): version, previous version, type (deploy/rollback), timestamp,
    actor, commit and image id. Objective records only."""
    return await _call(
        "get_recent_deployments", lambda: ops.recent_deployments(service, limit), {"service": service, "limit": limit}
    )


@mcp.tool(annotations=READ)
async def get_active_deployment(
    service: Annotated[str, Field(description="Versioned service")] = "payment-service",
) -> dict[str, Any]:
    """Which version of a versioned service currently receives traffic, as recorded in deployment state
    AND as observed live at the api-gateway."""
    return await _call("get_active_deployment", lambda: ops.active_deployment(service), {"service": service})


@mcp.tool(annotations=READ)
async def collect_incident_evidence(
    window_minutes: Annotated[int, Field(ge=2, le=60, description="Evidence window ending now")] = 15,
) -> dict[str, Any]:
    """Machine-readable evidence bundle for diagnostic analysis in the sandbox: aligned time series
    (unix ts, value) for checkout error rate / latency / traffic by version / errors by reason / pool and
    PostgreSQL connections, log event counts per 10s bucket per instance, deployments, container lifecycle,
    alerts, and prior restart actions. Large: analyse it with code (Code Mode) rather than reading it.
    Shape: metrics[<signal>] = [{labels, points: [[unix_ts, value|null], ...]}]; deployments = [{version,
    previous_version, type, deployed_at}]; log_event_totals = [{instance, level, event, count, first_seen,
    last_seen}]; log_event_counts[instance][event] = [[bucket_ts, count]]; window = {start_unix, end_unix,
    step_seconds}; container_lifecycle[instance]; alerts; restart_actions."""
    return await _call(
        "collect_incident_evidence", lambda: ops.collect_evidence(window_minutes), {"window_minutes": window_minutes}
    )


@mcp.tool(annotations=READ)
async def assess_action_risk(
    action: Annotated[Literal["restart_service", "rollback_deployment"], Field(description="Proposed action")],
    service: Annotated[str, Field(description="restart: instance name (payment-service-v2); rollback: service")],
    target_version: Annotated[str | None, Field(description="rollback only: version to roll back to")] = None,
) -> dict[str, Any]:
    """Deterministic blast-radius and risk assessment computed from live data and policy: risk level,
    policy class, whether approval is required, live request rate, dependents, expected disruption,
    target readiness, reversibility, and any blockers. Call before every mutating action."""
    args = {"action": action, "service": service, "target_version": target_version}
    return await _call("assess_action_risk", lambda: ops.assess_risk(action, service, target_version), args)


@mcp.tool(annotations=READ)
async def get_incident_timeline() -> dict[str, Any]:
    """The open incident's recorded timeline: tool calls, evidence, risk assessments, actions and
    verifications with timestamps."""
    return await _call("get_incident_timeline", ops.timeline, {})


@mcp.tool(annotations=READ)
async def get_reference_analyzer() -> dict[str, Any]:
    """Source of the reference incident analyzer (skills/incident-diagnostics/scripts/diagnose.py). Fetch it from
    INSIDE the sandbox so the code never passes through the conversation:
    mcp-client call-tool forgesre get_reference_analyzer '{}' | python3 -c "import json,sys;
    open('diagnose.py','w').write(json.load(sys.stdin)['source'])" && python diagnose.py --window 15"""

    def read() -> dict[str, Any]:
        path = settings.root / "skills" / "incident-diagnostics" / "scripts" / "diagnose.py"
        source = path.read_text()
        return ok(
            filename="diagnose.py",
            sha256=hashlib.sha256(source.encode()).hexdigest(),
            lines=source.count("\n"),
            source=source,
        )

    return await _call("get_reference_analyzer", read, {})


# --------------------------------------------------------------------------- probes / verification


@mcp.tool(annotations=PROBE)
async def run_synthetic_check(
    requests: Annotated[int, Field(ge=1, le=50, description="Number of synthetic checkout requests")] = 20,
) -> dict[str, Any]:
    """Send marked synthetic checkout requests through the public api-gateway and report success ratio,
    latency percentiles, status codes, errors, and which version served them."""
    return await _acall("run_synthetic_check", lambda: ops.synthetic_check(requests), {"requests": requests})


@mcp.tool(annotations=PROBE)
async def verify_recovery(
    settle_seconds: Annotated[
        int | None,
        Field(
            ge=0,
            le=90,
            description="Seconds after the last action to wait before measuring; never shorter than the metric window",
        ),
    ] = None,
) -> dict[str, Any]:
    """Objective recovery check against config/verification.yaml thresholds: active version readiness,
    synthetic checkout success, checkout error rate and p95 latency, PostgreSQL connection utilization.
    Waits until `settle_seconds` have passed since the last action so only post-action traffic is measured.
    Returns verdict RECOVERED or NOT_RECOVERED with every criterion's observed value."""
    return await _acall("verify_recovery", lambda: ops.verify(settle_seconds), {"settle_seconds": settle_seconds})


# --------------------------------------------------------------------------- YELLOW: safe, reversible


@mcp.tool(annotations=YELLOW)
async def restart_service(
    service: Annotated[str, Field(description="Instance to restart, e.g. payment-service-v2")],
    reason: Annotated[str, Field(min_length=10, max_length=4000, description="Why, citing evidence (stored trimmed)")],
) -> dict[str, Any]:
    """YELLOW action: restart one allow-listed container and wait for it to become live. Policy limits
    restarts per target per window; stateful services (postgres) can never be restarted. Completion only
    means the container is back — call verify_recovery to learn whether the incident is resolved."""
    return await _call("restart_service", lambda: ops.restart(service, reason), {"service": service, "reason": reason})


# --------------------------------------------------------------------------- RED: approval-gated


@mcp.tool(annotations=RED)
async def rollback_deployment(
    service: Annotated[str, Field(description="Versioned service to roll back, e.g. payment-service")],
    from_version: Annotated[str, Field(pattern=r"^v[0-9]{1,3}$", description="Currently active version")],
    to_version: Annotated[str, Field(pattern=r"^v[0-9]{1,3}$", description="Known-good version to restore")],
    reason: Annotated[
        str, Field(min_length=20, max_length=4000, description="Evidence-backed justification (stored trimmed)")
    ],
    approval_brief: Annotated[
        str,
        Field(
            min_length=80,
            max_length=8000,
            description=(
                "Completed human-facing rollback brief: hypothesis, numeric evidence, failed safe action, "
                "blast radius, target readiness and recovery plan. This is displayed in TrueForge before approval."
            ),
        ),
    ],
) -> dict[str, Any]:
    """RED action — production rollback, requires human approval in TrueForge. Validates the request
    (from_version must be active, target must exist and become ready, an incident must be open), starts
    the target if needed, atomically switches gateway traffic, confirms the gateway observes the switch,
    then stops the previous version to release its resources. Returns ALREADY_AT_TARGET if nothing to do."""
    args = {
        "service": service,
        "from_version": from_version,
        "to_version": to_version,
        "reason": reason,
        "approval_brief": approval_brief,
    }
    return await _call(
        "rollback_deployment",
        lambda: ops.rollback(service, from_version, to_version, reason, approval_brief),
        args,
    )


# --------------------------------------------------------------------------- report


@mcp.tool(annotations=PROBE)
async def generate_incident_report(
    summary: Annotated[str, Field(min_length=20, max_length=2000)],
    root_cause: Annotated[str, Field(min_length=20, max_length=2000, description="Hypothesis + mechanism")],
    evidence: Annotated[list[str], Field(min_length=1, max_length=12, description="Evidence items, one per line")],
    confidence: Literal["LOW", "MEDIUM", "HIGH"],
    sandbox_analysis: Annotated[
        str | dict[str, Any] | list[Any] | None,
        Field(description="Sandbox diagnostic output (text or the analyzer's JSON object)"),
    ] = None,
    human_decisions: Annotated[list[str], Field(max_length=10)] = [],  # noqa: B006
    follow_ups: Annotated[list[str], Field(max_length=10)] = [],  # noqa: B006
) -> dict[str, Any]:
    """Write artifacts/incidents/<incident-id>.md. Timeline, actions, before/after signals and
    verification results are rendered from recorded tool results; your fields are the narrative. Final
    status is RESOLVED only if the latest verify_recovery verdict was RECOVERED."""

    def run() -> dict[str, Any]:
        return ok(
            **build_report(
                settings,
                ops.audit,
                summary=summary,
                root_cause=root_cause,
                evidence=evidence,
                confidence=confidence,
                sandbox_analysis=_as_text(sandbox_analysis),
                human_decisions=human_decisions,
                follow_ups=follow_ups,
            )
        )

    return await _call("generate_incident_report", run, {"confidence": confidence})


# --------------------------------------------------------------------------- plain HTTP routes


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "forgesre-mcp", "environment": settings.environment})


class BearerAuth:
    """ASGI guard: /mcp requires `Authorization: Bearer $FORGESRE_MCP_TOKEN` when a token is configured."""

    def __init__(self, app, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if self.token and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(b"authorization", b"").decode()
            if not hmac.compare_digest(supplied, f"Bearer {self.token}"):
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def transport_security_settings() -> TransportSecuritySettings:
    """Allow loopback plus only the configured public tunnel hostname."""
    allowed_hosts = ["127.0.0.1:*", "localhost:*"]
    mcp_url = os.environ.get("FORGESRE_MCP_URL", "").strip()
    if mcp_url:
        hostname = urlsplit(mcp_url).hostname
        if not hostname:
            raise ValueError("FORGESRE_MCP_URL must contain a hostname")
        allowed_hosts.extend([hostname, f"{hostname}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )


def build_app():
    from .dashboard import register

    register(mcp, ops, settings)
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="127.0.0.1",
        transport_security=transport_security_settings(),
    )
    return BearerAuth(app, os.environ.get("FORGESRE_MCP_TOKEN") or None)


def main() -> None:
    uvicorn.run(build_app(), host="127.0.0.1", port=settings.mcp_port, log_level="warning")


if __name__ == "__main__":
    main()
