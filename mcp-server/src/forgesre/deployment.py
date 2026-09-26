"""Deployment state: which payment-service version receives traffic, plus history.

The gateway reads state/deployment.json on every request, so writing it here
is the actual traffic switch. Writes are atomic (tmp + rename) and serialized
with an flock so the CLI scripts and the MCP server never interleave.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings

STATE_FILE = "deployment.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class DeploymentStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dir = settings.state_dir
        self.path = self.dir / STATE_FILE

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".deployment.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"environment": self.settings.environment, "services": {}, "history": []}

    def _write(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)

    def active_version(self, service: str) -> str | None:
        return self.read().get("services", {}).get(service, {}).get("active_version")

    def history(self, service: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        items = self.read().get("history", [])
        if service:
            items = [h for h in items if h.get("service") == service]
        return items[-limit:][::-1]

    def record_switch(
        self,
        *,
        service: str,
        to_version: str,
        change_type: str,
        actor: str,
        metadata: dict[str, Any],
        reset: bool = False,
    ) -> dict[str, Any]:
        """Point traffic at to_version and append an objective history record."""
        with self._locked():
            state = {"environment": self.settings.environment, "services": {}, "history": []} if reset else self.read()
            previous = state.get("services", {}).get(service, {}).get("active_version")
            ts = now_iso()
            entry = {
                "deployment_id": f"dep-{uuid.uuid4().hex[:8]}",
                "service": service,
                "version": to_version,
                "previous_version": previous,
                "type": change_type,
                "deployed_at": ts,
                "deployed_by": actor,
                **metadata,
            }
            for h in state.get("history", []):
                if h.get("service") == service and h.get("status") == "active":
                    h["status"] = "superseded" if change_type == "deploy" else "rolled_back"
                    h["ended_at"] = ts
            entry["status"] = "active"
            state.setdefault("history", []).append(entry)
            state.setdefault("services", {})[service] = {
                "active_version": to_version,
                "updated_at": ts,
                "deployment_id": entry["deployment_id"],
            }
            self._write(state)
            return entry


def release_metadata(settings: Settings, source_dir: str | None, image_id: str | None) -> dict[str, Any]:
    """Objective release facts: git commit touching the source dir (if any) and image id."""
    meta: dict[str, Any] = {"image_id": image_id}
    if source_dir:
        try:
            out = subprocess.run(
                ["git", "log", "-1", "--format=%h%x09%s", "--", source_dir],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=settings.root,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out:
            sha, _, subject = out.partition("\t")
            meta["commit"] = sha
            meta["commit_subject"] = subject
        else:
            meta["commit"] = "uncommitted"
        meta["source_dir"] = source_dir
    return meta


def path_for(settings: Settings) -> Path:
    return settings.state_dir / STATE_FILE
