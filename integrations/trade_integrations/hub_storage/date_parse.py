"""Explicit NSE/historic date parsing without pandas format-inference warnings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

NSE_DATE_FORMATS: tuple[str, ...] = (
    "%d-%b-%y",
    "%d-%b-%Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d_%m_%y",
    "%b. %d, %Y",
)


def parse_date_scalar(raw: Any) -> str | None:
    """Parse one NSE/historic date value to ISO ``YYYY-MM-DD``."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in NSE_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", format="mixed")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def parse_date_series(series: pd.Series, *, dayfirst: bool = False) -> pd.Series:
    """Parse a date column using explicit formats; ``format='mixed'`` only for leftovers."""
    if series.empty:
        return pd.Series(dtype="datetime64[ns]")

    text = series.astype(str).str.strip()
    text = text.where(text.ne("") & text.ne("nan") & text.ne("NaT"), other=pd.NA)
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    for fmt in NSE_DATE_FORMATS:
        missing = parsed.isna()
        if not missing.any():
            break
        attempt = pd.to_datetime(text.loc[missing], format=fmt, errors="coerce")
        parsed.loc[missing] = attempt

    missing = parsed.isna()
    if missing.any():
        fallback = pd.to_datetime(text.loc[missing], errors="coerce", format="mixed")
        if dayfirst:
            still_missing = fallback.isna()
            if still_missing.any():
                fallback.loc[still_missing] = pd.to_datetime(
                    text.loc[missing].loc[still_missing],
                    errors="coerce",
                    dayfirst=True,
                    format="mixed",
                )
        parsed.loc[missing] = fallback

    return parsed


def format_datetime_series(series: pd.Series, *, utc: bool = False) -> pd.Series:
    """Parse datetimes and format as ``YYYY-MM-DD HH:MM:SS`` strings."""
    if utc:
        parsed = pd.to_datetime(series, errors="coerce", utc=True, format="mixed").dt.tz_convert(None)
    else:
        parsed = parse_date_series(series)
    return parsed.dt.strftime("%Y-%m-%d %H:%M:%S")


def format_date_series(series: pd.Series, *, dayfirst: bool = False) -> pd.Series:
    """Parse and format a date column as ``YYYY-MM-DD`` strings."""
    return parse_date_series(series, dayfirst=dayfirst).dt.strftime("%Y-%m-%d")
