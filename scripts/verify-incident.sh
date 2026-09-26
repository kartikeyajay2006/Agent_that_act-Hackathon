#!/usr/bin/env bash
# Exit 0 once the incident is observable: failing synthetics, error rate >=20%, alert firing.
source "$(dirname "$0")/lib.sh"
forgesre check --expect incident --timeout "${1:-120}"
