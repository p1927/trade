"""Replay service — sim clock + catalog quote serving."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trade_integrations.stock_simulator.catalog import ReplayCatalog
from trade_integrations.stock_simulator.config import SimConfig, load_sim_config
from trade_integrations.stock_simulator.quotes import to_openalgo_quote
from trade_integrations.stock_simulator.sim_clock import SimClock

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_service: "ReplayService | None" = None
_lock = threading.Lock()


class ReplayService:
    def __init__(self, config: SimConfig) -> None:
        self.config = config
        self.clock = SimClock(
            replay_date=config.replay_date,
            replay_time=config.replay_time,
            speed=config.speed,
            loop=config.loop,
            stepped=config.is_stepped,
        )
        self.catalog = ReplayCatalog(config.data_root)
        self._options = None
        self._tick_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.config.is_replay:
            return
        if self._tick_thread and self._tick_thread.is_alive():
            return
        self._stop.clear()
        self._tick_thread = threading.Thread(target=self._tick_loop, name="sim-replay-tick", daemon=True)
        self._tick_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def reload(self, config: SimConfig | None = None) -> None:
        if config is not None:
            self.config = config
        self.clock = SimClock(
            replay_date=self.config.replay_date,
            replay_time=self.config.replay_time,
            speed=self.config.speed,
            loop=self.config.loop,
            stepped=self.config.is_stepped,
        )
        self.catalog = ReplayCatalog(self.config.data_root)
        if self.config.is_stepped:
            from trade_integrations.stock_simulator.state_store import persist_sim_now

            persist_sim_now(replay_date=self.config.replay_date, sim_now=self.clock.now_ist())
        self.start()

    def sim_now(self) -> datetime:
        self.clock.advance_wall()
        return self.clock.now_ist()

    def get_quote(self, symbol: str, exchange: str) -> dict[str, Any]:
        now = self.sim_now()
        bar = self.catalog.bar_at(symbol, exchange, now)
        if bar is None:
            raise ValueError(f"No replay bar for {symbol}/{exchange} at {now.isoformat()}")
        return to_openalgo_quote(symbol=symbol, exchange=exchange, bar=bar, sim_ts=now)

    def get_multiquotes(self, symbols: list[dict[str, str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now = self.sim_now()
        for item in symbols:
            symbol = str(item.get("symbol") or "").upper()
            exchange = str(item.get("exchange") or "").upper()
            try:
                bar = self.catalog.bar_at(symbol, exchange, now)
                if bar is None:
                    out.append({"symbol": symbol, "exchange": exchange, "error": "no replay bar"})
                    continue
                out.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "data": to_openalgo_quote(
                            symbol=symbol, exchange=exchange, bar=bar, sim_ts=now
                        ),
                    }
                )
            except Exception as exc:
                out.append({"symbol": symbol, "exchange": exchange, "error": str(exc)})
        return out

    def get_option_chain(
        self,
        symbol: str,
        exchange: str,
        *,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        from trade_integrations.stock_simulator.options.synthesizer import synthesize_option_chain

        now = self.sim_now()
        spot_bar = self.catalog.bar_at(symbol, exchange, now)
        if spot_bar is None:
            raise ValueError(f"No spot bar for option chain: {symbol}/{exchange}")
        spot = float(spot_bar.get("ltp") or spot_bar.get("close") or 0)
        if self._options is None:
            from trade_integrations.stock_simulator.options.synthesizer import OptionsSynthesizer

            self._options = OptionsSynthesizer()
        return self._options.build_chain(
            underlying=symbol,
            exchange=exchange,
            spot=spot,
            sim_ts=now,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

    def step(self, *, minutes: int = 5) -> datetime:
        return self.clock.step(minutes=minutes)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "clock": self.clock.status(),
            "available_dates": self.catalog.available_dates("NIFTY", "NSE_INDEX")[-5:],
        }

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.clock.advance_wall()
            except Exception:
                logger.exception("sim replay tick failed")
            time.sleep(0.25)


def get_replay_service(*, reload: bool = False) -> ReplayService:
    global _service
    with _lock:
        config = load_sim_config()
        if _service is None or reload:
            if _service is not None:
                _service.stop()
            _service = ReplayService(config)
            if config.is_replay:
                _service.start()
        return _service
