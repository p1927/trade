# Hub News Wiki — Schema

## Page types

| Type | Directory | Purpose |
|------|-----------|---------|
| event | wiki/events/ | Distilled market events (parent + daily timeline) |
| entity | wiki/entities/ | Macro/micro actors (RBI, Iran, Reliance, oil) |
| theme | wiki/themes/ | Cross-cutting themes (rate cycle, geopolitical risk) |
| source | sources/news/ | Immutable raw ref exports from hub events |

## Frontmatter (event pages)

```yaml
---
type: event
event_id: evt:abc123
parent_event_id: evt:parent or null
title: Human-readable title
ticker: NIFTY
provenance: live | backfill | curated
market_impact_status: observed | claimed | predicted | unverified
compiled_at: ISO8601
processing_version: 1
source_count: 8
linked_factors: [oil_brent, fii_net_5d]
---
```

## Rules

- Wiki pages are **derived** from `events.parquet` — regenerate, never authoritative over SSOT.
- Impact numbers come from `event_outcomes` ledger, not free-form LLM prose.
- Outliers stay in a **Conflicts** section; do not delete dissenting sources.
- Use `[[entity-slug]]` wikilinks between events, entities, and themes.
