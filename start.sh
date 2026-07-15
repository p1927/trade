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
#   --no-bootstrap    Skip venv / pip install
#   --status          Print readiness and exit

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$ROOT/tradingagents"
OPENALGO_DIR="$ROOT/openalgo"
VIBE_DIR="$ROOT/vibetrading"
COMPOSE_FILE="$ROOT/docker-compose.stack.yml"
PID_FILE="$ROOT/.stack.pids"
LOG_DIR="$ROOT/openalgo/log"
OPENALGO_LOG="$LOG_DIR/stack-openalgo.log"

START_OPENALGO=1
START_AGENTS=1
START_VIBE=1
START_CLI=0
START_SEARXNG=1
DO_BOOTSTRAP=1
STATUS_ONLY=0

# Readiness flags (0/1)
READY_SEARXNG=0
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
    --no-bootstrap) DO_BOOTSTRAP=0 ;;
    --status) STATUS_ONLY=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./start.sh [options]

  (default)         Bootstrap, start SearXNG + OpenAlgo, launch Vibe Web UI
  --cli             TradingAgents interactive CLI instead of Vibe Web UI
  --openalgo-only   SearXNG + OpenAlgo only (browser trading, no agent UI)
  --agents-only     TradingAgents CLI only (SearXNG + OpenAlgo must already run)
  --no-searxng      Do not start/check SearXNG Docker
  --no-bootstrap    Skip venv creation and pip install
  --status          Check readiness of all services and exit

Services:
  SearXNG         http://localhost:5555        (Docker, news search)
  OpenAlgo        http://127.0.0.1:5001         (trading + broker UI)
  Vibe Trading    http://localhost:5899         (chat UI, default)
  Vibe API        http://localhost:8899         (backend)
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

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r pid name; do
      if kill -0 "$pid" 2>/dev/null; then
        log "Stopping $name (pid $pid)..."
        kill "$pid" 2>/dev/null || true
      fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
}

trap cleanup EXIT INT TERM

load_env() {
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
  echo "${SEARXNG_BASE_URL:-http://localhost:5555}"
}

openalgo_url() {
  echo "${OPENALGO_HOST:-http://127.0.0.1:5001}"
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
    "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
    "$vpy" -c "import trade_integrations"
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
  local base
  base="$(searxng_url)"

  if probe_searxng; then
    READY_SEARXNG=1
    return 0
  fi

  if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "SearXNG not reachable and $COMPOSE_FILE missing"
    return 1
  fi

  if ! command -v docker >/dev/null 2>&1; then
    fail "SearXNG not running and Docker is not installed"
    warn "News search will fall back to yfinance/alpha_vantage only"
    return 0
  fi

  if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon is not running — start Docker Desktop, then retry"
    warn "Continuing without SearXNG"
    return 0
  fi

  log "Starting SearXNG via Docker ..."
  docker compose -f "$COMPOSE_FILE" up -d searxng

  if wait_for_url "SearXNG" "${base%/}/" 45; then
    READY_SEARXNG=1
    return 0
  fi

  fail "SearXNG did not become ready — check: docker compose -f docker-compose.stack.yml logs searxng"
  return 1
}

check_searxng() {
  if probe_searxng; then
    READY_SEARXNG=1
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
  touch "$PID_FILE"

  local runner
  runner="$(pick_openalgo_runner)"
  log "Starting OpenAlgo ($runner) ..."
  (
    cd "$OPENALGO_DIR"
    # shellcheck disable=SC2086
    exec $runner
  ) >>"$OPENALGO_LOG" 2>&1 &
  echo "$! openalgo" >> "$PID_FILE"

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
    READY_VIBE=1
  fi
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

  if (( READY_VIBE )); then
    local vibe_ui="${VIBE_FRONTEND_PORT:-5899}"
    local vibe_api="${VIBE_BACKEND_PORT:-8899}"
    ok "Vibe Trading   Web UI http://localhost:${vibe_ui}  (API :${vibe_api})"
  elif (( START_VIBE )); then
    fail "Vibe Trading   not installed (pip install -e vibetrading/)"
  fi

  if [[ -n "${OPENALGO_API_KEY:-}" ]]; then
    ok "OpenAlgo API   key configured (live Indian data enabled)"
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
  log "Syncing Vibe operator config (OpenAlgo MCP + trade-stack skill) ..."
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
  backend_port="${VIBE_BACKEND_PORT:-8899}"
  frontend_port="${VIBE_FRONTEND_PORT:-5899}"
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

  log "Launching Vibe Trading Web UI ..."
  log "  Chat UI:  http://localhost:${frontend_port}"
  log "  API:      http://localhost:${backend_port}"
  log "  OpenAlgo MCP is wired via ~/.vibe-trading/agent.json"
  cd "$ROOT"
  exec "$vibe_bin" dev \
    --port "$backend_port" \
    --frontend-port "$frontend_port" \
    --frontend-dir "$frontend"
}

main() {
  load_env

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

  if (( STATUS_ONLY )); then
    check_openalgo
  elif (( START_OPENALGO )); then
    start_openalgo || true
  else
    check_openalgo
  fi

  if (( START_VIBE )); then
    check_vibe_ready
  fi

  print_status

  if (( STATUS_ONLY )); then
    if (( READY_SEARXNG && READY_OPENALGO && READY_AGENTS && ( ! START_VIBE || READY_VIBE ) )); then
      exit 0
    fi
    exit 1
  fi

  if (( ! READY_AGENTS )); then
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
    start_vibe_web
  elif (( START_CLI )); then
    start_tradingagents_cli
  else
    log "Services running. Press Ctrl+C to stop OpenAlgo (SearXNG Docker keeps running)."
    wait
  fi
}

main "$@"
