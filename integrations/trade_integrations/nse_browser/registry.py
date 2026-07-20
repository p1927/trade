"""Mission catalog — declarative fetch specs for NSE/NSDL browser module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from trade_integrations.context.hub import get_hub_dir

MissionId = Literal["fii_dii_history", "fpi_nsdl", "market_archives"]

_HUB_REL = Path("_data") / "nse_browser"


@dataclass(frozen=True)
class MissionSpec:
    """One fetch mission with hub paths and behavior flags."""

    id: MissionId
    label: str
    source_urls: tuple[str, ...]
    parquet_rel: str
    raw_subdir: str
    adaptive: bool = False
    freshness_hours: int = 24
    description: str = ""

    @property
    def parquet_path(self) -> Path:
        return get_hub_dir() / _HUB_REL / self.parquet_rel

    @property
    def raw_dir(self) -> Path:
        return get_hub_dir() / _HUB_REL / "raw" / self.raw_subdir

    @property
    def status_path(self) -> Path:
        return get_hub_dir() / _HUB_REL / "status" / f"{self.id}.json"


MISSIONS: dict[str, MissionSpec] = {
    "fii_dii_history": MissionSpec(
        id="fii_dii_history",
        label="FII/DII cash history",
        source_urls=(
            "https://www.nseindia.com/reports/fii-dii",
            "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/",
            "https://niftyinvest.com/fii-dii-data/fii-history",
        ),
        parquet_rel="fii_dii_daily.parquet",
        raw_subdir="fii_dii",
        adaptive=False,
        freshness_hours=18,
        description="NSE FII/FPI & DII trading activity CSV (NSE-only + combined)",
    ),
    "fpi_nsdl": MissionSpec(
        id="fpi_nsdl",
        label="NSDL FPI investment activity",
        source_urls=(
            "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx",
            "https://www.fpi.nsdl.co.in/web/Reports/Monthly.aspx",
        ),
        parquet_rel="fpi_daily.parquet",
        raw_subdir="fpi_nsdl",
        adaptive=False,
        freshness_hours=24,
        description="Foreign portfolio investment debt/equity/hybrid from NSDL",
    ),
    "market_archives": MissionSpec(
        id="market_archives",
        label="NSE market archives",
        source_urls=(
            "https://www.nseindia.com/resources/historical-reports-capital-market-daily-monthly-archives",
        ),
        parquet_rel="archives/manifest.parquet",
        raw_subdir="archives",
        adaptive=True,
        freshness_hours=168,
        description="Bulk deals, delivery, PE/PB and related NSE historical reports",
    ),
}

ARCHIVE_DATASETS: dict[str, dict[str, Any]] = {
    "bulk_deals": {
        "label": "Bulk / block deals",
        "keywords": ("bulk", "block deal"),
        "parquet_rel": "archives/bulk_deals.parquet",
    },
    "delivery": {
        "label": "Delivery position",
        "keywords": ("delivery", "deliverable"),
        "parquet_rel": "archives/delivery.parquet",
    },
    "pe_pb": {
        "label": "P/E P/B dividend yield",
        "keywords": ("p/e", "pe ratio", "p/b", "div yield"),
        "parquet_rel": "archives/pe_pb.parquet",
    },
}

# Canonical dataset ids exposed via MCP / orchestrator
DatasetId = Literal[
    "fii_dii",
    "fpi",
    "mf_sebi",
    "fii_sebi",
    "bulk_deals",
    "delivery",
    "pe_pb",
    "sector_indices",
]

DATASET_ALIASES: dict[str, DatasetId] = {
    "fii_dii": "fii_dii",
    "fii": "fii_dii",
    "dii": "fii_dii",
    "fiidii": "fii_dii",
    "fii-dii": "fii_dii",
    "foreign_institutional": "fii_dii",
    "institutional_flows": "fii_dii",
    "fpi": "fpi",
    "nsdl": "fpi",
    "nsdl_fpi": "fpi",
    "foreign_portfolio": "fpi",
    "mf_sebi": "mf_sebi",
    "mf": "mf_sebi",
    "mutual_fund": "mf_sebi",
    "mutual_fund_sebi": "mf_sebi",
    "fii_sebi": "fii_sebi",
    "fpi_sebi": "fii_sebi",
    "fii_equity_debt": "fii_sebi",
    "bulk_deals": "bulk_deals",
    "bulk": "bulk_deals",
    "block_deals": "bulk_deals",
    "block": "bulk_deals",
    "delivery": "delivery",
    "deliverable": "delivery",
    "pe_pb": "pe_pb",
    "pe": "pe_pb",
    "pb": "pe_pb",
    "pe_ratio": "pe_pb",
    "sector_indices": "sector_indices",
    "sector_index": "sector_indices",
    "sector": "sector_indices",
    "nifty_sector": "sector_indices",
}


@dataclass(frozen=True)
class DatasetSpec:
    """Maps a user-facing dataset to mission + hub parquet path."""

    id: DatasetId
    mission_id: MissionId
    parquet_rel: str
    label: str
    date_col: str = "date"


DATASETS: dict[str, DatasetSpec] = {
    "fii_dii": DatasetSpec(
        id="fii_dii",
        mission_id="fii_dii_history",
        parquet_rel="fii_dii_daily.parquet",
        label="FII/DII cash flows",
    ),
    "fpi": DatasetSpec(
        id="fpi",
        mission_id="fpi_nsdl",
        parquet_rel="fpi_daily.parquet",
        label="NSDL FPI investment activity",
    ),
    "mf_sebi": DatasetSpec(
        id="mf_sebi",
        mission_id="fii_dii_history",
        parquet_rel="mf_sebi_monthly.parquet",
        label="Mutual fund SEBI monthly flows (equity + debt)",
    ),
    "fii_sebi": DatasetSpec(
        id="fii_sebi",
        mission_id="fii_dii_history",
        parquet_rel="fii_sebi_monthly.parquet",
        label="FII/FPI SEBI monthly flows (equity + debt)",
    ),
    "bulk_deals": DatasetSpec(
        id="bulk_deals",
        mission_id="market_archives",
        parquet_rel="archives/bulk_deals.parquet",
        label="Bulk / block deals",
    ),
    "delivery": DatasetSpec(
        id="delivery",
        mission_id="market_archives",
        parquet_rel="archives/delivery.parquet",
        label="Delivery position",
    ),
    "pe_pb": DatasetSpec(
        id="pe_pb",
        mission_id="market_archives",
        parquet_rel="archives/pe_pb.parquet",
        label="Index P/E P/B",
    ),
    "sector_indices": DatasetSpec(
        id="sector_indices",
        mission_id="market_archives",
        parquet_rel="sector_index_daily.parquet",
        label="NSE sector index OHLC (nifty50 CSV archive)",
    ),
}


def resolve_dataset(name: str) -> DatasetSpec | None:
    """Resolve dataset id or alias to canonical DatasetSpec."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    canonical = DATASET_ALIASES.get(key)
    if canonical is None:
        canonical = key if key in DATASETS else None
    if canonical is None:
        return None
    return DATASETS.get(canonical)


def get_dataset(dataset_id: str) -> DatasetSpec | None:
    return resolve_dataset(dataset_id)


def get_mission(mission_id: str) -> MissionSpec | None:
    return MISSIONS.get(mission_id.strip())


def hub_root() -> Path:
    return get_hub_dir() / _HUB_REL


def session_dir() -> Path:
    return hub_root() / "session"


def cookies_path() -> Path:
    return session_dir() / "cookies.json"


def downloads_dir() -> Path:
    return hub_root() / "raw" / "downloads"
