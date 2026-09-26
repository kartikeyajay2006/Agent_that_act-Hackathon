# Three-minute demo script

This is a **2:55** recording plan for the required demo. It keeps TrueForge on screen for more than a minute and
shows the approval pause before the irreversible rollback. Keep three windows ready: TrueForge, Mission Control and
a terminal. Do not show `.env`, browser profiles, or any keys.

## Before recording

```bash
./scripts/demo-ready.sh
```

Then open:

- TrueForge: `http://localhost:8790` → **Agents** → **forgesre** → **Try**
- Mission Control: `http://127.0.0.1:18900/dashboard`

## Shot list and narration

| Time | Show | Say |
|---:|---|---|
| 0:00–0:12 | Healthy Mission Control: v1, green state, 0 % errors. Overlay: **ForgeSRE — production incidents, safely resolved.** | “What if an SRE agent could investigate and recover a production outage, but knew exactly when to stop and ask a human?” |
| 0:12–0:25 | Terminal: `./scripts/trigger-incident.sh`; cut to the dashboard turning red. | “I have deployed payment-service v2. Checkout failures, latency and database connection usage climb within seconds.” |
| 0:25–0:42 | Dashboard: three alerts, about 100 % errors, p95 about 2.5 seconds, database around 92 %. | “This is a live Docker production simulation: gateway, payment service, PostgreSQL, Prometheus and real traffic. The agent has not been told the cause.” |
| 0:42–0:52 | In TrueForge, paste: `Checkout is failing in production. Investigate and recover the service.` | “TrueForge runs the agent loop. ForgeSRE contributes the narrow MCP tools, deterministic policy and evidence.” |
| 0:52–1:25 | **TrueForge stays on screen.** Show tool calls for incident context, health, metrics, logs, deploy history and database health. Expand one response. | “Every step is a real MCP call against the running system. This is not a chat response pretending to be an action.” |
| 1:25–1:45 | **Still in TrueForge.** Expand its `exec` sandbox call and diagnostic output, highlighting v2, pool saturation and `db_pool_timeout`. | “The agent writes and runs Python in the TrueForge sandbox, then cross-checks its findings. Evidence reaches the sandbox through the harness, without production credentials in the code environment.” |
| 1:45–1:57 | `assess_action_risk`, then `restart_service`. | “It begins with the lowest-risk reversible action: a Yellow restart, allowed only after a risk check.” |
| 1:57–2:10 | `verify_recovery` returns `NOT_RECOVERED`; dashboard remains red. Use a labelled cut, ‘30 seconds later’, during the verification wait. | “The restart command succeeded, but the incident did not. Tool success is not incident success, so the agent changes strategy.” |
| 2:10–2:35 | **TrueForge Tool Approval Required panel.** Keep the exact `rollback_deployment` v2 → v1 request visible for at least three seconds. | “The evidence supports a rollback. But changing the version that serves customers is a Red action. TrueForge pauses the exact call for a human; the MCP server has not received it yet.” |
| 2:35–2:43 | Click **Allow** in TrueForge. | “After I allow this exact call, ForgeSRE attests the TrueForge approval record before changing production.” |
| 2:43–2:53 | Mission Control flips to v1, then shows 0 % errors and normal DB usage. | “Traffic atomically returns to v1 and the faulty v2 is retired.” |
| 2:53–2:55 | `verify_recovery: RECOVERED` plus incident report path. | “Recovery is proven by readiness, synthetic checkouts, error rate, latency and database thresholds—not the model’s opinion.” |

## Closing line

> “Monitoring tells us production broke. ForgeSRE finds out why, proves it in TrueForge, acts safely, and shows that production actually recovered.”

For a 20-second optional safety clip, repeat the run, click **Deny** in TrueForge, and show that v2 stays active and
no `rollback_deployment` call reaches the ForgeSRE audit log.
