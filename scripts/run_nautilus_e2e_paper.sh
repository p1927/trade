#!/usr/bin/env bash
# Paper E2E: Vibe agent → watch/handoff → OpenAlgo execution → cancel → exit
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi
export PYTHONPATH="${ROOT}/integrations${PYTHONPATH:+:${PYTHONPATH}}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1
exec "${ROOT}/.venv/bin/python" "${ROOT}/scripts/run_nautilus_e2e_paper.py" "$@"
