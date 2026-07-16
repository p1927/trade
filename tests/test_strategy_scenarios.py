"""Tests for build_scenarios distinct strategy mapping."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.strategy_ranker import build_scenarios


@pytest.mark.unit
class TestBuildScenarios:
    def test_emits_distinct_strategy_hints(self):
        ranked = [
            {"name": "long_straddle"},
            {"name": "bull_call_spread"},
            {"name": "bear_put_spread"},
            {"name": "iron_condor"},
        ]
        scenarios = build_scenarios([], ranked)
        hints = [s["strategy_hint"] for s in scenarios]
        assert len(scenarios) >= 3
        assert len(set(hints)) == len(hints)
        assert hints[0] == "long_straddle"

    def test_empty_ranked_returns_refresh_hint(self):
        scenarios = build_scenarios([], [])
        assert len(scenarios) == 1
        assert "refresh" in scenarios[0]["trigger"].lower()
