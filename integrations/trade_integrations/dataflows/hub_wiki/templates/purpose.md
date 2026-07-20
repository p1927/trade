# Hub News Intelligence — Purpose

This wiki captures distilled **India market news events** for NIFTY / index prediction research.

## Goals

- One canonical narrative per market event (deduplicated from RSS, SearXNG, and watcher feeds)
- Traceable sources with timeline and conflict sections
- Searchable context for prediction scenarios and agent research

## Key questions

- What happened, when, and which factors (oil, FII, RBI, geopolitics) are linked?
- How does this event relate to prior parent-topic rollups?
- Where do sources disagree (conflicts section)?

## Scope

- **In scope:** NIFTY, Sensex, India macro, FII/DII flows, RBI, earnings season, oil/geopolitical drivers
- **Out of scope:** sports, entertainment, unrelated foreign headlines without India market link

## Source of truth

Structured SSOT lives in `reports/hub/_data/news_events/events.parquet`. This wiki is the human-readable digest layer built by LLM Wiki ingest from `raw/sources/news/`.
