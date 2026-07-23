"""Link factor moves and headlines to human-readable *causes* for Nifty day moves."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Directional copy when a factor moved sharply (|d/d| >= threshold in builder).
_FACTOR_CAUSE_COPY: dict[str, dict[str, str]] = {
    "oil_brent": {
        "category": "commodity",
        "up": "Crude oil prices rose — often driven by Middle East tensions, OPEC supply cuts, US inventory draws, or global demand surprises.",
        "down": "Crude eased — softer demand outlook, higher supply, or easing geopolitical premium on oil.",
    },
    "oil_wti": {
        "category": "commodity",
        "up": "WTI crude moved higher — watch US inventory data and Middle East headlines for the trigger.",
        "down": "WTI crude softened — typically relief on supply or growth concerns.",
    },
    "usd_inr": {
        "category": "fx",
        "up": "Rupee weakened (USD/INR up) — raises import costs (oil, electronics) and can trigger FII outflows.",
        "down": "Rupee strengthened — supportive for inflation and foreign investor returns in INR terms.",
    },
    "india_vix": {
        "category": "risk",
        "up": "India VIX spiked — traders priced higher near-term fear (expiry, global risk-off, or domestic shock).",
        "down": "VIX fell — risk appetite improved; hedging demand eased.",
    },
    "sp500": {
        "category": "global",
        "up": "US equities rallied — global risk-on often lifts Nifty via FII flows and sentiment.",
        "down": "US equities sold off — global risk-off; FII selling and weaker Asia open frequently follow.",
    },
    "us_10y": {
        "category": "rates",
        "up": "US 10Y yields rose — tighter global financial conditions; growth stocks and EM flows often pressured.",
        "down": "US yields fell — easier global liquidity; supportive for EM equities including India.",
    },
    "fii_net_5d": {
        "category": "flows",
        "up": "FII net buying accelerated (5d) — foreign institutions added India exposure; often follows global risk-on.",
        "down": "FII net selling (5d) — foreign investors pulled money out; a common driver of Nifty dips when sustained.",
    },
    "dii_net_5d": {
        "category": "flows",
        "up": "DII net buying (5d) — domestic institutions absorbed supply; can cushion FII outflows.",
        "down": "DII net selling (5d) — domestic institutions reduced equity exposure alongside or instead of FIIs.",
    },
    "fii_fut_long_short_ratio": {
        "category": "flows",
        "up": "FII index futures long/short ratio rose — institutions added bullish positioning on Nifty.",
        "down": "FII futures ratio fell — cut longs or added shorts into index futures.",
    },
    "nifty_pcr": {
        "category": "derivatives",
        "up": "Put-call ratio rose — more put writing/buying vs calls; often hedging or bearish positioning.",
        "down": "PCR fell — call side dominant; bullish positioning or put unwinding.",
    },
    "gold": {
        "category": "commodity",
        "up": "Gold rallied — safe-haven bid; often coincides with equity stress or USD/rate uncertainty.",
        "down": "Gold eased — risk-on or stronger USD headwind to bullion.",
    },
    "index_sentiment": {
        "category": "sentiment",
        "up": "Aggregate index sentiment improved — positive news flow across Nifty constituents.",
        "down": "Index sentiment deteriorated — negative headlines dominated large-cap news.",
    },
    "repo_rate": {
        "category": "policy",
        "up": "Repo rate level moved up — tighter RBI stance or hike expectations pressure rate-sensitive stocks.",
        "down": "Easier policy signal — supports financials and rate-sensitive sectors.",
    },
    "nifty_return_7d": {
        "category": "technical",
        "up": "Short-term momentum was already positive — trend-following flows may have extended the move.",
        "down": "Recent momentum was negative — mean-reversion or stop-loss cascades may have amplified the drop.",
    },
    "constituent_momentum_7d": {
        "category": "breadth",
        "up": "Weighted constituent momentum was positive — broad participation in the prior week.",
        "down": "Constituent momentum was weak — narrow or fragile rally before the move.",
    },
}

_NEWS_KEYWORDS: dict[str, list[str]] = {
    "war": ["war", "conflict", "missile", "invasion", "geopolit"],
    "oil": ["oil", "crude", "opec", "brent", "wti", "petroleum"],
    "fii": ["fii", "foreign fund", "foreign investor", "outflow", "inflow"],
    "rbi": ["rbi", "repo", "mpc", "rate hike", "rate cut", "monetary"],
    "us": ["fed", "wall street", "s&p", "nasdaq", "treasury", "us market"],
    "earnings": ["results", "earnings", "quarter", "profit", "guidance"],
}


def _fetch_index_headlines(
    day: str,
    *,
    limit: int = 6,
    as_of_day: str | None = None,
    lookback_days: int = 0,
) -> list[dict[str, Any]]:
    """Headlines for Nifty / Indian market on a calendar day (hub SSOT with tags)."""
    from trade_integrations.dataflows.news_hub_bridge import (
        headlines_for_day,
        headlines_for_prediction_date,
        ingest_rows_to_hub,
        to_headline_dict,
    )

    as_of = (as_of_day or day)[:10]
    try:
        if lookback_days > 0:
            rows = headlines_for_prediction_date(
                as_of,
                ticker="NIFTY",
                lookback_days=lookback_days,
                limit=limit,
                ingest_if_missing=True,
            )
        else:
            rows = headlines_for_day(day, ticker="NIFTY", limit=limit, ingest_if_missing=True)
        if rows:
            return [to_headline_dict(r) for r in rows]
    except Exception as exc:
        logger.debug("hub headlines_for_day failed for %s: %s", day, exc)

    try:
        from trade_integrations.dataflows.hub_wiki.probe import llm_wiki_required_for_hub_news, check_ingest_allowed

        if llm_wiki_required_for_hub_news() and check_ingest_allowed().get("blocked"):
            return []
    except Exception:
        pass

    try:
        from trade_integrations.dataflows.index_research.company_news_backfill import (
            _fetch_rss_headlines,
            _google_news_rss_url,
        )
    except ImportError:
        return []

    after = day[:10]
    try:
        from datetime import date, timedelta

        before = (date.fromisoformat(after) + timedelta(days=1)).isoformat()
    except ValueError:
        return []

    queries = [
        "Nifty 50 India stock market",
        "India FII DII stock market",
    ]
    seen: set[str] = set()
    raw_rows: list[dict[str, Any]] = []
    for query in queries:
        url = _google_news_rss_url(query, after=after, before=before)
        for row in _fetch_rss_headlines(url, limit=limit):
            title = (row.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            raw_rows.append(
                {
                    "title": title[:220],
                    "source": row.get("source") or "google_news_rss",
                    "summary": "",
                    "url": "",
                    "published_at": f"{after}T09:00:00+00:00",
                }
            )
            if len(raw_rows) >= limit:
                break
        if len(raw_rows) >= limit:
            break

    if raw_rows:
        ingest_rows_to_hub(raw_rows, ticker="NIFTY", collection_day=after)
        return [
            {
                "title": r["title"],
                "source": r["source"],
                "summary": r.get("summary") or "",
                "tags": {},
            }
            for r in raw_rows
        ]
    return []


def _headline_tags(title_or_item: str | dict[str, Any]) -> list[str]:
    if isinstance(title_or_item, dict):
        from trade_integrations.dataflows.index_research.news_tags import legacy_topic_tags_from_item

        tagged = legacy_topic_tags_from_item(title_or_item)
        if tagged:
            return tagged
        title_or_item = str(title_or_item.get("title") or "")
    lower = str(title_or_item).lower()
    return [tag for tag, words in _NEWS_KEYWORDS.items() if any(w in lower for w in words)]


def build_causal_hypotheses(
    *,
    factor_drivers: list[dict[str, Any]],
    realized_1d_pct: float | None,
    calendar_events: list[dict[str, Any]] | None = None,
    index_headlines: list[dict[str, str]] | None = None,
    constituent_headlines: list[dict[str, str]] | None = None,
    move_threshold_pct: float = 3.0,
) -> list[dict[str, Any]]:
    """Ranked likely causes — factor-linked narratives plus news evidence."""
    hypotheses: list[dict[str, Any]] = []
    direction = "down" if (realized_1d_pct or 0) < 0 else "up"

    for driver in factor_drivers[:8]:
        factor = str(driver.get("factor") or "")
        change = float(driver.get("change_pct") or 0.0)
        if abs(change) < move_threshold_pct and factor not in (
            "fii_net_5d",
            "dii_net_5d",
            "india_vix",
            "oil_brent",
        ):
            continue
        copy = _FACTOR_CAUSE_COPY.get(factor)
        if not copy:
            label = driver.get("label") or factor
            hypotheses.append(
                {
                    "title": f"{label} moved {change:+.1f}% d/d",
                    "explanation": (
                        f"{label} shifted from {driver.get('prev')} to {driver.get('current')} "
                        f"({change:+.1f}% day-over-day), contributing to the index move."
                    ),
                    "category": "factor",
                    "confidence": min(0.85, 0.45 + abs(change) / 40),
                    "linked_factors": [factor],
                    "evidence": [],
                }
            )
            continue

        factor_dir = "up" if change > 0 else "down"
        conf = min(0.92, 0.5 + abs(change) / 35)
        hypotheses.append(
            {
                "title": f"{driver.get('label') or factor}: {change:+.1f}% d/d",
                "explanation": copy.get(factor_dir) or copy.get("up") or "",
                "category": copy.get("category") or "factor",
                "confidence": round(conf, 2),
                "linked_factors": [factor],
                "evidence": [
                    f"Level {driver.get('prev')} → {driver.get('current')} ({change:+.1f}% d/d)"
                ],
            }
        )

    for event in calendar_events or []:
        desc = str(event.get("description") or event.get("event") or "")
        if not desc:
            continue
        hypotheses.append(
            {
                "title": desc,
                "explanation": "Scheduled market event overlapped this session — often amplifies volatility.",
                "category": "calendar",
                "confidence": 0.55,
                "linked_factors": [],
                "evidence": [desc],
            }
        )

    for headline in (index_headlines or [])[:4]:
        title = headline.get("title") or ""
        if not title:
            continue
        tags = _headline_tags(title)
        hypotheses.append(
            {
                "title": title[:120],
                "explanation": "Market headline on this date — cross-check with factor moves above.",
                "category": tags[0] if tags else "news",
                "confidence": 0.5 if tags else 0.35,
                "linked_factors": [],
                "evidence": [title],
                "source": headline.get("source"),
            }
        )

    for headline in (constituent_headlines or [])[:3]:
        title = headline.get("title") or ""
        sym = headline.get("symbol") or ""
        if not title:
            continue
        hypotheses.append(
            {
                "title": f"{sym}: {title[:100]}" if sym else title[:120],
                "explanation": "Large-cap constituent news — weighted stocks can drag or lift the index.",
                "category": "company",
                "confidence": 0.45,
                "linked_factors": ["index_sentiment"],
                "evidence": [title],
                "source": headline.get("source"),
            }
        )

    if realized_1d_pct is not None and abs(realized_1d_pct) >= 0.75:
        if direction == "down" and not any(h.get("category") == "flows" for h in hypotheses):
            fii = next((d for d in factor_drivers if d.get("factor") == "fii_net_5d"), None)
            if fii and float(fii.get("change_pct") or 0) < -2:
                pass  # already covered
            elif realized_1d_pct <= -1.0:
                hypotheses.append(
                    {
                        "title": "Risk-off session",
                        "explanation": (
                            "Nifty fell ≥1% in a day — typically a mix of global cues, FII selling, "
                            "and macro shock (rates, oil, or geopolitics). Check flows and crude above."
                        ),
                        "category": "composite",
                        "confidence": 0.4,
                        "linked_factors": ["india_vix", "fii_net_5d", "oil_brent"],
                        "evidence": [f"Nifty 1d {realized_1d_pct:+.2f}%"],
                    }
                )

    hypotheses.sort(key=lambda h: float(h.get("confidence") or 0), reverse=True)
    return hypotheses[:12]


def collect_constituent_headlines_for_day(day: str, *, limit: int = 8) -> list[dict[str, str]]:
    """Headlines from archived constituent research on a date."""
    from trade_integrations.dataflows.index_research.drawdown_attribution import (
        _load_history_headlines,
    )
    from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

    out: list[dict[str, str]] = []
    for row in load_nifty50_constituents()[:20]:
        sym = row.symbol.strip().upper()
        for h in _load_history_headlines(sym, day):
            out.append({**h, "symbol": sym})
            if len(out) >= limit:
                return out
    return out
