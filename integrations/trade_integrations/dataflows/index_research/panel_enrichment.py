"""Enrich materialized history panel with prediction-only derived columns."""

from __future__ import annotations

import logging

from datetime import date

import os

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.sources.rbi_repo_schedule import load_repo_schedule

logger = logging.getLogger(__name__)


def _cold_tier_nifty_pe_series(trading_dates: list[str]) -> pd.Series:
    """Per-date trailing P/E from cold-tier valuation — no terminal-close scaling."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("nifty50_valuation_daily")
    if frame.empty or "nifty_pe" not in frame.columns:
        return pd.Series(dtype=float)
    daily = frame[["date", "nifty_pe"]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily["nifty_pe"] = pd.to_numeric(daily["nifty_pe"], errors="coerce")
    daily = daily.dropna(subset=["nifty_pe"]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged["nifty_pe"].values, index=trading_dates)


def _rolling_sum_on_trading_dates(
    flow: pd.DataFrame,
    column: str,
    trading_dates: list[str],
    *,
    window: int = 5,
) -> pd.Series:
    if flow.empty or column not in flow.columns:
        return pd.Series(dtype=float)
    daily = flow[["date", column]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.dropna(subset=[column]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    daily[f"{column}_{window}d"] = daily[column].rolling(window, min_periods=1).sum()
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily[["date", f"{column}_{window}d"]].sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged[f"{column}_{window}d"].values, index=trading_dates)


def _merge_flow_columns(
    frame: pd.DataFrame,
    *,
    allow_live_fetch: bool = False,
) -> pd.DataFrame:
    """Attach flow / PCR / 5d sums from merged public history."""
    if frame.empty or "date" not in frame.columns:
        return frame

    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        merge_flow_derivatives_frame,
    )

    out = frame.copy()
    dates = out["date"].astype(str).tolist()
    start, end = dates[0], dates[-1]

    flow: pd.DataFrame
    if "fii_net" in out.columns and out["fii_net"].notna().any():
        flow = out[["date"] + [c for c in out.columns if c in (
            "fii_net", "dii_net", "nifty_pcr", "fii_fut_long_short_ratio", "fii_sentiment_score"
        )]].copy()
    else:
        flow = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if not flow.empty:
        flow = flow.copy()
        flow["date"] = flow["date"].astype(str).str[:10]
        flow = flow.sort_values("date").drop_duplicates("date", keep="last")
        merge_cols = [
            c
            for c in (
                "fii_net",
                "dii_net",
                "nifty_pcr",
                "fii_fut_long_short_ratio",
                "fii_sentiment_score",
            )
            if c in flow.columns
        ]
        if merge_cols:
            subset = flow[["date"] + merge_cols]
            overlap = set(out.columns) & set(merge_cols) - {"date"}
            if overlap:
                out = out.drop(columns=list(overlap), errors="ignore")
            out = out.merge(subset, on="date", how="left")

        fii_5d = _rolling_sum_on_trading_dates(flow, "fii_net", dates, window=5)
        dii_5d = _rolling_sum_on_trading_dates(flow, "dii_net", dates, window=5)
    else:
        fii_5d = pd.Series(dtype=float)
        dii_5d = pd.Series(dtype=float)

    inst_5d = fii_5d + dii_5d if not fii_5d.empty and not dii_5d.empty else pd.Series(dtype=float)
    if not inst_5d.empty and not fii_5d.empty:
        denom = fii_5d.abs().clip(lower=50.0)
        absorption = pd.Series(
            np.where(fii_5d < 0, dii_5d / denom, np.where(fii_5d > 0, dii_5d / denom, np.nan)),
            index=fii_5d.index,
        )
    else:
        absorption = pd.Series(dtype=float)

    def _map_series(col: str, series: pd.Series) -> None:
        if series.empty:
            return
        mapped = out["date"].map(series)
        if col in out.columns:
            out[col] = mapped.combine_first(out[col])
        else:
            out[col] = mapped

    _map_series("fii_net_5d", fii_5d)
    _map_series("dii_net_5d", dii_5d)
    _map_series("institutional_net_5d", inst_5d)
    _map_series("dii_absorption_ratio", absorption)

    if "nifty_pe" not in out.columns or pd.to_numeric(out.get("nifty_pe"), errors="coerce").isna().all():
        cold_pe = _cold_tier_nifty_pe_series(dates)
        if not cold_pe.empty:
            _map_series("nifty_pe", cold_pe)

    if "index_sentiment" not in out.columns or pd.to_numeric(out.get("index_sentiment"), errors="coerce").isna().all():
        if "fii_sentiment_score" in out.columns:
            scores = pd.to_numeric(out["fii_sentiment_score"], errors="coerce")
            out["index_sentiment"] = np.clip((scores - 50.0) / 50.0, -1.0, 1.0)
        elif "india_vix" in out.columns:
            vix = pd.to_numeric(out["india_vix"], errors="coerce")
            out["index_sentiment"] = np.clip(-(vix - 14.0) / 20.0, -1.0, 1.0)

    return out


def _vector_repo_rates(dates: pd.Series) -> pd.Series:
    schedule = load_repo_schedule()
    parsed = [date.fromisoformat(d[:10]) for d in dates.astype(str)]
    rates: list[float] = []
    for day in parsed:
        rate = schedule[0][1]
        for effective, value in schedule:
            if day >= date.fromisoformat(effective):
                rate = value
            else:
                break
        rates.append(float(rate))
    return pd.Series(rates, index=dates.index)


def _append_repo_and_india_rates(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    out = frame.copy()
    out["repo_rate"] = _vector_repo_rates(out["date"])

    tbill_override = os.getenv("INDEX_INDIA_91D_TBILL", "").strip()
    ten_y_override = os.getenv("INDEX_INDIA_10Y", "").strip()
    credit_override = os.getenv("INDEX_INDIA_CREDIT_SPREAD", "").strip()

    if "india_91d_tbill" not in out.columns or pd.to_numeric(out["india_91d_tbill"], errors="coerce").isna().all():
        if tbill_override:
            out["india_91d_tbill"] = float(tbill_override)
        else:
            out["india_91d_tbill"] = out["repo_rate"]

    if "india_10y" not in out.columns or pd.to_numeric(out["india_10y"], errors="coerce").isna().all():
        if ten_y_override:
            out["india_10y"] = float(ten_y_override)
        else:
            out["india_10y"] = out["repo_rate"] + 0.65

    if credit_override and (
        "india_credit_spread" not in out.columns
        or pd.to_numeric(out.get("india_credit_spread"), errors="coerce").isna().all()
    ):
        out["india_credit_spread"] = float(credit_override)

    return out


def enrich_prediction_panel(
    frame: pd.DataFrame,
    *,
    allow_live_fetch: bool = False,
) -> pd.DataFrame:
    """Add flow, rates, PE, and sentiment columns consumed by Ridge / tracks."""
    if frame.empty:
        return frame
    out = frame.copy()
    out = _merge_flow_columns(out, allow_live_fetch=allow_live_fetch)
    out = _append_repo_and_india_rates(out)
    return out
