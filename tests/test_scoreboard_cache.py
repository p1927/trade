"""Scoreboard cache invalidation tests."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
    scoreboard_needs_refresh,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    BACKTEST_TRACK_IDS,
    SCOREBOARD_SCHEMA_VERSION,
)


@pytest.mark.unit
def test_scoreboard_needs_refresh_on_horizon_mismatch():
    report = {
        "schema_version": SCOREBOARD_SCHEMA_VERSION,
        "eval_count": 50,
        "horizon_days": 14,
        "history_days": 730,
        "tracks": {tid: {"eval_count": 10} for tid in BACKTEST_TRACK_IDS},
    }
    assert scoreboard_needs_refresh(report, horizon_days=14) is False
    assert scoreboard_needs_refresh(report, horizon_days=21) is True


@pytest.mark.unit
def test_scoreboard_needs_refresh_on_history_days():
    report = {
        "schema_version": SCOREBOARD_SCHEMA_VERSION,
        "eval_count": 50,
        "horizon_days": 14,
        "history_days": 365,
        "tracks": {tid: {"eval_count": 10} for tid in BACKTEST_TRACK_IDS},
    }
    assert scoreboard_needs_refresh(report, history_days=730) is True
