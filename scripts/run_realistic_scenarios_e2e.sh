#!/usr/bin/env bash
# Realistic E2E — India (NIFTY / OpenAlgo) by default; set REALISTIC_E2E_MARKET=us for Alpaca SPY.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export NAUTILUS_WATCH_ENABLE="${NAUTILUS_WATCH_ENABLE:-true}"
export NAUTILUS_BRIDGE_ALERT_OUTSIDE_HOURS="${NAUTILUS_BRIDGE_ALERT_OUTSIDE_HOURS:-true}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1
export REALISTIC_E2E_MARKET="${REALISTIC_E2E_MARKET:-in}"
export REALISTIC_E2E_SYMBOL="${REALISTIC_E2E_SYMBOL:-NIFTY}"

python3 "$ROOT/scripts/cleanup_autonomous_agents.py"

MARKET="${REALISTIC_E2E_MARKET:-in}"
SYMBOL="${REALISTIC_E2E_SYMBOL:-NIFTY}"

echo ""
echo "==> Scenario A ($MARKET): LLM analysis → forced orders → watch alert → revision"
python3 "$ROOT/scripts/run_realistic_agent_cycle_e2e.py" --skip-cleanup --market "$MARKET" --symbol "$SYMBOL" --metric-delay-sec 20

echo ""
echo "==> Scenario B ($MARKET): mechanical entry → spot stop → flatten"
python3 "$ROOT/scripts/run_realistic_stop_watch_e2e.py" --market "$MARKET" --symbol "$SYMBOL" --metric-delay-sec 20

echo ""
echo "==> All realistic E2E scenarios passed ($MARKET / $SYMBOL)"
