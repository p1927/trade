"""Compact autonomous prompts must fit Vibe HTTP limit."""

from __future__ import annotations

from nautilus_openalgo_bridge.models import BridgeSignal, QuoteSnapshot, WatchAlert, WatchRule
from nautilus_openalgo_bridge.vibe_trigger import build_alert_turn_prompt

from trade_integrations.autonomous_agents.turns import (
    HTTP_PROMPT_LIMIT,
    build_autonomous_turn_prompt,
    build_full_reasoning_prompt,
)


def _realistic_agent() -> dict:
    return {
        "id": "aa_size_test",
        "name": "NIFTY event vol",
        "symbols": ["NIFTY"],
        "mandate": (
            "Paper trade NIFTY options around event volatility. Focus on iron condor "
            "and straddle strategies when VIX is elevated."
        ),
        "constraints": {
            "mode": "paper",
            "confidence_threshold": 75,
            "budget_inr": 20000,
            "max_daily_loss_inr": 2000,
        },
        "execution_market": "IN",
        "mandate_config": {
            "allowed_instruments": ["options"],
            "holding_period": "3d",
            "flatten_policy": "eod",
        },
        "thesis": {
            "strategy": "iron condor",
            "direction": "neutral",
            "confidence": 72,
            "decision": "HOLD",
            "updated_at": "2026-07-23T08:00:00+00:00",
        },
        "user_guidance": [{"at": "2026-07-23", "text": "Prefer defined-risk structures only"}],
        "learnings": [
            {
                "at": "2026-07-21",
                "event": "EXIT",
                "strategy": "short straddle",
                "rationale": "VIX spike broke stop",
                "pnl_inr": -1200,
            }
        ]
        * 3,
        "decisions": [{"action": "HOLD", "confidence": 70, "rationale": "Waiting for event"}] * 3,
        "lifecycle": {
            "state": "watching",
            "active_strategy": "iron condor",
            "tried_strategies": ["short straddle", "iron condor"],
        },
    }


def test_compact_prompts_under_http_limit():
    agent = _realistic_agent()
    for turn_kind in ("bootstrap", "research", "strategy_revision", "post_execution"):
        prompt = build_autonomous_turn_prompt(agent=agent, turn_kind=turn_kind, compact=True)
        assert len(prompt) <= HTTP_PROMPT_LIMIT, f"{turn_kind}={len(prompt)}"
        assert "record_autonomous_decision" in prompt
        assert "# Autonomous agent turn" in prompt


def test_bridge_alert_prompt_under_http_limit():
    agent = _realistic_agent()
    agent["plan_approved_at"] = "2026-07-01T00:00:00+00:00"
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5),
        symbol="NIFTY",
        message="NIFTY moved +0.82% since entry",
        ltp=24650.0,
        move_pct=0.82,
    )
    quotes = {"NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=24650.0)}
    prompt = build_alert_turn_prompt(agent=agent, alert=alert, quotes=quotes)
    assert len(prompt) <= HTTP_PROMPT_LIMIT, len(prompt)
    assert "Nautilus watch alert" in prompt


def test_build_full_reasoning_prompt_is_compact():
    agent = _realistic_agent()
    prompt = build_full_reasoning_prompt(agent=agent, turn_kind="strategy_revision")
    assert len(prompt) <= HTTP_PROMPT_LIMIT
