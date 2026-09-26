# Repository map

Every file, what it does, and where to look when you want to change something. ~8k lines in total.

## Tree

```text
.
├── README.md                     product overview, showcase, quickstart
├── docker-compose.yml            the demo production stack (all ports on 127.0.0.1)
├── .env.example                  every setting, with comments (copy → .env)
├── ruff.toml                     repo-wide lint/format settings
│
├── agent/                        what TrueForge runs
│   ├── forgesre.system.md        the agent's instructions (closed loop, GREEN/YELLOW/RED, approval brief)
│   ├── setup.md                  what setup-trueforge.sh configures, and how to do it by hand
│   └── demo-prompts.md           prompts for the demo and follow-ups
│
├── config/                       behaviour lives here, not in code
│   ├── services.yaml             service catalog: containers, URLs, versions, dependencies
│   ├── policy.yaml               action classes, restart allowlist/budget, rollback rules, attestation switch
│   └── verification.yaml         recovery thresholds, settle time, probe count, log/evidence bounds
│
├── demo/                         the "production" system the agent operates
│   ├── Dockerfile                one image recipe for all services (SERVICE_DIR build arg)
│   ├── requirements.txt
│   ├── common/obs.py             JSON-lines logger shared by services
│   ├── gateway/app.py            /checkout → active payment version (reads state/deployment.json)
│   ├── payment-v1/app.py         known-good release
│   ├── payment-v2/app.py         faulty release (batched audit transactions exhaust the pool)
│   ├── traffic-generator/app.py  open-loop 10 rps checkout load
│   ├── database/init.sh          schema + forgesre_monitor role (pg_monitor, reserved connections)
│   └── prometheus/               scrape config + symptom alert rules
│
├── mcp-server/                   the ForgeSRE MCP server (Python package `forgesre`)
│   ├── pyproject.toml            deps: mcp (v2), httpx, docker, psycopg, pyyaml, trueforge-sdk
│   ├── src/forgesre/
│   │   ├── server.py             17 MCP tool definitions + annotations, Bearer auth, /healthz
│   │   ├── ops.py                every tool's logic: validation, policy, actions, verification, attestation
│   │   ├── catalog.py            name/version validation and lookups against services.yaml
│   │   ├── dockerctl.py          narrow Docker Engine operations on catalogued containers
│   │   ├── prom.py               14 named Prometheus signals (no raw PromQL from the model)
│   │   ├── logs.py               parse, filter, redact, bound, bucket container logs
│   │   ├── database.py           pg_stat_activity summary via the monitor role
│   │   ├── probes.py             /health and /ready probes, wait-until-ready
│   │   ├── synthetic.py          marked synthetic checkout probes
│   │   ├── deployment.py         deployment state file: atomic switch + history
│   │   ├── audit.py              incident state + append-only audit events
│   │   ├── report.py             incident report rendered from the audit log
│   │   ├── trueforge.py          TrueForge API: setup (model, connector, skill, agent) + approval attestation
│   │   ├── dashboard.py/.html    mission control (read-only) at /dashboard
│   │   ├── agent_driver.py       terminal driver for a TrueForge session (official trueforge-sdk)
│   │   ├── cli.py                `forgesre` CLI: init-state, deploy, reset, status, check, trueforge-setup, agent-run
│   │   ├── config.py             loads config/*.yaml + .env, validates at startup
│   │   └── results.py            success / failure / partial envelope with error codes
│   └── tests/
│       ├── test_units.py         parsing, thresholds, deployment state, report numbers, TrueForge event parsing
│       ├── test_safety.py        injection, allowlists, rate limit, rollback validation, attestation refusal/replay
│       ├── test_integration.py   live stack: incident, evidence, restart ≠ recovery, rollback, reset (marker: integration)
│       ├── test_trueforge_lifecycle.py  full lifecycle through TrueForge, allow + deny (marker: trueforge)
│       ├── test_trueforge_gate.py       approval gate with the configured real model (marker: trueforge)
│       ├── scripted_model.py     TEST DOUBLE: scripted OpenAI-compatible model for deterministic harness tests
│       ├── mcp_wire.py           raw JSON-RPC MCP client (what TrueForge sees on the wire)
│       └── conftest.py           isolated settings/state for unit tests
│
├── skills/incident-diagnostics/  git-backed TrueForge skill
│   ├── SKILL.md                  how to run and read the analyzer
│   └── scripts/diagnose.py       reference analyzer, runs in the sandbox, evidence via mcp_client
│
├── scripts/                      everything a human runs
│   ├── up.sh                     one command: stack + MCP + TrueForge + agent + healthy baseline
│   ├── setup.sh                  prerequisites, .env secrets, Python env, images
│   ├── doctor.sh                 checks every prerequisite and component, with the fix for each
│   ├── start-demo.sh / stop-demo.sh / restart-mcp.sh
│   ├── start-trueforge.sh        TrueForge 0.2.1, loopback allowlist, project-local SQLite
│   ├── setup-trueforge.sh        configure TrueForge (wraps `forgesre trueforge-setup`)
│   ├── reset-demo.sh             known-good baseline (removes v2, fresh metrics, waits for health)
│   ├── trigger-incident.sh       release pipeline ships payment-service v2
│   ├── verify-healthy.sh / verify-incident.sh
│   ├── run-agent.sh              drive the agent from a terminal (asks you to Allow/Deny)
│   ├── rehearse.sh               keyless rehearsal: scripted stand-in model, you click Allow/Deny in TrueForge
│   ├── lib.sh                    shared helpers (.env loading, MCP start/stop)
│   └── dev/                      snap.mjs (CDP screenshots), capture_showcase.py, rehearse.py
│
├── docs/                         see docs/README.md
├── artifacts/incidents/          generated reports (+ committed examples)
└── state/                        runtime state (git-ignored)
```

