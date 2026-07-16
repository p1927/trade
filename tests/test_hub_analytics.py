"""Tests for DuckDB hub analytics views."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.hub_analytics import duckdb_views as ha


@pytest.fixture
def hub(tmp_path, monkeypatch):
    data = tmp_path / "_data"
    (data / "index_predictions").mkdir(parents=True)
    (data / "options_predictions").mkdir(parents=True)
    (data / "auto_paper").mkdir(parents=True)
    (data / "trades").mkdir(parents=True)
    (data / "index_factors" / "daily").mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "predicted_at": "2026-07-01T00:00:00+00:00",
                "horizon_days": 5,
                "actual_return_pct": 0.5,
                "direction_correct": True,
            }
        ]
    ).to_parquet(data / "index_predictions" / "ledger.parquet", index=False)

    pd.DataFrame(
        [
            {
                "underlying": "NIFTY",
                "strategy_name": "iron_condor",
                "direction_correct": True,
            }
        ]
    ).to_parquet(data / "options_predictions" / "ledger.parquet", index=False)

    pd.DataFrame(
        [
            {
                "strategy": "iron_condor",
                "net_pnl_inr": 120.0,
                "action": "CLOSE",
                "intent_source": "execution_ledger",
                "widget_id": "tp_NIFTY_abc",
            }
        ]
    ).to_parquet(data / "auto_paper" / "outcomes.parquet", index=False)

    pd.DataFrame(
        [
            {
                "execution_id": "ex_NIFTY_abc",
                "widget_id": "tp_NIFTY_abc",
                "underlying": "NIFTY",
                "strategy": "iron_condor",
                "status": "closed",
                "realized_pnl_inr": 120.0,
                "executed_at": "2026-07-01T10:00:00+00:00",
                "closed_at": "2026-07-01T15:00:00+00:00",
            }
        ]
    ).to_parquet(data / "trades" / "executions.parquet", index=False)

    pd.DataFrame(
        [
            {"symbol": "NIFTY31JUL25C24500", "qty": 50, "price": 100.0},
        ]
    ).to_parquet(data / "trades" / "fills.parquet", index=False)

    pd.DataFrame([{"date": "2026-07-01", "macro_vix": 14.2}]).to_parquet(
        data / "index_factors" / "daily" / "2026-07-01.parquet",
        index=False,
    )

    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    return tmp_path


def test_register_views_and_select(hub):
    con = ha.get_hub_connection()
    try:
        count = con.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        assert count == 1
        views = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        assert "executions" in views
        assert "index_factors_daily" in views
    finally:
        con.close()


def test_builtin_strategy_pnl(hub):
    result = ha.run_builtin_query("strategy_pnl")
    assert result["row_count"] == 1
    assert result["rows"][0]["strategy"] == "iron_condor"


def test_readonly_blocks_mutating_sql():
    with pytest.raises(ValueError, match="not allowed"):
        ha.validate_readonly_sql("DROP TABLE outcomes")


def test_execution_outcome_join(hub):
    result = ha.run_builtin_query("execution_outcome_join")
    assert result["row_count"] >= 1
    assert result["rows"][0]["execution_id"] == "ex_NIFTY_abc"
