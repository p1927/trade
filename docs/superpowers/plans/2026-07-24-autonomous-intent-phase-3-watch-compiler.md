# Phase 3 — Watch Condition Compiler

**Type:** implement  
**Depends on:** Phase 1  
**Out of scope:** LLM extractor changes, UI

## Goal

Compile `AgentIntent.watch_conditions[]` → canonical `WatchSpec` + `schedules`. User conditions (time cadence, price levels, move %, groups, VIX) are authoritative — no silent 0.5% default.

## Files

| File | Action |
|------|--------|
| `integrations/trade_integrations/autonomous_agents/watch_compiler.py` | Create |
| `integrations/trade_integrations/autonomous_agents/mandate_config.py` | `to_watch_spec()` delegates to compiler |
| `integrations/trade_integrations/autonomous_agents/bootstrap.py` | Respect user conditions precedence |
| `integrations/trade_integrations/autonomous_agents/mcp_actions.py` | `set_agent_watch_spec` uses compiler |
| `tests/test_watch_compiler.py` | Create |

## WatchCondition → WatchRule mapping

| kind | schedules | rules |
|------|-----------|-------|
| `schedule` | `watch_ms = every_min * 60000` | none |
| `price_move` | — | `spot_move_pct` + direction |
| `price_level` | — | `level_above` / `level_below` |
| `volume` | — | `volume_spike_pct` |
| `oi` | — | `oi_change_pct` |
| `vix` | — | `level_above` / `level_below` on INDIAVIX |
| `composite` | per child | fan-out to multiple rules |

## Tasks

- [ ] **3.1** Implement `compile_watch_spec(intent: AgentIntent) -> tuple[schedules, WatchSpec]`
- [ ] **3.2** Validate compiled rules through `WatchRule.from_dict()` — fail closed on bad metrics
- [ ] **3.3** Remove default `spot_move_pct=0.5` injection when `watch_conditions` non-empty or user clarified cadence-only → set `needs_clarification` on alert conditions instead of guessing
- [ ] **3.4** Bootstrap: skip `build_watch_spec_for_strategy` overlay when agent has user-authored `watch_conditions`
- [ ] **3.5** Tests: 3 min cadence + 50 pt move; dual level rules; composite group

## Acceptance

- User-specified conditions appear verbatim on proposal `watch_spec` and survive commit
- No 0.5% rule unless user or strategy explicitly defines move threshold
