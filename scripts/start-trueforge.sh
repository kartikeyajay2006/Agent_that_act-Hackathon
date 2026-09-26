#!/usr/bin/env bash
# Run TrueForge (standalone/local mode) with the settings ForgeSRE needs.
#   - pinned version for reproducibility
#   - data kept in ./.trueforge (SQLite)
#   - outbound URL protections remain at TrueForge defaults
source "$(dirname "$0")/lib.sh"
require_env

TRUEFORGE_VERSION="${TRUEFORGE_VERSION:-0.2.1}"
TRUEFORGE_STATE_DIR="${TRUEFORGE_STATE_DIR:-$ROOT/.trueforge}"
mkdir -p "$TRUEFORGE_STATE_DIR"
export SQLITE_PATH="${SQLITE_PATH:-$TRUEFORGE_STATE_DIR/trueforge.sqlite}"
export PORT="${TRUEFORGE_PORT:-8790}"
if curl -fs "http://localhost:$PORT/api/v1/capabilities" >/dev/null 2>&1; then
  ok "TrueForge already running on http://localhost:$PORT"
  exit 0
fi

LOG_PATH="$TRUEFORGE_STATE_DIR/server.log"
say "starting TrueForge $TRUEFORGE_VERSION on http://localhost:$PORT (logs: $LOG_PATH)"
nohup npx -y "@truefoundry/trueforge@$TRUEFORGE_VERSION" >"$LOG_PATH" 2>&1 &
echo $! >"$RUN_DIR/trueforge.pid"
for _ in $(seq 1 120); do
  if curl -fs "http://localhost:$PORT/api/v1/capabilities" >/dev/null 2>&1; then
    grep -q "Local sandbox fallback is available" "$LOG_PATH" \
      && ok "TrueForge local sandbox provider available" \
      || warn "local sandbox unavailable — configure Daytona (DAYTONA_API_KEY) for sandbox execution"
    ok "TrueForge up — UI http://localhost:$PORT"
    exit 0
  fi
  sleep 1
done
die "TrueForge did not start; see $LOG_PATH"
