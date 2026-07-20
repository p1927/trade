"""Build rolling news event features from curated calendar + optional GDELT."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import load_history_dataset, save_history_dataset
from trade_integrations.dataflows.index_research.news_event_features import NEWS_EVENT_FACTOR_KEYS
from trade_integrations.dataflows.index_research.sources.gdelt_events import (
    fetch_gdelt_bigquery_counts,
    fetch_gdelt_topic_counts,
)
from trade_integrations.dataflows.index_research.sources.major_events_calendar import (
    daily_topic_counts_from_calendar,
    save_major_events_parquet,
)

_LOOKBACK = 7
_TOPIC_MAP = {
    "war": "news_war_7d",
    "oil": "news_oil_7d",
    "fii": "news_fii_7d",
    "rbi": "news_rbi_7d",
    "us_markets": "news_fii_7d",
}


def _rolling_sum(series: pd.Series, window: int = _LOOKBACK) -> pd.Series:
    return series.fillna(0.0).rolling(window=window, min_periods=1).sum()


def build_news_events_daily(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    use_gdelt: bool = True,
    gdelt_max_days: int | None = 120,
) -> pd.DataFrame:
    """Merge curated calendar + optional GDELT into daily news feature columns."""
    save_major_events_parquet()
    nifty = load_history_dataset("nifty_ohlcv_daily")
    if nifty.empty:
        macro = load_history_dataset("macro_daily")
        if macro.empty:
            return pd.DataFrame()
        trading_dates = macro["date"].astype(str).tolist()
    else:
        trading_dates = nifty["date"].astype(str).tolist()

    if start:
        trading_dates = [d for d in trading_dates if d >= start[:10]]
    if end:
        trading_dates = [d for d in trading_dates if d <= end[:10]]
    if not trading_dates:
        return pd.DataFrame()

    calendar_daily = daily_topic_counts_from_calendar(trading_dates)
    frame = calendar_daily.copy()

    gdelt = pd.DataFrame()
    if use_gdelt:
        end_day = end or trading_dates[-1]
        gdelt = fetch_gdelt_bigquery_counts(start=start, end=end_day)
        if gdelt.empty and gdelt_max_days:
            gdelt = fetch_gdelt_topic_counts(start=start, end=end_day, max_days=gdelt_max_days)

    if not gdelt.empty:
        gdelt["date"] = gdelt["date"].astype(str).str[:10]
        frame = frame.merge(gdelt, on="date", how="left", suffixes=("", "_gdelt"))
        for topic in _TOPIC_MAP:
            col = f"topic_{topic}"
            gdelt_col = col if col in gdelt.columns else None
            if gdelt_col and gdelt_col in frame.columns:
                frame[col] = frame[col].fillna(0.0) + frame[gdelt_col].fillna(0.0)

    for topic, feature in _TOPIC_MAP.items():
        raw_col = f"topic_{topic}"
        if raw_col not in frame.columns:
            frame[raw_col] = 0.0
        frame[feature] = _rolling_sum(frame[raw_col].astype(float))

    frame["news_material_7d"] = _rolling_sum(frame.get("news_material_raw", pd.Series(0.0, index=frame.index)))
    frame["news_surprise_7d"] = _rolling_sum(frame.get("news_surprise_raw", pd.Series(0.0, index=frame.index)))
    frame["news_crash_theme_7d"] = frame["news_war_7d"] + frame["news_fii_7d"] * 0.5
    frame["news_rally_theme_7d"] = 0.0
    frame["news_net_tone_7d"] = -frame["news_crash_theme_7d"]

    keep = ["date"] + [key for key in NEWS_EVENT_FACTOR_KEYS if key in frame.columns]
    return frame[keep].sort_values("date").reset_index(drop=True)


def backfill_news_history(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    use_gdelt: bool = True,
    gdelt_max_days: int | None = 120,
    dry_run: bool = False,
) -> dict[str, Any]:
    frame = build_news_events_daily(
        start=start,
        end=end,
        use_gdelt=use_gdelt,
        gdelt_max_days=gdelt_max_days,
    )
    if frame.empty:
        return {"status": "error", "reason": "no_trading_dates", "start": start, "end": end}
    nonzero = {
        col: int((frame[col].fillna(0) != 0).sum())
        for col in frame.columns
        if col != "date"
    }
    if dry_run:
        return {
            "status": "dry_run",
            "rows": len(frame),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
            "nonzero_days": nonzero,
        }
    result = save_history_dataset("news_events_daily", frame)
    return {"status": "ok", "rows": len(frame), "nonzero_days": nonzero, **result}
