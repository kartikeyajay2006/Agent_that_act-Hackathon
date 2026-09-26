#!/usr/bin/env bash
# Diagnose a ForgeSRE install: prerequisites, config, and every running component.
source "$(dirname "$0")/lib.sh"
fails=0
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else printf '\033[1;31m✗ %s\033[0m  → %s\n' "$1" "$3"; fails=$((fails+1)); fi; }

say "prerequisites"
check "docker running"                "docker info"                       "start Docker Desktop / the docker service"
check "docker compose v2"             "docker compose version"            "install Docker Compose v2"
check "uv installed"                  "command -v uv"                     "curl -LsSf https://astral.sh/uv/install.sh | sh"
check "node >= 22.14"                 "node -e 'const [a,b]=process.versions.node.split(\".\").map(Number);process.exit(a>22||(a===22&&b>=14)?0:1)'" "install Node.js 22.14+"
if [[ "$(uname)" == "Linux" ]]; then
  check "bwrap/socat/rg (local sandbox)" "command -v bwrap && command -v socat && command -v rg" "install bubblewrap socat ripgrep, or set DAYTONA_API_KEY"
fi

say "configuration"
check ".env present"                  "test -f '$ROOT/.env'"              "./scripts/setup.sh"
check "database secrets set"          "grep -qE '^POSTGRES_PASSWORD=[0-9a-f]{16,}' '$ROOT/.env'" "./scripts/setup.sh"
check "MCP token set"                 "grep -qE '^FORGESRE_MCP_TOKEN=.{16,}' '$ROOT/.env'" "./scripts/setup.sh"
check "model key set"                 "grep -qE '^MODEL_API_KEY=.+' '$ROOT/.env' || grep -qE '^MODEL_PROVIDER=custom' '$ROOT/.env'" "add MODEL_API_KEY to .env"

say "running components"
check "gateway healthy"               "curl -fs http://127.0.0.1:${GATEWAY_HOST_PORT:-18080}/health" "./scripts/start-demo.sh"
check "prometheus up"                 "curl -fs http://127.0.0.1:${PROMETHEUS_HOST_PORT:-19090}/-/ready" "./scripts/start-demo.sh"
check "MCP server up"                 "curl -fs http://127.0.0.1:${FORGESRE_MCP_PORT:-18900}/healthz" "./scripts/restart-mcp.sh"
check "TrueForge up"                  "curl -fs ${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" "./scripts/start-trueforge.sh"
check "TrueForge sees forgesre tools" "curl -fs ${TRUEFORGE_URL:-http://localhost:8790}/api/v1/mcp-servers/forgesre/tools | grep -q rollback_deployment" "./scripts/setup-trueforge.sh"
check "forgesre agent saved"          "curl -fs ${TRUEFORGE_URL:-http://localhost:8790}/api/v1/agents | grep -q '\"forgesre\"'" "./scripts/setup-trueforge.sh"
check "rollback gated in agent spec"  "curl -fs ${TRUEFORGE_URL:-http://localhost:8790}/api/v1/agents | grep -q 'require_approval_for_tools\":\[\"rollback_deployment'" "./scripts/setup-trueforge.sh"

echo
if ((fails)); then die "$fails check(s) failed"; else ok "all checks passed"; fi
