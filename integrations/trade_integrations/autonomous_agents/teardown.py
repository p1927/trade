"""Teardown helpers for draft and active autonomous agent deletion."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from trade_integrations.autonomous_agents.store import (
    _agents_root,
    clear_orchestrator_meta,
    delete_agent,
    delete_proposal,
    get_agent,
)

logger = logging.getLogger(__name__)


class OpenPositionsConflictError(Exception):
    """Raised when active delete is blocked by open agent-scoped positions."""

    def __init__(
        self,
        *,
        agent_id: str,
        count: int,
        openalgo_count: int = 0,
        alpaca_count: int = 0,
    ) -> None:
        self.agent_id = agent_id
        self.count = count
        self.openalgo_count = openalgo_count
        self.alpaca_count = alpaca_count
        super().__init__(f"agent has {count} open position(s)")


class OpenPositionsLookupError(Exception):
    """Raised when positionbook lookup fails — delete must not proceed silently."""

    def __init__(self, *, agent_id: str, reason: str) -> None:
        self.agent_id = agent_id
        self.reason = reason
        super().__init__(reason)


class FlattenIncompleteError(Exception):
    """Raised when flatten was requested but scoped positions remain."""

    def __init__(
        self,
        *,
        agent_id: str,
        openalgo_remaining: int,
        alpaca_remaining: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.openalgo_remaining = openalgo_remaining
        self.alpaca_remaining = alpaca_remaining
        self.detail = detail or {}
        total = openalgo_remaining + alpaca_remaining
        super().__init__(f"flatten incomplete — {total} position(s) remain")


@dataclass
class AgentPositionSnapshot:
    openalgo_rows: list[dict[str, Any]]
    alpaca_symbols: list[str]
    lookup_ok: bool
    lookup_error: str | None = None

    @property
    def total_open(self) -> int:
        return len(self.openalgo_rows) + len(self.alpaca_symbols)


def _proposals_dir() -> Path:
    return _agents_root() / "proposals"


def delete_proposals_for_agent(
    *,
    vibe_session_id: str | None = None,
    draft_agent_id: str | None = None,
    proposal_id: str | None = None,
) -> int:
    """Delete proposal JSON files tied to a draft or active agent."""
    removed = 0
    explicit_id = str(proposal_id or "").strip()
    if explicit_id:
        if delete_proposal(explicit_id):
            removed += 1

    root = _proposals_dir()
    if not root.is_dir():
        return removed

    orch = str(vibe_session_id or "").strip()
    draft_id = str(draft_agent_id or "").strip()
    for path in root.glob("aap_*.json"):
        if explicit_id and path.stem == explicit_id:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        match = False
        if orch and str(data.get("orchestrator_session_id") or "") == orch:
            match = True
        if draft_id and str(data.get("draft_agent_id") or "") == draft_id:
            match = True
        if draft_id and str(data.get("committed_agent_id") or "") == draft_id:
            match = True
        if match:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _openalgo_rows_for_agent(agent_id: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent
        from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

        client = get_openalgo_client()
        return filter_positions_for_agent(client.get_position_book(), agent_id), None
    except Exception as exc:
        logger.debug("openalgo position lookup failed for %s", agent_id, exc_info=True)
        return [], str(exc)


def snapshot_agent_positions(agent: dict[str, Any]) -> AgentPositionSnapshot:
    """Return scoped OpenAlgo position rows for one agent."""
    agent_id = str(agent.get("id") or "")
    openalgo_rows, openalgo_err = _openalgo_rows_for_agent(agent_id)

    if openalgo_err:
        return AgentPositionSnapshot(
            openalgo_rows=[],
            alpaca_symbols=[],
            lookup_ok=False,
            lookup_error=openalgo_err,
        )
    return AgentPositionSnapshot(
        openalgo_rows=openalgo_rows,
        alpaca_symbols=[],
        lookup_ok=True,
    )


def flatten_agent_positions(agent: dict[str, Any]) -> dict[str, Any]:
    """Flatten OpenAlgo legs scoped to one agent."""
    agent_id = str(agent.get("id") or "")
    symbols = list(agent.get("symbols") or ["NIFTY"])
    underlying = str(symbols[0] if symbols else "NIFTY").upper()
    snapshot = snapshot_agent_positions(agent)
    if not snapshot.lookup_ok:
        return {
            "status": "error",
            "error": snapshot.lookup_error or "position lookup failed",
            "remaining_positions": None,
            "openalgo_remaining": None,
            "alpaca_remaining": None,
        }

    result: dict[str, Any] = {
        "openalgo": {"status": "no_positions", "remaining_positions": 0},
        "alpaca": [],
        "remaining_positions": 0,
        "openalgo_remaining": 0,
        "alpaca_remaining": 0,
    }

    if snapshot.openalgo_rows:
        try:
            from nautilus_openalgo_bridge.config import is_bridge_market_open
            from nautilus_openalgo_bridge.execute import execute_intent
            from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
            from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

            client = get_openalgo_client()
            exit_result = execute_intent(
                ExecutionIntent(
                    action=IntentAction.EXIT,
                    agent_id=agent_id,
                    rationale=f"Delete autonomous agent {agent_id}",
                    underlying=underlying,
                    strategy="autonomous_cleanup",
                ),
                client=client,
                skip_preflight=not is_bridge_market_open(),
            )
            after = snapshot_agent_positions(agent)
            if not after.lookup_ok:
                result["openalgo"] = {
                    "status": "error",
                    "error": after.lookup_error,
                    "remaining_positions": len(snapshot.openalgo_rows),
                }
                result["openalgo_remaining"] = len(snapshot.openalgo_rows)
            else:
                remaining = len(after.openalgo_rows)
                result["openalgo"] = {
                    "status": exit_result.get("status"),
                    "remaining_positions": remaining,
                }
                result["openalgo_remaining"] = remaining
        except Exception as exc:
            result["openalgo"] = {
                "status": "error",
                "error": str(exc),
                "remaining_positions": len(snapshot.openalgo_rows),
            }
            result["openalgo_remaining"] = len(snapshot.openalgo_rows)

    for sym in snapshot.alpaca_symbols:
        row: dict[str, Any] = {"symbol": sym}
        row["status"] = "skipped"
        row["reason"] = "legacy_alpaca_path_removed"
        result["alpaca"].append(row)

    after = snapshot_agent_positions(agent)
    if after.lookup_ok:
        result["openalgo_remaining"] = len(after.openalgo_rows)
        result["alpaca_remaining"] = len(after.alpaca_symbols)
    else:
        result["status"] = "error"
        result["error"] = after.lookup_error
    result["remaining_positions"] = int(result.get("openalgo_remaining") or 0) + int(
        result.get("alpaca_remaining") or 0
    )
    result["status"] = "ok" if result["remaining_positions"] == 0 else "partial"
    return result


def _clear_agent_infra(agent_id: str) -> None:
    try:
        from trade_integrations.watch_registry.store import delete_watches_for_owner, sync_nautilus_registry_from_watches

        delete_watches_for_owner(owner_kind="autonomous_agent", owner_id=agent_id)
        sync_nautilus_registry_from_watches(restart_if_changed=True)
    except Exception:
        logger.debug("watch cleanup failed for %s", agent_id, exc_info=True)
    try:
        from nautilus_openalgo_bridge.handoff import clear_handoff

        clear_handoff(agent_id)
    except Exception:
        logger.debug("handoff cleanup failed for %s", agent_id, exc_info=True)


def _ensure_positions_allow_delete(
    agent: dict[str, Any],
    *,
    flatten_positions: bool,
) -> dict[str, Any] | None:
    snapshot = snapshot_agent_positions(agent)
    agent_id = str(agent.get("id") or "")
    if not snapshot.lookup_ok:
        if flatten_positions:
            flatten_result = flatten_agent_positions(agent)
            if str(flatten_result.get("status") or "") == "error":
                raise OpenPositionsLookupError(
                    agent_id=agent_id,
                    reason=str(flatten_result.get("error") or snapshot.lookup_error or "position lookup failed"),
                )
            after = snapshot_agent_positions(agent)
            if not after.lookup_ok:
                raise OpenPositionsLookupError(
                    agent_id=agent_id,
                    reason=after.lookup_error or snapshot.lookup_error or "position lookup failed after flatten",
                )
            if after.total_open > 0:
                raise FlattenIncompleteError(
                    agent_id=agent_id,
                    openalgo_remaining=len(after.openalgo_rows),
                    alpaca_remaining=len(after.alpaca_symbols),
                    detail=flatten_result,
                )
            return flatten_result
        raise OpenPositionsLookupError(agent_id=agent_id, reason=snapshot.lookup_error or "position lookup failed")

    if snapshot.total_open == 0:
        return None

    if not flatten_positions:
        raise OpenPositionsConflictError(
            agent_id=agent_id,
            count=snapshot.total_open,
            openalgo_count=len(snapshot.openalgo_rows),
            alpaca_count=len(snapshot.alpaca_symbols),
        )

    flatten_result = flatten_agent_positions(agent)
    if int(flatten_result.get("remaining_positions") or 0) > 0:
        raise FlattenIncompleteError(
            agent_id=agent_id,
            openalgo_remaining=int(flatten_result.get("openalgo_remaining") or 0),
            alpaca_remaining=int(flatten_result.get("alpaca_remaining") or 0),
            detail=flatten_result,
        )
    return flatten_result


def teardown_agent_resources(
    agent: dict[str, Any],
    *,
    mode: Literal["draft", "active"],
    session_service: Any | None = None,
    flatten_positions: bool = False,
) -> dict[str, Any]:
    """Remove side effects for draft or active agent deletion."""
    agent_id = str(agent.get("id") or "")
    vibe_session_id = str(agent.get("vibe_session_id") or "").strip()
    result: dict[str, Any] = {"agent_id": agent_id, "mode": mode}

    if mode == "draft":
        result["proposals_removed"] = delete_proposals_for_agent(
            vibe_session_id=vibe_session_id,
            draft_agent_id=agent_id,
        )
        if session_service and vibe_session_id:
            try:
                result["session_deleted"] = bool(session_service.delete_session(vibe_session_id))
            except Exception as exc:
                result["session_delete_error"] = str(exc)
        clear_orchestrator_meta(vibe_session_id or None)
        delete_agent(agent_id)
        result["status"] = "ok"
        return result

    flatten_result = _ensure_positions_allow_delete(agent, flatten_positions=flatten_positions)
    if flatten_result is not None:
        result["flatten"] = flatten_result

    _clear_agent_infra(agent_id)
    result["proposals_removed"] = delete_proposals_for_agent(
        vibe_session_id=vibe_session_id,
        draft_agent_id=agent_id,
        proposal_id=str(agent.get("proposal_id") or "") or None,
    )
    delete_agent(agent_id)
    result["status"] = "ok"
    return result


def delete_draft_autonomous_agent(agent_id: str, *, session_service: Any | None = None) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    if str(agent.get("status") or "") != "draft":
        raise ValueError(f"agent is not a draft: {agent_id}")
    return teardown_agent_resources(agent, mode="draft", session_service=session_service)


def delete_active_autonomous_agent(
    agent_id: str,
    *,
    flatten_positions: bool = False,
) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    status = str(agent.get("status") or "")
    if status == "draft":
        raise ValueError(f"use draft delete for draft agents: {agent_id}")
    return teardown_agent_resources(agent, mode="active", flatten_positions=flatten_positions)
