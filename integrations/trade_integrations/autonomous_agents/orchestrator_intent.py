"""Parse orchestrator chat into propose_autonomous_agent kwargs + auto-propose fallback."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from trade_integrations.autonomous_agents.symbol_extract import extract_orchestrator_symbols
from trade_integrations.dataflows.symbol_registry.openalgo_registry import (
    is_symbol_known_for_proposal,
    search_india_symbols,
)

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
_PROPOSAL_READY_RE = re.compile(
    r"\b("
    r"confirm the (proposal )?card|proposal (is )?ready|card above|"
    r"tap confirm|click confirm|use the card|see the card|proposal card"
    r")\b",
    re.I,
)
_CARD_CREATE_RE = re.compile(
    r"\b(create|show|select|make|generate|display)\s+(the\s+)?(proposal\s+)?card\b",
    re.I,
)
_INDEX_SYMBOLS = frozenset(
    {
        "NIFTY",
        "NIFTY50",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "BANKEX",
    }
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
        hits = search_india_symbols(token, limit=2)
        if len(hits) != 1:
            continue
        sym = str(hits[0].get("symbol") or "").upper()
        if sym and is_symbol_known_for_proposal(sym):
            return [sym]
    return []


def _us_market_explicit(text: str, symbols: list[str]) -> bool:
    from trade_integrations.autonomous_agents.market import symbol_execution_market

    if symbols and all(symbol_execution_market(str(s)) == "US" for s in symbols):
        return True
    stripped = re.sub(r"\$", " ", text or "")
    return bool(re.search(r"\b(us|usa|alpaca|nasdaq|nyse|america|usd|dollar)\b", stripped, re.I))


def _instruments_clarified(text: str) -> bool:
    return bool(re.search(r"\b(equity|equities|option|options|stock|stocks|fno|f&o)\b", text, re.I))


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


def assistant_claims_proposal_ready(text: str) -> bool:
    """Assistant prose implies a card exists without calling the propose tool."""
    blob = text or ""
    return bool(
        _PROPOSAL_READY_RE.search(blob)
        or _CARD_CREATE_RE.search(blob)
        or _assistant_hallucinated_proposal_id(blob)
    )


def orchestrator_has_propose_intent(user_message: str, assistant_text: str = "") -> bool:
    """True when the user turn warrants a proposal card."""
    text = user_message or ""
    if _has_create_intent(text) or _has_symbol_plus_goal_intent(text) or _has_adjust_intent(text):
        return True
    if assistant_claims_proposal_ready(assistant_text):
        return True
    return False



def _default_symbol(*, text: str) -> str | None:
    if _US_HINT_RE.search(text) and not _IN_HINT_RE.search(text):
        return "SPY"
    if _IN_HINT_RE.search(text) or not _US_HINT_RE.search(text):
        return "NIFTY"
    return None


def _infer_mandate(text: str, symbols: list[str]) -> str:
    sym = symbols[0] if symbols else "NIFTY"
    from trade_integrations.autonomous_agents.mandate_config import detect_observe_intent, observe_mandate_text

    if detect_observe_intent(text):
        return observe_mandate_text(sym)
    parts: list[str] = [f"Paper trade {sym} autonomously"]
    if _INTRADAY_RE.search(text):
        parts.append("intraday (flat by close)")
    elif _SWING_RE.search(text):
        parts.append("multi-day swing")
    if _AUTONOMOUS_RE.search(text):
        parts.append("research, watch, act when confident")
    return "; ".join(parts) + "."


def _symbol_proposable_for_auto_propose(symbol: str) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    if is_symbol_known_for_proposal(sym) or sym in {"SPY", "QQQ", "NVDA", "AAPL"}:
        return True
    try:
        from trade_integrations.dataflows.company_research.market import Market, detect_market

        return detect_market(sym) == Market.IN
    except Exception:
        return False


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
    hallucinated = assistant_claims_proposal_ready(assistant_text)

    if not symbols:
        if create_intent or symbol_goal_intent or hallucinated:
            default_sym = _default_symbol(text=text)
            if default_sym:
                symbols = [default_sym]
        elif adjust_intent and latest:
            symbols = list(latest.get("symbols") or [])

    if not symbols:
        return None

    if not all(_symbol_proposable_for_auto_propose(str(s)) for s in symbols):
        from trade_integrations.autonomous_agents.market import symbol_execution_market

        if not all(symbol_execution_market(str(s)) == "US" for s in symbols):
            return None

    instruments_missing = latest and "allowed_instruments" in list(latest.get("missing_fields") or [])
    if instruments_missing and not _instruments_clarified(user_message):
        if not adjust_intent:
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

    from trade_integrations.autonomous_agents.mandate_config import detect_observe_intent

    if detect_observe_intent(text):
        kwargs["agent_mode"] = "observe"
        kwargs["allowed_instruments"] = ["equity"]
        kwargs["mandate"] = _infer_mandate(user_message, symbols)

    kwargs["user_text"] = user_message
    if _IN_HINT_RE.search(user_message) and not _us_market_explicit(user_message, symbols):
        kwargs["execution_market"] = "IN"
    elif _us_market_explicit(user_message, symbols) and not _IN_HINT_RE.search(user_message):
        kwargs["execution_market"] = "US"

    sym0 = symbols[0]
    if not kwargs.get("name"):
        kwargs["name"] = f"{sym0} autonomous"

    kwargs = _merge_intent_into_propose_kwargs(
        kwargs,
        user_message=user_message,
        orchestrator_session_id=orchestrator_session_id,
        latest=latest,
    )
    return kwargs


def _load_prior_intent_from_latest(latest: dict[str, Any] | None) -> Any:
    if not latest:
        return None
    mc = latest.get("mandate_config") if isinstance(latest.get("mandate_config"), dict) else {}
    from trade_integrations.autonomous_agents.intent_store import load_intent_from_mandate_config

    return load_intent_from_mandate_config(mc)


def _merge_intent_into_propose_kwargs(
    kwargs: dict[str, Any],
    *,
    user_message: str,
    orchestrator_session_id: str,
    latest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Unified intent extraction — overrides legacy regex fields when explicit."""
    try:
        from trade_integrations.autonomous_agents.intent_extractor import extract_agent_intent
        from trade_integrations.autonomous_agents.intent_merge import intent_to_propose_kwargs
        from trade_integrations.autonomous_agents.mandate_config import observe_mandate_text

        prior = _load_prior_intent_from_latest(latest)
        result = extract_agent_intent(user_message, prior=prior, prefer_fast_path=True)
        intent = result.intent
        mapped = intent_to_propose_kwargs(intent)

        for key, value in mapped.items():
            if key == "intent":
                kwargs["intent"] = value
                continue
            if value is None:
                continue
            if key == "allowed_instruments" and intent.needs_clarification:
                if "instruments" in intent.needs_clarification:
                    kwargs.pop("allowed_instruments", None)
                    continue
            kwargs[key] = value

        if intent.engagement == "observe":
            sym = (intent.symbols or kwargs.get("symbols") or ["NIFTY"])[0]
            kwargs["agent_mode"] = "observe"
            kwargs["mandate"] = observe_mandate_text(str(sym))
        elif intent.clarified.get("engagement") and intent.engagement == "trade":
            kwargs.pop("agent_mode", None)

        kwargs["intent_source"] = result.source
        kwargs["intent_needs_clarification"] = list(intent.needs_clarification or [])
    except Exception:
        logger.debug("intent merge into auto-propose kwargs failed", exc_info=True)
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
        from trade_integrations.autonomous_agents.store import save_proposal

        save_proposal(proposal)
        result["proposal"] = proposal
        logger.info(
            "orchestrator auto-propose fallback created proposal %s for session %s",
            proposal.get("proposal_id"),
            orchestrator_session_id,
        )
    return result
