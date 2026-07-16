#!/usr/bin/env bash
# Start ED-ALPHA Docker stack (db + backend + frontend + batch).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ED_ALPHA_DIR="$ROOT/ed-alpha"

if [[ ! -d "$ED_ALPHA_DIR" ]]; then
  echo "ed-alpha submodule missing. Run: git submodule update --init ed-alpha" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  if [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
  fi
fi

cd "$ED_ALPHA_DIR"
docker compose up -d db
docker compose up -d backend
docker compose --profile batch up -d batch
echo "ED-ALPHA: http://localhost:8000 (API)  optional UI: docker compose up -d frontend"
