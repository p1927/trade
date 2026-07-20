"""arimax_macro — statsmodels SARIMAX with macro exogenous pct-change factors."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.backtest_runner import _forward_return_pct
from trade_integrations.dataflows.index_research.ml_experiments_defer import (
    resolve_direction_oos_pct,
    should_run_experiment,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history
from trade_integrations.dataflows.index_research.views import classify_index_view

logger = logging.getLogger(__name__)

_EXPERIMENT_ID = "arimax_macro"
_MIN_TRAIN_ROWS = 120
_EXOG_COLS: tuple[str, ...] = (
    "fii_net_5d",
    "india_vix",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "repo_rate_lag_1w",
    "cpi_yoy_proxy_lag_2w",
)


def run_arimax_macro(ctx: TrackContext) -> ForecastTrack:
    direction_oos = resolve_direction_oos_pct(ctx.ticker)
    if not should_run_experiment(_EXPERIMENT_ID, direction_oos_pct=direction_oos):
        return _deferred(direction_oos)

    try:
        import statsmodels.api as sm
    except ImportError:
        return _unavailable("statsmodels_not_installed", direction_oos=direction_oos)

    try:
        pred_pct, prov = _predict_arimax(ctx, sm_module=sm)
    except ValueError as exc:
        if str(exc) == "arimax_not_converged":
            return _unavailable("arimax_not_converged", direction_oos=direction_oos)
        logger.warning("arimax_macro failed: %s", exc)
        return _unavailable("train_predict_failed", error=str(exc))
    except Exception as exc:
        logger.warning("arimax_macro failed: %s", exc)
        return _unavailable("train_predict_failed", error=str(exc))

    return ForecastTrack(
        track_id="arimax_macro",
        expected_return_pct=pred_pct,
        view=classify_index_view(pred_pct),
        backtest_eligible=True,
        provenance={**prov, "experiment_id": _EXPERIMENT_ID, "direction_oos_pct": direction_oos},
    )


def _predict_arimax(ctx: TrackContext, *, sm_module) -> tuple[float, dict]:
    panel = load_aligned_factor_history(days=500)
    if panel is None or panel.empty or "close" not in panel.columns:
        raise ValueError("insufficient_panel_rows:0")

    horizon_days = int(ctx.horizon.days)
    closes = panel["close"].astype(float)
    y = _forward_return_pct(closes, horizon_days)
    exog_cols = [c for c in _EXOG_COLS if c in panel.columns]
    if not exog_cols:
        exog_cols = [
            c
            for c in ("fii_net_5d", "india_vix", "usd_inr_momentum_5d")
            if c in panel.columns
        ]
    if not exog_cols:
        raise ValueError("no_exog_columns")

    exog = panel[exog_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    valid = y.notna()
    y_clean = y[valid].astype(float)
    exog_clean = exog.loc[valid].astype(float)
    if len(y_clean) < _MIN_TRAIN_ROWS:
        raise ValueError(f"insufficient_training_pairs:{len(y_clean)}")

    model = sm_module.tsa.statespace.SARIMAX(
        y_clean.values,
        exog=exog_clean.values,
        order=(1, 0, 1),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fit = model.fit(disp=False, maxiter=200, method="lbfgs")
    mle_retvals = getattr(fit, "mle_retvals", None) or {}
    if not mle_retvals.get("converged", True):
        raise ValueError("arimax_not_converged")
    live_exog = np.array(
        [[float(ctx.macro_factors.get(c, 0.0) or 0.0) for c in exog_cols]],
        dtype=float,
    )
    forecast = fit.forecast(steps=1, exog=live_exog)
    pred = float(forecast.iloc[0] if hasattr(forecast, "iloc") else forecast[0])
    return round(pred, 4), {
        "source": "arimax_macro",
        "train_rows": len(y_clean),
        "exog_cols": exog_cols,
        "horizon_days": horizon_days,
        "aic": round(float(fit.aic), 2),
    }


def _deferred(direction_oos: float) -> ForecastTrack:
    return ForecastTrack(
        track_id="arimax_macro",
        expected_return_pct=0.0,
        view="neutral",
        available=False,
        backtest_eligible=False,
        provenance={
            "reason": "deferred_phase3_gate_passed",
            "experiment_id": _EXPERIMENT_ID,
            "direction_oos_pct": direction_oos,
        },
    )


def _unavailable(reason: str, **extra) -> ForecastTrack:
    return ForecastTrack(
        track_id="arimax_macro",
        expected_return_pct=0.0,
        view="neutral",
        available=False,
        backtest_eligible=False,
        provenance={"reason": reason, **extra},
    )
