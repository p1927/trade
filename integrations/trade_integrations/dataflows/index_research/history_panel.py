"""Materialized wide factor panel joining cold-tier history datasets."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    load_panel,
    save_panel,
)
from trade_integrations.dataflows.index_research.panel_invariants import ANNUAL_JOIN_BLOCKLIST
from trade_integrations.dataflows.index_research.sources.history_loader import enrich_history_features


def _join_annual_macro_by_year(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach india_macro_annual columns by calendar year on daily rows.

    Daily columns already present (e.g. ``usd_inr`` from ``macro_daily``) are never
    replaced by annual snapshots — annual values only fill NaN gaps. Replacing
    daily FX with one level per year destroys momentum features and Ridge eligibility.
    """
    if frame.empty or "date" not in frame.columns:
        return frame
    annual = load_history_dataset("india_macro_annual")
    if annual.empty or "year" not in annual.columns:
        return frame

    macro = annual.copy()
    join_cols = [
        c
        for c in macro.columns
        if c
        not in {
            "date",
            "year",
            "granularity",
            "source",
            "source_file",
        }
        and c not in ANNUAL_JOIN_BLOCKLIST
    ]
    if not join_cols:
        return frame

    macro = macro[["year"] + join_cols].drop_duplicates("year", keep="last")
    annual_by_year = macro.set_index("year")
    out = frame.copy()
    out["_year"] = out["date"].astype(str).str[:4].astype(int)

    for col in join_cols:
        mapped = out["_year"].map(annual_by_year[col])
        if col not in out.columns:
            out[col] = mapped
        else:
            existing = pd.to_numeric(out[col], errors="coerce")
            out[col] = existing.where(existing.notna(), mapped)

    return out.drop(columns=["_year"], errors="ignore")


_LAGGED_MACRO_FFILL_COLUMNS = (
    "usd_inr",
    "sp500",
    "gold",
    "oil_brent",
    "oil_wti",
    "us_10y",
    "nifty_pe",
    "nifty_pb",
    "nifty_dividend_yield",
)


def _ffill_lagged_macro_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill vendor-lagged macro columns when OHLCV exists for a trading day."""
    if frame.empty:
        return frame
    out = frame.copy()
    has_ohlcv = out["close"].notna() if "close" in out.columns else pd.Series(True, index=out.index)
    for col in _LAGGED_MACRO_FFILL_COLUMNS:
        if col not in out.columns:
            continue
        filled = out[col].ffill()
        out[col] = out[col].where(~has_ohlcv | out[col].notna(), filled)
    return out


def _merge_on_date(frames: list[pd.DataFrame]) -> pd.DataFrame:
    out: pd.DataFrame | None = None
    for frame in frames:
        if frame is None or frame.empty or "date" not in frame.columns:
            continue
        part = frame.copy()
        part["date"] = part["date"].astype(str).str[:10]
        if out is None:
            out = part
        else:
            overlap = set(out.columns) & set(part.columns) - {"date"}
            part = part.drop(columns=list(overlap), errors="ignore")
            out = out.merge(part, on="date", how="outer")
    if out is None:
        return pd.DataFrame()
    return out.sort_values("date").reset_index(drop=True)


def build_history_panel(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    panel_name: str = "NIFTY_2006_present",
) -> pd.DataFrame:
    """Join cold-tier datasets into one aligned wide panel."""
    del panel_name  # reserved for future multi-ticker panels
    nifty = load_history_dataset("nifty_ohlcv_daily")
    macro = load_history_dataset("macro_daily")
    flows = load_history_dataset("flow_cash_daily")
    deriv = load_history_dataset("flow_derivatives_daily")
    vix = load_history_dataset("india_vix_daily")
    news = load_history_dataset("news_events_daily")

    if nifty.empty and macro.empty:
        return pd.DataFrame()

    if not nifty.empty:
        base = nifty.rename(columns={"close": "close"})
    else:
        base = macro[["date", "nifty_close"]].rename(columns={"nifty_close": "close"})

    merged = _merge_on_date([base, macro, flows, deriv, vix, news])
    if "nifty_close" in merged.columns and "close" not in merged.columns:
        merged["close"] = merged["nifty_close"]

    if start:
        merged = merged[merged["date"] >= start[:10]]
    if end:
        merged = merged[merged["date"] <= end[:10]]

    if merged.empty:
        return merged

    merged = _join_annual_macro_by_year(merged)
    merged = _ffill_lagged_macro_columns(merged)

    from trade_integrations.dataflows.index_research.panel_enrichment import enrich_prediction_panel

    merged = enrich_prediction_panel(merged, allow_live_fetch=False)
    merged = enrich_history_features(merged)
    merged["date"] = merged["date"].astype(str).str[:10]
    return merged.sort_values("date").reset_index(drop=True)


def materialize_panel(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    panel_name: str = "NIFTY_2006_present",
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    frame = build_history_panel(start=start, end=end, panel_name=panel_name)
    if frame.empty:
        return {"status": "error", "reason": "empty_panel", "panel": panel_name}
    if dry_run:
        from trade_integrations.dataflows.index_research.panel_invariants import audit_panel_invariants

        inv = audit_panel_invariants(frame)
        return {
            "status": "dry_run",
            "rows": len(frame),
            "columns": len(frame.columns),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
            "invariants": inv,
        }
    result = save_panel(frame, name=panel_name, force=force)
    return {"status": "ok", **result}


def refresh_panel_tail(
    *,
    days: int = 14,
    panel_name: str = "NIFTY_2006_present",
    force: bool = False,
) -> dict[str, Any]:
    """Rebuild and merge the last *days* of panel rows into the production panel."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    window = max(1, days)
    end_day = date.fromisoformat(india_trading_date_iso()[:10])
    end = end_day.isoformat()
    start = (end_day - timedelta(days=window)).isoformat()
    existing = load_panel(panel_name)
    tail = build_history_panel(start=start, end=end, panel_name=panel_name)
    if tail.empty:
        return {"status": "error", "reason": "empty_tail", "panel": panel_name}
    if existing.empty:
        return materialize_panel(start="2006-01-01", end=end, panel_name=panel_name, force=force)

    cutoff = start[:10]
    kept = existing[existing["date"].astype(str).str[:10] < cutoff].copy()
    merged = pd.concat([kept, tail], ignore_index=True)
    merged["date"] = merged["date"].astype(str).str[:10]
    merged = merged.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    result = save_panel(merged, name=panel_name, force=force)
    return {"status": "ok", "mode": "tail_refresh", "window_days": window, **result}


def load_aligned_panel_history(
    *,
    days: int = 365,
    start: str | None = None,
    panel_name: str = "NIFTY_2006_present",
) -> pd.DataFrame:
    """Load materialized panel when available; otherwise build on the fly."""
    frame = load_panel(panel_name)
    from_materialized = not frame.empty
    if frame.empty:
        frame = build_history_panel(start=start or "2006-01-01")
    if frame.empty:
        return frame
    if start:
        frame = frame[frame["date"] >= start[:10]]
    if days > 0:
        frame = frame.tail(max(1, days))
    # Materialized panels are enriched at save time; re-running enrichment drifts invariants.
    if not frame.empty and not from_materialized:
        from trade_integrations.dataflows.index_research.panel_enrichment import enrich_prediction_panel

        frame = enrich_prediction_panel(frame, allow_live_fetch=False)
        frame = enrich_history_features(frame)
    return frame.reset_index(drop=True)
