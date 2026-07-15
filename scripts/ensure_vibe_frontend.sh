#!/usr/bin/env bash
# Install npm deps for the Vibe Trading frontend (vibetrading/ submodule).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="${VIBE_FRONTEND_DIR:-$ROOT/vibetrading/frontend}"

log() { echo "[vibe-frontend] $*"; }

if [[ ! -f "$FRONTEND/package.json" ]]; then
  echo "Vibe frontend not found at $FRONTEND" >&2
  echo "Initialize submodules: git submodule update --init --recursive vibetrading" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required for the Vibe Web UI." >&2
  echo "Install Node 20+, then re-run: ./scripts/ensure_vibe_frontend.sh" >&2
  exit 1
fi

if [[ ! -x "$FRONTEND/node_modules/.bin/vite" ]]; then
  log "Installing npm dependencies in $FRONTEND ..."
  (cd "$FRONTEND" && npm install)
fi

log "Frontend ready at $FRONTEND"
