"""OpenAlgo-only live index spot for prediction pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpotFetchResult:
    spot: float
    source: str  # "openalgo" | "unavailable"
    error: str | None = None


def _is_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "access_token",
            "expired",
            "revoked",
            "unauthorized",
            "authentication",
            "login",
            "session",
        )
    )


def fetch_index_spot(ticker: str) -> SpotFetchResult:
    """Fetch live index spot via OpenAlgo only (no hub cache, no history fallback)."""
    from trade_integrations.openalgo.market_data import fetch_quote_raw

    sym = (ticker or "NIFTY").strip().upper()
    quote = fetch_quote_raw(sym)
    if not quote:
        return SpotFetchResult(
            0.0,
            "unavailable",
            "OpenAlgo quote unavailable — check OpenAlgo is running and INDmoney is logged in",
        )

    raw_ltp = quote.get("ltp")
    try:
        ltp = float(raw_ltp) if raw_ltp is not None else 0.0
    except (TypeError, ValueError):
        ltp = 0.0

    if ltp > 0:
        return SpotFetchResult(ltp, "openalgo", None)

    err = (
        quote.get("quote_error")
        or quote.get("error")
        or "OpenAlgo returned no live LTP for index"
    )
    err_text = str(err)
    if _is_auth_error(err_text):
        err_text = f"{err_text} — re-login INDmoney in OpenAlgo"
    return SpotFetchResult(0.0, "unavailable", err_text)


def check_openalgo_index_quote_health(ticker: str) -> tuple[bool, str | None]:
    """Return (healthy, error_message)."""
    result = fetch_index_spot(ticker)
    if result.spot > 0:
        return True, None
    return False, result.error
