# OpenAlgo Market Authority — Master Plan Index

> **For agentic workers:** Execute phases **sequentially** via `superpowers:subagent-driven-development`. One implementer subagent per task; never parallel implementers on the same phase.

**Goal:** Consolidate paper/market/broker routing into OpenAlgo as execution authority; consumers use a single connector port. Eliminate duplicated state across Vibe, Trade, Nautilus, and dataflows.

**Parent architecture report:** `.cursor/plans/openalgo_market_authority_86feee36.plan.md`

## Phase map

| Phase | Plan file | Type | Depends on | Status |
|-------|-----------|------|------------|--------|
| 0 | [2026-07-23-market-authority-phase-0-connector.md](./2026-07-23-market-authority-phase-0-connector.md) | Finish in-flight refactor | — | **Met** — connector context, market_quotes, default profile parity |
| 1 | [2026-07-23-market-authority-phase-1-marketcontext.md](./2026-07-23-market-authority-phase-1-marketcontext.md) | OpenAlgo API + SDK | Phase 0 | **Met** — `/api/v1/marketcontext` + Trade client |
| 2 | [2026-07-23-market-authority-phase-2-paper-gates.md](./2026-07-23-market-authority-phase-2-paper-gates.md) | Cleanup + gate collapse | Phase 1 | **Met** — `context_verify`, preflight, auto_paper, trade_routes; env lock ops-only |
| 3 | [2026-07-23-market-authority-phase-3-trading-port.md](./2026-07-23-market-authority-phase-3-trading-port.md) | Port/adapter refactor | Phase 1–2 | **Met** — `OpenAlgoConnectorAdapter` for IN+US; direct Alpaca execution removed |
| 4 | [2026-07-23-market-authority-phase-4-alpaca-plugin.md](./2026-07-23-market-authority-phase-4-alpaca-plugin.md) | OpenAlgo broker plugin | Phase 3 | **Met** — US via OpenAlgo Alpaca plugin; legacy bridge Alpaca feeds removed |
| 5 | [2026-07-23-market-authority-phase-5-ws-watch.md](./2026-07-23-market-authority-phase-5-ws-watch.md) | Latency optimization | Phase 3 | **Partial** — `WATCH_FEED_MODE=ws` + REST fallback; bench script optional |

Supersedes partial scope in [2026-07-23-connector-execution-market.md](./2026-07-23-connector-execution-market.md) — Phase 0 absorbs and extends it.

## Global constraints (all phases)

- OpenAlgo `positionbook` wins on conflict; Nautilus never talks to broker directly.
- Do not import full Vibe agent stack from `trade_integrations` — shared JSON + ports only.
- `OPENALGO_PAPER_MODE` remains **ops deploy guard only** after Phase 2 — not business routing.
- Agent `constraints.mode` is mandate **intent** for prompts; execution authority is OpenAlgo `analyze_mode` (+ MarketContext).
- stock_simulator active forces IN market region regardless of profile label.
- India path must fully unify before blocking on Alpaca plugin (Phase 4).

## Mandatory verification protocol (every task, every phase)

From `fix-review-before-stack`, `review-evidence-discipline`, `test-before-report`, and `review-bugbot` skill:

```
Per task:
  1. Implement + targeted pytest (TDD where plan specifies)
  2. Pass 2 — author audit: git diff line-by-line; grep symmetry (parallel code paths)
  3. Pass 3 — Bugbot subagent on uncommitted changes (Diff: uncommitted changes)
  4. Re-read every Bugbot CONFIRMED finding in primary sources; label CONFIRMED/HYPOTHESIS/BY DESIGN
  5. Pass 4 — fix CONFIRMED only; repeat Pass 2→3 on NEW diff
  6. Stop when: ≥2 full convergence rounds AND 0 new CONFIRMED on latest diff
  7. Task reviewer subagent (SDD spec ✅ + quality approved)
  8. Record in `.superpowers/sdd/progress.md`
```

**Bugbot invocation (exact prompt shape):**

```text
Full Repository Path: /Users/pratyushmishra/Documents/GitHub/Trade
Diff: uncommitted changes
Custom Instructions: Focus on paper/market routing regressions, silent wrong-frame routing, status rollup lies, asymmetric IN/US paths.
```

**Phase completion gate:** full phase pytest scope (listed in each subplan) exits 0; phase-level Bugbot clean after convergence.

## SDD execution

1. Read phase plan once; create todos for all tasks.
2. Extract task brief: `scripts/task-brief docs/superpowers/plans/<phase-plan> N` (if SDD scripts present).
3. One implementer subagent per task (sequential).
4. After all tasks in phase: final whole-branch code reviewer on most capable model.
5. Do not start Phase N+1 until Phase N completion gate passes.

## Success criteria (program-level)

1. `POST /api/v1/marketcontext` answers paper/live, broker, simulator, positions authority.
2. Autonomous create → watch → execute uses one connector resolution path.
3. No production business routing reads `OPENALGO_PAPER_MODE` (ops lock only).
4. Nautilus handoff stamps `context_generation`.
5. New market = OpenAlgo plugin + connector profile, not 10-file scatter.

## Execution order

**Start with Phase 0** → Phase 1 → Phase 2 → Phase 3. Phase 4–5 when US unification / latency are prioritized.
