"""Nifty 50 trailing P/E — vendor chain before SearXNG finance enrichment."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CONSTITUENT_COVERAGE = 10


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


def _fetch_weighted_constituent_pe() -> dict[str, Any] | None:
    """Weighted average of constituent trailing P/E (Nifty 50 weights)."""
    from trade_integrations.dataflows import source_availability

    try:
        import yfinance as yf
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
    for row in constituents:
        sym = row.symbol.strip().upper()
        weight = float(weights.get(sym) or row.weight or 0.0)
        if weight <= 0:
            continue
        info = yf.Ticker(f"{sym}.NS").info or {}
        pe = info.get("trailingPE") or info.get("forwardPE")
        if pe is None:
            continue
        try:
            pe_val = float(pe)
        except (TypeError, ValueError):
            continue
        if pe_val <= 0 or pe_val > 500:
            continue
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


def resolve_nifty_trailing_pe() -> dict[str, Any] | None:
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
