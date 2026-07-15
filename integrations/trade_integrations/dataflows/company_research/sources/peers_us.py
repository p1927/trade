"""US market peers — finvizfinance with yfinance sector fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..config import get_research_config
from ..market import NormalizedTicker
from ..models import StageResult
from .resilience import SourceAttempt, remediation_for, run_sources, stage_status_from_attempts

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_peer(raw: Any, *, source: str) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"symbol": raw.upper(), "name": raw, "sector": "", "source": source}
    if isinstance(raw, dict):
        symbol = raw.get("symbol") or raw.get("ticker") or raw.get("Ticker") or ""
        return {
            "symbol": str(symbol).upper(),
            "name": raw.get("name") or raw.get("Company") or symbol,
            "sector": raw.get("sector") or raw.get("Sector") or "",
            "source": source,
        }
    return {"symbol": str(raw).upper(), "name": str(raw), "sector": "", "source": source}


def _fetch_finviz_peers(symbol: str, *, max_peers: int) -> dict[str, Any] | None:
    try:
        from finvizfinance.quote import finvizfinance
    except ImportError:
        return None
    try:
        stock = finvizfinance(symbol)
        peers = stock.ticker_peer()
    except Exception as exc:
        logger.info("finviz peers failed for %s: %s", symbol, exc)
        return None
    if peers is None:
        return None
    if hasattr(peers, "to_dict"):
        rows = peers.to_dict("records")
    elif isinstance(peers, list):
        rows = peers
    elif isinstance(peers, dict):
        rows = [{"symbol": k, **(v if isinstance(v, dict) else {})} for k, v in peers.items()]
    else:
        rows = [peers]
    normalized = [_normalize_peer(r, source="finvizfinance") for r in rows]
    normalized = [p for p in normalized if p.get("symbol")][:max_peers]
    if not normalized:
        return None
    return {"peers": normalized, "primary_source": "finvizfinance"}


def _fetch_yfinance_sector(normalized: NormalizedTicker) -> dict[str, Any] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    sector = info.get("sector") or ""
    industry = info.get("industry") or ""
    if not sector and not industry:
        return None
    return {
        "peers": [],
        "sector_context": {"sector": sector, "industry": industry, "source": "yfinance"},
        "primary_source": "yfinance",
    }


def fetch_peers_us(normalized: NormalizedTicker) -> StageResult:
    config = get_research_config()
    max_peers = config.max_peers
    symbol = normalized.base_symbol
    fetchers = [
        ("finvizfinance", lambda: _fetch_finviz_peers(symbol, max_peers=max_peers)),
        ("yfinance", lambda: _fetch_yfinance_sector(normalized)),
    ]
    attempts = run_sources(fetchers)
    peers: list[dict[str, Any]] = []
    sector_context: dict[str, Any] = {}
    primary = ""
    for attempt in attempts:
        if attempt.status != "ok" or not attempt.data:
            continue
        if attempt.data.get("peers"):
            peers = list(attempt.data["peers"])[:max_peers]
            primary = attempt.data.get("primary_source") or attempt.name
            break
        if attempt.data.get("sector_context"):
            sector_context = attempt.data["sector_context"]
            primary = attempt.data.get("primary_source") or attempt.name

    if not peers and not sector_context:
        attempts.append(
            SourceAttempt(
                name="finvizfinance",
                status="skipped",
                error="not_installed",
                remediation=remediation_for("not_installed"),
            )
        )

    has_output = bool(peers) or bool(sector_context)
    status = "ok" if peers else ("partial" if sector_context else stage_status_from_attempts(attempts, has_output=False))
    return StageResult(
        stage="peers",
        status=status,
        vendor=primary or "peers_us",
        fetched_at=_stage_now(),
        data={
            "peers": peers,
            "sector_context": sector_context,
            "source_attempts": [a.to_dict() for a in attempts],
        },
    )
