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
  echo "${TIMESCALE_DATABASE_URL}"
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

stack_searxng_url() {
  echo "${SEARXNG_BASE_URL:-http://localhost:5556}"
}

stack_http_ok() {
  curl -sf -o /dev/null -m 3 "$1" 2>/dev/null
}

stack_probe_searxng() {
  local base
  base="$(stack_searxng_url)"
  base="${base%/}"
  stack_http_ok "$base/" || stack_http_ok "$base/search?q=test&format=json"
}

stack_ensure_searxng() {
  local base compose
  base="$(stack_searxng_url)"

  if stack_probe_searxng; then
    return 0
  fi

  compose="$(stack_docker_compose_file)"
  if [[ ! -f "$compose" ]]; then
    echo "[stack] SearXNG not reachable and $compose missing" >&2
    return 1
  fi

  if ! stack_docker_available; then
    echo "[stack] SearXNG not running and Docker is unavailable" >&2
    echo "[stack] News search will fall back to yfinance/alpha_vantage only" >&2
    return 0
  fi

  echo "[stack] starting SearXNG via Docker ..."
  docker compose -f "$compose" up -d searxng

  local i
  for i in $(seq 1 45); do
    if stack_probe_searxng; then
      return 0
    fi
    sleep 1
  done

  echo "[stack] SearXNG did not become ready — check: docker compose -f docker-compose.stack.yml logs searxng" >&2
  return 1
}

stack_probe_timescale() {
  if ! stack_timescale_enabled; then
    return 0
  fi
  local root py
  root="$(_sdl_root)"
  py="$root/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  (cd "$root" && "$py" - <<'PY') >/dev/null 2>&1
from trade_integrations.env import load_trade_env
from trade_integrations.hub_storage.timescale_ticks import timescale_health

load_trade_env()
health = timescale_health()
raise SystemExit(0 if health.get("ok") else 1)
PY
}

stack_ensure_timescale() {
  if ! stack_timescale_enabled; then
    return 0
  fi

  if stack_probe_timescale; then
    return 0
  fi

  local compose
  compose="$(stack_docker_compose_file)"
  if [[ ! -f "$compose" ]]; then
    echo "[stack] TimescaleDB enabled but $compose missing" >&2
    return 1
  fi

  if ! stack_docker_available; then
    echo "[stack] TimescaleDB enabled but Docker is unavailable — hot ticks disabled" >&2
    return 0
  fi

  echo "[stack] starting TimescaleDB via Docker ..."
  stack_timescale_start_container

  if stack_timescale_wait_ready stack_probe_timescale 60 2; then
    return 0
  fi

  if stack_timescale_logs_indicate_stale_pid; then
    echo "[stack] TimescaleDB stale postmaster.pid — attempting auto-repair ..." >&2
    if stack_timescale_repair_stale_pid; then
      stack_timescale_start_container
      if stack_timescale_wait_ready stack_probe_timescale 60 2; then
        echo "[stack] TimescaleDB recovered after stale postmaster.pid repair"
        return 0
      fi
    fi
  elif stack_timescale_logs_indicate_recovery; then
    echo "[stack] TimescaleDB WAL recovery in progress — extending wait ..." >&2
    if stack_timescale_wait_ready stack_probe_timescale 180 2; then
      echo "[stack] TimescaleDB ready after WAL recovery"
      return 0
    fi
  fi

  echo "[stack] TimescaleDB did not become ready — repair: ./scripts/repair_timescale.sh" >&2
  return 1
}

stack_redis_enabled() {
  if [[ "${STACK_START_REDIS:-1}" == "0" ]]; then
    return 1
  fi
  local watch="${NAUTILUS_WATCH_ENABLE:-1}"
  watch="$(_sdl_lc "$watch")"
  if [[ "$watch" == "0" || "$watch" == "false" || "$watch" == "no" || "$watch" == "off" ]]; then
    [[ -n "${NAUTILUS_REDIS_URL:-}" ]]
    return $?
  fi
  return 0
}

stack_redis_url() {
  echo "${NAUTILUS_REDIS_URL:-redis://127.0.0.1:6379/0}"
}

stack_probe_redis() {
  if ! stack_redis_enabled; then
    return 0
  fi
  local url
  url="$(stack_redis_url)"
  if command -v redis-cli >/dev/null 2>&1 && redis-cli -u "$url" ping 2>/dev/null | grep -q PONG; then
    return 0
  fi
  local root py
  root="$(_sdl_root)"
  py="$root/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  (cd "$root" && NAUTILUS_REDIS_URL="$url" "$py" - <<'PY') >/dev/null 2>&1
import os
try:
    import redis
except ImportError:
    raise SystemExit(1)
url = os.getenv("NAUTILUS_REDIS_URL", "redis://127.0.0.1:6379/0")
client = redis.from_url(url, socket_connect_timeout=2)
client.ping()
PY
}

