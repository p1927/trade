"""Tests for external prediction models, registry, and store."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSnapshot,
    ExternalPredictionSource,
    ExternalPredictionTarget,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    add_source_to_watchlist,
    load_registry,
    remove_source_from_watchlist,
    save_registry,
    seed_registry_if_missing,
)
from trade_integrations.dataflows.index_research.external_predictions.store import (
    load_snapshot,
    rebuild_snapshot,
    upsert_prediction,
)


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _patch_refresh_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid network discovery/crawl during refresh batch tests."""
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.discover_sources_parallel",
        lambda sources, **kwargs: {src.id: [] for src in sources},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.crawl_sources_parallel",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context.build_and_save_expert_context",
        lambda **_k: {},
    )


def _ensure_vibetrading_agent_on_path() -> None:
    agent_dir = Path(__file__).resolve().parent.parent / "vibetrading" / "agent"
    agent_path = str(agent_dir)
    if agent_path not in sys.path:
        sys.path.insert(0, agent_path)


def test_target_round_trip() -> None:
    target = ExternalPredictionTarget(low=24000.0, mid=25500.0, high=26200.0)
    restored = ExternalPredictionTarget.from_dict(target.to_dict())
    assert restored.low == 24000.0
    assert restored.mid == 25500.0
    assert restored.high == 26200.0


def test_record_round_trip() -> None:
    record = ExternalPredictionRecord(
        source_id="goldman_sachs",
        horizon_days=30,
        as_of="2026-07-20",
        spot_at_fetch=24100.0,
        target=ExternalPredictionTarget(low=24800.0, mid=25500.0, high=26200.0),
        direction="bullish",
        expected_return_pct=5.8,
        rationale_bullets=["RBI cuts support risk assets", "FII flows turning positive"],
        confidence="high",
        provenance={"url": "https://example.com", "title": "GS Outlook"},
        fetch_status="ok",
    )
    restored = ExternalPredictionRecord.from_dict(record.to_dict())
    assert restored is not None
    assert restored.source_id == "goldman_sachs"
    assert restored.target.mid == 25500.0
    assert len(restored.rationale_bullets) == 2
    assert restored.fetch_status == "ok"


def test_seed_registry_writes_file(hub_dir: Path) -> None:
    sources = seed_registry_if_missing()
    assert len(sources) >= 8
    registry_file = hub_dir / "NIFTY" / "external_predictions" / "source_registry.json"
    assert registry_file.is_file()
    payload = json.loads(registry_file.read_text(encoding="utf-8"))
    assert isinstance(payload.get("sources"), list)


def test_watchlist_add_and_remove(hub_dir: Path) -> None:
    seed_registry_if_missing()
    added = add_source_to_watchlist(
        display_name="Trendlyne Research",
        domains=["trendlyne.com"],
        kind="broker",
    )
    assert added.watchlisted is True
    assert added.removable is True
    registry = load_registry()
    assert any(s.id == added.id for s in registry)

    assert remove_source_from_watchlist(added.id) is True
    registry = load_registry()
    entry = next(s for s in registry if s.id == added.id)
    assert entry.watchlisted is False

    assert remove_source_from_watchlist("moneycontrol") is False


def test_snapshot_empty_and_upsert(hub_dir: Path) -> None:
    seed_registry_if_missing()
    snapshot = load_snapshot(symbol="NIFTY", horizon_days=14)
    assert snapshot.symbol == "NIFTY"
    assert snapshot.horizon_days == 14
    assert snapshot.is_stale is True

    record = ExternalPredictionRecord(
        source_id="moneycontrol",
        horizon_days=14,
        as_of="2026-07-20",
        spot_at_fetch=24100.0,
        target=ExternalPredictionTarget(mid=25000.0, low=24500.0, high=25500.0),
        fetch_status="ok",
    )
    upsert_prediction(record)
    rebuilt = rebuild_snapshot(symbol="NIFTY", horizon_days=14, fetched_at="2026-07-20T12:00:00+00:00")
    assert isinstance(rebuilt, ExternalPredictionSnapshot)
    mc = next(p for p in rebuilt.predictions if p.source_id == "moneycontrol")
    assert mc.fetch_status == "ok"
    assert mc.target.mid == 25000.0


