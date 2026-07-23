# Hub News Wiki — Schema

## Page types

| Type | Directory | Purpose |
|------|-----------|---------|
| event | wiki/ (ingested) | Distilled market events after LLM Wiki ingest |
| entity | wiki/entities/ | Macro/micro actors (RBI, Iran, Reliance, oil) |
| concept | wiki/concepts/ | Cross-cutting concepts (rate cycle, geopolitical risk) |
| source | wiki/sources/ | LLM-generated per-source summaries |
| query | wiki/queries/ | Saved Deep Research answers (LLM-generated) |
| raw event | raw/sources/news/ | Immutable exports from hub events (Trade writes here) |
| raw research | raw/sources/research/ | Immutable Deep Research exports (Trade writes here) |

## Frontmatter (raw exports in raw/sources/news/ or raw/sources/research/)

```yaml
---
type: event          # or research for Deep Research exports
title: Human-readable title
sources: [news/slug.md]
event_id: evt:abc123
parent_event_id: evt:parent or null
ticker: NIFTY
provenance: live | backfill | curated
market_impact_status: observed | claimed | predicted | unverified
compiled_at: ISO8601
processing_version: 1
source_count: 8
linked_factors: [oil_brent, fii_net_5d]
gap_kind: conflicts   # research exports only
---
```

## Rules

- Wiki pages are **derived** from `events.parquet` — regenerate, never authoritative over SSOT.
- Trade exports to `raw/sources/news/` and `raw/sources/research/` only; LLM Wiki ingest builds searchable `wiki/` pages.
- Map `linked_factors` to `[[entity-slug]]` / `[[concept-slug]]` wikilinks on ingest.
- Impact numbers come from `event_outcomes` ledger, not free-form LLM prose.
- Outliers stay in a **Conflicts** section; do not delete dissenting sources.
- `type: event` on a raw source file is a Trade convention; LLM Wiki generates typed wiki pages on ingest.
