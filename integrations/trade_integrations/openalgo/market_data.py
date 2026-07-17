"""OpenAlgo vendor fetchers and option-chain normalization (no hub channel)."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.openalgo.rest_client import get_rest_client, openalgo_settings
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry, resolve_openalgo_symbol
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.stockstats_utils import _clean_dataframe

logger = logging.getLogger(__name__)


def openalgo_configured() -> bool:
    try:
        openalgo_settings()
        return bool(get_rest_client().api_key)
    except (RuntimeError, VendorNotConfiguredError):
        return False


def openalgo_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to OpenAlgo REST API; map client errors to TradingAgents vendor errors."""
    try:
        host, api_key = openalgo_settings()
    except RuntimeError as exc:
        raise VendorNotConfiguredError(
            "OPENALGO_API_KEY is not set. Start OpenAlgo, log in, generate an API "
            "key in the dashboard, and add it to your .env file."
        ) from exc

    if not api_key:
        raise VendorNotConfiguredError(
            "OPENALGO_API_KEY is not set. Start OpenAlgo, log in, generate an API "
            "key in the dashboard, and add it to your .env file."
        )

    body = {**payload, "apikey": api_key}
    symbol = str(payload.get("symbol") or payload.get("underlying") or "?")
    try:
        parsed = get_rest_client(host=host, api_key=api_key).post(endpoint, body)
    except RuntimeError as exc:
        message = str(exc)
        if "429" in message or "rate limit" in message.lower():
            raise VendorRateLimitError(f"OpenAlgo rate limited: {endpoint}") from exc
        raise NoMarketDataError(symbol, payload.get("symbol"), f"OpenAlgo {endpoint} error: {message}") from exc

    if parsed.get("status") not in (None, "success"):
        message = parsed.get("message") or parsed.get("error") or str(parsed)[:200]
        raise NoMarketDataError(symbol, payload.get("symbol"), f"OpenAlgo {endpoint} error: {message}")
    return parsed


