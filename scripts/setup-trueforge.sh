#!/usr/bin/env bash
# Configure TrueForge for ForgeSRE: model provider, MCP connector, sandbox, agent.
source "$(dirname "$0")/lib.sh"
require_env
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" >/dev/null \
  || die "TrueForge is not running — ./scripts/start-trueforge.sh"
mcp_running || start_mcp
forgesre trueforge-setup
