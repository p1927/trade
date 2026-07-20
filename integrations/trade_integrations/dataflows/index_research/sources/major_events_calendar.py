"""Curated major market events calendar for historical news features."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from trade_integrations.dataflows.index_research.history_store import save_history_dataset

_CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "major_events_calendar.yaml"


def calendar_yaml_path() -> Path:
    return _CALENDAR_PATH


def load_major_events_calendar(path: Path | None = None) -> pd.DataFrame:
    """Load curated events from bundled YAML."""
    src = path or _CALENDAR_PATH
    if not src.is_file():
        return pd.DataFrame(columns=["date", "event_type", "topic", "severity", "description"])
    payload = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    rows = payload.get("events") or []
    if not rows:
        return pd.DataFrame(columns=["date", "event_type", "topic", "severity", "description"])
    frame = pd.DataFrame(rows)
    frame["date"] = frame["date"].astype(str).str[:10]
    for col in ("event_type", "topic", "description"):
        if col in frame.columns:
            frame[col] = frame[col].astype(str)
    if "severity" in frame.columns:
        frame["severity"] = pd.to_numeric(frame["severity"], errors="coerce").fillna(3).astype(int)
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def save_major_events_parquet(path: Path | None = None) -> dict[str, Any]:
    frame = load_major_events_calendar(path)
    return save_history_dataset("major_events", frame)


def daily_topic_counts_from_calendar(
    trading_dates: list[str],
    *,
    path: Path | None = None,
) -> pd.DataFrame:
    """Map curated events to daily raw topic counts (not rolling)."""
    events = load_major_events_calendar(path)
    if not trading_dates:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    event_by_day = {str(row["date"])[:10]: row for _, row in events.iterrows()}
    for day in sorted(trading_dates):
        row: dict[str, Any] = {"date": day[:10]}
        for topic in ("war", "oil", "fii", "rbi", "us_markets"):
            row[f"topic_{topic}"] = 0.0
        hit = event_by_day.get(day[:10])
        if hit is not None:
            topic = str(hit.get("topic") or "index_sentiment")
            key = f"topic_{topic}"
            if key in row:
                row[key] = float(hit.get("severity") or 1)
            row["news_material_raw"] = float(hit.get("severity") or 1)
            row["news_surprise_raw"] = 1.0 if int(hit.get("severity") or 0) >= 4 else 0.0
        else:
            row["news_material_raw"] = 0.0
            row["news_surprise_raw"] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)
