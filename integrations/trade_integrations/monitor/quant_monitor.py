"""Quant repository monitor — diff quant_review snapshots and alert Vibe."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_INDEX_TICKERS = frozenset({"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "^NSEI"})


@dataclass
class QuantAlert:
    alert_type: str
    message: str
    ticker: str
    delta: dict[str, Any] = field(default_factory=dict)
    fired_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_type": self.alert_type,
            "message": self.message,
            "ticker": self.ticker,
            "delta": self.delta,
            "fired_at": self.fired_at,
        }


def _hash_payload(payload: dict[str, Any], keys: list[str]) -> str:
    subset = {k: payload.get(k) for k in keys}
    raw = json.dumps(subset, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _resolve_index_ticker(agent: dict[str, Any]) -> str | None:
    symbols = [str(s).upper() for s in (agent.get("symbols") or [])]
    for sym in symbols:
        if sym in _INDEX_TICKERS or sym.endswith("50"):
            return "NIFTY" if sym in {"NIFTY50", "^NSEI"} else sym
    return None


def diff_quant_review(prev: dict[str, Any] | None, curr: dict[str, Any]) -> list[QuantAlert]:
    """Return material alerts comparing two quant review payloads."""
    if not curr:
        return []
    ticker = str(curr.get("ticker") or "NIFTY").upper()
    alerts: list[QuantAlert] = []

    prev_profile = (prev or {}).get("active_strategy_profile")
    curr_profile = curr.get("active_strategy_profile")
    if curr_profile and curr_profile != prev_profile:
        alerts.append(
            QuantAlert(
                alert_type="profile_change",
                message=f"Strategy profile changed: {prev_profile or 'none'} → {curr_profile}",
                ticker=ticker,
                delta={"from": prev_profile, "to": curr_profile},
            )
        )

    prev_ta = ((prev or {}).get("ta_consensus") or {}).get("direction")
    curr_ta = (curr.get("ta_consensus") or {}).get("direction")
    if curr_ta and prev_ta and curr_ta != prev_ta:
        alerts.append(
            QuantAlert(
                alert_type="ta_consensus_flip",
                message=f"TA consensus flipped: {prev_ta} → {curr_ta}",
                ticker=ticker,
                delta={"from": prev_ta, "to": curr_ta},
            )
        )

    prev_dis_hash = _hash_payload(prev or {}, ["disagreements_with_forecast"])
    curr_dis = curr.get("disagreements_with_forecast") or []
    curr_dis_hash = _hash_payload(curr, ["disagreements_with_forecast"])
    if curr_dis and curr_dis_hash != prev_dis_hash:
        alerts.append(
            QuantAlert(
                alert_type="forecast_disagreement",
                message=f"New forecast disagreements ({len(curr_dis)} items)",
                ticker=ticker,
                delta={"count": len(curr_dis), "items": curr_dis[:3]},
            )
        )

    surprises = curr.get("surprises") or []
    prev_surprise_count = len((prev or {}).get("surprises") or [])
    if len(surprises) >= 3 and len(surprises) > prev_surprise_count:
        alerts.append(
            QuantAlert(
                alert_type="surprise_threshold",
                message=f"Quant surprises elevated ({len(surprises)} items)",
                ticker=ticker,
                delta={"surprises": surprises[:5]},
            )
        )

    return alerts


def run_quant_monitor_tick(agent_id: str, *, dispatch: bool = True) -> dict[str, Any]:
    """Refresh quant review, diff vs agent quant_state, optionally dispatch to Vibe."""
    from trade_integrations.autonomous_agents.store import get_agent, save_agent
    from trade_integrations.bridge.quant_review import run_quant_review
    from trade_integrations.context.hub import save_quant_review_history

    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return {"skipped": True, "reason": "agent_not_running"}

    ticker = _resolve_index_ticker(agent)
    if not ticker:
        return {"skipped": True, "reason": "not_index_agent"}

    quant_state = dict(agent.get("quant_state") or {})
    prev_snapshot = dict(quant_state.get("last_review") or {})

    review = run_quant_review(ticker, save=True)
    save_quant_review_history(ticker, review)

    alerts = diff_quant_review(prev_snapshot, review)
    dispatches: list[dict[str, Any]] = []

    if dispatch and alerts:
        from nautilus_openalgo_bridge.vibe_trigger import dispatch_quant_alert_sync

        for alert in alerts:
            result = dispatch_quant_alert_sync(
                agent_id,
                alert_type=alert.alert_type,
                message=alert.message,
                delta=alert.delta,
            )
            dispatches.append(result)
            if result.get("status") == "dispatched":
                break

    quant_state["last_review"] = review
    quant_state["last_profile"] = review.get("active_strategy_profile")
    quant_state["last_disagreement_hash"] = _hash_payload(review, ["disagreements_with_forecast"])
    quant_state["last_quant_alert_at"] = alerts[0].fired_at if alerts else quant_state.get("last_quant_alert_at")
    quant_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    agent["quant_state"] = quant_state
    save_agent(agent)

    return {
        "status": "ok",
        "ticker": ticker,
        "alerts": [a.to_dict() for a in alerts],
        "dispatches": dispatches,
    }


def get_quant_monitor_status(agent_id: str) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id) or {}
    quant_state = dict(agent.get("quant_state") or {})
    return {
        "agent_id": agent_id,
        "quant_state": quant_state,
        "last_quant_alert_at": agent.get("last_quant_alert_at"),
    }
