"""RiskActor — mandate max loss / position gates (IN OpenAlgo + US Alpaca)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig

from nautilus_openalgo_bridge.config import get_bridge_config
from nautilus_openalgo_bridge.market_hours import is_market_open_for_market
from nautilus_openalgo_bridge.models import BridgeSignal
from nautilus_openalgo_bridge.reconcile import open_positions_from_book, total_unrealized_pnl
from nautilus_openalgo_bridge.risk_state import set_trading_halt, should_skip_intent

SIGNAL_HALT = BridgeSignal.HALT_TRADING.value


class RiskActorConfig(ActorConfig, frozen=True):
    agent_id: str | None = None
    market: str = "IN"
    max_daily_loss_inr: float = 2_000.0
    max_open_positions: int = 3
    poll_interval_sec: int = 60


class RiskActor(Actor):
    """Poll positions/funds; publish HALT_TRADING on mandate breach."""

    def __init__(self, config: RiskActorConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._agent_id = (config.agent_id or "").strip() or None
        self._market = str(config.market or "IN").upper()
        self._max_loss = float(config.max_daily_loss_inr)
        self._max_positions = int(config.max_open_positions)
        self._bridge = get_bridge_config()
        self._halted = False

    def on_start(self) -> None:
        start = datetime.utcnow() + timedelta(seconds=15)
        self.clock.set_timer(
            "risk_poll",
            timedelta(seconds=max(30, self._cfg.poll_interval_sec)),
            start,
            stop_time=None,
            callback=self._on_risk_poll,
        )
        self.subscribe_signal(SIGNAL_HALT)
        self.log.info(
            f"RiskActor started agent={self._agent_id or 'global'} "
            f"market={self._market} max_loss={self._max_loss:,.0f}",
        )

    def _on_risk_poll(self, _event) -> None:
        if self._halted:
            return
        if not is_market_open_for_market(self._market):
            return
        try:
            if self._market == "US":
                self._poll_us_risk()
            else:
                self._poll_in_risk()
        except Exception as exc:
            self.log.warning(f"risk poll failed: {exc}")

    def _poll_in_risk(self) -> None:
        from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

        client = get_openalgo_client()
        rows = open_positions_from_book(client.get_position_book())
        pnl = total_unrealized_pnl(rows)
        open_count = len(rows)

        if open_count > self._max_positions:
            self._publish_halt(f"max_open_positions breached ({open_count}>{self._max_positions})")
            return

        if pnl is not None and pnl <= -abs(self._max_loss):
            self._publish_halt(f"max_daily_loss breached P&L ₹{pnl:,.0f}")
            return

        funds = client.get_funds()
        available = _funds_available(funds)
        if available is not None and available <= 0:
            self._publish_halt(f"insufficient funds available ₹{available:,.0f}")

    def _poll_us_risk(self) -> None:
        from trade_integrations.dataflows.alpaca import list_alpaca_positions

        rows = list_alpaca_positions()
        open_count = len([r for r in rows if float(r.get("qty") or 0) != 0])
        if open_count > self._max_positions:
            self._publish_halt(f"max_open_positions breached ({open_count}>{self._max_positions})")
            return

        total_pnl = 0.0
        found = False
        for row in rows:
            raw = row.get("unrealized_pl")
            if raw is None:
                continue
            try:
                total_pnl += float(raw)
                found = True
            except (TypeError, ValueError):
                continue
        if found and total_pnl <= -abs(self._max_loss):
            self._publish_halt(f"max_daily_loss breached P&L ${total_pnl:,.2f}")

    def _publish_halt(self, message: str) -> None:
        self._halted = True
        set_trading_halt(self._agent_id, message)
        payload = json.dumps(
            {"message": message, "agent_id": self._agent_id, "market": self._market},
            separators=(",", ":"),
        )
        self.publish_signal(
            name=SIGNAL_HALT,
            value=payload[:900],
            ts_event=self.clock.timestamp_ns(),
        )
        self.log.error(f"HALT_TRADING: {message}")

    def record_intent_dedupe_key(self, key: str) -> bool:
        agent_key = self._agent_id or "__global__"
        return should_skip_intent(agent_key, key)


def _funds_available(funds: dict) -> float | None:
    for key in ("availablecash", "availablemargin", "available_margin", "net"):
        raw = funds.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None
