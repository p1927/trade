# Hub Observability — Index Plan

**Date:** 2026-07-24  
**Goal:** Module-wise, agent-ready observability for watchers, LLM calls, and hub ingestion — Tier 0 always on; Langfuse/Loki optional.

## Phase map

| Phase | File | Type | Tier | Depends | Status |
|-------|------|------|------|---------|--------|
| 2 | [phase-2-tier0-emitter.md](2026-07-24-hub-observability-phase-2-tier0-emitter.md) | implement | 0 | — | Done |
| 6 | [phase-6-issue-registry.md](2026-07-24-hub-observability-phase-6-issue-registry.md) | implement | 0 | 2 | Done |
| 3 | [phase-3-instrumentation.md](2026-07-24-hub-observability-phase-3-instrumentation.md) | implement | 0 | 2, 6 | Done (Tier 0 scope) |
| 4 | [phase-4-loop-guard.md](2026-07-24-hub-observability-phase-4-loop-guard.md) | implement | 0 | 3 | Partial (`LoopGuard` + ReAct thresholds; not all loops wired) |
| 1 | [phase-1-loki-grafana.md](2026-07-24-hub-observability-phase-1-loki-grafana.md) | implement | 1 | 2 | Pending |
| 1b | [phase-1b-langfuse.md](2026-07-24-hub-observability-phase-1b-langfuse.md) | implement | 2 | 2 | Pending |
| 5 | [phase-5-hub-ui.md](2026-07-24-hub-observability-phase-5-hub-ui.md) | implement | all | 2, 6 | Pending |

## Execution order (locked)

1. **Phase 2 + 6** — emitter + issue registry + API (agent SSOT)
2. **Phase 3** — choke-point instrumentation
3. **Phase 4** — loop_guard + repeat-skip detection
4. **Phase 1 / 1b** — optional Docker profiles (`logs`, `llm-traces`)
5. **Phase 5** — Hub UI links + open-issues badge

## Global constraints

- Tier 0 runs on every `trade up` — no extra Docker required
- Agent reads `issues.jsonl` + `/trade/observability/issues` — never Grafana/Langfuse
- Do not replace PipelineLogger SSE, trace.jsonl, or hub JSONL audits — bridge only
- Langfuse/Loki profiles off by default

## Success criteria

- `emit("watch", "test")` writes to `log/observability/events.jsonl`
- `GET /trade/observability/issues?status=open` returns structured issues
- `had_errors=true` on a job opens an issue even when status is `ok`
- Phase 3: vibe_trigger skip_reason and scheduled job complete events visible in events.jsonl
