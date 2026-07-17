"""Shared types for the forecast lab."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.predictor import ModelArtifact

LabRunMode = Literal["tracks_only", "combine"]


@dataclass
class TrackContext:
    """Inputs for all tracks — built from hub cache or live aggregator."""

    ticker: str
    spot: float
    horizon: HorizonProfile
    macro_factors: dict[str, Any]
    signals: list[ConstituentSignal] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    scenario_anchor: float | None = None
    debate_payload: dict[str, Any] | None = None
    model_artifact: ModelArtifact | None = None
    as_of_day: str | None = None
    macro_trust_multiplier: float = 1.0
    prediction_snapshot: dict[str, Any] | None = None
    legacy_prediction: dict[str, Any] | None = None


@dataclass
class ForecastTrack:
    track_id: str
    expected_return_pct: float
    view: str
    available: bool = True
    confidence: float | None = None
    backtest_eligible: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CombinationResult:
    combiner_id: str
    expected_return_pct: float
    view: str
    weights: dict[str, float] = field(default_factory=dict)
    tracks_used: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ForecastLabResult:
    ticker: str
    horizon_days: int
    mode: LabRunMode
    enabled: bool = True
    forecast_tracks: dict[str, dict[str, Any]] = field(default_factory=dict)
    combiner: dict[str, Any] | None = None
    cause_stress_index: float | None = None
    cause_stress_label: str | None = None
    active_causes: list[str] = field(default_factory=list)
    channel_attribution: dict[str, float] | None = None
    active_combiner: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
