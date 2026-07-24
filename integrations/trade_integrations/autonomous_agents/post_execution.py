"""Post-order re-strategization turns after bridge execution reconciles."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.store import get_agent, save_agent

logger = logging.getLogger(__name__)

POST_EXECUTION_COOLDOWN_SEC = 300


def material_state_change(
    pre: dict[str, Any],
    post: dict[str, Any],
    *,
    order_state: dict[str, Any] | None = None,
) -> bool:
    """True when position or order state changed materially after an intent."""
    if int(pre.get("open_positions") or 0) != int(post.get("open_positions") or 0):
        return True
    if int(pre.get("legs") or 0) != int(post.get("legs") or 0):
        return True
    if post.get("handoff_book_mismatch"):
        return True
    if order_state:
        if order_state.get("rejected") or order_state.get("partial"):
            return True
        pending = int(order_state.get("pending") or 0)
        filled = int(order_state.get("filled") or 0)
        if filled > 0 or pending > 0:
            return True
    return False


def _post_execution_cooldown_active(agent: dict[str, Any]) -> bool:
    last_at = str(agent.get("last_post_execution_at") or "")
    if not last_at:
        return False
    try:
        dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return False
    return age < POST_EXECUTION_COOLDOWN_SEC


def should_dispatch_post_execution(
    agent: dict[str, Any],
    *,
    pre: dict[str, Any],
    post: dict[str, Any],
    order_state: dict[str, Any] | None = None,
    execution_status: str | None = None,
) -> tuple[bool, str]:
    if str(agent.get("status")) != "running":
        return False, "agent_not_running"
    if agent.get("streaming"):
        return False, "turn_in_flight"
    if execution_status not in {None, "executed", "skipped"}:
        return False, "execution_not_successful"
    try:
        from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

        if not is_plan_approved(agent):
            return False, "plan_not_approved"
    except ImportError:
        pass
    if _post_execution_cooldown_active(agent):
        return False, "post_execution_cooldown"
    if not material_state_change(pre, post, order_state=order_state):
        return False, "no_material_change"
    return True, "ok"


def build_post_execution_prompt(
    *,
    agent: dict[str, Any],
    intent_action: str,
    reconcile_payload: dict[str, Any],
    order_state: dict[str, Any] | None = None,
) -> str:
    from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt

    base = build_full_reasoning_prompt(agent=agent, turn_kind="post_execution")
    context = {
        "intent_action": intent_action,
        "reconcile": reconcile_payload,
        "order_state": order_state or {},
    }
    import json

    return (
        f"{base}\n"
        "## Post-execution review (mandatory)\n"
        "Orders just executed via OpenAlgo bridge. Confirm fills, cite P&L/charges, update "
        "watchers if levels changed, then decide HOLD | REVISE | EXIT.\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n"
    )


async def dispatch_post_execution_turn(
    agent_id: str,
    *,
    intent_action: str,
    reconcile_payload: dict[str, Any],
    pre_snapshot: dict[str, Any] | None = None,
    order_state: dict[str, Any] | None = None,
    execution_status: str | None = None,
) -> dict[str, Any]:
    def _obs_done(dispatched: bool, reason: str = "") -> dict[str, Any]:
        try:
            from trade_integrations.observability.hooks import emit_full_reasoning_dispatch

            emit_full_reasoning_dispatch(
                agent_id=agent_id,
                turn_kind="post_execution",
                dispatched=dispatched,
                reason=reason,
            )
        except ImportError:
            pass
        return {"status": "error" if not dispatched and reason in {"agent_not_found", "no_session_service"} else "skipped", "reason": reason}

    agent = get_agent(agent_id)
    if not agent:
        return _obs_done(False, "agent_not_found")

    pre = dict(pre_snapshot or {})
    post = dict(reconcile_payload or {})
    ok, reason = should_dispatch_post_execution(
        agent,
        pre=pre,
        post=post,
        order_state=order_state,
        execution_status=execution_status,
    )
    if not ok:
        return _obs_done(False, reason)

    from trade_integrations.autonomous_agents.watch import _session_service

    session_id = str(agent.get("vibe_session_id") or "").strip()
    svc = _session_service()
    if not svc or not session_id:
        return _obs_done(False, "no_session_service")

    prompt = build_post_execution_prompt(
        agent=agent,
        intent_action=intent_action,
        reconcile_payload=reconcile_payload,
        order_state=order_state,
    )
    now = datetime.now(timezone.utc).isoformat()
    agent["streaming"] = True
    agent["last_post_execution_at"] = now
    agent["last_full_reasoning_at"] = now
    save_agent(agent)

    try:
        await svc.send_message(session_id, prompt)
        try:
            from trade_integrations.observability.hooks import emit_full_reasoning_dispatch

            emit_full_reasoning_dispatch(
                agent_id=agent_id,
                turn_kind="post_execution",
                dispatched=True,
            )
        except ImportError:
            pass
        return {"status": "dispatched", "session_id": session_id}
    except Exception as exc:
        logger.warning("post_execution dispatch failed for %s: %s", agent_id, exc)
        latest = get_agent(agent_id) or agent
        latest["streaming"] = False
        save_agent(latest)
        try:
            from trade_integrations.observability.hooks import emit_full_reasoning_dispatch

            emit_full_reasoning_dispatch(
                agent_id=agent_id,
                turn_kind="post_execution",
                dispatched=False,
                reason=str(exc)[:200],
            )
        except ImportError:
            pass
        return {"status": "error", "error": str(exc)}


def schedule_post_execution_turn(
    agent_id: str,
    *,
    intent_action: str,
    reconcile_payload: dict[str, Any],
    pre_snapshot: dict[str, Any] | None = None,
    order_state: dict[str, Any] | None = None,
    execution_status: str | None = None,
) -> None:
    """Fire post-execution turn without blocking the reconcile caller."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(
            dispatch_post_execution_turn(
                agent_id,
                intent_action=intent_action,
                reconcile_payload=reconcile_payload,
                pre_snapshot=pre_snapshot,
                order_state=order_state,
                execution_status=execution_status,
            )
        )
        return

    loop.create_task(
        dispatch_post_execution_turn(
            agent_id,
            intent_action=intent_action,
            reconcile_payload=reconcile_payload,
            pre_snapshot=pre_snapshot,
            order_state=order_state,
            execution_status=execution_status,
        )
    )
