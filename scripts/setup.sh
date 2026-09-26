#!/usr/bin/env bash
# One-time setup: prerequisites, .env with random secrets, Python env, images.
source "$(dirname "$0")/lib.sh"

say "checking prerequisites"
for bin in docker uv node curl python3; do
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

# Do not infer that a database is new if Docker is unavailable: that could
# replace credentials for an existing persistent volume.
docker info >/dev/null 2>&1 || die "cannot inspect Docker state; start Docker before initializing local credentials"
database_exists=false
if docker volume inspect forgesre_pgdata >/dev/null 2>&1 || docker container inspect forgesre-postgres >/dev/null 2>&1; then
  database_exists=true
fi
if [[ "$database_exists" == true ]]; then
  python3 scripts/ensure-env.py --database-exists
else
  python3 scripts/ensure-env.py
fi

say "installing MCP server environment (uv)"
(cd mcp-server && VIRTUAL_ENV= uv sync --quiet)
ok "python environment ready"

say "building demo images"
"${COMPOSE[@]}" pull --quiet postgres postgres-exporter prometheus
"${COMPOSE[@]}" build --quiet
ok "images built"
ok "setup complete — next: ./scripts/start-demo.sh"
