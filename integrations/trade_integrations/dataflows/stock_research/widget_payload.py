"""Structured stock trade-plan widget payload for Vibe chat cards."""

from __future__ import annotations

import uuid
from typing import Any

from trade_integrations.context.hub import load_stock_research_json
from trade_integrations.dataflows.stock_research.aggregator import run_stock_research
from trade_integrations.dataflows.stock_research.models import StockResearchDoc


def _payoff_samples(payoff: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payoff:
        return []
    samples = payoff.get("samples") or payoff.get("curve") or []
    if not isinstance(samples, list):
        return []
    return [
        {
            "spot": row.get("spot") or row.get("underlying") or row.get("price") or row.get("x"),
            "pnl": row.get("pnl") or row.get("y"),
            "net_pnl": row.get("net_pnl"),
        }
        for row in samples
        if isinstance(row, dict) and (row.get("spot") or row.get("underlying") or row.get("price") or row.get("x")) is not None
    ]


def _pnl_over_time_samples(pot: dict[str, Any] | None) -> list[dict[str, Any]]:
    samples = (pot or {}).get("samples") or []
    return [
        {
            "days_to_expiry": s.get("days_to_expiry") or s.get("day"),
            "pnl": s.get("pnl"),
            "net_pnl": s.get("net_pnl"),
        }
        for s in samples
        if isinstance(s, dict)
    ]


def _stock_execute_steps(sym: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    action = str(row.get("action") or "BUY").upper()
    qty = int(row.get("quantity") or 1)
    product = str(row.get("product") or "CNC")
    return [
        {"step": 1, "action": "preview", "description": "Review entry, target, stop"},
        {"step": 2, "action": "confirm", "description": "User confirms stock order"},
        {
            "step": 3,
            "action": "execute_basket",
            "description": "Place equity order via OpenAlgo",
            "mcp_tool": "place_basket_order",
            "payload": {
                "orders": [
                    {
                        "symbol": sym,
                        "exchange": "NSE",
                        "action": action,
                        "quantity": str(qty),
                        "product": product,
                        "pricetype": "MARKET",
                    }
                ]
            },
        },
    ]


def _strategy_variants(doc: StockResearchDoc) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    sym = doc.ticker
    for row in (doc.ranked_strategies or [])[:5]:
        name = row.get("name")
        if not name or name in variants:
            continue
        rec = {
            "name": name,
            "score": row.get("score"),
            "tier": row.get("tier"),
            "rationale": row.get("rationale"),
            "action": row.get("action"),
            "quantity": row.get("quantity"),
            "product": row.get("product"),
            "target": row.get("target"),
            "stop": row.get("stop"),
            "legs": [
                {
                    "symbol": sym,
                    "side": row.get("action", "BUY"),
                    "quantity": row.get("quantity", 1),
                    "price": doc.spot,
                }
            ],
            "max_profit": row.get("max_profit"),
            "max_loss": row.get("max_loss"),
            "net_max_profit": row.get("net_max_profit"),
            "net_max_loss": row.get("net_max_loss"),
        }
        payoff = row.get("payoff") or doc.payoff or {}
        charges = row.get("charges") or doc.charges or {}
        variants[name] = {
            "recommended": rec,
            "payoff": {
                "gross_max_profit": payoff.get("max_profit"),
                "gross_max_loss": payoff.get("max_loss"),
                "net_max_profit": payoff.get("net_max_profit"),
                "net_max_loss": payoff.get("net_max_loss"),
                "samples": _payoff_samples(payoff),
            },
            "charges": {
                "per_leg": (charges.get("per_leg") or [])[:4],
                "net_debit_credit": charges.get("net_debit_credit"),
                "round_trip_charges": charges.get("round_trip_charges"),
            },
            "payoff_over_time": {"samples": _pnl_over_time_samples(row.get("payoff_over_time"))},
            "implementation_steps": _stock_execute_steps(sym, row),
        }
    return variants


def build_stock_trade_widget_from_doc(
    doc: StockResearchDoc,
    *,
    widget_intent: str | None = None,
) -> dict[str, Any]:
    """Build Vibe ``trade_plan.widget`` payload from a stock research doc."""
    rec = doc.recommended or {}
    charges = doc.charges or {}
    payoff = doc.payoff or {}
    ranked = doc.ranked_strategies or []
    widget_id = f"ts_{doc.ticker}_{uuid.uuid4().hex[:12]}"
    variants = _strategy_variants(doc)
    agent_recommended = rec.get("name") or (ranked[0].get("name") if ranked else "")

    payload = {
        "type": "trade_plan.widget",
        "widget_id": widget_id,
        "asset_type": "stock",
        "underlying": doc.ticker,
        "instrument_type": "stock",
        "market": doc.market,
        "as_of": doc.as_of.isoformat(),
        "spot": doc.spot,
        "plan_status": (
            "ready"
            if (doc.recommended and doc.payoff and doc.charges and (doc.prediction or {}).get("provenance"))
            else "partial"
        ),
        "prediction": doc.prediction or {},
        "events": doc.events[:12],
        "scenarios": doc.scenarios[:6],
        "agent_recommended_strategy": agent_recommended,
        "strategy_variants": variants,
        "ranked_strategies": [
            {
                "name": s.get("name"),
                "tier": s.get("tier"),
                "score": s.get("score"),
                "action": s.get("action"),
                "rationale": (s.get("rationale") or "")[:200],
            }
            for s in ranked[:5]
        ],
        "recommended": rec,
        "payoff": {
            "gross_max_profit": payoff.get("max_profit"),
            "gross_max_loss": payoff.get("max_loss"),
            "net_max_profit": payoff.get("net_max_profit"),
            "net_max_loss": payoff.get("net_max_loss"),
            "samples": _payoff_samples(payoff),
        },
        "payoff_over_time": {"samples": _pnl_over_time_samples(doc.payoff_over_time)},
        "charges": {
            "per_leg": (charges.get("per_leg") or [])[:4],
            "net_debit_credit": charges.get("net_debit_credit"),
            "round_trip_charges": charges.get("round_trip_charges"),
        },
        "implementation_steps": doc.implementation_steps or [],
        "meta": dict(doc.meta or {}),
        "browse_summary": doc.browse_summary or {},
    }
    from trade_integrations.trade_widgets.presentability import apply_widget_metadata

    return apply_widget_metadata(payload, widget_intent)


def build_stock_trade_widget(
    ticker: str,
    *,
    lookahead_days: int = 14,
    refresh: bool = False,
    widget_intent: str | None = None,
) -> dict[str, Any]:
    """Load or run stock research and return widget payload."""
    from trade_integrations.research.orchestrator import ensure_research_complete
    from trade_integrations.research.registry import ResearchKind

    result = ensure_research_complete(
        ticker,
        kind=ResearchKind.STOCK,
        refresh=refresh,
        horizon_days=lookahead_days,
    )
    doc = result.doc
    if doc is None:
        cached = load_stock_research_json(ticker)
        if cached is not None:
            doc = cached
        else:
            doc = run_stock_research(ticker, lookahead_days=lookahead_days)
    return build_stock_trade_widget_from_doc(doc, widget_intent=widget_intent)
