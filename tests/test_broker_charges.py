"""Tests for broker charge presets (Groww, INDmoney, Zerodha)."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.broker_charges.calculate import (
    calculate_charges_for_legs,
    calculate_charges_with_exit_for_legs,
    normalize_broker_id,
)


NIFTY_STRADDLE_LEGS = [
    {
        "side": "BUY",
        "segment": "OPTION",
        "option_type": "CE",
        "strike": 24100.0,
        "price": 133.05,
        "symbol": "NIFTY21JUL2624100CE",
        "lot_size": 65,
        "lots": 1,
        "quantity": 65,
    },
    {
        "side": "BUY",
        "segment": "OPTION",
        "option_type": "PE",
        "strike": 24100.0,
        "price": 174.55,
        "symbol": "NIFTY21JUL2624100PE",
        "lot_size": 65,
        "lots": 1,
        "quantity": 65,
    },
]


@pytest.mark.unit
class TestBrokerCharges:
    def test_normalize_broker_aliases(self):
        assert normalize_broker_id("INDmoney") == "indmoney"
        assert normalize_broker_id("groww") == "groww"
        assert normalize_broker_id("kite") == "zerodha"
        assert normalize_broker_id(None) == "indmoney"

    @pytest.mark.parametrize("broker", ["indmoney", "groww", "zerodha"])
    def test_nifty_straddle_matches_hub_reference(self, broker: str):
        """ATM straddle entry charges ~₹56 (hub reference used fallback schedule)."""
        charges = calculate_charges_for_legs(NIFTY_STRADDLE_LEGS, broker=broker)
        assert charges["charge_source"] == "presets"
        assert charges["broker_preset"] == broker
        total = charges["total"]["total_charges"]
        assert 55.5 <= total <= 56.5
        assert charges["total"]["brokerage"] == 40.0
        assert charges["total"]["stt"] == 0.0
        assert len(charges["per_leg"]) == 2

    def test_groww_and_indmoney_same_statutory_brokerage(self):
        groww = calculate_charges_for_legs(NIFTY_STRADDLE_LEGS, broker="groww")
        ind = calculate_charges_for_legs(NIFTY_STRADDLE_LEGS, broker="indmoney")
        assert groww["total"] == ind["total"]
        assert groww["broker_display"] == "Groww"
        assert ind["broker_display"] == "INDmoney"

    def test_short_leg_stt_on_sell(self):
        legs = [
            {
                "side": "SELL",
                "price": 91.5,
                "quantity": 65,
                "option_type": "CE",
                "strike": 24200.0,
                "symbol": "NIFTY21JUL2624200CE",
            }
        ]
        row = calculate_charges_for_legs(legs, broker="indmoney")["per_leg"][0]
        assert row["stt"] > 0
        assert row["brokerage"] == 20.0

    def test_round_trip_with_exit_at_spot(self):
        legs = [
            {
                "side": "BUY",
                "price": 133.05,
                "quantity": 65,
                "option_type": "CE",
                "strike": 24100.0,
                "symbol": "NIFTY21JUL2624100CE",
            },
            {
                "side": "SELL",
                "price": 91.5,
                "quantity": 65,
                "option_type": "CE",
                "strike": 24200.0,
                "symbol": "NIFTY21JUL2624200CE",
            },
        ]
        out = calculate_charges_with_exit_for_legs(legs, spot=24078.5, broker="indmoney")
        assert out["round_trip_charges"] >= out["total"]["total_charges"]
        assert out["exit_charges"] == 0.0
