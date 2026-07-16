"""Tests for index research prefetch wiring in hub_bridge."""

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
def test_prefetch_emits_index_artifact_and_widget():
    options_artifact = {"ticker": "NIFTY", "plan_status": "ready", "asset_type": "options"}
    index_artifact = {
        "ticker": "NIFTY",
        "plan_status": "ready",
        "asset_type": "index",
        "prediction": {"view": "bullish"},
        "top_factors": [{"factor": "usd_inr"}],
    }
    bus = _FakeEventBus()

    with (
        patch("src.trade.session_context.resolve_prefetch_ticker", return_value="NIFTY"),
        patch("src.trade.session_context.infer_prefetch_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=options_artifact),
        patch("src.trade.hub_bridge.prefetch_index_hub_plan", return_value=index_artifact),
        patch("src.trade.hub_bridge._options_auto_widget_enabled", return_value=False),
        patch("src.trade.hub_bridge._index_auto_widget_enabled", return_value=True),
        patch("src.trade.hub_bridge._maybe_emit_index_widget") as emit_index,
    ):
        context = prefetch_research_for_message("sess-index", "Where is NIFTY headed?", bus)

    artifact_events = [e for e in bus.events if e[1] == "research.artifact"]
    assert len(artifact_events) == 2
    asset_types = {e[2]["asset_type"] for e in artifact_events}
    assert asset_types == {"options", "index"}
    emit_index.assert_called_once_with(
        bus,
        "sess-index",
        "NIFTY",
        widget_intent="index_outlook",
    )
    assert "[index_research_context]" in context
    assert "[research_context]" in context


@pytest.mark.unit
def test_index_widget_respects_dedup_separately_from_options():
    from src.trade.hub_bridge import (
        WIDGET_EMIT_DEDUP_SECONDS,
        _record_widget_emitted,
        _should_emit_widget,
    )

    now = 6_000_000.0
    session_id = "sess-both"
    ticker = "NIFTY"

    _record_widget_emitted(session_id, ticker, now, widget_kind="options")
    assert _should_emit_widget(session_id, ticker, now + 1, widget_kind="options") is False
    assert _should_emit_widget(session_id, ticker, now + 1, widget_kind="index") is True

    _record_widget_emitted(session_id, ticker, now, widget_kind="index")
    after_window = now + WIDGET_EMIT_DEDUP_SECONDS
    assert _should_emit_widget(session_id, ticker, after_window, widget_kind="index") is True
