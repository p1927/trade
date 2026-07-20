"""US symbol registry from Alpaca active assets."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from trade_integrations.http import get

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = int(os.getenv("SYMBOL_REGISTRY_TTL_SEC", "3600"))
PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"

_FALLBACK_US_SYMBOLS = frozenset(
    {
        "AAPL",
        "AMD",
        "AMZN",
        "GOOG",
        "GOOGL",
        "INDA",
        "INDY",
        "INTC",
        "META",
        "MSFT",
        "NVDA",
        "QQQ",
        "SPY",
        "TSLA",
    }
)


@dataclass
class UsSymbolRegistry:
    symbols: frozenset[str]
    source: str
    loaded_at: float

    def is_listed(self, symbol: str) -> bool:
        raw = symbol.strip().upper()
        if not raw:
            return False
        if raw in self.symbols:
            return True
        if "." in raw and not raw.endswith((".NS", ".BO")):
            return True
        return False


_us_cache: UsSymbolRegistry | None = None
_us_loaded_at: float = 0.0


def _alpaca_settings() -> dict[str, str]:
    profile = (os.getenv("ALPACA_PROFILE") or "paper").strip().lower()
    is_paper = profile == "paper"
    return {
        "api_key": (os.getenv("ALPACA_API_KEY") or "").strip(),
        "secret": (
            os.getenv("ALPACA_API_SECRET")
            or os.getenv("ALPACA_SECRET_KEY")
            or ""
        ).strip(),
        "trade_base": (
            os.getenv("ALPACA_API_BASE")
            or (PAPER_HOST if is_paper else LIVE_HOST)
        ).rstrip("/"),
    }


def _alpaca_configured() -> bool:
    cfg = _alpaca_settings()
    return bool(cfg["api_key"] and cfg["secret"])


def _alpaca_headers() -> dict[str, str]:
    cfg = _alpaca_settings()
    return {
        "APCA-API-KEY-ID": cfg["api_key"],
        "APCA-API-SECRET-KEY": cfg["secret"],
    }


def _fetch_alpaca_assets() -> frozenset[str] | None:
    if not _alpaca_configured():
        return None
    cfg = _alpaca_settings()
    url = f"{cfg['trade_base']}/v2/assets"
    params = {"status": "active", "asset_class": "us_equity"}
    try:
        response = get(url, headers=_alpaca_headers(), params=params, timeout=30)
        if not response.ok:
            logger.info("Alpaca assets fetch failed: HTTP %s", response.status_code)
            return None
        payload = response.json()
        if not isinstance(payload, list):
            return None
        symbols: set[str] = set()
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("tradable", True)).lower() in {"false", "0"}:
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                symbols.add(sym)
        return frozenset(symbols) if symbols else None
    except Exception as exc:
        logger.info("Alpaca assets fetch error: %s", exc)
        return None


def load_us_registry(*, force_refresh: bool = False) -> UsSymbolRegistry:
    global _us_cache, _us_loaded_at
    now = time.time()
    if (
        not force_refresh
        and _us_cache is not None
        and (now - _us_loaded_at) < _CACHE_TTL_SEC
    ):
        return _us_cache

    symbols = _fetch_alpaca_assets()
    if symbols:
        registry = UsSymbolRegistry(symbols=symbols, source="alpaca_assets", loaded_at=now)
    else:
        registry = UsSymbolRegistry(
            symbols=_FALLBACK_US_SYMBOLS,
            source="fallback_static",
            loaded_at=now,
        )
    _us_cache = registry
    _us_loaded_at = now
    logger.info("Loaded US symbol registry (%s, %d symbols)", registry.source, len(registry.symbols))
    return registry


def get_us_registry() -> UsSymbolRegistry:
    return load_us_registry()


def is_us_listed_symbol(symbol: str) -> bool:
    return get_us_registry().is_listed(symbol)


def clear_us_registry_cache() -> None:
    global _us_cache, _us_loaded_at
    _us_cache = None
    _us_loaded_at = 0.0
