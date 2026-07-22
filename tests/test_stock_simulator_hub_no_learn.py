"""Hub no-learn gates during simulator replay."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _sim_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("HUB_NO_LEARN", "1")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2021-03-25")
    monkeypatch.setenv("NSE_REPLAY_SPEED", "0")
    monkeypatch.setenv("SIM_EVAL_MODE", "stepped")
    yield


def test_record_quote_snapshot_skipped_when_simulated():
    from trade_integrations.hub_capture.writers import record_quote_snapshot

    result = record_quote_snapshot(
        "NIFTY",
        {"ltp": 14500, "volume": 0, "simulated": True, "source": "stock_simulator"},
        source="stock_simulator",
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "hub_no_learn"


def test_record_chain_snapshot_skipped_when_hub_no_learn():
    from trade_integrations.hub_capture.writers import record_chain_snapshot

    result = record_chain_snapshot(
        "NIFTY",
        {"underlying": "NIFTY", "chain": [{"strike": 14500}], "simulated": True},
        source="stock_simulator",
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "hub_no_learn"


def test_intraday_capture_skipped_when_hub_no_learn():
    from trade_integrations.hub_capture.intraday import run_intraday_capture

    result = run_intraday_capture(entity_id="NIFTY")
    assert result["status"] == "skipped"
    assert result["reason"] == "hub_no_learn"


def test_sim_run_record_decision(monkeypatch):
    from trade_integrations.context.hub import get_hub_dir
    from trade_integrations.stock_simulator.sim_runs import finalize_run, load_run, record_decision, start_run

    hub = get_hub_dir()
    runs = hub / "_data" / "sim_runs"
    runs.mkdir(parents=True, exist_ok=True)

    run = start_run(agent_id="aa_test", replay_date="2021-03-25", starting_capital=100000)
    record_decision(agent_id="aa_test", decision={"decision": "HOLD", "confidence": 60})
    loaded = load_run(run["run_id"])
    assert loaded is not None
    assert len(loaded["decisions"]) == 1
    done = finalize_run(run_id=run["run_id"], session_pnl=250.0)
    assert done is not None
    assert done["status"] == "completed"
    assert done["session_pnl"] == 250.0


def test_patch_options_latest_skipped_under_hub_no_learn(monkeypatch, tmp_path):
    path = tmp_path / "options_research" / "latest.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"underlying":"NIFTY","spot":1}\n', encoding="utf-8")
    monkeypatch.setattr(
        "trade_integrations.hub_capture.channel._options_latest_path",
        lambda _entity: path,
    )
    from trade_integrations.hub_capture.channel import _patch_options_latest

    before = path.read_text(encoding="utf-8")
    _patch_options_latest(
        "NIFTY",
        {"chain": [{"strike": 14500}], "simulated": True},
        quote={"ltp": 14500.0, "simulated": True},
    )
    assert path.read_text(encoding="utf-8") == before


def test_state_store_persists_sim_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from trade_integrations.stock_simulator.state_store import load_sim_now, persist_sim_now

    ist = ZoneInfo("Asia/Kolkata")
    ts = datetime(2021, 3, 25, 11, 0, tzinfo=ist)
    persist_sim_now(replay_date="2021-03-25", sim_now=ts)
    loaded = load_sim_now(replay_date="2021-03-25")
    assert loaded is not None
    assert loaded.replace(second=0, microsecond=0) == ts.replace(second=0, microsecond=0)
