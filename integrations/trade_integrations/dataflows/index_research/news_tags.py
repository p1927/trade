"""Structured tags for verified news — filter by date, symbol, topic, factor, theme."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value

# Topic buckets (high-level "what they're talking about")
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "oil": ["oil", "crude", "opec", "brent", "wti", "petroleum", "barrel"],
    "fii": ["fii", "foreign fund", "foreign investor", "portfolio investor", "fpi"],
    "dii": ["dii", "domestic institutional", "mutual fund inflow"],
    "rbi": ["rbi", "repo", "mpc", "monetary policy", "rate hike", "rate cut"],
    "us_markets": ["fed", "federal reserve", "wall street", "s&p", "nasdaq", "dow", "us market"],
    "forex": ["usd", "dollar", "rupee", "forex", "currency", "usd/inr", "dxy"],
    "vix": ["vix", "volatility", "fear gauge", "risk-off", "risk on"],
    "earnings": ["earnings", "results", "quarter", "profit", "guidance", "ebitda"],
    "war": ["war", "conflict", "missile", "invasion", "geopolit", "ceasefire"],
    "gold": ["gold", "bullion", "safe haven"],
    "budget": ["budget", "fiscal", "union budget"],
    "banking": ["bank", "npa", "credit growth", "lending"],
}

# Market-movement / narrative themes
_THEME_KEYWORDS: dict[str, list[str]] = {
    "crash": ["crash", "plunge", "tank", "meltdown", "bloodbath", "capitulation"],
    "selloff": ["sell-off", "selloff", "selling pressure", "heavy selling", "outflow"],
    "rally": ["rally", "surge", "jump", "soar", "spurt", "charge", "bull run"],
    "recovery": ["rebound", "recover", "bounce", "comeback", "recoup"],
    "record_high": ["record high", "all-time high", "lifetime high", "fresh high"],
    "record_low": ["record low", "multi-year low", "fresh low", "52-week low"],
    "flat": ["range-bound", "muted", "sideways", "consolidat", "lacklustre"],
    "volatility_spike": ["volatile", "swings", "choppy", "whipsaw"],
}

# Map text → Ridge macro factor keys
_FACTOR_KEYWORDS: dict[str, list[str]] = {
    "fii_net_5d": ["fii", "foreign investor", "foreign fund", "fpi", "portfolio investor"],
    "dii_net_5d": ["dii", "domestic institutional"],
    "institutional_net_5d": ["institutional", "fii dii", "net flow"],
    "oil_brent": ["brent", "crude", "oil", "opec", "petroleum", "barrel"],
    "oil_wti": ["wti", "west texas"],
    "usd_inr": ["rupee", "usd/inr", "forex", "currency", "dollar"],
    "gold": ["gold", "bullion"],
    "sp500": ["s&p", "sp500", "wall street", "nasdaq", "dow"],
    "us_10y": ["treasury", "bond yield", "us 10y", "10-year yield"],
    "india_vix": ["india vix", "vix", "volatility index"],
    "repo_rate": ["rbi", "repo", "mpc", "rate hike", "rate cut"],
    "index_sentiment": ["sentiment", "risk-on", "risk off", "mood"],
    "nifty_pcr": ["pcr", "put call"],
    "nifty_pe": ["p/e", "pe ratio", "valuation"],
    "cpi_yoy_proxy": ["inflation", "cpi", "wholesale price"],
    "constituent_momentum_7d": ["breadth", "advance decline", "constituent"],
}

_SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "NIFTY": ["nifty 50", "nifty50", "nifty", "nse nifty"],
    "SENSEX": ["sensex", "bse sensex"],
    "BANKNIFTY": ["bank nifty", "banknifty"],
    "NIFTYIT": ["nifty it", "niftyit"],
    "NIFTYMID": ["midcap", "nifty midcap"],
}

_TICKER_RE = re.compile(r"\b([A-Z]{2,12})\b")


@dataclass
class ArticleTags:
    """Filterable tags attached to each canonical news story."""

    publish_day: str = ""
    symbols: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    flat: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _match_keys(text: str, mapping: dict[str, list[str]]) -> list[str]:
    lower = text.lower()
    hits: list[str] = []
    for key, words in mapping.items():
        if any(w in lower for w in words):
            hits.append(key)
    return hits


def _detect_symbols(text: str, *, default_ticker: str = "NIFTY") -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for sym, words in _SYMBOL_KEYWORDS.items():
        if any(w in lower for w in words) and sym not in found:
            found.append(sym)
    default = (default_ticker or "NIFTY").strip().upper()
    if default and default not in found:
        found.insert(0, default)
    return found[:6]


def _detect_equity_mentions(text: str) -> list[str]:
    """Optional uppercase tickers mentioned in headline (e.g. RELIANCE, TCS)."""
    skip = {
        "NIFTY", "SENSEX", "INDIA", "STOCK", "MARKET", "TODAY", "LIVE", "BREAKING",
        "FII", "DII", "RBI", "FED", "RSS", "GDP", "IPO", "ETF", "PCR", "MPC",
    }
    found: list[str] = []
    for match in _TICKER_RE.findall(text):
        if match in skip or len(match) < 3:
            continue
        if match not in found:
            found.append(match)
    return found[:4]


def _build_flat(
    *,
    publish_day: str,
    symbols: list[str],
    topics: list[str],
    factors: list[str],
    themes: list[str],
) -> list[str]:
    flat: list[str] = []
    if publish_day:
        flat.append(f"day:{publish_day}")
    for sym in symbols:
        flat.append(f"symbol:{sym}")
    for topic in topics:
        flat.append(f"topic:{topic}")
    for factor in factors:
        flat.append(f"factor:{factor}")
    for theme in themes:
        flat.append(f"theme:{theme}")
    return flat


def build_article_tags(
    title: str,
    summary: str = "",
    *,
    ticker: str = "NIFTY",
    published_at: str = "",
    implied_factors: list[str] | None = None,
) -> ArticleTags:
    """Generate tags during enrichment / dedup from title + body."""
    working = f"{title} {summary}".strip()
    publish_day = publish_day_from_value(published_at)

    topics = _match_keys(working, _TOPIC_KEYWORDS)
    themes = _match_keys(working, _THEME_KEYWORDS)
    factors = _match_keys(working, _FACTOR_KEYWORDS)

    for factor in implied_factors or []:
        if factor in MACRO_FACTOR_KEYS and factor not in factors:
            factors.append(factor)

    if not factors and topics:
        if "fii" in topics:
            factors.append("fii_net_5d")
        if "oil" in topics:
            factors.append("oil_brent")
        if "forex" in topics:
            factors.append("usd_inr")
        if "vix" in topics:
            factors.append("india_vix")
        if "rbi" in topics:
            factors.append("repo_rate")
        if "us_markets" in topics:
            factors.append("sp500")

    if not factors:
        factors.append("index_sentiment")

    symbols = _detect_symbols(working, default_ticker=ticker)
    for sym in _detect_equity_mentions(title):
        if sym not in symbols:
            symbols.append(sym)

    flat = _build_flat(
        publish_day=publish_day,
        symbols=symbols,
        topics=topics,
        factors=factors,
        themes=themes,
    )
    return ArticleTags(
        publish_day=publish_day,
        symbols=symbols,
        topics=topics,
        factors=factors[:8],
        themes=themes,
        flat=flat,
    )


def merge_article_tags(a: ArticleTags | dict[str, Any], b: ArticleTags | dict[str, Any]) -> ArticleTags:
    """Union tags when merging duplicate stories across sources."""

    def _list(payload: ArticleTags | dict[str, Any], key: str) -> list[str]:
        if isinstance(payload, ArticleTags):
            return list(getattr(payload, key) or [])
        return list(payload.get(key) or [])

    def _day(payload: ArticleTags | dict[str, Any]) -> str:
        if isinstance(payload, ArticleTags):
            return payload.publish_day or ""
        return str(payload.get("publish_day") or "")

    symbols = _uniq(_list(a, "symbols") + _list(b, "symbols"))
    topics = _uniq(_list(a, "topics") + _list(b, "topics"))
    factors = _uniq(_list(a, "factors") + _list(b, "factors"))
    themes = _uniq(_list(a, "themes") + _list(b, "themes"))
    publish_day = _day(b) or _day(a)
    flat = _build_flat(
        publish_day=publish_day,
        symbols=symbols,
        topics=topics,
        factors=factors,
        themes=themes,
    )
    return ArticleTags(
        publish_day=publish_day,
        symbols=symbols,
        topics=topics,
        factors=factors[:10],
        themes=themes,
        flat=flat,
    )


def tags_from_dict(raw: dict[str, Any] | None) -> ArticleTags:
    if not raw:
        return ArticleTags()
    return ArticleTags(
        publish_day=str(raw.get("publish_day") or ""),
        symbols=list(raw.get("symbols") or []),
        topics=list(raw.get("topics") or []),
        factors=list(raw.get("factors") or []),
        themes=list(raw.get("themes") or []),
        flat=list(raw.get("flat") or []),
    )


def record_matches_filters(
    record: dict[str, Any],
    *,
    since: str | None = None,
    until: str | None = None,
    publish_day: str | None = None,
    symbols: list[str] | None = None,
    topics: list[str] | None = None,
    factors: list[str] | None = None,
    themes: list[str] | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Return True when a hub record matches all supplied tag filters."""
    tag_obj = tags_from_dict(record.get("tags"))
    day = tag_obj.publish_day or publish_day_from_value(str(record.get("published_at") or ""))

    if publish_day and day != publish_day[:10]:
        return False
    if since and (not day or day < since[:10]):
        return False
    if until and (not day or day > until[:10]):
        return False

    def _has_any(pool: list[str], wanted: list[str] | None) -> bool:
        if not wanted:
            return True
        norm = {x.lower() for x in pool}
        return any(w.strip().lower() in norm or w.strip().lower().split(":")[-1] in norm for w in wanted)

    if not _has_any(tag_obj.symbols, symbols):
        return False
    if not _has_any(tag_obj.topics, topics):
        return False
    if not _has_any(tag_obj.factors, factors):
        tagged = [str(t.get("factor") or "") for t in (record.get("tagged_factors") or []) if isinstance(t, dict)]
        if not _has_any(tagged, factors):
            return False
    if not _has_any(tag_obj.themes, themes):
        return False
    if tags:
        flat = {t.lower() for t in tag_obj.flat}
        if not any(t.lower() in flat for t in tags):
            return False
    return True