stack_ensure_redis_docker() {
  if ! stack_redis_enabled; then
    return 0
  fi

  if stack_probe_redis; then
    return 0
  fi

  local compose
  compose="$(stack_docker_compose_file)"
  if [[ ! -f "$compose" ]]; then
    echo "[stack] Redis needed but $compose missing" >&2
    return 0
  fi

  if ! stack_docker_available; then
    echo "[stack] Redis needed — start Docker or: brew services start redis" >&2
    return 0
  fi

  echo "[stack] starting Redis via Docker ..."
  docker compose -f "$compose" up -d redis

  local i
  for i in $(seq 1 20); do
    if stack_probe_redis; then
      return 0
    fi
    sleep 1
  done

  echo "[stack] Redis did not become ready — Nautilus watch may fail" >&2
  return 0
}

# Ensure all hub Docker services (SearXNG, Timescale, Redis) based on env flags.
stack_ensure_hub_docker() {
  local ok=0 start_searxng="${STACK_START_SEARXNG:-1}"
  start_searxng="$(_sdl_lc "$start_searxng")"

  if [[ "$start_searxng" != "0" && "$start_searxng" != "false" && "$start_searxng" != "no" && "$start_searxng" != "off" ]]; then
    stack_ensure_searxng || ok=1
  fi

  if [[ "${STACK_START_TIMESCALE:-1}" != "0" ]]; then
    stack_ensure_timescale || ok=1
  fi

  stack_ensure_redis_docker || true
  return "$ok"
}

stack_docker_stop_service() {
  local service="$1"
  local compose
  if ! stack_docker_available; then
    return 0
  fi
  compose="$(stack_docker_compose_file)"
  [[ -f "$compose" ]] || return 0
  echo "[stack] stopping Docker service: $service ..."
  docker compose -f "$compose" stop "$service" 2>/dev/null || true
}

stack_docker_stop_searxng() {
  stack_docker_stop_service searxng
}

stack_docker_stop_redis() {
  stack_docker_stop_service redis
}

stack_docker_stop_all() {
  local compose
  if ! stack_docker_available; then
    return 0
  fi
  compose="$(stack_docker_compose_file)"
  [[ -f "$compose" ]] || return 0
  echo "[stack] stopping all hub Docker services ..."
  docker compose -f "$compose" stop 2>/dev/null || true
}

stack_docker_down_all() {
  local compose
  if ! stack_docker_available; then
    return 0
  fi
  compose="$(stack_docker_compose_file)"
  [[ -f "$compose" ]] || return 0
  echo "[stack] tearing down hub Docker compose stack ..."
  docker compose -f "$compose" down 2>/dev/null || true
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

stack_status_hub_docker() {
  local ok=1 start_searxng="${STACK_START_SEARXNG:-1}"
  start_searxng="$(_sdl_lc "$start_searxng")"

  echo "══════════════════════════════════════════════════════════"
  echo "  Hub Docker status"
  echo "══════════════════════════════════════════════════════════"

  if [[ "$start_searxng" != "0" && "$start_searxng" != "false" && "$start_searxng" != "no" && "$start_searxng" != "off" ]]; then
    if stack_probe_searxng; then
      echo "  ✓ SearXNG     $(stack_searxng_url)"
    else
      echo "  ✗ SearXNG     not reachable at $(stack_searxng_url)"
      ok=0
    fi
  else
    echo "  · SearXNG     skipped (STACK_START_SEARXNG=0)"
  fi

  if stack_timescale_enabled; then
    if stack_probe_timescale; then
      echo "  ✓ TimescaleDB $(stack_timescale_url)"
    else
      echo "  ✗ TimescaleDB not reachable at $(stack_timescale_url)"
      ok=0
    fi
  else
    echo "  · TimescaleDB disabled (TIMESCALE_ENABLED not set)"
  fi

  if stack_redis_enabled; then
    if stack_probe_redis; then
      echo "  ✓ Redis       $(stack_redis_url)"
    else
      echo "  ✗ Redis       not reachable at $(stack_redis_url)"
      ok=0
    fi
  else
    echo "  · Redis       skipped (NAUTILUS_WATCH_ENABLE=0)"
  fi

  local root py manifest
  root="$(_sdl_root)"
  py="$root/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  manifest="$("$py" - "$root" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "integrations"))
try:
    from trade_integrations.context.hub import get_hub_dir
    hub = get_hub_dir()
    mf = hub / "_data" / "manifest.json"
    if mf.is_file():
        print(f"manifest ok ({mf.stat().st_size} bytes, mtime={mf.stat().st_mtime:.0f})")
    else:
        print("manifest missing (run: python scripts/hub_inventory.py --write)")
except Exception as exc:
    print(f"hub check failed: {exc}")
PY
)"
  if [[ -n "$manifest" ]]; then
    if [[ "$manifest" == manifest\ ok* ]]; then
      echo "  ✓ Hub manifest $manifest"
    else
      echo "  ⚠ Hub manifest $manifest"
    fi
  fi

  echo "══════════════════════════════════════════════════════════"
  if (( ok )); then return 0; fi
  echo "  Fix: trade restart   (heals hub Docker + app tier)"
  echo "══════════════════════════════════════════════════════════"
  return 1
}

stack_ensure_hub_storage() {
  local root py
  root="$(_sdl_root)"
  py="$root/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  (cd "$root" && "$py" - "$root" <<'PY') 2>/dev/null || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "integrations"))
from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.verified_news_store import ensure_hub_storage

get_hub_dir().mkdir(parents=True, exist_ok=True)
ensure_hub_storage()
PY
}
