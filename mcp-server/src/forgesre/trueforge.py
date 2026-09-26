"""TrueForge HTTP API integration.

1. Idempotent setup of everything ForgeSRE needs inside TrueForge: model
   provider (properties taken from TrueForge's own catalog), the forgesre MCP
   connector (header auth), optional Daytona sandbox, and the ForgeSRE agent
   spec with rollback_deployment behind require_approval_for_tools.
2. Approval attestation: before a RED action executes, the MCP server confirms
   in TrueForge's durable session events that a human approved this exact call.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from trueforge_sdk import ScheduleManifest
from trueforge_sdk import TrueForge as TrueForgeSDK

AGENT_NAME = "forgesre"
READ_ONLY_AGENT_NAME = "forgesre-investigator"
MCP_SERVER_NAME = "forgesre"
APPROVAL_GATED_TOOLS = ["rollback_deployment", "@destructive"]
SKILL_NAME = "incident-diagnostics"
READ_ONLY_TOOLS = [
    "list_services",
    "get_incident_context",
    "get_service_health",
    "get_service_logs",
    "query_metrics",
    "get_database_health",
    "get_recent_deployments",
    "get_active_deployment",
    "collect_incident_evidence",
    "get_incident_timeline",
]


class TrueForgeError(RuntimeError):
    pass


# Limits for models TrueForge's catalog does not list (e.g. models behind the TrueFoundry AI Gateway).
# context_length also sets TrueForge's compaction threshold (80 %), so it matters for long incidents.
_KNOWN_MODELS: dict[str, dict[str, Any]] = {
    "gpt-4.1-mini": {"context_length": 1047576, "max_output_tokens": 32768},
    "gpt-4.1": {"context_length": 1047576, "max_output_tokens": 32768},
    "gpt-4.1-nano": {"context_length": 1047576, "max_output_tokens": 32768},
    "gpt-4o-mini": {"context_length": 128000, "max_output_tokens": 16384},
    "gpt-4o": {"context_length": 128000, "max_output_tokens": 16384},
    "gpt-5-mini": {"context_length": 400000, "max_output_tokens": 128000},
    "gemini-2-5-flash": {"context_length": 1048576, "max_output_tokens": 65536},
}
_REASONING = re.compile(r"(^|/)(gpt-5|o1|o3|o4)")


def known_model_properties(model_id: str) -> dict[str, Any]:
    short = model_id.rsplit("/", 1)[-1]
    return dict(_KNOWN_MODELS.get(short, {}))


def model_params(model: str, reasoning_effort: str | None = None) -> dict[str, Any]:
    """Reasoning models reject temperature; plain chat models get a low temperature for steady tool use."""
    if _REASONING.search(model.lower()) or "gpt-5" in model.lower():
        return {"reasoning_effort": reasoning_effort or os.environ.get("MODEL_REASONING_EFFORT", "low")}
    return {"temperature": 0.1, "parallel_tool_calls": False}


class TrueForge:
    def __init__(self, base_url: str, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _req(
        self, method: str, path: str, body: Any | None = None, params: dict | None = None, timeout: float | None = None
    ) -> Any:
        try:
            token = os.environ.get("TRUEFORGE_TOKEN")
            resp = httpx.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                params=params,
                timeout=timeout or self.timeout_s,
                headers={"Authorization": f"Bearer {token}"} if token else None,
            )
        except httpx.HTTPError as exc:
            raise TrueForgeError(f"TrueForge unreachable at {self.base_url}: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise TrueForgeError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------------ setup
    def catalog_models(self, provider_type: str) -> list[dict[str, Any]]:
        data = self._req("GET", "/api/v1/catalogs/model-providers").get("data", [])
        for p in data:
            if p.get("type") == provider_type:
                return p.get("models", [])
        return []

    def _model_entry(self, model_id: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
        """TrueForge catalog entry when there is one; otherwise known limits for the model family."""
        match = next((m for m in catalog if m["model_id"] == model_id), None)
        if match:
            return match
        name = re.sub(r"[^a-z0-9-]+", "-", model_id.lower()).strip("-")[:60] or "model"
        return {"model_id": model_id, "name": name, "properties": known_model_properties(model_id)}

    def configure_provider(
        self,
        *,
        provider: str,
        model_ids: list[str],
        api_key: str,
        base_url: str | None,
        name: str | None = None,
    ) -> list[str]:
        """Create/update one provider with one or more models; returns their TrueForge FQNs in order."""
        catalog = self.catalog_models(provider)
        manifest: dict[str, Any] = {"type": provider, "models": [self._model_entry(m, catalog) for m in model_ids]}
        if provider in ("custom", "truefoundry") and not base_url:
            raise TrueForgeError(f"a base URL is required for provider '{provider}'")
        if base_url:
            manifest["base_url"] = base_url
        if provider == "custom":
            manifest["name"] = name or "forgesre-model"
        if api_key:
            manifest["auth"] = {"api_key": api_key}
        existing = self._req("GET", "/api/v1/settings/model-providers").get("data", [])
        method = "PUT" if any(self._provider_key(p) == self._provider_key(manifest) for p in existing) else "POST"
        self._req(method, "/api/v1/settings/model-providers", {"manifest": manifest})
        visible = [m.get("name") or m.get("id") or "" for m in self._req("GET", "/api/v1/models").get("data", [])]
        fqns = []
        for entry in manifest["models"]:
            fqn = next((v for v in visible if v.endswith(f"/{entry['name']}")), None)
            if fqn is None:
                raise TrueForgeError(f"model {entry['name']} not visible in /api/v1/models after configuring")
            fqns.append(fqn)
        return fqns

    def configure_model(self, *, provider: str, model_id: str, api_key: str, base_url: str | None) -> str:
        """Primary model for the agent; returns its FQN."""
        return self.configure_provider(provider=provider, model_ids=[model_id], api_key=api_key, base_url=base_url)[0]

    @staticmethod
    def _provider_key(p: dict[str, Any]) -> tuple[str, str]:
        m = p.get("manifest", p)
        return (m.get("type", ""), m.get("name", ""))

    def mcp_transport_types(self) -> set[str]:
        """Read supported MCP manifest transports from this server's OpenAPI schema."""
        spec = self._req("GET", "/api/v1/openapi.json")
        schemas = (spec.get("components") or {}).get("schemas") or {}
        manifest = schemas.get("MCPServerManifest") or {}
        transports = (manifest.get("discriminator") or {}).get("mapping") or {}
        if transports:
            return set(transports)
        # Some OpenAPI generators omit the discriminator mapping but retain oneOf refs.
        refs = [item.get("$ref", "").rsplit("/", 1)[-1] for item in manifest.get("oneOf", [])]
        inferred = set()
        for ref in refs:
            lowered = ref.lower()
            if "stdio" in lowered:
                inferred.add("stdio")
            elif "truefoundry" in lowered:
                inferred.add("truefoundry")
            elif "remote" in lowered:
                inferred.add("remote")
        if inferred:
            return inferred
        raise TrueForgeError("could not determine supported MCP transports from installed TrueForge OpenAPI schema")

    @staticmethod
    def _is_loopback_url(url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def configure_mcp(self, *, url: str, token: str) -> None:
        if self._is_loopback_url(url):
            raise TrueForgeError(
                "refusing loopback remote MCP URL; use a supported stdio transport or provide "
                "FORGESRE_MCP_URL as a real non-loopback endpoint"
            )
        transports = self.mcp_transport_types()
        if "remote" not in transports:
            raise TrueForgeError("installed TrueForge schema does not support remote MCP endpoints")
        manifest = {
            "type": "remote",
            "name": MCP_SERVER_NAME,
            "url": url,
            "description": "ForgeSRE production operations: health, logs, metrics, deployments, "
            "sandbox evidence, risk assessment, restart, approval-gated rollback, verification, reports.",
            "auth": {"type": "header", "headers": {"Authorization": f"Bearer {token}"}},
        }
        existing = [
            s.get("manifest", s).get("name") for s in self._req("GET", "/api/v1/settings/mcp-servers").get("data", [])
        ]
        method = "PUT" if MCP_SERVER_NAME in existing else "POST"
        self._req(method, "/api/v1/settings/mcp-servers", {"manifest": manifest})

    def list_mcp_tools(self) -> list[dict[str, Any]]:
        return self._req("GET", f"/api/v1/mcp-servers/{MCP_SERVER_NAME}/tools").get("data", [])

    def configure_daytona(self, api_key: str) -> None:
        """First configuration builds a snapshot in the Daytona account, which can take several minutes."""
        catalog = self._req("GET", "/api/v1/catalogs/sandbox-providers").get("data", [])
        preset = next((p for p in catalog if p.get("type") == "daytona"), {"type": "daytona"})
        manifest = {**preset, "auth": {"api_key": api_key}}
        self._req("PUT", "/api/v1/settings/sandbox-providers", {"manifest": manifest}, timeout=1200)

    def sandbox_provider(self) -> str | None:
        try:
            data = self._req("GET", "/api/v1/settings/sandbox-providers").get("data")
        except TrueForgeError:
            return None
        return (data or {}).get("type") if isinstance(data, dict) else None

    def configure_skill(self, *, repo_url: str, ref: str, path: str) -> None:
        manifest = {
            "type": "git",
            "name": SKILL_NAME,
            "url": repo_url,
            "ref": ref,
            "path": path,
            "description": "Evidence-driven incident diagnosis in the sandbox: computes incident start, failing "
            "version, deployment timing, saturation, log signatures, restart effect and an evidence score.",
        }
        existing = [
            k.get("manifest", k).get("name") for k in self._req("GET", "/api/v1/settings/skills").get("data", [])
        ]
        self._req("PUT" if SKILL_NAME in existing else "POST", "/api/v1/settings/skills", {"manifest": manifest})

    def agent_manifest(
        self,
        *,
        model_fqn: str,
        instructions: str,
        with_skill: bool = False,
        params: dict[str, Any] | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        enabled_tools = READ_ONLY_TOOLS if read_only else ["@all"]
        return {
            "model": {"name": model_fqn, "params": params if params is not None else model_params(model_fqn)},
            "instructions": instructions,
            "mcp_servers": [
                {
                    "name": MCP_SERVER_NAME,
                    "enable_tools": enabled_tools,
                    "require_approval_for_tools": [] if read_only else APPROVAL_GATED_TOOLS,
                    "preload": True,
                }
            ],
            "skills": [{"name": SKILL_NAME}] if with_skill and not read_only else [],
            "config": {
                "sandbox": {"enabled": not read_only},
                "generative_ui": {"enabled": False},
                "ask_user_questions": {"enabled": False},
                "dynamic_sub_agents": {"enabled": False},
                "iteration_limit": 30 if read_only else 80,
            },
        }

    def upsert_agent(
        self,
        manifest: dict[str, Any],
        *,
        name: str = AGENT_NAME,
        description: str = "Autonomous production reliability agent (investigate, diagnose, act, verify, report)",
    ) -> dict[str, Any]:
        agents = self._req("GET", "/api/v1/agents").get("data", [])
        existing = next((a for a in agents if a.get("name") == name), None)
        body = {
            "name": name,
            "description": description,
            "manifest": manifest,
        }
        if existing:
            update = {k: v for k, v in body.items() if k != "name"}  # name is immutable
            return self._req("PUT", f"/api/v1/agents/{existing['id']}", update).get("data", {})
        return self._req("POST", "/api/v1/agents", body).get("data", {})

    def get_agent(self, name: str = AGENT_NAME) -> dict[str, Any] | None:
        agents = self._req("GET", "/api/v1/agents").get("data", [])
        return next((a for a in agents if a.get("name") == name), None)

    @staticmethod
    def validate_read_only_agent(agent: dict[str, Any] | None) -> list[str]:
        """Require the exact investigator profile and exact observation allowlist."""
        if agent is None or agent.get("name") != READ_ONLY_AGENT_NAME:
            raise TrueForgeError(f"saved agent '{READ_ONLY_AGENT_NAME}' is unavailable")
        manifest = agent.get("manifest") or {}
        servers = [s for s in manifest.get("mcp_servers", []) if s.get("name") == MCP_SERVER_NAME]
        if len(servers) != 1 or len(manifest.get("mcp_servers", [])) != 1:
            raise TrueForgeError(f"agent '{READ_ONLY_AGENT_NAME}' must have exactly one '{MCP_SERVER_NAME}' MCP entry")
        tools = servers[0].get("enable_tools")
        if not isinstance(tools, list) or set(tools) != set(READ_ONLY_TOOLS):
            raise TrueForgeError(
                f"agent '{READ_ONLY_AGENT_NAME}' tool allowlist mismatch; expected observation tools only"
            )
        config = manifest.get("config") or {}
        sandbox = config.get("sandbox") or {}
        if sandbox.get("enabled") is not False or manifest.get("skills"):
            raise TrueForgeError(f"agent '{READ_ONLY_AGENT_NAME}' must have sandbox and skills disabled")
        return list(tools)

    def upsert_read_only_schedule(
        self,
        *,
        name: str,
        cron: str,
        timezone: str,
        task: str,
        agent_name: str = READ_ONLY_AGENT_NAME,
        activate: bool = False,
        sdk_factory: Any = TrueForgeSDK,
    ) -> dict[str, str]:
        """Create/update a schedule only after confirming its target is the observation-only agent."""
        for label, value in (("schedule name", name), ("cron", cron), ("timezone", timezone), ("task", task)):
            if not value.strip():
                raise TrueForgeError(f"TrueForge schedule {label} must not be empty")
        if len(cron.split()) != 5:
            raise TrueForgeError("TrueForge schedule cron must be a standard five-field expression")
        self.validate_read_only_agent(self.get_agent(agent_name))

        try:
            sdk = sdk_factory(
                base_url=self.base_url,
                token=os.environ.get("TRUEFORGE_TOKEN") or None,
                timeout=max(self.timeout_s, 600),
            )
            status = "active" if activate else "paused"
            manifest = ScheduleManifest(cron=cron, timezone=timezone, task=task, status=status)
            matching = [schedule for schedule in sdk.schedules.list() if schedule.name == name]
            if len(matching) > 1:
                raise TrueForgeError(f"multiple TrueForge schedules use the configured name '{name}'")
            if matching:
                existing = matching[0]
                if existing.agent_name != agent_name:
                    raise TrueForgeError(f"schedule '{name}' belongs to a different agent; refusing to replace it")
                result = sdk.schedules.update(
                    schedule_id=existing.id,
                    name=name,
                    manifest=manifest,
                )
                operation = "updated"
            else:
                result = sdk.schedules.create(agent_name=agent_name, name=name, manifest=manifest)
                operation = "created"
        except TrueForgeError:
            raise
        except Exception as exc:
            raise TrueForgeError(f"TrueForge schedule API failed: {type(exc).__name__}: {exc}") from exc

        schedule = getattr(result, "data", result)
        return {
            "operation": operation,
            "id": str(schedule.id),
            "name": str(schedule.name),
            "agent_name": str(schedule.agent_name),
            "status": status,
        }

    # ------------------------------------------------------------------ attestation
    def session_events(self, session_id: str, max_pages: int = 20) -> list[dict[str, Any]]:
        """All events of a session (API pages newest-first; items are {turn_id, event})."""
        events: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 100}
            if token:
                params["page_token"] = token
            page = self._req("GET", f"/api/v1/sessions/{session_id}/events", params=params)
            events.extend(item.get("event", item) for item in page.get("data", []))
            token = (page.get("pagination") or {}).get("next_page_token")
            if not token:
                break
        return events[::-1]

    def session_turns(self, session_id: str, max_pages: int = 10) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 25}
            if token:
                params["page_token"] = token
            page = self._req("GET", f"/api/v1/sessions/{session_id}/turns", params=params)
            turns.extend(page.get("data", []))
            token = (page.get("pagination") or {}).get("next_page_token")
            if not token:
                break
        return turns

    def approvals(self, session_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Human approval decisions: the resuming turn's user.tool_approval inputs (and any such events)."""
        found = [
            {**item, "created_at": turn.get("created_at"), "turn_id": turn.get("id")}
            for turn in self.session_turns(session_id)
            for item in turn.get("input") or []
            if item.get("type") == "user.tool_approval"
        ]
        found += [e for e in events if e.get("type") == "user.tool_approval"]
        return found

    def find_approval(self, *, tool: str, arguments: dict[str, Any], within_minutes: int = 30) -> dict[str, Any] | None:
        """Find a TrueForge tool call to `tool` with matching arguments that a human approved."""
        since = datetime.now(UTC) - timedelta(minutes=within_minutes)
        sessions = self._req(
            "GET", "/api/v1/sessions", params={"limit": 10, "order": "desc", "start_timestamp": since.isoformat()}
        ).get("data", [])
        for s in sessions:
            events = self.session_events(s["id"])
            calls = {c["id"]: c for c in _tool_calls(events, tool)}
            if not calls:
                continue
            for decision in self.approvals(s["id"], events):
                call = calls.get(decision.get("tool_call_id", ""))
                if not call or (decision.get("approval") or {}).get("status") != "allow":
                    continue
                if _args_match(call.get("arguments"), arguments):
                    return {
                        "session_id": s["id"],
                        "tool_call_id": call["id"],
                        "approval_turn_id": decision.get("turn_id") or decision.get("id"),
                        "approved_at": decision.get("created_at"),
                    }
        return None


def _tool_calls(events: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    """MCP tool calls to forgesre/<tool> in model.message events: {id, arguments}."""
    out: list[dict[str, Any]] = []
    for e in events:
        if e.get("type") != "model.message":
            continue
        for tc in e.get("tool_calls") or []:
            info = tc.get("tool_info") or {}
            if info.get("type") != "mcp" or info.get("server_name") != MCP_SERVER_NAME or info.get("name") != tool:
                continue
            raw = (tc.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = None
            out.append({"id": tc.get("id"), "arguments": args})
    return out


def _args_match(call_args: Any, arguments: dict[str, Any]) -> bool:
    if not isinstance(call_args, dict):
        return False
    return all(call_args.get(k) == v for k, v in arguments.items())


def from_env() -> TrueForge:
    return TrueForge(
        os.environ.get("TRUEFORGE_BASE_URL") or os.environ.get("TRUEFORGE_URL", "http://localhost:8790")
    )
