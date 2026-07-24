"""Tests for external predictions blocked-page detection (Phase 1)."""

import base64

from trade_integrations.dataflows.index_research.external_predictions.page_block_detector import (
    BLOCK_REASON_COOKIE_BANNER,
    BLOCK_REASON_FOOTER_ONLY,
    BLOCK_REASON_THIN_MARKDOWN,
    detect_blocked_page,
)

# ET cookie modal snippet (dismiss-off capture — OneTrust visible in markdown head).
ET_COOKIE_BANNER_MD = """
# Markets
We value your privacy
We use cookies and similar technologies. Accept All Reject All
OneTrust Consent Manager
Manage Preferences
""".strip()

# Clean ET stocks/news listing with forecast lines + articleshow links.
ET_STOCKS_NEWS_CLEAN_MD = """
# Stock Market News
Latest updates on Nifty 50 and Indian equities.

## Nifty 50 outlook
Analysts see Nifty 50 target at 26,500 by month end with constructive forecast outlook.

[Nifty 50 target raised to 26,500](https://economictimes.indiatimes.com/markets/stocks/news/nifty-50-target-26500/articleshow/132357525.cms)
[Goldman Sachs pegs Nifty target at 26500](https://economictimes.indiatimes.com/markets/stocks/news/goldman-sachs-pegs-nifty-target-at-26500/articleshow/132357526.cms)
[Markets rally on FII flows](https://economictimes.indiatimes.com/markets/stocks/news/markets-rally-on-fii-flows/articleshow/132492041.cms)

More coverage on index support, resistance, and weekly prediction for Nifty 50.
Forecast outlook remains positive with target 26500 and outlook projection through month end.
""".strip() + (
    "\nAnalyst forecast and Nifty 50 target commentary. " * 40
)

# Broken /topic/nifty-50 crawl — thin shell without forecast keywords.
ET_TOPIC_NIFTY50_THIN_MD = """
Topic
Follow
Share
Markets Home
""".strip()

# Footer-only viewport — site chrome without article body in first 40 lines.
ET_FOOTER_ONLY_MD = """
Economic Times
Markets
Latest News
Trending
Copyright © Bennett, Coleman & Co. Ltd. All rights reserved.
Privacy Policy | Terms of Use
""".strip()


def test_cookie_banner_on_et_dismiss_off_snippet() -> None:
    signal = detect_blocked_page(
        url="https://economictimes.indiatimes.com/markets/stocks/news",
        markdown=ET_COOKIE_BANNER_MD,
        title="Markets",
    )
    assert signal.blocked is True
    assert BLOCK_REASON_COOKIE_BANNER in signal.reasons
    assert signal.confidence >= 0.8


def test_not_blocked_on_clean_stocks_news_markdown() -> None:
    signal = detect_blocked_page(
        url="https://economictimes.indiatimes.com/markets/stocks/news",
        markdown=ET_STOCKS_NEWS_CLEAN_MD,
        title="Stock Market News",
    )
    assert signal.blocked is False
    assert signal.reasons == []
    assert signal.confidence == 0.0


def test_thin_markdown_on_topic_nifty50_broken_sample() -> None:
    signal = detect_blocked_page(
        url="https://economictimes.indiatimes.com/topic/nifty-50",
        markdown=ET_TOPIC_NIFTY50_THIN_MD,
        title="Nifty 50",
    )
    assert signal.blocked is True
    assert BLOCK_REASON_THIN_MARKDOWN in signal.reasons


def test_footer_only_pattern() -> None:
    signal = detect_blocked_page(
        url="https://economictimes.indiatimes.com/markets/stocks/news",
        markdown=ET_FOOTER_ONLY_MD,
        title="Markets",
    )
    assert signal.blocked is True
    assert BLOCK_REASON_FOOTER_ONLY in signal.reasons


def test_vision_cookie_screenshot_when_markdown_thin_and_screenshot_large() -> None:
    large_jpeg = b"\xff\xd8" + (b"\x00" * (51 * 1024))
    screenshot_b64 = base64.b64encode(large_jpeg).decode("ascii")
    signal = detect_blocked_page(
        url="https://economictimes.indiatimes.com/topic/nifty-50",
        markdown=ET_TOPIC_NIFTY50_THIN_MD,
        screenshot_b64=screenshot_b64,
    )
    assert signal.blocked is True
    assert "vision_cookie_screenshot" in signal.reasons
