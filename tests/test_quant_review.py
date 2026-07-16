"""Tests for quant review bridge."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_integrations.bridge.quant_review import build_quant_review_payload


@pytest.mark.unit
def test_build_quant_review_payload_structure():
    fake_index = {
        "prediction": {"view": "bullish", "expected_return_pct": 1.5},
        "spot": 24500.0,
        "global_factors": [
            {"factor": "india_vix", "value": 14.5},
            {"factor": "nifty_rsi_14", "value": 58.0},
        ],
    }
    with patch(
        "trade_integrations.context.hub.load_index_research_json",
        return_value=fake_index,
    ), patch(
        "trade_integrations.bridge.quant_review._live_openalgo_technical_factors",
        return_value={"nifty_macd_histogram": 1.2},
    ), patch(
        "trade_integrations.dataflows.index_research.derivatives_bridge.load_derivatives_implied_factors",
        return_value=[],
    ), patch(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        return_value="up",
    ):
        payload = build_quant_review_payload("NIFTY", horizon_days=14)

    assert payload["ticker"] == "NIFTY"
    assert payload["disclaimer"]
    assert "ta_consensus" in payload
    assert payload["model_prediction_view"] == "bullish"
    assert isinstance(payload.get("surprises"), list)
    assert isinstance(payload.get("disagreements_with_forecast"), list)
