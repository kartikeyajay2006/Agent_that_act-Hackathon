"""Prometheus access through a fixed catalogue of named signals.

The agent picks a signal name; it never sends raw PromQL. Each signal is a
template with a {w} rate-window placeholder.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any

import httpx

from .results import ToolError

GW = 'service="api-gateway",route="/checkout"'

SIGNALS: dict[str, dict[str, str]] = {
    "checkout_error_rate": {
        "description": "Fraction of /checkout requests at the gateway that did not return 200",
        "unit": "ratio",
        "promql": f'(sum(rate(http_requests_total{{{GW},status!="200"}}[{{w}}])) or vector(0))'
        f" / clamp_min(sum(rate(http_requests_total{{{GW}}}[{{w}}])), 0.001)",
    },
    "checkout_request_rate": {
        "description": "Requests per second hitting /checkout",
        "unit": "req/s",
        "promql": f"sum(rate(http_requests_total{{{GW}}}[{{w}}]))",
    },
    "checkout_latency_p95": {
        "description": "p95 latency of /checkout at the gateway",
        "unit": "seconds",
        "promql": f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{GW}}}[{{w}}])))",
    },
    "checkout_errors_by_reason": {
        "description": "Gateway checkout failures per second, by failure reason and upstream version",
        "unit": "req/s",
        "promql": 'sum by (reason, upstream_version) (rate(http_request_errors_total{service="api-gateway"}[{w}]))',
    },
    "traffic_by_upstream_version": {
        "description": "Checkout requests per second, by payment-service version that served them",
        "unit": "req/s",
        "promql": f"sum by (upstream_version) (rate(http_requests_total{{{GW}}}[{{w}}]))",
    },
    "payment_error_rate": {
        "description": "Fraction of payment-service /pay requests failing, per version",
        "unit": "ratio",
        "promql": '(sum by (version) (rate(http_requests_total{service="payment-service",status!="200"}[{w}]))'
        ' or 0 * sum by (version) (rate(http_requests_total{service="payment-service"}[{w}])))'
        ' / clamp_min(sum by (version) (rate(http_requests_total{service="payment-service"}[{w}])), 0.001)',
    },
    "payment_errors_by_type": {
        "description": "payment-service failures per second, by version and error type",
        "unit": "req/s",
        "promql": 'sum by (version, error) (rate(http_request_errors_total{service="payment-service"}[{w}]))',
    },
    "payment_latency_p95": {
        "description": "p95 latency of payment-service /pay, per version",
        "unit": "seconds",
        "promql": "histogram_quantile(0.95, sum by (le, version) "
        '(rate(http_request_duration_seconds_bucket{service="payment-service"}[{w}])))',
    },
    "db_pool_active_connections": {
        "description": "Connections checked out of each payment-service version's pool",
        "unit": "connections",
        "promql": 'max by (version) (db_connections_active{service="payment-service"})',
    },
    "db_pool_utilization": {
        "description": "Checked-out connections / pool limit, per payment-service version",
        "unit": "ratio",
        "promql": 'max by (version) (db_connections_active{service="payment-service"})'
        ' / max by (version) (db_connections_max{service="payment-service"})',
    },
    "db_pool_requests_waiting": {
        "description": "Requests blocked waiting for a pool connection, per version",
        "unit": "requests",
        "promql": 'max by (version) (db_pool_requests_waiting{service="payment-service"})',
    },
    "db_connections_total": {
        "description": "Open PostgreSQL connections (all clients)",
        "unit": "connections",
        "promql": "sum(pg_stat_activity_count)",
    },
    "db_connection_utilization": {
        "description": "Open PostgreSQL connections / max_connections",
        "unit": "ratio",
        "promql": "sum(pg_stat_activity_count) / max(pg_settings_max_connections)",
    },
    "scrape_up": {
        "description": "Prometheus scrape health per target (1 = reachable)",
        "unit": "bool",
        "promql": "max by (job, instance_version) (up)",
    },
}

_WINDOW = re.compile(r"^[0-9]{1,3}[sm]$")


def validate_window(w: str) -> str:
    if not _WINDOW.fullmatch(w):
        raise ToolError("INVALID_INPUT", "rate window must look like 30s or 2m", field="rate_window", value=w)
    return w


def render(signal: str, window: str = "1m") -> str:
    if signal not in SIGNALS:
        raise ToolError("UNKNOWN_SIGNAL", f"unknown metric signal '{signal}'", known_signals=sorted(SIGNALS))
    return SIGNALS[signal]["promql"].replace("{w}", validate_window(window))


def _num(v: str) -> float | None:
    x = float(v)
    return None if math.isnan(x) or math.isinf(x) else x


class Prometheus:
    def __init__(self, base_url: str, timeout_s: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            resp = httpx.get(f"{self.base_url}{path}", params=params, timeout=self.timeout_s)
        except httpx.HTTPError as exc:
            raise ToolError("PROMETHEUS_UNAVAILABLE", f"cannot reach Prometheus: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ToolError("PROMETHEUS_ERROR", f"Prometheus returned {resp.status_code}", body=resp.text[:300])
        payload = resp.json()
        if payload.get("status") != "success":
            raise ToolError("PROMETHEUS_ERROR", payload.get("error", "query failed"))
        return payload["data"]

    def instant(self, promql: str, at: float | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": promql}
        if at is not None:
            params["time"] = at
        data = self._get("/api/v1/query", params)
        return [{"labels": r["metric"], "value": _num(r["value"][1])} for r in data.get("result", [])]

    def range(self, promql: str, start: float, end: float, step: float) -> list[dict[str, Any]]:
        data = self._get("/api/v1/query_range", {"query": promql, "start": start, "end": end, "step": step})
        return [
            {"labels": r["metric"], "points": [[float(t), _num(v)] for t, v in r.get("values", [])]}
            for r in data.get("result", [])
        ]

    def scalar(self, promql: str) -> float | None:
        rows = self.instant(promql)
        vals = [r["value"] for r in rows if r["value"] is not None]
        return vals[0] if vals else None

    def alerts(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/alerts", {}).get("alerts", [])

    def signal_now(self, signal: str, window: str = "1m") -> list[dict[str, Any]]:
        return self.instant(render(signal, window))

    def signal_range(self, signal: str, minutes: float, step_s: float, window: str = "30s") -> list[dict[str, Any]]:
        end = time.time()
        return self.range(render(signal, window), end - minutes * 60, end, step_s)


def summarize_series(points: list[list[float | None]], max_points: int = 30) -> dict[str, Any]:
    vals = [v for _, v in points if v is not None]
    if not vals:
        return {"samples": 0}
    stride = max(1, math.ceil(len(points) / max_points))
    sampled = [[int(t), None if v is None else round(v, 4)] for t, v in points[::stride]]
    return {
        "samples": len(vals),
        "first": round(vals[0], 4),
        "last": round(vals[-1], 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "avg": round(sum(vals) / len(vals), 4),
        "points": sampled,
    }
