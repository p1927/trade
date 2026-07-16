"""Tests for hub signals bridge into options events and markdown."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.signals_bridge import (
    format_corp_events_section,
    format_earnings_signal_section,
    hub_signals_to_events,
    prediction_signals_from_hub,
)
from trade_integrations.dataflows.options_research.strategy_ranker import rank_strategies


@pytest.mark.unit
class TestSignalsBridge:
    def test_earnings_section_shows_beat_probability(self):
        md = format_earnings_signal_section(
            {"beat_probability": 0.72, "confidence": "MEDIUM", "source": "finverse"}
        )
        assert "72.0%" in md
        assert "MEDIUM" in md

    def test_corp_events_section_shows_score(self):
        md = format_corp_events_section(
            {
                "status": "ok",
                "total_score": 1010,
                "rank": 3,
                "company_name": "Apple Inc.",
            }
        )
        assert "1010" in md
        assert "Apple" in md

    def test_hub_signals_merge_calendar_and_forecasts(self):
        events = hub_signals_to_events(
            calendar_events=[{"date": "2026-07-30", "type": "earnings", "description": "Q3"}],
            earnings_signal={"beat_probability": 0.7},
            corp_events={"status": "no_data", "detail": "batch pending"},
        )
        types = {e["type"] for e in events}
        assert "earnings" in types
        assert "earnings_signal" in types
        assert "corp_event_watch" in types

    def test_prediction_signals_bias(self):
        signals = prediction_signals_from_hub(
            earnings_signal={"beat_probability": 0.72},
            corp_events={"status": "ok", "total_score": 200, "rank": 5},
        )
        assert signals["earnings_bias"] == "bullish"
        assert signals["corp_event_score"] == 200.0


@pytest.mark.unit
class TestRankerSignals:
    def test_earnings_bias_boosts_event_strategies(self):
        candidates = [
            {
                "name": "long_straddle",
                "tags": ["event", "long_vol"],
                "legs": [
                    {
                        "side": "BUY",
                        "option_type": "CE",
                        "strike": 100,
                        "price": 8,
                        "symbol": "CE2",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                    {
                        "side": "BUY",
                        "option_type": "PE",
                        "strike": 100,
                        "price": 7,
                        "symbol": "PE2",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                ],
                "rationale": "test",
            },
            {
                "name": "iron_condor",
                "tags": ["range", "defined_risk"],
                "legs": [
                    {
                        "side": "BUY",
                        "option_type": "PE",
                        "strike": 90,
                        "price": 2,
                        "symbol": "PE1",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                    {
                        "side": "SELL",
                        "option_type": "CE",
                        "strike": 110,
                        "price": 4,
                        "symbol": "CE3",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                ],
                "rationale": "test",
            },
        ]
        chain = {
            "underlying_ltp": 100,
            "atm_strike": 100,
            "chain": [{"strike": 100, "ce": {"ltp": 8, "symbol": "CE2", "lotsize": 50}, "pe": {"ltp": 7, "symbol": "PE2", "lotsize": 50}}],
        }
        bullish = rank_strategies(
            candidates,
            chain_snapshot=chain,
            analytics={"iv_regime": "high", "atm_iv": 22},
            history={"rv30_pct": 15},
            events=[{"type": "earnings_signal", "impact_on_vol": "elevated"}],
            spot=100.0,
            prediction_signals={"earnings_bias": "bullish", "beat_probability": 0.72},
        )
        neutral = rank_strategies(
            candidates,
            chain_snapshot=chain,
            analytics={"iv_regime": "high", "atm_iv": 22},
            history={"rv30_pct": 15},
            events=[],
            spot=100.0,
            prediction_signals={},
        )
        bullish_straddle = next(r for r in bullish if r["name"] == "long_straddle")
        neutral_straddle = next(r for r in neutral if r["name"] == "long_straddle")
        assert bullish_straddle["score"] >= neutral_straddle["score"]
