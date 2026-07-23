"""OpenAlgo MarketContext client for Trade integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MarketContextError(RuntimeError):
    """Raised when MarketContext fetch or parse fails."""


@dataclass(frozen=True)
class MarketContext:
    context_generation: str
    data_broker: str
    execution_venue: str
    analyze_mode: bool
    market_region: str
    positions_authority: str
    quotes_source: str
    simulator: dict[str, Any]
    capabilities: tuple[str, ...]

    @classmethod
    def from_api_data(cls, data: dict[str, Any]) -> MarketContext:
        required = (
            "context_generation",
            "data_broker",
            "execution_venue",
            "analyze_mode",
            "market_region",
            "positions_authority",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise MarketContextError(f"MarketContext payload missing fields: {', '.join(missing)}")
        simulator = data.get("simulator")
        if not isinstance(simulator, dict):
            simulator = {"active": False}
        caps = data.get("capabilities") or []
        if not isinstance(caps, list):
            caps = []
        return cls(
            context_generation=str(data["context_generation"]),
            data_broker=str(data["data_broker"]),
            execution_venue=str(data["execution_venue"]),
            analyze_mode=bool(data["analyze_mode"]),
            market_region=str(data["market_region"]),
            positions_authority=str(data["positions_authority"]),
            quotes_source=str(data.get("quotes_source") or "broker_plugin"),
            simulator=dict(simulator),
            capabilities=tuple(str(x) for x in caps),
        )

    def is_paper_execution(self) -> bool:
        """Derive paper vs live from authoritative OpenAlgo fields."""
        venue = self.execution_venue.strip().lower()
        if self.market_region == "IN" and venue in ("sandbox", "broker"):
            return venue == "sandbox"
        return self.analyze_mode

    def to_execution_context_summary(self, *, profile_id: str | None = None) -> dict[str, Any]:
        """Compact dict for autonomous status, MCP, and hub UI."""
        summary: dict[str, Any] = {
            "broker": self.data_broker,
            "venue": self.execution_venue,
            "market_region": self.market_region,
            "analyze_mode": self.analyze_mode,
            "paper": self.is_paper_execution(),
            "context_generation": self.context_generation,
            "positions_authority": self.positions_authority,
            "simulator_active": bool(self.simulator.get("active")),
        }
        if profile_id:
            summary["profile_id"] = profile_id
        return summary


def fetch_market_context(*, host: str, api_key: str, timeout: float = 20.0) -> MarketContext:
    """Fetch authoritative market context from OpenAlgo."""
    from trade_integrations.openalgo.rest_client import OpenAlgoRestClient

    client = OpenAlgoRestClient(host=host, api_key=api_key)
    body = client.post("marketcontext", {"apikey": api_key}, timeout=int(timeout))
    if str(body.get("status") or "").lower() != "success":
        message = body.get("message") or body.get("error") or "MarketContext request failed"
        raise MarketContextError(str(message))
    data = body.get("data")
    if not isinstance(data, dict):
        raise MarketContextError("MarketContext response missing data object")
    return MarketContext.from_api_data(data)
