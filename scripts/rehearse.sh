#!/usr/bin/env bash
# Try the full flow WITHOUT a model API key: a scripted stand-in model drives the saved agent,
# TrueForge does everything else for real, and you click Allow / Deny in the TrueForge UI.
source "$(dirname "$0")/lib.sh"
require_env
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/agents" | grep -q '"forgesre"' \
  || die "run ./scripts/setup-trueforge.sh first (it can use MODEL_PROVIDER=custom for a keyless setup)"
if [[ -f "$RUN_DIR/rehearse.pid" ]] && kill -0 "$(cat "$RUN_DIR/rehearse.pid")" 2>/dev/null; then
  kill "$(cat "$RUN_DIR/rehearse.pid")"; sleep 1
fi
say "resetting and triggering the incident"
"$ROOT/scripts/reset-demo.sh" >/dev/null
"$ROOT/scripts/trigger-incident.sh" >/dev/null
"$ROOT/scripts/verify-incident.sh" 150 >/dev/null
say "starting rehearsal session (scripted stand-in model; real TrueForge, tools, sandbox, approval)"
VIRTUAL_ENV= nohup uv run --quiet --project "$ROOT/mcp-server" python "$ROOT/scripts/dev/rehearse.py" >"$RUN_DIR/rehearse.log" 2>&1 &
echo $! >"$RUN_DIR/rehearse.pid"
for _ in $(seq 1 180); do
  grep -q "PAUSED" "$RUN_DIR/rehearse.log" 2>/dev/null && break
  kill -0 "$(cat "$RUN_DIR/rehearse.pid")" 2>/dev/null || die "rehearsal failed; see $RUN_DIR/rehearse.log"
  sleep 1
done
grep "PAUSED" "$RUN_DIR/rehearse.log" || die "session did not reach the approval gate; see $RUN_DIR/rehearse.log"
ok "TrueForge is holding rollback_deployment for you"
echo "   1. open the session URL above → click Allow (or Deny)"
echo "   2. watch http://127.0.0.1:${FORGESRE_MCP_PORT:-18900}/dashboard"
echo "   3. report lands in artifacts/incidents/"
