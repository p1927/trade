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


def artifact_needs_retrain(artifact: ModelArtifact | None, horizon) -> bool:
    """True when stored Ridge artifact is missing or out of sync with the factor matrix."""
    if artifact is None or not artifact.feature_names or not artifact.coefficients:
        return True
    if artifact.horizon_name and artifact.horizon_name != horizon.name:
        return True
    history = load_aligned_factor_history(days=500)
    if history.empty:
        return False
    try:
        from trade_integrations.dataflows.index_research.factor_matrix import build_factor_matrix

        _, _, current_names = build_factor_matrix(history, horizon)
    except Exception as exc:
        logger.debug("index calibrator: factor matrix probe failed: %s", exc)
        return True
    if not current_names:
        return True
    return set(current_names) != set(artifact.feature_names)


def ensure_ridge_model_artifact(*, horizon_days: int | None = None) -> ModelArtifact | None:
    """Load stored Ridge artifact or retrain when feature universe changed."""
    horizon = resolve_horizon(horizon_days)
    artifact = load_stored_model_artifact()
    if not artifact_needs_retrain(artifact, horizon):
        return artifact
    logger.info("index calibrator: retraining Ridge — artifact stale or missing")
    return retrain(horizon_days=horizon.days)


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


def _ensure_news_features_backfilled() -> dict[str, object] | None:
    try:
        from trade_integrations.dataflows.index_research.news_event_features import (
            backfill_news_event_features,
        )

        return backfill_news_event_features(ticker="NIFTY")
    except Exception as exc:
        logger.debug("news feature backfill before retrain skipped: %s", exc)
        return None


def retrain(*, horizon_days: int | None = None) -> ModelArtifact | None:
    """Retrain macro Ridge model when aligned history is sufficient."""
    _ensure_news_features_backfilled()
    horizon = resolve_horizon(horizon_days)
    history = load_aligned_factor_history(days=500)
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

    from trade_integrations.dataflows.index_research.direction_calibration import sync_artifact_direction_oos

    sync_artifact_direction_oos(artifact)
    store_model_artifact(artifact)
    return artifact
