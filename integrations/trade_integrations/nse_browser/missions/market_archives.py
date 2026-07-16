"""NSE market archives mission — bulk deals, delivery, PE/PB."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.archive_collector import collect_all_archive_links
from trade_integrations.nse_browser.http_bridge import HttpBridge
from trade_integrations.nse_browser.hub_writer import (
    mission_result,
    save_mission_status,
    upsert_daily_parquet,
)
from trade_integrations.nse_browser.parsers.archives import (
    merge_archive_frames,
    parse_bulk_deals_csv,
    parse_delivery_csv,
    parse_pe_pb_csv,
)
from trade_integrations.nse_browser.registry import ARCHIVE_DATASETS, get_mission, hub_root
from trade_integrations.nse_browser.repository import save_raw_file, upsert_repo_parquet
from trade_integrations.nse_browser.session import HISTORICAL_MISSION_TIMEOUT_S, NodriverSession, run_mission_async

logger = logging.getLogger(__name__)

_ARCHIVES_HUB = "https://www.nseindia.com/resources/historical-reports-capital-market-daily-monthly-archives"

_PARSERS = {
    "bulk_deals": parse_bulk_deals_csv,
    "delivery": parse_delivery_csv,
    "pe_pb": parse_pe_pb_csv,
}


async def execute_market_archives(
    session: NodriverSession | None,
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    spec = get_mission("market_archives")
    if spec is None:
        return mission_result(mission="market_archives", status="error", vendor="nse_browser", error="unknown mission")

    if backfill_historical:
        agent_fallback = True

    artifacts: list[str] = []
    manifest_rows: list[dict[str, Any]] = []
    datasets_ok = 0

    async def _with_session(active: NodriverSession) -> dict[str, Any] | None:
        nonlocal datasets_ok
        if active.captcha_detected and not active.captcha_resolved:
            payload = mission_result(
                mission="market_archives",
                status="error",
                vendor="nse_browser",
                error="captcha_detected_human_required",
                data={"human_in_loop": True},
            )
            save_mission_status("market_archives", payload)
            return payload

        link_map = await collect_all_archive_links(active)
        if agent_fallback:
            empty = [d for d, links in link_map.items() if not links]
            if empty:
                from trade_integrations.nse_browser.agent_rescue import rescue_archive_links

                discovered = await rescue_archive_links(
                    active,
                    page_url=_ARCHIVES_HUB,
                    goal="Expand archive sections and reveal CSV download links for bulk delivery pe pb",
                )
                if discovered:
                    for url in discovered:
                        for dataset in empty:
                            if dataset in url.lower() or any(k in url.lower() for k in ("bulk", "delivery", "pe", "pb")):
                                link_map.setdefault(dataset, []).append(url)
                if any(not link_map.get(d) for d in empty):
                    link_map = await collect_all_archive_links(active)

        bridge = HttpBridge(active.cookies)

        for dataset, meta in ARCHIVE_DATASETS.items():
            parser = _PARSERS[dataset]
            links = link_map.get(dataset, [])
            incoming = pd.DataFrame()
            for url in links[:10]:
                status, text = bridge.get_text(url, referer=_ARCHIVES_HUB)
                if status != 200 or not text.strip():
                    continue
                raw_path = save_raw_file(
                    text,
                    dataset=f"archives/{dataset}",
                    name=f"{dataset}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
                )
                artifacts.append(str(raw_path))
                chunk = parser(text)
                if not chunk.empty:
                    incoming = merge_archive_frames(
                        incoming,
                        chunk,
                        key_cols=["date"] if "date" in chunk.columns else [],
                    )

            if not incoming.empty:
                upsert_repo_parquet(incoming, dataset_id=dataset, source="nse_archives")
                parquet_path = hub_root() / meta["parquet_rel"]
                upsert_daily_parquet(
                    incoming,
                    path=parquet_path,
                    date_col="date" if "date" in incoming.columns else "date",
                )
                datasets_ok += 1
                manifest_rows.append(
                    {
                        "dataset": dataset,
                        "rows": len(incoming),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
        return None

    if session is not None:
        early = await _with_session(session)
        if early is not None:
            return early
    else:
        async with NodriverSession(refresh_cookies=refresh_cookies) as owned:
            early = await _with_session(owned)
            if early is not None:
                return early

    if datasets_ok == 0:
        payload = mission_result(
            mission="market_archives",
            status="partial" if agent_fallback else "error",
            vendor="nse_browser",
            error="no_archive_rows",
            artifacts=artifacts,
        )
        save_mission_status("market_archives", payload)
        return payload

    manifest = pd.DataFrame(manifest_rows)
    upsert_daily_parquet(manifest, path=spec.parquet_path, date_col="updated_at")
    payload = mission_result(
        mission="market_archives",
        status="ok" if datasets_ok == len(ARCHIVE_DATASETS) else "partial",
        vendor="nse_browser",
        rows=int(manifest["rows"].sum()) if not manifest.empty else 0,
        artifacts=artifacts,
        data={"datasets_ok": datasets_ok, "datasets": manifest_rows},
    )
    save_mission_status("market_archives", payload)
    return payload


def run_market_archives(
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    timeout = HISTORICAL_MISSION_TIMEOUT_S if backfill_historical else None
    try:
        return run_mission_async(
            execute_market_archives(
                None,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
            ),
            timeout_s=timeout,
        )
    except TimeoutError:
        payload = mission_result(
            mission="market_archives",
            status="error",
            vendor="nse_browser",
            error="mission_timeout",
        )
        save_mission_status("market_archives", payload)
        return payload
    except Exception as exc:
        logger.exception("market_archives mission failed")
        payload = mission_result(
            mission="market_archives",
            status="error",
            vendor="nse_browser",
            error=str(exc),
        )
        save_mission_status("market_archives", payload)
        return payload
