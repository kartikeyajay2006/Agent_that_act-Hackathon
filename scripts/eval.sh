#!/usr/bin/env bash
# Score the ForgeSRE agent end to end through TrueForge with the configured model.
#   ./scripts/eval.sh                              # approve, deny, healthy (one run each)
#   ./scripts/eval.sh --scenario approve --runs 3  # repeatability
# Each run resets the demo, plays the human at the approval gate, and scores the session.
# Results: artifacts/evals/eval-<timestamp>.md / .json
source "$(dirname "$0")/lib.sh"
require_env
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/agents" | grep -q '"forgesre"' \
  || die "run ./scripts/setup-trueforge.sh first"
mcp_running || start_mcp
forgesre eval "$@"
