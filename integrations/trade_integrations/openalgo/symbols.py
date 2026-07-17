"""OpenAlgo symbol and expiry normalization (no TradingAgents graph imports)."""

from __future__ import annotations

from trade_integrations.dataflows.errors import NoMarketDataError

# Yahoo / TradingAgents aliases -> OpenAlgo (symbol, exchange)
# Index symbols use NSE_INDEX per OpenAlgo docs (docs.openalgo.in/symbol-format)
_OPENALGO_ALIASES: dict[str, tuple[str, str]] = {
    "^NSEI": ("NIFTY", "NSE_INDEX"),
    "NIFTY50": ("NIFTY", "NSE_INDEX"),
    "^BSESN": ("SENSEX", "BSE_INDEX"),
    "NIFTY": ("NIFTY", "NSE_INDEX"),
    "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
    "FINNIFTY": ("FINNIFTY", "NSE_INDEX"),
    "MIDCPNIFTY": ("MIDCPNIFTY", "NSE_INDEX"),
    "SENSEX": ("SENSEX", "BSE_INDEX"),
    "^INDIAVIX": ("INDIAVIX", "NSE_INDEX"),
    "INDIAVIX": ("INDIAVIX", "NSE_INDEX"),
}


def resolve_openalgo_symbol(symbol: str) -> tuple[str, str]:
    """Map a TradingAgents ticker to OpenAlgo symbol + exchange."""
    raw = symbol.strip().upper()
    if raw in _OPENALGO_ALIASES:
        return _OPENALGO_ALIASES[raw]
    if raw.endswith(".NS"):
        return raw[:-3], "NSE"
    if raw.endswith(".BO"):
        return raw[:-3], "BSE"
    if raw.startswith("^"):
        raise NoMarketDataError(
            symbol,
            raw,
            f"index {raw!r} is not mapped for OpenAlgo (use NIFTY / BANKNIFTY or *.NS)",
        )
    # Plain equity symbols default to NSE (RELIANCE, SBIN, …).
    return raw, "NSE"


def normalize_openalgo_expiry(expiry: str) -> str:
    """Convert OpenAlgo expiry (DD-MMM-YY or DDMMMYY) to DDMMMYY for optionchain."""
    raw = expiry.strip().upper().replace("-", "")
    return raw
