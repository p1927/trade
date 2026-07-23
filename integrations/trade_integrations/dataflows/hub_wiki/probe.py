"""LLM-Wiki availability probe — required before hub news ingest/drain."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_PAUSE_REASON = "llm_wiki_unavailable"
_USER_MESSAGE = (
    "LLM-Wiki is not running or not configured — please start LLM Wiki.app "
    "and set LLM_WIKI_PROJECT_ID in .env for news ingest."
)


def llm_wiki_required_for_hub_news() -> bool:
    """True when hub news ingest/drain must not run without a healthy wiki probe."""
    raw = os.getenv("HUB_NEWS_REQUIRE_LLM_WIKI", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from trade_integrations.hub_storage.news_staging_store import is_entity_pipeline_enabled

        if not is_entity_pipeline_enabled():
            return False
    except Exception:
        pass
    return True


def probe_llm_wiki(*, force_refresh: bool = False) -> dict[str, Any]:
    """Health + project id + path alignment + search probe."""
    from trade_integrations.dataflows.hub_wiki.client import (
        health_check,
        project_path_aligned,
        resolve_project_id,
        search_wiki,
    )
    from trade_integrations.dataflows.hub_wiki.config import (
        get_llm_wiki_project_dir,
        llm_wiki_base_url,
        llm_wiki_project_id,
    )
    from trade_integrations.dataflows.hub_wiki.search_dedup import reset_wiki_search_availability_cache

    if force_refresh:
        reset_wiki_search_availability_cache()

    base_url = llm_wiki_base_url()
    project_dir = str(get_llm_wiki_project_dir())
    configured_pid = llm_wiki_project_id()
    resolved_pid = resolve_project_id()
    project_id = configured_pid or resolved_pid or ""

    result: dict[str, Any] = {
        "ok": False,
        "base_url": base_url,
        "project_dir": project_dir,
        "project_id": project_id,
        "configured_project_id": configured_pid,
        "resolved_project_id": resolved_pid,
        "reachable": False,
        "aligned": False,
        "search_ok": False,
        "reason": "",
        "user_message": _USER_MESSAGE,
        "pause_reason": _PAUSE_REASON,
    }

    if not project_id:
        result["reason"] = "missing_project_id"
        return result

    health = health_check()
    result["health"] = health
    result["reachable"] = bool(health.get("ok"))
    if not result["reachable"]:
        result["reason"] = "health_check_failed"
        return result

    alignment = project_path_aligned()
    result["path_alignment"] = alignment
    result["aligned"] = bool(alignment.get("aligned"))
    if not result["aligned"]:
        result["reason"] = str(alignment.get("reason") or "project_path_misaligned")
        return result

    probe = search_wiki("NIFTY market news", top_k=3, project_id=project_id)
    result["search_probe"] = {
        "ok": bool(probe.get("ok")),
        "hits": len(probe.get("results") or []),
        "mode": probe.get("mode"),
    }
    result["search_ok"] = bool(probe.get("ok"))
    if not result["search_ok"]:
        result["reason"] = "search_probe_failed"
        return result

    result["ok"] = True
    result["reason"] = ""
    return result


def ingest_blocked_by_wiki(*, force_refresh: bool = False) -> dict[str, Any] | None:
    """Return block payload when wiki is required but probe fails; else None."""
    if not llm_wiki_required_for_hub_news():
        return None
    probe = probe_llm_wiki(force_refresh=force_refresh)
    if probe.get("ok"):
        return None
    return {
        "blocked": True,
        "reason": _PAUSE_REASON,
        "detail": str(probe.get("reason") or "probe_failed"),
        "user_message": _USER_MESSAGE,
        "llm_wiki": probe,
    }


def check_ingest_allowed(*, force_refresh: bool = False) -> dict[str, Any]:
    """Gate for ingest_rows_to_hub and scheduled ingest jobs."""
    block = ingest_blocked_by_wiki(force_refresh=force_refresh)
    if block:
        return block
    return {"blocked": False, "reason": ""}


def require_llm_wiki_for_hub_news(*, force_refresh: bool = False) -> None:
    """Raise when wiki probe fails and hub news requires it."""
    block = ingest_blocked_by_wiki(force_refresh=force_refresh)
    if block:
        raise RuntimeError(str(block.get("user_message") or _USER_MESSAGE))


def stack_status_message(probe: dict[str, Any] | None = None) -> str:
    """Single line for trade status / stack hub section."""
    from trade_integrations.dataflows.hub_wiki.config import llm_wiki_base_url

    payload = probe if probe is not None else probe_llm_wiki()
    base = str(payload.get("base_url") or llm_wiki_base_url())
    if payload.get("ok"):
        pid = str(payload.get("project_id") or "")
        return f"✓ LLM-Wiki running at {base} (project {pid}, search ok)"
    return f"✗ LLM-Wiki not running — please start LLM Wiki.app for news ingest ({base})"


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Probe LLM-Wiki for hub news pipeline")
    parser.add_argument("--json", action="store_true", help="Print JSON probe result")
    parser.add_argument("--status-line", action="store_true", help="Print trade status line")
    args = parser.parse_args()
    probe = probe_llm_wiki()
    if args.status_line:
        print(stack_status_message(probe))
    elif args.json:
        print(json.dumps(probe, indent=2))
    else:
        print(stack_status_message(probe))
    return 0 if probe.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
