"""Unit tests for the shared context hub."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.context.hub import (
    get_hub_dir,
    is_company_research_eligible,
    is_cache_fresh,
    load_company_research_markdown,
    save_company_research,
)
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc, StageResult


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

    def test_hub_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "custom"))
        assert get_hub_dir() == (tmp_path / "custom").resolve()
