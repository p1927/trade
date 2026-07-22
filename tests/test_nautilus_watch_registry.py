"""Tests for multi-agent Nautilus watch registry."""

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


def test_registry_add_and_list(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.watch_registry.store import create_watch

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "_stop_existing", lambda: None)
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    for aid in ("aa_one", "aa_two"):
        save_agent(
            {
                "id": aid,
                "status": "running",
                "symbols": ["NIFTY"],
                "execution_market": "IN",
                "vibe_session_id": f"sess_{aid}",
            }
        )
        create_watch(
            owner_kind="autonomous_agent",
            owner_id=aid,
            vibe_session_id=f"sess_{aid}",
            watch_spec=_sample_spec(),
            symbols=["NIFTY"],
        )
    nw.add_agent_to_registry("aa_one")
    nw.add_agent_to_registry("aa_two")
    ids = nw.get_registry_agent_ids()
    assert "aa_one" in ids
    assert "aa_two" in ids
    assert nw.is_agent_in_registry("aa_one")


def test_registry_remove(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.watch_registry.store import create_watch

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "_stop_existing", lambda: None)
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    save_agent(
        {
            "id": "aa_us",
            "status": "running",
            "symbols": ["SPY"],
            "execution_market": "US",
            "vibe_session_id": "sess_us",
        }
    )
    create_watch(
        owner_kind="autonomous_agent",
        owner_id="aa_us",
        vibe_session_id="sess_us",
        watch_spec={
            "rules": [{"symbol": "SPY", "metric": "spot_move_pct", "threshold": 0.5}],
            "cooldown_sec": 300,
        },
        symbols=["SPY"],
    )
    nw.add_agent_to_registry("aa_us")
    nw.remove_agent_from_registry("aa_us")
    assert not nw.is_agent_in_registry("aa_us")


def test_registry_persisted_to_file(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.watch_registry.store import create_watch

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "_stop_existing", lambda: None)
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    save_agent(
        {
            "id": "aa_persist",
            "status": "running",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "vibe_session_id": "sess_persist",
        }
    )
    create_watch(
        owner_kind="autonomous_agent",
        owner_id="aa_persist",
        vibe_session_id="sess_persist",
        watch_spec=_sample_spec(),
        symbols=["NIFTY"],
    )
    nw.add_agent_to_registry("aa_persist")
    data = json.loads((log_dir / "nautilus-watch.agents.json").read_text())
    assert any(row.get("agent_id") == "aa_persist" for row in data.get("agents", []))
