# Hub Distilled News Entity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw-per-article hub storage with **DistilledNewsEvent** entities (curated title, content, timeline, references, consensus), fed by a **staging queue** for immediate live reads and an **async distillation agent** for LLM enrichment and deduplication.

**Architecture:** Raw internet fetches land in `_data/news_staging/queue.parquet` and are returned immediately to live analysis. A background distillation worker matches refs to existing events (moderate semantic dedup + summary similarity ≥ 0.72), runs LLM synthesis to update the entity timeline and consensus, and persists to `_data/news_events/events.parquet`. `news_hub_bridge` reads the union (distilled + unmerged staging) with backward-compatible headline dicts.

**Tech Stack:** Python 3.11+, pandas/parquet (existing `hub_storage`), `news_tags` / `news_dedup` / `news_enrichment`, existing Vibe LLM client pattern, `scheduled_research/index_jobs.py` cron.

**Spec:** `docs/superpowers/specs/2026-07-17-hub-distilled-news-entity-design.md`

## Global Constraints

- All app reads/writes go through `trade_integrations.dataflows.news_hub_bridge` — not direct store imports.
- LLM runs only in distillation agent — never on ingest hot path.
- Staging ingest must return in < 200ms (no network LLM).
- Distilled events use existing factor taxonomy (`MACRO_FACTOR_KEYS`, `ArticleTags`) — no parallel vocab.
- Summary similarity threshold: **0.72** (env `HUB_NEWS_DEDUP_SUMMARY_THRESHOLD`).
- Moderate dedup bucket: `(publish_day, ticker, primary_factor, market_direction)`.
- Backward-compatible `to_headline_dict()` shape for existing consumers and tests.
- `records.parquet` kept readable during migration; do not delete until backfill validated.

---

## File map

| File | Responsibility |
|------|----------------|
| `hub_storage/news_event_models.py` | Dataclasses: `DistilledNewsEvent`, `NewsReference`, `TimelineEntry`, `Consensus` |
| `hub_storage/news_staging_store.py` | Staging queue CRUD (parquet) |
| `hub_storage/news_events_store.py` | Distilled events SSOT CRUD (parquet) |
| `index_research/news_distillation.py` | Match, LLM distill, merge logic |
| `index_research/news_distillation_agent.py` | Batch worker: pull queue → distill → upsert |
| `news_hub_bridge/_ingest.py` | Ingest → staging only + enqueue |
| `news_hub_bridge/__init__.py` | Union read path + new APIs |
| `index_research/news_dedup.py` | Add `summary_similarity()` export |
| `vibetrading/.../index_jobs.py` | Register `hub_news_distillation` cron |
| `scripts/distill_hub_news.py` | Manual/dry-run CLI |
| `scripts/backfill_distilled_events.py` | Migrate `records.parquet` → `events.parquet` |

---

### Task 1: Data models and staging store

**Files:**
- Create: `integrations/trade_integrations/hub_storage/news_event_models.py`
- Create: `integrations/trade_integrations/hub_storage/news_staging_store.py`
- Test: `tests/test_news_staging_store.py`

**Interfaces:**
- Consumes: `hub_storage/parquet_io.py`, `context/hub.get_hub_dir()`
- Produces:
  - `NewsReference`, `DistilledNewsEvent`, `TimelineEntry`, `EventConsensus` dataclasses with `to_dict()` / `from_dict()`
  - `enqueue_raw_ref(row: dict, *, ticker: str) -> str` → returns `ref_id`
  - `list_pending_refs(*, ticker: str | None, limit: int) -> list[dict]`
  - `mark_ref_merged(ref_id: str, event_id: str) -> None`
  - `staging_queue_stats() -> dict`

- [ ] **Step 1: Write the failing test**

```python
def test_enqueue_and_list_pending_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    from trade_integrations.hub_storage.news_staging_store import (
        enqueue_raw_ref,
        list_pending_refs,
        mark_ref_merged,
    )

    ref_id = enqueue_raw_ref(
        {"title": "FII sell", "summary": "Outflows continue", "url": "https://x/a"},
        ticker="NIFTY",
    )
    pending = list_pending_refs(ticker="NIFTY")
    assert len(pending) == 1
    assert pending[0]["ref_id"] == ref_id
    assert pending[0]["processing_status"] == "queued"

    mark_ref_merged(ref_id, "evt:abc")
    assert list_pending_refs(ticker="NIFTY") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_staging_store.py::test_enqueue_and_list_pending_ref -v`  
Expected: FAIL — module not found

- [ ] **Step 3: Implement models + staging store**

Storage path: `{hub}/_data/news_staging/queue.parquet`  
Columns: `ref_id`, `ticker`, `raw_title`, `raw_summary`, `url`, `publisher`, `vendor`, `published_at`, `fetched_at`, `tags_json`, `processing_status`, `merged_event_id`, `created_at`

