"""Tests for autonomous watch tick routing."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.store import save_agent  # noqa: E402
from trade_integrations.autonomous_agents.watch import (  # noqa: E402
    _detached_nautilus_watching,
    run_watch_tick,
    should_post_watch_to_chat,
)


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _in_agent(agent_id: str = "aa_watch") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Watch Test",
        "status": "running",
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate_config": {
            "market_hours_only": True,
            "allowed_instruments": ["options"],
        },
        "vibe_session_id": "sess_watch",
    }


def _us_agent(agent_id: str = "aa_us") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "US Watch Test",
        "status": "running",
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate_config": {
            "market_hours_only": False,
            "allowed_instruments": ["equity"],
        },
        "vibe_session_id": "sess_us",
    }


def test_should_post_watch_to_chat_false_when_market_closed():
    agent = _in_agent()
    feedback = {"alerts": [], "requires_action": False}
    assert should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=True) is False


def test_should_post_watch_to_chat_false_when_detached_nautilus_quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._detached_nautilus_watching",
        lambda _id: True,
    )
    agent = _in_agent()
    feedback = {"alerts": [], "requires_action": False}
    assert should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False) is False


def test_detached_nautilus_watching_false_when_bound_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.get_watch_process_status",
        lambda **_: {"alive": True, "bound_agent_id": "", "registry_agent_ids": []},
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.is_agent_in_registry",
        lambda _id: False,
    )
    assert _detached_nautilus_watching("aa_watch") is False


def test_detached_nautilus_watching_true_when_in_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.get_watch_process_status",
        lambda **_: {"alive": True, "bound_agent_id": None, "registry_agent_ids": ["aa_watch"]},
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.is_agent_in_registry",
        lambda aid: aid == "aa_watch",
    )
    assert _detached_nautilus_watching("aa_watch") is True


@pytest.mark.asyncio
async def test_market_closed_watch_does_not_post_chat(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    save_agent(_in_agent())
    append = AsyncMock()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._append_watch_system_message",
        append,
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.is_market_open_for_market",
        lambda _market, **_: False,
    )

    result = await run_watch_tick("aa_watch")
    assert result["reason"] == "outside_market_hours"
    append.assert_not_called()


@pytest.mark.asyncio
async def test_detached_nautilus_skips_poll_and_chat(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    save_agent(_in_agent())
    append = AsyncMock()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._append_watch_system_message",
        append,
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.is_market_open_for_market",
        lambda _market, **_: True,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._detached_nautilus_watching",
        lambda _id: True,
    )

    with patch("nautilus_openalgo_bridge.runtime.poll_loop.run_once") as mock_run_once:
        result = await run_watch_tick("aa_watch")
        mock_run_once.assert_not_called()

    assert result.get("delegated_to_detached") is True
    append.assert_not_called()


@pytest.mark.asyncio
async def test_us_agent_does_not_call_openalgo_poll(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    save_agent(_us_agent())
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.is_market_open_for_market",
        lambda _market, **_: True,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._detached_nautilus_watching",
        lambda _id: False,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.watch._nautilus_watch_enabled",
        lambda: True,
    )

    with patch("nautilus_openalgo_bridge.runtime.poll_loop.run_once") as mock_openalgo:
        with patch("nautilus_openalgo_bridge.runtime.poll_loop.run_once_alpaca") as mock_alpaca:
            mock_alpaca.return_value = {"alerts": [], "quotes": {}}
            result = await run_watch_tick("aa_us")
            mock_openalgo.assert_not_called()
            mock_alpaca.assert_called_once()

    assert "nautilus_alpaca" in str(result.get("watch_path", ""))
