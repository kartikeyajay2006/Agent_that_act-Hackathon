#!/usr/bin/env bash
# Run TrueForge (standalone/local mode) with the settings ForgeSRE needs.
#   - pinned version for reproducibility
#   - data kept in ./.trueforge (SQLite)
#   - outbound URL protections remain at TrueForge defaults
source "$(dirname "$0")/lib.sh"
require_env

TRUEFORGE_VERSION="${TRUEFORGE_VERSION:-0.2.1}"
mkdir -p "$ROOT/.trueforge"
export SQLITE_PATH="$ROOT/.trueforge/trueforge.sqlite"
export PORT="${TRUEFORGE_PORT:-8790}"
if curl -fs "http://localhost:$PORT/api/v1/capabilities" >/dev/null 2>&1; then
  ok "TrueForge already running on http://localhost:$PORT"
  exit 0
fi

say "starting TrueForge $TRUEFORGE_VERSION on http://localhost:$PORT (logs: .trueforge/server.log)"
nohup npx -y "@truefoundry/trueforge@$TRUEFORGE_VERSION" >"$ROOT/.trueforge/server.log" 2>&1 &
echo $! >"$RUN_DIR/trueforge.pid"
for _ in $(seq 1 120); do
  if curl -fs "http://localhost:$PORT/api/v1/capabilities" >/dev/null 2>&1; then
    grep -q "Local sandbox fallback is available" "$ROOT/.trueforge/server.log" \
      && ok "TrueForge local sandbox provider available" \
      || warn "local sandbox unavailable — configure Daytona (DAYTONA_API_KEY) for sandbox execution"
    ok "TrueForge up — UI http://localhost:$PORT"
    exit 0
  fi
  sleep 1
done
die "TrueForge did not start; see .trueforge/server.log"
