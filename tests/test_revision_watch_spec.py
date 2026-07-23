"""Tests for revision watch_spec auto-sync."""

from __future__ import annotations

import pytest

from trade_integrations.autonomous_agents.revision_watch_spec import (
    maybe_sync_watch_spec_on_revision,
    revision_needs_watch_update,
    watch_spec_matches_levels,
)


@pytest.mark.unit
def test_watch_spec_matches_levels_stop() -> None:
    spec = {
        "strategy": "buy_dip",
        "rules": [{"metric": "level_below", "threshold": 100.0, "label": "stop"}],
    }
    assert watch_spec_matches_levels(spec, strategy="buy_dip", stop=100.0)
    assert not watch_spec_matches_levels(spec, strategy="buy_dip", stop=99.0)


@pytest.mark.unit
def test_watch_spec_matches_hold_cash_target() -> None:
    from trade_integrations.autonomous_agents.strategy_watch_spec import build_watch_spec_for_strategy
    from trade_integrations.auto_paper.mandate_config import MandateConfig

    spec = build_watch_spec_for_strategy(
        strategy="hold_cash",
        mandate=MandateConfig(allowed_instruments=["equity"]),
        symbols=["NIFTY"],
        target=1200.0,
    )
    assert watch_spec_matches_levels(spec, strategy="hold_cash", target=1200.0)


@pytest.mark.unit
def test_revision_needs_watch_update_on_stop_change() -> None:
    agent = {
        "id": "aa_rev1",
        "symbols": ["NIFTY"],
        "watch_spec": {
            "strategy": "buy_dip",
            "rules": [{"metric": "level_below", "threshold": 100.0, "label": "stop"}],
        },
        "mandate_config": {},
    }
    assert revision_needs_watch_update(
        agent=agent,
        decision="REVISE",
        strategy="buy_dip",
        stop=95.0,
    )


@pytest.mark.unit
def test_maybe_sync_watch_spec_on_revision_updates_agent(monkeypatch) -> None:
    agent = {
        "id": "aa_rev2",
        "symbols": ["NIFTY"],
        "mandate_config": {"allowed_instruments": ["equity"]},
        "watch_spec": {"strategy": "buy_dip", "rules": []},
    }

    class _Profile:
        uses_nautilus_watch = False
        uses_nautilus_handoff = False

    monkeypatch.setattr(
        "trade_integrations.execution.profile.resolve_profile",
        lambda **kwargs: _Profile(),
    )
    result = maybe_sync_watch_spec_on_revision(
        agent,
        decision="REVISE",
        strategy="buy_dip",
        stop=95.0,
    )
    assert result["status"] == "ok"
    assert result["watch_spec_updated"] is True
    assert agent.get("watch_spec_updated_at")
    assert agent["watch_spec"]["strategy"] == "buy_dip"
