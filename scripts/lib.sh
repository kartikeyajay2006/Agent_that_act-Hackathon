#!/usr/bin/env bash
# Shared helpers for ForgeSRE scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"
cd "$ROOT"

# Load .env without overriding variables already set in the environment.
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] || export "$key=$value"
  done <"$ROOT/.env"
fi

# One exported value is shared by setup, preflight, and the session runner.
TRUEFORGE_URL="${TRUEFORGE_BASE_URL:-${TRUEFORGE_URL:-http://localhost:${TRUEFORGE_PORT:-8790}}}"
TRUEFORGE_BASE_URL="$TRUEFORGE_URL"
export TRUEFORGE_URL TRUEFORGE_BASE_URL

COMPOSE=(docker compose -f "$ROOT/docker-compose.yml" --profile release-v2)

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# Run the ForgeSRE operator CLI from the mcp-server project environment.
forgesre() {
  PYTHONPATH="$ROOT/mcp-server/src${PYTHONPATH:+:$PYTHONPATH}" VIRTUAL_ENV= \
    uv run --quiet --project "$ROOT/mcp-server" forgesre "$@"
}

require_env() {
  [[ -f "$ROOT/.env" ]] || die ".env missing — run ./scripts/setup.sh first"
}

mcp_running() {
  [[ -f "$RUN_DIR/mcp.pid" ]] && kill -0 "$(cat "$RUN_DIR/mcp.pid")" 2>/dev/null
}

start_mcp() {
  if mcp_running; then ok "MCP server already running (pid $(cat "$RUN_DIR/mcp.pid"))"; return; fi
  say "starting ForgeSRE MCP server on ${FORGESRE_MCP_HOST:-127.0.0.1}:${FORGESRE_MCP_PORT:-18900}"
  PYTHONPATH="$ROOT/mcp-server/src${PYTHONPATH:+:$PYTHONPATH}" VIRTUAL_ENV= \
    nohup uv run --quiet --project "$ROOT/mcp-server" forgesre-mcp >"$RUN_DIR/mcp.log" 2>&1 &
  echo $! >"$RUN_DIR/mcp.pid"
  for _ in $(seq 1 60); do
    mcp_running || die "MCP server exited during startup; see $RUN_DIR/mcp.log"
    if mcp_port_open; then
      ok "MCP server up — endpoint http://${FORGESRE_MCP_HOST:-127.0.0.1}:${FORGESRE_MCP_PORT:-18900}/mcp"
      return
    fi
    sleep 0.5
  done
  die "MCP server did not start; see $RUN_DIR/mcp.log"
}

mcp_port_open() {
  curl -fs "http://${FORGESRE_MCP_HOST:-127.0.0.1}:${FORGESRE_MCP_PORT:-18900}/healthz" >/dev/null 2>&1
}

stop_mcp() {
  if mcp_running; then
    kill "$(cat "$RUN_DIR/mcp.pid")"
    for _ in $(seq 1 40); do mcp_port_open || break; sleep 0.25; done
    ok "MCP server stopped"
  fi
  rm -f "$RUN_DIR/mcp.pid"
  mcp_port_open && die "port ${FORGESRE_MCP_PORT:-18900} is still served by another process"
  return 0
}
