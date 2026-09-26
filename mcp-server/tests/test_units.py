"""Unit tests: parsing, filtering, thresholds, deployment state, report, TrueForge event parsing."""

from __future__ import annotations

import json

import pytest

from forgesre import logs as logmod
from forgesre.catalog import validate_name
from forgesre.ops import evaluate_recovery
from forgesre.prom import SIGNALS, render, summarize_series
from forgesre.report import build_report
from forgesre.results import ToolError
from forgesre.trueforge import TrueForge, _args_match, _tool_calls

# ---------------------------------------------------------------- metrics


def test_render_substitutes_rate_window():
    q = render("checkout_error_rate", "45s")
    assert "[45s]" in q and "{w}" not in q


def test_every_signal_renders():
    for name in SIGNALS:
        assert "{w}" not in render(name, "30s")


@pytest.mark.parametrize("window", ["30", "1h", "5m]) or vector(1", "-1s", ""])
def test_render_rejects_bad_windows(window):
    with pytest.raises(ToolError) as e:
        render("checkout_error_rate", window)
    assert e.value.code == "INVALID_INPUT"


def test_render_rejects_unknown_signal():
    with pytest.raises(ToolError) as e:
        render("up or vector(1)", "30s")
    assert e.value.code == "UNKNOWN_SIGNAL"


def test_summarize_series_stats_and_bounds():
    points = [[1000 + i, float(i)] for i in range(100)] + [[2000, None]]
    s = summarize_series(points, max_points=10)
    assert s["min"] == 0 and s["max"] == 99 and s["first"] == 0 and s["last"] == 99
    assert s["samples"] == 100
    assert len(s["points"]) <= 11


def test_summarize_series_empty():
    assert summarize_series([[1, None]]) == {"samples": 0}


# ---------------------------------------------------------------- logs


def test_parse_structured_line():
    raw = '2026-09-26T06:00:00.1Z {"level":"error","event":"db_pool_timeout","active_connections":85}'
    rec = logmod.parse_line(raw, "payment-service-v2")
    assert rec["level"] == "ERROR" and rec["event"] == "db_pool_timeout" and rec["instance"] == "payment-service-v2"


def test_parse_unstructured_line():
    rec = logmod.parse_line("2026-09-26T06:00:00Z LOG: checkpoint starting", "postgres")
    assert rec["event"] == "unstructured" and "checkpoint" in rec["message"]


def test_redacts_secrets_and_dsn_passwords():
    text = 'connect postgresql://payments:s3cr3t@db:5432/x password=hunter2 "api_key": "abc123"'
    red = logmod.redact(text)
    assert "s3cr3t" not in red and "hunter2" not in red and "abc123" not in red


def test_level_and_query_filtering():
    rec = {"level": "WARN", "event": "db_pool_timeout", "instance": "a", "timestamp": "t"}
    assert logmod.matches(rec, "WARN", None)
    assert not logmod.matches(rec, "ERROR", None)
    assert logmod.matches(rec, None, "pool timeout")
    assert not logmod.matches(rec, None, "pool deadlock")


def test_log_summary_counts_and_first_last():
    recs = [
        {"instance": "v2", "level": "ERROR", "event": "db_pool_timeout", "timestamp": f"2026-01-01T00:00:0{i}Z"}
        for i in range(3)
    ]
    s = logmod.summarize(recs)["event_counts"][0]
    assert s["count"] == 3 and s["first_seen"].endswith("00Z") and s["last_seen"].endswith("02Z")


def test_bucket_counts_only_warn_and_lifecycle():
    recs = [
        {"instance": "v2", "level": "ERROR", "event": "e", "timestamp": "2026-01-01T00:00:01+00:00"},
        {"instance": "v2", "level": "ERROR", "event": "e", "timestamp": "2026-01-01T00:00:09+00:00"},
        {"instance": "v2", "level": "INFO", "event": "checkout_completed", "timestamp": "2026-01-01T00:00:05+00:00"},
        {"instance": "v2", "level": "INFO", "event": "service_started", "timestamp": "2026-01-01T00:00:05+00:00"},
    ]
    b = logmod.bucket_counts(recs, 10)
    assert b["v2"]["e"][0][1] == 2
    assert "checkout_completed" not in b["v2"] and "service_started" in b["v2"]


