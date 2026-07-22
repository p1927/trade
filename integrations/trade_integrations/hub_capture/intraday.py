"""Intraday capture job — fetch OpenAlgo chain for registered entities."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.options_research.market import resolve_options_instrument
from trade_integrations.dataflows.options_research.sources.chain_openalgo import fetch_chain_stage
from trade_integrations.hub_capture.gate import should_capture
from trade_integrations.hub_capture.registry import load_registry
from trade_integrations.hub_capture.writers import record_chain_snapshot

logger = logging.getLogger(__name__)


def run_intraday_capture(*, entity_id: str | None = None) -> dict[str, Any]:
    """Fetch and persist option chain for each capture-enabled entity (v1: NIFTY)."""
    from trade_integrations.stock_simulator.integration import hub_no_learn

    if hub_no_learn():
        return {"status": "skipped", "reason": "hub_no_learn", "entities": {}}
    reg = load_registry(create=False)
    entities = reg.get("entities") or []
    if entity_id:
        entities = [e for e in entities if str(e.get("id") or "").upper() == entity_id.strip().upper()]
    results: dict[str, Any] = {"captured_at": datetime.now(timezone.utc).isoformat(), "entities": {}}
    for entity in entities:
        eid = str(entity.get("id") or "NIFTY").upper()
        if not entity.get("capture_enabled"):
            results["entities"][eid] = {"status": "skipped", "reason": "disabled"}
            continue
        if not should_capture(eid, "derivatives_chain", registry=reg):
            results["entities"][eid] = {"status": "skipped", "reason": "gate_closed"}
            continue
        try:
            instrument = resolve_options_instrument(eid)
            stage = fetch_chain_stage(instrument, strike_count=15)
            if stage.status == "error" or not stage.data.get("chain"):
                results["entities"][eid] = {
                    "status": "error",
                    "vendor": stage.vendor,
                    "errors": stage.errors,
                }
                continue
            capture = record_chain_snapshot(
                eid,
                stage.data,
                source=str(stage.data.get("source") or stage.vendor),
                vendor=stage.vendor,
                captured_at=stage.fetched_at.isoformat() if stage.fetched_at else None,
            )
            results["entities"][eid] = {"status": "ok", "vendor": stage.vendor, "capture": capture}
        except Exception as exc:
            logger.exception("intraday capture failed for %s", eid)
            results["entities"][eid] = {"status": "error", "error": str(exc)}
    results["status"] = "ok" if any(
        v.get("status") == "ok" for v in results["entities"].values()
    ) else "partial"
    return results


def run_capture_backfill(*, entity_id: str = "NIFTY", days: int = 365) -> dict[str, Any]:
    """Backfill participant OI and NSE flow/derivatives history into hub."""
    if not should_capture(entity_id, "derivatives_chain") and not should_capture(entity_id, "flows"):
        return {"status": "skipped", "reason": "capture_disabled"}
    summary: dict[str, Any] = {"entity_id": entity_id, "steps": {}}
    try:
        from trade_integrations.dataflows.index_research.participant_oi_backfill import (
            backfill_participant_oi,
        )

        summary["steps"]["participant_oi"] = backfill_participant_oi(days=days)
    except Exception as exc:
        logger.exception("participant OI backfill failed")
        summary["steps"]["participant_oi"] = {"status": "error", "error": str(exc)}
    try:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        summary["steps"]["factor_enrichment"] = enrich_factor_history(days=days)
    except Exception as exc:
        logger.exception("factor enrichment backfill failed")
        summary["steps"]["factor_enrichment"] = {"status": "error", "error": str(exc)}
    summary["status"] = "ok"
    return summary
