"""Tests for upcoming events and factor simulation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.upcoming_events import build_upcoming_events


@pytest.mark.unit
def test_build_upcoming_events_from_constituents():
    today = date.today()
    event_date = (today + timedelta(days=5)).isoformat()
    signals = [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.1,
            sector="Energy",
            events=[{"type": "results", "date": event_date, "impact": "positive"}],
        ),
    ]
    macro = {"days_to_monthly_expiry": 3, "is_results_season": 1.0}
    rows = build_upcoming_events(signals, macro, horizon_days=14)
    assert any(r.get("symbol") == "RELIANCE" for r in rows)
    assert any(r["event_type"] == "monthly_expiry" for r in rows)


@pytest.mark.unit
def test_simulate_index_prediction_adjusts_return():
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction

    macro = {
        "oil_brent": 80.0,
        "usd_inr": 83.0,
        "india_vix": 14.0,
        "sp500": 5200.0,
        "index_sentiment": 0.1,
    }
    base = simulate_index_prediction(
        macro_factors=macro,
        factor_overrides={},
        spot=24500.0,
        bottom_up_return_pct=0.5,
        horizon_days=14,
    )
    assert "expected_return_pct" in base
    assert base["index_level"] > 0

    shocked = simulate_index_prediction(
        macro_factors=macro,
        factor_overrides={"india_vix": 20.0},
        spot=24500.0,
        bottom_up_return_pct=0.5,
        horizon_days=14,
    )
    assert "factor_explanation" in shocked
    assert shocked["index_level"] > 0
