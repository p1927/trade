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
