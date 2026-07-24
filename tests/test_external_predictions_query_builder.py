"""Tests for horizon-dated external prediction query builder and batch URL dedup."""

from trade_integrations.dataflows.index_research.external_predictions.batch_url_dedup import (
    BatchUrlRegistry,
    assign_discovery_url_owners,
    dedup_discovery_for_batch,
    dedupe_crawl_article_jobs,
)
from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    attribution_score,
    discovery_allowed_domains,
    has_stronger_attribution,
    is_discovery_redundant_domain,
)
from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
    SearxngDiscoveryResult,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.query_builder import (
    build_horizon_context,
    build_horizon_queries,
    expand_query_template,
    primary_domain,
)


def _broker_source(source_id: str, display_name: str, domains: list[str]) -> ExternalPredictionSource:
    return ExternalPredictionSource(
        id=source_id,
        display_name=display_name,
        kind="broker",
        domains=domains,
        search_queries=[
            '"{source_name}" Nifty 50 target forecast {today} {horizon_end}',
        ],
    )


def test_build_horizon_context_expands_calendar_tokens() -> None:
    trading_dates = [f"2026-07-{day:02d}" for day in range(1, 32)] + [f"2026-08-{day:02d}" for day in range(1, 15)]
    ctx = build_horizon_context(
        horizon_days=14,
        as_of_date="2026-07-23",
        trading_dates=trading_dates,
    )
    assert ctx["today"] == "2026-07-23"
    assert ctx["week_start"] == "2026-07-20"
    assert ctx["week_end"] == "2026-07-24"
    assert ctx["month_year"] == "July 2026"
    assert ctx["horizon_end"] == "2026-08-06"


def test_build_horizon_queries_use_open_attribution_not_site_operators() -> None:
    trading_dates = [f"2026-07-{day:02d}" for day in range(1, 32)] + [f"2026-08-{day:02d}" for day in range(1, 15)]
    source = _broker_source(
        "motilal_oswal",
        "Motilal Oswal",
        ["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    queries = build_horizon_queries(
        source,
        horizon_days=14,
        as_of_date="2026-07-23",
        trading_dates=trading_dates,
    )
    assert any("2026-08-06" in q for q in queries), "horizon_end should appear in queries"
    assert any("2026-07-23" in q for q in queries), "today should appear in queries"
    assert any("Motilal Oswal" in q for q in queries)
    assert not any("site:economictimes.indiatimes.com" in q for q in queries)
    assert not any("site:motilaloswal.com" in q for q in queries)
    assert len(queries) <= 5


def test_primary_domain_prefers_native_over_syndication() -> None:
    source = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    assert primary_domain(source) == "icicidirect.com"


def test_media_source_treats_own_domain_as_native() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
        native_domains,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )

    et = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        kind="media",
        domains=["economictimes.indiatimes.com", "economictimes.com"],
    )
    assert "economictimes.indiatimes.com" in native_domains(et)
    url = "https://economictimes.indiatimes.com/markets/stocks/news/article-1"
    assert has_stronger_attribution(url, source=et, title="Nifty outlook")


def test_expand_query_template_replaces_source_name() -> None:
    source = _broker_source("hdfc_securities", "HDFC Securities", ["hdfcsec.com"])
    ctx = build_horizon_context(horizon_days=14, as_of_date="2026-07-23", trading_dates=[])
    query = expand_query_template(
        '"{source_name}" Nifty 50 target forecast {today} {horizon_end}',
        context=ctx,
        source=source,
    )
    assert "HDFC Securities" in query
    assert "2026-07-23" in query
    assert ctx["horizon_end"] != ctx["today"]
    assert ctx["horizon_end_approx"] == "1"


def test_build_horizon_context_uses_calendar_fallback_when_calendar_missing() -> None:
    ctx = build_horizon_context(horizon_days=14, as_of_date="2026-07-23", trading_dates=[])
    assert ctx["today"] == "2026-07-23"
    assert ctx["horizon_end"] == "2026-08-12"
    assert ctx["horizon_end_approx"] == "1"


