#!/usr/bin/env bash
# Configure TrueForge for the read-only ForgeSRE investigation milestone.
# Pass --full to configure the existing action-capable `forgesre` agent instead.
source "$(dirname "$0")/lib.sh"
require_env
if [[ "${1:-}" == "--full" ]]; then
  full_profile=true
elif [[ $# -gt 0 ]]; then
  die "usage: $0 [--full]"
else
  full_profile=false
fi
curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" >/dev/null \
  || die "TrueForge is not running — ./scripts/start-trueforge.sh"
if [[ "$full_profile" == true ]]; then
  forgesre trueforge-setup --full
else
  forgesre trueforge-setup --read-only
fi
