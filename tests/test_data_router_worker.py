"""Tests for DataRouter background worker and backlog."""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.data_router import backlog
from trade_integrations.data_router.adapters.ohlcv import AdapterError
from trade_integrations.data_router.types import FetchSpec
from scripts.data_router_worker import process_job


@pytest.fixture
def hub_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "hub"))
    get_hub_dir().mkdir(parents=True, exist_ok=True)
    return get_hub_dir()


def test_enqueue_dedupes(hub_tmp):
    spec = FetchSpec(domain="ohlcv", market="us_equity", symbol="AAPL", start="2024-01-01", end="2024-01-31")
    job1, appended1 = backlog.enqueue(spec, "tiingo")
    job2, appended2 = backlog.enqueue(spec, "tiingo")
    assert job1 == job2
    assert appended1 is True
    assert appended2 is False
    assert backlog.pending_count() == 1


def test_worker_completes_ohlcv_job(hub_tmp):
    spec = FetchSpec(domain="ohlcv", market="us_equity", symbol="AAPL", start="2024-01-01", end="2024-01-05")
    job_id, _ = backlog.enqueue(spec, "yfinance")
    job = {
        "job_id": job_id,
        "spec": {
            "domain": spec.domain,
            "market": spec.market,
            "symbol": spec.symbol,
            "start": spec.start,
            "end": spec.end,
            "extra": spec.extra,
        },
        "source_id": "yfinance",
    }
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.05, 2.05],
            "volume": [100, 200],
        }
    )

    with patch("scripts.data_router_worker.fetch_ohlcv", return_value=frame):
        outcome = process_job(job)

    assert outcome == "completed"
    completed_path = hub_tmp / "_data" / "backlog" / "completed.jsonl"
    assert completed_path.is_file()
    rows = [json.loads(ln) for ln in completed_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows[-1]["job_id"] == job_id


def test_worker_requeues_on_budget(hub_tmp):
    spec = FetchSpec(domain="ohlcv", market="us_equity", symbol="AAPL", start="2024-01-01", end="2024-01-05")
    job_id, _ = backlog.enqueue(spec, "tiingo")
    job = {
        "job_id": job_id,
        "spec": {
            "domain": spec.domain,
            "market": spec.market,
            "symbol": spec.symbol,
            "start": spec.start,
            "end": spec.end,
            "extra": spec.extra,
        },
        "source_id": "tiingo",
    }

    with patch(
        "scripts.data_router_worker.fetch_ohlcv",
        side_effect=AdapterError("budget", reason="budget_exhausted"),
    ):
        outcome = process_job(job)

    assert outcome == "requeued"
    assert backlog.pending_count() == 1
