---
name: incident-diagnostics
description: Evidence-driven diagnosis of a production incident in the sandbox. Use after the first round of health/metrics/logs/deployment checks, before choosing a remediation, and again when a remediation did not work.
---

# Incident diagnostics (sandbox)

Goal: turn the raw evidence into numbers you can defend. The analysis runs in the TrueForge sandbox; the evidence is
fetched through the harness-bridged `mcp_client`, so the sandbox never needs production credentials.

## Run it

Use the sandbox `exec` tool (Code Mode). The skills directory is named in your sandbox instructions — `skills/`
in TrueForge's local sandbox, `/opt/tfy/skills/` on Daytona:

```bash
python <skills dir>/incident-diagnostics/scripts/diagnose.py --window 15
```

The script calls `forgesre / collect_incident_evidence` itself and prints one JSON object:

| Field | Meaning |
|---|---|
| `incident_start_unix` | first sample where checkout error rate crossed 5% |
| `error_rate_before` / `error_rate_peak_after` | baseline vs worst error rate |
| `failed_request_share_by_version`, `suspect_version` | which upstream version the failing requests went to |
| `deployment_before_incident` | last deployment before the start and how many seconds earlier |
| `pool_utilization`, `db_connection_utilization` | resource saturation before vs after |
| `dominant_error_by_instance`, `dominant_error` | most frequent ERROR event per instance (from logs) |
| `pool_vs_error_correlation` | Pearson r between the suspect's pool usage and the error rate |
| `restart_effects` | whether any earlier restart actually brought the error rate down |
| `checks`, `evidence_score` | six independent checks and the fraction that agree |

If you want another angle, write your own script next to it — for example to align log buckets with the error
series. Do not type numbers into a script; compute them from the evidence.

## Read the result

- `evidence_score` ≥ 0.8 with a named `suspect_version` and a deployment a few minutes before the start: a strong
  hypothesis that the release is the cause. State it as a hypothesis with confidence HIGH.
- Saturation (`pool_utilization.max_after` ≈ 1.0, `db_connection_utilization` high) plus pool-timeout log events points
  to resource exhaustion inside that version, not to the database itself.
- `restart_effects[].resolved == false` means the fault returns after a clean process start — a code or config
  defect in the running version, which a restart cannot fix. Rolling back to the previous version is the evidence-backed
  next step, and it goes through the TrueForge approval gate.
- If fewer than half the checks agree, do not remediate yet: collect more evidence.

Quote the JSON (or its key lines) in your approval brief and pass it as `sandbox_analysis` to
`generate_incident_report`.
