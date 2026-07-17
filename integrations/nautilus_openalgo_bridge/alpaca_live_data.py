"""Alpaca REST poller as a Nautilus LiveMarketDataClient."""

from __future__ import annotations

import asyncio

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

from nautilus_openalgo_bridge.alpaca_quote_feed import AlpacaQuoteFeed
from nautilus_openalgo_bridge.instrument_provider import OpenAlgoInstrumentProvider
from nautilus_openalgo_bridge.nautilus_config import AlpacaDataClientConfig
from nautilus_openalgo_bridge.nautilus_instruments import (
    build_us_equity_instrument,
    quote_snapshot_to_tick,
    us_symbol_to_instrument_id,
)


class AlpacaLiveDataClient(LiveMarketDataClient):
    """Poll Alpaca quotes and publish QuoteTick into the Nautilus data engine."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client_id: ClientId,
        venue: Venue | None,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: OpenAlgoInstrumentProvider,
        config: AlpacaDataClientConfig,
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
        self._feed = AlpacaQuoteFeed()
        self._quote_subs: set[InstrumentId] = set()
        self._poll_task: asyncio.Task | None = None
        self._poll_active = False
        self._symbol_by_instrument: dict[InstrumentId, str] = {}

    async def _connect(self) -> None:
        symbols = self._cfg.watch_symbols or ("SPY",)
        for symbol in symbols:
            instrument = build_us_equity_instrument(symbol)
            self._symbol_by_instrument[instrument.id] = str(symbol).upper()
            self._handle_data(instrument)
        self._poll_active = True
        self._poll_task = self.create_task(self._poll_loop())
        self._log.info(
            f"Alpaca live data connected (poll={self._cfg.poll_interval_ms}ms symbols={','.join(symbols)})",
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
        from nautilus_openalgo_bridge.market_hours import closed_market_poll_interval_sec, is_us_market_session_open

        interval = max(0.5, self._cfg.poll_interval_ms / 1000.0)
        while self._poll_active:
            if not is_us_market_session_open():
                await asyncio.sleep(closed_market_poll_interval_sec())
                continue
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
                    self._log.warning(f"Alpaca poll failed: {exc}")
                    quotes = {}
                for iid in list(self._quote_subs):
                    symbol = self._symbol_by_instrument.get(iid, str(iid.symbol))
                    snap = quotes.get(symbol) or quotes.get(symbol.upper())
                    if snap is None:
                        continue
                    tick = quote_snapshot_to_tick(snap, instrument_id=iid)
                    self._handle_data(tick)
            await asyncio.sleep(interval)


def instrument_id_for_us_symbol(symbol: str) -> InstrumentId:
    return us_symbol_to_instrument_id(symbol)
