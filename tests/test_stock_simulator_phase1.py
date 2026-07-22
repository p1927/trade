"""Tests for stock simulator Phase 1a core replay engine."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _sim_env(monkeypatch):
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2021-03-25")
    monkeypatch.setenv("NSE_REPLAY_TIME", "09:15")
    monkeypatch.setenv("NSE_REPLAY_SPEED", "0")
    monkeypatch.setenv("SIM_EVAL_MODE", "stepped")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(REPO / "data/nse/historic_data"))
    monkeypatch.setenv("HUB_NO_LEARN", "1")
    # Force fresh service per test
    import trade_integrations.stock_simulator.replay as replay_mod

    replay_mod._service = None
    yield
    replay_mod._service = None


def test_sim_clock_session_open():
    from trade_integrations.stock_simulator.sim_clock import SimClock

    clock = SimClock(replay_date="2021-03-25", replay_time="09:15", speed=0, stepped=True)
    assert clock.is_session_open(now=clock.now_ist())
    after_close = datetime(2021, 3, 25, 16, 0, tzinfo=IST)
    assert not clock.is_session_open(now=after_close)


def test_catalog_bar_lookup():
    from trade_integrations.stock_simulator.catalog import ReplayCatalog

    catalog = ReplayCatalog(REPO / "data/nse/historic_data")
    ts = datetime(2021, 3, 25, 10, 0, tzinfo=IST)
    bar = catalog.bar_at("NIFTY", "NSE_INDEX", ts)
    assert bar is not None
    assert bar["ltp"] > 0
    assert bar["high"] >= bar["low"]


def test_replay_service_multiquotes_shape():
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service(reload=True)
    svc.step(minutes=15)
    rows = svc.get_multiquotes([{"symbol": "NIFTY", "exchange": "NSE_INDEX"}])
    assert len(rows) == 1
    data = rows[0]["data"]
    assert data["simulated"] is True
    assert data["source"] == "stock_simulator"
    assert data["ltp"] > 0


def test_sim_clock_step_wrap_when_loop():
    from trade_integrations.stock_simulator.sim_clock import SimClock

    clock = SimClock(replay_date="2021-03-25", replay_time="09:15", speed=0, stepped=True, loop=True)
    for _ in range(80):
        clock.step(minutes=5)
    assert clock.now_ist().time().hour == 9


def test_options_synthesizer_chain():
    from trade_integrations.stock_simulator.options.synthesizer import OptionsSynthesizer

    synth = OptionsSynthesizer()
    ts = datetime(2021, 3, 25, 10, 0, tzinfo=IST)
    chain = synth.build_chain(
        underlying="NIFTY",
        exchange="NSE_INDEX",
        spot=14500.0,
        sim_ts=ts,
        strike_count=5,
    )
    assert chain["simulated"] is True
    assert len(chain["chain"]) == 5
    assert chain["underlying_ltp"] == 14500.0


def test_catalog_dedupes_intraday_session_bars():
    from trade_integrations.stock_simulator.catalog import ReplayCatalog

    catalog = ReplayCatalog(REPO / "data/nse/historic_data")
    frame = catalog._load_legacy_nifty_csv()
    day_count = len(frame[frame["day"] == "2021-03-25"])
    # Full NSE session 09:15–15:30 at 5m ≈ 76 bars (archive ends ~15:25 on this day)
    assert 70 <= day_count <= 78


def test_stepped_advance_after_watch_helper():
    from trade_integrations.stock_simulator.integration import maybe_advance_sim_after_watch
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service(reload=True)
    before = svc.sim_now()
    step = maybe_advance_sim_after_watch(minutes=5)
    assert step is not None
    assert svc.sim_now() > before
