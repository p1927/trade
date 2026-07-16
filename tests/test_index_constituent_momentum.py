"""Unit tests for constituent momentum rollup."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.constituent_momentum import (
    attach_constituent_momentum,
    momentum_coverage_stats,
    rollup_constituent_momentum,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal


@pytest.mark.unit
def test_rollup_constituent_momentum_weighted_average():
    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.6, momentum_7d_pct=4.0),
        ConstituentSignal(symbol="TCS", weight=0.4, momentum_7d_pct=-2.0),
    ]
    assert rollup_constituent_momentum(signals) == pytest.approx(1.6)


@pytest.mark.unit
def test_rollup_constituent_momentum_none_without_data():
    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.6),
        ConstituentSignal(symbol="TCS", weight=0.4),
    ]
    assert rollup_constituent_momentum(signals) is None


@pytest.mark.unit
def test_momentum_coverage_stats():
    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.6, momentum_7d_pct=4.0),
        ConstituentSignal(symbol="TCS", weight=0.4),
    ]
    stats = momentum_coverage_stats(signals)
    assert stats["with_momentum"] == 1
    assert stats["total"] == 2
    assert stats["coverage_pct"] == pytest.approx(50.0)


@pytest.mark.unit
def test_attach_constituent_momentum_uses_injected_returns():
    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.5),
        ConstituentSignal(symbol="TCS", weight=0.5),
    ]
    attached = attach_constituent_momentum(
        signals,
        returns_by_symbol={"RELIANCE": 3.0, "TCS": -1.0},
    )
    assert attached[0].momentum_7d_pct == pytest.approx(3.0)
    assert attached[1].momentum_7d_pct == pytest.approx(-1.0)
