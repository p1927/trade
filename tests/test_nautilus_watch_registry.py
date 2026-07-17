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
def log_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch._log_dir",
        lambda: log,
    )
    return log


def test_registry_add_and_list(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw

    monkeypatch.setattr(
        nw,
        "_agent_market_and_symbols",
        lambda _aid: ("IN", ["NIFTY"]),
    )
    nw.add_agent_to_registry("aa_one")
    nw.add_agent_to_registry("aa_two")
    ids = nw.get_registry_agent_ids()
    assert "aa_one" in ids
    assert "aa_two" in ids
    assert nw.is_agent_in_registry("aa_one")


def test_registry_remove(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw

    monkeypatch.setattr(nw, "_agent_market_and_symbols", lambda _aid: ("US", ["SPY"]))
    nw.add_agent_to_registry("aa_us")
    nw.remove_agent_from_registry("aa_us")
    assert not nw.is_agent_in_registry("aa_us")


def test_registry_persisted_to_file(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw

    monkeypatch.setattr(nw, "_agent_market_and_symbols", lambda _aid: ("IN", ["NIFTY"]))
    nw.add_agent_to_registry("aa_persist")
    data = json.loads((log_dir / "nautilus-watch.agents.json").read_text())
    assert any(row.get("agent_id") == "aa_persist" for row in data.get("agents", []))
