# Autonomous Track D — Revision watch_spec Enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** On `strategy_revision` turns, when the agent REVISEs/ADJUSTs with new strategy or stop/target levels, watchers must stay aligned — prompt mandate + automatic sync guard in `record_autonomous_decision`.

**Architecture:** New `revision_watch_spec.py` compares decision levels vs `agent.watch_spec`; `maybe_sync_watch_spec_on_revision` rebuilds via existing `build_watch_spec_for_strategy` + handoff/registry sync. Prompt footer on revision turns requires citing progress and updating watchers when levels change.

**Tech Stack:** Python, existing `strategy_watch_spec`, `mcp_set_watch_spec` handoff path.

## Global Constraints

- OpenAlgo remains sole IN execution authority; Nautilus owns watch rules.
- Minimal diff — reuse `build_watch_spec_for_strategy`, do not fork watch builders.
- Read-only prompt paths stay read-only.

---

### Task 1: revision_watch_spec module

- [ ] Add `revision_watch_spec.py` with level extraction, `watch_spec_matches_levels`, `maybe_sync_watch_spec_on_revision`
- [ ] Unit tests for match/mismatch and auto-sync

### Task 2: Wire record_autonomous_decision

- [ ] Accept optional `stop`, `target`, `spot` on decision
- [ ] Call `maybe_sync_watch_spec_on_revision` on REVISE/ADJUST (IN + US paths)
- [ ] Return `watch_spec_sync` in MCP response

### Task 3: Prompt enforcement

- [ ] Extend `strategy_revision` prompt footer in `turns.py` with mandatory watch_spec update rule

### Verify

```bash
pytest tests/test_revision_watch_spec.py tests/test_autonomous_turns.py tests/test_autonomous_mcp_actions.py -q
```
