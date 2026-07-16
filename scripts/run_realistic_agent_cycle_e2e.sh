#!/usr/bin/env bash
# Realistic autonomous agent cycle: analysis → forced orders → watch metrics → re-trigger.
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

if [[ "${1:-}" != "--skip-cleanup" ]]; then
  python3 "$ROOT/scripts/cleanup_autonomous_agents.py"
fi

exec python3 "$ROOT/scripts/run_realistic_agent_cycle_e2e.py" "$@"
