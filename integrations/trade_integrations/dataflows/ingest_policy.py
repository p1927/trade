"""Ingest policy — expensive batch vs real-time merge (no bloat).

- **Real-time** paths (quotes, live news ingest, verified hub rows): always merge with
  existing data; never full-replace without dedupe keys.
- **Batch / expensive** backfills (GitHub datasets, historic folder, curated runs):
  run only when ``explicit=True`` (user script, CLI, or API flag) — never as a
  side effect of a read/query path.
"""

from __future__ import annotations

import os
from typing import Literal

IngestKind = Literal["realtime", "batch"]


def ingest_kind_for_dataset(dataset: str) -> IngestKind:
    """Classify dataset writes for merge vs batch-only policy."""
    stem = str(dataset or "").strip().lower()
    if stem in {
        "flow_cash_daily",
        "flow_derivatives_daily",
        "macro_daily",
        "india_vix_daily",
        "nifty_ohlcv_daily",
    }:
        return "realtime"
    if stem.startswith("github_") or stem.startswith("nifty50_intraday"):
        return "batch"
    if "historic" in stem or stem.endswith("_backfill"):
        return "batch"
    return "realtime"


def batch_ingest_allowed(*, explicit: bool = False) -> bool:
    """Return True when an expensive batch ingest may run."""
    if explicit:
        return True
    return os.getenv("TRADE_ALLOW_BATCH_INGEST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_explicit_batch(*, explicit: bool, operation: str) -> None:
    """Raise when a batch ingest is attempted without explicit user intent."""
    if batch_ingest_allowed(explicit=explicit):
        return
    raise RuntimeError(
        f"Batch ingest blocked for {operation} — pass explicit=True or set "
        "TRADE_ALLOW_BATCH_INGEST=1 for scripted runs"
    )


def merge_on_save_default(dataset: str) -> bool:
    """Real-time datasets merge with cold tier; batch callers merge explicitly."""
    return ingest_kind_for_dataset(dataset) == "realtime"
