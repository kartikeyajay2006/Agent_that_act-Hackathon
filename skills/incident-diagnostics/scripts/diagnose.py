#!/usr/bin/env python3
"""Evidence-driven incident diagnosis. Runs inside the TrueForge sandbox.

Usage (inside the sandbox):
    python diagnose.py                     # fetch evidence via the harness-bridged mcp_client
    python diagnose.py evidence.json       # analyse a saved collect_incident_evidence result
    python diagnose.py --window 20         # evidence window in minutes (default 15)

Every number in the output is computed from the evidence bundle returned by the
forgesre MCP tool `collect_incident_evidence`. Nothing about the answer is
assumed: the suspect version, timings, and scores all come from the data.
The sandbox holds no credentials; mcp_client calls are bridged to the harness.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import datetime
from typing import Any

ERROR_THRESHOLD = 0.05  # same threshold as the CheckoutErrorRateHigh alert


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _points(series: list[dict[str, Any]], **match: str) -> list[tuple[float, float]]:
    for s in series or []:
        labels = s.get("labels") or {}
        if all(labels.get(k) == v for k, v in match.items()):
            return [(float(t), float(v)) for t, v in s.get("points", []) if v is not None]
    return []


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) < 5:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return round(cov / (va * vb), 3) if va and vb else None


def incident_start(err: list[tuple[float, float]]) -> float | None:
    """First sample above threshold that follows a below-threshold sample (or the first sample)."""
    prev_ok = True
    for t, v in err:
        if v > ERROR_THRESHOLD and prev_ok:
            return t
        prev_ok = v <= ERROR_THRESHOLD
    return None


def diagnose(ev: dict[str, Any]) -> dict[str, Any]:
    m = ev.get("metrics", {})
    err = _points(m.get("checkout_error_rate"))
    p95 = _points(m.get("checkout_latency_p95"))
    start = incident_start(err)
    out: dict[str, Any] = {"window": ev.get("window"), "incident_detected": start is not None}
    if start is None:
        out["note"] = f"checkout_error_rate never crossed {ERROR_THRESHOLD} in the window"
        return out

    before = [v for t, v in err if t < start]
    after = [v for t, v in err if t >= start]
    out["incident_start_unix"] = int(start)
    out["error_rate_before"] = _mean(before)
    out["error_rate_peak_after"] = round(max(after), 4) if after else None
    out["error_rate_latest"] = round(err[-1][1], 4) if err else None
    out["latency_p95_before_s"] = _mean([v for t, v in p95 if t < start])
    out["latency_p95_peak_after_s"] = round(max([v for t, v in p95 if t >= start] or [0]), 4)

    # Which upstream version carried the failing requests after the start?
    fail_by_version: dict[str, float] = {}
    for s in m.get("checkout_errors_by_reason") or []:
        ver = (s.get("labels") or {}).get("upstream_version", "?")
        fail_by_version[ver] = fail_by_version.get(ver, 0.0) + sum(
            v for t, v in s.get("points", []) if v is not None and t >= start
        )
    total_fail = sum(fail_by_version.values())
    shares = {k: round(v / total_fail, 3) for k, v in fail_by_version.items()} if total_fail else {}
    suspect = max(shares, key=shares.get) if shares else None
    out["failed_request_share_by_version"] = shares
    out["suspect_version"] = suspect

    # Deployment timing relative to the incident start.
    deploys = sorted(ev.get("deployments") or [], key=lambda d: d.get("deployed_at", ""))
    prior = [d for d in deploys if _ts(d["deployed_at"]) <= start + 5]
    last = prior[-1] if prior else None
    if last:
        out["deployment_before_incident"] = {
            "deployment_id": last.get("deployment_id"),
            "service": last.get("service"),
            "version": last.get("version"),
            "previous_version": last.get("previous_version"),
            "seconds_before_incident_start": round(start - _ts(last["deployed_at"]), 1),
        }

    # Resource saturation per version and at the database.
    pool = {
        (s.get("labels") or {}).get("version"): [(float(t), float(v)) for t, v in s["points"] if v is not None]
        for s in m.get("db_pool_utilization") or []
    }
    out["pool_utilization"] = {
        ver: {
            "before": _mean([v for t, v in pts if t < start]),
            "max_after": round(max([v for t, v in pts if t >= start] or [0]), 3),
        }
        for ver, pts in pool.items()
        if ver
    }
    dbu = _points(m.get("db_connection_utilization"))
    out["db_connection_utilization"] = {
        "before": _mean([v for t, v in dbu if t < start]),
        "max_after": round(max([v for t, v in dbu if t >= start] or [0]), 3),
    }

    # Dominant ERROR event per instance from logs.
    errors = [e for e in ev.get("log_event_totals") or [] if e.get("level") == "ERROR"]
    by_instance: dict[str, dict[str, Any]] = {}
    for e in errors:
        cur = by_instance.get(e["instance"])
        if cur is None or e["count"] > cur["count"]:
            by_instance[e["instance"]] = {"event": e["event"], "count": e["count"], "first_seen": e["first_seen"]}
    out["dominant_error_by_instance"] = by_instance
    top = max(errors, key=lambda e: e["count"]) if errors else None
    out["dominant_error"] = top and {"instance": top["instance"], "event": top["event"], "count": top["count"]}
    # The edge reports symptoms (every failed checkout); the cause shows up in a versioned backend instance.
    backend = [e for e in errors if e["instance"].rsplit("-", 1)[-1][:1] == "v" and e["instance"][-1].isdigit()]
    top_backend = max(backend, key=lambda e: e["count"]) if backend else None
    out["dominant_backend_error"] = top_backend and {
        "instance": top_backend["instance"],
        "event": top_backend["event"],
        "count": top_backend["count"],
    }

    # Does the suspect's pool saturation move with the user-facing error rate?
    corr = None
    if suspect and suspect in pool:
        pmap = dict(pool[suspect])
        pairs = [(pmap[t], v) for t, v in err if t in pmap]
        corr = _pearson([a for a, _ in pairs], [b for _, b in pairs])
    out["pool_vs_error_correlation"] = corr

    # Effect of any restart the agent already performed.
    effects = []
    window_start = (ev.get("window") or {}).get("start_unix", 0)
    for r in ev.get("restart_actions") or []:
        rt = _ts(r["completed_at"])
        if rt < window_start:
            continue
        pre = [v for t, v in err if rt - 60 <= t < rt]
        post = [v for t, v in err if rt + 30 <= t <= rt + 90]
        effects.append(
            {
                "target": r.get("target"),
                "error_rate_before": _mean(pre),
                "error_rate_30_90s_after": _mean(post),
                "resolved": bool(post) and max(post) <= ERROR_THRESHOLD,
            }
        )
    out["restart_effects"] = effects

    # Independent checks; the score is simply the fraction that agree.
    s_inst = [i for i in by_instance if suspect and i.endswith(f"-{suspect}")]
    checks = {
        "error_rate_step_change": bool(after)
        and max(after) > ERROR_THRESHOLD
        and max(after) > 5 * (out["error_rate_before"] or 0.001),
        "failures_concentrated_on_one_version": bool(shares) and max(shares.values()) >= 0.9,
        "suspect_deployed_shortly_before_start": bool(last)
        and last.get("version") == suspect
        and out["deployment_before_incident"]["seconds_before_incident_start"] <= 600,
        "suspect_pool_saturated": bool(suspect)
        and (out["pool_utilization"].get(suspect) or {}).get("max_after", 0) >= 0.9,
        "logs_errors_from_suspect_instance": bool(top_backend) and top_backend["instance"] in s_inst,
        "pool_tracks_error_rate": corr is not None and corr >= 0.6,
    }
    out["checks"] = checks
    out["evidence_score"] = round(sum(checks.values()) / len(checks), 2)
    if suspect and last:
        out["affected_service"] = last.get("service")
    return out


async def _fetch(window: int) -> dict[str, Any]:
    from mcp_client import call_tool  # provided by TrueForge Code Mode; bridged to the harness

    return await call_tool("forgesre", "collect_incident_evidence", body={"window_minutes": window})


def main() -> None:
    args = sys.argv[1:]
    window = 15
    if "--window" in args:
        window = int(args[args.index("--window") + 1])
        args = [a for a in args if a not in ("--window", str(window))]
    if args:
        with open(args[0]) as fh:
            evidence = json.load(fh)
    else:
        evidence = asyncio.run(_fetch(window))
    if isinstance(evidence, dict) and "structuredContent" in evidence:
        evidence = evidence["structuredContent"]
    print(json.dumps(diagnose(evidence), indent=2))


if __name__ == "__main__":
    main()
