"""Tests for hub market intelligence archive."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.hub_storage.market_intelligence_archive import archive_market_intelligence


def test_archive_derivatives_and_news(tmp_path, monkeypatch):
    symbol_dir = tmp_path / "NIFTY"
    (symbol_dir / "options_research").mkdir(parents=True)
    (symbol_dir / "company_research").mkdir(parents=True)
    (symbol_dir / "options_research" / "latest.json").write_text(
        json.dumps(
            {
                "underlying": "NIFTY",
                "chain_snapshot": {
                    "underlying": "NIFTY",
                    "underlying_ltp": 24000,
                    "expiry_date": "21JUL26",
                    "chain": [
                        {
                            "strike": 24000,
                            "ce": {"symbol": "NIFTYCE", "ltp": 120, "oi": 1000},
                            "pe": {"symbol": "NIFTYPE", "ltp": 90, "oi": 800},
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (symbol_dir / "company_research" / "latest.json").write_text(
        json.dumps(
            {
                "news": {
                    "blocks": [
                        {
                            "ticker": "NIFTY",
                            "source": "news_aggregator",
                            "headlines": [{"title": "Markets rise on RBI hold"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    summary = archive_market_intelligence(as_of_date="2026-07-16")
    assert summary["derivatives_rows_added"] == 2
    assert summary["news_rows_added"] == 1

    deriv_path = tmp_path / "_data" / "derivatives_chain" / "daily" / "2026-07-16.parquet"
    news_path = tmp_path / "_data" / "news" / "daily" / "2026-07-16.parquet"
    assert deriv_path.is_file()
    assert news_path.is_file()
    assert len(pd.read_parquet(deriv_path)) == 2
    assert len(pd.read_parquet(news_path)) == 1
