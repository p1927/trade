"""Black-Scholes-lite option chain synthesizer for NSE replay."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(*, spot: float, strike: float, t_years: float, vol: float, is_call: bool) -> float:
    if t_years <= 0 or spot <= 0 or strike <= 0:
        intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
        return intrinsic
    vol = max(0.05, vol)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    if is_call:
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _next_weekly_expiry(sim_ts: datetime) -> str:
    day = sim_ts.astimezone(IST).date()
    # Next Thursday (NIFTY weekly convention)
    days_ahead = (3 - day.weekday()) % 7
    if days_ahead == 0 and sim_ts.astimezone(IST).time().hour >= 15:
        days_ahead = 7
    expiry = day + timedelta(days=days_ahead or 7)
    return expiry.isoformat()


class OptionsSynthesizer:
    def __init__(self, *, default_vol: float = 0.14) -> None:
        self.default_vol = default_vol

    def build_chain(
        self,
        *,
        underlying: str,
        exchange: str,
        spot: float,
        sim_ts: datetime,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        expiry = expiry_date or _next_weekly_expiry(sim_ts)
        expiry_dt = datetime.strptime(expiry[:10], "%Y-%m-%d").replace(tzinfo=IST, hour=15, minute=30)
        t_years = max(1 / 365, (expiry_dt - sim_ts.astimezone(IST)).total_seconds() / (365 * 24 * 3600))
        step = 100.0 if spot >= 20000 else (50.0 if spot >= 10000 else 100.0)
        atm = round(spot / step) * step
        strikes = [atm + (i - strike_count // 2) * step for i in range(strike_count)]
        legs: list[dict[str, Any]] = []
        total_ce_oi = 0.0
        total_pe_oi = 0.0
        for strike in strikes:
            ce = _bs_price(spot=spot, strike=strike, t_years=t_years, vol=self.default_vol, is_call=True)
            pe = _bs_price(spot=spot, strike=strike, t_years=t_years, vol=self.default_vol, is_call=False)
            ce_oi = int(max(1000, (1.0 / (1 + abs(strike - spot) / step)) * 50000))
            pe_oi = int(max(1000, (1.0 / (1 + abs(strike - spot) / step)) * 48000))
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            legs.append(
                {
                    "strike": strike,
                    "strike_price": strike,
                    "ce_ltp": round(max(0.05, ce), 2),
                    "pe_ltp": round(max(0.05, pe), 2),
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi,
                    "ce_iv": round(self.default_vol * 100, 2),
                    "pe_iv": round(self.default_vol * 100, 2),
                }
            )
        return {
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
            "expiry_date": expiry[:10],
            "underlying_ltp": round(spot, 2),
            "spot": round(spot, 2),
            "total_call_oi": int(total_ce_oi),
            "total_put_oi": int(total_pe_oi),
            "chain": legs,
            "source": "stock_simulator",
            "simulated": True,
            "sim_ts": sim_ts.isoformat(),
        }


def synthesize_option_chain(**kwargs: Any) -> dict[str, Any]:
    return OptionsSynthesizer().build_chain(**kwargs)
