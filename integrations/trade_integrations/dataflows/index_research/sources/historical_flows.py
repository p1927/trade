"""Historical FII/DII + India VIX backfill into cold-tier storage."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import load_history_dataset, save_history_dataset
from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
    fetch_mrchartist_flow_frame,
    load_flow_cash_cache,
    load_nifty_oi_daily_frame,
    merge_flow_derivatives_frame,
    upsert_flow_cash_cache,
)
from trade_integrations.dataflows.index_research.sources.web_flow_fetch import fetch_niftyinvest_flow_frame

logger = logging.getLogger(__name__)


def _month_keys(start: str, end: str) -> list[str]:
    """Generate Nifty Invest yearMonth keys between start and end."""
    start_d = date.fromisoformat(start[:10]).replace(day=1)
    end_d = date.fromisoformat(end[:10])
    months: list[str] = []
    cursor = start_d
    while cursor <= end_d:
        months.append(f"{cursor.year}-{cursor.strftime('%b')}")
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def _load_repo_fii_dii(start: str, end: str) -> pd.DataFrame:
    try:
        from trade_integrations.nse_browser.repository import load_repo_dataset

        frame = load_repo_dataset("fii_dii")
    except Exception:
        return pd.DataFrame()
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _merge_flow_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid:
        return pd.DataFrame()
    combined = pd.concat(valid, ignore_index=True)
    combined["date"] = combined["date"].astype(str).str[:10]
    return combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_india_vix_history(*, start: str, end: str) -> pd.DataFrame:
    try:
        from trade_integrations.dataflows.index_research.sources.nselib_fetch import fetch_india_vix_range

        parsed = fetch_india_vix_range(start[:10], end[:10])
        if not parsed.empty:
            return parsed
    except Exception as exc:
        logger.debug("india_vix_data failed: %s", exc)

    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        end_exclusive = (datetime.strptime(end[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        hist = yf.Ticker("^INDIAVIX").history(start=start[:10], end=end_exclusive, auto_adjust=True)
        if hist.empty:
            return pd.DataFrame()
        frame = hist.reset_index()
        date_col = "Date" if "Date" in frame.columns else frame.columns[0]
        frame["date"] = pd.to_datetime(frame[date_col]).dt.strftime("%Y-%m-%d")
        frame["india_vix"] = frame["Close"].astype(float)
        return frame[["date", "india_vix"]].dropna().sort_values("date").reset_index(drop=True)
    except Exception as exc:
        logger.debug("yfinance VIX fallback failed: %s", exc)
        return pd.DataFrame()


def sync_merged_flow_derivatives_to_cold_tier(
    start: str,
    end: str,
    *,
    allow_live_fetch: bool = False,
) -> dict[str, Any]:
    """Persist derivative columns from merged flow frame into flow_derivatives_daily."""
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

    merged = merge_flow_derivatives_frame(start[:10], end[:10], allow_live_fetch=allow_live_fetch)
    if merged.empty:
        return {"status": "skipped", "reason": "empty_merge", "start": start[:10], "end": end[:10]}

    deriv_cols = (
        "nifty_pcr",
        "fii_sentiment_score",
        "fii_idx_fut_long",
        "fii_idx_fut_short",
        "fii_idx_put_oi",
        "fii_idx_call_oi",
        "fii_fut_long_short_ratio",
    )
    present = ["date"] + [c for c in deriv_cols if c in merged.columns]
    deriv = merged[present].copy()
    value_cols = [c for c in deriv.columns if c != "date"]
    if not value_cols or not deriv[value_cols].notna().any().any():
        return {"status": "skipped", "reason": "no_deriv_values", "start": start[:10], "end": end[:10]}

    existing = load_history_dataset("flow_derivatives_daily")
    overlay = overlay_derivative_columns(existing, deriv)
    result = save_history_dataset("flow_derivatives_daily", overlay)
    return {
        "status": "ok",
        "rows": len(overlay),
        "start": start[:10],
        "end": end[:10],
        **result,
    }


def backfill_flow_history(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    allow_live_fetch: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    end_day = (end or datetime.now(timezone.utc).date().isoformat())[:10]
    months = _month_keys(start, end_day)

    niftyinvest = fetch_niftyinvest_flow_frame(
        months=months,
        start=start,
        end=end_day,
        allow_live_fetch=allow_live_fetch,
        synthesize_months=True,
    )
    mrchartist = fetch_mrchartist_flow_frame(allow_live_fetch=allow_live_fetch, include_seeded=False)
    if not mrchartist.empty:
        mrchartist = mrchartist[(mrchartist["date"] >= start[:10]) & (mrchartist["date"] <= end_day)]

    repo_flows = _load_repo_fii_dii(start, end_day)
    cache = load_flow_cash_cache()
    if not cache.empty:
        cache = cache[(cache["date"] >= start[:10]) & (cache["date"] <= end_day)]

    cash = _merge_flow_frames([repo_flows, niftyinvest, mrchartist, cache])

    merged_flow = merge_flow_derivatives_frame(
        start[:10],
        end_day,
        allow_live_fetch=allow_live_fetch,
    )
    if not merged_flow.empty:
        cash_cols = [c for c in ("date", "fii_net", "dii_net", "source", "fii_buy", "fii_sell", "dii_buy", "dii_sell") if c in merged_flow.columns]
        if cash_cols:
            cash = _merge_flow_frames([cash, merged_flow[cash_cols]])

    deriv_cols = (
        "nifty_pcr",
        "fii_sentiment_score",
        "fii_idx_fut_long",
        "fii_idx_fut_short",
        "fii_idx_put_oi",
        "fii_idx_call_oi",
        "fii_fut_long_short_ratio",
    )
    deriv = pd.DataFrame()
    if not merged_flow.empty:
        present = ["date"] + [c for c in deriv_cols if c in merged_flow.columns]
        deriv = merged_flow[present].copy()

    oi_daily = load_nifty_oi_daily_frame(start=start, end=end_day)
    if not oi_daily.empty:
        from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

        deriv = overlay_derivative_columns(deriv, oi_daily)

    if not deriv.empty:
        from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

        cash = overlay_derivative_columns(cash, deriv)
        existing_deriv = load_history_dataset("flow_derivatives_daily")
        deriv = overlay_derivative_columns(existing_deriv, deriv)

    vix = fetch_india_vix_history(start=start, end=end_day)

    if cash.empty and vix.empty:
        return {"status": "error", "reason": "no_flow_data", "start": start, "end": end_day}

    coverage = 0.0
    if not cash.empty and "fii_net" in cash.columns:
        coverage = float(cash["fii_net"].notna().mean())

    if dry_run:
        return {
            "status": "dry_run",
            "cash_rows": len(cash),
            "deriv_rows": len(deriv),
            "vix_rows": len(vix),
            "fii_coverage_pct": round(coverage * 100.0, 1),
            "start": start,
            "end": end_day,
            "months_fetched": len(months),
        }

    cash_result: dict[str, Any] = {"status": "skipped"}
    if not cash.empty:
        cash_result = save_history_dataset("flow_cash_daily", cash)
        upsert_flow_cash_cache(cash.to_dict(orient="records"))

    deriv_result: dict[str, Any] = {"status": "skipped"}
    if not deriv.empty:
        deriv_result = save_history_dataset("flow_derivatives_daily", deriv)

    vix_result: dict[str, Any] = {"status": "skipped"}
    if not vix.empty:
        vix_result = save_history_dataset("india_vix_daily", vix)

    cold_sync = sync_merged_flow_derivatives_to_cold_tier(
        start[:10],
        end_day,
        allow_live_fetch=allow_live_fetch,
    )
    factor_sync: dict[str, Any] = {"status": "skipped"}
    try:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            sync_flow_factors_from_merge,
        )

        factor_sync = sync_flow_factors_from_merge(
            start=start[:10],
            end=end_day,
            allow_live_fetch=allow_live_fetch,
        )
    except Exception as exc:
        logger.warning("factor sync from merge failed: %s", exc)
        factor_sync = {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "cash_rows": len(cash),
        "vix_rows": len(vix),
        "fii_coverage_pct": round(coverage * 100.0, 1),
        "cash": cash_result,
        "derivatives": deriv_result,
        "vix": vix_result,
        "cold_deriv_sync": cold_sync,
        "factor_sync": factor_sync,
    }
