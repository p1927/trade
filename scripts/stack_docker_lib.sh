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

stack_require_docker() {
  if stack_docker_available; then
    return 0
  fi
  echo "[stack] ERROR: Docker is required but not running" >&2
  echo "[stack]   macOS: open -a Docker   (wait until Docker Desktop is ready)" >&2
  echo "[stack]   Then: ./trade heal" >&2
  return 1
}

stack_searxng_enabled() {
  local v="${STACK_START_SEARXNG:-1}"
  v="$(_sdl_lc "$v")"
  [[ "$v" != "0" && "$v" != "false" && "$v" != "no" && "$v" != "off" ]]
}

stack_timescale_should_ensure() {
  stack_timescale_enabled || return 1
  local v="${STACK_START_TIMESCALE:-1}"
  v="$(_sdl_lc "$v")"
  [[ "$v" != "0" && "$v" != "false" && "$v" != "no" && "$v" != "off" ]]
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

# SearXNG botdetection expects X-Real-IP (or X-Forwarded-For) on every request.
stack_searxng_real_ip() {
  echo "127.0.0.1"
}

stack_searxng_http_ok() {
  local url="$1"
  curl -sf -o /dev/null -m 3 -H "X-Real-IP: $(stack_searxng_real_ip)" "$url" 2>/dev/null
}

stack_http_ok() {
  curl -sf -o /dev/null -m 3 "$1" 2>/dev/null
}

_searxng_probe_check_unresponsive() {
  local required="${1:-bing}"
  python3 -c '
import json, sys
required = {e.strip().lower() for e in sys.argv[1].split(",") if e.strip()}
data = json.load(sys.stdin)
unresponsive = data.get("unresponsive_engines") or []
failed = []
for entry in unresponsive:
    name = str(entry[0] if entry else "").lower()
    if name not in required:
        continue
    reason = str(entry[1] if len(entry) > 1 else "")
    failed.append((name, reason))
if failed:
    for name, reason in failed:
        print(f"{name}: {reason}", file=sys.stderr)
    sys.exit(1)
' "$required"
}

_searxng_remediation_hint() {
  local reason="${1:-}"
  local lowered
  lowered="$(printf '%s' "$reason" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$lowered" ]] || return 0

  if [[ "$lowered" == *"name or service not known"* || "$lowered" == *"no address associated"* ]]; then
    echo "[stack] Hint: Bing DNS failed inside the container — add dns: [1.1.1.1, 8.8.8.8] to searxng in docker-compose.stack.yml and run: trade restart --force" >&2
    return 0
  fi
  if [[ "$lowered" == *"certificate"* || "$lowered" == *"ssl"* || "$lowered" == *"tls"* ]]; then
    echo "[stack] Hint: TLS verify failed — run ./scripts/export_searxng_ca.sh then trade restart --force" >&2
    return 0
  fi
  if [[ "$lowered" == *"captcha"* || "$lowered" == *"403"* || "$lowered" == *"access denied"* ]]; then
    echo "[stack] Hint: engine blocked — confirm stack/searxng/settings.yml keep_only: [bing] and restart SearXNG" >&2
    return 0
  fi
  if [[ "$lowered" == *"timeout"* || "$lowered" == *"readtimeout"* || "$lowered" == *"connecttimeout"* ]]; then
    echo "[stack] Hint: Bing timed out — raise outgoing.request_timeout in stack/searxng/settings.yml (currently 15s) and reduce query rate (SEARXNG_MIN_INTERVAL_SEC)" >&2
    return 0
  fi
  if [[ "$lowered" == *"500"* || "$lowered" == *"502"* || "$lowered" == *"503"* ]]; then
    echo "[stack] Hint: transient Bing upstream error — retry after a few seconds; client retries once automatically" >&2
    return 0
  fi
}

stack_searxng_probe_engines() {
  echo "${SEARXNG_PROBE_ENGINES:-${SEARXNG_NEWS_ENGINES:-bing}}"
}

_searxng_run_engine_probe() {
  local probe_url="$1" required="$2" label="$3"
  local lang="${SEARXNG_DEFAULT_LANG:-en-IN}"
  local body probe_err curl_rc

  body="$(curl -s -m 12 -H "X-Real-IP: $(stack_searxng_real_ip)" -H "Accept-Language: ${lang},en;q=0.9" \
    "$probe_url" 2>&1)" || curl_rc=$?
  if [[ -n "${curl_rc:-}" ]]; then
    echo "[stack] SearXNG ${label}: request failed — ${body:-curl exit ${curl_rc}}" >&2
    return 1
  fi

  if ! probe_err="$(printf '%s' "$body" | _searxng_probe_check_unresponsive "$required" 2>&1)"; then
    echo "[stack] SearXNG ${label}: engine ${required} failing${probe_err:+ — ${probe_err}} (check stack/searxng/settings.yml)" >&2
    _searxng_remediation_hint "$probe_err"
    return 1
  fi
  return 0
}

