"""Nautilus WatchActor — evaluate watch rules on QuoteTick and publish bridge signals."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import QuoteTick

from nautilus_openalgo_bridge.config import get_bridge_config
from nautilus_openalgo_bridge.handoff import handoff_mtime, load_agent_watch_spec, load_handoff
from nautilus_openalgo_bridge.models import BridgeSignal, QuoteSnapshot, WatchSpec
from nautilus_openalgo_bridge.nautilus_instruments import default_watch_instrument_ids
from nautilus_openalgo_bridge.reconcile import open_positions_from_book, total_unrealized_pnl
from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules
from nautilus_openalgo_bridge.watch_eval import evaluate_watch_spec

SIGNAL_REVIEW = BridgeSignal.REVIEW_NEEDED.value
SIGNAL_EXIT = BridgeSignal.EXIT_NOW.value
SIGNAL_THESIS = BridgeSignal.THESIS_BROKEN.value


class WatchActorConfig(ActorConfig, frozen=True):
    agent_id: str | None = None
    alert_cooldown_sec: int = 300
    trigger_vibe: bool = True


class WatchActor(Actor):
    """Subscribe to index QuoteTicks; publish REVIEW_NEEDED / EXIT_NOW signals."""

    def __init__(self, config: WatchActorConfig) -> None:
        super().__init__(config)
        self._bridge = get_bridge_config()
        self._agent_id = (config.agent_id or "").strip() or None
        self._alert_cooldown_sec = max(30, int(config.alert_cooldown_sec))
        self._trigger_vibe = bool(config.trigger_vibe)
        self._spec = WatchSpec()
        self._baselines: dict[str, float] = {}
        self._last_alert_at: dict[str, float] = {}
        self._last_handoff_mtime: float | None = None
        self._last_agent_reload_at: float = 0.0
        self._timer_names: list[str] = []

    def on_start(self) -> None:
        self._reload_watch_spec(force=True)
        for iid in default_watch_instrument_ids(self._bridge.watch_symbols):
            self.subscribe_quote_ticks(iid)
        self.subscribe_signal(SIGNAL_REVIEW)
        self.subscribe_signal(SIGNAL_EXIT)
        self.subscribe_signal(SIGNAL_THESIS)

        now = datetime.now(dt_timezone.utc)
        self._set_interval_timer("watch_reload", timedelta(seconds=30), self._on_reload_timer)
        self._set_interval_timer("watch_heartbeat", timedelta(minutes=1), self._on_heartbeat)
        self._schedule_flatten_timer()
        self.log.info(
            f"WatchActor started agent={self._agent_id or 'default'} rules={len(self._spec.rules)}",
        )

    def on_stop(self) -> None:
        for name in self._timer_names:
            try:
                self.clock.cancel_timer(name)
            except Exception:
                pass
        self._timer_names.clear()

    def _set_interval_timer(self, name: str, interval: timedelta, callback) -> None:
        start = datetime.now(dt_timezone.utc) + timedelta(seconds=1)
        self.clock.set_timer(name, interval, start, stop_time=None, callback=callback)
        self._timer_names.append(name)

    def _schedule_flatten_timer(self) -> None:
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        parts = self._bridge.market_close.strip().split(":")
        if len(parts) != 2:
            return
        close_h, close_m = int(parts[0]), int(parts[1])
        flatten_at = now.replace(hour=close_h, minute=max(0, close_m - 10), second=0, microsecond=0)
        if flatten_at <= now:
            return
        self.clock.set_time_alert("flatten_at_close", flatten_at, self._on_flatten_alert)
        self._timer_names.append("flatten_at_close")

    def _on_reload_timer(self, _event) -> None:
        self._reload_watch_spec()

    def _on_heartbeat(self, _event) -> None:
        self.log.info(
            f"WatchActor heartbeat agent={self._agent_id or 'default'} baselines={len(self._baselines)}",
        )

    def _on_flatten_alert(self, _event) -> None:
        handoff = load_handoff(self._agent_id) if self._agent_id else None
        if handoff and handoff.stop_rules.flatten_at_close:
            self.publish_signal(
                name=SIGNAL_EXIT,
                value="session_close_flatten",
                ts_event=self.clock.timestamp_ns(),
            )

    def _reload_watch_spec(self, *, force: bool = False) -> None:
        if not self._agent_id:
            self._spec = WatchSpec.from_dict({})
            return
        mt = handoff_mtime(self._agent_id)
        if not force and mt is not None and mt == self._last_handoff_mtime:
            if time.time() - self._last_agent_reload_at < 30:
                return
        self._last_handoff_mtime = mt
        self._last_agent_reload_at = time.time()
        handoff = load_handoff(self._agent_id)
        if handoff and handoff.watch_spec.rules:
            self._spec = handoff.watch_spec
            return
        raw = load_agent_watch_spec(self._agent_id)
        if raw:
            self._spec = WatchSpec.from_dict(raw)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        symbol = str(tick.instrument_id.symbol)
        ltp = tick.bid_price.as_double()
        self._baselines.setdefault(symbol, ltp)
        snap = QuoteSnapshot(symbol=symbol, exchange="NSE", ltp=ltp)
        quotes = {symbol: snap}
        alerts = evaluate_watch_spec(self._spec, quotes, baselines=self._baselines)

        if self._agent_id:
            handoff = load_handoff(self._agent_id)
            if handoff:
                try:
                    from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

                    rows = open_positions_from_book(get_openalgo_client().get_position_book())
                    pnl = total_unrealized_pnl(rows)
                    stop_alert = evaluate_stop_rules(
                        handoff,
                        quotes,
                        unrealized_pnl_inr=pnl,
                        config=self._bridge,
                    )
                    if stop_alert is not None:
                        alerts.insert(0, stop_alert)
                except Exception:
                    pass

        now = time.time()
        for alert in alerts:
            key = f"{alert.symbol}:{alert.rule.metric if alert.rule else alert.signal.value}"
            if now - self._last_alert_at.get(key, 0.0) < self._alert_cooldown_sec:
                continue
            self._last_alert_at[key] = now
            signal_name = alert.signal.value
            payload = json.dumps(
                {
                    "message": alert.message,
                    "symbol": alert.symbol,
                    "agent_id": self._agent_id,
                    "ltp": alert.ltp,
                    "trigger_vibe": self._trigger_vibe,
                },
                separators=(",", ":"),
            )
            self.publish_signal(
                name=signal_name,
                value=payload[:900],
                ts_event=tick.ts_event,
            )
            self.log.warning(f"WATCH ALERT [{signal_name}]: {alert.message}")
