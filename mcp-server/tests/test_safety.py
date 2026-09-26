"""Safety tests: server-side guards hold regardless of what the model asks for."""

from __future__ import annotations

import pytest

from forgesre.catalog import validate_name, validate_version
from forgesre.results import ToolError

REASON = "error rate high after deploy, pool exhausted, restart did not help"


@pytest.mark.parametrize(
    "name",
    ["rm -rf /", "payment-service; docker rm -f x", "../../etc/passwd", "$(whoami)", "Payment", "a", "x" * 80, ""],
)
def test_injection_shaped_names_rejected(name):
    with pytest.raises(ToolError) as e:
        validate_name(name)
    assert e.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("version", ["v1; rm", "latest", "1", "v1.2", "V1", "v1000"])
def test_bad_versions_rejected(version):
    with pytest.raises(ToolError):
        validate_version(version)


def test_unknown_service_rejected(ops):
    with pytest.raises(ToolError) as e:
        ops.catalog.instance("billing-service")
    assert e.value.code == "UNKNOWN_SERVICE"


def test_versioned_service_requires_explicit_instance_for_restart(ops):
    with pytest.raises(ToolError) as e:
        ops.restart("payment-service", REASON)
    assert e.value.code == "AMBIGUOUS_SERVICE"


@pytest.mark.parametrize("target", ["postgres", "prometheus"])
def test_restart_of_stateful_or_infra_is_refused(ops, target):
    with pytest.raises(ToolError) as e:
        ops.restart(target, REASON)
    assert e.value.code == "ACTION_NOT_ALLOWED"


def test_restart_rate_limited_by_policy(ops):
    for _ in range(2):
        ops.audit.record("action_completed", "restart_service", "success", {"target": "payment-service-v2"})
    with pytest.raises(ToolError) as e:
        ops.restart("payment-service-v2", REASON)
    assert e.value.code == "RATE_LIMITED"


def test_rollback_to_unknown_version_rejected(v2_active):
    with pytest.raises(ToolError) as e:
        v2_active.rollback("payment-service", "v2", "v9", REASON)
    assert e.value.code == "UNKNOWN_VERSION"


def test_rollback_to_current_version_is_noop(v2_active):
    out = v2_active.rollback("payment-service", "v1", "v2", REASON)
    assert out["result"] == "ALREADY_AT_TARGET"
    assert v2_active.deploy.active_version("payment-service") == "v2"


def test_rollback_from_wrong_version_rejected(v2_active):
    with pytest.raises(ToolError) as e:
        v2_active.rollback("payment-service", "v1", "v1", REASON)
    assert e.value.code == "VERSION_MISMATCH"
    assert v2_active.deploy.active_version("payment-service") == "v2"


def test_rollback_of_non_eligible_service_rejected(v2_active):
    with pytest.raises(ToolError) as e:
        v2_active.rollback("postgres", "v2", "v1", REASON)
    assert e.value.code == "ACTION_NOT_ALLOWED"


def test_rollback_requires_active_incident(v2_active):
    with pytest.raises(ToolError) as e:
        v2_active.rollback("payment-service", "v2", "v1", REASON)
    assert e.value.code == "NO_ACTIVE_INCIDENT"
    assert v2_active.deploy.active_version("payment-service") == "v2"


def _with_incident(ops):
    ops.audit.open_incident("test incident", [], {})
    return ops


def test_rollback_refused_without_trueforge_approval(v2_active):
    ops = _with_incident(v2_active)
    ops.trueforge.find_approval = lambda **kw: None
    with pytest.raises(ToolError) as e:
        ops.rollback("payment-service", "v2", "v1", REASON)
    assert e.value.code == "APPROVAL_NOT_FOUND"
    assert ops.deploy.active_version("payment-service") == "v2"
    assert not ops.audit.events(types={"action_started"})


def test_single_approval_cannot_be_replayed(v2_active):
    ops = _with_incident(v2_active)
    approval = {"session_id": "s", "tool_call_id": "call_1", "approval_event_id": "e", "approved_at": "t"}
    ops.trueforge.find_approval = lambda **kw: approval
    ops.audit.record("action_started", "rollback_deployment", "started", {"approval": {"attestation": approval}})
    with pytest.raises(ToolError) as e:
        ops.rollback("payment-service", "v2", "v1", REASON)
    assert e.value.code == "APPROVAL_ALREADY_USED"
    assert ops.deploy.active_version("payment-service") == "v2"


def test_unreachable_trueforge_blocks_rollback(v2_active):
    from forgesre.trueforge import TrueForgeError

    ops = _with_incident(v2_active)

    def boom(**kw):
        raise TrueForgeError("down")

    ops.trueforge.find_approval = boom
    with pytest.raises(ToolError) as e:
        ops.rollback("payment-service", "v2", "v1", REASON)
    assert e.value.code == "APPROVAL_UNVERIFIABLE"
    assert ops.deploy.active_version("payment-service") == "v2"


def test_no_generic_command_tool_is_exposed():
    from forgesre import server

    names = set(server.mcp._tool_manager._tools)  # type: ignore[attr-defined]
    for forbidden in ("run_shell", "exec", "run_command", "docker", "query_sql", "raw_promql"):
        assert not any(forbidden in n for n in names)
    assert "rollback_deployment" in names


def test_rollback_tool_is_annotated_destructive():
    from forgesre import server

    tool = server.mcp._tool_manager._tools["rollback_deployment"]  # type: ignore[attr-defined]
    assert tool.annotations.destructive_hint is True
    restart = server.mcp._tool_manager._tools["restart_service"]  # type: ignore[attr-defined]
    assert restart.annotations.destructive_hint is False and restart.annotations.read_only_hint is False


def test_agent_spec_gates_rollback_in_trueforge():
    from forgesre.trueforge import TrueForge

    spec = TrueForge("http://x").agent_manifest(model_fqn="p/m", instructions="i")
    mcp = spec["mcp_servers"][0]
    assert "rollback_deployment" in mcp["require_approval_for_tools"]
    assert spec["config"]["sandbox"]["enabled"] is True
