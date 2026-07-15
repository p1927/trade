"""India market peers — Tapetide profile peers with yfinance sector fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..config import get_research_config
from ..market import NormalizedTicker
from ..models import StageResult
from .resilience import (
    SourceAttempt,
    remediation_for,
    run_sources,
    stage_status_from_attempts,
)

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_peer(raw: dict[str, Any]) -> dict[str, Any]:
    symbol = (
        raw.get("symbol")
        or raw.get("ticker")
        or raw.get("nse_symbol")
        or raw.get("name")
        or ""
    )
    return {
        "symbol": str(symbol).upper().strip(),
        "name": raw.get("name") or raw.get("company_name") or symbol,
        "sector": raw.get("sector") or "",
        "market_cap": raw.get("market_cap"),
        "source": raw.get("source") or "unknown",
    }


def _fetch_tapetide_peers(symbol: str, *, max_peers: int) -> dict[str, Any] | None:
    from trade_integrations.clients.tapetide import get_company_profile

    profile = get_company_profile(symbol, include_peers=True)
    peers_raw = profile.get("peers") or profile.get("peer_list") or []
    if not isinstance(peers_raw, list):
        return None
    peers = [_normalize_peer({**p, "source": "tapetide"}) for p in peers_raw if isinstance(p, dict)]
    peers = [p for p in peers if p.get("symbol")][:max_peers]
    if not peers:
        return None
    return {"peers": peers, "primary_source": "tapetide"}


def _fetch_yfinance_peers(normalized: NormalizedTicker, *, max_peers: int) -> dict[str, Any] | None:
    import yfinance as yf

    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    sector = info.get("sector") or ""
    industry = info.get("industry") or ""
    # yfinance has no native NSE peer list; surface sector context for agents.
    if not sector and not industry:
        return None
    return {
        "peers": [],
        "sector_context": {"sector": sector, "industry": industry, "source": "yfinance"},
        "primary_source": "yfinance",
        "note": "No peer list from yfinance; sector/industry only. Set TAPETIDE_TOKEN for NSE peers.",
    }


def _fetch_nselib_peers(symbol: str, *, industry: str, max_peers: int) -> dict[str, Any] | None:
    if not industry:
        return None
    try:
        from nselib import capital_market
    except ImportError:
        return None
    try:
        frame = capital_market.nifty50_equity_list()
    except Exception as exc:
        logger.info("nselib nifty50 failed: %s", exc)
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    industry_col = "Industry" if "Industry" in frame.columns else None
    symbol_col = "Symbol" if "Symbol" in frame.columns else None
    name_col = "Company Name" if "Company Name" in frame.columns else None
    if not industry_col or not symbol_col:
        return None
    needle = industry.strip().lower()
    subset = frame[
        frame[industry_col].astype(str).str.lower().str.contains(needle.split()[0], na=False)
    ]
    peers = []
    for _, row in subset.iterrows():
        sym = str(row[symbol_col]).upper()
        if sym == symbol.upper():
            continue
        peers.append(
            {
                "symbol": sym,
                "name": str(row[name_col]) if name_col else sym,
                "sector": str(row[industry_col]),
                "source": "nselib:nifty50",
            }
        )
        if len(peers) >= max_peers:
            break
    if not peers:
        return None
    return {"peers": peers, "primary_source": "nselib"}


def fetch_peers_in(normalized: NormalizedTicker, *, industry_hint: str = "") -> StageResult:
    """Return sector peers for an India equity ticker."""
    config = get_research_config()
    max_peers = config.max_peers
    fetchers: list[tuple[str, Any]] = [
        ("yfinance", lambda: _fetch_yfinance_peers(normalized, max_peers=max_peers)),
    ]

    from trade_integrations.clients.tapetide import is_configured as tapetide_configured

    if tapetide_configured():
        fetchers.insert(
            0,
            ("tapetide", lambda: _fetch_tapetide_peers(normalized.base_symbol, max_peers=max_peers)),
        )

    industry = industry_hint
    if industry:
        fetchers.insert(
            1 if tapetide_configured() else 0,
            (
                "nselib",
                lambda: _fetch_nselib_peers(
                    normalized.base_symbol, industry=industry, max_peers=max_peers
                ),
            ),
        )

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

    if not peers and not sector_context:
        if not tapetide_configured():
            attempts.append(
                SourceAttempt(
                    name="tapetide",
                    status="skipped",
                    error="tapetide_not_configured",
                    remediation=remediation_for("tapetide_not_configured"),
                )
            )

    has_output = bool(peers) or bool(sector_context)
    status = stage_status_from_attempts(attempts, has_output=has_output)
    if peers:
        status = "ok"
    elif sector_context:
        status = "partial"

    return StageResult(
        stage="peers",
        status=status,
        vendor=primary or "trade_integrations.peers_in",
        fetched_at=_stage_now(),
        data={
            "peers": peers,
            "sector_context": sector_context,
            "source_attempts": [a.to_dict() for a in attempts],
        },
    )
