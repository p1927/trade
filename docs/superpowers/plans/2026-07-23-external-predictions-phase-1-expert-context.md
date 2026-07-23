# Phase 1: Financial Expert Context Store

**Goal:** Build `financial_expert_context.py` + `nifty_expert_brief.md`; persist `expert_context.json` on refresh start.

**Type:** implement | **Depends on:** Phase 0 | **Status:** **Done**

See [design spec](../specs/2026-07-23-external-predictions-expert-agent-design.md).

### Task 1: `nifty_expert_brief.md` curated brief
### Task 2: `build_expert_context()` from playbooks + interpret + hub
### Task 3: Wire into `refresh_all_external_predictions` start
### Task 4: Tests `tests/test_financial_expert_context.py`

```bash
pytest tests/test_financial_expert_context.py tests/test_external_predictions.py -q --timeout=120
```
