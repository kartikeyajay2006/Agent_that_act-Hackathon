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

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" .env
  sed -i "s/^MONITOR_PASSWORD=.*/MONITOR_PASSWORD=$(openssl rand -hex 16)/" .env
  sed -i "s/^FORGESRE_MCP_TOKEN=.*/FORGESRE_MCP_TOKEN=$(openssl rand -hex 24)/" .env
  ok "created .env with random database passwords — add MODEL_API_KEY before running the agent"
else
  ok ".env already exists"
fi

say "installing MCP server environment (uv)"
(cd mcp-server && VIRTUAL_ENV= uv sync --quiet)
ok "python environment ready"

say "building demo images"
"${COMPOSE[@]}" pull --quiet postgres postgres-exporter prometheus
"${COMPOSE[@]}" build --quiet
ok "images built"
ok "setup complete — next: ./scripts/start-demo.sh"
