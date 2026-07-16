"""Structured index research widget payload for Vibe chat cards."""

from __future__ import annotations

import uuid
from typing import Any

from trade_integrations.context.hub import load_index_research_json
from trade_integrations.dataflows.index_research.aggregator import run_index_research
from trade_integrations.dataflows.index_research.models import IndexResearchDoc


def build_index_trade_widget_from_doc(
    doc: IndexResearchDoc,
    *,
    widget_intent: str | None = None,
) -> dict[str, Any]:
    """Build Vibe ``trade_plan.widget`` payload from an index research doc."""
    widget_id = f"ti_{doc.ticker}_{uuid.uuid4().hex[:12]}"
    prediction = doc.prediction or {}
    range_block = prediction.get("range") or {}

    scenarios = [
        {
            "name": f"{s.get('event', 'event')} — {s.get('outcome', '')}".strip(" —"),
            "probability": s.get("probability"),
            "trigger": s.get("outcome"),
            "strategy_hint": s.get("event"),
            "index_range": s.get("index_range"),
        }
        for s in (doc.scenarios or [])
    ]

    payload = {
        "type": "trade_plan.widget",
        "widget_id": widget_id,
        "asset_type": "index",
        "underlying": doc.ticker,
        "instrument_type": "index",
        "market": "IN",
        "spot": doc.spot,
        "plan_status": doc.plan_status if hasattr(doc, "plan_status") else "ready",
        "prediction": {
            "view": prediction.get("view"),
            "confidence": (range_block.get("confidence") if isinstance(range_block, dict) else None),
            "expected_return_pct": prediction.get("expected_return_pct"),
            "range_low": range_block.get("low"),
            "range_high": range_block.get("high"),
            "signals": {
                "macro_delta_pct": prediction.get("macro_delta_pct"),
                "bottom_up_return_pct": prediction.get("bottom_up_return_pct"),
            },
        },
        "scenarios": scenarios,
        "regime": doc.regime,
        "factor_explanation": doc.factor_explanation,
        "factor_sensitivity": doc.factor_sensitivity,
        "event_impact_curves": doc.event_impact_curves,
        "constituent_signals": doc.constituent_signals[:10],
        "accuracy": doc.accuracy,
        "browse_summary": {"spot": doc.spot},
    }
    from trade_integrations.trade_widgets.presentability import apply_widget_metadata

    return apply_widget_metadata(payload, widget_intent)


def build_index_trade_widget(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    refresh: bool = False,
    widget_intent: str | None = None,
) -> dict[str, Any]:
    """Load or run index research and return widget payload."""
    sym = ticker.strip().upper()
    if not refresh:
        cached = load_index_research_json(sym)
        if cached and cached.factor_explanation:
            return build_index_trade_widget_from_doc(cached, widget_intent=widget_intent)
    doc = run_index_research(sym, horizon_days=horizon_days, refresh_constituents=refresh)
    return build_index_trade_widget_from_doc(doc, widget_intent=widget_intent)
