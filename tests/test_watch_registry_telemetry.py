"""Tests for watch registry live telemetry."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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


def _sample_spec(threshold: float = 0.5) -> dict:
    return {
        "rules": [
            {
                "symbol": "NIFTY",
                "metric": "spot_move_pct",
                "threshold": threshold,
                "direction": "either",
                "exchange": "NSE",
                "label": "NIFTY",
            }
        ],
        "cooldown_sec": 300,
    }


def _mock_quote(symbol: str, ltp: float, *, oi: float | None = None, volume: float | None = None):
    return SimpleNamespace(
        symbol=symbol.upper(),
        exchange="NSE",
        ltp=ltp,
        oi=oi,
        volume=volume,
    )


def test_live_snapshot_spot_move_distance(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.watch_registry import create_watch, telemetry

    telemetry.clear_telemetry_baseline_cache()

    watch = create_watch(
        owner_kind="session",
        owner_id="sess_live",
        vibe_session_id="sess_live",
        watch_spec=_sample_spec(threshold=0.5),
    )

    baseline_ltp = 24_000.0

    calls = {"n": 0}

    def fake_poll(*, symbols):
        calls["n"] += 1
        ltp = baseline_ltp if calls["n"] == 1 else baseline_ltp * 1.0052
        return {sym: _mock_quote(sym, ltp) for sym in symbols}

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.data_feed.OpenAlgoQuoteFeed.poll",
        lambda self, symbols: fake_poll(symbols=symbols),
    )

    first = telemetry.build_watches_live_snapshot(session_id="sess_live")
    assert first["status"] == "ok"
    assert len(first["watches"]) == 1
    rule = first["watches"][0]["rules"][0]
    assert rule["quote_available"] is True
    assert "move" in rule["condition_text"]
    assert rule["current"]["ltp"] == baseline_ltp
    assert rule["current"]["move_pct"] == pytest.approx(0.0, abs=0.001)
    assert rule["distance"]["fired"] is False
    assert rule["distance"]["unit"] == "pct"
    assert rule["distance"]["remaining"] == pytest.approx(0.5, abs=0.01)

    second = telemetry.build_watches_live_snapshot(session_id="sess_live")
    rule2 = second["watches"][0]["rules"][0]
    assert rule2["distance"]["fired"] is True
    assert rule2["distance"]["remaining"] == 0.0
    assert watch["watch_id"] == first["watches"][0]["watch_id"]


def test_agent_id_preferred_over_session(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.watch_registry import create_watch, telemetry

    telemetry.clear_telemetry_baseline_cache()

    create_watch(
        owner_kind="session",
        owner_id="sess_x",
        vibe_session_id="sess_x",
        watch_spec=_sample_spec(threshold=1.0),
        label="session watch",
    )
    create_watch(
        owner_kind="autonomous_agent",
        owner_id="aa_y",
        vibe_session_id="sess_y",
        watch_spec=_sample_spec(threshold=2.0),
        label="agent watch",
    )

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.data_feed.OpenAlgoQuoteFeed.poll",
        lambda self, symbols: {sym: _mock_quote(sym, 100.0) for sym in symbols},
    )

    result = telemetry.build_watches_live_snapshot(session_id="sess_x", agent_id="aa_y")
    assert len(result["watches"]) == 1
    assert result["watches"][0]["label"] == "agent watch"
    assert result["watches"][0]["rules"][0]["threshold"] == 2.0


def test_level_above_condition_text(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.watch_registry import create_watch, telemetry

    telemetry.clear_telemetry_baseline_cache()
    create_watch(
        owner_kind="session",
        owner_id="sess_vix",
        vibe_session_id="sess_vix",
        watch_spec={
            "rules": [
                {
                    "symbol": "INDIAVIX",
                    "metric": "level_above",
                    "threshold": 14.0,
                    "label": "INDIAVIX",
                }
            ]
        },
    )

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.data_feed.OpenAlgoQuoteFeed.poll",
        lambda self, symbols: {sym: _mock_quote(sym, 13.5) for sym in symbols},
    )

    result = telemetry.build_watches_live_snapshot(session_id="sess_vix")
    rule = result["watches"][0]["rules"][0]
    assert "level above" in rule["condition_text"]
    assert rule["current"]["ltp"] == 13.5
    assert rule["distance"]["fired"] is False
    assert rule["distance"]["remaining"] == pytest.approx(0.5, abs=0.01)
    assert rule["distance"]["unit"] == "points"


def test_empty_owner_returns_empty_watches(hub_tmp: Path):
    from trade_integrations.watch_registry.telemetry import build_watches_live_snapshot

    result = build_watches_live_snapshot()
    assert result["status"] == "ok"
    assert result["watches"] == []
