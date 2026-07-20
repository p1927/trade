"""Orchestrate NSE browser fetch, hub cache, and query for MCP / Vibe tools."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from trade_integrations.dataflows.ingest_policy import batch_ingest_allowed
from trade_integrations.nse_browser.hub_writer import (
    dataset_parquet_path,
    frame_to_records,
    is_mission_fresh,
    load_dataset_frame,
    load_mission_status,
    query_frame_by_dates,
)
from trade_integrations.nse_browser.missions import run_mission
from trade_integrations.nse_browser.registry import DATASETS, get_dataset, get_mission
from trade_integrations.nse_browser.repository import ingest_repository_to_hub, repo_root

logger = logging.getLogger(__name__)


def _default_dates(*, days: int = 30) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _needs_refresh(
    *,
    dataset_id: str,
    mission_id: str,
    frame: pd.DataFrame,
    start_date: str,
    end_date: str,
    date_col: str,
    refresh: bool,
) -> bool:
    if refresh:
        return True
    if frame.empty:
        return True
    fresh, _ = is_mission_fresh(mission_id)
    if not fresh:
        return True
    if date_col in frame.columns:
        in_range = query_frame_by_dates(
            frame,
            start_date=start_date,
            end_date=end_date,
            date_col=date_col,
            limit=10_000,
        )
        if in_range.empty:
            return True
    return False


def _build_summary(dataset_id: str, records: list[dict[str, Any]]) -> str:
    if not records:
        return f"No {dataset_id} rows in the requested range."
    if dataset_id == "fii_dii":
        latest = records[-1]
        day = latest.get("date", "?")
        fii = latest.get("fii_net")
        dii = latest.get("dii_net")
        parts = [f"FII/DII as of {day}"]
        if fii is not None:
            parts.append(f"FII net {fii:,.2f} Cr" if isinstance(fii, (int, float)) else f"FII net {fii}")
        if dii is not None:
            parts.append(f"DII net {dii:,.2f} Cr" if isinstance(dii, (int, float)) else f"DII net {dii}")
        return "; ".join(parts)
    if dataset_id == "fpi":
        latest = records[-1]
        day = latest.get("date", "?")
        eq = latest.get("fpi_equity_net_inr")
        if eq is not None:
            return f"FPI equity net INR {eq:,.2f} Cr on {day}" if isinstance(eq, (int, float)) else f"FPI on {day}"
    if dataset_id in ("mf_sebi", "fii_sebi"):
        latest = records[-1]
        day = latest.get("date", "?")
        label = "MF" if dataset_id == "mf_sebi" else "FII SEBI"
        eq = latest.get("equity_net")
        debt = latest.get("debt_net")
        parts = [f"{label} monthly as of {day}"]
        if eq is not None and isinstance(eq, (int, float)):
            parts.append(f"equity net {eq:,.2f} Cr")
        if debt is not None and isinstance(debt, (int, float)):
            parts.append(f"debt net {debt:,.2f} Cr")
        return "; ".join(parts)
    return f"{len(records)} {dataset_id} row(s) returned."


def get_nse_browser_data(
    dataset: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
    backfill_historical: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Fetch-if-stale and return parsed NSE/NSDL dataset rows from hub.

    Primary entry point for MCP and Vibe agents.
    """
    ingest_counts: dict[str, Any] = {}
    if refresh or backfill_historical or batch_ingest_allowed(explicit=False):
        ingest_counts = ingest_repository_to_hub()
    spec = get_dataset(dataset)
    if spec is None:
        return {
            "status": "error",
            "error": f"unknown dataset {dataset!r}",
            "available": list(DATASETS.keys()),
            "aliases_hint": "fii, dii, fpi, nsdl, mf_sebi, fii_sebi, bulk_deals, delivery, pe_pb",
        }

    default_start, default_end = _default_dates()
    start = (start_date or default_start)[:10]
    end = (end_date or default_end)[:10]
    mission = get_mission(spec.mission_id)
    if mission is None:
        return {"status": "error", "error": f"mission not found for {spec.id}"}

    frame = load_dataset_frame(spec.id)
    source = "hub_cache"
    mission_result: dict[str, Any] = {}

    force_fetch = refresh or backfill_historical
    if _needs_refresh(
        dataset_id=spec.id,
        mission_id=spec.mission_id,
        frame=frame,
        start_date=start,
        end_date=end,
        date_col=spec.date_col,
        refresh=force_fetch,
    ):
        logger.info(
            "Refreshing nse_browser mission %s for dataset %s (backfill=%s)",
            spec.mission_id,
            spec.id,
            backfill_historical,
        )
        mission_result = run_mission(
            spec.mission_id,
            refresh_cookies=refresh_cookies,
            agent_fallback=agent_fallback,
            backfill_historical=backfill_historical,
        )
        ingest_repository_to_hub()
        source = "fresh_fetch"
        frame = load_dataset_frame(spec.id)
    elif ingest_counts.get(spec.id):
        source = "repo_ingest"
        frame = load_dataset_frame(spec.id)

    queried = query_frame_by_dates(
        frame,
        start_date=start,
        end_date=end,
        date_col=spec.date_col,
        limit=limit,
    )
    records = frame_to_records(queried)

    fresh, fetched_at = is_mission_fresh(spec.mission_id)
    status_payload = load_mission_status(spec.mission_id)
    human_in_loop = status_payload.get("error") == "captcha_unresolved" or bool(
        (status_payload.get("data") or {}).get("human_in_loop")
    )

    parquet_path = dataset_parquet_path(spec.id)
    hub_paths = {"parquet": str(parquet_path) if parquet_path else None}

    if not records and mission_result.get("status") not in ("ok", "partial"):
        err = mission_result.get("error") or status_payload.get("error") or "no_rows_in_range"
        out: dict[str, Any] = {
            "status": "error",
            "dataset": spec.id,
            "mission": spec.mission_id,
            "source": source,
            "freshness": {"fetched_at": fetched_at, "stale": not fresh},
            "date_range": {"start": start, "end": end},
            "row_count": 0,
            "records": [],
            "summary": f"No data for {spec.label} between {start} and {end}.",
            "mission_result": mission_result or status_payload,
            "hub_paths": hub_paths,
            "error": err,
        }
        if human_in_loop:
            out["human_in_loop"] = True
            out["hint"] = "Complete CAPTCHA in headed Chrome and retry with refresh_cookies=True"
        return out

    mission_status = mission_result.get("status") or status_payload.get("status") or "ok"
    overall_status = "ok" if records else ("partial" if mission_status == "partial" else "error")

    return {
        "status": overall_status,
        "dataset": spec.id,
        "mission": spec.mission_id,
        "source": source,
        "freshness": {"fetched_at": fetched_at, "stale": not fresh},
        "date_range": {"start": start, "end": end},
        "row_count": len(records),
        "records": records,
        "summary": _build_summary(spec.id, records),
        "mission_result": mission_result or status_payload,
        "hub_paths": hub_paths,
        "repo_ingest": ingest_counts,
        "error": "" if records else (mission_result.get("error") or ""),
    }


def ingest_nse_repository() -> dict[str, Any]:
    """Sync git-tracked data/nse parquet into hub without browser fetch."""
    counts = ingest_repository_to_hub()
    return {
        "status": "ok" if counts else "partial",
        "ingested": counts,
        "repo_root": str(repo_root()),
    }
