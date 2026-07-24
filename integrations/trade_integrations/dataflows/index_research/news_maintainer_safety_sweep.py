"""Maintainer pass: bounded post-upsert safety scan over recent events."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_maintenance_safety_sweep(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 7,
    max_events: int = 200,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run single-pass safety scan for recent events (complement to write-path scan)."""
    from trade_integrations.dataflows.index_research.news_post_upsert_safety import (
        post_upsert_safety_enabled,
        run_post_upsert_safety_scan,
    )
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.hub_storage.news_events_store import list_events
    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    sym = ticker.strip().upper()
    if not post_upsert_safety_enabled():
        return {"ticker": sym, "skipped": True, "reason": "disabled"}

    pause = pipeline_pause_status(ticker=sym)
    if pause.get("pipeline_paused"):
        return {
            "ticker": sym,
            "skipped": True,
            "pipeline_paused": True,
            "pause_reason": str(pause.get("pause_reason") or ""),
        }

    from datetime import date, timedelta

    end = date.fromisoformat(india_trading_date_iso()[:10])
    since = (end - timedelta(days=max(lookback_days, 1))).isoformat()
    events = list_events(ticker=sym, since=since, limit=max_events, include_rejected=False)

    groups_merged = 0
    rows_removed = 0
    scanned = 0
    errors = 0

    for event in events:
        eid = str(event.get("event_id") or "").strip()
        if not eid:
            continue
        scanned += 1
        try:
            result = run_post_upsert_safety_scan(
                eid,
                ticker=sym,
                lookback_days=lookback_days,
                dry_run=dry_run,
            )
            groups_merged += int(result.get("groups_merged") or 0)
            rows_removed += int(result.get("rows_removed") or 0)
        except Exception as exc:
            errors += 1
            logger.warning("safety sweep failed for %s: %s", eid, exc)

    return {
        "ticker": sym,
        "lookback_days": lookback_days,
        "events_scanned": scanned,
        "groups_merged": groups_merged,
        "rows_removed": rows_removed,
        "errors": errors,
        "dry_run": dry_run,
    }
