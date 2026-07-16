"""Process-wide halt + intent dedupe state for Nautilus bridge actors."""

from __future__ import annotations

_halt_reasons: dict[str, str] = {}
_last_intent_keys: dict[str, str] = {}


def set_trading_halt(agent_id: str | None, reason: str) -> None:
    key = (agent_id or "").strip() or "__global__"
    _halt_reasons[key] = reason


def is_trading_halted(agent_id: str | None = None) -> bool:
    aid = (agent_id or "").strip()
    if aid and aid in _halt_reasons:
        return True
    return "__global__" in _halt_reasons


def halt_reason(agent_id: str | None = None) -> str | None:
    aid = (agent_id or "").strip()
    if aid and aid in _halt_reasons:
        return _halt_reasons[aid]
    return _halt_reasons.get("__global__")


def clear_trading_halt(agent_id: str | None = None) -> None:
    aid = (agent_id or "").strip()
    if aid:
        _halt_reasons.pop(aid, None)
    else:
        _halt_reasons.clear()


def should_skip_intent(agent_id: str, dedupe_key: str) -> bool:
    """Return True when the same intent key was already processed for this agent."""
    if not dedupe_key:
        return False
    prev = _last_intent_keys.get(agent_id)
    if prev == dedupe_key:
        return True
    _last_intent_keys[agent_id] = dedupe_key
    return False
