#!/usr/bin/env bash
# Exit 0 when checkout is healthy: synthetic >=95%, error rate <=5%, no firing alerts.
source "$(dirname "$0")/lib.sh"
forgesre check --expect healthy --timeout "${1:-90}"
