# Auto Paper → Autonomous Agents — Migration Index

**Status:** In progress (Phases 0–3 largely landed; Phases 4–5 partial)

**Goal:** Move auto_paper brain features into `autonomous_agents/`; retire standalone paper engine.

## Phase status

| Phase | Status | Notes |
|-------|--------|-------|
| 0 Foundation | **Done** | `agent_schema.py`, lifecycle backfill on `get_agent`, shims |
| 1 Mandate | **Done** | `mandate_config.py`, `mandate_enforcer.py`, `mandate.py`; auto_paper re-exports |
| 2 Lifecycle + ledger | **Done** | `lifecycle.py`, `outcome_ledger.py` on agent paths; commit sets `lifecycle` |
| 3 Decisions/rank/reconcile | **Partial** | `strategy_rank.py`, `reconcile.py`, `audit.py` moved; MCP still via `auto_paper.mcp_actions` |
| 4 Retire engine | **Partial** | Removed `start_auto_paper` on commit, legacy watch, paper stop on stop/delete |
| 5 Package delete | **Pending** | `auto_paper/` retains `mcp_actions`, `session_store`, `openalgo_client`, `engine`, routes |

## New homes (autonomous_agents)

- `mandate.py` / `mandate_config.py` / `mandate_enforcer.py` — mandate + guards
- `lifecycle.py` — state machine, tried/plan-B
- `outcome_ledger.py` — calibration parquet (`hub/_data/autonomous_agents/outcomes.parquet`)
- `strategy_rank.py` — scenario-weighted ranks
- `reconcile.py` — OpenAlgo positionbook vs ledger
- `audit.py` — action audit trail
- `agent_schema.py` — lifecycle backfill from legacy session JSON
- `market_hours.py` — session open helper (Nautilus/simulator)

## Still in auto_paper (to remove in Phase 5)

- `mcp_actions.py`, `engine.py`, `runner.py`, `session_store.py`, `openalgo_client.py`
- `market_feedback.py`, `agent_mandate.py`, `reflection.py`, `config.py`
- `/trade/auto-paper/*` routes, `auto_paper_jobs` scheduler

See `.cursor/plans/auto_paper_migration_019c3147.plan.md` for full task list.
