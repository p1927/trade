"""Weighted Nifty 50 constituent price momentum rollup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from trade_integrations.dataflows.index_research.models import ConstituentSignal

logger = logging.getLogger(__name__)

_YFINANCE_SUFFIX = ".NS"
_LOOKBACK_DAYS = 10


def _yfinance_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return f"{sym}{_YFINANCE_SUFFIX}"


def batch_fetch_returns_7d(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch 7d returns for many NSE symbols (one yfinance download)."""
    import yfinance as yf

    if not symbols:
        return {}

    yf_symbols = [_yfinance_symbol(s) for s in symbols]
    unique = sorted(set(yf_symbols))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(_LOOKBACK_DAYS, 12))

    try:
        panel = yf.download(
            unique,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.debug("batch momentum download failed: %s", exc)
        return {}

    if panel is None or panel.empty:
        return {}

    out: dict[str, float] = {}
    close_col = "Close" if "Close" in panel.columns else "close"
    if isinstance(panel.columns, pd.MultiIndex):
        for yf_sym in unique:
            try:
                closes = panel[(close_col, yf_sym)].astype(float).dropna()
            except (KeyError, TypeError):
                continue
            if len(closes) < 2:
                continue
            first = float(closes.iloc[0])
            last = float(closes.iloc[-1])
            if first > 0:
                base = yf_sym.replace(".NS", "").replace(".BO", "")
                out[base] = (last - first) / first * 100.0
    else:
        closes = panel[close_col].astype(float).dropna()
        if len(closes) >= 2 and unique:
            first = float(closes.iloc[0])
            last = float(closes.iloc[-1])
            if first > 0:
                base = unique[0].replace(".NS", "").replace(".BO", "")
                out[base] = (last - first) / first * 100.0
    return out


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
    force_refresh: bool = True,
) -> list[ConstituentSignal]:
    """Attach ``momentum_7d_pct`` to each signal (batch fetch or injected map).

    When ``force_refresh`` is false, existing ``momentum_7d_pct`` values are kept
    and only missing symbols are fetched.
    """
    from dataclasses import replace

    need_fetch = force_refresh or any(s.momentum_7d_pct is None for s in signals)
    if returns_by_symbol is None and signals and need_fetch:
        fetch_symbols = [
            s.symbol
            for s in signals
            if force_refresh or s.momentum_7d_pct is None
        ]
        if fetch_symbols:
            fetched = batch_fetch_returns_7d(fetch_symbols)
            returns_by_symbol = dict(fetched) if returns_by_symbol is None else {**returns_by_symbol, **fetched}

    updated: list[ConstituentSignal] = []
    for signal in signals:
        if not force_refresh and signal.momentum_7d_pct is not None:
            updated.append(signal)
            continue

        momentum: float | None = None
        if returns_by_symbol is not None:
            raw = returns_by_symbol.get(signal.symbol.upper())
            if raw is None:
                raw = returns_by_symbol.get(_yfinance_symbol(signal.symbol).replace(".NS", ""))
            if raw is not None:
                momentum = float(raw)
        if momentum is None:
            try:
                momentum = fetch_symbol_return_7d(signal.symbol)
            except Exception as exc:
                logger.debug("momentum fetch failed for %s: %s", signal.symbol, exc)
        updated.append(replace(signal, momentum_7d_pct=momentum))
    return updated


def resolve_constituent_momentum_rollup(
    signals: list[ConstituentSignal],
    *,
    fallback_factors: dict[str, float] | None = None,
) -> tuple[float | None, str]:
    """Return weighted rollup with index 7d return fallback when coverage is thin."""
    rollup = rollup_constituent_momentum(signals)
    if rollup is not None:
        return rollup, "constituent_momentum"

    if fallback_factors:
        index_ret = fallback_factors.get("nifty_return_7d")
        if index_ret is not None:
            try:
                return float(index_ret), "nifty_return_7d_fallback"
            except (TypeError, ValueError):
                pass
    return None, "missing"


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
