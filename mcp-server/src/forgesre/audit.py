"""Incident state + append-only structured audit events (JSONL)."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class AuditLog:
    def __init__(self, settings: Settings) -> None:
        self.events_path = settings.artifacts_dir / "audit" / "events.jsonl"
        self.incident_path = settings.state_dir / "incident.json"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.incident_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- incident -------------------------------------------------------
    def incident(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.incident_path.read_text())
        except (FileNotFoundError, ValueError):
            return None
        return data or None

    def open_incident_id(self) -> str | None:
        inc = self.incident()
        return inc["incident_id"] if inc and inc.get("status") == "open" else None

    def _save_incident(self, data: dict[str, Any] | None) -> None:
        tmp = self.incident_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data or {}, indent=2))
        os.replace(tmp, self.incident_path)

    def open_incident(self, title: str, alerts: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any]:
        with _lock:
            current = self.incident()
            if current and current.get("status") == "open":
                return current
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            inc = {
                "incident_id": f"INC-{stamp}",
                "title": title,
                "status": "open",
                "opened_at": now_iso(),
                "alerts_at_open": alerts,
                "snapshot_at_open": snapshot,
            }
            self._save_incident(inc)
        self.record("incident_started", "get_incident_context", "open", {"title": title, "alerts": alerts})
        return inc

    def update_incident(self, **fields: Any) -> dict[str, Any] | None:
        with _lock:
            inc = self.incident()
            if not inc:
                return None
            inc.update(fields)
            self._save_incident(inc)
            return inc

    def clear(self) -> None:
        """Archive the event log and forget the current incident (used by reset)."""
        with _lock:
            if self.events_path.exists() and self.events_path.stat().st_size > 0:
                archive = self.events_path.with_name(f"events-{datetime.now(UTC):%Y%m%d-%H%M%S}.jsonl")
                self.events_path.rename(archive)
            self._save_incident(None)

    # ---- events ---------------------------------------------------------
    def record(self, type_: str, source: str, status: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "event_id": f"evt-{uuid.uuid4().hex[:10]}",
            "incident_id": self.open_incident_id(),
            "timestamp": now_iso(),
            "type": type_,
            "source": source,
            "status": status,
            "data": data or {},
        }
        line = json.dumps(event, default=str)
        with _lock, open(self.events_path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(line + "\n")
            fcntl.flock(fh, fcntl.LOCK_UN)
        return event

    def events(self, incident_id: str | None = None, types: set[str] | None = None) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        out = []
        for line in Path(self.events_path).read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if incident_id and e.get("incident_id") != incident_id:
                continue
            if types and e.get("type") not in types:
                continue
            out.append(e)
        return out