def test_refresh_all_writes_snapshot(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_all_external_predictions,
    )
    from trade_integrations.dataflows.index_research.external_predictions.store import (
        load_snapshot,
    )

    seed_registry_if_missing()

    _patch_refresh_batch(monkeypatch)

    def _fake_refresh_source(source_id: str, **kwargs: object) -> ExternalPredictionRecord:
        record = ExternalPredictionRecord(
            source_id=source_id,
            horizon_days=int(kwargs.get("horizon_days") or 14),
            as_of="2026-07-20",
            spot_at_fetch=24000.0,
            target=ExternalPredictionTarget(mid=25000.0, low=24500.0, high=25500.0),
            fetch_status="ok",
        )
        upsert_prediction(record, symbol=str(kwargs.get("symbol") or "NIFTY"))
        return record

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.refresh_source",
        _fake_refresh_source,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._internal_forecast",
        lambda *_a, **_k: None,
    )

    snap = refresh_all_external_predictions(symbol="NIFTY", horizon_days=14, min_interval_sec=0)
    assert snap.fetched_at
    assert any(p.fetch_status == "ok" for p in snap.predictions)
    cached = load_snapshot(symbol="NIFTY", horizon_days=14)
    assert cached.fetched_at == snap.fetched_at


def test_refresh_emits_pipeline_logs(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_all_external_predictions,
    )
    from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

    seed_registry_if_missing()

    _patch_refresh_batch(monkeypatch)

    def _fake_refresh_source(source_id: str, **kwargs: object) -> ExternalPredictionRecord:
        pipeline = kwargs.get("pipeline")
        if isinstance(pipeline, PipelineLogger):
            pipeline.info("source", f"mock refresh {source_id}", source_id=source_id)
        record = ExternalPredictionRecord(
            source_id=source_id,
            horizon_days=14,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=25000.0),
        )
        upsert_prediction(record)
        return record

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.refresh_source",
        _fake_refresh_source,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._internal_forecast",
        lambda *_a, **_k: None,
    )

    pipeline = PipelineLogger()
    refresh_all_external_predictions(
        symbol="NIFTY",
        horizon_days=14,
        min_interval_sec=0,
        pipeline=pipeline,
    )
    stages = {entry.stage for entry in pipeline.entries}
    assert "refresh" in stages
    assert "source" in stages
    assert any("complete" in entry.message.lower() for entry in pipeline.entries)


def test_refresh_on_source_complete_callback(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_all_external_predictions,
    )

    seed_registry_if_missing()
    completed: list[str] = []

    _patch_refresh_batch(monkeypatch)

    def _fake_refresh_source(source_id: str, **kwargs: object) -> ExternalPredictionRecord:
        record = ExternalPredictionRecord(
            source_id=source_id,
            horizon_days=14,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=25000.0),
        )
        upsert_prediction(record)
        return record

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh.refresh_source",
        _fake_refresh_source,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._fetch_spot",
        lambda _sym, pipeline=None: 24000.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.refresh._internal_forecast",
        lambda *_a, **_k: None,
    )

    def on_complete(source_id: str, _record, partial) -> None:
        completed.append(source_id)
        assert partial.fetched_at

    refresh_all_external_predictions(
        symbol="NIFTY",
        horizon_days=14,
        min_interval_sec=0,
        on_source_complete=on_complete,
    )
    assert len(completed) >= 1


