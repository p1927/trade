"""Unit tests for index constituent attribution."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituent,
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal


def _fixture_signals() -> list[ConstituentSignal]:
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
            sentiment_score=-0.8,
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
def test_attribute_constituent_sentiment_contribution(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.attribution._today",
        lambda: date(2026, 7, 16),
    )

    signal = ConstituentSignal(symbol="HDFCBANK", weight=0.5, sentiment_score=0.2)
    attributed = attribute_constituent(signal)

    assert attributed.contribution_to_index_pct == pytest.approx(0.5)


@pytest.mark.unit
def test_attribute_constituent_earnings_bump_and_cap(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.attribution._today",
        lambda: date(2026, 7, 16),
    )

    capped = attribute_constituent(
        ConstituentSignal(symbol="TCS", weight=1.0, sentiment_score=-0.8)
    )
    assert capped.contribution_to_index_pct == pytest.approx(-3.0)

    with_bump = attribute_constituent(
        ConstituentSignal(
            symbol="RELIANCE",
            weight=1.0,
            sentiment_score=0.2,
            events=[{"type": "results", "date": "2026-07-20"}],
        )
    )
    assert with_bump.contribution_to_index_pct == pytest.approx(1.5)


@pytest.mark.unit
def test_attribute_constituents_sorted_and_rollup(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.attribution._today",
        lambda: date(2026, 7, 16),
    )

    attributed = attribute_constituents(_fixture_signals())
    contributions = [signal.contribution_to_index_pct for signal in attributed]

    assert attributed[0].symbol == "TCS"
    assert contributions == sorted(contributions, key=abs, reverse=True)

    rollup = rollup_attribution(attributed)
    assert rollup["total_contribution_pct"] == pytest.approx(sum(contributions))
    assert rollup["top_drivers"][0]["symbol"] == "TCS"
    assert len(rollup["top_drivers"]) == 3
