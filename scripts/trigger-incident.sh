#!/usr/bin/env bash
# Trigger the configured demo incident; omit the name to use config/incidents.yaml's default.
source "$(dirname "$0")/lib.sh"
require_env
[[ $# -le 1 ]] || die "usage: $0 [scenario-name]"
if [[ $# -eq 1 ]]; then
  forgesre trigger-incident "$1"
else
  forgesre trigger-incident
fi
ok "configured incident release is live — run ./scripts/verify-incident.sh"
