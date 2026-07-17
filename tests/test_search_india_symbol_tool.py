"""Tests for search_india_symbol tool and symbol validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


def test_search_india_symbol_tool_registry_fallback(monkeypatch) -> None:
    from src.tools.search_india_symbol_tool import SearchIndiaSymbolTool

    monkeypatch.setattr(
        "trade_integrations.dataflows.symbol_registry.openalgo_registry.search_india_symbols",
        lambda query, limit=5: [{"symbol": "RELIANCE", "name": "Reliance Industries", "exchange": "NSE"}],
    )

    tool = SearchIndiaSymbolTool()
    payload = json.loads(tool.execute(query="reliance", limit=3))
    assert payload["ok"] is True
    assert payload["matches"][0]["symbol"] == "RELIANCE"


def test_unknown_symbol_blocks_ready_proposal(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.validate_proposal_symbols",
        lambda symbols: [f"Unknown symbol {symbols[0]}"] if symbols == ["XYZFOO"] else [],
    )

    result = propose_autonomous_agent(
        symbols=["XYZFOO"],
        mandate="Paper trade XYZFOO",
        execution_market="IN",
    )
    assert result["status"] == "incomplete"
    assert result["proposal"]["routing_errors"]
