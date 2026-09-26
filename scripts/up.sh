#!/usr/bin/env bash
# One command: production stack + MCP server + TrueForge + ForgeSRE agent, then a healthy baseline.
source "$(dirname "$0")/lib.sh"

[[ -f "$ROOT/.env" ]] || "$ROOT/scripts/setup.sh"
"$ROOT/scripts/start-demo.sh"
"$ROOT/scripts/start-trueforge.sh"
if grep -qE "^MODEL_API_KEY=.+" "$ROOT/.env" || grep -qE "^MODEL_PROVIDER=custom" "$ROOT/.env"; then
  "$ROOT/scripts/setup-trueforge.sh"
else
  warn "MODEL_API_KEY is empty in .env — add it, then run ./scripts/setup-trueforge.sh"
fi
"$ROOT/scripts/reset-demo.sh"

cat <<MSG

$(ok "ForgeSRE is up")
  TrueForge UI      http://localhost:${TRUEFORGE_PORT:-8790}   (Agents → forgesre → Try)
  Mission control   http://127.0.0.1:${FORGESRE_MCP_PORT:-18900}/dashboard
  Checkout API      http://127.0.0.1:${GATEWAY_HOST_PORT:-18080}/checkout
  Prometheus        http://127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}

  Next: ./scripts/trigger-incident.sh   then ask the agent to investigate.
MSG
