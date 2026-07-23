"""Tests for BridgeSignalActor alert symbol and session gating."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _save_agent(hub: Path, agent_id: str, symbols: list[str]) -> None:
    path = hub / "_data" / "autonomous_agents" / f"{agent_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": agent_id,
                "symbols": symbols,
                "execution_market": "US",
                "constraints": {"mode": "paper"},
            }
        ),
        encoding="utf-8",
    )


def test_dispatch_vibe_alert_symbol_falls_back_to_agent_symbol(hub_tmp: Path) -> None:
    pytest.importorskip("nautilus_trader")
    from nautilus_openalgo_bridge.bridge_signal_actor import BridgeSignalActor, BridgeSignalActorConfig

    _save_agent(hub_tmp, "aa_spy", ["SPY"])
    actor = BridgeSignalActor(BridgeSignalActorConfig(agent_id="aa_spy"))
    with patch(
        "nautilus_openalgo_bridge.vibe_trigger.dispatch_watch_alert_sync",
        return_value={"status": "dispatched"},
    ) as mock_dispatch:
        actor._dispatch_vibe_alert("aa_spy", {"message": "review"})
    alert = mock_dispatch.call_args[0][1]
    assert alert.symbol == "SPY"


def test_on_signal_review_respects_agent_watch_session(hub_tmp: Path) -> None:
    pytest.importorskip("nautilus_trader")
    from nautilus_openalgo_bridge.bridge_signal_actor import BridgeSignalActor, BridgeSignalActorConfig

    _save_agent(hub_tmp, "aa_spy", ["SPY"])
    actor = BridgeSignalActor(BridgeSignalActorConfig(agent_id="aa_spy", trigger_vibe=True))
    signal = type("Sig", (), {"name": "REVIEW_NEEDED", "value": json.dumps({"message": "x"})})()
    with patch(
        "nautilus_openalgo_bridge.config.allow_vibe_alert_outside_market_hours", return_value=False
    ), patch("nautilus_openalgo_bridge.market_hours.is_agent_watch_session_open", return_value=False), patch.object(
        actor, "_dispatch_vibe_alert"
    ) as mock_dispatch:
        actor.on_signal(signal)
    mock_dispatch.assert_not_called()
