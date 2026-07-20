"""OpenAlgo vendor fetchers and option-chain normalization (no hub channel)."""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from trade_integrations.openalgo.rest_client import get_rest_client, openalgo_settings
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry, resolve_openalgo_symbol
from trade_integrations.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

logger = logging.getLogger(__name__)

_MIN_INDEX_CHAIN_STRIKES = 3


def _openalgo_capability(endpoint: str) -> str:
    ep = endpoint.strip().lower().lstrip("/")
    if ep in ("quotes", "multiquotes"):
        return "quotes"
    if ep == "optionchain":
        return "optionchain"
    if ep == "history":
        return "history"
    return "api"


def _record_openalgo_failure(endpoint: str, exc: Exception | str) -> None:
    from trade_integrations.dataflows import source_availability
    from trade_integrations.dataflows.company_research.sources.resilience import classify_error

    code = classify_error(exc)
    if code in ("openalgo_not_configured", "openalgo_unreachable"):
        source_availability.record_failure("openalgo", _openalgo_capability(endpoint), exc)


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLCV rows for downstream indicators (no TradingAgents import)."""
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _chain_strike_count(chain: dict[str, Any] | None) -> int:
    if not chain:
        return 0
    legs = chain.get("chain") or []
    return len(legs) if isinstance(legs, list) else 0


def _chain_oi_totals(chain: dict[str, Any]) -> tuple[float, float]:
    ce_oi = chain.get("total_call_oi")
    pe_oi = chain.get("total_put_oi")
    if ce_oi is not None and pe_oi is not None:
        try:
            return float(ce_oi), float(pe_oi)
        except (TypeError, ValueError):
            pass
    ce_total = pe_total = 0.0
    for leg in chain.get("chain") or []:
        if not isinstance(leg, dict):
            continue
        ce = leg.get("ce") if isinstance(leg.get("ce"), dict) else {}
        pe = leg.get("pe") if isinstance(leg.get("pe"), dict) else {}
        try:
            ce_total += float(ce.get("oi") or 0)
        except (TypeError, ValueError):
            pass
        try:
            pe_total += float(pe.get("oi") or 0)
        except (TypeError, ValueError):
            pass
    return ce_total, pe_total


def chain_is_usable(chain: dict[str, Any] | None, *, min_strikes: int = _MIN_INDEX_CHAIN_STRIKES) -> bool:
    """Reject sparse chains (single PE-only leg) that produce NaN PCR."""
    if not chain:
        return False
    legs = chain.get("chain") or []
    if not isinstance(legs, list) or not legs:
        return False
    ce_oi, pe_oi = _chain_oi_totals(chain)
    if ce_oi <= 0 or pe_oi <= 0:
        return False
    if len(legs) < min_strikes:
        return False
    pcr = chain.get("pcr")
    if pcr is not None:
        try:
            if math.isnan(float(pcr)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def resolve_default_option_expiry(underlying: str, exchange: str) -> str | None:
    """Nearest listed expiry from OpenAlgo (required for reliable INDstocks chains)."""
    try:
        expiries = fetch_option_expiry_dates(underlying, exchange)
    except Exception as exc:
        logger.debug("expiry lookup failed for %s@%s: %s", underlying, exchange, exc)
        return None
    return expiries[0] if expiries else None


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
        _record_openalgo_failure(endpoint, exc)
        raise VendorNotConfiguredError(
            "OPENALGO_API_KEY is not set. Start OpenAlgo, log in, generate an API "
            "key in the dashboard, and add it to your .env file."
        ) from exc

    if not api_key:
        _record_openalgo_failure(endpoint, "OPENALGO_API_KEY is not set")
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
        _record_openalgo_failure(endpoint, exc)
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


def _parse_quote_ltp(data: dict[str, Any]) -> tuple[float | None, str | None]:
    """Return (ltp, error). Treat vendor zero LTP as failure."""
    error = data.get("error") or data.get("message")
    raw_ltp = data.get("ltp")
    if raw_ltp is None:
        raw_ltp = data.get("last_price")
    try:
        ltp = float(raw_ltp) if raw_ltp is not None else None
    except (TypeError, ValueError):
        ltp = None
    if ltp is not None and ltp <= 0:
        return None, str(error or "vendor_zero_ltp")
    if ltp is None:
        return None, str(error or "missing_ltp")
    if error:
        return None, str(error)
    return ltp, None


def fetch_quote_raw(symbol: str, *, exchange: str | None = None) -> dict | None:
    """Direct OpenAlgo quote fetch (no hub channel)."""
    from trade_integrations.dataflows import source_availability

    if not source_availability.should_attempt("openalgo", "quotes"):
        return {
            "ltp": None,
            "volume": None,
            "change_pct": None,
            "high_52w": None,
            "low_52w": None,
            "source": "openalgo",
            "quote_error": "openalgo_quotes_circuit_open",
        }

    oa_symbol, resolved_exchange = resolve_openalgo_symbol(symbol)
    data = _quote_data(oa_symbol, exchange or resolved_exchange)
    if not data:
        return {
            "ltp": None,
            "volume": None,
            "change_pct": None,
            "high_52w": None,
            "low_52w": None,
            "source": "openalgo",
            "quote_error": "no_quote_data",
        }

    ltp, quote_error = _parse_quote_ltp(data)
    if ltp is None:
        return {
            "ltp": None,
            "volume": data.get("volume"),
            "change_pct": data.get("change_percent") or data.get("change_pct"),
            "high_52w": data.get("high_52w"),
            "low_52w": data.get("low_52w"),
            "source": "openalgo",
            "quote_error": quote_error,
            "error": data.get("error") or quote_error,
        }

    return {
        "ltp": ltp,
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
    """Batch quote fetch via OpenAlgo multiquotes endpoint (watch hot path — no hub channel)."""
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


def fetch_multi_quotes_for_research(requests: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Hub-channel multiquotes for research/MCP (write-through capture, not watch hot path)."""
    from trade_integrations.hub_capture.channel import get_multi_quotes
    from trade_integrations.openalgo.freshness import FreshnessPolicy

    normalized = [
        {"symbol": row["symbol"].upper(), "exchange": row["exchange"].upper()}
        for row in requests
        if isinstance(row, dict) and row.get("symbol") and row.get("exchange")
    ]
    if not normalized:
        return {}
    return get_multi_quotes(normalized, fetch_multi_quotes_raw, policy=FreshnessPolicy.NORMAL)


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
    from trade_integrations.dataflows import source_availability

    if not source_availability.should_attempt("openalgo", "optionchain"):
        return {}

    effective_expiry = expiry_date or resolve_default_option_expiry(underlying, exchange)
    normalized_expiry = normalize_openalgo_expiry(effective_expiry) if effective_expiry else None

    def _request(*, include_strike_count: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
        }
        if normalized_expiry:
            body["expiry_date"] = normalized_expiry
        if include_strike_count and strike_count is not None:
            body["strike_count"] = strike_count
        parsed = openalgo_post("optionchain", body)
        return normalize_option_chain_response(parsed, underlying, normalized_expiry or effective_expiry)

    if strike_count is not None:
        try:
            limited = _request(include_strike_count=True)
            if chain_is_usable(limited, min_strikes=min(strike_count, _MIN_INDEX_CHAIN_STRIKES)):
                return limited
        except Exception as exc:
            logger.debug(
                "OpenAlgo limited chain failed for %s (strike_count=%s): %s",
                underlying,
                strike_count,
                exc,
            )

    return _request(include_strike_count=False)


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


