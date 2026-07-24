"""Extract agent intent from user messages — LLM primary, regex fast-path fallback."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from trade_integrations.autonomous_agents.intent_merge import merge_agent_intent, watch_conditions_from_dicts
from trade_integrations.autonomous_agents.intent_schema import (
    VALID_INSTRUMENTS,
    AgentIntent,
    EngagementMode,
    InstrumentClass,
    IntentDelta,
    WatchCondition,
    default_agent_intent,
    intent_json_schema_block,
)

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")
_WATCH_MIN_RE = re.compile(
    r"(?:watch|check|poll|watcher\s+should\s+run\s+on|every)\s+(?:every\s+)?(\d+)\s*(?:min(?:ute)?s?|m)\b",
    re.I,
)
_OBSERVE_RE = re.compile(
    r"\b("
    r"watch\s+(?:the\s+)?(?:nifty|banknifty|index|market)|"
    r"want\s+to\s+watch|just\s+watch|watch\s+only|monitor\s+(?:the\s+)?(?:index|nifty)|"
    r"observe\s+only|report\s+only|no\s+trading|don'?t\s+trade"
    r")\b",
    re.I,
)
_TRADE_RE = re.compile(
    r"\b(paper\s+trade|options?\s+trad|futures?\s+trad|enter\s+(?:a\s+)?trade|execute|straddle|strangle)\b",
    re.I,
)
_OPTIONS_RE = re.compile(
    r"\b(options?|straddle|strangle|iron\s+condor|option\s+chain|ce\b|pe\b)\b",
    re.I,
)
_FUTURES_RE = re.compile(r"\b(futures?|fno|f&o)\b", re.I)
_INDEX_ONLY_RE = re.compile(r"\b(nifty\s*50|nifty50|index\s+view|index\s+outlook)\b", re.I)
_POINTS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:pt|pts|point|points)\b", re.I)
_LEVEL_ABOVE_RE = re.compile(r"\b(?:above|over|break(?:out)?\s+(?:above|over))\s+(\d[\d,]*(?:\.\d+)?)\b", re.I)
_LEVEL_BELOW_RE = re.compile(r"\b(?:below|under|break(?:down)?\s+(?:below|under))\s+(\d[\d,]*(?:\.\d+)?)\b", re.I)

_LLMCaller = Callable[[str, int], str]


def intent_extractor_llm_enabled() -> bool:
    raw = os.getenv("INTENT_EXTRACTOR_LLM", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    candidates: list[str] = []
    match = _JSON_OBJECT.search(text)
    if match:
        candidates.append(match.group(0))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for blob in candidates:
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _normalize_instruments(raw: Any) -> list[InstrumentClass]:
    out: list[InstrumentClass] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        key = str(item).strip().lower()
        if key in VALID_INSTRUMENTS and key not in out:
            out.append(key)  # type: ignore[arg-type]
    return out


def _delta_from_payload(payload: dict[str, Any], *, source_message_id: str = "") -> IntentDelta:
    explicit = [str(x).strip() for x in (payload.get("explicit_fields") or []) if str(x).strip()]
    needs = [str(x).strip() for x in (payload.get("needs_clarification") or []) if str(x).strip()]
    engagement_raw = payload.get("engagement")
    engagement: EngagementMode | None = None
    if engagement_raw is not None:
        val = str(engagement_raw).strip().lower()
        if val in {"observe", "trade"}:
            engagement = val  # type: ignore[assignment]

    schedules: dict[str, int] | None = None
    if isinstance(payload.get("schedules"), dict):
        schedules = {}
        for key in ("watch_ms", "research_ms"):
            if key in payload["schedules"]:
                try:
                    schedules[key] = max(1, int(payload["schedules"][key]))
                except (TypeError, ValueError):
                    pass

    constraints: dict[str, Any] | None = None
    if isinstance(payload.get("constraints"), dict):
        constraints = dict(payload["constraints"])

    watch_conditions: list[WatchCondition] | None = None
    if isinstance(payload.get("watch_conditions"), list):
        parsed = watch_conditions_from_dicts(payload["watch_conditions"])
        watch_conditions = parsed

    threshold: int | None = None
    if payload.get("confidence_threshold") is not None:
        try:
            threshold = max(0, min(100, int(payload["confidence_threshold"])))
        except (TypeError, ValueError):
            threshold = None

    symbols: list[str] | None = None
    if isinstance(payload.get("symbols"), list):
        symbols = [str(s).strip().upper() for s in payload["symbols"] if str(s).strip()]

    instruments: list[InstrumentClass] | None = None
    if payload.get("instruments") is not None:
        instruments = _normalize_instruments(payload.get("instruments"))

    return IntentDelta(
        engagement=engagement,
        instruments=instruments,
        symbols=symbols,
        schedules=schedules,
        watch_conditions=watch_conditions,
        confidence_threshold=threshold,
        constraints=constraints,
        explicit_fields=explicit,
        needs_clarification=needs,
        source_message_id=source_message_id,
    )


def validate_intent_delta(delta: IntentDelta) -> list[str]:
    errors: list[str] = []
    if "symbols" in delta.explicit_fields and delta.symbols is not None and not delta.symbols:
        errors.append("symbols cannot be empty when explicitly set")
    if "engagement" in delta.explicit_fields and delta.engagement is None:
        errors.append("engagement must be observe or trade when explicitly set")
    if "instruments" in delta.explicit_fields and delta.instruments is not None and not delta.instruments:
        errors.append("instruments cannot be empty when explicitly set")
    if delta.instruments:
        for inst in delta.instruments:
            if inst not in VALID_INSTRUMENTS:
                errors.append(f"unsupported instrument: {inst}")
    for row in delta.watch_conditions or []:
        if not row.symbol:
            errors.append("watch condition requires symbol")
    return errors


def _extract_symbols(text: str) -> list[str]:
    try:
        from trade_integrations.autonomous_agents.symbol_extract import extract_orchestrator_symbols

        return extract_orchestrator_symbols(text)
    except Exception:
        return []


def _extract_amounts(text: str) -> tuple[float | None, float | None]:
    try:
        from trade_integrations.autonomous_agents.orchestrator_intent import _extract_amounts as extract

        return extract(text)
    except Exception:
        return None, None


def _build_watch_conditions(text: str, symbols: list[str]) -> list[WatchCondition]:
    sym = (symbols[0] if symbols else "NIFTY").upper()
    conditions: list[WatchCondition] = []
    watch_match = _WATCH_MIN_RE.search(text)
    if watch_match:
        every_min = int(watch_match.group(1))
        conditions.append(
            WatchCondition(
                kind="schedule",
                symbol=sym,
                params={"every_min": every_min},
                label=f"poll every {every_min} min",
            )
        )
    points_match = _POINTS_RE.search(text)
    if points_match:
        conditions.append(
            WatchCondition(
                kind="price_move",
                symbol=sym,
                params={"points": float(points_match.group(1))},
                label=f"{points_match.group(1)} point move",
            )
        )
    for match in _LEVEL_ABOVE_RE.finditer(text):
        level = float(match.group(1).replace(",", ""))
        conditions.append(
            WatchCondition(
                kind="price_level",
                symbol=sym,
                params={"above": level},
                label=f"above {level:g}",
            )
        )
    for match in _LEVEL_BELOW_RE.finditer(text):
        level = float(match.group(1).replace(",", ""))
        conditions.append(
            WatchCondition(
                kind="price_level",
                symbol=sym,
                params={"below": level},
                label=f"below {level:g}",
            )
        )
    return conditions


def fast_path_extract_intent_delta(
    user_message: str,
    *,
    source_message_id: str = "",
) -> IntentDelta | None:
    """High-confidence regex extraction — skips LLM when unambiguous."""
    text = str(user_message or "").strip()
    if not text:
        return None

    symbols = _extract_symbols(text)
    explicit: list[str] = []
    needs: list[str] = []
    engagement: EngagementMode | None = None
    instruments: list[InstrumentClass] = []

    trade_hit = bool(_TRADE_RE.search(text))
    observe_hit = bool(_OBSERVE_RE.search(text)) and not trade_hit

    if observe_hit:
        engagement = "observe"
        explicit.append("engagement")
        if _INDEX_ONLY_RE.search(text) or (symbols and symbols[0] in {"NIFTY", "NIFTY50", "BANKNIFTY"}):
            instruments = ["index"]
        else:
            instruments = ["equity"]
        explicit.append("instruments")

    if trade_hit or _OPTIONS_RE.search(text):
        engagement = "trade"
        if "engagement" not in explicit:
            explicit.append("engagement")
        if _OPTIONS_RE.search(text):
            instruments = ["options"]
            explicit.append("instruments")
        elif _FUTURES_RE.search(text):
            instruments = ["futures"]
            explicit.append("instruments")
        elif symbols and not instruments:
            sym0 = str(symbols[0]).upper()
            if sym0 in {"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"}:
                needs.append("instruments")
            else:
                instruments = ["equity"]
                explicit.append("instruments")

    if symbols:
        explicit.append("symbols")

    watch_conditions = _build_watch_conditions(text, symbols)
    schedules: dict[str, int] | None = None
    for cond in watch_conditions:
        if cond.kind == "schedule":
            every_min = int(cond.params.get("every_min") or 0)
            if every_min > 0:
                schedules = {"watch_ms": every_min * 60_000}
                explicit.append("schedules")
                break

    if watch_conditions:
        explicit.append("watch_conditions")

    budget, max_loss = _extract_amounts(text)
    constraints: dict[str, Any] | None = None
    if budget is not None or max_loss is not None:
        constraints = {}
        if budget is not None:
            constraints["budget_inr"] = budget
        if max_loss is not None:
            constraints["max_daily_loss_inr"] = max_loss
        explicit.append("constraints")

    if symbols and not instruments and engagement != "observe":
        if any(sym in {"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY"} for sym in symbols):
            needs.append("instruments")

    if not explicit:
        return None

    return IntentDelta(
        engagement=engagement,
        instruments=instruments or None,
        symbols=symbols or None,
        schedules=schedules,
        watch_conditions=watch_conditions or None,
        constraints=constraints,
        explicit_fields=sorted(set(explicit)),
        needs_clarification=needs,
        source_message_id=source_message_id,
    )


def _build_llm_prompt(
    *,
    user_message: str,
    prior: AgentIntent | None,
    validator_errors: list[str] | None = None,
) -> str:
    prior_blob = json.dumps(prior.to_dict() if prior else {}, indent=2)
    retry = ""
    if validator_errors:
        retry = "Previous output failed validation:\n- " + "\n- ".join(validator_errors) + "\nFix and return valid JSON.\n"
    return f"""Extract autonomous agent intent from the user's latest message.

