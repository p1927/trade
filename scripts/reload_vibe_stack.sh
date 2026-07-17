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
  echo "[stack] restarting OpenAlgo + Vibe API + Vibe UI ..."
  local log_dir
  log_dir="$(stack_log_dir)"
  if [[ "${STACK_DEV:-0}" == "1" || "${STACK_DEV_RELOAD:-0}" == "1" ]]; then
    export STACK_DEV_RELOAD=1
    export STACK_DEV_FLASK_DEBUG=1
  fi
  stack_stop_pidfile "Vibe UI" "$log_dir/vibe-ui.pid" "vite --port $(stack_vibe_ui_port)"
  stack_stop_pidfile "Vibe API" "$log_dir/vibe-api.pid" "cli._legacy serve"
  stack_stop_pidfile "OpenAlgo" "$log_dir/openalgo.pid" "openalgo.*app.py"
  stack_kill_port "$(stack_vibe_ui_port)"
  stack_kill_port "$(stack_vibe_api_port)"
  stack_kill_port "$(stack_openalgo_port)"
  stack_kill_port 8765
  stack_start_openalgo
  stack_start_vibe_api
  stack_start_vibe_ui
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
    echo "[stack] restarting Nautilus watch ..."
    stack_stop_nautilus_watch
    stack_ensure_nautilus_watch || true
    ;;
  hub)
    echo "[stack] ensuring hub Docker tier ..."
    stack_ensure_hub_docker || true
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
stack_status_vibe_stack || true
