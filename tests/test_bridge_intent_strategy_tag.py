"""Bridge intent strategy tag tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


@pytest.mark.unit
def test_enter_intent_uses_agent_strategy_tag(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    agent_id = "aa_bridge_tag"
    (hub_tmp / "_data" / "autonomous_agents" / f"{agent_id}.json").write_text(
        json.dumps({"id": agent_id, "symbols": ["NIFTY"], "constraints": {"mode": "paper"}}),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_execute(intent, persist=True):
        captured["strategy"] = intent.strategy
        return {"status": "executed", "results": []}

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.execute.execute_intent",
        fake_execute,
    )
    monkeypatch.setattr(
        "trade_integrations.execution.bridge_intent.resolve_profile",
        lambda agent: MagicMock(
            market="IN",
            mode="paper",
            uses_nautilus_handoff=True,
            uses_openalgo_paper=True,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.execution.bridge_intent.get_agent",
        lambda aid: {"id": aid, "symbols": ["NIFTY"], "constraints": {"mode": "paper"}, "mandate_config": {}},
    )
    monkeypatch.setattr(
        "trade_integrations.execution.bridge_intent.mandate_config_from_agent",
        lambda agent: MagicMock(to_dict=lambda: {}, resolve_product=lambda: "MIS"),
    )
    monkeypatch.setattr(
        "trade_integrations.execution.bridge_intent.assert_widget_allowed",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.monitor.execution_ledger.record_execution_from_widget",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.lifecycle.sync_agent_lifecycle_after_basket",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.outcome_ledger.append_outcome",
        lambda *a, **k: None,
    )

    from trade_integrations.execution.bridge_intent import execute_widget_via_bridge

    widget = {
        "widget_id": "w1",
        "underlying": "NIFTY",
        "recommended": {"name": "iron condor"},
        "implementation_steps": [
            {
                "action": "execute_basket",
                "payload": {"orders": [{"symbol": "NIFTY24JUL24500CE", "action": "BUY", "quantity": 1}]},
            }
        ],
    }

    result = execute_widget_via_bridge(
        widget,
        "w1",
        agent_id=agent_id,
        action="ENTER",
        rationale="test",
        confidence=80,
    )
    assert captured["strategy"] == agent_id
    assert result["strategy"] == "iron condor"
    assert result["order_strategy"] == agent_id


@pytest.mark.unit
def test_submit_exit_intent_uses_agent_strategy_tag(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    agent_id = "aa_exit_tag"
    (hub_tmp / "_data" / "autonomous_agents" / f"{agent_id}.json").write_text(
        json.dumps({"id": agent_id, "symbols": ["NIFTY"], "constraints": {"mode": "paper"}}),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_submit(intent):
        captured["strategy"] = intent.strategy
        return "/tmp/exit.json"

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.handoff.load_handoff",
        lambda aid: type("H", (), {"legs": []})(),
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.intent_queue.submit_intent",
        fake_submit,
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.intent_queue.process_pending_intents",
        lambda max_count=1: [],
    )

    from trade_integrations.execution.bridge_intent import submit_exit_intent

    submit_exit_intent(agent_id=agent_id, rationale="flatten")
    assert captured["strategy"] == agent_id
