"""Tests for TradingAgents debate wiring in hub_bridge."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
INTEGRATIONS = ROOT / "integrations"
for path in (AGENT_SRC, INTEGRATIONS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.trade.hub_bridge import (  # noqa: E402
    _maybe_start_debate,
    prefetch_research_for_message,
)


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event_type: str, data: dict) -> None:
        self.events.append((session_id, event_type, data))


@pytest.mark.unit
def test_maybe_start_debate_emits_ready_when_cache_fresh():
    bus = _FakeEventBus()
    cached = {"ticker": "NIFTY", "rating": "HOLD", "asset_type": "options"}

    with (
        patch("src.trade.hub_bridge.load_debate_artifact", return_value=cached),
        patch("trade_integrations.context.hub.is_agent_debate_cache_fresh", return_value=True),
        patch("src.trade.hub_bridge.run_agent_debate_sync") as run_debate,
    ):
        _maybe_start_debate("sess-debate", "NIFTY", "options", bus)

    run_debate.assert_not_called()
    debate_events = [e for e in bus.events if e[1] == "research.debate"]
    assert len(debate_events) == 1
    assert debate_events[0][2]["status"] == "ready"
    assert debate_events[0][2]["debate"] == cached


@pytest.mark.unit
def test_finalize_message_triggers_debate_worker():
    bus = _FakeEventBus()
    artifact = {
        "ticker": "NIFTY",
        "plan_status": "ready",
        "asset_type": "options",
        "ranked_strategies": [{"name": "Iron condor"}],
    }
    debate_payload = {"ticker": "NIFTY", "rating": "BUY", "asset_type": "options"}

    with (
        patch("src.trade.session_context.resolve_prefetch_ticker", return_value="NIFTY"),
        patch("src.trade.session_context.infer_prefetch_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=artifact),
        patch("src.trade.hub_bridge._options_auto_widget_enabled", return_value=False),
        patch(
            "trade_integrations.tools.index_research_tools.is_index_research_eligible",
            return_value=False,
        ),
        patch("src.trade.hub_bridge.load_debate_artifact", return_value=None),
        patch("trade_integrations.context.hub.is_agent_debate_cache_fresh", return_value=False),
        patch("src.trade.hub_bridge.is_debate_running", return_value=False),
        patch("src.trade.hub_bridge.run_agent_debate_sync", return_value=debate_payload) as run_debate,
        patch("src.trade.hub_bridge.threading.Thread") as mock_thread,
    ):
        worker = mock_thread.return_value

        def _run_worker() -> None:
            target = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1]["target"]
            target()

        worker.start.side_effect = _run_worker
        prefetch_research_for_message("sess-finalize", "Finalize the NIFTY plan", bus)

    run_debate.assert_called_once_with("NIFTY", asset_type="options")
    statuses = [e[2]["status"] for e in bus.events if e[1] == "research.debate"]
    assert statuses == ["started", "ready"]
    ready = [e for e in bus.events if e[1] == "research.debate" and e[2]["status"] == "ready"][0]
    assert ready[2]["debate"] == debate_payload
