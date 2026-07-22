"""Assemble option chains from Hugging Face 1-minute replay parquet."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trade_integrations.stock_simulator.hf_paths import hf_replay_root, index_slug, options_dir

IST = ZoneInfo("Asia/Kolkata")


def _parse_expiry(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem[:10])
    except ValueError:
        return None


class OptionsReplayStore:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self._hf_root = hf_replay_root(data_root)
        self._frames: dict[str, pd.DataFrame] = {}

    def has_underlying(self, symbol: str, exchange: str) -> bool:
        slug = index_slug(symbol, exchange)
        if not slug:
            return False
        return options_dir(self.data_root, slug).is_dir()

    def chain_at(
        self,
        *,
        underlying: str,
        exchange: str,
        spot: float,
        sim_ts: datetime,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any] | None:
        slug = index_slug(underlying, exchange)
        if not slug:
            return None
        path = self._pick_expiry_file(slug, sim_ts, expiry_date)
        if path is None:
            return None
        frame = self._load_expiry(path)
        if frame.empty:
            return None

        sim_ist = sim_ts.astimezone(IST)
        day = sim_ist.date().isoformat()
        cutoff = pd.Timestamp(sim_ist)
        day_frame = frame[(frame["trading_day"] == day) & (frame["timestamp"] <= cutoff)]
        if day_frame.empty:
            return None

        latest = (
            day_frame.sort_values("timestamp")
            .groupby(["strike", "option_type"], as_index=False)
            .last()
        )
        if latest.empty:
            return None

        strikes = sorted(int(s) for s in latest["strike"].unique())
        if not strikes:
            return None
        atm = min(strikes, key=lambda s: abs(s - spot))
        half = max(1, strike_count // 2)
        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - half)
        hi = min(len(strikes), lo + strike_count)
        lo = max(0, hi - strike_count)
        selected = strikes[lo:hi]

        ce_by_strike = {
            int(row["strike"]): row
            for _, row in latest[latest["option_type"] == "CE"].iterrows()
        }
        pe_by_strike = {
            int(row["strike"]): row
            for _, row in latest[latest["option_type"] == "PE"].iterrows()
        }

        legs: list[dict[str, Any]] = []
        total_ce_oi = 0
        total_pe_oi = 0
        for strike in selected:
            ce = ce_by_strike.get(strike)
            pe = pe_by_strike.get(strike)
            ce_ltp = float(ce["close"]) if ce is not None else 0.0
            pe_ltp = float(pe["close"]) if pe is not None else 0.0
            ce_oi = int(ce["open_interest"]) if ce is not None else 0
            pe_oi = int(pe["open_interest"]) if pe is not None else 0
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            legs.append(
                {
                    "strike": float(strike),
                    "strike_price": float(strike),
                    "ce_ltp": round(max(0.0, ce_ltp), 2),
                    "pe_ltp": round(max(0.0, pe_ltp), 2),
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi,
                    "ce_iv": 0.0,
                    "pe_iv": 0.0,
                }
            )

        expiry_val = str(latest["expiry"].iloc[0])[:10]
        return {
            "underlying": slug,
            "exchange": exchange.upper(),
            "expiry_date": expiry_val,
            "underlying_ltp": round(spot, 2),
            "spot": round(spot, 2),
            "total_call_oi": int(total_ce_oi),
            "total_put_oi": int(total_pe_oi),
            "chain": legs,
            "source": "hf_replay",
            "simulated": True,
            "sim_ts": sim_ist.isoformat(),
            "replay_expiry_file": path.name,
        }

    def _pick_expiry_file(
        self,
        slug: str,
        sim_ts: datetime,
        expiry_date: str | None,
    ) -> Path | None:
        opt_dir = options_dir(self.data_root, slug)
        if not opt_dir.is_dir():
            return None
        files = sorted(opt_dir.glob("*.parquet"), key=lambda p: p.stem)
        if not files:
            return None
        if expiry_date:
            target = expiry_date[:10]
            for path in files:
                if path.stem == target:
                    return path
            return None
        sim_day = sim_ts.astimezone(IST).date()
        candidates = [p for p in files if (_parse_expiry(p.stem) or sim_day) >= sim_day]
        if not candidates:
            return files[-1]
        return candidates[0]

    def _load_expiry(self, path: Path) -> pd.DataFrame:
        key = str(path)
        if key in self._frames:
            return self._frames[key]
        if not path.is_file():
            self._frames[key] = pd.DataFrame()
            return self._frames[key]
        raw = pd.read_parquet(path)
        if "timestamp" in raw.columns:
            raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
            if raw["timestamp"].dt.tz is None:
                raw["timestamp"] = raw["timestamp"].dt.tz_localize(IST)
            else:
                raw["timestamp"] = raw["timestamp"].dt.tz_convert(IST)
        if "trading_day" not in raw.columns and "timestamp" in raw.columns:
            raw["trading_day"] = raw["timestamp"].dt.date.astype(str)
        self._frames[key] = raw.sort_values("timestamp").reset_index(drop=True)
        return self._frames[key]
