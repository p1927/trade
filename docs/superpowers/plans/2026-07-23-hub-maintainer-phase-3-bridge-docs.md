# Hub Maintainer Phase 3 — Bridge & Docs Contract

**Type:** Migrate + cleanup  
**Depends on:** Phase 2 (maintainer stages finalized)  
**Supersedes:** Stale sections of `docs/news-hub-bridge.md`  
**Out of scope:** UI (Phase 4)

## Goal

Align public Hub Source contract with events SSOT, maintainer modes, wiki gate, and visibility filter. Expose maintainer manifest via bridge/status where appropriate.

## File map

| File | Change |
|------|--------|
| `docs/news-hub-bridge.md` | Full rewrite of SSOT + pipeline + maintainer sections |
| `news_hub_bridge/__init__.py` | Optional: `hub_maintainer_last_summary()` read helper |
| `hub_status.py` | Surface `last_maintenance` from worker_last in API payload |
| `tests/test_hub_news_ingest.py` | Doc parity test or comment-only — optional |

## Task 1: Rewrite news-hub-bridge.md

- [ ] **Step 1:** Replace pipeline diagram:

```
Ingest → news_hub_bridge.ingest_* → staging queue
Staging drain (mode=drain) → resolver T0–T4 → events.parquet
Maintainer (mode=maintenance) → repair → backfill → fact backfill → compact → safety sweep → cleanup → rollup → impact refresh
All reads → news_hub_bridge.query_* / resolve_news_impact / union staging
```

- [ ] **Step 2:** Storage table:

| Artifact | Path |
|----------|------|
| Distilled SSOT | `_data/news_events/events.parquet` |
| Event index | `_data/news_events/event_index.parquet` |
| Staging | `_data/news_staging/` |
| Impact snapshot | `{TICKER}/index_research/news_impact_latest.json` |
| Legacy (read-only migration) | `_data/news_verified/records.parquet` |

- [ ] **Step 3:** Document maintainer vs drain modes, cron job IDs from `news_pipeline_config.py`.
- [ ] **Step 4:** Document wiki gate (`HUB_NEWS_REQUIRE_LLM_WIKI`), soft-create visibility (`news_prediction_visibility`), `hub_empty` status.
- [ ] **Step 5:** Document injection boundary: agents read index artifact / news-scenario tools; maintainer refreshes snapshots not live chat blocks.

## Task 2: hub_status maintainer summary

- [ ] **Step 1:** Add `news_maintainer` block to `build_hub_status`:
  - `last_run_at`, `last_mode`, `had_errors`, stage counts from `worker_last.last_maintenance`
  - `next_scheduled_cron` from pipeline config if available
- [ ] **Step 2:** Do not break existing keys (`news_staging`, `news_inventory`, …).

## Task 3: Bridge helper (optional thin wrapper)

- [ ] **Step 1:** If app code needs maintainer status, add `news_hub_bridge.maintainer_last_summary()` reading worker_last — **no store imports from vibetrading**.

## Evaluation items closed

- E-A, E-G (docs)
- Partial E-D (backend payload for Phase 4 UI)

## Phase verification

- [ ] Manual diff: every claim in doc verified against primary source (grep `events.parquet`, `run_hub_news_entity_job`).
- [ ] `build_hub_status` unit test or extend existing hub status test if present.

## Completion gate

- [ ] No doc reference to `records.parquet` as primary SSOT.
- [ ] Index plan E-A, E-G marked done in index Status table.
