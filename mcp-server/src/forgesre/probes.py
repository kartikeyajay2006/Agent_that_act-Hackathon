"""HTTP health/readiness probes against catalogued base URLs."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .catalog import Instance


def probe(inst: Instance, path: str, timeout_s: float = 2.0) -> dict[str, Any]:
    if not inst.base_url:
        return {"checked": False, "reason": "no_http_endpoint"}
    started = time.perf_counter()
    try:
        resp = httpx.get(f"{inst.base_url}{path}", timeout=timeout_s)
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text[:200]
        return {
            "checked": True,
            "ok": resp.status_code == 200,
            "http_status": resp.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "body": body,
        }
    except httpx.HTTPError as exc:
        return {
            "checked": True,
            "ok": False,
            "error": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def wait_ready(inst: Instance, timeout_s: float, path: str = "/ready") -> dict[str, Any]:
    """Poll until the endpoint answers 200 or the deadline passes. Returns the last probe."""
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    last: dict[str, Any] = {"checked": False}
    while time.monotonic() < deadline:
        last = probe(inst, path)
        if last.get("ok"):
            last["waited_seconds"] = round(time.monotonic() - started, 1)
            return last
        time.sleep(0.5)
    last["waited_seconds"] = round(time.monotonic() - started, 1)
    return last
