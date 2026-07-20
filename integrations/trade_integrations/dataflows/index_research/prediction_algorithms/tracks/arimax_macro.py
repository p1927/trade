"""arimax_macro — statsmodels SARIMAX with macro exogenous factors.

Exogenous columns are z-score scaled on the training window and zero-variance
columns are dropped before fit — statsmodels maintainers recommend scaling exog
for numerical stability; constant exog columns create singular designs.
"""

from __future__ import annotations

import logging
from typing import Any

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
_MIN_EXOG_STD = 1e-12
_EXOG_COLS: tuple[str, ...] = (
    "fii_net_5d",
    "india_vix",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "repo_rate_lag_1w",
    "cpi_yoy_proxy_lag_2w",
)


def _prepare_exog_for_sarimax(
    exog_train: pd.DataFrame,
    live_factors: dict[str, Any],
    *,
    min_std: float = _MIN_EXOG_STD,
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, dict[str, float]], list[str]]:
    """Z-score exog on the training window; drop columns with ~zero variance."""
    kept: list[str] = []
    dropped: list[str] = []
    scales: dict[str, dict[str, float]] = {}

    for col in exog_train.columns:
        series = pd.to_numeric(exog_train[col], errors="coerce").astype(float)
        std = float(series.std(ddof=0))
        if not np.isfinite(std) or std < min_std:
            dropped.append(col)
            continue
        mean = float(series.mean())
        kept.append(col)
        scales[col] = {"mean": round(mean, 6), "std": round(std, 6)}

    if not kept:
        raise ValueError("no_usable_exog_columns")

    train_scaled = np.column_stack(
        [((pd.to_numeric(exog_train[c], errors="coerce").astype(float) - scales[c]["mean"]) / scales[c]["std"]).values for c in kept]
    )
    live_scaled = np.array(
        [[(float(live_factors.get(c, 0.0) or 0.0) - scales[c]["mean"]) / scales[c]["std"] for c in kept]],
        dtype=float,
    )
    return train_scaled, live_scaled, kept, scales, dropped


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

    exog_scaled, live_exog_scaled, exog_cols_used, exog_scales, dropped_exog = _prepare_exog_for_sarimax(
        exog_clean,
        ctx.macro_factors,
    )
    if dropped_exog:
        logger.info(
            "arimax_macro dropped zero-variance exog columns: %s",
            ", ".join(dropped_exog),
        )

    model = sm_module.tsa.statespace.SARIMAX(
        y_clean.values,
        exog=exog_scaled,
        order=(1, 0, 1),
        enforce_stationarity=False,
        enforce_invertibility=False,
        concentrate_scale=True,
        use_exact_diffuse=True,
    )
    fit = model.fit(disp=False, maxiter=200, method="lbfgs")
    mle_retvals = getattr(fit, "mle_retvals", None) or {}
    if not mle_retvals.get("converged", True):
        raise ValueError("arimax_not_converged")
    forecast = fit.forecast(steps=1, exog=live_exog_scaled)
    pred = float(forecast.iloc[0] if hasattr(forecast, "iloc") else forecast[0])
    if not np.isfinite(pred):
        raise ValueError("arimax_non_finite_forecast")
    return round(pred, 4), {
        "source": "arimax_macro",
        "train_rows": len(y_clean),
        "exog_cols": exog_cols_used,
        "exog_cols_requested": exog_cols,
        "exog_dropped_zero_variance": dropped_exog,
        "exog_scales": exog_scales,
        "horizon_days": horizon_days,
        "aic": round(float(fit.aic), 2),
        "sarimax_stability": {
            "concentrate_scale": True,
            "use_exact_diffuse": True,
            "exog_standardized": True,
        },
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
