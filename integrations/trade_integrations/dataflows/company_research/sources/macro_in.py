"""India market macro context — India VIX and index mood."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import StageResult
from .resilience import (
    SourceAttempt,
    classify_error,
    remediation_for,
    run_sources,
    stage_status_from_attempts,
)

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_vix() -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker("^INDIAVIX").info or {}
    price = info.get("regularMarketPrice") or info.get("previousClose")
    if price is None:
        return None
    return {
        "india_vix": price,
        "source": "yfinance",
        "symbol": "^INDIAVIX",
    }


def _fetch_nselib_vix() -> dict[str, Any] | None:
    from nselib import capital_market

    end = datetime.now().date()
    start = end - timedelta(days=7)
    frame = capital_market.india_vix_data(
        from_date=start.strftime("%d-%m-%Y"),
        to_date=end.strftime("%d-%m-%Y"),
    )
    if frame is None or getattr(frame, "empty", True):
        return None
    latest = frame.iloc[-1].to_dict()
    vix_val = latest.get("close") or latest.get("CLOSE") or latest.get("vix")
    if vix_val is None:
        return None
    return {"india_vix": vix_val, "source": "nselib", "as_of": str(latest.get("date") or "")}


def _fetch_nifty_context() -> dict[str, Any] | None:
    import yfinance as yf

    nifty = yf.Ticker("^NSEI").info or {}
    if not nifty:
        return None
    return {
        "nifty_level": nifty.get("regularMarketPrice") or nifty.get("previousClose"),
        "nifty_change_pct": nifty.get("regularMarketChangePercent"),
        "source": "yfinance",
        "symbol": "^NSEI",
    }


def _fetch_openalgo_vix() -> dict[str, Any] | None:
    from trade_integrations.dataflows.openalgo import fetch_openalgo_quote

    quote = fetch_openalgo_quote("INDIAVIX")
    ltp = quote.get("ltp") if quote else None
    if ltp is None:
        return None
    return {"india_vix": ltp, "source": "openalgo", "symbol": "INDIAVIX"}


def _fetch_openalgo_nifty() -> dict[str, Any] | None:
    from trade_integrations.dataflows.openalgo import fetch_openalgo_quote

    quote = fetch_openalgo_quote("NIFTY")
    ltp = quote.get("ltp") if quote else None
    if ltp is None:
        return None
    return {
        "nifty_level": ltp,
        "nifty_change_pct": quote.get("change_pct"),
        "source": "openalgo",
        "symbol": "NIFTY",
    }


def fetch_macro_in() -> StageResult:
    """Market-wide India macro snapshot (VIX + Nifty)."""
    fetchers: list[tuple[str, Any]] = [
        ("yfinance_vix", _fetch_yfinance_vix),
        ("yfinance_nifty", _fetch_nifty_context),
    ]
    try:
        from trade_integrations.openalgo.market_data import openalgo_configured

        if openalgo_configured():
            fetchers.insert(0, ("openalgo_vix", _fetch_openalgo_vix))
            fetchers.insert(1, ("openalgo_nifty", _fetch_openalgo_nifty))
    except ImportError:
        pass
    try:
        import nselib  # noqa: F401

        fetchers.insert(0, ("nselib_vix", _fetch_nselib_vix))
    except ImportError:
        pass

    attempts = run_sources(fetchers)
    macro: dict[str, Any] = {"sources": {}}
    for attempt in attempts:
        if attempt.status == "ok" and attempt.data:
            macro["sources"][attempt.name] = attempt.data
            macro.update({k: v for k, v in attempt.data.items() if k != "source"})
    if macro.get("sources"):
        macro["primary_source"] = next(iter(macro["sources"]))

    has_output = bool(macro.get("india_vix") or macro.get("nifty_level"))
    status = stage_status_from_attempts(attempts, has_output=has_output)

    return StageResult(
        stage="macro",
        status=status,
        vendor=macro.get("primary_source") or "macro_in",
        fetched_at=_stage_now(),
        data={**macro, "source_attempts": [a.to_dict() for a in attempts]},
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
