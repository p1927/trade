"""Scrape FII/DII cash history from Moneycontrol and Nifty Invest via shared browser."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.hub_writer import mission_result, save_mission_status
from trade_integrations.nse_browser.parsers.fii_dii import merge_fii_dii_variants
from trade_integrations.nse_browser.parsers.web_flow import (
    moneycontrol_cash_url,
    niftyinvest_cash_url,
    parse_moneycontrol_cash_html,
    parse_niftyinvest_cash_csv,
)
from trade_integrations.nse_browser.registry import get_mission
from trade_integrations.nse_browser.repository import (
    load_repo_dataset,
    raw_dir,
    save_raw_file,
    upsert_repo_parquet,
)
from trade_integrations.nse_browser.session import (
    HISTORICAL_MISSION_TIMEOUT_S,
    NodriverSession,
    run_mission_async,
)

logger = logging.getLogger(__name__)

_DEFAULT_MONTHS_BACK = int(os.environ.get("WEB_FLOW_MONTHS_BACK", "18"))


def _month_range(*, months_back: int) -> list[tuple[int, int]]:
    today = date.today()
    y, m = today.year, today.month
    out: list[tuple[int, int]] = []
    for _ in range(max(1, months_back)):
        out.append((m, y))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


async def _scrape_months(session: NodriverSession, months: list[tuple[int, int]]) -> tuple[list[pd.DataFrame], list[str]]:
    frames: list[pd.DataFrame] = []
    artifacts: list[str] = []
    for month, year in months:
        mc_url = moneycontrol_cash_url(month=month, year=year)
        try:
            html = await session.goto(mc_url, resolve_captcha=True)
        except Exception as exc:
            logger.debug("moneycontrol %s-%s failed: %s", year, month, exc)
            html = ""
        if html and "Login Consent" not in html and "mclogin" not in html.lower()[:500]:
            rel = save_raw_file(html, dataset="web_flow", name=f"moneycontrol_{year}{month:02d}.html")
            artifacts.append(str(rel))
            frame = parse_moneycontrol_cash_html(html)
            if not frame.empty:
                frames.append(frame)

        ni_url = niftyinvest_cash_url(month=month, year=year)
        try:
            ni_html = await session.goto(ni_url, resolve_captcha=True)
        except Exception as exc:
            logger.debug("niftyinvest %s-%s failed: %s", year, month, exc)
            ni_html = ""
        if ni_html:
            rel = save_raw_file(ni_html, dataset="web_flow", name=f"niftyinvest_{year}{month:02d}.html")
            artifacts.append(str(rel))
            # Some months expose CSV in-page; try parser on HTML tables too
            if "fii" in ni_html.lower() and "dii" in ni_html.lower():
                frame = parse_moneycontrol_cash_html(ni_html)
                if frame.empty and "," in ni_html and "Date" in ni_html:
                    frame = parse_niftyinvest_cash_csv(ni_html)
                if not frame.empty:
                    for row in frame.to_dict("records"):
                        row["source"] = "niftyinvest_cash"
                    frames.append(frame)
    return frames, artifacts


async def execute_web_flow_history(
    *,
    refresh_cookies: bool = False,
    months_back: int = _DEFAULT_MONTHS_BACK,
    session: NodriverSession | None = None,
) -> dict[str, Any]:
    months = _month_range(months_back=months_back)

    async def _run(active: NodriverSession) -> dict[str, Any]:
        frames, artifacts = await _scrape_months(active, months)
        merged = merge_fii_dii_variants(*frames) if frames else pd.DataFrame()
        if merged.empty:
            payload = mission_result(
                mission="web_flow_history",
                status="error",
                vendor="web_scrape",
                error="no_rows_parsed",
                artifacts=artifacts,
                data={"months_tried": len(months)},
            )
            save_mission_status("web_flow_history", payload)
            return payload

        existing = load_repo_dataset("fii_dii")
        combined = merge_fii_dii_variants(existing, merged) if not existing.empty else merged
        upsert_repo_parquet(combined, dataset_id="fii_dii", source="web_flow_scrape")
        fii_spec = get_mission("fii_dii_history")
        if fii_spec is not None:
            from trade_integrations.nse_browser.hub_writer import upsert_daily_parquet

            upsert_daily_parquet(combined, path=fii_spec.parquet_path)

        payload = mission_result(
            mission="web_flow_history",
            status="ok",
            vendor="web_scrape",
            row_count=len(merged),
            artifacts=artifacts,
            data={
                "months_tried": len(months),
                "date_range": {
                    "start": str(merged["date"].min()),
                    "end": str(merged["date"].max()),
                },
                "sources": sorted({str(s) for s in merged.get("source", pd.Series()).unique()}),
            },
        )
        save_mission_status("web_flow_history", payload)
        return payload

    if session is not None:
        return await _run(session)

    async with NodriverSession(refresh_cookies=refresh_cookies) as owned:
        return await _run(owned)


def run_web_flow_history(**kwargs: Any) -> dict[str, Any]:
    timeout = float(os.environ.get("NSE_BROWSER_WEB_FLOW_TIMEOUT_S", str(HISTORICAL_MISSION_TIMEOUT_S)))
    return run_mission_async(execute_web_flow_history(**kwargs), timeout_s=timeout)


def load_web_flow_from_raw_cache() -> pd.DataFrame:
    """Parse saved web_flow HTML snapshots without launching a browser."""
    root = raw_dir("web_flow")
    if not root.is_dir():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for path in sorted(root.glob("*.html")):
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if path.name.startswith("moneycontrol_"):
            frame = parse_moneycontrol_cash_html(html)
        elif path.name.startswith("niftyinvest_"):
            frame = parse_niftyinvest_cash_csv(html)
            if frame.empty:
                frame = parse_moneycontrol_cash_html(html)
            if not frame.empty:
                frame = frame.copy()
                frame["source"] = "niftyinvest_cash"
        else:
            frame = parse_moneycontrol_cash_html(html)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return merge_fii_dii_variants(*frames)
