"""Tests for plan approval gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.plan_approval import (  # noqa: E402
    approve_agent_plan,
    ensure_plan_approval_record,
    is_awaiting_plan_approval,
    is_plan_approved,
    request_plan_reapproval,
    resolve_widget_id,
)
from trade_integrations.autonomous_agents.store import get_agent, load_agent, save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _agent(agent_id: str = "aa_plan") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Plan Test",
        "status": "running",
        "symbols": ["RELIANCE"],
        "bootstrap_status": "awaiting_plan_approval",
        "plan_approval_required": True,
        "active_trade_plan_widget_id": "tp_test_plan",
        "watch_spec": {"rules": [{"symbol": "RELIANCE", "metric": "spot_move_pct", "threshold": 1.0}], "strategy": "hold_cash"},
        "vibe_session_id": "sess_plan",
    }


def test_is_awaiting_plan_approval(hub_tmp: Path):
    save_agent(_agent())
    agent = get_agent("aa_plan")
    assert is_awaiting_plan_approval(agent) is True
    assert is_plan_approved(agent) is False


def test_approve_agent_plan(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.plan_approval.activate_agent_watch",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.plan_approval._activate_deferred_watch_spec",
        lambda *_a, **_k: None,
    )
    save_agent(_agent())
    result = approve_agent_plan("aa_plan")
    assert result["status"] == "ok"
    updated = get_agent("aa_plan")
    assert updated["bootstrap_status"] == "done"
    assert updated.get("plan_approved_at")
    assert updated.get("approved_trade_plan_widget_id")
    assert is_plan_approved(updated) is True


def test_approve_rejects_widget_mismatch(hub_tmp: Path):
    agent = _agent()
    agent["active_trade_plan_widget_id"] = "tp_one"
    save_agent(agent)
    result = approve_agent_plan("aa_plan", widget_id="tp_other")
    assert result["status"] == "error"


def test_request_plan_reapproval(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _agent()
    agent["bootstrap_status"] = "done"
    agent["plan_approved_at"] = "2026-07-16T20:00:00+00:00"
    agent["approved_trade_plan_widget_id"] = "tp_old"
    save_agent(agent)
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.plan_approval._pause_scheduled_research",
        lambda *_a, **_k: None,
    )
    result = request_plan_reapproval("aa_plan", "tp_new", source="user_guidance")
    assert result["status"] == "ok"
    updated = get_agent("aa_plan")
    assert updated["bootstrap_status"] == "awaiting_plan_approval"
    assert updated.get("active_trade_plan_widget_id") == "tp_new"
    assert not updated.get("plan_approved_at")


def test_resolve_widget_id_from_last_decision(hub_tmp: Path):
    agent = _agent()
    agent.pop("active_trade_plan_widget_id", None)
    agent["last_decision"] = {"widget_id": "tp_from_decision"}
    assert resolve_widget_id(agent) == "tp_from_decision"


def test_normalize_legacy_plan_approval_backfills_widget_ids(hub_tmp: Path):
    agent = _agent()
    agent["bootstrap_status"] = "done"
    agent["plan_approved_at"] = "2026-07-16T20:00:00+00:00"
    agent.pop("approved_trade_plan_widget_id", None)
    save_agent(agent)
    loaded = ensure_plan_approval_record(load_agent("aa_plan") or {}, persist=True)
    assert loaded.get("approved_trade_plan_widget_id") == "tp_test_plan"
