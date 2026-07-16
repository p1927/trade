"""Tests for stock widget presentability gates."""

from __future__ import annotations

import pytest

from trade_integrations.trade_widgets.presentability import is_widget_presentable


def _ready_stock_widget() -> dict:
    return {
        "type": "trade_plan.widget",
        "asset_type": "stock",
        "plan_status": "ready",
        "prediction": {
            "view": "bullish",
            "range": {"low": 1280.0, "high": 1320.0},
            "provenance": {"direction": "debate", "range": "quant"},
        },
        "recommended": {
            "action": "BUY",
            "max_profit": 500.0,
            "max_loss": 200.0,
            "legs": [{"symbol": "RELIANCE", "side": "BUY", "quantity": 1}],
        },
        "charges": {
            "round_trip_charges": 1.6,
            "per_leg": [{"leg": 1, "brokerage": 0.39}],
        },
    }


@pytest.mark.unit
class TestStockWidgetPresentability:
    def test_ready_stock_passes(self):
        assert is_widget_presentable(_ready_stock_widget(), "stock_strategy") is True

    def test_missing_range_fails(self):
        widget = _ready_stock_widget()
        widget["prediction"]["range"] = {}
        assert is_widget_presentable(widget, "stock_strategy") is False

    def test_missing_charges_fails(self):
        widget = _ready_stock_widget()
        widget["charges"] = {}
        assert is_widget_presentable(widget, "stock_strategy") is False

    def test_partial_status_fails(self):
        widget = _ready_stock_widget()
        widget["plan_status"] = "partial"
        assert is_widget_presentable(widget, "stock_strategy") is False
