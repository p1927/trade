"""Walk-forward retrain and drift detection for the index predictor."""

from __future__ import annotations

import logging

from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    load_stored_model_artifact,
    store_model_artifact,
    train_macro_ridge,
)
from trade_integrations.dataflows.index_research.sources.history_loader import (
    load_aligned_factor_history,
)

logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 0.20
_MIN_TRAINING_ROWS = 30


def should_retrain(mae_14d: float | None, *, baseline_mae: float | None = None) -> bool:
    """Return True when rolling MAE drift exceeds 20% vs training baseline."""
    if mae_14d is None:
        return False

    baseline = baseline_mae
    if baseline is None:
        artifact = load_stored_model_artifact()
        baseline = artifact.mae if artifact else None
    if baseline is None or baseline <= 0:
        return False

    drift = (float(mae_14d) - float(baseline)) / float(baseline)
    return drift > _DRIFT_THRESHOLD


def retrain(*, horizon_days: int | None = None) -> ModelArtifact | None:
    """Retrain macro Ridge model when aligned history is sufficient."""
    horizon = resolve_horizon(horizon_days)
    history = load_aligned_factor_history(days=365)
    min_rows = max(_MIN_TRAINING_ROWS, horizon.feature_window + horizon.days + 5)
    if history.empty or len(history) < min_rows:
        logger.info(
            "index calibrator: insufficient history (%s rows, need %s)",
            len(history),
            min_rows,
        )
        return None

    try:
        artifact = train_macro_ridge(history, horizon)
    except ImportError:
        logger.warning("index calibrator: scikit-learn unavailable; skip retrain")
        return None

    store_model_artifact(artifact)
    return artifact
