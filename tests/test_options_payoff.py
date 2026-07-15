"""Unit tests for payoff and charges math."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.payoff_charges import (
    calculate_charges,
    compute_payoff,
)


@pytest.mark.unit
class TestOptionsPayoff:
    def test_iron_condor_breakevens(self):
        spot = 100.0
        legs = [
            {"side": "BUY", "option_type": "PE", "strike": 80, "price": 1, "quantity": 1},
            {"side": "SELL", "option_type": "PE", "strike": 90, "price": 3, "quantity": 1},
            {"side": "SELL", "option_type": "CE", "strike": 110, "price": 3, "quantity": 1},
            {"side": "BUY", "option_type": "CE", "strike": 120, "price": 1, "quantity": 1},
        ]
        payoff = compute_payoff(legs, spot, steps=40, range_pct=0.25)
        assert payoff["max_profit"] is not None
        assert payoff["max_loss"] is not None
        assert payoff["max_profit"] > 0
        assert payoff["max_loss"] < 0
        assert len(payoff["samples"]) == 41

    def test_charges_positive(self):
        legs = [
            {"side": "BUY", "price": 50, "quantity": 50, "symbol": "NIFTYCE"},
            {"side": "SELL", "price": 30, "quantity": 50, "symbol": "NIFTYPE"},
        ]
        charges = calculate_charges(legs)
        assert charges["total"]["total_charges"] > 0
        assert len(charges["per_leg"]) == 2
