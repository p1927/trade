"""Agent rescue chain: Skyvern → nodriver MiniMax operator → MiniMax extract."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.nse_browser.session import NodriverSession

logger = logging.getLogger(__name__)

_FII_DII_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table_rows": {
            "type": "array",
            "description": "Daily FII/FPI and DII buy sell net values",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "category": {"type": "string"},
                    "buy": {"type": "number"},
                    "sell": {"type": "number"},
                    "net": {"type": "number"},
                    "fii_net": {"type": "number"},
                    "dii_net": {"type": "number"},
                },
            },
        }
    },
}

_FPI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "table_rows": {
            "type": "array",
            "description": "FPI debt equity hybrid gross buy sell net INR USD",
            "items": {"type": "object"},
        }
    },
}

_ARCHIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "download_urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "CSV download URLs for bulk deals delivery pe pb archives",
        },
        "notes": {"type": "string"},
    },
}


def _skyvern_rescue(
    *,
    goal: str,
    page_url: str,
    output_schema: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str]:
    from trade_integrations.nse_browser.skyvern_bridge import (
        rows_from_skyvern_output,
        run_skyvern_task,
        skyvern_configured,
    )

    if not skyvern_configured():
        return [], ""
    result = run_skyvern_task(goal, url=page_url, output_schema=output_schema, persist=True)
    if result.get("status") != "ok":
        logger.warning("Skyvern rescue failed: %s", result.get("error"))
        return [], ""
    rows = rows_from_skyvern_output(result.get("structured_output"))
    if rows:
        return rows, "skyvern"
    return [], ""


def _minimax_extract(
    *,
    goal: str,
    page_url: str,
    html: str,
    visible_text: str,
) -> tuple[list[dict[str, Any]], str]:
    from trade_integrations.nse_browser.agent_runner import _agent_enabled, extract_table_rows

    if not _agent_enabled():
        return [], ""
    rows = extract_table_rows(
        page_url=page_url,
        goal=goal,
        html=html,
        visible_text=visible_text,
    )
    if rows:
        return rows, "minimax_agent"
    return [], ""


async def _minimax_operator(
    session: NodriverSession,
    *,
    page_url: str,
    goal: str,
    max_steps: int = 5,
    timeout_s: int = 35,
) -> None:
    from trade_integrations.nse_browser.agent_runner import _agent_enabled, run_operator_loop

    if not _agent_enabled():
        return
    await run_operator_loop(
        session,
        page_url=page_url,
        goal=goal,
        max_steps=max_steps,
        timeout_s=timeout_s,
    )


async def rescue_fii_dii_rows(
    session: NodriverSession | None,
    *,
    page_url: str,
    goal: str,
    html: str = "",
    visible_text: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Skyvern → MiniMax operator (nodriver) → MiniMax extract."""
    rows, vendor = _skyvern_rescue(goal=goal, page_url=page_url, output_schema=_FII_DII_SCHEMA)
    if rows:
        return rows, vendor

    if session is not None:
        await _minimax_operator(
            session,
            page_url=page_url,
            goal="Scroll FII DII table; click Download csv if needed",
        )
        try:
            from trade_integrations.nse_browser.session import visible_text_from_html

            session.last_html = await session.tab.get_content()
            session.last_visible_text = visible_text_from_html(session.last_html)
            html = session.last_html
            visible_text = session.last_visible_text
        except Exception:
            pass

    return _minimax_extract(
        goal=goal,
        page_url=page_url,
        html=html,
        visible_text=visible_text,
    )


async def rescue_fpi_rows(
    *,
    page_url: str,
    goal: str,
    html: str = "",
    visible_text: str = "",
) -> tuple[list[dict[str, Any]], str]:
    rows, vendor = _skyvern_rescue(goal=goal, page_url=page_url, output_schema=_FPI_SCHEMA)
    if rows:
        return rows, vendor
    return _minimax_extract(goal=goal, page_url=page_url, html=html, visible_text=visible_text)


async def rescue_archive_links(
    session: NodriverSession,
    *,
    page_url: str,
    goal: str,
) -> list[str]:
    """Discover archive CSV links via Skyvern then MiniMax operator."""
    result = _skyvern_rescue(goal=goal, page_url=page_url, output_schema=_ARCHIVE_SCHEMA)
    rows = result[0]
    urls: list[str] = []
    if rows:
        first = rows[0] if rows else {}
        raw_urls = first.get("download_urls") if isinstance(first, dict) else None
        if isinstance(raw_urls, list):
            urls = [u for u in raw_urls if isinstance(u, str) and u.startswith("http")]
    if urls:
        return urls

    await _minimax_operator(session, page_url=page_url, goal=goal)
    return []


def rescue_status() -> dict[str, Any]:
    from trade_integrations.nse_browser.agent_runner import agent_status
    from trade_integrations.nse_browser.skyvern_bridge import skyvern_status

    return {
        "chain": ["skyvern", "nodriver_minimax_operator", "minimax_extract"],
        "skyvern": skyvern_status(),
        "minimax": agent_status(),
    }
