#!/usr/bin/env bash
# Dependency orchestration: reconcile → preflight → ensure → verify.
# Sourced from stack_lib.sh after stack_docker_lib.sh.

if [[ -n "${STACK_DEPS_SOURCED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
STACK_DEPS_SOURCED=1

_stack_deps_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

stack_session_file() {
  echo "$(stack_log_dir)/stack.session"
}

stack_hub_docker_required() {
  stack_searxng_enabled && return 0
  stack_timescale_should_ensure && return 0
  stack_redis_enabled && return 0
  return 1
}

stack_reconcile_orphan_lock() {
  local lockdir
  lockdir="$(stack_log_dir)/.stack.lock.d"
  [[ -d "$lockdir" ]] || return 0
  if [[ -z "$(ls -A "$lockdir" 2>/dev/null)" ]]; then
    rmdir "$lockdir" 2>/dev/null || true
    echo "[stack] removed orphan stack lock directory" >&2
  fi
}

stack_reconcile_all() {
  stack_reconcile_stale_dev_mode
  stack_reconcile_orphan_watchdogs
  stack_recover_stale_scheduler_jobs
  stack_reconcile_stale_claims
  stack_reconcile_nautilus_watch_pid
  stack_reconcile_orphan_lock
}

# Read-only reconcile for status — no scheduler boot recovery or service starts.
stack_reconcile_for_status() {
  stack_reconcile_stale_dev_mode
  stack_reconcile_orphan_watchdogs
  stack_reconcile_stale_claims
  stack_reconcile_nautilus_watch_pid
  stack_reconcile_orphan_lock
}

stack_reconcile_orphan_watchdogs() {
  local log_dir pid pidfile name
  log_dir="$(stack_log_dir)"
  for name in stack-heal stack-nautilus-heal; do
    pidfile="$log_dir/${name}.pid"
    [[ -f "$pidfile" ]] || continue
    pid="$(stack_read_pid "$pidfile")"
    if [[ -z "$pid" ]] || ! stack_pid_alive "$pid"; then
      echo "[stack] clearing stale watchdog pidfile ${name}.pid (pid ${pid:-none})"
      rm -f "$pidfile"
    fi
  done
}

stack_recover_stale_scheduler_jobs() {
  local py root recovered
  root="$(stack_root)"
  py="$(stack_pick_python)"
  recovered="$(
    PYTHONPATH="$root/vibetrading/agent:$root/integrations" "$py" -c "
from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_boot
count = recover_scheduler_jobs_on_stack_boot()
print(count or '', end='')
" 2>/dev/null || true
  )"
  if [[ -n "$recovered" && "$recovered" != "0" ]]; then
    echo "[stack] recovered $recovered stale scheduled research job(s) from RUNNING"
  fi
}

stack_recover_scheduler_jobs_on_shutdown() {
  local py root recovered
  root="$(stack_root)"
  py="$(stack_pick_python)"
  recovered="$(
    PYTHONPATH="$root/vibetrading/agent:$root/integrations" "$py" -c "
from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_shutdown
count = recover_scheduler_jobs_on_stack_shutdown()
print(count or '', end='')
" 2>/dev/null || true
  )"
  if [[ -n "$recovered" && "$recovered" != "0" ]]; then
    echo "[stack] reset $recovered scheduled research job(s) stuck in RUNNING on shutdown"
  fi
}

stack_preflight_dependencies() {
  local strict=0 hub_only=0 failures=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --strict) strict=1 ;;
      --hub-only) hub_only=1 ;;
    esac
    shift
  done

  local compose root
  root="$(stack_root)"
  compose="$(stack_docker_compose_file)"

  echo "[stack] dependency preflight ..."

  if (( ! hub_only )); then
    if ! stack_validate_ports_registry; then
      if (( strict )); then
        failures=$((failures + 1))
      else
        echo "[stack] WARN: port registry out of sync — run: ./trade sync-ports" >&2
      fi
    fi
  fi

  if stack_hub_docker_required; then
    if [[ ! -f "$compose" ]]; then
      echo "[stack] ERROR: hub compose missing at $compose" >&2
      failures=$((failures + 1))
    fi
    if ! stack_docker_available; then
      stack_require_docker || failures=$((failures + 1))
    fi
  fi

  if (( failures > 0 )); then
    echo "[stack] dependency preflight failed ($failures issue(s))" >&2
    return 1
  fi
  echo "[stack] dependency preflight ok"
  return 0
}

