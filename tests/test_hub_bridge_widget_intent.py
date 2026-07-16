"""Tests for widget intent gating in hub_bridge prefetch."""

from __future__ import annotations

import os
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
    prefetch_research_for_message,
    session_widget_emitted,
)


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event_type: str, data: dict) -> None:
        self.events.append((session_id, event_type, data))


@pytest.fixture(autouse=True)
def _clear_session_widget_emitted():
    session_widget_emitted.clear()
    yield
    session_widget_emitted.clear()


@pytest.mark.unit
def test_prefetch_no_widget_when_intent_none_even_if_auto_enabled():
    artifact = {
        "ticker": "NIFTY",
        "plan_status": "ready",
        "asset_type": "options",
        "ranked_strategies": [{"name": "Iron condor"}],
        "recommended": {"name": "Iron condor", "legs": [{"symbol": "X"}]},
    }
    bus = _FakeEventBus()

    with (
        patch("src.trade.session_context.resolve_prefetch_ticker", return_value="NIFTY"),
        patch("src.trade.session_context.infer_prefetch_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=artifact),
        patch("src.trade.hub_bridge._options_auto_widget_enabled", return_value=True),
        patch("src.trade.hub_bridge._maybe_emit_options_widget") as emit_opt,
        patch(
            "trade_integrations.tools.index_research_tools.is_index_research_eligible",
            return_value=False,
        ),
    ):
        prefetch_research_for_message("sess-none", "NIFTY", bus)

    emit_opt.assert_not_called()


@pytest.mark.unit
def test_prefetch_emits_options_widget_on_strategy_intent():
    artifact = {
        "ticker": "NIFTY",
        "plan_status": "ready",
        "asset_type": "options",
        "ranked_strategies": [{"name": "Iron condor"}],
    }
    bus = _FakeEventBus()

    with (
        patch("src.trade.session_context.resolve_prefetch_ticker", return_value="NIFTY"),
        patch("src.trade.session_context.infer_prefetch_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=artifact),
        patch("src.trade.hub_bridge._options_auto_widget_enabled", return_value=True),
        patch("src.trade.hub_bridge._maybe_emit_options_widget") as emit_opt,
        patch(
            "trade_integrations.tools.index_research_tools.is_index_research_eligible",
            return_value=False,
        ),
    ):
        prefetch_research_for_message("sess-strat", "Iron condor on NIFTY", bus)

    emit_opt.assert_called_once_with(
        bus,
        "sess-strat",
        "NIFTY",
        widget_intent="options_strategy",
    )
