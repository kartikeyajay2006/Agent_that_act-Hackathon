"""Steady checkout traffic against the api-gateway (open-loop, fixed rate)."""

from __future__ import annotations

import asyncio
import os
import random
import uuid

import httpx

from common.obs import JsonLogger

TARGET = os.environ.get("TARGET_URL", "http://gateway:8080/checkout")
RATE = float(os.environ.get("REQUESTS_PER_SECOND", "10"))
MAX_IN_FLIGHT = int(os.environ.get("MAX_IN_FLIGHT", "100"))
SUMMARY_EVERY_S = float(os.environ.get("SUMMARY_EVERY_SECONDS", "15"))
AMOUNT_RANGE = (int(os.environ.get("AMOUNT_MIN_CENTS", "500")), int(os.environ.get("AMOUNT_MAX_CENTS", "20000")))

log = JsonLogger("traffic-generator")


async def one_request(client: httpx.AsyncClient, stats: dict[str, int], sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            resp = await client.post(
                TARGET,
                json={
                    "customer_id": f"cust-{random.randint(1, 5000)}",
                    "amount_cents": random.randint(*AMOUNT_RANGE),
                    "currency": "USD",
                },
                headers={"x-request-id": uuid.uuid4().hex[:16]},
            )
            stats["ok" if resp.status_code == 200 else "failed"] += 1
        except httpx.HTTPError:
            stats["failed"] += 1


async def main() -> None:
    sem = asyncio.Semaphore(MAX_IN_FLIGHT)
    stats = {"ok": 0, "failed": 0}
    interval = 1.0 / RATE
    loop = asyncio.get_running_loop()
    next_summary = loop.time() + SUMMARY_EVERY_S
    log.info("traffic_started", target=TARGET, rps=RATE)
    async with httpx.AsyncClient(timeout=10) as client:
        next_tick = loop.time()
        while True:
            asyncio.create_task(one_request(client, stats, sem))
            next_tick += interval
            now = loop.time()
            if now >= next_summary:
                total = stats["ok"] + stats["failed"]
                log.info("traffic_summary", window_s=SUMMARY_EVERY_S, sent=total, **stats)
                stats["ok"] = stats["failed"] = 0
                next_summary = now + SUMMARY_EVERY_S
            await asyncio.sleep(max(0.0, next_tick - now))


if __name__ == "__main__":
    asyncio.run(main())
