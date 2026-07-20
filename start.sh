#!/usr/bin/env bash
# Bootstrap and run the full Trade stack:
#   1. SearXNG (Docker) — news search
#   2. OpenAlgo — live broker data + execution bridge
#   3. Vibe Trading Web UI (default) — chat, plans, OpenAlgo MCP orders
#   4. TradingAgents CLI (--cli) — batch multi-agent research
#
# Usage: ./start.sh [options]
#   --openalgo-only   OpenAlgo only (no Vibe / TradingAgents)
#   --cli             TradingAgents CLI instead of Vibe Web UI
#   --agents-only     TradingAgents only (expects SearXNG + OpenAlgo up)
#   --no-searxng      Skip SearXNG Docker check/start
#   --no-timescale    Skip TimescaleDB Docker check/start
#   --no-bootstrap    Skip venv / pip install
#   --status          Print readiness and exit

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ROOT="$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_docker_lib.sh"
AGENTS_DIR="$ROOT/tradingagents"
OPENALGO_DIR="$ROOT/openalgo"
VIBE_DIR="$ROOT/vibetrading"
COMPOSE_FILE="$ROOT/docker-compose.stack.yml"
LOG_DIR="$ROOT/openalgo/log"
OPENALGO_LOG="$LOG_DIR/stack-openalgo.log"
OPENALGO_BG_PID=""

START_OPENALGO=1
START_AGENTS=1
START_VIBE=1
START_CLI=0
START_SEARXNG=1
START_TIMESCALE=1
START_REDIS=1
DO_BOOTSTRAP=1
STATUS_ONLY=0
DAEMON=0
DEV_UI_ONLY=0

# Readiness flags (0/1)
READY_SEARXNG=0
READY_TIMESCALE=0
READY_REDIS=0
READY_OPENALGO=0
READY_AGENTS=0
READY_VIBE=0

for arg in "$@"; do
  case "$arg" in
    --openalgo-only) START_AGENTS=0; START_VIBE=0; START_CLI=0 ;;
    --cli) START_VIBE=0; START_CLI=1 ;;
    --vibe-only) START_CLI=0; START_VIBE=1 ;;
    --agents-only) START_OPENALGO=0; START_VIBE=0; START_CLI=1 ;;
    --no-searxng) START_SEARXNG=0 ;;
    --no-timescale) START_TIMESCALE=0 ;;
    --no-redis) START_REDIS=0 ;;
    --no-bootstrap) DO_BOOTSTRAP=0 ;;
    --status) STATUS_ONLY=1 ;;
    --daemon) DAEMON=1 ;;
    --dev-ui) DEV_UI_ONLY=1; START_SEARXNG=0; START_TIMESCALE=0; START_REDIS=0; START_OPENALGO=0 ;;
    -h|--help)
      # shellcheck disable=SC1091
      source "$ROOT/scripts/stack_ports.sh"
      stack_ensure_ports_env 2>/dev/null || true
      cat <<EOF
Usage: ./start.sh [options]

  (default)         Bootstrap, start SearXNG + OpenAlgo, launch Vibe Web UI
  --cli             TradingAgents interactive CLI instead of Vibe Web UI
  --openalgo-only   SearXNG + OpenAlgo only (browser trading, no agent UI)
  --agents-only     TradingAgents CLI only (SearXNG + OpenAlgo must already run)
  --no-searxng      Do not start/check SearXNG Docker
  --no-timescale    Do not start/check TimescaleDB Docker
  --no-redis        Do not start/check Redis Docker (Nautilus watch cache)
  --no-bootstrap    Skip venv creation and pip install
  --status          Check readiness of all services and exit
  --daemon          Start OpenAlgo + Vibe in background and exit

Services (from stack/ports.yaml):
  SearXNG         ${SEARXNG_BASE_URL:-run: python scripts/sync_stack_ports.py --apply}
  OpenAlgo        ${OPENALGO_HOST:-run: python scripts/sync_stack_ports.py --apply}
  Vibe Trading    ${VIBE_FRONTEND_URL:-run: python scripts/sync_stack_ports.py --apply}
  Vibe API        ${VIBE_BACKEND_URL:-run: python scripts/sync_stack_ports.py --apply}
  TradingAgents   interactive CLI (--cli)
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 1
      ;;
  esac
