"""Tests for HF-backed stock simulator replay (BankNifty + options)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parents[1]
HF_ROOT = REPO / "data/nse/historic_data/replay/hf-india-index-options-1m"


pytestmark = pytest.mark.skipif(
    not (HF_ROOT / "index" / "BANKNIFTY.parquet").is_file(),
    reason="HF replay dataset not downloaded",
)


@pytest.fixture(autouse=True)
def _sim_env(monkeypatch):
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-15")
    monkeypatch.setenv("NSE_REPLAY_TIME", "10:30")
    monkeypatch.setenv("NSE_REPLAY_SPEED", "0")
    monkeypatch.setenv("SIM_EVAL_MODE", "stepped")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(REPO / "data/nse/historic_data"))
    monkeypatch.setenv("HUB_NO_LEARN", "1")
    import trade_integrations.stock_simulator.replay as replay_mod

    replay_mod._service = None
    yield
    replay_mod._service = None


def test_banknifty_index_bar_from_hf():
    from trade_integrations.stock_simulator.catalog import ReplayCatalog

    catalog = ReplayCatalog(REPO / "data/nse/historic_data")
    ts = datetime(2024, 4, 15, 10, 30, tzinfo=IST)
    bar = catalog.bar_at("BANKNIFTY", "NSE_INDEX", ts)
    assert bar is not None
    assert bar["ltp"] > 40000
    assert bar["high"] >= bar["low"]


def test_options_replay_store_real_oi():
    from trade_integrations.stock_simulator.options.replay_store import OptionsReplayStore

    store = OptionsReplayStore(REPO / "data/nse/historic_data")
    ts = datetime(2024, 4, 15, 10, 30, tzinfo=IST)
    chain = store.chain_at(
        underlying="BANKNIFTY",
        exchange="NSE_INDEX",
        spot=48000.0,
        sim_ts=ts,
        strike_count=5,
    )
    assert chain is not None
    assert chain["source"] == "hf_replay"
    assert len(chain["chain"]) == 5
    assert chain["total_call_oi"] > 0 or chain["total_put_oi"] > 0
    atm = chain["chain"][len(chain["chain"]) // 2]
    assert atm["ce_ltp"] >= 0
    assert atm["pe_ltp"] >= 0


def test_replay_service_banknifty_option_chain_uses_hf():
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service(reload=True)
    chain = svc.get_option_chain("BANKNIFTY", "NSE_INDEX", strike_count=7)
    assert chain["source"] == "hf_replay"
    assert chain["underlying"] == "BANKNIFTY"
    assert len(chain["chain"]) == 7
    assert chain["simulated"] is True


def test_replay_service_multiquotes_banknifty():
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service(reload=True)
    rows = svc.get_multiquotes([{"symbol": "BANKNIFTY", "exchange": "NSE_INDEX"}])
    assert len(rows) == 1
    assert "data" in rows[0]
    assert rows[0]["data"]["ltp"] > 0
    assert rows[0]["data"]["source"] == "stock_simulator"
