"""Unit tests for options research hub persistence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.context.hub import (
    is_options_cache_fresh,
    load_options_research_json,
    load_options_research_markdown,
    save_options_research,
)
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.options_research.models import OptionsResearchDoc


@pytest.mark.unit
class TestOptionsHubPersistence:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
        now = datetime.now(timezone.utc)
        doc = OptionsResearchDoc(
            underlying="NIFTY",
            as_of=now,
            lookahead_days=14,
            instrument_type="index",
            market="IN",
            expiry="30JUL25",
            spot=24500.0,
            prediction={"view": "neutral", "iv_regime": "moderate"},
            recommended={"name": "iron_condor", "score": 0.72},
            stages=[
                StageResult(
                    stage="market",
                    status="ok",
                    vendor="test",
                    fetched_at=now,
                    data={},
                )
            ],
        )
        save_options_research(doc)
        loaded_md = load_options_research_markdown("NIFTY")
        loaded_json = load_options_research_json("NIFTY")
        assert loaded_md is not None
        assert "NIFTY" in loaded_md
        assert loaded_json is not None
        assert loaded_json.underlying == "NIFTY"
        assert is_options_cache_fresh("NIFTY") is True