def _uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def list_available_tag_vocab() -> dict[str, list[str]]:
    """Inventory of tag dimensions for UI / agent filter builders."""
    return {
        "topics": sorted(_TOPIC_KEYWORDS),
        "themes": sorted(_THEME_KEYWORDS),
        "factors": sorted(MACRO_FACTOR_KEYS),
        "symbols": sorted(_SYMBOL_KEYWORDS),
    }


# Map hub topic vocab → legacy classifier tokens (T0 audit, causal_attribution)
_TOPIC_ALIASES: dict[str, str] = {
    "us_markets": "us",
    "forex": "usd",
    "fii": "fii",
    "dii": "dii",
    "oil": "oil",
    "war": "war",
    "rbi": "rbi",
    "vix": "vix",
    "earnings": "earnings",
    "gold": "gold",
    "banking": "banking",
    "budget": "budget",
}

_FACTOR_TOPIC_HINTS: dict[str, str] = {
    "oil_brent": "oil",
    "oil_wti": "oil",
    "fii_net_5d": "fii",
    "dii_net_5d": "dii",
    "repo_rate": "rbi",
    "usd_inr": "usd",
    "india_vix": "vix",
    "sp500": "us",
    "us_10y": "us",
}


def tags_are_empty(tags: dict[str, Any] | None) -> bool:
    if not tags:
        return True
    return not any(tags.get(key) for key in ("topics", "factors", "themes"))


def topics_from_record(record: dict[str, Any]) -> set[str]:
    """Hub tags.topics mapped for downstream classifiers (T0 audit, playground)."""
    tag_obj = tags_from_dict(record.get("tags"))
    out: set[str] = set()
    for topic in tag_obj.topics:
        out.add(_TOPIC_ALIASES.get(topic, topic))
    for factor in tag_obj.factors:
        hint = _FACTOR_TOPIC_HINTS.get(factor)
        if hint:
            out.add(hint)
    return out


def legacy_topic_tags_from_item(item: dict[str, Any]) -> list[str]:
    """Legacy classifier tokens from hub-tagged headline dict."""
    return sorted(topics_from_record(item))


def factors_from_record(record: dict[str, Any]) -> list[str]:
    """Prefer hub tags.factors; fall back to tagged_factors list."""
    tag_obj = tags_from_dict(record.get("tags"))
    if tag_obj.factors:
        return list(tag_obj.factors)
    return [
        str(t.get("factor") or "")
        for t in (record.get("tagged_factors") or [])
        if isinstance(t, dict) and t.get("factor")
    ]
