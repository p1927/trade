#!/usr/bin/env python3
"""Extend prediction data audit with news, shock calibration, debate archive, and flow parity."""

from __future__ import annotations

from typing import Any

import pandas as pd

_PARITY_WARN_DELTA_PCT = 5.0


def _pcr_coverage_pct(frame: pd.DataFrame, *, date_col: str = "date", pcr_col: str = "nifty_pcr") -> float:
    if frame.empty or pcr_col not in frame.columns:
        return 0.0
    valid = pd.to_numeric(frame[pcr_col], errors="coerce").notna()
    return round(100.0 * valid.sum() / max(1, len(frame)), 1)


def audit_flow_parity(
    panel: pd.DataFrame,
    *,
    days: int = 365,
    allow_live_fetch: bool = False,
) -> dict[str, Any]:
    """Report panel vs factor-store vs cold-tier PCR coverage side-by-side."""
    from trade_integrations.dataflows.index_research.factor_store import load_factor_history
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        merge_flow_derivatives_frame,
        pcr_effective_start,
    )

    nifty = load_nifty_history(days=days)
    if nifty.empty or panel.empty:
        return {"status": "empty", "warnings": ["no_panel_or_nifty"]}

    trading_dates = nifty["date"].astype(str).str[:10].tolist()
    start, end = trading_dates[0], trading_dates[-1]
    pcr_start = pcr_effective_start(merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch))

    panel_slice = panel.copy()
    panel_slice["date"] = panel_slice["date"].astype(str).str[:10]
    panel_slice = panel_slice[(panel_slice["date"] >= start) & (panel_slice["date"] <= end)]
    if pcr_start:
        panel_slice = panel_slice[panel_slice["date"] >= pcr_start[:10]]

    panel_pct = _pcr_coverage_pct(panel_slice)

    cold = load_history_dataset("flow_derivatives_daily")
    cold_pct = 0.0
    if not cold.empty and "nifty_pcr" in cold.columns:
        cold_slice = cold.copy()
        cold_slice["date"] = cold_slice["date"].astype(str).str[:10]
        cold_slice = cold_slice[(cold_slice["date"] >= start) & (cold_slice["date"] <= end)]
        if pcr_start:
            cold_slice = cold_slice[cold_slice["date"] >= pcr_start[:10]]
        cold_pct = _pcr_coverage_pct(cold_slice)

    factor_pct = 0.0
    long_df = load_factor_history(start, end)
    if not long_df.empty and "factor" in long_df.columns:
        pcr = long_df[long_df["factor"] == "nifty_pcr"]
        era_dates = [d for d in trading_dates if pcr_start is None or d >= pcr_start[:10]]
        era_count = max(1, len(era_dates))
        if not pcr.empty:
            era_subset = pcr[pcr["date"].astype(str).str[:10].isin(era_dates)]
            factor_pct = round(100.0 * era_subset["value"].notna().sum() / era_count, 1)

    merge_frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    merge_pct = 0.0
    if not merge_frame.empty and "nifty_pcr" in merge_frame.columns:
        merge_slice = merge_frame.copy()
        merge_slice["date"] = merge_slice["date"].astype(str).str[:10]
        if pcr_start:
            merge_slice = merge_slice[merge_slice["date"] >= pcr_start[:10]]
        merge_pct = _pcr_coverage_pct(merge_slice)

    warnings: list[str] = []
    pairs = (
        ("panel_vs_cold", panel_pct, cold_pct),
        ("panel_vs_factor", panel_pct, factor_pct),
        ("factor_vs_cold", factor_pct, cold_pct),
    )
    for label, left, right in pairs:
        if abs(left - right) > _PARITY_WARN_DELTA_PCT:
            warnings.append(f"{label}_delta_{abs(left - right):.1f}pct")

    return {
        "window_days": days,
        "start": start,
        "end": end,
        "pcr_effective_start": pcr_start,
        "panel_pcr_coverage_pct": panel_pct,
        "cold_tier_pcr_coverage_pct": cold_pct,
        "factor_store_pcr_coverage_pct": factor_pct,
        "merge_frame_pcr_coverage_pct": merge_pct,
        "parity_warn_delta_pct": _PARITY_WARN_DELTA_PCT,
        "parity_ok": len(warnings) == 0,
        "warnings": warnings,
    }


def audit_ltim_status() -> dict[str, Any]:
    from trade_integrations.openalgo.symbols import _INDMONEY_UNAVAILABLE

    return {
        "ltim_status": "unavailable_indmoney",
        "excluded_symbols": sorted(_INDMONEY_UNAVAILABLE),
        "bottom_up_note": "LTIM excluded from bottom-up weight normalization",
    }


def audit_news_pipeline(*, ticker: str = "NIFTY") -> dict[str, Any]:
    from trade_integrations.context.hub import count_agent_debate_history
    from trade_integrations.dataflows.index_research.news_event_features import (
        load_news_model_config,
    )
    from trade_integrations.dataflows.index_research.news_shock_calibration import (
        load_shock_calibration,
    )
    from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
        debate_backtest_eligible,
    )

    shock = load_shock_calibration(ticker) or {}
    topics = shock.get("topics") or {}
    config = load_news_model_config(ticker)
    debate_count = count_agent_debate_history(ticker)
    return {
        "news_model_config": config,
        "shock_calibration_topics": len(topics),
        "shock_reconciled_total": shock.get("reconciled_total"),
        "debate_history_count": debate_count,
        "debate_backtest_eligible": debate_backtest_eligible(ticker),
        "debate_archive_min_dates": 60,
    }