Dedupe on enqueue: skip if same `ref_id` (sha256 of normalized url) already queued or merged today.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_staging_store.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add integrations/trade_integrations/hub_storage/news_event_models.py \
        integrations/trade_integrations/hub_storage/news_staging_store.py \
        tests/test_news_staging_store.py
git commit -m "feat(hub-news): add staging queue store for raw article refs"
```

---

### Task 2: Distilled events store

**Files:**
- Create: `integrations/trade_integrations/hub_storage/news_events_store.py`
- Test: `tests/test_news_events_store.py`

**Interfaces:**
- Consumes: `news_event_models.py`
- Produces:
  - `upsert_event(event: DistilledNewsEvent) -> None`
  - `get_event(event_id: str) -> dict | None`
  - `list_events(*, ticker: str, since: str | None, limit: int) -> list[dict]`
  - `query_events(*, ticker, topics, factors, themes, since, limit) -> list[dict]`

Storage path: `{hub}/_data/news_events/events.parquet`  
Columns: `event_id`, `ticker`, `title`, `content`, `timeline_json`, `references_json`, `consensus_json`, `tags_json`, `predicted_impact_json`, `actual_impact_json`, `status`, `processing_version`, `first_seen_at`, `updated_at`, `publish_day`

- [ ] **Step 1: Write the failing test**

```python
def test_upsert_and_query_event(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent
    from trade_integrations.hub_storage.news_events_store import list_events, upsert_event

    evt = DistilledNewsEvent(
        event_id="evt:test1",
        ticker="NIFTY",
        title="FII selling weighs on Nifty",
        content="Foreign investors sold for a third session…",
        publish_day="2026-04-28",
    )
    upsert_event(evt)
    rows = list_events(ticker="NIFTY", since="2026-04-28")
    assert rows[0]["event_id"] == "evt:test1"
    assert "FII" in rows[0]["title"]
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement store** (mirror patterns from `verified_news_store.py`)

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): add distilled events parquet store"
```

---

### Task 3: Summary similarity + event matching

**Files:**
- Modify: `integrations/trade_integrations/dataflows/index_research/news_dedup.py`
- Create: `integrations/trade_integrations/dataflows/index_research/news_event_matching.py`
- Test: `tests/test_news_event_matching.py`

**Interfaces:**
- Consumes: `news_dedup.semantic_cluster_key`, `news_tags.build_article_tags`
- Produces:
  - `summary_similarity(a: str, b: str) -> float`
  - `match_ref_to_event(ref: dict, events: list[dict], *, ticker: str) -> str | None` → `event_id` or None

Matching rules:
1. Same `(publish_day, ticker, primary_factor, direction)` bucket.
2. `summary_similarity(ref_summary, event.content) >= 0.72` OR same `semantic_cluster_key`.
3. Never merge conflicting direction (bullish vs bearish).

- [ ] **Step 1: Write failing tests** for similarity threshold and bucket guard

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement** using `difflib.SequenceMatcher` + token Jaccard fallback for short text

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): add event matching with summary similarity"
```

---

### Task 4: LLM distillation core

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/news_distillation.py`
- Test: `tests/test_news_distillation.py`

**Interfaces:**
- Consumes: `news_event_models`, `news_enrichment.build_structured_summary`, Vibe/agent LLM helper (grep existing `call_llm_json` or structured output pattern in repo)
- Produces:
  - `distill_ref_into_event(ref: dict, event: dict | None, *, ticker: str) -> DistilledNewsEvent`
  - `keyword_fallback_distill(ref, event) -> DistilledNewsEvent` (no LLM)

LLM prompt output schema: `title`, `content`, `consensus`, `timeline_entry`, `extracted_claims`, `tags`.

When `event is None`: create new event with `timeline=[{kind: "created", …}]`.  
When event exists: append `timeline_entry` kind `update`; merge references; refresh consensus.

- [ ] **Step 1: Write failing test with mocked LLM** returning fixed JSON

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement distill + keyword fallback**

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): add LLM distillation with keyword fallback"
```

---

### Task 5: Distillation agent worker

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/news_distillation_agent.py`
- Test: `tests/test_news_distillation_agent.py`

**Interfaces:**
- Consumes: staging store, events store, matching, distillation
- Produces:
  - `process_distillation_batch(*, ticker: str | None, limit: int) -> dict` → `{processed, merged, created, errors}`
  - `run_hub_news_distillation(config: dict | None) -> dict` (cron entry)

Flow:
```
pending = list_pending_refs(limit=50)
for ref in pending:
    events = list_events(ticker=ref.ticker, since=ref.publish_day)
    event_id = match_ref_to_event(ref, events)
    event = get_event(event_id) if event_id else None
    distilled = distill_ref_into_event(ref, event, ticker=ref.ticker)
    upsert_event(distilled)
    mark_ref_merged(ref.ref_id, distilled.event_id)