def _to_nselib_expiry(expiry_date: str | None) -> str | None:
    """Convert OpenAlgo-style expiry strings to nselib ``DD-MM-YYYY`` format."""
    if not expiry_date or not expiry_date.strip():
        return None

    from datetime import datetime

    text = expiry_date.strip().upper()
    candidates = [text.replace("-", ""), text]
    formats = ("%d%b%y", "%d-%b-%y", "%d-%m-%Y")
    for value in candidates:
        for fmt in formats:
            if fmt == "%d%b%y" and len(value) != 7:
                continue
            try:
                parsed = datetime.strptime(value, fmt)
            except ValueError:
                continue
            return parsed.strftime("%d-%m-%Y")
    return None


def _fetch_nselib_chain(
    underlying: str,
    expiry_date: str | None,
    *,
    is_index: bool,
) -> dict[str, Any] | None:
    from trade_integrations.dataflows import source_availability

    capability = "option_chain"
    if not source_availability.should_attempt("nselib", capability):
        return None

    try:
        from nselib import derivatives
    except ImportError as exc:
        source_availability.record_failure("nselib", capability, exc)
        return None

    symbol = underlying.upper()
    expiry_arg = _to_nselib_expiry(expiry_date)

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
        source_availability.record_failure("nselib", capability, exc)
        logger.warning("nselib option chain failed for %s: %s", symbol, exc)
        return None

    if frame is None or getattr(frame, "empty", True):
        source_availability.record_failure("nselib", capability, "empty option chain frame")
        return None

    rows = frame.to_dict("records")
    spot = _float_val(rows[0].get("Underlying_Value") if rows else None)
    expiry = str(rows[0].get("Expiry_Date") or expiry_date or "")
    chain = _nselib_rows_to_chain(rows)
    if not chain:
        source_availability.record_failure("nselib", capability, "empty normalized option chain")
        return None

    strikes = [float(r["strike"]) for r in chain]
    atm = min(strikes, key=lambda s: abs(s - (spot or strikes[len(strikes) // 2])))
    source_availability.record_success("nselib", capability)
    return {
        "underlying": symbol,
        "underlying_ltp": spot,
        "expiry_date": expiry,
        "atm_strike": atm,
        "chain": chain,
        "expiries": [expiry] if expiry else [],
        "source": "nselib",
    }


def _is_index_underlying(underlying: str, exchange: str) -> bool:
    if exchange.upper() in ("NSE_INDEX", "BSE_INDEX"):
        return True
    return underlying.upper() in {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "INDIAVIX",
    }


def fetch_option_chain_channel_vendor(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict[str, Any]:
    """Hub-channel vendor fn: OpenAlgo primary, nselib fallback on failure."""
    return fetch_option_chain_with_fallback(
        underlying,
        exchange,
        expiry_date=expiry_date,
        strike_count=strike_count,
        is_index=_is_index_underlying(underlying, exchange),
    )


def fetch_option_chain_with_fallback(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
    is_index: bool = True,
) -> dict[str, Any]:
    """Fetch option chain from OpenAlgo; fall back to nselib only when OpenAlgo fails."""
    resolved_expiry = expiry_date or resolve_default_option_expiry(underlying, exchange)
    try:
        chain = fetch_option_chain_raw(
            underlying,
            exchange,
            expiry_date=resolved_expiry,
            strike_count=strike_count,
        )
        if chain_is_usable(chain):
            return chain
        if chain.get("chain"):
            logger.debug(
                "OpenAlgo chain for %s sparse (%s strikes); retrying without strike_count",
                underlying,
                _chain_strike_count(chain),
            )
            chain = fetch_option_chain_raw(
                underlying,
                exchange,
                expiry_date=resolved_expiry,
                strike_count=None,
            )
            if chain_is_usable(chain):
                return chain
    except Exception as exc:
        logger.debug("OpenAlgo option chain failed for %s: %s", underlying, exc)

    fallback = _fetch_nselib_chain(underlying, resolved_expiry, is_index=is_index)
    if fallback:
        return fallback

    raise NoMarketDataError(
        underlying,
        underlying.upper(),
        "OpenAlgo and nselib option chain both unavailable",
    )
