"""Strategy lifecycle for autonomous paper trading agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.context.hub import load_options_research_json
from trade_integrations.monitor.execution_ledger import list_open_entries

# Lifecycle states (one active position per session by default).
STATE_IDLE = "IDLE"
STATE_RESEARCHING = "RESEARCHING"
STATE_ENTERING = "ENTERING"
STATE_MONITORING = "MONITORING"
STATE_FAILED = "FAILED"
STATE_EXITING = "EXITING"
STATE_HALTED = "HALTED"

_VALID_STATES = frozenset(
    {STATE_IDLE, STATE_RESEARCHING, STATE_ENTERING, STATE_MONITORING, STATE_FAILED, STATE_EXITING, STATE_HALTED}
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def default_lifecycle() -> dict[str, Any]:
    return {
        "state": STATE_IDLE,
        "active_widget_id": None,
        "active_strategy": None,
        "active_ticker": None,
        "entered_at": None,
        "last_transition_at": _now_iso(),
        "tried_strategies": [],
        "plan_b_candidates": [],
        "failure_reasons": [],
        "reentry_blocked_until": None,
    }


def load_lifecycle(session: dict[str, Any]) -> dict[str, Any]:
    raw = session.get("lifecycle")
    if not isinstance(raw, dict):
        return default_lifecycle()
    merged = default_lifecycle()
    merged.update({k: v for k, v in raw.items() if k in merged or k.startswith("active_")})
    if merged.get("state") not in _VALID_STATES:
        merged["state"] = STATE_IDLE
    return merged


def save_lifecycle(session: dict[str, Any], lifecycle: dict[str, Any]) -> dict[str, Any]:
    lifecycle["last_transition_at"] = _now_iso()
    session["lifecycle"] = lifecycle
    return session


def _transition(lifecycle: dict[str, Any], new_state: str, **extra: Any) -> dict[str, Any]:
    lifecycle = dict(lifecycle)
    lifecycle["state"] = new_state
    lifecycle.update(extra)
    lifecycle["last_transition_at"] = _now_iso()
    return lifecycle


def reentry_allowed(lifecycle: dict[str, Any]) -> tuple[bool, str]:
    """Anti-churn guard after exit/failure."""
    cfg = get_auto_paper_config()
    min_hold_minutes = cfg.min_hold_minutes
    blocked_until = lifecycle.get("reentry_blocked_until")
    if not blocked_until:
        return True, "ok"
    until = _parse_iso(str(blocked_until))
    if until is None:
        return True, "ok"
    now = datetime.now(timezone.utc)
    if now >= until:
        return True, "ok"
    remaining = int((until - now).total_seconds() / 60) + 1
    return False, f"reentry_cooldown_{remaining}m"


def build_plan_b_candidates(ticker: str, *, tried: list[str]) -> list[dict[str, Any]]:
    """Next ranked strategies not yet attempted this session."""
    doc = load_options_research_json(ticker)
    if doc is None:
        return []

    cfg = get_auto_paper_config()
    tried_set = {name.strip().lower() for name in tried if name}
    candidates: list[dict[str, Any]] = []

    for row in doc.ranked_strategies or []:
        name = str(getattr(row, "name", None) or (row.get("name") if isinstance(row, dict) else "") or "")
        if not name:
            continue
        if name.strip().lower() in tried_set:
            continue
        score = getattr(row, "score", None)
        if score is None and isinstance(row, dict):
            score = row.get("score")
        try:
            score_f = float(score or 0.0)
        except (TypeError, ValueError):
            score_f = 0.0
        if score_f < cfg.min_strategy_score:
            continue
        tier = getattr(row, "tier", None) or (row.get("tier") if isinstance(row, dict) else None)
        candidates.append({"name": name, "score": score_f, "tier": tier})
        if len(candidates) >= 3:
            break
    return candidates


def sync_lifecycle_from_positions(session: dict[str, Any]) -> dict[str, Any]:
    """Reconcile lifecycle state with open ledger entries."""
    lifecycle = load_lifecycle(session)
    open_entries = list_open_entries()
    ticker = str(session.get("primary_ticker") or (session.get("watchlist") or ["NIFTY"])[0]).upper()

    if open_entries:
        entry = open_entries[0]
        widget_id = str(entry.get("widget_id") or "")
        strategy = str(entry.get("recommended_name") or entry.get("strategy") or "")
        if lifecycle.get("state") in {STATE_IDLE, STATE_RESEARCHING, STATE_ENTERING, STATE_FAILED}:
            lifecycle = _transition(
                lifecycle,
                STATE_MONITORING,
                active_widget_id=widget_id or lifecycle.get("active_widget_id"),
                active_strategy=strategy or lifecycle.get("active_strategy"),
                active_ticker=str(entry.get("underlying") or ticker),
                entered_at=lifecycle.get("entered_at") or _now_iso(),
            )
        elif lifecycle.get("state") == STATE_MONITORING:
            lifecycle["active_widget_id"] = widget_id or lifecycle.get("active_widget_id")
            lifecycle["active_strategy"] = strategy or lifecycle.get("active_strategy")
            lifecycle["active_ticker"] = str(entry.get("underlying") or ticker)
    elif lifecycle.get("state") == STATE_MONITORING:
        lifecycle = _transition(
            lifecycle,
            STATE_IDLE,
            active_widget_id=None,
            active_strategy=None,
            active_ticker=None,
            entered_at=None,
        )

    tried = list(lifecycle.get("tried_strategies") or [])
    lifecycle["plan_b_candidates"] = build_plan_b_candidates(ticker, tried=tried)
    save_lifecycle(session, lifecycle)
    return lifecycle


def on_strategy_revised(
    session: dict[str, Any],
    *,
    new_strategy: str,
    widget_id: str | None = None,
    rationale: str = "",
) -> dict[str, Any]:
    """Lifecycle hook when agent revises strategy mid-session."""
    lifecycle = load_lifecycle(session)
    revisions = list(lifecycle.get("strategy_revisions") or [])
    revisions.append(
        {
            "at": _now_iso(),
            "from_strategy": lifecycle.get("active_strategy"),
            "to_strategy": new_strategy,
            "widget_id": widget_id,
            "rationale": rationale[:500],
        }
    )
    lifecycle["strategy_revisions"] = revisions[-20:]
    lifecycle = _transition(
        lifecycle,
        STATE_ENTERING,
        active_strategy=new_strategy,
        active_widget_id=widget_id or lifecycle.get("active_widget_id"),
        entered_at=_now_iso(),
    )
    save_lifecycle(session, lifecycle)
    return lifecycle


def on_decision(session: dict[str, Any], *, decision: str, rationale: str, ticker: str | None = None) -> dict[str, Any]:
    """Update lifecycle when agent records ENTER/EXIT/HOLD/SKIP/REVISE."""
    lifecycle = load_lifecycle(session)
    focus = (ticker or session.get("primary_ticker") or "NIFTY").upper()
    decision_u = decision.strip().upper()
    cfg = get_auto_paper_config()

    if decision_u in {"ENTER", "REVISE", "ADJUST"}:
        lifecycle = _transition(
            lifecycle,
            STATE_ENTERING,
            active_ticker=focus,
            entered_at=_now_iso(),
        )
        if decision_u in {"REVISE", "ADJUST"}:
            on_strategy_revised(session, new_strategy=lifecycle.get("active_strategy") or "revised", rationale=rationale)
            lifecycle = load_lifecycle(session)
    elif decision_u == "EXIT":
        strategy = lifecycle.get("active_strategy")
        tried = list(lifecycle.get("tried_strategies") or [])
        if strategy and strategy not in tried:
            tried.append(strategy)
        lifecycle = _transition(
            lifecycle,
            STATE_FAILED if "thesis" in rationale.lower() or "break" in rationale.lower() else STATE_IDLE,
            active_widget_id=None,
            active_strategy=None,
            active_ticker=None,
            entered_at=None,
            tried_strategies=tried,
            failure_reasons=(list(lifecycle.get("failure_reasons") or []) + [rationale])[-20:],
        )
        blocked = datetime.now(timezone.utc).timestamp() + cfg.min_hold_minutes * 60
        lifecycle["reentry_blocked_until"] = datetime.fromtimestamp(blocked, tz=timezone.utc).isoformat()
    elif decision_u in {"HOLD", "SKIP"} and lifecycle.get("state") == STATE_ENTERING:
        lifecycle = _transition(lifecycle, STATE_MONITORING if decision_u == "HOLD" else STATE_RESEARCHING)

    lifecycle["plan_b_candidates"] = build_plan_b_candidates(focus, tried=list(lifecycle.get("tried_strategies") or []))
    save_lifecycle(session, lifecycle)
    return lifecycle


def on_basket_executed(session: dict[str, Any], *, widget_id: str, strategy: str | None, underlying: str | None) -> dict[str, Any]:
    lifecycle = load_lifecycle(session)
    tried = list(lifecycle.get("tried_strategies") or [])
    name = strategy or lifecycle.get("active_strategy")
    if name and name not in tried:
        tried.append(name)
    lifecycle = _transition(
        lifecycle,
        STATE_MONITORING,
        active_widget_id=widget_id,
        active_strategy=name,
        active_ticker=(underlying or lifecycle.get("active_ticker") or "").upper() or None,
        entered_at=_now_iso(),
        tried_strategies=tried,
        reentry_blocked_until=None,
    )
    save_lifecycle(session, lifecycle)
    return lifecycle


def format_lifecycle_for_prompt(lifecycle: dict[str, Any]) -> str:
    allowed, reason = reentry_allowed(lifecycle)
    plan_b = lifecycle.get("plan_b_candidates") or []
    tried = lifecycle.get("tried_strategies") or []
    block = {
        "state": lifecycle.get("state"),
        "active_widget_id": lifecycle.get("active_widget_id"),
        "active_strategy": lifecycle.get("active_strategy"),
        "active_ticker": lifecycle.get("active_ticker"),
        "entered_at": lifecycle.get("entered_at"),
        "tried_strategies": tried,
        "reentry_allowed": allowed,
        "reentry_block_reason": None if allowed else reason,
        "plan_b_candidates": plan_b,
        "recent_failures": (lifecycle.get("failure_reasons") or [])[-3:],
    }
    import json

    lines = [
        "## Strategy lifecycle",
        f"- Current state: **{block['state']}**",
    ]
    if block["active_strategy"]:
        lines.append(f"- Open strategy: `{block['active_strategy']}` ({block['active_widget_id']})")
    if tried:
        lines.append(f"- Already tried this session: {', '.join(tried)}")
    if not allowed:
        lines.append(f"- Re-entry blocked for new basket only — **still run full research** ({reason})")
    if plan_b:
        names = ", ".join(f"{c['name']} ({c['score']:.2f})" for c in plan_b)
        lines.append(f"- Plan B (next ranked): {names}")
    lines.append("```json\n" + json.dumps(block, indent=2) + "\n```")
    return "\n".join(lines)
