"""OpenAlgo REST poller as a Nautilus LiveMarketDataClient."""

from __future__ import annotations

import asyncio
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.enums import LogColor
from nautilus_trader.data.messages import SubscribeQuoteTicks
from nautilus_trader.data.messages import UnsubscribeQuoteTicks
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue

from nautilus_openalgo_bridge.config import get_bridge_config
from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed
from nautilus_openalgo_bridge.instrument_provider import OpenAlgoInstrumentProvider
from nautilus_openalgo_bridge.nautilus_config import OpenAlgoDataClientConfig
from nautilus_openalgo_bridge.nautilus_instruments import (
    build_index_instrument,
    quote_snapshot_to_tick,
    watch_symbol_to_instrument_id,
)


class OpenAlgoLiveDataClient(LiveMarketDataClient):
    """Poll OpenAlgo multiquotes and publish QuoteTick into the Nautilus data engine."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client_id: ClientId,
        venue: Venue | None,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: OpenAlgoInstrumentProvider,
        config: OpenAlgoDataClientConfig,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._cfg = config
        self._feed = OpenAlgoQuoteFeed()
        self._quote_subs: set[InstrumentId] = set()
        self._poll_task: asyncio.Task | None = None
        self._poll_active = False
        self._symbol_by_instrument: dict[InstrumentId, str] = {}

    async def _connect(self) -> None:
        bridge_cfg = get_bridge_config()
        symbols = self._cfg.watch_symbols or bridge_cfg.watch_symbols
        for symbol in symbols:
            instrument = build_index_instrument(symbol)
            self._symbol_by_instrument[instrument.id] = normalize_symbol(symbol)
            self._handle_data(instrument)
        self._poll_active = True
        self._poll_task = self.create_task(self._poll_loop())
        self._log.info(
            f"OpenAlgo live data connected (poll={self._cfg.poll_interval_ms}ms symbols={','.join(symbols)})",
            LogColor.GREEN,
        )

    async def _disconnect(self) -> None:
        self._poll_active = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        self._quote_subs.add(command.instrument_id)
        symbol = self._symbol_by_instrument.get(command.instrument_id)
        if symbol is None:
            symbol = str(command.instrument_id.symbol)
            self._symbol_by_instrument[command.instrument_id] = symbol

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        self._quote_subs.discard(command.instrument_id)

    async def _poll_loop(self) -> None:
        interval = max(0.5, self._cfg.poll_interval_ms / 1000.0)
        while self._poll_active:
            if self._quote_subs:
                symbols = list(
                    {
                        self._symbol_by_instrument.get(iid, str(iid.symbol))
                        for iid in self._quote_subs
                    }
                )
                try:
                    quotes = await asyncio.to_thread(self._feed.poll, symbols)
                except Exception as exc:
                    self._log.warning(f"OpenAlgo poll failed: {exc}")
                    quotes = {}
                for iid in list(self._quote_subs):
                    symbol = self._symbol_by_instrument.get(iid, str(iid.symbol))
                    snap = quotes.get(symbol) or quotes.get(symbol.upper())
                    if snap is None:
                        continue
                    tick = quote_snapshot_to_tick(snap, instrument_id=iid)
                    self._handle_data(tick)
            await asyncio.sleep(interval)


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def instrument_id_for_watch_symbol(symbol: str) -> InstrumentId:
    return watch_symbol_to_instrument_id(symbol)
