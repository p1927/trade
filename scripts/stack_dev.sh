#!/usr/bin/env bash
# Development mode: hub Docker + OpenAlgo/Vibe with auto-reload, foreground Vite UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
export STACK_DEV=1
export STACK_DEV_RELOAD=1
export STACK_DEV_FLASK_DEBUG=1
export STACK_PORTS_STRICT=1

stack_load_env
stack_print_ports_summary

echo "[stack] dev mode — code + .env changes:"
echo "  Vibe API     uvicorn --reload (integrations/, vibetrading/agent/)"
echo "  Vibe UI      Vite HMR"
echo "  OpenAlgo     FLASK_DEBUG=1 (openalgo/)"
echo "  Env change   trade reload env"
echo "  Nautilus     trade reload nautilus (manual restart required)"
echo ""

if ! stack_validate_ports_registry; then
  exit 1
fi
stack_check_port_listeners || exit 1

stack_ensure_hub_docker || true
stack_ensure_hub_storage || true
stack_ensure_vibe_config || true

# OpenAlgo + Vibe API in background with reload; Vite foreground via start.sh handoff.
if ! stack_start_openalgo; then
  echo "[stack] OpenAlgo failed to start" >&2
  exit 1
fi

export STACK_DEV_FOREGROUND_VIBE=1
exec "$ROOT/start.sh" --dev-ui "$@"
