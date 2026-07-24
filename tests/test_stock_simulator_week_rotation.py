"""Tests for stock simulator last-week rotation."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


def test_sim_clock_week_rotation_advances_dates():
    from trade_integrations.stock_simulator.sim_clock import SimClock

    changes: list[tuple[str, str]] = []

    clock = SimClock(
        replay_date="2026-06-25",
        replay_time="09:15",
        speed=0,
        loop=True,
        stepped=True,
        week_dates=["2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"],
        week_index=0,
        on_replay_date_change=lambda old, new: changes.append((old, new)),
    )
    close = datetime(2026, 6, 25, 15, 30, tzinfo=IST)
    clock._sim_now = close
    clock.step(minutes=1)
    assert clock.replay_date == "2026-06-29"
    assert changes == [("2026-06-25", "2026-06-29")]

    clock._week_index = len(clock.week_dates) - 1
    clock._apply_replay_date(clock.week_dates[-1])
    clock._sim_now = datetime(2026, 7, 2, 15, 30, tzinfo=IST)
    clock.step(minutes=1)
    assert clock.replay_date == "2026-06-25"
    assert changes[-1] == ("2026-07-02", "2026-06-25")


def test_latest_trading_days_from_local_hf():
    from pathlib import Path

    from trade_integrations.stock_simulator.week_rotation import latest_trading_days

    repo = Path(__file__).resolve().parents[1]
    data_root = repo / "data/nse/historic_data"
    hf_index = data_root / "replay/hf-india-index-options-1m/index/NIFTY.parquet"
    if not hf_index.is_file():
        pytest.skip("HF replay dataset not downloaded")

    days = latest_trading_days(data_root, 5)
    assert len(days) == 5
    assert days[-1] >= days[0]
    assert days[-1] == "2026-07-02"


def test_resolve_week_replay_date_honors_explicit_outside_window():
    from pathlib import Path

    from trade_integrations.stock_simulator.week_rotation import resolve_week_replay_date

    repo = Path(__file__).resolve().parents[1]
    data_root = repo / "data/nse/historic_data"
    hf_index = data_root / "replay/hf-india-index-options-1m/index/NIFTY.parquet"
    if not hf_index.is_file():
        pytest.skip("HF replay dataset not downloaded")

    replay_date, week_dates = resolve_week_replay_date(data_root, "2024-04-15", n=5)
    assert replay_date == "2024-04-15"
    assert len(week_dates) == 5
    assert "2024-04-15" not in week_dates


def test_sim_clock_no_rotation_without_week_dates():
    from trade_integrations.stock_simulator.sim_clock import SimClock

    changes: list[tuple[str, str]] = []
    clock = SimClock(
        replay_date="2024-04-15",
        replay_time="09:15",
        speed=0,
        loop=True,
        stepped=True,
        week_dates=None,
        on_replay_date_change=lambda old, new: changes.append((old, new)),
    )
    assert not clock.week_mode

    clock._sim_now = datetime(2024, 4, 15, 15, 30, tzinfo=IST)
    clock.step(minutes=1)
    assert clock.replay_date == "2024-04-15"
    assert changes == []


def test_replay_service_skips_week_rotation_for_outside_window_date(monkeypatch):
    from pathlib import Path

    from trade_integrations.stock_simulator.replay import get_replay_service

    repo = Path(__file__).resolve().parents[1]
    data_root = repo / "data/nse/historic_data"
    hf_index = data_root / "replay/hf-india-index-options-1m/index/NIFTY.parquet"
    if not hf_index.is_file():
        pytest.skip("HF replay dataset not downloaded")

    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("NSE_REPLAY_WEEK_MODE", "1")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-15")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NSE_REPLAY_SPEED", "0")
    monkeypatch.setenv("SIM_EVAL_MODE", "stepped")
    import trade_integrations.stock_simulator.replay as replay_mod

    replay_mod._service = None
    svc = get_replay_service(reload=True)
    assert svc.config.replay_date == "2024-04-15"
    assert not svc.clock.week_mode
    import os

    assert os.getenv("NSE_REPLAY_DATE") == "2024-04-15"

    st = svc.status()
    assert st["week_mode"] is False
    assert st["clock"]["week_mode"] is False
    assert "2024-04-15" not in st["week_dates"]

    svc.clock._sim_now = datetime(2024, 4, 15, 15, 30, tzinfo=IST)
    svc.clock.step(minutes=1)
    assert svc.clock.replay_date == "2024-04-15"
    replay_mod._service = None


def test_load_sim_config_week_mode_resolves_latest(monkeypatch):
    from pathlib import Path

    from trade_integrations.stock_simulator.config import load_sim_config

    repo = Path(__file__).resolve().parents[1]
    data_root = repo / "data/nse/historic_data"
    hf_index = data_root / "replay/hf-india-index-options-1m/index/NIFTY.parquet"
    if not hf_index.is_file():
        pytest.skip("HF replay dataset not downloaded")

    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("NSE_REPLAY_WEEK_MODE", "1")
    monkeypatch.setenv("NSE_REPLAY_WEEK_COUNT", "5")
    monkeypatch.delenv("NSE_REPLAY_DATE", raising=False)
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(data_root))
    cfg = load_sim_config()
    assert cfg.week_mode is True
    assert len(cfg.week_dates) == 5
    assert cfg.replay_date == cfg.week_dates[-1]
