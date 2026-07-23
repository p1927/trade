# Hub Maintainer Phase 1 — SSOT Alignment

**Type:** Implement  
**Depends on:** Phase 0 audit complete  
**Supersedes:** —  
**Out of scope:** New adjudication sweep (Phase 2), UI (Phase 4)

## Goal

Migrate maintainer repair/backfill to **events.parquet** SSOT without changing externally visible behavior. Introduce explicit **MaintainerStageManifest** for observability.

## File map

| File | Change |
|------|--------|
| `news_entity_worker.py` | SSOT reads/writes; stage manifest; `_patch_worker_last` enrichment |
| `news_events_store.py` | Ensure patch/list helpers used by repair/backfill |
| `tests/test_news_entity_compaction.py` | Extend for repair/backfill on events |
| `tests/test_news_events_store.py` | Regression for meta patch paths |

## Task 1: Repair on events SSOT

**Files:** `news_entity_worker.py`, new tests in `tests/test_news_entity_compaction.py`

- [ ] **Step 1:** Write failing test: event with leaked MiniMax thinking in `content_summary` → `repair_leaked_distilled_summaries` fixes via `list_events` / `get_event`, not `list_verified_records`.
- [ ] **Step 2:** Refactor `repair_leaked_distilled_summaries` to iterate `list_events(ticker, include_rejected=True)` and upsert via events store + bridge ingest path (same as today’s `ingest_headline_rows` outcome).
- [ ] **Step 3:** Verify `event_index.parquet` updated on repair upsert (IDX invariant from resolver plan).
- [ ] **Step 4:** Run targeted pytest; exit 0.

## Task 2: Backfill on events SSOT

**Files:** `news_entity_worker.py`, `news_events_store.py`

- [ ] **Step 1:** Write failing test: event missing `event_meta.consensus` → backfill patches or redistills using events row shape.
- [ ] **Step 2:** Refactor `backfill_distilled_event_metadata` to source rows from `list_events`; use `patch_event_meta` / events upsert instead of `patch_verified_event_meta` where equivalent.
- [ ] **Step 3:** Preserve redistill triggers (leak, missing event_id, multi-ref minimax rows).
- [ ] **Step 4:** Grep symmetry — no maintainer-only `list_verified_records` left except migration/back-compat reads inside `verified_news_store` adapter.

## Task 3: Maintainer stage manifest

**Files:** `news_entity_worker.py`, `hub_status.py` (optional expose last run)

- [ ] **Step 1:** Add `MAINTAINER_STAGES` ordered tuple mirroring index plan stage list (existing stages only in Phase 1).
- [ ] **Step 2:** Each stage result includes: `stage`, `status`, `duration_ms`, counts (`merged`, `removed`, `repaired`, `errors`, …).
- [ ] **Step 3:** Persist last maintenance manifest to `reports/hub/_data/news_staging/worker_last.json` under key `last_maintenance` (merge with existing worker_last).
- [ ] **Step 4:** `run_hub_news_entity_job` top-level summary includes `stages: [...]` for API/UI (Phase 4).

## Task 4: Drain mode parity check

- [ ] **Step 1:** Confirm `mode=drain` still skips repair/backfill/cleanup/rollup (unchanged).
- [ ] **Step 2:** Add regression test if missing: drain result keys unchanged vs baseline snapshot.

## Phase pytest scope

```bash
python -m pytest tests/test_news_entity_compaction.py tests/test_news_events_store.py \
  tests/test_news_migrations.py -q --timeout=120
```

## Completion gate

- [ ] Grep: no `list_verified_records` in `repair_leaked` / `backfill_distilled` bodies.
- [ ] Convergence loop clean on phase diff.
- [ ] All Task 1–3 tests pass.
