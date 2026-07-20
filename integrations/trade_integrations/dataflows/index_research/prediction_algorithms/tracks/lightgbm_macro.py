"""lightgbm_macro — Phase G deferred experiment track (not combiner merge v1)."""

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

_EXPERIMENT_ID = "lightgbm_ensemble"


def run_lightgbm_macro(ctx: TrackContext) -> ForecastTrack:
    direction_oos = resolve_direction_oos_pct(ctx.ticker)
    if not should_run_experiment(_EXPERIMENT_ID, direction_oos_pct=direction_oos):
        return ForecastTrack(
            track_id="lightgbm_macro",
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

    artifact = ctx.model_artifact or load_stored_model_artifact()
    if artifact is None or not artifact.feature_names:
        return ForecastTrack(
            track_id="lightgbm_macro",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=False,
            provenance={"reason": "no_model_artifact"},
        )

    try:
        import lightgbm as lgb
    except ImportError:
        return ForecastTrack(
            track_id="lightgbm_macro",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=False,
            provenance={"reason": "lightgbm_not_installed", "direction_oos_pct": direction_oos},
        )

    try:
        pred_pct, prov = predict_tabular_macro(
            ctx.macro_factors,
            ctx.horizon,
            artifact,
            as_of_day=ctx.as_of_day,
            train_fn=_train_lgb,
            predict_fn=_predict_lgb,
        )
    except Exception as exc:
        logger.warning("lightgbm_macro failed: %s", exc)
        return ForecastTrack(
            track_id="lightgbm_macro",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=False,
            provenance={"reason": "train_predict_failed", "error": str(exc)},
        )

    return ForecastTrack(
        track_id="lightgbm_macro",
        expected_return_pct=pred_pct,
        view=classify_index_view(pred_pct),
        backtest_eligible=True,
        provenance={
            **prov,
            "source": "lightgbm_macro",
            "experiment_id": _EXPERIMENT_ID,
            "direction_oos_pct": direction_oos,
        },
    )


def _train_lgb(rows_x, rows_y, feature_names):
    import lightgbm as lgb

    train = lgb.Dataset(rows_x, label=rows_y, feature_name=feature_names)
    params = {
        "objective": "regression",
        "metric": "mae",
        "verbosity": -1,
        "seed": 42,
        "num_leaves": 16,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
    }
    return lgb.train(params, train, num_boost_round=80)


def _predict_lgb(model, live_vec):
    import numpy as np

    data = live_vec if isinstance(live_vec, np.ndarray) else np.asarray(live_vec, dtype=np.float64)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return model.predict(data)
