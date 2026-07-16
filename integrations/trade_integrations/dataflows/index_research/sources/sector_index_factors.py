"""Sector index price factors from NSE sector CSV archive."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from trade_integrations.nse_browser.parsers.sector_indices import breadth_sector_slugs

logger = logging.getLogger(__name__)

_RETURN_WINDOW = 7
_BENCHMARK_SLUG = "nifty50"
_PRIVATE_BANK = "private_bank"
_PSU_BANK = "psu_bank"


def _pivot_closes(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "close" not in frame.columns:
        return pd.DataFrame()
    out = frame.pivot_table(index="date", columns="index_slug", values="close", aggfunc="last")
    out.index = out.index.astype(str)
    return out.sort_index()


def _rolling_return_pct(closes: pd.DataFrame, window: int = _RETURN_WINDOW) -> pd.DataFrame:
    if closes.empty:
        return pd.DataFrame()
    return closes.pct_change(periods=window) * 100.0


def build_sector_price_factor_series(
    frame: pd.DataFrame,
    trading_dates: list[str],
) -> dict[str, pd.Series]:
    """
    Compute sector rotation factors aligned to Nifty trading dates.

    Returns series keyed by factor name:
    - sector_breadth_price_7d: share of sector indices with positive 7d return
    - sector_rel_strength_mean_7d: mean sector 7d return minus Nifty 7d return
    - bank_private_vs_psu_spread_7d: private bank minus PSU bank 7d return
    """
    empty: dict[str, pd.Series] = {
        "sector_breadth_price_7d": pd.Series(dtype=float),
        "sector_rel_strength_mean_7d": pd.Series(dtype=float),
        "bank_private_vs_psu_spread_7d": pd.Series(dtype=float),
    }
    if frame.empty:
        return empty

    closes = _pivot_closes(frame)
    if closes.empty:
        return empty

    rets = _rolling_return_pct(closes)
    breadth_sectors = sorted(breadth_sector_slugs() & set(rets.columns))
    if not breadth_sectors:
        return empty

    breadth_vals: dict[str, float] = {}
    rel_vals: dict[str, float] = {}
    bank_vals: dict[str, float] = {}
    nifty_rets = rets[_BENCHMARK_SLUG] if _BENCHMARK_SLUG in rets.columns else pd.Series(dtype=float)

    for day in rets.index:
        sector_row = rets.loc[day, breadth_sectors]
        valid = sector_row.dropna()
        if not valid.empty:
            breadth_vals[day] = float((valid > 0).sum() / len(valid))
            if not nifty_rets.empty and day in nifty_rets.index and not pd.isna(nifty_rets[day]):
                rel_vals[day] = float(valid.mean() - float(nifty_rets[day]))
        if _PRIVATE_BANK in rets.columns and _PSU_BANK in rets.columns:
            priv = rets.loc[day, _PRIVATE_BANK]
            psu = rets.loc[day, _PSU_BANK]
            if not pd.isna(priv) and not pd.isna(psu):
                bank_vals[day] = float(priv - psu)

    aligned = set(trading_dates)
    return {
        "sector_breadth_price_7d": pd.Series(
            {d: breadth_vals[d] for d in breadth_vals if d in aligned}
        ),
        "sector_rel_strength_mean_7d": pd.Series({d: rel_vals[d] for d in rel_vals if d in aligned}),
        "bank_private_vs_psu_spread_7d": pd.Series({d: bank_vals[d] for d in bank_vals if d in aligned}),
    }


def build_monthly_equity_flow_series(
    monthly_frame: pd.DataFrame,
    trading_dates: list[str],
    *,
    value_col: str = "equity_net",
    factor_name: str,
) -> pd.Series:
    """Forward-fill month-end equity net (₹ Cr) onto trading dates."""
    if monthly_frame.empty or value_col not in monthly_frame.columns:
        return pd.Series(dtype=float)
    monthly = monthly_frame.copy()
    monthly["date"] = monthly["date"].astype(str).str[:10]
    monthly = monthly.sort_values("date").drop_duplicates("date", keep="last")
    series = pd.Series(monthly[value_col].astype(float).values, index=monthly["date"])
    out: dict[str, float] = {}
    for day in trading_dates:
        eligible = series.index[series.index <= day[:10]]
        if len(eligible) == 0:
            continue
        val = series.loc[eligible[-1]]
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            out[day] = float(val)
    return pd.Series(out)
