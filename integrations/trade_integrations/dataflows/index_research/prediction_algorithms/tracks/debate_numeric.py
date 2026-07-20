"""debate_numeric track — structured agent debate forecast."""

from __future__ import annotations

from trade_integrations.research.debate_synthesis import extract_structured_debate

from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    debate_backtest_eligible,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext


def run_debate_numeric(ctx: TrackContext) -> ForecastTrack:
    archive_ok = debate_backtest_eligible(ctx.ticker)
    debate = extract_structured_debate(ctx.debate_payload)
    if not debate or debate.get("expected_return_pct") is None:
        return ForecastTrack(
            track_id="debate_numeric",
            expected_return_pct=0.0,
            view=str(debate.get("view") or "neutral") if debate else "neutral",
            available=False,
            backtest_eligible=False,
            provenance={
                "reason": "debate_unavailable",
                "backtest_eligible": False,
                "debate_archive_eligible": archive_ok,
            },
        )

    value = float(debate.get("expected_return_pct") or 0.0)
    return ForecastTrack(
        track_id="debate_numeric",
        expected_return_pct=round(value, 4),
        view=str(debate.get("view") or "neutral"),
        confidence=_optional_float(debate.get("direction_confidence")),
        backtest_eligible=archive_ok,
        provenance={
            "source": "agent_debate",
            "backtest_eligible": archive_ok,
            "debate_archive_eligible": archive_ok,
        },
    )


def _optional_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
