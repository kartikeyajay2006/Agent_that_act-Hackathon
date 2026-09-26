"""Mission-control dashboard routes (read-only)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .config import Settings
from .ops import Ops


def register(mcp: MCPServer, ops: Ops, settings: Settings) -> None:
    """Attach dashboard routes to the MCP server's HTTP app."""
