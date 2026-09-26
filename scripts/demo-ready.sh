#!/usr/bin/env bash
# One reliable pre-flight command before recording or presenting ForgeSRE.
# It brings up the complete stack, saves the TrueForge agent, restores a healthy
# baseline, and then checks every dependency the audience will see.
set -euo pipefail

source "$(dirname "$0")/lib.sh"

say "preparing the ForgeSRE demo"
"$ROOT/scripts/up.sh"
"$ROOT/scripts/doctor.sh"

cat <<MSG

$(ok "demo ready — start recording")
  1. Open TrueForge:       ${TRUEFORGE_URL:-http://localhost:8790} → Agents → forgesre → Try
  2. Open Mission Control: http://127.0.0.1:${FORGESRE_MCP_PORT:-18900}/dashboard
  3. Trigger the incident: ./scripts/trigger-incident.sh
  4. Wait for red alerts:  ./scripts/verify-incident.sh 150
  5. Prompt the agent:     Checkout is failing in production. Investigate and recover the service.

Before clicking Allow, make sure the TrueForge approval panel and Mission Control are both visible.
MSG
