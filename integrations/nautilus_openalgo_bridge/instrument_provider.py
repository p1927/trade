"""Static instrument provider for OpenAlgo NSE index watch symbols."""

from __future__ import annotations

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import InstrumentProviderConfig

from nautilus_openalgo_bridge.nautilus_instruments import (
    build_index_instrument,
    build_us_equity_instrument,
    is_us_watch_symbol,
)


def _instrument_for_watch_symbol(symbol: str):
    if is_us_watch_symbol(symbol):
        return build_us_equity_instrument(symbol)
    return build_index_instrument(symbol)


class OpenAlgoInstrumentProvider(InstrumentProvider):
    """Preload NSE index and US equity watch instrument definitions."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        config: InstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._symbols = symbols

    async def load_all_async(self, filters: dict | None = None) -> None:
        for symbol in self._symbols:
            self.add(_instrument_for_watch_symbol(symbol))
        self._loaded = True

    async def load_async(self, instrument_id) -> None:
        symbol = str(instrument_id.symbol)
        self.add(_instrument_for_watch_symbol(symbol))
        self._loaded = True
