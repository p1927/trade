"""Plug-and-play forecast lab — independent tracks, combiners, optional cause layer.

Single entry: ``run_forecast_lab`` in ``api.py``.
Disable entirely via ``INDEX_PREDICTION_LAB_ENABLED=0``.
"""

from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
from trade_integrations.dataflows.index_research.prediction_algorithms.config import lab_enabled
from trade_integrations.dataflows.index_research.prediction_algorithms.types import (
    ForecastLabResult,
    ForecastTrack,
    TrackContext,
)

__all__ = [
    "ForecastLabResult",
    "ForecastTrack",
    "TrackContext",
    "lab_enabled",
    "run_forecast_lab",
]
