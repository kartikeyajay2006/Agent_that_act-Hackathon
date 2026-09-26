"""Integration tests against the running docker demo stack (ordered; ~5 minutes).

    uv run pytest -m integration

Covers: healthy baseline, incident trigger, logs/metrics/DB evidence, restart
of the right container, restart NOT fixing the incident, real rollback, and
reset back to the known-good state. TrueForge approval attestation is covered
by test_safety.py and the TrueForge end-to-end run; here it is disabled via a
test-only policy override so the rollback mechanics can be exercised directly.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import subprocess
import time

import httpx
import pytest

from forgesre.config import get_settings
from forgesre.ops import Ops

pytestmark = pytest.mark.integration
ROOT = get_settings().root
REASON = "integration test: error rate high after v2 deploy, pool exhausted"


def _stack_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:18080/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


if not _stack_up():
    pytest.skip("demo stack not running (./scripts/start-demo.sh)", allow_module_level=True)


def sh(script: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ROOT / "scripts" / script), *args], capture_output=True, text=True, timeout=timeout, cwd=ROOT
    )


@pytest.fixture(scope="module")
def ops():
    base = get_settings()
    policy = copy.deepcopy(base.policy)
    policy["actions"]["rollback_deployment"]["require_trueforge_attestation"] = False
    return Ops(dataclasses.replace(base, policy=policy))


def wait_for(pred, timeout: float, every: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(every)
    return pred()


def test_01_reset_gives_healthy_baseline(ops):
    proc = sh("reset-demo.sh", "150", timeout=400)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert ops.deploy.active_version("payment-service") == "v1"
    assert not ops.docker.state(ops.catalog.instance("payment-service-v2")).get("exists")
    health = ops.service_health("payment-service")
    assert health["overall"] == "healthy"


def test_02_healthy_synthetics(ops):
    out = asyncio.run(ops.synthetic_check(10))
    assert out["success_ratio"] == 1.0
    assert set(out["routed_to"]) == {"payment-service-v1"}


def test_03_trigger_produces_observable_incident(ops):
    assert sh("trigger-incident.sh").returncode == 0
    assert ops.deploy.active_version("payment-service") == "v2"
    proc = sh("verify-incident.sh", "150", timeout=200)
    assert proc.returncode == 0, proc.stdout[-1500:]


def test_04_logs_show_pool_timeouts_from_v2(ops):
    out = ops.service_logs("payment-service", 5, "ERROR", None, 5)
    events = {(e["instance"], e["event"]) for e in out["event_counts"]}
    assert ("payment-service-v2", "db_pool_timeout") in events
    assert out["returned"] <= 5


def test_05_prometheus_shows_incident(ops):
    snap = ops.snapshot()
    assert snap["checkout_error_rate"] > 0.2
    assert snap["db_connection_utilization"] > 0.8
    assert snap["db_pool_utilization_by_version"]["v2"] >= 0.99


def test_06_database_shows_v2_holding_connections(ops):
    db = ops.db_health()
    top = db["connections_by_client"][0]
    assert top["client"] == "payment-service-v2"
    assert "idle in transaction" in top["by_state"]


def test_07_restart_restarts_the_right_container(ops):
    v1 = ops.catalog.instance("payment-service-v1")
    v2 = ops.catalog.instance("payment-service-v2")
    v1_before = ops.docker.state(v1)["started_at"]
    v2_before = ops.docker.state(v2)["started_at"]
    out = ops.restart("payment-service-v2", REASON)
    assert out["success"] and out["container_running"]
    assert ops.docker.state(v2)["started_at"] != v2_before
    assert ops.docker.state(v1)["started_at"] == v1_before


def test_08_restart_does_not_resolve_incident(ops):
    out = asyncio.run(ops.verify(None))
    assert out["verdict"] == "NOT_RECOVERED"
    assert "synthetic_success_ratio" in out["failed_criteria"]


def test_09_rollback_changes_live_routing(ops):
    ops._ensure_incident()
    out = ops.rollback("payment-service", "v2", "v1", REASON)
    assert out["success"], json.dumps(out)[:500]
    assert out["gateway_observed_version"] == "v1"
    route = httpx.get("http://127.0.0.1:18080/route", timeout=2).json()
    assert route["active_version"] == "v1"
    assert not ops.docker.state(ops.catalog.instance("payment-service-v2")).get("running")
    again = ops.rollback("payment-service", "v2", "v1", REASON)
    assert again["result"] == "ALREADY_AT_TARGET"


def test_10_recovery_verified_after_rollback(ops):
    out = asyncio.run(ops.verify(None))
    assert out["verdict"] == "RECOVERED", json.dumps(out["criteria"])
    synth = asyncio.run(ops.synthetic_check(20))
    assert synth["success_ratio"] >= 0.95


def test_11_reset_returns_to_known_state(ops):
    sh("trigger-incident.sh")
    proc = sh("reset-demo.sh", "150", timeout=400)
    assert proc.returncode == 0, proc.stdout[-1500:]
    assert ops.deploy.active_version("payment-service") == "v1"
    assert ops.audit.incident() is None
    assert not ops.docker.state(ops.catalog.instance("payment-service-v2")).get("exists")


def test_12_mcp_endpoint_requires_token_and_serves_tools():
    import mcp_wire

    unauth = httpx.post("http://127.0.0.1:18900/mcp", json={}, timeout=5)
    assert unauth.status_code == 401
    tools = {t["name"]: t for t in mcp_wire.list_tools()}
    assert tools["rollback_deployment"]["annotations"]["destructiveHint"] is True
    assert tools["get_service_logs"]["annotations"]["readOnlyHint"] is True
