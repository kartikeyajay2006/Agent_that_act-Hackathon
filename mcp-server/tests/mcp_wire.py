"""Minimal MCP streamable-HTTP client speaking raw JSON-RPC (what TrueForge sees on the wire)."""

from __future__ import annotations

import itertools
import json
import os
from typing import Any

import httpx

from forgesre.config import get_settings

get_settings()  # loads .env (token, ports)
MCP_URL = os.environ.get("FORGESRE_MCP_URL", "http://127.0.0.1:18900/mcp")
_ids = itertools.count(1)
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def rpc(method: str, params: dict[str, Any] | None = None, timeout: float = 180) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}}
    headers = dict(HEADERS, **{"mcp-protocol-version": "2025-06-18"})
    if os.environ.get("FORGESRE_MCP_TOKEN"):
        headers["authorization"] = f"Bearer {os.environ['FORGESRE_MCP_TOKEN']}"
    resp = httpx.post(MCP_URL, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:])
                if msg.get("id") == body["id"]:
                    return msg
        raise RuntimeError(f"no response in SSE stream: {resp.text[:300]}")
    return resp.json()


def list_tools() -> list[dict[str, Any]]:
    return rpc("tools/list")["result"]["tools"]


def call(name: str, arguments: dict[str, Any] | None = None, timeout: float = 180) -> dict[str, Any]:
    msg = rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
    if "error" in msg:
        return {"_rpc_error": msg["error"]}
    result = msg["result"]
    if result.get("structuredContent") is not None:
        out = result["structuredContent"]
        return out.get("result", out) if isinstance(out, dict) and set(out) == {"result"} else out
    text = "".join(c.get("text", "") for c in result.get("content", []))
    try:
        return json.loads(text)
    except ValueError:
        return {"_text": text, "_is_error": result.get("isError")}
