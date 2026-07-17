"""Reuse NIFTY constituent signals from a cached index research hub artifact."""

from __future__ import annotations

from trade_integrations.context.hub import load_index_research_json
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc


def signals_from_cached_doc(cached_doc: IndexResearchDoc | None) -> list[ConstituentSignal]:
    """Build constituent signals from the last saved index research snapshot."""
    if cached_doc is None:
        return []
    signals: list[ConstituentSignal] = []
    for row in cached_doc.constituent_signals or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        weight = row.get("weight")
        try:
            weight_f = float(weight) if weight is not None else 0.0
        except (TypeError, ValueError):
            weight_f = 0.0
        sentiment = row.get("sentiment_score")
        try:
            sentiment_f = float(sentiment) if sentiment is not None else None
        except (TypeError, ValueError):
            sentiment_f = None
        momentum = row.get("momentum_7d_pct")
        try:
            momentum_f = float(momentum) if momentum is not None else None
        except (TypeError, ValueError):
            momentum_f = None
        signals.append(
            ConstituentSignal(
                symbol=symbol,
                weight=weight_f,
                sector=str(row.get("sector") or ""),
                events=list(row.get("events") or []),
                factors=list(row.get("factors") or []),
                sentiment_score=sentiment_f,
                momentum_7d_pct=momentum_f,
                contribution_to_index_pct=row.get("contribution_to_index_pct"),
            )
        )
    return signals


def load_cached_constituent_signals(ticker: str = "NIFTY") -> list[ConstituentSignal]:
    """Load constituent signals from hub index research for *ticker*."""
    return signals_from_cached_doc(load_index_research_json(ticker.strip().upper()))
