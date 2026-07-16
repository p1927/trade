"""Tests for Vibe trade-plan widget payload builder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.options_research.models import OptionsResearchDoc
from trade_integrations.dataflows.options_research.widget_payload import (
    build_options_trade_widget_from_doc,
)


def _sample_doc() -> OptionsResearchDoc:
    return OptionsResearchDoc(
        underlying="NIFTY",
        as_of=datetime(2026, 7, 16, tzinfo=timezone.utc),
        lookahead_days=14,
        instrument_type="index",
        market="IN",
        expiry="31JUL25",
        spot=24500.0,
        prediction={
            "view": "bullish",
            "iv_regime": "elevated",
            "confidence": 0.65,
            "earnings": {"beat_probability": 0.7},
            "corp_events": {"status": "no_data"},
        },
        scenarios=[
            {
                "name": "Spot up 2%",
                "probability": 0.35,
                "trigger": "Breakout above resistance",
                "strategy_hint": "bull call spread",
            },
            {
                "name": "Range bound",
                "probability": 0.4,
                "trigger": "IV crush post event",
                "strategy_hint": "iron condor",
            },
        ],
        ranked_strategies=[
            {
                "name": "Bull Call Spread",
                "tier": "recommended",
                "score": 82,
                "max_profit": 12000,
                "max_loss": 4500,
                "net_max_profit": 11500,
                "net_max_loss": 5000,
            },
            {"name": "Iron Condor", "tier": "alternative", "score": 71},
        ],
        recommended={
            "name": "Bull Call Spread",
            "tier": "recommended",
            "score": 82,
            "rationale": "Fits bullish view with defined risk",
            "legs": [
                {"side": "BUY", "symbol": "NIFTY31JUL25C24500", "quantity": 50, "price": 120},
                {"side": "SELL", "symbol": "NIFTY31JUL25C24700", "quantity": 50, "price": 45},
            ],
        },
        payoff={
            "max_profit": 12000,
            "max_loss": 4500,
            "net_max_profit": 11500,
            "net_max_loss": 5000,
            "samples": [
                {"spot": 24000, "pnl": -4500, "net_pnl": -5000},
                {"spot": 24500, "pnl": 0, "net_pnl": -250},
                {"spot": 25000, "pnl": 12000, "net_pnl": 11500},
            ],
        },
        payoff_over_time={
            "samples": [
                {"days_to_expiry": 14, "pnl": 2000, "net_pnl": 1800},
                {"days_to_expiry": 0, "pnl": 12000, "net_pnl": 11500},
            ]
        },
        charges={
            "net_debit_credit": 3750,
            "round_trip_charges": 420,
            "per_leg": [
                {"symbol": "NIFTY31JUL25C24500", "brokerage": 40, "stt": 0, "gst": 7},
            ],
        },
        implementation_steps=[
            {
                "step": 4,
                "action": "execute_basket",
                "payload": {
                    "orders": [
                        {"symbol": "NIFTY31JUL25C24500", "action": "BUY", "quantity": 50},
                    ]
                },
            }
        ],
        meta={
            "strategy_builder_url": "http://127.0.0.1:5001/strategy-builder?plan=NIFTY",
            "strategy_builder_execute_url": "http://127.0.0.1:5001/strategy-builder?plan=NIFTY&execute=1",
        },
    )


@pytest.mark.unit
class TestWidgetPayload:
    def test_build_widget_shape(self):
        widget = build_options_trade_widget_from_doc(_sample_doc())
        assert widget["type"] == "trade_plan.widget"
        assert widget["underlying"] == "NIFTY"
        assert widget["widget_id"].startswith("tp_NIFTY_")
        assert len(widget["scenarios"]) == 2
        assert widget["recommended"]["name"] == "Bull Call Spread"
        assert len(widget["payoff"]["samples"]) == 3
        assert widget["charges"]["net_debit_credit"] == 3750
        assert widget["implementation_steps"][0]["action"] == "execute_basket"

    def test_prediction_includes_signal_summaries(self):
        widget = build_options_trade_widget_from_doc(_sample_doc())
        pred = widget["prediction"]
        assert "70.0%" in pred["earnings_summary"]
        assert pred["view"] == "bullish"
