"""Unit tests for options instrument routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_integrations.dataflows.company_research.market import Market
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.dataflows.options_research.market import (
    InstrumentType,
    is_options_research_eligible,
    options_research_ineligible_reason,
    resolve_options_instrument,
)


@pytest.mark.unit
class TestOptionsMarket:
    def test_nifty_index_routing(self):
        inst = resolve_options_instrument("NIFTY")
        assert inst.instrument_type == InstrumentType.INDEX
        assert inst.underlying_exchange == "NSE_INDEX"
        assert inst.options_exchange == "NFO"
        assert inst.display_symbol == "NIFTY"

    def test_reliance_stock_routing(self):
        inst = resolve_options_instrument("RELIANCE")
        assert inst.instrument_type == InstrumentType.STOCK
        assert inst.options_exchange == "NFO"
        assert inst.underlying_exchange == "NSE"
        assert inst.display_symbol == "RELIANCE"

    def test_nvda_us_routing(self):
        inst = resolve_options_instrument("NVDA")
        assert inst.market == Market.US
        assert inst.underlying_exchange == "US"
        assert inst.options_exchange == "US"

    def test_eligibility(self):
        assert is_options_research_eligible("NIFTY") is True
        assert is_options_research_eligible("RELIANCE") is True
        assert is_options_research_eligible("RELIANCE.NS") is True
        assert is_options_research_eligible("NVDA") is False
        assert is_options_research_eligible("") is False

    def test_ineligible_reason_nvda(self):
        assert options_research_ineligible_reason("NVDA") == "us_market"

    def test_run_options_research_skips_nvda_without_chain(self):
        with patch(
            "trade_integrations.dataflows.options_research.sources.chain_openalgo.fetch_chain_stage"
        ) as fetch_chain:
            doc = run_options_research("NVDA")
        fetch_chain.assert_not_called()
        assert doc.meta.get("skip_reason") == "us_market"
        assert doc.stages[0].status == "skipped"
