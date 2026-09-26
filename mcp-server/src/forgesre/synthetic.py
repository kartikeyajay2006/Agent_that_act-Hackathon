"""Synthetic checkout probes through the public gateway (marked x-synthetic)."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from typing import Any

import httpx


async def run_checkout_probes(gateway_url: str, n: int, timeout_s: float, concurrency: int = 5) -> dict[str, Any]:
    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async def one(i: int, client: httpx.AsyncClient) -> None:
        async with sem:
            started = time.perf_counter()
            rid = f"synthetic-{uuid.uuid4().hex[:10]}"
            try:
                resp = await client.post(
                    f"{gateway_url}/checkout",
                    json={"customer_id": f"synthetic-{i}", "amount_cents": 5000, "currency": "USD"},
                    headers={"x-synthetic": "true", "x-request-id": rid},
                )
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                results.append(
                    {
                        "ok": resp.status_code == 200,
                        "status": resp.status_code,
                        "routed_to": body.get("routed_to"),
                        "error": body.get("error"),
                        "ms": (time.perf_counter() - started) * 1000,
                    }
                )
            except httpx.HTTPError as exc:
                results.append(
                    {
                        "ok": False,
                        "status": None,
                        "error": type(exc).__name__,
                        "ms": (time.perf_counter() - started) * 1000,
                    }
                )

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        await asyncio.gather(*(one(i, client) for i in range(n)))

    lat = sorted(r["ms"] for r in results)
    ok_count = sum(r["ok"] for r in results)

    def pct(p: float) -> float:
        return round(lat[min(len(lat) - 1, int(p * len(lat)))], 1) if lat else 0.0

    return {
        "requests": n,
        "succeeded": ok_count,
        "failed": n - ok_count,
        "success_ratio": round(ok_count / n, 3) if n else 0.0,
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": round(lat[-1], 1) if lat else 0.0},
        "status_codes": dict(Counter(str(r["status"]) for r in results)),
        "errors": dict(Counter(r["error"] for r in results if r["error"])),
        "routed_to": dict(Counter(r["routed_to"] for r in results if r.get("routed_to"))),
    }
