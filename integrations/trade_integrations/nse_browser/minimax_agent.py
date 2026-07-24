"""MiniMax M3 agent for reading NSE/NSDL pages and extracting structured data."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "MiniMax-M3"
_MAX_HTML_CHARS = int(os.environ.get("NSE_BROWSER_AGENT_MAX_HTML", "48000"))
_THINK_BLOCK = re.compile(
    r"<\s*(?:redacted_)?think(?:ing)?\s*>.*?<\s*/\s*(?:redacted_)?think(?:ing)?\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN = re.compile(r"<\s*(?:redacted_)?think(?:ing)?\s*>.*", re.DOTALL | re.IGNORECASE)


def _api_key() -> str:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if key:
        return key
    return os.environ.get("MINIMAX_CN_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").strip()


def _model() -> str:
    return os.environ.get("NSE_BROWSER_AGENT_MODEL", _DEFAULT_MODEL).strip()


def _is_minimax_model(model: str | None = None) -> bool:
    name = (model or _model()).strip().lower()
    return name.startswith("minimax")


def _default_completion_tokens() -> int:
    """When callers omit a limit, avoid MiniMax's tiny implicit output cap."""
    try:
        return max(256, int(os.getenv("MINIMAX_DEFAULT_COMPLETION_TOKENS", "4096")))
    except ValueError:
        return 4096


def normalize_completion_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Map deprecated ``max_tokens`` → ``max_completion_tokens`` for MiniMax-M3."""
    out = dict(kwargs)
    model = str(out.get("model") or _model())
    if not _is_minimax_model(model):
        return out

    if out.get("max_completion_tokens") is None:
        if out.get("max_tokens") is not None:
            out["max_completion_tokens"] = out.pop("max_tokens")
        else:
            out["max_completion_tokens"] = _default_completion_tokens()
    else:
        out.pop("max_tokens", None)

    out.pop("max_tokens", None)
    return out


def strip_minimax_thinking(text: str | None) -> str:
    """Remove MiniMax chain-of-thought blocks from model output."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    cleaned = _THINK_OPEN.sub("", cleaned).strip()
    return cleaned


def extract_message_content(message: Any) -> str:
    """Return user-facing text from ``content`` only — never ``reasoning_content``."""
    content = str(getattr(message, "content", None) or "")
    return strip_minimax_thinking(content)


