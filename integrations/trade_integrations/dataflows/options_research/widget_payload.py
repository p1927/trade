"""Structured trade-plan widget payload for Vibe chat cards."""

from __future__ import annotations

import uuid
from typing import Any

from trade_integrations.context.hub import load_options_research_json
from trade_integrations.dataflows.company_research.signals_bridge import (
    format_corp_events_section,
    format_earnings_signal_section,
)
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.dataflows.options_research.models import OptionsResearchDoc


def _payoff_samples(payoff: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payoff:
        return []
    samples = payoff.get("samples") or payoff.get("curve") or []
    if isinstance(samples, list):
        return [
            {
                "spot": row.get("spot") or row.get("x"),
                "pnl": row.get("pnl") or row.get("y"),
                "net_pnl": row.get("net_pnl"),
            }
            for row in samples
            if isinstance(row, dict)
        ]
    return []


def _pnl_over_time_samples(doc: OptionsResearchDoc) -> list[dict[str, Any]]:
    pot = doc.payoff_over_time or {}
    samples = pot.get("samples") or []
    return [
        {
            "days_to_expiry": s.get("days_to_expiry"),
            "pnl": s.get("pnl"),
            "net_pnl": s.get("net_pnl"),
        }
        for s in samples
        if isinstance(s, dict)
    ]


def build_options_trade_widget_from_doc(doc: OptionsResearchDoc) -> dict[str, Any]:
    """Build Vibe ``trade_plan.widget`` payload from an options research doc."""
    pred = doc.prediction or {}
    rec = doc.recommended or {}
    charges = doc.charges or {}
    payoff = doc.payoff or {}
    widget_id = f"tp_{doc.underlying}_{uuid.uuid4().hex[:12]}"

    return {
        "type": "trade_plan.widget",
        "widget_id": widget_id,
        "underlying": doc.underlying,
        "instrument_type": doc.instrument_type,
        "market": doc.market,
        "as_of": doc.as_of.isoformat(),
        "expiry": doc.expiry,
        "spot": doc.spot,
        "prediction": {
            "view": pred.get("view"),
            "iv_regime": pred.get("iv_regime"),
            "expected_move_pct": pred.get("expected_move_pct"),
            "confidence": pred.get("confidence"),
            "signals": pred.get("signals") or {},
            "earnings_summary": format_earnings_signal_section(pred.get("earnings")).strip(),
            "corp_events_summary": format_corp_events_section(pred.get("corp_events")).strip(),
        },
        "events": doc.events[:12],
        "scenarios": doc.scenarios[:6],
        "ranked_strategies": [
            {
                "name": s.get("name"),
                "tier": s.get("tier"),
                "score": s.get("score"),
                "pop": s.get("pop"),
                "event_fit": s.get("event_fit"),
                "signal_fit": s.get("signal_fit"),
                "max_profit": s.get("max_profit"),
                "max_loss": s.get("max_loss"),
                "net_max_profit": s.get("net_max_profit"),
                "net_max_loss": s.get("net_max_loss"),
                "rationale": (s.get("rationale") or "")[:200],
            }
            for s in (doc.ranked_strategies or [])[:5]
        ],
        "recommended": rec,
        "payoff": {
            "breakevens": payoff.get("breakevens"),
            "gross_max_profit": payoff.get("gross_max_profit") or payoff.get("max_profit"),
            "gross_max_loss": payoff.get("gross_max_loss") or payoff.get("max_loss"),
            "net_max_profit": payoff.get("net_max_profit"),
            "net_max_loss": payoff.get("net_max_loss"),
            "samples": _payoff_samples(payoff),
        },
        "payoff_over_time": {"samples": _pnl_over_time_samples(doc)},
        "charges": {
            "per_leg": (charges.get("per_leg") or charges.get("legs") or [])[:8],
            "total": charges.get("total"),
            "net_debit_credit": charges.get("net_debit_credit"),
            "round_trip_charges": charges.get("round_trip_charges"),
            "exit": charges.get("exit"),
        },
        "implementation_steps": doc.implementation_steps or [],
        "meta": dict(doc.meta or {}),
        "browse_summary": doc.browse_summary or {},
    }


def build_options_trade_widget(
    ticker: str,
    *,
    expiry_date: str | None = None,
    lookahead_days: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Load or run options research and return widget payload."""
    if not refresh:
        cached = load_options_research_json(ticker)
        if cached is not None:
            return build_options_trade_widget_from_doc(cached)
    doc = run_options_research(
        ticker,
        expiry_date=expiry_date,
        lookahead_days=lookahead_days,
    )
    return build_options_trade_widget_from_doc(doc)
