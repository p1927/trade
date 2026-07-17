"""Tapetide-backed identity and calendar enrichment."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.clients.tapetide import (
    TapetideNotConfiguredError,
    TapetideRateLimitError,
    get_company_profile,
    get_stock_events,
    is_configured,
    is_rate_limit_message,
)

logger = logging.getLogger(__name__)


def fetch_tapetide_identity(symbol: str) -> dict[str, Any] | None:
    if not is_configured():
        return None
    try:
        profile = get_company_profile(symbol, include_peers=False)
    except TapetideNotConfiguredError:
        raise
    except TapetideRateLimitError as exc:
        logger.warning("Tapetide identity rate-limited for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.warning("Tapetide identity failed for %s: %s", symbol, exc)
        return None

    if profile.get("raw_text") and is_rate_limit_message(str(profile["raw_text"])):
        logger.warning("Tapetide identity rate-limited for %s", symbol)
        return None

    quote = profile.get("quote") or profile.get("current_quote") or {}
    fundamentals = profile.get("fundamentals") or profile.get("key_ratios") or {}
    if not profile and not quote:
        return None

    return {
        "name": profile.get("name") or profile.get("company_name") or symbol,
        "sector": profile.get("sector") or "",
        "industry": profile.get("industry") or "",
        "exchange": profile.get("exchange") or "NSE",
        "last_price": quote.get("ltp") or quote.get("last_price") or quote.get("price"),
        "market_cap": fundamentals.get("market_cap") or quote.get("market_cap"),
        "pe_ratio": fundamentals.get("pe") or fundamentals.get("pe_ratio"),
        "currency": "INR",
        "source": "tapetide",
    }


def fetch_tapetide_calendar_events(symbol: str) -> list[dict[str, Any]]:
    if not is_configured():
        return []
    try:
        payload = get_stock_events(symbol)
    except TapetideNotConfiguredError:
        raise
    except TapetideRateLimitError as exc:
        logger.warning("Tapetide events rate-limited for %s: %s", symbol, exc)
        return []
    except Exception as exc:
        logger.warning("Tapetide events failed for %s: %s", symbol, exc)
        return []

    if payload.get("raw_text") and is_rate_limit_message(str(payload["raw_text"])):
        logger.warning("Tapetide events rate-limited for %s", symbol)
        return []

    events: list[dict[str, Any]] = []
    symbol_upper = symbol.strip().upper()

    for bucket, default_type in (
        ("corporate_actions", "corporate_action"),
        ("upcoming_events", "event"),
        ("events", "event"),
        ("news", "news"),
    ):
        rows = payload.get(bucket)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or row.get("description") or row.get("headline") or ""
            if not title:
                continue
            events.append(
                {
                    "symbol": symbol_upper,
                    "company": row.get("company") or row.get("company_name") or "",
                    "type": (row.get("type") or row.get("event_type") or default_type).lower().replace(" ", "_"),
                    "purpose": row.get("purpose") or row.get("category") or bucket,
                    "description": title,
                    "date": str(row.get("date") or row.get("event_date") or row.get("ex_date") or ""),
                    "source": "tapetide:get_stock_events",
                }
            )

    if not events and payload.get("raw_text"):
        raw_text = str(payload.get("raw_text"))
        if any(
            marker in raw_text.lower()
            for marker in (
                "mcp error",
                "validation error",
                "invalid input",
                "unauthorized",
                "rate limit",
                "free tier limit",
                "quota exceeded",
            )
        ):
            return []
    return events
