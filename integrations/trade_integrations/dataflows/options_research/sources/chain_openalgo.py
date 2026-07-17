"""Option chain snapshot via OpenAlgo with nselib fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.openalgo import (
    VendorNotConfiguredError,
    fetch_option_chain,
    fetch_option_expiry_dates,
    normalize_openalgo_expiry,
)
from trade_integrations.openalgo.market_data import _fetch_nselib_chain
from tradingagents.dataflows.errors import NoMarketDataError

from ..market import OptionsInstrument
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def _apply_fallback(reason: str) -> bool:
        nonlocal vendor, data
        errors.append(reason)
        fallback = _fetch_nselib_chain(
            instrument.underlying_symbol,
            expiry_date,
            is_index=instrument.instrument_type.value == "index",
        )
        if fallback:
            vendor = "nselib"
            data = fallback
            return True
        return False

    try:
        expiries = fetch_option_expiry_dates(
            instrument.underlying_symbol,
            instrument.options_exchange,
        )
        chosen_expiry = (
            normalize_openalgo_expiry(expiry_date)
            if expiry_date
            else (normalize_openalgo_expiry(expiries[0]) if expiries else "")
        )
        chain = fetch_option_chain(
            instrument.underlying_symbol,
            instrument.underlying_exchange,
            expiry_date=chosen_expiry or None,
            strike_count=strike_count,
        )
        chain["expiries"] = expiries
        chain["options_exchange"] = instrument.options_exchange
        data = chain
        if not chain.get("chain"):
            if not _apply_fallback("OpenAlgo returned empty chain"):
                return StageResult(
                    stage="chain",
                    status="error",
                    vendor="openalgo",
                    fetched_at=now,
                    errors=errors,
                )
    except (NoMarketDataError, VendorNotConfiguredError) as exc:
        if not _apply_fallback(str(exc)):
            return StageResult(
                stage="chain",
                status="error",
                vendor="openalgo",
                fetched_at=now,
                errors=errors,
            )
    except Exception as exc:
        if not _apply_fallback(str(exc)):
            return StageResult(
                stage="chain",
                status="error",
                vendor="openalgo",
                fetched_at=now,
                errors=errors,
            )

    status = "ok" if data.get("chain") else "partial"
    return StageResult(
        stage="chain",
        status=status,
        vendor=vendor,
        fetched_at=now,
        data=data,
        errors=errors,
    )
