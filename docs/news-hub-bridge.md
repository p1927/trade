# News Hub Bridge — public API contract

**Single entry point:** `trade_integrations.dataflows.news_hub_bridge`

Anyone who needs news — TradingAgents, Vibe, OpenAlgo monitor, index analysis, API routes, autonomous agents — must use this package. Internal pipeline modules are not part of the public surface.

## Rule

| Do | Don't |
|----|--------|
| `from trade_integrations.dataflows import news_hub_bridge` | `from ...news_impact_engine import ...` |
| `news_hub_bridge.headlines_for_day(...)` | `from ...news_collect import collect_headlines_for_day` |
| `news_hub_bridge.query_verified_news(...)` | `from ...verified_news_store import list_verified_records` (in app code) |

Ops scripts under `scripts/` may call internal repair helpers (`repair_hub_tags`, `reconcile_matured_impacts`) directly when maintaining the hub.

## Pipeline (internal, automatic)

```
Source fetch (RSS / aggregator / SearXNG / archive / watcher)
  → ingest_* on news_hub_bridge
  → dedup + tags (news_dedup)
  → verify cache-first (news_impact_engine)
  → hub SSOT (reports/hub/_data/news_verified/records.parquet)
  → consumers read via news_hub_bridge
```

## Public API

### Read — use for any consumer

| Function | Purpose |
|----------|---------|
| `headlines_for_day(day, ticker, limit)` | Tagged headlines for a calendar day; ingests on cache miss |
| `to_headline_dict(item)` | Normalize hub row for attribution / miss analysis |
| `list_headlines_for_date(day, ticker)` | Hub records for one publish day |
| `list_recent_headlines(ticker, limit)` | Latest verified headlines (any day) |
| `query_verified_news(...)` | Filter hub by date, topic, factor, theme tags |
| `resolve_news_impact(ticker, doc)` | Unified snapshot: `latest.json` → file → hub |
| `load_news_impact(ticker)` | Read `news_impact_latest.json` |
| `refresh_news_impact(ticker, ...)` | Ingest + build + save snapshot |
| `sync_news_impact_to_index_doc(doc)` | Attach resolved news_impact before saving index research |
| `save_news_impact(report, ticker)` | Write snapshot file only |
| `tag_inventory(ticker)` | Tag vocab summary for filter UIs |

### Ingest — source adapters only

Called by wired fetchers after they collect raw items. Application code should not fetch RSS/aggregator directly and skip ingest.

| Function | Wired in |
|----------|----------|
| `ingest_news_articles` | `news_aggregator/aggregator.py`, `register.py` (yfinance fallback) |
| `ingest_rss_entries` | `rss_feeds.py` (TradingAgents sentiment feeds) |
| `ingest_searxng_results` | `searxng_news.py` |
| `ingest_rows_to_hub` | `news_watcher.py`, `moneycontrol_rss.py` |
| `enrich_articles_with_hub_tags` | Aggregator (appends tags to agent markdown) |

### Utility

| Function | Purpose |
|----------|---------|
| `hub_ticker_for_symbol(symbol, kind)` | Map `RELIANCE.NS`, `^NSEI`, global → hub partition |

## Wired sources (all ingest through bridge)

- TradingAgents `get_news` / `get_global_news` (aggregated vendor)
- TradingAgents sentiment RSS feeds (`sentiment_rss_feeds` in config)
- SearXNG vendor (`get_news_searxng`)
- yfinance direct vendor (patched in `register.py`)
- Company research news stage (via aggregator)
- Moneycontrol / Google India RSS (calendar signals)
- Material news watcher (NIFTY / BANKNIFTY)
- Index research aggregator + light refresh
- Index `news_collect` path (internal to pipeline, exposed read via bridge)

## Storage layout

| Artifact | Path |
|----------|------|
| Hub SSOT records | `reports/hub/_data/news_verified/records.parquet` |
| Impact snapshot | `reports/hub/{TICKER}/index_research/news_impact_latest.json` |
| Embedded in index doc | `reports/hub/{TICKER}/index_research/latest.json` → `news_impact` |

## Example

```python
from trade_integrations.dataflows import news_hub_bridge as news

# Analysis: headlines for a prediction day
rows = news.headlines_for_day("2026-04-28", ticker="NIFTY", limit=8)

# API / UI: full impact snapshot
report = news.resolve_news_impact(ticker="NIFTY", doc=index_doc)

# Filter by tag
oil = news.query_verified_news(ticker="NIFTY", topics=["oil"], limit=20)

# Refresh after market open
report = news.refresh_news_impact(ticker="NIFTY", refresh_ingest=True)
```

## Internal modules (do not import from app code)

- `integrations/.../index_research/news_impact_engine.py`
- `integrations/.../index_research/news_collect.py`
- `integrations/.../index_research/news_dedup.py`
- `integrations/.../hub_storage/verified_news_store.py`
- `integrations/.../news_hub_bridge/_ingest.py`
