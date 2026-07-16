"""Unit tests for Nautilus ↔ OpenAlgo bridge models and watch evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import (  # noqa: E402
    ExecutionIntent,
    ExecutionLeg,
    IntentAction,
    PositionHandoff,
    QuoteSnapshot,
    WatchGate,
    WatchRule,
    WatchSpec,
)
from nautilus_openalgo_bridge.watch_eval import evaluate_rule, evaluate_watch_spec  # noqa: E402


def test_watch_spec_json_roundtrip():
    spec = WatchSpec(
        rules=[
            WatchRule(
                symbol="NIFTY",
                metric="spot_move_pct",
                threshold=0.5,
                direction="either",
                baseline_ltp=24500.0,
            ),
            WatchRule(symbol="INDIAVIX", metric="level_above", threshold=14.0),
        ],
        gate=WatchGate(skip_if_unchanged_minutes=15),
    )
    raw = json.dumps(spec.to_dict())
    restored = WatchSpec.from_dict(json.loads(raw))
    assert len(restored.rules) == 2
    assert restored.rules[0].symbol == "NIFTY"
    assert restored.rules[0].threshold == 0.5
    assert restored.gate.skip_if_unchanged_minutes == 15


def test_execution_intent_roundtrip():
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_test",
        rationale="Thesis broken",
        confidence=82,
        underlying="NIFTY",
        legs=[],
    )
    intent.legs.append(
        ExecutionLeg.from_dict(
            {
                "symbol": "NIFTY24JUL24500CE",
                "exchange": "NFO",
                "action": "SELL",
                "quantity": 50,
            }
        )
    )
    restored = ExecutionIntent.from_dict(intent.to_dict())
    assert restored.action == IntentAction.EXIT
    assert restored.agent_id == "aa_test"
    assert restored.legs[0].symbol == "NIFTY24JUL24500CE"


def test_position_handoff_roundtrip():
    handoff = PositionHandoff(
        agent_id="aa_test",
        widget_id="tp_NIFTY_abc",
        underlying="NIFTY",
        legs=[],
        entry_spot=24500.0,
        watch_spec=WatchSpec(
            rules=[WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5)]
        ),
    )
    restored = PositionHandoff.from_dict(handoff.to_dict())
    assert restored.agent_id == "aa_test"
    assert restored.entry_spot == 24500.0
    assert restored.watch_spec.rules[0].threshold == 0.5


def test_spot_move_rule_fires():
    rule = WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5, baseline_ltp=100.0)
    quote = QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=100.6)
    alert = evaluate_rule(rule, quote, baseline_ltp=100.0)
    assert alert is not None
    assert alert.move_pct == pytest.approx(0.6)


def test_spot_move_rule_silent_within_threshold():
    rule = WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5, baseline_ltp=100.0)
    quote = QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=100.2)
    assert evaluate_rule(rule, quote, baseline_ltp=100.0) is None


def test_level_above_rule_fires():
    rule = WatchRule(symbol="INDIAVIX", metric="level_above", threshold=14.0)
    quote = QuoteSnapshot(symbol="INDIAVIX", exchange="NSE_INDEX", ltp=14.5)
    alert = evaluate_rule(rule, quote)
    assert alert is not None
    assert "above" in alert.message


def test_resolve_openalgo_index_uses_nse_index():
    from nautilus_openalgo_bridge.instruments import resolve_openalgo_symbol

    assert resolve_openalgo_symbol("NIFTY") == ("NIFTY", "NSE_INDEX")
    assert resolve_openalgo_symbol("BANKNIFTY") == ("BANKNIFTY", "NSE_INDEX")


def test_evaluate_watch_spec_multiple_rules():
    spec = WatchSpec(
        rules=[
            WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5),
            WatchRule(symbol="INDIAVIX", metric="level_above", threshold=14.0),
        ]
    )
    quotes = {
        "NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=100.6),
        "INDIAVIX": QuoteSnapshot(symbol="INDIAVIX", exchange="NSE", ltp=13.0),
    }
    alerts = evaluate_watch_spec(spec, quotes, baselines={"NIFTY": 100.0})
    assert len(alerts) == 1
    assert alerts[0].symbol == "NIFTY"
