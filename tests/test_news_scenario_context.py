"""Tests for news scenario ContextBuilder prompt."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


@pytest.mark.unit
def test_news_scenario_prompt_includes_policy():
    from src.agent.context import ContextBuilder
    from src.agent.memory import WorkspaceMemory
    from src.agent.tools import ToolRegistry

    builder = ContextBuilder(
        registry=ToolRegistry(),
        memory=WorkspaceMemory(),
        session_config={"session_kind": "news_scenario_advisor"},
    )
    prompt = builder.build_system_prompt()
    assert "news-scenario advisor" in prompt.lower()
    assert "pipeline_as_of" in prompt
    assert "save_news_scenario_draft" in prompt
    assert "run_news_event_scenario" in prompt


@pytest.mark.unit
def test_default_prompt_excludes_news_scenario_block():
    from src.agent.context import ContextBuilder
    from src.agent.memory import WorkspaceMemory
    from src.agent.tools import ToolRegistry

    builder = ContextBuilder(
        registry=ToolRegistry(),
        memory=WorkspaceMemory(),
        session_config={},
    )
    prompt = builder.build_system_prompt()
    assert "news-scenario advisor" not in prompt.lower()
