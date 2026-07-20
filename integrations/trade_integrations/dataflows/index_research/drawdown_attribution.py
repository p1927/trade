"""Constituent-level attribution for Nifty drawdown days."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir, list_company_research_history
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

logger = logging.getLogger(__name__)


def _load_history_headlines(symbol: str, day: str) -> list[dict[str, str]]:
    path = get_hub_dir() / symbol.strip().upper() / "company_research" / "history" / f"{day[:10]}.json"
    if not path.is_file():
        snapshots = list_company_research_history(symbol, days=400)
        for snap in snapshots:
            if str(snap.get("date") or "")[:10] == day[:10]:
                news = snap.get("news") or {}
                if isinstance(news, dict):
                    headlines = news.get("headlines") or []
                    return [
                        {
                            "title": str(h.get("title") or h.get("headline") or "")[:200],
                            "source": str(h.get("source") or "")[:60],
                        }
                        for h in headlines[:3]
                        if isinstance(h, dict) and (h.get("title") or h.get("headline"))
                    ]
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    news = payload.get("news") or {}
    headlines = news.get("headlines") or []
    return [
        {
            "title": str(h.get("title") or h.get("headline") or "")[:200],
            "source": str(h.get("source") or "")[:60],
        }
        for h in headlines[:3]
        if isinstance(h, dict) and (h.get("title") or h.get("headline"))
    ]


def _fetch_constituent_returns(
    day: str,
    *,
    lookback_days: int = 10,
    constituents: list | None = None,
) -> dict[str, float]:
    """1d return % per constituent ending on ``day``."""
    from trade_integrations.dataflows import source_availability
    from trade_integrations.dataflows.company_research.sources.resilience import classify_error

    try:
        import yfinance as yf
    except ImportError:
        return {}

    if not source_availability.should_attempt("yfinance", "history"):
        return {}

    end = datetime.strptime(day[:10], "%Y-%m-%d").date() + timedelta(days=2)
    start = end - timedelta(days=lookback_days + 5)
    returns: dict[str, float] = {}
    rows = constituents if constituents is not None else load_nifty50_constituents()
    for row in rows:
        sym = row.symbol.strip().upper()
        yf_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
        try:
            hist = yf.Ticker(yf_sym).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
        except Exception as exc:
            if classify_error(exc) == "vendor_rate_limited":
                source_availability.record_failure("yfinance", "history", exc)
            continue
        if hist is None or hist.empty or len(hist) < 2:
            continue
        close_col = "Close" if "Close" in hist.columns else "close"
        closes = hist[close_col].astype(float)
        closes.index = closes.index.tz_localize(None) if hasattr(closes.index, "tz") else closes.index
        day_ts = datetime.strptime(day[:10], "%Y-%m-%d")
        eligible = closes.index[closes.index <= day_ts]
        if len(eligible) < 2:
            continue
        today = float(closes.loc[eligible[-1]])
        prev = float(closes.loc[eligible[-2]])
        if prev <= 0:
            continue
        returns[sym] = (today - prev) / prev * 100.0
    return returns


def enrich_drawdown_with_constituents(drawdown: dict[str, Any]) -> dict[str, Any]:
    """Attach top constituent movers and headlines for a drawdown row."""
    day = str(drawdown.get("date") or "")[:10]
    if not day:
        return drawdown

    constituents = load_nifty50_constituents()
    rets = _fetch_constituent_returns(day, constituents=constituents)
    if not rets:
        return drawdown

    weight_map = {row.symbol.strip().upper(): float(row.weight) for row in constituents}
    ranked: list[dict[str, Any]] = []
    for sym, ret_1d in rets.items():
        weight = weight_map.get(sym, 0.0)
        ranked.append(
            {
                "symbol": sym,
                "weight_pct": round(weight * 100, 2),
                "return_1d_pct": round(ret_1d, 3),
                "index_contribution_pct": round(weight * ret_1d, 4),
                "headlines": _load_history_headlines(sym, day),
            }
        )
    ranked.sort(key=lambda r: r["index_contribution_pct"])
    drawdown["constituent_movers"] = ranked[:8]
    drawdown["worst_contributors"] = ranked[:5]
    drawdown["best_contributors"] = list(reversed(ranked[-5:]))

    from trade_integrations.dataflows.index_research.causal_attribution import (
        _fetch_index_headlines,
        build_causal_hypotheses,
    )

    index_headlines = _fetch_index_headlines(day, limit=4)
    const_headlines = [
        {**h, "symbol": m["symbol"]}
        for m in ranked[:5]
        for h in (m.get("headlines") or [])
    ]
    drawdown["index_headlines"] = index_headlines
    drawdown["causal_hypotheses"] = build_causal_hypotheses(
        factor_drivers=drawdown.get("factor_drivers") or [],
        realized_1d_pct=float(drawdown.get("realized_1d_pct") or 0),
        calendar_events=drawdown.get("calendar_events") or [],
        index_headlines=index_headlines,
        constituent_headlines=const_headlines,
    )
    return drawdown


def enrich_drawdowns(drawdowns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_drawdown_with_constituents(dict(row)) for row in drawdowns]
