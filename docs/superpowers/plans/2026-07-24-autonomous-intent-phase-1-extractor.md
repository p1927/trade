# Phase 1 — LLM Agent Intent Extractor

**Type:** implement  
**Depends on:** [Index plan](2026-07-24-autonomous-intent-unified-index.md)  
**Out of scope:** Proposal card UI, watch compiler, dead code deletion

## Goal

Single `extract_agent_intent()` entry point: structured LLM extraction with **latest-message override** merge, persisted on session and agent.

## Files

| File | Action |
|------|--------|
| `integrations/trade_integrations/autonomous_agents/intent_schema.py` | Create — `AgentIntent`, `WatchCondition`, JSON schema |
| `integrations/trade_integrations/autonomous_agents/intent_extractor.py` | Create — LLM extract, validate, merge |
| `integrations/trade_integrations/autonomous_agents/intent_merge.py` | Create — `merge_agent_intent(prior, delta)` |
| `vibetrading/agent/src/session/service.py` | Wire post-turn + post-user-message |
| `tests/test_agent_intent_extractor.py` | Create |

## Tasks

- [ ] **1.1** Define JSON schema for extractor output: full intent fields + `explicit_fields[]` + `needs_clarification[]`
- [ ] **1.2** Implement LLM prompt: prior intent JSON + latest user message → delta; include instrument enum `equity|options|futures|index`
- [ ] **1.3** Validator: reject invalid symbols, empty symbols when engagement set, contradictory instruments
- [ ] **1.4** `merge_agent_intent`: only fields listed in `explicit_fields` overwrite prior; recompute `capabilities` via `derive_capabilities()`
- [ ] **1.5** Optional fast-path: high-confidence regex for "paper trade options NIFTY" skips LLM (env `INTENT_EXTRACTOR_LLM=1` default on)
- [ ] **1.6** Persist to `mandate_config.intent` on orchestrator session and agent record
- [ ] **1.7** Tests with mocked LLM responses: observe index, trade options, futures, clarification needed, latest message override

## Acceptance

- Same message + prior → deterministic merge output
- "Watch NIFTY" then "actually options trading" → second message flips `instruments` and `capabilities`
- Extractor called from orchestrator auto-propose path (not a separate code path)
