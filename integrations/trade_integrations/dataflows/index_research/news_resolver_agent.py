"""T4 gray-zone resolver — MiniMax adjudication for ambiguous staging matches."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_GRAY_LOW = 0.60
_DEFAULT_MAX_PER_DRAIN = 10


def resolver_agent_gray_low() -> float:
    try:
        return float(os.getenv("HUB_NEWS_RESOLVER_AGENT_GRAY_LOW", str(_DEFAULT_GRAY_LOW)))
    except ValueError:
        return _DEFAULT_GRAY_LOW


def resolver_agent_max_per_drain() -> int:
    try:
        return max(0, int(os.getenv("HUB_NEWS_RESOLVER_AGENT_MAX_PER_DRAIN", str(_DEFAULT_MAX_PER_DRAIN))))
    except ValueError:
        return _DEFAULT_MAX_PER_DRAIN


def resolver_agent_enabled() -> bool:
    flag = os.getenv("HUB_NEWS_RESOLVER_AGENT_ENABLED", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    from trade_integrations.nse_browser.minimax_agent import minimax_configured

    return minimax_configured()


def _resolver_agent_model() -> str:
    return os.getenv("HUB_NEWS_RESOLVER_AGENT_MODEL", "MiniMax-M3").strip()


def _parse_agent_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def adjudicate_gray_zone(
    ref: dict[str, Any],
    *,
    candidate: dict[str, Any],
    match_score: float,
    ticker: str,
) -> dict[str, Any]:
    """Return {action, event_id?, reason} for ambiguous match scores."""
    from trade_integrations.nse_browser.minimax_agent import (
        chat_completions_create,
        extract_message_content,
        minimax_configured,
    )

    if not minimax_configured():
        return {"action": "create", "reason": "agent_unconfigured"}

    cand_id = str(candidate.get("canonical_story_id") or candidate.get("event_id") or "")
    prompt = {
        "task": "Resolve whether a staging news ref enriches an existing hub event, should be discarded as duplicate, or needs a new event.",
        "ticker": ticker,
        "match_score": round(match_score, 3),
        "ref": {
            "title": ref.get("title") or "",
            "summary": (ref.get("summary") or "")[:1200],
            "url": ref.get("url") or "",
            "published_at": ref.get("published_at") or "",
        },
        "candidate_event": {
            "event_id": cand_id,
            "title": candidate.get("title") or "",
            "summary": (candidate.get("content_summary") or candidate.get("content") or "")[:1200],
            "published_at": candidate.get("published_at") or "",
        },
        "allowed_actions": ["enrich", "discard", "create"],
        "response_schema": {
            "action": "enrich|discard|create",
            "event_id": "required when action=enrich",
            "reason": "short string",
        },
    }
    system = (
        "You are a hub news dedup resolver. Output ONLY valid JSON matching response_schema. "
        "Choose enrich when the ref adds the same story with new non-contradictory facts; "
        "discard when syndicated with no new facts; create when it is a distinct story."
    )
    try:
        response = chat_completions_create(
            model=_resolver_agent_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_completion_tokens=512,
        )
        message = response.choices[0].message
        parsed = _parse_agent_json(extract_message_content(message))
    except Exception as exc:
        logger.debug("T4 resolver agent failed: %s", exc)
        return {"action": "create", "reason": "agent_error"}

    action = str(parsed.get("action") or "").strip().lower()
    if action not in {"enrich", "discard", "create"}:
        return {"action": "create", "reason": "agent_invalid_action"}
    event_id = str(parsed.get("event_id") or cand_id).strip()
    if action == "enrich" and not event_id:
        return {"action": "create", "reason": "agent_missing_event_id"}
    return {
        "action": action,
        "event_id": event_id if action == "enrich" else "",
        "reason": str(parsed.get("reason") or "agent_gray_zone")[:200],
    }
