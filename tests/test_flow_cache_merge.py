"""Fast unit tests for flow cache merge / cached-only analysis paths."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


@pytest.mark.unit
def test_merge_cached_skips_live_fetchers(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    repo = pd.DataFrame([{"date": "2026-07-15", "fii_net": 50.0, "source": "nse_repo"}])

    with patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.fetch_mrchartist_flow_frame",
        return_value=pd.DataFrame(),
    ) as mr_mock, patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.fetch_mrchartist_latest_session",
        return_value=pd.DataFrame(),
    ) as latest_mock, patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.fetch_nselib_fii_dii_frame",
        return_value=pd.DataFrame(),
    ) as nse_mock, patch(
        "trade_integrations.nse_browser.repository.load_nse_repository_fii_dii_frame",
        return_value=repo,
    ):
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            merge_flow_derivatives_frame,
        )

        merged = merge_flow_derivatives_frame("2026-07-15", "2026-07-15", allow_live_fetch=False)

    mr_mock.assert_called_once_with(include_seeded=False, allow_live_fetch=False)
    latest_mock.assert_called_once_with(allow_live_fetch=False)
    assert nse_mock.call_args.kwargs.get("allow_live_fetch") is False
    assert not merged.empty
    assert float(merged.iloc[0]["fii_net"]) == pytest.approx(50.0)


@pytest.mark.unit
def test_merge_hub_rows_win_over_stale_flow_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    cache_dir = tmp_path / "_data" / "index_factors"
    cache_dir.mkdir(parents=True)
    pd.DataFrame([{"date": "2026-07-13", "fii_net": 1.0, "source": "flow_cache"}]).to_csv(
        cache_dir / "flow_cash_daily.csv",
        index=False,
    )
    hub_nse = tmp_path / "_data" / "nse_browser"
    hub_nse.mkdir(parents=True)
    pd.DataFrame(
        [{"date": "2026-07-13", "fii_net": -100.0, "dii_net": 200.0, "source": "nse_browser_combined"}]
    ).to_csv(hub_nse / "fii_dii_daily.csv", index=False)

    with patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.fetch_web_flow_cash_frame",
        return_value=pd.DataFrame(),
    ), patch(
        "trade_integrations.nse_browser.repository.load_nse_repository_fii_dii_frame",
        return_value=pd.DataFrame(),
    ):
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            merge_flow_derivatives_frame,
        )

        merged = merge_flow_derivatives_frame("2026-07-13", "2026-07-13", allow_live_fetch=False)

    assert float(merged.iloc[0]["fii_net"]) == pytest.approx(-100.0)


@pytest.mark.unit
def test_upsert_flow_cash_cache_last_wins_by_date(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        load_flow_cash_cache,
        upsert_flow_cash_cache,
    )

    upsert_flow_cash_cache([{"date": "2026-07-10", "fii_net": 10.0, "source": "a"}])
    upsert_flow_cash_cache([{"date": "2026-07-10", "fii_net": 20.0, "source": "b"}])
    frame = load_flow_cash_cache()

    assert len(frame) == 1
    assert float(frame.iloc[0]["fii_net"]) == pytest.approx(20.0)


@pytest.mark.unit
def test_enrich_factor_history_cached_only_skips_fao_backfill():
    nifty = pd.DataFrame({"date": ["2026-07-14"], "close": [25000.0]})
    flow = pd.DataFrame({"date": ["2026-07-14"], "fii_net": [100.0], "dii_net": [50.0]})

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
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.backfill_nse_fao_to_cache",
    ) as fao_mock, patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.merge_flow_derivatives_frame",
        return_value=flow,
    ) as merge_mock, patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_fii_net_5d_series",
        return_value=pd.Series({"2026-07-14": 100.0}),
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_dii_net_5d_series",
        return_value=pd.Series({"2026-07-14": 50.0}),
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_institutional_joint_series",
        return_value=(pd.Series(dtype=float), pd.Series(dtype=float)),
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_nifty_pe_proxy_series",
        return_value=pd.Series(dtype=float),
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.build_constituent_momentum_series",
        return_value=pd.Series(dtype=float),
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.flow_backfill_summary",
        return_value={"status": "ok"},
    ) as summary_mock, patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.upsert_daily_factors",
    ):
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        enrich_factor_history(days=30, allow_live_fetch=False)

    fao_mock.assert_not_called()
    merge_mock.assert_called_once()
    assert merge_mock.call_args.kwargs.get("allow_live_fetch") is False
    summary_mock.assert_called_once()
    assert summary_mock.call_args.kwargs.get("allow_live_fetch") is False
