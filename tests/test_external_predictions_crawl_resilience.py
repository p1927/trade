"""Tests for crawl resilience helpers."""

import json

import pytest

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
    crawl_rows_all_bot_blocked,
    crawl_rows_all_failed,
    crawl_rows_any_bot_blocked,
    crawl_rows_have_usable_text,
    is_bot_block_error,
    should_run_searxng_fallback,
    sort_urls_for_crawl,
)


def test_is_bot_block_error_detects_akamai() -> None:
    assert is_bot_block_error("Blocked by anti-bot protection: Akamai block (Reference #)")
    assert not is_bot_block_error("Connection timeout")


def test_is_akamai_wrapped_markdown_detects_geo_shell() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
        is_akamai_wrapped_markdown,
    )

    wrapped = (
        "[REDIRECT_QUERY_STRING] => url=https://www.moneycontrol.com/news/tags/nifty.html\n"
        "[REQUEST_URI] => /europe/?url=https://www.moneycontrol.com/news/tags/nifty.html\n"
    )
    assert is_akamai_wrapped_markdown(
        wrapped,
        "https://www.moneycontrol.com/news/tags/nifty.html",
    )
    assert not is_akamai_wrapped_markdown(
        "Nifty 50 analysts expect the index to reach 24000 by month end.",
        "https://economictimes.indiatimes.com/markets/stocks/news",
    )


def test_finalize_crawl_result_marks_akamai_wrapped_as_failure() -> None:
    from trade_integrations.dataflows.crawl4ai_client import _finalize_crawl_result

    wrapped = "[REDIRECT_QUERY_STRING] => url=https://www.moneycontrol.com/news/tags/nifty.html"
    row = _finalize_crawl_result(
        url="https://www.moneycontrol.com/news/tags/nifty.html",
        batch_profile="cdp",
        markdown=wrapped,
        title="Wrong title",
        metadata={"browser_profile": "cdp"},
        elapsed_ms=10.0,
    )
    assert row.success is False
    assert "Akamai wrapped" in row.error_message
    assert row.metadata.get("akamai_wrapped") is True


def test_crawl_rows_all_bot_blocked() -> None:
    rows = [
        (
            "https://www.moneycontrol.com/x",
            CrawlPageResult(
                url="https://www.moneycontrol.com/x",
                success=False,
                error_message="Blocked by anti-bot protection: Akamai block",
            ),
        ),
    ]
    assert crawl_rows_all_bot_blocked(rows)


def test_crawl_rows_any_bot_blocked_mixed_failures() -> None:
    rows = [
        (
            "https://example.com/a",
            CrawlPageResult(url="https://example.com/a", success=False, error_message="Connection timeout"),
        ),
        (
            "https://www.moneycontrol.com/x",
            CrawlPageResult(
                url="https://www.moneycontrol.com/x",
                success=False,
                error_message="Blocked by anti-bot protection: Akamai block",
            ),
        ),
    ]
    assert not crawl_rows_all_bot_blocked(rows)
    assert crawl_rows_any_bot_blocked(rows)


def test_crawl_rows_have_usable_text() -> None:
    rows = [
        (
            "https://economictimes.indiatimes.com/markets/stocks/news",
            CrawlPageResult(
                url="https://economictimes.indiatimes.com/markets/stocks/news",
                success=True,
                markdown="Nifty closed lower today. Markets were volatile across sectors. " * 3,
            ),
        ),
    ]
    assert crawl_rows_have_usable_text(rows)


def test_should_run_searxng_fallback_matrix() -> None:
    bot_row = CrawlPageResult(
        url="https://www.moneycontrol.com/x",
        success=False,
        error_message="Blocked by anti-bot protection: Akamai block",
    )
    ok_text = CrawlPageResult(
        url="https://economictimes.indiatimes.com/news",
        success=True,
        markdown="Nifty market wrap with enough content to qualify for usable text fallback path. " * 2,
    )
    timeout_row = CrawlPageResult(url="https://example.com", success=False, error_message="Connection timeout")

    run, reason = should_run_searxng_fallback([("u", bot_row)], "Blocked by anti-bot")
    assert run and reason == "bot_all"

    run, reason = should_run_searxng_fallback(
        [("a", timeout_row), ("b", bot_row)],
        "Connection timeout",
    )
    assert run and reason == "bot_any"

    run, reason = should_run_searxng_fallback([("u", ok_text)], "Crawl failed for all URLs")
    assert run and reason == "crawl_no_forecast"

    timeout_row = CrawlPageResult(url="https://example.com", success=False, error_message="Connection timeout")
    run, reason = should_run_searxng_fallback([("u", timeout_row)], "Connection timeout")
    assert run and reason == "crawl_all_failed"

    short_ok = CrawlPageResult(url="https://example.com", success=True, markdown="Nifty page stub")
    run, reason = should_run_searxng_fallback([("u", short_ok)], "No forecast in crawl")
    assert run and reason == "crawl_no_forecast"

    run, reason = should_run_searxng_fallback([], "anything")
    assert not run and reason == ""

    run, reason = should_run_searxng_fallback([("u", bot_row)], "Connection timeout")
    assert run and reason in {"bot_all", "bot_message", "bot_any"}


