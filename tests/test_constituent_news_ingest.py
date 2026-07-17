"""Tests for constituent news hub ingest on refresh-all-50."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.company_research.models import CompanyResearchDoc


def _fake_doc_with_news(symbol: str, *, headlines: list[dict[str, str]]) -> CompanyResearchDoc:
    return CompanyResearchDoc(
        ticker=symbol,
        as_of=datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
        lookahead_days=14,
        market="IN",
        news={
            "blocks": [
                {
                    "ticker": symbol,
                    "source": "searxng",
                    "headlines": headlines,
                }
            ],
            "batch_mode": "nifty50",
        },
    )


@pytest.mark.unit
def test_ingest_skipped_when_not_refresh(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.index_research import constituent_news_ingest as mod
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    calls: list[int] = []
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.ingest_rows_to_hub",
        lambda *a, **k: calls.append(1) or {"ingested": 1},
    )

    doc = _fake_doc_with_news("RELIANCE", headlines=[{"title": "Reliance beats estimates"}])
    stats = mod.maybe_ingest_constituent_news(doc, symbol="RELIANCE", refresh=False)
    assert stats.get("skipped") is True
    assert stats.get("ingested") == 0
    assert calls == []


@pytest.mark.unit
def test_ingest_runs_when_refresh(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.index_research import constituent_news_ingest as mod
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)

    captured: list[tuple[list[dict], str]] = []

    def fake_ingest(rows, *, ticker, **kwargs):
        captured.append((rows, ticker))
        return {"ingested": len(rows), "queued": len(rows), "ticker": ticker}

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.ingest_rows_to_hub",
        fake_ingest,
    )

    doc = _fake_doc_with_news("RELIANCE", headlines=[{"title": "Reliance beats estimates"}])
    stats = mod.maybe_ingest_constituent_news(doc, symbol="RELIANCE", refresh=True)
    assert stats["ingested"] == 1
    assert captured
    assert captured[0][1] == "RELIANCE"
    assert captured[0][0][0]["title"] == "Reliance beats estimates"


@pytest.mark.unit
def test_batch_research_one_ingests_on_refresh(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.index_research.sources import batch_constituents as batch

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(batch, "is_cache_fresh", lambda _sym: False)

    doc = _fake_doc_with_news("RELIANCE", headlines=[{"title": "Reliance Q1 strong"}])
    ingest_calls: list[bool] = []

    monkeypatch.setattr(batch, "run_company_research", lambda *a, **k: doc)
    monkeypatch.setattr(batch, "save_company_research", lambda _doc: None)
    monkeypatch.setattr(
        batch,
        "maybe_ingest_constituent_news",
        lambda _doc, *, symbol, refresh: ingest_calls.append(refresh)
        or {"ingested": 1 if refresh else 0},
    )
    monkeypatch.setattr(batch, "set_nifty50_batch", lambda _active: None)

    batch._research_one("RELIANCE", lookahead_days=14, refresh=True)
    assert ingest_calls == [True]

    ingest_calls.clear()
    batch._research_one("RELIANCE", lookahead_days=14, refresh=False)
    assert ingest_calls == []


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    return hub
