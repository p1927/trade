"""Tests for unified Nifty 50 batch fetch policy."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.fetch_policy import (
    allow_tiered_apis,
    is_nifty50_batch,
    news_sources_for_batch,
    set_nifty50_batch,
    tiered_source_allowed,
)
from trade_integrations.dataflows.news_aggregator.config import get_aggregator_sources


@pytest.mark.unit
def test_nifty50_batch_disables_tiered_apis():
    assert allow_tiered_apis() is True
    assert tiered_source_allowed("tapetide") is True
    assert tiered_source_allowed("alpha_vantage") is True

    set_nifty50_batch(True)
    try:
        assert is_nifty50_batch() is True
        assert allow_tiered_apis() is False
        assert tiered_source_allowed("tapetide") is False
        assert tiered_source_allowed("alpha_vantage") is False
        assert tiered_source_allowed("searxng") is True
        assert news_sources_for_batch() == ["searxng"]
        assert get_aggregator_sources() == ["searxng"]
    finally:
        set_nifty50_batch(False)

    assert allow_tiered_apis() is True


@pytest.mark.unit
def test_identity_in_skips_tapetide_during_batch(monkeypatch):
    from trade_integrations.dataflows.company_research.market import normalize_ticker
    from trade_integrations.dataflows.company_research.sources.identity_in import fetch_identity_in

    monkeypatch.setattr(
        "trade_integrations.clients.tapetide.is_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.company_research.sources.identity_in._fetch_yfinance",
        lambda _n: {"name": "Reliance", "sector": "Energy", "source": "yfinance"},
    )

    set_nifty50_batch(True)
    try:
        result = fetch_identity_in(normalize_ticker("RELIANCE"))
        names = [a.get("name") for a in (result.data.get("source_attempts") or [])]
        assert "tapetide" not in names
    finally:
        set_nifty50_batch(False)
