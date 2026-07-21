"""Align live macro snapshots with panel-derived factors used in Ridge training."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.prediction_data_requirements import PANEL_DERIVED_FACTORS
from trade_integrations.dataflows.index_research.spread_features import SPREAD_OUTPUT_KEYS

# Derived columns that must match the materialized panel (train/serve parity).
LIVE_PANEL_PARITY_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *PANEL_DERIVED_FACTORS,
            *SPREAD_OUTPUT_KEYS,
            "constituent_momentum_7d",
            "nifty_return_7d",
            "nifty_return_14d",
        )
    )
)


def load_panel_factor_row(
    trading_day: str,
    *,
    panel_name: str = "NIFTY_2006_present",
    keys: tuple[str, ...] = LIVE_PANEL_PARITY_KEYS,
) -> dict[str, float]:
    """Return panel factor values for *trading_day* (or last row on/before that day)."""
    from trade_integrations.dataflows.index_research.history_store import load_panel

    frame = load_panel(panel_name)
    if frame.empty or "date" not in frame.columns:
        return {}

    subset = frame.copy()
    subset["date"] = subset["date"].astype(str).str[:10]
    day = str(trading_day)[:10]
    on_or_before = subset[subset["date"] <= day]
    if on_or_before.empty:
        return {}

    row = on_or_before.iloc[-1]
    out: dict[str, float] = {}
    for key in keys:
        if key not in row.index:
            continue
        try:
            val = float(row[key])
        except (TypeError, ValueError):
            continue
        if math.isnan(val) or math.isinf(val):
            continue
        out[key] = val
    return out


def merge_panel_parity_into_factors(
    factors: dict[str, Any],
    trading_day: str,
    *,
    panel_name: str = "NIFTY_2006_present",
    keys: tuple[str, ...] = LIVE_PANEL_PARITY_KEYS,
) -> tuple[dict[str, Any], list[str]]:
    """Overlay panel-derived values onto live factors; panel wins for parity keys."""
    panel_row = load_panel_factor_row(trading_day, panel_name=panel_name, keys=keys)
    if not panel_row:
        return factors, []

    merged = dict(factors)
    applied: list[str] = []
    for key, value in panel_row.items():
        merged[key] = value
        applied.append(key)
    return merged, applied


def upsert_factor_rows_for_parity(
    factor_rows: list[dict[str, Any]],
    factors: dict[str, Any],
    applied: list[str],
    *,
    source: str = "panel_parity",
) -> list[dict[str, Any]]:
    """Replace factor_rows entries for keys applied from the panel."""
    if not applied:
        return factor_rows

    from trade_integrations.dataflows.index_research.explain import _FACTOR_LABELS

    applied_set = frozenset(applied)
    kept = [row for row in factor_rows if str(row.get("factor") or "") not in applied_set]
    for key in applied:
        if key not in factors:
            continue
        kept.append(
            {
                "factor": key,
                "label": _FACTOR_LABELS.get(key) or key.replace("_", " ").title(),
                "value": factors[key],
                "source": source,
            }
        )
    return kept
