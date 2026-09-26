# Judging map

Where each claim is implemented and how to check it yourself.

| Claim | Where | How to verify |
|---|---|---|
| TrueForge runs the agent loop | No LLM SDK in this repo; agent defined via TrueForge API in `mcp-server/src/forgesre/trueforge.py` | `grep -ri "anthropic\|openai" mcp-server/src` finds only config strings; watch the session in the TrueForge UI |
| Real actions on real systems | `ops.restart` (Docker Engine API), `ops.rollback` (gateway route file + container stop) | `curl 127.0.0.1:18080/route` before/after; `docker ps` |
| Real incident, not fixtures | `demo/payment-v2/app.py` batch-commit audit transactions vs pool size | `docker exec forgesre-postgres psql -U payments -c "select application_name,state,count(*) from pg_stat_activity group by 1,2"` during the incident |
| Evidence from 3+ classes | Prometheus signals, logs, pg_stat_activity, deployment history, sandbox analysis | TrueForge session trace |
| Sandbox does meaningful work | `skills/incident-diagnostics/scripts/diagnose.py` run via Code Mode; evidence fetched by harness-bridged `mcp_client` | `sandbox.created` event in the session; output quoted in the report |
| No hardcoded conclusion | Analyzer derives the suspect from `checkout_errors_by_reason` shares; report numbers rendered from audit log | Read `diagnose.py`; run it against a saved bundle |
| Safe action autonomous | `restart_service` annotated write (not destructive); not in `require_approval_for_tools` | Runs without a pause in the session |
| High-impact action gated by TrueForge | Agent spec: `require_approval_for_tools: ["rollback_deployment", "@destructive"]`; tool annotated `destructiveHint: true` | `tests/test_trueforge_gate.py` (deny: server never called; approve: executes) |
| Gate holds even outside TrueForge | `Ops._attest_approval` looks up the Allow in TrueForge turn inputs, matches args, single-use | `tests/test_safety.py::test_rollback_refused_without_trueforge_approval`, `::test_single_approval_cannot_be_replayed` |
| Closed loop: action ≠ success | `verify_recovery` with thresholds from `config/verification.yaml` | Restart → NOT_RECOVERED in the timeline; dashboard loop counter |
| Blast radius computed, not invented | `Ops.assess_risk` from live request rate, dependents, target readiness, pool connections held | Call `assess_action_risk` twice in different states |
| Server-side safety | Name/version regex, catalog lookups, allowlists, rate limit, no shell tool, bearer auth, localhost binding | `tests/test_safety.py` (30 cases) |
| Incident report traceable | `report.py` renders timeline/actions/before-after from `artifacts/audit/events.jsonl` | Compare report numbers to the JSON sidecar |
| Deterministic demo | `reset-demo.sh`, `trigger-incident.sh`, `verify-*.sh` | `pytest -m integration` runs the whole cycle twice |

## TrueForge capabilities used

| Capability | How ForgeSRE uses it |
|---|---|
| Agent execution loop | Entire incident lifecycle |
| Remote MCP connector (header auth) | All production reads and actions |
| Tool annotations + `require_approval_for_tools` | YELLOW runs, RED pauses for a human |
| Native approval UI / `user.tool_approval` | The rollback decision; also read back for attestation |
| Sandbox as a tool + Code Mode | Diagnostic analysis with bridged MCP calls, no credentials in the sandbox |
| Skills (git-backed) | `incident-diagnostics` loaded into the sandbox on demand |
| Sessions, events, turns API | Trace, dashboard banner, approval attestation, headless driver |
| Local sandbox / Daytona | Local SRT sandbox in standalone mode; Daytona if `DAYTONA_API_KEY` is set |
