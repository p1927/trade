"""Tests for options trade widget auto-emit dedup in hub_bridge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from src.trade.hub_bridge import (  # noqa: E402
    WIDGET_EMIT_DEDUP_SECONDS,
    _record_widget_emitted,
    _should_emit_widget,
    session_widget_emitted,
)


@pytest.fixture(autouse=True)
def _clear_session_widget_emitted():
    session_widget_emitted.clear()
    yield
    session_widget_emitted.clear()


@pytest.mark.unit
class TestWidgetAutoEmitDedup:
    def test_should_emit_when_no_prior_emit(self):
        now = 1_000_000.0
        assert _should_emit_widget("sess-1", "NIFTY", now) is True

    def test_should_emit_normalizes_ticker_case(self):
        now = 1_000_000.0
        _record_widget_emitted("sess-1", "nifty", now, widget_kind="options")
        assert _should_emit_widget("sess-1", "NIFTY", now + 1, widget_kind="options") is False

    def test_dedup_prevents_second_emit_within_window(self):
        now = 2_000_000.0
        session_id = "sess-dedup"
        ticker = "RELIANCE"

        assert _should_emit_widget(session_id, ticker, now) is True
        _record_widget_emitted(session_id, ticker, now, widget_kind="options")

        within_window = now + WIDGET_EMIT_DEDUP_SECONDS - 1
        assert _should_emit_widget(session_id, ticker, within_window) is False

    def test_allows_emit_after_dedup_window(self):
        now = 3_000_000.0
        session_id = "sess-expired"
        ticker = "BANKNIFTY"

        _record_widget_emitted(session_id, ticker, now, widget_kind="options")
        after_window = now + WIDGET_EMIT_DEDUP_SECONDS
        assert _should_emit_widget(session_id, ticker, after_window) is True

    def test_dedup_is_per_session(self):
        now = 4_000_000.0
        ticker = "NIFTY"

        _record_widget_emitted("sess-a", ticker, now, widget_kind="options")
        assert _should_emit_widget("sess-a", ticker, now + 1) is False
        assert _should_emit_widget("sess-b", ticker, now + 1) is True

    def test_empty_session_id_never_emits(self):
        assert _should_emit_widget("", "NIFTY", 5_000_000.0) is False
