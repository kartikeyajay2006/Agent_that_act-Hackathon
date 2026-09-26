# Setting up ForgeSRE inside TrueForge

`./scripts/setup-trueforge.sh` does all of this through TrueForge's HTTP API and is safe to re-run. This page explains
what it configures so you can check it in the UI or do it by hand.

## 1. Run TrueForge

```bash
./scripts/start-trueforge.sh
```

Runs `npx @truefoundry/trueforge@0.2.1` in local (standalone) mode with:

| Setting | Value | Why |
|---|---|---|
| `SQLITE_PATH` | `.trueforge/trueforge.sqlite` | Sessions stay with the project |
| `PORT` | `8790` | TrueForge default |
| `OUTBOUND_URL_ALLOWED_HOSTS` | `["127.0.0.1","localhost"]` | TrueForge blocks private hosts by default; the ForgeSRE MCP server listens on 127.0.0.1 |

In standalone mode TrueForge provides a local sandbox (bubblewrap on Linux) when no provider is configured. To use
Daytona instead, set `DAYTONA_API_KEY` in `.env`; setup configures it under **Settings → Sandbox providers**.

## 2. Model

**Settings → Models.** Setup reads `MODEL_PROVIDER`, `MODEL_ID`, `MODEL_API_KEY` (and `MODEL_BASE_URL` for `custom` /
`truefoundry`) from `.env`, copies the model's properties from TrueForge's own catalog
(`GET /api/v1/catalogs/model-providers`) and registers the provider. Use a strong tool-calling model; the incident
involves long evidence and multi-step reasoning.

## 3. MCP connector

**Settings → Connectors → Add MCP Server**

| Field | Value |
|---|---|
| Name | `forgesre` |
| URL | `http://127.0.0.1:18900/mcp` |
| Auth | Header `Authorization: Bearer <FORGESRE_MCP_TOKEN from .env>` |

TrueForge should list 16 tools. `rollback_deployment` shows as destructive, `restart_service` as write, the rest as
read-only.

## 4. Skill

Attached automatically when the sandbox can clone it (Daytona, or a local host whose git helpers live under
`/usr/lib*`). On Fedora/RHEL the local sandbox cannot run git's HTTPS helper, so setup leaves the skill detached and the
agent instructions fetch the same analyzer with `curl` instead. Force either way with `FORGESRE_ATTACH_SKILL`.

**Settings → Skills → Import from GitHub**

| Field | Value |
|---|---|
| Name | `incident-diagnostics` |
| URL | `https://github.com/kartikeyajay2006/Agent_that_act-Hackathon` (or your fork: `FORGESRE_SKILL_REPO`) |
| Path | `skills/incident-diagnostics` |
| Ref | `main` (`FORGESRE_SKILL_REF`) |

## 5. Agent

**Build Agent**, or the API spec setup writes:

```json
{
  "name": "forgesre",
  "manifest": {
    "model": { "name": "<provider>/<model>", "params": { "temperature": 0.1 } },
    "instructions": "<agent/forgesre.system.md with {{DIAGNOSTICS_SOURCE}} filled in by setup>",
    "mcp_servers": [{
      "name": "forgesre",
      "enable_tools": ["@all"],
      "require_approval_for_tools": ["rollback_deployment", "@destructive"],
      "preload": true
    }],
    "skills": [{ "name": "incident-diagnostics" }],
    "config": {
      "sandbox": { "enabled": true },
      "generative_ui": { "enabled": false },
      "ask_user_questions": { "enabled": false },
      "dynamic_sub_agents": { "enabled": false },
      "iteration_limit": 80
    }
  }
}
```

If you paste the instructions into the UI by hand, replace `{{DIAGNOSTICS_SOURCE}}` with how to run the analyzer
(skill path, or the `curl … diagnose.py` line setup prints).

In the UI: **Select MCP Tools → forgesre**, make sure the shield (approval) is on for `rollback_deployment` and off for
`restart_service`; **Runtime Config → Sandbox** on.

`ask_user_questions` is off on purpose: the only human checkpoint should be the approval gate, so the agent cannot
turn a rollback decision into a chat question.

## 6. Check it

```bash
curl -s localhost:8790/api/v1/mcp-servers/forgesre/tools | jq '.data[] | {name, annotations}'
curl -s localhost:8790/api/v1/agents | jq '.data[] | select(.name=="forgesre") | .manifest.mcp_servers'
cd mcp-server && uv run pytest -m trueforge -s     # approval gate + sandbox through real sessions
```
