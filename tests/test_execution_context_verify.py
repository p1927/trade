"""Tests for execution context verification."""

from __future__ import annotations

import pytest

from trade_integrations.execution.context_verify import (
    apply_context_verification,
    verify_agent_execution_context,
)
from trade_integrations.openalgo.market_context import MarketContext


def _ctx(*, analyze: bool) -> MarketContext:
    venue = "sandbox" if analyze else "broker"
    authority = "sandbox.db" if analyze else "broker"
    return MarketContext(
        context_generation="2026-07-23T09:15:00+05:30",
        data_broker="zerodha",
        execution_venue=venue,
        analyze_mode=analyze,
        market_region="IN",
        positions_authority=authority,
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("equity",),
    )


@pytest.mark.unit
def test_paper_mandate_live_openalgo_blocked() -> None:
    agent = {"constraints": {"mode": "paper"}}
    result = verify_agent_execution_context(
        agent=agent,
        market_context=_ctx(analyze=False),
        env_paper_lock=False,
        allow_analyzer_sync=False,
    )
    assert result.ok is False
    assert result.action_taken == "blocked"


@pytest.mark.unit
def test_paper_mandate_sync_allowed_under_env_lock() -> None:
    agent = {"constraints": {"mode": "paper"}}
    result = verify_agent_execution_context(
        agent=agent,
        market_context=_ctx(analyze=False),
        env_paper_lock=True,
        allow_analyzer_sync=True,
    )
    assert result.ok is True
    assert result.action_taken == "analyzer_enabled"


@pytest.mark.unit
def test_env_lock_blocks_live_intent() -> None:
    agent = {"constraints": {"mode": "live"}}
    result = verify_agent_execution_context(
        agent=agent,
        market_context=_ctx(analyze=False),
        env_paper_lock=True,
        allow_analyzer_sync=False,
    )
    assert result.ok is False
    assert "env_paper_lock" in result.reason


@pytest.mark.unit
def test_live_intent_analyze_on_warns_but_ok() -> None:
    agent = {"constraints": {"mode": "live"}}
    result = verify_agent_execution_context(
        agent=agent,
        market_context=_ctx(analyze=True),
        env_paper_lock=False,
    )
    assert result.ok is True
    assert "live_intent" in result.reason


@pytest.mark.unit
def test_apply_context_verification_sync_success() -> None:
    verification = verify_agent_execution_context(
        agent={"constraints": {"mode": "paper"}},
        market_context=_ctx(analyze=False),
        env_paper_lock=True,
        allow_analyzer_sync=True,
    )
    applied = apply_context_verification(verification, sync_analyzer=lambda: True)
    assert applied.ok is True
    assert applied.action_taken == "none"


@pytest.mark.unit
def test_apply_context_verification_sync_failure() -> None:
    verification = verify_agent_execution_context(
        agent={"constraints": {"mode": "paper"}},
        market_context=_ctx(analyze=False),
        env_paper_lock=True,
        allow_analyzer_sync=True,
    )
    applied = apply_context_verification(verification, sync_analyzer=lambda: False)
    assert applied.ok is False
    assert applied.reason == "analyzer_sync_failed"
