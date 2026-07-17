"""Tests for extended watch rule evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import QuoteSnapshot, WatchRule  # noqa: E402
from nautilus_openalgo_bridge.watch_eval import evaluate_rule  # noqa: E402


def test_oi_change_pct_fires():
    rule = WatchRule(symbol="NIFTY", metric="oi_change_pct", threshold=5.0, direction="either")
    quote = QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=100.0, oi=110.0)
    alert = evaluate_rule(rule, quote, baseline_oi=100.0)
    assert alert is not None
    assert "OI changed" in alert.message


def test_volume_spike_pct_fires():
    rule = WatchRule(symbol="NIFTY", metric="volume_spike_pct", threshold=20.0, direction="either")
    quote = QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=100.0, volume=150.0)
    alert = evaluate_rule(rule, quote, baseline_volume=100.0)
    assert alert is not None
    assert "volume spike" in alert.message
