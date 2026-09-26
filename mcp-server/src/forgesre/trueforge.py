"""TrueForge HTTP API integration.

1. Idempotent setup of everything ForgeSRE needs inside TrueForge: model
   provider (properties taken from TrueForge's own catalog), the forgesre MCP
   connector (header auth), optional Daytona sandbox, and the ForgeSRE agent
   spec with rollback_deployment behind require_approval_for_tools.
2. Approval attestation: before a RED action executes, the MCP server confirms
   in TrueForge's durable session events that a human approved this exact call.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

AGENT_NAME = "forgesre"
MCP_SERVER_NAME = "forgesre"
APPROVAL_GATED_TOOLS = ["rollback_deployment", "@destructive"]
SKILL_NAME = "incident-diagnostics"


class TrueForgeError(RuntimeError):
    pass


class TrueForge:
    def __init__(self, base_url: str, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _req(self, method: str, path: str, body: Any | None = None, params: dict | None = None) -> Any:
        try:
            resp = httpx.request(method, f"{self.base_url}{path}", json=body, params=params, timeout=self.timeout_s)
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
        raise TrueForgeError(f"provider type '{provider_type}' is not in the TrueForge model catalog")

    def configure_model(self, *, provider: str, model_id: str, api_key: str, base_url: str | None) -> str:
        """Create/update the provider and return the model FQN the agent spec should use."""
        if provider in ("custom", "truefoundry"):
            name = re.sub(r"[^a-z0-9-]+", "-", model_id.lower()).strip("-")[:60] or "model"
            models = [{"model_id": model_id, "name": name, "properties": {}}]
            manifest: dict[str, Any] = {"type": provider, "models": models}
            if not base_url:
                raise TrueForgeError(f"MODEL_BASE_URL is required for provider '{provider}'")
            manifest["base_url"] = base_url
            if provider == "custom":
                manifest["name"] = "forgesre-model"
            if api_key:
                manifest["auth"] = {"api_key": api_key}
        else:
            catalog = self.catalog_models(provider)
            match = next((m for m in catalog if m["model_id"] == model_id), None)
            if match is None:
                raise TrueForgeError(
                    f"model '{model_id}' is not in TrueForge's {provider} catalog: {[m['model_id'] for m in catalog]}"
                )
            manifest = {"type": provider, "auth": {"api_key": api_key}, "models": [match]}
            if base_url:
                manifest["base_url"] = base_url
        existing = self._req("GET", "/api/v1/settings/model-providers").get("data", [])
        method = "PUT" if any(self._provider_key(p) == self._provider_key(manifest) for p in existing) else "POST"
        self._req(method, "/api/v1/settings/model-providers", {"manifest": manifest})
        models = self._req("GET", "/api/v1/models").get("data", [])
        wanted = manifest["models"][0]["name"]
        for m in models:
            fqn = m.get("name") or m.get("id") or ""
            if fqn.endswith(f"/{wanted}"):
                return fqn
        raise TrueForgeError(f"model {wanted} not visible in /api/v1/models after configuring: {models}")

    @staticmethod
    def _provider_key(p: dict[str, Any]) -> tuple[str, str]:
        m = p.get("manifest", p)
        return (m.get("type", ""), m.get("name", ""))

    def configure_mcp(self, *, url: str, token: str) -> None:
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
        catalog = self._req("GET", "/api/v1/catalogs/sandbox-providers").get("data", [])
        preset = next((p for p in catalog if p.get("type") == "daytona"), {"type": "daytona"})
        manifest = {**preset, "auth": {"api_key": api_key}}
        self._req("PUT", "/api/v1/settings/sandbox-providers", {"manifest": manifest})

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

    def agent_manifest(self, *, model_fqn: str, instructions: str, with_skill: bool = False) -> dict[str, Any]:
        return {
            "model": {"name": model_fqn, "params": {"temperature": 0.1}},
            "instructions": instructions,
            "mcp_servers": [
                {
                    "name": MCP_SERVER_NAME,
                    "enable_tools": ["@all"],
                    "require_approval_for_tools": APPROVAL_GATED_TOOLS,
                    "preload": True,
                }
            ],
            "skills": [{"name": SKILL_NAME}] if with_skill else [],
            "config": {
                "sandbox": {"enabled": True},
                "generative_ui": {"enabled": False},
                "ask_user_questions": {"enabled": False},
                "dynamic_sub_agents": {"enabled": False},
                "iteration_limit": 80,
            },
        }

    def upsert_agent(self, manifest: dict[str, Any]) -> dict[str, Any]:
        agents = self._req("GET", "/api/v1/agents").get("data", [])
        existing = next((a for a in agents if a.get("name") == AGENT_NAME), None)
        body = {
            "name": AGENT_NAME,
            "description": "Autonomous production reliability agent (investigate, diagnose, act, verify, report)",
            "manifest": manifest,
        }
        if existing:
            return self._req("PUT", f"/api/v1/agents/{existing['id']}", body).get("data", {})
        return self._req("POST", "/api/v1/agents", body).get("data", {})

    def get_agent(self) -> dict[str, Any] | None:
        agents = self._req("GET", "/api/v1/agents").get("data", [])
        return next((a for a in agents if a.get("name") == AGENT_NAME), None)

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
    return TrueForge(os.environ.get("TRUEFORGE_URL", "http://localhost:8790"))