```

- [ ] **Step 1: Write integration test** with mocked LLM + in-memory hub dir

- [ ] **Step 2–4: Implement and verify**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): add async distillation batch worker"
```

---

### Task 6: Wire ingest to staging queue (fast path)

**Files:**
- Modify: `integrations/trade_integrations/dataflows/news_hub_bridge/_ingest.py`
- Modify: `integrations/trade_integrations/dataflows/news_hub_bridge/__init__.py`
- Test: `tests/test_news_hub_bridge_staging.py`

**Interfaces:**
- Modify `ingest_rows_to_hub()`:
  - Enqueue each row to staging (keyword tags sync)
  - Trigger debounced `process_distillation_batch` via `threading` or fire-and-forget asyncio task — **do not await LLM**
  - Return `{queued: N, distilled: 0}` (legacy keys: `ingested` = queued count)

Keep legacy path behind env `HUB_NEWS_LEGACY_INGEST=1` for rollback during migration.

- [ ] **Step 1: Test ingest returns immediately without LLM call**

- [ ] **Step 2–4: Implement**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): route ingest through staging queue"
```

---

### Task 7: Bridge union read path

**Files:**
- Modify: `integrations/trade_integrations/dataflows/news_hub_bridge/__init__.py`
- Test: `tests/test_news_hub_bridge_staging.py` (extend)

**Interfaces:**
- Modify `query_verified_news()` and `headlines_for_day()`:
  1. Query `events.parquet` (primary)
  2. Append pending staging refs not yet merged
  3. Map via `distilled_event_to_headline_dict(event)` — backward compatible

New helpers:
- `distilled_event_to_headline_dict(event: dict) -> dict`
- `get_distilled_event(event_id: str) -> dict | None`
- `distillation_queue_stats() -> dict`

Legacy fallback: if `events.parquet` empty, read `records.parquet`.

- [ ] **Step 1: Test union returns staging when no distilled yet**

- [ ] **Step 2: Test prefers distilled after merge**

- [ ] **Step 3–4: Implement**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): union read path for distilled + staging"
```

---

### Task 8: Daily cron + ops scripts

**Files:**
- Modify: `vibetrading/agent/src/scheduled_research/index_jobs.py`
- Modify: `vibetrading/agent/src/trade/index_prediction_jobs.py` (job label)
- Create: `scripts/distill_hub_news.py`
- Create: `scripts/backfill_distilled_events.py`
- Test: `tests/test_index_scheduled_jobs.py` (extend)

**Interfaces:**
- New job type: `hub_news_distillation`
- Cron default: `35 18 * * *` (env `HUB_NEWS_DISTILLATION_CRON`)
- `run_hub_news_distillation_job(config)` → calls agent + compaction (merge duplicate events)

CLI:
```bash
python scripts/distill_hub_news.py --ticker NIFTY --batch-size 100 --dry-run
python scripts/backfill_distilled_events.py --days 90
```

- [ ] **Step 1: Test job registration creates `hub_news_distillation`**

- [ ] **Step 2–4: Implement**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): schedule daily distillation and ops scripts"
```

---

### Task 9: Backfill + migration from records.parquet

**Files:**
- Create: `scripts/backfill_distilled_events.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/news_distillation_agent.py`

**Logic:**
- Load each `records.parquet` row
- Convert to single-ref `DistilledNewsEvent` (title/content from existing fields, references from sources_json)
- Upsert to events.parquet
- Do not delete records.parquet

- [ ] **Step 1: Dry-run backfill on fixture hub**

- [ ] **Step 2: Verify event count ≈ records count post-backfill**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(hub-news): backfill distilled events from legacy records"
```

---

### Task 10: Daily event compaction (dedup distilled entities)

**Files:**
- Modify: `integrations/trade_integrations/dataflows/index_research/news_distillation_agent.py`
- Test: `tests/test_news_distillation_agent.py` (extend)

After batch processing, merge duplicate **events** (not refs):
- Same match rules as Task 3
- Union references and timelines
- Re-run LLM distill on merged set for fresh consensus
- Log removed event_ids → `distillation_log.jsonl`

- [ ] **Step 1: Test two similar events collapse to one**

- [ ] **Step 2–4: Implement compaction pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-news): compact duplicate distilled events daily"
```

---

## Self-review (spec coverage)

| Spec requirement | Task |
|------------------|------|
| DistilledNewsEvent entity | 1, 2 |
| References + timeline | 1, 2, 4 |
| Staging queue fast path | 1, 6 |
| Live union read | 7 |
| Async LLM distillation | 4, 5 |
| Moderate dedup + similarity 0.72 | 3, 10 |
| Daily cron | 8 |
| Backward-compatible API | 7 |
| Migration from records.parquet | 9 |
| Daily compaction + timeline dedup | 8, 10 |

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — one implementer subagent per task, review between tasks
2. **Inline Execution** — execute tasks in this session with checkpoints

Which approach do you want?
