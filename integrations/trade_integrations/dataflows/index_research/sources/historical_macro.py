"""Bulk macro + NIFTY OHLCV backfill for cold-tier history storage."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from trade_integrations.hub_storage.parquet_io import combine_first_numeric
from trade_integrations.dataflows.index_research.history_store import save_history_dataset
from trade_integrations.dataflows.index_research.technical_features import enrich_nifty_technical_columns

logger = logging.getLogger(__name__)

_YFINANCE_FACTORS: dict[str, str] = {
    "oil_brent": "BZ=F",
    "oil_wti": "CL=F",
    "usd_inr": "INR=X",
    "gold": "GC=F",
    "sp500": "^GSPC",
}

_FRED_SERIES: dict[str, str] = {
    "oil_brent": "DCOILBRENTEU",
    "oil_wti": "DCOILWTICO",
    "us_10y": "DGS10",
}


def _fetch_yfinance_ohlcv(symbol: str, start: str, end_exclusive: str) -> pd.DataFrame:
    import yfinance as yf

    hist = yf.Ticker(symbol).history(start=start, end=end_exclusive, auto_adjust=True)
    if hist.empty:
        return pd.DataFrame()
    frame = hist.reset_index()
    date_col = "Date" if "Date" in frame.columns else frame.columns[0]
    frame["date"] = pd.to_datetime(frame[date_col]).dt.strftime("%Y-%m-%d")
    out = frame[["date"]].copy()
    for src, dst in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume")):
        if src in frame.columns:
            out[dst] = frame[src].astype(float)
    return out.sort_values("date").reset_index(drop=True)


def _fetch_yfinance_close_series(symbol: str, start: str, end_exclusive: str) -> pd.Series:
    frame = _fetch_yfinance_ohlcv(symbol, start, end_exclusive)
    if frame.empty or "close" not in frame.columns:
        return pd.Series(dtype=float)
    return pd.Series(frame["close"].astype(float).values, index=frame["date"], name=symbol)


def _fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return pd.Series(dtype=float)
    try:
        from trade_integrations.http import get

        response = get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
                "observation_end": end,
                "sort_order": "asc",
            },
            timeout=45,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except Exception as exc:
        logger.debug("FRED %s fetch failed: %s", series_id, exc)
        return pd.Series(dtype=float)

    values: dict[str, float] = {}
    for obs in observations:
        raw = obs.get("value")
        day = str(obs.get("date") or "")[:10]
        if not day or raw in (".", None, ""):
            continue
        try:
            values[day] = float(raw)
        except (TypeError, ValueError):
            continue
    if not values:
        return pd.Series(dtype=float)
    return pd.Series(values)


def _macro_frame_from_nifty(
    nifty: pd.DataFrame,
    *,
    start: str,
    end_day: str,
) -> pd.DataFrame:
    end_exclusive = (
        datetime.strptime(end_day, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    macro = nifty[["date", "close"]].rename(columns={"close": "nifty_close"}).copy()
    for col in ("open", "high", "low", "volume"):
        if col in nifty.columns:
            macro[col] = nifty[col]

    for factor, symbol in _YFINANCE_FACTORS.items():
        series = _fetch_yfinance_close_series(symbol, start, end_exclusive)
        if not series.empty:
            macro[factor] = macro["date"].map(series)

    for factor, series_id in _FRED_SERIES.items():
        if factor in macro.columns and macro[factor].notna().any():
            continue
        series = _fetch_fred_series(series_id, start, end_day)
        if not series.empty:
            mapped = macro["date"].map(series)
            if factor in macro.columns:
                macro[factor] = combine_first_numeric(mapped, macro[factor])
            else:
                macro[factor] = mapped
            macro[factor] = macro[factor].ffill()

    us10y = _fetch_fred_series("DGS10", start, end_day)
    if not us10y.empty:
        macro["us_10y"] = macro["date"].map(us10y).ffill()

    technical = enrich_nifty_technical_columns(
        macro.rename(columns={"nifty_close": "close"})[["date", "close"]].copy()
    )
    for col in technical.columns:
        if col not in {"date", "close"}:
            macro[col] = technical[col]
    return macro


def build_macro_daily_tail_frame(*, start: str, end: str) -> pd.DataFrame:
    """Build macro columns for a date window using cold-tier Nifty OHLCV when available."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    end_day = end[:10]
    end_exclusive = (
        datetime.strptime(end_day, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    nifty = load_history_dataset("nifty_ohlcv_daily")
    if not nifty.empty:
        nifty = nifty.copy()
        nifty["date"] = nifty["date"].astype(str).str[:10]
        nifty = nifty[(nifty["date"] >= start[:10]) & (nifty["date"] <= end_day)]
    if nifty.empty or "close" not in nifty.columns:
        nifty = _fetch_yfinance_ohlcv("^NSEI", start, end_exclusive)
    if nifty.empty:
        return pd.DataFrame()
    return _macro_frame_from_nifty(nifty, start=start, end_day=end_day)


def backfill_macro_history(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Fetch macro + NIFTY OHLCV and write cold-tier parquets."""
    end_day = (end or datetime.now(timezone.utc).date().isoformat())[:10]
    end_exclusive = (
        datetime.strptime(end_day, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    nifty = _fetch_yfinance_ohlcv("^NSEI", start, end_exclusive)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history", "start": start, "end": end_day}

    macro = _macro_frame_from_nifty(nifty, start=start, end_day=end_day)

    coverage = {
        col: round(float(macro[col].notna().mean()) * 100.0, 1)
        for col in macro.columns
        if col != "date"
    }

    if dry_run:
        return {
            "status": "dry_run",
            "rows": len(macro),
            "start": str(macro["date"].iloc[0]),
            "end": str(macro["date"].iloc[-1]),
            "coverage_pct": coverage,
        }

    nifty_out = nifty.copy()
    macro_out = macro.drop(columns=[c for c in ("open", "high", "low", "volume") if c in macro.columns], errors="ignore")

    nifty_result = save_history_dataset("nifty_ohlcv_daily", nifty_out)
    macro_result = save_history_dataset("macro_daily", macro_out)
    return {
        "status": "ok",
        "rows": len(macro),
        "start": str(macro["date"].iloc[0]),
        "end": str(macro["date"].iloc[-1]),
        "coverage_pct": coverage,
        "nifty_ohlcv": nifty_result,
        "macro_daily": macro_result,
    }
