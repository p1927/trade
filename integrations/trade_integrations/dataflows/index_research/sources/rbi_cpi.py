"""RBI CPI and policy rate context — best-effort scrape or env seeds."""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

_RBI_PRESS_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
_STATIC_POLICY_DATES = [
    "2026-08-06",
    "2026-10-01",
    "2026-12-05",
    "2027-02-05",
    "2027-04-07",
]


def _parse_repo_rate(text: str) -> float | None:
    patterns = [
        r"repo\s+rate\s+(?:at|to|stands?\s+at|unchanged\s+at)\s+(\d+(?:\.\d+)?)\s*(?:per\s+cent|%)?",
        r"policy\s+repo\s+rate\s+(?:at|to)\s+(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _parse_cpi_yoy(text: str) -> float | None:
    patterns = [
        r"cpi(?:\s*\(combined\))?\s+inflation\s+(?:at|stood\s+at)\s+(\d+(?:\.\d+)?)\s*(?:per\s+cent|%)?",
        r"retail\s+inflation\s+(?:at|stood\s+at)\s+(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _scrape_rbi_press_releases() -> dict[str, Any] | None:
    import requests

    response = requests.get(_RBI_PRESS_URL, timeout=15)
    response.raise_for_status()
    text = response.text
    if not text.strip():
        return None

    repo_rate = _parse_repo_rate(text)
    cpi_yoy = _parse_cpi_yoy(text)
    if repo_rate is None and cpi_yoy is None:
        return None

    return {
        "repo_rate": repo_rate,
        "cpi_yoy_proxy": cpi_yoy,
        "rbi_events": _extract_policy_dates(text),
        "source": "rbi_scrape",
    }


def _extract_policy_dates(text: str) -> list[str]:
    """Best-effort parse of MPC meeting dates from press release HTML."""
    events: list[str] = []
    for match in re.finditer(
        r"(?:monetary\s+policy|mpc)\s+(?:committee\s+)?(?:meeting|review)[^0-9]{0,40}"
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        text,
        re.IGNORECASE,
    ):
        day, month, year = match.groups()
        try:
            events.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            continue
    return sorted(set(events))


def _fetch_inflation_etf_proxy() -> float | None:
    """Optional CPI proxy — env/RBI scrape preferred; skip noisy invalid yfinance symbols."""
    env_default = os.getenv("RBI_CPI_YOY_PROXY_DEFAULT", "5.0").strip()
    try:
        return float(env_default)
    except ValueError:
        return 5.0


def fetch_rbi_cpi_context() -> dict:
    """Return repo_rate, cpi_yoy_proxy, rbi_events — never raises."""
    result: dict[str, Any] = {
        "repo_rate": None,
        "cpi_yoy_proxy": None,
        "rbi_events": [],
        "source": "env_seed",
    }

    try:
        scraped = _scrape_rbi_press_releases()
        if scraped:
            result.update(scraped)
    except Exception as exc:
        logger.debug("RBI scrape failed: %s", exc)

    if result["repo_rate"] is None:
        raw = os.getenv("RBI_REPO_RATE", "6.5").strip()
        try:
            result["repo_rate"] = float(raw)
        except ValueError:
            result["repo_rate"] = 6.5

    if result["cpi_yoy_proxy"] is None:
        env_cpi = os.getenv("RBI_CPI_YOY_PROXY", "").strip()
        if env_cpi:
            try:
                result["cpi_yoy_proxy"] = float(env_cpi)
            except ValueError:
                pass
        if result["cpi_yoy_proxy"] is None:
            result["cpi_yoy_proxy"] = _fetch_inflation_etf_proxy()

    if not result["rbi_events"]:
        result["rbi_events"] = list(_STATIC_POLICY_DATES)

    return result
