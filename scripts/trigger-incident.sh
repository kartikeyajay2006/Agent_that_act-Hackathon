#!/usr/bin/env bash
# The incident: the release pipeline ships payment-service v2 to production.
source "$(dirname "$0")/lib.sh"
require_env
say "release pipeline: deploying payment-service v2"
forgesre deploy payment-service v2
ok "v2 is live and receiving checkout traffic — run ./scripts/verify-incident.sh"
