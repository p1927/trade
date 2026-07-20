"""Nifty 50 constituent list and index weights."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.index_research.factor_store import get_factor_data_dir
from trade_integrations.dataflows.index_research.models import ConstituentRow
from trade_integrations.dataflows.index_research.sources.weights_nse import (
    fetch_nifty50_weights,
    fetch_yfinance_mcap_weights,
)

logger = logging.getLogger(__name__)

_EQUAL_WEIGHT = 1.0 / 50.0
_NSELIB_VENDOR = "nselib"
_NSELIB_CAPABILITY = "nifty50_equity_list"
_logged_tiers: set[str] = set()
_ROWS_CACHE: dict[str, Any] | None = None
_RESULT_CACHE: list[ConstituentRow] | None = None
_RESULT_CACHE_AT: float = 0.0

# Last-resort fallback when nselib and data/nse/historic_data are unavailable.
_NIFTY50_HARDCODED: tuple[str, ...] = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "ITC", "INFY", "INDIGO", "JSWSTEEL", "JIOFIN", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "MAXHEALTH", "NTPC", "NESTLEIND", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA", "TCS", "TATACONSUM",
    "TMPV", "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
)


def get_weights_cache_path() -> Path:
    """Return path to cached Nifty 50 weights JSON."""
    return get_factor_data_dir().parent / "weights" / "latest.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_weight_map(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {
        symbol.upper().strip(): float(value)
        for symbol, value in weights.items()
        if symbol and value is not None and float(value) > 0
    }
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in cleaned.items()}


def _load_cached_weights(path: Path) -> tuple[dict[str, float], str] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("cached weights unreadable: %s", exc)
        return None

    raw = payload.get("weights") if isinstance(payload, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None

    normalized = _normalize_weight_map(raw)
    if not normalized:
        return None
    source = str(payload.get("source") or "cache")
    return normalized, source


def _save_weights_cache(
    path: Path,
    *,
    weights: dict[str, float],
    source: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of": _now_utc().isoformat(),
        "source": source,
        "weights": weights,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _symbols_from_json_payload(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("symbols")
    if not isinstance(raw, list):
        return []
    return [str(symbol).upper().strip() for symbol in raw if str(symbol).strip()]


def _rows_from_symbols(symbols: list[str], *, source: str = "local") -> list[dict[str, str]]:
    return [
        {"symbol": symbol, "name": symbol, "sector": "", "source": source}
        for raw in symbols
        if (symbol := str(raw).upper().strip())
    ]


def _constituents_cache_ttl_sec() -> float:
    raw = os.getenv("CONSTITUENTS_CACHE_TTL_SEC", "86400").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 86400.0


def _log_source_tier(source_tier: str, count: int) -> None:
    if source_tier in _logged_tiers:
        return
    _logged_tiers.add(source_tier)
    labels = {
        "local_historic_data": "local historic_data",
        "niftyindices": "niftyindices CSV",
        "nselib": "nselib",
        "hardcoded": "hardcoded fallback",
    }
    label = labels.get(source_tier, source_tier)
    logger.info("Nifty 50 constituents: using %s (%d symbols)", label, count)


def _fetch_local_nifty50_rows() -> list[dict[str, str]]:
    """Primary source — repo JSON/CSV then hub curated cache."""
    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import hub_dir
    from trade_integrations.nse_browser.parsers.historic_data import (
        _LOCAL_NIFTY50_LIST_NAMES,
        historic_data_dir,
        parse_ind_nifty50_list_csv,
    )
    from trade_integrations.nse_browser.repository import repo_root

    hist_dir = historic_data_dir(repo_root())
    candidates: list[tuple[Path, str]] = [
        (hist_dir / "ind_nifty50_constituents_current.json", "nse_historic_data_ind_nifty50list"),
        (hub_dir() / "nifty50" / "constituents_current.json", "hub_constituents_current"),
    ]
    for path, source in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("constituents local JSON unreadable (%s): %s", path, exc)
            continue
        if not isinstance(payload, dict):
            continue
        symbols = _symbols_from_json_payload(payload)
        if symbols:
            return _rows_from_symbols(symbols, source=source)

    for name in _LOCAL_NIFTY50_LIST_NAMES:
        parsed = parse_ind_nifty50_list_csv(hist_dir / name)
        if parsed.get("status") == "ok" and parsed.get("symbols"):
            return _rows_from_symbols(list(parsed["symbols"]), source="ind_nifty50list_csv")
    return []


def nifty50_fallback_symbols() -> tuple[str, ...]:
    """Last-resort symbol list when load_nifty50_constituents returns empty."""
    rows = _fetch_local_nifty50_rows()
    if rows:
        return tuple(row["symbol"] for row in rows)
    return _NIFTY50_HARDCODED


def _fetch_niftyindices_rows() -> list[dict[str, str]]:
    from trade_integrations.openalgo.bulk_history_persist import _fetch_niftyindices_symbols

    symbols = _fetch_niftyindices_symbols(_NSELIB_CAPABILITY)
    if symbols:
        return _rows_from_symbols(symbols, source="niftyindices")
    return []


def _fetch_nselib_rows() -> list[dict[str, str]]:
    from trade_integrations.dataflows import source_availability

    if not source_availability.should_attempt(_NSELIB_VENDOR, _NSELIB_CAPABILITY):
        return []

    try:
        from nselib import capital_market
    except ImportError as exc:
        source_availability.record_failure(_NSELIB_VENDOR, _NSELIB_CAPABILITY, exc)
        logger.info("nselib not installed; cannot load Nifty 50 list")
        return []

    try:
        frame = capital_market.nifty50_equity_list()
    except Exception as exc:
        source_availability.record_failure(_NSELIB_VENDOR, _NSELIB_CAPABILITY, exc)
        logger.info("nselib nifty50_equity_list failed: %s", exc)
        return []

    if frame is None or getattr(frame, "empty", True):
        source_availability.record_failure(
            _NSELIB_VENDOR,
            _NSELIB_CAPABILITY,
            "empty nifty50_equity_list frame",
        )
        return []

    symbol_col = "Symbol" if "Symbol" in frame.columns else None
    name_col = "Company Name" if "Company Name" in frame.columns else None
    sector_col = "Industry" if "Industry" in frame.columns else None
    if not symbol_col:
        source_availability.record_failure(
            _NSELIB_VENDOR,
            _NSELIB_CAPABILITY,
            "missing Symbol column in nifty50_equity_list",
        )
        return []

    rows: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        symbol = str(row[symbol_col]).upper().strip()
        if not symbol:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(row[name_col]).strip() if name_col else symbol,
                "sector": str(row[sector_col]).strip() if sector_col else "",
                "source": "nselib",
            }
        )
    if not rows:
        source_availability.record_failure(
            _NSELIB_VENDOR,
            _NSELIB_CAPABILITY,
            "no symbols parsed from nifty50_equity_list",
        )
        return []

    source_availability.record_success(_NSELIB_VENDOR, _NSELIB_CAPABILITY)
    return rows


def _fetch_hardcoded_nifty50_rows() -> list[dict[str, str]]:
    return _rows_from_symbols(list(_NIFTY50_HARDCODED), source="hardcoded")


def _fetch_nifty50_rows(*, allow_nselib: bool = False) -> tuple[list[dict[str, str]], str]:
    rows = _fetch_local_nifty50_rows()
    if rows:
        _log_source_tier("local_historic_data", len(rows))
        return rows, "local_historic_data"

    rows = _fetch_niftyindices_rows()
    if rows:
        _log_source_tier("niftyindices", len(rows))
        return rows, "niftyindices"

    if allow_nselib:
        rows = _fetch_nselib_rows()
        if rows:
            _log_source_tier("nselib", len(rows))
            return rows, "nselib"

    rows = _fetch_hardcoded_nifty50_rows()
    _log_source_tier("hardcoded", len(rows))
    return rows, "hardcoded"


def _resolve_weights(
    symbols: list[str],
    *,
    force_refresh: bool,
    cache_path: Path,
) -> tuple[dict[str, float], str]:
    if not force_refresh:
        cached = _load_cached_weights(cache_path)
        if cached:
            return cached

    nse_weights = fetch_nifty50_weights()
    if nse_weights:
        normalized = _normalize_weight_map(nse_weights)
        if normalized:
            _save_weights_cache(cache_path, weights=normalized, source="nse")
            return normalized, "nse"

    yf_weights = fetch_yfinance_mcap_weights(symbols)
    if yf_weights:
        normalized = _normalize_weight_map(yf_weights)
        if normalized:
            _save_weights_cache(cache_path, weights=normalized, source="yfinance_mcap")
            return normalized, "yfinance_mcap"

    cached = _load_cached_weights(cache_path)
    if cached:
        return cached

    return {}, "equal_weight"


def _merge_constituents(
    rows: list[dict[str, str]],
    weights: dict[str, float],
) -> list[ConstituentRow]:
    if not rows:
        return []

    merged: list[ConstituentRow] = []
    for row in rows:
        symbol = row["symbol"]
        weight = weights.get(symbol, _EQUAL_WEIGHT)
        merged.append(
            ConstituentRow(
                symbol=symbol,
                name=row.get("name") or symbol,
                sector=row.get("sector") or "",
                weight=weight,
            )
        )

    total = sum(item.weight for item in merged)
    if total <= 0:
        equal = 1.0 / len(merged)
        for item in merged:
            item.weight = equal
        return merged

    if abs(total - 1.0) > 1e-9:
        for item in merged:
            item.weight = item.weight / total
    return merged


def clear_constituents_cache() -> None:
    """Reset in-process constituent caches (tests and force_refresh)."""
    global _ROWS_CACHE, _RESULT_CACHE, _RESULT_CACHE_AT
    _ROWS_CACHE = None
    _RESULT_CACHE = None
    _RESULT_CACHE_AT = 0.0
    _logged_tiers.clear()


def load_nifty50_constituents(*, force_refresh: bool = False) -> list[ConstituentRow]:
    """Load Nifty 50 constituents with normalized index weights."""
    global _ROWS_CACHE, _RESULT_CACHE, _RESULT_CACHE_AT

    if force_refresh:
        clear_constituents_cache()

    now = time.monotonic()
    ttl = _constituents_cache_ttl_sec()
    if (
        not force_refresh
        and _RESULT_CACHE is not None
        and now - _RESULT_CACHE_AT < ttl
    ):
        return list(_RESULT_CACHE)

    rows: list[dict[str, str]] | None = None
    if (
        not force_refresh
        and _ROWS_CACHE is not None
        and now - float(_ROWS_CACHE.get("cached_at") or 0.0) < ttl
    ):
        cached_rows = _ROWS_CACHE.get("rows")
        if isinstance(cached_rows, list) and cached_rows:
            rows = cached_rows

    if rows is None:
        rows, source_tier = _fetch_nifty50_rows(allow_nselib=force_refresh)
        if rows:
            _ROWS_CACHE = {
                "rows": rows,
                "source_tier": source_tier,
                "cached_at": now,
            }

    if not rows:
        return []

    symbols = [row["symbol"] for row in rows]
    cache_path = get_weights_cache_path()
    weights, _source = _resolve_weights(symbols, force_refresh=force_refresh, cache_path=cache_path)
    merged = _merge_constituents(rows, weights)
    _RESULT_CACHE = merged
    _RESULT_CACHE_AT = now
    return list(merged)
