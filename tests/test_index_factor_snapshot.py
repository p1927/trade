"""Unit tests for the daily index factor snapshot."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.snapshot import (
    build_constituent_aggregate_rows,
    run_snapshot,
)


def _mock_signals() -> list[ConstituentSignal]:
    return [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.6,
            sector="Energy",
            sentiment_score=0.2,
            events=[{"type": "results", "date": "2026-07-20"}],
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.4,
            sector="Information Technology",
            sentiment_score=0.6,
            events=[{"type": "dividend", "date": "2026-07-18"}],
        ),
        ConstituentSignal(
            symbol="INFY",
            weight=0.3,
            sector="Information Technology",
            sentiment_score=0.4,
            events=[{"type": "earnings", "date": "2026-07-22"}],
        ),
    ]


@pytest.mark.unit
def test_build_constituent_aggregate_rows():
    rows = build_constituent_aggregate_rows(_mock_signals())
    by_factor = {row["factor"]: row for row in rows}

    assert by_factor["sector_breadth_mean_sentiment"]["value"] == pytest.approx(0.35)
    assert by_factor["earnings_events_14d_count"]["value"] == pytest.approx(2.0)
    assert by_factor["sector_breadth_mean_sentiment"]["source"] == "constituent_aggregate"


@pytest.mark.unit
def test_snapshot_script_writes_daily_file(monkeypatch):
    save_mock = MagicMock()
    batch_mock = MagicMock(return_value=_mock_signals())
    collect_mock = MagicMock(
        return_value=[
            {"factor": "usd_inr", "value": 83.2, "source": "yfinance"},
            {"factor": "index_sentiment", "value": 0.4, "source": "constituent_roll_up"},
        ]
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.batch_constituent_research",
        batch_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.collect_global_factor_rows",
        collect_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.save_daily_factors",
        save_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.get_factor_data_dir",
        lambda: MagicMock(__truediv__=lambda _self, name: f"/tmp/index_factors/daily/{name}"),
    )

    summary = run_snapshot(snapshot_date="2026-07-16", skip_constituents=False)

    batch_mock.assert_called_once_with(refresh=False)
    collect_mock.assert_called_once_with(constituent_sentiments=[0.2, 0.6, 0.4])
    save_mock.assert_called_once()
    saved_date, saved_rows = save_mock.call_args.args

    assert saved_date == "2026-07-16"
    factors = {row["factor"] for row in saved_rows}
    assert "usd_inr" in factors
    assert "index_sentiment" in factors
    assert "sector_breadth_mean_sentiment" in factors
    assert "earnings_events_14d_count" in factors
    assert summary["factor_count"] == len(saved_rows)


@pytest.mark.unit
def test_snapshot_skip_constituents(monkeypatch):
    save_mock = MagicMock()
    batch_mock = MagicMock()
    collect_mock = MagicMock(
        return_value=[{"factor": "oil_brent", "value": 80.0, "source": "yfinance"}]
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.batch_constituent_research",
        batch_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.collect_global_factor_rows",
        collect_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.save_daily_factors",
        save_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.snapshot.get_factor_data_dir",
        lambda: MagicMock(__truediv__=lambda _self, name: f"/tmp/index_factors/daily/{name}"),
    )

    summary = run_snapshot(snapshot_date="2026-07-16", skip_constituents=True)

    batch_mock.assert_not_called()
    collect_mock.assert_called_once_with(constituent_sentiments=None)
    save_mock.assert_called_once_with(
        "2026-07-16",
        [{"factor": "oil_brent", "value": 80.0, "source": "yfinance"}],
    )
    assert summary["skip_constituents"] is True
    assert summary["constituent_count"] == 0
