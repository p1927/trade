#!/usr/bin/env bash
# Bootstrap and run the full TradingAgents stack:
#   1. SearXNG (Docker) — news search
#   2. OpenAlgo — live broker data + options UI
#   3. TradingAgents — AI research CLI
#
# Usage: ./start.sh [options]
#   --openalgo-only   OpenAlgo only (no TradingAgents CLI)
#   --agents-only     TradingAgents only (expects SearXNG + OpenAlgo up)
#   --no-searxng      Skip SearXNG Docker check/start
#   --no-bootstrap    Skip venv / pip install
#   --status          Print readiness and exit

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENALGO_DIR="$ROOT/openalgo"
COMPOSE_FILE="$ROOT/docker-compose.stack.yml"
PID_FILE="$ROOT/.stack.pids"
LOG_DIR="$ROOT/openalgo/log"
OPENALGO_LOG="$LOG_DIR/stack-openalgo.log"

START_OPENALGO=1
START_AGENTS=1
START_SEARXNG=1
DO_BOOTSTRAP=1
STATUS_ONLY=0

# Readiness flags (0/1)
READY_SEARXNG=0
READY_OPENALGO=0
READY_AGENTS=0

for arg in "$@"; do
  case "$arg" in
    --openalgo-only) START_AGENTS=0 ;;
    --agents-only) START_OPENALGO=0 ;;
    --no-searxng) START_SEARXNG=0 ;;
    --no-bootstrap) DO_BOOTSTRAP=0 ;;
    --status) STATUS_ONLY=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./start.sh [options]

  (default)         Bootstrap, start SearXNG + OpenAlgo, launch TradingAgents CLI
  --openalgo-only   SearXNG + OpenAlgo only (browser trading, no CLI)
  --agents-only     TradingAgents only (SearXNG + OpenAlgo must already run)
  --no-searxng      Do not start/check SearXNG Docker
  --no-bootstrap    Skip venv creation and pip install
  --status          Check readiness of all services and exit

Services:
  SearXNG         http://localhost:5555   (Docker, news search)
  OpenAlgo        http://127.0.0.1:5001    (trading + INDmoney)
  TradingAgents   interactive CLI         (AI research)
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
  echo "${OPENALGO_HOST:-http://127.0.0.1:5000}"
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

  local vpy="$ROOT/.venv/bin/python"
  if ! "$vpy" -c "import tradingagents" 2>/dev/null; then
    log "Installing TradingAgents (editable) ..."
    "$ROOT/.venv/bin/pip" install -q -e "$ROOT"
  fi

  if "$vpy" -c "import tradingagents" 2>/dev/null; then
    READY_AGENTS=1
    return 0
  fi
  fail "TradingAgents import failed after install"
  return 1
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
    fail "Missing openalgo/ — run: git clone https://github.com/marketcalls/openalgo.git openalgo"
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
    "$ROOT/.venv/bin/tradingagents"
  elif command -v tradingagents >/dev/null 2>&1; then
    tradingagents
  else
    "$(pick_python)" -m cli.main
  fi
}

main() {
  load_env

  if (( DO_BOOTSTRAP )); then
    bootstrap_tradingagents || true
  else
    if [[ -x "$ROOT/.venv/bin/python" ]] && "$ROOT/.venv/bin/python" -c "import tradingagents" 2>/dev/null; then
      READY_AGENTS=1
    fi
  fi
  check_tradingagents_config

  if (( START_SEARXNG )); then
    ensure_searxng || true
  else
    check_searxng
  fi

  if (( START_OPENALGO )); then
    start_openalgo || true
  else
    check_openalgo
  fi

  print_status

  if (( STATUS_ONLY )); then
    if (( READY_SEARXNG && READY_OPENALGO && READY_AGENTS )); then
      exit 0
    fi
    exit 1
  fi

  if (( ! READY_AGENTS )); then
    fail "TradingAgents is not ready — run without --no-bootstrap or: pip install -e ."
    exit 1
  fi

  if (( START_AGENTS )); then
    start_tradingagents_cli
  else
    log "Services running. Press Ctrl+C to stop OpenAlgo (SearXNG Docker keeps running)."
    wait
  fi
}

main "$@"
