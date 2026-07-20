"""Safe nselib wrappers — explicit date ranges only (never bare period= calls to NSE)."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.date_parse import format_date_series, parse_date_scalar, parse_date_series
from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

from trade_integrations.dataflows import source_availability

logger = logging.getLogger(__name__)

_NSELIB_VENDOR = "nselib"
_NSELIB_DATE = "%d-%m-%Y"
_ISO_DATE = "%Y-%m-%d"


def iso_to_nselib(day: str) -> str:
    return datetime.strptime(day[:10], _ISO_DATE).strftime(_NSELIB_DATE)


def period_to_iso_range(period: str, *, end: date | None = None) -> tuple[str, str]:
    """Map nselib period codes to ISO dates locally (avoids broken NSE period endpoints)."""
    end_d = end or date.today()
    mapping = {
        "1D": 1,
        "1W": 7,
        "1M": 30,
        "6M": 182,
        "1Y": 365,
    }
    days = mapping.get(period.upper())
    if days is None:
        raise ValueError(f"unsupported nselib period: {period}")
    start_d = end_d - timedelta(days=days)
    return start_d.isoformat(), end_d.isoformat()


def normalize_index_ohlcv_frame(raw: pd.DataFrame, *, index_slug: str = "nifty50") -> pd.DataFrame:
    """Normalize nselib index_data columns to hub OHLCV schema."""
    if raw is None or raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().upper(): c for c in raw.columns}
    date_col = cols.get("TIMESTAMP") or cols.get("DATE")
    if date_col is None:
        return pd.DataFrame()

    rename = {}
    for src_key, dst in (
        ("OPEN_INDEX_VAL", "open"),
        ("HIGH_INDEX_VAL", "high"),
        ("LOW_INDEX_VAL", "low"),
        ("CLOSE_INDEX_VAL", "close"),
        ("TRADED_QTY", "volume"),
        ("TURN_OVER", "turnover_cr"),
    ):
        if src_key in cols:
            rename[cols[src_key]] = dst

    out = raw.rename(columns=rename)
    out["date"] = format_date_series(raw[date_col], dayfirst=True)
    out = out.dropna(subset=["date"])
    for col in ("open", "high", "low", "close", "volume", "turnover_cr"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out["index_slug"] = index_slug
    out["granularity"] = "daily"
    out["source"] = "nselib_index_data"
    keep = [
        c
        for c in (
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover_cr",
            "index_slug",
            "granularity",
            "source",
        )
        if c in out.columns
    ]
    return out[keep].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_index_data_range(
    index: str,
    start: str,
    end: str,
    *,
    index_slug: str = "nifty50",
    chunk_days: int = 90,
    sleep_seconds: float = 0.3,
) -> pd.DataFrame:
    """Fetch index OHLCV via nselib using explicit from/to chunks (period= avoided)."""
    capability = "index_data"
    if not source_availability.should_attempt(_NSELIB_VENDOR, capability):
        return pd.DataFrame()

    try:
        from nselib import capital_market
    except ImportError as exc:
        source_availability.record_failure(_NSELIB_VENDOR, capability, exc)
        return pd.DataFrame()

    start_d = datetime.strptime(start[:10], _ISO_DATE).date()
    end_d = datetime.strptime(end[:10], _ISO_DATE).date()
    if start_d > end_d:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    cursor = start_d
    while cursor <= end_d:
        chunk_end = min(cursor + timedelta(days=max(chunk_days - 1, 1)), end_d)
        try:
            raw = capital_market.index_data(
                index=index,
                from_date=cursor.strftime(_NSELIB_DATE),
                to_date=chunk_end.strftime(_NSELIB_DATE),
            )
            normalized = normalize_index_ohlcv_frame(raw, index_slug=index_slug)
            if not normalized.empty:
                frames.append(normalized)
        except Exception as exc:
            logger.debug(
                "nselib index_data failed %s %s-%s: %s",
                index,
                cursor,
                chunk_end,
                exc,
            )
        cursor = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if not frames:
        source_availability.record_failure(_NSELIB_VENDOR, capability, "empty index_data result")
        return pd.DataFrame()
    combined = concat_frames(frames)
    result = combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    source_availability.record_success(_NSELIB_VENDOR, capability)
    return result


def fetch_index_data_period(index: str, period: str, *, index_slug: str = "nifty50", **kwargs: Any) -> pd.DataFrame:
    """Safe replacement for index_data(..., period='1Y') — converts period locally first."""
    start, end = period_to_iso_range(period)
    return fetch_index_data_range(index, start, end, index_slug=index_slug, **kwargs)


def fetch_india_vix_range(start: str, end: str, *, sleep_seconds: float = 0.25) -> pd.DataFrame:
    """Fetch India VIX history with explicit dates (no period=)."""
    capability = "india_vix_data"
    if not source_availability.should_attempt(_NSELIB_VENDOR, capability):
        return pd.DataFrame()

    try:
        from nselib import capital_market
    except ImportError as exc:
        source_availability.record_failure(_NSELIB_VENDOR, capability, exc)
        return pd.DataFrame()

    try:
        raw = capital_market.india_vix_data(
            from_date=iso_to_nselib(start),
            to_date=iso_to_nselib(end),
        )
    except Exception as exc:
        source_availability.record_failure(_NSELIB_VENDOR, capability, exc)
        logger.debug("nselib india_vix_data failed %s-%s: %s", start, end, exc)
        return pd.DataFrame()

    if raw is None or raw.empty:
        source_availability.record_failure(_NSELIB_VENDOR, capability, "empty india_vix_data frame")
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = next((c for c in raw.columns if "date" in str(c).lower()), raw.columns[0])
    value_col = next(
        (c for c in raw.columns if "close" in str(c).lower() or "vix" in str(c).lower()),
        raw.columns[-1],
    )
    out = pd.DataFrame(
        {
            "date": format_date_series(raw[date_col], dayfirst=True),
            "india_vix": pd.to_numeric(raw[value_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["india_vix"])
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    result = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    source_availability.record_success(_NSELIB_VENDOR, capability)
    return result


def backfill_nifty50_ohlcv_gaps(
    repo_root: Any,
    *,
    allow_live_fetch: bool = True,
    max_gap_days: int = 120,
) -> dict[str, Any]:
    """Extend nifty50_ohlcv_daily parquet from nselib when local CSV ends before today."""
    from trade_integrations.nse_browser.parsers.historic_data import (
        _dataset_path,
        _merge_index_ohlcv,
        _read_dataset,
        _write_dataset,
    )

    path = _dataset_path(repo_root, "nifty50_ohlcv_daily")
    existing = _read_dataset(path)
    if existing.empty or "date" not in existing.columns:
        start = (date.today() - timedelta(days=max_gap_days)).isoformat()
    else:
        last = str(existing["date"].max())[:10]
        start = (datetime.strptime(last, _ISO_DATE).date() + timedelta(days=1)).isoformat()

    end = date.today().isoformat()
    if start > end:
        return {"status": "skipped", "reason": "already_current", "end": str(existing["date"].max())[:10]}

    if not allow_live_fetch:
        return {"status": "skipped", "reason": "live_fetch_disabled", "would_start": start, "would_end": end}

    incoming = fetch_index_data_range("NIFTY 50", start, end, index_slug="nifty50")
    if incoming.empty:
        return {"status": "error", "reason": "nselib_empty", "start": start, "end": end}

    merged = _merge_index_ohlcv(existing, incoming)
    _write_dataset(merged, path)
    return {
        "status": "ok",
        "rows_added": len(incoming),
        "total_rows": len(merged),
        "start": str(merged["date"].iloc[0]),
        "end": str(merged["date"].iloc[-1]),
        "path": str(path),
    }
