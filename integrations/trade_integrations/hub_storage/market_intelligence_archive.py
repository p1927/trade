"""Archive news headlines and derivatives chain rows into hub parquet."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.parquet_io import append_daily_rows

logger = logging.getLogger(__name__)

_NEWS_DAILY_REL = Path("_data") / "news" / "daily"
_DERIVATIVES_DAILY_REL = Path("_data") / "derivatives_chain" / "daily"


def _daily_path(base: Path, day: str) -> Path:
    return base / f"{day}.parquet"


def _hub_symbols() -> list[Path]:
    hub = get_hub_dir()
    if not hub.is_dir():
        return []
    return [
        path
        for path in sorted(hub.iterdir())
        if path.is_dir() and not path.name.startswith("_")
    ]


def _flatten_news_rows(symbol: str, payload: dict[str, Any], captured_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    news = payload.get("news") or {}
    if isinstance(news, dict):
        for block in news.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            ticker = str(block.get("ticker") or symbol).upper()
            for headline in block.get("headlines") or []:
                if not isinstance(headline, dict):
                    continue
                title = str(headline.get("title") or "").strip()
                if not title:
                    continue
                rows.append(
                    {
                        "captured_at": captured_at,
                        "symbol": ticker,
                        "title": title[:500],
                        "summary": str(headline.get("summary") or "")[:1000],
                        "url": str(headline.get("url") or headline.get("link") or "")[:500],
                        "source": block.get("source") or news.get("source") or "news_aggregator",
                        "label": block.get("label"),
                    }
                )
        for headline in news.get("headlines") or []:
            if isinstance(headline, dict) and headline.get("title"):
                rows.append(
                    {
                        "captured_at": captured_at,
                        "symbol": symbol.upper(),
                        "title": str(headline["title"])[:500],
                        "summary": str(headline.get("summary") or "")[:1000],
                        "url": str(headline.get("url") or headline.get("link") or "")[:500],
                        "source": news.get("source") or "news_aggregator",
                        "label": None,
                    }
                )
    return rows


def _flatten_derivatives_rows(symbol: str, payload: dict[str, Any], captured_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    chain_snapshot = payload.get("chain_snapshot") or {}
    if not isinstance(chain_snapshot, dict):
        return rows
    underlying = str(chain_snapshot.get("underlying") or payload.get("underlying") or symbol).upper()
    expiry = chain_snapshot.get("expiry_date") or payload.get("expiry")
    spot = chain_snapshot.get("underlying_ltp") or payload.get("spot")
    for leg in chain_snapshot.get("chain") or []:
        if not isinstance(leg, dict):
            continue
        strike = leg.get("strike") or leg.get("strike_price")
        if strike is None:
            continue
        for opt_type in ("CE", "PE"):
            nested = leg.get(opt_type.lower()) or leg.get(opt_type)
            if isinstance(nested, dict):
                ltp = nested.get("ltp") or nested.get("price")
                oi = nested.get("oi") or nested.get("open_interest")
                iv = nested.get("iv") or nested.get("implied_volatility")
                volume = nested.get("volume")
                opt_symbol = nested.get("symbol")
            else:
                prefix = opt_type.lower()
                ltp = leg.get(f"{prefix}_ltp") or leg.get(f"{prefix}_price")
                oi = leg.get(f"{prefix}_oi") or leg.get(f"{prefix}_OI")
                iv = leg.get(f"{prefix}_iv") or leg.get(f"{prefix}_IV")
                volume = leg.get(f"{prefix}_volume")
                opt_symbol = leg.get(f"{prefix}_symbol")
            if ltp is None and oi is None:
                continue
            rows.append(
                {
                    "captured_at": captured_at,
                    "underlying": underlying,
                    "expiry": expiry,
                    "spot": spot,
                    "strike": strike,
                    "option_type": opt_type,
                    "ltp": ltp,
                    "oi": oi,
                    "iv": iv,
                    "volume": volume,
                    "symbol": opt_symbol,
                }
            )
    return rows


def _append_daily_rows(base: Path, day: str, rows: list[dict[str, Any]], dedupe_keys: list[str]) -> int:
    if not rows:
        return 0
    dest = _daily_path(base, day)
    return append_daily_rows(dest, rows, dedupe_keys=dedupe_keys or None)


def archive_market_intelligence(*, as_of_date: str | None = None) -> dict[str, Any]:
    """Extract news + derivatives chain datapoints from hub latest.json into daily parquet."""
    day = as_of_date or datetime.now(timezone.utc).date().isoformat()
    captured_at = datetime.now(timezone.utc).isoformat()
    news_rows: list[dict[str, Any]] = []
    deriv_rows: list[dict[str, Any]] = []
    symbols_scanned = 0

    for symbol_dir in _hub_symbols():
        symbols_scanned += 1
        sym = symbol_dir.name
        company_latest = symbol_dir / "company_research" / "latest.json"
        if company_latest.is_file():
            try:
                payload = json.loads(company_latest.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    news_rows.extend(_flatten_news_rows(sym, payload, captured_at))
            except (OSError, json.JSONDecodeError):
                logger.debug("skip company news for %s", sym, exc_info=True)

        options_latest = symbol_dir / "options_research" / "latest.json"
        if options_latest.is_file():
            try:
                payload = json.loads(options_latest.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    deriv_rows.extend(_flatten_derivatives_rows(sym, payload, captured_at))
            except (OSError, json.JSONDecodeError):
                logger.debug("skip options chain for %s", sym, exc_info=True)

    news_base = get_hub_dir() / _NEWS_DAILY_REL
    deriv_base = get_hub_dir() / _DERIVATIVES_DAILY_REL
    news_added = _append_daily_rows(
        news_base,
        day,
        news_rows,
        dedupe_keys=["symbol", "title", "captured_at"],
    )
    deriv_added = _append_daily_rows(
        deriv_base,
        day,
        deriv_rows,
        dedupe_keys=["underlying", "expiry", "strike", "option_type", "captured_at"],
    )
    return {
        "date": day,
        "symbols_scanned": symbols_scanned,
        "news_rows_added": news_added,
        "derivatives_rows_added": deriv_added,
        "news_path": str(_daily_path(news_base, day)),
        "derivatives_path": str(_daily_path(deriv_base, day)),
    }
