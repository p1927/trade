"""Tests for handoff mtime reload in poll loop."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.handoff import load_handoff, save_handoff, stamp_handoff_context_generation  # noqa: E402
from nautilus_openalgo_bridge.models import PositionHandoff, WatchRule, WatchSpec  # noqa: E402
from nautilus_openalgo_bridge.runtime.poll_loop import maybe_reload_watch_spec  # noqa: E402
from trade_integrations.autonomous_agents.store import save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_reload_when_handoff_mtime_changes(hub_tmp: Path):
    agent_id = "aa_reload"
    save_agent(
        {
            "id": agent_id,
            "symbols": ["NIFTY"],
            "watch_spec": {"rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}]},
        }
    )
    initial_spec = WatchSpec(rules=[WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5)])
    save_handoff(
        PositionHandoff(
            agent_id=agent_id,
            widget_id=None,
            underlying="NIFTY",
            legs=[],
            entry_spot=24000.0,
            watch_spec=initial_spec,
        )
    )
    handoff_path = hub_tmp / "_data" / "nautilus_handoffs" / f"{agent_id}.json"
    first_mtime = handoff_path.stat().st_mtime

    spec, hm, am = maybe_reload_watch_spec(
        agent_id,
        initial_spec,
        last_handoff_mtime=None,
        last_agent_mtime=None,
    )
    assert hm == first_mtime
    assert spec.rules[0].threshold == 0.5

    time.sleep(0.02)
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    payload["watch_spec"] = {
        "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 1.25}],
    }
    handoff_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    new_mtime = handoff_path.stat().st_mtime

    spec2, hm2, _ = maybe_reload_watch_spec(
        agent_id,
        spec,
        last_handoff_mtime=first_mtime,
        last_agent_mtime=am,
    )
    assert hm2 == new_mtime
    assert spec2.rules[0].threshold == 1.25


def test_no_reload_when_mtime_unchanged(hub_tmp: Path):
    agent_id = "aa_stable"
    spec = WatchSpec(rules=[WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5)])
    save_handoff(
        PositionHandoff(
            agent_id=agent_id,
            widget_id=None,
            underlying="NIFTY",
            legs=[],
            entry_spot=24000.0,
            watch_spec=spec,
        )
    )
    mt = (hub_tmp / "_data" / "nautilus_handoffs" / f"{agent_id}.json").stat().st_mtime
    out_spec, out_mt, _ = maybe_reload_watch_spec(agent_id, spec, last_handoff_mtime=mt, last_agent_mtime=mt)
    assert out_spec is spec
    assert out_mt == mt


def test_stamp_handoff_context_generation(hub_tmp: Path):
    agent_id = "aa_ctx"
    save_agent(
        {
            "id": agent_id,
            "symbols": ["NIFTY"],
            "watch_spec": {"rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}]},
        }
    )
    generation = "2026-07-23T09:15:00+05:30"
    handoff = stamp_handoff_context_generation(agent_id, generation)
    assert handoff is not None
    assert handoff.context_generation == generation
    loaded = load_handoff(agent_id)
    assert loaded is not None
    assert loaded.context_generation == generation
