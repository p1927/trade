"""Optional MiniMax M3 agent fallback — reads nodriver page snapshots and drives browser."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

from trade_integrations.nse_browser.minimax_agent import (
    discover_from_page,
    extract_tables_from_page,
    minimax_configured,
    plan_browser_action,
)

logger = logging.getLogger(__name__)


def _agent_enabled() -> bool:
    if os.environ.get("NSE_BROWSER_AGENT_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return minimax_configured()


def discover_download_urls(
    *,
    page_url: str,
    goal: str,
    html: str = "",
    visible_text: str = "",
) -> list[str]:
    """MiniMax discovers CSV/API download URLs from page content."""
    if not _agent_enabled():
        return []
    try:
        payload = discover_from_page(
            page_url=page_url,
            goal=goal,
            html=html,
            visible_text=visible_text,
        )
        urls = list(payload.get("download_urls") or []) + list(payload.get("api_urls") or [])
        return [str(u) for u in urls if u and str(u).startswith("http")]
    except Exception as exc:
        logger.warning("MiniMax discover_download_urls failed: %s", exc)
        return []


def extract_table_rows(
    *,
    page_url: str,
    goal: str,
    html: str = "",
    visible_text: str = "",
) -> list[dict[str, Any]]:
    """MiniMax extracts visible table rows as structured dicts."""
    if not _agent_enabled():
        return []
    try:
        payload = extract_tables_from_page(
            page_url=page_url,
            goal=goal,
            html=html,
            visible_text=visible_text,
        )
        rows = payload.get("table_rows") or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:
        logger.warning("MiniMax extract_table_rows failed: %s", exc)
        return []


async def run_operator_loop(
    session,
    *,
    page_url: str,
    goal: str,
    max_steps: int = 4,
    timeout_s: float = 40,
) -> list[dict[str, Any]]:
    """
    Short observe-act loop: MiniMax plans clicks/scrolls; nodriver executes.

    Returns action log for debugging.
    """
    if not _agent_enabled():
        return []

    from trade_integrations.nse_browser.session import PAGE_WAIT_S

    log: list[dict[str, Any]] = []
    deadline = asyncio.get_event_loop().time() + timeout_s

    for step in range(1, max_steps + 1):
        if asyncio.get_event_loop().time() >= deadline:
            break
        if session.captcha_detected and not session.captcha_resolved:
            break

        screenshot_b64: str | None = None
        if os.environ.get("NSE_BROWSER_AGENT_VISION", "0").strip().lower() in {"1", "true", "yes"}:
            png = await session.screenshot_png()
            if png:
                screenshot_b64 = base64.b64encode(png).decode("ascii")

        try:
            html = await session.tab.get_content() if session.tab else session.last_html
        except Exception:
            html = session.last_html

        plan = plan_browser_action(
            page_url=page_url,
            goal=goal,
            visible_text=session.last_visible_text,
            html=html,
            screenshot_b64=screenshot_b64,
            step=step,
            max_steps=max_steps,
        )
        action = plan.get("action", "done")
        target = plan.get("target", "")
        log.append({"step": step, **plan})

        if action == "done":
            break
        if action == "wait":
            await asyncio.sleep(PAGE_WAIT_S)
            continue
        if action == "scroll":
            await session.scroll_down()
            try:
                session.last_html = await session.tab.get_content()
                from trade_integrations.nse_browser.session import visible_text_from_html

                session.last_visible_text = visible_text_from_html(session.last_html)
            except Exception:
                pass
            continue
        if action == "click" and target:
            await session.click_target(target)
            try:
                session.last_html = await session.tab.get_content()
                from trade_integrations.nse_browser.session import visible_text_from_html

                session.last_visible_text = visible_text_from_html(session.last_html)
            except Exception:
                pass

    return log


def agent_status() -> dict[str, Any]:
    return {
        "enabled": _agent_enabled(),
        "configured": minimax_configured(),
        "provider": "minimax",
        "model": os.environ.get("NSE_BROWSER_AGENT_MODEL", "MiniMax-M3"),
        "base_url": os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        "vision": os.environ.get("NSE_BROWSER_AGENT_VISION", "0"),
    }
