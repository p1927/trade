"""Tests for orchestrator ContextBuilder prompt."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


@pytest.mark.unit
class TestOrchestratorContext:
    def test_orchestrator_prompt_includes_preloaded_skill(self):
        from src.agent.context import ContextBuilder
        from src.agent.memory import WorkspaceMemory
        from src.agent.tools import ToolRegistry

        builder = ContextBuilder(
            registry=ToolRegistry(),
            memory=WorkspaceMemory(),
            session_config={"session_kind": "autonomous_orchestrator"},
        )
        prompt = builder.build_system_prompt()
        assert "autonomous-agent orchestrator" in prompt.lower()
        assert "propose_autonomous_agent" in prompt
        assert "user sees no card" in prompt.lower() or "sees nothing" in prompt.lower()
        assert "Orchestrator workflow (preloaded)" in prompt

    def test_non_orchestrator_prompt_excludes_preloaded_skill(self):
        from src.agent.context import ContextBuilder
        from src.agent.memory import WorkspaceMemory
        from src.agent.tools import ToolRegistry

        builder = ContextBuilder(
            registry=ToolRegistry(),
            memory=WorkspaceMemory(),
            session_config={"session_kind": "autonomous_agent"},
        )
        prompt = builder.build_system_prompt()
        assert "Orchestrator workflow (preloaded)" not in prompt
