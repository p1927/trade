"""Code enforcement of user-configured mandate rules for autonomous agents."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.mandate_config import MandateConfig, mandate_config_from_session
from trade_integrations.autonomous_agents.market_hours import is_trading_session_open
from trade_integrations.monitor.execution_ledger import list_open_entries


class MandateViolation(Exception):
    """Raised when an action would violate the user's mandate."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _mandate_from_session(session: dict[str, Any]) -> MandateConfig:
    return mandate_config_from_session(session)


def _session_market(session: dict[str, Any]) -> str:
    mc = session.get("mandate_config") if isinstance(session.get("mandate_config"), dict) else {}
    market = str((mc or {}).get("market") or session.get("execution_market") or "IN").upper()
    return market if market in {"IN", "US"} else "IN"


def widget_instrument_class(widget: dict[str, Any]) -> str:
    """Return ``options`` or ``equity`` from widget metadata."""
    wid = str(widget.get("widget_id") or "")
    if wid.startswith("ts_"):
        return "equity"
    if wid.startswith("tp_") or wid.startswith("ti_"):
        return "options"
    asset = str((widget.get("recommended") or {}).get("asset_class") or "").lower()
    if asset in {"equity", "stock"}:
        return "equity"
    if asset in {"options", "option"}:
        return "options"
    steps = widget.get("implementation_steps") or []
    for step in steps:
        orders = (step.get("payload") or {}).get("orders") or []
        for order in orders:
            if not isinstance(order, dict):
                continue
            exch = str(order.get("exchange") or "").upper()
            if exch == "NFO":
                return "options"
            if exch in {"NSE", "BSE"}:
                return "equity"
    return "options"


def assert_widget_allowed(
    widget: dict[str, Any],
    mandate: MandateConfig,
) -> None:
    """Ensure widget instrument type is permitted by mandate."""
    inst = widget_instrument_class(widget)
    allowed = {str(x).strip().lower() for x in (mandate.allowed_instruments or []) if str(x).strip()}
    if not allowed:
        return
    if inst not in allowed:
        raise MandateViolation(
            "instrument_not_allowed",
            f"Mandate allows {sorted(allowed)} but widget is {inst}",
        )


def assert_can_execute(
    session: dict[str, Any],
    *,
    mandate: MandateConfig | None = None,
    confidence: int | None = None,
    ticker: str | None = None,
    research_kind: str | None = None,
    require_active_session: bool = True,
) -> None:
    """Guard ENTER/ADJUST basket execution."""
    if require_active_session and not session.get("enabled"):
        raise MandateViolation("session_inactive", "Trading session is not active")
    if session.get("halted"):
        reason = session.get("halt_reason") or "halted"
        raise MandateViolation("session_halted", f"Session halted: {reason}")

    mandate = mandate or _mandate_from_session(session)

    if mandate.market_hours_only and not is_trading_session_open(market=_session_market(session)):
        raise MandateViolation("outside_market_hours", "Market is closed for this agent's trading window")

    open_count = len(list_open_entries())
    if open_count >= mandate.max_open_positions:
        raise MandateViolation(
            "max_positions",
            f"Already at max open positions ({mandate.max_open_positions})",
        )

    if confidence is not None and confidence < mandate.confidence_threshold:
        raise MandateViolation(
            "confidence_below_threshold",
            f"Confidence {confidence} below threshold {mandate.confidence_threshold}",
        )

    symbol = (ticker or session.get("primary_ticker") or "").strip().upper()
    if symbol and research_kind in ("options", "stock"):
        try:
            from trade_integrations.monitor.service import MonitorService
            from trade_integrations.research.preflight import evaluate_research_preflight, preflight_blocks_enter

            report = MonitorService().evaluate_ticker(symbol, kind=research_kind)
            preflight = evaluate_research_preflight(symbol, kind=research_kind, staleness=report)
            if preflight_blocks_enter(preflight):
                reasons = ", ".join(preflight.get("blocking_reasons") or [])
                raise MandateViolation("research_preflight_failed", f"Hub/data preflight failed: {reasons}")
        except MandateViolation:
            raise
        except Exception:
            pass


def validate_decision(
    decision: str,
    session: dict[str, Any],
    *,
    mandate: MandateConfig | None = None,
) -> tuple[str, list[str]]:
    """
    Validate and optionally override agent decisions against mandate.

    Returns (decision, warnings).
    """
    mandate = mandate or _mandate_from_session(session)
    decision_u = decision.strip().upper()
    warnings: list[str] = []
    open_positions = len(list_open_entries())
    market = _session_market(session)
    market_open = is_trading_session_open(market=market)

    if decision_u == "HOLD" and open_positions > 0:
        if mandate.needs_session_close_flatten() and mandate.market_hours_only:
            if not market_open:
                warnings.append("mandate_override: market closed with open positions — flatten required")
                return "EXIT", warnings
        if mandate.flatten_policy == "session_close" and not market_open:
            warnings.append("mandate_override: session_close policy with open positions after close")
            return "EXIT", warnings

    if decision_u in {"ENTER", "REVISE", "ADJUST"} and mandate.revision_policy == "user_guidance_only":
        guidance = list(session.get("user_guidance") or [])
        if not guidance:
            warnings.append("revision_policy blocks entry until user guidance is set")
            return "SKIP", warnings

    return decision_u, warnings


def product_for_session(session: dict[str, Any], *, default_product: str = "MIS") -> str:
    mandate = _mandate_from_session(session)
    resolved = mandate.resolve_product()
    if resolved:
        return resolved
    return default_product
