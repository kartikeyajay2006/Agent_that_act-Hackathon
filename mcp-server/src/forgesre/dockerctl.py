"""Narrow Docker Engine operations on catalogued containers only.

There is intentionally no generic exec / run / command surface here.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from typing import Any

import docker
from docker.errors import APIError, NotFound

from .catalog import Instance
from .config import Settings
from .results import ToolError


def _parse_ts(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    # Docker uses RFC3339 with nanoseconds; trim to microseconds for fromisoformat.
    head, _, frac = value.rstrip("Z").partition(".")
    frac = (frac[:6] if frac else "0").ljust(6, "0")
    return datetime.fromisoformat(f"{head}.{frac}+00:00")


class DockerControl:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env(timeout=20)
            except docker.errors.DockerException as exc:
                raise ToolError("DOCKER_UNAVAILABLE", f"cannot reach Docker Engine: {exc}") from exc
        return self._client

    def _container(self, inst: Instance):
        try:
            return self.client.containers.get(inst.container)
        except NotFound:
            return None
        except APIError as exc:
            raise ToolError("DOCKER_ERROR", str(exc)) from exc

    def state(self, inst: Instance) -> dict[str, Any]:
        c = self._container(inst)
        if c is None:
            return {"exists": False, "status": "not_deployed"}
        st = c.attrs.get("State", {})
        started = _parse_ts(st.get("StartedAt"))
        health = (st.get("Health") or {}).get("Status")
        return {
            "exists": True,
            "status": st.get("Status"),
            "running": bool(st.get("Running")),
            "health": health,
            "restart_count": c.attrs.get("RestartCount", 0),
            "started_at": started.isoformat() if started else None,
            "uptime_seconds": round((datetime.now(UTC) - started).total_seconds(), 1) if started else None,
            "exit_code": st.get("ExitCode"),
            "oom_killed": st.get("OOMKilled", False),
            "image": c.attrs.get("Config", {}).get("Image"),
            "image_id": (c.attrs.get("Image") or "")[:19],
        }

    def logs(self, inst: Instance, since: datetime, until: datetime | None = None, tail: int = 5000) -> list[str]:
        c = self._container(inst)
        if c is None:
            return []
        kwargs: dict[str, Any] = {"since": since, "timestamps": True, "stdout": True, "stderr": True, "tail": tail}
        if until is not None:
            kwargs["until"] = until
        raw = c.logs(**kwargs)
        return raw.decode("utf-8", errors="replace").splitlines()

    def restart(self, inst: Instance, stop_timeout: int = 5) -> None:
        c = self._container(inst)
        if c is None:
            raise ToolError("NOT_DEPLOYED", f"{inst.name} has no container to restart")
        c.restart(timeout=stop_timeout)

    def stop(self, inst: Instance, stop_timeout: int = 5) -> bool:
        c = self._container(inst)
        if c is None:
            return False
        c.stop(timeout=stop_timeout)
        return True

    def remove(self, inst: Instance) -> bool:
        c = self._container(inst)
        if c is None:
            return False
        c.remove(force=True)
        return True

    def ensure_running(self, inst: Instance) -> str:
        """Start an existing container, or create it through docker compose (fixed args, catalogued name)."""
        c = self._container(inst)
        if c is not None and c.status == "running":
            return "already_running"
        if c is not None:
            c.start()
            return "started"
        if not inst.compose_service:
            raise ToolError("NOT_DEPLOYED", f"{inst.name} is not deployed and has no compose definition")
        cmd = ["docker", "compose", "-f", str(self.settings.compose_file)]
        if inst.spec.get("compose_profile"):
            cmd += ["--profile", inst.spec["compose_profile"]]
        cmd += ["up", "-d", "--no-deps", inst.compose_service]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=self.settings.root)
        if proc.returncode != 0:
            raise ToolError("DEPLOY_FAILED", f"compose up failed for {inst.name}", stderr=proc.stderr[-800:])
        return "created"

    def wait_running(self, inst: Instance, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            st = self.state(inst)
            if st.get("running"):
                return True
            time.sleep(0.5)
        return False
