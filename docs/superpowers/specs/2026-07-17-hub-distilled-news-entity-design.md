# Hub Distilled News Entity — Design Spec

**Date:** 2026-07-17  
**Status:** Approved direction — supersedes `2026-07-17-hub-news-deduplication-design.md`  
**Scope:** Hub architecture for curated news events, async enrichment queue, and evolving timelines

---

## Problem

Today the hub stores **raw internet articles** (one row per headline) in `records.parquet`. Multiple outlets covering the same event create bloat. Summaries are not reconciled (ref A says Nifty 24,500; ref B says 24,800). News evolves intraday but we only snapshot at ingest. Enrichment and verification block the live analysis path.

## Vision

The hub SSOT is a **Distilled News Event** — our curated understanding of a market event — not a copy of someone else's headline.

```
Internet articles (raw refs)
        │
        ▼
  Staging queue (immediate, unprocessed)
        │
        ├──► Live analysis reads staging + distilled (no wait)
        │
        ▼
  Distillation agent (async, daily + on enqueue)
        │
        ▼
  Hub distilled events (SSOT) — title, content, timeline, references, consensus
```

---

## Core concepts

### 1. DistilledNewsEvent (hub entity)

One entity per market **event** (moderate dedup: same day, same factor/direction, similar narrative).

| Field | Purpose |
|-------|---------|
| `event_id` | Stable UUID; never changes when refs are added |
| `ticker` | Hub partition (`NIFTY`, `RELIANCE`, …) |
| `title` | Our distilled headline — factual, de-clickbait |
| `content` | Synthesized narrative: what happened, why it matters, reconciled numbers |
| `timeline` | Ordered updates as the story evolves (see below) |
| `references` | Source articles with per-source claims |
| `consensus` | Reconciled view: direction, factor weights, level ranges, confidence |
| `tags` | Existing `ArticleTags` (topics, themes, factors) |
| `predicted_impact` / `actual_impact` | Unchanged from current impact engine |
| `status` | `active` \| `matured` \| `superseded` |
| `processing_version` | Bump when distillation re-runs |
| `first_seen_at` / `updated_at` | Lifecycle |

### 2. Reference (attached to event)

Each internet article we ingest becomes a **reference**, not a hub row.

```json
{
  "ref_id": "ref:sha256(url)",
  "url": "https://…",
  "publisher": "Moneycontrol",
  "vendor": "searxng",
  "raw_title": "FII selling drags Nifty lower",
  "raw_summary": "…",
  "published_at": "2026-04-28T10:30:00+00:00",
  "fetched_at": "2026-04-28T11:00:00+00:00",
  "extracted_claims": [
    {"kind": "level_target", "symbol": "NIFTY", "value": 24500, "direction": "down"},
    {"kind": "flow", "factor": "fii_net_5d", "direction": "outflow"}
  ]
}
```

### 3. Timeline (within event)

News evolves; the entity accumulates **timeline entries** instead of spawning duplicate rows.

```json
{
  "at": "2026-04-28T14:00:00+00:00",
  "kind": "update",
  "summary": "Additional FII outflow data confirmed; Nifty tested 24,400 support.",
  "source_ref_ids": ["ref:abc", "ref:def"],
  "consensus_snapshot": {"direction": "bearish", "nifty_range": [24300, 24500]}
}
```

Kinds: `created`, `update`, `revision` (consensus changed), `matured` (horizon reached).

### 4. Consensus (reconciled understanding)

LLM + rules produce a single structured view from all references:

```json
{
  "direction": "bearish",
  "primary_factors": ["fii_net_5d"],
  "nifty_level_range": [24300, 24500],
  "narrative": "Foreign investors continued selling; multiple sources agree on downward pressure though targets differ (24,400–24,500).",
  "confidence": 0.72,
  "conflicts": [
    {"field": "nifty_level", "refs": ["ref:a says 24500", "ref:b says 24800"], "resolution": "Weight recent ref; range 24300–24500"}
  ]
}
```

---

## Two-tier storage

| Tier | Path | Contents | Read path |
|------|------|----------|-----------|
| **Staging queue** | `_data/news_staging/queue.parquet` | Raw refs awaiting distillation | Live analysis (immediate) |
| **Distilled SSOT** | `_data/news_events/events.parquet` | DistilledNewsEvent entities | All consumers after processing |

**Migration:** `records.parquet` remains readable during transition; distillation backfills `events.parquet`; bridge API reads union then prefers distilled.

---

## Async queue + live read path

### Ingest (fast path)

```
Source fetch → ingest_raw_ref(row)
  → append to staging queue (dedupe by url hash)
  → enqueue distillation job (event_id TBD)
  → return immediately {queued: true, ref_id: …}
```

**No LLM on ingest.** Keyword tags attached synchronously via existing `build_article_tags()` for staging filter UI.

### Live analysis (no wait)

When `headlines_for_day()` or `query_verified_news()` is called:

