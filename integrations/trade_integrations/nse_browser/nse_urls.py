"""Canonical NSE / NSDL URLs and API endpoints for browser + HTTP fetch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["nse", "nsdl", "archive"]


@dataclass(frozen=True)
class NseEndpoint:
    key: str
    url: str
    kind: SourceKind
    label: str
    referer: str = ""
    content_type: str = "auto"  # json | csv | html | auto


NSE_HOME = "https://www.nseindia.com"

# --- FII / DII ---
FII_DII_REPORT_PAGE = "https://www.nseindia.com/reports/fii-dii"
FII_DII_HISTORICAL_PAGE = "https://www.nseindia.com/all-reports/historical-equities-fii-fpi-dii-trading-activity"
FII_DII_API_JSON = f"{NSE_HOME}/api/fiidiiTradeReact"
FII_DII_API_CSV = f"{FII_DII_API_JSON}?csv=true"
FII_DII_API_CSV_CANDIDATES = (
    f"{NSE_HOME}/api/fiidiiTradeCSV",
    f"{NSE_HOME}/api/fiidiiTradeReactCSV",
    f"{NSE_HOME}/api/fiidii-trade-csv",
)

# --- F&O participant OI (archives used by existing backfill) ---
FAO_PARTICIPANT_OI_ARCHIVE = (
    "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv",
    "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv",
)

# --- NSDL FPI ---
NSDL_FPI_LATEST = "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx"
NSDL_FPI_MONTHLY = "https://www.fpi.nsdl.co.in/web/Reports/Monthly.aspx"
NSDL_FPI_ARCHIVE = "https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx"

# --- Historical market reports hub ---
NSE_HISTORICAL_REPORTS_HUB = (
    "https://www.nseindia.com/resources/historical-reports-capital-market-daily-monthly-archives"
)
NSE_HISTORICAL_REPORTS = (NSE_HISTORICAL_REPORTS_HUB,)
NSE_BULK_BLOCK_ARCHIVES = f"{NSE_HOME}/report-details/bulk-block-deals-on-nse"
NSE_DELIVERY_ARCHIVES = f"{NSE_HOME}/all-reports"
NSE_PE_PB_PAGE = f"{NSE_HOME}/market-data/live-market-indices"

ENDPOINTS: dict[str, NseEndpoint] = {
    "fii_dii_page": NseEndpoint(
        "fii_dii_page", FII_DII_REPORT_PAGE, "nse", "FII/DII report page", referer=NSE_HOME
    ),
    "fii_dii_json": NseEndpoint(
        "fii_dii_json",
        FII_DII_API_JSON,
        "nse",
        "FII/DII JSON (today)",
        referer=FII_DII_REPORT_PAGE,
        content_type="json",
    ),
    "nsdl_fpi_latest": NseEndpoint(
        "nsdl_fpi_latest", NSDL_FPI_LATEST, "nsdl", "NSDL FPI latest", content_type="html"
    ),
    "nsdl_fpi_monthly": NseEndpoint(
        "nsdl_fpi_monthly", NSDL_FPI_MONTHLY, "nsdl", "NSDL FPI monthly", content_type="html"
    ),
    "historical_reports": NseEndpoint(
        "historical_reports", NSE_HISTORICAL_REPORTS[0], "archive", "NSE historical reports hub"
    ),
    "bulk_block": NseEndpoint(
        "bulk_block", NSE_BULK_BLOCK_ARCHIVES, "archive", "Bulk/block deals archives"
    ),
    "pe_pb": NseEndpoint("pe_pb", NSE_PE_PB_PAGE, "archive", "Index PE/PB"),
}


def all_mission_urls(mission_id: str) -> list[str]:
    """Return ordered URL list for a mission."""
    if mission_id == "fii_dii_history":
        return [FII_DII_REPORT_PAGE, FII_DII_HISTORICAL_PAGE, FII_DII_API_JSON, FII_DII_API_CSV, *FII_DII_API_CSV_CANDIDATES]
    if mission_id == "fpi_nsdl":
        return [NSDL_FPI_LATEST, NSDL_FPI_MONTHLY, NSDL_FPI_ARCHIVE]
    if mission_id == "market_archives":
        return [
            NSE_HISTORICAL_REPORTS[0],
            NSE_BULK_BLOCK_ARCHIVES,
            NSE_DELIVERY_ARCHIVES,
            NSE_PE_PB_PAGE,
        ]
    return []
