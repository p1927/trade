"""India company_research data sources — what we depend on and fallback order.

Consumed by factor_catalog (UI/API) and agent context. Keep in sync when adding
or reordering fetchers under sources/*.py.
"""

from __future__ import annotations

from typing import Any

# reliability: high | partial | fragile | optional
# cost: free | freemium | broker

INDIA_COMPANY_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "key": "openalgo",
        "label": "OpenAlgo (INDmoney / Zerodha / …)",
        "stages": ["identity"],
        "priority": 1,
        "env": ["OPENALGO_HOST", "OPENALGO_API_KEY"],
        "package": "openalgo (local)",
        "provides": ["ltp", "volume", "bid", "ask", "ohlc", "oi"],
        "limits": "Broker API rate limits (~5 req/s for INDmoney quotes)",
        "reliability": "high",
        "cost": "broker",
        "notes": "Live execution authority; quotes only — no sector, peers, or fundamentals.",
    },
    {
        "key": "yfinance",
        "label": "Yahoo Finance (.NS / .BO)",
        "stages": ["identity", "fundamentals", "calendar", "peers"],
        "priority": 2,
        "env": [],
        "package": "yfinance",
        "provides": ["sector", "industry", "market_cap", "pe_ratio", "ratios", "earnings_date"],
        "limits": "Informal Yahoo throttling on heavy batch use",
        "reliability": "high",
        "cost": "free",
        "notes": "Baseline for identity and fundamentals; peers stage gets sector context only.",
    },
    {
        "key": "dalal_bse",
        "label": "dalal (BSE routes)",
        "stages": ["identity", "fundamentals", "calendar", "filings"],
        "priority": 3,
        "env": ["TRADINGAGENTS_BSE_CODE_MAP"],
        "package": "dalal>=0.2.1",
        "provides": ["sector", "industry", "pe_ratio", "fundamentals_table", "bse_announcements"],
        "limits": "Requires BSE scrip code (auto via bse.getScripCode when map missing)",
        "reliability": "high",
        "cost": "free",
        "notes": "Preferred BSE path; dalal NSE routes hit Akamai 403 — do not use for NSE.",
    },
    {
        "key": "bse_india",
        "label": "BSE India API (bse package)",
        "stages": ["calendar"],
        "priority": 1,
        "env": [],
        "package": "bse>=3.3.0",
        "provides": ["corporate_announcements", "corporate_actions"],
        "limits": "Scrape/API courtesy; 7-day lookback in calendar_in",
        "reliability": "high",
        "cost": "free",
        "notes": "Primary calendar source when Tapetide quota exhausted.",
    },
    {
        "key": "screener_in",
        "label": "Screener.in (screenercli)",
        "stages": ["peers"],
        "priority": 1,
        "env": [],
        "package": "screenercli>=0.1.2",
        "provides": ["peer_comparison", "sector", "industry", "peer_market_cap"],
        "limits": "HTML scrape; add delays for batch; respect screener.in ToS",
        "reliability": "high",
        "cost": "free",
        "notes": "Primary peer list replacement for Tapetide (≥5 peers typical).",
    },
    {
        "key": "nselib",
        "label": "nselib (NSE public data)",
        "stages": ["identity", "fundamentals", "calendar", "peers", "macro"],
        "priority": 4,
        "env": [],
        "package": "nselib>=2.0",
        "provides": ["pe_ratio", "quarterly_results", "event_calendar", "nifty50_list", "india_vix"],
        "limits": "NSE-side fragility; calendar/financials APIs often return empty",
        "reliability": "fragile",
        "cost": "free",
        "notes": "pe_ratio works; event_calendar_for_equity unreliable. Peers = Nifty50 industry heuristic only.",
    },
    {
        "key": "tapetide",
        "label": "Tapetide MCP",
        "stages": ["identity", "peers", "fundamentals", "calendar"],
        "priority": 99,
        "env": [
            "TAPETIDE_TOKEN",
            "TAPETIDE_MCP_URL",
            "TAPETIDE_ENABLED",
            "TAPETIDE_BATCH",
            "TAPETIDE_CACHE_MINUTES",
        ],
        "package": "requests (remote MCP)",
        "provides": ["company_profile", "peers", "key_ratios", "stock_events"],
        "limits": "Free tier ~4,000 MCP calls/day; skipped in Nifty batch unless TAPETIDE_BATCH=true",
        "reliability": "optional",
        "cost": "freemium",
        "notes": "Optional enrichment only. Set TAPETIDE_ENABLED=false when quota hit.",
    },
    {
        "key": "moneycontrol_rss",
        "label": "Moneycontrol RSS",
        "stages": ["calendar"],
        "priority": 5,
        "env": [],
        "package": "feedparser (via moneycontrol_rss)",
        "provides": ["results_news"],
        "limits": "Sparse coverage",
        "reliability": "fragile",
        "cost": "free",
        "notes": "Weak calendar fallback.",
    },
)

STAGE_SOURCE_ORDER: dict[str, list[str]] = {
    "identity": ["openalgo", "yfinance", "dalal_bse", "nselib", "tapetide"],
    "peers": ["screener_in", "tapetide", "nselib", "yfinance"],
    "calendar": ["bse_india", "yfinance", "nselib", "moneycontrol_rss", "dalal_bse", "tapetide"],
    "fundamentals": ["dalal_bse", "yfinance", "nselib", "tapetide"],
    "filings": ["dalal_bse"],
}


def list_india_company_data_sources() -> dict[str, Any]:
    """Return dependency matrix for India company_research stages."""
    return {
        "market": "IN",
        "sources": [dict(row) for row in INDIA_COMPANY_DATA_SOURCES],
        "stage_source_order": STAGE_SOURCE_ORDER,
        "tapetide_policy": {
            "batch_default": "skipped (TAPETIDE_BATCH=false)",
            "calendar": "only when no events from cheaper sources",
            "disable": "TAPETIDE_ENABLED=false or unset TAPETIDE_TOKEN",
        },
    }


def sources_for_stage(stage: str) -> list[dict[str, Any]]:
    """Sources that feed a pipeline stage, in priority order."""
    order = STAGE_SOURCE_ORDER.get(stage, [])
    by_key = {row["key"]: row for row in INDIA_COMPANY_DATA_SOURCES}
    return [by_key[key] for key in order if key in by_key]
