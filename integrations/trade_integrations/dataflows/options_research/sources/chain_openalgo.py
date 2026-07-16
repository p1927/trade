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
from tradingagents.dataflows.errors import NoMarketDataError

from ..market import OptionsInstrument
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _float_val(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_val(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _nselib_rows_to_chain(rows: list[dict[str, Any]], *, lot_size: int = 1) -> list[dict[str, Any]]:
    """Convert nselib compact option chain rows to OpenAlgo-style chain."""
    chain: list[dict[str, Any]] = []
    for row in rows:
        strike = _float_val(row.get("Strike_Price") or row.get("strike") or row.get("STRIKE"))
        if not strike:
            continue
        ce_ltp = _float_val(row.get("CALLS_LTP") or row.get("ce_ltp"))
        pe_ltp = _float_val(row.get("PUTS_LTP") or row.get("pe_ltp"))
        entry: dict[str, Any] = {"strike": strike}
        if ce_ltp:
            entry["ce"] = {
                "ltp": ce_ltp,
                "oi": _int_val(row.get("CALLS_OI") or row.get("ce_oi")),
                "iv": _float_val(row.get("CALLS_IV") or row.get("ce_iv")),
                "lotsize": lot_size,
                "symbol": str(row.get("CALLS_Symbol") or row.get("ce_symbol") or ""),
            }
        if pe_ltp:
            entry["pe"] = {
                "ltp": pe_ltp,
                "oi": _int_val(row.get("PUTS_OI") or row.get("pe_oi")),
                "iv": _float_val(row.get("PUTS_IV") or row.get("pe_iv")),
                "lotsize": lot_size,
                "symbol": str(row.get("PUTS_Symbol") or row.get("pe_symbol") or ""),
            }
        if entry.get("ce") or entry.get("pe"):
            chain.append(entry)
    return chain


def _fetch_nselib_chain(instrument: OptionsInstrument, expiry_date: str | None) -> dict[str, Any] | None:
    try:
        from nselib import derivatives
    except ImportError:
        return None

    symbol = instrument.underlying_symbol
    expiry_arg = None
    if expiry_date:
        # nselib expects DD-MM-YYYY when provided
        raw = expiry_date.strip().upper().replace("-", "")
        if len(raw) == 7:
            expiry_arg = f"{raw[:2]}-{raw[2:5]}-{raw[5:]}"

    try:
        if instrument.instrument_type.value == "index":
            frame = derivatives.nse_live_option_chain(
                symbol=symbol,
                expiry_date=expiry_arg,
                oi_mode="compact",
            )
        else:
            frame = derivatives.nse_live_option_chain(
                symbol=symbol,
                expiry_date=expiry_arg,
                oi_mode="compact",
                instrument="equities",
            )
    except Exception as exc:
        logger.warning("nselib option chain failed for %s: %s", symbol, exc)
        return None

    if frame is None or getattr(frame, "empty", True):
        return None

    rows = frame.to_dict("records")
    spot = _float_val(rows[0].get("Underlying_Value") if rows else None)
    expiry = str(rows[0].get("Expiry_Date") or expiry_date or "")
    chain = _nselib_rows_to_chain(rows)
    if not chain:
        return None

    strikes = [float(r["strike"]) for r in chain]
    atm = min(strikes, key=lambda s: abs(s - (spot or strikes[len(strikes) // 2])))
    return {
        "underlying": symbol,
        "underlying_ltp": spot,
        "expiry_date": expiry,
        "atm_strike": atm,
        "chain": chain,
        "expiries": [expiry] if expiry else [],
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

    def _apply_fallback(reason: str) -> bool:
        nonlocal vendor, data
        errors.append(reason)
        fallback = _fetch_nselib_chain(instrument, expiry_date)
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
    if status in {"ok", "partial"} and data.get("chain"):
        try:
            from trade_integrations.hub_capture.writers import record_chain_snapshot

            symbol = str(data.get("underlying") or instrument.underlying_symbol).upper()
            record_chain_snapshot(
                symbol,
                data,
                source=str(data.get("source") or vendor),
                vendor=vendor,
                captured_at=now.isoformat(),
            )
        except Exception:
            logger.debug("hub capture chain snapshot skipped", exc_info=True)
    return StageResult(
        stage="chain",
        status=status,
        vendor=vendor,
        fetched_at=now,
        data=data,
        errors=errors,
    )
