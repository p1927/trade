# Hub Maintainer Phase 4 — Hub Ops UI

**Type:** Implement  
**Depends on:** Phase 1 (manifest in API summary); Phase 3 optional for status block  
**Supersedes:** —  
**Out of scope:** Maintainer core logic (Phases 1–2)

## Goal

Close **all** evaluation UI gaps without removing Hub page features. Operators see maintainer scope, results, gates, source health, and prediction visibility semantics.

## File map

| File | Change |
|------|--------|
| `vibetrading/frontend/src/pages/Hub.tsx` | Gates, maintainer feedback, labels, badges |
| `vibetrading/frontend/src/lib/api.ts` | Extend `HubStatusPayload` types |
| `tests/` | Frontend optional; backend contract tests if added |

## Task 1: HubStatusPayload types

- [ ] **Step 1:** Add to `HubStatusPayload`:
  - `gates?: { hub_ready?: boolean; blocking?: Array<{ id; passes; user_message; action }> }`
  - `source_availability?: Array<{ vendor; capability; status; ... }>`
  - `news_events_migration?: { needed?: boolean; state?: unknown }`
  - `news_maintainer?: { last_run_at?; had_errors?; stages?; ... }`
- [ ] **Step 2:** Match backend keys from Phase 1 manifest + Phase 3 hub_status.

## Task 2: Migration / hub_ready banner

- [ ] **Step 1:** When `data.status === "migration_required"` OR `gates.hub_ready === false`, show blocking banner with action from `gates.blocking[0].action` (same pattern as ingest pause banner).
- [ ] **Step 2:** Do not hide existing staging pause banner — stack both when applicable.

## Task 3: Maintainer run feedback (E-H)

- [ ] **Step 1:** Update `runMaintenance` to read `HubStagingDrainResponse`:
  - `status === "paused"` → setError with message (mirror `runIngest`)
  - `summary.had_errors` → warning banner with stage breakdown if `summary.stages` present
  - Success → inline summary: merged, removed, repaired, adjudicated counts
- [ ] **Step 2:** Rename UI copy:
  - Cron label: **Maintainer cron (repair · backfill · merge · cleanup)**
  - Button: **Run maintainer now** with tooltip listing stages

## Task 4: Source availability StatCard (E-D)

- [ ] **Step 1:** New StatCard “Source health” listing `source_availability` top N vendors with status chips (available / rate_limited / unavailable).
- [ ] **Step 2:** Empty state when list missing.

## Task 5: Prediction visibility badge (E-B)

- [ ] **Step 1:** Shared helper mirroring `visible_for_prediction_attribution` rule (port logic to TS or derive from `ref_count` + `provenance`):
  - Show badge **Hidden from prediction** when distilled && ref_count < 2
  - Staging rows unchanged (always visible to prediction union)
- [ ] **Step 2:** Optional filter toggle “Show prediction-hidden only”.

## Task 6: Light sources default (E-F)

- [ ] **Step 1:** Align default: set `NewsPipelineConfig` default `light_ingest_sources` to `"rss,watcher"` **OR** change Hub placeholder to `"rss"` — pick one, document in index.
- [ ] **Step 2:** Recommended: `"rss,watcher"` in config to match operational intent (watcher is light-tier source per entity plan).

## Task 7: Maintainer last run panel

- [ ] **Step 1:** In “News pipeline schedule” StatCard or new mini panel, show `news_maintainer.last_run_at`, `had_errors`, top stage counts from hub status poll (30s refresh).

## Evaluation closure

| ID | Task |
|----|------|
| E-B | Task 5 |
| E-D | Tasks 1–2, 4 |
| E-E | Task 3 |
| E-F | Task 6 |
| E-H | Task 3 |

## Completion gate

- [ ] Manual: Run maintainer → UI shows paused OR success summary OR had_errors.
- [ ] Migration gate visible when backend returns `migration_required`.
- [ ] No Hub features removed (ingest, drain, discard, pipeline config, news list filters all remain).