Prior intent (inherit unspecified fields — latest message overrides only what it explicitly states):
{prior_blob}

Latest user message:
{user_message}

Return ONLY valid JSON matching this schema:
{intent_json_schema_block()}

Rules:
- List every field the latest message explicitly addresses in explicit_fields.
- engagement=observe when user wants watch/monitor/report only without trading.
- engagement=trade when user wants paper/live trading, options, futures, or execution.
- instruments: equity | options | futures | index — pick what the user wants to strategize on.
- symbols: pass tickers exactly as stated (NIFTY not NIFTYBEES).
- watch_conditions: encode cadence (schedule/every_min), price moves (points or pct), levels (above/below), VIX, composite groups.
- Put ambiguous index instrument type in needs_clarification (do not guess options vs index watch).
- Do not invent budget or loss caps unless the user stated them.
{retry}"""


def _default_llm_caller(prompt: str, max_tokens: int) -> str:
    from trade_integrations.dataflows.index_research.news_distillation import call_minimax_json_text

    return call_minimax_json_text(prompt, max_tokens=max_tokens)


@dataclass
class IntentExtractResult:
    intent: AgentIntent
    delta: IntentDelta
    source: str
    validation_errors: list[str]


def extract_agent_intent(
    user_message: str,
    *,
    prior: AgentIntent | None = None,
    source_message_id: str = "",
    use_llm: bool | None = None,
    prefer_fast_path: bool = False,
    llm_caller: _LLMCaller | None = None,
) -> IntentExtractResult:
    """Extract and merge intent for one user message."""
    prior_intent = prior or default_agent_intent()
    llm_on = intent_extractor_llm_enabled() if use_llm is None else bool(use_llm)

    delta: IntentDelta | None = None
    source = "none"

    if prefer_fast_path:
        delta = fast_path_extract_intent_delta(user_message, source_message_id=source_message_id)
        if delta is not None:
            errors = validate_intent_delta(delta)
            if errors:
                delta = None
            else:
                source = "fast_path"

    if delta is None and llm_on:
        caller = llm_caller or _default_llm_caller
        errors: list[str] = []
        for _ in range(3):
            prompt = _build_llm_prompt(
                user_message=user_message,
                prior=prior_intent,
                validator_errors=errors or None,
            )
            try:
                raw = caller(prompt, 1400)
            except Exception:
                logger.debug("intent LLM call failed", exc_info=True)
                break
            payload = _parse_json_object(raw)
            if not payload:
                errors = ["response was not valid JSON object"]
                continue
            candidate = _delta_from_payload(payload, source_message_id=source_message_id)
            errors = validate_intent_delta(candidate)
            if errors:
                continue
            delta = candidate
            source = "llm"
            break

    if delta is None:
        delta = fast_path_extract_intent_delta(user_message, source_message_id=source_message_id)
        if delta is not None:
            errors = validate_intent_delta(delta)
            if errors:
                delta = None
            else:
                source = "fast_path"

    if delta is None:
        delta = IntentDelta(source_message_id=source_message_id)

    merged = merge_agent_intent(prior_intent, delta)
    return IntentExtractResult(
        intent=merged,
        delta=delta,
        source=source,
        validation_errors=validate_intent_delta(delta),
    )


def refresh_intent_for_message(
    user_message: str,
    *,
    prior: AgentIntent | None = None,
    source_message_id: str = "",
    use_llm: bool | None = None,
    llm_caller: _LLMCaller | None = None,
) -> AgentIntent:
    """Convenience wrapper returning merged intent only."""
    return extract_agent_intent(
        user_message,
        prior=prior,
        source_message_id=source_message_id,
        use_llm=use_llm,
        llm_caller=llm_caller,
    ).intent
