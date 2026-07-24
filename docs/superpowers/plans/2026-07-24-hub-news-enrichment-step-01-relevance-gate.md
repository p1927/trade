# Step 01 — Relevance Gate

**Type:** implement  
**Depends on:** —  
**Module:** `hub_news_pipeline/step_01_relevance_gate.py`

## Goal

Drop obvious non-finance refs before fetch/LLM. LLM assesses ambiguous refs. Emit `StepResult` with verdict.

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/pipeline_context.py` | Create — `RefPipelineContext`, `StepResult` |
| `hub_news_pipeline/step_01_relevance_gate.py` | Create — wrap `news_relevance.assess_ref_relevance` |
| `hub_news_pipeline/pipeline_runner.py` | Create — skeleton `run_step`, `run_ref_pipeline` |
| `news_hub_bridge/_ingest.py` | Wire optional call to step 01 (or keep existing gate, delegate to same function) |
| `tests/hub_news_pipeline/test_step_01_relevance_gate.py` | Create |

## Step contract

```python
def run_step_01_relevance_gate(ctx: RefPipelineContext) -> tuple[RefPipelineContext, StepResult]:
    # sets ctx.relevance_verdict, ctx.should_continue
    # should_continue=False → ctx.discard_reason="irrelevant_not_finance"
```

## Tasks

- [ ] `RefPipelineContext` with `ref`, `step_trace: list[StepResult]`, flags
- [ ] Cricket/entertainment → `should_continue=False`
- [ ] NIFTY/FII headline → `should_continue=True`
- [ ] Ambiguous → LLM when MiniMax configured (mock in tests)
- [ ] Tests: rule pass, rule fail, ambiguous mock LLM

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_01_relevance_gate.py -q
```

## Gate

Step 01 tests exit 0; no fetch/LLM in step 01 itself except relevance LLM.
