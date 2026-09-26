#!/usr/bin/env bash
# Run the ForgeSRE agent inside TrueForge from the terminal (streams the real session).
#   ./scripts/run-agent.sh                 # asks you to approve/deny RED actions
#   ./scripts/run-agent.sh --deny          # scripted denial (failure acceptance test)
#   ./scripts/run-agent.sh --approve       # scripted approval (automated e2e test)
source "$(dirname "$0")/lib.sh"
require_env
PROMPT="${FORGESRE_PROMPT:-Production checkout failures are being reported. Investigate the incident, determine the root cause, take safe recovery actions, and restore the system.}"
forgesre agent-run --prompt "$PROMPT" "$@"
