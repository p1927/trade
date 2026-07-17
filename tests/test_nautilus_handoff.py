"""Tests for Phase 6 handoff ↔ autonomous agent integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.handoff import (  # noqa: E402
    build_handoff_shell_from_agent,
    clear_agent_position_state,
    clear_handoff,
    load_handoff,
    save_handoff,
    sync_watch_spec_to_handoff,
    update_agent_thesis_from_handoff,
)
from nautilus_openalgo_bridge.models import PositionHandoff, WatchRule, WatchSpec  # noqa: E402
from trade_integrations.autonomous_agents.store import save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _sample_agent(agent_id: str = "aa_test") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Test",
        "status": "running",
        "symbols": ["NIFTY"],
        "mandate_config": {
            "holding_period": "multi_day",
            "flatten_policy": "manual",
            "alert_rules": {"spot_move_pct": 0.5},
        },
        "constraints": {"max_daily_loss_inr": 2000},
        "thesis": {"prior_view": "range bound"},
        "vibe_session_id": "sess_1",
    }


def test_sync_watch_spec_creates_shell_handoff(hub_tmp: Path):
    agent_id = "aa_shell"
    save_agent(_sample_agent(agent_id))

    watch_spec = {
        "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.75}],
        "gate": {"skip_if_unchanged_minutes": 10},
    }
    handoff = sync_watch_spec_to_handoff(agent_id, watch_spec)
    assert handoff is not None
    assert handoff.agent_id == agent_id
    assert len(handoff.watch_spec.rules) == 1
    assert handoff.watch_spec.rules[0].threshold == 0.75
    assert load_handoff(agent_id) is not None


def test_sync_watch_spec_updates_existing(hub_tmp: Path):
    agent_id = "aa_update"
    save_agent(_sample_agent(agent_id))

    initial = PositionHandoff(
        agent_id=agent_id,
        widget_id="w1",
        underlying="NIFTY",
        legs=[],
        entry_spot=24000.0,
        watch_spec=WatchSpec(rules=[WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5)]),
    )
    save_handoff(initial)

    updated_spec = {
        "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 1.0}],
    }
    handoff = sync_watch_spec_to_handoff(agent_id, updated_spec)
    assert handoff is not None
    assert handoff.widget_id == "w1"
    assert handoff.entry_spot == 24000.0
    assert handoff.watch_spec.rules[0].threshold == 1.0


def test_update_agent_thesis_from_handoff(hub_tmp: Path):
    agent_id = "aa_thesis"
    save_agent(_sample_agent(agent_id))

    handoff = PositionHandoff(
        agent_id=agent_id,
        widget_id="tp_nifty",
        underlying="NIFTY",
        legs=[],
        entry_spot=24100.0,
        watch_spec=WatchSpec(
            rules=[WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5)]
        ),
    )
    update_agent_thesis_from_handoff(handoff)

    agent_path = hub_tmp / "_data" / "autonomous_agents" / f"{agent_id}.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    assert agent["thesis"]["entry_spot"] == 24100.0
    assert agent["thesis"]["active_widget_id"] == "tp_nifty"
    assert agent["watch_spec"]["rules"][0]["threshold"] == 0.5


def test_clear_agent_position_state(hub_tmp: Path):
    agent_id = "aa_exit"
    save_agent(_sample_agent(agent_id))
    save_handoff(
        PositionHandoff(
            agent_id=agent_id,
            widget_id="w1",
            underlying="NIFTY",
            legs=[],
            entry_spot=24000.0,
        )
    )

    clear_agent_position_state(agent_id)
    assert load_handoff(agent_id) is None

    agent_path = hub_tmp / "_data" / "autonomous_agents" / f"{agent_id}.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    assert "position_closed_at" in agent["thesis"]
    assert "active_widget_id" not in agent["thesis"]


def test_mcp_set_watch_spec_syncs_handoff(hub_tmp: Path):
    from trade_integrations.autonomous_agents.mcp_actions import mcp_set_watch_spec

    agent_id = "aa_mcp"
    save_agent(_sample_agent(agent_id))
    watch_spec = {
        "rules": [{"symbol": "INDIAVIX", "metric": "level_above", "threshold": 15.0}],
    }
    result = mcp_set_watch_spec(agent_id, watch_spec)
    assert result["status"] == "ok"
    assert result["handoff_synced"] is True

    handoff = load_handoff(agent_id)
    assert handoff is not None
    assert handoff.watch_spec.rules[0].symbol == "INDIAVIX"


def test_build_handoff_shell_from_agent(hub_tmp: Path):
    agent = _sample_agent("aa_build")
    shell = build_handoff_shell_from_agent(agent)
    assert shell.agent_id == "aa_build"
    assert shell.underlying == "NIFTY"
    assert shell.legs == []


def test_clear_handoff_missing_is_noop(hub_tmp: Path):
    assert clear_handoff("aa_missing") is False


def test_ensure_handoff_for_agent_from_hub_json(hub_tmp: Path):
    from nautilus_openalgo_bridge.handoff import ensure_handoff_for_agent, load_handoff

    agents_dir = hub_tmp / "_data" / "autonomous_agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "aa_hub.json").write_text(
        json.dumps(
            {
                "id": "aa_hub",
                "symbols": ["NIFTY"],
                "constraints": {"max_daily_loss_inr": 1800},
                "watch_spec": {
                    "rules": [
                        {"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.3, "direction": "either"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    handoff = ensure_handoff_for_agent("aa_hub")
    assert handoff is not None
    assert len(handoff.watch_spec.rules) == 1
    assert load_handoff("aa_hub") is not None
