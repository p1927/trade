"""Process-wide halt + intent dedupe state for Nautilus bridge actors."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_halt_reasons: dict[str, str] = {}
_last_intent_keys: dict[str, str] = {}

_REDIS_HALT_PREFIX = "nautilus:halt:"
_REDIS_DEDUPE_PREFIX = "nautilus:intent_dedupe:"


def _redis_client():
    try:
        from nautilus_openalgo_bridge.config import get_bridge_config

        url = (get_bridge_config().redis_url or "").strip()
        if not url:
            return None
        import redis

        return redis.from_url(url, decode_responses=True)
    except Exception:
        logger.debug("redis client unavailable for risk_state", exc_info=True)
        return None


def _halt_key(agent_id: str | None) -> str:
    aid = (agent_id or "").strip()
    return aid or "__global__"


def set_trading_halt(agent_id: str | None, reason: str) -> None:
    key = _halt_key(agent_id)
    _halt_reasons[key] = reason
    client = _redis_client()
    if client is not None:
        try:
            client.set(f"{_REDIS_HALT_PREFIX}{key}", reason)
        except Exception:
            logger.debug("redis halt set failed", exc_info=True)
    if agent_id:
        try:
            from trade_integrations.autonomous_agents.store import get_agent, save_agent

            agent = get_agent(str(agent_id).strip())
            if agent:
                agent["trading_halted"] = True
                agent["trading_halt_reason"] = reason
                save_agent(agent)
        except Exception:
            logger.debug("hub halt persist skipped", exc_info=True)


def is_trading_halted(agent_id: str | None = None) -> bool:
    aid = (agent_id or "").strip()
    client = _redis_client()
    if client is not None:
        try:
            if aid and client.get(f"{_REDIS_HALT_PREFIX}{aid}"):
                return True
            if client.get(f"{_REDIS_HALT_PREFIX}__global__"):
                return True
        except Exception:
            logger.debug("redis halt read failed", exc_info=True)
    if aid and aid in _halt_reasons:
        return True
    return "__global__" in _halt_reasons


def halt_reason(agent_id: str | None = None) -> str | None:
    aid = (agent_id or "").strip()
    client = _redis_client()
    if client is not None:
        try:
            if aid:
                val = client.get(f"{_REDIS_HALT_PREFIX}{aid}")
                if val:
                    return str(val)
            val = client.get(f"{_REDIS_HALT_PREFIX}__global__")
            if val:
                return str(val)
        except Exception:
            logger.debug("redis halt reason read failed", exc_info=True)
    if aid and aid in _halt_reasons:
        return _halt_reasons[aid]
    return _halt_reasons.get("__global__")


def clear_trading_halt(agent_id: str | None = None) -> None:
    aid = (agent_id or "").strip()
    if aid:
        _halt_reasons.pop(aid, None)
    else:
        _halt_reasons.clear()
    client = _redis_client()
    if client is not None:
        try:
            if aid:
                client.delete(f"{_REDIS_HALT_PREFIX}{aid}")
            else:
                for key in client.scan_iter(f"{_REDIS_HALT_PREFIX}*"):
                    client.delete(key)
        except Exception:
            logger.debug("redis halt clear failed", exc_info=True)
    if aid:
        try:
            from trade_integrations.autonomous_agents.store import get_agent, save_agent

            agent = get_agent(aid)
            if agent:
                agent.pop("trading_halted", None)
                agent.pop("trading_halt_reason", None)
                save_agent(agent)
        except Exception:
            logger.debug("hub halt clear skipped", exc_info=True)


def clear_intent_dedupe(agent_id: str | None = None) -> None:
    """Clear in-memory and Redis intent dedupe keys (tests and agent teardown)."""
    aid = (agent_id or "").strip()
    if aid:
        _last_intent_keys.pop(aid, None)
    else:
        _last_intent_keys.clear()
    client = _redis_client()
    if client is None:
        return
    try:
        if aid:
            client.delete(f"{_REDIS_DEDUPE_PREFIX}{aid}")
        else:
            for key in client.scan_iter(f"{_REDIS_DEDUPE_PREFIX}*"):
                client.delete(key)
    except Exception:
        logger.debug("redis dedupe clear failed", exc_info=True)


def should_skip_intent(agent_id: str, dedupe_key: str) -> bool:
    """Return True when the same intent key was already processed for this agent."""
    if not dedupe_key:
        return False
    client = _redis_client()
    if client is not None:
        try:
            rkey = f"{_REDIS_DEDUPE_PREFIX}{agent_id}"
            prev = client.get(rkey)
            if prev == dedupe_key:
                return True
            client.set(rkey, dedupe_key, ex=86400)
            return False
        except Exception:
            logger.debug("redis dedupe check failed", exc_info=True)
    prev = _last_intent_keys.get(agent_id)
    if prev == dedupe_key:
        return True
    _last_intent_keys[agent_id] = dedupe_key
    return False
