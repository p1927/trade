"""Backfill daily macro factor snapshots from yfinance/FRED historical series."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from trade_integrations.dataflows.index_research.calendar_features import (
    calendar_factor_dict,
)
from trade_integrations.dataflows.index_research.factor_store import (
    get_factor_data_dir,
    save_daily_factors,
)
from trade_integrations.dataflows.index_research.sources.history_loader import (
    load_nifty_history,
)
from trade_integrations.dataflows.index_research.technical_features import (
    enrich_nifty_technical_columns,
)

logger = logging.getLogger(__name__)

_YFINANCE_FACTORS: dict[str, str] = {
    "oil_brent": "BZ=F",
    "oil_wti": "CL=F",
    "usd_inr": "INR=X",
    "gold": "GC=F",
    "sp500": "^GSPC",
    "india_vix": "^INDIAVIX",
}

_MIN_BACKFILL_ROWS = 30


from trade_integrations.dataflows.index_research.sources.rbi_repo_schedule import repo_rate_on


def _fetch_yfinance_close_series(symbol: str, start: str, end: str) -> pd.Series:
    import yfinance as yf

    hist = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
    if hist.empty:
        return pd.Series(dtype=float)
    frame = hist.reset_index()
    date_col = "Date" if "Date" in frame.columns else frame.columns[0]
    close_col = "Close" if "Close" in frame.columns else "close"
    dates = pd.to_datetime(frame[date_col]).dt.strftime("%Y-%m-%d")
    return pd.Series(frame[close_col].astype(float).values, index=dates, name=symbol)


def _fetch_fred_dgs10_series(start: str, end: str) -> pd.Series:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return pd.Series(dtype=float)
    try:
        import requests

        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DGS10",
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
                "observation_end": end,
                "sort_order": "asc",
            },
            timeout=30,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
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
    except Exception as exc:
        logger.debug("FRED DGS10 backfill failed: %s", exc)
        return pd.Series(dtype=float)


def _count_existing_snapshots() -> int:
    out_dir = get_factor_data_dir()
    if not out_dir.is_dir():
        return 0
    count = 0
    for path in out_dir.iterdir():
        if path.suffix in {".parquet", ".csv"} and path.stem[:4].isdigit():
            count += 1
    return count


def backfill_factor_history(*, days: int = 365, start: str | None = None) -> dict[str, int | str]:
    """Write daily macro factor snapshots for Nifty trading days via historical prices."""
    nifty = load_nifty_history(days=days, start=start)
    if nifty.empty:
        return {"days_written": 0, "reason": "no_nifty_history"}

    start = str(nifty["date"].iloc[0])
    end = str(nifty["date"].iloc[-1])
    end_exclusive = (
        datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    frame = nifty[["date", "close"]].copy()
    for factor, symbol in _YFINANCE_FACTORS.items():
        series = _fetch_yfinance_close_series(symbol, start, end_exclusive)
        if series.empty:
            continue
        frame[factor] = frame["date"].map(series)

    us10y = _fetch_fred_dgs10_series(start, end)
    if not us10y.empty:
        frame["us_10y"] = frame["date"].map(us10y).ffill()

    repo_rate = repo_rate_on(end)
    frame["repo_rate"] = frame["date"].map(lambda d: repo_rate_on(str(d)))
    frame = enrich_nifty_technical_columns(frame)

    technical_keys = (
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_realized_vol_20d",
        "nifty_ma20_distance_pct",
    )

    days_written = 0
    for _, row in frame.iterrows():
        day = str(row["date"])
        rows: list[dict] = []
        for factor in list(_YFINANCE_FACTORS) + ["us_10y", "repo_rate"]:
            if factor not in row.index:
                continue
            value = row[factor]
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            rows.append(
                {
                    "factor": factor,
                    "value": float(value),
                    "source": "backfill_rbi_schedule",
                }
            )
        for factor in technical_keys:
            if factor not in row.index:
                continue
            value = row[factor]
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            rows.append(
                {
                    "factor": factor,
                    "value": float(value),
                    "source": "backfill_technical",
                }
            )
        try:
            cal = calendar_factor_dict(date.fromisoformat(day))
        except ValueError:
            cal = {}
        for factor, value in cal.items():
            rows.append(
                {
                    "factor": factor,
                    "value": float(value),
                    "source": "backfill_calendar",
                }
            )
        if rows:
            save_daily_factors(day, rows)
            days_written += 1

    enrichment: dict[str, int | str] = {}
    try:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        enrichment = enrich_factor_history(days=days if not start else max(days, 5000))
    except Exception as exc:
        logger.warning("factor enrichment failed: %s", exc)

    return {
        "days_written": days_written,
        "start": start,
        "end": end,
        "repo_rate": repo_rate,
        "enrichment": enrichment,
    }


def ensure_factor_history(*, days: int = 365, min_rows: int = _MIN_BACKFILL_ROWS) -> dict:
    """Backfill factor store when fewer than ``min_rows`` daily snapshots exist."""
    existing = _count_existing_snapshots()
    if existing >= min_rows:
        enrichment: dict[str, int | str] = {}
        try:
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            enrichment = enrich_factor_history(days=days)
        except Exception as exc:
            logger.warning("factor enrichment failed: %s", exc)
        return {
            "backfilled": False,
            "existing_snapshots": existing,
            "enrichment": enrichment,
        }
    summary = backfill_factor_history(days=days)
    summary["backfilled"] = True
    summary["existing_snapshots"] = existing
    return summary


def backfill_if_needed(*, days: int = 365, min_rows: int = _MIN_BACKFILL_ROWS) -> dict:
    """Alias used by calibration runner."""
    return ensure_factor_history(days=days, min_rows=min_rows)
