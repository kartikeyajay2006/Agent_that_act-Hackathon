#!/usr/bin/env bash
# Exit 0 once any configured incident alert is firing.
source "$(dirname "$0")/lib.sh"
forgesre check --expect incident --timeout "${1:-120}"
