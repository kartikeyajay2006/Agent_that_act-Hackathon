#!/usr/bin/env bash
# Start the demo production stack (payment-service v1 live) and the MCP server.
source "$(dirname "$0")/lib.sh"
require_env

forgesre init-state
say "starting production stack"
docker compose -f "$ROOT/docker-compose.yml" up -d --wait
ok "stack running: gateway http://127.0.0.1:${GATEWAY_HOST_PORT:-18080}  prometheus http://127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}"
start_mcp