def test_append_source_complete_writes_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_vibetrading_agent_on_path()
    jobs_root = tmp_path / "log" / "external_predictions_jobs"
    jobs_root.mkdir(parents=True)
    monkeypatch.setattr(
        "src.trade.external_predictions_run_jobs._jobs_root",
        lambda: jobs_root,
    )
    from src.trade import external_predictions_run_jobs as jobs_mod
    from src.trade.external_predictions_run_jobs import append_source_complete, get_job, start_job

    jobs_mod.EXTERNAL_PREDICTIONS_RUN_JOBS.clear()
    jobs_mod._ACTIVE_BY_SCOPE.clear()
    job_id, _ = start_job(ticker="NIFTY", horizon_days=14)
    append_source_complete(
        job_id,
        source_id="moneycontrol",
        record={"source_id": "moneycontrol", "fetch_status": "ok"},
        partial_snapshot={"symbol": "NIFTY", "horizon_days": 14, "fetched_at": "2026-07-23T12:00:00+00:00"},
    )
    job = get_job(job_id)
    assert job is not None
    assert job.get("partial_snapshot") is not None
    assert any(log.get("stage") == "source_complete" for log in job.get("logs") or [])


def test_save_snapshot_serializes_internal_forecast_datetime(hub_dir: Path) -> None:
    from datetime import datetime, timezone

    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSnapshot,
    )
    from trade_integrations.dataflows.index_research.external_predictions.store import (
        save_snapshot,
        snapshot_path,
    )

    snap = ExternalPredictionSnapshot(
        symbol="NIFTY",
        horizon_days=14,
        fetched_at="2026-07-20T12:00:00+00:00",
        internal_forecast={
            "as_of": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "direction": "bullish",
        },
    )
    save_snapshot(snap)
    payload = json.loads(snapshot_path("NIFTY", 14).read_text(encoding="utf-8"))
    assert isinstance(payload["internal_forecast"]["as_of"], str)
    assert "2026-07-20" in payload["internal_forecast"]["as_of"]


def test_external_predictions_job_start_reuses_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_vibetrading_agent_on_path()
    jobs_root = tmp_path / "log" / "external_predictions_jobs"
    jobs_root.mkdir(parents=True)

    monkeypatch.setattr(
        "src.trade.external_predictions_run_jobs._jobs_root",
        lambda: jobs_root,
    )
    monkeypatch.setattr(
        "src.trade.external_predictions_run_jobs.spawn_worker",
        lambda _job_id: None,
    )

    from src.trade import external_predictions_run_jobs as jobs_mod
    from src.trade.external_predictions_run_jobs import get_active_job, job_id_valid, start_job

    jobs_mod.EXTERNAL_PREDICTIONS_RUN_JOBS.clear()
    jobs_mod._ACTIVE_BY_SCOPE.clear()

    job_id_1, reused_1 = start_job(ticker="NIFTY", horizon_days=14)
    assert reused_1 is False
    assert job_id_valid(job_id_1)

    job_id_2, reused_2 = start_job(ticker="NIFTY", horizon_days=14)
    assert reused_2 is True
    assert job_id_2 == job_id_1

    active = get_active_job("NIFTY", horizon_days=14)
    assert active is not None
    assert active["job_id"] == job_id_1
    assert active["status"] in ("queued", "running")

    job_id_3, reused_3 = start_job(ticker="NIFTY", horizon_days=30)
    assert reused_3 is False
    assert job_id_3 != job_id_1


def test_url_policy_rejects_careers_and_options() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
        is_allowed_listing_url,
        is_allowed_url,
    )

    assert is_allowed_listing_url("https://www.goldmansachs.com/careers").allowed is False
    assert is_allowed_url("https://example.com/market/derivatives/nifty-50-target").allowed is False
    assert is_allowed_url(
        "https://economictimes.indiatimes.com/markets/stocks/news/goldman-sachs-pegs-nifty-target-at-26500/articleshow/132357525.cms",
        title="Goldman Sachs pegs Nifty target at 26,500",
    ).allowed is True


