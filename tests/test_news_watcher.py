"""Unit tests for material news watcher and options monitor scheduler."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trade_integrations.dataflows.news_aggregator.models import NewsArticle
from trade_integrations.monitor.news_watcher import (
    MaterialHeadline,
    check_material_news,
    headline_fingerprint,
)

AGENT_ROOT = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))


@pytest.mark.unit
def test_earnings_headline_is_material(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    article = NewsArticle(
        title="NIFTY heavyweight reports strong Q1 earnings beat",
        link="https://example.com/earnings",
        pub_date=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
    )
    since = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)

    with patch(
        "trade_integrations.monitor.news_watcher._fetch_ticker_articles",
        return_value=[article],
    ):
        headlines = check_material_news("NIFTY", since)

    assert len(headlines) == 1
    assert isinstance(headlines[0], MaterialHeadline)
    assert "earnings" in headlines[0].matched_keywords
    assert headlines[0].fingerprint == headline_fingerprint(article.title, article.link)

    seen_path = tmp_path / "_data" / "news_seen" / "NIFTY.json"
    assert seen_path.is_file()
    payload = json.loads(seen_path.read_text(encoding="utf-8"))
    assert headlines[0].fingerprint in payload["fingerprints"]


@pytest.mark.unit
def test_same_headline_not_material_second_time(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    article = NewsArticle(
        title="RBI policy update moves banking stocks",
        link="https://example.com/rbi",
        pub_date=datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc),
    )
    since = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)

    with patch(
        "trade_integrations.monitor.news_watcher._fetch_ticker_articles",
        return_value=[article],
    ):
        first = check_material_news("NIFTY", since)
        second = check_material_news("NIFTY", since)

    assert len(first) == 1
    assert second == []


@pytest.mark.unit
def test_scheduler_skipped_when_monitor_disabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "false")
    monkeypatch.setenv("OPTIONS_MONITOR_ENABLE_SCHEDULER", "true")

    from src.scheduled_research.options_jobs import run_options_plan_refresh_job

    with patch(
        "src.scheduled_research.options_jobs.refresh_options_research"
    ) as refresh_mock:
        result = run_options_plan_refresh_job({"watchlist": ["NIFTY"]})

    assert result == {"skipped": True, "reason": "monitor_disabled"}
    refresh_mock.assert_not_called()