## Where to look for …

| I want to … | Go to |
|---|---|
| change what counts as "recovered" | `config/verification.yaml` |
| allow/deny restarting another service | `config/policy.yaml` → `restart_service.allowed_targets` |
| change which tools need approval | `mcp-server/src/forgesre/trueforge.py` → `APPROVAL_GATED_TOOLS`, then `./scripts/setup-trueforge.sh` |
| change the agent's behaviour | `agent/forgesre.system.md`, then `./scripts/setup-trueforge.sh` |
| add a tool | `ops.py` (logic) + `server.py` (MCP definition + annotation) + a test in `test_safety.py` |
| add a metric the agent can query | `prom.py` → `SIGNALS` |
| change the incident | `demo/payment-v2/app.py` (and `AUDIT_BATCH_SIZE` / pool size in `docker-compose.yml`) |
| add a service | `docker-compose.yml` + `config/services.yaml` (+ `demo/prometheus/prometheus.yml`) |
| see what happened in a run | TrueForge session; `artifacts/audit/events.jsonl`; `artifacts/incidents/` |
| regenerate README images | `uv run --project mcp-server python scripts/dev/capture_showcase.py --clean-sessions` |

## One rollback call, file by file

1. **TrueForge** — the model emits `rollback_deployment`; the agent spec (written by `trueforge.py::agent_manifest`)
   lists it in `require_approval_for_tools`, so TrueForge ends the turn with `tool.approval_required`.
2. **Human** clicks Allow; TrueForge records a `user.tool_approval` turn input and dispatches the call to
   `FORGESRE_MCP_URL` (approved non-loopback URL) with the connector's Bearer token; local loopback is refused by setup.
3. **`server.py`** — `BearerAuth` checks the token; `rollback_deployment()` validates argument shapes (pydantic
   patterns) and calls `ops.rollback` in a worker thread; every call is recorded as a `tool_call` audit event.
4. **`ops.py::rollback`** — policy (`config/policy.yaml`), catalog lookups (`catalog.py`), live version
   (`deployment.py`), open incident (`audit.py`, auto-opened from Prometheus alerts via `prom.py`).
5. **`ops.py::_attest_approval`** → **`trueforge.py::find_approval`** — reads recent sessions' events (the call's
   arguments) and turns (the Allow), matches service/from/to, refuses a reused approval.
6. **`dockerctl.py::ensure_running`** + **`probes.py::wait_ready`** — target v1 up and `/ready`.
7. **`deployment.py::record_switch`** — flock, atomic rewrite of `state/deployment.json` with history.
8. **gateway** (`demo/gateway/app.py`) — sees the new mtime on its next request and routes to v1;
   `ops.rollback` polls `/route` until it confirms.
9. **`dockerctl.py::stop`** — v2 stopped; its 85 stranded PostgreSQL sessions close.
10. **`audit.py`** — `action_started` (with the attestation) and `action_completed`; the agent then calls
    `verify_recovery`, and `report.py` later renders all of it.

## Ports

| Port | Service |
|---|---|
| 8790 | TrueForge UI + API |
| 18900 | ForgeSRE MCP server (`/mcp`, `/dashboard`, `/api/state`, `/healthz`) |
| 18080 | api-gateway |
| 18101 / 18102 | payment-service v1 / v2 |
| 19090 | Prometheus |
| 15432 | PostgreSQL |
| 18990 | scripted test-double model (tests / rehearsal only) |

All configurable in `.env`; all bound to 127.0.0.1.
