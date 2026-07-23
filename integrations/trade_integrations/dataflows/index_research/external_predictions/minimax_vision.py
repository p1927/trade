"""MiniMax M3 multimodal helpers for external prediction vision."""

from __future__ import annotations

import logging
import os
from typing import Any

from trade_integrations.nse_browser.minimax_agent import (
    _model,
    _parse_json_response,
    chat_completions_create,
    extract_message_content,
    minimax_configured,
)

logger = logging.getLogger(__name__)


def vision_enabled() -> bool:
    default = "1" if minimax_configured() else "0"
    return os.environ.get("EXTERNAL_PREDICTIONS_EXPERT_VISION", default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def call_minimax_vision_json(
    *,
    system_prompt: str,
    user_text: str,
    image_jpeg_b64_list: list[str],
    max_tokens: int = 1400,
) -> dict[str, Any]:
    if not minimax_configured():
        raise RuntimeError("MINIMAX_API_KEY not configured for vision extraction")

    user_parts: list[dict[str, Any]] = [{"type": "text", "text": user_text[:12000]}]
    for blob in image_jpeg_b64_list:
        if not blob:
            continue
        user_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{blob}"},
            }
        )

    response = chat_completions_create(
        model=_model(),
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_parts},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    message = response.choices[0].message
    content = extract_message_content(message)
    return _parse_json_response(content)


def vision_cross_check(
    *,
    target_mid: float | None,
    target_date: str,
    direction: str,
    image_jpeg_b64_list: list[str],
    url: str,
    title: str,
) -> tuple[bool, str]:
    """Return (ok, error_message) after multimodal verification."""
    if not image_jpeg_b64_list:
        return True, ""
    if target_mid is None:
        return False, "vision_no_target"

    prompt = (
        f"Article URL: {url}\nTitle: {title}\n"
        f"Extracted NIFTY 50 target mid: {target_mid:,.0f}\n"
        f"Extracted target date: {target_date or 'unknown'}\n"
        f"Direction: {direction}\n\n"
        "Look at the screenshot(s). Return ONLY JSON:\n"
        '{"supports_forecast": true|false, "reason": "brief explanation"}\n'
        "Set supports_forecast=false if the visible page does not show this NIFTY 50 index target "
        "or contradicts the extracted level/date. "
        "Technical resistance/support levels (e.g. 'face resistance near 24,000') are NOT analyst "
        "price targets unless the article explicitly states a NIFTY 50 target at that level."
    )
    try:
        payload = call_minimax_vision_json(
            system_prompt=(
                "You verify NIFTY 50 index street forecasts from financial news screenshots. "
                "Be strict about index-level targets, not single-stock prices."
            ),
            user_text=prompt,
            image_jpeg_b64_list=image_jpeg_b64_list,
            max_tokens=600,
        )
    except Exception as exc:
        logger.warning("vision cross-check failed: %s", exc)
        return False, f"vision_unavailable: {exc}"

    if payload.get("supports_forecast") is False:
        reason = str(payload.get("reason") or "vision_mismatch").strip()
        return False, reason or "vision_mismatch"
    return True, ""
