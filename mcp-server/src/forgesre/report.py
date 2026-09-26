"""Incident report: agent narrative + facts rendered from the audit log.

Every number in the factual sections comes from recorded tool results
(snapshots, action results, verifications, deployment history). The agent
supplies only the narrative fields, which are labelled as its analysis.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .audit import AuditLog, now_iso
from .config import Settings
from .deployment import DeploymentStore


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _num(v: Any, unit: str = "") -> str:
    return "n/a" if v is None else f"{v:g}{unit}"


def _signals_table(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    before, after = before or {}, after or {}

    def pool(snap: dict[str, Any]) -> str:
        v = snap.get("active_version")
        return _pct((snap.get("db_pool_utilization_by_version") or {}).get(v or ""))

    rows = [
        ("Active payment-service version", before.get("active_version") or "n/a", after.get("active_version") or "n/a"),
        ("Checkout error rate", _pct(before.get("checkout_error_rate")), _pct(after.get("checkout_error_rate"))),
        (
            "Checkout p95 latency",
            _num(before.get("checkout_latency_p95_seconds"), "s"),
            _num(after.get("checkout_latency_p95_seconds"), "s"),
        ),
        (
            "PostgreSQL connections",
            _num(before.get("db_connections_total")),
            _num(after.get("db_connections_total")),
        ),
        (
            "PostgreSQL connection utilization",
            _pct(before.get("db_connection_utilization")),
            _pct(after.get("db_connection_utilization")),
        ),
        ("Active version pool utilization", pool(before), pool(after)),
        (
            "Measured at",
            (before.get("at") or "n/a")[:19],
            (after.get("at") or "n/a")[:19],
        ),
    ]
    out = ["| Signal | Before | After |", "|---|---|---|"]
    out += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return out


def build_report(
    settings: Settings,
    audit: AuditLog,
    *,
    summary: str,
    root_cause: str,
    evidence: list[str],
    confidence: str,
    sandbox_analysis: str | None,
    human_decisions: list[str],
    follow_ups: list[str],
) -> dict[str, Any]:
    inc = audit.incident() or {}
    incident_id = inc.get("incident_id") or f"INC-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    events = audit.events(inc.get("incident_id")) if inc.get("incident_id") else []
    actions_started = {e["data"].get("action_id"): e for e in events if e["type"] == "action_started"}
    actions_done = [e for e in events if e["type"] == "action_completed"]
    verifications = [e for e in events if e["type"] == "verification_completed"]
    last_verification = verifications[-1] if verifications else None
    resolved = bool(last_verification and last_verification["status"] == "RECOVERED")
    final_status = "RESOLVED" if resolved else "UNRESOLVED"

    deployments = DeploymentStore(settings).history("payment-service", 10)[::-1]
    before = inc.get("snapshot_at_open")
    after = (last_verification or {}).get("data", {}).get("signals")

    lines: list[str] = [
        f"# Incident Report — {incident_id}",
        "",
        f"**Status:** {final_status}  ",
        f"**Environment:** {settings.environment}  ",
        f"**Opened:** {inc.get('opened_at', 'n/a')}  ",
        f"**Report generated:** {now_iso()}  ",
        "**Handled by:** ForgeSRE agent running on TrueForge",
        "",
        "## Summary",
        "",
        summary.strip(),
        "",
        "## Detection",
        "",
    ]
    for a in inc.get("alerts_at_open", []):
        lines.append(
            f"- `{a.get('alert')}` ({a.get('severity')}) firing since {a.get('active_since')} — "
            f"{a.get('summary')} (value at detection: {a.get('value')})"
        )
    if not inc.get("alerts_at_open"):
        lines.append("- No alert was recorded at incident open.")
    lines += ["", "## Impact", ""]
    if before:
        rpm = (before.get("checkout_requests_per_second") or 0) * 60
        lines += [
            f"- Checkout error rate at detection: **{_pct(before.get('checkout_error_rate'))}** "
            f"of ~{rpm:.0f} requests/minute",
            f"- Checkout p95 latency at detection: **{_num(before.get('checkout_latency_p95_seconds'), 's')}**",
            f"- PostgreSQL connection utilization at detection: **{_pct(before.get('db_connection_utilization'))}**",
        ]
    lines += ["", "## Timeline (UTC)", ""]
    for d in deployments:
        lines.append(
            f"- {d['deployed_at'][11:19]} — deployment `{d['deployment_id']}`: payment-service "
            f"{d.get('previous_version') or '∅'} → **{d['version']}** ({d['type']}, by {d['deployed_by']})"
        )
    interesting = {
        "incident_started",
        "evidence_collected",
        "risk_assessed",
        "action_started",
        "action_completed",
        "verification_completed",
    }
    for e in events:
        if e["type"] not in interesting:
            continue
        d = e["data"]
        what = {
            "incident_started": f"incident opened: {d.get('title')}",
            "evidence_collected": f"evidence bundle collected ({d.get('window_minutes')} min window)",
            "risk_assessed": f"risk assessed for {d.get('action')} → {d.get('risk_level')}",
            "action_started": f"{e['source']} started ({d.get('target') or d.get('to_version') or ''})",
            "action_completed": f"{e['source']} {e['status']} in {d.get('duration_seconds')}s",
            "verification_completed": f"verification → **{e['status']}** "
            f"(failed: {', '.join(d.get('failed_criteria') or []) or 'none'})",
        }[e["type"]]
        lines.append(f"- {e['timestamp'][11:19]} — {what}")
    lines += ["", "## Evidence", ""]
    lines += [f"{i}. {item.strip()}" for i, item in enumerate(evidence, 1)] or ["(none supplied)"]
    if sandbox_analysis:
        lines += ["", "### Sandbox diagnostic (TrueForge sandbox)", "", sandbox_analysis.strip()]
    lines += [
        "",
        "## Root Cause",
        "",
        f"*Agent hypothesis — confidence: **{confidence}***",
        "",
        root_cause.strip(),
        "",
        "## Actions Taken",
        "",
    ]
    for i, done in enumerate(actions_done, 1):
        d = done["data"]
        start = actions_started.get(d.get("action_id"), {})
        sd = start.get("data", {})
        target = d.get("target") or f"{sd.get('service')} {sd.get('from_version')} → {sd.get('to_version')}"
        lines.append(f"{i}. **{done['source']}** — {target}")
        if sd.get("reason"):
            lines.append(f"   - Reason: {sd['reason']}")
        if done["source"] == "rollback_deployment":
            lines.append(
                "   - Approval: required and enforced by TrueForge (tool dispatched only after human approval)"
            )
        lines.append(
            f"   - Execution: {done['status']} in {d.get('duration_seconds')}s (action `{d.get('action_id')}`)"
        )
        following = next(
            (v for v in verifications if v["timestamp"] > done["timestamp"]),
            None,
        )
        if following:
            lines.append(
                f"   - Verified outcome: **{following['status']}** "
                f"(failed criteria: {', '.join(following['data'].get('failed_criteria') or []) or 'none'})"
            )
        else:
            lines.append("   - Verified outcome: not verified")
    if not actions_done:
        lines.append("No mutating action was executed.")
    lines += ["", "## Verification", ""]
    lines += _signals_table(before, after)
    if last_verification:
        lines += ["", "Final verification criteria:", ""]
        for c in last_verification["data"].get("criteria", []):
            mark = "✅" if c["passed"] else "❌"
            lines.append(
                f"- {mark} `{c['criterion']}` observed {c['observed']} (needs {c['operator']} {c['threshold']})"
            )
        s = last_verification["data"].get("synthetic", {})
        lines.append(f"- Synthetic checkout probes: {s.get('succeeded')}/{s.get('requests')} succeeded")
    lines += ["", "## Human Decisions", ""]
    lines += [f"- {h}" for h in human_decisions] or ["- None recorded"]
    lines += ["", "## Follow-ups", ""]
    lines += [f"- {f}" for f in follow_ups] or ["- None"]
    lines += ["", "## Final Status", "", f"**{final_status}**", ""]

    out_dir = settings.artifacts_dir / "incidents"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{incident_id}.md"
    md_path.write_text("\n".join(lines))
    (out_dir / f"{incident_id}.json").write_text(
        json.dumps({"incident": inc, "events": events, "final_status": final_status}, indent=2, default=str)
    )
    audit.record("report_generated", "generate_incident_report", final_status, {"path": str(md_path)})
    if inc:
        audit.update_incident(status="resolved" if resolved else "report_filed_unresolved", closed_at=now_iso())
    return {
        "incident_id": incident_id,
        "final_status": final_status,
        "report_path": str(md_path.relative_to(settings.root))
        if md_path.is_relative_to(settings.root)
        else str(md_path),
        "markdown": "\n".join(lines),
    }
