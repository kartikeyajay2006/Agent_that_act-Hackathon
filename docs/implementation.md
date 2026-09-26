# Problem statement and what is implemented

## The problem

A payments service starts failing at 2 a.m. The on-call engineer does the same things every time:

1. read the alert, check dashboards, pull logs, look at recent deploys, check the database;
2. form a theory and try to prove it;
3. try the cheapest reversible fix;
4. check whether it actually worked — often it didn't;
5. escalate to the risky fix (a production rollback), which somebody has to sign off;
6. confirm recovery with numbers, and write it all up.

Steps 1–4 and 6 are mechanical, evidence-driven and slow at 2 a.m. Step 5 is a judgement call with real blast
radius. That split is exactly what an agent harness is for: let the agent do the mechanical work end to end, and make
the risky step impossible without a human.

**Why an agent, not a runbook script?** A script can restart a service. It cannot notice that the restart "worked" but
the incident did not go away, go back to the evidence, and change strategy. That closed loop — act, measure, decide
again — is the job.

**Why people would hand this over:** it is the most repetitive, time-critical part of on-call, the evidence is
machine-readable, and the one decision that matters (roll back production?) stays with a person, backed by an
evidence brief instead of a 2 a.m. guess.

## Goals and non-goals

| Goals | Non-goals |
|---|---|
| A real system that really breaks, deterministically | Kubernetes, multi-cluster, cloud accounts |
| TrueForge runs the loop; no hidden orchestration of our own | Our own agent framework or LLM client |
| Evidence from several independent classes | A chatbot that explains commands |
| Diagnosis computed by code in the sandbox | Numbers typed by the model |
| Safe action autonomous, risky action human-gated | Unrestricted shell or Docker for the model |
| Objective recovery verification | "Looks fixed" |
| Reproducible on a laptop in minutes | Five incident types |

## What is implemented

Status legend: ✅ built and verified by a test or a recorded run · 🟡 built, verified only partially (see note)

### Production environment (`demo/`, `docker-compose.yml`)

| Item | Status | Detail |
|---|---|---|
| api-gateway | ✅ | `/checkout` routed to the active payment version from `state/deployment.json`; JSON logs; request/error/latency metrics |
| payment-service v1 | ✅ | bounded psycopg pool (10), writes to PostgreSQL |
| payment-service v2 with a genuine defect | ✅ | batched audit transactions (batch 100) larger than the pool (85) strand connections `idle in transaction` |
| PostgreSQL + least-privilege monitor role | ✅ | `forgesre_monitor` has `pg_monitor` and reserved connections |
| Prometheus, exporter, alert rules | ✅ | 2 s scrape; CheckoutErrorRateHigh, CheckoutLatencyHigh, DatabaseConnectionsHigh |
| Traffic generator | ✅ | open-loop 10 rps |
| reset / trigger / verify scripts | ✅ | reset ~60 s to a verified healthy baseline; incident observable ~15–30 s after trigger |

### MCP server (`mcp-server/src/forgesre/`)

| Item | Status | Detail |
|---|---|---|
| 17 tools over streamable HTTP | ✅ | official `mcp` SDK v2; honest annotations (`readOnlyHint`, `destructiveHint`) |
| Observation tools | ✅ | incident context, health, bounded logs, 14 named Prometheus signals, pg_stat_activity, deployments, evidence bundle, timeline |
| Deterministic risk assessment | ✅ | live request rate, dependents, target readiness, connections released, expected disruption, blockers |
| `restart_service` (YELLOW) | ✅ | allowlist, 2 per 10 min per target, waits for liveness, reminds the agent to verify |
| `rollback_deployment` (RED) | ✅ | validation, TrueForge approval attestation (single use), target readiness, atomic route switch, gateway confirmation, stops previous version |
| `verify_recovery` | ✅ | waits for post-action window; readiness, 20 synthetic probes, error rate, p95, DB utilization vs config |
| Incident report | ✅ | narrative from the agent; timeline/actions/before-after rendered from the audit log |
| Audit log + incident state | ✅ | every tool call, action and verification |
| Bearer auth, input validation, redaction | ✅ | see `tests/test_safety.py` |
| Mission control dashboard | ✅ | health, signals, control loop with iteration counter, approval banner from TrueForge |

