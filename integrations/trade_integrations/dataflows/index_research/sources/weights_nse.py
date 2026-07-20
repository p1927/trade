"""Nifty 50 index weight sources — NSE composition scrape and yfinance fallback."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_NIFTY50_HEATMAP_URL = (
    "https://iislliveblob.niftyindices.com/jsonfiles/HeatmapDetail/FinalHeatmapNIFTY%2050.json"
)


def _normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in raw.values() if v > 0)
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in raw.items() if value > 0}


def _weights_from_heatmap(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    raw: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        mcap = row.get("Indexmcap_today")
        if not symbol or mcap is None:
            continue
        try:
            value = float(mcap)
        except (TypeError, ValueError):
            continue
        if value > 0:
            raw[symbol] = value
    if not raw:
        return None
    return _normalize_weights(raw)


def _fetch_heatmap_weights() -> dict[str, float] | None:
    try:
        import requests
    except ImportError:
        return None

    try:
        response = requests.get(
            _NIFTY50_HEATMAP_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.info("niftyindices heatmap weights failed: %s", exc)
        return None

    if not isinstance(payload, list):
        return None
    return _weights_from_heatmap(payload)


def fetch_nifty50_weights() -> dict[str, float] | None:
    """Fetch Nifty 50 weights from NSE index composition heatmap (best effort)."""
    return _fetch_heatmap_weights()


def fetch_yfinance_mcap_weights(symbols: list[str]) -> dict[str, float]:
    """Derive normalized weights from yfinance market-cap for `.NS` tickers."""
    from trade_integrations.dataflows import source_availability

    try:
        import yfinance as yf
    except ImportError:
        logger.info("yfinance not installed; cannot compute mcap weights")
        return {}

    if not source_availability.should_attempt("yfinance", "history"):
        return {}

    raw: dict[str, float] = {}
    for symbol in symbols:
        sym = symbol.strip().upper()
        if not sym:
            continue
        try:
            info = yf.Ticker(f"{sym}.NS").info or {}
            mcap = info.get("marketCap")
            if mcap is None:
                continue
            value = float(mcap)
            if value > 0:
                raw[sym] = value
        except Exception as exc:
            logger.debug("yfinance mcap failed for %s: %s", sym, exc)
            continue

    return _normalize_weights(raw)
