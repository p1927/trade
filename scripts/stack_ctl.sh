#!/usr/bin/env bash
# Canonical stack lifecycle — start, stop, restart, status, preflight.
# All human and agent entry points should use: trade up | down | restart | status | doctor
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
mkdir -p "$(stack_log_dir)"
stack_load_env

usage() {
  cat <<'EOF'
stack_ctl — internal stack lifecycle (use ./trade instead)

Commands:
  up              Preflight + start/heal background stack (OpenAlgo + Vibe + hub)
  down [--all]    Stop stack ( --all: + hub Docker + tunnels )
  restart [--force]  Heal (default) or full stop+start (--force)
  ensure          Start only services that are down (no preflight)
  status          Vibe + hub Docker status
  preflight       Dependency checks only (no start/stop)

Public interface:
  trade up                 Background stack (recommended)
  trade down               Stop stack
  trade restart            Heal — start missing services
  trade restart --force    Full reset
  trade status             Health summary
  trade doctor             Preflight + hub integration checks
  trade dev                Dev mode with auto-reload (foreground UI)
EOF
}

stack_ctl_up() {
  stack_with_lock stack_ctl_up_inner
}

stack_ctl_up_inner() {
  stack_refuse_if_dev_mode
  echo "[stack] starting background stack ..."
  stack_preflight_start || {
    echo "[stack] fix issues above, then: trade doctor" >&2
    exit 1
  }
  if ! stack_ensure_vibe_stack; then
    echo "[stack] one or more services failed to start — see log/" >&2
    stack_status_vibe_stack || true
    exit 1
  fi
  stack_write_instance_manifest
  stack_print_ready
  stack_status_vibe_stack
}

stack_ctl_down() {
  stack_with_lock stack_stop_vibe_stack "$@"
  echo "[stack] stack stopped"
}

stack_ctl_restart() {
  local force=0
  for arg in "$@"; do
    case "$arg" in
      --force|-f) force=1 ;;
    esac
  done

  stack_with_lock stack_ctl_restart_inner "$force"
}

stack_ctl_restart_inner() {
  local force="${1:-0}"
  if (( ! force )); then
    stack_refuse_if_dev_mode
  fi
  if (( force )); then
    echo "[stack] full restart (--force): stop then preflight + start ..."
    stack_stop_vibe_stack
    stack_preflight_start || {
      echo "[stack] preflight failed after stop — fix issues, then: trade doctor" >&2
      exit 1
    }
  else
    echo "[stack] heal: preflight + start only what's down ..."
    stack_preflight_start || {
      echo "[stack] preflight failed — try: trade restart --force" >&2
      exit 1
    }
  fi

  if ! stack_ensure_vibe_stack; then
    echo "[stack] one or more services failed — see log/" >&2
    stack_status_vibe_stack || true
    exit 1
  fi
  stack_write_instance_manifest
  stack_print_ready
  stack_status_vibe_stack
}

stack_ctl_ensure() {
  stack_with_lock stack_ctl_ensure_inner
}

stack_ctl_ensure_inner() {
  stack_refuse_if_dev_mode
  stack_reconcile_stale_dev_mode
  echo "[stack] ensuring stack (no full preflight) ..."
  if ! stack_ensure_vibe_stack; then
    echo "[stack] ensure failed — try: trade up" >&2
    exit 1
  fi
  stack_write_instance_manifest
  stack_print_ready
  stack_status_vibe_stack
}

stack_ctl_status() {
  stack_status_vibe_stack
}

stack_ctl_preflight() {
  stack_preflight_start
}

cmd="${1:-}"
shift || true

case "$cmd" in
  up|start)
    stack_ctl_up
    ;;
  down|stop)
    stack_ctl_down "$@"
    ;;
  restart)
    stack_ctl_restart "$@"
    ;;
  ensure|heal)
    stack_ctl_ensure
    ;;
  status)
    stack_ctl_status
    ;;
  preflight|doctor-lite)
    stack_ctl_preflight
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    echo "Unknown stack_ctl command: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
