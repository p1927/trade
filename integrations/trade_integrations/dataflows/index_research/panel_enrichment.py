"""Enrich materialized history panel with prediction-only derived columns."""

from __future__ import annotations

import logging

from datetime import date

import os

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.sources.india_rates import (
    cold_tier_rbi_rate_series,
    fetch_india_10y_fred_series,
)
from trade_integrations.dataflows.index_research.sources.rbi_repo_schedule import load_repo_schedule

logger = logging.getLogger(__name__)


def _cold_tier_cpi_series(trading_dates: list[str]) -> pd.Series:
    """Merge-asof monthly CPI YoY onto trading dates as cpi_yoy_proxy."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("india_cpi_monthly_yoy")
    if frame.empty:
        return pd.Series(dtype=float)
    col = "cpi_yoy_pct" if "cpi_yoy_pct" in frame.columns else None
    if col is None:
        for candidate in ("cpi_yoy", "yoy_pct", "inflation_yoy"):
            if candidate in frame.columns:
                col = candidate
                break
    if col is None:
        return pd.Series(dtype=float)
    daily = frame[["date", col]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily = daily.dropna(subset=[col]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged[col].values, index=trading_dates)


def _cold_tier_news_sentiment_series(trading_dates: list[str]) -> pd.Series:
    """Map cold-tier news sentiment_mean to index_sentiment [-1, 1] via merge_asof."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("india_news_sentiment_daily")
    if frame.empty:
        return pd.Series(dtype=float)
    col = "sentiment_mean" if "sentiment_mean" in frame.columns else None
    if col is None:
        for candidate in ("sentiment", "mean_sentiment", "avg_sentiment"):
            if candidate in frame.columns:
                col = candidate
                break
    if col is None:
        return pd.Series(dtype=float)
    daily = frame[["date", col]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily = daily.dropna(subset=[col]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    values = np.clip(merged[col].astype(float), -1.0, 1.0)
    return pd.Series(values.values, index=trading_dates)


def _cold_tier_valuation_series(trading_dates: list[str], column: str) -> pd.Series:
    """Per-date valuation column from cold-tier nifty50_valuation_daily (backward asof)."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("nifty50_valuation_daily")
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    daily = frame[["date", column]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.dropna(subset=[column]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged[column].values, index=trading_dates)


def _cold_tier_nifty_pe_series(trading_dates: list[str]) -> pd.Series:
    """Per-date trailing P/E from cold-tier valuation — no terminal-close scaling."""
    return _cold_tier_valuation_series(trading_dates, "nifty_pe")


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
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

    out = frame.copy()
    dates = out["date"].astype(str).tolist()
    start, end = dates[0], dates[-1]

    merged = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    if merged.empty:
        flow = out.copy()
    else:
        merged = merged.copy()
        merged["date"] = merged["date"].astype(str).str[:10]
        merged = merged.sort_values("date").drop_duplicates("date", keep="last")
        cash_cols = [c for c in ("fii_net", "dii_net", "fii_buy", "fii_sell", "dii_buy", "dii_sell", "source") if c in merged.columns]
        deriv_cols = [c for c in merged.columns if c not in cash_cols and c != "date"]
        if cash_cols:
            cash_part = merged[["date"] + cash_cols]
            overlap = set(out.columns) & set(cash_cols) - {"date"}
            if overlap:
                out = out.drop(columns=list(overlap), errors="ignore")
            out = out.merge(cash_part, on="date", how="left")
        if deriv_cols:
            deriv_part = merged[["date"] + deriv_cols]
            out = overlay_derivative_columns(out, deriv_part)
        flow = merged

    if not flow.empty:
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

    for val_col in ("nifty_pb", "nifty_dividend_yield"):
        if val_col not in out.columns or pd.to_numeric(out.get(val_col), errors="coerce").isna().all():
            cold_val = _cold_tier_valuation_series(dates, val_col)
            if not cold_val.empty:
                _map_series(val_col, cold_val)

    news_sent = _cold_tier_news_sentiment_series(dates)
    if not news_sent.empty:
        _map_series("index_sentiment", news_sent)
    elif "index_sentiment" not in out.columns or pd.to_numeric(out.get("index_sentiment"), errors="coerce").isna().all():
        if "fii_sentiment_score" in out.columns:
            scores = pd.to_numeric(out["fii_sentiment_score"], errors="coerce")
            out["index_sentiment"] = np.clip((scores - 50.0) / 50.0, -1.0, 1.0)
        elif "india_vix" in out.columns:
            vix = pd.to_numeric(out["india_vix"], errors="coerce")
            out["index_sentiment"] = np.clip(-(vix - 14.0) / 20.0, -1.0, 1.0)

    cold_cpi = _cold_tier_cpi_series(dates)
    if not cold_cpi.empty:
        _map_series("cpi_yoy_proxy", cold_cpi)

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


def _append_india_10y_with_sources(out: pd.DataFrame, *, ten_y_override: str) -> pd.DataFrame:
    """RBI WSS through last observed week; FRED merge_asof after; repo+spread last resort."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    dates = out["date"].astype(str).tolist()
    if ten_y_override:
        out["india_10y"] = float(ten_y_override)
        out["india_10y_source"] = "india_rates_env"
        return out

    last_rbi_date: str | None = None
    rbi_by_date: dict[str, float] = {}
    rbi_frame = load_history_dataset("india_rbi_wss_weekly")
    if not rbi_frame.empty and "india_10y" in rbi_frame.columns:
        rbi_slice = rbi_frame[["date", "india_10y"]].copy()
        rbi_slice["date"] = rbi_slice["date"].astype(str).str[:10]
        rbi_slice["india_10y"] = pd.to_numeric(rbi_slice["india_10y"], errors="coerce")
        rbi_slice = rbi_slice.dropna(subset=["india_10y"]).sort_values("date").drop_duplicates("date", keep="last")
        if not rbi_slice.empty:
            last_rbi_date = str(rbi_slice["date"].iloc[-1])[:10]
            trading = pd.DataFrame({"date": pd.to_datetime(dates)})
            merged_rbi = pd.merge_asof(
                trading.sort_values("date"),
                rbi_slice.assign(date=pd.to_datetime(rbi_slice["date"])).sort_values("date"),
                on="date",
                direction="backward",
            )
            for day, val in zip(dates, merged_rbi["india_10y"].tolist(), strict=False):
                if val is not None and not pd.isna(val) and (last_rbi_date is None or day <= last_rbi_date):
                    rbi_by_date[day] = float(val)

    fred_by_date: dict[str, float] = {}
    fred_series = fetch_india_10y_fred_series(dates[0], dates[-1])
    if not fred_series.empty:
        fred_df = fred_series.reset_index()
        fred_df.columns = ["date", "india_10y"]
        fred_df["date"] = pd.to_datetime(fred_df["date"].astype(str).str[:10])
        trading = pd.DataFrame({"date": pd.to_datetime(dates)})
        merged_fred = pd.merge_asof(
            trading.sort_values("date"),
            fred_df.sort_values("date"),
            on="date",
            direction="backward",
        )
        for day, val in zip(dates, merged_fred["india_10y"].tolist(), strict=False):
            if val is not None and not pd.isna(val):
                fred_by_date[day] = float(val)

    values: list[float] = []
    sources: list[str] = []
    for day in dates:
        if day in rbi_by_date:
            values.append(rbi_by_date[day])
            sources.append("rbi_wss")
            continue
        if day in fred_by_date:
            values.append(fred_by_date[day])
            sources.append("fred")
            continue
        repo = out.loc[out["date"].astype(str) == day, "repo_rate"]
        values.append(float(repo.iloc[0]) + 0.65 if not repo.empty else np.nan)
        sources.append("proxy")

    mapped = pd.Series(values, index=dates)
    if "india_10y" in out.columns:
        out["india_10y"] = out["date"].map(mapped).combine_first(pd.to_numeric(out["india_10y"], errors="coerce"))
    else:
        out["india_10y"] = out["date"].map(mapped)
    source_mapped = pd.Series(sources, index=dates)
    if "india_10y_source" in out.columns:
        out["india_10y_source"] = out["date"].map(source_mapped).combine_first(out["india_10y_source"])
    else:
        out["india_10y_source"] = out["date"].map(source_mapped)
    return out


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
            dates = out["date"].astype(str).tolist()
            rbi_tbill = cold_tier_rbi_rate_series(dates, "india_91d_tbill")
            if not rbi_tbill.empty and rbi_tbill.notna().any():
                out["india_91d_tbill"] = out["date"].map(rbi_tbill).combine_first(out["repo_rate"])
            else:
                out["india_91d_tbill"] = out["repo_rate"]

    if "india_10y" not in out.columns or pd.to_numeric(out["india_10y"], errors="coerce").isna().all():
        out = _append_india_10y_with_sources(out, ten_y_override=ten_y_override)
    elif not ten_y_override:
        out = _append_india_10y_with_sources(out, ten_y_override="")

    if credit_override:
        out["india_credit_spread"] = float(credit_override)
    else:
        out["india_credit_spread"] = np.nan

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
    try:
        from trade_integrations.dataflows.index_research.fundamental_features import enrich_fundamental_columns
        from trade_integrations.dataflows.index_research.spread_features import enrich_spread_columns

        out = enrich_fundamental_columns(out)
        out = enrich_spread_columns(out)
    except Exception:
        pass
    try:
        from trade_integrations.dataflows.index_research.ml_adapters.macro_lag_features import enrich_macro_lag_columns
        from trade_integrations.dataflows.index_research.ml_adapters.stationary_frame import to_stationary_pct_change
        from trade_integrations.dataflows.index_research.prediction_algorithms.config import pandas_ta_enabled

        out = enrich_macro_lag_columns(out)
        out = to_stationary_pct_change(out)
        if pandas_ta_enabled():
            from trade_integrations.dataflows.index_research.ml_adapters.ta_pandas_ta import enrich_pandas_ta_columns

            out = enrich_pandas_ta_columns(out)
    except Exception:
        pass
    return out
