# Demo runbook (five minutes)

The rubric gives five minutes "to show the job, the agent doing it, and where the harness fits". The approval moment
must be on camera.

## Before you start

```bash
./scripts/up.sh          # stack + MCP server + TrueForge + agent, ends at a healthy baseline
./scripts/doctor.sh      # everything green?
```

Three windows side by side:

1. **TrueForge** — http://localhost:8790 → Agents → `forgesre` → **Try** (fresh chat, don't type yet)
2. **Mission control** — http://127.0.0.1:18900/dashboard
3. **Terminal** in the repo root, large font

Do not show `.env` or any API key on screen.

## Run of show

| Time | Do | Say |
|---|---|---|
| 0:00 | Mission control: all green, v1 live, 0 % errors | "The job: first response to a production incident. This is a small but real stack — gateway, payment service, Postgres, Prometheus, live traffic." |
| 0:20 | `./scripts/trigger-incident.sh` | "The release pipeline just shipped payment-service v2." |
| 0:35 | Dashboard goes red: errors ~100 %, DB connections ~91 %, three alerts | "Every checkout is failing. Nobody has told the agent why." |
| 0:45 | TrueForge: *Checkout is failing in production. Investigate and recover the service.* | "From here, TrueForge runs the loop. Our code is only tools and guard rails." |
| 1:00 | Agent steps: incident context, health, metrics, logs, deployments, database | "Each step is a real MCP call against the running system." |
| 1:40 | Expand the two `exec` steps | "It wrote its own diagnostic and ran it in the TrueForge sandbox. The evidence came in through the harness — no credentials in the sandbox. Then it cross-checked with our analyzer: failures 100 % on v2, deployed seconds before the spike, pool saturated." |
| 2:20 | `restart_service` runs without asking | "Restart is YELLOW: reversible, so policy lets the agent do it alone." |
| 2:40 | `verify_recovery` → NOT_RECOVERED; dashboard shows **Loop 2** | "The restart succeeded. The incident didn't go away. Tool success is not incident success, so it goes back to the evidence." |
| 3:10 | Approval brief in chat, then TrueForge **Tool Approval Required** — dashboard shows the blue banner | "A rollback changes what code serves every customer. The model can recommend it; it cannot authorise it. TrueForge is holding the call — our server hasn't even seen it." |
| 3:40 | Click **Allow** | "And before touching anything, our server checks TrueForge's own record that a human allowed this exact call." |
| 3:50 | Dashboard: route → v1, v2 retired | |
| 4:10 | `verify_recovery` → RECOVERED: 20/20 probes, 0 % errors, DB ~7 % | "Recovered is a verdict from thresholds in config, not the model's opinion." |
| 4:30 | Open `artifacts/incidents/INC-….md` | "Every number in the report comes from recorded tool results." |
| 4:45 | Close | "Monitoring tells you production broke. ForgeSRE finds out why, proves it, acts safely, and shows that production actually recovered." |

If there is time, or as a separate 20-second clip: reset, trigger, same prompt, click **Deny** — route stays v2,
`grep rollback_deployment artifacts/audit/events.jsonl` is empty, the agent lists safe next steps.

## Recording checklist

- [ ] Rehearsed three times with the real model; typical run length noted
- [ ] Browser zoom so the TrueForge steps are readable at 1080p
- [ ] The **Allow / Deny** panel is fully visible for at least three seconds
- [ ] Dashboard visible when the route flips to v1
- [ ] No `.env`, keys or personal tabs on screen
- [ ] Under five minutes; cut the waiting during `verify_recovery` if needed (say that you cut it)
- [ ] Link added to the README

## No model key? Rehearse the flow anyway

```bash
./scripts/rehearse.sh
```

A scripted stand-in model drives the saved agent; TrueForge, the tools, the sandbox and the approval gate are all
real, and **you** click Allow or Deny in the TrueForge UI. Use it to practise timing — never present it as the agent.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Anything unclear | `./scripts/doctor.sh` — every failed check prints its fix |
| Healthy check never passes after reset | `docker compose ps`; re-run `./scripts/reset-demo.sh` |
| TrueForge can't reach the MCP server | `./scripts/restart-mcp.sh`; start TrueForge via `start-trueforge.sh` (sets the loopback allowlist) |
| Rollback returns `APPROVAL_NOT_FOUND` | the call wasn't approved in TrueForge, or TrueForge restarted mid-run — approve it in the chat |
| Agent slow | faster model in `.env`, then `./scripts/setup-trueforge.sh`; keep the recorded video as backup |
