"""Tests for unified watch registry (session + autonomous owners)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


@pytest.fixture
def log_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch._log_dir",
        lambda: log,
    )
    return log


def _sample_spec() -> dict:
    return {
        "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}],
        "cooldown_sec": 300,
    }


def test_create_list_delete_session_watch(hub_tmp: Path, log_dir: Path):
    from trade_integrations.watch_registry import (
        create_watch,
        delete_watch,
        list_watches,
        nautilus_owner_id,
    )

    watch = create_watch(
        owner_kind="session",
        owner_id="sess_abc",
        vibe_session_id="sess_abc",
        watch_spec=_sample_spec(),
        label="NIFTY move",
    )
    assert watch["watch_id"].startswith("w_")
    assert watch["symbols"] == ["NIFTY"]

    rows = list_watches(owner_kind="session", owner_id="sess_abc")
    assert len(rows) == 1
    assert nautilus_owner_id(owner_kind="session", owner_id="sess_abc") == "ws_sess_abc"

    assert delete_watch(watch["watch_id"]) is True
    assert list_watches(owner_kind="session", owner_id="sess_abc") == []


def test_sync_nautilus_registry_from_session_watch(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.watch_registry import create_watch, sync_nautilus_registry_from_watches
    from trade_integrations.autonomous_agents import nautilus_watch as nw

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "_stop_existing", lambda: None)
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    create_watch(
        owner_kind="session",
        owner_id="sess_sync",
        vibe_session_id="sess_sync",
        watch_spec=_sample_spec(),
    )
    result = sync_nautilus_registry_from_watches(restart_if_changed=False)
    assert result["status"] == "ok"
    assert "ws_sess_sync" in nw.get_registry_agent_ids()


def test_list_active_nautilus_owners_includes_infra_paused_plan_approved(
    hub_tmp: Path,
    log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.watch_registry import create_watch, list_active_nautilus_owners

    agents_dir = hub_tmp / "_data" / "autonomous_agents"
    agents_dir.mkdir(parents=True)
    agent_id = "aa_infra_watch"
    save_agent(
        {
            "id": agent_id,
            "type": "autonomous_agent.instance",
            "name": "Infra paused",
            "status": "paused",
            "pause_reason": "infra",
            "plan_approved_at": "2026-07-16T20:00:00+00:00",
            "vibe_session_id": "sess_infra",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "execution_backend": "openalgo",
            "constraints": {"mode": "paper"},
            "mandate_config": {},
            "watch_spec": _sample_spec(),
        }
    )
    create_watch(
        owner_kind="autonomous_agent",
        owner_id=agent_id,
        vibe_session_id="sess_infra",
        watch_spec=_sample_spec(),
        symbols=["NIFTY"],
    )
    owners = list_active_nautilus_owners()
    assert any(row.get("owner_id") == agent_id for row in owners)
