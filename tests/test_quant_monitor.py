"""Tests for quant monitor diffs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.monitor.quant_monitor import diff_quant_review  # noqa: E402


def test_profile_change_alert():
    prev = {"active_strategy_profile": "momentum", "ticker": "NIFTY"}
    curr = {"active_strategy_profile": "defensive", "ticker": "NIFTY"}
    alerts = diff_quant_review(prev, curr)
    assert any(a.alert_type == "profile_change" for a in alerts)


def test_ta_consensus_flip_alert():
    prev = {"ta_consensus": {"direction": "bullish"}, "ticker": "NIFTY"}
    curr = {"ta_consensus": {"direction": "bearish"}, "ticker": "NIFTY"}
    alerts = diff_quant_review(prev, curr)
    assert any(a.alert_type == "ta_consensus_flip" for a in alerts)
