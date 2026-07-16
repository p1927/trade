"""Nifty 50 constituent list and index weights."""

from __future__ import annotations

import json
import logging
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


def _fetch_nselib_rows() -> list[dict[str, str]]:
    try:
        from nselib import capital_market
    except ImportError:
        logger.info("nselib not installed; cannot load Nifty 50 list")
        return []

    try:
        frame = capital_market.nifty50_equity_list()
    except Exception as exc:
        logger.info("nselib nifty50_equity_list failed: %s", exc)
        return []

    if frame is None or getattr(frame, "empty", True):
        return []

    symbol_col = "Symbol" if "Symbol" in frame.columns else None
    name_col = "Company Name" if "Company Name" in frame.columns else None
    sector_col = "Industry" if "Industry" in frame.columns else None
    if not symbol_col:
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
            }
        )
    return rows


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


def load_nifty50_constituents(*, force_refresh: bool = False) -> list[ConstituentRow]:
    """Load Nifty 50 constituents with normalized index weights."""
    rows = _fetch_nselib_rows()
    if not rows:
        return []

    symbols = [row["symbol"] for row in rows]
    cache_path = get_weights_cache_path()
    weights, _source = _resolve_weights(symbols, force_refresh=force_refresh, cache_path=cache_path)
    return _merge_constituents(rows, weights)
