<!-- Example output of mcp-server/tests/test_trueforge_lifecycle.py[deny]: the human denied the rollback in TrueForge. -->

# Incident Report — INC-20260926-071733

**Status:** UNRESOLVED
**Environment:** demo-production
**Opened:** 2026-09-26T07:17:33.624+00:00
**Report generated:** 2026-09-26T07:18:29.268+00:00
**Handled by:** ForgeSRE agent running on TrueForge

## Summary

Scripted harness contract test of the ForgeSRE incident lifecycle (test double model).

## Detection

- `CheckoutErrorRateHigh` (page) firing since 2026-09-26T07:17:20.997448896Z — More than 5% of checkout requests are failing (value at detection: 0.4518)
- `CheckoutLatencyHigh` (page) firing since 2026-09-26T07:17:20.997448896Z — Checkout p95 latency above 1s (value at detection: 2.4447)
- `DatabaseConnectionsHigh` (warning) firing since 2026-09-26T07:17:17.079660426Z — PostgreSQL connection usage above 80% of max_connections (value at detection: 0.91)

## Impact

- Checkout error rate at detection: **52.6%** of ~656 requests/minute
- Checkout p95 latency at detection: **2.4525s**
- PostgreSQL connection utilization at detection: **91.0%**

## Timeline (UTC)

- 07:16:11 — deployment `dep-6c4e05bd`: payment-service ∅ → **v1** (deploy, by release-pipeline)
- 07:17:09 — deployment `dep-5ab1c8a7`: payment-service v1 → **v2** (deploy, by release-pipeline)
- 07:17:33 — incident opened: Production alert: CheckoutErrorRateHigh, CheckoutLatencyHigh, DatabaseConnectionsHigh
- 07:17:45 — evidence bundle collected (15 min window)
- 07:17:45 — risk assessed for restart_service → MEDIUM
- 07:17:45 — restart_service started (payment-service-v2)
- 07:17:49 — restart_service success in 4.1s
- 07:18:28 — verification → **NOT_RECOVERED** (failed: active_version_ready, synthetic_success_ratio, checkout_error_rate, checkout_latency_p95_seconds, db_connection_utilization)
- 07:18:28 — risk assessed for rollback_deployment → HIGH

## Evidence

1. See recorded tool results and sandbox output for this scripted test run.

### Sandbox diagnostic (TrueForge sandbox)

```json
{
  "window": {
    "start_unix": 1790406165,
    "end_unix": 1790407065,
    "step_seconds": 10,
    "rate_window": "30s"
  },
  "incident_detected": true,
  "incident_start_unix": 1790407045,
  "error_rate_before": 0.0,
  "error_rate_peak_after": 1.0,
  "error_rate_latest": 1.0,
  "latency_p95_before_s": 0.0542,
  "latency_p95_peak_after_s": 2.475,
  "failed_request_share_by_version": {
    "v2": 1.0
  },
  "suspect_version": "v2",
  "deployment_before_incident": {
    "deployment_id": "dep-5ab1c8a7",
    "service": "payment-service",
    "version": "v2",
    "previous_version": "v1",
    "seconds_before_incident_start": 16.0
  },
  "pool_utilization": {
    "v1": {
      "before": 0.0,
      "max_after": 0.0
    },
    "v2": {
      "before": 0.6706,
      "max_after": 1.0
    }
  },
  "db_connection_utilization": {
    "before": 0.1433,
    "max_after": 0.91
  },
  "dominant_error_by_instance": {
    "api-gateway": {
      "event": "checkout_failed",
      "count": 3425,
      "first_seen": "2026-09-26T07:03:02.618+00:00"
    },
    "payment-service-v2": {
      "event": "db_pool_timeout",
      "count": 291,
      "first_seen": "2026-09-26T07:17:18.315+00:00"
    }
  },
  "dominant_error": {
    "instance": "api-gateway",
    "event": "checkout_failed",
    "count": 3425
  },
  "dominant_backend_error": {
    "instance": "payment-service-v2",
    "event": "db_pool_timeout",
    "count": 291
  },
  "pool_vs_error_correlation": null,
  "restart_effects": [],
  "checks": {
    "error_rate_step_change": true,
    "failures_concentrated_on_one_version": true,
    "suspect_deployed_shortly_before_start": true,
    "suspect_pool_saturated": true,
    "logs_errors_from_suspect_instance": true,
    "pool_tracks_error_rate": false
  },
  "evidence_score": 0.83,
  "affected_service": "payment-service"
}
```

## Root Cause

*Agent hypothesis — confidence: **MEDIUM***

Hypothesis under test: payment-service v2 exhausts its database connection pool.

## Actions Taken

1. **restart_service** — payment-service-v2
   - Reason: checkout failing, payment-service-v2 pool exhausted per logs and metrics; reversible restart first
   - Execution: success in 4.1s (action `act-5b172c7d`)
   - Verified outcome: **NOT_RECOVERED** (failed criteria: active_version_ready, synthetic_success_ratio, checkout_error_rate, checkout_latency_p95_seconds, db_connection_utilization)

## Verification

| Signal | Before | After |
|---|---|---|
| Active payment-service version | v2 | v2 |
| Checkout error rate | 52.6% | 100.0% |
| Checkout p95 latency | 2.4525s | 2.475s |
| PostgreSQL connections | 91 | 91 |
| PostgreSQL connection utilization | 91.0% | 91.0% |
| Active version pool utilization | 100.0% | 100.0% |
| Measured at | 2026-09-26T07:17:33 | 2026-09-26T07:18:28 |

Final verification criteria:

- ❌ `active_version_ready` observed False (needs == True)
- ❌ `synthetic_success_ratio` observed 0.0 (needs >= 0.95)
- ❌ `checkout_error_rate` observed 1.0 (needs <= 0.05)
- ❌ `checkout_latency_p95_seconds` observed 2.475 (needs <= 1.0)
- ❌ `db_connection_utilization` observed 0.91 (needs <= 0.7)
- Synthetic checkout probes: 0/20 succeeded

## Human Decisions

- rollback_deployment denied in TrueForge

## Follow-ups

- Scripted test run — no follow-ups.

## Final Status

**UNRESOLVED**
