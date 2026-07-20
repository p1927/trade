"""Source adapters invoked by DataRouter."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.data_router.types import FetchSpec

logger = logging.getLogger(__name__)


class AdapterError(Exception):
    """Non-fatal adapter failure for chain continuation."""

    def __init__(self, message: str, *, reason: str = "error") -> None:
        super().__init__(message)
        self.reason = reason


def fetch_ohlcv(source_id: str, spec: FetchSpec) -> pd.DataFrame:
    """Fetch OHLCV from a catalog source id."""
    adapter = _resolve_adapter(source_id)
    if adapter is None:
        raise AdapterError(f"unknown source {source_id}", reason="not_configured")
    return adapter(spec)


def _resolve_adapter(source_id: str):
    sid = source_id.strip().lower()
    mapping = {
        "openalgo": _fetch_openalgo,
        "yfinance": _fetch_yfinance,
        "yahoo": _fetch_vibe_loader,
        "stooq": _fetch_vibe_loader,
        "tiingo": _fetch_vibe_loader,
        "fmp": _fetch_vibe_loader,
        "finnhub": _fetch_vibe_loader,
        "alphavantage": _fetch_vibe_loader,
        "alpha_vantage": _fetch_alpha_vantage,
        "eod_historical": _fetch_eod_historical,
    }
    return mapping.get(sid)


def _require_dates(spec: FetchSpec) -> tuple[str, str]:
    if not spec.start or not spec.end:
        raise AdapterError("start and end dates required", reason="no_data")
    return spec.start[:10], spec.end[:10]


def _fetch_openalgo(spec: FetchSpec) -> pd.DataFrame:
    if not spec.symbol:
        raise AdapterError("symbol required", reason="no_data")
    start, end = _require_dates(spec)
    try:
        from trade_integrations.dataflows.openalgo import get_openalgo_stock_data

        raw = get_openalgo_stock_data(spec.symbol, start, end)
        if isinstance(raw, pd.DataFrame):
            return raw
        if isinstance(raw, str) and raw.strip():
            from io import StringIO

            return pd.read_csv(StringIO(raw))
    except Exception as exc:
        raise AdapterError(str(exc), reason="error") from exc
    raise AdapterError("openalgo returned no data", reason="no_data")


def _fetch_yfinance(spec: FetchSpec) -> pd.DataFrame:
    if not spec.symbol:
        raise AdapterError("symbol required", reason="no_data")
    start, end = _require_dates(spec)
    try:
        from tradingagents.dataflows.y_finance import get_YFin_data_online

        raw = get_YFin_data_online(spec.symbol, start, end)
        if isinstance(raw, pd.DataFrame):
            return raw
        if isinstance(raw, str) and raw.strip():
            from io import StringIO

            return pd.read_csv(StringIO(raw))
    except Exception as exc:
        raise AdapterError(str(exc), reason="error") from exc
    raise AdapterError("yfinance returned no data", reason="no_data")


def _fetch_vibe_loader(spec: FetchSpec) -> pd.DataFrame:
    if not spec.symbol:
        raise AdapterError("symbol required", reason="no_data")
    start, end = _require_dates(spec)
    source_id = spec.extra.get("_source_id", "yahoo")
    try:
        from backtest.loaders.registry import LOADER_REGISTRY, _ensure_registered

        _ensure_registered()
        loader_cls = LOADER_REGISTRY.get(source_id)
        if loader_cls is None:
            raise AdapterError(f"loader {source_id} not registered", reason="not_configured")
        loader = loader_cls()
        if not loader.is_available():
            raise AdapterError(f"{source_id} not configured", reason="not_configured")
        result = loader.fetch([spec.symbol], start, end)
        frame = result.get(spec.symbol)
        if frame is None or frame.empty:
            raise AdapterError(f"{source_id} no data", reason="no_data")
        return frame.reset_index() if frame.index.name == "trade_date" else frame
    except Exception as exc:
        from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted, TieredApiNotConfiguredError

        if isinstance(exc, TieredApiBudgetExhausted):
            raise AdapterError(str(exc), reason="budget_exhausted") from exc
        if isinstance(exc, TieredApiNotConfiguredError):
            raise AdapterError(str(exc), reason="not_configured") from exc
        raise AdapterError(str(exc), reason="error") from exc


def _fetch_alpha_vantage(spec: FetchSpec) -> pd.DataFrame:
    if not spec.symbol:
        raise AdapterError("symbol required", reason="no_data")
    start, end = _require_dates(spec)
    try:
        from tradingagents.dataflows.alpha_vantage_stock import get_stock

        raw = get_stock(spec.symbol, start, end)
        if isinstance(raw, pd.DataFrame):
            return raw
        if isinstance(raw, str) and raw.strip():
            from io import StringIO

            return pd.read_csv(StringIO(raw))
    except Exception as exc:
        from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted

        if isinstance(exc, TieredApiBudgetExhausted):
            raise AdapterError(str(exc), reason="budget_exhausted") from exc
        raise AdapterError(str(exc), reason="error") from exc
    raise AdapterError("alpha_vantage no data", reason="no_data")


def _fetch_eod_historical(spec: FetchSpec) -> pd.DataFrame:
    if not spec.symbol:
        raise AdapterError("symbol required", reason="no_data")
    start, end = _require_dates(spec)
    try:
        from trade_integrations.tiered_api.sources.eod_historical import get_eod_historical_daily

        rows = get_eod_historical_daily(
            spec.symbol,
            exchange=spec.extra.get("exchange", "NSE"),
            start=start,
            end=end,
        )
        if not rows:
            raise AdapterError("eod_historical no data", reason="no_data")
        frame = pd.DataFrame(rows)
        rename = {"date": "date", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
        for col in rename:
            if col not in frame.columns and col.capitalize() in frame.columns:
                frame = frame.rename(columns={col.capitalize(): col})
        return frame
    except AdapterError:
        raise
    except Exception as exc:
        from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted

        if isinstance(exc, TieredApiBudgetExhausted):
            raise AdapterError(str(exc), reason="budget_exhausted") from exc
        raise AdapterError(str(exc), reason="error") from exc
