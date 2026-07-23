"""Tests for ADJUST leg diff helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import ExecutionLeg  # noqa: E402
from trade_integrations.execution.bridge_intent import build_adjust_legs_from_widget  # noqa: E402


def test_build_adjust_legs_closes_old_and_opens_new():
    handoff = [
        ExecutionLeg(symbol="NIFTY24JUL24500CE", exchange="NFO", action="SELL", quantity=50),
        ExecutionLeg(symbol="NIFTY24JUL24600CE", exchange="NFO", action="BUY", quantity=50),
    ]
    widget = {
        "implementation_steps": [
            {
                "action": "execute_basket",
                "payload": {
                    "orders": [
                        {
                            "symbol": "NIFTY24JUL24500CE",
                            "exchange": "NFO",
                            "action": "SELL",
                            "quantity": 50,
                        },
                        {
                            "symbol": "NIFTY24JUL24700CE",
                            "exchange": "NFO",
                            "action": "BUY",
                            "quantity": 50,
                        },
                    ]
                },
            }
        ]
    }
    delta = build_adjust_legs_from_widget(handoff_legs=handoff, widget=widget, product="NRML")
    symbols = [leg.symbol for leg in delta]
    assert "NIFTY24JUL24600CE" in symbols
    assert "NIFTY24JUL24700CE" in symbols
