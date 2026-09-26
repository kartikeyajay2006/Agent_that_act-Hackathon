# Demo runbook (2–3 minutes on stage)

## Before you go on

```bash
./scripts/start-trueforge.sh      # TrueForge UI on http://localhost:8790
./scripts/reset-demo.sh           # clean baseline, waits until healthy (~60 s)
./scripts/setup-trueforge.sh      # only once per machine / after changing the model
```

Open three windows:

1. **TrueForge** — http://localhost:8790 → Agents → `forgesre` → Try (new chat, do not type yet)
2. **Mission control** — http://127.0.0.1:18900/dashboard
3. **Terminal** in the repo root

## Script

| Time | Do | Say |
|---|---|---|
| 0:00 | Show mission control: all green, v1 live, 0% errors | "A small but real production stack: gateway, payment service, Postgres, Prometheus, live traffic." |
| 0:10 | `./scripts/trigger-incident.sh` | "The release pipeline just shipped payment-service v2." |
| 0:20 | Dashboard turns red: errors → 100%, DB connections → 92% | "Checkout is failing for every customer. Nobody has told the agent why." |
| 0:25 | In TrueForge, send: *Checkout is failing in production. Investigate and recover the service.* | "From here TrueForge runs the loop. Our code only provides tools and guard rails." |
| 0:30 | Point at the TrueForge tool calls: incident context, health, metrics, logs, deployments, database | "Each of these is a real MCP call against the running system." |
| 0:55 | Sandbox step: the diagnostic runs in the TrueForge sandbox | "It pulls the evidence through the harness into the sandbox and computes: failures 100% on v2, v2 deployed ~20 s before the spike, pool saturated, 6 of 6 checks agree. No credentials in the sandbox." |
| 1:10 | Agent states the hypothesis with evidence and confidence | |
| 1:20 | `restart_service payment-service-v2` runs on its own | "Restart is a YELLOW action — reversible, so policy lets the agent do it without asking." |
| 1:30 | `verify_recovery` → **NOT_RECOVERED**, loop counter goes to 2 on the dashboard | "Tool success is not incident success. The restart worked; the incident didn't go away. So it goes back to investigating." |
| 1:40 | `assess_action_risk` then `rollback_deployment` → TrueForge shows **approval required**; dashboard shows the blue banner | "The model can recommend a production rollback. It cannot authorize one. TrueForge is holding the call — our server hasn't even seen it." |
| 1:50 | Click **Allow** | "And even now, the MCP server checks TrueForge's session record for this exact approval before it touches anything." |
| 2:00 | Dashboard: gateway routes to v1, v2 stopped | |
| 2:10 | `verify_recovery` → **RECOVERED**: 20/20 synthetic, errors 0%, DB ~7% | "Recovery is declared by thresholds in config, not by the model's opinion." |
| 2:20 | Open `artifacts/incidents/INC-….md` | "Every number in this report comes from recorded tool results." |

Closing line:

> Monitoring tells you production broke. ForgeSRE investigates why, proves it, acts safely, and verifies that
> production actually recovered.

## Denial variant (30 s, if asked)

`./scripts/reset-demo.sh && ./scripts/trigger-incident.sh`, same prompt, click **Deny**. Show: route still v2,
`grep rollback artifacts/audit/events.jsonl` is empty, the agent acknowledges and lists safe next steps.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Healthy check never passes after reset | `docker compose ps`; re-run `./scripts/reset-demo.sh` |
| TrueForge says the MCP server is unreachable | `./scripts/restart-mcp.sh`; check `OUTBOUND_URL_ALLOWED_HOSTS` was set by `start-trueforge.sh` |
| Rollback returns `APPROVAL_NOT_FOUND` | the call was not approved in TrueForge (or TrueForge was restarted mid-run); approve it in the chat |
| Agent is slow | pick a faster model in `.env` and re-run `./scripts/setup-trueforge.sh` |
