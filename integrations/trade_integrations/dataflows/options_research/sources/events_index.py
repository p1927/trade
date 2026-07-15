"""Index options macro context — India VIX, FII/DII, trading calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_vix() -> dict[str, Any] | None:
    try:
        from nselib import capital_market

        end = datetime.now().date()
        start = end - timedelta(days=7)
        frame = capital_market.india_vix_data(
            from_date=start.strftime("%d-%m-%Y"),
            to_date=end.strftime("%d-%m-%Y"),
        )
        if frame is None or getattr(frame, "empty", True):
            return None
        latest = frame.iloc[-1].to_dict()
        vix = latest.get("close") or latest.get("CLOSE")
        return {"india_vix": vix, "source": "nselib", "as_of": str(latest.get("date") or "")}
    except Exception as exc:
        logger.debug("nselib VIX unavailable: %s", exc)
    try:
        import yfinance as yf

        info = yf.Ticker("^INDIAVIX").info or {}
        price = info.get("regularMarketPrice") or info.get("previousClose")
        if price is not None:
            return {"india_vix": price, "source": "yfinance", "symbol": "^INDIAVIX"}
    except Exception:
        pass
    return None


def _fetch_fii_dii() -> dict[str, Any] | None:
    try:
        from nselib import capital_market

        end = datetime.now().date()
        start = end - timedelta(days=5)
        frame = capital_market.fii_dii_trading_activity(
            from_date=start.strftime("%d-%m-%Y"),
            to_date=end.strftime("%d-%m-%Y"),
        )
        if frame is None or getattr(frame, "empty", True):
            return None
        latest = frame.iloc[-1].to_dict()
        return {"latest": latest, "source": "nselib"}
    except Exception as exc:
        logger.debug("FII/DII fetch failed: %s", exc)
        return None


def fetch_events_index(*, lookahead_days: int) -> StageResult:
    """Macro events and context for index options (no company calendar)."""
    now = _stage_now()
    events: list[dict[str, Any]] = []
    vix = _fetch_vix()
    fii = _fetch_fii_dii()

    if vix and vix.get("india_vix"):
        level = float(vix["india_vix"])
        regime = "high" if level >= 18 else "moderate" if level >= 13 else "low"
        events.append(
            {
                "date": vix.get("as_of") or now.date().isoformat(),
                "type": "india_vix",
                "description": f"India VIX at {level:.2f} ({regime})",
                "source": vix.get("source", "unknown"),
                "impact_on_price": "neutral",
                "impact_on_vol": regime,
            }
        )

    if fii and fii.get("latest"):
        events.append(
            {
                "date": now.date().isoformat(),
                "type": "fii_dii_flow",
                "description": "Latest FII/DII cash market activity",
                "source": "nselib",
                "impact_on_price": "directional",
                "impact_on_vol": "moderate",
                "detail": fii["latest"],
            }
        )

    events.append(
        {
            "date": "",
            "type": "macro_watch",
            "description": (
                f"Monitor RBI policy, CPI, and global risk over next {lookahead_days} days "
                "(use TradingAgents macro/Polymarket for specifics)"
            ),
            "source": "trade_stack",
            "impact_on_price": "uncertain",
            "impact_on_vol": "elevated",
        }
    )

    return StageResult(
        stage="events",
        status="ok" if vix or fii else "partial",
        vendor="nselib+yfinance",
        fetched_at=now,
        data={"events": events, "vix": vix, "fii_dii": fii, "lookahead_days": lookahead_days},
    )
