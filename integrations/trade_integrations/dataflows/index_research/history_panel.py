"""Materialized wide factor panel joining cold-tier history datasets."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    load_panel,
    save_panel,
)
from trade_integrations.dataflows.index_research.sources.history_loader import enrich_history_features


def _join_annual_macro_by_year(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach india_macro_annual columns by calendar year on daily rows."""
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
    ]
    if not join_cols:
        return frame

    macro = macro[["year"] + join_cols].drop_duplicates("year", keep="last")
    out = frame.copy()
    out["_year"] = out["date"].astype(str).str[:4].astype(int)
    overlap = set(out.columns) & set(join_cols) - {"_year"}
    if overlap:
        out = out.drop(columns=list(overlap), errors="ignore")
    out = out.merge(macro, left_on="_year", right_on="year", how="left")
    out = out.drop(columns=["_year", "year"], errors="ignore")
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
) -> dict[str, Any]:
    frame = build_history_panel(start=start, end=end, panel_name=panel_name)
    if frame.empty:
        return {"status": "error", "reason": "empty_panel", "panel": panel_name}
    if dry_run:
        return {
            "status": "dry_run",
            "rows": len(frame),
            "columns": len(frame.columns),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        }
    result = save_panel(frame, name=panel_name)
    return {"status": "ok", **result}


def load_aligned_panel_history(
    *,
    days: int = 365,
    start: str | None = None,
    panel_name: str = "NIFTY_2006_present",
) -> pd.DataFrame:
    """Load materialized panel when available; otherwise build on the fly."""
    frame = load_panel(panel_name)
    if frame.empty:
        frame = build_history_panel(start=start or "2006-01-01")
    if frame.empty:
        return frame
    if start:
        frame = frame[frame["date"] >= start[:10]]
    if days > 0:
        frame = frame.tail(max(1, days))
    return frame.reset_index(drop=True)
