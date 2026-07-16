"""Tests for paper outcome ledger calibration."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.auto_paper import outcome_ledger as ol  # noqa: E402


@pytest.fixture
def ledger_path(tmp_path, monkeypatch):
    path = tmp_path / "outcomes.parquet"
    monkeypatch.setattr(ol, "ledger_path", lambda: path)
    return path


def test_paper_strategy_calibration_low_sample(ledger_path):
    assert ol.paper_strategy_calibration_adjustment("long_straddle") == 0.0


def test_paper_strategy_calibration_high_hit(ledger_path):
    df = pd.DataFrame(
        [
            {"strategy": "long_straddle", "net_pnl_inr": 100.0, "action": "EXIT"},
            {"strategy": "long_straddle", "net_pnl_inr": 50.0, "action": "EXIT"},
            {"strategy": "long_straddle", "net_pnl_inr": 80.0, "action": "EXIT"},
        ]
    )
    ol.save_ledger(df)
    assert ol.paper_strategy_calibration_adjustment("long_straddle") == pytest.approx(0.05)


def test_reconcile_exit_outcome_fills_pnl(ledger_path):
    ol.append_outcome(
        symbol="NIFTY",
        strategy="iron_condor",
        action="EXIT",
        intent_source="vibe_decision",
        net_pnl_inr=None,
    )
    row = ol.reconcile_exit_outcome(
        symbol="NIFTY",
        strategy="iron_condor",
        net_pnl_inr=-250.0,
    )
    assert row is not None
    assert float(row["net_pnl_inr"]) == -250.0


def test_execution_calibration_adjustment(ledger_path):
    df = pd.DataFrame(
        [
            {
                "strategy": "iron_condor",
                "net_pnl_inr": 100.0,
                "action": "CLOSE",
                "intent_source": "execution_ledger",
            },
            {
                "strategy": "iron_condor",
                "net_pnl_inr": 80.0,
                "action": "CLOSE",
                "intent_source": "execution_ledger",
            },
            {
                "strategy": "iron_condor",
                "net_pnl_inr": 50.0,
                "action": "CLOSE",
                "intent_source": "execution_ledger",
            },
        ]
    )
    ol.save_ledger(df)
    assert ol.execution_calibration_adjustment("iron_condor") == pytest.approx(0.05)
    assert ol.paper_strategy_calibration_adjustment("iron_condor") == 0.0
