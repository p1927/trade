"""Verify agent mandate against OpenAlgo MarketContext authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from trade_integrations.openalgo.market_context import MarketContext


@dataclass(frozen=True)
class ContextVerification:
    ok: bool
    reason: str
    action_taken: str | None  # none | analyzer_enabled | blocked


def _agent_mode(agent: dict[str, Any]) -> str:
    mode = str((agent.get("constraints") or {}).get("mode") or "paper").strip().lower()
    return mode if mode in {"paper", "live"} else "paper"


def verify_agent_execution_context(
    *,
    agent: dict[str, Any],
    market_context: MarketContext,
    env_paper_lock: bool,
    allow_analyzer_sync: bool = False,
) -> ContextVerification:
    """Compare agent mandate intent to authoritative OpenAlgo market context."""
    mode = _agent_mode(agent)
    analyze = bool(market_context.analyze_mode)

    if env_paper_lock and mode == "live" and not analyze:
        return ContextVerification(
            ok=False,
            reason="env_paper_lock_blocks_live_execution",
            action_taken="blocked",
        )

    if mode == "paper" and not analyze:
        if allow_analyzer_sync and env_paper_lock:
            return ContextVerification(
                ok=True,
                reason="paper_mandate_sync_analyzer_under_env_lock",
                action_taken="analyzer_enabled",
            )
        return ContextVerification(
            ok=False,
            reason="paper_mandate_requires_analyze_mode",
            action_taken="blocked",
        )

    if mode == "live" and analyze:
        return ContextVerification(
            ok=True,
            reason="live_intent_with_analyze_on_paper_fills",
            action_taken="none",
        )

    if env_paper_lock and not analyze:
        return ContextVerification(
            ok=False,
            reason="env_paper_lock_requires_analyze_mode",
            action_taken="blocked",
        )

    return ContextVerification(ok=True, reason="context_ok", action_taken="none")


def apply_context_verification(
    verification: ContextVerification,
    *,
    sync_analyzer: Callable[[], bool],
) -> ContextVerification:
    """Run optional analyzer sync when verification requests it."""
    if verification.action_taken != "analyzer_enabled":
        return verification
    if sync_analyzer():
        return ContextVerification(
            ok=True,
            reason=verification.reason,
            action_taken="none",
        )
    return ContextVerification(
        ok=False,
        reason="analyzer_sync_failed",
        action_taken="blocked",
    )


def ensure_paper_execution_ready(
    client: Any,
    *,
    agent: dict[str, Any] | None = None,
    env_paper_lock: bool = True,
) -> MarketContext:
    """Verify market context and sync analyzer when env lock allows."""
    market_context = client.get_market_context()
    verification = verify_agent_execution_context(
        agent=agent or {"constraints": {"mode": "paper"}},
        market_context=market_context,
        env_paper_lock=env_paper_lock,
        allow_analyzer_sync=env_paper_lock,
    )
    verification = apply_context_verification(
        verification,
        sync_analyzer=client.ensure_analyzer_mode,
    )
    if not verification.ok:
        raise RuntimeError(verification.reason)
    if not market_context.analyze_mode:
        return client.get_market_context()
    return market_context
