#!/usr/bin/env bash
# Bootstrap Python 3.12+ venv for the Nautilus watch node (separate from trade .venv).
#
# Usage:
#   ./scripts/setup_nautilus.sh
#   ./scripts/setup_nautilus.sh --verify

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv-nautilus"
PY="${NAUTILUS_PYTHON:-python3.12}"

verify_only=false
if [[ "${1:-}" == "--verify" ]]; then
  verify_only=true
fi

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: $PY not found. Install Python 3.12+ or set NAUTILUS_PYTHON." >&2
  exit 1
fi

if [[ "$verify_only" == true ]]; then
  if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "Nautilus venv: missing (run ./scripts/setup_nautilus.sh)"
    exit 1
  fi
  export PYTHONPATH="${ROOT}/integrations:${ROOT}/tradingagents${PYTHONPATH:+:${PYTHONPATH}}"
  "${VENV}/bin/python" -c "import nautilus_trader, nautilus_openalgo_bridge, requests; print('nautilus_trader:', nautilus_trader.__version__)"
  exit 0
fi

if [[ ! -d "$VENV" ]]; then
  "$PY" -m venv "$VENV"
fi

"${VENV}/bin/pip" install -U pip wheel
# PyPI wheel is recommended; submodule is for source reference and sync.
"${VENV}/bin/pip" install "nautilus_trader>=1.228" redis requests

echo "Nautilus watch venv ready at ${VENV}"
export PYTHONPATH="${ROOT}/integrations:${ROOT}/tradingagents${PYTHONPATH:+:${PYTHONPATH}}"
"${VENV}/bin/python" -c "import nautilus_trader, nautilus_openalgo_bridge; print('nautilus_trader:', nautilus_trader.__version__)"