stack_probe_searxng_health() {
  local base
  base="$(stack_searxng_url)"
  base="${base%/}"
  stack_searxng_http_ok "$base/healthz"
}

stack_probe_searxng_engines() {
  local base probe_url lang probe_engines
  base="$(stack_searxng_url)"
  base="${base%/}"
  lang="${SEARXNG_DEFAULT_LANG:-en-IN}"
  probe_engines="$(stack_searxng_probe_engines)"

  # Startup/heal only — real engine probes (not every status tick; Bing news is rate-sensitive).
  probe_url="${base}/search?q=trade+hub+probe&format=json&categories=general&engines=${probe_engines}&language=${lang}"
  _searxng_run_engine_probe "$probe_url" "$probe_engines" "probe" || return 1

  probe_url="${base}/search?q=NIFTY+markets+probe&format=json&categories=news&engines=${probe_engines}&language=${lang}"
  _searxng_run_engine_probe "$probe_url" "$probe_engines" "news probe" || return 1
  return 0
}

stack_probe_searxng() {
  stack_probe_searxng_health || return 1
  stack_probe_searxng_engines || return 1
}

stack_llm_wiki_python() {
  local root py
  root="$(_sdl_root)"
  py="$root/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  echo "$py"
}

stack_probe_llm_wiki() {
  local root py
  root="$(_sdl_root)"
  py="$(stack_llm_wiki_python)"
  PYTHONPATH="$root/integrations" "$py" -m trade_integrations.dataflows.hub_wiki.probe >/dev/null 2>&1
}

stack_print_llm_wiki_status() {
  local root py line
  root="$(_sdl_root)"
  py="$(stack_llm_wiki_python)"
  line="$(PYTHONPATH="$root/integrations" "$py" -m trade_integrations.dataflows.hub_wiki.probe --status-line 2>/dev/null || true)"
  if [[ -n "$line" ]]; then
    echo "  $line"
  else
    echo "  ✗ LLM-Wiki not running — please start LLM Wiki.app for news ingest"
  fi
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
    stack_require_docker
    return 1
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
    echo "[stack] TimescaleDB enabled but Docker is unavailable" >&2
    stack_require_docker
    return 1
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
    return 1
  fi

  if ! stack_docker_available; then
    echo "[stack] Redis needed but Docker is unavailable" >&2
    stack_require_docker
    return 1
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
  return 1
}

# Ensure all hub Docker services (SearXNG, Timescale, Redis) based on env flags.
stack_ensure_hub_docker() {
  local ok=0 ready=0 total=0
  local searxng_mark="·" redis_mark="·" timescale_mark="·"

  if stack_searxng_enabled; then
    total=$((total + 1))
    if stack_ensure_searxng; then
      searxng_mark="✓"
      ready=$((ready + 1))
    else
      searxng_mark="✗"
      ok=1
    fi
  fi

  if stack_timescale_should_ensure; then
    total=$((total + 1))
    if stack_ensure_timescale; then
      timescale_mark="✓"
      ready=$((ready + 1))
    else
      timescale_mark="✗"
      ok=1
    fi
  fi

  if stack_redis_enabled; then
    total=$((total + 1))
    if stack_ensure_redis_docker; then
      redis_mark="✓"
      ready=$((ready + 1))
    else
      redis_mark="✗"
      ok=1
    fi
  fi

  if (( total > 0 )); then
    echo "[stack] hub docker: $ready/$total ready ($searxng_mark SearXNG $redis_mark Redis $timescale_mark Timescale)"
  fi
  if (( ok )); then
    echo "[stack] fix: start Docker Desktop if needed, then: ./trade heal" >&2
  fi
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
  local stop_searxng="${STACK_STOP_SEARXNG:-0}"
  stop_searxng="$(_sdl_lc "$stop_searxng")"
  if [[ "$stop_searxng" == "1" || "$stop_searxng" == "true" || "$stop_searxng" == "yes" || "$stop_searxng" == "on" ]]; then
    docker compose -f "$compose" stop timescaledb redis searxng 2>/dev/null || true
  else
    docker compose -f "$compose" stop timescaledb redis 2>/dev/null || true
  fi
}

stack_status_hub_docker() {
  local ok=1 start_searxng="${STACK_START_SEARXNG:-1}"
  start_searxng="$(_sdl_lc "$start_searxng")"

  echo "══════════════════════════════════════════════════════════"
  echo "  Hub Docker status"
  echo "══════════════════════════════════════════════════════════"

  if [[ "$start_searxng" != "0" && "$start_searxng" != "false" && "$start_searxng" != "no" && "$start_searxng" != "off" ]]; then
    if stack_probe_searxng_health; then
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

  stack_print_llm_wiki_status || true

  echo "══════════════════════════════════════════════════════════"
  if (( ok )); then return 0; fi
  echo "  Fix: ./trade heal   (starts missing hub Docker services)"
  echo "  Full reset: ./trade restart --force"
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
