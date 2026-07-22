"""Load historic NSE bars and serve as-of replay snapshots."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trade_integrations.stock_simulator.hf_paths import index_parquet_path, index_slug

IST = ZoneInfo("Asia/Kolkata")

_LEGACY_INTRADAY_CSV = "nifty50_intraday_5min.csv"


def _normalize_naive_to_ist(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is not None:
        return ts.tz_convert(IST)
    return ts.tz_localize("UTC").tz_convert(IST)


def _bucket_ts(sim_ist: datetime, bar_minutes: int) -> datetime:
    bucket = sim_ist.replace(second=0, microsecond=0)
    if bar_minutes <= 1:
        return bucket
    minute = bucket.minute - (bucket.minute % bar_minutes)
    return bucket.replace(minute=minute)


class ReplayCatalog:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self._frames: dict[str, pd.DataFrame] = {}
        self._bar_minutes: dict[str, int] = {}
        self._legacy_nifty: pd.DataFrame | None = None

    def available_dates(self, symbol: str, exchange: str) -> list[str]:
        frame = self._load_symbol(symbol, exchange)
        if frame.empty:
            return []
        days = frame["ts_ist"].dt.date.astype(str).unique().tolist()
        return sorted(days)

    def bar_at(self, symbol: str, exchange: str, sim_now: datetime) -> dict[str, float | int] | None:
        slug = index_slug(symbol, exchange)
        if not slug:
            return None
        sim_ist = sim_now.astimezone(IST)
        day = sim_ist.date().isoformat()

        bar = self._bar_from_frame(self._load_symbol(symbol, exchange), sim_ist, day)
        if bar is not None:
            return bar

        if slug == "NIFTY":
            legacy = self._load_legacy_nifty_csv()
            return self._bar_from_frame(legacy, sim_ist, day, bar_minutes=5)
        return None

    def _bar_from_frame(
        self,
        frame: pd.DataFrame,
        sim_ist: datetime,
        day: str,
        *,
        bar_minutes: int | None = None,
    ) -> dict[str, float | int] | None:
        if frame.empty:
            return None
        day_frame = frame[frame["day"] == day]
        if day_frame.empty:
            return None
        minutes = bar_minutes or int(frame.attrs.get("bar_minutes", 1))
        bucket = _bucket_ts(sim_ist, minutes)
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

    def _load_symbol(self, symbol: str, exchange: str) -> pd.DataFrame:
        slug = index_slug(symbol, exchange)
        if not slug:
            return pd.DataFrame()
        if slug in self._frames:
            return self._frames[slug]

        hf_path = index_parquet_path(self.data_root, slug)
        if hf_path.is_file():
            raw = pd.read_parquet(hf_path)
            raw["ts_ist"] = pd.to_datetime(raw["timestamp"], errors="coerce")
            if raw["ts_ist"].dt.tz is None:
                raw["ts_ist"] = raw["ts_ist"].dt.tz_localize(IST)
            else:
                raw["ts_ist"] = raw["ts_ist"].dt.tz_convert(IST)
            raw = raw.dropna(subset=["ts_ist"]).sort_values("ts_ist")
            raw["day"] = raw["ts_ist"].dt.date.astype(str)
            t = raw["ts_ist"].dt.time
            open_t = datetime.strptime("09:15", "%H:%M").time()
            close_t = datetime.strptime("15:30", "%H:%M").time()
            raw = raw[(t >= open_t) & (t <= close_t)]
            raw.attrs["bar_minutes"] = 1
            self._bar_minutes[slug] = 1
            self._frames[slug] = raw.reset_index(drop=True)
            return self._frames[slug]

        self._frames[slug] = pd.DataFrame()
        return self._frames[slug]

    def _load_legacy_nifty_csv(self) -> pd.DataFrame:
        if self._legacy_nifty is not None:
            return self._legacy_nifty
        path = self.data_root / _LEGACY_INTRADAY_CSV
        if not path.is_file():
            self._legacy_nifty = pd.DataFrame()
            return self._legacy_nifty
        raw = pd.read_csv(path)
        raw["ts_ist"] = pd.to_datetime(raw["date"], errors="coerce").map(_normalize_naive_to_ist)
        raw = raw.dropna(subset=["ts_ist"]).sort_values("ts_ist")
        raw["day"] = raw["ts_ist"].dt.date.astype(str)
        t = raw["ts_ist"].dt.time
        open_t = datetime.strptime("09:15", "%H:%M").time()
        close_t = datetime.strptime("15:30", "%H:%M").time()
        raw = raw[(t >= open_t) & (t <= close_t)]
        raw["bucket"] = raw["ts_ist"].dt.floor("5min")
        raw = raw.drop_duplicates(subset=["day", "bucket"], keep="first")
        raw = raw.drop(columns=["bucket"])
        raw.attrs["bar_minutes"] = 5
        self._legacy_nifty = raw.reset_index(drop=True)
        return self._legacy_nifty
