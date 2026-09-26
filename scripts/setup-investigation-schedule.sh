#!/usr/bin/env bash
# Create/update the configurable TrueForge schedule for the read-only investigator.
source "$(dirname "$0")/lib.sh"
require_env
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--activate" ) ]]; then
  die "usage: $0 [--activate]"
fi
forgesre trueforge-schedule-setup "$@"
