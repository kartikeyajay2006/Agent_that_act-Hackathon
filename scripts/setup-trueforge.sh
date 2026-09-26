#!/usr/bin/env bash
# Configure TrueForge for ForgeSRE: model provider, MCP connector, sandbox, agent.
source "$(dirname "$0")/lib.sh"
require_env
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" >/dev/null \
  || die "TrueForge is not running — ./scripts/start-trueforge.sh"
case "$(uname -s)" in
  Linux*) ;;
  *) [[ -n "${DAYTONA_API_KEY:-}" ]] || die "non-Linux hosts need a valid Daytona key for the TrueForge sandbox" ;;
esac
mcp_running || start_mcp
forgesre trueforge-setup
