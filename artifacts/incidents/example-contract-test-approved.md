<!-- Example output of mcp-server/tests/test_trueforge_lifecycle.py[allow]: TrueForge ran the loop with a scripted test-double model; every number below comes from the live stack. -->

# Incident Report — INC-20260926-071052

**Status:** RESOLVED  
**Environment:** demo-production  
**Opened:** 2026-09-26T07:10:52.880+00:00  
**Report generated:** 2026-09-26T07:12:38.702+00:00  
**Handled by:** ForgeSRE agent running on TrueForge

## Summary

Scripted harness contract test of the ForgeSRE incident lifecycle (test double model).

## Detection

- `CheckoutErrorRateHigh` (page) firing since 2026-09-26T07:10:35.997448896Z — More than 5% of checkout requests are failing (value at detection: 0.7966)
- `CheckoutLatencyHigh` (page) firing since 2026-09-26T07:10:35.997448896Z — Checkout p95 latency above 1s (value at detection: 2.4686)
- `DatabaseConnectionsHigh` (warning) firing since 2026-09-26T07:10:27.079660426Z — PostgreSQL connection usage above 80% of max_connections (value at detection: 0.91)

## Impact

- Checkout error rate at detection: **86.8%** of ~632 requests/minute
- Checkout p95 latency at detection: **2.4712s**
- PostgreSQL connection utilization at detection: **91.0%**

## Timeline (UTC)

- 07:09:22 — deployment `dep-35c6a460`: payment-service ∅ → **v1** (deploy, by release-pipeline)
- 07:10:19 — deployment `dep-5d5016c7`: payment-service v1 → **v2** (deploy, by release-pipeline)
- 07:10:52 — incident opened: Production alert: CheckoutErrorRateHigh, CheckoutLatencyHigh, DatabaseConnectionsHigh
- 07:11:22 — evidence bundle collected (15 min window)
- 07:11:22 — risk assessed for restart_service → MEDIUM
- 07:11:22 — restart_service started (payment-service-v2)
- 07:11:26 — restart_service success in 4.1s
- 07:12:05 — deployment `dep-8e01cf42`: payment-service v2 → **v1** (rollback, by forgesre-agent, human-approved in TrueForge session 01m3e8tws780bkbb926hzkbb1b)
- 07:12:05 — verification → **NOT_RECOVERED** (failed: active_version_ready, synthetic_success_ratio, checkout_error_rate, checkout_latency_p95_seconds, db_connection_utilization)
- 07:12:05 — risk assessed for rollback_deployment → HIGH
- 07:12:05 — rollback_deployment started (payment-service v2 → v1)
- 07:12:08 — rollback_deployment success in 2.6s
- 07:12:38 — verification → **RECOVERED** (failed: none)

## Evidence

1. See recorded tool results and sandbox output for this scripted test run.

### Sandbox diagnostic (TrueForge sandbox)

```json
{
  "window": {
    "start_unix": 1790405781,
    "end_unix": 1790406681,
    "step_seconds": 10,
    "rate_window": "30s"
  },
  "incident_detected": true,
  "incident_start_unix": 1790406631,
  "error_rate_before": 0.0,
  "error_rate_peak_after": 1.0,
  "error_rate_latest": 1.0,
  "latency_p95_before_s": 0.0541,
  "latency_p95_peak_after_s": 2.475,
  "failed_request_share_by_version": {
    "v2": 1.0
  },
  "suspect_version": "v2",
  "deployment_before_incident": {
    "deployment_id": "dep-5d5016c7",
    "service": "payment-service",
    "version": "v2",
    "previous_version": "v1",
    "seconds_before_incident_start": 12.0
  },
  "pool_utilization": {
    "v1": {
      "before": 0.0,
      "max_after": 0.0
    },
    "v2": {
      "before": 0.3529,
      "max_after": 1.0
    }
  },
  "db_connection_utilization": {
    "before": 0.0983,
    "max_after": 0.92
  },
  "dominant_error_by_instance": {
    "api-gateway": {
      "event": "checkout_failed",
      "count": 3372,
      "first_seen": "2026-09-26T06:56:21.020+00:00"
    },
    "payment-service-v2": {
      "event": "db_pool_timeout",
      "count": 565,
      "first_seen": "2026-09-26T07:10:28.715+00:00"
    }
  },
  "dominant_error": {
    "instance": "api-gateway",
    "event": "checkout_failed",
    "count": 3372
  },
  "dominant_backend_error": {
    "instance": "payment-service-v2",
    "event": "db_pool_timeout",
    "count": 565
  },
  "pool_vs_error_correlation": 0.635,
  "restart_effects": [],
  "checks": {
    "error_rate_step_change": true,
    "failures_concentrated_on_one_version": true,
    "suspect_deployed_shortly_before_start": true,
    "suspect_pool_saturated": true,
    "logs_errors_from_suspect_instance": true,
    "pool_tracks_error_rate": true
  },
  "evidence_score": 1.0,
  "affected_service": "payment-service"
}
```

## Root Cause

*Agent hypothesis — confidence: **MEDIUM***

Hypothesis under test: payment-service v2 exhausts its database connection pool.

## Actions Taken

1. **restart_service** — payment-service-v2
   - Reason: checkout failing, payment-service-v2 pool exhausted per logs and metrics; reversible restart first
   - Execution: success in 4.1s (action `act-37c6a344`)
   - Verified outcome: **NOT_RECOVERED** (failed criteria: active_version_ready, synthetic_success_ratio, checkout_error_rate, checkout_latency_p95_seconds, db_connection_utilization)
2. **rollback_deployment** — payment-service v2 → v1
   - Reason: restart did not recover: v2 carries all failures, deployed shortly before the spike, pool saturated
   - Approval: required and enforced by TrueForge (tool dispatched only after human approval)
   - Execution: success in 2.6s (action `act-2757f12b`)
   - Verified outcome: **RECOVERED** (failed criteria: none)

## Verification

| Signal | Before | After |
|---|---|---|
| Active payment-service version | v2 | v1 |
| Checkout error rate | 86.8% | 0.0% |
| Checkout p95 latency | 2.4712s | 0.0492s |
| PostgreSQL connections | 91 | 6 |
| PostgreSQL connection utilization | 91.0% | 6.0% |
| Active version pool utilization | 100.0% | 0.0% |
| Measured at | 2026-09-26T07:10:52 | 2026-09-26T07:12:38 |

Final verification criteria:

- ✅ `active_version_ready` observed True (needs == True)
- ✅ `synthetic_success_ratio` observed 1.0 (needs >= 0.95)
- ✅ `checkout_error_rate` observed 0.0 (needs <= 0.05)
- ✅ `checkout_latency_p95_seconds` observed 0.0492 (needs <= 1.0)
- ✅ `db_connection_utilization` observed 0.06 (needs <= 0.7)
- Synthetic checkout probes: 20/20 succeeded

## Human Decisions

- rollback_deployment approved

## Follow-ups

- Scripted test run — no follow-ups.

## Final Status

**RESOLVED**
