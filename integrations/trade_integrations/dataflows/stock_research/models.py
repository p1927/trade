"""Normalized models for stock trade plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trade_integrations.dataflows.company_research.models import StageResult


@dataclass
class StockResearchDoc:
    """Structured stock trade plan artifact."""

    ticker: str
    as_of: datetime
    lookahead_days: int
    market: str = "IN"
    execution_market: str = "IN"
    spot: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    browse_summary: dict[str, Any] = field(default_factory=dict)
    prediction: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    ranked_strategies: list[dict[str, Any]] = field(default_factory=list)
    recommended: dict[str, Any] = field(default_factory=dict)
    payoff: dict[str, Any] = field(default_factory=dict)
    payoff_over_time: dict[str, Any] = field(default_factory=dict)
    charges: dict[str, Any] = field(default_factory=dict)
    implementation_steps: list[dict[str, Any]] = field(default_factory=list)
    stages: list[StageResult] = field(default_factory=list)
