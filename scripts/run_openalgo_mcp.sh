#!/usr/bin/env bash
# Launch OpenAlgo MCP for Vibe Trading with the correct cwd and venv.
# Must run from openalgo/ so `import openalgo` resolves to the pip SDK, not the repo folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENALGO_DIR="$ROOT/openalgo"
PY="$OPENALGO_DIR/.venv/bin/python"
MCPSERVER="$OPENALGO_DIR/mcp/mcpserver.py"

if [[ ! -f "$MCPSERVER" ]]; then
  echo "OpenAlgo MCP server not found at $MCPSERVER" >&2
  echo "Initialize submodules: git submodule update --init --recursive openalgo" >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  echo "OpenAlgo MCP requires openalgo/.venv with dependencies installed." >&2
  echo "  cd openalgo && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

cd "$OPENALGO_DIR"

# Load trade-stack secrets (OpenAlgo + Alpaca) when present.
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export TRADE_INTEGRATIONS_SKIP_APPLY=1
export PYTHONPATH="$ROOT/integrations:$ROOT/tradingagents${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" mcp/mcpserver.py "$@"
