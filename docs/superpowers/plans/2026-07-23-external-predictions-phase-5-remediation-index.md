# External Predictions Phase 5 — Remediation Index

**Goal:** Reduce refresh errors (Akamai blocks, wrong pages, false rejects) and clarify UX.

| Phase | Plan file | Type | Status |
|-------|-----------|------|--------|
| 5A–5D | (inline — crawl fallback, URL priority, validator mid-from-high, UX) | implement | **Done** (convergence 2×; R1/R2/R3/R6 fixed) |
| 5E | [2026-07-23-external-predictions-phase-5e-searxng-triggers.md](./2026-07-23-external-predictions-phase-5e-searxng-triggers.md) | implement | **Done** |
| 5F | [2026-07-23-external-predictions-phase-5f-resistance-validator.md](./2026-07-23-external-predictions-phase-5f-resistance-validator.md) | implement | **Done** |

## Carried-forward issue log (was deferred — now scheduled)

| ID | Summary | Phase | Status |
|----|---------|-------|--------|
| R4 | SearXNG not run when crawl OK but no forecast picked | 5E | **fixed** |
| R5 | Mixed failure types skip bot-block fallback | 5E | **fixed** |
| R7 | Resistance level promoted to analyst `mid` | 5F | **fixed** |

**Rule:** `.cursor/rules/no-defer-migrate-forward.mdc` — no chat-only deferrals; these rows must close before unrelated work.

## Convergence fixes already shipped (5A–5D)

- R1 SearXNG provenance overwrite — fixed
- R2 `urls_tried` missing crawled URLs — fixed
- R3 last OK article URL dropped — fixed
- R6 partial job JSON sanitize — fixed

**Gate:** Refresh job `done`; rollup improves; pytest `test_external_predictions*.py` exits 0.
