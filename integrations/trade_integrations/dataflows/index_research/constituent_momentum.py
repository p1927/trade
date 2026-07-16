"""Weighted Nifty 50 constituent price momentum rollup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from trade_integrations.dataflows.index_research.models import ConstituentSignal

logger = logging.getLogger(__name__)

_YFINANCE_SUFFIX = ".NS"
_LOOKBACK_DAYS = 10


def _yfinance_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return f"{sym}{_YFINANCE_SUFFIX}"


def fetch_symbol_return_7d(symbol: str) -> float | None:
    """Fetch 7-day close-to-close return (%) for one NSE symbol via yfinance."""
    import yfinance as yf

    yf_symbol = _yfinance_symbol(symbol)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    hist = yf.Ticker(yf_symbol).history(start=start, end=end, auto_adjust=True)
    if hist is None or hist.empty or len(hist) < 2:
        return None

    close_col = "Close" if "Close" in hist.columns else "close"
    closes = hist[close_col].astype(float)
    if len(closes) < 2:
        return None

    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def attach_constituent_momentum(
    signals: list[ConstituentSignal],
    *,
    returns_by_symbol: dict[str, float] | None = None,
) -> list[ConstituentSignal]:
    """Attach ``momentum_7d_pct`` to each signal (fetch or use injected map)."""
    from dataclasses import replace

    updated: list[ConstituentSignal] = []
    for signal in signals:
        momentum: float | None = None
        if returns_by_symbol is not None:
            raw = returns_by_symbol.get(signal.symbol.upper())
            if raw is not None:
                momentum = float(raw)
        else:
            try:
                momentum = fetch_symbol_return_7d(signal.symbol)
            except Exception as exc:
                logger.debug("momentum fetch failed for %s: %s", signal.symbol, exc)
        updated.append(replace(signal, momentum_7d_pct=momentum))
    return updated


def rollup_constituent_momentum(signals: list[ConstituentSignal]) -> float | None:
    """Weight-averaged 7d return across constituents with momentum data."""
    weighted = 0.0
    total_weight = 0.0
    for signal in signals:
        if signal.momentum_7d_pct is None or signal.weight <= 0:
            continue
        weighted += signal.weight * float(signal.momentum_7d_pct)
        total_weight += signal.weight
    if total_weight <= 0:
        return None
    return weighted / total_weight


def momentum_coverage_stats(signals: list[ConstituentSignal]) -> dict[str, float | int]:
    """How many constituents have usable 7d momentum for bottom-up blend."""
    total = len(signals)
    with_momentum = sum(1 for signal in signals if signal.momentum_7d_pct is not None)
    pct = round(with_momentum / total * 100.0, 1) if total else 0.0
    return {"with_momentum": with_momentum, "total": total, "coverage_pct": pct}
