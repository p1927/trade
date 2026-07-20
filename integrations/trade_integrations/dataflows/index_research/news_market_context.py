"""OpenAlgo index bundle + hub factor snapshot for news ingest / distillation."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS

logger = logging.getLogger(__name__)

_CONTEXT_REL = Path("_data") / "news_ingest" / "last_market_context.json"
_INDEX_QUOTE_SYMBOLS = ("NIFTY", "BANKNIFTY", "INDIAVIX")
_FACTOR_KEYS = (
    "india_vix",
    "fii_net_5d",
    "dii_net_5d",
    "usd_inr",
    "oil_brent",
    "nifty_pcr",
    "nifty_return_7d",
    "sp500",
    "us_10y",
)


def _context_path() -> Path:
    path = get_hub_dir() / _CONTEXT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_index_quotes() -> dict[str, dict[str, Any]]:
    from trade_integrations.dataflows.openalgo import fetch_openalgo_live_snapshot

    quotes: dict[str, dict[str, Any]] = {}
    for symbol in _INDEX_QUOTE_SYMBOLS:
        try:
            snap = fetch_openalgo_live_snapshot(symbol)
        except Exception as exc:
            logger.debug("OpenAlgo quote failed for %s: %s", symbol, exc)
            snap = None
        if not snap or snap.get("ltp") is None:
            continue
        quotes[symbol] = {
            "ltp": round(float(snap["ltp"]), 4),
            "change_pct": snap.get("change_pct"),
            "source": str(snap.get("source") or "openalgo"),
        }
    return quotes


def _fetch_factor_snapshot(*, ticker: str = "NIFTY") -> dict[str, float]:
    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            factor_snapshot_at,
        )
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame, feature_cols = load_aligned_factor_history(ticker=ticker.strip().upper())
        if frame is None or frame.empty:
            return {}
        today = datetime.now(timezone.utc).date().isoformat()
        keys = tuple(k for k in _FACTOR_KEYS if k in MACRO_FACTOR_KEYS)
        snap = factor_snapshot_at(today, frame, feature_cols, keys=keys)
        if not snap and "date" in frame.columns:
            last_day = str(frame["date"].iloc[-1])[:10]
            snap = factor_snapshot_at(last_day, frame, feature_cols, keys=keys)
        return {k: round(float(v), 4) for k, v in snap.items() if v is not None}
    except Exception as exc:
        logger.debug("hub factor snapshot failed: %s", exc)
        return {}


def refresh_index_market_context(
    *,
    ticker: str = "NIFTY",
    persist: bool = True,
) -> dict[str, Any]:
    """Build OpenAlgo index bundle + cached hub factors; optionally persist."""
    quotes = _fetch_index_quotes()
    factors = _fetch_factor_snapshot(ticker=ticker)
    bundle: dict[str, Any] = {
        "as_of": _now_iso(),
        "ticker": ticker.strip().upper(),
        "quotes": quotes,
        "factors": factors,
        "source": "openalgo+hub_factors",
        "quote_symbols_requested": list(_INDEX_QUOTE_SYMBOLS),
        "quotes_ok": len(quotes),
        "factors_ok": len(factors),
    }
    if persist:
        _context_path().write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return bundle


def load_last_market_context() -> dict[str, Any] | None:
    path = _context_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def market_context_max_age_hours() -> float:
    try:
        return max(0.5, float(os.getenv("HUB_NEWS_MARKET_CONTEXT_MAX_AGE_H", "6")))
    except ValueError:
        return 6.0


def _context_age_hours(bundle: dict[str, Any]) -> float | None:
    raw = str(bundle.get("as_of") or "").strip()
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def get_market_context_for_pipeline(
    *,
    ticker: str = "NIFTY",
    refresh: bool = False,
) -> dict[str, Any]:
    """Return cached market bundle; refresh when stale or explicitly requested."""
    if refresh:
        return refresh_index_market_context(ticker=ticker, persist=True)
    cached = load_last_market_context()
    if cached:
        age = _context_age_hours(cached)
        if age is not None and age <= market_context_max_age_hours():
            return cached
    return refresh_index_market_context(ticker=ticker, persist=True)


def format_market_context_for_prompt(bundle: dict[str, Any] | None) -> str:
    """Compact tape summary for MiniMax prompts."""
    if not bundle:
        return "Market context unavailable."
    lines: list[str] = []
    quotes = bundle.get("quotes") if isinstance(bundle.get("quotes"), dict) else {}
    for sym in _INDEX_QUOTE_SYMBOLS:
        q = quotes.get(sym) if isinstance(quotes, dict) else None
        if not isinstance(q, dict) or q.get("ltp") is None:
            continue
        pct = q.get("change_pct")
        pct_s = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
        lines.append(f"{sym}: {q['ltp']}{pct_s}")
    factors = bundle.get("factors") if isinstance(bundle.get("factors"), dict) else {}
    if factors:
        factor_bits = [f"{k}={v}" for k, v in list(factors.items())[:8]]
        lines.append("Factors: " + ", ".join(factor_bits))
    if not lines:
        return "Market context unavailable (OpenAlgo/factors empty)."
    return "Market tape at ingest: " + "; ".join(lines)
