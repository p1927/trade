#!/usr/bin/env bash
# Start the Nautilus TradingNode watch bridge (OpenAlgo → WatchActor → signals).
# Legacy poll loop: --legacy-poll or --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv-nautilus"
PY="${ROOT}/.venv/bin/python"

_pick_python() {
  local candidate
  for candidate in "${VENV}/bin/python" "${ROOT}/.venv/bin/python" python3; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" -c "import nautilus_openalgo_bridge" 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  echo "[run_nautilus_watch] nautilus_openalgo_bridge not importable — run: ./scripts/setup_nautilus.sh" >&2
  return 1
}

PY="$(_pick_python)" || exit 1

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

export PYTHONPATH="${ROOT}/integrations${PYTHONPATH:+:${PYTHONPATH}}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1

exec "$PY" -m nautilus_openalgo_bridge.runtime.run_watch_node "$@"