def test_sort_urls_for_crawl_deprioritizes_moneycontrol() -> None:
    urls = [
        "https://www.moneycontrol.com/news/business/markets/",
        "https://economictimes.indiatimes.com/markets/stocks/news",
    ]
    ordered = sort_urls_for_crawl(urls)
    assert ordered[0].startswith("https://economictimes")


def test_validate_record_uses_high_when_mid_missing() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    record = ExternalPredictionRecord(
        source_id="motilal_oswal",
        symbol="NIFTY",
        horizon_days=14,
        target=ExternalPredictionTarget(low=None, mid=None, high=24000.0),
        extraction={"instrument": "NIFTY50"},
    )
    body = "Nifty 50 analysts expect the index to reach 24000 by month end."
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "ok"
    assert validated.target.mid == 24000.0


def test_browser_profile_tiers_default_stealth(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import (
        browser_profile_tiers,
        primary_browser_profile,
    )

    monkeypatch.delenv("CRAWL4AI_CDP_URL", raising=False)
    monkeypatch.delenv("CRAWL4AI_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setenv("CRAWL4AI_UNDETECTED", "0")
    assert browser_profile_tiers() == ["stealth"]
    assert primary_browser_profile() == "stealth"


def test_browser_profile_tiers_cdp_before_stealth(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import (
        browser_profile_tiers,
        next_browser_profile,
        primary_browser_profile,
    )

    monkeypatch.setenv("CRAWL4AI_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.delenv("CRAWL4AI_PROXY", raising=False)
    monkeypatch.setenv("CRAWL4AI_UNDETECTED", "0")
    assert browser_profile_tiers() == ["cdp", "stealth"]
    assert primary_browser_profile() == "cdp"
    assert next_browser_profile("cdp") == "stealth"


def test_run_config_enables_popup_dismiss_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import _run_config

    monkeypatch.delenv("CRAWL4AI_REMOVE_CONSENT_POPUPS", raising=False)
    monkeypatch.delenv("CRAWL4AI_REMOVE_OVERLAY_ELEMENTS", raising=False)
    cfg = _run_config(screenshot=True)
    assert cfg.remove_consent_popups is True
    assert cfg.remove_overlay_elements is True
    assert cfg.js_code
    assert "popup_container" in cfg.js_code
    assert "dismissPatterns" in cfg.js_code


def test_run_config_popup_dismissal_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import _run_config

    monkeypatch.setenv("CRAWL4AI_REMOVE_CONSENT_POPUPS", "0")
    monkeypatch.setenv("CRAWL4AI_REMOVE_OVERLAY_ELEMENTS", "0")
    cfg = _run_config(screenshot=True)
    assert cfg.remove_consent_popups is False
    assert cfg.remove_overlay_elements is False
    assert not cfg.js_code


def test_run_config_consent_only_omits_overlay_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import _run_config

    monkeypatch.setenv("CRAWL4AI_REMOVE_CONSENT_POPUPS", "1")
    monkeypatch.setenv("CRAWL4AI_REMOVE_OVERLAY_ELEMENTS", "0")
    cfg = _run_config(screenshot=True)
    assert cfg.remove_consent_popups is True
    assert cfg.remove_overlay_elements is False
    assert cfg.js_code
    assert "dismissPatterns" in cfg.js_code
    assert "popup_container" not in cfg.js_code


def test_run_config_overlay_only_omits_consent_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import _run_config

    monkeypatch.setenv("CRAWL4AI_REMOVE_CONSENT_POPUPS", "0")
    monkeypatch.setenv("CRAWL4AI_REMOVE_OVERLAY_ELEMENTS", "1")
    cfg = _run_config(screenshot=True)
    assert cfg.remove_consent_popups is False
    assert cfg.remove_overlay_elements is True
    assert cfg.js_code
    assert "popup_container" in cfg.js_code
    assert "dismissPatterns" not in cfg.js_code


def test_filter_searxng_hits_rejects_off_domain() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
        filter_searxng_hits_for_source,
    )

    source = ExternalPredictionSource(
        id="moneycontrol",
        display_name="Moneycontrol",
        domains=["moneycontrol.com"],
    )
    hits = [
        {
            "url": "https://timesofindia.indiatimes.com/markets/nifty",
            "title": "Nifty 50 target forecast",
        },
        {
            "url": "https://www.moneycontrol.com/news/business/markets/nifty-50-outlook",
            "title": "Nifty 50 weekly outlook target",
        },
    ]
    filtered = filter_searxng_hits_for_source(hits, source)
    assert len(filtered) == 1
    assert "moneycontrol.com" in filtered[0]["url"]


def test_resolve_source_urls_includes_landing_and_curated() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        resolve_source_urls,
    )

    source = ExternalPredictionSource(
        id="motilal_oswal",
        display_name="Motilal Oswal",
        domains=["motilaloswal.com", "economictimes.indiatimes.com"],
        landing_urls=["https://www.motilaloswal.com/research-and-reports"],
        curated_urls=["https://economictimes.indiatimes.com/topic/nifty-50"],
    )
    urls = resolve_source_urls(source, horizon_days=14)
    assert "https://www.motilaloswal.com/research-and-reports" in urls
    assert "https://economictimes.indiatimes.com/topic/nifty-50" in urls
    assert urls.index("https://www.motilaloswal.com/research-and-reports") < urls.index(
        "https://economictimes.indiatimes.com/topic/nifty-50"
    )


def test_browse_enabled_from_entry_urls_only() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.browse_agent import (
        browse_enabled_for_source,
        resolve_browse_entry_urls,
    )

    with_landing_only = ExternalPredictionSource(
        id="equitylogy",
        display_name="EquityLogy",
        domains=["equitylogy.in"],
        landing_urls=["https://equitylogy.in/market/nifty/"],
        entry_urls=[],
    )
    assert not browse_enabled_for_source(with_landing_only)
    assert resolve_browse_entry_urls(with_landing_only, horizon_days=14) == []
    assert resolve_browse_entry_urls(
        with_landing_only,
        horizon_days=14,
        include_landing_fallback=True,
    ) == ["https://equitylogy.in/market/nifty/"]

    source = ExternalPredictionSource(
        id="choice_india",
        display_name="Choice India",
        domains=["choiceindia.com"],
        landing_urls=["https://choiceindia.com/blog"],
        entry_urls=["https://choiceindia.com/blog"],
    )
    assert browse_enabled_for_source(source)
    entries = resolve_browse_entry_urls(source, horizon_days=14)
    assert entries == ["https://choiceindia.com/blog"]


def test_is_structured_hub_weekly_support_resistance() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        is_structured_nifty_forecast_hub,
    )

    body = (
        "Nifty 50 weekly outlook: support at 24,000 and resistance at 24,800 "
        "for the coming week."
    )
    assert is_structured_nifty_forecast_hub(body, title="Weekly Nifty 50 view")


def test_validate_record_rejects_resistance_only_level() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    record = ExternalPredictionRecord(
        source_id="icici_direct",
        symbol="NIFTY",
        horizon_days=14,
        target=ExternalPredictionTarget(mid=24000.0, high=24000.0),
        extraction={"instrument": "NIFTY50"},
        rationale_bullets=["Resistance near 24,000 caps near-term upside"],
    )
    body = "Nifty 50 is likely to face resistance near the 24,000 mark in the near term."
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "not_found"
    assert validated.error_message == "resistance_not_target"


@pytest.mark.parametrize(
    "body",
    [
        "Nifty 50 may see resistance near 24,000 in the near term.",
        "Analysts see resistance at 24,000 for Nifty 50 as a ceiling.",
        "Bearish forecast on Nifty 50 with resistance at 24,000 capping upside.",
    ],
)
def test_validate_record_rejects_resistance_bypass_patterns(body: str) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    record = ExternalPredictionRecord(
        source_id="icici_direct",
        symbol="NIFTY",
        horizon_days=14,
        target=ExternalPredictionTarget(mid=24000.0, high=24000.0),
        extraction={"instrument": "NIFTY50"},
    )
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "not_found"
    assert validated.error_message == "resistance_not_target"


def test_validate_record_rejects_sees_at_level_as_resistance() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    body = "Broker sees Nifty 50 at 24,000 as near-term resistance before the next leg up."
    record = ExternalPredictionRecord(
        source_id="icici_direct",
        symbol="NIFTY",
        horizon_days=14,
        target=ExternalPredictionTarget(mid=24000.0, high=24000.0),
        extraction={"instrument": "NIFTY50"},
    )
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "not_found"
    assert validated.error_message == "resistance_not_target"


def test_validate_record_keeps_explicit_weekly_outlook_target() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    record = ExternalPredictionRecord(
        source_id="economictimes",
        symbol="NIFTY",
        horizon_days=14,
        target=ExternalPredictionTarget(mid=24500.0, high=24500.0, low=24000.0),
        extraction={"instrument": "NIFTY50"},
        provenance={"title": "Nifty weekly outlook: 24,500 holds the key"},
    )
    body = (
        "Weekly Nifty 50 outlook: 24,500 holds the key to the next leg of gains; "
        "support at 23,800–24,000 remains intact."
    )
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "ok"
    assert validated.target.mid == 24500.0


def test_refresh_source_searxng_on_crawl_no_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionSource,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_source,
    )

    src = ExternalPredictionSource(
        id="livemint",
        display_name="Livemint",
        domains=["livemint.com"],
        curated_urls=["https://www.livemint.com/market"],
    )
    crawl_row = (
        "https://www.livemint.com/market",
        CrawlPageResult(
            url="https://www.livemint.com/market",
            success=True,
            markdown="Markets closed lower. Sector rotation continued across midcaps. " * 4,
            title="Markets",
        ),
    )
    fallback = ExternalPredictionRecord(
        source_id="livemint",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        target=ExternalPredictionTarget(mid=25000.0),
        provenance={
            "url": "https://www.livemint.com/example",
            "fetch_method": "searxng_text",
            "navigation_mode": "searxng_fallback",
            "searxng_trigger": "crawl_no_forecast",
        },
    )
    calls: list[str] = []

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.get_source",
        lambda _sid: src,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.try_fast_path_then_exploratory",
        lambda *_a, **_k: (None, [crawl_row], None),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.browse_enabled_for_source",
        lambda _src: False,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda *_a, **_k: None,
    )

    def _fallback(*_a, **_k):
        calls.append("fallback")
        return fallback

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.extract_via_searxng_fallback",
        _fallback,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.persist_refresh_result",
        lambda record, **_k: (record, record),
    )

    record = refresh_source(
        "livemint",
        symbol="NIFTY",
        horizon_days=14,
        spot=22365.0,
        crawl_group={"livemint": [crawl_row]},
    )
    assert calls == ["fallback"]
    assert record.provenance.get("searxng_trigger") == "crawl_no_forecast"


