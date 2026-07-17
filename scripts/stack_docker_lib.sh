#!/usr/bin/env bash
# Docker helpers for hub stack services (TimescaleDB, Redis, SearXNG).

if [[ -n "${STACK_DOCKER_LIB_SOURCED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
STACK_DOCKER_LIB_SOURCED=1

_sdl_root() {
  if [[ -n "${STACK_ROOT:-}" ]]; then
    echo "$STACK_ROOT"
    return
  fi
  if [[ -n "${ROOT:-}" ]]; then
    echo "$ROOT"
    return
  fi
  local here="${BASH_SOURCE[0]}"
  echo "$(cd "$(dirname "$here")/.." && pwd)"
}

_sdl_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

stack_ensure_docker_path() {
  if command -v docker >/dev/null 2>&1; then
    return 0
  fi
  local docker_bin="/Applications/Docker.app/Contents/Resources/bin"
  if [[ -x "$docker_bin/docker" ]]; then
    PATH="$docker_bin:$PATH"
    export PATH
  fi
}

stack_docker_compose_file() {
  echo "$(_sdl_root)/docker-compose.stack.yml"
}

stack_docker_available() {
  stack_ensure_docker_path
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

stack_timescale_enabled() {
  local flag="${TIMESCALE_ENABLED:-}"
  flag="$(_sdl_lc "$flag")"
  [[ "$flag" == "1" || "$flag" == "true" || "$flag" == "yes" || "$flag" == "on" ]]
}

stack_timescale_url() {
  echo "${TIMESCALE_DATABASE_URL:-postgresql://postgres:tradehub@localhost:5433/trade_hub}"
}

stack_timescale_logs_indicate_stale_pid() {
  local compose logs
  compose="$(stack_docker_compose_file)"
  logs="$(docker compose -f "$compose" logs timescaledb 2>/dev/null | tail -40 || true)"
  [[ -n "$logs" ]] || return 1
  echo "$logs" | grep -qE 'bogus data in lock file "postmaster\.pid"|postmaster\.pid": ""'
}

stack_timescale_logs_indicate_recovery() {
  local compose logs
  compose="$(stack_docker_compose_file)"
  logs="$(docker compose -f "$compose" logs timescaledb 2>/dev/null | tail -80 || true)"
  [[ -n "$logs" ]] || return 1
  echo "$logs" | grep -qiE \
    'database system was interrupted|redo starts|recovery in progress|starting archive recovery|consistent recovery state reached'
}

stack_timescale_wait_ready() {
  local probe_cmd="${1:-}"
  local max_attempts="${2:-60}"
  local sleep_secs="${3:-2}"
  local attempt=0

  if [[ -z "$probe_cmd" ]]; then
    echo "[stack] stack_timescale_wait_ready requires a probe command" >&2
    return 1
  fi

  while (( attempt < max_attempts )); do
    if eval "$probe_cmd"; then
      return 0
    fi
    if stack_timescale_logs_indicate_recovery; then
      if (( attempt == 0 || attempt % 15 == 0 )); then
        echo "[stack] TimescaleDB WAL recovery in progress — waiting (do not interrupt) ..."
      fi
      max_attempts=$(( max_attempts < 180 ? 180 : max_attempts ))
    fi
    sleep "$sleep_secs"
    attempt=$((attempt + 1))
  done
  return 1
}

stack_timescale_repair_stale_pid() {
  local compose
  compose="$(stack_docker_compose_file)"

  if ! stack_docker_available; then
    echo "[stack] Docker not available — cannot repair TimescaleDB" >&2
    return 1
  fi
  if [[ ! -f "$compose" ]]; then
    echo "[stack] missing $compose" >&2
    return 1
  fi

  echo "[stack] repairing TimescaleDB stale postmaster.pid ..."
  docker compose -f "$compose" stop timescaledb 2>/dev/null || true
  sleep 1

  docker compose -f "$compose" run --rm --no-deps --entrypoint bash timescaledb -c '
    PGDATA="${PGDATA:-/var/lib/postgresql/data}"
    pidfile="$PGDATA/postmaster.pid"
    if [[ ! -f "$pidfile" ]]; then
      echo "No postmaster.pid — nothing to repair"
      exit 0
    fi
    pid="$(head -1 "$pidfile" 2>/dev/null | tr -d "[:space:]" || true)"
    if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
      echo "Removing stale/corrupt postmaster.pid"
      rm -f "$pidfile"
      exit 0
    fi
    echo "postmaster.pid references running pid $pid — not removing" >&2
    exit 1
  '
}

stack_timescale_start_container() {
  local compose
  compose="$(stack_docker_compose_file)"
  docker compose -f "$compose" up -d timescaledb
}

stack_timescale_stop_graceful() {
  local compose
  compose="$(stack_docker_compose_file)"
  if stack_docker_available && [[ -f "$compose" ]]; then
    docker compose -f "$compose" stop timescaledb 2>/dev/null || true
  fi
}

stack_hub_docker_stop_graceful() {
  local compose
  if ! stack_docker_available; then
    return 0
  fi
  compose="$(stack_docker_compose_file)"
  if [[ ! -f "$compose" ]]; then
    return 0
  fi
  echo "[stack] stopping hub Docker services (graceful SIGTERM) ..."
  docker compose -f "$compose" stop timescaledb redis 2>/dev/null || true
}
