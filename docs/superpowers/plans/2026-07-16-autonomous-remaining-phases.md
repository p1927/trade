# Autonomous Agent — Remaining Phases

**Date:** 2026-07-16  
**Depends on:** mandate_config hardening + Nautilus bridge (implemented)

## Phase 1 — Default alignment (Nautilus watch on)

- `NAUTILUS_WATCH_ENABLE` defaults to `true` in bridge config, `.env.example`, and `watch.py`
- Stack status treats unset as enabled (opt-out via `NAUTILUS_WATCH_ENABLE=0`)

## Phase 2 — Backend observability API

- `autonomous_agents/runtime_status.py` — per-agent: `scheduler_health`, `nautilus_watch`, `handoff_active`, `market_open`, `mandate_summary`, `last_decision`
- Enrich `GET /autonomous-agents` and `GET /autonomous-agents/{id}` with `runtime` block
- `GET /autonomous-agents/stack-health` — infra vs trader summary

## Phase 3 — Outcome ledger → ranker calibration

**Status:** **Shipped** — global ranker + autonomous learning bridge via `agent_learning.py`.

- `outcome_ledger.py`: metrics, per-strategy adjustment, reconcile on EXIT — **done**
- `paper_strategy_calibration_adjustment(name)` in `strategy_ranker.py` — **done**
- `[trade_calibration]` in `hub_context.py` for agent turns — **done** (via session prefetch)
- `agent_learning.py`: lifecycle + per-agent reflections + learnings in turns; EXIT hooks — **done**

## Phase 4 — Autonomous Hub UI

- Extend `AutonomousAgentInstance` types with `runtime`, `mandate_config`
- `AutonomousAgentCard`: mandate chips, scheduler/Nautilus health dots, last revision/decision
- Hub header: stack health strip (Vibe scheduler + Nautilus watch)
