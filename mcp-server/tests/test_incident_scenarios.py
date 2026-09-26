from __future__ import annotations

from types import SimpleNamespace

from forgesre import cli


def test_incident_scenarios_resolve_to_catalogued_versions(settings):
    scenarios = settings.incidents["scenarios"]
    assert settings.incidents["default_scenario"] in scenarios
    for scenario in scenarios.values():
        assert scenario["version"] in settings.service_map[scenario["service"]]["versions"]


def test_scenario_trigger_uses_configured_service_and_version(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_deploy", lambda ops, service, version: calls.append((service, version)) or 0)

    result = cli.cmd_trigger_incident(SimpleNamespace(settings=settings), "latency-regression")

    configured = settings.incidents["scenarios"]["latency-regression"]
    assert result == 0
    assert calls == [(configured["service"], configured["version"])]


def test_incident_check_accepts_any_firing_configured_alert(monkeypatch):
    async def mocked_synthetic_check(*args, **kwargs):
        return {"success_ratio": 1.0}

    monkeypatch.setattr(cli, "run_checkout_probes", mocked_synthetic_check)

    def scalar(signal, window):
        return {
            "checkout_error_rate": 0.0,
            "checkout_latency_p95": 1.4,
            "checkout_request_rate": 10.0,
        }[signal]

    ops = SimpleNamespace(
        settings=SimpleNamespace(
            verification={
                "recovery": {"metric_window": "20s", "synthetic_requests": 4},
                "synthetic": {"timeout_seconds": 2},
            }
        ),
        catalog=SimpleNamespace(instance=lambda name: SimpleNamespace(base_url="http://gateway.invalid")),
        _scalar=scalar,
        _alerts=lambda: [{"state": "firing", "alert": "CheckoutLatencyHigh"}],
        prom=SimpleNamespace(scalar=lambda query: 0),
        deploy=SimpleNamespace(active_version=lambda service: "v3"),
    )

    passed, facts = cli._check_once(ops, "incident")

    assert passed
    assert facts["firing_alerts"] == ["CheckoutLatencyHigh"]
    assert facts["checkout_latency_p95_20s"] == 1.4
