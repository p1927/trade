"""Nifty OHLCV history and aligned factor time-series for model training."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.calendar_features import (
    calendar_factor_dict,
)
from trade_integrations.dataflows.index_research.factor_store import load_factor_history
from trade_integrations.dataflows.index_research.technical_features import (
    enrich_nifty_technical_columns,
)

NIFTY_SYMBOL = "^NSEI"


def refresh_nifty_history_tail_if_stale(*, end: str | None = None) -> dict[str, object]:
    """Append recent Nifty daily bars when cold-tier cache lags the trading calendar."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.history_ingest import merge_with_priority
    from trade_integrations.dataflows.index_research.history_store import (
        load_history_dataset,
        save_history_dataset,
    )
    from trade_integrations.dataflows.index_research.sources.historical_macro import (
        _fetch_yfinance_ohlcv,
    )

    target = (end or india_trading_date_iso())[:10]
    cached = load_history_dataset("nifty_ohlcv_daily")
    max_date = ""
    if not cached.empty and "date" in cached.columns:
        max_date = str(cached["date"].astype(str).str[:10].max())
    if max_date and max_date >= target:
        return {"status": "ok", "reason": "fresh", "max_date": max_date}

    if max_date:
        start_dt = datetime.strptime(max_date[:10], "%Y-%m-%d") - timedelta(days=7)
        start = start_dt.strftime("%Y-%m-%d")
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    end_exclusive = (
        datetime.strptime(target[:10], "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    tail = _fetch_yfinance_ohlcv(NIFTY_SYMBOL, start, end_exclusive)
    if tail.empty:
        return {
            "status": "error",
            "reason": "yfinance_tail_empty",
            "max_date": max_date or None,
            "target": target,
        }
    tail = tail.copy()
    tail["source"] = "yfinance_tail"
    merged = merge_with_priority([cached, tail], on=["date"]) if not cached.empty else tail
    result = save_history_dataset("nifty_ohlcv_daily", merged)
    new_max = str(merged["date"].astype(str).str[:10].max())
    return {
        "status": "ok",
        "reason": "tail_refreshed",
        "max_date": new_max,
        "target": target,
        **result,
    }


def load_nifty_history(days: int = 365, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Load Nifty spot close history via yfinance ``^NSEI`` or cold-tier cache."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    cached = load_history_dataset("nifty_ohlcv_daily")
    if not cached.empty and "close" in cached.columns:
        try:
            refresh_nifty_history_tail_if_stale()
            cached = load_history_dataset("nifty_ohlcv_daily")
        except Exception:
            pass
        frame = cached.copy()
        frame["date"] = frame["date"].astype(str).str[:10]
        if start:
            frame = frame[frame["date"] >= start[:10]]
        if end:
            frame = frame[frame["date"] <= end[:10]]
        if days > 0 and not start:
            frame = frame.tail(max(1, days))
        cols = ["date", "close"]
        for optional in ("high", "low", "open", "volume"):
            if optional in frame.columns:
                cols.append(optional)
        return frame[cols].sort_values("date").reset_index(drop=True)

    import yfinance as yf

    end_dt = datetime.now(timezone.utc)
    if end:
        end_dt = datetime.strptime(end[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if start:
        start_dt = datetime.strptime(start[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start_dt = end_dt - timedelta(days=days)
    hist = yf.Ticker(NIFTY_SYMBOL).history(start=start_dt, end=end_dt + timedelta(days=1))
    if hist.empty:
        return pd.DataFrame(columns=["date", "close"])

    frame = hist.reset_index()
    if "Date" in frame.columns:
        date_series = frame["Date"]
    elif "Datetime" in frame.columns:
        date_series = frame["Datetime"]
    elif "index" in frame.columns:
        date_series = frame["index"]
    else:
        date_series = frame.iloc[:, 0]
    frame["date"] = pd.to_datetime(date_series).dt.strftime("%Y-%m-%d")
    if "Close" in frame.columns:
        frame["close"] = frame["Close"].astype(float)
    elif "close" in frame.columns:
        frame["close"] = frame["close"].astype(float)
    else:
        return pd.DataFrame(columns=["date", "close"])

    for src, dst in (("High", "high"), ("Low", "low"), ("Open", "open"), ("Volume", "volume")):
        if src in frame.columns:
            frame[dst] = frame[src].astype(float)
        elif dst in frame.columns:
            frame[dst] = frame[dst].astype(float)

    cols = ["date", "close"]
    for optional in ("high", "low", "open", "volume"):
        if optional in frame.columns:
            cols.append(optional)
    return frame[cols].sort_values("date").reset_index(drop=True)


def _append_calendar_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame
    out = frame.copy()
    for key in ("days_to_monthly_expiry", "is_budget_week", "is_results_season"):
        out[key] = np.nan
    for idx, raw_date in enumerate(out["date"]):
        try:
            as_of = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        cal = calendar_factor_dict(as_of)
        for key, value in cal.items():
            out.at[idx, key] = value
    return out


def _append_institutional_joint_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Joint FII–DII features (literature: absorption ratio, post-2023 regime)."""
    if frame.empty:
        return frame
    if "fii_net_5d" not in frame.columns or "dii_net_5d" not in frame.columns:
        return frame
    out = frame.copy()
    fii = pd.to_numeric(out["fii_net_5d"], errors="coerce")
    dii = pd.to_numeric(out["dii_net_5d"], errors="coerce")
    out["institutional_net_5d"] = fii + dii
    denom = fii.abs().clip(lower=50.0)
    out["dii_absorption_ratio"] = np.where(
        fii < 0,
        dii / denom,
        np.where(fii > 0, dii / denom, np.nan),
    )
    return out


def enrich_history_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add technical + calendar + institutional joint + Phase I derived columns."""
    if frame.empty:
        return frame
    enriched = enrich_nifty_technical_columns(frame)
    enriched = _append_calendar_columns(enriched)
    enriched = _append_institutional_joint_columns(enriched)
    try:
        from trade_integrations.dataflows.index_research.fundamental_features import enrich_fundamental_columns
        from trade_integrations.dataflows.index_research.spread_features import enrich_spread_columns

        enriched = enrich_fundamental_columns(enriched)
        enriched = enrich_spread_columns(enriched)
    except Exception:
        pass
    return enriched


def load_aligned_factor_history(days: int = 365, *, start: str | None = None) -> pd.DataFrame:
    """Merge Nifty closes with wide-format daily factor columns from panel or factor store."""
    try:
        from trade_integrations.dataflows.index_research.history_panel import load_aligned_panel_history

        panel = load_aligned_panel_history(days=days, start=start)
        if panel is not None and not panel.empty and "close" in panel.columns:
            return panel.sort_values("date").reset_index(drop=True)
    except Exception:
        pass

    nifty = load_nifty_history(days=days, start=start)
    if nifty.empty:
        return pd.DataFrame()

    start = nifty["date"].iloc[0]
    end = nifty["date"].iloc[-1]
    factors_long = load_factor_history(start, end)
    if factors_long.empty:
        return nifty

    if "date" not in factors_long.columns or "factor" not in factors_long.columns:
        return nifty

    value_col = "value" if "value" in factors_long.columns else factors_long.columns[-1]
    wide = factors_long.pivot_table(
        index="date",
        columns="factor",
        values=value_col,
        aggfunc="last",
    )
    wide = wide.reset_index()
    wide["date"] = wide["date"].astype(str).str[:10]

    aligned = nifty.merge(wide, on="date", how="left")
    aligned = enrich_history_features(aligned)
    return aligned.sort_values("date").reset_index(drop=True)
