"""Tests for debate synthesis merge."""

from __future__ import annotations

import pytest

from trade_integrations.research.debate_synthesis import (
    extract_structured_debate,
    merge_stock_prediction,
)


@pytest.mark.unit
class TestDebateSynthesis:
    def test_extract_from_debate_json(self):
        raw = {
            "as_of": "2026-07-16T12:00:00+00:00",
            "rating": 7,
            "final_trade_decision": "Bullish on RELIANCE — accumulate on dips toward ₹1280, target ₹1360.",
            "investment_debate": {"judge_decision": "Buy with stop near ₹1250"},
            "analyst_reports": {"market": "Strong sector tailwind"},
        }
        out = extract_structured_debate(raw)
        assert out["view"] == "bullish"
        assert out["direction_confidence"] == 0.7
        assert out["target"] == 1360.0

    def test_merge_hybrid_c(self):
        debate = {
            "view": "bullish",
            "direction_confidence": 0.72,
            "expected_return_pct": 1.2,
            "target": 1360.0,
            "stop": 1250.0,
            "debate_as_of": "2026-07-16",
        }
        quant = {
            "view": "neutral",
            "expected_return_pct": 0.3,
            "range": {"low": 1280.0, "high": 1315.0},
            "model_confidence": 0.55,
            "source": "realized_vol_momentum",
        }
        merged = merge_stock_prediction(debate, quant, spot=1296.0, horizon_days=1)
        assert merged["provenance"]["range"] == "quant"
        assert merged["provenance"]["direction"] == "debate"
        assert merged["range"]["low"] == pytest.approx(1280.0, abs=5.0)
        assert merged["confidence"] == 0.55
        assert merged["expected_return_pct"] == pytest.approx(0.84, abs=0.01)

    def test_merge_quant_only(self):
        quant = {
            "view": "neutral",
            "expected_return_pct": 0.0,
            "range": {"low": 1270.0, "high": 1320.0},
            "model_confidence": 0.5,
            "source": "fallback_band",
        }
        merged = merge_stock_prediction({}, quant, spot=1296.0)
        assert merged["provenance"]["direction"] == "quant"
        assert merged["target"] == 1320.0