done

# ── helpers ──────────────────────────────────────────────────────────────────

log()  { echo "[stack] $*"; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ⚠ $*"; }
fail() { echo "  ✗ $*" >&2; }

# macOS /bin/bash is 3.2 — no ${var,,} lowercase expansion.
_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

cleanup() {
  if [[ -n "${OPENALGO_BG_PID:-}" ]] && kill -0 "$OPENALGO_BG_PID" 2>/dev/null; then
    log "Stopping OpenAlgo (pid $OPENALGO_BG_PID)..."
    kill "$OPENALGO_BG_PID" 2>/dev/null || true
  fi
}

# Disabled for --status / --daemon: those modes must not kill background services on exit.
if (( STATUS_ONLY || DAEMON )); then
  trap - EXIT INT TERM
else
  trap cleanup EXIT INT TERM
fi

load_env() {
  # shellcheck disable=SC1091
  source "$ROOT/scripts/stack_ports.sh"
  stack_ensure_ports_env || true
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
}

pick_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
  else
    echo "python3"
  fi
}

ensure_docker_path() {
  if command -v docker >/dev/null 2>&1; then
    return 0
  fi
  local docker_bin="/Applications/Docker.app/Contents/Resources/bin"
  if [[ -x "$docker_bin/docker" ]]; then
    PATH="$docker_bin:$PATH"
    export PATH
  fi
}

pick_openalgo_runner() {
  if command -v uv >/dev/null 2>&1; then
    echo "uv run app.py"
  elif [[ -x "$OPENALGO_DIR/.venv/bin/python" ]]; then
    echo "$OPENALGO_DIR/.venv/bin/python app.py"
  else
    echo "python3 app.py"
  fi
}

http_ok() {
  curl -sf -o /dev/null -m 3 "$1" 2>/dev/null
}

searxng_url() {
  echo "${SEARXNG_BASE_URL}"
}

openalgo_url() {
  echo "${OPENALGO_HOST}"
}

wait_for_url() {
  local label="$1" url="$2" attempts="${3:-60}"
  log "Waiting for $label at $url ..."
  for ((i = 1; i <= attempts; i++)); do
    if http_ok "$url"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# ── bootstrap ────────────────────────────────────────────────────────────────

bootstrap_tradingagents() {
  local py="python3"
  if ! command -v "$py" >/dev/null 2>&1; then
    fail "python3 not found"
    return 1
  fi

  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    log "Creating Python venv at .venv ..."
    "$py" -m venv "$ROOT/.venv"
  fi

  if [[ ! -d "$AGENTS_DIR" ]]; then
    fail "Missing tradingagents/ submodule — run: git submodule update --init --recursive"
    return 1
  fi

  local vpy="$ROOT/.venv/bin/python"
  if ! "$vpy" -c "import trade_integrations; import tradingagents" 2>/dev/null; then
    log "Installing TradingAgents engine + trade integrations ..."
    "$ROOT/.venv/bin/pip" install -q -e "$AGENTS_DIR"
    "$ROOT/.venv/bin/pip" install -q -e ".[dev,research]"
  if [[ -x "$ROOT/scripts/ensure_prediction_ml.sh" ]]; then
      bash "$ROOT/scripts/ensure_prediction_ml.sh" || fail "prediction ML setup failed — run: trade setup"
    fi
    "$vpy" -c "import trade_integrations"
  fi

  if "$vpy" -c "import trade_integrations; import tradingagents" 2>/dev/null; then
    "$ROOT/.venv/bin/pip" install -q pyyaml 2>/dev/null || true
    "$vpy" "$ROOT/scripts/sync_stack_ports.py" --apply 2>/dev/null || true
  fi

  if (( START_VIBE )) && [[ ! -d "$VIBE_DIR" ]]; then
    fail "Missing vibetrading/ submodule — run: git submodule update --init --recursive vibetrading"
    return 1
  fi

  if (( START_VIBE )) && [[ ! -x "$ROOT/.venv/bin/vibe-trading" ]]; then
    log "Installing Vibe Trading from vibetrading/ submodule ..."
    "$ROOT/.venv/bin/pip" install -q -e "$VIBE_DIR"
  fi

  if "$vpy" -c "import trade_integrations; import tradingagents" 2>/dev/null; then
    READY_AGENTS=1
  else
    fail "TradingAgents import failed after install"
    return 1
  fi

  if (( START_VIBE )) && command -v "$ROOT/.venv/bin/vibe-trading" >/dev/null 2>&1; then
    READY_VIBE=1
  elif (( START_VIBE )); then
    warn "vibe-trading CLI not found — run: pip install -e vibetrading/"
  fi

  return 0
}

check_tradingagents_config() {
  local provider="${TRADINGAGENTS_LLM_PROVIDER:-openai}"
  case "$provider" in
    minimax)
      [[ -n "${MINIMAX_API_KEY:-}" || -n "${MINIMAX_CN_API_KEY:-}" ]] || \
        warn "MINIMAX_API_KEY not set — LLM calls will fail"
      ;;
    openai)
      [[ -n "${OPENAI_API_KEY:-}" ]] || warn "OPENAI_API_KEY not set — LLM calls will fail"
      ;;
    google)
      [[ -n "${GOOGLE_API_KEY:-}" ]] || warn "GOOGLE_API_KEY not set — LLM calls will fail"
      ;;
    anthropic)
      [[ -n "${ANTHROPIC_API_KEY:-}" ]] || warn "ANTHROPIC_API_KEY not set — LLM calls will fail"
      ;;
  esac
}