def test_record_from_crawl_group_records_searxng_attempt_when_fallback_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        _record_from_crawl_group,
    )

    src = ExternalPredictionSource(
        id="moneycontrol",
        display_name="Moneycontrol",
        domains=["moneycontrol.com"],
        curated_urls=["https://www.moneycontrol.com/news/business/markets/"],
    )
    blocked_row = (
        "https://www.moneycontrol.com/news/business/markets/",
        CrawlPageResult(
            url="https://www.moneycontrol.com/news/business/markets/",
            success=False,
            error_message="Blocked by anti-bot protection: Akamai block",
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.resolve_source_urls",
        lambda *_a, **_k: ["https://www.moneycontrol.com/news/business/markets/"],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.extract_via_searxng_fallback",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.persist_refresh_result",
        lambda record, **_k: (record, record),
    )

    record = _record_from_crawl_group(
        src,
        [blocked_row],
        symbol="NIFTY",
        horizon_days=14,
        spot_val=22365.0,
    )
    assert record.provenance.get("searxng_attempted") is True
    assert record.provenance.get("searxng_trigger") == "bot_all"
    assert "SearXNG fallback" in (record.error_message or "")


def test_resolve_source_urls_keeps_last_ok_article_when_allowed_url_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import url_policy
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        resolve_source_urls,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
    )

    article = (
        "https://economictimes.indiatimes.com/markets/stocks/news/"
        "nifty-weekly-outlook/articleshow/132492041.cms"
    )
    prior = ExternalPredictionRecord(
        source_id="economictimes",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        provenance={"url": article, "title": "Nifty weekly outlook"},
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher.load_source_prediction",
        lambda *_args, **_kwargs: prior,
    )

    def _deny_allowed(url: str, *, title: str = ""):
        return url_policy.UrlPolicyResult(allowed=False, reason="test_deny")

    def _allow_candidate(url: str, *, title: str = ""):
        return url_policy.UrlPolicyResult(allowed=True, reason="ok")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher.is_allowed_url",
        _deny_allowed,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher.is_candidate_article_url",
        _allow_candidate,
    )

    source = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        domains=["economictimes.indiatimes.com"],
        curated_urls=[],
        landing_urls=[],
    )
    urls = resolve_source_urls(source, horizon_days=14)
    assert article in urls