def test_url_policy_rejects_careers_and_options() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
        is_allowed_listing_url,
        is_allowed_url,
        is_candidate_article_url,
        link_score,
    )

    assert is_allowed_listing_url("https://www.goldmansachs.com/careers").allowed is False
    assert is_allowed_url("https://example.com/market/derivatives/nifty-50-target").allowed is False
    assert is_allowed_url(
        "https://economictimes.indiatimes.com/markets/stocks/news/goldman-sachs-pegs-nifty-target-at-26500/articleshow/132357525.cms",
        title="Goldman Sachs pegs Nifty target at 26,500",
    ).allowed is True

    generic_url = (
        "https://economictimes.indiatimes.com/markets/stocks/news/"
        "markets-rally-on-fii-flows/articleshow/999.cms"
    )
    assert is_allowed_url(generic_url, title="Markets rally on FII flows").allowed is False
    assert is_candidate_article_url(generic_url, title="Markets rally on FII flows").allowed is True

    nifty_score = link_score(
        "Nifty 50 target raised to 26,500",
        "https://economictimes.indiatimes.com/markets/stocks/news/nifty-target/articleshow/1.cms",
    )
    generic_score = link_score(
        "Markets rally on FII flows",
        "https://economictimes.indiatimes.com/markets/stocks/news/markets-rally/articleshow/2.cms",
    )
    assert nifty_score > generic_score


def test_link_discovery_summary() -> None:
    from collections import Counter

    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        LinkDiscoveryStats,
        format_link_discovery_summary,
    )

    stats = LinkDiscoveryStats(seen=10, kept=2, skip_reasons=Counter({"not_article": 5, "wrong_domain": 3}))
    summary = format_link_discovery_summary(stats)
    assert "10 seen" in summary
    assert "2 kept" in summary
    assert "5 not_article" in summary
    assert "3 wrong_domain" in summary


def test_extract_article_links_ranks_nifty_and_accepts_generic_headlines() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        extract_article_links,
    )

    source = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        domains=["economictimes.indiatimes.com"],
    )
    markdown = """
[Careers at GS](https://www.goldmansachs.com/careers)
[Markets rally on FII flows](https://economictimes.indiatimes.com/markets/stocks/news/markets-rally/articleshow/456.cms)
[Nifty 50 target raised](https://economictimes.indiatimes.com/markets/stocks/news/nifty-50-target-26500/articleshow/123.cms)
[Index options guide](https://economictimes.indiatimes.com/markets/options/nifty-ce-pe)
"""
    links = extract_article_links(markdown, source, limit=3)
    assert len(links) == 2
    assert links[0].endswith("/articleshow/123.cms")
    assert links[1].endswith("/articleshow/456.cms")


def test_extract_article_links_prefers_native_scored_links() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        extract_article_links,
    )

    source = ExternalPredictionSource(
        id="economictimes",
        display_name="Economic Times",
        domains=["economictimes.indiatimes.com"],
    )
    native_links = [
        {
            "href": "https://economictimes.indiatimes.com/markets/stocks/news/nifty-50-target/articleshow/777.cms",
            "text": "Nifty 50 target raised",
            "total_score": 0.9,
        },
        {
            "href": "https://economictimes.indiatimes.com/markets/stocks/news/markets-rally/articleshow/888.cms",
            "text": "Markets rally",
            "total_score": 0.1,
        },
    ]
    links = extract_article_links("", source, limit=2, native_links=native_links)
    assert links[0].endswith("/articleshow/777.cms")
    assert links[1].endswith("/articleshow/888.cms")


def test_horizon_validator_accepts_window() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        horizon_window_days,
        validate_record,
    )

    lo, hi = horizon_window_days(14)
    assert lo == 7
    assert hi == 28

    record = ExternalPredictionRecord(
        source_id="test",
        horizon_days=14,
        as_of="2026-07-20",
        spot_at_fetch=24000.0,
        target=ExternalPredictionTarget(mid=25000.0),
        target_date="2026-08-03",
        fetch_status="ok",
    )
    body = "Goldman Sachs sees Nifty 50 target at 25,000 by August on FII flows."
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "ok"
    assert validated.provenance["horizon_match"]["in_window"] is True


