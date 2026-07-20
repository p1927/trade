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

stack_dev_prepare_inner() {
  local log_dir
  log_dir="$(stack_log_dir)"
  echo "[stack] dev mode — stopping background daemon tier (heal + app services) ..."
  stack_stop_heal_daemon
  stack_stop_data_worker
  stack_stop_dev_nautilus_heal
  stack_stop_claimed "Vibe UI" "vibe-ui" "$log_dir/vibe-ui.pid" "$(stack_vibe_ui_port)"
  stack_stop_claimed "Vibe API" "vibe-api" "$log_dir/vibe-api.pid" "$(stack_vibe_api_port)"
  stack_stop_claimed "OpenAlgo" "openalgo" "$log_dir/openalgo.pid" "$(stack_openalgo_port)"
  stack_kill_openalgo_ws_proxy
  stack_reconcile_stale_claims
  stack_wait_port_free "$(stack_vibe_ui_port)" 15 || true
  stack_wait_port_free "$(stack_vibe_api_port)" 15 || true
  stack_wait_port_free "$(stack_openalgo_port)" 15 || true
  stack_set_stack_mode "dev"
}

stack_load_env
stack_reconcile_all
stack_print_ports_summary

echo "[stack] dev mode — code + .env changes:"
echo "  Command      ./trade dev   (from repo root — not bare 'trade')"
echo "  Vibe API     uvicorn --reload (integrations/, vibetrading/agent/)"
echo "  Vite UI      Vite HMR"
echo "  OpenAlgo     FLASK_DEBUG=1 (openalgo/)"
echo "  Env change   trade reload env"
echo "  Nautilus     runs independently of trade dev; trade reload nautilus after handoff/watch_spec changes; trade status shows watch PID"
echo ""

if ! stack_validate_ports_registry; then
  exit 1
fi

stack_preflight_dependencies --strict || {
  echo "[stack] dev blocked: fix hub dependencies before starting (Docker, compose file)" >&2
  exit 1
}

stack_bootstrap_session "dev" "clean"

# Stop daemon app tier before strict port check (otherwise ports look "foreign").
stack_with_lock stack_dev_prepare_inner

stack_reconcile_nautilus_watch_pid

stack_check_port_listeners || exit 1

stack_ensure_dependencies hub || {
  echo "[stack] dev blocked: hub Docker tier failed to start" >&2
  stack_clear_stack_mode
  exit 1
}
stack_verify_dependencies hub || {
  echo "[stack] dev blocked: hub Docker verification failed" >&2
  stack_clear_stack_mode
  exit 1
}

stack_ensure_hub_storage || true
stack_ensure_vibe_config || true

# OpenAlgo in background with FLASK_DEBUG; Vite + reload API via start.sh handoff.
if ! stack_start_openalgo; then
  echo "[stack] OpenAlgo failed to start" >&2
  stack_clear_stack_mode
  exit 1
fi

stack_ensure_nautilus_watch || {
  echo "[stack] dev blocked: Nautilus watch required but failed to start" >&2
  stack_clear_stack_mode
  exit 1
}
stack_start_dev_nautilus_heal || true

export STACK_DEV_FOREGROUND_VIBE=1
echo "[stack] dev mode running — keep THIS terminal open (closing it stops the stack)"
exec "$ROOT/start.sh" --dev-ui "$@"
