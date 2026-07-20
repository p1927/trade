"""xgboost_macro — XGBoost tabular macro experiment track."""

from __future__ import annotations

import logging

from trade_integrations.dataflows.index_research.ml_adapters.tabular_track_base import predict_tabular_macro
from trade_integrations.dataflows.index_research.ml_experiments_defer import (
    resolve_direction_oos_pct,
    should_run_experiment,
)
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view

logger = logging.getLogger(__name__)

_EXPERIMENT_ID = "xgboost_ensemble"


def run_xgboost_macro(ctx: TrackContext) -> ForecastTrack:
    direction_oos = resolve_direction_oos_pct(ctx.ticker)
    if not should_run_experiment(_EXPERIMENT_ID, direction_oos_pct=direction_oos):
        return _deferred_track(direction_oos)

    artifact = ctx.model_artifact or load_stored_model_artifact()
    if artifact is None or not artifact.feature_names:
        return _unavailable("no_model_artifact")

    try:
        import xgboost as xgb
    except ImportError:
        return _unavailable("xgboost_not_installed", direction_oos=direction_oos)

    try:
        pred_pct, prov = predict_tabular_macro(
            ctx.macro_factors,
            ctx.horizon,
            artifact,
            as_of_day=ctx.as_of_day,
            train_fn=_train_xgb,
            predict_fn=_predict_xgb,
        )
    except Exception as exc:
        logger.warning("xgboost_macro failed: %s", exc)
        return _unavailable("train_predict_failed", error=str(exc))

    return ForecastTrack(
        track_id="xgboost_macro",
        expected_return_pct=pred_pct,
        view=classify_index_view(pred_pct),
        backtest_eligible=True,
        provenance={**prov, "experiment_id": _EXPERIMENT_ID, "direction_oos_pct": direction_oos},
    )


def _deferred_track(direction_oos: float) -> ForecastTrack:
    return ForecastTrack(
        track_id="xgboost_macro",
        expected_return_pct=0.0,
        view="neutral",
        available=False,
        backtest_eligible=False,
        provenance={
            "reason": "deferred_phase3_gate_passed",
            "direction_oos_pct": direction_oos,
            "experiment_id": _EXPERIMENT_ID,
        },
    )


def _unavailable(reason: str, **extra) -> ForecastTrack:
    return ForecastTrack(
        track_id="xgboost_macro",
        expected_return_pct=0.0,
        view="neutral",
        available=False,
        backtest_eligible=False,
        provenance={"reason": reason, **extra},
    )


def _train_xgb(rows_x, rows_y, feature_names):
    import xgboost as xgb

    model = xgb.XGBRegressor(
        n_estimators=80,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        objective="reg:squarederror",
        feature_names=feature_names,
    )
    model.fit(rows_x, rows_y)
    return model


def _predict_xgb(model, live_vec):
    return model.predict(live_vec)
