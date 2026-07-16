"""Unit tests for global macro collector (index research)."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_collect_global_factor_rows_returns_expected_keys(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_yfinance_factor",
        lambda factor, symbol: {
            "factor": factor,
            "value": 100.0 if factor == "usd_inr" else 50.0,
            "source": "yfinance",
            "metadata": {"symbol": symbol},
        },
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_us_10y",
        lambda: {"factor": "us_10y", "value": 4.2, "source": "fred_direct"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_india_vix",
        lambda: {"factor": "india_vix", "value": 14.5, "source": "nselib"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_fii_net_5d",
        lambda: {"factor": "fii_net_5d", "value": 1200.0, "source": "nselib"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_nifty_pe",
        lambda: {"factor": "nifty_pe", "value": 22.1, "source": "yfinance"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_rbi_factors",
        lambda: {
            "rows": [
                {"factor": "repo_rate", "value": 6.5, "source": "env_seed"},
                {"factor": "cpi_yoy_proxy", "value": 4.8, "source": "env_seed"},
            ],
            "context": {"repo_rate": 6.5, "cpi_yoy_proxy": 4.8},
        },
    )

    from trade_integrations.dataflows.index_research.macro_global import (
        collect_global_factor_rows,
    )

    rows = collect_global_factor_rows(constituent_sentiments=[0.2, 0.4, 0.6])
    factors = {row["factor"] for row in rows}

    assert "usd_inr" in factors
    assert "oil_brent" in factors
    assert "index_sentiment" in factors
    assert rows[0]["factor"] in factors
    usd_row = next(row for row in rows if row["factor"] == "usd_inr")
    assert usd_row["value"] == 100.0
    assert usd_row["source"] == "yfinance"


@pytest.mark.unit
def test_macro_global_stage_degraded_on_partial_failure(monkeypatch):
    def _yf(factor: str, symbol: str):
        if factor == "oil_brent":
            raise RuntimeError("yfinance timeout")
        return {
            "factor": factor,
            "value": 83.0,
            "source": "yfinance",
            "metadata": {"symbol": symbol},
        }

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_yfinance_factor",
        _yf,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_us_10y",
        lambda: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_india_vix",
        lambda: {"factor": "india_vix", "value": 13.0, "source": "yfinance"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_fii_net_5d",
        lambda: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_nifty_pe",
        lambda: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.macro_global._fetch_rbi_factors",
        lambda: {
            "rows": [{"factor": "repo_rate", "value": 6.5, "source": "env_seed"}],
            "context": {},
        },
    )

    from trade_integrations.dataflows.index_research.macro_global import (
        fetch_global_macro_snapshot,
    )

    stage = fetch_global_macro_snapshot()
    assert stage.stage == "macro_global"
    assert stage.status in ("partial", "ok")
    assert stage.data["factors"]
    assert "usd_inr" in stage.data["factors"]
    assert "oil_brent" not in stage.data["factors"]
    assert "india_vix" in stage.data["factors"]
    assert any("oil_brent" in err for err in stage.errors)


@pytest.mark.unit
def test_rbi_cpi_env_seed_fallback(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.rbi_cpi._scrape_rbi_press_releases",
        lambda: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.rbi_cpi._fetch_inflation_etf_proxy",
        lambda: None,
    )
    monkeypatch.setenv("RBI_REPO_RATE", "6.25")

    from trade_integrations.dataflows.index_research.sources.rbi_cpi import (
        fetch_rbi_cpi_context,
    )

    context = fetch_rbi_cpi_context()
    assert context["repo_rate"] == 6.25
    assert context["source"] == "env_seed"
    assert context["rbi_events"]
