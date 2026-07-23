"""Unit tests for scheduled index research job dispatch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.scheduled_research.index_jobs import (
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_RESEARCH,
    INDEX_JOB_TYPES,
    dispatch_index_job_sync,
    is_index_scheduler_enabled,
    register_default_index_jobs,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.mark.unit
class TestIndexSchedulerEnv:
    def test_enabled_when_env_true(self):
        assert is_index_scheduler_enabled("true") is True
        assert is_index_scheduler_enabled("1") is True

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("INDEX_RESEARCH_ENABLE_SCHEDULER", raising=False)
        assert is_index_scheduler_enabled() is False


@pytest.mark.unit
class TestIndexJobDispatch:
    def test_snapshot_job_calls_run_snapshot(self):
        job = ScheduledResearchJob(
            id="snap-1",
            prompt="snapshot",
            schedule="86400000",
            config={"job_type": JOB_TYPE_INDEX_FACTOR_SNAPSHOT},
        )
        with patch(
            "src.scheduled_research.index_jobs.run_index_factor_snapshot_job",
            return_value={"rows": 3},
        ) as run_mock:
            dispatch_index_job_sync(job)
        run_mock.assert_called_once_with(job.config)

    def test_research_job_calls_pipeline(self):
        job = ScheduledResearchJob(
            id="research-1",
            prompt="research",
            schedule="604800000",
            config={"job_type": JOB_TYPE_INDEX_RESEARCH, "ticker": "NIFTY"},
        )
        with patch("src.scheduled_research.index_jobs.run_index_research_job") as run_mock:
            dispatch_index_job_sync(job)
        run_mock.assert_called_once_with(job.config)

    def test_calibration_job_calls_runner(self):
        job = ScheduledResearchJob(
            id="cal-1",
            prompt="calibration",
            schedule="86400000",
            config={"job_type": JOB_TYPE_INDEX_CALIBRATION, "ticker": "NIFTY"},
        )
        with patch(
            "src.scheduled_research.index_jobs.run_index_calibration_job",
            return_value={"retrained": False},
        ) as run_mock:
            dispatch_index_job_sync(job)
        run_mock.assert_called_once_with(job.config)

    def test_unknown_job_type_raises(self):
        job = ScheduledResearchJob(
            id="bad",
            prompt="bad",
            schedule="60000",
            config={"job_type": "unknown"},
        )
        with pytest.raises(ValueError, match="unsupported index job_type"):
            dispatch_index_job_sync(job)


@pytest.mark.unit
class TestIndexJobRegistration:
    def test_registers_defaults_when_missing(self, tmp_path):
        store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
        created = register_default_index_jobs(store)
        assert created >= 4
        jobs = store.load()
        assert "nifty-index-factor-snapshot" in jobs
        assert "nifty-index-research" in jobs
        assert "nifty-hub-news-entity" in jobs
        assert jobs["nifty-index-factor-snapshot"].config["job_type"] == JOB_TYPE_INDEX_FACTOR_SNAPSHOT
        snap_cfg = jobs["nifty-index-factor-snapshot"].config
        assert snap_cfg.get("skip_constituents") is True
        assert snap_cfg.get("enrich_rolling_only") is True
        assert snap_cfg.get("participant_oi_days") == 1
        assert snap_cfg.get("live_fetch_days") == 1
        assert jobs["nifty-index-research"].config["job_type"] == JOB_TYPE_INDEX_RESEARCH

    def test_idempotent_registration(self, tmp_path):
        store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
        first = register_default_index_jobs(store)
        assert first >= 4
        assert register_default_index_jobs(store) == 0

    def test_external_predictions_cron_not_registered(self, tmp_path):
        store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
        register_default_index_jobs(store)
        jobs = store.load()
        assert "nifty-external-predictions-refresh" not in jobs

    def test_index_job_types_frozen(self):
        assert JOB_TYPE_INDEX_FACTOR_SNAPSHOT in INDEX_JOB_TYPES
        assert JOB_TYPE_INDEX_RESEARCH in INDEX_JOB_TYPES
        assert JOB_TYPE_INDEX_CALIBRATION in INDEX_JOB_TYPES
        assert JOB_TYPE_COMPANY_RESEARCH_ARCHIVE in INDEX_JOB_TYPES


@pytest.mark.unit
class TestFactorSnapshotEnrichmentPolicy:
    def test_scheduled_enrichment_skips_batch_historic_ingest(self):
        with patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment._prepare_nse_repository_layers",
        ) as prep_mock, patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.purge_anomalous_factor_snapshots",
            return_value=[],
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.load_nifty_history",
            return_value=__import__("pandas").DataFrame(),
        ):
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            prep_mock.return_value = {"seed": {}, "hub": {}}
            enrich_factor_history(days=7, batch_historic=False, enrichment_mode="light")
            prep_mock.assert_called_once_with(
                allow_live_fetch=True,
                enrich_days=7,
                batch_historic=False,
                skip_niftyinvest_fetch=False,
                force_hub_mirror=False,
                live_fetch_days=7,
            )

    def test_light_enrichment_skips_alpha_zoo_and_news(self):
        import pandas as pd

        nifty = pd.DataFrame({"date": ["2026-01-01"], "close": [100.0]})
        with patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment._prepare_nse_repository_layers",
            return_value={"seed": {}, "hub": {}},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.purge_anomalous_factor_snapshots",
            return_value=[],
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.load_nifty_history",
            return_value=nifty,
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.merge_flow_derivatives_frame",
            return_value=pd.DataFrame(),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_fii_net_5d_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_dii_net_5d_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_institutional_joint_series",
            return_value=(pd.Series(dtype=float), pd.Series(dtype=float)),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_nifty_pe_proxy_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_constituent_momentum_series",
        ) as momentum_mock, patch(
            "trade_integrations.dataflows.index_research.news_event_features.backfill_news_event_features",
        ) as news_mock, patch(
            "trade_integrations.dataflows.index_research.alpha_bridge.backfill.backfill_alpha_zoo_history",
        ) as alpha_mock, patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.upsert_daily_factors",
        ):
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            result = enrich_factor_history(days=7, enrichment_mode="light")
            momentum_mock.assert_not_called()
            news_mock.assert_not_called()
            alpha_mock.assert_not_called()
            assert result["news_event_features"]["status"] == "skipped"
            assert result["alpha_zoo_backfill"]["status"] == "skipped"

    def test_factor_snapshot_reraises_pipeline_cancel(self):
        from trade_integrations.dataflows.index_research.pipeline_cancel import PipelineCancelledError

        with patch(
            "trade_integrations.dataflows.index_research.snapshot.run_snapshot",
            return_value={"date": "2026-01-01"},
        ), patch(
            "trade_integrations.dataflows.index_research.participant_oi_backfill.backfill_participant_oi",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
            side_effect=PipelineCancelledError("shutdown"),
        ):
            from src.scheduled_research.index_jobs import run_index_factor_snapshot_job

            with pytest.raises(PipelineCancelledError):
                run_index_factor_snapshot_job({})

    def test_factor_snapshot_skips_finalize_when_enrich_fails(self):
        with patch(
            "trade_integrations.dataflows.index_research.history_ingest.persist_daily_hub_market_data",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.snapshot.run_snapshot",
            return_value={"date": "2026-01-01"},
        ), patch(
            "trade_integrations.dataflows.index_research.participant_oi_backfill.backfill_participant_oi",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
            return_value={"status": "error", "reason": "mock"},
        ), patch(
            "trade_integrations.dataflows.index_research.history_ingest.finalize_daily_cold_tier",
        ) as finalize_mock:
            from src.scheduled_research.index_jobs import run_index_factor_snapshot_job

            summary = run_index_factor_snapshot_job({})
            finalize_mock.assert_not_called()
            assert summary["cold_tier_finalize"]["reason"] == "factor_enrichment_failed"
            assert summary.get("had_errors") is True

    def test_factor_snapshot_skips_finalize_when_persist_fails(self):
        with patch(
            "trade_integrations.dataflows.index_research.history_ingest.persist_daily_hub_market_data",
            return_value={"status": "error", "reason": "mock", "ohlcv": {"status": "error"}},
        ), patch(
            "trade_integrations.dataflows.index_research.snapshot.run_snapshot",
            return_value={"date": "2026-01-01"},
        ), patch(
            "trade_integrations.dataflows.index_research.participant_oi_backfill.backfill_participant_oi",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.history_ingest.finalize_daily_cold_tier",
        ) as finalize_mock:
            from src.scheduled_research.index_jobs import run_index_factor_snapshot_job

            summary = run_index_factor_snapshot_job({})
            finalize_mock.assert_not_called()
            assert summary["cold_tier_finalize"]["reason"] == "persist_failed"
            assert summary.get("had_errors") is True

    def test_factor_snapshot_skips_finalize_when_no_nifty_history(self):
        with patch(
            "trade_integrations.dataflows.index_research.history_ingest.persist_daily_hub_market_data",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.snapshot.run_snapshot",
            return_value={"date": "2026-01-01"},
        ), patch(
            "trade_integrations.dataflows.index_research.participant_oi_backfill.backfill_participant_oi",
            return_value={"status": "ok"},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
            return_value={"days_enriched": 0, "reason": "no_nifty_history"},
        ), patch(
            "trade_integrations.dataflows.index_research.history_ingest.finalize_daily_cold_tier",
        ) as finalize_mock:
            from src.scheduled_research.index_jobs import run_index_factor_snapshot_job

            summary = run_index_factor_snapshot_job({})
            finalize_mock.assert_not_called()
            assert summary["cold_tier_finalize"]["reason"] == "no_nifty_history"
            assert summary.get("had_errors") is True


@pytest.mark.unit
class TestMacroRefreshPolicy:
    def test_macro_refresh_uses_light_sync_and_skip_repo_sync(self):
        with patch(
            "trade_integrations.nse_browser.repository.sync_light_repo_seed_layers",
            return_value={"fii_dii": 1},
        ) as light_mock, patch(
            "trade_integrations.nse_browser.repository.ingest_repository_to_hub",
            return_value={"fii_dii": 10},
        ) as hub_mock:
            from src.scheduled_research.trade_data_jobs import run_nse_macro_refresh_job

            summary = run_nse_macro_refresh_job({})
            light_mock.assert_called_once()
            hub_mock.assert_called_once_with(skip_repo_sync=True, allow_live_fetch=False)
            assert summary["seed_layers"] == {"fii_dii": 1}


@pytest.mark.unit
class TestOrchestratorHubMirrorPolicy:
    def test_ingest_nse_repository_skips_batch_repo_sync(self):
        with patch(
            "trade_integrations.nse_browser.orchestrator.sync_light_repo_seed_layers",
            return_value={"sector_indices": 100},
        ) as light_mock, patch(
            "trade_integrations.nse_browser.orchestrator.ingest_repository_to_hub",
            return_value={"fii_dii": 5},
        ) as hub_mock:
            from trade_integrations.nse_browser.orchestrator import ingest_nse_repository

            payload = ingest_nse_repository()
            light_mock.assert_called_once_with(allow_live_fetch=False)
            hub_mock.assert_called_once_with(skip_repo_sync=True, allow_live_fetch=False)
            assert payload["seed_layers"]["sector_indices"] == 100


@pytest.mark.unit
class TestIncrementalEnrichment:
    def test_skips_days_with_complete_light_factors(self):
        import pandas as pd

        nifty = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "close": [100.0, 101.0]})
        with patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment._prepare_nse_repository_layers",
            return_value={"seed": {}, "hub": {}},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.purge_anomalous_factor_snapshots",
            return_value=[],
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.load_nifty_history",
            return_value=nifty,
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.merge_flow_derivatives_frame",
            return_value=pd.DataFrame(),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_fii_net_5d_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_dii_net_5d_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_institutional_joint_series",
            return_value=(pd.Series(dtype=float), pd.Series(dtype=float)),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_nifty_pe_proxy_series",
            return_value=pd.Series(dtype=float),
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.upsert_daily_factors",
        ) as upsert_mock, patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.select_enrichment_candidate_days",
            return_value=["2026-01-02"],
        ):
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            result = enrich_factor_history(days=2, enrichment_mode="light")
            assert result["days_skipped"] == 1
            assert upsert_mock.call_count == 1

    def test_rolling_only_limits_candidate_days(self):
        from trade_integrations.dataflows.index_research.factor_store import (
            select_enrichment_candidate_days,
        )

        dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
        with patch(
            "trade_integrations.dataflows.index_research.factor_store.filter_days_needing_enrichment",
            side_effect=lambda trading_dates, **kwargs: list(trading_dates),
        ) as filter_mock:
            result = select_enrichment_candidate_days(
                dates,
                light_mode=True,
                rolling_only=True,
                max_lookback=7,
            )
        assert filter_mock.call_count == 1
        assert filter_mock.call_args.args[0] == dates[-7:]
        assert result == dates[-7:]

    def test_all_days_complete_early_exit(self):
        import pandas as pd

        nifty = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "close": [100.0, 101.0]})
        with patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment._prepare_nse_repository_layers",
            return_value={"seed": {}, "hub": {}},
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.purge_anomalous_factor_snapshots",
            return_value=[],
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.load_nifty_history",
            return_value=nifty,
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.select_enrichment_candidate_days",
            return_value=[],
        ), patch(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.merge_flow_derivatives_frame",
        ) as merge_mock:
            from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
                enrich_factor_history,
            )

            result = enrich_factor_history(days=7, enrichment_mode="light", enrich_rolling_only=True)
            assert result["status"] == "skipped"
            assert result["reason"] == "all_days_complete"
            merge_mock.assert_not_called()


@pytest.mark.unit
class TestFlowCacheIncremental:
    def test_upsert_noop_when_values_unchanged(self, tmp_path, monkeypatch):
        import pandas as pd

        cache_path = tmp_path / "flow_cash.parquet"
        existing = pd.DataFrame(
            [{"date": "2026-01-01", "fii_cash": 100.0, "dii_cash": 50.0}],
        )
        existing.to_parquet(cache_path, index=False)

        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.get_flow_cash_cache_path",
            lambda: cache_path,
        )
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.load_flow_cash_cache",
            lambda: existing.copy(),
        )

        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            upsert_flow_cash_cache,
        )

        written = upsert_flow_cash_cache(
            [{"date": "2026-01-01", "fii_cash": 100.0, "dii_cash": 50.0}],
        )
        assert written == 0
        assert pd.read_parquet(cache_path)["fii_cash"].iloc[0] == 100.0


@pytest.mark.unit
class TestNewsMarketContext:
    def test_fetch_factor_snapshot_uses_short_history_window(self, monkeypatch):
        import pandas as pd

        frame = pd.DataFrame(
            {
                "date": ["2026-07-20"],
                "close": [25000.0],
                "india_vix": [12.5],
                "fii_net_5d": [1000.0],
            }
        )
        monkeypatch.setenv("HUB_NEWS_FACTOR_HISTORY_DAYS", "14")
        with patch(
            "trade_integrations.dataflows.index_research.sources.history_loader.load_aligned_factor_history",
            return_value=frame,
        ) as load_mock:
            from trade_integrations.dataflows.index_research.news_market_context import (
                _fetch_factor_snapshot,
            )

            snap = _fetch_factor_snapshot()
        load_mock.assert_called_once_with(days=14)
        assert snap.get("india_vix") == 12.5

    def test_light_ingest_uses_cached_market_context(self, monkeypatch):
        cached = {
            "as_of": "2026-07-20T10:00:00+00:00",
            "quotes": {"NIFTY": {"ltp": 25000.0}},
            "factors": {"india_vix": 12.0},
            "quotes_ok": 1,
            "factors_ok": 1,
            "source": "cached",
        }
        with patch(
            "trade_integrations.dataflows.index_research.news_market_context.get_market_context_for_pipeline",
            return_value=cached,
        ) as ctx_mock, patch(
            "trade_integrations.dataflows.index_research.news_market_context.refresh_index_market_context",
        ) as refresh_mock, patch(
            "trade_integrations.dataflows.index_research.hub_news_ingest._ingest_rss",
            return_value={"queued": 0, "ingested": 0},
        ), patch(
            "trade_integrations.dataflows.news_hub_bridge.hub_news_pipeline_status",
            return_value={"queued": 0},
        ):
            from trade_integrations.dataflows.index_research.hub_news_ingest import run_hub_news_ingest

            result = run_hub_news_ingest(ticker="NIFTY", mode="light", sources="rss")
        ctx_mock.assert_called_once_with(ticker="NIFTY", refresh=False)
        refresh_mock.assert_not_called()
        assert result["market_context"]["source"] == "cached"


@pytest.mark.unit
class TestHubNewsLightSourceGuard:
    def test_light_mode_drops_heavy_sources_from_config(self):
        from trade_integrations.dataflows.index_research.hub_news_ingest import (
            _apply_light_source_guard,
        )

        selected = {"rss", "searxng", "watcher"}
        assert _apply_light_source_guard(selected, ingest_mode="light") == {"rss"}
        assert _apply_light_source_guard(selected, ingest_mode="full") == selected


@pytest.mark.unit
class TestConditionalHubMirror:
    def test_skips_hub_mirror_when_fingerprint_unchanged(self):
        with patch(
            "trade_integrations.nse_browser.repository.sync_all_repo_seed_layers",
        ), patch(
            "trade_integrations.nse_browser.repository.repo_hub_sync_fingerprint",
            return_value="fp-abc",
        ), patch(
            "trade_integrations.nse_browser.repository._load_manifest",
            return_value={"hub_sync": {"fingerprint": "fp-abc", "last_counts": {"fii_dii": 3}}},
        ), patch(
            "trade_integrations.nse_browser.repository._ingest_repository_parquet_to_hub",
        ) as mirror_mock:
            from trade_integrations.nse_browser.repository import ingest_repository_to_hub

            counts = ingest_repository_to_hub(skip_repo_sync=True)
            mirror_mock.assert_not_called()
            assert counts["fii_dii"] == 3


@pytest.mark.unit
class TestRepoConsistencyJob:
    def test_consistency_job_calls_checker(self):
        with patch(
            "trade_integrations.nse_browser.repo_consistency.run_repo_consistency_check",
            return_value={"status": "ok", "drift": []},
        ) as check_mock:
            from src.scheduled_research.trade_data_jobs import run_nse_repo_consistency_job

            summary = run_nse_repo_consistency_job({"trigger_reingest_on_drift": False})
            check_mock.assert_called_once_with(trigger_reingest_on_drift=False)
            assert summary["status"] == "ok"


@pytest.mark.unit
class TestHistoricManifestParserVersion:
    def test_skip_busts_when_parser_version_changes(self, tmp_path: Path) -> None:
        from trade_integrations.nse_browser.parsers import historic_data as hd

        source = tmp_path / "panel.csv"
        out = tmp_path / "panel.parquet"
        source.write_text("date,value\n2024-01-01,1\n", encoding="utf-8")
        out.write_text("placeholder", encoding="utf-8")

        manifest = {
            "datasets": {
                "panel": {
                    "source_sha256": hd._source_fingerprint(source)["source_sha256"],
                    "parser_version": "old-version",
                }
            }
        }
        assert hd._should_skip_historic_dataset(
            manifest=manifest,
            dataset_key="panel",
            source_path=source,
            out_path=out,
        ) is False

        manifest["datasets"]["panel"]["parser_version"] = hd._historic_parser_version()
        assert hd._should_skip_historic_dataset(
            manifest=manifest,
            dataset_key="panel",
            source_path=source,
            out_path=out,
        ) is True


@pytest.mark.unit
class TestScheduledRoutesDispatchRouting:
    @pytest.mark.asyncio
    async def test_routes_index_job_types(self):
        from src.api import scheduled_routes

        job = ScheduledResearchJob(
            id="route-test",
            prompt="index",
            schedule="60000",
            config={"job_type": JOB_TYPE_INDEX_RESEARCH},
        )
        with patch("src.scheduled_research.index_jobs.dispatch_index_job") as dispatch_mock:
            dispatch_mock.return_value = None
            await scheduled_routes._dispatch_scheduled_research_job(job)
        dispatch_mock.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_default_jobs_use_agent_session(self):
        from src.api import scheduled_routes

        job = ScheduledResearchJob(
            id="agent-test",
            prompt="analyze RELIANCE",
            schedule="60000",
            config={},
        )
        session = MagicMock(session_id="sess-1")
        svc = MagicMock()
        svc.create_session.return_value = session
        svc.send_message = AsyncMock()
        host = MagicMock()
        host._get_session_service.return_value = svc

        with patch.dict("sys.modules", {"api_server": host}):
            await scheduled_routes._dispatch_scheduled_research_job(job)

        svc.create_session.assert_called_once()
        svc.send_message.assert_awaited_once_with("sess-1", "analyze RELIANCE")
