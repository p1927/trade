"""Unit tests for options instrument routing."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.options_research.market import (
    InstrumentType,
    is_options_research_eligible,
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
        assert inst.display_symbol == "RELIANCE"

    def test_eligibility(self):
        assert is_options_research_eligible("NIFTY") is True
        assert is_options_research_eligible("RELIANCE") is True
        assert is_options_research_eligible("RELIANCE.NS") is True
        assert is_options_research_eligible("") is False
