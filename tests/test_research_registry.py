"""Tests for research contract registry."""

from __future__ import annotations

import pytest

from trade_integrations.research.registry import (
    ResearchKind,
    get_contract,
    eligible_kinds_for_ticker,
    resolve_kind_for_ticker,
)


@pytest.mark.unit
class TestResearchRegistry:
    def test_options_contract_stages(self):
        c = get_contract(ResearchKind.OPTIONS)
        assert c.hub_subdir == "options_research"
        assert c.widget_intent == "options_strategy"
        stage_ids = [s.id for s in c.stages]
        assert "options_research" in stage_ids
        assert "live_quote" in stage_ids

    def test_stock_contract_requires_debate(self):
        c = get_contract(ResearchKind.STOCK)
        debate = next(s for s in c.stages if s.id == "agent_debate")
        assert debate.required is True
        assert "prediction.provenance" in c.required_widget_fields

    def test_index_contract(self):
        c = get_contract(ResearchKind.INDEX)
        assert c.hub_subdir == "index_research"
        assert c.widget_intent == "index_outlook"

    def test_resolve_reliance_stock(self):
        kind = resolve_kind_for_ticker("RELIANCE", prefer=ResearchKind.STOCK)
        assert kind == ResearchKind.STOCK

    def test_resolve_nifty_prefers_options_by_default(self):
        kinds = eligible_kinds_for_ticker("NIFTY")
        assert ResearchKind.INDEX in kinds
        assert ResearchKind.OPTIONS in kinds
        assert resolve_kind_for_ticker("NIFTY", prefer=ResearchKind.INDEX) == ResearchKind.INDEX
        assert resolve_kind_for_ticker("NIFTY", prefer=ResearchKind.OPTIONS) == ResearchKind.OPTIONS

    def test_empty_ticker_none(self):
        assert resolve_kind_for_ticker("") is None
        assert eligible_kinds_for_ticker("") == ()
