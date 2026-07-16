#!/usr/bin/env bash
# Process pending Nautilus bridge execution intents (Phase 5 queue).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv-nautilus"
PY="${ROOT}/.venv/bin/python"

if [[ -x "${VENV}/bin/python" ]]; then
  PY="${VENV}/bin/python"
elif ! command -v "$PY" >/dev/null 2>&1; then
  PY="python3"
fi

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

export PYTHONPATH="${ROOT}/integrations${PYTHONPATH:+:${PYTHONPATH}}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1

exec "$PY" -m nautilus_openalgo_bridge.runtime.run_process_intents "$@"
