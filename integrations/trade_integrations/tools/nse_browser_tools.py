"""Vibe / TradingAgents tools for NSE browser fetch module."""

from __future__ import annotations

import json
from typing import Annotated, Any

from trade_integrations.nse_browser.hub_writer import (
    is_mission_fresh,
    load_archive_dataset,
    load_fii_dii_daily,
    load_fpi_daily,
    load_mission_status,
)
from trade_integrations.nse_browser.missions import list_missions, run_mission
from trade_integrations.nse_browser.orchestrator import get_nse_browser_data as _orchestrate_data
from trade_integrations.nse_browser.orchestrator import ingest_nse_repository as _ingest_repo
from trade_integrations.nse_browser.registry import DATASETS, MISSIONS, get_dataset


def query_nse_browser_data(
    dataset: str,
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
    backfill_historical: bool = False,
    limit: int = 500,
) -> str:
    """
    Callable JSON API for MCP — fetch NSE/NSDL dataset rows from hub (fetch-if-stale).

    Prefer this from MCP; use get_nse_browser_data for LangChain tool registration.
    """
    result = _orchestrate_data(
        dataset,
        start_date=start_date,
        end_date=end_date,
        refresh=refresh,
        refresh_cookies=refresh_cookies,
        agent_fallback=agent_fallback,
        backfill_historical=backfill_historical,
        limit=limit,
    )
    return json.dumps(result, indent=2, default=str)


def query_ingest_nse_repository() -> str:
    """Callable JSON API for MCP — sync data/nse repo parquet into hub."""
    return json.dumps(_ingest_repo(), indent=2, default=str)


def query_nse_browser_status() -> str:
    """Callable JSON API for MCP — hub cache status for all datasets."""
    return _build_status_json()


def _build_status_json() -> str:
    from trade_integrations.nse_browser.agent_rescue import rescue_status

    payload: dict[str, Any] = {
        "missions": list_missions(),
        "datasets": list(DATASETS.keys()),
        "rescue": rescue_status(),
        "dataset_status": {did: _dataset_status(did) for did in DATASETS},
        "mission_status": {mid: load_mission_status(mid) for mid in MISSIONS},
        "preferred_tool": "get_nse_browser_data",
        "agentic_tool": "run_browser_task",
    }
    return json.dumps(payload, indent=2, default=str)


def query_run_browser_task(
    goal: str,
    start_urls: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    max_steps: int = 50,
    persist: bool = True,
) -> str:
    """Run an agentic Skyvern browse/extract task (ad-hoc web research)."""
    from trade_integrations.nse_browser.skyvern_bridge import run_skyvern_task

    url = None
    if start_urls:
        url = start_urls[0] if isinstance(start_urls, list) else str(start_urls)
    max_wait = max(60, min(max_steps * 6, 600))
    result = run_skyvern_task(
        goal,
        url=url,
        output_schema=output_schema,
        max_wait_s=max_wait,
        persist=persist,
    )
    return json.dumps(result, indent=2, default=str)


def get_nse_browser_data(
    dataset: Annotated[
        str,
        "Dataset: fii_dii | fpi | bulk_deals | delivery | pe_pb (aliases: fii, dii, nsdl, bulk)",
    ],
    start_date: Annotated[str | None, "Start date YYYY-MM-DD; default ~30 days ago"] = None,
    end_date: Annotated[str | None, "End date YYYY-MM-DD; default today"] = None,
    refresh: Annotated[bool, "Force browser fetch even if hub cache is fresh"] = False,
    refresh_cookies: Annotated[bool, "Bootstrap nodriver cookies before fetch"] = False,
    agent_fallback: Annotated[bool, "Skyvern then MiniMax rescue when page navigation fails"] = True,
    backfill_historical: Annotated[
        bool, "Full historical backfill via CSV download + archives (headed browser, ~120s)"
    ] = False,
    limit: Annotated[int, "Max rows to return"] = 500,
) -> str:
    """
    Fetch NSE/NSDL data not available via simple APIs.

    Covers FII/DII cash flows, NSDL FPI breakdown, and NSE archive CSVs (bulk deals,
    delivery, PE/PB). Reads hub cache first; browses NSE/NSDL only when stale or
    refresh=True. Returns parsed rows and persists to hub parquet.

    Agent routing:
    - FII/DII / institutional flows → dataset=\"fii_dii\"
    - FPI / NSDL foreign portfolio → dataset=\"fpi\"
    - Bulk/block deals → dataset=\"bulk_deals\"
    - Delivery data → dataset=\"delivery\"
    - Index PE/PB → dataset=\"pe_pb\"
    """
    return query_nse_browser_data(
        dataset,
        start_date=start_date,
        end_date=end_date,
        refresh=refresh,
        refresh_cookies=refresh_cookies,
        agent_fallback=agent_fallback,
        backfill_historical=backfill_historical,
        limit=limit,
    )


