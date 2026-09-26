#!/usr/bin/env bash
# Stop everything (keeps the database volume; use reset-demo.sh for a clean slate).
source "$(dirname "$0")/lib.sh"
stop_mcp
"${COMPOSE[@]}" down
ok "demo stopped"
