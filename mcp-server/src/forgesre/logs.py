"""Parse, filter and bound container logs (JSON lines with Docker timestamps)."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}
_SECRET = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*|\"\s*:\s*\")([^\s\",]+)")
_DSN = re.compile(r"(postgres(?:ql)?://[^:/\s]+:)([^@\s]+)(@)")


def redact(text: str) -> str:
    text = _DSN.sub(r"\1***\3", text)
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def parse_line(raw: str, instance: str) -> dict[str, Any]:
    ts, _, rest = raw.partition(" ")
    rest = redact(rest.strip())
    try:
        rec = json.loads(rest)
        if not isinstance(rec, dict):
            raise ValueError
    except ValueError:
        rec = {"level": "INFO", "event": "unstructured", "message": rest}
    rec.setdefault("timestamp", ts)
    rec["instance"] = instance
    rec["level"] = str(rec.get("level", "INFO")).upper()
    return rec


def matches(rec: dict[str, Any], min_level: str | None, query: str | None) -> bool:
    if min_level and LEVELS.get(rec["level"], 20) < LEVELS.get(min_level.upper(), 20):
        return False
    if query:
        blob = json.dumps(rec, default=str).lower()
        return all(term in blob for term in query.lower().split())
    return True


def bound_record(rec: dict[str, Any], max_chars: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if isinstance(v, str) and len(v) > max_chars:
            v = v[:max_chars] + "…"
        out[k] = v
    return out


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_event = Counter((r["instance"], r["level"], r.get("event", "?")) for r in records)
    first_seen: dict[tuple[str, str], str] = {}
    last_seen: dict[tuple[str, str], str] = {}
    for r in records:
        key = (r["instance"], r.get("event", "?"))
        first_seen.setdefault(key, r["timestamp"])
        last_seen[key] = r["timestamp"]
    return {
        "event_counts": [
            {
                "instance": inst,
                "level": lvl,
                "event": ev,
                "count": n,
                "first_seen": first_seen[(inst, ev)],
                "last_seen": last_seen[(inst, ev)],
            }
            for (inst, lvl, ev), n in by_event.most_common(20)
        ]
    }


def bucket_counts(records: list[dict[str, Any]], bucket_s: int) -> dict[str, dict[str, list[list[int]]]]:
    """{instance: {event: [[bucket_unix_ts, count], ...]}} for WARN+ and lifecycle events."""
    lifecycle = {"service_started", "service_stopping", "route_switched", "gateway_started"}
    out: dict[str, dict[str, Counter]] = {}
    for r in records:
        if LEVELS.get(r["level"], 20) < 30 and r.get("event") not in lifecycle:
            continue
        try:
            ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        b = int(ts // bucket_s * bucket_s)
        out.setdefault(r["instance"], {}).setdefault(r.get("event", "?"), Counter())[b] += 1
    return {inst: {ev: sorted([t, n] for t, n in c.items()) for ev, c in evs.items()} for inst, evs in out.items()}
