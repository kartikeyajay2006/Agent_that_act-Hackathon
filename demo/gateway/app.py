"""api-gateway: public entrypoint for checkout traffic.

Routes POST /checkout to whichever payment-service version is marked active in
the deployment state file. The state file is written by the deployment
controller (scripts / MCP server); the gateway only ever reads it, so a
rollback is a real traffic switch rather than a UI change.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from common.obs import JsonLogger

SERVICE = "api-gateway"
ROUTED_SERVICE = "payment-service"
STATE_FILE = Path(os.environ.get("DEPLOY_STATE_FILE", "/state/deployment.json"))
UPSTREAMS: dict[str, str] = json.loads(os.environ["PAYMENT_UPSTREAMS"])
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "5"))
SUCCESS_LOG_SAMPLE = float(os.environ.get("SUCCESS_LOG_SAMPLE", "0.05"))

log = JsonLogger(SERVICE, min_level=os.environ.get("LOG_LEVEL", "INFO"))
app = FastAPI(title=SERVICE)

REQUESTS = Counter(
    "http_requests",
    "HTTP requests handled",
    ["service", "route", "method", "status", "upstream_version"],
)
ERRORS = Counter(
    "http_request_errors",
    "HTTP requests that failed",
    ["service", "route", "upstream_version", "reason"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0),
)
ACTIVE_UPSTREAM = Gauge(
    "gateway_active_upstream",
    "1 for the payment-service version currently receiving traffic",
    ["service", "version"],
)
SERVICE_UP = Gauge("service_up", "1 when the service considers itself ready", ["service", "version"])


class RouteTable:
    """Reads the active version from the state file, re-reading only on mtime change."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._active: str | None = None

    def active_version(self) -> str | None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self._active
        if mtime != self._mtime:
            try:
                state = json.loads(self.path.read_text())
                version = state["services"][ROUTED_SERVICE]["active_version"]
            except (OSError, ValueError, KeyError) as exc:
                log.error("route_table_unreadable", path=str(self.path), error=str(exc))
                return self._active
            if version != self._active:
                log.info("route_switched", service=ROUTED_SERVICE, from_version=self._active, to_version=version)
            self._active = version
            self._mtime = mtime
            for v in UPSTREAMS:
                ACTIVE_UPSTREAM.labels(ROUTED_SERVICE, v).set(1 if v == version else 0)
        return self._active


routes = RouteTable(STATE_FILE)
client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)


@app.on_event("startup")
async def startup() -> None:
    version = routes.active_version()
    SERVICE_UP.labels(SERVICE, "n/a").set(1)
    log.info("gateway_started", active_version=version, upstreams=list(UPSTREAMS))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": SERVICE}


@app.get("/ready")
async def ready() -> Response:
    version = routes.active_version()
    if version is None or version not in UPSTREAMS:
        return JSONResponse({"status": "not_ready", "reason": "no_active_route"}, status_code=503)
    return JSONResponse({"status": "ready", "active_version": version})


@app.get("/route")
async def route() -> dict:
    return {"service": ROUTED_SERVICE, "active_version": routes.active_version()}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/checkout")
async def checkout(request: Request) -> Response:
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    synthetic = request.headers.get("x-synthetic") == "true"
    try:
        body = await request.json()
    except ValueError:
        body = {}
    version = routes.active_version()
    status, reason, payload = 503, "no_route", {"error": "no active payment-service route"}

    if version in UPSTREAMS:
        try:
            upstream = await client.post(
                f"{UPSTREAMS[version]}/pay",
                json=body,
                headers={"x-request-id": request_id},
            )
            status = 200 if upstream.status_code < 400 else 502
            reason = "ok" if status == 200 else f"upstream_{upstream.status_code}"
            try:
                payload = upstream.json()
            except ValueError:
                payload = {"error": upstream.text[:200]}
        except httpx.TimeoutException:
            status, reason, payload = 504, "upstream_timeout", {"error": "payment-service timed out"}
        except httpx.HTTPError as exc:
            status, reason, payload = 503, "upstream_unreachable", {"error": type(exc).__name__}

    elapsed = time.perf_counter() - started
    upstream_version = version or "none"
    REQUESTS.labels(SERVICE, "/checkout", "POST", str(status), upstream_version).inc()
    LATENCY.labels(SERVICE, "/checkout").observe(elapsed)
    if status != 200:
        ERRORS.labels(SERVICE, "/checkout", upstream_version, reason).inc()
        log.error(
            "checkout_failed",
            request_id=request_id,
            upstream="payment-service",
            upstream_version=upstream_version,
            status=status,
            reason=reason,
            upstream_error=payload.get("error") if isinstance(payload, dict) else None,
            duration_ms=round(elapsed * 1000, 1),
            synthetic=synthetic,
        )
    elif synthetic or random.random() < SUCCESS_LOG_SAMPLE:
        log.info(
            "checkout_completed",
            request_id=request_id,
            upstream_version=upstream_version,
            duration_ms=round(elapsed * 1000, 1),
            synthetic=synthetic,
        )
    payload = payload if isinstance(payload, dict) else {"result": payload}
    payload.update({"request_id": request_id, "routed_to": f"payment-service-{upstream_version}"})
    return JSONResponse(payload, status_code=status)
