"""Tests for external prediction discovery URLs and navigation paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.test_external_predictions import _patch_refresh_batch
from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
    NavigationStep,
    NavigationTrace,
)
from trade_integrations.dataflows.index_research.external_predictions.path_store import (
    approve_path,
    get_effective_path,
    mark_path_stale,
    save_auto_path,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    load_registry,
    save_registry,
    seed_registry_if_missing,
)


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_navigation_trace_round_trip() -> None:
    trace = NavigationTrace(
        steps=[NavigationStep(action="goto", url="https://example.com/article")],
        final_url="https://example.com/article",
        approved_by="auto",
        created_at="2026-07-23T12:00:00+00:00",
    )
    restored = NavigationTrace.from_dict(trace.to_dict())
    assert restored is not None
    assert restored.final_url.endswith("/article")
    assert restored.steps[0].action == "goto"


def test_save_and_get_effective_path(hub_dir: Path) -> None:
    seed_registry_if_missing()
    trace = save_auto_path(
        "moneycontrol",
        horizon_days=14,
        final_url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
    )
    assert trace is not None
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    effective = get_effective_path(src, horizon_days=14)
    assert effective is not None
    assert effective.final_url.endswith("123.html")


def test_mark_path_stale(hub_dir: Path) -> None:
    seed_registry_if_missing()
    save_auto_path(
        "moneycontrol",
        horizon_days=14,
        final_url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
    )
    mark_path_stale("moneycontrol", horizon_days=14)
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    assert get_effective_path(src, horizon_days=14) is None


def test_approve_path_promotes_saved(hub_dir: Path) -> None:
    seed_registry_if_missing()
    save_auto_path(
        "moneycontrol",
        horizon_days=14,
        final_url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
    )
    promoted = approve_path("moneycontrol", horizon_days=14)
    assert promoted is not None
    assert promoted.approved_by == "user"
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    assert src.approved_paths["14"].approved_by == "user"


def test_resolve_source_urls_merges_discovery() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        resolve_source_urls,
    )

    source = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        domains=["economictimes.indiatimes.com"],
        curated_urls=[],
        landing_urls=[],
    )
    discovery = [
        "https://economictimes.indiatimes.com/markets/stocks/news/nifty-50-target-26500/articleshow/123.cms",
    ]
    urls = resolve_source_urls(source, horizon_days=14, discovery_urls=discovery)
    assert discovery[0] in urls


def test_discover_sources_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
        discover_sources_parallel,
    )

    source = ExternalPredictionSource(
        id="test_src",
        display_name="Test Source",
        domains=["example.com"],
        search_queries=["Nifty target {horizon} days"],
    )

    def _fake_discover(src, *, horizon_days, pipeline=None):
        return [
            "https://example.com/markets/stocks/news/nifty-50-target-25000/articleshow/1.cms",
        ]

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.fetcher.discover_source_urls",
        _fake_discover,
    )
    out = discover_sources_parallel([source], horizon_days=14)
    assert out["test_src"][0].endswith("/1.cms")


def test_try_fast_path_fallback_on_replay_failure(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.navigation_paths import (
        try_fast_path_then_exploratory,
    )

    seed_registry_if_missing()
    save_auto_path(
        "moneycontrol",
        horizon_days=14,
        final_url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
    )
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    exploratory = [
        (
            "https://www.moneycontrol.com/news/nifty-target-24800-456.html",
            CrawlPageResult(url="https://www.moneycontrol.com/news/nifty-target-24800-456.html", success=True),
        )
    ]

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.navigation_paths.replay_navigation_path",
        lambda *args, **kwargs: MagicMock(success=False, error_message="replay_crawl_failed"),
    )

    replay, rows, backup = try_fast_path_then_exploratory(
        src,
        horizon_days=14,
        exploratory_rows=exploratory,
    )
    assert replay is not None
    assert replay.success is False
    assert rows == exploratory
    assert backup == exploratory
    stale_src = next(s for s in load_registry() if s.id == "moneycontrol")
    assert get_effective_path(stale_src, horizon_days=14) is None


def test_try_fast_path_forwards_replay_metadata(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.navigation_paths import (
        ReplayResult,
        try_fast_path_then_exploratory,
    )

    seed_registry_if_missing()
    save_auto_path(
        "moneycontrol",
        horizon_days=14,
        final_url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
    )
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    exploratory = [
        (
            "https://www.moneycontrol.com/news/nifty-target-24800-456.html",
            CrawlPageResult(url="https://www.moneycontrol.com/news/nifty-target-24800-456.html", success=True),
        )
    ]
    replay = ReplayResult(
        success=True,
        url="https://www.moneycontrol.com/news/nifty-target-25000-123.html",
        title="Nifty target",
        markdown="Nifty 50 target 25000",
        elapsed_ms=42,
        metadata={"screenshot_b64": "abc123"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.navigation_paths.replay_navigation_path",
        lambda *args, **kwargs: replay,
    )

    _, rows, backup = try_fast_path_then_exploratory(
        src,
        horizon_days=14,
        exploratory_rows=exploratory,
    )
    assert len(rows) == 1
    _, crawl = rows[0]
    assert crawl.metadata.get("screenshot_b64") == "abc123"
    assert backup == exploratory


def test_refresh_wires_parallel_discovery(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_all_external_predictions,
    )

    seed_registry_if_missing()
    discovery_calls: list[dict] = []

    def _fake_discover(sources, *, horizon_days, pipeline=None):
        discovery_calls.append({"count": len(sources), "horizon_days": horizon_days})
        return {src.id: [] for src in sources}

    _patch_refresh_batch(monkeypatch)
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.discover_sources_parallel",
        _fake_discover,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.refresh_source",
        lambda source_id, **kwargs: ExternalPredictionRecord(
            source_id=source_id,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=25000.0),
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._internal_forecast",
        lambda *_a, **_k: None,
    )

    refresh_all_external_predictions(symbol="NIFTY", horizon_days=14, min_interval_sec=0)
    assert discovery_calls
    assert discovery_calls[0]["horizon_days"] == 14


def test_mark_path_stale_symmetric_after_approve(hub_dir: Path) -> None:
    seed_registry_if_missing()
    url = "https://www.moneycontrol.com/news/nifty-target-25000-123.html"
    save_auto_path("moneycontrol", horizon_days=14, final_url=url)
    approve_path("moneycontrol", horizon_days=14)
    mark_path_stale("moneycontrol", horizon_days=14)
    src = next(s for s in load_registry() if s.id == "moneycontrol")
    assert src.approved_paths["14"].stale is True
    assert src.saved_paths["14"].stale is True
    assert get_effective_path(src, horizon_days=14) is None


def test_replay_allows_saved_article_url_without_title_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
        NavigationTrace,
    )
    from trade_integrations.dataflows.index_research.external_predictions.navigation_paths import (
        replay_navigation_path,
    )

    source = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        domains=["economictimes.indiatimes.com"],
    )
    trace = NavigationTrace(
        final_url=(
            "https://economictimes.indiatimes.com/markets/stocks/news/"
            "nifty-50-target-26500/articleshow/123.cms"
        ),
    )

    def _fake_crawl(urls, pipeline=None):
        return [
            CrawlPageResult(
                url=urls[0],
                success=True,
                markdown="Nifty 50 target raised to 26,500 on strong flows.",
            )
        ]

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.navigation_paths.crawl_urls_parallel_sync",
        _fake_crawl,
    )
    result = replay_navigation_path(trace, source=source)
    assert result.success is True
    assert result.url.endswith("/articleshow/123.cms")
