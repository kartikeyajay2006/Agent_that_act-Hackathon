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
| Full autonomous run with the configured real model | ✅ | `truefoundry/openai-polaris-gpt-4-1-mini` completed the approve scenario at **100/100**: generated sandbox code, approval pause, attested rollback, objective recovery and RESOLVED report. Re-run the evaluator after changing the prompt or model. |

## How it was verified

| Check | Result |
|---|---|
| `pytest` (units + safety) | 63 passed — parsing, thresholds, report numbers, TrueForge event parsing, injection, allowlists, rate limits, rollback validation, attestation refusal and replay |
| `pytest -m integration` (live stack) | 12 passed — reset, healthy baseline, incident, logs/metrics/DB evidence, correct container restarted, restart NOT recovered, real rollback, RECOVERED, reset again, MCP auth |
| `test_trueforge_lifecycle.py[allow]` | passed — TrueForge routed 12 tool calls, 2 sandbox executions (generated script + analyzer), evidence via bridge, NOT_RECOVERED → RECOVERED, 1 approval pause, attested rollback, report RESOLVED |
| `test_trueforge_lifecycle.py[deny]` | passed — same investigation, pause, Deny: rollback never reached the MCP server, v2 untouched, report UNRESOLVED |
| `test_trueforge_gate.py` with a real local model (qwen2.5 3B) | deny: 0 rollback calls reached the server, v2 kept; allow: rollback executed with attestation |
| `./scripts/eval.sh --scenario approve --runs 1` with configured model | 100/100 — real-model run reached the TrueForge approval gate, used sandbox diagnostics, recovered v1 and filed a RESOLVED report |

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

The hard part was not making an agent call a tool. It was making the seams between the saved agent, TrueForge's API,
the MCP transport, and the demo system agree—and keeping the safety boundary intact when they did not. These are the
build failures that changed the design.

### A 404 must not turn into “try the powerful agent”

The first read-only run failed with `Agent not found: forgesre-investigator`. The setup path and investigation path had
to agree on a persisted agent identity; silently trying the general-purpose agent would have hidden the setup defect
and widened the tool surface. The integration now fails closed: it checks that the named investigator exists and
that its saved MCP allowlist is exactly the observation set, with sandbox and skills disabled. The guard is exercised
in [`test_read_only_investigation.py`](../mcp-server/tests/test_read_only_investigation.py).

### “Reachable” is not the same as “permitted”

TrueForge rejected a loopback MCP URL under its outbound URL protections. We checked the installed server schema for a
same-machine transport instead of guessing: this TrueForge version exposed URL-backed MCP manifests, not stdio. A
Cloudflare tunnel then exposed a second boundary: the MCP SDK's localhost Host-header protection rejected the
tunnel's public host. The fix was not to disable that protection or bind the service publicly. The MCP server keeps
DNS-rebinding protection enabled, stays bound to loopback, retains bearer auth, and allows only the hostname derived
from its configured MCP URL in addition to localhost. Tests cover localhost, the configured host, and an unrelated
host in [`test_transport_security.py`](../mcp-server/tests/test_transport_security.py). For local setups that cannot
provide an approved reachable endpoint, setup reports the limitation rather than weakening the policy.

### An error turn is not a successful turn with missing output

The runner emitted `turn.created`, then failed while reading `.output` from a `TurnStateError`. That secondary Python
exception obscured the model/runtime failure. The stream handler now branches on TrueForge's actual terminal state,
surfaces the original error message, and only reads output from a completed turn. A regression fixture for the error
state lives in [`test_read_only_investigation.py`](../mcp-server/tests/test_read_only_investigation.py).

### The diagnosis needs evidence that survives the workload

An early connection-leak simulation did not leave useful database evidence because the pooled connections could be
reclaimed. The final v2 scenario instead holds connections in an open audit batch until the batch fills, producing
observable pool pressure and `idle in transaction` sessions. That is why the investigator gathers independent
signals—metrics, logs, database state, and deployment history—rather than inferring a cause from one alert. The second
latency scenario reuses that same observation surface; its configuration and rehearsal status are tracked separately
in [`config/incidents.yaml`](../config/incidents.yaml) and [`docs/demo.md`](demo.md).

### Small runtime mismatches can look like product failures

Several failures were caused by assumptions at library boundaries, not by the incident logic:

- The local sandbox could not clone the git-backed analyzer on the tested Fedora setup, and one sandbox-side GitHub
  fetch returned HTTP 502. The analyzer can instead be delivered by the MCP bridge, so the sandbox does not need
  direct GitHub access.
- The MCP SDK may parse JSON-looking tool arguments before calling the report handler. The handler accepts both text
  and structured values rather than relying on one wire representation.
- Prometheus returns an empty vector when there are no errors; treating “no series” as zero is necessary for a clean
  baseline to pass verification. The error-rate query explicitly supplies zero in that case.
- TrueForge records approval decisions as turn inputs. The attestation reads the session's turns, not only its event
  stream, so a direct MCP call cannot mistake an absent event for human approval.

The common lesson: inspect the installed API and the actual response shape at each boundary, then test the failure
path itself. Do not turn a failed connection, missing metric, or pending approval into a permissive default.
