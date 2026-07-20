"""Normalized models for the index research pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trade_integrations.dataflows.company_research.models import StageResult


@dataclass
class FactorSnapshot:
    """Single factor observation for a given date."""

    date: str
    factor: str
    value: float
    z_score: float | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PredictionRecord:
    """Logged forecast for later reconciliation against actual Nifty moves."""

    predicted_at: datetime
    horizon_days: int
    spot_at_prediction: float
    expected_return_pct: float
    range_low: float
    range_high: float
    actual_return_pct: float | None = None
    direction_correct: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConstituentRow:
    """Static constituent metadata for index attribution."""

    symbol: str
    name: str = ""
    weight: float = 0.0
    sector: str = ""


@dataclass
class ConstituentSignal:
    """Per-constituent signal aggregated into index view."""

    symbol: str
    weight: float = 0.0
    sector: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    factors: list[dict[str, Any]] = field(default_factory=list)
    sentiment_score: float | None = None
    momentum_7d_pct: float | None = None
    contribution_to_index_pct: float | None = None


@dataclass
class IndexResearchDoc:
    """Structured index research dossier built by the pipeline."""

    ticker: str
    as_of: datetime
    horizon: dict[str, Any] = field(default_factory=dict)
    spot: float | None = None
    spot_source: str | None = None
    spot_error: str | None = None
    data_warnings: list[str] = field(default_factory=list)
    prediction: dict[str, Any] = field(default_factory=dict)
    regime: dict[str, Any] = field(default_factory=dict)
    global_factors: list[dict[str, Any]] = field(default_factory=list)
    constituent_signals: list[dict[str, Any]] = field(default_factory=list)
    sector_breadth: dict[str, Any] = field(default_factory=dict)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    accuracy: dict[str, Any] = field(default_factory=dict)
    factor_explanation: dict[str, Any] = field(default_factory=dict)
    factor_sensitivity: list[dict[str, Any]] = field(default_factory=list)
    event_impact_curves: list[dict[str, Any]] = field(default_factory=list)
    upcoming_events: list[dict[str, Any]] = field(default_factory=list)
    cascade_calibration: dict[str, Any] = field(default_factory=dict)
    news_impact: dict[str, Any] = field(default_factory=dict)
    event_overlay: dict[str, Any] = field(default_factory=dict)
    news_shock_calibration: dict[str, Any] = field(default_factory=dict)
    stages: list[StageResult] = field(default_factory=list)
    pipeline_log: list[dict[str, Any]] = field(default_factory=list)
