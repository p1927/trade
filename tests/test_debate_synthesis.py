"""Tests for debate synthesis merge."""

from __future__ import annotations

import pytest

from trade_integrations.research.debate_synthesis import (
    apply_debate_bias_to_stock_ranked,
    extract_structured_debate,
    merge_index_prediction,
    merge_options_context,
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

    def test_bearish_debate_promotes_hold_cash(self):
        ranked = [
            {"name": "event_play", "score": 0.62, "tier": "Recommended", "action": "BUY"},
            {"name": "buy_dip", "score": 0.58, "action": "BUY"},
            {"name": "hold_cash", "score": 0.45, "action": "HOLD"},
        ]
        out = apply_debate_bias_to_stock_ranked(
            ranked,
            debate_view="bearish",
            debate_confidence=0.7,
        )
        assert out[0]["name"] == "hold_cash"
        assert out[0]["score"] > ranked[2]["score"]

    def test_merge_index_prediction(self):
        debate = {
            "view": "bearish",
            "direction_confidence": 0.65,
            "expected_return_pct": -1.5,
            "rationale": "Trim exposure",
            "debate_as_of": "2026-07-16",
        }
        base = {
            "view": "bullish",
            "confidence": 0.8,
            "direction_confidence": 0.72,
            "expected_return_pct": 1.2,
        }
        merged = merge_index_prediction(debate, base)
        assert merged["view"] == "bearish"
        assert merged["direction_view"] == "bearish"
        assert merged["expected_return_pct"] == pytest.approx(-0.42, abs=0.01)
        assert merged["provenance"]["direction"] == "debate"
        assert merged["confidence"] == 0.65
        assert merged["quant"]["expected_return_pct"] == 1.2
        assert merged["debate"]["expected_return_pct"] == -1.5

    def test_merge_index_prediction_bearish_debate_bullish_quant(self):
        debate = {
            "view": "bearish",
            "direction_confidence": 0.7,
            "expected_return_pct": -2.0,
            "debate_as_of": "2026-07-16",
        }
        base = {
            "view": "bullish",
            "direction_confidence": 0.68,
            "expected_return_pct": 1.2,
        }
        merged = merge_index_prediction(debate, base)
        assert merged["expected_return_pct"] == pytest.approx(-0.72, abs=0.01)
        assert merged["view"] == merged["direction_view"]
        assert merged["view"] in {"bearish", "neutral", "bullish"}
        bottom_up = 0.0
        assert merged["macro_delta_pct"] == pytest.approx(
            merged["expected_return_pct"] - bottom_up, abs=0.01
        )

    def test_merge_index_prediction_recomputes_macro_delta(self):
        debate = {
            "view": "bearish",
            "direction_confidence": 0.7,
            "expected_return_pct": -2.0,
            "debate_as_of": "2026-07-16",
        }
        base = {
            "view": "bullish",
            "expected_return_pct": 1.0,
            "bottom_up_return_pct": 0.3,
            "macro_delta_pct": 0.7,
            "direction_confidence": 0.68,
        }
        merged = merge_index_prediction(debate, base)
        assert merged["expected_return_pct"] == pytest.approx(-0.8, abs=0.01)
        assert merged["macro_delta_pct"] == pytest.approx(-1.1, abs=0.01)
        assert merged["quant"]["macro_delta_pct"] == 0.7

    def test_merge_options_context_biases_puts(self):
        doc = {
            "ranked_strategies": [
                {"name": "bull_call_spread", "score": 0.7, "tier": "Recommended", "legs": []},
                {"name": "bear_put_spread", "score": 0.55, "tier": "Consider", "legs": []},
            ],
            "recommended": {"name": "bull_call_spread", "score": 0.7},
            "prediction": {"view": "neutral"},
        }
        debate = {"view": "bearish", "direction_confidence": 0.75, "debate_as_of": "2026-07-16"}
        merged = merge_options_context(debate, doc)
        assert merged["ranked_strategies"][0]["name"] == "bear_put_spread"
        assert merged["recommended"]["name"] == "bear_put_spread"
        assert merged["prediction"]["debate_view"] == "bearish"
