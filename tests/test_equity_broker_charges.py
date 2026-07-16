"""Tests for equity broker charges."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.broker_charges.calculate import calculate_equity_charges_for_legs


@pytest.mark.unit
class TestEquityBrokerCharges:
    def test_indmoney_cnc_buy_has_stamp_not_stt(self):
        legs = [
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "price": 1296.6,
                "quantity": 1,
                "product": "CNC",
            }
        ]
        out = calculate_equity_charges_for_legs(legs, broker="indmoney")
        row = out["per_leg"][0]
        assert row["stt"] == 0.0
        assert row["stamp"] > 0
        assert out["round_trip_charges"] > row["total_charges"]
        assert out["broker_preset"] == "indmoney"

    def test_round_trip_includes_exit_side(self):
        legs = [{"symbol": "TCS", "side": "BUY", "price": 4000, "quantity": 1, "product": "CNC"}]
        out = calculate_equity_charges_for_legs(legs, broker="indmoney", include_exit=True)
        assert out["exit_charges"] > 0
        assert out["round_trip_charges"] == pytest.approx(
            out["total"]["total_charges"] + out["exit_charges"],
            abs=0.01,
        )
