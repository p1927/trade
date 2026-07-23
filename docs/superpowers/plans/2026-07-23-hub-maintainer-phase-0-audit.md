# Hub Maintainer Phase 0 — Audit & Mandate Gate

**Type:** Plan gate (no production code until checklist complete)  
**Depends on:** —  
**Supersedes:** —  
**Out of scope:** UI polish, doc edits (Phase 3–4)

## Goal

Inventory every maintainer stage and legacy path; lock the maintainer mandate and stage order before code changes. Prove no feature will be dropped.

## File map

| File | Role |
|------|------|
| `integrations/.../news_entity_worker.py` | `run_hub_news_entity_job`, repair, backfill, compact |
| `integrations/.../news_cleanup.py` | Stale pending, rejected archive |
| `integrations/.../news_rollup.py` | Parent topic digest |
| `integrations/.../news_post_upsert_safety.py` | Per-upsert safety |
| `integrations/.../news_llm_story_pipeline.py` | Fact adjudication |
| `integrations/.../hub_storage/news_pipeline_config.py` | Cron + config |
| `docs/news-hub-bridge.md` | Stale contract (flag only) |

## Task 1: Stage inventory + legacy path audit

- [ ] **Step 1:** Document current `maintenance` vs `drain` return shapes from `run_hub_news_entity_job` (all keys: migration, staging, repair, backfill, compact_events, cleanup, rollup, news_impact_refresh, had_errors).
- [ ] **Step 2:** Grep `list_verified_records` / `records.parquet` inside maintainer stages (`repair_leaked`, `backfill_distilled_event_metadata`). List each callsite and target SSOT replacement (`list_events`, `get_event`, `patch_event_meta`).
- [ ] **Step 3:** Confirm `compact_distilled_events` uses `list_events` + two-signal + wiki — note env flags (`wiki_search_enabled`, `cluster_threshold` from pipeline config).
- [ ] **Step 4:** List features that must not regress (checkbox in audit table):

| Feature | Module | Must keep |
|---------|--------|-----------|
| Staging drain in maint mode | news_entity_worker | yes |
| Multi-pass compact | compact_distilled_events | yes |
| Wiki club merge | search_dedup | yes |
| Parent rollup digest | news_rollup | yes |
| Stale pending discard | news_cleanup | yes |
| Rejected archive | news_cleanup | yes |
| Leak repair + redistill | repair, backfill | yes |
| Impact refresh no ingest | _refresh_news_impact_cache | yes |
| Pause on migration/wiki | pipeline_pause_status | yes |
| had_errors rollup | _part_had_errors | yes |
| Continuous drain job | news_pipeline_config | yes |
| Hub “Run maintainer now” API | trade_routes | yes |

## Task 2: Maintainer mandate one-pager

- [ ] **Step 1:** Add section to this phase file (or short spec under `docs/superpowers/specs/`) stating:
  - Primary job: dedupe, merge, enrich, prune — **not** fetch new headlines (ingest owns that).
  - Fact fetch = adjudication / article body enrichment on **existing refs**, not live RSS.
  - Soft-create policy: hide from prediction until 2 refs; maintainer merges singles into clusters when evidence appears.
- [ ] **Step 2:** User approval implicit when Phase 1 starts.

## Completion gate

- [ ] Audit table filled with file:line citations.
- [ ] Zero “unknown” rows for legacy `list_verified_records` in maintainer path.
- [ ] Plan index phase map unchanged except Status → Ready for Phase 1.

**Pytest:** None (read-only phase).
