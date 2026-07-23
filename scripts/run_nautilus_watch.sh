#!/usr/bin/env bash
# Start the Nautilus TradingNode watch bridge (OpenAlgo → WatchActor → signals).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv-nautilus"
export PYTHONPATH="${ROOT}/integrations:${ROOT}/tradingagents${PYTHONPATH:+:${PYTHONPATH}}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1

_pick_python() {
  local candidate
  # Main trade venv has integrations + tradingagents deps; nautilus_trader installed via setup.
  for candidate in "${ROOT}/.venv/bin/python" "${VENV}/bin/python" python3; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" -c "
from nautilus_openalgo_bridge.runtime.run_watch_node import main  # noqa: F401
import nautilus_trader
" 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  echo "[run_nautilus_watch] nautilus watch dependencies missing — run: ./scripts/setup_nautilus.sh" >&2
  return 1
}

PY="$(_pick_python)" || exit 1

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

exec "$PY" -m nautilus_openalgo_bridge.runtime.run_watch_node "$@"
