# Hub Maintainer Phase 2 — Enrichment, Facts & Dedup Completeness

**Type:** Implement  
**Depends on:** Phase 1  
**Supersedes:** —  
**Out of scope:** Hub UI, bridge doc (Phases 3–4)

## Goal

Extend maintainer with **fact adjudication backfill**, **bounded post-upsert safety sweep**, and **conditional index rebuild** — fulfilling the product mandate (merge relevant stories, enrich, remove bloat, factual consensus) without removing any Phase 1 stage.

## File map

| File | Change |
|------|--------|
| `news_entity_worker.py` | New stages in maintenance path |
| `news_maintainer_facts.py` | **Create** — fact/adjudication backfill orchestrator |
| `news_maintainer_safety_sweep.py` | **Create** — 7d bounded safety scan |
| `news_llm_story_pipeline.py` | Reuse `pre_enrich_refs_for_adjudication`, `run_story_pipeline_batch` |
| `news_post_upsert_safety.py` | Reuse scan logic |
| `news_event_index.py` | Conditional rebuild |
| `tests/test_news_maintainer_facts.py` | **Create** |
| `tests/test_news_maintainer_safety_sweep.py` | **Create** |

## Task 1: Fact adjudication backfill stage

**Purpose:** Events/refs missing `extracted_claims` or adjudication summary get enriched (article fetch where configured + LLM adjudication batch) so consensus/distillation has factual claims.

- [ ] **Step 1:** Write failing test: event with refs lacking claims → `run_fact_adjudication_backfill(ticker, limit=50)` attaches claims without creating duplicate events.
- [ ] **Step 2:** Implement `run_fact_adjudication_backfill` in `news_maintainer_facts.py`:
  - Select candidates: events in lookback window with refs missing claims (respect `llm_adjudication_enabled` from pipeline config).
  - Call existing pipeline helpers — **do not duplicate LLM prompts**.
  - Respect `pipeline_pause_status` / wiki gate — skip with `{skipped: true}` not silent ok.
  - Cap batch size via `adjudication_batch_size` config.
- [ ] **Step 3:** Wire into `run_hub_news_entity_job` after `backfill`, before `compact_events`.
- [ ] **Step 4:** Manifest counts: `adjudicated_refs`, `discarded_hoax`, `errors`.

## Task 2: Post-upsert safety sweep (maintenance-only)

**Purpose:** Catch duplicate clubs missed on write path; resolver plan says single-pass on upsert — this is **scheduled bounded** complement, not recursive upsert.

- [ ] **Step 1:** Write failing test: two near-duplicate events in 7d window → sweep merges or flags per `run_post_upsert_safety_scan` rules without infinite loop.
- [ ] **Step 2:** Implement `run_maintenance_safety_sweep(ticker, lookback_days=7, max_events=200)` in `news_maintainer_safety_sweep.py`:
  - Iterate recent event_ids from `list_events`.
  - Call existing `run_post_upsert_safety_scan` per candidate (respect `HUB_NEWS_POST_UPSERT_SAFETY_SCAN`).
  - Aggregate `merged`, `removed`, `skipped`, `errors`.
- [ ] **Step 3:** Insert after `compact_events`, before `cleanup_hub_news`.
- [ ] **Step 4:** Document BY DESIGN: sweep is single-pass per event per run; compaction handles broader dedup.

## Task 3: Conditional event_index rebuild

- [ ] **Step 1:** After stages that set `rows_removed > 0` or `groups_merged > 0`, call `rebuild_event_index(ticker=...)` from `news_event_index.py` (if function exists) or document hook already in store.
- [ ] **Step 2:** Test: compact removes rows → index row count matches events count.
- [ ] **Step 3:** Include in manifest: `index_rebuilt: bool`.

## Task 4: Soft-create merge behavior (no deletion)

- [ ] **Step 1:** Verify compaction two-signal merge promotes single-ref events when second ref cluster appears — extend test if gap.
- [ ] **Step 2:** Maintainer must **not** archive events solely for `ref_count < 1` (prediction visibility is read-side filter only).

## Task 5: Preserve all compaction/cleanup/rollup features

- [ ] **Step 1:** Run existing compaction tests unchanged.
- [ ] **Step 2:** Confirm `rollup_parent_topic_events` still runs last in rollup section (after cleanup).
- [ ] **Step 3:** Confirm wiki compaction metrics still in `worker_last` / manifest.

## Phase pytest scope

```bash
python -m pytest tests/test_news_maintainer_facts.py tests/test_news_maintainer_safety_sweep.py \
  tests/test_news_entity_compaction.py tests/test_news_resolver.py \
  tests/test_news_event_index.py -q --timeout=120
```

## Completion gate

- [ ] Maintenance job runs 11+ stages (see index plan order) with none removed.
- [ ] New stages skippable when LLM/wiki paused (fail-closed, not error masked as ok).
- [ ] Program pytest gate (index) passes.
