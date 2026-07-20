"""Tests for async intent queue."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.intent_queue import (  # noqa: E402
    list_pending_intents,
    process_pending_intents,
    submit_intent,
)
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from nautilus_openalgo_bridge.risk_state import clear_intent_dedupe, clear_trading_halt

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    clear_trading_halt()
    clear_intent_dedupe()
    yield hub
    clear_trading_halt()
    clear_intent_dedupe()


def test_submit_and_process_intent(hub_tmp: Path):
    intent = ExecutionIntent(
        action=IntentAction.HOLD,
        agent_id="aa_q",
        rationale="wait",
        intent_id="intent_test_hold",
    )
    path = submit_intent(intent)
    assert path.is_file()
    assert len(list_pending_intents()) == 1

    with patch("nautilus_openalgo_bridge.execute.reconcile_after_intent") as mock_reconcile:
        mock_reconcile.return_value = {"status": "ok"}
        results = process_pending_intents(client=MagicMock(), max_count=5)

    assert len(results) == 1
    assert results[0]["status"] == "skipped"
    assert len(list_pending_intents()) == 0
    processed = hub_tmp / "_data" / "nautilus_intents" / "processed" / "intent_test_hold.json"
    assert processed.is_file()
    archived = json.loads(processed.read_text(encoding="utf-8"))
    assert archived["_execution_result"]["status"] == "skipped"


def test_process_pending_skips_halted_intent(hub_tmp: Path):
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_halted",
        rationale="flatten",
        intent_id="intent_halted_exit",
    )
    submit_intent(intent)
    assert len(list_pending_intents()) == 1

    with patch("nautilus_openalgo_bridge.intent_queue.is_trading_halted", return_value=True):
        results = process_pending_intents(client=MagicMock(), max_count=5)

    assert len(results) == 1
    assert results[0]["status"] == "halted_skipped"
    assert len(list_pending_intents()) == 0
    skipped = hub_tmp / "_data" / "nautilus_intents" / "halted_skipped" / "intent_halted_exit.json"
    assert skipped.is_file()
