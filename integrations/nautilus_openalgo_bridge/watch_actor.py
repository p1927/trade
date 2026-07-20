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
from nautilus_openalgo_bridge.models import BridgeSignal, QuoteSnapshot, WatchAlert, WatchSpec
from nautilus_openalgo_bridge.nautilus_instruments import default_watch_instrument_ids
from nautilus_openalgo_bridge.reconcile import open_positions_from_book, total_unrealized_pnl
from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules
from nautilus_openalgo_bridge.thesis_eval import evaluate_thesis_for_agent
from nautilus_openalgo_bridge.watch_eval import evaluate_watch_spec

SIGNAL_REVIEW = BridgeSignal.REVIEW_NEEDED.value
SIGNAL_EXIT = BridgeSignal.EXIT_NOW.value
SIGNAL_THESIS = BridgeSignal.THESIS_BROKEN.value


class WatchActorConfig(ActorConfig, frozen=True):
    agent_id: str | None = None
    alert_cooldown_sec: int = 300
    trigger_vibe: bool = True
    market: str = "IN"
    watch_symbols: list[str] | None = None


class WatchActor(Actor):
    """Subscribe to index QuoteTicks; publish REVIEW_NEEDED / EXIT_NOW signals."""

    def __init__(self, config: WatchActorConfig) -> None:
        super().__init__(config)
        self._bridge = get_bridge_config()
        self._agent_id = (config.agent_id or "").strip() or None
        self._alert_cooldown_sec = max(30, int(config.alert_cooldown_sec))
        self._trigger_vibe = bool(config.trigger_vibe)
        self._market = str(config.market or "IN").upper()
        self._watch_symbols = tuple(config.watch_symbols or ())
        self._spec = WatchSpec()
        self._baselines: dict[str, float] = {}
        self._latest_ltp: dict[str, float] = {}
        self._oi_baselines: dict[str, float] = {}
        self._volume_baselines: dict[str, float] = {}
        self._last_alert_at: dict[str, float] = {}
        self._last_handoff_mtime: float | None = None
        self._last_agent_reload_at: float = 0.0
        self._timer_names: list[str] = []

    def on_start(self) -> None:
        self._reload_watch_spec(force=True)
        symbols = self._watch_symbols or self._bridge.watch_symbols
        for iid in default_watch_instrument_ids(symbols, market=self._market):
            self.subscribe_quote_ticks(iid)
        self.subscribe_signal(SIGNAL_REVIEW)
        self.subscribe_signal(SIGNAL_EXIT)
        self.subscribe_signal(SIGNAL_THESIS)

        self._set_interval_timer("watch_reload", timedelta(seconds=30), self._on_reload_timer)
        self._set_interval_timer("watch_heartbeat", timedelta(minutes=1), self._on_heartbeat)
        if self._agent_id:
            self._set_interval_timer("thesis_eval", timedelta(seconds=30), self._on_thesis_timer)
        self._schedule_flatten_timer()
        self.log.info(
            f"WatchActor started agent={self._agent_id or 'default'} market={self._market} rules={len(self._spec.rules)}",
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
        if self._market == "US":
            return
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        parts = self._bridge.market_close.strip().split(":")
        if len(parts) != 2:
            return
        close_h, close_m = int(parts[0]), int(parts[1])
        close_dt = datetime.combine(
            now.date(),
            datetime.min.time().replace(hour=close_h, minute=close_m),
            tzinfo=ist,
        )
        flatten_at = close_dt - timedelta(minutes=10)
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

    def _on_thesis_timer(self, _event) -> None:
        if not self._agent_id:
            return
        from nautilus_openalgo_bridge.market_hours import is_market_open_for_market

        if not is_market_open_for_market(self._market):
            return
        quotes = {sym: QuoteSnapshot(symbol=sym, exchange=self._market, ltp=ltp) for sym, ltp in self._baselines.items()}
        alert = self._evaluate_thesis_alerts(quotes)
        if alert is not None:
            self._publish_alert(alert, ts_event=self.clock.timestamp_ns())

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
        else:
            raw = load_agent_watch_spec(self._agent_id)
            if raw:
                self._spec = WatchSpec.from_dict(raw)
        self._refresh_spot_baselines()

    def _refresh_spot_baselines(self) -> None:
        for rule in self._spec.rules:
            if rule.metric != "spot_move_pct":
                continue
            for key in (rule.symbol, rule.symbol.upper()):
                latest = self._latest_ltp.get(key)
                if latest is not None:
                    self._baselines[key] = latest

    def _evaluate_thesis_alerts(self, quotes: dict[str, QuoteSnapshot]) -> WatchAlert | None:
        if not self._agent_id:
            return None
        handoff = load_handoff(self._agent_id)
        underlying = handoff.underlying if handoff else (self._watch_symbols[0] if self._watch_symbols else "NIFTY")
        quote = quotes.get(underlying) or quotes.get(str(underlying).upper())
        live_spot = quote.ltp if quote else None
        pnl = self._position_pnl()
        return evaluate_thesis_for_agent(self._agent_id, live_spot=live_spot, position_pnl=pnl)

    def _position_pnl(self) -> float | None:
        if not self._agent_id:
            return None
        if self._market == "US":
            try:
                from trade_integrations.dataflows.alpaca import list_alpaca_positions

                rows = list_alpaca_positions()
                total = 0.0
                found = False
                for row in rows:
                    raw = row.get("unrealized_pl") or row.get("unrealized_plpc")
                    if raw is None:
                        continue
                    try:
                        total += float(raw)
                        found = True
                    except (TypeError, ValueError):
                        continue
                return total if found else None
            except Exception:
                return None
        try:
            from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

            rows = open_positions_from_book(get_openalgo_client().get_position_book())
            return total_unrealized_pnl(rows)
        except Exception:
            return None

    def _publish_alert(self, alert, *, ts_event: int) -> None:
        now = time.time()
        key = f"{alert.symbol}:{alert.rule.metric if alert.rule else alert.signal.value}"
        if now - self._last_alert_at.get(key, 0.0) < self._alert_cooldown_sec:
            return
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
            ts_event=ts_event,
        )
        self.log.warning(f"WATCH ALERT [{signal_name}]: {alert.message}")

    def on_quote_tick(self, tick: QuoteTick) -> None:
        from nautilus_openalgo_bridge.market_hours import is_market_open_for_market

        if not is_market_open_for_market(self._market):
            return
        symbol = str(tick.instrument_id.symbol)
        ltp = tick.bid_price.as_double()
        self._latest_ltp[symbol] = ltp
        self._baselines.setdefault(symbol, ltp)
        snap = QuoteSnapshot(symbol=symbol, exchange=self._market, ltp=ltp)
        quotes = {symbol: snap}
        alerts = evaluate_watch_spec(
            self._spec,
            quotes,
            baselines=self._baselines,
            oi_baselines=self._oi_baselines,
            volume_baselines=self._volume_baselines,
        )

        if self._agent_id:
            handoff = load_handoff(self._agent_id)
            if handoff and self._market != "US":
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
            elif handoff and self._market == "US":
                pnl = self._position_pnl()
                stop_alert = evaluate_stop_rules(
                    handoff,
                    quotes,
                    unrealized_pnl_inr=pnl,
                    config=self._bridge,
                )
                if stop_alert is not None:
                    alerts.insert(0, stop_alert)

        for alert in alerts:
            self._publish_alert(alert, ts_event=tick.ts_event)
