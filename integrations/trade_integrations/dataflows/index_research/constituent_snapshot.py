"""Reuse NIFTY constituent signals from a cached index research hub artifact."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from trade_integrations.context.hub import get_hub_dir, load_index_research_json
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc

logger = logging.getLogger(__name__)

MIN_CONSTITUENT_SIGNALS = 40
NIFTY50_CONSTITUENT_TARGET = 50


def is_partial_constituent_cache(count: int) -> bool:
    """True when cached constituent count is too low for a reliable bottom-up block."""
    return count < MIN_CONSTITUENT_SIGNALS


def partial_constituent_warning(count: int) -> str:
    return (
        f"Only {count} of {NIFTY50_CONSTITUENT_TARGET} constituent signals loaded — "
        "check 'Refresh all 50 constituents' and Run analysis."
    )


def _constituent_signal_from_row(row: object) -> ConstituentSignal | None:
    """Parse one hub JSON row into a ConstituentSignal, or None when invalid."""
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        return None
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
    return ConstituentSignal(
        symbol=symbol,
        weight=weight_f,
        sector=str(row.get("sector") or ""),
        events=list(row.get("events") or []),
        factors=list(row.get("factors") or []),
        sentiment_score=sentiment_f,
        momentum_7d_pct=momentum_f,
        contribution_to_index_pct=row.get("contribution_to_index_pct"),
    )


def signals_from_cached_doc(cached_doc: IndexResearchDoc | None) -> list[ConstituentSignal]:
    """Build constituent signals from the last saved index research snapshot."""
    if cached_doc is None:
        return []
    signals: list[ConstituentSignal] = []
    for row in cached_doc.constituent_signals or []:
        signal = _constituent_signal_from_row(row)
        if signal is not None:
            signals.append(signal)
    return signals


def recover_constituent_signals_from_history(
    ticker: str = "NIFTY",
    *,
    min_count: int = MIN_CONSTITUENT_SIGNALS,
) -> list[ConstituentSignal] | None:
    """Load the newest hub history snapshot with at least *min_count* constituents."""
    sym = ticker.strip().upper()
    history_dir = get_hub_dir() / sym / "index_research" / "history"
    if not history_dir.is_dir():
        return None

    best_signals: list[ConstituentSignal] | None = None
    best_count = 0
    for path in sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("constituent_signals") or []
        if not isinstance(rows, list):
            continue
        recovered: list[ConstituentSignal] = []
        for row in rows:
            signal = _constituent_signal_from_row(row)
            if signal is not None:
                recovered.append(signal)
        count = len(recovered)
        if count < min_count or count <= best_count:
            continue
        best_signals = recovered
        best_count = count
        if best_count >= NIFTY50_CONSTITUENT_TARGET:
            break

    if best_signals:
        logger.info(
            "Recovered %s constituent signals from hub history for %s",
            best_count,
            sym,
        )
    return best_signals


def resolve_cached_constituent_signals(
    cached_doc: IndexResearchDoc | None,
    *,
    ticker: str = "NIFTY",
) -> tuple[list[ConstituentSignal], list[str]]:
    """Load cached signals; recover from history when the live snapshot is partial."""
    warnings: list[str] = []
    signals = signals_from_cached_doc(cached_doc)
    if not is_partial_constituent_cache(len(signals)):
        return signals, warnings

    recovered = recover_constituent_signals_from_history(ticker)
    if recovered and len(recovered) > len(signals):
        logger.warning(
            "Partial constituent cache for %s (%s stocks) — using history recovery (%s stocks)",
            ticker,
            len(signals),
            len(recovered),
        )
        return recovered, warnings

    warnings.append(partial_constituent_warning(len(signals)))
    return signals, warnings


def load_cached_constituent_signals(ticker: str = "NIFTY") -> list[ConstituentSignal]:
    """Load constituent signals from hub index research for *ticker*."""
    signals, _ = resolve_cached_constituent_signals(load_index_research_json(ticker.strip().upper()), ticker=ticker)
    return signals
