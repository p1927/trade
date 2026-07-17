"""Bridge signal handler — Nautilus signals → Vibe trigger + OpenAlgo execution."""

from __future__ import annotations

import json
import os
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig

from nautilus_openalgo_bridge.config import allow_vibe_alert_outside_market_hours
from nautilus_openalgo_bridge.market_hours import agent_market, is_market_open_for_market
from nautilus_openalgo_bridge.handoff import load_handoff
from nautilus_openalgo_bridge.intent_queue import process_pending_intents
from nautilus_openalgo_bridge.models import BridgeSignal, WatchAlert, WatchRule
from nautilus_openalgo_bridge.risk_state import is_trading_halted, set_trading_halt

SIGNAL_REVIEW = BridgeSignal.REVIEW_NEEDED.value
SIGNAL_EXIT = BridgeSignal.EXIT_NOW.value
SIGNAL_THESIS = BridgeSignal.THESIS_BROKEN.value
SIGNAL_HALT = BridgeSignal.HALT_TRADING.value
SIGNAL_EXECUTE = BridgeSignal.EXECUTE_INTENT.value


class BridgeSignalActorConfig(ActorConfig, frozen=True):
    agent_id: str | None = None
    trigger_vibe: bool = True


class BridgeSignalActor(Actor):
    """Subscribe to bridge signals and dispatch side effects outside Nautilus."""

    def __init__(self, config: BridgeSignalActorConfig) -> None:
        super().__init__(config)
        self._agent_id = (config.agent_id or "").strip() or None
        self._trigger_vibe = bool(config.trigger_vibe)

    def on_start(self) -> None:
        for name in (SIGNAL_REVIEW, SIGNAL_EXIT, SIGNAL_THESIS, SIGNAL_HALT, SIGNAL_EXECUTE):
            self.subscribe_signal(name)
        from datetime import datetime, timedelta, timezone

        start = datetime.now(timezone.utc) + timedelta(seconds=5)
        self.clock.set_timer(
            "intent_queue",
            timedelta(seconds=10),
            start,
            stop_time=None,
            callback=self._on_intent_queue_timer,
        )
        if os.getenv("NAUTILUS_TEST_FIRE_ALERT", "").strip().lower() in {"1", "true", "yes", "on"}:
            # After ExecEngine ~10s startup delay + actor init
            test_at = datetime.now(timezone.utc) + timedelta(seconds=28)
            self.clock.set_time_alert("test_fire_alert", test_at, self._on_test_fire_alert)
        self.log.info(f"BridgeSignalActor started agent={self._agent_id or 'default'}")

    def on_signal(self, signal) -> None:
        name = getattr(signal, "name", None)
        if not name:
            return
        payload = _parse_payload(getattr(signal, "value", ""))
        agent_id = str(payload.get("agent_id") or self._agent_id or "").strip()
        if not agent_id:
            self.log.warning(f"signal {name} skipped — no agent_id")
            return

        if is_trading_halted(agent_id):
            self.log.warning(f"signal {name} skipped — trading halted for {agent_id}")
            return

        if name == SIGNAL_REVIEW:
            if not self._trigger_vibe or not payload.get("trigger_vibe", True):
                return
            if not allow_vibe_alert_outside_market_hours() and not is_market_open_for_market(
                agent_market(agent_id),
            ):
                self.log.info("skip Vibe dispatch — outside market hours")
                return
            self._dispatch_vibe_alert(agent_id, payload)
        elif name == SIGNAL_THESIS:
            if not self._trigger_vibe:
                return
            if not allow_vibe_alert_outside_market_hours() and not is_market_open_for_market(
                agent_market(agent_id),
            ):
                self.log.info("skip thesis dispatch — outside market hours")
                return
            self._dispatch_thesis_alert(agent_id, payload)
        elif name == SIGNAL_EXIT:
            self._dispatch_exit(agent_id, payload)
        elif name == SIGNAL_HALT:
            reason = str(payload.get("message") or "risk breach")
            set_trading_halt(agent_id, reason)
            self.log.error(f"HALT_TRADING for {agent_id}: {reason}")
        elif name == SIGNAL_EXECUTE:
            self.log.info(f"EXECUTE_INTENT queued for {agent_id}")

    def _on_test_fire_alert(self, _event) -> None:
        if not self._agent_id:
            self.log.warning("test alert skipped — no agent_id")
            return
        self.log.info("NAUTILUS_TEST_FIRE_ALERT — injecting synthetic REVIEW_NEEDED")
        self._dispatch_vibe_alert(
            self._agent_id,
            {
                "message": "Integration test alert (NAUTILUS_TEST_FIRE_ALERT)",
                "symbol": "NIFTY",
                "ltp": 0.0,
                "trigger_vibe": True,
            },
        )

    def _on_intent_queue_timer(self, _event) -> None:
        if self._agent_id and is_trading_halted(self._agent_id):
            self.log.warning("intent queue skipped — trading halted")
            return
        import concurrent.futures

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                results = pool.submit(process_pending_intents, max_count=3).result(timeout=120)
        except Exception as exc:
            self.log.warning(f"intent queue tick failed: {exc}")
            return
        for row in results:
            self.log.info(f"intent {row.get('intent_id')} → {row.get('status')}")

    def _dispatch_vibe_alert(self, agent_id: str, payload: dict[str, Any]) -> None:
        from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

        alert = WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=WatchRule(symbol=str(payload.get("symbol") or "NIFTY"), metric="spot_move_pct", threshold=0),
            symbol=str(payload.get("symbol") or "NIFTY"),
            message=str(payload.get("message") or "watch alert"),
            ltp=float(payload["ltp"]) if payload.get("ltp") is not None else None,
        )
        try:
            result = dispatch_watch_alert_sync(agent_id, alert)
            reason = result.get("reason") or result.get("error") or ""
            self.log.info(f"Vibe dispatch: {result.get('status')}" + (f" ({reason})" if reason else ""))
        except Exception as exc:
            self.log.error(f"Vibe dispatch failed: {exc}")

    def _dispatch_thesis_alert(self, agent_id: str, payload: dict[str, Any]) -> None:
        from nautilus_openalgo_bridge.vibe_trigger import dispatch_thesis_alert_sync

        alert = WatchAlert(
            signal=BridgeSignal.THESIS_BROKEN,
            rule=WatchRule(symbol=str(payload.get("symbol") or "NIFTY"), metric="spot_move_pct", threshold=0),
            symbol=str(payload.get("symbol") or "NIFTY"),
            message=str(payload.get("message") or "thesis break"),
            ltp=float(payload["ltp"]) if payload.get("ltp") is not None else None,
        )
        try:
            result = dispatch_thesis_alert_sync(agent_id, alert)
            self.log.info(f"Thesis dispatch: {result.get('status')}")
        except Exception as exc:
            self.log.error(f"Thesis dispatch failed: {exc}")

    def _dispatch_exit(self, agent_id: str, payload: dict[str, Any]) -> None:
        from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent

        handoff = load_handoff(agent_id)
        alert = WatchAlert(
            signal=BridgeSignal.EXIT_NOW,
            rule=None,
            symbol=str(payload.get("symbol") or (handoff.underlying if handoff else "NIFTY")),
            message=str(payload.get("message") or payload.get("value") or "exit"),
        )
        try:
            result = dispatch_exit_intent(
                agent_id,
                alert,
                underlying=handoff.underlying if handoff else None,
            )
            self.log.info(f"EXIT intent: {result.get('status')}")
        except Exception as exc:
            self.log.error(f"EXIT intent failed: {exc}")


def _parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"message": value}
    return {"message": str(value)}
