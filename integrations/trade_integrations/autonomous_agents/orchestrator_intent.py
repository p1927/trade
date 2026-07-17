"""Parse orchestrator chat into propose_autonomous_agent kwargs + auto-propose fallback."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from trade_integrations.autonomous_agents.symbol_extract import extract_orchestrator_symbols
from trade_integrations.dataflows.symbol_registry.openalgo_registry import search_india_symbols

logger = logging.getLogger(__name__)

_PROPOSE_TOOL = "propose_autonomous_agent"
_PROPOSAL_ID_RE = re.compile(r"\baap_[0-9a-f]{8,}\b", re.I)
_CREATE_INTENT_RE = re.compile(
    r"\b("
    r"create|build|set\s*up|setup|start|make|launch|new"
    r")\s+(an?\s+)?(autonomous\s+)?(agent|bot|trader)\b",
    re.I,
)
_ADJUST_INTENT_RE = re.compile(
    r"\b(adjust|change|update|modify|instead|make\s+it|lower|raise|increase|decrease|re-propose|repropose)\b",
    re.I,
)
_PAPER_TRADE_RE = re.compile(r"\bpaper\s+trade\b", re.I)
_AUTONOMOUS_RE = re.compile(r"\bautonomous\b", re.I)
_INTRADAY_RE = re.compile(r"\bintraday\b", re.I)
_SWING_RE = re.compile(r"\bswing\b", re.I)
_US_HINT_RE = re.compile(r"\b(us|usa|alpaca|nasdaq|nyse|america|usd|dollar)\b|\$", re.I)
_IN_HINT_RE = re.compile(r"\b(india|indian|nse|bse|nifty|banknifty|₹|inr|openalgo)\b", re.I)
_AMOUNT_RE = re.compile(
    r"(?:budget|₹|\$|inr|usd|rs\.?|loss(?:\s+limit)?|max(?:imum)?\s+(?:daily\s+)?loss)"
    r"[^\d]{0,4}(\d+(?:,\d{3})*(?:\.\d+)?)\s*([kKmMlL]|lakh|lac|cr|crore|million|billion)?",
    re.I,
)
_WATCH_MIN_RE = re.compile(r"(?:watch|check|poll)\s+(?:every\s+)?(\d+)\s*min", re.I)
_NAME_TOKEN_RE = re.compile(r"\b([a-z]{4,})\b")
_NAME_SEARCH_SKIP = frozenset(
    {
        "agent",
        "autonomous",
        "budget",
        "create",
        "every",
        "intraday",
        "paper",
        "please",
        "start",
        "swing",
        "trade",
        "watch",
        "maximum",
        "minimum",
    }
)
_TRADING_GOAL_RE = re.compile(
    r"\b("
    r"paper\s+trade|watch|swing|intraday|trade|invest|monitor|"
    r"max\s+loss|budget|autonomous|agent"
    r")\b",
    re.I,
)


def orchestrator_auto_propose_enabled() -> bool:
    raw = os.getenv("ORCHESTRATOR_AUTO_PROPOSE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _normalize_amount(raw: str, suffix: str | None) -> float:
    value = float(raw.replace(",", ""))
    if not suffix:
        return value
    s = suffix.lower()
    if s in {"k", "thousand"}:
        return value * 1_000
    if s in {"m", "million"}:
        return value * 1_000_000
    if s in {"l", "lakh", "lac"}:
        return value * 100_000
    if s in {"cr", "crore"}:
        return value * 10_000_000
    if s == "billion":
        return value * 1_000_000_000
    return value


def _extract_amounts(text: str) -> tuple[float | None, float | None]:
    budget: float | None = None
    max_loss: float | None = None
    for match in _AMOUNT_RE.finditer(text):
        snippet = text[max(0, match.start() - 10) : match.end()].lower()
        amount = _normalize_amount(match.group(1), match.group(2))
        if "loss" in snippet:
            max_loss = amount
        elif budget is None:
            budget = amount
    return budget, max_loss


def _extract_symbols(text: str) -> list[str]:
    found = extract_orchestrator_symbols(text)
    if found:
        return found
    return _search_symbols_from_name_tokens(text)


def _search_symbols_from_name_tokens(text: str) -> list[str]:
    lower = (text or "").lower()
    for match in _NAME_TOKEN_RE.finditer(lower):
        token = match.group(1)
        if token in _NAME_SEARCH_SKIP:
            continue
        hits = search_india_symbols(token, limit=1)
        if hits:
            sym = str(hits[0].get("symbol") or "").upper()
            if sym:
                return [sym]
    return []


def _has_trading_goal(text: str) -> bool:
    return bool(_TRADING_GOAL_RE.search(text))


def _has_create_intent(text: str) -> bool:
    return bool(
        _CREATE_INTENT_RE.search(text)
        or _PAPER_TRADE_RE.search(text)
        or (_AUTONOMOUS_RE.search(text) and re.search(r"\b(agent|trade|watch)\b", text, re.I))
    )


def _has_symbol_plus_goal_intent(text: str) -> bool:
    """Symbol + trading goal without explicit 'create agent' phrasing."""
    return bool(_extract_symbols(text) and _has_trading_goal(text))


def _has_adjust_intent(text: str) -> bool:
    return bool(_ADJUST_INTENT_RE.search(text))


def _assistant_hallucinated_proposal_id(text: str) -> bool:
    return bool(_PROPOSAL_ID_RE.search(text))


def _default_symbol(*, text: str) -> str | None:
    if _US_HINT_RE.search(text) and not _IN_HINT_RE.search(text):
        return "SPY"
    if _IN_HINT_RE.search(text) or not _US_HINT_RE.search(text):
        return "NIFTY"
    return None


def _infer_mandate(text: str, symbols: list[str]) -> str:
    sym = symbols[0] if symbols else "NIFTY"
    parts: list[str] = [f"Paper trade {sym} autonomously"]
    if _INTRADAY_RE.search(text):
        parts.append("intraday (flat by close)")
    elif _SWING_RE.search(text):
        parts.append("multi-day swing")
    if _AUTONOMOUS_RE.search(text):
        parts.append("research, watch, act when confident")
    return "; ".join(parts) + "."


def build_auto_propose_kwargs(
    *,
    user_message: str,
    assistant_text: str = "",
    orchestrator_session_id: str,
) -> dict[str, Any] | None:
    """Build kwargs for propose_autonomous_agent when the LLM skipped the tool."""
    text = f"{user_message}\n{assistant_text}".strip()
    symbols = _extract_symbols(user_message) or _extract_symbols(text)
    latest: dict[str, Any] | None = None

    if orchestrator_session_id:
        try:
            from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator

            latest = load_latest_proposal_for_orchestrator(orchestrator_session_id)
            if latest and not latest.get("committed_agent_id") and not symbols:
                symbols = list(latest.get("symbols") or [])
        except Exception:
            logger.debug("latest proposal lookup failed", exc_info=True)

    create_intent = _has_create_intent(user_message)
    symbol_goal_intent = _has_symbol_plus_goal_intent(user_message)
    adjust_intent = _has_adjust_intent(user_message)
    hallucinated = _assistant_hallucinated_proposal_id(assistant_text)

    if not symbols:
        if create_intent or symbol_goal_intent or hallucinated:
            default_sym = _default_symbol(text=text)
            if default_sym:
                symbols = [default_sym]
        elif adjust_intent and latest:
            symbols = list(latest.get("symbols") or [])

    if not symbols:
        return None

    if not (create_intent or symbol_goal_intent or adjust_intent or hallucinated):
        return None

    budget, max_loss = _extract_amounts(user_message)
    watch_match = _WATCH_MIN_RE.search(user_message)

    kwargs: dict[str, Any] = {
        "symbols": symbols,
        "orchestrator_session_id": orchestrator_session_id,
    }

    if latest and not latest.get("committed_agent_id"):
        if latest.get("name"):
            kwargs["name"] = latest["name"]
        if latest.get("mandate") and not _has_create_intent(user_message):
            kwargs["mandate"] = latest["mandate"]
        constraints = dict(latest.get("constraints") or {})
        if budget is None and constraints.get("budget_inr") is not None:
            kwargs["budget_inr"] = constraints["budget_inr"]
        if max_loss is None and constraints.get("max_daily_loss_inr") is not None:
            kwargs["max_daily_loss_inr"] = constraints["max_daily_loss_inr"]
        if constraints.get("confidence_threshold") is not None:
            kwargs["confidence_threshold"] = constraints["confidence_threshold"]
        schedules = dict(latest.get("schedules") or {})
        if not watch_match and schedules.get("watch_ms"):
            kwargs["watch_interval_min"] = max(1, int(schedules["watch_ms"]) // 60_000)

    if budget is not None:
        kwargs["budget_inr"] = budget
    if max_loss is not None:
        kwargs["max_daily_loss_inr"] = max_loss
    if watch_match:
        kwargs["watch_interval_min"] = int(watch_match.group(1))

    if "mandate" not in kwargs:
        kwargs["mandate"] = _infer_mandate(user_message, symbols)

    kwargs["user_text"] = user_message
    if _IN_HINT_RE.search(user_message) and not _US_HINT_RE.search(user_message):
        kwargs["execution_market"] = "IN"
    elif _US_HINT_RE.search(user_message) and not _IN_HINT_RE.search(user_message):
        kwargs["execution_market"] = "US"

    sym0 = symbols[0]
    if not kwargs.get("name"):
        kwargs["name"] = f"{sym0} autonomous"

    return kwargs


def maybe_auto_propose_after_orchestrator_turn(
    *,
    orchestrator_session_id: str,
    user_message: str,
    assistant_text: str,
    tools_called: list[str] | set[str],
) -> dict[str, Any] | None:
    """Server fallback: persist + return a proposal when the LLM skipped the tool."""
    if not orchestrator_auto_propose_enabled():
        return None
    called = {str(t) for t in tools_called}
    if any(_PROPOSE_TOOL in name or name == _PROPOSE_TOOL for name in called):
        return None

    kwargs = build_auto_propose_kwargs(
        user_message=user_message,
        assistant_text=assistant_text,
        orchestrator_session_id=orchestrator_session_id,
    )
    if kwargs is None:
        return None

    from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent

    result = propose_autonomous_agent(**kwargs)
    if result.get("status") not in {"ready", "incomplete"}:
        return None

    proposal = result.get("proposal")
    if isinstance(proposal, dict):
        proposal["session_id"] = orchestrator_session_id
        proposal["auto_proposed"] = True
        result["proposal"] = proposal
        logger.info(
            "orchestrator auto-propose fallback created proposal %s for session %s",
            proposal.get("proposal_id"),
            orchestrator_session_id,
        )
    return result
