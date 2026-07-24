"""Unit tests for per-owner watch baseline helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def test_owner_baseline_key_matches_nautilus_owner():
    from trade_integrations.watch_registry.baselines import owner_baseline_key

    assert owner_baseline_key("aa_test", "nifty") == "aa_test:NIFTY"
    assert owner_baseline_key("ws_sess1", "NIFTY") == "ws_sess1:NIFTY"


def test_seed_symbol_baseline_matches_setdefault_semantics():
    from trade_integrations.watch_registry.baselines import seed_symbol_baseline

    store: dict[str, float] = {}
    assert seed_symbol_baseline(store, "NIFTY", 100.0) == 100.0
    assert seed_symbol_baseline(store, "NIFTY", 200.0) == 100.0
    assert store["NIFTY"] == 100.0


def test_prune_owner_baselines_keeps_active_symbols():
    from trade_integrations.watch_registry.baselines import prune_owner_baselines

    ltp = {"aa_x:NIFTY": 100.0, "aa_x:INDIAVIX": 14.0, "aa_y:NIFTY": 50.0}
    prune_owner_baselines((ltp,), nautilus_owner="aa_x", active_symbols={"NIFTY"})
    assert "aa_x:NIFTY" in ltp
    assert "aa_x:INDIAVIX" not in ltp
    assert "aa_y:NIFTY" in ltp


def test_seed_quote_symbol_baselines_seeds_all_fields():
    from trade_integrations.watch_registry.baselines import seed_quote_symbol_baselines

    ltp: dict[str, float] = {}
    oi: dict[str, float] = {}
    vol: dict[str, float] = {}
    seed_quote_symbol_baselines(
        ltp_baselines=ltp,
        symbol="NIFTY",
        ltp=24_000.0,
        oi=1_000_000.0,
        volume=50_000.0,
        oi_baselines=oi,
        volume_baselines=vol,
    )
    assert ltp["NIFTY"] == 24_000.0
    assert oi["NIFTY"] == 1_000_000.0
    assert vol["NIFTY"] == 50_000.0

