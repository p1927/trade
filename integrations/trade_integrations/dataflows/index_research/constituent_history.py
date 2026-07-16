"""Build constituent research history time series for prediction UI."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from trade_integrations.context.hub import list_company_research_history

logger = logging.getLogger(__name__)


def _sentiment_score(sentiment: dict[str, Any]) -> float | None:
    raw = sentiment.get("score")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass

    scores = sentiment.get("scores") or []
    if scores:
        total = 0.0
        count = 0
        for row in scores:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "neutral").lower()
            conf = float(row.get("score") or 0.5)
            if label == "positive":
                total += conf
            elif label == "negative":
                total -= conf
            count += 1
        if count:
            return max(-1.0, min(1.0, total / count))

    summary = sentiment.get("summary")
    if isinstance(summary, dict):
        pos = float(summary.get("positive_pct") or 0)
        neg = float(summary.get("negative_pct") or 0)
        if pos or neg:
            return max(-1.0, min(1.0, (pos - neg) / 100.0))
    return None


def _fetch_price_return_series(symbol: str, *, days: int) -> list[dict[str, Any]]:
    """Supplement sparse research history with yfinance close returns."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    yf_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 5)
    try:
        hist = yf.Ticker(yf_symbol).history(start=start.date(), end=end.date(), auto_adjust=True)
    except Exception as exc:
        logger.debug("price history failed for %s: %s", symbol, exc)
        return []

    if hist is None or hist.empty:
        return []

    close_col = "Close" if "Close" in hist.columns else "close"
    closes = hist[close_col].astype(float)
    closes.index = closes.index.tz_localize(None) if hasattr(closes.index, "tz") else closes.index

    points: list[dict[str, Any]] = []
    for ts, close in closes.items():
        day = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        loc = closes.index.get_loc(ts)
        idx = int(loc) if isinstance(loc, (int, float)) else int(loc.start)
        ret_7d = None
        if idx >= 7:
            base = float(closes.iloc[idx - 7])
            if base > 0:
                ret_7d = (float(close) / base - 1.0) * 100.0
        points.append({"date": day, "close": float(close), "return_7d_pct": ret_7d})
    return points


def build_constituent_history_series(
    symbol: str,
    *,
    days: int = 90,
    weight: float | None = None,
) -> dict[str, Any]:
    """Merge archived company research with optional price trend supplement."""
    key = symbol.strip().upper()
    snapshots = list_company_research_history(key, days=days)
    points: list[dict[str, Any]] = []

    for snap in snapshots:
        sentiment_raw = snap.get("sentiment") if isinstance(snap.get("sentiment"), dict) else {}
        score = _sentiment_score(sentiment_raw)
        contribution = None
        if score is not None and weight is not None and weight > 0:
            contribution = round(weight * score * 2.0, 4)
        points.append(
            {
                "date": snap["date"],
                "sentiment_score": score,
                "contribution_proxy_pct": contribution,
                "source": "company_research_archive",
                "headline_count": len((snap.get("news") or {}).get("headlines") or []),
            }
        )

    price_points = _fetch_price_return_series(key, days=days)
    price_by_date = {row["date"]: row for row in price_points}
    archive_by_date = {point["date"]: point for point in points}

    merged_dates = sorted(set(price_by_date) | set(archive_by_date))
    if not merged_dates and points:
        merged_dates = [point["date"] for point in points]

    merged: list[dict[str, Any]] = []
    for day in merged_dates[-days:]:
        archive = archive_by_date.get(day)
        price_row = price_by_date.get(day)
        score = archive.get("sentiment_score") if archive else None
        if score is None and price_row and price_row.get("return_7d_pct") is not None:
            score = max(-1.0, min(1.0, float(price_row["return_7d_pct"]) / 10.0))
        contribution = None
        if score is not None and weight is not None and weight > 0:
            contribution = round(weight * score * 2.0, 4)
        merged.append(
            {
                "date": day,
                "sentiment_score": score,
                "contribution_proxy_pct": contribution,
                "return_7d_pct": price_row.get("return_7d_pct") if price_row else None,
                "close": price_row.get("close") if price_row else None,
                "source": archive.get("source") if archive else "price_proxy",
                "headline_count": archive.get("headline_count") if archive else None,
            }
        )

    return {
        "symbol": key,
        "days": days,
        "snapshot_count": len(snapshots),
        "points": merged,
        "has_research_archive": len(snapshots) > 0,
        "weight": weight,
    }
