"""payment-service v1: records a payment in PostgreSQL through a bounded pool."""

from __future__ import annotations

import os
import random
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.concurrency import run_in_threadpool

from common.obs import JsonLogger

SERVICE = "payment-service"
VERSION = os.environ.get("APP_VERSION", "v1")
POOL_MIN = int(os.environ.get("DB_POOL_MIN", "4"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
POOL_TIMEOUT = float(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "2.0"))
PROCESSOR_LATENCY_MS = (
    float(os.environ.get("PROCESSOR_LATENCY_MIN_MS", "15")),
    float(os.environ.get("PROCESSOR_LATENCY_MAX_MS", "45")),
)

log = JsonLogger(SERVICE, VERSION, min_level=os.environ.get("LOG_LEVEL", "INFO"))
app = FastAPI(title=f"{SERVICE}-{VERSION}")

pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=POOL_MIN,
    max_size=POOL_MAX,
    timeout=POOL_TIMEOUT,
    kwargs={"application_name": f"{SERVICE}-{VERSION}", "autocommit": True},
    open=False,
)

REQUESTS = Counter("http_requests", "HTTP requests handled", ["service", "version", "route", "status"])
ERRORS = Counter("http_request_errors", "HTTP requests that failed", ["service", "version", "route", "error"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "version", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0),
)
DB_ACTIVE = Gauge("db_connections_active", "Pool connections checked out", ["service", "version"])
DB_OPEN = Gauge("db_connections_open", "Pool connections open to PostgreSQL", ["service", "version"])
DB_MAX = Gauge("db_connections_max", "Pool connection limit", ["service", "version"])
DB_WAITING = Gauge("db_pool_requests_waiting", "Requests waiting for a pool connection", ["service", "version"])
SERVICE_UP = Gauge("service_up", "1 when the service can serve traffic", ["service", "version"])


def pool_snapshot() -> dict[str, int]:
    stats = pool.get_stats()
    size = stats.get("pool_size", 0)
    available = stats.get("pool_available", 0)
    return {
        "active": max(size - available, 0),
        "open": size,
        "waiting": stats.get("requests_waiting", 0),
        "limit": POOL_MAX,
    }


def refresh_pool_gauges() -> dict[str, int]:
    snap = pool_snapshot()
    DB_ACTIVE.labels(SERVICE, VERSION).set(snap["active"])
    DB_OPEN.labels(SERVICE, VERSION).set(snap["open"])
    DB_WAITING.labels(SERVICE, VERSION).set(snap["waiting"])
    return snap


@app.on_event("startup")
def startup() -> None:
    DB_MAX.labels(SERVICE, VERSION).set(POOL_MAX)
    pool.open(wait=True, timeout=30)
    SERVICE_UP.labels(SERVICE, VERSION).set(1)
    log.info("service_started", pool_min=POOL_MIN, pool_limit=POOL_MAX, pool_timeout_s=POOL_TIMEOUT)


@app.on_event("shutdown")
def shutdown() -> None:
    log.info("service_stopping", **pool_snapshot())
    pool.close(timeout=5)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready() -> Response:
    snap = refresh_pool_gauges()
    try:
        with pool.connection(timeout=1.0) as conn:
            conn.execute("SELECT 1")
    except PoolTimeout:
        SERVICE_UP.labels(SERVICE, VERSION).set(0)
        return JSONResponse({"status": "not_ready", "reason": "db_pool_exhausted", "pool": snap}, status_code=503)
    except Exception as exc:  # database unreachable is a readiness failure, not a crash
        SERVICE_UP.labels(SERVICE, VERSION).set(0)
        return JSONResponse({"status": "not_ready", "reason": type(exc).__name__, "pool": snap}, status_code=503)
    SERVICE_UP.labels(SERVICE, VERSION).set(1)
    return JSONResponse({"status": "ready", "version": VERSION, "pool": snap})


@app.get("/version")
def version() -> dict:
    return {"service": SERVICE, "version": VERSION}


@app.get("/metrics")
def metrics() -> Response:
    refresh_pool_gauges()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def insert_payment(payment_id: str, customer_id: str, amount_cents: int, currency: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO payments (id, customer_id, amount_cents, currency, status, service_version)"
            " VALUES (%s, %s, %s, %s, 'captured', %s)",
            (payment_id, customer_id, amount_cents, currency, VERSION),
        )


@app.post("/pay")
async def pay(request: Request) -> Response:
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
    body = await request.json()
    payment_id = str(uuid.uuid4())
    customer_id = str(body.get("customer_id", "anonymous"))[:64]
    amount_cents = int(body.get("amount_cents", 0))
    currency = str(body.get("currency", "USD"))[:3]

    status, error = 200, None
    try:
        await run_in_threadpool(insert_payment, payment_id, customer_id, amount_cents, currency)
        await run_in_threadpool(time.sleep, random.uniform(*PROCESSOR_LATENCY_MS) / 1000)
    except PoolTimeout:
        status, error = 500, "db_pool_timeout"
        snap = pool_snapshot()
        log.error(
            "db_pool_timeout",
            request_id=request_id,
            active_connections=snap["active"],
            pool_limit=snap["limit"],
            requests_waiting=snap["waiting"],
            wait_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    except Exception as exc:
        status, error = 500, "db_error"
        log.error("db_error", request_id=request_id, error_type=type(exc).__name__, error=str(exc)[:300])

    elapsed = time.perf_counter() - started
    REQUESTS.labels(SERVICE, VERSION, "/pay", str(status)).inc()
    LATENCY.labels(SERVICE, VERSION, "/pay").observe(elapsed)
    refresh_pool_gauges()
    if error:
        ERRORS.labels(SERVICE, VERSION, "/pay", error).inc()
        return JSONResponse({"error": error, "version": VERSION}, status_code=status)
    return JSONResponse({"payment_id": payment_id, "status": "captured", "version": VERSION})
