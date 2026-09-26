#!/usr/bin/env bash
# Configure TrueForge for ForgeSRE: model provider, MCP connector, sandbox, agent.
source "$(dirname "$0")/lib.sh"
require_env
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" >/dev/null \
  || die "TrueForge is not running — ./scripts/start-trueforge.sh"
case "$(uname -s)" in
  Linux*|Darwin*) ;;  # TrueForge ships a local sandbox for Linux (bubblewrap) and macOS (Seatbelt)
  *) [[ -n "${DAYTONA_API_KEY:-}" ]] || die "this OS has no TrueForge local sandbox; set DAYTONA_API_KEY (or use WSL2)" ;;
esac
mcp_running || start_mcp
forgesre trueforge-setup
