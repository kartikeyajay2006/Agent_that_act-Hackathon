#!/usr/bin/env bash
# One-time setup: prerequisites, .env with random secrets, Python env, images.
source "$(dirname "$0")/lib.sh"

say "checking prerequisites"
for bin in docker uv node curl openssl; do
  command -v "$bin" >/dev/null || die "missing prerequisite: $bin"
done
docker compose version >/dev/null || die "docker compose v2 is required"
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&b>=14)?0:1)' \
  || die "TrueForge needs Node.js >= 22.14 (found $(node --version))"
missing=()
for bin in bwrap socat rg; do command -v "$bin" >/dev/null || missing+=("$bin"); done
if ((${#missing[@]})); then
  warn "TrueForge local sandbox needs: ${missing[*]} (or set DAYTONA_API_KEY to use Daytona)"
fi
ok "prerequisites present"

[[ -f .env ]] || cp .env.example .env
# Replace placeholder or empty secrets with random values (works whether or not .env was copied by hand).
fill_secret() {
  local key=$1 bytes=$2 current
  current=$(grep -E "^${key}=" .env | cut -d= -f2-)
  if [[ -z "$current" || "$current" == change-me* ]]; then
    sed -i.bak "s/^${key}=.*/${key}=$(openssl rand -hex "$bytes")/" .env && rm -f .env.bak  # GNU + BSD sed
    ok "generated ${key}"
  fi
}
fill_secret POSTGRES_PASSWORD 16
fill_secret MONITOR_PASSWORD 16
fill_secret FORGESRE_MCP_TOKEN 24
grep -qE "^MODEL_API_KEY=.+" .env || warn "MODEL_API_KEY is empty — add your model provider key to .env before ./scripts/setup-trueforge.sh"

say "installing MCP server environment (uv)"
(cd mcp-server && VIRTUAL_ENV= uv sync --quiet)
ok "python environment ready"

say "building demo images"
"${COMPOSE[@]}" pull --quiet postgres postgres-exporter prometheus
"${COMPOSE[@]}" build --quiet
ok "images built"
ok "setup complete — next: ./scripts/start-demo.sh"
