"""Option chain snapshot via OpenAlgo with nselib fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.openalgo import (
    VendorNotConfiguredError,
    fetch_option_chain,
    fetch_option_expiry_dates,
)
from tradingagents.dataflows.errors import NoMarketDataError

from ..market import OptionsInstrument
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_expiry(expiry: str) -> str:
    return expiry.strip().upper().replace("-", "")


def _fetch_nselib_chain(instrument: OptionsInstrument, expiry_date: str | None) -> dict[str, Any] | None:
    try:
        from nselib import derivatives
    except ImportError:
        return None
    symbol = instrument.underlying_symbol
    try:
        if instrument.instrument_type.value == "index":
            frame, expiries, spot = derivatives.nse_live_option_chain(symbol=symbol, oi_mode="compact")
        else:
            frame, expiries, spot = derivatives.nse_live_option_chain(
                symbol=symbol,
                oi_mode="compact",
                instrument="equities",
            )
    except Exception as exc:
        logger.warning("nselib option chain failed for %s: %s", symbol, exc)
        return None
    rows = frame.to_dict("records") if hasattr(frame, "to_dict") else []
    return {
        "underlying": symbol,
        "underlying_ltp": spot,
        "expiry_date": expiry_date or (expiries[0] if expiries else ""),
        "expiries": list(expiries or []),
        "chain_rows": rows[:200],
        "source": "nselib",
    }


def fetch_chain_stage(
    instrument: OptionsInstrument,
    *,
    expiry_date: str | None = None,
    strike_count: int = 15,
) -> StageResult:
    """Load live option chain; prefer OpenAlgo, fall back to nselib."""
    now = _stage_now()
    errors: list[str] = []
    data: dict[str, Any] = {}
    vendor = "openalgo"

    try:
        expiries = fetch_option_expiry_dates(
            instrument.underlying_symbol,
            instrument.options_exchange,
        )
        chosen_expiry = _normalize_expiry(expiry_date) if expiry_date else (
            _normalize_expiry(expiries[0]) if expiries else ""
        )
        if not chosen_expiry and expiries:
            chosen_expiry = _normalize_expiry(expiries[0])
        chain = fetch_option_chain(
            instrument.underlying_symbol,
            instrument.underlying_exchange,
            expiry_date=chosen_expiry or None,
            strike_count=strike_count,
        )
        chain["expiries"] = expiries
        chain["options_exchange"] = instrument.options_exchange
        data = chain
    except (NoMarketDataError, VendorNotConfiguredError) as exc:
        errors.append(str(exc))
        fallback = _fetch_nselib_chain(instrument, expiry_date)
        if fallback:
            vendor = "nselib"
            data = fallback
        else:
            return StageResult(
                stage="chain",
                status="error",
                vendor="openalgo",
                fetched_at=now,
                errors=errors,
            )
    except Exception as exc:
        errors.append(str(exc))
        fallback = _fetch_nselib_chain(instrument, expiry_date)
        if fallback:
            vendor = "nselib"
            data = fallback
        else:
            return StageResult(
                stage="chain",
                status="error",
                vendor="openalgo",
                fetched_at=now,
                errors=errors,
            )

    status = "ok" if data.get("chain") or data.get("chain_rows") else "partial"
    return StageResult(
        stage="chain",
        status=status,
        vendor=vendor,
        fetched_at=now,
        data=data,
        errors=errors,
    )
