"""Tests for equity-first allowed_instruments resolution."""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestInstrumentResolve:
    def test_reliance_paper_trade_defaults_equity(self):
        from trade_integrations.autonomous_agents.mandate_config import resolve_allowed_instruments

        result = resolve_allowed_instruments(
            ["RELIANCE"],
            "Paper trade Reliance intraday ₹50k",
            execution_market="IN",
        )
        assert result == ["equity"]

    def test_reliance_iron_condor_defaults_options(self):
        from trade_integrations.autonomous_agents.mandate_config import resolve_allowed_instruments

        result = resolve_allowed_instruments(
            ["RELIANCE"],
            "Iron condor on Reliance",
            execution_market="IN",
        )
        assert result == ["options"]

    def test_nifty_plain_is_ambiguous(self):
        from trade_integrations.autonomous_agents.mandate_config import resolve_allowed_instruments

        result = resolve_allowed_instruments(
            ["NIFTY"],
            "Paper trade NIFTY",
            execution_market="IN",
        )
        assert result is None

    def test_nifty_intraday_defaults_options(self):
        from trade_integrations.autonomous_agents.mandate_config import resolve_allowed_instruments

        result = resolve_allowed_instruments(
            ["NIFTY"],
            "Paper trade NIFTY intraday",
            execution_market="IN",
        )
        assert result == ["options"]

    def test_explicit_allowed_instruments(self):
        from trade_integrations.autonomous_agents.mandate_config import resolve_allowed_instruments

        result = resolve_allowed_instruments(
            ["RELIANCE"],
            "Paper trade",
            execution_market="IN",
            explicit=["options"],
        )
        assert result == ["options"]
