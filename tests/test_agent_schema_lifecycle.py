"""Agent schema lifecycle tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


@pytest.mark.unit
def test_draft_skips_lifecycle_backfill(agents_hub) -> None:
    from trade_integrations.autonomous_agents.agent_schema import ensure_agent_lifecycle
    from trade_integrations.autonomous_agents.store import save_agent

    agent = {
        "id": "aa_draft_skip",
        "status": "draft",
        "symbols": [],
    }
    save_agent(agent)
    result = ensure_agent_lifecycle(agent, persist=False)
    assert "lifecycle" not in result or not result.get("lifecycle")


@pytest.mark.unit
def test_get_agent_assigns_default_lifecycle(agents_hub) -> None:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent_id = "aa_default_lc"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
        }
    )

    loaded = get_agent(agent_id)
    assert loaded is not None
    assert loaded["lifecycle"]["state"] == "IDLE"


@pytest.mark.unit
def test_native_decision_blocks_lifecycle_backfill_on_get_agent(agents_hub) -> None:
    from trade_integrations.autonomous_agents.mcp_actions import mcp_record_decision
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent_id = "aa_native_lc"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "mandate_config": {"allowed_instruments": ["options"]},
            "lifecycle": {"state": "IDLE", "last_transition_at": "2026-07-23T10:00:00Z"},
        }
    )

    result = mcp_record_decision(
        agent_id=agent_id,
        decision="HOLD",
        rationale="Wait for setup",
        confidence=45,
    )
    assert result["status"] == "ok"

    loaded = get_agent(agent_id)
    assert loaded is not None
    assert loaded["last_decision"]["decision"] == "HOLD"
    assert loaded["decisions"]
    assert loaded["lifecycle"]["state"] == "IDLE"
