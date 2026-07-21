#!/usr/bin/env bash
# One-shot trade stack setup for a fresh clone or new machine.
#
# Installs Python venv deps, prediction ML runtime, OpenAlgo venv, Vite frontend,
# Nautilus watch venv, synced .env across OpenAlgo/Vibe, then runs trade doctor.
#
# Usage:
#   ./scripts/setup_trade_stack.sh
#   ./scripts/setup_trade_stack.sh --verify-only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"
STACK_ROOT="$ROOT"
stack_load_env

VERIFY_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/setup_trade_stack.sh [--verify-only]

  (default)       Install all stack dependencies and verify (trade setup)
  --verify-only   Run trade doctor checks only — no installs

After setup: trade doctor && trade up   (or trade dev)
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

log() { echo "[setup] $*"; }
warn() { echo "[setup] WARN: $*" >&2; }
die() { echo "[setup] ERROR: $*" >&2; exit 1; }

if (( VERIFY_ONLY )); then
  exec "$ROOT/scripts/stack_doctor.sh" "$@"
fi

log "Trade stack setup — fresh machine / clone bootstrap"
stack_print_ports_summary

require_submodule() {
  local name="$1"
  if [[ ! -e "$ROOT/$name/.git" && ! -f "$ROOT/$name/.git" ]]; then
    die "Missing submodule $name — run: git submodule update --init --recursive $name"
  fi
}

require_submodule tradingagents
require_submodule vibetrading
require_submodule openalgo

PY="$(stack_pick_python)"
if [[ ! -x "$PY" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    die "python3 not found"
  fi
  log "Creating repo .venv ..."
  python3 -m venv "$ROOT/.venv"
  PY="$(stack_pick_python)"
fi

if ! "$PY" -c "import trade_integrations; import tradingagents" 2>/dev/null; then
  log "Installing trade integrations + TradingAgents ..."
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e "$ROOT/tradingagents"
  "$PY" -m pip install -q -e ".[dev,research,external-predictions,prediction]"
  "$PY" -m pip install -q 'requests>=2.34.2'
fi

if [[ ! -x "$ROOT/.venv/bin/vibe-trading" ]]; then
  log "Installing Vibe Trading CLI ..."
  "$PY" -m pip install -q -e "$ROOT/vibetrading"
fi

log "Syncing root .env defaults + ports into OpenAlgo/Vibe agent ..."
stack_sync_env

if [[ -x "$ROOT/scripts/ensure_prediction_ml.sh" ]]; then
  log "Installing prediction ML runtime (libomp + sklearn/shap + lightgbm/xgboost/darts) ..."
  bash "$ROOT/scripts/ensure_prediction_ml.sh"
else
  die "Missing scripts/ensure_prediction_ml.sh"
fi

if [[ -x "$ROOT/scripts/ensure_crawl4ai.sh" ]]; then
  log "Installing Crawl4AI + Playwright for external predictions ..."
  bash "$ROOT/scripts/ensure_crawl4ai.sh"
else
  die "Missing scripts/ensure_crawl4ai.sh"
fi

if [[ -x "$ROOT/scripts/ensure_openalgo_venv.sh" ]]; then
  log "Installing OpenAlgo venv ..."
  bash "$ROOT/scripts/ensure_openalgo_venv.sh"
else
  die "Missing scripts/ensure_openalgo_venv.sh"
fi

if [[ -x "$ROOT/scripts/ensure_vibe_frontend.sh" ]]; then
  log "Installing Vibe frontend (npm) ..."
  bash "$ROOT/scripts/ensure_vibe_frontend.sh"
else
  die "Missing scripts/ensure_vibe_frontend.sh"
fi

if [[ ! -x "$ROOT/scripts/setup_nautilus.sh" ]]; then
  die "Missing scripts/setup_nautilus.sh — Nautilus watch is required"
fi
log "Installing Nautilus watch venv (required) ..."
bash "$ROOT/scripts/setup_nautilus.sh"

if [[ -x "$ROOT/scripts/setup_vibe.py" ]]; then
  log "Syncing Vibe operator config (~/.vibe-trading) ..."
  "$PY" "$ROOT/scripts/setup_vibe.py"
fi

log "Re-syncing .env after Vibe/Nautilus setup ..."
stack_sync_env
stack_load_env

if command -v docker >/dev/null 2>&1; then
  log "Ensuring hub Docker tier (SearXNG, Timescale, Redis) ..."
  if ! stack_ensure_dependencies hub; then
    warn "Hub Docker tier not ready — install/start Docker Desktop, then: trade up"
  elif ! stack_verify_dependencies hub; then
    warn "Hub Docker verification incomplete — try: trade up"
  fi
else
  warn "Docker not installed — hub news/search tier requires Docker Desktop"
fi

log "Running verification (trade doctor) ..."
if ! bash "$ROOT/scripts/stack_doctor.sh"; then
  die "Verification failed — fix issues above and re-run: trade setup"
fi

log "Setup complete."
echo ""
echo "  Start background stack:  trade up"
echo "  Dev mode (HMR):          trade dev"
echo "  Re-check anytime:        trade doctor"
echo ""
