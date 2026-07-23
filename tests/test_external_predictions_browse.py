"""Unit tests for external predictions exploratory browse agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
from trade_integrations.dataflows.index_research.external_predictions.browse_agent import (
    MAX_BROWSE_STEPS,
    BrowseResult,
    browse_enabled_for_source,
    browse_result_to_crawl_row,
    has_browse_entry_urls,
    resolve_browse_entry_urls,
    run_exploratory_browse,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
    NavigationStep,
    NavigationTrace,
)
from trade_integrations.dataflows.index_research.external_predictions.path_store import (
    get_effective_path,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    load_registry,
    seed_registry_if_missing,
)

_LISTING_MD = """
# Markets
Browse NIFTY 50 news and analysis.
[Nifty 50 target raised to 26,500](https://example.com/markets/stocks/news/nifty-50-target-26500/articleshow/99.cms)
"""

_FORECAST_MD = """
Nifty 50 index outlook remains bullish.
Analysts target Nifty 50 at 26,500 by year-end on strong FII flows.
"""


def _browse_source(**overrides) -> ExternalPredictionSource:
    base = dict(
        id="user_broker",
        display_name="User Broker",
        kind="broker",
        domains=["example.com"],
        entry_urls=["https://example.com/markets"],
    )
    base.update(overrides)
    return ExternalPredictionSource(**base)


def test_has_browse_entry_urls() -> None:
    assert has_browse_entry_urls(_browse_source()) is True
    assert has_browse_entry_urls(_browse_source(entry_urls=[])) is False


def test_resolve_browse_entry_urls_formats_horizon() -> None:
    source = _browse_source(entry_urls=["https://example.com/markets/{horizon}d"])
    urls = resolve_browse_entry_urls(source, horizon_days=14)
    assert urls == ["https://example.com/markets/14d"]


def test_run_exploratory_browse_stops_on_forecast_page() -> None:
    source = _browse_source()
    calls: list[str] = []

    def _crawl(url: str, score_links: bool) -> CrawlPageResult:
        calls.append(url)
        if url.endswith("/markets"):
            return CrawlPageResult(
                url=url,
                success=True,
                title="Markets",
                markdown=_LISTING_MD,
                metadata={
                    "links": [
                        {
                            "href": "https://example.com/markets/stocks/news/nifty-50-target-26500/articleshow/99.cms",
                            "text": "Nifty 50 target raised",
                        }
                    ]
                },
            )
        return CrawlPageResult(
            url=url,
            success=True,
            title="Nifty target",
            markdown=_FORECAST_MD,
            metadata={"screenshot_b64": "abc123"},
        )

    result = run_exploratory_browse(
        source,
        horizon_days=14,
        crawl_one=_crawl,
    )
    assert result.success is True
    assert result.steps_taken == 2
    assert len(calls) == 2
    assert result.url.endswith("/articleshow/99.cms")
    assert result.trace.final_url == result.url
    assert result.trace.steps[0].action == "goto"
    assert result.trace.steps[1].action == "click"
    assert result.metadata.get("screenshot_b64") == "abc123"


def test_run_exploratory_browse_respects_max_steps() -> None:
    source = _browse_source()
    step = {"n": 0}

    def _crawl(url: str, score_links: bool) -> CrawlPageResult:
        step["n"] += 1
        n = step["n"]
        next_url = f"https://example.com/markets/page/{n + 1}"
        return CrawlPageResult(
            url=url,
            success=True,
            title=f"Page {n}",
            markdown=f"Markets listing page {n}\n[Next]({next_url})",
            metadata={"links": [{"href": next_url, "text": "Next"}]},
        )

    result = run_exploratory_browse(
        source,
        horizon_days=14,
        crawl_one=_crawl,
        max_steps=MAX_BROWSE_STEPS,
    )
    assert result.steps_taken == MAX_BROWSE_STEPS
    assert result.success is False
    assert result.error_message == "browse_no_forecast"
    assert len(result.trace.steps) == MAX_BROWSE_STEPS


def test_run_exploratory_browse_no_entry_urls() -> None:
    source = _browse_source(entry_urls=[])

    def _crawl(url: str, score_links: bool) -> CrawlPageResult:
        raise AssertionError("crawl should not run without entry URLs")

    result = run_exploratory_browse(source, horizon_days=14, crawl_one=_crawl)
    assert result.success is False
    assert result.error_message == "no_entry_urls"


def test_browse_result_to_crawl_row() -> None:
    browse = BrowseResult(
        success=True,
        trace=NavigationTrace(
            steps=[NavigationStep(action="goto", url="https://example.com/a")],
            final_url="https://example.com/a",
        ),
        url="https://example.com/a",
        title="Article",
        markdown=_FORECAST_MD,
        metadata={"screenshot_b64": "shot"},
    )
    url, crawl = browse_result_to_crawl_row(browse)
    assert url == "https://example.com/a"
    assert crawl.success is True
    assert crawl.markdown == _FORECAST_MD
    assert crawl.metadata["screenshot_b64"] == "shot"


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_refresh_source_uses_browse_when_entry_urls_exist(
    hub_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_source,
    )

    seed_registry_if_missing()
    registry = load_registry()
    src = next(s for s in registry if s.id == "moneycontrol")
    src.entry_urls = ["https://www.moneycontrol.com/markets"]
    from trade_integrations.dataflows.index_research.external_predictions import source_registry

    source_registry.save_registry(registry)

    browse_url = "https://www.moneycontrol.com/news/nifty-browse.html"
    browse_trace = NavigationTrace(
        steps=[
            NavigationStep(action="goto", url="https://www.moneycontrol.com/markets"),
            NavigationStep(action="click", url=browse_url),
        ],
        final_url=browse_url,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.run_exploratory_browse",
        lambda *args, **kwargs: BrowseResult(
            success=True,
            trace=browse_trace,
            url=browse_url,
            title="Browse article",
            markdown=_FORECAST_MD,
            metadata={"screenshot_b64": "browse-shot"},
            steps_taken=2,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.try_fast_path_then_exploratory",
        lambda source, *, horizon_days, exploratory_rows, pipeline=None: (
            None,
            exploratory_rows,
            exploratory_rows,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.resolve_source_urls",
        lambda *args, **kwargs: ["https://www.moneycontrol.com/markets"],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.filter_markdown_for_extraction",
        lambda markdown, *args, **kwargs: markdown,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.extract_forecast",
        lambda **kwargs: ExternalPredictionRecord(
            source_id=kwargs["source"].id,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=26500.0),
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda rows, *args, **kwargs: rows[0] if rows else None,
    )

    record = refresh_source(
        src.id,
        symbol="NIFTY",
        horizon_days=14,
        crawl_group={src.id: []},
    )
    assert record.fetch_status == "ok"
    assert record.provenance.get("fetch_method") == "browse_agent"
    assert record.provenance.get("navigation_mode") == "exploratory"
    updated = next(s for s in load_registry() if s.id == "moneycontrol")
    effective = get_effective_path(updated, horizon_days=14)
    assert effective is not None
    assert effective.final_url == browse_url
    assert len(effective.steps) == 2


def test_browse_disabled_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_PREDICTIONS_BROWSE_DISABLED", "1")
    assert browse_enabled_for_source(_browse_source()) is False


def test_approve_path_after_exploratory_refresh(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.path_store import (
        approve_path,
    )
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_source,
    )

    seed_registry_if_missing()
    registry = load_registry()
    src = next(s for s in registry if s.id == "moneycontrol")
    src.entry_urls = ["https://www.moneycontrol.com/markets"]
    from trade_integrations.dataflows.index_research.external_predictions import source_registry

    source_registry.save_registry(registry)

    browse_url = "https://www.moneycontrol.com/news/nifty-browse.html"
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.run_exploratory_browse",
        lambda *args, **kwargs: BrowseResult(
            success=True,
            trace=NavigationTrace(
                steps=[NavigationStep(action="goto", url=browse_url)],
                final_url=browse_url,
                approved_by="auto",
            ),
            url=browse_url,
            title="Browse article",
            markdown=_FORECAST_MD,
            steps_taken=1,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.try_fast_path_then_exploratory",
        lambda source, *, horizon_days, exploratory_rows, pipeline=None: (
            None,
            exploratory_rows,
            exploratory_rows,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.filter_markdown_for_extraction",
        lambda markdown, *args, **kwargs: markdown,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.extract_forecast",
        lambda **kwargs: ExternalPredictionRecord(
            source_id=kwargs["source"].id,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=26500.0),
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.pick_best_crawl_result",
        lambda rows, *args, **kwargs: rows[0] if rows else None,
    )

    record = refresh_source(src.id, symbol="NIFTY", horizon_days=14, crawl_group={src.id: []})
    assert record.fetch_status == "ok"

    before = get_effective_path(next(s for s in load_registry() if s.id == "moneycontrol"), horizon_days=14)
    assert before is not None
    assert before.approved_by == "auto"

    promoted = approve_path("moneycontrol", horizon_days=14)
    assert promoted is not None
    assert promoted.approved_by == "user"

    after = get_effective_path(next(s for s in load_registry() if s.id == "moneycontrol"), horizon_days=14)
    assert after is not None
    assert after.approved_by == "user"
