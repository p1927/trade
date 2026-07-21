"""Tests for flow cache promotion to cold tier."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.unit
def test_sync_flow_cache_to_cold_tier_merges_fii_net(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history_dir = tmp_path / "_data" / "history"
    history_dir.mkdir(parents=True)
    factors_dir = tmp_path / "_data" / "index_factors"
    factors_dir.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "date": "2026-07-20",
                "fii_net": -999.0,
                "dii_net": 100.0,
                "source": "mrchartist",
            }
        ]
    ).to_parquet(history_dir / "flow_cash_daily.parquet", index=False)

    pd.DataFrame(
        [
            {
                "date": "2026-07-21",
                "fii_net": 1650.16,
                "dii_net": -656.88,
                "nifty_pcr": 1.05,
                "source": "niftyinvest_api",
            }
        ]
    ).to_parquet(factors_dir / "flow_cash_daily.parquet", index=False)

    from trade_integrations.dataflows.index_research.history_ingest import sync_flow_cache_to_cold_tier
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    result = sync_flow_cache_to_cold_tier()
    assert result["status"] == "ok"

    cash = load_history_dataset("flow_cash_daily")
    row = cash[cash["date"].astype(str).str[:10] == "2026-07-21"].iloc[0]
    assert float(row["fii_net"]) == pytest.approx(1650.16)
    assert float(row["dii_net"]) == pytest.approx(-656.88)
    assert "nifty_pcr" not in cash.columns

    deriv = load_history_dataset("flow_derivatives_daily")
    drow = deriv[deriv["date"].astype(str).str[:10] == "2026-07-21"]
    assert not drow.empty
    assert float(drow.iloc[0]["nifty_pcr"]) == pytest.approx(1.05)


@pytest.mark.unit
def test_finalize_daily_cold_tier_marks_empty_cache_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history_dir = tmp_path / "_data" / "history"
    history_dir.mkdir(parents=True)
    panel_dir = tmp_path / "_data" / "index_factors" / "panel"
    panel_dir.mkdir(parents=True)

    pd.DataFrame([{"date": "2026-07-20", "close": 24238.5}]).to_parquet(
        history_dir / "nifty_ohlcv_daily.parquet", index=False
    )
    pd.DataFrame([{"date": "2026-07-20", "fii_net": -1.0, "dii_net": 2.0}]).to_parquet(
        history_dir / "flow_cash_daily.parquet", index=False
    )
    pd.DataFrame([{"date": "2026-07-20", "close": 24238.5}]).to_parquet(
        panel_dir / "NIFTY_2006_present.parquet", index=False
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_ingest.sync_repo_flows_to_cold_tier",
        lambda **_: {"status": "skipped", "reason": "empty_frame"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_ingest.sync_flow_cache_to_cold_tier",
        lambda: {"status": "skipped", "reason": "empty_cache"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_ingest.sync_macro_daily_tail",
        lambda **_: {"status": "ok", "tail_start": "2026-07-08", "tail_end": "2026-07-21"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_ingest.sync_india_vix_tail",
        lambda **_: {"status": "ok", "tail_start": "2026-07-08", "tail_end": "2026-07-21"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_panel.refresh_panel_tail",
        lambda **_: {"status": "ok", "mode": "tail_refresh"},
    )

    from trade_integrations.dataflows.index_research.history_ingest import finalize_daily_cold_tier

    result = finalize_daily_cold_tier()
    assert result["status"] == "partial"
    assert "cache_flows" in result["failed_steps"]


@pytest.mark.unit
def test_sync_flow_cache_migrates_legacy_deriv_cols_from_existing_cash(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history_dir = tmp_path / "_data" / "history"
    history_dir.mkdir(parents=True)
    factors_dir = tmp_path / "_data" / "index_factors"
    factors_dir.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "date": "2026-07-21",
                "fii_net": 100.0,
                "dii_net": 50.0,
                "nifty_pcr": 1.11,
                "source": "legacy",
            }
        ]
    ).to_parquet(history_dir / "flow_cash_daily.parquet", index=False)

    pd.DataFrame(
        [
            {
                "date": "2026-07-21",
                "fii_net": 1650.16,
                "dii_net": -656.88,
                "source": "niftyinvest_api",
            }
        ]
    ).to_parquet(factors_dir / "flow_cash_daily.parquet", index=False)

    from trade_integrations.dataflows.index_research.history_ingest import sync_flow_cache_to_cold_tier
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    result = sync_flow_cache_to_cold_tier()
    assert result["status"] == "ok"

    cash = load_history_dataset("flow_cash_daily")
    assert "nifty_pcr" not in cash.columns
    row = cash[cash["date"].astype(str).str[:10] == "2026-07-21"].iloc[0]
    assert float(row["fii_net"]) == pytest.approx(1650.16)

    deriv = load_history_dataset("flow_derivatives_daily")
    drow = deriv[deriv["date"].astype(str).str[:10] == "2026-07-21"]
    assert not drow.empty
    assert float(drow.iloc[0]["nifty_pcr"]) == pytest.approx(1.11)


@pytest.mark.unit
def test_persist_flow_cash_shrink_guard_blocks_wipe(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history_dir = tmp_path / "_data" / "history"
    history_dir.mkdir(parents=True)

    rows = [
        {"date": f"2026-07-{day:02d}", "fii_net": float(day), "dii_net": 1.0}
        for day in range(1, 11)
    ]
    pd.DataFrame(rows).to_parquet(history_dir / "flow_cash_daily.parquet", index=False)

    from trade_integrations.dataflows.index_research.history_ingest import _persist_flow_cash_cold_tier

    tiny = pd.DataFrame([{"date": "2026-07-21", "fii_net": 1.0, "dii_net": 2.0}])
    result = _persist_flow_cash_cold_tier(tiny, overlay=tiny)
    assert result["status"] == "error"
    assert result["reason"] == "flow_cash_shrink_guard"