### TrueForge integration (`agent/`, `trueforge.py`, `skills/`)

| Item | Status | Detail |
|---|---|---|
| One-command TrueForge setup | ✅ | model provider (properties from TrueForge's catalog), MCP connector with header auth, optional Daytona, skill, agent spec |
| Native approval gate | ✅ | `require_approval_for_tools: ["rollback_deployment", "@destructive"]` |
| Deny never reaches the server | ✅ | verified with a real local model and with the contract test |
| Allow executes with attestation | ✅ | server reads the Allow from TrueForge turns and records session/tool-call/turn/time |
| Sandbox diagnosis with generated code | ✅ | agent writes its own script, then cross-checks with the reference analyzer; evidence via Code Mode bridge |
| Skill attached where it can install | ✅ | auto-detected; elsewhere the analyzer is delivered into the sandbox through the MCP bridge (no internet needed) |
| Full autonomous run with a frontier model | 🟡 | **not yet run** — no model API key was available while building. Every mechanism the model depends on is verified (below). Do a rehearsal with your key before demoing. |

## How it was verified

| Check | Result |
|---|---|
| `pytest` (units + safety) | 63 passed — parsing, thresholds, report numbers, TrueForge event parsing, injection, allowlists, rate limits, rollback validation, attestation refusal and replay |
| `pytest -m integration` (live stack) | 12 passed — reset, healthy baseline, incident, logs/metrics/DB evidence, correct container restarted, restart NOT recovered, real rollback, RECOVERED, reset again, MCP auth |
| `test_trueforge_lifecycle.py[allow]` | passed — TrueForge routed 12 tool calls, 2 sandbox executions (generated script + analyzer), evidence via bridge, NOT_RECOVERED → RECOVERED, 1 approval pause, attested rollback, report RESOLVED |
| `test_trueforge_lifecycle.py[deny]` | passed — same investigation, pause, Deny: rollback never reached the MCP server, v2 untouched, report UNRESOLVED |
| `test_trueforge_gate.py` with a real local model (qwen2.5 3B) | deny: 0 rollback calls reached the server, v2 kept; allow: rollback executed with attestation |

The lifecycle tests use a **scripted test-double model** (`mcp-server/tests/scripted_model.py`) so the result depends
on TrueForge and ForgeSRE, not on a model's reasoning. It is never used as the agent in a demo.

### What a recorded run produced

From `artifacts/incidents/example-contract-test-approved.md` (numbers rendered from the audit log):

| Signal | At detection | After rollback |
|---|---|---|
| Serving version | v2 | v1 |
| Checkout error rate | 83–100 % | 0.0 % |
| Checkout p95 | 2.47 s | ~50 ms |
| PostgreSQL connections | 91 (91 %) | 6–7 (6–7 %) |
| Synthetic probes | 0/20 | 20/20 |

Sandbox analyzer output on the same run: failures 100 % on v2, v2 deployed 12–22 s before the incident start, v2 pool
saturated at 1.0, dominant backend error `db_pool_timeout`, evidence score 0.83–1.0 depending on how much baseline
history is in the window.

## Problems found while building (and what we did)

| Problem | Impact | Fix |
|---|---|---|
| Leaked pool connections were garbage-collected, so the DB showed nothing | no database evidence | v2 keeps them in an open batch — a realistic defect that holds DB sessions |
| TrueForge blocks loopback MCP endpoints and 0.2.1 has no stdio transport | local MCP cannot be attached safely | detect transports from installed OpenAPI; refuse loopback and require an approved non-loopback endpoint |
| MCP SDK v2 renamed FastMCP → `MCPServer` | outdated examples break | written against the installed v2 API |
| TrueForge stores approvals as turn inputs, not events | attestation missed them | attestation reads `/sessions/{id}/turns` |
| Local sandbox cannot run git HTTPS on Fedora | skill install failed and broke the sandbox | detect; deliver the analyzer through the MCP bridge instead |
| Fetching the analyzer from GitHub inside the sandbox returned HTTP 502 once | flaky, needs venue internet | `get_reference_analyzer` tool, fetched with `mcp-client` from inside the sandbox |
| MCP SDK pre-parses JSON-looking strings | report rejected sandbox JSON | tool accepts text or object |
| Error-rate PromQL returned empty (not 0) with no errors | healthy check never passed | `or vector(0)` |
