#!/usr/bin/env python3
"""Verify prediction ML runtime (libomp + sklearn/shap + lightgbm/xgboost/darts). Used by trade preflight."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if INTEGRATIONS.is_dir() and str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.ml_runtime_env import prepare_yfinance_runtime, verify_prediction_ml  # noqa: E402

prepare_yfinance_runtime()


def main() -> int:
    ok, message = verify_prediction_ml()
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
