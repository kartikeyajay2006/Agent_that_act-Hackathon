#!/usr/bin/env bash
# Rehearse the real model three times, or use --scripted for a keyless contract run.
source "$(dirname "$0")/lib.sh"
require_env

if [[ "${1:-}" == "--scripted" ]]; then
  curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/agents" | grep -q '"forgesre"' \
    || die "run ./scripts/setup-trueforge.sh first"
  if [[ -f "$RUN_DIR/rehearse.pid" ]] && kill -0 "$(cat "$RUN_DIR/rehearse.pid")" 2>/dev/null; then
    kill "$(cat "$RUN_DIR/rehearse.pid")"
    sleep 1
  fi
  say "scripted rehearsal: resetting and triggering the incident"
  "$ROOT/scripts/reset-demo.sh" >/dev/null
  "$ROOT/scripts/trigger-incident.sh" >/dev/null
  "$ROOT/scripts/verify-incident.sh" 150 >/dev/null
  say "scripted rehearsal: starting stand-in model"
  VIRTUAL_ENV= nohup uv run --quiet --project "$ROOT/mcp-server" python "$ROOT/scripts/dev/rehearse.py" \
    >"$RUN_DIR/rehearse.log" 2>&1 &
  echo $! >"$RUN_DIR/rehearse.pid"
  for _ in $(seq 1 180); do
    grep -q "PAUSED" "$RUN_DIR/rehearse.log" 2>/dev/null && break
    kill -0 "$(cat "$RUN_DIR/rehearse.pid")" 2>/dev/null || die "rehearsal failed; see $RUN_DIR/rehearse.log"
    sleep 1
  done
  grep "PAUSED" "$RUN_DIR/rehearse.log" || die "session did not reach the approval gate; see $RUN_DIR/rehearse.log"
  ok "TrueForge is holding rollback_deployment for you"
  echo "   1. open the session URL above → click Allow or Deny"
  echo "   2. watch http://127.0.0.1:${FORGESRE_MCP_PORT:-18900}/dashboard"
  echo "   3. report lands in artifacts/incidents/"
  exit 0
fi

RUNS="${1:-3}"
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || die "usage: ./scripts/rehearse.sh [positive run count]"

"$ROOT/scripts/preflight.sh"

cat <<'EOF'

Rehearsal prompt:
Production checkout failures are being reported. Investigate the incident,
determine the root cause, take safe recovery actions, and restore the system.

For each run, keep the TrueForge session visible. Confirm that the trace shows
the generated sandbox probe, the incident-diagnostics analyzer, a concise
approval brief, and recovery after Allow.
EOF

for run in $(seq 1 "$RUNS"); do
  say "rehearsal $run/$RUNS: resetting to the healthy baseline"
  "$ROOT/scripts/reset-demo.sh" 150
  say "rehearsal $run/$RUNS: triggering the v2 incident"
  "$ROOT/scripts/trigger-incident.sh"
  "$ROOT/scripts/verify-incident.sh" 120
  read -r -p "Open TrueForge, send the prompt above, approve rollback, then press Enter to continue... " _
done

ok "completed $RUNS rehearsal preparations; review the TrueForge session traces and reports"
