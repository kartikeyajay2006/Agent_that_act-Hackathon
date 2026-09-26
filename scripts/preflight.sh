#!/usr/bin/env bash
# Verify local prerequisites and configuration before a TrueForge rehearsal.
source "$(dirname "$0")/lib.sh"

require_env

for bin in docker uv node curl; do
  command -v "$bin" >/dev/null 2>&1 || die "missing prerequisite: $bin"
done

docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "Docker Desktop is not running or is not accessible"

node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&b>=14)?0:1)' \
  || die "Node.js >= 22.14 is required (found $(node --version))"

for key in MODEL_PROVIDER MODEL_ID MODEL_API_KEY FORGESRE_MCP_TOKEN; do
  value="${!key:-}"
  [[ -n "$value" ]] || die "$key is empty in .env"
done

case "${MODEL_API_KEY}" in
  change-me|your-*|'<*>') die "MODEL_API_KEY still contains a placeholder" ;;
esac

case "$(uname -s)" in
  Linux*|Darwin*) ;;  # TrueForge ships a local sandbox for Linux (bubblewrap) and macOS (Seatbelt)
  *) [[ -n "${DAYTONA_API_KEY:-}" ]] || die "this OS has no TrueForge local sandbox; set DAYTONA_API_KEY (or use WSL2)" ;;
esac

if curl -fs "http://${FORGESRE_MCP_HOST:-127.0.0.1}:${FORGESRE_MCP_PORT:-18900}/healthz" >/dev/null 2>&1; then
  ok "ForgeSRE MCP server is running"
else
  warn "ForgeSRE MCP server is not running yet; ./scripts/up.sh (or start-demo.sh) starts it"
fi

if curl -fs "${TRUEFORGE_URL:-http://localhost:8790}/api/v1/capabilities" >/dev/null 2>&1; then
  ok "TrueForge is reachable at ${TRUEFORGE_URL:-http://localhost:8790}"
else
  warn "TrueForge is not running; start it with ./scripts/start-trueforge.sh"
fi

ok "local prerequisites and model configuration are ready"
