"""Configuration for datasets/* GitHub macro repositories."""

from __future__ import annotations

import os
from pathlib import Path

# Curated from https://github.com/awesomedata/awesome-public-datasets (Finance/Economics)
# and https://github.com/datasets — all free, structured CSV.
DATASETS: tuple[dict[str, str | tuple[tuple[str, str], ...]], ...] = (
    {
        "key": "us_10y",
        "repo": "datasets/bond-yields-us-10y",
        "branch": "main",
        "path": "data/monthly.csv",
        "source_url": "https://github.com/datasets/bond-yields-us-10y",
    },
    {
        "key": "gold",
        "repo": "datasets/gold-prices",
        "branch": "main",
        "path": "data/monthly.csv",
        "source_url": "https://github.com/datasets/gold-prices",
    },
    {
        "key": "exchange_rates_daily",
        "repo": "datasets/exchange-rates",
        "branch": "main",
        "path": "data/daily.csv",
        "source_url": "https://github.com/datasets/exchange-rates",
    },
    {
        "key": "vix_daily",
        "repo": "datasets/finance-vix",
        "branch": "main",
        "path": "data/vix-daily.csv",
        "source_url": "https://github.com/datasets/finance-vix",
    },
    {
        "key": "oil_brent_daily",
        "repo": "datasets/oil-prices",
        "branch": "main",
        "path": "data/brent-daily.csv",
        "source_url": "https://github.com/datasets/oil-prices",
    },
    {
        "key": "oil_wti_daily",
        "repo": "datasets/oil-prices",
        "branch": "main",
        "path": "data/wti-daily.csv",
        "source_url": "https://github.com/datasets/oil-prices",
    },
    {
        "key": "us_cpi",
        "repo": "datasets/cpi-us",
        "branch": "main",
        "path": "data/cpiai.csv",
        "source_url": "https://github.com/datasets/cpi-us",
    },
    {
        "key": "us_gdp_quarter",
        "repo": "datasets/gdp-us",
        "branch": "main",
        "path": "data/quarter.csv",
        "source_url": "https://github.com/datasets/gdp-us",
    },
)

HUB_SUBDIR = "_data/macro/github_datasets"

# Below yfinance (40) — fills history; yfinance wins on overlap.
SOURCE_NAME = "github_datasets"


def raw_url(repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def _trade_stack_root() -> Path:
    if custom := os.environ.get("TRADE_STACK_ROOT", "").strip():
        return Path(custom).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def cache_dir() -> Path:
    if custom := os.environ.get("GITHUB_DATASETS_CACHE", "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "data" / "github_datasets" / "raw"


def hub_data_dir() -> Path:
    from trade_integrations.context.hub import get_hub_dir

    return get_hub_dir() / HUB_SUBDIR
