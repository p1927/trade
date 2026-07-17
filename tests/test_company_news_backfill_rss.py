"""Tests for Google News RSS link extraction in company news backfill."""

from __future__ import annotations


def test_fetch_rss_headlines_extracts_item_link(monkeypatch):
    import requests

    from trade_integrations.dataflows.index_research import company_news_backfill as mod

    xml = b"""<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>Nifty falls on FII selling</title>
        <link>https://news.example.com/nifty-fii</link>
        <pubDate>Mon, 28 Apr 2026 10:00:00 GMT</pubDate>
        <source>Example News</source>
      </item>
    </channel></rss>"""

    class FakeResponse:
        content = xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())

    rows = mod._fetch_rss_headlines("https://news.google.com/rss/search?q=test")
    assert len(rows) == 1
    assert rows[0]["url"] == "https://news.example.com/nifty-fii"
