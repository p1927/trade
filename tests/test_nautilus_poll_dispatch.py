"""Tests for watch alert dispatch market routing (poll_loop helper used by node path)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import BridgeSignal, WatchAlert, WatchRule  # noqa: E402
from nautilus_openalgo_bridge.runtime.poll_loop import _dispatch_alerts  # noqa: E402


def test_dispatch_alerts_uses_agent_watch_session_gate() -> None:
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol="SPY", metric="spot_move_pct", threshold=1.0),
        symbol="SPY",
        message="review",
    )
    with patch(
        "nautilus_openalgo_bridge.market_hours.is_agent_watch_session_open", return_value=True
    ) as mock_session, patch(
        "nautilus_openalgo_bridge.vibe_trigger.dispatch_watch_alert_sync",
        return_value={"status": "dispatched"},
    ):
        result = _dispatch_alerts("aa_spy", [alert], quotes={}, trigger_vibe=True)
    mock_session.assert_called_once_with("aa_spy")
    assert result[0]["status"] == "dispatched"


def test_dispatch_alerts_skipped_when_agent_session_closed() -> None:
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol="SPY", metric="spot_move_pct", threshold=1.0),
        symbol="SPY",
        message="review",
    )
    with patch(
        "nautilus_openalgo_bridge.config.allow_vibe_alert_outside_market_hours", return_value=False
    ), patch("nautilus_openalgo_bridge.market_hours.is_agent_watch_session_open", return_value=False):
        result = _dispatch_alerts("aa_spy", [alert], quotes={}, trigger_vibe=True)
    assert result == [{"status": "skipped", "reason": "outside_market_hours"}]