stack_verify_dependencies() {
  local tier="${1:-all}" failures=0

  echo "[stack] verifying dependencies ($tier) ..."

  if [[ "$tier" == "hub" || "$tier" == "all" ]]; then
    if stack_searxng_enabled; then
      if stack_probe_searxng; then
        echo "  ✓ SearXNG probe"
      else
        echo "  ✗ SearXNG probe failed at $(stack_searxng_url)" >&2
        failures=$((failures + 1))
      fi
    fi
    if stack_timescale_should_ensure; then
      if stack_probe_timescale; then
        echo "  ✓ TimescaleDB probe"
      else
        echo "  ✗ TimescaleDB probe failed" >&2
        failures=$((failures + 1))
      fi
    fi
    if stack_redis_enabled; then
      if stack_probe_redis; then
        echo "  ✓ Redis probe"
      else
        echo "  ✗ Redis probe failed at $(stack_redis_url)" >&2
        failures=$((failures + 1))
      fi
    fi
    if stack_probe_llm_wiki; then
      echo "  ✓ LLM-Wiki probe (news ingest)"
    else
      echo "  ✗ LLM-Wiki probe failed — start LLM Wiki.app and set LLM_WIKI_PROJECT_ID" >&2
    fi
  fi

  if [[ "$tier" == "app" || "$tier" == "all" ]]; then
    local openalgo_port api_port ui_port
    openalgo_port="$(stack_openalgo_port)"
    api_port="$(stack_vibe_api_port)"
    ui_port="$(stack_vibe_ui_port)"
    if stack_http_ok "http://127.0.0.1:${openalgo_port}/"; then
      echo "  ✓ OpenAlgo HTTP"
    else
      echo "  ✗ OpenAlgo not responding on :$openalgo_port" >&2
      failures=$((failures + 1))
    fi
    if stack_vibe_api_http_ok "$api_port"; then
      echo "  ✓ Vibe API /health"
    else
      echo "  ✗ Vibe API /health failed on :$api_port" >&2
      failures=$((failures + 1))
    fi
    if stack_http_ok "http://127.0.0.1:${ui_port}/"; then
      echo "  ✓ Vibe UI HTTP"
    else
      echo "  ✗ Vibe UI not responding on :$ui_port" >&2
      failures=$((failures + 1))
    fi
  fi

  if [[ "$tier" == "all" ]]; then
    if [[ "${NAUTILUS_WATCH_ENABLE:-1}" != "0" && "${NAUTILUS_WATCH_ENABLE:-}" != "false" ]]; then
      local nautilus_pid
      nautilus_pid="$(stack_read_pid "$(stack_log_dir)/nautilus-watch.pid")"
      if stack_nautilus_pid_valid "$nautilus_pid"; then
        echo "  ✓ Nautilus watch pid=$nautilus_pid"
      elif stack_nautilus_registry_present; then
        echo "  ✗ Nautilus watch not running (registry present)" >&2
        failures=$((failures + 1))
      fi
    fi
  fi

  if (( failures > 0 )); then
    echo "[stack] dependency verify failed ($failures issue(s))" >&2
    return 1
  fi
  return 0
}

stack_ensure_dependencies() {
  local tier="${1:-all}" ok=0 clean_hub=0
  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --clean-hub) clean_hub=1 ;;
    esac
    shift
  done

  if [[ "$tier" == "hub" || "$tier" == "all" ]]; then
    if (( clean_hub )) && stack_docker_available; then
      local compose
      compose="$(stack_docker_compose_file)"
      if [[ -f "$compose" ]]; then
        echo "[stack] force-recreating hub Docker services (STACK_CLEAN_HUB_ON_BOOT) ..."
        docker compose -f "$compose" up -d --force-recreate searxng redis timescaledb 2>/dev/null || true
      fi
    fi
    stack_ensure_hub_docker || ok=1
  fi

  if [[ "$tier" == "app" || "$tier" == "all" ]]; then
    stack_ensure_hub_storage || true
    stack_ensure_vibe_config || true
    stack_start_openalgo || ok=1
    stack_start_vibe_api || ok=1
    stack_start_vibe_ui || ok=1
  fi

  if [[ "$tier" == "all" ]]; then
    stack_ensure_nautilus_watch || ok=1
    stack_sync_nautilus_claim
    stack_ensure_heal_daemon_if_dead
    stack_ensure_data_worker_if_enabled
    stack_warn_stale_exposure
  fi

  return "$ok"
}

stack_bootstrap_session() {
  local mode="${1:-daemon}" bootstrap="${2:-heal}"
  local session_id file py
  session_id="$(date +%s)-$$"
  file="$(stack_session_file)"
  mkdir -p "$(stack_log_dir)"

  if [[ "$bootstrap" == "clean" ]]; then
    stack_reconcile_stale_claims
    stack_reconcile_nautilus_watch_pid
    rm -f "$(stack_log_dir)/stack.instance"
  fi

  py="$(stack_pick_python)"
  "$py" - "$file" "$mode" "$bootstrap" "$session_id" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mode": sys.argv[2],
            "bootstrap": sys.argv[3],
            "session_id": sys.argv[4],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

stack_command_needs_heal() {
  local cmd="$1"
  case "$cmd" in
    dev|reload|research|data|tiered-api|data-router|start|up|heal|restart)
      return 0
      ;;
  esac
  return 1
}

