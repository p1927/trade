#!/usr/bin/env bash
# Reload app-tier services after code or env changes (keeps hub Docker running).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

TARGET="${1:-app}"
shift || true

STACK_ROOT="$ROOT"
stack_load_env

_reload_env() {
  echo "[stack] syncing ports + Vibe env from root .env ..."
  local py
  py="$(stack_pick_python)"
  "$py" "$ROOT/scripts/sync_stack_ports.py" --apply || true
  "$py" "$ROOT/scripts/setup_vibe.py" || true
  stack_validate_ports_registry || true
  stack_load_env
}

_reload_app() {
  stack_with_lock _reload_app_inner
}

_reload_app_inner() {
  stack_refuse_if_dev_mode
  echo "[stack] restarting OpenAlgo + Vibe API + Vibe UI ..."
  local log_dir ok=0
  log_dir="$(stack_log_dir)"
  if [[ "${STACK_DEV:-0}" == "1" || "${STACK_DEV_RELOAD:-0}" == "1" ]]; then
    export STACK_DEV_RELOAD=1
    export STACK_DEV_FLASK_DEBUG=1
  fi
  stack_stop_claimed "Vibe UI" "vibe-ui" "$log_dir/vibe-ui.pid" "$(stack_vibe_ui_port)"
  stack_stop_claimed "Vibe API" "vibe-api" "$log_dir/vibe-api.pid" "$(stack_vibe_api_port)"
  stack_stop_claimed "OpenAlgo" "openalgo" "$log_dir/openalgo.pid" "$(stack_openalgo_port)"
  stack_kill_openalgo_ws_proxy
  stack_start_openalgo || ok=1
  stack_start_vibe_api || ok=1
  stack_start_vibe_ui || ok=1
  return "$ok"
}

case "$TARGET" in
  env)
    _reload_env
    _reload_app
    ;;
  app)
    _reload_app
    ;;
  nautilus)
    stack_reconcile_nautilus_watch_pid
    local watch_pid
    watch_pid="$(stack_read_pid "$(stack_log_dir)/nautilus-watch.pid")"
    if stack_nautilus_pid_valid "$watch_pid"; then
      echo "[stack] Nautilus watch already running (pid $watch_pid)"
      stack_sync_nautilus_claim
    else
      echo "[stack] restarting Nautilus watch ..."
      stack_stop_nautilus_watch
      stack_ensure_nautilus_watch || true
    fi
    ;;
  hub)
    echo "[stack] ensuring hub Docker tier ..."
    stack_ensure_dependencies hub || exit 1
    stack_verify_dependencies hub || exit 1
    ;;
  all)
    _reload_env
    _reload_app
    stack_stop_nautilus_watch
    stack_ensure_nautilus_watch || true
    ;;
  -h|--help|help)
    cat <<'EOF'
Usage: trade reload [target]

Targets:
  app       Restart OpenAlgo + Vibe API + UI (default)
  env       Sync ports/.env into Vibe + restart app tier
  nautilus  Restart Nautilus watch node only
  hub       Ensure/restart hub Docker (Timescale, Redis, SearXNG)
  all       env + app + nautilus

Examples:
  trade reload              # after Vibe/OpenAlgo Python edits (if not using trade dev)
  trade reload env          # after editing root .env or stack/ports.yaml
  trade reload nautilus     # after nautilus_openalgo_bridge edits
EOF
    exit 0
    ;;
  *)
    echo "Unknown reload target: $TARGET (try: app, env, nautilus, hub, all)" >&2
    exit 1
    ;;
esac

stack_print_ready
stack_write_instance_manifest
if ! stack_status_vibe_stack; then
  echo "[stack] reload finished with failures — see log/" >&2
  exit 1
fi
