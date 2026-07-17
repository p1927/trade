"""Tests for trade_integrations.openalgo.market_data."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.errors import NoMarketDataError


def test_normalize_option_chain_adds_pcr():
    from trade_integrations.openalgo.market_data import normalize_option_chain_response

    raw = {"chain": [{"strike": 100, "ce": {"oi": 100}, "pe": {"oi": 200}}]}
    out = normalize_option_chain_response(raw, "NIFTY", "16JUL26")
    assert out["pcr"] == 2.0
    assert out["source"] == "openalgo"


def test_fetch_option_chain_with_fallback_uses_nselib(monkeypatch):
    from trade_integrations.openalgo import market_data

    def fail_openalgo(*_args, **_kwargs):
        raise NoMarketDataError("NIFTY", "NIFTY", "OpenAlgo unavailable")

    monkeypatch.setattr(market_data, "fetch_option_chain_raw", fail_openalgo)
    monkeypatch.setattr(
        market_data,
        "_fetch_nselib_chain",
        lambda _underlying, _expiry, *, is_index=True: {
            "underlying": "NIFTY",
            "chain": [{"strike": 24500, "ce": {"oi": 1}, "pe": {"oi": 2}}],
            "source": "nselib",
        },
    )

    out = market_data.fetch_option_chain_with_fallback(
        "NIFTY",
        "NFO",
        expiry_date="16JUL26",
        is_index=True,
    )
    assert out["source"] == "nselib"
    assert len(out["chain"]) == 1


def test_fetch_option_chain_with_fallback_prefers_openalgo(monkeypatch):
    from trade_integrations.openalgo import market_data

    openalgo_chain = {
        "underlying": "NIFTY",
        "chain": [{"strike": 24500, "ce": {"oi": 10}, "pe": {"oi": 20}}],
        "source": "openalgo",
    }
    monkeypatch.setattr(market_data, "fetch_option_chain_raw", lambda *_a, **_k: openalgo_chain)

    def nselib_should_not_run(*_args, **_kwargs):
        raise AssertionError("nselib fallback should not run when OpenAlgo succeeds")

    monkeypatch.setattr(market_data, "_fetch_nselib_chain", nselib_should_not_run)

    out = market_data.fetch_option_chain_with_fallback("NIFTY", "NFO", expiry_date="16JUL26")
    assert out["source"] == "openalgo"


@pytest.mark.unit
def test_resolve_openalgo_symbol_from_symbols_module():
    from trade_integrations.openalgo.symbols import resolve_openalgo_symbol

    assert resolve_openalgo_symbol("RELIANCE.NS") == ("RELIANCE", "NSE")
    assert resolve_openalgo_symbol("^NSEI") == ("NIFTY", "NSE_INDEX")
