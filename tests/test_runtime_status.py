"""Tests for autonomous agent runtime observability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_integrations.autonomous_agents.runtime_status import build_agent_runtime


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    (hub / "_data" / "nautilus_handoffs").mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


@pytest.mark.unit
class TestRuntimeStatus:
    def test_handoff_active_when_watch_shell_exists(self, hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        agent_id = "aa_rt1"
        handoff_path = hub_tmp / "_data" / "nautilus_handoffs" / f"{agent_id}.json"
        handoff_path.write_text(
            json.dumps(
                {
                    "agent_id": agent_id,
                    "underlying": "NIFTY",
                    "legs": [],
                    "entry_spot": 24500.0,
                    "watch_spec": {"rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}]},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            "trade_integrations.autonomous_agents.runtime_status._nautilus_process_alive",
            lambda: False,
        )
        monkeypatch.setattr(
            "trade_integrations.autonomous_agents.nautilus_watch.get_watch_process_status",
            lambda: {"bound_agent_id": agent_id, "alive": False, "enabled": True},
        )

        agent = {
            "id": agent_id,
            "symbols": ["NIFTY"],
            "status": "running",
            "execution_market": "IN",
            "mandate_config": {"allowed_instruments": ["options"]},
            "constraints": {"mode": "paper"},
            "schedules": {"watch_ms": 420000},
        }
        runtime = build_agent_runtime(agent)
        assert runtime["handoff_active"] is True
        assert runtime["watch_configured"] is True
        assert runtime["nautilus_bound_agent_id"] == agent_id