1. Load **distilled events** from `events.parquet` matching filters.
2. Load **staging refs** not yet merged (`processing_status = queued`).
3. Return **union**, deduped by URL; staging items marked `provenance: staging`.
4. Downstream (index research, prediction) uses these as today — no pipeline change required for v1.

### Distillation agent (background)

Runs on:
- Each enqueue (debounced batch every 2 min during market hours)
- Daily compaction cron (`18:35 IST`)
- Manual `scripts/distill_hub_news.py`

```
pull batch from staging (status=queued)
  → for each ref:
      match existing event (semantic cluster + summary similarity ≥ 0.72)
      OR create new event shell
  → LLM distill: title, content, consensus, timeline entry, extracted_claims
  → upsert events.parquet
  → mark staging ref merged → event_id
  → remove from active queue (retain in staging archive for audit)
```

**Matching (moderate B):** Same `publish_day + ticker + primary_factor + direction`; summary similarity ≥ 0.72 on distilled `content` or raw summaries.

---

## Distillation LLM contract

Input: event (if exists) + new ref(s) + factor catalog hints.

Output JSON (structured, stored on event):

```json
{
  "title": "FII selling weighs on Nifty; index tests 24,400",
  "content": "Multi-paragraph distilled narrative with reconciled facts…",
  "consensus": { … },
  "timeline_entry": { "kind": "update", "summary": "…" },
  "extracted_claims": [ … ],
  "tags": { "topics": ["fii"], "themes": ["selloff"], "factors": ["fii_net_5d"] }
}
```

**Rules enforced in prompt:**
- Never copy headline verbatim; synthesize.
- When refs disagree on numbers, state range + which ref said what.
- Map claims to `MACRO_FACTOR_KEYS` where possible.
- Direction must align with themes unless explicitly mixed.

Fallback when LLM unavailable: keyword enrichment only (`news_enrichment.py`); event stays `provenance: keyword_only`.

---

## Public API changes (`news_hub_bridge`)

| Function | Behavior change |
|----------|-----------------|
| `ingest_*` | Write to staging queue; enqueue distillation (not direct SSOT upsert) |
| `query_verified_news()` | Read distilled events + unmerged staging; map to legacy headline dict shape |
| `headlines_for_day()` | Same union; prefer distilled when ref already merged |
| `to_headline_dict()` | Accept `DistilledNewsEvent` or staging ref |
| **New** `get_distilled_event(event_id)` | Full entity with timeline + references |
| **New** `distillation_queue_stats()` | Queue depth, oldest pending, last run |

Legacy `records.parquet` fields map to distilled shape:

| Old | New |
|-----|-----|
| `canonical_story_id` | `event_id` |
| `title` | `title` (distilled) |
| `content_summary` | `content` |
| `sources_json` | `references` |
| `structured_summary_json` | `consensus` subset |
| — | `timeline_json` (new column) |

---

## Daily compaction job

`hub_news_distillation` cron `35 18 * * *`:

1. Process all remaining staging queue items.
2. Re-match orphan staging refs against events (repair pass).
3. Merge duplicate events (same cluster + content similarity).
4. Archive staging rows older than 7d to `_data/news_staging/archive/{date}.parquet`.
5. Mechanical date-key dedup on factor/prediction parquet (unchanged from prior spec).
6. Write compaction report to `_data/news_events/distillation_log.jsonl`.

---

## Consumer impact

| Consumer | Change |
|----------|--------|
| Index research / light refresh | Reads union API — transparent |
| Prediction attribution | Uses `event_id`; fewer duplicates improve signal |
| News impact panel UI | Show timeline + references expandable; consensus badge |
| TradingAgents / Vibe | Receive distilled content in hub artifacts, not raw headlines |
| Autonomous agents | Same bridge API |

---

## Non-goals (v1)

- Real-time WebSocket push of timeline updates to UI (poll on existing interval).
- Cross-ticker event linking (NIFTY event linked to RELIANCE) — v2.
- Full article body fetch beyond summary field — reuse aggregator summary.
- Deleting `records.parquet` — deprecate after backfill validation.

---

## Success criteria

1. Live analysis latency unchanged — ingest returns in < 200ms without LLM.
2. Staging queue drains within 10 min during market hours under normal load.
3. Duplicate event rate in 90d window drops ≥ 30% vs raw-per-article model.
4. Distilled events include ≥ 2 references on average for high-traffic days.
5. `query_verified_news()` backward-compatible dict shape for all existing tests.

---

## Resolved decisions

| Question | Decision |
|----------|----------|
| Store raw or distilled? | **Distilled entity SSOT; raw as references** |
| Live path | **Staging queue immediate; distillation async** |
| Dedup aggressiveness | **Moderate B + summary similarity 0.72** |
| Evolution | **Timeline within entity, not new rows** |
| LLM when | **Distillation agent only, not on ingest** |
