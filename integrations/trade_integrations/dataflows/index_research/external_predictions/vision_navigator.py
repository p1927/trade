"""MiniMax vision planner for blocked-page navigation recovery."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from trade_integrations.dataflows.index_research.external_predictions.minimax_vision import (
    call_minimax_vision_json,
)
from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
    decode_screenshot_payload,
    resize_for_m3_tiles,
)
from trade_integrations.nse_browser.minimax_agent import minimax_configured

logger = logging.getLogger(__name__)

VisionNavActionKind = Literal[
    "click_text",
    "click_selector",
    "press_key",
    "scroll",
    "wait",
    "done",
]

_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"click_text", "click_selector", "press_key", "scroll", "wait", "done"}
)
_MAX_ACTIONS = 5
_FORBIDDEN_CLICK_TEXT = re.compile(r"^\s*enable\s*$", re.I)
_GOTO_PATTERN = re.compile(r"^\s*goto\s*:", re.I)

_SYSTEM_PROMPT = (
    "You help a financial researcher clear popups and reach NIFTY 50 forecast content "
    "on Indian financial news sites. Return ONLY JSON:\n"
    '{"actions": [{"action": "click_text", "target": "Maybe Later", "reason": "dismiss notification"}], '
    '"page_clear": true}\n'
    "Allowed actions: click_text, click_selector, press_key, scroll, wait, done. "
    "Prefer click_text for ET/Moneycontrol modals (Accept All, Maybe Later, ×). "
    "Never click Enable on push notifications — use Maybe Later or close instead. "
    "Max 5 actions. No goto or off-domain navigation."
)

_BROWSE_SYSTEM_PROMPT = (
    "You help a financial researcher pick the next link on an Indian financial news listing "
    "to reach a NIFTY 50 weekly/index forecast article. Return ONLY JSON:\n"
    '{"next_url": "https://example.com/.../articleshow/123.cms", "pick": 1, "reason": "..."}\n'
    "Use next_url when you can identify the href from the screenshot or candidate list. "
    "Use pick (1-based index) when a numbered candidate list is provided. "
    "Return pick=0 and empty next_url when no suitable forecast link is visible. "
    "Never pick off-domain or generic home pages."
)

BrowseVisionGoal = Literal["pick_listing_link", "open_forecast_article"]


@dataclass(frozen=True)
class VisionBrowsePlan:
    next_url: str
    pick_index: int
    reason: str


class VisionNavAction(TypedDict):
    action: VisionNavActionKind
    target: str
    reason: str


def _screenshot_to_m3_b64_list(screenshot_b64: str) -> list[str]:
    raw = decode_screenshot_payload(screenshot_b64)
    if not raw:
        return []
    tiles = resize_for_m3_tiles(raw)
    return [base64.b64encode(tile).decode("ascii") for tile in tiles if tile]


def _is_forbidden_action(action: VisionNavAction) -> bool:
    kind = action.get("action") or ""
    target = str(action.get("target") or "")
    if kind == "click_text" and _FORBIDDEN_CLICK_TEXT.match(target):
        return True
    if _GOTO_PATTERN.match(target):
        return True
    lowered = target.lower()
    if "http://" in lowered or "https://" in lowered:
        return True
    return False


def _normalize_action(raw: dict[str, Any]) -> VisionNavAction | None:
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip()
    if action not in _ALLOWED_ACTIONS:
        return None
    target = str(raw.get("target") or "")
    reason = str(raw.get("reason") or "").strip()
    normalized: VisionNavAction = {
        "action": action,  # type: ignore[typeddict-item]
        "target": target,
        "reason": reason,
    }
    if _is_forbidden_action(normalized):
        return None
    return normalized


def validate_vision_actions(raw_actions: list[Any]) -> list[VisionNavAction]:
    """Parse and clamp MiniMax action list to the safety allowlist."""
    out: list[VisionNavAction] = []
    for row in raw_actions or []:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_action(row)
        if normalized is None:
            continue
        out.append(normalized)
        if len(out) >= _MAX_ACTIONS:
            break
    return out


def _build_user_prompt(
    *,
    url: str,
    goal: str,
    block_reasons: list[str],
    prior_actions: list[VisionNavAction] | None,
) -> str:
    lines = [
        f"URL: {url}",
        f"Goal: {goal}",
        f"Block reasons: {', '.join(block_reasons) if block_reasons else 'unknown'}",
    ]
    if prior_actions:
        lines.append("Prior failed actions (avoid repeating):")
        for idx, act in enumerate(prior_actions[:3], start=1):
            lines.append(
                f"  {idx}. {act.get('action')} target={act.get('target')!r} "
                f"reason={act.get('reason')!r}"
            )
    lines.append(
        "Return JSON with up to 5 actions to dismiss overlays and reveal forecast content."
    )
    return "\n".join(lines)


def plan_vision_navigation(
    *,
    screenshot_b64: str,
    url: str,
    goal: str,
    block_reasons: list[str],
    prior_actions: list[VisionNavAction] | None = None,
) -> list[VisionNavAction]:
    """Plan overlay-dismiss actions from a page screenshot via MiniMax M3."""
    if not minimax_configured():
        raise RuntimeError("MINIMAX_API_KEY not configured for vision navigation")

    images = _screenshot_to_m3_b64_list(screenshot_b64)
    if not images:
        logger.warning("vision navigation: no M3 tiles from screenshot for %s", url)
        return []

    user_text = _build_user_prompt(
        url=url,
        goal=goal,
        block_reasons=block_reasons,
        prior_actions=prior_actions,
    )
    try:
        payload = call_minimax_vision_json(
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            image_jpeg_b64_list=images,
            max_tokens=900,
        )
    except Exception as exc:
        logger.warning("vision navigation planner failed for %s: %s", url, exc)
        raise

    raw_actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(raw_actions, list):
        return []
    return validate_vision_actions(raw_actions)


def _build_browse_user_prompt(
    *,
    url: str,
    goal: BrowseVisionGoal,
    candidates: list[tuple[str, str]] | None,
) -> str:
    lines = [f"URL: {url}", f"Goal: {goal}"]
    if goal == "pick_listing_link":
        lines.append("Pick the listing link most likely to lead to a NIFTY 50 forecast article.")
    else:
        lines.append(
            "Identify the best visible link or article tile to open a NIFTY 50 forecast article."
        )
    if candidates:
        lines.append("Candidate links:")
        for idx, (title, href) in enumerate(candidates[:10], start=1):
            lines.append(f"  {idx}. {title or href} — {href}")
    lines.append('Return JSON: {"next_url": "...", "pick": <0-based or 1-based index>, "reason": "..."}')
    return "\n".join(lines)


def _resolve_browse_pick(
    payload: dict[str, Any],
    *,
    candidates: list[tuple[str, str]] | None,
) -> VisionBrowsePlan:
    reason = str(payload.get("reason") or "").strip()
    next_url = str(payload.get("next_url") or payload.get("url") or "").strip()
    if next_url.lower().startswith(("http://", "https://")):
        return VisionBrowsePlan(next_url=next_url, pick_index=0, reason=reason)

    pick_raw = payload.get("pick", payload.get("index", 0))
    try:
        pick_idx = int(pick_raw)
    except (TypeError, ValueError):
        pick_idx = 0
    if candidates and pick_idx >= 1 and pick_idx <= len(candidates):
        _title, href = candidates[pick_idx - 1]
        return VisionBrowsePlan(next_url=href, pick_index=pick_idx, reason=reason)
    return VisionBrowsePlan(next_url="", pick_index=0, reason=reason)


def plan_vision_browse_next_url(
    *,
    screenshot_b64: str,
    url: str,
    goal: BrowseVisionGoal,
    candidates: list[tuple[str, str]] | None = None,
    block_reasons: list[str] | None = None,
) -> VisionBrowsePlan:
    """Plan browse navigation — return next_url from screenshot (and optional candidates)."""
    if not minimax_configured():
        raise RuntimeError("MINIMAX_API_KEY not configured for vision navigation")

    images = _screenshot_to_m3_b64_list(screenshot_b64)
    if not images:
        logger.warning("vision browse: no M3 tiles from screenshot for %s", url)
        return VisionBrowsePlan(next_url="", pick_index=0, reason="no_screenshot_tiles")

    user_text = _build_browse_user_prompt(url=url, goal=goal, candidates=candidates)
    if block_reasons:
        user_text = f"{user_text}\nBlock reasons: {', '.join(block_reasons)}"

    try:
        payload = call_minimax_vision_json(
            system_prompt=_BROWSE_SYSTEM_PROMPT,
            user_text=user_text,
            image_jpeg_b64_list=images,
            max_tokens=400,
        )
    except Exception as exc:
        logger.warning("vision browse planner failed for %s: %s", url, exc)
        raise

    if not isinstance(payload, dict):
        return VisionBrowsePlan(next_url="", pick_index=0, reason="invalid_payload")
    return _resolve_browse_pick(payload, candidates=candidates)


def vision_nav_goal_from_block_reasons(block_reasons: list[str]) -> str:
    """Map block reasons to a planner goal string."""
    if not block_reasons:
        return "dismiss_overlays"
    if any("cookie" in r or "notification" in r or "overlay" in r for r in block_reasons):
        return "dismiss_overlays"
    if any("thin" in r or "footer" in r for r in block_reasons):
        return "listing_forecast"
    return "dismiss_overlays"
