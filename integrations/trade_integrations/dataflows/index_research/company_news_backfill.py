"""Retroactive company news archive for Nifty constituents via Google News RSS."""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

logger = logging.getLogger(__name__)

_POSITIVE = frozenset({"beat", "growth", "profit", "surge", "upgrade", "record", "strong", "gain"})
_NEGATIVE = frozenset({"miss", "loss", "fall", "drop", "downgrade", "weak", "decline", "cut", "fraud", "probe"})


def _company_research_history_path(symbol: str, day: str) -> Path:
    return get_hub_dir() / symbol.strip().upper() / "company_research" / "history" / f"{day}.json"


def _next_day(day: str) -> str:
    d = date.fromisoformat(day[:10])
    return (d + timedelta(days=1)).isoformat()


def _google_news_rss_url(query: str, *, after: str, before: str) -> str:
    q = f"{query} after:{after} before:{before}"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    )


def _fetch_rss_headlines(url: str, *, limit: int = 8) -> list[dict[str, str]]:
    try:
        import requests

        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        logger.debug("RSS fetch failed %s: %s", url[:80], exc)
        return []

    rows: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = (item.findtext("pubDate") or "").strip()
        source = (item.findtext("source") or "google_news").strip()
        rows.append({"title": title[:500], "published": pub[:80], "source": source[:80]})
        if len(rows) >= limit:
            break
    return rows


def _headline_sentiment(headlines: list[dict[str, str]]) -> float:
    if not headlines:
        return 0.0
    score = 0.0
    for row in headlines:
        text = str(row.get("title") or "").lower()
        tokens = set(re.findall(r"[a-z]{3,}", text))
        pos = len(tokens & _POSITIVE)
        neg = len(tokens & _NEGATIVE)
        if pos > neg:
            score += 0.25
        elif neg > pos:
            score -= 0.25
    return float(max(-1.0, min(1.0, score / max(1, len(headlines)))))


def _minimal_archive_doc(symbol: str, day: str, headlines: list[dict[str, str]]) -> dict[str, Any]:
    sentiment = _headline_sentiment(headlines)
    return {
        "ticker": symbol,
        "as_of": f"{day}T18:00:00+00:00",
        "lookahead_days": 14,
        "market": "IN",
        "news": {
            "headlines": headlines,
            "source": "google_news_rss_backfill",
            "lookback_days": 1,
        },
        "sentiment": {
            "score": sentiment,
            "source": "headline_lexicon_proxy",
            "summary": f"{len(headlines)} headlines on {day}",
        },
        "backfill": True,
    }


def backfill_constituent_news_day(symbol: str, day: str, *, overwrite: bool = False) -> bool:
    """Fetch one day of headlines and save to company_research/history/{day}.json."""
    key = symbol.strip().upper()
    path = _company_research_history_path(key, day[:10])
    if path.is_file() and not overwrite:
        return False

    query = f"{key} NSE stock India"
    url = _google_news_rss_url(query, after=day[:10], before=_next_day(day[:10]))
    headlines = _fetch_rss_headlines(url)
    if not headlines:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_minimal_archive_doc(key, day[:10], headlines), indent=2), encoding="utf-8")
    return True


def backfill_drawdown_news(
    *,
    ticker: str = "NIFTY",
    sleep_seconds: float = 0.25,
    overwrite: bool = False,
) -> dict[str, int | str]:
    """Backfill news archives for major drawdown dates × all Nifty constituents."""
    from trade_integrations.dataflows.index_research.backtest_runner import load_backtest_report

    report = load_backtest_report(ticker) or {}
    drawdowns = report.get("major_drawdowns") or []
    dates = sorted({str(row.get("date") or "")[:10] for row in drawdowns if row.get("date")})
    if not dates:
        return {"status": "error", "reason": "no_drawdown_dates", "hint": "run backtest first"}

    constituents = [row.symbol for row in load_nifty50_constituents()]
    written = 0
    skipped = 0
    errors = 0
    for sym in constituents:
        for day in dates:
            try:
                if backfill_constituent_news_day(sym, day, overwrite=overwrite):
                    written += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.debug("drawdown news %s %s: %s", sym, day, exc)
                errors += 1
            time.sleep(sleep_seconds)

    return {
        "status": "ok",
        "drawdown_dates": len(dates),
        "symbols": len(constituents),
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "dates": dates,
    }


def backfill_nifty_constituent_news(
    *,
    days: int = 180,
    symbols: list[str] | None = None,
    sleep_seconds: float = 0.35,
    overwrite: bool = False,
) -> dict[str, int | str]:
    """Backfill Google News RSS snapshots for Nifty constituents over trading days."""
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history"}

    trading_days = nifty["date"].astype(str).tolist()
    constituents = symbols or [row.symbol for row in load_nifty50_constituents()]
    if not constituents:
        return {"status": "error", "reason": "no_constituents"}

    written = 0
    skipped = 0
    errors = 0
    for sym in constituents:
        for day in trading_days:
            try:
                if backfill_constituent_news_day(sym, day, overwrite=overwrite):
                    written += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.debug("news backfill %s %s: %s", sym, day, exc)
                errors += 1
            time.sleep(sleep_seconds)

    return {
        "status": "ok",
        "symbols": len(constituents),
        "trading_days": len(trading_days),
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "start": trading_days[0],
        "end": trading_days[-1],
    }
