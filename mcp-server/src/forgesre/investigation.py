"""Read-only incident investigation through the saved TrueForge agent."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from trueforge_sdk import TrueForge, TurnStateDone, TurnStateError

from .trueforge import READ_ONLY_AGENT_NAME
from .trueforge import TrueForge as AdminClient

READ_ONLY_PROMPT = (
    "Investigate the reported production incident using only the read-only ForgeSRE tools available to you. "
    "Gather relevant alerts, health, metrics, logs, database status, deployment history, and incident evidence. "
    "Summarize observed facts, likely cause, impact, and confidence, and cite the evidence behind each conclusion. "
    "Do not run probes, use a sandbox, restart, deploy, roll back, modify, or otherwise change infrastructure. "
    "If an approval-required event occurs, leave it pending for a human and stop."
)


class InvestigationUnavailable(RuntimeError):
    """TrueForge server or the saved read-only agent is unavailable."""


@dataclass
class InvestigationResult:
    session_id: str
    events: list[Any] = field(default_factory=list)
    approval_events: list[Any] = field(default_factory=list)
    status: str = "unknown"
    output: str = ""


def investigate(
    request: str,
    *,
    base_url: str | None = None,
    client_factory: Callable[..., Any] = TrueForge,
    admin_factory: Callable[..., Any] = AdminClient,
    on_event: Callable[[Any], None] | None = None,
) -> InvestigationResult:
    """Create a session, submit one read-only request, and stream its events.

    This function intentionally has no resume or approval submission path.
    """
    incident_request = request.strip()
    if not incident_request:
        raise ValueError("incident request must not be empty")
    resolved_url = base_url or os.environ.get("TRUEFORGE_BASE_URL") or os.environ.get(
        "TRUEFORGE_URL", "http://localhost:8790"
    )

    try:
        admin = admin_factory(resolved_url)
        AdminClient.validate_read_only_agent(admin.get_agent(READ_ONLY_AGENT_NAME))
        client = client_factory(base_url=resolved_url, timeout=900)
        session = client.sessions.create(agent={"name": READ_ONLY_AGENT_NAME}).data
        result = InvestigationResult(session_id=session.id)
        stream = client.sessions.create_turn_stream(
            session_id=session.id,
            input=[{"type": "user.message", "content": f"{READ_ONLY_PROMPT}\n\nIncident:\n{incident_request}"}],
        )
        for event in stream:
            result.events.append(event)
            if event.type == "tool.approval_required":
                result.approval_events.append(event)
            if on_event:
                on_event(event)
            if event.type == "turn.done":
                state = event.state
                result.status = state.status
                if isinstance(state, TurnStateError):
                    raise RuntimeError(f"TrueForge turn failed (status=error): {state.message}")
                if isinstance(state, TurnStateDone):
                    result.output = (state.output.content if state.output else "") or ""
        return result
    except InvestigationUnavailable:
        raise
    except Exception as exc:
        detail = str(exc)
        if "saved agent" in detail and "unavailable" in detail:
            detail = f"Saved agent '{READ_ONLY_AGENT_NAME}' is unavailable or not configured for read-only tools."
        raise InvestigationUnavailable(
            f"Could not run read-only agent '{READ_ONLY_AGENT_NAME}' at {resolved_url}: {detail}"
        ) from exc
