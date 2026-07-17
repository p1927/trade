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

export SKYVERN_BASE_URL="${SKYVERN_BASE_URL:-http://localhost:8010}"
export SKYVERN_API_PREFIX="${SKYVERN_API_PREFIX:-/v1}"

# Auto-read self-hosted API key from Docker-generated credentials.toml
if [[ -z "${SKYVERN_API_KEY:-}" ]]; then
  for cred_file in \
    "$ROOT/.skyvern-data/.skyvern/credentials.toml" \
    "$ROOT/.skyvern/credentials.toml"; do
    if [[ -f "$cred_file" ]]; then
      SKYVERN_API_KEY="$(sed -n 's/.*cred[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$cred_file" | head -n1)"
      if [[ -n "$SKYVERN_API_KEY" ]]; then
        export SKYVERN_API_KEY
        break
      fi
    fi
  done
fi

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
  echo "Warning: Skyvern local API key not found — run scripts/start_skyvern.sh and wait for healthy status" >&2
fi

exec "$PY" -m skyvern run mcp
