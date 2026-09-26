#!/usr/bin/env bash
# Restore the known-good baseline regardless of what state a previous run left.
source "$(dirname "$0")/lib.sh"
require_env

say "restoring baseline deployment"
forgesre reset
say "clearing demo data"
if docker ps --format '{{.Names}}' | grep -qx forgesre-postgres; then
  docker exec forgesre-postgres psql -q -U payments -d payments \
    -c "TRUNCATE payments, payment_audit;" >/dev/null && ok "payments tables truncated"
fi
say "starting stack with fresh metrics history"
docker compose -f "$ROOT/docker-compose.yml" up -d --wait
docker compose -f "$ROOT/docker-compose.yml" up -d --force-recreate --renew-anon-volumes prometheus >/dev/null 2>&1
ok "prometheus recreated with an empty TSDB"
rm -f "$ROOT"/state/incident.json.tmp
if mcp_running; then ok "MCP server still running"; else start_mcp; fi
say "waiting for a healthy baseline (needs ~30s of traffic history)"
forgesre check --expect healthy --timeout "${1:-120}"
ok "baseline healthy — ready to ./scripts/trigger-incident.sh"
