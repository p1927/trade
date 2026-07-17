"""RBI CPI and policy rate context — scrape, SearXNG finance, or explicit env override."""

from __future__ import annotations

import logging
import os
import re
from datetime import date
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


def _merge_source(primary: str, extra: str) -> str:
    if not primary or primary == "missing":
        return extra
    if extra in primary:
        return primary
    return f"{primary}+{extra}"


def _apply_env_override(result: dict[str, Any], field: str, env_name: str) -> None:
    if result.get(field) is not None:
        return
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return
    try:
        result[field] = float(raw)
    except ValueError:
        logger.debug("Invalid %s=%r", env_name, raw)
        return
    result["source"] = _merge_source(str(result.get("source") or "missing"), "env_override")


def _finalize_source(result: dict[str, Any]) -> None:
    has_data = result.get("repo_rate") is not None or result.get("cpi_yoy_proxy") is not None
    source = str(result.get("source") or "missing")
    if not has_data:
        result["source"] = "missing"
    elif source == "missing":
        result["source"] = "partial"


def fetch_rbi_cpi_context() -> dict:
    """Return repo_rate, cpi_yoy_proxy, rbi_events — never raises."""
    result: dict[str, Any] = {
        "repo_rate": None,
        "cpi_yoy_proxy": None,
        "rbi_events": [],
        "source": "missing",
    }

    try:
        scraped = _scrape_rbi_press_releases()
        if scraped:
            result.update(scraped)
    except Exception as exc:
        logger.debug("RBI scrape failed: %s", exc)

    if result["repo_rate"] is None or result["cpi_yoy_proxy"] is None:
        try:
            from trade_integrations.dataflows.searxng_finance import fetch_rbi_macro_via_searxng

            enriched = fetch_rbi_macro_via_searxng()
            if enriched:
                if result["repo_rate"] is None and enriched.get("repo_rate") is not None:
                    result["repo_rate"] = enriched["repo_rate"]
                if result["cpi_yoy_proxy"] is None and enriched.get("cpi_yoy_proxy") is not None:
                    result["cpi_yoy_proxy"] = enriched["cpi_yoy_proxy"]
                result["source"] = _merge_source(
                    str(result.get("source") or "missing"),
                    str(enriched.get("source") or "searxng_finance"),
                )
                if enriched.get("metadata"):
                    result.setdefault("metadata", {}).update(enriched["metadata"])
        except Exception as exc:
            logger.debug("RBI SearXNG enrichment failed: %s", exc)

    _apply_env_override(result, "repo_rate", "RBI_REPO_RATE")
    _apply_env_override(result, "cpi_yoy_proxy", "RBI_CPI_YOY_PROXY")

    if not result["rbi_events"]:
        result["rbi_events"] = list(_STATIC_POLICY_DATES)

    _finalize_source(result)
    return result
