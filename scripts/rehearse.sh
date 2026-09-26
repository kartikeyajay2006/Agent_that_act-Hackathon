#!/usr/bin/env bash
# Prepare repeated incident rehearsals. The agent run remains human-visible so
# the approval brief and Allow/Deny decision can be checked in TrueForge.
source "$(dirname "$0")/lib.sh"
require_env

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
