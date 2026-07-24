"""Execute vision navigation actions on a Playwright page."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.pipeline import (
    PipelineLogger,
)
from trade_integrations.dataflows.index_research.external_predictions.vision_navigator import (
    VisionNavAction,
)

logger = logging.getLogger(__name__)

_SCROLL_RE = re.compile(r"^(up|down):(\d+)$", re.I)
_ALLOWED_KEYS = frozenset({"Home", "Escape", "PageDown"})

_JS_CLICK_BY_TEXT = """
(target) => {
  const needle = (target || "").trim().toLowerCase();
  if (!needle) return false;
  const nodes = document.querySelectorAll("a, button, span, div, input[type='button'], [role='button']");
  for (const el of nodes) {
    const text = (el.textContent || el.value || el.getAttribute("aria-label") || "").trim();
    if (!text) continue;
    if (text.toLowerCase().includes(needle)) {
      el.click();
      return true;
    }
  }
  return false;
}
"""


async def _click_text_with_fallback(page: Any, target: str) -> None:
    try:
        locator = page.get_by_text(target, exact=False).first
        await locator.click(timeout=3000)
    except Exception as primary_exc:
        clicked = await page.evaluate(_JS_CLICK_BY_TEXT, target)
        if clicked:
            return
        raise primary_exc


async def _click_selector(page: Any, target: str) -> None:
    await page.locator(target).first.click(timeout=3000)


async def _press_key(page: Any, target: str) -> None:
    key = str(target or "").strip()
    if key not in _ALLOWED_KEYS:
        raise ValueError(f"Unsupported key: {key}")
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None and hasattr(keyboard, "press"):
        await keyboard.press(key)
        return
    press = getattr(page, "press", None)
    if callable(press):
        await press(key)
        return
    raise RuntimeError("Page has no keyboard.press or press method")


async def _scroll(page: Any, target: str) -> None:
    match = _SCROLL_RE.match(str(target or "").strip())
    if not match:
        raise ValueError(f"Invalid scroll target: {target!r}")
    direction = match.group(1).lower()
    amount = int(match.group(2))
    delta = amount if direction == "down" else -amount
    await page.evaluate("(dy) => window.scrollBy(0, dy)", delta)


async def _wait(target: str) -> None:
    raw = str(target or "").strip()
    try:
        ms = int(raw or "500")
    except ValueError:
        ms = 500
    await asyncio.sleep(max(0.0, ms / 1000.0))


async def execute_vision_actions(
    page: Any,
    actions: list[VisionNavAction],
    *,
    pipeline: PipelineLogger | None = None,
) -> dict[str, Any]:
    """Run planned vision actions; collect per-step errors without aborting."""
    executed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, action in enumerate(actions):
        kind = str(action.get("action") or "")
        target = str(action.get("target") or "")
        reason = str(action.get("reason") or "")
        label = f"{kind}:{target or '-'}"
        try:
            if kind == "click_text":
                await _click_text_with_fallback(page, target)
            elif kind == "click_selector":
                await _click_selector(page, target)
            elif kind == "press_key":
                await _press_key(page, target)
            elif kind == "scroll":
                await _scroll(page, target)
            elif kind == "wait":
                await _wait(target)
            elif kind == "done":
                executed.append({"index": idx, "action": kind, "target": target, "reason": reason})
                break
            else:
                raise ValueError(f"Unsupported action: {kind}")
            executed.append({"index": idx, "action": kind, "target": target, "reason": reason})
            if pipeline:
                pipeline.info("vision_nav", f"Executed {label}", url=target or None)
        except Exception as exc:
            msg = str(exc)
            errors.append({"index": idx, "action": kind, "target": target, "error": msg})
            logger.debug("vision action failed %s: %s", label, msg)
            if pipeline:
                pipeline.warn("vision_nav", f"Failed {label}: {msg}")

    return {"executed": executed, "errors": errors}
