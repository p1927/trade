"""Constituent signal replay from archived company_research for walk-forward tracks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.models import ConstituentSignal

MIN_HYBRID_CONSTITUENTS = 8


def company_history_path(symbol: str, day: str) -> Path:
    return get_hub_dir() / symbol.strip().upper() / "company_research" / "history" / f"{day[:10]}.json"


def load_constituent_signals_for_day(day: str, macro_factors: dict | None = None) -> list[ConstituentSignal]:
    """Load archived constituent sentiment/momentum for a historical trading day."""
    signals: list[ConstituentSignal] = []
    constituents = load_nifty50_constituents()
    for row in constituents:
        path = company_history_path(row.symbol, day)
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sentiment = (payload.get("sentiment") or {}).get("score")
        try:
            sentiment_f = float(sentiment) if sentiment is not None else None
        except (TypeError, ValueError):
            sentiment_f = None
        momentum = payload.get("return_7d_pct") or payload.get("momentum_7d_pct")
        try:
            momentum_f = float(momentum) if momentum is not None else None
        except (TypeError, ValueError):
            momentum_f = None
        signals.append(
            ConstituentSignal(
                symbol=row.symbol,
                weight=row.weight,
                sector=row.sector,
                sentiment_score=sentiment_f,
                momentum_7d_pct=momentum_f,
            )
        )
    if len(signals) >= MIN_HYBRID_CONSTITUENTS:
        return signals

    # Fallback: index-level sentiment proxy so bottom_up / quant_ridge still produce a signal.
    factors = macro_factors or {}
    raw_sent = factors.get("index_sentiment")
    if raw_sent is not None:
        try:
            sent_f = float(raw_sent)
            if sent_f == sent_f:
                return [
                    ConstituentSignal(
                        symbol="_INDEX_SENTIMENT",
                        weight=1.0,
                        sector="index",
                        sentiment_score=sent_f,
                    )
                ]
        except (TypeError, ValueError):
            pass
    return signals


def bottom_up_archive_coverage(
    trading_dates: list[str],
    *,
    min_constituents: int = MIN_HYBRID_CONSTITUENTS,
) -> dict[str, Any]:
    """Share of dates with enough archived constituent history for hybrid replay."""
    if not trading_dates:
        return {"coverage_pct": 0.0, "eligible_days": 0, "total_days": 0}
    eligible = 0
    for day in trading_dates:
        signals = load_constituent_signals_for_day(day)
        if len(signals) >= min_constituents:
            eligible += 1
    total = len(trading_dates)
    return {
        "coverage_pct": round(100.0 * eligible / total, 1) if total else 0.0,
        "eligible_days": eligible,
        "total_days": total,
    }


def bottom_up_return_from_archives(day: str, *, horizon_days: int) -> float | None:
    """Replay bottom-up attribution when archived company_research/history exists."""
    signals = load_constituent_signals_for_day(day)
    if len(signals) < MIN_HYBRID_CONSTITUENTS:
        return None
    attributed = attribute_constituents(
        signals,
        horizon_days=horizon_days,
        as_of_day=day,
        use_calibration=False,
    )
    rollup = rollup_attribution(attributed)
    return float(rollup["total_contribution_pct"])
