"""Nautilus live client factories."""

from __future__ import annotations

import asyncio

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import Venue

from nautilus_openalgo_bridge.config import get_bridge_config
from nautilus_openalgo_bridge.instrument_provider import OpenAlgoInstrumentProvider
from nautilus_openalgo_bridge.nautilus_config import OpenAlgoDataClientConfig
from nautilus_openalgo_bridge.openalgo_live_data import OpenAlgoLiveDataClient


class OpenAlgoLiveDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: OpenAlgoDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> OpenAlgoLiveDataClient:
        symbols = tuple(config.watch_symbols or ())
        provider = OpenAlgoInstrumentProvider(symbols=symbols)
        return OpenAlgoLiveDataClient(
            loop=loop,
            client_id=ClientId(name),
            venue=Venue("NSE"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
        )