def test_refresh_source_preserves_searxng_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionRecord,
        ExternalPredictionSource,
        ExternalPredictionTarget,
    )
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_source,
    )

    src = ExternalPredictionSource(
        id="moneycontrol",
        display_name="Moneycontrol",
        domains=["moneycontrol.com"],
        curated_urls=["https://www.moneycontrol.com/news/business/markets/"],
    )
    fallback = ExternalPredictionRecord(
        source_id="moneycontrol",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        target=ExternalPredictionTarget(mid=24625.0),
        provenance={
            "url": "https://economictimes.indiatimes.com/markets/indices/nifty-50",
            "fetch_method": "searxng_text",
            "navigation_mode": "searxng_fallback",
        },
    )
    blocked_row = (
        "https://www.moneycontrol.com/news/business/markets/",
        CrawlPageResult(
            url="https://www.moneycontrol.com/news/business/markets/",
            success=False,
            error_message="Blocked by anti-bot protection: Akamai block",
        ),
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.get_source",
        lambda _sid: src,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.try_fast_path_then_exploratory",
        lambda *_a, **_k: (None, [blocked_row], None),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.browse_enabled_for_source",
        lambda _src: False,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.extract_via_searxng_fallback",
        lambda *_a, **_k: fallback,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.persist_refresh_result",
        lambda record, **_k: (record, record),
    )

    record = refresh_source(
        "moneycontrol",
        symbol="NIFTY",
        horizon_days=14,
        spot=22365.0,
        crawl_group={"moneycontrol": [blocked_row]},
    )
    assert record.provenance.get("navigation_mode") == "searxng_fallback"
    assert record.provenance.get("fetch_method") == "searxng_text"


