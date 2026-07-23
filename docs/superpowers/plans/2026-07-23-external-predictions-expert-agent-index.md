# External Predictions Expert Agent — Master Plan Index

> **For agentic workers:** Execute phases **sequentially** via `superpowers:subagent-driven-development`. One implementer subagent per task; never parallel implementers on the same phase.

**Goal:** User-initiated Miscellaneous street-forecast pipeline with financial expert agent, vision verification, path learning, and incremental UI — replacing crawl-only + cron refresh.

**Design spec:** [2026-07-23-external-predictions-expert-agent-design.md](../specs/2026-07-23-external-predictions-expert-agent-design.md)

**Shipped in:** `7321e7f` | **Last verified:** 2026-07-23 (`68 passed` expert + resolver smoke scope)

## Phase map

| Phase | Plan file | Type | Depends on | Status |
|-------|-----------|------|------------|--------|
| 0 | [2026-07-23-external-predictions-phase-0-refactor.md](./2026-07-23-external-predictions-phase-0-refactor.md) | refactor + cleanup | — | **Done** |
| 1 | [2026-07-23-external-predictions-phase-1-expert-context.md](./2026-07-23-external-predictions-phase-1-expert-context.md) | implement | Phase 0 | **Done** (foundation) |
| 2 | [2026-07-23-external-predictions-phase-2-discovery-paths.md](./2026-07-23-external-predictions-phase-2-discovery-paths.md) | implement | Phase 1 | **Done** |
| 3 | [2026-07-23-external-predictions-phase-3-expert-vision.md](./2026-07-23-external-predictions-phase-3-expert-vision.md) | implement | Phase 2 | **Done** |
| 4 | [2026-07-23-external-predictions-phase-4-browse-sources-ui.md](./2026-07-23-external-predictions-phase-4-browse-sources-ui.md) | implement | Phase 3 | **Done** |

## Global constraints (all phases)

- User-initiated refresh only; **no** `EXTERNAL_PREDICTIONS_REFRESH_CRON` scheduled job.
- Self-hosted: Crawl4AI + MiniMax M3 + SearXNG.
- Street forecasts display-only; do not feed quant combiner.
- Structured `ExternalPredictionRecord` in hub store.
- Screenshot resize 512 or 1024 for M3; full-page stored for thumbnail.
- Path scope per `(source_id, horizon_days)`.
- Parallel sources; incremental SSE `source_complete` rendering.

## Verification protocol (every task)

```
Per task: implement → targeted pytest → Pass 2 diff audit → Pass 3 Bugbot/manual checklist → task reviewer
Phase gate: full phase pytest scope exits 0
Record: .superpowers/sdd/progress.md
```

## Success criteria (program-level)

1. Cron removed; Refresh button only.
2. Cached load on tab open; incremental cards on refresh.
3. Expert context store builds from playbooks + live data.
4. Discovery + fast/exploratory path routing works.
5. MiniMax M3 vision extract + thumbnail on cards.
6. User can add sites; approve paths.

## Execution order

Phase 0 → 1 → 2 → 3 → 4. Do not start N+1 until phase gate passes.
