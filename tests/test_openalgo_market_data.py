"""Tests for trade_integrations.openalgo.market_data."""

from __future__ import annotations

import pytest

from typing import Any

from trade_integrations.dataflows.errors import NoMarketDataError


def test_normalize_option_chain_adds_pcr():
    from trade_integrations.openalgo.market_data import normalize_option_chain_response

    raw = {"chain": [{"strike": 100, "ce": {"oi": 100}, "pe": {"oi": 200}}]}
    out = normalize_option_chain_response(raw, "NIFTY", "16JUL26")
    assert out["pcr"] == 2.0
    assert out["source"] == "openalgo"


def test_to_nselib_expiry_converts_openalgo_formats():
    from trade_integrations.openalgo.market_data import _to_nselib_expiry

    assert _to_nselib_expiry("28-JUL-26") == "28-07-2026"
    assert _to_nselib_expiry("16JUL26") == "16-07-2026"
    assert _to_nselib_expiry("28-07-2026") == "28-07-2026"
    assert _to_nselib_expiry("bad-expiry") is None


def test_fetch_nselib_chain_uses_numeric_expiry(monkeypatch):
    import sys

    from trade_integrations.openalgo import market_data

    captured: dict[str, Any] = {}

    def fake_nse_live_option_chain(**kwargs):
        captured.update(kwargs)

        class _Frame:
            empty = True

        return _Frame()

    fake_derivatives = type(
        "Derivatives",
        (),
        {"nse_live_option_chain": staticmethod(fake_nse_live_option_chain)},
    )()
    monkeypatch.setitem(sys.modules, "nselib", type("Nselib", (), {"derivatives": fake_derivatives})())

    market_data._fetch_nselib_chain("BANKNIFTY", "28-JUL-26", is_index=True)
    assert captured["expiry_date"] == "28-07-2026"


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
            "chain": [
                {"strike": 24500, "ce": {"oi": 1}, "pe": {"oi": 2}},
                {"strike": 24550, "ce": {"oi": 3}, "pe": {"oi": 4}},
                {"strike": 24600, "ce": {"oi": 5}, "pe": {"oi": 6}},
            ],
            "total_call_oi": 9,
            "total_put_oi": 12,
            "pcr": 1.3333,
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
    assert len(out["chain"]) == 3


def test_fetch_option_chain_with_fallback_prefers_openalgo(monkeypatch):
    from trade_integrations.openalgo import market_data

    openalgo_chain = {
        "underlying": "NIFTY",
        "chain": [
            {"strike": 24500, "ce": {"oi": 10}, "pe": {"oi": 20}},
            {"strike": 24550, "ce": {"oi": 11}, "pe": {"oi": 21}},
            {"strike": 24600, "ce": {"oi": 12}, "pe": {"oi": 22}},
        ],
        "total_call_oi": 33,
        "total_put_oi": 63,
        "pcr": 1.9091,
        "source": "openalgo",
    }
    monkeypatch.setattr(market_data, "fetch_option_chain_raw", lambda *_a, **_k: openalgo_chain)
    monkeypatch.setattr(
        market_data,
        "resolve_default_option_expiry",
        lambda *_a, **_k: "21-JUL-26",
    )

    def nselib_should_not_run(*_args, **_kwargs):
        raise AssertionError("nselib fallback should not run when OpenAlgo succeeds")

    monkeypatch.setattr(market_data, "_fetch_nselib_chain", nselib_should_not_run)

    out = market_data.fetch_option_chain_with_fallback("NIFTY", "NFO", expiry_date="16JUL26")
    assert out["source"] == "openalgo"


def test_fetch_option_chain_raw_resolves_expiry(monkeypatch):
    from trade_integrations.openalgo import market_data

    captured: dict[str, Any] = {}

    def fake_post(endpoint, payload):
        captured["body"] = payload
        return {
            "status": "success",
            "chain": [
                {"strike": 24500, "ce": {"oi": 100}, "pe": {"oi": 200}},
                {"strike": 24550, "ce": {"oi": 110}, "pe": {"oi": 210}},
                {"strike": 24600, "ce": {"oi": 120}, "pe": {"oi": 220}},
            ],
        }

    monkeypatch.setattr(market_data, "openalgo_post", fake_post)
    monkeypatch.setattr(
        market_data,
        "resolve_default_option_expiry",
        lambda *_a, **_k: "21-JUL-26",
    )

    out = market_data.fetch_option_chain_raw("NIFTY", "NFO", strike_count=10)
    assert captured["body"]["expiry_date"] == "21JUL26"
    assert "strike_count" not in captured["body"] or len(out["chain"]) >= 3


def test_chain_is_usable_rejects_pe_only_leg():
    from trade_integrations.openalgo.market_data import chain_is_usable

    sparse = {
        "chain": [{"strike": 25050, "pe": {"oi": 2015}}],
        "total_call_oi": 0,
        "total_put_oi": 2015,
        "pcr": None,
    }
    assert chain_is_usable(sparse) is False


@pytest.mark.unit
def test_resolve_openalgo_symbol_from_symbols_module():
    from trade_integrations.openalgo.symbols import resolve_openalgo_symbol

    assert resolve_openalgo_symbol("RELIANCE.NS") == ("RELIANCE", "NSE")
    assert resolve_openalgo_symbol("^NSEI") == ("NIFTY", "NSE_INDEX")
    assert resolve_openalgo_symbol("AAPL") == ("AAPL", "NASDAQ")
    assert resolve_openalgo_symbol("SPY") == ("SPY", "NASDAQ")
