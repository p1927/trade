"""Screener.in peer comparison — free scrape fallback for Tapetide peers."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MCAP_RE = re.compile(r"([\d,.]+)")


def _symbol_from_peer_url(url: str) -> str:
    parts = [part for part in str(url or "").strip("/").split("/") if part]
    if "company" in parts:
        idx = parts.index("company")
        if idx + 1 < len(parts):
            return parts[idx + 1].upper()
    return ""


def _parse_market_cap(values: dict[str, Any]) -> float | None:
    raw = values.get("Mar Cap Rs.Cr.") or values.get("Market Cap")
    if raw is None:
        return None
    match = _MCAP_RE.search(str(raw))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "")) * 1e7
    except ValueError:
        return None


def fetch_screener_peers(symbol: str, *, max_peers: int) -> dict[str, Any] | None:
    """Return industry peers from screener.in peer-comparison."""
    try:
        from screener_cli.parsers.peers import parse as parse_peers
        from screener_cli.scraper import fetch_page_with_fallback
    except ImportError:
        logger.info("screenercli not installed; skip screener peers for %s", symbol)
        return None

    symbol_upper = symbol.strip().upper()
    try:
        page = fetch_page_with_fallback(symbol_upper, view="consolidated")
        soup = page[0] if isinstance(page, tuple) else page
        comparison = parse_peers(soup)
    except Exception as exc:
        logger.info("screener.in peers failed for %s: %s", symbol_upper, exc)
        return None

    if comparison is None or not comparison.peers:
        return None

    peers: list[dict[str, Any]] = []
    for row in comparison.peers:
        peer_symbol = _symbol_from_peer_url(getattr(row, "url", "") or "")
        if not peer_symbol or peer_symbol == symbol_upper:
            continue
        values = getattr(row, "values", {}) or {}
        peers.append(
            {
                "symbol": peer_symbol,
                "name": getattr(row, "name", peer_symbol) or peer_symbol,
                "sector": comparison.industry or comparison.sector or "",
                "market_cap": _parse_market_cap(values),
                "source": "screener.in:peer_comparison",
            }
        )
        if len(peers) >= max_peers:
            break

    if not peers:
        return None

    return {
        "peers": peers,
        "primary_source": "screener.in",
        "sector_context": {
            "sector": comparison.sector or "",
            "industry": comparison.industry or "",
            "source": "screener.in",
        },
    }
