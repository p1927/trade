"""NSE browser mission adapter for flows domain."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.data_router.adapters.ohlcv import AdapterError
from trade_integrations.data_router.types import FetchSpec

logger = logging.getLogger(__name__)


def fetch_flows(source_id: str, spec: FetchSpec) -> pd.DataFrame:
    """Run an NSE mission and return normalized flow rows when available."""
    if source_id.strip().lower() != "nse_browser":
        raise AdapterError(f"unsupported flows source {source_id}", reason="not_configured")

    mission_id = str(spec.extra.get("mission_id") or spec.extra.get("dataset_id") or "fii_dii_history")
    refresh = bool(spec.extra.get("refresh_cookies", False))
    backfill = bool(spec.extra.get("backfill_historical", False))

    try:
        from trade_integrations.nse_browser.missions import run_mission

        result = run_mission(
            mission_id,
            refresh_cookies=refresh,
            agent_fallback=bool(spec.extra.get("agent_fallback", False)),
            backfill_historical=backfill,
        )
    except Exception as exc:
        raise AdapterError(str(exc), reason="error") from exc

    status = str(result.get("status") or "")
    if status not in ("ok", "partial"):
        err = str(result.get("error") or status or "mission failed")
        raise AdapterError(err, reason="no_data")

    dataset_id = str(spec.extra.get("dataset_id") or _mission_dataset_id(mission_id))
    frame = _load_hub_flows(dataset_id)
    if frame is None or frame.empty:
        raise AdapterError(f"mission ok but no hub rows for {dataset_id}", reason="no_data")
    return frame


def _mission_dataset_id(mission_id: str) -> str:
    mapping = {
        "fii_dii_history": "fii_dii",
        "fpi_nsdl": "fpi",
        "market_archives": "archives",
    }
    return mapping.get(mission_id.strip(), mission_id.strip())


def _load_hub_flows(dataset_id: str) -> pd.DataFrame:
    try:
        from trade_integrations.nse_browser.hub_writer import load_dataset_frame

        frame = load_dataset_frame(dataset_id)
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    except Exception as exc:
        logger.debug("hub flows load failed for %s: %s", dataset_id, exc)
        return pd.DataFrame()


def mirror_flows_to_normalized(spec: FetchSpec, frame: pd.DataFrame, *, source: str) -> str | None:
    """Copy mission output into datasets/flows/ mirror."""
    from trade_integrations.data_router import normalized_store

    return normalized_store.write_flows(spec, frame, source=source)
