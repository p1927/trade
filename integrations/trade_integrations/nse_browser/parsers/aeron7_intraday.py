"""Aggregate Aeron7 NIFTY index futures intraday text files into daily F&O proxies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

_AERON7_ROOT_NAMES: tuple[str, ...] = (
    "aeron7_nifty_intraday",
    "nifty-banknifty-intraday-data",
)


def aeron7_intraday_roots(historic_root: Path) -> list[Path]:
    """Return candidate local clone paths for the Aeron7 GitHub dataset."""
    roots: list[Path] = []
    for name in _AERON7_ROOT_NAMES:
        path = historic_root / name
        if path.is_dir():
            roots.append(path)
    return roots


def _parse_aeron7_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 7:
        return None
    symbol = parts[0].upper()
    if symbol not in {"NIFTY_F1", "NIFTY_F2"}:
        return None
    try:
        day = datetime.strptime(parts[1], "%Y%m%d").date().isoformat()
        volume = float(parts[-1])
    except (ValueError, TypeError):
        return None
    if volume <= 0:
        return None
    return {
        "date": day,
        "symbol": symbol,
        "time": parts[2],
        "volume": volume,
    }


def _last_bar_volume(path: Path) -> dict[str, float]:
    last_by_day: dict[str, tuple[str, float]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        parsed = _parse_aeron7_line(line)
        if parsed is None:
            continue
        day = str(parsed["date"])
        time_key = str(parsed["time"])
        volume = float(parsed["volume"])
        prev = last_by_day.get(day)
        if prev is None or time_key >= prev[0]:
            last_by_day[day] = (time_key, volume)
    return {day: values[1] for day, values in last_by_day.items()}


def aggregate_aeron7_nifty_futures(root: Path) -> pd.DataFrame:
    """
    Build daily index futures positioning proxies from local Aeron7 clone.

    ``fii_idx_fut_long`` / ``fii_idx_fut_short`` use front-month (F1) and
    next-month (F2) end-of-session volumes; ratio fills ``fii_fut_long_short_ratio``.
    """
    if not root.is_dir():
        return pd.DataFrame()

    f1_by_day: dict[str, float] = {}
    f2_by_day: dict[str, float] = {}
    for path in root.rglob("NIFTY_F1.txt"):
        f1_by_day.update(_last_bar_volume(path))
    for path in root.rglob("NIFTY_F2.txt"):
        f2_by_day.update(_last_bar_volume(path))

    days = sorted(set(f1_by_day) | set(f2_by_day))
    rows: list[dict[str, Any]] = []
    for day in days:
        f1 = f1_by_day.get(day)
        f2 = f2_by_day.get(day)
        row: dict[str, Any] = {"date": day}
        if f1 is not None:
            row["fii_idx_fut_long"] = float(f1)
        if f2 is not None:
            row["fii_idx_fut_short"] = float(f2)
        if f1 and f2 and f2 > 0:
            row["fii_fut_long_short_ratio"] = float(f1) / float(f2)
        if len(row) > 1:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    frame["source"] = "historic_data_aeron7_futures"
    frame["granularity"] = "daily"
    frame["variant"] = "derivatives"
    return frame.reset_index(drop=True)
