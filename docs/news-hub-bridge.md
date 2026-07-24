# News Hub Bridge — public API contract

**Single entry point:** `trade_integrations.dataflows.news_hub_bridge`

Anyone who needs news — TradingAgents, Vibe, OpenAlgo monitor, index analysis, API routes, autonomous agents — must use this package. Internal pipeline modules are not part of the public surface.

## Rule

| Do | Don't |
|----|--------|
| `from trade_integrations.dataflows import news_hub_bridge` | `from ...news_impact_engine import ...` (app code) |
| `news_hub_bridge.query_verified_news(...)` | Direct `news_events_store` / `verified_news_store` imports (app code) |
| Ops maintainer / migration scripts | Leaving unmigrated rows in legacy `records.parquet` |

Ops scripts under `scripts/` may call internal repair helpers and `ensure_hub_news_migrations()` directly.

## Pipeline

```
Source fetch (RSS / aggregator / SearXNG / archive / watcher)
  → news_hub_bridge.ingest_*  (fast path → staging queue, no LLM)
  → staging drain (resolver T0–T4) → events.parquet SSOT
  → maintainer (mode=maintenance): migrate → repair → backfill → fact adjudication
    → compact → safety sweep → cleanup → rollup → impact refresh → index sync
  → all reads via news_hub_bridge (union staging + distilled)
```

### Entity worker modes

| Mode | Trigger | Scope |
|------|---------|-------|
| `drain` | Continuous cron, manual drain | Staging batch, light compact (7d), impact refresh |
| `maintenance` | Daily cron, Hub “Run maintainer now” | Full hygiene pipeline (see index plan) |

Maintainer is **not** ingest. It merges duplicates, enriches facts, archives legacy rows, and prunes bloat.

### Gates

- **LLM-Wiki:** `HUB_NEWS_REQUIRE_LLM_WIKI=1` — ingest and distillation pause when wiki unavailable.
- **Migration:** Legacy `records.parquet` rows must migrate to `events.parquet` before hub is `hub_ready`.
- **Soft-create visibility:** Single-ref events are hidden from prediction attribution until a second ref corroborates (`news_prediction_visibility`).

## Storage layout

| Artifact | Path |
|----------|------|
| Distilled SSOT | `reports/hub/_data/news_events/events.parquet` |
| Event index | `reports/hub/_data/news_events/event_index.parquet` |
| Staging queue | `reports/hub/_data/news_staging/` |
| Migration state | `reports/hub/_data/news_events/migration_state.json` |
| Impact snapshot | `reports/hub/{TICKER}/index_research/news_impact_latest.json` |
| Embedded in index doc | `reports/hub/{TICKER}/index_research/latest.json` → `news_impact` |
| Legacy (read/migrate only) | `reports/hub/_data/news_verified/records.parquet` |

Run one-shot cutover: `python scripts/finalize_hub_news_ssot.py`

## Public API

### Read — use for any consumer

| Function | Purpose |
|----------|---------|
| `headlines_for_day(day, ticker, limit)` | Tagged headlines for a calendar day |
| `query_verified_news(...)` | Filter hub by date, topic, factor, theme tags |
| `query_with_staging(...)` | Distilled + pending staging union |
| `resolve_news_impact(ticker, doc)` | Unified snapshot: latest.json → file → hub |
| `load_news_impact(ticker)` | Read `news_impact_latest.json` |
| `refresh_news_impact(ticker, ...)` | Build + save snapshot (ingest optional) |
| `sync_news_impact_to_index_doc(doc)` | Attach resolved news_impact before saving index research |
| `to_headline_dict(item)` | Normalize hub row for attribution |

Prediction reads apply `filter_prediction_attribution_items` (soft-create policy). Hub inventory UI shows all union items.

### Ingest — source adapters only

| Function | Wired in |
|----------|----------|
| `ingest_news_articles` | Aggregator, register yfinance fallback |
| `ingest_rss_entries` | RSS feeds |
| `ingest_searxng_results` | SearXNG |
| `ingest_rows_to_hub` | Material watcher, Moneycontrol RSS |
| `run_hub_news_ingest` | Scheduled full/light ingest jobs |

### Maintainer → injection

Maintainer refreshes `news_impact_latest.json` and (when `INDEX_NEWS_SYNC_ON_MAINTAINER=1`) embeds into existing `latest.json`. Vibe hub injection reads index artifact on prefetch — not live maintainer output. News-scenario sessions bind frozen `pipeline_as_of` and use MCP tools; maintainer does not invalidate active scenario drafts.

## Example

```python
from trade_integrations.dataflows import news_hub_bridge as news

rows = news.query_with_staging(ticker="NIFTY", limit=20)
report = news.resolve_news_impact(ticker="NIFTY", doc=index_doc)
report = news.refresh_news_impact(ticker="NIFTY", refresh_ingest=False)
```

## Internal modules (do not import from app code)

- `index_research/news_impact_engine.py`
- `index_research/news_entity_worker.py`
- `index_research/news_maintainer_*.py`
- `hub_storage/news_events_store.py`
- `hub_storage/news_migrations.py`
- `news_hub_bridge/_ingest.py`