def _env_truthy(name: str, *, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def minimax_reasoning_split_enabled() -> bool:
    """When True, thinking lands in ``reasoning_content`` / ``reasoning_details``, not ``content``."""
    if "MINIMAX_REASONING_SPLIT" in os.environ:
        return _env_truthy("MINIMAX_REASONING_SPLIT", default="1")
    return _env_truthy("MINIMAX_DISTILL_REASONING_SPLIT", default="1")


def minimax_thinking_disabled() -> bool:
    """Opt-out for MiniMax adaptive thinking (default: reasoning enabled)."""
    if _env_truthy("MINIMAX_THINKING_DISABLED", default="0"):
        return True
    if "MINIMAX_JSON_THINKING_DISABLED" in os.environ:
        return _env_truthy("MINIMAX_JSON_THINKING_DISABLED", default="0")
    return False


def merge_minimax_extra_body(extra_body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply MiniMax-M3 defaults: adaptive thinking + reasoning_split.

    See https://platform.minimax.io/docs/guides/text-m3-function-call
    """
    body = dict(extra_body or {})
    if minimax_reasoning_split_enabled():
        body.setdefault("reasoning_split", True)
    if minimax_thinking_disabled():
        body["thinking"] = {"type": "disabled"}
    else:
        body.setdefault("thinking", {"type": "adaptive"})
    return body


def apply_minimax_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OpenAI-compat chat/completions request body for MiniMax-M3."""
    if payload.get("max_completion_tokens") is None:
        if payload.get("max_tokens") is not None:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        else:
            payload["max_completion_tokens"] = _default_completion_tokens()
    else:
        payload.pop("max_tokens", None)

    existing = payload.get("extra_body")
    payload["extra_body"] = merge_minimax_extra_body(
        existing if isinstance(existing, dict) else None,
    )
    return payload


def minimax_configured() -> bool:
    return bool(_api_key())


def _client():
    from openai import OpenAI

    return OpenAI(api_key=_api_key(), base_url=_base_url())


def chat_completions_create(**kwargs: Any) -> Any:
    """Queue-backed MiniMax chat completion (serialized + rate-limit retry)."""
    from trade_integrations.nse_browser.minimax_queue import chat_completions_create as _queued_create

    normalized = normalize_completion_kwargs(kwargs)
    model = str(normalized.get("model") or _model())
    if _is_minimax_model(model):
        apply_minimax_request_payload(normalized)
    try:
        from trade_integrations.observability.hooks import llm_call_span

        with llm_call_span(provider="minimax", model=model, tier="pipeline") as llm_meta:
            result = _queued_create(_client(), **normalized)
            llm_meta["messages"] = len(normalized.get("messages") or [])
            return result
    except ImportError:
        return _queued_create(_client(), **normalized)


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_HTML_CHARS]


def _parse_json_response(text: str) -> dict[str, Any]:
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


def analyze_page(
    *,
    page_url: str,
    goal: str,
    html: str,
    visible_text: str = "",
    schema: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Ask MiniMax to extract structured data from a page snapshot.

    Returns JSON dict per schema keys (download_urls, table_rows, csv_urls, notes, ...).
    """
    if not minimax_configured():
        raise RuntimeError(
            "MINIMAX_API_KEY not set — required for NSE browser agent. "
            "Set MINIMAX_API_KEY and MINIMAX_BASE_URL in .env"
        )

    schema = schema or {
        "download_urls": "list of absolute CSV/download URLs found on page",
        "table_rows": "list of dict rows if tables are visible",
        "api_urls": "list of XHR/API URLs referenced in page scripts",
        "notes": "short string explaining what you found",
    }
    stripped = visible_text or _strip_html(html)
    user_content = (
        f"Page URL: {page_url}\n"
        f"Goal: {goal}\n\n"
        f"Visible text (truncated):\n{stripped[:12000]}\n\n"
        f"HTML snippet (truncated):\n{(html or '')[:8000]}\n\n"
        f"Return ONLY valid JSON with these fields:\n{json.dumps(schema, indent=2)}\n"
        "Do not invent numbers — extract only what is present. Use empty lists if not found."
    )

    try:
        response = chat_completions_create(
            model=_model(),
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise financial data extraction agent for NSE India and NSDL pages. "
                        "Output strict JSON only. Never hallucinate trading figures."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("MiniMax API call failed: %s", exc)
        raise

    content = ""
    if response.choices:
        content = extract_message_content(response.choices[0].message)
    return _parse_json_response(content)


def discover_from_page(*, page_url: str, goal: str, html: str, visible_text: str = "") -> dict[str, Any]:
    return analyze_page(
        page_url=page_url,
        goal=goal,
        html=html,
        visible_text=visible_text,
        schema={
            "download_urls": "absolute URLs for CSV/Excel downloads",
            "api_urls": "NSE api/* endpoints referenced in scripts or network hints",
            "click_selectors": "CSS selectors or link text to click for downloads",
            "notes": "brief explanation",
        },
    )


def extract_tables_from_page(*, page_url: str, goal: str, html: str, visible_text: str = "") -> dict[str, Any]:
    return analyze_page(
        page_url=page_url,
        goal=goal,
        html=html,
        visible_text=visible_text,
        schema={
            "table_rows": "list of row dicts with date, category, buy, sell, net fields where visible",
            "download_urls": "CSV download links if any",
            "notes": "brief explanation",
        },
    )


def plan_browser_action(
    *,
    page_url: str,
    goal: str,
    visible_text: str,
    html: str = "",
    screenshot_b64: str | None = None,
    step: int = 1,
    max_steps: int = 4,
) -> dict[str, Any]:
    """
    MiniMax returns one browser action for observe-act loop.

    action: click | scroll | wait | done
    target: link text or CSS selector (for click)
    """
    if not minimax_configured():
        return {"action": "done", "reason": "minimax_not_configured"}

    use_vision = os.environ.get("NSE_BROWSER_AGENT_VISION", "0").strip().lower() in {"1", "true", "yes"}
    stripped = visible_text or _strip_html(html)
    user_parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Page URL: {page_url}\n"
                f"Goal: {goal}\n"
                f"Step {step} of {max_steps}\n\n"
                f"Visible text:\n{stripped[:10000]}\n\n"
                "Return ONLY JSON: "
                '{"action":"click|scroll|wait|done","target":"link text or css selector","reason":"..."}'
            ),
        }
    ]
    if use_vision and screenshot_b64:
        user_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
            }
        )

    try:
        response = chat_completions_create(
            model=_model(),
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You operate a browser on NSE India pages. "
                        "Prefer scroll/wait over click. Never click Download unless goal requires it. "
                        "Output strict JSON only."
                    ),
                },
                {"role": "user", "content": user_parts if use_vision and screenshot_b64 else user_parts[0]["text"]},
            ],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("MiniMax plan_browser_action failed: %s", exc)
        return {"action": "done", "reason": str(exc)}

    content = ""
    if response.choices:
        content = extract_message_content(response.choices[0].message)
    payload = _parse_json_response(content)
    action = str(payload.get("action") or "done").lower()
    if action not in {"click", "scroll", "wait", "done"}:
        action = "done"
    return {
        "action": action,
        "target": str(payload.get("target") or ""),
        "reason": str(payload.get("reason") or ""),
    }
