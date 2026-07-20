"""Tests for ExecutionIntent → OpenAlgo execution."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.execute import execute_intent, leg_to_openalgo_order, legs_to_openalgo_orders  # noqa: E402
from nautilus_openalgo_bridge.models import ExecutionIntent, ExecutionLeg, IntentAction  # noqa: E402


def test_leg_to_openalgo_order_maps_fields():
    leg = ExecutionLeg(
        symbol="NIFTY24JUL24500CE",
        exchange="NFO",
        action="SELL",
        quantity=50,
        product="NRML",
        order_type="MARKET",
    )
    order = leg_to_openalgo_order(leg)
    assert order["symbol"] == "NIFTY24JUL24500CE"
    assert order["action"] == "SELL"
    assert order["quantity"] == 50


def test_execute_hold_skips():
    intent = ExecutionIntent(action=IntentAction.HOLD, agent_id="aa_x", rationale="wait")
    with patch("nautilus_openalgo_bridge.execute.reconcile_after_intent") as mock_rec:
        mock_rec.return_value = {"status": "ok"}
        result = execute_intent(intent, client=MagicMock(), persist=False)
    assert result["status"] == "skipped"
    mock_rec.assert_called_once()


def test_execute_exit_close_all():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.close_all_positions.return_value = {"status": "ok"}
    client.get_position_book.return_value = []
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_x",
        rationale="flatten",
    )
    with patch("nautilus_openalgo_bridge.execute.run_preflight", return_value={"blocked": False}), patch(
        "nautilus_openalgo_bridge.execute.reconcile_after_intent", return_value={"open_positions": 0}
    ), patch("nautilus_openalgo_bridge.handoff.clear_agent_position_state"):
        result = execute_intent(intent, client=client, persist=False, skip_preflight=True)
    assert result["status"] == "executed"
    assert result["mode"] == "close_all"
    client.close_all_positions.assert_called_once()


def test_execute_exit_with_legs_basket():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.place_basket.return_value = [{"orderid": "1"}]
    client.get_position_book.return_value = []
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_x",
        rationale="partial exit",
        legs=[
            ExecutionLeg(
                symbol="NIFTY24JUL24500CE",
                exchange="NFO",
                action="SELL",
                quantity=25,
            )
        ],
    )
    with patch("nautilus_openalgo_bridge.execute.reconcile_after_intent", return_value={"open_positions": 0}), patch(
        "nautilus_openalgo_bridge.handoff.clear_agent_position_state"
    ):
        result = execute_intent(intent, client=client, persist=False, skip_preflight=True)
    assert result["status"] == "executed"
    assert result["mode"] == "leg_basket"
    client.place_basket.assert_called_once()
    orders = client.place_basket.call_args[0][0]
    assert orders[0]["symbol"] == "NIFTY24JUL24500CE"


def test_execute_enter_blocked_by_preflight():
    client = MagicMock()
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch(
        "nautilus_openalgo_bridge.execute.run_preflight",
        return_value={"blocked": True, "reason": "outside_market_hours"},
    ):
        result = execute_intent(intent, client=client, persist=False)
    assert result["status"] == "blocked"
    client.place_basket.assert_not_called()


def test_execute_enter_requires_legs():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    intent = ExecutionIntent(action=IntentAction.ENTER, agent_id="aa_x", rationale="go", legs=[])
    with patch("nautilus_openalgo_bridge.execute.run_preflight", return_value={"blocked": False}):
        result = execute_intent(intent, client=client, persist=False, skip_preflight=True)
    assert result["status"] == "error"


def test_execute_exit_records_outcome_ledger():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.close_all_positions.return_value = {"status": "ok"}
    client.get_position_book.return_value = [{"pnl": -120.5, "quantity": 25, "symbol": "NIFTY24JUL24500CE"}]
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_x",
        rationale="flatten",
        underlying="NIFTY",
        strategy="short_straddle",
    )
    with patch("nautilus_openalgo_bridge.execute.run_preflight", return_value={"blocked": False}), patch(
        "nautilus_openalgo_bridge.execute.reconcile_after_intent",
        return_value={"open_positions": 0, "unrealized_pnl_inr": 0.0},
    ), patch("nautilus_openalgo_bridge.handoff.clear_agent_position_state"), patch(
        "trade_integrations.auto_paper.outcome_ledger.append_outcome"
    ) as append_mock, patch(
        "trade_integrations.auto_paper.outcome_ledger.reconcile_exit_outcome"
    ) as reconcile_mock:
        result = execute_intent(intent, client=client, persist=False, skip_preflight=True)
    assert result["status"] == "executed"
    append_mock.assert_called_once()
    reconcile_mock.assert_called_once()
    kwargs = reconcile_mock.call_args.kwargs
    assert kwargs.get("net_pnl_inr") == -120.5


def test_execute_exit_realized_pnl_from_partial_reconcile():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.close_all_positions.return_value = {"status": "ok"}
    client.get_position_book.return_value = [{"pnl": -200.0, "quantity": 25, "symbol": "NIFTY24JUL24500CE"}]
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_x",
        rationale="partial flatten",
        underlying="NIFTY",
        strategy="short_straddle",
    )
    with patch("nautilus_openalgo_bridge.execute.run_preflight", return_value={"blocked": False}), patch(
        "nautilus_openalgo_bridge.execute.reconcile_after_intent",
        return_value={"open_positions": 1, "unrealized_pnl_inr": -50.0},
    ), patch("nautilus_openalgo_bridge.handoff.clear_agent_position_state"), patch(
        "trade_integrations.auto_paper.outcome_ledger.append_outcome"
    ), patch("trade_integrations.auto_paper.outcome_ledger.reconcile_exit_outcome") as reconcile_mock:
        result = execute_intent(intent, client=client, persist=False, skip_preflight=True)
    assert result["status"] == "executed"
    reconcile_mock.assert_called_once()
    assert reconcile_mock.call_args.kwargs.get("net_pnl_inr") == -150.0


def test_process_intent_file_invalid_json(tmp_path: Path):
    from nautilus_openalgo_bridge.execute import process_intent_file

    bad = tmp_path / "bad-intent.json"
    bad.write_text("{not json", encoding="utf-8")
    result = process_intent_file(str(bad))
    assert result["status"] == "error"
    assert "path" in result
