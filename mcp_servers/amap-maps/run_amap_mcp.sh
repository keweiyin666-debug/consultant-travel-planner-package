#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

if [ -f "$PROJECT_ROOT/config/amap.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/config/amap.env"
  set +a
fi

if [ -z "${AMAP_WEBSERVICE_KEY:-}" ]; then
  echo "AMAP_WEBSERVICE_KEY is not configured. Create config/amap.env or .env in the project root." >&2
  exit 1
fi

export AMAP_WEBSERVICE_KEY
export AMAP_MAPS_API_KEY="${AMAP_MAPS_API_KEY:-$AMAP_WEBSERVICE_KEY}"

exec npx -y @amap/amap-maps-mcp-server

