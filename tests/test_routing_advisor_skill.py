"""Tests for entity-based advisor skill routing."""

from __future__ import annotations

import pytest

from trade_integrations.execution.routing_context import (
    advisor_skill_id_for_routing,
    debate_asset_type_for_agent,
    format_advisor_skill_block,
    india_debate_eligible_for_agent,
    research_kinds_for_agent,
    resolve_agent_routing,
)
from trade_integrations.research.registry import ResearchKind


def _agent(**overrides) -> dict:
    base = {
        "id": "aa_test",
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate": "paper trade",
        "mandate_config": {"allowed_instruments": ["options"]},
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestAdvisorSkillRouting:
    def test_nifty_options_agent_uses_index_advisor(self):
        routing = resolve_agent_routing(_agent())
        assert routing.research_asset_type == "index"
        assert advisor_skill_id_for_routing(routing) == "index-advisor"
        block = format_advisor_skill_block(routing, turn_kind="research")
        assert 'load_skill("index-advisor")' in block
        assert "options-advisor" in block

    def test_equity_agent_uses_stock_advisor(self):
        routing = resolve_agent_routing(
            _agent(
                symbols=["RELIANCE"],
                mandate_config={"allowed_instruments": ["equity"]},
                mandate="swing equity",
            )
        )
        assert routing.research_asset_type == "stock"
        assert advisor_skill_id_for_routing(routing) == "stock-advisor"
        block = format_advisor_skill_block(routing, turn_kind="bootstrap")
        assert 'load_skill("stock-advisor")' in block

    def test_index_symbol_equity_mandate_uses_index_advisor(self):
        routing = resolve_agent_routing(
            _agent(
                symbols=["NIFTY"],
                mandate_config={"allowed_instruments": ["equity"]},
                mandate="index directional equity view",
            )
        )
        assert routing.research_asset_type == "index"
        assert advisor_skill_id_for_routing(routing) == "index-advisor"

    def test_non_index_options_agent_uses_options_advisor(self):
        routing = resolve_agent_routing(
            _agent(
                symbols=["RELIANCE"],
                mandate_config={"allowed_instruments": ["options"]},
                mandate="options on reliance",
            )
        )
        assert routing.research_asset_type == "options"
        assert advisor_skill_id_for_routing(routing) == "options-advisor"

    def test_us_agent_has_no_advisor_skill(self):
        routing = resolve_agent_routing(
            _agent(symbols=["SPY"], execution_market="US", mandate_config={"allowed_instruments": ["equity"]})
        )
        assert advisor_skill_id_for_routing(routing) is None
        assert format_advisor_skill_block(routing, turn_kind="research") == ""

    def test_research_kinds_include_index_overlay_for_nifty(self):
        kinds = research_kinds_for_agent(_agent())
        assert ResearchKind.INDEX in kinds
        assert ResearchKind.OPTIONS in kinds

    def test_research_kinds_equity_reliance_is_stock_only(self):
        kinds = research_kinds_for_agent(
            _agent(
                symbols=["RELIANCE"],
                mandate_config={"allowed_instruments": ["equity"]},
            )
        )
        assert kinds == (ResearchKind.STOCK,)

    def test_research_kinds_index_equity_nifty_is_index_only(self):
        kinds = research_kinds_for_agent(
            _agent(
                symbols=["NIFTY"],
                mandate_config={"allowed_instruments": ["equity"]},
            )
        )
        assert kinds == (ResearchKind.INDEX,)

    def test_debate_asset_type_nifty_options_is_options(self):
        routing = resolve_agent_routing(_agent())
        assert debate_asset_type_for_agent(_agent()) == "options"

    def test_debate_asset_type_reliance_equity_is_stock(self):
        agent = _agent(
            symbols=["RELIANCE"],
            mandate_config={"allowed_instruments": ["equity"]},
        )
        assert debate_asset_type_for_agent(agent) == "stock"

    def test_us_agent_skips_india_debate(self):
        agent = _agent(symbols=["SPY"], execution_market="US")
        eligible, reason = india_debate_eligible_for_agent(agent, "SPY")
        assert eligible is False
        assert reason == "us_agent"
