"""Tests for agent execution status and OpenAlgo MarketContext passthrough."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_integrations.autonomous_agents.agent_status import (
    OpenAlgoAuthority,
    build_execution_context_summary,
    get_agent_execution_status,
    load_openalgo_authority,
)
from trade_integrations.autonomous_agents.runtime_status import build_agent_runtime, build_stack_health
from trade_integrations.openalgo.market_context import MarketContext


def _sample_market_context() -> MarketContext:
    return MarketContext(
        context_generation="2026-07-23T10:00:00+05:30",
        data_broker="zerodha",
        execution_venue="sandbox",
        analyze_mode=True,
        market_region="IN",
        positions_authority="sandbox.db",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("options", "equity"),
    )


@pytest.mark.unit
def test_market_context_to_execution_context_summary() -> None:
    summary = _sample_market_context().to_execution_context_summary(profile_id="openalgo-paper-sdk")
    assert summary["broker"] == "zerodha"
    assert summary["venue"] == "sandbox"
    assert summary["market_region"] == "IN"
    assert summary["paper"] is True
    assert summary["analyze_mode"] is True
    assert summary["profile_id"] == "openalgo-paper-sdk"


@pytest.mark.unit
def test_build_execution_context_summary_uses_openalgo_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.agent_status.load_openalgo_authority",
        lambda **kwargs: OpenAlgoAuthority(
            market_context=_sample_market_context(),
            execution_context=_sample_market_context().to_execution_context_summary(
                profile_id="openalgo-paper-sdk"
            ),
            funds=None,
        ),
    )
    summary = build_execution_context_summary(agent={"connector_profile_id": "openalgo-paper-sdk"})
    assert summary is not None
    assert summary["broker"] == "zerodha"


@pytest.mark.unit
def test_load_openalgo_authority_single_market_context_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_market_context.return_value = _sample_market_context()
    client.get_funds.return_value = {"availablecash": 100000}
    monkeypatch.setattr(
        "trade_integrations.execution.openalgo_client.OpenAlgoClient",
        lambda: client,
    )
    authority = load_openalgo_authority(agent={"connector_profile_id": "openalgo-paper-sdk"})
    assert authority.execution_context is not None
    client.get_market_context.assert_called_once()
    client.analyzer_status.assert_not_called()


@pytest.mark.unit
def test_get_agent_execution_status_from_market_context(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = OpenAlgoAuthority(
        market_context=_sample_market_context(),
        execution_context=_sample_market_context().to_execution_context_summary(
            profile_id="openalgo-paper-sdk"
        ),
        funds={"availablecash": 100000},
    )
    monkeypatch.setattr(
        "trade_integrations.monitor.execution_ledger.list_open_entries_live",
        lambda: [],
    )
    from trade_integrations.autonomous_agents.reconcile import PaperReconcileReport

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.reconcile.reconcile_paper_state",
        lambda: PaperReconcileReport(),
    )

    status = get_agent_execution_status(
        agent={
            "id": "aa_test",
            "status": "running",
            "symbols": ["NIFTY"],
            "connector_profile_id": "openalgo-paper-sdk",
            "mandate_config": {"market": "IN", "allowed_instruments": ["options"]},
        },
        authority=authority,
    )
    assert status["execution_context"]["broker"] == "zerodha"
    assert status["analyze_mode"] is True


@pytest.mark.unit
def test_build_agent_runtime_passthrough_execution_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = _sample_market_context().to_execution_context_summary()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.agent_status.load_openalgo_authority",
        lambda **kwargs: OpenAlgoAuthority(
            market_context=_sample_market_context(),
            execution_context=sample,
            funds=None,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.agent_status.get_agent_execution_status",
        lambda **kwargs: {
            "session": {"autonomous_agent_id": "aa_rt_ctx", "enabled": True},
            "open_positions": 0,
            "market_open": True,
            "execution_context": sample,
            "analyze_mode": True,
        },
    )
    monkeypatch.setattr(
        "trade_integrations.execution.profile.resolve_profile_from_context",
        lambda **kwargs: MagicMock(uses_nautilus_watch=False, uses_nautilus_handoff=False),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
        lambda: False,
    )

    runtime = build_agent_runtime(
        {
            "id": "aa_rt_ctx",
            "status": "running",
            "symbols": ["NIFTY"],
            "thesis": {"strategy": "iron condor"},
            "watch_spec_updated_at": "2026-07-23T10:00:00+00:00",
        }
    )
    assert runtime["execution_context"]["broker"] == "zerodha"
    assert runtime["watch_strategy"] == "iron condor"
    assert runtime["watch_spec_updated_at"] == "2026-07-23T10:00:00+00:00"


@pytest.mark.unit
def test_build_stack_health_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._paper_runtime",
        lambda agent, authority=None: {"session": {}, "scheduler_health": "disabled", "market_open": False},
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.store.list_agents",
        lambda: [],
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.agent_status.load_openalgo_authority",
        lambda **kwargs: type("A", (), {"market_context": None, "execution_context": None, "funds": None})(),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
        lambda: False,
    )
    health = build_stack_health()
    assert "scheduler_health" in health
