"""Tests for stop rule evaluation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import (  # noqa: E402
    BridgeSignal,
    PositionHandoff,
    QuoteSnapshot,
    StopRules,
)
from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules  # noqa: E402


def test_max_loss_triggers_exit_now():
    handoff = PositionHandoff(
        agent_id="aa_s",
        widget_id="w",
        underlying="NIFTY",
        legs=[],
        entry_spot=24000.0,
        stop_rules=StopRules(max_loss_inr=1500.0),
    )
    quotes = {"NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE_INDEX", ltp=23900.0)}
    alert = evaluate_stop_rules(handoff, quotes, unrealized_pnl_inr=-2000.0)
    assert alert is not None
    assert alert.signal == BridgeSignal.EXIT_NOW


def test_flatten_at_close_window():
    handoff = PositionHandoff(
        agent_id="aa_s",
        widget_id="w",
        underlying="NIFTY",
        legs=[],
        entry_spot=24000.0,
        stop_rules=StopRules(flatten_at_close=True),
    )
    quotes = {"NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE_INDEX", ltp=24000.0)}
    with patch("nautilus_openalgo_bridge.stop_eval.is_flatten_at_close_window", return_value=True):
        alert = evaluate_stop_rules(handoff, quotes)
    assert alert is not None
    assert "flatten" in alert.message.lower()
