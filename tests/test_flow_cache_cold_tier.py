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

    deriv = load_history_dataset("flow_derivatives_daily")
    drow = deriv[deriv["date"].astype(str).str[:10] == "2026-07-21"]
    assert not drow.empty
    assert float(drow.iloc[0]["nifty_pcr"]) == pytest.approx(1.05)
