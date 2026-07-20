"""NSDL FPI mission — nselib first, nodriver + monthly/archive pages."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

from trade_integrations.nse_browser.direct_fetch import fetch_nselib_fpi_latest
from trade_integrations.nse_browser.http_bridge import HttpBridge
from trade_integrations.nse_browser.hub_writer import (
    mission_result,
    save_mission_status,
    upsert_daily_parquet,
)
from trade_integrations.nse_browser.nse_urls import NSDL_FPI_ARCHIVE, NSDL_FPI_LATEST, NSDL_FPI_MONTHLY
from trade_integrations.nse_browser.parsers.fpi import (
    aggregate_fpi_daily,
    parse_fpi_html_tables,
    parse_fpi_investment_table,
)
from trade_integrations.nse_browser.registry import get_mission
from trade_integrations.nse_browser.repository import save_raw_file, upsert_repo_parquet
from trade_integrations.nse_browser.session import (
    HISTORICAL_MISSION_TIMEOUT_S,
    NodriverSession,
    run_mission_async,
)

logger = logging.getLogger(__name__)


def _fetch_via_nselib() -> tuple[pd.DataFrame, str]:
    frame = fetch_nselib_fpi_latest()
    if frame.empty:
        return frame, "nselib_empty"
    return frame, "nselib"


async def _fetch_via_browser_session(
    session: NodriverSession,
    *,
    backfill_historical: bool,
) -> tuple[pd.DataFrame, list[str], str, str]:
    spec = get_mission("fpi_nsdl")
    artifacts: list[str] = []
    frames: list[pd.DataFrame] = []
    html = ""
    visible = ""

    pages = [NSDL_FPI_LATEST]
    if backfill_historical:
        pages.extend([NSDL_FPI_MONTHLY, NSDL_FPI_ARCHIVE])

    for page_url in pages:
        html = await session.goto(page_url)
        visible = session.last_visible_text
        if session.captcha_detected and not session.captcha_resolved:
            continue
        if html:
            slug = page_url.rstrip("/").split("/")[-1].replace(".aspx", "")
            save_raw_file(html, dataset="fpi", name=f"nsdl_{slug}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.html")
            artifacts.append(str(spec.raw_dir / f"nsdl_{slug}.html"))
        frame = parse_fpi_html_tables(html)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        bridge = HttpBridge(session.cookies)
        for page_url in pages:
            status, text = bridge.get_text(page_url)
            if status == 200 and text:
                frame = parse_fpi_html_tables(text)
                if not frame.empty:
                    frames.append(frame)

    if frames:
        combined = concat_frames(frames)
        return combined, artifacts, html, visible
    return pd.DataFrame(), artifacts, html, visible


async def execute_fpi_nsdl(
    session: NodriverSession | None,
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    spec = get_mission("fpi_nsdl")
    if spec is None:
        return mission_result(mission="fpi_nsdl", status="error", vendor="nsdl", error="unknown mission")

    vendor = "nselib"
    artifacts: list[str] = []
    detail, note = _fetch_via_nselib()
    html_snapshot = ""
    visible_snapshot = ""

    if detail.empty or backfill_historical:
        vendor = "nse_browser_nsdl"
        if session is not None:
            detail, artifacts, html_snapshot, visible_snapshot = await _fetch_via_browser_session(
                session,
                backfill_historical=backfill_historical,
            )
        else:
            async with NodriverSession(refresh_cookies=refresh_cookies) as owned:
                detail, artifacts, html_snapshot, visible_snapshot = await _fetch_via_browser_session(
                    owned,
                    backfill_historical=backfill_historical,
                )

    if detail.empty and agent_fallback:
        from trade_integrations.nse_browser.agent_rescue import rescue_fpi_rows

        rows, rescue_vendor = await rescue_fpi_rows(
            page_url=NSDL_FPI_LATEST,
            goal="Extract FPI investment activity table with debt equity hybrid gross buy sell net INR USD",
            html=html_snapshot,
            visible_text=visible_snapshot,
        )
        if rows:
            detail = parse_fpi_investment_table(pd.DataFrame(rows), source=rescue_vendor or "agent")
            vendor = rescue_vendor or "agent"

    if detail.empty:
        payload = mission_result(
            mission="fpi_nsdl",
            status="error",
            vendor=vendor,
            error="no_fpi_rows",
            data={"nselib_note": note},
            artifacts=artifacts,
        )
        save_mission_status("fpi_nsdl", payload)
        return payload

    daily = aggregate_fpi_daily(detail)
    if daily.empty:
        payload = mission_result(
            mission="fpi_nsdl",
            status="partial",
            vendor=vendor,
            error="aggregate_empty",
            artifacts=artifacts,
        )
        save_mission_status("fpi_nsdl", payload)
        return payload

    upsert_repo_parquet(daily, dataset_id="fpi", source=vendor)
    rows_written = upsert_daily_parquet(daily, path=spec.parquet_path)
    payload = mission_result(
        mission="fpi_nsdl",
        status="ok",
        vendor=vendor,
        rows=rows_written,
        date_range={"start": str(daily["date"].min()), "end": str(daily["date"].max())},
        artifacts=artifacts,
        data={
            "fpi_equity_days": int(daily["fpi_equity_net_inr"].notna().sum())
            if "fpi_equity_net_inr" in daily.columns
            else 0,
        },
    )
    save_mission_status("fpi_nsdl", payload)
    return payload


def run_fpi_nsdl(
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    timeout = HISTORICAL_MISSION_TIMEOUT_S if backfill_historical else None
    try:
        return run_mission_async(
            execute_fpi_nsdl(
                None,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
            ),
            timeout_s=timeout,
        )
    except TimeoutError:
        payload = mission_result(
            mission="fpi_nsdl",
            status="error",
            vendor="nse_browser_nsdl",
            error="mission_timeout",
        )
        save_mission_status("fpi_nsdl", payload)
        return payload
    except Exception as exc:
        logger.exception("fpi_nsdl mission failed")
        payload = mission_result(
            mission="fpi_nsdl",
            status="error",
            vendor="nse_browser_nsdl",
            error=str(exc),
        )
        save_mission_status("fpi_nsdl", payload)
        return payload
