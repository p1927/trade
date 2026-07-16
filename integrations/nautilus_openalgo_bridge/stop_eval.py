"""Evaluate handoff stop rules (max loss, flatten-at-close)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config
from nautilus_openalgo_bridge.models import BridgeSignal, PositionHandoff, QuoteSnapshot, WatchAlert


def is_flatten_at_close_window(config: BridgeConfig | None = None, *, now=None) -> bool:
    """True within 10 minutes before configured market close (IST)."""
    cfg = config or get_bridge_config()
    ist = ZoneInfo("Asia/Kolkata")
    now = now or datetime.now(ist)
    parts = cfg.market_close.strip().split(":")
    if len(parts) != 2:
        return False
    close_h, close_m = int(parts[0]), int(parts[1])
    close_dt = datetime.combine(
        now.date(),
        datetime.min.time().replace(hour=close_h, minute=close_m),
        tzinfo=ist,
    )
    window_start = close_dt - timedelta(minutes=10)
    return window_start <= now <= close_dt


def evaluate_stop_rules(
    handoff: PositionHandoff,
    quotes: dict[str, QuoteSnapshot],
    *,
    unrealized_pnl_inr: float | None = None,
    config: BridgeConfig | None = None,
) -> WatchAlert | None:
    """Hard stop alerts — no LLM required."""
    rules = handoff.stop_rules
    underlying = handoff.underlying.upper()
    quote = quotes.get(underlying) or quotes.get(handoff.underlying)

    if rules.max_loss_inr is not None and unrealized_pnl_inr is not None:
        if unrealized_pnl_inr <= -abs(rules.max_loss_inr):
            return WatchAlert(
                signal=BridgeSignal.EXIT_NOW,
                rule=None,
                symbol=underlying,
                message=f"Max loss breached: P&L ₹{unrealized_pnl_inr:,.0f} (limit ₹{-rules.max_loss_inr:,.0f})",
                ltp=quote.ltp if quote else None,
            )

    if rules.spot_stop_pct is not None and quote is not None and handoff.entry_spot > 0:
        move = ((quote.ltp - handoff.entry_spot) / handoff.entry_spot) * 100.0
        limit = abs(rules.spot_stop_pct)
        if abs(move) >= limit:
            return WatchAlert(
                signal=BridgeSignal.EXIT_NOW,
                rule=None,
                symbol=underlying,
                message=f"Spot stop: {move:+.2f}% from entry (limit {limit}%)",
                ltp=quote.ltp,
                move_pct=move,
            )

    if rules.flatten_at_close and is_flatten_at_close_window(config):
        return WatchAlert(
            signal=BridgeSignal.EXIT_NOW,
            rule=None,
            symbol=underlying,
            message="Session close flatten window — exit open legs",
            ltp=quote.ltp if quote else None,
        )

    return None
