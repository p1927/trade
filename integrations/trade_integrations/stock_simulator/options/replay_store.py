"""Assemble option chains from Hugging Face 1-minute replay parquet."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trade_integrations.stock_simulator.hf_paths import hf_replay_root, index_slug, options_dir

IST = ZoneInfo("Asia/Kolkata")

_OPENALGO_EXPIRY_RE = re.compile(
    r"^(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})$",
    re.IGNORECASE,
)


def _parse_expiry(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem[:10])
    except ValueError:
        return None


def _expiry_to_file_stem(expiry_date: str) -> str | None:
    """Map OpenAlgo DDMMMYY / ISO expiry to parquet filename stem (YYYY-MM-DD)."""
    raw = expiry_date.strip().upper().replace("-", "")
    try:
        return date.fromisoformat(expiry_date[:10]).isoformat()
    except ValueError:
        pass
    match = _OPENALGO_EXPIRY_RE.match(raw)
    if not match:
        return None
    day, month, yy = match.groups()
    month_num = "JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC".index(month.upper()[:3]) // 3 + 1
    year = 2000 + int(yy)
    return date(year, month_num, int(day)).isoformat()


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

        strikes = sorted(float(s) for s in latest["strike"].unique())
        if not strikes:
            return None
        atm = min(strikes, key=lambda s: abs(s - spot))
        atm_idx = strikes.index(atm)
        # OpenAlgo: strike_count strikes above and below ATM (inclusive window).
        lo = max(0, atm_idx - strike_count)
        hi = min(len(strikes), atm_idx + strike_count + 1)
        selected = strikes[lo:hi]

        ce_by_strike = {
            float(row["strike"]): row
            for _, row in latest[latest["option_type"] == "CE"].iterrows()
        }
        pe_by_strike = {
            float(row["strike"]): row
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

    def quote_at(self, openalgo_symbol: str, exchange: str, sim_ts: datetime) -> dict[str, float | int] | None:
        """LTP/OI for an OpenAlgo-format option symbol at sim_ts."""
        from trade_integrations.stock_simulator.master_contract import parse_openalgo_option_symbol

        parsed = parse_openalgo_option_symbol(openalgo_symbol)
        if parsed is None:
            return None
        slug = parsed["base"]
        index_exchange = "BSE_INDEX" if slug == "SENSEX" else "NSE_INDEX"
        if not self.has_underlying(slug, index_exchange):
            return None

        opt_dir = options_dir(self.data_root, slug)
        expiry_iso = parsed["expiry"].isoformat()
        path = opt_dir / f"{expiry_iso}.parquet"
        if not path.is_file():
            return None

        frame = self._load_expiry(path)
        if frame.empty:
            return None

        sim_ist = sim_ts.astimezone(IST)
        day = sim_ist.date().isoformat()
        cutoff = pd.Timestamp(sim_ist)
        leg_frame = frame[
            (frame["strike"] == parsed["strike"])
            & (frame["option_type"] == parsed["option_type"])
        ]
        if leg_frame.empty:
            return None
        day_frame = leg_frame[(leg_frame["trading_day"] == day) & (leg_frame["timestamp"] <= cutoff)]
        if day_frame.empty:
            # Before open or sparse day — last bar at or before sim_ts on any prior session.
            prior = leg_frame[leg_frame["timestamp"] <= cutoff]
            if prior.empty:
                return None
            hit = prior.sort_values("timestamp").iloc[-1]
        else:
            hit = day_frame.sort_values("timestamp").iloc[-1]
        ltp = float(hit["close"])
        return {
            "open": float(hit["open"]),
            "high": float(hit["high"]),
            "low": float(hit["low"]),
            "close": ltp,
            "ltp": ltp,
            "volume": int(hit.get("volume") or 0),
            "oi": int(hit.get("open_interest") or 0),
            "prev_close": ltp,
            "bar_ts": pd.Timestamp(hit["timestamp"]).isoformat(),
        }

    def history_bars(
        self,
        openalgo_symbol: str,
        exchange: str,
        start: str,
        end: str,
        *,
        bar_minutes: int = 1,
    ) -> list[dict[str, Any]]:
        """Intraday OHLCV (+ OI) for an OpenAlgo-format option symbol."""
        from trade_integrations.stock_simulator.master_contract import parse_openalgo_option_symbol

        parsed = parse_openalgo_option_symbol(openalgo_symbol)
        if parsed is None:
            return []

        slug = parsed["base"]
        opt_dir = options_dir(self.data_root, slug)
        expiry_iso = parsed["expiry"].isoformat()
        path = opt_dir / f"{expiry_iso}.parquet"
        if not path.is_file():
            return []

        frame = self._load_expiry(path)
        if frame.empty:
            return []

        leg = frame[
            (frame["strike"] == parsed["strike"])
            & (frame["option_type"] == parsed["option_type"])
            & (frame["trading_day"] >= start)
            & (frame["trading_day"] <= end)
        ]
        if leg.empty:
            return []

        if bar_minutes >= 1440:
            buckets = (
                leg.groupby("trading_day", as_index=False)
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                    open_interest=("open_interest", "last"),
                    timestamp=("timestamp", "last"),
                )
            )
        elif bar_minutes <= 1:
            buckets = leg.copy()
            buckets["bucket"] = buckets["timestamp"]
        else:
            leg = leg.copy()
            leg["bucket"] = leg["timestamp"].dt.floor(f"{bar_minutes}min")
            buckets = (
                leg.groupby(["trading_day", "bucket"], as_index=False)
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                    open_interest=("open_interest", "last"),
                    timestamp=("timestamp", "last"),
                )
            )

        rows: list[dict[str, Any]] = []
        for _, hit in buckets.sort_values("timestamp").iterrows():
            vol = hit.get("volume")
            oi = hit.get("open_interest")
            ts_val = (
                str(hit["trading_day"])
                if bar_minutes >= 1440 and "trading_day" in hit
                else pd.Timestamp(hit["timestamp"]).isoformat()
            )
            rows.append(
                {
                    "timestamp": ts_val,
                    "open": float(hit["open"]),
                    "high": float(hit["high"]),
                    "low": float(hit["low"]),
                    "close": float(hit["close"]),
                    "volume": int(vol) if vol is not None and pd.notna(vol) else 0,
                    "oi": int(oi) if oi is not None and pd.notna(oi) else 0,
                }
            )
        return rows

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
            target = _expiry_to_file_stem(expiry_date) or expiry_date[:10]
            for path in files:
                if path.stem == target:
                    return path
            return None
        sim_day = sim_ts.astimezone(IST).date()
        candidates = [
            p for p in files if (exp := _parse_expiry(p.stem)) is not None and exp >= sim_day
        ]
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