def ingest_nse_repository() -> str:
    """Refresh hub from git-tracked data/nse parquet without browser fetch."""
    return query_ingest_nse_repository()


def fetch_nse_browser_data(
    mission: Annotated[str, "Mission id: fii_dii_history, fpi_nsdl, or market_archives"],
    *,
    refresh: bool = False,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
    include_records: bool = False,
    dataset: str | None = None,
) -> str:
    """
    Low-level mission runner. Prefer get_nse_browser_data for agent use.

    When refresh=False and include_records=True, returns cached hub rows if available.
    """
    mission_id = mission.strip()
    if mission_id not in MISSIONS:
        return json.dumps(
            {
                "status": "error",
                "error": f"unknown mission {mission_id!r}",
                "available": list(MISSIONS.keys()),
            },
            indent=2,
        )
    if refresh:
        result = run_mission(
            mission_id,
            refresh_cookies=refresh_cookies,
            agent_fallback=agent_fallback,
            backfill_historical=backfill_historical,
        )
        if include_records and dataset:
            data = json.loads(query_nse_browser_data(dataset, refresh=False))
            result = {**result, "records": data.get("records", []), "row_count": data.get("row_count", 0)}
        return json.dumps(result, indent=2, default=str)

    if include_records and dataset:
        return query_nse_browser_data(dataset, refresh=False)

    cached = load_mission_status(mission_id)
    if cached:
        return json.dumps(cached, indent=2, default=str)
    return json.dumps(
        {
            "status": "missing",
            "mission": mission_id,
            "hint": "Re-run with refresh=True or use get_nse_browser_data(refresh=True)",
        },
        indent=2,
    )


def _dataset_status(dataset_id: str) -> dict[str, Any]:
    spec = get_dataset(dataset_id)
    if spec is None:
        return {}
    if spec.id == "fii_dii":
        frame = load_fii_dii_daily()
    elif spec.id == "fpi":
        frame = load_fpi_daily()
    else:
        frame = load_archive_dataset(spec.id)
    fresh, fetched_at = is_mission_fresh(spec.mission_id)
    status = load_mission_status(spec.mission_id)
    return {
        "label": spec.label,
        "mission": spec.mission_id,
        "rows": len(frame),
        "start": str(frame["date"].min()) if not frame.empty and "date" in frame.columns else None,
        "end": str(frame["date"].max()) if not frame.empty and "date" in frame.columns else None,
        "fresh": fresh,
        "fetched_at": fetched_at,
        "last_status": status.get("status"),
        "last_error": status.get("error") or "",
    }


def get_nse_browser_status() -> str:
    """Return hub cache status for all nse_browser missions and datasets."""
    return _build_status_json()


def run_browser_task(
    goal: Annotated[str, "Natural-language browse/extract objective"],
    start_urls: Annotated[list[str] | None, "Optional entry URLs"] = None,
    output_schema: Annotated[dict[str, Any] | None, "JSON schema for structured extract"] = None,
    max_steps: Annotated[int, "Agent step budget (maps to timeout)"] = 50,
    persist: Annotated[bool, "Save result to hub tasks/"] = True,
) -> str:
    """
    Agentic web research via Skyvern (falls back to error if Skyvern unavailable).

    Use for ad-hoc research: RBI/SEBI filings, event pages, macro data, any public URL.
    For preset India datasets (FII/DII, FPI, archives) prefer get_nse_browser_data.
    """
    return query_run_browser_task(
        goal,
        start_urls=start_urls,
        output_schema=output_schema,
        max_steps=max_steps,
        persist=persist,
    )


try:
    from langchain_core.tools import tool as _lc_tool

    get_nse_browser_data = _lc_tool(get_nse_browser_data)
    get_nse_browser_status = _lc_tool(get_nse_browser_status)
except ImportError:
    pass
