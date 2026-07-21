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
  "$PY" - <<PY
import sys
sys.path.insert(0, "${ROOT}/integrations")
from trade_integrations.ml_runtime_env import ensure_libomp_symlink

ok, message = ensure_libomp_symlink()
print(f"[prediction-ml] libomp: {message}")
if not ok:
    print(f"[prediction-ml] note: {message}", file=sys.stderr)
PY
fi

echo "[prediction-ml] installing Python packages into $(dirname "$PY") ..."
"$PIP" install -q --upgrade pip
"$PIP" install -q \
  "scikit-learn>=1.3" \
  "shap>=0.44" \
  "lightgbm>=4.0" \
  "xgboost>=2.0" \
  "statsmodels>=0.14" \
  "pandas-ta>=0.3" \
  "darts>=0.30" \
  "vectorbt>=0.26" \
  "backtrader>=1.9"

echo "[prediction-ml] verifying imports ..."
stack_verify_prediction_ml
"$PY" - <<'PY'
import statsmodels
import pandas_ta
import vectorbt
import backtrader

print(
    "ok extras:",
    f"statsmodels={statsmodels.__version__}",
    f"vectorbt={vectorbt.__version__}",
)
PY

echo "[prediction-ml] done"
