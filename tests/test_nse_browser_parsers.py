"""Tests for NSE browser parsers and hub merge (no live NSE)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from trade_integrations.nse_browser.parsers.archives import parse_bulk_deals_csv, parse_pe_pb_csv
from trade_integrations.nse_browser.parsers.fii_dii import (
    merge_fii_dii_variants,
    parse_fii_dii_csv,
    parse_fii_dii_json,
)
from trade_integrations.nse_browser.parsers.fpi import aggregate_fpi_daily, parse_fpi_investment_table
from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
    load_nse_browser_fii_dii_frame,
    merge_flow_derivatives_frame,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nse_browser"


def test_parse_fii_dii_csv_combined():
    text = (FIXTURES / "fii_dii_combined.csv").read_text(encoding="utf-8")
    frame = parse_fii_dii_csv(text, variant="combined")
    assert len(frame) == 2
    assert frame.iloc[0]["date"] == "2026-07-13"
    assert float(frame.iloc[0]["fii_net"]) == pytest.approx(-3395.80)
    assert float(frame.iloc[0]["dii_net"]) == pytest.approx(2354.58)


def test_parse_nsdl_fpi_html_subtotals():
    html_path = FIXTURES.parent / ".." / ".." / "reports" / "hub" / "_data" / "nse_browser" / "raw" / "fpi_nsdl" / "nsdl_latest_20260716.html"
    if not html_path.is_file():
        pytest.skip("live NSDL html snapshot not present")
    from trade_integrations.nse_browser.parsers.fpi import aggregate_fpi_daily, parse_nsdl_fpi_html

    detail = parse_nsdl_fpi_html(html_path.read_text(encoding="utf-8"))
    assert not detail.empty
    daily = aggregate_fpi_daily(detail)
    assert not daily.empty
    assert "fpi_equity_net_inr" in daily.columns


def test_parse_fii_dii_json():
    payload = (
        '[{"category":"FII/FPI","date":"13-Jul-2026","buyValue":100,"sellValue":90,"netValue":10},'
        '{"category":"DII","date":"13-Jul-2026","buyValue":200,"sellValue":150,"netValue":50}]'
    )
    frame = parse_fii_dii_json(payload)
    assert len(frame) == 1
    assert float(frame.iloc[0]["fii_net"]) == pytest.approx(10.0)
    assert float(frame.iloc[0]["dii_net"]) == pytest.approx(50.0)


def test_merge_fii_dii_variants_prefers_later_variant():
    a = parse_fii_dii_csv(
        "Category,Date,Buy Value,Sell Value,Net Value\nFII/FPI,13-Jul-2026,1,2,-1\n",
        variant="nse_only",
    )
    b = parse_fii_dii_csv(
        "Category,Date,Buy Value,Sell Value,Net Value\nFII/FPI,13-Jul-2026,1,2,-99\n",
        variant="combined",
    )
    merged = merge_fii_dii_variants(a, b)
    assert float(merged.iloc[0]["fii_net"]) == pytest.approx(-99)


def test_parse_fpi_investment_table_and_aggregate():
    raw = pd.DataFrame(
        [
            {
                "Reporting Date": "13-Jul-2026",
                "Debt/Equity": "Equity",
                "Investment Route": "Stock Exchange",
                "Gross Purchases(Rs. Crore)": 100.0,
                "Gross Sales (Rs. Crore)": 90.0,
                "Net Investment (Rs. Crore)": 10.0,
                "Net Investment US($) million": 1.2,
            },
            {
                "Reporting Date": "13-Jul-2026",
                "Debt/Equity": "Debt-General Limit",
                "Investment Route": "Stock Exchange",
                "Gross Purchases(Rs. Crore)": 50.0,
                "Gross Sales (Rs. Crore)": 60.0,
                "Net Investment (Rs. Crore)": -10.0,
                "Net Investment US($) million": -1.1,
            },
        ]
    )
    detail = parse_fpi_investment_table(raw, source="test")
    daily = aggregate_fpi_daily(detail)
    assert len(daily) == 1
    assert float(daily.iloc[0]["fpi_equity_net_inr"]) == pytest.approx(10.0)
    assert float(daily.iloc[0]["fpi_debt_net_inr"]) == pytest.approx(-10.0)


def test_parse_archive_csv_pe_pb():
    text = "Date,Index,P/E,P/B\n2026-07-13,NIFTY,22.1,3.4\n"
    frame = parse_pe_pb_csv(text)
    assert len(frame) == 1
    assert frame.iloc[0]["dataset"] == "pe_pb"


def test_parse_bulk_deals_csv():
    text = "Date,Symbol,Client Name,Buy/Sell,Quantity,Trade Price/Wgt. Avg. Price\n2026-07-13,RELIANCE,ACME,BUY,1000,2500\n"
    frame = parse_bulk_deals_csv(text)
    assert len(frame) == 1
    assert frame.iloc[0]["dataset"] == "bulk_deals"


def test_load_nse_browser_fii_dii_frame_empty_without_hub(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    frame = load_nse_browser_fii_dii_frame("2026-01-01", "2026-12-31")
    assert frame.empty


def test_merge_flow_includes_browser_hub_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    hub_nse = tmp_path / "_data" / "nse_browser"
    hub_nse.mkdir(parents=True)
    df = pd.DataFrame(
        [
            {"date": "2026-07-13", "fii_net": -100.0, "dii_net": 200.0, "source": "nse_browser_combined"},
        ]
    )
    try:
        df.to_parquet(hub_nse / "fii_dii_daily.parquet", index=False)
    except ImportError:
        df.to_csv(hub_nse / "fii_dii_daily.csv", index=False)

    merged = merge_flow_derivatives_frame("2026-07-13", "2026-07-13")
    assert not merged.empty
    assert float(merged.iloc[0]["fii_net"]) == pytest.approx(-100.0)


def test_parse_sector_index_csv():
    from trade_integrations.nse_browser.parsers.sector_indices import (
        load_nifty50_sector_csvs,
        parse_sector_index_csv,
    )
    from trade_integrations.nse_browser.repository import repo_root

    text = (
        '"Index Name","Date","Open","High","Low","Close"\n'
        '"NIFTY METAL","16 Jul 2026","12620.9","12623.15","12483.4","12495.90"\n'
        '"NIFTY 50","16 Jul 2026","25000","25100","24900","25050"\n'
    )
    frame = parse_sector_index_csv(text, source_file="test.csv")
    assert len(frame) == 2
    assert set(frame["index_slug"]) == {"metal", "nifty50"}
    assert frame.iloc[0]["date"] == "2026-07-16"

    repo = repo_root()
    if (repo / "nifty50").is_dir():
        loaded = load_nifty50_sector_csvs(repo)
        assert not loaded.empty
        assert "close" in loaded.columns
        assert loaded["index_slug"].nunique() >= 5

