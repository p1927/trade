"""Unit tests for strategy ranker ordering."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.strategy_ranker import rank_strategies


def _fixture_chain(spot: float = 100.0) -> dict:
    return {
        "underlying_ltp": spot,
        "atm_strike": spot,
        "chain": [
            {
                "strike": spot - 100,
                "ce": {"ltp": 5, "symbol": "CE1", "lotsize": 50},
                "pe": {"ltp": 2, "symbol": "PE1", "lotsize": 50},
            },
            {
                "strike": spot,
                "ce": {"ltp": 8, "symbol": "CE2", "lotsize": 50},
                "pe": {"ltp": 7, "symbol": "PE2", "lotsize": 50},
            },
            {
                "strike": spot + 100,
                "ce": {"ltp": 4, "symbol": "CE3", "lotsize": 50},
                "pe": {"ltp": 9, "symbol": "PE3", "lotsize": 50},
            },
        ],
    }


@pytest.mark.unit
class TestOptionsRanker:
    def test_ranks_liquid_candidates(self):
        candidates = [
            {
                "name": "iron_condor",
                "tags": ["range", "defined_risk"],
                "legs": [
                    {
                        "side": "BUY",
                        "option_type": "PE",
                        "strike": 100,
                        "price": 2,
                        "symbol": "PE1",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                    {
                        "side": "SELL",
                        "option_type": "CE",
                        "strike": 200,
                        "price": 4,
                        "symbol": "CE3",
                        "lot_size": 50,
                        "lots": 1,
                        "quantity": 50,
                    },
                ],
                "rationale": "test",
            },
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
        ]
        ranked = rank_strategies(
            candidates,
            chain_snapshot=_fixture_chain(),
            analytics={"iv_regime": "high", "atm_iv": 22},
            history={"rv30_pct": 15},
            events=[{"type": "earnings", "impact_on_vol": "elevated"}],
            spot=100.0,
        )
        assert len(ranked) == 2
        assert ranked[0]["score"] >= ranked[1]["score"]
        assert "tier" in ranked[0]

    def test_skips_illiquid(self):
        candidates = [
            {
                "name": "bad",
                "tags": [],
                "legs": [{"side": "BUY", "price": 0, "symbol": ""}],
                "rationale": "x",
            }
        ]
        ranked = rank_strategies(
            candidates,
            chain_snapshot=_fixture_chain(),
            analytics={},
            history={},
            events=[],
            spot=100.0,
        )
        assert ranked == []
