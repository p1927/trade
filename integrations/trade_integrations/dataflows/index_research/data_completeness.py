"""Ensure FII/DII/PCR factor history meets minimum coverage before model use."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.dataflows.index_research.factor_store import load_factor_history
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

logger = logging.getLogger(__name__)

FLOW_FACTORS: tuple[str, ...] = ("fii_net_5d", "dii_net_5d", "nifty_pcr")
MIN_FLOW_COVERAGE_PCT = 90.0
DEFAULT_ENRICH_DAYS = 365


def measure_flow_coverage(
    *,
    days: int = DEFAULT_ENRICH_DAYS,
    allow_live_fetch: bool = False,
) -> dict[str, Any]:
    """Return per-factor non-null coverage (%) over the Nifty trading window.

    FII/DII gate uses *flow-era* coverage (days on/after first real cash flow row)
    so pre-history calendar days do not fail the gate when sources only publish ~6mo.
    """
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        flow_effective_start,
        merge_flow_derivatives_frame,
    )

    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"trading_days": 0, "factors": {}, "min_pct": 0.0, "passes_gate": False}

    trading_dates = nifty["date"].astype(str).str[:10].tolist()
    if not trading_dates:
        return {"trading_days": 0, "factors": {}, "min_pct": 0.0, "passes_gate": False}

    start, end = trading_dates[0], trading_dates[-1]
    long_df = load_factor_history(start, end)
    day_count = max(1, len(trading_dates))

    flow_frame = merge_flow_derivatives_frame(start, end, allow_live_fetch=allow_live_fetch)
    era_start = flow_effective_start(flow_frame)
    era_dates = [d for d in trading_dates if era_start is None or d >= era_start[:10]]
    era_day_count = max(1, len(era_dates))

    factors: dict[str, dict[str, Any]] = {}
    min_pct = 100.0

    for key in FLOW_FACTORS:
        if long_df.empty or "factor" not in long_df.columns:
            pct = 0.0
            days_present = 0
            era_present = 0
            era_pct = 0.0
        else:
            subset = long_df[long_df["factor"] == key]
            days_present = int(subset["value"].notna().sum()) if not subset.empty else 0
            pct = round(100.0 * days_present / day_count, 1)
            if key in {"fii_net_5d", "dii_net_5d"} and era_dates:
                era_subset = subset[subset["date"].astype(str).str[:10].isin(era_dates)]
                era_present = int(era_subset["value"].notna().sum()) if not era_subset.empty else 0
                era_pct = round(100.0 * era_present / era_day_count, 1)
            else:
                era_present = days_present
                era_pct = pct

        gate_pct = era_pct if key in {"fii_net_5d", "dii_net_5d"} else pct
        factors[key] = {
            "days_present": days_present,
            "coverage_pct": pct,
            "flow_era_days_present": era_present,
            "flow_era_coverage_pct": era_pct,
            "gate_coverage_pct": gate_pct,
        }
        min_pct = min(min_pct, gate_pct)

    passes = min_pct >= MIN_FLOW_COVERAGE_PCT
    return {
        "trading_days": day_count,
        "start": start,
        "end": end,
        "flow_effective_start": era_start,
        "factors": factors,
        "min_pct": min_pct,
        "passes_gate": passes,
        "gate_threshold_pct": MIN_FLOW_COVERAGE_PCT,
    }


def ensure_factor_data_complete(
    *,
    days: int = DEFAULT_ENRICH_DAYS,
    min_pct: float = MIN_FLOW_COVERAGE_PCT,
    force_enrich: bool = False,
    enrich: bool = True,
    allow_live_fetch: bool = False,
) -> dict[str, Any]:
    """Run factor enrichment when flow coverage is below threshold.

    When ``enrich=False`` (fast analysis), measure cached coverage only and never
    block on NiftyInvest / Mr. Chartist live backfill. Live HTTP is opt-in via
    ``allow_live_fetch=True`` (scheduled jobs / manual backfill only).
    """
    before = measure_flow_coverage(days=days, allow_live_fetch=False)
    enriched = False
    enrich_result: dict[str, Any] | None = None

    if enrich and (force_enrich or not before.get("passes_gate") or float(before.get("min_pct") or 0) < min_pct):
        try:
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            enrich_result = enrich_factor_history(days=days, allow_live_fetch=allow_live_fetch)
            enriched = True
            logger.info("factor enrichment completed: %s", enrich_result)
        except Exception as exc:
            logger.warning("factor enrichment failed: %s", exc)
            return {
                "enriched": False,
                "before": before,
                "after": before,
                "enrich_result": None,
                "error": str(exc),
                "passes_gate": bool(before.get("passes_gate")),
                "skipped_enrich": not enrich,
            }

    after = measure_flow_coverage(days=days, allow_live_fetch=False)
    return {
        "enriched": enriched,
        "before": before,
        "after": after,
        "enrich_result": enrich_result,
        "passes_gate": bool(after.get("passes_gate")),
        "skipped_enrich": not enrich and not enriched,
    }
