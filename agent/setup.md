# Setting up ForgeSRE inside TrueForge

`./scripts/setup-trueforge.sh` configures the read-only investigator by default; `--full` explicitly selects the
existing action-capable `forgesre` profile. Setup checks the installed TrueForge MCP schema before configuration.

## 1. Run TrueForge

```bash
./scripts/start-trueforge.sh
```

Runs `npx @truefoundry/trueforge@0.2.1` in local (standalone) mode with:

| Setting | Value | Why |
|---|---|---|
| `SQLITE_PATH` | `.trueforge/trueforge.sqlite` | Sessions stay with the project |
| `PORT` | `8790` | TrueForge default |
| Outbound URL policy | TrueForge defaults | Setup does not add loopback/private hosts to an allowlist |

In standalone mode TrueForge provides a local sandbox (bubblewrap on Linux) when no provider is configured. To use
Daytona instead, set `DAYTONA_API_KEY` in `.env`; setup configures it under **Settings → Sandbox providers**.

## 2. Model

**Settings → Models.** Setup reads `MODEL_PROVIDER`, `MODEL_ID`, `MODEL_API_KEY` (and `MODEL_BASE_URL` for `custom` /
`truefoundry`) from `.env`, copies the model's properties from TrueForge's own catalog
(`GET /api/v1/catalogs/model-providers`) and registers the provider. Use a strong tool-calling model; the incident
involves long evidence and multi-step reasoning.

## 3. MCP connector and transport support

TrueForge 0.2.1's installed OpenAPI schema advertises only `remote` and `truefoundry` URL-backed MCP manifests; it
does not support stdio/local-command MCP. Setup refuses to register the local loopback URL and leaves settings
unchanged. Do not add loopback hosts to TrueForge's outbound allowlist.

For a deployment with an approved, TrueForge-reachable MCP service, set `FORGESRE_MCP_URL` to its non-loopback HTTPS
`/mcp` URL. Bearer auth remains enabled using `FORGESRE_MCP_TOKEN`. Do not expose the development server publicly to
work around URL protections.

| Field | Value |
|---|---|
| Name | `forgesre` |
| URL | `FORGESRE_MCP_URL` (approved non-loopback HTTPS endpoint) |
| Auth | Header `Authorization: Bearer <FORGESRE_MCP_TOKEN from .env>` |

After the endpoint is reachable, TrueForge should list its MCP tools. The investigator agent receives only its exact
observation allowlist; restart and rollback are excluded.

## 4. Read-only investigator

The default setup creates the distinct `forgesre-investigator` agent with only observation tools. It has no
synthetic probes, skills, sandbox, restart, or rollback capability. Run it with `./scripts/run-investigation.sh`; its
preflight validates the saved agent name and allowlist, and it never submits an approval response.

## 5. Skill (full profile only)

Attached automatically when the sandbox can clone it (Daytona, or a local host whose git helpers live under
`/usr/lib*`). On Fedora/RHEL the local sandbox cannot run git's HTTPS helper, so setup leaves the skill detached and the
agent pulls the same analyzer through the MCP bridge (`get_reference_analyzer`) from inside the sandbox. Force either way
with `FORGESRE_ATTACH_SKILL`.

**Settings → Skills → Import from GitHub**

| Field | Value |
|---|---|
| Name | `incident-diagnostics` |
| URL | `https://github.com/kartikeyajay2006/Agent_that_act-Hackathon` (or your fork: `FORGESRE_SKILL_REPO`) |
| Path | `skills/incident-diagnostics` |
| Ref | `main` (`FORGESRE_SKILL_REF`) |

## 6. Full action-capable agent (explicit `--full` only)

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
(skill path, or the `mcp-client call-tool forgesre get_reference_analyzer …` line setup writes).

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