stack_command_is_status() {
  local cmd="$1"
  case "$cmd" in
    status|status-vibe|status-hub) return 0 ;;
  esac
  return 1
}

stack_maybe_heal_before_command() {
  local cmd="$1"
  if stack_command_is_status "$cmd"; then
    stack_reconcile_for_status
    return 0
  fi
  stack_reconcile_all
  if ! stack_command_needs_heal "$cmd"; then
    return 0
  fi
  if stack_dev_mode_active && [[ "$cmd" != "dev" ]]; then
    return 0
  fi
  echo "[stack] ensuring hub dependencies before: $cmd"
  stack_ensure_dependencies hub || {
    echo "[stack] WARN: hub dependencies not fully ready (strict commands will fail)" >&2
    return 0
  }
}

stack_ensure_heal_daemon_if_dead() {
  if ! stack_heal_daemon_enabled || stack_dev_mode_flagged; then
    return 0
  fi
  local pidfile pid
  pidfile="$(stack_log_dir)/stack-heal.pid"
  pid="$(stack_read_pid "$pidfile")"
  if [[ -n "$pid" ]] && stack_pid_alive "$pid"; then
    return 0
  fi
  stack_start_heal_daemon
}

stack_ensure_data_worker_if_enabled() {
  if ! stack_data_worker_enabled || stack_dev_mode_flagged; then
    return 0
  fi
  stack_start_data_worker
}

stack_warn_stale_exposure() {
  if stack_dev_mode_flagged; then
    return 0
  fi
  local state pid
  state="$(stack_root)/.exposure.state"
  [[ -f "$state" ]] || return 0
  pid="$(grep -E '"pid"' "$state" 2>/dev/null | grep -Eo '[0-9]+' | head -1 || true)"
  if [[ -n "$pid" ]] && ! stack_pid_alive "$pid"; then
    echo "[stack] WARN: exposure tunnel state stale (pid $pid dead) — run: ./trade tunnel restart" >&2
  fi
}

stack_clear_stale_exposure_state() {
  local force=0 root state pids_file pid
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force|-f) force=1 ;;
    esac
    shift
  done
  root="$(stack_root)"
  state="$root/.exposure.state"
  pids_file="$root/.exposure.pids"
  if (( force )); then
    rm -f "$state" "$pids_file"
    return 0
  fi
  if [[ -f "$state" ]]; then
    pid="$(grep -E '"pid"' "$state" 2>/dev/null | grep -Eo '[0-9]+' | head -1 || true)"
    if [[ -z "$pid" ]] || ! stack_pid_alive "$pid"; then
      rm -f "$state"
    fi
  fi
  if [[ -f "$pids_file" ]]; then
    pid="$(tr -d '[:space:]' <"$pids_file" 2>/dev/null | grep -Eo '[0-9]+' | head -1 || true)"
    if [[ -z "$pid" ]] || ! stack_pid_alive "$pid"; then
      rm -f "$pids_file"
    fi
  fi
}

stack_status_json() {
  local py
  py="$(stack_pick_python)"
  STACK_ROOT="$(stack_root)" OPENALGO_HOST="${OPENALGO_HOST:-}" \
    VIBE_BACKEND_PORT="$(stack_vibe_api_port)" \
    VIBE_FRONTEND_PORT="$(stack_vibe_ui_port)" \
    SEARXNG_BASE_URL="$(stack_searxng_url)" \
    NAUTILUS_REDIS_URL="$(stack_redis_url)" \
    "$py" - <<'PY'
import json
import os
import subprocess
import urllib.request

def curl_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False

def redis_ok() -> bool:
    url = os.environ.get("NAUTILUS_REDIS_URL", "redis://127.0.0.1:6379/0")
    try:
        out = subprocess.run(
            ["redis-cli", "-u", url, "ping"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return "PONG" in (out.stdout or "")
    except Exception:
        return False

api_port = os.environ.get("VIBE_BACKEND_PORT", "8899")
ui_port = os.environ.get("VIBE_FRONTEND_PORT", "5899")
openalgo = os.environ.get("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
searxng = os.environ.get("SEARXNG_BASE_URL", "http://localhost:5556").rstrip("/")

payload = {
    "searxng": {"ok": curl_ok(f"{searxng}/")},
    "redis": {"ok": redis_ok()},
    "openalgo": {"ok": curl_ok(f"{openalgo}/")},
    "vibe_api": {"ok": curl_ok(f"http://127.0.0.1:{api_port}/health")},
    "vibe_ui": {"ok": curl_ok(f"http://127.0.0.1:{ui_port}/")},
}
print(json.dumps(payload, indent=2))
PY
}