def _coerce_history_timestamps(series: pd.Series) -> pd.Series:
    """OpenAlgo/INDstocks returns Unix seconds; yfinance returns datetimes."""
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() >= max(1, len(series) // 2):
        max_val = float(numeric.max())
        if max_val > 1e12:
            return pd.to_datetime(numeric, unit="ms", errors="coerce")
        if max_val > 1e9:
            return pd.to_datetime(numeric, unit="s", errors="coerce")
    return pd.to_datetime(series, errors="coerce")


def _history_rows_to_frame(rows: list) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame = frame.rename(
        columns={
            "timestamp": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    frame["Date"] = _coerce_history_timestamps(frame["Date"])
    frame = frame.dropna(subset=["Date"])
    return _clean_dataframe(frame)


def fetch_history_raw(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    interval: str = "D",
) -> pd.DataFrame:
    """Fetch OHLCV from OpenAlgo historical API."""
    oa_symbol, oa_exchange = resolve_openalgo_symbol(symbol)
    parsed = openalgo_post(
        "history",
        {
            "symbol": oa_symbol,
            "exchange": oa_exchange,
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    rows = parsed.get("data") or []
    if not rows:
        raise NoMarketDataError(
            symbol,
            f"{oa_symbol}@{oa_exchange}",
            "OpenAlgo history returned no rows",
        )
    return _history_rows_to_frame(rows)


def _quote_data(oa_symbol: str, exchange: str) -> dict | None:
    try:
        parsed = openalgo_post("quotes", {"symbol": oa_symbol, "exchange": exchange})
        return parsed.get("data")
    except (NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError):
        return None


def fetch_quote_raw(symbol: str) -> dict | None:
    """Direct OpenAlgo quote fetch (no hub channel)."""
    oa_symbol, exchange = resolve_openalgo_symbol(symbol)
    data = _quote_data(oa_symbol, exchange)
    if not data:
        return None
    return {
        "ltp": data.get("ltp") or data.get("last_price"),
        "volume": data.get("volume"),
        "change_pct": data.get("change_percent") or data.get("change_pct"),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "source": "openalgo",
    }


def _extract_multiquote_rows(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload.get("quotes"), list):
        return [row for row in payload["quotes"] if isinstance(row, dict)]
    if isinstance(payload.get("data"), list):
        return [row for row in payload["data"] if isinstance(row, dict)]
    if isinstance(payload.get("results"), list):
        return [row for row in payload["results"] if isinstance(row, dict)]
    return []


def parse_multi_quotes_payload(payload: dict[str, Any] | list[Any]) -> dict[str, dict[str, Any]]:
    """Normalize multiquotes response to ``symbol@exchange`` -> quote row."""
    rows = _extract_multiquote_rows(payload if isinstance(payload, dict) else {"data": payload})
    if not rows and isinstance(payload, dict):
        if payload and all(isinstance(value, dict) for value in payload.values()):
            out: dict[str, dict[str, Any]] = {}
            for raw_key, row in payload.items():
                symbol = str(row.get("symbol") or raw_key).upper()
                exchange = str(row.get("exchange") or "NSE").upper()
                out[f"{symbol}@{exchange}"] = row
            return out
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").upper()
        if not symbol:
            continue
        exchange = str(row.get("exchange") or "NSE").upper()
        out[f"{symbol}@{exchange}"] = row
    return out


def fetch_multi_quotes_raw(requests: list[dict[str, str]]) -> dict[str, Any]:
    """Batch quote fetch via OpenAlgo multiquotes endpoint."""
    normalized = [
        {"symbol": row["symbol"].upper(), "exchange": row["exchange"].upper()}
        for row in requests
        if isinstance(row, dict) and row.get("symbol") and row.get("exchange")
    ]
    if not normalized:
        return {}
    parsed = openalgo_post("multiquotes", {"symbols": normalized})
    data = parsed.get("data")
    return data if isinstance(data, dict) else parsed


def _unwrap_openalgo_market_payload(parsed: dict) -> dict:
    """OpenAlgo returns option chain fields at top level; expiry dates under data."""
    data = parsed.get("data")
    if isinstance(data, dict) and data.get("chain"):
        return data
    if parsed.get("chain"):
        return parsed
    if isinstance(data, list):
        return {"chain": data}
    return data if isinstance(data, dict) else {}


def normalize_option_chain_response(
    parsed: dict[str, Any],
    underlying: str,
    expiry: str | None,
) -> dict[str, Any]:
    """Normalize OpenAlgo option-chain payload with PCR and totals."""
    meta = _unwrap_openalgo_market_payload(parsed)
    chain = meta.get("chain") or []
    ce_oi = sum(int(row.get("ce", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pe_oi = sum(int(row.get("pe", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pcr = round(pe_oi / ce_oi, 4) if ce_oi else None
    return {
        "underlying": meta.get("underlying") or underlying.upper(),
        "underlying_ltp": meta.get("underlying_ltp"),
        "expiry_date": meta.get("expiry_date") or expiry,
        "atm_strike": meta.get("atm_strike"),
        "chain": chain,
        "pcr": pcr,
        "total_call_oi": ce_oi,
        "total_put_oi": pe_oi,
        "source": "openalgo",
    }


def fetch_option_chain_raw(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict[str, Any]:
    """Direct OpenAlgo option chain fetch (no hub channel)."""
    body: dict[str, Any] = {
        "underlying": underlying.upper(),
        "exchange": exchange.upper(),
    }
    normalized_expiry = normalize_openalgo_expiry(expiry_date) if expiry_date else None
    if normalized_expiry:
        body["expiry_date"] = normalized_expiry
    if strike_count is not None:
        body["strike_count"] = strike_count
    parsed = openalgo_post("optionchain", body)
    return normalize_option_chain_response(parsed, underlying, normalized_expiry or expiry_date)


def fetch_option_expiry_dates(
    symbol: str,
    exchange: str = "NFO",
    *,
    instrument_type: str = "options",
) -> list[str]:
    """Return available option expiry dates (DD-MMM-YY or DDMMMYY) from OpenAlgo."""
    parsed = openalgo_post(
        "expiry",
        {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "instrumenttype": instrument_type,
        },
    )
    data = parsed.get("data") or {}
    if isinstance(data, list):
        return [str(x) for x in data]
    return list(data.get("expiry_dates") or data.get("expiries") or [])


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


def _fetch_nselib_chain(
    underlying: str,
    expiry_date: str | None,
    *,
    is_index: bool,
) -> dict[str, Any] | None:
    try:
        from nselib import derivatives
    except ImportError:
        return None

    symbol = underlying.upper()
    expiry_arg = None
    if expiry_date:
        raw = expiry_date.strip().upper().replace("-", "")
        if len(raw) == 7:
            expiry_arg = f"{raw[:2]}-{raw[2:5]}-{raw[5:]}"

    try:
        if is_index:
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


def fetch_option_chain_with_fallback(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
    is_index: bool = True,
) -> dict[str, Any]:
    """Fetch option chain from OpenAlgo; fall back to nselib only when OpenAlgo fails."""
    try:
        chain = fetch_option_chain_raw(
            underlying,
            exchange,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )
        if chain.get("chain"):
            return chain
    except Exception as exc:
        logger.debug("OpenAlgo option chain failed for %s: %s", underlying, exc)

    fallback = _fetch_nselib_chain(underlying, expiry_date, is_index=is_index)
    if fallback:
        return fallback

    raise NoMarketDataError(
        underlying,
        underlying.upper(),
        "OpenAlgo and nselib option chain both unavailable",
    )
