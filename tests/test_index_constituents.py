"""Unit tests for Nifty 50 constituent loader."""

from __future__ import annotations

import json

import pandas as pd
import pytest


def _mock_nselib_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": "RELIANCE",
                "Company Name": "Reliance Industries Ltd.",
                "Industry": "Oil Gas & Consumable Fuels",
            },
            {
                "Symbol": "TCS",
                "Company Name": "Tata Consultancy Services Ltd.",
                "Industry": "Information Technology",
            },
            {
                "Symbol": "HDFCBANK",
                "Company Name": "HDFC Bank Ltd.",
                "Industry": "Financial Services",
            },
        ]
    )


@pytest.mark.unit
def test_load_from_cached_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    weights_dir = tmp_path / "_data" / "index_factors" / "weights"
    weights_dir.mkdir(parents=True)
    cache_path = weights_dir / "latest.json"
    cache_path.write_text(
        json.dumps(
            {
                "as_of": "2026-07-16T10:00:00+00:00",
                "source": "nse",
                "weights": {
                    "RELIANCE": 0.5,
                    "TCS": 0.3,
                    "HDFCBANK": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_nifty50_equity_list():
        return _mock_nselib_frame()

    class FakeCapitalMarket:
        nifty50_equity_list = staticmethod(fake_nifty50_equity_list)

    class FakeNselib:
        capital_market = FakeCapitalMarket()

    monkeypatch.setitem(
        __import__("sys").modules,
        "nselib",
        FakeNselib(),
    )

    from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

    rows = load_nifty50_constituents()
    by_symbol = {row.symbol: row for row in rows}

    assert len(rows) == 3
    assert by_symbol["RELIANCE"].name == "Reliance Industries Ltd."
    assert by_symbol["RELIANCE"].sector == "Oil Gas & Consumable Fuels"
    assert by_symbol["RELIANCE"].weight == pytest.approx(0.5)
    assert by_symbol["TCS"].weight == pytest.approx(0.3)
    assert by_symbol["HDFCBANK"].weight == pytest.approx(0.2)
    assert sum(row.weight for row in rows) == pytest.approx(1.0)


@pytest.mark.unit
def test_yfinance_fallback_normalizes(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    def fake_nifty50_equity_list():
        return _mock_nselib_frame()

    class FakeCapitalMarket:
        nifty50_equity_list = staticmethod(fake_nifty50_equity_list)

    class FakeNselib:
        capital_market = FakeCapitalMarket()

    monkeypatch.setitem(__import__("sys").modules, "nselib", FakeNselib())
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.constituents.fetch_nifty50_weights",
        lambda: None,
    )

    def fake_yfinance_weights(symbols: list[str]) -> dict[str, float]:
        assert symbols == ["RELIANCE", "TCS", "HDFCBANK"]
        return {"RELIANCE": 100.0, "TCS": 200.0, "HDFCBANK": 100.0}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.constituents.fetch_yfinance_mcap_weights",
        fake_yfinance_weights,
    )

    from trade_integrations.dataflows.index_research.constituents import (
        get_weights_cache_path,
        load_nifty50_constituents,
    )

    rows = load_nifty50_constituents(force_refresh=True)
    total = sum(row.weight for row in rows)

    assert len(rows) == 3
    assert total == pytest.approx(1.0)
    assert rows[0].weight == pytest.approx(0.25)
    assert rows[1].weight == pytest.approx(0.5)
    assert rows[2].weight == pytest.approx(0.25)

    cache_path = get_weights_cache_path()
    assert cache_path.is_file()
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["source"] == "yfinance_mcap"
    assert sum(cached["weights"].values()) == pytest.approx(1.0)


@pytest.mark.unit
def test_load_from_local_json_when_nselib_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    class FakeCapitalMarket:
        @staticmethod
        def nifty50_equity_list():
            raise FileNotFoundError("No data equity list available")

    class FakeNselib:
        capital_market = FakeCapitalMarket()

    monkeypatch.setitem(__import__("sys").modules, "nselib", FakeNselib())
    monkeypatch.setattr(
        "trade_integrations.nse_browser.repository.repo_root",
        lambda: tmp_path,
    )

    from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

    local_json = tmp_path / "historic_data" / "ind_nifty50_constituents_current.json"
    local_json.parent.mkdir(parents=True)
    local_json.write_text(
        json.dumps(
            {
                "status": "ok",
                "symbols": ["RELIANCE", "TCS", "HDFCBANK"],
                "count": 3,
            }
        ),
        encoding="utf-8",
    )

    rows = load_nifty50_constituents()
    assert len(rows) == 3
    assert {row.symbol for row in rows} == {"RELIANCE", "TCS", "HDFCBANK"}
    assert sum(row.weight for row in rows) == pytest.approx(1.0)


@pytest.mark.unit
def test_constituent_row_has_name_sector_weight():
    from trade_integrations.dataflows.index_research.models import ConstituentRow

    row = ConstituentRow(
        symbol="INFY",
        name="Infosys Ltd.",
        sector="Information Technology",
        weight=0.032,
    )

    assert row.symbol == "INFY"
    assert row.name == "Infosys Ltd."
    assert row.sector == "Information Technology"
    assert row.weight == pytest.approx(0.032)
