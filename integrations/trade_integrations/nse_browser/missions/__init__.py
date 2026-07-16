"""Mission dispatch for NSE browser module."""

from __future__ import annotations

import os
from typing import Any, Callable

from trade_integrations.nse_browser.missions.fii_dii_history import execute_fii_dii_history, run_fii_dii_history
from trade_integrations.nse_browser.missions.fpi_nsdl import execute_fpi_nsdl, run_fpi_nsdl
from trade_integrations.nse_browser.missions.market_archives import execute_market_archives, run_market_archives
from trade_integrations.nse_browser.missions.web_flow_history import execute_web_flow_history, run_web_flow_history
from trade_integrations.nse_browser.registry import MISSIONS
from trade_integrations.nse_browser.session import HISTORICAL_MISSION_TIMEOUT_S, NodriverSession, run_mission_async

_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "fii_dii_history": run_fii_dii_history,
    "fpi_nsdl": run_fpi_nsdl,
    "market_archives": run_market_archives,
}

_EXECUTE: dict[str, Callable[..., Any]] = {
    "fii_dii_history": execute_fii_dii_history,
    "fpi_nsdl": execute_fpi_nsdl,
    "market_archives": execute_market_archives,
}

# Order: NSE pages first, NSDL last (same browser tab — no reopen)
_BATCH_ORDER = ("fii_dii_history", "market_archives", "fpi_nsdl")


def list_missions() -> list[dict[str, str]]:
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "description": spec.description,
            "adaptive": str(spec.adaptive),
        }
        for spec in MISSIONS.values()
    ]


def run_mission(
    mission_id: str,
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    runner = _RUNNERS.get(mission_id.strip())
    if runner is None:
        return {
            "mission": mission_id,
            "status": "error",
            "error": f"unknown mission: {mission_id}",
            "available": list(_RUNNERS.keys()),
        }
    return runner(
        refresh_cookies=refresh_cookies,
        agent_fallback=agent_fallback,
        backfill_historical=backfill_historical,
    )


async def _run_all_in_one_browser(
    *,
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> dict[str, Any]:
    """Run every mission in a single nodriver session (avoids NSE bot blocks)."""
    import os

    results: dict[str, dict[str, Any]] = {}
    async with NodriverSession(refresh_cookies=refresh_cookies) as session:
        await session.goto("https://www.nseindia.com")
        for mission_id in _BATCH_ORDER:
            execute = _EXECUTE.get(mission_id)
            if execute is None:
                continue
            results[mission_id] = await execute(
                session,
                refresh_cookies=False,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
            )
        try:
            results["web_flow_history"] = await execute_web_flow_history(
                session=session,
                refresh_cookies=False,
            )
        except Exception as exc:
            results["web_flow_history"] = {"status": "error", "error": str(exc)}
    ok = sum(1 for r in results.values() if r.get("status") == "ok")
    return {
        "status": "ok" if ok else "partial",
        "missions": results,
        "ok_count": ok,
        "shared_browser": True,
    }


def run_all_missions(
    *,
    shared_browser: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    if shared_browser:
        batch_timeout = float(
            os.environ.get(
                "NSE_BROWSER_BATCH_TIMEOUT_S",
                str(HISTORICAL_MISSION_TIMEOUT_S * 3),
            )
        )
        try:
            return run_mission_async(_run_all_in_one_browser(**kwargs), timeout_s=batch_timeout)
        except TimeoutError:
            return {"status": "error", "error": "mission_timeout", "shared_browser": True}
    results = {mid: run_mission(mid, **kwargs) for mid in _RUNNERS}
    ok = sum(1 for r in results.values() if r.get("status") == "ok")
    return {"status": "ok" if ok else "partial", "missions": results, "ok_count": ok, "shared_browser": False}
