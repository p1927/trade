#!/usr/bin/env bash
# Install prediction ML + execution-sim Python deps into repo .venv (macOS: libomp for LightGBM).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"
stack_load_env

PY="$(stack_pick_python)"
PIP="${PY%/python}/pip"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if ! brew list libomp &>/dev/null; then
    echo "[prediction-ml] installing libomp (required for LightGBM on macOS) ..."
    brew install libomp
  fi
  stack_export_ml_runtime_env
fi

echo "[prediction-ml] installing Python packages into $(dirname "$PY") ..."
"$PIP" install -q --upgrade pip
"$PIP" install -q \
  "lightgbm>=4.0" \
  "xgboost>=2.0" \
  "statsmodels>=0.14" \
  "pandas-ta>=0.3" \
  "darts>=0.30" \
  "vectorbt>=0.26" \
  "backtrader>=1.9"

echo "[prediction-ml] verifying imports ..."
"$PY" - <<'PY'
import lightgbm
import xgboost
import statsmodels
import pandas_ta
import darts
import vectorbt
import backtrader

print(
    "ok:",
    f"lightgbm={lightgbm.__version__}",
    f"xgboost={xgboost.__version__}",
    f"darts={darts.__version__}",
    f"vectorbt={vectorbt.__version__}",
)
PY

echo "[prediction-ml] done"
