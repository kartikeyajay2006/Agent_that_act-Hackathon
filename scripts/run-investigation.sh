#!/usr/bin/env bash
# Run a read-only investigation in the saved TrueForge investigator agent.
source "$(dirname "$0")/lib.sh"
require_env
PROMPT="${FORGESRE_PROMPT:-Production checkout failures are being reported. Investigate the incident and report evidence, likely causes, and impact. Do not change infrastructure.}"
forgesre investigator-preflight || die "read-only investigator preflight failed; run ./scripts/setup-trueforge.sh"
forgesre investigate --prompt "$PROMPT"
