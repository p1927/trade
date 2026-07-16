"""Unit tests for thesis-break detection on executed plans."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.options_research.models import OptionsResearchDoc
from trade_integrations.monitor.thesis_break import evaluate_thesis_break


def _sample_doc(*, spot: float = 24500.0, expected_move_pct: float = 2.0) -> OptionsResearchDoc:
    return OptionsResearchDoc(
        underlying="NIFTY",
        as_of=datetime(2026, 7, 16, tzinfo=timezone.utc),
        lookahead_days=14,
        instrument_type="index",
        market="IN",
        expiry="31JUL25",
        spot=spot,
        prediction={
            "view": "bullish",
            "expected_move_pct": expected_move_pct,
        },
        scenarios=[
            {
                "name": "bearish_breakdown",
                "probability": 0.22,
                "trigger": "Spot sells off toward lower expected range",
                "strategy_hint": "bear_put_spread",
            }
        ],
        recommended={
            "name": "Bull Call Spread",
            "net_max_loss": 5000,
        },
        payoff={"net_max_loss": 5000},
    )


def _ledger_entry() -> dict:
    return {
        "widget_id": "tp_NIFTY_abc123",
        "underlying": "NIFTY",
        "prediction_view": "bullish",
        "plan_spot": 24500.0,
        "net_max_loss": 5000.0,
        "scenarios": [
            {
                "name": "bearish_breakdown",
                "probability": 0.22,
                "trigger": "Spot sells off toward lower expected range",
            }
        ],
    }


@pytest.mark.unit
def test_spot_outside_expected_move_marks_broken():
    doc = _sample_doc(expected_move_pct=2.0)
    ledger_entry = _ledger_entry()
    live_spot = 24500.0 * 0.97

    report = evaluate_thesis_break(
        doc,
        ledger_entry,
        live_spot=live_spot,
        position_pnl=None,
    )

    assert report.broken is True
    assert "spot_outside_expected_move" in report.reasons
    assert report.severity == "high"


@pytest.mark.unit
def test_max_loss_proximity_marks_broken():
    doc = _sample_doc()
    ledger_entry = _ledger_entry()

    report = evaluate_thesis_break(
        doc,
        ledger_entry,
        live_spot=24500.0,
        position_pnl=-4100.0,
    )

    assert report.broken is True
    assert "max_loss_proximity" in report.reasons
    assert report.severity == "medium"


@pytest.mark.unit
def test_scenario_adverse_trigger_for_bullish_view():
    doc = _sample_doc(expected_move_pct=2.0)
    ledger_entry = _ledger_entry()
    live_spot = 24500.0 * 0.989

    report = evaluate_thesis_break(
        doc,
        ledger_entry,
        live_spot=live_spot,
        position_pnl=None,
    )

    assert report.broken is True
    assert any(reason.startswith("scenario_adverse:") for reason in report.reasons)


@pytest.mark.unit
def test_within_thresholds_not_broken():
    doc = _sample_doc(expected_move_pct=2.0)
    ledger_entry = _ledger_entry()

    report = evaluate_thesis_break(
        doc,
        ledger_entry,
        live_spot=24520.0,
        position_pnl=-500.0,
    )

    assert report.broken is False
    assert report.reasons == []
    assert report.severity == "low"
