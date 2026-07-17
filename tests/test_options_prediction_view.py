"""Unit tests for options prediction view heuristic."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.aggregator import _prediction_view


@pytest.mark.unit
@pytest.mark.parametrize(
    ("analytics", "events", "signals", "expected"),
    [
        ({"bias": "bullish"}, [], {}, "bullish"),
        ({"iv_regime": "high"}, [], {}, "range_short_vol"),
        ({"iv_regime": "low"}, [], {}, "directional_debit"),
        ({}, [], {"earnings_bias": "bullish"}, "bullish_earnings"),
        ({}, [], {"earnings_bias": "bearish"}, "bearish_earnings"),
        ({}, [], {"corp_event_score": 55}, "corp_event_vol"),
        (
            {"iv_regime": "moderate"},
            [{"type": "earnings", "impact_on_vol": "high"}],
            {},
            "event_volatility",
        ),
        ({}, [], {}, "neutral"),
    ],
)
def test_prediction_view_matrix(analytics, events, signals, expected):
    assert _prediction_view(analytics, events, prediction_signals=signals) == expected
