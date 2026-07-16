"""India symbol registry from OpenAlgo SymToken (broker master contracts)."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from trade_integrations.dataflows.symbol_registry.openalgo_indices import ALL_INDEX_SYMBOLS

logger = logging.getLogger(__name__)

_CASH_EXCHANGES = frozenset({"NSE", "BSE", "NSE_INDEX", "BSE_INDEX"})
_FNO_EXCHANGES = frozenset({"NFO", "BFO", "MCX", "CDS"})
_CACHE_TTL_SEC = int(os.getenv("SYMBOL_REGISTRY_TTL_SEC", "3600"))

_UNDERLYING_PATTERN = re.compile(
    r"^(.+?)"
    r"(\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2})"
    r"(?:\d+(?:\.\d+)?)?(?:FUT|CE|PE)?$",
    re.IGNORECASE,
)

_LEGACY_INDEX_ALIASES = frozenset({"NIFTY50", "^NSEI", "^BSESN"})


@dataclass
class IndiaSymbolRegistry:
    cash_symbols: frozenset[str]
    index_symbols: frozenset[str]
    fno_underlyings: frozenset[str]
    source: str
    loaded_at: float
    db_path: str | None = None

    def is_listed(self, symbol: str) -> bool:
        raw = symbol.strip().upper()
        if not raw:
            return False
        if raw.endswith(".NS") or raw.endswith(".BO"):
            return True
        if raw in _LEGACY_INDEX_ALIASES:
            return True
        base = raw.rsplit(".", 1)[0] if raw.endswith((".NS", ".BO")) else raw
        return base in self.cash_symbols or base in self.index_symbols

    def is_fno_underlying(self, symbol: str) -> bool:
        raw = symbol.strip().upper()
        if not raw:
            return False
        if raw.startswith("^"):
            raw = raw[1:]
        if raw in _LEGACY_INDEX_ALIASES:
            return True
        if raw in self.index_symbols or raw in ALL_INDEX_SYMBOLS:
            return True
        return raw in self.fno_underlyings


_registry_cache: IndiaSymbolRegistry | None = None
_registry_loaded_at: float = 0.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve_openalgo_db_path() -> Path | None:
    env_url = (
        os.getenv("OPENALGO_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()
    if env_url.startswith("sqlite:///"):
        rel = env_url.replace("sqlite:///", "", 1)
        path = Path(rel)
        if not path.is_absolute():
            path = _repo_root() / "openalgo" / path
        return path if path.is_file() else None
    default = _repo_root() / "openalgo" / "db" / "openalgo.db"
    return default if default.is_file() else None


def _extract_underlying(symbol: str, exchange: str) -> str | None:
    if exchange not in _FNO_EXCHANGES:
        return None
    match = _UNDERLYING_PATTERN.match(symbol.upper())
    if match:
        return match.group(1).upper()
    return None


def _load_from_sqlite(db_path: Path) -> IndiaSymbolRegistry | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT UPPER(symbol) AS symbol, UPPER(exchange) AS exchange
            FROM symtoken
            WHERE exchange IS NOT NULL
            """
        )
        cash: set[str] = set(ALL_INDEX_SYMBOLS)
        index: set[str] = set(ALL_INDEX_SYMBOLS)
        fno_underlyings: set[str] = set(ALL_INDEX_SYMBOLS)

        for row in cur.fetchall():
            sym = str(row["symbol"] or "").strip().upper()
            exch = str(row["exchange"] or "").strip().upper()
            if not sym or not exch:
                continue
            if exch in _CASH_EXCHANGES:
                cash.add(sym)
                if exch.endswith("_INDEX"):
                    index.add(sym)
            elif exch in _FNO_EXCHANGES:
                underlying = _extract_underlying(sym, exch)
                if underlying:
                    fno_underlyings.add(underlying)

        conn.close()
        return IndiaSymbolRegistry(
            cash_symbols=frozenset(cash),
            index_symbols=frozenset(index),
            fno_underlyings=frozenset(fno_underlyings),
            source="openalgo_symtoken",
            loaded_at=time.time(),
            db_path=str(db_path),
        )
    except Exception as exc:
        logger.info("OpenAlgo symtoken registry load failed: %s", exc)
        return None


def _openalgo_search_settings() -> tuple[str, str] | None:
    host = (os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").rstrip("/")
    api_key = (os.getenv("OPENALGO_API_KEY") or "").strip()
    if not api_key:
        return None
    return host, api_key


def probe_india_symbol_live(symbol: str) -> dict[str, Any] | None:
    """Live exact-match probe via OpenAlgo REST search (fallback when DB missing)."""
    settings = _openalgo_search_settings()
    if not settings:
        return None
    host, api_key = settings
    raw = symbol.strip().upper()
    if not raw:
        return None
    try:
        response = requests.post(
            f"{host}/api/v1/search",
            json={"apikey": api_key, "query": raw},
            timeout=10,
        )
        body = response.json() if response.content else {}
        if not response.ok or body.get("status") != "success":
            return None
        rows = body.get("data") or []
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() == raw:
                return row
        return None
    except Exception as exc:
        logger.debug("OpenAlgo live symbol probe failed for %s: %s", raw, exc)
        return None


def is_india_symbol_live(symbol: str) -> bool:
    row = probe_india_symbol_live(symbol)
    if not row:
        return False
    exchange = str(row.get("exchange") or "").upper()
    return exchange in _CASH_EXCHANGES or exchange in _FNO_EXCHANGES


def load_india_registry(*, force_refresh: bool = False) -> IndiaSymbolRegistry | None:
    global _registry_cache, _registry_loaded_at
    now = time.time()
    if (
        not force_refresh
        and _registry_cache is not None
        and (now - _registry_loaded_at) < _CACHE_TTL_SEC
    ):
        return _registry_cache

    db_path = resolve_openalgo_db_path()
    registry = _load_from_sqlite(db_path) if db_path else None
    if registry is not None:
        _registry_cache = registry
        _registry_loaded_at = now
        logger.info(
            "Loaded India symbol registry from %s (%d cash, %d F&O underlyings)",
            db_path,
            len(registry.cash_symbols),
            len(registry.fno_underlyings),
        )
        return registry
    return None


def get_india_registry() -> IndiaSymbolRegistry | None:
    return load_india_registry()


def is_india_listed_symbol(symbol: str) -> bool:
    registry = get_india_registry()
    if registry and registry.is_listed(symbol):
        return True
    if is_india_symbol_live(symbol):
        return True
    return False


def is_india_fno_underlying(symbol: str) -> bool:
    registry = get_india_registry()
    if registry and registry.is_fno_underlying(symbol):
        return True
    row = probe_india_symbol_live(symbol)
    if row:
        exchange = str(row.get("exchange") or "").upper()
        if exchange in _FNO_EXCHANGES or exchange.endswith("_INDEX"):
            return True
    return False


def clear_india_registry_cache() -> None:
    global _registry_cache, _registry_loaded_at
    _registry_cache = None
    _registry_loaded_at = 0.0
