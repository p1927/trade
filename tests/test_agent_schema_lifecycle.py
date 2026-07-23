"""Agent schema lifecycle backfill tests."""

from __future__ import annotations

import json
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
    (hub / "_data" / "auto_paper" / "sessions").mkdir(parents=True)
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
def test_get_agent_backfills_lifecycle_from_session(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent_id = "aa_backfill_lc"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
        }
    )
    session_payload = {
        "enabled": True,
        "autonomous_agent_id": agent_id,
        "lifecycle": {
            "state": "MONITORING",
            "active_strategy": "iron_condor",
            "tried_strategies": ["iron_condor"],
        },
    }
    monkeypatch.setattr(
        "trade_integrations.auto_paper.session_store.load_session",
        lambda **kwargs: session_payload if kwargs.get("autonomous_agent_id") == agent_id else {},
    )

    loaded = get_agent(agent_id)
    assert loaded is not None
    assert loaded["lifecycle"]["state"] == "MONITORING"
    assert loaded["lifecycle"]["active_strategy"] == "iron_condor"
