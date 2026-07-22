"""Paths for Hugging Face india-index-options-1m replay bundle."""

from __future__ import annotations

from pathlib import Path

_HF_DIRNAME = "hf-india-index-options-1m"

_INDEX_ALIASES: dict[tuple[str, str], str] = {
    ("NIFTY", "NSE_INDEX"): "NIFTY",
    ("NIFTY 50", "NSE_INDEX"): "NIFTY",
    ("NIFTY50", "NSE_INDEX"): "NIFTY",
    ("BANKNIFTY", "NSE_INDEX"): "BANKNIFTY",
    ("NIFTY BANK", "NSE_INDEX"): "BANKNIFTY",
    ("SENSEX", "BSE_INDEX"): "SENSEX",
    ("SENSEX", "NSE_INDEX"): "SENSEX",
}


def hf_replay_root(data_root: Path) -> Path:
    return data_root / "replay" / _HF_DIRNAME


def index_slug(symbol: str, exchange: str) -> str | None:
    key = (symbol.strip().upper(), exchange.strip().upper())
    return _INDEX_ALIASES.get(key)


def index_parquet_path(data_root: Path, slug: str) -> Path:
    return hf_replay_root(data_root) / "index" / f"{slug}.parquet"


def options_dir(data_root: Path, slug: str) -> Path:
    return hf_replay_root(data_root) / "options" / slug
