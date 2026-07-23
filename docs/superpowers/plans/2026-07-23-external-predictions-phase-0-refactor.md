# Phase 0: Refactor — User-Only Refresh + Incremental SSE

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. See [index](./2026-07-23-external-predictions-expert-agent-index.md).

**Goal:** Remove scheduled cron refresh; load cached snapshot on tab open; stream per-source results via SSE `source_complete`; summary header on Miscellaneous tab.

**Type:** refactor + cleanup

**Depends on:** —

## Global Constraints

- No `EXTERNAL_PREDICTIONS_REFRESH_CRON` scheduled job.
- Cache TTL = stale badge only; never auto-fetch.
- User clicks Refresh to start refresh job.

## Phase pytest scope

```bash
pytest tests/test_index_scheduled_jobs.py tests/test_external_predictions.py -q --timeout=120
```

---

### Task 1: Remove cron registration and dispatch

**Files:**
- Modify: `vibetrading/agent/src/scheduled_research/index_jobs.py`
- Test: `tests/test_index_scheduled_jobs.py`

- [ ] Remove `JOB_TYPE_EXTERNAL_PREDICTIONS_REFRESH`, handler, `run_external_predictions_refresh_job`, cron job entry
- [ ] Add test: `nifty-external-predictions-refresh` not in registered jobs

---

### Task 2: Incremental `source_complete` SSE

**Files:**
- Modify: `integrations/.../external_predictions/refresh.py`
- Modify: `vibetrading/agent/src/trade/external_predictions_run_jobs.py`
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Modify: `vibetrading/frontend/src/lib/api.ts`
- Modify: `vibetrading/frontend/src/hooks/useExternalPredictions.ts`
- Test: `tests/test_external_predictions.py`

**Interfaces:**
- `refresh_all_external_predictions(..., on_source_complete=None)` callback after each source
- SSE event `source_complete` with `{ partial_snapshot, source_id }`

---

### Task 3: Summary header UI

**Files:**
- Modify: `vibetrading/frontend/src/components/prediction/ExternalPredictionsPanel.tsx`
- Modify: `vibetrading/frontend/src/lib/externalPredictionsUtils.ts` (helper for summary stats)

Show: horizon, sources with forecasts count, target range, last updated.

---

### Completion gate

- Cron job absent from defaults
- Refresh still works via user button
- SSE `source_complete` updates snapshot before `done`
- Phase pytest exits 0
