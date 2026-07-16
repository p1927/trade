"""Unit tests for Phase 2 browse summary and payoff over time."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.browse_summary import build_browse_summary
from trade_integrations.dataflows.options_research.payoff_charges import (
    calculate_charges_with_exit,
    compute_payoff_over_time,
)


@pytest.mark.unit
class TestBrowseSummary:
    def test_builds_from_chain(self):
        chain_snapshot = {
            "underlying": "NIFTY",
            "underlying_ltp": 24000,
            "atm_strike": 24000,
            "expiry_date": "21JUL26",
            "expiries": ["21JUL26", "28JUL26"],
            "pcr": 1.1,
            "source": "openalgo",
            "chain": [
                {
                    "strike": 24000,
                    "ce": {"ltp": 100, "oi": 5000, "iv": 18},
                    "pe": {"ltp": 95, "oi": 4800, "iv": 17},
                },
                {
                    "strike": 24100,
                    "ce": {"ltp": 80, "oi": 3000, "iv": 17},
                    "pe": {"ltp": 120, "oi": 3200, "iv": 18},
                },
            ],
        }
        summary = build_browse_summary(chain_snapshot)
        assert summary["spot"] == 24000
        assert summary["atm_strike"] == 24000
        assert len(summary["top_strikes"]) >= 1
        assert summary["expiries"] == ["21JUL26", "28JUL26"]


@pytest.mark.unit
class TestPayoffOverTime:
    def test_samples_over_dte(self):
        legs = [
            {"side": "BUY", "option_type": "CE", "strike": 100, "price": 5, "quantity": 10},
            {"side": "BUY", "option_type": "PE", "strike": 100, "price": 4, "quantity": 10},
        ]
        out = compute_payoff_over_time(legs, 100.0, expiry="21JUL26", points=5)
        assert len(out["samples"]) == 5
        assert out["samples"][0]["days_to_expiry"] >= out["samples"][-1]["days_to_expiry"]
        assert "net_pnl" in out["samples"][0]

    def test_exit_charges_on_short(self):
        legs = [
            {"side": "SELL", "option_type": "CE", "strike": 100, "price": 5, "quantity": 50},
        ]
        ch = calculate_charges_with_exit(legs, spot=105.0)
        assert ch.get("round_trip_charges", 0) > ch["total"]["total_charges"]
