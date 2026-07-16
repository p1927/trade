"""Unit tests for index regime classification and scenario builder."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import build_index_scenarios


def _scenario_signals() -> list[ConstituentSignal]:
    return [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.1,
            events=[{"type": "results", "date": "2026-07-20"}],
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.08,
            events=[{"type": "earnings", "date": "2026-07-22"}],
        ),
        ConstituentSignal(
            symbol="INFY",
            weight=0.07,
            events=[{"type": "earnings", "date": "2026-07-25"}],
        ),
    ]


@pytest.mark.unit
def test_classify_regime_labels():
    assert classify_regime(india_vix=14.0, nifty_trend_20d="up")["label"] == "bull"
    assert classify_regime(india_vix=14.0, nifty_trend_20d="down")["label"] == "bear"
    assert classify_regime(india_vix=22.0, nifty_trend_20d="up")["label"] == "bear"
    assert classify_regime(india_vix=12.0, nifty_trend_20d="flat")["label"] == "bull"


@pytest.mark.unit
def test_build_index_scenarios_includes_event_buckets(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )

    scenarios = build_index_scenarios(
        _scenario_signals(),
        {
            "repo_rate": 6.5,
            "rbi_events": [{"date": "2026-07-25", "type": "mpc"}],
        },
        spot=24500.0,
        horizon_days=14,
    )

    events = {scenario["event"] for scenario in scenarios}
    assert "earnings_cluster" in events
    assert "rbi_policy" in events
    assert "monthly_expiry" in events
    assert 3 <= len(scenarios) <= 6


@pytest.mark.unit
def test_build_index_scenarios_range_bounds(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )

    spot = 24500.0
    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5},
        spot=spot,
        horizon_days=14,
    )

    for scenario in scenarios:
        low, high = scenario["index_range"]
        assert low < high
        assert low > spot * 0.9
        assert high < spot * 1.1
