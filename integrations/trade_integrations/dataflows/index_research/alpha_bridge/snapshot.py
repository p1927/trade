"""Daily Alpha Zoo composite snapshot for the index factor store."""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

from trade_integrations.dataflows.index_research.alpha_bridge.compute import (
    COMPOSITE_KEYS,
    compute_composites_from_panel,
)
from trade_integrations.dataflows.index_research.alpha_bridge.config import is_bridge_enabled
from trade_integrations.dataflows.index_research.alpha_bridge.panel import build_nifty50_panel
from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors

logger = logging.getLogger(__name__)

_SOURCE = "alpha_zoo_bridge"


def _today() -> str:
    return date.today().isoformat()


def compute_alpha_zoo_snapshot(
    *,
    as_of_day: str | None = None,
) -> list[dict[str, Any]]:
    """Return factor rows for alpha_zoo_* composites (empty when bridge disabled)."""
    if not is_bridge_enabled():
        return []

    day = (as_of_day or _today())[:10]
    try:
        panel = build_nifty50_panel(as_of_day=day)
        composites = compute_composites_from_panel(panel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alpha_zoo snapshot failed: %s", exc)
        return []

    rows: list[dict[str, Any]] = []
    for key in COMPOSITE_KEYS:
        raw = composites.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        rows.append({"factor": key, "value": value, "source": _SOURCE})
    return rows


def maybe_persist_alpha_zoo_factors(*, as_of_day: str | None = None) -> list[dict[str, Any]]:
    """Compute and upsert alpha_zoo rows when bridge is enabled."""
    rows = compute_alpha_zoo_snapshot(as_of_day=as_of_day)
    if not rows:
        return []
    day = (as_of_day or _today())[:10]
    try:
        upsert_daily_factors(day, rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alpha_zoo factor upsert failed: %s", exc)
    return rows


def apply_alpha_zoo_to_macro(
    macro_factors: dict[str, Any],
    global_factors: list[dict[str, Any]],
    *,
    as_of_day: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge alpha_zoo composites into live macro dicts (no-op when disabled)."""
    rows = maybe_persist_alpha_zoo_factors(as_of_day=as_of_day)
    if not rows:
        return macro_factors, global_factors

    out_macro = dict(macro_factors)
    out_global = list(global_factors)
    for row in rows:
        key = str(row.get("factor") or "")
        val = row.get("value")
        if not key or val is None:
            continue
        out_macro[key] = float(val)
        out_global.append(row)
    return out_macro, out_global
