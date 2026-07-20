"""Pre-warm OpenAlgo quotes for autonomous agents (watch latency)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def prewarm_agent_quotes(*, symbols: list[str]) -> dict[str, Any]:
    """Best-effort multiquote + WS subscription before first watch tick."""
    normalized = [str(s or "").strip().upper() for s in symbols if str(s or "").strip()]
    if not normalized:
        return {"status": "skipped", "reason": "no_symbols"}

    try:
        from nautilus_openalgo_bridge.instruments import multiquote_requests
        from trade_integrations.openalgo.market_data import fetch_multi_quotes_raw
        from trade_integrations.openalgo.ws_client import ensure_ws_feed

        requests = multiquote_requests(normalized)
        if not requests:
            return {"status": "skipped", "reason": "no_requests"}
        try:
            ensure_ws_feed(requests)
        except Exception:
            logger.debug("prewarm WS feed skipped", exc_info=True)
        payload = fetch_multi_quotes_raw(requests)
        count = 0
        if isinstance(payload, dict):
            for key in ("quotes", "data", "results"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    count = len(rows)
                    break
        return {"status": "ok", "symbols": normalized, "quote_rows": count}
    except Exception as exc:
        logger.warning("prewarm_agent_quotes failed: %s", exc)
        return {"status": "error", "error": str(exc)[:200]}
