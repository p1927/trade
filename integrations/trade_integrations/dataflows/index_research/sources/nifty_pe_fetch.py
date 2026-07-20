"""Nifty 50 trailing P/E — vendor chain before SearXNG finance enrichment."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CONSTITUENT_COVERAGE = 10
_WEIGHTED_PE_WORKERS = 8


def _resolve_trading_day(trading_day: str | None) -> str:
    if trading_day:
        return trading_day[:10]
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    return india_trading_date_iso()[:10]


def _fetch_yfinance_index_pe() -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker("^NSEI").info or {}
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe is None:
        return None
    try:
        value = float(pe)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    from datetime import datetime, timezone

    return {
        "value": value,
        "source": "yfinance",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "metadata": {"symbol": "^NSEI", "field": "trailingPE"},
    }


def _constituent_pe_row(symbol: str, weight: float) -> tuple[float, float] | None:
    import yfinance as yf

    sym = symbol.strip().upper()
    if weight <= 0:
        return None
    info = yf.Ticker(f"{sym}.NS").info or {}
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe is None:
        return None
    try:
        pe_val = float(pe)
    except (TypeError, ValueError):
        return None
    if pe_val <= 0 or pe_val > 500:
        return None
    return pe_val, weight


def _fetch_weighted_constituent_pe() -> dict[str, Any] | None:
    """Weighted average of constituent trailing P/E (Nifty 50 weights)."""
    from trade_integrations.dataflows import source_availability

    try:
        import yfinance as yf  # noqa: F401 — availability check
    except ImportError:
        return None

    if not source_availability.should_attempt("yfinance", "history"):
        return None

    from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
    from trade_integrations.dataflows.index_research.sources.weights_nse import fetch_nifty50_weights

    weights = fetch_nifty50_weights() or {}
    constituents = load_nifty50_constituents()
    if not constituents:
        return None

    pe_weighted = 0.0
    weight_sum = 0.0
    used = 0

    with ThreadPoolExecutor(max_workers=_WEIGHTED_PE_WORKERS) as pool:
        futures = {
            pool.submit(
                _constituent_pe_row,
                row.symbol,
                float(weights.get(row.symbol.strip().upper()) or row.weight or 0.0),
            ): row.symbol
            for row in constituents
        }
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                logger.debug("constituent pe fetch failed: %s", exc)
                continue
            if result is None:
                continue
            pe_val, weight = result
            pe_weighted += pe_val * weight
            weight_sum += weight
            used += 1

    if used < _MIN_CONSTITUENT_COVERAGE or weight_sum <= 0:
        return None

    return {
        "value": round(pe_weighted / weight_sum, 4),
        "source": "nifty50_weighted_constituents",
        "metadata": {
            "constituents_used": used,
            "weight_coverage": round(weight_sum, 4),
        },
    }


def _resolve_nifty_trailing_pe_live() -> dict[str, Any] | None:
    """Primary vendors first, then SearXNG finance portals — no manual env required."""
    for fetcher in (_fetch_yfinance_index_pe, _fetch_weighted_constituent_pe):
        try:
            payload = fetcher()
        except Exception as exc:
            logger.debug("nifty_pe fetcher %s failed: %s", fetcher.__name__, exc)
            payload = None
        if payload and payload.get("value") is not None:
            return payload

    try:
        from trade_integrations.dataflows.searxng_finance import fetch_nifty_trailing_pe_via_searxng

        enriched = fetch_nifty_trailing_pe_via_searxng()
    except ImportError:
        enriched = None
    except Exception as exc:
        logger.debug("searxng nifty_pe failed: %s", exc)
        enriched = None

    if enriched and enriched.get("value") is not None:
        return {
            "value": float(enriched["value"]),
            "source": enriched.get("source") or "searxng_finance",
            "metadata": {
                k: enriched[k]
                for k in ("query", "url", "engines")
                if enriched.get(k) is not None
            },
        }
    return None


def resolve_nifty_trailing_pe(
    *,
    trading_day: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Resolve trailing P/E with trading-day cache."""
    from trade_integrations.dataflows.index_research.day_cache import get_or_fetch

    day = _resolve_trading_day(trading_day)
    payload, _cached = get_or_fetch(
        namespace="nifty_pe",
        trading_day=day,
        fetch_fn=_resolve_nifty_trailing_pe_live,
        force=force,
    )
    return payload if isinstance(payload, dict) else None