def test_build_fallback_queries_include_site_for_media() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )
    from trade_integrations.dataflows.index_research.external_predictions.query_builder import (
        build_fallback_queries,
    )

    livemint = ExternalPredictionSource(
        id="livemint",
        display_name="Livemint",
        kind="media",
        domains=["livemint.com"],
    )
    queries = build_fallback_queries(livemint, horizon_days=14, as_of_date="2026-07-23", trading_dates=[])
    assert any("site:livemint.com" in q for q in queries)


def test_build_fallback_queries_broker_broader_attribution() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.query_builder import (
        build_fallback_queries,
    )

    source = _broker_source("motilal_oswal", "Motilal Oswal", ["motilaloswal.com", "economictimes.indiatimes.com"])
    queries = build_fallback_queries(source, horizon_days=14, as_of_date="2026-07-23", trading_dates=[])
    assert any("Motilal Oswal" in q for q in queries)
    assert not any("site:" in q for q in queries)


def test_batch_url_registry_skips_second_source_on_same_url() -> None:
    registry = BatchUrlRegistry()
    url = "https://economictimes.indiatimes.com/topic/nifty-50"
    registry.claim(url, "motilal_oswal")
    assert registry.is_claimed_by_other(url, "icici_direct")
    assert not registry.is_claimed_by_other(url, "motilal_oswal")


def test_dedup_discovery_for_batch_filters_claimed_urls() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/article-1"
    motilal = _broker_source("motilal_oswal", "Motilal Oswal", ["motilaloswal.com"])
    icici = _broker_source("icici_direct", "ICICI Direct", ["icicidirect.com"])
    registry = BatchUrlRegistry()
    registry.claim(url, "motilal_oswal")
    discovery = {
        "motilal_oswal": SearxngDiscoveryResult(urls=[url]),
        "icici_direct": SearxngDiscoveryResult(urls=[url, "https://www.icicidirect.com/research/equity"]),
    }
    out = dedup_discovery_for_batch(discovery, [motilal, icici], registry)
    assert out["motilal_oswal"].urls == [url]
    assert url not in out["icici_direct"].urls
    assert "https://www.icicidirect.com/research/equity" in out["icici_direct"].urls


def test_attribution_assigns_syndication_url_to_named_broker() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/nifty-outlook"
    motilal = _broker_source(
        "motilal_oswal",
        "Motilal Oswal",
        ["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    icici = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    discovery = {
        "motilal_oswal": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Motilal Oswal sees Nifty at 26500"}],
        ),
        "icici_direct": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Nifty 50 outlook for next month"}],
        ),
    }
    owners = assign_discovery_url_owners(discovery, [motilal, icici])
    key = "economictimes.indiatimes.com/markets/stocks/news/nifty-outlook"
    assert owners[key] == "motilal_oswal"

    out = dedup_discovery_for_batch(discovery, [motilal, icici], BatchUrlRegistry())
    assert url in out["motilal_oswal"].urls
    assert url not in out["icici_direct"].urls


def test_has_stronger_attribution_requires_name_or_native_domain() -> None:
    url = "https://economictimes.indiatimes.com/topic/nifty-50"
    icici = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    assert not has_stronger_attribution(url, source=icici, title="Nifty 50 weekly view")
    assert has_stronger_attribution(
        url,
        source=icici,
        title="ICICI Direct Nifty 50 target",
    )
    assert attribution_score(icici, url, title="Nifty 50 weekly view") < attribution_score(
        icici,
        url,
        title="ICICI Direct Nifty 50 target",
    )