def test_browse_skips_when_crawl_has_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_source,
    )

    src = ExternalPredictionSource(
        id="moneycontrol",
        display_name="Moneycontrol",
        domains=["moneycontrol.com"],
        entry_urls=["https://www.moneycontrol.com/markets"],
    )
    crawl_row = (
        "https://www.moneycontrol.com/news/nifty-outlook.html",
        CrawlPageResult(
            url="https://www.moneycontrol.com/news/nifty-outlook.html",
            success=True,
            title="Nifty weekly outlook",
            markdown="Nifty 50 target 26500 by expiry",
        ),
    )
    browse_calls: list[str] = []

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.get_source",
        lambda _sid: src,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.try_fast_path_then_exploratory",
        lambda *_a, **_k: (None, [crawl_row], None),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.browse_enabled_for_source",
        lambda _src: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda *_a, **_k: crawl_row,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.run_exploratory_browse",
        lambda *_a, **_k: browse_calls.append("browse") or None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._record_from_crawl_group",
        lambda *_a, **_k: type("Rec", (), {"fetch_status": "ok", "provenance": {"fetch_method": "crawl4ai"}})(),
    )

    refresh_source(
        "moneycontrol",
        symbol="NIFTY",
        horizon_days=14,
        crawl_group={"moneycontrol": [crawl_row]},
    )
    assert browse_calls == []


def test_pick_best_prefers_articleshow_over_listing() -> None:
    from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        pick_best_crawl_result,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )

    listing_md = (
        "Nifty 50 outlook remains positive. Resistance near the 24,000 mark. "
        "Support at 23,800. Target 23900 for the index this week. " * 4
    )
    article_md = (
        "Motilal Oswal sees Nifty 50 target at 25,200 by month end. "
        "The brokerage expects upside from earnings. Forecast outlook remains constructive. "
        "Nifty 50 target 25200. " * 3
    )
    rows = [
        (
            "https://economictimes.indiatimes.com/topic/nifty-50",
            CrawlPageResult(url="https://economictimes.indiatimes.com/topic/nifty-50", success=True, markdown=listing_md),
        ),
        (
            "https://economictimes.indiatimes.com/markets/stocks/news/article-123/articleshow/123.cms",
            CrawlPageResult(
                url="https://economictimes.indiatimes.com/markets/stocks/news/article-123/articleshow/123.cms",
                success=True,
                markdown=article_md,
            ),
        ),
    ]
    source = ExternalPredictionSource(
        id="motilal_oswal",
        display_name="Motilal Oswal",
        kind="broker",
        domains=["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    best = pick_best_crawl_result(rows, horizon_days=14, source=source)
    assert best is not None
    assert "articleshow" in best[0]
