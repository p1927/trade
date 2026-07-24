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
  down [--all|--hub]  Stop stack ( --all: + hub Docker + tunnels; --hub: hub Docker only )
  restart [--force] [--kill-dev]  Heal (default) or full stop+start (--force; --kill-dev stops dev first)
  ensure [--hub-only]  Start only services that are down
  status [--json]   Vibe + hub Docker status (read-only — does not start services)
  preflight       Dependency checks only (no start/stop)

Public interface:
  trade up                 Background stack (recommended)
  trade down               Stop stack
  trade heal [--hub-only]  Heal missing services
  trade restart            Heal — start missing services
  trade restart --force    Full reset
  trade status [--json]    Health summary
  trade doctor             Preflight + hub integration checks
  trade dev                Dev mode with auto-reload (foreground UI)
EOF
}

stack_ctl_up() {
  stack_with_lock stack_ctl_up_inner
}

stack_ctl_up_inner() {
  stack_reconcile_all
  if stack_dev_mode_active; then
    echo "[stack] dev mode is running — keep that terminal open for hot reload." >&2
    echo "[stack] To switch to background daemon: Ctrl+C in the dev terminal, then: ./trade up" >&2
    exit 1
  fi
  stack_refuse_if_dev_mode
  echo "[stack] starting background stack ..."
  stack_preflight_start || {
    echo "[stack] fix issues above, then: trade setup" >&2
    exit 1
  }
  stack_preflight_dependencies --strict || {
    echo "[stack] hub dependency preflight failed — fix Docker/compose, then: trade doctor" >&2
    exit 1
  }
  stack_bootstrap_session "daemon" "clean"
  stack_set_tier_owner "daemon"
  if ! stack_ensure_vibe_stack; then
    echo "[stack] one or more services failed to start — see log/" >&2
    stack_status_vibe_stack || true
    exit 1
  fi
  stack_verify_dependencies all || {
    echo "[stack] post-start verification failed" >&2
    exit 1
  }
  stack_write_instance_manifest
  stack_start_heal_daemon
  stack_start_data_worker
  stack_print_ready
  stack_status_vibe_stack
}

stack_ctl_down() {
  stack_with_lock stack_stop_vibe_stack "$@"
  echo "[stack] stack stopped"
}

stack_ctl_restart() {
  local force=0 kill_dev=0
  for arg in "$@"; do
    case "$arg" in
      --force|-f) force=1 ;;
      --kill-dev) kill_dev=1 ;;
    esac
  done
  if (( kill_dev && ! force )); then
    echo "[stack] --kill-dev requires --force (full stop + start)" >&2
    exit 1
  fi

  stack_with_lock stack_ctl_restart_inner "$force" "$kill_dev"
}

stack_ctl_restart_inner() {
  local force="${1:-0}" kill_dev="${2:-0}"
  stack_reconcile_all
  if stack_dev_mode_active; then
    if (( kill_dev && force )); then
      echo "[stack] --kill-dev: stopping dev session before daemon restart ..."
      stack_kill_dev_session
    else
      echo "[stack] dev mode is running — keep that terminal open for hot reload." >&2
      echo "[stack] To switch to background daemon: Ctrl+C in the dev terminal, then: ./trade up" >&2
      echo "[stack] Or force: trade restart --force --kill-dev" >&2
      exit 1
    fi
  fi
  if (( ! force )); then
    stack_refuse_if_dev_mode
  fi
  if (( force )); then
    echo "[stack] full restart (--force): stop then preflight + start ..."
    stack_stop_vibe_stack
    stack_bootstrap_session "daemon" "clean"
    stack_set_tier_owner "daemon"
    stack_preflight_start || {
      echo "[stack] preflight failed after stop — fix issues, then: trade doctor" >&2
      exit 1
    }
  else
    echo "[stack] heal: preflight + start only what's down ..."
    stack_bootstrap_session "daemon" "heal"
    stack_set_tier_owner "daemon"
    stack_preflight_start || {
      echo "[stack] preflight failed — try: trade restart --force" >&2
      exit 1
    }
  fi
  stack_preflight_dependencies --strict || {
    echo "[stack] hub dependency preflight failed" >&2
    exit 1
  }

  if ! stack_ensure_vibe_stack; then
    echo "[stack] one or more services failed — see log/" >&2
    stack_status_vibe_stack || true
    exit 1
  fi
  stack_verify_dependencies all || {
    echo "[stack] post-heal verification failed" >&2
    exit 1
  }
  stack_write_instance_manifest
  stack_start_heal_daemon
  stack_start_data_worker
  stack_print_ready
  stack_status_vibe_stack
}

stack_ctl_ensure() {
  stack_with_lock stack_ctl_ensure_inner "$@"
}

stack_ctl_ensure_inner() {
  local hub_only=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --hub-only) hub_only=1 ;;
    esac
    shift
  done

  stack_reconcile_all
  if (( hub_only )); then
    echo "[stack] ensuring hub Docker tier ..."
    stack_preflight_dependencies --hub-only || true
    if ! stack_ensure_dependencies hub; then
      stack_heal_fail_loud "hub ensure failed — check Docker and docker-compose.stack.yml"
      exit 1
    fi
    stack_verify_dependencies hub || {
      stack_heal_fail_loud "hub verification failed after ensure"
      exit 1
    }
    return 0
  fi

  stack_refuse_if_dev_mode
  stack_refuse_if_dev_tier_owner
  echo "[stack] ensuring stack ..."
  stack_bootstrap_session "daemon" "heal"
  stack_set_tier_owner "daemon"
  stack_preflight_dependencies || true

  if stack_tier_already_healthy; then
    stack_record_heal_result 0 "already healthy" 0
    stack_ensure_data_worker_if_enabled
    stack_warn_stale_exposure
    return 0
  fi

  local max_attempts attempt reason=""
  max_attempts="$(stack_heal_max_attempts)"
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    echo "[stack] heal attempt ${attempt}/${max_attempts} ..."
    if stack_ensure_vibe_stack && stack_verify_dependencies all; then
      stack_record_heal_result 0 "" "$attempt"
      stack_ensure_data_worker_if_enabled
      stack_warn_stale_exposure
      return 0
    fi
    reason="one or more services failed to start — see $(stack_log_dir)/"
  done

  stack_record_heal_result 1 "$reason" "$max_attempts"
  stack_heal_fail_loud "$reason"
  exit 1
}

stack_ctl_status() {
  stack_status_vibe_stack "$@"
}

stack_ctl_preflight() {
  stack_reconcile_all
  stack_preflight_start
  stack_preflight_dependencies --strict
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
    stack_ctl_ensure "$@"
    ;;
  status)
    stack_ctl_status "$@"
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
