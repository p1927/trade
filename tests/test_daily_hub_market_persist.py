"""Tests for daily hub OHLCV persistence."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.unit
def test_upsert_ohlcv_daily_factors_writes_open_close(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history_dir = tmp_path / "_data" / "history"
    history_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "date": "2026-07-21",
                "open": 24216.05,
                "high": 24262.2,
                "low": 24135.65,
                "close": 24150.85,
                "volume": 1000.0,
                "source": "yfinance_tail",
            }
        ]
    ).to_parquet(history_dir / "nifty_ohlcv_daily.parquet", index=False)

    from trade_integrations.dataflows.index_research.history_ingest import upsert_ohlcv_daily_factors
    from trade_integrations.dataflows.index_research.factor_store import load_factor_history

    result = upsert_ohlcv_daily_factors("2026-07-21")
    assert result["status"] == "ok"
    assert "nifty_open" in result["factors"]
    assert "nifty_close" in result["factors"]

    factors = load_factor_history("2026-07-21", "2026-07-21")
    by_name = {row["factor"]: row["value"] for _, row in factors.iterrows()}
    assert by_name["nifty_open"] == pytest.approx(24216.05)
    assert by_name["nifty_close"] == pytest.approx(24150.85)
