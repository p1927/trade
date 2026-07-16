"""Tests for positionbook reconciliation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.reconcile import (  # noqa: E402
    open_positions_from_book,
    sync_handoff_from_position_book,
    total_unrealized_pnl,
)
from trade_integrations.autonomous_agents.store import save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_open_positions_filters_zero_qty():
    rows = [
        {"symbol": "NIFTY24JUL24500CE", "quantity": 50},
        {"symbol": "NIFTY24JUL24600CE", "quantity": 0},
    ]
    assert len(open_positions_from_book(rows)) == 1


def test_total_unrealized_pnl():
    rows = [{"pnl": 100.0}, {"pnl": -50.0}]
    assert total_unrealized_pnl(rows) == 50.0


def test_sync_handoff_from_position_book(hub_tmp: Path):
    agent_id = "aa_reconcile"
    save_agent(
        {
            "id": agent_id,
            "symbols": ["NIFTY"],
            "mandate_config": {},
            "constraints": {},
        }
    )
    client = MagicMock()
    client.get_position_book.return_value = [
        {
            "symbol": "NIFTY24JUL24500CE",
            "exchange": "NFO",
            "quantity": 50,
            "product": "NRML",
            "pnl": 120.0,
        }
    ]
    client.get_symbol_info.return_value = {"symbol": "NIFTY24JUL24500CE"}

    handoff = sync_handoff_from_position_book(agent_id, client=client, underlying="NIFTY")
    assert handoff is not None
    assert len(handoff.legs) == 1
    assert handoff.legs[0].symbol == "NIFTY24JUL24500CE"