# ── SearXNG (Docker) ─────────────────────────────────────────────────────────

probe_searxng() {
  local base
  base="$(searxng_url)"
  base="${base%/}"
  http_ok "$base/" || http_ok "$base/search?q=test&format=json"
}

ensure_searxng() {
  if stack_probe_searxng; then
    READY_SEARXNG=1
    return 0
  fi
  stack_ensure_searxng || true
  if stack_probe_searxng; then
    READY_SEARXNG=1
    return 0
  fi
  return 1
}

check_searxng() {
  if stack_probe_searxng; then
    READY_SEARXNG=1
  fi
}

# ── TimescaleDB (Docker) ─────────────────────────────────────────────────────

timescale_enabled() {
  local flag="${TIMESCALE_ENABLED:-}"
  flag="$(_lc "$flag")"
  [[ "$flag" == "1" || "$flag" == "true" || "$flag" == "yes" || "$flag" == "on" ]]
}

timescale_url() {
  echo "${TIMESCALE_DATABASE_URL}"
}

probe_timescale() {
  if ! timescale_enabled; then
    return 0
  fi
  (cd "$ROOT" && "$(pick_python)" - <<'PY') >/dev/null 2>&1
from trade_integrations.env import load_trade_env
from trade_integrations.hub_storage.timescale_ticks import timescale_health

load_trade_env()
health = timescale_health()
raise SystemExit(0 if health.get("ok") else 1)
PY
}

ensure_timescale() {
  if ! timescale_enabled; then
    return 0
  fi
  if stack_probe_timescale; then
    READY_TIMESCALE=1
    return 0
  fi
  stack_ensure_timescale || true
  if stack_probe_timescale; then
    READY_TIMESCALE=1
    return 0
  fi
  return 1
}

check_timescale() {
  if ! timescale_enabled; then
    return 0
  fi
  if stack_probe_timescale; then
    READY_TIMESCALE=1
  fi
}

# ── Redis (Docker — Nautilus watch node cache) ───────────────────────────────

redis_enabled() {
  if [[ "${START_REDIS:-1}" == "0" ]]; then
    return 1
  fi
  local watch="${NAUTILUS_WATCH_ENABLE:-1}"
  watch="$(_lc "$watch")"
  if [[ "$watch" == "0" || "$watch" == "false" || "$watch" == "no" || "$watch" == "off" ]]; then
    [[ -n "${NAUTILUS_REDIS_URL:-}" ]]
    return $?
  fi
  return 0
}

redis_url() {
  echo "${NAUTILUS_REDIS_URL}"
}

probe_redis() {
  if ! redis_enabled; then
    return 0
  fi
  if command -v redis-cli >/dev/null 2>&1; then
    redis-cli -u "$(redis_url)" ping 2>/dev/null | grep -q PONG && return 0
  fi
  (cd "$ROOT" && "$(pick_python)" - <<'PY') >/dev/null 2>&1
import os
try:
    import redis
except ImportError:
    raise SystemExit(1)
url = os.getenv("NAUTILUS_REDIS_URL")
if not url:
    raise SystemExit(1)
client = redis.from_url(url, socket_connect_timeout=2)
client.ping()
PY
}

