"""automl_cached — reads offline PyCaret/AutoGluon artifact (no heavy import on hot path)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view

logger = logging.getLogger(__name__)

_ARTIFACT_NAME = "automl_forecast_latest.json"


def automl_artifact_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / _ARTIFACT_NAME


def run_automl_cached(ctx: TrackContext) -> ForecastTrack:
    path = automl_artifact_path(ctx.ticker)
    if not path.is_file():
        return ForecastTrack(
            track_id="automl_cached",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=False,
            provenance={"reason": "artifact_missing", "path": str(path)},
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ForecastTrack(
            track_id="automl_cached",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=False,
            provenance={"reason": "artifact_read_failed", "error": str(exc)},
        )

    horizon_days = int(ctx.horizon.days)
    pred = float(payload.get("expected_return_pct") or 0.0)
    artifact_horizon = int(payload.get("horizon_days") or horizon_days)
    if artifact_horizon != horizon_days:
        logger.debug(
            "automl_cached horizon mismatch artifact=%s ctx=%s",
            artifact_horizon,
            horizon_days,
        )

    return ForecastTrack(
        track_id="automl_cached",
        expected_return_pct=round(pred, 4),
        view=classify_index_view(pred),
        backtest_eligible=False,
        confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
        provenance={
            "source": "automl_cached",
            "model_type": payload.get("model_type"),
            "as_of": payload.get("as_of"),
            "artifact_path": str(path),
        },
    )
