"""Tests for thesis-break agent resolution and dispatch routing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.thesis_break import resolve_running_agent_for_symbol  # noqa: E402
from trade_integrations.autonomous_agents.store import save_agent  # noqa: E402
from trade_integrations.monitor.execution_ledger import record_execution, save_ledger  # noqa: E402


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _running_agent(
    agent_id: str,
    *,
    symbol: str = "NIFTY",
    widget_id: str | None = None,
    last_full_reasoning_at: str = "2026-07-23T10:00:00+00:00",
) -> dict:
    agent = {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": agent_id,
        "status": "running",
        "symbols": [symbol],
        "execution_market": "IN",
        "vibe_session_id": f"sess_{agent_id}",
        "last_full_reasoning_at": last_full_reasoning_at,
    }
    if widget_id:
        agent["active_trade_plan_widget_id"] = widget_id
        agent["watch_spec"] = {"widget_id": widget_id}
    return agent


def test_resolve_agent_from_ledger_agent_id(agents_hub) -> None:
    save_ledger(
        [
            {
                "execution_id": "ex_nifty_1",
                "widget_id": "tp_old",
                "underlying": "NIFTY",
                "status": "open",
                "agent_id": "aa_ledger",
            }
        ]
    )
    save_agent(_running_agent("aa_other", symbol="NIFTY", last_full_reasoning_at="2026-07-23T12:00:00+00:00"))
    save_agent(_running_agent("aa_ledger", symbol="NIFTY", last_full_reasoning_at="2026-07-23T09:00:00+00:00"))

    resolved = resolve_running_agent_for_symbol("NIFTY", widget_id="tp_old")
    assert resolved == "aa_ledger"


def test_resolve_agent_prefers_new_widget_id_match(agents_hub) -> None:
    save_agent(
        _running_agent(
            "aa_new",
            widget_id="tp_new",
            last_full_reasoning_at="2026-07-23T11:00:00+00:00",
        )
    )
    save_agent(
        _running_agent(
            "aa_old",
            widget_id="tp_old",
            last_full_reasoning_at="2026-07-23T12:00:00+00:00",
        )
    )

    resolved = resolve_running_agent_for_symbol("NIFTY", widget_id="tp_new")
    assert resolved == "aa_new"


def test_resolve_agent_tie_breaks_multi_symbol_by_activity(agents_hub) -> None:
    save_agent(_running_agent("aa_recent", last_full_reasoning_at="2026-07-23T13:00:00+00:00"))
    save_agent(_running_agent("aa_stale", last_full_reasoning_at="2026-07-23T08:00:00+00:00"))

    resolved = resolve_running_agent_for_symbol("NIFTY")
    assert resolved == "aa_recent"


def test_record_execution_persists_agent_id(agents_hub) -> None:
    entry = record_execution(
        widget_id="tp_exec",
        underlying="NIFTY",
        legs=[],
        prediction_view=None,
        recommended_name="iron_condor",
        scenarios=[],
        execution_mode="paper",
        agent_id="aa_exec",
    )
    assert entry["agent_id"] == "aa_exec"