# ---------------------------------------------------------------- verification thresholds

TH = {
    "synthetic_success_ratio_min": 0.95,
    "checkout_error_rate_max": 0.05,
    "checkout_latency_p95_seconds_max": 1.0,
    "db_connection_utilization_max": 0.7,
    "active_version_ready": True,
}
HEALTHY = {"checkout_error_rate": 0.0, "checkout_latency_p95_seconds": 0.05, "db_connection_utilization": 0.07}


def test_recovery_passes_when_all_thresholds_hold():
    crit = evaluate_recovery(ready=True, synthetic_ratio=1.0, signals=HEALTHY, thresholds=TH)
    assert all(c["passed"] for c in crit)


@pytest.mark.parametrize(
    "override, failing",
    [
        ({"ready": False}, "active_version_ready"),
        ({"synthetic_ratio": 0.9}, "synthetic_success_ratio"),
        ({"signals": {**HEALTHY, "checkout_error_rate": 0.06}}, "checkout_error_rate"),
        ({"signals": {**HEALTHY, "checkout_latency_p95_seconds": 2.4}}, "checkout_latency_p95_seconds"),
        ({"signals": {**HEALTHY, "db_connection_utilization": 0.91}}, "db_connection_utilization"),
        ({"signals": {**HEALTHY, "checkout_error_rate": None}}, "checkout_error_rate"),
    ],
)
def test_recovery_fails_on_each_threshold(override, failing):
    args = {"ready": True, "synthetic_ratio": 1.0, "signals": HEALTHY, "thresholds": TH, **override}
    crit = {c["criterion"]: c["passed"] for c in evaluate_recovery(**args)}
    assert crit[failing] is False
    assert sum(not v for v in crit.values()) == 1


def test_thresholds_loaded_from_config(settings):
    th = settings.verification["recovery"]["thresholds"]
    assert set(TH) <= set(th)


# ---------------------------------------------------------------- deployment state


def test_record_switch_tracks_active_and_history(ops):
    ops.deploy.record_switch(service="payment-service", to_version="v1", change_type="deploy", actor="t", metadata={})
    ops.deploy.record_switch(service="payment-service", to_version="v2", change_type="deploy", actor="t", metadata={})
    ops.deploy.record_switch(service="payment-service", to_version="v1", change_type="rollback", actor="t", metadata={})
    assert ops.deploy.active_version("payment-service") == "v1"
    hist = ops.deploy.history("payment-service")
    assert [h["version"] for h in hist] == ["v1", "v2", "v1"]
    assert [h["status"] for h in hist] == ["active", "rolled_back", "superseded"]
    assert hist[0]["previous_version"] == "v2"
    state = json.loads(ops.deploy.path.read_text())
    assert state["services"]["payment-service"]["active_version"] == "v1"


def test_gateway_state_file_is_written_atomically(ops):
    ops.deploy.record_switch(service="payment-service", to_version="v1", change_type="deploy", actor="t", metadata={})
    assert not list(ops.deploy.dir.glob("*.tmp"))


# ---------------------------------------------------------------- report