def test_horizon_validator_soft_mismatch_keeps_record() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        validate_record,
    )

    record = ExternalPredictionRecord(
        source_id="test",
        horizon_days=14,
        as_of="2026-07-20",
        spot_at_fetch=24000.0,
        target=ExternalPredictionTarget(mid=26500.0),
        target_date="2027-06-30",
        fetch_status="ok",
    )
    body = "Goldman Sachs sees Nifty 50 at 26,500 by June 2027."
    validated = validate_record(record, body=body, used_regex_only=False)
    assert validated.fetch_status == "ok"
    assert validated.provenance["horizon_match"]["in_window"] is False
    assert validated.provenance["horizon_match"].get("soft_mismatch") is True


def test_record_to_live_forecast_mapper() -> None:
    from trade_integrations.dataflows.index_research.external_predictions.validators import (
        record_to_live_forecast,
    )

    record = ExternalPredictionRecord(
        source_id="test",
        as_of="2026-07-20",
        spot_at_fetch=24000.0,
        target=ExternalPredictionTarget(low=24500.0, mid=25000.0, high=25500.0),
        expected_return_pct=4.17,
    )
    mapped = record_to_live_forecast(record)
    assert mapped is not None
    assert mapped["spot"] == 24000.0
    assert mapped["rangeLow"] == 24500.0
    assert mapped["rangeHigh"] == 25500.0


def test_extract_retries_on_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.extractor import (
        extract_prediction_from_text,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )

    source = ExternalPredictionSource(id="test", display_name="Test Broker")
    body = "Analyst sees Nifty 50 index target at 25,000 by month end on strong flows."
    prompts: list[str] = []

    def _fake_minimax(prompt: str, *, max_tokens: int = 1200) -> dict:
        prompts.append(prompt)
        if len(prompts) == 1:
            return {
                "has_prediction": True,
                "instrument": "RELIANCE",
                "target_mid": 26000,
                "direction": "bullish",
                "confidence": "medium",
            }
        return {
            "has_prediction": True,
            "instrument": "NIFTY50",
            "target_mid": 25000,
            "target_low": 24500,
            "target_high": 25500,
            "direction": "bullish",
            "confidence": "high",
            "rationale_bullets": ["Flows", "Earnings"],
        }

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.extractor._call_minimax",
        _fake_minimax,
    )

    record = extract_prediction_from_text(
        source=source,
        horizon_days=14,
        spot=24000.0,
        title="Nifty outlook",
        url="https://example.com/nifty-target",
        snippet=body[:200],
        body=body,
    )
    assert len(prompts) == 2
    assert "Previous extraction failed validation" in prompts[1]
    assert record.fetch_status == "ok"
    assert record.target.mid == 25000.0
    assert record.extraction.get("attempt") == 2


def test_extract_regex_does_not_retry_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.extractor import (
        extract_prediction_from_text,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )

    source = ExternalPredictionSource(id="test", display_name="Test Broker")
    body = "Broker sees Nifty 50 target at 25,000 on FII flows."

    def _fail_minimax(*_args, **_kwargs):
        raise RuntimeError("minimax unavailable")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.extractor._call_minimax",
        _fail_minimax,
    )

    record = extract_prediction_from_text(
        source=source,
        horizon_days=14,
        spot=24000.0,
        title="Nifty outlook",
        url="https://example.com/nifty-target",
        snippet=body[:200],
        body=body,
    )
    assert record.fetch_status == "ok"
    assert record.extraction.get("model") == "regex"
    assert record.extraction.get("attempt") == 1


def test_seed_sources_have_curated_urls(hub_dir: Path) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.curated_urls import (
        curated_urls_for_source,
    )

    sources = seed_registry_if_missing()
    for src in sources:
        if src.added_by != "seed":
            continue
        assert src.curated_urls, f"{src.id} missing curated_urls"
        assert curated_urls_for_source(src.id)
