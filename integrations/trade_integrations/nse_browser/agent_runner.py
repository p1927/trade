"""Optional MiniMax M3 agent fallback — reads nodriver page snapshots and drives browser."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.nse_browser.minimax_agent import (
    discover_from_page,
    extract_tables_from_page,
    minimax_configured,
    plan_browser_action,
)
from trade_integrations.nse_browser.registry import hub_root
from trade_integrations.nse_browser.session import NodriverSession, run_mission_async, visible_text_from_html

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


def _task_dir(task_id: str) -> Path:
    path = hub_root() / "tasks" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _persist_task_artifact(task_id: str, name: str, data: Any) -> str:
    dest = _task_dir(task_id) / name
    if isinstance(data, (dict, list)):
        dest.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    else:
        dest.write_text(str(data), encoding="utf-8")
    return str(dest)


async def _run_adhoc_browser_task_async(
    goal: str,
    *,
    url: str | None,
    max_steps: int,
    timeout_s: float,
) -> dict[str, Any]:
    page_url = url or "about:blank"
    async with NodriverSession(refresh_cookies=False) as session:
        if url:
            await session.goto(url)
        action_log = await run_operator_loop(
            session,
            page_url=page_url,
            goal=goal,
            max_steps=max_steps,
            timeout_s=timeout_s,
        )
        html = session.last_html
        visible_text = session.last_visible_text
        if session.tab is not None:
            try:
                html = await session.tab.get_content()
                visible_text = visible_text_from_html(html)
            except Exception:
                pass
        rows = extract_table_rows(
            page_url=page_url,
            goal=goal,
            html=html,
            visible_text=visible_text,
        )
        download_urls = discover_download_urls(
            page_url=page_url,
            goal=goal,
            html=html,
            visible_text=visible_text,
        )
        structured: dict[str, Any] = {}
        if rows:
            structured["table_rows"] = rows
        if download_urls:
            structured["download_urls"] = download_urls
        return {
            "structured_output": structured or None,
            "action_log": action_log,
            "page_url": page_url,
            "row_count": len(rows),
            "download_url_count": len(download_urls),
        }


def run_nodriver_agent_task(
    goal: str,
    *,
    url: str | None = None,
    max_steps: int = 10,
    persist: bool = True,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Run ad-hoc browse/extract via local nodriver + MiniMax operator."""
    task_id = task_id or f"tsk_{uuid.uuid4().hex[:12]}"
    started = datetime.now(timezone.utc).isoformat()
    if not _agent_enabled():
        return {
            "status": "error",
            "task_id": task_id,
            "engine": "nodriver_minimax",
            "error": "minimax_agent_not_configured",
            "hint": "Set MINIMAX_API_KEY and ensure NSE_BROWSER_AGENT_DISABLED is not set",
        }

    timeout_s = float(os.environ.get("NSE_BROWSER_MISSION_TIMEOUT_S", "55"))
    try:
        payload = run_mission_async(
            _run_adhoc_browser_task_async(
                goal,
                url=url,
                max_steps=max(1, min(max_steps, 20)),
                timeout_s=timeout_s,
            ),
            timeout_s=timeout_s + 5,
        )
    except Exception as exc:
        return {
            "status": "error",
            "task_id": task_id,
            "engine": "nodriver_minimax",
            "error": str(exc),
        }

    ok = bool(payload.get("structured_output"))
    result: dict[str, Any] = {
        "status": "ok" if ok else "error",
        "task_id": task_id,
        "engine": "nodriver_minimax",
        "goal": goal,
        "url": url,
        "structured_output": payload.get("structured_output"),
        "action_log": payload.get("action_log"),
        "row_count": payload.get("row_count", 0),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if not ok:
        result["error"] = "no_structured_output"

    if persist:
        result["hub_path"] = _persist_task_artifact(task_id, "result.json", result)

    return result
