"""Lightweight repo ↔ hub consistency checks without full re-ingest."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_KEY_DATASETS = ("fii_dii", "fii_sebi", "mf_sebi", "sector_indices", "fpi")


def run_repo_consistency_check(*, trigger_reingest_on_drift: bool = False) -> dict[str, Any]:
    """Compare repo manifest fingerprint and row counts against hub mirrors."""
    from trade_integrations.nse_browser.hub_writer import load_dataset_frame
    from trade_integrations.nse_browser.repository import (
        ingest_repository_to_hub,
        load_repo_dataset,
        repo_hub_sync_fingerprint,
        sync_light_repo_seed_layers,
    )
    from trade_integrations.nse_browser.repository import _load_manifest

    manifest = _load_manifest()
    current_fp = repo_hub_sync_fingerprint()
    hub_sync = manifest.get("hub_sync") if isinstance(manifest.get("hub_sync"), dict) else {}
    stored_fp = hub_sync.get("fingerprint")

    drift: list[dict[str, Any]] = []
    if stored_fp and stored_fp != current_fp:
        drift.append(
            {
                "kind": "hub_sync_fingerprint",
                "stored": stored_fp,
                "current": current_fp,
            }
        )
    elif not stored_fp:
        drift.append({"kind": "hub_sync_missing", "current": current_fp})

    for dataset_id in _KEY_DATASETS:
        repo_frame = load_repo_dataset(dataset_id)
        hub_frame = load_dataset_frame(dataset_id)
        repo_rows = len(repo_frame)
        hub_rows = len(hub_frame)
        if repo_rows != hub_rows:
            drift.append(
                {
                    "kind": "row_count",
                    "dataset": dataset_id,
                    "repo_rows": repo_rows,
                    "hub_rows": hub_rows,
                }
            )
            continue
        if not repo_frame.empty and "date" in repo_frame.columns:
            repo_end = str(repo_frame["date"].astype(str).str[:10].max())
            hub_end = (
                str(hub_frame["date"].astype(str).str[:10].max())
                if not hub_frame.empty and "date" in hub_frame.columns
                else None
            )
            if hub_end and repo_end != hub_end:
                drift.append(
                    {
                        "kind": "date_range",
                        "dataset": dataset_id,
                        "repo_end": repo_end,
                        "hub_end": hub_end,
                    }
                )

    status = "ok" if not drift else "drift"
    result: dict[str, Any] = {
        "status": status,
        "drift": drift,
        "fingerprint": current_fp,
        "hub_sync_fingerprint": stored_fp,
    }

    if drift and trigger_reingest_on_drift:
        logger.warning("repo consistency drift detected; running light re-sync")
        seed = sync_light_repo_seed_layers(allow_live_fetch=False)
        hub = ingest_repository_to_hub(skip_repo_sync=True, force_hub_mirror=True)
        result["reingest"] = {"seed_layers": seed, "hub": hub}
        result["status"] = "repaired" if hub else status

    return result
