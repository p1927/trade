"""darts_macro — Darts SKLearnModel with past covariates from factor panel."""

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

_EXPERIMENT_ID = "darts_macro"
_MIN_TRAIN_ROWS = 120
_COVARIATE_COLS: tuple[str, ...] = (
    "fii_net_5d",
    "india_vix",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "nifty_rsi_14",
)


def run_darts_macro(ctx: TrackContext) -> ForecastTrack:
    direction_oos = resolve_direction_oos_pct(ctx.ticker)
    if not should_run_experiment(_EXPERIMENT_ID, direction_oos_pct=direction_oos):
        return _deferred(direction_oos)

    try:
        from darts import TimeSeries
        from darts.models import SKLearnModel
    except ImportError:
        return _unavailable("darts_not_installed", direction_oos=direction_oos)

    try:
        pred_pct, prov = _predict_darts(ctx, TimeSeries=TimeSeries, SKLearnModel=SKLearnModel)
    except Exception as exc:
        logger.warning("darts_macro failed: %s", exc)
        return _unavailable("train_predict_failed", error=str(exc))

    return ForecastTrack(
        track_id="darts_macro",
        expected_return_pct=pred_pct,
        view=classify_index_view(pred_pct),
        backtest_eligible=True,
        provenance={**prov, "experiment_id": _EXPERIMENT_ID, "direction_oos_pct": direction_oos},
    )


def _prepare_darts_frame(
    panel: pd.DataFrame,
    horizon_days: int,
    cov_cols: list[str],
) -> pd.DataFrame:
    working = panel.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date"]).set_index("date").sort_index()
    working = working[~working.index.duplicated(keep="last")]
    if working.empty or "close" not in working.columns:
        raise ValueError("insufficient_panel_rows:0")

    for col in cov_cols:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")

    close = pd.to_numeric(working["close"], errors="coerce")
    bidx = pd.bdate_range(working.index.min(), working.index.max())
    aligned = working.reindex(bidx)
    aligned["close"] = close.reindex(bidx).ffill().bfill()
    for col in cov_cols:
        if col in aligned.columns:
            aligned[col] = aligned[col].ffill().bfill().fillna(0.0)

    aligned["y"] = _forward_return_pct(aligned["close"].astype(float), horizon_days)
    first = aligned["y"].first_valid_index()
    last = aligned["y"].last_valid_index()
    if first is None or last is None:
        raise ValueError("insufficient_panel_rows:0")

    df = aligned.loc[first:last].dropna(subset=["y"])
    if len(df) < _MIN_TRAIN_ROWS:
        raise ValueError(f"insufficient_training_pairs:{len(df)}")
    return df


def _predict_darts(ctx: TrackContext, *, TimeSeries, SKLearnModel) -> tuple[float, dict]:
    panel = load_aligned_factor_history(days=400)
    if panel is None or panel.empty or "close" not in panel.columns or "date" not in panel.columns:
        raise ValueError("insufficient_panel_rows:0")

    horizon_days = int(ctx.horizon.days)
    cov_cols = [c for c in _COVARIATE_COLS if c in panel.columns]
    if not cov_cols:
        cov_cols = [c for c in ("fii_net_5d", "india_vix") if c in panel.columns]

    df = _prepare_darts_frame(panel, horizon_days, cov_cols)

    series_y = TimeSeries.from_series(df["y"], fill_missing_dates=True, freq="B")
    past_cov = TimeSeries.from_dataframe(df[cov_cols], fill_missing_dates=True, freq="B")

    try:
        import lightgbm as lgb

        lgb_model = lgb.LGBMRegressor(
            n_estimators=60,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            verbose=-1,
        )
        model = SKLearnModel(model=lgb_model, lags=5, lags_past_covariates=3)
    except ImportError:
        from sklearn.linear_model import Ridge

        model = SKLearnModel(model=Ridge(alpha=1.0), lags=5, lags_past_covariates=3)

    model.fit(series_y, past_covariates=past_cov)
    pred_series = model.predict(n=1, series=series_y, past_covariates=past_cov)
    pred = float(pred_series.values()[-1][0])

    return round(pred, 4), {
        "source": "darts_macro",
        "train_rows": len(df),
        "covariate_cols": cov_cols,
        "horizon_days": horizon_days,
    }


def _deferred(direction_oos: float) -> ForecastTrack:
    return ForecastTrack(
        track_id="darts_macro",
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
        track_id="darts_macro",
        expected_return_pct=0.0,
        view="neutral",
        available=False,
        backtest_eligible=False,
        provenance={"reason": reason, **extra},
    )
