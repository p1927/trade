"""Historical backfill of alpha_zoo composite factors."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from trade_integrations.dataflows.index_research.alpha_bridge.compute import (
    COMPOSITE_KEYS,
    compute_composites_history,
)
from trade_integrations.dataflows.index_research.alpha_bridge.config import (
    basket_alpha_ids,
    is_bridge_enabled,
    lookback_days,
)
from trade_integrations.dataflows.index_research.alpha_bridge.panel import build_nifty50_panel
from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors

logger = logging.getLogger(__name__)

_SOURCE = "alpha_zoo_bridge_backfill"


def backfill_alpha_zoo_history(*, days: int = 365) -> dict[str, int | str]:
    """Backfill daily alpha_zoo composite factors over ``days`` trading history."""
    if not is_bridge_enabled():
        return {"status": "skipped", "reason": "bridge_disabled", "days_written": 0}

    end = date.today()
    start = end - timedelta(days=max(int(days), 30))
    panel = build_nifty50_panel(
        as_of_day=end.isoformat(),
        lookback=max(int(days) + lookback_days(), 120),
    )
    history = compute_composites_history(panel, alpha_ids=basket_alpha_ids())
    if history.empty:
        return {"status": "empty", "days_written": 0}

    history["date"] = pd.to_datetime(history["date"]).dt.date
    mask = (history["date"] >= start) & (history["date"] <= end)
    history = history.loc[mask]

    written = 0
    for _, row in history.iterrows():
        day = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])[:10]
        rows = []
        for key in COMPOSITE_KEYS:
            if key not in row or pd.isna(row[key]):
                continue
            rows.append(
                {
                    "factor": key,
                    "value": float(row[key]),
                    "source": _SOURCE,
                }
            )
        if rows:
            upsert_daily_factors(day, rows)
            written += 1

    return {"status": "ok", "days_written": written, "start": start.isoformat(), "end": end.isoformat()}
