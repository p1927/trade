"""Unit tests for index regime classification and scenario builder."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import (
    build_index_scenarios,
    reconcile_prediction_with_scenarios,
    scenario_weighted_return_pct,
)


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
def test_build_index_scenarios_budget_week(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 2, 1),
    )

    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5, "is_budget_week": 1.0},
        spot=24500.0,
        horizon_days=14,
    )
    events = {scenario["event"] for scenario in scenarios}
    assert "union_budget" in events


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


@pytest.mark.unit
def test_scenario_weighted_return_near_spot(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )
    spot = 24072.75
    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5, "rbi_events": [{"date": "2026-07-25"}]},
        spot=spot,
        horizon_days=14,
    )
    anchor = scenario_weighted_return_pct(scenarios, spot=spot)
    assert anchor is not None
    assert abs(anchor) < 2.0


@pytest.mark.unit
def test_build_index_scenarios_sorted_by_probability_desc(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )
    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5, "rbi_events": [{"date": "2026-07-25"}]},
        spot=24500.0,
        horizon_days=14,
    )
    probs = [float(s["probability"]) for s in scenarios]
    assert probs == sorted(probs, reverse=True)
    assert all(s.get("label") and s.get("description") for s in scenarios)
    assert all("midpoint_return_pct" in s for s in scenarios)
    assert abs(sum(probs) - 1.0) < 0.02


@pytest.mark.unit
def test_reconcile_stores_raw_headline_metadata(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )
    spot = 24072.75
    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5, "rbi_events": [{"date": "2026-07-25"}]},
        spot=spot,
        horizon_days=14,
    )
    raw = {
        "expected_return_pct": 5.5,
        "bottom_up_return_pct": 0.5,
        "macro_delta_pct": 5.0,
        "range": {"low": spot * 0.95, "high": spot * 1.1, "confidence": 0.5},
    }
    reconciled = reconcile_prediction_with_scenarios(raw, scenarios, spot=spot, mae_pct=1.5)
    assert reconciled["raw_expected_return_pct"] == 5.5
    assert reconciled["raw_macro_delta_pct"] == 5.0
    assert reconciled["scenario_anchor_return_pct"] is not None


@pytest.mark.unit
def test_reconcile_prediction_pulls_toward_scenarios(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 16),
    )
    spot = 24072.75
    scenarios = build_index_scenarios(
        _scenario_signals(),
        {"repo_rate": 6.5, "rbi_events": [{"date": "2026-07-25"}]},
        spot=spot,
        horizon_days=14,
    )
    raw = {
        "expected_return_pct": 5.5,
        "bottom_up_return_pct": 0.5,
        "macro_delta_pct": 5.0,
        "range": {"low": spot * 0.95, "high": spot * 1.1, "confidence": 0.5},
    }
    reconciled = reconcile_prediction_with_scenarios(raw, scenarios, spot=spot, mae_pct=1.5)
    assert reconciled["reconciled_with_scenarios"] is True
    assert reconciled["expected_return_pct"] < raw["expected_return_pct"]
    assert abs(reconciled["expected_return_pct"]) < 2.0
