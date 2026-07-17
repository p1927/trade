"""India market identity — multi-source merge with guaranteed fallbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from tradingagents.dataflows.errors import VendorNotConfiguredError

from ..market import NormalizedTicker
from ..models import StageResult
from ..source_registry import optional_source_names
from .resilience import (
    SourceAttempt,
    merge_identity_fields,
    remediation_for,
    resolve_bse_scrip_code,
    run_sources,
    stage_errors,
    stage_status_from_attempts,
)
from .tapetide_in import fetch_tapetide_identity

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_openalgo(normalized: NormalizedTicker) -> dict[str, Any] | None:
    from trade_integrations.dataflows.openalgo import _openalgo_post, resolve_openalgo_symbol

    symbol, exchange = resolve_openalgo_symbol(normalized.input_ticker)
    data = _openalgo_post("quotes", {"symbol": symbol, "exchange": exchange})
    quote = data.get("data") or data
    if not quote:
        return None
    return {
        "name": quote.get("name") or quote.get("symbol") or normalized.base_symbol,
        "sector": quote.get("sector") or "",
        "industry": quote.get("industry") or "",
        "exchange": exchange,
        "last_price": quote.get("ltp") or quote.get("last_price") or quote.get("close"),
        "currency": "INR",
        "source": "openalgo",
    }


def _fetch_yfinance(normalized: NormalizedTicker) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    if not info:
        return None
    return {
        "name": info.get("longName") or info.get("shortName") or normalized.base_symbol,
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "exchange": info.get("fullExchangeName") or info.get("exchange") or "NSE",
        "last_price": info.get("regularMarketPrice") or info.get("currentPrice"),
        "market_cap": info.get("marketCap"),
        "currency": info.get("currency") or "INR",
        "source": "yfinance",
    }


def _fetch_dalal_bse(normalized: NormalizedTicker) -> dict[str, Any] | None:
    import dalal  # type: ignore[import-untyped]

    scrip = resolve_bse_scrip_code(normalized.base_symbol)
    if not scrip:
        return None
    quote = dalal.quote(scrip, exchange="BSE")
    meta = dalal.meta(scrip) or {}
    if not quote and not meta:
        return None
    header = (quote or {}).get("Header") or {}
    cmpname = (quote or {}).get("Cmpname") or {}
    ltp = header.get("LTP")
    return {
        "name": cmpname.get("FullN") or meta.get("SecurityId") or normalized.base_symbol,
        "sector": meta.get("Sector") or "",
        "industry": meta.get("IndustryNew") or meta.get("Industry") or "",
        "exchange": "BSE",
        "last_price": ltp,
        "pe_ratio": meta.get("PE") or meta.get("ConPE"),
        "market_cap": None,
        "bse_scrip_code": scrip,
        "currency": "INR",
        "source": "dalal_bse",
    }


def _fetch_nselib_pe(normalized: NormalizedTicker) -> dict[str, Any] | None:
    from nselib import capital_market

    for offset in range(0, 6):
        trade_day = (datetime.now() - timedelta(days=offset)).strftime("%d-%m-%Y")
        try:
            frame = capital_market.pe_ratio(trade_date=trade_day)
        except Exception:
            continue
        if frame is None or frame.empty or "symbol" not in frame.columns:
            continue
        row = frame[frame["symbol"].astype(str).str.upper() == normalized.base_symbol]
        if row.empty:
            continue
        record = row.iloc[0].to_dict()
        return {
            "pe_ratio": record.get("pe") or record.get("PE"),
            "sector": record.get("industry") or record.get("Industry") or "",
            "industry": record.get("industry") or "",
            "exchange": "NSE",
            "source": "nselib",
            "trade_date": trade_day,
        }
    return None


def fetch_identity_in(normalized: NormalizedTicker) -> StageResult:
    """Resolve company identity using every available India source."""
    base = {
        "base_symbol": normalized.base_symbol,
        "yfinance_symbol": normalized.yfinance_symbol,
        "openalgo_symbol": normalized.openalgo_symbol,
        "openalgo_exchange": normalized.openalgo_exchange,
    }

    fetchers: list[tuple[str, Any]] = [
        ("yfinance", lambda: _fetch_yfinance(normalized)),
    ]

    if resolve_bse_scrip_code(normalized.base_symbol):
        fetchers.append(("dalal_bse", lambda: _fetch_dalal_bse(normalized)))

    try:
        from trade_integrations.dataflows.openalgo import _openalgo_settings

        _openalgo_settings()
        fetchers.insert(0, ("openalgo", lambda: _fetch_openalgo(normalized)))
    except (VendorNotConfiguredError, ImportError):
        pass

    try:
        import nselib  # noqa: F401

        fetchers.append(("nselib", lambda: _fetch_nselib_pe(normalized)))
    except ImportError:
        pass

    from trade_integrations.clients.tapetide import is_configured as tapetide_configured

    if tapetide_configured():
        fetchers.append(("tapetide", lambda: fetch_tapetide_identity(normalized.base_symbol)))

    optional = optional_source_names("identity")
    attempts = run_sources(fetchers, optional=optional)
    merged = merge_identity_fields(attempts)
    merged.update(base)

    if not resolve_bse_scrip_code(normalized.base_symbol):
        attempts.append(
            SourceAttempt(
                name="dalal_bse",
                status="skipped",
                error="bse_code_missing",
                remediation=remediation_for("bse_code_missing"),
            )
        )

    ok_sources = [a.name for a in attempts if a.status == "ok"]
    vendor = "+".join(ok_sources) if ok_sources else "identity_in"
    status = stage_status_from_attempts(
        attempts, has_output=bool(merged.get("name")), stage="identity"
    )

    return StageResult(
        stage="identity",
        status=status if merged.get("name") else "error",
        vendor=vendor,
        fetched_at=_stage_now(),
        data={
            **merged,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=stage_errors(attempts, stage="identity"),
    )
