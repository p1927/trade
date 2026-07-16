"""Tests for stock trade-plan widget payload."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.stock_research.models import StockResearchDoc
from trade_integrations.dataflows.stock_research.widget_payload import (
    build_stock_trade_widget_from_doc,
)


def _sample_stock_doc() -> StockResearchDoc:
    return StockResearchDoc(
        ticker="RELIANCE",
        as_of=datetime(2026, 7, 16, tzinfo=timezone.utc),
        lookahead_days=14,
        spot=2850.0,
        prediction={"view": "bullish", "confidence": 0.72},
        scenarios=[
            {
                "name": "base_case",
                "probability": "medium",
                "trigger": "No catalyst",
                "strategy_hint": "buy_dip",
            }
        ],
        ranked_strategies=[
            {
                "name": "buy_dip",
                "action": "BUY",
                "score": 0.78,
                "tier": "Recommended",
                "rationale": "Pullback entry",
                "quantity": 1,
                "product": "CNC",
            },
            {"name": "hold_cash", "action": "HOLD", "score": 0.45, "tier": "Consider"},
        ],
        recommended={
            "name": "buy_dip",
            "action": "BUY",
            "score": 0.78,
            "tier": "Recommended",
            "quantity": 1,
        },
        charges={"net_debit_credit": 2850, "round_trip_charges": 120},
        payoff={"max_profit": 500, "max_loss": 200, "samples": [{"spot": 2850, "pnl": 0}]},
    )


@pytest.mark.unit
class TestStockWidgetPayload:
    def test_stock_widget_shape(self):
        widget = build_stock_trade_widget_from_doc(_sample_stock_doc())
        assert widget["type"] == "trade_plan.widget"
        assert widget["asset_type"] == "stock"
        assert widget["widget_id"].startswith("ts_RELIANCE_")
        assert "buy_dip" in widget["strategy_variants"]
        assert widget["strategy_variants"]["buy_dip"]["implementation_steps"][-1]["action"] == "execute_basket"
