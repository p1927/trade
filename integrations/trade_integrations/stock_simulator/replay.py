"""Replay service — sim clock + catalog quote serving."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trade_integrations.stock_simulator.catalog import ReplayCatalog
from trade_integrations.stock_simulator.config import SimConfig, load_sim_config
from trade_integrations.stock_simulator.hf_paths import hf_replay_root
from trade_integrations.stock_simulator.quotes import to_openalgo_quote
from trade_integrations.stock_simulator.sim_clock import SimClock
from trade_integrations.stock_simulator.week_rotation import latest_trading_days, week_index_for_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_service: "ReplayService | None" = None
_lock = threading.Lock()


class ReplayService:
    def __init__(self, config: SimConfig) -> None:
        self.config = config
        week_dates = list(config.week_dates) if config.week_mode else None
        if week_dates and config.replay_date not in week_dates:
            week_dates = None
        week_index = week_index_for_date(list(week_dates or []), config.replay_date) if week_dates else None
        os.environ["NSE_REPLAY_DATE"] = config.replay_date
        self.clock = SimClock(
            replay_date=config.replay_date,
            replay_time=config.replay_time,
            speed=config.speed,
            loop=config.loop,
            stepped=config.is_stepped,
            week_dates=week_dates,
            week_index=week_index,
            on_replay_date_change=self._on_replay_date_change,
        )
        self.catalog = ReplayCatalog(config.data_root)
        self._options = None
        self._options_store = None
        self._tick_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _on_replay_date_change(self, old_date: str, new_date: str) -> None:
        logger.info("sim replay week rotation %s -> %s", old_date, new_date)
        os.environ["NSE_REPLAY_DATE"] = new_date
        self.config = replace(self.config, replay_date=new_date)
        self._options_store = None
        try:
            from threading import Thread

            from utils.auth_utils import async_master_contract_download

            Thread(target=async_master_contract_download, args=("stock_simulator",), daemon=True).start()
        except Exception:
            logger.debug("master contract refresh skipped after week rotation", exc_info=True)

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
        week_dates = list(self.config.week_dates) if self.config.week_mode else None
        if week_dates and self.config.replay_date not in week_dates:
            week_dates = None
        week_index = week_index_for_date(list(week_dates or []), self.config.replay_date) if week_dates else None
        os.environ["NSE_REPLAY_DATE"] = self.config.replay_date
        self.clock = SimClock(
            replay_date=self.config.replay_date,
            replay_time=self.config.replay_time,
            speed=self.config.speed,
            loop=self.config.loop,
            stepped=self.config.is_stepped,
            week_dates=week_dates,
            week_index=week_index,
            on_replay_date_change=self._on_replay_date_change,
        )
        self.catalog = ReplayCatalog(self.config.data_root)
        self._options_store = None
        if self.config.is_stepped:
            from trade_integrations.stock_simulator.state_store import persist_sim_now

            persist_sim_now(replay_date=self.config.replay_date, sim_now=self.clock.now_ist())
        self.start()

    def sim_now(self) -> datetime:
        self.clock.advance_wall()
        return self.clock.now_ist()

    def get_quote(self, symbol: str, exchange: str) -> dict[str, Any]:
        now = self.sim_now()
        bar = self._quote_bar(symbol, exchange, now)
        if bar is None:
            raise ValueError(f"No replay bar for {symbol}/{exchange} at {now.isoformat()}")
        return to_openalgo_quote(
            symbol=symbol,
            exchange=exchange,
            bar=bar,
            sim_ts=now,
            bar_minutes=int(bar.get("bar_minutes") or 1),
            oi=int(bar.get("oi") or 0),
        )

    def get_multiquotes(self, symbols: list[dict[str, str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now = self.sim_now()
        for item in symbols:
            symbol = str(item.get("symbol") or "").upper()
            exchange = str(item.get("exchange") or "").upper()
            try:
                bar = self._quote_bar(symbol, exchange, now)
                if bar is None:
                    out.append({"symbol": symbol, "exchange": exchange, "error": "no replay bar"})
                    continue
                out.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "data": to_openalgo_quote(
                            symbol=symbol,
                            exchange=exchange,
                            bar=bar,
                            sim_ts=now,
                            bar_minutes=int(bar.get("bar_minutes") or 1),
                            oi=int(bar.get("oi") or 0),
                        ),
                    }
                )
            except Exception as exc:
                out.append({"symbol": symbol, "exchange": exchange, "error": str(exc)})
        return out

    def _quote_bar(self, symbol: str, exchange: str, now: datetime) -> dict[str, Any] | None:
        ex = exchange.upper()
        if ex in {"NFO", "BFO"}:
            if self._options_store is None:
                from trade_integrations.stock_simulator.options.replay_store import OptionsReplayStore

                self._options_store = OptionsReplayStore(self.config.data_root)
            opt = self._options_store.quote_at(symbol, ex, now)
            if opt is not None:
                return {**opt, "bar_minutes": 1}
        bar = self.catalog.bar_at(symbol, exchange, now)
        if bar is None:
            return None
        return dict(bar)

    def get_option_chain(
        self,
        symbol: str,
        exchange: str,
        *,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        now = self.sim_now()
        spot_bar = self.catalog.bar_at(symbol, exchange, now)
        if spot_bar is None:
            raise ValueError(f"No spot bar for option chain: {symbol}/{exchange}")
        spot = float(
            to_openalgo_quote(
                symbol=symbol,
                exchange=exchange,
                bar=spot_bar,
                sim_ts=now,
                bar_minutes=int(spot_bar.get("bar_minutes") or 1),
            )["ltp"]
        )

        if self._options_store is None:
            from trade_integrations.stock_simulator.options.replay_store import OptionsReplayStore

            self._options_store = OptionsReplayStore(self.config.data_root)

        if self._options_store.has_underlying(symbol, exchange):
            chain = self._options_store.chain_at(
                underlying=symbol,
                exchange=exchange,
                spot=spot,
                sim_ts=now,
                expiry_date=expiry_date,
                strike_count=strike_count,
            )
            if chain is not None:
                return chain

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
        bn_dates = self.catalog.available_dates("BANKNIFTY", "NSE_INDEX")
        nifty_dates = self.catalog.available_dates("NIFTY", "NSE_INDEX")
        week_dates = list(self.clock.week_dates) if self.clock.week_mode else (
            list(self.config.week_dates)
            if self.config.week_mode
            else latest_trading_days(self.config.data_root, self.config.week_days_count)
        )
        data_watermark = nifty_dates[-1] if nifty_dates else None
        active_expiry = None
        options_source = (
            "hf_replay"
            if nifty_dates and (hf_replay_root(self.config.data_root) / "options" / "NIFTY").is_dir()
            else None
        )
        store = self._options_store
        if store is not None and store.has_underlying("NIFTY", "NSE_INDEX"):
            now = self.clock.now_ist()
            spot_bar = self.catalog.bar_at("NIFTY", "NSE_INDEX", now)
            if spot_bar is not None:
                chain = store.chain_at(
                    underlying="NIFTY",
                    exchange="NSE_INDEX",
                    spot=float(spot_bar["ltp"]),
                    sim_ts=now,
                    strike_count=1,
                )
                if chain is not None:
                    active_expiry = chain.get("expiry_date")
        return {
            "mode": self.config.mode,
            "clock": self.clock.status(),
            "week_mode": self.clock.week_mode,
            "week_dates": week_dates,
            "week_days_count": self.config.week_days_count,
            "data_watermark": data_watermark,
            "options_source": options_source,
            "active_expiry": active_expiry,
            "available_dates": {
                "NIFTY": nifty_dates[-5:] if nifty_dates else [],
                "BANKNIFTY": bn_dates[-5:] if bn_dates else [],
            },
            "hf_replay": bool(bn_dates),
        }

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.clock.advance_wall()
            except Exception:
                logger.exception("sim replay tick failed")
            time.sleep(0.25)


def _maybe_hydrate_sim_env() -> None:
    """Load UI-persisted sim settings when running inside OpenAlgo."""
    try:
        from broker.stock_simulator.api._trade_path import hydrate_simulator_env_from_db

        hydrate_simulator_env_from_db()
    except Exception:
        pass


def _config_changed(existing: SimConfig, fresh: SimConfig) -> bool:
    return (
        existing.mode != fresh.mode
        or existing.replay_date != fresh.replay_date
        or existing.replay_time != fresh.replay_time
        or existing.speed != fresh.speed
        or existing.loop != fresh.loop
        or existing.eval_mode != fresh.eval_mode
        or existing.data_root != fresh.data_root
        or existing.week_mode != fresh.week_mode
        or existing.week_days_count != fresh.week_days_count
        or existing.week_dates != fresh.week_dates
    )


def get_replay_service(*, reload: bool = False) -> ReplayService:
    global _service
    with _lock:
        _maybe_hydrate_sim_env()
        config = load_sim_config()
        if _service is not None and not reload and _config_changed(_service.config, config):
            reload = True
        if _service is None or reload:
            if _service is not None:
                _service.stop()
            _service = ReplayService(config)
            if config.is_replay:
                _service.start()
        return _service
