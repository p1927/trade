"""Multi-agent position scoping tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.agent_scoping import (  # noqa: E402
    default_exit_underlying,
    filter_positions_for_agent,
    strategy_tag_for_agent,
)
from nautilus_openalgo_bridge.reconcile import total_unrealized_pnl  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _save_agent(hub: Path, agent_id: str, symbols: list[str]) -> None:
    import json

    path = hub / "_data" / "autonomous_agents" / f"{agent_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": agent_id,
                "symbols": symbols,
                "execution_market": "IN",
                "constraints": {"mode": "paper"},
            }
        ),
        encoding="utf-8",
    )


def test_strategy_tag_defaults_to_agent_id(hub_tmp: Path):
    _save_agent(hub_tmp, "aa_one", ["NIFTY"])
    assert strategy_tag_for_agent("aa_one") == "aa_one"


def test_filter_by_strategy_tag(hub_tmp: Path):
    _save_agent(hub_tmp, "aa_nifty", ["NIFTY"])
    rows = [
        {"symbol": "NIFTY24JUL24500CE", "quantity": 25, "strategy": "aa_nifty", "pnl": -50},
        {"symbol": "BANKNIFTY24JUL52000CE", "quantity": 15, "strategy": "aa_bank", "pnl": -30},
    ]
    scoped = filter_positions_for_agent(rows, "aa_nifty")
    assert len(scoped) == 1
    assert scoped[0]["symbol"].startswith("NIFTY")


def test_two_agents_isolated_pnl(hub_tmp: Path):
    _save_agent(hub_tmp, "aa_a", ["NIFTY"])
    _save_agent(hub_tmp, "aa_b", ["BANKNIFTY"])
    book = [
        {"symbol": "NIFTY24JUL24500CE", "quantity": 10, "strategy": "aa_a", "pnl": -100},
        {"symbol": "BANKNIFTY24JUL52000CE", "quantity": 10, "strategy": "aa_b", "pnl": -200},
    ]
    pnl_a = total_unrealized_pnl(filter_positions_for_agent(book, "aa_a"))
    pnl_b = total_unrealized_pnl(filter_positions_for_agent(book, "aa_b"))
    assert pnl_a == -100
    assert pnl_b == -200


def test_default_exit_underlying_uses_agent_symbol_without_handoff(hub_tmp: Path):
    _save_agent(hub_tmp, "aa_spy", ["SPY"])
    assert default_exit_underlying("aa_spy") == "SPY"
