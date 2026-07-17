"""Agent rescue chain: nodriver MiniMax operator → MiniMax extract."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.nse_browser.session import NodriverSession

logger = logging.getLogger(__name__)


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
    """MiniMax operator (nodriver) → MiniMax extract."""
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
    return _minimax_extract(goal=goal, page_url=page_url, html=html, visible_text=visible_text)


async def rescue_archive_links(
    session: NodriverSession,
    *,
    page_url: str,
    goal: str,
) -> list[str]:
    """Discover archive CSV links via MiniMax operator then link discovery."""
    from trade_integrations.nse_browser.agent_runner import discover_download_urls

    await _minimax_operator(session, page_url=page_url, goal=goal)
    try:
        from trade_integrations.nse_browser.session import visible_text_from_html

        session.last_html = await session.tab.get_content()
        session.last_visible_text = visible_text_from_html(session.last_html)
    except Exception:
        pass
    return discover_download_urls(
        page_url=page_url,
        goal=goal,
        html=session.last_html,
        visible_text=session.last_visible_text,
    )


def rescue_status() -> dict[str, Any]:
    from trade_integrations.nse_browser.agent_runner import agent_status

    return {
        "chain": ["nodriver_minimax_operator", "minimax_extract"],
        "minimax": agent_status(),
    }
