"""Option chain snapshot via OpenAlgo with nselib fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.openalgo import fetch_option_chain
from trade_integrations.openalgo.market_data import fetch_option_expiry_dates
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError

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
        vendor = str(chain.get("source") or "openalgo")
    except (NoMarketDataError, VendorNotConfiguredError) as exc:
        errors.append(str(exc))
        return StageResult(
            stage="chain",
            status="error",
            vendor=vendor,
            fetched_at=now,
            errors=errors,
        )
    except Exception as exc:
        errors.append(str(exc))
        return StageResult(
            stage="chain",
            status="error",
            vendor=vendor,
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
