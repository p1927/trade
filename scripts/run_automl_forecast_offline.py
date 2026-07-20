#!/usr/bin/env python3
"""Offline AutoML forecast — PyCaret or sklearn fallback; writes hub artifact."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def _load_panel(days: int):
    from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

    return load_aligned_factor_history(days=days)


def _sklearn_fallback(panel, horizon_days: int) -> dict:
    from trade_integrations.dataflows.index_research.backtest_runner import _forward_return_pct
    from trade_integrations.dataflows.index_research.factor_matrix import build_factor_matrix
    from trade_integrations.dataflows.index_research.horizon import resolve_horizon
    from sklearn.linear_model import Ridge

    horizon = resolve_horizon(horizon_days)
    matrix, feature_names = build_factor_matrix(panel, horizon)
    if matrix.empty or len(matrix) < 80:
        raise ValueError("insufficient_panel_for_automl")
    y = _forward_return_pct(panel["close"].astype(float), horizon_days)
    y = y.loc[matrix.index]
    valid = y.notna()
    x = matrix.loc[valid]
    y = y.loc[valid]
    model = Ridge(alpha=1.0)
    model.fit(x.values, y.values)
    live_x = x.iloc[-1:].values
    pred = float(model.predict(live_x)[0])
    return {
        "expected_return_pct": round(pred, 4),
        "model_type": "sklearn_ridge_fallback",
        "horizon_days": horizon_days,
        "feature_count": len(feature_names),
        "train_rows": len(y),
    }


def _pycaret_forecast(panel, horizon_days: int) -> dict:
    from pycaret.time_series import compare_models, finalize_model, predict_model, setup

    exog_cols = [
        c
        for c in panel.columns
        if c not in {"date", "close", "open", "high", "low", "volume"}
        and str(panel[c].dtype) != "object"
    ][:12]
    data = panel[["close"] + exog_cols].copy()
    data = data.rename(columns={"close": "y"})
    exp = setup(
        data=data,
        target="y",
        fh=horizon_days,
        fold=3,
        session_id=42,
        verbose=False,
        exogenous=exog_cols if exog_cols else None,
    )
    best = compare_models(include=["lr", "ridge", "lasso", "en", "rf"], verbose=False)
    final = finalize_model(best)
    preds = predict_model(final, verbose=False)
    pred_col = [c for c in preds.columns if c not in data.columns and c != "y"]
    pred = float(preds[pred_col[0]].iloc[-1]) if pred_col else 0.0
    return {
        "expected_return_pct": round(pred, 4),
        "model_type": "pycaret_time_series",
        "horizon_days": horizon_days,
        "exogenous": exog_cols,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline AutoML forecast for NIFTY")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--engine", choices=("auto", "pycaret", "sklearn"), default="auto")
    args = parser.parse_args()

    panel = _load_panel(args.days)
    if panel is None or panel.empty:
        print(json.dumps({"status": "error", "message": "no_panel"}))
        return 1

    result: dict
    engine = args.engine
    if engine == "auto":
        try:
            import pycaret.time_series  # noqa: F401

            engine = "pycaret"
        except ImportError:
            engine = "sklearn"

    try:
        if engine == "pycaret":
            result = _pycaret_forecast(panel, args.horizon_days)
        else:
            result = _sklearn_fallback(panel, args.horizon_days)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1

    from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.automl_cached import (
        automl_artifact_path,
    )

    payload = {
        **result,
        "status": "ok",
        "ticker": args.ticker.strip().upper(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
    }
    path = automl_artifact_path(args.ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "path": str(path), **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
