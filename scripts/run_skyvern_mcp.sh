#!/usr/bin/env bash
# Launch Skyvern MCP for Vibe Trading (stdio transport).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export SKYVERN_BASE_URL="${SKYVERN_BASE_URL:-http://localhost:8000}"
export SKYVERN_API_PREFIX="${SKYVERN_API_PREFIX:-/api/v1}"

PY="${SKYVERN_MCP_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ROOT/openalgo/.venv/bin/python" ]]; then
    PY="$ROOT/openalgo/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
  else
    echo "Skyvern MCP requires python3 with skyvern installed (pip install 'skyvern[server]')" >&2
    exit 1
  fi
fi

if ! "$PY" -c "import skyvern" 2>/dev/null; then
  echo "Skyvern package not found. Install: pip install 'skyvern[server]'" >&2
  exit 1
fi

if [[ -z "${SKYVERN_API_KEY:-}" ]]; then
  echo "Warning: SKYVERN_API_KEY not set — copy from http://localhost:8080/settings after docker compose up" >&2
fi

exec "$PY" -m skyvern run mcp
