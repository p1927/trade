"""Tests for autonomous agent turn prompts."""

from __future__ import annotations

import pytest

from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt


@pytest.mark.unit
class TestAutonomousTurns:
    def test_running_agent_footer_forbids_user_questions(self):
        agent = {
            "id": "aa_test1",
            "name": "NIFTY bot",
            "symbols": ["NIFTY"],
            "mandate": "Event vol paper",
            "constraints": {"mode": "paper", "confidence_threshold": 75},
            "execution_market": "IN",
            "mandate_config": {},
        }
        prompt = build_full_reasoning_prompt(agent=agent, turn_kind="research")
        assert "do not ask the user questions" in prompt.lower()
        assert "record_autonomous_decision" in prompt
        assert "Decision: ENTER" in prompt
        assert "handoff cycle" in prompt
        assert 'load_skill("index-advisor")' in prompt
        assert "get_index_trade_plan" in prompt

    def test_equity_agent_prompt_uses_stock_advisor(self):
        agent = {
            "id": "aa_eq1",
            "name": "RELIANCE swing",
            "symbols": ["RELIANCE"],
            "mandate": "Equity swing paper",
            "constraints": {"mode": "paper", "confidence_threshold": 75},
            "execution_market": "IN",
            "mandate_config": {"allowed_instruments": ["equity"]},
        }
        prompt = build_full_reasoning_prompt(agent=agent, turn_kind="research")
        assert 'load_skill("stock-advisor")' in prompt
        assert 'load_skill("options-advisor")' not in prompt

    def test_strategy_revision_includes_progress_block(self, monkeypatch):
        agent = {
            "id": "aa_test1",
            "name": "NIFTY bot",
            "symbols": ["NIFTY"],
            "mandate": "Event vol paper",
            "constraints": {"mode": "paper", "confidence_threshold": 75},
            "execution_market": "IN",
            "mandate_config": {},
        }

        def _fake_progress(**kwargs):
            if kwargs.get("turn_kind") != "strategy_revision":
                return ""
            return (
                "## Strategy progress (mandatory — cite before REVISE/HOLD/EXIT)\n"
                "```json\n{\"position_state\": \"flat\"}\n```\n"
            )

        monkeypatch.setattr(
            "trade_integrations.autonomous_agents.turns.format_strategy_progress_for_prompt",
            _fake_progress,
        )
        revision = build_full_reasoning_prompt(agent=agent, turn_kind="strategy_revision")
        research = build_full_reasoning_prompt(agent=agent, turn_kind="research")
        assert "Strategy progress (mandatory" in revision
        assert "Strategy progress (mandatory" not in research

    def test_read_strategy_progress_snapshot_does_not_raise(self):
        from trade_integrations.autonomous_agents.strategy_progress import read_strategy_progress_snapshot

        payload = read_strategy_progress_snapshot(
            agent={
                "id": "aa_prog1",
                "symbols": ["NIFTY"],
                "constraints": {"mode": "paper"},
                "mandate_config": {},
                "thesis": {"strategy": "iron condor", "updated_at": "2026-07-22T10:00:00+00:00"},
            }
        )
        assert payload["snapshot"]["agent_id"] == "aa_prog1"
        assert "assessment" in payload["snapshot"]
        assert payload["snapshot"]["assessment"]["position_state"] == "flat"

    def test_progress_assessment_uses_scoped_positionbook(self):
        from trade_integrations.autonomous_agents.strategy_progress import _progress_assessment

        assessment = _progress_assessment(
            thesis={"strategy": "iron condor"},
            hub={},
            position={"open_positions": 0, "open_legs": 2, "watch_configured": True},
            plan={"strategy": "iron condor"},
            lifecycle={"active_strategy": "iron condor"},
        )
        assert assessment["position_state"] == "flat"
        assert any("handoff legs exist" in n for n in assessment["notes"])