ensure_redis() {
  if ! redis_enabled; then
    return 0
  fi
  if stack_ensure_redis_docker; then
    if stack_probe_redis; then
      READY_REDIS=1
    fi
    return 0
  fi
  return 0
}

check_redis() {
  if ! redis_enabled; then
    return 0
  fi
  if stack_probe_redis; then
    READY_REDIS=1
  fi
}

# ── OpenAlgo ─────────────────────────────────────────────────────────────────

probe_openalgo() {
  local base
  base="$(openalgo_url)"
  base="${base%/}"
  http_ok "$base/"
}

wait_for_openalgo() {
  local base
  base="$(openalgo_url)"
  base="${base%/}"
  wait_for_url "OpenAlgo" "$base/" 90
}

start_openalgo() {
  if [[ -f "$ROOT/scripts/stack_lib.sh" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/scripts/stack_lib.sh"
    STACK_ROOT="$ROOT"
    stack_load_env
    if stack_start_openalgo; then
      READY_OPENALGO=1
      return 0
    fi
    fail "OpenAlgo failed to start — see $(stack_log_dir)/openalgo.log"
    return 1
  fi

  if [[ ! -d "$OPENALGO_DIR" ]]; then
    fail "Missing openalgo/ submodule — run: git submodule update --init --recursive"
    return 1
  fi
  if [[ ! -f "$OPENALGO_DIR/.env" ]]; then
    fail "Missing openalgo/.env — copy from openalgo/.sample.env"
    return 1
  fi

  if probe_openalgo; then
    log "OpenAlgo already running."
    READY_OPENALGO=1
    return 0
  fi

  mkdir -p "$LOG_DIR"

  local runner
  runner="$(pick_openalgo_runner)"
  log "Starting OpenAlgo ($runner) ..."
  (
    cd "$OPENALGO_DIR"
    # shellcheck disable=SC2086
    exec $runner
  ) >>"$OPENALGO_LOG" 2>&1 &
  OPENALGO_BG_PID=$!

  if wait_for_openalgo; then
    READY_OPENALGO=1
    return 0
  fi

  fail "OpenAlgo failed to start — see $OPENALGO_LOG"
  return 1
}

check_openalgo() {
  if probe_openalgo; then
    READY_OPENALGO=1
  fi
}

# ── status report ────────────────────────────────────────────────────────────

check_vibe_ready() {
  if [[ -d "$VIBE_DIR" ]] && [[ -x "$ROOT/.venv/bin/vibe-trading" ]]; then
    local frontend
    frontend="$(vibe_frontend_dir)"
    if [[ -f "$frontend/package.json" && -x "$frontend/node_modules/.bin/vite" ]]; then
      READY_VIBE=1
    fi
  fi
}

probe_vibe_http() {
  local api="${VIBE_BACKEND_PORT}"
  local ui="${VIBE_FRONTEND_PORT}"
  http_ok "http://127.0.0.1:${api}/" && http_ok "http://127.0.0.1:${ui}/"
}

daemon_vibe_running() {
  probe_vibe_http
}

check_openalgo_mcp() {
  if [[ ! -x "$ROOT/scripts/run_openalgo_mcp.sh" ]]; then
    return 1
  fi
  if [[ ! -x "$OPENALGO_DIR/.venv/bin/python" ]]; then
    return 1
  fi
  (cd "$OPENALGO_DIR" && "$OPENALGO_DIR/.venv/bin/python" -c "from openalgo import api" >/dev/null 2>&1)
}

print_status() {
  echo ""
  echo "══════════════════════════════════════════════════════════"
  echo "  Stack readiness"
  echo "══════════════════════════════════════════════════════════"

  if (( READY_SEARXNG )); then
    ok "SearXNG        $(searxng_url)  (news search)"
  else
    fail "SearXNG        not reachable at $(searxng_url)"
  fi

  if timescale_enabled; then
    if (( READY_TIMESCALE )); then
      ok "TimescaleDB    $(timescale_url)  (hot market ticks)"
    else
      fail "TimescaleDB    enabled but not reachable at $(timescale_url)"
    fi
  else
    warn "TimescaleDB    disabled (set TIMESCALE_ENABLED=true for hot tick tier)"
  fi

  if redis_enabled; then
    if (( READY_REDIS )); then
      ok "Redis          $(redis_url)  (Nautilus watch cache)"
    else
      fail "Redis          enabled but not reachable ($(redis_url))"
    fi
  else
    warn "Redis          skipped (NAUTILUS_WATCH_ENABLE=0 and no NAUTILUS_REDIS_URL)"
  fi

  if (( READY_OPENALGO )); then
    ok "OpenAlgo       $(openalgo_url)/  (trading UI)"
    ok "  Strategy Builder  $(openalgo_url)/strategybuilder"
    ok "  Option Chain      $(openalgo_url)/optionchain"
  else
    fail "OpenAlgo       not reachable at $(openalgo_url)"
  fi

  if (( READY_AGENTS )); then
    local provider="${TRADINGAGENTS_LLM_PROVIDER:-openai}"
    local model="${TRADINGAGENTS_QUICK_THINK_LLM:-default}"
    ok "TradingAgents  CLI ready ($provider / $model)"
  else
    fail "TradingAgents  not installed"
  fi

  if probe_vibe_http; then
    local vibe_ui="${VIBE_FRONTEND_PORT}"
    local vibe_api="${VIBE_BACKEND_PORT}"
    ok "Vibe Trading   Web UI http://localhost:${vibe_ui}  (API :${vibe_api}) — running"
  elif (( READY_VIBE )); then
    local vibe_ui="${VIBE_FRONTEND_PORT}"
    local vibe_api="${VIBE_BACKEND_PORT}"
    fail "Vibe Trading   installed but not running — use: trade up"
    warn "               (Web UI :${vibe_ui}, API :${vibe_api})"
  elif (( START_VIBE )); then
    fail "Vibe Trading   not ready (pip install -e vibetrading/ && ./scripts/ensure_vibe_frontend.sh)"
  fi

  if [[ -n "${OPENALGO_API_KEY:-}" ]]; then
    ok "OpenAlgo API   key configured (live Indian data enabled)"
    if check_openalgo_mcp; then
      ok "OpenAlgo MCP   wired for Vibe agent (options chain, orders)"
    else
      warn "OpenAlgo MCP   not ready — run: cd openalgo && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
      warn "               then: python scripts/setup_vibe.py"
    fi
  else
    warn "OpenAlgo API   OPENALGO_API_KEY not set — prices fall back to yfinance"
  fi

  if [[ -n "${ALPHA_VANTAGE_API_KEY:-}" ]]; then
    ok "Alpha Vantage  key configured"
  else
    warn "Alpha Vantage  no key — news/market fallback limited"
  fi

  echo "══════════════════════════════════════════════════════════"
  echo ""
}

# ── launch ───────────────────────────────────────────────────────────────────

start_tradingagents_cli() {
  log "Launching TradingAgents CLI ..."
  cd "$ROOT"
  if [[ -x "$ROOT/.venv/bin/tradingagents" ]]; then
    "$ROOT/.venv/bin/python" -c "import trade_integrations" >/dev/null
    "$ROOT/.venv/bin/tradingagents"
  elif command -v tradingagents >/dev/null 2>&1; then
    tradingagents
  else
    "$(pick_python)" -m cli.main
  fi
}

vibe_frontend_dir() {
  echo "${VIBE_FRONTEND_DIR:-$VIBE_DIR/frontend}"
}

setup_vibe_config() {
  log "Syncing stack .env + Vibe operator config (OpenAlgo MCP + trade-stack skill) ..."
  # shellcheck disable=SC1091
  source "$ROOT/scripts/stack_lib.sh"
  STACK_ROOT="$ROOT"
  stack_sync_env || true
  "$(pick_python)" "$ROOT/scripts/setup_vibe.py"
}

ensure_vibe_frontend() {
  local frontend
  frontend="$(vibe_frontend_dir)"
  if [[ -f "$frontend/package.json" && -x "$frontend/node_modules/.bin/vite" ]]; then
    return 0
  fi
  if [[ -x "$ROOT/scripts/ensure_vibe_frontend.sh" ]]; then
    log "Vibe frontend not built — running ensure_vibe_frontend.sh (requires Node.js) ..."
    bash "$ROOT/scripts/ensure_vibe_frontend.sh" || return 1
  fi
}

start_vibe_web() {
  local frontend backend_port frontend_port vibe_bin
  frontend="$(vibe_frontend_dir)"
  backend_port="${VIBE_BACKEND_PORT}"
  frontend_port="${VIBE_FRONTEND_PORT}"
  vibe_bin="$ROOT/.venv/bin/vibe-trading"

  if [[ ! -x "$vibe_bin" ]]; then
    fail "vibe-trading not found — run: pip install -e vibetrading/"
    return 1
  fi

  setup_vibe_config || true
  ensure_vibe_frontend || {
    fail "Vibe frontend missing. Install Node 20+ and run: ./scripts/ensure_vibe_frontend.sh"
    return 1
  }

  if [[ ! -f "$frontend/package.json" ]]; then
    fail "Vibe frontend not found at $frontend"
    return 1
  fi

  # shellcheck disable=SC1091
  source "$ROOT/scripts/stack_lib.sh"
  stack_load_env
  stack_preflight_start || {
    fail "Stack preflight failed — run: trade doctor"
    return 1
  }

  if daemon_vibe_running; then
    if (( DEV_UI_ONLY )) || [[ "${STACK_DEV_FOREGROUND_VIBE:-0}" == "1" ]]; then
      log "Stopping background Vibe tier before dev foreground start ..."
      # shellcheck disable=SC1091
      source "$ROOT/scripts/stack_lib.sh"
      STACK_ROOT="$ROOT"
      stack_load_env
      local log_dir api_port ui_port
      log_dir="$(stack_log_dir)"
      api_port="$(stack_vibe_api_port)"
      ui_port="$(stack_vibe_ui_port)"
      stack_stop_claimed "Vibe UI" "vibe-ui" "$log_dir/vibe-ui.pid" "$ui_port"
      stack_stop_claimed "Vibe API" "vibe-api" "$log_dir/vibe-api.pid" "$api_port"
      stack_wait_port_free "$ui_port" 15 || true
      stack_wait_port_free "$api_port" 15 || true
    else
      warn "Vibe stack already running on ports ${backend_port}/${frontend_port}"
      warn "Open http://localhost:${frontend_port} — use 'trade restart' to restart background services"
      exit 0
    fi
  fi

  log "Launching Vibe Trading Web UI ..."
  log "  Chat UI:  http://localhost:${frontend_port}"
  log "  API:      http://localhost:${backend_port}"
  log "  OpenAlgo MCP is wired via ~/.vibe-trading/agent.json"
  if (( DEV_UI_ONLY )) || [[ "${STACK_DEV_RELOAD:-0}" == "1" ]]; then
    log "  Dev reload: Vite HMR + Vibe API --reload + OpenAlgo FLASK_DEBUG (if trade dev)"
    log "  After .env change: trade reload env"
  else
    log "  Tip: for dev with auto-reload use: trade dev"
    log "  Tip: for background stack use: trade up"
  fi
  cd "$ROOT"

  local vibe_pid dev_args=()
  if (( DEV_UI_ONLY )) || [[ "${STACK_DEV_RELOAD:-0}" == "1" ]]; then
    dev_args+=(--reload-api)
  fi
  "$vibe_bin" dev \
    --port "$backend_port" \
    --frontend-port "$frontend_port" \
    --frontend-dir "$frontend" \
    "${dev_args[@]}" &
  vibe_pid=$!

  cleanup_with_vibe() {
    if [[ -n "${vibe_pid:-}" ]] && kill -0 "$vibe_pid" 2>/dev/null; then
      log "Stopping Vibe (pid $vibe_pid)..."
      kill "$vibe_pid" 2>/dev/null || true
      wait "$vibe_pid" 2>/dev/null || true
    fi
    if (( DEV_UI_ONLY )); then
      # shellcheck disable=SC1091
      source "$ROOT/scripts/stack_lib.sh"
      STACK_ROOT="$ROOT"
      stack_load_env
      local log_dir api_port ui_port openalgo_port
      log_dir="$(stack_log_dir)"
      api_port="$(stack_vibe_api_port)"
      ui_port="$(stack_vibe_ui_port)"
      openalgo_port="$(stack_openalgo_port)"
      stack_stop_claimed "Vibe UI" "vibe-ui" "$log_dir/vibe-ui.pid" "$ui_port"
      stack_stop_claimed "Vibe API" "vibe-api" "$log_dir/vibe-api.pid" "$api_port"
      stack_stop_claimed "OpenAlgo" "openalgo" "$log_dir/openalgo.pid" "$openalgo_port"
      stack_kill_openalgo_ws_proxy
      stack_clear_stack_mode
      log "Dev mode ended — restart with: ./trade dev  (or ./trade up for background daemon)"
    fi
    cleanup
  }
  trap cleanup_with_vibe EXIT INT TERM

  wait "$vibe_pid"
}

main() {
  load_env

  # shellcheck disable=SC1091
  source "$ROOT/scripts/stack_ports.sh"
  stack_validate_ports_registry || true
  if (( ! DEV_UI_ONLY && ! STATUS_ONLY )); then
    stack_check_port_listeners || true
  fi

  if (( DEV_UI_ONLY )); then
    check_vibe_ready || { fail "Vibe not ready"; exit 1; }
    start_vibe_web
    exit 0
  fi

  if (( DO_BOOTSTRAP )); then
    bootstrap_tradingagents || true
  else
    if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" -c "import trade_integrations; import tradingagents" 2>/dev/null; then
      READY_AGENTS=1
    fi
  fi
  check_tradingagents_config

  if (( START_SEARXNG )); then
    ensure_searxng || true
  else
    check_searxng
  fi

  if (( START_TIMESCALE )); then
    ensure_timescale || true
  else
    check_timescale
  fi

  if (( START_REDIS )); then
    ensure_redis || true
  else
    check_redis
  fi

  stack_ensure_hub_storage || true

  if (( STATUS_ONLY )); then
    check_openalgo
  elif (( START_OPENALGO && ! DAEMON )); then
    start_openalgo || true
  else
    check_openalgo
  fi

  if (( START_VIBE )); then
    check_vibe_ready
  fi

  print_status

  if (( STATUS_ONLY )); then
    local vibe_ok=1 hub_ok=1
    if (( START_VIBE )) && ! probe_vibe_http; then
      vibe_ok=0
    fi
    if (( START_SEARXNG )) && ! (( READY_SEARXNG )); then
      hub_ok=0
    fi
    if timescale_enabled && ! (( READY_TIMESCALE )); then
      hub_ok=0
    fi
    if redis_enabled && ! (( READY_REDIS )); then
      hub_ok=0
    fi
    if (( READY_OPENALGO && READY_AGENTS && hub_ok && ( ! START_VIBE || vibe_ok ) )); then
      exit 0
    fi
    exit 1
  fi

  if (( ! READY_AGENTS && ! (DAEMON && START_VIBE) )); then
    fail "TradingAgents is not ready — run without --no-bootstrap or:"
    fail "  pip install -e tradingagents/ && pip install -e ."
    exit 1
  fi

  if (( START_VIBE )); then
    check_vibe_ready
    if (( ! READY_VIBE )); then
      fail "Vibe Trading is not ready — run: pip install -e vibetrading/"
      exit 1
    fi
    if (( DAEMON )); then
      trap - EXIT INT TERM
      exec bash "$ROOT/scripts/stack_ctl.sh" up
    fi
    start_vibe_web
  elif (( START_CLI )); then
    start_tradingagents_cli
  elif (( DAEMON && START_OPENALGO && ! START_VIBE )); then
    trap - EXIT INT TERM
    # shellcheck disable=SC1091
    source "$ROOT/scripts/stack_lib.sh"
    STACK_ROOT="$ROOT"
    stack_load_env
    stack_start_openalgo
    echo ""
    echo "Ready:"
    echo "  OpenAlgo  http://127.0.0.1:$(stack_openalgo_port)"
    echo ""
    echo "Stop: trade down"
  else
    log "Services running. Press Ctrl+C to stop OpenAlgo (SearXNG Docker keeps running)."
    wait
  fi
}

main "$@"
