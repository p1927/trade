"""Configuration for Nifty 100 financial intelligence GitHub source."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_REPO = "Samadhan1904/nifty100-financial-intelligence"
DEFAULT_BRANCH = "main"

RAW_FILES: tuple[tuple[str, str], ...] = (
    ("data/raw/companies.xlsx", "companies"),
    ("data/raw/profitandloss.xlsx", "profitandloss"),
    ("data/raw/balancesheet.xlsx", "balancesheet"),
    ("data/raw/cashflow.xlsx", "cashflow"),
    ("data/raw/analysis.xlsx", "analysis"),
    ("data/raw/prosandcons.xlsx", "prosandcons"),
    ("data/raw/documents.xlsx", "documents"),
)

HUB_SUBDIR = "_data/fundamentals/nifty100"


def github_repo() -> str:
    return os.environ.get("NIFTY100_FININTEL_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO


def github_branch() -> str:
    return os.environ.get("NIFTY100_FININTEL_BRANCH", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH


def raw_url(rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{github_repo()}/{github_branch()}/{rel_path}"


def _trade_stack_root() -> Path:
    if custom := os.environ.get("TRADE_STACK_ROOT", "").strip():
        return Path(custom).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def cache_dir() -> Path:
    if custom := os.environ.get("NIFTY100_FININTEL_CACHE", "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "data" / "nifty100_financial_intel" / "raw"


def hub_data_dir() -> Path:
    from trade_integrations.context.hub import get_hub_dir

    return get_hub_dir() / HUB_SUBDIR
