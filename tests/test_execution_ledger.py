"""Unit tests for execution ledger persistence."""

from __future__ import annotations

import json

import pytest

from trade_integrations.monitor.execution_ledger import (
    close_ledger_entry,
    get_ledger_entry,
    list_open_by_underlying,
    list_open_entries,
    load_ledger,
    match_positions_for_entry,
    record_execution,
    record_execution_from_widget,
)


def _sample_widget() -> dict:
    return {
        "type": "trade_plan.widget",
        "widget_id": "tp_NIFTY_abc123def456",
        "underlying": "NIFTY",
        "spot": 24500.0,
        "prediction": {"view": "bullish"},
        "agent_recommended_strategy": "Bull Call Spread",
        "recommended": {
            "name": "Bull Call Spread",
            "legs": [
                {"side": "BUY", "symbol": "NIFTY31JUL25C24500", "quantity": 50},
                {"side": "SELL", "symbol": "NIFTY31JUL25C24700", "quantity": 50},
            ],
            "net_max_loss": 5000,
        },
        "scenarios": [
            {
                "name": "bearish_breakdown",
                "probability": 0.22,
                "trigger": "Spot sells off toward lower expected range",
                "strategy_hint": "bear_put_spread",
            }
        ],
    }


@pytest.mark.unit
def test_record_load_and_list_open_by_underlying(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    entry = record_execution(
        widget_id="tp_NIFTY_test001",
        underlying="NIFTY",
        legs=[{"symbol": "NIFTY31JUL25C24500", "quantity": 50}],
        prediction_view="bullish",
        recommended_name="Bull Call Spread",
        scenarios=[{"name": "base_case"}],
        broker_order_ids=["OID-1"],
        plan_spot=24500.0,
        net_max_loss=5000.0,
    )

    assert entry["execution_id"].startswith("ex_NIFTY_")
    assert entry["status"] == "open"

    ledger_path = tmp_path / "_data" / "executions" / "ledger.json"
    assert ledger_path.is_file()

    loaded = load_ledger()
    assert len(loaded) == 1
    assert loaded[0]["widget_id"] == "tp_NIFTY_test001"

    assert get_ledger_entry("tp_NIFTY_test001") == loaded[0]
    assert list_open_entries() == loaded
    assert list_open_by_underlying("nifty") == loaded
    assert list_open_by_underlying("BANKNIFTY") == []


@pytest.mark.unit
def test_record_execution_from_widget_extracts_order_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    widget = _sample_widget()
    results = [{"orderid": "BR-100"}, {"order_id": "BR-101"}]
    entry = record_execution_from_widget(widget, results, execution_mode="paper")

    assert entry["widget_id"] == widget["widget_id"]
    assert entry["underlying"] == "NIFTY"
    assert entry["prediction_view"] == "bullish"
    assert entry["recommended_name"] == "Bull Call Spread"
    assert len(entry["legs"]) == 2
    assert entry["broker_order_ids"] == ["BR-100", "BR-101"]
    assert entry["execution_mode"] == "paper"
    assert entry["plan_spot"] == 24500.0
    assert entry["net_max_loss"] == 5000.0


@pytest.mark.unit
def test_match_positions_for_entry_sums_pnl():
    ledger_entry = {
        "legs": [
            {"symbol": "NIFTY31JUL25C24500"},
            {"symbol": "NIFTY31JUL25C24700"},
        ]
    }
    position_book = {
        "status": "success",
        "data": [
            {"symbol": "NIFTY31JUL25C24500", "pnl": -1200},
            {"symbol": "NIFTY31JUL25C24700", "pnl": 300},
            {"symbol": "RELIANCE", "pnl": 999},
        ],
    }

    matched, total_pnl = match_positions_for_entry(ledger_entry, position_book)

    assert len(matched) == 2
    assert total_pnl == -900.0


@pytest.mark.unit
def test_ledger_round_trip_json_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    record_execution_from_widget(_sample_widget(), [{"orderid": "1"}])
    payload = json.loads(
        (tmp_path / "_data" / "executions" / "ledger.json").read_text(encoding="utf-8")
    )

    assert "entries" in payload
    row = payload["entries"][0]
    for key in (
        "execution_id",
        "widget_id",
        "underlying",
        "legs",
        "prediction_view",
        "recommended_name",
        "scenarios",
        "executed_at",
        "status",
        "broker_order_ids",
    ):
        assert key in row


@pytest.mark.unit
def test_save_ledger_writes_executions_parquet(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    record_execution(
        widget_id="tp_NIFTY_parquet",
        underlying="NIFTY",
        legs=[{"symbol": "NIFTY31JUL25C24500", "quantity": 50}],
        prediction_view="bullish",
        recommended_name="Bull Call Spread",
        scenarios=[],
        broker_order_ids=["OID-1"],
        plan_spot=24500.0,
    )

    parquet_path = tmp_path / "_data" / "trades" / "executions.parquet"
    csv_path = parquet_path.with_suffix(".csv")
    assert parquet_path.is_file() or csv_path.is_file()

    try:
        import pandas as pd

        df = pd.read_parquet(parquet_path) if parquet_path.is_file() else pd.read_csv(csv_path)
    except ImportError:
        df = __import__("pandas").read_csv(csv_path)
    assert len(df) == 1
    assert df.iloc[0]["widget_id"] == "tp_NIFTY_parquet"
    assert df.iloc[0]["strategy"] == "Bull Call Spread"


@pytest.mark.unit
def test_close_ledger_entry_sets_pnl_and_outcome(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    entry = record_execution(
        widget_id="tp_NIFTY_close",
        underlying="NIFTY",
        legs=[{"symbol": "NIFTY31JUL25C24500"}],
        prediction_view="bullish",
        recommended_name="Bull Call Spread",
        scenarios=[],
        execution_mode="paper",
    )

    def _fake_position_book():
        return {
            "data": [
                {"symbol": "NIFTY31JUL25C24500", "quantity": 50, "pnl": 150.0},
            ]
        }

    monkeypatch.setattr(
        "trade_integrations.monitor.execution_ledger.fetch_position_book",
        _fake_position_book,
    )
    assert close_ledger_entry(entry["widget_id"]) is True

    closed = get_ledger_entry(entry["widget_id"])
    assert closed["status"] == "closed"
    assert closed.get("closed_at")
    assert closed.get("realized_pnl_inr") == 150.0

    outcomes_path = tmp_path / "_data" / "auto_paper" / "outcomes.parquet"
    outcomes_csv = outcomes_path.with_suffix(".csv")
    assert outcomes_path.is_file() or outcomes_csv.is_file()
