"""Unit tests for the shared context hub."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.context.hub import (
    get_hub_dir,
    is_company_research_eligible,
    is_cache_fresh,
    is_stock_cache_fresh,
    load_company_research_markdown,
    save_company_research,
    save_stock_research,
)
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc, StageResult
from trade_integrations.dataflows.stock_research.models import StockResearchDoc


@pytest.mark.unit
class TestCompanyResearchEligibility:
    def test_stock_symbol(self):
        assert is_company_research_eligible("RELIANCE") is True

    def test_index_excluded(self):
        assert is_company_research_eligible("^NSEI") is False
        assert is_company_research_eligible("NIFTY") is False

    def test_crypto_excluded(self):
        assert is_company_research_eligible("BTC-USD", asset_type="crypto") is False


@pytest.mark.unit
class TestContextHubPersistence:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
        now = datetime.now(timezone.utc)
        doc = CompanyResearchDoc(
            ticker="RELIANCE",
            as_of=now,
            lookahead_days=14,
            market="IN",
            identity={"name": "Reliance Industries"},
            stages=[
                StageResult(
                    stage="market",
                    status="ok",
                    vendor="test",
                    fetched_at=now,
                    data={"market": "IN"},
                )
            ],
        )
        save_company_research(doc)
        loaded = load_company_research_markdown("RELIANCE")
        assert loaded is not None
        assert "Reliance Industries" in loaded
        assert is_cache_fresh("RELIANCE") is True
        history_dir = tmp_path / "RELIANCE" / "company_research" / "history"
        assert history_dir.is_dir()
        assert len(list(history_dir.glob("*.json"))) >= 1

    def test_hub_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "custom"))
        assert get_hub_dir() == (tmp_path / "custom").resolve()

    def test_stock_cache_fresh_uses_stock_research_path(self, tmp_path, monkeypatch):
        """Regression: is_stock_cache_fresh must not read company_research/latest.json."""
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
        now = datetime.now(timezone.utc)
        company = CompanyResearchDoc(
            ticker="RELIANCE",
            as_of=now,
            lookahead_days=14,
            market="IN",
            stages=[],
        )
        stock = StockResearchDoc(
            ticker="RELIANCE",
            as_of=now,
            lookahead_days=14,
            market="IN",
            stages=[],
        )
        save_company_research(company)
        assert is_stock_cache_fresh("RELIANCE") is False
        save_stock_research(stock)
        assert is_stock_cache_fresh("RELIANCE") is True