def test_report_numbers_come_from_recorded_events(ops):
    before = {"active_version": "v2", "checkout_error_rate": 0.93, "db_connection_utilization": 0.91}
    ops.audit.open_incident("Production alert: CheckoutErrorRateHigh", [{"alert": "CheckoutErrorRateHigh"}], before)
    ops.audit.record(
        "action_started",
        "rollback_deployment",
        "started",
        {"action_id": "a1", "service": "payment-service", "from_version": "v2", "to_version": "v1", "reason": "r"},
    )
    ops.audit.record("action_completed", "rollback_deployment", "success", {"action_id": "a1", "duration_seconds": 2.5})
    after = {"active_version": "v1", "checkout_error_rate": 0.0, "db_connection_utilization": 0.07}
    crit = evaluate_recovery(ready=True, synthetic_ratio=1.0, signals={**HEALTHY, **after}, thresholds=TH)
    ops.audit.record(
        "verification_completed",
        "verify_recovery",
        "RECOVERED",
        {"criteria": crit, "failed_criteria": [], "signals": after, "synthetic": {"succeeded": 20, "requests": 20}},
    )
    out = build_report(
        ops.settings, ops.audit, summary="s" * 20, root_cause="r" * 20, evidence=["e"], confidence="HIGH",
        sandbox_analysis=None, human_decisions=[], follow_ups=[],
    )  # fmt: skip
    md = out["markdown"]
    assert out["final_status"] == "RESOLVED"
    assert "93.0%" in md and "91.0%" in md and "7.0%" in md and "20/20" in md
    assert (ops.settings.artifacts_dir / "incidents" / f"{out['incident_id']}.md").exists()


def test_report_unresolved_without_recovered_verification(ops):
    ops.audit.open_incident("x", [], {})
    out = build_report(
        ops.settings, ops.audit, summary="s" * 20, root_cause="r" * 20, evidence=["e"], confidence="LOW",
        sandbox_analysis=None, human_decisions=["rollback denied"], follow_ups=[],
    )  # fmt: skip
    assert out["final_status"] == "UNRESOLVED"
    assert "No mutating action was executed." in out["markdown"]


# ---------------------------------------------------------------- TrueForge event parsing

EVENTS = [
    {"type": "turn.created"},
    {
        "type": "model.message",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "x",
                    "arguments": '{"service":"payment-service","from_version":"v2","to_version":"v1"}',
                },
                "tool_info": {"type": "mcp", "server_name": "forgesre", "name": "rollback_deployment"},
            },
            {
                "id": "call_2",
                "function": {"name": "y", "arguments": "{}"},
                "tool_info": {"type": "mcp", "server_name": "other", "name": "rollback_deployment"},
            },
        ],
    },
    {"type": "tool.approval_required", "tool_calls": [{"id": "call_1"}]},
]


def test_tool_calls_only_from_forgesre_server():
    calls = _tool_calls(EVENTS, "rollback_deployment")
    assert [c["id"] for c in calls] == ["call_1"]
    assert calls[0]["arguments"]["from_version"] == "v2"


def test_args_match_requires_every_key():
    assert _args_match({"a": 1, "b": 2}, {"a": 1})
    assert not _args_match({"a": 1}, {"a": 1, "b": 2})
    assert not _args_match(None, {"a": 1})


def _fake_trueforge(decision: str) -> TrueForge:
    tf = TrueForge("http://trueforge.invalid")
    turns = [
        {"id": "turn1", "input": [{"type": "user.message", "content": "go"}]},
        {
            "id": "turn2",
            "created_at": "2026-09-26T06:38:26Z",
            "input": [{"type": "user.tool_approval", "tool_call_id": "call_1", "approval": {"status": decision}}],
        },
    ]
    tf._req = lambda method, path, body=None, params=None: {"data": [{"id": "sess1"}]}  # type: ignore[method-assign]
    tf.session_events = lambda sid, max_pages=20: EVENTS  # type: ignore[method-assign]
    tf.session_turns = lambda sid, max_pages=10: turns  # type: ignore[method-assign]
    return tf


def test_find_approval_allow():
    found = _fake_trueforge("allow").find_approval(
        tool="rollback_deployment", arguments={"service": "payment-service", "from_version": "v2", "to_version": "v1"}
    )
    assert found and found["tool_call_id"] == "call_1" and found["session_id"] == "sess1"


def test_find_approval_ignores_deny_and_other_args():
    wanted = {"service": "payment-service", "from_version": "v2", "to_version": "v1"}
    assert _fake_trueforge("deny").find_approval(tool="rollback_deployment", arguments=wanted) is None
    assert (
        _fake_trueforge("allow").find_approval(tool="rollback_deployment", arguments={**wanted, "to_version": "v3"})
        is None
    )


def test_names_are_strictly_validated():
    assert validate_name("payment-service-v2") == "payment-service-v2"
