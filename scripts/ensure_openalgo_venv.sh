#!/usr/bin/env bash
# Bootstrap OpenAlgo Python venv (broker UI + MCP server deps).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENALGO="${OPENALGO_DIR:-$ROOT/openalgo}"
PY="${OPENALGO}/.venv/bin/python"
PIP="${OPENALGO}/.venv/bin/pip"

log() { echo "[openalgo-venv] $*"; }

if [[ ! -d "$OPENALGO" ]]; then
  echo "OpenAlgo submodule missing at $OPENALGO" >&2
  echo "Run: git submodule update --init --recursive openalgo" >&2
  exit 1
fi

if [[ ! -f "$OPENALGO/requirements.txt" ]]; then
  echo "Missing $OPENALGO/requirements.txt" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to create the OpenAlgo venv" >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  log "Creating venv at $OPENALGO/.venv ..."
  python3 -m venv "$OPENALGO/.venv"
fi

log "Installing OpenAlgo requirements ..."
"$PIP" install -q --upgrade pip
"$PIP" install -q -r "$OPENALGO/requirements.txt"

log "Verifying OpenAlgo MCP imports ..."
ROOT="$ROOT" "$PY" "$ROOT/scripts/setup_vibe.py" --verify

log "OpenAlgo venv ready at $OPENALGO/.venv"
