"""Unit tests for batch constituent research and index hub persistence."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from trade_integrations.context.hub import (
    is_index_research_cache_fresh,
    load_index_research_json,
    load_index_research_markdown,
    save_index_research,
)
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc
from trade_integrations.dataflows.index_research.models import ConstituentRow, IndexResearchDoc


def _mock_constituents() -> list[ConstituentRow]:
    return [
        ConstituentRow(
            symbol="RELIANCE",
            name="Reliance Industries Ltd.",
            sector="Oil Gas & Consumable Fuels",
            weight=0.6,
        ),
        ConstituentRow(
            symbol="TCS",
            name="Tata Consultancy Services Ltd.",
            sector="Information Technology",
            weight=0.4,
        ),
    ]


def _company_doc(
    symbol: str,
    *,
    sentiment_score: float = 0.42,
    event_date: str | None = None,
) -> CompanyResearchDoc:
    today = date.today()
    event_day = event_date or (today + timedelta(days=3)).isoformat()
    now = datetime.now(timezone.utc)
    return CompanyResearchDoc(
        ticker=symbol,
        as_of=now,
        lookahead_days=14,
        market="IN",
        calendar_events=[
            {
                "date": event_day,
                "type": "results",
                "description": f"{symbol} quarterly results",
            }
        ],
        sentiment={"score": sentiment_score},
    )


@pytest.mark.unit
def test_batch_uses_cached_company_research(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.load_nifty50_constituents",
        lambda: _mock_constituents(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.is_cache_fresh",
        lambda _sym: True,
    )

    docs = {
        "RELIANCE": _company_doc("RELIANCE", sentiment_score=0.55),
        "TCS": _company_doc("TCS", sentiment_score=0.33),
    }

    def fake_load(symbol: str) -> CompanyResearchDoc | None:
        return docs.get(symbol)

    run_mock = MagicMock()
    save_mock = MagicMock()
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.load_company_research_json",
        fake_load,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.run_company_research",
        run_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.save_company_research",
        save_mock,
    )

    from trade_integrations.dataflows.index_research.sources.batch_constituents import (
        batch_constituent_research,
    )

    signals = batch_constituent_research(max_workers=2, lookahead_days=14)

    assert len(signals) == 2
    assert signals[0].symbol == "RELIANCE"
    assert signals[0].weight == pytest.approx(0.6)
    assert signals[0].sector == "Oil Gas & Consumable Fuels"
    assert signals[0].sentiment_score == pytest.approx(0.55)
    assert len(signals[0].events) == 1
    assert signals[0].events[0]["type"] == "results"
    assert signals[0].factors == []
    assert signals[0].contribution_to_index_pct is None

    assert signals[1].symbol == "TCS"
    assert signals[1].weight == pytest.approx(0.4)
    assert signals[1].sentiment_score == pytest.approx(0.33)

    run_mock.assert_not_called()
    save_mock.assert_not_called()


@pytest.mark.unit
def test_batch_runs_research_when_stale(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.load_nifty50_constituents",
        lambda: _mock_constituents(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.is_cache_fresh",
        lambda _sym: False,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.load_company_research_json",
        lambda _sym: None,
    )

    run_mock = MagicMock(side_effect=lambda sym, **kwargs: _company_doc(sym))
    save_mock = MagicMock()
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.run_company_research",
        run_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.batch_constituents.save_company_research",
        save_mock,
    )

    from trade_integrations.dataflows.index_research.sources.batch_constituents import (
        batch_constituent_research,
    )

    signals = batch_constituent_research(max_workers=2, lookahead_days=14)

    assert len(signals) == 2
    assert run_mock.call_count == 2
    assert save_mock.call_count == 2
    run_mock.assert_any_call("RELIANCE", lookahead_days=14)
    run_mock.assert_any_call("TCS", lookahead_days=14)


@pytest.mark.unit
def test_hub_index_research_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    doc = IndexResearchDoc(
        ticker="NIFTY",
        as_of=now,
        horizon={"days": 14},
        spot=24500.0,
        constituent_signals=[
            {"symbol": "RELIANCE", "weight": 0.1, "sentiment_score": 0.5},
            {"symbol": "TCS", "weight": 0.08, "sentiment_score": 0.3},
        ],
    )

    save_index_research(doc)
    loaded = load_index_research_json("NIFTY")
    markdown = load_index_research_markdown("NIFTY")

    assert loaded is not None
    assert loaded.ticker == "NIFTY"
    assert loaded.spot == pytest.approx(24500.0)
    assert len(loaded.constituent_signals) == 2
    assert loaded.constituent_signals[0]["symbol"] == "RELIANCE"
    assert markdown is not None
    assert "Index Research — NIFTY" in markdown
    assert "Constituents analyzed:** 2" in markdown
    assert is_index_research_cache_fresh("NIFTY") is True
