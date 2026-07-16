"""Tests for Vibe stack setup scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.mark.unit
class TestSetupVibe:
    def test_render_agent_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENALGO_API_KEY", "test-key-123")
        monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
        monkeypatch.chdir(tmp_path)

        root = Path(__file__).resolve().parents[1]
        openalgo_mcp = root / "openalgo" / "mcp" / "mcpserver.py"
        if not openalgo_mcp.is_file():
            pytest.skip("openalgo submodule not checked out")

        # Import after chdir so ROOT resolves correctly in script — use direct import from repo
        import sys

        sys.path.insert(0, str(root))
        from scripts.setup_vibe import render_agent_json

        payload = render_agent_json()
        assert "openalgo" in payload["mcpServers"]
        server = payload["mcpServers"]["openalgo"]
        assert server["args"][0] == "test-key-123"
        assert server["args"][1] == "http://127.0.0.1:5001"
        wrapper = root / "scripts" / "run_openalgo_mcp.sh"
        assert server["command"] == str(wrapper.resolve())

    def test_sync_writes_agent_json(self, monkeypatch, tmp_path):
        root = Path(__file__).resolve().parents[1]
        openalgo_mcp = root / "openalgo" / "mcp" / "mcpserver.py"
        if not openalgo_mcp.is_file():
            pytest.skip("openalgo submodule not checked out")

        vibe_home = tmp_path / "vibe-home"
        monkeypatch.setenv("VIBE_TRADING_HOME", str(vibe_home))
        monkeypatch.setenv("OPENALGO_API_KEY", "abc")
        monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "minimax")
        monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_LLM", "MiniMax-M3")
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")

        import sys

        sys.path.insert(0, str(root))
        from scripts.setup_vibe import sync_agent_json, sync_skills, sync_vibe_env

        agent_path = sync_agent_json()
        skill_paths = sync_skills()
        env_path = sync_vibe_env(force=True)

        assert agent_path.is_file()
        assert skill_paths
        trade_stack = vibe_home / "skills" / "user" / "trade-stack" / "SKILL.md"
        options_advisor = vibe_home / "skills" / "user" / "options-advisor" / "SKILL.md"
        assert trade_stack.is_file()
        assert options_advisor.is_file()
        assert env_path and env_path.is_file()

        data = json.loads(agent_path.read_text())
        assert "openalgo" in data["mcpServers"]
        env_text = env_path.read_text()
        assert "LANGCHAIN_PROVIDER=minimax" in env_text
        assert "MINIMAX_API_KEY=mm-key" in env_text
        assert "TRADE_STACK_HUB_DIR" in env_text

        skill = trade_stack.read_text()
        assert "OpenAlgo MCP" in skill
