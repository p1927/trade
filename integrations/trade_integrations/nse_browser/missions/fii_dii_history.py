"""FII/DII history — CSV download → DOM → network → repo + hub."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.direct_fetch import (
    bootstrap_nse_session_cookies,
    fetch_fii_dii_csv_url,
    fetch_fii_dii_json,
    fetch_fii_dii_react_csv,
    rows_from_agent_table,
)
from trade_integrations.nse_browser.dom_extract import collect_nsearchives_csv_links, extract_fii_dii_table_all
from trade_integrations.nse_browser.http_bridge import HttpBridge
from trade_integrations.nse_browser.hub_writer import (
    mission_result,
    save_mission_status,
    upsert_daily_parquet,
)
from trade_integrations.nse_browser.nse_urls import (
    FII_DII_API_CSV_CANDIDATES,
    FII_DII_API_JSON,
    FII_DII_HISTORICAL_PAGE,
    FII_DII_REPORT_PAGE,
    NSE_HISTORICAL_REPORTS,
)
from trade_integrations.nse_browser.parsers.fii_dii import (
    merge_fii_dii_variants,
    parse_fii_dii_csv,
    parse_fii_dii_json,
)
from trade_integrations.nse_browser.registry import get_mission
from trade_integrations.nse_browser.repository import (
    load_repo_dataset,
    raw_dir,
    save_raw_file,
    seed_mrchartist_fii_dii,
    upsert_repo_parquet,
)
from trade_integrations.nse_browser.session import (
    HISTORICAL_MISSION_TIMEOUT_S,
    MISSION_TIMEOUT_S,
    NodriverSession,
    run_mission_async,
)

logger = logging.getLogger(__name__)

_MIN_HISTORY_DAYS = int(os.environ.get("NSE_FII_DII_MIN_HISTORY_DAYS", "100"))


def _frames_from_sniffer(session: NodriverSession) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for cap in session.network_sniffer.captured:
        body = (cap.body or "").strip()
        if not body:
            continue
        try:
            if body.lstrip().startswith("{") or "fiidii" in cap.url.lower():
                frame = parse_fii_dii_json(body)
            else:
                variant = "nse_only" if "nse" in cap.url.lower() and "bse" not in cap.url.lower() else "combined"
                frame = parse_fii_dii_csv(body, variant=variant)
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            logger.debug("sniffer parse failed for %s: %s", cap.url[:60], exc)
    return frames


def _parse_download_file(path: Path, *, variant: str) -> pd.DataFrame:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return pd.DataFrame()
    if not text.strip():
        return pd.DataFrame()
    if text.lstrip().startswith("{"):
        return parse_fii_dii_json(text)
    return parse_fii_dii_csv(text, variant=variant)


_FII_DII_DOWNLOAD_IDS = ("downloadNseCsvBtn", "downloadCsvBtn")


async def _download_fii_dii_csvs(session: NodriverSession, artifacts: list[str]) -> list[pd.DataFrame]:
    """Silent CDP download of NSE-only + combined FII/DII CSV buttons."""
    frames: list[pd.DataFrame] = []
    download_path = raw_dir("fii_dii")
    download_path.mkdir(parents=True, exist_ok=True)
    variants = ("nse_only", "combined")

    from trade_integrations.nse_browser.download_manager import DownloadManager

    session.downloads = DownloadManager(directory=download_path)
    if session.tab is not None:
        await session.downloads.configure(session.tab)

    for btn_id, variant in zip(_FII_DII_DOWNLOAD_IDS, variants):
        paths: list[str] = []

        async def _click_btn(element_id: str = btn_id) -> None:
            await session.tab.evaluate(
                f"""
                () => {{
                  const el = document.getElementById({element_id!r});
                  if (el) el.click();
                }}
                """
            )

        try:
            path = await session.downloads.trigger_and_wait(_click_btn, timeout_s=30)
            if path:
                paths = [path]
        except Exception as exc:
            logger.debug("button download %s failed: %s", btn_id, exc)

        for path_str in paths:
            path = Path(path_str)
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.lstrip().startswith("<"):
                logger.debug("skipping HTML masquerading as CSV: %s", path.name)
                continue
            repo_raw = save_raw_file(
                text,
                dataset="fii_dii",
                name=f"{variant}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv",
            )
            artifacts.append(str(repo_raw))
            frame = _parse_download_file(path, variant=variant)
            if not frame.empty:
                frames.append(frame)
    return frames


async def _collect_browser_data(
    session: NodriverSession,
    *,
    agent_fallback: bool,
    artifacts: list[str],
    backfill_historical: bool,
) -> tuple[list[pd.DataFrame], list[str]]:
    parsed_frames: list[pd.DataFrame] = []
    urls: list[str] = []

    await session.goto(FII_DII_REPORT_PAGE)
    if session.captcha_detected and not session.captcha_resolved:
        return parsed_frames, urls

    # Tier 1: silent CSV downloads (full history)
    parsed_frames.extend(await _download_fii_dii_csvs(session, artifacts))

    # Tier 2: DOM all rows with scroll
    dom_rows = await extract_fii_dii_table_all(session.tab)
    if dom_rows:
        dom_frame = rows_from_agent_table(dom_rows)
        if not dom_frame.empty:
            parsed_frames.append(dom_frame)

    # Tier 3: network sniffer
    parsed_frames.extend(_frames_from_sniffer(session))

    # Tier 4: href/API discovery
    hrefs = await session.find_csv_hrefs()
    seen: set[str] = set()
    for href in list(hrefs) + list(FII_DII_API_CSV_CANDIDATES) + [FII_DII_API_JSON]:
        if href and href not in seen:
            seen.add(href)
            urls.append(href)

    if backfill_historical:
        for page in (FII_DII_HISTORICAL_PAGE, NSE_HISTORICAL_REPORTS[0]):
            if not page or not str(page).startswith("http"):
                continue
            try:
                await session.goto(page)
            except Exception as exc:
                logger.debug("historical reports goto failed: %s", exc)
                continue
            for href in await collect_nsearchives_csv_links(session.tab):
                if href and href.startswith("http") and href not in seen:
                    seen.add(href)
                    urls.append(href)
            for href in await session.find_csv_hrefs():
                if href and href.startswith("http") and href not in seen:
                    seen.add(href)
                    urls.append(href)
            for href in await session.find_links_by_keywords(("fii", "dii", "fpi", "institutional", "trading")):
                if href and href.startswith("http") and href not in seen:
                    seen.add(href)
                    urls.append(href)

    if not parsed_frames and agent_fallback:
        from trade_integrations.nse_browser.agent_rescue import rescue_fii_dii_rows

        rows, _vendor = await rescue_fii_dii_rows(
            session,
            page_url=FII_DII_REPORT_PAGE,
            goal="Extract FII FPI and DII buy sell net values; download CSV if available",
            html=session.last_html,
            visible_text=session.last_visible_text,
        )
        if rows:
            agent_frame = rows_from_agent_table(rows)
            if not agent_frame.empty:
                parsed_frames.append(agent_frame)
        if not parsed_frames:
            parsed_frames.extend(await _download_fii_dii_csvs(session, artifacts))
            dom_rows = await extract_fii_dii_table_all(session.tab)
            if dom_rows:
                dom_frame = rows_from_agent_table(dom_rows)
                if not dom_frame.empty:
                    parsed_frames.append(dom_frame)

    return parsed_frames, urls


async def execute_fii_dii_history(
    session: NodriverSession | None,
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    spec = get_mission("fii_dii_history")
    if spec is None:
        return mission_result(mission="fii_dii_history", status="error", vendor="nse_browser", error="unknown mission")

    if backfill_historical:
        agent_fallback = True

    artifacts: list[str] = []
    parsed_frames: list[pd.DataFrame] = []

    cookies = bootstrap_nse_session_cookies()
    react_csv = fetch_fii_dii_react_csv(cookies)
    if not react_csv.empty:
        parsed_frames.append(react_csv)
    if not backfill_historical:
        json_frame = fetch_fii_dii_json(cookies)
        if not json_frame.empty:
            parsed_frames.append(json_frame)

    urls: list[str] = []
    rescue_vendor = ""

    async def _with_session(active: NodriverSession) -> None:
        nonlocal parsed_frames, urls, rescue_vendor
        browser_frames, urls = await _collect_browser_data(
            active,
            agent_fallback=agent_fallback,
            artifacts=artifacts,
            backfill_historical=backfill_historical,
        )
        parsed_frames.extend(browser_frames)

        if active.captcha_detected and not active.captcha_resolved and not active.cookies:
            return

        bridge = HttpBridge(active.cookies or cookies)
        for url in urls:
            frame = fetch_fii_dii_csv_url(url, active.cookies or cookies)
            if frame.empty:
                status, text = bridge.get_text(url, referer=FII_DII_REPORT_PAGE)
                if status != 200 or not text.strip():
                    continue
                save_raw_file(text, dataset="fii_dii", name=f"api_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.txt")
                if text.lstrip().startswith("{"):
                    frame = parse_fii_dii_json(text)
                else:
                    frame = parse_fii_dii_csv(text, variant="combined")
            if not frame.empty:
                parsed_frames.append(frame)

        if not parsed_frames and agent_fallback:
            from trade_integrations.nse_browser.agent_rescue import rescue_fii_dii_rows

            rows, _rescue_vendor = await rescue_fii_dii_rows(
                active,
                page_url=FII_DII_REPORT_PAGE,
                goal="Extract FII FPI and DII buy sell net values from visible tables",
                html=active.last_html,
                visible_text=active.last_visible_text,
            )
            agent_frame = rows_from_agent_table(rows)
            if not agent_frame.empty:
                parsed_frames.append(agent_frame)
                rescue_vendor = rescue_vendor or _rescue_vendor

    if session is not None:
        await _with_session(session)
        if session.captcha_detected and not session.captcha_resolved and not session.cookies:
            payload = mission_result(
                mission="fii_dii_history",
                status="error",
                vendor="nse_browser",
                error="captcha_unresolved",
                data={"human_in_loop": True, "hint": "Complete CAPTCHA in headed Chrome and retry"},
            )
            save_mission_status("fii_dii_history", payload)
            return payload
    else:
        async with NodriverSession(refresh_cookies=refresh_cookies) as owned:
            await _with_session(owned)
            if owned.captcha_detected and not owned.captcha_resolved and not owned.cookies:
                payload = mission_result(
                    mission="fii_dii_history",
                    status="error",
                    vendor="nse_browser",
                    error="captcha_unresolved",
                    data={"human_in_loop": True, "hint": "Complete CAPTCHA in headed Chrome and retry"},
                )
                save_mission_status("fii_dii_history", payload)
                return payload

    merged = merge_fii_dii_variants(*parsed_frames)
    if merged.empty or (backfill_historical and len(merged) < _MIN_HISTORY_DAYS):
        from trade_integrations.nse_browser.repository import sync_fii_dii_repo_layers

        sync_fii_dii_repo_layers()
        existing = load_repo_dataset("fii_dii")
        merged = merge_fii_dii_variants(existing, merged) if not merged.empty else existing

    if merged.empty:
        payload = mission_result(
            mission="fii_dii_history",
            status="error",
            vendor="nse_browser",
            error="no_rows_parsed",
            artifacts=artifacts,
            data={"urls_tried": len(urls)},
        )
        save_mission_status("fii_dii_history", payload)
        return payload

    upsert_repo_parquet(merged, dataset_id="fii_dii", source="nse_browser_csv")
    rows_written = upsert_daily_parquet(merged, path=spec.parquet_path)
    unique_days = merged["date"].nunique() if "date" in merged.columns else 0
    vendor_label = "nse_browser"
    if agent_fallback and rescue_vendor:
        vendor_label = f"nse_browser+{rescue_vendor}"
    elif agent_fallback:
        vendor_label = "nse_browser+agent"
    payload = mission_result(
        mission="fii_dii_history",
        status="ok",
        vendor=vendor_label,
        rows=rows_written,
        date_range={"start": str(merged["date"].min()), "end": str(merged["date"].max())},
        artifacts=artifacts,
        data={
            "unique_days": int(unique_days),
            "repo_path": str(raw_dir("fii_dii")),
            "fii_net_days": int(merged["fii_net"].notna().sum()) if "fii_net" in merged.columns else 0,
            "dii_net_days": int(merged["dii_net"].notna().sum()) if "dii_net" in merged.columns else 0,
        },
    )
    save_mission_status("fii_dii_history", payload)
    return payload


def run_fii_dii_history(
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    timeout = HISTORICAL_MISSION_TIMEOUT_S if backfill_historical else MISSION_TIMEOUT_S
    try:
        return run_mission_async(
            execute_fii_dii_history(
                None,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
            ),
            timeout_s=timeout,
        )
    except TimeoutError:
        payload = mission_result(
            mission="fii_dii_history",
            status="error",
            vendor="nse_browser",
            error="mission_timeout",
            data={"timeout_s": timeout},
        )
        save_mission_status("fii_dii_history", payload)
        return payload
    except Exception as exc:
        logger.exception("fii_dii_history mission failed")
        payload = mission_result(
            mission="fii_dii_history",
            status="error",
            vendor="nse_browser",
            error=str(exc),
        )
        save_mission_status("fii_dii_history", payload)
        return payload
