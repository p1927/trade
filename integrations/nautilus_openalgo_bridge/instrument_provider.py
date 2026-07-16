"""Static instrument provider for OpenAlgo NSE index watch symbols."""

from __future__ import annotations

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig

from nautilus_openalgo_bridge.nautilus_instruments import build_index_instrument


class OpenAlgoInstrumentProvider(InstrumentProvider):
    """Preload NIFTY / BANKNIFTY / INDIAVIX IndexInstrument definitions."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        config: InstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._symbols = symbols

    async def load_all_async(self, filters: dict | None = None) -> None:
        for symbol in self._symbols:
            self.add(build_index_instrument(symbol))
        self._loaded = True

    async def load_async(self, instrument_id) -> None:
        symbol = str(instrument_id.symbol)
        self.add(build_index_instrument(symbol))
        self._loaded = True