def test_global_bank_topic_url_has_strong_attribution() -> None:
    url = "https://economictimes.indiatimes.com/topic/goldman-sachs-nifty"
    goldman = ExternalPredictionSource(
        id="goldman_sachs",
        display_name="Goldman Sachs",
        kind="global_bank",
        domains=["economictimes.indiatimes.com", "livemint.com"],
    )
    assert has_stronger_attribution(url, source=goldman, title="Nifty outlook")
    assert has_stronger_attribution(
        url,
        source=goldman,
        title="Goldman sees Nifty at 26000",
    )


def test_filter_crawl_rows_drops_all_claimed_without_attribution() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/article-1"
    motilal = _broker_source("motilal_oswal", "Motilal Oswal", ["motilaloswal.com"])
    icici = _broker_source("icici_direct", "ICICI Direct", ["icicidirect.com"])
    registry = BatchUrlRegistry()
    registry.claim(url, "motilal_oswal")
    rows = [(url, type("Row", (), {"title": "Nifty weekly view", "success": True})())]
    filtered = registry.filter_crawl_rows(rows, source=icici)
    assert filtered == []


def test_dedup_discovery_filters_hits_for_non_owner() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/nifty-outlook"
    motilal = _broker_source(
        "motilal_oswal",
        "Motilal Oswal",
        ["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    icici = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    discovery = {
        "motilal_oswal": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Motilal Oswal sees Nifty at 26500"}],
        ),
        "icici_direct": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Nifty 50 outlook for next month"}],
        ),
    }
    out = dedup_discovery_for_batch(discovery, [motilal, icici], BatchUrlRegistry())
    assert any(h.get("url") == url for h in out["motilal_oswal"].hits)
    assert not any(h.get("url") == url for h in out["icici_direct"].hits)


def test_discovery_allowed_domains_includes_broker_native_hosts() -> None:
    motilal = _broker_source("motilal_oswal", "Motilal Oswal", ["motilaloswal.com", "economictimes.indiatimes.com"])
    allowed = discovery_allowed_domains([motilal], trusted_domains=("moneycontrol.com",))
    assert "motilaloswal.com" in allowed
    assert "moneycontrol.com" in allowed


def test_is_discovery_redundant_skips_known_syndication() -> None:
    motilal = _broker_source("motilal_oswal", "Motilal Oswal", ["motilaloswal.com", "economictimes.indiatimes.com"])
    assert is_discovery_redundant_domain("economictimes.indiatimes.com", [motilal])
    assert not is_discovery_redundant_domain("motilaloswal.com", [motilal])


def test_exclusive_ownership_requires_strong_attribution() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/generic-nifty"
    motilal = _broker_source(
        "motilal_oswal",
        "Motilal Oswal",
        ["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    icici = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    discovery = {
        "motilal_oswal": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Nifty 50 weekly view"}],
        ),
        "icici_direct": SearxngDiscoveryResult(
            urls=[url],
            hits=[{"url": url, "title": "Nifty 50 outlook update"}],
        ),
    }
    owners = assign_discovery_url_owners(discovery, [motilal, icici])
    key = "economictimes.indiatimes.com/markets/stocks/news/generic-nifty"
    assert key not in owners


def test_dedupe_crawl_article_jobs_respects_discovery_owner() -> None:
    url = "https://economictimes.indiatimes.com/markets/stocks/news/nifty-outlook"
    motilal = _broker_source(
        "motilal_oswal",
        "Motilal Oswal",
        ["motilaloswal.com", "economictimes.indiatimes.com"],
    )
    icici = _broker_source(
        "icici_direct",
        "ICICI Direct",
        ["icicidirect.com", "economictimes.indiatimes.com"],
    )
    owners = {
        "economictimes.indiatimes.com/markets/stocks/news/nifty-outlook": "motilal_oswal",
    }
    jobs = dedupe_crawl_article_jobs(
        [
            ("icici_direct", url),
            ("motilal_oswal", url),
        ],
        [motilal, icici],
        attribution_owners=owners,
    )
    assert jobs == [("motilal_oswal", url)]
