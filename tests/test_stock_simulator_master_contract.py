"""Tests for HF-backed simulator master contract builder."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trade_integrations.stock_simulator.master_contract import (
    build_symtoken_rows,
    openalgo_option_symbol,
    parse_openalgo_option_symbol,
)

REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO / "data/nse/historic_data"
HF_ROOT = DATA_ROOT / "replay/hf-india-index-options-1m"


def test_openalgo_option_symbol_format():
    sym = openalgo_option_symbol("NIFTY", date(2024, 4, 25), 23000, "CE")
    assert sym == "NIFTY25APR2423000CE"


def test_parse_openalgo_option_symbol_roundtrip():
    sym = "BANKNIFTY25APR2448000PE"
    parsed = parse_openalgo_option_symbol(sym)
    assert parsed is not None
    assert parsed["base"] == "BANKNIFTY"
    assert parsed["strike"] == 48000
    assert parsed["option_type"] == "PE"


@pytest.mark.skipif(not HF_ROOT.is_dir(), reason="HF replay data not present")
def test_build_symtoken_rows_nifty_only(monkeypatch):
    monkeypatch.setenv("SIM_MC_UNDERLYINGS", "NIFTY")
    monkeypatch.setenv("SIM_MC_MAX_EXPIRIES", "4")
    rows = build_symtoken_rows(data_root=DATA_ROOT, replay_date="2024-04-15")
    assert rows
    keys = {(r["symbol"], r["exchange"]) for r in rows}
    assert ("NIFTY", "NSE_INDEX") in keys
    nfo = [r for r in rows if r["exchange"] == "NFO" and r["symbol"].startswith("NIFTY")]
    assert nfo
    assert any(r["instrumenttype"] == "CE" for r in nfo)


@pytest.mark.skipif(not HF_ROOT.is_dir(), reason="HF replay data not present")
def test_build_symtoken_rows_all_hf_underlyings(monkeypatch):
    monkeypatch.setenv("SIM_MC_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX")
    rows = build_symtoken_rows(data_root=DATA_ROOT, replay_date="2024-04-15")
    keys = {(r["symbol"], r["exchange"]) for r in rows}
    assert ("NIFTY", "NSE_INDEX") in keys
    assert ("BANKNIFTY", "NSE_INDEX") in keys
    assert ("SENSEX", "BSE_INDEX") in keys


def test_openalgo_master_contract_module_importable():
    import subprocess

    proc = subprocess.run(
        [
            "uv",
            "run",
            "python3",
            "-c",
            "from broker.stock_simulator.database.master_contract_db import master_contract_download; print('ok')",
        ],
        cwd=REPO / "openalgo",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


@pytest.mark.skipif(not HF_ROOT.is_dir(), reason="HF replay data not present")
def test_mc_fingerprint_after_download(monkeypatch):
    monkeypatch.setenv("SIM_MC_UNDERLYINGS", "NIFTY")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-15")
    import subprocess

    proc = subprocess.run(
        [
            "uv",
            "run",
            "python3",
            "-c",
            (
                "from broker.stock_simulator.database.master_contract_db import master_contract_download; "
                "from database.master_contract_status_db import get_status; "
                "from utils.auth_utils import should_download_master_contract; "
                "master_contract_download(); "
                "stats=get_status('stock_simulator').get('exchange_stats') or {}; "
                "assert stats.get('replay_date')=='2024-04-15'; "
                "ok, reason = should_download_master_contract('stock_simulator'); "
                "print('should_download', ok, reason)"
            ),
        ],
        cwd=REPO / "openalgo",
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **__import__("os").environ,
            "NSE_REPLAY_DATE": "2024-04-15",
            "NSE_REPLAY_DATA_ROOT": str(DATA_ROOT),
            "STOCK_SIMULATOR_MODE": "replay",
            "SIM_MC_UNDERLYINGS": "NIFTY",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "should_download False" in proc.stdout


@pytest.mark.skipif(not HF_ROOT.is_dir(), reason="HF replay data not present")
def test_replay_option_quote(monkeypatch):
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "replay")
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-15")
    monkeypatch.setenv("NSE_REPLAY_TIME", "10:30")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(DATA_ROOT))
    monkeypatch.setenv("SIM_MC_UNDERLYINGS", "NIFTY")
    rows = build_symtoken_rows(data_root=DATA_ROOT, replay_date="2024-04-15")
    nfo = next(
        r
        for r in rows
        if r["exchange"] == "NFO"
        and r["instrumenttype"] == "CE"
        and r["symbol"] == "NIFTY25APR2422350CE"
    )
    import trade_integrations.stock_simulator.replay as replay_mod

    replay_mod.get_replay_service(reload=True)
    svc = replay_mod.get_replay_service()
    quote = svc.get_quote(nfo["symbol"], "NFO")
    assert quote["simulated"] is True
    assert quote["ltp"] > 0
