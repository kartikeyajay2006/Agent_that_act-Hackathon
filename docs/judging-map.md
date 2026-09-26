# Judging map

Each official criterion → what the judges need to see → where it is in the code → how to show it live. For the honest
gap analysis and self-scores, see [winning-plan.md](winning-plan.md).

## 30 pts — The harness is doing the work

> "A judge has to watch TrueForge reach a real tool, run generated code in the sandbox, and hold for a person."

| Judges must see | How ForgeSRE does it | Where | Show it live |
|---|---|---|---|
| TrueForge reaches a **real tool** | 17 MCP tools act on a running Docker stack, Prometheus and PostgreSQL | `mcp-server/src/forgesre/server.py`, `ops.py`, `dockerctl.py` | TrueForge chat → Agent steps; `curl 127.0.0.1:18080/route` flips v2 → v1 |
| **Generated code** in the sandbox | Agent writes its own diagnostic script, runs it via `exec`, then cross-checks with the reference analyzer; evidence reaches the sandbox through the Code Mode bridge | `agent/forgesre.system.md` step 5, `skills/incident-diagnostics/` | Expand the `exec` steps in TrueForge; the script and its JSON are in the step |
| **Hold for a person** | `rollback_deployment` in `require_approval_for_tools` (and annotated destructive) | `trueforge.py::agent_manifest` | TrueForge "Tool Approval Required" panel with Allow / Deny |
| TrueForge is not decorative | No LLM client in the repo; TrueForge runs the loop, sandbox, approvals, sessions | `grep -ri "anthropic\|openai" mcp-server/src` → config strings only | Show the session trace and the agent spec |

Evidence: `tests/test_trueforge_lifecycle.py` (12 routed tool calls, 2 sandbox executions, 1 approval pause),
[showcase screenshots](assets/showcase/).

## 25 pts — It actually runs

> "Someone who has never seen the project should be able to clone it, follow the README, and get it going on their own laptop."

| What helps | Where |
|---|---|
| One command to bring everything up | `scripts/up.sh` |
| Tells you exactly what is missing and how to fix it | `scripts/doctor.sh` |
| Secrets generated for you; nothing to hand-edit except the model key | `scripts/setup.sh`, `.env.example` |
| Try it without a model key (you still click Allow/Deny) | `scripts/rehearse.sh` |
| Deterministic incident and reset | `scripts/reset-demo.sh`, `trigger-incident.sh`, `verify-*.sh` |
| Pinned TrueForge version | `scripts/start-trueforge.sh` (0.2.1) |
| Portable scripts (GNU + BSD tools), everything on 127.0.0.1, ports in `.env` | `scripts/`, `docker-compose.yml` |

Evidence: `pytest -m integration` runs reset → incident → rollback → reset twice (12 passed).
Gap: tested on one Fedora laptop only — see winning-plan task 3.

## 20 pts — Where it stops

| Action | Class | Who decides | Enforced by |
|---|---|---|---|
| Reads, probes, verification, report | GREEN | agent | — |
| `restart_service` | YELLOW | agent, if `assess_action_risk` shows no blockers | allowlist, 2 per 10 min per target, never `postgres` (`config/policy.yaml`) |
| `rollback_deployment` | RED | **a human** | TrueForge gate **and** server-side attestation of the exact Allow, single use |
| shell, raw Docker/PromQL/SQL, deletes, DB writes | never | nobody | not exposed at all |

Why the boundary sits there: a restart is reversible and changes no version or data; a rollback changes what code
serves every customer. The agent must write an approval brief (hypothesis, evidence, what it already tried and how that
verified, blast radius from `assess_action_risk`, recovery plan) before the call.

Evidence:
- Deny through TrueForge: rollback never reaches the MCP server, v2 untouched (`test_trueforge_lifecycle.py[deny]`, `test_trueforge_gate.py`)
- Direct call without approval: `APPROVAL_NOT_FOUND`; replayed approval: `APPROVAL_ALREADY_USED` (`test_safety.py`)
- Code Mode cannot call destructive tools (TrueForge `blockDestructiveToolsInCodeMode`)

## 15 pts — A job worth handing over

| Question | Answer |
|---|---|
| Would a real person delegate this? | Yes: first-response incident triage is repetitive, time-critical toil every on-call rotation does, often at night |
| What does the human keep? | The one irreversible decision, with an evidence brief instead of a guess |
| What does the human get back? | A recovered service, objective proof of recovery, and a written incident report |
| Is it interesting? | The agent has to notice that its own successful action did not fix the problem and change strategy — closed-loop control, not command execution |

## 10 pts — Demo clarity

Five-minute run of show with lines to say: [demo.md](demo.md). The [walkthrough GIF](assets/showcase/walkthrough.gif)
is the storyboard. Must show: the job (checkout down), the agent doing it (tool calls + sandbox), where the harness fits
(the Allow/Deny panel), the outcome (recovered + report).

## Required submissions

| Requirement | Status |
|---|---|
| Public repository | ✅ github.com/kartikeyajay2006/Agent_that_act-Hackathon |
| README that works on someone else's laptop | 🟡 written for it; needs a fresh-laptop test |
| Code running on TrueForge (real tool, sandbox, approval) | ✅ |
| Disclosure of AI assistants used | ✅ README → "AI assistance" |
| Demo video showing the approval moment | ❌ to record |

## TrueForge capabilities used

| Capability | How ForgeSRE uses it |
|---|---|
| Agent execution loop | The whole incident lifecycle |
| Remote MCP connector with header auth | Every production read and action |
| Tool annotations + `require_approval_for_tools` | YELLOW runs; RED pauses for a human |
| Native approval UI (`user.tool_approval`) | The rollback decision — read back by the server for attestation |
| Sandbox as a tool + Code Mode | Agent-written diagnostics; evidence via harness-bridged `mcp_client`; no credentials in the sandbox |
| Skills (git-backed) | `incident-diagnostics` (attached where the sandbox can clone it; otherwise delivered via the MCP bridge) |
| Sessions / events / turns API | Trace, dashboard approval banner, attestation, terminal driver, tests |
| Local sandbox / Daytona | Local SRT sandbox by default; Daytona with `DAYTONA_API_KEY` |
