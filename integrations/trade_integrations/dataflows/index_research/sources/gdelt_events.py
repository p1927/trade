"""GDELT event ingest for historical news topic counts (optional BigQuery or daily files)."""

from __future__ import annotations

import logging
import os
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_GDELT_DAILY_URL = "http://data.gdeltproject.org/events/{yyyymmdd}.export.CSV.zip"

_TOPIC_THEME_MAP: dict[str, tuple[str, ...]] = {
    "war": ("WAR", "MIL", "TERR", "ARMEDCONFLICT", "REBELLION"),
    "oil": ("OIL", "ENERGY", "PETROLEUM", "GAS"),
    "rbi": ("CENTRALBANK", "RESERVEBANK", "MONPOL", "INTERESTRATE"),
    "fii": ("FOREIGNINVEST", "CAPITALFLOW", "STOCKMARKET", "ECON"),
    "us_markets": ("UNITEDSTATES", "FEDERALRESERVE", "WALLSTREET", "NASDAQ"),
}


def _parse_gdelt_day(raw: str) -> str | None:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


def _topic_from_themes(theme_str: str) -> str | None:
    upper = str(theme_str or "").upper()
    for topic, markers in _TOPIC_THEME_MAP.items():
        if any(marker in upper for marker in markers):
            return topic
    return None


def _fetch_gdelt_daily_file(day: str) -> pd.DataFrame:
    """Download one GDELT daily export and return India/macro-relevant rows."""
    try:
        import requests
    except ImportError:
        return pd.DataFrame()

    yyyymmdd = day.replace("-", "")
    url = _GDELT_DAILY_URL.format(yyyymmdd=yyyymmdd)
    try:
        response = requests.get(url, timeout=60)
        if response.status_code == 404:
            return pd.DataFrame()
        response.raise_for_status()
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            if not names:
                return pd.DataFrame()
            with archive.open(names[0]) as handle:
                frame = pd.read_csv(handle, sep="\t", header=None, low_memory=False)
    except Exception as exc:
        logger.debug("GDELT daily %s failed: %s", day, exc)
        return pd.DataFrame()

    if frame.empty or frame.shape[1] < 58:
        return pd.DataFrame()

    # GDELT 1.0 export column indices (0-based): 51=ActionGeo_CountryCode, 53=AvgTone, 57=GoldsteinScale
    country = frame.iloc[:, 51].astype(str)
    themes = frame.iloc[:, 26].astype(str) if frame.shape[1] > 26 else pd.Series([""] * len(frame))
    mask = country.str.upper().eq("IN") | themes.str.contains("INDIA", case=False, na=False)
    subset = frame.loc[mask].copy()
    if subset.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, item in subset.iterrows():
        topic = _topic_from_themes(str(item.iloc[26] if len(item) > 26 else ""))
        if not topic:
            continue
        rows.append(
            {
                "date": day,
                "topic": topic,
                "count": 1.0,
                "avg_tone": float(item.iloc[34]) if len(item) > 34 else 0.0,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def fetch_gdelt_topic_counts(
    *,
    start: str,
    end: str,
    max_days: int | None = None,
    sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Aggregate GDELT daily files into per-day topic counts."""
    if os.getenv("GDELT_DISABLE", "").strip() in {"1", "true", "yes"}:
        return pd.DataFrame()

    start_d = date.fromisoformat(start[:10])
    end_d = date.fromisoformat(end[:10])
    days: list[str] = []
    cursor = start_d
    while cursor <= end_d:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    if max_days is not None:
        days = days[: max(0, max_days)]

    frames: list[pd.DataFrame] = []
    import time

    for idx, day in enumerate(days):
        part = _fetch_gdelt_daily_file(day)
        if not part.empty:
            frames.append(part)
        if sleep_s > 0 and idx % 10 == 9:
            time.sleep(sleep_s)

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    agg = (
        combined.groupby(["date", "topic"], as_index=False)["count"]
        .sum()
        .pivot(index="date", columns="topic", values="count")
        .fillna(0.0)
        .reset_index()
    )
    agg["date"] = agg["date"].astype(str).str[:10]
    return agg.sort_values("date").reset_index(drop=True)


def fetch_gdelt_bigquery_counts(*, start: str, end: str) -> pd.DataFrame:
    """Optional BigQuery path when GOOGLE_CLOUD_PROJECT is configured."""
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        return pd.DataFrame()
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.debug("google-cloud-bigquery not installed")
        return pd.DataFrame()

    client = bigquery.Client(project=project)
    query = f"""
    SELECT
      DATE(PARSE_DATE('%Y%m%d', CAST(SQLDATE AS STRING))) AS day,
      COUNTIF(LOWER(Actor1CountryCode) = 'ind' OR LOWER(Actor2CountryCode) = 'ind') AS india_events
    FROM `gdelt-bq.gdeltv2.events`
    WHERE SQLDATE BETWEEN {start.replace('-', '')} AND {end.replace('-', '')}
    GROUP BY day
    ORDER BY day
    """
    try:
        frame = client.query(query).to_dataframe()
    except Exception as exc:
        logger.debug("GDELT BigQuery failed: %s", exc)
        return pd.DataFrame()
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["day"]).dt.strftime("%Y-%m-%d")
    frame["topic_fii"] = frame["india_events"].astype(float)
    return frame[["date", "topic_fii"]]
