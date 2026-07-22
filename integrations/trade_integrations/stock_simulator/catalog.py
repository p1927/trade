"""Load historic NSE bars and serve as-of replay snapshots."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

_INDEX_SYMBOLS: dict[tuple[str, str], str] = {
    ("NIFTY", "NSE_INDEX"): "nifty50",
    ("NIFTY 50", "NSE_INDEX"): "nifty50",
    ("NIFTY50", "NSE_INDEX"): "nifty50",
}

_INTRADAY_CSV = "nifty50_intraday_5min.csv"


def _normalize_naive_to_ist(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is not None:
        return ts.tz_convert(IST)
    # nifty50_intraday_5min.csv stores naive timestamps in UTC (03:45 UTC = 09:15 IST).
    return ts.tz_localize("UTC").tz_convert(IST)


class ReplayCatalog:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self._frames: dict[str, pd.DataFrame] = {}

    def available_dates(self, symbol: str, exchange: str) -> list[str]:
        frame = self._load_symbol(symbol, exchange)
        if frame.empty:
            return []
        days = frame["ts_ist"].dt.date.astype(str).unique().tolist()
        return sorted(days)

    def bar_at(self, symbol: str, exchange: str, sim_now: datetime) -> dict[str, float | int] | None:
        frame = self._load_symbol(symbol, exchange)
        if frame.empty:
            return None
        sim_ist = sim_now.astimezone(IST)
        day = sim_ist.date().isoformat()
        day_frame = frame[frame["day"] == day]
        if day_frame.empty:
            return None
        bucket = sim_ist.replace(second=0, microsecond=0)
        minute = bucket.minute - (bucket.minute % 5)
        bucket = bucket.replace(minute=minute)
        row = day_frame[day_frame["ts_ist"] <= bucket]
        if row.empty:
            return None
        hit = row.iloc[-1]
        prev = day_frame[day_frame["ts_ist"] < hit["ts_ist"]]
        prev_close = float(prev.iloc[-1]["close"]) if not prev.empty else float(hit["open"])
        return {
            "open": float(hit["open"]),
            "high": float(hit["high"]),
            "low": float(hit["low"]),
            "close": float(hit["close"]),
            "ltp": float(hit["close"]),
            "volume": int(hit.get("volume") or 0),
            "prev_close": prev_close,
            "bar_ts": hit["ts_ist"].isoformat(),
        }

    def _slug(self, symbol: str, exchange: str) -> str | None:
        key = (symbol.strip().upper(), exchange.strip().upper())
        return _INDEX_SYMBOLS.get(key)

    def _load_symbol(self, symbol: str, exchange: str) -> pd.DataFrame:
        slug = self._slug(symbol, exchange)
        if not slug:
            return pd.DataFrame()
        if slug in self._frames:
            return self._frames[slug]
        path = self.data_root / _INTRADAY_CSV
        if not path.is_file():
            self._frames[slug] = pd.DataFrame()
            return self._frames[slug]
        raw = pd.read_csv(path)
        raw["ts_ist"] = pd.to_datetime(raw["date"], errors="coerce").map(_normalize_naive_to_ist)
        raw = raw.dropna(subset=["ts_ist"])
        raw = raw.sort_values("ts_ist")
        raw["day"] = raw["ts_ist"].dt.date.astype(str)
        # NSE cash session in IST
        t = raw["ts_ist"].dt.time
        open_t = datetime.strptime("09:15", "%H:%M").time()
        close_t = datetime.strptime("15:30", "%H:%M").time()
        raw = raw[(t >= open_t) & (t <= close_t)]
        raw["bucket"] = raw["ts_ist"].dt.floor("5min")
        raw = raw.drop_duplicates(subset=["day", "bucket"], keep="first")
        raw = raw.drop(columns=["bucket"])
        self._frames[slug] = raw.reset_index(drop=True)
        return self._frames[slug]
